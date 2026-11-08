# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_restore_step: the restore's step choice, from both clusters' reads (plan, Restore).

The records are scripted in the shape qumulo.cluster.replication_info returns both sides in. The edge edge01 was
rebuilt from OLD_UUID to EDGE_UUID; its relationship /projects to hub01:/replicas/edge01-projects left X on the hub.
R is the restore relationship, from the hub's replica directory to the edge's source path, which the reverse turns into
the declared relationship's orientation.
"""

from __future__ import annotations

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import key_hash
from ansible_collections.qumulo.cluster.plugins.modules import replication_restore_step

EDGE, HUB = "edge01", "hub01"
EDGE_UUID = "a4a4b3f0-5d7e-4c51-9e0e-8e4a3d2c1b10"
OLD_UUID = "9533a9e2-e57a-454f-8aa0-9c77b706b7bd"
HUB_UUID = "59052546-452f-483e-a800-0a925d3fa1bd"
OTHER_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
FOREIGN_UUID = "c0f3ee1c-3f3a-4bb0-bd37-6a2f06c8f5d1"
SOURCE, TARGET = "/projects", "/replicas/edge01-projects"
X_ID, R_ID = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
EDGE_ADDRESS, HUB_ADDRESS = "203.0.113.10", "203.0.113.20"
X_POINT, R_POINT = "2026-11-01T08:21:54Z", "2026-11-02T09:00:00Z"
# The ids of X's and R's recovery-point snapshots on the hub.
X_SNAPSHOT, R_SNAPSHOT = 5, 40
NOW = "2026-11-02T12:00:00Z"
ARTIFACTS = "/srv/artifacts"
DIRECTORY = f"{ARTIFACTS}/{EDGE}/restore"
KEY = {"source_path": SOURCE, "target_host": HUB, "target_cluster_uuid": HUB_UUID, "target_path": TARGET}
RECORD_PATH = f"{DIRECTORY}/{key_hash(KEY)}.yml"
STAMP_PATH = f"{DIRECTORY}/done-{key_hash(KEY)}.yml"
NAME = f"{EDGE}:{SOURCE} to {HUB}:{TARGET}"
SECOND_PASS = f"ansible-playbook qumulo.cluster.edge_license_complete -i inv -l {EDGE}"
PLAYBOOK = f"ansible-playbook qumulo.cluster.replication_restore -i inv -l {EDGE}"
REPORT = "rebuild-20261101T100000.yml"
ENTRY = {"source_path": SOURCE, "target": HUB, "target_path": TARGET, "replication_enabled": True, "blackout_window_timezone": "UTC"}


def record(record_id, source_uuid, source_path, target_uuid, target_path, state="ESTABLISHED", **extra):
    return {
        "id": record_id,
        "source_cluster_uuid": source_uuid,
        "target_cluster_uuid": target_uuid,
        "source_path": source_path,
        "target_path": target_path,
        "target_address": None,
        "target_port": None,
        "state": state,
        "end_reason": None,
        "job_state": "REPLICATION_NOT_RUNNING",
        "recovery_point": None,
        "error_from_last_job": None,
        "target_root_read_only": True,
        **extra,
    }


def x(state="ESTABLISHED", read_only=True, point=X_POINT, snapshot_id=X_SNAPSHOT, **extra):
    """X, the hub's target record from the edge's old UUID."""
    found = {"recovery_point": point, "recovery_point_snapshot_id": snapshot_id, "target_root_read_only": read_only, **extra}
    return record(X_ID, OLD_UUID, SOURCE, HUB_UUID, TARGET, state, **found)


def pre_source(state="ESTABLISHED", **extra):
    """R before the reverse, on the hub: its source record from the replica directory to the edge."""
    return record(R_ID, HUB_UUID, TARGET, EDGE_UUID, SOURCE, state, target_address=EDGE_ADDRESS, target_port=3712, **extra)


def pre_target(state="ESTABLISHED", point=None, **extra):
    """R before the reverse, on the edge: its target record."""
    return record(R_ID, HUB_UUID, TARGET, EDGE_UUID, SOURCE, state, recovery_point=point, **extra)


def post_source(state="DISCONNECTED", record_id=R_ID, **extra):
    """R after the reverse, on the edge: the declared relationship's source record."""
    return record(record_id, EDGE_UUID, SOURCE, HUB_UUID, TARGET, state, target_address=HUB_ADDRESS, target_port=3712, recovery_point=R_POINT, **extra)


def post_target(state="DISCONNECTED", point=R_POINT, snapshot_id=R_SNAPSHOT, record_id=R_ID, **extra):
    """R after the reverse, on the hub: the declared relationship's target record."""
    return record(record_id, EDGE_UUID, SOURCE, HUB_UUID, TARGET, state, recovery_point=point, recovery_point_snapshot_id=snapshot_id, **extra)


def facts(sources=(), targets=()):
    return {"source_relationships": list(sources), "target_relationships": list(targets)}


def restore_record(marked=False, point=R_POINT, snapshot_id=R_SNAPSHOT, written_at="2026-11-02T10:00:00Z", **extra):
    found = {
        "format": 1,
        "key": KEY,
        "r_id": R_ID,
        "x_id": X_ID,
        "x_source_cluster_uuid": OLD_UUID,
        "x_source_path": SOURCE,
        "x_snapshot_ids": [X_SNAPSHOT],
        "recovery_point": point,
        "recovery_point_snapshot_id": snapshot_id,
        "written_at": written_at,
        **extra,
    }
    if marked:
        found["reconnected"] = True
    return found


# Step 1's inputs, all passing.
READY = {
    "source_directory": {"missing": [], "first_object": None},
    "record_directory_writable": True,
    "relationship_check": {"changed": True, "unmet": []},
    "target_check": {"changed": True, "unmet": []},
    "target_spokes": [],
    "edge_spokes": [],
}


def o1(first):
    """The target check's O1 entry for a source directory holding first, as replication_target gives it under allow_directories_only."""
    return {
        "row": "O1",
        "message": f"{SOURCE} holds {first}, and allow_directories_only lets the source's content replace only subdirectories; the authorize would replace it",
        "lifted": False,
        "pending": False,
        "facts": {"target_path": SOURCE, "holds_objects": True, "first_object": first},
    }


def holding(first):
    """Step 1's inputs for a source directory holding first, with the unlifted O1 entry the role's target check then carries."""
    return {**READY, "source_directory": {"missing": [], "first_object": first}, "target_check": {"changed": True, "unmet": [o1(first)]}}


EXIT_BEFORE_RECOVERY_POINT = (
    f"To abandon it, delete R {R_ID} on {HUB}, then on {EDGE}; {EDGE}:{SOURCE} then holds a partial copy, which must not be released over "
    f"the replica, and {HUB}:{TARGET} still holds the full replica"
)
EXIT_STEPS_4_TO_6 = (
    f"To abandon it, delete R {R_ID} on both {HUB} and {EDGE}; {EDGE} keeps a complete copy and anything written since, and {HUB}:{TARGET} keeps the replica"
)
HAND_OFF = (
    f"{HUB} cannot reach {EDGE} at {EDGE_ADDRESS}:3712, its qumulo_replication_address and qumulo_replication_port, so R's create failed, and "
    f"nothing was written on either cluster. Either set qumulo_replication_address to an address of {EDGE} that {HUB} reaches, and run "
    f"{PLAYBOOK} again; or copy the replica from {HUB}:{TARGET} into {EDGE}:{SOURCE} over a protocol, then run ansible-playbook "
    f"qumulo.cluster.baseline -i inv -l {EDGE} with allow_non_empty_target on the entry and the release its held refusal prints."
)
REVERSE_STOP = (
    f"{EDGE} cannot reach {HUB} at {HUB_ADDRESS}:13712, the target's qumulo_replication_address and port, so the reverse of R {R_ID} failed. "
    f"{EDGE}:{SOURCE} holds the replica's content and is writable, and the old record {X_ID} still locks {HUB}:{TARGET}. Fix the path from "
    f"{EDGE} to {HUB}, or {HUB}'s qumulo_replication_address. {EXIT_STEPS_4_TO_6}. {PLAYBOOK} resumes it."
)


def released_after(target_host, target_path, sibling_source, sibling_target_host, sibling_target_path, source=SOURCE):
    """The release a relationship takes when a sibling with an equal or outer source path was restored into its directory first."""
    other = f"{EDGE}:{sibling_source} to {sibling_target_host}:{sibling_target_path}"
    return (
        f"{target_host}:{target_path} holds a replica of {EDGE}:{source}, but {other} was restored into {EDGE}:{sibling_source} first, so "
        f"{EDGE}:{source} already holds that relationship's data. Release {target_host}:{target_path} with allow_non_empty_target on the "
        f"entry and the release ansible-playbook qumulo.cluster.baseline -i inv -l {EDGE} prints, which replaces its replica from the "
        "restored directory."
    )


def step(run_module, edge=(), hub=(), edge_targets=(), hub_sources=(), check_mode=False, record_text=None, **extra):
    """One call with edge's and hub's records: edge holds sources and edge_targets, hub holds hub_sources and targets."""
    args = {
        "edge": EDGE,
        "edge_cluster_uuid": EDGE_UUID,
        "previous_cluster_uuids": [OLD_UUID],
        "inventory_cluster_uuids": {EDGE: EDGE_UUID, HUB: HUB_UUID},
        "entry_point": "playbook",
        "inventory": "inv",
        "artifact_dir": ARTIFACTS,
        "now": NOW,
        "relationship": ENTRY,
        "target_cluster_uuid": HUB_UUID,
        "edge_relationships": facts(edge, edge_targets),
        "target_relationships": facts(hub_sources, hub),
        "replication_address": EDGE_ADDRESS,
        "target_replication_address": HUB_ADDRESS,
        "record_text": record_text,
        **extra,
    }
    result = run_module(replication_restore_step, args, check_mode=check_mode)
    assert result.get("changed", False) is False
    return result


def ok(result):
    assert not result.get("failed"), result.get("msg")
    return result


def actions(result):
    return [action["action"] for action in result["actions"]]


def text(found):
    return yaml.safe_dump(found)


# Item 5: step 1 or step 2


def test_x_with_no_pair_asks_for_step_1s_inputs_with_the_options_of_both_check_passes(run_module):
    result = ok(step(run_module, hub=[x()]))

    assert result["decision"] == "read"
    assert result["reads"] == ["source_directory", "relationship_check", "target_check", "target_spokes", "edge_spokes", "record_directory_writable"]
    assert result["check_options"]["relationship"] == {
        "source_path": TARGET,
        "target_cluster_uuid": EDGE_UUID,
        "target_path": SOURCE,
        "target_address": EDGE_ADDRESS,
        "target_port": 3712,
        "replication_enabled": True,
        "blackout_windows": [],
    }
    assert result["check_options"]["target"] == {
        "source_cluster_uuid": HUB_UUID,
        "source_path": TARGET,
        "target_path": SOURCE,
        "source_creates": True,
        "allow_directories_only": True,
        "directory_pending": True,
        "strict_capacity": False,
    }
    assert result["x"]["id"] == X_ID
    assert result["restore_to_do"] is True


def test_a_missing_directory_and_its_declared_ancestors_are_created_first_outermost_first(run_module):
    directories = [{"path": "/"}, {"path": "/projects/"}, {"path": "/top"}]
    entry = {**ENTRY, "source_path": "/top/projects"}
    found = {**READY, "source_directory": {"missing": ["/top/projects", "/top"], "first_object": None}}
    hub_x = record(X_ID, OLD_UUID, "/top/projects", HUB_UUID, TARGET, recovery_point=X_POINT)

    result = ok(step(run_module, hub=[hub_x], relationship=entry, directories=directories, **found))

    assert (result["decision"], result["step"]) == ("step", 1)
    assert result["source_directories"] == ["/top", "/top/projects"]
    assert result["actions"] == [
        {"action": "create_directory", "cluster": "edge", "path": "/top"},
        {"action": "create_directory", "cluster": "edge", "path": "/top/projects"},
        {"action": "ensure_record_directory", "path": DIRECTORY, "mode": "0700"},
    ]


