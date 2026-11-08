# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""network.py's answers to which network carries an address: holding in the document, carrying in the status.

The status bodies are written by hand, with documentation addresses, in the shape Qumulo Core 7.11.0 answers
/v3/network/status (qumulo-findings item 37): four hardware nodes with a STATIC untagged network, and a VM edge whose
host manages its network. Each device's details are under network_details, with use_for, where the 7.11.0 OpenAPI
document gives api_network_specific_fields, with type; a qumulo_ip_addresses entry's network_id is not the network's id
in the document on hardware; and on the VM edge the host's addresses are under host_routable_ip_addresses, with qumulo_ip_addresses
empty while the document has no frontend network, and one entry assigned by HOST, with an empty address and network_id
0, once HOST network 0 is declared. No status has been seen with a DHCP network or with two HOST networks; those cases
follow the schema.
"""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import network


def entry(network_id, address="", floating=(), assigned_by="STATIC", netmask="255.255.255.0"):
    """One qumulo_ip_addresses entry: a network's node address and floating addresses on a device."""
    return {"address": address, "assigned_by": assigned_by, "floating_addresses": list(floating), "gateway": None, "netmask": netmask, "network_id": network_id}


def device(name, *entries, use="FRONTEND_AND_BACKEND", host=(), key="network_details"):
    """A device as 7.11.0 answers it, or under key api_network_specific_fields as the OpenAPI document gives it."""
    details = {"host_routable_ip_addresses": list(host), "qumulo_ip_addresses": list(entries)}
    details["use_for" if key == "network_details" else "type"] = use
    return {"name": name, "vlan_id": None, key: details}


def unused(name, *addresses):
    """A device with no configured network, such as the loopback, which lists its addresses tied to no network."""
    return {"name": name, "vlan_id": None, "network_details": {"addresses": list(addresses), "use_for": "NONE"}}


def node(node_id, *devices):
    return {"node_id": node_id, "node_name": f"node{node_id}", "network_statuses": {}, "managed_interface_statuses": [], "devices": list(devices)}


