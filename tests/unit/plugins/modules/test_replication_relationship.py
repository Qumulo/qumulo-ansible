# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_relationship, driven through FakeQumulo. Response bodies follow what Qumulo Core 7.10.3A returns."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import replication_relationship
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import (
    ADDRESS,
    REL_ID,
    SOURCE_STATUS,
    TARGET_STATUS,
    TARGET_UUID,
    WINDOW,
    snapshot,
)

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
OLD_TARGET_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
OTHER_HOST_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
UNKNOWN_UUID = "0f6b0c39-3f43-4c1b-9b7e-2a3c4d5e6f70"
FLOATING = "203.0.113.41"
NAT = "198.51.100.7"
NEW_ID = "1c7e3b52-6a0d-4f1e-9b8a-2d5c4e3f2a10"
OLD_ID = "b3016a10-f464-4026-bbae-9a6f9e0a4118"
ETAG = '"ncbMsA"'
ARGS = {"source_path": "/projects", "target_cluster_uuid": TARGET_UUID, "target_path": "/replicas/edge01-projects", "target_address": ADDRESS}
WINDOW_READ = {**WINDOW, "on_days": ["MON", "FRI"]}
HASH_MISMATCH = {"error_class": "replication_source_relationship_hash_mismatch_error"}
ENDED_BY_TARGET = 'The relationship to target path "/replicas/edge01-projects/" on target cluster "dr01" was deleted by the target cluster.'

STATUSES = "/v2/replication/source-relationships/status/"
TARGET_STATUSES = "/v2/replication/target-relationships/status/"
SOURCES = "/v2/replication/source-relationships/"
ATTRIBUTES = "/v1/files/%2Fprojects/info/attributes"
ENTRIES = "/v1/files/%2Fprojects/entries/?limit=1000"
AGGREGATES = "/v1/files/%2Fprojects/aggregates/"
SNAPSHOTS = "/v4/snapshots/status/?limit=1000"


def source_route(record_id):
    return f"/v2/replication/source-relationships/{record_id}"


def status_route(record_id):
    return f"/v2/replication/source-relationships/{record_id}/status"


def status(
    record_id=REL_ID, *, uuid=TARGET_UUID, address=ADDRESS, port=3712, state="ESTABLISHED", source="/projects/", target="/replicas/edge01-projects/", **settings
):
    """A source status record as GET .../status returns it, of /projects on this cluster."""
    return {
        **SOURCE_STATUS,
        "id": record_id,
        "target_address": address,
        "target_port": port,
        "blackout_windows": [WINDOW_READ],
        "state": state,
        "end_reason": ENDED_BY_TARGET if state == "ENDED" else "",
        "source_cluster_uuid": CLUSTER_ID.upper(),
        "source_root_path": source,
        "target_cluster_uuid": uuid,
        "target_root_path": target,
        "target_root_read_only": state == "ESTABLISHED",
        "recovery_point": SOURCE_STATUS["recovery_point"] if state != "AWAITING_AUTHORIZATION" else "",
        **settings,
    }


def configuration(record):
    """The configuration GET .../{id} returns for a status record; the server expands EVERY_DAY here too."""
    keys = (
        "id",
        "target_address",
        "target_port",
        "source_root_id",
        "source_root_read_only",
        "map_local_ids_to_nfs_ids",
        "replication_enabled",
        "replication_mode",
        "blackout_window_timezone",
        "blackout_windows",
        "snapshot_policies",
    )
    return {key: record[key] for key in keys}


def described(record):
    """A status record in the shape both sides share."""
    return {
        "id": record["id"],
        "source_cluster_uuid": CLUSTER_ID,
        "target_cluster_uuid": record["target_cluster_uuid"].lower(),
        "source_path": "/projects",
        "target_path": record["target_root_path"].rstrip("/"),
        "target_address": record["target_address"],
        "target_port": record["target_port"],
        "state": record["state"],
        "end_reason": record["end_reason"] or None,
        "job_state": record["job_state"],
        "job_start_time": record["job_start_time"] or None,
        "recovery_point_snapshot_id": None,
        "recovery_point": record["recovery_point"] or None,
        "error_from_last_job": record["error_from_last_job"] or None,
        "target_root_read_only": record["target_root_read_only"],
        "replication_enabled": record["replication_enabled"],
        "map_local_ids_to_nfs_ids": record["map_local_ids_to_nfs_ids"],
        "source_root_read_only": record["source_root_read_only"],
        "blackout_windows": record["blackout_windows"],
        "blackout_window_timezone": record["blackout_window_timezone"],
    }


def target_record(record_id=OLD_ID, *, source_uuid=TARGET_UUID, source="/replicas/edge01-projects/", target="/projects/", state="DISCONNECTED"):
    """A target status record on this cluster, as a reversal leaves one."""
    return {
        **TARGET_STATUS,
        "id": record_id,
        "state": state,
        "source_cluster_uuid": source_uuid,
        "source_root_path": source,
        "source_address": ADDRESS,
        "source_port": 3712,
        "target_cluster_uuid": CLUSTER_ID,
        "target_root_path": target,
        "replication_enabled": True,
    }


def aggregates(files=1, symlinks=0, others=0, capacity=8192):
    return {
        "path": "/projects/",
        "id": "3",
        "total_capacity": str(capacity),
        "total_data": "4096",
        "total_named_stream_data": "0",
        "total_meta": "4096",
        "total_files": str(files),
        "total_directories": "0",
        "total_symlinks": str(symlinks),
        "total_other_objects": str(others),
        "total_named_streams": "0",
        "total_external_objects": None,
    }


def entry(path, kind="FS_FILE_TYPE_FILE"):
    return {"path": path, "name": path.rstrip("/").rsplit("/", 1)[1], "type": kind, "id": "40"}


def expect_source_directory(fake, files=None, aggregate=None):
    """The source directory read for the target's authorize: attributes, the walk's first page, then aggregates."""
    fake.expect("GET", ATTRIBUTES, response={"path": "/projects/", "type": "FS_FILE_TYPE_DIRECTORY", "id": "3"})
    fake.expect("GET", ENTRIES, response={"files": [entry("/projects/a.txt")] if files is None else list(files), "paging": {"next": ""}})
    fake.expect("GET", AGGREGATES, response=aggregate or aggregates())


