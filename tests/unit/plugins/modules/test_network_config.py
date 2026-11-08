# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.network_config, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import copy
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import network_config
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "10.20.0.5", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
NETWORK, VALIDATE, TENANTS, STATUS = "/v3/network", "/v3/network/validate", "/v1/multitenancy/tenants/", "/v3/network/status"
SWITCHES = ("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "replication_enabled", "nfs_enabled", "smb_enabled")


def tenant(tenant_id, name, networks=(), **switches):
    return {"id": tenant_id, "name": name, **dict.fromkeys(SWITCHES, True), **switches, "networks": list(networks), "identity_config_id": 1}


DEFAULT = tenant(1, "Default", networks=[1])


@pytest.fixture
def fake_api(fake_api):
    """A cluster with the one tenant a cluster forms with, which a run that will write reads for the continuity rule."""

    def make(*tenants, **kwargs):
        fake = fake_api(**kwargs)
        fake.serve(TENANTS, {"entries": list(tenants) or [DEFAULT]}, optional=True)
        return fake

    return make


BOND = {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]}
VLANS = [
    {"vlan_id": 20, "mtu": None, "network_id": 2, "secondary_network_id": None},
    {"vlan_id": 10, "mtu": 1500, "network_id": 3, "secondary_network_id": None},
]


def static(network_id=1, name="Default", ip_ranges=("10.20.0.5-6",), floating=("10.20.0.50-51",), netmask="255.255.255.0", tenant=1, gateway=None):
    """A STATIC network in the wire shape of /v3/network: network 1 carries the cluster's one IPv4 gateway, others none."""
    if gateway is None:
        gateway = "10.20.0.1" if network_id == 1 else ""
    addresses = {"default_gateway": gateway, "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), "netmask": netmask}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "STATIC", "static_addresses": addresses}}


def host(network_id=1, name="Default", floating=(), netmask=None, tenant=1):
    addresses = {"floating_ip_ranges": list(floating), "netmask": netmask}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "HOST", "host_addresses": addresses}}


def document(*networks, interfaces=None):
    return {"managed_interfaces": interfaces, "frontend_networks": list(networks)}


def managed(**changes):
    return {"frontend_bond_config": BOND, "frontend_vlans": VLANS, "backend_bond_config": None, **changes}


STATIC_WANT = {
    "id": 1,
    "name": "Default",
    "type": "STATIC",
    "netmask": "24",
    "default_gateway": "10.20.0.1",
    "ip_ranges": ["10.20.0.5", "10.20.0.6"],
    "floating_ip_ranges": ["10.20.0.50-10.20.0.51"],
}


def writes(fake):
    return [e for e in fake.log if e.method == "PUT"]


def test_identical_network_sends_no_put(fake_api, run_module):
    """QUIRK-002: an identical PUT re-runs address allocation, so ranges and netmasks written another way must compare equal."""
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed()), etag='"f7tcsg"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [STATIC_WANT], "managed_interfaces": managed()})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert result["network_config"]["frontend_networks"][0]["ip_ranges"] == ["10.20.0.5-10.20.0.6"]
    assert result["network_config"]["frontend_networks"][0]["netmask"] == "255.255.255.0"


def test_vlans_compare_by_vlan_id_not_by_order(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed()), etag='"f7tcsg"')

    result = run_module(network_config, {**CONNECTION, "managed_interfaces": managed(frontend_vlans=list(reversed(VLANS)))})

    assert result["changed"] is False, result.get("msg")
    assert [vlan["vlan_id"] for vlan in result["network_config"]["managed_interfaces"]["frontend_vlans"]] == [10, 20]


def test_bond_networks_compare_as_a_set(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed(frontend_bond_config={**BOND, "networks": [1, 2]})), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "managed_interfaces": managed(frontend_bond_config={**BOND, "networks": [2, 1]})})

    assert result["changed"] is False, result.get("msg")


def test_interfaces_given_in_part_keep_the_keys_they_leave_out(fake_api, run_module):
    """F4: a key the inventory leaves out, or sets to null where the cluster leaves it out, is no difference, so no PUT."""
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed()), etag='"1"')

    given = {"frontend_bond_config": {"mtu": 9000}, "frontend_vlans": [{"vlan_id": 10, "network_id": 3}, {"vlan_id": 20, "network_id": 2}]}
    result = run_module(network_config, {**CONNECTION, "managed_interfaces": given})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_interfaces_given_in_part_put_the_whole_merged_document(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed()), etag='"1"')
    merged = managed(frontend_vlans=[{**VLANS[1], "mtu": 9000}])
    fake.expect("PUT", NETWORK, body=document(static(), interfaces=merged), if_match='"1"', status=202)
    fake.expect("GET", NETWORK, response=document(static(), interfaces=merged), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "managed_interfaces": {"frontend_vlans": [{"vlan_id": 10, "mtu": 9000}]}})

    assert result["changed"] is True, result.get("msg")
    assert result["network_config"]["managed_interfaces"]["frontend_vlans"] == [{"vlan_id": 10, "mtu": 9000, "network_id": 3}]


@pytest.mark.parametrize("netmask", ["32", "/32", "255.255.255.255"])
def test_host_netmask_compares_with_its_dotted_form(fake_api, run_module, netmask):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(netmask="255.255.255.255")), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "Default", "type": "HOST", "netmask": netmask}]})

    assert result["changed"] is False, result.get("msg")


def test_cidr_block_in_a_static_network_leaves_out_its_network_and_broadcast_addresses(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(ip_ranges=["10.20.0.1-2"], floating=[])), etag='"1"')

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.0/30"], "floating_ip_ranges": []}
    result = run_module(network_config, {**CONNECTION, "api_host": "10.20.0.1", "frontend_networks": [want]})

    assert result["changed"] is False, result.get("msg")


