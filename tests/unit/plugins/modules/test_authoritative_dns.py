# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.authoritative_dns, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import authoritative_dns
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/authoritative-dns/settings"
NAME = "edge01.corp.example.com."


def stored(enabled=True, fqdn=NAME, host_restrictions=(), record_ttl_seconds=0):
    return {"enabled": enabled, "fqdn": fqdn, "host_restrictions": list(host_restrictions), "record_ttl_seconds": record_ttl_seconds}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(host_restrictions=["10.0.0.0-10.255.255.255"]), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "enabled": True, "fqdn": NAME, "host_restrictions": ["10.0.0.0/8"], "record_ttl_seconds": 0})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["authoritative_dns"] == {"enabled": True, "fqdn": NAME, "host_restrictions": ["10.0.0.0-10.255.255.255"], "record_ttl_seconds": 0}
    assert writes(fake) == []


def test_a_name_without_its_final_dot_is_the_stored_name(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "fqdn": "edge01.corp.example.com"})

    assert result["changed"] is False, result.get("msg")


def test_change_patches_only_the_changed_keys_and_the_name_with_its_dot(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=False, fqdn=""), etag='"AQAAAA"')
    fake.expect("PATCH", SETTINGS, body={"enabled": True, "fqdn": NAME}, if_match=NO_HEADER, response=stored())
    fake.expect("GET", SETTINGS, response=stored(), etag='"AgAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "enabled": True, "fqdn": "edge01.corp.example.com"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["authoritative_dns"] == stored()


def test_authoritative_dns_never_sends_if_match(fake_api, run_module):
    """QUIRK-024: once the settings were written, the GET ETag never matches what PATCH checks, so a PATCH sending it fails 412."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"CdDVaw"')
    fake.expect("PATCH", SETTINGS, body={"record_ttl_seconds": 5}, if_match=NO_HEADER, response=stored(record_ttl_seconds=5))
    fake.expect("GET", SETTINGS, response=stored(record_ttl_seconds=5), etag='"other"')

    result = run_module(authoritative_dns, {**CONNECTION, "record_ttl_seconds": 5})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_host_restrictions_are_compared_as_sets_of_addresses(fake_api, run_module):
    """The cluster merges, sorts and rewrites the ranges: 10.0.0.0/30 and 10.0.0.5 come back as 10.0.0.0-3 and 10.0.0.5."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(host_restrictions=["10.0.0.0-3", "10.0.0.5", "2001:db8::-ff"]), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": ["2001:DB8::/120", "10.0.0.5", "10.0.0.2-10.0.0.3", "10.0.0.0/31"]})

    assert result["changed"] is False, result.get("diff") or result.get("msg")
    assert result["authoritative_dns"]["host_restrictions"] == ["10.0.0.0-10.0.0.3", "10.0.0.5", "2001:db8::-2001:db8::ff"]


def test_host_restrictions_are_sent_as_a_list(fake_api, run_module):
    """QUIRK-023: the API documentation types host_restrictions as a string, but the cluster takes and returns a list."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')
    fake.expect("PATCH", SETTINGS, body={"host_restrictions": ["10.0.0.0-10.0.0.3", "192.168.10.7"]}, if_match=NO_HEADER)
    fake.expect("GET", SETTINGS, response=stored(host_restrictions=["10.0.0.0-3", "192.168.10.7"]), etag='"AgAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": ["192.168.10.7", "10.0.0.0/30"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["authoritative_dns"]["host_restrictions"] == ["10.0.0.0-10.0.0.3", "192.168.10.7"]


def test_an_ipv4_mapped_address_is_its_ipv4_address(fake_api, run_module):
    """The cluster stores ::ffff:192.0.2.7 as 192.0.2.7, so the module compares it so too."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(host_restrictions=["192.0.2.7"]), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": ["::ffff:192.0.2.7"]})

    assert result["changed"] is False, result.get("diff") or result.get("msg")


def test_an_empty_list_lets_every_client_query(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(host_restrictions=["10.0.0.0-255"]), etag='"AQAAAA"')
    fake.expect("PATCH", SETTINGS, body={"host_restrictions": []}, if_match=NO_HEADER)
    fake.expect("GET", SETTINGS, response=stored(), etag='"AgAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": []})

    assert result["changed"] is True, result.get("msg")
    assert result["authoritative_dns"]["host_restrictions"] == []


def test_check_mode_reports_changed_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "record_ttl_seconds": 60}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["authoritative_dns"]["record_ttl_seconds"] == 60
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=False), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "enabled": True}, check_mode=True, diff=True)

    assert result["diff"] == {"before": stored(enabled=False), "after": stored()}


