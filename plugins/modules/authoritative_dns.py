#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: authoritative_dns
short_description: Manage the DNS server a Qumulo cluster runs for its own name
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Turns on or off the cluster's authoritative DNS server, which answers queries for the cluster's name with its
    floating addresses, and sets that name, the clients that may query it, and how long its answers may be cached.
  - Each answer gives one node's floating address; a cluster without floating addresses has no address to answer with.
    Clients reach the server only once the site's DNS delegates the name to the cluster's floating addresses, which
    this module does not set up.
  - An option left unset keeps the cluster's current value.
  - When O(terraform_owned_areas) lists V(dns.fqdn), O(enabled) and O(fqdn) fail before any request is sent, unless
    O(take_over_from_terraform=true). O(host_restrictions) and O(record_ttl_seconds) are not covered by V(dns.fqdn).
options:
  enabled:
    description:
      - Whether the cluster answers DNS queries for O(fqdn).
      - V(true) fails before any write when the cluster would be left without a name.
    type: bool
  fqdn:
    description:
      - The cluster's fully qualified name, such as V(edge01.corp.example.com). The cluster stores it with a final
        dot, which the module adds when it is missing.
      - Compared as written, apart from the final dot; the cluster keeps the case it is sent.
      - Each label between dots takes 1 to 63 of the characters a-z, A-Z, 0-9 and V(-), and neither starts nor ends
        with V(-); the whole name takes at most 253 characters. Any other name fails before any request is sent.
      - Once a name is set, the cluster refuses to clear it, so V("") fails before the write on a cluster that has a
        name, and the message names that name. On a cluster that has never had a name, V("") changes nothing
        (G-DNS-FQDN-CLEAR).
    type: str
  host_restrictions:
    description:
      - The clients that may query the server, as single addresses, V(first-last) ranges or CIDR blocks. V([]) lets
        every client query it.
      - Compared as sets of addresses, so V(10.0.0.0/30), V(10.0.0.0-10.0.0.3) and V(10.0.0.0-3) are the same.
      - The cluster stores an IPv4-mapped address such as V(::ffff:192.0.2.7) as its IPv4 address, and so does the
        module; an IPv4-mapped range or block fails before any request is sent, and is written as IPv4 instead.
      - The cluster takes and returns a list, although its API documentation types the field as a string (QUIRK-023).
    type: list
    elements: str
  record_ttl_seconds:
    description:
      - How long, in seconds, a resolver may cache an answer, from 0 to 3600. Any other value fails before any
        request is sent.
      - V(0) is the cluster's own default; a resolver then asks again each time.
    type: int
  terraform_owned_areas:
    description:
      - The areas Terraform owns on this cluster, as the inventory's C(qumulo_terraform_owned_areas) lists them.
        Only V(dns.fqdn) matters here; it covers O(enabled) and O(fqdn).
    type: list
    elements: str
    default: []
  take_over_from_terraform:
    description: Allow O(enabled) and O(fqdn) although O(terraform_owned_areas) lists V(dns.fqdn).
    type: bool
    default: false
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
  - "API: GET and PATCH /v1/authoritative-dns/settings, without If-Match (QUIRK-024); the last writer wins. The read
    answers HTTP 503 while the node is out of quorum, and is retried within O(retry_timeout)."
  - "Registers: G-DNS-FQDN-CLEAR, QUIRK-023, QUIRK-024."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Answer for the edge's name on its floating addresses, to the site's networks only
  qumulo.cluster.authoritative_dns:
    enabled: true
    fqdn: edge01.corp.example.com
    host_restrictions: [10.0.0.0/8, 192.168.10.0/24]

- name: Preview letting resolvers cache answers for a minute
  qumulo.cluster.authoritative_dns:
    record_ttl_seconds: 60
  check_mode: true
  diff: true
  register: dns_preview

- name: Show the settings the change would leave
  ansible.builtin.debug:
    var: dns_preview.authoritative_dns

- name: Let resolvers cache answers for a minute
  qumulo.cluster.authoritative_dns:
    record_ttl_seconds: 60

- name: Let any client query the server
  qumulo.cluster.authoritative_dns:
    host_restrictions: []

# On a cluster the Terraform provider formed, with qumulo_terraform_owned_areas: [network.floating_ips, dns.fqdn],
# the provider keeps the name and whether the server is on; the collection still sets who may ask.
- name: Restrict the clients, leaving the name to Terraform
  qumulo.cluster.authoritative_dns:
    host_restrictions: [10.0.0.0/8]
    terraform_owned_areas: [network.floating_ips, dns.fqdn]

# Terraform applies its own name again on its next run.
- name: Set the name here although Terraform owns dns.fqdn
  qumulo.cluster.authoritative_dns:
    fqdn: edge01.corp.example.com
    terraform_owned_areas: [network.floating_ips, dns.fqdn]
    take_over_from_terraform: true

# Leave fqdn out to keep the name the cluster has. Setting fqdn to "" fails on a cluster that has a name.
- name: Stop answering, keeping the name
  qumulo.cluster.authoritative_dns:
    enabled: false
"""

RETURN = r"""
authoritative_dns:
  description: The settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    enabled:
      description: Whether the cluster answers for its name.
      type: bool
    fqdn:
      description: The cluster's name with its final dot, or V("") on a cluster that has never had one.
      type: str
    host_restrictions:
      description: The clients that may query, merged and sorted, each written V(first-last) or as a single address.
      type: list
      elements: str
    record_ttl_seconds:
      description: How long a resolver may cache an answer, in seconds.
      type: int
  sample: {enabled: true, fqdn: edge01.corp.example.com., host_restrictions: [10.0.0.0-10.255.255.255], record_ttl_seconds: 0}
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
  sample: {record_ttl_seconds: 60}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {record_ttl_seconds: 0}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import AuthoritativeDns

ARGUMENT_SPEC = {
    "enabled": {"type": "bool"},
    "fqdn": {"type": "str"},
    "host_restrictions": {"type": "list", "elements": "str"},
    "record_ttl_seconds": {"type": "int"},
    "terraform_owned_areas": {"type": "list", "elements": "str", "default": []},
    "take_over_from_terraform": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), AuthoritativeDns)


if __name__ == "__main__":
    main()
