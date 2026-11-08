# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The restore's step choice: which step of a rebuilt source's restore from its replica runs next, from fresh reads.

The plan's Restore section (specs/011-replication) names the records:
- X, the old record on the target that holds the replica: a live record at the target path from a previous source
  UUID of the edge;
- R, the restore relationship, from the target's replica directory to the edge's declared source path. Before the
  reverse its source record is on the target and its target record on the edge; after it, the other way round, in the
  declared relationship's own orientation;
- the restore record, which the controller writes after X's delete and marks before the reconnect.

replication_restore_step calls choose() for each relationship, which applies the selection rule in its order and
returns the first answer that applies: a step with its ordered actions, or an outcome. pick() is the edge's run-level
choice of relationships, and compose_record() and round_check() serve step 7's record and step 4's rounds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_path, inside
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import ENDED, ESTABLISHED, LIVE, PENDING, blocking, is_ip_literal, lower, overlaps
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import (
    RestoreRecordError,
    key_hash,
    normalized_key,
    parse_record,
    record_path,
    restore_dir,
    stamp,
    stamp_path,
)

DISCONNECTING = "DISCONNECTING"
DISCONNECTED = "DISCONNECTED"
RECONNECTING = "RECONNECTING"
REESTABLISHING = "REESTABLISHING"
# The states that show a reconnect was accepted, and the ones a source that lags may still show beside them.
RECONNECTED = frozenset({RECONNECTING, REESTABLISHING, ESTABLISHED})
LAGGING = frozenset({DISCONNECTED, RECONNECTING, REESTABLISHING})
LAST_STEP = 9
# The server's refusal of a create or a reverse whose peer address cannot be reached; nothing is written (research R8, R19).
CONNECTION_ERROR = "replication_connection_error"
# The declared entry's settings, which step 9 applies to R.
SETTINGS = ("replication_enabled", "map_local_ids_to_nfs_ids", "source_root_read_only", "blackout_windows", "blackout_window_timezone")

# The report's outcome names (plan, Restore, "Each relationship's outcome"), and those the run fails on.
DONE = "done"
REFUSED = "refused"
STOPPED = "stopped"
NOT_RESTORED = "not restored"
REFUSED_BY_STEP_21 = "refused by step 21"
NOT_APPLICABLE = "not applicable"
FAILED = "failed"
FAILING = frozenset({REFUSED, STOPPED, NOT_RESTORED, REFUSED_BY_STEP_21, FAILED})


def utc(text, option):
    """An ISO 8601 time as an aware UTC datetime; a time without a zone counts as UTC."""
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError as e:
        raise QumuloError(f"{option} {text!r} is not an ISO 8601 time") from e
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_point_command(entry_point, inventory, edge):
    playbook = "edge_license_complete" if entry_point == "second_pass" else "replication_restore"
    return f"ansible-playbook qumulo.cluster.{playbook} -i {inventory} -l {edge}"


def exit_before_recovery_point(r_id, edge, source_path, target_host, target_path):
    """The abandon exit before R's first recovery point (plan, Abandon)."""
    return (
        f"To abandon it, delete R {r_id} on {target_host}, then on {edge}; {edge}:{source_path} then holds a partial copy, which must "
        f"not be released over the replica, and {target_host}:{target_path} still holds the full replica"
    )


def release_after_sibling(edge, inventory, source_path, target_host, target_path, sibling):
    """The way on of a relationship whose directory a sibling with an equal or outer source path was restored into first.

    The edge's directory already holds the sibling's restored data, so the release replaces this relationship's replica
    from it; a copy of this replica in around that data would put its files over the restored ones.
    """
    other = f"{edge}:{sibling['source_path']} to {sibling['target_host']}:{sibling['target_path']}"
    return (
        f"{target_host}:{target_path} holds a replica of {edge}:{source_path}, but {other} was restored into {edge}:{sibling['source_path']} "
        f"first, so {edge}:{source_path} already holds that relationship's data. Release {target_host}:{target_path} with "
        f"allow_non_empty_target on the entry and the release ansible-playbook qumulo.cluster.baseline -i {inventory} -l {edge} prints, "
        "which replaces its replica from the restored directory."
    )


def _nesting(a, b):
    """How path a stands to path b: 'is', 'lies under' or 'holds'; None when neither contains the other."""
    if a == b:
        return "is"
    if inside(a, b):
        return "lies under"
    if inside(b, a):
        return "holds"
    return None


def _lists(facts, cluster):
    """A cluster's source and target status lists, both of which a record's absence needs (Terms, Sides)."""
    facts = facts or {}
    missing = [side for side in ("source_relationships", "target_relationships") if facts.get(side) is None]
    if missing:
        raise QumuloError(
            f"the {cluster}'s replication facts lack {' and '.join(missing)}; a record counts as missing only when it is absent in both "
            "orientations, so pass both lists, read whole or by id"
        )
    return list(facts["source_relationships"]), list(facts["target_relationships"])


@dataclass
class Records:
    """The records with R's keys and X, sorted from both clusters' status lists."""

    x: dict | None = None
    ended_beside_x: list = field(default_factory=list)
    foreign: list = field(default_factory=list)
    # Before the reverse: the target's source record from the replica directory, the edge's target record.
    pre_source: list = field(default_factory=list)
    pre_target: list = field(default_factory=list)
    # After the reverse, the declared relationship's orientation: the edge's source record, the target's target record.
    post_source: list = field(default_factory=list)
    post_target: list = field(default_factory=list)

    def by_id(self, records, relationship_id):
        return next((record for record in records if str(record["id"]) == str(relationship_id)), None)


