#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: portal_spoke
short_description: Propose a spoke portal to a hub cluster, or delete it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Runs on the spoke cluster. A spoke proposes a portal relationship and its root directories; the hub accepts them with
    M(qumulo.cluster.portal_hub). The C(qumulo.cluster.portal_pair) role pairs both sides in one play.
  - The portal is identified by the pair of cluster UUIDs, this cluster's and O(hub_cluster_uuid), and each root by its
    spoke root path, never by a server id. A cluster has at most one spoke portal to a given hub cluster.
  - Creating the spoke portal and proposing a root cannot be retried safely, so before either the module checks on this
    cluster that each new spoke root does not exist yet and that its parent directory does.
  - With O(state=absent), deletes the spoke portal, which hands its outstanding writes to the hub first.
options:
  hub_cluster_uuid:
    description:
      - UUID of the hub cluster.
      - When O(hub_hosts) reach a cluster with another UUID, the module withdraws the proposal it just made and fails.
    type: str
    required: true
  hub_hosts:
    description:
      - IP addresses of the hub cluster, as this cluster reaches them on the portal port.
      - Needed to create the spoke portal. Changed in place when they differ, compared in any order.
      - Never an FQDN. For a cluster with floating IPs, its floating IPs; for a multi-AZ cluster in AWS, its load
        balancer's IPs; otherwise its node IPs.
    type: list
    elements: dict
    suboptions:
      address:
        description: An IP address of the hub.
        type: str
        required: true
      port:
        description: The hub's portal port.
        type: int
        default: 3713
  type:
    description:
      - The type a new spoke portal is created with.
      - Used only when the spoke portal is created. The hub decides the type of an accepted portal, so change it with
        M(qumulo.cluster.portal_hub); a different value here adds a note and changes nothing.
    type: str
    choices: [read_write, read_only]
    default: read_write
  roots:
    description:
      - Root directories to propose. A proposed root gives the spoke access to the hub root once the hub authorizes it.
      - The module proposes the roots that the spoke portal lacks. Roots not listed are left as they are.
      - A spoke root that already maps to another hub root fails, since a root cannot be changed in place.
    type: list
    elements: dict
    default: []
    suboptions:
      spoke_root:
        description:
          - Absolute path of the spoke root on this cluster. The server creates it, so it must not exist yet, and its
            parent directory must.
        type: str
        required: true
      hub_root:
        description: Absolute path of an existing directory on the hub cluster.
        type: str
        required: true
  state:
    description: Whether the spoke portal exists.
    type: str
    choices: [present, absent]
    default: present
  allow_data_loss:
    description:
      - Allow deleting a spoke portal whose hub is DISCONNECTED. The delete is then forced, and spoke writes that the hub
        has not received are lost.
      - Without it, deleting a DISCONNECTED spoke portal fails before any request that changes the cluster.
    type: bool
    default: false
  wait:
    description:
      - With O(state=absent), wait until the spoke portal is gone.
    type: bool
    default: true
  wait_timeout:
    description:
      - Seconds to wait for a delete that a portal transition blocks, and for the spoke portal to be gone.
    type: int
    default: 600
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
    support: partial
    details:
      - Creating the spoke portal and proposing a root cannot be retried safely (G-PORTAL-ONESHOT). The module lists and
        matches first and checks the spoke root before either.
      - Spoke portals carry no ETag, so a concurrent change between the read and a write goes undetected (G-PORTAL-ETAG).
notes:
  - "API: GET and POST /v2/portal/spokes/, PATCH and DELETE /v2/portal/spokes/{id}, POST /v2/portal/spokes/{id}/roots/,
    GET /v1/files/{ref}/info/attributes."
  - Idempotency gaps G-PORTAL-ONESHOT and G-PORTAL-ETAG apply.
  - A PATCH that repeats the stored value still rewrites the record and re-runs its connectivity checks (QUIRK-005), so
    the module sends O(hub_hosts) only when they differ.
  - A delete that a portal transition blocks, such as a drain in progress, is retried until O(wait_timeout).
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task names another cluster. Set them once for the play
# with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Propose a read-write portal to the hub, with one root
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    hub_hosts:
      - address: 10.0.0.10
      - address: 10.0.0.11
    roots:
      - spoke_root: /hub-data
        hub_root: /data

