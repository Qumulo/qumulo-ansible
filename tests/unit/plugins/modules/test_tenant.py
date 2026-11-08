# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.tenant, driven through FakeQumulo.

Tenant bodies follow /v1/multitenancy/tenants/ in the Qumulo Core 7.10.3 API documents and what spec 014's probe P1
and spike S2 recorded: the list is {entries: [...]}, unpaged, with no ETag; the item GET has one; a POST without
identity_config_id is refused. Network documents follow /v3/network.
"""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import tenant
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import ANY, CLUSTER_ID, NO_HEADER

TENANTS, NETWORK, STATUS = "/v1/multitenancy/tenants/", "/v3/network", "/v3/network/status"
SHARES, EXPORTS = "/v3/smb/shares/", "/v3/nfs/exports/"
SMB_OVERRIDES, NFS_OVERRIDES = "/v1/multitenancy/smb/settings/", "/v1/multitenancy/nfs/settings/"
PROTECTION = "/v1/cluster/protection/status"
SWITCHES = ("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "replication_enabled", "nfs_enabled", "smb_enabled")
# api_host is a node address of network 1, so the continuity rule places it with no status read.
CONNECTION = {"api_host": "10.20.0.5", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}


def item(tenant_id):
    return f"{TENANTS}{tenant_id}"


def entry(tenant_id, name, networks=(), identity_config_id=1, **switches):
    on = dict.fromkeys(SWITCHES, True)
    return {"id": tenant_id, "name": name, **on, **switches, "networks": list(networks), "identity_config_id": identity_config_id}


DEFAULT = entry(1, "Default", networks=[1])


def off(**on):
    """Every switch off but those given, as a tenant the module creates with them."""
    return {**dict.fromkeys(SWITCHES, False), **on}


def static(network_id, name, ip_ranges, tenant=None):
    block = {"default_gateway": "10.20.0.1", "ip_ranges": list(ip_ranges), "floating_ip_ranges": [], "netmask": "255.255.255.0"}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "STATIC", "static_addresses": block}}


def host(network_id, name, floating=(), tenant=None):
    block = {"floating_ip_ranges": list(floating), "netmask": None}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "HOST", "host_addresses": block}}


BOND = {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]}


def managed(*networks, vlans=(), backend=None):
    interfaces = {"frontend_bond_config": BOND, "frontend_vlans": list(vlans), "backend_bond_config": backend}
    return {"managed_interfaces": interfaces, "frontend_networks": list(networks)}


MANAGED = managed(static(1, "Default", ["10.20.0.5-6"], tenant=1))


def cluster(fake_api, *entries, document=None):
    """A cluster whose tenant list is read once per fetch, and whose network document (MANAGED by default) is there to read."""
    fake = fake_api()
    fake.expect("GET", TENANTS, response={"entries": list(entries)})
    fake.serve(NETWORK, MANAGED if document is None else document, optional=True)
    fake.serve(PROTECTION, {"protection_system_type": "PROTECTION_SYSTEM_TYPE_EC"}, optional=True)
    return fake


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "PATCH", "PUT", "DELETE") and e.path != "/v1/session/login"]


# Create, update and rename (spec FR-001 to FR-004)


def test_create_sends_every_switch_no_network_and_tenant_1s_identity_configuration(fake_api, run_module):
    """QUIRK-110: the server has no default identity configuration, so the create always sends one."""
    fake = cluster(fake_api, entry(1, "Default", networks=[1], identity_config_id=3))
    body = {"name": "finance", **off(nfs_enabled=True), "networks": [], "identity_config_id": 3}
    fake.expect("POST", TENANTS, body=body, response=entry(2, "finance", **off(nfs_enabled=True)))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "finance", **off(nfs_enabled=True))]})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "nfs_enabled": True})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["tenant"] == {"name": "finance", **off(nfs_enabled=True), "networks": []}


def test_an_existing_tenant_with_the_same_switches_sends_nothing(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT)

    result = run_module(tenant, {**CONNECTION, "name": "Default", "smb_enabled": True})

    assert result["changed"] is False, result.get("msg")
    assert result["tenant"] == {"name": "Default", **dict.fromkeys(SWITCHES, True), "networks": [1]}
    assert writes(fake) == []


def test_update_patches_only_the_keys_that_differ_with_if_match_and_never_networks(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance", smb_enabled=False))
    fake.expect("GET", item(2), response=entry(2, "finance", smb_enabled=False), etag='"t2"')
    fake.expect("PATCH", item(2), body={"smb_enabled": True}, if_match='"t2"', response=entry(2, "finance"))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "finance")]})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "smb_enabled": True, "nfs_enabled": True})

    assert result["changed"] is True, result.get("msg")
    assert result["tenant"]["smb_enabled"] is True


def test_a_switch_left_unset_keeps_the_tenants_value(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance", nfs_enabled=False))

    result = run_module(tenant, {**CONNECTION, "name": "finance"})

    assert result["changed"] is False, result.get("msg")
    assert result["tenant"]["nfs_enabled"] is False
    assert writes(fake) == []


def test_an_item_read_that_already_holds_the_change_writes_nothing(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance", smb_enabled=False))
    fake.expect("GET", item(2), response=entry(2, "finance"), etag='"t3"')
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "finance")]})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "smb_enabled": True})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_rename_patches_the_tenant_of_previous_name(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"))
    fake.expect("GET", item(2), response=entry(2, "finance"), etag='"t2"')
    fake.expect("PATCH", item(2), body={"name": "accounts"}, if_match='"t2"', response=entry(2, "accounts"))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "accounts")]})

    result = run_module(tenant, {**CONNECTION, "name": "accounts", "previous_name": "finance"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"]["name"] == "finance"
    assert result["diff"]["after"]["name"] == "accounts"


def test_previous_name_renames_nothing_once_the_name_exists(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"), entry(3, "accounts"))

    result = run_module(tenant, {**CONNECTION, "name": "accounts", "previous_name": "finance"})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_without_previous_name_a_new_name_is_a_create_not_a_rename(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"))
    fake.expect("POST", TENANTS, body=ANY, response=entry(3, "accounts"))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "finance"), entry(3, "accounts", **off())]})

    result = run_module(tenant, {**CONNECTION, "name": "accounts"})

    assert result["changed"] is True, result.get("msg")
    assert [e.method for e in writes(fake)] == ["POST"]


def test_a_lost_post_is_reconciled_by_reading_the_list_by_name(fake_api, run_module):
    created = entry(2, "finance", **off())
    fake = cluster(fake_api, DEFAULT)
    fake.expect("POST", TENANTS, raises=URLError(ConnectionResetError("reset by peer")))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, created]})

    result = run_module(tenant, {**CONNECTION, "name": "finance"})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]
    assert [e.method for e in writes(fake)] == ["POST"]


def test_the_patch_carries_no_if_match_only_when_the_tenant_gives_no_etag(fake_api, run_module):
    """Every tenant GET the probes made gave an ETag; without one, the PATCH goes without If-Match rather than fail."""
    fake = cluster(fake_api, DEFAULT, entry(2, "finance", smb_enabled=False))
    fake.expect("GET", item(2), response=entry(2, "finance", smb_enabled=False))
    fake.expect("PATCH", item(2), body={"smb_enabled": True}, if_match=NO_HEADER, response=entry(2, "finance"))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "finance")]})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "smb_enabled": True})

    assert result["changed"] is True, result.get("msg")


def test_the_cloud_platforms_refusal_says_tenant_writes_are_not_supported(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance", smb_enabled=False))
    fake.expect("GET", item(2), response=entry(2, "finance", smb_enabled=False), etag='"t2"')
    refusal = {"error_class": "multitenancy_api_platform_not_allowed_error", "description": "not allowed on this platform"}
    fake.expect("PATCH", item(2), status=403, response=refusal)

    result = run_module(tenant, {**CONNECTION, "name": "finance", "smb_enabled": True})

    assert result["failed"] is True
    assert result["msg"].startswith("this cluster refuses tenant writes (HTTP 403 multitenancy_api_platform_not_allowed_error)")
    assert result["error_class"] == "multitenancy_api_platform_not_allowed_error"


# Delete and its refusals (spec FR-005, FR-006)


def empty_content(fake, smb=(), nfs=()):
    fake.expect("GET", SHARES, response={"entries": []})
    fake.expect("GET", EXPORTS, response={"entries": []})
    fake.expect("GET", SMB_OVERRIDES, response=list(smb))
    fake.expect("GET", NFS_OVERRIDES, response=list(nfs))


def test_delete_is_satisfied_when_no_tenant_has_the_name(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT)

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert result["tenant"] == {}
    assert writes(fake) == []


def test_delete_sends_if_match_and_names_the_overrides_the_cascade_removed(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"))
    empty_content(fake, smb=[{"key": 2, "value": {"session_encryption": "REQUIRED"}}, {"key": 1, "value": {}}], nfs=[{"key": 1, "value": {}}])
    fake.expect("GET", item(2), response=entry(2, "finance"), etag='"t2"')
    fake.expect("DELETE", item(2), if_match='"t2"', status=200)

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["removed_overrides"] == ["smb"]
    assert result["diff"]["after"] == {}
    assert result["diff"]["before"]["name"] == "finance"


def test_a_lost_delete_is_reconciled_by_the_name_being_gone(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"))
    empty_content(fake)
    fake.expect("GET", item(2), response=entry(2, "finance"), etag='"t2"')
    fake.expect("DELETE", item(2), raises=URLError(ConnectionResetError("reset by peer")))
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT]})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]


def test_delete_refuses_the_last_tenant(fake_api, run_module):
    fake = cluster(fake_api, entry(1, "Default"))

    result = run_module(tenant, {**CONNECTION, "name": "Default", "state": "absent"})

    assert result["failed"] is True
    assert result["msg"] == "Deleting tenant Default would leave the cluster with no tenant."
    assert writes(fake) == []


def test_delete_refuses_a_tenant_that_holds_a_network_a_share_or_an_export(fake_api, run_module):
    fake = cluster(
        fake_api,
        DEFAULT,
        entry(2, "finance", networks=[2]),
        document=managed(static(1, "Default", ["10.20.0.5-6"], 1), static(2, "fin-vlan", ["10.30.0.5-6"], 2)),
    )
    fake.expect("GET", SHARES, response={"entries": [{"share_name": "Files", "tenant_id": 1}, {"share_name": "ledger", "tenant_id": 2}]})
    fake.expect("GET", EXPORTS, response={"entries": [{"export_path": "/", "tenant_id": 1}, {"export_path": "/ledger", "tenant_id": 2}]})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent"})

    assert result["failed"] is True
    assert result["msg"] == (
        "tenant finance still holds network 2 (fin-vlan), SMB share 'ledger' and NFS export '/ledger'. The cluster deletes "
        "a tenant's shares, exports and SMB and NFS overrides with it (G-TENANT-CASCADE), so the module deletes only a "
        "tenant that holds none: remove its shares and exports, and move its networks to another tenant with "
        "network_config, first"
    )
    assert writes(fake) == []


def test_in_check_mode_content_removed_judges_the_roles_planned_removals(fake_api, run_module):
    """The role removes the tenant's shares, exports and networks before step 20a, so check mode cannot see them gone."""
    fake = cluster(fake_api, DEFAULT, entry(2, "finance", networks=[2]))
    fake.expect("GET", SMB_OVERRIDES, response=[])
    fake.expect("GET", NFS_OVERRIDES, response=[])

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent", "content_removed": True}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


