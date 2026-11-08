# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The replication area: relationships that copy a source directory on one cluster to a target directory on another.

Each side keeps a record of a relationship, under one id the source generates, and the ids change when a
relationship is made again. The modules find a relationship by its key instead: on the source by (source path,
target cluster UUID, target path), and on the target by (source cluster UUID, source path, target path). The address
is a setting, never part of the key. Paths compare without the trailing slash the server adds (QUIRK-020), and UUIDs
without regard to case.

The source module and the target module each report the conditions that keep them from writing as unmet entries, in
one shape (unmet()), so that the role and replication_pair_plan classify them without parsing messages. The overwrite
table in specs/011-replication says what each row means and what lifts it.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import NamedTuple

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_path
from ansible_collections.qumulo.cluster.plugins.module_utils.normalize import canonical_days
from ansible_collections.qumulo.cluster.plugins.module_utils.polling import UNANSWERED, PollingResource

# The status lists are plain arrays, not paged. A create and an authorize are never replayed (G-REPL-ONESHOT): a lost
# one is reconciled by reading the lists.
SOURCES = Route("/v2/replication/source-relationships/", ("GET", "POST"))  # GET lists the configuration only
SOURCE_STATUSES = Route("/v2/replication/source-relationships/status/", ("GET",))
SOURCE = Route("/v2/replication/source-relationships/{id}", ("GET", "PATCH", "DELETE"))
SOURCE_STATUS = Route("/v2/replication/source-relationships/{id}/status", ("GET",))
NO_TARGET_ETAG = "the target has no configuration route, and no target answer carries an ETag"
TARGET_STATUSES = Route("/v2/replication/target-relationships/status/", ("GET",))
TARGET_STATUS = Route("/v2/replication/target-relationships/{id}/status", ("GET",))
TARGET_AUTHORIZE = Route("/v2/replication/target-relationships/{id}/authorize", ("POST",), etag="omit", why=NO_TARGET_ETAG)
TARGET_DELETE = Route("/v2/replication/target-relationships/{id}/delete", ("POST",), etag="omit", why=NO_TARGET_ETAG)
# The failover verbs (research R18), each sent once and reconciled by reading the record (G-REPL-ONESHOT).
TARGET_MAKE_WRITABLE = Route("/v2/replication/target-relationships/{id}/make-writable", ("POST",), etag="omit", why=NO_TARGET_ETAG)
TARGET_RECONNECT = Route("/v2/replication/target-relationships/{id}/reconnect", ("POST",), etag="omit", why=NO_TARGET_ETAG)
SOURCE_REVERSE = Route("/v2/replication/source-relationships/reverse-target-relationship", ("POST",))  # sent to the current target
OBJECTS = Route("/v3/replication/object-relationships/", ("GET",))

PENDING = "AWAITING_AUTHORIZATION"
ESTABLISHED = "ESTABLISHED"
ENDED = "ENDED"
# The states a failover by hand, or a reconnect after one, leaves a relationship in; the modules change none of them.
FAILOVER = frozenset({"DISCONNECTING", "DISCONNECTED", "RECONNECTING", "REESTABLISHING"})
# A live record holds its target directory read-only, or did until a failover.
LIVE = FAILOVER | {ESTABLISHED}
STATES = LIVE | {PENDING, ENDED}
DEFAULT_PORT = 3712

WINDOW_KEYS = ("start_hour", "start_minute", "end_hour", "end_minute", "on_days")
SETTINGS = ("replication_enabled", "map_local_ids_to_nfs_ids", "source_root_read_only", "blackout_windows", "blackout_window_timezone")
# The keys of a relationship as both sides report it; each side gives null for what only the other records.
SHAPE = (
    "id",
    "source_cluster_uuid",
    "target_cluster_uuid",
    "source_path",
    "target_path",
    "target_address",
    "target_port",
    "state",
    "end_reason",
    "job_state",
    "job_start_time",
    "recovery_point",
    "recovery_point_snapshot_id",
    "error_from_last_job",
    "target_root_read_only",
    *SETTINGS,
)
# An object relationship's fields that hold credentials, which replication_info never returns.
OBJECT_CREDENTIALS = frozenset({"access_key_id", "secret_access_key"})


def lower(uuid):
    return uuid.strip().lower() if uuid else uuid


