# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.tenancy_status_due, which plugins/filter/tenancy_status_due.yml documents."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import status_due


def tenancy_status_due(tenants, cluster_tenants):
    """Whether step 2 reads the network status: tenancy_plan's status_due, on the inventory's qumulo_tenants."""
    try:
        return status_due(tenants or [], cluster_tenants)
    except (AttributeError, KeyError, TypeError) as e:
        raise AnsibleFilterError(f"qumulo_tenants: each entry needs a name, and tenant_info's tenants a name and an id ({e!r})") from e


class FilterModule:
    def filters(self):
        return {"tenancy_status_due": tenancy_status_due}
