# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""continuity.py's rule of spec 014 ("The continuity rule"): the management network, REST enabled, and the refusals.

Network documents are built in the wire shape of /v3/network and read through network.canonical_document, as the
modules read them; status bodies are in the shape Qumulo Core 7.11.0 answers /v3/network/status (qumulo-findings
item 37): each device's details under network_details, a qumulo_ip_addresses entry's network_id that is not the
document's id on hardware, and on a VM edge whose host manages its network, the host's addresses under
host_routable_ip_addresses beside the declared HOST network's entry, which is assigned by HOST with an empty address.
"""

from __future__ import annotations

import socket

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import continuity, network
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import (
    NO_ADDRESS,
    NO_NETWORK,
    OUTSIDE,
    STATUS_UNAVAILABLE,
    Breach,
    Management,
    NotRead,
    OffDevice,
    Unavailable,
)

BOND = {"frontend_bond_config": {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]}, "backend_bond_config": None}
UNAVAILABLE = Unavailable("GET /v3/network/status failed: HTTP 500 net_read_error")


def static(network_id, name, ip_ranges=(), floating=(), tenant=None):
    block = {"default_gateway": "192.0.2.1", "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), "netmask": "255.255.255.0"}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "STATIC", "static_addresses": block}}


def dhcp(network_id, name, floating=()):
    block = {"floating_ip_ranges": list(floating), "netmask": None}
    return {"id": network_id, "name": name, "tenant_id": None, "addresses": {"type": "DHCP", "dhcp_addresses": block}}


def host(network_id, name, floating=()):
    block = {"floating_ip_ranges": list(floating), "netmask": None}
    return {"id": network_id, "name": name, "tenant_id": None, "addresses": {"type": "HOST", "host_addresses": block}}


def document(*networks, managed=True):
    return network.canonical_document({"managed_interfaces": BOND if managed else None, "frontend_networks": list(networks)})


MANAGED = document(
    static(1, "Default", ["192.0.2.11-14"], ["192.0.2.51-54"]),
    static(2, "finance", ["198.51.100.11-14"]),
    static(3, "eng", ["2001:db8::11-14"]),
)
HOSTED = document(host(0, "Default", ["192.0.2.60-61"]), managed=False)
EMPTY_HOSTED = document(managed=False)


def entry(network_id, address="", floating=(), assigned_by="STATIC"):
    return {"network_id": network_id, "assigned_by": assigned_by, "netmask": "", "address": address, "floating_addresses": list(floating)}


def status(*entries, host_addresses=()):
    """One node with one frontend device carrying entries, and the host's addresses on it."""
    details = {"host_routable_ip_addresses": list(host_addresses), "qumulo_ip_addresses": list(entries), "use_for": "FRONTEND_AND_BACKEND"}
    return [{"node_id": 1, "node_name": "node1", "network_statuses": {}, "devices": [{"name": "eth0", "network_details": details}]}]


EMPTY_STATUS = status()
HOST_STATUS = status(entry(0, assigned_by="HOST"), host_addresses=["192.0.2.21"])  # a VM edge's, with HOST network 0 declared


def management_network(declared, document_in_force, status, addresses, planned_ids=frozenset(), current=None):
    """continuity.management_network for a write that leaves the document as it is, unless current says otherwise."""
    current = document_in_force if current is None else current
    return continuity.management_network(declared, document_in_force, status, addresses, planned_ids, current=current)


# status_needed: no declared set, and ranges leave an address on no network or place one on a HOST network


@pytest.mark.parametrize(
    ("declared", "document_in_force", "addresses", "needs"),
    [
        ({1}, MANAGED, ["203.0.113.9"], False),
        (None, MANAGED, ["192.0.2.11"], False),
        (None, MANAGED, ["192.0.2.52", "2001:db8::12"], False),
        (None, MANAGED, ["192.0.2.11", "203.0.113.9"], True),
        (None, HOSTED, ["192.0.2.60"], True),
        (None, HOSTED, ["192.0.2.21"], True),
        (None, document(dhcp(1, "Default", ["192.0.2.51"])), ["192.0.2.30"], True),
        (None, MANAGED, [], False),
    ],
)
def test_status_needed(declared, document_in_force, addresses, needs):
    assert continuity.status_needed(declared, document_in_force, addresses) is needs


# management_network


