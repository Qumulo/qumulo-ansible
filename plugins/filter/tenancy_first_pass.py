# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.tenancy_first_pass, which plugins/filter/tenancy_first_pass.yml documents."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import first_pass


def tenancy_first_pass(tenants, cluster_tenants, document, network=None):
    """What the edge_rebuild replay's tenant 1 pass declares: tenancy_plan's first_pass, on the inventory's qumulo_tenants."""
    try:
        return first_pass(tenants or [], cluster_tenants, document, network)
    except KeyError as e:
        if e.args == ("id",):
            raise AnsibleFilterError("qumulo_tenants' networks and qumulo_network.frontend_networks: each network needs an id") from e
        raise AnsibleFilterError(f"qumulo_tenants: each entry needs a name, and tenant_info's tenants a name and an id ({e!r})") from e
    except (AttributeError, TypeError) as e:
        raise AnsibleFilterError(f"qumulo_tenants: each entry needs a name, and tenant_info's tenants a name and an id ({e!r})") from e


class FilterModule:
    def filters(self):
        return {"tenancy_first_pass": tenancy_first_pass}
