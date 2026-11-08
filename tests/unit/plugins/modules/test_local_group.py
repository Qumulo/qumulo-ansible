# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.local_group, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import local_group
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
GROUPS, USERS = "/v1/groups/", "/v1/users/"
SID = "S-1-5-21-1534280521-3995163514-3722741250-"
BUILT_IN = [{"id": "513", "name": "Users", "sid": SID + "513", "gid": ""}, {"id": "514", "name": "Guests", "sid": SID + "514", "gid": "65534"}]


def group(gid="5000", group_id="1000", name="edge-admins"):
    return {"id": group_id, "name": name, "sid": SID + group_id, "gid": "" if gid is None else gid}


def user(name, user_id, primary="513"):
    return {"id": user_id, "name": name, "primary_group": primary, "sid": SID + user_id, "uid": "", "home_directory": None, "can_change_password": True}


ADMIN = user("admin", "500")
BACKUP = user("svc-backup", "1001")
OPERATOR = user("svc-ops", "1002")
PRIMARY = user("svc-primary", "1003", primary="1000")  # a member because edge-admins is its primary group


def expect_group(fake, found=True, members=(), gid="5000"):
    fake.expect("GET", GROUPS, response=[*BUILT_IN, group(gid)] if found else BUILT_IN)
    if found:
        fake.expect("GET", "/v1/groups/1000/members/", response=list(members))


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background and e.path != "/v1/identity/find"]


def test_absent_group_is_created_then_its_members_added(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, found=False)
    fake.expect("GET", USERS, response=[ADMIN, BACKUP])
    fake.expect("POST", GROUPS, body={"name": "edge-admins", "gid": "5000"}, if_match=NO_HEADER, response=group())
    fake.expect("POST", "/v1/groups/1000/members/", body={"member_id": "1001"}, response=BACKUP)
    expect_group(fake, members=[BACKUP])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "gid": 5000, "members": ["svc-backup"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["local_group"] == {"name": "edge-admins", "gid": 5000, "members": ["svc-backup"]}


def test_group_without_members_is_created_with_no_gid(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, found=False)
    fake.expect("POST", GROUPS, body={"name": "edge-admins", "gid": ""}, response=group(None))
    expect_group(fake, gid=None)

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins"})

    assert result["changed"] is True, result.get("msg")
    assert result["local_group"] == {"name": "edge-admins", "gid": None, "members": []}


def test_matching_group_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, members=[BACKUP, PRIMARY])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "gid": 5000, "members": ["svc-backup"], "purge": True})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["local_group"]["members"] == ["svc-backup", "svc-primary"]
    assert writes(fake) == []


def test_members_are_additive_without_purge(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, members=[OPERATOR])
    fake.expect("GET", USERS, response=[BACKUP, OPERATOR])
    fake.expect("POST", "/v1/groups/1000/members/", body={"member_id": "1001"}, response=BACKUP)
    expect_group(fake, members=[OPERATOR, BACKUP])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"]})

    assert result["changed"] is True
    assert result["local_group"]["members"] == ["svc-backup", "svc-ops"]
    assert writes(fake) == ["POST /v1/groups/1000/members/"]


def test_purge_removes_unlisted_members_but_never_a_primary_group_member(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, members=[BACKUP, OPERATOR, PRIMARY])
    fake.expect("GET", USERS, response=[BACKUP, OPERATOR, PRIMARY])
    fake.expect("DELETE", "/v1/groups/1000/members/1002")
    expect_group(fake, members=[BACKUP, PRIMARY])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"], "purge": True})

    assert result["changed"] is True
    assert writes(fake) == ["DELETE /v1/groups/1000/members/1002"]


