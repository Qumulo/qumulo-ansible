# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.tenancy_plan: the cluster_baseline role's step 2 for spec 014, run with no cluster.

The cluster option holds what step 2 reads. The tenant bodies follow tenant_info's result, identity_config_id
included; the network documents follow network_info's document subset, built here from /v3/network wire shapes; status
follows /v3/network/status, as test_continuity.py says.
"""

from __future__ import annotations

import json
import re

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import continuity, network, tenancy
from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy_plan as planning
from ansible_collections.qumulo.cluster.plugins.modules import network_config, tenancy_plan
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import CORP as CAPTURED_CORP
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import FINANCE as CAPTURED_FINANCE
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import NET1 as CAPTURED_NET1
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import NET2 as CAPTURED_NET2
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import NET3 as CAPTURED_NET3
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import TENANTS_LEFT_OUT, VLANS, listed_tenant, wire_document
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import TWO as CAPTURED_TWO

SWITCHES = tenancy.SWITCHES
API_HOST = "192.0.2.11"  # a node address of network 1, which Default holds


def tenant(tenant_id, name, networks=(), identity_config_id=1, **switches):
    """A tenant as tenant_info returns it; every switch on unless given."""
    return {
        "id": tenant_id,
        "name": name,
        **dict.fromkeys(SWITCHES, True),
        **switches,
        "networks": list(networks),
        "identity_config_id": identity_config_id,
    }


DEFAULT = tenant(1, "Default", networks=[1])


def static(network_id, name, ip_ranges, floating=(), tenant=None, gateway=None, netmask="255.255.255.0"):
    """A STATIC network in the wire shape of /v3/network: network 1 carries the cluster's one IPv4 gateway, others none."""
    if gateway is None:
        gateway = "192.0.2.1" if network_id == 1 else ""
    block = {"default_gateway": gateway, "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), "netmask": netmask}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "STATIC", "static_addresses": block}}


def host(network_id, name, floating=(), tenant=None):
    """A HOST network in the wire shape of /v3/network."""
    block = {"floating_ip_ranges": list(floating), "netmask": None}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "HOST", "host_addresses": block}}


BOND = {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]}


def vlan(vlan_id, network_id, secondary=None, mtu=None):
    return {"vlan_id": vlan_id, "network_id": network_id, "secondary_network_id": secondary, "mtu": mtu}


def wire(*networks, vlans=(), bond=(1,), backend=None, managed=True):
    """The document as /v3/network returns it."""
    interfaces = {"frontend_bond_config": {**BOND, "networks": list(bond)}, "frontend_vlans": list(vlans), "backend_bond_config": backend} if managed else None
    return {"managed_interfaces": interfaces, "frontend_networks": list(networks)}


def doc(*networks, tenants=(DEFAULT,), **interfaces):
    """The document as network_info's document subset returns it: canonical, each network's tenant by name."""
    names = {t["id"]: t["name"] for t in tenants}
    return network.named_document(network.canonical_document(wire(*networks, **interfaces)), names.get)


NET1 = static(1, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"], tenant=1)
ONE = doc(NET1)


def reads(*tenants, document=None, status=None, unavailable=None, protection=None, shares=(), exports=(), overrides=None, nfs=None):
    """What step 2 read, the cluster option; Default alone on ONE by default."""
    return {
        "tenants": list(tenants) or [DEFAULT],
        "network": document or ONE,
        "status": status,
        "status_unavailable": unavailable,
        "protection_type": protection,
        "smb_shares": list(shares),
        "nfs_exports": list(exports),
        "nfs_overrides": overrides or {},
        "nfs_settings": {
            "v4_enabled": False,
            "krb5_enabled": False,
            "krb5i_enabled": False,
            "krb5p_enabled": False,
            "auth_sys_enabled": True,
            "idmap_domain": "",
            **(nfs or {}),
        },
    }


def entry(name, **keys):
    """One qumulo_tenants entry."""
    return {"name": name, **keys}


def net(network_id, name, ip_ranges=(), floating=(), **keys):
    """A network as the inventory writes it, under a tenant or in qumulo_network.frontend_networks."""
    return {"id": network_id, "name": name, "type": "STATIC", "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), **keys}


def share(name, fs_path=None, **keys):
    return {"share_name": name, "fs_path": fs_path or f"/{name.lower()}", **keys}


def export(path, fs_path=None, **keys):
    restrictions = [{"host_restrictions": [], "read_only": False, "user_mapping": "NFS_MAP_NONE"}]
    return {"export_path": path, "fs_path": fs_path or path, "restrictions": restrictions, **keys}


def plan(run_module, *, tenants=(), cluster=None, **options):
    return run_module(tenancy_plan, {"tenants": list(tenants), "cluster": cluster or reads(), "api_host": API_HOST, **options})


def verbose_plan(run_module, **keys):
    """The module's result at -vvv, where a refused plan also returns what it computed, merged back in for the tests that
    read it."""
    result = plan(run_module, _ansible_verbosity=3, **keys)
    return {**result.pop("plan", {}), **result}


def refusals(result):
    return [refusal["message"] for refusal in result.get("refusals", [])]


def passed(result):
    assert not result.get("failed"), result.get("msg")
    return result


def pick(item, *keys):
    return {key: item.get(key) for key in keys}


def test_the_cluster_s_one_tenant_declared_as_it_is_changes_nothing_and_refuses_nothing(run_module):
    result = passed(plan(run_module, tenants=[entry("Default")]))

    assert result["changed"] is False
    assert refusals(result) == []


# T1451a: the inventory rules (spec 014, FR-026), judged against the names after the declared renames.

FINANCE = tenant(2, "finance", networks=[2])
NET2 = static(2, "finance", ["198.51.100.11-14"], ["198.51.100.21-24"], tenant=2)
TWO = doc(NET1, NET2, vlans=[vlan(20, 2, mtu=1500)], tenants=(DEFAULT, FINANCE))  # finance's network on VLAN 20


def test_two_entries_of_one_name_are_refused(run_module):
    result = plan(run_module, tenants=[entry("Default"), entry("finance"), entry("Default", smb_enabled=False)])

    assert refusals(result) == ["qumulo_tenants[0] and qumulo_tenants[2] both declare tenant Default; declare each tenant once."]


def test_two_entries_whose_names_differ_only_in_case_are_two_tenants(run_module):
    """Tenant names compare exactly, as the cluster compares them."""
    result = plan(run_module, tenants=[entry("Default"), entry("default", state="absent")])

    assert refusals(result) == []


def test_an_identity_key_is_refused_as_reserved(run_module):
    """FR-018 to FR-021: per-tenant identity waits for its own spec."""
    result = plan(run_module, tenants=[entry("Default", identity={"ad_domain": "corp.example.com"})])

    assert refusals(result) == ["qumulo_tenants[0] (Default) sets identity, which is reserved for per-tenant identity, which is not supported yet. Remove it."]


SMB_SETTINGS_KEYS = (
    "bypass_traverse_checking, hide_shares_from_unauthorized_hosts, hide_shares_from_unauthorized_users, session_encryption, "
    "signing_required, snapshot_directory_mode and supported_dialects"
)


@pytest.mark.parametrize(
    ("settings", "refusal"),
    [
        (
            {"smb_settings": {"sesion_encryption": "REQUIRED"}},
            "qumulo_tenants[0] (Default) sets smb_settings.sesion_encryption, which smb_settings does not take; did you mean session_encryption?",
        ),
        (
            {"nfs_settings": {"v4_enable": True}},
            "qumulo_tenants[0] (Default) sets nfs_settings.v4_enable, which nfs_settings does not take; did you mean v4_enabled?",
        ),
        (
            {"smb_settings": {"guest_access": True}},
            f"qumulo_tenants[0] (Default) sets smb_settings.guest_access, which smb_settings does not take; it takes {SMB_SETTINGS_KEYS}.",
        ),
        (
            {"smb_settings": {"state": "inherit"}},
            (
                "qumulo_tenants[0] (Default) sets smb_settings.state, which smb_settings does not take; to give the tenant the global "
                "settings, write smb_settings: inherit."
            ),
        ),
        (
            {"nfs_settings": {"tenant": "finance"}},
            (
                "qumulo_tenants[0] (Default) sets nfs_settings.tenant, which nfs_settings does not take; the settings are those of "
                "the tenant whose entry holds them, so leave tenant out."
            ),
        ),
    ],
    ids=["smb", "nfs", "no near key", "state", "tenant"],
)
def test_a_key_a_tenant_s_settings_do_not_take_is_refused_naming_the_tenant_and_the_key(run_module, settings, refusal):
    """Issue #342: the argument spec cannot check the keys of a value that may also be inherit, so the plan does."""
    result = plan(run_module, tenants=[entry("Default", **settings)])

    assert refusals(result) == [refusal]


def test_the_preflight_s_replay_refuses_a_key_a_tenant_s_settings_do_not_take():
    """The edge_rebuild preflight reads replay(), so a rebuild refuses the key before the drain."""
    found = planning.replay([entry("Default", nfs_settings={"v4_enable": True})], (), (), {"smb_files_share": "present", "nfs_root_export": "present"})

    assert [refusal.message for refusal in found.findings.refusals] == [
        "qumulo_tenants[0] (Default) sets nfs_settings.v4_enable, which nfs_settings does not take; did you mean v4_enabled?"
    ]


@pytest.mark.parametrize("settings", ["inherit", {"session_encryption": "REQUIRED", "signing_required": True}], ids=["inherit", "known keys"])
def test_a_tenant_s_settings_with_known_keys_or_inherit_are_not_refused(run_module, settings):
    result = plan(run_module, tenants=[entry("Default", smb_settings=settings)])

    assert refusals(result) == []


@pytest.mark.parametrize(
    ("options", "place"),
    [
        ({"smb_shares": [share("forms", tenants=["hr"])]}, "qumulo_smb_shares[0] (forms)"),
        ({"nfs_exports": [export("/forms", tenants=["Default", "hr"])]}, "qumulo_nfs_exports[0] (/forms)"),
        ({"network": {"frontend_networks": [net(1, "Default", tenant="hr")]}}, "qumulo_network.frontend_networks[0] (network 1)"),
    ],
)
def test_a_flat_entry_naming_a_tenant_neither_declared_nor_held_is_refused(run_module, options, place):
    """US1-10."""
    result = plan(run_module, tenants=[entry("Default")], **options)

    assert refusals(result) == [f"{place} names tenant hr, which qumulo_tenants does not declare and the cluster does not hold."]


def test_a_flat_entry_naming_a_tenant_the_cluster_holds_but_the_inventory_leaves_out_passes(run_module):
    result = plan(run_module, tenants=[entry("Default")], cluster=reads(DEFAULT, FINANCE, document=TWO), smb_shares=[share("forms", tenants=["finance"])])

    assert refusals(result) == []


def test_a_flat_network_naming_a_tenant_declared_absent_is_refused(run_module):
    """Decision D24: a network kept for a tenant the run deletes contradicts the delete."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance", state="absent")],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
        network={"frontend_networks": [net(1, "Default"), net(2, "finance", tenant="finance")]},
    )

    assert refusals(result) == [
        (
            "qumulo_network.frontend_networks[1] (network 2) names tenant finance, which qumulo_tenants declares state: absent. "
            "List the network under a tenant that stays, or leave it out so the run removes it."
        )
    ]


@pytest.mark.parametrize(
    ("nested", "place"),
    [
        ({"smb_shares": [share("ledger", tenants=["finance"])]}, "the SMB share ledger of tenant finance"),
        ({"nfs_exports": [export("/ledger", tenants=["finance"])]}, "the NFS export /ledger of tenant finance"),
    ],
)
def test_tenants_on_a_nested_entry_is_refused(run_module, nested, place):
    result = plan(run_module, tenants=[entry("Default"), entry("finance", **nested)], cluster=reads(DEFAULT, FINANCE, document=TWO))

    assert refusals(result) == [f"{place} sets tenants, but an entry under a tenant belongs to that tenant. Remove tenants."]


@pytest.mark.parametrize(
    ("nested", "place"),
    [
        ({"smb_shares": [share("ledger", tenant="finance")]}, "the SMB share ledger of tenant finance"),
        ({"nfs_exports": [export("/ledger", tenant="Default")]}, "the NFS export /ledger of tenant finance"),
    ],
)
def test_tenant_on_a_nested_entry_is_refused(run_module, nested, place):
    """Whatever tenant it names, as tenants on a nested entry is: the entry's place names its tenant."""
    result = plan(run_module, tenants=[entry("Default"), entry("finance", **nested)], cluster=reads(DEFAULT, FINANCE, document=TWO))

    assert refusals(result) == [f"{place} sets tenant, but an entry under a tenant belongs to that tenant. Remove tenant."]


def test_tenants_with_tenant_id_on_a_flat_entry_is_refused(run_module):
    result = plan(run_module, tenants=[entry("Default")], nfs_exports=[export("/home", tenants=["Default"], tenant_id=1)])

    assert refusals(result) == ["qumulo_nfs_exports[0] (/home) sets both tenants and tenant_id; name its tenants once, with tenants."]


@pytest.mark.parametrize("keys", [{"tenant": "Default"}, {"tenant": "Default", "tenant_id": 1}, {"tenant": "Default", "tenants": ["Default"]}])
def test_tenant_on_a_flat_share_is_refused_for_tenants(run_module, keys):
    """Question 8: an inventory names a flat entry's tenants one way, with tenants."""
    result = plan(run_module, tenants=[entry("Default")], smb_shares=[share("home", **keys)])

    assert refusals(result) == ["qumulo_smb_shares[0] (home) sets tenant, which a qumulo_smb_shares entry does not take; write tenants: [Default]."]


def test_tenant_with_tenant_id_on_a_flat_network_is_refused(run_module):
    result = plan(run_module, tenants=[entry("Default")], network={"frontend_networks": [net(1, "Default", tenant="Default", tenant_id=1)]})

    assert refusals(result) == ["qumulo_network.frontend_networks[0] (network 1) sets both tenant and tenant_id; give tenant alone."]


@pytest.mark.parametrize(
    ("given", "warning"),
    [
        (1, "the SMB share home of tenant Default: tenant_id 1 is tenant Default, the tenant it is under, so tenant_id is dropped; remove it."),
        (9, "the SMB share home of tenant Default: tenant_id 9 is no tenant on the cluster, so tenant_id is dropped; remove it."),
    ],
)
def test_tenant_id_on_a_nested_entry_naming_its_own_tenant_or_none_is_dropped_with_a_warning(run_module, given, warning):
    """Question 7: an entry's place under a tenant names its tenant."""
    result = passed(plan(run_module, tenants=[entry("Default", smb_shares=[share("home", tenant_id=given)])]))

    assert warning in result["warnings"]


def test_tenant_id_on_a_nested_entry_naming_another_tenant_is_refused(run_module):
    result = plan(
        run_module,
        tenants=[entry("Default", nfs_exports=[export("/ledger", tenant_id=2)]), entry("finance")],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
    )

    assert refusals(result) == [
        (
            "the NFS export /ledger of tenant Default sets tenant_id 2, which is tenant finance, but an entry under a tenant "
            "belongs to that tenant. Move it under finance, or remove tenant_id."
        )
    ]


def test_an_inventory_deleting_every_tenant_is_refused_naming_the_entries(run_module):
    """US1-29."""
    result = plan(run_module, tenants=[entry("Default", state="absent"), entry("finance", state="absent")], cluster=reads(DEFAULT, FINANCE, document=TWO))

    assert refusals(result) == ["This run would delete every tenant (Default and finance). Remove state: absent from one of them."]


def test_a_create_whose_identity_configuration_the_tenants_leave_undecided_is_refused(run_module):
    """US1-23: tenant 1 is gone, and the tenants left use identity configurations 1 and 2."""
    finance, eng = tenant(2, "finance", identity_config_id=1), tenant(3, "eng", identity_config_id=2)

    result = plan(run_module, tenants=[entry("hr")], cluster=reads(finance, eng))

    assert refusals(result) == [
        (
            "creating tenant hr: a new tenant takes the identity configuration of tenant 1, or once tenant 1 is gone the one every "
            "tenant shares, but tenant 1 is gone and the tenants use different ones: eng (identity configuration 2) and finance "
            "(identity configuration 1). Choosing one for the new tenant is per-tenant identity, which the collection does "
            "not support yet"
        )
    ]


def test_a_create_takes_tenant_1_s_identity_configuration_while_tenant_1_is_there(run_module):
    result = plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, tenant(2, "finance", identity_config_id=2)))

    assert refusals(result) == []


def test_a_second_tenant_while_the_only_one_holds_a_backend_network_is_refused(run_module):
    """US1-13, FR-006: with two tenants a tenant network off the frontend interfaces aborts every node."""
    backend = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [4]}
    default = tenant(1, "Default", networks=[1, 4])
    document = doc(NET1, static(4, "backend", ["10.0.0.11-14"], tenant=1), backend=backend, tenants=(default,))

    result = plan(run_module, tenants=[entry("Default"), entry("finance")], cluster=reads(default, document=document))

    assert refusals(result) == [
        (
            "creating tenant finance would make a second tenant while tenant Default holds network 4 (backend), which is not on "
            "the frontend bond, a frontend VLAN or a HOST network, and with two tenants such a network aborts every node "
            "(G-TENANT-BACKEND). Unbind it first with a tenant PATCH of networks that leaves it out (qq "
            "multitenancy_modify_tenant --network-id), since qq network_mod_network --clear-tenant-id puts it back in the only "
            "tenant"
        )
    ]


# The rename refusal: a rename still to be done whose new name the cluster holds (FR-026, the plan's table).

A, B, C = tenant(1, "A", networks=[1]), tenant(2, "B"), tenant(3, "C")
SWAP_REFUSED = (
    "qumulo_tenants[0] renames A to B and qumulo_tenants[1] renames B to A, but the cluster holds both A and B, so the "
    "first rename would find B there and rename nothing. Make each step of a swap or a chain of renames in a run of its "
    "own, through a name the cluster does not hold."
)
CHAIN_REFUSED = (
    "qumulo_tenants[0] renames A to B and qumulo_tenants[1] renames B to C, but the cluster holds both A and B, so the "
    "first rename would find B there and rename nothing. Make each step of a swap or a chain of renames in a run of its "
    "own, through a name the cluster does not hold."
)