class Restore:
    """One relationship's restore, decided from its inputs (plan, Restore)."""

    def __init__(self, params, check_mode):
        self.params = params
        self.check_mode = check_mode
        entry = params["relationship"]
        for required in ("source_path", "target", "target_path"):
            if not entry.get(required):
                raise QumuloError(f"relationship lacks {required}")
        self.edge = params["edge"]
        self.source_path = canonical_path(entry["source_path"], "relationship.source_path")
        self.target_host = entry["target"]
        self.target_path = canonical_path(entry["target_path"], "relationship.target_path")
        self.edge_uuid = lower(params["edge_cluster_uuid"])
        self.target_uuid = lower(params["target_cluster_uuid"])
        self.previous = {lower(uuid) for uuid in params["previous_cluster_uuids"]}
        self.now = utc(params["now"], "now") if params["now"] else datetime.now(UTC)
        self.name = f"{self.edge}:{self.source_path} to {self.target_host}:{self.target_path}"
        self.entry_point = entry_point_command(params["entry_point"], params["inventory"], self.edge)
        self.resume = entry_point_command("playbook", params["inventory"], self.edge)
        self.key = normalized_key(
            {"source_path": self.source_path, "target_host": self.target_host, "target_cluster_uuid": self.target_uuid, "target_path": self.target_path}
        )
        artifact_dir = params["artifact_dir"] or ""
        self.host_dir = os.path.join(artifact_dir, self.edge) if artifact_dir else ""
        self.directory = restore_dir(artifact_dir, self.edge) if artifact_dir else ""
        self.record_path = record_path(self.directory, self.key) if artifact_dir else ""
        self.stamp_path = stamp_path(self.directory, self.key) if artifact_dir else ""
        claims = params["report_claims"] or {}
        self.report = claims.get("report")
        claimed = [normalized_key(key) for key in claims.get("claimed") or []]
        self.claimed = any(_same_key(key, self.key) for key in claimed)
        self.warnings = []
        self.pre_actions = []
        self.failed_guards = []

    # The answer

    def result(self, decision, **found):
        outcome = found.get("outcome")
        answer = {
            "decision": decision,
            "step": None,
            "outcome": outcome,
            "fails": outcome in FAILING,
            "message": "",
            "note": "",
            "summary": "",
            "actions": [],
            "reads": [],
            "sequence": [],
            "restore_to_do": self.restore_to_do,
            "relationship_id": self.r_id,
            "x": self.records.x if hasattr(self, "records") else None,
            "past_delete": False,
            "snapshots_left": [],
            "name": self.name,
            "record_path": self.record_path,
            "stamp_path": self.stamp_path,
            "source_directories": self.source_directories(),
            **found,
        }
        # A note is a clause the summary line and the refusals end with a period of their own.
        answer["note"] = answer["note"].rstrip(".")
        answer["actions"] = self.pre_actions + answer["actions"]
        answer["warnings"] = self.warnings
        if outcome == NOT_APPLICABLE and not answer["summary"]:
            answer["summary"] = f"{self.name}: not applicable: {answer['note']}."
        return answer

    def step(self, number, actions, **found):
        sequence = list(range(number, LAST_STEP + 1)) if self.check_mode else []
        return self.result("step", step=number, actions=actions, sequence=sequence, **found)

    def outcome(self, outcome, message="", **found):
        decision = outcome.replace(" ", "_")
        return self.result(decision, outcome=outcome, message=message, **found)

    def source_directories(self):
        """The source path and its declared ancestors, outermost first, whose existence step 1 needs."""
        ancestors = sorted(
            {
                canonical_path(entry["path"], "a qumulo_directories path")
                for entry in self.params["directories"] or []
                if entry.get("path") and entry.get("state", "present") != "absent"
            }
        )
        found = [path for path in ancestors if path != "/" and self.source_path.startswith(path + "/")]
        return sorted(found, key=lambda path: path.count("/")) + [self.source_path]

    # The selection rule

    def choose(self):
        self.r_id = None
        self.restore_to_do = self.claimed
        try:
            self.record = self._record()
        except RestoreRecordError as e:
            # A record of an unknown format stops the run, naming the file (item 1): the role fails the edge on stops_run.
            way_on = (
                f"Move it aside only once no restore of {self.edge} is under way on any controller, then run {self.entry_point} again; a run "
                "without it cannot resume that restore's reconnect."
            )
            raise QumuloError(f"{_sentence(str(e))} {way_on}", stops_run=True) from e
        if self.record is not None:
            self.restore_to_do = True
            self.r_id = str(self.record["r_id"])
        if self.params["target_error"]:
            return self._unreachable()
        self.records = self._sort()
        if self.records.x is not None:
            self.restore_to_do = True
        if self.record is not None:
            stale = self._stale()
            if stale:
                self.warnings.append(stale)
                self.pre_actions.append({"action": "remove_record", "path": self.record_path})
                self.record, self.r_id = None, None
                self.restore_to_do = self.claimed or self.records.x is not None
        if self.record is not None:
            answer = self._reconnected()
            if answer is not None:
                return answer
        pair = self._pair()
        if pair is not None:
            self.restore_to_do = True
        if self.record is None:
            done = self._done()
            if done is not None:
                return done
        if pair is not None:
            return self._in_progress(pair)
        if self.records.x is not None:
            return self._step_1_or_2()
        return self._not_applicable()

    def _record(self):
        text = self.params["record_text"]
        if text is None:
            return None
        record = parse_record(text, self.record_path or "the restore record")
        if not _same_key(normalized_key(record["key"]), self.key):
            raise RestoreRecordError(f"The restore record {self.record_path} holds the key {record['key']}, not this relationship's.")
        return record

    def _unreachable(self):
        """A target that does not answer: failed while the relationship has a restore to do, which only the record and the
        report claim can show then, and otherwise not applicable with the warning."""
        if self.restore_to_do:
            message = f"{_sentence(self.params['target_error'])} Fix the cause and run {self.entry_point} again."
            return self.outcome(FAILED, message)
        warning = f"{self.edge}: {self.target_host} did not answer, so this run could not check its records of {self.edge}'s relationships."
        self.warnings.append(warning)
        self._no_report_warning()
        return self.outcome(NOT_APPLICABLE, note=warning)

    def _no_report_warning(self):
        if self.report is None and self.record is None:
            self.warnings.append(
                f"{self.edge}: no rebuild report on this controller, under {self.host_dir or '<edge_rebuild_artifact_dir>/' + self.edge}, so this run "
                "cannot tell a stranded restore from a failover by hand."
            )

    def _sort(self):
        edge_sources, edge_targets = _lists(self.params["edge_relationships"], "edge")
        target_sources, target_targets = _lists(self.params["target_relationships"], "target")
        found = Records()
        known = {self.edge_uuid, *self.previous, *(lower(uuid) for uuid in (self.params["inventory_cluster_uuids"] or {}).values())}
        holders = []
        for record in target_targets:
            if record["target_path"] != self.target_path:
                continue
            uuid = lower(record["source_cluster_uuid"])
            if record["state"] in LIVE and uuid in self.previous:
                holders.append(record)
            elif record["state"] in LIVE and uuid not in known:
                found.foreign.append(record)
            elif record["state"] == ENDED and uuid != self.edge_uuid:
                found.ended_beside_x.append(record)
        if len(holders) > 1:
            ids = ", ".join(str(record["id"]) for record in holders)
            raise QumuloError(f"{len(holders)} live records from previous UUIDs of {self.edge} hold {self.target_host}:{self.target_path} (ids {ids})")
        found.x = holders[0] if holders else None
        pre = (self.target_path, self.edge_uuid, self.source_path)
        found.pre_source = [record for record in target_sources if (record["source_path"], lower(record["target_cluster_uuid"]), record["target_path"]) == pre]
        found.pre_target = [
            record
            for record in edge_targets
            if (lower(record["source_cluster_uuid"]), record["source_path"], record["target_path"]) == (self.target_uuid, *pre[::2])
        ]
        post = (self.source_path, self.target_uuid, self.target_path)
        found.post_source = [record for record in edge_sources if (record["source_path"], lower(record["target_cluster_uuid"]), record["target_path"]) == post]
        found.post_target = [
            record
            for record in target_targets
            if (lower(record["source_cluster_uuid"]), record["source_path"], record["target_path"]) == (self.edge_uuid, self.source_path, self.target_path)
        ]
        self.lists = {"edge": (edge_sources, edge_targets), "target": (target_sources, target_targets)}
        return found

    def _on(self, cluster, relationship_id):
        """R's record on a cluster by id, in either orientation, and which: V(pre) or V(post); (None, None) when absent in both."""
        sources, targets = self.lists[cluster]
        pre, post = (targets, sources) if cluster == "edge" else (sources, targets)
        for orientation, records in (("post", post), ("pre", pre)):
            record = self.records.by_id(records, relationship_id)
            if record is not None:
                return record, orientation
        return None, None

    # Item 1: a stale record

    def _stale(self):
        edge_r = self._on("edge", self.r_id)[0]
        target_r = self._on("target", self.r_id)[0]
        if edge_r is None or target_r is None:
            where = " and ".join(name for name, record in ((self.edge, edge_r), (self.target_host, target_r)) if record is None)
            why = f"R {self.r_id} is missing on {where}"
        elif ENDED in (edge_r["state"], target_r["state"]):
            why = f"R {self.r_id} is ENDED"
        else:
            return None
        return f"{self.name}: removed the stale restore record {self.record_path}, since {why}, and an ENDED or missing R can never reconnect."

    # Item 2: an accepted reconnect

    def _reconnected(self):
        edge_r, edge_side = self._on("edge", self.r_id)
        target_r, target_side = self._on("target", self.r_id)
        states = (edge_r["state"], target_r["state"])
        marked = bool(self.record.get("reconnected"))
        if not marked and any(state in RECONNECTED for state in states):
            marked = True
            self.record = {**self.record, "reconnected": True}
            self.pre_actions.append({"action": "mark_record", "path": self.record_path, "record": self.record})
        if not marked:
            return None
        declared = edge_side == "post" and target_side == "post"
        if declared and states == (ESTABLISHED, ESTABLISHED):
            return self._step_9(edge_r)
        if declared and any(state in RECONNECTED for state in states) and all(state in RECONNECTED | LAGGING for state in states):
            return self.step(8, [self._poll_established()], past_delete=True, wait_only=True)
        if edge_side == "pre" and target_side == "pre" and any(state in RECONNECTED for state in states):
            note = (
                f"R {self.r_id} is live from {self.target_host} to {self.edge} again, as after a reverse and a reconnect by hand, so the restore "
                f"record {self.record_path} no longer describes it; that is a failover, which the replication_failover role owns. Remove the record once the "
                "relationship is as you want it."
            )
            return self._refused_with_record(note)
        note = (
            f"The restore record {self.record_path} is marked reconnected, and R {self.r_id} is {edge_r['state']} on {self.edge} and "
            f"{target_r['state']} on {self.target_host}: a failover by hand, or a run stopped between the mark and the reconnect. A marked "
            f"record never sends a reconnect. After checking that nobody has written on {self.target_host}:{self.target_path}, reconnect R "
            f"{self.r_id} by id with qumulo.cluster.replication_reverse and allow_data_loss, then remove the record."
        )
        return self._refused_with_record(note)

    def _refused_with_record(self, note):
        """Not applicable with a restore record: refused with its note, named or not, since the operator's restore touched it."""
        note = note.rstrip(".")
        return self.outcome(REFUSED, f"{note}.", note=note)

    # Item 3: done

    def _done(self):
        declared = [record for record in self.records.post_source if record["state"] == ESTABLISHED]
        if not declared:
            return None
        self.r_id = str(declared[0]["id"])
        actions = []
        if self.claimed and self.report is not None:
            actions.append(self._write_stamp())
        return self.outcome(DONE, actions=actions, past_delete=True, sequence=[])

    def _write_stamp(self):
        return {"action": "write_stamp", "path": self.stamp_path, "stamp": stamp(self.key, self.r_id, self.report, iso(self.now))}

    # Item 4: a restore in progress

    def _pair(self):
        """R's pair, before or after the reverse (Terms), as (orientation, edge record, target record), or None."""
        found = self.records
        if self.record is not None:
            edge_r, edge_side = self._on("edge", self.r_id)
            target_r, target_side = self._on("target", self.r_id)
            reversed_pair = edge_side == "post" and target_side == "post" and PENDING not in (edge_r["state"], target_r["state"])
            # While X exists nothing else can authorize at the target path, so the pair needs no guard then.
            if reversed_pair and (found.x is not None or self._guards_hold(target_r)):
                return ("post", edge_r, target_r)
            return None
        for target_r in found.pre_source:
            edge_r = found.by_id(found.pre_target, target_r["id"])
            if edge_r is not None and ENDED not in (edge_r["state"], target_r["state"]):
                self.r_id = str(target_r["id"])
                return ("pre", edge_r, target_r)
        if found.x is not None:
            for edge_r in found.post_source:
                target_r = found.by_id(found.post_target, edge_r["id"])
                if target_r is not None and not ({ENDED, PENDING} & {edge_r["state"], target_r["state"]}):
                    self.r_id = str(edge_r["id"])
                    return ("post", edge_r, target_r)
        return None

    def _guards_hold(self, target_r):
        """An unmarked record's two guards (plan, the restore record); each one that fails is kept, with its note.

        The server replaces R's recovery-point snapshot on the target at every reconnect and every make-writable there, and
        at every job that takes a new recovery point, so its id moving shows a reconnect sent elsewhere before any job.
        """
        record = self.record
        self.failed_guards = []
        point = target_r.get("recovery_point")
        if point != record["recovery_point"]:
            self.failed_guards.append(
                f"R {self.r_id} on {self.target_host} reads recovery point {point or 'none'}, not {record['recovery_point'] or 'none'}, so a job "
                "has completed there since the restore record was written, after a reconnect by hand or from another controller. A failover "
                f"by hand belongs to the replication_failover role. Remove the restore record {self.record_path}."
            )
        now_id, then_id = _int_or_none(target_r.get("recovery_point_snapshot_id")), _int_or_none(record["recovery_point_snapshot_id"])
        if now_id != then_id:
            self.failed_guards.append(
                f"R {self.r_id} on {self.target_host} holds recovery-point snapshot {_or_none(now_id)}, not {_or_none(then_id)}, so R was reconnected or "
                f"made writable on {self.target_host} since the restore record was written, by hand or from another controller. A failover by "
                f"hand belongs to the replication_failover role. Remove the restore record {self.record_path}."
            )
        written = utc(record["written_at"], "the record's written_at")
        window = self.params["resume_window"]
        if self.now - written > timedelta(seconds=window):
            self.failed_guards.append(
                f"The restore record {self.record_path} is not marked, and it was written at {record['written_at']}, more than "
                f"replication_restore_resume_window ({window} seconds) ago, too long ago to tell whether R {self.r_id} was reconnected elsewhere, "
                f"or failed over on {self.target_host}, since. A failover by hand belongs to the replication_failover role. Remove the restore record "
                f"{self.record_path}. For your own restore stopped past the resume window, check that nobody has written on "
                f"{self.target_host}:{self.target_path}, reconnect R {self.r_id} by id with qumulo.cluster.replication_reverse and "
                "allow_data_loss, then remove the record."
            )
        return not self.failed_guards

    def _in_progress(self, pair):
        orientation, edge_r, target_r = pair
        x = self.records.x
        if orientation == "pre":
            if x is None:
                stop = (
                    f"the old record that held {self.target_host}:{self.target_path} is gone, deleted by hand before the reverse, so the restore "
                    f"cannot go on; R {self.r_id} may also be a hand reversal of the declared relationship, which the replication_failover role owns"
                )
                return self._stop(stop, self._exit_before_reverse(edge_r))
            if x["state"] != ESTABLISHED:
                return self._stop(self._x_in_use(x), self._exit_before_reverse(edge_r))
            return self._pre_reverse_step(edge_r)
        if x is not None:
            if x["state"] != ESTABLISHED:
                return self._stop(self._x_in_use(x), self._exit_steps_4_to_6())
            if (edge_r["state"], target_r["state"]) != (DISCONNECTED, DISCONNECTED):
                stop = (
                    f"R {self.r_id} is {edge_r['state']} on {self.edge} and {target_r['state']} on {self.target_host}, where step 7 needs DISCONNECTED on both"
                )
                return self._stop(stop, self._exit_steps_4_to_6())
            return self._step_7(target_r)
        if (edge_r["state"], target_r["state"]) != (DISCONNECTED, DISCONNECTED):
            stop = f"R {self.r_id} is {edge_r['state']} on {self.edge} and {target_r['state']} on {self.target_host}, where step 8 needs DISCONNECTED on both"
            return self._stop(stop, self._exit_after_delete(), past_delete=True)
        return self._step_8(edge_r)

    def _pre_reverse_step(self, edge_r):
        state = edge_r["state"]
        if state == PENDING:
            return self._step_3()
        if state == ESTABLISHED and not edge_r.get("recovery_point"):
            return self.step(4, [{"action": "wait_for_recovery_point", "relationship_id": self.r_id}], deadline=self._deadline())
        if state == ESTABLISHED:
            return self._step_5(make_writable=True)
        if state == DISCONNECTING:
            return self._step_5(make_writable=False)
        if state == DISCONNECTED:
            return self._step_6()
        stop = f"R {self.r_id} is {state} on {self.edge}, which no step of the restore leaves before the reverse"
        return self._stop(stop, self._exit_before_reverse(edge_r))

    def _deadline(self):
        started = utc(self.params["wait_started"], "wait_started") if self.params["wait_started"] else self.now
        return iso(started + timedelta(seconds=self.params["wait_timeout"]))

    def _x_in_use(self, x):
        return (
            f"the replica {self.target_host}:{self.target_path} is in use: its old record {x['id']} is {x['state']}, not ESTABLISHED, as when an "
            "operator made it writable; the restore never deletes it or reconnects after it"
        )

    def _stop(self, text, exit_text, **found):
        message = f"{text}. {exit_text}. {self.resume} resumes it."
        return self.outcome(STOPPED, message, **found)

    # The abandon exits, by where the restore stopped (plan, Abandon)

    def _exit_before_reverse(self, edge_r):
        if edge_r.get("recovery_point"):
            return self._exit_steps_4_to_6()
        return exit_before_recovery_point(self.r_id, self.edge, self.source_path, self.target_host, self.target_path)

    def _exit_steps_4_to_6(self):
        return (
            f"To abandon it, delete R {self.r_id} on both {self.target_host} and {self.edge}; {self.edge} keeps a complete copy and anything "
            f"written since, and {self.target_host}:{self.target_path} keeps the replica"
        )

    def _exit_after_delete(self):
        return (
            "The way out is to finish the restore: deleting R and the record now would leave two writable copies, and the declared relationship "
            "would then need allow_non_empty_target and replace the target's copy with the edge's"
        )

    # The steps

    def _step_3(self):
        options = {
            "source_cluster_uuid": self.target_uuid,
            "source_path": self.target_path,
            "target_path": self.source_path,
            "relationship_id": self.r_id,
            "allow_directories_only": True,
            "wait": True,
        }
        return self.step(3, [{"action": "authorize", "cluster": "edge", "options": options}])

    def _step_5(self, make_writable):
        actions = []
        if make_writable:
            actions.append({"action": "make_writable", "cluster": "edge", "relationship_id": self.r_id, "allow_data_loss": True})
        timeout = self.params["reverse_timeout"]
        text = f"R {self.r_id} did not read DISCONNECTED on {self.edge} within {timeout} seconds of the make-writable"
        actions.append(
            {
                "action": "poll",
                "polls": [{"cluster": "edge", "side": "target", "relationship_id": self.r_id, "until": [DISCONNECTED]}],
                "timeout": timeout,
                "timeout_message": f"{text}. {self._exit_steps_4_to_6()}. {self.resume} resumes it.",
            }
        )
        return self.step(5, actions)

    def _step_6(self):
        address, port = self.params["target_replication_address"], self.params["target_replication_port"]
        options = {"relationship_id": self.r_id, "address": address, "port": port}
        stop = (
            f"{self.edge} cannot reach {self.target_host} at {address}:{port}, the target's qumulo_replication_address and port, so the "
            f"reverse of R {self.r_id} failed. {self.edge}:{self.source_path} holds the replica's content and is writable, and the old record "
            f"{self.records.x['id']} still locks {self.target_host}:{self.target_path}. Fix the path from {self.edge} to {self.target_host}, or "
            f"{self.target_host}'s qumulo_replication_address"
        )
        reverse = {
            "action": "reverse",
            "cluster": "edge",
            "options": options,
            # Research R19: the server refuses a reverse it cannot send with this class, and changes neither record.
            "stop_on": CONNECTION_ERROR,
            "stop_message": f"{stop}. {self._exit_steps_4_to_6()}. {self.resume} resumes it.",
        }
        return self.step(6, [reverse])

    def _step_7(self, target_r):
        if not self.params["artifact_dir"]:
            stop = "edge_rebuild_artifact_dir is empty, so the restore cannot write the record it needs before it deletes the old record"
            return self._stop(stop, self._exit_steps_4_to_6())
        x = self.records.x
        x_point_snapshot = _int_or_none(x.get("recovery_point_snapshot_id"))
        record = {
            "format": 1,
            "key": self.key,
            "r_id": self.r_id,
            "x_id": str(x["id"]),
            "x_source_cluster_uuid": lower(x["source_cluster_uuid"]),
            "x_source_path": x["source_path"],
            "x_snapshot_ids": [] if x_point_snapshot is None else [x_point_snapshot],
            # The first reading, kept when the read after the delete fails (compose_record()).
            "recovery_point": target_r.get("recovery_point"),
            "recovery_point_snapshot_id": _int_or_none(target_r.get("recovery_point_snapshot_id")),
            "written_at": None,
        }
        writable = f"the record directory {self.directory} is not writable, so the restore cannot write the record it needs before it deletes the old record"
        actions = [
            {
                "action": "ensure_record_directory",
                "path": self.directory,
                "mode": "0700",
                "stop_message": f"{writable}. {self._exit_steps_4_to_6()}. {self.resume} resumes it.",
            },
            {
                "action": "delete_x",
                "cluster": "target",
                "options": {
                    "source_cluster_uuid": lower(x["source_cluster_uuid"]),
                    "source_path": x["source_path"],
                    "target_path": self.target_path,
                    "state": "absent",
                },
            },
            {"action": "read_after_delete", "cluster": "target", "relationship_id": self.r_id, "side": "target"},
            {"action": "write_record", "path": self.record_path, "record": record},
        ]
        return self.step(7, actions)

    def _step_8(self, edge_r):
        unmarked = dict(self.record)
        marked = {**unmarked, "reconnected": True}
        refusal = f"Run the restore again: {self.resume}. A reconnect by hand must be followed by removing the restore record {self.record_path}."
        actions = [
            {"action": "mark_record", "path": self.record_path, "record": marked},
            {
                "action": "reconnect",
                "cluster": "target",
                "options": {"relationship_id": self.r_id, "allow_data_loss": True, "wait": False},
                # A definite refusal with R still DISCONNECTED clears the mark; a lost answer keeps it.
                "on_refusal": {"clear_mark_when": DISCONNECTED, "path": self.record_path, "record": unmarked, "way_on": refusal},
            },
            self._poll_established(edge_r),
        ]
        return self.step(8, actions, past_delete=True)

    def _poll_established(self, edge_r=None):
        if edge_r is None:
            edge_r = self._on("edge", self.r_id)[0]
        timeout = self.params["reconnect_timeout"]
        address, port = edge_r.get("target_address"), edge_r.get("target_port")
        text = (
            f"R {self.r_id} did not read ESTABLISHED on both {self.edge} and {self.target_host} within {timeout} seconds of the reconnect. The "
            f"likeliest cause is that {self.edge} cannot reach {self.target_host} at {address}:{port}, the address and port R's source record on "
            f"{self.edge} uses; check that path. The restore record {self.record_path} stays marked, so the next run only polls"
        )
        return {
            "action": "poll",
            "polls": [
                {"cluster": "edge", "side": "source", "relationship_id": self.r_id, "until": [ESTABLISHED]},
                {"cluster": "target", "side": "target", "relationship_id": self.r_id, "until": [ESTABLISHED]},
            ],
            "timeout": timeout,
            "timeout_message": f"{text}. {self.resume} resumes it.",
        }

    def _step_9(self, edge_r):
        entry = self.params["relationship"]
        options = {
            "source_path": self.source_path,
            "target_cluster_uuid": self.target_uuid,
            "target_path": self.target_path,
            "target_address": self.params["target_replication_address"],
            "target_port": self.params["target_replication_port"],
            "relationship_id": self.r_id,
            **{key: entry[key] for key in SETTINGS if key in entry},
        }
        actions = [
            {"action": "apply_settings", "cluster": "edge", "options": options},
            self._write_stamp(),
            {"action": "remove_record", "path": self.record_path},
        ]
        # The record goes with step 9, and with it the only list of what X left (G-REPL-SNAPSHOT).
        left = [{"host": self.target_host, "id": snapshot_id} for snapshot_id in self.record["x_snapshot_ids"]]
        return self.step(9, actions, past_delete=True, summary=self._summary(), snapshots_left=left)

    def _summary(self):
        line = f"{self.name}: restored from {self.target_host}, and the relationship runs from {self.edge_uuid}."
        found = self.params["subdirectories"] or []
        replaced = [canonical_path(entry["path"]) for entry in found if entry.get("exists")]
        deleted = [canonical_path(entry["path"]) for entry in found if not entry.get("exists")]
        if not found:
            return line
        baseline = f"ansible-playbook qumulo.cluster.baseline -i {self.params['inventory']} -l {self.edge}"
        lead = f" Run {baseline} to put back what the restore's first job replaced: "
        if not deleted:
            return f"{line}{lead}{', '.join(replaced)}."
        if not replaced:
            return f"{line}{lead}{', '.join(deleted)}, missing now."
        return f"{line}{lead}{', '.join(replaced)}, and {', '.join(deleted)}, missing now."

    # Item 5: step 1 or step 2

    def _step_1_or_2(self):
        needed = self._missing_inputs()
        if needed:
            return self.result("read", reads=needed, check_options=self._check_options())
        refusals = self._refusals()
        self._warn_on_x()
        if refusals:
            return self.outcome(REFUSED, " ".join(refusals))
        missing = self._missing_directories()
        if missing:
            actions = [{"action": "create_directory", "cluster": "edge", "path": path} for path in missing]
            actions.append({"action": "ensure_record_directory", "path": self.directory, "mode": "0700"})
            return self.step(1, actions)
        options = self._check_options()["relationship"]
        hand_off = (
            f"{self.target_host} cannot reach {self.edge} at {options['target_address']}:{options['target_port']}, its "
            f"qumulo_replication_address and qumulo_replication_port, so R's create failed, and nothing was written on either cluster. "
            f"Either set qumulo_replication_address to an address of {self.edge} that {self.target_host} reaches, and run {self.entry_point} "
            f"again; or copy the replica from {self.target_host}:{self.target_path} into {self.edge}:{self.source_path} over a protocol, then run "
            f"ansible-playbook qumulo.cluster.baseline -i {self.params['inventory']} -l {self.edge} with allow_non_empty_target on the entry "
            "and the release its held refusal prints."
        )
        create = {"action": "create_relationship", "cluster": "target", "options": options, "stop_on": CONNECTION_ERROR, "stop_message": hand_off}
        return self.step(2, [create])

    def _check_options(self):
        """The options of R's create on the target and of its authorize on the edge, for the check passes and steps 2 and 3."""
        return {
            "relationship": {
                "source_path": self.target_path,
                "target_cluster_uuid": self.edge_uuid,
                "target_path": self.source_path,
                "target_address": self.params["replication_address"],
                "target_port": self.params["replication_port"],
                "replication_enabled": True,
                "blackout_windows": [],
            },
            "target": {
                "source_cluster_uuid": self.target_uuid,
                "source_path": self.target_path,
                "target_path": self.source_path,
                "source_creates": True,
                "allow_directories_only": True,
                # Pending counts only for a missing directory, which step 1 creates before the authorize.
                "directory_pending": True,
                "strict_capacity": False,
            },
        }

    def _missing_inputs(self):
        params = self.params
        needed = []
        if params["source_directory"] is None:
            needed.append("source_directory")
        if params["relationship_check"] is None:
            needed.append("relationship_check")
        if params["target_check"] is None:
            needed.append("target_check")
        if self._expects_license() and params["license"] is None and params["predicted_license_pending"] is None:
            needed.append("license")
        if self._declared_clamp() is not None and params["clamp_plan"] is None and params["predicted_license_pending"] is None:
            needed.append("clamp_plan")
        if params["target_spokes"] is None:
            needed.append("target_spokes")
        if params["edge_spokes"] is None:
            needed.append("edge_spokes")
        if params["record_directory_writable"] is None:
            needed.append("record_directory_writable")
        return needed

    def _missing_directories(self):
        missing = {canonical_path(path) for path in (self.params["source_directory"] or {}).get("missing") or []}
        return [path for path in self.source_directories() if path in missing]

    def _refusals(self):
        x = self.records.x
        refusals = []
        if x["state"] != ESTABLISHED or not x.get("target_root_read_only"):
            how = x["state"] if x["state"] != ESTABLISHED else "ESTABLISHED, but the directory is not read-only"
            refusals.append(
                f"{self.target_host}:{self.target_path} is writable: its old record {x['id']} is {how}. The restore's reconnect reverts the "
                "directory to R's last recovery point, so writes made there after it would be lost. Restore by the copy and the release."
            )
        for record in self.records.ended_beside_x:
            refusals.append(
                f"ENDED record {record['id']} from {record['source_cluster_uuid']}:{record['source_path']} lies at {self.target_host}:"
                f"{self.target_path} beside the old record {x['id']} (O5), so the directory may hold writes made since it was released. "
                "Restore by the copy and the release."
            )
        if not x.get("recovery_point"):
            refusals.append(
                f"The old record {x['id']} at {self.target_host}:{self.target_path} has no recovery point, so it holds no complete replica to "
                "restore. Restore by the copy and the release."
            )
        refusals.extend(self._directory_refusals())
        refusals.extend(self._address_refusals())
        if not self.params["artifact_dir"]:
            refusals.append("edge_rebuild_artifact_dir is empty, so no restore record could be written. Set it to a directory on the controller.")
        elif not self.params["record_directory_writable"]:
            refusals.append(
                f"The record directory {self.directory} cannot be created or written. Make {self.params['artifact_dir']} writable, or run from "
                "a controller where it is."
            )
        refusals.extend(self._license_refusals())
        refusals.extend(self._spoke_refusals())
        refused_ids = set()
        refusals.extend(self._relationship_refusals(refused_ids))
        leftovers = self._leftovers()
        if leftovers:
            refused_ids |= {str(record["id"]) for _host, record in leftovers}
            named = ", ".join(f"record {record['id']} on {host} ({record['state']})" for host, record in leftovers)
            refusals.append(
                f"R's leftover records lie at the restore's paths: {named}. Delete them, on {self.target_host} first and then on {self.edge}, "
                "and run the restore again."
            )
        refusals.extend(self._check_refusals(refused_ids))
        return refusals

    def _address_refusals(self):
        """The edge's address, at which the target reaches it for the restore, and the target's, which step 6's reverse sends."""
        refusals = []
        address = self.params["replication_address"]
        if not address:
            refusals.append(
                f"{self.edge} has no qumulo_replication_address, and the target reaches the edge at it. Set qumulo_replication_address to an IP "
                f"address of {self.edge}."
            )
        elif not is_ip_literal(address):
            refusals.append(
                f"{self.edge}'s qumulo_replication_address, {address}, is not an IP literal, and the target reaches the edge at it. Set "
                f"qumulo_replication_address to an IP address of {self.edge}."
            )
        target_address = self.params["target_replication_address"]
        how = "has no qumulo_replication_address" if not target_address else f"has qumulo_replication_address {target_address}, not an IP literal"
        if not is_ip_literal(target_address):
            refusals.append(
                f"{self.target_host} {how}, and step 6's reverse sends {self.edge} the address at which it reaches {self.target_host}. Set "
                f"qumulo_replication_address to an IP address of {self.target_host}."
            )
        return refusals

    def _directory_refusals(self):
        first = (self.params["source_directory"] or {}).get("first_object")
        if not first:
            return []
        siblings = [
            {**entry, "source_path": canonical_path(entry["source_path"]), "target_path": canonical_path(entry["target_path"])}
            for entry in self.params["siblings"] or []
            if entry.get("past_delete") and self._overlaps_sibling(entry)
        ]
        outer = next((entry for entry in siblings if inside(self.source_path, entry["source_path"])), None)
        if outer is not None:
            return [release_after_sibling(self.edge, self.params["inventory"], self.source_path, self.target_host, self.target_path, outer)]
        sibling = siblings[0] if siblings else None
        if sibling is not None:
            # A sibling restored inside this directory holds only part of it, so this replica's other files are copied in around it.
            other = f"{self.edge}:{sibling['source_path']} to {sibling['target_host']}:{sibling['target_path']}"
            return [
                (
                    f"{self.edge}:{self.source_path} holds {first}, since {other} was restored into it first. To keep that data, copy the replica "
                    f"from {self.target_host}:{self.target_path} over a protocol into {self.edge}:{self.source_path}, around {other}'s data, then run "
                    f"ansible-playbook qumulo.cluster.baseline -i {self.params['inventory']} -l {self.edge} with allow_non_empty_target on the entry "
                    "and the release its held refusal prints."
                )
            ]
        return [
            (
                f"{self.edge}:{self.source_path} holds {first}, so the restore cannot fill it from {self.target_host}:{self.target_path} without "
                f"replacing it. Files written on {self.edge} since the replay are the likeliest cause, and they may be the only copy of themselves. "
                f"Either move them out of {self.source_path}, run the restore again, and move them back once the relationship runs from {self.edge}; "
                f"or keep them, copy the replica in around them over a protocol, and run ansible-playbook qumulo.cluster.baseline -i "
                f"{self.params['inventory']} -l {self.edge} with allow_non_empty_target on the entry and the release its held refusal prints."
            )
        ]

    def _overlaps_sibling(self, sibling):
        return overlaps(canonical_path(sibling["source_path"]), self.source_path)

    # The license rule (plan, Restore, step 1's preconditions) and the capacity gate's clamp half

    def _capacity(self):
        return self.params["capacity"] or {}

    def _expects_license(self):
        return bool(self.params["in_edges"]) and self._capacity().get("license") != "none"

    def _declared_clamp(self):
        return self._capacity().get("clamp")

    def _license_refusals(self):
        params = self.params
        refusals = []
        predicted = params["predicted_license_pending"]
        # The clamp half applies with a license active or under license: none (spec User Story 8, scenario 2).
        active = True
        if self._expects_license():
            active = not predicted if predicted is not None else (params["license"] or {}).get("license_status") == "active"
            if not active:
                refusals.append(
                    f"{self.edge} holds no active license, and it expects one, so the restore would start a full sync of {self.target_host}:"
                    f"{self.target_path} into a cluster held to its unlicensed capacity. Once the license applies, by the license file from "
                    "Qumulo support in qumulo_capacity.license_document or by Qumulo issuing it to the cluster, run ansible-playbook "
                    f"qumulo.cluster.edge_license_complete -i {params['inventory']} -l {self.edge}, which restores it after the license and the "
                    f"clamp. If {self.edge} runs without a license, add license: none to {self.edge}'s qumulo_capacity, keeping its other keys, "
                    "such as clamp and free_threshold."
                )
        if self._declared_clamp() is not None and predicted is None and active:
            plan = params["clamp_plan"] or {}
            if plan.get("failed") or "msg" in plan:
                refusals.append(f"{self.edge}'s clamp plan failed{_reason(plan.get('msg'))}. Fix the cause and run {self.entry_point} again.")
            elif plan.get("changed"):
                refusals.append(
                    f"{self.edge}'s clamp differs from qumulo_capacity.clamp. Run ansible-playbook qumulo.cluster.edge_license_complete -i "
                    f"{params['inventory']} -l {self.edge}, which sets the clamp, then the restore."
                )
        return refusals

    # Scenario 14: portals and other relationships

    def _spoke_refusals(self):
        refusals = []
        for root in _spoke_roots(self.params["target_spokes"]):
            how = _nesting(self.target_path, root)
            if how:
                refusals.append(
                    f"{self.target_host}:{self.target_path} {how} the portal spoke root {root}, whose content is cached hub data, so the restore "
                    "cannot copy it as the replica. Restore by the copy and the release."
                )
        for root in _spoke_roots(self.params["edge_spokes"]):
            how = _nesting(self.source_path, root)
            if how:
                refusals.append(
                    f"{self.edge}:{self.source_path} {how} the portal spoke root {root}; the restore cannot fill a directory a portal serves. Move "
                    "the relationship's source path off the portal in the inventory, or restore by the copy and the release, copying around "
                    f"{root}, which the portal serves from the hub."
                )
        return refusals

    def _host_of(self, uuid):
        hosts = self.params["inventory_cluster_uuids"] or {}
        return next((host for host, each in sorted(hosts.items()) if lower(each) == lower(uuid)), uuid)

    def _relationship_refusals(self, refused_ids):
        edge_sources, edge_targets = self.lists["edge"]
        own = {str(record["id"]) for record in self.records.post_source + self.records.pre_target}
        refusals = []
        for record in edge_sources:
            if record["state"] == ENDED or str(record["id"]) in own:
                continue
            if overlaps(record["source_path"], self.source_path):
                how = "the same directory as" if record["source_path"] == self.source_path else "nested with"
                refused_ids.add(str(record["id"]))
                refusals.append(
                    f"{self.edge}:{record['source_path']} to {self._host_of(record['target_cluster_uuid'])}:{record['target_path']} is {how} "
                    f"{self.source_path}, and the restore would make it read-only and replace its content. If the replay created it from the empty "
                    "directory, delete it on both clusters, run the restore again, and the next baseline creates it again from the restored data; "
                    "otherwise restore by the copy and the release."
                )
        for record in edge_targets:
            if record["state"] == ENDED or str(record["id"]) in own:
                continue
            if overlaps(record["target_path"], self.source_path):
                refused_ids.add(str(record["id"]))
                refusals.append(
                    f"{self._host_of(record['source_cluster_uuid'])}:{record['source_path']} to {self.edge}:{record['target_path']} replicates into "
                    f"{self.source_path}, which the restore fills. Move one of the two paths in the inventory, or restore by the copy and the release."
                )
        return refusals

    def _leftovers(self):
        """Every record with R's keys while R has no pair (Terms, a leftover), as (host, record).

        A pending record on the edge with no source record on the target is the trace of a create that failed halfway,
        which step 2 runs again and step 3's real pass prunes, so it is none.
        """
        found = self.records
        on_target = {str(record["id"]) for record in found.pre_source + found.post_target}
        on_edge = {str(record["id"]) for record in found.pre_target + found.post_source}
        leftovers = []
        for host, records, other in (
            (self.target_host, found.pre_source, on_edge),
            (self.target_host, found.post_target, on_edge),
            (self.edge, found.pre_target, on_target),
            (self.edge, found.post_source, on_target),
        ):
            for record in records:
                one_sided = str(record["id"]) not in other
                if record in found.pre_target and record["state"] == PENDING and one_sided:
                    continue
                reversed_pending = record in found.post_source + found.post_target and record["state"] == PENDING
                if record["state"] == ENDED or one_sided or reversed_pending:
                    leftovers.append((host, record))
        return leftovers

    def _check_refusals(self, refused_ids):
        # The holds-a-file refusal names the first object the target check's O1 entry reports.
        named_o1 = bool((self.params["source_directory"] or {}).get("first_object"))
        refusals = []
        for check, cluster, module in (("relationship_check", self.target_host, "replication_relationship"), ("target_check", self.edge, "replication_target")):
            result = self.params[check] or {}
            entries = [
                entry
                for entry in blocking(result.get("unmet") or [])
                # An ENDED R source record would read as O3, whose consent the restore refuses; the leftover names it instead, and the
                # relationship refusals above name what O7 and O9 would.
                if entry["row"] != "O3" and not (entry["row"] == "O1" and named_o1) and str((entry.get("facts") or {}).get("id")) not in refused_ids
            ]
            if entries:
                refusals.append(f"The check-mode run of {module} on {cluster} for R reports: " + "; ".join(entry["message"] for entry in entries) + ".")
            elif (result.get("failed") or "msg" in result) and not blocking(result.get("unmet") or []):
                refusals.append(f"The check-mode run of {module} on {cluster} for R failed{_reason(result.get('msg'))}.")
        return refusals

    def _warn_on_x(self):
        x = self.records.x
        running = x.get("job_state") == "REPLICATION_RUNNING"
        error = x.get("error_from_last_job")
        if not running and not error:
            return
        shows = f"a running job, started at {x.get('job_start_time') or 'an unknown time'}" if running else f"the error {error!r}"
        owned = x.get("recovery_point_snapshot_id")
        snapshot = f"its recovery-point snapshot {owned}" if owned is not None else "its recovery-point snapshot"
        self.warnings.append(
            f"{self.name}: the old record {x['id']} shows {shows}, so {self.target_host}:{self.target_path} may hold a partial job; {snapshot}, "
            f"at recovery point {x.get('recovery_point') or 'none'}, is the last consistent copy."
        )

    # Item 6: not applicable, and the outcomes past it

    def _not_applicable(self):
        found = self.records
        if found.foreign:
            return self._foreign(found.foreign[0])
        stranded = self._stranded()
        if stranded is not None:
            return stranded
        if self.record is not None:
            note = self._guard_note()
            return self._refused_with_record(note)
        if self.previous:
            held = self._held_without_x()
            if held is not None:
                return held
        note = (
            f"no old record from a previous UUID of {self.edge} holds {self.target_host}:{self.target_path}, so there is nothing to restore from. "
            f"To replicate from {self.edge}, copy the replica into {self.edge}:{self.source_path} over a protocol, then run ansible-playbook "
            f"qumulo.cluster.baseline -i {self.params['inventory']} -l {self.edge} with allow_non_empty_target on the entry and the release its "
            "held refusal prints"
        )
        return self._skipped_or_named(note)

    def _skipped_or_named(self, note):
        if self.params["named"]:
            return self.outcome(REFUSED, f"{note}.", note=note)
        return self.outcome(NOT_APPLICABLE, note=note)

    def _foreign(self, record):
        uuid = lower(record["source_cluster_uuid"])
        reason = (
            f"{uuid}'s record {record['id']} holds {self.target_path}. If {self.edge} was rebuilt, apply host_vars_patch from the rebuild report "
            f"to host_vars/{self.edge}.yml, or, if {self.edge} was re-formed outside edge_rebuild, add {uuid} to its qumulo_previous_cluster_uuids, "
            "and run the second pass again; otherwise that record belongs to another cluster, and the baseline refuses the relationship (O2): "
            "end that cluster's relationship, or move the target path in the inventory."
        )
        if self.previous:
            return self.outcome(NOT_RESTORED, reason)
        self.warnings.append(f"{self.name}: {reason}")
        return self.outcome(NOT_APPLICABLE, note=reason.rstrip("."))

    def _stranded(self):
        """A DISCONNECTED pair of the declared key with no X and no restore record, or one reconnecting with no record."""
        found = self.records
        if self.record is not None or found.x is not None:
            return None
        for edge_r in found.post_source:
            target_r = found.by_id(found.post_target, edge_r["id"])
            if target_r is None:
                continue
            states = {edge_r["state"], target_r["state"]}
            self.r_id = str(edge_r["id"])
            if states & {RECONNECTING, REESTABLISHING} and states <= RECONNECTED | LAGGING:
                note = (
                    f"R {self.r_id} is {edge_r['state']} on {self.edge} and {target_r['state']} on {self.target_host}, with no restore record "
                    f"under {self.directory or '<edge_rebuild_artifact_dir>'}: another controller's restore in its reconnect, or a reconnect by "
                    "hand. Wait for it, or run the restore from the controller that holds the record"
                )
                return self._skipped_or_named(note)
            if states == {DISCONNECTED}:
                return self._stranded_pair()
            self.r_id = None
        return None

    def _stranded_pair(self):
        note = (
            f"R {self.r_id}, the declared relationship's records on {self.edge} and {self.target_host}, is DISCONNECTED on both, with no old "
            f"record and no restore record at {self.record_path or '<edge_rebuild_artifact_dir>'}. It reads as a failover by hand, which the "
            "replication_failover role owns. It is also what a crash between the old record's delete and the restore record's write leaves, or a resume from a "
            "controller without the record, or a hand delete of the old record after the reverse. If it is your own stopped restore, do one "
            "of these: (1) move the record here from the controller that wrote it, deleting it there, and run again; (2) check that nobody "
            f"has written on {self.target_host}:{self.target_path}, since the reconnect reverts it, reconnect R {self.r_id} by id with "
            "qumulo.cluster.replication_reverse and allow_data_loss, then remove any record of it on the controller that wrote it, or run "
            "the restore there once while R is ESTABLISHED, which removes the record; the next baseline applies the declared settings, and "
            "qumulo.cluster.snapshot_info lists the ownerless snapshots the record named; (3) if the old record was deleted by hand before "
            f"step 7, delete R {self.r_id} on both clusters"
        )
        if self.params["named"]:
            return self.outcome(REFUSED, f"{note}.", note=note)
        if self.params["entry_point"] == "second_pass" and self.restore_to_do:
            return self.outcome(NOT_RESTORED, f"{note}.", note=note)
        self.warnings.append(f"{self.name}: {note}.")
        self._no_report_warning()
        return self.outcome(NOT_APPLICABLE, note=note)

    def _guard_note(self):
        if self.failed_guards:
            return " ".join(self.failed_guards)
        return (
            f"The restore record {self.record_path} is not marked, and R {self.r_id} is not DISCONNECTED on both sides in the declared "
            f"relationship's orientation, so R may have been reconnected elsewhere, or failed over on {self.target_host}, since the record was "
            "written. A failover by hand belongs to the replication_failover role. Remove the restore record once the relationship is as you want it"
        )

    def _held_without_x(self):
        """A relationship with no X, no pair of R and no record on an edge with previous UUIDs: step 21's own check pass decides."""
        check = self.params["step21"]
        if check is None:
            return self.result("read", reads=["step21"])
        status = check.get("status")
        if status == "held":
            return self.outcome(NOT_RESTORED, check.get("message") or "")
        if status == "refused":
            return self.outcome(REFUSED_BY_STEP_21, check.get("message") or "")
        return None