def test_gid_change_puts_the_group_with_if_match_from_its_get(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, gid="4000")
    fake.expect("GET", "/v1/groups/1000", response=group("4000"), etag='"K8z0Sw"')
    fake.expect("PUT", "/v1/groups/1000", body={"id": "1000", "name": "edge-admins", "gid": "5000"}, if_match='"K8z0Sw"', response=group())
    expect_group(fake)

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "gid": 5000})

    assert result["changed"] is True, result.get("msg")
    assert result["local_group"]["gid"] == 5000


def test_gid_another_writer_set_since_the_read_is_not_written_again(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, gid="4000")
    fake.expect("GET", "/v1/groups/1000", response=group("4000"), etag='"K8z0Sw"')
    fake.expect("PUT", "/v1/groups/1000", if_match='"K8z0Sw"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", "/v1/groups/1000", response=group(), etag='"L9a1Tx"')
    expect_group(fake)

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "gid": 5000})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == ["PUT /v1/groups/1000"]


def test_member_added_by_another_writer_since_the_read_is_not_an_error(fake_api, run_module):
    fake = fake_api()
    expect_group(fake)
    fake.expect("GET", USERS, response=[BACKUP])
    already = {"error_class": "auth_user_already_in_group_error", "description": "user is already in the group"}
    fake.expect("POST", "/v1/groups/1000/members/", body={"member_id": "1001"}, status=409, response=already)
    expect_group(fake, members=[BACKUP])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"]})

    assert not result.get("failed"), result.get("msg")
    assert result["local_group"]["members"] == ["svc-backup"]


def test_member_post_refused_for_another_reason_fails(fake_api, run_module):
    fake = fake_api()
    expect_group(fake)
    fake.expect("GET", USERS, response=[BACKUP])
    refused = {"error_class": "auth_user_not_found_error", "description": "no such user"}
    fake.expect("POST", "/v1/groups/1000/members/", body={"member_id": "1001"}, status=404, response=refused)

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"]})

    assert result["failed"] is True
    assert "auth_user_not_found_error" in result["msg"]


def test_missing_member_fails_and_names_it(fake_api, run_module):
    fake = fake_api()
    expect_group(fake)
    fake.expect("GET", USERS, response=[ADMIN])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"]})

    assert result["failed"] is True
    assert "local user 'svc-backup' does not exist" in result["msg"]
    assert writes(fake) == []


def test_check_mode_reports_a_missing_member_as_a_change_with_a_note(fake_api, run_module):
    """In a fresh baseline run in check mode, the users the group lists are created by a later task."""
    fake = fake_api()
    expect_group(fake, found=False)
    fake.expect("GET", USERS, response=[ADMIN])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"]}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert "local user 'svc-backup' does not exist yet" in " ".join(result["notes"])
    assert writes(fake) == []


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, found=False)

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "gid": 5000}, check_mode=True)

    assert result["changed"] is True
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, members=[OPERATOR])
    fake.expect("GET", USERS, response=[BACKUP, OPERATOR])

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "members": ["svc-backup"], "purge": True}, check_mode=True, diff=True)

    assert result["diff"] == {
        "before": {"name": "edge-admins", "gid": 5000, "members": ["svc-ops"]},
        "after": {"name": "edge-admins", "gid": 5000, "members": ["svc-backup"]},
    }


def test_state_absent_deletes(fake_api, run_module):
    fake = fake_api()
    expect_group(fake)
    fake.expect("DELETE", "/v1/groups/1000")

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "state": "absent"})

    assert result["changed"] is True
    assert result["local_group"] == {}


def test_state_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_group(fake, found=False)

    result = run_module(local_group, {**CONNECTION, "name": "edge-admins", "state": "absent"})

    assert result["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize("name", ["Users", "Guests"])
def test_deleting_a_built_in_group_fails_before_any_request(fake_api, run_module, name):
    fake = fake_api()

    result = run_module(local_group, {**CONNECTION, "name": name, "state": "absent"})

    assert result["failed"] is True
    assert f"'{name}' is a built-in group, which the cluster never deletes" in result["msg"]
    assert fake.log == []