@pytest.mark.parametrize(
    ("entries", "cluster", "expected"),
    [
        pytest.param([entry("B", previous_name="A"), entry("A", previous_name="B")], (A, B), [SWAP_REFUSED], id="swap still to do"),
        pytest.param([entry("B", previous_name="A"), entry("C", previous_name="B")], (A, B), [CHAIN_REFUSED], id="chain still to do"),
        pytest.param([entry("B", previous_name="A"), entry("C", previous_name="B")], (tenant(1, "B", networks=[1]), C), [], id="chain done"),
        pytest.param([entry("A", previous_name="B"), entry("B", previous_name="T")], (A, B), [], id="swap done through a free name"),
        pytest.param([entry("B", previous_name="A"), entry("A")], (A, B), [], id="A renamed to B, then a new A"),
    ],
)
def test_the_rename_refusal_of_each_row_of_the_plan_s_table(run_module, entries, cluster, expected):
    assert refusals(plan(run_module, tenants=entries, cluster=reads(*cluster))) == expected


def test_a_shared_entry_naming_a_tenant_the_run_renames_is_refused_with_the_old_name(run_module):
    """US1-14: names are judged after the renames."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("accounts", previous_name="finance")],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
        smb_shares=[share("forms", tenants=["finance"])],
    )

    assert refusals(result) == [
        (
            "qumulo_smb_shares[0] (forms) names tenant finance, which qumulo_tenants does not declare and the cluster does "
            "not hold. This run renames finance to accounts; name accounts."
        )
    ]


def test_a_previous_name_that_an_entry_also_declares_is_a_valid_name(run_module):
    """Row 5 of the plan's table: A was renamed to B and a new A declared, so a shared entry naming A is not stale."""
    result = plan(run_module, tenants=[entry("B", previous_name="A"), entry("A")], cluster=reads(A, B), smb_shares=[share("forms", tenants=["A"])])

    assert refusals(result) == []


def test_two_entries_renaming_one_tenant_are_refused(run_module):
    """Question 9."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("accounts", previous_name="finance"), entry("ledger", previous_name="finance")],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
    )

    assert refusals(result) == ["qumulo_tenants[1] (accounts) and qumulo_tenants[2] (ledger) both rename tenant finance; a tenant takes one new name."]


# T1452a: the pairs each share and export entry claims (FR-037, FR-028), and steps 18, 19 and 20a.


def held_share(name, tenant_id):
    """A share as smb_info returns it; tenancy_plan reads share_name and tenant_id."""
    return {"share_name": name, "fs_path": f"/{name.lower()}", "tenant": None, "tenant_id": tenant_id}


def held_export(path, tenant_id):
    """An export as nfs_info returns it; tenancy_plan reads export_path and tenant_id."""
    return {"export_path": path, "fs_path": path, "tenant": None, "tenant_id": tenant_id}


HR = tenant(3, "hr")
THREE = (DEFAULT, tenant(2, "finance"), HR)


def test_tenants_all_gives_every_present_tenant_one_copy(run_module):
    """US2-13."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance"), entry("hr")],
            cluster=reads(*THREE),
            smb_shares=[share("home", tenants="all", expand_fs_path_variables=True)],
        )
    )

    assert result["smb"] == {
        "removals": [],
        "overrides": [],
        "shares": [{"share_name": "home", "fs_path": "/home", "expand_fs_path_variables": True, "tenants": ["Default", "finance", "hr"]}],
    }


def test_an_absent_tenant_s_pairs_are_claimed_for_removal_by_all_and_by_a_list(run_module):
    """US2-17: finance's home and forms go at step 18, hr's copies stay, and finance goes at 20a."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance", state="absent"), entry("hr")],
            cluster=reads(*THREE, shares=[held_share("home", 2), held_share("forms", 2), held_share("home", 3), held_share("forms", 3)]),
            smb_shares=[share("home", tenants="all"), share("forms", tenants=["finance", "hr"])],
        )
    )

    assert result["smb"]["removals"] == [
        {"share_name": "home", "tenants": ["finance"], "state": "absent"},
        {"share_name": "forms", "tenants": ["finance"], "state": "absent"},
    ]
    assert result["smb"]["shares"] == [
        {"share_name": "home", "fs_path": "/home", "tenants": ["Default", "hr"]},
        {"share_name": "forms", "fs_path": "/forms", "tenants": ["hr"]},
    ]
    assert [pick(item, "name", "state") for item in result["step_20a"]] == [{"name": "finance", "state": "absent"}]


@pytest.mark.parametrize(
    ("removal", "expected"),
    [
        pytest.param({}, [], id="leaving the list removes nothing"),
        pytest.param(
            {"smb_shares": [share("forms", tenants=["finance"]), share("forms", tenants=["hr"], state="absent")]},
            [{"share_name": "forms", "tenants": ["hr"], "state": "absent"}],
            id="a flat absent pair",
        ),
        pytest.param({"hr": [share("forms", state="absent")]}, [{"share_name": "forms", "tenant": "hr", "state": "absent"}], id="a nested absent pair"),
    ],
)
def test_a_tenant_leaving_a_list_keeps_its_copy_until_an_absent_pair_claims_it(run_module, removal, expected):
    """US2-14: forms was in finance and hr; hr has left the list."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance"), entry("hr", smb_shares=removal.get("hr", []))],
            cluster=reads(*THREE, shares=[held_share("forms", 2), held_share("forms", 3)]),
            smb_shares=removal.get("smb_shares", [share("forms", tenants=["finance"])]),
        )
    )

    assert result["smb"]["removals"] == expected
    assert result["smb"]["shares"] == [{"share_name": "forms", "fs_path": "/forms", "tenants": ["finance"]}]


def test_a_share_of_the_entry_s_name_in_a_tenant_it_does_not_claim_gets_no_item(run_module):
    """US2-16."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance"), entry("hr")],
            cluster=reads(*THREE, shares=[held_share("forms", 3)]),
            smb_shares=[share("forms", tenants=["finance"])],
        )
    )

    assert result["smb"] == {"removals": [], "overrides": [], "shares": [{"share_name": "forms", "fs_path": "/forms", "tenants": ["finance"]}]}


@pytest.mark.parametrize("nested_name", ["ledger", "LEDGER"])
def test_a_pair_a_nested_and_a_shared_entry_both_claim_is_refused(run_module, nested_name):
    """US2-15: SMB names compare casefolded."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance", smb_shares=[share(nested_name)]), entry("hr")],
        cluster=reads(*THREE),
        smb_shares=[share("ledger", tenants=["finance", "hr"])],
    )

    assert refusals(result) == [
        (
            f"finance's SMB share {nested_name} comes from both the SMB share {nested_name} of tenant finance and "
            "qumulo_smb_shares[0] (ledger); a share comes from one entry only."
        )
    ]


def test_the_factory_absent_beside_a_nested_files_is_refused(run_module):
    result = plan(run_module, tenants=[entry("Default", smb_shares=[share("Files", fs_path="/")])], factory_defaults={"smb_files_share": "absent"})

    assert refusals(result) == [
        (
            "Default's SMB share Files comes from both the SMB share Files of tenant Default and "
            "qumulo_factory_defaults.smb_files_share; a share comes from one entry only."
        )
    ]


def test_an_absent_tenant_s_nested_exports_are_removed_first_in_step_19(run_module):
    """US2-18: finance's exports go before anything else of step 19."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance", state="absent", nfs_exports=[export("/ledger"), export("/ledger/2026")])],
            cluster=reads(DEFAULT, tenant(2, "finance"), exports=[held_export("/", 1), held_export("/ledger", 2), held_export("/ledger/2026", 2)]),
            nfs_exports=[export("/scratch", tenants=["Default"])],
            factory_defaults={"nfs_root_export": "absent"},
        )
    )

    assert result["nfs"] == {
        "removals": [
            {"export_path": "/ledger", "tenant": "finance", "state": "absent"},
            {"export_path": "/ledger/2026", "tenant": "finance", "state": "absent"},
            {"export_path": "/", "tenant": "Default", "state": "absent"},
        ],
        "overrides": [],
        "exports": [{**export("/scratch"), "tenants": ["Default"]}],
    }


FLAT_WITHOUT_TENANT = (
    "qumulo_smb_shares[0] (home) names no tenant, but this run has more than one tenant (Default and finance). Name them with tenants, such as tenants: all."
)


@pytest.mark.parametrize(
    ("cluster", "entries"),
    [
        pytest.param((DEFAULT, tenant(2, "finance")), [entry("Default")], id="the cluster has two"),
        pytest.param((DEFAULT,), [entry("Default"), entry("finance")], id="the run creates the second"),
    ],
)
def test_a_flat_entry_with_no_tenant_is_refused_once_the_run_has_two_tenants(run_module, cluster, entries):
    """US2-3."""
    result = plan(run_module, tenants=entries, cluster=reads(*cluster), smb_shares=[share("home")])

    assert refusals(result) == [FLAT_WITHOUT_TENANT]


def test_a_flat_entry_with_no_tenant_on_one_tenant_keeps_no_tenant_key(run_module):
    result = passed(plan(run_module, tenants=[entry("Default")], smb_shares=[share("home")], nfs_exports=[export("/home", state="absent")]))

    assert result["smb"]["shares"] == [share("home")]
    assert result["nfs"]["removals"] == [{"export_path": "/home", "state": "absent"}]


def test_an_absent_tenant_holding_a_share_and_an_export_no_entry_claims_is_refused(run_module):
    """US5-4: the run would leave them to the server's cascade."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance", state="absent", smb_shares=[share("ledger")])],
        cluster=reads(DEFAULT, tenant(2, "finance"), shares=[held_share("ledger", 2), held_share("audit", 2)], exports=[held_export("/x", 2)]),
    )

    assert refusals(result) == [
        (
            "tenant finance is state: absent but holds SMB share 'audit' and NFS export '/x' that no entry declares or claims "
            "for it. Declare each under finance, or list finance in the shared entry that has it, so the run removes it before "
            "the tenant."
        )
    ]


def test_tenants_all_with_no_present_tenant_is_refused(run_module):
    result = plan(run_module, smb_shares=[share("home", tenants="all")])

    assert refusals(result) == [
        "qumulo_smb_shares[0] (home) sets tenants: all, but qumulo_tenants declares no present tenant. Declare the tenants, or name them in tenants."
    ]


@pytest.mark.parametrize(
    ("tenants", "message"),
    [
        ([], "qumulo_nfs_exports[0] (/home): tenants is empty; name at least one tenant."),
        (["Default", "finance", "Default"], "qumulo_nfs_exports[0] (/home): tenants names Default more than once."),
    ],
)
def test_an_empty_list_or_a_repeated_name_in_tenants_is_refused(run_module, tenants, message):
    result = plan(run_module, tenants=[entry("Default"), entry("finance")], nfs_exports=[export("/home", tenants=tenants)])

    assert refusals(result) == [message]


def test_an_old_inventory_s_tenant_id_counts_as_tenants_with_a_warning_per_entry(run_module):
    """US4-4: a flat tenant_id: 1 on two tenants is tenants: [Default], by its name."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance")],
            cluster=reads(DEFAULT, tenant(2, "finance")),
            smb_shares=[share("home", tenant_id=1)],
            nfs_exports=[export("/home", tenant_id=1)],
        )
    )

    assert result["smb"]["shares"] == [{**share("home"), "tenants": ["Default"]}]
    assert result["nfs"]["exports"] == [{**export("/home"), "tenants": ["Default"]}]
    assert result["warnings"] == [
        "qumulo_smb_shares[0] (home): tenant_id 1 is translated to tenant Default; tenant_id is deprecated, so write tenants: [Default].",
        "qumulo_nfs_exports[0] (/home): tenant_id 1 is translated to tenant Default; tenant_id is deprecated, so write tenants: [Default].",
    ]


def test_a_tenant_id_is_translated_to_the_tenant_s_name_after_the_renames(run_module):
    result = passed(plan(run_module, tenants=[entry("Main", previous_name="Default")], smb_shares=[share("home", tenant_id=1)]))

    assert result["smb"]["shares"] == [{**share("home"), "tenants": ["Main"]}]


def test_a_tenant_id_the_cluster_lacks_skips_its_entry_with_a_warning(run_module):
    """FR-031: tenant ids are never reused, so the id names no tenant."""
    result = passed(plan(run_module, tenants=[entry("Default")], nfs_exports=[export("/home", tenant_id=9)]))

    assert result["nfs"]["exports"] == []
    assert result["warnings"] == [
        (
            "qumulo_nfs_exports[0] (/home): tenant_id 9 is no tenant on the cluster, since tenant ids are never reused, "
            "so the entry is skipped; name its tenants with tenants."
        )
    ]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        pytest.param([entry("Default"), entry("finance")], "Default", id="two tenants"),
        pytest.param([entry("Main", previous_name="Default"), entry("finance")], "Main", id="tenant 1 renamed"),
    ],
)
def test_the_factory_files_share_absent_removes_tenant_1_s_and_no_other(run_module, entries, expected):
    """US2-11, FR-028."""
    result = passed(
        plan(
            run_module,
            tenants=entries,
            cluster=reads(DEFAULT, tenant(2, "finance"), shares=[held_share("Files", 1), held_share("Files", 2)]),
            factory_defaults={"smb_files_share": "absent"},
        )
    )

    assert result["smb"]["removals"] == [{"share_name": "Files", "tenant": expected, "state": "absent"}]


def test_the_factory_absent_with_no_tenant_1_does_nothing_and_says_so(run_module):
    result = passed(
        plan(run_module, tenants=[entry("finance")], cluster=reads(tenant(2, "finance", networks=[1])), factory_defaults={"smb_files_share": "absent"})
    )

    assert result["smb"]["removals"] == []
    assert result["warnings"] == [
        "qumulo_factory_defaults.smb_files_share: absent does nothing, since the cluster has no tenant 1, which held the factory SMB share Files."
    ]


SMB_FACTORY_GOES = (
    "tenant Default is state: absent, so the factory SMB share Files goes with it; "
    "qumulo_factory_defaults.smb_files_share has no effect on a tenant declared absent."
)
NFS_FACTORY_GOES = (
    "tenant Default is state: absent, so the factory NFS export / goes with it; "
    "qumulo_factory_defaults.nfs_root_export has no effect on a tenant declared absent."
)


@pytest.mark.parametrize(
    ("factory", "warnings"),
    [
        pytest.param({"smb_files_share": "present"}, [SMB_FACTORY_GOES], id="the share present, the export unset"),
        pytest.param({"smb_files_share": "present", "nfs_root_export": "present"}, [SMB_FACTORY_GOES, NFS_FACTORY_GOES], id="both present"),
        pytest.param({}, [], id="both unset"),
    ],
)
def test_tenant_1_declared_absent_takes_its_factory_share_and_export_as_its_content(run_module, factory, warnings):
    """US5-12: present has no effect on a tenant declared absent, and the run says so; an unset option draws nothing."""
    default, finance = tenant(1, "Default"), tenant(2, "finance", networks=[1])
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", state="absent", smb_shares=[share("Files", fs_path="/")], nfs_exports=[export("/")]), entry("finance")],
            cluster=reads(
                default,
                finance,
                document=doc(static(1, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"], tenant=2)),
                shares=[held_share("Files", 1)],
                exports=[held_export("/", 1)],
            ),
            factory_defaults=factory,
        )
    )

    assert result["smb"]["removals"] == [{"share_name": "Files", "tenant": "Default", "state": "absent"}]
    assert result["nfs"]["removals"] == [{"export_path": "/", "tenant": "Default", "state": "absent"}]
    assert [pick(item, "name", "state") for item in result["step_20a"]] == [{"name": "Default", "state": "absent"}]
    assert result["warnings"] == warnings


def test_the_order_of_step_18_s_removals_and_overrides(run_module):
    """Removals: the absent pairs of tenants that stay, then each absent tenant's content, then the factory share."""
    result = passed(
        plan(
            run_module,
            tenants=[
                entry("Default", smb_shares=[share("old", state="absent")], smb_settings="inherit"),
                entry("finance", smb_settings={"session_encryption": "REQUIRED"}),
                entry("hr", state="absent", smb_shares=[share("audit")]),
            ],
            cluster=reads(*THREE, shares=[held_share("Files", 1), held_share("audit", 3), held_share("forms", 3)]),
            smb_shares=[share("forms", tenants=["finance", "hr"]), share("tmp", tenants=["Default"], state="absent")],
            factory_defaults={"smb_files_share": "absent"},
        )
    )

    assert result["smb"]["removals"] == [
        {"share_name": "old", "tenant": "Default", "state": "absent"},
        {"share_name": "tmp", "tenants": ["Default"], "state": "absent"},
        {"share_name": "audit", "tenant": "hr", "state": "absent"},
        {"share_name": "forms", "tenants": ["hr"], "state": "absent"},
        {"share_name": "Files", "tenant": "Default", "state": "absent"},
    ]
    assert result["smb"]["overrides"] == [
        {"tenant": "Default", "state": "inherit"},
        {"tenant": "finance", "state": "present", "session_encryption": "REQUIRED"},
    ]
    assert result["smb"]["shares"] == [{"share_name": "forms", "fs_path": "/forms", "tenants": ["finance"]}]


# T1453a: the network document of step 3 (the plan's "Networks" rules; FR-012, FR-013, FR-026).

