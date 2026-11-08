#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: network_config
short_description: Manage a Qumulo cluster's network document
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Manages the cluster's whole network document, its frontend networks and bond and VLAN interfaces, through one
    read and, when something differs, one write.
  - The cluster re-runs static and floating IP allocation on every write, even one that changes nothing (QUIRK-002),
    so the module writes only when the document differs, comparing it the way the server stores it. The placement of
    static addresses on nodes cannot be pinned (G-NET-PLACEMENT).
  - Address ranges are compared as sets of addresses, so V(10.1.1.20-21), V(10.1.1.20-10.1.1.21) and the two single
    addresses are the same. A netmask such as V(24) is the same as V(255.255.255.0). VLANs are matched by their VLAN
    id, not their order.
  - The document has three parts. V(interfaces) is the bond and VLAN interfaces, V(networks) is each frontend
    network's name, tenant, type, netmask, gateway and static address ranges, and V(floating_ips) is each network's
    floating address ranges. The module manages only the parts in O(owned_parts) and keeps the cluster's values for
    the others. With O(managed_by=terraform_provider), V(floating_ips) is left out of O(owned_parts) by default, as
    the provider applies its floating addresses again on CNQ.
  - The module refuses a change that drops the address in O(api_host) from the static or floating ranges, unless
    O(allow_management_disruption=true) and O(api_host_after) names an address to connect to afterwards.
  - On a cluster whose network the host manages, such as a container or a host-managed cloud cluster, every network
    has type V(HOST) and only its name, tenant, netmask and floating ranges can change.
  - The module names a network's tenant by name, through O(frontend_networks[].tenant). Every write sends each frontend
    network's tenant, the declared one or the one the cluster reports, except for a network on the backend bond, which
    never carries one.
  - Before a write the module refuses a document that gives networks of two tenants to one interface or VLAN, naming the
    interface or VLAN, because the cluster's own write does not check it. It refuses only when the write changes what
    that interface or VLAN holds. A conflict the cluster already has, as another writer left it, is warned of and does
    not block a write that leaves that interface or VLAN alone. With more than one tenant after the write, it refuses a
    new network that names no tenant, unless the network is on the backend bond.
  - Before every write that changes the document, the module refuses a document in which two or more V(STATIC) networks
    carry a default gateway of one IP version, and the message names the networks and the remedy;
    O(frontend_networks[].default_gateway) states the rule. The refusal comes before the tenant checks above, the
    continuity rule and check mode's validation, so it holds in check mode too.
  - Before every write that changes the document, the module applies the continuity rule to the play's own connection,
    unless O(allow_management_disruption) is set. It judges the tenants and their networks as they will be immediately
    after the write. With one tenant, that tenant must have REST enabled, as O(cluster_port) defines it. With two or
    more, each network of the management network, which is O(management_network) or the networks the module finds for
    O(api_host), must belong to a tenant with REST enabled, and a management network the module cannot determine is
    refused too. A write that fails the rule is refused before it is sent, in check mode too, with a message that says
    what to set.
  - With more than one tenant, each service listens only on the networks of the tenants that enable it.
