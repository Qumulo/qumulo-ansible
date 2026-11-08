#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: role
short_description: Manage a custom Qumulo role and its privileges
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or deletes a custom role, with its description and the privileges it grants. Who holds the role is
    M(qumulo.cluster.role_member)'s to manage.
  - The cluster compares role names without regard to case, and so does the module. A role cannot be renamed.
  - O(privileges) is the role's whole set, compared without regard to order. Privileges the cluster does not know, as
    on an older Qumulo Core version, fail before any write.
  - The built-in roles C(Administrators), C(Observers), C(Data-Administrators) and C(Qumulo-Support) cannot be changed
    or deleted. A task that names one and matches it reports no change; one that would change or delete it fails
    before any write.
  - The module refuses to delete a role that still has members, before any write, and the message names
    M(qumulo.cluster.role_member). In check mode it reports the delete instead, with a note, because an earlier task may remove them.
options:
  name:
    description: The role's name, 1 to 64 characters without whitespace.
    type: str
    required: true
  description:
    description: What the role is for. A new role without one gets V("").
    type: str
  privileges:
    description:
      - Every privilege the role grants, such as V(PRIVILEGE_SNAPSHOT_READ). A new role without them grants none.
      - The privileges differ by Qumulo Core version, so they are not listed here; the cluster lists its own at
        C(GET /v1/auth/privileges/).
    type: list
    elements: str
  state:
    description: V(present) creates or changes the role; V(absent) deletes it, which fails while it has members.
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
  - "API: GET and POST /v1/auth/roles/; GET, PATCH and DELETE /v1/auth/roles/{name}, with If-Match; GET
    /v1/auth/privileges/ before the privileges change; GET /v1/auth/roles/{name}/members (paginated) before a
    delete."
  - A role is found in the list of roles, since the cluster answers a GET of a role that does not exist with the
    error a missing route gets (QUIRK-030).
  - "Registers: QUIRK-030."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# A role must exist before its members, so create it first and assign members afterwards.
- name: Let backup operators read snapshots and quotas
  qumulo.cluster.role:
    name: Backup-Operators
    description: Reads snapshots and quotas for the backup service
    privileges:
      - PRIVILEGE_SNAPSHOT_READ
      - PRIVILEGE_QUOTA_READ

- name: Give the role to the backup service's AD group
  qumulo.cluster.role_member:
    role: Backup-Operators
    members: ['ad:CORP\Backup-Service']

- name: Preview a privilege added to the role
  qumulo.cluster.role:
    name: Backup-Operators
    privileges:
      - PRIVILEGE_SNAPSHOT_READ
      - PRIVILEGE_QUOTA_READ
      - PRIVILEGE_SNAPSHOT_WRITE
  check_mode: true
  diff: true
  register: role_preview

- name: Show the privileges the change would leave
  ansible.builtin.debug:
    var: role_preview.role.privileges

- name: Let backup operators also take snapshots
  qumulo.cluster.role:
    name: Backup-Operators
    privileges:
      - PRIVILEGE_SNAPSHOT_READ
      - PRIVILEGE_QUOTA_READ
      - PRIVILEGE_SNAPSHOT_WRITE

- name: Change what the role is described as, keeping its privileges
  qumulo.cluster.role:
    name: Backup-Operators
    description: Reads and takes snapshots for the backup service

# The role cannot be deleted while it has members, so remove them first.
- name: Delete the role and its members
  block:
    - name: Remove every member of the role
      qumulo.cluster.role_member:
        role: Backup-Operators
        members: []
        purge: true

    - name: Delete the role
      qumulo.cluster.role:
        name: Backup-Operators
        state: absent
"""

RETURN = r"""
role:
  description: The role after the task, or in check mode the role the task would leave; V({}) once deleted.
  returned: always
  type: dict
  contains:
    name:
      description: The role's name, in the case the cluster keeps.
      type: str
    description:
      description: What the role is for.
      type: str
    privileges:
      description: Every privilege the role grants, sorted.
      type: list
      elements: str
  sample: {name: Backup-Operators, description: Reads snapshots and quotas, privileges: [PRIVILEGE_QUOTA_READ, PRIVILEGE_SNAPSHOT_READ]}
notes:
  description:
    - What the module noticed, such as a role that still has members when a delete is previewed in check mode, or a write
      it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["role 'Backup-Operators' still has 2 members; an earlier task is expected to remove them"]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the role back did not confirm it.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the keys that did not converge.
  returned: when the role did not converge
  type: dict
  sample: {privileges: [PRIVILEGE_SNAPSHOT_READ]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the role did not converge
  type: dict
  sample: {privileges: []}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import Role
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "description": {"type": "str"},
    "privileges": {"type": "list", "elements": "str"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), Role)


if __name__ == "__main__":
    main()
