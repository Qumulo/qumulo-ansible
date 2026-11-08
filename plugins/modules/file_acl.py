#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: file_acl
short_description: Declare the access control list of a file or directory on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Makes the ACL of one path the entries the task lists, in order, and replaces the whole ACL when it differs.
  - The ACL is matched by the path it belongs to.
  - Reports drift as a change, with the ACL the cluster holds in the diff's C(before), so a check run with diff shows
    what changed since the ACL was last declared.
  - The module never creates the path. In check mode a missing path is reported as a change; outside it the module fails.
  - Setting a POSIX mode, as M(qumulo.cluster.directory) does, rewrites the ACL, and writing an ACL changes the mode the
    cluster reports (G-FS-ACL-MODE).
options:
  path:
    description: Absolute path of the file or directory, such as V(/data/projects). A trailing slash is ignored.
    type: str
    required: true
  aces:
    description:
      - The entries, in order. The order is compared exactly, and the first entry that grants or denies a right to a
        user decides it. An empty list denies everyone.
      - The cluster does not apply a changed ACL to the files beneath the path. Entries with inheritance flags apply to
        what is created beneath it afterwards.
    type: list
    elements: dict
    required: true
    suboptions:
      type:
        description: Whether the entry allows or denies its rights.
        type: str
        choices: [ALLOWED, DENIED]
        default: ALLOWED
      trustee:
        description:
          - Whom the entry is for, in the trustee forms M(qumulo.cluster.smb_share) takes, such as V(local:svc_backup),
            V(ad:CORP\\Engineering), V(Everyone), V(internal:File Owner), V(uid:1000), V(gid:100) or
            V(sid:S-1-5-21-1-2-3-1001).
          - V(internal:File Owner) and V(internal:File Group Owner) are the owner and the group of the file the entry is on.
            Any other V(internal:) name is refused.
          - A bare name other than V(Everyone) is refused.
          - Authenticated Users is V(ad:NT AUTHORITY\\Authenticated Users), which the cluster resolves joined to a domain or not.
          - Compared by the identity it resolves to, so V(uid:1000) and the LDAP user with that uid are one trustee.
          - An entry the cluster reads back compares by the identity it gives with the entry.
        type: str
        required: true
      rights:
        description:
          - The rights, compared as a set. V(ALL) and V(ACCESS_RIGHTS_ALL) stand for every right, V(ADD_FILE) for
            V(MODIFY) and V(ADD_SUBDIR) for V(EXTEND), as the cluster stores them.
        type: list
        elements: str
        required: true
        choices: [READ, READ_EA, READ_ATTR, READ_ACL, WRITE_EA, WRITE_ATTR, WRITE_ACL, CHANGE_OWNER, WRITE_GROUP, DELETE,
                  EXECUTE, MODIFY, EXTEND, DELETE_CHILD, SYNCHRONIZE, ALL, ACCESS_RIGHTS_ALL, ADD_FILE, ADD_SUBDIR]
      flags:
        description: The entry's inheritance flags, compared as a set.
        type: list
        elements: str
        choices: [OBJECT_INHERIT, CONTAINER_INHERIT, NO_PROPAGATE_INHERIT, INHERIT_ONLY, INHERITED]
        default: []
  control:
    description: The ACL's control flags, compared as a set. Left unset, the ACL keeps the flags it has.
    type: list
    elements: str
    choices: [PRESENT, DEFAULTED, TRUSTED, AUTO_INHERIT, PROTECTED]
  posix_special_permissions:
    description: The set-user-ID, set-group-ID and sticky bits, compared as a set. Left unset, the ACL keeps the ones it has.
    type: list
    elements: str
    choices: [SET_UID, SET_GID, STICKY_BIT]
  allow_data_loss:
    description:
      - Replace the ACL of a path inside a portal spoke root. The spoke passes the write through to the hub, so it
        replaces the hub's ACL.
      - Without this option such a path is refused when its ACL would change, in check mode too.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - A trustee or path that does not exist yet, because an earlier task creates it or joins its domain, is reported
        as a change instead of failing.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET /v1/files/{path}/info/attributes; GET and PUT /v2/files/{path}/info/acl; POST /v1/identity/find; when the
    ACL would change, GET /v2/portal/spokes/ and the attributes of each spoke root."
  - The ACL's ETag is the file's revision, which any change beneath a directory also bumps. The PUT carries the ETag of
    the module's own read, and a refusal for a stale ETag reads the ACL again and compares again, up to five times.
  - "Registers: G-FS-ACL-MODE."
seealso:
  - module: qumulo.cluster.directory
    description: Creates the directory an ACL is set on, and sets its POSIX mode.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Give engineering full control of the project tree, and everyone else read access
  # Declare the ACL or the POSIX mode of a path, not both (G-FS-ACL-MODE): leave mode out of the
  # qumulo.cluster.directory task for this path.
  qumulo.cluster.file_acl:
    path: /data/projects
    control: [PRESENT, AUTO_INHERIT]
    aces:
      - trustee: ad:CORP\Engineering
        rights: [ALL]
        flags: [OBJECT_INHERIT, CONTAINER_INHERIT]
      - trustee: Everyone
        rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]
        flags: [OBJECT_INHERIT, CONTAINER_INHERIT]

- name: Create a directory and give it an ACL
  block:
    - name: Create the archive directory
      qumulo.cluster.directory:
        path: /data/archive

    - name: Let the backup account read the archive directory
      qumulo.cluster.file_acl:
        path: /data/archive
        aces:
          - trustee: local:svc_backup
            rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]
            flags: [OBJECT_INHERIT, CONTAINER_INHERIT]

