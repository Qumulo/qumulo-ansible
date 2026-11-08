#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_target
short_description: Authorize a replication relationship on its target cluster, or delete the target's records of it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Runs on the target cluster. The source's create registers a relationship here, awaiting authorization; this module
    authorizes it, which makes the target directory read-only and lets the source's content replace the directory's.
    The replication roles pair both sides.
  - The relationship is identified by O(source_cluster_uuid), O(source_path) and O(target_path), never by a server id
    alone. Paths compare without a trailing slash, and UUIDs without regard to case.
  - With O(relationship_id), the id the source's create returned, the module reads that record before anything else,
    apart from the check of O(replication_address) when that is set. An ESTABLISHED record, or one in a failover
    state, returns at once without listing the target's relationships or looking at the directory.
  - Before an authorize, the module evaluates the rows of the overwrite table that fall to the target, and refuses
    before any write while any of them is unmet and not lifted. Every unmet row is reported at once in RV(unmet), in
    this order. O7, a live relationship at a path that contains the target path or lies inside it. O9, this cluster
    replicating to the declared source at a path equal to or nested with the target path, a reversal. O2, a live record
    of another key at the target path, a holder. O6, a live record of the key that the source does not hold. O5, an
    ENDED record at the target path over a directory that holds files. O4, an empty source over a target directory
    that holds files. O1, a target directory that holds any entry.
  - A release, one O(release) entry bound to a record's id and naming rows, lifts only those rows of that record. O1
    is lifted only by O(allow_non_empty_directory), or by O(allow_directories_only) for a directory holding only
    directories; O6, O7 and O9 by nothing.
  - Emptiness is decided from the directory's entries, never from its aggregates. Row O1 counts every entry, a
    subdirectory included. Rows O4 and O5 count only files, links and other objects at any depth.
  - Before the authorize, the real run deletes the records a release lifted, and prunes stale records of the key that
    hold nothing, pending ones, and ENDED ones over a directory that holds no file.
  - With O(state=absent), deletes every record of the key in any state, the ENDED and pending records of
    O(previous_source_cluster_uuids) at the same paths, and a live holder at the target path only with a release bound
    to it. The target directory keeps its data, and becomes writable once no record holds it.
