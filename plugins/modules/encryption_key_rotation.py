#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: encryption_key_rotation
short_description: Rotate a Qumulo cluster's at-rest encryption key once it is older than a given age
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Rotates the master key that encrypts the cluster's data at rest when the key was last rotated more than
    O(max_age_days) days ago, and otherwise changes nothing.
  - A rotation is an action, not a setting. The key's age keeps a second run from rotating again (G-ACTIONS).
  - With the cluster's own key store, a rotation makes a new key. With a KMS key store, the cluster rotates only to
    another key the KMS holds, named by O(key_id) (G-MON-KMS-ROTATE).
  - The age is measured against the controller's clock, in UTC. With a KMS key store, a key is aged from the later of
    its last rotation and the KMS configuration's creation time, so a key never rotated is aged from the configuration.
    The cluster's own key store reports no configuration time, so there a key never rotated is due.
  - Check mode reads the key's age and reports the rotation the task would make.
  - Fails on a cluster formed without encryption at rest, which has no key to rotate.
options:
  max_age_days:
    description:
      - Rotate when the key was last rotated more than this many days ago. V(0) rotates on every run. A negative value
        fails.
      - With a KMS key store, a key older than this fails unless O(key_id) is set; with O(key_id), the age is not
        checked.
    type: int
    required: true
  key_id:
    description:
      - With a KMS key store, the id of the KMS key to rotate to. The module rotates when it differs from the key in
        use, whatever the age, and leaves the key alone when it is the key in use.
      - With the cluster's own key store, setting it fails before any write.
    type: str
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - A rotation is an action; the key's age keeps a second run from rotating again, except with O(max_age_days=0).
notes:
  - "API: GET /v2/encryption/key-store and /v2/encryption/key-store/status; POST /v2/encryption/rotate-keys, never sent
    twice. A rotation whose answer is lost is confirmed by reading the key store again."
  - "Registers: G-ACTIONS, G-MON-KMS-ROTATE."
  - The collection does not manage the KMS key store itself, its host and certificates.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Rotate the encryption key when it is older than 90 days
  qumulo.cluster.encryption_key_rotation:
    max_age_days: 90

- name: See whether the key is due, without rotating it
  qumulo.cluster.encryption_key_rotation:
    max_age_days: 90
  check_mode: true
  diff: true
  register: key_due

- name: Show the key's age in days, when the key is due
  ansible.builtin.debug:
    var: key_due.rotation.age_days
  when: key_due is changed

- name: Fail when the key is older than a year
  qumulo.cluster.encryption_key_rotation:
    max_age_days: 365
  check_mode: true
  register: key_overdue
  failed_when: key_overdue is changed

# A KMS key store rotates only to the key set in key_id, so with one, give key_id as in the next recipe.
- name: Rotate the cluster's own encryption key now, whatever its age
  qumulo.cluster.encryption_key_rotation:
    max_age_days: 0

# With a KMS key store the cluster rotates only to another key the KMS holds. A scheduled run rotates only when it is
# given a key id that is not in use, so change qumulo_kms_key_id to rotate.
- name: Rotate to a new key the KMS holds
  qumulo.cluster.encryption_key_rotation:
    max_age_days: 365
    key_id: "{{ qumulo_kms_key_id }}"

- name: Read the key store without rotating
  qumulo.cluster.encryption_key_rotation:
    max_age_days: 90
  check_mode: true
  register: rotated

- name: Show when the key was last rotated
  ansible.builtin.debug:
    var: rotated.key_store.last_key_rotation_time
"""

RETURN = r"""
rotation:
  description: The rotation the task made, or in check mode would make.
  returned: when the key is rotated, or in check mode would be
  type: dict
  contains:
    key_store:
      description: The key store, V(Local) or V(KMS).
      type: str
    last_key_rotation_time:
      description: When the key was last rotated before the task, as the cluster reports it; empty or V(null) for a key never rotated.
      type: str
    age_days:
      description: The key's age in days before the task, to two decimal places; V(null) when the cluster's own key store reports no rotation for the key.
      type: float
    aged_from:
      description:
        - V(configuration) when the age is counted from the KMS configuration's creation time, which is later than the
          key's last rotation or stands in for a key never rotated; absent when it is counted from the rotation.
      type: str
    key_id:
      description: The KMS key rotated to, with a KMS key store.
      type: str
  sample: {key_store: Local, last_key_rotation_time: "2026-08-01T02:00:02.767044175Z", age_days: 93.41}
key_store:
  description: The key store after a rotation, and otherwise, in check mode included, the key store as it is.
  returned: always
  type: dict
  contains:
    type:
      description: V(Local) or V(KMS).
      type: str
    key_id:
      description: The KMS key in use, with a KMS key store.
      type: str
    last_key_rotation_time:
      description: When the key was last rotated, as the cluster reports it.
      type: str
  sample: {type: Local, last_key_rotation_time: "2026-11-02T02:00:02.767044175Z"}
notes:
  description: What the module noticed, such as a rotation it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["rotation confirmed by reading back after a lost connection"]
maybe_applied:
  description: V(true) when the rotation was sent, the connection was lost, and the key store does not show a rotation.
  returned: on such a failure
  type: bool
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import EncryptionKeyRotation

ARGUMENT_SPEC = {
    "max_age_days": {"type": "int", "required": True},
    "key_id": {"type": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), EncryptionKeyRotation)


if __name__ == "__main__":
    main()
