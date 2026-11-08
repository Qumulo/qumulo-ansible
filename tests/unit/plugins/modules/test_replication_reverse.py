# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_reverse, driven through FakeQumulo, with the status records of fixtures_replication.py.

This cluster is the relationship's target before the reverse: R runs from SOURCE_UUID's /src to this cluster's /dst.
After the reverse it holds a source record of the same id, from /dst back to SOURCE_UUID's /src (research R18).
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import replication_reverse as replication_reverse_utils
from ansible_collections.qumulo.cluster.plugins.module_utils import snapshots
from ansible_collections.qumulo.cluster.plugins.modules import replication_reverse
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import REL_ID, SOURCE_STATUS, SOURCE_UUID, TARGET_STATUS

CONNECTION = {"api_host": "edge01.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SOURCE_ADDRESS = "203.0.113.50"
TARGET = f"/v2/replication/target-relationships/{REL_ID}/status"
SOURCE = f"/v2/replication/source-relationships/{REL_ID}/status"
MAKE_WRITABLE = f"/v2/replication/target-relationships/{REL_ID}/make-writable"
RECONNECT = f"/v2/replication/target-relationships/{REL_ID}/reconnect"
REVERSE = "/v2/replication/source-relationships/reverse-target-relationship"
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))
NOT_FOUND = {"error_class": "relationship_manager_relationship_not_found_error", "description": "The target relationship does not exist."}
NOT_DISCONNECTED = {
    "error_class": "relationship_manager_not_disconnected_error",
    "description": "Reverse relationship is not allowed on an established relationship. Disconnect first.",
}
HELD = {
    "error_class": "relationship_manager_target_contains_another_relationship_error",
    "description": "The target directory contains another relationship.",
}
RECOVERY_POINT = SOURCE_STATUS["recovery_point"]


def target(state="ESTABLISHED", **changes):
    """This cluster's target record of R."""
    record = {
        **TARGET_STATUS,
        "state": state,
        "target_cluster_uuid": CLUSTER_ID,
        "target_root_path": "/dst/",
        "target_root_read_only": state in ("ESTABLISHED", "DISCONNECTING", "RECONNECTING", "REESTABLISHING"),
        "recovery_point": RECOVERY_POINT,
    }
    return {**record, **changes}


def reversed_source(state="DISCONNECTED", address=SOURCE_ADDRESS, port=3712):
    """This cluster's source record of R after the reverse, to the old source."""
    return {
        **SOURCE_STATUS,
        "state": state,
        "source_cluster_uuid": CLUSTER_ID,
        "source_root_path": "/dst/",
        "target_cluster_uuid": SOURCE_UUID,
        "target_root_path": "/src/",
        "target_address": address,
        "target_port": port,
        "target_root_read_only": False,
        "blackout_windows": [],
        "blackout_window_timezone": "",
    }


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


def reads(fake):
    return [e.path for e in fake.log if e.method == "GET" and not e.background]


def run(run_module, verb, check_mode=False, diff=False, **args):
    return run_module(replication_reverse, {**CONNECTION, "relationship_id": REL_ID, "verb": verb, **args}, check_mode=check_mode, diff=diff)


def reverse(run_module, **args):
    return run(run_module, "reverse", source_address=SOURCE_ADDRESS, **args)


# make_writable


def test_make_writable_is_sent_to_the_target_from_established_and_the_record_read_once(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target())
    fake.expect("POST", MAKE_WRITABLE, body=None)
    fake.expect("GET", TARGET, response=target("DISCONNECTING"))

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["state"] == "DISCONNECTING", "the module returns what it reads, and never waits"
    assert result["relationship"]["target_path"] == "/dst"
    assert writes(fake) == [f"POST {MAKE_WRITABLE}"]
    assert fake_clock.sleeps == []


def test_make_writable_needs_allow_data_loss_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, "make_writable")

    assert result["failed"] is True
    assert result["msg"] == (
        f"make_writable sends the target directory of relationship {REL_ID} back to its recovery point, discarding whatever was "
        "written there after it; set allow_data_loss to allow it"
    )
    assert reads(fake) == [] and writes(fake) == []


