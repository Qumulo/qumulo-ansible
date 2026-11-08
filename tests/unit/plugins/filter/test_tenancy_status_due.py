# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.tenancy_status_due: the role's step 2 reads status when tenancy_plan's status_due says (decision D26).

test_tenancy_plan.py covers status_due itself; these cases pin the filter's hand-off to it and its own rules.
"""

from __future__ import annotations

import pytest
from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.filter.tenancy_status_due import tenancy_status_due

DEFAULT = {"id": 1, "name": "Default"}
FINANCE = {"id": 2, "name": "finance"}


@pytest.mark.parametrize(
    ("tenants", "cluster", "due"),
    [
        pytest.param([{"name": "Default"}], [DEFAULT, FINANCE], True, id="two tenants on the cluster"),
        pytest.param([{"name": "Default"}, {"name": "finance"}], [DEFAULT], True, id="a create"),
        pytest.param([{"name": "finance", "previous_name": "Default"}], [DEFAULT], False, id="a rename"),
    ],
)
def test_the_filter_gives_status_due_s_answer(tenants, cluster, due):
    assert tenancy_status_due(tenants, cluster) is due


@pytest.mark.parametrize("tenants", [None, []], ids=["null", "empty"])
@pytest.mark.parametrize(("cluster", "due"), [([DEFAULT], False), ([DEFAULT, FINANCE], True)], ids=["one tenant", "two tenants"])
def test_qumulo_tenants_unset_reads_status_only_on_two_tenants(tenants, cluster, due):
    assert tenancy_status_due(tenants, cluster) is due


@pytest.mark.parametrize(
    ("tenants", "cluster"),
    [
        pytest.param([{"previous_name": "Default"}], [DEFAULT], id="an entry without a name"),
        pytest.param(["finance"], [DEFAULT], id="an entry that is not a dict"),
        pytest.param([{"name": "finance"}], [{"id": 1}], id="a cluster tenant without a name"),
    ],
)
def test_a_tenant_without_a_name_fails_naming_qumulo_tenants(tenants, cluster):
    with pytest.raises(AnsibleFilterError, match="qumulo_tenants: each entry needs a name"):
        tenancy_status_due(tenants, cluster)
