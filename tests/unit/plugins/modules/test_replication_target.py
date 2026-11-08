# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_target, driven through FakeQumulo, with the status records of fixtures_replication.py."""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import replication_target
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, entries_path
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import REL_ID, SOURCE_STATUS, SOURCE_UUID, TARGET_STATUS, snapshot

CONNECTION = {"api_host": "dr01.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
OLD_SOURCE_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
OTHER_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
STALE_ID = "1a1a1a1a-0000-4000-8000-000000000001"
HOLDER_ID = "2b2b2b2b-0000-4000-8000-000000000002"
ENDED_ID = "3c3c3c3c-0000-4000-8000-000000000003"
OTHER_ID = "4d4d4d4d-0000-4000-8000-000000000004"
DIR_ID = "300"
ARGS = {"source_cluster_uuid": SOURCE_UUID, "source_path": "/src", "target_path": "/dst"}
TARGETS = "/v2/replication/target-relationships/status/"
SOURCES = "/v2/replication/source-relationships/status/"
SNAPSHOTS = "/v4/snapshots/status/?limit=1000"
FILE_SYSTEM = "/v1/file-system"
EVICTION = "/v1/portal/spokes/eviction-settings"
METRICS = "/v2/metrics/endpoints/default/data"
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))
NOT_FOUND = {"error_class": "relationship_manager_relationship_not_found_error", "description": "relationship not found"}
NO_ENTRY = {"error_class": "fs_no_such_entry_error", "description": "/dst does not exist"}
DIRECTORY = "FS_FILE_TYPE_DIRECTORY"


def status(record_id=REL_ID, state="AWAITING_AUTHORIZATION", *, source=SOURCE_UUID, source_path="/src/", target_path="/dst", **changes):
    """A target status record on this cluster; target_root_path reads back as sent while it awaits authorization, then with a slash."""
    record = {
        **TARGET_STATUS,
        "id": record_id,
        "state": state,
        "source_cluster_uuid": source,
        "source_root_path": source_path,
        "target_cluster_uuid": CLUSTER_ID,
        "target_root_path": target_path if state == "AWAITING_AUTHORIZATION" else target_path.rstrip("/") + "/",
        "target_root_read_only": state in ("ESTABLISHED", "DISCONNECTING"),
        "recovery_point": "" if state == "AWAITING_AUTHORIZATION" else SOURCE_STATUS["recovery_point"],
    }
    return {**record, **changes}


def reversed_source(record_id=OTHER_ID, *, source_path="/dst/", target=SOURCE_UUID, state="ESTABLISHED"):
    """A source status record on this cluster: this cluster replicating source_path to target."""
    return {
        **SOURCE_STATUS,
        "id": record_id,
        "state": state,
        "source_cluster_uuid": CLUSTER_ID,
        "source_root_path": source_path,
        "target_cluster_uuid": target,
        "target_root_path": "/restore/",
    }


def entry(path, kind="FS_FILE_TYPE_FILE"):
    return {"path": path + ("/" if kind == DIRECTORY else ""), "name": path.rsplit("/", 1)[1], "type": kind}


def expect_own(fake, record):
    fake.expect("GET", f"/v2/replication/target-relationships/{record['id']}/status", response=record)


def expect_directory(fake, files=None, *, walk=(), counted=1, symlinks=0, others=0, size=8192, path="/dst"):
    """The target directory's reads: attributes, entries, each subdirectory the walk lists, then aggregates.

    files None is a missing directory; walk lists (subdirectory, its entries) in the order the walk reads them.
    """
    if files is None:
        fake.expect("GET", attributes_path(path), status=404, response=NO_ENTRY)
        return
    fake.expect("GET", attributes_path(path), response=attributes(path, file_id=DIR_ID))
    fake.expect("GET", entries_path(path) + "?limit=1000", response={"files": list(files), "paging": {"next": ""}})
    for subdirectory, entries in walk:
        fake.expect("GET", entries_path(subdirectory) + "?limit=1000", response={"files": list(entries), "paging": {"next": ""}})
    if files:
        aggregates = {
            "path": path + "/",
            "files": [],
            "total_files": str(counted),
            "total_symlinks": str(symlinks),
            "total_other_objects": str(others),
            "total_capacity": str(size),
        }
        fake.expect("GET", f"/v1/files/{path.replace('/', '%2F')}/aggregates/", response=aggregates)


def expect_lists(fake, *targets, sources=()):
    fake.expect("GET", TARGETS, response=list(targets))
    fake.expect("GET", SOURCES, response=list(sources))


def expect_snapshots(fake, *snapshots):
    fake.expect("GET", SNAPSHOTS, response={"entries": list(snapshots), "paging": {"next": ""}})


def expect_authorize(fake, record_id=REL_ID, *, flag=False, **kwargs):
    query = "?allow-non-empty-directory=true" if flag else ""
    fake.expect("POST", f"/v2/replication/target-relationships/{record_id}/authorize{query}", **kwargs)


def expect_delete(fake, record_id, **kwargs):
    fake.expect("POST", f"/v2/replication/target-relationships/{record_id}/delete", **kwargs)


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


def rows(result):
    return [entry["row"] for entry in result["unmet"]]


def by_row(result, row):
    return next(entry for entry in result["unmet"] if entry["row"] == row)


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(replication_target, {**CONNECTION, **ARGS, **args}, check_mode=check_mode, diff=diff)


# Reading by id first


def test_a_pending_record_is_authorized_by_the_source_s_id_and_waited_for(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status())
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, diff=True, relationship_id=REL_ID, source_empty=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["relationship"]["state"] == "ESTABLISHED"
    assert result["relationship"]["target_path"] == "/dst"
    assert result["relationship"]["source_cluster_uuid"] == SOURCE_UUID
    assert result["unmet"] == []
    before, after = result["diff"]["before"], result["diff"]["after"]
    assert (before["relationship"]["state"], after["relationship"]["state"]) == ("AWAITING_AUTHORIZATION", "ESTABLISHED")
    assert before["records"] == after["records"] == []
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize"]
    assert fake_clock.sleeps == [5.0]


def test_the_authorize_never_sends_allow_fs_path_create(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status())
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True)

    assert result["changed"] is True, result.get("msg")
    assert not any("allow-fs-path-create" in write for write in writes(fake))


