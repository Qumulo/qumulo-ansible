#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: network_info
short_description: Read a Qumulo cluster's network and name resolution settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the cluster's network document, its frontend interfaces and their networks, the older single-network
    settings, the DNS settings, the lookup overrides, the authoritative DNS settings, the network status and the
    network that carries O(api_host), and changes nothing.
  - The V(document), V(dns), V(dns_lookup_overrides) and V(authoritative_dns) subsets have the option shape of
    M(qumulo.cluster.network_config), M(qumulo.cluster.dns_settings), M(qumulo.cluster.dns_lookup_overrides) and
    M(qumulo.cluster.authoritative_dns), so they can be fed back to those modules.
  - Each network of the V(document) subset carries C(tenant), the name of its tenant, beside C(tenant_id). C(tenant) is
    V(null) for a network no tenant holds, on a cluster that does not offer the tenant list, and for a user without
    PRIVILEGE_TENANT_READ. It is also V(null) for a network of the backend bond, which takes no tenant, though
    C(tenant_id) there shows the tenant the cluster put the network in. So the subset can be given back to
    M(qumulo.cluster.network_config), which drops that C(tenant_id) with a warning.
  - The V(status) subset is the cluster's answer to C(GET /v3/network/status) as the cluster gives it, a list with one
    entry per node. RV(network.status) describes its shape and what 7.11.0 showed.
  - The V(api_host_network) subset is the id, in the network document, of the network that carries O(api_host), which
    the status shows on a frontend device. It is unavailable when O(api_host) is a name, or when the status and the
    network document name no one network for its address, as when the play reaches the cluster through NAT or a load
    balancer, when the cluster has no frontend network yet, or when the document has two networks of the type the
    status shows the address as.
  - With O(resolve_names) or O(resolve_addresses), it also asks the cluster's own resolver to look those up and shows
    what the cluster sees. A lookup runs whatever O(gather_subset) lists, and in check mode too, since it changes
    nothing; with O(gather_subset=[]) the module gathers only the lookups.
  - On a node that is not yet part of a cluster (state C(UNCONFIGURED)) no login is possible, so every subset and
    lookup is listed in RV(unavailable_subsets), and the module warns.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V([]) gathers every subset too, unless O(resolve_names) or O(resolve_addresses) is given; then only the
        lookups run.
    type: list
    elements: str
    choices: [all, document, interfaces, legacy_settings, dns, dns_lookup_overrides, authoritative_dns, status, api_host_network]
    default: [all]
  resolve_names:
    description: Names for the cluster's resolver to look up, each answered with its addresses in RV(network.resolved_names).
    type: list
    elements: str
    default: []
  resolve_addresses:
    description: Addresses for the cluster's resolver to look up, each answered with its name in RV(network.resolved_addresses).
    type: list
    elements: str
    default: []
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(document) GET /v3/network and GET /v1/multitenancy/tenants/ for the tenants' names; V(interfaces) GET
    /v2/network/interfaces/ and, for each interface, /v2/network/interfaces/{id}/networks/; V(legacy_settings) GET
    /v1/network/settings; V(dns) GET /v1/dns/configs/system; V(dns_lookup_overrides) GET /v1/dns/lookup-override-config;
    V(authoritative_dns) GET /v1/authoritative-dns/settings; V(status) GET /v3/network/status; V(api_host_network) GET
    /v3/network/status and GET /v3/network, with the status and the document each read once for the subsets that need
    them; O(resolve_names) POST /v1/dns/resolve-names-to-ips; O(resolve_addresses) POST /v1/dns/resolve-ips-to-names."
  - The tenant list that V(document) reads needs PRIVILEGE_TENANT_READ. A user without it, and a cluster that does not
    offer the list, get V(null) for C(tenant) in each network, and the subset is still gathered; the module warns of
    the missing privilege. Any other error reading the list fails the module.
  - The C(tenant) of the V(document) subset resolves through the multitenancy REST API, which Qumulo marks as preview; a
    later Qumulo Core release may change it.
  - The single-network settings of V(legacy_settings) are deprecated, and the cluster refuses them (HTTP 501) on
    networking its host manages, with more than one network, or with a VLAN; the subset is then unavailable. Any
    other error fails the module.
  - An HTTP 500 from C(GET /v3/network/status), which the Qumulo Core container answers (G-NET-APPLY), and the 404,
    405, 406 or 501 of a cluster that does not offer the status leave V(status) and V(api_host_network) listed in
    RV(unavailable_subsets), with the status's reason for both from one read, unless O(api_host) is a name. The other
    subsets are still gathered. Any other error fails the module.
  - The DNS servers and search domains that V(interfaces) shows on each network are the cluster's one DNS setting,
    the same on every network; V(dns) reads that one setting directly.
  - A cluster whose network its host manages, such as a container or a host-managed cloud cluster, lists no
    interfaces.
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task names another cluster. Set them once for the play
# with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read every subset
  qumulo.cluster.network_info:
  register: network

- name: Show the frontend networks
  ansible.builtin.debug:
    var: network.network.document.frontend_networks