def expect_checks(fake, records=(), targets=(), files=None, aggregate=None):
    """What a run reads before a create: the source list, the target list for row O8, and the source directory."""
    fake.expect("GET", STATUSES, response=list(records))
    fake.expect("GET", TARGET_STATUSES, response=list(targets))
    expect_source_directory(fake, files, aggregate)


def expect_create(fake, body, created):
    fake.expect("POST", SOURCES, body=body, response=configuration(created))
    fake.expect("GET", status_route(created["id"]), response=created)


def expect_delete(fake, record, snapshots_before=(), snapshots_after=()):
    """One record's delete, between the snapshot lists the task reads before and after its deletes."""
    fake.expect("GET", SNAPSHOTS, response={"entries": list(snapshots_before), "paging": {"next": ""}})
    fake.expect("GET", source_route(record["id"]), response=configuration(record), etag=ETAG)
    fake.expect("DELETE", source_route(record["id"]), if_match=ETAG)
    if snapshots_before:
        fake.expect("GET", SNAPSHOTS, response={"entries": list(snapshots_after), "paging": {"next": ""}})


def writes(fake):
    """The requests a test sent that are not reads, without the login the client sends by itself."""
    return [(e.method, e.path) for e in fake.log if not e.background and e.method != "GET"]


def create_body(**settings):
    return {
        "source_root_path": "/projects",
        "target_root_path": "/replicas/edge01-projects",
        "target_address": ADDRESS,
        "target_port": 3712,
        **settings,
    }


def pending(record_id=NEW_ID, **overrides):
    return status(record_id, state="AWAITING_AUTHORIZATION", target="/replicas/edge01-projects", **overrides)


# create, no-op, PATCH, check mode, diff


def test_creates_with_the_declared_settings_and_reads_the_record_back(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake)
    created = pending(blackout_windows=[WINDOW_READ], blackout_window_timezone="UTC", replication_enabled=False)
    expect_create(fake, create_body(replication_enabled=False, blackout_windows=[WINDOW_READ], blackout_window_timezone="UTC"), created)

    result = run_module(
        replication_relationship,
        {**CONNECTION, **ARGS, "replication_enabled": False, "blackout_windows": [WINDOW], "blackout_window_timezone": "UTC"},
    )

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["creates"] is True
    assert result["relationship"] == described(created)
    assert result["read_by_id"] is None
    assert (result["source_empty"], result["source_files"], result["source_bytes"]) == (False, 1, 8192)
    assert result["unmet"] == [] and result["deleted"] == []


def test_the_mode_and_policies_are_never_sent(fake_api, run_module):
    fake = fake_api()
    adopted = status(replication_mode="REPLICATION_SNAPSHOT_POLICY", snapshot_policies=[{"id": 4, "target_expiration": "never"}])
    fake.expect("GET", STATUSES, response=[adopted])
    fake.expect("GET", source_route(REL_ID), response=configuration(adopted), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response={**adopted, "replication_enabled": False})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert "replication_mode" not in result["relationship"] and "snapshot_policies" not in result["relationship"]


def test_a_matching_relationship_is_a_no_op_after_one_list(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status()])

    result = run_module(
        replication_relationship,
        {**CONNECTION, **ARGS, "replication_enabled": True, "blackout_windows": [WINDOW], "blackout_window_timezone": "America/Los_Angeles"},
    )

    assert result["changed"] is False, result.get("msg")
    assert result["creates"] is False
    assert result["read_by_id"] == REL_ID
    assert result["relationship"] == described(status())
    assert (result["source_empty"], result["source_files"], result["source_bytes"]) == (None, None, None)


def test_windows_in_another_order_or_with_every_day_spelled_out_are_the_same(fake_api, run_module):
    second = {"start_hour": 1, "start_minute": 0, "end_hour": 2, "end_minute": 0, "on_days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]}
    fake_api().expect("GET", STATUSES, response=[status(blackout_windows=[WINDOW_READ, second])])

    result = run_module(
        replication_relationship,
        {
            **CONNECTION,
            **ARGS,
            "blackout_windows": [{**second, "on_days": ["EVERY_DAY"]}, WINDOW],
            "blackout_window_timezone": "America/Los_Angeles",
        },
    )

    assert result["changed"] is False, result.get("msg")


def test_patches_only_the_differing_keys_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False, "blackout_windows": []}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response=status(replication_enabled=False, blackout_windows=[]))

    result = run_module(
        replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False, "blackout_windows": [], "map_local_ids_to_nfs_ids": False}
    )

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["replication_enabled"] is False
    assert result["relationship"]["blackout_windows"] == []


LATE_WINDOW = {"start_hour": 20, "start_minute": 0, "end_hour": 22, "end_minute": 0, "on_days": ["SAT", "TUE"]}
SINGLE_KEYS = [
    ({"source_root_read_only": True}, {"source_root_read_only": True}, {"source_root_read_only": True}),
    ({"map_local_ids_to_nfs_ids": True}, {"map_local_ids_to_nfs_ids": True}, {"map_local_ids_to_nfs_ids": True}),
    ({"target_port": 13712}, {"target_port": 13712}, {"target_port": 13712}),
    (
        {"blackout_windows": [LATE_WINDOW], "blackout_window_timezone": "America/Los_Angeles"},
        {"blackout_windows": [{**LATE_WINDOW, "on_days": ["TUE", "SAT"]}]},
        {"blackout_windows": [{**LATE_WINDOW, "on_days": ["TUE", "SAT"]}]},
    ),
]


@pytest.mark.parametrize(
    ("declared", "body", "stored"), SINGLE_KEYS, ids=["source_root_read_only", "map_local_ids_to_nfs_ids", "target_port", "blackout_windows"]
)
def test_each_setting_is_patched_alone_with_its_days_in_the_server_s_order(fake_api, run_module, declared, body, stored):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body=body, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response=status(**stored))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, **declared})

    assert result["changed"] is True, result.get("msg")


@pytest.mark.parametrize(
    "declared",
    [{"source_root_read_only": False}, {"map_local_ids_to_nfs_ids": False}, {"target_port": 3712}, {"replication_enabled": True}],
    ids=["source_root_read_only", "map_local_ids_to_nfs_ids", "target_port", "replication_enabled"],
)
def test_each_setting_already_stored_is_no_change(fake_api, run_module, declared):
    fake_api().expect("GET", STATUSES, response=[status()])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, **declared})

    assert result["changed"] is False, result.get("msg")