def test_from_step_1s_state_the_module_returns_step_2_creating_r_on_the_target(run_module):
    result = ok(step(run_module, hub=[x()], **READY))

    assert (result["decision"], result["step"]) == ("step", 2)
    assert result["actions"] == [
        {
            "action": "create_relationship",
            "cluster": "target",
            "options": {
                "source_path": TARGET,
                "target_cluster_uuid": EDGE_UUID,
                "target_path": SOURCE,
                "target_address": EDGE_ADDRESS,
                "target_port": 3712,
                "replication_enabled": True,
                "blackout_windows": [],
            },
            "stop_on": "replication_connection_error",
            "stop_message": HAND_OFF,
        }
    ]
    assert result["snapshots_left"] == []


def test_a_pending_r_record_on_the_edge_alone_runs_step_1s_checks_and_then_step_2_again(run_module):
    result = ok(step(run_module, hub=[x()], edge_targets=[pre_target("AWAITING_AUTHORIZATION")], **READY))

    assert result["step"] == 2


def test_in_check_mode_the_module_returns_the_remaining_predicted_sequence(run_module):
    result = ok(step(run_module, hub=[x()], check_mode=True, **READY))

    assert result["step"] == 2
    assert result["sequence"] == [2, 3, 4, 5, 6, 7, 8, 9]


def test_step_1_warns_on_a_running_job_naming_xs_recovery_point_snapshot(run_module):
    running = x(job_state="REPLICATION_RUNNING", job_start_time="2026-11-02T11:00:00Z")

    result = ok(step(run_module, hub=[running], **READY))

    assert result["warnings"] == [
        (
            f"{NAME}: the old record {X_ID} shows a running job, started at 2026-11-02T11:00:00Z, so {HUB}:{TARGET} may hold a partial job; "
            f"its recovery-point snapshot {X_SNAPSHOT}, at recovery point {X_POINT}, is the last consistent copy."
        )
    ]


def test_step_1_warns_on_an_error_of_xs_last_job_without_a_snapshot_id(run_module):
    result = ok(step(run_module, hub=[x(error_from_last_job="disk full", snapshot_id=None)], **READY))

    assert result["warnings"] == [
        (
            f"{NAME}: the old record {X_ID} shows the error 'disk full', so {HUB}:{TARGET} may hold a partial job; "
            f"its recovery-point snapshot, at recovery point {X_POINT}, is the last consistent copy."
        )
    ]


# Step 1's preconditions: each refusal before any write, in check mode too


def refused(result):
    ok(result)
    assert result["decision"] == "refused"
    assert result["outcome"] == "refused"
    assert result["fails"] is True
    assert result["actions"] == []
    return result["message"]


@pytest.mark.parametrize("check_mode", [False, True])
@pytest.mark.parametrize(
    ("holder", "words"),
    [
        (x(read_only=False), "is ESTABLISHED, but the directory is not read-only"),
        (x(state="DISCONNECTED"), "is DISCONNECTED."),
        (x(point=None), "has no recovery point"),
    ],
)
def test_a_writable_replica_or_one_without_a_recovery_point_refuses(run_module, check_mode, holder, words):
    message = refused(step(run_module, hub=[holder], check_mode=check_mode, **READY))

    assert words in message
    assert message.endswith("Restore by the copy and the release.")


def test_an_ended_record_beside_x_refuses_as_o5(run_module):
    ended = record("33", FOREIGN_UUID, "/old", HUB_UUID, TARGET, "ENDED", end_reason="deleted")

    message = refused(step(run_module, hub=[x(), ended], **READY))

    assert f"ENDED record 33 from {FOREIGN_UUID}:/old lies at {HUB}:{TARGET} beside the old record {X_ID} (O5)" in message


def test_a_directory_holding_a_file_refuses_with_scenario_2s_text_alone_not_the_check_passes_o1_beside_it(run_module):
    message = refused(step(run_module, hub=[x()], **holding("/projects/new.txt")))

    assert message == (
        f"{EDGE}:{SOURCE} holds /projects/new.txt, so the restore cannot fill it from {HUB}:{TARGET} without replacing it. Files written on "
        f"{EDGE} since the replay are the likeliest cause, and they may be the only copy of themselves. Either move them out of {SOURCE}, run "
        f"the restore again, and move them back once the relationship runs from {EDGE}; or keep them, copy the replica in around them over a "
        f"protocol, and run ansible-playbook qumulo.cluster.baseline -i inv -l {EDGE} with allow_non_empty_target on the entry and the release "
        "its held refusal prints."
    )
    assert "empty the directory" not in message and "empty it" not in message


@pytest.mark.parametrize("sibling_path", [SOURCE, "/"], ids=["equal", "the sibling outside"])
def test_a_directory_a_sibling_with_an_equal_or_outer_path_was_restored_into_first_takes_the_release(run_module, sibling_path):
    sibling = {"source_path": sibling_path, "target_host": "dr02", "target_path": "/replicas/other/", "past_delete": True}

    message = refused(step(run_module, hub=[x()], siblings=[sibling], **holding("/projects/a.txt")))

    assert message == released_after(HUB, TARGET, sibling_path, "dr02", "/replicas/other")


def test_a_directory_a_sibling_inside_it_was_restored_into_first_takes_the_copy_around_it_and_the_release(run_module):
    sibling = {"source_path": "/projects/inner", "target_host": "dr02", "target_path": "/replicas/other", "past_delete": True}

    message = refused(step(run_module, hub=[x()], siblings=[sibling], **holding("/projects/a.txt")))

    other = f"{EDGE}:/projects/inner to dr02:/replicas/other"
    assert message == (
        f"{EDGE}:{SOURCE} holds /projects/a.txt, since {other} was restored into it first. To keep that data, copy the replica from {HUB}:{TARGET} "
        f"over a protocol into {EDGE}:{SOURCE}, around {other}'s data, then run ansible-playbook qumulo.cluster.baseline -i inv -l {EDGE} with "
        "allow_non_empty_target on the entry and the release its held refusal prints."
    )


def test_a_sibling_not_yet_past_its_delete_leaves_the_plain_holds_a_file_text(run_module):
    sibling = {"source_path": SOURCE, "target_host": "dr02", "target_path": "/replicas/other", "past_delete": False}

    assert "Files written on" in refused(step(run_module, hub=[x()], siblings=[sibling], **holding("/projects/a.txt")))


def test_an_edge_whose_replication_address_is_not_an_ip_literal_refuses(run_module):
    message = refused(step(run_module, hub=[x()], **{**READY, "replication_address": "edge01.example.com"}))

    assert message == (
        f"{EDGE}'s qumulo_replication_address, edge01.example.com, is not an IP literal, and the target reaches the edge at it. Set "
        f"qumulo_replication_address to an IP address of {EDGE}."
    )


def test_an_edge_without_a_replication_address_refuses(run_module):
    message = refused(step(run_module, hub=[x()], **{**READY, "replication_address": None}))

    assert message == (
        f"{EDGE} has no qumulo_replication_address, and the target reaches the edge at it. Set qumulo_replication_address to an IP address of {EDGE}."
    )


@pytest.mark.parametrize(
    ("address", "how"),
    [("hub01.example.com", "has qumulo_replication_address hub01.example.com, not an IP literal"), (None, "has no qumulo_replication_address")],
    ids=["a host name", "unset"],
)
def test_a_target_whose_replication_address_step_6_cannot_send_refuses_before_any_write(run_module, address, how):
    message = refused(step(run_module, hub=[x()], **{**READY, "target_replication_address": address}))

    assert message == (
        f"{HUB} {how}, and step 6's reverse sends {EDGE} the address at which it reaches {HUB}. Set qumulo_replication_address to an IP address of {HUB}."
    )


def test_an_empty_artifact_dir_refuses(run_module):
    message = refused(step(run_module, hub=[x()], **{**READY, "artifact_dir": ""}))

    assert message == "edge_rebuild_artifact_dir is empty, so no restore record could be written. Set it to a directory on the controller."


def test_an_unwritable_record_directory_refuses(run_module):
    message = refused(step(run_module, hub=[x()], **{**READY, "record_directory_writable": False}))

    assert message == f"The record directory {DIRECTORY} cannot be created or written. Make {ARTIFACTS} writable, or run from a controller where it is."


def test_an_unmet_check_pass_refuses_naming_it(run_module):
    o7 = {"row": "O1", "message": "/projects holds entries", "lifted": False, "pending": False, "facts": {}}
    pending = {"row": "target_missing", "message": "pending", "lifted": False, "pending": True, "facts": {}}
    found = {**READY, "target_check": {"unmet": [o7, pending], "msg": "refused"}, "relationship_check": {"msg": "connection refused", "unmet": []}}

    message = refused(step(run_module, hub=[x()], **found))

    assert f"The check-mode run of replication_relationship on {HUB} for R failed: connection refused." in message
    assert f"The check-mode run of replication_target on {EDGE} for R reports: /projects holds entries." in message
    assert "pending" not in message


def test_a_check_pass_that_failed_without_a_message_says_so(run_module):
    message = refused(step(run_module, hub=[x()], **{**READY, "relationship_check": {"failed": True, "unmet": []}}))

    assert message == f"The check-mode run of replication_relationship on {HUB} for R failed with no message."


LICENSE_TEXT = (
    f"{EDGE} holds no active license, and it expects one, so the restore would start a full sync of {HUB}:{TARGET} into a cluster held to "
    "its unlicensed capacity. Once the license applies, by the license file from Qumulo support in qumulo_capacity.license_document or by "
    f"Qumulo issuing it to the cluster, run ansible-playbook qumulo.cluster.edge_license_complete -i inv -l {EDGE}, which restores it after "
    f"the license and the clamp. If {EDGE} runs without a license, add license: none to {EDGE}'s qumulo_capacity, keeping its other keys, "
    "such as clamp and free_threshold."
)
CLAMP_TEXT = (
    f"{EDGE}'s clamp differs from qumulo_capacity.clamp. Run ansible-playbook qumulo.cluster.edge_license_complete -i inv -l {EDGE}, which "
    "sets the clamp, then the restore."
)
NO_LICENSE = {"license_status": "absent"}
ACTIVE = {"license_status": "active"}


@pytest.mark.parametrize(
    "capacity",
    [{"license": "pending", "clamp": "4TB"}, {"clamp": "4TB"}, None, {"license": "required"}],
    ids=["qumulo_capacity", "a clamp alone", "no qumulo_capacity", "required"],
)
def test_the_license_rule_refuses_an_edges_host_with_no_active_license(run_module, capacity):
    clamp = {"clamp_plan": {"changed": False}} if capacity and "clamp" in capacity else {}

    message = refused(step(run_module, hub=[x()], in_edges=True, capacity=capacity, license=NO_LICENSE, **clamp, **READY))

    assert message == LICENSE_TEXT


@pytest.mark.parametrize(
    ("in_edges", "capacity"),
    [(True, {"license": "none"}), (True, {"license": "none", "clamp": "1TB"}), (False, None)],
    ids=["none", "none with a clamp", "outside qumulo_edges"],
)
def test_the_license_rule_passes_none_and_a_host_outside_qumulo_edges(run_module, in_edges, capacity):
    clamp = {"clamp_plan": {"changed": False}} if capacity and "clamp" in capacity else {}

    result = ok(step(run_module, hub=[x()], in_edges=in_edges, capacity=capacity, license=NO_LICENSE, **clamp, **READY))

    assert result["step"] == 2


def test_a_capacity_with_the_license_document_fails_argument_validation_without_its_value(run_module):
    document = "-----BEGIN QUMULO LICENSE-----"
    capacity = {"license": "pending", "clamp": "4TB", "license_document": document}

    result = step(run_module, hub=[x()], in_edges=True, capacity=capacity, license=NO_LICENSE, **READY)

    assert result["failed"] is True
    assert "capacity.license_document" in result["msg"]
    assert document not in str(result)


