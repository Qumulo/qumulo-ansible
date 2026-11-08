# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_pair_plan: the replication_pair role's join of the source's and the target's check passes.

The check results are scripted in the shape the two modules return them, with their unmet entries built by
replication.unmet(), the shape both modules share. A refusal is scripted as the role registers it, with failed_when:
false, which keeps the msg and the unmet entries and drops failed.
"""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import unmet
from ansible_collections.qumulo.cluster.plugins.modules import replication_pair_plan
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import REL_ID as HOLDER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import SOURCE_UUID as OLD_EDGE

HOST, PATH = "qhub", "/r"
OTHER = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
ENDED_ID = "c0f3ee1c-3f3a-4bb0-bd37-6a2f06c8f5d1"
FILE_ID = "3"
DECLARED = ({"path": PATH},)


def source_ok(creates=True, entries=(), **extra):
    """A source check that predicts a create, or with creates=False finds the relationship."""
    return {"changed": creates, "creates": creates, "relationship": {"id": None if creates else HOLDER}, "unmet": list(entries), **extra}


def source_refused(*entries, msg="the replication relationship cannot be created: ..."):
    return {"changed": False, "msg": msg, "unmet": list(entries), "releases": []}


def target_ok(authorizes=True, entries=(), holders=()):
    """A target check that predicts an authorize, or with authorizes=False finds the relationship established."""
    return {"changed": authorizes, "unmet": list(entries), "holders": list(holders), "releases": [], "replaces": 0 if authorizes else None}


def target_refused(*entries, holders=(), msg="the replication relationship cannot be authorized: ..."):
    return {"changed": False, "msg": msg, "unmet": list(entries), "holders": list(holders), "releases": [], "replaces": 1}


def holder(source_uuid=OLD_EDGE, **extra):
    return {
        "id": HOLDER,
        "source_cluster_uuid": source_uuid,
        "source_path": "/projects",
        "state": "ESTABLISHED",
        "recovery_point": "2026-11-01T08:21:54Z",
        "error_from_last_job": None,
        "job_state": "REPLICATION_NOT_RUNNING",
        "target_root_read_only": True,
        **extra,
    }


def o1(lifted=False):
    return unmet("O1", f"{PATH} holds entries; set allow_non_empty_target", facts={"target_path": PATH}, lifted=lifted)


def o2(found, lifted=False):
    """O2 for a holder, from a previous source UUID when it is OLD_EDGE's, as the target reports it with OLD_EDGE previous."""
    facts = {**found, "from_previous_source": found["source_cluster_uuid"] == OLD_EDGE}
    return unmet("O2", f"relationship {found['id']} holds {PATH}", binds_to=found["id"], facts=facts, lifted=lifted)


def o3(lifted=False):
    return unmet("O3", f"source record {ENDED_ID} is ENDED", binds_to=ENDED_ID, facts={"id": ENDED_ID}, lifted=lifted)


def o4(binds_to=FILE_ID, lifted=False):
    facts = {"source_empty": True, "target_path": PATH}
    return unmet("O4", f"the source directory holds no files, and {PATH} holds files", binds_to=binds_to, facts=facts, lifted=lifted)


def o5(record_id=ENDED_ID, lifted=False):
    facts = {"id": record_id, "source_cluster_uuid": OLD_EDGE, "source_path": "/projects", "end_reason": "deleted by the source", "of_the_key": True}
    return unmet("O5", f"ENDED relationship {record_id} lies over {PATH}", binds_to=record_id, facts=facts, lifted=lifted)


def o7():
    return unmet("O7", "relationship 77 holds /r/inner, inside /r; remove that relationship first", facts={"id": "77"})


def plan(run_module, source, target, release=(), previous=(), allow=False, directories=DECLARED, **extra):
    args = {
        "source": source,
        "target": target,
        "release": list(release),
        "target_host": HOST,
        "target_path": PATH,
        "previous_source_cluster_uuids": list(previous),
        "allow_non_empty_target": allow,
        "target_directories": list(directories),
        **extra,
    }
    result = run_module(replication_pair_plan, args)
    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    return result