def test_the_declared_networks_are_the_set():
    assert management_network({2}, MANAGED, NotRead("tenant"), ["203.0.113.9"]) == Management(frozenset({2}))


def test_a_management_network_declared_with_no_network_is_refused():
    with pytest.raises(QumuloError) as failure:
        management_network(set(), MANAGED, NotRead("tenant"), ["192.0.2.12"])

    assert str(failure.value) == ("the management network is declared with no network; declare the id of the network this play reaches the cluster on")


def test_a_declared_network_outside_the_document_leaves_the_set_undetermined():
    assert management_network({1, 7}, MANAGED, NotRead("tenant"), []) == Management(frozenset({1}), OUTSIDE, (7,))


@pytest.mark.parametrize("address", ["192.0.2.12", "192.0.2.53"])
def test_a_node_or_floating_range_places_the_address_with_no_status(address):
    assert management_network(None, MANAGED, NotRead("tenant"), [address]) == Management(frozenset({1}))


def test_a_static_management_network_is_judged_with_status_unavailable_as_with_it_available():
    assert management_network(None, MANAGED, UNAVAILABLE, ["192.0.2.12"]) == Management(frozenset({1}))


def test_a_dual_stack_name_gives_two_networks():
    assert management_network(None, MANAGED, NotRead("tenant"), ["192.0.2.12", "2001:db8::13"]) == Management(frozenset({1, 3}))


def test_an_address_on_no_network_adds_nothing():
    found = management_network(None, MANAGED, EMPTY_STATUS, ["192.0.2.12", "203.0.113.9"])

    assert found == Management(frozenset({1}))


def test_status_places_a_dhcp_management_network():
    """ip_ranges holds only a STATIC network's ranges, so a DHCP node address is placed by status alone."""
    dhcp_document = document(dhcp(1, "Default", ["192.0.2.51"]))

    found = management_network(None, dhcp_document, status(entry(0, "192.0.2.30", assigned_by="DHCP")), ["192.0.2.30"])

    assert found == Management(frozenset({1}))


def test_status_places_a_host_address_on_network_0():
    """The VM edge's host associates network 0, a real id that must never read as unset."""
    assert management_network(None, HOSTED, HOST_STATUS, ["192.0.2.21"]) == Management(frozenset({0}))


def test_status_places_a_host_address_on_the_current_documents_host_network():
    """Status shows the document in force now; a second HOST network that the write adds does not hide which serves."""
    in_force = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)

    assert management_network(None, in_force, HOST_STATUS, ["192.0.2.21"], current=HOSTED) == Management(frozenset({0}))


def test_status_places_no_host_address_once_the_cluster_has_two_host_networks():
    """Status gives no network id beside the host's addresses, so it cannot tell which HOST network serves them."""
    two = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)

    assert management_network(None, two, HOST_STATUS, ["192.0.2.21"]) == Management(frozenset(), NO_NETWORK)


def test_a_host_networks_floating_address_with_status_available_is_that_network():
    assert management_network(None, HOSTED, HOST_STATUS, ["192.0.2.61"]) == Management(frozenset({0}))


def test_a_static_address_no_range_of_the_document_holds_is_on_no_network_whatever_its_network_id():
    found = management_network(None, document(static(2, "finance", ["198.51.100.11-14"])), status(entry(2, "192.0.2.30")), ["192.0.2.30"])

    assert found == Management(frozenset(), NO_NETWORK)


def test_no_address_leaves_the_set_undetermined():
    assert management_network(None, MANAGED, NotRead("tenant"), []) == Management(frozenset(), NO_ADDRESS)


def test_no_network_carrying_any_address_leaves_the_set_undetermined():
    assert management_network(None, MANAGED, EMPTY_STATUS, ["203.0.113.9"]) == Management(frozenset(), NO_NETWORK)


@pytest.mark.parametrize(
    ("document_in_force", "address"),
    [(MANAGED, "203.0.113.9"), (HOSTED, "192.0.2.21"), (HOSTED, "192.0.2.60")],
    ids=["on-no-range", "host-address", "host-floating-address"],
)
def test_status_unavailable_where_it_is_needed_leaves_the_set_undetermined(document_in_force, address):
    found = management_network(None, document_in_force, UNAVAILABLE, ["192.0.2.12", address])

    assert found.undetermined == STATUS_UNAVAILABLE


