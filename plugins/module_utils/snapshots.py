# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The snapshots area: snapshot policies, on-demand snapshots, and the facts about snapshots."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime, timedelta

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.files import attributes, canonical_path, file_id, inside, require_directory
from ansible_collections.qumulo.cluster.plugins.module_utils.normalize import canonical_days
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import spoke_roots
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Action, Current, Resource
from ansible_collections.qumulo.cluster.plugins.module_utils.security_keys import KEY, key_facts, list_keys
from ansible_collections.qumulo.cluster.plugins.module_utils.signing import Signer, bare_public_key

POLICY_STATUSES = Route("/v3/snapshots/policies/status/", ("GET",))  # each policy with its directory's path
POLICIES = Route("/v3/snapshots/policies/", ("POST",))
POLICY = Route("/v3/snapshots/policies/{id}", ("GET", "PATCH"))
POLICY_DELETE = Route(
    "/v3/snapshots/policies/{id}",
    ("DELETE",),
    etag="omit",
    why="QUIRK-008: the server accepts If-Match on a policy DELETE and ignores it; a lost DELETE is reconciled by reading back, never resent",
)


def canonical_schedule(schedule):
    """A schedule option without its unset fields, with EVERY_DAY written as the seven days the server lists."""
    out = {key: value for key, value in schedule.items() if value is not None}
    if "on_days" in out:
        out["on_days"] = canonical_days(out["on_days"])
    return out


def canonical_ttl(ttl):
    return "" if ttl.strip().lower() == "never" else ttl.strip()


def policy_lock_key_id(data):
    """The id of the key that locks a policy's snapshots, or None: lock_key_ref in a policy, lock_key in a status entry."""
    held = data.get("lock_key_ref") or data.get("lock_key")
    return None if held in (None, "") else str(held)


def key_label(key):
    """How the collection names a key: its name, or its id when the cluster stored it with an empty name (QUIRK-120)."""
    return key["name"] or str(key["id"])


def policy_options(data, lock_key=None):
    """A policy, with the source_file_path of its status entry, in the option shape of snapshot_policy.

    lock_key is the name of the key that locks its snapshots, which the policy holds only by id; the caller reads it.
    """
    return {
        "policy_name": data["policy_name"],
        "path": canonical_path(data["source_file_path"]),
        "enabled": data["enabled"],
        "expiration_time_to_live": data["schedule"]["expiration_time_to_live"],
        "snapshot_name_template": data["snapshot_name_template"],
        "schedule": dict(data["schedule"]["creation_schedule"]),
        "lock_key": lock_key,
    }