def is_ip_literal(address):
    try:
        ipaddress.ip_address(str(address or ""))
    except ValueError:
        return False
    return True


def _text(value):
    """A status string the server leaves empty for none, such as recovery_point before the first job, as None."""
    return value or None


def _snapshot_id(snapshot):
    """The id of a snapshot a status record names, such as recovery_point_snapshot, as an integer; None for none."""
    return None if not snapshot or snapshot.get("id") is None else int(snapshot["id"])


def blackout_windows(windows):
    """Blackout windows in the module's shape: the five keys, with the days in the server's order."""
    return [{**{key: window[key] for key in WINDOW_KEYS}, "on_days": canonical_days(window["on_days"])} for window in windows or []]


def state_kind(state):
    """The row of the plan's state table a state falls in: pending, established, failover or ended."""
    if state == PENDING:
        return "pending"
    if state == ESTABLISHED:
        return "established"
    if state in FAILOVER:
        return "failover"
    if state == ENDED:
        return "ended"
    raise QumuloError(f"a replication relationship reports state {state!r}, which this collection does not know")


def server_path(path):
    """A path a status record reports, without its trailing slash (QUIRK-020).

    A path that is not absolute, such as an empty one, is kept as read: it can match no declared key, and one odd record
    must not fail a whole read.
    """
    return canonical_path(path) if isinstance(path, str) and path.startswith("/") else path


def _common(record):
    return {
        "id": record["id"],
        "source_cluster_uuid": lower(record.get("source_cluster_uuid")),
        "target_cluster_uuid": lower(record.get("target_cluster_uuid")),
        "source_path": server_path(record["source_root_path"]),
        "target_path": server_path(record["target_root_path"]),
        "state": record["state"],
        "end_reason": _text(record.get("end_reason")),
        "job_state": record.get("job_state"),
        "job_start_time": _text(record.get("job_start_time")),
        "recovery_point": _text(record.get("recovery_point")),
        "recovery_point_snapshot_id": _snapshot_id(record.get("recovery_point_snapshot")),
        "error_from_last_job": _text(record.get("error_from_last_job")),
        "target_root_read_only": record.get("target_root_read_only"),
    }


def describe_source(record):
    """A source status record in the shape both sides share."""
    described = {
        **_common(record),
        "target_address": record["target_address"],
        "target_port": record["target_port"],
        **{key: record.get(key) for key in SETTINGS},
        "blackout_windows": blackout_windows(record.get("blackout_windows")),
    }
    return {key: described[key] for key in SHAPE}


def describe_target(record):
    """A target status record in the shape both sides share. The target records neither the address nor the settings."""
    described = {**_common(record), "target_address": None, "target_port": None, **dict.fromkeys(SETTINGS)}
    return {key: described[key] for key in SHAPE}


def snapshot_owners(snapshot):
    """The ids of the relationships that own a snapshot, as /v4/snapshots/status/ lists it (research R13).

    An owner without an id reads as "None", which owns nothing the caller asks about, rather than failing the read.
    """
    return {str(owner.get("id")) for owner in snapshot.get("owners") or []}


def _sources(client):
    return {"source_relationships": [describe_source(record) for record in client.get(SOURCE_STATUSES).data or []]}


def _targets(client):
    return {"target_relationships": [describe_target(record) for record in client.get(TARGET_STATUSES).data or []]}


def _objects(client):
    records = client.get(OBJECTS).data or []
    return {"object_relationships": [{key: value for key, value in record.items() if key not in OBJECT_CREDENTIALS} for record in records]}


# gather_subset name -> function of the client returning a dict of that subset's facts, in gathering order
REPLICATION_INFO_SUBSETS = {"source": _sources, "target": _targets, "object": _objects}
# A side's status route by id, and the function that describes its records.
BY_SIDE = {"source": (SOURCE_STATUS, describe_source), "target": (TARGET_STATUS, describe_target)}


def read_by_id(client, side, relationship_id):
    """This cluster's record of relationship_id on side, source or target, described; None when it holds none there."""
    route, describe = BY_SIDE[side]
    found = client.get_or_none(route, params={"id": relationship_id})
    return None if found is None else describe(found.data)


# Keys


class SourceKey(NamedTuple):
    """A relationship's key on the source; build it with source_key()."""

    source_path: str
    target_cluster_uuid: str
    target_path: str


