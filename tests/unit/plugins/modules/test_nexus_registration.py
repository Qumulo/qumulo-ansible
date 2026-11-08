# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.nexus_registration, driven through FakeQumulo and FakeNexus. Nexus is never called."""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import nexus
from ansible_collections.qumulo.cluster.plugins.modules import nexus_registration
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_nexus import ACCOUNT_ID, NEXUS_TOKEN, ok
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
REGISTRATION = "/v1/nexus/registration"
ROTATE = "/v1/nexus/registration/rotate"
SUPPORT = "/v1/support/settings"
API_TOKENS = "/api/v1/api_tokens"
NONE = {"nexus_enabled": True, "registration_status": "NONE", "secret_created_at": None}
PENDING = {**NONE, "registration_status": "PENDING_REGISTRATION"}
REGISTERED = {**NONE, "registration_status": "REGISTERED", "secret_created_at": "2026-10-31T02:00:00Z"}
ROTATED = {"monitor_uri": "/v1/nexus/registration"}
DNS_ERROR = {"error_class": "dns_resolution_error", "description": "api.nexus.qumulo.com: Name or service not known"}
MINT = {"token_type": "CLUSTER_OTP", "description": f"qumulo.cluster: register cluster {CLUSTER_ID}"}
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))


def cluster_writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


def expect_register(fake, key, after=None):
    fake.expect("PUT", REGISTRATION, body={"join_key": key}, response=PENDING)
    fake.expect("POST", ROTATE, status=202, response=ROTATED)
    fake.expect("GET", REGISTRATION, response=after or REGISTERED)


@pytest.fixture
def registered(monkeypatch):
    values = []
    monkeypatch.setattr(nexus, "register_secret", values.append)
    return values


def test_registered_is_unchanged_and_mints_nothing(fake_api, fake_nexus, run_module):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1", "nexus_api_token": NEXUS_TOKEN})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["registration"] == REGISTERED
    assert cluster_writes(fake) == []
    assert nexus_fake.log == []


def test_registration_posts_rotate_for_a_synchronous_result(fake_api, run_module):
    """QUIRK-011: the status never reports ERROR_REGISTERING, so the PUT is followed by rotate, which registers inline."""
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    expect_register(fake, "KEY1")

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert result["changed"] is True, result.get("msg")
    assert result["registration"] == REGISTERED
    assert result["join_key_source"] == "given"
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}"]


RACED = {"error_class": "db_revision_mismatch_error", "description": "Entry DB_SUPER_SK_GLOBAL_VALUE_MONITORING hash mismatch"}


def test_a_rotate_that_races_the_clusters_own_registration_reads_the_status(fake_api, run_module):
    """QUIRK-018: a stored key registers the cluster by itself within seconds; a rotate in that window answers 412."""
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, status=412, response=RACED)
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["registration"] == REGISTERED
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}"]


def test_a_raced_rotate_is_sent_again_while_the_status_is_pending(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, status=412, response=RACED)
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=202, response=ROTATED)
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert not result.get("failed"), result.get("msg")
    assert result["registration"] == REGISTERED
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}", f"POST {ROTATE}"]
    assert fake_clock.sleeps == [2]


def test_a_rotate_that_keeps_racing_fails_as_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    for _attempt in range(2):
        fake.expect("POST", ROTATE, status=412, response=RACED)
        fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=412, response=RACED)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "db_revision_mismatch_error" in result["msg"]
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}"] + [f"POST {ROTATE}"] * 3


# What rotate answers when the gateway in front of Nexus fails the cluster's call: the cluster cannot parse the page.
GATEWAY_PAGE = "<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n</body>\r\n</html>\r\n"


def upstream_page(body):
    return {"error_class": "decode_error", "description": f"JSON error: Failed to parse JSON: expected value at line 1 column 1: body was: {body}"}


def test_a_rotate_answered_with_a_nexus_gateway_page_is_sent_again_while_pending(fake_api, run_module, fake_clock):
    """QUIRK-022: a gateway in front of Nexus that fails answers with an HTML page, which rotate reports as decode_error."""
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, status=400, response=upstream_page(GATEWAY_PAGE))
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=202, response=ROTATED)
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert not result.get("failed"), result.get("msg")
    assert result["registration"] == REGISTERED
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}", f"POST {ROTATE}"]
    assert fake_clock.sleeps == [2]


