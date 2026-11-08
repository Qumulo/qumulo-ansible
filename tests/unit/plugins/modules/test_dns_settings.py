# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.dns_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import dns_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
DNS = "/v1/dns/configs/system"


def stored(servers=(), domains=()):
    return {"id": 1, "dns_servers": list(servers), "dns_search_domains": list(domains), "lookup_overrides": []}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["10.0.0.2", "10.0.0.3"], ["corp.example.com"]), etag='"AQAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["10.0.0.2", "10.0.0.3"], "search_domains": ["corp.example.com"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["dns_settings"] == {"servers": ["10.0.0.2", "10.0.0.3"], "search_domains": ["corp.example.com"]}
    assert writes(fake) == []


def test_server_text_the_cluster_kept_verbatim_matches_its_canonical_form(fake_api, run_module):
    """QUIRK-014: the cluster stores an address as sent: 2001:DB8::0001 comes back so, and is the same server as 2001:db8::1."""
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["2001:DB8::0001"]), etag='"AQAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["2001:db8::1"]})

    assert result["changed"] is False
    assert result["dns_settings"]["servers"] == ["2001:db8::1"]


def test_server_given_in_another_form_is_compared_canonical(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["2001:db8::1"]), etag='"AQAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["2001:DB8::0001"]})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_server_given_in_another_form_is_sent_canonical(fake_api, run_module):
    """QUIRK-014: the cluster keeps the text it is sent, so the module sends canonical text."""
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["2001:db8::1"]), etag='"AQAAAA"')
    fake.expect("PATCH", DNS, body={"dns_servers": ["2001:db8::2"]}, if_match='"AQAAAA"')
    fake.expect("GET", DNS, response=stored(["2001:db8::2"]), etag='"AgAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["2001:DB8::0002"]})

    assert result["changed"] is True, result.get("msg")
    assert result["dns_settings"]["servers"] == ["2001:db8::2"]


def test_change_patches_only_the_changed_list_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["10.0.0.2"], ["corp.example.com"]), etag='"AQAAAA"')
    fake.expect("PATCH", DNS, body={"dns_servers": ["10.0.0.3", "10.0.0.2"]}, if_match='"AQAAAA"')
    fake.expect("GET", DNS, response=stored(["10.0.0.3", "10.0.0.2"], ["corp.example.com"]), etag='"AgAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["10.0.0.3", "10.0.0.2"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["dns_settings"] == {"servers": ["10.0.0.3", "10.0.0.2"], "search_domains": ["corp.example.com"]}


def test_reordered_servers_are_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["10.0.0.2", "10.0.0.3"]), etag='"AQAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["10.0.0.3", "10.0.0.2"]}, check_mode=True)

    assert result["changed"] is True


def test_empty_list_clears_and_unset_option_keeps(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["10.0.0.2"], ["corp.example.com"]), etag='"AQAAAA"')
    fake.expect("PATCH", DNS, body={"dns_search_domains": []}, if_match='"AQAAAA"')
    fake.expect("GET", DNS, response=stored(["10.0.0.2"]), etag='"AgAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "search_domains": []})

    assert result["changed"] is True
    assert result["dns_settings"] == {"servers": ["10.0.0.2"], "search_domains": []}


def test_check_mode_reports_changed_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(), etag='"AQAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["10.0.0.2"]}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["dns_settings"]["servers"] == ["10.0.0.2"]
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DNS, response=stored(["10.0.0.2"]), etag='"AQAAAA"')

    result = run_module(dns_settings, {**CONNECTION, "servers": ["10.0.0.9"]}, check_mode=True, diff=True)

    assert result["diff"] == {
        "before": {"servers": ["10.0.0.2"], "search_domains": []},
        "after": {"servers": ["10.0.0.9"], "search_domains": []},
    }


@pytest.mark.parametrize("server", ["dns.example.com", "10.0.0.256", ""])
def test_server_that_is_not_an_ip_address_fails_before_any_request(fake_api, run_module, server):
    fake = fake_api()

    result = run_module(dns_settings, {**CONNECTION, "servers": ["10.0.0.2", server]})

    assert result["failed"] is True
    assert f"servers: '{server}' is not an IP address" in result["msg"]
    assert fake.log == []