# Each network carries the name of its tenant in tenant, beside the deprecated tenant_id. tenant is null for a network
# no tenant holds, on a cluster that does not offer the tenant list, and for a user without PRIVILEGE_TENANT_READ. It is
# also null for a network of the backend bond, which takes no tenant.
- name: Show the tenant that holds each frontend network
  ansible.builtin.debug:
    msg: "{{ item.name }}: {{ item.tenant }}"
  loop: "{{ network.network.document.frontend_networks }}"
  loop_control:
    label: "{{ item.id }}"

- name: Read only the DNS settings and the lookup overrides
  qumulo.cluster.network_info:
    gather_subset: [dns, dns_lookup_overrides]
  register: names

# The subsets document, dns, dns_lookup_overrides and authoritative_dns have the option shape of the modules that
# manage them. Both clusters must accept the credentials the play sets, since an api_token is bound to one cluster.
- name: Copy cluster01's lookup overrides to this cluster
  block:
    - name: Read cluster01's lookup overrides
      qumulo.cluster.network_info:
        api_host: cluster01.example.com
        gather_subset: [dns_lookup_overrides]
      register: source_network

    - name: Apply them to this cluster
      qumulo.cluster.dns_lookup_overrides:
        overrides: "{{ source_network.network.dns_lookup_overrides.overrides }}"

# When the status answers HTTP 500 (G-NET-APPLY), the subset is listed in unavailable_subsets with the reason, and the
# loop below has nothing to show.
- name: Read the network status
  qumulo.cluster.network_info:
    gather_subset: [status]
  register: network_status

- name: Show whether each interface has been applied, node by node
  ansible.builtin.debug:
    msg: "{{ item.node_name }}: {{ item.managed_interface_statuses | default([]) }}"
  loop: "{{ network_status.network.status | default([]) }}"
  loop_control:
    label: "{{ item.node_name }}"

# Pass the id, in a list, as management_network to the tenant and network_config modules.
# Behind NAT or a load balancer no network carries the address the play connects to, so the subset is unavailable
# and says so; give management_network the network that api_host is forwarded to instead.
- name: Find the network that carries the address this play connects to
  qumulo.cluster.network_info:
    gather_subset: [api_host_network]
  register: reached

- name: Show the network id
  ansible.builtin.debug:
    var: reached.network.api_host_network
  when: reached.network.api_host_network is defined

- name: Show why no network was found
  ansible.builtin.debug:
    var: reached.unavailable_subsets.api_host_network
  when: reached.unavailable_subsets.api_host_network is defined

# Before a cluster joins a domain, this shows whether it can find the domain controller.
- name: Check that the cluster resolves its domain controller, gathering nothing else
  qumulo.cluster.network_info:
    gather_subset: []
    resolve_names: [dc1.corp.example.com]
  register: lookup

- name: Stop unless the name resolved
  ansible.builtin.assert:
    that: lookup.network.resolved_names[0].result == 'OK'

- name: Look up the name the cluster gives the domain controller's address
  qumulo.cluster.network_info:
    gather_subset: []
    resolve_addresses: [10.0.0.10]
  register: reverse

- name: Show the name the address resolved to, or why it did not
  ansible.builtin.debug:
    msg: "{{ r.hostname if r.result == 'OK' else r.result }}"
  vars:
    r: "{{ reverse.network.resolved_addresses[0] }}"

- name: Read the single-network settings, which a host-managed or multi-network cluster refuses
  qumulo.cluster.network_info:
    gather_subset: [legacy_settings]
  register: legacy

- name: Show the reason, if any
  ansible.builtin.debug:
    var: legacy.unavailable_subsets | default({})
