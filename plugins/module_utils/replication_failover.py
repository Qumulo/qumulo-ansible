# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The disaster-recovery role's next step for one relationship, from fresh reads of both clusters (specs/025-replication-failover).

A is the relationship's declared source host and B its declared target host, whatever the direction the records run in
now. In the declared direction B holds a target record and A a source record; after a reverse, B holds a source record
and A a target record of the same id. plan() names the relationship's state from those records, lists every step the
action has left from it, and refuses, before the first step, whatever a step of the action would be refused for.

Each verb moves the record of the cluster it is sent to at once, and the peer's record follows seconds later, or not
while the peer cannot be reached (research F6, F13). So each direction's state is read from the record a verb moves
first, and the other record is allowed to lag. Two pairs of states still read the same; the plan takes each as the one
whose next step loses nothing, unless the run's last verb says otherwise (spec Decision 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_path
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import (
    ENDED,
    ESTABLISHED,
    PENDING,
    SETTINGS,
    blackout_windows,
    is_ip_literal,
    lower,
    only_one,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.replication_target import replication_address_verdict

ACTIONS = ("failover", "resync", "failback")
DISCONNECTING = "DISCONNECTING"
DISCONNECTED = "DISCONNECTED"
RECONNECTING = frozenset({"RECONNECTING", "REESTABLISHING"})
RUNNING = "REPLICATION_RUNNING"

# The states plan() names.
REPLICATING = "replicating"
FAILING_OVER = "failing over"
FAILED_OVER = "failed over"
RECONNECTING_DECLARED = "reconnecting"
REVERSED = "reversed"
RESYNCING = "resyncing"
RESYNCED = "resynced"
SOURCE_DISCONNECTING = "failing back, source disconnecting"
SOURCE_WRITABLE = "failing back, source writable"
REVERSED_BACK = "failing back, reversed"
SOURCE_STUCK = "source record stuck"


@dataclass
class Hosts:
    """The two hosts as the inventory gives them, and the relationship's declared key."""

    source: str
    target: str
    source_uuid: str
    target_uuid: str
    source_path: str
    target_path: str
    source_address: str | None
    source_port: int
    target_address: str | None
    target_port: int
    name: str

    @classmethod
    def from_params(cls, params):
        entry = params["relationship"]
        target_path = canonical_path(entry["target_path"], "target_path")
        return cls(
            source=params["source_host"],
            target=params["target_host"],
            source_uuid=lower(params["source_cluster_uuid"]),
            target_uuid=lower(params["target_cluster_uuid"]),
            source_path=canonical_path(entry["source_path"], "source_path"),
            target_path=target_path,
            source_address=params["source_replication_address"],
            source_port=params["source_replication_port"],
            target_address=params["target_replication_address"],
            target_port=params["target_replication_port"],
            name=f"{params['target_host']}:{target_path}",
        )


@dataclass
class Found:
    """The relationship's records on both clusters, in the direction they run in; A's is None when A did not answer or holds none."""

    direction: str
    on_target: dict
    on_source: dict | None


def plan(params):
    """The result of replication_failover_plan for one relationship: its state, the steps left, and the next one."""
    hosts = Hosts.from_params(params)
    found = find(params, hosts)
    state = state_of(found, params["last_verb"], params["action"])
    names = step_names(params, state, found, hosts)
    notes, warnings = [], []
    refuse(params, hosts, found, names, notes, warnings)
    steps = [describe(name, hosts, found, params) for name in names]
    return {
        "state": state,
        "direction": found.direction,
        "relationship_id": str(found.on_target["id"]),
        "recovery_point": found.on_target["recovery_point"],
        "done": not steps,
        "step": steps[0] if steps else None,
        "steps": steps,
        "notes": notes,
        "warnings": warnings,
    }


def _records(facts, kind):
    return (facts or {}).get(f"{kind}_relationships") or []


def _matching(records, uuid_key, uuids, paths):
    return [r for r in records if r[uuid_key] in uuids and (r["source_path"], r["target_path"]) == paths]


def find(params, hosts):
    """The records of the declared key on both clusters, in either direction (spec Decision 3); ENDED ones only when no live one is left."""
    on_b, on_a = params["target_relationships"], params["source_relationships"]
    declared_key = (hosts.source_path, hosts.target_path)
    reversed_key = (hosts.target_path, hosts.source_path)
    previous = {lower(uuid) for uuid in params["source_previous_cluster_uuids"]}
    held = [r for r in _matching(_records(on_b, "target"), "source_cluster_uuid", previous, declared_key) if r["state"] != ENDED]
    if held:
        raise QumuloError(
            f"{hosts.target} holds a record of {hosts.target_path} from a previous UUID of {hosts.source} (id {held[0]['id']}): {hosts.source} was "
            "rebuilt, and that record holds the replica a restore brings back. Restore it with playbooks/replication_restore.yml, not a failover"
        )
    every = {
        "declared": _matching(_records(on_b, "target"), "source_cluster_uuid", {hosts.source_uuid}, declared_key),
        "reversed": _matching(_records(on_b, "source"), "target_cluster_uuid", {hosts.source_uuid}, reversed_key),
    }
    live = {direction: [r for r in records if r["state"] != ENDED] for direction, records in every.items()}
    if live["declared"] and live["reversed"]:
        ids = ", ".join(str(r["id"]) for r in live["declared"] + live["reversed"])
        raise QumuloError(
            f"{hosts.target} holds records of {hosts.name} in both directions (ids {ids}); a failover acts on one relationship, so delete the one that "
            "is not wanted first"
        )
    direction = "reversed" if live["reversed"] or (every["reversed"] and not every["declared"]) else "declared"
    candidates = live[direction] or every[direction]
    if not candidates:
        raise QumuloError(
            f"{hosts.target} holds no record of {hosts.name}: no target record from {hosts.source}'s UUID {hosts.source_uuid} of "
            f"{hosts.source_path}, and no source record of {hosts.target_path} to it; there is nothing to fail over"
        )
    on_target = only_one(candidates, f"the relationship {hosts.name} on {hosts.target}")
    on_source = None
    if on_a is not None:
        if direction == "declared":
            a_records = _matching(_records(on_a, "source"), "target_cluster_uuid", {hosts.target_uuid}, declared_key)
        else:
            a_records = _matching(_records(on_a, "target"), "source_cluster_uuid", {hosts.target_uuid}, reversed_key)
        on_source = next((r for r in a_records if str(r["id"]) == str(on_target["id"])), None)
    return Found(direction, on_target, on_source)


def state_of(found, last_verb, action):
    """The relationship's state, from the record a verb moves first in each direction (plan.md, the state table).

    A failover reads B's record alone, since it sends nothing to A and A may lag, be ENDED or not answer.
    """
    b = found.on_target["state"]
    a = found.on_source["state"] if found.on_source else None
    if b in (PENDING, ENDED):
        return b
    if found.direction == "declared" and action == "failover" and b == ESTABLISHED:
        return REPLICATING
    if found.direction == "declared":
        if b == DISCONNECTING:
            return FAILING_OVER
        if b == DISCONNECTED:
            return REVERSED_BACK if last_verb == "reverse_to_target" else FAILED_OVER
        if b == ESTABLISHED and a in (PENDING, ENDED):
            # A source record that cannot follow B's back to ESTABLISHED; a wait for it would never end.
            return SOURCE_STUCK
        if b in RECONNECTING or (b == ESTABLISHED and a not in (None, ESTABLISHED)):
            return RECONNECTING_DECLARED
        return REPLICATING
    if a is None:
        return REVERSED
    if a in (PENDING, ENDED):
        return a
    if a == DISCONNECTING:
        return SOURCE_DISCONNECTING
    if a == DISCONNECTED:
        return SOURCE_WRITABLE if last_verb == "make_writable_source" else REVERSED
    if a == ESTABLISHED and b == ESTABLISHED:
        return RESYNCED
    return RESYNCING


# What each action has left from each state, before step_names drops the steps the records show are not due; a state
# an action does not start from is refused, naming the action that does (FR-011).
RESYNC_FROM = {
    FAILING_OVER: ("wait_target_writable", "wait_source_disconnected", "reverse_to_source", "apply_mapping", "reconnect_source"),
    FAILED_OVER: ("wait_source_disconnected", "reverse_to_source", "apply_mapping", "reconnect_source"),
    REVERSED: ("apply_mapping", "reconnect_source"),
    RESYNCING: ("apply_mapping", "wait_resynced"),
    RESYNCED: ("apply_mapping",),
}
CUTOVER = ("await_sync", "make_writable_source", "reverse_to_target", "reconnect_target", "apply_settings")
FROM = {
    "failover": {REPLICATING: ("make_writable_target",), FAILING_OVER: ("wait_target_writable",), FAILED_OVER: ()},
    "resync": RESYNC_FROM,
    "failback": {
        **{state: (*steps, *CUTOVER) for state, steps in RESYNC_FROM.items()},
        SOURCE_DISCONNECTING: ("wait_source_writable", *CUTOVER[2:]),
        SOURCE_WRITABLE: CUTOVER[2:],
        REVERSED_BACK: CUTOVER[3:],
        RECONNECTING_DECLARED: ("wait_replicating", "apply_settings"),
        REPLICATING: ("apply_settings",),
    },
}
NEXT_ACTION = {
    REPLICATING: "failover",
    FAILED_OVER: "resync or failback",
    REVERSED: "resync or failback",
    RESYNCING: "failback",
    RESYNCED: "failback",
    RECONNECTING_DECLARED: "failback, which waits until both records read ESTABLISHED",
}


def step_names(params, state, found, hosts):
    """The names of the steps the action has left from state; a refusal when it does not start from there."""
    action, table = params["action"], FROM[params["action"]]
    if state == SOURCE_STUCK:
        raise QumuloError(_stuck_message(action, hosts, found.on_source["state"]))
    if state not in table:
        if state in (PENDING, ENDED):
            raise QumuloError(f"the relationship {hosts.name} is {state}; a failover acts only on a relationship that has replicated")
        raise QumuloError(
            f"the relationship {hosts.name} is {state}, which {action} does not start from; run {NEXT_ACTION.get(state, 'it again once it settles')}"
        )
    names = list(table[state])
    if params["synced"] and "await_sync" in names:
        names.remove("await_sync")
    if "wait_source_disconnected" in names and (found.on_source is None or found.on_source["state"] == DISCONNECTED):
        names.remove("wait_source_disconnected")
    if "apply_mapping" in names and not _mapping_due(params["relationship"], found):
        names.remove("apply_mapping")
    if state == REPLICATING and "apply_settings" in names and not _settings_differ(params["relationship"], found.on_source):
        names.remove("apply_settings")
    return names


def _stuck_message(action, hosts, a_state):
    """The refusal of a source record that cannot follow the target's ESTABLISHED one, naming each record and the way on."""
    remedy = {
        ENDED: (
            "declare the relationship again: the baseline then reports row O3 on it, which recreate on the entry and a "
            "release naming O3 lift (Replicate a directory to another cluster)"
        ),
        PENDING: f"delete that record on {hosts.source}, then run the baseline, which creates the relationship again",
    }[a_state]
    return (
        f"{hosts.source}'s source record of {hosts.name} is {a_state}, while {hosts.target}'s target record reads ESTABLISHED; "
        f"{hosts.source}'s record cannot follow it back, so the {action} has nothing it can move. To replicate again, {remedy}"
    )


def _mapping_due(entry, found):
    """Whether B's reversed source record lacks the declared id mapping, which the copy back needs (spec Decision 9)."""
    if not entry.get("map_local_ids_to_nfs_ids"):
        return False
    return found.direction == "declared" or not found.on_target["map_local_ids_to_nfs_ids"]


def _settings_differ(entry, record):
    """Whether A's source record differs from a declared setting; due, since unknown, without the record."""
    if record is None:
        return True
    for key in SETTINGS:
        want = entry.get(key)
        if want is None:
            continue
        have = record.get(key)
        if key == "blackout_windows":
            want, have = blackout_windows(want), blackout_windows(have)
        elif key == "blackout_window_timezone":
            # As replication_relationship sends it, without the spaces around it.
            want = want.strip()
        if want != have:
            return True
    return False


def refuse(params, hosts, found, names, notes, warnings):
    """Every refusal of the steps left, before the first of them (FR-011)."""
    action = params["action"]
    if params["source_relationships"] is None:
        if action != "failover":
            raise QumuloError(f"{action} needs {hosts.source}, which did not answer: {params['source_error']}")
        notes.append(f"{hosts.source} did not answer ({params['source_error']}); the plan goes on from {hosts.target}'s record alone")
    elif found.on_source is None and names:
        if action != "failover":
            raise QumuloError(f"{hosts.source} holds no record of the relationship {hosts.name}; a {action} reverses both records, so it needs both")
        notes.append(f"{hosts.source} holds no record of {hosts.name}; the plan goes on from {hosts.target}'s record alone")
    if "make_writable_target" in names:
        _refuse_failover(params, hosts, found, notes)
    reverses = {
        "reverse_to_source": (hosts.source, hosts.source_address, hosts.source_path, "source_tenancy", "resync"),
        "reverse_to_target": (hosts.target, hosts.target_address, hosts.target_path, "target_tenancy", "failback"),
    }
    for name, (host, address, path, tenancy, which) in reverses.items():
        if name not in names:
            continue
        if not is_ip_literal(address):
            raise QumuloError(
                f"{host}'s qumulo_replication_address, {address!r}, is not an IP address; the {which}'s reverse makes {host} the target and sends it"
            )
        given = params[tenancy]
        if given is None:
            raise QumuloError(f"the plan needs {tenancy}, {host}'s tenants and network document, to check that replication reaches {address}")
        refusal, warning = replication_address_verdict(
            address,
            path,
            lambda given=given: given["tenants"],
            # network_info's document subset is already in the canonical shape network.holding reads.
            lambda given=given: given.get("document") or {"frontend_networks": []},
            lambda tenant: tenant["replication_enabled"],
        )
        if warning:
            warnings.append(warning)
        if refusal:
            raise QumuloError(f"{refusal} The {which} makes {host} the target of {hosts.name}.")


def _refuse_failover(params, hosts, found, notes):
    """A make-writable on B needs a recovery point, and no job running on a source that answers (spec Decision 8)."""
    record, source = found.on_target, found.on_source
    if not record["recovery_point"]:
        raise QumuloError(
            f"the relationship {hosts.name} has no recovery point on {hosts.target}: its first job has not finished, so its target cannot be made writable"
        )
    if source is not None and source["job_state"] == RUNNING:
        if not params["allow_partial_job"]:
            raise QumuloError(
                f"{hosts.source} is running a job of {hosts.name}, started {source['job_start_time']}; a failover now discards the changes it carries. "
                "Run again once the job ends; with writes stopped on the source, the next job carries the rest. To discard the job's changes, as for a "
                "job that makes no progress, run with -e replication_failover_allow_partial_job=true"
            )
        notes.append(f"{hosts.source}'s job started {source['job_start_time']} is discarded, as replication_failover_allow_partial_job allows")
    if params["source_relationships"] is None and record["job_state"] == RUNNING:
        notes.append(
            f"{hosts.target}'s record shows a job started {record['job_start_time']}, which cannot finish while {hosts.source} does not answer "
            "(QUIRK-125); the failover discards its partial changes"
        )


def describe(name, hosts, found, params):
    """One step: what the role runs, on which cluster, and its line in the plan."""
    rp = found.on_target["recovery_point"]
    rid = str(found.on_target["id"])
    a, b, sp, tp = hosts.source, hosts.target, hosts.source_path, hosts.target_path
    skipping = set(params["newer_writes_hosts"])

    def compare(host):
        if host in skipping:
            return f"it does not compare first, since replication_failover_allow_newer_writes names {host}"
        return "the real run first compares it with the recovery point's snapshot and stops when anything was written there since"

    cluster, kind, text = {
        "make_writable_target": (
            "target",
            "verb",
            f"{b}: make {tp} writable. It goes back to the recovery point {rp}; writes on {a} after it stay behind on {a}",
        ),
        "wait_target_writable": ("target", "poll", f"{b}: wait until its record reads DISCONNECTED"),
        "wait_source_disconnected": ("source", "poll", f"{a}: wait until its record reads DISCONNECTED, as {b}'s does"),
        "reverse_to_source": (
            "target",
            "verb",
            f"{b}: reverse the relationship toward {a} at {hosts.source_address}:{hosts.source_port}, so {b} replicates {tp} to {a}; no data changes",
        ),
        "apply_mapping": ("target", "settings", f"{b}: turn map_local_ids_to_nfs_ids on in its source record, as declared, since the reverse turned it off"),
        "reconnect_source": ("source", "verb", f"{a}: reconnect. {sp} goes back to the recovery point and becomes read-only; {compare(a)}"),
        "wait_resynced": ("source", "poll", f"{a} and {b}: wait until both records read ESTABLISHED; {b}'s writes then reach {a}"),
        "await_sync": (
            "target",
            "sync",
            f"{b}: wait until its source record's recovery point has moved twice past the one the wait reads first, so {a} holds what {b} held",
        ),
        "make_writable_source": ("source", "verb", f"{a}: make {sp} writable, from the recovery point, which holds what {b} held"),
        "wait_source_writable": ("source", "poll", f"{a}: wait until its record reads DISCONNECTED"),
        "reverse_to_target": (
            "source",
            "verb",
            f"{a}: reverse the relationship toward {b} at {hosts.target_address}:{hosts.target_port}, so {a} replicates {sp} to {b} again; no data changes",
        ),
        "reconnect_target": ("target", "verb", f"{b}: reconnect. {tp} goes back to the recovery point and becomes read-only; {compare(b)}"),
        "wait_replicating": ("target", "poll", f"{a} and {b}: wait until both records read ESTABLISHED"),
        "apply_settings": ("source", "settings", f"{a}: apply the relationship's declared settings where its record differs, as a reverse may reset them"),
    }[name]
    step = {"name": name, "cluster": cluster, "host": a if cluster == "source" else b, "kind": kind, "relationship_id": rid, "text": text}
    if kind == "verb":
        step["verb"] = VERBS[name]
        step["polls"] = POLLS.get(name, [])
        if name == "reverse_to_source":
            step.update(source_address=hosts.source_address, source_port=hosts.source_port)
        elif name == "reverse_to_target":
            step.update(source_address=hosts.target_address, source_port=hosts.target_port)
    elif kind == "poll":
        step["polls"] = POLLS[name]
    elif kind == "sync":
        step["recovery_point"] = rp
    elif name == "apply_mapping":
        step["options"] = {
            "source_path": tp,
            "target_cluster_uuid": hosts.source_uuid,
            "target_path": sp,
            "target_address": hosts.source_address,
            "target_port": hosts.source_port,
            "relationship_id": rid,
            "map_local_ids_to_nfs_ids": True,
        }
    else:
        entry = params["relationship"]
        step["options"] = {
            "source_path": sp,
            "target_cluster_uuid": hosts.target_uuid,
            "target_path": tp,
            "target_address": hosts.target_address,
            "target_port": hosts.target_port,
            "relationship_id": rid,
            **{key: entry[key] for key in SETTINGS if entry.get(key) is not None},
        }
    return step


# The replication_reverse verb each verb step sends.
VERBS = {
    "make_writable_target": "make_writable",
    "make_writable_source": "make_writable",
    "reverse_to_source": "reverse",
    "reverse_to_target": "reverse",
    "reconnect_source": "reconnect",
    "reconnect_target": "reconnect",
}
_TARGET_DISCONNECTED = [{"cluster": "target", "side": "target", "until": [DISCONNECTED]}]
_SOURCE_DISCONNECTED_DECLARED = [{"cluster": "source", "side": "source", "until": [DISCONNECTED]}]
_SOURCE_DISCONNECTED = [{"cluster": "source", "side": "target", "until": [DISCONNECTED]}]
_RESYNCED = [{"cluster": "source", "side": "target", "until": [ESTABLISHED]}, {"cluster": "target", "side": "source", "until": [ESTABLISHED]}]
_REPLICATING = [{"cluster": "target", "side": "target", "until": [ESTABLISHED]}, {"cluster": "source", "side": "source", "until": [ESTABLISHED]}]
# The records each step waits for, by inventory role and side, and the states that end the wait. A verb step's polls
# run right after its verb, so that the step ends where the next one starts (spec Decision 2).
POLLS = {
    "make_writable_target": _TARGET_DISCONNECTED,
    "wait_target_writable": _TARGET_DISCONNECTED,
    "wait_source_disconnected": _SOURCE_DISCONNECTED_DECLARED,
    "reconnect_source": _RESYNCED,
    "wait_resynced": _RESYNCED,
    "make_writable_source": _SOURCE_DISCONNECTED,
    "wait_source_writable": _SOURCE_DISCONNECTED,
    "reconnect_target": _REPLICATING,
    "wait_replicating": _REPLICATING,
}
