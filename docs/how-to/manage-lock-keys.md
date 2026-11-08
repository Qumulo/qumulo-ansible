<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Manage the keys that lock snapshots

This page manages the keys in a cluster's file system key store, the keys that lock snapshots: it registers a key, shows
what each key locks, locks each snapshot a snapshot policy takes with a key, replaces a key's public key, and disables
or removes a key.
When you finish, the key store holds the keys you declared, a rerun of each task reports no change, and you can see which
snapshots, snapshot policies and replication targets use each key.
Locking a snapshot with a key is in [Take a snapshot before a change](snapshot-before-a-change.md#lock-the-snapshot).

## Before you start

- Set up the inventory: [Set up an inventory for your clusters](set-up-inventory.md).
  The tasks below take their connection from the `module_defaults` block of the play on
  [Take a snapshot before a change](snapshot-before-a-change.md#write-the-play).
  Put the tasks in a play like that one, saved as `manage-keys.yml`.
- A rebuilt or cloned cluster starts with an empty key store.
  When the inventory's `qumulo_snapshot_policies` declares a policy's `lock_key`, the baseline refuses a key the store
  does not hold, so register the keys before the baseline runs, or set the consent that lets it apply the policies
  without them ([A rebuilt or cloned cluster](#a-rebuilt-or-cloned-cluster)).
- Have the key's private key in unencrypted PEM form in an ansible-vault, and the `openssl` command on the controller,
  to register a key by its private key or to replace one.
  The module refuses a key encrypted with a passphrase of its own.
  Add a private key to the vault as in step 1 of
  [Unlock the snapshot](snapshot-before-a-change.md#unlock-the-snapshot), as `vault_legal_hold_private_key`.
- To replace a key, have both private keys, the old key's and the new key's.

## Register a key by its private key

The cluster registers a key only with its name signed by the private key.
Given the private key, the module derives the public key and signs the name:

```yaml
    - name: Register the legal-hold key in the key store
      qumulo.cluster.security_key:
        name: legal-hold
        private_key: "{{ vault_legal_hold_private_key }}"
        comment: Locks the snapshots kept for legal holds
      tags: [key]
```

Preview it, then run it:

```sh
ansible-playbook -i inventory manage-keys.yml -l edge01 --tags key --check --diff --ask-vault-pass
ansible-playbook -i inventory manage-keys.yml -l edge01 --tags key --ask-vault-pass
```

The check run shows the key's `name`, `comment`, `enabled` and `public_key` in the diff, and the real run reports a
change.
The task returns the key in `security_key`: its `id`, `name`, `comment`, `enabled` and `public_key`.
The private key appears in no result, log or diff.
Rerun it: it reports `ok`.

## Register a key whose private key stays off the controller

Where the private key is kept, make the public key file and sign the key's name:

```sh
openssl pkey -in legal-hold.pem -pubout -out keys/legal-hold.pub
printf %s legal-hold | openssl dgst -sha256 -sign legal-hold.pem | openssl base64 -A
```

Put the signature in the host's vars, such as `inventory/host_vars/edge01.yml`, as `legal_hold_name_signature`.
It is not a secret and does not expire.
Give the signature with the public key:

```yaml
    - name: Register the legal-hold key with a signature of its name
      qumulo.cluster.security_key:
        name: legal-hold
        public_key: "{{ lookup('ansible.builtin.file', 'keys/legal-hold.pub') }}"
        verification_signature: "{{ legal_hold_name_signature }}"
      tags: [key]
```

This needs no `openssl` on the controller, and it only registers the key.
To replace that key later, put both private keys on the controller, as in [Replace a key](#replace-a-key).

## See what a key locks

`snapshot_info` reads every key in the key store with what uses it:

```yaml
    - name: Read the lock keys and what uses each
      qumulo.cluster.snapshot_info:
        gather_subset: [security_keys]
      register: key_facts
      tags: [verify]

    - name: Show each key, whether it is enabled, and what uses it
      ansible.builtin.debug:
        msg: "{{ item.name }} (id {{ item.id }}, {{ 'enabled' if item.enabled else 'disabled' }}): {{ item.usages }}"
      loop: "{{ key_facts.snapshots.security_keys }}"
      loop_control:
        label: "{{ item.name }}"
      tags: [verify]
```

Each entry of `snapshots.security_keys` has `id`, `name`, `comment`, `enabled`, `public_key` and `usages`.
Each usage is a `type`, which is `snapshot`, `snapshot_policy` or `replication_target`, and an `id`, sorted by type and
then by id.
The key's `id` is the `lock_key` of each snapshot it locks.
Add `policies` to the task's `gather_subset`, and `snapshots.policies` names each policy's key by its name in
`lock_key`.
An API user who may not read the key store gets `security_keys` in `unavailable_subsets`, and the other subsets as
before.

## Lock each snapshot a policy takes

`snapshot_policy` takes `lock_key`, the name or id of a key in the key store, and locks each snapshot the policy takes
with it.
Register the key first ([Register a key by its private key](#register-a-key-by-its-private-key)); the task refuses a key
the store does not hold, or a disabled one.

```yaml
    - name: Lock each snapshot the daily policy takes with the legal-hold key
      qumulo.cluster.snapshot_policy:
        policy_name: projects-daily
        lock_key: legal-hold
      tags: [policy-key]
```

Preview it, then run it:

```sh
ansible-playbook -i inventory manage-keys.yml -l edge01 --tags policy-key --check --diff
ansible-playbook -i inventory manage-keys.yml -l edge01 --tags policy-key
```

The diff shows `lock_key` going from null to `legal-hold`.
The policy compares and returns the key by its name, so a task that names the key by id reports no change when the
policy holds that key.
Rerun it: it reports `ok`.
Snapshots the policy took earlier are not changed.

- A policy left without `lock_key` keeps the key it has.
  `lock_key: ""` takes the key off.
- `recreate: true`, which moves a policy to another `path`, keeps the key on the new policy.
- To lock with another key, name it.
- The inventory declares it the same way, as `lock_key` in an entry of `qumulo_snapshot_policies`
  ([the policy recipe](inventory-recipes.md#put-quotas-and-snapshot-policies-on-the-right-directories)).
  [See what a key locks](#see-what-a-key-locks) shows how to read each policy's key by name.

## A rebuilt or cloned cluster

A rebuild or a clone replays the inventory onto a cluster whose key store is empty.
The capture of the source writes each policy's `lock_key` by the key's name, but it cannot write the key
([What capture leaves out](../reference/capture-and-clone.md#what-capture-leaves-out)).
Step 20 of the baseline sets each policy's key, so the key has to be in the store by then.

The baseline reads the key store once at step 2, before it writes anything.
When a declared `lock_key` names a key the store does not hold, it refuses the whole run, in a check run too.
Step 2 checks only a `lock_key` that names a key: one that is unset, null or `""` is not checked.
Step 2 does not check whether a key is disabled: `snapshot_policy` refuses a disabled key at step 20, after the earlier
steps have written, unless the policy already holds it, so enable the key before the run.
The user the play connects as needs `PRIVILEGE_FS_KEY_MANAGEMENT_READ` to read the key store.
An entry with `state: absent` is not checked.
An edge rebuild does not read the key store, since the new edge's is empty: it refuses any declared `lock_key` without
the flag below, before the predestroy changes anything, so a rebuild takes the second way only.
You have two ways on:

1. Register each key on the target before the baseline runs, as
   [Register a key by its private key](#register-a-key-by-its-private-key) shows, then run the baseline.
   The policies get their keys in the same run.
2. Accept that the policies take unlocked snapshots for a while.
   Run the baseline, or the rebuild, with `-e qumulo_allow_deferred_policy_lock_keys=true`.
   A value in group_vars or host_vars has no effect, since the roles set the variable to `false` in their vars, which
   outrank the inventory.
   Step 20 applies each such policy without its declared key, so the policy keeps whatever key it holds; the run finishes
   and lists each one in `cluster_baseline_policy_lock_key_hand_offs`.
   Register the keys, then run the baseline again, without the flag: it sets each key, and the list is empty.
   The snapshots the policy takes between the two runs are not locked.

[Rebuild an edge](edge-rebuild.md#rebuild-the-edge) and
[Clone an edge](clone-edge.md#declare-what-the-clone-leaves-out) say where each way fits in those runs.

## Change a key's comment

```yaml
    - name: Change the legal-hold key's comment
      qumulo.cluster.security_key:
        name: legal-hold
        comment: Locks the snapshots kept for the 2026 audit
      tags: [key]
```

Leave out `public_key` and `private_key` and the key keeps its public key.
Check-run it with `--check --diff` to see the comment change.

## Disable or remove a key

```yaml
    - name: Stop the legal-hold key from locking more snapshots
      qumulo.cluster.security_key:
        name: legal-hold
        enabled: false
      tags: [key-disable]

    - name: Remove the legal-hold key from the key store
      qumulo.cluster.security_key:
        name: legal-hold
        state: absent
      tags: [never, key-remove]
```

A disabled key locks no more snapshots, and it still unlocks the ones it locked.
Set `enabled: true` to lock with it again.

The cluster refuses to disable a key that a snapshot policy or a replication target uses, and to remove a key that a
snapshot, a snapshot policy or a replication target uses; the module refuses the same before any write, in a check run
too.
To remove a key, unlock its snapshots with `snapshot_lock`
([Unlock the snapshot](snapshot-before-a-change.md#unlock-the-snapshot)), take the key off its policies with
`lock_key: ""` on `snapshot_policy` ([Lock each snapshot a policy takes](#lock-each-snapshot-a-policy-takes)), and take
it off its replication targets.
The collection does not manage a replication target's lock key, so use the `qq` command:

```sh
qq replication_set_target_relationship_lock --relationship-id <id> --clear-lock-key
```

[See what a key locks](#see-what-a-key-locks) lists what is left.

The module refuses a key with an empty name, which a key made outside the collection can have.
Remove it by its id, which `security_keys` in `snapshot_info` lists:

```sh
qq fs_security_delete_key -k <id>
```

## Replace a key

To rotate the lock key, replace its public key.
The key keeps its id, name, comment and whether it is enabled, so the snapshots it locks unlock with the new private
key, and the policies that use it keep using it.

The cluster replaces a key only when both private keys sign its replacement challenge.
Have both in the vault, the old key's and the new key's, even when the old key was registered with a signature of its
name.

1. Add the new private key to the vault under a name of its own, such as `vault_legal_hold_private_key_2027`.
   Keep the old one for now.
2. Change the key task to give both keys:

   ```yaml
       - name: Replace the legal-hold key with the 2027 key pair
         qumulo.cluster.security_key:
           name: legal-hold
           private_key: "{{ vault_legal_hold_private_key_2027 }}"
           old_private_key: "{{ vault_legal_hold_private_key }}"
           comment: Locks the snapshots kept for legal holds
         tags: [key]
   ```

3. Preview and run:

   ```sh
   ansible-playbook -i inventory manage-keys.yml -l edge01 --tags key --check --diff --ask-vault-pass
   ansible-playbook -i inventory manage-keys.yml -l edge01 --tags key --ask-vault-pass
   ```

   The check run checks both keys against their public keys and shows `public_key` changing; it asks the cluster for no
   challenge.
   The real run replaces the key and reports a change.
   Rerun it: it reports `ok`, since the key store already holds the new public key.
4. Point every task that gives the key's `private_key`, such as the unlock task, at the new private key.
   A snapshot locked before the replacement unlocks with the new private key, and the old private key unlocks nothing.
   Once the key is replaced, `old_private_key` may come out of the task and the old private key out of the vault; the
   task still reports `ok`.
   Leaving them in is harmless.

## When it fails

Each refusal names what to change; connection and identity failures are in
[Troubleshooting](../reference/troubleshooting.md).
The task refuses before it sends a write, and in a check run too.

- `key <name> is not in the key store; to register it, give public_key or private_key`: the key does not exist and the
  task gave neither.
- `key <name> is not in the key store, and the cluster registers a key only with its name signed by its private key`:
  give `private_key`, or `verification_signature` with `public_key`.
- `public_key and private_key do not match`: give the public half of that private key, or leave `public_key` out.
- `name '<name>' is not a key name`: give a name that is neither empty nor made of digits only.
- `private_key is encrypted with a passphrase`: give the key decrypted, and keep it encrypted with ansible-vault.
- `private_key needs the openssl command on the controller`: install `openssl`, or register the key with
  `verification_signature`.
- `key <name> holds another public key`: replacing a key needs `old_private_key`, the private key of the public key the
  key store holds now, and `private_key`, the new key's.
  See [Replace a key](#replace-a-key).
- `old_private_key does not match the public key of <name> in the key store`: give the private key whose public half
  the key store holds now.
- `key <name> cannot be removed while it is used by <usages>, which the cluster refuses; <steps>`: do the steps the
  message ends with, one for each kind of usage, as in [Disable or remove a key](#disable-or-remove-a-key).
  The message names at most ten usages, then counts the rest; the `security_keys` subset lists all of them.
- `key <name> cannot be disabled while it is used by <usages>`: a snapshot policy or a replication target uses the key.
  A key that only locks snapshots can be disabled.
- The connection dropped after a write was sent: the module does not send it again.
  It reads the key back, and when the key shows the write took, it reports a note saying so.
  Otherwise the task fails with `maybe_applied`
  ([what `maybe_applied` means](../explanation/state-management.md#maybe-applied)).

A policy's `lock_key` is refused before any write, in a check run too:

- `snapshot policy '<name>': the file system key store holds no key <key>; register it first with
  qumulo.cluster.security_key`: `lock_key` names a key the store does not hold.
  Register it ([Register a key by its private key](#register-a-key-by-its-private-key)), or correct the name.
- `snapshot policy '<name>': the key <key> is disabled, and the cluster sets no disabled key on a policy; enable it
  first with enabled: true on qumulo.cluster.security_key`: set `enabled: true` on the key, or name another.
  A policy that already holds the key is no change.
- `snapshot policy '<name>': a recreate would set the key <key> on the new policy, and the API user may not read the key
  to know whether the cluster takes it; give the user PRIVILEGE_FS_KEY_MANAGEMENT_READ, or take the key off with
  lock_key: "" first`: the user may not read the key store, so the module refuses a `recreate` before it deletes the
  policy.
- `qumulo_snapshot_policies declares lock keys for <policy> (key <key>), and the key store holds no such key, so the
  policies' snapshots would not be locked; register each key first with qumulo.cluster.security_key, or pass
  -e qumulo_allow_deferred_policy_lock_keys=true to apply the policies without their keys now and set the keys on a
  later run`: step 2 of the baseline refused the whole run before it wrote anything.
  Do one of the two ways in [A rebuilt or cloned cluster](#a-rebuilt-or-cloned-cluster).
  When the API user may not read the key store, the message says `the key store could not be read` and why in place of
  `the key store holds no such key`; give that user `PRIVILEGE_FS_KEY_MANAGEMENT_READ`, which `snapshot_policy` needs
  too to set a key, or pass the flag.
- `<host>: qumulo_snapshot_policies declares lock keys for <policies>, and the rebuilt edge's key store is empty until
  each key is registered again with qumulo.cluster.security_key, which takes its private key or a signature of its name;
  pass -e qumulo_allow_deferred_policy_lock_keys=true to the rebuild, so the replay applies those policies without their
  keys and hands them off, then register the keys and run the baseline again`: the edge rebuild refused before the
  predestroy changed anything, in either form of the rebuild ([Rebuild an edge](edge-rebuild.md#before-you-start)).

A `snapshot_lock` task refuses a disabled key, and a key the store does not hold; those are in
[Take a snapshot before a change](snapshot-before-a-change.md#when-it-fails).

Every option and return value is in `ansible-doc qumulo.cluster.security_key`.
