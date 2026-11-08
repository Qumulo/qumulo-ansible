#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_failover_plan
short_description: Choose the next step of a replication failover, resync or failback, from fresh reads of both clusters
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - The C(qumulo.cluster.replication_failover) role calls this module before every step, with fresh reads of both
    clusters, and runs only the step it returns. Its first answer lists every step left, which is the role's dry run.
  - A pure computation. It sends no request, takes no connection options, and is not in the C(qumulo.cluster.qumulo)
    action group, so C(module_defaults) for that group do not reach it.
  - The source and the target are the relationship's declared ones, whatever direction the records run in. In the
    declared direction the target holds a target record from the source's UUID with the two paths; after a reverse it
    holds a source record of the target path to the source's UUID. The module matches both clusters' records against
    both forms.
  - It names the relationship's state, RV(state), and lists the steps O(action) has left from it, RV(steps).
  - Each verb moves the record of the cluster it is sent to first, and the peer's record follows seconds later. The
    module reads the state from the record a verb moves first, and the other record may lag.
  - Two pairs of states read the same. The source's target record DISCONNECTED after a reverse reads as the resync's
    state before its reconnect, unless O(last_verb) is the failback's make-writable on the source. The target's record
    DISCONNECTED in the declared direction reads as failed over, unless O(last_verb) is the failback's reverse.
  - It fails, before any step, when no record of the relationship is on the target, when records run in both directions
    or two run in one, when a live record on the target is from a previous UUID of the source, when the state is one
    O(action) does not start from, when a make-writable has no recovery point or the source answers with a job running
    and O(allow_partial_job) is false, when a resync or failback has no answer from the source or no record on it, when
    a reverse is toward an address that is not an IP literal, and when a new target's tenant has replication off. The
    message says what would lift it, where something does.
options:
  action:
    description:
      - V(failover) makes the target's copy writable. V(resync) reverses the relationship and reconnects it, so the
        target replicates its writes to the source. V(failback) resyncs when needed, then waits for a current recovery
        point, makes the source writable, reverses it back and reconnects the target, and applies the declared settings.
    type: str
    choices: [failover, resync, failback]
    required: true
  relationship:
    description:
      - The declared relationship, one entry of the source host's C(qumulo_replication_relationships), with
        C(source_path) and C(target_path), and the settings a failback applies at its end.
    type: dict
    required: true
  source_host:
    description: The source's inventory host name.
    type: str
    required: true
  target_host:
    description: The target's inventory host name.
    type: str
    required: true
  source_cluster_uuid:
    description: The source's C(qumulo_cluster_uuid).
    type: str
    required: true
  target_cluster_uuid:
    description: The target's C(qumulo_cluster_uuid).
    type: str
    required: true
  source_replication_address:
    description:
      - The source's C(qumulo_replication_address), which a resync's reverse sends.
      - Required, as an IP literal, when the steps left hold the resync's reverse.
    type: str
  source_replication_port:
    description: The source's C(qumulo_replication_port).
    type: int
    default: 3712
  target_replication_address:
    description:
      - The target's C(qumulo_replication_address), which a failback's reverse sends.
      - Required, as an IP literal, when the steps left hold the failback's reverse.
    type: str
  target_replication_port:
    description: The target's C(qumulo_replication_port).
    type: int
    default: 3712
  source_relationships:
    description:
      - The source's C(replication) facts from M(qumulo.cluster.replication_info).
      - Leave it out when the source did not answer.
    type: dict
  source_error:
    description: Why the source did not answer, when it did not. A resync or failback names it in its refusal.
    type: str
  target_relationships:
    description: The target's C(replication) facts from M(qumulo.cluster.replication_info).
    type: dict
    required: true
  source_tenancy:
    description:
      - The source's tenants, C(tenants), as M(qumulo.cluster.tenant_info) gives them, and its network document,
        C(document), as M(qumulo.cluster.network_info) gives it, for the check that the tenant serving its replication
        address has replication on. Required when the steps left hold a resync's reverse.
    type: dict
  target_tenancy:
    description: The target's tenants and network document, as O(source_tenancy). Required when the steps left hold a failback's reverse.
    type: dict
  source_previous_cluster_uuids:
    description:
      - The source host's C(qumulo_previous_cluster_uuids). A live record on the target from one of them holds the replica
        a rebuilt source's restore brings back, and the module refuses, naming the restore.
    type: list
    elements: str
    default: []
  last_verb:
    description:
      - The name of the last verb step this run sent, the C(name) of that answer's RV(step), or unset before the run
        sends one.
      - It decides the two states that read the same. A finished wait for the source's record to read DISCONNECTED
        counts as V(make_writable_source).
    type: str
  newer_writes_hosts:
    description:
      - The inventory hosts that the role's C(replication_failover_allow_newer_writes) names, whose reconnects skip the
        comparison.
      - The text of a reconnect step sent to one of them says that it does not compare first. Nothing else in the answer
        depends on it.
    type: list
    elements: str
    default: []
  allow_partial_job:
    description: Let a failover discard the changes of a job the source, which answers, is running.
    type: bool
    default: false
  synced:
    description: Whether this run's wait for a current recovery point, the step V(await_sync), has finished, which drops that step.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - While the source does not answer, the target's record of a job the source was running reads REPLICATION_RUNNING for
    as long as it was watched (QUIRK-125), so the module refuses a make-writable for a running job only when the source
    answers, and otherwise notes the partial job the make-writable discards.
  - C(docs/explanation/replication.md) says why the module reads the states as it does.