def test_the_license_is_read_only_where_the_rule_expects_one(run_module):
    expected = ok(step(run_module, hub=[x()], in_edges=True, capacity={"license": "pending"}, **READY))
    unexpected = ok(step(run_module, hub=[x()], in_edges=True, capacity={"license": "none"}, **READY))

    assert expected["reads"] == ["license"]
    assert unexpected["step"] == 2


@pytest.mark.parametrize("license_found", [ACTIVE, NO_LICENSE], ids=["active", "none active"])
def test_the_clamp_half_refuses_a_stored_clamp_that_differs_with_or_without_a_license_under_none(run_module, license_found):
    capacity = {"license": "none", "clamp": "2TB"}

    message = refused(step(run_module, hub=[x()], in_edges=True, capacity=capacity, license=license_found, clamp_plan={"changed": True}, **READY))

    assert message == CLAMP_TEXT


def test_the_clamp_half_refuses_with_a_license_active_and_passes_with_no_clamp_declared(run_module):
    capacity = {"license": "pending", "clamp": "4TB"}

    assert refused(step(run_module, hub=[x()], in_edges=True, capacity=capacity, license=ACTIVE, clamp_plan={"changed": True}, **READY)) == CLAMP_TEXT
    passed = ok(step(run_module, hub=[x()], in_edges=True, capacity={"license": "none"}, license=ACTIVE, **READY))
    assert passed["step"] == 2


@pytest.mark.parametrize("plan", [{"changed": True}, {"msg": "lowering the clamp needs allow_decrease=true"}], ids=["differs", "failed"])
def test_with_no_license_where_one_is_expected_only_the_license_refuses_and_not_the_clamp_half(run_module, plan):
    # The clamp half applies with a license active or under license: none; the license refusal names the second pass,
    # which sets the clamp after the license.
    capacity = {"license": "pending", "clamp": "4TB"}

    message = refused(step(run_module, hub=[x()], in_edges=True, capacity=capacity, license=NO_LICENSE, clamp_plan=plan, **READY))

    assert message == LICENSE_TEXT


@pytest.mark.parametrize(
    ("license_found", "reads"), [({"license": ACTIVE}, ["clamp_plan"]), ({}, ["license", "clamp_plan"])], ids=["license_read", "neither_read"]
)
def test_a_declared_clamp_asks_for_the_clamp_plan_after_the_license(run_module, license_found, reads):
    capacity = {"license": "pending", "clamp": "4TB"}

    result = ok(step(run_module, hub=[x()], in_edges=True, capacity=capacity, **license_found, **READY))

    assert result["reads"] == reads


def test_a_failed_clamp_plan_refuses_with_its_message(run_module):
    plan = {"msg": "lowering the clamp needs allow_decrease=true"}

    message = refused(step(run_module, hub=[x()], capacity={"clamp": "1GB"}, clamp_plan=plan, **READY))

    assert message == f"{EDGE}'s clamp plan failed: lowering the clamp needs allow_decrease=true. Fix the cause and run {PLAYBOOK} again."


def test_a_clamp_plan_that_failed_without_a_message_says_so(run_module):
    message = refused(step(run_module, hub=[x()], capacity={"clamp": "1GB"}, clamp_plan={"failed": True}, **READY))

    assert message == f"{EDGE}'s clamp plan failed with no message. Fix the cause and run {PLAYBOOK} again."


def predicted(run_module, pending):
    capacity = {"license": "pending", "clamp": "4TB"}
    return ok(step(run_module, hub=[x()], check_mode=True, in_edges=True, capacity=capacity, predicted_license_pending=pending, **READY))


def test_in_check_mode_a_predicted_license_replaces_the_license_read_and_passes_the_clamp_half(run_module):
    result = predicted(run_module, False)

    assert (result["decision"], result["step"]) == ("step", 2)


def test_in_check_mode_a_license_predicted_pending_refuses_with_the_license_rule(run_module):
    assert refused(predicted(run_module, True)) == LICENSE_TEXT


# Scenario 14: portals and other relationships


def spokes(*roots):
    return [{"roots": [{"spoke_root": root} for root in roots]}]


@pytest.mark.parametrize(
    ("root", "how"),
    [(TARGET, "is"), ("/replicas", "lies under"), (f"{TARGET}/cache", "holds")],
)
def test_xs_directory_and_a_spoke_root_on_the_target_refuse_with_scenario_14s_text(run_module, root, how):
    message = refused(step(run_module, hub=[x()], **{**READY, "target_spokes": spokes(root)}))

    assert message == (
        f"{HUB}:{TARGET} {how} the portal spoke root {root}, whose content is cached hub data, so the restore cannot copy it as the replica. "
        "Restore by the copy and the release."
    )


@pytest.mark.parametrize(("root", "how"), [(SOURCE, "is"), ("/", "lies under"), ("/projects/cache/", "holds")])
def test_the_source_directory_and_a_spoke_root_on_the_edge_refuse_with_scenario_14s_text(run_module, root, how):
    canonical = root.rstrip("/") or "/"

    message = refused(step(run_module, hub=[x()], **{**READY, "edge_spokes": spokes(root)}))

    assert message == (
        f"{EDGE}:{SOURCE} {how} the portal spoke root {canonical}; the restore cannot fill a directory a portal serves. Move the relationship's "
        f"source path off the portal in the inventory, or restore by the copy and the release, copying around {canonical}, which the portal "
        "serves from the hub."
    )


@pytest.mark.parametrize(("path", "how"), [(SOURCE, "the same directory as"), ("/projects/inner", "nested with"), ("/", "nested with")])
def test_a_live_source_relationship_on_the_edge_nested_with_the_source_path_refuses(run_module, path, how):
    other = record("44", EDGE_UUID, path, OTHER_UUID, "/replicas/x")
    hosts = {EDGE: EDGE_UUID, HUB: HUB_UUID, "dr02": OTHER_UUID}

    message = refused(step(run_module, hub=[x()], edge=[other], inventory_cluster_uuids=hosts, **READY))

    assert message == (
        f"{EDGE}:{path} to dr02:/replicas/x is {how} {SOURCE}, and the restore would make it read-only and replace its content. If the replay "
        "created it from the empty directory, delete it on both clusters, run the restore again, and the next baseline creates it again from "
        "the restored data; otherwise restore by the copy and the release."
    )


def test_a_relationship_into_the_edge_nested_with_the_source_path_refuses_before_its_o7(run_module):
    into = record("55", OTHER_UUID, "/data", EDGE_UUID, "/projects/in")
    o7 = {
        "row": "O7",
        "message": "relationship 55 holds /projects/in; remove that relationship first",
        "lifted": False,
        "pending": False,
        "facts": {"id": "55"},
    }
    hosts = {EDGE: EDGE_UUID, HUB: HUB_UUID, "dr02": OTHER_UUID}

    message = refused(step(run_module, hub=[x()], edge_targets=[into], inventory_cluster_uuids=hosts, **{**READY, "target_check": {"unmet": [o7]}}))

    assert message == (
        f"dr02:/data to {EDGE}:/projects/in replicates into {SOURCE}, which the restore fills. Move one of the two paths in the inventory, or "
        "restore by the copy and the release."
    )


@pytest.mark.parametrize("path", ["/replicas/edge01-projects/inner", "/replicas", "/elsewhere"])
def test_another_relationship_on_the_target_nested_with_the_replica_directory_is_refused_at_no_step(run_module, path):
    other = record("66", OTHER_UUID, "/data", HUB_UUID, path)
    hosts = {EDGE: EDGE_UUID, HUB: HUB_UUID, "dr02": OTHER_UUID}

    assert ok(step(run_module, hub=[x(), other], inventory_cluster_uuids=hosts, **READY))["step"] == 2


@pytest.mark.parametrize(
    "found",
    [
        {"target_spokes": spokes("/replicas/edge01-projects-2")},
        {"edge_spokes": spokes("/projects-cache")},
        {"edge": [record("44", EDGE_UUID, "/home", OTHER_UUID, "/replicas/home")]},
        {"edge_targets": [record("55", OTHER_UUID, "/data", EDGE_UUID, "/in")]},
    ],
    ids=["a target spoke beside the replica", "an edge spoke beside the source", "a source relationship at /home", "a relationship into /in"],
)
def test_an_unrelated_portal_spoke_or_relationship_lets_step_1_pass(run_module, found):
    hosts = {EDGE: EDGE_UUID, HUB: HUB_UUID, "dr02": OTHER_UUID}

    result = ok(step(run_module, hub=[x()], inventory_cluster_uuids=hosts, **{**READY, **found}))

    assert (result["decision"], result["step"]) == ("step", 2)


# Leftovers


@pytest.mark.parametrize(
    ("edge_targets", "hub_sources", "edge", "hub", "named"),
    [
        ([pre_target("ENDED")], [pre_source()], [], [], f"record {R_ID} on edge01 (ENDED)"),
        ([pre_target()], [pre_source("ENDED")], [], [], f"record {R_ID} on hub01 (ENDED)"),
        ([pre_target()], [], [], [], f"record {R_ID} on edge01 (ESTABLISHED)"),
        ([], [pre_source("AWAITING_AUTHORIZATION")], [], [], f"record {R_ID} on hub01 (AWAITING_AUTHORIZATION)"),
        (
            [],
            [],
            [post_source("AWAITING_AUTHORIZATION")],
            [post_target("AWAITING_AUTHORIZATION")],
            f"record {R_ID} on hub01 (AWAITING_AUTHORIZATION), record {R_ID} on edge01 (AWAITING_AUTHORIZATION)",
        ),
    ],
    ids=["ended on the edge", "ended on the target", "one-sided", "a pending source on the target alone", "pending in the reversed orientation"],
)
def test_a_leftover_refuses_at_step_1_naming_the_abandon_exit_not_o3(run_module, edge_targets, hub_sources, edge, hub, named):
    o3 = {"row": "O3", "message": "source record is ENDED", "binds_to": R_ID, "lifted": False, "pending": False, "facts": {"id": R_ID}}
    found = {**READY, "relationship_check": {"unmet": [o3]}}

    message = refused(step(run_module, hub=[x(), *hub], edge=edge, edge_targets=edge_targets, hub_sources=hub_sources, **found))

    assert message == f"R's leftover records lie at the restore's paths: {named}. Delete them, on {HUB} first and then on {EDGE}, and run the restore again."


# Item 4: a restore in progress, before the reverse


def test_rs_pair_pending_on_the_edge_gives_step_3_authorizing_by_id_with_wait(run_module):
    result = ok(step(run_module, hub=[x()], hub_sources=[pre_source("AWAITING_AUTHORIZATION")], edge_targets=[pre_target("AWAITING_AUTHORIZATION")]))

    assert result["step"] == 3
    assert result["actions"] == [
        {
            "action": "authorize",
            "cluster": "edge",
            "options": {
                "source_cluster_uuid": HUB_UUID,
                "source_path": TARGET,
                "target_path": SOURCE,
                "relationship_id": R_ID,
                "allow_directories_only": True,
                "wait": True,
            },
        }
    ]
    assert result["relationship_id"] == R_ID


def test_r_established_on_the_edge_with_no_recovery_point_waits_at_step_4_with_its_deadline(run_module):
    result = ok(step(run_module, hub=[x()], hub_sources=[pre_source()], edge_targets=[pre_target()], wait_started="2026-11-02T11:00:00Z", wait_timeout=600))

    assert result["step"] == 4
    assert result["actions"] == [{"action": "wait_for_recovery_point", "relationship_id": R_ID}]
    assert result["deadline"] == "2026-11-02T11:10:00Z"


def test_step_4s_deadline_runs_from_now_without_wait_started(run_module):
    result = ok(step(run_module, hub=[x()], hub_sources=[pre_source()], edge_targets=[pre_target()]))

    assert result["deadline"] == "2026-11-03T12:00:00Z"


