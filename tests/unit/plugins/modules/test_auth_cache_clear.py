# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.auth_cache_clear, driven through FakeQumulo."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import auth_cache_clear
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
CLEAR = "/v1/auth/clear-cache"


def test_clears_the_cache_and_reports_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("POST", CLEAR, body=None, response=b"")

    result = run_module(auth_cache_clear, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["plan"] == {"cache": "authentication"}


def test_every_run_clears_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("POST", CLEAR, response=b"")
    fake.expect("POST", CLEAR, response=b"")

    first = run_module(auth_cache_clear, CONNECTION)
    second = run_module(auth_cache_clear, CONNECTION)

    assert first["changed"] is True and second["changed"] is True


def test_check_mode_predicts_the_clear_and_sends_nothing(fake_api, run_module):
    fake = fake_api()

    result = run_module(auth_cache_clear, CONNECTION, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"] == {"before": {}, "after": {"cache": "authentication"}}
    assert [e for e in fake.log if not e.background] == []


def test_a_clear_whose_answer_is_lost_is_sent_again(fake_api, run_module):
    """Clearing a cache twice does no more than clearing it once, so a lost answer is retried, never reported as maybe applied."""
    fake = fake_api()
    fake.expect("POST", CLEAR, raises=TimeoutError("timed out"))
    fake.expect("POST", CLEAR, response=b"")

    result = run_module(auth_cache_clear, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert "maybe_applied" not in result


def test_a_refusal_fails_the_task(fake_api, run_module):
    fake = fake_api()
    fake.expect("POST", CLEAR, status=403, response={"error_class": "http_forbidden_error", "description": "Forbidden"})

    result = run_module(auth_cache_clear, CONNECTION)

    assert result["failed"] is True
    assert "403" in result["msg"]
