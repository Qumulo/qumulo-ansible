#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: role_member
short_description: Manage the members of a Qumulo role
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Assigns trustees, such as local users and groups or Active Directory users and groups, to the role O(role), or
    removes them.
  - Trustees are named, never given by id, and resolved on every run. A member is the same trustee when it resolves to
    the same identity, so V(ad:CORP\\alice) and V(ad:alice@corp.example.com) are one member.
  - An C(ad:) trustee resolves only once the cluster has joined the domain, except
    V(ad:NT AUTHORITY\\Authenticated Users), which resolves joined to a domain or not.
  - The role must exist, as M(qumulo.cluster.role) creates a custom one, unless the task only removes members, with
    O(state=absent) or with O(purge) and no O(members), since a role that does not exist has none to remove; the task
    then reports no change, with a note, and looks up no member. In check mode a role that does not exist yet has no
    members, with a note, because an earlier task may create it.
  - A name the cluster could not find stays unresolvable for about a minute after it is created (QUIRK-017). A run
    that creates a local user and assigns it a role soon after a check run looked the user up can therefore fail
    to resolve it.
options:
  role:
    description: The role's name, such as C(Administrators), C(Observers) or a custom role.
    type: str
    required: true
  members:
    description:
      - Trustees in the collection's grammar, such as V(local:svc-backup), V(ad:CORP\\Qumulo-Admins),
        V(ldap_group:admins), V(sid:S-1-5-21-...), V(uid:1000) or V(Everyone). A bare name is refused, because it
        is ambiguous once the cluster uses AD or LDAP.
      - V(qumulo_support:qumulosupport:everyone) is the member a cluster registered with Nexus has in the
        C(Qumulo-Support) role.
      - With O(state=present), a trustee that does not exist fails the task; in check mode it is reported as a change,
        with a note, because an earlier task may create it or join its domain.
      - With O(state=absent), a trustee that does not exist is no member, with a note, in check mode and in a real run
        alike, as after its account is deleted.
    type: list
    elements: str
    required: true
  purge:
    description:
      - With O(state=present), also remove members not listed in O(members).
      - The local C(admin) and C(system) users always stay in C(Administrators), as the server requires. O(purge) is refused for
        the C(Qumulo-Support) role, which holds the assignment Qumulo's support access relies on.
    type: bool
    default: false
  allow_management_disruption:
    description:
      - Allow removing the user this task connects as from the role, which can take away the privileges later tasks
        need. Only a direct assignment of that user is detected, not one through a group.
    type: bool
    default: false
  state:
    description: V(present) assigns the members; V(absent) removes them.
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
  - "API: GET (paginated) and POST /v1/auth/roles/{role}/members, DELETE /v1/auth/roles/{role}/members/{auth_id},
    POST /v1/identity/find; GET /v1/session/who-am-i when the task connects with an access token."
  - "Registers: QUIRK-017."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# An ad: trustee resolves only after the cluster has joined the domain with qumulo.cluster.ad_domain, and a local
# trustee only after qumulo.cluster.local_user created it. Deleting a local user drops its role assignments, so assign
# roles after the users are final.
- name: Make the AD group of storage administrators the cluster's administrators
  qumulo.cluster.role_member:
    role: Administrators
    members: ['ad:CORP\Qumulo-Admins']

- name: Give a local service user the Observers role
  qumulo.cluster.role_member:
    role: Observers
    members: ['local:svc-monitor']

- name: Preview which observers purging would remove
  qumulo.cluster.role_member:
    role: Observers
    members: ['local:svc-monitor']
    purge: true
  check_mode: true
  diff: true
  register: observers_preview

- name: Show the observers that would remain
  ansible.builtin.debug:
    var: observers_preview.role_member.members

- name: Let only the monitoring service user observe
  qumulo.cluster.role_member:
    role: Observers
    members: ['local:svc-monitor']
    purge: true

- name: Add the storage operators to the administrators, keeping the other members
  qumulo.cluster.role_member:
    role: Administrators
    members: ['ad:CORP\Storage-Operators']

- name: Take the storage operators out of the administrators
  qumulo.cluster.role_member:
    role: Administrators
    members: ['ad:CORP\Storage-Operators']
    state: absent

# A role with members cannot be deleted. This removes every member of a custom role, so that qumulo.cluster.role can
# delete it.
- name: Remove every member of the Backup-Operators role
  qumulo.cluster.role_member:
    role: Backup-Operators
    members: []
    purge: true
"""

RETURN = r"""
role_member:
  description: The role's members after the task, or in check mode the members the task would leave.
  returned: always
  type: dict
  contains:
    role:
      description: The role's name.
      type: str
    members:
      description: Every member as a trustee string, sorted.
      type: list
      elements: str
  sample: {role: Administrators, members: ['ad:CORP\Qumulo-Admins', 'local:admin']}
notes:
  description:
    - What the module noticed, such as a role that does not exist yet in check mode, or a trustee that does not exist and
      so is no member.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["role 'Backup-Operators' does not exist yet; an earlier task is expected to create it"]
maybe_applied:
  description: V(true) when a membership write was sent and the connection was lost before the cluster answered. The module does not
    send it again.
  returned: on a failure after such a write
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import RoleMember
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "role": {"type": "str", "required": True},
    "members": {"type": "list", "elements": "str", "required": True},
    "purge": {"type": "bool", "default": False},
    "allow_management_disruption": {"type": "bool", "default": False},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), RoleMember)


if __name__ == "__main__":
    main()