def test_a_patch_answered_412_is_rebuilt_from_the_re_read_with_its_etag(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag='"A"')
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match='"A"', status=412, response=HASH_MISMATCH)
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag='"B"')
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match='"B"')
    fake.expect("GET", status_route(REL_ID), response=status(replication_enabled=False))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert fake_clock.sleeps == [0.5]


def test_a_patch_answered_412_writes_nothing_when_another_writer_already_made_it(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag='"A"')
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match='"A"', status=412, response=HASH_MISMATCH)
    fake.expect("GET", source_route(REL_ID), response=configuration(status(replication_enabled=False)), etag='"B"')
    fake.expect("GET", status_route(REL_ID), response=status(replication_enabled=False))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"]["replication_enabled"] is False


def test_a_412_retry_leaves_another_writer_s_change_to_an_undeclared_key(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag='"A"')
    fake.expect("PATCH", source_route(REL_ID), body={"target_address": FLOATING}, if_match='"A"', status=412, response=HASH_MISMATCH)
    fake.expect("GET", source_route(REL_ID), response=configuration(status(replication_enabled=False)), etag='"B"')
    fake.expect("PATCH", source_route(REL_ID), body={"target_address": FLOATING}, if_match='"B"')
    fake.expect("GET", status_route(REL_ID), response=status(address=FLOATING, replication_enabled=False))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_address": FLOATING})

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["replication_enabled"] is False


def test_check_mode_predicts_the_create_and_writes_nothing(fake_api, run_module):
    expect_checks(fake_api())

    result = run_module(
        replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [WINDOW], "blackout_window_timezone": "UTC"}, check_mode=True, diff=True
    )

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is True
    predicted = result["relationship"]
    assert predicted["id"] is None
    assert predicted["state"] == "AWAITING_AUTHORIZATION"
    assert (predicted["source_cluster_uuid"], predicted["target_cluster_uuid"]) == (CLUSTER_ID, TARGET_UUID)
    assert (predicted["source_path"], predicted["target_path"]) == ("/projects", "/replicas/edge01-projects")
    assert (predicted["target_address"], predicted["target_port"]) == (ADDRESS, 3712)
    assert predicted["blackout_windows"] == [WINDOW_READ] and predicted["blackout_window_timezone"] == "UTC"
    assert predicted["replication_enabled"] is True
    assert result["diff"] == {"before": {"relationship": {}, "records": []}, "after": {"relationship": predicted, "records": []}}


def test_check_mode_predicts_what_a_create_without_settings_stores(fake_api, run_module):
    expect_checks(fake_api())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True)

    predicted = result["relationship"]
    assert (predicted["blackout_window_timezone"], predicted["blackout_windows"]) == ("", []), result.get("msg")
    assert (predicted["replication_enabled"], predicted["map_local_ids_to_nfs_ids"], predicted["source_root_read_only"]) == (True, False, False)


def test_check_mode_predicts_a_patch_with_its_diff(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status()])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_address": FLOATING}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["read_by_id"] == REL_ID
    assert result["diff"]["before"]["relationship"]["target_address"] == ADDRESS
    assert result["diff"]["after"]["relationship"]["target_address"] == FLOATING


def test_a_lost_patch_is_reconciled_by_reading_and_never_sent_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), raises=ConnectionResetError())
    fake.expect("GET", status_route(REL_ID), response=status(replication_enabled=False))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert any("reconciled" in note for note in result["notes"])


def test_a_lost_patch_that_did_not_take_fails_without_sending_it_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), raises=ConnectionResetError())
    fake.expect("GET", status_route(REL_ID), response=status())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["failed"] is True
    assert "did not converge on replication_enabled" in result["msg"]
    assert result["maybe_applied"] is True


# adoption and address changes


def test_a_relationship_made_by_hand_at_another_address_of_the_target_is_adopted_with_one_patch(fake_api, run_module):
    fake = fake_api()
    by_hand = status(address=NAT, port=13712)
    fake.expect("GET", STATUSES, response=[by_hand])
    fake.expect("GET", source_route(REL_ID), response=configuration(by_hand), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"target_address": ADDRESS, "target_port": 3712}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response=status())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is False
    assert (result["relationship"]["target_address"], result["relationship"]["target_port"]) == (ADDRESS, 3712)


def test_a_move_to_a_floating_ip_is_one_patch_of_the_address(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"target_address": FLOATING}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response=status(address=FLOATING))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_address": FLOATING})

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["target_address"] == FLOATING


def test_an_address_spelled_another_way_is_the_same(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status(address="2001:db8::40")])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_address": "2001:0db8:0000::0040"})

    assert result["changed"] is False, result.get("msg")


def test_a_host_name_as_the_address_fails_before_any_request(fake_api, run_module):
    fake_api()

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_address": "dr01.example.com"})

    assert result["failed"] is True
    assert "not an IP address" in result["msg"]


def test_relationship_id_with_absent_is_refused(fake_api, run_module):
    fake_api()

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "relationship_id": REL_ID, "state": "absent"})

    assert result["failed"] is True
    assert "relationship_id" in result["msg"]


def test_absent_needs_no_address(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[])

    result = run_module(replication_relationship, {**CONNECTION, **{k: v for k, v in ARGS.items() if k != "target_address"}, "state": "absent"})

    assert result["changed"] is False, result.get("msg")


# matching


def test_a_target_path_read_back_with_or_without_its_slash_matches(fake_api, run_module):
    # QUIRK-020: target_root_path reads back as sent until the source first polls the target, then with a slash.
    for target in ("/replicas/edge01-projects", "/replicas/edge01-projects/"):
        fake_api().expect("GET", STATUSES, response=[status(target=target)])

        result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_path": "/replicas/edge01-projects/"})

        assert result["changed"] is False, result.get("msg")
        assert result["relationship"]["target_path"] == "/replicas/edge01-projects"


def test_the_target_uuid_matches_without_regard_to_case(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status(uuid=TARGET_UUID.upper())])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "target_cluster_uuid": TARGET_UUID.upper()})

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"]["target_cluster_uuid"] == TARGET_UUID


def test_two_live_matches_fail_naming_both_ids(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status(), pending()])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert REL_ID in result["msg"] and NEW_ID in result["msg"]