def test_nothing_unmet_goes_ahead_with_a_create(run_module):
    result = plan(run_module, source_ok(), target_ok())

    assert result["status"] == "go"
    assert result["message"] == ""
    assert (result["unmet"], result["blocking"], result["releases"], result["lifting_nothing"], result["failures"]) == ([], [], [], [], [])
    assert result["pairing"] is True


@pytest.mark.parametrize(
    ("creates", "authorizes", "pairing"),
    [(True, True, True), (False, True, True), (False, False, False)],
)
def test_pairing_is_due_for_a_create_or_an_authorize(run_module, creates, authorizes, pairing):
    result = plan(run_module, source_ok(creates=creates), target_ok(authorizes=authorizes))

    assert result["pairing"] is pairing


def test_pending_conditions_do_not_stop_the_check(run_module):
    source_missing = unmet("source_missing", "/projects does not exist yet", facts={}, pending=True)
    target_missing = unmet("target_missing", f"{PATH} does not exist yet", facts={}, pending=True)

    result = plan(run_module, source_ok(entries=[source_missing]), target_ok(entries=[target_missing]))

    assert result["status"] == "go"
    assert [(entry["row"], entry["side"]) for entry in result["unmet"]] == [("source_missing", "source"), ("target_missing", "target")]
    assert result["blocking"] == []


def test_every_unmet_condition_of_both_sides_is_reported_in_one_order(run_module):
    o8 = unmet("O8", "this cluster is the target of relationship 8 from the declared target", facts={"id": "8"})
    misdirected = unmet("misdirected", "source record 5 reaches another inventory cluster", facts={"id": "5"})
    capacity = unmet("capacity", "the source directory holds more than the target can reuse", facts={})

    result = plan(
        run_module,
        source_refused(o8, misdirected),
        target_refused(o1(), o7(), o4(), capacity),
    )

    assert result["status"] == "refused"
    assert [(entry["row"], entry["side"]) for entry in result["unmet"]] == [
        ("misdirected", "source"),
        ("capacity", "target"),
        ("O8", "source"),
        ("O7", "target"),
        ("O4", "target"),
        ("O1", "target"),
    ]
    assert result["blocking"] == result["unmet"]
    assert result["message"].startswith(f"{HOST}:{PATH} is refused before any write on either cluster: ")
    for entry in result["unmet"]:
        assert entry["message"] in result["message"]
    assert result["failures"] == []


def test_a_holder_from_a_previous_source_uuid_holds_the_relationship(run_module):
    found = holder()

    result = plan(
        run_module,
        source_ok(),
        target_refused(o2(found), o4(binds_to=HOLDER), o1(), holders=[found]),
        previous=[OLD_EDGE],
    )

    assert result["status"] == "held"
    assert result["releases"] == [f"{HOST}:{PATH}@{HOLDER}:O2,O4"]
    assert result["holders"] == [found]
    message = result["message"]
    assert message.startswith(f"{HOST}:{PATH} is held: its replica may be the only copy")
    assert f"Record {HOLDER} from {OLD_EDGE}:/projects, a previous UUID of the source, holds it (ESTABLISHED" in message
    assert "recovery point 2026-11-01T08:21:54Z" in message
    assert "copy the replica back into the source directory" in message
    assert f'"qumulo_replication_release": ["{HOST}:{PATH}@{HOLDER}:O2,O4"]' in message
    # The entry lacks allow_non_empty_target, and the directory holds the replica.
    assert "allow_non_empty_target: true on the relationship's entry" in message
    assert "state: absent and run with the same release, which deletes the holder" in message


@pytest.mark.parametrize("state", ["DISCONNECTED", "RECONNECTING"])
def test_a_held_relationship_whose_holder_failed_over_says_the_replica_is_writable(run_module, state):
    found = holder(state=state, target_root_read_only=False)

    result = plan(run_module, source_ok(), target_refused(o2(found), o4(binds_to=HOLDER), holders=[found]), previous=[OLD_EDGE])

    assert result["status"] == "held"
    assert f"holds it ({state}," in result["message"]
    assert "A failover by hand made the replica writable, so it may be in use" in result["message"]


def test_a_held_relationship_whose_holder_is_established_says_nothing_of_a_failover(run_module):
    found = holder()

    result = plan(run_module, source_ok(), target_refused(o2(found), o4(binds_to=HOLDER), holders=[found]), previous=[OLD_EDGE])

    assert "failover" not in result["message"]