def test_status_not_read_where_it_is_needed_is_an_internal_error_naming_the_caller():
    with pytest.raises(QumuloError) as failure:
        management_network(None, MANAGED, NotRead("network_config"), ["203.0.113.9"])

    assert str(failure.value) == (
        "internal error: network_config did not read the cluster's network status, which the continuity rule needs to "
        "place 203.0.113.9; report this as a bug in the collection"
    )


@pytest.mark.parametrize(("count", "needs"), [(0, False), (1, False), (2, True), (3, True)])
def test_the_management_network_is_needed_at_two_tenants_or_more(count, needs):
    assert continuity.needed([f"tenant{i}" for i in range(count)]) is needs


# hosts_on_device: with status available, every HOST network of the set must show on a device: be on the cluster, with
# the host's addresses on a frontend device of some node


def test_a_host_network_on_a_device_passes():
    assert continuity.hosts_on_device(frozenset({0}), None, HOSTED, HOSTED, HOST_STATUS) == []


def test_a_host_network_on_no_device_is_returned_with_whether_the_current_document_has_it():
    planned = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)

    assert continuity.hosts_on_device(frozenset({0, 4}), {0, 4}, planned, HOSTED, EMPTY_STATUS) == [OffDevice(0, True), OffDevice(4, False)]


def test_a_host_network_the_run_adds_is_on_no_device_while_the_host_serves_another():
    planned = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)

    assert continuity.hosts_on_device(frozenset({0, 4}), {0, 4}, planned, HOSTED, HOST_STATUS) == [OffDevice(4, False)]


def test_each_host_network_on_the_cluster_shows_on_a_device_while_the_host_serves_one():
    """Status cannot tell which of two HOST networks the host's addresses belong to, so it shows neither on no device."""
    two = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)

    assert continuity.hosts_on_device(frozenset({0, 4}), None, two, two, HOST_STATUS) == []


@pytest.mark.parametrize(
    "unserved",
    [status(entry(0, "192.0.2.11")), status(host_addresses=["192.0.2.21"])],
    ids=["a STATIC entry", "the host's addresses alone"],
)
def test_a_host_network_is_on_no_device_when_no_frontend_device_lists_an_entry_assigned_by_host(unserved):
    """A host-managed node lists the host's addresses with no HOST network at all, as it does before the network applies."""
    assert continuity.hosts_on_device(frozenset({0}), {0}, HOSTED, HOSTED, unserved) == [OffDevice(0, True)]


def test_a_static_network_is_never_looked_for_on_a_device():
    assert continuity.hosts_on_device(frozenset({1}), None, MANAGED, MANAGED, EMPTY_STATUS) == []


@pytest.mark.parametrize("unread", [UNAVAILABLE, NotRead("tenant")], ids=["unavailable", "not-read"])
def test_an_undeclared_static_set_needs_no_status(unread):
    """api_host in a STATIC range: status_needed is false, so a module run alone has no status, and needs none."""
    assert continuity.status_needed(None, MANAGED, ["192.0.2.12"]) is False
    assert continuity.hosts_on_device(frozenset({1}), None, MANAGED, MANAGED, unread) == []


@pytest.mark.parametrize("unread", [UNAVAILABLE, NotRead("tenant")], ids=["unavailable", "not-read"])
def test_a_declared_host_network_is_trusted_when_status_is_not_there(unread):
    assert continuity.hosts_on_device(frozenset({0}), {0}, HOSTED, HOSTED, unread) == []


def test_an_undeclared_set_without_status_is_an_internal_error():
    """management_network has already refused it, so a caller that asks is wrong."""
    with pytest.raises(QumuloError, match="^internal error: "):
        continuity.hosts_on_device(frozenset({0}), None, HOSTED, HOSTED, UNAVAILABLE)


# rest_reachable: REST enabled by the cluster-side port (spec 014, "The continuity rule", Definitions)


def switches(rest, web):
    return {"rest_api_enabled": rest, "web_ui_enabled": web, "smb_enabled": True}


BINDINGS = {1: "Default", 2: "finance", 3: None}


