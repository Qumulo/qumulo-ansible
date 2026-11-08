# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.network_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import dns_lookup_overrides, network_config, network_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HOST_NETWORK = {
    "id": 1,
    "name": "Default",
    "tenant_id": 1,
    "addresses": {"type": "HOST", "host_addresses": {"floating_ip_ranges": ["10.20.0.50-51"], "netmask": "255.255.255.0"}},
}
BOND = {"id": 1, "name": "bond0", "bonding_mode": "ACTIVE_BACKUP", "default_gateway": "10.20.0.1", "default_gateway_ipv6": "", "mtu": 1500}
V2_NETWORK = {
    "id": 1,
    "name": "Default",
    "assigned_by": "STATIC",
    "ip_ranges": ["10.20.0.5-6"],
    "floating_ip_ranges": ["10.20.0.50-51"],
    "netmask": "255.255.255.0",
    "dns_servers": ["10.0.0.2"],
    "dns_search_domains": [],
    "mtu": 1500,
    "vlan_id": 0,
    "tenant_id": 1,
}
OVERRIDES = [{"ip_address": "2001:db8::20", "aliases": ["dr01"]}, {"ip_address": "10.0.0.10", "aliases": ["dc1"]}]
V1_NOT_SUPPORTED = {"error_class": "net_config_v1_not_supported_error", "description": "V1 API is not compatible with Host-Managed Networking"}
TENANTS = {"entries": [{"id": 1, "name": "Default", "rest_api_enabled": True, "web_ui_enabled": True, "networks": [1], "identity_config_id": 1}]}
STATUS_500 = {"error_class": "net_read_error", "description": "decode_error: EOF while parsing a value"}
STATE = {
    "/v3/network": {"frontend_networks": [HOST_NETWORK], "managed_interfaces": None},
    "/v1/multitenancy/tenants/": TENANTS,
    "/v2/network/interfaces/": [BOND],
    "/v2/network/interfaces/1/networks/": [V2_NETWORK],
    # One table: the system DNS config carries the lookup overrides the override route returns.
    "/v1/dns/configs/system": {"id": 1, "dns_servers": ["2001:DB8::0053"], "dns_search_domains": ["corp.example.com"], "lookup_overrides": OVERRIDES},
    "/v1/dns/lookup-override-config": {"lookup_overrides": OVERRIDES},
    "/v1/authoritative-dns/settings": {"enabled": True, "fqdn": "edge01.corp.example.com.", "host_restrictions": ["10.0.0.0-255"], "record_ttl_seconds": 0},
}