def test_an_empty_source_over_a_full_target_with_no_holder_is_held_naming_the_copy_and_the_release(run_module):
    # No holder and no ENDED record, so O4 binds to the target directory's file id, and the entry lacks
    # allow_non_empty_target, so O1 is unmet too.
    result = plan(run_module, source_ok(), target_refused(o4(), o1()), previous=[OLD_EDGE])

    assert result["status"] == "held"
    assert result["releases"] == [f"{HOST}:{PATH}@{FILE_ID}:O4"]
    message = result["message"]
    assert "No record holds it, but the source directory holds no files while the target directory does (O4)" in message
    assert "copy the replica back into the source directory" in message
    assert f"{HOST}:{PATH}@{FILE_ID}:O4" in message
    assert "allow_non_empty_target: true on the relationship's entry" in message
    assert "To keep the replica as plain data instead, set the entry to state: absent." in message


def test_the_consent_is_not_named_again_once_the_entry_gives_it(run_module):
    result = plan(run_module, source_ok(), target_refused(o4(), o1(lifted=True)), previous=[OLD_EDGE], allow=True)

    assert result["status"] == "held"
    assert "allow_non_empty_target: true on the relationship's entry" not in result["message"]


def test_a_holder_from_another_cluster_beside_o4_on_a_rebuilt_source_is_held_naming_that_holder(run_module):
    # Held because of O4; the record that holds the directory is another cluster's, and the message says so.
    found = holder(source_uuid=OTHER)

    result = plan(run_module, source_ok(), target_refused(o2(found), o4(binds_to=HOLDER), o1(lifted=True), holders=[found]), previous=[OLD_EDGE], allow=True)

    assert result["status"] == "held"
    assert result["releases"] == [f"{HOST}:{PATH}@{HOLDER}:O2,O4"]
    assert f"Record {HOLDER} from {OTHER}:/projects, another cluster, holds it" in result["message"]
    assert "No record holds it" not in result["message"]


def test_without_previous_source_uuids_nothing_is_held(run_module):
    result = plan(run_module, source_ok(), target_refused(o2(holder()), o4(binds_to=HOLDER)))

    assert result["status"] == "refused"
    assert result["releases"] == [f"{HOST}:{PATH}@{HOLDER}:O2,O4"]
    assert f'"qumulo_replication_release": ["{HOST}:{PATH}@{HOLDER}:O2,O4"]' in result["message"]


def test_a_holder_from_another_source_is_refused_not_held(run_module):
    found = holder(source_uuid=OTHER)

    result = plan(run_module, source_ok(), target_refused(o2(found), holders=[found]), previous=[OLD_EDGE])

    assert result["status"] == "refused"


def test_a_held_relationship_reports_its_other_refusals_too(run_module):
    result = plan(run_module, source_ok(), target_refused(o7(), o4()), previous=[OLD_EDGE])

    assert result["status"] == "held"
    assert "remove that relationship first" in result["message"]


def test_a_release_that_lifts_both_rows_leaves_the_relationship_not_held(run_module):
    found = holder()
    release = [f"{HOST}:{PATH}@{HOLDER}:O2,O4"]

    result = plan(
        run_module,
        source_ok(),
        target_ok(entries=[o2(found, lifted=True), o4(binds_to=HOLDER, lifted=True), o1(lifted=True)], holders=[found]),
        release=release,
        previous=[OLD_EDGE],
        allow=True,
    )

    assert result["status"] == "go"
    assert result["releases"] == []
    assert result["lifting_nothing"] == []


def test_a_release_lifts_only_the_rows_it_names(run_module):
    # Spec User Story 3, scenario 7: a release naming O2 only, over an empty source.
    found = holder()

    result = plan(
        run_module,
        source_ok(),
        target_refused(o2(found, lifted=True), o4(binds_to=HOLDER), o1(lifted=True), holders=[found]),
        release=[f"{HOST}:{PATH}@{HOLDER}:O2"],
        previous=[OLD_EDGE],
        allow=True,
    )

    assert result["status"] == "held"
    assert [entry["row"] for entry in result["blocking"]] == ["O4"]
    # The entry lifts O2, so it is not one to remove; the printed one carries both rows.
    assert result["lifting_nothing"] == []
    assert result["releases"] == [f"{HOST}:{PATH}@{HOLDER}:O2,O4"]


