#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: ftp_settings
short_description: Manage a Qumulo cluster's FTP server settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster's FTP server settings. Options left unset keep the cluster's values.
  - Only the settings that differ are written, in one PATCH, with the If-Match of the read.
  - The cluster has one FTP server, which answers on every network, so the module has no tenant option.
options:
  enabled:
    description: Whether the cluster allows FTP connections.
    type: bool
  check_remote_host:
    description:
      - Whether all the connections of one FTP session must come from the same host.
      - Turning it off allows FXP, a transfer between two servers that a third host directs.
    type: bool
  log_operations:
    description: Whether the cluster logs FTP operations.
    type: bool
  chroot_users:
    description:
      - Whether the cluster confines FTP users with C(chroot).
    type: bool
  allow_unencrypted_connections:
    description:
      - Whether the cluster accepts FTP connections that are not encrypted.
      - With V(false) it accepts only encrypted connections.
    type: bool
  expand_wildcards:
    description: Whether C(LIST) and C(NLST) take POSIX-like shell wildcards in the final path component.
    type: bool
  anonymous_user:
    description:
      - The local user that anonymous logins act as, written V(local:<name>), or V("") for no anonymous logins.
      - The cluster takes only a local user. Any other form, a bare name, C(Everyone), or an AD, SMB or NFS identity
        included, fails before any request, naming the form the option takes. The name of a local group, written
        V(local:<name>), reaches the cluster, which refuses it.
      - The name is compared in any case, as the cluster finds a local user, and the result and the diff show it as the
        cluster holds it. In check mode a changed user is looked up in the cluster's user list, so the result shows
        the cluster's spelling there too.
      - A name the cluster has no local user for fails, naming M(qumulo.cluster.local_user). In check mode no write is
        sent, so such a name is reported as a change with a note in RV(notes), as a user an earlier task creates.
      - After the local user is deleted, the cluster keeps the setting with an empty name, which the module returns as
        V(local:). Declaring V(local:) fails.
    type: str
  greeting:
    description:
      - The text sent with the C(220) reply to a new connection.
      - It takes 1 to 1024 bytes of UTF-8, compared exactly, a trailing newline included. An empty greeting, or one over
        1024 bytes, fails before any request, in check mode too.
      - The cluster cannot set the factory greeting back once it is changed (G-FTP-GREETING).
    type: str
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
  - "API: GET and PATCH /v0/ftp/settings, the PATCH with the If-Match of the read; GET /v1/users/ in check mode when
    O(anonymous_user) changes, so a check run then needs the privilege to read local users."
  - The cluster accepts no empty greeting and has no reset, so the factory greeting cannot be set back once changed
    (G-FTP-GREETING).
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Serve FTP over TLS only, with users confined by chroot
  # A second run changes nothing.
  qumulo.cluster.ftp_settings:
    enabled: true
    allow_unencrypted_connections: false
    chroot_users: true

- name: Let anonymous logins act as the local user ftp-guest
  # The cluster_baseline role makes the users of qumulo_local_users before it applies the FTP settings. The cluster
  # finds the name in any case and returns its own spelling, so local:FTP-GUEST is no change against ftp-guest.
  block:
    - name: Create the local user ftp-guest
      qumulo.cluster.local_user:
        name: ftp-guest
        primary_group: Guests
        password: "{{ vault_ftp_guest_pw }}"

    - name: Let anonymous logins act as ftp-guest
      qumulo.cluster.ftp_settings:
        anonymous_user: local:ftp-guest

- name: Turn anonymous logins off
  # Also use this when the local user was deleted: the cluster then names a user with an empty name, which the module
  # reads as local:, and "" replaces it.
  qumulo.cluster.ftp_settings:
    anonymous_user: ""

- name: Greet clients with the site's notice
  # The greeting is compared exactly, so the newline a block scalar ends with is part of it. The cluster cannot set the
  # factory greeting back once this runs; take a capture of the cluster first, whose qumulo_ftp_settings.greeting
  # holds the text to go back to.
  qumulo.cluster.ftp_settings:
    greeting: |
      Authorized use only.

- name: Put back the greeting a capture of the cluster recorded
  # A capture taken before the greeting changed holds the factory text in qumulo_ftp_settings.greeting; sending it back
  # is the only way to restore it.
  qumulo.cluster.ftp_settings:
    greeting: "{{ qumulo_ftp_settings.greeting }}"

- name: Let clients use wildcards in listings and run FXP transfers
  qumulo.cluster.ftp_settings:
    expand_wildcards: true
    check_remote_host: false

- name: Preview turning the FTP server off
  qumulo.cluster.ftp_settings:
    enabled: false
  check_mode: true
  diff: true
  register: ftp_preview

- name: Show whether the FTP server would change
  ansible.builtin.debug:
    msg: "enabled would go from {{ ftp_preview.diff.before.enabled }} to {{ ftp_preview.diff.after.enabled }}"
  when: ftp_preview.changed

- name: Turn the FTP server off
  qumulo.cluster.ftp_settings:
    enabled: false
"""

RETURN = r"""
ftp_settings:
  description:
    - Every setting after the task, with the option names above.
    - In check mode they are the settings the task would leave.
  returned: always
  type: dict
  contains:
    enabled:
      description: Whether the cluster allows FTP connections.
      type: bool
    check_remote_host:
      description: Whether all the connections of a session must come from the same host.
      type: bool
    log_operations:
      description: Whether the cluster logs FTP operations.
      type: bool
    chroot_users:
      description: Whether the cluster confines FTP users with C(chroot).
      type: bool
    allow_unencrypted_connections:
      description: Whether the cluster accepts connections that are not encrypted.
      type: bool
    expand_wildcards:
      description: Whether C(LIST) and C(NLST) expand wildcards.
      type: bool
    anonymous_user:
      description:
        - The local user anonymous logins act as, as V(local:<name>) in the cluster's spelling, V("") for none, and
          V(local:) for a user since deleted. Any other identity type, which the cluster refuses to store, reads as
          C(<id_type in lower case>:<id_value>), such as V(local_group:Users).
      type: str
    greeting:
      description: The text sent with the C(220) reply to a new connection.
      type: str
  sample:
    enabled: true
    check_remote_host: true
    log_operations: true
    chroot_users: true
    allow_unencrypted_connections: false
    expand_wildcards: false
    anonymous_user: local:ftp-guest
    greeting: "Authorized use only.\n"
notes:
  description:
    - What the module noticed, such as, in check mode, an anonymous user the cluster does not hold yet, or a write it
      confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["local user 'ftp-guest' does not exist yet; an earlier task must create it"]
diff:
  description: The settings before and after the task, with the option names above.
  returned: when the task runs with diff mode on
  type: dict
  contains:
    before:
      description: The settings before the task.
      type: dict
    after:
      description: The settings after the task.
      type: dict
maybe_applied:
  description:
    - True when a write was sent and the read-back did not confirm it. The module fails with C(changed) true.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options the read-back after the write did not return as written.
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {chroot_users: true}
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {chroot_users: false}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.ftp import FtpSettings

ARGUMENT_SPEC = {
    "enabled": {"type": "bool"},
    "check_remote_host": {"type": "bool"},
    "log_operations": {"type": "bool"},
    "chroot_users": {"type": "bool"},
    "allow_unencrypted_connections": {"type": "bool"},
    "expand_wildcards": {"type": "bool"},
    "anonymous_user": {"type": "str"},
    "greeting": {"type": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), FtpSettings)


if __name__ == "__main__":
    main()
