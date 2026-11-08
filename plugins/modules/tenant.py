#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: tenant
short_description: Manage a Qumulo cluster's network tenant
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, renames, changes or deletes a network tenant, and sets which services it allows on its networks.
  - The tenant is found by name, never by its id.
  - The network document, which M(qumulo.cluster.network_config) manages, owns which networks a tenant holds. This module
    never changes them. A create sends an empty list, a change never sends C(networks), and RV(tenant.networks) returns
    them read-only.
  - A create sends one POST with every service switch, an unset one as V(false), no networks, and the identity
    configuration of tenant 1, or, once tenant 1 is gone, the one every tenant shares. When tenant 1 is gone and the
    tenants' identity configurations differ, the create fails, because per-tenant identity is not supported. A POST
    whose answer is lost is confirmed by reading the tenant list by name.
  - A change sends one PATCH with the tenant's current ETag as If-Match, carrying only the keys that differ.
  - Before a create, a change of O(rest_api_enabled) or O(web_ui_enabled), or a delete, the module applies the continuity
    rule to the play's own connection, unless O(allow_management_disruption) is set. It judges the tenants and their
    networks as they will be immediately after the write. With one tenant after the write, that tenant must have REST
    enabled, as O(cluster_port) defines it. With two or more, each network of the management network, which is
    O(management_network) or the networks the module finds for O(api_host), must belong to a tenant with REST enabled,
    and a management network the module cannot determine is refused too. A write that fails the rule is refused before
    it is sent, with a message that says what to set. A rename, and a change of any other switch, are not judged.
  - With more than one tenant, each service listens only on the networks of the tenants that enable it.
options:
  name:
    description:
      - The tenant's name, compared exactly.
    type: str
    required: true
  previous_name:
    description:
      - The tenant's earlier name, for a rename.
      - When no tenant has O(name) and one has O(previous_name), that tenant is renamed to O(name) with one PATCH, which also carries any switch that differs.
      - When a tenant already has O(name), nothing is renamed.
      - When no tenant has either name, the module creates O(name).
      - Ignored with O(state=absent), which acts on O(name) only.
    type: str
  state:
    description:
      - Whether the tenant exists.
      - V(absent) reports no change when no tenant has O(name).
      - V(absent) fails, before any write, for the last tenant, and while the tenant holds a network, an SMB share or an
        NFS export. The message lists them.
      - Otherwise V(absent) sends one DELETE with If-Match. A DELETE whose answer is lost is confirmed by the tenant list no
        longer holding O(name). RV(removed_overrides) names the SMB and NFS overrides the delete removed, or in check mode
        would remove.
    type: str
    choices: [present, absent]
    default: present
  web_ui_enabled:
    description:
      - Whether the tenant's networks serve the web UI, on TCP 80 and 443.
      - Unset is V(false) when the tenant is created, and unchanged on an existing tenant.
      - A change of it is judged by the continuity rule; O(cluster_port) says which of the two switches REST enabled means.
    type: bool
  rest_api_enabled:
    description:
      - Whether the tenant's networks serve the REST API, on TCP 8000.
      - Unset is V(false) when the tenant is created, and unchanged on an existing tenant.
      - A change of it is judged by the continuity rule; O(cluster_port) says which of the two switches REST enabled means.
    type: bool
  ssh_enabled:
    description:
      - Whether the tenant's networks serve SSH, on TCP 22.
      - Unset is V(false) when the tenant is created, and unchanged on an existing tenant.
    type: bool
  replication_enabled:
    description:
      - Whether the tenant's networks serve replication, on TCP 3712.
      - Unset is V(false) when the tenant is created, and unchanged on an existing tenant.
    type: bool
  nfs_enabled:
    description:
      - Whether the tenant's networks serve NFS, on TCP 111 and 2049.
      - Unset is V(false) when the tenant is created, and unchanged on an existing tenant.
    type: bool
  smb_enabled:
    description:
      - Whether the tenant's networks serve SMB, on TCP 445.
      - Unset is V(false) when the tenant is created, and unchanged on an existing tenant.
    type: bool
  management_network:
    description:
      - The ids of the networks this play reaches the cluster on, which the continuity rule keeps in a tenant with REST
        enabled. Network id 0 is a real id.
      - Used only when two or more tenants remain after a judged write.
      - Given, each id must be a network on the cluster, and an empty list fails. In check mode with
        O(planned_networks), an id that O(planned_networks) binds is accepted though the cluster lacks it, for a
        network that the C(cluster_baseline) role's network step adds. The module takes the ids as given, without checking that a HOST network among them
        shows on a device, and reads no network status.
      - Unset, the module resolves the addresses of O(api_host) on the controller and finds the network whose ranges hold
        each one in the network document. For an address no range holds, or one on a HOST network, it reads the cluster's
        network status, which must place the address and show a HOST network on a device.
    type: list
    elements: int
  cluster_port:
    description:
      - The TCP port the cluster listens on for this play, when it differs from O(api_port) because a load balancer or
        tunnel sits in between.
      - It decides which switch "REST enabled" means in the continuity rule. V(443) means O(web_ui_enabled), since the web
        UI listener forwards C(/api) to REST. Any other port means O(rest_api_enabled).
      - Unset, O(api_port) is taken as the cluster port, except that at O(api_port=443) the module cannot tell which
        listener the play reaches, fails closed, and needs both O(web_ui_enabled) and O(rest_api_enabled) on.
    type: int
  allow_management_disruption:
    description:
      - Skip the continuity rule, so the module sends a write that may cut this play off from the cluster.
      - It does not lift the refusal of a second tenant while a backend network is held, or the delete refusals at O(state).
    type: bool
    default: false
  planned_networks:
    description:
      - Which tenant holds each network, as it will be immediately after this write. The keys are network ids and the
        values are tenant names.
      - Used only in check mode, where it also tells which networks the document will hold, so an
        O(management_network) id it binds counts as a network on the cluster. Set by the cluster_baseline role, not by
        a task written by hand.
    type: dict
  planned_tenants:
    description:
      - Whether each tenant has REST enabled, as it will be immediately after this write. The keys are tenant names and
        the values are booleans.
      - Used only in check mode. Set by the cluster_baseline role, not by a task written by hand.
    type: dict
  content_removed:
    description:
      - Whether the cluster_baseline role removes the tenant's networks, shares and exports before this delete.
      - Used only in check mode, where V(true) skips the refusal for a tenant that still holds a network, an SMB share or an
        NFS export. The refusal of the last tenant still applies. Set by the cluster_baseline role, not by a task written
        by hand.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Predicts the change as on any cluster, including a cloud cluster that refuses the real write.
      - Makes the same refusals as a real run.
      - Warns, before a predicted create or change, when the cluster's protection type is object-backed, because the real
        write would be refused.
  diff_mode:
    support: full
    details:
      - Shows the tenant before and after, as O(name) and the six switches, with RV(tenant.networks) the same on both sides.
      - A create's before is empty and its after has no networks. A delete's after is empty. A rename shows the old name
        before and the new one after.
  idempotent:
    support: full