def test_a_record_at_another_address_with_an_unknown_uuid_is_left_alone(fake_api, run_module):
    fake = fake_api()
    elsewhere = status(OLD_ID, uuid=UNKNOWN_UUID, address=NAT)
    expect_checks(fake, records=[elsewhere])
    expect_create(fake, create_body(), pending())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["deleted"] == [] and result["unmet"] == []


def test_records_of_other_paths_are_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(OLD_ID, target="/replicas/other/")])
    expect_create(fake, create_body(), pending())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")


def test_a_record_of_a_neighbouring_source_path_is_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(OLD_ID, source="/projects2/"), status(NEW_ID, source="/projects/sub/")])
    expect_create(fake, create_body(), pending("e5a0c1d2-0000-4000-8000-00000000000b"))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["deleted"] == [] and result["unmet"] == []


def test_a_create_the_server_refuses_for_an_unreachable_target_fails_with_its_error_class(fake_api, run_module):
    # research R19; the restore's step 2 hands off on this error_class (replication_restore_step's stop_on).
    fake = fake_api()
    expect_checks(fake)
    unreachable = {"error_class": "replication_connection_error", "description": "Cannot communicate with the target cluster."}
    fake.expect("POST", SOURCES, body=create_body(), status=400, response=unreachable)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["error_class"] == "replication_connection_error"
    assert "maybe_applied" not in result
    assert writes(fake) == [("POST", SOURCES)]


def test_a_lost_create_is_reconciled_by_listing_and_never_sent_again(fake_api, run_module):
    fake = fake_api()
    existing = status(OLD_ID, uuid=UNKNOWN_UUID, address=NAT)
    expect_checks(fake, records=[existing])
    fake.expect("POST", SOURCES, raises=ConnectionResetError())
    fake.expect("GET", STATUSES, response=[existing, pending()])
    fake.expect("GET", status_route(NEW_ID), response=pending())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["id"] == NEW_ID
    assert any("create" in note and "reconciled" in note for note in result["notes"])


def test_a_lost_create_beside_a_changed_peer_at_the_declared_address_finds_only_the_new_record(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    expect_checks(fake, records=[old])
    fake.expect("POST", SOURCES, raises=ConnectionResetError())
    fake.expect("GET", STATUSES, response=[old, pending()])
    fake.expect("GET", status_route(NEW_ID), response=pending())
    expect_delete(fake, old)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate_changed_peer": True})

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["id"] == NEW_ID
    assert [record["id"] for record in result["deleted"]] == [OLD_ID]


def test_a_lost_create_that_listing_cannot_find_fails_without_sending_it_again(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake)
    fake.expect("POST", SOURCES, raises=ConnectionResetError())
    fake.expect("GET", STATUSES, response=[])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "no new record" in result["msg"] and "G-REPL-ONESHOT" in result["msg"]


def test_a_lost_create_that_listing_finds_twice_fails_naming_both(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake)
    fake.expect("POST", SOURCES, raises=ConnectionResetError())
    fake.expect("GET", STATUSES, response=[pending(), pending(OLD_ID)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert NEW_ID in result["msg"] and OLD_ID in result["msg"]


def test_a_patched_record_gone_before_its_read_back_fails_as_changed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), status=404, response={"error_class": "relationship_manager_relationship_not_found_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "is missing after the write" in result["msg"]


# relationship_id: the real pass reads by id the record the check pass returned as read_by_id


def test_the_real_pass_reads_the_check_pass_s_match_by_id(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", status_route(REL_ID), response=status())
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response=status(replication_enabled=False))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "relationship_id": REL_ID, "replication_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert result["read_by_id"] == REL_ID


def test_a_record_gone_since_the_check_pass_fails_and_is_not_created_again(fake_api, run_module):
    fake_api().expect("GET", status_route(REL_ID), status=404, response={"error_class": "relationship_manager_relationship_not_found_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "relationship_id": REL_ID})

    assert result["failed"] is True
    assert REL_ID in result["msg"]


def test_a_record_ended_since_the_check_pass_fails(fake_api, run_module):
    fake_api().expect("GET", status_route(REL_ID), response=status(state="ENDED"))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "relationship_id": REL_ID})

    assert result["failed"] is True
    assert "is ENDED now" in result["msg"]


def test_a_relationship_id_of_another_key_fails(fake_api, run_module):
    fake_api().expect("GET", status_route(REL_ID), response=status(target="/replicas/other/"))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "relationship_id": REL_ID})

    assert result["failed"] is True
    assert "/replicas/other" in result["msg"]


# the source directory


def test_a_missing_source_directory_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[])
    fake.expect("GET", TARGET_STATUSES, response=[])
    fake.expect("GET", ATTRIBUTES, status=404, response={"error_class": "fs_no_such_entry_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "source_path_pending": True})

    assert result["failed"] is True
    assert [entry["row"] for entry in result["unmet"]] == ["source_missing"]
    assert result["unmet"][0]["pending"] is False
    assert result["source_empty"] is True


def test_a_missing_source_directory_is_pending_in_check_mode_when_the_directory_step_creates_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[])
    fake.expect("GET", TARGET_STATUSES, response=[])
    fake.expect("GET", ATTRIBUTES, status=404, response={"error_class": "fs_no_such_entry_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "source_path_pending": True}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["creates"] is True
    assert [(entry["row"], entry["pending"]) for entry in result["unmet"]] == [("source_missing", True)]
    assert (result["source_empty"], result["source_files"], result["source_bytes"]) == (True, 0, 0)


def test_a_missing_source_directory_is_not_pending_in_check_mode_without_the_flag(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[])
    fake.expect("GET", TARGET_STATUSES, response=[])
    fake.expect("GET", ATTRIBUTES, status=404, response={"error_class": "fs_no_such_entry_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True)

    assert result["failed"] is True
    assert [(entry["row"], entry["pending"]) for entry in result["unmet"]] == [("source_missing", False)]
    assert writes(fake) == []


def test_a_source_holding_only_directories_counts_as_empty(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[])
    fake.expect("GET", TARGET_STATUSES, response=[])
    fake.expect("GET", ATTRIBUTES, response={"path": "/projects/", "type": "FS_FILE_TYPE_DIRECTORY", "id": "3"})
    fake.expect("GET", ENTRIES, response={"files": [entry("/projects/team-a/", "FS_FILE_TYPE_DIRECTORY")], "paging": {"next": ""}})
    fake.expect("GET", "/v1/files/%2Fprojects%2Fteam-a/entries/?limit=1000", response={"files": [], "paging": {"next": ""}})
    fake.expect("GET", AGGREGATES, response=aggregates(files=0, capacity=4096))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True)

    assert (result["source_empty"], result["source_files"], result["source_bytes"]) == (True, 0, 4096), result.get("msg")


def test_a_file_just_deleted_does_not_count_though_the_aggregates_still_do(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, files=[], aggregate=aggregates(files=1, capacity=8192))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True)

    assert (result["source_empty"], result["source_files"], result["source_bytes"]) == (True, 1, 8192), result.get("msg")


def test_the_walk_stops_at_the_first_object_and_counts_links_and_other_objects(fake_api, run_module):
    fake = fake_api()
    expect_checks(
        fake, files=[entry("/projects/link", "FS_FILE_TYPE_SYMLINK"), entry("/projects/deep/", "FS_FILE_TYPE_DIRECTORY")], aggregate=aggregates(1, 2, 3)
    )

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True)

    assert (result["source_empty"], result["source_files"]) == (False, 6), result.get("msg")


def test_a_pending_match_reports_the_source_directory_for_the_authorize(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[pending()])
    fake.expect("GET", TARGET_STATUSES, response=[])
    expect_source_directory(fake)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [WINDOW], "blackout_window_timezone": "America/Los_Angeles"})

    assert result["changed"] is False, result.get("msg")
    assert result["read_by_id"] == NEW_ID
    assert (result["source_empty"], result["source_files"], result["source_bytes"]) == (False, 1, 8192)


