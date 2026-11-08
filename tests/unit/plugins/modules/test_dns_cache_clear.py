# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.dns_cache_clear, driven through FakeQumulo. Request bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import dns_cache_clear
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
CLEAR = "/v1/dns/clear-dns-cache"
BOTH = {"skip_forward_cache": False, "skip_reverse_cache": False}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_both_caches_are_cleared_by_default_and_reported_changed(fake_api, run_module):
    """G-ACTIONS: nothing can be read back, so every run clears and reports a change."""
    fake = fake_api()
    fake.expect("POST", CLEAR, body=BOTH)

    result = run_module(dns_cache_clear, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["dns_cache_clear"] == {"caches": ["forward", "reverse"]}


def test_one_cache_skips_the_other(fake_api, run_module):
    fake = fake_api()
    fake.expect("POST", CLEAR, body={"skip_forward_cache": True, "skip_reverse_cache": False})

    result = run_module(dns_cache_clear, {**CONNECTION, "caches": ["reverse"]})

    assert result["changed"] is True, result.get("msg")
    assert result["dns_cache_clear"] == {"caches": ["reverse"]}


def test_check_mode_predicts_the_clear_and_sends_nothing(fake_api, run_module):
    fake = fake_api()

    result = run_module(dns_cache_clear, {**CONNECTION, "caches": ["reverse", "forward", "reverse"]}, check_mode=True, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["dns_cache_clear"] == {"caches": ["forward", "reverse"]}
    assert result["diff"] == {"before": {}, "after": {"caches": ["forward", "reverse"]}}
    assert writes(fake) == []


def test_a_clear_answered_503_is_sent_again(fake_api, run_module):
    """Clearing twice does no harm, so the route is replayable."""
    fake = fake_api()
    fake.expect("POST", CLEAR, body=BOTH, status=503, response={"error_class": "service_unavailable", "description": "busy"})
    fake.expect("POST", CLEAR, body=BOTH)

    result = run_module(dns_cache_clear, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_no_cache_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(dns_cache_clear, {**CONNECTION, "caches": []})

    assert result["failed"] is True
    assert "caches is empty, so there is nothing to clear" in result["msg"]
    assert fake.log == []
