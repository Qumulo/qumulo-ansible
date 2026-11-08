# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.host_vars_duplicates: what adoption and the clone would write that the inventory already declares (issue #303).

A host_vars file leaves out each variable the inventory defines, so a group's flat qumulo_smb_shares, qumulo_nfs_exports
or qumulo_network stays in force beside the qumulo_tenants the file writes. The baseline refuses a share, export or
network that two entries declare. So the file leaves out each share or export entry the inventory already declares,
with a note, as it leaves out a variable the inventory defines, and the playbooks refuse only a network declared in both,
or an entry the inventory declares with other values.
"""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.filter.host_vars_duplicates import host_vars_duplicates

FILES = {"share_name": "Files", "fs_path": "/", "description": ""}
PROJECTS = {"share_name": "Projects", "fs_path": "/projects", "description": "", "access": []}
ROOT_EXPORT = {"export_path": "/", "fs_path": "/", "description": ""}
DEFAULT = {"name": "Default", "networks": [{"id": 1, "name": "Default"}], "smb_shares": [FILES, PROJECTS], "nfs_exports": [ROOT_EXPORT]}


def shares(result, tenant=0):
    return [share["share_name"] for share in result["variables"]["qumulo_tenants"][tenant].get("smb_shares", [])]


def test_a_tenant_share_a_group_s_flat_list_declares_is_left_out_with_a_note():
    result = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, {"qumulo_smb_shares": [PROJECTS]})

    assert shares(result) == ["Files"]
    assert result["left_out"] == [
        {
            "what": "the SMB share Projects of tenant Default",
            "why": "the inventory declares it in qumulo_smb_shares[0], and the baseline refuses an SMB share declared twice; the inventory's entry applies",
        }
    ]
    assert result["refusals"] == []


def test_an_inventory_entry_that_gives_fewer_keys_with_the_file_s_values_is_the_same_entry():
    result = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, {"qumulo_smb_shares": [{"share_name": "projects", "fs_path": "/projects"}]})

    assert shares(result) == ["Files"]
    assert result["refusals"] == []


def test_an_inventory_entry_with_other_values_is_refused_naming_the_keys():
    result = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, {"qumulo_smb_shares": [{**PROJECTS, "fs_path": "/elsewhere", "description": "x"}]})

    assert result["refusals"] == [
        (
            "the SMB share Projects of tenant Default, which the inventory declares in qumulo_smb_shares[0] and the file in "
            "qumulo_tenants[Default].smb_shares[1] with other values of fs_path, description"
        )
    ]


def test_a_network_declared_in_both_is_refused_whichever_tenant_holds_it():
    inventory = {"qumulo_network": {"frontend_networks": [{"id": 1, "tenant": "eng"}]}}

    result = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, inventory)

    assert result["refusals"] == [
        "network 1, which the inventory declares in qumulo_network.frontend_networks[0] and the file in qumulo_tenants[Default].networks[0]"
    ]
    assert result["variables"]["qumulo_tenants"][0]["networks"] == DEFAULT["networks"]


def test_a_network_under_a_tenant_declared_absent_is_not_counted():
    inventory = {"qumulo_tenants": [{"name": "Old", "state": "absent", "networks": [{"id": 3}]}]}

    result = host_vars_duplicates({"qumulo_network": {"frontend_networks": [{"id": 3, "tenant": "eng"}]}}, inventory)

    assert result["refusals"] == []


def test_the_captured_factory_share_the_group_removes_is_left_out_for_the_removal():
    inventory = {"qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"}}

    result = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, inventory)

    assert shares(result) == ["Projects"]
    assert "nfs_exports" not in result["variables"]["qumulo_tenants"][0]
    assert result["left_out"][0] == {
        "what": "the SMB share Files of tenant Default",
        "why": "the inventory removes it, in qumulo_factory_defaults.smb_files_share, so the next baseline removes it from the cluster",
    }
    assert result["refusals"] == []


def test_an_entry_the_inventory_declares_absent_is_left_out_for_the_removal():
    result = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, {"qumulo_smb_shares": [{"share_name": "Projects", "state": "absent"}]})

    assert shares(result) == ["Files"]
    assert result["left_out"][0]["why"].startswith("the inventory removes it, in qumulo_smb_shares[0]")


def test_the_file_s_factory_removal_gives_way_to_the_inventory_s_entry():
    inventory = {"qumulo_tenants": [{"name": "Default", "smb_shares": [FILES]}]}
    written = {"qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "present"}}

    result = host_vars_duplicates(written, inventory)

    assert result["variables"]["qumulo_factory_defaults"] == {"nfs_root_export": "present"}
    assert result["left_out"] == [
        {
            "what": "the removal of the SMB share Files of tenant Default",
            "why": "the inventory declares the SMB share in qumulo_tenants[Default].smb_shares[0], which the next baseline applies instead",
        }
    ]


def test_without_a_duplicate_the_variables_are_kept_as_they_are():
    written = {"qumulo_tenants": [DEFAULT]}

    result = host_vars_duplicates(
        written, {"qumulo_smb_shares": [{"share_name": "Home", "fs_path": "/home"}], "qumulo_network": {"frontend_networks": [{"id": 2}]}}
    )

    assert result == {"variables": written, "left_out": [], "refusals": []}


def test_share_names_match_without_regard_to_case_and_export_paths_exactly():
    inventory = {"qumulo_smb_shares": [{"share_name": "projects"}], "qumulo_nfs_exports": [{"export_path": "/Projects"}]}
    written = {"qumulo_tenants": [{"name": "Default", "smb_shares": [PROJECTS], "nfs_exports": [{"export_path": "/projects"}]}]}

    result = host_vars_duplicates(written, inventory)

    assert [entry["what"] for entry in result["left_out"]] == ["the SMB share Projects of tenant Default"]
    assert result["variables"]["qumulo_tenants"][0]["nfs_exports"] == [{"export_path": "/projects"}]


def test_a_flat_entry_claims_only_the_tenants_it_names_and_tenants_all_every_one():
    written = {"qumulo_tenants": [DEFAULT, {"name": "eng", "smb_shares": [PROJECTS]}]}

    named = host_vars_duplicates(written, {"qumulo_smb_shares": [{**PROJECTS, "tenants": ["eng"]}]})
    every = host_vars_duplicates(written, {"qumulo_smb_shares": [{**PROJECTS, "tenants": "all"}]})

    assert (shares(named), shares(named, 1)) == (["Files", "Projects"], [])
    assert "smb_shares" not in named["variables"]["qumulo_tenants"][1]
    assert (shares(every), shares(every, 1)) == (["Files"], [])


def test_a_flat_entry_without_tenants_claims_the_only_tenant_and_no_tenant_of_several():
    one = host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, {"qumulo_smb_shares": [PROJECTS]})
    # The baseline refuses an entry that names no tenant on a run of several, for a reason of its own.
    several = host_vars_duplicates({"qumulo_tenants": [DEFAULT, {"name": "eng"}]}, {"qumulo_smb_shares": [PROJECTS]})

    assert shares(one) == ["Files"]
    assert several["left_out"] == []


def test_tenant_id_1_and_the_factory_removal_find_tenant_1_by_its_previous_name():
    renamed = {"qumulo_tenants": [{**DEFAULT, "name": "accounts", "previous_name": "Default"}, {"name": "Default", "smb_shares": [FILES]}]}
    inventory = {"qumulo_smb_shares": [{**PROJECTS, "tenant_id": 1}, {**PROJECTS, "tenant_id": 3}], "qumulo_factory_defaults": {"smb_files_share": "absent"}}

    result = host_vars_duplicates(renamed, inventory)

    assert [entry["what"] for entry in result["left_out"]] == ["the SMB share Files of tenant accounts", "the SMB share Projects of tenant accounts"]
    assert shares(result, 1) == ["Files"]


def test_a_cluster_without_tenant_1_gives_its_factory_removal_and_tenant_id_1_no_tenant():
    # PR 300's form: capture declares Default absent and the factory share and export absent.
    written = {
        "qumulo_tenants": [{"name": "Default", "state": "absent"}, {"name": "Prod", "smb_shares": [PROJECTS]}],
        "qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
    }
    inventory = {"qumulo_smb_shares": [{**FILES, "tenants": "all"}, {**PROJECTS, "tenant_id": 1}]}

    lacking = host_vars_duplicates(written, inventory, lacks_tenant_1=True)
    unsaid = host_vars_duplicates(written, inventory)

    assert lacking == {"variables": written, "left_out": [], "refusals": []}
    # An absent entry is never tenant 1, even when the caller does not say the cluster lacks it.
    assert unsaid == lacking


def test_a_cluster_without_tenant_1_whose_other_tenant_is_named_default_gives_the_removal_no_tenant():
    written = {"qumulo_tenants": [{"name": "Default", "smb_shares": [FILES]}], "qumulo_factory_defaults": {"smb_files_share": "absent"}}

    result = host_vars_duplicates(written, {"qumulo_smb_shares": [{**FILES, "tenants": ["Default"]}]}, lacks_tenant_1=True)

    assert [entry["what"] for entry in result["left_out"]] == ["the SMB share Files of tenant Default"]


def test_the_file_s_shared_entry_loses_only_the_tenants_the_inventory_declares_it_for():
    inventory = {"qumulo_tenants": [{"name": "eng", "smb_shares": [PROJECTS]}, {"name": "ops"}, {"name": "dev"}]}

    listed = host_vars_duplicates({"qumulo_smb_shares": [{**PROJECTS, "tenants": ["eng", "ops"]}]}, inventory)
    every = host_vars_duplicates({"qumulo_smb_shares": [{**PROJECTS, "tenants": "all"}]}, inventory)
    alone = host_vars_duplicates({"qumulo_smb_shares": [{**PROJECTS, "tenants": ["eng"]}]}, inventory)

    assert listed["variables"]["qumulo_smb_shares"] == [{**PROJECTS, "tenants": ["ops"]}]
    assert every["variables"]["qumulo_smb_shares"] == [{**PROJECTS, "tenants": ["ops", "dev"]}]
    assert alone["variables"]["qumulo_smb_shares"] == []


def test_a_variable_the_file_holds_outranks_the_inventory_s_and_an_excluded_one_is_not_the_inventory_s():
    written = {"qumulo_tenants": [DEFAULT], "qumulo_smb_shares": []}
    inventory = {"qumulo_smb_shares": [PROJECTS], "qumulo_network": {"frontend_networks": [{"id": 1}]}}

    result = host_vars_duplicates(written, inventory, exclude=["qumulo_network"])

    assert result == {"variables": written, "left_out": [], "refusals": []}


def test_a_duplicate_within_the_inventory_or_within_the_file_is_not_the_file_s_to_leave_out():
    inventory = {"qumulo_smb_shares": [{**PROJECTS, "tenants": ["eng"]}], "qumulo_tenants": [{"name": "eng", "smb_shares": [PROJECTS]}]}
    written = {"qumulo_nfs_exports": [{**ROOT_EXPORT, "tenants": ["eng"]}, {**ROOT_EXPORT, "tenants": ["eng"]}]}

    assert host_vars_duplicates(written, inventory) == {"variables": written, "left_out": [], "refusals": []}


def test_without_qumulo_tenants_or_with_it_empty_a_flat_entry_claims_the_cluster_s_one_tenant():
    inventory = {"qumulo_factory_defaults": {"smb_files_share": "absent"}}

    unset = host_vars_duplicates({"qumulo_smb_shares": [FILES]}, inventory)
    empty = host_vars_duplicates({"qumulo_smb_shares": [FILES], "qumulo_tenants": []}, inventory)

    assert unset["variables"]["qumulo_smb_shares"] == empty["variables"]["qumulo_smb_shares"] == []
    assert unset["left_out"][0]["what"] == "the SMB share Files of the cluster's one tenant"


@pytest.mark.parametrize("value", [None, [], {}])
def test_an_inventory_variable_that_is_null_or_empty_declares_nothing(value):
    inventory = {"qumulo_smb_shares": value, "qumulo_nfs_exports": value, "qumulo_network": value or None, "qumulo_factory_defaults": value or None}

    assert host_vars_duplicates({"qumulo_tenants": [DEFAULT]}, inventory)["left_out"] == []


def test_an_entry_without_its_name_or_id_is_left_to_the_baseline_to_refuse():
    inventory = {"qumulo_smb_shares": [{"fs_path": "/"}], "qumulo_nfs_exports": [{"export_path": "/a/"}], "qumulo_network": {"frontend_networks": [{}]}}
    written = {"qumulo_tenants": [{"name": "Default", "networks": [{}], "smb_shares": [{"fs_path": "/"}], "nfs_exports": [{"export_path": "/a/"}]}]}

    assert host_vars_duplicates(written, inventory) == {"variables": written, "left_out": [], "refusals": []}


def test_the_given_variables_are_never_changed_in_place():
    written = {"qumulo_tenants": [{**DEFAULT, "smb_shares": [FILES, PROJECTS]}]}

    host_vars_duplicates(written, {"qumulo_smb_shares": [PROJECTS]})

    assert written["qumulo_tenants"][0]["smb_shares"] == [FILES, PROJECTS]


# Round 2 of PR 324's walkthrough: entries compare as the modules compare them, and a key the file's entry gives that
# the inventory's leaves out is lost on a cluster that creates the entry from the inventory's.
CAPTURED_SHARE = {
    "share_name": "Projects",
    "fs_path": "/projects",
    "description": "",
    "permissions": [{"type": "ALLOWED", "trustee": "world:Everyone", "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]}],
    "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]}],
    "access_based_enumeration_enabled": False,
    "require_encryption": False,
    "default_file_create_mode": "0644",
}
CAPTURED_EXPORT = {
    "export_path": "/projects",
    "fs_path": "/projects",
    "description": "",
    "restrictions": [
        {
            "host_restrictions": ["10.0.0.0/24"],
            "read_only": False,
            "require_privileged_port": False,
            "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
            "user_mapping": "NFS_MAP_NONE",
        }
    ],
    "fields_to_present_as_32_bit": ["FILE_IDS", "FILE_SIZES", "FS_SIZE"],
}


@pytest.mark.parametrize(
    "spelled",
    [
        {"permissions": [{"trustee": "Everyone", "rights": ["ALL"]}]},
        {"permissions": [{"type": "ALLOWED", "trustee": "world:Everyone", "rights": ["write", "read", "change_permissions"]}]},
        {"create_fs_path": True},
        {"fs_path": "/projects/"},
        {"default_file_create_mode": "644"},
        {"network_permissions": [{"rights": ["ALL"]}]},
    ],
)
def test_an_smb_entry_spelled_as_the_module_takes_it_is_the_same_entry(spelled):
    written = {"qumulo_tenants": [{"name": "Default", "smb_shares": [CAPTURED_SHARE]}]}

    result = host_vars_duplicates(written, {"qumulo_smb_shares": [{"share_name": "Projects", "fs_path": "/projects", **spelled}]})

    assert result["refusals"] == []
    assert len(result["left_out"]) == 1


@pytest.mark.parametrize(
    "spelled",
    [
        {"restrictions": [{"host_restrictions": ["10.0.0.0/24"]}]},
        {"restrictions": [{"host_restrictions": ["10.0.0.0/24"], "require_tls": False}]},
        {"fields_to_present_as_32_bit": ["ALL"]},
        {"fs_path": "/projects/", "create_fs_path": True},
    ],
)
def test_an_nfs_entry_spelled_as_the_module_takes_it_is_the_same_entry(spelled):
    written = {"qumulo_tenants": [{"name": "Default", "nfs_exports": [CAPTURED_EXPORT]}]}

    result = host_vars_duplicates(written, {"qumulo_nfs_exports": [{"export_path": "/projects", "fs_path": "/projects", **spelled}]})

    assert result["refusals"] == []
    assert len(result["left_out"]) == 1


def test_an_entry_whose_canonical_values_differ_is_still_refused():
    written = {"qumulo_tenants": [{"name": "Default", "smb_shares": [CAPTURED_SHARE]}]}

    result = host_vars_duplicates(written, {"qumulo_smb_shares": [{"share_name": "Projects", "permissions": [{"trustee": "Everyone", "rights": ["READ"]}]}]})

    assert result["refusals"][0].endswith("with other values of permissions")


def test_on_a_cluster_that_creates_the_entry_a_key_only_the_file_gives_differs_unless_a_create_gives_it():
    restricted = {**CAPTURED_SHARE, "network_permissions": [{"type": "ALLOWED", "address_ranges": ["10.0.0.0/24"], "rights": ["READ"]}]}
    group = {"qumulo_smb_shares": [{"share_name": "Projects", "fs_path": "/projects", "permissions": [{"trustee": "Everyone", "rights": ["ALL"]}]}]}

    def judged(share):
        return host_vars_duplicates({"qumulo_tenants": [{"name": "Default", "smb_shares": [share]}]}, group, cluster_holds_entries=False)

    assert judged(restricted)["refusals"] == [
        (
            "the SMB share Projects of tenant Default, which the inventory declares in qumulo_smb_shares[0] and the file in "
            "qumulo_tenants[Default].smb_shares[0] with other values of network_permissions, which the inventory's entry leaves out"
        )
    ]
    # Every key the captured share gives beside the group's is what a create gives it, so nothing is lost.
    assert judged(CAPTURED_SHARE)["refusals"] == []


def test_on_a_cluster_that_creates_the_entry_one_the_inventory_gives_without_permissions_is_refused():
    group = {"qumulo_smb_shares": [{"share_name": "Projects", "fs_path": "/projects"}]}

    result = host_vars_duplicates({"qumulo_tenants": [{"name": "Default", "smb_shares": [CAPTURED_SHARE]}]}, group, cluster_holds_entries=False)

    assert result["refusals"][0].endswith("with other values of permissions, which the inventory's entry leaves out")


def test_on_a_cluster_that_holds_the_entry_the_note_names_the_keys_no_entry_declares_now():
    described = {**CAPTURED_SHARE, "description": "team space", "require_encryption": True}
    group = {"qumulo_smb_shares": [{"share_name": "Projects", "fs_path": "/projects", "permissions": [{"trustee": "Everyone", "rights": ["ALL"]}]}]}

    result = host_vars_duplicates({"qumulo_tenants": [{"name": "Default", "smb_shares": [described]}]}, group)

    assert result["left_out"][0]["why"] == (
        "the inventory declares it in qumulo_smb_shares[0], and the baseline refuses an SMB share declared twice; the inventory's entry applies. "
        "The cluster keeps its description and require_encryption, which no entry declares now, so a rebuild from the inventory would not "
        "restore them"
    )


def test_no_empty_factory_mapping_is_left_behind():
    inventory = {"qumulo_tenants": [{"name": "Default", "smb_shares": [FILES], "nfs_exports": [ROOT_EXPORT]}]}
    written = {"qumulo_factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"}}

    assert "qumulo_factory_defaults" not in host_vars_duplicates(written, inventory)["variables"]


def test_a_shared_entry_for_all_tenants_keeps_only_the_present_ones_left_and_says_all_became_a_list():
    inventory = {"qumulo_tenants": [{"name": "eng", "smb_shares": [PROJECTS]}, {"name": "ops"}, {"name": "old", "state": "absent"}]}

    result = host_vars_duplicates({"qumulo_smb_shares": [{**PROJECTS, "tenants": "all"}]}, inventory)
    alone = host_vars_duplicates(
        {"qumulo_smb_shares": [{**PROJECTS, "tenants": "all"}]}, {"qumulo_tenants": inventory["qumulo_tenants"][:1] + inventory["qumulo_tenants"][2:]}
    )

    assert result["variables"]["qumulo_smb_shares"] == [{**PROJECTS, "tenants": ["ops"]}]
    assert result["left_out"][0]["why"].endswith(
        "; its tenants: all is written as the list of the other tenants, which does not cover a tenant added to the inventory later"
    )
    assert alone["variables"]["qumulo_smb_shares"] == []


def test_pr_331_s_heir_of_tenant_1_is_not_tenant_1_while_the_cluster_lacks_it():
    written = {
        "qumulo_tenants": [{"name": "Prod", "previous_name": "Default", "smb_shares": [FILES]}],
        "qumulo_factory_defaults": {"smb_files_share": "present"},
    }
    inventory = {"qumulo_factory_defaults": {"smb_files_share": "absent"}}

    lacking = host_vars_duplicates({"qumulo_tenants": written["qumulo_tenants"]}, inventory, lacks_tenant_1=True)
    holding = host_vars_duplicates({"qumulo_tenants": written["qumulo_tenants"]}, inventory)

    assert lacking["left_out"] == []
    assert [entry["what"] for entry in holding["left_out"]] == ["the SMB share Files of tenant Prod"]


def test_the_note_on_one_key_no_entry_declares_now_says_it():
    described = {**CAPTURED_SHARE, "description": "team space"}
    group = {"qumulo_smb_shares": [{"share_name": "Projects", "fs_path": "/projects", "permissions": [{"trustee": "Everyone", "rights": ["ALL"]}]}]}

    result = host_vars_duplicates({"qumulo_tenants": [{"name": "Default", "smb_shares": [described]}]}, group)

    assert result["left_out"][0]["why"].endswith(
        "The cluster keeps its description, which no entry declares now, so a rebuild from the inventory would not restore it"
    )


def test_a_null_key_in_the_inventory_s_entry_is_unset_as_the_role_and_module_take_it():
    written = {"qumulo_tenants": [{"name": "Default", "smb_shares": [CAPTURED_SHARE]}]}

    result = host_vars_duplicates(written, {"qumulo_smb_shares": [{"share_name": "Projects", "fs_path": "/projects", "description": None}]})

    assert result["refusals"] == []
    assert len(result["left_out"]) == 1


# Issue #335: when the inventory defines qumulo_tenants, the file cannot hold the captured one, so captured_tenants
# gives it, and what it holds is judged entry by entry against the inventory's.
CAPTURED = [
    {"name": "Default", "nfs_enabled": True, "networks": [], "smb_shares": [FILES, PROJECTS], "nfs_exports": [ROOT_EXPORT]},
    {"name": "eng", "nfs_enabled": False, "networks": [], "smb_shares": [{"share_name": "Code", "fs_path": "/code"}]},
]
FLAT = {"qumulo_smb_shares": [], "qumulo_nfs_exports": []}


def test_with_an_empty_inventory_qumulo_tenants_each_captured_share_is_written_flat_for_its_tenant():
    result = host_vars_duplicates(FLAT, {"qumulo_tenants": []}, captured_tenants=CAPTURED)

    assert result["variables"]["qumulo_smb_shares"] == [
        {**FILES, "tenants": ["Default"]},
        {**PROJECTS, "tenants": ["Default"]},
        {"share_name": "Code", "fs_path": "/code", "tenants": ["eng"]},
    ]
    assert result["variables"]["qumulo_nfs_exports"] == [{**ROOT_EXPORT, "tenants": ["Default"]}]
    assert [entry["what"] for entry in result["left_out"]] == ["tenant Default", "tenant eng"]
    assert "the inventory's qumulo_tenants does not declare it" in result["left_out"][0]["why"]
    assert result["refusals"] == []


def test_a_captured_share_the_inventory_s_tenant_declares_is_left_out_and_one_it_declares_otherwise_refused():
    same = host_vars_duplicates(
        FLAT,
        {"qumulo_tenants": [{"name": "Default", "smb_shares": [{"share_name": "projects", "fs_path": "/projects/", "access": []}]}]},
        captured_tenants=CAPTURED[:1],
    )
    other = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default", "smb_shares": [{**PROJECTS, "fs_path": "/x"}]}]}, captured_tenants=CAPTURED[:1])

    assert [share["share_name"] for share in same["variables"]["qumulo_smb_shares"]] == ["Files"]
    assert same["left_out"] == [
        {
            "what": "the SMB share Projects of tenant Default",
            "why": "the inventory declares it in qumulo_tenants[Default].smb_shares[0], and the baseline refuses an SMB share declared twice; "
            "the inventory's entry applies",
        }
    ]
    assert other["refusals"] == [
        (
            "the SMB share Projects of tenant Default, which the inventory declares in qumulo_tenants[Default].smb_shares[0] and the capture in "
            "qumulo_tenants[Default].smb_shares[1] with other values of fs_path"
        )
    ]


def test_a_tenant_the_inventory_declares_with_other_switches_is_refused_and_the_same_ones_pass():
    other = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default", "nfs_enabled": False}]}, captured_tenants=CAPTURED[:1])
    same = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default", "nfs_enabled": True}]}, captured_tenants=CAPTURED[:1])

    assert other["refusals"] == ["tenant Default, which the inventory declares in qumulo_tenants[Default] and the capture with other values of nfs_enabled"]
    assert same["refusals"] == [] and same["left_out"] == []


def test_a_tenant_the_inventory_removes_has_nothing_of_it_written():
    result = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default"}, {"name": "eng", "state": "absent"}]}, captured_tenants=CAPTURED)

    assert [share["share_name"] for share in result["variables"]["qumulo_smb_shares"]] == ["Files", "Projects"]
    assert result["left_out"] == [
        {
            "what": "tenant eng",
            "why": "the inventory declares it state: absent, so the next baseline deletes it once the inventory declares or claims each share "
            "and export it holds; what the capture holds under it is not written",
        }
    ]


def test_a_captured_share_neither_flat_list_of_the_file_can_hold_is_noted():
    result = host_vars_duplicates(
        {"qumulo_nfs_exports": []}, {"qumulo_tenants": [{"name": "Default"}], "qumulo_smb_shares": [FILES]}, captured_tenants=CAPTURED[:1]
    )

    assert "qumulo_smb_shares" not in result["variables"]
    assert result["left_out"] == [
        {
            "what": "the SMB share Files of tenant Default",
            "why": "the inventory declares it in qumulo_smb_shares[0], and the baseline refuses an SMB share declared twice; the inventory's entry applies",
        },
        {
            "what": "the SMB share Projects of tenant Default",
            "why": "the inventory defines both qumulo_tenants and qumulo_smb_shares, so a host_vars file can hold it in neither; declare it under "
            "tenant Default in the inventory",
        },
    ]


def test_a_captured_tenant_network_is_refused_unless_the_inventory_lists_it_under_that_tenant():
    captured = [{"name": "Default", "networks": [{"id": 1}, {"id": 2}]}]
    inventory = {"qumulo_tenants": [{"name": "Default", "networks": [{"id": 1}]}, {"name": "eng", "networks": [{"id": 2}]}]}

    result = host_vars_duplicates(FLAT, inventory, captured_tenants=captured)

    assert result["refusals"] == ["network 2, which tenant Default holds and the inventory's qumulo_tenants does not list under Default"]
    assert host_vars_duplicates(FLAT, {"qumulo_tenants": []}, captured_tenants=captured)["refusals"] == [
        "network 1, which tenant Default holds and the inventory's qumulo_tenants does not list under Default",
        "network 2, which tenant Default holds and the inventory's qumulo_tenants does not list under Default",
    ]


def test_captured_tenants_count_only_while_the_file_leaves_qumulo_tenants_out():
    written = {**FLAT, "qumulo_tenants": CAPTURED}

    assert host_vars_duplicates(written, {"qumulo_tenants": []}, captured_tenants=CAPTURED) == {"variables": written, "left_out": [], "refusals": []}


def test_a_switch_the_capture_does_not_hold_is_not_compared():
    # On a cloud cluster capture writes no service switches, since no tenant write is possible there.
    captured = [{"name": "Default", "networks": []}]

    result = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default", "smb_enabled": True}]}, captured_tenants=captured)

    assert result["refusals"] == []


# PR 361's walkthrough: beside the inventory's qumulo_tenants, the baseline takes tenant 1, the one tenant and the
# tenants a captured tenants: all names from the cluster, so the filter takes them from the captured tenants.
SHARED = {"share_name": "Shared", "fs_path": "/shared"}
ONLY_DEFAULT = [{"name": "Default", "networks": [], "smb_shares": [FILES, PROJECTS]}]


@pytest.mark.parametrize("declared", [[], [{"name": "eng"}]])
def test_the_inventory_s_factory_removal_claims_the_captured_tenant_1_beside_its_qumulo_tenants(declared):
    inventory = {"qumulo_tenants": declared, "qumulo_factory_defaults": {"smb_files_share": "absent"}}

    result = host_vars_duplicates(FLAT, inventory, captured_tenants=ONLY_DEFAULT)

    assert [share["share_name"] for share in result["variables"]["qumulo_smb_shares"]] == ["Projects"]
    assert {"what": "the SMB share Files of tenant Default", "why": result["left_out"][-1]["why"]} in result["left_out"]
    assert "the inventory removes it, in qumulo_factory_defaults.smb_files_share" in result["left_out"][-1]["why"]


def test_a_flat_inventory_entry_without_tenants_claims_the_cluster_s_one_tenant_beside_an_empty_qumulo_tenants():
    written = {"qumulo_nfs_exports": []}

    other = host_vars_duplicates(written, {"qumulo_tenants": [], "qumulo_smb_shares": [{**PROJECTS, "description": "other"}]}, captured_tenants=ONLY_DEFAULT)
    same = host_vars_duplicates(written, {"qumulo_tenants": [], "qumulo_smb_shares": [PROJECTS]}, captured_tenants=ONLY_DEFAULT)

    assert other["refusals"] == [
        (
            "the SMB share Projects of tenant Default, which the inventory declares in qumulo_smb_shares[0] and the capture in "
            "qumulo_tenants[Default].smb_shares[1] with other values of description"
        )
    ]
    assert [entry["what"] for entry in same["left_out"]][:2] == ["tenant Default", "the SMB share Projects of tenant Default"]
    assert same["left_out"][1]["why"].startswith("the inventory declares it in qumulo_smb_shares[0]")


def test_a_captured_tenants_all_entry_is_written_as_the_captured_tenants():
    captured = [{"name": "A", "networks": [], "smb_shares": [SHARED]}, {"name": "B", "networks": [], "smb_shares": [SHARED]}]
    written = {"qumulo_smb_shares": [{**SHARED, "tenants": "all"}], "qumulo_nfs_exports": []}

    empty = host_vars_duplicates(written, {"qumulo_tenants": []}, captured_tenants=[{**tenant, "smb_shares": []} for tenant in captured])
    partial = host_vars_duplicates(
        written, {"qumulo_tenants": [{"name": "A", "smb_shares": [SHARED]}]}, captured_tenants=[{**t, "smb_shares": []} for t in captured]
    )

    assert empty["variables"]["qumulo_smb_shares"] == [{**SHARED, "tenants": ["A", "B"]}]
    assert {
        "what": "the tenants of the SMB share Shared",
        "why": "beside the inventory's qumulo_tenants, tenants: all would mean the tenants the inventory declares, so it is written as the "
        "cluster's tenants, A and B",
    } in empty["left_out"]
    assert partial["variables"]["qumulo_smb_shares"] == [{**SHARED, "tenants": ["B"]}]
    assert "the SMB share Shared of tenant A" in [entry["what"] for entry in partial["left_out"]]


def test_a_captured_tenant_the_inventory_renames_is_matched_by_its_previous_name():
    captured = [{"name": "Default", "networks": [{"id": 1}], "smb_shares": [PROJECTS]}]
    inventory = {"qumulo_tenants": [{"name": "Corp", "previous_name": "Default", "networks": [{"id": 1}]}]}

    result = host_vars_duplicates(FLAT, inventory, captured_tenants=captured)

    assert result["refusals"] == []
    assert result["left_out"] == []
    assert result["variables"]["qumulo_smb_shares"] == [{**PROJECTS, "tenants": ["Corp"]}]


def test_a_captured_tenant_network_the_inventory_lists_with_other_values_is_refused():
    captured = [{"name": "Default", "networks": [{"id": 1, "type": "DHCP"}]}]
    inventory = {"qumulo_tenants": [{"name": "Default", "networks": [{"id": 1, "type": "STATIC", "ip_ranges": ["10.0.0.10-20"]}]}]}

    result = host_vars_duplicates(FLAT, inventory, captured_tenants=captured)

    assert result["refusals"] == [
        "network 1 of tenant Default, which the inventory lists in qumulo_tenants[Default].networks[0] and the capture with other values of type, ip_ranges"
    ]


def test_a_captured_tenant_network_the_inventory_lists_flat_for_that_tenant_is_listed():
    captured = [{"name": "Default", "networks": [{"id": 1, "type": "DHCP"}]}]
    inventory = {"qumulo_tenants": [{"name": "Default"}], "qumulo_network": {"frontend_networks": [{"id": 1, "tenant": "Default"}]}}

    assert host_vars_duplicates(FLAT, inventory, captured_tenants=captured)["refusals"] == []


def test_the_capture_s_absent_default_is_noted_as_not_written_and_not_as_a_live_tenant():
    captured = [{"name": "Default", "state": "absent"}, {"name": "Prod", "networks": []}]

    result = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Prod"}]}, captured_tenants=captured, lacks_tenant_1=True)
    declared = host_vars_duplicates(
        FLAT, {"qumulo_tenants": [{"name": "Prod"}, {"name": "Default", "state": "absent"}]}, captured_tenants=captured, lacks_tenant_1=True
    )

    assert result["left_out"] == [
        {
            "what": "the capture's Default with state: absent",
            "why": "it has a rebuild delete the Default a newly formed cluster has, and a host_vars file cannot hold qumulo_tenants beside the "
            "inventory's, so it is not written; declare it in the inventory's qumulo_tenants",
        }
    ]
    assert declared["left_out"] == []


def test_the_note_on_an_undeclared_tenant_other_than_tenant_1_says_a_rebuild_refuses_its_entries():
    result = host_vars_duplicates(FLAT, {"qumulo_tenants": []}, captured_tenants=CAPTURED)

    assert not result["left_out"][0]["why"].endswith("until qumulo_tenants declares it")
    assert result["left_out"][1]["why"].endswith("; a rebuild from the inventory refuses the entries that name it until qumulo_tenants declares it")


def test_a_null_switch_in_the_inventory_s_tenant_is_unset():
    assert host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default", "nfs_enabled": None}]}, captured_tenants=CAPTURED[:1])["refusals"] == []


@pytest.mark.parametrize(("given", "refused"), [({"session_encryption": "NONE"}, False), ({"session_encryption": "REQUIRED"}, True), ("inherit", True)])
def test_a_tenant_s_settings_compare_by_the_keys_the_inventory_gives(given, refused):
    captured = [{"name": "Default", "networks": [], "smb_settings": {"session_encryption": "NONE", "signing_required": False}}]

    result = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Default", "smb_settings": given}]}, captured_tenants=captured)

    assert result["refusals"] == (
        ["tenant Default, which the inventory declares in qumulo_tenants[Default] and the capture with other values of smb_settings"] if refused else []
    )


def test_a_captured_export_the_inventory_s_tenant_declares_otherwise_is_refused():
    inventory = {"qumulo_tenants": [{"name": "Default", "nfs_exports": [{"export_path": "/", "fs_path": "/other"}]}]}

    result = host_vars_duplicates(FLAT, inventory, captured_tenants=CAPTURED[:1])

    assert result["refusals"] == [
        (
            "the NFS export / of tenant Default, which the inventory declares in qumulo_tenants[Default].nfs_exports[0] and the capture in "
            "qumulo_tenants[Default].nfs_exports[0] with other values of fs_path"
        )
    ]


def test_the_rebuild_warning_covers_a_renamed_tenant_1_since_a_rebuild_forms_it_as_default():
    captured = [{"name": "Corp", "previous_name": "Default", "networks": []}]

    result = host_vars_duplicates(FLAT, {"qumulo_tenants": []}, captured_tenants=captured)

    # Declaring Corp alone would have a rebuild create it beside the Default it forms, not rename that Default.
    assert result["left_out"][0]["why"].endswith(
        "a rebuild from the inventory refuses the entries that name it until qumulo_tenants declares it with previous_name: Default"
    )


# Round 2 of PR 361's walkthrough.
def test_tenant_1_is_the_cluster_s_even_when_the_inventory_renames_a_default_the_cluster_no_longer_holds():
    captured = [{"name": "Main", "previous_name": "Default", "networks": [], "smb_shares": [FILES]}]
    inventory = {"qumulo_tenants": [{"name": "Corp", "previous_name": "Default"}], "qumulo_factory_defaults": {"smb_files_share": "absent"}}

    result = host_vars_duplicates(FLAT, inventory, captured_tenants=captured)

    assert result["refusals"] == []
    assert result["variables"]["qumulo_smb_shares"] == []
    assert "the SMB share Files of tenant Main" in [entry["what"] for entry in result["left_out"]]


def test_a_rename_already_done_matches_no_other_captured_tenant():
    captured = [
        {"name": "Corp", "previous_name": "Default", "networks": [], "smb_shares": [PROJECTS]},
        {"name": "Default", "networks": [], "smb_shares": [{"share_name": "D1", "fs_path": "/d1"}]},
    ]

    result = host_vars_duplicates(FLAT, {"qumulo_tenants": [{"name": "Corp", "previous_name": "Default"}]}, captured_tenants=captured)

    assert result["variables"]["qumulo_smb_shares"] == [{**PROJECTS, "tenants": ["Corp"]}, {"share_name": "D1", "fs_path": "/d1", "tenants": ["Default"]}]
    assert [entry["what"] for entry in result["left_out"]] == ["tenant Default"]


def test_a_tenant_network_listed_without_vlan_id_is_untagged():
    tagged = [{"name": "Default", "networks": [{"id": 2, "vlan_id": 20, "mtu": 1500}]}]
    untagged = [{"name": "Default", "networks": [{"id": 2}]}]
    listed = {"qumulo_tenants": [{"name": "Default", "networks": [{"id": 2}]}]}
    flat = {"qumulo_tenants": [{"name": "Default"}], "qumulo_network": {"frontend_networks": [{"id": 2, "tenant": "Default"}]}}

    assert host_vars_duplicates(FLAT, listed, captured_tenants=tagged)["refusals"] == [
        (
            "network 2 of tenant Default, which the inventory lists in qumulo_tenants[Default].networks[0] and the capture with other values of vlan_id "
            "(omitted under a tenant's entry, which is untagged)"
        )
    ]
    assert host_vars_duplicates(FLAT, listed, captured_tenants=untagged)["refusals"] == []
    # A flat network takes no vlan_id; the tenant's VLAN comes from elsewhere.
    assert host_vars_duplicates(FLAT, flat, captured_tenants=tagged)["refusals"] == []


def test_a_tenant_the_inventory_removes_still_counts_against_the_one_tenant():
    inventory = {"qumulo_tenants": [{"name": "eng", "state": "absent"}], "qumulo_smb_shares": [PROJECTS]}

    result = host_vars_duplicates({"qumulo_nfs_exports": []}, inventory, captured_tenants=CAPTURED)

    assert not any(entry["why"].startswith("the inventory declares it in qumulo_smb_shares") for entry in result["left_out"])


def test_tenants_all_is_kept_when_the_inventory_declares_the_captured_tenants():
    captured = [{"name": "A", "networks": []}, {"name": "B", "networks": []}]
    written = {"qumulo_smb_shares": [{**SHARED, "tenants": "all"}], "qumulo_nfs_exports": []}

    result = host_vars_duplicates(written, {"qumulo_tenants": [{"name": "A"}, {"name": "B"}]}, captured_tenants=captured)

    assert result == {"variables": written, "left_out": [], "refusals": []}


def test_a_captured_tenants_all_entry_is_left_out_when_the_inventory_removes_every_tenant_holding_it():
    # Kept as all, it would mean the tenant the inventory creates, which never held it.
    captured = [{"name": "Default", "networks": []}, {"name": "B", "networks": []}]
    written = {"qumulo_smb_shares": [{**SHARED, "tenants": "all"}], "qumulo_nfs_exports": []}
    inventory = {"qumulo_tenants": [{"name": "Default", "state": "absent"}, {"name": "B", "state": "absent"}, {"name": "C"}]}

    result = host_vars_duplicates(written, inventory, captured_tenants=captured)

    assert result["variables"]["qumulo_smb_shares"] == []
    assert {
        "what": "the SMB share Shared",
        "why": "its tenants: all would mean the tenants the inventory declares, and every tenant that holds it is one the inventory removes",
    } in result["left_out"]