def test_a_read_answered_503_while_out_of_quorum_is_retried(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, status=503, response={"error_class": "configs_are_empty", "description": "configurations have not been loaded yet"})
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "enabled": True})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_an_empty_name_fails_before_the_write_on_a_cluster_that_has_one(fake_api, run_module):
    """G-DNS-FQDN-CLEAR: once a name is set, the cluster refuses to clear it."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "fqdn": ""})

    assert result["failed"] is True
    assert f'fqdn: "" asks to clear the name {NAME}' in result["msg"]
    assert "leave fqdn out, or declare that name" in result["msg"]
    assert "list dns.fqdn in qumulo_terraform_owned_areas" in result["msg"]
    assert "enabled" not in result["msg"]
    assert writes(fake) == []


def test_an_empty_name_on_a_cluster_never_named_changes_nothing(fake_api, run_module):
    """What a read of a cluster that has never had a name returns can be given back, as capture and network_info do."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=False, fqdn=""), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "enabled": False, "fqdn": "", "host_restrictions": [], "record_ttl_seconds": 0})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize(
    "fqdn", ["edge..example.com", ".", "edge example.com", "-edge.example.com", "edge-.example.com", "a" * 64 + ".example.com", "a." * 127 + "ab"]
)
def test_a_name_that_is_not_a_dns_name_fails_before_any_request(fake_api, run_module, fqdn):
    fake = fake_api()

    result = run_module(authoritative_dns, {**CONNECTION, "fqdn": fqdn})

    assert result["failed"] is True
    assert f"fqdn: '{fqdn}' is not a DNS name" in result["msg"]
    assert fake.log == []


def test_enabling_a_cluster_left_without_a_name_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(enabled=False, fqdn=""), etag='"AQAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "enabled": True})

    assert result["failed"] is True
    assert "enabled: true needs fqdn" in result["msg"]
    assert writes(fake) == []


def test_the_longest_record_ttl_is_taken(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')
    fake.expect("PATCH", SETTINGS, body={"record_ttl_seconds": 3600}, if_match=NO_HEADER)
    fake.expect("GET", SETTINGS, response=stored(record_ttl_seconds=3600), etag='"AgAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "record_ttl_seconds": 3600})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize("ttl", [-1, 3601])
def test_a_record_ttl_out_of_range_fails_before_any_request(fake_api, run_module, ttl):
    fake = fake_api()

    result = run_module(authoritative_dns, {**CONNECTION, "record_ttl_seconds": ttl})

    assert result["failed"] is True
    assert f"record_ttl_seconds: {ttl} is not between 0 and 3600" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("entry", ["::ffff:192.0.2.0/120", "::ffff:192.0.2.1-::ffff:192.0.2.9"])
def test_an_ipv4_mapped_range_fails_before_any_request(fake_api, run_module, entry):
    """The cluster's form of a mapped range is not recorded, so the module asks for IPv4 rather than guess."""
    fake = fake_api()

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": [entry]})

    assert result["failed"] is True
    assert f"host_restrictions: '{entry}' is an IPv4-mapped range; write it as IPv4" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("entry", ["corp.example.com", "10.0.0.256", "10.0.0.9-10.0.0.3", ""])
def test_a_restriction_that_is_not_an_address_range_fails_before_any_request(fake_api, run_module, entry):
    fake = fake_api()

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": ["10.0.0.1", entry]})

    assert result["failed"] is True
    assert f"host_restrictions: '{entry}' is not an address range" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("option", [{"enabled": True}, {"fqdn": NAME}])
def test_the_name_terraform_owns_fails_before_any_request(fake_api, run_module, option):
    fake = fake_api()

    result = run_module(authoritative_dns, {**CONNECTION, **option, "terraform_owned_areas": ["network.floating_ips", "dns.fqdn"]})

    assert result["failed"] is True
    assert f"{next(iter(option))} is in dns.fqdn, which Terraform owns" in result["msg"]
    assert "take_over_from_terraform: true" in result["msg"]
    assert fake.log == []


def test_the_clients_stay_the_collections_while_terraform_owns_the_name(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(), etag='"AQAAAA"')
    fake.expect("PATCH", SETTINGS, body={"host_restrictions": ["10.0.0.0-10.255.255.255"]}, if_match=NO_HEADER)
    fake.expect("GET", SETTINGS, response=stored(host_restrictions=["10.0.0.0-10.255.255.255"]), etag='"AgAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "host_restrictions": ["10.0.0.0/8"], "terraform_owned_areas": ["dns.fqdn"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_take_over_from_terraform_sets_the_name_terraform_owns(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored(fqdn="old.corp.example.com."), etag='"AQAAAA"')
    fake.expect("PATCH", SETTINGS, body={"fqdn": NAME}, if_match=NO_HEADER)
    fake.expect("GET", SETTINGS, response=stored(), etag='"AgAAAA"')

    result = run_module(authoritative_dns, {**CONNECTION, "fqdn": NAME, "terraform_owned_areas": ["dns.fqdn"], "take_over_from_terraform": True})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["authoritative_dns"]["fqdn"] == NAME
