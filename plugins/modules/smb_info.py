#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: smb_info
short_description: Read a Qumulo cluster's SMB shares, SMB server settings and open SMB sessions
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the SMB configuration of one cluster, and changes nothing.
  - Each share and the settings come back in the option shape of M(qumulo.cluster.smb_share) and
    M(qumulo.cluster.smb_settings), so they can be fed back to those modules.
  - Each share names its tenant in C(tenant), before C(tenant_id), which is deprecated with
    O(qumulo.cluster.smb_share#module:tenant_id). C(tenant) is V(null) where the cluster does not offer the tenant
    list, which gives no warning, and for a user without PRIVILEGE_TENANT_READ (HTTP 403), where the module warns once
    per run that the tenant names are null and that reading the tenant list needs that privilege. Any other error
    reading the list fails the module.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V(shares) gathers the shares of every tenant, V(settings) the SMB server settings, V(tenant_settings) each
        tenant's override of them, and V(sessions) the open SMB sessions.
    type: list
    elements: str
    choices: [all, shares, settings, tenant_settings, sessions]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(shares) GET /v3/smb/shares/ with trustee names; V(settings) GET /v1/smb/settings;
    V(tenant_settings) GET /v1/multitenancy/smb/settings/, which answers a list of key and value pairs, one for each
    override; V(sessions) GET /v1/smb/sessions/, paged. V(shares) and V(tenant_settings) also GET
    /v1/multitenancy/tenants/ for the tenant names, which needs PRIVILEGE_TENANT_READ. V(tenant_settings) reads the
    override list only when it could read the tenant list."
  - Qumulo marks the C(/v3/smb/shares/) route the V(shares) subset reads as preview in Qumulo Core 7.10.3 and 7.11.0; a
    later Qumulo Core release may change it.
  - The C(tenant) of the V(shares) subset and the V(tenant_settings) subset resolve through the multitenancy REST API,
    which Qumulo marks as preview; a later Qumulo Core release may change it.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the shares, the server settings and the open sessions
  qumulo.cluster.smb_info:
  register: smb

- name: Read the open SMB sessions
  qumulo.cluster.smb_info:
    gather_subset: [sessions]
  register: smb_sessions

- name: Check that no client is connected before a rebuild
  ansible.builtin.assert:
    that: smb_sessions.smb.sessions | length == 0

- name: Read only the SMB server settings
  qumulo.cluster.smb_info:
    gather_subset: [settings]
  register: smb_server

- name: Fail when SMB signing is not required
  ansible.builtin.assert:
    that: smb_server.smb.settings.signing_required
    fail_msg: SMB signing is not required on this cluster

- name: List each share with the directory it serves
  qumulo.cluster.smb_info:
    gather_subset: [shares]
  register: smb_share_list

- name: Show each share with its directory
  ansible.builtin.debug:
    msg: "{{ item.share_name }} -> {{ item.fs_path }}"
  loop: "{{ smb_share_list.smb.shares }}"
  loop_control:
    label: "{{ item.share_name }}"

- name: Show the tenant that holds each share
  # tenant is the tenant's name, and null where the cluster does not offer the tenant list or the user may not read it.
  ansible.builtin.debug:
    msg: "{{ item.share_name }} is in tenant {{ item.tenant }}"
  loop: "{{ smb_share_list.smb.shares }}"
  loop_control:
    label: "{{ item.share_name }}"

- name: Read the SMB settings each tenant overrides
  # A tenant without an override of its own is left out, and uses the global settings.
  qumulo.cluster.smb_info:
    gather_subset: [tenant_settings]
  register: smb_tenants

- name: Show each tenant's session encryption
  ansible.builtin.debug:
    msg: "{{ item.key }}: {{ item.value.session_encryption }}"
  loop: "{{ smb_tenants.smb.tenant_settings | dict2items }}"
  loop_control:
    label: "{{ item.key }}"
"""

RETURN = r"""
smb:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    shares:
      description:
        - Every share of every tenant, in the option shape of M(qumulo.cluster.smb_share).
        - Each share carries C(tenant), its tenant's name, before C(tenant_id), which is deprecated; read C(tenant).
          C(tenant) is V(null) where the tenant list is not offered or not readable (HTTP 403).
      type: list
      elements: dict
      sample:
        - share_name: projects
          tenant: Default
          tenant_id: 1
          fs_path: /data/projects
          description: Project data
          permissions:
            - {type: ALLOWED, trustee: world:Everyone, rights: [READ]}
          network_permissions:
            - {type: ALLOWED, address_ranges: [], rights: [CHANGE_PERMISSIONS, READ, WRITE]}
          access_based_enumeration_enabled: false
          require_encryption: false
          default_file_create_mode: "0644"
          default_directory_create_mode: "0755"
          expand_fs_path_variables: false
          offline_files_caching_mode: NO_CACHING
    settings:
      description: The SMB server settings, in the option shape of M(qumulo.cluster.smb_settings).
      type: dict
      sample:
        session_encryption: NONE
        supported_dialects: [SMB2_DIALECT_3_0, SMB2_DIALECT_3_11]
        hide_shares_from_unauthorized_users: false
        hide_shares_from_unauthorized_hosts: false
        snapshot_directory_mode: VISIBLE
        bypass_traverse_checking: false
        signing_required: true
    tenant_settings:
      description:
        - Each tenant's override of the SMB server settings, by tenant name, in the option shape of
          M(qumulo.cluster.smb_settings). A tenant without an override is left out, and uses the settings in
          RV(smb.settings).
        - Empty where the tenant list is not offered or not readable, since the tenant names come from it, and the
          override list is then not read. Also empty where the cluster does not offer the override list.
      type: dict
      sample:
        eng:
          session_encryption: REQUIRED
          supported_dialects: [SMB2_DIALECT_3_0, SMB2_DIALECT_3_11]
          hide_shares_from_unauthorized_users: false
          hide_shares_from_unauthorized_hosts: false
          snapshot_directory_mode: VISIBLE
          bypass_traverse_checking: false
          signing_required: true
    sessions:
      description:
        - The open SMB sessions, as the cluster reports them.
        - The cluster may report more fields than the sample shows.
      type: list
      elements: dict
      sample:
        - {identity: {domain: LOCAL, auth_id: "500"}, originator: 10.0.0.5, share_names: [Files], location: "1:1"}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SMB_INFO_MODULE_SUBSETS

ARGUMENT_SPEC = {"gather_subset": {"type": "list", "elements": "str", "choices": ["all", *SMB_INFO_MODULE_SUBSETS], "default": ["all"]}}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "smb", SMB_INFO_MODULE_SUBSETS)


if __name__ == "__main__":
    main()