def test_content_removed_is_ignored_in_a_real_run(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"))
    fake.expect("GET", SHARES, response={"entries": [{"share_name": "ledger", "tenant_id": 2}]})
    fake.expect("GET", EXPORTS, response={"entries": []})

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent", "content_removed": True})

    assert result["failed"] is True
    assert "SMB share 'ledger'" in result["msg"]
    assert writes(fake) == []


BACKEND_BOND = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [3]}


@pytest.mark.parametrize("check_mode", [False, True])
def test_a_second_tenant_is_refused_while_a_tenant_holds_a_backend_network(fake_api, run_module, check_mode):
    document = managed(static(1, "Default", ["10.20.0.5-6"], 1), static(3, "backend", ["10.99.0.5-6"], 1), backend=BACKEND_BOND)
    fake = cluster(fake_api, entry(1, "Default", networks=[1, 3]), document=document)

    result = run_module(tenant, {**CONNECTION, "name": "finance"}, check_mode=check_mode)

    assert result["failed"] is True
    assert result["msg"] == (
        "creating tenant finance would make a second tenant while tenant Default holds network 3 (backend), which is not "
        "on the frontend bond, a frontend VLAN or a HOST network, and with two tenants such a network aborts every node "
        "(G-TENANT-BACKEND). Unbind it first with a tenant PATCH of networks that leaves it out (qq "
        "multitenancy_modify_tenant --network-id), since qq network_mod_network --clear-tenant-id puts it back in the "
        "only tenant"
    )
    assert writes(fake) == []


