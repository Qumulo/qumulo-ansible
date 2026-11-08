<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Take a snapshot before a change

This page takes a snapshot of a directory before a change of your own, such as an upgrade or a migration, finds that
snapshot again when you rerun the play, and locks it so that nothing deletes it early.
When you finish, the cluster holds one named snapshot of the directory, a rerun takes no second one, and, if you locked
it, nothing deletes the snapshot before it expires, and only the key's private key unlocks it sooner.

The baseline's snapshot policies take snapshots on a schedule
([Declare common settings in the inventory](inventory-recipes.md)).
This page is for the one-off snapshot you take by hand.

## Before you start

- Set up the inventory: [Set up an inventory for your clusters](set-up-inventory.md).
  The example inventory vaults its API passwords, so the commands on this page pass `--ask-vault-pass`; use
  `--vault-password-file` in place of it if you keep the vault password in a file.
- Use a directory that exists on the cluster.
  The play refuses a missing directory, except in a check run, where it reports that an earlier task is expected to
  create it.
- On an edge, snapshot a directory that is not on or under a portal's spoke root.
  The cluster takes no snapshot there, so the play refuses before it sends the request, in a check run too
  (G-SPOKE-PATHS, [Gaps](../reference/gaps.md)); snapshot that data on the hub
  ([why](../explanation/edges-and-portals.md#spoke-roots-hold-the-hubs-data)).
  The play below uses `/local/scratch`, the example inventory's directory on `edge01` outside its spoke root
  `/projects`.
- To lock the snapshot, have the lock key in the cluster's file system key store, or have its private key in
  unencrypted PEM form in an ansible-vault and the `openssl` command on the controller, so that
  [Lock the snapshot](#lock-the-snapshot) registers it.
  [Manage the keys that lock snapshots](manage-lock-keys.md) covers registering a key without its private key.
- To unlock the snapshot, or to lock one that never expires, have the key's private key in an ansible-vault and
  `openssl` on the controller as well.

## Write the play

Put the play in a file of your own.
The `module_defaults` block is the header every playbook of the collection starts with; it hands the inventory's
connection variables to each module ([Set up an inventory](set-up-inventory.md)).

```yaml
# snapshot-before-change.yml
- name: Snapshot a directory before a change
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
    - name: Snapshot the scratch directory before the migration
      qumulo.cluster.snapshot:
        path: /local/scratch
        name: before-migration
        expiration_time_to_live: 2weeks
        reuse_existing: true
      register: before_migration
      tags: [snapshot]

    - name: Show the snapshot's id and full name
      ansible.builtin.debug:
        msg: "{{ before_migration.snapshot.id | default('not taken yet') }}: {{ before_migration.snapshot.name }}"
      tags: [snapshot]
```

`reuse_existing: true` with a `name` is what makes a rerun safe.
The cluster names each snapshot `<id>_<name>`, with an id it generates, so a plain rerun takes another snapshot;
with `reuse_existing`, the module looks for a snapshot of the directory named `<id>_before-migration` and takes none
when it finds one ([why](../explanation/state-management.md#keys-not-ids), and
[the trade-off](../explanation/state-management.md#actions-are-not-settings)).

`expiration_time_to_live` is a whole number and a unit: `90minutes`, `12hours`, `7days`, `2weeks` or `6months`.
The time counts from when the task runs on the controller.
When the task finds an existing snapshot, that snapshot keeps its own expiration, whatever the task says.

## Take the snapshot

1. Preview the run on one host:

   ```sh
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags snapshot --check --diff --ask-vault-pass
   ```

   The check run reports the snapshot it would take, with its expiration.
   The id is known only once the snapshot is taken, so the `debug` task shows `not taken yet`.
2. Take it:

   ```sh
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags snapshot --ask-vault-pass
   ```

   The `debug` task prints the snapshot's id and full name, such as `12: 12_before-migration`.
3. Run it again.
   The `snapshot` task reports `ok` and returns the snapshot the first run took.

## Lock the snapshot

Lock the snapshot so that nothing deletes it, or shortens its life, before it expires or you unlock it.
Only a signature made with the key's private key unlocks it, and the cluster keeps only the public key.

1. If the key store already holds the key, go to step 3.
   Otherwise add the private key to the inventory, unencrypted and inside an ansible-vault, as
   `vault_legal_hold_private_key`:

   ```sh
   ansible-vault encrypt_string --stdin-name vault_legal_hold_private_key < legal-hold.pem
   ```

   Paste the output into the host's vars file, such as `inventory/host_vars/edge01.yml`.
2. Register the key in the key store.
   Add the task to the play, after the tasks above:

   ```yaml
       - name: Register the legal-hold key in the key store
         qumulo.cluster.security_key:
           name: legal-hold
           private_key: "{{ vault_legal_hold_private_key }}"
           comment: Locks the snapshots kept before a change
         tags: [snapshot, key]
   ```

   The module derives the public key from the private key and signs the name `legal-hold` with it, because the cluster
   registers a key only with that signature.
   The private key appears in no result, log or diff.
   When the key store already holds that key, the task reports `ok`.
   [Manage the keys that lock snapshots](manage-lock-keys.md) shows how to see what the store holds, and how to
   register a key without its private key.
   Preview and run the task by itself, before the lock task exists:

   ```sh
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags key --check --diff --ask-vault-pass
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags key --ask-vault-pass
   ```

   The check run shows the key's `name`, `comment`, `enabled` and `public_key` in the diff, and the real run reports a
   change.
   Rerun it: it reports `ok`.
   Run the key task first because, in a check run, the lock task fails while the key store holds no such key.
3. Add the lock task to the play, after the tasks above:

   ```yaml
       - name: Lock the snapshot until it expires
         qumulo.cluster.snapshot_lock:
           snapshot_id: "{{ before_migration.snapshot.id }}"
           lock_key: legal-hold
         when: before_migration.snapshot.id is defined
         tags: [snapshot]
   ```

   `lock_key` is the key's name or id in the key store.
   The `when` skips the task in a check run before the snapshot exists, because there is no id to lock yet.
   To preview the lock, check-run the play after step 2 of [Take the snapshot](#take-the-snapshot) has taken the
   snapshot and the key store holds the key.
4. Preview and run:

   ```sh
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags snapshot --check --diff --ask-vault-pass
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags snapshot --ask-vault-pass
   ```

   The first run reports a change, and `--diff` shows `lock_key` going from null to `legal-hold`.
   The task returns the lock in `snapshot_lock`: the `snapshot_id`, `name`, `path`, `expiration` and `lock_key`.
   Rerun it: it reports `ok`.

A snapshot locked with another key is refused; unlock it with that key first.

## Make the change, then check the snapshot

Make your change.
`before_migration.snapshot` holds the snapshot's `id`, `name`, `path`, `timestamp` and `expiration`.

To read the snapshots back, add tasks that use `qumulo.cluster.snapshot_info`:

```yaml
    - name: Read the cluster's snapshots
      qumulo.cluster.snapshot_info:
        gather_subset: [snapshots]
      register: snapshot_facts
      tags: [verify]

    - name: Show the snapshots of the scratch directory and whether each is locked
      ansible.builtin.debug:
        msg: "{{ item.name }}: {{ 'locked with key ' ~ item.lock_key if item.lock_key is not none else 'unlocked' }}"
      loop: >-
        {{ snapshot_facts.snapshots.snapshots
           | selectattr('path', 'equalto', '/local/scratch') | list }}
      loop_control:
        label: "{{ item.name }}"
      tags: [verify]
```

```sh
ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags verify --ask-vault-pass
```

Each entry of `snapshots.snapshots` has `name`, `expiration`, `in_delete` and `lock_key`, the id of the key that locks
the snapshot, or null while it is unlocked.

## Unlock the snapshot

The unlock signs the cluster's unlock challenge with the key's private key.

1. Add the private key to the inventory, unencrypted and inside an ansible-vault.
   A PEM spans several lines, so give it to the command on standard input:

   ```sh
   ansible-vault encrypt_string --stdin-name vault_legal_hold_private_key < legal-hold.pem
   ```

   Paste the output into the host's vars file, such as `inventory/host_vars/edge01.yml`, or into a group's
   ([Keep secrets out of the inventory](store-secrets.md#encrypt-a-license-document)).
2. Add a task that runs only when asked for, and give it the snapshot's full name:

   ```yaml
       - name: Unlock the snapshot once the migration is signed off
         qumulo.cluster.snapshot_lock:
           name: "{{ snapshot_name }}"
           state: unlocked
           private_key: "{{ vault_legal_hold_private_key }}"
         tags: [never, unlock]
   ```

3. Run it with the snapshot's full name, as the `verify` run lists it:

   ```sh
   ansible-playbook -i inventory snapshot-before-change.yml -l edge01 --tags unlock -e snapshot_name=12_before-migration --ask-vault-pass
   ```

The module checks the key against the key store before it asks the cluster for an unlock challenge.
A check run (`--check`) checks the key and asks for no challenge.
Unlocking a snapshot that is already unlocked changes nothing.

## Lock a snapshot that never expires

The module locks a snapshot that never expires only when the task gives the matching `private_key`
([why](../explanation/refusals-and-guards.md#refuse-dont-guess)).
Add the private key to the inventory as in step 1 of [Unlock the snapshot](#unlock-the-snapshot), then take a snapshot
with no expiration and lock it with the key:

```yaml
    - name: Snapshot the scratch directory and keep it until it is deleted
      qumulo.cluster.snapshot:
        path: /local/scratch
        name: audit-hold
        reuse_existing: true
      register: audit_hold

    - name: Lock the snapshot that never expires, proving the key can unlock it
      qumulo.cluster.snapshot_lock:
        snapshot_id: "{{ audit_hold.snapshot.id }}"
        lock_key: legal-hold
        private_key: "{{ vault_legal_hold_private_key }}"
      when: audit_hold.snapshot.id is defined
```

A snapshot with no `expiration_time_to_live`, or with `never`, is kept until you delete it.
To lock such a snapshot with the key's name alone, give it an expiration first, for example with
`qq snapshot_modify_snapshot --id <id> --expiration <time>`.

## Variants

- **A snapshot without a name of your own.**
  Leave out `name` and `reuse_existing`.
  The cluster names the snapshot `<id>_OnDemand`, and every run takes a new one.
  Without `reuse_existing`, every run of the task takes another snapshot and reports a change.
- **Lock a snapshot from an earlier run.**
  `snapshot_lock` takes the snapshot's full name in `name` instead of `snapshot_id`; give one, not both.

## When it fails

Each refusal names what to change; connection and identity failures are in
[Troubleshooting](../reference/troubleshooting.md).
The usual ones:

- `<path> does not exist; create it first`: create the directory, for example with `qumulo.cluster.directory`.
  A path that is a file is refused too.
- The directory is under a spoke root: snapshot it on the hub.
- `there is no snapshot named <name>`: `snapshot_lock` takes the full name, `<id>_<name>`, not the name the task
  gave; read it from the `verify` run.
- The key store holds no such key: register it with `qumulo.cluster.security_key`, as in
  [Lock the snapshot](#lock-the-snapshot).
  A check run of the lock task fails this way until a real run of the key task registers the key.
- The snapshot is locked with another key: unlock it with that key's private key first.
- The snapshot is being deleted: it cannot be locked.
- `to unlock the snapshot, give private_key`: add `private_key` to the task.
- `private_key` does not match the key store: give the key whose public half the store holds.
- The snapshot never expires and the task gives no `private_key`: give the key, or set an expiration with
  `qq snapshot_modify_snapshot` first, as in [Lock a snapshot that never expires](#lock-a-snapshot-that-never-expires).
- `the key <name> is disabled`: the cluster locks nothing with a disabled key; set `enabled: true` on the key task.
  A snapshot the disabled key already locks reports `ok`, and the key still unlocks it.
- Refusals of the `security_key` task, such as a key that cannot be created, replaced, removed or disabled, are in
  [Manage the keys that lock snapshots](manage-lock-keys.md#when-it-fails).
- The connection dropped after the request was sent: the module does not send it again.
  With a `name`, it lists the directory's snapshots to find the one the request took, and reports a note saying so.
  With a `name` and `reuse_existing`, a rerun finds the snapshot or takes it.
  Without a `name`, or when it finds none, the task fails and says a snapshot may have been taken: run the `verify`
  tasks to list the directory's snapshots before you run it again
  ([what `maybe_applied` means](../explanation/state-management.md#maybe-applied)).

Every option and return value is in `ansible-doc qumulo.cluster.snapshot`, `qumulo.cluster.snapshot_lock`,
`qumulo.cluster.security_key` and `qumulo.cluster.snapshot_info`.
To upgrade after taking the snapshot, see [Upgrade a fleet](upgrade.md).
