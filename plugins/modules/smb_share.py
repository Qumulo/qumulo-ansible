#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: smb_share
short_description: Manage an SMB share on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or removes one SMB share. The share is found by its name, and by its tenant when O(tenant) or
    O(tenant_id) is set, never by its id. With O(tenants) the module does this once for each tenant named.
  - Options left unset are not managed, so an existing share keeps the values it has.
  - Share names are matched without regard to case, as the cluster matches them.
  - A newly formed cluster has a share named C(Files) on C(/).
options:
  share_name:
    description: The share's name. It is the key, so a different case names the same share and does not rename it.
    type: str
    required: true
  tenant:
    description:
      - The tenant the share belongs to, by name, compared exactly. The share is matched within this tenant only, and
        its tenant never changes.
      - Needed only when a share of this name exists in more than one tenant. Without it the task then fails, and the
        message names the tenants and says to set O(tenant).
      - A create makes the share in this tenant. Without a tenant, the create names none, and the cluster chooses one.
      - Outside check mode, a name that no tenant has fails, and the message lists the tenants. In check mode such a
        name is taken as a tenant an earlier task creates, so a create is reported as a change with a note in RV(notes),
        and the result shows the share with that name and a V(null) C(tenant_id). With O(state=absent) such a name is
        satisfied, and the task changes nothing.
      - Needs the tenant list, which needs PRIVILEGE_TENANT_READ; a failure to read it fails the task.
    type: str
  tenants:
    description:
      - Tenants, by name and compared exactly. The module runs once for each name, as it would with O(tenant) set to
        that name, and reads the list of shares once for all of them, again after each write. It writes only to a
        tenant whose share differs from the one the task describes.
      - RV(smb_share) and the diff are keyed by tenant name. A side where the share is missing, or where the tenant
        is not on the cluster and O(state=absent), is V({}). A name the cluster lacks is handled as for O(tenant).
      - Names only. V(all), an empty list and a name that appears twice are refused.
      - The names are taken in the order given. Before the first write every name is looked up, and outside check
        mode a name the cluster lacks fails the task. In check mode too, a tenant that does not hold the share fails it
        then while O(fs_path) or O(permissions) is unset, a tenant an earlier task creates included.
      - Apart from that lookup, whose message lists the cluster's tenants, a failure for one tenant starts its message
        with the tenant's name in quotes, as in C(tenant 'eng':). Outside check mode, when earlier tenants were already
        changed, the message ends with C(; the task had already changed tenant 'eng'), or C(tenants 'a' and 'b'), and
        the result has C(changed) true.
      - Mutually exclusive with O(tenant) and with O(tenant_id).
    type: list
    elements: str
  tenant_id:
    description:
      - Deprecated, and removed in release 0.3.0 of this collection; use O(tenant). The id of the tenant the share
        belongs to.
      - Accepted beside O(tenant) when both name one tenant, so a share read with M(qumulo.cluster.smb_info) can be
        given back, and refused when they differ, naming both.
      - With O(state=absent), when no tenant has the name in O(tenant) and none has this id, the task changes nothing;
        when this id belongs to a tenant, the task is refused.
      - Used alone, it chooses the share and goes in the body of a create. It is refused when the tenant list can be
        read and no tenant holds the id, with the message C(tenant_id <n> is no tenant on the cluster; name its tenant
        with tenant), and with O(state=absent) the task then changes nothing. Where the list is not offered, or the
        user may not read it (HTTP 403), the id is used without that check.
      - Mutually exclusive with O(tenants).
    type: int
  state:
    description: Whether the share exists. Removing a share leaves its directory and data in place.
    type: str
    choices: [present, absent]
    default: present
  fs_path:
    description:
      - Absolute path of the directory the share serves. A trailing slash is ignored.
      - Required to create a share.
    type: str
  create_fs_path:
    description:
      - Create O(fs_path) when it does not exist.
      - The cluster makes the new directories readable by everyone, and the last one writable by everyone.
      - Sent with every create and every change (QUIRK-007).
    type: bool
    default: false
  description:
    description: A description of the share.
    type: str
  permissions:
    description:
      - The share's access control list, in order. For each right, the first entry that matches the user and names
        the right decides it, so a V(DENIED) entry overrides only the V(ALLOWED) entries after it, and a later entry
        can allow a right the earlier ones did not name. A right no matching entry allows is denied.
      - A user gets a right only when O(network_permissions) allows it too.
      - Required to create a share. An empty list denies everyone.
      - The list replaces the share's whole access control list.
    type: list
    elements: dict
    suboptions:
      type:
        description: Whether the entry allows or denies its rights.
        type: str
        choices: [ALLOWED, DENIED]
        default: ALLOWED
      trustee:
        description:
          - Whom the entry applies to, as a trustee such as V(Everyone), V(local:admin), V(ad:CORP\\alice),
            V(uid:1000) or V(sid:S-1-5-21-1-2-3-1001). A bare name is refused.
          - Authenticated Users is V(ad:NT AUTHORITY\\Authenticated Users), which the cluster resolves joined to a domain or not.
          - Compared by the identity it resolves to. An entry the cluster reads back compares by the identity it gives with
            the entry.
        type: str
        required: true
      rights:
        description:
          - The rights, compared as a set. V(ALL) is V(READ), V(WRITE) and V(CHANGE_PERMISSIONS); V(NONE) is no rights.
          - An V(ALLOWED) entry with V(WRITE) needs V(READ) too, because the cluster refuses write-only access.
        type: list
        elements: str
        choices: [READ, WRITE, CHANGE_PERMISSIONS, ALL, NONE]
        required: true
  network_permissions:
    description:
      - Which client addresses may use the share, and with which rights, in order. For each right, the first entry
        whose O(network_permissions[].address_ranges) match the client's address and that names the right decides it,
        as in O(permissions), so a V(DENIED) entry overrides only the V(ALLOWED) entries after it.
      - A right no matching entry allows is denied, and a client allowed no right at all cannot connect to the share,
        including an address that matches no entry.
      - The list replaces the share's network permissions.
      - A share created without it allows every address. An empty list denies every address.
    type: list
    elements: dict
    suboptions:
      type:
        description: Whether the entry allows or denies its rights.
        type: str
        choices: [ALLOWED, DENIED]
        default: ALLOWED
      address_ranges:
        description: Addresses, CIDR blocks or ranges, compared as written and in order. An empty list matches every address.
        type: list
        elements: str
        default: []
      rights:
        description: The rights, as for O(permissions[].rights).
        type: list
        elements: str
        choices: [READ, WRITE, CHANGE_PERMISSIONS, ALL, NONE]
        required: true
  access_based_enumeration_enabled:
    description: Hide files and directories from users who cannot read them.
    type: bool
  require_encryption:
    description: Require clients to encrypt their traffic to this share.
    type: bool
  default_file_create_mode:
    description:
      - POSIX mode of the files SMB clients create, as an octal string such as V('0644').
      - Quote it. YAML reads an unquoted octal as a decimal number, so a number is refused.
    type: raw
  default_directory_create_mode:
    description:
      - POSIX mode of the directories SMB clients create, as an octal string such as V('0755').
      - Quote it. YAML reads an unquoted octal as a decimal number, so a number is refused.
    type: raw
  expand_fs_path_variables:
    description: Expand C(%U) in O(fs_path) to the connecting user's name.
    type: bool
  offline_files_caching_mode:
    description: The offline-files caching the share offers Windows clients.
    type: str
    choices: [NO_CACHING, MANUAL_CACHING, AUTOMATIC_CACHING]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - A trustee that does not exist yet, because an earlier task creates it or joins its domain, is reported as a change.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET and POST /v3/smb/shares/; GET, PATCH (If-Match) and DELETE /v3/smb/shares/{id}; POST /v1/identity/find;
    GET /v1/multitenancy/tenants/ on every run, before the write in a run without O(tenant) or O(tenants). With
    O(tenant) or O(tenants) that read needs PRIVILEGE_TENANT_READ and any failure of it fails the task; without them,
    a cluster that does not offer the list and a user who may not read it (HTTP 403) get V(null) for C(tenant) in the
    results. The PATCH never carries the tenant."
  - Qumulo marks the C(/v3/smb/shares/) routes this module uses as preview in Qumulo Core 7.10.3 and 7.11.0; a later
    Qumulo Core release may change them.
  - O(tenant) and O(tenants) resolve through the multitenancy REST API, which Qumulo marks as preview; a later Qumulo
    Core release may change it.
  - Rights the cluster stores in a finer form than V(READ), V(WRITE) and V(CHANGE_PERMISSIONS), written by another
    tool, read back as those finer rights, and the module replaces them.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Share the project tree, read-only for everyone and full access for the engineers
  qumulo.cluster.smb_share:
    share_name: projects
    fs_path: /data/projects
    description: Project data
    permissions:
      - trustee: ad:CORP\Engineering
        rights: [ALL]
      - trustee: Everyone
        rights: [READ]

