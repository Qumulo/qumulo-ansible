# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The source side of a replication relationship: create it, change its settings, or delete the source's records of it.

The module finds its relationship among this cluster's source records by (source path, target cluster UUID, target
path); the address and the port are settings. A create is one-shot, and registers the relationship on the target in the
same request, so every condition that stops a write is evaluated before the first one, in check mode too: the source's
rows of the overwrite table (specs/011-replication, "Overwrite and release rules"), O3 and O8, and the conditions
outside it. Both replacements, of a changed peer and of an ENDED record, create first and delete the old record only
once the new one is shown to reach the declared target UUID.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import node_state
from ansible_collections.qumulo.cluster.plugins.module_utils.files import AGGREGATES, attributes, holds_objects, require_directory
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import (
    ENDED,
    FAILOVER,
    PENDING,
    SETTINGS,
    SHAPE,
    SOURCE,
    SOURCE_STATUS,
    SOURCE_STATUSES,
    SOURCES,
    TARGET_STATUSES,
    Relationship,
    blackout_windows,
    blocking,
    describe_source,
    describe_target,
    key_of_source,
    lower,
    match_source,
    only_one,
    overlaps,
    parse_release,
    release_lifts,
    releases_to_print,
    reporting_order,
    same_address,
    snapshot_owners,
    source_key,
    unmet,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import SNAPSHOT_STATUSES

# What a PATCH changes; the key is fixed at create.
MANAGED = ("target_address", "target_port", *SETTINGS)
# What Qumulo Core 7.10.3A stores for a setting a create leaves out.
CREATE_DEFAULTS = {
    "replication_enabled": True,
    "map_local_ids_to_nfs_ids": False,
    "source_root_read_only": False,
    "blackout_windows": [],
    "blackout_window_timezone": "",
}
# The last job's error on a record whose target was re-formed with a new certificate (research R7).
SECURE_CONNECTION_ERROR = "Cannot establish a secure connection"
NOT_FOUND = "relationship_manager_relationship_not_found_error"


@dataclass
class Found:
    """This cluster's source records sorted against the declared relationship.

    current: the live or pending record of the key.
    ended: the key's ENDED records, leftovers beside current, or each governed by row O3 when there is no current.
    """

    current: dict | None = None
    ended: list = field(default_factory=list)
    changed_peers: list = field(default_factory=list)
    misdirected: list = field(default_factory=list)


@dataclass
class SourceDirectory:
    """The source directory as the target's authorize needs it: whether it is empty, from entries, and its aggregates."""

    exists: bool = False
    empty: bool = True
    files: int = 0
    size: int = 0


def _record_text(record):
    return f"{record['id']} ({record['state']}, to {record['target_cluster_uuid']} at {record['target_address']}:{record['target_port']})"


class ReplicationRelationship(Relationship):
    """replication_relationship: a relationship found on its source by (source path, target cluster UUID, target path)."""

    READ_ONLY = frozenset(SHAPE) - frozenset(MANAGED)
    UNORDERED = frozenset({"blackout_windows"})

    def __init__(self, module, client):
        # The polling base gives this class _after_writes; it never waits, so it has no wait_timeout and its deadline is now.
        module.params.setdefault("wait_timeout", 0)
        super().__init__(module, client)
        params = self.params
        self.key = source_key(params["source_path"], params["target_cluster_uuid"], params["target_path"])
        self.previous = [lower(uuid) for uuid in params["previous_target_cluster_uuids"]]
        if self.key.target_cluster_uuid in self.previous:
            raise QumuloError(
                f"previous_target_cluster_uuids holds the declared target UUID {self.key.target_cluster_uuid}; a cluster's previous UUIDs never hold its own"
            )
        self.releases = [self._release(entry) for entry in params["release"]]
        self.address = params["target_address"]
        self.port = params["target_port"]
        self.windows = None if params["blackout_windows"] is None else blackout_windows(params["blackout_windows"])
        timezone = params["blackout_window_timezone"]
        self.timezone = None if timezone is None else timezone.strip()
        if params["state"] == "absent" and params["relationship_id"] is not None:
            raise QumuloError("relationship_id is for the real run of state=present after its check run; state=absent finds the records by key")
        if params["state"] == "present":
            self._check_address()
            if self.windows and not self.timezone:
                raise QumuloError(
                    "blackout_windows need blackout_window_timezone, the timezone they are read in; declare it, such as UTC, since a "
                    "timezone cannot be cleared once set (G-REPL-TZ)"
                )
        self.check = module.check_mode
        self.record_id = None  # the record fetch() reads, once the task knows it
        self.listed_ids = set()
        self.snapshots = []

    def describe(self):
        return f"the replication relationship from {self.key.source_path} to {self.key.target_cluster_uuid}:{self.key.target_path}"

    def _release(self, entry):
        release = parse_release(entry)
        if release["target_path"] != self.key.target_path:
            raise QumuloError(
                f"release entry {entry!r} names target path {release['target_path']}, not {self.key.target_path}; pass only this relationship's entries"
            )
        return release

    def _check_address(self):
        try:
            ipaddress.ip_address(self.address)
        except ValueError:
            raise QumuloError(f"target_address {self.address!r} is not an IP address; the server takes an IP literal, never a host name") from None

    def _check_timezone(self, current):
        stored = current["blackout_window_timezone"] if current else None
        if self.timezone == "" and stored:
            raise QumuloError(
                f"{self.describe()} has blackout_window_timezone {stored}, and a PATCH of an empty timezone leaves it in place (QUIRK-019); "
                "declare the timezone it has, or another one (G-REPL-TZ)"
            )

    # Resource hooks

    def fetch(self):
        found = self.client.get_or_none(SOURCE_STATUS, params={"id": self.record_id})
        return None if found is None else Current(self.record_id, describe_source(found.data))

    def desired(self, before):
        wanted = {
            "target_address": self.address,
            "target_port": self.port,
            **{key: self.params[key] for key in ("replication_enabled", "map_local_ids_to_nfs_ids", "source_root_read_only")},
            "blackout_windows": self.windows,
            "blackout_window_timezone": self.timezone,
        }
        return {**before, **{key: value for key, value in wanted.items() if value is not None}}

    def same(self, key, a, b):
        if key == "target_address" and a is not None and b is not None:
            return same_address(a, b)
        return super().same(key, a, b)

    def update(self, current, after, keys):
        def build(configuration):
            stored = {
                **{key: configuration.get(key) for key in MANAGED},
                "blackout_windows": blackout_windows(configuration.get("blackout_windows")),
            }
            # Only the keys this task changes: after a 412, another writer's change to any other key stands.
            changed = [key for key in keys if not self.same(key, stored[key], after[key])]
            return {key: after[key] for key in changed} or None

        # After a 412, the re-read may show another writer already set every key; then nothing is written.
        self.wrote = self.client.read_modify_write(SOURCE, build, method="PATCH", params={"id": current.id}) is not None

    def settle(self, after):
        current = self.fetch()
        if current is None:
            raise QumuloError(f"{self.describe()} (id {self.record_id}) is missing after the write")
        return self._converged(current.data, after)

    def _converged(self, observed, after):
        # Only what the task declares must read back; another writer may change the rest at any time.
        declared = self.desired({})
        drift = [key for key in MANAGED if key in declared and not self.same(key, observed.get(key), after[key])]
        if drift:
            raise QumuloError(
                f"{self.describe()} did not converge on {', '.join(drift)}",
                expected={key: after[key] for key in drift},
                observed={key: observed.get(key) for key in drift},
            )
        return observed

    # present

    def run(self):
        if self.params["state"] == "absent":
            return self._absent()
        found = self._find()
        if not self.params["create"]:
            return self._delete_only(found)
        current = found.current
        self._check_timezone(current)
        creates = current is None
        entries = [self._misdirected(record) for record in found.misdirected]
        directory = None
        if creates or current["state"] == PENDING:
            # Only then can the target still authorize, and only without a live record of the key can a reversal stand in the way.
            entries.extend(self._o8())
            directory = self._source_directory()
            if not directory.exists:
                entries.append(self._source_missing())
        if creates:
            entries.extend(self._o3(record) for record in found.ended)
            entries.extend(self._changed_peer(record) for record in found.changed_peers)
        entries = reporting_order(entries)
        stopping = blocking(entries)
        if stopping:
            raise QumuloError(
                f"{self.describe()} cannot be {'created' if creates else 'changed'}: " + "; ".join(entry["message"] for entry in stopping),
                changed=False,
                **self._facts(entries, directory),
            )
        if creates:
            return self._create_first(found, entries, directory)
        return self._update_current(found, entries, directory)

    def _find(self):
        if self.params["relationship_id"] is not None:
            return self._find_by_id(str(self.params["relationship_id"]))
        records = [describe_source(record) for record in self.client.get(SOURCE_STATUSES).data or []]
        self.listed_ids = {str(record["id"]) for record in records}
        matched = match_source(
            records,
            self.key,
            address=self.address,
            port=self.port,
            previous_uuids=self.previous,
            inventory_uuids=self.params["inventory_cluster_uuids"],
        )
        if all(record["state"] == ENDED for record in matched.matches):
            # Every record of the key is ENDED, so row O3 governs each.
            return Found(None, matched.matches, matched.changed_peers, matched.misdirected)
        # ENDED records beside a live or pending one, such as a prune on the target leaves (research R5), are in
        # matched.ended; only two live or pending records are ambiguous.
        current = only_one(matched.matches, f"{self.describe()} in a live or pending state")
        return Found(current, matched.ended, matched.changed_peers, matched.misdirected)

    def _find_by_id(self, record_id):
        found = self.client.get_or_none(SOURCE_STATUS, params={"id": record_id})
        if found is None:
            raise QumuloError(
                f"source record {record_id}, which relationship_id names, is gone; run the check again, since this module never creates in its place"
            )
        record = describe_source(found.data)
        if key_of_source(record) != self.key:
            raise QumuloError(
                f"source record {record_id} is from {record['source_path']} to {record['target_cluster_uuid']}:{record['target_path']}, not {self.describe()}"
            )
        if record["state"] == ENDED:
            raise QumuloError(f"source record {record_id} is ENDED now ({record['end_reason']}); run the check again")
        self.listed_ids = {record_id}
        return Found(current=record)

    def _update_current(self, found, entries, directory):
        current = found.current
        # Beside a live or pending record of the key, an ENDED one holds nothing, and a changed peer is the trace of a
        # replacement that stopped after its create; both are deleted with no create.
        leftovers = [*found.ended, *found.changed_peers]
        after = self.desired(current)
        keys = self.changes(current, after, creating=False)
        if current["state"] in FAILOVER:
            self.client.warnings.append(
                f"{self.describe()} (id {current['id']}) is {current['state']}, a failover state; this module changes nothing in its state, only its settings"
            )
        read_by_id = None if leftovers else current["id"]
        if self.check:
            for record in leftovers:
                self.notes.append(f"would delete source record {_record_text(record)}, which a record of this key replaces")
            return self._result(bool(keys or leftovers), current, after, leftovers, entries, directory, read_by_id=read_by_id)
        self.record_id = str(current["id"])
        with self._after_writes():
            if keys:
                after = self._write_and_settle(Current(self.record_id, current), after, keys, creating=False)
                if self.wrote:
                    self.done.append(f"changed {', '.join(keys)}")
                else:
                    keys = []
            deleted = self._delete_records(leftovers)
        for record in deleted:
            self.notes.append(f"deleted source record {_record_text(record)}, which a record of this key replaces")
        return self._result(bool(keys or deleted), current, after, deleted, entries, directory, read_by_id=read_by_id)

    def _create_first(self, found, entries, directory):
        replaced = [*found.ended, *found.changed_peers]
        if self.check:
            after = self._predicted()
            for record in replaced:
                self.notes.append(f"would delete source record {_record_text(record)} once the new relationship reaches {self.key.target_cluster_uuid}")
            return self._result(True, {}, after, replaced, entries, directory, creates=True)
        created = self._create()
        with self._after_writes():
            deleted = self._delete_records(replaced)
        if deleted:
            self.notes.append(
                f"created relationship {created['id']}, read back reaching {self.key.target_cluster_uuid}, then deleted "
                + ", ".join(_record_text(record) for record in deleted)
            )
        return self._result(True, {}, created, deleted, entries, directory, creates=True)

    def _predicted(self):
        blank = {
            **dict.fromkeys(SHAPE),
            **self.key._asdict(),
            "source_cluster_uuid": lower(node_state(self.client)[0]),
            "state": PENDING,
            "target_root_read_only": False,
            **CREATE_DEFAULTS,
        }
        return self.desired(blank)

    def _create(self):
        wanted = self.desired({})
        declared = {key: wanted[key] for key in SETTINGS if key in wanted}
        body = {
            "source_root_path": self.key.source_path,
            "target_root_path": self.key.target_path,
            "target_address": self.address,
            "target_port": self.port,
            **declared,
        }
        try:
            created_id = str(self.client.post(SOURCES, body=body).data["id"])
        except QumuloMaybeApplied as lost:
            created_id = self._created_after(lost)
            self.notes.append("the create was reconciled after a lost connection")
        self.done.append(f"created {self.describe()} (id {created_id})")
        self.record_id = created_id
        current = self.fetch()
        if current is None:
            raise QumuloError(f"{self.describe()} (id {created_id}) is missing after the create", changed=True)
        record = current.data
        if record["target_cluster_uuid"] != self.key.target_cluster_uuid:
            self._withdraw(record)
        with self._after_writes():
            return self._converged(record, self.desired(record))

    def _created_after(self, lost):
        """The id of the record a create whose answer was lost made, found by listing; a create is never sent again."""
        records = [describe_source(record) for record in self.client.get(SOURCE_STATUSES).data or []]
        made = [
            record
            for record in records
            if str(record["id"]) not in self.listed_ids
            and (record["source_path"], record["target_path"]) == (self.key.source_path, self.key.target_path)
            and same_address(record["target_address"], self.address)
            and record["target_port"] == self.port
        ]
        if len(made) == 1:
            return str(made[0]["id"])
        if not made:
            raise QumuloError(
                f"{lost}; listing shows no new record of {self.describe()}, so the create did not take effect here. The target may hold a "
                "pending record of it, which replication_target prunes; run again (G-REPL-ONESHOT)",
                changed=True,
                maybe_applied=True,
            ) from None
        ids = ", ".join(str(record["id"]) for record in made)
        raise QumuloError(
            f"{lost}; listing shows {len(made)} new records with these paths (ids {ids}); delete all but one", changed=True, maybe_applied=True
        ) from None

    def _withdraw(self, record):
        """Delete a create that reached a cluster other than the declared target, and fail (spec FR-005)."""
        with self._after_writes():
            self._delete_record(record)
        raise QumuloError(
            f"target_address {self.address}:{self.port} reaches cluster {record['target_cluster_uuid']}, not target_cluster_uuid "
            f"{self.key.target_cluster_uuid}, so the relationship this task created there (id {record['id']}) is deleted. The cluster at that "
            "address keeps an ENDED record of it, which this module cannot reach; delete it there (G-REPL-ONESHOT). Check that the address is "
            "the target's, or that the NAT in front of it forwards to the target",
            changed=True,
        )

    def _delete_only(self, found):
        """create=false: delete the previous-UUID records that cannot replicate to an old cluster kept beside the new one."""
        entries = [self._misdirected(record) for record in found.misdirected]
        if entries:
            raise QumuloError(f"{self.describe()}: " + "; ".join(entry["message"] for entry in entries), changed=False, **self._facts(entries, None))
        doomed, kept = [], []
        for record in found.changed_peers:
            at_declared = same_address(record["target_address"], self.address) and record["target_port"] == self.port
            unreachable = SECURE_CONNECTION_ERROR in (record["error_from_last_job"] or "")
            (doomed if record["target_cluster_uuid"] in self.previous and (at_declared or unreachable) else kept).append(record)
        for record in kept:
            self.notes.append(f"changed peer {_record_text(record)} is left for a create-first replacement")
        current = found.current or {}
        if self.check:
            deleted = doomed
        else:
            with self._after_writes():
                deleted = self._delete_records(doomed)
        return self._result(bool(deleted), current, current, deleted, [], None)

    # The unmet entries; each message names what would lift it.

    def _misdirected(self, record):
        return unmet(
            "misdirected",
            f"source record {record['id']} with these paths at the declared address {self.address}:{self.port} reaches {record['target_cluster_uuid']}, "
            f"another inventory cluster, not {self.key.target_cluster_uuid}; so the address reaches the wrong cluster, and only a corrected "
            "replication address lifts it",
            facts={key: record[key] for key in ("id", "target_cluster_uuid", "target_address", "target_port", "state")},
        )

    def _changed_peer(self, record):
        previous = record["target_cluster_uuid"] in self.previous
        return unmet(
            "changed_peer",
            f"source record {_record_text(record)} has these paths and a changed peer"
            f"{', a previous UUID of the target' if previous else ''}; recreate_changed_peer=true creates the new relationship first, checks "
            f"that it reaches {self.key.target_cluster_uuid}, and only then deletes {record['id']}",
            facts={**{key: record[key] for key in ("id", "target_cluster_uuid", "target_address", "target_port", "state")}, "previous": previous},
            lifted=self.params["recreate_changed_peer"],
        )

    def _o3(self, record):
        record_id = str(record["id"])
        return unmet(
            "O3",
            f"source record {record_id} of this relationship is ENDED ({record['end_reason']}), as when an operator ended it on purpose; "
            f"recreate=true with a release bound to {record_id} naming O3 replaces it, whatever the target directory holds, and deletes it "
            f"once the new relationship reaches {self.key.target_cluster_uuid}",
            binds_to=record_id,
            facts={key: record[key] for key in ("id", "end_reason", "target_address", "target_port")},
            lifted=self.params["recreate"] and release_lifts(self.releases, "O3", record_id),
        )

    def _o8(self):
        found = []
        for record in (describe_target(record) for record in self.client.get(TARGET_STATUSES).data or []):
            if (
                record["source_cluster_uuid"] == self.key.target_cluster_uuid
                and record["state"] != ENDED
                and overlaps(record["target_path"], self.key.source_path)
            ):
                found.append(
                    unmet(
                        "O8",
                        f"this cluster is the target of relationship {record['id']} from the declared target's {record['source_path']} into "
                        f"{record['target_path']} ({record['state']}), equal to or nested with {self.key.source_path}: the relationship is reversed, "
                        "a failover state that the replication_failover role's failback turns back, and nothing here lifts it",
                        facts={key: record[key] for key in ("id", "source_cluster_uuid", "source_path", "target_path", "state")},
                    )
                )
        return found

    def _source_missing(self):
        path = self.key.source_path
        pending = self.check and self.params["source_path_pending"]
        if pending:
            message = f"{path} does not exist yet; the directory step creates it before the create, and it counts as empty"
        else:
            message = f"{path} does not exist on this cluster; create it first, for example with qumulo.cluster.directory"
        return unmet("source_missing", message, facts={"source_path": path}, pending=pending)

    def _source_directory(self):
        path = self.key.source_path
        found = attributes(self.client, path)
        if found is None:
            return SourceDirectory()
        require_directory(path, found.data)
        empty = not holds_objects(self.client, path)
        aggregates = self.client.get(AGGREGATES, params={"ref": path}).data
        files = sum(int(aggregates[key]) for key in ("total_files", "total_symlinks", "total_other_objects"))
        return SourceDirectory(exists=True, empty=empty, files=files, size=int(aggregates["total_capacity"]))

    # deletes

    def _delete_records(self, records):
        """Delete records, and return those this task deleted; one already gone is no change."""
        if not records:
            return []
        ids = {str(record["id"]) for record in records}
        owned = {snapshot["id"] for snapshot in self.client.paginate(SNAPSHOT_STATUSES, "entries") if snapshot_owners(snapshot) & ids}
        deleted = [record for record in records if self._delete_record(record)]
        if owned:
            # The source's delete takes its replication snapshot, which lists as in_delete for a moment after.
            self.snapshots = [
                {"id": snapshot["id"], "name": snapshot["name"]}
                for snapshot in self.client.paginate(SNAPSHOT_STATUSES, "entries")
                if snapshot["id"] in owned and not snapshot.get("in_delete")
            ]
        if self.snapshots:
            self.notes.append(
                "snapshots the deleted records owned are left, never expiring: "
                + ", ".join(f"{snapshot['name']} (id {snapshot['id']})" for snapshot in self.snapshots)
                + "; delete them once they are no longer wanted (G-REPL-SNAPSHOT)"
            )
        return deleted

    def _delete_record(self, record):
        """Whether this request deleted the record; False when it was already gone."""
        record_id = str(record["id"])
        found = self.client.get_or_none(SOURCE, params={"id": record_id})
        if found is None:
            self.notes.append(f"source record {record_id} was already gone")
            return False
        try:
            self.client.delete(SOURCE, params={"id": record_id}, if_match=found.etag)
        except QumuloMaybeApplied as lost:
            if self.client.get_or_none(SOURCE_STATUS, params={"id": record_id}) is not None:
                raise QumuloError(f"{lost}; source record {record_id} still exists", changed=True, maybe_applied=True) from None
            self.notes.append(f"the delete of source record {record_id} was reconciled after a lost connection")
        except QumuloApiError as e:
            if not (e.status == 404 and e.is_(NOT_FOUND)):
                raise
            self.notes.append(f"source record {record_id} was already gone")
            return False
        self.done.append(f"deleted source record {_record_text(record)}")
        return True

    # absent

    def _absent(self):
        records = [describe_source(record) for record in self.client.get(SOURCE_STATUSES).data or []]
        uuids = {self.key.target_cluster_uuid, *self.previous}
        doomed = [
            record
            for record in records
            if (record["source_path"], record["target_path"]) == (self.key.source_path, self.key.target_path) and record["target_cluster_uuid"] in uuids
        ]
        before = next((record for record in doomed if key_of_source(record) == self.key and record["state"] != ENDED), {})
        if self.check:
            deleted = doomed
        else:
            with self._after_writes():
                deleted = self._delete_records(doomed)
        if deleted:
            self.notes.append(
                f"the target keeps its record of each deleted relationship, and {self.key.target_path} its data, until replication_target with "
                "state=absent deletes it there; the source tells the target of a delete only once (G-REPL-NOTIFY)"
            )
        return self._result(bool(deleted), before, {}, deleted, [], None)

    # results

    def _facts(self, entries, directory):
        return {
            "unmet": entries,
            "releases": [{"target_path": self.key.target_path, **release} for release in releases_to_print(entries)],
            "source_empty": None if directory is None else directory.empty,
            "source_files": None if directory is None else directory.files,
            "source_bytes": None if directory is None else directory.size,
        }

    def _result(self, changed, before, after, deletes, entries, directory, *, creates=False, read_by_id=None):
        """The result; its diff shows the records the task deletes beside the relationship it creates or changes."""
        result = {
            "changed": changed,
            "relationship": after,
            "creates": creates,
            "read_by_id": None if read_by_id is None else str(read_by_id),
            **self._facts(entries, directory),
            "deleted": list(deletes),
            "snapshots": self.snapshots,
        }
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"relationship": before, "records": list(deletes)}, "after": {"relationship": after, "records": []}}
        return result