def wire_static(network_id, name, ip_ranges=(), floating=()):
    block = {"default_gateway": "", "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), "netmask": "255.255.255.0"}
    return {"id": network_id, "name": name, "tenant_id": 1, "addresses": {"type": "STATIC", "static_addresses": block}}


def wire_dhcp(network_id, name):
    return {"id": network_id, "name": name, "tenant_id": 1, "addresses": {"type": "DHCP", "dhcp_addresses": {"floating_ip_ranges": [], "netmask": None}}}


def canonical(*networks, managed=None):
    return network.canonical_document({"managed_interfaces": managed, "frontend_networks": list(networks)})


# The hardware shape: the untagged network, which the document holds as 1, shows as network_id 0, as on 7.11.0, and
# each node shows its own node address and the floating address it holds. The VLAN network's status is not recorded;
# its network_id 1 here is one more id that is not the document's.
BOND = {"frontend_bond_config": {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 1500, "networks": [1]}, "frontend_vlans": []}
STATIC_DOCUMENT = canonical(wire_static(1, "Default", ["192.0.2.11-14"], ["192.0.2.51-54"]), wire_static(2, "finance", ["198.51.100.11-14"]), managed=BOND)
STATIC_STATUS = [
    node(1, device("bond0", entry(0, "192.0.2.11", ["192.0.2.51"])), device("bond0.20", entry(1, "198.51.100.11"), use="FRONTEND")),
    node(2, device("bond0", entry(0, "192.0.2.12", ["192.0.2.52"])), device("bond0.20", entry(1, "198.51.100.12"), use="FRONTEND")),
]

# The VM edge's shape: one node, its interface with the host's two addresses, and the loopback. With no frontend
# network the interface lists no qumulo_ip_addresses; with HOST network 0 declared it lists that network's entry.
VM_STATUS = [node(1, device("enp1s0", host=["192.0.2.21", "2001:db8::21"]), unused("lo", "127.0.0.1", "::1"))]
VM_HOSTED_STATUS = [node(1, device("enp1s0", entry(0, assigned_by="HOST", netmask=""), host=["192.0.2.21", "2001:db8::21"]), unused("lo", "127.0.0.1", "::1"))]


@pytest.mark.parametrize(
    ("address", "carrier"),
    [
        ("192.0.2.11", 1),  # a node address
        ("192.0.2.52", 1),  # a floating address, on another node
        ("198.51.100.12", 2),  # a VLAN network's node address
    ],
)
def test_carrying_names_a_static_network_by_the_documents_ranges_and_never_by_the_status_network_id(address, carrier):
    assert network.carrying(STATIC_STATUS, STATIC_DOCUMENT, address) == carrier


def test_carrying_reads_the_key_the_openapi_document_gives():
    status = [node(1, device("bond0", entry(0, "192.0.2.11", ["192.0.2.51"]), key="api_network_specific_fields"))]

    assert network.carrying(status, STATIC_DOCUMENT, "192.0.2.51") == 1


def test_carrying_needs_the_status_to_show_the_address():
    """192.0.2.13 is in network 1's node range, but no node holds it yet."""
    assert network.carrying(STATIC_STATUS, STATIC_DOCUMENT, "192.0.2.13") is None


def test_carrying_finds_no_network_for_a_static_address_no_range_holds():
    """The status shows an address the document no longer gives; its network_id names no network of the document."""
    status = [node(1, device("bond0", entry(1, "203.0.113.11")))]

    assert network.carrying(status, STATIC_DOCUMENT, "203.0.113.11") is None


@pytest.mark.parametrize("address", ["192.0.2.21", "2001:DB8:0::21"])
def test_carrying_places_a_host_address_on_the_documents_host_network(address):
    """The host associates the interface with the HOST network, and status gives no network id beside its addresses."""
    document = canonical(wire_host(0, []))

    assert network.carrying(VM_HOSTED_STATUS, document, address) == 0
    assert network.carrying(VM_STATUS, document, address) == 0


def test_carrying_finds_no_network_on_a_host_managed_cluster_with_no_network_yet():
    assert network.carrying(VM_STATUS, canonical(), "192.0.2.21") is None


def test_carrying_cannot_tell_two_host_networks_apart_by_a_host_address():
    document = canonical(wire_host(0, []), {**wire_host(3, []), "name": "finance"})

    assert network.carrying(VM_HOSTED_STATUS, document, "192.0.2.21") is None


def test_carrying_names_a_host_network_by_its_floating_range():
    """With two HOST networks, a floating address that status lists is named by the range that holds it."""
    document = canonical(wire_host(0, ["192.0.2.60-61"]), {**wire_host(3, ["2001:db8::60"]), "name": "finance"})
    status = [node(1, device("eth0", entry(7, "", ["192.0.2.60"], assigned_by="HOST", netmask=""), host=["192.0.2.21"]))]

    assert network.carrying(status, document, "192.0.2.60") == 0


def test_carrying_places_a_dhcp_node_address_on_the_documents_one_dhcp_network():
    """ip_ranges holds only a STATIC network's node addresses, so the entry's assignment names the network."""
    status = [node(1, device("bond0", entry(0, "192.0.2.30", assigned_by="DHCP")))]

    assert network.carrying(status, canonical(wire_dhcp(1, "Default"), managed=BOND), "192.0.2.30") == 1
    assert network.carrying(status, canonical(wire_dhcp(1, "Default"), wire_dhcp(2, "lab"), managed=BOND), "192.0.2.30") is None


def test_carrying_finds_no_network_for_an_address_on_a_backend_or_unused_device():
    status = [node(1, device("bond1", entry(0, "192.0.2.11"), use="BACKEND"), unused("lo", "127.0.0.1"))]

    assert network.carrying(status, STATIC_DOCUMENT, "192.0.2.11") is None
    assert network.carrying(VM_STATUS, canonical(wire_host(0, [])), "127.0.0.1") is None


def test_carrying_finds_no_network_for_an_address_no_device_holds():
    assert network.carrying(STATIC_STATUS, STATIC_DOCUMENT, "203.0.113.9") is None


@pytest.mark.parametrize(
    ("status", "carries"),
    [
        pytest.param(VM_STATUS, False, id="the host's addresses alone, as with no HOST network"),
        pytest.param(VM_HOSTED_STATUS, True, id="the host's addresses and a HOST entry"),
        pytest.param([node(1, device("eth0", entry(0, assigned_by="HOST", netmask=""), key="api_network_specific_fields"))], True, id="a HOST entry"),
        pytest.param(STATIC_STATUS, False, id="hardware"),
        pytest.param([node(1, device("eth1", host=["192.0.2.21"], use="BACKEND"), unused("lo", "127.0.0.1"))], False, id="backend only"),
    ],
)
def test_carries_host_network_asks_whether_a_frontend_device_lists_an_entry_assigned_by_host(status, carries):
    """A host-managed node lists the host's addresses with no HOST network at all, so they show no HOST network served."""
    assert network.carries_host_network(status) is carries


def test_carrying_takes_the_one_dhcp_network_off_the_backend_bond():
    bonds = {**BOND, "backend_bond_config": {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [2]}}
    status = [node(1, device("bond0", entry(0, "192.0.2.30", assigned_by="DHCP")))]

    assert network.carrying(status, canonical(wire_dhcp(1, "Default"), wire_dhcp(2, "backend"), managed=bonds), "192.0.2.30") == 1


def test_carrying_skips_a_device_whose_details_are_null():
    status = [node(1, {"name": "ens1f1", "network_details": None}, device("bond0", entry(0, "192.0.2.11")))]

    assert network.carrying(status, STATIC_DOCUMENT, "192.0.2.11") == 1


def test_carrying_reads_the_openapi_key_beside_a_network_details_that_names_no_use():
    """/v1/network/status gives link statistics under network_details; a device carrying both is read from the key that says its use."""
    both = {**device("bond0", entry(0, "192.0.2.11"), key="api_network_specific_fields"), "network_details": {"cable_status": "CONNECTED"}}

    assert network.carrying([node(1, both)], STATIC_DOCUMENT, "192.0.2.11") == 1


def wire_host(network_id, floating):
    return {
        "id": network_id,
        "name": "Default",
        "tenant_id": 1,
        "addresses": {"type": "HOST", "host_addresses": {"floating_ip_ranges": floating, "netmask": None}},
    }


def test_holding_finds_a_host_network_by_its_floating_address_until_the_document_drops_it():
    before = network.canonical_document({"managed_interfaces": None, "frontend_networks": [wire_host(0, ["192.0.2.60-61"])]})
    after = network.canonical_document({"managed_interfaces": None, "frontend_networks": [wire_host(0, [])]})

    assert network.holding(before, "192.0.2.61")["id"] == 0
    assert network.holding(after, "192.0.2.61") is None


def test_holding_matches_the_address_family():
    """::c000:23c has the integer value of 192.0.2.60, which an IPv4 range holding 192.0.2.60 must not count."""
    document = network.canonical_document({"managed_interfaces": None, "frontend_networks": [wire_host(0, ["192.0.2.60"])]})

    assert network.holding(document, "::c000:23c") is None


def wire_document():
    """A wire document as /v3/network answers it: a STATIC network given as a CIDR block, a DHCP, a HOST and a VLAN network, and a backend bond."""
    static = {"default_gateway": "192.0.2.1", "ip_ranges": ["192.0.2.0/28"], "floating_ip_ranges": ["192.0.2.51-52"], "netmask": "24"}
    vlan = {"default_gateway": "198.51.100.1", "ip_ranges": ["198.51.100.11-14"], "floating_ip_ranges": [], "netmask": "255.255.255.0"}
    networks = [
        {"id": 1, "name": "Default", "tenant_id": 1, "addresses": {"type": "STATIC", "static_addresses": static}},
        {"id": 2, "name": "finance", "tenant_id": 2, "addresses": {"type": "STATIC", "static_addresses": vlan}},
        {"id": 3, "name": "lab", "tenant_id": 1, "addresses": {"type": "DHCP", "dhcp_addresses": {"floating_ip_ranges": [], "netmask": None}}},
        {"id": 4, "name": "backend", "addresses": {"type": "DHCP", "dhcp_addresses": {"floating_ip_ranges": [], "netmask": None}}},
        wire_host(5, ["192.0.2.60-61"]),
    ]
    interfaces = {
        "frontend_bond_config": {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [3, 1]},
        "frontend_vlans": [{"vlan_id": 20, "network_id": 2, "secondary_network_id": None, "mtu": 1500}],
        "backend_bond_config": {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [4]},
    }
    return {"managed_interfaces": interfaces, "frontend_networks": networks}


def test_wire_document_puts_a_canonical_document_back_in_the_wire_shape():
    """tenancy_plan reads network_info's canonical document and builds step 3's over it with desired_document, which reads the wire shape."""
    canonical = network.canonical_document(wire_document())

    assert network.canonical_document(network.wire_document(canonical)) == canonical


def test_wire_document_drops_the_tenant_names_and_keeps_tenant_id():
    """network_info's document names each network's tenant beside its id; the wire shape carries only the id."""
    names = {1: "Default", 2: "finance"}
    canonical = network.canonical_document(wire_document())
    named = network.named_document(canonical, names.get)

    wire = network.wire_document(named)

    assert network.canonical_document(wire) == canonical
    assert [network_["tenant_id"] for network_ in wire["frontend_networks"] if "tenant_id" in network_] == [1, 2, 1, 1]
    assert not any("tenant" in network_ for network_ in wire["frontend_networks"])


def test_wire_document_keeps_a_host_managed_document_without_interfaces():
    canonical = network.canonical_document({"managed_interfaces": None, "frontend_networks": [wire_host(0, ["192.0.2.60"])]})

    wire = network.wire_document(canonical)

    assert wire["managed_interfaces"] is None
    assert network.canonical_document(wire) == canonical


@pytest.mark.parametrize(
    ("given", "canonical"),
    [
        pytest.param(["192.0.2.11-14"], ["192.0.2.11-192.0.2.14"], id="ipv4-trailing-part"),
        pytest.param(["192.0.2.11-192.0.2.14"], ["192.0.2.11-192.0.2.14"], id="ipv4-whole"),
        pytest.param(["2001:db8::11-14"], ["2001:db8::11-2001:db8::14"], id="ipv6-trailing-part"),
        pytest.param(["2001:db8::11-2001:db8::14"], ["2001:db8::11-2001:db8::14"], id="ipv6-whole-compressed"),
        pytest.param(["2001:db8::11-2001:db8:0:0:0:0:0:14"], ["2001:db8::11-2001:db8::14"], id="ipv6-whole-exploded"),
        pytest.param(["2001:DB8::11-2001:DB8::14"], ["2001:db8::11-2001:db8::14"], id="ipv6-upper-case"),
        pytest.param(["192.0.2.11-2.14"], ["192.0.2.11-192.0.2.14"], id="ipv4-two-trailing-parts"),
        pytest.param(["2001:db8::1:11-2:14"], ["2001:db8::1:11-2001:db8::2:14"], id="ipv6-two-trailing-groups"),
    ],
)
def test_a_range_s_last_address_is_whole_or_its_trailing_parts_and_canonical_ranges_read_back_alike(given, canonical):
    """A compressed IPv6 last address has fewer groups than eight, yet is a whole address, not trailing parts."""
    assert network.canonical_ranges(given) == canonical
    assert network.canonical_ranges(canonical) == canonical


def test_a_range_whose_last_address_has_every_part_but_does_not_parse_is_refused():
    with pytest.raises(ValueError):
        network.canonical_ranges(["192.0.2.11-192.0.2.300"])