# FR-005 and misdirected records


def test_a_create_that_reaches_another_cluster_is_deleted_at_once_and_fails(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake)
    wrong = pending(uuid=UNKNOWN_UUID)
    fake.expect("POST", SOURCES, body=create_body(), response=configuration(wrong))
    fake.expect("GET", status_route(NEW_ID), response=wrong)
    fake.expect("GET", source_route(NEW_ID), response=configuration(wrong), etag=ETAG)
    fake.expect("DELETE", source_route(NEW_ID), if_match=ETAG)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["changed"] is True
    assert f"{ADDRESS}:3712 reaches cluster {UNKNOWN_UUID}" in result["msg"]
    assert "G-REPL-ONESHOT" in result["msg"]
    assert "recreate" not in result["msg"]


def test_a_changed_peer_is_untouched_when_the_new_create_reaches_the_wrong_cluster(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    expect_checks(fake, records=[old])
    wrong = pending(uuid=UNKNOWN_UUID)
    fake.expect("POST", SOURCES, body=create_body(), response=configuration(wrong))
    fake.expect("GET", status_route(NEW_ID), response=wrong)
    fake.expect("GET", source_route(NEW_ID), response=configuration(wrong), etag=ETAG)
    fake.expect("DELETE", source_route(NEW_ID), if_match=ETAG)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate_changed_peer": True})

    assert result["failed"] is True
    assert writes(fake) == [("POST", SOURCES), ("DELETE", source_route(NEW_ID))]


def test_a_record_of_another_inventory_host_at_the_declared_address_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(OLD_ID, uuid=OTHER_HOST_UUID)])

    result = run_module(
        replication_relationship, {**CONNECTION, **ARGS, "inventory_cluster_uuids": [OTHER_HOST_UUID, TARGET_UUID], "recreate_changed_peer": True}
    )

    assert result["failed"] is True
    assert [entry["row"] for entry in result["unmet"]] == ["misdirected"]
    assert OTHER_HOST_UUID in result["msg"] and TARGET_UUID in result["msg"] and ADDRESS in result["msg"]


def test_a_record_of_another_inventory_host_elsewhere_is_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(OLD_ID, uuid=OTHER_HOST_UUID, address=NAT)])
    expect_create(fake, create_body(), pending())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "inventory_cluster_uuids": [OTHER_HOST_UUID]})

    assert result["changed"] is True, result.get("msg")


# changed peers


def test_a_changed_peer_fails_without_recreate_changed_peer(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(OLD_ID, uuid=OLD_TARGET_UUID)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert [(entry["row"], entry["lifted"]) for entry in result["unmet"]] == [("changed_peer", False)]
    assert result["unmet"][0]["lifted_by"] == "recreate_changed_peer"


def test_a_changed_peer_at_the_declared_address_is_recreated_create_first(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    expect_checks(fake, records=[old])
    expect_create(fake, create_body(), pending())
    expect_delete(fake, old)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate_changed_peer": True})

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is True
    assert result["relationship"]["id"] == NEW_ID
    assert [record["id"] for record in result["deleted"]] == [OLD_ID]
    assert writes(fake) == [("POST", SOURCES), ("DELETE", source_route(OLD_ID))]
    assert any("then deleted" in note for note in result["notes"])


def test_a_previous_uuid_record_at_another_address_is_a_changed_peer(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID, address=NAT)
    expect_checks(fake, records=[old])
    expect_create(fake, create_body(), pending())
    expect_delete(fake, old)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate_changed_peer": True, "previous_target_cluster_uuids": [OLD_TARGET_UUID]})

    assert [record["id"] for record in result["deleted"]] == [OLD_ID], result.get("msg")


def test_check_mode_predicts_the_create_and_the_changed_peer_s_delete(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    expect_checks(fake, records=[old])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate_changed_peer": True}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is True
    assert result["read_by_id"] is None
    assert [record["id"] for record in result["deleted"]] == [OLD_ID]
    assert result["diff"]["before"]["records"] == [described(old)]
    assert [(entry["row"], entry["lifted"]) for entry in result["unmet"]] == [("changed_peer", True)]


def test_a_changed_peer_beside_a_match_is_deleted_with_no_create(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    fake.expect("GET", STATUSES, response=[old, status()])
    expect_delete(fake, old)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [WINDOW], "blackout_window_timezone": "America/Los_Angeles"})

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is False
    assert result["read_by_id"] is None
    assert [record["id"] for record in result["deleted"]] == [OLD_ID]
    assert writes(fake) == [("DELETE", source_route(OLD_ID))]


def test_a_check_run_that_predicts_a_delete_returns_no_id_to_read_by(fake_api, run_module):
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    fake_api().expect("GET", STATUSES, response=[old, status()])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["id"] == REL_ID
    assert result["read_by_id"] is None
    assert result["diff"]["before"]["records"] == [described(old)]


def test_previous_target_uuids_holding_the_declared_one_are_refused(fake_api, run_module):
    fake_api()

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "previous_target_cluster_uuids": [OLD_TARGET_UUID, TARGET_UUID.upper()]})

    assert result["failed"] is True
    assert "previous UUIDs never hold its own" in result["msg"]