INV1 = net(1, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"])  # network 1 as ONE and TWO hold it
INV2 = net(2, "finance", ["198.51.100.11-14"], ["198.51.100.21-24"], vlan_id=20, mtu=1500)  # network 2 as TWO holds it
FLAT2 = net(2, "finance", ["198.51.100.11-14"], ["198.51.100.21-24"])  # the same, as qumulo_network.frontend_networks lists it
VLAN20 = {"vlan_id": 20, "network_id": 2, "secondary_network_id": None, "mtu": 1500}


def option(network, tenant=None):
    """A network as step 3 passes it to network_config: without vlan_id, mtu and tenant_id, with its tenant by name."""
    out = {key: value for key, value in network.items() if key not in ("vlan_id", "mtu", "tenant_id")}
    return {**out, "tenant": tenant} if tenant else out


def interfaces(result):
    return result["network"]["managed_interfaces"]


def test_the_plan_s_example_puts_finance_s_network_on_its_vlan_and_default_s_on_the_bond(run_module):
    """The plan's "The inventory": finance is created with network 2 on VLAN 20, and retired is long gone."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[INV2]), entry("retired", state="absent", networks=[])],
            network={"managed_interfaces": {"frontend_bond_config": {"mtu": 9000}}},
        )
    )

    assert pick(result["network"], "frontend_networks", "managed_interfaces") == {
        "frontend_networks": [option(INV1, "Default"), option(INV2, "finance")],
        "managed_interfaces": {"frontend_bond_config": {"mtu": 9000, "networks": [1]}, "frontend_vlans": [VLAN20]},
    }
    assert result["network_changes"] is True
    assert result["bindings"] == {"1": "Default", "2": "finance"}


def test_a_converged_two_tenant_inventory_changes_nothing(run_module):
    """The cluster's document names each network's tenant, which the comparison leaves out."""
    result = passed(
        plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[INV2])], cluster=reads(DEFAULT, FINANCE, document=TWO))
    )

    assert result["network_changes"] is False
    assert result["bindings"] == {"1": "Default", "2": "finance"}
    assert result["warnings"] == []


@pytest.mark.parametrize(
    ("default_networks", "vlans", "bindings"),
    [
        pytest.param([INV1], [], {"1": "Default"}, id="removed with its VLAN"),
        pytest.param([INV1, INV2], [VLAN20], {"1": "Default", "2": "Default"}, id="moved to a present tenant"),
    ],
)
def test_a_network_under_an_absent_tenant_is_removed_or_moved_when_a_present_tenant_lists_it(run_module, default_networks, vlans, bindings):
    """US1-8, step 3: finance's whole entry, with state: absent."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=default_networks), entry("finance", state="absent", networks=[INV2])],
            cluster=reads(DEFAULT, FINANCE, document=TWO),
        )
    )

    assert [item["id"] for item in result["network"]["frontend_networks"]] == [int(network_id) for network_id in bindings]
    assert interfaces(result)["frontend_vlans"] == vlans
    assert result["bindings"] == bindings


def test_with_no_networks_declared_the_document_keeps_every_live_network_and_its_tenant(run_module):
    """The managed_interfaces-only document: frontend_networks stays unset, so network_config keeps the live networks."""
    given = {"frontend_bond_config": {"mtu": 1500}}
    result = passed(
        plan(run_module, tenants=[entry("Default"), entry("finance")], cluster=reads(DEFAULT, FINANCE, document=TWO), network={"managed_interfaces": given})
    )

    assert "frontend_networks" not in result["network"]
    assert interfaces(result) == given
    assert result["network_changes"] is True
    assert result["bindings"] == {"1": "Default", "2": "finance"}


def test_an_absent_tenant_s_networks_alone_leave_the_document_unset(run_module):
    """Question 6: retired's networks: [] marks removals only, so the present tenants need not declare networks."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance"), entry("retired", state="absent", networks=[])],
            cluster=reads(DEFAULT, FINANCE, document=TWO),
        )
    )

    assert pick(result["network"], "frontend_networks", "managed_interfaces") == {"frontend_networks": None, "managed_interfaces": None}
    assert result["network_changes"] is False


def test_once_a_tenant_declares_networks_every_present_tenant_must(run_module):
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance"), entry("hr"), entry("retired", state="absent")],
        cluster=reads(DEFAULT, FINANCE, HR, document=TWO),
    )

    assert refusals(result) == [
        (
            "qumulo_tenants[1] (finance) and qumulo_tenants[2] (hr) leave out networks, but qumulo_tenants[0] (Default) "
            "declares them. Once a tenant declares networks, the tenants' networks and qumulo_network.frontend_networks are "
            "the whole set of frontend networks, so each present tenant declares its own, networks: [] for none."
        )
    ]


@pytest.mark.parametrize(
    ("tenants", "flat", "message"),
    [
        pytest.param(
            [entry("Default", networks=[INV1, INV2]), entry("finance", networks=[INV2])],
            None,
            "network 2 is listed under tenant Default and under tenant finance; list each network once.",
            id="under two present tenants",
        ),
        pytest.param(
            [entry("Default", networks=[INV1]), entry("finance", networks=[INV2, INV2])],
            None,
            "network 2 is listed twice under tenant finance; list each network once.",
            id="twice under one tenant",
        ),
        pytest.param(
            [entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
            [FLAT2],
            "network 2 is listed in qumulo_network.frontend_networks[0] and under tenant finance; list each network once.",
            id="under a tenant and in the flat list",
        ),
    ],
)
def test_a_network_listed_twice_is_refused(run_module, tenants, flat, message):
    """FR-026."""
    result = plan(run_module, tenants=tenants, cluster=reads(DEFAULT, FINANCE, document=TWO), network={"frontend_networks": flat})

    assert refusals(result) == [message]


def test_an_absent_tenant_s_network_kept_in_the_flat_list_is_refused(run_module):
    """Decision D24: the flat list keeps what the delete removes."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", state="absent", networks=[INV2])],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
        network={"frontend_networks": [FLAT2]},
    )

    assert refusals(result) == [
        (
            "network 2 is listed under tenant finance, which qumulo_tenants declares state: absent, and kept in "
            "qumulo_network.frontend_networks[0]. Leave it out of qumulo_network.frontend_networks so the run removes it, "
            "or give it a tenant that stays."
        )
    ]


NOT_BUILT = (
    "which this run keeps: no present tenant declares networks and qumulo_network.frontend_networks is unset, so the run "
    "sends no network document. Declare networks under every present tenant, or set qumulo_network.frontend_networks, "
    "leaving the network out so the run removes it, or giving it a tenant that stays to move it."
)


@pytest.mark.parametrize(
    ("finance", "options", "kept"),
    [
        pytest.param(
            {},
            {"network": {"frontend_networks": [INV1, FLAT2]}},
            "kept in qumulo_network.frontend_networks[1]. Give the network a tenant that stays, or leave it out so the run removes it.",
            id="kept in the flat list",
        ),
        pytest.param({}, {}, NOT_BUILT, id="kept since no networks are declared"),
        pytest.param({"networks": [INV2]}, {}, NOT_BUILT, id="listed under the absent tenant, with no networks declared"),
        pytest.param(
            {},
            {"network": {"frontend_networks": [{"id": 1, "floating_ip_ranges": ["192.0.2.21-24"]}]}, "owned_parts": ["interfaces", "floating_ips"]},
            (
                "which this run keeps: owned_parts leaves out networks, so the run neither removes a network nor moves one "
                "to another tenant. Add networks to owned_parts."
            ),
            id="kept since the run does not own networks",
        ),
    ],
)
def test_a_network_an_absent_tenant_still_holds_after_step_3_is_refused(run_module, finance, options, kept):
    """US5-4: the server's cascade would take it with the tenant. The remedy is one that removes it from where it is."""
    result = plan(run_module, tenants=[entry("Default"), entry("finance", state="absent", **finance)], cluster=reads(DEFAULT, FINANCE, document=TWO), **options)

    assert refusals(result) == [f"tenant finance is state: absent but holds network 2 (finance), {kept}"]


def test_a_flat_network_whose_tenant_id_is_a_tenant_declared_absent_is_refused(run_module):
    """Decision D24, through the tenant_id translation."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance", state="absent")],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
        network={"frontend_networks": [INV1, {**FLAT2, "tenant_id": 2}]},
    )

    assert refusals(result) == [
        (
            "qumulo_network.frontend_networks[1] (network 2) sets tenant_id 2, which is tenant finance, and qumulo_tenants "
            "declares finance state: absent. List the network under a tenant that stays, or leave it out so the run removes it."
        )
    ]


def test_a_document_that_removes_a_network_of_a_tenant_the_inventory_leaves_out_is_refused(run_module):
    """US1-16."""
    result = plan(run_module, tenants=[entry("Default", networks=[INV1])], cluster=reads(DEFAULT, FINANCE, document=TWO))

    assert refusals(result) == [
        (
            "This run removes network 2 (finance), which tenant finance holds, but qumulo_tenants does not declare finance. "
            "Declare finance and list the network under it, or keep the network in qumulo_network.frontend_networks."
        )
    ]


BACKEND = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [4]}
LAB = net(3, "lab", ["203.0.113.11-14"], ["203.0.113.21-24"])


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(
            None,
            [
                (
                    "qumulo_network.frontend_networks[0] (network 3) is a new network with no tenant, but this run has more "
                    "than one tenant (Default and finance), so the cluster would hold it in no tenant and it would serve no "
                    "client. Name its tenant with tenant, or list it under its tenant in qumulo_tenants."
                )
            ],
            id="refused",
        ),
        pytest.param({"backend_bond_config": {**BACKEND, "networks": [3]}}, [], id="on the backend bond, passed"),
    ],
)
def test_a_new_flat_network_with_no_tenant_on_two_tenants(run_module, given, expected):
    """US1-22, FR-012."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
        network={"frontend_networks": [LAB], "managed_interfaces": given},
    )

    assert refusals(result) == expected


INV4 = net(4, "backend", ["10.0.0.11-14"])
NET4 = static(4, "backend", ["10.0.0.11-14"])
G_TENANT_BACKEND = (
    "is on the backend bond, and a backend network takes no tenant: with two tenants a tenant network without a frontend "
    "interface aborts every node (G-TENANT-BACKEND)."
)


@pytest.mark.parametrize(
    ("finance_networks", "flat", "message"),
    [
        pytest.param(
            [INV2, INV4], [], f"network 4 of tenant finance {G_TENANT_BACKEND} List it in qumulo_network.frontend_networks instead.", id="under a tenant"
        ),
        pytest.param(
            [INV2],
            [{**INV4, "tenant": "finance"}],
            f"qumulo_network.frontend_networks[0] (network 4) {G_TENANT_BACKEND} Leave tenant out.",
            id="a flat entry's tenant",
        ),
    ],
)
def test_a_tenant_on_a_backend_network_is_refused(run_module, finance_networks, flat, message):
    """US1-12, FR-013."""
    document = doc(NET1, NET2, NET4, vlans=[vlan(20, 2, mtu=1500)], backend=BACKEND, tenants=(DEFAULT, FINANCE))
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", networks=finance_networks)],
        cluster=reads(DEFAULT, FINANCE, document=document),
        network={"frontend_networks": flat},
    )

    assert refusals(result) == [message]


@pytest.mark.parametrize(
    ("default_networks", "finance_networks", "interface"),
    [
        pytest.param([INV1, net(3, "Default6", ["2001:db8:20::11-14"], vlan_id=20)], [INV2], "VLAN 20 carries 3 (Default6)", id="one VLAN"),
        pytest.param([INV1], [FLAT2], "the frontend bond bond0 carries 1 (Default)", id="the bond, untagged"),
    ],
)
def test_two_tenants_networks_on_one_interface_are_refused(run_module, default_networks, finance_networks, interface):
    """US1-11, FR-013: the IPv4 and IPv6 networks of one VLAN under two tenants, or two tenants' untagged networks."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=default_networks), entry("finance", networks=finance_networks)],
        cluster=reads(DEFAULT, FINANCE, document=TWO),
    )

    assert refusals(result) == [
        (
            f"{interface} in tenant 'Default' and 2 (finance) in tenant 'finance', but the networks of one interface or VLAN "
            "belong to one tenant. Put each tenant's networks on an interface or VLAN of their own"
        )
    ]


def test_two_tenants_on_one_interface_already_on_the_cluster_warn_and_pass(run_module):
    """Question 11: as network_config, a conflict the run leaves as it is warns, so step 2 and step 3 agree."""
    on_the_bond = doc(NET1, NET2, bond=(1, 2), tenants=(DEFAULT, FINANCE))
    result = passed(
        plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[FLAT2])], cluster=reads(DEFAULT, FINANCE, document=on_the_bond))
    )

    assert result["network_changes"] is False
    assert result["warnings"] == [
        (
            "the frontend bond bond0 already carries 1 (Default) in tenant 'Default' and 2 (finance) in tenant 'finance', "
            "but the networks of one interface or VLAN belong to one tenant"
        )
    ]


# One IPv4 and one IPv6 default gateway (qumulo-findings item 35): the cluster refuses a second at step 3, after step 3a's
# creates, so step 2 refuses it first.

DEFAULT6 = net(4, "Default6", ["2001:db8::11-14"], default_gateway="2001:db8::1")
INV6 = net(3, "finance6", ["2001:db8:20::11-14"], vlan_id=20)


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
    ("default_networks", "finance_networks"),
    [
        pytest.param([INV1], [INV2], id="the VLAN network without one"),
        pytest.param([{**INV1, "default_gateway": ""}], [{**INV2, "default_gateway": "198.51.100.1"}], id="the one IPv4 gateway on the VLAN network"),
        pytest.param([INV1, DEFAULT6], [INV2, INV6], id="one of each"),
    ],
)
def test_one_default_gateway_of_each_ip_version_passes(run_module, default_networks, finance_networks):
    result = passed(plan(run_module, tenants=[entry("Default", networks=default_networks), entry("finance", networks=finance_networks)]))

    assert result["network_changes"] is True
    assert result["warnings"] == []


@pytest.mark.parametrize(
    ("default_networks", "finance_networks", "expected"),
    [
        pytest.param([INV1], [{**INV2, "default_gateway": "198.51.100.1"}], [(two_gateways("1 (Default) and 2 (finance)", 4), ["1", "2"])], id="two IPv4"),
        pytest.param(
            [INV1, DEFAULT6],
            [INV2, {**INV6, "default_gateway": "2001:db8:20::1"}],
            [(two_gateways("3 (finance6) and 4 (Default6)", 6), ["3", "4"])],
            id="two IPv6",
        ),
        pytest.param(
            [INV1, DEFAULT6],
            [{**INV2, "default_gateway": "198.51.100.1"}, {**INV6, "default_gateway": "2001:db8:20::1"}],
            [(two_gateways("1 (Default) and 2 (finance)", 4), ["1", "2"]), (two_gateways("3 (finance6) and 4 (Default6)", 6), ["3", "4"])],
            id="two of each",
        ),
    ],
)
def test_a_second_default_gateway_of_one_ip_version_is_refused_at_step_2(run_module, default_networks, finance_networks, expected):
    """Network 1 keeps the cluster's gateway, since the inventory leaves default_gateway unset on it. The refusal names the networks."""
    result = plan(run_module, tenants=[entry("Default", networks=default_networks), entry("finance", networks=finance_networks)])

    assert result["failed"] is True
    assert [(refusal["message"], refusal["names"]) for refusal in result["refusals"]] == expected


# Finance's network with a gateway of its own, which a cluster that does not enforce the rule may hold.
NET2_GATEWAY = static(2, "finance", ["198.51.100.11-14"], ["198.51.100.21-24"], tenant=2, gateway="198.51.100.1")
TWO_HELD = doc(NET1, NET2_GATEWAY, vlans=[vlan(20, 2, mtu=1500)], tenants=(DEFAULT, FINANCE))


def test_two_default_gateways_the_cluster_already_holds_warn_on_a_run_that_writes_the_document(run_module):
    networks = [{**INV2, "floating_ip_ranges": ["198.51.100.21-25"]}]
    result = passed(
        plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=networks)], cluster=reads(DEFAULT, FINANCE, document=TWO_HELD))
    )

    assert result["network_changes"] is True
    assert result["warnings"] == [held_gateways("1 (Default) and 2 (finance)", 4)]


def test_two_default_gateways_the_cluster_already_holds_warn_of_nothing_when_the_run_changes_no_document(run_module):
    """Without a network PUT the gateways are the cluster's own, and step 2 does not judge them."""
    result = passed(
        plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[INV2])], cluster=reads(DEFAULT, FINANCE, document=TWO_HELD))
    )

    assert result["network_changes"] is False
    assert result["warnings"] == []


# The VLANs and the bond (the plan's "Networks").

FINANCE6 = static(3, "finance6", ["2001:db8:20::11-14"], tenant=2, gateway="2001:db8:20::1", netmask="2001:db8:20::/64")


def test_an_existing_vlan_keeps_its_ipv6_network_first(run_module):
    finance = tenant(2, "finance", networks=[2, 3])
    document = doc(NET1, NET2, FINANCE6, vlans=[vlan(20, 3, secondary=2, mtu=1500)], tenants=(DEFAULT, finance))
    networks = [INV2, net(3, "finance6", ["2001:db8:20::11-14"], vlan_id=20)]
    result = passed(
        plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=networks)], cluster=reads(DEFAULT, finance, document=document))
    )

    assert interfaces(result)["frontend_vlans"] == [{"vlan_id": 20, "network_id": 3, "secondary_network_id": 2, "mtu": 1500}]
    assert result["network_changes"] is False


@pytest.mark.parametrize(
    ("networks", "expected"),
    [
        pytest.param(
            [net(3, "finance6", ["2001:db8:30::11-14"], vlan_id=30), net(2, "finance", ["198.51.100.11-14"], vlan_id=30)],
            {"vlan_id": 30, "network_id": 2, "secondary_network_id": 3},
            id="dual-stack, IPv4 first",
        ),
        pytest.param([net(3, "finance6", ["2001:db8:30::11-14"], vlan_id=30)], {"vlan_id": 30, "network_id": 3, "secondary_network_id": None}, id="IPv6 only"),
    ],
)
def test_a_new_vlan_takes_its_ipv4_network_first(run_module, networks, expected):
    result = passed(plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=networks)]))

    assert interfaces(result)["frontend_vlans"] == [expected]


NET5 = static(5, "lab", ["203.0.113.11-14"], tenant=1)
INV5 = net(5, "lab", ["203.0.113.11-14"])
LAB_DEFAULT = tenant(1, "Default", networks=[1, 5])
WITH_LAB = doc(NET1, NET2, NET5, vlans=[vlan(20, 2, mtu=1500), vlan(50, 5)], tenants=(LAB_DEFAULT, FINANCE))  # network 5 on VLAN 50


def test_without_frontend_vlans_the_cluster_s_vlans_of_flat_networks_are_kept(run_module):
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
            cluster=reads(LAB_DEFAULT, FINANCE, document=WITH_LAB),
            network={"frontend_networks": [INV5]},
        )
    )

    assert interfaces(result)["frontend_vlans"] == [VLAN20, {"vlan_id": 50, "network_id": 5}]
    assert result["network_changes"] is False


