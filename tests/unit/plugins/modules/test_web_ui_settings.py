# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.web_ui_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import web_ui_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/web-ui/settings"
NONE = {"inactivity_timeout": None, "login_banner": None}
BANNER = "Authorized use only."
HALF_HOUR = {"nanoseconds": "1800000000000"}


def writes(fake):
    return [e for e in fake.log if e.method == "PUT"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(web_ui_settings, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_settings_read_in_minutes_and_unchanged_send_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"inactivity_timeout": HALF_HOUR, "login_banner": BANNER}, etag='"w1"')

    result = run(run_module, inactivity_timeout=30, login_banner=BANNER)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["web_ui_settings"] == {"inactivity_timeout": 30, "login_banner": BANNER}
    assert writes(fake) == []


def test_none_reads_as_no_banner_and_no_timeout(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=NONE, etag='"w1"')

    result = run(run_module, inactivity_timeout=0, login_banner="")

    assert result["changed"] is False
    assert result["web_ui_settings"] == {"inactivity_timeout": 0, "login_banner": ""}


def test_a_change_puts_the_whole_document_with_if_match_keeping_the_other_setting(fake_api, run_module):
    """The PUT replaces the document, so the banner the task leaves unset is sent as the cluster holds it."""
    after = {"inactivity_timeout": HALF_HOUR, "login_banner": BANNER}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"inactivity_timeout": None, "login_banner": BANNER}, etag='"w1"')
    fake.expect("PUT", SETTINGS, body=after, if_match='"w1"', response=after)
    fake.expect("GET", SETTINGS, response=after, etag='"w2"')

    result = run(run_module, inactivity_timeout=30)

    assert result["changed"] is True
    assert result["web_ui_settings"] == {"inactivity_timeout": 30, "login_banner": BANNER}


def test_an_empty_banner_and_a_zero_timeout_are_sent_as_null(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"inactivity_timeout": HALF_HOUR, "login_banner": BANNER}, etag='"w1"')
    fake.expect("PUT", SETTINGS, body=NONE, if_match='"w1"', response=NONE)
    fake.expect("GET", SETTINGS, response=NONE, etag='"w2"')

    result = run(run_module, inactivity_timeout=0, login_banner="")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_timeout_set_in_seconds_elsewhere_reads_back_as_fractional_minutes(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"inactivity_timeout": {"nanoseconds": "150000000000"}, "login_banner": None}, etag='"w1"')

    result = run(run_module, check_mode=True, diff=True, inactivity_timeout=3)

    assert result["changed"] is True
    assert result["diff"]["before"]["inactivity_timeout"] == 2.5


@pytest.mark.parametrize("minutes", [1, 601, -5])
def test_a_timeout_outside_the_clusters_range_fails_before_any_request(fake_api, run_module, minutes):
    fake = fake_api()

    result = run(run_module, inactivity_timeout=minutes)

    assert result["failed"] is True
    assert "inactivity_timeout" in result["msg"] and "2 to 600" in result["msg"]
    assert fake.log == []


def test_a_banner_over_32_kib_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, login_banner="x" * (32 * 1024 + 1))

    assert result["failed"] is True
    assert "32 KiB" in result["msg"]
    assert fake.log == []


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=NONE, etag='"w1"')

    result = run(run_module, check_mode=True, diff=True, login_banner=BANNER)

    assert result["changed"] is True
    assert result["diff"] == {"before": {"inactivity_timeout": 0, "login_banner": ""}, "after": {"inactivity_timeout": 0, "login_banner": BANNER}}
    assert writes(fake) == []
