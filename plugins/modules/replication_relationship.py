#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_relationship
short_description: Declare a replication relationship on its source cluster, or delete the source's records of it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Runs on the source cluster. A create registers the relationship on the target too, awaiting authorization there,
    which M(qumulo.cluster.replication_target) gives. The replication roles pair both sides.
  - The relationship is identified by O(source_path), O(target_cluster_uuid) and O(target_path), never by a server id
    or by the address. Paths compare without a trailing slash, and UUIDs without regard to case.
  - O(target_address) and O(target_port) are settings, like O(replication_enabled), O(map_local_ids_to_nfs_ids),
    O(source_root_read_only), O(blackout_windows) and O(blackout_window_timezone). One PATCH, with If-Match from the
    relationship's configuration, sends only those that differ. The server refuses an address that reaches another
    cluster.
  - C(replication_mode) and C(snapshot_policies) are never compared or sent, so an adopted relationship keeps them.
  - A create is never sent twice. A create whose answer is lost is reconciled by listing, and two records of the key
    fail, naming both ids.
  - After a create, the module reads the new record. When the address reached a cluster other than
    O(target_cluster_uuid), it deletes the record at once and fails, naming the address and the UUID it reached.
  - A record with the same paths and another target UUID is a changed peer when it is at the declared address and port,
    or when its UUID is in O(previous_target_cluster_uuids), at any address. With O(recreate_changed_peer), the module
    creates the new relationship first, checks the UUID it reaches, and only then deletes the changed peer; without
    it, it fails. A changed peer beside a record of the key is deleted with no create. A record at the declared address
    whose UUID is in O(inventory_cluster_uuids) is never a changed peer; it fails, since the address reaches the wrong
    cluster.
  - Before any write the module evaluates the rows of the overwrite table that fall to the source, and refuses while
    any of them is unmet and not lifted, reporting every unmet one in RV(unmet). O3, an ENDED record of the key with
    no live one, needs O(recreate) and a release bound to that record; the real run then creates, reads the target UUID
    back, and deletes the ENDED record. O8, this cluster holding a target record from the declared target at a path
    equal to or nested with the source path, is a reversal, a failover state that nothing here lifts.
  - An ENDED record of the key beside a live one, as a prune on the target leaves after a repeated create, holds
    nothing; the real run deletes it.
  - A missing source directory is unmet, and in check mode pending with O(source_path_pending).
  - In a failover state (C(DISCONNECTING), C(DISCONNECTED), C(RECONNECTING), C(REESTABLISHING)) the module changes
    nothing in the relationship's state, still PATCHes its settings, and warns.
  - With O(state=absent), deletes on this cluster the record of the key, in any state, and every record with the same
    paths whose target UUID is in O(previous_target_cluster_uuids). The target keeps its record, and its directory its
    data, until M(qumulo.cluster.replication_target) with O(state=absent) runs there.
