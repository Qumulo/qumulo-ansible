#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: smb_settings
short_description: Manage a Qumulo cluster's SMB server settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster-wide SMB server settings, or with O(tenant) one tenant's override of them. Options left unset
    keep the cluster's values.
  - Only the settings that differ are written, in one PATCH. With O(tenant), a new override is created with one PUT,
    and O(state=inherit) deletes the override.
options:
  session_encryption:
    description: Whether SMB sessions are encrypted.
    type: str
    choices: [NONE, PREFERRED, REQUIRED]
  supported_dialects:
    description:
      - The SMB dialects the cluster offers, compared as a set.
      - An empty list turns the SMB server off.
    type: list
    elements: str
    choices: [SMB2_DIALECT_2_002, SMB2_DIALECT_2_1, SMB2_DIALECT_3_0, SMB2_DIALECT_3_11]
  hide_shares_from_unauthorized_users:
    description: Hide shares from users who have no access to them.
    type: bool
  hide_shares_from_unauthorized_hosts:
    description: Hide shares from hosts that have no access to them.
    type: bool
  snapshot_directory_mode:
    description: How the C(.snapshot) directory appears to SMB clients.
    type: str
    choices: [VISIBLE, HIDDEN, DISABLED]
  bypass_traverse_checking:
    description: Let users reach a directory without the right to traverse its parents.
    type: bool
  signing_required:
    description: Require SMB clients to sign their messages.
    type: bool
  tenant:
    description:
      - The tenant whose override of the SMB server settings the task manages, by name, compared exactly. Unset, the
        task manages the global settings.
      - With a tenant, the settings options describe that tenant's override. A new override is created with a PUT
        whose body is the global settings document as the cluster returns it, with the declared options over it, so a
        setting that this module does not model keeps its global value. An existing override keeps the values of the
        options left unset.
      - Outside check mode, a name that no tenant has fails, and the message lists the tenants. In check mode such a
        name is taken as a tenant an earlier task creates, so the task reports a change with a note in RV(notes). With
        O(state=inherit) such a name is satisfied, and the task changes nothing.
      - Needs the tenant list, which needs PRIVILEGE_TENANT_READ.
    type: str
  state:
    description:
      - V(present) makes sure the settings are as declared. With O(tenant) it creates the tenant's override when it has
        none, even with no settings declared, in which case the override holds the global settings' current values, and
        otherwise changes only the settings that differ.
      - V(inherit) removes the tenant's override, so that the tenant uses the global settings. It needs O(tenant), the
        settings options are not used with it, and it changes nothing when the tenant has no override.
      - A tenant has no override only when the cluster answers api_smb_tenant_settings_do_not_exist_error; any other
        error answer to the read of the override fails the task (QUIRK-112).
    type: str
    choices: [present, inherit]
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
  - "API: without O(tenant), GET and PATCH /v1/smb/settings. With O(tenant): GET /v1/multitenancy/tenants/, which needs
    PRIVILEGE_TENANT_READ; GET, PATCH (If-Match) and DELETE (If-Match) /v1/multitenancy/smb/settings/{tenant_id}; PUT
    to the same route, without If-Match, to create the override, with the global settings document as the cluster
    returns it and the task's options over it (QUIRK-113); and GET /v1/smb/settings for that document."
  - The PATCH of the global settings carries no If-Match, so a change another client makes between the module's read and
    its write is overwritten (QUIRK-001). The PATCH of a tenant's override carries the If-Match of its read.
  - O(tenant) resolves through the multitenancy REST API, which Qumulo marks as preview; a later Qumulo Core release
    may change it.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Require signing and offer only SMB 3
  qumulo.cluster.smb_settings:
    signing_required: true
    supported_dialects: [SMB2_DIALECT_3_0, SMB2_DIALECT_3_11]

- name: Encrypt sessions with clients that support it
  qumulo.cluster.smb_settings:
    session_encryption: PREFERRED

- name: Refuse sessions that do not encrypt
  qumulo.cluster.smb_settings:
    session_encryption: REQUIRED

- name: Hide shares from users and hosts that have no access to them
  qumulo.cluster.smb_settings:
    hide_shares_from_unauthorized_users: true
    hide_shares_from_unauthorized_hosts: true

- name: Preview hiding the .snapshot directory from SMB clients
  qumulo.cluster.smb_settings:
    snapshot_directory_mode: HIDDEN
  check_mode: true
  diff: true
  register: smb_preview