options:
  source_cluster_uuid:
    description:
      - UUID of the source cluster. For a source rebuilt with a new UUID, the old relationship is a holder (O2) of the
        new one's directory.
    type: str
    required: true
  source_path:
    description: The source directory on the source cluster.
    type: str
    required: true
  target_path:
    description:
      - The target directory on this cluster. It must exist before the authorize; the module never creates it.
    type: str
    required: true
  relationship_id:
    description:
      - The id the source's create returned, or the id of the source's existing record. The module authorizes only
        this record, and fails when this cluster does not hold it.
      - Give this or O(source_creates) with O(state=present).
    type: str
  source_creates:
    description:
      - For a check-mode run before the source's create, when there is no id yet. Every record of the key is then
        stale, and the module predicts the authorize, noting that the relationship does not exist yet.
      - A run that is not in check mode fails with it, since nothing can be authorized before the create.
    type: bool
    default: false
  allow_non_empty_directory:
    description:
      - Let the source's content replace a target directory that holds entries (row O1). The first job deletes whatever
        the source lacks, subdirectories included. The module then sends the server's C(allow-non-empty-directory),
        and only while the directory holds entries.
      - The replication roles pass a relationship entry's C(allow_non_empty_target) here, and accept it only for a
        target directory the target host declares.
    type: bool
    default: false
  allow_directories_only:
    description:
      - For the restore of a rebuilt source from its replica. Let the source's content replace a target directory that
        holds only directories, at any depth, and refuse one that holds a file, link or other object at any depth, as
        row O1 refuses it. The module then sends the server's C(allow-non-empty-directory), and only while the directory
        holds entries.
      - The walk that decides it is the one the module takes right before every authorize, and the only one, so a file
        written between the walk and the authorize goes unseen. The O1 entry reports C(holds_objects), and the first
        object found as C(first_object).
      - It cannot be given with O(allow_non_empty_directory), which allows files too.
    type: bool
    default: false
  directory_pending:
    description:
      - In check mode, a missing target directory is pending, since an earlier step of the run creates it. It counts as
        empty, and row O7 is still evaluated. Outside check mode a missing directory always fails.
    type: bool
    default: false
  source_empty:
    description:
      - Whether the source directory is missing or holds no file, link or other object at any depth, as
        C(qumulo.cluster.replication_relationship) reports it. Unset counts as empty for row O4.
    type: bool
  source_files:
    description:
      - The source directory's count of files, links and other objects. When the source holds files, but fewer than
        the target directory that the authorize replaces, the module warns that the source may be only partly restored.
    type: int
  source_bytes:
    description:
      - The source directory's size in bytes. When it exceeds the space this cluster can reuse, its free space plus the
        target directory's own size plus, on a portal spoke, the portal cache less its eviction reserve, the module
        warns; with O(strict_capacity) it refuses.
    type: int
  strict_capacity:
    description: Refuse, rather than warn, when the source looks larger than the space this cluster can reuse.
    type: bool
    default: false
  release:
    description:
      - Release entries for this relationship, each C(<target host>:<target path>@<record id>:<rows>). An entry lifts
        only the rows it names, and only while the record with its id exists.
      - With O(state=absent), an entry bound to a live holder at the target path and naming O2 deletes the holder.
      - An entry for another target path fails.
    type: list
    elements: str
    default: []
  previous_source_cluster_uuids:
    description:
      - UUIDs the source cluster had before a rebuild. With O(state=absent), the ENDED and pending records of these
        UUIDs at the same paths are deleted too, since they hold nothing. A holder of one of them is reported with
        C(from_previous_source).
      - It must not hold O(source_cluster_uuid).
    type: list
    elements: str
    default: []
  state:
    description: Whether the relationship is authorized on this cluster.
    type: str
    choices: [present, absent]
    default: present
  replication_address:
    description:
      - The IP address the source connects to, which is the target host's C(qumulo_replication_address). The
        C(qumulo.cluster.replication_pair) role passes it, as its C(replication_pair_target_address); this option is
        not for direct use. A value that is not an IP address fails the module.
      - With O(state=present) the module checks, before it reads the record and before any write, that the tenant
        serving the address has C(replication_enabled). It reads the tenant list, which needs PRIVILEGE_TENANT_READ.
        With two or more tenants it also reads the network document, which needs PRIVILEGE_NETWORK_READ, and takes the
        tenant that holds the network whose ranges hold the address; with one tenant, that tenant. It refuses when that
        tenant has C(replication_enabled) false. The message names the tenant and says to set C(replication_enabled)
        true on it; with two or more tenants it also names the network, and offers moving the address to a network of
        a tenant that has it on.
      - The check runs on every run, so a relationship that is already ESTABLISHED, into a tenant with replication
        off, is refused each time, until replication is turned on for that tenant or the address moves to a network of
        a tenant that has it on.
      - With two or more tenants, an address that no network's ranges hold, such as a HOST network's host address, a
        DHCP network's node address or an address behind NAT, and a network that no tenant holds, are not checked, and
        the module warns. With O(state=absent), or unset, the module reads nothing for it and checks nothing.
    type: str
  planned_tenant_writes:
    description:
      - The tenant writes that the target host's own run of the C(qumulo.cluster.cluster_baseline) role makes earlier in
        the play, its steps 3a and 3b, as M(qumulo.cluster.tenant) items with C(name), C(previous_name) on a rename, and
        the switches each sets. The C(qumulo.cluster.replication_pair) role passes them in check mode when the target
        host is in the play; this option is not for direct use.
      - Read only in check mode, by the check of O(replication_address). It takes a tenant's C(replication_enabled) from
        the item that sets it, since the real run reads the tenants after those writes. An item names the tenant by its
        name on the cluster in C(previous_name) on a rename, and in C(name) otherwise.
      - The networks are still read as they are, so a check run that also moves the address's network to another tenant
        judges the network's tenant before the move.
    type: list
    elements: dict
    default: []
  wait:
    description:
      - After an authorize, wait until the target's record is ESTABLISHED. A record that turns to a failover state
        instead ends the wait with a warning.
    type: bool
    default: true
  wait_timeout:
    description: Seconds to wait.
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
      - Evaluates the overwrite table as the real run does, and fails on the same unmet rows and the same refusal of
        O(replication_address), with a tenant's switch as O(planned_tenant_writes) leaves it. With O(source_creates) it
        predicts the authorize of a relationship the source has not created yet.
  diff_mode:
    support: full
    details:
      - The diff's before and after each hold C(relationship), in the shape of RV(relationship), and C(records), the
        records the task deletes, or in check mode would delete. After's C(records) is always empty.
  idempotent:
    support: partial
    details:
      - An authorize cannot be resent; the module authorizes only a record awaiting authorization, and reconciles a lost
        answer by reading the record (G-REPL-ONESHOT).
      - Authorizing into a directory that holds entries replaces its content (G-REPL-OVERWRITE).
