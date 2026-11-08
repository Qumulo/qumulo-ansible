# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.dns_lookup_overrides, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import dns_lookup_overrides
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
TABLE = "/v1/dns/lookup-override-config"
DC1 = {"ip_address": "10.0.0.10", "aliases": ["dc1.corp.example.com", "dc1"]}
DR01 = {"ip_address": "2001:db8::20", "aliases": ["dr01.corp.example.com"]}


def stored(*entries):
    return {"lookup_overrides": list(entries)}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DR01, DC1), etag='"AwAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1, {**DR01, "ip_address": "2001:DB8:0::0020"}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["dns_lookup_overrides"] == {"overrides": [DC1, DR01]}
    assert writes(fake) == []


def test_an_ipv4_mapped_address_is_its_ipv4_address(fake_api, run_module):
    """The cluster stores ::ffff:10.0.0.10 as 10.0.0.10, so the module compares it so too."""
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DC1), etag='"AwAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [{**DC1, "ip_address": "::ffff:10.0.0.10"}]})

    assert result["changed"] is False, result.get("diff") or result.get("msg")


def test_a_table_with_an_address_in_two_entries_reads_as_one_entry(fake_api, run_module):
    """The cluster keeps both entries of an address sent twice; the module merges them, names in table order."""
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DC1, DR01, {"ip_address": "10.0.0.10", "aliases": ["dc1", "dc1b"]}), etag='"AwAAAA"')

    result = run_module(
        dns_lookup_overrides, {**CONNECTION, "overrides": [{"ip_address": "10.0.0.10", "aliases": ["dc1.corp.example.com", "dc1", "dc1b"]}, DR01]}
    )

    assert result["changed"] is False, result.get("diff") or result.get("msg")
    assert writes(fake) == []


def test_the_order_of_an_entrys_names_is_a_change(fake_api, run_module):
    """The first name is the one a reverse lookup of the address returns."""
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DC1), etag='"AwAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [{**DC1, "aliases": ["dc1", "dc1.corp.example.com"]}]}, check_mode=True)

    assert result["changed"] is True


def test_change_puts_the_whole_table_sorted_and_canonical_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DC1), etag='"AwAAAA"')
    fake.expect("PUT", TABLE, body=stored(DC1, DR01), if_match='"AwAAAA"', response=stored(DC1, DR01))
    fake.expect("GET", TABLE, response=stored(DC1, DR01), etag='"BAAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [{**DR01, "ip_address": "2001:DB8::20"}, DC1]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["dns_lookup_overrides"] == {"overrides": [DC1, DR01]}


def test_an_address_the_list_leaves_out_is_removed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DC1, DR01), etag='"AwAAAA"')
    fake.expect("PUT", TABLE, body=stored(DC1), if_match='"AwAAAA"')
    fake.expect("GET", TABLE, response=stored(DC1), etag='"BAAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1]})

    assert result["changed"] is True, result.get("msg")
    assert result["dns_lookup_overrides"] == {"overrides": [DC1]}


def test_an_empty_list_clears_the_table(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DC1), etag='"AwAAAA"')
    fake.expect("PUT", TABLE, body=stored(), if_match='"AwAAAA"')
    fake.expect("GET", TABLE, response=stored(), etag='"BAAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": []})

    assert result["changed"] is True, result.get("msg")
    assert result["dns_lookup_overrides"] == {"overrides": []}


def test_a_dns_settings_write_in_between_is_read_again_and_the_table_written_once(fake_api, run_module):
    """The table shares its revision with /v1/dns/configs/system, so a write there makes this PUT's If-Match stale."""
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(), etag='"AwAAAA"')
    fake.expect("PUT", TABLE, body=stored(DC1), if_match='"AwAAAA"', status=412, response={"error_class": "db_revision_mismatch_error"})
    fake.expect("GET", TABLE, response=stored(), etag='"BAAAAA"')
    fake.expect("PUT", TABLE, body=stored(DC1), if_match='"BAAAAA"')
    fake.expect("GET", TABLE, response=stored(DC1), etag='"CAAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_check_mode_reports_changed_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(), etag='"AwAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1]}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["dns_lookup_overrides"] == {"overrides": [DC1]}
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", TABLE, response=stored(DR01), etag='"AwAAAA"')

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1]}, check_mode=True, diff=True)

    assert result["diff"] == {"before": {"overrides": [DR01]}, "after": {"overrides": [DC1]}}


@pytest.mark.parametrize("address", ["dc1.corp.example.com", "10.0.0.256", ""])
def test_an_address_that_does_not_parse_fails_before_any_request(fake_api, run_module, address):
    fake = fake_api()

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1, {"ip_address": address, "aliases": ["dc2"]}]})

    assert result["failed"] is True
    assert f"overrides[1].ip_address: '{address}' is not an IP address" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("alias", ["a", "1dc", "dc-", "dc_1", "dc 1", ""])
def test_a_name_the_cluster_would_refuse_fails_before_any_request(fake_api, run_module, alias):
    fake = fake_api()

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [{"ip_address": "10.0.0.10", "aliases": ["dc1", alias]}]})

    assert result["failed"] is True
    assert f"overrides[0].aliases: '{alias}' is not a name the cluster takes" in result["msg"]
    assert fake.log == []


def test_an_address_listed_twice_fails_before_any_request(fake_api, run_module):
    """The cluster would keep both entries; the module keys the table by address."""
    fake = fake_api()

    result = run_module(dns_lookup_overrides, {**CONNECTION, "overrides": [DC1, {"ip_address": "::ffff:10.0.0.10", "aliases": ["dc1b"]}]})

    assert result["failed"] is True
    assert "10.0.0.10 is listed twice" in result["msg"]
    assert fake.log == []