def test_a_nexus_gateway_page_is_retried_only_until_retry_timeout(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    for _attempt in range(4):
        fake.expect("POST", ROTATE, status=400, response=upstream_page(GATEWAY_PAGE))
        fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=400, response=upstream_page(GATEWAY_PAGE))

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1", "retry_timeout": 20})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "502 Bad Gateway" in result["msg"]
    assert fake_clock.sleeps == [2, 4, 8, 6]  # backing off, and the last pause ends at the timeout
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}"] + [f"POST {ROTATE}"] * 5


def test_a_nexus_gateway_page_once_the_key_is_gone_fails_without_another_rotate(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, status=400, response=upstream_page(GATEWAY_PAGE))
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "502 Bad Gateway" in result["msg"]
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}"]
    assert fake_clock.sleeps == []


@pytest.mark.parametrize(
    ("status", "error", "shown"),
    [
        (500, DNS_ERROR, "dns_resolution_error"),
        (400, upstream_page("503 Service Unavailable"), "503 Service Unavailable"),
        (400, upstream_page("<html><head><title>404 Not Found</title></head></html>"), "404 Not Found"),
    ],
    ids=["cluster error", "plain-text page", "HTML 4xx page"],
)
def test_a_rotate_failure_other_than_a_gateway_page_fails_at_once(fake_api, run_module, fake_clock, status, error, shown):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, status=status, response=error)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert shown in result["msg"]
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}"]
    assert fake_clock.sleeps == []


def test_join_key_is_masked_as_a_secret():
    assert nexus_registration.ARGUMENT_SPEC["join_key"]["no_log"] is True


def test_join_key_is_never_returned(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    expect_register(fake, "KEY1")

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"}, diff=True)

    assert "KEY1" not in str(result)
    assert result["diff"] == {"before": {"registration_status": "NONE"}, "after": {"registration_status": "REGISTERED"}}


def test_registration_writes_send_no_if_match(fake_api, run_module):
    """The cluster's background secret rotation changes the ETag, and a registration write does not depend on it."""
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    expect_register(fake, "KEY1")

    run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert [e.headers.get("If-Match") for e in fake.log if e.method in ("PUT", "POST") and not e.background] == [None, None]


def test_none_with_a_token_mints_a_key_then_registers(fake_api, fake_nexus, run_module, registered):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=NONE)
    nexus_fake.expect("POST", API_TOKENS, body=MINT, response=ok({"otp_code": "MINTED1"}))
    expect_register(fake, "MINTED1")

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN})

    assert result["changed"] is True, result.get("msg")
    assert result["join_key_source"] == "minted"
    assert "MINTED1" in registered
    assert "MINTED1" not in str(result)
    assert nexus_fake.bearer_tokens() == [f"Bearer {NEXUS_TOKEN}"]


def test_mint_names_the_account_when_one_is_given(fake_api, fake_nexus, run_module):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=NONE)
    nexus_fake.expect("POST", API_TOKENS, body={**MINT, "account_id": ACCOUNT_ID}, response=ok({"otp_code": "MINTED1"}))
    expect_register(fake, "MINTED1")

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN, "nexus_account_id": ACCOUNT_ID})

    assert result["changed"] is True, result.get("msg")


NEXUS_GATEWAY_PAGE = GATEWAY_PAGE.encode()


def test_a_mint_answered_502_by_the_gateway_is_sent_again_and_registers(fake_api, fake_nexus, run_module, fake_clock):
    """Nothing is applied to the cluster before the key is stored, and a key minted for a lost answer is never used."""
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=NONE)
    nexus_fake.expect("POST", API_TOKENS, body=MINT, status=502, response=NEXUS_GATEWAY_PAGE)
    nexus_fake.expect("POST", API_TOKENS, body=MINT, response=ok({"otp_code": "MINTED1"}))
    expect_register(fake, "MINTED1")

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN})

    assert not result.get("failed"), result.get("msg")
    assert (result["changed"], result["join_key_source"]) == (True, "minted")
    assert cluster_writes(fake) == [f"PUT {REGISTRATION}", f"POST {ROTATE}"]


def test_a_mint_the_gateway_answers_502_until_retry_timeout_fails_without_a_change(fake_api, fake_nexus, run_module, fake_clock):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=NONE)
    for _attempt in range(3):
        nexus_fake.expect("POST", API_TOKENS, body=MINT, status=502, response=NEXUS_GATEWAY_PAGE)

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN, "retry_timeout": 3})

    assert (result["failed"], result.get("changed", False)) == (True, False)
    assert "maybe_applied" not in result
    assert "HTTP 502" in result["msg"]
    assert "the gateway in front of Nexus" in result["msg"]
    assert cluster_writes(fake) == []


