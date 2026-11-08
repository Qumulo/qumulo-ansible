#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_restore_lock
short_description: Hold the controller's lock on an edge's restore, so one restore runs per edge at a time
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Takes or removes the lock that keeps a second restore of an edge from starting on the same controller while one is
    under way. The C(replication_pair) role's C(restore.yml) takes it before its first step and removes it in an
    C(always:) section.
  - An action plugin that runs on the controller. It sends no request, takes no connection options, and is not in the
    C(qumulo.cluster.qumulo) action group, so C(module_defaults) for that group do not reach it.
  - The lock is a JSON file, C(format) 1, holding O(holder), a random token for this run, the controller's host name,
    boot id and PID namespace, the C(ansible-playbook) process's id, its start in clock ticks since boot and its start
    time, and the lease's expiry. It is written to C(<path>.tmp-<token>), synced to the disk, and linked to O(path); then
    the temporary name is unlinked, and any C(<path>.tmp-*) older than O(lease) is removed. A lock that is empty or not
    JSON refuses, naming the file to remove by hand once no run is under way.
  - A lock that another run holds is taken over, with a warning, when its process is gone, if it was taken on this
    controller, or once its lease has run out, if it was taken on another host or on a controller without C(/proc),
    where whether its process is alive cannot be read. This controller is the same host name, the same boot id and the
    same PID namespace; a lock from any other controller waits for its lease. Its process is the one whose start ticks
    the lock records. A lock whose process is alive on this controller is never taken over, whatever its lease says.
    Any other lock refuses, naming its holder, its process, its controller and its lease's end.
  - A take that passes the lock's O(token), by the same O(holder) and from the C(ansible-playbook) process that took it,
    renews it, extending its lease to O(lease) from now. The renewal is written to C(<path>.tmp-<token>) and renamed over
    O(path) under the guard, after the run reads the lock again and finds its token still there. A take by that process
    for another holder refuses. A renewal never takes a new lock; one whose lock is gone, or holds another token, or
    holds O(token) but was taken by another process, refuses.
  - A takeover holds an exclusive C(flock) on C(<path>.guard) while it reads the lock again and replaces it; the removal
    holds it while it reads the lock again and unlinks it. The kernel drops the C(flock) when the process dies. The
    guard file is kept. A run waits at most 60 seconds for the guard, then refuses.
  - O(state=present) creates the lock's directory and each missing one above it with mode 0700, and never changes the
    mode of one that exists, so a directory that cannot be written fails the take.
  - The lock is the controller's alone. Two controllers can both restore one edge.
options:
  path:
    description: The lock file, C(<edge_rebuild_artifact_dir>/<edge>/restore/lock).
    type: path
    required: true
  state:
    description:
      - V(present) takes the lock for this run and returns its C(token); with O(token), it renews the lock that holds it.
      - V(absent) removes the lock only when it holds O(token) and this C(ansible-playbook) process took it, so a run
        that found another run's lock never removes it.
    type: str
    choices: [present, absent]
    default: present
  token:
    description:
      - The token of the lock this run holds, as the take returned it in C(token). Required with O(state=absent).
      - With O(state=present), the take renews the lock that holds it, and fails when the lock no longer does. Without
        it, a take never renews, so a second import of the playbook in the same process takes a lock of its own.
      - With O(state=absent), the empty string removes nothing, as after a take that failed.
    type: str
  host:
    description:
      - The inventory host whose lock O(path) is, which the refusals name.
      - Defaults to the task's C(inventory_hostname).
    type: str
  lease:
    description:
      - How long, in seconds from the take, another host may not take the lock over, at least 1. Required with
        O(state=present).
    type: int
  holder:
    description:
      - What holds the lock, written into it and named by the refusal of another run.
      - The C(replication_pair) role's restore takes the default.
    type: str
    default: restore
attributes:
  check_mode:
    support: full
    details: Reports whether it would take, take over, renew or remove the lock, and refuses a lock it would refuse; it
      creates nothing.
  diff_mode:
    support: none
  idempotent:
    support: full
    details: The take, the renewal and the removal report no change, so a converge's idempotence run counts none.
extends_documentation_fragment:
  - qumulo.cluster.attributes
notes:
  - "API: none."
