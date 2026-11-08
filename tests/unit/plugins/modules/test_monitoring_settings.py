# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.monitoring_settings, driven through FakeQumulo. Bodies are as Qumulo Core 7.10.3 serves them."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import monitoring_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/support/settings"
STATUS = "/v1/support/status/"
# What PATCH answers: the status route without its trailing slash, which the server does not serve (QUIRK-003).
MONITOR_URI = {"monitor_uri": "/v1/support/status"}
STORED = {
    "enabled": False,
    "mq_host": "api.missionq.qumulo.com",
    "mq_port": 443,
    "mq_proxy_host": "",
    "mq_proxy_port": 0,
    "s3_proxy_host": "monitor.qumulo.com",
    "s3_proxy_port": 443,
    "s3_proxy_disable_https": False,
    "vpn_enabled": False,
    "vpn_host": "ep1.qumulo.com",
    "period": 60,
    "nexus_enabled": False,
    "nexus_host": "api.nexus.qumulo.com",
    "nexus_port": 443,
    "nexus_interval": 30,
    "nexus_registration_key": None,
    "nexus_secret_created_at": None,
}
OPTIONS = {
    "enabled": True,
    "mq_host": "mq.example.com",
    "mq_port": 8443,
    "mq_proxy_host": "proxy.example.com",
    "mq_proxy_port": 3128,
    "s3_proxy_host": "s3proxy.example.com",
    "s3_proxy_port": 8080,
    "s3_proxy_disable_https": True,
    "vpn_enabled": True,
    "vpn_host": "vpn.example.com",
    "period": 120,
}


def status(*states):
    return [{"node_id": n, "monitoring": state, "file_upload": "DISABLED", "vpn_connection": "DISABLED"} for n, state in enumerate(states, 1)]


def stored(**changes):
    return {**STORED, **changes}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def expect_write(fake, body, after, monitoring=("DISABLED",)):
    fake.expect("GET", SETTINGS, response=STORED, etag='"a"')
    fake.expect("PATCH", SETTINGS, body=body, if_match='"a"', status=202, response=MONITOR_URI)
    fake.expect("GET", SETTINGS, response=after, etag='"b"')
    fake.expect("GET", STATUS, response=status(*monitoring))


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"a"')

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": False, "period": 60, "mq_host": "api.missionq.qumulo.com"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert "monitoring_status" not in result


def test_change_patches_only_the_changed_keys_with_if_match(fake_api, run_module):
    fake = fake_api()
    expect_write(fake, {"period": 120}, stored(period=120))

    result = run_module(monitoring_settings, {**CONNECTION, "period": 120, "enabled": False})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["monitoring_settings"]["period"] == 120
    assert result["monitoring_status"] == status("DISABLED")


def test_every_option_maps_to_its_wire_key(fake_api, run_module):
    fake = fake_api()
    expect_write(fake, OPTIONS, stored(**OPTIONS), monitoring=("IN_PROGRESS",))

    result = run_module(monitoring_settings, {**CONNECTION, **OPTIONS})

    assert result["changed"] is True, result.get("msg")
    assert result["monitoring_settings"] == OPTIONS


def test_polls_the_support_status_route_not_the_monitor_uri(fake_api, run_module):
    """QUIRK-003: PATCH names /v1/support/status as its monitor URI, which answers 404; the route is /v1/support/status/."""
    fake = fake_api()
    expect_write(fake, {"period": 120}, stored(period=120), monitoring=("CONNECTED",))

    result = run_module(monitoring_settings, {**CONNECTION, "period": 120})

    assert result["monitoring_status"] == status("CONNECTED")
    assert [e.path for e in fake.log if e.path.startswith("/v1/support/status")] == [STATUS]


def test_monitoring_settings_never_puts(fake_api, run_module):
    """QUIRK-010: a PUT clears a pending join key, so every write is a PATCH of the changed keys, never nexus_*."""
    fake = fake_api()
    expect_write(fake, {"period": 120}, stored(period=120))

    result = run_module(monitoring_settings, {**CONNECTION, "period": 120})

    assert result["changed"] is True, result.get("msg")
    [patch] = writes(fake)
    assert patch.method == "PATCH"
    assert not [key for key in patch.body if key.startswith("nexus_")]


def test_nexus_fields_and_the_pending_join_key_are_never_returned(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(nexus_enabled=True, nexus_registration_key="PENDING-KEY"), etag='"a"')

    result = run_module(monitoring_settings, {**CONNECTION}, diff=True)

    assert result["changed"] is False
    assert sorted(result["monitoring_settings"]) == sorted(OPTIONS)
    assert "PENDING-KEY" not in str(result)
    assert sorted(result["diff"]["before"]) == sorted(OPTIONS)


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"a"')

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True, "wait": True}, check_mode=True)

    assert result["changed"] is True
    assert result["monitoring_settings"] == {key: value for key, value in stored(enabled=True).items() if key in OPTIONS}
    assert "monitoring_status" not in result  # nothing was written, so there is no status to wait for
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    expect_write(fake, {"vpn_host": "vpn.example.com"}, stored(vpn_host="vpn.example.com"))

    result = run_module(monitoring_settings, {**CONNECTION, "vpn_host": "vpn.example.com"}, diff=True)

    assert result["diff"]["before"]["vpn_host"] == "ep1.qumulo.com"
    assert result["diff"]["after"]["vpn_host"] == "vpn.example.com"


