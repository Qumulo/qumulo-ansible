#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: dns_cache_clear
short_description: Clear a Qumulo cluster's DNS cache
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Clears the cache of answers the cluster's resolver has kept, so that its next lookup asks the DNS servers again.
  - Nothing can be read back to tell whether the cache holds anything, so the module always reports a change, and in
    check mode reports the change it would make without sending it (G-ACTIONS).
  - The cluster never caches the answers of its lookup overrides, so a change of M(qumulo.cluster.dns_lookup_overrides)
    needs no clearing.
options:
  caches:
    description:
      - The caches to clear. V(forward) holds name-to-address answers, and V(reverse) address-to-name answers.
      - An empty list fails before any request is sent.
    type: list
    elements: str
    choices: [forward, reverse]
    default: [forward, reverse]
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
    support: none
    details:
      - Every run clears the caches again and reports a change.
notes:
  - "API: POST /v1/dns/clear-dns-cache. A clear interrupted by a lost connection is sent again."
  - "Registers: G-ACTIONS."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Clear both caches after the site's DNS moved a domain controller
  qumulo.cluster.dns_cache_clear:

- name: Clear only the reverse cache after a PTR record changed
  qumulo.cluster.dns_cache_clear:
    caches: [reverse]

- name: Preview clearing the reverse cache
  qumulo.cluster.dns_cache_clear:
    caches: [reverse]
  check_mode: true
  diff: true
  register: clear_preview

- name: Show the caches a real run would clear
  ansible.builtin.debug:
    var: clear_preview.dns_cache_clear.caches

# The module reports a change on every run, so clear the cache only when something calls for it: when a task
# changed the DNS servers (below), or with a when: condition. A change of the lookup overrides needs no clearing.
- name: Point the cluster at new DNS servers, and have a handler clear what the old ones answered
  qumulo.cluster.dns_settings:
    servers: [10.0.0.4, 10.0.0.5]
  notify: Clear the cluster's DNS cache

# In the play's handlers:
- name: Clear the cluster's DNS cache
  qumulo.cluster.dns_cache_clear:

# The same without a handler: a task conditioned on the change.
- name: Point the cluster at new DNS servers, keeping the result for the next task
  qumulo.cluster.dns_settings:
    servers: [10.0.0.4, 10.0.0.5]
  register: new_servers

- name: Clear what the old servers answered
  qumulo.cluster.dns_cache_clear:
  when: new_servers is changed
"""

RETURN = r"""
dns_cache_clear:
  description: What the task cleared, or in check mode what it would clear.
  returned: always
  type: dict
  contains:
    caches:
      description: The caches, sorted.
      type: list
      elements: str
  sample: {caches: [forward, reverse]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import DnsCacheClear

ARGUMENT_SPEC = {
    "caches": {"type": "list", "elements": "str", "choices": ["forward", "reverse"], "default": ["forward", "reverse"]},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), DnsCacheClear)


if __name__ == "__main__":
    main()