def test_r_in_sync_gives_step_5_make_writable_then_a_poll_by_id_bounded_by_the_reverse_timeout(run_module):
    result = ok(step(run_module, hub=[x()], hub_sources=[pre_source()], edge_targets=[pre_target(point=R_POINT)], reverse_timeout=120))

    assert result["step"] == 5
    make_writable, poll = result["actions"]
    assert make_writable == {"action": "make_writable", "cluster": "edge", "relationship_id": R_ID, "allow_data_loss": True}
    assert poll["polls"] == [{"cluster": "edge", "side": "target", "relationship_id": R_ID, "until": ["DISCONNECTED"]}]
    assert poll["timeout"] == 120
    assert poll["timeout_message"].startswith(f"R {R_ID} did not read DISCONNECTED on {EDGE} within 120 seconds of the make-writable. To abandon it")
    assert poll["timeout_message"].endswith(f"{PLAYBOOK} resumes it.")


def test_a_rerun_reading_disconnecting_polls_again_rather_than_make_writable(run_module):
    result = ok(step(run_module, hub=[x()], hub_sources=[pre_source()], edge_targets=[pre_target("DISCONNECTING", point=R_POINT)]))

    assert result["step"] == 5
    assert actions(result) == ["poll"]


def test_r_disconnected_on_the_edge_gives_step_6_the_reverse_with_the_targets_address(run_module):
    result = ok(
        step(run_module, hub=[x()], hub_sources=[pre_source()], edge_targets=[pre_target("DISCONNECTED", point=R_POINT)], target_replication_port=13712)
    )

    assert result["step"] == 6
    assert result["actions"] == [
        {
            "action": "reverse",
            "cluster": "edge",
            "options": {"relationship_id": R_ID, "address": HUB_ADDRESS, "port": 13712},
            "stop_on": "replication_connection_error",
            "stop_message": REVERSE_STOP,
        }
    ]


@pytest.mark.parametrize(
    ("edge_target", "exit_text"),
    [
        (pre_target(), EXIT_BEFORE_RECOVERY_POINT),
        (pre_target(point=R_POINT), EXIT_STEPS_4_TO_6),
        (pre_target("DISCONNECTED", point=R_POINT), EXIT_STEPS_4_TO_6),
    ],
    ids=["step 4", "step 5", "step 6"],
)
def test_x_not_sound_before_the_reverse_stops_with_no_verb_naming_the_replica_in_use(run_module, edge_target, exit_text):
    result = ok(step(run_module, hub=[x(state="DISCONNECTED")], hub_sources=[pre_source()], edge_targets=[edge_target]))

    assert result["decision"] == "stopped"
    assert result["actions"] == []
    assert result["message"] == (
        f"the replica {HUB}:{TARGET} is in use: its old record {X_ID} is DISCONNECTED, not ESTABLISHED, as when an operator made it writable; "
        f"the restore never deletes it or reconnects after it. {exit_text}. {PLAYBOOK} resumes it."
    )


def test_rs_pair_is_recognised_without_x_soundness_x_disconnected_is_a_stop_not_not_applicable(run_module):
    result = ok(step(run_module, hub=[x(state="DISCONNECTED", read_only=False)], hub_sources=[pre_source()], edge_targets=[pre_target(point=R_POINT)]))

    assert result["outcome"] == "stopped"


def test_x_deleted_by_hand_before_the_reverse_stops_naming_the_exit_for_steps_4_to_6(run_module):
    result = ok(step(run_module, hub_sources=[pre_source()], edge_targets=[pre_target(point=R_POINT)]))

    assert result["outcome"] == "stopped"
    assert result["message"] == (
        f"the old record that held {HUB}:{TARGET} is gone, deleted by hand before the reverse, so the restore cannot go on; R {R_ID} may also "
        f"be a hand reversal of the declared relationship, which the replication_failover role owns. {EXIT_STEPS_4_TO_6}. {PLAYBOOK} resumes it."
    )


# After the reverse, X still there: step 7


def test_after_the_reverse_with_x_sound_step_7_ensures_the_directory_deletes_x_by_its_own_key_reads_and_writes_the_record(run_module):
    result = ok(step(run_module, hub=[x(), post_target()], edge=[post_source()]))

    assert result["step"] == 7
    assert actions(result) == ["ensure_record_directory", "delete_x", "read_after_delete", "write_record"]
    ensure, delete, read, write = result["actions"]
    assert (ensure["path"], ensure["mode"]) == (DIRECTORY, "0700")
    assert delete == {
        "action": "delete_x",
        "cluster": "target",
        "options": {"source_cluster_uuid": OLD_UUID, "source_path": SOURCE, "target_path": TARGET, "state": "absent"},
    }
    assert read == {"action": "read_after_delete", "cluster": "target", "relationship_id": R_ID, "side": "target"}
    assert write["path"] == RECORD_PATH
    # The first reading of R's recovery point and its snapshot, which the read after the delete confirms.
    assert write["record"] == restore_record(written_at=None)


def test_after_the_reverse_x_established_but_writable_lets_step_7_go_ahead(run_module):
    result = ok(step(run_module, hub=[x(read_only=False), post_target()], edge=[post_source()]))

    assert result["step"] == 7


def test_x_disconnected_after_the_reverse_with_no_record_is_a_stop_and_step_7_never_deletes_it(run_module):
    result = ok(step(run_module, hub=[x(state="DISCONNECTED"), post_target()], edge=[post_source()]))

    assert result["outcome"] == "stopped"
    assert result["actions"] == []
    assert f"delete R {R_ID} on both {HUB} and {EDGE}" in result["message"]


def test_step_7_with_an_empty_artifact_dir_stops_before_the_delete(run_module):
    result = ok(step(run_module, hub=[x(), post_target()], edge=[post_source()], artifact_dir=""))

    assert result["outcome"] == "stopped"
    assert result["message"].startswith("edge_rebuild_artifact_dir is empty")


def test_step_7_names_the_stop_when_the_record_directory_cannot_be_written_before_the_delete(run_module):
    result = ok(step(run_module, hub=[x(), post_target()], edge=[post_source()]))

    ensure = result["actions"][0]
    assert ensure["stop_message"].startswith(f"the record directory {DIRECTORY} is not writable, so the restore cannot write the record")
    assert ensure["stop_message"].endswith(f"{PLAYBOOK} resumes it.")


def compose(run_module, relationship):
    base = restore_record(written_at=None, point=X_POINT, snapshot_id=30)
    args = {"edge": EDGE, "entry_point": "playbook", "now": NOW, "after_delete": {"record": base, "relationship": relationship}}
    return ok(run_module(replication_restore_step, args))["record"]


def test_the_record_after_xs_delete_holds_rs_recovery_point_and_its_snapshots_id_from_that_read_and_its_write_time(run_module):
    written = compose(run_module, post_target(point=R_POINT, snapshot_id="41"))

    assert written == restore_record(snapshot_id=41, written_at=NOW, point=R_POINT)


def test_a_failed_read_after_the_delete_keeps_the_first_values_and_writes_the_record(run_module):
    written = compose(run_module, None)

    assert written == restore_record(snapshot_id=30, written_at=NOW, point=X_POINT)


# Step 8 from an unmarked record, and its guards


def unmarked_call(run_module, point=R_POINT, snapshot_id=R_SNAPSHOT, **extra):
    """A call past X's delete with an unmarked record, R DISCONNECTED on both sides with the given recovery point and snapshot."""
    return step(run_module, hub=[post_target(point=point, snapshot_id=snapshot_id)], edge=[post_source()], record_text=text(restore_record(**extra)))


def test_an_unmarked_record_with_its_guards_holding_resumes_step_8_marking_before_the_reconnect(run_module):
    result = ok(unmarked_call(run_module))

    assert result["step"] == 8
    assert actions(result) == ["mark_record", "reconnect", "poll"]
    mark, reconnect, poll = result["actions"]
    assert mark == {"action": "mark_record", "path": RECORD_PATH, "record": {**restore_record(), "reconnected": True}}
    assert reconnect["cluster"] == "target"
    assert reconnect["options"] == {"relationship_id": R_ID, "allow_data_loss": True, "wait": False}
    assert reconnect["on_refusal"] == {
        "clear_mark_when": "DISCONNECTED",
        "path": RECORD_PATH,
        "record": restore_record(),
        "way_on": f"Run the restore again: {PLAYBOOK}. A reconnect by hand must be followed by removing the restore record {RECORD_PATH}.",
    }
    assert poll["polls"] == [
        {"cluster": "edge", "side": "source", "relationship_id": R_ID, "until": ["ESTABLISHED"]},
        {"cluster": "target", "side": "target", "relationship_id": R_ID, "until": ["ESTABLISHED"]},
    ]
    assert poll["timeout"] == 1800
    assert f"{EDGE} cannot reach {HUB} at {HUB_ADDRESS}:3712, the address and port R's source record on {EDGE} uses" in poll["timeout_message"]
    assert "blackout" not in poll["timeout_message"]
    assert result["past_delete"] is True


def test_step_8_resumes_with_names_too(run_module):
    result = step(run_module, hub=[post_target()], edge=[post_source()], record_text=text(restore_record()), named=True)

    assert ok(result)["step"] == 8


def guard_refusal(result):
    message = refused(result)
    assert "reconnect" not in actions(result)
    assert "mark_record" not in actions(result)
    return message


POINT_CHANGED = (
    f"R {R_ID} on {HUB} reads recovery point 2026-11-02T11:30:00Z, not {R_POINT}, so a job has completed there since the restore record was "
    f"written, after a reconnect by hand or from another controller. A failover by hand belongs to the replication_failover role. Remove the restore record "
    f"{RECORD_PATH}."
)
SNAPSHOT_CHANGED = (
    f"R {R_ID} on {HUB} holds recovery-point snapshot 41, not {R_SNAPSHOT}, so R was reconnected or made writable on {HUB} since the restore "
    f"record was written, by hand or from another controller. A failover by hand belongs to the replication_failover role. Remove the restore record "
    f"{RECORD_PATH}."
)


def test_an_unmarked_record_whose_recovery_point_changed_is_refused_with_its_note_never_the_reconnect(run_module):
    message = guard_refusal(unmarked_call(run_module, point="2026-11-02T11:30:00Z"))

    assert message == POINT_CHANGED
    assert "allow_data_loss" not in message


def test_an_unmarked_record_whose_recovery_point_snapshot_changed_is_refused_with_its_note_never_the_reconnect(run_module):
    message = guard_refusal(unmarked_call(run_module, snapshot_id=41))

    assert message == SNAPSHOT_CHANGED
    assert "allow_data_loss" not in message


def test_both_guards_on_the_target_failing_are_both_named(run_module):
    message = guard_refusal(unmarked_call(run_module, point="2026-11-02T11:30:00Z", snapshot_id=41))

    assert message == f"{POINT_CHANGED} {SNAPSHOT_CHANGED}"


def test_the_snapshot_ids_compare_as_integers(run_module):
    assert ok(unmarked_call(run_module, snapshot_id=str(R_SNAPSHOT)))["step"] == 8


def test_an_unmarked_record_past_its_resume_window_is_refused_with_the_reconnect_by_id_as_its_way_on(run_module):
    message = guard_refusal(unmarked_call(run_module, written_at="2026-11-01T11:00:00Z"))

    assert message == (
        f"The restore record {RECORD_PATH} is not marked, and it was written at 2026-11-01T11:00:00Z, more than replication_restore_resume_window "
        f"(86400 seconds) ago, too long ago to tell whether R {R_ID} was reconnected elsewhere, or failed over on {HUB}, since. A failover by "
        f"hand belongs to the replication_failover role. Remove the restore record {RECORD_PATH}. For your own restore stopped past the resume window, "
        "check that "
        f"nobody has written on {HUB}:{TARGET}, reconnect R {R_ID} by id with qumulo.cluster.replication_reverse and allow_data_loss, then "
        "remove the record."
    )