def test_make_writable_needs_a_recovery_point(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target(recovery_point=""))

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["failed"] is True
    assert "has no recovery point yet" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("state", ["DISCONNECTING", "DISCONNECTED"])
def test_make_writable_already_done_sends_nothing(fake_api, run_module, state):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target(state))

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"]["state"] == state
    assert any("nothing was sent" in note for note in result["notes"])
    assert writes(fake) == []


@pytest.mark.parametrize("state", ["AWAITING_AUTHORIZATION", "RECONNECTING", "REESTABLISHING", "ENDED"])
def test_make_writable_from_any_other_state_is_refused(fake_api, run_module, state):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target(state))

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["failed"] is True
    assert result["msg"] == f"replication relationship {REL_ID} is {state} here; make_writable is sent only to an ESTABLISHED relationship"
    assert writes(fake) == []


def test_make_writable_on_a_cluster_without_the_target_record_is_refused(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, status=404, response=NOT_FOUND)

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["failed"] is True
    assert result["msg"] == (f"this cluster holds no target record of replication relationship {REL_ID}; make_writable is sent to the relationship's target")
    assert writes(fake) == []


def test_make_writable_in_check_mode_predicts_the_disconnect(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target())

    result = run(run_module, "make_writable", check_mode=True, diff=True, allow_data_loss=True)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["state"] == "DISCONNECTED"
    assert result["relationship"]["target_root_read_only"] is False
    assert result["diff"]["before"]["relationship"]["state"] == "ESTABLISHED"
    assert writes(fake) == []


def test_a_lost_make_writable_is_reconciled_by_reading_the_record(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target())
    fake.expect("POST", MAKE_WRITABLE, raises=LOST)
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["changed"] is True, result.get("msg")
    assert "the make_writable was reconciled after a lost connection" in result["notes"]
    assert writes(fake) == [f"POST {MAKE_WRITABLE}"]


def test_a_lost_make_writable_that_left_the_record_established_is_never_resent(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target())
    fake.expect("POST", MAKE_WRITABLE, raises=LOST)
    fake.expect("GET", TARGET, response=target())

    result = run(run_module, "make_writable", allow_data_loss=True)

    assert result["failed"] is True
    assert result["maybe_applied"] is True and result["changed"] is True
    assert "which is never resent; run again to read it and decide (G-REPL-ONESHOT)" in result["msg"]
    assert writes(fake) == [f"POST {MAKE_WRITABLE}"]


# reverse


