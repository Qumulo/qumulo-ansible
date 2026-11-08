# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.local_user, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import json

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import local_user
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
USERS, GROUPS, WHO_AM_I = "/v1/users/", "/v1/groups/", "/v1/session/who-am-i"
SID = "S-1-5-21-1534280521-3995163514-3722741250-"
GROUP_LIST = [
    {"id": "513", "name": "Users", "sid": SID + "513", "gid": ""},
    {"id": "514", "name": "Guests", "sid": SID + "514", "gid": "65534"},
    {"id": "1000", "name": "edge-admins", "sid": SID + "1000", "gid": "5000"},
]


def user(name="svc-backup", user_id="1001", primary="513", uid="", home=None):
    return {"id": user_id, "name": name, "primary_group": primary, "sid": SID + user_id, "uid": uid, "home_directory": home, "can_change_password": True}


ADMIN = user("admin", "500", uid="0")


def expect_user(fake, found=None):
    fake.expect("GET", USERS, response=[ADMIN, *([found] if found else [])])
    fake.expect("GET", GROUPS, response=GROUP_LIST)


def expect_put(fake, body, before=None, after=None):
    fake.expect("GET", "/v1/users/1001", response=before or user(), etag='"6hzAuA"')
    fake.expect("PUT", "/v1/users/1001", body=body, if_match='"6hzAuA"', response=after)
    fake.expect("GET", USERS, response=[ADMIN, after])


def put_body(**changes):
    return {"id": "1001", "name": "svc-backup", "primary_group": "513", "uid": "", "home_directory": None, "password": None, **changes}


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method not in ("GET",) and not e.background]


def test_absent_user_is_created_with_its_password(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)
    body = {"name": "svc-backup", "primary_group": "1000", "uid": "5001", "home_directory": None, "password": "pw-1"}
    fake.expect("POST", USERS, body=body, if_match=NO_HEADER, response=user(primary="1000", uid="5001"))
    fake.expect("GET", USERS, response=[ADMIN, user(primary="1000", uid="5001")])

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "primary_group": "edge-admins", "uid": 5001, "password": "pw-1"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["local_user"] == {"name": "svc-backup", "primary_group": "edge-admins", "uid": 5001, "home_directory": None}


def test_new_user_without_a_primary_group_joins_users(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)
    fake.expect("POST", USERS, body={"name": "svc-backup", "primary_group": "513", "uid": "", "home_directory": None, "password": "pw-1"}, response=user())
    fake.expect("GET", USERS, response=[ADMIN, user()])

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "password": "pw-1"})

    assert result["local_user"]["primary_group"] == "Users", result.get("msg")


def test_creating_without_a_password_fails_before_writing(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup"})

    assert result["failed"] is True
    assert "password is needed to create local user 'svc-backup'" in result["msg"]
    assert writes(fake) == []


def test_matching_user_with_update_password_on_create_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user(uid="5001"))

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "uid": 5001, "password": "pw-1"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_empty_home_directory_and_none_are_the_same(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user(home=""))

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "home_directory": ""})

    assert result["changed"] is False
    assert result["local_user"]["home_directory"] is None


def test_uid_change_puts_the_whole_user_without_a_password(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())
    expect_put(fake, put_body(uid="5001"), after=user(uid="5001"))

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "uid": 5001, "password": "pw-1"})

    assert result["changed"] is True, result.get("msg")
    assert result["local_user"]["uid"] == 5001


def test_update_password_always_sends_it_and_hides_it_from_the_diff(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())
    expect_put(fake, put_body(password="pw-2"), after=user())

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "password": "pw-2", "update_password": "always"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert "password" not in result["local_user"]
    assert "password" not in result["diff"]["after"]
    assert result["diff"]["after_header"] == "changed, not shown: password"
    assert "connection_password_changed" not in result
    assert "pw-2" not in json.dumps(result)


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "password": "pw-1"}, check_mode=True)

    assert result["changed"] is True
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "primary_group": "edge-admins"}, check_mode=True, diff=True)

    assert result["diff"]["before"] == {"name": "svc-backup", "primary_group": "Users", "uid": None, "home_directory": None}
    assert result["diff"]["after"] == {"name": "svc-backup", "primary_group": "edge-admins", "uid": None, "home_directory": None}


