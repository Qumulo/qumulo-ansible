#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: dns_settings
short_description: Manage the DNS servers and search domains a Qumulo cluster resolves names with
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster's system DNS configuration, the equivalent of its C(resolv.conf).
  - An option left unset keeps the cluster's current value. An empty list clears it.
  - Both lists are compared in order.
  - A change of the servers does not clear the cluster's DNS cache; see M(qumulo.cluster.dns_cache_clear).
options:
  servers:
    description:
      - The DNS servers, as IP addresses, in the order the cluster asks them.
      - A value that is not an IP address fails before any request is sent; the server accepts only IP addresses.
      - Addresses are compared in their canonical form, so V(2001:DB8::0001) and V(2001:db8::1) are the same server.
    type: list
    elements: str
  search_domains:
    description:
      - The domains appended to a name that is not fully qualified, in order.
    type: list
    elements: str
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET and PATCH /v1/dns/configs/system, with If-Match."
  - The cluster stores each server address as it was sent, so its own text may differ from the canonical form the module
    compares and returns (QUIRK-014).
  - On a cluster whose network is managed by its host, such as a container or a host-managed cloud cluster, the
    setting is stored but the host may resolve names its own way.
  - "Registers: QUIRK-014."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Resolve names through the site's DNS servers
  qumulo.cluster.dns_settings:
    servers: [10.0.0.2, 10.0.0.3]
    search_domains: [corp.example.com]

- name: Preview putting a new server first
  qumulo.cluster.dns_settings:
    servers: [10.0.0.4, 10.0.0.2, 10.0.0.3]
  check_mode: true
  diff: true
  register: dns_preview

- name: Show the servers the change would leave, in the order the cluster would ask them
  ansible.builtin.debug:
    var: dns_preview.dns_settings.servers

# A change of the servers leaves the old servers' answers in the cluster's cache; see qumulo.cluster.dns_cache_clear.
- name: Put a new server first, keeping the search domains
  qumulo.cluster.dns_settings:
    servers: [10.0.0.4, 10.0.0.2, 10.0.0.3]

# servers is the whole list, so read the cluster's list and extend it.
- name: Read the cluster's DNS settings
  qumulo.cluster.network_info:
    gather_subset: [dns]
  register: current

- name: Add a DNS server at the end, keeping the servers the cluster has
  qumulo.cluster.dns_settings:
    servers: "{{ current.network.dns.servers + ['10.0.0.6'] }}"

- name: Clear the search domains, keeping the servers
  qumulo.cluster.dns_settings:
    search_domains: []

- name: Remove every DNS server, keeping the search domains
  qumulo.cluster.dns_settings:
    servers: []
"""

RETURN = r"""
dns_settings:
  description: The settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    servers:
      description: The DNS servers, in canonical form, in order.
      type: list
      elements: str
    search_domains:
      description: The search domains, in order.
      type: list
      elements: str
  sample: {servers: [10.0.0.2, 10.0.0.3], search_domains: [corp.example.com]}
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the settings back did not confirm
    it. The task fails with C(changed) true.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the options that did not converge.
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {servers: [10.0.0.4, 10.0.0.2]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {servers: [10.0.0.2]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import DnsSettings

ARGUMENT_SPEC = {
    "servers": {"type": "list", "elements": "str"},
    "search_domains": {"type": "list", "elements": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), DnsSettings)


if __name__ == "__main__":
    main()