def test_the_reverse_is_sent_to_the_current_target_with_the_source_s_address_and_port(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", REVERSE, body={"target_relationship_id": REL_ID, "source_address": SOURCE_ADDRESS, "source_port": 13712})
    fake.expect("GET", SOURCE, response=reversed_source(port=13712))

    result = reverse(run_module, source_port=13712)

    assert result["changed"] is True, result.get("msg")
    relationship = result["relationship"]
    assert relationship["id"] == REL_ID
    assert (relationship["source_path"], relationship["target_path"]) == ("/dst", "/src")
    assert (relationship["target_address"], relationship["target_port"]) == (SOURCE_ADDRESS, 13712)
    assert relationship["state"] == "DISCONNECTED"
    assert writes(fake) == [f"POST {REVERSE}"]
    assert fake_clock.sleeps == []


def test_the_reverse_sends_the_default_port(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", REVERSE, body={"target_relationship_id": REL_ID, "source_address": SOURCE_ADDRESS, "source_port": 3712})
    fake.expect("GET", SOURCE, response=reversed_source())

    result = reverse(run_module)

    assert result["changed"] is True, result.get("msg")


@pytest.mark.parametrize(("state", "first"), [("ESTABLISHED", ", so make_writable comes first"), ("DISCONNECTING", ""), ("RECONNECTING", "")])
def test_the_reverse_is_sent_only_from_disconnected(fake_api, run_module, state, first):
    # research R18: on an ESTABLISHED relationship the server answers 400 relationship_manager_not_disconnected_error.
    fake = fake_api()
    fake.expect("GET", TARGET, response=target(state))

    result = reverse(run_module)

    assert result["failed"] is True
    assert result["msg"] == f"replication relationship {REL_ID} is {state} here; the reverse is sent only to a DISCONNECTED relationship{first}"
    assert writes(fake) == []


def test_the_reverse_on_the_current_source_is_refused(fake_api, run_module):
    # research R18: on the source cluster the server answers 404; the module refuses before sending it.
    fake = fake_api()
    fake.expect("GET", TARGET, status=404, response=NOT_FOUND)
    fake.expect("GET", SOURCE, response=SOURCE_STATUS)

    result = reverse(run_module)

    assert result["failed"] is True
    assert result["msg"] == (
        f"this cluster holds only the source record of replication relationship {REL_ID}, to {SOURCE_STATUS['target_address']}:3712; the reverse "
        "is sent to the relationship's current target, with the current source's address as source_address"
    )
    assert writes(fake) == []


def test_the_reverse_on_a_cluster_with_no_record_is_refused(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, status=404, response=NOT_FOUND)
    fake.expect("GET", SOURCE, status=404, response=NOT_FOUND)

    result = reverse(run_module)

    assert result["failed"] is True
    assert result["msg"] == f"this cluster holds no record of replication relationship {REL_ID}; the reverse is sent to the relationship's current target"


def test_a_reverse_already_done_is_read_and_never_sent_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, status=404, response=NOT_FOUND)
    fake.expect("GET", SOURCE, response=reversed_source("ESTABLISHED"))

    result = reverse(run_module)

    assert result["changed"] is False, result.get("msg")
    assert result["relationship"]["target_address"] == SOURCE_ADDRESS
    assert any("so it was reversed before; nothing was sent" in note for note in result["notes"])
    assert writes(fake) == []


def test_the_reverse_needs_an_ip_literal_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, "reverse", source_address="hub01.example.com")

    assert result["failed"] is True
    assert result["msg"] == "source_address 'hub01.example.com' is not an IP address; the server takes only an IP literal"
    assert reads(fake) == []


def test_the_reverse_needs_a_source_address(fake_api, run_module):
    result = run(run_module, "reverse")

    assert result["failed"] is True
    assert "source_address" in result["msg"]


