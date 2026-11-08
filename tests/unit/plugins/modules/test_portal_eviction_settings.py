# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.portal_eviction_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import portal_eviction_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
EVICTION = "/v1/portal/spokes/eviction-settings"


def test_unchanged_sends_no_write(fake_api, run_module):
    fake = fake_api()
    # A stored float that differs from the request only in its last bits is the same setting.
    fake.expect("GET", EVICTION, response={"free_threshold": 0.15000000000000002}, etag='"4"')

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": 0.15})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["eviction_settings"]["free_threshold"] == pytest.approx(0.15)


def test_change_patches_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EVICTION, response={"free_threshold": 0.05}, etag='"4"')
    fake.expect("PATCH", EVICTION, body={"free_threshold": 0.15}, if_match='"4"')
    fake.expect("GET", EVICTION, response={"free_threshold": 0.15}, etag='"5"')

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": 0.15})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["eviction_settings"] == {"free_threshold": 0.15}


@pytest.mark.parametrize("free_threshold", [-0.01, 1.01])
def test_out_of_range_fails_before_request(fake_api, run_module, free_threshold):
    fake = fake_api()

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": free_threshold})

    assert result["failed"] is True
    assert "free_threshold must be within [0.0, 1.0]" in result["msg"]
    assert str(free_threshold) in result["msg"]
    assert fake.log == []


def test_check_mode_reports_changed_without_writing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EVICTION, response={"free_threshold": 0.05}, etag='"4"')

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": 0.15}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["eviction_settings"] == {"free_threshold": 0.15}
    assert [e.method for e in fake.log if not e.background] == ["GET"]


def test_diff_shows_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EVICTION, response={"free_threshold": 0.05}, etag='"4"')

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": 0.15}, check_mode=True, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["diff"] == {"before": {"free_threshold": 0.05}, "after": {"free_threshold": 0.15}}


@pytest.mark.parametrize("free_threshold", [0.0, 1.0])
def test_range_ends_are_accepted(fake_api, run_module, free_threshold):
    fake = fake_api()
    fake.expect("GET", EVICTION, response={"free_threshold": free_threshold}, etag='"4"')

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": free_threshold})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_a_difference_above_one_millionth_is_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EVICTION, response={"free_threshold": 0.15 + 2e-6}, etag='"4"')

    result = run_module(portal_eviction_settings, {**CONNECTION, "free_threshold": 0.15}, check_mode=True)

    assert result["changed"] is True
