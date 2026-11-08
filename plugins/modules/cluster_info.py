#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: cluster_info
short_description: Read a Qumulo cluster's identity, version, license, capacity and product type
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers facts about one Qumulo cluster, one or two GET requests per subset, and changes nothing.
  - On a node that is not yet part of a cluster (state C(UNCONFIGURED)) no login is possible, so only the
    V(node) and V(version) subsets, which need no login, are gathered. The others are listed in
    RV(unavailable_subsets), and the module warns.
  - A subset that this cluster's product type or version does not offer is listed in RV(unavailable_subsets)
    with the reason, and the module does not fail.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. Each is returned under RV(cluster).
      - V(node) the cluster's UUID and the node's state, V(version) the Qumulo Core release and build, V(settings) the
        cluster's name, V(license) the license, V(capacity) the capacity clamp, V(product) the product type,
        V(filesystem) the file system's sizes.
      - V(nodes) the nodes, V(portals) the number of spoke and hub portals, V(protection) the protection status,
        V(upgrade) the upgrade's state and V(upgrade_blocked) the upgrade preflight.
      - V(all) gathers every subset, adding V(nodes), V(portals), V(protection), V(upgrade) and V(upgrade_blocked) to the
        default.
      - V(node) and V(version) need no login. Every other subset logs in.
    type: list
    elements: str
    choices: [all, node, version, settings, license, capacity, product, filesystem, nodes, portals, protection, upgrade,
              upgrade_blocked]
    default: [node, version, settings, license, capacity, product, filesystem]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(node) GET /v1/node/state and V(version) GET /v1/version, both without login; V(settings) GET /v1/cluster/settings;
    V(license) GET /v1/license/status and /v1/license/enforcement; V(capacity) GET /v1/capacity/clamp;
    V(product) GET /v2/metrics/endpoints/default/data as text/plain; V(filesystem) GET /v1/file-system; V(nodes) GET /v1/cluster/nodes/;
    V(portals) GET /v2/portal/spokes/ and /v2/portal/hubs/; V(protection) GET /v1/cluster/protection/status;
    V(upgrade) GET /v3/upgrade/status, /v2/upgrade/blocked, /v1/shutdown/reboot/status and /v1/cluster/protection/status,
    and on an object-backed cluster /v1/cluster/nodes/; V(upgrade_blocked) GET /v2/upgrade/blocked."
  - The product type has no REST endpoint of its own. It is read from the labels of the C(qumulo_info) sample on the
    cluster's metrics endpoint.
  - A route this cluster does not offer (HTTP 404, 405, 406 or 501) makes its subset unavailable. Any other error,
    such as a missing RBAC privilege, fails the module.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the default facts
  qumulo.cluster.cluster_info:
  register: info

- name: Show the cluster's name, release and license state
  ansible.builtin.debug:
    msg: "{{ info.cluster.cluster_name }} runs Qumulo Core {{ info.cluster.version }}, license {{ info.cluster.license.state }}"

- name: Stop unless this is an unprotected edge
  ansible.builtin.assert:
    that: info.cluster.product.cluster_type == 'unprotected_edge'

- name: Read the node's UUID and state without logging in
  qumulo.cluster.cluster_info:
    gather_subset: [node, version]
  register: node_info

- name: Stop unless the node belongs to a cluster
  ansible.builtin.assert:
    that: node_info.cluster.cluster_id is not none
    fail_msg: "The node is {{ node_info.cluster.state }} and belongs to no cluster yet."

- name: Read the license and when it expires
  qumulo.cluster.cluster_info:
    gather_subset: [license]
  register: license_info

- name: Show when the license expires
  ansible.builtin.debug:
    var: license_info.cluster.license.expiration_date

- name: Read the nodes of the cluster
  qumulo.cluster.cluster_info:
    gather_subset: [nodes]
  register: nodes_info

- name: Show how many nodes the cluster has
  ansible.builtin.debug:
    var: nodes_info.cluster.node_count

- name: Read whether an upgrade is idle and unblocked
  qumulo.cluster.cluster_info:
    gather_subset: [upgrade]
  register: upgrade_info

- name: Stop unless the cluster is ready for an upgrade
  ansible.builtin.assert:
    that:
      - upgrade_info.cluster.upgrade.status.state == 'UPGRADE_STATE_IDLE'
      - not upgrade_info.cluster.upgrade.blocked.is_blocked

- name: Read everything, including nodes and portals
  qumulo.cluster.cluster_info:
    gather_subset: [all]
  register: everything

- name: Show the subsets this cluster could not answer
  ansible.builtin.debug:
    var: everything.unavailable_subsets | default({})
