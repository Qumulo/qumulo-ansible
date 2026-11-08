# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.item_areas: the ownership check finds a tenant's keys in their areas (FR-030).

The per-area tests read cluster_baseline_item_areas from the role's vars file, so they test the matchers the role uses.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from ansible_collections.qumulo.cluster.plugins.filter.item_areas import item_areas

ROOT = Path(__file__).resolve().parents[4]
ROLE_VARS = ROOT / "roles" / "cluster_baseline" / "vars" / "main.yml"


def role_matchers(area):
    """The role's matchers for area alone."""
    return {area: yaml.safe_load(ROLE_VARS.read_text(encoding="utf-8"))["cluster_baseline_item_areas"][area]}


def pairs(area, *variables):
    return [{"area": area, "variable": variable} for variable in variables]


def test_a_tenants_networks_are_in_network_empty_included():
    tenants = [{"name": "Default", "networks": [{"id": 1}]}, {"name": "finance", "networks": []}, {"name": "eng"}]

    assert item_areas(role_matchers("network"), qumulo_tenants=tenants) == pairs(
        "network", "qumulo_tenants[Default].networks", "qumulo_tenants[finance].networks"
    )


def test_a_tenant_networks_floating_ranges_are_in_network_floating_ips_empty_included():
    networks = [{"id": 2, "floating_ip_ranges": ["198.51.100.21-24"]}, {"id": 3, "floating_ip_ranges": []}, {"id": 4}]

    assert item_areas(role_matchers("network.floating_ips"), qumulo_tenants=[{"name": "finance", "networks": networks}]) == pairs(
        "network.floating_ips", "qumulo_tenants[finance].networks[2].floating_ip_ranges", "qumulo_tenants[finance].networks[3].floating_ip_ranges"
    )


def test_a_tenants_smb_settings_inherit_included_and_shares_are_in_smb():
    tenants = [
        {"name": "finance", "smb_settings": "inherit"},
        {"name": "eng", "smb_settings": {"session_encryption": "REQUIRED"}, "smb_shares": [{"share_name": "eng", "fs_path": "/eng"}]},
        {"name": "ops", "nfs_settings": {"v4_enabled": True}},
    ]

    assert item_areas(role_matchers("smb"), qumulo_tenants=tenants) == pairs(
        "smb", "qumulo_tenants[finance].smb_settings", "qumulo_tenants[eng].smb_settings", "qumulo_tenants[eng].smb_shares"
    )


def test_a_tenants_nfs_settings_and_exports_are_in_nfs():
    tenants = [
        {"name": "finance", "nfs_settings": "inherit"},
        {"name": "eng", "nfs_exports": [{"export_path": "/eng", "fs_path": "/eng"}]},
        {"name": "ops", "smb_settings": {"session_encryption": "REQUIRED"}},
    ]

    assert item_areas(role_matchers("nfs"), qumulo_tenants=tenants) == pairs("nfs", "qumulo_tenants[finance].nfs_settings", "qumulo_tenants[eng].nfs_exports")


def test_an_item_is_named_by_its_id_else_its_name_else_its_index():
    networks = [{"id": 0, "name": "zero", "floating_ip_ranges": []}, {"name": "eng", "floating_ip_ranges": []}, {"floating_ip_ranges": []}]
    matchers = {"network.floating_ips": ["qumulo_tenants[].networks[].floating_ip_ranges"]}

    assert item_areas(matchers, qumulo_tenants=[{"name": "Default"}, {"networks": networks}]) == pairs(
        "network.floating_ips",
        "qumulo_tenants[1].networks[0].floating_ip_ranges",
        "qumulo_tenants[1].networks[eng].floating_ip_ranges",
        "qumulo_tenants[1].networks[2].floating_ip_ranges",
    )


def test_an_unset_variable_declares_nothing():
    matchers = {"network": ["qumulo_tenants[].networks"]}

    assert item_areas(matchers, qumulo_tenants=None) == []
    assert item_areas(matchers) == []


def test_a_null_key_an_item_not_a_dict_and_a_list_key_not_a_list_declare_nothing():
    tenants = [
        "finance",
        {"name": "eng", "networks": None},
        {"name": "ops", "networks": "ops"},
        {"name": "dev", "networks": [{"id": 5, "floating_ip_ranges": None}]},
    ]

    assert item_areas({"network.floating_ips": ["qumulo_tenants[].networks[].floating_ip_ranges"]}, qumulo_tenants=tenants) == []


def test_a_key_is_given_once_for_each_area_in_matcher_order():
    tenants = [{"name": "finance", "smb_settings": "inherit"}, {"name": "finance", "smb_settings": "inherit"}]
    matchers = {"tenants": ["qumulo_tenants[].smb_settings"], "smb": ["qumulo_tenants[].smb_settings", "qumulo_tenants[].smb_settings"]}

    assert item_areas(matchers, qumulo_tenants=tenants) == [
        {"area": "tenants", "variable": "qumulo_tenants[finance].smb_settings"},
        {"area": "smb", "variable": "qumulo_tenants[finance].smb_settings"},
    ]


def test_flat_shares_and_exports_that_name_their_tenants_are_in_tenants():
    shares = [{"share_name": "home", "tenants": "all"}, {"share_name": "pub"}, {"share_name": "old", "tenant_id": 2}]
    exports = [{"export_path": "/finance", "tenants": ["finance"]}, {"export_path": "/pub"}]

    assert item_areas(role_matchers("tenants"), qumulo_smb_shares=shares, qumulo_nfs_exports=exports) == pairs(
        "tenants", "qumulo_smb_shares[home].tenants", "qumulo_smb_shares[old].tenant_id", "qumulo_nfs_exports[/finance].tenants"
    )
