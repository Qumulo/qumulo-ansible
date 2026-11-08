#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: snapshot_info
short_description: Read a Qumulo cluster's snapshots, snapshot policies, snapshot capacity and lock keys
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the snapshots, snapshot policies, snapshot capacity and lock keys of one cluster, and changes nothing.
  - Each policy comes back in the option shape of M(qumulo.cluster.snapshot_policy), so it carries its name and no id.
  - Each key comes back in the option shape of M(qumulo.cluster.security_key), with its id and what uses it.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V(snapshots) is every snapshot, V(policies) every snapshot policy, V(capacity) the space the snapshots use, and
        V(security_keys) every key in the file system key store with what uses it.
      - When the API user may not read the key store, V(security_keys) is listed in RV(unavailable_subsets) and the other
        subsets are gathered. A key deleted while the keys are read is left out.
    type: list
    elements: str
    choices: [all, snapshots, policies, capacity, security_keys]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(snapshots) GET /v4/snapshots/status/; V(policies) GET /v3/snapshots/policies/status/;
    V(capacity) GET /v1/snapshots/total-used-capacity; V(security_keys) GET /v1/file-system/security/keys/ and GET
    /v1/file-system/security/keys/{id}/usages for each key."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the snapshots, policies, capacity and lock keys
  qumulo.cluster.snapshot_info:
  register: snapshots

- name: List the snapshots that are not being deleted
  ansible.builtin.debug:
    msg: "{{ snapshots.snapshots.snapshots | rejectattr('in_delete') | map(attribute='name') | list }}"

- name: List the snapshots of the project tree
  qumulo.cluster.snapshot_info:
    gather_subset: [snapshots]
  register: project_snapshots

- name: Show the names of the project tree's snapshots
  ansible.builtin.debug:
    msg: "{{ project_snapshots.snapshots.snapshots | selectattr('path', 'equalto', '/data/projects') | map(attribute='name') | list }}"

- name: Read the snapshot policies
  qumulo.cluster.snapshot_info:
    gather_subset: [policies]
  register: policy_facts

- name: List the policies that are switched off
  ansible.builtin.debug:
    msg: "{{ policy_facts.snapshots.policies | rejectattr('enabled') | map(attribute='policy_name') | list }}"

- name: Read how much space the snapshots use
  qumulo.cluster.snapshot_info:
    gather_subset: [capacity]
  register: snapshot_capacity

- name: Show the space deleting every snapshot would free
  ansible.builtin.debug:
    msg: "{{ snapshot_capacity.snapshots.capacity.used_bytes }} bytes"

- name: Read the lock keys and what uses each
  qumulo.cluster.snapshot_info:
    gather_subset: [security_keys]
  register: key_facts

- name: List the ids of the snapshots the legal-hold key locks
  ansible.builtin.debug:
    msg: >-
      {{ key_facts.snapshots.security_keys | selectattr('name', 'equalto', 'legal-hold') | map(attribute='usages') | flatten
         | selectattr('type', 'equalto', 'snapshot') | map(attribute='id') | list }}