class SnapshotPolicy(Resource):
    RETURN_KEY = "snapshot_policy"
    READ_ONLY = frozenset({"policy_name"})
    CREATE_ONLY = frozenset({"path"})  # the server refuses a new source directory on an existing policy

    def __init__(self, module, client):
        super().__init__(module, client)
        params = self.params
        self.name = params["policy_name"]
        if not 1 <= len(self.name) <= 50 or "/" in self.name:
            raise QumuloError(f"policy_name '{self.name}' must be 1 to 50 characters without '/'")
        wanted = {key: params[key] for key in ("enabled", "snapshot_name_template") if params.get(key) is not None}
        if params.get("path") is not None:
            wanted["path"] = canonical_path(params["path"])
        if params.get("expiration_time_to_live") is not None:
            wanted["expiration_time_to_live"] = canonical_ttl(params["expiration_time_to_live"])
        if params.get("schedule") is not None:
            wanted["schedule"] = canonical_schedule(params["schedule"])
        self.wanted = wanted
        self.lock_key = params.get("lock_key")  # a key's name or id; "" takes the key off; None leaves it
        self.key_ids = {}  # the name of each key the run read -> its id, which the policy holds
        self.disabled_keys = set()  # the names of the keys the run read that are disabled
        self.unread_keys = set()  # the ids of held keys the API user may not read, whose state is unknown

    def describe(self):
        return f"snapshot policy '{self.name}'"

    def fetch(self):
        found = [entry for entry in self.client.get(POLICY_STATUSES).data["entries"] if entry["policy_name"] == self.name]
        # 7.10.3 refuses a second policy with a name in use, so more than one is a cluster this module cannot choose for.
        if len(found) > 1:
            listed = ", ".join(f"{entry['id']} on {entry['source_file_path']}" for entry in found)
            raise QumuloError(f"snapshot policy '{self.name}' names more than one policy ({listed}); rename all but one of them")
        response = self.client.get_or_none(POLICY, params={"id": found[0]["id"]}) if found else None
        return Current(response.data["id"], {**found[0], **response.data}, response.etag) if response else None

    def normalize(self, current):
        held = policy_lock_key_id(current.data)
        return policy_options(current.data, None if held is None else self._held_key_name(held))

    def _held_key_name(self, held):
        """The name of the key the policy holds; its id, with a warning, when the API user may not read the key."""
        try:
            return key_label(self._key(held))
        except QumuloApiError as e:
            if e.status != 403:
                raise
        warning = f"{self.describe()} names its lock key by id {held}: the API user may not read the key store"
        if warning not in self.client.warnings:
            self.client.warnings.append(warning)
        self.key_ids[held] = held
        self.unread_keys.add(held)
        return held

    def _key(self, ref):
        """The key ref names, by name or id; a key that is not in the key store is refused."""
        try:
            key = self.client.get(KEY, params={"ref": ref}).data
        except QumuloApiError as e:
            if e.status == 404 and not e.is_("http_not_found_error"):
                raise QumuloError(
                    f"{self.describe()}: the file system key store holds no key {ref}; register it first with qumulo.cluster.security_key"
                ) from None
            raise
        label = key_label(key)
        self.key_ids[label] = str(key["id"])
        self.disabled_keys.discard(label)
        if key["disabled"]:
            self.disabled_keys.add(label)
        return key

    def desired(self, before):
        if not before:
            if "path" not in self.wanted or "schedule" not in self.wanted:
                raise QumuloError(f"snapshot policy '{self.name}' does not exist; set path and schedule to create it")
            before = {"policy_name": self.name, "enabled": True, "expiration_time_to_live": ""}
        after = {**before, **self.wanted}
        if self.lock_key is not None:
            after["lock_key"] = self._wanted_key(before) if self.lock_key != "" else None
        else:
            after.setdefault("lock_key", None)
        recreating = self.params.get("recreate") and before.get("path") not in (None, after.get("path"))
        if recreating and after["lock_key"] in self.unread_keys:
            raise QumuloError(
                f"{self.describe()}: a recreate would set the key {after['lock_key']} on the new policy, and the API user may not read "
                "the key to know whether the cluster takes it; give the user PRIVILEGE_FS_KEY_MANAGEMENT_READ, or take the key off "
                'with lock_key: "" first'
            )
        if recreating and after["lock_key"] in self.disabled_keys:
            # The create would be refused after the delete, leaving no policy.
            raise QumuloError(
                f"{self.describe()}: a recreate would set the key {after['lock_key']} on the new policy, and the key is disabled, which "
                'the cluster refuses; enable it with enabled: true on qumulo.cluster.security_key, or take it off with lock_key: ""'
            )
        if "schedule" in before and self.same("schedule", before["schedule"], after["schedule"]):
            after["schedule"] = before["schedule"]  # the same schedule; report it with the fields the cluster filled in
        return after

    def _wanted_key(self, before):
        """The name of the key lock_key names; a disabled key is refused, as the cluster refuses it, unless already held."""
        key = self._key(self.lock_key)
        label = key_label(key)
        if key["disabled"] and before.get("lock_key") != label:
            raise QumuloError(
                f"{self.describe()}: the key {label} is disabled, and the cluster sets no disabled key on a policy; enable it "
                "first with enabled: true on qumulo.cluster.security_key"
            )
        return label

    def same(self, key, a, b):
        if key == "schedule" and a is not None and b is not None:
            # The server fills in fields a schedule left out, so only the fields the task gives are compared.
            return all(a.get(field) == value for field, value in b.items())
        return super().same(key, a, b)

    def create(self, after):
        body = {
            "policy_name": self.name,
            "enabled": after["enabled"],
            "source_file_id": file_id(self.client, after["path"]),
            "schedule": {"creation_schedule": after["schedule"], "expiration_time_to_live": after["expiration_time_to_live"]},
        }
        if "snapshot_name_template" in after:
            body["snapshot_name_template"] = after["snapshot_name_template"]
        if after.get("lock_key") is not None:
            # A recreate carries the key the policy held, by the id read with it.
            body["lock_key_ref"] = self.key_ids[after["lock_key"]]
        self.client.post(POLICIES, body=body)

    def update(self, current, after, keys):
        body = {key: after[key] for key in ("enabled", "snapshot_name_template") if key in keys}
        if "lock_key" in keys:
            body["lock_key_ref"] = None if after["lock_key"] is None else self.key_ids[after["lock_key"]]
        schedule = {}
        if "schedule" in keys:  # QUIRK-006: sending a creation_schedule restarts the schedule's timing
            schedule["creation_schedule"] = after["schedule"]
        if "expiration_time_to_live" in keys:
            schedule["expiration_time_to_live"] = after["expiration_time_to_live"]
        if schedule:
            body["schedule"] = schedule
        self.client.patch(POLICY, params={"id": current.id}, body=body, if_match=current.etag)

    def delete(self, current):
        self.client.delete(POLICY_DELETE, params={"id": current.id})


