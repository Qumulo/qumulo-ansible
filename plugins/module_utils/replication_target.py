# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The target side of a replication relationship: authorize it once the overwrite table allows, or delete its records.

The source's create registers the relationship here, awaiting authorization. The module reads that record by the id
the source returned, and returns at once when it is ESTABLISHED or in a failover state. Only before an authorize or a
prune does it list this cluster's records and look at the target directory, to evaluate the rows of the overwrite table
that fall to the target (specs/011-replication, "Overwrite and release rules"): O7, O9, O2, O6, O5, O4 and O1.

Given replication_address, the module first checks that the tenant serving it has replication on (specs/014-multi-tenancy,
FR-029), from this cluster's tenant list and network document. A check run takes a tenant's switch from the target host's
own tenant writes when the role passes them, as the real run reads it after those writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ansible_collections.qumulo.cluster.plugins.module_utils import network, tenancy
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import CLUSTER_INFO_SUBSETS, node_state, product
from ansible_collections.qumulo.cluster.plugins.module_utils.files import (
    AGGREGATES,
    DIRECTORY,
    ENTRIES,
    attributes,
    canonical_path,
    objects_under,
    require_directory,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import PORTAL_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import (
    ENDED,
    ESTABLISHED,
    FAILOVER,
    LIVE,
    PENDING,
    SHAPE,
    SOURCE_STATUSES,
    TARGET_AUTHORIZE,
    TARGET_DELETE,
    TARGET_STATUS,
    TARGET_STATUSES,
    Relationship,
    blocking,
    describe_source,
    describe_target,
    key_of_target,
    lower,
    overlaps,
    parse_release,
    release_lifts,
    releases_to_print,
    reporting_order,
    snapshot_owners,
    state_kind,
    target_key,
    unmet,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import SNAPSHOT_STATUSES

# The names the server gives a target's recovery-point snapshot and a make-writable's revert snapshot. Such a snapshot
# loses its owner when its relationship is deleted, and can read back with an empty path (G-REPL-SNAPSHOT).
REPLICATION_SNAPSHOT = re.compile(r"^\d+_(replication_from|revert_to)_")
NOT_FOUND = "relationship_manager_relationship_not_found_error"


@dataclass
class TargetDirectory:
    """The target directory as the overwrite table reads it.

    has_entries counts any entry, a subdirectory included, as the server does for an authorize (row O1). holds_objects
    counts only files, links and other objects at any depth, as emptiness does everywhere else (rows O4 and O5), and
    first_object is the path of the first one the walk met.
    """

    file_id: str | None = None  # None while the directory does not exist
    has_entries: bool = False
    first_object: str | None = None
    replaces: int = 0  # files, links and other objects, from the aggregates
    size: int = 0  # total_capacity, from the aggregates

    @property
    def holds_objects(self):
        return self.first_object is not None


@dataclass
class Evaluation:
    """What the overwrite table found before an authorize: the unmet entries, and the deletes the real pass sends first."""

    entries: list = field(default_factory=list)
    holders: list = field(default_factory=list)
    releases: list = field(default_factory=list)  # records a release lifts, deleted before the authorize
    prunes: list = field(default_factory=list)  # stale records of the key that hold nothing
    directory: TargetDirectory = field(default_factory=TargetDirectory)


def _nested(path, target_path):
    """Whether a path a record reports is equal to or nested with target_path.

    A path read back without its leading slash, which server_path keeps as read, is neither.
    """
    return isinstance(path, str) and path.startswith("/") and overlaps(path, target_path)


def _holder(record):
    keys = ("id", "source_cluster_uuid", "source_path", "state", "recovery_point", "error_from_last_job", "job_state", "target_root_read_only")
    return {key: record[key] for key in keys}


def _snapshot_path(snapshot):
    path = snapshot.get("source_file_path")
    return canonical_path(path, "source_file_path") if path else None


class ReplicationTarget(Relationship):
    """replication_target: a relationship found on its target by (source cluster UUID, source path, target path)."""

    def __init__(self, module, client):
        super().__init__(module, client)
        params = self.params
        self.key = target_key(params["source_cluster_uuid"], params["source_path"], params["target_path"])
        self.previous = {lower(uuid) for uuid in params["previous_source_cluster_uuids"]}
        if self.key.source_cluster_uuid in self.previous:
            raise QumuloError(
                f"previous_source_cluster_uuids holds the declared source UUID {self.key.source_cluster_uuid}; a cluster's previous UUIDs never hold its own"
            )
        self.releases = [self._release(entry) for entry in params["release"]]
        self.source_id = None if params["relationship_id"] is None else str(params["relationship_id"])
        if params["state"] == "present" and (self.source_id is None) == (not params["source_creates"]):
            raise QumuloError("give relationship_id, the id the source's create returned, or source_creates=true for a check pass before the create; not both")
        self.check = module.check_mode
        self.snapshots = []

    def describe(self):
        return f"the replication relationship {self._key_text()}"

    def _key_text(self):
        return f"from {self.key.source_cluster_uuid}:{self.key.source_path} to {self.key.target_path}"

    def _release(self, entry):
        release = parse_release(entry)
        if release["target_path"] != self.key.target_path:
            raise QumuloError(
                f"release entry {entry!r} names target path {release['target_path']}, not {self.key.target_path}; pass only this relationship's entries"
            )
        return release

    def _warn(self, message):
        self.client.warnings.append(message)

    # present

    def run(self):
        if self.params["state"] == "absent":
            return self._absent()
        self._check_replication_address()
        own = None
        if self.source_id is not None:
            own = self._read_own()
            kind = state_kind(own["state"])
            if kind == "established":
                return self._result(False, own, own)
            if kind == "failover":
                self._warn_failover(own)
                return self._result(False, own, own)
        elif not self.check:
            raise QumuloError(
                f"{self.describe()} does not exist yet: source_creates is for the check pass before the source's create; "
                "authorize with relationship_id, the id the create returned"
            )
        evaluation = self._evaluate(own)
        self._refuse(evaluation)
        if self.check:
            return self._predicted(own, evaluation)
        with self._after_writes():
            deleted = self._delete_records([*evaluation.releases, *evaluation.prunes])
            self._authorize(own["id"], evaluation.directory)
        after = self._settle(own["id"])
        for record in deleted:
            if record in evaluation.prunes:
                self.notes.append(f"pruned stale target record {record['id']} ({record['state']}), which held nothing")
        return self._result(True, own, after, evaluation, deleted)

    def _check_replication_address(self):
        """Spec 014 FR-029: refuse a relationship whose replication address is served by a tenant with replication off.

        It runs before the record is read, so an ESTABLISHED relationship that cannot replicate is refused on every run.
        """
        given = self.params["replication_address"]
        if given is None:
            return
        refusal, warning = replication_address_verdict(
            given,
            self.key.target_path,
            lambda: tenancy.tenant_entries(self.client),
            lambda: network.canonical_document(self.client.get(network.NETWORK).data),
            self._replication_enabled,
        )
        if warning:
            self._warn(warning)
        if refusal:
            raise QumuloError(refusal, changed=False)

    def _replication_enabled(self, tenant):
        """The tenant's replication switch at this check, which a check run takes from the target host's own planned writes.

        In the real run those writes come earlier in the play, so the tenant list already holds them.
        """
        if self.check:
            for write in self.params["planned_tenant_writes"]:
                current_name = write.get("previous_name") or write.get("name")
                if current_name == tenant["name"] and write.get("replication_enabled") is not None:
                    return write["replication_enabled"]
        return tenant["replication_enabled"]

    def _read_own(self):
        found = self.client.get_or_none(TARGET_STATUS, params={"id": self.source_id})
        if found is None:
            raise QumuloError(
                f"this cluster holds no target record with id {self.source_id}, the source's relationship; "
                "the source's create registers it here, so check that the source reaches this cluster at its address"
            )
        own = describe_target(found.data)
        if key_of_target(own) != self.key:
            raise QumuloError(
                f"target record {self.source_id} is from {own['source_cluster_uuid']}:{own['source_path']} to {own['target_path']}, not {self._key_text()}"
            )
        return own

    def _warn_failover(self, own):
        self._warn(f"{self.describe()} (id {own['id']}) is {own['state']}, a failover state; this module changes nothing in it and does not wait for it")

    def _evaluate(self, own):
        """Every row of the overwrite table that falls to the target, in the reporting order, before any write."""
        found = Evaluation(directory=self._directory())
        directory = found.directory
        own_id = None if own is None else str(own["id"])
        targets = [describe_target(record) for record in self.client.get(TARGET_STATUSES).data or []]
        sources = [describe_source(record) for record in self.client.get(SOURCE_STATUSES).data or []]
        ended = []
        if own is not None and own["state"] == ENDED:
            found.entries.append(
                unmet(
                    "ended_on_target",
                    f"target record {own_id} is ENDED ({own['end_reason']}), though the source holds this id: the target directory was deleted "
                    "or renamed; delete the relationship on both sides, then declare it again",
                    facts={"id": own_id, "end_reason": own["end_reason"]},
                )
            )
        if directory.file_id is None:
            pending = self.check and self.params["directory_pending"]
            found.entries.append(self._missing(pending))
        for record in targets:
            if str(record["id"]) == own_id:
                continue
            state = record["state"]
            if key_of_target(record) == self.key:
                if state == PENDING or (state == ENDED and not directory.holds_objects):
                    found.prunes.append(record)
                elif state == ENDED:
                    ended.append(record)
                else:
                    found.entries.append(self._o6(record))
            elif record["target_path"] == self.key.target_path:
                if state in LIVE:
                    found.holders.append(_holder(record))
                elif state == ENDED and directory.holds_objects:
                    ended.append(record)
            elif state in LIVE and _nested(record["target_path"], self.key.target_path):
                found.entries.append(self._o7(record))
        for record in sources:
            if (
                record["target_cluster_uuid"] == self.key.source_cluster_uuid
                and record["state"] != ENDED
                and _nested(record["source_path"], self.key.target_path)
            ):
                found.entries.append(self._o9(record))
        for holder in found.holders:
            found.entries.append(self._o2(holder))
        for record in ended:
            found.entries.append(self._o5(record))
        if self.params["source_empty"] is not False and directory.holds_objects:
            binds_to = (found.holders or ended or [{"id": directory.file_id}])[0]["id"]
            found.entries.append(self._o4(binds_to))
        if directory.has_entries:
            found.entries.append(self._o1(directory))
        found.entries.extend(self._capacity(directory))
        found.entries = reporting_order(found.entries)
        lifted = {entry["binds_to"] for entry in found.entries if entry["row"] in ("O2", "O5") and entry["lifted"]}
        found.releases = [record for record in targets if str(record["id"]) in lifted]
        self._warn_partial(directory)
        return found

    def _directory(self):
        path = self.key.target_path
        found = attributes(self.client, path)
        if found is None:
            return TargetDirectory()
        require_directory(path, found.data)
        directory = TargetDirectory(file_id=str(found.data["id"]))
        for child in self.client.iterate(ENTRIES, "files", params={"ref": path}):
            directory.has_entries = True
            first = child if child["type"] != DIRECTORY else next(objects_under(self.client, canonical_path(child["path"])), None)
            if first is not None:
                directory.first_object = canonical_path(first["path"])
                break
        if directory.has_entries:
            aggregates = self.client.get(AGGREGATES, params={"ref": path}).data
            directory.replaces = sum(int(aggregates[key]) for key in ("total_files", "total_symlinks", "total_other_objects"))
            directory.size = int(aggregates["total_capacity"])
        return directory

    # The unmet entries, one function per row; each message names what would lift it.

    def _missing(self, pending):
        path = self.key.target_path
        if pending:
            message = f"{path} does not exist yet; the directory step creates it before the authorize, and it counts as empty"
        else:
            message = f"{path} does not exist; create it first with qumulo.cluster.directory, path only, since replication sets its attributes"
        return unmet("target_missing", message, facts={"target_path": path}, pending=pending)

    def _o1(self, directory):
        path = self.key.target_path
        facts = {"target_path": path, "holds_objects": directory.holds_objects, "first_object": directory.first_object}
        if self.params["allow_directories_only"]:
            if directory.holds_objects:
                message = (
                    f"{path} holds {directory.first_object}, and allow_directories_only lets the source's content replace only "
                    "subdirectories; the authorize would replace it"
                )
            else:
                message = f"{path} holds only directories, which the source's content replaces under allow_directories_only"
            return unmet("O1", message, facts=facts, lifted=not directory.holds_objects)
        return unmet(
            "O1",
            f"{path} holds entries, subdirectories included, and the source's content replaces them: the first job deletes whatever the "
            "source lacks; set allow_non_empty_directory (allow_non_empty_target on the relationship's entry) to allow it",
            facts=facts,
            lifted=self.params["allow_non_empty_directory"],
        )

    def _o2(self, holder):
        holder_id = str(holder["id"])
        previous = holder["source_cluster_uuid"] in self.previous
        return unmet(
            "O2",
            f"relationship {holder_id} from {holder['source_cluster_uuid']}:{holder['source_path']} holds {self.key.target_path} ({holder['state']})"
            f"{', from a previous UUID of the source' if previous else ''}; a release bound to {holder_id} naming O2 deletes it, and the directory "
            "keeps its data",
            binds_to=holder_id,
            facts={**holder, "from_previous_source": previous},
            lifted=release_lifts(self.releases, "O2", holder_id),
        )

    def _o4(self, binds_to):
        given = self.params["source_empty"] is not None
        source = "the source directory holds no files" if given else "source_empty was not given, so the source counts as empty"
        return unmet(
            "O4",
            f"{source}, and {self.key.target_path} holds files that the authorize would replace with nothing; a release bound to {binds_to} "
            "naming O4 allows it",
            binds_to=binds_to,
            facts={"source_empty": self.params["source_empty"], "target_path": self.key.target_path},
            lifted=release_lifts(self.releases, "O4", binds_to),
        )

    def _o5(self, record):
        record_id = str(record["id"])
        of_the_key = key_of_target(record) == self.key
        return unmet(
            "O5",
            f"ENDED relationship {record_id} from {record['source_cluster_uuid']}:{record['source_path']} ({record['end_reason']}) lies over "
            f"{self.key.target_path}, which may hold writes the source lacks; a release bound to {record_id} naming O5 deletes it",
            binds_to=record_id,
            facts={
                "id": record_id,
                "source_cluster_uuid": record["source_cluster_uuid"],
                "source_path": record["source_path"],
                "end_reason": record["end_reason"],
                "of_the_key": of_the_key,
            },
            lifted=release_lifts(self.releases, "O5", record_id),
        )

    def _o6(self, record):
        return unmet(
            "O6",
            f"relationship {record['id']} of this key is {record['state']} here, but the source does not hold it, as after a lost delete "
            "notification; only state=absent removes it, and declaring the relationship again then replaces the directory's content",
            facts={"id": str(record["id"]), "state": record["state"]},
        )

    def _o7(self, record):
        return unmet(
            "O7",
            f"relationship {record['id']} from {record['source_cluster_uuid']}:{record['source_path']} holds {record['target_path']} "
            f"({record['state']}), which contains or lies inside {self.key.target_path}; remove that relationship first",
            facts={key: record[key] for key in ("id", "source_cluster_uuid", "source_path", "target_path", "state")},
        )

    def _o9(self, record):
        return unmet(
            "O9",
            f"this cluster replicates {record['source_path']} to the declared source as relationship {record['id']} ({record['state']}): "
            "the relationship is reversed, a failover state that the replication_failover role's failback turns back, and nothing here lifts it",
            facts={key: record[key] for key in ("id", "target_cluster_uuid", "source_path", "target_path", "state")},
        )

    def _capacity(self, directory):
        """The capacity rule (spec FR-007): a shortfall warns, and is unmet only with strict_capacity."""
        wanted = self.params["source_bytes"]
        if wanted is None:
            return []
        filesystem = CLUSTER_INFO_SUBSETS["filesystem"](self.client)["filesystem"]
        free = filesystem.get("free_size_bytes") or 0
        cache = filesystem.get("portal_cache_size_bytes") or 0
        evictable = 0
        if cache:
            threshold = PORTAL_INFO_SUBSETS["eviction"](self.client)["eviction_settings"]["free_threshold"]
            evictable = max(0, cache - int(threshold * (filesystem.get("total_size_bytes") or 0)))
        reusable = free + directory.size + evictable
        if wanted <= reusable:
            return []
        parts = f"free {free}, the target directory's own {directory.size}" + (f", the portal cache less its eviction reserve {evictable}" if cache else "")
        if product(self.client)["unprotected_edge"]:
            remedy = (
                "raise qumulo_capacity.clamp with playbooks/edge_cache_resize.yml, which grows the cache in place when the license and the "
                "disk allow; otherwise rebuild the edge onto a larger thin disk"
            )
        else:
            remedy = "free space on this cluster, or replicate a smaller directory"
        message = f"the source directory holds {wanted} bytes, more than the {reusable} bytes this cluster can reuse ({parts}); {remedy}"
        if not self.params["strict_capacity"]:
            self._warn(f"{self.describe()}: {message}. The comparison is approximate; strict_capacity=true refuses it")
            return []
        facts = {"source_bytes": wanted, "reusable_bytes": reusable, "free_bytes": free, "target_bytes": directory.size, "evictable_bytes": evictable}
        return [unmet("capacity", message, facts=facts)]

    def _warn_partial(self, directory):
        files = self.params["source_files"]
        if self.params["source_empty"] is False and files is not None and files < directory.replaces:
            self._warn(
                f"the source directory holds {files} files, links and other objects, fewer than the {directory.replaces} in "
                f"{self.key.target_path} that the authorize replaces; the source may be only partly restored"
            )

    def _refuse(self, evaluation):
        stopping = blocking(evaluation.entries)
        if stopping:
            raise QumuloError(
                f"{self.describe()} cannot be authorized: " + "; ".join(entry["message"] for entry in stopping),
                changed=False,
                **self._facts(evaluation),
            )

    def _facts(self, evaluation):
        return {
            "unmet": evaluation.entries,
            "releases": [{"target_path": self.key.target_path, **release} for release in releases_to_print(evaluation.entries)],
            "holders": evaluation.holders,
            "replaces": evaluation.directory.replaces,
        }

    # writes

    def _delete_records(self, records):
        """Delete records, and return those this task deleted; one already gone is no change."""
        if not records:
            return []
        owned = self._owned_snapshots(records)
        deleted = [record for record in records if self._delete_record(record)]
        self.snapshots = self._snapshots_left(owned)
        return deleted

    def _delete_record(self, record):
        """Whether this request deleted the record; False when it was already gone."""
        record_id = str(record["id"])
        try:
            self.client.post(TARGET_DELETE, params={"id": record_id})
        except QumuloMaybeApplied as lost:
            if self.client.get_or_none(TARGET_STATUS, params={"id": record_id}) is not None:
                raise QumuloError(f"{lost}; target record {record_id} still exists", changed=True, maybe_applied=True) from None
            self.notes.append(f"the delete of target record {record_id} was reconciled after a lost connection")
        except QumuloApiError as e:
            if not (e.status == 404 and e.is_(NOT_FOUND)):
                raise
            self.notes.append(f"target record {record_id} was already gone")
            return False
        self.done.append(f"deleted target record {record_id} ({record['state']}, from {record['source_cluster_uuid']}:{record['source_path']})")
        return True

    def _authorize(self, own_id, directory):
        # The flag goes only with the consent, and only for a directory that holds entries now, so anything that appears
        # after the check is refused by the server. Under allow_directories_only the walk just before this authorize is the
        # consent's only check. The collection never sends allow-fs-path-create.
        consent = self.params["allow_non_empty_directory"] or (self.params["allow_directories_only"] and not directory.holds_objects)
        query = {"allow-non-empty-directory": True} if directory.has_entries and consent else None
        try:
            self.client.post(TARGET_AUTHORIZE, params={"id": own_id}, query=query)
        except QumuloMaybeApplied as lost:
            found = self.client.get_or_none(TARGET_STATUS, params={"id": own_id})
            if found is None or found.data["state"] == PENDING:
                raise QumuloError(
                    f"{lost}; target record {own_id} {'is gone' if found is None else 'still awaits authorization'}, and an authorize is never resent; "
                    "run again to read it and decide (G-REPL-ONESHOT)",
                    changed=True,
                    maybe_applied=True,
                ) from None
            self.notes.append("the authorize was reconciled after a lost connection")
        self.done.append(f"authorized target record {own_id}")

    def _settle(self, own_id):
        if self.params["wait"]:
            record = self.wait_by_id(TARGET_STATUS, own_id, lambda record: record["state"] != PENDING, "ESTABLISHED")
        else:
            record = self.client.get(TARGET_STATUS, params={"id": own_id}).data
        after = describe_target(record)
        if after["state"] == ENDED:
            raise QumuloError(f"{self.describe()} (id {own_id}) ENDED after the authorize: {after['end_reason']}", changed=True)
        if after["state"] in FAILOVER:
            self._warn_failover(after)
        return after

    # snapshots

    def _owned_snapshots(self, records):
        ids = {str(record["id"]) for record in records}
        return {snapshot["id"] for snapshot in self.client.paginate(SNAPSHOT_STATUSES, "entries") if snapshot_owners(snapshot) & ids}

    def _snapshots_left(self, owned):
        """The snapshots the deleted records owned, and the replication snapshots owned by nothing at the target path.

        A record's snapshot can lose its owner before this task, at the source's delete (research R13), so the
        ownerless ones count; an earlier removal at the same path may have left some of them.
        """
        left = []
        for snapshot in self.client.paginate(SNAPSHOT_STATUSES, "entries"):
            ownerless = not snapshot_owners(snapshot) and _snapshot_path(snapshot) == self.key.target_path and REPLICATION_SNAPSHOT.match(snapshot["name"])
            if snapshot["id"] in owned or ownerless:
                left.append({"id": snapshot["id"], "name": snapshot["name"]})
        if left:
            would = "would be " if self.check else ""
            self.notes.append(
                f"snapshots that the deleted records owned, or that replication left at {self.key.target_path} owned by nothing, perhaps in an "
                f"earlier removal; they {would}left, never expiring: "
                + ", ".join(f"{snapshot['name']} (id {snapshot['id']})" for snapshot in left)
                + "; delete them once the copy is no longer wanted (G-REPL-SNAPSHOT)"
            )
        return left

    # absent

    def _absent(self):
        targets = [describe_target(record) for record in self.client.get(TARGET_STATUSES).data or []]
        doomed, holders = [], []
        would = "would be " if self.check else "is "
        for record in targets:
            same_paths = (record["source_path"], record["target_path"]) == (self.key.source_path, self.key.target_path)
            # A previous source UUID's ENDED or pending record at the same paths holds nothing; a live one is a holder.
            leftover = same_paths and record["source_cluster_uuid"] in self.previous and record["state"] in (PENDING, ENDED)
            if key_of_target(record) == self.key or leftover:
                doomed.append(record)
            elif record["target_path"] == self.key.target_path and record["state"] in LIVE:
                if release_lifts(self.releases, "O2", record["id"]):
                    doomed.append(record)
                    self.notes.append(f"holder {record['id']} {would}deleted under its release; the directory keeps the replica as plain data")
                else:
                    holders.append(_holder(record))
                    self.notes.append(
                        f"relationship {record['id']} from {record['source_cluster_uuid']}:{record['source_path']} still holds {self.key.target_path} "
                        f"({record['state']}); it is left, and a release bound to {record['id']} naming O2 deletes it"
                    )
        deleted = doomed
        if doomed and self.check:
            self.snapshots = self._snapshots_left(self._owned_snapshots(doomed))
        elif doomed:
            with self._after_writes():
                deleted = self._delete_records(doomed)
        if deleted:
            self.notes.append(
                f"{self.key.target_path} keeps its data and is writable once no record holds it; declaring the relationship again replaces its content"
            )
        result = {
            "changed": bool(deleted),
            "relationship": {},
            "deleted": deleted,
            "unmet": [],
            "releases": [],
            "holders": holders,
            "replaces": None,
            "snapshots": self.snapshots,
        }
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"relationship": {}, "records": deleted}, "after": {"relationship": {}, "records": []}}
        return result

    # results

    def _predicted(self, own, evaluation):
        if own is None:
            self.notes.append(
                f"{self.describe()} does not exist yet: the source's create registers it here, and this module then authorizes it by the id the create returns"
            )
            own_uuid = lower(node_state(self.client)[0])
            blank = {**dict.fromkeys(SHAPE), **self.key._asdict(), "target_cluster_uuid": own_uuid}
            after = {**blank, "state": ESTABLISHED, "target_root_read_only": True}
        else:
            after = {**own, "state": ESTABLISHED, "target_root_read_only": True}
        deletes = [*evaluation.releases, *evaluation.prunes]
        if deletes:
            self.snapshots = self._snapshots_left(self._owned_snapshots(deletes))
        for record in deletes:
            self.notes.append(f"would delete target record {record['id']} ({record['state']}, from {record['source_cluster_uuid']}:{record['source_path']})")
        return self._result(True, own or {}, after, evaluation, deletes)

    def _result(self, changed, before, after, evaluation=None, deletes=()):
        """The result; its diff shows the records the task deletes beside the relationship it authorizes."""
        result = {"changed": changed, "relationship": after, "unmet": [], "releases": [], "holders": [], "replaces": None, "snapshots": self.snapshots}
        if evaluation is not None:
            result.update(self._facts(evaluation))
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"relationship": before, "records": list(deletes)}, "after": {"relationship": after, "records": []}}
        return result


