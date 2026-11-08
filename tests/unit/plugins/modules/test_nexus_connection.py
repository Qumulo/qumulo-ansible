# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.nexus_connection, driven through FakeQumulo. Bodies are as Qumulo Core 7.10.3 serves them."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import nexus_connection
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
NEXUS = "/v1/nexus/connection"
SUPPORT = "/v1/support/settings"
STORED = {
    "nexus_enabled": False,
    "nexus_host": "api.nexus.qumulo.com",
    "nexus_port": 443,
    "nexus_interval": 30,
    "nexus_capability_remote_support": True,
    "nexus_capability_sso": False,
}
OPTIONS = {
    "nexus_enabled": True,
    "nexus_host": "nexus.example.com",
    "nexus_port": 8443,
    "nexus_interval": 60,
    "nexus_capability_remote_support": False,
    "nexus_capability_sso": True,
}


def stored(**changes):
    return {**STORED, **changes}


def support(enabled):
    return {"enabled": enabled, "period": 60, "nexus_enabled": False, "nexus_registration_key": None}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')

    result = run_module(nexus_connection, {**CONNECTION, "nexus_enabled": False, "nexus_host": "api.nexus.qumulo.com"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["nexus_connection"] == STORED
    assert writes(fake) == []


def test_change_patches_only_the_changed_keys_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')
    fake.expect("PATCH", NEXUS, body={"nexus_interval": 60}, if_match='"a"', response=stored(nexus_interval=60), etag='"b"')
    fake.expect("GET", NEXUS, response=stored(nexus_interval=60), etag='"b"')

    result = run_module(nexus_connection, {**CONNECTION, "nexus_interval": 60})

    assert result["changed"] is True, result.get("msg")
    assert result["nexus_connection"]["nexus_interval"] == 60


def test_enabling_checks_that_monitoring_is_on_first(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')
    fake.expect("GET", SUPPORT, response=support(True), etag='"a"')
    fake.expect("PATCH", NEXUS, body=OPTIONS, if_match='"a"', response=OPTIONS, etag='"b"')
    fake.expect("GET", NEXUS, response=OPTIONS, etag='"b"')

    result = run_module(nexus_connection, {**CONNECTION, **OPTIONS})

    assert result["changed"] is True, result.get("msg")
    assert result["nexus_connection"] == OPTIONS


def test_enabling_while_monitoring_is_off_fails_before_any_write(fake_api, run_module):
    """QUIRK-015: PATCH /v1/nexus/connection accepts nexus_enabled=true with monitoring off, though /v1/support/settings refuses it."""
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')
    fake.expect("GET", SUPPORT, response=support(False), etag='"a"')

    result = run_module(nexus_connection, {**CONNECTION, "nexus_enabled": True})

    assert result["failed"] is True
    assert "nexus_enabled=true needs cloud monitoring on" in result["msg"]
    assert "qumulo.cluster.monitoring_settings" in result["msg"]
    assert writes(fake) == []


def test_check_mode_with_monitoring_off_predicts_the_change_and_warns(fake_api, run_module):
    """In a check-mode run, the monitoring_settings task before this one has not turned monitoring on."""
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')
    fake.expect("GET", SUPPORT, response=support(False), etag='"a"')

    result = run_module(nexus_connection, {**CONNECTION, "nexus_enabled": True}, check_mode=True)

    assert (result.get("failed"), result["changed"]) == (None, True), result.get("msg")
    assert any("nexus_enabled=true needs cloud monitoring on" in w for w in result["warnings"])
    assert writes(fake) == []


def test_already_enabled_does_not_read_the_monitoring_settings(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=stored(nexus_enabled=True), etag='"a"')
    fake.expect("PATCH", NEXUS, body={"nexus_port": 8443}, response=stored(nexus_enabled=True, nexus_port=8443))
    fake.expect("GET", NEXUS, response=stored(nexus_enabled=True, nexus_port=8443))

    result = run_module(nexus_connection, {**CONNECTION, "nexus_enabled": True, "nexus_port": 8443})

    assert result["changed"] is True, result.get("msg")
    assert SUPPORT not in [e.path for e in fake.log]


def test_disabling_does_not_read_the_monitoring_settings(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=stored(nexus_enabled=True), etag='"a"')
    fake.expect("PATCH", NEXUS, body={"nexus_enabled": False}, response=STORED)
    fake.expect("GET", NEXUS, response=STORED)

    result = run_module(nexus_connection, {**CONNECTION, "nexus_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert SUPPORT not in [e.path for e in fake.log]


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')

    result = run_module(nexus_connection, {**CONNECTION, "nexus_host": "nexus.example.com"}, check_mode=True)

    assert result["changed"] is True
    assert result["nexus_connection"]["nexus_host"] == "nexus.example.com"
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')

    result = run_module(nexus_connection, {**CONNECTION, "nexus_capability_sso": True}, check_mode=True, diff=True)

    assert result["diff"] == {"before": STORED, "after": stored(nexus_capability_sso=True)}


def test_412_from_a_background_secret_rotation_rereads_and_patches_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NEXUS, response=STORED, etag='"a"')
    fake.expect("PATCH", NEXUS, body={"nexus_interval": 60}, if_match='"a"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", NEXUS, response=STORED, etag='"rotated"')
    fake.expect("PATCH", NEXUS, body={"nexus_interval": 60}, if_match='"rotated"', response=stored(nexus_interval=60))
    fake.expect("GET", NEXUS, response=stored(nexus_interval=60))

    result = run_module(nexus_connection, {**CONNECTION, "nexus_interval": 60})

    assert result["changed"] is True, result.get("msg")
