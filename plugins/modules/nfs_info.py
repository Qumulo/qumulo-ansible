#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: nfs_info
short_description: Read a Qumulo cluster's NFS exports and NFS server settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the NFS configuration of one cluster, and changes nothing.
  - Each export and the settings come back in the option shape of M(qumulo.cluster.nfs_export) and
    M(qumulo.cluster.nfs_settings), so they can be fed back to those modules.
  - Each export names its tenant in C(tenant), before C(tenant_id), which is deprecated with
    O(qumulo.cluster.nfs_export#module:tenant_id). C(tenant) is V(null) where the cluster does not offer the tenant
    list, which gives no warning, and for a user without PRIVILEGE_TENANT_READ (HTTP 403), where the module warns once
    per run that the tenant names are null and that reading the tenant list needs that privilege. Any other error
    reading the list fails the module.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V(exports) gathers the exports of every tenant, V(settings) the NFS server settings, and V(tenant_settings)
        each tenant's override of them.
    type: list
    elements: str
    choices: [all, exports, settings, tenant_settings]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(exports) GET /v3/nfs/exports/; V(settings) GET /v2/nfs/settings; V(tenant_settings)
    GET /v1/multitenancy/nfs/settings/, which answers a list of key and value pairs, one for each override.
    V(exports) and V(tenant_settings) also GET /v1/multitenancy/tenants/ for the tenant names, which needs
    PRIVILEGE_TENANT_READ. V(tenant_settings) reads the override list only when it could read the tenant list."
  - Qumulo marks the C(/v3/nfs/exports/) route the V(exports) subset reads as preview in Qumulo Core 7.10.3 and
    7.11.0; a later Qumulo Core release may change it.
  - The C(tenant) of the V(exports) subset and the V(tenant_settings) subset resolve through the multitenancy REST API,
    which Qumulo marks as preview; a later Qumulo Core release may change it.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the exports and the server settings
  qumulo.cluster.nfs_info:
  register: nfs

- name: Read only the NFS server settings
  qumulo.cluster.nfs_info:
    gather_subset: [settings]
  register: nfs_server

- name: Show whether NFSv4.1 is on
  ansible.builtin.debug:
    msg: "NFSv4.1 is {{ 'on' if nfs_server.nfs.settings.v4_enabled else 'off' }}"

- name: List the export paths and the directories they serve
  qumulo.cluster.nfs_info:
    gather_subset: [exports]
  register: nfs_exports

- name: Show each export path with its directory
  ansible.builtin.debug:
    msg: "{{ item.export_path }} -> {{ item.fs_path }}"
  loop: "{{ nfs_exports.nfs.exports }}"
  loop_control:
    label: "{{ item.export_path }}"

- name: Show the tenant that holds each export
  # tenant is the tenant's name, and null where the cluster does not offer the tenant list or the user may not read it.
  ansible.builtin.debug:
    msg: "{{ item.export_path }} is in tenant {{ item.tenant }}"
  loop: "{{ nfs_exports.nfs.exports }}"
  loop_control:
    label: "{{ item.export_path }}"

- name: Read the NFS settings each tenant overrides
  # A tenant without an override of its own is left out, and uses the global settings.
  qumulo.cluster.nfs_info:
    gather_subset: [tenant_settings]
  register: nfs_tenants

- name: Show whether NFSv4.1 is on for each tenant with an override
  ansible.builtin.debug:
    msg: "{{ item.key }}: NFSv4.1 is {{ 'on' if item.value.v4_enabled else 'off' }}"
  loop: "{{ nfs_tenants.nfs.tenant_settings | dict2items }}"
  loop_control:
    label: "{{ item.key }}"

- name: Fail when the factory export of / is still there
  qumulo.cluster.nfs_info:
    gather_subset: [exports]
  register: nfs_factory_check
  failed_when: nfs_factory_check.nfs.exports | selectattr('export_path', 'equalto', '/') | list | length > 0
"""

RETURN = r"""
nfs:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    exports:
      description:
        - Every export of every tenant, in the option shape of M(qumulo.cluster.nfs_export).
        - Each export carries C(tenant), its tenant's name, before C(tenant_id), which is deprecated; read C(tenant).
          C(tenant) is V(null) where the tenant list is not offered or not readable (HTTP 403).
      type: list
      elements: dict
      sample:
        - export_path: /projects
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
    settings:
      description: The NFS server settings, in the option shape of M(qumulo.cluster.nfs_settings).
      type: dict
      sample:
        v4_enabled: false
        krb5_enabled: false
        krb5i_enabled: false
        krb5p_enabled: false
        auth_sys_enabled: true
        idmap_domain: ""
    tenant_settings:
      description:
        - Each tenant's override of the NFS server settings, by tenant name, in the option shape of
          M(qumulo.cluster.nfs_settings). A tenant without an override is left out, and uses the settings in
          RV(nfs.settings).
        - Empty where the tenant list is not offered or not readable, since the tenant names come from it, and the
          override list is then not read. Also empty where the cluster does not offer the override list.
      type: dict
      sample:
        eng:
          v4_enabled: true
          krb5_enabled: false
          krb5i_enabled: false
          krb5p_enabled: false
          auth_sys_enabled: true
          idmap_domain: ""
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import NFS_INFO_MODULE_SUBSETS

ARGUMENT_SPEC = {"gather_subset": {"type": "list", "elements": "str", "choices": ["all", *NFS_INFO_MODULE_SUBSETS], "default": ["all"]}}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "nfs", NFS_INFO_MODULE_SUBSETS)


if __name__ == "__main__":
    main()