def _sentence(text):
    return text if text.endswith(".") else f"{text}."


def _reason(msg):
    """A failure's message as the tail of "... failed<tail>."."""
    return f": {msg.rstrip('.')}" if msg else " with no message"


def _int_or_none(value):
    return None if value is None else int(value)


def _or_none(value):
    return "none" if value is None else value


def _same_key(a, b):
    return (a["source_path"], lower(a["target_cluster_uuid"]), a["target_path"]) == (b["source_path"], lower(b["target_cluster_uuid"]), b["target_path"])


def _spoke_roots(spokes):
    return sorted({canonical_path(root["spoke_root"]) for spoke in spokes or [] for root in spoke.get("roots") or [] if root.get("spoke_root")})


def choose(params, check_mode):
    """The selection rule's answer for one relationship (plan, Restore, the selection rule)."""
    return Restore(params, check_mode).choose()


def round_check(params):
    """One waiting relationship against a round's read of the edge's target list (plan, Phases): landed, stopped or waiting.

    The deadline is checked last, so a relationship that landed while another's steps ran is never stopped.
    """
    edge = params["edge"]
    waiting = params["waiting"]
    relationship_id = str(waiting["relationship_id"])
    now = utc(params["now"], "now") if params["now"] else datetime.now(UTC)
    deadline = utc(waiting["wait_started"], "waiting.wait_started") + timedelta(seconds=params["wait_timeout"])
    # The round's read failed when the role passes no list; a read that succeeded and lacks R shows R deleted.
    read = params["edge_relationships"]
    targets = (read or {}).get("target_relationships") or []
    record = next((each for each in targets if str(each["id"]) == relationship_id), None)
    resume = entry_point_command("playbook", params["inventory"], edge)
    entry = params["relationship"]
    target_host = entry["target"]
    source_path = canonical_path(entry["source_path"], "relationship.source_path")
    again = f"run {entry_point_command(params['entry_point'], params['inventory'], edge)} again"
    found = {"deadline": iso(deadline), "relationship_id": relationship_id}
    if record is not None and record.get("recovery_point"):
        return {"decision": "landed", "outcome": None, "fails": False, "message": "", **found}
    if record is not None and record["state"] == ENDED:
        message = (
            f"R {relationship_id} ended with no recovery point ({record.get('end_reason') or 'no reason given'}). Delete R on {target_host} and "
            f"then on {edge}, empty {edge}:{source_path}, which holds R's partial copy, and {again}."
        )
        return {"decision": "stopped", "outcome": STOPPED, "fails": True, "message": message, **found}
    if read is not None and record is None:
        message = (
            f"R {relationship_id} is missing on {edge}, deleted while it waited for its full sync. Delete R on {target_host} too, empty "
            f"{edge}:{source_path}, which may hold R's partial copy, and {again}."
        )
        return {"decision": "stopped", "outcome": STOPPED, "fails": True, "message": message, **found}
    if now >= deadline:
        state = f"{record.get('job_state') or 'no job state'}, last error {record.get('error_from_last_job') or 'none'}" if record else "not read"
        stop = f"R {relationship_id} had no recovery point by {iso(deadline)} ({state})"
        exit_text = exit_before_recovery_point(
            relationship_id, edge, source_path, target_host, canonical_path(entry["target_path"], "relationship.target_path")
        )
        message = f"{stop}. {exit_text}. {resume} resumes it."
        return {"decision": "stopped", "outcome": STOPPED, "fails": True, "message": message, **found}
    return {"decision": "wait", "outcome": None, "fails": False, "message": "", **found}


