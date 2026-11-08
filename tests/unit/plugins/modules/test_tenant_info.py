# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.tenant_info, driven through FakeQumulo; the tenant list as spec 014's probe P1 recorded it."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import tenant_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "10.20.0.5", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
TENANTS = "/v1/multitenancy/tenants/"
SWITCHES = ("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "replication_enabled", "nfs_enabled", "smb_enabled")


def test_tenants_come_in_the_tenant_modules_shape_by_name_with_read_only_keys(fake_api, run_module):
    """Decision D30: an info module returns its area's module input shape, plus the read-only keys a caller maps by.

    identity_config_id is read-only too: tenancy_plan reads it to refuse a create whose identity configuration the
    tenants leave undecided (spec 014, US1-23).
    """
    finance = {"id": 4, "name": "finance", **dict.fromkeys(SWITCHES, False), "nfs_enabled": True, "networks": [3, 2], "identity_config_id": 2}
    default = {"id": 1, "name": "Default", **dict.fromkeys(SWITCHES, True), "networks": [1], "identity_config_id": 1}
    fake_api().expect("GET", TENANTS, response={"entries": [finance, default]})

    result = run_module(tenant_info, CONNECTION)

    assert result["changed"] is False, result.get("msg")
    assert result["tenants"] == [
        {"id": 1, "name": "Default", **dict.fromkeys(SWITCHES, True), "networks": [1], "identity_config_id": 1},
        {"id": 4, "name": "finance", **dict.fromkeys(SWITCHES, False), "nfs_enabled": True, "networks": [2, 3], "identity_config_id": 2},
    ]


def test_check_mode_reads_the_same(fake_api, run_module):
    fake_api().expect("GET", TENANTS, response={"entries": []})

    assert run_module(tenant_info, CONNECTION, check_mode=True)["tenants"] == []
