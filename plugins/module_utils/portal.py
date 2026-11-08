# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The portal area: settings and objects that portals between clusters use.

A portal is a relationship between a spoke cluster and a hub cluster. Each side keeps its own record of it,
a spoke on the spoke and a hub on the hub, under server ids that differ between the two sides and change when
a portal is made again. The modules here find a portal by the pair of cluster UUIDs instead, and a root by its
path.

Spokes and hubs carry no ETag (G-PORTAL-ETAG), and a PATCH that repeats the stored value still rewrites the
record and re-runs its connectivity checks (QUIRK-005), so every PATCH here follows a diff.
"""

from __future__ import annotations

import posixpath
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import CLUSTER_INFO_SUBSETS, FILE_ATTRIBUTES, HUBS, SPOKES, node_state
from ansible_collections.qumulo.cluster.plugins.module_utils.polling import UNANSWERED, PollingResource
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Field, Settings
from ansible_collections.qumulo.cluster.plugins.module_utils.version import cluster_version, supports

EVICTION = Route("/v1/portal/spokes/eviction-settings", ("GET", "PATCH"))
NO_ETAG = "the GET returns no ETag (G-PORTAL-ETAG); writes follow a diff, since a same-value PATCH rewrites the record (QUIRK-005)"
SPOKE = Route("/v2/portal/spokes/{id}", ("GET", "PATCH", "DELETE"), etag="omit", why=NO_ETAG)
SPOKE_ROOTS = Route("/v2/portal/spokes/{id}/roots/", ("POST",))
HUB = Route("/v2/portal/hubs/{id}", ("GET", "PATCH", "DELETE"), etag="omit", why=NO_ETAG)
HUB_ACCEPT = Route("/v2/portal/hubs/{id}/accept", ("POST",))
HUB_ROOT = Route("/v2/portal/hubs/{id}/roots/{dir}", ("POST",))
FLOAT_TOLERANCE = 1e-6

PENDING, ACCEPTED, DELETING = "PENDING", "ACCEPTED", "DELETING"
ACTIVE, DISCONNECTED = "ACTIVE", "DISCONNECTED"
# States of a portal on its way to another state: draining to read-only, to paused, or before deletion.
TRANSITIONS = frozenset({"QUIESCING", "PAUSING", DELETING})
DIRECTORY = "FS_FILE_TYPE_DIRECTORY"
BLOCKED = "blocked_by_portal_transition_error"  # a delete refused while the portal changes type


def _close(a, b):
    return abs(a - b) < FLOAT_TOLERANCE


class EvictionSettings(Settings):
    RETURN_KEY = "eviction_settings"
    ROUTE = EVICTION
    FIELDS: ClassVar[dict[str, Field]] = {"free_threshold": Field("free_threshold", compare=_close)}

    def __init__(self, module, client):
        super().__init__(module, client)
        want = self.params["free_threshold"]
        if not 0.0 <= want <= 1.0:
            raise QumuloError(f"free_threshold must be within [0.0, 1.0], got {want}")


def canonical_path(path, option):
    """An absolute path without a trailing slash, as the modules compare paths; the server ends directory paths with one."""
    if not path.startswith("/"):
        raise QumuloError(f"{option} must be an absolute path, not {path!r}")
    return path.rstrip("/") or "/"


def wire_type(option):
    return "PORTAL_" + option.upper()


def option_type(wire):
    return wire.removeprefix("PORTAL_").lower()


def hosts(entries):
    return [{"address": host["address"], "port": int(host["port"])} for host in entries]


def path_of(client, file_id):
    """The path of a directory on this cluster, from its file id; None when it no longer exists. Read once per id."""
    paths = client.memo.setdefault("portal_paths", {})
    if file_id not in paths:
        found = client.get_or_none(FILE_ATTRIBUTES, params={"ref": file_id})
        paths[file_id] = canonical_path(found.data["path"], "path") if found else None
    return paths[file_id]


def spoke_roots(client):
    """{spoke root path: hub cluster UUID} of every spoke portal on this cluster whose root directory still exists."""
    roots = {}
    for spoke in client.get(SPOKES).data["entries"]:
        for root in spoke["roots"]:
            path = path_of(client, root["local_root"])
            if path:
                roots[path] = _lower(spoke["hub_cluster_uuid"])
    return roots


def _lower(uuid):
    """UUIDs in the shape both sides share are lower case, as the modules compare them."""
    return uuid.lower() if uuid else uuid


def _sorted_roots(roots):
    return sorted(roots, key=lambda root: (root["spoke_root"] or "", root["hub_root"] or ""))


def describe_spoke(client, spoke):
    """A spoke record in the shape both sides share. A spoke does not record where the hub reaches it."""
    return {
        "hub_cluster_uuid": _lower(spoke["hub_cluster_uuid"]),
        "spoke_cluster_uuid": _lower(node_state(client)[0]),
        "hub_hosts": hosts(spoke["hub_hosts"]),
        "spoke_hosts": None,
        "type": option_type(spoke["type"]),
        "state": spoke["state"],
        "status": spoke["status"],
        "roots": _sorted_roots(
            {
                "spoke_root": path_of(client, root["local_root"]),
                # The spoke learns the hub root's path from the hub, so it is null until the hub reports it.
                "hub_root": canonical_path(root["remote_root_path"], "remote_root_path") if root.get("remote_root_path") else None,
                "authorized": bool(root["authorized"]),
            }
            for root in spoke["roots"]
        ),
    }


def describe_hub(client, hub):
    """A hub record in the shape both sides share. A hub knows its roots by hub path only."""
    roots = [(file_id, False) for file_id in hub["pending_roots"]] + [(file_id, True) for file_id in hub["authorized_roots"]]
    return {
        "hub_cluster_uuid": _lower(node_state(client)[0]),
        "spoke_cluster_uuid": _lower(hub["spoke_cluster_uuid"]),
        "hub_hosts": None,
        "spoke_hosts": hosts(hub["spoke_hosts"]),
        "type": option_type(hub["type"]),
        "state": hub["state"],
        "status": hub["status"],
        "roots": _sorted_roots({"spoke_root": None, "hub_root": path_of(client, file_id), "authorized": authorized} for file_id, authorized in roots),
    }


def transitional_states(client):
    """TRANSITIONS, and SUSPENDING on the releases that have it."""
    return TRANSITIONS | ({"SUSPENDING"} if supports(client, "portal.suspending") else frozenset())


def _spokes(client):
    return {"spokes": [describe_spoke(client, spoke) for spoke in client.get(SPOKES).data["entries"]]}


def _hubs(client):
    return {"hubs": [describe_hub(client, hub) for hub in client.get(HUBS).data["entries"]]}


def _eviction(client):
    return {"eviction_settings": {"free_threshold": client.get(EVICTION).data["free_threshold"]}}


def _cache(client):
    # cluster_info's filesystem subset owns the read of /v1/file-system.
    return {"portal_cache_size_bytes": CLUSTER_INFO_SUBSETS["filesystem"](client)["filesystem"].get("portal_cache_size_bytes")}


# gather_subset name -> function of the client returning a dict of that subset's facts, in gathering order
PORTAL_INFO_SUBSETS = {"spokes": _spokes, "hubs": _hubs, "eviction": _eviction, "cache": _cache}


def _same_uuid(a, b):
    return (a or "").lower() == (b or "").lower()


def _root_map(roots):
    return {root["spoke_root"]: root["hub_root"] for root in roots or []}


class _Portal(PollingResource):
    """What a spoke and a hub share: the result shape, and deletes that wait out portal transitions."""

    RETURN_KEY = "portal"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.portal_id = None

    def _refuse_disconnected(self, record):
        if self.params["state"] == "absent" and record["status"] == DISCONNECTED and not self.params["allow_data_loss"]:
            raise QumuloError(f"{self.describe()} is DISCONNECTED, {self.LOSS}; set allow_data_loss=true to force the delete")

    def delete(self, current):
        """Delete, retrying while a portal transition blocks it. Forced only for a DISCONNECTED portal, which fetch() lets through only with allow_data_loss."""
        query = {"force": True} if current.data["status"] == DISCONNECTED else None
        while True:
            try:
                self.client.delete(self.ROUTE, params={"id": self.portal_id}, query=query)
                return
            except QumuloMaybeApplied:
                if not self._deleting():
                    raise
                self.notes.append("write reconciled after a lost connection")
                return
            except QumuloApiError as e:
                if not e.is_(BLOCKED):
                    raise
                if not self._pause():
                    raise QumuloError(f"{self.describe()} could not be deleted within {self.timeout}s: {e}", changed=False) from None

    def _deleting(self):
        """After a lost DELETE: whether one read shows the portal gone or DELETING, so that the delete took."""
        record = self._poll(self.ROUTE, id=self.portal_id)
        return record is None or (record is not UNANSWERED and record["state"] == DELETING)

    def settle_absent(self):
        if not self.params["wait"]:
            return
        last = None
        while True:
            record = self._poll(self.ROUTE, id=self.portal_id)
            if record is None:
                return
            if record is not UNANSWERED:
                last = record
            if not self._pause():
                where = f"still {last['state']} (status {last['status']})" if last else "not answering"
                raise QumuloError(f"{self.describe()} is {where} after {self.timeout}s")


class PortalSpoke(_Portal):
    """portal_spoke: a spoke portal found by its hub's cluster UUID, and its roots by spoke root path."""

    ROUTE = SPOKE
    READ_ONLY = frozenset({"spoke_cluster_uuid", "spoke_hosts", "state", "status"})
    UNORDERED = frozenset({"hub_hosts"})
    LOSS = "so deleting it discards spoke writes the hub has not received"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.hub_uuid = self.params["hub_cluster_uuid"].strip().lower()
        self.roots = []
        for root in self.params["roots"]:
            spoke_root = canonical_path(root["spoke_root"], "roots[].spoke_root")
            if spoke_root == "/":
                raise QumuloError("roots[].spoke_root cannot be /: the server creates the spoke root")
            self.roots.append({"spoke_root": spoke_root, "hub_root": canonical_path(root["hub_root"], "roots[].hub_root")})

    def describe(self):
        return f"the spoke portal to hub {self.hub_uuid}"

    def fetch(self):
        matches = [spoke for spoke in self.client.get(SPOKES).data["entries"] if _same_uuid(spoke["hub_cluster_uuid"], self.hub_uuid)]
        if len(matches) > 1:
            ids = ", ".join(str(spoke["id"]) for spoke in matches)
            raise QumuloError(f"this cluster has {len(matches)} spoke portals to hub {self.hub_uuid} (ids {ids}); delete all but one")
        if not matches:
            return None
        self._refuse_disconnected(matches[0])
        self.portal_id = str(matches[0]["id"])
        return Current(self.portal_id, matches[0])

    def normalize(self, current):
        return describe_spoke(self.client, current.data)

    def desired(self, before):
        self.before = before
        hub_hosts = hosts(self.params["hub_hosts"]) if self.params["hub_hosts"] is not None else before.get("hub_hosts")
        if hub_hosts is None:
            raise QumuloError(f"hub_hosts is needed to create the spoke portal to hub {self.hub_uuid}")
        kind = self.params["type"]
        if before and kind != before["type"]:
            self.notes.append(f"the hub decides the type of this portal, {before['type']}; change it to {kind} with qumulo.cluster.portal_hub")
            kind = before["type"]
        known = _root_map(before.get("roots"))
        roots = list(before.get("roots") or [])
        for root in self.roots:
            spoke_root, hub_root = root["spoke_root"], root["hub_root"]
            if spoke_root in known:
                if known[spoke_root] not in (None, hub_root):
                    raise QumuloError(
                        f"spoke root {spoke_root} already maps to hub root {known[spoke_root]}, not {hub_root}; "
                        "a root cannot change in place, so delete the spoke portal and propose it again"
                    )
                continue
            self._check_new_root(spoke_root)
            roots.append({**root, "authorized": False})
            known[spoke_root] = hub_root
        return {
            **before,
            "hub_cluster_uuid": before.get("hub_cluster_uuid", self.hub_uuid),
            "hub_hosts": hub_hosts,
            "type": kind,
            "roots": _sorted_roots(roots),
        }

    def _check_new_root(self, spoke_root):
        """Proposing cannot be retried safely (G-PORTAL-ONESHOT), so check here what the server would refuse."""
        if self.client.get_or_none(FILE_ATTRIBUTES, params={"ref": spoke_root}) is not None:
            raise QumuloError(f"spoke root {spoke_root} already exists on this cluster; the server creates it, so choose a path that does not exist")
        parent = posixpath.dirname(spoke_root)
        found = self.client.get_or_none(FILE_ATTRIBUTES, params={"ref": parent})
        if found is None or found.data.get("type") != DIRECTORY:
            raise QumuloError(f"the parent of spoke root {spoke_root}, {parent}, is not a directory on this cluster")

    def same(self, key, a, b):
        if key == "roots":
            a, b = _root_map(a), _root_map(b)
            return a.keys() == b.keys() and all(None in (a[k], b[k]) or a[k] == b[k] for k in a)
        return super().same(key, a, b)

    def create(self, after):
        created = self.client.post(SPOKES, body={"type": wire_type(after["type"]), "hub_hosts": after["hub_hosts"]}).data
        if not _same_uuid(created["hub_cluster_uuid"], self.hub_uuid):
            self.client.delete(SPOKE, params={"id": created["id"]})
            raise QumuloError(
                f"hub_hosts reach cluster {created['hub_cluster_uuid']}, not hub_cluster_uuid {self.hub_uuid}; "
                "the spoke portal this task proposed was withdrawn"
            )
        self.done.append(f"proposed {self.describe()} (spoke id {created['id']})")
        with self._after_writes():
            self._propose(created["id"], after["roots"])

    def update(self, current, after, keys):
        with self._after_writes():
            if "hub_hosts" in keys:
                self.client.patch(SPOKE, params={"id": current.id}, body={"hub_hosts": after["hub_hosts"]})
                self.done.append("changed hub_hosts")
            if "roots" in keys:
                known = _root_map(self.before["roots"])
                self._propose(current.id, [root for root in after["roots"] if root["spoke_root"] not in known])

    def _propose(self, spoke_id, roots):
        for root in roots:
            self.client.post(SPOKE_ROOTS, params={"id": spoke_id}, body={"spoke_root_path": root["spoke_root"], "hub_root_path": root["hub_root"]})
            self.done.append(f"proposed spoke root {root['spoke_root']}")