@pytest.mark.parametrize(
    ("cluster_port", "api_port", "rest", "web", "breaches"),
    [
        (443, 8000, False, True, []),
        (443, 443, True, False, [Breach("finance", 2, ("web_ui_enabled",))]),
        (8000, 443, False, True, [Breach("finance", 2, ("rest_api_enabled",))]),
        (9000, 9000, False, True, [Breach("finance", 2, ("rest_api_enabled",))]),
        (None, 443, True, True, []),
        (None, 443, False, True, [Breach("finance", 2, ("rest_api_enabled",), fail_closed=True)]),
        (None, 443, True, False, [Breach("finance", 2, ("web_ui_enabled",), fail_closed=True)]),
        (None, 443, False, False, [Breach("finance", 2, ("rest_api_enabled", "web_ui_enabled"), fail_closed=True)]),
        (None, 8000, True, False, []),
        (None, 8000, False, True, [Breach("finance", 2, ("rest_api_enabled",))]),
    ],
    ids=[
        "declared-443-web-ui-on",
        "declared-443-web-ui-off",
        "declared-8000",
        "declared-other",
        "undeclared-443-both-on",
        "undeclared-443-rest-off",
        "undeclared-443-web-ui-off",
        "undeclared-443-both-off",
        "undeclared-8000-web-ui-off",
        "undeclared-8000-rest-off",
    ],
)
def test_rest_enabled_follows_the_cluster_side_port(cluster_port, api_port, rest, web, breaches):
    tenants = {"Default": switches(True, True), "finance": switches(rest, web)}

    assert continuity.rest_reachable(tenants, BINDINGS, frozenset({1, 2}), cluster_port, api_port) == breaches


def test_with_one_tenant_only_that_tenant_is_asked_and_no_network_is_looked_up():
    assert continuity.rest_reachable({"Default": switches(False, True)}, {}, frozenset(), None, 8000) == [Breach("Default", None, ("rest_api_enabled",))]
    assert continuity.rest_reachable({"Default": switches(True, False)}, {}, frozenset(), None, 8000) == []


def test_a_binding_to_a_tenant_the_caller_did_not_list_is_an_internal_error():
    with pytest.raises(QumuloError, match="^internal error: network binding names tenant eng, "):
        continuity.rest_reachable({"Default": switches(True, True), "finance": switches(True, True)}, {1: "eng"}, frozenset({1}), None, 8000)


@pytest.mark.parametrize(("items", "text"), [([], "none"), (["a"], "a"), (["a", "b"], "a and b"), (["a", "b", "c"], "a, b and c")])
def test_listed(items, text):
    assert continuity.listed(items) == text


def test_a_network_no_tenant_holds_fails():
    tenants = {"Default": switches(True, True), "finance": switches(True, True)}

    assert continuity.rest_reachable(tenants, BINDINGS, frozenset({1, 3}), None, 8000) == [Breach(None, 3)]


# refusal_message: each text of the spec's Messages, the first that applies in the spec's order


def refuse(**facts):
    base = {
        "write": "creating tenant finance",
        "in_role": True,
        "api_host": "cluster.example.com",
        "addresses": ["192.0.2.12"],
        "tenants": {"Default": switches(True, True), "finance": switches(True, True)},
        "bindings": BINDINGS,
        "management": Management(frozenset({1})),
        "current": MANAGED,
        "in_force": MANAGED,
        "removed": [],
        "off_device": [],
        "breaches": [],
    }
    return continuity.refusal_message(**{**base, **facts})


def test_no_refusal_when_the_management_network_stays_reachable():
    assert refuse() is None


def test_with_one_tenant_an_undetermined_network_is_never_refused():
    assert refuse(tenants={"Default": switches(True, True)}, management=Management(frozenset(), NO_NETWORK)) is None


MESSAGE_1 = "The cluster's networking is host-managed and it has no frontend network yet, so with a second tenant REST would listen on no network. "


@pytest.mark.parametrize(
    ("in_role", "text"),
    [
        (
            True,
            MESSAGE_1 + "List its HOST network under one tenant in qumulo_tenants and run once before adding the second "
            "tenant, and, if this cluster's network status is unavailable, set qumulo_management_network to that network's id.",
        ),
        (
            False,
            MESSAGE_1 + "Add the HOST network with network_config before creating the second tenant, and, if this "
            "cluster's network status is unavailable, pass its id as management_network.",
        ),
    ],
)
def test_message_1_a_host_managed_cluster_with_no_frontend_network(in_role, text):
    undeclared = refuse(in_role=in_role, current=EMPTY_HOSTED, in_force=EMPTY_HOSTED, management=Management(frozenset(), NO_NETWORK))
    declared = refuse(in_role=in_role, current=EMPTY_HOSTED, in_force=EMPTY_HOSTED, management=Management(frozenset(), OUTSIDE, (0,)))

    assert undeclared == declared == text