- name: Share a directory that does not exist yet, with the mode and owner it should have
  # A directory the cluster creates for create_fs_path is readable by everyone and its last level is writable by
  # everyone, so make the directory first, with a mode and an owner, and share it afterwards.
  block:
    - name: Make the shared scratch directory
      qumulo.cluster.directory:
        path: /data/scratch
        mode: "0770"
        owner: local:svc_build
        group: ad:CORP\Engineering

    - name: Share the scratch directory with the engineers
      qumulo.cluster.smb_share:
        share_name: scratch
        fs_path: /data/scratch
        permissions:
          - trustee: ad:CORP\Engineering
            rights: [ALL]

- name: Share a directory and let the cluster create it, readable by everyone and writable by everyone at its last level
  qumulo.cluster.smb_share:
    share_name: incoming
    fs_path: /data/incoming
    create_fs_path: true
    permissions:
      - trustee: Everyone
        rights: [READ, WRITE]

- name: Share each user's home directory at /home/<user name>
  # Each user's directory must already exist, with that user as its owner and a mode such as "0700" (make it with
  # qumulo.cluster.directory). The share's ACL does not restrict who reads it; access_based_enumeration_enabled only
  # hides what a user cannot read.
  qumulo.cluster.smb_share:
    share_name: home
    fs_path: /home/%U
    expand_fs_path_variables: true
    access_based_enumeration_enabled: true
    permissions:
      - trustee: Everyone
        rights: [ALL]