options:
  frontend_networks:
    description:
      - The frontend networks. With V(networks) in O(owned_parts) the list is complete, so a network that is not
        listed is removed; unset keeps the cluster's networks.
      - An option left unset in an entry keeps that network's current value; a new network starts empty.
      - Without V(networks) in O(owned_parts), each entry names an existing network by O(frontend_networks[].id) and
        sets only its O(frontend_networks[].floating_ip_ranges).
    type: list
    elements: dict
    suboptions:
      id:
        description: The network's id, chosen by the client and stable across runs.
        type: int
        required: true
      name:
        description: The network's name. Needed for each network when V(networks) is owned.
        type: str
      type:
        description: How nodes get their addresses. Needed for each network when V(networks) is owned.
        type: str
        choices: [DHCP, STATIC, HOST]
      tenant:
        description:
          - The tenant the network belongs to, by name, compared exactly. It is part of V(networks).
          - Unset keeps the tenant the cluster reports for an existing network. A new network left without one is held
            by the only tenant on a cluster with one tenant, and is refused on a cluster with more, unless it is on the
            backend bond.
          - Outside check mode, a name that no tenant has fails, and the message lists the tenants. In check mode such a
            name is taken as a tenant an earlier task creates, so the module reports a change with a note, the result
            shows the network with that name and a V(null) C(tenant_id), and the document it validates sends the
            network without a tenant.
          - A network on the backend bond takes no tenant, so a tenant declared on one is refused (G-TENANT-BACKEND).
            Its C(tenant) is V(null) in the results and the diff, whatever C(tenant_id) shows, so a document read with
            M(qumulo.cluster.network_info) is accepted as it is; the C(tenant_id) in it is dropped with a warning.
          - Needs the tenant list, which needs PRIVILEGE_TENANT_READ.
        type: str
      tenant_id:
        description:
          - Deprecated, and removed in release 0.3.0 of this collection; use O(frontend_networks[].tenant). The id of
            the tenant the network belongs to.
          - Accepted beside O(frontend_networks[].tenant) when both name one tenant, so a document read with
            M(qumulo.cluster.network_info) can be given back, and refused when they differ, naming both.
          - An id that no tenant has is dropped with a warning, and the network keeps the tenant the cluster reports.
            On a network of the backend bond it is dropped with a warning too (G-TENANT-BACKEND).
        type: int
      netmask:
        description: The netmask, as a prefix length such as V(24) or a dotted mask such as V(255.255.255.0).
        type: str
      default_gateway:
        description:
          - The default gateway of a V(STATIC) network.
          - Unset on a new network, the network has none, sent as an empty gateway. Unset on an existing network, the
            cluster's gateway is kept. V("") removes it.
          - A chassis cluster takes one IPv4 and one IPv6 default gateway in all, so at most one V(STATIC) network carries
            an IPv4 gateway and at most one an IPv6 gateway. The module refuses a second one of either version on every
            cluster, before the write and in check mode too, unless the cluster already gives each of those networks a
            gateway of that version.
        type: str
      ip_ranges:
        description:
          - The static address ranges of a V(STATIC) network, as single addresses, V(first-last) ranges or CIDR
            blocks. A CIDR block's network and broadcast addresses are not used.
        type: list
        elements: str
      floating_ip_ranges:
        description: The floating address ranges. Needs V(floating_ips) in O(owned_parts).
        type: list
        elements: str
  managed_interfaces:
    description:
      - The bond and VLAN interfaces, in the shape C(GET /v3/network) returns them; V(null) on a cluster whose network
        the host manages. Unset keeps the cluster's. Needs V(interfaces) in O(owned_parts).
      - A key left out keeps the cluster's value, in the bonds and in each VLAN too, and a key set to V(null) compares
        equal to one the cluster leaves out. VLANs are matched by C(vlan_id), and C(frontend_vlans) is the whole set.
    type: dict
  owned_parts:
    description:
      - The parts of the document this task manages; the cluster's values are kept for the others.
      - Unset means every part, except V(floating_ips) when O(managed_by=terraform_provider).
      - A value given for a part that is not owned fails before any request is sent.
    type: list
    elements: str
    choices: [interfaces, networks, floating_ips]
  managed_by:
    description: Who formed the cluster, as the inventory's C(qumulo_managed_by) says.
    type: str
    choices: [none, terraform_provider, legacy_provisioner]
    default: none
  take_over_from_terraform:
    description: Allow V(floating_ips) in O(owned_parts) when O(managed_by=terraform_provider).
    type: bool
    default: false
  validate:
    description: In check mode, ask the cluster to validate the new document, which writes nothing, so that check mode reports what the cluster would refuse.
    type: bool
    default: true
  allow_management_disruption:
    description:
      - Allow a change that drops the address in O(api_host). O(api_host_after) is then needed.
      - It also skips the continuity rule, so the module sends a write that may cut this play off from the cluster.
      - It does not lift the tenant refusals, such as a new network with no tenant on a cluster with more than one
        tenant, a write that puts networks of two tenants on one interface or VLAN, and a tenant on a backend network.
    type: bool
    default: false
  api_host_after:
    description: The address to reach the cluster at after a change that drops O(api_host); the module reads the result back through it.
    type: str
  management_network:
    description:
      - The ids of the networks this play reaches the cluster on, which the continuity rule keeps in a tenant with REST
        enabled. Network id 0 is a real id.
      - Used only when two or more tenants remain after the write.
      - Given, each id must be a network in the document the write leaves, and an empty list fails. A network of the set
        that the write removes is refused. The module takes the ids as given, without checking that a HOST network among
        them shows on a device, and reads no network status.
      - Unset, the module resolves the addresses of O(api_host) on the controller and finds the network whose ranges hold
        each one in the document the write leaves. For an address no range holds, or one on a HOST network, it reads the
        cluster's network status, which must place the address and show a HOST network on a device.
    type: list
    elements: int
  cluster_port:
    description:
      - The TCP port the cluster listens on for this play, when it differs from O(api_port) because a load balancer or
        tunnel sits in between.
      - It decides which switch "REST enabled" means in the continuity rule. V(443) means C(web_ui_enabled), since the web
        UI listener forwards C(/api) to REST. Any other port means C(rest_api_enabled).
      - Unset, O(api_port) is taken as the cluster port, except that at O(api_port=443) the module cannot tell which
        listener the play reaches, fails closed, and needs both C(web_ui_enabled) and C(rest_api_enabled) on. That holds
        with one tenant too.
    type: int
  planned_tenants:
    description:
      - Whether each tenant has REST enabled, as it will be immediately after this write. The keys are tenant names and
        the values are booleans.
      - The keys are also the tenants after the write. With more than one key, a new network that names no tenant is
        refused. With one key, the continuity rule asks only that tenant, unless a network of the document the write
        leaves is bound to a tenant the keys leave out.
      - A tenant the keys leave out that the cluster has is judged by its switches as the cluster holds them now. A name
        that neither the keys nor the cluster have is taken as a tenant an earlier task creates, which the module notes
        and does not judge.
      - Used only in check mode. Set by the cluster_baseline role, not by a task written by hand.
    type: dict
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Makes the refusals a real run makes, the continuity rule's included, and with O(validate) has the cluster
        validate the new document.
      - A tenant name the cluster lacks is taken as one an earlier task creates, rather than refused, as
        O(frontend_networks[].tenant) says. With O(planned_tenants) set, the refusals and the continuity rule take the
        tenants after the write from it instead of from the cluster, as O(planned_tenants) says.
      - A network the write puts in a tenant that neither O(planned_tenants), when set, nor the cluster has is not
        judged on that tenant's REST switches, which do not exist yet. The module adds a note, and the real run judges
        them.
  diff_mode:
    support: full
    details:
      - Shows the document before and after. Each network carries C(tenant), its tenant's name, beside C(tenant_id).
        C(tenant) is V(null) for a network of the backend bond, and in a run that read no tenant list.
  idempotent:
    support: partial
    details:
      - Writes only on a difference; a write re-runs address allocation, and static placement cannot be pinned.
