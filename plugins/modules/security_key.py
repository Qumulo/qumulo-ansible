#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: security_key
short_description: Manage a key that locks snapshots in a Qumulo cluster's file system key store
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Registers, changes, replaces or removes one key in the cluster's file system key store. The store holds the public
    half of an ECDSA key, such as a P-256 key; M(qumulo.cluster.snapshot_lock) locks a snapshot with it, and only a
    signature by the private half unlocks it.
  - A key is matched by its O(name). The cluster gives each key an id, which the module returns and never matches on.
  - The cluster takes a new key only with its name signed by the private key, so a key that does not exist yet needs
    O(private_key) or O(verification_signature).
  - A key that holds another public key is replaced through the cluster's replacement challenge. The cluster takes the
    replacement only with the challenge signed by both private keys, and the module signs it in the run that sends the
    replacement, so replacing a key needs the old and the new private key on the controller, a key registered with
    O(verification_signature) included. The key keeps its id, comment, state and usages, so what it locked unlocks with
    the new private key.
options:
  name:
    description:
      - The key's name, unique in the key store. A name of digits only, or an empty one, is refused before any request;
        the cluster stores an empty name, which no request can then name (QUIRK-120).
      - The module does not rename a key. A key renamed outside the collection is not found, and a key of this name is
        created beside it.
    type: str
    required: true
  state:
    description: Whether the key is in the key store.
    type: str
    choices: [present, absent]
    default: present
  public_key:
    description:
      - The key's public half, in PEM or as base64 DER. Compared as the base64 DER the cluster stores.
      - With O(private_key) it may be left out, and the module derives it; when both are given they must match.
      - To create the key, give this or O(private_key). On an existing key, leaving out both keeps the key's public key.
    type: str
  private_key:
    description:
      - The key's private half, in unencrypted PEM. A key encrypted with a passphrase of its own is refused.
      - To create the key, the module signs its O(name) with it. To replace a key, it is the new key, which signs the
        replacement challenge.
      - The module signs with the C(openssl) command on the controller. It writes the key to a file only the
        controller's user can read, and removes the file once openssl exits. The key is never logged, returned or shown
        in a diff.
    type: str
  verification_signature:
    description:
      - The key's O(name) signed with its private key, ECDSA over SHA-256, as base64 of the DER signature, the value
        C(qq fs_security_add_key --verification-signature) takes. It creates the key without giving the module the
        private key.
      - Used only to create the key, and ignored when the key exists. It cannot replace a key, because the replacement
        challenge must be signed, not the name.
      - Give this or O(private_key), not both. Requires O(public_key).
    type: str
  old_private_key:
    description:
      - The private half of the public key the key store holds now, in unencrypted PEM. Requires O(private_key).
      - Needed only when the key store holds a public key other than the one the task declares, through O(public_key) or
        through O(private_key); the module then replaces the key. Without it the module refuses the replacement.
      - The module checks it against the key store's public key, and O(private_key) against the declared one, before it
        asks the cluster for a challenge.
      - Not used, and not checked, when the key store already holds the declared public key, so a task that replaced the
        key reports no change when it runs again. When no key of this O(name) exists, the module registers the key and
        adds a note to RV(notes) that this option went unused.
      - Handled as O(private_key) is, and never logged, returned or shown in a diff.
    type: str
  comment:
    description: The key's comment. Left unset, an existing key keeps its comment, and a new key gets an empty one.
    type: str
  enabled:
    description:
      - Whether the key can lock snapshots. A disabled key still unlocks what it locked.
      - Left unset, an existing key keeps its state, and a new key is enabled.
      - The cluster refuses to disable a key that a snapshot policy or a replication target uses, so the module refuses
        it before any write, in check mode too. A key that only locks snapshots can be disabled.
    type: bool
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Checks the private keys against their public keys, and asks the cluster for no replacement challenge.
      - Refuses what a real run refuses before any write, such as a create without a signature, a replacement without
        O(old_private_key) or a key in use.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET /v1/file-system/security/keys/{name}; POST /v1/file-system/security/keys/; GET, PATCH and DELETE
    /v1/file-system/security/keys/{id}; GET /v1/file-system/security/keys/{id}/usages; GET
    /v1/file-system/security/keys/{id}/key-replacement-challenge and POST /v1/file-system/security/keys/{id}/replace."
  - With O(private_key) or O(old_private_key) the controller needs the C(openssl) command; the module fails before any
    request without it.
  - The cluster refuses to delete a key that a snapshot, a snapshot policy or a replication target uses, so the module
    refuses it before any write, naming each by type and id; a refusal names at most ten, then counts the rest.
    The refusal ends with the step that frees each kind of usage.
  - The module returns no usages. M(qumulo.cluster.snapshot_info) reads them in its V(security_keys) subset.
  - If the connection is lost after a write was sent, the module reads the key back to decide, and never sends it
    twice.
  - A replacement together with a change of O(comment) or O(enabled), and a new key with O(enabled=false), are each two
    writes, since the create takes no disabled field. If the connection is lost after the first write and
    reading the key back shows it took, the module adds a note to RV(notes) and sends the second write. If the second
    write fails, the task fails with C(changed) true, since the first took.