options:
  source_path:
    description: The source directory on this cluster.
    type: str
    required: true
  target_cluster_uuid:
    description:
      - UUID of the target cluster. It is part of the key, so a target rebuilt with a new UUID makes a new
        relationship, and the old record is a changed peer.
    type: str
    required: true
  target_path:
    description: The target directory on the target cluster.
    type: str
    required: true
  target_address:
    description:
      - The IP address at which this cluster reaches the target's replication port, such as a floating IP of the
        target, or behind NAT the NAT's outside address. The server takes an IP literal only.
      - Required with O(state=present).
    type: str
  target_port:
    description: The target's replication port as this cluster reaches it.
    type: int
    default: 3712
  replication_enabled:
    description: Whether the relationship replicates. Unset keeps the current value, and a create leaves the server's default, true.
    type: bool
  map_local_ids_to_nfs_ids:
    description: Whether local user and group ids are mapped to NFS ids on the target. Unset keeps the current value.
    type: bool
  source_root_read_only:
    description: Whether the source directory is read-only while the relationship exists. Unset keeps the current value.
    type: bool
  blackout_windows:
    description:
      - When replication pauses, in O(blackout_window_timezone), which they need. Unset keeps the current windows, and
        an empty list removes them all.
    type: list
    elements: dict
    suboptions:
      start_hour:
        description: The hour the window starts, 0 to 23.
        type: int
        required: true
      start_minute:
        description: The minute the window starts, 0 to 59.
        type: int
        required: true
      end_hour:
        description: The hour the window ends, 0 to 23.
        type: int
        required: true
      end_minute:
        description: The minute the window ends, 0 to 59.
        type: int
        required: true
      on_days:
        description: The days the window applies on. V(EVERY_DAY) reads back as all seven.
        type: list
        elements: str
        choices: [EVERY_DAY, SUN, MON, TUE, WED, THU, FRI, SAT]
        required: true
  blackout_window_timezone:
    description:
      - The timezone of O(blackout_windows), such as V(America/Los_Angeles). Unset keeps the current timezone.
      - A timezone cannot be cleared once set (QUIRK-019), so an empty value fails while one is set.
    type: str
  recreate:
    description:
      - With a release bound to it, lets the module replace an ENDED record of the key, the only one, with a new
        relationship (row O3). The ENDED record is deleted once the new one is shown to reach O(target_cluster_uuid).
    type: bool
    default: false
  recreate_changed_peer:
    description:
      - Replace a changed peer, create first. The replication roles pass it; without it, a changed peer fails.
    type: bool
    default: false
  create:
    description:
      - With V(false), the module only deletes, and creates and PATCHes nothing. It deletes the changed peers whose
        UUID is in O(previous_target_cluster_uuids) and that are at the declared address, or whose last job reports
        that it cannot establish a secure connection. Any other changed peer is left for a later create-first
        replacement.
      - The replication roles pass V(false) while they defer a relationship.
    type: bool
    default: true
  previous_target_cluster_uuids:
    description:
      - UUIDs the target cluster had before a rebuild. Records with the same paths and one of these UUIDs are changed
        peers at any address, and O(state=absent) deletes them too.
      - It must not hold O(target_cluster_uuid).
    type: list
    elements: str
    default: []
  inventory_cluster_uuids:
    description:
      - The UUIDs of the other clusters in the inventory. A record whose target UUID is one of them is never a changed
        peer, and at the declared address it fails, since the address reaches another inventory cluster.
    type: list
    elements: str
    default: []
  release:
    description:
      - Release entries for this relationship, each C(<target host>:<target path>@<record id>:<rows>). An entry naming
        O3 and bound to an ENDED record of the key lifts O3 for that record, with O(recreate).
      - An entry for another target path fails.
    type: list
    elements: str
    default: []
  source_path_pending:
    description:
      - In check mode, a missing source directory is pending, since an earlier step of the run creates it, and counts as
        empty. Outside check mode a missing source directory always fails.
    type: bool
    default: false
  relationship_id:
    description:
      - The id a check run of this module returned as RV(read_by_id). The module then reads only that record, instead of
        listing this cluster's relationships, and fails when it is gone or ENDED; it never falls back to a create.
      - Only with O(state=present).
    type: str
  state:
    description: Whether the relationship exists on this cluster.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Evaluates the source's rows of the overwrite table as the real run does, and fails on the same unmet rows. It
        predicts a create with a null id and RV(creates).
  diff_mode:
    support: full
    details:
      - The diff's before and after each hold C(relationship), in the shape of RV(relationship), and C(records), the
        records the task deletes, or in check mode would delete. After's C(records) is always empty.
  idempotent:
    support: partial
    details:
      - A create cannot be sent again; the module matches by key first and reconciles a lost create by listing
        (G-REPL-ONESHOT).
      - A target rebuilt with a new UUID leaves the old record pinned to its certificate, never ENDED (G-REPL-PEER).
notes:
  - "API: GET /v2/replication/source-relationships/status/, GET and PATCH and DELETE
    /v2/replication/source-relationships/{id}, GET /v2/replication/source-relationships/{id}/status,
    POST /v2/replication/source-relationships/, GET /v2/replication/target-relationships/status/ for row O8,
    GET /v1/files/{ref}/info/attributes, GET /v1/files/{ref}/entries/, GET /v1/files/{ref}/aggregates/,
    GET /v4/snapshots/status/."
  - Idempotency gaps G-REPL-ONESHOT and G-REPL-PEER apply. A create to an address that reaches another cluster leaves an
    ENDED record there, which this module cannot reach (G-REPL-ONESHOT).
  - The source tells the target of a delete only once, so a removal deletes here and then on the target
    (G-REPL-NOTIFY). The target's last recovery-point snapshot outlives the removal, owned by nothing
    (G-REPL-SNAPSHOT); M(qumulo.cluster.replication_target) names it.
  - A blackout timezone cannot be cleared (G-REPL-TZ, QUIRK-019), so blackout windows without a timezone, and an empty
    timezone where one is set, fail before any write.
  - The source reports the target path as it was sent until it first polls the target, and then with a trailing slash
    (QUIRK-020); the module compares and reports paths without it.
  - Files owned by local users and groups other than the built-in ones replicate as NFS ids, so they need
    O(map_local_ids_to_nfs_ids) and an NFS uid or gid on each, and come back from a replica owned by those ids, which
    reach the same user or group only through a matching uid or gid (G-REPL-IDMAP).
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Preview the relationship to dr01 without writing, with the refusals it would meet
  # A refusal still returns unmet, so the preview does not fail the play.
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    replication_enabled: true
    blackout_windows:
      - {start_hour: 8, start_minute: 0, end_hour: 18, end_minute: 0, on_days: [MON, TUE, WED, THU, FRI]}
    blackout_window_timezone: America/Los_Angeles
    previous_target_cluster_uuids: "{{ hostvars['dr01'].qumulo_previous_cluster_uuids | default([]) }}"
    inventory_cluster_uuids: "{{ groups['qumulo_clusters'] | map('extract', hostvars, 'qumulo_cluster_uuid') | list }}"
    recreate_changed_peer: true
  check_mode: true
  diff: true
  register: preview
  failed_when: false