"""

RETURN = r"""
cluster:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    cluster_id:
      description: The cluster's UUID, or V(null) on an UNCONFIGURED node. From the V(node) subset.
      type: str
      sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
    state:
      description: The node's state, such as V(ACTIVE) or V(UNCONFIGURED). From the V(node) subset.
      type: str
      sample: ACTIVE
    version:
      description: The Qumulo Core release, dotted. From the V(version) subset.
      type: str
      sample: 7.10.3
    build_id:
      description: The build of that release. From the V(version) subset.
      type: str
      sample: 385145.1.82.1.73
    cluster_name:
      description: From the V(settings) subset.
      type: str
      sample: edge01
    license:
      description: From the V(license) subset.
      type: dict
      contains:
        state:
          description: V(LICENSE_STATE_NONE), V(LICENSE_STATE_ACTIVE) or V(LICENSE_STATE_EXPIRED).
          type: str
          sample: LICENSE_STATE_ACTIVE
        cluster_uuid:
          description: The cluster UUID the license is bound to, or V(null) without a license.
          type: str
          sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
        usable_capacity_cap:
          description: Bytes of usable capacity the license allows, or V(null) when it sets no cap or there is no license.
          type: int
          sample: 4000000000000
        expiration_date:
          description: As the cluster reports it, or V(null) without a license.
          type: str
          sample: "2027-10-31T23:59:59Z"
        enforced:
          description: Whether the cluster enforces the license, or V(null) when this version does not say.
          type: bool
          sample: true
    capacity_clamp:
      description: Bytes the usable capacity is clamped to, or V(null) when no clamp is set. From the V(capacity) subset.
      type: int
      sample: 4000000000000
    product:
      description: From the V(product) subset. The labels are V(unknown) when the metrics carry no C(qumulo_info) sample.
      type: dict
      contains:
        product_intent:
          description:
            - The C(product_intent) label of the C(qumulo_info) metric, as the cluster reports it.
            - For example V(default) for a general-purpose cluster or V(edge_accelerator) for an unprotected edge.
          type: str
          sample: edge_accelerator
        cluster_type:
          description: The C(cluster_type) label, V(unprotected_edge) or V(protected_cluster).
          type: str
          sample: unprotected_edge
        unprotected_edge:
          description: Whether RV(cluster.product.cluster_type) is V(unprotected_edge); V(null) when it is unknown.
          type: bool
          sample: true
    node_count:
      description: From the V(nodes) subset.
      type: int
      sample: 1
    nodes:
      description: Each node as the cluster reports it. From the V(nodes) subset.
      type: list
      elements: dict
      sample:
        - {id: 1, node_status: online, node_name: edge01-1, uuid: 0d9e6a3b-2f41-4c8e-9b7a-5e1f3c2d4a60, label: "00:00:00:00:00:01",
           model_number: None, serial_number: 3c7e1a52-8b4d-4f09-a6e2-91d05f7b8c34, mac_address: ""}
    filesystem:
      description: The file system's sizes in bytes, each field of C(/v1/file-system) that ends in C(_bytes). From the V(filesystem) subset.
      type: dict
      contains:
        total_size_bytes:
          description: The file system's usable size.
          type: int
          sample: 4000000000000
        free_size_bytes:
          description: The space not yet used.
          type: int
          sample: 3712000000000
    portals:
      description: Counts of spoke and hub portals on this cluster. From the V(portals) subset.
      type: dict
      sample: {spokes: 0, hubs: 0}
    protection:
      description:
        - The protection status from the V(protection) subset.
        - On an object-backed cluster only C(protection_system_type) is returned.
      type: dict
      sample:
        {blocks_per_stripe: 1, data_blocks_per_stripe: 1, protection_system_type: PROTECTION_SYSTEM_TYPE_EC, max_node_failures: 0,
         max_drive_failures: 0, max_drive_spares: 0, remaining_node_failures: 0, remaining_drive_failures: 0, pending_configuration: null}
    upgrade:
      description:
        - The upgrade's state, from the V(upgrade) subset.
        - C(status) is C(GET /v3/upgrade/status)'s answer, never the deprecated v2 status, which reads REBOOTING as IDLE
          (QUIRK-090), with any URL in C(settings.install_path) or C(error_info) stripped of its query string and user
          information. C(blocked) is the upgrade preflight, and C(reboot) the reboot manager's status.
        - The subset, and so V(all), reads C(/v1/shutdown/reboot/status) once per gather, and each such read asks every
          node for its boot identity.
        - C(protection) gives the node tolerances on every cluster type, including an object-backed one, where the
          V(protection) subset returns only C(protection_system_type). C(configured_node_failures) is
          C(max_node_failures) on an erasure-coded cluster, and on an object-backed one 0 below three nodes and 1 from
          three.
      type: dict
      sample:
        status: {state: UPGRADE_STATE_IDLE, progress: null, settings: null, error_info: null}
        blocked: {is_blocked: false, cluster_degraded: false, blocked_reason: ""}
        reboot: {state: REBOOT_IDLE, rolling: false, upgrade_initiated: false, total_node_count: 0, rebooted_node_count: 0, error_message: null}
        protection: {type: PROTECTION_SYSTEM_TYPE_EC, configured_node_failures: 0, remaining_node_failures: 0}
    upgrade_blocked:
      description:
        - The upgrade preflight, C(GET /v2/upgrade/blocked)'s answer, from the V(upgrade_blocked) subset, which reads
          nothing else. Unlike V(upgrade), it does not read the reboot status.
      type: dict
      sample: {is_blocked: false, cluster_degraded: false, blocked_reason: ""}
unavailable_subsets:
  description: Each requested subset this cluster could not answer, with the reason.
  returned: when a subset was unavailable
  type: dict
  sample: {portals: "GET /v2/portal/spokes/ failed: HTTP 404 ..."}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import CLUSTER_INFO_SUBSETS, DEFAULT_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *CLUSTER_INFO_SUBSETS], "default": DEFAULT_SUBSETS},
}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "cluster", CLUSTER_INFO_SUBSETS)


if __name__ == "__main__":
    main()