notes:
  - "API: GET and POST /v1/multitenancy/tenants/; GET, PATCH (If-Match) and DELETE (If-Match)
    /v1/multitenancy/tenants/{tenant_id}; GET /v3/network for the second-tenant check, the continuity rule, and the network names in a delete refusal;
    GET /v3/network/status only for a judged write that leaves two or more tenants, without O(management_network), when
    no network's ranges place an address of O(api_host) or one places it on a HOST network; before a delete, GET
    /v3/smb/shares/, /v3/nfs/exports/, /v1/multitenancy/smb/settings/ and /v1/multitenancy/nfs/settings/; in check mode,
    before a predicted write, GET /v1/cluster/protection/status."
  - Qumulo marks the multitenancy REST API this module uses as preview; a later Qumulo Core release may change it.
  - A delete removes the tenant's shares, exports and SMB and NFS overrides with it, so the module refuses it while the
    tenant holds any share or export (G-TENANT-CASCADE).
  - Qumulo Core refuses every tenant write on AWS, Azure, GCP and OCI, and the module maps that refusal to a message
    that says so (G-TENANT-CLOUD).
  - The first create of a second tenant, and the delete that leaves one tenant, rebind every listener on every node and
    end every NFS and replication connection. The module warns of it in the run, and in a check run that predicts it
    (G-TENANT-LISTEN).
  - Every create removes the static routes of every node. The module warns of it in the run, and in a check run that
    predicts it (G-TENANT-ROUTES).
  - A create that would make a second tenant is refused while any tenant holds a network that is not on the frontend
    bond, a frontend VLAN or a HOST network, such as a backend-bond network, and the message names the network
    (G-TENANT-BACKEND).
  - When C(GET /v3/network/status) answers HTTP 500, as the Qumulo Core container does, a judged write that leaves two
    or more tenants is refused unless O(management_network) is given (G-NET-APPLY).
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task sets api_port. Set them once for the play with
# module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# A tenant holds no network until network_config gives it one, and a switch left unset is off on a new tenant. Every
# create removes the static routes of every node, and the first create of a second tenant also rebinds every listener on
# every node and ends NFS and replication connections; the module warns of it in the run and in a check run, so run it
# in check mode first. That create is refused while a tenant holds a backend-bond network: unbind the network first with
# a tenant PATCH of networks that leaves it out (qq multitenancy_modify_tenant --network-id), not with
# qq network_mod_network --clear-tenant-id, which puts it back in the only tenant.
- name: Create the finance tenant with NFS only
  qumulo.cluster.tenant:
    name: finance
    nfs_enabled: true

# The preview shows the tenant before and after. Check mode sends nothing, so it reports the same change until a real run
# makes it.
- name: Preview turning on SMB and SSH for the finance tenant
  qumulo.cluster.tenant:
    name: finance
    smb_enabled: true
    ssh_enabled: true
  check_mode: true
  diff: true
  register: tenant_preview

- name: Show the switches the finance tenant would have
  ansible.builtin.debug:
    var: tenant_preview.tenant