def test_the_reverse_in_check_mode_predicts_the_new_source_record(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))

    result = reverse(run_module, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    relationship = result["relationship"]
    assert relationship["source_cluster_uuid"] == CLUSTER_ID and relationship["target_cluster_uuid"] == SOURCE_UUID
    assert (relationship["source_path"], relationship["target_path"]) == ("/dst", "/src")
    assert (relationship["target_address"], relationship["target_port"], relationship["state"]) == (SOURCE_ADDRESS, 3712, "DISCONNECTED")
    assert writes(fake) == []


def test_a_lost_reverse_is_reconciled_by_reading_the_source_record(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", REVERSE, raises=LOST)
    fake.expect("GET", SOURCE, response=reversed_source())
    fake.expect("GET", SOURCE, response=reversed_source())

    result = reverse(run_module)

    assert result["changed"] is True, result.get("msg")
    assert "the reverse was reconciled after a lost connection" in result["notes"]
    assert writes(fake) == [f"POST {REVERSE}"]


def test_a_lost_reverse_that_left_no_source_record_is_never_resent(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", REVERSE, raises=LOST)
    fake.expect("GET", SOURCE, status=404, response=NOT_FOUND)

    result = reverse(run_module)

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "G-REPL-ONESHOT" in result["msg"]
    assert writes(fake) == [f"POST {REVERSE}"]


def test_the_server_s_not_disconnected_answer_is_a_refusal_that_changed_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", REVERSE, status=400, response=NOT_DISCONNECTED)

    result = reverse(run_module)

    assert result["failed"] is True
    assert result.get("changed") is False
    assert "maybe_applied" not in result
    assert result["msg"].endswith(f"; replication relationship {REL_ID} is no longer DISCONNECTED here, so make_writable comes first")


def test_an_unreachable_source_address_is_the_server_s_refusal(fake_api, run_module):
    # research R19: the server answers 400 replication_connection_error and leaves both records as they were.
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    unreachable = {"error_class": "replication_connection_error", "description": "Cannot communicate with the target cluster."}
    fake.expect("POST", REVERSE, status=400, response=unreachable)

    result = reverse(run_module)

    assert result["failed"] is True
    assert result["error_class"] == "replication_connection_error"
    assert "maybe_applied" not in result


# reconnect


def test_reconnect_is_sent_to_the_new_target_and_never_waits(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, body=None)
    fake.expect("GET", TARGET, response=target("RECONNECTING"))

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["state"] == "RECONNECTING"
    assert writes(fake) == [f"POST {RECONNECT}"]
    assert reads(fake) == [TARGET, TARGET]
    assert fake_clock.sleeps == []


# The reconnect's comparison without allow_data_loss (specs/025-replication-failover, FR-014 to FR-016): this cluster's /dst
# against its recovery point snapshot, through a snapshot taken now that expires an hour later.
SNAPSHOTS = "/v3/snapshots/"
NOW = datetime(2026, 11, 8, 6, 30, 0, tzinfo=UTC)
CHECK = {"source_file_id": "4100", "name_suffix": "reconnect_check", "expiration": "2026-11-09T06:30:00Z"}
CHANGES = "/v2/snapshots/77/changes-since/12?limit=1000"


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(snapshots, "utc_now", lambda: NOW)


def disconnected_with_snapshot(source_file_id="4100"):
    """R's target record here, DISCONNECTED, whose recovery point snapshot is of /dst, file 4100."""
    return target("DISCONNECTED", target_root_id="4100", recovery_point_snapshot={"id": 12, "timestamp": RECOVERY_POINT, "source_file_id": source_file_id})


def expect_comparison(fake, *pages):
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("POST", SNAPSHOTS, body=CHECK, response={"id": 77, "name": "77_reconnect_check"})
    url = CHANGES
    for index, entries in enumerate(pages):
        following = f"/v2/snapshots/77/changes-since/12?after={index + 1}&limit=1000" if index + 1 < len(pages) else None
        fake.expect("GET", url, response={"entries": entries, "paging": {"next": following}})
        url = following


def test_reconnect_without_allow_data_loss_compares_and_sends_it_when_nothing_changed(fake_api, run_module, frozen_now):
    fake = fake_api()
    expect_comparison(fake, [])
    fake.expect("DELETE", "/v3/snapshots/77")
    fake.expect("POST", RECONNECT, body=None)
    fake.expect("GET", TARGET, response=target("RECONNECTING"))

    result = run(run_module, "reconnect")

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST {SNAPSHOTS}", "DELETE /v3/snapshots/77", f"POST {RECONNECT}"]
    assert f"/dst holds no change since the recovery point {RECOVERY_POINT}, so the reconnect discards nothing" in result["notes"]


def test_reconnect_without_allow_data_loss_refuses_changes_naming_them_and_deletes_its_snapshot(fake_api, run_module, frozen_now):
    fake = fake_api()
    changes = [{"op": "MODIFY", "path": f"/dst/f{index}.txt"} for index in range(11)]
    expect_comparison(fake, changes[:6], changes[6:])
    fake.expect("DELETE", "/v3/snapshots/77")

    result = run(run_module, "reconnect")

    assert result["failed"] is True
    assert result["changed"] is False
    assert result["msg"] == (
        f"/dst changed since the recovery point {RECOVERY_POINT}: 11 changes ("
        + ", ".join(f"MODIFY /dst/f{index}.txt" for index in range(10))
        + f", and 1 more). The reconnect sends /dst back to the recovery point {RECOVERY_POINT}, discarding whatever was written there "
        "after it; copy what should be kept to the relationship's source first, or set allow_data_loss to discard it"
    )
    assert result["changes"] == changes
    assert result["change_count"] == 11
    assert writes(fake) == [f"POST {SNAPSHOTS}", "DELETE /v3/snapshots/77"]


def test_reconnect_comparison_that_fails_still_deletes_its_snapshot(fake_api, run_module, frozen_now):
    fake = fake_api()
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("POST", SNAPSHOTS, body=CHECK, response={"id": 77})
    fake.expect("GET", CHANGES, status=404, response={"error_class": "snapshot_not_found_error", "description": "no such snapshot"})
    fake.expect("DELETE", "/v3/snapshots/77")

    result = run(run_module, "reconnect")

    assert result["failed"] is True
    assert writes(fake) == [f"POST {SNAPSHOTS}", "DELETE /v3/snapshots/77"]


def test_reconnect_whose_snapshot_delete_fails_warns_that_it_expires(fake_api, run_module, frozen_now):
    fake = fake_api()
    expect_comparison(fake, [])
    fake.expect("DELETE", "/v3/snapshots/77", status=500, response={"error_class": "internal_error", "description": "boom"})
    fake.expect("POST", RECONNECT, body=None)
    fake.expect("GET", TARGET, response=target("RECONNECTING"))

    result = run(run_module, "reconnect")

    assert result["changed"] is True, result.get("msg")
    assert any("the comparison's snapshot 77 was not deleted" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "record",
    [target("DISCONNECTED"), disconnected_with_snapshot(source_file_id="9999")],
    ids=["no_recovery_point_snapshot", "a_snapshot_of_another_directory"],
)
def test_reconnect_without_a_recovery_point_snapshot_of_its_directory_is_refused_before_any_write(fake_api, run_module, record):
    fake = fake_api()
    fake.expect("GET", TARGET, response=record)
    fake.expect("GET", TARGET, response=record)

    result = run(run_module, "reconnect")

    assert result["failed"] is True
    assert result["msg"] == (
        f"reconnect sends /dst back to the recovery point {RECOVERY_POINT}, discarding whatever was written there after it, and the record names "
        "no recovery point snapshot of /dst to compare it with; set allow_data_loss to reconnect without comparing"
    )
    assert writes(fake) == []


def test_reconnect_comparison_stops_reading_at_its_cap(fake_api, run_module, frozen_now, monkeypatch):
    monkeypatch.setattr(replication_reverse_utils, "CHANGE_CAP", 3)
    fake = fake_api()
    changes = [{"op": "CREATE", "path": f"/dst/n{index}"} for index in range(4)]
    expect_comparison(fake, changes[:2], changes[2:])
    fake.expect("DELETE", "/v3/snapshots/77")

    result = run(run_module, "reconnect")

    assert result["msg"].startswith(
        f"/dst changed since the recovery point {RECOVERY_POINT}: at least 3 changes (CREATE /dst/n0, CREATE /dst/n1, CREATE /dst/n2, and more)"
    )
    assert result["changes"] == changes[:3]
    assert result["change_count"] is None


def test_reconnect_in_check_mode_takes_no_snapshot_and_notes_the_comparison(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())

    result = run(run_module, "reconnect", check_mode=True)

    assert result["changed"] is True
    assert result["relationship"]["state"] == "RECONNECTING"
    assert writes(fake) == [] and reads(fake) == [TARGET]
    assert any("the real run first compares /dst with the recovery point's snapshot" in note for note in result["notes"])


def test_reconnect_with_allow_data_loss_compares_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("POST", RECONNECT, body=None)
    fake.expect("GET", TARGET, response=target("RECONNECTING"))

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["changed"] is True
    assert writes(fake) == [f"POST {RECONNECT}"]


@pytest.mark.parametrize("state", ["RECONNECTING", "REESTABLISHING", "ESTABLISHED"])
def test_reconnect_already_accepted_sends_nothing(fake_api, run_module, state):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target(state))

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["changed"] is False, result.get("msg")
    assert result["notes"] == [f"replication relationship {REL_ID} is already {state} here, so it was reconnected before; nothing was sent"]
    assert writes(fake) == []


@pytest.mark.parametrize("state", ["AWAITING_AUTHORIZATION", "DISCONNECTING", "ENDED"])
def test_reconnect_from_any_other_state_is_refused(fake_api, run_module, state):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target(state))

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result["msg"] == f"replication relationship {REL_ID} is {state} here; reconnect is sent only to a DISCONNECTED relationship"
    assert writes(fake) == []


def test_reconnect_on_the_new_source_is_refused(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, status=404, response=NOT_FOUND)

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result["msg"] == (
        f"this cluster holds no target record of replication relationship {REL_ID}; reconnect is sent to the relationship's target, which after "
        "a reverse is the old source"
    )


def test_reconnect_in_check_mode_predicts_reconnecting(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))

    result = run(run_module, "reconnect", check_mode=True, allow_data_loss=True)

    assert result["changed"] is True, result.get("msg")
    assert result["relationship"]["state"] == "RECONNECTING"
    assert result["relationship"]["target_root_read_only"] is True
    assert writes(fake) == []


