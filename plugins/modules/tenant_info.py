#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: tenant_info
short_description: Read a Qumulo cluster's network tenants
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Reads every network tenant of one cluster, and changes nothing.
  - Each tenant comes back in the option shape of M(qumulo.cluster.tenant), so it carries its name and its six service
    switches. It also carries three read-only keys that M(qumulo.cluster.tenant) never takes, RV(tenants[].id),
    RV(tenants[].networks) and RV(tenants[].identity_config_id), so a caller can map ids to names and see which identity
    configuration each tenant uses.
  - It returns no SMB or NFS override, share or export.
options: {}
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: GET /v1/multitenancy/tenants/."
  - Qumulo marks the multitenancy REST API this module uses as preview; a later Qumulo Core release may change it.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the cluster's tenants
  qumulo.cluster.tenant_info:
  register: tenant_facts

- name: List the names of the tenants
  ansible.builtin.debug:
    msg: "{{ tenant_facts.tenants | map(attribute='name') | list }}"

# Each tenant's networks are ids. This maps them to the names of the tenants that hold them.
- name: Show the name of the tenant that holds network 2
  ansible.builtin.debug:
    msg: "{{ tenant_facts.tenants | selectattr('networks', 'contains', 2) | map(attribute='name') | list }}"

- name: Show the names of the tenants that have SMB on
  ansible.builtin.debug:
    msg: "{{ tenant_facts.tenants | selectattr('smb_enabled') | map(attribute='name') | list }}"
"""

RETURN = r"""
tenants:
  description: >-
    Every tenant, sorted by name, in the option shape of M(qumulo.cluster.tenant) with three read-only keys,
    RV(tenants[].id), RV(tenants[].networks) and RV(tenants[].identity_config_id).
  returned: always
  type: list
  elements: dict
  contains:
    id:
      description: The tenant's id, read-only. M(qumulo.cluster.tenant) never takes it.
      type: int
      sample: 2
    name:
      description: The tenant's name.
      type: str
      sample: finance
    web_ui_enabled:
      description: Whether the tenant's networks serve the web UI.
      type: bool
      sample: false
    rest_api_enabled:
      description: Whether the tenant's networks serve the REST API.
      type: bool
      sample: false
    ssh_enabled:
      description: Whether the tenant's networks serve SSH.
      type: bool
      sample: false
    replication_enabled:
      description: Whether the tenant's networks serve replication.
      type: bool
      sample: false
    nfs_enabled:
      description: Whether the tenant's networks serve NFS.
      type: bool
      sample: true
    smb_enabled:
      description: Whether the tenant's networks serve SMB.
      type: bool
      sample: false
    networks:
      description: The ids of the networks the tenant holds, sorted, read-only. M(qumulo.cluster.tenant) never takes it.
      type: list
      elements: int
      sample: [2]
    identity_config_id:
      description: The id of the identity configuration the tenant uses, read-only. M(qumulo.cluster.tenant) never
        takes it. M(qumulo.cluster.tenancy_plan) reads it to find the identity configuration a new tenant takes.
      type: int
      sample: 1
  sample:
    - id: 1
      name: Default
      web_ui_enabled: true
      rest_api_enabled: true
      ssh_enabled: true
      replication_enabled: true
      nfs_enabled: true
      smb_enabled: true
      networks: [1]
      identity_config_id: 1
    - id: 2
      name: finance
      web_ui_enabled: false
      rest_api_enabled: false
      ssh_enabled: false
      replication_enabled: false
      nfs_enabled: true
      smb_enabled: false
      networks: [2]
      identity_config_id: 1
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy import tenants_facts

ARGUMENT_SPEC = {}


def main():
    run(qumulo_module(ARGUMENT_SPEC), tenants_facts)


if __name__ == "__main__":
    main()