- name: Let only the engineers and the contractors into the project share
  # The list replaces the share's whole ACL, so it repeats the entries that stay.
  qumulo.cluster.smb_share:
    share_name: projects
    permissions:
      - trustee: ad:CORP\Engineering
        rights: [ALL]
      - trustee: ad:CORP\Contractors
        rights: [READ]

- name: Deny the interns write access before the broader entry would allow it
  # The first matching entry decides each right, so the DENIED entry comes before the ALLOWED one.
  qumulo.cluster.smb_share:
    share_name: projects
    permissions:
      - type: DENIED
        trustee: ad:CORP\Interns
        rights: [WRITE, CHANGE_PERMISSIONS]
      - trustee: ad:CORP\Engineering
        rights: [ALL]
      - trustee: Everyone
        rights: [READ]

- name: Allow the project share only from the office and VPN networks
  qumulo.cluster.smb_share:
    share_name: projects
    network_permissions:
      - address_ranges: [10.20.0.0/16, 10.99.0.0/24]
        rights: [ALL]

- name: Set the modes of the files and directories SMB clients create in the project share
  qumulo.cluster.smb_share:
    share_name: projects
    default_file_create_mode: "0664"
    default_directory_create_mode: "0775"

- name: Let laptops keep the field-reports share for offline use
  qumulo.cluster.smb_share:
    share_name: field-reports
    offline_files_caching_mode: MANUAL_CACHING

- name: Describe the project share in the eng tenant
  # The share is found by its name within the tenant, so a share of the same name in another tenant is left alone. A
  # tenant the cluster lacks fails the task, in check mode too, since the task does not set fs_path and permissions to
  # create the share there.
  qumulo.cluster.smb_share:
    share_name: projects
    tenant: eng
    description: Project data of the eng tenant

- name: Share the project tree in the eng and finance tenants
  # One run for both tenants. A tenant that already holds the share is written only when it differs, and the result is
  # keyed by tenant name. A tenant that holds no share of this name needs fs_path and permissions, as here.
  qumulo.cluster.smb_share:
    share_name: projects
    tenants: [eng, finance]
    fs_path: /data/projects
    permissions:
      - trustee: Everyone
        rights: [READ]

- name: Preview giving the project share one description in two tenants
  # Both tenants must already hold the share: a tenant without it needs fs_path and permissions to create it. Each side
  # of the diff maps a tenant name to that tenant's share.
  qumulo.cluster.smb_share:
    share_name: projects
    tenants: [eng, finance]
    description: Project data
  check_mode: true
  diff: true
  register: projects_by_tenant

- name: Show the description each tenant's share would change from
  ansible.builtin.debug:
    msg: "{{ item.key }}: {{ item.value.description | default('no share') }}"
  loop: "{{ projects_by_tenant.diff.before | dict2items }}"
  loop_control:
    label: "{{ item.key }}"

- name: Remove the scratch share from the eng and finance tenants
  # A tenant the cluster lacks, or one without the share, is already as the task asks and is left alone.
  qumulo.cluster.smb_share:
    share_name: scratch
    tenants: [eng, finance]
    state: absent

- name: Preview requiring encryption on the project share
  qumulo.cluster.smb_share:
    share_name: projects
    require_encryption: true
  check_mode: true
  diff: true
  register: projects_preview