notes:
  - "API: GET and PUT /v3/network (If-Match; the PUT is never sent without a difference); in check mode,
    PUT /v3/network/validate without If-Match, since it writes nothing; GET /v1/multitenancy/tenants/ when the
    document differs or an entry names a tenant (O(frontend_networks[].tenant) or O(frontend_networks[].tenant_id)),
    which needs PRIVILEGE_TENANT_READ; GET /v3/network/status only for a write that leaves two or more tenants, without
    O(management_network) or O(allow_management_disruption), when the ranges of the document the write leaves place an
    address of O(api_host) on no network or on a HOST network, and never with one tenant."
  - O(frontend_networks[].tenant) resolves through the multitenancy REST API, which Qumulo marks as preview; a later
    Qumulo Core release may change it.
  - The network applies asynchronously after the write. The module verifies the stored document, not each node's
    addresses (G-NET-APPLY).
  - The continuity rule reads an HTTP 500 from C(GET /v3/network/status), which the Qumulo Core container answers, as
    unavailable, as it does a status the cluster does not offer. With two or more tenants after the write and no
    O(management_network), the module then refuses a write when an address of O(api_host) is on no network's ranges or
    on a HOST network, with a message that names O(management_network). A given O(management_network) is taken as
    given, a HOST network among its ids included, and the status is not read (G-NET-APPLY).
  - The refusal of a change that drops O(api_host) checks only an O(api_host) that is an IP address. The continuity rule
    also resolves a name on the controller and checks each of its addresses.
  - This module never returns C(maybe_applied). After a lost connection it reads the document back, and fails with
    RV(expected) and RV(observed) when the read-back differs from what it wrote.
  - A write that names a tenant for a new network, or a different tenant for an existing one, removes the static routes
    of every node. The module warns of it in the run, and in a check run that predicts it, naming each network and the
    tenant it is bound to, as in "binds network 2 (eng) to tenant 'finance'". It warns after the write, and in check
    mode after the validation, so a write the module refuses warns of nothing (G-TENANT-ROUTES).
  - A network on the backend bond takes no tenant. The cluster's write accepts one, and once there are two tenants such a
    network aborts every node, so the module never sends one. With one tenant the cluster puts a network sent without a
    tenant in that tenant, so each write binds a backend network to it again, including one the operator unbound before
    adding a second tenant (G-TENANT-BACKEND).
  - With more than one tenant, REST answers only on the networks of the tenants that enable it, so the continuity rule
    judges every write that changes the document (G-TENANT-LISTEN).
  - A managed network document that holds a DHCP network holds no other network. The cluster refuses such a document,
    and a check run, which validates the document with the cluster, fails the same way (G-NET-DHCP).
  - "Registers: G-NET-APPLY, G-NET-DHCP, G-NET-PLACEMENT, G-TENANT-BACKEND, G-TENANT-LISTEN, G-TENANT-ROUTES, QUIRK-002."
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task sets api_port. Set them once for the play with
# module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Rename the host-managed network, leaving the floating addresses to Terraform
  qumulo.cluster.network_config:
    managed_by: terraform_provider
    frontend_networks:
      - {id: 1, name: Default, type: HOST, netmask: "24"}

