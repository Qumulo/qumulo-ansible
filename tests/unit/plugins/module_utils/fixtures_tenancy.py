# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""A two-tenant cluster with networks on VLANs, and what capture_info writes of it with the tenants area left out.

test_capture_info.py captures the cluster and checks that the capture is TENANTS_LEFT_OUT; test_tenancy_plan.py replays
TENANTS_LEFT_OUT through step 2 onto this cluster and onto others, so a change to capture's flat form breaks both.
"""

from __future__ import annotations

SWITCHES = ("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "replication_enabled", "nfs_enabled", "smb_enabled")
API_HOST = "192.0.2.11"  # a node address of network 1, which tenant 1 holds


def listed_tenant(tenant_id, name, networks=(), identity_config_id=1, **switches):
    """A tenant as the tenant list and tenant_info answer it; every switch on unless given."""
    return {"id": tenant_id, "name": name, **dict.fromkeys(SWITCHES, True), **switches, "networks": list(networks), "identity_config_id": identity_config_id}


def wire_static(network_id, name, tenant_id, ip_ranges, floating=(), gateway=""):
    """A STATIC network in the wire shape of /v3/network."""
    block = {"default_gateway": gateway, "ip_ranges": list(ip_ranges), "floating_ip_ranges": list(floating), "netmask": "255.255.255.0"}
    return {"id": network_id, "name": name, "tenant_id": tenant_id, "addresses": {"type": "STATIC", "static_addresses": block}}


BOND = {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000}


def wire_document(*networks, bond=(1,), vlans=(), backend=None):
    """A network document in the wire shape of /v3/network."""
    interfaces = {"frontend_bond_config": {**BOND, "networks": list(bond)}, "backend_bond_config": backend, "frontend_vlans": list(vlans)}
    return {"frontend_networks": list(networks), "managed_interfaces": interfaces}


# Tenant 1 renamed Corp holds network 1, untagged on the bond; finance holds networks 2 and 3, each on a VLAN of its own.
CORP = listed_tenant(1, "Corp", networks=[1])
FINANCE = listed_tenant(2, "finance", networks=[2, 3], ssh_enabled=False)
NET1 = wire_static(1, "Corp", 1, ["192.0.2.11-14"], ["192.0.2.21-24"], gateway="192.0.2.1")
NET2 = wire_static(2, "finance", 2, ["198.51.100.11-14"])
NET3 = wire_static(3, "finance-b", 2, ["203.0.113.11-14"])
VLANS = [{"vlan_id": 20, "network_id": 2, "mtu": 1500}, {"vlan_id": 30, "network_id": 3}]
TWO = wire_document(NET1, NET2, NET3, vlans=VLANS)

# qumulo_network as capture writes it of TWO with the tenants area left out: every network flat with its tenant by name,
# the VLANs and the bond as the cluster has them (spec 014, FR-022, decision D23).
TENANTS_LEFT_OUT = {
    "frontend_networks": [
        {
            "id": 1,
            "name": "Corp",
            "type": "STATIC",
            "netmask": "255.255.255.0",
            "default_gateway": "192.0.2.1",
            "ip_ranges": ["192.0.2.11-192.0.2.14"],
            "floating_ip_ranges": ["192.0.2.21-192.0.2.24"],
            "tenant": "Corp",
        },
        {"id": 2, "name": "finance", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["198.51.100.11-198.51.100.14"], "tenant": "finance"},
        {"id": 3, "name": "finance-b", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["203.0.113.11-203.0.113.14"], "tenant": "finance"},
    ],
    "managed_interfaces": {
        "frontend_bond_config": {**BOND, "networks": [1]},
        "frontend_vlans": [{"vlan_id": 20, "network_id": 2, "mtu": 1500}, {"vlan_id": 30, "network_id": 3}],
    },
}

# An edge without tenant 1 (issue #287): eng holds its one network, and it has no share or export.
ENG = listed_tenant(2, "eng", networks=[1])
ENG_NET = wire_static(1, "eng", 2, ["192.0.2.11-14"])
WITHOUT_TENANT_1 = wire_document(ENG_NET)
# What capture_info writes of it with the network, smb, nfs and tenants areas: Default declared absent and the factory share
# and export claimed for removal, so that a cluster formed from the inventory, as a rebuild forms one, drops them.
WITHOUT_TENANT_1_CAPTURE = {
    "qumulo_tenants": [
        {"name": "Default", "state": "absent"},
        {
            "name": "eng",
            **dict.fromkeys(SWITCHES, True),
            "networks": [{"id": 1, "name": "eng", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["192.0.2.11-192.0.2.14"]}],
            "smb_settings": "inherit",
            "nfs_settings": "inherit",
        },
    ],
    "qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
    "qumulo_network": {"frontend_networks": [], "managed_interfaces": {"frontend_bond_config": {**BOND, "networks": []}, "frontend_vlans": []}},
    "qumulo_smb_shares": [],
    "qumulo_nfs_exports": [],
}

# An edge without tenant 1 whose other tenant is named Default: no entry may delete it (issue #287).
NAMED_DEFAULT = listed_tenant(2, "Default", networks=[1])
NAMED_ENG = listed_tenant(3, "eng")
NAMED_DEFAULT_NET = wire_static(1, "Default", 2, ["192.0.2.11-14"])
NAMED_DEFAULT_DOCUMENT = wire_document(NAMED_DEFAULT_NET)
NAMED_DEFAULT_CAPTURE = {
    "qumulo_tenants": [
        {
            "name": "Default",
            **dict.fromkeys(SWITCHES, True),
            "networks": [{"id": 1, "name": "Default", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["192.0.2.11-192.0.2.14"]}],
            "smb_settings": "inherit",
            "nfs_settings": "inherit",
        },
        {"name": "eng", **dict.fromkeys(SWITCHES, True), "networks": [], "smb_settings": "inherit", "nfs_settings": "inherit"},
    ],
    "qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
    "qumulo_network": {"frontend_networks": [], "managed_interfaces": {"frontend_bond_config": {**BOND, "networks": []}, "frontend_vlans": []}},
    "qumulo_smb_shares": [],
    "qumulo_nfs_exports": [],
}

# An edge without tenant 1 with a network no tenant holds: capture writes Default absent all the same, so the rebuild
# preflight refuses the inventory, before the old edge is destroyed, until the network has a tenant that stays.
SPARE_NET = wire_static(2, "spare", None, ["198.51.100.11-14"])
WITH_SPARE = wire_document(ENG_NET, SPARE_NET, bond=(1, 2))
WITH_SPARE_CAPTURE = {
    "qumulo_tenants": [
        {"name": "Default", "state": "absent"},
        {
            "name": "eng",
            **dict.fromkeys(SWITCHES, True),
            "networks": [{"id": 1, "name": "eng", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["192.0.2.11-192.0.2.14"]}],
            "smb_settings": "inherit",
            "nfs_settings": "inherit",
        },
    ],
    "qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
    "qumulo_network": {
        "frontend_networks": [{"id": 2, "name": "spare", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["198.51.100.11-198.51.100.14"]}],
        "managed_interfaces": {"frontend_bond_config": {**BOND, "networks": [2]}, "frontend_vlans": []},
    },
    "qumulo_smb_shares": [],
    "qumulo_nfs_exports": [],
}

# An edge without tenant 1 on networking the host manages: eng holds its HOST network, and capture renames Default to eng,
# so a rebuild keeps that network in tenant 1 throughout.
HOSTED_ENG = listed_tenant(2, "eng", networks=[0])
HOSTED_ENG_NET = {"id": 0, "name": "eng", "tenant_id": 2, "addresses": {"type": "HOST", "host_addresses": {"floating_ip_ranges": [], "netmask": None}}}
HOSTED_WITHOUT_TENANT_1 = {"frontend_networks": [HOSTED_ENG_NET], "managed_interfaces": None}
HOSTED_WITHOUT_TENANT_1_CAPTURE = {
    "qumulo_tenants": [
        {
            "name": "eng",
            "previous_name": "Default",
            **dict.fromkeys(SWITCHES, True),
            "networks": [{"id": 0, "name": "eng", "type": "HOST"}],
            "smb_settings": "inherit",
            "nfs_settings": "inherit",
        },
    ],
    "qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
    "qumulo_network": {"frontend_networks": []},
    "qumulo_smb_shares": [],
    "qumulo_nfs_exports": [],
}

# Two tenants, Default holding network 1 and eng none, beside a network no tenant holds (issue #305): capture writes the
# spare network flat with no tenant, so the rebuild preflight refuses it, before the old edge is destroyed, as a new
# network with no tenant on a replay of two tenants.
DEFAULT_NET = wire_static(1, "Default", 1, ["192.0.2.11-14"])
TWO_WITH_SPARE = wire_document(DEFAULT_NET, SPARE_NET, bond=(1, 2))
TWO_WITH_SPARE_CAPTURE = {
    "qumulo_tenants": [
        {
            "name": "Default",
            **dict.fromkeys(SWITCHES, True),
            "networks": [{"id": 1, "name": "Default", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["192.0.2.11-192.0.2.14"]}],
            "smb_settings": "inherit",
            "nfs_settings": "inherit",
        },
        {"name": "eng", **dict.fromkeys(SWITCHES, True), "networks": [], "smb_settings": "inherit", "nfs_settings": "inherit"},
    ],
    "qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
    "qumulo_network": WITH_SPARE_CAPTURE["qumulo_network"],
    "qumulo_smb_shares": [],
    "qumulo_nfs_exports": [],
}