def test_a_given_join_key_wins_over_minting(fake_api, fake_nexus, run_module):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=NONE)
    expect_register(fake, "KEY1")

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1", "nexus_api_token": NEXUS_TOKEN})

    assert result["join_key_source"] == "given", result.get("msg")
    assert nexus_fake.log == []


def test_none_without_a_key_or_a_token_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION})

    assert result["failed"] is True
    assert "set join_key, or nexus_api_token to mint one" in result["msg"]
    assert cluster_writes(fake) == []


@pytest.mark.parametrize("status", [NONE, PENDING], ids=["none", "pending"])
def test_connection_off_fails_before_minting_or_writing(fake_api, fake_nexus, run_module, status):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response={**status, "nexus_enabled": False})

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN})

    assert result["failed"] is True
    assert "qumulo.cluster.nexus_connection (nexus_enabled: true)" in result["msg"]
    assert cluster_writes(fake) == []
    assert nexus_fake.log == []


def test_check_mode_with_the_connection_off_predicts_the_registration_and_warns(fake_api, fake_nexus, run_module):
    """In a check-mode run, the nexus_connection task before this one has not turned the connection on."""
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response={**NONE, "nexus_enabled": False})

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN}, check_mode=True)

    assert (result.get("failed"), result["changed"]) == (None, True), result.get("msg")
    assert any("qumulo.cluster.nexus_connection (nexus_enabled: true)" in w for w in result["warnings"])
    assert cluster_writes(fake) == []
    assert nexus_fake.log == []


def test_pending_with_a_new_key_replaces_the_stored_one(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=PENDING)
    expect_register(fake, "KEY2")

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY2"})

    assert result["changed"] is True, result.get("msg")
    assert result["join_key_source"] == "given"


def test_pending_without_a_new_key_registers_with_the_stored_one(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=202, response=ROTATED)
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION})

    assert result["changed"] is True, result.get("msg")
    assert result["join_key_source"] == "stored"
    assert cluster_writes(fake) == [f"POST {ROTATE}"]


def test_pending_stored_key_refused_with_a_token_mints_a_new_one(fake_api, fake_nexus, run_module):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=500, response={"error_class": "nexus_error", "description": "join key already used"})
    nexus_fake.expect("POST", API_TOKENS, body=MINT, response=ok({"otp_code": "MINTED1"}))
    expect_register(fake, "MINTED1")

    result = run_module(nexus_registration, {**CONNECTION, "nexus_api_token": NEXUS_TOKEN})

    assert result["changed"] is True, result.get("msg")
    assert result["join_key_source"] == "minted"
    assert any("join key already used" in note for note in result["notes"])


def test_pending_stored_key_refused_without_a_token_fails_naming_the_way_out(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, status=500, response={"error_class": "nexus_error", "description": "join key already used"})

    result = run_module(nexus_registration, {**CONNECTION})

    assert (result["failed"], result["changed"]) == (True, False)
    assert "join key already used" in result["msg"]
    assert "single-use" in result["msg"]
    assert "set join_key to a new key, or nexus_api_token to mint one" in result["msg"]


def test_rotate_failure_after_storing_a_key_reports_the_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, status=500, response=DNS_ERROR)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "Name or service not known" in result["msg"]
    assert "single-use" in result["msg"]


def test_a_rotate_whose_answer_is_lost_is_settled_by_the_status(fake_api, run_module):
    """rotate is never resent, so a lost answer is settled by reading whether the cluster registered."""
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    fake.expect("PUT", REGISTRATION, body={"join_key": "KEY1"}, response=PENDING)
    fake.expect("POST", ROTATE, raises=LOST)
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert (result.get("failed"), result["changed"]) == (None, True), result.get("msg")
    assert result["registration"] == REGISTERED


def test_a_rotate_whose_answer_is_lost_and_that_did_not_register_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("POST", ROTATE, raises=LOST)
    fake.expect("GET", REGISTRATION, response=PENDING)

    result = run_module(nexus_registration, {**CONNECTION})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "may or may not have been applied" in result["msg"]
    assert "the registration status is PENDING_REGISTRATION" in result["msg"]
    assert "single-use" in result["msg"]


