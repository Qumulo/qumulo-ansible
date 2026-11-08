# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.snapshot, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import snapshots
from ansible_collections.qumulo.cluster.plugins.modules import snapshot
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
SNAPSHOTS = "/v3/snapshots/"
STATUSES = "/v4/snapshots/status/?limit=1000"
SPOKES = "/v2/portal/spokes/"
NOW = datetime(2026, 11, 2, 2, 44, 35, 267716, tzinfo=UTC)
NOT_FOUND = error("fs_no_such_entry_error")


@pytest.fixture(autouse=True)
def now(monkeypatch):
    monkeypatch.setattr(snapshots, "utc_now", lambda: NOW, raising=False)


def taken(snapshot_id, name, expiration=""):
    """POST /v3/snapshots/'s answer."""
    return {
        "id": snapshot_id,
        "name": name,
        "timestamp": "2026-11-02T02:44:35.267716486Z",
        "source_file_id": "100",
        "policy_id": None,
        "expiration": expiration,
        "in_delete": False,
    }


def status(snapshot_id, name, path="/data/projects/", in_delete=False, expiration=""):
    """One entry of GET /v4/snapshots/status/."""
    return {**taken(snapshot_id, name, expiration), "source_file_path": path, "policy_name": None, "owners": [], "in_delete": in_delete, "lock_key": None}


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method == "POST" and not e.background]


def directory(fake, spokes=()):
    fake.expect("GET", attributes_path("/data/projects"), response=attributes("/data/projects", file_id="100"))
    fake.expect("GET", SPOKES, response={"entries": list(spokes)})


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(snapshot, {**CONNECTION, "path": "/data/projects", **args}, check_mode=check_mode, diff=diff)