def test_every_failed_guard_is_named(run_module):
    message = guard_refusal(unmarked_call(run_module, point="2026-11-02T11:30:00Z", snapshot_id=41, written_at="2026-11-01T11:00:00Z"))

    assert message.startswith(f"{POINT_CHANGED} {SNAPSHOT_CHANGED} The restore record {RECORD_PATH} is not marked, and it was written at")


def test_a_stale_record_is_removed_before_any_guard(run_module):
    # R ENDED on the edge, and its recovery point changed on the hub: the record is stale, not a failed guard.
    hub = [x(), post_target(point="2026-11-02T11:30:00Z")]

    result = ok(step(run_module, hub=hub, edge=[post_source("ENDED")], record_text=text(restore_record()), **READY))

    assert result["actions"][0] == {"action": "remove_record", "path": RECORD_PATH}
    assert "is ENDED" in result["warnings"][0]
    assert "reads recovery point" not in result["message"]


def test_an_accepted_reconnect_is_marked_before_any_guard(run_module):
    hub = [post_target("REESTABLISHING", point="2026-11-02T11:30:00Z", snapshot_id=41)]

    result = ok(step(run_module, hub=hub, edge=[post_source()], record_text=text(restore_record(written_at="2026-11-01T00:00:00Z"))))

    assert (result["step"], actions(result)) == (8, ["mark_record", "poll"])


# Item 2: an accepted reconnect


MARK = {"action": "mark_record", "path": RECORD_PATH, "record": {**restore_record(), "reconnected": True}}


@pytest.mark.parametrize(("edge_state", "hub_state"), [("RECONNECTING", "DISCONNECTED"), ("DISCONNECTED", "REESTABLISHING")])
def test_an_unmarked_record_with_r_reconnecting_is_marked_first_then_waits(run_module, edge_state, hub_state):
    result = ok(step(run_module, hub=[post_target(hub_state)], edge=[post_source(edge_state)], record_text=text(restore_record())))

    assert (result["step"], actions(result)) == (8, ["mark_record", "poll"])
    assert result["actions"][0] == MARK


def test_an_unmarked_record_with_r_established_on_both_sides_is_marked_first_then_runs_step_9(run_module):
    result = ok(step(run_module, hub=[post_target("ESTABLISHED")], edge=[post_source("ESTABLISHED")], record_text=text(restore_record())))

    assert (result["step"], actions(result)) == (9, ["mark_record", "apply_settings", "write_stamp", "remove_record"])
    assert result["actions"][0] == MARK


@pytest.mark.parametrize(
    ("edge_state", "hub_state"),
    [("ESTABLISHED", "DISCONNECTED"), ("DISCONNECTED", "ESTABLISHED"), ("RECONNECTING", "REESTABLISHING"), ("REESTABLISHING", "RECONNECTING")],
)
def test_a_marked_record_with_one_side_reconnected_and_the_other_lagging_gives_the_read_only_wait(run_module, edge_state, hub_state):
    result = ok(step(run_module, hub=[post_target(hub_state)], edge=[post_source(edge_state)], record_text=text(restore_record(marked=True))))

    assert result["step"] == 8
    assert actions(result) == ["poll"]


def test_a_marked_record_with_r_established_on_both_sides_gives_step_9_settings_stamp_and_removal(run_module):
    claims = {"report": REPORT, "claimed": [KEY]}
    entry = {**ENTRY, "blackout_windows": [], "map_local_ids_to_nfs_ids": True}

    result = ok(
        step(
            run_module,
            hub=[post_target("ESTABLISHED")],
            edge=[post_source("ESTABLISHED")],
            record_text=text(restore_record(marked=True)),
            report_claims=claims,
            relationship=entry,
        )
    )

    assert result["step"] == 9
    assert actions(result) == ["apply_settings", "write_stamp", "remove_record"]
    settings, write_stamp, remove = result["actions"]
    assert settings["options"] == {
        "source_path": SOURCE,
        "target_cluster_uuid": HUB_UUID,
        "target_path": TARGET,
        "target_address": HUB_ADDRESS,
        "target_port": 3712,
        "relationship_id": R_ID,
        "replication_enabled": True,
        "blackout_window_timezone": "UTC",
        "blackout_windows": [],
        "map_local_ids_to_nfs_ids": True,
    }
    assert write_stamp == {
        "action": "write_stamp",
        "path": STAMP_PATH,
        "stamp": {"format": 1, "key": KEY, "r_id": R_ID, "report": REPORT, "written_at": NOW},
    }
    assert remove == {"action": "remove_record", "path": RECORD_PATH}
    assert result["summary"] == f"{NAME}: restored from {HUB}, and the relationship runs from {EDGE_UUID}."
    assert result["snapshots_left"] == [{"host": HUB, "id": X_SNAPSHOT}]


@pytest.mark.parametrize(
    ("subdirectories", "tail"),
    [
        ([{"path": "/projects/a", "exists": True}, {"path": "/projects/b/", "exists": True}], "replaced: /projects/a, /projects/b."),
        ([{"path": "/projects/a", "exists": True}, {"path": "/projects/b", "exists": False}], "replaced: /projects/a, and /projects/b, missing now."),
        ([{"path": "/projects/a", "exists": False}, {"path": "/projects/b", "exists": False}], "replaced: /projects/a, /projects/b, missing now."),
    ],
    ids=["replaced", "replaced and deleted", "deleted"],
)
def test_the_summary_names_the_declared_subdirectories_replaced_and_deleted(run_module, subdirectories, tail):
    result = ok(
        step(
            run_module,
            hub=[post_target("ESTABLISHED")],
            edge=[post_source("ESTABLISHED")],
            record_text=text(restore_record(marked=True)),
            subdirectories=subdirectories,
        )
    )

    assert result["summary"] == (
        f"{NAME}: restored from {HUB}, and the relationship runs from {EDGE_UUID}. Run ansible-playbook qumulo.cluster.baseline -i inv -l {EDGE} "
        f"to put back what the restore's first job {tail}"
    )


@pytest.mark.parametrize(
    ("edge_state", "hub_state"),
    [("DISCONNECTED", "DISCONNECTED"), ("DISCONNECTING", "ESTABLISHED"), ("DISCONNECTED", "DISCONNECTING")],
    ids=["both disconnected", "disconnecting", "different failover states"],
)
def test_a_marked_record_with_r_otherwise_is_refused_with_its_note_and_no_reconnect(run_module, edge_state, hub_state):
    result = ok(step(run_module, hub=[post_target(hub_state)], edge=[post_source(edge_state)], record_text=text(restore_record(marked=True))))

    assert refused(result) == (
        f"The restore record {RECORD_PATH} is marked reconnected, and R {R_ID} is {edge_state} on {EDGE} and {hub_state} on {HUB}: a failover by "
        "hand, or a run stopped between the mark and the reconnect. A marked record never sends a reconnect. After checking that nobody has "
        f"written on {HUB}:{TARGET}, reconnect R {R_ID} by id with qumulo.cluster.replication_reverse and allow_data_loss, then remove the record."
    )


@pytest.mark.parametrize("state", ["ESTABLISHED", "RECONNECTING"])
def test_a_marked_record_with_r_live_in_the_pre_reverse_orientation_is_not_applicable_with_no_poll(run_module, state):
    result = ok(step(run_module, hub_sources=[pre_source(state)], edge_targets=[pre_target(state)], record_text=text(restore_record(marked=True))))

    message = refused(result)
    assert "that is a failover, which the replication_failover role owns" in message
    assert "poll" not in actions(result)


# Item 1: stale or unknown records


def test_a_record_of_unknown_format_stops_the_run_naming_the_file_and_its_way_on(run_module):
    result = step(run_module, hub=[x()], record_text=text({**restore_record(), "format": 2}))

    assert result["failed"] is True
    assert result["stops_run"] is True
    assert result["msg"] == (
        f"The restore record {RECORD_PATH} is of an unknown format: it is not format 1. Move it aside only once no restore of {EDGE} is under "
        f"way on any controller, then run {PLAYBOOK} again; a run without it cannot resume that restore's reconnect."
    )


@pytest.mark.parametrize(
    ("edge", "hub", "edge_targets", "why"),
    [
        ([post_source("ENDED")], [post_target()], [], f"R {R_ID} is ENDED"),
        ([], [post_target()], [], f"R {R_ID} is missing on {EDGE}"),
        ([post_source()], [], [], f"R {R_ID} is missing on {HUB}"),
        ([], [], [], f"R {R_ID} is missing on {EDGE} and {HUB}"),
    ],
    ids=["ended", "missing on the edge", "missing on the target", "missing on both"],
)
def test_a_stale_record_is_reported_named_and_removed_before_anything_else(run_module, edge, hub, edge_targets, why):
    result = ok(step(run_module, hub=[x(), *hub], edge=edge, edge_targets=edge_targets, record_text=text(restore_record()), **READY))

    assert result["actions"][0] == {"action": "remove_record", "path": RECORD_PATH}
    assert result["warnings"][0] == f"{NAME}: removed the stale restore record {RECORD_PATH}, since {why}, and an ENDED or missing R can never reconnect."


def test_a_by_id_read_in_the_wrong_orientation_never_makes_a_live_record_missing(run_module):
    result = step(
        run_module,
        record_text=text(restore_record()),
        edge_relationships={"source_relationships": []},
        target_relationships=facts([], [post_target()]),
    )

    assert result["failed"] is True
    assert "lack target_relationships" in result["msg"]


def test_the_record_found_in_the_pre_reverse_orientation_is_not_missing(run_module):
    result = ok(step(run_module, hub_sources=[pre_source("DISCONNECTED")], edge_targets=[pre_target("DISCONNECTED")], record_text=text(restore_record())))

    assert not any(action["action"] == "remove_record" for action in result["actions"])


def test_the_records_file_name_is_the_hash_of_its_normalized_key(run_module):
    entry = {**ENTRY, "source_path": "/projects/", "target_path": "/replicas/edge01-projects/"}

    result = ok(step(run_module, hub=[x()], relationship=entry, target_cluster_uuid=HUB_UUID.upper()))

    assert result["record_path"] == RECORD_PATH
    assert result["stamp_path"] == STAMP_PATH


# Item 3: done


def test_done_is_decided_before_not_applicable_a_named_established_relationship_is_done_not_refused(run_module):
    result = ok(step(run_module, edge=[post_source("ESTABLISHED")], hub=[post_target("ESTABLISHED")], named=True))

    assert result["outcome"] == "done"
    assert result["fails"] is False
    assert result["actions"] == []
    assert result["past_delete"] is True


def test_done_with_the_report_claim_standing_writes_the_stamp(run_module):
    claims = {"report": REPORT, "claimed": [{**KEY, "source_path": "/projects/"}]}

    result = ok(step(run_module, edge=[post_source("ESTABLISHED")], hub=[post_target("ESTABLISHED")], report_claims=claims))

    assert result["outcome"] == "done"
    assert result["actions"] == [
        {"action": "write_stamp", "path": STAMP_PATH, "stamp": {"format": 1, "key": KEY, "r_id": R_ID, "report": REPORT, "written_at": NOW}}
    ]


# Item 6: not applicable, and the outcomes past it


def test_no_x_and_no_pair_is_skipped_under_all_and_refused_with_the_copy_and_the_release_when_named(run_module):
    held_go = {"status": "go", "message": ""}

    skipped = ok(step(run_module, step21=held_go))
    named = ok(step(run_module, step21=held_go, named=True))

    assert (skipped["outcome"], skipped["fails"]) == ("not applicable", False)
    assert skipped["summary"] == f"{NAME}: not applicable: {skipped['note']}."
    assert skipped["note"] == (
        f"no old record from a previous UUID of {EDGE} holds {HUB}:{TARGET}, so there is nothing to restore from. To replicate from {EDGE}, "
        f"copy the replica into {EDGE}:{SOURCE} over a protocol, then run ansible-playbook qumulo.cluster.baseline -i inv -l {EDGE} with "
        "allow_non_empty_target on the entry and the release its held refusal prints"
    )
    assert refused(named) == f"{skipped['note']}."