# create: false, the delete-only pass


def test_create_false_deletes_only_previous_uuid_records_at_the_declared_address_or_unreachable(fake_api, run_module):
    fake = fake_api()
    at_address = status(OLD_ID, uuid=OLD_TARGET_UUID)
    unreachable = status(NEW_ID, uuid=UNKNOWN_UUID, address=NAT, error_from_last_job="Cannot establish a secure connection.")
    kept = status(REL_ID, uuid=OTHER_HOST_UUID, address=NAT)
    unknown_at_address = status("e5a0c1d2-0000-4000-8000-000000000009", uuid="a1b2c3d4-0000-4000-8000-00000000000a")
    fake.expect("GET", STATUSES, response=[at_address, unreachable, kept, unknown_at_address])
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    for record in (at_address, unreachable):
        fake.expect("GET", source_route(record["id"]), response=configuration(record), etag=ETAG)
        fake.expect("DELETE", source_route(record["id"]), if_match=ETAG)

    result = run_module(
        replication_relationship,
        {**CONNECTION, **ARGS, "create": False, "previous_target_cluster_uuids": [OLD_TARGET_UUID, UNKNOWN_UUID, OTHER_HOST_UUID]},
    )

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is False
    assert result["read_by_id"] is None
    assert [record["id"] for record in result["deleted"]] == [OLD_ID, NEW_ID]
    assert sum("left for a create-first replacement" in note for note in result["notes"]) == 2


def test_create_false_still_refuses_another_inventory_cluster_s_record_at_the_declared_address(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status(OLD_ID, uuid=OTHER_HOST_UUID)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "create": False, "inventory_cluster_uuids": [OTHER_HOST_UUID]})

    assert result["failed"] is True
    assert [entry["row"] for entry in result["unmet"]] == ["misdirected"]


def test_create_false_creates_and_patches_nothing(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[pending(REL_ID, replication_enabled=True)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "create": False, "replication_enabled": False})

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"]["id"] == REL_ID


def test_create_false_with_nothing_to_delete_is_no_change(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "create": False}, check_mode=True)

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"] == {}


# overwrite rows O3 and O8


def test_an_ended_match_fails_without_recreate_and_a_release_bound_to_it(fake_api, run_module):
    for options in ({}, {"recreate": True}, {"release": [f"dr01:/replicas/edge01-projects@{REL_ID}:O3"]}):
        fake = fake_api()
        expect_checks(fake, records=[status(state="ENDED")])

        result = run_module(replication_relationship, {**CONNECTION, **ARGS, **options})

        assert result["failed"] is True, options
        assert [(entry["row"], entry["binds_to"], entry["lifted"]) for entry in result["unmet"]] == [("O3", REL_ID, False)]
        assert result["releases"] == [{"target_path": "/replicas/edge01-projects", "record_id": REL_ID, "rows": ["O3"]}]
        assert result["unmet"][0]["lifted_by"] == "recreate and release"


def test_a_release_bound_to_another_record_lifts_nothing(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(state="ENDED")])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate": True, "release": [f"dr01:/replicas/edge01-projects@{OLD_ID}:O3"]})

    assert result["failed"] is True
    assert result["unmet"][0]["lifted"] is False


def test_a_release_bound_to_the_ended_record_but_naming_another_row_lifts_nothing(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(state="ENDED")])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate": True, "release": [f"dr01:/replicas/edge01-projects@{REL_ID}:O4"]})

    assert result["failed"] is True
    assert [(entry["row"], entry["lifted"]) for entry in result["unmet"]] == [("O3", False)]


def test_two_ended_records_each_need_their_own_release(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake, records=[status(state="ENDED"), status(OLD_ID, state="ENDED")])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate": True, "release": [f"dr01:/replicas/edge01-projects@{REL_ID}:O3"]})

    assert result["failed"] is True
    assert [(entry["binds_to"], entry["lifted"]) for entry in result["unmet"]] == [(REL_ID, True), (OLD_ID, False)]
    assert result["releases"] == [{"target_path": "/replicas/edge01-projects", "record_id": OLD_ID, "rows": ["O3"]}]


def test_with_both_releases_one_create_replaces_both_ended_records(fake_api, run_module):
    fake = fake_api()
    first, second = status(state="ENDED"), status(OLD_ID, state="ENDED")
    expect_checks(fake, records=[first, second])
    expect_create(fake, create_body(), pending())
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    for record in (first, second):
        fake.expect("GET", source_route(record["id"]), response=configuration(record), etag=ETAG)
        fake.expect("DELETE", source_route(record["id"]), if_match=ETAG)
    releases = [f"dr01:/replicas/edge01-projects@{REL_ID}:O3", f"dr01:/replicas/edge01-projects@{OLD_ID}:O3"]

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate": True, "release": releases})

    assert result["changed"] is True, result.get("msg")
    assert [record["id"] for record in result["deleted"]] == [REL_ID, OLD_ID]
    assert writes(fake) == [("POST", SOURCES), ("DELETE", source_route(REL_ID)), ("DELETE", source_route(OLD_ID))]


def test_the_release_is_inert_once_the_recreate_deleted_its_record(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[pending()])
    fake.expect("GET", TARGET_STATUSES, response=[])
    expect_source_directory(fake)

    result = run_module(
        replication_relationship,
        {
            **CONNECTION,
            **ARGS,
            "recreate": True,
            "release": [f"dr01:/replicas/edge01-projects@{REL_ID}:O3"],
            "blackout_windows": [WINDOW],
            "blackout_window_timezone": "America/Los_Angeles",
        },
    )

    assert result["changed"] is False, result.get("msg")
    assert (result["unmet"], result["releases"]) == ([], [])


