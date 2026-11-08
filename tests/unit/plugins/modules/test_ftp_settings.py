# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.ftp_settings, driven through FakeQumulo. Bodies follow the OpenAPI document's FTP settings schema."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import ftp_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v0/ftp/settings"
GREETING = "Welcome.\n"
STORED = {
    "enabled": False,
    "check_remote_host": True,
    "log_operations": True,
    "chroot_users": False,
    "allow_unencrypted_connections": True,
    "expand_wildcards": False,
    "anonymous_user": None,
    "greeting": GREETING,
}
OPTIONS = {**STORED, "anonymous_user": ""}
USERS = "/v1/users/"
LOCAL_USERS = [{"id": "500", "name": "admin"}, {"id": "501", "name": "guest"}, {"id": "1000", "name": "ftp-guest"}]


def guest(name):
    return {"id_type": "LOCAL_USER", "id_value": name}


def writes(fake):
    return [e for e in fake.log if e.method == "PATCH"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(ftp_settings, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_matching_settings_send_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')

    result = run(run_module, enabled=False, chroot_users=False, anonymous_user="", greeting=GREETING)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["ftp_settings"] == OPTIONS
    assert writes(fake) == []


def test_a_change_patches_only_the_differing_fields_with_the_reads_if_match(fake_api, run_module):
    after = {**STORED, "enabled": True, "allow_unencrypted_connections": False, "chroot_users": True}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect(
        "PATCH", SETTINGS, body={"enabled": True, "allow_unencrypted_connections": False, "chroot_users": True}, if_match='"f1"', response={"enabled": True}
    )
    fake.expect("GET", SETTINGS, response=after, etag='"f2"')

    result = run(run_module, enabled=True, allow_unencrypted_connections=False, chroot_users=True, log_operations=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["ftp_settings"] == {**OPTIONS, "enabled": True, "allow_unencrypted_connections": False, "chroot_users": True}


def test_a_412_reads_again_and_sends_only_what_still_differs(fake_api, run_module):
    """Another client turned FTP on between the read and the PATCH; only chroot_users is left to write."""
    raced = {**STORED, "enabled": True}
    after = {**raced, "chroot_users": True}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect(
        "PATCH", SETTINGS, body={"enabled": True, "chroot_users": True}, if_match='"f1"', status=412, response={"error_class": "db_revision_mismatch_error"}
    )
    fake.expect("GET", SETTINGS, response=raced, etag='"f2"')
    fake.expect("PATCH", SETTINGS, body={"chroot_users": True}, if_match='"f2"', response={"chroot_users": True})
    fake.expect("GET", SETTINGS, response=after, etag='"f3"')

    result = run(run_module, enabled=True, chroot_users=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_check_mode_reports_the_change_with_a_diff_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')

    result = run(run_module, check_mode=True, diff=True, enabled=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": OPTIONS, "after": {**OPTIONS, "enabled": True}}
    assert writes(fake) == []


def test_a_read_back_that_differs_fails_with_expected_and_observed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"chroot_users": True}, response={"chroot_users": True})
    fake.expect("GET", SETTINGS, response=STORED, etag='"f2"')

    result = run(run_module, chroot_users=True)

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["expected"] == {"chroot_users": True}
    assert result["observed"] == {"chroot_users": False}


# The greeting (G-FTP-GREETING)


def test_a_greeting_is_compared_exactly_with_its_trailing_newline(fake_api, run_module):
    after = {**STORED, "greeting": "Welcome."}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"greeting": "Welcome."}, response={"greeting": "Welcome."})
    fake.expect("GET", SETTINGS, response=after, etag='"f2"')

    result = run(run_module, greeting="Welcome.")

    assert result["changed"] is True
    assert result["ftp_settings"]["greeting"] == "Welcome."


@pytest.mark.parametrize("check_mode", [False, True])
def test_an_empty_greeting_fails_before_any_request(fake_api, run_module, check_mode):
    fake = fake_api()

    result = run(run_module, check_mode=check_mode, greeting="")

    assert result["failed"] is True
    assert "G-FTP-GREETING" in result["msg"] and "factory greeting" in result["msg"]
    assert fake.log == []


def test_a_greeting_over_1024_bytes_fails_before_any_request(fake_api, run_module):
    """The cluster counts bytes of UTF-8: 513 two-byte characters are 1026 bytes."""
    fake = fake_api()

    result = run(run_module, greeting="é" * 513)

    assert result["failed"] is True
    assert "1026 bytes" in result["msg"] and "1024" in result["msg"]
    assert fake.log == []


def test_a_greeting_of_1024_bytes_is_sent(fake_api, run_module):
    greeting = "é" * 512
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"greeting": greeting}, response={"greeting": greeting})
    fake.expect("GET", SETTINGS, response={**STORED, "greeting": greeting}, etag='"f2"')

    result = run(run_module, greeting=greeting)

    assert not result.get("failed"), result.get("msg")


# The anonymous user


def test_a_local_user_is_sent_as_a_local_user_identity(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"anonymous_user": guest("ftp-guest")}, if_match='"f1"', response={"anonymous_user": guest("ftp-guest")})
    fake.expect("GET", SETTINGS, response={**STORED, "anonymous_user": guest("ftp-guest")}, etag='"f2"')

    result = run(run_module, anonymous_user="local:ftp-guest")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["ftp_settings"]["anonymous_user"] == "local:ftp-guest"


def test_an_empty_anonymous_user_is_sent_as_null(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "anonymous_user": guest("ftp-guest")}, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"anonymous_user": None}, response={"anonymous_user": None})
    fake.expect("GET", SETTINGS, response=STORED, etag='"f2"')

    result = run(run_module, anonymous_user="")

    assert result["changed"] is True
    assert result["ftp_settings"]["anonymous_user"] == ""