def test_a_held_relationship_with_no_x_asks_for_step_21s_check_pass(run_module):
    assert ok(step(run_module))["reads"] == ["step21"]


def test_a_held_relationship_with_no_x_is_not_restored_with_the_held_message(run_module):
    result = ok(step(run_module, step21={"status": "held", "message": "hub01:/replicas/edge01-projects is held: ..."}))

    assert (result["outcome"], result["fails"]) == ("not restored", True)
    assert result["message"] == "hub01:/replicas/edge01-projects is held: ..."


def test_a_step_21_refusal_is_refused_by_step_21(run_module):
    result = ok(step(run_module, step21={"status": "refused", "message": "refused before any write"}))

    assert (result["outcome"], result["decision"], result["message"]) == ("refused by step 21", "refused_by_step_21", "refused before any write")


FOREIGN_TEXT = (
    f"{FOREIGN_UUID}'s record 77 holds {TARGET}. If {EDGE} was rebuilt, apply host_vars_patch from the rebuild report to host_vars/{EDGE}.yml, "
    f"or, if {EDGE} was re-formed outside edge_rebuild, add {FOREIGN_UUID} to its qumulo_previous_cluster_uuids, and run the second pass "
    "again; otherwise that record belongs to another cluster, and the baseline refuses the relationship (O2): end that cluster's "
    "relationship, or move the target path in the inventory."
)


def foreign(uuid=FOREIGN_UUID):
    return record("77", uuid, "/data", HUB_UUID, TARGET)


def test_a_foreign_record_is_not_restored_on_an_edge_with_previous_uuids(run_module):
    result = ok(step(run_module, hub=[foreign()], entry_point="second_pass"))

    assert (result["outcome"], result["message"]) == ("not restored", FOREIGN_TEXT)


def test_a_foreign_record_only_warns_on_an_edge_with_no_previous_uuids(run_module):
    result = ok(step(run_module, hub=[foreign()], previous_cluster_uuids=[], entry_point="second_pass"))

    assert (result["outcome"], result["fails"]) == ("not applicable", False)
    assert result["warnings"] == [f"{NAME}: {FOREIGN_TEXT}"]


def test_a_record_from_an_inventory_hosts_uuid_is_not_foreign(run_module):
    hosts = {EDGE: EDGE_UUID, HUB: HUB_UUID, "dr02": OTHER_UUID}

    result = ok(step(run_module, hub=[foreign(OTHER_UUID)], inventory_cluster_uuids=hosts, step21={"status": "go"}))

    assert result["outcome"] == "not applicable"
    assert "record 77" not in result["note"]


STRANDED_NOTE = (
    f"R {R_ID}, the declared relationship's records on {EDGE} and {HUB}, is DISCONNECTED on both, with no old record and no restore record at "
    f"{RECORD_PATH}. It reads as a failover by hand, which the replication_failover role owns. It is also what a crash between the old record's delete and the "
    "restore record's write leaves, or a resume from a controller without the record, or a hand delete of the old record after the reverse. "
    "If it is your own stopped restore, do one of these: (1) move the record here from the controller that wrote it, deleting it there, and "
    f"run again; (2) check that nobody has written on {HUB}:{TARGET}, since the reconnect reverts it, reconnect R {R_ID} by id with "
    "qumulo.cluster.replication_reverse and allow_data_loss, then remove any record of it on the controller that wrote it, or run the restore "
    "there once while R is ESTABLISHED, which removes the record; the next baseline applies the declared settings, and "
    "qumulo.cluster.snapshot_info lists the ownerless snapshots the record named; (3) if the old record was deleted by hand before step 7, "
    f"delete R {R_ID} on both clusters"
)
NO_REPORT = f"{EDGE}: no rebuild report on this controller, under {ARTIFACTS}/{EDGE}, so this run cannot tell a stranded restore from a failover by hand."


def stranded(run_module, **extra):
    """The one-relationship crash trace: X deleted, the record never written, R DISCONNECTED in the declared orientation."""
    return ok(step(run_module, hub=[post_target()], edge=[post_source()], **extra))


def test_a_stranded_pair_fails_from_the_second_pass_through_the_report_claim(run_module):
    result = stranded(run_module, entry_point="second_pass", report_claims={"report": REPORT, "claimed": [KEY]})

    assert (result["outcome"], result["fails"]) == ("not restored", True)
    assert result["restore_to_do"] is True
    assert (result["note"], result["message"]) == (STRANDED_NOTE, f"{STRANDED_NOTE}.")


def test_a_stranded_pair_named_in_the_second_pass_with_a_restore_to_do_is_refused(run_module):
    result = stranded(run_module, entry_point="second_pass", report_claims={"report": REPORT, "claimed": [KEY]}, named=True)

    assert refused(result) == f"{STRANDED_NOTE}."


def test_a_stranded_pair_x_deleted_by_hand_after_the_reverse_warns_from_the_playbook_with_the_three_ways_on(run_module):
    result = stranded(run_module, report_claims={"report": REPORT, "claimed": [KEY]})

    assert (result["outcome"], result["fails"]) == ("not applicable", False)
    assert result["note"] == STRANDED_NOTE
    assert result["warnings"] == [f"{NAME}: {STRANDED_NOTE}."]


def test_a_stranded_pair_only_warns_from_the_second_pass_once_a_stamp_retired_the_claim(run_module):
    result = stranded(run_module, entry_point="second_pass", report_claims={"report": REPORT, "claimed": []})

    assert (result["outcome"], result["fails"]) == ("not applicable", False)
    assert result["restore_to_do"] is False
    assert len(result["warnings"]) == 1


def test_a_stranded_pair_with_no_rebuild_report_warns_that_the_controller_cannot_tell(run_module):
    result = stranded(run_module, entry_point="second_pass")

    assert result["outcome"] == "not applicable"
    assert result["warnings"] == [f"{NAME}: {STRANDED_NOTE}.", NO_REPORT]


def test_a_stranded_pair_is_refused_with_its_note_when_named(run_module):
    assert refused(stranded(run_module, named=True)) == f"{STRANDED_NOTE}."


@pytest.mark.parametrize("state", ["RECONNECTING", "REESTABLISHING"])
def test_a_no_record_pair_reconnecting_gets_the_note_to_wait_or_run_from_the_records_controller(run_module, state):
    result = ok(step(run_module, hub=[post_target()], edge=[post_source(state)]))

    assert result["outcome"] == "not applicable"
    assert (
        "another controller's restore in its reconnect, or a reconnect by hand. Wait for it, or run the restore from the controller that holds the record"
        in result["note"]
    )


def test_r_deleted_on_the_target_alone_after_the_reverse_is_no_pair_and_goes_to_step_21s_check(run_module):
    assert ok(step(run_module, edge=[post_source()]))["reads"] == ["step21"]


def test_a_pair_with_no_record_in_two_failover_states_is_no_stranded_pair_and_goes_to_step_21s_check(run_module):
    result = ok(step(run_module, hub=[post_target("ESTABLISHED")], edge=[post_source("DISCONNECTED")]))

    assert result["reads"] == ["step21"]
    assert result["warnings"] == []


# A target that does not answer: the restore-to-do rule


UNREACHABLE = "hub01 did not answer: connection timed out"
WARNING_UNREACHABLE = f"{EDGE}: {HUB} did not answer, so this run could not check its records of {EDGE}'s relationships."


@pytest.mark.parametrize("entry_point", ["playbook", "second_pass"])
def test_an_unreachable_target_fails_from_either_entry_point_while_the_report_claims_the_relationship(run_module, entry_point):
    result = ok(
        step(run_module, target_error=UNREACHABLE, target_relationships=None, entry_point=entry_point, report_claims={"report": REPORT, "claimed": [KEY]})
    )

    command = SECOND_PASS if entry_point == "second_pass" else PLAYBOOK
    assert (result["outcome"], result["fails"]) == ("failed", True)
    assert result["message"] == f"{UNREACHABLE}. Fix the cause and run {command} again."


def test_an_unreachable_targets_error_ending_in_a_period_keeps_one(run_module):
    claims = {"report": REPORT, "claimed": [KEY]}

    result = ok(step(run_module, target_error=f"{UNREACHABLE}.", target_relationships=None, report_claims=claims))

    assert result["message"] == f"{UNREACHABLE}. Fix the cause and run {PLAYBOOK} again."


def test_an_unreachable_target_fails_while_a_restore_record_exists(run_module):
    result = ok(step(run_module, target_error=UNREACHABLE, target_relationships=None, record_text=text(restore_record())))

    assert result["outcome"] == "failed"


def test_an_unreachable_target_only_warns_once_a_stamp_retired_the_claim(run_module):
    result = ok(
        step(run_module, target_error=UNREACHABLE, target_relationships=None, entry_point="second_pass", report_claims={"report": REPORT, "claimed": []})
    )

    assert (result["outcome"], result["fails"]) == ("not applicable", False)
    assert result["warnings"] == [WARNING_UNREACHABLE]
    assert result["summary"] == f"{NAME}: not applicable: {WARNING_UNREACHABLE}"


def test_an_unreachable_target_with_no_rebuild_report_warns_that_the_controller_cannot_tell(run_module):
    result = ok(step(run_module, target_error=UNREACHABLE, target_relationships=None, entry_point="second_pass"))

    assert result["warnings"] == [WARNING_UNREACHABLE, NO_REPORT]


def test_a_relationship_with_neither_an_unreachable_target_nor_a_stranded_pair_prints_no_report_warning(run_module):
    result = ok(step(run_module, step21={"status": "go"}, entry_point="second_pass"))

    assert result["warnings"] == []


# A relationship released by the copy and the release, with no claim left, or one restored and stamped beside a sibling
# the report still claims.
STAMPED = [{"report": REPORT, "claimed": []}, {"report": REPORT, "claimed": [{**KEY, "source_path": "/home", "target_path": "/replicas/edge01-home"}]}]


@pytest.mark.parametrize("claims", STAMPED, ids=["released", "a sibling still claimed"])
def test_a_stamped_relationship_failed_over_by_hand_warns_with_the_stranded_note_and_passes(run_module, claims):
    result = stranded(run_module, entry_point="second_pass", report_claims=claims)

    assert (result["outcome"], result["fails"]) == ("not applicable", False)
    assert result["warnings"] == [f"{NAME}: {STRANDED_NOTE}."]


@pytest.mark.parametrize("claims", STAMPED, ids=["released", "a sibling still claimed"])
def test_a_stamped_relationship_whose_target_does_not_answer_warns_and_passes(run_module, claims):
    result = ok(step(run_module, target_error=UNREACHABLE, target_relationships=None, entry_point="second_pass", report_claims=claims))

    assert (result["outcome"], result["fails"]) == ("not applicable", False)
    assert result["warnings"] == [WARNING_UNREACHABLE]


# Step 4's rounds


def round_call(run_module, read, now, **extra):
    args = {
        "edge": EDGE,
        "edge_cluster_uuid": EDGE_UUID,
        "target_cluster_uuid": HUB_UUID,
        "entry_point": "second_pass",
        "inventory": "inv",
        "now": now,
        "relationship": ENTRY,
        "waiting": {"relationship_id": R_ID, "wait_started": "2026-11-02T10:00:00Z"},
        "wait_timeout": 3600,
        **extra,
    }
    # A round whose read failed passes no list.
    if read is not None:
        args["edge_relationships"] = {"target_relationships": read}
    return ok(run_module(replication_restore_step, args))


def test_a_round_lands_a_relationship_with_a_recovery_point_even_past_its_deadline(run_module):
    result = round_call(run_module, [pre_target(point=R_POINT)], "2026-11-02T12:00:00Z")

    assert (result["decision"], result["deadline"]) == ("landed", "2026-11-02T11:00:00Z")