def test_with_recreate_and_its_release_an_ended_match_is_replaced_create_first(fake_api, run_module):
    fake = fake_api()
    ended = status(state="ENDED")
    expect_checks(fake, records=[ended])
    expect_create(fake, create_body(), pending())
    expect_delete(fake, ended)

    result = run_module(
        replication_relationship,
        {**CONNECTION, **ARGS, "recreate": True, "release": [f"dr01:/replicas/edge01-projects/@{REL_ID}:O3,O4"]},
    )

    assert result["changed"] is True, result.get("msg")
    assert result["creates"] is True
    assert [(entry["row"], entry["lifted"]) for entry in result["unmet"]] == [("O3", True)]
    assert result["releases"] == []
    assert [record["id"] for record in result["deleted"]] == [REL_ID]
    assert writes(fake) == [("POST", SOURCES), ("DELETE", source_route(REL_ID))]


def test_an_ended_record_beside_a_live_one_is_a_leftover_and_deleted(fake_api, run_module):
    # A prune on the target of a pending record the source still holds ends the source's record (research R5).
    fake = fake_api()
    ended = status(OLD_ID, state="ENDED")
    fake.expect("GET", STATUSES, response=[ended, status()])
    expect_delete(fake, ended)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["unmet"] == []
    assert [record["id"] for record in result["deleted"]] == [OLD_ID]


@pytest.mark.parametrize("leftover", [status(OLD_ID, state="ENDED"), status(OLD_ID, uuid=OLD_TARGET_UUID)], ids=["ended", "changed peer"])
def test_a_leftover_beside_a_pending_record_is_deleted_with_no_create_nor_consent(fake_api, run_module, leftover):
    # A create-first replacement that stopped after its create leaves the new pending record beside the old one.
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[pending(), leftover])
    fake.expect("GET", TARGET_STATUSES, response=[])
    expect_source_directory(fake)
    expect_delete(fake, leftover)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [WINDOW], "blackout_window_timezone": "America/Los_Angeles"})

    assert result["changed"] is True, result.get("msg")
    assert result["unmet"] == []
    assert [record["id"] for record in result["deleted"]] == [OLD_ID]
    assert result["read_by_id"] is None
    assert result["source_empty"] is False
    assert writes(fake) == [("DELETE", source_route(OLD_ID))]


def test_a_release_for_another_target_path_fails(fake_api, run_module):
    fake_api()

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "release": [f"dr01:/replicas/other@{REL_ID}:O3"]})

    assert result["failed"] is True
    assert "/replicas/other" in result["msg"]


def test_a_reversed_record_refuses_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_checks(
        fake,
        targets=[
            target_record(),
            target_record(NEW_ID, target="/elsewhere/"),
            target_record(REL_ID, source_uuid=UNKNOWN_UUID),
            target_record("e5a0c1d2-0000-4000-8000-00000000000c", state="ENDED"),
        ],
    )

    result = run_module(replication_relationship, {**CONNECTION, **ARGS}, check_mode=True)

    assert result["failed"] is True
    assert [(entry["row"], entry["facts"]["id"]) for entry in result["unmet"]] == [("O8", OLD_ID)]
    assert result["unmet"][0]["lifted_by"] is None


@pytest.mark.parametrize("target", ["/projects/team-a/", "/"], ids=["inside", "containing"])
def test_a_reversed_record_nested_with_the_source_path_refuses(fake_api, run_module, target):
    fake = fake_api()
    expect_checks(fake, targets=[target_record(target=target)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert [entry["row"] for entry in result["unmet"]] == ["O8"], result.get("msg")


def test_every_unmet_condition_is_reported_at_once(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status(state="ENDED"), status(OLD_ID, uuid=OLD_TARGET_UUID), status(NEW_ID, uuid=OTHER_HOST_UUID)])
    fake.expect("GET", TARGET_STATUSES, response=[target_record()])
    fake.expect("GET", ATTRIBUTES, status=404, response={"error_class": "fs_no_such_entry_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "inventory_cluster_uuids": [OTHER_HOST_UUID]})

    assert result["failed"] is True
    assert [entry["row"] for entry in result["unmet"]] == ["misdirected", "source_missing", "changed_peer", "O8", "O3"]
    assert writes(fake) == []


# failover states


@pytest.mark.parametrize("state", ["DISCONNECTING", "DISCONNECTED", "RECONNECTING", "REESTABLISHING"])
def test_a_failover_state_patches_settings_and_warns(fake_api, run_module, state):
    fake = fake_api()
    record = status(state=state)
    fake.expect("GET", STATUSES, response=[record])
    fake.expect("GET", source_route(REL_ID), response=configuration(record), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"replication_enabled": False}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response={**record, "replication_enabled": False})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["state"] == state
    assert any("a failover state" in warning for warning in result["warnings"])


# timezones


def test_blackout_windows_without_a_timezone_fail_before_any_request(fake_api, run_module):
    fake_api()

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [WINDOW]})

    assert result["failed"] is True
    assert "blackout_window_timezone" in result["msg"] and "G-REPL-TZ" in result["msg"]


@pytest.mark.parametrize("timezone", ["", " "], ids=["empty", "blank"])
def test_blackout_windows_with_an_empty_timezone_fail_before_any_request(fake_api, run_module, timezone):
    fake_api()

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [WINDOW], "blackout_window_timezone": timezone})

    assert result["failed"] is True
    assert "G-REPL-TZ" in result["msg"]


def test_an_empty_timezone_where_one_is_set_fails_before_any_write(fake_api, run_module):
    # QUIRK-019: a PATCH of an empty timezone answers 200 and leaves the stored one.
    fake_api().expect("GET", STATUSES, response=[status()])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [], "blackout_window_timezone": ""})

    assert result["failed"] is True
    assert "QUIRK-019" in result["msg"] and "America/Los_Angeles" in result["msg"]


def test_an_empty_timezone_where_none_is_set_is_no_change(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status(blackout_windows=[], blackout_window_timezone="")])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_windows": [], "blackout_window_timezone": ""})

    assert result["changed"] is False, result.get("msg")


def test_a_timezone_change_is_one_patch(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("PATCH", source_route(REL_ID), body={"blackout_window_timezone": "UTC"}, if_match=ETAG)
    fake.expect("GET", status_route(REL_ID), response=status(blackout_window_timezone="UTC"))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "blackout_window_timezone": "UTC"})

    assert result["changed"] is True, result.get("msg")


# absent