def test_a_retagged_tenant_network_leaves_its_old_vlan(run_module):
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[{**INV2, "vlan_id": 30}])],
            cluster=reads(DEFAULT, FINANCE, document=TWO),
        )
    )

    assert interfaces(result)["frontend_vlans"] == [{**VLAN20, "vlan_id": 30}]
    assert result["network_changes"] is True


def test_the_bond_drops_a_removed_and_a_renumbered_untagged_tenant_network(run_module):
    """Question 9: with frontend_bond_config.networks unset, the cluster bond's networks are the base."""
    default = tenant(1, "Default", networks=[1, 3, 6])
    document = doc(
        NET1,
        NET2,
        static(3, "Default-b", ["192.0.2.31-34"], tenant=1),
        static(6, "Default-c", ["192.0.2.61-64"], tenant=1),
        vlans=[vlan(20, 2, mtu=1500)],
        bond=(1, 3, 6),
        tenants=(default, FINANCE),
    )
    renumbered = net(4, "Default-b", ["192.0.2.31-34"])
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1, renumbered]), entry("finance", networks=[INV2])],
            cluster=reads(default, FINANCE, document=document),
            network={"managed_interfaces": {"frontend_bond_config": {"mtu": 9000}}},
        )
    )

    assert interfaces(result)["frontend_bond_config"] == {"mtu": 9000, "networks": [1, 4]}
    assert [item["id"] for item in result["network"]["frontend_networks"]] == [1, 4, 2]


def test_the_bond_starts_from_the_inventory_s_networks_when_it_gives_them(run_module):
    """The inventory's networks the union keeps, so network 7, which it lacks, is dropped."""
    given = {"frontend_bond_config": {"networks": [5, 7]}, "frontend_vlans": []}
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
            cluster=reads(LAB_DEFAULT, FINANCE, document=WITH_LAB),
            network={"frontend_networks": [INV5], "managed_interfaces": given},
        )
    )

    assert interfaces(result) == {"frontend_bond_config": {"networks": [1, 5]}, "frontend_vlans": [VLAN20]}


@pytest.mark.parametrize(
    ("networks", "message"),
    [
        pytest.param(
            [INV2, net(3, "finance-b", ["198.51.100.31-34"], vlan_id=20)],
            "VLAN 20 carries two IPv4 networks, 2 (finance) and 3 (finance-b); a VLAN carries one network of each address family.",
            id="two IPv4 networks",
        ),
        pytest.param(
            [INV2, net(3, "finance6", ["2001:db8:20::11-14"], vlan_id=20, mtu=9000)],
            "VLAN 20 is given two mtu values, 1500 by network 2 (finance) and 9000 by network 3 (finance6); give its networks one mtu.",
            id="two mtu values",
        ),
    ],
)
def test_a_vlan_s_networks_are_one_of_each_family_with_one_mtu(run_module, networks, message):
    """US1-11."""
    result = plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", networks=networks)], cluster=reads(DEFAULT, FINANCE, document=TWO))

    assert refusals(result) == [message]


@pytest.mark.parametrize(
    ("cluster", "flat", "given", "vlan_id", "message"),
    [
        pytest.param(
            reads(DEFAULT, FINANCE, document=TWO),
            None,
            {"frontend_vlans": [vlan(20, 2)]},
            20,
            (
                "VLAN 20 is in qumulo_network.managed_interfaces.frontend_vlans and is the vlan_id of network 2 of tenant "
                "finance; a VLAN is declared in one place. Remove it from frontend_vlans, or give the network another vlan_id."
            ),
            id="in frontend_vlans",
        ),
        pytest.param(
            reads(LAB_DEFAULT, FINANCE, document=WITH_LAB),
            [INV5],
            None,
            50,
            (
                "VLAN 50 carries network 5 of qumulo_network.frontend_networks, so the run keeps it, and is the vlan_id of "
                "network 2 of tenant finance; a VLAN is declared in one place. List the VLAN's networks under finance with "
                "vlan_id 50, or give the network another vlan_id."
            ),
            id="kept for the flat networks",
        ),
    ],
)
def test_a_vlan_id_declared_in_both_places_is_refused(run_module, cluster, flat, given, vlan_id, message):
    """Question 10."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[{**INV2, "vlan_id": vlan_id}])],
        cluster=cluster,
        network={"frontend_networks": flat, "managed_interfaces": given},
    )

    assert refusals(result) == [message]


# VLAN 20 carries finance's network 2 and network 6, which no tenant holds (issue #301).
NET6 = static(6, "spare6", ["2001:db8:20::11-14"], netmask="2001:db8:20::/64")
INV6 = net(6, "spare6", ["2001:db8:20::11-14"], netmask="2001:db8:20::/64")
MIXED = doc(NET1, NET2, NET6, vlans=[vlan(20, 2, secondary=6, mtu=1500)], tenants=(DEFAULT, FINANCE))
MIXED_REFUSAL = (
    "VLAN 20 carries network 6 (spare6), which no qumulo_tenants entry lists, beside network 2 of tenant finance, and the run derives a "
    "tenant network's VLAN from its vlan_id and puts network 6 on no interface, so it would take network 6 off VLAN 20. "
    "List network 6 (spare6) under finance with vlan_id 20, or give each network a VLAN of its own."
)


@pytest.mark.parametrize(
    ("finance", "interfaces", "flat_tenant"),
    [
        pytest.param(INV2, None, None, id="frontend_vlans unset"),
        pytest.param(INV2, {"frontend_vlans": []}, None, id="frontend_vlans empty"),
        pytest.param({**INV2, "vlan_id": 30}, None, None, id="the tenant network re-tagged"),
        pytest.param(INV2, None, "finance", id="network 6 flat with tenant finance"),
    ],
)
def test_a_live_vlan_that_also_carries_a_network_no_tenant_lists_is_refused_rather_than_rewritten(run_module, finance, interfaces, flat_tenant):
    """The derived VLAN would leave network 6 out, and the network would stay on the cluster on no interface. A flat
    entry's tenant does not help: only a network under a qumulo_tenants entry takes its VLAN from vlan_id."""
    flat = INV6 if flat_tenant is None else {**INV6, "tenant": flat_tenant}
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", networks=[finance])],
        cluster=reads(DEFAULT, FINANCE, document=MIXED),
        network={"frontend_networks": [flat], "managed_interfaces": interfaces},
    )

    assert result["failed"] is True
    assert refusals(result) == [MIXED_REFUSAL]
    assert "network_changes" not in result


# VLAN 30 carries networks 7 and 8, which no tenant holds, beside finance's VLAN 20.
NET7 = static(7, "spare7", ["203.0.113.71-74"])
INV7 = net(7, "spare7", ["203.0.113.71-74"])
NET8 = static(8, "spare8", ["2001:db8:30::11-14"], netmask="2001:db8:30::/64")
FLAT_PAIR = doc(NET1, NET2, NET7, NET8, vlans=[vlan(20, 2, mtu=1500), vlan(30, 7, secondary=8)], tenants=(DEFAULT, FINANCE))


@pytest.mark.parametrize(
    ("finance", "flat", "cluster", "message"),
    [
        pytest.param(
            [],
            [INV6],
            reads(DEFAULT, FINANCE, document=MIXED),
            (
                "VLAN 20 carries network 6 (spare6), which the run keeps but puts on no interface, so it would take network 6 "
                "off VLAN 20. Keep VLAN 20 with network 6 in qumulo_network.managed_interfaces.frontend_vlans, or list it "
                "under a tenant with vlan_id 20."
            ),
            id="the tenant network retired",
        ),
        pytest.param(
            [INV2],
            [INV7],
            reads(DEFAULT, FINANCE, document=FLAT_PAIR),
            (
                "VLAN 30 carries network 7 (spare7), which the run keeps but puts on no interface, so it would take network 7 "
                "off VLAN 30. Keep VLAN 30 with network 7 in qumulo_network.managed_interfaces.frontend_vlans, or list it "
                "under a tenant with vlan_id 30."
            ),
            id="the other flat network removed",
        ),
    ],
)
def test_a_live_vlan_whose_kept_network_lands_on_no_interface_is_refused_without_a_tenant_network_on_it(run_module, finance, flat, cluster, message):
    """The VLAN is neither kept, since a network it carries is not flat, nor derived, since no tenant network names it."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", networks=finance)],
        cluster=cluster,
        network={"frontend_networks": flat},
    )

    assert refusals(result) == [message]
    assert "network_changes" not in result


@pytest.mark.parametrize(
    ("finance", "network", "vlans"),
    [
        pytest.param([INV2], None, [VLAN20], id="network 6 removed"),
        pytest.param(
            [INV2],
            {"frontend_networks": [INV6], "managed_interfaces": {"frontend_vlans": [{"vlan_id": 60, "network_id": 6}]}},
            [VLAN20, {"vlan_id": 60, "network_id": 6}],
            id="network 6 on a VLAN of its own",
        ),
        pytest.param(
            [INV2, {**INV6, "vlan_id": 20}],
            None,
            [{**VLAN20, "secondary_network_id": 6}],
            id="network 6 listed under finance with vlan_id 20",
        ),
    ],
)
def test_a_live_vlan_whose_other_network_the_run_removes_or_puts_on_a_vlan_passes(run_module, finance, network, vlans):
    """VLAN 20 then loses no network the cluster keeps."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1]), entry("finance", networks=finance)],
            cluster=reads(DEFAULT, FINANCE, document=MIXED),
            network=network,
        )
    )

    assert interfaces(result)["frontend_vlans"] == vlans


HOSTED = doc(host(1, "Default", ["192.0.2.21-24"], tenant=1), managed=False)
HOST1 = net(1, "Default", floating=["192.0.2.21-24"], type="HOST")


def test_a_host_tenant_network_on_host_managed_networking_passes_with_no_interfaces(run_module):
    """US1-21. The management network is declared, as the role needs where status is unavailable (decision D26)."""
    finance = [net(2, "finance", floating=["198.51.100.21-24"], type="HOST")]
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[HOST1]), entry("finance", networks=finance)],
            cluster=reads(document=HOSTED, unavailable="HTTP 500 (G-NET-APPLY)"),
            management_network=[1],
        )
    )

    assert "managed_interfaces" not in result["network"]
    assert result["bindings"] == {"1": "Default", "2": "finance"}


@pytest.mark.parametrize(
    ("network_2", "message"),
    [
        pytest.param(
            net(2, "finance", floating=["198.51.100.21-24"], type="HOST", vlan_id=20),
            "network 2 of tenant finance has vlan_id 20, but the host manages this cluster's networking, so a tenant network takes no VLAN. Remove vlan_id.",
            id="vlan_id",
        ),
        pytest.param(
            net(2, "finance", ["198.51.100.11-14"]),
            "network 2 of tenant finance is STATIC, but the host manages this cluster's networking, so a tenant network is HOST. Give it type: HOST.",
            id="STATIC",
        ),
    ],
)
def test_a_tagged_or_static_tenant_network_on_host_managed_networking_is_refused(run_module, network_2, message):
    """US1-21."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[HOST1]), entry("finance", networks=[network_2])],
        cluster=reads(document=HOSTED, unavailable="HTTP 500 (G-NET-APPLY)"),
        management_network=[1],
    )

    assert refusals(result) == [message]


# The flat forms that carry tenants over (FR-012, FR-031).


def test_a_pre_feature_network_with_tenant_ids_on_two_tenants_carries_each_tenant_by_name(run_module):
    """An old inventory, with no qumulo_tenants, on a two-tenant cluster: not refused."""
    result = passed(
        plan(run_module, cluster=reads(DEFAULT, FINANCE, document=TWO), network={"frontend_networks": [{**INV1, "tenant_id": 1}, {**FLAT2, "tenant_id": 2}]})
    )

    assert result["network"]["frontend_networks"] == [option(INV1, "Default"), option(FLAT2, "finance")]
    assert result["network_changes"] is False
    assert result["warnings"] == [
        "qumulo_network.frontend_networks[0] (network 1): tenant_id 1 is translated to tenant Default; tenant_id is deprecated, so write tenant: Default.",
        "qumulo_network.frontend_networks[1] (network 2): tenant_id 2 is translated to tenant finance; tenant_id is deprecated, so write tenant: finance.",
    ]


def test_a_flat_network_s_tenant_id_draws_the_module_s_warning_alone():
    """US2-11 warns once per entry, so the option leaves Ansible's own deprecation warning of tenant_id to network_config."""
    options = tenancy_plan.ARGUMENT_SPEC["network"]["options"]["frontend_networks"]["options"]

    assert options["tenant_id"] == {"type": "int"}


def test_a_backend_network_s_tenant_id_is_dropped_with_a_warning_and_sent_with_no_tenant(run_module):
    """FR-013, FR-031: an old capture writes one."""
    default = tenant(1, "Default", networks=[1, 4])
    document = doc(NET1, static(4, "backend", ["10.0.0.11-14"], tenant=1), backend=BACKEND, tenants=(default,))
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default")],
            cluster=reads(default, document=document),
            network={"frontend_networks": [INV1, {**INV4, "tenant_id": 1}]},
        )
    )

    assert result["network"]["frontend_networks"] == [INV1, INV4]
    assert result["network_changes"] is False
    assert result["warnings"] == [
        "qumulo_network.frontend_networks[1] (network 4): network 4 is on the backend bond, which takes no tenant, so tenant_id 1 is dropped."
    ]


def test_a_flat_network_s_tenant_id_the_cluster_lacks_is_dropped_with_a_warning_and_the_entry_kept(run_module):
    """FR-031: the network keeps the tenant the cluster reports."""
    result = passed(plan(run_module, tenants=[entry("Default")], network={"frontend_networks": [{**INV1, "tenant_id": 9}]}))

    assert result["network"]["frontend_networks"] == [INV1]
    assert result["bindings"] == {"1": "Default"}
    assert result["warnings"] == [
        (
            "qumulo_network.frontend_networks[0] (network 1): tenant_id 9 is no tenant on the cluster, since tenant ids are "
            "never reused, so it is dropped and the network keeps the tenant the cluster reports; name its tenant with tenant."
        )
    ]


def test_a_flat_network_s_tenant_is_carried_into_the_document(run_module):
    """FR-012: a flat entry names its tenant as network_config takes it."""
    moved = {**FLAT2, "tenant": "Default"}
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("finance")],
            cluster=reads(DEFAULT, FINANCE, document=TWO),
            network={"frontend_networks": [INV1, moved]},
        )
    )

    assert result["network"]["frontend_networks"] == [INV1, moved]
    assert result["network_changes"] is True
    assert result["bindings"] == {"1": "Default", "2": "Default"}


def test_a_network_the_cluster_lacks_is_refused_where_the_run_does_not_own_networks(run_module):
    """Without networks in owned_parts a flat entry may set only floating addresses, on a network the cluster has.

    The refusal names the inventory's place, beside stage 4's outputs.
    """
    flat = [{"id": 1, "floating_ip_ranges": ["192.0.2.21-24"]}, {"id": 3, "floating_ip_ranges": ["192.0.2.51-54"]}]
    result = verbose_plan(run_module, tenants=[entry("Default")], network={"frontend_networks": flat}, owned_parts=["interfaces", "floating_ips"])

    assert refusals(result) == [
        "qumulo_network.frontend_networks[1]: network 3 is not on the cluster, and this run does not own networks, so it adds no network."
    ]
    assert pick(result, "smb", "nfs", "step_20a", "network") == {
        "smb": {"removals": [], "overrides": [], "shares": []},
        "nfs": {"removals": [], "overrides": [], "exports": []},
        "step_20a": [],
        "network": None,
    }


def test_a_tenant_s_networks_where_the_run_does_not_own_networks_are_refused_for_the_entry(run_module):
    """A tenant network is sent with its tenant, a field of networks, which the entry itself never writes."""
    floating = {"id": 1, "floating_ip_ranges": ["192.0.2.21-24"]}
    result = plan(run_module, tenants=[entry("Default", networks=[floating])], owned_parts=["interfaces", "floating_ips"])

    assert refusals(result) == [
        (
            "qumulo_tenants[0] (Default) declares networks, but owned_parts leaves out networks, so the run cannot give a "
            "network to a tenant. Add networks to owned_parts, or leave networks out of the entry."
        )
    ]


# The lifted merge: step 2 decides step 3's "differs" and bindings as network_config does (design section 6.4).


def network_config_view(fake_api, run_module, live, tenants, result):
    """network_config's own answer, in check mode, to step 3's document: (changed, network id -> tenant name).

    It serves the tenants as step 3 of a check run finds them, so a tenant the run creates resolves to Pending. A
    network network_config names no tenant on is left out, since bindings gives a null-tenant network to the only tenant.
    """
    fake = fake_api()
    fake.serve("/v3/network", live)
    fake.serve("/v1/multitenancy/tenants/", {"entries": list(tenants)}, optional=True)
    document = {key: result["network"][key] for key in ("frontend_networks", "managed_interfaces") if key in result["network"]}
    args = {
        "api_host": API_HOST,
        "api_password": "secret",
        "expected_cluster_uuid": CLUSTER_ID,
        **document,
        "validate": False,
        "allow_management_disruption": True,
    }
    answer = run_module(network_config, args, check_mode=True)
    assert not answer.get("failed"), answer.get("msg")
    return answer["changed"], {str(item["id"]): item["tenant"] for item in answer["network_config"]["frontend_networks"] if item["tenant"] is not None}


CIDR = static(1, "Default", ["192.0.2.1-14"], ["192.0.2.21-24"], tenant=1)  # 192.0.2.0/28 without its ends
DEFAULT_ON_20 = tenant(1, "Default", networks=[1, 2])


