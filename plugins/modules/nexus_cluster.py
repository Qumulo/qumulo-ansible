#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: nexus_cluster
short_description: Retire a cluster in Qumulo's Nexus service
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Marks a cluster UUID as decommissioned in Nexus. The cluster is found by O(cluster_uuid).
  - A cluster Nexus already lists as decommissioned, or does not list at all, is left alone.
  - The connection options name a cluster too. When that cluster is the one being retired, the module first checks that
    its Nexus registration has been removed (M(qumulo.cluster.nexus_registration) O(qumulo.cluster.nexus_registration#module:state=absent)).
options:
  cluster_uuid:
    description: The UUID of the cluster to retire in Nexus.
    type: str
    required: true
  state:
    description: V(decommissioned) retires the cluster in Nexus. Nexus offers no way back.
    type: str
    choices: [decommissioned]
    default: decommissioned
  allow_connected:
    description:
      - Retire the cluster even while Nexus reports it connected. Without it the module fails for a connected cluster.
      - Nexus may keep reporting a cluster connected for a short while after its registration was removed.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.nexus
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Check mode reads Nexus and the cluster, as a real run does, and changes nothing.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: Nexus C(GET /api/v1/clusters?cluster_uuid=), C(GET /api/v1/clusters/<uuid>/fleet/connection) and
    C(PATCH /api/v1/clusters/<uuid>/status); when O(cluster_uuid) is the cluster at O(api_host), GET /v1/node/state
    and GET /v1/nexus/registration on it."
  - O(nexus_api_token) is required, and needs a Nexus role that may decommission clusters in the cluster's organization.
  - The module reads the cluster's status back after retiring it. When Nexus lists the cluster without a status, or no
    longer lists it, the module fails and reports a change, because it cannot show that the cluster is retired.
  - Retiring does not stop a cluster that still has the UUID. Given a new join key, it registers again, and Nexus keeps
    listing it as decommissioned (QUIRK-018).
  - The connection options must name a reachable cluster and its credentials even when O(cluster_uuid) belongs to a
    cluster that no longer exists (G-NEXUS-RETIRE). After a rebuild, run it against the new edge.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables. They must name a reachable cluster, such as
# the rebuilt edge, although the cluster that had the retired UUID no longer exists (G-NEXUS-RETIRE).
- name: Preview retiring the UUID an edge had before its rebuild
  qumulo.cluster.nexus_cluster:
    cluster_uuid: 0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02
    nexus_api_token: "{{ vault_nexus_api_token }}"
  check_mode: true
  diff: true
  register: retire_preview

- name: Show the cluster as Nexus would list it
  ansible.builtin.debug:
    var: retire_preview.nexus_cluster

# Nexus offers no way back. Retire only a UUID that no cluster will use again, such as the one an edge had before a
# rebuild gave it a new one: a cluster given a new join key registers again under the same UUID.
- name: Retire the UUID an edge had before its rebuild
  qumulo.cluster.nexus_cluster:
    cluster_uuid: 0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02
    nexus_api_token: "{{ vault_nexus_api_token }}"

- name: Retire several old UUIDs
  qumulo.cluster.nexus_cluster:
    cluster_uuid: "{{ item }}"
    nexus_api_token: "{{ vault_nexus_api_token }}"
  loop:
    - 0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02
    - 5f1c8e90-3a2b-4d67-8c14-b7a9e0d2f6a3

# A cluster that still connects is usually still in use. Nexus may report one connected for a short while after its
# registration was removed.
- name: Retire the old UUID although Nexus still reports it connected
  qumulo.cluster.nexus_cluster:
    cluster_uuid: 0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02
    nexus_api_token: "{{ vault_nexus_api_token }}"
    allow_connected: true

# Retire a cluster that is about to be destroyed. Its Nexus registration must be removed first, and Nexus may report
# it connected for a short while after that, hence allow_connected.
- name: Remove a cluster's registration and retire it in Nexus before destroying it
  block:
    - name: Read the cluster's UUID
      qumulo.cluster.cluster_info:
        gather_subset: [node]
      register: this_cluster

    - name: Remove the cluster's Nexus registration
      qumulo.cluster.nexus_registration:
        state: absent

    - name: Retire the cluster's UUID in Nexus
      qumulo.cluster.nexus_cluster:
        cluster_uuid: "{{ this_cluster.cluster.cluster_id }}"
        nexus_api_token: "{{ vault_nexus_api_token }}"
        allow_connected: true
"""

RETURN = r"""
nexus_cluster:
  description: The cluster as Nexus lists it after the task, or in check mode as it would list it, or V(null) when Nexus does not list it.
  returned: always
  type: dict
  contains:
    cluster_uuid:
      description: The cluster's UUID.
      type: str
    cluster_name:
      description: The cluster's name in Nexus.
      type: str
    status:
      description: The cluster's status in Nexus, such as V(DECOMMISSIONED), or V(null) when Nexus does not report it.
      type: str
    connected:
      description: Whether Nexus reported the cluster connected; V(null) when the module did not ask, as for a cluster already decommissioned.
      type: bool
  sample: {cluster_uuid: 0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02, cluster_name: edge01, status: DECOMMISSIONED, connected: false}
notes:
  description: Why the module did or did not act, when that is not plain from the result.
  returned: when there is something to say
  type: list
  elements: str
  sample: ["Nexus has no cluster 0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02, so there is nothing to retire"]
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import NexusCluster
from ansible_collections.qumulo.cluster.plugins.module_utils.nexus import nexus_argument_spec

ARGUMENT_SPEC = {
    "cluster_uuid": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["decommissioned"], "default": "decommissioned"},
    "allow_connected": {"type": "bool", "default": False},
    **nexus_argument_spec(),
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), NexusCluster)


if __name__ == "__main__":
    main()
