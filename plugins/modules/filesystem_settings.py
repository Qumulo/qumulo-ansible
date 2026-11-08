#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: filesystem_settings
short_description: Manage a Qumulo cluster's file system settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster-wide file system settings, the permissions mode, access time updates and change notification.
    Options left unset keep the cluster's values.
  - These settings are cluster-wide, so the module has no key.
  - The cluster keeps these settings in three documents, and only a document whose settings differ is written.
options:
  permissions_mode:
    description:
      - How the cluster reconciles a POSIX mode change from NFS with the ACL a file carries for SMB, and the reverse.
      - A change applies to later operations only; the cluster rewrites no existing ACL.
    type: str
    choices: [NATIVE, CROSS_PROTOCOL, CROSS_PROTOCOL_POSIX_PRIORITY]
  atime_enabled:
    description: Whether reading a file updates its access time.
    type: bool
  atime_granularity:
    description: How stale an access time may get before a read updates it.
    type: str
    choices: [HOUR, DAY, WEEK]
  notify_recursive_mode:
    description:
      - How the cluster answers an SMB client that watches a directory tree for changes.
      - V(ENABLED) reports changes anywhere in the tree, which can be costly when clients watch large trees.
        V(DISABLED_ERROR) refuses such a watch, and V(DISABLED_IGNORE) watches only the directory itself.
    type: str
    choices: [ENABLED, DISABLED_ERROR, DISABLED_IGNORE]
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
  - "API: GET and PUT /v1/file-system/settings/permissions; GET and PATCH (If-Match) /v1/file-system/settings/atime;
    GET and PUT (If-Match) /v1/file-system/settings/notify."
  - The permissions document carries no ETag, so a change another client makes to it between the module's read and its
    write is overwritten.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Update access times at most once an hour
  qumulo.cluster.filesystem_settings:
    atime_enabled: true
    atime_granularity: HOUR

- name: Stop reads from updating access times
  qumulo.cluster.filesystem_settings:
    atime_enabled: false

- name: Refuse recursive change notification
  # ENABLED reports changes anywhere in a watched tree, which can be costly when clients watch large trees.
  qumulo.cluster.filesystem_settings:
    notify_recursive_mode: DISABLED_ERROR

- name: Use the cross-protocol permissions mode
  # A change applies to later operations only. Set it before the tasks that create directories and ACLs.
  qumulo.cluster.filesystem_settings:
    permissions_mode: CROSS_PROTOCOL

- name: Set every file system setting in one task
  qumulo.cluster.filesystem_settings:
    permissions_mode: CROSS_PROTOCOL
    atime_enabled: true
    atime_granularity: DAY
    notify_recursive_mode: DISABLED_IGNORE

- name: Preview a change of the permissions mode
  qumulo.cluster.filesystem_settings:
    permissions_mode: NATIVE
  check_mode: true
  diff: true
  register: fs_settings_preview

- name: Show the permissions mode the cluster uses now
  ansible.builtin.debug:
    msg: "permissions_mode is {{ fs_settings_preview.diff.before.permissions_mode }} and would become NATIVE"
  when: fs_settings_preview.changed
"""

RETURN = r"""
filesystem_settings:
  description: The settings after the task, or in check mode the settings the task would leave, with the option names above.
  returned: always
  type: dict
  contains:
    permissions_mode:
      description: How POSIX mode changes and ACLs are reconciled.
      type: str
      sample: CROSS_PROTOCOL
    atime_enabled:
      description: Whether reading a file updates its access time.
      type: bool
      sample: true
    atime_granularity:
      description: How stale an access time may get before a read updates it.
      type: str
      sample: HOUR
    notify_recursive_mode:
      description: How the cluster answers an SMB client that watches a directory tree.
      type: str
      sample: ENABLED
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
  sample: {atime_enabled: true}
observed:
  description: The values the cluster holds for the same options.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {atime_enabled: false}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.filesystem import FilesystemSettings

ARGUMENT_SPEC = {
    "permissions_mode": {"type": "str", "choices": ["NATIVE", "CROSS_PROTOCOL", "CROSS_PROTOCOL_POSIX_PRIORITY"]},
    "atime_enabled": {"type": "bool"},
    "atime_granularity": {"type": "str", "choices": ["HOUR", "DAY", "WEEK"]},
    "notify_recursive_mode": {"type": "str", "choices": ["ENABLED", "DISABLED_ERROR", "DISABLED_IGNORE"]},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), FilesystemSettings)


if __name__ == "__main__":
    main()
