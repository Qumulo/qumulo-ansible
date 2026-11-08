# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.snapshot_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import snapshot_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
DAILY = {"timezone": "UTC", "frequency": "SCHEDULE_DAILY_OR_WEEKLY", "hour": 2, "minute": 30, "on_days": ["MON", "FRI"]}
POLICY = {
    "id": 1,
    "policy_name": "projects-daily",
    "snapshot_name_template": "{ID}_{Policy}_{Directory}",
    "source_file_id": "100",
    "source_file_path": "/data/projects/",
    "schedule": {"id": 1, "creation_schedule": DAILY, "expiration_time_to_live": "7days"},
    "enabled": True,
    "lock_key": None,
    "owners": [],
}
SNAPSHOT = {
    "id": 3,
    "name": "3_projects-daily_projects",
    "timestamp": "2026-10-31T02:41:36.296864386Z",
    "source_file_id": "100",
    "source_file_path": "/data/projects/",
    "policy_id": 1,
    "policy_name": None,
    "expiration": "2026-11-07T02:41:36.296864386Z",
    "owners": [],
    "in_delete": False,
    "lock_key": None,
}
PUBLIC_KEY = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEexample"  # not a real key: the module only passes it through
KEY = {"id": "2", "name": "legal-hold", "comment": "holds", "disabled": False, "public_key": PUBLIC_KEY}


def test_default_subsets(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v4/snapshots/status/?limit=1000", response={"entries": [SNAPSHOT]})
    fake.expect("GET", "/v3/snapshots/policies/status/", response={"entries": [POLICY]})
    fake.expect("GET", "/v1/snapshots/total-used-capacity", response={"bytes": "4096"})
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": []})

    result = run_module(snapshot_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["snapshots"] == {
        "snapshots": [{**SNAPSHOT, "path": "/data/projects"}],
        "policies": [
            {
                "policy_name": "projects-daily",
                "path": "/data/projects",
                "enabled": True,
                "expiration_time_to_live": "7days",
                "snapshot_name_template": "{ID}_{Policy}_{Directory}",
                "schedule": DAILY,
                "lock_key": None,
            }
        ],
        "capacity": {"used_bytes": 4096},
        "security_keys": [],
    }


def test_snapshots_are_read_page_by_page(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v4/snapshots/status/?limit=1000", response={"entries": [SNAPSHOT], "paging": {"next": "/v4/snapshots/status/?after=3&limit=1000"}})
    fake.expect("GET", "/v4/snapshots/status/?after=3&limit=1000", response={"entries": [{**SNAPSHOT, "id": 4}], "paging": {"next": ""}})

    result = run_module(snapshot_info, {**CONNECTION, "gather_subset": ["snapshots"]})

    assert [s["id"] for s in result["snapshots"]["snapshots"]] == [3, 4]


def test_security_keys_come_back_in_security_key_s_shape_with_what_uses_each(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": [KEY, {**KEY, "id": "3", "name": "archive", "disabled": True}]})
    fake.expect(
        "GET",
        "/v1/file-system/security/keys/2/usages?limit=1000",
        response={"usages": [{"type": "snapshot_policy", "id": "1"}, {"type": "snapshot", "id": "3"}], "paging": {"next": None}},
    )
    fake.expect("GET", "/v1/file-system/security/keys/3/usages?limit=1000", response={"usages": [], "paging": {"next": None}})

    result = run_module(snapshot_info, {**CONNECTION, "gather_subset": ["security_keys"]})

    assert not result.get("failed"), result.get("msg")
    assert result["snapshots"] == {
        # Sorted by name, though the cluster listed legal-hold first.
        "security_keys": [
            {"id": "3", "name": "archive", "comment": "holds", "enabled": False, "public_key": PUBLIC_KEY, "usages": []},
            {
                "id": "2",
                "name": "legal-hold",
                "comment": "holds",
                "enabled": True,
                "public_key": PUBLIC_KEY,
                "usages": [{"type": "snapshot", "id": "3"}, {"type": "snapshot_policy", "id": "1"}],
            },
        ]
    }


def test_a_key_store_the_user_may_not_read_is_unavailable_and_the_rest_is_gathered(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v4/snapshots/status/?limit=1000", response={"entries": [SNAPSHOT]})
    fake.expect("GET", "/v3/snapshots/policies/status/", response={"entries": []})
    fake.expect("GET", "/v1/snapshots/total-used-capacity", response={"bytes": "0"})
    fake.expect("GET", "/v1/file-system/security/keys/", status=403, response={"error_class": "http_forbidden_error", "description": "no privilege"})

    result = run_module(snapshot_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert set(result["snapshots"]) == {"snapshots", "policies", "capacity"}
    assert "security_keys" in result["unavailable_subsets"]


def test_a_policy_s_lock_key_comes_back_by_name(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/policies/status/", response={"entries": [{**POLICY, "lock_key": "2"}]})
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": [KEY]})

    result = run_module(snapshot_info, {**CONNECTION, "gather_subset": ["policies"]})

    assert not result.get("failed"), result.get("msg")
    assert result["snapshots"]["policies"][0]["lock_key"] == "legal-hold"


def test_a_policy_s_lock_key_comes_back_by_id_with_a_warning_when_the_key_store_may_not_be_read(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/policies/status/", response={"entries": [{**POLICY, "lock_key": "2"}]})
    fake.expect("GET", "/v1/file-system/security/keys/", status=403, response={"error_class": "access_denied_error", "description": "no"})

    result = run_module(snapshot_info, {**CONNECTION, "gather_subset": ["policies"]})

    assert not result.get("failed"), result.get("msg")
    assert result["snapshots"]["policies"][0]["lock_key"] == "2"
    assert any("lock keys by id" in warning for warning in result["warnings"])


def test_a_policy_s_key_with_an_empty_name_comes_back_by_its_id(fake_api, run_module):
    """QUIRK-120: an empty name would read as no key."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/policies/status/", response={"entries": [{**POLICY, "lock_key": "6"}]})
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": [{**KEY, "id": "6", "name": ""}]})

    result = run_module(snapshot_info, {**CONNECTION, "gather_subset": ["policies"]})

    assert result["snapshots"]["policies"][0]["lock_key"] == "6"