MESSAGE_2 = "This run removes network 1 (Default), which carries this play's connection. "
ROLE_2 = (
    "List it under the networks of a tenant in qumulo_tenants that is not state: absent, or set "
    "qumulo_allow_management_disruption: true and qumulo_api_host_after to the address the play uses after the run."
)
WITHOUT_1 = document(static(2, "finance", ["198.51.100.11-14"]), static(3, "eng", ["2001:db8::11-14"]))


@pytest.mark.parametrize(
    ("declared", "addresses", "removed"),
    [(None, ["192.0.2.12"], [1]), ({1}, [], [1]), (None, ["198.51.100.12"], []), ({2}, [], [])],
    ids=["found-by-range", "declared", "kept-by-range", "declared-kept"],
)
def test_removed_networks_are_those_of_the_current_set_the_document_in_force_lacks(declared, addresses, removed):
    assert continuity.removed_networks(declared, addresses, MANAGED, WITHOUT_1, UNAVAILABLE) == removed


def test_removed_networks_finds_a_host_network_by_its_floating_address():
    current = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)
    in_force = document(host(4, "finance"), managed=False)

    assert continuity.removed_networks(None, ["192.0.2.60"], current, in_force, UNAVAILABLE) == [0]


def test_removed_networks_places_an_address_by_status_on_the_current_document():
    in_force = document(static(2, "finance", ["198.51.100.11-14"]))
    current = document(dhcp(1, "Default"), static(2, "finance", ["198.51.100.11-14"]))

    assert continuity.removed_networks(None, ["192.0.2.30"], current, in_force, status(entry(0, "192.0.2.30", assigned_by="DHCP"))) == [1]


def test_removed_networks_finds_the_host_network_the_hosts_address_is_on():
    in_force = document(host(4, "finance"), managed=False)

    assert continuity.removed_networks(None, ["192.0.2.21"], HOSTED, in_force, HOST_STATUS) == [0]


def test_a_network_status_shows_but_neither_document_has_is_not_one_the_run_removes():
    assert continuity.removed_networks(None, ["203.0.113.9"], MANAGED, MANAGED, status(entry(9, "203.0.113.9"))) == []


@pytest.mark.parametrize(
    ("in_role", "management", "text"),
    [
        (True, Management(frozenset(), NO_NETWORK), MESSAGE_2 + ROLE_2),
        (True, Management(frozenset(), OUTSIDE, (1,)), MESSAGE_2 + ROLE_2),
        (False, Management(frozenset(), NO_NETWORK), MESSAGE_2 + "Keep it in the networks you pass, or set allow_management_disruption: true."),
    ],
    ids=["found-by-range", "declared", "module-alone"],
)
def test_message_2_the_run_removes_a_network_of_the_set(in_role, management, text):
    assert refuse(in_role=in_role, write="the network change", in_force=WITHOUT_1, management=management, removed=[1]) == text


def test_message_2_names_the_removed_network_from_the_current_document():
    current = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance"), managed=False)
    in_force = document(host(4, "finance"), managed=False)

    found = refuse(write="the network change", current=current, in_force=in_force, management=Management(frozenset(), NO_NETWORK), removed=[0])

    assert found.startswith("This run removes network 0 (Default), which carries this play's connection. ")


@pytest.mark.parametrize(
    ("in_role", "planned", "text"),
    [
        (True, None, "qumulo_management_network is 7, which is neither on the cluster nor in this inventory."),
        (False, None, "management_network is 7, which is not on the cluster."),
        (
            True,
            document(static(7, "finance-vlan", ["198.51.100.21-24"])),
            (
                "qumulo_management_network is 7 (finance-vlan), which is not on the cluster. Add network 7 (finance-vlan) "
                "first, in a run whose inventory does not yet list tenant finance."
            ),
        ),
    ],
    ids=["role", "module-alone", "added-by-the-run"],
)
def test_message_3_a_declared_network_the_document_lacks(in_role, planned, text):
    management = Management(frozenset(), OUTSIDE, (7,))

    assert refuse(in_role=in_role, management=management, planned=planned, tenant="finance") == text