"""

EXAMPLES = r"""
- name: Restore one edge at a time
  # Run on the controller. The lease is the longest the restore can take, in seconds; the always section removes the
  # lock even when a step fails, and removes nothing when the take itself failed.
  block:
    - name: Take the edge's restore lock
      qumulo.cluster.replication_restore_lock:
        path: "{{ edge_rebuild_artifact_dir }}/{{ inventory_hostname }}/restore/lock"
        lease: 90000
      register: restore_lock

    - name: Restore
      ansible.builtin.debug:
        msg: The restore's steps run here.
  always:
    - name: Remove the edge's restore lock, if this run holds it
      qumulo.cluster.replication_restore_lock:
        path: "{{ edge_rebuild_artifact_dir }}/{{ inventory_hostname }}/restore/lock"
        state: absent
        token: "{{ restore_lock.token | default('') }}"

- name: Preview taking the edge's restore lock, to see whether another run holds it
  # Check mode creates nothing; it refuses a lock it would refuse.
  qumulo.cluster.replication_restore_lock:
    path: "{{ edge_rebuild_artifact_dir }}/{{ inventory_hostname }}/restore/lock"
    lease: 90000
  check_mode: true

- name: Name a holder other than the default when taking the lock
  qumulo.cluster.replication_restore_lock:
    path: "{{ edge_rebuild_artifact_dir }}/{{ inventory_hostname }}/restore/lock"
    lease: 3600
    holder: nightly-restore
    host: edge01
  register: named_lock

- name: Extend the lease of a lock this run holds
  # Pass the token the take returned, and the same holder. The renewal fails when the lock is gone or holds another
  # token.
  qumulo.cluster.replication_restore_lock:
    path: "{{ edge_rebuild_artifact_dir }}/{{ inventory_hostname }}/restore/lock"
    lease: 3600
    holder: nightly-restore
    token: "{{ named_lock.token }}"

- name: Remove the lock this run holds
  qumulo.cluster.replication_restore_lock:
    path: "{{ edge_rebuild_artifact_dir }}/{{ inventory_hostname }}/restore/lock"
    state: absent
    token: "{{ named_lock.token }}"
"""

RETURN = r"""
path:
  description: O(path).
  returned: always
  type: str
  sample: /var/lib/qumulo-edge-rebuild/edge01/restore/lock
lock:
  description: The lock this run holds, or with check mode would hold.
  returned: O(state=present)
  type: dict
  sample:
    format: 1
    holder: restore
    token: 5f0c7d3b9a1e4c2f8d6b0a7e3c1d9f42
    controller: ctl01
    boot_id: 3b7e1c52-6f0a-4d9e-8a14-0c5d2e9f7b36
    pid_namespace: "pid:[4026531836]"
    pid: 41872
    start_ticks: 91823411
    start_time: "2026-11-05T09:14:02.000Z"
    lease_expiry: "2026-11-06T10:14:02.000Z"
  contains:
    format:
      description: The lock's format, 1.
      type: int
    holder:
      description: O(holder).
      type: str
    token:
      description: This run's token.
      type: str
    controller:
      description: The controller's host name.
      type: str
    boot_id:
      description: The controller's boot id, from C(/proc/sys/kernel/random/boot_id); null without C(/proc).
      type: str
    pid_namespace:
      description: The PID namespace the C(ansible-playbook) process runs in, from C(/proc/self/ns/pid), such as
        V(pid:[4026531836]); null without C(/proc).
      type: str
    pid:
      description: The C(ansible-playbook) process's id.
      type: int
    start_ticks:
      description: When that process started, in clock ticks since boot, which tells it from a later process of the same
        id; null without C(/proc).
      type: int
    start_time:
      description: When that process started, in ISO 8601 UTC, for the messages; when the controller has no C(/proc),
        when the lock was taken.
      type: str
    lease_expiry:
      description: When the lease runs out, in ISO 8601 UTC.
      type: str
token:
  description: The token of the lock this run holds, which a renewal and the removal pass back as O(token).
  returned: O(state=present)
  type: str
  sample: 5f0c7d3b9a1e4c2f8d6b0a7e3c1d9f42
took_over:
  description: Whether the take replaced another run's lock.
  returned: O(state=present)
  type: bool
removed:
  description: Whether the lock held O(token) and this process took it, so that it was removed, or with check mode would be.
  returned: O(state=absent)
  type: bool
"""

from ansible.module_utils.basic import AnsibleModule

ARGUMENT_SPEC = {
    "path": {"type": "path", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "lease": {"type": "int"},
    "holder": {"type": "str", "default": "restore"},
    "token": {"type": "str", "no_log": False},
    "host": {"type": "str"},
}
REQUIRED_IF = [("state", "present", ("lease",)), ("state", "absent", ("token",))]


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, required_if=REQUIRED_IF, supports_check_mode=True)
    module.fail_json(msg="replication_restore_lock runs as an action plugin on the controller, which Ansible calls in place of this module.")


if __name__ == "__main__":
    main()
