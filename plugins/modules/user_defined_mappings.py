#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: user_defined_mappings
short_description: Manage which Active Directory users a Qumulo cluster maps to which LDAP users
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster's whole list of user-defined mappings, each mapping an Active Directory user, by its down-level
    logon name, to the LDAP user it is the same person as. The cluster then treats the two as one identity.
  - O(mappings) is the whole list. A mapping the cluster has and the list leaves out is removed, and V([]) removes
    every one.
  - The list is compared without regard to order, and sent in the order given.
  - The cluster accepts a mapping only when Active Directory is joined and POSIX attributes from AD are off. Outside a
    domain the module refuses a list that is not empty before any write (QUIRK-031); in check mode it notes that an
    earlier task is expected to join the domain. While the cluster reads POSIX ids from AD, the module refuses a list
    that is not empty before any write, in check mode too.
  - A changed mapping reaches a user the cluster has already cached only once the cache is cleared or expires.
options:
  mappings:
    description:
      - Every mapping the cluster should hold.
      - Each AD name may appear once, in any case. V(DOMAIN\\*) maps every user of the domain to the LDAP user of the same name; it
        must then be the only mapping, with O(mappings[].ldap_name=*). The module checks both before any request.
    type: list
    elements: dict
    required: true
    suboptions:
      down_level_logon_name:
        description: The Active Directory user as V(DOMAIN\\name), with the domain's NetBIOS name.
        type: str
        required: true
      ldap_name:
        description: The LDAP user's name.
        type: str
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
  - "API: GET and PUT /v1/auth/user-defined-mappings/, with If-Match; GET /v1/ad/status and /v1/ad/monitor before a
    list that is not empty is written."
  - The cluster checks that each domain's NetBIOS name resolves through the joined domain, and refuses the list
    otherwise.
  - "Registers: QUIRK-031."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# The cluster accepts mappings only after it has joined the domain with qumulo.cluster.ad_domain, with
# use_ad_posix_attributes: false.
- name: Map two AD users to their LDAP accounts
  qumulo.cluster.user_defined_mappings:
    mappings:
      - {down_level_logon_name: 'CORP\alice', ldap_name: alice}
      - {down_level_logon_name: 'CORP\bob', ldap_name: robert}

- name: Preview a mapping added to the list
  qumulo.cluster.user_defined_mappings:
    mappings:
      - {down_level_logon_name: 'CORP\alice', ldap_name: alice}
      - {down_level_logon_name: 'CORP\bob', ldap_name: robert}
      - {down_level_logon_name: 'CORP\carol', ldap_name: carol}
  check_mode: true
  diff: true
  register: mappings_preview

- name: Show the mappings the change would leave
  ansible.builtin.debug:
    var: mappings_preview.user_defined_mappings.mappings

# The list is the whole set, so a mapping left out is removed.
- name: Keep only alice's mapping
  qumulo.cluster.user_defined_mappings:
    mappings:
      - {down_level_logon_name: 'CORP\alice', ldap_name: alice}

- name: Map every CORP user to the LDAP user of the same name
  qumulo.cluster.user_defined_mappings:
    mappings:
      - {down_level_logon_name: 'CORP\*', ldap_name: '*'}

# The cluster caches identities. A user it has already seen follows a changed mapping once the cache is cleared.
- name: Set the user-defined mappings
  qumulo.cluster.user_defined_mappings:
    mappings:
      - {down_level_logon_name: 'CORP\alice', ldap_name: alice}
      - {down_level_logon_name: 'CORP\bob', ldap_name: robert}
  register: mapped

- name: Clear the authentication cache after the mappings changed
  qumulo.cluster.auth_cache_clear:
  when: mapped is changed

- name: Remove every mapping
  qumulo.cluster.user_defined_mappings:
    mappings: []
"""

RETURN = r"""
user_defined_mappings:
  description: The mappings after the task, in the cluster's order, or in check mode those the task would leave.
  returned: always
  type: dict
  contains:
    mappings:
      description: Each mapping, with C(down_level_logon_name) and C(ldap_name).
      type: list
      elements: dict
  sample: {mappings: [{down_level_logon_name: 'CORP\alice', ldap_name: alice}]}
notes:
  description:
    - What the module noticed, such as a cluster not joined to a domain yet in check mode, or a write it confirmed by
      reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["the cluster is not joined to Active Directory, which user-defined mappings need; an earlier task is expected to join it"]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the mappings back did not confirm it.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the keys that did not converge.
  returned: when the mappings did not converge
  type: dict
  sample: {mappings: [{down_level_logon_name: 'CORP\alice', ldap_name: alice}]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the mappings did not converge
  type: dict
  sample: {mappings: []}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import UserDefinedMappings
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "mappings": {
        "type": "list",
        "elements": "dict",
        "required": True,
        "options": {
            "down_level_logon_name": {"type": "str", "required": True},
            "ldap_name": {"type": "str", "required": True},
        },
    },
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), UserDefinedMappings)


if __name__ == "__main__":
    main()