def _without_gateway(network):
    network = copy.deepcopy(network)
    network["addresses"]["static_addresses"]["default_gateway"] = ""
    return network


def test_a_static_network_without_a_gateway_goes_on_the_wire_with_an_empty_one(fake_api, run_module):
    # The cluster's decoder refuses a null default_gateway (qumulo-findings item 36), and a chassis
    # cluster takes one IPv4 gateway in all (item 35), so a second STATIC network carries none.
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static()), etag='"1"')
    second = _without_gateway(static(2, "Second", ip_ranges=("10.30.0.5-6",), floating=("10.30.0.50-51",)))
    sent = {key: value for key, value in second.items() if key != "tenant_id"}  # a new network with one tenant names none
    fake.expect("PUT", NETWORK, body=document(static(), sent), if_match='"1"', status=202)
    fake.expect("GET", NETWORK, response=document(static(), second), etag='"2"')

    added = {"id": 2, "name": "Second", "type": "STATIC", "netmask": "24", "ip_ranges": ["10.30.0.5-6"], "floating_ip_ranges": ["10.30.0.50-51"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [STATIC_WANT, added]})

    assert result["changed"] is True, result.get("msg")
    assert result["network_config"]["frontend_networks"][1]["default_gateway"] is None


def test_an_empty_gateway_on_the_cluster_matches_a_network_that_leaves_it_unset(fake_api, run_module):
    fake = fake_api()
    second = _without_gateway(static(2, "Second", ip_ranges=("10.30.0.5-6",), floating=("10.30.0.50-51",)))
    fake.expect("GET", NETWORK, response=document(static(), second), etag='"1"')

    added = {"id": 2, "name": "Second", "type": "STATIC", "netmask": "24", "ip_ranges": ["10.30.0.5-6"], "floating_ip_ranges": ["10.30.0.50-51"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [STATIC_WANT, added]})

    assert result["changed"] is False, result.get("msg")


def test_a_floating_write_sends_a_gateway_less_network_back_with_an_empty_gateway(fake_api, run_module):
    # A write that leaves the networks to the cluster re-sends each one as read, a STATIC network without a gateway
    # included, which went out as null before.
    fake = fake_api()
    second = _without_gateway(static(2, "Second", ip_ranges=("10.30.0.5-6",), floating=("10.30.0.50-51",)))
    fake.expect("GET", NETWORK, response=document(static(), second), etag='"1"')
    widened = _without_gateway(static(2, "Second", ip_ranges=("10.30.0.5-6",), floating=("10.30.0.50-53",)))
    fake.expect("PUT", NETWORK, body=document(static(), widened), if_match='"1"', status=202)
    fake.expect("GET", NETWORK, response=document(static(), widened), etag='"2"')

    result = run_module(
        network_config, {**CONNECTION, "owned_parts": ["floating_ips"], "frontend_networks": [{"id": 2, "floating_ip_ranges": ["10.30.0.50-53"]}]}
    )

    assert result["changed"] is True, result.get("msg")


def test_rename_puts_the_whole_document_with_if_match_and_reads_it_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(floating=["172.30.0.200-201"], netmask="255.255.255.0")), etag='"f7tcsg"')
    renamed = document(host(name="it-network", floating=["172.30.0.200-201"], netmask="255.255.255.0"))
    fake.expect("PUT", NETWORK, body=renamed, if_match='"f7tcsg"', status=202, response={"monitor_uri": "/v3/network/status/"})
    fake.expect("GET", NETWORK, response=renamed, etag='"l0trrw"')

    want = {"id": 1, "name": "it-network", "type": "HOST", "netmask": "24", "floating_ip_ranges": ["172.30.0.200", "172.30.0.201"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [want]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["network_config"]["frontend_networks"][0]["name"] == "it-network"


def test_network_missing_from_the_list_is_removed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(), host(2, "Second")), etag='"1"')
    fake.expect("PUT", NETWORK, body=document(host()), if_match='"1"', status=202)
    fake.expect("GET", NETWORK, response=document(host()), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "Default", "type": "HOST"}]})

    assert result["changed"] is True, result.get("msg")


def test_unset_tenant_keeps_the_network_s_tenant(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(tenant=3)), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "Default", "type": "HOST"}]})

    assert result["changed"] is False, result.get("msg")
    assert result["network_config"]["frontend_networks"][0]["tenant_id"] == 3


def test_given_tenant_that_differs_is_written(fake_api, run_module):
    fake = fake_api(DEFAULT, tenant(2, "eng"))
    fake.expect("GET", NETWORK, response=document(host(tenant=1)), etag='"1"')
    fake.expect("PUT", NETWORK, body=document(host(tenant=2)), status=202)
    fake.expect("GET", NETWORK, response=document(host(tenant=2)), etag='"2"')

    entry = {"id": 1, "name": "Default", "type": "HOST", "tenant_id": 2}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [entry], "management_network": [1]})

    assert result["changed"] is True, result.get("msg")
    assert result["network_config"]["frontend_networks"][0]["tenant_id"] == 2


def test_new_network_needs_a_name_and_a_type(fake_api, run_module):
    fake = fake_api()

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "Default"}]})

    assert result["failed"] is True
    assert "frontend_networks[0] needs name and type" in result["msg"]
    assert fake.log == []


def test_floating_ips_are_kept_when_terraform_owns_them(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(floating=["10.20.0.50-51"])), etag='"1"')
    renamed = document(host(name="edge", floating=["10.20.0.50-51"]))
    fake.expect("PUT", NETWORK, body=renamed, if_match='"1"', status=202)
    fake.expect("GET", NETWORK, response=renamed, etag='"2"')

    result = run_module(network_config, {**CONNECTION, "managed_by": "terraform_provider", "frontend_networks": [{"id": 1, "name": "edge", "type": "HOST"}]})

    assert result["changed"] is True, result.get("msg")
    assert result["network_config"]["frontend_networks"][0]["floating_ip_ranges"] == ["10.20.0.50-10.20.0.51"]