@pytest.mark.parametrize(
    ("entries", "flat", "given", "networks", "read"),
    [
        pytest.param(
            [entry("Default"), entry("finance")],
            [INV1, {**FLAT2, "floating_ip_ranges": ["198.51.100.21-29"]}],
            None,
            (NET1, NET2),
            (DEFAULT, FINANCE),
            id="a flat entry carried over",
        ),
        pytest.param(
            [entry("Default"), entry("finance")],
            None,
            {"frontend_vlans": [vlan(20, 2, mtu=9000)]},
            (NET1, NET2),
            (DEFAULT, FINANCE),
            id="frontend_networks unset",
        ),
        pytest.param(
            [entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
            None,
            {"frontend_bond_config": {"mtu": 1500}},
            (NET1, NET2),
            (DEFAULT, FINANCE),
            id="a bond-only managed_interfaces",
        ),
        pytest.param(
            [entry("Default", networks=[net(1, "Default", ["192.0.2.0/28"], ["192.0.2.21-24"])])], None, None, (CIDR,), (DEFAULT,), id="a CIDR block unchanged"
        ),
        pytest.param(
            [entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
            None,
            None,
            (NET1, {**NET2, "tenant_id": 1}),
            (DEFAULT_ON_20,),
            id="moved to a tenant the run creates",
        ),
        pytest.param([], [INV1], None, (NET1, {**NET2, "tenant_id": 1}), (DEFAULT_ON_20,), id="no qumulo_tenants, and a network removed"),
    ],
)
def test_step_2_decides_the_change_and_the_bindings_as_network_config_does(fake_api, run_module, entries, flat, given, networks, read):
    live = wire(*networks, vlans=[vlan(20, 2, mtu=1500)] if len(networks) > 1 else ())
    names = {t["id"]: t["name"] for t in read}
    document = network.named_document(network.canonical_document(live), names.get)
    result = passed(
        plan(run_module, tenants=entries, cluster=reads(*read, document=document), network={"frontend_networks": flat, "managed_interfaces": given})
    )

    changed, named = network_config_view(fake_api, run_module, live, read, result)

    assert (result["network_changes"], {key: value for key, value in result["bindings"].items() if key in named}) == (changed, named)
    assert named, "network_config named no tenant, so the bindings were not compared"


# No qumulo_tenants: every inventory from before this feature, which step 2 plans on every run of the role.

ONE_ON_20 = doc(NET1, {**NET2, "tenant_id": 1}, vlans=[vlan(20, 2, mtu=1500)], tenants=(DEFAULT_ON_20,))  # Default holds both networks


@pytest.mark.parametrize(
    "cluster",
    [
        pytest.param(reads(DEFAULT_ON_20, document=ONE_ON_20), id="the only tenant's"),
        pytest.param(reads(DEFAULT, FINANCE, document=TWO), id="a tenant of two"),
    ],
)
def test_without_qumulo_tenants_the_flat_list_removes_a_network_as_network_config_does(run_module, cluster):
    """Only a present entry's networks brings US1-16's refusal; the flat list alone is the whole set, as it is today."""
    flat = {"frontend_networks": [INV1], "managed_interfaces": {"frontend_vlans": []}}
    result = passed(plan(run_module, cluster=cluster, network=flat))

    assert pick(result["network"], "frontend_networks", "managed_interfaces") == flat
    assert result["network_changes"] is True
    assert result["bindings"] == {"1": "Default"}
    assert result["warnings"] == []


def test_without_qumulo_tenants_a_new_flat_network_goes_to_the_only_tenant(run_module):
    result = passed(plan(run_module, network={"frontend_networks": [INV1, FLAT2]}))

    assert pick(result["network"], "frontend_networks") == {"frontend_networks": [INV1, FLAT2]}
    assert result["network_changes"] is True
    assert result["bindings"] == {"1": "Default", "2": "Default"}


def test_without_qumulo_tenants_flat_shares_exports_and_the_factory_options_are_the_only_tenant_s(run_module):
    """A flat tenant_id is translated with a warning, and the factory absent claims the share for tenant 1."""
    result = passed(
        plan(
            run_module,
            cluster=reads(shares=[held_share("Files", 1)], exports=[held_export("/", 1)]),
            smb_shares=[share("home"), share("docs", tenant_id=1)],
            nfs_exports=[export("/home"), export("/old", tenant_id=1, state="absent")],
            factory_defaults={"smb_files_share": "absent", "nfs_root_export": "present"},
        )
    )

    assert result["smb"] == {
        "removals": [{"share_name": "Files", "tenant": "Default", "state": "absent"}],
        "overrides": [],
        "shares": [share("home"), {**share("docs"), "tenants": ["Default"]}],
    }
    assert result["nfs"] == {"removals": [{"export_path": "/old", "tenants": ["Default"], "state": "absent"}], "overrides": [], "exports": [export("/home")]}
    assert result["step_20a"] == []
    assert pick(result, "network", "network_changes", "bindings") == {"network": {}, "network_changes": False, "bindings": {"1": "Default"}}
    assert result["warnings"] == [
        "qumulo_smb_shares[1] (docs): tenant_id 1 is translated to tenant Default; tenant_id is deprecated, so write tenants: [Default].",
        "qumulo_nfs_exports[1] (/old): tenant_id 1 is translated to tenant Default; tenant_id is deprecated, so write tenants: [Default].",
    ]


@pytest.mark.parametrize(
    ("smb_shares", "factory", "message"),
    [
        pytest.param(
            [share("home"), share("HOME", tenant_id=1)],
            {},
            "Default's SMB share home comes from both qumulo_smb_shares[0] (home) and qumulo_smb_shares[1] (HOME); a share comes from one entry only.",
            id="with and without tenant_id",
        ),
        pytest.param(
            [share("Files", fs_path="/")],
            {"smb_files_share": "absent"},
            (
                "Default's SMB share Files comes from both qumulo_smb_shares[0] (Files) and qumulo_factory_defaults.smb_files_share; "
                "a share comes from one entry only."
            ),
            id="and the factory absent",
        ),
    ],
)
def test_without_qumulo_tenants_a_share_two_entries_claim_is_refused(run_module, smb_shares, factory, message):
    """FR-026, FR-037: on one tenant a flat entry with no tenant claims the only tenant's pair."""
    result = plan(run_module, cluster=reads(shares=[held_share("Files", 1)]), smb_shares=smb_shares, factory_defaults=factory)

    assert refusals(result) == [message]


# T1457a: the NFSv4.1 end state of each tenant (FR-016; US2-7, US2-18 and US2-19).

LEDGER = (held_export("/ledger", 2), held_export("/ledger/2026", 2))  # finance's exports, one inside the other
V4_OFF = {"finance": {"v4_enabled": False}}  # finance's override keeps NFSv4.1 off


def tenant_end_state(tenant_name, outer, inner):
    return (
        f"NFSv4.1 needs every export of tenant {tenant_name} outside every other, but after this run tenant {tenant_name} serves "
        f"NFSv4.1 with export '{outer}' containing '{inner}'. Remove or move one of them, or keep v4_enabled off for {tenant_name}."
    )


def global_enable(tenant_name, outer, inner):
    return (
        f"NFSv4.1 needs every export of tenant {tenant_name} outside every other, but qumulo_nfs_settings turns NFSv4.1 on while "
        f"export '{outer}' of tenant {tenant_name} contains '{inner}'; the cluster checks every tenant, one whose override keeps "
        "NFSv4.1 off included. Remove or move one of them first."
    )


def test_inherit_turning_nfsv4_on_over_nested_exports_is_refused(run_module):
    """US2-7: NFSv4.1 is on globally, and finance's override, which keeps it off, goes."""
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance", nfs_settings="inherit")],
        cluster=reads(DEFAULT, FINANCE, document=TWO, exports=LEDGER, overrides=V4_OFF, nfs={"v4_enabled": True}),
    )

    assert refusals(result) == [tenant_end_state("finance", "/ledger", "/ledger/2026")]


def test_turning_nfsv4_on_passes_once_the_run_removes_one_of_two_nested_exports(run_module):
    """US2-8."""
    finance = entry("finance", nfs_settings={"v4_enabled": True}, nfs_exports=[export("/ledger/2026", state="absent")])
    result = passed(plan(run_module, tenants=[entry("Default"), finance], cluster=reads(DEFAULT, FINANCE, document=TWO, exports=LEDGER, overrides=V4_OFF)))

    assert result["nfs"]["removals"] == [{"export_path": "/ledger/2026", "tenant": "finance", "state": "absent"}]


def test_exports_of_two_tenants_never_nest(run_module):
    """US2-9: / in Default and /home in finance, with NFSv4.1 turning on globally."""
    exports = (held_export("/", 1), held_export("/home", 2))
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance")],
        cluster=reads(DEFAULT, FINANCE, document=TWO, exports=exports),
        nfs_settings={"v4_enabled": True},
    )

    assert refusals(result) == []


def test_an_absent_tenant_s_nested_exports_do_not_hold_back_a_global_enable(run_module):
    """US2-18: step 19 removes finance's exports before it changes the global settings."""
    finance = entry("finance", state="absent", nfs_exports=[export("/ledger"), export("/ledger/2026")])
    result = plan(
        run_module,
        tenants=[entry("Default"), finance],
        cluster=reads(DEFAULT, tenant(2, "finance"), exports=LEDGER, overrides=V4_OFF),
        nfs_settings={"v4_enabled": True},
    )

    assert refusals(result) == []


@pytest.mark.parametrize("entries", [pytest.param([entry("Default")], id="undeclared"), pytest.param([entry("Default"), entry("finance")], id="declared")])
def test_a_global_enable_is_refused_while_a_tenant_s_exports_nest_whatever_its_override(run_module, entries):
    """US2-19: the cluster checks every tenant, finance's override keeping NFSv4.1 off included."""
    result = plan(
        run_module, tenants=entries, cluster=reads(DEFAULT, FINANCE, document=TWO, exports=LEDGER, overrides=V4_OFF), nfs_settings={"v4_enabled": True}
    )

    assert refusals(result) == [global_enable("finance", "/ledger", "/ledger/2026")]


def test_a_new_export_nested_in_one_of_a_tenant_serving_nfsv4_is_refused(run_module):
    result = plan(
        run_module,
        tenants=[entry("Default"), entry("finance", nfs_exports=[export("/ledger/2026")])],
        cluster=reads(DEFAULT, FINANCE, document=TWO, exports=LEDGER[:1], nfs={"v4_enabled": True}),
    )

    assert refusals(result) == [tenant_end_state("finance", "/ledger", "/ledger/2026")]


@pytest.mark.parametrize(
    ("qumulo_nfs_settings", "expected"),
    [
        pytest.param({"v4_enabled": True}, [tenant_end_state("hr", "/hr", "/hr/payroll")], id="turned on globally"),
        pytest.param(None, [], id="off globally"),
    ],
)
def test_a_created_tenant_s_override_without_v4_enabled_follows_the_global_end_state(run_module, qumulo_nfs_settings, expected):
    """Step 19 seeds a new override from the global settings it has just applied."""
    hr = entry("hr", nfs_settings={"auth_sys_enabled": True}, nfs_exports=[export("/hr"), export("/hr/payroll")])
    result = plan(run_module, tenants=[entry("Default"), hr], nfs_settings=qumulo_nfs_settings)

    assert refusals(result) == expected


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        pytest.param({"nfs_root_export": "absent"}, [], id="with the factory export removed"),
        pytest.param({}, [global_enable("Default", "/", "/scratch")], id="with it kept"),
    ],
)
def test_the_plan_s_example_turns_nfsv4_on_once_the_factory_export_goes(run_module, factory, expected):
    """The plan's "The inventory": the factory / export nests every other export of Default."""
    finance = entry("finance", networks=[INV2], nfs_settings={"v4_enabled": False}, nfs_exports=[export("/ledger", fs_path="/finance/ledger")])
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[INV1], nfs_settings="inherit"), finance, entry("retired", state="absent", networks=[])],
        cluster=reads(exports=[held_export("/", 1), held_export("/scratch", 1)]),
        nfs_exports=[export("/scratch", tenants=["Default", "finance"])],
        nfs_settings={"v4_enabled": True},
        factory_defaults=factory,
    )

    assert refusals(result) == expected


def test_a_tenant_created_under_a_renamed_tenant_s_old_name_has_no_override_yet(run_module):
    """Row 5 of the rename table, run for the first time: A becomes B and keeps its override, and the new A has none."""
    a = tenant(1, "A", networks=[1])
    new_a = entry("A", nfs_exports=[export("/x"), export("/x/y")])
    result = plan(
        run_module,
        tenants=[entry("B", previous_name="A"), new_a],
        cluster=reads(a, overrides={"A": {"v4_enabled": False}}, nfs={"v4_enabled": True}),
    )

    assert refusals(result) == [tenant_end_state("A", "/x", "/x/y")]


# T1454a: the run's writes and the continuity verdict of each judged write (spec 014, "The continuity rule"; FR-010,
# FR-011, FR-025, decisions D21 and D26).

CHECK_MODE_KEYS = ("management_network", "planned_networks", "planned_tenants", "content_removed")


def sent(item):
    """A step_3a, step_3b or step_20a item less the check-mode inputs: what tenant is asked to write."""
    return {key: value for key, value in item.items() if key not in CHECK_MODE_KEYS}


def verdicts(result):
    return [pick(verdict, "step", "write", "tenant", "management_network", "message") for verdict in result["verdicts"]]


def verdict(step, write, tenant, management, message=None):
    return {"step": step, "write": write, "tenant": tenant, "management_network": management, "message": message}


def address(network_id, node_address="", floating=(), assigned_by="STATIC"):
    """One qumulo_ip_addresses entry of /v3/network/status: a network's node address and floating addresses on a device."""
    return {"network_id": network_id, "assigned_by": assigned_by, "netmask": "", "address": node_address, "floating_addresses": list(floating)}


def status(*entries, host_addresses=()):
    """/v3/network/status, in the shape 7.11.0 answers, for one node whose one frontend device carries entries and host_addresses."""
    details = {"host_routable_ip_addresses": list(host_addresses), "qumulo_ip_addresses": list(entries), "use_for": "FRONTEND_AND_BACKEND"}
    return [{"node_id": 1, "node_name": "node1", "network_statuses": {}, "devices": [{"name": "eth0", "network_details": details}]}]


def resolving(monkeypatch, *addresses):
    """Stub the controller's resolver: api_host resolves to addresses, or to none."""
    if not addresses:

        def unresolved(*args, **kwargs):
            raise continuity.socket.gaierror(continuity.socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(continuity.socket, "getaddrinfo", unresolved)
        return
    answers = [
        (continuity.socket.AF_INET6 if ":" in text else continuity.socket.AF_INET, continuity.socket.SOCK_STREAM, 6, "", (text, 0)) for text in addresses
    ]
    monkeypatch.setattr(continuity.socket, "getaddrinfo", lambda host, port, **kw: answers)


NAT = "203.0.113.5"  # an address no network carries: the play reaches the cluster through NAT
NAT_STATUS = status(address(1, "192.0.2.11", ["192.0.2.21"]), address(2, "198.51.100.11"))  # status that places no NAT address
STATUS_UNAVAILABLE = "GET /v3/network/status failed: HTTP 500 net_read_error"
MOVED1 = net(1, "Default", ["192.0.2.31-34"], ["192.0.2.41-44"])  # network 1 away from API_HOST's address
FINANCE_REST_OFF = tenant(2, "finance", networks=[2], rest_api_enabled=False)


def test_each_judging_point_is_judged_after_its_own_write_in_run_order(run_module):
    """The spec's judging points: a create at 3a, the PUT at 3, a REST disable at 3b and a delete at 20a."""
    hr = tenant(3, "hr")
    result = passed(
        plan(
            run_module,
            tenants=[
                entry("Default", networks=[INV1]),
                entry("finance", web_ui_enabled=False, networks=[{**INV2, "floating_ip_ranges": ["198.51.100.21-29"]}]),
                entry("lab", rest_api_enabled=True, networks=[]),
                entry("hr", state="absent"),
            ],
            cluster=reads(DEFAULT, FINANCE, hr, document=TWO),
        )
    )

    assert verdicts(result) == [
        verdict("3a", "creating tenant lab", "lab", [1]),
        verdict("3", "this network change", None, [1]),
        verdict("3b", "setting web_ui_enabled: false on tenant finance", "finance", [1]),
        verdict("20a", "deleting tenant hr", "hr", [1]),
    ]
    assert result["network"]["management_network"] == [1]
    assert [item.get("management_network") for step in ("step_3a", "step_3b", "step_20a") for item in result[step]] == [[1], [1], [1]]


def test_a_rename_and_an_smb_toggle_behind_nat_on_two_tenants_are_not_judged(run_module):
    """US1-28: no judged write, so the management network is never looked up and status never read."""
    finance = tenant(2, "finance", networks=[2], smb_enabled=False)
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default"), entry("accounts", previous_name="finance", smb_enabled=True)],
            cluster=reads(DEFAULT, finance, document=TWO),
            api_host=NAT,
        )
    )

    assert result["verdicts"] == []
    assert result["step_3a"] == [{"name": "accounts", "previous_name": "finance", "smb_enabled": True}]


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        pytest.param([1], [], id="declared"),
        pytest.param(
            None,
            [
                (
                    "No network on the cluster carries 203.0.113.5 (203.0.113.5). If this play reaches the cluster through NAT or a "
                    "load balancer, set qumulo_management_network to the id of the network 203.0.113.5 is forwarded to."
                )
            ],
            id="undeclared",
        ),
    ],
)
def test_a_create_behind_nat_is_judged_on_the_declared_network(run_module, declared, expected):
    """US1-18: the variable is the network api_host is forwarded to; without it, message 5."""
    result = verbose_plan(run_module, tenants=[entry("finance")], cluster=reads(status=NAT_STATUS), api_host=NAT, management_network=declared)

    assert refusals(result) == expected
    assert verdicts(result)[0]["management_network"] == ([1] if declared else [])


def test_a_3a_write_finds_the_network_in_the_current_document_and_later_writes_in_the_planned_one(run_module):
    """API_HOST's address moves from network 1 to finance's new network 3 at step 3, then Default's REST goes off."""
    to_finance = net(3, "finance3", ["192.0.2.11-14"], vlan_id=30)
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", rest_api_enabled=False, networks=[MOVED1]), entry("finance", rest_api_enabled=True, networks=[to_finance])],
        )
    )

    assert verdicts(result) == [
        verdict("3a", "creating tenant finance", "finance", [1]),
        verdict("3", "this network change", None, [3]),
        verdict("3b", "setting rest_api_enabled: false on tenant Default", "Default", [3]),
    ]


def dhcp(network_id, name, floating=(), tenant=None):
    """A DHCP network in the wire shape of /v3/network."""
    block = {"floating_ip_ranges": list(floating), "netmask": None}
    return {"id": network_id, "name": name, "tenant_id": tenant, "addresses": {"type": "DHCP", "dhcp_addresses": block}}


