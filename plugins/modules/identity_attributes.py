#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: identity_attributes
short_description: Manage the home directory a Qumulo cluster gives an Active Directory, LDAP or POSIX identity
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets or removes the attributes the cluster keeps for one identity it does not own, such as an Active Directory
    user. The only attribute is the home directory.
  - The identity is named by O(trustee), never given by id, and resolved on every run.
  - A local user's home directory is M(qumulo.cluster.local_user)'s O(qumulo.cluster.local_user#module:home_directory),
    and the cluster stores both in one place, so a trustee that resolves to a local identity is refused.
  - The cluster lists no identity's attributes, so M(qumulo.cluster.capture_info) cannot read them back (G-ID-ATTRIBUTES).
options:
  trustee:
    description:
      - The identity, in the collection's trustee grammar, such as V(ad:CORP\\alice), V(ldap_user:alice), V(uid:1000),
        V(gid:1000) or V(sid:S-1-5-21-...). A bare name is refused, because it is ambiguous once the cluster uses AD or
        LDAP.
      - An C(ad:) trustee resolves only once the cluster has joined the domain, except
        V(ad:NT AUTHORITY\\Authenticated Users), which resolves joined to a domain or not.
      - With O(state=present), a trustee that does not exist fails the task; in check mode it is reported as a change,
        with a note, because an earlier task may join its domain.
      - With O(state=absent), a trustee that does not exist has no attributes to remove, so the task reports no change,
        with a note, in check mode and in a real run alike, as after the identity's account is deleted.
    type: str
    required: true
  home_directory:
    description:
      - The identity's home directory, as the path the cluster shows, such as V(/home/alice).
      - Required with O(state=present).
    type: str
  state:
    description: V(present) sets the attributes; V(absent) removes them, so the cluster keeps none for the identity.
    type: str
    choices: [present, absent]
    default: present
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
  - "API: POST /v1/identity/find; GET, PUT and DELETE /v1/auth/identities/{auth_id}/attributes, which take no
    If-Match, so the last writer wins."
  - "Registers: G-ID-ATTRIBUTES."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# An ad: trustee resolves only after the cluster has joined the domain with qumulo.cluster.ad_domain.
- name: Give an AD user a home directory
  qumulo.cluster.identity_attributes:
    trustee: 'ad:CORP\alice'
    home_directory: /home/alice

- name: Give an AD user a home directory, named by its user principal name
  qumulo.cluster.identity_attributes:
    trustee: 'ad:alice@corp.example.com'
    home_directory: /home/alice

- name: Give an LDAP user a home directory
  qumulo.cluster.identity_attributes:
    trustee: ldap_user:alice
    home_directory: /home/alice

- name: Give an NFS user, known only by its uid, a home directory
  qumulo.cluster.identity_attributes:
    trustee: uid:1000
    home_directory: /home/u1000

- name: Preview moving an AD user's home directory
  qumulo.cluster.identity_attributes:
    trustee: 'ad:CORP\alice'
    home_directory: /home2/alice
  check_mode: true
  diff: true
  register: home_preview

- name: Show the attributes the move would leave
  ansible.builtin.debug:
    var: home_preview.identity_attributes

# The cluster caches identities; a changed home directory reaches one it has already seen once the cache is cleared.
- name: Move an AD user's home directory
  qumulo.cluster.identity_attributes:
    trustee: 'ad:CORP\alice'
    home_directory: /home2/alice
  register: home_moved

- name: Clear the authentication cache after the home directory moved
  qumulo.cluster.auth_cache_clear:
  when: home_moved is changed

- name: Remove an AD user's attributes
  qumulo.cluster.identity_attributes:
    trustee: 'ad:CORP\alice'
    state: absent
"""

RETURN = r"""
identity_attributes:
  description: The identity's attributes after the task, or in check mode those the task would leave; V({}) when it has none.
  returned: always
  type: dict
  contains:
    trustee:
      description: The identity as a trustee string, as the cluster names it.
      type: str
    home_directory:
      description: The identity's home directory.
      type: str
  sample: {trustee: 'ad:CORP\alice', home_directory: /home/alice}
notes:
  description:
    - What the module noticed, such as a trustee that does not exist yet in check mode, or a write it confirmed by
      reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["trustee 'ad:CORP\\alice' does not exist yet; an earlier task is expected to create it or join its domain"]
maybe_applied:
  description: V(true) when the write was sent and the connection was lost before the cluster answered, and reading the attributes back did not confirm it.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The attributes the task wrote, on a failure where the cluster read back something else.
  returned: when the attributes did not converge
  type: dict
  sample: {home_directory: /home/alice}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the attributes did not converge
  type: dict
  sample: {home_directory: /home/alice2}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import IdentityAttributes
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "trustee": {"type": "str", "required": True},
    "home_directory": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC, required_if=[("state", "present", ("home_directory",))]), IdentityAttributes)


if __name__ == "__main__":
    main()