- name: Static addresses for an on-premises edge
  qumulo.cluster.network_config:
    frontend_networks:
      - id: 1
        name: Default
        type: STATIC
        netmask: "24"
        default_gateway: 10.20.0.1
        ip_ranges: [10.20.0.5]
        floating_ip_ranges: [10.20.0.50-51]

# In check mode the cluster validates the new document without writing it, so the check run fails on a document
# the cluster would refuse.
- name: Preview two more floating addresses
  qumulo.cluster.network_config:
    owned_parts: [floating_ips]
    frontend_networks:
      - {id: 1, floating_ip_ranges: [10.20.0.50-53]}
  check_mode: true
  diff: true
  register: network_preview

- name: Show the networks the change would leave
  ansible.builtin.debug:
    var: network_preview.network_config.frontend_networks

- name: Add two floating addresses, keeping the static addresses and interfaces
  qumulo.cluster.network_config:
    owned_parts: [floating_ips]
    frontend_networks:
      - {id: 1, floating_ip_ranges: [10.20.0.50-53]}

- name: Set the frontend bond's MTU to 9000, keeping the networks
  qumulo.cluster.network_config:
    owned_parts: [interfaces]
    managed_interfaces:
      frontend_bond_config: {mtu: 9000}

# The finance tenant and network 2 must exist already; the tenant module creates a tenant. Network 2 must be on an
# interface or VLAN of its own, since the networks of one interface or VLAN belong to one tenant. With networks owned the
# list is the whole set, so it names network 1 too; a network that names no tenant keeps the one the cluster reports.
# Binding a network to another tenant removes the static routes of every node, and the check run warns of it. The check
# run also validates the document with the cluster. When an earlier task of the same play creates the finance tenant, a
# check run reports the change with a note and validates the network without a tenant.
- name: Preview binding the Finance network to the finance tenant
  qumulo.cluster.network_config:
    frontend_networks:
      - {id: 1, name: Default, type: STATIC}
      - {id: 2, name: Finance, type: STATIC, tenant: finance}
  check_mode: true
  diff: true
  register: tenant_preview