def test_missing_primary_group_fails_naming_it(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "primary_group": "it-missing", "password": "pw-1"})

    assert result["failed"] is True
    assert "local group 'it-missing' does not exist" in result["msg"]
    assert writes(fake) == []


def test_check_mode_reports_a_missing_primary_group_as_a_change_with_a_note(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "primary_group": "it-missing"}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert "local group 'it-missing' does not exist yet" in " ".join(result["notes"])


def test_admin_password_change_needs_allow_admin_password_change(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)

    result = run_module(local_user, {**CONNECTION, "api_username": "svc-ansible", "name": "admin", "password": "pw-2", "update_password": "always"})

    assert result["failed"] is True
    assert "allow_admin_password_change" in result["msg"]
    assert "console and SSH password" in result["msg"]
    assert writes(fake) == []


def test_password_change_of_the_connecting_user_needs_allow_management_disruption(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())

    result = run_module(local_user, {**CONNECTION, "api_username": "svc-backup", "name": "svc-backup", "password": "pw-2", "update_password": "always"})

    assert result["failed"] is True
    assert "the user this task connects as" in result["msg"]
    assert "allow_management_disruption" in result["msg"]
    assert writes(fake) == []


def test_connecting_user_found_through_who_am_i_with_an_access_token(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())
    fake.expect("GET", WHO_AM_I, response={**user(), "privileges": [], "access_token_id": "7"})
    connection = {**CONNECTION, "api_password": None, "api_token": "access-v1:abc"}

    result = run_module(local_user, {**connection, "name": "svc-backup", "password": "pw-2", "update_password": "always"})

    assert result["failed"] is True
    assert "the user this task connects as" in result["msg"]


def test_allowed_password_change_of_the_connecting_user_says_so(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())
    expect_put(fake, put_body(password="pw-2"), after=user())
    args = {"api_username": "svc-backup", "name": "svc-backup", "password": "pw-2", "update_password": "always", "allow_management_disruption": True}

    result = run_module(local_user, {**CONNECTION, **args})

    assert result["changed"] is True, result.get("msg")
    assert result["connection_password_changed"] is True
    assert fake.params["api_password"] == "pw-2"


def test_check_mode_password_change_of_the_connecting_user_does_not_say_so(fake_api, run_module):
    """F2: the role switches its connection password on this flag, and in check mode the cluster keeps the old one."""
    fake = fake_api()
    expect_user(fake, user())
    args = {"api_username": "svc-backup", "name": "svc-backup", "password": "pw-2", "update_password": "always", "allow_management_disruption": True}

    result = run_module(local_user, {**CONNECTION, **args}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert "connection_password_changed" not in result
    assert writes(fake) == []
    assert fake.params["api_password"] == "secret"


def test_password_change_of_the_token_s_user_keeps_the_token(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())
    fake.expect("GET", WHO_AM_I, response={**user(), "privileges": [], "access_token_id": "7"})
    expect_put(fake, put_body(password="pw-2"), after=user())
    connection = {**CONNECTION, "api_password": None, "api_token": "access-v1:abc"}
    args = {"name": "svc-backup", "password": "pw-2", "update_password": "always", "allow_management_disruption": True}

    result = run_module(local_user, {**connection, **args})

    assert result["changed"] is True, result.get("msg")
    assert result["connection_password_changed"] is True
    assert fake.params["api_password"] != "pw-2"


def test_state_absent_deletes(fake_api, run_module):
    fake = fake_api()
    expect_user(fake, user())
    fake.expect("DELETE", "/v1/users/1001")

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "state": "absent"})

    assert result["changed"] is True
    assert result["local_user"] == {}


def test_state_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_user(fake)

    result = run_module(local_user, {**CONNECTION, "name": "svc-backup", "state": "absent"})

    assert result["changed"] is False


@pytest.mark.parametrize("name", ["admin", "guest"])
def test_deleting_a_built_in_user_fails_before_any_request(fake_api, run_module, name):
    fake = fake_api()

    result = run_module(local_user, {**CONNECTION, "name": name, "state": "absent"})

    assert result["failed"] is True
    assert f"'{name}' is a built-in user, which the cluster never deletes" in result["msg"]
    assert fake.log == []