def test_an_inventory_declaring_only_the_new_tenant_is_judged_through_status_on_a_dhcp_network(run_module):
    """A DHCP network's node address is in no range, so status places it (FR-011, decision D26)."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("finance", rest_api_enabled=True)],
            cluster=reads(document=doc(dhcp(1, "Default", tenant=1)), status=status(address(1, API_HOST, assigned_by="DHCP"))),
        )
    )

    assert verdicts(result) == [verdict("3a", "creating tenant finance", "finance", [1])]


HOSTED_STATUS = status(address(1, assigned_by="HOST"), host_addresses=[API_HOST])  # as a VM edge answers with HOST network 1


def test_a_host_network_carries_its_host_address_through_status(run_module):
    result = passed(plan(run_module, tenants=[entry("finance")], cluster=reads(document=HOSTED, status=HOSTED_STATUS)))

    assert verdicts(result) == [verdict("3a", "creating tenant finance", "finance", [1])]


def test_a_write_leaving_one_tenant_never_looks_up_the_management_network(run_module):
    """With one tenant after the write the rule asks only that the tenant has REST enabled; status is not read."""
    result = passed(plan(run_module, tenants=[entry("Default", networks=[{**INV1, "floating_ip_ranges": ["192.0.2.21-29"]}])], api_host=NAT))

    assert verdicts(result) == [verdict("3", "this network change", None, None)]


EMPTY_HOSTED = doc(managed=False)  # a fresh host-managed cluster: no managed interfaces and no frontend network
MESSAGE_2 = (
    "This run removes network {}, which carries this play's connection. List it under the networks of a tenant in "
    "qumulo_tenants that is not state: absent, or set qumulo_allow_management_disruption: true and qumulo_api_host_after "
    "to the address the play uses after the run."
)
DROPPED = (
    "Step 3's network document would drop {}, the address this play connects to, which would cut this play off from the "
    "cluster. Set qumulo_allow_management_disruption: true, and set qumulo_api_host_after to the address the play "
    "connects to after step 3."
)
HOSTED_TWO = doc(host(1, "Default", ["192.0.2.21-24"], tenant=1), host(2, "finance", ["198.51.100.21-24"], tenant=2), managed=False, tenants=(DEFAULT, FINANCE))
HOST2 = net(2, "finance", floating=["198.51.100.21-24"], type="HOST")
HOST3 = net(3, "Default3", floating=["192.0.2.31-34"], type="HOST")


def test_message_1_the_variable_on_a_fresh_host_managed_cluster(run_module):
    """US1-15: declare the HOST network and run once with one tenant first."""
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[HOST1]), entry("finance", networks=[])],
        cluster=reads(tenant(1, "Default"), document=EMPTY_HOSTED, unavailable=STATUS_UNAVAILABLE),
        management_network=[1],
    )

    assert refusals(result) == [
        (
            "The cluster's networking is host-managed and it has no frontend network yet, so with a second tenant REST would "
            "listen on no network. List its HOST network under one tenant in qumulo_tenants and run once before adding the "
            "second tenant, and, if this cluster's network status is unavailable, set qumulo_management_network to that "
            "network's id."
        )
    ]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(
            {"tenants": [entry("Default", networks=[INV1]), entry("finance", networks=[])], "management_network": [2]},
            [MESSAGE_2.format("2 (finance)")],
            id="the variable",
        ),
        pytest.param(
            {
                "tenants": [entry("Default", networks=[net(3, "Default3", ["192.0.2.31-34"])]), entry("finance", networks=[INV2])],
                "cluster": reads(DEFAULT, FINANCE, document=TWO, status=status(address(1, API_HOST))),
            },
            [MESSAGE_2.format("1 (Default)"), DROPPED.format(API_HOST)],
            id="a STATIC network",
        ),
        pytest.param(
            {
                "tenants": [entry("Default", networks=[HOST3]), entry("finance", networks=[HOST2])],
                "cluster": reads(DEFAULT, FINANCE, document=HOSTED_TWO, status=status(address(1, floating=["192.0.2.21"], assigned_by="HOST"))),
                "api_host": "192.0.2.21",
            },
            [MESSAGE_2.format("1 (Default)"), DROPPED.format("192.0.2.21")],
            id="a HOST network",
        ),
    ],
)
def test_message_2_the_run_removes_the_management_network(run_module, options, expected):
    """US1-30, and the network api_host's address is on."""
    result = plan(run_module, **{"cluster": reads(DEFAULT, FINANCE, document=TWO), **options})

    assert refusals(result) == expected


@pytest.mark.parametrize(
    ("default_networks", "expected"),
    [
        pytest.param(None, "qumulo_management_network is 7, which is neither on the cluster nor in this inventory.", id="never had"),
        pytest.param(
            [INV1, net(7, "lab", ["192.0.2.71-74"])],
            "qumulo_management_network is 7 (lab), which is not on the cluster. Add network 7 (lab) first, in a run whose "
            "inventory does not yet list tenant finance.",
            id="added by the run",
        ),
    ],
)
def test_message_3_the_variable_names_a_network_the_cluster_lacks(run_module, default_networks, expected):
    """US1-30; a create at 3a is judged before step 3 adds the network."""
    finance = entry("finance") if default_networks is None else entry("finance", networks=[])
    result = plan(run_module, tenants=[entry("Default", networks=default_networks), finance], management_network=[7])

    assert refusals(result) == [expected]


def test_message_4_a_create_on_a_management_network_unbound_by_hand(run_module):
    """US1-24: the create at 3a is judged on network 1, which no tenant holds, though step 3 would bind it again."""
    default = tenant(1, "Default", networks=[3])
    document = doc(static(1, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"]), static(3, "Default3", ["192.0.2.31-34"], tenant=1), bond=(1, 3))
    result = verbose_plan(
        run_module,
        tenants=[entry("Default", networks=[MOVED1, net(3, "Default3", ["192.0.2.11-14"])]), entry("finance", networks=[])],
        cluster=reads(default, document=document),
    )

    assert refusals(result) == [
        "Network 1 (Default) carries this play's connection but belongs to no tenant. List it under the networks of its tenant in qumulo_tenants."
    ]
    assert verdicts(result)[1] == verdict("3", "this network change", None, [3])


def test_message_5_a_name_that_resolves_to_no_address(run_module, monkeypatch):
    resolving(monkeypatch)
    result = plan(run_module, tenants=[entry("finance")], api_host="nowhere.example.com")

    assert refusals(result) == [
        "nowhere.example.com resolves to no address. Set qumulo_management_network to the id of the network this play reaches the cluster on."
    ]


MESSAGE_6_NOT_YET = (
    "HOST network 3 (finance3) is not on the cluster yet, so it cannot carry this play's connection. Add it in a run that "
    "leaves qumulo_management_network on the network this play uses now, then set qumulo_management_network to 3 "
    "(finance3) once status shows it on a device."
)
MESSAGE_6_NO_DEVICE = (
    "Status shows HOST network 1 (Default) on no device. A HOST network serves only on an interface the host associates "
    "with its id, and a node reads that association when it starts. If 1 (Default) was just created, run again once "
    "status shows it applied; otherwise set its id to the one the host associates with an interface."
)
OFF_DEVICE_STATUS = status()  # no frontend device has the host's addresses, so every HOST network is on no device


def test_message_6_a_host_network_the_run_creates_is_not_on_a_device_yet(run_module):
    finance3 = net(3, "finance3", floating=["198.51.100.31-34"], type="HOST")
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[HOST1]), entry("finance", networks=[HOST2, finance3])],
        cluster=reads(DEFAULT, FINANCE, document=HOSTED_TWO, status=OFF_DEVICE_STATUS),
        management_network=[3],
    )

    assert refusals(result) == [MESSAGE_6_NOT_YET]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param({"management_network": [1]}, MESSAGE_6_NO_DEVICE, id="declared"),
        pytest.param({"api_host": "192.0.2.21"}, MESSAGE_6_NO_DEVICE, id="its floating address"),
        pytest.param(
            {},
            "No network on the cluster carries 192.0.2.11 (192.0.2.11). If this play reaches the cluster through NAT or a load "
            "balancer, set qumulo_management_network to the id of the network 192.0.2.11 is forwarded to.",
            id="an address status does not show, message 5 first",
        ),
    ],
)
def test_message_6_a_host_management_network_on_no_device(run_module, options, expected):
    result = plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, FINANCE, document=HOSTED_TWO, status=OFF_DEVICE_STATUS), **options)

    assert refusals(result) == [expected]


def test_message_6_a_declared_host_network_while_the_node_lists_only_the_hosts_addresses(run_module):
    """A host-managed node lists the host's addresses with no HOST network served, as before the network applies."""
    unserved = status(host_addresses=[API_HOST])
    result = plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, FINANCE, document=HOSTED_TWO, status=unserved), management_network=[1])

    assert refusals(result) == [MESSAGE_6_NO_DEVICE]


def test_a_declared_host_network_shows_on_a_device_while_the_host_serves_one_of_two(run_module):
    """Status gives no network id beside the host's addresses, so with two HOST networks it shows neither on no device."""
    result = passed(plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, FINANCE, document=HOSTED_TWO, status=HOSTED_STATUS), management_network=[1]))

    assert verdicts(result) == [verdict("3a", "creating tenant hr", "hr", [1])]


FINANCE6 = static(3, "finance6", ["2001:db8:20::11-14"], tenant=2, gateway="2001:db8:20::1", netmask="2001:db8:20::/64")


@pytest.mark.parametrize(
    ("answers", "management"),
    [
        pytest.param(("198.51.100.11", "2001:db8:20::11"), [2, 3], id="a dual-stack name"),
        pytest.param((API_HOST, NAT), [1], id="a stray address on no network"),
    ],
)
def test_a_name_s_every_address_adds_its_network(run_module, monkeypatch, answers, management):
    """US1-17: a dual-stack name gives both networks of a VLAN; an address on no network adds nothing."""
    resolving(monkeypatch, *answers)
    finance = tenant(2, "finance", networks=[2, 3])
    document = doc(NET1, NET2, FINANCE6, vlans=[vlan(20, 2, 3, mtu=1500)], tenants=(DEFAULT, finance))
    result = passed(
        plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, finance, document=document, status=NAT_STATUS), api_host="cluster.example.com")
    )

    assert verdicts(result) == [verdict("3a", "creating tenant hr", "hr", management)]


def test_a_put_moving_api_host_s_address_to_a_tenant_with_rest_off_is_refused(run_module):
    """US1-20: step 3's write is judged on network 4, where the address ends."""
    moved = net(4, "moved", ["192.0.2.11-14"], vlan_id=40)
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[MOVED1]), entry("finance", networks=[INV2, moved])],
        cluster=reads(DEFAULT, FINANCE_REST_OFF, document=TWO),
    )

    assert refusals(result) == [
        (
            "This network change would leave network 4 (moved) in tenant finance with rest_api_enabled off, and this play would "
            "lose its connection. Set rest_api_enabled: true on finance, or list 4 (moved) under a tenant that has rest_api_enabled: true."
        )
    ]


def test_a_create_is_judged_on_the_current_network_when_the_inventory_renumbers_it(run_module):
    """US1-25: same addresses, new id; the create at 3a is judged on network 1, which Default holds."""
    renumbered = net(5, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"])
    result = passed(plan(run_module, tenants=[entry("Default", networks=[renumbered]), entry("finance", networks=[])]))

    assert verdicts(result) == [verdict("3a", "creating tenant finance", "finance", [1]), verdict("3", "this network change", None, [5])]


def test_a_single_tenant_rest_disable_is_refused(run_module):
    """US1-27."""
    result = verbose_plan(run_module, tenants=[entry("Default", rest_api_enabled=False)])

    assert refusals(result) == [
        (
            "Setting rest_api_enabled: false on tenant Default would leave tenant Default, the only tenant, with rest_api_enabled "
            "off, and this play would lose its connection. Set rest_api_enabled: true on Default."
        )
    ]
    assert verdicts(result) == [verdict("3b", "setting rest_api_enabled: false on tenant Default", "Default", None, refusals(result)[0])]


@pytest.mark.parametrize(
    ("default", "expected"),
    [
        pytest.param(DEFAULT, [], id="REST on"),
        pytest.param(
            tenant(1, "Default", networks=[1], rest_api_enabled=False),
            [
                (
                    "Deleting tenant hr would leave tenant Default, the only tenant, with rest_api_enabled off, and this play would "
                    "lose its connection. Set rest_api_enabled: true on Default."
                )
            ],
            id="REST off",
        ),
    ],
)
def test_a_delete_from_two_tenants_to_one_behind_nat_is_judged_on_the_survivor_alone(run_module, default, expected):
    """Decision D21; a one-line delete of a tenant without networks needs no variable, and status is not read."""
    result = verbose_plan(run_module, tenants=[entry("hr", state="absent")], cluster=reads(default, tenant(3, "hr")), api_host=NAT)

    assert refusals(result) == expected
    assert verdicts(result)[0]["management_network"] is None


def test_a_one_line_delete_of_a_tenant_with_networks_behind_nat_meets_message_5_at_the_put(run_module):
    """The PUT that removes finance's network still leaves two tenants, so the management network is needed there."""
    result = verbose_plan(
        run_module,
        tenants=[entry("Default", networks=[INV1]), entry("finance", state="absent", networks=[INV2])],
        cluster=reads(DEFAULT, FINANCE, document=TWO, status=NAT_STATUS),
        api_host=NAT,
    )

    assert refusals(result) == [
        (
            "No network on the cluster carries 203.0.113.5 (203.0.113.5). If this play reaches the cluster through NAT or a load "
            "balancer, set qumulo_management_network to the id of the network 203.0.113.5 is forwarded to."
        )
    ]
    assert [(v["step"], v["management_network"]) for v in result["verdicts"]] == [("3", []), ("20a", None)]


MESSAGE_5_UNAVAILABLE = (
    "The cluster's network status is unavailable (G-NET-APPLY), so the collection cannot tell which network carries {0} "
    "({0}), or whether a HOST network serves it. Set qumulo_management_network to the id of the network this play reaches "
    "the cluster on."
)
HOSTED_CHANGE = [entry("Default", networks=[HOST1]), entry("finance", networks=[{**HOST2, "floating_ip_ranges": ["198.51.100.21-29"]}])]


def test_status_unavailable_a_declared_host_network_is_trusted_without_the_host_check(run_module):
    """Decision D26, US1-31."""
    result = passed(
        plan(run_module, tenants=HOSTED_CHANGE, cluster=reads(DEFAULT, FINANCE, document=HOSTED_TWO, unavailable=STATUS_UNAVAILABLE), management_network=[1])
    )

    assert verdicts(result) == [verdict("3", "this network change", None, [1])]


@pytest.mark.parametrize("api_host", [pytest.param(API_HOST, id="on no range"), pytest.param("192.0.2.21", id="on a HOST floating range")])
def test_status_unavailable_without_the_variable_is_refused_with_message_5_s_third_text(run_module, api_host):
    result = plan(run_module, tenants=HOSTED_CHANGE, cluster=reads(DEFAULT, FINANCE, document=HOSTED_TWO, unavailable=STATUS_UNAVAILABLE), api_host=api_host)

    assert refusals(result) == [MESSAGE_5_UNAVAILABLE.format(api_host)]


@pytest.mark.parametrize("given", [pytest.param({"status": NAT_STATUS}, id="available"), pytest.param({"unavailable": STATUS_UNAVAILABLE}, id="unavailable")])
def test_a_static_management_network_needs_no_status(run_module, given):
    result = passed(plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, FINANCE, document=TWO, **given)))

    assert verdicts(result) == [verdict("3a", "creating tenant hr", "hr", [1])]


def test_status_needed_but_not_read_is_an_internal_error(run_module):
    """Decision D26: never message 5, since the role reads status whenever the cluster holds two tenants."""
    result = plan(run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, FINANCE, document=TWO), api_host=NAT)

    assert result["failed"] is True
    assert result["msg"] == (
        "internal error: the cluster_baseline role's step 2 did not read the cluster's network status, which the continuity "
        "rule needs to place 203.0.113.5; report this as a bug in the collection"
    )


@pytest.mark.parametrize(
    ("entries", "cluster", "due"),
    [
        pytest.param([{"name": "Default"}], [DEFAULT, FINANCE], True, id="two tenants on the cluster"),
        pytest.param([{"name": "Default"}, {"name": "finance"}], [DEFAULT], True, id="a create"),
        pytest.param([{"name": "finance", "previous_name": "Default", "state": "present"}], [DEFAULT], False, id="a rename"),
        pytest.param([{"name": "Default"}, {"name": "finance", "state": "absent"}], [DEFAULT], False, id="an absent entry the cluster lacks"),
        pytest.param([{"name": "Default", "ssh_enabled": False}], [DEFAULT], False, id="one tenant left one"),
        pytest.param([{"name": "B", "previous_name": "Default"}, {"name": "Default"}], [DEFAULT], True, id="a create of the name a rename frees"),
        pytest.param([{"name": "B", "previous_name": "A"}, {"name": "A", "previous_name": "X"}], [tenant(1, "X")], True, id="a chain of renames"),
    ],
)
def test_status_due_the_step_2_read_s_condition(entries, cluster, due):
    """Question 13 of the build design: the role computes it before tenancy_plan runs."""
    assert planning.status_due(entries, cluster) is due


def test_tenants_are_the_names_once_the_run_is_done(run_module):
    """The role counts them for FR-030's warning: the cluster's after the renames, sorted, then the creates."""
    cluster = reads(DEFAULT, tenant(2, "old"), tenant(3, "gone"))
    inventory = [entry("Default"), entry("zeta", previous_name="old"), entry("gone", state="absent"), entry("alpha")]

    result = passed(plan(run_module, tenants=inventory, cluster=cluster))

    assert result["tenants"] == ["Default", "zeta", "alpha"]