def test_a_release_that_lifts_nothing_refuses_naming_it(run_module):
    stale = f"{HOST}:{PATH}@{OTHER}:O2"

    result = plan(run_module, source_ok(), target_ok(), release=[stale])

    assert result["status"] == "refused"
    assert result["lifting_nothing"] == [stale]
    assert f"release entry {stale!r} lifts nothing in this run; a release is for one run only, so remove it" in result["message"]


def test_a_release_naming_a_reported_row_it_does_not_lift_lifts_nothing(run_module):
    # O3 needs recreate as well as the release, so the release alone lifts nothing.
    entry = f"{HOST}:{PATH}@{ENDED_ID}:O3"

    result = plan(run_module, source_refused(o3()), target_ok(), release=[entry])

    assert result["lifting_nothing"] == [entry]
    assert result["status"] == "refused"


def test_a_release_that_lifts_a_row_on_the_source_alone_lifts_something(run_module):
    release = [f"{HOST}:{PATH}@{ENDED_ID}:O3"]

    result = plan(run_module, source_ok(entries=[o3(lifted=True)]), target_ok(), release=release)

    assert result["status"] == "go"
    assert result["lifting_nothing"] == []
    assert result["target_release"] == [f"{HOST}:{PATH}@{ENDED_ID}:O3,O5"]


def test_a_release_is_not_called_spent_when_a_check_failed_before_it_could_report_the_row(run_module):
    # The target did not answer, so it reported no O2; the release may still be the right one.
    release = [f"{HOST}:{PATH}@{HOLDER}:O2"]

    result = plan(run_module, source_ok(), {"changed": False, "msg": "the cluster did not answer"}, release=release)

    assert result["status"] == "refused"
    assert result["failures"] == ["the target check failed: the cluster did not answer"]
    assert result["lifting_nothing"] == []
    assert "remove it" not in result["message"]


def test_o3_with_o4_prints_two_entries(run_module):
    # Spec FR-013: one entry bound to the source's ENDED record, one bound as O4 says.
    result = plan(run_module, source_refused(o3()), target_refused(o4()))

    assert sorted(result["releases"]) == sorted([f"{HOST}:{PATH}@{ENDED_ID}:O3", f"{HOST}:{PATH}@{FILE_ID}:O4"])


def test_the_targets_o5_on_the_record_the_source_reports_under_o3_is_left_out(run_module):
    # Under source_creates, the target reports its ENDED copy of the source's O3 record as O5; the record is O3's.
    result = plan(run_module, source_refused(o3()), target_refused(o5()))

    assert [(entry["row"], entry["side"]) for entry in result["unmet"]] == [("O3", "source")]
    assert result["releases"] == [f"{HOST}:{PATH}@{ENDED_ID}:O3"]


def test_an_o5_on_another_record_is_kept_beside_o3(run_module):
    other = "a1d5c8f0-0b44-4f0e-8f3e-1c7b5a9d2e10"

    result = plan(run_module, source_refused(o3()), target_refused(o5(other)))

    assert [entry["row"] for entry in result["unmet"]] == ["O3", "O5"]
    assert sorted(result["releases"]) == sorted([f"{HOST}:{PATH}@{ENDED_ID}:O3", f"{HOST}:{PATH}@{other}:O5"])


def test_o4_bound_to_the_o3_record_joins_it_in_one_entry(run_module):
    # The target binds O4 to its ENDED copy of the O3 record, the record O5 would have named.
    result = plan(run_module, source_refused(o3()), target_refused(o5(), o4(binds_to=ENDED_ID)))

    assert [entry["row"] for entry in result["unmet"]] == ["O3", "O4"]
    assert result["releases"] == [f"{HOST}:{PATH}@{ENDED_ID}:O3,O4"]


def test_the_o3_release_lifts_both_sides_and_goes_ahead(run_module):
    release = [f"{HOST}:{PATH}@{ENDED_ID}:O3"]

    result = plan(run_module, source_ok(entries=[o3(lifted=True)]), target_ok(entries=[o5(lifted=True)]), release=release)

    assert result["status"] == "go"
    assert result["lifting_nothing"] == []
    assert result["target_release"] == [f"{HOST}:{PATH}@{ENDED_ID}:O3,O5"]