def _hub_roots(roots):
    """A hub's roots as comparable pairs; a root whose directory is gone has hub_root None."""
    return sorted(((root["hub_root"], root["authorized"]) for root in roots or []), key=lambda pair: (pair[0] or "", pair[1]))


class PortalHub(_Portal):
    """portal_hub: a hub portal found by its spoke's cluster UUID, and its roots by hub root path."""

    ROUTE = HUB
    READ_ONLY = frozenset({"hub_cluster_uuid", "hub_hosts", "status"})
    UNORDERED = frozenset({"spoke_hosts"})
    LOSS = "so a delete without force never finishes, and a forced one discards spoke writes the hub has not received (G-PORTAL-STALE)"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.spoke_uuid = self.params["spoke_cluster_uuid"].strip().lower()
        self.authorized = [canonical_path(path, "authorized_roots[]") for path in self.params["authorized_roots"]]
        self.root_ids = {}  # hub root path -> file id, for accept and authorize
        self.unsettled = False  # an unchanged portal that wait=true still has to wait for

    def describe(self):
        return f"the hub portal for spoke {self.spoke_uuid}"

    def fetch(self):
        matches = [hub for hub in self.client.get(HUBS).data["entries"] if _same_uuid(hub["spoke_cluster_uuid"], self.spoke_uuid)]
        if len(matches) > 1:
            ids = ", ".join(str(hub["id"]) for hub in matches)
            raise QumuloError(f"this cluster has {len(matches)} hub portals for spoke {self.spoke_uuid} (ids {ids}); delete all but one")
        if not matches:
            return None
        record = matches[0]
        self.portal_id = str(record["id"])
        if self._going_away(record):
            record = self._wait_while_going_away(record)
            if record is None:
                return None
        self._refuse_disconnected(record)
        return Current(self.portal_id, record)

    def _going_away(self, record):
        """DELETING and DISCONNECTED, as a hub portal reads for a few seconds after its spoke's delete, or for good after a delete without force."""
        waits = self.params["state"] == "absent" and self.params["wait"] and not self.params["allow_data_loss"] and not self.module.check_mode
        return waits and record["state"] == DELETING and record["status"] == DISCONNECTED

    def _wait_while_going_away(self, record):
        """The record once it is no longer going away, None once it is gone, or the last one read at wait_timeout."""
        while self._going_away(record):
            if not self._pause():
                return record
            polled = self._poll(HUB, id=self.portal_id)
            if polled is None:
                return None
            if polled is not UNANSWERED:
                record = polled
        return record

    def normalize(self, current):
        for file_id in [*current.data["pending_roots"], *current.data["authorized_roots"]]:
            self.root_ids[path_of(self.client, file_id)] = file_id
        return describe_hub(self.client, current.data)

    def desired(self, before):
        self.before = before
        spoke_hosts = hosts(self.params["spoke_hosts"]) if self.params["spoke_hosts"] is not None else before.get("spoke_hosts")
        if not before:
            # Only a check-mode run predicts the accept; in a real run, create() fails after this.
            self.notes.append(f"spoke {self.spoke_uuid} has proposed no portal to this hub yet; qumulo.cluster.portal_spoke on the spoke proposes it")
            predicted = {
                "spoke_cluster_uuid": self.spoke_uuid,
                "spoke_hosts": spoke_hosts,
                "type": self.params["type"],
                "state": ACCEPTED,
                "roots": [{"spoke_root": None, "hub_root": path, "authorized": True} for path in self.authorized],
            }
            return {key: value for key, value in predicted.items() if value is not None}
        state = before["state"]
        if state == DELETING:
            raise QumuloError(f"{self.describe()} is being deleted; wait until it is gone, then propose it again from the spoke")
        if state == PENDING:
            if self.params["spoke_hosts"] is None:
                raise QumuloError(f"spoke_hosts is needed to accept {self.describe()}")
            state = ACCEPTED  # accepting is the one change that moves the state
        roots = {root["hub_root"]: dict(root) for root in before["roots"]}
        for path in self.authorized:
            if path not in roots:
                raise QumuloError(f"hub root {path} is not proposed by spoke {self.spoke_uuid}; propose it with qumulo.cluster.portal_spoke on the spoke first")
            roots[path]["authorized"] = True
        settling = state in transitional_states(self.client) or before["status"] != ACTIVE
        self.unsettled = settling and self.params["wait"] and not self.module.check_mode
        return {
            **before,
            "spoke_hosts": spoke_hosts,
            "type": self.params["type"] or before["type"],
            "state": state,
            "roots": _sorted_roots(roots.values()),
        }

    def same(self, key, a, b):
        if key == "roots":
            return _hub_roots(a) == _hub_roots(b)
        return super().same(key, a, b)

    def run(self):
        """As Resource.run(), and with wait=true an unchanged portal that is still settling is waited for too."""
        result = super().run()
        if self.unsettled and not result["changed"]:
            try:
                result[self.RETURN_KEY] = self.settle(result[self.RETURN_KEY])
            except QumuloError as e:
                e.details.setdefault("changed", False)
                raise
        return result

    def create(self, after):
        raise QumuloError(f"spoke {self.spoke_uuid} has proposed no portal to this hub; run qumulo.cluster.portal_spoke on the spoke first")

    def update(self, current, after, keys):
        newly = [root["hub_root"] for root in after["roots"] if root["authorized"] and not self._authorized(root["hub_root"])]
        patch = {}
        with self._after_writes():
            if "state" in keys:
                authorized = [self.root_ids[root["hub_root"]] for root in after["roots"] if root["authorized"]]
                self.client.post(HUB_ACCEPT, params={"id": current.id}, body={"spoke_hosts": after["spoke_hosts"], "authorized_roots": authorized})
                self.done.append("accepted the portal")
            else:
                for path in newly:
                    self.client.post(HUB_ROOT, params={"id": current.id, "dir": self.root_ids[path]})
                    self.done.append(f"authorized hub root {path}")
                if "spoke_hosts" in keys:
                    patch["spoke_hosts"] = after["spoke_hosts"]
            if after["type"] != self.before["type"]:
                patch["type"] = wire_type(after["type"])
            if patch:
                self.client.patch(HUB, params={"id": current.id}, body=patch)

    def _authorized(self, path):
        return any(root["hub_root"] == path and root["authorized"] for root in self.before["roots"])

    def settle(self, after):
        """Wait until the portal is ACCEPTED and ACTIVE as asked; with wait=false, read it once.

        An unset type is not waited for: a drain shows the old type for a while after it starts.
        """
        unasked = {"state"} | ({"type"} if self.params["type"] is None else set())
        compared = [key for key in after if key not in self.READ_ONLY | unasked]
        goal = "ACCEPTED and ACTIVE" + (f" as {after['type']}" if "type" in compared else "")
        last = None
        while True:
            record = self._poll(HUB, id=self.portal_id)
            if record is None:
                raise QumuloError(f"{self.describe()} is missing after the write")
            if record is not UNANSWERED:
                observed = self.normalize(Current(self.portal_id, record))
                if not self.params["wait"]:
                    return observed
                self._check_state(record["state"])
                if record["state"] == ACCEPTED and record["status"] == ACTIVE and all(self.same(k, observed.get(k), after[k]) for k in compared):
                    return observed
                last = observed
            if not self._pause():
                seen = f"{last['state']} (status {last['status']}, type {last['type']})" if last else "not answering"
                raise QumuloError(f"{self.describe()} is {seen} after {self.timeout}s; waited for {goal}")

    def _check_state(self, state):
        if state not in {PENDING, ACCEPTED} | transitional_states(self.client):
            raise QumuloError(f"{self.describe()} reports state {state}, which Qumulo Core {cluster_version(self.client)} does not have")

    def delete(self, current):
        """A hub portal already DELETING needs only the wait, unless its spoke is gone and only force ends it."""
        if current.data["state"] == DELETING and current.data["status"] != DISCONNECTED:
            return
        super().delete(current)
