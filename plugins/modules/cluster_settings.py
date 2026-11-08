#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: cluster_settings
short_description: Manage a Qumulo cluster's name
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Renames the cluster when its name differs from O(cluster_name). A cluster has one name, so the module manages the
    cluster at O(api_host).
  - The name is also the cluster's Active Directory computer account, and the server refuses a rename while the
    cluster is joined to a domain. Before a rename, the module reads the domain state and fails with what to do, in
    check mode too.
options:
  cluster_name:
    description:
      - The cluster's name, 2 to 15 letters, digits or hyphens, starting and ending with a letter or digit.
      - A name outside these rules fails before any request is sent.
    type: str
    required: true
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
  - "API: GET and PUT /v1/cluster/settings, with If-Match; before a rename, GET /v1/ad/status."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# Name the edge before it joins a domain: the name becomes its computer account, which cannot be renamed while joined.
- name: Name the edge
  qumulo.cluster.cluster_settings:
    cluster_name: edge01

- name: Preview renaming the edge
  qumulo.cluster.cluster_settings:
    cluster_name: edge02
  check_mode: true
  diff: true
  register: rename_preview

- name: Show the name the rename would leave
  ansible.builtin.debug:
    var: rename_preview.cluster_settings.cluster_name

- name: Rename the edge
  qumulo.cluster.cluster_settings:
    cluster_name: edge02

# A rename fails while the cluster is joined to a domain, in check mode too.
# Leave the domain, rename, and join again. Give the join the same options as the cluster's first join, such as ou and
# domain_netbios, or the rejoin uses the domain's defaults.
- name: Rename an edge that is joined to a domain
  block:
    - name: Leave the domain
      qumulo.cluster.ad_domain:
        domain: corp.example.com
        state: absent
        join_user: svc-qjoin
        join_password: "{{ vault_ad_join_password }}"

    - name: Rename the edge
      qumulo.cluster.cluster_settings:
        cluster_name: edge02

    - name: Join the domain under the new name
      qumulo.cluster.ad_domain:
        domain: corp.example.com
        join_user: svc-qjoin
        join_password: "{{ vault_ad_join_password }}"
"""

RETURN = r"""
cluster_settings:
  description: The settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    cluster_name:
      description: The cluster's name.
      type: str
  sample: {cluster_name: edge01}
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["write reconciled after a lost connection"]
maybe_applied:
  description: V(true) when the rename was sent, the connection was lost, and reading the name back did not confirm it.
  returned: on such a failure
  type: bool
expected:
  description: The settings the rename should have left, when the name read back after the write differs.
  returned: on such a failure
  type: dict
  sample: {cluster_name: edge02}
observed:
  description: The settings read back after the write, when they differ from RV(expected).
  returned: on such a failure
  type: dict
  sample: {cluster_name: edge01}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import ClusterSettings
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "cluster_name": {"type": "str", "required": True},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), ClusterSettings)


if __name__ == "__main__":
    main()