def test_an_established_record_returns_at_once_without_listing(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"]["state"] == "ESTABLISHED"
    assert [e.path for e in fake.log if not e.background] == [f"/v2/replication/target-relationships/{REL_ID}/status"]


@pytest.mark.parametrize("state", ["DISCONNECTING", "DISCONNECTED", "RECONNECTING", "REESTABLISHING"])
def test_a_failover_state_warns_and_changes_nothing(fake_api, run_module, state):
    fake = fake_api()
    expect_own(fake, status(state=state))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is False, result.get("msg")
    assert any(f"is {state}, a failover state" in warning for warning in result["warnings"])
    assert writes(fake) == []


def test_a_target_that_lacks_the_source_s_id_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", f"/v2/replication/target-relationships/{REL_ID}/status", status=404, response=NOT_FOUND)

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert f"holds no target record with id {REL_ID}" in result["msg"]
    assert writes(fake) == []


def test_a_record_of_another_key_under_the_source_s_id_fails(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status(source_path="/other/"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert f"{SOURCE_UUID}:/other" in result["msg"]
    assert writes(fake) == []


def test_a_live_source_record_whose_target_record_is_ended_fails_naming_the_end_reason(fake_api, run_module):
    fake = fake_api()
    reason = 'The target directory "/dst/" was deleted.'
    expect_own(fake, status(state="ENDED", end_reason=reason))
    expect_directory(fake, None)
    expect_lists(fake, status(state="ENDED", end_reason=reason))

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert rows(result) == ["ended_on_target", "target_missing"]
    assert reason in result["msg"]
    assert writes(fake) == []


# source_creates and stale records


def test_source_creates_in_check_mode_predicts_the_authorize_and_ignores_stale_records(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [])], counted=0)
    expect_lists(fake, status(STALE_ID), status(ENDED_ID, "ENDED"))
    expect_snapshots(fake)
    expect_snapshots(fake)

    result = run(run_module, check_mode=True, diff=True, source_creates=True, source_empty=False, allow_non_empty_directory=True)

    assert result["changed"] is True, result.get("msg")
    assert rows(result) == ["O1"] and by_row(result, "O1")["lifted"] is True
    assert result["relationship"]["id"] is None
    assert result["relationship"]["state"] == "ESTABLISHED"
    assert result["relationship"]["target_cluster_uuid"] == CLUSTER_ID
    assert result["diff"]["before"]["relationship"] == {}
    assert [record["id"] for record in result["diff"]["before"]["records"]] == [STALE_ID, ENDED_ID], "the diff shows the records the real run deletes"
    assert result["diff"]["after"]["records"] == []
    assert any("does not exist yet" in note for note in result["notes"])
    assert any(f"would delete target record {STALE_ID}" in note for note in result["notes"])
    assert any(f"would delete target record {ENDED_ID}" in note for note in result["notes"])
    assert writes(fake) == []


def test_source_creates_outside_check_mode_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, source_creates=True)

    assert result["failed"] is True
    assert "source_creates is for the check pass" in result["msg"]
    assert writes(fake) == []


def test_the_real_pass_prunes_stale_pending_and_ended_records_before_the_authorize(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(STALE_ID), status(ENDED_ID, "ENDED"), status())
    expect_snapshots(fake, snapshot(7, "7_replication_from_edge01", owners=[ENDED_ID]))
    expect_delete(fake, STALE_ID)
    expect_delete(fake, ENDED_ID)
    expect_snapshots(fake, snapshot(7, "7_replication_from_edge01"))
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [
        f"POST /v2/replication/target-relationships/{STALE_ID}/delete",
        f"POST /v2/replication/target-relationships/{ENDED_ID}/delete",
        f"POST /v2/replication/target-relationships/{REL_ID}/authorize",
    ]
    assert result["snapshots"] == [{"id": 7, "name": "7_replication_from_edge01"}]
    assert any("7_replication_from_edge01 (id 7)" in note and "G-REPL-SNAPSHOT" in note for note in result["notes"])


def test_check_mode_with_the_source_s_id_and_a_release_predicts_the_holder_s_delete(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID), status())
    expect_snapshots(fake, snapshot(9, "9_replication_from_edge01", owners=[HOLDER_ID]))
    expect_snapshots(fake, snapshot(9, "9_replication_from_edge01", owners=[HOLDER_ID]))

    result = run(
        run_module,
        check_mode=True,
        diff=True,
        relationship_id=REL_ID,
        source_empty=False,
        allow_non_empty_directory=True,
        release=[f"dr01:/dst@{HOLDER_ID}:O2"],
    )

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []
    assert any(f"would delete target record {HOLDER_ID}" in note for note in result["notes"])
    assert any("9_replication_from_edge01" in note and "would be left" in note for note in result["notes"])
    assert result["snapshots"] == [{"id": 9, "name": "9_replication_from_edge01"}]
    before, after = result["diff"]["before"], result["diff"]["after"]
    assert (before["relationship"]["state"], after["relationship"]["state"]) == ("AWAITING_AUTHORIZATION", "ESTABLISHED")
    assert ([record["id"] for record in before["records"]], after["records"]) == ([HOLDER_ID], [])


def test_source_creates_with_a_live_record_of_the_key_is_o6(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, [])
    expect_lists(fake, status(STALE_ID, "ESTABLISHED"))

    result = run(run_module, check_mode=True, source_creates=True)

    assert result["failed"] is True
    assert rows(result) == ["O6"]


def test_a_prune_answered_404_counts_as_done(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(STALE_ID), status())
    expect_snapshots(fake)
    expect_delete(fake, STALE_ID, status=404, response=NOT_FOUND)
    expect_snapshots(fake)
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert result["changed"] is True, result.get("msg")


def test_a_failure_after_a_prune_reports_the_change(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(STALE_ID), status())
    expect_snapshots(fake)
    expect_delete(fake, STALE_ID)
    expect_snapshots(fake)
    expect_authorize(fake, status=400, response={"error_class": "relationship_manager_target_path_not_empty_error", "description": "not empty"})

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert result["failed"] is True
    assert result["changed"] is True
    assert f"this task had already deleted target record {STALE_ID}" in result["msg"]


# Row O1, and T1100p's answer


def test_a_directory_holding_only_a_subdirectory_is_refused_as_o1_without_the_consent(fake_api, run_module):
    # research R3, observed on 2026-11-01: the server answers relationship_manager_target_path_not_empty_error to an
    # authorize into a directory holding only directories, unless allow-non-empty-directory is set.
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [entry("/dst/team-a/sub", DIRECTORY)]), ("/dst/team-a/sub", [])], counted=0)
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert result["failed"] is True
    assert rows(result) == ["O1"]
    assert "allow_non_empty_directory" in result["msg"]
    assert by_row(result, "O1")["lifted_by"] == "allow_non_empty_target"
    assert result["replaces"] == 0
    assert writes(fake) == []