- name: Show how the .snapshot directory appears now
  ansible.builtin.debug:
    msg: "snapshot_directory_mode is {{ smb_preview.diff.before.snapshot_directory_mode }} and would become HIDDEN"
  when: smb_preview.changed

- name: Stop requiring signing, and offer SMB 2.1 again
  qumulo.cluster.smb_settings:
    signing_required: false
    supported_dialects: [SMB2_DIALECT_2_1, SMB2_DIALECT_3_0, SMB2_DIALECT_3_11]

- name: Turn the SMB server off
  qumulo.cluster.smb_settings:
    supported_dialects: []

- name: Preview giving the eng tenant its own SMB settings that require encryption
  # The new override holds the global settings as the cluster has them now, with session_encryption changed. The diff
  # shows {} for before, since the tenant has no override yet, and the full override for after. A tenant the cluster
  # lacks reports the same change in check mode.
  qumulo.cluster.smb_settings:
    tenant: eng
    session_encryption: REQUIRED
  check_mode: true
  diff: true
  register: eng_smb_preview

- name: Show the override the eng tenant would get
  ansible.builtin.debug:
    var: eng_smb_preview.diff.after
  when: eng_smb_preview.changed

- name: Require encrypted sessions for the eng tenant only
  # The first run creates the override, from the global settings with session_encryption changed. A second run changes
  # nothing. The global settings and the other tenants are left alone.
  qumulo.cluster.smb_settings:
    tenant: eng
    session_encryption: REQUIRED

- name: Let the eng tenant use the global SMB settings again
  # Removes the eng tenant's override. When the tenant has none, the task changes nothing.
  qumulo.cluster.smb_settings:
    tenant: eng
    state: inherit
"""

RETURN = r"""
smb_settings:
  description:
    - The settings after the task, with the option names above.
    - In check mode they are the settings the task would leave.
    - With O(tenant), they are the tenant's override, with every setting, and an empty dict after O(state=inherit).
  returned: always
  type: dict
  sample:
    session_encryption: NONE
    supported_dialects: [SMB2_DIALECT_3_0, SMB2_DIALECT_3_11]
    hide_shares_from_unauthorized_users: false
    hide_shares_from_unauthorized_hosts: false
    snapshot_directory_mode: VISIBLE
    bypass_traverse_checking: false
    signing_required: true
notes:
  description:
    - What the module noticed, such as, with O(tenant) in check mode, a tenant that does not exist yet, or a write it
      confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["tenant 'finance' does not exist yet; an earlier task is expected to create it"]
diff:
  description:
    - The settings before and after the task, with the option names above.
    - With O(tenant), each side is the tenant's override with every setting, and V({}) for a side where the tenant has
      none. In check mode, the after of a create is the global settings the cluster holds now with the declared options
      changed.
  returned: when the task runs with diff mode on
  type: dict
  contains:
    before:
      description: The settings before the task.
      type: dict
    after:
      description: The settings after the task.
      type: dict
    before_header:
      description: "The label of the before side: C(tenant) and the tenant's name."
      returned: with O(tenant)
      type: str
      sample: tenant eng
    after_header:
      description: "The label of the after side: C(tenant) and the tenant's name."
      returned: with O(tenant)
      type: str
      sample: tenant eng
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
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.overrides import SmbOverride
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SmbSettings

DIALECTS = ["SMB2_DIALECT_2_002", "SMB2_DIALECT_2_1", "SMB2_DIALECT_3_0", "SMB2_DIALECT_3_11"]
ARGUMENT_SPEC = {
    "session_encryption": {"type": "str", "choices": ["NONE", "PREFERRED", "REQUIRED"]},
    "supported_dialects": {"type": "list", "elements": "str", "choices": DIALECTS},
    "hide_shares_from_unauthorized_users": {"type": "bool"},
    "hide_shares_from_unauthorized_hosts": {"type": "bool"},
    "snapshot_directory_mode": {"type": "str", "choices": ["VISIBLE", "HIDDEN", "DISABLED"]},
    "bypass_traverse_checking": {"type": "bool"},
    "signing_required": {"type": "bool"},
    "tenant": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "inherit"], "default": "present"},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, required_if=[["state", "inherit", ["tenant"]]])
    if module.params["tenant"] is None:
        run_resource(module, SmbSettings)
    else:
        run_resource(module, SmbOverride)


if __name__ == "__main__":
    main()