def test_disabling_monitoring_warns_that_it_also_disables_nexus(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=True, nexus_enabled=True), etag='"a"')
    fake.expect("PATCH", SETTINGS, body={"enabled": False}, if_match='"a"', status=202, response=MONITOR_URI)
    fake.expect("GET", SETTINGS, response=stored(enabled=False, nexus_enabled=False), etag='"b"')
    fake.expect("GET", STATUS, response=status("DISABLED"))

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": False})

    assert result["changed"] is True
    assert any("also disables the Nexus connection (nexus_enabled)" in w for w in result["warnings"])


@pytest.mark.parametrize(
    ("before", "args"),
    [
        ({"enabled": True, "nexus_enabled": False}, {"enabled": False}),
        ({"enabled": True, "nexus_enabled": True}, {"enabled": True}),
        ({"enabled": False, "nexus_enabled": True}, {"enabled": False}),
    ],
    ids=["nexus_already_off", "monitoring_stays_on", "monitoring_already_off"],
)
def test_no_nexus_warning_unless_monitoring_turns_off_with_nexus_on(fake_api, run_module, before, args):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(**before), etag='"a"')

    result = run_module(monitoring_settings, {**CONNECTION, **args}, check_mode=True)

    assert not [w for w in result["warnings"] if "nexus" in w.lower()]


def test_check_mode_also_warns_that_disabling_disables_nexus(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=True, nexus_enabled=True), etag='"a"')

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": False}, check_mode=True)

    assert result["changed"] is True
    assert any("nexus_enabled" in w for w in result["warnings"])


def test_412_rereads_and_patches_again_since_the_etag_drifts(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"a"')
    fake.expect("PATCH", SETTINGS, body={"period": 120}, if_match='"a"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", SETTINGS, response=STORED, etag='"rotated"')
    fake.expect("PATCH", SETTINGS, body={"period": 120}, if_match='"rotated"', status=202, response=MONITOR_URI)
    fake.expect("GET", SETTINGS, response=stored(period=120), etag='"b"')
    fake.expect("GET", STATUS, response=status("DISABLED"))

    result = run_module(monitoring_settings, {**CONNECTION, "period": 120})

    assert result["changed"] is True, result.get("msg")


def test_settings_that_do_not_converge_fail(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"a"')
    fake.expect("PATCH", SETTINGS, body={"period": 120}, if_match='"a"', status=202, response=MONITOR_URI)
    fake.expect("GET", SETTINGS, response=STORED, etag='"b"')

    result = run_module(monitoring_settings, {**CONNECTION, "period": 120})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "did not converge on period" in result["msg"]


def test_server_refusal_fails_with_its_message(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"a"')
    fake.expect(
        "PATCH",
        SETTINGS,
        body={"enabled": True},
        status=400,
        response={"error_class": "http_bad_request_error", "description": "Monitoring cannot be toggled on this platform."},
    )

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True})

    assert result["failed"] is True
    assert "Monitoring cannot be toggled on this platform." in result["msg"]


def test_wait_polls_until_every_node_connects(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_write(fake, {"enabled": True}, stored(enabled=True), monitoring=("IN_PROGRESS", "CONNECTED"))
    fake.expect("GET", STATUS, response=status("CONNECTED", "IN_PROGRESS"))
    fake.expect("GET", STATUS, response=status("CONNECTED", "CONNECTED"))

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True, "wait": True})

    assert result["changed"] is True, result.get("msg")
    assert result["monitoring_status"] == status("CONNECTED", "CONNECTED")
    assert fake_clock.sleeps == [5, 5]


def test_wait_keeps_polling_while_no_node_is_listed(fake_api, run_module, fake_clock):
    """An empty status list says nothing about the nodes, so it never ends the wait."""
    fake = fake_api()
    expect_write(fake, {"enabled": True}, stored(enabled=True), monitoring=())
    fake.expect("GET", STATUS, response=status("CONNECTED"))

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True, "wait": True})

    assert result["monitoring_status"] == status("CONNECTED"), result.get("msg")
    assert fake_clock.sleeps == [5]


def test_wait_for_disabled_monitoring_waits_for_disabled(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=True), etag='"a"')
    fake.expect("PATCH", SETTINGS, body={"enabled": False}, status=202, response=MONITOR_URI)
    fake.expect("GET", SETTINGS, response=STORED, etag='"b"')
    fake.expect("GET", STATUS, response=status("CONNECTED"))
    fake.expect("GET", STATUS, response=status("DISABLED"))

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": False, "wait": True})

    assert result["monitoring_status"] == status("DISABLED"), result.get("msg")


def test_wait_fails_at_once_when_a_node_reports_error(fake_api, run_module):
    fake = fake_api()
    expect_write(fake, {"enabled": True}, stored(enabled=True), monitoring=("CONNECTED", "ERROR"))

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True, "wait": True})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "node 2 reports monitoring ERROR" in result["msg"]
    assert "mq_host" in result["msg"]


def test_wait_times_out_naming_the_last_status(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_write(fake, {"enabled": True}, stored(enabled=True), monitoring=("IN_PROGRESS",))
    for _poll in range(3):  # at t = 5, 10 and 12
        fake.expect("GET", STATUS, response=status("IN_PROGRESS"))

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True, "wait": True, "wait_timeout": 12})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "monitoring is not CONNECTED on every node after 12s" in result["msg"]
    assert "node 1: IN_PROGRESS" in result["msg"]
    assert fake_clock.sleeps == [5, 5, 2]


def test_wait_without_a_change_reads_nothing_more(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=True), etag='"a"')

    result = run_module(monitoring_settings, {**CONNECTION, "enabled": True, "wait": True})

    assert result["changed"] is False
    assert [e.path for e in fake.log if not e.background] == [SETTINGS]