def test_the_target_release_adds_o5_only_to_entries_that_name_o3(run_module):
    release = [f"{HOST}:{PATH}/@{ENDED_ID}:O3", f"{HOST}:{PATH}@{HOLDER}:O2,O4", f"{HOST}:{PATH}@{OTHER}:O3,O5"]

    result = run_module(replication_pair_plan, {"release": release, "target_host": HOST, "target_path": PATH})

    assert result["target_release"] == [f"{HOST}:{PATH}@{ENDED_ID}:O3,O5", release[1], release[2]]
    # Without the checks, only the release forms and the directories.
    assert "status" not in result
    assert (result["target_declared"], result["ancestors"]) == (False, [])


@pytest.mark.parametrize(
    ("result_key", "side"),
    [("source", "source"), ("target", "target")],
)
def test_a_check_that_failed_for_another_reason_refuses_with_its_message(run_module, result_key, side):
    failed = {"failed": True, "msg": "2 records match the relationship (ids 1, 2); delete all but one"}
    checks = {"source": source_ok(), "target": target_ok(), result_key: failed}

    result = plan(run_module, checks["source"], checks["target"])

    assert result["status"] == "refused"
    assert result["failures"] == [f"the {side} check failed: 2 records match the relationship (ids 1, 2); delete all but one"]
    assert result["failures"][0] in result["message"]


def test_a_failure_registered_with_failed_when_false_still_refuses(run_module):
    # The role registers both checks with failed_when: false, which keeps the msg and drops failed.
    result = plan(run_module, {"changed": False, "msg": "the cluster did not answer"}, target_ok())

    assert result["status"] == "refused"
    assert result["failures"] == ["the source check failed: the cluster did not answer"]


def test_a_refusal_registered_with_ignore_errors_counts_the_same(run_module):
    result = plan(run_module, source_ok(), {**target_refused(o1()), "failed": True})

    assert result["status"] == "refused"
    assert [entry["row"] for entry in result["blocking"]] == ["O1"]
    assert result["failures"] == []


def test_a_refusal_with_only_lifted_entries_is_a_failure(run_module):
    result = plan(run_module, source_ok(), {"failed": True, "msg": "the cluster did not answer", "unmet": [o1(lifted=True)]}, allow=True)

    assert result["failures"] == ["the target check failed: the cluster did not answer"]


def test_the_consent_counts_only_for_a_declared_target_directory(run_module):
    result = plan(run_module, source_ok(), target_ok(entries=[o1(lifted=True)]), allow=True, directories=[{"path": "/elsewhere"}])

    assert result["status"] == "refused"
    assert f"allow_non_empty_target is accepted only for a target directory the target host declares, and {HOST}'s" in result["message"]
    assert result["target_declared"] is False


def test_a_declared_target_directory_accepts_the_consent(run_module):
    result = plan(run_module, source_ok(), target_ok(entries=[o1(lifted=True)]), allow=True, directories=[{"path": PATH + "/"}])

    assert result["status"] == "go"
    assert result["target_declared"] is True


def test_the_declared_ancestors_come_outermost_first(run_module):
    directories = [
        {"path": "/a/b/c"},
        {"path": "/a/"},
        {"path": "/x"},
        {"path": "/a/b"},
        {"path": "/a/bc"},
        {"path": "/a/gone", "state": "absent"},
        {"path": "/"},
    ]

    result = run_module(replication_pair_plan, {"target_host": HOST, "target_path": "/a/b/c/", "target_directories": directories})

    assert result["ancestors"] == [{"path": "/a"}, {"path": "/a/b"}]
    assert result["target_declared"] is True


def test_a_declared_directory_without_a_path_fails_with_a_message(run_module):
    result = run_module(replication_pair_plan, {"target_host": HOST, "target_path": PATH, "target_directories": [{"mode": "0755"}]})

    assert result["failed"]
    assert "qumulo_directories entry without a path" in result["msg"]


def test_a_directory_declared_absent_is_not_declared(run_module):
    result = run_module(replication_pair_plan, {"target_host": HOST, "target_path": PATH, "target_directories": [{"path": PATH, "state": "absent"}]})

    assert result["target_declared"] is False