"""

RETURN = r"""
network:
  description: The gathered facts. Each key is present only when its subset was gathered or its lookup asked for.
  returned: always
  type: dict
  contains:
    document:
      description:
        - The network document, as M(qumulo.cluster.network_config) takes and returns it.
        - Each network carries C(tenant), the name of its tenant, beside C(tenant_id), which is deprecated; read
          C(tenant). C(tenant) is V(null) for a network no tenant holds, on a cluster that does not offer the tenant
          list, and for a user without PRIVILEGE_TENANT_READ. It is also V(null) for a network of the backend bond,
          which takes no tenant, though C(tenant_id) there shows the tenant the cluster put the network in.
      type: dict
      sample: {frontend_networks: [{id: 1, name: Default, type: STATIC, tenant: Default, tenant_id: 1, netmask: 255.255.255.0,
               default_gateway: 10.20.0.1, ip_ranges: [10.20.0.5-10.20.0.8], floating_ip_ranges: [10.20.0.50-10.20.0.53]}],
               managed_interfaces: {frontend_bond_config: {interface_name: bond0, networks: [1]}, frontend_vlans: []}}
    interfaces:
      description: Each frontend interface as the cluster returns it, with its C(networks).
      type: list
      elements: dict
      sample: [{id: 1, name: bond0, bonding_mode: ACTIVE_BACKUP, mtu: 1500, networks: [{id: 1, name: Default, assigned_by: STATIC}]}]
    legacy_settings:
      description: The single-network settings, as the cluster returns them.
      type: dict
      sample: {assigned_by: STATIC, ip_ranges: [10.20.0.5-6], floating_ip_ranges: [], netmask: 255.255.255.0, gateway: 10.20.0.1}
    dns:
      description: The DNS settings, as M(qumulo.cluster.dns_settings) takes them.
      type: dict
      sample: {servers: [10.0.0.2], search_domains: [corp.example.com]}
    dns_lookup_overrides:
      description: The lookup override table, as M(qumulo.cluster.dns_lookup_overrides) takes it.
      type: dict
      sample: {overrides: [{ip_address: 10.0.0.10, aliases: [dc1.corp.example.com, dc1]}]}
    authoritative_dns:
      description: The authoritative DNS settings, as M(qumulo.cluster.authoritative_dns) returns them.
      type: dict
      sample: {enabled: true, fqdn: edge01.corp.example.com., host_restrictions: [10.0.0.0-10.255.255.255], record_ttl_seconds: 0}
    status:
      description:
        - The cluster's answer to C(GET /v3/network/status), as the cluster gives it, with one entry per node.
        - On 7.11.0 each entry's C(devices) carry, under C(network_details), the device's role in C(use_for), seen as
          V(FRONTEND_AND_BACKEND) and, on the loopback, V(NONE); the C(qumulo_ip_addresses) of the networks the device
          carries, each with its C(network_id), C(assigned_by), C(address), C(floating_addresses), C(netmask) and
          C(gateway); and the C(host_routable_ip_addresses) the node's host assigns. A device of use V(NONE) lists its
          C(addresses). On 7.11.0 hardware an entry's C(network_id) did not name the network in the network document; network 1 of the
          document showed as V(0).
        - On the VM edge on 7.11.0 its one device lists the virtual machine's addresses under
          C(host_routable_ip_addresses), and the loopback is a device of C(use_for) V(NONE) with its C(addresses). With
          no frontend network the device lists no C(qumulo_ip_addresses); with a HOST network declared it lists one entry
          assigned by V(HOST), with an empty C(address); for HOST network 0 its C(network_id) was V(0).
        - Each entry's C(managed_interface_statuses) lists each interface the cluster manages, with its C(name),
          C(status) and C(details). The VM edge, whose host manages its network, listed none. On 7.11.0 hardware
          C(network_statuses) was empty with a network applied; on the VM edge it mapped the declared HOST network's id to
          its C(name), C(status) and C(details).
      type: list
      elements: dict
      sample:
        - node_id: 1
          node_name: node1
          network_statuses: {}
          managed_interface_statuses:
            - {name: bond0, status: APPLIED, details: ""}
          devices:
            - name: bond0
              network_details:
                use_for: FRONTEND_AND_BACKEND
                qumulo_ip_addresses:
                  - network_id: 0
                    assigned_by: STATIC
                    netmask: 255.255.255.0
                    address: 10.20.0.5
                    floating_addresses: [10.20.0.50]
                    gateway: null
                host_routable_ip_addresses: []
    api_host_network:
      description:
        - The id, in the network document, of the network that carries O(api_host), which the status shows on a frontend
          device. Network id 0 is a real id.
        - Absent, and listed in RV(unavailable_subsets), when O(api_host) is a name, when the status and the network
          document name no one network for its address, and when the status is unavailable.
      type: int
      sample: 1
    resolved_names:
      description: Each name of O(resolve_names) with the addresses it resolved to and the C(result), V(OK), V(NOT_FOUND), V(TIMEOUT) or V(ERROR).
      type: list
      elements: dict
      sample: [{hostname: dc1.corp.example.com, ip_addresses: [10.0.0.10], result: OK}]
    resolved_addresses:
      description: Each address of O(resolve_addresses) with the name it resolved to and the C(result).
      type: list
      elements: dict
      sample: [{ip_address: 10.0.0.10, hostname: dc1.corp.example.com, result: OK}]
unavailable_subsets:
  description: Each requested subset this cluster could not answer, with the reason.
  returned: when a subset was unavailable
  type: dict
  sample:
    legacy_settings: "GET /v1/network/settings failed: HTTP 501 ..."
    status: "GET /v3/network/status failed: HTTP 500 ..."
    api_host_network: "GET /v3/network/status failed: HTTP 500 ..."
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.network import NETWORK_INFO_SUBSETS, network_lookups

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *NETWORK_INFO_SUBSETS], "default": ["all"]},
    "resolve_names": {"type": "list", "elements": "str", "default": []},
    "resolve_addresses": {"type": "list", "elements": "str", "default": []},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC)
    lookups = network_lookups(module.params["resolve_names"], module.params["resolve_addresses"])
    # A lookup asked for runs whatever gather_subset lists, so its name joins the subsets to gather.
    module.params["gather_subset"] = [*module.params["gather_subset"], *lookups]
    run_info(module, "network", {**NETWORK_INFO_SUBSETS, **lookups})


if __name__ == "__main__":
    main()
