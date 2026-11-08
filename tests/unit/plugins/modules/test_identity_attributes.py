# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.identity_attributes, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import identity_attributes
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
ALICE = {"domain": "ACTIVE_DIRECTORY", "auth_id": "12884902889", "uid": None, "gid": None, "sid": "S-1-5-21-9-9-9-1110", "name": "CORP\\alice"}
UID_1000 = {"domain": "POSIX_USER", "auth_id": "12884902888", "uid": 1000, "gid": None, "sid": "S-1-5-88-1-1000", "name": None}
LOCAL = {"domain": "LOCAL", "auth_id": "1001", "uid": None, "gid": None, "sid": "S-1-5-21-1-2-3-1001", "name": "svc-backup"}
ATTRIBUTES = "/v1/auth/identities/{}/attributes"
NOT_SET = {"error_class": "auth_identity_not_found_error", "description": "System error"}


def setup(fake_api, *known):
    fake = fake_api()
    fake.identities = list(known)
    return fake


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("PUT", "DELETE") and not e.background]


def test_an_identity_without_attributes_gets_them(fake_api, run_module):
    path = ATTRIBUTES.format(ALICE["auth_id"])
    fake = setup(fake_api, ALICE)
    fake.expect("GET", path, status=404, response=NOT_SET)
    fake.expect("PUT", path, body={"home_directory": "/home/alice"}, response={"home_directory": "/home/alice"})
    fake.expect("GET", path, response={"home_directory": "/home/alice"})

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "ad:CORP\\alice", "home_directory": "/home/alice"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["identity_attributes"] == {"trustee": "ad:CORP\\alice", "home_directory": "/home/alice"}


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    path = ATTRIBUTES.format(UID_1000["auth_id"])
    fake = setup(fake_api, UID_1000)
    fake.expect("GET", path, response={"home_directory": "/home/u1000"})

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "uid:1000", "home_directory": "/home/u1000"})

    assert result["changed"] is False, result.get("msg")
    assert result["identity_attributes"] == {"trustee": "uid:1000", "home_directory": "/home/u1000"}
    assert writes(fake) == []


def test_a_new_home_directory_is_put(fake_api, run_module):
    path = ATTRIBUTES.format(UID_1000["auth_id"])
    fake = setup(fake_api, UID_1000)
    fake.expect("GET", path, response={"home_directory": "/home/old"})
    fake.expect("PUT", path, body={"home_directory": "/home/u1000"}, response={"home_directory": "/home/u1000"})
    fake.expect("GET", path, response={"home_directory": "/home/u1000"})

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "uid:1000", "home_directory": "/home/u1000"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"]["home_directory"] == "/home/old"
    assert result["diff"]["after"]["home_directory"] == "/home/u1000"


def test_absent_deletes_the_attributes(fake_api, run_module):
    path = ATTRIBUTES.format(ALICE["auth_id"])
    fake = setup(fake_api, ALICE)
    fake.expect("GET", path, response={"home_directory": "/home/alice"})
    fake.expect("DELETE", path)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "ad:CORP\\alice", "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["identity_attributes"] == {}
    assert writes(fake) == [f"DELETE {path}"]


def test_absent_without_attributes_is_unchanged_and_sends_no_delete(fake_api, run_module):
    """The cluster answers a DELETE of attributes it does not hold with auth_identity_not_found_error, so none is sent."""
    path = ATTRIBUTES.format(ALICE["auth_id"])
    fake = setup(fake_api, ALICE)
    fake.expect("GET", path, status=404, response=NOT_SET)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "ad:CORP\\alice", "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["real run", "check mode"])
def test_absent_for_a_trustee_that_does_not_exist_is_unchanged_with_a_note(fake_api, run_module, check_mode):
    """An identity whose account was deleted holds no attributes, so a removal of them has nothing to do, in either mode."""
    fake = setup(fake_api)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "ad:CORP\\gone", "state": "absent"}, check_mode=check_mode)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["identity_attributes"] == {}
    assert any("does not exist" in note for note in result["notes"]), result.get("notes")
    assert writes(fake) == []


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    path = ATTRIBUTES.format(UID_1000["auth_id"])
    fake = setup(fake_api, UID_1000)
    fake.expect("GET", path, status=404, response=NOT_SET)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "uid:1000", "home_directory": "/home/u1000"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


def test_a_local_identity_is_refused_for_local_user(fake_api, run_module):
    fake = setup(fake_api, LOCAL)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "sid:S-1-5-21-1-2-3-1001", "home_directory": "/home/svc"})

    assert result["failed"] is True
    assert "local_user" in result["msg"]
    assert writes(fake) == []


def test_a_local_trustee_is_refused_before_any_request(fake_api, run_module):
    fake = setup(fake_api)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "local:svc-backup", "home_directory": "/home/svc"})

    assert result["failed"] is True
    assert "local_user" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_bare_name_is_refused(fake_api, run_module):
    fake_api()

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "alice", "home_directory": "/home/alice"})

    assert result["failed"] is True
    assert "a bare name is ambiguous" in result["msg"]


def test_a_trustee_that_does_not_exist_fails(fake_api, run_module):
    setup(fake_api)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "ad:CORP\\nobody", "home_directory": "/home/nobody"})

    assert result["failed"] is True
    assert "ad:CORP\\nobody" in result["msg"]


def test_in_check_mode_a_trustee_that_does_not_exist_yet_is_a_change_with_a_note(fake_api, run_module):
    fake = setup(fake_api)

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "ad:CORP\\alice", "home_directory": "/home/alice"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert any("earlier task" in note for note in result["notes"]), result.get("notes")
    assert writes(fake) == []


def test_present_needs_a_home_directory(fake_api, run_module):
    fake_api()

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "uid:1000"})

    assert result["failed"] is True
    assert "home_directory" in result["msg"]


def test_a_lost_put_is_reconciled_by_reading_back(fake_api, run_module):
    path = ATTRIBUTES.format(UID_1000["auth_id"])
    fake = setup(fake_api, UID_1000)
    fake.expect("GET", path, status=404, response=NOT_SET)
    fake.expect("PUT", path, raises=TimeoutError("timed out"))
    fake.expect("GET", path, response={"home_directory": "/home/u1000"})

    result = run_module(identity_attributes, {**CONNECTION, "trustee": "uid:1000", "home_directory": "/home/u1000"})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]
