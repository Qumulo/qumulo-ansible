# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.time_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import time_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
TIME = "/v2/time/settings"
NTP, HYPERVISOR = "API_TIME_SOURCE_NTP", "API_TIME_SOURCE_HYPERVISOR"
FACTORY = {"source": NTP, "use_ad_for_primary": False, "ntp_servers": ["0.qumulo.pool.ntp.org", "1.qumulo.pool.ntp.org"]}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')

    result = run_module(time_settings, {**CONNECTION, **FACTORY})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["time_settings"] == FACTORY
    assert writes(fake) == []


def test_change_patches_only_the_changed_keys_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')
    fake.expect("PATCH", TIME, body={"ntp_servers": ["ntp1.corp.example.com"]}, if_match='"Z-NlNA"')
    fake.expect("GET", TIME, response={**FACTORY, "ntp_servers": ["ntp1.corp.example.com"]}, etag='"2"')

    result = run_module(time_settings, {**CONNECTION, "ntp_servers": ["ntp1.corp.example.com"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["time_settings"]["ntp_servers"] == ["ntp1.corp.example.com"]


def test_reordered_servers_are_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')

    result = run_module(time_settings, {**CONNECTION, "ntp_servers": list(reversed(FACTORY["ntp_servers"]))}, check_mode=True)

    assert result["changed"] is True


def test_check_mode_and_diff_report_the_change_without_writing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')

    result = run_module(time_settings, {**CONNECTION, "use_ad_for_primary": True}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": FACTORY, "after": {**FACTORY, "use_ad_for_primary": True}}
    assert writes(fake) == []


@pytest.mark.parametrize("server", ["", " ntp1.corp.example.com", "ntp1.corp.example.com "])
def test_blank_or_padded_server_fails_before_any_request(fake_api, run_module, server):
    fake = fake_api()

    result = run_module(time_settings, {**CONNECTION, "ntp_servers": [server]})

    assert result["failed"] is True
    assert f"ntp_servers: {server!r} is empty or has leading or trailing spaces" in result["msg"]
    assert fake.log == []


def test_ntp_without_servers_fails_before_writing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')

    result = run_module(time_settings, {**CONNECTION, "ntp_servers": []})

    assert result["failed"] is True
    assert "API_TIME_SOURCE_NTP needs at least one entry in ntp_servers" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("live", [FACTORY, {**FACTORY, "ntp_servers": [], "use_ad_for_primary": True}], ids=["ntp_servers", "use_ad_for_primary"])
def test_hypervisor_with_the_ntp_servers_left_in_place_fails_naming_the_option(fake_api, run_module, live):
    """Unset options keep the cluster's values, and the server refuses HYPERVISOR with servers or with use_ad_for_primary."""
    fake = fake_api()
    fake.expect("GET", TIME, response=live, etag='"Z-NlNA"')

    result = run_module(time_settings, {**CONNECTION, "source": HYPERVISOR})

    assert result["failed"] is True
    assert "API_TIME_SOURCE_HYPERVISOR needs ntp_servers: [] and use_ad_for_primary: false" in result["msg"]
    assert writes(fake) == []


def test_hypervisor_with_no_servers_is_written(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')
    fake.expect("PATCH", TIME, body={"source": HYPERVISOR, "ntp_servers": []}, if_match='"Z-NlNA"')
    fake.expect("GET", TIME, response={"source": HYPERVISOR, "use_ad_for_primary": False, "ntp_servers": []}, etag='"3"')

    result = run_module(time_settings, {**CONNECTION, "source": HYPERVISOR, "ntp_servers": []})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_cluster_managed_by_qumulo_fails_clearly(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TIME, response=FACTORY, etag='"Z-NlNA"')
    fake.expect("PATCH", TIME, status=400, response={"error_class": "time_configuration_disabled_error", "description": "disabled"})

    result = run_module(time_settings, {**CONNECTION, "ntp_servers": ["ntp1.corp.example.com"]})

    assert result["failed"] is True
    assert "this cluster's time is managed by Qumulo" in result["msg"]
    assert "remove the time settings from this cluster's baseline" in result["msg"]