"""

EXAMPLES = r"""
# The qumulo.cluster.replication_failover role does these reads and calls before every step. Use the module directly
# to read what a failover would do without the role. The role goes on without the source only when its read gave up or
# ended in a 502, 503 or 504; this recipe treats every failure of the read as the source not answering.
- name: Record how to connect to edge01 and dr01
  # The reads below take their connections from cluster_baseline_connections, which this include records first.
  ansible.builtin.include_role:
    name: qumulo.cluster.cluster_baseline
    tasks_from: connections.yml
  vars:
    cluster_baseline_connection_hosts: [edge01, dr01]

- name: Read the source's relationships, which may not answer
  qumulo.cluster.replication_info:
    gather_subset: [source, target]
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['edge01'] }}"
  register: edge01_replication
  failed_when: false

- name: Read the target's relationships
  qumulo.cluster.replication_info:
    gather_subset: [source, target]
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['dr01'] }}"
  register: dr01_replication

- name: Plan the failover of edge01's projects to dr01
  qumulo.cluster.replication_failover_plan:
    action: failover
    relationship: {source_path: /projects, target: dr01, target_path: /replicas/edge01-projects}
    source_host: edge01
    target_host: dr01
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    source_relationships: "{{ edge01_replication.replication | default(omit) }}"
    source_error: "{{ edge01_replication.msg | default(omit) }}"
    target_relationships: "{{ dr01_replication.replication }}"
  register: failover_plan

- name: Show every step left
  # A second run, once dr01 is writable, lists no step, and done is true.
  ansible.builtin.debug:
    msg: "{{ failover_plan.steps | map(attribute='text') | list }}"

- name: Read edge01's tenants
  qumulo.cluster.tenant_info:
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['edge01'] }}"
  register: edge01_tenants

- name: Read edge01's network document
  qumulo.cluster.network_info:
    gather_subset: [document]
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['edge01'] }}"
  register: edge01_networks

- name: Read dr01's tenants
  qumulo.cluster.tenant_info:
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['dr01'] }}"
  register: dr01_tenants

- name: Read dr01's network document
  qumulo.cluster.network_info:
    gather_subset: [document]
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['dr01'] }}"
  register: dr01_networks

- name: Plan the failback of edge01's projects with both addresses and both clusters' tenants
  # A resync's reverse needs the source's address, and a failback's the target's, as an IP literal, and each reverse
  # needs the tenants and the network document of the cluster that becomes the target. A failback takes both clusters'
  # when it runs the resync's steps first. The source's relationships come from the reads at the top of these examples.
  qumulo.cluster.replication_failover_plan:
    action: failback
    relationship: {source_path: /projects, target: dr01, target_path: /replicas/edge01-projects}
    source_host: edge01
    target_host: dr01
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    source_replication_address: 203.0.113.10
    target_replication_address: 203.0.113.40
    source_relationships: "{{ edge01_replication.replication }}"
    target_relationships: "{{ dr01_replication.replication }}"
    source_tenancy: {tenants: "{{ edge01_tenants.tenants }}", document: "{{ edge01_networks.network.document }}"}
    target_tenancy: {tenants: "{{ dr01_tenants.tenants }}", document: "{{ dr01_networks.network.document }}"}
  register: failback_plan
