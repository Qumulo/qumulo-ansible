# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.tenancy_first_pass: what the edge_rebuild replay's tenant 1 pass declares (issues #294, #306).

A host-managed cluster with no frontend network refuses a second tenant's create at step 2 (message 1), because the
creates at 3a come before the network document at step 3. When the replay creates a tenant on such a newly formed
edge, it first runs the baseline's tenancy step with tenant 1 alone and tenant 1's own networks, or, when tenant 1
holds none, as when it is declared absent, every network the inventory lists for a tenant that stays.
"""

from __future__ import annotations

import pytest
from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.filter.tenancy_first_pass import tenancy_first_pass

FORMED = [{"id": 1, "name": "Default"}]
NO_NETWORK = {"managed_interfaces": None, "frontend_networks": []}
HOST0 = {"id": 0, "name": "Default", "type": "HOST"}
SECOND = {"name": "it-second", "networks": [], "smb_enabled": True, "smb_shares": [{"share_name": "s", "fs_path": "/"}]}


def test_a_renamed_tenant_1_goes_first_alone_with_its_networks_flat():
    first = {
        "name": "it-one",
        "previous_name": "Default",
        "networks": [HOST0],
        "rest_api_enabled": True,
        "smb_shares": [{"share_name": "one", "fs_path": "/"}],
        "nfs_settings": {"v4_enabled": False},
    }

    # Flat, as every network the pass sends: the pass's step 2 then makes no host-managed check of tenant 1's own
    # networks, which the preflight makes before the destroy, and the pass is one shape whatever tenant 1's entry holds.
    assert tenancy_first_pass([first, SECOND], FORMED, NO_NETWORK) == {
        "tenants": [{"name": "it-one", "previous_name": "Default"}],
        "network": {"frontend_networks": [HOST0]},
    }


def test_tenant_1_named_default_goes_first_wherever_the_inventory_lists_it():
    assert tenancy_first_pass([SECOND, {"name": "Default", "networks": [{**HOST0, "vlan_id": None}]}], FORMED, NO_NETWORK) == {
        "tenants": [{"name": "Default"}],
        "network": {"frontend_networks": [HOST0]},
    }


def test_the_flat_networks_step_2_leaves_in_tenant_1_go_with_it_alone():
    # Tenant 1's by its name or tenant_id 1, with no tenant, or with a tenant_id the formed edge lacks, which step 2 drops.
    # The pass sends each with no tenant, which in a run of one tenant is tenant 1. Another tenant's stays for step 3.
    own = {**HOST0, "tenant": "it-one"}
    by_id = {"id": 3, "name": "by-id", "type": "HOST", "tenant_id": 1}
    unnamed = {"id": 4, "name": "unnamed", "type": "HOST"}
    stale_id = {"id": 6, "name": "stale-id", "type": "HOST", "tenant_id": 2}
    other = {"id": 5, "name": "other", "type": "HOST", "tenant": "it-second"}
    network = {"frontend_networks": [own, by_id, unnamed, stale_id, other]}

    result = tenancy_first_pass([{"name": "it-one", "previous_name": "Default"}, {"name": "it-second"}], FORMED, NO_NETWORK, network)

    assert result == {
        "tenants": [{"name": "it-one", "previous_name": "Default"}],
        "network": {"frontend_networks": [HOST0, {"id": 3, "name": "by-id", "type": "HOST"}, unnamed, {"id": 6, "name": "stale-id", "type": "HOST"}]},
    }


def test_a_second_tenant_s_own_network_waits_for_step_3_when_tenant_1_holds_one():
    # As before this work: the baseline's step 2 then sees only tenant 1's network, which places the play's address
    # without qumulo_management_network when the play reaches the edge on it and the new edge answers its network status,
    # and step 3 adds the second tenant's.
    own = {"id": 1, "name": "second", "type": "HOST"}
    first = {"name": "it-one", "previous_name": "Default", "networks": [HOST0]}

    result = tenancy_first_pass([first, {**SECOND, "networks": [own]}], FORMED, NO_NETWORK, {"frontend_networks": []})

    assert result == {"tenants": [{"name": "it-one", "previous_name": "Default"}], "network": {"frontend_networks": [HOST0]}}


def test_with_tenant_1_holding_none_every_listed_network_goes_flat_without_its_tenant_keys():
    # vlan_id and mtu are what step 3 sends another way, and the host-managed check refuses them before the destroy.
    own = {"id": 1, "name": "second", "type": "HOST", "vlan_id": None, "mtu": None}
    other = {"id": 5, "name": "other", "type": "HOST", "tenant": "it-second"}
    first = {"name": "it-one", "previous_name": "Default", "networks": []}

    result = tenancy_first_pass([first, {**SECOND, "networks": [own]}], FORMED, NO_NETWORK, {"frontend_networks": [other]})

    assert result == {
        "tenants": [{"name": "it-one", "previous_name": "Default"}],
        "network": {"frontend_networks": [{"id": 5, "name": "other", "type": "HOST"}, {"id": 1, "name": "second", "type": "HOST"}]},
    }


@pytest.mark.parametrize(
    "entry", [None, {"name": "Default", "state": "absent", "networks": [{"id": 7, "name": "gone", "type": "HOST"}]}], ids=["undeclared", "absent"]
)
def test_tenant_1_undeclared_or_absent_holds_every_network_in_the_pass(entry):
    # Tenant 1 holds no network of its own, so the pass gives the formed tenant 1 every network, and the baseline's
    # creates find a frontend network; a tenant 1 declared absent is deleted by the baseline at 20a, once step 3 has bound
    # its networks elsewhere. An absent tenant's own networks are not sent.
    tenants = [*([entry] if entry else []), {"name": "it-third", "networks": [HOST0]}, SECOND]

    assert tenancy_first_pass(tenants, FORMED, NO_NETWORK) == {"tenants": [{"name": "Default"}], "network": {"frontend_networks": [HOST0]}}


@pytest.mark.parametrize("named", [None, "Default"], ids=["no tenant", "tenant Default"])
def test_an_undeclared_tenant_1_goes_first_with_its_flat_network(named):
    flat = {**HOST0, **({"tenant": named} if named else {})}

    result = tenancy_first_pass([{"name": "it-second", "networks": []}], FORMED, NO_NETWORK, {"frontend_networks": [flat]})

    assert result == {"tenants": [{"name": "Default"}], "network": {"frontend_networks": [HOST0]}}


@pytest.mark.parametrize(
    ("tenants", "document"),
    [
        pytest.param([{"name": "Default", "networks": [HOST0]}], NO_NETWORK, id="no tenant to create"),
        pytest.param([{"name": "it-one", "previous_name": "Default", "networks": [HOST0]}], NO_NETWORK, id="a rename alone"),
        pytest.param(
            [{"name": "Default", "networks": [HOST0]}, SECOND],
            {"managed_interfaces": None, "frontend_networks": [{"id": 0, "name": "Default", "type": "HOST", "tenant_id": 1}]},
            id="a frontend network already",
        ),
        pytest.param(
            [{"name": "Default", "networks": [HOST0]}, SECOND],
            {"managed_interfaces": {"frontend_vlans": []}, "frontend_networks": []},
            id="managed interfaces",
        ),
        pytest.param([{"name": "Default", "networks": []}, SECOND], NO_NETWORK, id="no network listed"),
        pytest.param(
            [{"name": "it-gone", "state": "absent", "networks": [HOST0]}, {"name": "Default", "networks": []}, SECOND],
            NO_NETWORK,
            id="only an absent tenant's network",
        ),
        pytest.param([{"name": "Default"}, {"name": "it-second"}], NO_NETWORK, id="no networks at all"),
    ],
)
def test_no_pass_where_the_baseline_needs_none_or_it_would_write_nothing(tenants, document):
    assert tenancy_first_pass(tenants, FORMED, document) == {}


def test_qumulo_tenants_unset_needs_no_pass():
    assert tenancy_first_pass(None, FORMED, NO_NETWORK) == {}


@pytest.mark.parametrize(
    "tenants",
    [pytest.param([{"previous_name": "Default"}], id="an entry without a name"), pytest.param(["it-one"], id="an entry that is not a dict")],
)
def test_a_tenant_without_a_name_fails_naming_qumulo_tenants(tenants):
    with pytest.raises(AnsibleFilterError, match="qumulo_tenants: each entry needs a name"):
        tenancy_first_pass(tenants, FORMED, NO_NETWORK)


def test_a_network_without_an_id_fails_naming_the_networks():
    with pytest.raises(AnsibleFilterError, match="each network needs an id"):
        tenancy_first_pass(
            [{"name": "Default", "networks": [{"name": "x", "type": "HOST"}]}, SECOND], FORMED, NO_NETWORK, {"frontend_networks": [{"name": "y"}]}
        )