def test_a_vlan_or_host_network_does_not_hold_back_a_second_tenant(fake_api, run_module):
    vlan = {"vlan_id": 20, "mtu": None, "network_id": 2, "secondary_network_id": 4}
    document = managed(static(1, "Default", ["10.20.0.5-6"], 1), static(2, "v4", ["10.30.0.5-6"], 1), host(5, "host"), vlans=[vlan])
    fake = cluster(fake_api, entry(1, "Default", networks=[1, 2, 4, 5]), document=document)

    result = run_module(tenant, {**CONNECTION, "name": "finance"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


# The continuity rule (spec 014, "The continuity rule"; FR-010, FR-011)

TWO = managed(static(1, "Default", ["10.20.0.5-6"], 1), static(2, "fin-vlan", ["10.30.0.5-6"], 2))
FINANCE_HOLDS_2 = entry(2, "finance", networks=[2])
ON_FINANCE = {**CONNECTION, "api_host": "10.30.0.5"}  # a node address of network 2, which finance holds
ONE_TENANT_BREACH = (
    "Setting rest_api_enabled: false on tenant Default would leave tenant Default, the only tenant, with rest_api_enabled "
    "off, and this play would lose its connection. Set rest_api_enabled: true on Default."
)


def reads(fake, path):
    return [e for e in fake.log if e.method == "GET" and e.path == path]


def test_a_rest_disable_on_the_only_tenant_is_refused_without_reading_the_network(fake_api, run_module):
    """US1-27: with one tenant every service listens everywhere, so the rule asks only that tenant."""
    fake = cluster(fake_api, DEFAULT)

    result = run_module(tenant, {**CONNECTION, "name": "Default", "rest_api_enabled": False})

    assert result["failed"] is True
    assert result["msg"] == ONE_TENANT_BREACH
    assert writes(fake) == []
    assert reads(fake, NETWORK) == []


def test_allow_management_disruption_skips_the_rule(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT)
    fake.expect("GET", item(1), response=DEFAULT, etag='"t1"')
    fake.expect("PATCH", item(1), body={"rest_api_enabled": False}, response=entry(1, "Default", rest_api_enabled=False))
    fake.expect("GET", TENANTS, response={"entries": [entry(1, "Default", networks=[1], rest_api_enabled=False)]})

    result = run_module(tenant, {**CONNECTION, "name": "Default", "rest_api_enabled": False, "allow_management_disruption": True})

    assert result["changed"] is True, result.get("msg")


def test_a_web_ui_disable_at_port_8000_is_no_breach(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT)
    fake.expect("GET", item(1), response=DEFAULT, etag='"t1"')
    fake.expect("PATCH", item(1), body={"web_ui_enabled": False}, response=entry(1, "Default", web_ui_enabled=False))
    fake.expect("GET", TENANTS, response={"entries": [entry(1, "Default", networks=[1], web_ui_enabled=False)]})

    result = run_module(tenant, {**CONNECTION, "name": "Default", "web_ui_enabled": False})

    assert result["changed"] is True, result.get("msg")


def test_with_two_tenants_the_rest_disable_on_the_holder_of_the_management_network_is_refused(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)

    result = run_module(tenant, {**ON_FINANCE, "name": "finance", "rest_api_enabled": False})

    assert result["failed"] is True
    assert result["msg"] == (
        "Setting rest_api_enabled: false on tenant finance would leave network 2 (fin-vlan) in tenant finance with "
        "rest_api_enabled off, and this play would lose its connection. Set rest_api_enabled: true on finance, or list "
        "2 (fin-vlan) under a tenant that has rest_api_enabled: true."
    )
    assert writes(fake) == []


def test_with_two_tenants_a_rest_disable_on_a_tenant_without_the_management_network_passes(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)
    fake.expect("GET", item(1), response=DEFAULT, etag='"t1"')
    fake.expect("PATCH", item(1), body={"rest_api_enabled": False}, response=entry(1, "Default", rest_api_enabled=False))
    fake.expect("GET", TENANTS, response={"entries": [entry(1, "Default", networks=[1], rest_api_enabled=False), FINANCE_HOLDS_2]})

    result = run_module(tenant, {**ON_FINANCE, "name": "Default", "rest_api_enabled": False})

    assert result["changed"] is True, result.get("msg")
    assert reads(fake, STATUS) == []


BEHIND_NAT = {**CONNECTION, "api_host": "203.0.113.9"}  # on no network of the cluster


@pytest.mark.parametrize(
    ("args", "patch"),
    [({"name": "accounts", "previous_name": "finance"}, {"name": "accounts"}), ({"name": "finance", "smb_enabled": False}, {"smb_enabled": False})],
    ids=["rename", "smb-switch"],
)
def test_a_rename_or_another_switch_is_not_judged_behind_nat_on_two_tenants(fake_api, run_module, args, patch):
    """US1-28: neither can cut the play off, so neither reads the network or its status."""
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)
    fake.expect("GET", item(2), response=FINANCE_HOLDS_2, etag='"t2"')
    fake.expect("PATCH", item(2), body=patch, response=FINANCE_HOLDS_2)
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, args["name"], networks=[2], smb_enabled=patch.get("smb_enabled", True))]})

    result = run_module(tenant, {**BEHIND_NAT, **args})

    assert result["changed"] is True, result.get("msg")
    assert reads(fake, NETWORK) == [] and reads(fake, STATUS) == []