def test_absent_reports_a_snapshot_that_outlives_its_relationship_s_delete(fake_api, run_module):
    # The source's delete normally takes its replication snapshot (research R13); one still listed and not in_delete
    # afterwards is reported, and a snapshot of a record the task leaves is not.
    fake = fake_api()
    record = status()
    fake.expect("GET", STATUSES, response=[record, status(OLD_ID, uuid=UNKNOWN_UUID, address=NAT)])
    owned = snapshot(7, "7_replication_to_dr01", owners=[REL_ID], path="/projects/")
    other = snapshot(8, "8_replication_to_elsewhere", owners=[OLD_ID], path="/projects/")
    daily = snapshot(2, "daily", path="/projects/")
    expect_delete(fake, record, snapshots_before=[owned, other, daily], snapshots_after=[{**owned, "owners": []}, other, daily])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"] == {}
    assert [record["id"] for record in result["deleted"]] == [REL_ID]
    assert result["snapshots"] == [{"id": 7, "name": "7_replication_to_dr01"}]
    assert any("G-REPL-NOTIFY" in note for note in result["notes"])
    assert result["diff"] == {"before": {"relationship": described(record), "records": [described(record)]}, "after": {"relationship": {}, "records": []}}


def test_a_snapshot_the_delete_is_taking_is_not_left(fake_api, run_module):
    fake = fake_api()
    record = status()
    fake.expect("GET", STATUSES, response=[record])
    owned = snapshot(7, "7_replication_to_dr01", owners=[REL_ID], path="/projects/")
    expect_delete(fake, record, snapshots_before=[owned], snapshots_after=[snapshot(7, "7_replication_to_dr01", path="/projects/", in_delete=True)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["snapshots"] == []
    assert not any("G-REPL-SNAPSHOT" in note for note in result["notes"])


def test_absent_also_deletes_records_of_previous_target_uuids_in_any_state(fake_api, run_module):
    fake = fake_api()
    records = [status(state="ENDED"), status(OLD_ID, uuid=OLD_TARGET_UUID, address=NAT), status(NEW_ID, uuid=UNKNOWN_UUID)]
    fake.expect("GET", STATUSES, response=records)
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    for record in records[:2]:
        fake.expect("GET", source_route(record["id"]), response=configuration(record), etag=ETAG)
        fake.expect("DELETE", source_route(record["id"]), if_match=ETAG)

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "previous_target_cluster_uuids": [OLD_TARGET_UUID], "state": "absent"})

    assert [record["id"] for record in result["deleted"]] == [REL_ID, OLD_ID], result.get("msg")
    assert result["snapshots"] == []


def test_absent_with_the_entry_s_own_uuid_deletes_its_previous_uuid_records_when_the_target_is_gone(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID, error_from_last_job="Cannot establish a secure connection.")
    fake.expect("GET", STATUSES, response=[old])
    expect_delete(fake, old)

    result = run_module(
        replication_relationship,
        {**CONNECTION, **{k: v for k, v in ARGS.items() if k != "target_address"}, "previous_target_cluster_uuids": [OLD_TARGET_UUID], "state": "absent"},
    )

    assert [record["id"] for record in result["deleted"]] == [OLD_ID], result.get("msg")


def test_absent_treats_a_record_already_gone_as_done(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    fake.expect("GET", source_route(REL_ID), status=404, response={"error_class": "relationship_manager_relationship_not_found_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert result["deleted"] == []
    assert any(REL_ID in note for note in result["notes"])


def test_absent_treats_a_delete_answered_404_as_done(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("DELETE", source_route(REL_ID), status=404, response={"error_class": "relationship_manager_relationship_not_found_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"})

    assert result["changed"] is False, result.get("msg")


def test_a_lost_delete_is_reconciled_by_reading(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("DELETE", source_route(REL_ID), raises=ConnectionResetError())
    fake.expect("GET", status_route(REL_ID), status=404, response={"error_class": "relationship_manager_relationship_not_found_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert any("reconciled after a lost connection" in note for note in result["notes"])


def test_a_lost_delete_of_a_record_still_there_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response=[status()])
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    fake.expect("GET", source_route(REL_ID), response=configuration(status()), etag=ETAG)
    fake.expect("DELETE", source_route(REL_ID), raises=ConnectionResetError())
    fake.expect("GET", status_route(REL_ID), response=status())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"})

    assert result["failed"] is True
    assert result["maybe_applied"] is True


def test_absent_in_check_mode_predicts_the_deletes_and_writes_nothing(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status()])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert [record["id"] for record in result["deleted"]] == [REL_ID]
    assert result["snapshots"] == []
    assert result["read_by_id"] is None
    assert result["diff"] == {"before": {"relationship": described(status()), "records": [described(status())]}, "after": {"relationship": {}, "records": []}}


def test_absent_with_nothing_there_is_no_change(fake_api, run_module):
    fake_api().expect("GET", STATUSES, response=[status(OLD_ID, uuid=UNKNOWN_UUID)])

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert "notes" not in result


def test_a_failure_after_the_create_reports_what_the_task_had_done(fake_api, run_module):
    fake = fake_api()
    old = status(OLD_ID, uuid=OLD_TARGET_UUID)
    expect_checks(fake, records=[old])
    expect_create(fake, create_body(), pending())
    fake.expect("GET", SNAPSHOTS, response={"entries": [], "paging": {"next": ""}})
    fake.expect("GET", source_route(OLD_ID), response=configuration(old), etag=ETAG)
    fake.expect("DELETE", source_route(OLD_ID), if_match=ETAG, status=412, response={"error_class": "replication_source_relationship_hash_mismatch_error"})

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "recreate_changed_peer": True})

    assert result["failed"] is True
    assert result["changed"] is True
    assert NEW_ID in result["msg"]


def test_a_create_whose_settings_do_not_read_back_fails_as_changed(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake)
    expect_create(fake, create_body(replication_enabled=False), pending(replication_enabled=True))

    result = run_module(replication_relationship, {**CONNECTION, **ARGS, "replication_enabled": False})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "did not converge on replication_enabled" in result["msg"]


def test_the_create_sends_no_if_match(fake_api, run_module):
    fake = fake_api()
    expect_checks(fake)
    fake.expect("POST", SOURCES, body=create_body(), if_match=NO_HEADER, response=configuration(pending()))
    fake.expect("GET", status_route(NEW_ID), response=pending())

    result = run_module(replication_relationship, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