def test_rotate_that_leaves_the_cluster_unregistered_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)
    expect_register(fake, "KEY1", after=PENDING)

    result = run_module(nexus_registration, {**CONNECTION, "join_key": "KEY1"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "registration status is PENDING_REGISTRATION, not REGISTERED" in result["msg"]


@pytest.mark.parametrize(
    ("status", "args", "source"),
    [
        (NONE, {"join_key": "KEY1"}, "given"),
        (NONE, {"nexus_api_token": NEXUS_TOKEN}, "minted"),
        (PENDING, {}, "stored"),
    ],
    ids=["given", "minted", "stored"],
)
def test_check_mode_predicts_the_registration_and_sends_nothing(fake_api, fake_nexus, run_module, status, args, source):
    fake = fake_api()
    nexus_fake = fake_nexus()
    fake.expect("GET", REGISTRATION, response=status)

    result = run_module(nexus_registration, {**CONNECTION, **args}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["join_key_source"] == source
    assert result["registration"] == {**status, "registration_status": "REGISTERED"}
    assert result["diff"]["after"] == {"registration_status": "REGISTERED"}
    assert cluster_writes(fake) == []
    assert nexus_fake.log == []


def test_absent_on_a_registered_cluster_deletes_the_registration(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=REGISTERED)
    fake.expect("DELETE", REGISTRATION, response=NONE)
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["registration"] == NONE
    assert cluster_writes(fake) == [f"DELETE {REGISTRATION}"]


def test_absent_on_a_pending_cluster_clears_the_stored_key_with_patch(fake_api, run_module):
    """DELETE clears only the secret (QUIRK-016); the stored key is cleared by PATCHing it empty, never by a PUT (QUIRK-010)."""
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("GET", SUPPORT, response={"enabled": True, "nexus_registration_key": "KEY1"}, etag='"a"')
    fake.expect("PATCH", SUPPORT, body={"nexus_registration_key": ""}, if_match='"a"', status=202, response={"monitor_uri": "/v1/support/status"})
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert cluster_writes(fake) == [f"PATCH {SUPPORT}"]
    assert "KEY1" not in str(result)


def test_absent_on_a_registered_cluster_with_a_stored_key_clears_both(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=REGISTERED)
    fake.expect("DELETE", REGISTRATION, response=PENDING)
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("GET", SUPPORT, response={"enabled": True, "nexus_registration_key": "KEY1"}, etag='"a"')
    fake.expect("PATCH", SUPPORT, body={"nexus_registration_key": ""}, if_match='"a"', status=202, response={"monitor_uri": "/v1/support/status"})
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert cluster_writes(fake) == [f"DELETE {REGISTRATION}", f"PATCH {SUPPORT}"]


def test_absent_that_leaves_a_registration_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=REGISTERED)
    fake.expect("DELETE", REGISTRATION, response=REGISTERED)
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "registration status is REGISTERED, not NONE" in result["msg"]


def test_absent_retries_the_key_patch_when_a_secret_rotation_moves_the_etag(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=PENDING)
    fake.expect("GET", SUPPORT, response={"enabled": True, "nexus_registration_key": "KEY1"}, etag='"a"')
    fake.expect("PATCH", SUPPORT, body={"nexus_registration_key": ""}, if_match='"a"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", SUPPORT, response={"enabled": True, "nexus_registration_key": "KEY1"}, etag='"rotated"')
    fake.expect("PATCH", SUPPORT, body={"nexus_registration_key": ""}, if_match='"rotated"', status=202, response={"monitor_uri": "/v1/support/status"})
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")


def test_absent_on_a_status_it_does_not_know_fails_without_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response={**NONE, "registration_status": "ERROR_REGISTERING"})

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert (result["failed"], result["changed"]) == (True, False)
    assert "the registration status is ERROR_REGISTERING, which the module does not know how to remove" in result["msg"]
    assert cluster_writes(fake) == []


def test_absent_on_an_unregistered_cluster_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=NONE)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"})

    assert result["changed"] is False
    assert cluster_writes(fake) == []


def test_absent_in_check_mode_sends_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", REGISTRATION, response=REGISTERED)

    result = run_module(nexus_registration, {**CONNECTION, "state": "absent"}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["registration"] == NONE
    assert result["diff"] == {"before": {"registration_status": "REGISTERED"}, "after": {"registration_status": "NONE"}}
    assert cluster_writes(fake) == []