class TargetKey(NamedTuple):
    """A relationship's key on the target; build it with target_key()."""

    source_cluster_uuid: str
    source_path: str
    target_path: str


def source_key(source_path, target_cluster_uuid, target_path):
    return SourceKey(canonical_path(source_path, "source_path"), lower(target_cluster_uuid), canonical_path(target_path, "target_path"))


def target_key(source_cluster_uuid, source_path, target_path):
    return TargetKey(lower(source_cluster_uuid), canonical_path(source_path, "source_path"), canonical_path(target_path, "target_path"))


def key_of_source(described):
    return SourceKey(described["source_path"], described["target_cluster_uuid"], described["target_path"])


def key_of_target(described):
    return TargetKey(described["source_cluster_uuid"], described["source_path"], described["target_path"])


def only_one(matches, what):
    """The one match, or None; QumuloError naming every id when there are more."""
    if len(matches) > 1:
        ids = ", ".join(str(match["id"]) for match in matches)
        raise QumuloError(f"{len(matches)} records match {what} (ids {ids}); delete all but one")
    return matches[0] if matches else None


def same_address(a, b):
    """Whether two IP literals name one address, however each is spelled."""
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return a == b


def overlaps(a, b):
    """Whether two canonical paths are equal, or one lies inside the other."""
    return a == b or a == "/" or b == "/" or a.startswith(b + "/") or b.startswith(a + "/")


@dataclass
class SourceMatch:
    """A source's status records sorted against one declared relationship (spec FR-004).

    matches: the records of the key that are not ENDED, or, when every record of the key is ENDED, those (row O3).
    ended: the key's ENDED records beside one that is not, as O3's create leaves them until its delete; the real
      pass deletes them.
    changed_peers: records with the same paths and another target UUID that is no other inventory host's: at the
      declared address and port, or of a previous UUID of the target host at any address.
    misdirected: records with the same paths at the declared address and port whose target UUID is another
      inventory host's, so the address reaches the wrong cluster.
    Every other record, such as one at another address of an unknown UUID, belongs to another target and is left out.
    """

    matches: list = field(default_factory=list)
    ended: list = field(default_factory=list)
    changed_peers: list = field(default_factory=list)
    misdirected: list = field(default_factory=list)


def match_source(records, key, *, address, port=DEFAULT_PORT, previous_uuids=(), inventory_uuids=()):
    """Sort described source records against key, a SourceKey, for a relationship declared at address and port."""
    previous = {lower(uuid) for uuid in previous_uuids}
    if key.target_cluster_uuid in previous:
        raise QumuloError(f"the previous target UUIDs hold the declared target UUID {key.target_cluster_uuid}; a cluster's previous UUIDs never hold its own")
    others = {lower(uuid) for uuid in inventory_uuids} - {key.target_cluster_uuid}
    found = SourceMatch()
    for record in records:
        if (record["source_path"], record["target_path"]) != (key.source_path, key.target_path):
            continue
        uuid = record["target_cluster_uuid"]
        at_declared = same_address(record["target_address"], address) and record["target_port"] == port
        if uuid == key.target_cluster_uuid:
            found.matches.append(record)
        elif uuid in others:
            if at_declared:
                found.misdirected.append(record)
        elif at_declared or uuid in previous:
            found.changed_peers.append(record)
    if any(record["state"] != ENDED for record in found.matches):
        found.ended = [record for record in found.matches if record["state"] == ENDED]
        found.matches = [record for record in found.matches if record["state"] != ENDED]
    return found


@dataclass
class TargetMatch:
    """A target's status records of one key: the source's own record, and every other record of the key, which is stale."""

    own: dict | None = None
    stale: list = field(default_factory=list)


def match_target(records, key, source_id=None):
    """Sort described target records of key, a TargetKey; with no source_id, as when the source will create, all are stale."""
    found = TargetMatch()
    for record in records:
        if key_of_target(record) != key:
            continue
        if source_id is not None and str(record["id"]) == str(source_id):
            found.own = record
        else:
            found.stale.append(record)
    return found


# Unmet conditions

