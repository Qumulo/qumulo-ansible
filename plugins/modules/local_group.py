#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: local_group
short_description: Manage a Qumulo cluster's local group and its members
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or deletes the cluster-local group named O(name), and adds or removes its local users.
  - The group is found by name, never by its id.
  - Every user whose primary group is this group is a member, and the server keeps that membership itself; the module
    never adds or removes it, and it never counts as an extra member for O(purge).
options:
  name:
    description: The group's name. Users and groups share one set of names on a cluster.
    type: str
    required: true
  gid:
    description:
      - The group's NFS group id. Unset keeps the current value; a new group then has none.
      - The module cannot clear a group id once set.
    type: int
  members:
    description:
      - Names of local users who are members, besides those whose primary group this is.
      - Unset leaves membership alone. Listed users are added; with O(purge), members not listed are removed.
      - A listed user who does not exist fails the task. In check mode it is reported as a change, with a note,
        because an earlier task may create it.
    type: list
    elements: str
  purge:
    description: Remove members not listed in O(members). Members whose primary group this is always stay.
    type: bool
    default: false
  state:
    description:
      - Whether the group exists. The built-in groups C(Users) and C(Guests) cannot be deleted; asking to fails before
        any request is sent.
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
  - "API: GET and POST /v1/groups/; GET, PUT (with If-Match) and DELETE /v1/groups/{id}; GET and POST
    /v1/groups/{id}/members/, DELETE /v1/groups/{id}/members/{member_id}; GET /v1/users/."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# A user's primary group must exist before the user, so create the group first and set its members once the users
# exist. The cluster_baseline role works in this order.
- name: Create the group without members, before its users exist
  qumulo.cluster.local_group:
    name: edge-admins
    gid: 5000

- name: Create a group and put two existing users in it
  qumulo.cluster.local_group:
    name: edge-operators
    gid: 5001
    members: [svc-backup, svc-monitor]

- name: Make svc-backup its only member, besides users whose primary group it is
  qumulo.cluster.local_group:
    name: edge-admins
    members: [svc-backup]
    purge: true

- name: Preview adding svc-monitor
  qumulo.cluster.local_group:
    name: edge-admins
    members: [svc-monitor]
  check_mode: true
  diff: true
  register: group_preview

- name: Show the members the change would leave
  ansible.builtin.debug:
    var: group_preview.local_group.members

- name: Add svc-monitor, keeping the other members
  qumulo.cluster.local_group:
    name: edge-admins
    members: [svc-monitor]

- name: Remove every member except users whose primary group it is
  qumulo.cluster.local_group:
    name: edge-admins
    members: []
    purge: true

- name: Delete the group
  qumulo.cluster.local_group:
    name: edge-admins
    state: absent
"""

RETURN = r"""
local_group:
  description: The group after the task, or in check mode the group the task would leave; empty after a delete.
  returned: always
  type: dict
  contains:
    name:
      description: The group's name.
      type: str
    gid:
      description: The NFS group id, or V(null).
      type: int
    members:
      description: Every member's name, sorted, including users whose primary group this is.
      type: list
      elements: str
  sample: {name: edge-admins, gid: 5000, members: [svc-backup]}
notes:
  description:
    - What the module noticed, such as a member that does not exist yet in check mode, or a write it confirmed by reading
      back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["local user 'svc-monitor' does not exist yet; an earlier task is expected to create it"]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the group back did not confirm it.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the keys that did not converge.
  returned: when the group did not converge
  type: dict
  sample: {gid: 5000}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the group did not converge
  type: dict
  sample: {gid: 5001}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import LocalGroup
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "gid": {"type": "int"},
    "members": {"type": "list", "elements": "str"},
    "purge": {"type": "bool", "default": False},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), LocalGroup)


if __name__ == "__main__":
    main()