notes:
  - "API: GET /v2/replication/target-relationships/status/, GET /v2/replication/target-relationships/{id}/status,
    POST /v2/replication/target-relationships/{id}/authorize, POST /v2/replication/target-relationships/{id}/delete,
    GET /v2/replication/source-relationships/status/, GET /v1/files/{ref}/info/attributes, GET /v1/files/{ref}/entries/,
    GET /v1/files/{ref}/aggregates/, GET /v4/snapshots/status/; for O(source_bytes), GET /v1/file-system and, on a
    portal spoke, GET /v1/portal/spokes/eviction-settings; for O(replication_address), GET /v1/multitenancy/tenants/,
    which needs PRIVILEGE_TENANT_READ, and, with two or more tenants, GET /v3/network, which needs
    PRIVILEGE_NETWORK_READ."
  - O(replication_address) is checked through the multitenancy REST API, which Qumulo marks as preview; a later Qumulo
    Core release may change it.
  - Idempotency gaps G-REPL-ONESHOT and G-REPL-OVERWRITE apply. A rebuilt source's old record holds the replica until
    a release (G-REPL-PEER). The source tells the target of a delete only once, so a removal deletes on the source and
    then here (G-REPL-NOTIFY). A deleted record leaves its recovery-point snapshot, owned by nothing, which the result
    names (G-REPL-SNAPSHOT).
  - On the same path, the server reports a non-empty directory before another relationship's hold (QUIRK-021). The
    module looks for holders first, so it reports the holder.
  - The capacity comparison is approximate. Whether C(free_size_bytes) leaves out a spoke's evictable cache is inferred,
    not observed.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Preview the authorize before the source creates the relationship
  # Run against the target cluster. directory_pending counts a target directory that an earlier task creates.
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    source_creates: true
    directory_pending: true
  check_mode: true
  diff: true
  register: preview

- name: Show what would keep the relationship from being authorized
  ansible.builtin.debug:
    msg: "{{ preview.unmet | default([]) | map(attribute='message') | list }}"

- name: Create the target directory before the authorize
  qumulo.cluster.directory:
    path: /replicas/edge01-projects

- name: Authorize the relationship the source created, and wait until it is ESTABLISHED
  # created is the register of the source's qumulo.cluster.replication_relationship task.
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    relationship_id: "{{ created.relationship.id }}"
    source_empty: "{{ created.source_empty }}"
    source_files: "{{ created.source_files }}"
    source_bytes: "{{ created.source_bytes }}"

- name: Preview the authorize of the relationship the source created
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    relationship_id: "{{ created.relationship.id }}"
    source_empty: "{{ created.source_empty }}"
  check_mode: true
  diff: true

- name: Authorize without waiting for the first job to reach ESTABLISHED
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    relationship_id: "{{ created.relationship.id }}"
    source_empty: "{{ created.source_empty }}"
    wait: false

- name: Authorize, refusing rather than warning when the source looks larger than the space this cluster can reuse
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    relationship_id: "{{ created.relationship.id }}"
    source_empty: "{{ created.source_empty }}"
    source_bytes: "{{ created.source_bytes }}"
    strict_capacity: true

- name: Authorize into a directory that already holds a copy, letting the source's content replace it
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge02'].qumulo_cluster_uuid }}"
    source_path: /data
    target_path: /replicas/edge02-data
    relationship_id: "{{ edge02_relationship_id }}"
    source_empty: false
    allow_non_empty_directory: true