@pytest.mark.parametrize("check_mode", [False, True])
def test_the_same_user_in_another_case_is_no_change_and_keeps_the_clusters_spelling(fake_api, run_module, check_mode):
    """The cluster finds a local user by its name in any case and reads back the user's own spelling; no list is read."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "anonymous_user": guest("ftp-guest")}, etag='"f1"')

    result = run(run_module, check_mode=check_mode, diff=True, anonymous_user="LOCAL: FTP-Guest ")

    assert result["changed"] is False
    assert result["ftp_settings"]["anonymous_user"] == "local:ftp-guest"
    assert result["diff"]["before"]["anonymous_user"] == result["diff"]["after"]["anonymous_user"] == "local:ftp-guest"
    assert writes(fake) == []


def test_a_declared_name_is_written_stripped_and_read_back_in_the_clusters_spelling(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"anonymous_user": guest("FTP-Guest")}, response={"anonymous_user": guest("FTP-Guest")})
    fake.expect("GET", SETTINGS, response={**STORED, "anonymous_user": guest("ftp-guest")}, etag='"f2"')

    result = run(run_module, anonymous_user=" local:FTP-Guest")

    assert not result.get("failed"), result.get("msg")
    assert result["ftp_settings"]["anonymous_user"] == "local:ftp-guest"


def test_a_deleted_anonymous_user_reads_as_local_and_an_empty_one_clears_it(fake_api, run_module):
    deleted = {**STORED, "anonymous_user": guest("")}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=deleted, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"anonymous_user": None}, response={"anonymous_user": None})
    fake.expect("GET", SETTINGS, response=STORED, etag='"f2"')

    result = run(run_module, diff=True, anonymous_user="")

    assert result["changed"] is True
    assert result["diff"]["before"]["anonymous_user"] == "local:"


def test_a_deleted_anonymous_user_is_left_alone_by_a_task_that_does_not_declare_one(fake_api, run_module):
    deleted = {**STORED, "anonymous_user": guest("")}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=deleted, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"enabled": True}, response={"enabled": True})
    fake.expect("GET", SETTINGS, response={**deleted, "enabled": True}, etag='"f2"')

    result = run(run_module, enabled=True)

    assert not result.get("failed"), result.get("msg")
    assert result["ftp_settings"]["anonymous_user"] == "local:"


def test_an_identity_type_the_module_does_not_know_still_reads(fake_api, run_module):
    """The cluster refuses to store anything but a local user today; a read of another must not fail the task."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "anonymous_user": {"id_type": "QUMULO_OPERATOR", "id_value": "x"}}, etag='"f1"')
    fake.expect("GET", USERS, response=LOCAL_USERS)

    result = run(run_module, check_mode=True, anonymous_user="local:ftp-guest")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize(
    "value", ["uid:1000", "gid:100", "sid:S-1-5-21-1-2-3-1001", "Everyone", "ad:CORP\\alice", "local:", "alice", "uid:abc", "internal:File Owner"]
)
def test_anything_but_a_local_user_fails_before_any_request_with_one_message(fake_api, run_module, value):
    fake = fake_api()

    result = run(run_module, anonymous_user=value)

    assert result["failed"] is True
    assert result["msg"].startswith("anonymous_user must be a local user, written local:<name>")
    assert fake.log == []


def test_a_user_the_cluster_lacks_fails_naming_it_and_local_user(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, status=404, response={"error_class": "auth_identity_not_found_error", "description": "nobody"})

    result = run(run_module, anonymous_user="local:nobody")

    assert result["failed"] is True
    assert "local:nobody" in result["msg"] and "qumulo.cluster.local_user" in result["msg"]
    assert "auth_identity_not_found_error" in result["msg"]


def test_a_user_the_cluster_lacks_is_a_change_with_a_note_in_check_mode(fake_api, run_module):
    """A check run reads the user list, as local_group and s3_access_key do, so a misspelled user shows before the apply."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("GET", USERS, response=LOCAL_USERS)

    result = run(run_module, check_mode=True, anonymous_user="local:made-by-an-earlier-task")

    assert result["changed"] is True
    assert result["notes"] == ["local user 'made-by-an-earlier-task' does not exist yet; an earlier task must create it"]
    assert result["ftp_settings"]["anonymous_user"] == "local:made-by-an-earlier-task"
    assert writes(fake) == []


def test_a_check_run_shows_a_changed_user_in_the_clusters_spelling(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("GET", USERS, response=LOCAL_USERS)

    result = run(run_module, check_mode=True, diff=True, anonymous_user="local:FTP-GUEST")

    assert result["changed"] is True
    assert result["diff"]["after"]["anonymous_user"] == "local:ftp-guest"
    assert "notes" not in result


def test_a_run_outside_check_mode_reads_no_user_list(fake_api, run_module):
    """The cluster's refusal names a missing user, and the read-back gives its spelling, so only a check run needs the list."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, body={"anonymous_user": guest("FTP-GUEST")}, response={"anonymous_user": guest("FTP-GUEST")})
    fake.expect("GET", SETTINGS, response={**STORED, "anonymous_user": guest("ftp-guest")}, etag='"f2"')

    result = run(run_module, anonymous_user="local:FTP-GUEST")

    assert not result.get("failed"), result.get("msg")
    assert USERS not in [e.path for e in fake.log]


def test_a_local_groups_name_fails_with_the_clusters_message(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"f1"')
    fake.expect("PATCH", SETTINGS, status=400, response={"error_class": "http_bad_request_error", "description": "Users is not a user"})

    result = run(run_module, anonymous_user="local:Users")

    assert result["failed"] is True
    assert "Users is not a user" in result["msg"]