- name: Deny contractors deletes before anything allows them
  qumulo.cluster.file_acl:
    path: /data/projects
    aces:
      - type: DENIED
        trustee: ad:CORP\Contractors
        rights: [DELETE, DELETE_CHILD]
      - trustee: ad:CORP\Engineering
        rights: [ALL]

- name: Make the drop directory writable by everyone, with the sticky bit
  # aces replaces the whole ACL, so list every entry the directory keeps.
  qumulo.cluster.file_acl:
    path: /data/dropbox
    posix_special_permissions: [STICKY_BIT]
    aces:
      - trustee: Everyone
        rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, ADD_FILE, ADD_SUBDIR, SYNCHRONIZE]

- name: Check whether the ACL drifted from the declared one
  qumulo.cluster.file_acl:
    path: /data/projects
    control: [PRESENT, AUTO_INHERIT]
    aces:
      - trustee: ad:CORP\Engineering
        rights: [ALL]
        flags: [OBJECT_INHERIT, CONTAINER_INHERIT]
      - trustee: Everyone
        rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]
        flags: [OBJECT_INHERIT, CONTAINER_INHERIT]
  check_mode: true
  diff: true
  register: acl_check

- name: Show the entries the cluster holds when they drifted
  ansible.builtin.debug:
    var: acl_check.diff.before.aces
  when: acl_check.changed

- name: Replace the ACL of a path inside a portal spoke root
  # The spoke passes the write through to the hub and replaces the hub's ACL. Where you can, declare the ACL on the hub
  # instead, because the hub's own inventory may declare a different one.
  qumulo.cluster.file_acl:
    path: /hub-share/projects
    allow_data_loss: true
    aces:
      - trustee: ad:CORP\Engineering
        rights: [ALL]
        flags: [OBJECT_INHERIT, CONTAINER_INHERIT]
"""

RETURN = r"""
acl:
  description: The ACL after the task.
  returned: always
  type: dict
  contains:
    path:
      description: The path, without a trailing slash.
      type: str
      sample: /data/projects
    control:
      description: The control flags.
      type: list
      elements: str
      sample: [PRESENT, AUTO_INHERIT]
    posix_special_permissions:
      description: The special permission bits.
      type: list
      elements: str
      sample: []
    aces:
      description: >-
        The entries, in order, with their rights expanded. A trustee is as the cluster renders it, such as
        V(world:Everyone), except in check mode when the ACL would change, where it is as the task gave it. An identity
        no trustee form names is shown by the name the cluster gives, or failing that its domain.
      type: list
      elements: dict
      contains:
        type:
          description: Whether the entry allows or denies its rights.
          type: str
          sample: ALLOWED
        trustee:
          description: Whom the entry is for.
          type: str
          sample: local:Users
        rights:
          description: The rights, with each alias expanded, in the order the cluster lists them.
          type: list
          elements: str
          sample: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]
        flags:
          description: The inheritance flags.
          type: list
          elements: str
          sample: [OBJECT_INHERIT, CONTAINER_INHERIT]
notes:
  description:
    - What the module noticed, such as a path that does not exist yet in check mode, a path inside a portal spoke root
      that O(allow_data_loss) let the task write, or a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["/data/archive does not exist yet; its ACL is set once an earlier task creates it"]
maybe_applied:
  description: True when a write was sent and the read-back did not confirm it, as after a lost connection.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options that read back differently.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {posix_special_permissions: [STICKY_BIT]}
observed:
  description: The values the cluster holds for the same options.
  returned: when the cluster did not converge on what was written
  type: dict
  sample: {posix_special_permissions: []}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.files import FileAcl

RIGHTS = [
    "READ",
    "READ_EA",
    "READ_ATTR",
    "READ_ACL",
    "WRITE_EA",
    "WRITE_ATTR",
    "WRITE_ACL",
    "CHANGE_OWNER",
    "WRITE_GROUP",
    "DELETE",
    "EXECUTE",
    "MODIFY",
    "EXTEND",
    "DELETE_CHILD",
    "SYNCHRONIZE",
]
ARGUMENT_SPEC = {
    "path": {"type": "str", "required": True},
    "aces": {
        "type": "list",
        "elements": "dict",
        "required": True,
        "options": {
            "type": {"type": "str", "choices": ["ALLOWED", "DENIED"], "default": "ALLOWED"},
            "trustee": {"type": "str", "required": True},
            "rights": {"type": "list", "elements": "str", "required": True, "choices": [*RIGHTS, "ALL", "ACCESS_RIGHTS_ALL", "ADD_FILE", "ADD_SUBDIR"]},
            "flags": {
                "type": "list",
                "elements": "str",
                "choices": ["OBJECT_INHERIT", "CONTAINER_INHERIT", "NO_PROPAGATE_INHERIT", "INHERIT_ONLY", "INHERITED"],
                "default": [],
            },
        },
    },
    "control": {"type": "list", "elements": "str", "choices": ["PRESENT", "DEFAULTED", "TRUSTED", "AUTO_INHERIT", "PROTECTED"]},
    "posix_special_permissions": {"type": "list", "elements": "str", "choices": ["SET_UID", "SET_GID", "STICKY_BIT"]},
    "allow_data_loss": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), FileAcl)


if __name__ == "__main__":
    main()