def test_tenants_with_no_tenant_write_are_the_cluster_s(run_module):
    result = passed(plan(run_module, tenants=[entry("Default")], cluster=reads(*THREE)))

    assert result["tenants"] == ["Default", "finance", "hr"]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param({}, [DROPPED.format(API_HOST)], id="a node address"),
        pytest.param({"cluster": reads(document=HOSTED), "api_host": "192.0.2.21"}, [DROPPED.format("192.0.2.21")], id="a HOST floating address"),
        pytest.param(
            {"allow_management_disruption": True},
            [
                (
                    f"Step 3's network document would drop {API_HOST}, the address this play connects to. Set "
                    "qumulo_api_host_after to the address the play connects to after step 3."
                )
            ],
            id="the flag without api_host_after",
        ),
        pytest.param({"allow_management_disruption": True, "api_host_after": "192.0.2.31"}, [], id="the flag with api_host_after"),
    ],
)
def test_the_address_check_of_step_3_runs_at_step_2(run_module, options, expected):
    """Design section 2.4; allow_management_disruption skips every verdict but not this check."""
    network_1 = HOST1 if "api_host" in options else MOVED1
    moved = {**network_1, "floating_ip_ranges": ["192.0.2.41-44"]}
    result = verbose_plan(run_module, **{"tenants": [entry("Default", networks=[moved])], **options})

    assert refusals(result) == expected
    assert result["verdicts"] == ([] if options.get("allow_management_disruption") else [verdict("3", "this network change", None, None)])


REST_OFF_REFUSED = (
    "Setting rest_api_enabled: false on tenant Default would leave network 1 (Default) in tenant Default with rest_api_enabled "
    "off, and this play would lose its connection. Set rest_api_enabled: true on Default, or list 1 (Default) under a "
    "tenant that has rest_api_enabled: true."
)
WEB_UI_OFF_REFUSED = (
    "Setting web_ui_enabled: false on tenant Default would leave network 1 (Default) in tenant Default with web_ui_enabled "
    "off, and this play would lose its connection. Set web_ui_enabled: true on Default, or list 1 (Default) under a tenant "
    "that has web_ui_enabled: true."
)


@pytest.mark.parametrize(
    ("ports", "switch", "expected"),
    [
        pytest.param({"api_port": 443, "cluster_port": 8000}, "rest_api_enabled", [REST_OFF_REFUSED], id="443 to 8000, REST"),
        pytest.param({"api_port": 443, "cluster_port": 8000}, "web_ui_enabled", [], id="443 to 8000, web UI"),
        pytest.param({"api_port": 8000, "cluster_port": 443}, "web_ui_enabled", [WEB_UI_OFF_REFUSED], id="8000 to 443, web UI"),
        pytest.param({"api_port": 8000, "cluster_port": 443}, "rest_api_enabled", [], id="8000 to 443, REST"),
        pytest.param({"api_port": 8000}, "web_ui_enabled", [], id="8000 to 443 undeclared, web UI"),
    ],
)
def test_the_cluster_side_port_picks_the_switch_rest_needs(run_module, ports, switch, expected):
    """Decision D21: a disable on the tenant holding the management network, on two tenants."""
    result = plan(run_module, tenants=[entry("Default", **{switch: False})], cluster=reads(DEFAULT, FINANCE, document=TWO), **ports)

    assert refusals(result) == expected


def fail_closed(write, switch):
    return (
        f"{write} would leave tenant Default, which holds this play's connection, with {switch} off. This play connects on "
        "port 443 and declares no cluster port, so the collection cannot tell whether it reaches the REST listener or the "
        "web UI listener, and needs both on. If nothing between this play and the cluster changes the port, set "
        "qumulo_cluster_port: 443; if a load balancer maps 443 to another port, set qumulo_cluster_port to that port."
    )


@pytest.mark.parametrize("switch", ["rest_api_enabled", "web_ui_enabled"])
def test_client_port_443_undeclared_refuses_either_disable_on_one_tenant(run_module, switch):
    """The rule fails closed: a load balancer may map 443 to 8000."""
    result = plan(run_module, tenants=[entry("Default", **{switch: False})], api_port=443)

    assert refusals(result) == [fail_closed(f"Setting {switch}: false on tenant Default", switch)]


@pytest.mark.parametrize(
    ("off", "cluster_port", "expected"),
    [
        pytest.param({"rest_api_enabled": False}, None, [fail_closed("This network change", "rest_api_enabled")], id="one off, undeclared"),
        pytest.param({"rest_api_enabled": False}, 443, [], id="one off, 443 declared"),
        pytest.param(
            {"rest_api_enabled": False, "web_ui_enabled": False},
            None,
            [
                (
                    "This network change would leave tenant Default, the only tenant, with rest_api_enabled and web_ui_enabled off, "
                    "and this play would lose its connection. Set rest_api_enabled: true and web_ui_enabled: true on Default."
                )
            ],
            id="both off",
        ),
    ],
)
def test_a_put_at_client_port_443_on_one_tenant(run_module, off, cluster_port, expected):
    result = plan(
        run_module,
        tenants=[entry("Default", networks=[{**INV1, "floating_ip_ranges": ["192.0.2.21-29"]}])],
        cluster=reads(tenant(1, "Default", networks=[1], **off)),
        api_port=443,
        cluster_port=cluster_port,
    )

    assert refusals(result) == expected


def test_a_flat_network_s_tenant_is_the_binding_judged(run_module):
    """FR-012: network 1 moves to finance, whose REST is off, through the flat list alone."""
    result = plan(
        run_module,
        cluster=reads(DEFAULT, FINANCE_REST_OFF, document=TWO),
        network={"frontend_networks": [{**INV1, "tenant": "finance"}, {**FLAT2, "tenant": "finance"}]},
    )

    assert refusals(result) == [
        (
            "This network change would leave network 1 (Default) in tenant finance with rest_api_enabled off, and this play would "
            "lose its connection. Set rest_api_enabled: true on finance, or list 1 (Default) under a tenant that has rest_api_enabled: true."
        )
    ]


def test_a_put_from_a_capture_with_tenants_left_out_passes_on_two_tenants(run_module):
    """The flat entries name their tenants, so the judged PUT binds each as the cluster has it."""
    widened = {**FLAT2, "floating_ip_ranges": ["198.51.100.21-29"], "tenant": "finance"}
    result = passed(plan(run_module, cluster=reads(DEFAULT, FINANCE, document=TWO), network={"frontend_networks": [{**INV1, "tenant": "Default"}, widened]}))

    assert verdicts(result) == [verdict("3", "this network change", None, [1])]


MOVED_TO_FINANCE = [entry("Default", rest_api_enabled=False, networks=[]), entry("finance", rest_api_enabled=True, networks=[INV1, INV2])]


@pytest.mark.parametrize(
    ("cluster", "first"),
    [
        pytest.param(
            reads(DEFAULT, FINANCE_REST_OFF, document=TWO), verdict("3a", "setting rest_api_enabled: true on tenant finance", "finance", [1]), id="enabled"
        ),
        pytest.param(reads(document=doc(NET1, NET2, vlans=[vlan(20, 2, mtu=1500)])), verdict("3a", "creating tenant finance", "finance", [1]), id="created"),
    ],
)
def test_a_disable_after_the_move_is_judged_on_the_planned_bindings(run_module, cluster, first):
    """US1-6: the management network moves to finance at 3, then Default's REST goes off at 3b."""
    result = passed(plan(run_module, tenants=MOVED_TO_FINANCE, cluster=cluster))

    assert verdicts(result) == [
        first,
        verdict("3", "this network change", None, [1]),
        verdict("3b", "setting rest_api_enabled: false on tenant Default", "Default", [1]),
    ]


def test_a_move_to_a_tenant_created_with_rest_off_is_refused_at_the_put(run_module):
    """US1-7: refused at step 2, before any write; the create itself passes, judged on the current bindings."""
    result = verbose_plan(run_module, tenants=[entry("Default", networks=[]), entry("finance", networks=[INV1])])

    assert refusals(result) == [
        (
            "This network change would leave network 1 (Default) in tenant finance with rest_api_enabled off, and this play would "
            "lose its connection. Set rest_api_enabled: true on finance, or list 1 (Default) under a tenant that has rest_api_enabled: true."
        )
    ]
    assert verdicts(result)[0] == verdict("3a", "creating tenant finance", "finance", [1])


def test_the_switch_split_enables_at_3a_disables_at_3b_and_a_create_sends_every_set_switch(run_module):
    """The order renames, enables, creates at 3a; a renamed tenant's later item finds it under its old name."""
    default = tenant(1, "Default", networks=[1], ssh_enabled=False)
    finance = tenant(2, "finance", networks=[2], nfs_enabled=False)
    result = passed(
        plan(
            run_module,
            tenants=[
                entry("Default", ssh_enabled=True, replication_enabled=False, smb_enabled=True),
                entry("hr", web_ui_enabled=False, nfs_enabled=True),
                entry("accounts", previous_name="finance", nfs_enabled=True, smb_enabled=False),
            ],
            cluster=reads(default, finance, document=TWO),
        )
    )

    assert [sent(item) for item in result["step_3a"]] == [
        {"name": "accounts", "previous_name": "finance", "nfs_enabled": True},
        {"name": "Default", "ssh_enabled": True},
        {"name": "hr", "web_ui_enabled": False, "nfs_enabled": True},
    ]
    assert [sent(item) for item in result["step_3b"]] == [
        {"name": "Default", "replication_enabled": False},
        {"name": "accounts", "previous_name": "finance", "smb_enabled": False},
    ]
    assert [verdict["write"] for verdict in result["verdicts"]] == ["creating tenant hr"]


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        pytest.param((API_HOST, "192.0.2.12"), [], id="a name giving two addresses"),
        pytest.param(None, [], id="a literal address"),
        pytest.param(
            (),
            ["cluster.example.com resolves to no address. Set qumulo_management_network to the id of the network this play reaches the cluster on."],
            id="a name giving none",
        ),
    ],
)
def test_api_host_is_resolved_on_the_controller(run_module, monkeypatch, answers, expected):
    if answers is None:
        monkeypatch.setattr(continuity.socket, "getaddrinfo", lambda *a, **kw: pytest.fail("a literal address needs no lookup"))
    else:
        resolving(monkeypatch, *answers)
    result = plan(
        run_module, tenants=[entry("hr")], cluster=reads(DEFAULT, FINANCE, document=TWO), api_host=API_HOST if answers is None else "cluster.example.com"
    )

    assert refusals(result) == expected


# T1455a: each writer's check-mode inputs, as they will be immediately after that writer's write, and FR-027's validate.


def inputs_of(item):
    return pick(item, "management_network", "planned_networks", "planned_tenants")


@pytest.mark.parametrize(
    "cluster",
    [
        pytest.param(reads(DEFAULT, FINANCE_REST_OFF, document=TWO), id="enabled at 3a"),
        pytest.param(reads(document=doc(NET1, NET2, vlans=[vlan(20, 2, mtu=1500)])), id="created at 3a"),
    ],
)
def test_a_disable_after_the_move_takes_the_planned_bindings_and_services(run_module, cluster):
    """US1-6: Default's 3b disable sees network 1 under finance, finance's REST on, and its own REST off."""
    result = passed(plan(run_module, tenants=MOVED_TO_FINANCE, cluster=cluster))

    assert [inputs_of(item) for item in result["step_3b"]] == [
        {"management_network": [1], "planned_networks": {"1": "finance", "2": "finance"}, "planned_tenants": {"Default": False, "finance": True}}
    ]