def test_a_release_for_another_relationship_fails(run_module):
    result = run_module(
        replication_pair_plan,
        {"source": source_ok(), "target": target_ok(), "release": [f"dr02:{PATH}@{HOLDER}:O2"], "target_host": HOST, "target_path": PATH},
    )

    assert result["failed"]
    assert f"names dr02:{PATH}, not {HOST}:{PATH}" in result["msg"]


def test_a_malformed_release_fails(run_module):
    result = run_module(replication_pair_plan, {"release": [f"{HOST}:{PATH}:O2"], "target_host": HOST, "target_path": PATH})

    assert result["failed"]
    assert "is not <target host>:<target path>@<record id>:<rows>" in result["msg"]


def test_a_removal_with_a_release_that_deleted_its_holder(run_module):
    removed = {"changed": True, "deleted": [{"id": "own"}, {"id": HOLDER}], "holders": []}

    result = run_module(
        replication_pair_plan,
        {"removed": removed, "release": [f"{HOST}:{PATH}@{HOLDER}:O2"], "target_host": HOST, "target_path": PATH},
    )

    assert (result["status"], result["message"], result["lifting_nothing"]) == ("go", "", [])
    assert "unmet" not in result


def test_a_removal_with_a_release_that_deleted_nothing_refuses(run_module):
    stale = f"{HOST}:{PATH}@{HOLDER}:O2"
    removed = {"changed": False, "deleted": [], "holders": []}

    result = run_module(replication_pair_plan, {"removed": removed, "release": [stale], "target_host": HOST, "target_path": PATH})

    assert result["status"] == "refused"
    assert result["lifting_nothing"] == [stale]
    assert result["message"] == f"{HOST}:{PATH}: release entry {stale!r} lifts nothing in this run; a release is for one run only, so remove it"


def test_previous_source_uuids_compare_without_case(run_module):
    found = holder()

    result = plan(run_module, source_ok(), target_refused(o2(found), holders=[found]), previous=[OLD_EDGE.upper()])

    assert result["status"] == "held"


# The restore form (spec User Story 3, scenario 3): a held relationship X's facts allow to restore names the restore
# in place of the copy and the release.

EDGE, SOURCE_PATH, NEW_EDGE = "edge01", "/projects", "a7d1e0f4-2b9c-4e61-8f3a-5c0d9b4e2a17"
LICENSED = (
    f"The second pass restores it: once the license applies, ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l {EDGE} "
    f"copies the replica back into {SOURCE_PATH} and reverses the relationship, so that it runs from {NEW_EDGE} again. If {EDGE} runs "
    f"without a license, add license: none to {EDGE}'s qumulo_capacity, keeping its other keys, such as clamp and free_threshold."
)
UNLICENSED = (
    f"The second pass restores it: ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l {EDGE} copies the replica back "
    f"into {SOURCE_PATH} and reverses the relationship, so that it runs from {NEW_EDGE} again. {EDGE} runs without a license, so nothing "
    "waits for one."
)
PLAYBOOK = (
    f"ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l {EDGE} restores it: it copies the replica back into "
    f"{SOURCE_PATH} and reverses the relationship, so that it runs from {EDGE}'s current UUID again."
)


def restorable(run_module, found=None, rule="expected", address="203.0.113.10", source=None, release=(), entries=None):
    """The plan of a relationship held by found, X, over an empty source directory, with the restore's options."""
    found = holder() if found is None else found
    if entries is None:
        entries = [o2(found), o4(binds_to=HOLDER), o1(lifted=True)]
    # An option the role does not know is omitted, as default(omit) leaves it.
    options = {"source_license_rule": rule, "source_replication_address": address}
    return plan(
        run_module,
        source_ok(source_empty=True) if source is None else source,
        target_refused(*entries, holders=[found]),
        release=release,
        previous=[OLD_EDGE],
        allow=True,
        source_host=EDGE,
        source_path=SOURCE_PATH,
        source_cluster_uuid=NEW_EDGE,
        **{name: value for name, value in options.items() if value is not None},
    )