def serve(fake_api, legacy=None, optional=False):
    """A fake serving STATE and the single-network settings; optional when a test gathers only some subsets."""
    fake = fake_api()
    for path, response in STATE.items():
        fake.serve(path, response, optional=optional)
    if legacy is None:
        fake.serve("/v1/network/settings", V1_NOT_SUPPORTED, status=501, optional=optional)
    else:
        fake.serve("/v1/network/settings", legacy, optional=optional)
    # As on the container, the status answers 500 (G-NET-APPLY).
    fake.serve("/v3/network/status", STATUS_500, status=500, optional=optional)
    return fake


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_every_subset_in_its_modules_shape(fake_api, run_module):
    fake = serve(fake_api)

    result = run_module(network_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    facts = result["network"]
    assert facts["document"] == {
        "frontend_networks": [
            {
                "id": 1,
                "name": "Default",
                "type": "HOST",
                "tenant_id": 1,
                "tenant": "Default",
                "netmask": "255.255.255.0",
                "default_gateway": None,
                "ip_ranges": [],
                "floating_ip_ranges": ["10.20.0.50-10.20.0.51"],
            }
        ],
        "managed_interfaces": None,
    }
    assert facts["interfaces"] == [{**BOND, "networks": [V2_NETWORK]}]
    assert facts["dns"] == {"servers": ["2001:db8::53"], "search_domains": ["corp.example.com"]}
    assert facts["dns_lookup_overrides"] == {
        "overrides": [{"ip_address": "10.0.0.10", "aliases": ["dc1"]}, {"ip_address": "2001:db8::20", "aliases": ["dr01"]}]
    }
    assert facts["authoritative_dns"] == {
        "enabled": True,
        "fqdn": "edge01.corp.example.com.",
        "host_restrictions": ["10.0.0.0-10.0.0.255"],
        "record_ttl_seconds": 0,
    }
    assert "resolved_names" not in facts and "resolved_addresses" not in facts
    assert writes(fake) == []


def test_the_single_network_settings_the_cluster_refuses_are_unavailable(fake_api, run_module):
    """The deprecated /v1/network/settings answers 501 on host-managed networking, more than one network or a VLAN."""
    serve(fake_api)

    result = run_module(network_info, CONNECTION)

    assert "legacy_settings" not in result["network"]
    assert list(result["unavailable_subsets"]) == ["legacy_settings", "status", "api_host_network"]
    assert "net_config_v1_not_supported_error" in result["unavailable_subsets"]["legacy_settings"]


def test_the_single_network_settings_as_the_cluster_returns_them(fake_api, run_module):
    legacy = {"assigned_by": "STATIC", "ip_ranges": ["10.20.0.5-6"], "floating_ip_ranges": [], "netmask": "255.255.255.0", "gateway": "10.20.0.1"}
    serve(fake_api, legacy=legacy, optional=True)

    result = run_module(network_info, {**CONNECTION, "gather_subset": ["legacy_settings"]})

    assert result["network"] == {"legacy_settings": legacy}
    assert "unavailable_subsets" not in result


def test_a_host_managed_cluster_lists_no_interfaces(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v2/network/interfaces/", [])

    result = run_module(network_info, {**CONNECTION, "gather_subset": ["interfaces"]})

    assert result["network"] == {"interfaces": []}


def test_the_lookup_overrides_feed_back_to_their_module_unchanged(fake_api, run_module):
    serve(fake_api, optional=True)
    facts = run_module(network_info, {**CONNECTION, "gather_subset": ["dns_lookup_overrides"]})["network"]

    result = run_module(dns_lookup_overrides, {**CONNECTION, **facts["dns_lookup_overrides"]}, check_mode=True)

    assert result["changed"] is False, result.get("diff") or result.get("msg")


def test_lookups_ask_the_clusters_resolver_whatever_the_subsets(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v1/dns/configs/system", STATE["/v1/dns/configs/system"])
    names = [{"hostname": "dc1.corp.example.com", "ip_addresses": ["10.0.0.10"], "result": "OK"}]
    addresses = [{"ip_address": "10.0.0.11", "hostname": "", "result": "NOT_FOUND"}]
    fake.expect("POST", "/v1/dns/resolve-names-to-ips", body=["dc1.corp.example.com"], response=names)
    fake.expect("POST", "/v1/dns/resolve-ips-to-names", body=["10.0.0.11"], response=addresses)

    result = run_module(
        network_info, {**CONNECTION, "gather_subset": ["dns"], "resolve_names": ["dc1.corp.example.com"], "resolve_addresses": ["10.0.0.11"]}, check_mode=True
    )

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["network"]["resolved_names"] == names
    assert result["network"]["resolved_addresses"] == addresses
    assert set(result["network"]) == {"dns", "resolved_names", "resolved_addresses"}


def test_a_lookup_answered_503_is_sent_again(fake_api, run_module):
    """A lookup writes nothing, so the route is replayable."""
    fake = fake_api()
    fake.serve("/v1/dns/configs/system", STATE["/v1/dns/configs/system"])
    fake.expect("POST", "/v1/dns/resolve-names-to-ips", body=["dc1"], status=503, response={"error_class": "service_unavailable", "description": "busy"})
    fake.expect("POST", "/v1/dns/resolve-names-to-ips", body=["dc1"], response=[{"hostname": "dc1", "ip_addresses": [], "result": "TIMEOUT"}])

    result = run_module(network_info, {**CONNECTION, "gather_subset": ["dns"], "resolve_names": ["dc1"]})

    assert not result.get("failed"), result.get("msg")
    assert result["network"]["resolved_names"][0]["result"] == "TIMEOUT"


def test_a_static_document_feeds_back_to_network_config_unchanged(fake_api, run_module):
    """The document subset is network_config's shape, for a STATIC network too, which the container cannot show."""
    static = {
        "id": 1,
        "name": "Default",
        "tenant_id": 1,
        "addresses": {
            "type": "STATIC",
            "static_addresses": {
                "default_gateway": "10.20.0.1",
                "ip_ranges": ["10.20.0.5-6"],
                "floating_ip_ranges": ["10.20.0.50-51"],
                "netmask": "255.255.255.0",
            },
        },
    }
    fake = fake_api()
    fake.serve("/v3/network", {"frontend_networks": [static], "managed_interfaces": None})
    fake.serve("/v1/multitenancy/tenants/", TENANTS)
    facts = run_module(network_info, {**CONNECTION, "gather_subset": ["document"]})["network"]

    result = run_module(network_config, {**CONNECTION, **{k: v for k, v in facts["document"].items() if v is not None}}, check_mode=True)

    assert result["changed"] is False, result.get("diff") or result.get("msg")
    assert writes(fake) == []


def test_an_unconfigured_node_has_no_network_to_read(fake_api, run_module):
    fake = fake_api(cluster_id="", state="UNCONFIGURED")

    result = run_module(network_info, {**CONNECTION, "expected_cluster_uuid": "", "resolve_names": ["dc1"]})

    assert result["network"] == {}, result.get("msg")
    assert sorted(result["unavailable_subsets"]) == [
        "api_host_network",
        "authoritative_dns",
        "dns",
        "dns_lookup_overrides",
        "document",
        "interfaces",
        "legacy_settings",
        "resolved_names",
        "status",
    ]
    assert any("UNCONFIGURED" in w for w in result["warnings"])
    assert [e for e in fake.log if not e.background] == []


# The status and api_host_network subsets (spec 014, plan "network_info (extended)"). The bodies are in the shape Qumulo
# Core 7.11.0 answers (qumulo-findings item 37), written by hand with documentation addresses.

# A VM edge whose host manages its network, with no frontend network: the host's addresses on its interface, no
# qumulo_ip_addresses entry, and the loopback with addresses tied to no network.
STATUS = [
    {
        "node_id": 1,
        "node_name": "node1",
        "network_statuses": {},
        "managed_interface_statuses": [],
        "devices": [
            {
                "name": "enp1s0",
                "network_details": {
                    "host_routable_ip_addresses": ["192.0.2.21", "2001:db8::21"],
                    "qumulo_ip_addresses": [],
                    "use_for": "FRONTEND_AND_BACKEND",
                },
            },
            {"name": "lo", "network_details": {"addresses": ["127.0.0.1", "::1"], "use_for": "NONE"}},
        ],
    }
]
HOSTED = {"frontend_networks": [{**HOST_NETWORK, "id": 0}], "managed_interfaces": None}
# The same edge with HOST network 0 declared: its interface lists that network's entry, and network_statuses names it.
HOST_ENTRY = {"address": "", "assigned_by": "HOST", "floating_addresses": [], "gateway": None, "netmask": "", "network_id": 0}
HOSTED_STATUS = [
    {
        **STATUS[0],
        "network_statuses": {"0": {"details": "", "name": "Default", "status": "APPLIED"}},
        "devices": [
            {"name": "enp1s0", "network_details": {**STATUS[0]["devices"][0]["network_details"], "qumulo_ip_addresses": [HOST_ENTRY]}},
            STATUS[0]["devices"][1],
        ],
    }
]


def status_reads(fake):
    return [e for e in fake.log if e.path == "/v3/network/status"]


def test_the_status_subset_is_the_status_answer(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network/status", STATUS)

    result = run_module(network_info, {**CONNECTION, "gather_subset": ["status"]})

    assert result["network"] == {"status": STATUS}, result.get("msg")


def test_api_host_network_is_the_host_network_that_carries_the_hosts_address_and_reuses_the_status_read(fake_api, run_module):
    """Network 0 is a real id: the HOST network the VM edge's host associates with its interface."""
    fake = fake_api()
    fake.serve("/v3/network/status", HOSTED_STATUS)
    fake.serve("/v3/network", HOSTED)

    result = run_module(network_info, {**CONNECTION, "api_host": "192.0.2.21", "gather_subset": ["status", "api_host_network"]})

    assert result["network"]["api_host_network"] == 0, result.get("msg")
    assert len(status_reads(fake)) == 1


def test_api_host_network_is_the_documents_id_of_the_static_network_whose_range_holds_api_host(fake_api, run_module):
    """On 7.11.0 hardware the untagged network the document holds as 1 shows in the status as network_id 0."""
    static = {"default_gateway": "192.0.2.1", "ip_ranges": ["192.0.2.11-14"], "floating_ip_ranges": ["192.0.2.51-54"], "netmask": "255.255.255.0"}
    bond = {"frontend_bond_config": {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 1500, "networks": [1]}, "frontend_vlans": []}
    entry = {
        "address": "192.0.2.11",
        "assigned_by": "STATIC",
        "floating_addresses": ["192.0.2.51"],
        "gateway": None,
        "netmask": "255.255.255.0",
        "network_id": 0,
    }
    status = [
        {
            **STATUS[0],
            "devices": [
                {"name": "bond0", "network_details": {"host_routable_ip_addresses": [], "qumulo_ip_addresses": [entry], "use_for": "FRONTEND_AND_BACKEND"}}
            ],
        }
    ]
    fake = fake_api()
    fake.serve("/v3/network/status", status)
    fake.serve(
        "/v3/network",
        {
            "frontend_networks": [{"id": 1, "name": "Default", "tenant_id": 1, "addresses": {"type": "STATIC", "static_addresses": static}}],
            "managed_interfaces": bond,
        },
    )

    result = run_module(network_info, {**CONNECTION, "api_host": "192.0.2.51", "gather_subset": ["api_host_network"]})

    assert result["network"]["api_host_network"] == 1, result.get("msg")


def test_a_status_500_leaves_both_subsets_unavailable_with_its_reason_after_one_read(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network/status", STATUS_500, status=500)

    result = run_module(network_info, {**CONNECTION, "api_host": "192.0.2.21", "gather_subset": ["status", "api_host_network"]})

    assert result["network"] == {}, result.get("msg")
    assert result["unavailable_subsets"]["status"] == result["unavailable_subsets"]["api_host_network"]
    assert "HTTP 500 net_read_error" in result["unavailable_subsets"]["status"]
    assert len(status_reads(fake)) == 1


def test_api_host_network_is_unavailable_for_a_name_or_an_address_no_network_carries(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network/status", STATUS)
    fake.serve("/v3/network", HOSTED)

    by_name = run_module(network_info, {**CONNECTION, "gather_subset": ["api_host_network"]})
    behind_nat = run_module(network_info, {**CONNECTION, "api_host": "203.0.113.9", "gather_subset": ["api_host_network"]})

    assert by_name["unavailable_subsets"]["api_host_network"].startswith("api_host qumulo.example.com is a name")
    assert behind_nat["unavailable_subsets"]["api_host_network"] == (
        "no network of the cluster carries 203.0.113.9, as when the play reaches it through NAT or a load balancer"
    )


def test_api_host_network_says_so_when_the_status_shows_the_address_but_two_host_networks_could_carry_it(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network/status", HOSTED_STATUS)
    fake.serve("/v3/network", {"frontend_networks": [{**HOST_NETWORK, "id": 0}, {**HOST_NETWORK, "id": 3, "name": "finance"}], "managed_interfaces": None})

    result = run_module(network_info, {**CONNECTION, "api_host": "192.0.2.21", "gather_subset": ["api_host_network"]})

    assert result["unavailable_subsets"]["api_host_network"] == (
        "no network of the cluster carries 192.0.2.21 by the status: it shows the address as HOST, and the network document "
        "has 2 HOST networks, which the status does not tell apart"
    )


def test_the_document_and_api_host_network_read_the_document_once(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network/status", HOSTED_STATUS)
    fake.serve("/v3/network", HOSTED)
    fake.serve("/v1/multitenancy/tenants/", TENANTS)

    result = run_module(network_info, {**CONNECTION, "api_host": "192.0.2.21", "gather_subset": ["document", "api_host_network"]})

    assert result["network"]["api_host_network"] == 0, result.get("msg")
    assert len([e for e in fake.log if e.path == "/v3/network"]) == 1


def test_api_host_network_says_so_when_the_cluster_has_no_frontend_network_yet(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network/status", STATUS)
    fake.serve("/v3/network", {"frontend_networks": [], "managed_interfaces": None})

    result = run_module(network_info, {**CONNECTION, "api_host": "192.0.2.21", "gather_subset": ["api_host_network"]})

    assert result["unavailable_subsets"]["api_host_network"] == "no network of the cluster carries 192.0.2.21: the cluster has no frontend network yet"


def test_a_document_read_by_a_user_without_the_tenant_privilege_names_no_tenant_and_says_why(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v3/network", {"frontend_networks": [HOST_NETWORK], "managed_interfaces": None})
    fake.serve("/v1/multitenancy/tenants/", {"error_class": "permission_denied_error", "description": "no"}, status=403)

    result = run_module(network_info, {**CONNECTION, "gather_subset": ["document"]})

    assert result["network"]["document"]["frontend_networks"][0]["tenant"] is None, result.get("msg")
    assert result["warnings"] == ["the document's tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ (permission_denied_error)"]
