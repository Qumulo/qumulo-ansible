#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_reverse
short_description: Make a replication target writable, reverse a relationship, or reconnect it, by its id
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sends one failover verb for the replication relationship O(relationship_id), on the cluster the verb goes to.
  - V(make_writable), on the relationship's target, from ESTABLISHED with a recovery point. The target directory goes
    back to the recovery point and becomes writable, and the relationship goes DISCONNECTED on both sides.
  - >-
    V(reverse), on the current target, from DISCONNECTED. This cluster's target record becomes a source record of the
    same id, to the current source at O(source_address) and O(source_port), and the current source's record becomes a
    target record. Both stay DISCONNECTED. This cluster's directory stays writable from the make-writable. The current
    source's directory keeps its read-only state: it stays read-only after the reverse while another relationship's
    record holds it, until that record's delete, and when C(source_root_read_only) made it read-only.
  - V(reconnect), on the relationship's target, from DISCONNECTED; after a reverse, that is the old source. The target
    directory goes back to the recovery point and becomes read-only again, and the relationship goes RECONNECTING, then
    ESTABLISHED once the source reaches this cluster.
  - The module reads the record first and sends the verb only from that state. A record already in the state the verb
    leaves is reported unchanged, and any other state is refused before any write.
  - It never waits. It reads the record once after the verb and returns it.
  - A verb whose answer is lost is reconciled by reading the record, and never sent again. When the record shows no
    sign of it, the module fails with C(maybe_applied) set, and the next run reads the record and decides. A refusal
    from the server, or from the module, fails with nothing changed.
  - A lost answer to the comparison's snapshot, which a V(reconnect) without O(allow_data_loss) takes first, fails with
    C(changed) true and without C(maybe_applied). Only that snapshot may exist, and it expires a day after it was taken.
    Nothing was sent to the relationship, so the next run compares and reconnects.
options:
  relationship_id:
    description: The id of the relationship, which both sides share, and a reverse keeps.
    type: str
    required: true
  verb:
    description: The verb to send.
    type: str
    choices: [make_writable, reverse, reconnect]
    required: true
  allow_data_loss:
    description:
      - Let V(make_writable) or V(reconnect) send the target directory back to the relationship's recovery point, which
        discards whatever was written there after it.
      - V(make_writable) fails without it, before any request.
      - V(reconnect) without it first compares the target directory with the recovery point's snapshot, through a
        snapshot of the directory that the module deletes once it has compared, and sends the reconnect only when
        nothing changed since. The snapshot is set to expire a day after the controller's clock time, should the module
        stop before it deletes it.
      - A V(reconnect) without it that finds changes fails with nothing changed, naming their count and the first ten,
        and returns all of them, up to 1000, in RV(changes) and their count in RV(change_count). It also fails, with no
        change returned, when the record names no recovery point snapshot of the target directory. With O(allow_data_loss)
        the reconnect is sent without comparing.
      - V(reverse) takes no consent, since it moves the records and changes no data.
    type: bool
    default: false
  source_address:
    description:
      - For V(reverse), the IP address at which this cluster reaches the current source. It becomes the target address
        of the new source record.
      - Required with O(verb=reverse).
    type: str
  source_port:
    description: For V(reverse), the current source's replication port.
    type: int
    default: 3712
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Reads the record, refuses what the real run refuses, and returns the record the verb is predicted to leave.
      - A V(reconnect) without O(allow_data_loss) takes no snapshot, so check mode cannot compare; it notes that the
        real run compares.
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - No verb can be resent, so each is sent only from its own state; a record already in the state the verb leaves
        reports no change, and a lost answer is reconciled by reading (G-REPL-ONESHOT).
notes:
  - "API: GET /v2/replication/target-relationships/{id}/status, GET /v2/replication/source-relationships/{id}/status,
    POST /v2/replication/target-relationships/{id}/make-writable, POST
    /v2/replication/source-relationships/reverse-target-relationship, POST
    /v2/replication/target-relationships/{id}/reconnect; the reconnect's comparison: GET
    /v2/replication/target-relationships/{id}/status for the raw record, POST /v3/snapshots/, GET
    /v2/snapshots/{newer_id}/changes-since/{older_id}, DELETE /v3/snapshots/{id}."
  - The verbs are one-shot, so the module reconciles a lost answer by reading the record (G-REPL-ONESHOT).
  - The reconnect's comparison creates a snapshot of the target directory and deletes it, and reads the changes between
    two snapshots, so the user needs rights to create and delete snapshots and to read their differences.
  - A make-writable and a reverse leave replication snapshots owned by nothing, which never expire and which
    M(qumulo.cluster.snapshot_info) lists (G-REPL-SNAPSHOT).
  - A reverse carries the id, the paths, the address and C(replication_enabled); the blackout windows, their timezone,
    C(map_local_ids_to_nfs_ids) and C(source_root_read_only) come back at their defaults.
  - V(reconnect) is refused with HTTP 409 C(relationship_manager_target_contains_another_relationship_error) while
    another record still holds this cluster's directory.
  - An already reversed relationship is recognised by this cluster's source record of the id going to
    O(source_address) and O(source_port); a source record to another address is the current source, and refused.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Preview the make-writable, with the record before and the record it would leave
  # Run against the cluster that holds the relationship's target record.
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: make_writable
    allow_data_loss: true
  check_mode: true
  diff: true