SNAPSHOT_STATUSES = Route("/v4/snapshots/status/", ("GET",))
TOTAL_USED_CAPACITY = Route("/v1/snapshots/total-used-capacity", ("GET",))


def _snapshots(client):
    return {"snapshots": [{**entry, "path": canonical_path(entry["source_file_path"])} for entry in client.paginate(SNAPSHOT_STATUSES, "entries")]}


def _policies(client):
    entries = client.get(POLICY_STATUSES).data["entries"]
    names = _key_names(client) if any(policy_lock_key_id(entry) for entry in entries) else {}
    return {"policies": [policy_options(entry, _lock_key_name(entry, names)) for entry in entries]}


def _key_names(client):
    """Each key's id -> its name; empty, with a warning, when the API user may not read the key store."""
    try:
        return {str(key["id"]): key_label(key) for key in list_keys(client)}
    except SubsetUnavailable as e:
        warning = f"policies name their lock keys by id: {e}"
        if warning not in client.warnings:
            client.warnings.append(warning)
        return {}


def _lock_key_name(entry, names):
    """The name of the key that locks the policy's snapshots, its id when the key store could not be read, or None."""
    held = policy_lock_key_id(entry)
    return None if held is None else names.get(held) or held


def _capacity(client):
    return {"capacity": {"used_bytes": int(client.get(TOTAL_USED_CAPACITY).data["bytes"])}}


# gather_subset name -> function of the client returning a dict of that subset's facts
SNAPSHOT_INFO_SUBSETS = {"snapshots": _snapshots, "policies": _policies, "capacity": _capacity, "security_keys": key_facts}


SNAPSHOTS = Route("/v3/snapshots/", ("POST",))
SNAPSHOT_DELETE = Route("/v3/snapshots/{id}", ("DELETE",), etag="omit", why="the delete is sent only for a snapshot the module itself just created")
CHANGES_SINCE = Route("/v2/snapshots/{newer_id}/changes-since/{older_id}", ("GET",))  # paged, entries of {op, path}
TTL = re.compile(r"^([1-9][0-9]*)\s*(minute|hour|day|week|month)s?$", re.IGNORECASE)
UNIT = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}


def utc_now():
    return datetime.now(UTC)