# The overwrite table's rows (spec, "Overwrite and release rules"), and what lifts each: a release entry bound to a
# record, the entry's consent, both, or nothing. lifted_by is for the operator who edits the inventory, so O1 names
# the relationship entry's allow_non_empty_target, from which the role passes replication_target's
# allow_non_empty_directory.
ROWS = {
    "O1": "allow_non_empty_target",
    "O2": "release",
    "O3": "recreate and release",
    "O4": "release",
    "O5": "release",
    "O6": None,
    "O7": None,
    "O8": None,
    "O9": None,
}
# The other conditions a module reports as unmet, and what lifts each.
CONDITIONS = {
    "source_missing": None,  # the source directory does not exist; pending when the directory step will create it
    "target_missing": None,  # the target directory does not exist; pending when the directory step will create it
    "changed_peer": "recreate_changed_peer",  # a record of the same paths to a changed peer (spec FR-004)
    "misdirected": None,  # a record at the declared address of another inventory host's UUID
    "ended_on_target": None,  # the source's record is live and the target's record of the same id is ENDED
    "capacity": None,  # the target cannot hold the source; with strict_capacity, since otherwise it only warns
}
# The rows a release entry can lift; parse_release refuses any other.
RELEASABLE = frozenset({"O2", "O3", "O4", "O5"})
# The order the modules report unmet rows in. FR-007 orders the target's rows, O7, O9, O2, O6, O5, then O4 before O1,
# so a held relationship reads as held. The source's O8 and O3 come first, and the other conditions before every row,
# which are this collection's choices.
REPORTING_ORDER = ("O8", "O3", "O7", "O9", "O2", "O6", "O5", "O4", "O1")


def unmet(row, message, *, binds_to=None, facts=None, lifted=False, pending=False):
    """One unmet condition, as both modules return it in their unmet list.

    row: an overwrite-table row (ROWS) or another condition (CONDITIONS).
    binds_to: for a releasable row, the id a release entry must carry to lift it: the record's id, or for O4 without a
      holder or an ENDED record, the target directory's file id.
    facts: what the role and the reports need without parsing the message, such as a holder's source UUID and state.
    lifted: whether this run's consent or release lifts it; an entry that is lifted does not stop the write.
    pending: whether it is a check-mode prediction that an earlier step of the run meets, such as a directory to create.
      Only a condition can be pending; an overwrite-table row always needs its consent.
    """
    if row not in ROWS and row not in CONDITIONS:
        raise ValueError(f"unmet row {row!r} is neither an overwrite-table row nor a known condition")
    if (row in RELEASABLE) != (binds_to is not None):
        raise ValueError(f"unmet row {row}: a releasable row binds to an id, and no other row does")
    lifted_by = ROWS.get(row) or CONDITIONS.get(row)
    if lifted and not lifted_by:
        raise ValueError(f"unmet row {row} cannot be lifted")
    if pending and row in ROWS:
        raise ValueError(f"unmet row {row} is an overwrite-table row, which cannot be pending")
    return {
        "row": row,
        "message": message,
        "binds_to": None if binds_to is None else str(binds_to),
        "lifted_by": lifted_by,
        "lifted": bool(lifted),
        "pending": bool(pending),
        "facts": dict(facts or {}),
    }


def reporting_order(entries):
    """Unmet entries with the table's rows in the reporting order, and other conditions first, each in the order given."""
    position = {row: index for index, row in enumerate(REPORTING_ORDER)}
    return sorted(entries, key=lambda entry: position.get(entry["row"], -1))


def blocking(entries):
    """The unmet entries that stop a write: neither lifted nor pending."""
    return [entry for entry in entries if not entry["lifted"] and not entry["pending"]]


# Releases

RELEASE_FORM = "<target host>:<target path>@<record id>:<rows>"


def parse_release(entry):
    """A qumulo_replication_release entry as a dict (spec FR-013): split at the first colon, the last @, then the last colon."""
    host, colon, rest = str(entry).partition(":")
    path, at, bound = rest.rpartition("@")
    record_id, colon_after, rows = bound.rpartition(":")
    if not (host and colon and path and at and record_id and colon_after and rows):
        raise QumuloError(f"release entry {entry!r} is not {RELEASE_FORM}")
    named = [row.strip() for row in rows.split(",")]
    if "" in named:
        raise QumuloError(f"release entry {entry!r} is not {RELEASE_FORM}: its rows hold an empty name")
    unknown = [row for row in named if row not in RELEASABLE]
    if unknown:
        raise QumuloError(f"release entry {entry!r} names {', '.join(unknown)}; a release lifts only {', '.join(sorted(RELEASABLE))}")
    return {"target_host": host, "target_path": canonical_path(path, "the release entry's target path"), "record_id": record_id, "rows": sorted(set(named))}


