#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: snapshot
short_description: Take a snapshot of a directory on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Takes an on-demand snapshot of one directory, with an optional name and expiration.
  - Every run takes another snapshot and reports a change (G-ACTIONS), unless O(reuse_existing) finds one to return.
  - The cluster prefixes each snapshot's name with an id it generates, C(<id>_<name>), so the name the task gives never
    identifies a snapshot by itself. With O(reuse_existing), the module looks for a snapshot of the directory whose name
    is O(name) after its id.
options:
  path:
    description:
      - Absolute path of the directory to snapshot, such as V(/data/projects). A trailing slash is ignored.
      - The cluster takes no snapshot of a directory on or under a portal spoke root (G-SPOKE-PATHS); the module
        refuses it first, even in check mode.
    type: str
    required: true
  name:
    description:
      - The name the snapshot carries after its id, such as V(before-upgrade), without C(/). Left unset, the cluster
        names it V(OnDemand).
      - The id and the name together are at most 128 characters.
    type: str
  expiration_time_to_live:
    description:
      - How long the snapshot is kept, as a whole number and a unit, such as V(90minutes), V(12hours), V(7days),
        V(2weeks) or V(6months). The unit may be singular, and months are calendar months.
      - The time is counted from when the module runs on the controller.
      - V(never), or leaving it unset, keeps the snapshot until it is deleted.
    type: str
  reuse_existing:
    description:
      - Take no snapshot when one of O(path) named O(name) exists and is not being deleted, and return the newest such
        snapshot instead. Needs O(name).
      - Its expiration is left as it is, whatever O(expiration_time_to_live) says.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Predicts the snapshot, with the expiration it would get; the id is known only once it is taken.
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - Taking a snapshot is a one-shot action. With O(reuse_existing) and O(name), a second run finds the first run's
        snapshot and takes none.
notes:
  - "API: GET /v1/files/{path}/info/attributes; GET /v2/portal/spokes/; with O(name), GET /v4/snapshots/status/;
    POST /v3/snapshots/."
  - If the connection is lost after the snapshot was requested, the module never requests it again. With O(name) it
    lists the directory's snapshots to find the one the request took; without, it fails, saying a snapshot may have
    been taken.
  - "Registers: G-ACTIONS, G-SPOKE-PATHS."
seealso:
  - module: qumulo.cluster.snapshot_lock
    description: Locks a snapshot so that it cannot be deleted.
  - module: qumulo.cluster.snapshot_policy
    description: Takes snapshots on a schedule.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Snapshot the project tree before an upgrade, once however often the play reruns
  qumulo.cluster.snapshot:
    path: /data/projects
    name: before-upgrade-7-11
    expiration_time_to_live: 2weeks
    reuse_existing: true
  register: before_upgrade

- name: Show the snapshot's id and name
  ansible.builtin.debug:
    msg: "{{ before_upgrade.snapshot.id }}: {{ before_upgrade.snapshot.name }}"

- name: Take a snapshot of the project tree that is kept until it is deleted
  # Every run takes another snapshot and reports a change.
  qumulo.cluster.snapshot:
    path: /data/projects
    name: audit-hold

- name: Take an unnamed snapshot kept for 90 days
  # The cluster names it OnDemand after its id.
  qumulo.cluster.snapshot:
    path: /data/projects
    expiration_time_to_live: 90days

- name: Preview a snapshot kept for a day
  qumulo.cluster.snapshot:
    path: /data/projects
    expiration_time_to_live: 1day
  check_mode: true
  diff: true
  register: snapshot_preview

- name: Show when it would expire
  ansible.builtin.debug:
    msg: "it would expire at {{ snapshot_preview.snapshot.expiration }}"
  when: snapshot_preview.changed
"""

RETURN = r"""
snapshot:
  description: The snapshot taken, or found with O(reuse_existing); in check mode, the one that would be taken.
  returned: always
  type: dict
  contains:
    id:
      description: The snapshot's id; absent in check mode before it is taken.
      type: int
      sample: 12
    name:
      description: The snapshot's name, its id and the name the task gave; in check mode, the name the task gave, or V(OnDemand).
      type: str
      sample: 12_before-upgrade-7-11
    path:
      description: The directory, without a trailing slash.
      type: str
      sample: /data/projects
    timestamp:
      description: When the snapshot was taken; absent in check mode.
      type: str
      sample: "2026-11-02T02:44:35.267716486Z"
    expiration:
      description: When the snapshot expires, in RFC 3339; an empty string for never.
      type: str
      sample: "2026-11-16T02:44:35Z"
notes:
  description: What the module noticed, such as a snapshot it found by listing after the connection was lost, or a directory that does not exist yet
    in check mode.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: True when the snapshot was requested and the connection was lost before the module could confirm that the cluster took it.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import Snapshot

ARGUMENT_SPEC = {
    "path": {"type": "str", "required": True},
    "name": {"type": "str"},
    "expiration_time_to_live": {"type": "str"},
    "reuse_existing": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC, required_if=[("reuse_existing", True, ("name",))]), Snapshot)


if __name__ == "__main__":
    main()