def _add_months(moment, months):
    """moment that many calendar months later, on the month's last day where it has no such day."""
    month = moment.month - 1 + months
    year, month = moment.year + month // 12, month % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def expiration(ttl):
    """The RFC 3339 expiration a time to live such as 7days gives from now; "" for none or never."""
    if ttl is None or ttl.strip().lower() in ("", "never"):
        return ""
    found = TTL.match(ttl.strip())
    if not found:
        raise QumuloError(f"expiration_time_to_live '{ttl}' is not a time to live such as 90minutes, 12hours, 7days, 2weeks or 6months, or never")
    count, unit = int(found.group(1)), found.group(2).lower()
    now = utc_now().replace(microsecond=0)
    later = _add_months(now, count) if unit == "month" else now + count * UNIT[unit]
    return later.strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_options(entry, path):
    return {key: entry[key] for key in ("id", "name", "timestamp", "expiration")} | {"path": path}


class Snapshot(Action):
    RETURN_KEY = "snapshot"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.path = canonical_path(self.params["path"])
        self.name = self.params.get("name")
        if self.name is not None and (not self.name or "/" in self.name):
            raise QumuloError(f"name '{self.name}' must be a non-empty name without '/'")
        self.expiration = expiration(self.params.get("expiration_time_to_live"))
        self.notes = []
        self.file_id = None
        self.found = None  # the snapshot reuse_existing returns
        self.seen = set()  # ids of the snapshots of the name before this run, to find the one a lost request took

    def plan(self):
        found = attributes(self.client, self.path)
        if found is None:
            if not self.module.check_mode:
                raise QumuloError(f"{self.path} does not exist; create it first, for example with qumulo.cluster.directory")
            self.notes.append(f"{self.path} does not exist yet; the snapshot is taken once an earlier task creates it")
        else:
            require_directory(self.path, found.data)
            self.file_id = found.data["id"]
            self._refuse_spoke_root()
            if self.name is not None:
                named = self._named()
                self.seen = {entry["id"] for entry in named}
                if named and self.params["reuse_existing"]:
                    self.found = max(named, key=lambda entry: entry["id"])
                    return None
        return {"path": self.path, "name": self.name or "OnDemand", "expiration": self.expiration}

    def _refuse_spoke_root(self):
        for root, hub in spoke_roots(self.client).items():
            if inside(self.path, root):
                raise QumuloError(
                    f"{self.path} lies in the spoke root {root} of the portal to hub {hub}, where the cluster takes no snapshot "
                    "(G-SPOKE-PATHS); snapshot it on the hub"
                )

    def _named(self):
        """The snapshots of this directory named <id>_<name> that are not being deleted."""
        return [
            entry
            for entry in self.client.paginate(SNAPSHOT_STATUSES, "entries")
            if canonical_path(entry["source_file_path"]) == self.path and entry["name"] == f"{entry['id']}_{self.name}" and not entry["in_delete"]
        ]

    def run(self):
        result = super().run()
        if self.found is not None:
            result[self.RETURN_KEY] = snapshot_options(self.found, self.path)
        if self.notes:
            result["notes"] = self.notes
        return result

    def perform(self, plan):
        body = {"expiration": plan["expiration"], "source_file_id": self.file_id}
        if self.name is not None:
            body = {"name_suffix": self.name, **body}
        try:
            taken = self.client.post(SNAPSHOTS, body=body).data
        except QumuloMaybeApplied as lost:
            taken = self._reconcile(lost)
        return {self.RETURN_KEY: snapshot_options(taken, self.path)}

    def _reconcile(self, lost):
        if self.name is None:
            raise QumuloError(
                f"{lost}; a snapshot named OnDemand may have been taken of {self.path}; list the snapshots before running again", **lost.details
            ) from None
        new = [entry for entry in self._named() if entry["id"] not in self.seen]
        if not new:
            raise QumuloError(f"{lost}; no new snapshot named {self.name} of {self.path} was found", **lost.details) from None
        self.notes.append("write reconciled after a lost connection")
        return max(new, key=lambda entry: entry["id"])


SNAPSHOT_STATUS = Route("/v3/snapshots/status/{id}", ("GET",))
LOCK = Route("/v3/snapshots/{id}/lock", ("POST",))
UNLOCK_CHALLENGE = Route("/v3/snapshots/{id}/unlock-challenge", ("GET",))  # single-use, and audited
UNLOCK = Route("/v3/snapshots/{id}/unlock", ("POST",))