def release_lifts(releases, row, binds_to):
    """Whether a parsed release entry is bound to binds_to and names row.

    The caller passes only the entries that name this relationship, by target host and path: an O4 can bind to a
    directory's file id, which another cluster can hold too.
    """
    return any(release["record_id"] == str(binds_to) and row in release["rows"] for release in releases)


def releases_to_print(entries):
    """One release per id that still binds an unmet entry no release lifts, with every row the check reported on that id.

    The rows already lifted stay in it, so the printed entry alone lifts all of them on the next run.
    """
    by_id, waiting = {}, set()
    for entry in entries:
        if entry["binds_to"] is not None:
            by_id.setdefault(entry["binds_to"], set()).add(entry["row"])
            if not entry["lifted"]:
                waiting.add(entry["binds_to"])
    return [{"record_id": record_id, "rows": sorted(rows)} for record_id, rows in by_id.items() if record_id in waiting]


def format_release(target_host, target_path, record_id, rows):
    return f"{target_host}:{canonical_path(target_path, 'target_path')}@{record_id}:{','.join(rows)}"


# Waits


class Relationship(PollingResource):
    """What the source and target classes share: the result key, and a wait on one relationship by its id."""

    RETURN_KEY = "relationship"

    def wait_by_id(self, route, relationship_id, done, goal):
        """Poll route, a status route, by id until done(record) holds, and return that record.

        goal says what the wait is for, for its timeout message. A record that is gone fails at once.
        """
        last = None
        while True:
            record = self._poll(route, id=relationship_id)
            if record is None:
                raise QumuloError(f"{self.describe()} (id {relationship_id}) is gone; waited for {goal}")
            if record is not UNANSWERED:
                if done(record):
                    return record
                last = record
            if not self._pause():
                seen = f"still {last['state']}" if last else "not answering"
                raise QumuloError(f"{self.describe()} (id {relationship_id}) is {seen} after {self.timeout}s; waited for {goal}")


def releases_lifting_nothing(releases, entries):
    """The parsed release entries that lift none of the unmet entries: none bound to the entry's id, naming its row, lifted.

    The role refuses such an entry, so one left in an inventory fails the next run instead of waiting for a case to lift.
    """
    used = {(entry["binds_to"], entry["row"]) for entry in entries if entry["binds_to"] is not None and entry["lifted"]}
    return [release for release in releases if not any((release["record_id"], row) in used for row in release["rows"])]


# The pair plan: the replication_pair role's join of both modules' check passes


@dataclass
class PairPlan:
    """What the replication_pair role does with one relationship after both check passes (replication_pair_plan)."""

    status: str = "go"  # go, held or refused
    unmet: list = field(default_factory=list)
    blocking: list = field(default_factory=list)
    releases: list = field(default_factory=list)
    lifting_nothing: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    pairing: bool = False
    holders: list = field(default_factory=list)
    message: str = ""
    restore_form: bool = False


@dataclass
class RestoreForm:
    """What the held message's restore form names (replication_pair_plan's source_* options).

    license_rule: expected, none or outside_edges, the license rule's answer for the source host.
    """

    host: str
    source_path: str
    cluster_uuid: str
    license_rule: str
    ip_literal: bool


def declared_directories(target_path, directories):
    """Whether target_path is among the declared directories, and the declared ancestors of it, outermost first.

    directories: the target host's qumulo_directories, each with a path and an optional state; absent ones do not count.
    Each ancestor is its entry, with its path canonical, so the role creates it as the target host declares it.
    """
    if any(not entry.get("path") for entry in directories or []):
        raise QumuloError("the target host's directories hold a qumulo_directories entry without a path")
    present = [
        {**entry, "path": canonical_path(entry["path"], "a qumulo_directories path")}
        for entry in directories or []
        if entry.get("state", "present") != "absent"
    ]
    ancestors = sorted(
        (entry for entry in present if entry["path"] != "/" and target_path.startswith(entry["path"] + "/")), key=lambda entry: entry["path"].count("/")
    )
    return any(entry["path"] == target_path for entry in present), ancestors