def test_a_snapshot_without_a_name_or_expiration_lets_the_cluster_name_it(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect("POST", SNAPSHOTS, body={"expiration": "", "source_file_id": "100"}, response=taken(12, "12_OnDemand"))

    result = run(run_module)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["snapshot"] == {
        "id": 12,
        "name": "12_OnDemand",
        "path": "/data/projects",
        "timestamp": "2026-11-02T02:44:35.267716486Z",
        "expiration": "",
    }


@pytest.mark.parametrize(
    ("ttl", "expiration"),
    [
        ("90minutes", "2026-11-02T04:14:35Z"),
        ("12hours", "2026-11-02T14:44:35Z"),
        ("7days", "2026-11-09T02:44:35Z"),
        ("2weeks", "2026-11-16T02:44:35Z"),
        ("1month", "2026-12-02T02:44:35Z"),
        ("6months", "2027-05-02T02:44:35Z"),
        ("never", ""),
    ],
)
def test_a_time_to_live_becomes_an_expiration_from_now(fake_api, run_module, ttl, expiration):
    fake = fake_api()
    directory(fake)
    fake.expect("GET", STATUSES, response={"entries": []})
    fake.expect(
        "POST", SNAPSHOTS, body={"name_suffix": "nightly", "expiration": expiration, "source_file_id": "100"}, response=taken(12, "12_nightly", expiration)
    )

    result = run(run_module, name="nightly", expiration_time_to_live=ttl)

    assert not result.get("failed"), result.get("msg")
    assert result["snapshot"]["expiration"] == expiration


def test_a_month_from_the_31st_ends_on_the_last_day_of_a_shorter_month(fake_api, run_module, monkeypatch):
    monkeypatch.setattr(snapshots, "utc_now", lambda: datetime(2027, 1, 31, 12, 0, 0, tzinfo=UTC), raising=False)
    fake = fake_api()
    directory(fake)

    result = run(run_module, check_mode=True, expiration_time_to_live="1months")

    assert result["snapshot"]["expiration"] == "2027-02-28T12:00:00Z"


@pytest.mark.parametrize("ttl", ["7", "seven days", "7 fortnights", "-1days", "0days"])
def test_a_time_to_live_the_module_cannot_read_fails_before_any_request(fake_api, run_module, ttl):
    fake = fake_api()

    result = run(run_module, expiration_time_to_live=ttl)

    assert result["failed"] is True
    assert "expiration_time_to_live" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("name", ["a/b", ""])
def test_a_name_with_a_slash_or_empty_fails_before_any_request(fake_api, run_module, name):
    fake = fake_api()

    result = run(run_module, name=name)

    assert result["failed"] is True
    assert fake.log == []


def test_reuse_existing_returns_the_newest_snapshot_of_the_name_and_takes_none(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect(
        "GET",
        STATUSES,
        response={
            "entries": [
                status(3, "3_before-upgrade"),
                status(7, "7_before-upgrade", expiration="2026-11-16T02:44:35Z"),
                status(8, "8_before-upgrade", in_delete=True),
                status(9, "9_before-upgrade", path="/data/other/"),
                status(10, "10_xbefore-upgrade"),
            ]
        },
    )

    result = run(run_module, name="before-upgrade", reuse_existing=True, expiration_time_to_live="1days")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["snapshot"]["id"] == 7
    assert result["snapshot"]["expiration"] == "2026-11-16T02:44:35Z"
    assert writes(fake) == []


def test_reuse_existing_takes_a_snapshot_when_none_has_the_name(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect("GET", STATUSES, response={"entries": [status(3, "3_other")]})
    fake.expect("POST", SNAPSHOTS, body={"name_suffix": "before-upgrade", "expiration": "", "source_file_id": "100"}, response=taken(4, "4_before-upgrade"))

    result = run(run_module, name="before-upgrade", reuse_existing=True)

    assert result["changed"] is True
    assert result["snapshot"]["name"] == "4_before-upgrade"


def test_reuse_existing_needs_a_name(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, reuse_existing=True)

    assert result["failed"] is True
    assert "name" in result["msg"]
    assert fake.log == []


def test_check_mode_predicts_the_snapshot_with_diff_and_takes_none(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect("GET", STATUSES, response={"entries": []})

    result = run(run_module, check_mode=True, diff=True, name="nightly", reuse_existing=True, expiration_time_to_live="7days")

    planned = {"path": "/data/projects", "name": "nightly", "expiration": "2026-11-09T02:44:35Z"}
    assert result["changed"] is True
    assert result["snapshot"] == planned
    assert result["diff"] == {"before": {}, "after": planned}
    assert writes(fake) == []


def test_a_missing_directory_fails_naming_the_directory_module(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", attributes_path("/data/projects"), status=404, response=NOT_FOUND)

    result = run(run_module)

    assert result["failed"] is True
    assert "does not exist" in result["msg"] and "qumulo.cluster.directory" in result["msg"]


def test_a_missing_directory_is_a_predicted_snapshot_in_check_mode(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", attributes_path("/data/projects"), status=404, response=NOT_FOUND)

    result = run(run_module, check_mode=True)

    assert result["changed"] is True
    assert any("does not exist yet" in note for note in result["notes"])


SPOKE = {
    "id": 1,
    "hub_cluster_uuid": HUB_UUID,
    "hub_hosts": [],
    "type": "PORTAL_READ_WRITE",
    "state": "ACCEPTED",
    "status": "ACTIVE",
    "roots": [{"local_root": "200", "remote_root": "300", "remote_root_path": "/hub/", "authorized": True}],
}


@pytest.mark.parametrize("root", ["/data/projects", "/data"])
def test_a_directory_on_or_under_a_spoke_root_is_refused(fake_api, run_module, root):
    fake = fake_api()
    directory(fake, spokes=[SPOKE])
    fake.expect("GET", "/v1/files/200/info/attributes", response=attributes(root, file_id="200"))

    result = run(run_module, check_mode=True)

    assert result["failed"] is True
    assert f"spoke root {root}" in result["msg"] and "G-SPOKE-PATHS" in result["msg"]


def test_a_directory_holding_a_spoke_root_may_be_snapshotted(fake_api, run_module):
    fake = fake_api()
    directory(fake, spokes=[SPOKE])
    fake.expect("GET", "/v1/files/200/info/attributes", response=attributes("/data/projects/edge", file_id="200"))

    result = run(run_module, check_mode=True)

    assert result["changed"] is True


def test_a_lost_connection_after_the_post_finds_the_named_snapshot_by_listing(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect("GET", STATUSES, response={"entries": [status(3, "3_nightly")]})
    fake.expect("POST", SNAPSHOTS, raises=ConnectionResetError())
    fake.expect("GET", STATUSES, response={"entries": [status(3, "3_nightly"), status(5, "5_nightly")]})

    result = run(run_module, name="nightly")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["snapshot"]["id"] == 5
    assert any("reconciled" in note for note in result["notes"])
    assert writes(fake) == [f"POST {SNAPSHOTS}"]


def test_a_lost_connection_after_the_post_with_no_new_snapshot_fails_as_maybe_applied(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect("GET", STATUSES, response={"entries": [status(3, "3_nightly")]})
    fake.expect("POST", SNAPSHOTS, raises=ConnectionResetError())
    fake.expect("GET", STATUSES, response={"entries": [status(3, "3_nightly")]})

    result = run(run_module, name="nightly")

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "no new snapshot named nightly" in result["msg"]
    assert writes(fake) == [f"POST {SNAPSHOTS}"]


def test_a_lost_connection_without_a_name_fails_saying_a_snapshot_may_have_been_taken(fake_api, run_module):
    fake = fake_api()
    directory(fake)
    fake.expect("POST", SNAPSHOTS, raises=ConnectionResetError())

    result = run(run_module)

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert writes(fake) == [f"POST {SNAPSHOTS}"]
