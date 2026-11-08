#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: auth_cache_clear
short_description: Clear a Qumulo cluster's cache of identities and group memberships
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Clears what the cluster has cached of identities, their mappings and their group memberships, from Active
    Directory, LDAP and its own configuration, so that the next lookup asks again.
  - Clearing is an action, not a state, so the module reports a change on every run (G-ACTIONS). In check mode it
    reports the change and clears nothing.
  - Lookups are slower until the cache fills again.
options: {}
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details: Reports the change and clears nothing.
  diff_mode:
    support: full
  idempotent:
    support: none
    details: Every run clears the cache again.
notes:
  - "API: POST /v1/auth/clear-cache."
  - "Registers: G-ACTIONS."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# A changed mapping or home directory applies to an identity the cluster has already cached only once the cache is
# cleared. Clear it from a handler, so that it runs once at the end of the play and only after a change.
- name: Set the user-defined mappings, and clear the cache when they changed
  qumulo.cluster.user_defined_mappings:
    mappings:
      - {down_level_logon_name: 'CORP\alice', ldap_name: alice}
      - {down_level_logon_name: 'CORP\bob', ldap_name: robert}
  notify: Clear the authentication cache

# In the play's handlers:
- name: Clear the authentication cache
  qumulo.cluster.auth_cache_clear:

# The same without a handler, as the cluster_baseline role does: a task conditioned on the change.
- name: Give an AD user a home directory
  qumulo.cluster.identity_attributes:
    trustee: 'ad:CORP\alice'
    home_directory: /home/alice
  register: home_set

- name: Clear the authentication cache after the home directory changed
  qumulo.cluster.auth_cache_clear:
  when: home_set is changed

- name: Clear the authentication cache now
  qumulo.cluster.auth_cache_clear:

- name: Show that a run would clear the cache, and clear nothing
  qumulo.cluster.auth_cache_clear:
  check_mode: true
  diff: true
  register: clear_preview
"""

RETURN = r"""
plan:
  description: What the module does, or in check mode would do.
  returned: always
  type: dict
  contains:
    cache:
      description: The cache cleared, always V(authentication).
      type: str
  sample: {cache: authentication}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import AuthCacheClear
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), AuthCacheClear)


if __name__ == "__main__":
    main()
