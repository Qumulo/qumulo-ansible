#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: directory
short_description: Make sure a directory exists on a Qumulo cluster, with its mode, owner and group
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates a directory by its path, with any missing parent directories, and sets its POSIX mode, owner and group
    when they are given.
  - A directory is matched by O(path), never by its file id.
  - Options left unset are not managed, so an existing directory keeps the values it has.
  - The module never removes data. With O(state=absent) it removes only an empty directory, and fails on one that
    holds anything. The root directory cannot be removed.
options:
  path:
    description:
      - Absolute path of the directory, such as V(/data/projects). A trailing slash is ignored.
      - Parent directories that do not exist are created with the cluster's default mode, owner and group.
    type: str
    required: true
  state:
    description:
      - V(present) creates the directory when it is missing and sets the options given.
      - V(absent) removes the directory if it is empty, and fails if it is not. A directory that does not exist is
        already absent.
    type: str
    choices: [present, absent]
    default: present
  mode:
    description:
      - POSIX mode as an octal string of three or four digits, such as V('0755') or V('755').
      - Must be a string. YAML reads an unquoted V(0755) as the decimal number 493, and the module refuses a number.
      - Setting the mode rewrites the directory's ACL to match it (G-FS-ACL-MODE).
    type: raw
  owner:
    description:
      - The owner, as a trustee such as V(local:svc_backup), V(ad:CORP\\alice), V(uid:1000) or V(sid:S-1-5-21-1-2-3-1001).
      - A bare name other than V(Everyone) is refused.
      - Compared by the identity it resolves to, so V(uid:1000) and the LDAP user with that uid are the same owner.
      - The owner the cluster reads back compares by the identity it gives with it, so a SID it cannot look up does not fail the task.
    type: str
  group:
    description:
      - The group, as a trustee such as V(local:Users), V(gid:100) or V(ad:CORP\\Engineering).
      - A bare name other than V(Everyone) is refused, and the group is compared like O(owner).
    type: str
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - An owner or group that does not exist yet, because an earlier task creates it or joins its domain,
        is reported as a change instead of failing.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET and PATCH /v1/files/{path}/info/attributes, POST /v1/files/{parent}/entries/, DELETE /v1/files/{path},
    POST /v1/identity/find."
  - Another client creating the same directory at the same moment is not an error, as long as the entry is a directory.
  - A path that exists and is not a directory is a failure.
  - "Registers: G-FS-ACL-MODE."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Create a directory with the cluster's default mode, owner and group
  qumulo.cluster.directory:
    path: /data/projects

- name: Make the project tree, owned by the backup service account
  # Setting the mode rewrites the directory's ACL to match it (G-FS-ACL-MODE).
  # To declare the ACL instead, leave mode out and use qumulo.cluster.file_acl.
  qumulo.cluster.directory:
    path: /data/projects
    mode: "0775"
    owner: local:svc_backup
    group: local:Users

- name: Create a nested directory, with its missing parents
  qumulo.cluster.directory:
    path: /data/projects/2026/q4
    owner: local:svc_backup

- name: Hand the project tree to the engineering group, keeping its mode and owner
  qumulo.cluster.directory:
    path: /data/projects
    group: ad:CORP\Engineering

- name: Give a home directory to the NFS user and group it belongs to
  qumulo.cluster.directory:
    path: /home/alice
    mode: "0700"
    owner: uid:1000
    group: gid:100

- name: Check whether the scratch directory has mode 0777
  qumulo.cluster.directory:
    path: /data/scratch
    mode: "0777"
  check_mode: true
  diff: true
  register: scratch_check

- name: Report a scratch directory that is missing or has another mode
  ansible.builtin.debug:
    msg: "/data/scratch: {{ scratch_check.diff.before.mode | default('no directory') }}, wanted 0777"
  when: scratch_check.changed

- name: Remove an empty scratch directory
  # The task fails when the directory holds anything.
  qumulo.cluster.directory:
    path: /data/scratch
    state: absent
"""

RETURN = r"""
directory:
  description: The directory after the task, or an empty dict after O(state=absent).
  returned: always
  type: dict
  contains:
    path:
      description: The path, without a trailing slash.
      type: str
      sample: /data/projects
    mode:
      description: The POSIX mode, four octal digits.
      type: str
      sample: "0775"
    owner:
      description: The owner as a trustee. After a write, the cluster's spelling, such as V(uid:1000) or a V(sid:) form. When nothing was written,
        the task's spelling.
      type: str
      sample: local:svc_backup
    group:
      description: The group as a trustee. After a write, the cluster's spelling, such as V(gid:100) or a V(sid:) form. When nothing was written, the
        task's spelling.
      type: str
      sample: local:Users
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: True when a write was sent and the read-back did not confirm it, as after a lost connection.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options that read back differently.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {mode: "0775"}
observed:
  description: The values the cluster holds for the same options.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {mode: "0755"}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.files import Directory

ARGUMENT_SPEC = {
    "path": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "mode": {"type": "raw"},
    "owner": {"type": "str"},
    "group": {"type": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), Directory)


if __name__ == "__main__":
    main()