- name: Propose a second root, leaving the first as it is
  # The spoke root must not exist yet, and its parent must.
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    roots:
      - spoke_root: /hub-archive
        hub_root: /archive

- name: Reach the hub at its replacement nodes
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    hub_hosts:
      - address: 10.0.0.12
      - address: 10.0.0.13

- name: Preview the portal from the inventory
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    hub_hosts: "{{ hostvars['hub01'].qumulo_portal_hosts }}"
    type: read_only
    roots: "{{ qumulo_portal.roots }}"
  check_mode: true
  diff: true
  register: spoke_preview

- name: Show what the module noted, such as a type only the hub can change
  ansible.builtin.debug:
    var: spoke_preview.notes
  when: spoke_preview.notes is defined

- name: Drain the portal on the hub, which decides its type
  # Set the portal to read_only on the hub before deleting the spoke portal.
  qumulo.cluster.portal_hub:
    api_host: hub01.example.com
    api_password: "{{ vault_hub01_admin_password }}"
    expected_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    spoke_cluster_uuid: "{{ qumulo_cluster_uuid }}"
    type: read_only

- name: Delete the spoke portal after the hub drained it
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    state: absent

- name: Delete the spoke portal and return without waiting for it to be gone
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    state: absent
    wait: false

- name: Force the delete of the portal to a hub that no longer answers
  # Writes the hub has not received are lost.
  qumulo.cluster.portal_spoke:
    hub_cluster_uuid: "{{ old_hub_cluster_uuid }}"
    state: absent
    allow_data_loss: true
"""

RETURN = r"""
portal:
  description:
    - The spoke portal after the task, empty after a delete. M(qumulo.cluster.portal_info) reports hubs and spokes in this shape.
    - In check mode, the portal the task would create holds only the options.
  returned: always
  type: dict
  contains:
    hub_cluster_uuid:
      description: UUID of the hub cluster.
      type: str
    spoke_cluster_uuid:
      description: UUID of this cluster.
      type: str
    hub_hosts:
      description: Where this cluster reaches the hub, each with C(address) and C(port).
      type: list
      elements: dict
    spoke_hosts:
      description: Always V(null) on a spoke, which does not record where the hub reaches it.
      type: list
      elements: dict
    type:
      description: V(read_write) or V(read_only).
      type: str
    state:
      description: V(PENDING) until the hub accepts the portal, then V(ACCEPTED), or a state of a transition such as V(QUIESCING).
      type: str
    status:
      description: V(ACTIVE), V(INACTIVE), V(DEGRADED) or V(DISCONNECTED).
      type: str
    roots:
      description: Each root with C(spoke_root), C(hub_root) and C(authorized). C(hub_root) is V(null) until the hub reports its path.
      type: list
      elements: dict
  sample:
    hub_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
    spoke_cluster_uuid: 625d63a2-9663-4b0e-9d45-ec119a5a9a49
    hub_hosts: [{address: 10.0.0.10, port: 3713}]
    spoke_hosts: null
    type: read_write
    state: ACCEPTED
    status: ACTIVE
    roots: [{spoke_root: /hub-data, hub_root: /data, authorized: true}]
notes:
  description: What the module noticed but did not change, such as a O(type) that differs from the accepted portal's.
  returned: when there is something to note
  type: list
  elements: str
  sample: [the hub decides the type of this portal, read_write; change it to read_only with qumulo.cluster.portal_hub]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the portal back did not confirm it.
    The task fails with C(changed) true.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the options that did not converge.
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {hub_hosts: [{address: 10.0.0.12, port: 3713}, {address: 10.0.0.13, port: 3713}]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {hub_hosts: [{address: 10.0.0.10, port: 3713}]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import PortalSpoke

HOST = {"address": {"type": "str", "required": True}, "port": {"type": "int", "default": 3713}}
ROOT = {"spoke_root": {"type": "str", "required": True}, "hub_root": {"type": "str", "required": True}}
ARGUMENT_SPEC = {
    "hub_cluster_uuid": {"type": "str", "required": True},
    "hub_hosts": {"type": "list", "elements": "dict", "options": HOST},
    "type": {"type": "str", "choices": ["read_write", "read_only"], "default": "read_write"},
    "roots": {"type": "list", "elements": "dict", "options": ROOT, "default": []},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "allow_data_loss": {"type": "bool", "default": False},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 600},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), PortalSpoke)


if __name__ == "__main__":
    main()