def test_behind_nat_with_management_network_the_declared_network_is_judged(fake_api, run_module):
    """US1-18: the declared network stands for the one api_host is forwarded to."""
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)

    result = run_module(tenant, {**BEHIND_NAT, "name": "finance", "rest_api_enabled": False, "management_network": [2]})

    assert result["failed"] is True
    assert result["msg"].startswith("Setting rest_api_enabled: false on tenant finance would leave network 2 (fin-vlan) in tenant finance")
    assert reads(fake, STATUS) == []


HOSTED = {"managed_interfaces": None, "frontend_networks": [host(0, "Default", tenant=1)]}
CONTAINER = {**CONNECTION, "api_host": "localhost"}  # the tier 1 lane's NAT: localhost resolves to loopback


@pytest.fixture
def loopback(monkeypatch):
    from ansible_collections.qumulo.cluster.plugins.module_utils import continuity

    answers = [(2, 1, 6, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(continuity.socket, "getaddrinfo", lambda *a, **kw: answers)


def test_us1_26_a_create_with_management_network_trusts_it_and_reads_no_status(fake_api, run_module, loopback):
    fake = cluster(fake_api, entry(1, "Default", networks=[0]), document=HOSTED)
    fake.expect("POST", TENANTS, body=ANY, response=entry(2, "finance", **off()))
    fake.expect("GET", TENANTS, response={"entries": [entry(1, "Default", networks=[0]), entry(2, "finance", **off())]})

    result = run_module(tenant, {**CONTAINER, "name": "finance", "management_network": [0]})

    assert result["changed"] is True, result.get("msg")
    assert reads(fake, STATUS) == []


def test_us1_26_without_management_network_status_unavailable_refuses_with_message_5(fake_api, run_module, loopback):
    fake = cluster(fake_api, entry(1, "Default", networks=[0]), document=HOSTED)
    fake.expect("GET", STATUS, status=500, response={"error_class": "net_read_error", "description": "decode_error"})

    result = run_module(tenant, {**CONTAINER, "name": "finance"})

    assert result["failed"] is True
    assert result["msg"] == (
        "The cluster's network status is unavailable (G-NET-APPLY), so the collection cannot tell which network carries "
        "localhost (127.0.0.1), or whether a HOST network serves it. Set management_network to the id of the network "
        "this play reaches the cluster on."
    )
    assert writes(fake) == []


def test_a_delete_from_two_tenants_to_one_is_judged_on_the_survivor_alone(fake_api, run_module):
    fake = cluster(fake_api, entry(1, "Default", networks=[1], rest_api_enabled=False), entry(2, "finance"), document=TWO)
    empty_content(fake)

    result = run_module(tenant, {**BEHIND_NAT, "name": "finance", "state": "absent"})

    assert result["failed"] is True
    assert result["msg"].startswith("Deleting tenant finance would leave tenant Default, the only tenant, with rest_api_enabled off")
    assert writes(fake) == [] and reads(fake, NETWORK) == []


@pytest.mark.parametrize(
    ("cluster_port", "switch", "refused"),
    [(443, "rest_api_enabled", False), (443, "web_ui_enabled", True), (8000, "rest_api_enabled", True), (8000, "web_ui_enabled", False)],
)
def test_cluster_port_picks_the_switch_rest_enabled_means(fake_api, run_module, cluster_port, switch, refused):
    """A load balancer maps the play's port 443: declared 443 the play reaches REST through the web UI listener."""
    fake = cluster(fake_api, DEFAULT)
    if not refused:
        fake.expect("GET", item(1), response=DEFAULT, etag='"t1"')
        fake.expect("PATCH", item(1), body={switch: False}, response=entry(1, "Default", **{switch: False}))
        fake.expect("GET", TENANTS, response={"entries": [entry(1, "Default", networks=[1], **{switch: False})]})

    result = run_module(tenant, {**CONNECTION, "api_port": 443, "cluster_port": cluster_port, "name": "Default", switch: False})

    assert result.get("failed", False) is refused, result.get("msg")


@pytest.mark.parametrize("switch", ["rest_api_enabled", "web_ui_enabled"])
def test_at_client_port_443_with_no_cluster_port_either_disable_fails_closed(fake_api, run_module, switch):
    fake = cluster(fake_api, DEFAULT)

    result = run_module(tenant, {**CONNECTION, "api_port": 443, "name": "Default", switch: False})

    assert result["failed"] is True
    assert result["msg"].startswith(
        f"Setting {switch}: false on tenant Default would leave tenant Default, which holds this play's connection, with {switch} off."
    )
    assert "set cluster_port: 443" in result["msg"]
    assert writes(fake) == []


def test_in_check_mode_the_planned_bindings_and_tenants_are_judged(fake_api, run_module):
    """US1-6: the role moves network 2 to eng at step 3 before finance's REST disable at 3b."""
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)
    planned = {"planned_networks": {"1": "Default", "2": "eng"}, "planned_tenants": {"Default": True, "eng": True, "finance": False}}

    result = run_module(tenant, {**ON_FINANCE, "name": "finance", "rest_api_enabled": False, **planned}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


def test_in_check_mode_without_the_planned_inputs_the_cluster_is_judged(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)

    result = run_module(tenant, {**ON_FINANCE, "name": "finance", "rest_api_enabled": False}, check_mode=True)

    assert result["failed"] is True
    assert writes(fake) == []


def test_the_planned_inputs_are_ignored_in_a_real_run(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)
    planned = {"planned_networks": {"2": "eng"}, "planned_tenants": {"Default": True, "eng": True, "finance": False}}

    result = run_module(tenant, {**ON_FINANCE, "name": "finance", "rest_api_enabled": False, **planned})

    assert result["failed"] is True
    assert writes(fake) == []


# US1-20: step 3 moves the play's connection onto network 4, which only the planned document holds, so in a role check
# run the writes after it at 3b and 20a are told management_network 4 while the cluster still lacks it.
RETIRED = entry(3, "retired")
AFTER_THE_MOVE = {"1": "Default", "2": "finance", "4": "Default"}


@pytest.mark.parametrize(
    ("args", "tenants"),
    [
        pytest.param({"name": "retired", "state": "absent", "content_removed": True}, {"Default": True, "finance": True}, id="the 20a delete"),
        pytest.param({"name": "finance", "rest_api_enabled": False}, {"Default": True, "finance": False, "retired": True}, id="a 3b disable"),
    ],
)
def test_in_check_mode_a_management_network_the_planned_networks_bind_is_on_the_planned_document(fake_api, run_module, args, tenants):
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, RETIRED, document=TWO)
    if args.get("state") == "absent":
        fake.expect("GET", SMB_OVERRIDES, response=[])
        fake.expect("GET", NFS_OVERRIDES, response=[])
    planned = {"management_network": [4], "planned_networks": AFTER_THE_MOVE, "planned_tenants": tenants}

    result = run_module(tenant, {**CONNECTION, **args, **planned}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [] and reads(fake, STATUS) == []


def test_in_check_mode_a_management_network_the_planned_networks_lack_is_still_refused(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, RETIRED, document=TWO)
    fake.expect("GET", SMB_OVERRIDES, response=[])
    fake.expect("GET", NFS_OVERRIDES, response=[])
    planned = {"management_network": [5], "planned_networks": AFTER_THE_MOVE, "planned_tenants": {"Default": True, "finance": True}}

    result = run_module(tenant, {**CONNECTION, "name": "retired", "state": "absent", "content_removed": True, **planned}, check_mode=True)

    assert result["failed"] is True
    assert result["msg"] == "management_network is 5, which is not on the cluster."


# Warnings and diff (spec FR-007, FR-008, FR-040)

LISTENER = (
    "creating tenant finance changes the cluster from 1 tenant to 2: every listener on every node rebinds and every NFS "
    "and replication connection ends (G-TENANT-LISTEN), and it removes the static routes of every node (G-TENANT-ROUTES)"
)


@pytest.mark.parametrize("check_mode", [False, True])
def test_the_first_create_of_a_second_tenant_warns_in_the_run_and_in_check_mode(fake_api, run_module, check_mode):
    fake = cluster(fake_api, DEFAULT)
    if not check_mode:
        fake.expect("POST", TENANTS, body=ANY, response=entry(2, "finance", **off()))
        fake.expect("GET", TENANTS, response={"entries": [DEFAULT, entry(2, "finance", **off())]})

    result = run_module(tenant, {**CONNECTION, "name": "finance"}, check_mode=check_mode)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == [LISTENER]


def test_the_delete_that_leaves_one_tenant_warns(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "finance"))
    empty_content(fake)

    result = run_module(tenant, {**CONNECTION, "name": "finance", "state": "absent"}, check_mode=True)

    assert result["warnings"] == [
        (
            "deleting tenant finance changes the cluster from 2 tenants to 1: every listener on every node rebinds and every "
            "NFS and replication connection ends (G-TENANT-LISTEN)"
        )
    ]


def test_a_third_tenant_warns_only_of_the_static_routes(fake_api, run_module):
    """Qumulo Core's documentation: creating a tenant removes the static routes of every node."""
    cluster(fake_api, DEFAULT, entry(2, "finance"))

    result = run_module(tenant, {**CONNECTION, "name": "eng"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == ["creating tenant eng removes the static routes of every node (G-TENANT-ROUTES)"]


def test_the_create_diff_shows_an_empty_before_and_no_networks_after(fake_api, run_module):
    cluster(fake_api, DEFAULT, entry(2, "eng"))

    result = run_module(tenant, {**CONNECTION, "name": "finance", "nfs_enabled": True}, check_mode=True, diff=True)

    assert result["diff"] == {"before": {}, "after": {"name": "finance", **off(nfs_enabled=True), "networks": []}}


def test_a_change_diff_shows_the_networks_read_only_on_both_sides(fake_api, run_module):
    cluster(fake_api, DEFAULT, entry(2, "finance", networks=[2], smb_enabled=False))

    result = run_module(tenant, {**CONNECTION, "name": "finance", "smb_enabled": True}, check_mode=True, diff=True)

    assert result["diff"]["before"]["networks"] == result["diff"]["after"]["networks"] == [2]
    assert (result["diff"]["before"]["smb_enabled"], result["diff"]["after"]["smb_enabled"]) == (False, True)


@pytest.mark.parametrize("kind", ["PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "PROTECTION_SYSTEM_TYPE_COLD_OBJECT_BACKED"])
def test_check_mode_predicts_the_change_on_cloud_and_warns_that_the_run_will_be_refused(fake_api, run_module, kind):
    """FR-007: on cloud a tenant write is refused, but check mode predicts it as on any cluster."""
    fake = fake_api()
    fake.expect("GET", TENANTS, response={"entries": [entry(1, "Default", smb_enabled=False)]})
    fake.expect("GET", PROTECTION, response={"protection_system_type": kind})

    result = run_module(tenant, {**CONNECTION, "name": "Default", "smb_enabled": True}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == [
        (
            f"this cluster's protection type, {kind}, marks a cloud cluster, and Qumulo Core refuses every tenant write on a "
            "cloud platform: the run fails where this check predicts a change"
        )
    ]


def test_check_mode_reads_no_protection_type_when_nothing_would_change(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT)

    result = run_module(tenant, {**CONNECTION, "name": "Default"}, check_mode=True)

    assert result["changed"] is False
    assert reads(fake, PROTECTION) == []


# PR 234's walkthrough


def test_planned_networks_and_planned_tenants_come_together(fake_api, run_module):
    result = run_module(tenant, {**CONNECTION, "name": "finance", "planned_networks": {"2": "finance"}}, check_mode=True)

    assert result["failed"] is True
    assert "parameters are required together: planned_networks, planned_tenants" in result["msg"]


def test_a_planned_tenant_whose_rest_is_the_text_false_is_judged_off(fake_api, run_module):
    """The role templates the planned values, so a value can arrive as text."""
    fake = cluster(fake_api, DEFAULT, FINANCE_HOLDS_2, document=TWO)
    planned = {"planned_networks": {"1": "Default", "2": "finance"}, "planned_tenants": {"Default": "true", "finance": "false"}}

    result = run_module(tenant, {**ON_FINANCE, "name": "finance", "smb_enabled": True, "rest_api_enabled": False, **planned}, check_mode=True)

    assert result["failed"] is True
    assert result["msg"].startswith("Setting rest_api_enabled: false on tenant finance would leave network 2 (fin-vlan)")
    assert writes(fake) == []


def test_a_create_the_cloud_refuses_warns_of_nothing_it_did_not_do(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT, entry(2, "eng"))
    refusal = {"error_class": "multitenancy_api_platform_not_allowed_error", "description": "not allowed on this platform"}
    fake.expect("POST", TENANTS, status=403, response=refusal)

    result = run_module(tenant, {**CONNECTION, "name": "finance"})

    assert result["failed"] is True
    assert result["warnings"] == []


def test_a_create_from_one_tenant_reads_the_network_document_once(fake_api, run_module):
    fake = cluster(fake_api, DEFAULT)

    result = run_module(tenant, {**CONNECTION, "name": "finance"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert len(reads(fake, NETWORK)) == 1