def compose_record(params):
    """Step 7's restore record from the read after X's delete (plan, the restore record, Ordering).

    A failed read of R keeps the first recovery point and its snapshot's id, from the read that fed step 7's call, which can
    only make the guard stricter.
    """
    after = params["after_delete"]
    record = dict(after["record"])
    relationship = after.get("relationship")
    if relationship is not None:
        record["recovery_point"] = relationship.get("recovery_point")
        record["recovery_point_snapshot_id"] = _int_or_none(relationship.get("recovery_point_snapshot_id"))
    now = utc(params["now"], "now") if params["now"] else datetime.now(UTC)
    record["written_at"] = iso(now)
    return {"record": record}


# The run's choice of relationships (plan, The playbook; Restore, the restore play)


def pick(params):
    """Which present relationships out of the edge the run picks, and what the run refuses or reports before any step."""
    edge = params["edge"]
    entry_point = params["entry_point"]
    inventory = params["inventory"]
    previous = {lower(uuid) for uuid in params["previous_cluster_uuids"]}
    hosts = params["inventory_cluster_uuids"] or {}
    present = []
    for entry in params["relationships"] or []:
        if entry.get("state", "present") == "absent":
            continue
        present.append(
            {
                "source_path": canonical_path(entry["source_path"], "a relationship's source_path"),
                "target_host": entry["target"],
                "target_path": canonical_path(entry["target_path"], "a relationship's target_path"),
            }
        )
    names = params["names"]
    matched, unmatched = [], []
    if names is not None:
        wanted = set()
        for name in names:
            parsed = _parse_name(name)
            if parsed is None and entry_point == "playbook":
                raise QumuloError(malformed_name(name))
            # The second pass prints the same sentence once for the run, so here the name only matches nothing.
            if parsed is not None and any((each["target_host"], each["target_path"]) == parsed for each in present):
                matched.append(name)
                wanted.add(parsed)
            else:
                unmatched.append(name)
        picked = [{**each, "named": True} for each in present if (each["target_host"], each["target_path"]) in wanted]
    else:
        picked = [{**each, "named": False} for each in present]
    found = {"picked": picked, "matched_names": matched, "unmatched_names": unmatched, "refusal": "", "outcomes": [], "warnings": [], "ends": False}
    if entry_point == "playbook":
        refusals = []
        if params["release"]:
            refusals.append(
                f"{edge}: this run carries qumulo_replication_release, which is for the baseline; the restore takes none. Run "
                f"{entry_point_command('playbook', inventory, edge)} again without it."
            )
        if not previous:
            refusals.append(
                f"{edge} has no qumulo_previous_cluster_uuids, so the restore can find no old record holding its replica. Apply host_vars_patch "
                f"from the rebuild report to host_vars/{edge}.yml, or, if it was re-formed outside edge_rebuild, add its old UUID to "
                "qumulo_previous_cluster_uuids, and run the restore again."
            )
        for name in unmatched:
            refusals.append(
                f"qumulo_replication_restore names {name}, which {edge} does not declare present. Name a present relationship out of it as "
                "<target host>:<target path>."
            )
        if refusals:
            found["refusal"] = " ".join(refusals)
            return found
    targets = params["targets"] or {}
    for each in picked:
        each["name"] = f"{edge}:{each['source_path']} to {each['target_host']}:{each['target_path']}"
        each["target_answered"], each["x"] = _target_x(targets, each, previous)
    if not previous and entry_point == "second_pass":
        found["ends"] = True
        warned = set()
        known = {lower(uuid) for uuid in hosts.values()}
        for each in picked:
            read = targets.get(each["target_host"])
            if not each["target_answered"]:
                if each["target_host"] not in warned:
                    warned.add(each["target_host"])
                    found["warnings"].append(
                        f"{edge}: {each['target_host']} did not answer, so this run could not check its records of {edge}'s relationships."
                    )
                continue
            for record in read.get("target_relationships") or []:
                if record["target_path"] == each["target_path"] and record["state"] in LIVE and lower(record["source_cluster_uuid"]) not in known:
                    found["ends"] = False
        return found
    _group(found, edge, inventory, entry_point)
    _restored_first(found, params, present, previous)
    return found