def test_a_directory_holding_only_a_subdirectory_is_authorized_with_the_flag_under_the_consent(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [])], counted=0)
    expect_lists(fake, status())
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=True, allow_non_empty_directory=True)

    assert result["changed"] is True, result.get("msg")
    assert rows(result) == ["O1"], "a directory holding only directories holds no files, so O4 does not apply"


def test_an_empty_directory_is_authorized_without_the_flag_even_with_the_consent(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, allow_non_empty_directory=True)

    assert result["changed"] is True, result.get("msg")


# allow_directories_only, the restore's exception to row O1 (plan, decision 12)


def test_allow_directories_only_authorizes_a_directory_holding_only_directories_with_the_flag(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [entry("/dst/team-a/sub", DIRECTORY)]), ("/dst/team-a/sub", [])], counted=0)
    expect_lists(fake, status())
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_directories_only=True)

    assert result["changed"] is True, result.get("msg")
    o1 = by_row(result, "O1")
    assert o1["lifted"] is True
    assert o1["facts"] == {"target_path": "/dst", "holds_objects": False, "first_object": None}
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize?allow-non-empty-directory=true"]


def test_allow_directories_only_refuses_a_file_at_any_depth_and_sends_no_authorize(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [entry("/dst/team-a/notes.txt")])])
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_directories_only=True)

    assert result["failed"] is True
    assert rows(result) == ["O1"]
    o1 = by_row(result, "O1")
    assert o1["lifted"] is False
    assert o1["message"] == (
        "/dst holds /dst/team-a/notes.txt, and allow_directories_only lets the source's content replace only subdirectories; the authorize would replace it"
    )
    assert o1["facts"] == {"target_path": "/dst", "holds_objects": True, "first_object": "/dst/team-a/notes.txt"}
    assert writes(fake) == []


def test_allow_directories_only_refuses_a_link_in_the_directory_itself(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/latest", "FS_FILE_TYPE_SYMLINK")], counted=0, symlinks=1)
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_directories_only=True)

    assert result["failed"] is True
    assert by_row(result, "O1")["facts"]["first_object"] == "/dst/latest"
    assert writes(fake) == []


def test_allow_directories_only_authorizes_an_empty_directory_without_the_flag(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, allow_directories_only=True)

    assert result["changed"] is True, result.get("msg")
    assert rows(result) == []


def test_allow_directories_only_in_check_mode_reports_the_walk_without_a_write(fake_api, run_module):
    # The restore's step 1 reads whether the edge's directory holds a file from this check-mode result.
    fake = fake_api()
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [entry("/dst/team-a/notes.txt")])])
    expect_lists(fake)

    result = run(run_module, check_mode=True, source_creates=True, source_empty=False, allow_directories_only=True)

    assert result["failed"] is True
    assert by_row(result, "O1")["facts"] == {"target_path": "/dst", "holds_objects": True, "first_object": "/dst/team-a/notes.txt"}
    assert writes(fake) == []


def test_allow_directories_only_in_check_mode_predicts_the_authorize_over_directories(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [])], counted=0)
    expect_lists(fake)

    result = run(run_module, check_mode=True, source_creates=True, source_empty=False, allow_directories_only=True)

    assert result["changed"] is True, result.get("msg")
    assert by_row(result, "O1")["lifted"] is True
    assert writes(fake) == []


def test_allow_directories_only_cannot_be_given_with_allow_non_empty_directory(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, relationship_id=REL_ID, allow_directories_only=True, allow_non_empty_directory=True)

    assert result["failed"] is True
    assert "mutually exclusive" in result["msg"]
    assert writes(fake) == []


def test_the_o1_entry_reports_the_walk_without_allow_directories_only(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert by_row(result, "O1")["facts"] == {"target_path": "/dst", "holds_objects": True, "first_object": "/dst/a.txt"}


# Row O2


@pytest.mark.parametrize(
    ("source", "source_path"),
    [(OTHER_UUID, "/src/"), (SOURCE_UUID, "/old-src/")],
    ids=["another source", "the declared source with another source path"],
)
def test_a_live_record_of_another_key_at_the_target_path_is_o2(fake_api, run_module, source, source_path):
    fake = fake_api()
    holder = status(HOLDER_ID, "ESTABLISHED", source=source, source_path=source_path)
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, holder, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True)

    assert result["failed"] is True
    assert rows(result) == ["O2", "O1"]
    o2 = by_row(result, "O2")
    assert (o2["binds_to"], o2["lifted"], o2["facts"]["source_cluster_uuid"], o2["facts"]["source_path"]) == (HOLDER_ID, False, source, source_path.rstrip("/"))
    assert result["releases"] == [{"target_path": "/dst", "record_id": HOLDER_ID, "rows": ["O2"]}]
    assert writes(fake) == []


def test_a_pending_record_of_another_key_at_the_target_path_holds_nothing(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(OTHER_ID, source=OTHER_UUID), status())
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")
    assert (result["unmet"], result["holders"]) == ([], [])
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize"]


def test_a_holder_is_reported_before_the_directory_s_entries(fake_api, run_module):
    # QUIRK-021: on the same path the server answers relationship_manager_target_path_not_empty_error before the 409
    # for another relationship's hold. The module looks for holders itself, so it reports the holder and sends nothing.
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OTHER_UUID), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert result["failed"] is True
    assert rows(result) == ["O2", "O1"]
    assert result["msg"].index(HOLDER_ID) < result["msg"].index("holds entries")
    assert writes(fake) == []


def test_a_release_bound_to_the_holder_deletes_it_then_authorizes(fake_api, run_module):
    fake = fake_api()
    holder = status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID)
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, holder, status())
    expect_snapshots(fake, snapshot(9, "9_replication_from_edge01", owners=[HOLDER_ID]))
    expect_delete(fake, HOLDER_ID)
    expect_snapshots(fake, snapshot(9, "9_replication_from_edge01"))
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(
        run_module,
        relationship_id=REL_ID,
        source_empty=False,
        allow_non_empty_directory=True,
        release=[f"dr01:/dst/@{HOLDER_ID}:O2"],
        previous_source_cluster_uuids=[OLD_SOURCE_UUID],
    )

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [
        f"POST /v2/replication/target-relationships/{HOLDER_ID}/delete",
        f"POST /v2/replication/target-relationships/{REL_ID}/authorize?allow-non-empty-directory=true",
    ]
    o2 = by_row(result, "O2")
    assert o2["lifted"] is True and o2["facts"]["from_previous_source"] is True
    assert result["snapshots"] == [{"id": 9, "name": "9_replication_from_edge01"}]


