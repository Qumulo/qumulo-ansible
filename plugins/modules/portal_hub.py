#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: portal_hub
short_description: Accept a spoke's portal proposal on the hub, set its type and roots, or delete it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Runs on the hub cluster. A spoke proposes a portal with M(qumulo.cluster.portal_spoke), which leaves a PENDING hub
    portal here; this module accepts it, authorizes its roots, and sets its type. The C(qumulo.cluster.portal_pair)
    role pairs both sides in one play.
  - The portal is identified by the pair of cluster UUIDs, this cluster's and O(spoke_cluster_uuid), and each root by its
    hub root path, never by a server id.
  - Accepting a portal or changing its type makes the hub and the spoke unavailable briefly; with O(wait), the module
    waits until the portal is ACCEPTED and ACTIVE with the requested type and roots, reading through the break.
  - Setting O(type=read_only) on a read-write portal drains the spoke. The portal reports QUIESCING until the spoke's
    outstanding writes reach the hub, and with O(wait), the module waits for that.
  - With O(state=absent), deletes the hub portal. Deleting the spoke portal with M(qumulo.cluster.portal_spoke) removes
    the hub portal too; this module waits for that, or deletes the record of a spoke that no longer exists, for example
    after a rebuild gave the spoke a new UUID.
  - With O(state=present), the module fails when the hub portal is DELETING, and a real run fails when the spoke has
    proposed no portal. A cluster with more than one hub portal for O(spoke_cluster_uuid) fails, naming their ids.
options:
  spoke_cluster_uuid:
    description:
      - UUID of the spoke cluster. To remove the record of a rebuilt spoke, give the UUID it had before the rebuild.
    type: str
    required: true
  spoke_hosts:
    description:
      - IP addresses of the spoke cluster, as this cluster reaches them on the portal port.
      - Needed to accept the portal. Changed in place when they differ, compared in any order.
      - Never an FQDN. For a cluster with floating IPs, its floating IPs; for a multi-AZ cluster in AWS, its load
        balancer's IPs; otherwise its node IPs.
    type: list
    elements: dict
    suboptions:
      address:
        description: An IP address of the spoke.
        type: str
        required: true
      port:
        description: The spoke's portal port.
        type: int
        default: 3713
  type:
    description:
      - The portal's type. Unset keeps the type the spoke proposed or the hub last set.
      - Changing V(read_write) to V(read_only) drains the spoke first.
    type: str
    choices: [read_write, read_only]
  authorized_roots:
    description:
      - Hub root paths to authorize. Each must be a root the spoke proposed.
      - Roots not listed are left as they are, pending or authorized.
    type: list
    elements: str
    default: []
  state:
    description: Whether the hub portal exists.
    type: str
    choices: [present, absent]
    default: present
  allow_data_loss:
    description:
      - Allow deleting a hub portal whose spoke is DISCONNECTED, such as the record of a spoke that was rebuilt or
        destroyed. The delete is then forced, and spoke writes that the hub has not received are lost.
      - Without it, deleting a DISCONNECTED hub portal fails before any request that changes the cluster.
      - A hub portal that is DELETING and DISCONNECTED, as it reads for a few seconds after its spoke's delete, is waited
        for first, with O(wait); only one that is still there after O(wait_timeout) fails.
    type: bool
    default: false
  wait:
    description:
      - Wait until the portal is ACCEPTED and ACTIVE with the requested type and roots after a change, or until it is gone
        with O(state=absent).
      - A portal that needs no change but is still in a transition such as QUIESCING, or not ACTIVE, is waited for too, so
        a run during a drain returns once the drain is done. Its result then reports no change.
      - With O(type) unset, the type is not waited for, since a drain shows the old type for a while after it starts.
      - With V(false), the module returns after its requests, and its result shows the portal as it was read just after them.
    type: bool
    default: true
  wait_timeout:
    description:
      - Seconds to wait, including for a delete that a portal transition blocks.
    type: int
    default: 600
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - A proposal that an earlier task in check mode would make is predicted as accepted, with a note.
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - Accepting cannot be retried safely, so the module accepts only a PENDING portal (G-PORTAL-ONESHOT).
      - Hub portals carry no ETag, so a concurrent change between the read and a write goes undetected (G-PORTAL-ETAG).
      - The record of a spoke that no longer exists can only be forced away (G-PORTAL-STALE).
notes:
  - "API: GET /v2/portal/hubs/, GET, PATCH and DELETE /v2/portal/hubs/{id}, POST /v2/portal/hubs/{id}/accept,
    POST /v2/portal/hubs/{id}/roots/{dir}, GET /v1/files/{ref}/info/attributes."
  - Idempotency gaps G-PORTAL-ONESHOT, G-PORTAL-ETAG and G-PORTAL-STALE apply.
  - A PATCH that repeats the stored value still rewrites the record and re-runs its connectivity checks (QUIRK-005), so
    the module sends O(type) and O(spoke_hosts) only when they differ.
  - Qumulo Core 7.10.3 offers the types V(read_write) and V(read_only) only.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Accept the edge's portal and authorize its root
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    spoke_hosts:
      - address: 10.0.1.20
    authorized_roots: [/data]