- name: Authorize into a directory that holds only directories, refusing one that holds a file
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge02'].qumulo_cluster_uuid }}"
    source_path: /data
    target_path: /replicas/edge02-data
    relationship_id: "{{ edge02_relationship_id }}"
    source_empty: false
    allow_directories_only: true

- name: After a copy back into the rebuilt source, release its old relationship and authorize the new one
  # Pass the release with -e for this run only, never in the inventory; the refusal printed it with the holder's id and
  # its rows. The module refuses entries for other target paths, so pass only this relationship's.
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    relationship_id: "{{ created.relationship.id }}"
    source_empty: false
    allow_non_empty_directory: true
    release: >-
      {{ qumulo_replication_release | default([])
         | select('match', inventory_hostname ~ ':/replicas/edge01-projects/?@') | list }}

- name: Remove the relationship on the target after the source's delete, keeping the directory and its data
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    previous_source_cluster_uuids: "{{ hostvars['edge01'].qumulo_previous_cluster_uuids | default([]) }}"
    state: absent
  register: removed

- name: Show the snapshots the removal left
  ansible.builtin.debug:
    msg: "{{ removed.snapshots | map(attribute='name') | list }}"

- name: Remove a relationship whose source cluster is gone for good
  qumulo.cluster.replication_target:
    source_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
    source_path: /projects
    target_path: /replicas/old-edge-projects
    state: absent

- name: Remove the relationship from edge01 on the target, and the old relationship that holds its directory, with the release its refusal printed
  # The holder is a live record of another key at the target path, so the delete needs a release bound to its id that
  # names O2. The key's own records go too, whatever their state.
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    release: >-
      {{ qumulo_replication_release | default([])
         | select('match', inventory_hostname ~ ':/replicas/edge01-projects/?@') | list }}
    state: absent
"""

RETURN = r"""
relationship:
  description:
    - The target's record after the task, in the shape M(qumulo.cluster.replication_info) reports both sides in; empty
      after O(state=absent). The settings and the address are null, since only the source records them.
    - In check mode, the predicted record; with O(source_creates), its id is null.
  returned: always
  type: dict
  contains:
    id:
      description: The relationship's id, which both sides share.
      type: str
    source_cluster_uuid:
      description: UUID of the source cluster.
      type: str
    target_cluster_uuid:
      description: UUID of this cluster.
      type: str
    source_path:
      description: The source directory, without a trailing slash.
      type: str
    target_path:
      description: The target directory, without a trailing slash.
      type: str
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
    target_address:
      description: Always null; only the source records it.
      type: str
    target_port:
      description: Always null; only the source records it.
      type: int
    replication_enabled:
      description: Always null; only the source records it.
      type: bool
    map_local_ids_to_nfs_ids:
      description: Always null; only the source records it.
      type: bool
    source_root_read_only:
      description: Always null; only the source records it.
      type: bool
    blackout_windows:
      description: Always null; only the source records it.
      type: list
      elements: dict
    blackout_window_timezone:
      description: Always null; only the source records it.
      type: str
  sample:
    id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
    source_cluster_uuid: 9533a9e2-e57a-454f-8aa0-9c77b706b7bd
    target_cluster_uuid: 59052546-452f-483e-a800-0a925d3fa1bd
    source_path: /projects
    target_path: /replicas/edge01-projects
    target_address: null
    target_port: null
    state: ESTABLISHED
    end_reason: null
    job_state: REPLICATION_NOT_RUNNING
    job_start_time: null
    recovery_point: "2026-11-01T08:21:54.728656093Z"
    recovery_point_snapshot_id: 64
    error_from_last_job: null
    target_root_read_only: true
    replication_enabled: null
    map_local_ids_to_nfs_ids: null
    source_root_read_only: null
    blackout_windows: null
    blackout_window_timezone: null