@pytest.mark.parametrize(
    "release",
    [f"dr01:/dst@{OTHER_ID}:O2", f"dr01:/dst@{HOLDER_ID}:O5", f"dr01:/dst@{HOLDER_ID}:O4"],
    ids=["bound to another record", "naming another row", "naming only O4"],
)
def test_a_release_bound_to_another_record_or_naming_other_rows_lifts_nothing(fake_api, run_module, release):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OTHER_UUID), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True, release=[release])

    assert result["failed"] is True
    assert by_row(result, "O2")["lifted"] is False
    assert writes(fake) == []


def test_the_release_deletes_an_equal_path_holder_only(fake_api, run_module):
    fake = fake_api()
    nested = status(HOLDER_ID, "ESTABLISHED", source=OTHER_UUID, target_path="/dst/inner")
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, nested, status())

    result = run(run_module, relationship_id=REL_ID, release=[f"dr01:/dst@{HOLDER_ID}:O2"])

    assert result["failed"] is True
    assert rows(result) == ["O7"]
    assert writes(fake) == []


def test_holders_are_returned_as_fields(fake_api, run_module):
    fake = fake_api()
    holder = status(HOLDER_ID, "DISCONNECTED", source=OLD_SOURCE_UUID, error_from_last_job="Cannot establish a secure connection.")
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, holder, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True)

    assert result["holders"] == [
        {
            "id": HOLDER_ID,
            "source_cluster_uuid": OLD_SOURCE_UUID,
            "source_path": "/src",
            "state": "DISCONNECTED",
            "recovery_point": "2026-11-01T08:21:54.728656093Z",
            "error_from_last_job": "Cannot establish a secure connection.",
            "job_state": "REPLICATION_NOT_RUNNING",
            "target_root_read_only": False,
        }
    ]


# Row O4


def test_an_empty_source_over_a_directory_with_files_is_o4_bound_to_the_holder(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=True, allow_non_empty_directory=True, release=[f"dr01:/dst@{HOLDER_ID}:O2"])

    assert result["failed"] is True
    assert rows(result) == ["O2", "O4", "O1"]
    assert (by_row(result, "O2")["lifted"], by_row(result, "O4")["lifted"]) == (True, False)
    assert by_row(result, "O4")["binds_to"] == HOLDER_ID
    assert result["releases"] == [{"target_path": "/dst", "record_id": HOLDER_ID, "rows": ["O2", "O4"]}], "every row the check reported, O2 too"
    assert writes(fake) == []


def test_o4_binds_to_the_ended_record_without_a_holder_and_prints_one_entry_with_o5(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(ENDED_ID, "ENDED", source=OTHER_UUID), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=True, allow_non_empty_directory=True)

    assert rows(result) == ["O5", "O4", "O1"]
    assert by_row(result, "O4")["binds_to"] == ENDED_ID
    assert result["releases"] == [{"target_path": "/dst", "record_id": ENDED_ID, "rows": ["O4", "O5"]}]


def test_o4_binds_to_the_directory_s_file_id_when_the_source_creates(fake_api, run_module):
    # With O3 on the source, the role prints two entries: the source's ENDED record for O3, and this one for O4.
    fake = fake_api()
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake)

    result = run(run_module, check_mode=True, source_creates=True, source_empty=True, allow_non_empty_directory=True)

    assert result["failed"] is True
    assert by_row(result, "O4")["binds_to"] == DIR_ID
    assert result["releases"] == [{"target_path": "/dst", "record_id": DIR_ID, "rows": ["O4"]}]


def test_a_release_naming_o4_lifts_it_and_the_authorize_goes_ahead(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status())
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=True, allow_non_empty_directory=True, release=[f"dr01:/dst@{DIR_ID}:O4"])

    assert result["changed"] is True, result.get("msg")
    assert by_row(result, "O4")["lifted"] is True


def test_without_source_empty_a_directory_with_files_is_refused_as_o4(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, allow_non_empty_directory=True)

    assert result["failed"] is True
    assert rows(result) == ["O4", "O1"]
    assert "source_empty was not given" in by_row(result, "O4")["message"]


def test_o4_counts_files_at_any_depth(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/team-a", DIRECTORY)], walk=[("/dst/team-a", [entry("/dst/team-a/notes.txt")])])
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, source_empty=True, allow_non_empty_directory=True)

    assert rows(result) == ["O4", "O1"]


def test_a_release_naming_o4_bound_to_the_directory_lifts_nothing_beside_a_holder(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=True, allow_non_empty_directory=True, release=[f"dr01:/dst@{DIR_ID}:O4"])

    assert result["failed"] is True
    o4 = by_row(result, "O4")
    assert (o4["binds_to"], o4["lifted"]) == (HOLDER_ID, False)
    assert writes(fake) == []


# Rows O5, O6, O7 and O9


@pytest.mark.parametrize(("source", "of_the_key"), [(SOURCE_UUID, True), (OTHER_UUID, False)], ids=["of the key", "not of the key"])
def test_an_ended_record_over_a_directory_with_files_is_o5(fake_api, run_module, source, of_the_key):
    fake = fake_api()
    reason = 'The relationship to target path "/dst/" was deleted by the source cluster.'
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(ENDED_ID, "ENDED", source=source, end_reason=reason), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True)

    assert result["failed"] is True
    assert rows(result) == ["O5", "O1"]
    o5 = by_row(result, "O5")
    assert (o5["binds_to"], o5["facts"]["of_the_key"], o5["facts"]["end_reason"]) == (ENDED_ID, of_the_key, reason)


def test_a_release_bound_to_the_ended_record_deletes_it_then_authorizes(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(ENDED_ID, "ENDED", source=OTHER_UUID), status())
    expect_snapshots(fake)
    expect_delete(fake, ENDED_ID)
    expect_snapshots(fake)
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True, release=[f"dr01:/dst@{ENDED_ID}:O5"])

    assert result["changed"] is True, result.get("msg")
    assert writes(fake)[0] == f"POST /v2/replication/target-relationships/{ENDED_ID}/delete"


def test_a_release_bound_to_the_ended_record_naming_o2_lifts_nothing(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(fake, status(ENDED_ID, "ENDED", source=OTHER_UUID), status())

    result = run(run_module, relationship_id=REL_ID, source_empty=False, allow_non_empty_directory=True, release=[f"dr01:/dst@{ENDED_ID}:O2"])

    assert result["failed"] is True
    assert by_row(result, "O5")["lifted"] is False
    assert writes(fake) == []


def test_an_ended_record_of_another_key_over_an_empty_directory_is_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(ENDED_ID, "ENDED", source=OTHER_UUID), status())
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize"]


