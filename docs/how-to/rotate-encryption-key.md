<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Rotate the encryption key

This page rotates the master key that encrypts a cluster's data at rest, with a play of your own, and checks that the
cluster reports the new rotation.
The baseline never rotates the key, because a rotation is an action rather than a setting
(G-ACTIONS in the [gap register](../reference/gaps.md);
[why](../explanation/state-management.md#actions-are-not-settings)).

## Before you start

- Set up an inventory that connects to the cluster: [Set up an inventory for your clusters](set-up-inventory.md).
- Use a cluster formed with encryption at rest.
  Encryption is chosen when a cluster is formed, and on a cluster formed without it the task fails because there is no
  key to rotate.
- With a KMS key store, create the new key in the KMS first and have its id ready.
  The cluster rotates a KMS key only to another key the KMS holds (G-MON-KMS-ROTATE in the [gap register](../reference/gaps.md)).
  The collection does not manage the KMS key store itself, its host or its certificates.

## Rotate the key when it is older than a given age

This section is for a cluster with its own key store.
With a KMS key store, use [Rotate a KMS key](#rotate-a-kms-key) instead.

Save this play as `rotate-key.yml` in the project directory.
The `module_defaults` block is the header every playbook of the collection starts with
([Check the connection](set-up-inventory.md#check-the-connection) shows it).

```yaml
# rotate-key.yml
- name: Rotate the encryption key
  hosts: qumulo_clusters
  gather_facts: false
  module_defaults:
    group/qumulo.cluster.qumulo:
      api_host: "{{ qumulo_api_host }}"
      api_port: "{{ qumulo_api_port | default(8000) }}"
      api_username: "{{ qumulo_api_username | default('admin') }}"
      api_password: "{{ qumulo_api_password | default('') }}"
      api_token: "{{ qumulo_api_token | default('') }}"
      validate_certs: "{{ qumulo_validate_certs | default(true) }}"
      ca_path: "{{ qumulo_ca_path | default(omit) }}"
      expected_cluster_uuid: "{{ qumulo_cluster_uuid | default('') }}"
  tasks:
    - name: Rotate the encryption key when it is older than 90 days
      qumulo.cluster.encryption_key_rotation:
        max_age_days: 90
```

Run it on one host first, with `-l`.
Preview with a check run, then rotate:

```sh
ansible-playbook -i inventory rotate-key.yml -l edge01 --ask-vault-pass --check --diff
ansible-playbook -i inventory rotate-key.yml -l edge01 --ask-vault-pass
```

Use `--vault-password-file` in place of `--ask-vault-pass` if you keep the vault password in a file, and leave both out
when the inventory holds no vault values.

When the key is due, the check run reports `changed`, and `--diff` shows its age and last rotation time.
When the key is not older than `max_age_days`, the task reports `ok` and shows no age.
The age is measured against the controller's clock, in UTC.
A key the cluster reports as never rotated is due.

A play run on a schedule rotates the key at the first run after it passes `max_age_days`, and changes nothing on the
runs between.
With a KMS key store this play fails once the key is older than `max_age_days`, because the module has no key to
rotate to without a `key_id`.

## Verify the rotation

Run the check again.
The task now reports `ok`, because the key is new:

```sh
ansible-playbook -i inventory rotate-key.yml -l edge01 --ask-vault-pass --check --diff
```

To see when the cluster says the key was last rotated, read the key store with
[monitoring_info](../../plugins/modules/monitoring_info.py).
Add these tasks to the play, or to a play of your own with the same header:

```yaml
    - name: Read the encryption key store
      qumulo.cluster.monitoring_info:
        gather_subset: [encryption]
      register: encryption_state

    - name: Show when the key was last rotated
      ansible.builtin.debug:
        var: encryption_state.monitoring.encryption.last_key_rotation_time
```

Or register the rotation task and read `key_store.last_key_rotation_time`.

## Variants

### Rotate now, whatever the key's age

On a cluster with its own key store, `max_age_days: 0` rotates on every run, so run such a task once.
A schedule must not carry it.
With a KMS key store, give a new `key_id` instead ([Rotate a KMS key](#rotate-a-kms-key)).

```yaml
    - name: Rotate the encryption key now, whatever its age
      qumulo.cluster.encryption_key_rotation:
        max_age_days: 0
```

### Fail a pipeline when the key is overdue

A check run reports `changed` when the key is due.
Turn that into a failure, and rotate in a separate run:

```yaml
    - name: Fail when the key is older than a year
      qumulo.cluster.encryption_key_rotation:
        max_age_days: 365
      check_mode: true
      register: key_overdue
      failed_when: key_overdue is changed
```

### Rotate a KMS key

With a KMS key store the module rotates only when `key_id` differs from the key in use, whatever the key's age, and
leaves the key alone when `key_id` is the key in use.
A schedule with one fixed `key_id` therefore rotates once and never again.
Give a new `key_id` each period, for example from a variable you change when you create the next key in the KMS:

```yaml
    - name: Rotate to a new key the KMS holds
      qumulo.cluster.encryption_key_rotation:
        max_age_days: 365
        key_id: "{{ qumulo_kms_key_id }}"
```

With `key_id` set, `max_age_days` is not checked, but it is still required.
Without `key_id`, a KMS key older than `max_age_days` fails, and the message names the key in use.
A KMS key is aged from the later of its last rotation and when the KMS was configured, so a key never rotated is aged
from the configuration.

## If a run stops

The module never sends a rotation twice.
When the connection is lost after the module sent it, the module reads the key store again.
If the key store shows the rotation, the task succeeds and `notes` says it was confirmed by reading back.
If it does not, the task fails with `maybe_applied: true`.

Run the check again and read what it says.
If the key is still due, run the play again.
[A run that stopped in the middle](respond-to-a-refusal.md#a-run-that-stopped-in-the-middle) says what to do with
`maybe_applied`, and [why](../explanation/state-management.md#maybe-applied) the module never resends.

If the cluster accepts the rotation but the key store still shows the old rotation time (or the old key), the task fails
with `changed: true`.
Run the check again before you retry.

## What next

- [Take a snapshot before a change](snapshot-before-a-change.md) is another action you run from a play of your own.
- [Actions are not settings](../explanation/state-management.md#actions-are-not-settings) explains why the baseline
  never rotates the key and why a rerun does not rotate it again.
- [encryption_key_rotation](../../plugins/modules/encryption_key_rotation.py) lists every option and return value.
- [Respond to a refusal](respond-to-a-refusal.md) and
  [A change that may or may not have been applied](../reference/troubleshooting.md#a-change-that-may-or-may-not-have-been-applied)
  cover a task that fails or stops in the middle.
