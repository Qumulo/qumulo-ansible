#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: snapshot_lock
short_description: Lock or unlock a snapshot on a Qumulo cluster with a key from its key store
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Locks one snapshot with a key the cluster's file system key store holds, or unlocks it.
  - A snapshot is matched by O(snapshot_id) or by its full O(name).
  - Until it is unlocked, a locked snapshot cannot be deleted, given an earlier expiration, or given one where it has
    none, and only a signature made with the key's private key unlocks it. The cluster keeps only the public key.
  - The module locks a snapshot that never expires only when the task gives O(private_key) and the key matches the key
    store's public key. No option lifts that refusal.
  - The key must already be in the key store; M(qumulo.cluster.security_key) registers it. The module refuses to lock
    with a disabled key, in check mode too, before any write. A snapshot the disabled key already locks reports no
    change, and the key still unlocks it.
options:
  snapshot_id:
    description: The snapshot's id, as M(qumulo.cluster.snapshot) returns it. Give this or O(name), not both.
    type: int
  name:
    description:
      - The snapshot's full name, its id and the name it was given, such as V(12_before-upgrade). Give this or
        O(snapshot_id), not both.
    type: str
  state:
    description: Whether the snapshot is locked.
    type: str
    choices: [locked, unlocked]
    default: locked
  lock_key:
    description:
      - The key that locks the snapshot, by its name or id in the key store. Required to lock.
      - A snapshot locked with another key is refused; unlock it first. With O(state=unlocked), the module unlocks only
        a snapshot this key holds.
      - A key that is not in the key store, or that is disabled, is refused for a lock.
    type: str
  private_key:
    description:
      - The lock key's private key, in unencrypted PEM. Required to unlock, and to lock a snapshot that never expires.
        A key encrypted with a passphrase of its own is refused.
      - The module checks it against the key store's public key before it asks the cluster for anything that would
        spend a challenge.
      - The module signs with the C(openssl) command on the controller, which must be installed. It writes the key to a
        file only the controller's user can read, and removes the file once openssl exits. The key is never logged,
        returned or shown in a diff.
    type: str
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Checks the private key against the key store, and asks the cluster for no unlock challenge.
  diff_mode:
    support: full
  idempotent:
    support: full
    details:
      - The cluster refuses to lock a locked snapshot and to unlock an unlocked one, so the module reads the lock first
        and sends neither when the snapshot already has the state asked for (G-SNAP-LOCK).
notes:
  - "API: GET /v3/snapshots/status/{id}, or GET /v4/snapshots/status/ with O(name); GET
    /v1/file-system/security/keys/{key}; POST /v3/snapshots/{id}/lock; GET /v3/snapshots/{id}/unlock-challenge and POST
    /v3/snapshots/{id}/unlock."
  - With O(private_key) the controller needs the C(openssl) command; the module fails before any request without it.
  - If the connection is lost after a lock or an unlock was sent, the module reads the lock back to decide, and never
    sends it twice.
  - "Registers: G-SNAP-LOCK."
seealso:
  - module: qumulo.cluster.snapshot
    description: Takes the snapshot to lock, and returns its id and full name.
  - module: qumulo.cluster.security_key
    description: Registers the key that locks the snapshot, enables or disables it, and replaces its public key.
  - module: qumulo.cluster.snapshot_info
    description: Reports each snapshot's C(lock_key), and in its V(security_keys) subset the snapshots each key locks.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Take a snapshot before an upgrade and lock it with the legal-hold key
  # The key store must hold the legal-hold public key already, registered with qumulo.cluster.security_key.
  block:
    - name: Take the snapshot, which expires in two weeks
      qumulo.cluster.snapshot:
        path: /data/projects
        name: before-upgrade
        expiration_time_to_live: 2weeks
        reuse_existing: true
      register: before_upgrade

    - name: Lock the snapshot with the legal-hold key
      # In check mode the snapshot is not taken yet, so there is nothing to lock.
      qumulo.cluster.snapshot_lock:
        snapshot_id: "{{ before_upgrade.snapshot.id }}"
        lock_key: legal-hold
      when: before_upgrade.snapshot.id is defined

- name: Lock a snapshot that never expires, proving the key can unlock it
  # Without private_key the module refuses to lock a snapshot that never expires. To lock one without the private key,
  # give the snapshot an expiration first, for example with qq snapshot_modify_snapshot --id <id> --expiration <time>.
  # Keep the private key in an ansible-vault.
  qumulo.cluster.snapshot_lock:
    name: 14_audit-hold
    lock_key: legal-hold
    private_key: "{{ vault_legal_hold_private_key }}"

- name: Check whether the snapshot is still locked
  # In check mode the task fails if the legal-hold key is disabled and the snapshot is not locked with it.
  qumulo.cluster.snapshot_lock:
    snapshot_id: 12
    lock_key: legal-hold
  check_mode: true
  diff: true
  register: lock_check

- name: Show the key the snapshot is locked with
  ansible.builtin.debug:
    msg: "locked with {{ lock_check.diff.before.lock_key | default('no key', true) }}"

- name: Unlock the snapshot
  qumulo.cluster.snapshot_lock:
    snapshot_id: 12
    state: unlocked
    private_key: "{{ vault_legal_hold_private_key }}"
"""

RETURN = r"""
snapshot_lock:
  description: The snapshot's lock after the task.
  returned: always
  type: dict
  contains:
    snapshot_id:
      description: The snapshot's id.
      type: int
      sample: 12
    name:
      description: The snapshot's full name.
      type: str
      sample: 12_before-upgrade
    path:
      description: The directory the snapshot is of, without a trailing slash.
      type: str
      sample: /data/projects
    expiration:
      description: When the snapshot expires, in RFC 3339; an empty string for never.
      type: str
      sample: "2026-11-16T02:44:35Z"
    lock_key:
      description: The name of the key the snapshot is locked with, or null when it is unlocked.
      type: str
      sample: legal-hold
notes:
  description: What the module noticed, such as a lock it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: True when a lock or an unlock was sent and the read-back did not show it took effect, as after a lost connection.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import SnapshotLock

ARGUMENT_SPEC = {
    "snapshot_id": {"type": "int"},
    "name": {"type": "str"},
    "state": {"type": "str", "choices": ["locked", "unlocked"], "default": "locked"},
    "lock_key": {"type": "str", "no_log": False},
    "private_key": {"type": "str", "no_log": True},
}


def main():
    module = qumulo_module(
        ARGUMENT_SPEC,
        mutually_exclusive=[("snapshot_id", "name")],
        required_one_of=[("snapshot_id", "name")],
        required_if=[("state", "locked", ("lock_key",))],
    )
    run_resource(module, SnapshotLock)


if __name__ == "__main__":
    main()