def test_a_live_record_of_the_key_the_source_does_not_hold_is_o6_and_nothing_lifts_it(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(STALE_ID, "ESTABLISHED"), status())

    result = run(run_module, relationship_id=REL_ID, allow_non_empty_directory=True)

    assert result["failed"] is True
    assert rows(result) == ["O6"]
    assert by_row(result, "O6")["lifted_by"] is None
    assert "state=absent" in result["msg"]


@pytest.mark.parametrize("path", ["/dst/inner", "/"], ids=["inside", "containing"])
def test_a_live_record_at_a_nested_path_is_o7(fake_api, run_module, path):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OTHER_UUID, target_path=path), status())

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert rows(result) == ["O7"]
    assert "remove that relationship first" in result["msg"]


def test_ended_and_pending_records_at_nested_paths_hold_nothing(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(OTHER_ID, "ENDED", source=OTHER_UUID, target_path="/dst/inner"), status(STALE_ID, source=OTHER_UUID, target_path="/"), status())
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")
    assert result["unmet"] == []


@pytest.mark.parametrize("source_path", ["/dst/", "/dst/sub/", "/"])
def test_a_source_record_here_to_the_declared_source_at_a_nested_path_is_o9(fake_api, run_module, source_path):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(), sources=[reversed_source(source_path=source_path, state="AWAITING_AUTHORIZATION")])

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert rows(result) == ["O9"]
    assert "reversed" in result["msg"]


def test_a_source_record_here_to_another_cluster_or_ended_is_not_o9(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status(), sources=[reversed_source(target=OTHER_UUID), reversed_source(STALE_ID, state="ENDED")])
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")


