#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: nfs_settings
short_description: Manage a Qumulo cluster's NFS server settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster-wide NFS server settings, or with O(tenant) one tenant's override of them. Options left unset
    keep the cluster's values.
  - Only the settings that differ are written, in one PATCH. With O(tenant), a new override is created with one PUT,
    and O(state=inherit) deletes the override.
options:
  v4_enabled:
    description:
      - Serve NFSv4.1 as well as NFSv3.
      - The cluster refuses to turn NFSv4.1 on while one export's path, the path clients mount
        (O(qumulo.cluster.nfs_export#module:export_path)), is inside the path of another export of the same tenant, as
        C(/data/projects) is inside C(/data). Only export paths count, not the directories the exports serve, so
        exports at C(/a) and C(/b) may serve C(/) and C(/x). Export paths of different tenants are not compared, so one
        tenant's export at C(/) and another's at C(/home) do not count. The export at C(/) that a newly formed cluster
        has contains every other export of its tenant.
      - When the task turns NFSv4.1 on for the global settings, the module looks for such a pair first, in check mode
        too, in every tenant, one whose override keeps NFSv4.1 off included, as the cluster's own check does, and fails
        naming the first pair it finds. With O(tenant), the module looks only at that tenant's exports, as O(tenant)
        describes.
    type: bool
  krb5_enabled:
    description: Accept Kerberos authentication (C(krb5)).
    type: bool
  krb5i_enabled:
    description: Accept Kerberos with integrity checking (C(krb5i)).
    type: bool
  krb5p_enabled:
    description: Accept Kerberos with privacy (C(krb5p)).
    type: bool
  auth_sys_enabled:
    description: Accept C(AUTH_SYS) authentication.
    type: bool
  idmap_domain:
    description: The NFSv4 ID-mapping domain. An empty string clears it.
    type: str
  tenant:
    description:
      - The tenant whose override of the NFS server settings the task manages, by name, compared exactly. Unset, the
        task manages the global settings.
      - With a tenant, the settings options describe that tenant's override. A new override is created with a PUT
        whose body is the global settings document as the cluster returns it, with the declared options over it, so a
        setting that this module does not model keeps its global value. An existing override keeps the values of the
        options left unset.
      - Turning O(v4_enabled) on in the override, which includes creating an override that has it on, first looks for
        two of the tenant's export paths where one is inside the other, in check mode too. The task fails naming the
        pair. O(state=inherit) looks the same way when the global settings have NFSv4.1 on and the override has it
        off (QUIRK-114). Only this tenant's exports are looked at, and none for a tenant that an earlier task creates,
        which holds no export yet.
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
      - A tenant has no override only when the cluster answers api_nfs_tenant_settings_do_not_exist_error; any other
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
  - "API: without O(tenant), GET and PATCH /v2/nfs/settings, and GET /v3/nfs/exports/ before turning NFSv4.1 on. With
    O(tenant): GET /v1/multitenancy/tenants/, which needs PRIVILEGE_TENANT_READ; GET, PATCH (If-Match) and DELETE
    (If-Match) /v1/multitenancy/nfs/settings/{tenant_id}; PUT to the same route, without If-Match, to create the
    override, with the global settings document as the cluster returns it and the task's options over it (QUIRK-113,
    QUIRK-115); GET /v2/nfs/settings for that document, and before an inherit; and GET /v3/nfs/exports/ for the
    nesting check of O(tenant)."
  - The global settings route returns no ETag, so a change another client makes between the module's read and its write
    is overwritten. The PATCH of a tenant's override carries the If-Match of its read.
  - O(tenant) resolves through the multitenancy REST API, which Qumulo marks as preview; a later Qumulo Core release
    may change it.
  - The Kerberos flavours work only once the cluster is joined to Active Directory, or holds a standalone KDC's keytab
    (M(qumulo.cluster.kerberos_keytab)), and its DNS and time agree with the KDC's.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Serve NFSv4.1 with Kerberos privacy only
  # The cluster refuses NFSv4.1 while one export's path is inside another's in the same tenant, and the factory export
  # of / contains every other export of its tenant. Remove or replace it first with qumulo.cluster.nfs_export.
  qumulo.cluster.nfs_settings:
    v4_enabled: true
    auth_sys_enabled: false
    krb5_enabled: false
    krb5i_enabled: false
    krb5p_enabled: true
    idmap_domain: corp.example.com

- name: Turn NFSv4.1 on after replacing the factory export of / with exports that do not nest
  # The module names a pair of nested export paths and fails before it writes. Remove one of the pair, or give the
  # inner one a path outside the outer one.
  block:
    - name: Export the project tree at its own path
      # While NFSv4.1 is off, the cluster accepts this export inside the export of /.
      qumulo.cluster.nfs_export:
        export_path: /projects
        fs_path: /data/projects
        restrictions:
          - host_restrictions: [10.20.0.0/16]

    - name: Remove the factory export of /
      qumulo.cluster.nfs_export:
        export_path: /
        state: absent

    - name: Serve NFSv4.1
      qumulo.cluster.nfs_settings:
        v4_enabled: true

- name: Accept Kerberos with integrity checking as well
  qumulo.cluster.nfs_settings:
    krb5i_enabled: true

- name: Set the NFSv4 ID-mapping domain
  qumulo.cluster.nfs_settings:
    idmap_domain: corp.example.com

- name: Preview turning NFSv4.1 on
  qumulo.cluster.nfs_settings:
    v4_enabled: true
  check_mode: true
  diff: true
  register: nfs_preview

- name: Show the settings that would change
  ansible.builtin.debug:
    msg: "{{ nfs_preview.diff.before }} -> {{ nfs_preview.diff.after }}"
  when: nfs_preview.changed

- name: Go back to NFSv3 with AUTH_SYS, and clear the ID-mapping domain
  qumulo.cluster.nfs_settings:
    v4_enabled: false
    auth_sys_enabled: true
    idmap_domain: ""

- name: Preview turning NFSv4.1 on for the eng tenant only
  # The new override holds the global settings as the cluster has them now, with v4_enabled changed. The diff shows {}
  # for before, since the tenant has no override yet, and the full override for after. The task fails, in check mode
  # too, when two of the eng tenant's export paths nest, and names them. The other tenants' exports are not looked at.
  qumulo.cluster.nfs_settings:
    tenant: eng
    v4_enabled: true
  check_mode: true
  diff: true
  register: eng_nfs_preview

- name: Show the override the eng tenant would get
  ansible.builtin.debug:
    var: eng_nfs_preview.diff.after
  when: eng_nfs_preview.changed

- name: Serve NFSv4.1 to the eng tenant only
  # The first run creates the override, from the global settings with v4_enabled changed. A second run changes nothing.
  qumulo.cluster.nfs_settings:
    tenant: eng
    v4_enabled: true

- name: Let the eng tenant use the global NFS settings again
  # Removes the eng tenant's override. When the global settings have NFSv4.1 on and the override has it off, the task
  # first looks for nested export paths in the eng tenant and fails naming them. When the tenant has no override, the
  # task changes nothing.
  qumulo.cluster.nfs_settings:
    tenant: eng
    state: inherit
"""

RETURN = r"""
nfs_settings:
  description:
    - The settings after the task, with the option names above.
    - In check mode they are the settings the task would leave.
    - With O(tenant), they are the tenant's override, with every setting, and an empty dict after O(state=inherit).
  returned: always
  type: dict
  sample:
    v4_enabled: false
    krb5_enabled: true
    krb5i_enabled: true
    krb5p_enabled: true
    auth_sys_enabled: true
    idmap_domain: ""
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
  sample: {idmap_domain: corp.example.com}
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {idmap_domain: ''}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import NfsSettings
from ansible_collections.qumulo.cluster.plugins.module_utils.overrides import NfsOverride

ARGUMENT_SPEC = {
    "v4_enabled": {"type": "bool"},
    "krb5_enabled": {"type": "bool"},
    "krb5i_enabled": {"type": "bool"},
    "krb5p_enabled": {"type": "bool"},
    "auth_sys_enabled": {"type": "bool"},
    "idmap_domain": {"type": "str"},
    "tenant": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "inherit"], "default": "present"},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, required_if=[["state", "inherit", ["tenant"]]])
    if module.params["tenant"] is None:
        run_resource(module, NfsSettings)
    else:
        run_resource(module, NfsOverride)


if __name__ == "__main__":
    main()