- name: Show each network and the tenant that would hold it
  ansible.builtin.debug:
    msg: "{{ item.name }}: {{ item.tenant }}"
  loop: "{{ tenant_preview.network_config.frontend_networks }}"
  loop_control:
    label: "{{ item.id }}"

# A second run reports no change. A network that names no tenant keeps its tenant, so network 1 stays where it is.
- name: Bind the Finance network to the finance tenant
  qumulo.cluster.network_config:
    frontend_networks:
      - {id: 1, name: Default, type: STATIC}
      - {id: 2, name: Finance, type: STATIC, tenant: finance}

# With two or more tenants the change is judged on the network this play reaches the cluster on: the write is refused
# if the tenant that holds that network would not have REST enabled afterwards. Behind NAT or a load balancer the
# module cannot see which network that is, so name it. Network 1 here is the network api_host is forwarded to.
- name: Add floating addresses from a play that reaches a two-tenant cluster behind NAT
  qumulo.cluster.network_config:
    owned_parts: [floating_ips]
    frontend_networks:
      - {id: 1, floating_ip_ranges: [10.20.0.50-55]}
    management_network: [1]

# The play connects to port 18000 of a tunnel that forwards to the cluster's web UI port 443. With cluster_port: 443,
# REST enabled means web_ui_enabled, so the write passes while the tenant that holds network 1 has web_ui_enabled on.
# Without cluster_port the module takes 18000 as the cluster's port and judges rest_api_enabled instead.
- name: Add floating addresses from a play on a tunnel to the web UI port
  qumulo.cluster.network_config:
    owned_parts: [floating_ips]
    frontend_networks:
      - {id: 1, floating_ip_ranges: [10.20.0.50-55]}
    api_port: 18000
    cluster_port: 443
    management_network: [1]

# On a CNQ cluster the Terraform provider formed, the provider applies its own floating addresses again on its next run.
# On an edge or bare-metal cluster the provider formed, take_over_from_terraform with floating_ips in owned_parts hands
# the floating addresses to this task.
- name: Set the floating addresses here although Terraform owns them
  qumulo.cluster.network_config:
    managed_by: terraform_provider
    owned_parts: [floating_ips]
    take_over_from_terraform: true
    frontend_networks:
      - {id: 1, floating_ip_ranges: [10.20.0.50-53]}

# Dropping the address the play connects to needs both options. Later tasks connect to the returned api_host.
- name: Move the edge's static address
  qumulo.cluster.network_config:
    frontend_networks:
      - id: 1
        name: Default
        type: STATIC
        ip_ranges: [10.20.0.6]
    allow_management_disruption: true
    api_host_after: 10.20.0.6
  register: readdressed

# The playbooks' module_defaults read qumulo_api_host.
- name: Connect to the new address from here on
  ansible.builtin.set_fact:
    qumulo_api_host: "{{ readdressed.api_host }}"
  when: readdressed.api_host is defined

# With networks owned, the list is the whole set, so a network left out is removed.
- name: Remove every network but the default one, keeping its settings
  qumulo.cluster.network_config:
    frontend_networks:
      - {id: 1, name: Default, type: STATIC}