- name: Show what the share would change from
  ansible.builtin.debug:
    msg: "require_encryption is {{ projects_preview.diff.before.require_encryption }} and would become true"
  when: projects_preview.changed

- name: Remove the scratch share, keeping the directory and its data
  qumulo.cluster.smb_share:
    share_name: scratch
    state: absent

- name: Remove the factory share
  # Every newly formed cluster has a share named Files on /. Its directory and data stay.
  qumulo.cluster.smb_share:
    share_name: Files
    state: absent
"""

RETURN = r"""
smb_share:
  description:
    - The share after the task, with the option names above; an empty dict after O(state=absent).
    - In check mode, and when nothing is written, it is the share as read with the task's options laid over it.
      A create in check mode returns only the options the task set.
    - Each share carries C(tenant), its tenant's name, before C(tenant_id), which is deprecated with O(tenant_id).
      C(tenant) is V(null) when the tenant list does not name the id, and, in a task without O(tenant) or O(tenants),
      when the cluster does not offer the list or the user may not read it (HTTP 403). In check mode a tenant that an
      earlier task creates shows its name there, with a V(null) C(tenant_id).
    - With O(tenants) it is a dict keyed by tenant name, each value the share of that tenant as above, and V({}) for a
      tenant that has none.
    - A permission's trustee that no trustee form names is shown by the name the cluster gives, or failing
      that its domain.
  returned: always
  type: dict
  sample:
    share_name: projects
    tenant: Default
    tenant_id: 1
    fs_path: /data/projects
    description: Project data
    permissions:
      - {type: ALLOWED, trustee: ad:CORP\Engineering, rights: [CHANGE_PERMISSIONS, READ, WRITE]}
      - {type: ALLOWED, trustee: world:Everyone, rights: [READ]}
    network_permissions:
      - {type: ALLOWED, address_ranges: [], rights: [CHANGE_PERMISSIONS, READ, WRITE]}
    access_based_enumeration_enabled: false
    require_encryption: false
    default_file_create_mode: "0644"
    default_directory_create_mode: "0755"
    expand_fs_path_variables: false
    offline_files_caching_mode: NO_CACHING
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
    - The share before and after the task, with the option names above, and C(tenant) before C(tenant_id).
    - With O(tenants), C(before) and C(after) each map a tenant's name to the share, V({}) where it is missing.
  returned: when the task runs with diff mode on
  type: dict
  contains:
    before:
      description:
        - The share before the task; an empty dict when it did not exist.
        - With O(tenants), a dict keyed by tenant name.
      type: dict
    after:
      description:
        - The share after the task; an empty dict after O(state=absent).
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
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SmbShare
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy import run_scoped

RIGHTS = ["READ", "WRITE", "CHANGE_PERMISSIONS", "ALL", "NONE"]
ARGUMENT_SPEC = {
    "share_name": {"type": "str", "required": True},
    "tenant": {"type": "str"},
    "tenants": {"type": "list", "elements": "str"},
    "tenant_id": {"type": "int", "removed_in_version": "0.3.0", "removed_from_collection": "qumulo.cluster"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "fs_path": {"type": "str"},
    "create_fs_path": {"type": "bool", "default": False},
    "description": {"type": "str"},
    "permissions": {
        "type": "list",
        "elements": "dict",
        "options": {
            "type": {"type": "str", "choices": ["ALLOWED", "DENIED"], "default": "ALLOWED"},
            "trustee": {"type": "str", "required": True},
            "rights": {"type": "list", "elements": "str", "choices": RIGHTS, "required": True},
        },
    },
    "network_permissions": {
        "type": "list",
        "elements": "dict",
        "options": {
            "type": {"type": "str", "choices": ["ALLOWED", "DENIED"], "default": "ALLOWED"},
            "address_ranges": {"type": "list", "elements": "str", "default": []},
            "rights": {"type": "list", "elements": "str", "choices": RIGHTS, "required": True},
        },
    },
    "access_based_enumeration_enabled": {"type": "bool"},
    "require_encryption": {"type": "bool"},
    "default_file_create_mode": {"type": "raw"},
    "default_directory_create_mode": {"type": "raw"},
    "expand_fs_path_variables": {"type": "bool"},
    "offline_files_caching_mode": {"type": "str", "choices": ["NO_CACHING", "MANUAL_CACHING", "AUTOMATIC_CACHING"]},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, mutually_exclusive=[["tenant", "tenants"], ["tenant_id", "tenants"]])
    run(module, lambda client: run_scoped(module, client, SmbShare))


if __name__ == "__main__":
    main()