- name: Make the edge's copy writable, discarding writes after the recovery point
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: make_writable
    allow_data_loss: true

- name: Wait until the edge's record reads DISCONNECTED
  # The module never waits, so poll the record the verb leaves.
  qumulo.cluster.replication_info:
    relationship_id: "{{ restore_id }}"
    side: target
  register: edge_record
  until: edge_record.replication.relationship.state | default('') == 'DISCONNECTED'
  retries: 60
  delay: 5

- name: Preview the reverse toward the hub's replication address
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: reverse
    source_address: "{{ hostvars['hub01'].qumulo_replication_address }}"
    source_port: "{{ hostvars['hub01'].qumulo_replication_port | default(3712) }}"
  check_mode: true
  diff: true

- name: Reverse the relationship on the edge, toward the hub's replication address
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: reverse
    source_address: "{{ hostvars['hub01'].qumulo_replication_address }}"
    source_port: "{{ hostvars['hub01'].qumulo_replication_port | default(3712) }}"

- name: Preview the reconnect that reverts the hub's directory to the recovery point
  # Run against the hub, the new target, after the record that held its directory is deleted.
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: reconnect
    allow_data_loss: true
  check_mode: true
  diff: true

- name: Reconnect, which reverts the hub's directory to the recovery point
  # Run against the hub, the new target, after the record that held its directory is deleted.
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: reconnect
    allow_data_loss: true

- name: Reconnect the edge only when nothing was written in its directory after the recovery point
  # Run against the edge, the target after a reverse on the hub. Without allow_data_loss the module compares the
  # directory with the recovery point's snapshot first, and fails, naming the changes, when it would discard any.
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ restore_id }}"
    verb: reconnect

- name: Wait until the hub's record reads ESTABLISHED
  # Run against the hub, the relationship's target after the reverse.
  qumulo.cluster.replication_info:
    relationship_id: "{{ restore_id }}"
    side: target
  register: hub_record
  until: hub_record.replication.relationship.state | default('') == 'ESTABLISHED'
  retries: 60
  delay: 5

- name: Apply the declared blackout windows to the relationship the reverse returned to its defaults
  # Run against the edge, which holds the relationship's source record again.
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['hub01'].qumulo_replication_address }}"
    blackout_windows:
      - {start_hour: 8, start_minute: 0, end_hour: 18, end_minute: 0, on_days: [MON, TUE, WED, THU, FRI]}
    blackout_window_timezone: America/Los_Angeles
"""

RETURN = r"""
relationship:
  description:
    - This cluster's record of the relationship after the verb, read once, in the shape
      M(qumulo.cluster.replication_info) reports both sides in. After V(reverse) it is the new source record; after the
      other verbs, the target record, whose address and settings are null.
    - It may still read the state before the verb's, since the server moves on by itself; the module does not wait.
    - In check mode, the record the verb is predicted to leave.
  returned: always
  type: dict
  sample:
    id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
    source_cluster_uuid: 9533a9e2-e57a-454f-8aa0-9c77b706b7bd
    target_cluster_uuid: 59052546-452f-483e-a800-0a925d3fa1bd
    source_path: /replicas/edge01-projects
    target_path: /projects
    target_address: null
    target_port: null
    state: DISCONNECTED
    end_reason: null
    job_state: REPLICATION_NOT_RUNNING
    job_start_time: null
    recovery_point: "2026-11-01T08:21:54.728656093Z"
    recovery_point_snapshot_id: 64
    error_from_last_job: null
    target_root_read_only: false
    replication_enabled: null
    map_local_ids_to_nfs_ids: null
    source_root_read_only: null
    blackout_windows: null
    blackout_window_timezone: null
notes:
  description: What the module noticed, such as a verb found already done, or a lost answer reconciled.
  returned: when there is something to note
  type: list
  elements: str
changes:
  description:
    - Each change in the target directory since the recovery point, with C(op) and C(path), as the snapshot comparison
      lists it.
  returned: when a V(reconnect) without O(allow_data_loss) is refused because the target directory changed
  type: list
  elements: dict
  sample: [{op: MODIFY, path: /projects/plan.txt}]
change_count:
  description:
    - How many changes the target directory holds since the recovery point. V(null) when there are more than 1000, which
      RV(changes) then lists the first 1000 of.
  returned: when a V(reconnect) without O(allow_data_loss) is refused because the target directory changed
  type: int
  sample: 3
maybe_applied:
  description:
    - True when a verb was sent and the module could not confirm it, so the next run reads the record and decides. The
      module never resends it. A lost answer to the comparison's snapshot does not set it, since no verb was sent.
  returned: on a failure after a lost answer
  type: bool
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.replication_reverse import ReplicationReverse

ARGUMENT_SPEC = {
    "relationship_id": {"type": "str", "required": True},
    "verb": {"type": "str", "choices": ["make_writable", "reverse", "reconnect"], "required": True},
    "allow_data_loss": {"type": "bool", "default": False},
    "source_address": {"type": "str"},
    "source_port": {"type": "int", "default": 3712},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC, required_if=[("verb", "reverse", ["source_address"])]), ReplicationReverse)


if __name__ == "__main__":
    main()