def replication_address_verdict(given, target, read_tenants, read_document, replication_enabled):
    """Spec 014 FR-029 for a relationship into target, a path, whose source connects to given, an IP address's text.

    read_tenants gives the cluster's tenants as the tenant list answers them, and is called only for an IP address;
    read_document gives the canonical network document, and is called only with more than one tenant;
    replication_enabled gives a tenant's switch. Returns
    (refusal, warning), each a message or None. No status is read: an address no range holds, a HOST network's host
    address, a DHCP network's node address or one behind NAT, is not judged (spec 014, decision D26).
    """
    try:
        address = network.canonical_ip(given)
    except ValueError:
        return f"replication_address {given!r} is not an IP address; give the address the source connects to", None
    tenants = read_tenants()
    if len(tenants) == 1:
        only = tenants[0]["name"]
        if not replication_enabled(tenants[0]):
            return (
                f"Replication into {target} connects to {address}, but {only}, the only tenant, has replication_enabled: false. "
                f"Set replication_enabled: true on {only}."
            ), None
        return None, None
    held = network.holding(read_document(), address)
    if held is None:
        return None, (
            f"Replication into {target} connects to {address}, which no network's ranges on this cluster hold, as for a HOST "
            "network's host address, a DHCP network's node address or an address behind NAT; whether its tenant has replication_enabled "
            "is not checked."
        )
    where = f"network {held['id']} ({held['name']})"
    tenant = next((entry for entry in tenants if entry["id"] == held["tenant_id"]), None)
    if tenant is None:
        return None, (
            f"Replication into {target} connects to {address}, on {where}, which no tenant holds; whether replication is enabled for it is not checked. "
            f"Bind network {held['id']} to a tenant to have it checked."
        )
    if not replication_enabled(tenant):
        name = tenant["name"]
        return (
            f"Replication into {target} connects to {address}, on {where} of tenant {name}, which has replication_enabled: false. "
            f"Set replication_enabled: true on {name}, or set qumulo_replication_address to an address on a network of a tenant "
            "that has it on."
        ), None
    return None, None
