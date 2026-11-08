# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.nfs_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import nfs_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

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
FACTORY = {
    "id": "1",
    "export_path": "/",
    "tenant_id": 1,
    "fs_path": "/",
    "description": "",
    "restrictions": [
        {
            "host_restrictions": [],
            "read_only": False,
            "require_privileged_port": False,
            "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
            "user_mapping": "NFS_MAP_ROOT",
            "map_to_user": {"id_type": "LOCAL_USER", "id_value": "guest"},
        }
    ],
    "fields_to_present_as_32_bit": ["FS_SIZE", "FILE_IDS"],
}
FACTORY_OPTIONS = {
    "export_path": "/",
    "tenant_id": 1,
    "fs_path": "/",
    "description": "",
    "restrictions": [
        {
            "host_restrictions": [],
            "read_only": False,
            "require_privileged_port": False,
            "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
            "user_mapping": "NFS_MAP_ROOT",
            "map_to_user": "local:guest",
        }
    ],
    "fields_to_present_as_32_bit": ["FILE_IDS", "FS_SIZE"],
}
SETTINGS = {"v4_enabled": False, "krb5_enabled": True, "krb5p_enabled": True, "krb5i_enabled": True, "auth_sys_enabled": True, "idmap_domain": None}


def test_default_subsets(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/nfs/exports/", response={"entries": [FACTORY]})
    fake.expect("GET", "/v2/nfs/settings", response=SETTINGS)
    fake.expect("GET", "/v1/multitenancy/nfs/settings/", response=[{"key": 1, "value": {**SETTINGS, "v4_enabled": True}}])

    result = run_module(nfs_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    named = {"export_path": "/", "tenant": "Default", **{k: v for k, v in FACTORY_OPTIONS.items() if k != "export_path"}}
    assert result["nfs"] == {
        "exports": [named],
        "settings": {**SETTINGS, "idmap_domain": ""},
        "tenant_settings": {"Default": {**SETTINGS, "v4_enabled": True, "idmap_domain": ""}},
    }


def test_exports_only(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/nfs/exports/", response={"entries": []})

    result = run_module(nfs_info, {**CONNECTION, "gather_subset": ["exports"]})

    assert result["nfs"] == {"exports": []}


@pytest.mark.parametrize("status", [403, 404])
def test_a_tenant_list_that_cannot_be_read_names_no_tenant_and_warns_only_of_a_missing_privilege(fake_api, run_module, status):
    fake = fake_api()
    fake.serve("/v1/multitenancy/tenants/", {"error_class": "api_access_denied_error", "description": "test"}, status=status)
    fake.expect("GET", "/v3/nfs/exports/", response={"entries": [FACTORY]})

    result = run_module(nfs_info, {**CONNECTION, "gather_subset": ["exports", "tenant_settings"]})

    assert result["nfs"]["exports"][0]["tenant"] is None, result.get("msg")
    assert result["nfs"]["tenant_settings"] == {}
    assert result.get("warnings", []) == (
        ["the tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ (api_access_denied_error)"] if status == 403 else []
    )