seealso:
  - module: qumulo.cluster.snapshot_lock
    description: Locks a snapshot with a key from the key store, and unlocks it with the private key.
  - module: qumulo.cluster.snapshot_info
    description: Lists the keys with what uses each, in its V(security_keys) subset.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# Keep private keys in an ansible-vault, unencrypted inside it; the module refuses a key with a passphrase of its own.
- name: Register the legal-hold key, signing its name with the private key
  # The module derives the public key from the private one. A second run changes nothing.
  qumulo.cluster.security_key:
    name: legal-hold
    private_key: "{{ vault_legal_hold_private_key }}"
    comment: Locks the snapshots kept for legal holds

- name: Register the legal-hold key without the private key reaching the controller
  # Where the private key is kept, make the public key file and sign the name:
  #   openssl pkey -in legal-hold.pem -pubout -out keys/legal-hold.pub
  #   printf %s legal-hold | openssl dgst -sha256 -sign legal-hold.pem | openssl base64 -A
  # Put the signature in the host's vars as legal_hold_name_signature. It is not a secret and does not expire. It
  # registers the key and does nothing else: to rotate the key later, both private keys must be on the controller.
  qumulo.cluster.security_key:
    name: legal-hold
    public_key: "{{ lookup('ansible.builtin.file', 'keys/legal-hold.pub') }}"
    verification_signature: "{{ legal_hold_name_signature }}"

- name: Preview a change of the legal-hold key's comment
  # Leaving out the keys keeps the key's own. The diff shows the comment going from the old text to the new.
  qumulo.cluster.security_key:
    name: legal-hold
    comment: Locks the snapshots kept for the 2026 audit
  check_mode: true
  diff: true

- name: Change the legal-hold key's comment
  qumulo.cluster.security_key:
    name: legal-hold
    comment: Locks the snapshots kept for the 2026 audit

- name: Stop the legal-hold key from locking more snapshots
  # A disabled key still unlocks the snapshots it locked, and M(qumulo.cluster.snapshot_lock) refuses to lock with it.
  # The cluster refuses to disable a key that a snapshot policy or a replication target uses, and so does the module,
  # naming each. Set enabled: true to lock with the key again.
  qumulo.cluster.security_key:
    name: legal-hold
    enabled: false

- name: Replace the legal-hold key with a new key pair, keeping what it locks
  # Rotating a key needs both private keys on the controller, whether the key was registered with its private key
  # or with a signature of its name: the cluster takes a replacement only with its challenge signed by both.
  # The key keeps its id, so the snapshots it locks unlock with the new private key from now on, and a policy that
  # locks with it keeps locking. The old private key unlocks nothing afterwards. A second run finds the new public key
  # in place and changes nothing, so old_private_key may stay in the task.
  qumulo.cluster.security_key:
    name: legal-hold
    private_key: "{{ vault_legal_hold_private_key_2027 }}"
    old_private_key: "{{ vault_legal_hold_private_key }}"
    comment: Locks the snapshots kept for legal holds

- name: Remove the legal-hold key from the key store
  # Refused while a snapshot, a snapshot policy or a replication target uses the key, and the refusal ends with the
  # step that frees each kind of usage. Unlock the snapshots with M(qumulo.cluster.snapshot_lock), take the key off each
  # policy with lock_key: "" on M(qumulo.cluster.snapshot_policy), and take it off each replication target. The
  # collection does not manage a target's lock key, so qq does:
  # qq replication_set_target_relationship_lock --relationship-id <id> --clear-lock-key.
  # Remove a key with an empty name, which this module refuses, by its id: qq fs_security_delete_key -k <id>.
  qumulo.cluster.security_key:
    name: legal-hold
    state: absent
"""

RETURN = r"""
security_key:
  description: The key after the task, or an empty dict after O(state=absent).
  returned: always
  type: dict
  contains:
    id:
      description: The key's id, which M(qumulo.cluster.snapshot_info) reports as a snapshot's C(lock_key). Missing in
        check mode before the key exists.
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
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost, or an
    O(old_private_key) that went unused because the key did not exist.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the key
    back did not confirm it. The task fails with C(changed) true.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the options that did not converge.
  returned: on a failure when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {enabled: false}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: on a failure when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {enabled: true}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.security_keys import SecurityKey

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "public_key": {"type": "str", "no_log": False},
    "private_key": {"type": "str", "no_log": True},
    "verification_signature": {"type": "str", "no_log": False},
    "old_private_key": {"type": "str", "no_log": True},
    "comment": {"type": "str"},
    "enabled": {"type": "bool"},
}


def main():
    module = qumulo_module(
        ARGUMENT_SPEC,
        mutually_exclusive=[("private_key", "verification_signature")],
        required_by={"old_private_key": "private_key", "verification_signature": "public_key"},
    )
    run_resource(module, SecurityKey)


if __name__ == "__main__":
    main()