def test_floating_ips_given_while_terraform_owns_them_fail_before_any_request(fake_api, run_module):
    fake = fake_api()
    want = {"id": 1, "name": "edge", "type": "HOST", "floating_ip_ranges": ["10.20.0.50"]}

    result = run_module(network_config, {**CONNECTION, "managed_by": "terraform_provider", "frontend_networks": [want]})

    assert result["failed"] is True
    assert "frontend_networks[0].floating_ip_ranges is in floating_ips, which this task does not own" in result["msg"]
    assert fake.log == []


def test_owning_floating_ips_from_terraform_needs_take_over_from_terraform(fake_api, run_module):
    fake = fake_api()
    args = {"managed_by": "terraform_provider", "owned_parts": ["networks", "floating_ips"], "frontend_networks": [{"id": 1, "name": "edge", "type": "HOST"}]}

    result = run_module(network_config, {**CONNECTION, **args})

    assert result["failed"] is True
    assert "take_over_from_terraform" in result["msg"]
    assert fake.log == []


def test_taken_over_floating_ips_are_written(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(floating=["10.20.0.50-51"])), etag='"1"')
    fake.expect("PUT", NETWORK, body=document(host(floating=["10.20.0.60"])), status=202)
    fake.expect("GET", NETWORK, response=document(host(floating=["10.20.0.60"])), etag='"2"')
    want = {"id": 1, "name": "Default", "type": "HOST", "floating_ip_ranges": ["10.20.0.60"]}
    args = {"managed_by": "terraform_provider", "owned_parts": ["networks", "floating_ips"], "take_over_from_terraform": True, "frontend_networks": [want]}

    result = run_module(network_config, {**CONNECTION, **args})

    assert result["changed"] is True, result.get("msg")


def test_only_floating_ips_owned_sets_them_on_the_named_network(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(name="kept", floating=[]), host(2, "other")), etag='"1"')
    after = document(host(name="kept", floating=["10.20.0.60"]), host(2, "other"))
    fake.expect("PUT", NETWORK, body=after, status=202)
    fake.expect("GET", NETWORK, response=after, etag='"2"')

    args = {"owned_parts": ["floating_ips"], "frontend_networks": [{"id": 1, "floating_ip_ranges": ["10.20.0.60"]}]}
    result = run_module(network_config, {**CONNECTION, **args})

    assert result["changed"] is True, result.get("msg")


def test_interfaces_given_without_owning_them_fail_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(network_config, {**CONNECTION, "owned_parts": ["networks"], "managed_interfaces": managed()})

    assert result["failed"] is True
    assert "managed_interfaces is in interfaces, which this task does not own" in result["msg"]
    assert fake.log == []


def test_check_mode_validates_the_new_document_without_writing_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", VALIDATE, body=document(host(name="it-network")), if_match=NO_HEADER)

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "it-network", "type": "HOST"}]}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"]["frontend_networks"][0]["name"] == "Default"
    assert result["diff"]["after"]["frontend_networks"][0]["name"] == "it-network"
    assert [e.path for e in writes(fake)] == [VALIDATE]


def test_check_mode_reports_what_the_cluster_would_refuse(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", VALIDATE, status=400, response={"error_class": "network_static_not_allowed_error", "description": "STATIC is not allowed"})

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.5"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [want]}, check_mode=True)

    assert result["failed"] is True
    assert "the cluster refuses this network document" in result["msg"]
    assert "network_static_not_allowed_error" in result["msg"]


def test_check_mode_without_validate_sends_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "validate": False, "frontend_networks": [{"id": 1, "name": "x", "type": "HOST"}]}, check_mode=True)

    assert result["changed"] is True
    assert writes(fake) == []


def test_dropping_the_api_host_address_needs_allow_management_disruption(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static()), etag='"1"')

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.7"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [want]})

    assert result["failed"] is True
    assert "would drop 10.20.0.5, the address this task connects to" in result["msg"]
    assert "api_host_after" in result["msg"]
    assert writes(fake) == []


def test_an_api_host_ipv6_address_in_brackets_is_checked_as_an_address(fake_api, run_module):
    """The client takes an IPv6 api_host already in the brackets a URL needs; the address check must read it too."""
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(ip_ranges=("2001:db8::5-6",), floating=(), netmask=None)), etag='"1"')

    want = {**STATIC_WANT, "ip_ranges": ["2001:db8::7"], "floating_ip_ranges": [], "netmask": None}
    result = run_module(network_config, {**CONNECTION, "api_host": "[2001:db8::5]", "frontend_networks": [want]})

    assert result["failed"] is True
    assert "would drop [2001:db8::5], the address this task connects to" in result["msg"]
    assert writes(fake) == []


def test_allowed_disruption_still_needs_api_host_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static()), etag='"1"')

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.7"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [want], "allow_management_disruption": True})

    assert result["failed"] is True
    assert "api_host_after" in result["msg"]


def test_allowed_disruption_reads_back_through_api_host_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static()), etag='"1"')
    moved = document(static(ip_ranges=["10.20.0.7"]))
    fake.expect("PUT", NETWORK, body=moved, status=202)
    fake.expect("GET", NETWORK, response=moved, etag='"2"')

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.7"]}
    args = {"frontend_networks": [want], "allow_management_disruption": True, "api_host_after": "10.20.0.7"}
    result = run_module(network_config, {**CONNECTION, **args})

    assert result["changed"] is True, result.get("msg")
    assert result["api_host"] == "10.20.0.7"
    assert fake.params["api_host"] == "10.20.0.5"


