# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.cluster_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import cluster_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/cluster/settings"
AD_STATUS = "/v1/ad/status"


def ad_status(status, domain=""):
    return {"status": status, "domain": domain, "ou": "", "domain_netbios": "", "dcs": []}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_name_is_unchanged_and_reads_nothing_else(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"cluster_name": "edge01"}, etag='"3NEIng"')

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": "edge01"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["cluster_settings"] == {"cluster_name": "edge01"}


def test_rename_checks_the_domain_then_puts_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"cluster_name": "qedge"}, etag='"3NEIng"')
    fake.expect("GET", AD_STATUS, response=ad_status("NOT_IN_DOMAIN"))
    fake.expect("PUT", SETTINGS, body={"cluster_name": "edge01"}, if_match='"3NEIng"')
    fake.expect("GET", SETTINGS, response={"cluster_name": "edge01"}, etag='"4"')

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": "edge01"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["cluster_settings"] == {"cluster_name": "edge01"}


def test_rename_while_joined_fails_naming_the_domain_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"cluster_name": "qedge"}, etag='"3NEIng"')
    fake.expect("GET", AD_STATUS, response=ad_status("JOINED_TO_DOMAIN", "corp.example.com"))

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": "edge01"})

    assert result["failed"] is True
    assert "cannot rename 'qedge' to 'edge01' while it is joined to corp.example.com" in result["msg"]
    assert "ad_domain state=absent" in result["msg"]
    assert writes(fake) == []


def test_check_mode_predicts_the_refusal_while_joined(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"cluster_name": "qedge"}, etag='"3NEIng"')
    fake.expect("GET", AD_STATUS, response=ad_status("JOINED_TO_DOMAIN", "corp.example.com"))

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": "edge01"}, check_mode=True)

    assert result["failed"] is True


def test_check_mode_and_diff_report_a_rename_without_writing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"cluster_name": "qedge"}, etag='"3NEIng"')
    fake.expect("GET", AD_STATUS, response=ad_status("NOT_IN_DOMAIN"))

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": "edge01"}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": {"cluster_name": "qedge"}, "after": {"cluster_name": "edge01"}}
    assert writes(fake) == []


@pytest.mark.parametrize("name", ["ab", "a" * 15, "edge-01", "0edge9"])
def test_names_inside_the_rules_are_accepted(fake_api, run_module, name):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={"cluster_name": name}, etag='"3NEIng"')

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": name})

    assert result["changed"] is False, result.get("msg")


@pytest.mark.parametrize("name", ["a", "a" * 16, "-edge", "edge-", "edge_01", "edge.01", ""])
def test_names_outside_the_rules_fail_before_any_request(fake_api, run_module, name):
    fake = fake_api()

    result = run_module(cluster_settings, {**CONNECTION, "cluster_name": name})

    assert result["failed"] is True
    assert f"cluster_name '{name}' must be 2 to 15 letters, digits or hyphens" in result["msg"]
    assert fake.log == []
