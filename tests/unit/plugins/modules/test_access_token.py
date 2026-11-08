# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.access_token, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK and the
behaviour specs/023-access-tokens/plan.md R2 describes."""

from __future__ import annotations

import datetime
import stat

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils import client
from ansible_collections.qumulo.cluster.plugins.modules import access_token
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import ADMIN, GUEST, error, fake_with_identities

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
TOKEN_CONNECTION = {"api_host": "qumulo.example.com", "api_token": "access-v1:connecting-not-real", "api_password": "", "expected_cluster_uuid": CLUSTER_ID}
TOKENS = "/v1/auth/access-tokens/"
GUEST_TOKENS = f"{TOKENS}?user=auth_id%3A{GUEST['auth_id']}&limit=1000"
WHO_AM_I = "/v1/session/who-am-i"
OLD = "1000000000000000000001"
OTHER = "1000000000000000000002"
NEW = "1000000000000000000003"
BEARER = "access-v1:test-bearer-not-real"
FUTURE = "2099-01-01T00:00:00Z"
PAST = "2020-01-01T00:00:00Z"


def token(token_id, created="2026-11-01T00:00:00.5Z", enabled=True, expiration=None, user=None):
    return {
        "id": token_id,
        "user": dict(user or GUEST),
        "creator": dict(ADMIN),
        "creation_time": created,
        "expiration_time": expiration,
        "enabled": enabled,
    }


def listing(*entries):
    return {"entries": list(entries), "paging": {"next": None}}


def option(token_id, created="2026-11-01T00:00:00.5Z", enabled=True, expiration=None):
    return {"user": "local:guest", "id": token_id, "creator": "local:admin", "creation_time": created, "expiration_time": expiration, "enabled": enabled}


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def no_bearer_outside_its_key(result):
    rest = {key: value for key, value in result.items() if key != "bearer_token"}
    assert BEARER not in repr(rest)


@pytest.fixture
def secrets(tmp_path):
    """The secret destination: a directory the module makes."""
    return tmp_path / "artifacts" / "edge01" / "secrets"


def run(run_module, secrets=None, check_mode=False, diff=False, no_log=True, connection=None, **args):
    destination = {} if secrets is None else {"secret_destination": str(secrets)}
    return run_module(
        access_token, {**(connection or CONNECTION), "user": "local:guest", "_ansible_no_log": no_log, **destination, **args}, check_mode=check_mode, diff=diff
    )


def expect_make(fake, before=(), body=None, created="2026-11-02T00:00:00Z"):
    fake.expect("POST", TOKENS, body=body or {"user": {"auth_id": GUEST["auth_id"]}}, response={"id": NEW, "bearer_token": BEARER})
    fake.expect("GET", GUEST_TOKENS, response=listing(*before, token(NEW, created)))


# User Story 1: give a service account a token, once.


def test_a_user_without_a_token_gets_one_its_bearer_written_to_a_file_and_returned_once(fake_api, run_module, monkeypatch, secrets):
    registered = []
    monkeypatch.setattr(client, "register_secret", registered.append)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    expect_make(fake)

    result = run(run_module, secrets)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["access_tokens"] == [option(NEW, "2026-11-02T00:00:00Z")]
    assert result["bearer_token"] == BEARER
    assert result["access_token_id"] == NEW
    assert BEARER in registered
    written = secrets / f"access-token-local_guest-{NEW}.yml"
    assert result["secret_file"] == str(written)
    assert yaml.safe_load(written.read_text(encoding="utf-8")) == {"user": "local:guest", "access_token_id": NEW, "bearer_token": BEARER}
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o700
    no_bearer_outside_its_key(result)


def test_a_user_with_a_token_is_unchanged_and_no_bearer_is_returned(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))

    result = run(run_module, no_log=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["access_tokens"] == [option(OLD)]
    assert "bearer_token" not in result and "secret_file" not in result
    assert writes(fake) == []


def test_check_mode_makes_no_token_writes_no_file_and_returns_no_bearer(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())

    result = run(run_module, secrets, check_mode=True, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["access_tokens"] == [{"user": "local:guest", "id": None, "creator": None, "creation_time": None, "expiration_time": None, "enabled": True}]
    assert "bearer_token" not in result
    assert result["diff"]["before"] == {"user": "local:guest", "access_tokens": []}
    assert result["diff"]["after"]["access_tokens"] == [{"id": "(a new token)", "enabled": True, "expiration_time": None}]
    assert writes(fake) == []
    assert not secrets.exists()


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check mode"])
def test_a_task_without_no_log_that_would_make_a_token_fails_before_any_write(fake_api, run_module, secrets, check_mode):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())

    result = run(run_module, secrets, no_log=False, check_mode=check_mode)

    assert result["failed"] is True
    assert "no_log: true" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check mode"])
def test_a_task_without_a_secret_destination_that_would_make_a_token_fails_before_any_write(fake_api, run_module, check_mode):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())

    result = run(run_module, check_mode=check_mode)

    assert result["failed"] is True
    assert "secret_destination" in result["msg"]
    assert writes(fake) == []


def test_a_user_that_does_not_exist_yet_is_a_new_token_in_check_mode(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)

    result = run(run_module, secrets, check_mode=True, user="local:svc-new")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["access_tokens"][0]["user"] == "local:svc-new"
    assert any("local:svc-new" in note for note in result["notes"])
    assert writes(fake) == []


def test_a_lost_create_deletes_the_token_it_made_and_fails(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("POST", TOKENS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(NEW)))
    fake.expect("DELETE", f"{TOKENS}{NEW}")

    result = run(run_module, secrets, state="rotated")

    assert result["failed"] is True
    assert result["changed"] is True
    assert NEW in result["msg"] and "run the task again" in result["msg"]
    assert [e.path for e in writes(fake) if e.method == "DELETE"] == [f"{TOKENS}{NEW}"]


def test_a_lost_create_that_made_nothing_fails_saying_so(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_TOKENS, response=listing())

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert "no token was made" in result["msg"]


def test_a_bearer_the_file_cannot_take_deletes_the_token_and_keeps_the_old_ones(fake_api, run_module, monkeypatch, secrets):
    from ansible_collections.qumulo.cluster.plugins.module_utils import access_tokens

    def full(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(access_tokens, "write_secret_file", full)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("POST", TOKENS, response={"id": NEW, "bearer_token": BEARER})
    fake.expect("DELETE", f"{TOKENS}{NEW}")

    result = run(run_module, secrets, state="rotated")

    assert result["failed"] is True
    assert result["changed"] is True
    assert NEW in result["msg"] and "No space left on device" in result["msg"]
    assert [e.path for e in writes(fake) if e.method == "DELETE"] == [f"{TOKENS}{NEW}"]
    no_bearer_outside_its_key(result)


def test_a_token_the_listing_does_not_show_is_deleted_with_its_file(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, response={"id": NEW, "bearer_token": BEARER})
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("DELETE", f"{TOKENS}{NEW}")

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert NEW in result["msg"]
    assert list(secrets.iterdir()) == []
    no_bearer_outside_its_key(result)


# User Story 2: disable or expire a token.


def test_enabled_false_disables_each_token_without_if_match_and_a_second_run_changes_nothing(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER, enabled=False)))
    fake.expect("PATCH", f"{TOKENS}{OLD}", body={"enabled": False}, if_match=NO_HEADER, response=token(OLD, enabled=False))

    result = run(run_module, enabled=False, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert [entry["enabled"] for entry in result["access_tokens"]] == [False, False]
    assert result["diff"]["before"]["access_tokens"][0] == {"id": OLD, "enabled": True, "expiration_time": None}

    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, enabled=False)))
    again = run(run_module, enabled=False)
    assert again["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize(
    ("declared", "read_back"),
    [
        ("2030-01-01T00:00:00-08:00", "2030-01-01T08:00:00Z"),
        ("2030-06-01T12:00:00.500+02:00", "2030-06-01T10:00:00.5Z"),
        ("2030-06-01t12:00:00.123456789z", "2030-06-01T12:00:00.123456789Z"),
    ],
    ids=["an offset", "a fraction with zeros", "lower case"],
)
def test_an_expiration_time_is_compared_as_an_instant(fake_api, run_module, declared, read_back):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, expiration=read_back)))

    result = run(run_module, expiration_time=declared)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


@pytest.mark.parametrize(
    ("declared", "sent"),
    [("2031-01-01T00:00:00-08:00", "2031-01-01T08:00:00Z"), ("2031-01-01 08:00:00.250z", "2031-01-01T08:00:00.25Z")],
    ids=["an offset", "a space and a fraction"],
)
def test_a_different_expiration_time_is_sent_in_utc_with_z(fake_api, run_module, declared, sent):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, expiration="2030-01-01T08:00:00Z")))
    fake.expect("PATCH", f"{TOKENS}{OLD}", body={"expiration_time": sent}, response=token(OLD, expiration="2031-01-01T08:00:00Z"))

    result = run(run_module, expiration_time=declared)

    assert result["changed"] is True
    assert result["access_tokens"][0]["expiration_time"] == "2031-01-01T08:00:00Z"


def test_never_removes_an_expiration_and_leaves_a_token_without_one(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, expiration=FUTURE), token(OTHER)))
    fake.expect("PATCH", f"{TOKENS}{OLD}", body={"expiration_time": None}, response=token(OLD))

    result = run(run_module, expiration_time="never")

    assert result["changed"] is True
    assert [entry["expiration_time"] for entry in result["access_tokens"]] == [None, None]


@pytest.mark.parametrize("bad", ["2030-01-01T00:00:00", "2030-01-01", "tomorrow", "", "2030-13-01T00:00:00Z", "2030-01-01T00:00:00.1234567891Z"])
def test_an_expiration_time_outside_the_form_fails_before_any_request(fake_api, run_module, bad):
    fake = fake_with_identities(fake_api)

    result = run(run_module, expiration_time=bad)

    assert result["failed"] is True
    assert "expiration_time" in result["msg"] and "RFC 3339" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_past_expiration_time_for_a_token_the_task_would_make_fails_before_any_write(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())

    result = run(run_module, secrets, expiration_time=PAST)

    assert result["failed"] is True
    assert PAST in result["msg"]
    assert writes(fake) == []


def test_a_past_expiration_time_expires_a_token_that_exists(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("PATCH", f"{TOKENS}{OLD}", body={"expiration_time": PAST}, response=token(OLD, expiration=PAST))

    result = run(run_module, expiration_time=PAST)

    assert result["changed"] is True


def test_a_new_token_gets_the_declared_expiration_and_is_disabled_after_the_create(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, body={"user": {"auth_id": GUEST["auth_id"]}, "expiration_time": FUTURE}, response={"id": NEW, "bearer_token": BEARER})
    fake.expect("GET", GUEST_TOKENS, response=listing(token(NEW, expiration=FUTURE)))
    fake.expect("PATCH", f"{TOKENS}{NEW}", body={"enabled": False}, response=token(NEW, expiration=FUTURE, enabled=False))

    result = run(run_module, secrets, expiration_time=FUTURE, enabled=False)

    assert not result.get("failed"), result.get("msg")
    assert result["access_tokens"] == [option(NEW, expiration=FUTURE, enabled=False)]
    assert result["bearer_token"] == BEARER


def test_a_token_left_expired_is_warned_about_since_a_warning_survives_no_log(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, expiration=PAST)))

    result = run(run_module)

    assert result["changed"] is False
    assert any(OLD in warning and "expired" in warning for warning in result["warnings"])


# User Story 3: rotate a service's token.


def test_rotated_makes_and_stores_the_new_token_before_it_deletes_the_old_one(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    expect_make(fake, before=[token(OLD)])
    fake.expect("DELETE", f"{TOKENS}{OLD}")

    result = run(run_module, secrets, state="rotated", diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["bearer_token"] == BEARER
    assert result["access_tokens"] == [option(NEW, "2026-11-02T00:00:00Z")]
    assert [entry["id"] for entry in result["diff"]["before"]["access_tokens"]] == [OLD]
    assert [entry["id"] for entry in result["diff"]["after"]["access_tokens"]] == [NEW]
    assert [(e.method, e.path) for e in writes(fake)] == [("POST", TOKENS), ("DELETE", f"{TOKENS}{OLD}")]
    no_bearer_outside_its_key(result)


def test_rotated_for_a_user_with_two_tokens_fails_before_any_write_naming_both(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, expiration=PAST), token(OTHER)))

    result = run(run_module, secrets, state="rotated")

    assert result["failed"] is True
    assert OLD in result["msg"] and OTHER in result["msg"]
    assert "state: absent" in result["msg"]
    assert writes(fake) == []


def test_rotated_for_a_user_with_no_token_makes_one(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    expect_make(fake)

    result = run(run_module, secrets, state="rotated")

    assert result["changed"] is True
    assert result["bearer_token"] == BEARER


def test_rotated_in_check_mode_writes_nothing(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))

    result = run(run_module, secrets, state="rotated", check_mode=True, diff=True)

    assert result["changed"] is True
    assert [entry["id"] for entry in result["diff"]["after"]["access_tokens"]] == ["(a new token)"]
    assert "bearer_token" not in result
    assert writes(fake) == []


def test_an_old_token_another_client_deleted_counts_as_deleted(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    expect_make(fake, before=[token(OLD)])
    fake.expect("DELETE", f"{TOKENS}{OLD}", status=404, response=error("unknown_access_token_error"))

    result = run(run_module, secrets, state="rotated")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


# User Story 4: the token the task connects with.


@pytest.mark.parametrize(
    "args",
    [{"state": "absent"}, {"state": "rotated"}, {"enabled": False}, {"expiration_time": PAST}],
    ids=["absent", "rotated", "disabled", "expired"],
)
def test_the_token_in_use_is_not_cut_off_without_allow_management_disruption(fake_api, run_module, secrets, args):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})

    result = run(run_module, secrets, connection=TOKEN_CONNECTION, **args)

    assert result["failed"] is True
    assert OLD in result["msg"] and "allow_management_disruption" in result["msg"]
    assert writes(fake) == []


def test_rotating_the_token_in_use_deletes_it_last_and_says_the_connection_changed(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})
    expect_make(fake, before=[token(OLD)])
    fake.expect("DELETE", f"{TOKENS}{OLD}")

    result = run(run_module, secrets, connection=TOKEN_CONNECTION, state="rotated", allow_management_disruption=True)

    assert not result.get("failed"), result.get("msg")
    assert result["connection_token_changed"] is True
    assert result["bearer_token"] == BEARER
    assert fake.log[-1].method == "DELETE"


def test_revoking_the_token_in_use_says_the_connection_changed(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER)))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})
    fake.expect("DELETE", f"{TOKENS}{OTHER}")
    fake.expect("DELETE", f"{TOKENS}{OLD}")

    result = run(run_module, connection=TOKEN_CONNECTION, state="absent", allow_management_disruption=True)

    assert result["connection_token_changed"] is True


def test_a_token_not_in_use_is_removed_without_the_option(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("GET", WHO_AM_I, response={"id": ADMIN["auth_id"], "name": "admin", "access_token_id": OTHER})
    fake.expect("DELETE", f"{TOKENS}{OLD}")

    result = run(run_module, connection=TOKEN_CONNECTION, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert "connection_token_changed" not in result


def test_a_task_that_connects_with_a_password_reads_no_who_am_i(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("DELETE", f"{TOKENS}{OLD}")

    result = run(run_module, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert WHO_AM_I not in [e.path for e in fake.log]


# User Story 5: revoke a user's tokens.


def test_absent_deletes_every_token_and_a_second_run_changes_nothing(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER)))
    fake.expect("DELETE", f"{TOKENS}{OLD}")
    fake.expect("DELETE", f"{TOKENS}{OTHER}")

    result = run(run_module, state="absent", diff=True)

    assert result["changed"] is True
    assert result["access_tokens"] == []
    assert result["diff"]["after"]["access_tokens"] == []

    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    again = run(run_module, state="absent")
    assert again["changed"] is False
    assert writes(fake) == []


def test_absent_in_check_mode_deletes_nothing(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))

    result = run(run_module, state="absent", check_mode=True)

    assert result["changed"] is True
    assert writes(fake) == []


def test_absent_for_a_user_that_does_not_exist_changes_nothing(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, state="absent", user="local:svc-gone")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["access_tokens"] == []
    assert writes(fake) == []


def test_a_lost_delete_fails_as_maybe_applied(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("DELETE", f"{TOKENS}{OLD}", raises=ConnectionResetError("reset"))

    result = run(run_module, state="absent")

    assert result["failed"] is True
    assert result["maybe_applied"] is True


def test_a_bare_name_is_refused_before_any_request(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, user="guest")

    assert result["failed"] is True
    assert [e for e in fake.log if not e.background] == []


# Design review round 1.


@pytest.mark.parametrize("failing", ["disable", "delete"])
def test_a_failure_after_the_bearer_is_stored_names_the_new_token_and_its_file_and_keeps_the_old_ones(fake_api, run_module, secrets, failing):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    expect_make(fake, before=[token(OLD)])
    if failing == "disable":
        fake.expect("PATCH", f"{TOKENS}{NEW}", status=500, response=error("internal_error"))
    else:
        fake.expect("DELETE", f"{TOKENS}{OLD}", status=500, response=error("internal_error"))

    result = run(run_module, secrets, state="rotated", **({"enabled": False} if failing == "disable" else {}))

    assert result["failed"] is True
    assert result["changed"] is True
    assert NEW in result["msg"] and OLD in result["msg"]
    assert result["access_token_id"] == NEW
    assert result["secret_file"] == str(secrets / f"access-token-local_guest-{NEW}.yml")
    assert (secrets / f"access-token-local_guest-{NEW}.yml").exists()
    no_bearer_outside_its_key(result)


def test_an_expiration_within_the_hour_on_the_token_in_use_is_refused(fake_api, run_module):
    soon = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})

    result = run(run_module, connection=TOKEN_CONNECTION, expiration_time=soon)

    assert result["failed"] is True
    assert "allow_management_disruption" in result["msg"]
    assert writes(fake) == []


# Design review round 2.


def test_a_patch_that_cuts_off_the_token_in_use_is_sent_last(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER, created="2026-11-02T00:00:00Z")))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})
    fake.expect("PATCH", f"{TOKENS}{OTHER}", body={"enabled": False}, response=token(OTHER, created="2026-11-02T00:00:00Z", enabled=False))
    fake.expect("PATCH", f"{TOKENS}{OLD}", body={"enabled": False}, response=token(OLD, enabled=False))

    result = run(run_module, connection=TOKEN_CONNECTION, enabled=False, allow_management_disruption=True)

    assert not result.get("failed"), result.get("msg")
    assert [entry["id"] for entry in result["access_tokens"]] == [OLD, OTHER]


def test_a_patch_that_fails_after_another_succeeded_reports_a_change(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER, created="2026-11-02T00:00:00Z")))
    fake.expect("PATCH", f"{TOKENS}{OLD}", body={"enabled": False}, response=token(OLD, enabled=False))
    fake.expect("PATCH", f"{TOKENS}{OTHER}", status=500, response=error("internal_error"))

    result = run(run_module, enabled=False)

    assert result["failed"] is True
    assert result["changed"] is True
    assert OLD in result["msg"]


def test_a_lost_create_whose_listing_fails_too_says_a_token_may_be_left(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_TOKENS, status=500, response=error("internal_error"))

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["maybe_applied"] is True
    assert "state: rotated" in result["msg"]


def test_a_failure_after_the_bearer_is_stored_is_warned_about_since_no_log_hides_the_message(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    expect_make(fake, before=[token(OLD)])
    fake.expect("DELETE", f"{TOKENS}{OLD}", status=500, response=error("internal_error"))

    result = run(run_module, secrets, state="rotated")

    assert result["failed"] is True
    assert any(NEW in warning and OLD in warning and "secrets" in warning for warning in result["warnings"])
    assert BEARER not in repr(result["warnings"])


def test_a_confirming_listing_that_fails_names_the_kept_tokens(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("POST", TOKENS, response={"id": NEW, "bearer_token": BEARER})
    fake.expect("GET", GUEST_TOKENS, status=500, response=error("internal_error"))

    result = run(run_module, secrets, state="rotated")

    assert result["failed"] is True
    assert NEW in result["msg"] and OLD in result["msg"]


@pytest.mark.parametrize("state", ["present", "rotated"])
def test_a_user_that_does_not_exist_fails_outside_check_mode(fake_api, run_module, secrets, state):
    fake = fake_with_identities(fake_api)

    result = run(run_module, secrets, state=state, user="local:svc-gone")

    assert result["failed"] is True
    assert writes(fake) == []


def test_absent_for_a_user_that_does_not_exist_changes_nothing_in_check_mode(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, state="absent", user="local:svc-gone", check_mode=True)

    assert result["changed"] is False
    assert writes(fake) == []


# The docs review's code findings.


def test_a_delete_that_fails_after_another_succeeded_reports_a_change(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER, created="2026-11-02T00:00:00Z")))
    fake.expect("DELETE", f"{TOKENS}{OLD}")
    fake.expect("DELETE", f"{TOKENS}{OTHER}", status=500, response=error("internal_error"))

    result = run(run_module, state="absent")

    assert result["failed"] is True
    assert result["changed"] is True
    assert OLD in result["msg"]


def test_a_lost_create_whose_stray_token_cannot_be_deleted_says_to_remove_it_by_hand(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_TOKENS, response=listing(token(NEW)))
    fake.expect("DELETE", f"{TOKENS}{NEW}", status=500, response=error("internal_error"))

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert result["changed"] is True
    assert NEW in result["msg"] and "by hand" in result["msg"]


def test_a_missing_token_whose_file_cannot_be_removed_reports_a_change(fake_api, run_module, monkeypatch, secrets):
    from ansible_collections.qumulo.cluster.plugins.module_utils import access_tokens

    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, response={"id": NEW, "bearer_token": BEARER})
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("DELETE", f"{TOKENS}{NEW}")

    def busy(path):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr(access_tokens, "_unlink", busy, raising=False)

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert result["changed"] is True
    assert "Device or resource busy" in result["msg"]


# The walkthrough review of the pull request.


def test_the_two_token_refusal_marks_the_token_in_use_and_names_the_delete_by_id(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD), token(OTHER, created="2026-11-02T00:00:00Z", expiration=PAST)))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})

    result = run(run_module, secrets, connection=TOKEN_CONNECTION, state="rotated")

    assert result["failed"] is True
    assert f"{OLD} (in use)" in result["msg"]
    assert f"{OTHER} (expired)" in result["msg"]
    assert "qq auth_delete_access_token" in result["msg"]
    assert writes(fake) == []


def test_a_failure_after_the_store_says_how_to_delete_the_old_token_by_id(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    expect_make(fake, before=[token(OLD)])
    fake.expect("DELETE", f"{TOKENS}{OLD}", status=500, response=error("internal_error"))

    result = run(run_module, secrets, state="rotated")

    assert f"qq auth_delete_access_token {OLD}" in result["msg"]
    assert any(f"qq auth_delete_access_token {OLD}" in warning for warning in result["warnings"])
    no_bearer_outside_its_key(result)


def test_a_lost_delete_of_an_old_token_says_it_may_be_gone(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    expect_make(fake, before=[token(OLD)])
    fake.expect("DELETE", f"{TOKENS}{OLD}", raises=ConnectionResetError("reset"))

    result = run(run_module, secrets, state="rotated")

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert f"{OLD} may have been deleted" in result["msg"]
    assert "kept the old tokens" not in result["msg"]
    assert f"if {OLD} is still listed" in result["msg"]
    assert any(f"qq auth_delete_access_token {OLD}" in warning and "G-TOKEN" in warning for warning in result["warnings"])
    no_bearer_outside_its_key(result)


def test_a_lost_create_with_no_new_token_listed_says_what_it_saw(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing())
    fake.expect("POST", TOKENS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_TOKENS, response=listing())

    result = run(run_module, secrets)

    assert "the listing after it shows no new token" in result["msg"]


def test_the_expired_warning_says_how_to_make_the_token_log_in_again(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD, expiration=PAST)))

    result = run(run_module)

    assert any("expiration_time" in warning and "state: rotated" in warning for warning in result["warnings"])


def test_rotating_the_token_in_use_into_a_disabled_one_is_refused(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_TOKENS, response=listing(token(OLD)))
    fake.expect("GET", WHO_AM_I, response={"id": GUEST["auth_id"], "name": "guest", "access_token_id": OLD})

    result = run(run_module, secrets, connection=TOKEN_CONNECTION, state="rotated", enabled=False, allow_management_disruption=True)

    assert result["failed"] is True
    assert "enabled: false" in result["msg"]
    assert writes(fake) == []