def test_a_lost_reconnect_is_reconciled_by_reading_the_record(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, raises=LOST)
    fake.expect("GET", TARGET, response=target("ESTABLISHED"))
    fake.expect("GET", TARGET, response=target("ESTABLISHED"))

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["changed"] is True, result.get("msg")
    assert "the reconnect was reconciled after a lost connection" in result["notes"]
    assert writes(fake) == [f"POST {RECONNECT}"]


def test_a_lost_reconnect_that_left_the_record_disconnected_is_never_resent(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, raises=LOST)
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert writes(fake) == [f"POST {RECONNECT}"]


def test_a_reconnect_refused_while_another_record_holds_the_directory_changed_nothing(fake_api, run_module):
    # research R18: the reconnect answers 409 while the old record still holds the directory. A definite refusal leaves
    # the record DISCONNECTED, which the restore reads to clear its mark.
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, status=409, response=HELD)

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result["error_class"] == "relationship_manager_target_contains_another_relationship_error"
    assert "maybe_applied" not in result


def test_the_server_s_not_disconnected_answer_to_a_reconnect_says_to_read_it_again(fake_api, run_module):
    # A reconnect a racing run already sent leaves the record RECONNECTING, which make_writable would not fix.
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, status=400, response=NOT_DISCONNECTED)

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result.get("changed") is False
    assert "maybe_applied" not in result
    assert result["msg"].endswith(
        f"; replication relationship {REL_ID} is no longer DISCONNECTED here; the reconnect needs the relationship DISCONNECTED, as the reverse "
        "leaves it, so run again to read it"
    )


