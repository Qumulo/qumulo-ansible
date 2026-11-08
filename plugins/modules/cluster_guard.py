#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: cluster_guard
short_description: Check that a Qumulo cluster is the one a play expects, and wait until it is ready
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Reads only, and never reports a change.
  - With O(mode=match), checks that the cluster at O(api_host) has the UUID O(expected_cluster_uuid) before any
    credentials are sent, then waits until the cluster is ready to serve requests. Every qumulo.cluster module checks
    the UUID before it logs in; this module adds the wait.
  - With O(mode=rebuilt), checks that the cluster was rebuilt and holds nothing yet. It passes for a node that is
    UNCONFIGURED, or for an ACTIVE cluster that meets every condition below; any other cluster fails, and the
    message names the first condition it does not meet.
  - The ACTIVE cluster's UUID is O(new_cluster_uuid). This is checked before any credentials are sent, and a node
    that belongs to any other cluster fails at once, whatever its state.
  - The ACTIVE cluster is an unprotected edge, according to the C(cluster_type) label of its C(qumulo_info) metric.
  - The ACTIVE cluster's name is O(cluster_name).
  - The ACTIVE cluster has no spoke portals and no hub portals.
  - The ACTIVE cluster has no SMB share other than the factory C(Files) share, and no NFS export other than the
    factory C(/) export.
options:
  mode:
    description:
      - V(match) checks that the cluster is the one in O(expected_cluster_uuid), which must be set; an empty value fails.
      - V(rebuilt) checks that the cluster is newly formed and empty. O(expected_cluster_uuid) is not used.
    type: str
    choices: [match, rebuilt]
    default: match
  new_cluster_uuid:
    description:
      - The UUID the rebuilt cluster must have when it is ACTIVE. Needed for O(mode=rebuilt) on an ACTIVE cluster.
    type: str
  cluster_name:
    description:
      - The name the rebuilt cluster must have when it is ACTIVE. Needed for O(mode=rebuilt) on an ACTIVE cluster.
    type: str
  wait:
    description:
      - Wait until the cluster is ready.
      - It is ready when its node state is ACTIVE, a login succeeds, and the attributes of the root directory can be read.
        With O(mode=rebuilt), a node that is UNCONFIGURED is ready at once.
      - With V(false), the node state is read once. O(mode=match) then checks only the UUID and logs in to nothing,
        and O(mode=rebuilt) fails for a node that is neither UNCONFIGURED nor ACTIVE.
      - A UUID that is not the expected one fails at once, without waiting.
    type: bool
    default: true
  wait_timeout:
    description:
      - Seconds to wait for the cluster to be ready, including while its REST API does not answer.
      - The wait ends at this deadline even when O(retry_timeout) is longer, though a single attempt can still
        take up to O(request_timeout).
    type: int
    default: 600
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: GET /v1/node/state (no login), GET /v1/version (no login), GET /v1/files/%2F/info/attributes;
    O(mode=rebuilt) also GET /v2/metrics/endpoints/default/data, /v1/cluster/settings, /v2/portal/spokes/,
    /v2/portal/hubs/, /v3/smb/shares/ and /v3/nfs/exports/."
  - The module warns when the cluster runs a Qumulo Core release older than the oldest one this collection is tested against.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# Run the guard first in a play, before any task that writes.
- name: Stop unless api_host is the expected cluster, and wait until it is ready
  qumulo.cluster.cluster_guard:
    expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"

- name: Wait up to half an hour for a cluster that is still starting
  qumulo.cluster.cluster_guard:
    expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"
    wait_timeout: 1800

- name: Check the cluster's UUID once, without waiting or logging in
  qumulo.cluster.cluster_guard:
    expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"
    wait: false

- name: Record the cluster's state and release
  qumulo.cluster.cluster_guard:
    expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"
  register: guard

- name: Show the cluster's state and release
  ansible.builtin.debug:
    msg: "{{ guard.cluster_id }} is {{ guard.state }} on Qumulo Core {{ guard.version }}"

- name: Before configuring a rebuilt edge, check that it is new and empty
  qumulo.cluster.cluster_guard:
    mode: rebuilt
    new_cluster_uuid: "{{ qumulo_new_cluster_uuid }}"
    cluster_name: "{{ qumulo_cluster_name }}"
  when: qumulo_rebuild | default(false) | bool

- name: Check a rebuilt edge once, without waiting for it
  qumulo.cluster.cluster_guard:
    mode: rebuilt
    new_cluster_uuid: "{{ qumulo_new_cluster_uuid }}"
    cluster_name: "{{ qumulo_cluster_name }}"
    wait: false
"""

RETURN = r"""
cluster_id:
  description: The cluster's UUID, or V(null) on an UNCONFIGURED node.
  returned: success
  type: str
  sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
state:
  description: The node's state, such as V(ACTIVE) or V(UNCONFIGURED).
  returned: success
  type: str
  sample: ACTIVE
version:
  description: The Qumulo Core release, dotted.
  returned: success
  type: str
  sample: 7.10.3
live_cluster_uuid:
  description: The UUID the cluster at O(api_host) reports, or V(null) for an UNCONFIGURED node.
  returned: failure, with O(mode=match), when the cluster's UUID is not O(expected_cluster_uuid)
  type: str
  sample: 9533a9e2-e57a-454f-8aa0-9c77b706b7bd
expected_cluster_uuid:
  description: The UUID the task expected, from O(expected_cluster_uuid).
  returned: failure, with O(mode=match), when the cluster's UUID is not O(expected_cluster_uuid)
  type: str
  sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import Guard
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "mode": {"type": "str", "choices": ["match", "rebuilt"], "default": "match"},
    "new_cluster_uuid": {"type": "str"},
    "cluster_name": {"type": "str"},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 600},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), Guard)


if __name__ == "__main__":
    main()