class SnapshotLock:
    """A snapshot's lock: read first, since the cluster refuses to lock a locked snapshot or unlock an unlocked one."""

    RETURN_KEY = "snapshot_lock"

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.notes = []
        self.private_key = self.params.get("private_key")
        self.signer = Signer(module, "private_key") if self.private_key else None

    def run(self):
        if self.params.get("lock_key") == "":
            # An empty key_ref is the key list's route, which reads as no key (#333).
            raise QumuloError("lock_key is empty: give the name or id of a key in the key store")
        entry = self._snapshot()
        locking = self.params["state"] == "locked"
        # Unlocking reads the key the task gives only when there is a lock to check it against.
        wants_key = self.params.get("lock_key") is not None and (locking or entry["lock_key"] is not None)
        named = self._key(self.params["lock_key"]) if wants_key else None
        held = self._holder(entry, named)
        before = self._options(entry, held)
        if locking:
            changed = self._plan_lock(entry, held, named)
        else:
            changed = held is not None
            if changed and named is not None and held is not named:
                raise QumuloError(f"snapshot {entry['name']} is locked with the key {held['name']}, not {named['name']}; the task names another key")
            if changed:
                self._check_private_key(held, "unlock the snapshot")
        after = self._options(entry, named if locking else None)
        if changed and not self.module.check_mode:
            if locking:
                self._lock(entry, named)
            else:
                self._unlock(entry)
        result = {"changed": changed, self.RETURN_KEY: after}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": before, "after": after}
        return result

    def _holder(self, entry, wanted):
        """The key that locks the snapshot, or None; read only when it is not the wanted key."""
        if entry["lock_key"] is None:
            return None
        if wanted is not None and str(entry["lock_key"]) == str(wanted["id"]):
            return wanted
        return self._key(entry["lock_key"])

    def _snapshot(self):
        if self.params.get("snapshot_id") is not None:
            found = self.client.get_or_none(SNAPSHOT_STATUS, params={"id": self.params["snapshot_id"]})
            if found is None:
                raise QumuloError(f"there is no snapshot {self.params['snapshot_id']} on this cluster")
            return found.data
        named = [entry for entry in self.client.paginate(SNAPSHOT_STATUSES, "entries") if entry["name"] == self.params["name"]]
        if not named:
            raise QumuloError(f"there is no snapshot named {self.params['name']} on this cluster")
        return named[0]

    def _key(self, ref):
        try:
            return self.client.get(KEY, params={"ref": ref}).data
        except QumuloApiError as e:
            if e.status == 404 and not e.is_("http_not_found_error"):
                raise QumuloError(f"the file system key store holds no key {ref}; register it first with qumulo.cluster.security_key") from None
            raise

    def _options(self, entry, key):
        return {
            "snapshot_id": entry["id"],
            "name": entry["name"],
            "path": canonical_path(entry["source_file_path"]),
            "expiration": entry["expiration"],
            "lock_key": key["name"] if key else None,
        }

    def _plan_lock(self, entry, held, wanted):
        """Whether to lock: not when the wanted key holds the lock; refused under another key, or forever without a provable unlock."""
        if held is not None:
            if held is wanted:
                return False
            raise QumuloError(f"snapshot {entry['name']} is locked with the key {held['name']}; unlock it first with that key's private key")
        if wanted["disabled"]:
            raise QumuloError(
                f"the key {wanted['name']} is disabled, and the cluster locks nothing with a disabled key; enable it first with "
                "enabled: true on qumulo.cluster.security_key"
            )
        if entry["in_delete"]:
            raise QumuloError(f"snapshot {entry['name']} is being deleted and cannot be locked")
        if not entry["expiration"] and not self.private_key:
            # Design decision, not a gap: a permanent lock needs a provable unlock (design section 5, snapshot_lock).
            raise QumuloError(
                f"snapshot {entry['name']} never expires, and the cluster gives a locked snapshot no expiration later, so only the "
                f"private key of {wanted['name']} could ever free it; give private_key, which the module checks against the key "
                f"store, or set an expiration on the snapshot first, for example with qq snapshot_modify_snapshot --id {entry['id']} "
                "--expiration <time>"
            )
        if self.private_key:
            self._check_private_key(wanted, "lock the snapshot")
        return True

    def _check_private_key(self, key, purpose):
        if not self.private_key:
            raise QumuloError(f"to {purpose}, give private_key, the private key of {key['name']}, the key that holds the lock")
        if self.signer.public_key(self.private_key) != bare_public_key(key["public_key"]):
            raise QumuloError(f"private_key does not match the public key of {key['name']} in the key store")

    def _lock(self, entry, key):
        try:
            self.client.post(LOCK, params={"id": entry["id"]}, body={"lock_key_ref": str(key["id"])})
        except QumuloMaybeApplied as lost:
            self._reconcile(lost, entry, locked=True)

    def _unlock(self, entry):
        challenge = self.client.get(UNLOCK_CHALLENGE, params={"id": entry["id"]}).data["challenge"]
        try:
            self.client.post(UNLOCK, params={"id": entry["id"]}, body={"signature": self.signer.sign(self.private_key, challenge)})
        except QumuloMaybeApplied as lost:
            self._reconcile(lost, entry, locked=False)

    def _reconcile(self, lost, entry, locked):
        now = self.client.get(SNAPSHOT_STATUS, params={"id": entry["id"]}).data["lock_key"]
        if (now is not None) != locked:
            raise QumuloError(f"{lost}; reading back shows snapshot {entry['name']} still {'unlocked' if locked else 'locked'}", **lost.details) from None
        self.notes.append("write reconciled after a lost connection")