- name: Show what would keep the relationship from being created
  ansible.builtin.debug:
    msg: "{{ preview.unmet | map(attribute='message') | list }}"

- name: Stop when the preview refused the relationship
  ansible.builtin.fail:
    msg: "{{ preview.msg }}"
  when: preview.msg is defined

- name: Create the relationship to dr01, or adopt one made by hand at another address of dr01 with one PATCH
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    replication_enabled: true
    blackout_windows:
      - {start_hour: 8, start_minute: 0, end_hour: 18, end_minute: 0, on_days: [MON, TUE, WED, THU, FRI]}
    blackout_window_timezone: America/Los_Angeles
    previous_target_cluster_uuids: "{{ hostvars['dr01'].qumulo_previous_cluster_uuids | default([]) }}"
    inventory_cluster_uuids: "{{ groups['qumulo_clusters'] | map('extract', hostvars, 'qumulo_cluster_uuid') | list }}"
    recreate_changed_peer: true
  register: created

- name: Show what the target's authorize needs from the source
  ansible.builtin.debug:
    msg: >-
      id {{ created.relationship.id }}, source_empty {{ created.source_empty }}, source_files {{ created.source_files }},
      source_bytes {{ created.source_bytes }}

- name: Apply the relationship, reading only the record the preview returned
  # read_by_id is null when the preview predicted a create or a delete, and the module then lists as usual.
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    relationship_id: "{{ preview.read_by_id }}"

- name: Preview a change of the blackout windows
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    blackout_windows:
      - {start_hour: 22, start_minute: 0, end_hour: 23, end_minute: 59, on_days: [EVERY_DAY]}
    blackout_window_timezone: America/Los_Angeles
  check_mode: true
  diff: true

- name: Remove all blackout windows of the relationship
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    blackout_windows: []

- name: Pause replication without deleting the relationship
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    replication_enabled: false

- name: Replicate files owned by local users as NFS ids
  # Each such local user and group needs an NFS uid or gid (G-REPL-IDMAP).
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    map_local_ids_to_nfs_ids: true

- name: Keep the source directory read-only while the relationship exists
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    source_root_read_only: true

- name: Move the relationship to a floating IP of the same target, one PATCH
  # Prefer a floating IP of the target: it stays reachable when a node of the target goes down.
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: 203.0.113.41

- name: Replace a relationship the operator ended, with the release its refusal printed
  # Pass the release with -e for this run only, never in the inventory.
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    recreate: true
    release: >-
      {{ qumulo_replication_release | default([])
         | select('match', 'dr01:/replicas/edge01-projects/?@') | list }}

- name: Delete the records of dr01's previous UUIDs that can no longer replicate, and create nothing
  # Only those at the declared address, or whose last job cannot establish a secure connection; the rest are left for a
  # create-first replacement.
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    previous_target_cluster_uuids: "{{ hostvars['dr01'].qumulo_previous_cluster_uuids }}"
    create: false

- name: Preview the relationship before the play creates the source directory
  qumulo.cluster.replication_relationship:
    source_path: /projects/new
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects-new
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    source_path_pending: true
  check_mode: true
  diff: true

- name: Remove the relationship on the source, and the records of the target's previous UUIDs
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    previous_target_cluster_uuids: "{{ hostvars['dr01'].qumulo_previous_cluster_uuids | default([]) }}"
    state: absent