@pytest.mark.parametrize(
    ("rule", "sentence"),
    [("expected", LICENSED), ("none", UNLICENSED), ("outside_edges", PLAYBOOK)],
    ids=["edge_expecting_a_license", "license_none_edge", "host_outside_qumulo_edges"],
)
def test_a_holder_that_allows_the_restore_names_it_after_the_holders_sentence(run_module, rule, sentence):
    result = restorable(run_module, rule=rule)

    assert result["status"] == "held"
    assert result["restore_form"] is True
    opening = (
        f"{HOST}:{PATH} is held: its replica may be the only copy of the rebuilt source's data, so nothing is written on either cluster. "
        f"Record {HOLDER} from {OLD_EDGE}:/projects, a previous UUID of the source, holds it (ESTABLISHED, recovery point "
        "2026-11-01T08:21:54Z, last error none). "
    )
    # The copy's release is gone with the sentence it was in, so the plain-data part prints it.
    keep = (
        " To keep the replica as plain data instead, set the entry to state: absent and run with this release, which deletes the holder: "
        f"""-e '{{"qumulo_replication_release": ["{HOST}:{PATH}@{HOLDER}:O2,O4"]}}'. Unmet: """
    )
    assert result["message"].startswith(opening + sentence + keep)
    assert "To replicate again" not in result["message"]
    assert result["releases"] == [f"{HOST}:{PATH}@{HOLDER}:O2,O4"]


@pytest.mark.parametrize(
    "case",
    [
        {"found": holder(state="DISCONNECTED", target_root_read_only=False)},
        {"found": holder(target_root_read_only=False)},
        {"found": holder(recovery_point=None)},
        {"address": "edge01.example.com"},
        {"address": None},
        {"source": source_ok(source_empty=False)},
        {"source": source_ok()},
        {"rule": None},
    ],
    ids=["failed_over", "writable", "no_recovery_point", "address_a_name", "no_address", "source_holds_a_file", "source_unread", "no_rule"],
)
def test_a_holder_that_does_not_allow_the_restore_keeps_the_copy_and_the_release(run_module, case):
    result = restorable(run_module, **case)

    assert result["status"] == "held"
    assert result["restore_form"] is False
    assert "To replicate again, copy the replica back into the source directory" in result["message"]
    assert f'"qumulo_replication_release": ["{HOST}:{PATH}@{HOLDER}:O2,O4"]' in result["message"]
    assert "set the entry to state: absent and run with the same release, which deletes the holder." in result["message"]
    assert "restores it" not in result["message"]


def test_a_hold_by_another_clusters_record_keeps_the_copy_and_the_release(run_module):
    # Held by O4 alone: the record over the directory is not one from a previous UUID of the source, so it is no X.
    found = holder(source_uuid=OTHER)

    result = restorable(run_module, found=found, entries=[o2(found), o4(binds_to=HOLDER), o1(lifted=True)])

    assert result["status"] == "held"
    assert result["restore_form"] is False
    assert "To replicate again" in result["message"]
    assert "run with the same release, which deletes the holder." in result["message"]


def test_a_release_that_lifts_the_holders_row_keeps_the_copy_and_the_release(run_module):
    # Spec User Story 3, scenario 7's check run: the release names O2, so the run is the copy's, and O4 still holds it.
    found = holder()

    result = restorable(
        run_module,
        found=found,
        entries=[o2(found, lifted=True), o4(binds_to=HOLDER), o1(lifted=True)],
        release=[f"{HOST}:{PATH}@{HOLDER}:O2"],
    )

    assert result["status"] == "held"
    assert result["restore_form"] is False
    assert f'"qumulo_replication_release": ["{HOST}:{PATH}@{HOLDER}:O2,O4"]' in result["message"]


def test_a_relationship_that_is_not_held_has_no_restore_form(run_module):
    result = plan(
        run_module,
        source_ok(),
        target_ok(),
        source_host=EDGE,
        source_path=SOURCE_PATH,
        source_cluster_uuid=NEW_EDGE,
        source_license_rule="expected",
        source_replication_address="203.0.113.10",
    )

    assert result["status"] == "go"
    assert result["restore_form"] is False


def test_the_license_rule_needs_what_the_restore_form_names(run_module):
    result = run_module(
        replication_pair_plan, {"source": source_ok(), "target": target_ok(), "target_host": HOST, "target_path": PATH, "source_license_rule": "none"}
    )

    assert result["failed"]
    assert "source_license_rule" in result["msg"]
