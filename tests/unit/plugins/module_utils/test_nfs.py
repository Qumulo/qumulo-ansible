# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""nfs.py's nested-export check, which pairs export paths within one tenant (spec 014, FR-016).

Export names are unique per tenant, so two tenants may hold paths that would nest across them; NFSv4.1 refuses
nesting within a tenant only. Bodies follow /v3/nfs/exports/ in the Qumulo Core 7.10.3 API documents.
"""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils import nfs
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import client_params

EXPORTS = "/v3/nfs/exports/"


def exports(*pairs):
    return {"entries": [{"id": str(i), "export_path": path, "tenant_id": tenant} for i, (path, tenant) in enumerate(pairs, 1)]}


def connect():
    return QumuloClient(client_params(api_token="t"))


def test_tenant_a_at_the_root_and_tenant_b_at_home_do_not_nest(fake_api):
    fake_api().expect("GET", EXPORTS, response=exports(("/", 1), ("/home", 2)))

    assert nfs.nested_export(connect()) is None


def test_two_paths_of_one_tenant_nest_whatever_the_other_tenants_hold(fake_api):
    fake_api().expect("GET", EXPORTS, response=exports(("/", 1), ("/data", 2), ("/data/projects", 2)))

    assert nfs.nested_export(connect()) == ("/data", "/data/projects")


def test_the_check_for_one_tenant_looks_only_at_that_tenants_paths(fake_api):
    """An override's check: tenant 1 nests, tenant 2 does not."""
    fake = fake_api()
    fake.expect("GET", EXPORTS, response=exports(("/", 1), ("/home", 1), ("/data", 2)))
    fake.expect("GET", EXPORTS, response=exports(("/", 1), ("/home", 1), ("/data", 2)))
    client = connect()

    assert nfs.nested_export(client, tenant_id=2) is None
    assert nfs.nested_export(client, tenant_id=1) == ("/", "/home")