def test_api_host_after_an_ipv6_address_is_bracketed_in_the_read_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static()), etag='"1"')
    moved = document(static(ip_ranges=["10.20.0.7"]))
    fake.expect("PUT", NETWORK, body=moved, status=202)
    fake.expect("GET", NETWORK, response=moved, etag='"2"')

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.7"]}
    args = {"frontend_networks": [want], "allow_management_disruption": True, "api_host_after": "fd00::7"}
    result = run_module(network_config, {**CONNECTION, **args})

    assert result["changed"] is True, result.get("msg")
    assert fake.log[-1].netloc == "[fd00::7]:8000"


def test_api_host_in_the_floating_ranges_still_counts_as_kept(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static()), etag='"1"')
    moved = document(static(ip_ranges=["10.20.0.7"], floating=["10.20.0.5", "10.20.0.50-51"]))
    fake.expect("PUT", NETWORK, body=moved, status=202)
    fake.expect("GET", NETWORK, response=moved, etag='"2"')

    want = {**STATIC_WANT, "ip_ranges": ["10.20.0.7"], "floating_ip_ranges": ["10.20.0.5", "10.20.0.50-10.20.0.51"]}
    result = run_module(network_config, {**CONNECTION, "frontend_networks": [want]})

    assert result["changed"] is True, result.get("msg")


def test_lost_connection_after_the_put_is_reconciled_by_reading(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", NETWORK, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    fake.expect("GET", NETWORK, response=document(host(name="it-network")), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "it-network", "type": "HOST"}]})

    assert result["changed"] is True, result.get("msg")
    assert "write reconciled after a lost connection" in result["notes"]


def test_document_that_reads_back_different_fails_reporting_the_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", NETWORK, body=document(host(name="it-network")), status=202)
    fake.expect("GET", NETWORK, response=document(host()), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "it-network", "type": "HOST"}]})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "the network document did not converge" in result["msg"]
    assert result["expected"]["frontend_networks"][0]["name"] == "it-network"
    assert result["observed"]["frontend_networks"][0]["name"] == "Default"


def test_412_reread_that_already_matches_sends_no_second_put(fake_api, run_module):
    """F5: the ETag drifts as addresses are allocated, so a 412 is expected; another writer may already have written the same."""
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", NETWORK, body=document(host(name="it-network")), if_match='"1"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", NETWORK, response=document(host(name="it-network")), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "it-network", "type": "HOST"}]})

    assert result["changed"] is False, result.get("msg")
    assert [e.path for e in writes(fake)] == [NETWORK]
    assert result["network_config"]["frontend_networks"][0]["name"] == "it-network"


def test_412_reread_that_still_differs_puts_against_the_new_etag(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", NETWORK, body=document(host(name="it-network")), if_match='"1"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", NETWORK, response=document(host()), etag='"2"')
    fake.expect("PUT", NETWORK, body=document(host(name="it-network")), if_match='"2"', status=202)
    fake.expect("GET", NETWORK, response=document(host(name="it-network")), etag='"3"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [{"id": 1, "name": "it-network", "type": "HOST"}]})

    assert result["changed"] is True, result.get("msg")


def test_412_reread_settles_against_the_parts_it_did_not_own_as_last_read(fake_api, run_module):
    """F5: Terraform may move the floating addresses between the reads; the write keeps the newer ones and converges."""
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(host(floating=["10.20.0.50"])), etag='"1"')
    fake.expect("PUT", NETWORK, if_match='"1"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", NETWORK, response=document(host(floating=["10.20.0.60"])), etag='"2"')
    renamed = document(host(name="edge", floating=["10.20.0.60"]))
    fake.expect("PUT", NETWORK, body=renamed, if_match='"2"', status=202)
    fake.expect("GET", NETWORK, response=renamed, etag='"3"')

    result = run_module(network_config, {**CONNECTION, "managed_by": "terraform_provider", "frontend_networks": [{"id": 1, "name": "edge", "type": "HOST"}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize("bad", ["10.20.0.300", "10.20.0.9-10.20.0.5", "not-an-address"])
def test_range_that_does_not_parse_fails_before_any_request(fake_api, run_module, bad):
    fake = fake_api()
    want = {**STATIC_WANT, "ip_ranges": [bad]}

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [want]})

    assert result["failed"] is True
    assert f"frontend_networks[0].ip_ranges: '{bad}' is not an address range" in result["msg"]
    assert fake.log == []


def test_unset_parts_keep_the_document(fake_api, run_module):
    fake = fake_api()
    live = document(static(), interfaces=managed())
    fake.expect("GET", NETWORK, response=copy.deepcopy(live), etag='"1"')

    result = run_module(network_config, CONNECTION)

    assert result["changed"] is False, result.get("msg")


# Tenants by name (spec 014, FR-012 to FR-014 and FR-031)

ENG = tenant(2, "eng", networks=[2])
TWO_TENANTS = (tenant(1, "Default", networks=[1]), ENG)
DEFAULT_NET = {"id": 1, "name": "Default", "type": "STATIC"}
ENG_NET = {"id": 2, "name": "eng", "type": "STATIC"}


def two_networks(eng_tenant=2):
    """Default's network, with the cluster's one IPv4 gateway, and eng's on VLAN 20."""
    return document(static(), static(2, "eng", ("10.30.0.5-6",), (), tenant=eng_tenant), interfaces=managed(frontend_vlans=[VLANS[0]]))


def puts(fake, path=NETWORK):
    return [e.body for e in fake.log if e.method == "PUT" and e.path == path]


def test_a_network_takes_its_tenant_by_name_and_every_network_keeps_its_tenant_in_the_put(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance"))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", NETWORK, body=two_networks(eng_tenant=3), status=202)
    fake.expect("GET", NETWORK, response=two_networks(eng_tenant=3), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]})

    assert result["changed"] is True, result.get("msg")
    assert [n["tenant_id"] for n in puts(fake)[0]["frontend_networks"]] == [1, 3]
    assert [(n["tenant"], n["tenant_id"]) for n in result["network_config"]["frontend_networks"]] == [("Default", 1), ("finance", 3)]