"""

RETURN = r"""
state:
  description:
    - The relationship's state, one of V(replicating), V(failing over), V(failed over), V(reconnecting), V(reversed),
      V(resyncing), V(resynced), V(failing back, source disconnecting), V(failing back, source writable) and
      V(failing back, reversed).
  returned: success
  type: str
  sample: failed over
direction:
  description: V(declared) when the target holds the target record, V(reversed) when it holds a source record.
  returned: success
  type: str
  sample: declared
relationship_id:
  description: The relationship's id, which both records share and a reverse keeps.
  returned: success
  type: str
  sample: 9465828e-7dcf-466e-a550-78ed0bd6b79b
recovery_point:
  description: The target's record's recovery point, or V(null) before the first job.
  returned: success
  type: str
  sample: "2026-11-08T06:26:34.190479565Z"
done:
  description: Whether the action has nothing left to do.
  returned: success
  type: bool
  sample: false
step:
  description: The next step, the first of RV(steps), or V(null) when RV(done).
  returned: success
  type: dict
  sample:
    name: make_writable_target
    cluster: target
    host: dr01
    kind: verb
    verb: make_writable
    relationship_id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
    polls: [{cluster: target, side: target, until: [DISCONNECTED]}]
    text: >-
      dr01: make /replicas/edge01-projects writable. It goes back to the recovery point
      2026-11-08T06:26:34.190479565Z; writes on edge01 after it stay behind on edge01
steps:
  description:
    - Every step left, in order. Each has C(name), C(cluster), V(source) or V(target), the inventory role of the cluster
      it runs on, C(host), that cluster's inventory host, C(kind), V(verb), V(poll), V(sync) or V(settings),
      C(relationship_id) and C(text), its line in the plan.
    - A V(verb) step has C(verb), the M(qumulo.cluster.replication_reverse) verb, C(polls), the records to wait for
      after it, and for V(reverse) C(source_address) and C(source_port). A V(poll) step has C(polls), each C(cluster),
      C(side) and C(until), the states that end it. A V(sync) step has C(recovery_point), the value the wait counts
      moves past. A V(settings) step has C(options), for M(qumulo.cluster.replication_relationship).
  returned: success
  type: list
  elements: dict
  sample:
    - name: make_writable_target
      cluster: target
      host: dr01
      kind: verb
      verb: make_writable
      relationship_id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
      polls: [{cluster: target, side: target, until: [DISCONNECTED]}]
      text: >-
        dr01: make /replicas/edge01-projects writable. It goes back to the recovery point
        2026-11-08T06:26:34.190479565Z; writes on edge01 after it stay behind on edge01
notes:
  description: What the module noticed, such as a source that did not answer.
  returned: success
  type: list
  elements: str
  sample:
    - edge01 did not answer (the cluster did not answer); the plan goes on from dr01's record alone
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.replication_failover import ACTIONS, plan

ARGUMENT_SPEC = {
    "action": {"type": "str", "choices": list(ACTIONS), "required": True},
    "relationship": {"type": "dict", "required": True},
    "source_host": {"type": "str", "required": True},
    "target_host": {"type": "str", "required": True},
    "source_cluster_uuid": {"type": "str", "required": True},
    "target_cluster_uuid": {"type": "str", "required": True},
    "source_replication_address": {"type": "str"},
    "source_replication_port": {"type": "int", "default": 3712},
    "target_replication_address": {"type": "str"},
    "target_replication_port": {"type": "int", "default": 3712},
    "source_relationships": {"type": "dict"},
    "source_error": {"type": "str"},
    "target_relationships": {"type": "dict", "required": True},
    "source_tenancy": {"type": "dict"},
    "target_tenancy": {"type": "dict"},
    "source_previous_cluster_uuids": {"type": "list", "elements": "str", "default": []},
    "last_verb": {"type": "str"},
    "allow_partial_job": {"type": "bool", "default": False},
    "newer_writes_hosts": {"type": "list", "elements": "str", "default": []},
    "synced": {"type": "bool", "default": False},
}


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        result = plan(module.params)
    except QumuloError as e:
        module.fail_json(msg=str(e))
    for warning in result.pop("warnings"):
        module.warn(warning)
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
