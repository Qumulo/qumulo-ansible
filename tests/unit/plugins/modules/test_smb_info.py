# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.smb_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import smb_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import ADMIN, EVERYONE, GUEST

TENANTS = {
    "entries": [{"id": 1, "name": "Default", "networks": [], "identity_config_id": 1}, {"id": 2, "name": "eng", "networks": [], "identity_config_id": 1}]
}


@pytest.fixture
def fake_api(fake_api):
    """A cluster with the tenants Default (1) and eng (2), whose names the shares and overrides are reported by."""

    def make(*args, **kwargs):
        fake = fake_api(*args, **kwargs)
        fake.serve("/v1/multitenancy/tenants/", TENANTS, optional=True)
        return fake

    return make


CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
ALL = ["READ", "WRITE", "CHANGE_PERMISSIONS"]
FILES = {
    "id": "2",
    "share_name": "Files",
    "tenant_id": 1,
    "fs_path": "/",
    "description": "",
    "permissions": [
        {"type": "DENIED", "trustee": GUEST, "rights": ALL},
        {"type": "ALLOWED", "trustee": ADMIN, "rights": ALL},
        {"type": "ALLOWED", "trustee": EVERYONE, "rights": ALL},
    ],
    "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ALL}],
    "access_based_enumeration_enabled": False,
    "default_file_create_mode": "0644",
    "default_directory_create_mode": "0755",
    "require_encryption": False,
    "allow_fs_path_create": False,
    "expand_fs_path_variables": False,
    "offline_files_caching_mode": "NO_CACHING",
}
FILES_OPTIONS = {
    "share_name": "Files",
    "tenant_id": 1,
    "fs_path": "/",
    "description": "",
    "permissions": [
        {"type": "DENIED", "trustee": "local:guest", "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]},
        {"type": "ALLOWED", "trustee": "local:admin", "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]},
        {"type": "ALLOWED", "trustee": "world:Everyone", "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]},
    ],
    "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]}],
    "access_based_enumeration_enabled": False,
    "require_encryption": False,
    "default_file_create_mode": "0644",
    "default_directory_create_mode": "0755",
    "expand_fs_path_variables": False,
    "offline_files_caching_mode": "NO_CACHING",
}
SETTINGS = {
    "session_encryption": "NONE",
    "supported_dialects": ["SMB2_DIALECT_3_0", "SMB2_DIALECT_3_11"],
    "hide_shares_from_unauthorized_users": False,
    "hide_shares_from_unauthorized_hosts": False,
    "snapshot_directory_mode": "VISIBLE",
    "bypass_traverse_checking": False,
    "signing_required": True,
}
SESSION = {"identity": {"domain": "LOCAL", "auth_id": "500"}, "originator": "10.0.0.5", "share_names": ["Files"], "location": "1:1"}


def expect_shares(fake):
    fake.expect("GET", "/v3/smb/shares/?populate-trustee-names=true", response={"entries": [FILES]})


# What smb_info returns for the share: its options with the tenant by name before its id (spec 014, FR-031).
NAMED = {"share_name": "Files", "tenant": "Default", **{k: v for k, v in FILES_OPTIONS.items() if k != "share_name"}}


def test_default_subsets(fake_api, run_module):
    fake = fake_api()
    expect_shares(fake)
    fake.expect("GET", "/v1/smb/settings", response=SETTINGS, etag='"e"')
    fake.expect("GET", "/v1/multitenancy/smb/settings/", response=[])
    fake.expect("GET", "/v1/smb/sessions/?limit=1000", response={"session_infos": [SESSION], "paging": {"next": None}})

    result = run_module(smb_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["smb"] == {"shares": [NAMED], "settings": SETTINGS, "tenant_settings": {}, "sessions": [SESSION]}


def test_shares_come_back_in_the_option_shape_of_smb_share(fake_api, run_module):
    fake = fake_api()
    expect_shares(fake)

    result = run_module(smb_info, {**CONNECTION, "gather_subset": ["shares"]})

    assert result["smb"] == {"shares": [NAMED]}
    assert list(result["smb"]["shares"][0])[:3] == ["share_name", "tenant", "tenant_id"]
    assert "allow_fs_path_create" not in result["smb"]["shares"][0]


def test_sessions_are_read_page_by_page(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/smb/sessions/?limit=1000", response={"session_infos": [SESSION], "paging": {"next": "/v1/smb/sessions/?after=1&limit=1000"}})
    fake.expect("GET", "/v1/smb/sessions/?after=1&limit=1000", response={"session_infos": [{**SESSION, "location": "1:2"}], "paging": {"next": None}})

    result = run_module(smb_info, {**CONNECTION, "gather_subset": ["sessions"]})

    assert [s["location"] for s in result["smb"]["sessions"]] == ["1:1", "1:2"]


def test_tenant_settings_are_each_override_by_tenant_name_and_a_tenant_without_one_is_left_out(fake_api, run_module):
    """The override list answers {key: tenant id, value: override} pairs (spec 014, probe P3)."""
    fake = fake_api()
    override = {**SETTINGS, "session_encryption": "REQUIRED"}
    fake.expect("GET", "/v1/multitenancy/smb/settings/", response=[{"key": 2, "value": override}, {"key": 9, "value": SETTINGS}])

    result = run_module(smb_info, {**CONNECTION, "gather_subset": ["tenant_settings"]})

    assert result["smb"] == {"tenant_settings": {"eng": override}}, result.get("msg")


@pytest.mark.parametrize("status", [403, 404])
def test_tenant_settings_are_empty_without_reading_the_overrides_where_the_tenant_list_cannot_be_read(fake_api, run_module, status):
    fake = fake_api()
    fake.serve("/v1/multitenancy/tenants/", {"error_class": "api_access_denied_error", "description": "test"}, status=status)

    result = run_module(smb_info, {**CONNECTION, "gather_subset": ["tenant_settings"]})

    assert result["smb"] == {"tenant_settings": {}}, result.get("msg")
    assert result.get("warnings", []) == (
        ["the tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ (api_access_denied_error)"] if status == 403 else []
    )


def test_a_user_without_the_tenant_privilege_is_warned_once_why_the_tenants_are_unnamed(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v1/multitenancy/tenants/", {"error_class": "api_access_denied_error", "description": "test"}, status=403)
    expect_shares(fake)

    result = run_module(smb_info, {**CONNECTION, "gather_subset": ["shares", "tenant_settings"]})

    assert result["smb"] == {"shares": [{**NAMED, "tenant": None}], "tenant_settings": {}}, result.get("msg")
    assert result["warnings"] == ["the tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ (api_access_denied_error)"]


def test_tenant_settings_are_empty_where_the_override_list_is_not_offered(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/multitenancy/smb/settings/", status=404, response={"error_class": "http_not_found_error", "description": "test"})

    result = run_module(smb_info, {**CONNECTION, "gather_subset": ["tenant_settings"]})

    assert result["smb"] == {"tenant_settings": {}}, result.get("msg")