def test_tenant_and_tenant_id_are_accepted_together_when_they_name_one_tenant(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "eng", "tenant_id": 2}]})

    assert result["changed"] is False, result.get("msg")


def test_tenant_and_tenant_id_that_differ_are_refused_naming_both(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "eng", "tenant_id": 1}]})

    assert result["failed"] is True
    assert result["msg"] == "frontend_networks[1] names tenant 'eng' and tenant_id 1, which is tenant 'Default'; give tenant alone"
    assert puts(fake) == []


def test_a_tenant_id_the_cluster_lacks_is_dropped_with_a_warning_and_the_network_keeps_its_tenant(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant_id": 9}]})

    assert result["changed"] is False, result.get("msg")
    assert any(w.startswith("frontend_networks[1]: tenant_id 9 is no tenant on the cluster") for w in result["warnings"])


def test_a_tenant_the_cluster_lacks_fails_naming_the_tenants(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]})

    assert result["failed"] is True
    assert result["msg"] == "no tenant on this cluster is named 'finance'; its tenants are 'Default' and 'eng'"


def test_in_check_mode_a_pending_tenants_network_is_validated_without_tenant_id(fake_api, run_module):
    """FR-014: an earlier task creates finance, so the document check mode validates names no tenant for its network."""
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", VALIDATE, if_match=NO_HEADER, status=200)

    planned = {"Default": True, "eng": True, "finance": True}
    args = {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}], "planned_tenants": planned}
    result = run_module(network_config, args, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert "tenant_id" not in puts(fake, VALIDATE)[0]["frontend_networks"][1]
    assert result["network_config"]["frontend_networks"][1]["tenant"] == "finance"
    assert result["network_config"]["frontend_networks"][1]["tenant_id"] is None
    assert result["notes"] == ["tenant 'finance' does not exist yet; an earlier task is expected to create it"]


BACKEND = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [4]}


def with_backend(backend_tenant=1, name="backend"):
    return document(static(), static(4, name, ("10.99.0.5-6",), (), tenant=backend_tenant), interfaces=managed(frontend_vlans=[], backend_bond_config=BACKEND))


def test_a_tenant_on_a_backend_network_is_refused(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=with_backend(), etag='"1"')

    entries = [DEFAULT_NET, {"id": 4, "name": "backend", "type": "STATIC", "tenant": "Default"}]
    result = run_module(network_config, {**CONNECTION, "frontend_networks": entries})

    assert result["failed"] is True
    assert result["msg"].startswith("frontend_networks[1]: network 4 is on the backend bond, and a backend network takes no tenant")


def test_a_tenant_id_on_a_backend_network_is_dropped_and_no_backend_network_carries_a_tenant_in_the_put(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", NETWORK, response=with_backend(), etag='"1"')
    fake.expect("PUT", NETWORK, status=202)
    fake.expect("GET", NETWORK, response=with_backend(name="backend-2"), etag='"2"')

    entries = [DEFAULT_NET, {"id": 4, "name": "backend-2", "type": "STATIC", "tenant_id": 1}]
    result = run_module(network_config, {**CONNECTION, "frontend_networks": entries})

    assert result["changed"] is True, result.get("msg")
    assert "tenant_id" not in puts(fake)[0]["frontend_networks"][1]
    assert puts(fake)[0]["frontend_networks"][0]["tenant_id"] == 1
    assert "frontend_networks[1]: network 4 is on the backend bond, which takes no tenant, so tenant_id 1 is dropped" in result["warnings"]


def test_two_tenants_on_one_vlan_are_refused(fake_api, run_module):
    vlan = {"vlan_id": 20, "mtu": None, "network_id": 2, "secondary_network_id": 3}
    six = {
        "id": 3,
        "name": "eng6",
        "addresses": {
            "type": "STATIC",
            "static_addresses": {"default_gateway": "2001:db8::1", "ip_ranges": ["2001:db8::5-6"], "floating_ip_ranges": [], "netmask": None},
        },
        "tenant_id": 2,
    }
    live = document(static(), static(2, "eng", ("10.30.0.5-6",), (), tenant=2), six, interfaces=managed(frontend_vlans=[vlan]))
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=live, etag='"1"')

    entries = [DEFAULT_NET, ENG_NET, {"id": 3, "name": "eng6", "type": "STATIC", "tenant": "Default"}]
    result = run_module(network_config, {**CONNECTION, "frontend_networks": entries})

    assert result["failed"] is True
    assert result["msg"] == (
        "VLAN 20 carries 3 (eng6) in tenant 'Default' and 2 (eng) in tenant 'eng', but the networks of one interface or "
        "VLAN belong to one tenant. Put each tenant's networks on an interface or VLAN of their own"
    )
    assert puts(fake) == []


def test_a_new_network_with_no_tenant_is_refused_on_a_cluster_with_two_tenants(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed(frontend_vlans=[])), etag='"1"')

    entries = [DEFAULT_NET, {**ENG_NET, "ip_ranges": ["10.30.0.5-6"]}]
    result = run_module(network_config, {**CONNECTION, "frontend_networks": entries, "managed_interfaces": managed(frontend_vlans=[VLANS[0]])})

    assert result["failed"] is True
    assert result["msg"].startswith("network 2 (eng) is new and names no tenant")
    assert puts(fake) == []