def _target_x(targets, relationship, previous):
    """(answered, X): whether the relationship's target answered the run's read, and X there, or None."""
    read = targets.get(relationship["target_host"])
    if read is None or read.get("error"):
        return False, None
    found = None
    for record in read.get("target_relationships") or []:
        if record["target_path"] == relationship["target_path"] and record["state"] in LIVE and lower(record["source_cluster_uuid"]) in previous:
            found = record
    return True, found


def _group(found, edge, inventory, entry_point):
    """The nested group, refused, and the fan-out's losers, not restored, among picked relationships whose X exists."""
    with_x = [each for each in found["picked"] if each["x"] is not None]
    nested = []
    for index, a in enumerate(with_x):
        for b in with_x[index + 1 :]:
            if a["source_path"] != b["source_path"] and overlaps(a["source_path"], b["source_path"]):
                nested.append((a, b) if len(a["source_path"]) < len(b["source_path"]) else (b, a))
    if nested and entry_point == "playbook":
        texts = []
        for outer, inner in nested:
            texts.append(
                f"{edge}:{outer['source_path']} and {edge}:{inner['source_path']} are nested, and only one replica can be restored into a directory. "
                f"Name the outer one in qumulo_replication_restore, {outer['target_host']}:{outer['target_path']}; the inner one then takes the "
                "release."
            )
        found["refusal"] = " ".join(texts)
        return
    grouped = set()
    for outer, inner in nested:
        command = (
            f"ansible-playbook qumulo.cluster.replication_restore -i {inventory} -l {edge} -e "
            f'\'{{"qumulo_replication_restore": ["{outer["target_host"]}:{outer["target_path"]}"]}}\''
        )
        for one, other in ((outer, inner), (inner, outer)):
            if one["name"] in grouped:
                continue
            grouped.add(one["name"])
            found["outcomes"].append(
                {
                    "name": one["name"],
                    "outcome": REFUSED,
                    "fails": True,
                    "message": (
                        f"{edge}:{one['source_path']} and {edge}:{other['source_path']} are nested, and only one replica can be restored into a "
                        f"directory. Restore the outer one first: {command}; the inner one then takes the release."
                    ),
                }
            )
    by_path = {}
    for each in with_x:
        if each["name"] not in grouped:
            by_path.setdefault(each["source_path"], []).append(each)
    for source_path, fans in by_path.items():
        if len(fans) < 2:
            continue
        # The newer recovery point wins; with equal ones, the target host that sorts first. ISO 8601 times in UTC sort as text.
        ordered = sorted(fans, key=lambda each: (_negated(each["x"].get("recovery_point") or ""), each["target_host"]))
        winner = ordered[0]
        for loser in ordered[1:]:
            found["outcomes"].append(
                {
                    "name": loser["name"],
                    "outcome": NOT_RESTORED,
                    "fails": True,
                    "message": (
                        f"{loser['target_host']}:{loser['target_path']} holds an older replica of {source_path} than {winner['target_host']}:"
                        f"{winner['target_path']}, which was restored. Release it with allow_non_empty_target on the entry and the release "
                        f"ansible-playbook qumulo.cluster.baseline -i {inventory} -l {edge} prints, which replaces the older replica from the "
                        "restored directory."
                    ),
                }
            )
    decided = {outcome["name"] for outcome in found["outcomes"]}
    found["picked"] = [each for each in found["picked"] if each["name"] not in decided]