"""

RETURN = r"""
relationship:
  description:
    - The source's record of the key after the task, in the shape M(qumulo.cluster.replication_info) reports both sides
      in; empty after O(state=absent), and with O(create=false) when there is none.
    - In check mode, the predicted record; for a create, its id is null and its state C(AWAITING_AUTHORIZATION).
  returned: always
  type: dict
  contains:
    id:
      description: The relationship's id, which both sides share.
      type: str
    source_cluster_uuid:
      description: UUID of this cluster.
      type: str
    target_cluster_uuid:
      description: UUID of the cluster the address reaches.
      type: str
    source_path:
      description: The source directory, without a trailing slash.
      type: str
    target_path:
      description: The target directory, without a trailing slash.
      type: str
    target_address:
      description: The address this cluster reaches the target at.
      type: str
    target_port:
      description: The port this cluster reaches the target at.
      type: int
    state:
      description: V(AWAITING_AUTHORIZATION), V(ESTABLISHED), a failover state, or V(ENDED).
      type: str
    end_reason:
      description: Why an ENDED relationship ended.
      type: str
    job_state:
      description: V(REPLICATION_RUNNING) or V(REPLICATION_NOT_RUNNING).
      type: str
    job_start_time:
      description: When the running job started; null when no job runs.
      type: str
    recovery_point:
      description: When the last job that completed started; null before the first.
      type: str
    recovery_point_snapshot_id:
      description: The id of the snapshot that holds the recovery point on this cluster; null when the record names none.
      type: int
    error_from_last_job:
      description: The last job's error, or null.
      type: str
    target_root_read_only:
      description: Whether the target directory is read-only.
      type: bool
    replication_enabled:
      description: Whether the relationship replicates.
      type: bool
    map_local_ids_to_nfs_ids:
      description: Whether local ids are mapped to NFS ids.
      type: bool
    source_root_read_only:
      description: Whether the source directory is read-only.
      type: bool
    blackout_windows:
      description: The blackout windows, with their days in the server's order, Sunday first.
      type: list
      elements: dict
    blackout_window_timezone:
      description: The timezone of the blackout windows; empty when none was ever set.
      type: str
  sample:
    id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
    source_cluster_uuid: 9533a9e2-e57a-454f-8aa0-9c77b706b7bd
    target_cluster_uuid: 59052546-452f-483e-a800-0a925d3fa1bd
    source_path: /projects
    target_path: /replicas/edge01-projects
    target_address: 203.0.113.40
    target_port: 3712
    state: ESTABLISHED
    end_reason: null
    job_state: REPLICATION_NOT_RUNNING
    job_start_time: null
    recovery_point: "2026-11-01T08:21:54.728656093Z"
    recovery_point_snapshot_id: 117
    error_from_last_job: null
    target_root_read_only: true
    replication_enabled: true
    map_local_ids_to_nfs_ids: false
    source_root_read_only: false
    blackout_windows:
      - {start_hour: 8, start_minute: 0, end_hour: 18, end_minute: 0, on_days: [MON, TUE, WED, THU, FRI]}
    blackout_window_timezone: America/Los_Angeles
creates:
  description:
    - Whether the task creates a relationship, or in check mode would, a replacement of a changed peer or of an ENDED
      record included. The replication roles then run the target's check with C(source_creates).
  returned: always
  type: bool