def test_binding_a_network_to_another_tenant_warns_of_the_static_routes(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance"))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]}, check_mode=True)

    assert (
        "this network change binds network 2 (eng) to tenant 'finance', which removes the static routes of every node (G-TENANT-ROUTES)" in result["warnings"]
    )


# One IPv4 and one IPv6 default gateway (qumulo-findings item 35)

GATEWAY_TENANTS = (tenant(1, "Default", networks=[1, 3]), tenant(2, "eng", networks=[2, 4]))
IPV6 = {"floating": (), "netmask": None}


def four_networks(gateways=("10.20.0.1", "", "", "")):
    """Default's IPv4 and IPv6 networks on the bond and eng's on VLAN 20, carrying gateways, by default the one IPv4."""
    networks = [
        static(1, "Default", gateway=gateways[0]),
        static(2, "eng", ("10.30.0.5-6",), (), tenant=2, gateway=gateways[1]),
        static(3, "Default6", ("2001:db8::5-6",), **IPV6, gateway=gateways[2]),
        static(4, "eng6", ("2001:db8:1::5-6",), **IPV6, tenant=2, gateway=gateways[3]),
    ]
    vlan = {"vlan_id": 20, "mtu": None, "network_id": 2, "secondary_network_id": 4}
    return document(*networks, interfaces=managed(frontend_bond_config={**BOND, "networks": [1, 3]}, frontend_vlans=[vlan]))


def with_gateways(*gateways):
    names = ("Default", "eng", "Default6", "eng6")
    return [{"id": index, "name": name, "type": "STATIC", "default_gateway": gateway} for index, (name, gateway) in enumerate(zip(names, gateways), 1)]


def two_gateways(networks, version):
    return (
        f"Networks {networks} each carry an IPv{version} default gateway, but a cluster on chassis nodes takes one IPv{version} "
        "default gateway, and the module refuses a second on every cluster. Keep the gateway on one network and set "
        'default_gateway: "" on the others; a new network left without default_gateway has none.'
    )


def held_gateways(networks, version):
    return (
        f"Networks {networks} each carry an IPv{version} default gateway, which a chassis cluster refuses; the cluster already "
        'holds them, so they are kept. To leave the gateway on one network, set default_gateway: "" on the others.'
    )


@pytest.mark.parametrize(
    "gateways",
    [("", "10.30.0.1", "", ""), ("10.20.0.1", "", "2001:db8::1", "")],
    ids=["the one IPv4 gateway on a VLAN network", "one of each, the VLAN networks without"],
)
def test_one_default_gateway_of_each_ip_version_is_validated(fake_api, run_module, gateways):
    fake = fake_api(*GATEWAY_TENANTS)
    fake.expect("GET", NETWORK, response=four_networks(), etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    result = run_module(network_config, {**CONNECTION, "frontend_networks": with_gateways(*gateways)}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result.get("warnings", []) == []
    assert [network["default_gateway"] for network in result["network_config"]["frontend_networks"]] == [gateway or None for gateway in gateways]


@pytest.mark.parametrize(
    ("gateways", "message"),
    [
        (("10.20.0.1", "10.30.0.1", "", ""), two_gateways("1 (Default) and 2 (eng)", 4)),
        (("10.20.0.1", "", "2001:db8::1", "2001:db8:1::1"), two_gateways("3 (Default6) and 4 (eng6)", 6)),
        (
            ("10.20.0.1", "10.30.0.1", "2001:db8::1", "2001:db8:1::1"),
            f"{two_gateways('1 (Default) and 2 (eng)', 4)} {two_gateways('3 (Default6) and 4 (eng6)', 6)}",
        ),
    ],
    ids=["two IPv4", "two IPv6", "two of each"],
)
@pytest.mark.parametrize("check_mode", [False, True])
def test_a_second_default_gateway_of_one_ip_version_is_refused_before_validate_or_the_put(fake_api, run_module, gateways, message, check_mode):
    """The cluster refuses it on chassis nodes, and a client cannot tell those from others, so the module refuses it on any."""
    fake = fake_api(*GATEWAY_TENANTS)
    fake.expect("GET", NETWORK, response=four_networks(), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": with_gateways(*gateways)}, check_mode=check_mode)

    assert result["failed"] is True
    assert result["msg"] == message
    assert puts(fake) == []
    assert puts(fake, VALIDATE) == []


THREE_TENANTS = (tenant(1, "Default", networks=[1, 3]), tenant(2, "eng", networks=[2]))


def three_networks(gateways):
    """Three IPv4 networks: Default's on the bond and on VLAN 30, eng's on VLAN 20, carrying the gateways given."""
    networks = [
        static(1, "Default", gateway=gateways[0]),
        static(2, "eng", ("10.30.0.5-6",), (), tenant=2, gateway=gateways[1]),
        static(3, "lab", ("10.40.0.5-6",), (), tenant=1, gateway=gateways[2]),
    ]
    vlans = [VLANS[0], {"vlan_id": 30, "mtu": None, "network_id": 3, "secondary_network_id": None}]
    return document(*networks, interfaces=managed(frontend_vlans=vlans))


def three_with(gateways, **changes):
    entries = [
        {"id": index, "name": name, "type": "STATIC", "default_gateway": gateway}
        for index, (name, gateway) in enumerate(zip(("Default", "eng", "lab"), gateways), 1)
    ]
    entries[2].update(changes)
    return entries


HELD = ("10.20.0.1", "10.30.0.1", "")


@pytest.mark.parametrize("check_mode", [True, False])
def test_two_default_gateways_the_cluster_already_holds_warn_and_do_not_block_a_write(fake_api, run_module, check_mode):
    """The cluster took them, so it is not one that refuses them; a task that leaves them alone may still write."""
    fake = fake_api(*THREE_TENANTS)
    fake.expect("GET", NETWORK, response=three_networks(HELD), etag='"1"')
    widened = three_networks(HELD)
    widened["frontend_networks"][2]["addresses"]["static_addresses"]["floating_ip_ranges"] = ["10.40.0.50"]
    if check_mode:
        fake.expect("PUT", VALIDATE, body=widened, status=200)
    else:
        fake.expect("PUT", NETWORK, body=widened, if_match='"1"', status=202)
        fake.expect("GET", NETWORK, response=widened, etag='"2"')

    args = {**CONNECTION, "frontend_networks": three_with(HELD, floating_ip_ranges=["10.40.0.50"])}
    result = run_module(network_config, args, check_mode=check_mode)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == [held_gateways("1 (Default) and 2 (eng)", 4)]


def test_two_default_gateways_the_cluster_already_holds_warn_of_nothing_when_nothing_changes(fake_api, run_module):
    fake = fake_api(*THREE_TENANTS)
    fake.expect("GET", NETWORK, response=three_networks(HELD), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": three_with(HELD)})

    assert result["changed"] is False, result.get("msg")
    assert result.get("warnings", []) == []
    assert puts(fake) == []


def test_a_gateway_added_beside_two_the_cluster_holds_is_refused(fake_api, run_module):
    fake = fake_api(*THREE_TENANTS)
    fake.expect("GET", NETWORK, response=three_networks(HELD), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": three_with((*HELD[:2], "10.40.0.1"))})

    assert result["failed"] is True
    assert result["msg"] == two_gateways("1 (Default), 2 (eng) and 3 (lab)", 4)
    assert puts(fake) == []


def test_removing_one_of_three_held_gateways_warns_of_the_two_left(fake_api, run_module):
    fake = fake_api(*THREE_TENANTS)
    fake.expect("GET", NETWORK, response=three_networks((*HELD[:2], "10.40.0.1")), etag='"1"')
    fake.expect("PUT", VALIDATE, body=three_networks(HELD), status=200)

    result = run_module(network_config, {**CONNECTION, "frontend_networks": three_with(HELD)}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == [held_gateways("1 (Default) and 2 (eng)", 4)]


# The continuity rule on the module's own document (spec 014, "The continuity rule"; FR-010, FR-011)


def reads(fake, path):
    return [e for e in fake.log if e.method == "GET" and e.path == path]


def test_a_run_that_changes_nothing_and_names_no_tenant_reads_no_tenant_list_and_names_no_tenant(fake_api, run_module):
    """FR-031: the result has the same shape whether or not the run read the tenant list."""
    fake = fake_api()
    two = static(2, "Two", ip_ranges=("10.30.0.5-6",), floating=(), tenant=1)
    fake.expect("GET", NETWORK, response=document(static(), two, interfaces=managed()), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "managed_interfaces": managed()})

    assert result["changed"] is False, result.get("msg")
    assert reads(fake, TENANTS) == []
    networks = result["network_config"]["frontend_networks"]
    assert [(network["tenant"], network["tenant_id"]) for network in networks] == [(None, 1), (None, 1)]
    assert list(networks[0]).index("tenant") == list(networks[0]).index("tenant_id") - 1


ON_ENG = {**CONNECTION, "api_host": "10.30.0.5"}  # a node address of network 2, which eng holds


def test_moving_the_management_network_to_a_tenant_without_rest_is_refused(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance", rest_api_enabled=False))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    result = run_module(network_config, {**ON_ENG, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]})

    assert result["failed"] is True
    assert result["msg"] == (
        "This network change would leave network 2 (eng) in tenant finance with rest_api_enabled off, and this play would "
        "lose its connection. Set rest_api_enabled: true on finance, or list 2 (eng) under a tenant that has "
        "rest_api_enabled: true."
    )
    assert puts(fake) == [] and reads(fake, STATUS) == []


def test_in_check_mode_planned_tenants_judge_the_planned_document(fake_api, run_module):
    """US1-6: the role turns finance's REST on at 3a, before this PUT, so check mode passes it."""
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance", rest_api_enabled=False))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    planned = {"Default": True, "eng": True, "finance": True}
    args = {**ON_ENG, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}], "planned_tenants": planned}
    result = run_module(network_config, args, check_mode=True)

    assert result["changed"] is True, result.get("msg")


def test_at_one_tenant_the_rule_asks_that_tenant_alone_and_reads_no_status(fake_api, run_module):
    fake = fake_api(tenant(1, "Default", networks=[1], rest_api_enabled=False))
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')

    result = run_module(network_config, {**CONNECTION, "api_host": "203.0.113.9", "frontend_networks": [{"id": 1, "name": "renamed", "type": "HOST"}]})

    assert result["failed"] is True
    assert result["msg"].startswith("This network change would leave tenant Default, the only tenant, with rest_api_enabled off")
    assert reads(fake, STATUS) == []


def test_with_management_network_given_status_is_never_read(fake_api, run_module):
    fake = fake_api(tenant(1, "Default", networks=[1]), tenant(2, "eng"))
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    args = {**CONNECTION, "api_host": "203.0.113.9", "frontend_networks": [{"id": 1, "name": "renamed", "type": "HOST"}], "management_network": [1]}
    result = run_module(network_config, args, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert reads(fake, STATUS) == []


def test_without_management_network_an_unplaced_address_reads_status_and_a_500_refuses_with_message_5(fake_api, run_module):
    fake = fake_api(tenant(1, "Default", networks=[1]), tenant(2, "eng"))
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("GET", STATUS, status=500, response={"error_class": "net_read_error", "description": "decode_error"})

    args = {**CONNECTION, "api_host": "203.0.113.9", "frontend_networks": [{"id": 1, "name": "renamed", "type": "HOST"}]}
    result = run_module(network_config, args)

    assert result["failed"] is True
    assert result["msg"].startswith("The cluster's network status is unavailable (G-NET-APPLY)")
    assert puts(fake) == []


def test_allow_management_disruption_skips_the_rule(fake_api, run_module):
    fake = fake_api(tenant(1, "Default", networks=[1], rest_api_enabled=False))
    fake.expect("GET", NETWORK, response=document(host()), etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    args = {**CONNECTION, "api_host": "203.0.113.9", "frontend_networks": [{"id": 1, "name": "renamed", "type": "HOST"}], "allow_management_disruption": True}
    result = run_module(network_config, args, check_mode=True)

    assert result["changed"] is True, result.get("msg")


def test_alone_in_check_mode_a_management_network_moving_to_a_pending_tenant_is_predicted_with_a_note(fake_api, run_module):
    """FR-014: a tenant an earlier task creates has no REST switches to judge yet; the real run judges them."""
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    result = run_module(network_config, {**ON_ENG, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == [
        "tenant 'finance' does not exist yet; an earlier task is expected to create it, and the check does not judge its REST switches; the run does"
    ]


# PR 236's walkthrough


def test_a_document_with_a_backend_network_read_by_network_info_feeds_back_unchanged(fake_api, run_module):
    """FR-031: the backend network's tenant reads as null, since the network takes none, so the read gives back."""
    from ansible_collections.qumulo.cluster.plugins.modules import network_info

    fake = fake_api()
    fake.serve(NETWORK, with_backend())
    document = run_module(network_info, {**CONNECTION, "gather_subset": ["document"]})["network"]["document"]

    assert [n["tenant"] for n in document["frontend_networks"]] == ["Default", None]
    result = run_module(network_config, {**CONNECTION, **document}, check_mode=True)

    assert result["changed"] is False, result.get("msg")
    assert [n["tenant"] for n in result["network_config"]["frontend_networks"]] == ["Default", None]


def test_a_rebinding_write_the_continuity_rule_refuses_warns_of_no_static_routes(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance", rest_api_enabled=False))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    result = run_module(network_config, {**ON_ENG, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]})

    assert result["failed"] is True
    assert result["warnings"] == []


def test_in_check_mode_the_planned_tenants_count_for_a_new_network_without_a_tenant(fake_api, run_module):
    """The role creates the second tenant at 3a, before this PUT, so the check counts two tenants as the run will."""
    fake = fake_api()
    fake.expect("GET", NETWORK, response=document(static(), interfaces=managed(frontend_vlans=[])), etag='"1"')

    entries = [DEFAULT_NET, {**ENG_NET, "ip_ranges": ["10.30.0.5-6"]}]
    args = {**CONNECTION, "frontend_networks": entries, "managed_interfaces": managed(frontend_vlans=[VLANS[0]])}
    result = run_module(network_config, {**args, "planned_tenants": {"Default": True, "finance": True}}, check_mode=True)

    assert result["failed"] is True
    assert result["msg"].startswith("network 2 (eng) is new and names no tenant")


def test_a_cluster_tenant_the_plan_leaves_out_is_judged_by_its_live_switches(fake_api, run_module):
    """As a tenant the role renames earlier in the run: the bindings name it as it is now."""
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance", rest_api_enabled=False))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')

    planned = {"Default": True, "eng": True}
    result = run_module(
        network_config, {**ON_ENG, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}], "planned_tenants": planned}, check_mode=True
    )

    assert result["failed"] is True
    assert result["msg"].startswith("This network change would leave network 2 (eng) in tenant finance with rest_api_enabled off")


def test_a_conflict_already_on_a_vlan_warns_and_lets_a_write_that_leaves_it_alone_through(fake_api, run_module):
    vlan = {"vlan_id": 20, "mtu": None, "network_id": 2, "secondary_network_id": 3}
    six = {
        "id": 3,
        "name": "eng6",
        "addresses": {
            "type": "STATIC",
            "static_addresses": {"default_gateway": "2001:db8::1", "ip_ranges": ["2001:db8::5-6"], "floating_ip_ranges": [], "netmask": None},
        },
        "tenant_id": 1,
    }
    live = document(static(), static(2, "eng", ("10.30.0.5-6",), (), tenant=2), six, interfaces=managed(frontend_vlans=[vlan]))
    fake = fake_api(*TWO_TENANTS)
    fake.expect("GET", NETWORK, response=live, etag='"1"')
    fake.expect("PUT", VALIDATE, status=200)

    result = run_module(
        network_config, {**CONNECTION, "owned_parts": ["floating_ips"], "frontend_networks": [{"id": 1, "floating_ip_ranges": ["10.20.0.52"]}]}, check_mode=True
    )

    assert result["changed"] is True, result.get("msg")
    assert any(w.startswith("VLAN 20 already carries 3 (eng6) in tenant 'Default' and 2 (eng) in tenant 'eng'") for w in result["warnings"])


def test_a_rebinding_put_that_does_not_converge_still_warns_of_the_static_routes(fake_api, run_module):
    """The PUT went out, so the routes may be gone, though the read-back shows another document."""
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance"))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", NETWORK, status=202)
    fake.expect("GET", NETWORK, response=two_networks(), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]})

    assert result["failed"] is True
    assert result["msg"] == "the network document did not converge"
    assert any("removes the static routes of every node" in w for w in result["warnings"])


def test_a_rebinding_write_a_412_reread_finds_already_made_sends_nothing_and_warns_of_nothing(fake_api, run_module):
    fake = fake_api(*TWO_TENANTS, tenant(3, "finance"))
    fake.expect("GET", NETWORK, response=two_networks(), etag='"1"')
    fake.expect("PUT", NETWORK, status=412, response={"error_class": "http_precondition_failed_error", "description": "etag"})
    fake.expect("GET", NETWORK, response=two_networks(eng_tenant=3), etag='"2"')

    result = run_module(network_config, {**CONNECTION, "frontend_networks": [DEFAULT_NET, {**ENG_NET, "tenant": "finance"}]})

    assert result["changed"] is False, result.get("msg")
    assert not any("static routes" in w for w in result["warnings"])