# The switches the task does not set stay as they are. A second run reports no change.
- name: Turn SMB on for the finance tenant
  qumulo.cluster.tenant:
    name: finance
    smb_enabled: true

# A run after the rename finds eng and renames nothing. When a tenant named eng already exists, the task manages eng and
# leaves engineering as it is; when neither exists, it creates eng.
- name: Rename the engineering tenant to eng
  qumulo.cluster.tenant:
    name: eng
    previous_name: engineering

# With two or more tenants, REST must stay on for a tenant that holds the network this play connects on; the module
# refuses a write that would cut the play off. Behind NAT or a load balancer it cannot see which network that is, so
# name it. Network 1 here is the network api_host is forwarded to.
- name: Create the analytics tenant from a play that reaches the cluster behind NAT
  qumulo.cluster.tenant:
    name: analytics
    rest_api_enabled: true
    ssh_enabled: true
    management_network: [1]

# The play connects to a tunnel's port 18000, which forwards to the cluster's web UI port 443. With cluster_port: 443,
# REST enabled means web_ui_enabled, so this change of rest_api_enabled passes while the tenant that holds network 2,
# the network the tunnel reaches, has web_ui_enabled on. Without cluster_port the module takes 18000 as the cluster's
# port, judges rest_api_enabled, and refuses the write if finance holds network 2.
- name: Turn off REST for the finance tenant, from a play on a tunnel to the web UI port
  qumulo.cluster.tenant:
    name: finance
    rest_api_enabled: false
    api_port: 18000
    cluster_port: 443
    management_network: [2]

# The delete is refused while the tenant holds a network, an SMB share or an NFS export, and for the last tenant.
# Move its networks to another tenant with network_config, and delete its shares and exports, first.
# The delete that leaves one tenant rebinds every listener on every node and ends NFS and replication connections, and
# the module warns of it.
# A second run reports no change.
- name: Delete the finance tenant
  qumulo.cluster.tenant:
    name: finance
    state: absent
  register: finance_removed

- name: Show the overrides the delete removed with the finance tenant
  ansible.builtin.debug:
    var: finance_removed.removed_overrides
  when: finance_removed.removed_overrides is defined
"""

RETURN = r"""
tenant:
  description: The tenant after the task, or in check mode the tenant the task would leave; empty after a delete.
  returned: always
  type: dict
  contains:
    name:
      description: The tenant's name.
      type: str
    web_ui_enabled:
      description: Whether the tenant's networks serve the web UI.
      type: bool
    rest_api_enabled:
      description: Whether the tenant's networks serve the REST API.
      type: bool
    ssh_enabled:
      description: Whether the tenant's networks serve SSH.
      type: bool
    replication_enabled:
      description: Whether the tenant's networks serve replication.
      type: bool
    nfs_enabled:
      description: Whether the tenant's networks serve NFS.
      type: bool
    smb_enabled:
      description: Whether the tenant's networks serve SMB.
      type: bool
    networks:
      description:
        - The ids of the networks the tenant holds, sorted, read-only.
        - The module never changes them. M(qumulo.cluster.network_config) does.
      type: list
      elements: int
  sample:
    name: finance
    web_ui_enabled: false
    rest_api_enabled: false
    ssh_enabled: false
    replication_enabled: false
    nfs_enabled: true
    smb_enabled: false
    networks: [2]
removed_overrides:
  description: The overrides the delete removed with the tenant, or in check mode would remove, V(smb) and V(nfs).
  returned: after a delete, or a check-mode delete, of a tenant that has an SMB or NFS override
  type: list
  elements: str
  sample: [smb, nfs]
notes:
  description:
    - What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["write reconciled after a lost connection"]
status:
  description:
    - The HTTP status of a request the cluster answered with an error, including a tenant write that a cloud platform
      refuses.
    - The collection-wide return keys are listed in C(docs/reference/README.md).
  returned: on a failure the cluster answered with an HTTP error
  type: int
  sample: 403
error_class:
  description: The error class the cluster named for that request, or V(unknown).
  returned: on a failure the cluster answered with an HTTP error
  type: str
  sample: multitenancy_api_platform_not_allowed_error
cluster_unanswered:
  description: V(true) when the cluster did not answer before the retry deadline, which tells a cluster that did not answer from one that refused.
  returned: on a failure because the cluster did not answer
  type: bool
  sample: true
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the tenant back did not confirm it.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the keys that did not converge.
  returned: when the tenant did not converge
  type: dict
  sample: {smb_enabled: true}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the tenant did not converge
  type: dict
  sample: {smb_enabled: false}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy import SWITCHES, Tenant

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "previous_name": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    **{switch: {"type": "bool"} for switch in SWITCHES},
    "management_network": {"type": "list", "elements": "int"},
    "cluster_port": {"type": "int"},
    "allow_management_disruption": {"type": "bool", "default": False},
    "planned_networks": {"type": "dict"},
    "planned_tenants": {"type": "dict"},
    "content_removed": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC, required_together=[["planned_networks", "planned_tenants"]]), Tenant)


if __name__ == "__main__":
    main()
