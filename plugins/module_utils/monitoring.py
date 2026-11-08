# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The monitoring area: cloud monitoring, the cluster's Nexus connection and its Nexus registration.

/v1/support/settings and /v1/nexus/* read and write one document on the cluster, so they share one
ETag, which the cluster's background Nexus secret rotation also changes.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.client import MAX_BACKOFF, QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import NOT_OFFERED_STATUS, _read, node_state
from ansible_collections.qumulo.cluster.plugins.module_utils.nexus import CLUSTER_STATUS, FLEET_CONNECTION, NexusClient, find_cluster, mint_join_key
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Action, Current, Field, Resource, Settings

SHARED_ETAG = "one ETag covers /v1/support/settings and /v1/nexus/*, and the cluster's background Nexus secret rotation changes it"
# PUT is never declared: it also writes nexus_registration_key, and a null there clears a pending join key (QUIRK-010).
SUPPORT_SETTINGS = Route("/v1/support/settings", ("GET", "PATCH"), etag="drift", why=SHARED_ETAG)
# PATCH names /v1/support/status as its monitor URI, but only /v1/support/status/ is served (QUIRK-003).
SUPPORT_STATUS = Route("/v1/support/status/", ("GET",))
NEXUS_CONNECTION = Route("/v1/nexus/connection", ("GET", "PATCH"), etag="drift", why=SHARED_ETAG)
NEXUS_REGISTRATION = Route(
    "/v1/nexus/registration",
    ("GET", "PUT", "DELETE"),
    etag="omit",
    why="the cluster's background Nexus secret rotation changes the ETag, and no registration write depends on the state it read",
)
# Registers inline when a key is stored, so its answer says whether registration worked (QUIRK-011). Never resent.
NEXUS_ROTATE = Route("/v1/nexus/registration/rotate", ("POST",))
AUDIT_SYSLOG = Route("/v1/audit/syslog/config", ("GET",))
AUDIT_SYSLOG_STATUS = Route("/v1/audit/syslog/status", ("GET",))
AUDIT_CLOUDWATCH = Route("/v1/audit/cloudwatch/config", ("GET",))
AUDIT_CLOUDWATCH_STATUS = Route("/v1/audit/cloudwatch/status", ("GET",))
# The part of the audit area that holds its CloudWatch destinations, which Terraform can own.
AUDIT_CLOUDWATCH_AREA = "audit.cloudwatch"
KEY_STORE_STATUS = Route("/v2/encryption/key-store/status", ("GET",))
# A cluster that cannot send audit logs to CloudWatch answers its CloudWatch routes with HTTP 403 and this class.
NO_CLOUDWATCH = "audit_to_cloudwatch_platform_error"

NONE, PENDING, REGISTERED = "NONE", "PENDING_REGISTRATION", "REGISTERED"
DECOMMISSIONED = "DECOMMISSIONED"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
SINGLE_USE = "a join key is single-use, so set join_key to a new key, or nexus_api_token to mint one"
# Rotates sent while the cluster's own registration with a stored key is in flight, and the seconds between them.
ROTATE_ATTEMPTS = 3
ROTATE_RACE_PAUSE = 2
# How rotate reports a 5xx error page from the gateway in front of Nexus, which it could not parse (QUIRK-022).
NEXUS_GATEWAY_PAGE = re.compile(r"body was:\s*<html\b.*?<title>\s*5\d\d\b", re.IGNORECASE | re.DOTALL)

MONITORING_FIELDS = (
    "enabled",
    "mq_host",
    "mq_port",
    "mq_proxy_host",
    "mq_proxy_port",
    "s3_proxy_host",
    "s3_proxy_port",
    "s3_proxy_disable_https",
    "vpn_enabled",
    "vpn_host",
    "period",
)
NEXUS_CONNECTION_FIELDS = (
    "nexus_enabled",
    "nexus_host",
    "nexus_port",
    "nexus_interval",
    "nexus_capability_remote_support",
    "nexus_capability_sso",
)
# nexus_enabled, which the registration also reports, belongs to the nexus_connection subset.
NEXUS_REGISTRATION_FIELDS = ("registration_status", "secret_created_at")
POLL_INTERVAL = 5.0


class MonitoringSettings(Settings):
    """The cloud monitoring half of /v1/support/settings; its nexus_* fields belong to the Nexus modules."""

    RETURN_KEY = "monitoring_settings"
    ROUTE = SUPPORT_SETTINGS
    FIELDS: ClassVar[dict[str, Field]] = {name: Field(name) for name in MONITORING_FIELDS}

    def __init__(self, module, client):
        super().__init__(module, client)
        self.status = None

    def desired(self, before):
        after = super().desired(before)
        if before.get("enabled") and after.get("enabled") is False and self._response.data.get("nexus_enabled"):
            self.client.warnings.append(
                "enabled=false also disables the Nexus connection (nexus_enabled), which stays off until qumulo.cluster.nexus_connection turns it on again"
            )
        return after

    def settle(self, after):
        observed = super().settle(after)
        if self.params["wait"]:
            self.status = wait_for_monitoring(self.client, observed["enabled"], self.params["wait_timeout"])
        else:
            self.status = self.client.get(SUPPORT_STATUS).data
        return observed

    def result(self, changed, before, after, keys):
        result = super().result(changed, before, after, keys)
        if self.status is not None:
            result["monitoring_status"] = self.status
        return result


class NexusConnection(Settings):
    RETURN_KEY = "nexus_connection"
    ROUTE = NEXUS_CONNECTION
    FIELDS: ClassVar[dict[str, Field]] = {name: Field(name) for name in NEXUS_CONNECTION_FIELDS}

    def desired(self, before):
        after = super().desired(before)
        # This route accepts the change with monitoring off, though /v1/support/settings refuses it; refuse it here too.
        if after.get("nexus_enabled") and not before.get("nexus_enabled") and not monitoring_enabled(self.client):
            refuse_unless_check_mode(
                self.module,
                self.client,
                "nexus_enabled=true needs cloud monitoring on, and it is off; turn it on first with qumulo.cluster.monitoring_settings (enabled: true)",
            )
        return after


class NexusRegistration:
    """nexus_registration: registers by the cluster's status, so a registered cluster is never sent a key (G-NEXUS-KEY)."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.notes = []

    def run(self):
        before = self._read()
        if self.params["state"] == "absent":
            return self._absent(before)
        return self._present(before)

    def _present(self, before):
        status = before["registration_status"]
        if status == REGISTERED:
            return self._result(False, before, before)
        if not before.get("nexus_enabled"):
            refuse_unless_check_mode(
                self.module,
                self.client,
                "registering with Nexus needs the Nexus connection on, and it is off; turn it on first with "
                "qumulo.cluster.nexus_connection (nexus_enabled: true)",
            )
        if self.params.get("join_key"):
            source = "given"
        elif status == PENDING:
            source = "stored"
        elif self.params.get("nexus_api_token"):
            source = "minted"
        else:
            raise QumuloError("the cluster is not registered with Nexus; set join_key, or nexus_api_token to mint one")
        if self.module.check_mode:
            return self._result(True, before, {**before, "registration_status": REGISTERED}, source)
        if source == "stored":
            source, settled = self._register_with_the_stored_key()
            if source == "stored":
                return self._result(True, before, settled or self._expect(REGISTERED), source)
        key = self.params["join_key"] if source == "given" else self._mint()
        self.client.put(NEXUS_REGISTRATION, body={"join_key": key})
        try:
            settled = self._rotate()
        except QumuloApiError as e:
            raise QumuloError(f"the cluster stored the join key, but registering with it failed: {e}; {SINGLE_USE}", changed=True) from None
        return self._result(True, before, settled or self._expect(REGISTERED), source)

    def _rotate(self):
        """POSTs rotate, which registers with the stored key: the registration it read when the status had to settle
        whether the cluster registered, None when rotate answered."""
        races, backoff = 0, ROTATE_RACE_PAUSE
        deadline = self.client.clock() + self.params["retry_timeout"]
        while True:
            try:
                return self._post_rotate()
            except QumuloApiError as e:
                error = e
            if error.status == 412 and races < ROTATE_ATTEMPTS - 1:
                # A cluster that stores a key registers with it by itself within seconds, and a rotate in that window
                # answers 412 having done nothing (QUIRK-018).
                races += 1
                pause = ROTATE_RACE_PAUSE
            elif _nexus_gateway_page(error) and self.client.clock() < deadline:
                # The gateway in front of Nexus failed the cluster's call, so a later rotate may get through (QUIRK-022).
                pause = min(backoff, deadline - self.client.clock())
                backoff = min(backoff * 2, MAX_BACKOFF)
            else:
                raise error
            current = self._read()
            if current["registration_status"] == REGISTERED:
                return current
            if error.status != 412 and current["registration_status"] != PENDING:
                raise error  # no key is stored any more, so another rotate has nothing to register with
            self.client.sleep(pause)

    def _post_rotate(self):
        try:
            self.client.post(NEXUS_ROTATE)
            return None
        except QumuloMaybeApplied as e:
            current = self._read()
            if current["registration_status"] != REGISTERED:
                raise QumuloError(f"{e}; the registration status is {current['registration_status']}; {SINGLE_USE}", changed=True, maybe_applied=True) from None
            return current

    def _register_with_the_stored_key(self):
        """("stored", registration) when the stored key registered the cluster; ("minted", None) when it failed and a
        token can mint another."""
        try:
            return "stored", self._rotate()
        except QumuloApiError as e:
            if not self.params.get("nexus_api_token"):
                raise QumuloError(f"registering with the join key the cluster stores failed: {e}; {SINGLE_USE}", changed=False) from None
            self.notes.append(f"the join key the cluster stored did not register it ({e}); registered with a newly minted key")
            return "minted", None

    def _mint(self):
        cluster_id = node_state(self.client)[0]
        return mint_join_key(NexusClient(self.params), cluster_id, self.params.get("nexus_account_id"))

    def _absent(self, before):
        if before["registration_status"] == NONE:
            return self._result(False, before, before)
        if before["registration_status"] not in (REGISTERED, PENDING):
            raise QumuloError(
                f"the registration status is {before['registration_status']}, which the module does not know how to remove; nothing was changed",
                changed=False,
            )
        if self.module.check_mode:
            return self._result(True, before, {**before, "registration_status": NONE, "secret_created_at": None})
        current = before
        if current["registration_status"] == REGISTERED:
            self.client.delete(NEXUS_REGISTRATION)  # clears the secret only; a stored key leaves the status PENDING
            current = self._read()
        if current["registration_status"] == PENDING:
            self.client.read_modify_write(SUPPORT_SETTINGS, _clear_stored_key, method="PATCH")
            current = self._read()
        if current["registration_status"] != NONE:
            raise QumuloError(f"the registration status is {current['registration_status']}, not NONE, after removing the registration", changed=True)
        return self._result(True, before, current)

    def _read(self):
        return self.client.get(NEXUS_REGISTRATION).data

    def _expect(self, wanted):
        current = self._read()
        if current["registration_status"] != wanted:
            raise QumuloError(f"the registration status is {current['registration_status']}, not {wanted}, after registering", changed=True)
        return current

    def _result(self, changed, before, after, source=None):
        result = {"changed": changed, "registration": after}
        if source and changed:
            result["join_key_source"] = source
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"registration_status": before["registration_status"]}, "after": {"registration_status": after["registration_status"]}}
        return result


class NexusCluster:
    """nexus_cluster: retires a cluster UUID in Nexus, once, and never one that is still in use."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.notes = []

    def run(self):
        uuid = self.params["cluster_uuid"].strip()
        if not UUID.match(uuid):
            raise QumuloError(f"cluster_uuid must be a cluster UUID, not {uuid!r}")
        nexus = NexusClient(self.params)
        row = find_cluster(nexus, uuid)
        if row is None:
            self.notes.append(f"Nexus has no cluster {uuid}, so there is nothing to retire")
            return self._result(False, None, None)
        if row.get("status") == DECOMMISSIONED:
            return self._result(False, row, row)
        self._refuse_the_registered_cluster_at_api_host(uuid)
        fleet = nexus.get(FLEET_CONNECTION, params={"uuid": uuid}) or {}
        connected = fleet.get("connected")
        if connected and not self.params["allow_connected"]:
            raise QumuloError(
                f"cluster {uuid} is still connected to Nexus (last seen {fleet.get('last_seen')}); a cluster that still "
                "connects is usually in use, so set allow_connected: true to retire it anyway"
            )
        if self.module.check_mode:
            return self._result(True, row, {**row, "status": DECOMMISSIONED}, connected)
        nexus.patch(CLUSTER_STATUS, params={"uuid": uuid}, body={"status": DECOMMISSIONED})
        after = find_cluster(nexus, uuid)
        if after is None:
            raise QumuloError(f"Nexus no longer lists cluster {uuid} after retiring it, so the module cannot confirm its status", changed=True)
        if after.get("status") is None:
            # Idempotence rests on reading DECOMMISSIONED back; without it every run would PATCH again.
            raise QumuloError(
                f"Nexus accepted retiring cluster {uuid}, but its listing reports no status, so the module cannot tell a retired cluster from a live one",
                changed=True,
            )
        if after["status"] != DECOMMISSIONED:
            raise QumuloError(f"Nexus reports cluster {uuid} as {after['status']}, not {DECOMMISSIONED}, after retiring it", changed=True)
        return self._result(True, row, after, connected)

    def _refuse_the_registered_cluster_at_api_host(self, uuid):
        if (node_state(self.client)[0] or "").lower() != uuid.lower():
            return
        status = self.client.get(NEXUS_REGISTRATION).data["registration_status"]
        if status != NONE:
            raise QumuloError(
                f"cluster {uuid} is the cluster at {self.params['api_host']}, and it is still registered with Nexus ({status}); "
                "remove its registration first with qumulo.cluster.nexus_registration (state: absent)"
            )

    def _result(self, changed, before, after, connected=None):
        summary = None
        if after is not None:
            summary = {key: after.get(key) for key in ("cluster_uuid", "cluster_name", "status")}
            summary["connected"] = connected
        result = {"changed": changed, "nexus_cluster": summary}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"status": (before or {}).get("status")}, "after": {"status": (after or {}).get("status")}}
        return result


AUDIT_DESTINATIONS = Route("/v2/audit/destinations/", ("GET", "POST"))
# A PATCH changes only enabled, whatever else it carries (QUIRK-062), so it is never declared.
AUDIT_DESTINATION = Route("/v2/audit/destinations/{id}", ("GET", "PUT", "DELETE"))
SYSLOG, CLOUDWATCH, LOCAL = "SYSLOG", "CLOUDWATCH", "LOCAL"
# The options of each destination type; the cluster returns the others as null.
AUDIT_TYPE_OPTIONS = {
    SYSLOG: ("server_address", "server_port", "log_format"),
    CLOUDWATCH: ("log_group_name", "region"),
    LOCAL: (),
}
# Names the cluster reserves for a destination of one type.
RESERVED_AUDIT_NAMES = {"syslog": SYSLOG, "cloudwatch": CLOUDWATCH, "local": LOCAL}


def audit_destination_options(config):
    """A destination's configuration in audit_destination's option shape: the options of its type, without nulls."""
    options = {"name": config["name"], "type": config["type"], "enabled": config["enabled"]}
    options.update({key: config[key] for key in AUDIT_TYPE_OPTIONS.get(config["type"], ()) if config.get(key) is not None})
    return options


class AuditDestination(Resource):
    RETURN_KEY = "audit_destination"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["name"]
        self._check_name(self.params.get("type"))
        owned = set() if self.params["take_over_from_terraform"] else set(self.params["terraform_owned_areas"])
        self.owns_audit = "audit" in owned  # every destination is Terraform's
        self.owns_cloudwatch = AUDIT_CLOUDWATCH_AREA in owned  # only the CloudWatch ones are

    def describe(self):
        return f"audit destination '{self.name}'"

    def _check_name(self, kind):
        reserved = RESERVED_AUDIT_NAMES.get(self.name)
        if reserved and kind and kind != reserved:
            raise QumuloError(f"the name '{self.name}' is reserved for a {reserved} destination; name this {kind} destination otherwise")
        if kind == LOCAL and self.name != "local":
            raise QumuloError(f"a LOCAL destination must be named 'local', not '{self.name}'")

    def fetch(self):
        found = [entry for entry in self.client.get(AUDIT_DESTINATIONS).data["destinations"] if entry["config"]["name"] == self.name]
        # The cluster refuses a second destination with a name in use, on create and on rename, so more than one is a
        # cluster this module cannot choose for.
        if len(found) > 1:
            raise QumuloError(f"{self.describe()} names more than one destination ({', '.join(entry['id'] for entry in found)}); rename all but one")
        response = self.client.get_or_none(AUDIT_DESTINATION, params={"id": found[0]["id"]}) if found else None
        current = Current(response.data["id"], response.data["config"], response.etag) if response else None
        if current and self.params["state"] == "absent":
            self._refuse_what_terraform_owns(current.data["type"])  # the delete is the write
        return current

    def changes(self, before, after, creating):
        keys = super().changes(before, after, creating)
        if keys or creating:
            self._refuse_what_terraform_owns(before.get("type"), after["type"])
        return keys

    def _refuse_what_terraform_owns(self, *types):
        """Refuses a write Terraform owns: any write under audit, and under audit.cloudwatch one that creates, changes or
        deletes a CloudWatch destination, or changes one to or from that type."""
        if self.owns_audit:
            what, area = "an audit destination", "audit"
        elif self.owns_cloudwatch and CLOUDWATCH in types:
            what, area = "a CloudWatch destination", AUDIT_CLOUDWATCH_AREA
        else:
            return
        raise QumuloError(
            f"{self.describe()}: this would write {what}, and terraform_owned_areas gives {area} to Terraform; manage it there, "
            "or set take_over_from_terraform: true to manage it here"
        )

    def normalize(self, current):
        return audit_destination_options(current.data)

    def desired(self, before):
        kind = self.params.get("type") or before.get("type")
        if kind is None:
            raise QumuloError(f"{self.describe()} does not exist; set type to create it")
        if kind not in AUDIT_TYPE_OPTIONS:
            raise QumuloError(f"{self.describe()} has type {kind}, which this module does not know; set type to one it knows to replace it")
        self._check_name(kind)
        given = {key: self.params[key] for key in ("enabled", *AUDIT_TYPE_OPTIONS[kind]) if self.params.get(key) is not None}
        stray = sorted(
            key for options in AUDIT_TYPE_OPTIONS.values() for key in options if key not in AUDIT_TYPE_OPTIONS[kind] and self.params.get(key) is not None
        )
        if stray:
            owner = {key: other for other, options in AUDIT_TYPE_OPTIONS.items() for key in options}
            raise QumuloError(f"{', '.join(f'{key} applies only to {owner[key]}' for key in stray)}; {self.describe()} is {kind}")
        if before.get("type") == kind:
            return {**before, **given}
        # A new destination, or a change of type, which drops the old type's options.
        return {"name": self.name, "type": kind, "enabled": before.get("enabled", True), **given}

    def _config(self, after):
        return {"type": after["type"], "name": self.name, **{key: value for key, value in after.items() if key not in ("type", "name")}}

    def create(self, after):
        self.client.post(AUDIT_DESTINATIONS, body={"config": self._config(after)})

    def update(self, current, after, keys):
        # The whole configuration as the cluster holds it, since a PUT resets what it leaves out to the cluster's
        # defaults; every type's options are null unless after sets them, so a change of type drops the old ones.
        cleared = {key: None for options in AUDIT_TYPE_OPTIONS.values() for key in options}
        body = {**current.data, **cleared, **self._config(after)}
        self.client.put(AUDIT_DESTINATION, params={"id": current.id}, body=body, if_match=current.etag)

    def delete(self, current):
        self.client.delete(AUDIT_DESTINATION, params={"id": current.id}, if_match=current.etag)


KEY_STORE = Route("/v2/encryption/key-store", ("GET",))
# Every call rotates again, so it is never resent; a lost answer is settled by reading the key store.
ROTATE_KEYS = Route("/v2/encryption/rotate-keys", ("POST",))
NOT_ENCRYPTED = "Not Encrypted"
KMS = "KMS"
SECONDS_PER_DAY = 86400
# The cluster writes up to nine digits of a second, and datetime parses at most six.
ROTATION_TIME = re.compile(r"^(?P<seconds>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(?P<fraction>\d+))?Z$")


def utc_now():
    return datetime.now(UTC)


def rotation_time(text, what="the key's last rotation"):
    """A time the cluster reports, such as last_key_rotation_time, as an aware datetime."""
    match = ROTATION_TIME.match(text)
    if not match:
        raise QumuloError(f"the cluster reports {what} as {text!r}, which the module cannot read")
    seconds = datetime.strptime(match["seconds"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    return seconds.replace(microsecond=int((match["fraction"] or "0")[:6].ljust(6, "0")))


class EncryptionKeyRotation(Action):
    """encryption_key_rotation: rotates the at-rest key only once it is older than max_age_days (G-ACTIONS)."""

    RETURN_KEY = "rotation"

    def __init__(self, module, client):
        super().__init__(module, client)
        if self.params["max_age_days"] < 0:
            raise QumuloError(f"max_age_days must be 0 or more, not {self.params['max_age_days']}")
        self.notes = []
        self.before = None
        self.configured = None  # when a KMS key store was configured, the age of a key it never rotated

    def run(self):
        result = super().run()
        if result["changed"] and not self.module.check_mode:
            result["key_store"] = self.after
        else:
            result["key_store"] = self.before
        if self.notes:
            result["notes"] = self.notes
        return result

    def read(self):
        store = self.client.get(KEY_STORE).data
        if store["type"] != KMS and (store.get("local_store") or {}).get("status") == NOT_ENCRYPTED:
            raise QumuloError("the cluster is not encrypted at rest, so it has no key to rotate; encryption is chosen when a cluster is formed")
        rotated = self.client.get(KEY_STORE_STATUS).data["last_key_rotation_time"]
        found = {"type": store["type"], "last_key_rotation_time": rotated}
        if store["type"] == KMS:
            found["key_id"] = store["kms_store"]["key_id"]
            self.configured = store["kms_store"].get("config_creation_time")
        return found

    def plan(self):
        self.before = self.read()
        rotated = self.before["last_key_rotation_time"]
        # The key is aged from the later of its rotation and a KMS key store's configuration; the cluster's own key store
        # has no configuration time, so there a key it never rotated has no age and is due.
        times = {}
        if rotated:
            times["rotation"] = rotation_time(rotated)
        if self.configured:
            times["configuration"] = rotation_time(self.configured, "the KMS configuration's creation time")
        aged_from = max(times, key=times.get) if times else None
        age = None if aged_from is None else (utc_now() - times[aged_from]).total_seconds() / SECONDS_PER_DAY
        due = age is None or age > self.params["max_age_days"]
        plan = {"key_store": self.before["type"], "last_key_rotation_time": rotated, "age_days": None if age is None else round(age, 2)}
        if aged_from == "configuration":
            plan["aged_from"] = "configuration"
        key_id = self.params.get("key_id")
        if self.before["type"] != KMS:
            if key_id:
                raise QumuloError("key_id needs a KMS key store, and the cluster uses its own; leave key_id unset to rotate its key")
            return plan if due else None
        if key_id:
            return {**plan, "key_id": key_id} if key_id != self.before["key_id"] else None
        if due:
            age_text = "reports no rotation or configuration time" if age is None else f"is {plan['age_days']} days old, more than max_age_days"
            raise QumuloError(
                f"the KMS key {self.before['key_id']} {age_text}; the cluster "
                "rotates a KMS key only to another key the KMS holds, so set key_id to its id (G-MON-KMS-ROTATE)"
            )
        return None

    def perform(self, plan):
        body = {"key_id": plan["key_id"]} if "key_id" in plan else {}
        try:
            self.client.post(ROTATE_KEYS, body=body)
        except QumuloMaybeApplied as lost:
            self.after = self.read()
            if not self._rotated(plan):
                raise QumuloError(f"{lost}; the key store does not show a rotation", changed=True, maybe_applied=True) from None
            self.notes.append("rotation confirmed by reading back after a lost connection")
            return
        self.after = self.read()
        if not self._rotated(plan):
            raise QumuloError(
                f"the cluster accepted the rotation but still reports the key rotated at {self.after['last_key_rotation_time']}"
                + (f" with key {self.after.get('key_id')}" if "key_id" in plan else ""),
                changed=True,
            )

    def _rotated(self, plan):
        if "key_id" in plan:
            return self.after.get("key_id") == plan["key_id"]
        return self.after["last_key_rotation_time"] != plan["last_key_rotation_time"]


def _nexus_gateway_page(error):
    """Whether rotate failed on a 5xx error page from the gateway in front of Nexus, which a later rotate may get past."""
    return error.status == 400 and error.is_("decode_error") and NEXUS_GATEWAY_PAGE.search(error.description) is not None


def _clear_stored_key(data):
    """The PATCH body that clears a stored join key: empty, never null, which only a PUT would send (QUIRK-010)."""
    return {"nexus_registration_key": ""} if data.get("nexus_registration_key") else None


def refuse_unless_check_mode(module, client, reason):
    """Fails on an unmet precondition, but only warns in check mode, where the earlier task that meets it changed nothing."""
    if not module.check_mode:
        raise QumuloError(reason)
    client.warnings.append(f"{reason}. A real run fails here unless an earlier task meets this first.")


def monitoring_enabled(client):
    return bool(client.get(SUPPORT_SETTINGS).data.get("enabled"))


def wait_for_monitoring(client, enabled, timeout):
    """Each node's status, once every node reports monitoring CONNECTED (or DISABLED when it is off); ERROR fails at once."""
    want = "CONNECTED" if enabled else "DISABLED"
    deadline = client.clock() + timeout
    while True:
        nodes = client.get(SUPPORT_STATUS).data or []
        for node in nodes:
            if node.get("monitoring") == "ERROR":
                raise QumuloError(
                    f"node {node.get('node_id')} reports monitoring ERROR; check mq_host, the proxy settings and that the cluster can reach them",
                    monitoring_status=nodes,
                )
        if nodes and all(node.get("monitoring") == want for node in nodes):
            return nodes
        left = deadline - client.clock()
        if left <= 0:
            seen = ", ".join(f"node {node.get('node_id')}: {node.get('monitoring')}" for node in nodes) or "no nodes listed"
            raise QumuloError(f"monitoring is not {want} on every node after {timeout}s ({seen})", monitoring_status=nodes)
        client.sleep(min(POLL_INTERVAL, left))


def _fields(data, names):
    return {name: data[name] for name in names if name in data}


def _settings_subset(client):
    return {"settings": _fields(_read(client, SUPPORT_SETTINGS), MONITORING_FIELDS)}


def _status_subset(client):
    return {"status": _read(client, SUPPORT_STATUS)}


def _nexus_connection_subset(client):
    return {"nexus_connection": _fields(_read(client, NEXUS_CONNECTION), NEXUS_CONNECTION_FIELDS)}


def _nexus_registration_subset(client):
    return {"nexus_registration": _fields(_read(client, NEXUS_REGISTRATION), NEXUS_REGISTRATION_FIELDS)}


def _audit_subset(client):
    syslog = {**_read(client, AUDIT_SYSLOG), "status": _read(client, AUDIT_SYSLOG_STATUS)}
    try:
        cloudwatch = {**client.get(AUDIT_CLOUDWATCH).data, "status": client.get(AUDIT_CLOUDWATCH_STATUS).data}
    except QumuloApiError as e:
        if not (e.is_(NO_CLOUDWATCH) or e.status in NOT_OFFERED_STATUS):
            raise
        cloudwatch = None
    return {"audit": {"syslog": syslog, "cloudwatch": cloudwatch}}


def _encryption_subset(client):
    return {"encryption": _read(client, KEY_STORE_STATUS)}


# gather_subset name -> function of the client returning a dict of that subset's facts, in gathering order.
# None reads /v1/support/vpn-keys, which returns a private key in cleartext.
MONITORING_INFO_SUBSETS = {
    "settings": _settings_subset,
    "status": _status_subset,
    "nexus_connection": _nexus_connection_subset,
    "nexus_registration": _nexus_registration_subset,
    "audit": _audit_subset,
    "encryption": _encryption_subset,
}