DEFER_LOCK_KEYS = "qumulo_allow_deferred_policy_lock_keys"


def policy_lock_key_plan(policies, keys, allow_deferred, unreadable=""):
    """What cluster_baseline's step 20 applies of qumulo_snapshot_policies, given the key store (spec 026, Decision 4).

    keys are snapshot_info's security_keys, or None with unreadable the reason when the key store could not be read. A
    policy whose declared lock_key names no key there is refused, naming the remedies; with allow_deferred it is applied
    without lock_key, so it keeps any key it holds, and handed off. Returns {policies, hand_offs, refusal}.
    """
    present = set()
    for key in keys or []:
        present.update({key["name"], str(key["id"])})
    # A removal sets no key, so an absent entry's stale lock_key is not missing.
    missing = [
        policy for policy in policies if policy.get("lock_key") and policy.get("state", "present") != "absent" and str(policy["lock_key"]) not in present
    ]
    if not missing:
        return {"policies": list(policies), "hand_offs": [], "refusal": ""}
    named = ", ".join(f"{policy['policy_name']} (key {policy['lock_key']})" for policy in missing)
    why = f"the key store could not be read: {unreadable}" if keys is None else "the key store holds no such key"
    if not allow_deferred:
        return {
            "policies": list(policies),
            "hand_offs": [],
            "refusal": (
                f"qumulo_snapshot_policies declares lock keys for {named}, and {why}, so the policies' snapshots would not be "
                "locked; register each key first with qumulo.cluster.security_key, or pass "
                f"-e {DEFER_LOCK_KEYS}=true to apply the policies without their keys now and set the keys on a later run"
            ),
        }
    deferred = {policy["policy_name"] for policy in missing}
    applied = [{k: v for k, v in policy.items() if k != "lock_key"} if policy["policy_name"] in deferred else policy for policy in policies]
    remedy = (
        f"the key store could not be read ({unreadable}): give the API user PRIVILEGE_FS_KEY_MANAGEMENT_READ, and register the key "
        "with qumulo.cluster.security_key if it is not, then run the baseline again, which sets it"
        if keys is None
        else "register the key with qumulo.cluster.security_key, then run the baseline again, which sets it"
    )
    hand_offs = [
        f"The snapshot policy {policy['policy_name']} does not lock its snapshots with its lock key {policy['lock_key']}, and keeps "
        f"the key it holds, if any: {remedy}."
        for policy in missing
    ]
    return {"policies": applied, "hand_offs": hand_offs, "refusal": ""}