def test_a_round_waits_before_the_deadline_and_carries_an_error_without_stopping(run_module):
    result = round_call(run_module, [pre_target(error_from_last_job="Target cluster error: Cluster is out of space.")], "2026-11-02T10:30:00Z")

    assert result["decision"] == "wait"
    assert result["fails"] is False


def test_a_round_stops_at_the_deadline_with_the_job_state_and_its_last_error(run_module):
    waiting = pre_target(job_state="REPLICATION_RUNNING", error_from_last_job="Target cluster error: Cluster is out of space.")

    result = round_call(run_module, [waiting], "2026-11-02T11:00:00Z")

    assert (result["decision"], result["outcome"], result["fails"]) == ("stopped", "stopped", True)
    assert result["message"] == (
        f"R {R_ID} had no recovery point by 2026-11-02T11:00:00Z (REPLICATION_RUNNING, last error Target cluster error: Cluster is out of space.). "
        f"{EXIT_BEFORE_RECOVERY_POINT}. {PLAYBOOK} resumes it."
    )


def test_a_round_stops_early_only_on_r_ended_with_no_recovery_point(run_module):
    result = round_call(run_module, [pre_target("ENDED", end_reason="deleted by the source")], "2026-11-02T10:05:00Z")

    assert result["message"] == (
        f"R {R_ID} ended with no recovery point (deleted by the source). Delete R on {HUB} and then on {EDGE}, empty {EDGE}:{SOURCE}, which "
        f"holds R's partial copy, and run {SECOND_PASS} again."
    )


# The run's choice of relationships


def pick_call(run_module, relationships, targets=None, names=None, entry_point="playbook", **extra):
    args = {
        "edge": EDGE,
        "previous_cluster_uuids": [OLD_UUID],
        "inventory_cluster_uuids": {EDGE: EDGE_UUID, HUB: HUB_UUID, "dr02": OTHER_UUID},
        "entry_point": entry_point,
        "inventory": "inv",
        "relationships": relationships,
        "names": names,
        "targets": targets or {},
        **extra,
    }
    return ok(run_module(replication_restore_step, args))


def held_x(record_id, source_path, target_path, point=X_POINT):
    return record(record_id, OLD_UUID, source_path, HUB_UUID, target_path, recovery_point=point)


def test_every_present_relationship_is_passed_under_all_and_an_absent_one_never(run_module):
    entries = [
        ENTRY,
        {**ENTRY, "source_path": "/home", "target_path": "/r/home"},
        {**ENTRY, "source_path": "/gone", "target_path": "/r/gone", "state": "absent"},
    ]

    result = pick_call(run_module, entries, {HUB: facts()})

    assert [(each["source_path"], each["named"]) for each in result["picked"]] == [(SOURCE, False), ("/home", False)]
    assert result["refusal"] == ""


def test_names_compare_without_the_target_paths_trailing_slash(run_module):
    entries = [ENTRY, {**ENTRY, "source_path": "/home", "target_path": "/r/home"}]

    result = pick_call(run_module, entries, {HUB: facts()}, names=[f"{HUB}:{TARGET}/"])

    assert [(each["source_path"], each["named"]) for each in result["picked"]] == [(SOURCE, True)]
    assert result["matched_names"] == [f"{HUB}:{TARGET}/"]


def test_a_name_matching_no_present_relationship_fails_the_playbooks_run(run_module):
    absent = {**ENTRY, "source_path": "/gone", "target_path": "/r/gone", "state": "absent"}

    result = pick_call(run_module, [ENTRY, absent], names=[f"{HUB}:/r/gone"])

    assert result["unmatched_names"] == [f"{HUB}:/r/gone"]
    assert result["refusal"] == (
        f"qumulo_replication_restore names {HUB}:/r/gone, which {EDGE} does not declare present. Name a present relationship out of it as "
        "<target host>:<target path>."
    )


def test_in_the_second_pass_an_edge_no_name_matches_restores_nothing_and_reports_the_unmatched_name(run_module):
    result = pick_call(run_module, [ENTRY], {HUB: facts()}, names=["dr02:/other"], entry_point="second_pass")

    assert (result["picked"], result["unmatched_names"], result["refusal"]) == ([], ["dr02:/other"], "")


def test_an_edge_with_no_previous_uuids_fails_the_playbooks_run_naming_the_host_vars_patch(run_module):
    result = pick_call(run_module, [ENTRY], previous_cluster_uuids=[])

    assert result["refusal"] == (
        f"{EDGE} has no qumulo_previous_cluster_uuids, so the restore can find no old record holding its replica. Apply host_vars_patch from "
        f"the rebuild report to host_vars/{EDGE}.yml, or, if it was re-formed outside edge_rebuild, add its old UUID to "
        "qumulo_previous_cluster_uuids, and run the restore again."
    )


def test_a_release_fails_the_playbooks_run(run_module):
    assert pick_call(run_module, [ENTRY], release=True)["refusal"] == (
        f"{EDGE}: this run carries qumulo_replication_release, which is for the baseline; the restore takes none. Run {PLAYBOOK} again without it."
    )


def test_an_edge_with_no_previous_uuids_ends_at_the_target_read_with_the_unreachable_warning(run_module):
    result = pick_call(run_module, [ENTRY], {HUB: {"error": "timed out"}}, entry_point="second_pass", previous_cluster_uuids=[])

    assert result["ends"] is True
    assert result["warnings"] == [WARNING_UNREACHABLE]
    assert result["refusal"] == ""


def test_an_edge_with_no_previous_uuids_goes_on_when_a_target_holds_a_foreign_record(run_module):
    result = pick_call(run_module, [ENTRY], {HUB: facts([], [foreign()])}, entry_point="second_pass", previous_cluster_uuids=[])

    assert result["ends"] is False


NESTED = [ENTRY, {**ENTRY, "source_path": "/projects/inner", "target_path": "/r/inner"}]
NESTED_TARGETS = {HUB: facts([], [x(), held_x("12", "/projects/inner", "/r/inner")])}


@pytest.mark.parametrize("names", [None, [f"{HUB}:{TARGET}", f"{HUB}:/r/inner"]], ids=["all", "named"])
def test_two_picked_relationships_whose_x_exists_with_nested_source_paths_fail_the_playbooks_run(run_module, names):
    result = pick_call(run_module, NESTED, NESTED_TARGETS, names=names)

    assert result["refusal"] == (
        f"{EDGE}:{SOURCE} and {EDGE}:/projects/inner are nested, and only one replica can be restored into a directory. Name the outer one in "
        f"qumulo_replication_restore, {HUB}:{TARGET}; the inner one then takes the release."
    )


def test_the_second_pass_refuses_only_the_nested_group_naming_the_playbook_with_its_name(run_module):
    entries = [*NESTED, {**ENTRY, "source_path": "/home", "target_path": "/r/home"}]

    result = pick_call(run_module, entries, NESTED_TARGETS, entry_point="second_pass")

    assert [each["source_path"] for each in result["picked"]] == ["/home"]
    command = f'ansible-playbook qumulo.cluster.replication_restore -i inv -l {EDGE} -e \'{{"qumulo_replication_restore": ["{HUB}:{TARGET}"]}}\''
    assert result["outcomes"] == [
        {
            "name": NAME,
            "outcome": "refused",
            "fails": True,
            "message": (
                f"{EDGE}:{SOURCE} and {EDGE}:/projects/inner are nested, and only one replica can be restored into a directory. Restore the "
                f"outer one first: {command}; the inner one then takes the release."
            ),
        },
        {
            "name": f"{EDGE}:/projects/inner to {HUB}:/r/inner",
            "outcome": "refused",
            "fails": True,
            "message": (
                f"{EDGE}:/projects/inner and {EDGE}:{SOURCE} are nested, and only one replica can be restored into a directory. Restore the "
                f"outer one first: {command}; the inner one then takes the release."
            ),
        },
    ]


def test_a_picked_relationship_without_x_nested_with_one_that_has_x_does_not_fail_the_run(run_module):
    result = pick_call(run_module, NESTED, {HUB: facts([], [x()])})

    assert result["refusal"] == ""
    assert len(result["picked"]) == 2


FAN_OUT = [ENTRY, {**ENTRY, "target": "dr02", "target_path": "/replicas/b"}]


def fan_out_targets(hub_point, dr02_point):
    return {
        HUB: facts([], [held_x(X_ID, SOURCE, TARGET, hub_point)]),
        "dr02": facts([], [record("13", OLD_UUID, SOURCE, OTHER_UUID, "/replicas/b", recovery_point=dr02_point)]),
    }


def fan_out_loser(loser, loser_path, winner, winner_path):
    return {
        "name": f"{EDGE}:{SOURCE} to {loser}:{loser_path}",
        "outcome": "not restored",
        "fails": True,
        "message": (
            f"{loser}:{loser_path} holds an older replica of {SOURCE} than {winner}:{winner_path}, which was restored. Release it with "
            f"allow_non_empty_target on the entry and the release ansible-playbook qumulo.cluster.baseline -i inv -l {EDGE} prints, which "
            "replaces the older replica from the restored directory."
        ),
    }


def test_equal_source_paths_restore_the_newer_recovery_point_and_the_other_is_not_restored_with_its_release(run_module):
    result = pick_call(run_module, FAN_OUT, fan_out_targets("2026-11-01T09:00:00Z", "2026-11-01T08:00:00Z"), entry_point="second_pass")

    assert [each["target_host"] for each in result["picked"]] == [HUB]
    assert result["outcomes"] == [fan_out_loser("dr02", "/replicas/b", HUB, TARGET)]


def test_equal_source_paths_with_equal_recovery_points_restore_the_target_host_that_sorts_first(run_module):
    result = pick_call(run_module, FAN_OUT, fan_out_targets("2026-11-01T09:00:00Z", "2026-11-01T09:00:00Z"), entry_point="second_pass")

    assert [each["target_host"] for each in result["picked"]] == ["dr02"]
    assert result["outcomes"] == [fan_out_loser(HUB, TARGET, "dr02", "/replicas/b")]


# A sibling restored first: its target answered with no X for it, and its restore record on the controller, or its
# restore stamp naming the newest rebuild report, shows it.


def files_of(*keys, stamp=True):
    return [f"{'done-' if stamp else ''}{key_hash(key)}.yml" for key in keys]


def stamped(*keys, report=REPORT):
    """The report claim's read when each key's restore stamp names report, the newest rebuild report."""
    return {"report": report, "claimed": [], "stamped": [key_hash(key) for key in keys]}


DR02_KEY = {"source_path": SOURCE, "target_host": "dr02", "target_cluster_uuid": OTHER_UUID, "target_path": "/replicas/b"}


@pytest.mark.parametrize("entry_point", ["playbook", "second_pass"])
def test_a_fan_out_loser_on_a_later_run_still_takes_the_release_by_the_winners_stamp(run_module, entry_point):
    # The winner on dr02 was restored, so only the hub's X is left.
    targets = {HUB: facts([], [x()]), "dr02": facts()}

    result = pick_call(run_module, FAN_OUT, targets, entry_point=entry_point, restore_files=files_of(DR02_KEY), report_claims=stamped(DR02_KEY))

    assert [each["target_host"] for each in result["picked"]] == ["dr02"]
    assert result["outcomes"] == [
        {"name": NAME, "outcome": "not restored", "fails": True, "message": released_after(HUB, TARGET, SOURCE, "dr02", "/replicas/b")}
    ]


def test_an_inner_relationship_takes_the_release_once_its_outer_sibling_is_past_xs_delete_by_its_record(run_module):
    outer = {"source_path": "/", "target_host": "dr02", "target_cluster_uuid": OTHER_UUID, "target_path": "/replicas/all"}
    entries = [ENTRY, {"source_path": "/", "target": "dr02", "target_path": "/replicas/all"}]
    targets = {HUB: facts([], [x()]), "dr02": facts()}

    result = pick_call(run_module, entries, targets, entry_point="second_pass", restore_files=files_of(outer, stamp=False))

    assert result["outcomes"] == [
        {"name": NAME, "outcome": "not restored", "fails": True, "message": released_after(HUB, TARGET, "/", "dr02", "/replicas/all")}
    ]