@pytest.mark.parametrize(
    ("in_role", "text"),
    [
        (True, "Network 3 (eng) carries this play's connection but belongs to no tenant. List it under the networks of its tenant in qumulo_tenants."),
        (
            False,
            (
                "Network 3 (eng) carries this play's connection but belongs to no tenant. Pass it with its tenant to "
                "network_config, or run qq network_mod_network --network-id 3 --tenant-id <tenant id>."
            ),
        ),
    ],
)
def test_message_4_a_network_no_tenant_holds_comes_before_message_6(in_role, text):
    found = refuse(in_role=in_role, management=Management(frozenset({3})), breaches=[Breach(None, 3)], off_device=[OffDevice(3, True)])

    assert found == text


@pytest.mark.parametrize(
    ("undetermined", "addresses", "in_role", "text"),
    [
        (
            NO_ADDRESS,
            [],
            True,
            "cluster.example.com resolves to no address. Set qumulo_management_network to the id of the network this play reaches the cluster on.",
        ),
        (
            NO_ADDRESS,
            [],
            False,
            "cluster.example.com resolves to no address. Set management_network to the id of the network this play reaches the cluster on.",
        ),
        (
            NO_NETWORK,
            ["203.0.113.9", "2001:db8::9"],
            True,
            (
                "No network on the cluster carries cluster.example.com (203.0.113.9, 2001:db8::9). If this play reaches the "
                "cluster through NAT or a load balancer, set qumulo_management_network to the id of the network "
                "cluster.example.com is forwarded to."
            ),
        ),
        (
            NO_NETWORK,
            ["203.0.113.9"],
            False,
            (
                "No network on the cluster carries cluster.example.com (203.0.113.9). If this play reaches the cluster "
                "through NAT or a load balancer, set management_network to the id of the network cluster.example.com is "
                "forwarded to."
            ),
        ),
        (
            STATUS_UNAVAILABLE,
            ["192.0.2.21"],
            True,
            (
                "The cluster's network status is unavailable (G-NET-APPLY), so the collection cannot tell which network "
                "carries cluster.example.com (192.0.2.21), or whether a HOST network serves it. Set qumulo_management_network "
                "to the id of the network this play reaches the cluster on."
            ),
        ),
        (
            STATUS_UNAVAILABLE,
            ["192.0.2.21"],
            False,
            (
                "The cluster's network status is unavailable (G-NET-APPLY), so the collection cannot tell which network "
                "carries cluster.example.com (192.0.2.21), or whether a HOST network serves it. Set management_network to "
                "the id of the network this play reaches the cluster on."
            ),
        ),
    ],
    ids=["no-address", "no-address-alone", "no-network", "no-network-alone", "unavailable", "unavailable-alone"],
)
def test_message_5_an_undetermined_network(undetermined, addresses, in_role, text):
    management = Management(frozenset(), undetermined)

    assert refuse(in_role=in_role, addresses=addresses, current=HOSTED, in_force=HOSTED, management=management) == text


PLANNED_HOST = document(host(0, "Default", ["192.0.2.60-61"]), host(4, "finance-host"), managed=False)


@pytest.mark.parametrize(
    ("off", "in_role", "text"),
    [
        (
            OffDevice(4, False),
            True,
            (
                "HOST network 4 (finance-host) is not on the cluster yet, so it cannot carry this play's connection. Add it "
                "in a run that leaves qumulo_management_network on the network this play uses now, then set "
                "qumulo_management_network to 4 (finance-host) once status shows it on a device."
            ),
        ),
        (
            OffDevice(4, False),
            False,
            (
                "HOST network 4 (finance-host) is not on the cluster yet, so it cannot carry this play's connection. Add it "
                "in a run that leaves management_network on the network this play uses now, then set management_network to "
                "4 (finance-host) once status shows it on a device."
            ),
        ),
        (
            OffDevice(0, True),
            True,
            (
                "Status shows HOST network 0 (Default) on no device. A HOST network serves only on an interface the host "
                "associates with its id, and a node reads that association when it starts. If 0 (Default) was just created, "
                "run again once status shows it applied; otherwise set its id to the one the host associates with an interface."
            ),
        ),
    ],
    ids=["not-on-the-cluster-yet", "not-on-the-cluster-yet-alone", "on-no-device"],
)
def test_message_6_a_host_network_on_no_device(off, in_role, text):
    bindings = {0: "Default", 4: "finance"}

    found = refuse(in_role=in_role, current=HOSTED, in_force=PLANNED_HOST, bindings=bindings, management=Management(frozenset({off.network})), off_device=[off])

    assert found == text