def _restored_first(found, params, present, previous):
    """Each picked relationship whose X exists and whose directory a sibling with an equal or outer source path was restored into.

    The sibling's X is gone by then, so the fan-out above no longer sees it. Its target answered this run's read with no X
    for it, and its restore record past X's delete, or its restore stamp naming the edge's newest rebuild report, is on
    the controller. A stamp keeps its name across rebuilds, so one from an earlier rebuild shows nothing. The release
    stays the way on, run after run.
    """
    records = set(params["restore_files"] or [])
    claims = params["report_claims"] or {}
    stamped = set(claims.get("stamped") or [])
    targets = params["targets"] or {}
    hosts = params["inventory_cluster_uuids"] or {}
    edge, inventory = params["edge"], params["inventory"]
    restored = []
    for each in present:
        uuid = hosts.get(each["target_host"])
        if not uuid:
            continue
        digest = key_hash({**each, "target_cluster_uuid": uuid})
        if f"{digest}.yml" not in records and digest not in stamped:
            continue
        answered, x = _target_x(targets, each, previous)
        if answered and x is None:
            restored.append(each)
    for each in found["picked"]:
        if each["x"] is None:
            continue
        mine = (each["source_path"], each["target_host"], each["target_path"])
        sibling = next(
            (
                other
                for other in restored
                if (other["source_path"], other["target_host"], other["target_path"]) != mine and inside(each["source_path"], other["source_path"])
            ),
            None,
        )
        if sibling is not None:
            message = release_after_sibling(edge, inventory, each["source_path"], each["target_host"], each["target_path"], sibling)
            found["outcomes"].append({"name": each["name"], "outcome": NOT_RESTORED, "fails": True, "message": message})
    decided = {outcome["name"] for outcome in found["outcomes"]}
    found["picked"] = [each for each in found["picked"] if each["name"] not in decided]


def malformed_name(name):
    return (
        f"qumulo_replication_restore names '{name}', which is not <target host>:<target path>; name a relationship by its target host and "
        f"target path, such as {name}:/replicas/edge01."
    )


def _parse_name(name):
    """A qumulo_replication_restore entry as (target host, canonical target path), or None without a ':'."""
    host, colon, path = str(name).partition(":")
    if not colon:
        return None
    return host, canonical_path(path, f"qumulo_replication_restore's entry '{name}': the target path")


def _negated(text):
    """A sort key that orders text descending."""
    return tuple(-ord(character) for character in text) + (1,)