def test_an_outer_relationship_whose_inner_sibling_was_restored_first_is_picked_for_step_1s_copy_around_it(run_module):
    inner = {"source_path": "/projects/inner", "target_host": HUB, "target_cluster_uuid": HUB_UUID, "target_path": "/r/inner"}

    result = pick_call(run_module, NESTED, {HUB: facts([], [x()])}, restore_files=files_of(inner), report_claims=stamped(inner))

    assert [each["source_path"] for each in result["picked"]] == [SOURCE, "/projects/inner"]
    assert result["outcomes"] == []


def test_a_relationships_own_stamp_an_unrelated_ones_and_a_target_outside_the_inventory_decide_nothing(run_module):
    home = {"source_path": "/home", "target_host": HUB, "target_cluster_uuid": HUB_UUID, "target_path": "/r/home"}
    entries = [ENTRY, {**ENTRY, "source_path": "/home", "target_path": "/r/home"}, {**ENTRY, "target": "dr09", "target_path": "/r/elsewhere"}]

    result = pick_call(run_module, entries, {HUB: facts([], [x()])}, restore_files=files_of(KEY, home), report_claims=stamped(KEY, home))

    assert len(result["picked"]) == 3
    assert result["outcomes"] == []


# An outer sibling /projects to hub01 and an inner relationship /projects/inner to dr02, whose X on dr02 is live.
OUTER_INNER = [ENTRY, {**ENTRY, "source_path": "/projects/inner", "target": "dr02", "target_path": "/r/inner"}]
INNER_NAME = f"{EDGE}:/projects/inner to dr02:/r/inner"
INNER_X = record("13", OLD_UUID, "/projects/inner", OTHER_UUID, "/r/inner", recovery_point=X_POINT)


def inner_targets(hub):
    return {HUB: hub, "dr02": facts([], [INNER_X])}


def picked_names(result):
    return [each["name"] for each in result["picked"]]


@pytest.mark.parametrize("entry_point", ["playbook", "second_pass"])
def test_an_outer_sibling_whose_target_answered_with_no_x_and_whose_stamp_names_the_newest_report_was_restored_first(run_module, entry_point):
    result = pick_call(run_module, OUTER_INNER, inner_targets(facts()), entry_point=entry_point, restore_files=files_of(KEY), report_claims=stamped(KEY))

    assert picked_names(result) == [NAME]
    assert result["outcomes"] == [
        {
            "name": INNER_NAME,
            "outcome": "not restored",
            "fails": True,
            "message": released_after("dr02", "/r/inner", SOURCE, HUB, TARGET, source="/projects/inner"),
        }
    ]


def test_an_outer_siblings_stamp_naming_an_older_rebuild_report_shows_no_restore_in_this_rebuild(run_module):
    result = pick_call(run_module, OUTER_INNER, inner_targets(facts()), entry_point="second_pass", restore_files=files_of(KEY), report_claims=stamped())

    assert picked_names(result) == [NAME, INNER_NAME]
    assert result["outcomes"] == []


@pytest.mark.parametrize("stamp", [True, False], ids=["stamp naming the newest report", "record"])
def test_an_outer_sibling_whose_x_is_live_was_not_restored_whatever_the_controller_holds(run_module, stamp):
    result = pick_call(
        run_module,
        OUTER_INNER,
        inner_targets(facts([], [x()])),
        names=["dr02:/r/inner"],
        restore_files=files_of(KEY, stamp=stamp),
        report_claims=stamped(KEY),
    )

    assert picked_names(result) == [INNER_NAME]
    assert result["outcomes"] == []


@pytest.mark.parametrize("stamp", [True, False], ids=["stamp naming the newest report", "record"])
def test_an_outer_sibling_whose_target_did_not_answer_is_not_known_restored(run_module, stamp):
    targets = inner_targets({"error": "hub01 did not answer: connection refused"})

    result = pick_call(run_module, OUTER_INNER, targets, entry_point="second_pass", restore_files=files_of(KEY, stamp=stamp), report_claims=stamped(KEY))

    assert picked_names(result) == [NAME, INNER_NAME]
    assert result["outcomes"] == []


# The walkthrough's reproductions: the edge rebuilt a second time, with only the first rebuild's stamp of the outer
# relationship on the controller and both X's live.


def test_the_playbook_naming_only_the_inner_relationship_after_a_second_rebuild_picks_it(run_module):
    result = pick_call(
        run_module,
        OUTER_INNER,
        inner_targets(facts([], [x()])),
        names=["dr02:/r/inner"],
        restore_files=files_of(KEY),
        report_claims={"report": REPORT, "claimed": [KEY], "stamped": []},
    )

    assert picked_names(result) == [INNER_NAME]
    assert result["outcomes"] == []


def test_the_second_pass_with_the_outer_target_not_answering_after_a_second_rebuild_picks_the_inner_relationship(run_module):
    targets = inner_targets({"error": "hub01 did not answer: connection refused"})

    result = pick_call(
        run_module,
        OUTER_INNER,
        targets,
        entry_point="second_pass",
        restore_files=files_of(KEY),
        report_claims={"report": REPORT, "claimed": [KEY], "stamped": []},
    )

    assert picked_names(result) == [NAME, INNER_NAME]
    assert result["outcomes"] == []


def test_three_nested_relationships_are_each_refused_once_in_the_second_pass(run_module):
    entries = [*NESTED, {**ENTRY, "source_path": "/projects/inner/deep", "target_path": "/r/deep"}]
    targets = {HUB: facts([], [x(), held_x("12", "/projects/inner", "/r/inner"), held_x("14", "/projects/inner/deep", "/r/deep")])}

    result = pick_call(run_module, entries, targets, entry_point="second_pass")

    assert result["picked"] == []
    assert sorted(each["name"] for each in result["outcomes"]) == sorted(
        [NAME, f"{EDGE}:/projects/inner to {HUB}:/r/inner", f"{EDGE}:/projects/inner/deep to {HUB}:/r/deep"]
    )
    assert {each["outcome"] for each in result["outcomes"]} == {"refused"}


def test_two_relationships_to_one_target_that_does_not_answer_warn_once(run_module):
    entries = [ENTRY, {**ENTRY, "source_path": "/home", "target_path": "/r/home"}]

    result = pick_call(run_module, entries, {HUB: {"error": "timed out"}}, entry_point="second_pass", previous_cluster_uuids=[])

    assert result["warnings"] == [WARNING_UNREACHABLE]


# Inputs the module refuses, and states no step leaves


def test_two_live_records_from_previous_uuids_at_the_target_path_fail_naming_both(run_module):
    result = step(run_module, hub=[x(), record("99", OLD_UUID, "/other", HUB_UUID, TARGET)])

    assert result["failed"] is True
    assert f"2 live records from previous UUIDs of {EDGE} hold {HUB}:{TARGET} (ids {X_ID}, 99)" in result["msg"]


@pytest.mark.parametrize(
    ("extra", "words"),
    [
        ({"relationship": {"source_path": SOURCE, "target": HUB}}, "relationship lacks target_path"),
        ({"now": "yesterday"}, "now 'yesterday' is not an ISO 8601 time"),
        ({"record_text": text(restore_record(key={**KEY, "source_path": "/home"}))}, "not this relationship's"),
    ],
)
def test_inputs_the_module_cannot_decide_from_fail_it(run_module, extra, words):
    result = step(run_module, hub=[x()], **extra)

    assert result["failed"] is True
    assert words in result["msg"]


def test_in_the_second_pass_a_name_without_a_target_path_matches_nothing_and_the_others_still_pick(run_module):
    result = pick_call(run_module, [ENTRY], {HUB: facts()}, names=[HUB, f"{HUB}:{TARGET}"], entry_point="second_pass")

    assert [each["source_path"] for each in result["picked"]] == [SOURCE]
    assert (result["matched_names"], result["unmatched_names"]) == ([f"{HUB}:{TARGET}"], [HUB])


def test_a_name_without_a_target_path_fails_the_playbooks_choice(run_module):
    result = run_module(replication_restore_step, {"edge": EDGE, "entry_point": "playbook", "relationships": [ENTRY], "names": [HUB]})

    assert result["failed"] is True
    assert result["msg"] == (
        f"qumulo_replication_restore names '{HUB}', which is not <target host>:<target path>; name a relationship "
        f"by its target host and target path, such as {HUB}:/replicas/edge01."
    )


def test_a_name_with_a_relative_target_path_fails_naming_the_entry(run_module):
    result = run_module(replication_restore_step, {"edge": EDGE, "entry_point": "playbook", "relationships": [ENTRY], "names": [f"{HUB}:replicas"]})

    assert result["failed"] is True
    assert result["msg"] == (
        f"qumulo_replication_restore's entry '{HUB}:replicas': the target path 'replicas' must be an absolute path, such as /data/projects"
    )


def test_r_in_a_state_no_step_leaves_before_the_reverse_stops(run_module):
    result = ok(step(run_module, hub=[x()], hub_sources=[pre_source()], edge_targets=[pre_target("RECONNECTING", point=R_POINT)]))

    assert result["outcome"] == "stopped"
    assert f"R {R_ID} is RECONNECTING on {EDGE}, which no step of the restore leaves before the reverse" in result["message"]


def test_after_the_reverse_with_x_r_not_disconnected_on_both_sides_stops_before_step_7(run_module):
    result = ok(step(run_module, hub=[x(), post_target("DISCONNECTING")], edge=[post_source()]))

    assert result["outcome"] == "stopped"
    assert "where step 7 needs DISCONNECTED on both" in result["message"]


def test_an_unmarked_record_with_r_disconnecting_stops_short_of_step_8(run_module):
    result = ok(step(run_module, hub=[post_target("DISCONNECTING")], edge=[post_source()], record_text=text(restore_record())))

    assert result["outcome"] == "stopped"
    assert "where step 8 needs DISCONNECTED on both" in result["message"]
    assert "The way out is to finish the restore" in result["message"]


def test_an_unmarked_record_whose_r_reads_in_the_pre_reverse_orientation_is_refused_with_its_note(run_module):
    result = ok(step(run_module, hub_sources=[pre_source("DISCONNECTED")], edge_targets=[pre_target("DISCONNECTED")], record_text=text(restore_record())))

    assert f"R {R_ID} is not DISCONNECTED on both sides in the declared relationship's orientation" in refused(result)


def test_a_relationship_into_the_edge_from_a_host_outside_the_inventory_is_named_by_its_uuid(run_module):
    into = record("55", FOREIGN_UUID, "/data", EDGE_UUID, SOURCE)

    assert refused(step(run_module, hub=[x()], edge_targets=[into], **READY)).startswith(f"{FOREIGN_UUID}:/data to {EDGE}:{SOURCE} replicates into")


def test_a_round_whose_read_failed_stops_at_the_deadline_saying_r_was_not_read(run_module):
    result = round_call(run_module, None, "2026-11-02T11:00:00Z")

    assert result["message"] == f"R {R_ID} had no recovery point by 2026-11-02T11:00:00Z (not read). {EXIT_BEFORE_RECOVERY_POINT}. {PLAYBOOK} resumes it."


def test_a_round_whose_read_failed_waits_before_the_deadline(run_module):
    assert round_call(run_module, None, "2026-11-02T10:30:00Z")["decision"] == "wait"


def test_a_round_stops_at_once_on_r_missing_from_a_read_that_succeeded(run_module):
    result = round_call(run_module, [], "2026-11-02T10:05:00Z")

    assert (result["decision"], result["outcome"], result["fails"]) == ("stopped", "stopped", True)
    assert result["message"] == (
        f"R {R_ID} is missing on {EDGE}, deleted while it waited for its full sync. Delete R on {HUB} too, empty {EDGE}:{SOURCE}, which may hold "
        f"R's partial copy, and run {SECOND_PASS} again."
    )