def target_releases(releases):
    """The release entries as the target's passes take them: an entry naming O3 also lifts O5 for the same id.

    The source's ENDED record under O3 and the target's ENDED copy of it share the id, and the target, which cannot see
    the source, reports that copy as O5. The record is O3's, so the release printed for O3 alone lifts both.
    """
    lifted = []
    for entry in releases:
        release = parse_release(entry)
        rows = release["rows"]
        if "O3" in rows and "O5" not in rows:
            entry = format_release(release["target_host"], release["target_path"], release["record_id"], sorted({*rows, "O5"}))
        lifted.append(entry)
    return lifted


def _check_entries(result, side):
    """A check pass's unmet entries, each marked with its side, and its failure when no entry explains it.

    A refusal registered with failed_when: false, as the role registers both checks, keeps its msg but not its failed;
    a module that succeeds returns no msg.
    """
    if not result:
        return [], []
    entries = [{**entry, "side": side} for entry in result.get("unmet") or []]
    failure = None
    if (result.get("failed") or "msg" in result) and not blocking(entries):
        failure = f"the {side} check failed: {result.get('msg') or 'without a message'}"
    return entries, [failure] if failure else []


def pair_plan(
    *,
    source,
    target,
    release,
    target_host,
    target_path,
    previous_source_cluster_uuids=(),
    allow_non_empty_target=False,
    target_directories=(),
    removed=None,
    restore=None,
):
    """Join the source's and the target's check passes for one relationship (spec FR-008, the overwrite table's notes).

    source and target are the two modules' check results, a refusal included; removed is replication_target's absent
    result, for a removal, which has no check pass. release holds the qumulo_replication_release entries that name this
    relationship.
    """
    target_path = canonical_path(target_path, "target_path")
    releases = [parse_release(entry) for entry in release]
    for entry, parsed in zip(release, releases):
        if (parsed["target_host"], parsed["target_path"]) != (target_host, target_path):
            raise QumuloError(f"release entry {entry!r} names {parsed['target_host']}:{parsed['target_path']}, not {target_host}:{target_path}")
    plan = PairPlan()
    name = f"{target_host}:{target_path}"
    if removed is not None:
        deleted = {str(record["id"]) for record in removed.get("deleted") or []}
        plan.lifting_nothing = [entry for entry, parsed in zip(release, releases) if parsed["record_id"] not in deleted]
        if plan.lifting_nothing:
            plan.status = "refused"
            plan.message = f"{name}: {_lifting_nothing_text(plan.lifting_nothing)}"
        return plan
    source_entries, source_failures = _check_entries(source, "source")
    target_entries, target_failures = _check_entries(target, "target")
    # The target's ENDED copy of the source's O3 record is O3's, and the release for O3 lifts it (target_releases).
    o3 = {entry["binds_to"] for entry in source_entries if entry["row"] == "O3"}
    target_entries = [entry for entry in target_entries if not (entry["row"] == "O5" and entry["binds_to"] in o3)]
    plan.unmet = reporting_order([*source_entries, *target_entries])
    plan.blocking = blocking(plan.unmet)
    plan.failures = [*source_failures, *target_failures]
    plan.releases = [format_release(target_host, target_path, printed["record_id"], printed["rows"]) for printed in releases_to_print(plan.unmet)]
    # A check that failed reported none of the rows a release may be for, so no release reads as spent then.
    nothing = [] if plan.failures else releases_lifting_nothing(releases, plan.unmet)
    plan.lifting_nothing = [entry for entry, parsed in zip(release, releases) if parsed in nothing]
    # The target evaluates the overwrite table, and so reports what an authorize replaces, only before an authorize.
    plan.pairing = bool((source or {}).get("creates")) or (target or {}).get("replaces") is not None
    plan.holders = list((target or {}).get("holders") or [])
    declared = declared_directories(target_path, target_directories)[0]
    consent = []
    if allow_non_empty_target and not declared:
        consent.append(
            f"allow_non_empty_target is accepted only for a target directory the target host declares, and {target_host}'s "
            f"qumulo_directories does not declare {target_path}"
        )
    previous = {lower(uuid) for uuid in previous_source_cluster_uuids}
    held = [
        entry
        for entry in plan.blocking
        if previous and (entry["row"] == "O4" or (entry["row"] == "O2" and lower(entry["facts"].get("source_cluster_uuid")) in previous))
    ]
    reasons = [entry["message"] for entry in plan.blocking] + consent + plan.failures
    if plan.lifting_nothing:
        reasons.append(_lifting_nothing_text(plan.lifting_nothing))
    if held:
        plan.status = "held"
        sentence = _restore_sentence(restore, source, held)
        plan.restore_form = sentence is not None
        plan.message = _held_text(name, plan, held, reasons, sentence)
    elif reasons:
        plan.status = "refused"
        plan.message = f"{name} is refused before any write on either cluster: " + "; ".join(reasons) + _releases_text(plan.releases)
    return plan


