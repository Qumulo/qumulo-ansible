#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: dns_lookup_overrides
short_description: Manage the names a Qumulo cluster resolves without asking DNS
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster's lookup overrides, its equivalent of C(/etc/hosts), which map an address to names before DNS is
    asked.
  - O(overrides) is the whole table. An address the cluster has and the list leaves out is removed, and V([]) clears
    the table. A change takes effect at once, with no need for M(qumulo.cluster.dns_cache_clear).
  - Entries are compared by address, in any order, and addresses in their canonical form, so V(2001:DB8::0005) and
    V(2001:db8::5) are the same. The order of an entry's names is kept and compared.
  - The cluster accepts a table that lists one address in several entries. The module reads such a table as one entry
    per address, its names in table order, so a task that declares that entry changes nothing.
options:
  overrides:
    description:
      - The table, one entry per address.
      - An address listed twice, an address that does not parse, or a name the cluster would refuse fails before any
        request is sent.
    type: list
    elements: dict
    required: true
    suboptions:
      ip_address:
        description: The address, IPv4 or IPv6. An IPv4-mapped IPv6 address is its IPv4 address.
        type: str
        required: true
      aliases:
        description:
          - The names that resolve to O(overrides[].ip_address), the first being the name its reverse lookup returns.
          - Each takes the letters a-z and A-Z, digits, V(-) and V(.), starts with a letter, ends with a letter or
            digit, and is at least two characters long.
        type: list
        elements: str
        required: true
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
  - "API: GET and PUT /v1/dns/lookup-override-config, with If-Match. The table shares its revision with the DNS
    settings of M(qumulo.cluster.dns_settings), so a write of either between this module's read and its write makes the
    cluster refuse the write with 412. The module then reads again and, if the table still differs, PUTs the whole
    table again, so the last writer of the table wins."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# The cluster finds a domain controller or a replication peer that the site's DNS does not know.
- name: Resolve the domain controllers and the replication peer without DNS
  qumulo.cluster.dns_lookup_overrides:
    overrides:
      - {ip_address: 10.0.0.10, aliases: [dc1.corp.example.com, dc1]}
      - {ip_address: 10.0.0.11, aliases: [dc2.corp.example.com, dc2]}
      - {ip_address: "2001:db8::20", aliases: [dr01.corp.example.com]}

# overrides is the whole table, so the tasks below list every entry; only dc1's names are swapped.
- name: Preview putting the short name first, so that a reverse lookup returns it
  qumulo.cluster.dns_lookup_overrides:
    overrides:
      - {ip_address: 10.0.0.10, aliases: [dc1, dc1.corp.example.com]}
      - {ip_address: 10.0.0.11, aliases: [dc2.corp.example.com, dc2]}
      - {ip_address: "2001:db8::20", aliases: [dr01.corp.example.com]}
  check_mode: true
  diff: true
  register: overrides_preview

- name: Show the table the change would leave
  ansible.builtin.debug:
    var: overrides_preview.dns_lookup_overrides.overrides

- name: Put the short name first, so that a reverse lookup returns it
  qumulo.cluster.dns_lookup_overrides:
    overrides:
      - {ip_address: 10.0.0.10, aliases: [dc1, dc1.corp.example.com]}
      - {ip_address: 10.0.0.11, aliases: [dc2.corp.example.com, dc2]}
      - {ip_address: "2001:db8::20", aliases: [dr01.corp.example.com]}

# overrides is the whole table, so read the cluster's table and extend it. An address the table already has fails the
# task, since an address takes one entry; replace that entry instead.
- name: Read the cluster's lookup overrides
  qumulo.cluster.network_info:
    gather_subset: [dns_lookup_overrides]
  register: current

- name: Add a third domain controller, keeping the entries the cluster has
  qumulo.cluster.dns_lookup_overrides:
    overrides: "{{ current.network.dns_lookup_overrides.overrides + [{'ip_address': '10.0.0.12', 'aliases': ['dc3.corp.example.com', 'dc3']}] }}"

# Each change to the table needs a fresh read, or it writes back the entries as they were before the earlier change.
- name: Read the cluster's lookup overrides again
  qumulo.cluster.network_info:
    gather_subset: [dns_lookup_overrides]
  register: before_removal

- name: Remove the entry for 10.0.0.11, keeping the others
  qumulo.cluster.dns_lookup_overrides:
    overrides: "{{ before_removal.network.dns_lookup_overrides.overrides | rejectattr('ip_address', 'equalto', '10.0.0.11') | list }}"

- name: Clear the table
  qumulo.cluster.dns_lookup_overrides:
    overrides: []
"""

RETURN = r"""
dns_lookup_overrides:
  description: The table after the task, or in check mode the table the task would leave.
  returned: always
  type: dict
  contains:
    overrides:
      description: The entries sorted by address, each address in canonical form and its names in order.
      type: list
      elements: dict
  sample: {overrides: [{ip_address: 10.0.0.10, aliases: [dc1.corp.example.com, dc1]}]}
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
  sample: {overrides: [{ip_address: 10.0.0.10, aliases: [dc1, dc1.corp.example.com]}]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {overrides: [{ip_address: 10.0.0.10, aliases: [dc1.corp.example.com, dc1]}]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import DnsLookupOverrides

ARGUMENT_SPEC = {
    "overrides": {
        "type": "list",
        "elements": "dict",
        "required": True,
        "options": {
            "ip_address": {"type": "str", "required": True},
            "aliases": {"type": "list", "elements": "str", "required": True},
        },
    },
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), DnsLookupOverrides)


if __name__ == "__main__":
    main()
