#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: nfs_export
short_description: Manage an NFS export on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or removes one NFS export. The export is found by its export path, and by its tenant when
    O(tenant) or O(tenant_id) is set, never by its id. With O(tenants) the module does this once for each tenant named.
  - Options left unset are not managed, so an existing export keeps the values it has.
  - A newly formed cluster has an export at C(/).
options:
  export_path:
    description:
      - The path clients mount, such as V(/projects). It is the key. Only V(/) may end with a slash, as the cluster requires.
      - While NFSv4.1 is on (O(qumulo.cluster.nfs_settings#module:v4_enabled)), the cluster refuses an export whose
        path is inside the path of another export of the same tenant, or contains one. Export paths of different
        tenants are not compared.
    type: str
    required: true
  tenant:
    description:
      - The tenant the export belongs to, by name, compared exactly. The export is matched within this tenant only, and
        its tenant never changes.
      - Needed only when the export path exists in more than one tenant. Without it the task then fails, and the
        message names the tenants and says to set O(tenant).
      - A create makes the export in this tenant. Without a tenant, the create names none, and the cluster chooses one.
      - Outside check mode, a name that no tenant has fails, and the message lists the tenants. In check mode such a
        name is taken as a tenant an earlier task creates, so a create is reported as a change with a note in RV(notes),
        and the result shows the export with that name and a V(null) C(tenant_id). With O(state=absent) such a name is
        satisfied, and the task changes nothing.
      - Needs the tenant list, which needs PRIVILEGE_TENANT_READ; a failure to read it fails the task.
    type: str
  tenants:
    description:
      - Tenants, by name and compared exactly. The module runs once for each name, as it would with O(tenant) set to
        that name, and reads the list of exports once for all of them, again after each write. It writes only to a
        tenant whose export differs from the one the task describes.
      - RV(nfs_export) and the diff are keyed by tenant name. A side where the export is missing, or where the tenant
        is not on the cluster and O(state=absent), is V({}). A name the cluster lacks is handled as for O(tenant).
      - Names only. V(all), an empty list and a name that appears twice are refused.
      - The names are taken in the order given. Before the first write every name is looked up, and outside check
        mode a name the cluster lacks fails the task. In check mode too, a tenant that does not hold the export fails it
        then while O(fs_path) or O(restrictions) is unset, a tenant an earlier task creates included.
      - Apart from that lookup, whose message lists the cluster's tenants, a failure for one tenant starts its message
        with the tenant's name in quotes, as in C(tenant 'eng':). Outside check mode, when earlier tenants were already
        changed, the message ends with C(; the task had already changed tenant 'eng'), or C(tenants 'a' and 'b'), and
        the result has C(changed) true.
      - Mutually exclusive with O(tenant) and with O(tenant_id).
    type: list
    elements: str
  tenant_id:
    description:
      - Deprecated, and removed in release 0.3.0 of this collection; use O(tenant). The id of the tenant the export
        belongs to.
      - Accepted beside O(tenant) when both name one tenant, so an export read with M(qumulo.cluster.nfs_info) can be
        given back, and refused when they differ, naming both.
      - With O(state=absent), when no tenant has the name in O(tenant) and none has this id, the task changes nothing;
        when this id belongs to a tenant, the task is refused.
      - Used alone, it chooses the export and goes in the body of a create. It is refused when the tenant list can be
        read and no tenant holds the id, with the message C(tenant_id <n> is no tenant on the cluster; name its tenant
        with tenant), and with O(state=absent) the task then changes nothing. Where the list is not offered, or the
        user may not read it (HTTP 403), the id is used without that check.
      - Mutually exclusive with O(tenants).
    type: int
  state:
    description:
      - Whether the export exists. Removing an export leaves its directory and data in place.
      - Removing an export ends the mounts of clients that use it.
    type: str
    choices: [present, absent]
    default: present
  fs_path:
    description: Absolute path of the directory the export serves. A trailing slash is ignored. Required to create an export.
    type: str
  create_fs_path:
    description:
      - Create O(fs_path) when the export is created and the directory does not exist.
      - The cluster makes the new directories readable by everyone, and the last one writable by everyone.
    type: bool
    default: false
  description:
    description: A description of the export. An export created without one gets an empty description.
    type: str
  fields_to_present_as_32_bit:
    description:
      - The fields the export presents to clients as 32-bit values, compared as a set.
      - V(ALL) stands for all three, and V(NONE) for none.
    type: list
    elements: str
    choices: [FILE_IDS, FILE_SIZES, FS_SIZE, ALL, NONE]
  restrictions:
    description:
      - Which hosts may mount the export and how, in order; the first restriction that matches a host decides.
      - Required to create an export; the cluster refuses an export without one.
      - The list replaces every restriction of the export. Replacing it can break the mounts of clients already connected.
    type: list
    elements: dict
    suboptions:
      host_restrictions:
        description:
          - Addresses, CIDR blocks, ranges or host names, compared as written and in order.
          - An empty list matches every host. It is not the same as V(['*']), and the module does not treat them as equal.
        type: list
        elements: str
        default: []
      read_only:
        description: Mount read-only.
        type: bool
        default: false
      require_privileged_port:
        description: Accept only clients that connect from a port below 1024.
        type: bool
        default: false
      required_authentication_mode:
        description: The authentication the clients must use.
        type: str
        choices: [AUTHENTICATION_MODE_NONE, AUTHENTICATION_MODE_KRB5, AUTHENTICATION_MODE_KRB5I, AUTHENTICATION_MODE_KRB5P]
        default: AUTHENTICATION_MODE_NONE
      user_mapping:
        description: Which users are mapped to O(restrictions[].map_to_user) - none, root only, or all.
        type: str
        choices: [NFS_MAP_NONE, NFS_MAP_ROOT, NFS_MAP_ALL]
        default: NFS_MAP_NONE
      map_to_user:
        description:
          - The user mapped users become, as V(local:<name>) for a local user or V(uid:<n>) for an NFS uid.
          - Required unless O(restrictions[].user_mapping=NFS_MAP_NONE), and refused with it.
          - A uid with leading zeros, such as V(uid:0065), is written as the decimal V(65) (QUIRK-013).
        type: str
      map_to_group:
        description:
          - The group of mapped users, as V(gid:<n>). Required with a V(uid:<n>) user, and refused with a local user.
          - Leading zeros are dropped, as for O(restrictions[].map_to_user).
        type: str
      require_tls:
        description:
          - Accept only clients that use TLS.
          - Needs Qumulo Core 7.11.0 or later. The module fails on an older release, and on a release whose API does
            not take the field.
          - Left unset, it is not sent, and the cluster stores V(false) whenever the restrictions are written.
        type: bool
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
  - "API: GET and POST /v3/nfs/exports/; GET, PATCH (If-Match) and DELETE /v3/nfs/exports/{id}; GET /v1/version, and
    GET /openapi.json when it is served, for O(restrictions[].require_tls); GET /v1/multitenancy/tenants/ on every
    run, before the write in a run without O(tenant) or O(tenants). With O(tenant) or O(tenants) that read needs
    PRIVILEGE_TENANT_READ and any failure of it fails the task; without them, a cluster that does not offer the list
    and a user who may not read it (HTTP 403) get V(null) for C(tenant) in the results. The PATCH never carries the
    tenant."
  - Qumulo marks the C(/v3/nfs/exports/) routes this module uses as preview in Qumulo Core 7.10.3 and 7.11.0; a later
    Qumulo Core release may change them.
  - O(tenant) and O(tenants) resolve through the multitenancy REST API, which Qumulo marks as preview; a later Qumulo
    Core release may change it.
  - When the local user a restriction maps to is deleted, the cluster reads the mapping back empty and refuses every
    later change to the export until its restrictions are replaced. A task that would change anything else then fails,
    saying so, unless it sets O(restrictions); a task that changes nothing still succeeds (G-MAPTO).
  - "Registers: G-MAPTO, QUIRK-013."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Export the project tree read-write to the build network, squashing root, and read-only to everyone else
  qumulo.cluster.nfs_export:
    export_path: /projects
    fs_path: /data/projects
    description: Project data
    restrictions:
      - host_restrictions: [10.20.0.0/16]
        user_mapping: NFS_MAP_ROOT
        map_to_user: uid:65534
        map_to_group: gid:65534
      - host_restrictions: []
        read_only: true

- name: Export a directory that does not exist yet, with the mode and owner it should have
  # A directory the cluster creates for create_fs_path is readable by everyone and its last level is writable by
  # everyone, so make the directory first, with a mode and an owner, and export it afterwards.
  block:
    - name: Make the scratch directory
      qumulo.cluster.directory:
        path: /data/scratch
        mode: "0770"
        # The uid and gid the build hosts run as, which is how NFS AUTH_SYS clients reach the directory.
        owner: uid:2000
        group: gid:2000

    - name: Export the scratch directory to the build network
      qumulo.cluster.nfs_export:
        export_path: /scratch
        fs_path: /data/scratch
        restrictions:
          - host_restrictions: [10.20.0.0/16]

- name: Export a directory and let the cluster create it, readable by everyone and writable by everyone at its last level
  qumulo.cluster.nfs_export:
    export_path: /incoming
    fs_path: /data/incoming
    create_fs_path: true
    restrictions:
      - host_restrictions: [10.20.0.0/16]

- name: Map every user of the render farm to a local service account
  qumulo.cluster.nfs_export:
    export_path: /renders
    fs_path: /data/renders
    restrictions:
      - host_restrictions: [10.30.0.0/24]
        user_mapping: NFS_MAP_ALL
        map_to_user: local:svc_render

- name: Require Kerberos privacy, TLS and a privileged port from every client of the finance export
  # require_tls needs Qumulo Core 7.11.0 or later.
  qumulo.cluster.nfs_export:
    export_path: /finance
    fs_path: /data/finance
    restrictions:
      - host_restrictions: []
        required_authentication_mode: AUTHENTICATION_MODE_KRB5P
        require_privileged_port: true
        require_tls: true

- name: Present file ids as 32-bit values to the legacy clients of the archive export
  qumulo.cluster.nfs_export:
    export_path: /archive
    fs_path: /data/archive
    fields_to_present_as_32_bit: [FILE_IDS]
    restrictions:
      - host_restrictions: [10.40.0.0/24]
        read_only: true

- name: Describe the project export, leaving its restrictions as they are
  qumulo.cluster.nfs_export:
    export_path: /projects
    description: Project data, read-write for builds

- name: Describe the project export in the eng tenant
  # The export is found by its path within the tenant, so an export of the same path in another tenant is left alone.
  # A tenant the cluster lacks fails the task, in check mode too, since the task does not set fs_path and restrictions
  # to create the export there.
  qumulo.cluster.nfs_export:
    export_path: /projects
    tenant: eng
    description: Project data of the eng tenant

- name: Export the project tree in the eng and finance tenants
  # One run for both tenants. A tenant that already holds the export is written only when it differs, and the result is
  # keyed by tenant name. A tenant that holds no export at this path needs fs_path and restrictions, as here.
  qumulo.cluster.nfs_export:
    export_path: /projects
    tenants: [eng, finance]
    fs_path: /data/projects
    restrictions:
      - host_restrictions: [10.20.0.0/16]

- name: Preview giving the project export one description in two tenants
  # Both tenants must already hold the export: a tenant without it needs fs_path and restrictions to create it. Each
  # side of the diff maps a tenant name to that tenant's export.
  qumulo.cluster.nfs_export:
    export_path: /projects
    tenants: [eng, finance]
    description: Project data
  check_mode: true
  diff: true
  register: projects_by_tenant

- name: Show the description each tenant's export would change from
  ansible.builtin.debug:
    msg: "{{ item.key }}: {{ item.value.description | default('no export') }}"
  loop: "{{ projects_by_tenant.diff.before | dict2items }}"
  loop_control:
    label: "{{ item.key }}"

- name: Remove the scratch export from the eng and finance tenants
  # A tenant the cluster lacks, or one without the export, is already as the task asks and is left alone.
  qumulo.cluster.nfs_export:
    export_path: /scratch
    tenants: [eng, finance]
    state: absent

- name: Stop exporting the project tree to other hosts, and give the lab network read-only access
  # The list replaces every restriction. Clients already mounted may lose their mounts.
  qumulo.cluster.nfs_export:
    export_path: /projects
    restrictions:
      - host_restrictions: [10.20.0.0/16]
        user_mapping: NFS_MAP_ROOT
        map_to_user: uid:65534
        map_to_group: gid:65534
      - host_restrictions: [192.168.50.0/24]
        read_only: true
        require_privileged_port: true

- name: Preview replacing the project export's restrictions
  qumulo.cluster.nfs_export:
    export_path: /projects
    restrictions:
      - host_restrictions: [10.20.0.0/16]
        user_mapping: NFS_MAP_ROOT
        map_to_user: uid:65534
        map_to_group: gid:65534
      - host_restrictions: [192.168.50.0/24]
        read_only: true
        require_privileged_port: true
  check_mode: true
  diff: true
  register: projects_export

- name: Stop before a change that could break mounted clients
  ansible.builtin.assert:
    that: not projects_export.changed
    fail_msg: >-
      /projects would change from {{ projects_export.diff.before.restrictions | default('nothing') }}
      to {{ projects_export.nfs_export.restrictions }}

- name: Remove the export of the scratch directory, keeping the directory and its data
  qumulo.cluster.nfs_export:
    export_path: /scratch
    state: absent

- name: Remove the factory export of / so that NFSv4.1 can be turned on
  # Every newly formed cluster has an export at / in its first tenant. Every other export path of that tenant is inside
  # it, so NFSv4.1 cannot be turned on while it exists. Clients that mount / lose their mounts, so export what they
  # need before removing it. Add tenant when another tenant has an export at / too.
  # Turn NFSv4.1 on afterwards with qumulo.cluster.nfs_settings.
  qumulo.cluster.nfs_export:
    export_path: /
    state: absent
"""

RETURN = r"""
nfs_export:
  description:
    - The export after the task, with the option names above; an empty dict after O(state=absent).
    - In check mode, and when nothing is written, it is the export as read with the task's options laid over it.
      A create in check mode returns only the options the task set.
    - Each export carries C(tenant), its tenant's name, before C(tenant_id), which is deprecated with O(tenant_id).
      C(tenant) is V(null) when the tenant list does not name the id, and, in a task without O(tenant) or O(tenants),
      when the cluster does not offer the list or the user may not read it (HTTP 403). In check mode a tenant that an
      earlier task creates shows its name there, with a V(null) C(tenant_id).
    - With O(tenants) it is a dict keyed by tenant name, each value the export of that tenant as above, and V({}) for
      a tenant that has none.
    - O(restrictions[].require_tls) appears in a restriction when the cluster reports it. When the task sets
      O(restrictions) and nothing is written, as in check mode, it appears only where the task set it.
  returned: always
  type: dict
  sample:
    export_path: /projects
    tenant: Default
    tenant_id: 1
    fs_path: /data/projects
    description: Project data
    fields_to_present_as_32_bit: []
    restrictions:
      - host_restrictions: [10.20.0.0/16]
        read_only: false
        require_privileged_port: false
        required_authentication_mode: AUTHENTICATION_MODE_NONE
        user_mapping: NFS_MAP_ROOT
        map_to_user: uid:65534
        map_to_group: gid:65534
notes:
  description:
    - What the module noticed, such as a tenant that does not exist yet in check mode, or a write it confirmed by
      reading back after the connection was lost.
    - "With O(tenants), each note starts with the name of the tenant it is about, as in
      C(eng: write reconciled after a lost connection)."
  returned: when there is something to note
  type: list
  elements: str
  sample: ["tenant 'finance' does not exist yet; an earlier task is expected to create it"]
diff:
  description:
    - The export before and after the task, with the option names above, and C(tenant) before C(tenant_id).
    - With O(tenants), C(before) and C(after) each map a tenant's name to the export, V({}) where it is missing.
  returned: when the task runs with diff mode on
  type: dict
  contains:
    before:
      description:
        - The export before the task; an empty dict when it did not exist.
        - With O(tenants), a dict keyed by tenant name.
      type: dict
    after:
      description:
        - The export after the task; an empty dict after O(state=absent).
        - With O(tenants), a dict keyed by tenant name.
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
  sample: {description: Project tree for the build farm}
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {description: Project tree}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import NfsExport
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy import run_scoped

ARGUMENT_SPEC = {
    "export_path": {"type": "str", "required": True},
    "tenant": {"type": "str"},
    "tenants": {"type": "list", "elements": "str"},
    "tenant_id": {"type": "int", "removed_in_version": "0.3.0", "removed_from_collection": "qumulo.cluster"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "fs_path": {"type": "str"},
    "create_fs_path": {"type": "bool", "default": False},
    "description": {"type": "str"},
    "fields_to_present_as_32_bit": {"type": "list", "elements": "str", "choices": ["FILE_IDS", "FILE_SIZES", "FS_SIZE", "ALL", "NONE"]},
    "restrictions": {
        "type": "list",
        "elements": "dict",
        "options": {
            "host_restrictions": {"type": "list", "elements": "str", "default": []},
            "read_only": {"type": "bool", "default": False},
            "require_privileged_port": {"type": "bool", "default": False},
            "required_authentication_mode": {
                "type": "str",
                "choices": ["AUTHENTICATION_MODE_NONE", "AUTHENTICATION_MODE_KRB5", "AUTHENTICATION_MODE_KRB5I", "AUTHENTICATION_MODE_KRB5P"],
                "default": "AUTHENTICATION_MODE_NONE",
            },
            "user_mapping": {"type": "str", "choices": ["NFS_MAP_NONE", "NFS_MAP_ROOT", "NFS_MAP_ALL"], "default": "NFS_MAP_NONE"},
            "map_to_user": {"type": "str"},
            "map_to_group": {"type": "str"},
            "require_tls": {"type": "bool"},
        },
    },
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, mutually_exclusive=[["tenant", "tenants"], ["tenant_id", "tenants"]])
    run(module, lambda client: run_scoped(module, client, NfsExport))


if __name__ == "__main__":
    main()