def test_a_reconnect_the_server_answers_with_no_such_relationship_is_a_refusal_that_changed_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, status=404, response=NOT_FOUND)

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result.get("changed") is False
    assert "maybe_applied" not in result
    assert result["msg"] == (
        f"POST {RECONNECT} failed: HTTP 404 relationship_manager_relationship_not_found_error: The target relationship does not exist.; this "
        f"cluster no longer holds a target record of replication relationship {REL_ID}"
    )


def test_a_record_missing_after_the_verb_fails_as_changed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TARGET, response=target("DISCONNECTED"))
    fake.expect("POST", RECONNECT, body=None)
    fake.expect("GET", TARGET, status=404, response=NOT_FOUND)

    result = run(run_module, "reconnect", allow_data_loss=True)

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["msg"] == f"replication relationship {REL_ID} is missing here after the reconnect"


def test_reconnect_whose_comparison_snapshot_answer_is_lost_says_only_the_snapshot_may_exist(fake_api, run_module, frozen_now):
    fake = fake_api()
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("GET", TARGET, response=disconnected_with_snapshot())
    fake.expect("POST", SNAPSHOTS, body=CHECK, raises=LOST)

    result = run(run_module, "reconnect")

    assert result["failed"] is True
    assert result["changed"] is True
    assert "the comparison's snapshot of /dst may exist, and expires by itself a day after it was taken" in result["msg"]
    assert "maybe_applied" not in result
    assert writes(fake) == [f"POST {SNAPSHOTS}"]