def test_the_plain_breach_on_a_network():
    found = refuse(write="the network change", breaches=[Breach("finance", 2, ("rest_api_enabled",))], management=Management(frozenset({2})))

    assert found == (
        "The network change would leave network 2 (finance) in tenant finance with rest_api_enabled off, and this play "
        "would lose its connection. Set rest_api_enabled: true on finance, or list 2 (finance) under a tenant that has "
        "rest_api_enabled: true."
    )


def test_the_plain_breach_with_both_switches_off_gives_no_port():
    breach = Breach("finance", 2, ("rest_api_enabled", "web_ui_enabled"), fail_closed=True)

    found = refuse(write="the network change", breaches=[breach], management=Management(frozenset({2})))

    assert found == (
        "The network change would leave network 2 (finance) in tenant finance with rest_api_enabled and web_ui_enabled "
        "off, and this play would lose its connection. Set rest_api_enabled: true and web_ui_enabled: true on finance, "
        "or list 2 (finance) under a tenant that has rest_api_enabled: true and web_ui_enabled: true."
    )


def test_the_plain_breach_on_the_only_tenant():
    found = refuse(
        write="turning rest_api_enabled off on tenant Default",
        tenants={"Default": switches(False, True)},
        management=None,
        breaches=[Breach("Default", None, ("rest_api_enabled",))],
    )

    assert found == (
        "Turning rest_api_enabled off on tenant Default would leave tenant Default, the only tenant, with "
        "rest_api_enabled off, and this play would lose its connection. Set rest_api_enabled: true on Default."
    )


FAIL_CLOSED = (
    "Turning web_ui_enabled off on tenant Default would leave tenant Default, which holds this play's connection, with "
    "web_ui_enabled off. This play connects on port 443 and declares no cluster port, so the collection cannot tell "
    "whether it reaches the REST listener or the web UI listener, and needs both on. "
)


@pytest.mark.parametrize(
    ("in_role", "port_variable"),
    [(True, "qumulo_cluster_port"), (False, "cluster_port")],
)
def test_the_fail_closed_breach_gives_the_port_variable(in_role, port_variable):
    found = refuse(
        write="turning web_ui_enabled off on tenant Default",
        in_role=in_role,
        tenants={"Default": switches(True, False)},
        management=None,
        breaches=[Breach("Default", None, ("web_ui_enabled",), fail_closed=True)],
    )

    assert found == FAIL_CLOSED + (
        f"If nothing between this play and the cluster changes the port, set {port_variable}: 443; if a load balancer "
        f"maps 443 to another port, set {port_variable} to that port."
    )


@pytest.mark.parametrize(
    ("in_role", "text"),
    [
        (True, "This run would delete every tenant (Default and finance). Remove state: absent from one of them."),
        (False, "Deleting tenant Default would leave the cluster with no tenant."),
    ],
)
def test_a_write_that_leaves_no_tenant(in_role, text):
    found = refuse(write="deleting tenant Default", in_role=in_role, tenants={}, management=None, tenant="Default", entries=["Default", "finance"])

    assert found == text


# api_host_addresses: what api_host resolves to on the controller


def test_a_name_gives_its_addresses_once_each_ipv4_first(monkeypatch):
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:DB8::11", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.11", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::11", 0, 0, 0)),
    ]
    monkeypatch.setattr(continuity.socket, "getaddrinfo", lambda host, port, **kw: answers)

    assert continuity.api_host_addresses("cluster.example.com") == ["192.0.2.11", "2001:db8::11"]


def test_a_literal_address_is_its_own_address(monkeypatch):
    monkeypatch.setattr(continuity.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("a literal address needs no lookup"))

    assert continuity.api_host_addresses("2001:DB8:0::11") == ["2001:db8::11"]


def test_an_ipv6_address_in_brackets_is_its_own_address(monkeypatch):
    """The client takes an IPv6 api_host already in the brackets a URL needs."""
    monkeypatch.setattr(continuity.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("a literal address needs no lookup"))

    assert continuity.api_host_addresses("[2001:DB8::11]") == ["2001:db8::11"]


def test_a_name_that_does_not_resolve_gives_no_address(monkeypatch):
    def unresolved(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(continuity.socket, "getaddrinfo", unresolved)

    assert continuity.api_host_addresses("nowhere.example.com") == []
