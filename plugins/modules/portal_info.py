#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: portal_info
short_description: Read a Qumulo cluster's spoke and hub portals, its eviction threshold and its cache size
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the portals of one cluster and changes nothing. A portal is identified by the pair of cluster UUIDs.
  - Spokes and hubs are reported in one shape, the one M(qumulo.cluster.portal_spoke) and M(qumulo.cluster.portal_hub)
    return, so a spoke's entry and the hub's entry for it compare field by field. Each side records only what it knows,
    and the rest is V(null).
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
    type: list
    elements: str
    choices: [all, spokes, hubs, eviction, cache]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(spokes) GET /v2/portal/spokes/; V(hubs) GET /v2/portal/hubs/; both GET /v1/files/{ref}/info/attributes for each
    root's path; V(eviction) GET /v1/portal/spokes/eviction-settings; V(cache) GET /v1/file-system."
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task names another cluster. Set them once for the play
# with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read every portal fact of the edge
  qumulo.cluster.portal_info:
  register: edge_facts

- name: Read the edge's spoke portals
  qumulo.cluster.portal_info:
    gather_subset: [spokes]
  register: edge_portals

- name: Read the hub's record of the edge's portal, to compare with the edge's own
  # Run on each side of a pairing to see both. The hub's entry for the edge is found by the edge's UUID.
  qumulo.cluster.portal_info:
    gather_subset: [hubs]
    api_host: hub01.example.com
    api_password: "{{ vault_hub01_admin_password }}"
    expected_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
  register: hub_portals

- name: Show the hub's entry for the edge
  ansible.builtin.debug:
    msg: "{{ hub_portals.portals.hubs | selectattr('spoke_cluster_uuid', 'equalto', qumulo_cluster_uuid | lower) | list }}"

- name: Read how much the edge uses to cache spoke data, and its eviction threshold
  qumulo.cluster.portal_info:
    gather_subset: [cache, eviction]
  register: edge_cache

- name: Wait until the edge's portal to the hub is ACCEPTED and ACTIVE
  qumulo.cluster.portal_info:
    gather_subset: [spokes]
  register: edge_portals
  until: >-
    edge_portals.portals.spokes | selectattr('hub_cluster_uuid', 'equalto', hostvars['hub01'].qumulo_cluster_uuid | lower)
    | selectattr('state', 'equalto', 'ACCEPTED') | selectattr('status', 'equalto', 'ACTIVE') | list | length == 1
  retries: 60
  delay: 5
"""

RETURN = r"""
portals:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    spokes:
      description:
        - This cluster's spoke portals, from the V(spokes) subset.
        - Each has C(hub_cluster_uuid), C(spoke_cluster_uuid), C(hub_hosts), C(spoke_hosts), C(type), C(state),
          C(status) and C(roots), as M(qumulo.cluster.portal_spoke) returns them.
      type: list
      elements: dict
      sample:
        - hub_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
          spoke_cluster_uuid: 625d63a2-9663-4b0e-9d45-ec119a5a9a49
          hub_hosts: [{address: 10.0.0.10, port: 3713}]
          spoke_hosts: null
          type: read_write
          state: ACCEPTED
          status: ACTIVE
          roots: [{spoke_root: /hub-data, hub_root: /data, authorized: true}]
    hubs:
      description:
        - This cluster's hub portals, from the V(hubs) subset, in the same shape as RV(portals.spokes).
      type: list
      elements: dict
      sample:
        - hub_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
          spoke_cluster_uuid: 625d63a2-9663-4b0e-9d45-ec119a5a9a49
          hub_hosts: null
          spoke_hosts: [{address: 10.0.1.20, port: 3713}]
          type: read_write
          state: ACCEPTED
          status: ACTIVE
          roots: [{spoke_root: null, hub_root: /data, authorized: true}]
    eviction_settings:
      description: The cache eviction threshold, from the V(eviction) subset, as M(qumulo.cluster.portal_eviction_settings) returns it.
      type: dict
      sample: {free_threshold: 0.05}
    portal_cache_size_bytes:
      description:
        - Bytes the cluster uses to cache data for its spoke portals, from the V(cache) subset.
        - V(null) when the file system does not report it; absent, with the subset in RV(unavailable_subsets), when the route cannot be read.
      type: int
      sample: 53687091200
unavailable_subsets:
  description: Each requested subset this cluster could not read, with the reason.
  returned: when a gathered subset cannot be read on this cluster
  type: dict
  sample: {cache: "the node is UNCONFIGURED"}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import PORTAL_INFO_SUBSETS

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *PORTAL_INFO_SUBSETS], "default": ["all"]},
}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "portals", PORTAL_INFO_SUBSETS)


if __name__ == "__main__":
    main()