"""

RETURN = r"""
network_config:
  description: The document after the task, or in check mode the document the task would leave, in canonical form.
  returned: always
  type: dict
  contains:
    frontend_networks:
      description:
        - The networks, sorted by id, in the shape of O(frontend_networks), ranges merged and written V(first-last).
        - Each network also carries C(tenant), its tenant's name, beside C(tenant_id). C(tenant) is V(null) for a
          network no tenant holds, for a tenant id the list lacks, and for a network of the backend bond, which takes no
          tenant, whatever C(tenant_id) shows. It is V(null) on every network in a run that read no tenant list, which
          is a run that changes nothing and whose entries set neither O(frontend_networks[].tenant) nor
          O(frontend_networks[].tenant_id). In check mode the name of a tenant that an earlier task creates is shown,
          with a V(null) C(tenant_id).
        - C(tenant_id) is deprecated with O(frontend_networks[].tenant_id); read C(tenant).
      type: list
      elements: dict
    managed_interfaces:
      description: The interfaces, VLANs sorted by VLAN id and keys whose value is V(null) left out, or V(null).
      type: dict
  sample:
    frontend_networks: [{id: 1, name: Default, type: HOST, tenant: Default, tenant_id: 1, netmask: 255.255.255.0,
                         default_gateway: null, ip_ranges: [], floating_ip_ranges: [10.20.0.50-10.20.0.51]}]
    managed_interfaces: null
api_host:
  description: The address later tasks must connect to, after a change that dropped O(api_host).
  returned: when O(api_host_after) is set and the document differed, outside check mode, and not on a failure
  type: str
  sample: 10.20.0.6
notes:
  description:
    - What the module noticed, such as a write it confirmed by reading back after the connection was lost, or, in
      check mode, a tenant that does not exist yet and that an earlier task is expected to create. Such a tenant has one
      note, however many networks name it.
    - Unless O(allow_management_disruption) is set, the note on such a tenant also says that the check run does not
      judge its REST switches and that the real run does. It does not when O(planned_tenants) names the tenant.
  returned: when there is something to note
  type: list
  elements: str
expected:
  description:
    - The document the write should have left, in canonical form with C(tenant_id) and no C(tenant).
  returned: when the stored document did not converge on the written one, including after a lost connection
  type: dict
  sample:
    frontend_networks: [{id: 1, name: Default, type: HOST, tenant_id: 1, netmask: 255.255.255.0, default_gateway: null,
                         ip_ranges: [], floating_ip_ranges: [10.20.0.50-10.20.0.53]}]
    managed_interfaces: null
observed:
  description:
    - The document the cluster holds instead, in canonical form with C(tenant_id) and no C(tenant).
  returned: when the stored document did not converge on the written one, including after a lost connection
  type: dict
  sample:
    frontend_networks: [{id: 1, name: Default, type: HOST, tenant_id: 1, netmask: 255.255.255.0, default_gateway: null,
                         ip_ranges: [], floating_ip_ranges: [10.20.0.50-10.20.0.51]}]
    managed_interfaces: null
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import NETWORK_OPTIONS, NetworkConfig

ARGUMENT_SPEC = {
    "frontend_networks": {"type": "list", "elements": "dict", "options": NETWORK_OPTIONS},
    "managed_interfaces": {"type": "dict"},
    "owned_parts": {"type": "list", "elements": "str", "choices": ["interfaces", "networks", "floating_ips"]},
    "managed_by": {"type": "str", "choices": ["none", "terraform_provider", "legacy_provisioner"], "default": "none"},
    "take_over_from_terraform": {"type": "bool", "default": False},
    "validate": {"type": "bool", "default": True},
    "allow_management_disruption": {"type": "bool", "default": False},
    "api_host_after": {"type": "str"},
    "management_network": {"type": "list", "elements": "int"},
    "cluster_port": {"type": "int"},
    "planned_tenants": {"type": "dict"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), NetworkConfig)


if __name__ == "__main__":
    main()