unmet:
  description:
    - Every condition that keeps the authorize from going ahead, lifted or not, in the reporting order. Empty when the
      record needed no authorize, and with O(state=absent). A run that fails on one still returns the list.
  returned: always
  type: list
  elements: dict
  contains:
    row:
      description: The overwrite table's row, such as V(O2), or another condition such as V(target_missing).
      type: str
    message:
      description: What is wrong, and what would lift it.
      type: str
    binds_to:
      description: For a row a release lifts, the id a release entry must carry; null otherwise.
      type: str
    lifted_by:
      description: What can lift it, such as V(release) or V(allow_non_empty_target); null when nothing can.
      type: str
    lifted:
      description: Whether this run's consent or release lifts it.
      type: bool
    pending:
      description: Whether it is a check-mode prediction that an earlier step of the run meets.
      type: bool
    facts:
      description: The facts behind it, such as a holder's id, source UUID and state.
      type: dict
releases:
  description:
    - The release entries to pass, one per record id that still binds a row no release lifts, each with
      C(target_path), C(record_id) and C(rows). C(rows) holds every row reported on that record, the lifted ones
      included, so the printed entry alone lifts them all. Prefix the target host's inventory name to pass one. Empty
      with O(state=absent).
  returned: always, and on a refusal before any write
  type: list
  elements: dict
  contains:
    target_path:
      description: The target path of the relationship.
      type: str
    record_id:
      description: The id of the record that binds the rows.
      type: str
    rows:
      description: Every row reported on that record.
      type: list
      elements: str
  sample:
    - target_path: /replicas/edge01-projects
      record_id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
      rows: [O2, O4]
holders:
  description:
    - Each live record of another key at the target path, with C(id), C(source_cluster_uuid), C(source_path),
      C(state), C(recovery_point), C(error_from_last_job), C(job_state) and C(target_root_read_only).
    - With O(state=absent), the holders the task left, since no release bound to them named O2.
  returned: always, and on a refusal before any write
  type: list
  elements: dict
replaces:
  description:
    - The target directory's count of files, links and other objects that an authorize replaces, from its aggregates;
      null when the module did not evaluate the overwrite table, as with O(state=absent).
  returned: always, and on a refusal before any write
  type: int
deleted:
  description:
    - With O(state=absent), the records deleted, in the shape of RV(relationship).
    - With O(state=present), the records a release lifted and the stale records pruned before the authorize are not
      listed here. They are in the diff's C(before.records), and the pruned ones in RV(notes).
  returned: when O(state=absent)
  type: list
  elements: dict
snapshots:
  description:
    - The snapshots the records the task deleted owned, and the replication snapshots owned by nothing at the target
      path, each with C(id) and C(name). A record's snapshot can lose its owner at the source's delete, before this
      task, so the ownerless ones count; an earlier removal at the same path may have left some of them. None
      expires. Empty when the task deleted nothing.
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
  description: What the module noticed, such as a relationship the source has not created yet, or the snapshots left.
  returned: when there is something to note
  type: list
  elements: str
maybe_applied:
  description:
    - True when an authorize or a delete was sent and the module could not confirm it, so the next run reads the
      cluster and decides. The module never resends it.
  returned: on a failure after a lost answer
  type: bool
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.replication_target import ReplicationTarget

ARGUMENT_SPEC = {
    "source_cluster_uuid": {"type": "str", "required": True},
    "source_path": {"type": "str", "required": True},
    "target_path": {"type": "str", "required": True},
    "relationship_id": {"type": "str"},
    "source_creates": {"type": "bool", "default": False},
    "allow_non_empty_directory": {"type": "bool", "default": False},
    "allow_directories_only": {"type": "bool", "default": False},
    "directory_pending": {"type": "bool", "default": False},
    "source_empty": {"type": "bool"},
    "source_files": {"type": "int"},
    "source_bytes": {"type": "int"},
    "strict_capacity": {"type": "bool", "default": False},
    "release": {"type": "list", "elements": "str", "default": []},
    "previous_source_cluster_uuids": {"type": "list", "elements": "str", "default": []},
    "replication_address": {"type": "str"},
    "planned_tenant_writes": {"type": "list", "elements": "dict", "default": []},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 600},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, mutually_exclusive=[("allow_non_empty_directory", "allow_directories_only")])
    run_resource(module, ReplicationTarget)


if __name__ == "__main__":
    main()