- name: Reach the edge at its new address
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    spoke_hosts:
      - address: 10.0.1.21

- name: Preview accepting the second edge's portal
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge02'].qumulo_cluster_uuid }}"
    spoke_hosts:
      - address: 10.0.2.20
    authorized_roots: [/data]
  check_mode: true
  diff: true
  register: edge02_preview

- name: Show the predicted portal and what the module noted
  ansible.builtin.debug:
    msg: >-
      {{ edge02_preview.portal.state | default('not proposed yet') }},
      notes: {{ edge02_preview.notes | default([]) }}

- name: Authorize a second root the edge has proposed
  # Roots not listed are left as they are, so the first root stays authorized.
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    authorized_roots: [/archive]

- name: Accept the edge's portal and return before it is ACTIVE
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    spoke_hosts:
      - address: 10.0.1.20
    authorized_roots: [/data]
    wait: false

- name: Drain the edge before deleting its portal
  # The portal reads QUIESCING until the edge's outstanding writes reach the hub; the module waits for that, so the
  # delete that follows strands no writes.
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    type: read_only

- name: Delete the drained portal
  # Deleting the spoke's portal with qumulo.cluster.portal_spoke removes this one too; the module waits for that.
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    state: absent

- name: Make the second edge's portal read-write, which pauses both clusters briefly
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge02'].qumulo_cluster_uuid }}"
    type: read_write

- name: Remove the record of the edge as it was before a rebuild
  # Forced: writes the hub had not received from the old edge are lost. After a rebuild, the last entry of the edge's
  # qumulo_previous_cluster_uuids is the UUID it had before.
  qumulo.cluster.portal_hub:
    spoke_cluster_uuid: "{{ hostvars['edge01'].qumulo_previous_cluster_uuids | last }}"
    state: absent
    allow_data_loss: true
"""

RETURN = r"""
portal:
  description:
    - The hub portal after the task, empty after a delete. M(qumulo.cluster.portal_info) reports hubs and spokes in this shape.
    - In check mode, a portal that is not proposed yet holds only the options.
  returned: always
  type: dict
  contains:
    hub_cluster_uuid:
      description: UUID of this cluster.
      type: str
    spoke_cluster_uuid:
      description: UUID of the spoke cluster.
      type: str
    hub_hosts:
      description: Always V(null) on a hub, which does not record where the spoke reaches it.
      type: list
      elements: dict
    spoke_hosts:
      description: Where this cluster reaches the spoke, each with C(address) and C(port).
      type: list
      elements: dict
    type:
      description: V(read_write) or V(read_only).
      type: str
    state:
      description: V(PENDING), V(ACCEPTED), or a state of a transition such as V(QUIESCING) or V(DELETING).
      type: str
    status:
      description: V(ACTIVE), V(INACTIVE), V(DEGRADED) or V(DISCONNECTED).
      type: str
    roots:
      description: Each root with C(spoke_root), always V(null) on a hub, C(hub_root) and C(authorized).
      type: list
      elements: dict
  sample:
    hub_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
    spoke_cluster_uuid: 625d63a2-9663-4b0e-9d45-ec119a5a9a49
    hub_hosts: null
    spoke_hosts: [{address: 10.0.1.20, port: 3713}]
    type: read_write
    state: ACCEPTED
    status: ACTIVE
    roots: [{spoke_root: null, hub_root: /data, authorized: true}]
notes:
  description: What the module noticed, such as a proposal that check mode could not see yet.
  returned: when there is something to note
  type: list
  elements: str
  sample: [spoke 625d63a2-9663-4b0e-9d45-ec119a5a9a49 has proposed no portal to this hub yet; qumulo.cluster.portal_spoke on the spoke proposes it]
maybe_applied:
  description: True when a write was sent and the read-back did not confirm that it took effect.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import PortalHub

HOST = {"address": {"type": "str", "required": True}, "port": {"type": "int", "default": 3713}}
ARGUMENT_SPEC = {
    "spoke_cluster_uuid": {"type": "str", "required": True},
    "spoke_hosts": {"type": "list", "elements": "dict", "options": HOST},
    "type": {"type": "str", "choices": ["read_write", "read_only"]},
    "authorized_roots": {"type": "list", "elements": "str", "default": []},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "allow_data_loss": {"type": "bool", "default": False},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 600},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), PortalHub)


if __name__ == "__main__":
    main()
