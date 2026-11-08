# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.nfs_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import nfs_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v2/nfs/settings"
EXPORTS = "/v3/nfs/exports/"
STORED = {"v4_enabled": False, "krb5_enabled": True, "krb5p_enabled": True, "krb5i_enabled": True, "auth_sys_enabled": True, "idmap_domain": None}
OPTIONS = {"v4_enabled": False, "krb5_enabled": True, "krb5i_enabled": True, "krb5p_enabled": True, "auth_sys_enabled": True, "idmap_domain": ""}


def export(path, tenant_id=1):
    return {"id": path, "export_path": path, "tenant_id": tenant_id, "fs_path": "/", "description": "", "restrictions": [], "fields_to_present_as_32_bit": []}


def writes(fake):
    return [e for e in fake.log if e.method == "PATCH"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(nfs_settings, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)

    result = run(run_module, krb5_enabled=True, idmap_domain="")

    assert result["changed"] is False
    assert result["nfs_settings"] == OPTIONS
    assert writes(fake) == []


def test_change_patches_only_the_changed_keys(fake_api, run_module):
    after = {**STORED, "krb5_enabled": False, "idmap_domain": "corp.example.com"}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("PATCH", SETTINGS, body={"krb5_enabled": False, "idmap_domain": "corp.example.com"}, if_match=NO_HEADER, response=after)
    fake.expect("GET", SETTINGS, response=after)

    result = run(run_module, krb5_enabled=False, idmap_domain="corp.example.com", auth_sys_enabled=True)

    assert result["changed"] is True
    assert result["nfs_settings"] == {**OPTIONS, "krb5_enabled": False, "idmap_domain": "corp.example.com"}


def test_empty_idmap_domain_clears_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "idmap_domain": "corp.example.com"})
    fake.expect("PATCH", SETTINGS, body={"idmap_domain": None}, response=STORED)
    fake.expect("GET", SETTINGS, response=STORED)

    result = run(run_module, idmap_domain="")

    assert result["changed"] is True
    assert result["nfs_settings"]["idmap_domain"] == ""


@pytest.mark.parametrize("check_mode", [False, True], ids=["apply", "check_mode"])
def test_enabling_v4_with_nested_exports_fails_before_any_write(fake_api, run_module, check_mode):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", EXPORTS, response={"entries": [export("/data"), export("/"), export("/data/projects", tenant_id=2)]})

    result = run(run_module, check_mode=check_mode, v4_enabled=True)

    assert result["failed"] is True
    assert result["msg"] == (
        "NFSv4.1 needs every export outside every other, but export '/' contains '/data'; remove or move one of them before setting v4_enabled"
    )
    assert writes(fake) == []


def test_enabling_v4_with_nested_exports_below_the_root_fails_before_any_write(fake_api, run_module):
    """With the factory / export removed, as the module's documentation advises, nesting below it still counts."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", EXPORTS, response={"entries": [export("/data/projects"), export("/data")]})

    result = run(run_module, v4_enabled=True)

    assert result["failed"] is True
    assert result["msg"] == (
        "NFSv4.1 needs every export outside every other, but export '/data' contains '/data/projects'; remove or move one of them before setting v4_enabled"
    )
    assert writes(fake) == []


def test_enabling_v4_with_sibling_exports_patches(fake_api, run_module):
    """/data and /data2 share a prefix of characters, not of path components."""
    after = {**STORED, "v4_enabled": True}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", EXPORTS, response={"entries": [export("/data"), export("/data2")]})
    fake.expect("PATCH", SETTINGS, body={"v4_enabled": True}, response=after)
    fake.expect("GET", SETTINGS, response=after)

    result = run(run_module, v4_enabled=True)

    assert result["changed"] is True


def test_v4_already_on_reads_no_exports(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "v4_enabled": True})

    result = run(run_module, v4_enabled=True)

    assert result["changed"] is False


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)

    result = run(run_module, check_mode=True, krb5p_enabled=False)

    assert result["changed"] is True
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)

    result = run(run_module, check_mode=True, diff=True, auth_sys_enabled=False)

    assert result["diff"] == {"before": OPTIONS, "after": {**OPTIONS, "auth_sys_enabled": False}}


# A tenant's override and NFSv4.1's nesting check within the tenant (spec 014, FR-015, FR-016)

TENANTS = "/v1/multitenancy/tenants/"
TENANT_LIST = {
    "entries": [{"id": 1, "name": "Default", "networks": [], "identity_config_id": 1}, {"id": 2, "name": "eng", "networks": [], "identity_config_id": 1}]
}
OVERRIDE = "/v1/multitenancy/nfs/settings/2"
NESTED_IN_ENG = {
    "entries": [
        {"id": "1", "export_path": "/", "tenant_id": 1},
        {"id": "2", "export_path": "/data", "tenant_id": 2},
        {"id": "3", "export_path": "/data/x", "tenant_id": 2},
    ]
}


def test_enabling_v4_on_a_tenants_override_checks_that_tenants_exports(fake_api, run_module):
    fake = fake_api()
    fake.serve(TENANTS, TENANT_LIST)
    fake.expect("GET", OVERRIDE, response={**STORED, "v4_enabled": False}, etag='"o1"')
    fake.expect("GET", EXPORTS, response=NESTED_IN_ENG)

    result = run(run_module, tenant="eng", v4_enabled=True)

    assert result["failed"] is True
    assert result["msg"].startswith("NFSv4.1 needs every export of tenant 'eng' outside every other, but export '/data' contains '/data/x'")


def test_inherit_checks_the_tenants_exports_when_the_global_settings_turn_v4_on(fake_api, run_module):
    """QUIRK-114: the override's DELETE skips the nesting check that the global settings then apply to the tenant."""
    fake = fake_api()
    fake.serve(TENANTS, TENANT_LIST)
    fake.expect("GET", OVERRIDE, response={**STORED, "v4_enabled": False}, etag='"o1"')
    fake.expect("GET", SETTINGS, response={**STORED, "v4_enabled": True})
    fake.expect("GET", EXPORTS, response=NESTED_IN_ENG)

    result = run(run_module, tenant="eng", state="inherit")

    assert result["failed"] is True
    assert result["msg"].endswith("remove or move one of them before inherit, which turns NFSv4.1 on from the global settings")


def test_present_creates_the_override_by_put_of_every_field_from_the_global_settings(fake_api, run_module):
    """QUIRK-115: the PUT would take the factory default for each field it left out, not the global setting."""
    fake = fake_api()
    fake.serve(TENANTS, TENANT_LIST)
    global_settings = {**STORED, "krb5_enabled": False}
    created = {**global_settings, "auth_sys_enabled": False}
    fake.expect("GET", OVERRIDE, status=404, response={"error_class": "api_nfs_tenant_settings_do_not_exist_error", "description": "test"})
    fake.expect("GET", SETTINGS, response=global_settings)
    fake.expect("PUT", OVERRIDE, if_match=NO_HEADER, response=created, etag='"o1"')
    fake.expect("GET", OVERRIDE, response=created, etag='"o1"')

    result = run(run_module, tenant="eng", auth_sys_enabled=False)

    assert result["changed"] is True, result.get("msg")
    put = next(e for e in fake.log if e.method == "PUT")
    assert set(put.body) == set(STORED)
    assert (put.body["krb5_enabled"], put.body["auth_sys_enabled"]) == (False, False)
