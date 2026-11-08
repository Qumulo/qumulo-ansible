#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_info
short_description: Read a Qumulo cluster's replication relationships, as source, as target and to object storage
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the replication relationships of one cluster and changes nothing. A cluster reports only its own records, so
    a relationship's two sides are read by running the module against each cluster.
  - Source and target relationships are reported in one shape, so a relationship's entry on its source and its entry
    on its target compare key by key. Each side records only what it knows, and the rest is V(null).
  - Object relationships, which copy between a directory and an S3 bucket on one cluster, are reported as the cluster
    lists them, without their credentials. The collection does not manage them.
  - With O(relationship_id) and O(side), the module reads that one record instead of the lists, as a wait polls it.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
    type: list
    elements: str
    choices: [all, source, target, object]
    default: [all]
  relationship_id:
    description:
      - The id of one relationship to read, from this cluster's record of it on O(side), in place of the lists.
      - It cannot be given with a O(gather_subset) other than V(all).
    type: str
  side:
    description:
      - Which of this cluster's records of O(relationship_id) to read, V(source) or V(target). After a reverse one
        cluster can hold either.
      - Required with O(relationship_id).
    type: str
    choices: [source, target]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(source) GET /v2/replication/source-relationships/status/; V(target) GET
    /v2/replication/target-relationships/status/; V(object) GET /v3/replication/object-relationships/."
  - A relationship whose target was rebuilt with a new UUID stays ESTABLISHED on the source, with the old
    C(target_cluster_uuid) and an C(error_from_last_job) of V(Cannot establish a secure connection.); it never reaches
    ENDED (G-REPL-PEER).
  - Paths are reported without a trailing slash. The source reports a target path as it was sent until it first reaches
    the target, and with a trailing slash afterwards (QUIRK-020).
  - "API, with O(relationship_id): GET /v2/replication/source-relationships/{id}/status for V(source), GET
    /v2/replication/target-relationships/{id}/status for V(target)."
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task names another cluster. Set them once for the play
# with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: List every replication relationship of this cluster, as source, as target and to object storage
  qumulo.cluster.replication_info:
  register: replication

- name: Read the edge's relationships as a source
  qumulo.cluster.replication_info:
    gather_subset: [source]
  register: edge_replication

- name: Find the edge's relationships to dr01's address that no longer reach dr01's current UUID
  # A target rebuilt with a new UUID leaves the source's record ESTABLISHED with the old UUID, so compare each
  # record's target_cluster_uuid with the UUID the target has now.
  ansible.builtin.debug:
    msg: >-
      {{ edge_replication.replication.source_relationships | selectattr('target_address', 'equalto', dr_address)
         | rejectattr('target_cluster_uuid', 'equalto', dr_uuid) }}

- name: List the directory-to-bucket relationships of this cluster, without their credentials
  qumulo.cluster.replication_info:
    gather_subset: [object]
  register: object_replication

- name: Wait until the DR cluster holds the relationship ESTABLISHED
  qumulo.cluster.replication_info:
    gather_subset: [target]
  register: dr_replication
  until: >-
    dr_replication.replication.target_relationships | selectattr('source_path', 'equalto', '/projects')
    | selectattr('state', 'equalto', 'ESTABLISHED') | list | length == 1
  retries: 60
  delay: 5

- name: Read the edge's record of one relationship, as its source
  qumulo.cluster.replication_info:
    relationship_id: "{{ relationship_id }}"
    side: source
  register: source_record

- name: Fail unless the DR cluster holds its record of the relationship the edge reads
  # The two records share one shape, so their keys compare directly.
  qumulo.cluster.replication_info:
    relationship_id: "{{ relationship_id }}"
    side: target
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ dr01_connection }}"
  register: target_record
  failed_when: target_record.replication.relationship is none

- name: Show the recovery point each side reports
  ansible.builtin.debug:
    msg: >-
      source {{ source_record.replication.relationship.recovery_point }},
      target {{ target_record.replication.relationship.recovery_point }}

- name: Wait until the edge's record of one relationship reads DISCONNECTED
  qumulo.cluster.replication_info:
    relationship_id: "{{ relationship_id }}"
    side: target
  register: edge_record
  until: edge_record.replication.relationship.state | default('') == 'DISCONNECTED'
  retries: 60
  delay: 5
"""

RETURN = r"""
replication:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    source_relationships:
      description:
        - The relationships this cluster is the source of, from the V(source) subset.
        - Each has C(id), C(source_cluster_uuid), C(target_cluster_uuid), C(source_path), C(target_path),
          C(target_address), C(target_port), C(state), C(end_reason), C(job_state), C(job_start_time),
          C(recovery_point), C(recovery_point_snapshot_id), C(error_from_last_job), C(target_root_read_only),
          C(replication_enabled), C(map_local_ids_to_nfs_ids), C(source_root_read_only), C(blackout_windows) and
          C(blackout_window_timezone).
        - UUIDs are in lower case, and a blackout window's C(on_days) lists the days from SUN to SAT.
        - C(end_reason), C(job_start_time), C(recovery_point) and C(error_from_last_job) are V(null) when the cluster
          reports none; C(job_start_time) is set only while a job runs.
        - C(blackout_window_timezone) is reported as the cluster stores it, V("") when none was ever set, since a
          timezone once set cannot be cleared (QUIRK-019).
        - C(recovery_point_snapshot_id) is the id of the snapshot that holds the recovery point on this cluster, an
          integer, or V(null) when the record names none.
      type: list
      elements: dict
      sample:
        - id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
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
          blackout_windows: []
          blackout_window_timezone: ""
    target_relationships:
      description:
        - The relationships this cluster is the target of, from the V(target) subset, in the same shape as
          RV(replication.source_relationships).
        - The target records neither the address nor the settings, so C(target_address), C(target_port) and the five
          settings are V(null).
      type: list
      elements: dict
      sample:
        - id: 9465828e-7dcf-466e-a550-78ed0bd6b79b
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
    object_relationships:
      description:
        - The relationships between a directory and an S3 bucket, from the V(object) subset, as the cluster lists them,
          without C(access_key_id) or C(secret_access_key).
      type: list
      elements: dict
    relationship:
      description:
        - With O(relationship_id), this cluster's record of it on O(side), in the shape of
          RV(replication.source_relationships), the target's with null for what only the source records; V(null) when
          this cluster holds no record of that id on that side.
        - The only key gathered then.
      type: dict
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import REPLICATION_INFO_SUBSETS, read_by_id

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *REPLICATION_INFO_SUBSETS], "default": ["all"]},
    "relationship_id": {"type": "str"},
    "side": {"type": "str", "choices": ["source", "target"]},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, required_together=[("relationship_id", "side")])
    params = module.params
    if params["relationship_id"] is None:
        run_info(module, "replication", REPLICATION_INFO_SUBSETS)
        return
    if params["gather_subset"] != ["all"]:
        module.fail_json(msg="relationship_id reads one record, so leave gather_subset at its default, all")
    run(module, lambda client: {"changed": False, "replication": {"relationship": read_by_id(client, params["side"], params["relationship_id"])}})


if __name__ == "__main__":
    main()
