# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""tenancy.py: the tenant list, the name lookup, the create's identity configuration and the cloud refusal.

The tenant bodies follow /v1/multitenancy/tenants/ in the Qumulo Core 7.10.3 API documents, and the list's shape what
spec 014's probe P1 recorded: {entries: [...]}, unpaged.
"""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import client_params

TENANTS = "/v1/multitenancy/tenants/"
PROTECTION = "/v1/cluster/protection/status"


def tenant(tenant_id, name, identity_config_id=1, networks=()):
    switches = dict.fromkeys(("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "replication_enabled", "nfs_enabled", "smb_enabled"), True)
    return {"id": tenant_id, "name": name, **switches, "networks": list(networks), "identity_config_id": identity_config_id}


DEFAULT, FINANCE, ENG = tenant(1, "Default", networks=[1]), tenant(2, "finance", networks=[2]), tenant(4, "eng")


def connect():
    return QumuloClient(client_params(api_token="t"))


def test_tenant_ids_maps_names_to_ids_and_back_from_one_read(fake_api):
    """QUIRK-111: the list has no lookup by name and no ETag, so it is read once and scanned."""
    fake = fake_api()
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, FINANCE, ENG]})
    client = connect()

    ids = tenancy.tenant_ids(client)
    again = tenancy.tenant_ids(client)

    assert ids.by_name == {"Default": 1, "finance": 2, "eng": 4}
    assert ids.by_id == {1: "Default", 2: "finance", 4: "eng"}
    assert again == ids
    assert [e.path for e in fake.log if not e.background] == [TENANTS]


def test_tenant_ids_reads_the_list_again_once_a_writer_forgets_it(fake_api):
    """A tenant create, rename or delete changes the list within the run, so the writer forgets the read."""
    fake = fake_api()
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT]})
    fake.expect("GET", TENANTS, response={"entries": [DEFAULT, FINANCE]})
    client = connect()

    assert tenancy.tenant_ids(client).by_name == {"Default": 1}
    tenancy.forget_tenants(client)
    assert tenancy.tenant_ids(client).by_name == {"Default": 1, "finance": 2}


def test_resolve_gives_the_id_of_the_tenant_of_that_exact_name(fake_api):
    fake_api().expect("GET", TENANTS, response={"entries": [DEFAULT, FINANCE]})

    assert tenancy.resolve(connect(), "finance", check_mode=False) == 2


def test_resolve_lists_the_tenants_when_the_name_is_missing(fake_api):
    fake_api().expect("GET", TENANTS, response={"entries": [DEFAULT, FINANCE, ENG]})

    with pytest.raises(QumuloError) as failure:
        tenancy.resolve(connect(), "Finance", check_mode=False)

    assert str(failure.value) == "no tenant on this cluster is named 'Finance'; its tenants are 'Default', 'eng' and 'finance'"


def test_resolve_gives_pending_in_check_mode_for_a_tenant_an_earlier_task_creates(fake_api):
    fake_api().expect("GET", TENANTS, response={"entries": [DEFAULT]})

    found = tenancy.resolve(connect(), "finance", check_mode=True)

    assert isinstance(found, tenancy.Pending)
    assert str(found) == "finance"
    assert found != "finance"  # never equal to anything, so whatever it is compared with differs


def test_create_takes_tenant_1s_identity_configuration():
    tenants = [tenant(1, "Default", identity_config_id=3), tenant(2, "finance", identity_config_id=5)]

    assert tenancy.create_identity_config(tenants) == 3


def test_create_takes_the_identity_configuration_every_tenant_shares_once_tenant_1_is_gone():
    assert tenancy.create_identity_config([tenant(2, "finance", identity_config_id=2), tenant(4, "eng", identity_config_id=2)]) == 2


def test_create_refuses_when_tenant_1_is_gone_and_the_tenants_differ():
    """Spec 014, US1-23: the message gives the tenants and the spec that will cover per-tenant identity."""
    with pytest.raises(QumuloError) as failure:
        tenancy.create_identity_config([tenant(4, "eng", identity_config_id=3), tenant(2, "finance", identity_config_id=2)])

    assert str(failure.value) == (
        "a new tenant takes the identity configuration of tenant 1, or once tenant 1 is gone the one every tenant shares, "
        "but tenant 1 is gone and the tenants use different ones: eng (identity configuration 3) and finance (identity "
        "configuration 2). Choosing one for the new tenant is per-tenant identity, which the collection does not support "
        "yet"
    )


@pytest.mark.parametrize(
    ("before", "after", "switches"),
    [(1, 2, True), (2, 1, True), (2, 3, False), (3, 2, False), (1, 1, False)],
)
def test_listener_switch_is_crossing_between_one_tenant_and_two(before, after, switches):
    assert tenancy.listener_switch(before, after) is switches


def platform_refusal():
    body = {"error_class": "multitenancy_api_platform_not_allowed_error", "description": "operation not allowed on this platform"}
    return QumuloApiError("POST", TENANTS, 403, body)


def test_the_platform_refusal_says_that_cloud_platforms_refuse_tenant_writes():
    mapped = tenancy.platform_refusal(platform_refusal())

    assert str(mapped) == (
        "this cluster refuses tenant writes (HTTP 403 multitenancy_api_platform_not_allowed_error): Qumulo Core does not "
        "allow creating, changing or deleting a tenant on a cloud platform (AWS, Azure, GCP or OCI), so a cloud cluster "
        "keeps its one tenant. Remove the tenant change from this cluster's tasks"
    )
    assert mapped.details == {"status": 403, "error_class": "multitenancy_api_platform_not_allowed_error"}


def test_any_other_error_is_not_the_platform_refusal():
    other = QumuloApiError("POST", TENANTS, 403, {"error_class": "permission_denied_error", "description": "no"})

    assert tenancy.platform_refusal(other) is other


@pytest.mark.parametrize("kind", ["PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "PROTECTION_SYSTEM_TYPE_COLD_OBJECT_BACKED"])
def test_cloud_warning_for_an_object_backed_protection_type(fake_api, kind):
    fake_api().expect("GET", PROTECTION, response={"protection_system_type": kind})

    assert tenancy.cloud_warning(connect()) == (
        f"this cluster's protection type, {kind}, marks a cloud cluster, and Qumulo Core refuses every tenant write on a "
        "cloud platform: the run fails where this check predicts a change"
    )


def test_no_cloud_warning_for_an_erasure_coded_cluster(fake_api):
    fake_api().expect("GET", PROTECTION, response={"protection_system_type": "PROTECTION_SYSTEM_TYPE_EC", "blocks_left_to_rebuild": 0})

    assert tenancy.cloud_warning(connect()) is None


def test_no_cloud_warning_when_the_protection_status_cannot_be_read(fake_api):
    """The warning is advice in a check run, so a read the API user is not allowed fails nothing."""
    fake_api().expect("GET", PROTECTION, status=403, response={"error_class": "permission_denied_error", "description": "no"})

    assert tenancy.cloud_warning(connect()) is None


def test_no_cloud_warning_when_the_cluster_does_not_offer_its_protection_status(fake_api):
    fake_api().expect("GET", PROTECTION, status=404, response={"error_class": "http_not_found_error", "description": "no route"})

    assert tenancy.cloud_warning(connect()) is None