@pytest.mark.parametrize(
    ("tenants", "cluster", "expected"),
    [
        pytest.param(
            [entry("Default", networks=[MOVED1, net(3, "Default3", ["192.0.2.11-14"])]), entry("finance", networks=[])],
            reads(
                tenant(1, "Default", networks=[3]),
                document=doc(static(1, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"]), static(3, "Default3", ["192.0.2.31-34"], tenant=1), bond=(1, 3)),
            ),
            {"management_network": [1], "planned_networks": {"3": "Default"}, "planned_tenants": {"Default": True, "finance": False}},
            id="US1-24",
        ),
        pytest.param(
            [entry("Default", networks=[net(5, "Default", ["192.0.2.11-14"], ["192.0.2.21-24"])]), entry("finance", networks=[])],
            reads(),
            {"management_network": [1], "planned_networks": {"1": "Default"}, "planned_tenants": {"Default": True, "finance": False}},
            id="US1-25",
        ),
    ],
)
def test_a_create_at_3a_takes_its_inputs_on_the_current_document(run_module, tenants, cluster, expected):
    """The create is written before step 3's PUT, so its tenant judges it on the cluster's bindings."""
    result = verbose_plan(run_module, tenants=tenants, cluster=cluster)

    assert [inputs_of(item) for item in result["step_3a"]] == [expected]


@pytest.mark.parametrize(
    ("allow", "expected"),
    [
        pytest.param(
            False,
            {"management_network": None, "planned_networks": {"1": "Default", "2": "Default"}, "planned_tenants": {"Default": True}, "content_removed": True},
            id="judged",
        ),
        pytest.param(
            True, {"management_network": None, "planned_networks": None, "planned_tenants": None, "content_removed": True}, id="allow_management_disruption"
        ),
    ],
)
def test_a_delete_takes_content_removed_and_the_bindings_without_its_tenant(run_module, allow, expected):
    """finance's network moves to Default at step 3, and the delete leaves one tenant, so no management network."""
    result = passed(
        plan(
            run_module,
            tenants=[entry("Default", networks=[INV1, INV2]), entry("finance", state="absent", networks=[])],
            cluster=reads(DEFAULT, FINANCE, document=TWO),
            allow_management_disruption=allow,
            api_host_after=API_HOST,
        )
    )

    assert [pick(item, "management_network", "planned_networks", "planned_tenants", "content_removed") for item in result["step_20a"]] == [expected]


def test_step_3_takes_the_services_after_3a_and_its_own_management_network(run_module):
    """The plan's example: finance is created at 3a, so step 3's write judges it, with its REST off."""
    result = passed(plan(run_module, tenants=[entry("Default", networks=[INV1]), entry("finance", smb_enabled=True, networks=[INV2])]))

    assert pick(result["network"], "management_network", "planned_tenants", "planned_networks") == {
        "management_network": [1],
        "planned_tenants": {"Default": True, "finance": False},
        "planned_networks": None,
    }


@pytest.mark.parametrize(
    ("ports", "expected"),
    [
        pytest.param({}, {"Default": True, "hr": False, "lab": True}, id="8000: rest_api_enabled"),
        pytest.param({"api_port": 443, "cluster_port": 443}, {"Default": True, "hr": True, "lab": False}, id="443 declared: web_ui_enabled"),
        pytest.param({"api_port": 443}, {"Default": True, "hr": False, "lab": False}, id="443 undeclared: both"),
    ],
)
def test_planned_tenants_is_rest_enabled_under_each_port_rule(run_module, ports, expected):
    """Decision D21: REST enabled is the switch, or both, that the cluster-side port needs."""
    created = [entry("hr", web_ui_enabled=True), entry("lab", rest_api_enabled=True)]
    result = passed(plan(run_module, tenants=created, cluster=reads(DEFAULT, FINANCE, document=TWO), **ports))

    assert result["step_3a"][-1]["planned_tenants"] == {"finance": True, **expected}


@pytest.mark.parametrize(
    ("tenants", "cluster", "network_changes", "validate"),
    [
        pytest.param(
            [entry("Default", networks=[INV1]), entry("finance", networks=[INV2])],
            reads(document=doc(NET1, {**NET2, "tenant_id": None}, vlans=[vlan(20, 2, mtu=1500)])),
            True,
            True,
            id="a network another writer unassigned, listed under a new tenant",
        ),
        pytest.param(
            [entry("Default", networks=[INV1]), entry("finance", networks=[{**INV2, "floating_ip_ranges": ["198.51.100.21-29"]}])],
            reads(DEFAULT, FINANCE, document=TWO),
            True,
            False,
            id="no write at 3a",
        ),
        pytest.param([entry("hr", rest_api_enabled=True)], reads(DEFAULT, FINANCE, document=TWO), False, False, id="the document matches"),
    ],
)
def test_step_2_validates_step_3_s_document_when_3a_writes_first(run_module, tenants, cluster, network_changes, validate):
    """FR-027: a 3a write lands before step 3, so step 2 has network_config check the document with step 3's inputs."""
    result = passed(plan(run_module, tenants=tenants, cluster=cluster))

    assert (result["network_changes"], result["validate"]) == (network_changes, validate)


# T1456a: the cloud refusal, through upgrade_path.host_kind (FR-026, decision D21).

OBJECT_BACKED, COLD = "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "PROTECTION_SYSTEM_TYPE_COLD_OBJECT_BACKED"


def cloud(why, writes):
    return (
        f"This is a cloud cluster ({why}), and no cloud cluster supports tenancy: Qumulo Core refuses {writes} there. Remove "
        "them from qumulo_tenants. A cluster on premises whose storage is object-backed declares qumulo_cluster_kind: baremetal."
    )


@pytest.mark.parametrize(
    ("kind", "protection"),
    [
        pytest.param("baremetal", None, id="baremetal"),
        pytest.param("baremetal", OBJECT_BACKED, id="object-backed, declared baremetal"),
        pytest.param(None, "PROTECTION_SYSTEM_TYPE_EC", id="unmarked erasure-coded"),
        pytest.param(None, None, id="unmarked, protection unread"),
    ],
)
def test_a_second_tenant_on_a_cluster_on_premises_is_not_refused(run_module, kind, protection):
    kind_given = {} if kind is None else {"cluster_kind": kind}  # the role omits an unset qumulo_cluster_kind
    result = passed(plan(run_module, tenants=[entry("Default"), entry("finance")], cluster=reads(protection=protection), **kind_given))

    assert verdicts(result) == [verdict("3a", "creating tenant finance", "finance", [1])]


@pytest.mark.parametrize("protection", [OBJECT_BACKED, COLD])
def test_a_second_tenant_on_an_unmarked_object_backed_cluster_is_refused(run_module, protection):
    result = plan(run_module, tenants=[entry("Default"), entry("finance")], cluster=reads(protection=protection))

    assert refusals(result) == [cloud(f"its protection type, {protection}, is object-backed", "creating tenant finance")]
    assert "verdicts" not in result


@pytest.mark.parametrize("allow", [False, True])
def test_a_switch_change_on_a_cnq_cluster_is_refused(run_module, allow):
    """allow_management_disruption skips the continuity rule, not the platform's refusal."""
    result = plan(run_module, tenants=[entry("Default", ssh_enabled=False)], cluster_kind="cnq", allow_management_disruption=allow)

    assert refusals(result) == [cloud("qumulo_cluster_kind: cnq", "setting ssh_enabled: false on tenant Default")]


def test_every_tenant_write_on_a_cloud_cluster_is_listed(run_module):
    result = plan(
        run_module,
        tenants=[entry("main", previous_name="Default", rest_api_enabled=True), entry("hr", state="absent"), entry("lab", smb_enabled=False)],
        cluster=reads(DEFAULT, tenant(3, "hr")),
        cluster_kind="cnq",
    )

    assert refusals(result) == [cloud("qumulo_cluster_kind: cnq", "renaming tenant Default to main, creating tenant lab and deleting tenant hr")]


def test_a_rename_on_a_cloud_cluster_lists_the_switches_it_sends(run_module):
    """An operator who removes only the rename would be refused again for the enable."""
    result = plan(
        run_module,
        tenants=[entry("main", previous_name="Default", ssh_enabled=True)],
        cluster=reads(tenant(1, "Default", networks=[1], ssh_enabled=False)),
        cluster_kind="cnq",
    )

    assert refusals(result) == [cloud("qumulo_cluster_kind: cnq", "renaming tenant Default to main with ssh_enabled: true")]


def test_a_network_change_alone_on_a_cnq_cluster_is_not_refused(run_module):
    """The network PUT is no tenant write; a cloud cluster has one tenant, judged as any single-tenant cluster is."""
    result = passed(plan(run_module, tenants=[entry("Default", networks=[{**INV1, "floating_ip_ranges": ["192.0.2.21-29"]}])], cluster_kind="cnq"))

    assert verdicts(result) == [verdict("3", "this network change", None, None)]


def test_a_tenant_write_the_cloud_check_refuses_is_not_also_judged(run_module):
    """An unmarked object-backed cluster reached on no network, with a second tenant: the cloud message alone, not message 5."""
    result = plan(run_module, tenants=[entry("Default"), entry("finance")], cluster=reads(protection=OBJECT_BACKED), api_host=NAT)

    assert refusals(result) == [cloud(f"its protection type, {OBJECT_BACKED}, is object-backed", "creating tenant finance")]


def test_the_cloud_refusal_comes_before_the_rules_on_the_tenant_it_refuses(run_module):
    """A flat share with no tenants is refused on a run with two tenants, but on CNQ the second tenant is the problem."""
    result = plan(run_module, tenants=[entry("Default"), entry("finance")], smb_shares=[share("Data")], cluster_kind="cnq")

    assert refusals(result) == [cloud("qumulo_cluster_kind: cnq", "creating tenant finance")]


# T1476: the edge_rebuild preflight's model of step 2 on a newly formed edge, held to plan() on such an edge.

HOST1 = {"id": 1, "name": "Default", "type": "HOST"}
FORMED = reads(
    tenant(1, "Default", networks=[1]),
    document=doc(host(1, "Default", tenant=1), managed=False),
    shares=[{"share_name": "Files", "fs_path": "/", "tenant_id": 1}],
    exports=[{"export_path": "/", "fs_path": "/", "tenant_id": 1}],
)


@pytest.mark.parametrize(
    "inventory",
    [
        {"tenants": [entry("Default"), entry("eng", smb_shares=[share("builds")], nfs_exports=[export("/eng")])], "smb_shares": [share("home", tenants="all")]},
        {"tenants": [entry("Corp", previous_name="Default")], "smb_shares": [share("projects")]},
        {"smb_shares": [share("projects", tenant_id=1), share("builds", tenant_id=2)]},
        {"tenants": [entry("Default"), entry("eng", state="absent", smb_shares=[share("old")])], "nfs_exports": [export("/tmp", tenants="all")]},
        {"smb_shares": [share("Files", tenants=["Default"])], "factory_defaults": {"smb_files_share": "absent"}},
        {"smb_shares": [share("x", tenants=["Default"]), share("x", tenants=["Default"])]},
        {"tenants": [entry("x"), entry("x")]},
        {"tenants": [entry("Default", state="absent")]},
        {"tenants": [entry("eng")], "smb_shares": [share("projects", tenant="eng")]},
        {"tenants": [entry("eng")], "smb_shares": [share("projects")]},
        {"tenants": [entry("Default", networks=[HOST1]), entry("eng", networks=[])]},
        {"tenants": [entry("eng")], "network": {"frontend_networks": [HOST1]}},
        {"tenants": [entry("eng")], "network": {"frontend_networks": [{**HOST1, "tenant": "eng"}]}},
        {"tenants": [entry("eng")], "network": {"frontend_networks": [{**HOST1, "tenant_id": 1}]}},
        {"tenants": [entry("Default"), entry("eng", networks=[HOST1])]},
        {"tenants": [entry("Default", networks=[HOST1])], "network": {"frontend_networks": [HOST1]}},
        {"tenants": [entry("Default"), entry("eng", state="absent", networks=[HOST1])], "network": {"frontend_networks": [HOST1]}},
        {"tenants": [entry("Default", networks=[HOST1])], "owned_parts": ["interfaces", "floating_ips"]},
        {"tenants": [entry("Default", networks=[{**HOST1, "floating_ip_ranges": ["192.0.2.50"]}])], "owned_parts": ["interfaces", "networks"]},
        {"tenants": [entry("Default", networks=[{**HOST1, "floating_ip_ranges": ["192.0.2.50"]}])]},
    ],
    ids=[
        "nested-and-all",
        "rename-default",
        "tenant-id",
        "absent-tenant",
        "factory-claim",
        "pair-twice",
        "tenant-twice",
        "no-tenant-left",
        "singular-tenant",
        "no-tenants-two-tenants",
        "nested-networks",
        "flat-network-no-tenant",
        "flat-network-tenant",
        "flat-network-tenant-id",
        "networks-left-out",
        "network-twice",
        "absent-tenants-network-kept-flat",
        "networks-not-owned",
        "floating-addresses-not-owned",
        "floating-addresses-owned",
    ],
)
def test_replay_judges_the_inventory_as_plan_does_on_a_newly_formed_edge(run_module, inventory):
    """Up to the claims and the network rules that need no read: replay() leaves out the cloud refusal, the NFSv4.1 end
    state, the network document and the continuity verdicts, which need the new edge's own reads, so plan() judges no
    write here."""
    result = plan(run_module, cluster=FORMED, allow_management_disruption=True, **inventory)
    modeled = planning.replay(
        inventory.get("tenants", []),
        inventory.get("smb_shares", []),
        inventory.get("nfs_exports", []),
        inventory.get("factory_defaults"),
        inventory.get("network"),
        inventory.get("owned_parts"),
    )

    assert [refusal.message for refusal in modeled.findings.refusals] == refusals(result)
    assert set(modeled.findings.warnings) <= set(result.get("warnings", []))
    if not modeled.findings.refusals:
        assert modeled.smb.items == result["smb"]["shares"]
        assert modeled.nfs.items == result["nfs"]["exports"]
        assert {str(network_id): tenant for network_id, tenant in (modeled.bindings or {}).items()} == {
            network_id: tenant for network_id, tenant in result["bindings"].items() if int(network_id) in (modeled.bindings or {})
        }


def test_names_after_renames_reads_a_rename_to_do_and_leaves_one_done():
    held = [tenant(1, "Default"), tenant(2, "engineering"), tenant(3, "ops"), tenant(4, "operations")]
    tenants = [entry("eng", previous_name="engineering"), entry("ops", previous_name="operations")]

    assert planning.names_after_renames(tenants, held) == {1: "Default", 2: "eng", 3: "ops", 4: "operations"}


# T1473a: the round trips of a capture with the tenants area left out on two tenants (plan, "tenancy_plan"). The capture
# is fixtures_tenancy.TENANTS_LEFT_OUT, which test_capture_info.py checks capture_info writes of fixtures_tenancy.TWO.
def captured_reads(*tenants, document=None):
    """What step 2 reads of a cluster holding tenants and the wire document, the captured cluster unless given."""
    names = {listed["id"]: listed["name"] for listed in tenants}
    return reads(*tenants, document=network.named_document(network.canonical_document(document or CAPTURED_TWO), names.get))


def test_a_capture_with_tenants_left_out_replayed_onto_its_cluster_changes_nothing(run_module):
    result = passed(plan(run_module, cluster=captured_reads(CAPTURED_CORP, CAPTURED_FINANCE), network=TENANTS_LEFT_OUT))

    assert result["network_changes"] is False
    assert result["step_3a"] == result["step_3b"] == []


def test_a_capture_with_tenants_left_out_replayed_onto_a_rebuilt_cluster_binds_each_network_to_its_named_tenant(run_module):
    """The rebuilt cluster has the tenants, which their owner made, and only the network the controller reaches."""
    rebuilt = wire_document(CAPTURED_NET1, vlans=[])
    tenants = (listed_tenant(1, "Corp", networks=[1]), listed_tenant(2, "finance", ssh_enabled=False))

    result = passed(plan(run_module, cluster=captured_reads(*tenants, document=rebuilt), network=TENANTS_LEFT_OUT))

    assert result["network_changes"] is True
    assert result["bindings"] == {"1": "Corp", "2": "finance", "3": "finance"}


def test_a_capture_with_tenants_left_out_binds_again_a_network_another_writer_unassigned(run_module):
    unassigned = wire_document(CAPTURED_NET1, {**CAPTURED_NET2, "tenant_id": None}, CAPTURED_NET3, vlans=VLANS)
    finance = listed_tenant(2, "finance", networks=[3], ssh_enabled=False)

    result = passed(plan(run_module, cluster=captured_reads(CAPTURED_CORP, finance, document=unassigned), network=TENANTS_LEFT_OUT))

    assert result["network_changes"] is True
    assert result["bindings"]["2"] == "finance"


def test_a_capture_with_tenants_left_out_is_refused_giving_the_name_of_a_tenant_renamed_outside_the_inventory(run_module):
    """Decision D23: another owner of tenants renamed finance to accounts, which leaves the captured name stale."""
    accounts = {**CAPTURED_FINANCE, "name": "accounts"}

    result = plan(run_module, cluster=captured_reads(CAPTURED_CORP, accounts), network=TENANTS_LEFT_OUT)

    assert result["failed"] is True
    assert any("finance" in message and "qumulo_network.frontend_networks[1]" in message for message in refusals(result)), refusals(result)


# The edge_rebuild replay's tenant 1 pass (issue #306): on a freshly formed host-managed edge, step 2 run with what
# first_pass gives accepts it, and the baseline's step 2 after it, on the document the pass leaves (every sent network
# HOST and in tenant 1), binds each network to its tenant with no refusal. The status is unavailable, as the container's
# is, so the management network is the variable's.
def hosted_net(network_id, name, **keys):
    return {"id": network_id, "name": name, "type": "HOST", **keys}


ON = dict.fromkeys(SWITCHES, True)
FORMED_TENANTS = [{"id": 1, "name": "Default"}]
FACTORY_HELD = {"shares": (held_share("Files", 1),), "exports": (held_export("/", 1),)}
PASS_SHAPES = {
    "tenant 1 renamed with its network": (
        [entry("eng", previous_name="Default", networks=[hosted_net(0, "eng")], **ON), entry("ops", networks=[], **ON)],
        None,
    ),
    "tenant 1 undeclared": ([entry("eng", networks=[hosted_net(0, "eng")], **ON), entry("ops", networks=[], **ON)], None),
    "tenant 1 absent": ([entry("Default", state="absent"), entry("eng", networks=[hosted_net(0, "eng")], **ON), entry("ops", networks=[], **ON)], None),
    "a second tenant's own HOST network": (
        [entry("eng", previous_name="Default", networks=[hosted_net(0, "eng")], **ON), entry("ops", networks=[hosted_net(1, "ops")], **ON)],
        None,
    ),
    "flat networks named for each tenant": (
        [entry("eng", previous_name="Default", **ON), entry("ops", **ON)],
        {"frontend_networks": [hosted_net(0, "eng", tenant="eng"), hosted_net(1, "ops", tenant="ops")]},
    ),
    "tenant 1 absent, flat networks named": (
        [entry("Default", state="absent"), entry("eng", **ON), entry("ops", **ON)],
        {"frontend_networks": [hosted_net(0, "eng", tenant="eng"), hosted_net(1, "ops", tenant="ops")]},
    ),
}


@pytest.mark.parametrize("shape", list(PASS_SHAPES))
def test_the_tenant_1_pass_and_the_baseline_after_it_refuse_nothing(run_module, shape):
    tenants, flat = PASS_SHAPES[shape]
    factory = {"smb_files_share": "absent", "nfs_root_export": "absent"} if tenants[0].get("state") == "absent" else {}
    passed = planning.first_pass(tenants, FORMED_TENANTS, {"managed_interfaces": None, "frontend_networks": []}, flat)
    sent_networks = passed["network"]["frontend_networks"]
    formed = reads(tenant(1, "Default"), document=EMPTY_HOSTED, unavailable=STATUS_UNAVAILABLE, **FACTORY_HELD)

    first = plan(run_module, tenants=passed["tenants"], cluster=formed, network=passed["network"], management_network=[0])

    assert refusals(first) == [], refusals(first)
    assert first["network_changes"] is True
    first_name = passed["tenants"][0]["name"]
    held = tenant(1, first_name, networks=[item["id"] for item in sent_networks])
    document = doc(*(host(item["id"], item["name"], tenant=1) for item in sent_networks), managed=False, tenants=(held,))
    after = reads(held, document=document, unavailable=STATUS_UNAVAILABLE, **FACTORY_HELD)
    options = {"network": flat} if flat is not None else {}

    baseline = plan(run_module, tenants=tenants, cluster=after, factory_defaults=factory, management_network=[0], **options)

    assert refusals(baseline) == [], refusals(baseline)
    # Each listed network ends in its tenant, whether the pass wrote it or step 3 adds it; the shapes name each network
    # for its tenant.
    listed = [item for entry_ in tenants if entry_.get("state") != "absent" for item in entry_.get("networks") or []]
    listed += (flat or {}).get("frontend_networks") or []
    assert baseline["bindings"] == {str(item["id"]): item["name"] for item in listed}
    assert [item["name"] for item in baseline["step_20a"]] == (["Default"] if factory else [])


def test_the_baseline_after_a_pass_of_two_host_networks_cannot_place_the_play_without_the_management_network(run_module):
    """Why the rebuild preflight refuses such a pass unless qumulo_management_network is set: with the status read, the
    play's host address is on no network's range, and of two HOST networks step 2 cannot tell which one carries it."""
    tenants = [
        entry("eng", previous_name="Default", networks=[hosted_net(0, "eng")], **ON),
        entry("ops", networks=[hosted_net(1, "ops")], **ON),
    ]
    held = tenant(1, "eng", networks=[0, 1])
    document = doc(host(0, "eng", tenant=1), host(1, "ops", tenant=1), managed=False, tenants=(held,))
    read = status(address(0, assigned_by="HOST"), address(1, assigned_by="HOST"), host_addresses=[API_HOST])

    result = plan(run_module, tenants=tenants, cluster=reads(held, document=document, status=read, **FACTORY_HELD))

    assert any("qumulo_management_network" in message for message in refusals(result)), refusals(result)
    named = plan(run_module, tenants=tenants, cluster=reads(held, document=document, status=read, **FACTORY_HELD), management_network=[0])

    assert refusals(named) == [], refusals(named)


# A refused plan's failure. Past the network stage the plan holds the network document with every address range in it,
# so the failure that Ansible prints gives only the refusals, and the plan only at -vvv, Ansible's debugging level.

ADDRESS_RANGE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}-[\d.]+|[0-9A-Fa-f]*:[0-9A-Fa-f:]*-[0-9A-Fa-f:]+")
STAGE_5_KEYS = {"smb", "nfs", "step_20a", "tenants"}
REFUSED_PLANS = [
    pytest.param([entry("finance"), entry("finance")], set(), id="stage 2"),
    pytest.param([entry("Default", networks=[INV1]), entry("finance", networks=[INV1])], STAGE_5_KEYS, id="stage 5"),
    pytest.param(
        [entry("Default", rest_api_enabled=False, networks=[INV1])],
        STAGE_5_KEYS | {"network", "network_changes", "bindings", "validate", "step_3a", "step_3b", "verdicts"},
        id="stage 6",
    ),
]


@pytest.mark.parametrize(("tenants", "computed"), REFUSED_PLANS)
def test_a_refused_plan_fails_with_its_refusals_alone_and_no_address_range(run_module, tenants, computed):
    result = plan(run_module, tenants=tenants)

    assert set(result) == {"failed", "msg", "refusals", "warnings"}
    assert result["msg"] == "\n".join(refusals(result))
    assert ADDRESS_RANGE.findall(json.dumps(result)) == []


@pytest.mark.parametrize(("tenants", "computed"), REFUSED_PLANS)
def test_at_vvv_a_refused_plan_also_returns_what_it_computed_under_plan(run_module, tenants, computed):
    result = plan(run_module, tenants=tenants, _ansible_verbosity=3)

    assert set(result) == {"failed", "msg", "refusals", "warnings", "plan"}
    assert set(result["plan"]) == computed
    assert bool(ADDRESS_RANGE.findall(json.dumps(result["plan"]))) == ("network" in computed)


def test_the_address_range_pattern_finds_ipv4_and_ipv6_ranges():
    assert ADDRESS_RANGE.findall('{"ip_ranges": ["192.0.2.11-14", "2001:db8::11-2001:db8::14"]}') == ["192.0.2.11-14", "2001:db8::11-2001:db8::14"]


def test_the_baseline_after_a_pass_of_tenant_1_s_network_refuses_a_management_network_step_3_adds_later(run_module):
    """Why the rebuild preflight refuses a qumulo_management_network the pass leaves to step 3: the first create is
    judged against the document the pass leaves, which lacks the second tenant's network until step 3 adds it."""
    tenants = [
        entry("eng", previous_name="Default", networks=[hosted_net(0, "eng")], **ON),
        entry("ops", networks=[hosted_net(1, "ops")], **ON),
    ]
    held = tenant(1, "eng", networks=[0])
    document = doc(host(0, "eng", tenant=1), managed=False, tenants=(held,))
    read = status(address(0, assigned_by="HOST"), host_addresses=[API_HOST])

    result = plan(run_module, tenants=tenants, cluster=reads(held, document=document, status=read, **FACTORY_HELD), management_network=[1])

    assert any("network 1" in message and "not on the cluster" in message for message in refusals(result)), refusals(result)
