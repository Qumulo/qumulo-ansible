#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: unprotected_edge_create
short_description: Form a single-node unprotected edge from an unconfigured Qumulo node
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Forms an unprotected edge, a single-node cluster, from the node at O(api_host) when that node is UNCONFIGURED, and
    returns the new cluster's UUID.
  - The node is identified by O(api_host), and the cluster it forms by O(cluster_name).
  - Every formation gives the cluster a new UUID, which the request cannot choose (G-UUID).
  - A node that is already ACTIVE in a cluster named O(cluster_name) is left as it is, and the module reports no
    change. When O(expected_cluster_uuid) is set, that cluster must also have that UUID, or the module fails before
    any credentials are sent.
  - A node that is ACTIVE in a cluster with another name fails the module.
  - With O(wait=true), the module waits until the node is ACTIVE, a login as C(admin) succeeds, and the root
    directory can be read.
  - The request that forms the cluster needs no login. When neither O(api_password) nor O(api_token) is set,
    O(api_password) is O(admin_password).
options:
  cluster_name:
    description:
      - The new cluster's name, 2 to 15 letters, digits or hyphens, starting and ending with a letter or digit.
      - A name outside these rules fails before any request is sent.
    type: str
    required: true
  admin_password:
    description: The password the new cluster's C(admin) user gets. It cannot be read back.
    type: str
    required: true
  eula_accepted:
    description: Accept Qumulo's end user license agreement, which forming a cluster needs. Anything but V(true) fails before any request is sent.
    type: bool
    required: true
  wait:
    description: Wait until the new cluster is ready. With V(false), the module returns once the cluster has accepted the request.
    type: bool
    default: true
  wait_timeout:
    description: Seconds to wait for the node to become ACTIVE and serve its file system, including while its REST API does not answer.
    type: int
    default: 900
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Predicts the formation; the new UUID is known only once it has happened.
  diff_mode:
    support: full
    details:
      - The diff shows the node's state before, V(UNCONFIGURED), and the cluster the task forms, with its name and
        V(ACTIVE), in check mode too.
  idempotent:
    support: partial
    details:
      - Forms a cluster only from an UNCONFIGURED node, and each formation mints a new UUID.
notes:
  - "API: GET /v1/node/state and GET /v1/cluster/settings, both without login; POST /v2/cluster/unprotected-edge/create
    without login; with O(wait), GET /v1/files/%2F/info/attributes."
  - The create request is never sent twice. With O(wait), if its connection is lost, the module waits for the node,
    which then shows whether it formed; without O(wait), it fails with RV(maybe_applied).
  - "Registers: G-UUID."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Check that the reinstalled node is unconfigured and would be formed
  qumulo.cluster.unprotected_edge_create:
    cluster_name: "{{ qumulo_cluster_name }}"
    admin_password: "{{ qumulo_api_password }}"
    eula_accepted: true
  check_mode: true
  diff: true
  register: edge_preview

- name: On a rebuild, stop if the node already holds a cluster
  ansible.builtin.assert:
    that: edge_preview.unprotected_edge.state == 'UNCONFIGURED'
    fail_msg: "{{ qumulo_api_host }} is already ACTIVE as {{ edge_preview.unprotected_edge.cluster_id }}"

- name: Form the edge on a reinstalled node, then record its new UUID
  # Every formation gives the cluster a new UUID (G-UUID). A node already ACTIVE under this name is left as it is.
  # What is keyed on the previous UUID, such as the license, the hub's portal record and the Nexus registration, is
  # then replayed for the new one; the edge_rebuild role does that.
  qumulo.cluster.unprotected_edge_create:
    cluster_name: "{{ qumulo_cluster_name }}"
    admin_password: "{{ qumulo_api_password }}"
    eula_accepted: true
  register: formed

- name: Point the identity guard at the new cluster
  ansible.builtin.set_fact:
    qumulo_cluster_uuid: "{{ formed.unprotected_edge.cluster_id }}"

- name: Form the edge, or confirm that the ACTIVE node is the cluster whose UUID the inventory records
  # An ACTIVE node with another UUID fails before any credentials are sent.
  qumulo.cluster.unprotected_edge_create:
    cluster_name: "{{ qumulo_cluster_name }}"
    admin_password: "{{ qumulo_api_password }}"
    eula_accepted: true
    expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"

- name: Start forming the edge and return when the node has accepted the request
  # With wait false the result still shows the node as UNCONFIGURED, with no UUID.
  qumulo.cluster.unprotected_edge_create:
    cluster_name: "{{ qumulo_cluster_name }}"
    admin_password: "{{ qumulo_api_password }}"
    eula_accepted: true
    wait: false
"""

RETURN = r"""
unprotected_edge:
  description:
    - The cluster after the task. In check mode on an UNCONFIGURED node, the cluster the task would form, with no UUID yet.
    - With O(wait=false), the request as sent, with no UUID.
  returned: always
  type: dict
  contains:
    cluster_id:
      description: The cluster's UUID; V(null) when it is not known yet.
      type: str
    cluster_name:
      description: The cluster's name.
      type: str
    state:
      description: The node's state, such as V(ACTIVE), or V(UNCONFIGURED) in check mode and with O(wait=false).
      type: str
  sample: {cluster_id: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11, cluster_name: edge01, state: ACTIVE}
maybe_applied:
  description: True when the create was sent and its connection was lost, and either O(wait) was false or the wait did not show that the node formed.
  returned: on such a failure
  type: bool
  sample: true
live_cluster_uuid:
  description: The UUID of the ACTIVE cluster at O(api_host), when it differs from O(expected_cluster_uuid).
  returned: on that failure
  type: str
  sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
expected_cluster_uuid:
  description: O(expected_cluster_uuid), as the module compared it.
  returned: on that failure
  type: str
  sample: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import EdgeCreate, with_admin_password
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "cluster_name": {"type": "str", "required": True},
    "admin_password": {"type": "str", "required": True, "no_log": True},
    "eula_accepted": {"type": "bool", "required": True},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 900},
}


def main():
    run_resource(with_admin_password(qumulo_module(ARGUMENT_SPEC)), EdgeCreate)


if __name__ == "__main__":
    main()