"""

RETURN = r"""
snapshots:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    snapshots:
      description:
        - Every snapshot as the cluster reports it, with C(path), the snapshotted directory without a trailing slash.
      type: list
      elements: dict
      contains:
        id:
          description: The snapshot's id.
          type: int
          sample: 3
        name:
          description: >-
            The snapshot's full name: its id, then the name the task gave, V(OnDemand), or what the policy's
            C(snapshot_name_template) made.
          type: str
          sample: 3_projects-daily_projects
        path:
          description: The snapshotted directory, without a trailing slash.
          type: str
          sample: /data/projects
        source_file_path:
          description: The snapshotted directory as the cluster reports it, with a trailing slash.
          type: str
          sample: /data/projects/
        source_file_id:
          description: The snapshotted directory's file id.
          type: str
          sample: "100"
        timestamp:
          description: When the snapshot was taken.
          type: str
          sample: "2026-10-31T02:41:36.296864386Z"
        expiration:
          description: When the snapshot expires, in RFC 3339; an empty string for never.
          type: str
          sample: "2026-11-07T02:41:36.296864386Z"
        policy_id:
          description: The id of the policy that took the snapshot, or null for an on-demand snapshot.
          type: int
        policy_name:
          description: The name of that policy, or null.
          type: str
        in_delete:
          description: Whether the cluster is deleting the snapshot.
          type: bool
          sample: false
        lock_key:
          description:
            - The id of the key the snapshot is locked with, as the cluster reports it, or null when it is unlocked.
            - M(qumulo.cluster.snapshot_lock) returns the key's name.
          type: raw
          sample: "1"
        owners:
          description: The replication relationships that hold the snapshot, each as C(id).
          type: list
          elements: dict
          sample: []
    policies:
      description: Every snapshot policy, in the option shape of M(qumulo.cluster.snapshot_policy).
      type: list
      elements: dict
      contains:
        policy_name:
          description: The policy's name.
          type: str
          sample: projects-hourly
        path:
          description: The directory the policy snapshots.
          type: str
          sample: /data/projects
        enabled:
          description: Whether the policy takes snapshots.
          type: bool
          sample: true
        expiration_time_to_live:
          description: How long the policy's snapshots are kept, as the cluster stores it; an empty string for never.
          type: str
          sample: 7days
        snapshot_name_template:
          description: How the policy names its snapshots.
          type: str
          sample: "{ID}_{Policy}_{Directory}"
        lock_key:
          description:
            - The name of the key that locks each snapshot the policy takes, or null for none.
            - The key's id when the API user may not read the key store, with a warning, or when the key has no name
              (QUIRK-120).
          type: str
          sample: legal-hold
        schedule:
          description: When the policy takes snapshots, in the fields of O(qumulo.cluster.snapshot_policy#module:schedule).
          type: dict
          sample: {frequency: SCHEDULE_DAILY_OR_WEEKLY, hour: 2, minute: 0, on_days: [MON, TUE, WED, THU, FRI], timezone: UTC}
    capacity:
      description: Snapshot capacity in bytes.
      type: dict
      contains:
        used_bytes:
          description: About how many bytes deleting every snapshot would free.
          type: int
          sample: 1048576
    security_keys:
      description: Every key in the file system key store, in the option shape of M(qumulo.cluster.security_key).
      type: list
      elements: dict
      contains:
        id:
          description: The key's id, which a snapshot's C(lock_key) names.
          type: str
          sample: "2"
        name:
          description: The key's name.
          type: str
          sample: legal-hold
        comment:
          description: The key's comment.
          type: str
          sample: Locks the snapshots kept for legal holds
        enabled:
          description: Whether the key can lock snapshots.
          type: bool
          sample: true
        public_key:
          description: The key's public half, as base64 DER.
          type: str
          sample: MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE...
        usages:
          description:
            - What uses the key, each C(type) and C(id), sorted by type and then id. C(type) is V(snapshot),
              V(snapshot_policy) or V(replication_target).
          type: list
          elements: dict
          sample: [{type: snapshot, id: "12"}, {type: snapshot_policy, id: "1"}]
unavailable_subsets:
  description: The subsets that could not be gathered, each with the reason, such as V(security_keys) for an API user who
    may not read the key store.
  returned: when a subset could not be gathered
  type: dict
  sample: {security_keys: "the API user may not read the key store: ..."}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import SNAPSHOT_INFO_SUBSETS

ARGUMENT_SPEC = {"gather_subset": {"type": "list", "elements": "str", "choices": ["all", *SNAPSHOT_INFO_SUBSETS], "default": ["all"]}}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "snapshots", SNAPSHOT_INFO_SUBSETS)


if __name__ == "__main__":
    main()
