#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: directory_quota
short_description: Manage the hard capacity quota of a directory on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets, changes or removes the quota of one directory, named by its path.
  - A quota is matched by the path of its directory. The module resolves the path to the directory's id on every run
    and never keeps the id.
  - With O(state=present) the directory must exist. In check mode a missing directory is reported as a change; outside it the module fails.
options:
  path:
    description:
      - Absolute path of the directory, such as V(/data/projects). A trailing slash is ignored.
      - The cluster refuses a quota on a directory inside a portal spoke's root, and the module fails with that
        reason (G-SPOKE-PATHS).
    type: str
    required: true
  state:
    description:
      - V(present) sets the quota to O(limit).
      - V(absent) removes the quota. A directory that has no quota, or does not exist, already has none.
    type: str
    choices: [present, absent]
    default: present
  limit:
    description:
      - The quota in bytes, as a number or a size with a unit, such as V(500GB) (10**9 bytes each) or V(2TiB) (2**40 bytes each).
      - The units are B, KB, MB, GB, TB, PB and EB, which are powers of 1000, and KiB, MiB, GiB, TiB, PiB and EiB,
        which are powers of 1024, in any case. A fraction such as V(1.5TiB) is accepted when it is a whole number of bytes.
      - Required with O(state=present).
    type: str
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - A directory that does not exist yet, because an earlier task creates it, is reported as a change instead of failing.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET /v1/files/{path}/info/attributes, then GET, PUT (If-Match) and DELETE /v1/files/quotas/{id}, and POST /v1/files/quotas/."
  - The quota follows the directory's id, so a renamed directory keeps its quota under its new path, and a run that names
    the old path no longer finds it.
  - "Registers: G-SPOKE-PATHS."
seealso:
  - module: qumulo.cluster.directory
    description: Creates the directory a quota is set on.
  - module: qumulo.cluster.filesystem_info
    description: Lists every quota with its path and usage.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Create the project tree and cap it at 2 TiB
  # The cluster refuses a quota on a directory inside a portal spoke's root (G-SPOKE-PATHS).
  block:
    - name: Create the project tree
      qumulo.cluster.directory:
        path: /data/projects

    - name: Cap the project tree at 2 TiB
      qumulo.cluster.directory_quota:
        path: /data/projects
        limit: 2TiB

- name: Raise the project quota to 3 TiB
  qumulo.cluster.directory_quota:
    path: /data/projects
    limit: 3TiB

- name: Cap the project tree at an exact number of bytes
  qumulo.cluster.directory_quota:
    path: /data/projects
    limit: "2199023255552"

- name: Preview a 500 GB quota on the scratch tree
  qumulo.cluster.directory_quota:
    path: /data/scratch
    limit: 500GB
  check_mode: true
  diff: true
  register: scratch_quota

- name: Show the scratch tree's quota before and after
  ansible.builtin.debug:
    msg: "{{ scratch_quota.diff.before.limit | default('no quota') }} -> {{ scratch_quota.quota.limit }} bytes"
  when: scratch_quota.changed

- name: Remove the quota
  qumulo.cluster.directory_quota:
    path: /data/projects
    state: absent
"""

RETURN = r"""
quota:
  description: The quota after the task, or an empty dict after O(state=absent).
  returned: always
  type: dict
  contains:
    path:
      description: The directory's path, without a trailing slash.
      type: str
      sample: /data/projects
    limit:
      description: The quota in bytes.
      type: int
      sample: 2199023255552
notes:
  description:
    - What the module noticed, such as a directory that does not exist yet in check mode, or a write it confirmed by
      reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["/data/scratch does not exist yet; the quota is set once an earlier task creates it"]
maybe_applied:
  description: True when a write was sent and the read-back did not confirm it, as after a lost connection.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options that read back differently.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {limit: 2199023255552}
observed:
  description: The values the cluster holds for the same options.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {limit: 1099511627776}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.quotas import DirectoryQuota

ARGUMENT_SPEC = {
    "path": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "limit": {"type": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC, required_if=[("state", "present", ["limit"])]), DirectoryQuota)


if __name__ == "__main__":
    main()