read_by_id:
  description:
    - This module's own hand-off from a check run to the real run that follows it, given back as O(relationship_id),
      so that run reads that record alone. Null after a create, or when a delete is due, since that run must then list
      this cluster's relationships again.
    - It is not the id for M(qumulo.cluster.replication_target); pass that module's
      O(qumulo.cluster.replication_target#module:relationship_id) the RV(relationship.id) a run of this module returns.
  returned: always
  type: str
unmet:
  description:
    - Every condition that keeps the task from writing, lifted or not, other conditions first and then the overwrite
      table's rows. Empty with O(state=absent); with O(create=false), only misdirected records. A run that fails on one
      still returns the list.
    - Such a refusal, before any write, returns RV(unmet), RV(releases), RV(source_empty), RV(source_files) and
      RV(source_bytes), and none of RV(relationship), RV(creates), RV(read_by_id), RV(deleted) and RV(snapshots).
  returned: always
  type: list
  elements: dict
  contains:
    row:
      description: >-
        The overwrite table's row, V(O3) or V(O8), or another condition: V(source_missing), V(changed_peer) or
        V(misdirected).
      type: str
    message:
      description: What is wrong, and what would lift it.
      type: str
    binds_to:
      description: For O3, the id of the ENDED record a release entry must carry; null otherwise.
      type: str
    lifted_by:
      description: What can lift it, such as V(recreate and release) or V(recreate_changed_peer); null when nothing can.
      type: str
    lifted:
      description: Whether this run's options or release lift it.
      type: bool
    pending:
      description: Whether it is a check-mode prediction that an earlier step of the run meets.
      type: bool
    facts:
      description: The facts behind it, such as the record's id, target UUID, address and state.
      type: dict
releases:
  description:
    - The release entries to pass, one per ENDED record whose O3 no release lifts, each with C(target_path),
      C(record_id) and C(rows). Prefix the target host's inventory name to pass one.
  returned: always, and on a refusal before any write
  type: list
  elements: dict
  contains:
    target_path:
      description: The target path of the ENDED record's relationship.
      type: str
    record_id:
      description: The id of the ENDED record, which the release entry binds to.
      type: str
    rows:
      description: Every row reported on that record.
      type: list
      elements: str
  sample:
    - target_path: /replicas/edge01-projects
      record_id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
      rows: [O3]
source_empty:
  description:
    - Whether the source directory is missing or holds no file, link or other object at any depth, decided from its
      entries; a directory holding only directories is empty. Null when the relationship is live, since the target then
      needs no authorize.
  returned: always, and on a refusal before any write
  type: bool
source_files:
  description: The source directory's count of files, links and other objects, from its aggregates; null as for RV(source_empty).
  returned: always, and on a refusal before any write
  type: int
source_bytes:
  description: The source directory's C(total_capacity) in bytes, from its aggregates; null as for RV(source_empty).
  returned: always, and on a refusal before any write
  type: int
deleted:
  description:
    - The records the task deleted, or in check mode would delete, in the shape of RV(relationship), in the order it
      deletes them. A replacement deletes the old record only after its create.
  returned: always
  type: list
  elements: dict
snapshots:
  description:
    - The snapshots the deleted records owned that are still there after their delete, each with C(id) and C(name).
      The source's delete normally removes its replication snapshot, so this is normally empty. Empty in check mode.
  returned: always
  type: list
  elements: dict
  contains:
    id:
      description: The snapshot's id.
      type: int
    name:
      description: The snapshot's name.
      type: str
notes:
  description: What the module noticed, such as a record deleted as a leftover, or a delete already done.
  returned: when there is something to note
  type: list
  elements: str
maybe_applied:
  description:
    - True when a create, a PATCH of the settings or a delete was sent and the module could not confirm it, so the next run reads the cluster
      and decides. The module never resends it.
  returned: on a failure after a lost answer
  type: bool
expected:
  description: The settings the task wrote, for those that did not read back as written.
  returned: on a failure when the read-back after a create or a PATCH differs from what was written
  type: dict
  sample: {blackout_window_timezone: America/Los_Angeles}
observed:
  description: What the source record read back for the keys of RV(expected).
  returned: on a failure when the read-back after a create or a PATCH differs from what was written
  type: dict
  sample: {blackout_window_timezone: UTC}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.replication_source import ReplicationRelationship

WINDOW = {
    "start_hour": {"type": "int", "required": True},
    "start_minute": {"type": "int", "required": True},
    "end_hour": {"type": "int", "required": True},
    "end_minute": {"type": "int", "required": True},
    "on_days": {"type": "list", "elements": "str", "required": True, "choices": ["EVERY_DAY", "SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
}

ARGUMENT_SPEC = {
    "source_path": {"type": "str", "required": True},
    "target_cluster_uuid": {"type": "str", "required": True},
    "target_path": {"type": "str", "required": True},
    "target_address": {"type": "str"},
    "target_port": {"type": "int", "default": 3712},
    "replication_enabled": {"type": "bool"},
    "map_local_ids_to_nfs_ids": {"type": "bool"},
    "source_root_read_only": {"type": "bool"},
    "blackout_windows": {"type": "list", "elements": "dict", "options": WINDOW},
    "blackout_window_timezone": {"type": "str"},
    "recreate": {"type": "bool", "default": False},
    "recreate_changed_peer": {"type": "bool", "default": False},
    "create": {"type": "bool", "default": True},
    "previous_target_cluster_uuids": {"type": "list", "elements": "str", "default": []},
    "inventory_cluster_uuids": {"type": "list", "elements": "str", "default": []},
    "release": {"type": "list", "elements": "str", "default": []},
    "source_path_pending": {"type": "bool", "default": False},
    "relationship_id": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC, required_if=[("state", "present", ["target_address"])]), ReplicationRelationship)


if __name__ == "__main__":
    main()