def _lifting_nothing_text(entries):
    quoted = ", ".join(repr(entry) for entry in entries)
    return f"release entry {quoted} lifts nothing in this run; a release is for one run only, so remove it"


def _releases_text(printed):
    if not printed:
        return ""
    return f". The release that lifts the releasable rows, for one run, passed with -e: {json_release(printed)}"


def json_release(printed):
    quoted = ", ".join(f'"{entry}"' for entry in printed)
    return f"'{{\"qumulo_replication_release\": [{quoted}]}}'"


def _restore_sentence(restore, source, held):
    """The restore form's sentence, when X's facts allow the restore (spec User Story 3, scenario 3); None otherwise.

    X is the unlifted O2 holder from a previous UUID of the source. The full check is the restore's own step 1.
    """
    x = next((entry["facts"] for entry in held if entry["row"] == "O2"), None)
    if restore is None or x is None or not restore.ip_literal or (source or {}).get("source_empty") is not True:
        return None
    if x.get("state") != ESTABLISHED or x.get("target_root_read_only") is not True or not x.get("recovery_point"):
        return None
    host, path = restore.host, restore.source_path
    if restore.license_rule == "outside_edges":
        return (
            f"ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l {host} restores it: it copies the replica back into {path} "
            f"and reverses the relationship, so that it runs from {host}'s current UUID again."
        )
    command = f"ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l {host}"
    copies = f"copies the replica back into {path} and reverses the relationship, so that it runs from {restore.cluster_uuid} again."
    if restore.license_rule == "none":
        return f"The second pass restores it: {command} {copies} {host} runs without a license, so nothing waits for one."
    return (
        f"The second pass restores it: once the license applies, {command} {copies} If {host} runs without a license, add license: none "
        f"to {host}'s qumulo_capacity, keeping its other keys, such as clamp and free_threshold."
    )


def _held_text(name, plan, held, reasons, restore_sentence=None):
    """The held refusal: the replica is the only copy, and the way out is the restore when restore_sentence names it, or
    a copy and the release."""
    # A holder from a previous source UUID first; otherwise another cluster's, when O4 alone makes it held.
    previous = next((entry["facts"] for entry in held if entry["row"] == "O2"), None)
    holder = previous or next((entry["facts"] for entry in plan.blocking if entry["row"] == "O2"), None)
    if holder is not None:
        keep = "set the entry to state: absent and run with the same release, which deletes the holder"
        whose = "a previous UUID of the source" if previous else "another cluster"
        lock = (
            f" Record {holder['id']} from {holder['source_cluster_uuid']}:{holder['source_path']}, {whose}, holds it "
            f"({holder['state']}, recovery point {holder.get('recovery_point') or 'none'}, last error {holder.get('error_from_last_job') or 'none'})."
        )
        if holder["state"] in FAILOVER:
            lock += " A failover by hand made the replica writable, so it may be in use and differ from the old source's data."
    else:
        keep = "set the entry to state: absent"
        lock = " No record holds it, but the source directory holds no files while the target directory does (O4)."
    consent = ""
    if any(entry["row"] == "O1" and not entry["lifted"] for entry in plan.unmet):
        consent = ", with allow_non_empty_target: true on the relationship's entry, since the directory holds the replica"
    way = (
        "To replicate again, copy the replica back into the source directory, then run again"
        f"{consent} with the release passed for that run only: -e {json_release(plan.releases)}."
    )
    if restore_sentence:
        # The restore form names a holder from a previous UUID, and prints the release here in place of the copy's sentence.
        way = restore_sentence
        keep = f"set the entry to state: absent and run with this release, which deletes the holder: -e {json_release(plan.releases)}"
    return (
        f"{name} is held: its replica may be the only copy of the rebuilt source's data, so nothing is written on either cluster.{lock} "
        f"{way} To keep the replica as plain data instead, {keep}. Unmet: " + "; ".join(reasons)
    )
