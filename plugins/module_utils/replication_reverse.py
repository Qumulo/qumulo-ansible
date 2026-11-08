# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""A failover's verbs on one replication relationship, named by its id (research R18).

- make_writable, on the relationship's target, from ESTABLISHED with a recovery point: the target directory goes back
  to the recovery point and becomes writable, and the relationship goes DISCONNECTED on both sides.
- reverse, on the current target, from DISCONNECTED: this cluster's target record becomes a source record of the same
  id, to the current source at source_address and source_port, and the current source's record a target record.
- reconnect, on the relationship's target, from DISCONNECTED: the target directory goes back to the recovery point and
  becomes read-only again, and the relationship goes RECONNECTING, then ESTABLISHED. After a reverse that target is the
  old source.

Without allow_data_loss, a reconnect first compares the target directory with the recovery point's snapshot, through a
snapshot of the directory taken now, and is sent only when nothing was written there since (specs/025-replication-failover,
Decision 6); with it, the reconnect is sent without comparing. A make-writable always needs allow_data_loss: its target is
read-only, so all it can discard is a job's partial changes.

None of the three can be resent safely, so each is sent only from its own state, and a lost answer is reconciled by
reading the record, never by sending again (G-REPL-ONESHOT). The module never waits: a wait is a read-only poll of
replication_info, which can send no verb.
"""

from __future__ import annotations

import ipaddress
from itertools import islice

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import (
    ESTABLISHED,
    SHAPE,
    SOURCE_REVERSE,
    TARGET_MAKE_WRITABLE,
    TARGET_RECONNECT,
    TARGET_STATUS,
    read_by_id,
    same_address,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Resource
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import CHANGES_SINCE, SNAPSHOT_DELETE, SNAPSHOTS, expiration

DISCONNECTED = "DISCONNECTED"
# The states a make-writable leaves, and a reconnect.
WRITABLE = frozenset({"DISCONNECTING", DISCONNECTED})
RECONNECTED = frozenset({"RECONNECTING", "REESTABLISHING", ESTABLISHED})
NOT_FOUND = "relationship_manager_relationship_not_found_error"
NOT_DISCONNECTED = "relationship_manager_not_disconnected_error"
# What each verb needs first when the server answers that the relationship is not DISCONNECTED.
NOT_DISCONNECTED_ADVICE = {
    "reverse": ", so make_writable comes first",
    "reconnect": "; the reconnect needs the relationship DISCONNECTED, as the reverse leaves it, so run again to read it",
}
# The changes a refused reconnect names in its message; the count says how many more there are.
SHOWN_CHANGES = 10
# The changes the comparison reads at most; a directory with more is reported as having at least this many.
CHANGE_CAP = 1000
# The comparison's snapshot frees itself this long after it is taken, should the module stop before deleting it. The
# controller's clock sets the expiration, so a day leaves room for a clock that differs from the cluster's.
CHECK_TTL = "1day"
CHECK_SUFFIX = "reconnect_check"


class ReplicationReverse(Resource):
    """replication_reverse: one verb on the relationship relationship_id, from the state research R18 gives it."""

    RETURN_KEY = "relationship"

    def __init__(self, module, client):
        super().__init__(module, client)
        params = self.params
        self.verb = params["verb"]
        self.id = str(params["relationship_id"])
        self.check = module.check_mode
        self.allow_data_loss = params["allow_data_loss"]
        if self.verb == "make_writable" and not self.allow_data_loss:
            raise QumuloError(
                f"{self.verb} sends the target directory of relationship {self.id} back to its recovery point, discarding whatever was "
                "written there after it; set allow_data_loss to allow it"
            )
        self.address = params["source_address"]
        self.port = params["source_port"]
        if self.verb == "reverse":
            try:
                ipaddress.ip_address(self.address)
            except ValueError:
                raise QumuloError(f"source_address {self.address!r} is not an IP address; the server takes only an IP literal") from None

    def describe(self):
        return f"replication relationship {self.id}"

    def run(self):
        return {"make_writable": self._make_writable, "reverse": self._reverse, "reconnect": self._reconnect}[self.verb]()

    # The verbs

    def _make_writable(self):
        before = self._target_record("make_writable is sent to the relationship's target")
        state = before["state"]
        if state in WRITABLE:
            self.notes.append(f"{self.describe()} is already {state} here, so it was made writable before; nothing was sent")
            return self._result(False, before, before)
        if state != ESTABLISHED:
            raise QumuloError(f"{self.describe()} is {state} here; make_writable is sent only to an ESTABLISHED relationship")
        if not before["recovery_point"]:
            raise QumuloError(f"{self.describe()} has no recovery point yet; make_writable reverts the directory to it, so wait until a job completes")
        predicted = {**before, "state": DISCONNECTED, "target_root_read_only": False}
        return self._send(TARGET_MAKE_WRITABLE, {"id": self.id}, None, before, predicted, lambda: self._applied("target", WRITABLE))

    def _reverse(self):
        before = read_by_id(self.client, "target", self.id)
        if before is None:
            return self._reversed_already()
        state = before["state"]
        if state != DISCONNECTED:
            first = ", so make_writable comes first" if state == ESTABLISHED else ""
            raise QumuloError(f"{self.describe()} is {state} here; the reverse is sent only to a DISCONNECTED relationship{first}")
        predicted = {
            **dict.fromkeys(SHAPE),
            "id": before["id"],
            "source_cluster_uuid": before["target_cluster_uuid"],
            "target_cluster_uuid": before["source_cluster_uuid"],
            "source_path": before["target_path"],
            "target_path": before["source_path"],
            "target_address": self.address,
            "target_port": self.port,
            "state": DISCONNECTED,
            "recovery_point": before["recovery_point"],
        }
        body = {"target_relationship_id": self.id, "source_address": self.address, "source_port": self.port}
        return self._send(SOURCE_REVERSE, None, body, before, predicted, lambda: self._reversed_record() is not None)

    def _reconnect(self):
        before = self._target_record("reconnect is sent to the relationship's target, which after a reverse is the old source")
        state = before["state"]
        if state in RECONNECTED:
            self.notes.append(f"{self.describe()} is already {state} here, so it was reconnected before; nothing was sent")
            return self._result(False, before, before)
        if state != DISCONNECTED:
            raise QumuloError(f"{self.describe()} is {state} here; reconnect is sent only to a DISCONNECTED relationship")
        if not self.allow_data_loss:
            self._compare(before)
        predicted = {**before, "state": "RECONNECTING", "target_root_read_only": True}
        return self._send(TARGET_RECONNECT, {"id": self.id}, None, before, predicted, lambda: self._applied("target", RECONNECTED))

    # The reconnect's comparison

    def _compare(self, record):
        """Refuse the reconnect when the target directory changed since the recovery point (spec 025, FR-014 to FR-016)."""
        path, point = record["target_path"], record["recovery_point"]
        where = f"reconnect sends {path} back to the recovery point {point}, discarding whatever was written there after it"
        if self.check:
            self.notes.append(f"{where}; the real run first compares {path} with the recovery point's snapshot, which check mode cannot take")
            return
        # The described record keeps the snapshot's id only; the raw one also says which directory the snapshot is of.
        raw = self.client.get(TARGET_STATUS, params={"id": self.id}).data
        older, root = raw.get("recovery_point_snapshot") or {}, raw.get("target_root_id")
        if older.get("id") is None or not root or str(older.get("source_file_id")) != str(root):
            raise QumuloError(
                f"{where}, and the record names no recovery point snapshot of {path} to compare it with; set allow_data_loss to reconnect without comparing",
                changed=False,
            )
        body = {"source_file_id": root, "name_suffix": CHECK_SUFFIX, "expiration": expiration(CHECK_TTL)}
        try:
            newer = self.client.post(SNAPSHOTS, body=body).data["id"]
        except QumuloMaybeApplied as lost:
            # Only the comparison's snapshot may have been made; the relationship was sent nothing, so the next run decides.
            raise QumuloError(
                f"{lost}; the comparison's snapshot of {path} may exist, and expires by itself a day after it was taken. Nothing was "
                f"sent to {self.describe()}; run again to compare and reconnect",
                changed=True,
            ) from None
        try:
            changes = list(islice(self.client.iterate(CHANGES_SINCE, "entries", params={"newer_id": newer, "older_id": older["id"]}), CHANGE_CAP + 1))
        finally:
            self._delete_check_snapshot(newer)
        if changes:
            capped = len(changes) > CHANGE_CAP
            changes = changes[:CHANGE_CAP]
            shown = ", ".join(f"{change['op']} {change['path']}" for change in changes[:SHOWN_CHANGES])
            if capped:
                count, more = f"at least {CHANGE_CAP}", ", and more"
            else:
                count, more = str(len(changes)), (f", and {len(changes) - SHOWN_CHANGES} more" if len(changes) > SHOWN_CHANGES else "")
            raise QumuloError(
                f"{path} changed since the recovery point {point}: {count} changes ({shown}{more}). The {where}; copy what should be "
                "kept to the relationship's source first, or set allow_data_loss to discard it",
                changed=False,
                changes=changes,
                change_count=None if capped else len(changes),
            )
        self.notes.append(f"{path} holds no change since the recovery point {point}, so the reconnect discards nothing")

    def _delete_check_snapshot(self, snapshot_id):
        try:
            self.client.delete(SNAPSHOT_DELETE, params={"id": snapshot_id})
        except QumuloError as e:
            self.client.warnings.append(f"the comparison's snapshot {snapshot_id} was not deleted ({e}); it expires by itself a day after it was taken")

    # Reads

    def _target_record(self, where):
        record = read_by_id(self.client, "target", self.id)
        if record is None:
            raise QumuloError(f"this cluster holds no target record of {self.describe()}; {where}")
        return record

    def _reversed_record(self):
        """This cluster's source record of the relationship when it is the reverse's, to source_address and source_port."""
        record = read_by_id(self.client, "source", self.id)
        return record if record is not None and self._to_source(record) else None

    def _to_source(self, record):
        return same_address(record["target_address"], self.address) and record["target_port"] == self.port

    def _reversed_already(self):
        found = read_by_id(self.client, "source", self.id)
        if found is None:
            raise QumuloError(f"this cluster holds no record of {self.describe()}; the reverse is sent to the relationship's current target")
        if self._to_source(found):
            self.notes.append(f"{self.describe()} is already a source record here, to {self.address}:{self.port}, so it was reversed before; nothing was sent")
            return self._result(False, found, found)
        raise QumuloError(
            f"this cluster holds only the source record of {self.describe()}, to {found['target_address']}:{found['target_port']}; the reverse is "
            "sent to the relationship's current target, with the current source's address as source_address"
        )

    def _applied(self, side, states):
        record = read_by_id(self.client, side, self.id)
        return record is not None and record["state"] in states

    # The write

    def _send(self, route, params, body, before, predicted, applied):
        """Send the verb once, unless in check mode, and read the record once after it; the module never waits."""
        if self.check:
            return self._result(True, before, predicted)
        try:
            self.client.post(route, params=params, body=body)
        except QumuloMaybeApplied as lost:
            if not applied():
                raise QumuloError(
                    f"{lost}; {self.describe()} shows no sign of the {self.verb}, which is never resent; run again to read it and decide (G-REPL-ONESHOT)",
                    changed=True,
                    maybe_applied=True,
                ) from None
            self.notes.append(f"the {self.verb} was reconciled after a lost connection")
        except QumuloApiError as e:
            raise self._refusal(e) from None
        after = read_by_id(self.client, "source" if self.verb == "reverse" else "target", self.id)
        if after is None:
            raise QumuloError(f"{self.describe()} is missing here after the {self.verb}", changed=True)
        return self._result(True, before, after)

    def _refusal(self, error):
        """The server's definite refusal of the verb, as one of the module's own; nothing was changed."""
        if error.status == 404 and error.is_(NOT_FOUND):
            return QumuloError(f"{error}; this cluster no longer holds a target record of {self.describe()}", changed=False)
        if error.status == 400 and error.is_(NOT_DISCONNECTED):
            return QumuloError(f"{error}; {self.describe()} is no longer DISCONNECTED here{NOT_DISCONNECTED_ADVICE.get(self.verb, '')}", changed=False)
        return error

    def _result(self, changed, before, after):
        result = {"changed": changed, "relationship": after}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"relationship": before}, "after": {"relationship": after}}
        return result