def test_records_whose_paths_read_back_empty_hold_nothing(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    empty = status(OTHER_ID, "ESTABLISHED", source=OTHER_UUID, target_root_path="")
    expect_lists(fake, empty, status(), sources=[{**reversed_source(), "source_root_path": ""}])
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")
    assert result["unmet"] == []


def test_every_unmet_row_is_reported_at_once_in_the_reporting_order(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")])
    expect_lists(
        fake,
        status(ENDED_ID, "ENDED", source=OTHER_UUID),
        status(STALE_ID, "ESTABLISHED"),
        status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID),
        status(OTHER_ID, "ESTABLISHED", source=OTHER_UUID, target_path="/dst/inner"),
        status(),
        sources=[reversed_source("5e5e5e5e-0000-4000-8000-000000000005")],
    )

    result = run(run_module, relationship_id=REL_ID, source_empty=True)

    assert result["failed"] is True
    assert rows(result) == ["O7", "O9", "O2", "O6", "O5", "O4", "O1"]
    assert result["releases"] == [
        {"target_path": "/dst", "record_id": HOLDER_ID, "rows": ["O2", "O4"]},
        {"target_path": "/dst", "record_id": ENDED_ID, "rows": ["O5"]},
    ]
    assert writes(fake) == []


# A pending directory


def test_a_pending_directory_counts_as_empty_and_o7_is_still_evaluated(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, None)
    expect_lists(fake, status(HOLDER_ID, "ESTABLISHED", source=OTHER_UUID, target_path="/"))

    result = run(run_module, check_mode=True, source_creates=True, directory_pending=True)

    assert result["failed"] is True
    assert rows(result) == ["target_missing", "O7"]
    assert by_row(result, "target_missing")["pending"] is True


def test_a_pending_directory_alone_predicts_the_authorize(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, None)
    expect_lists(fake)

    result = run(run_module, check_mode=True, source_creates=True, directory_pending=True)

    assert result["changed"] is True, result.get("msg")
    assert rows(result) == ["target_missing"]


def test_a_missing_directory_fails_outside_check_mode_even_when_pending(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, None)
    expect_lists(fake, status())

    result = run(run_module, relationship_id=REL_ID, directory_pending=True)

    assert result["failed"] is True
    assert by_row(result, "target_missing")["pending"] is False
    assert "qumulo.cluster.directory" in result["msg"]


# What the authorize replaces, and the capacity rule


def test_replaces_is_the_directory_s_aggregate_count_and_a_smaller_source_warns(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")], counted=30, symlinks=6, others=4)
    expect_lists(fake, status())
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_files=12, allow_non_empty_directory=True)

    assert result["replaces"] == 40, "files, links and other objects"
    assert any("partly restored" in warning for warning in result["warnings"])


def test_a_source_with_as_many_files_does_not_warn(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")], counted=40)
    expect_lists(fake, status())
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_files=40, allow_non_empty_directory=True)

    assert result["warnings"] == []


def file_system(free, total=1000000, cache=None):
    found = {"block_size_bytes": 4096, "total_size_bytes": str(total), "free_size_bytes": str(free), "snapshot_size_bytes": "0"}
    return found if cache is None else {**found, "portal_cache_size_bytes": str(cache)}


def metrics(cluster_type):
    return f'# TYPE qumulo_info gauge\nqumulo_info{{cluster_type="{cluster_type}",product_intent="x"}} 1\n# EOF\n'.encode()


def test_the_target_directory_s_own_size_counts_as_reusable(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [entry("/dst/a.txt")], size=5000)
    expect_lists(fake, status())
    fake.expect("GET", FILE_SYSTEM, response=file_system(free=5000))
    expect_authorize(fake, flag=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_bytes=10000, allow_non_empty_directory=True, strict_capacity=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == []


def test_a_spoke_s_cache_less_its_reserve_counts_as_reusable(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    fake.expect("GET", FILE_SYSTEM, response=file_system(free=1000, total=100000, cache=20000))
    fake.expect("GET", EVICTION, response={"free_threshold": 0.1})
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_bytes=11000, strict_capacity=True)

    assert result["changed"] is True, result.get("msg")


def test_the_spoke_s_eviction_reserve_is_not_reusable(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    fake.expect("GET", FILE_SYSTEM, response=file_system(free=1000, total=100000, cache=20000))
    fake.expect("GET", EVICTION, response={"free_threshold": 0.1})
    fake.expect("GET", METRICS, response=metrics("protected_cluster"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_bytes=11001, strict_capacity=True)

    assert result["failed"] is True
    assert rows(result) == ["capacity"]
    assert by_row(result, "capacity")["facts"]["evictable_bytes"] == 10000
    assert writes(fake) == []


def test_a_capacity_shortfall_warns_and_the_authorize_goes_ahead(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    fake.expect("GET", FILE_SYSTEM, response=file_system(free=1000))
    fake.expect("GET", METRICS, response=metrics("protected_cluster"))
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_bytes=5000)

    assert result["changed"] is True, result.get("msg")
    assert any("strict_capacity" in warning for warning in result["warnings"])


def test_a_capacity_shortfall_on_an_edge_refuses_with_strict_capacity_naming_the_clamp_and_the_rebuild(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    fake.expect("GET", FILE_SYSTEM, response=file_system(free=1000))
    fake.expect("GET", METRICS, response=metrics("unprotected_edge"))

    result = run(run_module, relationship_id=REL_ID, source_empty=False, source_bytes=5000, strict_capacity=True)

    assert result["failed"] is True
    assert rows(result) == ["capacity"]
    assert "qumulo_capacity.clamp" in result["msg"] and "edge_cache_resize.yml" in result["msg"] and "larger thin disk" in result["msg"]
    assert by_row(result, "capacity")["facts"]["reusable_bytes"] == 1000
    assert writes(fake) == []


# Waiting, and lost answers


def test_a_failover_state_during_the_wait_warns(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status(state="DISCONNECTED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")
    assert any("is DISCONNECTED, a failover state" in warning for warning in result["warnings"])


def test_a_record_that_ends_during_the_wait_fails_and_reports_the_change(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status(state="ENDED", end_reason="The relationship was deleted by the source cluster."))

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert result["changed"] is True
    assert "deleted by the source cluster" in result["msg"]


def test_without_wait_the_record_is_read_once(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status())

    result = run(run_module, relationship_id=REL_ID, wait=False)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["state"] == "AWAITING_AUTHORIZATION"
    assert fake_clock.sleeps == []


def test_a_lost_authorize_is_reconciled_by_reading_the_record(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake, raises=LOST)
    expect_own(fake, status(state="ESTABLISHED"))
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID)

    assert result["changed"] is True, result.get("msg")
    assert any("reconciled" in note for note in result["notes"])
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize"]


def test_a_lost_authorize_that_left_the_record_pending_is_never_resent(fake_api, run_module):
    fake = fake_api()
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake, raises=LOST)
    expect_own(fake, status())

    result = run(run_module, relationship_id=REL_ID)

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "still awaits authorization" in result["msg"] and "G-REPL-ONESHOT" in result["msg"]
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize"]


# The replication address's tenant (specs/014-multi-tenancy, FR-029)

TENANTS_LIST = "/v1/multitenancy/tenants/"
NETWORK = "/v3/network"
STATUS = "/v3/network/status"
FINANCE_ADDRESS = "198.51.100.21"  # a floating address of network 2


def tenant(tenant_id, name, networks=(), replication_enabled=True):
    """A tenant as GET /v1/multitenancy/tenants/ lists it."""
    switches = dict.fromkeys(("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "nfs_enabled", "smb_enabled"), True)
    return {"id": tenant_id, "name": name, **switches, "replication_enabled": replication_enabled, "networks": list(networks), "identity_config_id": 1}


def static(network_id, name, gateway, ip_ranges, floating, tenant_id):
    """A STATIC network as GET /v3/network returns it."""
    block = {"default_gateway": gateway, "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), "netmask": "255.255.255.0"}
    network = {"id": network_id, "name": name, "addresses": {"type": "STATIC", "static_addresses": block}}
    return network if tenant_id is None else {**network, "tenant_id": tenant_id}


def network_document(finance_tenant=2):
    """Network 1 of Default, and network 2, finance's unless finance_tenant says otherwise."""
    networks = [
        static(1, "Default", "203.0.113.1", ["203.0.113.11-14"], ["203.0.113.21-24"], 1),
        static(2, "finance", "198.51.100.1", ["198.51.100.11-14"], ["198.51.100.21-24"], finance_tenant),
    ]
    bond = {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]}
    vlans = [{"vlan_id": 20, "network_id": 2, "secondary_network_id": None, "mtu": None}]
    return {"managed_interfaces": {"frontend_bond_config": bond, "frontend_vlans": vlans, "backend_bond_config": None}, "frontend_networks": networks}


def expect_tenants(fake, *tenants):
    fake.expect("GET", TENANTS_LIST, response={"entries": list(tenants)})


def two_tenants(fake, finance_replication, document=None):
    expect_tenants(fake, tenant(1, "Default", [1]), tenant(2, "finance", [2], replication_enabled=finance_replication))
    fake.expect("GET", NETWORK, response=document or network_document())


def expect_a_pass(fake):
    """The reads and the authorize of a pending record into an empty directory, after the address check."""
    expect_own(fake, status())
    expect_directory(fake, [])
    expect_lists(fake, status())
    expect_authorize(fake)
    expect_own(fake, status(state="ESTABLISHED"))


def reads(fake):
    return [e.path for e in fake.log if e.method == "GET" and not e.background and e.path != "/v1/node/state"]


TWO_TENANT_REFUSAL = (
    "Replication into /dst connects to 198.51.100.21, on network 2 (finance) of tenant finance, which has replication_enabled: false. "
    "Set replication_enabled: true on finance, or set qumulo_replication_address to an address on a network of a tenant that has it on."
)


@pytest.mark.parametrize(
    ("check_mode", "args"),
    [(False, {"relationship_id": REL_ID}), (True, {"source_creates": True})],
    ids=["before_the_authorize", "in_check_mode_before_the_create"],
)
def test_an_address_on_a_network_of_a_tenant_with_replication_off_is_refused(fake_api, run_module, check_mode, args):
    fake = fake_api()
    two_tenants(fake, finance_replication=False)

    result = run(run_module, check_mode=check_mode, replication_address=FINANCE_ADDRESS, **args)

    assert result["failed"] is True
    assert result["msg"] == TWO_TENANT_REFUSAL
    assert result["changed"] is False
    assert reads(fake) == [TENANTS_LIST, NETWORK], "refused before the module reads its record, so before the authorize"
    assert writes(fake) == []


def test_the_check_runs_before_an_established_record_returns(fake_api, run_module):
    # An ESTABLISHED record returns before the lists, so a check after it would never run on an established relationship.
    fake = fake_api()
    two_tenants(fake, finance_replication=True)
    expect_own(fake, status(state="ESTABLISHED"))

    result = run(run_module, relationship_id=REL_ID, replication_address=FINANCE_ADDRESS)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert reads(fake) == [TENANTS_LIST, NETWORK, f"/v2/replication/target-relationships/{REL_ID}/status"]


def test_an_address_on_a_network_of_a_tenant_with_replication_on_passes(fake_api, run_module):
    fake = fake_api()
    two_tenants(fake, finance_replication=True)
    expect_a_pass(fake)

    result = run(run_module, relationship_id=REL_ID, source_empty=False, replication_address=FINANCE_ADDRESS)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert not result.get("warnings")
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/authorize"]


def test_an_address_on_no_range_is_not_judged_and_warns_naming_it_without_reading_status(fake_api, run_module):
    # A HOST network's host address, or one behind NAT, is in no range of the document. FakeQumulo fails a status read.
    fake = fake_api()
    two_tenants(fake, finance_replication=False)
    expect_a_pass(fake)

    result = run(run_module, relationship_id=REL_ID, source_empty=False, replication_address="192.0.2.50")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert [warning for warning in result["warnings"] if "192.0.2.50" in warning and "a DHCP network's node address" in warning], result["warnings"]
    assert STATUS not in reads(fake)


def test_an_address_on_a_network_no_tenant_holds_is_not_judged_and_warns(fake_api, run_module):
    fake = fake_api()
    two_tenants(fake, finance_replication=False, document=network_document(finance_tenant=None))
    expect_a_pass(fake)

    result = run(run_module, relationship_id=REL_ID, source_empty=False, replication_address=FINANCE_ADDRESS)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert [
        warning for warning in result["warnings"] if FINANCE_ADDRESS in warning and "network 2 (finance)" in warning and "Bind network 2 to a tenant" in warning
    ], result["warnings"]


def test_the_only_tenant_with_replication_off_is_refused_without_reading_the_network(fake_api, run_module):
    fake = fake_api()
    expect_tenants(fake, tenant(1, "Default", [1, 2], replication_enabled=False))

    result = run(run_module, relationship_id=REL_ID, replication_address=FINANCE_ADDRESS)

    assert result["failed"] is True
    assert result["msg"] == (
        "Replication into /dst connects to 198.51.100.21, but Default, the only tenant, has replication_enabled: false. "
        "Set replication_enabled: true on Default."
    )
    assert result["changed"] is False
    assert reads(fake) == [TENANTS_LIST]


def test_the_only_tenant_with_replication_on_passes_without_reading_the_network(fake_api, run_module):
    fake = fake_api()
    expect_tenants(fake, tenant(1, "Default", [1, 2]))
    expect_a_pass(fake)

    result = run(run_module, relationship_id=REL_ID, source_empty=False, replication_address=FINANCE_ADDRESS)

    assert not result.get("failed"), result.get("msg")
    assert NETWORK not in reads(fake)


def test_without_the_address_neither_the_tenants_nor_the_network_is_read(fake_api, run_module):
    # Neither read is scripted, so FakeQumulo fails the test if either is sent.
    fake = fake_api()
    expect_a_pass(fake)

    result = run(run_module, relationship_id=REL_ID, source_empty=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_an_address_that_is_not_an_ip_address_is_refused_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, relationship_id=REL_ID, replication_address="hub01.example.com")

    assert result["failed"] is True
    assert "hub01.example.com" in result["msg"] and "IP address" in result["msg"]
    assert result["changed"] is False
    assert reads(fake) == []


def test_absent_reads_nothing_for_the_address(fake_api, run_module):
    # Neither the tenants nor the network is scripted, so FakeQumulo fails the test if either is read.
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[])

    result = run(run_module, state="absent", replication_address=FINANCE_ADDRESS)

    assert not result.get("failed"), result.get("msg")
    assert reads(fake) == [TARGETS]


# The target host's own tenant writes, which a check run predicts (specs/014-multi-tenancy, FR-029)


def expect_a_predicted_create(fake):
    """The reads of a check pass before the source's create, into a directory an earlier task creates."""
    expect_directory(fake, None)
    expect_lists(fake)


def run_predicted(run_module, writes_planned, check_mode=True):
    return run(
        run_module,
        check_mode=check_mode,
        source_creates=check_mode,
        relationship_id=None if check_mode else REL_ID,
        directory_pending=True,
        replication_address=FINANCE_ADDRESS,
        planned_tenant_writes=writes_planned,
    )


@pytest.mark.parametrize(
    "planned",
    [
        [{"name": "finance", "replication_enabled": True, "smb_enabled": True}],
        [{"name": "accounts", "previous_name": "finance", "replication_enabled": True}],
    ],
    ids=["turned_on", "renamed_and_turned_on"],
)
def test_a_check_run_takes_the_target_s_own_step_that_turns_replication_on(fake_api, run_module, planned):
    fake = fake_api()
    two_tenants(fake, finance_replication=False)
    expect_a_predicted_create(fake)

    result = run_predicted(run_module, [{"name": "Default", "ssh_enabled": False}, *planned])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_check_run_takes_the_only_tenant_s_switch_from_the_target_s_own_step(fake_api, run_module):
    fake = fake_api()
    expect_tenants(fake, tenant(1, "Default", [1, 2], replication_enabled=False))
    expect_a_predicted_create(fake)

    result = run_predicted(run_module, [{"name": "Default", "replication_enabled": True}])

    assert not result.get("failed"), result.get("msg")
    assert NETWORK not in reads(fake)


def test_a_check_run_refuses_when_the_target_s_own_step_turns_replication_off(fake_api, run_module):
    fake = fake_api()
    two_tenants(fake, finance_replication=True)

    result = run_predicted(run_module, [{"name": "finance", "replication_enabled": False}])

    assert result["failed"] is True
    assert result["msg"] == TWO_TENANT_REFUSAL


def test_a_real_run_judges_the_tenants_as_it_reads_them(fake_api, run_module):
    # In a real run the target's own writes are done before the check, so the read already holds them.
    fake = fake_api()
    two_tenants(fake, finance_replication=False)

    result = run_predicted(run_module, [{"name": "finance", "replication_enabled": True}], check_mode=False)

    assert result["failed"] is True
    assert result["msg"] == TWO_TENANT_REFUSAL


# Options


def test_previous_source_uuids_holding_the_declared_source_are_refused(fake_api, run_module):
    fake_api()

    result = run(run_module, relationship_id=REL_ID, previous_source_cluster_uuids=[SOURCE_UUID.upper()])

    assert result["failed"] is True
    assert "previous_source_cluster_uuids holds the declared source UUID" in result["msg"]


@pytest.mark.parametrize("args", [{}, {"relationship_id": REL_ID, "source_creates": True}], ids=["neither", "both"])
def test_present_takes_the_source_s_id_or_source_creates(fake_api, run_module, args):
    fake_api()

    result = run(run_module, **args)

    assert result["failed"] is True
    assert "relationship_id" in result["msg"] and "source_creates" in result["msg"]


@pytest.mark.parametrize(
    ("release", "message"),
    [("dr01:/other@1:O2", "names target path /other, not /dst"), ("dr01:/dst@1:O6", "a release lifts only"), ("dr01:/dst", "is not <target host>")],
)
def test_a_release_entry_for_another_path_or_an_unreleasable_row_is_refused(fake_api, run_module, release, message):
    fake_api()

    result = run(run_module, relationship_id=REL_ID, release=[release])

    assert result["failed"] is True
    assert message in result["msg"]


# Removal


def test_absent_deletes_the_key_s_records_in_any_state_and_names_the_snapshots_left(fake_api, run_module):
    fake = fake_api()
    expect_lists_absent = [status(state="ENDED"), status(STALE_ID, "ESTABLISHED"), status(OTHER_ID, "ESTABLISHED", source=OTHER_UUID, target_path="/elsewhere")]
    fake.expect("GET", TARGETS, response=expect_lists_absent)
    expect_snapshots(fake, snapshot(4, "4_replication_from_edge01", owners=[REL_ID]))
    expect_delete(fake, REL_ID)
    expect_delete(fake, STALE_ID, status=404, response=NOT_FOUND)
    expect_snapshots(fake, snapshot(4, "4_replication_from_edge01"), snapshot(5, "5_replication_from_edge01", path=""))

    result = run(run_module, diff=True, state="absent")

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"] == {}
    assert [record["id"] for record in result["deleted"]] == [REL_ID], "a record already gone was not deleted by this task"
    assert (result["unmet"], result["releases"], result["holders"], result["replaces"]) == ([], [], [], None)
    assert result["snapshots"] == [{"id": 4, "name": "4_replication_from_edge01"}]
    assert any("replaces its content" in note for note in result["notes"])
    assert any(STALE_ID in note and "already gone" in note for note in result["notes"])
    assert [record["id"] for record in result["diff"]["before"]["records"]] == [REL_ID]
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{REL_ID}/delete", f"POST /v2/replication/target-relationships/{STALE_ID}/delete"]


def test_absent_names_ownerless_replication_snapshots_at_the_target_path(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[status(state="ENDED")])
    left = [
        snapshot(3, "3_replication_from_edge01"),
        snapshot(4, "4_replication_from_edge01", owners=[REL_ID]),
        snapshot(6, "6_daily"),
        snapshot(8, "8_replication_from_edge01", path="/other/"),
    ]
    expect_snapshots(fake, *left)
    expect_delete(fake, REL_ID)
    expect_snapshots(fake, *[{**each, "owners": []} for each in left])

    result = run(run_module, state="absent")

    assert [each["id"] for each in result["snapshots"]] == [3, 4], "the one the record owned, and the replication one owned by nothing at /dst"


def test_absent_also_deletes_ended_and_pending_records_of_previous_source_uuids(fake_api, run_module):
    fake = fake_api()
    fake.expect(
        "GET",
        TARGETS,
        response=[status(ENDED_ID, "ENDED", source=OLD_SOURCE_UUID), status(STALE_ID, source=OLD_SOURCE_UUID), status(OTHER_ID, "ENDED", source=OTHER_UUID)],
    )
    expect_snapshots(fake)
    expect_delete(fake, ENDED_ID)
    expect_delete(fake, STALE_ID)
    expect_snapshots(fake)

    result = run(run_module, state="absent", previous_source_cluster_uuids=[OLD_SOURCE_UUID])

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{ENDED_ID}/delete", f"POST /v2/replication/target-relationships/{STALE_ID}/delete"]


def test_absent_leaves_a_holder_without_its_release_and_says_so(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID)])

    result = run(run_module, state="absent", previous_source_cluster_uuids=[OLD_SOURCE_UUID])

    assert result["changed"] is False, result.get("msg")
    assert [holder["id"] for holder in result["holders"]] == [HOLDER_ID]
    assert result["holders"][0]["source_cluster_uuid"] == OLD_SOURCE_UUID
    assert any(HOLDER_ID in note for note in result["notes"])
    assert writes(fake) == []


def test_absent_deletes_an_equal_path_holder_under_its_release_only(fake_api, run_module):
    fake = fake_api()
    fake.expect(
        "GET",
        TARGETS,
        response=[status(HOLDER_ID, "ESTABLISHED", source=OLD_SOURCE_UUID), status(OTHER_ID, "ESTABLISHED", source=OTHER_UUID, target_path="/dst/inner")],
    )
    expect_snapshots(fake)
    expect_delete(fake, HOLDER_ID)
    expect_snapshots(fake)

    result = run(run_module, state="absent", release=[f"dr01:/dst@{HOLDER_ID}:O2", f"dr01:/dst@{OTHER_ID}:O2"])

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST /v2/replication/target-relationships/{HOLDER_ID}/delete"]


def test_absent_in_check_mode_deletes_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[status(state="ENDED")])
    expect_snapshots(fake, snapshot(4, "4_replication_from_edge01", owners=[REL_ID]))
    expect_snapshots(fake, snapshot(4, "4_replication_from_edge01", owners=[REL_ID]))

    result = run(run_module, check_mode=True, diff=True, state="absent")

    assert result["changed"] is True, result.get("msg")
    assert [record["id"] for record in result["deleted"]] == [REL_ID]
    assert [record["id"] for record in result["diff"]["before"]["records"]] == [REL_ID]
    assert result["snapshots"] == [{"id": 4, "name": "4_replication_from_edge01"}]
    assert any("would be left" in note for note in result["notes"])
    assert writes(fake) == []


def test_absent_with_nothing_left_changes_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[status(OTHER_ID, "ESTABLISHED", source=OTHER_UUID, target_path="/elsewhere")])

    result = run(run_module, state="absent")

    assert result["changed"] is False, result.get("msg")
    assert result["deleted"] == []
    assert "notes" not in result


def test_a_lost_delete_is_reconciled_by_reading_the_record(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[status(state="ENDED")])
    expect_snapshots(fake)
    expect_delete(fake, REL_ID, raises=LOST)
    fake.expect("GET", f"/v2/replication/target-relationships/{REL_ID}/status", status=404, response=NOT_FOUND)
    expect_snapshots(fake)

    result = run(run_module, state="absent")

    assert result["changed"] is True, result.get("msg")
    assert any("reconciled after a lost connection" in note for note in result["notes"])


def test_a_lost_delete_that_left_the_record_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGETS, response=[status(state="ENDED")])
    expect_snapshots(fake)
    expect_delete(fake, REL_ID, raises=LOST)
    expect_own(fake, status(state="ENDED"))

    result = run(run_module, state="absent")

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "still exists" in result["msg"]
