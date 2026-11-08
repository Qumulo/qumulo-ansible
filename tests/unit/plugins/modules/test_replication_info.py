# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_info, driven through FakeQumulo. Response bodies follow what Qumulo Core 7.10.3A returns."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import replication_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
PEER_UUID = "59052546-452f-483e-a800-0a925d3fa1bd"
SOURCE = {
    "id": "9465828e-7dcf-466e-a550-78ed0bd6b79b",
    "target_address": "203.0.113.40",
    "target_port": 3712,
    "source_root_read_only": False,
    "map_local_ids_to_nfs_ids": False,
    "replication_enabled": True,
    "replication_mode": "REPLICATION_CONTINUOUS",
    "blackout_window_timezone": "UTC",
    "blackout_windows": [{"start_hour": 22, "start_minute": 0, "end_hour": 23, "end_minute": 30, "on_days": ["EVERY_DAY"]}],
    "snapshot_policies": [],
    "state": "ESTABLISHED",
    "end_reason": "",
    "source_cluster_name": "edge01",
    "source_cluster_uuid": CLUSTER_ID,
    "source_root_path": "/projects/",
    "target_cluster_name": "dr01",
    "target_cluster_uuid": PEER_UUID.upper(),
    "target_root_path": "/replicas/edge01-projects/",
    "target_root_read_only": True,
    "job_state": "REPLICATION_NOT_RUNNING",
    "job_start_time": "",
    "recovery_point": "2026-11-01T08:21:54.728656093Z",
    "error_from_last_job": "",
    "duration_of_last_job": {"nanoseconds": "202844024"},
    "source_root_id": "106505742056649366299408859139",
    "next_active_time": "",
    "replication_job_status": None,
    "queued_snapshot_count": 0,
    "recovery_point_snapshot": None,
    "replicating_snapshot": None,
}
TARGET = {
    "id": "b3016a10-f464-4026-bbae-9a6f9e0a4118",
    "state": "ENDED",
    "end_reason": 'The relationship to target path "/replicas/hub-archive/" was deleted by the source cluster.',
    "source_cluster_name": "hub01",
    "source_cluster_uuid": PEER_UUID,
    "source_root_path": "/archive/",
    "source_root_read_only": False,
    "source_address": None,
    "source_port": None,
    "target_cluster_name": "edge01",
    "target_cluster_uuid": CLUSTER_ID,
    "target_root_path": "/replicas/hub-archive/",
    "target_root_read_only": False,
    "job_state": "REPLICATION_NOT_RUNNING",
    "job_start_time": "",
    "recovery_point": "2026-11-01T08:22:24.962641967Z",
    "error_from_last_job": "",
    "duration_of_last_job": None,
    "target_root_id": "105461042526668352992613236744",
    "replication_enabled": False,
    "replication_job_status": None,
    "recovery_point_snapshot": None,
    "lock_key": None,
}
OBJECT = {
    "id": "c0ffee00-0000-4000-8000-000000000001",
    "direction": "COPY_TO_OBJECT",
    "local_directory_id": "3",
    "object_store_address": "s3.us-west-2.amazonaws.com",
    "bucket": "edge01-archive",
    "object_folder": "projects/",
    "region": "us-west-2",
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
}
SOURCE_DESCRIBED = {
    "id": SOURCE["id"],
    "source_cluster_uuid": CLUSTER_ID,
    "target_cluster_uuid": PEER_UUID,
    "source_path": "/projects",
    "target_path": "/replicas/edge01-projects",
    "target_address": "203.0.113.40",
    "target_port": 3712,
    "state": "ESTABLISHED",
    "end_reason": None,
    "job_state": "REPLICATION_NOT_RUNNING",
    "job_start_time": None,
    "recovery_point": "2026-11-01T08:21:54.728656093Z",
    "recovery_point_snapshot_id": None,
    "error_from_last_job": None,
    "target_root_read_only": True,
    "replication_enabled": True,
    "map_local_ids_to_nfs_ids": False,
    "source_root_read_only": False,
    "blackout_windows": [{"start_hour": 22, "start_minute": 0, "end_hour": 23, "end_minute": 30, "on_days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]}],
    "blackout_window_timezone": "UTC",
}


def expect_everything(fake):
    fake.expect("GET", "/v2/replication/source-relationships/status/", response=[SOURCE])
    fake.expect("GET", "/v2/replication/target-relationships/status/", response=[TARGET])
    fake.expect("GET", "/v3/replication/object-relationships/", response=[OBJECT])


def test_default_gathers_source_target_and_object_relationships(fake_api, run_module):
    expect_everything(fake_api())

    result = run_module(replication_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["replication"]["source_relationships"] == [SOURCE_DESCRIBED]
    assert result["replication"]["target_relationships"] == [
        {
            "id": TARGET["id"],
            "source_cluster_uuid": PEER_UUID,
            "target_cluster_uuid": CLUSTER_ID,
            "source_path": "/archive",
            "target_path": "/replicas/hub-archive",
            "target_address": None,
            "target_port": None,
            "state": "ENDED",
            "end_reason": TARGET["end_reason"],
            "job_state": "REPLICATION_NOT_RUNNING",
            "job_start_time": None,
            "recovery_point": "2026-11-01T08:22:24.962641967Z",
            "recovery_point_snapshot_id": None,
            "error_from_last_job": None,
            "target_root_read_only": False,
            "replication_enabled": None,
            "map_local_ids_to_nfs_ids": None,
            "source_root_read_only": None,
            "blackout_windows": None,
            "blackout_window_timezone": None,
        }
    ]


def test_object_relationships_are_listed_without_their_credentials(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/replication/object-relationships/", response=[{**OBJECT, "secret_access_key": "never-returned"}])

    result = run_module(replication_info, {**CONNECTION, "gather_subset": ["object"]})

    assert not result.get("failed"), result.get("msg")
    expected = {key: value for key, value in OBJECT.items() if key != "access_key_id"}
    assert result["replication"] == {"object_relationships": [expected]}
    assert "AKIAIOSFODNN7EXAMPLE" not in str(result)


@pytest.mark.parametrize(
    ("subset", "path", "fact"),
    [
        ("source", "/v2/replication/source-relationships/status/", "source_relationships"),
        ("target", "/v2/replication/target-relationships/status/", "target_relationships"),
    ],
)
def test_a_subset_reads_only_its_own_list(fake_api, run_module, subset, path, fact):
    fake = fake_api()
    fake.expect("GET", path, response=[])

    result = run_module(replication_info, {**CONNECTION, "gather_subset": [subset]})

    assert not result.get("failed"), result.get("msg")
    assert result["replication"] == {fact: []}
    assert [e.path for e in fake.log if not e.background] == [path]


def test_check_mode_reads_and_changes_nothing(fake_api, run_module):
    fake = fake_api()
    expect_everything(fake)

    result = run_module(replication_info, CONNECTION, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert {e.method for e in fake.log if not e.background} == {"GET"}


# One relationship by id, as the restore's waits read it

SOURCE_STATUS_PATH = f"/v2/replication/source-relationships/{SOURCE['id']}/status"
TARGET_STATUS_PATH = f"/v2/replication/target-relationships/{TARGET['id']}/status"
NOT_FOUND = {"error_class": "relationship_manager_relationship_not_found_error", "description": "The relationship does not exist."}


def test_a_relationship_id_on_the_source_side_reads_that_record_alone_in_the_usual_shape(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SOURCE_STATUS_PATH, response=SOURCE)

    result = run_module(replication_info, {**CONNECTION, "relationship_id": SOURCE["id"], "side": "source"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["replication"] == {"relationship": SOURCE_DESCRIBED}
    assert [e.path for e in fake.log if not e.background] == [SOURCE_STATUS_PATH]


def test_a_relationship_id_on_the_target_side_reads_the_target_record_with_null_settings(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET_STATUS_PATH, response=TARGET)

    result = run_module(replication_info, {**CONNECTION, "relationship_id": TARGET["id"], "side": "target"})

    assert not result.get("failed"), result.get("msg")
    relationship = result["replication"]["relationship"]
    assert relationship["id"] == TARGET["id"]
    assert relationship["source_path"] == "/archive"
    assert relationship["state"] == "ENDED"
    assert relationship["target_address"] is None
    assert relationship["replication_enabled"] is None
    assert list(relationship) == list(SOURCE_DESCRIBED)
    assert [e.path for e in fake.log if not e.background] == [TARGET_STATUS_PATH]


def test_a_record_this_cluster_does_not_hold_on_that_side_reads_as_null(fake_api, run_module):
    # After a reverse, a cluster holds the other side's record of the same id, so a missing one is an answer, not a failure.
    fake = fake_api()
    fake.expect("GET", TARGET_STATUS_PATH, status=404, response=NOT_FOUND)

    result = run_module(replication_info, {**CONNECTION, "relationship_id": TARGET["id"], "side": "target"})

    assert not result.get("failed"), result.get("msg")
    assert result["replication"] == {"relationship": None}


def test_a_running_job_reports_its_start_time(fake_api, run_module):
    fake = fake_api()
    running = {**SOURCE, "job_state": "REPLICATION_RUNNING", "job_start_time": "2026-11-01T09:00:00.000000000Z"}
    fake.expect("GET", SOURCE_STATUS_PATH, response=running)

    result = run_module(replication_info, {**CONNECTION, "relationship_id": SOURCE["id"], "side": "source"})

    assert not result.get("failed"), result.get("msg")
    relationship = result["replication"]["relationship"]
    assert relationship["job_state"] == "REPLICATION_RUNNING"
    assert relationship["job_start_time"] == "2026-11-01T09:00:00.000000000Z"


def test_the_target_record_by_id_reports_its_recovery_point_snapshot_id(fake_api, run_module):
    # The restore's guard compares this id on the target's record between its steps 7 and 8.
    fake = fake_api()
    snapshot = {"id": 117, "name": "117_replication_from_hub01", "timestamp": "2026-11-01T08:22:24Z"}
    fake.expect("GET", TARGET_STATUS_PATH, response={**TARGET, "recovery_point_snapshot": snapshot})

    result = run_module(replication_info, {**CONNECTION, "relationship_id": TARGET["id"], "side": "target"})

    assert not result.get("failed"), result.get("msg")
    assert result["replication"]["relationship"]["recovery_point_snapshot_id"] == 117


def test_a_relationship_id_needs_a_side(fake_api, run_module):
    fake = fake_api()

    result = run_module(replication_info, {**CONNECTION, "relationship_id": SOURCE["id"]})

    assert result.get("failed")
    assert "relationship_id" in result["msg"] and "side" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_relationship_id_with_a_gather_subset_is_refused(fake_api, run_module):
    fake = fake_api()

    result = run_module(replication_info, {**CONNECTION, "relationship_id": SOURCE["id"], "side": "source", "gather_subset": ["source"]})

    assert result.get("failed")
    assert result["msg"] == "relationship_id reads one record, so leave gather_subset at its default, all"
    assert [e for e in fake.log if not e.background] == []


def test_check_mode_reads_one_relationship_and_changes_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SOURCE_STATUS_PATH, response=SOURCE)

    result = run_module(replication_info, {**CONNECTION, "relationship_id": SOURCE["id"], "side": "source"}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["replication"]["relationship"] == SOURCE_DESCRIBED
    assert {e.method for e in fake.log if not e.background} == {"GET"}
