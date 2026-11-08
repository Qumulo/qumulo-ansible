#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: local_user
short_description: Manage a Qumulo cluster's local user
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or deletes the cluster-local user named O(name).
  - The user is found by name, never by its id.
  - A password cannot be read back (G-PASSWORD). It is sent when the user is created, and on later runs only with
    O(update_password=always), which then reports a change on every run.
  - Membership of groups other than the primary group is M(qumulo.cluster.local_group)'s to manage. Changing the primary group
    adds the user to the new group; the server keeps the user in the old one too.
options:
  name:
    description: The user's name. Users and groups share one set of names on a cluster.
    type: str
    required: true
  primary_group:
    description:
      - Name of the local group that is the user's primary group. Unset keeps the current one; a new user gets
        C(Users).
      - A group that does not exist fails the task. In check mode it is reported as a change, with a note, because an
        earlier task may create it.
    type: str
  uid:
    description: The user's NFS user id. Unset keeps the current value; a new user then has none.
    type: int
  home_directory:
    description: The user's home directory path. Unset keeps the current value; an empty string clears it.
    type: str
  password:
    description:
      - The user's password. Needed to create a user.
      - Sent on create, and on later runs only with O(update_password=always).
    type: str
  update_password:
    description:
      - V(on_create) sends O(password) only when the user is created, so later runs report no change for it.
      - V(always) sends it on every run, and so always reports a change.
    type: str
    choices: [on_create, always]
    default: on_create
  allow_admin_password_change:
    description:
      - Allow a change to the password of C(admin). The server applies it to the operating system's console and SSH
        password of every node too (G-ADMIN-OS). Without this option the module refuses the change.
    type: bool
    default: false
  allow_management_disruption:
    description:
      - Allow a change to the password of the user this task connects as. Later tasks must then connect with the new
        password. The module reports RV(connection_password_changed) when it made such a change.
    type: bool
    default: false
  state:
    description: Whether the user exists. C(admin) and C(guest) cannot be deleted; asking to fails before any request is sent.
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
    support: partial
    details:
      - The password cannot be read back, so O(update_password=always) reports a change on every run.
notes:
  - "API: GET and POST /v1/users/; GET, PUT (with If-Match) and DELETE /v1/users/{id}; GET /v1/groups/;
    GET /v1/session/who-am-i when the task connects with an access token."
  - The PUT that changes a user sends its uid and home directory again, as the server replaces the whole user.
  - "Registers: G-PASSWORD, G-ADMIN-OS."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# A user's primary group must exist first: create it with qumulo.cluster.local_group, or use a built-in one.
- name: Create a service user, setting its password only when it is created
  qumulo.cluster.local_user:
    name: svc-backup
    primary_group: Users
    uid: 5001
    password: "{{ vault_svc_backup_pw }}"

- name: Create a user with a home directory in an existing group
  qumulo.cluster.local_user:
    name: svc-monitor
    primary_group: edge-admins
    uid: 5002
    home_directory: /home/svc-monitor
    password: "{{ vault_svc_monitor_pw }}"

- name: Preview moving svc-backup to the edge-admins group
  qumulo.cluster.local_user:
    name: svc-backup
    primary_group: edge-admins
  check_mode: true
  diff: true
  register: user_preview

- name: Show the user the change would leave
  ansible.builtin.debug:
    var: user_preview.local_user

# The cluster keeps the user in its old primary group too.
- name: Make edge-admins svc-backup's primary group and give it a home directory
  qumulo.cluster.local_user:
    name: svc-backup
    primary_group: edge-admins
    home_directory: /home/svc-backup

- name: Clear svc-backup's home directory
  qumulo.cluster.local_user:
    name: svc-backup
    home_directory: ""

# The password cannot be read back, so this task reports a change on every run.
- name: Set svc-backup's password from the vault
  qumulo.cluster.local_user:
    name: svc-backup
    password: "{{ vault_svc_backup_pw }}"
    update_password: always

# The password of admin is also every node's console and SSH password (G-ADMIN-OS). When the play connects as
# admin, later tasks must connect with the new password; connection_password_changed says when. The collection's
# playbooks/admin_password_rotate.yml does this from the inventory.
- name: Rotate the admin password
  qumulo.cluster.local_user:
    name: admin
    password: "{{ vault_admin_password_next }}"
    update_password: always
    allow_admin_password_change: true
    allow_management_disruption: true
  register: admin_rotated

# The playbooks' module_defaults read qumulo_api_password, so later tasks connect with the new password.
- name: Connect with the new admin password from here on
  ansible.builtin.set_fact:
    qumulo_api_password: "{{ vault_admin_password_next }}"
  when: admin_rotated.connection_password_changed | default(false)

- name: Delete the service user
  qumulo.cluster.local_user:
    name: svc-backup
    state: absent
"""

RETURN = r"""
local_user:
  description: The user after the task, or in check mode the user the task would leave; empty after a delete. The password is never returned.
  returned: always
  type: dict
  contains:
    name:
      description: The user's name.
      type: str
    primary_group:
      description: The primary group's name.
      type: str
    uid:
      description: The NFS user id, or V(null).
      type: int
    home_directory:
      description: The home directory, or V(null).
      type: str
  sample: {name: svc-backup, primary_group: Users, uid: 5001, home_directory: null}
connection_password_changed:
  description: V(true) when the task changed the password of the user it connects as.
  returned: when the password of the connecting user changed
  type: bool
  sample: true
notes:
  description:
    - What the module noticed, such as a primary group that does not exist yet in check mode, or a write it confirmed by
      reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["local group 'edge-admins' does not exist yet; an earlier task is expected to create it"]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the user back did not confirm it.
    A lost change of an existing user's password, with O(update_password=always), cannot be confirmed by reading back, so that task then always fails
    with this set.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the keys that did not converge. The password is left out.
  returned: when the user did not converge
  type: dict
  sample: {uid: 5001}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the user did not converge
  type: dict
  sample: {uid: 5002}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import LocalUser
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "primary_group": {"type": "str"},
    "uid": {"type": "int"},
    "home_directory": {"type": "str"},
    "password": {"type": "str", "no_log": True},
    "update_password": {"type": "str", "choices": ["on_create", "always"], "default": "on_create", "no_log": False},
    "allow_admin_password_change": {"type": "bool", "default": False, "no_log": False},
    "allow_management_disruption": {"type": "bool", "default": False},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), LocalUser)


if __name__ == "__main__":
    main()
