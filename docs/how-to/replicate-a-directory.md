<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Replicate a directory to another cluster

This page declares a replication relationship in the inventory, from a directory on one cluster to a directory on
another, and has the baseline create and authorize it.
When you finish, the target directory holds a read-only copy of the source directory, and both clusters report the
relationship `ESTABLISHED`.
Variants cover a target that already holds data, schedules and other settings, a new address, a release, an edge
without a license yet, and driving the two modules yourself; the last section removes a relationship.
For why creating and authorizing are two steps, and what authorizing overwrites, see
[Replication](../explanation/replication.md).

## Before you start

- Set up the inventory with both clusters as hosts, as [Set up an inventory for your clusters](set-up-inventory.md)
  shows.
  Both hosts need `qumulo_cluster_uuid`.
  The examples replicate from `edge01`, the source, to `dr01`, the target.
  If you copied the example inventory, its `edge01` already declares a relationship to `hub01`, with `hub01`'s address
  and path-only directory: adapt that entry, and read this page's `dr01` names as `hub01`'s.
  If the inventory holds vault values, add `--ask-vault-pass` or `--vault-password-file` to every `ansible-playbook`
  command below ([Keep secrets out of the inventory](store-secrets.md)).
- Have the source directory.
  Declare it in the source host's `qumulo_directories`, or create it first.
  A source directory that does not exist stops the run.
- Pick the address the source reaches the target's replication service at.
  It is an IP address, never a host name, and it is yours to make reachable, behind NAT included.
  The port is 3712 unless the target serves another.
  Choose it as you choose a portal's address ([Choose the addresses](pair-edge-with-hub.md#choose-the-addresses)).
  The collection never derives it from `qumulo_api_host` or from the cluster's network status.
  For a target that Terraform formed, the inventory plugin sets it from the output's `replication_address`
  ([Publish the output](terraform.md#publish-the-output)).
- Know that authorizing makes the target directory read-only and lets the source's content replace the directory's.
  A target directory that is empty, or created by this run, loses nothing.

## Declare the relationship

The relationship is declared on the source host, and the target host declares the other half.

1. On the source host, list the relationship:

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_directories:
     - path: /projects
   qumulo_replication_relationships:
     - source_path: /projects
       target: dr01                         # an inventory host
       target_path: /replicas/edge01-projects
   ```

2. On the target host, declare the target directory by its path alone, and give the address the source reaches it at:

   ```yaml
   # inventory/host_vars/dr01.yml
   qumulo_replication_address: 203.0.113.40
   qumulo_directories:
     - path: /replicas/edge01-projects      # path only; replication sets its attributes from the source
   ```

   Declare nothing inside the target directory.
   Give the entry only `path`: a `mode`, `owner`, `group` or `state: absent` on it stops the run.
   `qumulo_replication_port` defaults to 3712. Set it on the target host only if the target's replication service
   listens on another port.
   Each target directory serves one relationship, and no two hosts declare one address and port.
   The rules are in [Inventory variables](../reference/variables.md#replication).

At step 21, before it touches any relationship, the baseline checks these declarations and fails naming each one that is
wrong.

## Preview the relationship

Run the baseline on the source host with `--check --diff`:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
```

`-l edge01` is enough: the run reaches `dr01` with the connection variables in its `host_vars` and `group_vars`.
A check run writes nothing on either cluster.
It runs both sides' checks, so a refusal appears now, with every unmet condition of the two clusters reported at once
and what would lift each.
[If the preview refuses](#if-the-preview-refuses) says what to do about each, and
[Respond to a refusal](respond-to-a-refusal.md) covers the general case.

## Apply it

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
```

Step 21 of the [`cluster_baseline`](../../roles/cluster_baseline/) role runs the [`replication_pair`](../../roles/replication_pair/) role for the
relationship, in this order:

1. It checks both sides and stops before any write if a condition is unmet and nothing lifts it.
2. It creates the target directory, path only, after any ancestor that `dr01` declares.
   The run creates it if `dr01` has not run yet, so the source and the target may run in either order.
3. It creates the relationship on `edge01`.
   The create registers it on `dr01` too, awaiting authorization.
4. It authorizes the relationship on `dr01` and waits until `dr01` reports it `ESTABLISHED`.
5. It waits until `edge01`'s record leaves `AWAITING_AUTHORIZATION`, since the source learns of the authorize on its next
   poll, and fails if the record ENDED instead.

A second run changes nothing.

## Verify

Read the relationship from both ends with [`replication_info`](../../plugins/modules/replication_info.py):

```yaml
# read-replication.yml
- name: Read the relationship from both ends
  hosts: edge01,dr01
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
    - name: Read the relationships this cluster is the source or the target of
      qumulo.cluster.replication_info:
        gather_subset: [source, target]
      register: relationships

    - name: Show them
      ansible.builtin.debug:
        var: relationships.replication
```

```sh
ansible-playbook read-replication.yml -i inventory
```

- `edge01` has an entry with `source_path: /projects` in `replication.source_relationships`, and `dr01` has one with
  `target_path: /replicas/edge01-projects` in `replication.target_relationships`, both with the same `id`.
- Both have `state: ESTABLISHED` and `error_from_last_job: null`.
- Both report `target_root_read_only: true`, the sign that the authorize made the target directory read-only.
- The source's entry has `source_path: /projects`, `target_path: /replicas/edge01-projects` and
  `target_address: 203.0.113.40`.
  The target records neither the address nor the settings, so its entry holds null there.
- `recovery_point` is null until the first job completes.
  A set `recovery_point` means the target holds a copy.

`replication_info` with `relationship_id` and `side` reads one relationship's record on a cluster.
Run it on both clusters to compare the two entries key by key.

## If the preview refuses

Before it authorizes, the target checks the directory it will replace and refuses before any write while a check is
unmet.
Each refusal names its row of the overwrite table, such as `O1`, and what lifts it.
What each row means is in [The overwrite table](../explanation/replication.md#the-overwrite-table).
Do this for the row the refusal names:

- **The target directory must exist.**
  The baseline creates it from the target host's `qumulo_directories`.
  A hand-run authorize fails until the directory exists, because the module never creates it.
- **O1**, the target directory holds an entry, a subdirectory included: set `allow_non_empty_target: true` on the
  relationship's entry, after you have read what the directory holds
  ([Replace the content of a target directory](#replace-the-content-of-a-target-directory)).
- **O2**, another relationship's live record holds the target path: pass the release the refusal prints, which names O2
  and the holder's id ([Lift a refusal with a release](#lift-a-refusal-with-a-release)).
- **O3**, the source's only record of the relationship is ENDED: set `recreate: true` on the entry, and pass the release
  the refusal prints, which names O3.
- **O4**, an empty source over a target directory that holds files: pass the release that names O4.
- **O5**, an ENDED record at the target path over a directory that holds files: pass the release that names O5.
  It needs no `recreate`.
- **O6**, the target holds a live record of the relationship that the source does not: remove the relationship with
  `state: absent`, then declare it again ([Remove a relationship](#remove-a-relationship)).
- **O7**, a live relationship holds a path that contains the target path or lies inside it: remove that relationship,
  not this one's paths.
- **O8 and O9**, a relationship that replicates the other way over these paths: nothing lifts them, since a failover
  has turned the relationship around. Run the failback of
  [Fail over to a replica, and fail back](fail-over-replication.md) to turn it back.

Every unmet row is reported at once, in the module's `unmet` result, with the facts behind it.
The target also warns when the source looks larger than the space it can reuse.
`strict_capacity: true` on the entry refuses instead of warning.

A relationship from a source that was formed again is held, not refused, because its replica may be the only copy of the
source's data.
[Restore a rebuilt edge's data from its replica](restore-replica.md) has that case.

## Variants

### Replace the content of a target directory

Use this for a target directory that already holds entries, such as a copy kept by hand:

```yaml
# inventory/host_vars/edge01.yml
qumulo_replication_relationships:
  - source_path: /projects
    target: dr01
    target_path: /replicas/edge01-projects
    allow_non_empty_target: true
```

1. Read what the target directory holds, and keep what you need.
   The first job deletes whatever the source lacks.
2. Run the preview from [Preview the relationship](#preview-the-relationship) and read the diff.
3. Apply.

Removing the relationship later does not restore the data it replaced.

### Pause replication on a schedule

Blackout windows and the other settings sit on the same entry:

```yaml
qumulo_replication_relationships:
  - source_path: /projects
    target: dr01
    target_path: /replicas/edge01-projects
    blackout_windows:
      - {start_hour: 8, start_minute: 0, end_hour: 18, end_minute: 0, on_days: [MON, TUE, WED, THU, FRI]}
    blackout_window_timezone: America/Los_Angeles
```

`blackout_windows` need `blackout_window_timezone`.
A timezone cannot be cleared once it is set (G-REPL-TZ, [Gaps](../reference/gaps.md); QUIRK-019,
[Quirks](../reference/quirks.md)), so an empty timezone where one is set, and windows without a timezone, fail before
any write.
`replication_enabled`, `map_local_ids_to_nfs_ids` and `source_root_read_only` are settings of the entry too.
An unset setting keeps the cluster's value.
Changing one is a PATCH of the existing relationship.

### Move the target to another address

To reach the target at another address, such as a floating IP, change `qumulo_replication_address` on the target host
and run the baseline on the source host.
The relationship is changed in place, with one PATCH, and so is one made by hand at another address of the same target
(the address is a setting, not part of the key: [Keys, not ids](../explanation/state-management.md#keys-not-ids)).

### Lift a refusal with a release

A release lifts the rows it names, for one record and one run.
The refusal prints each release that would lift it, as `<target host>:<target path>@<record id>:<rows>`.
Read the holder and the rows first, then pass the entry with `-e`, never in the inventory:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 \
  -e '{"qumulo_replication_release": ["dr01:/replicas/edge01-projects@<record id>:O2,O4"]}'
```

- Run it on the source host, as above.
  An entry for a target host that the run reaches must name one of the run's relationships, or it fails the run.
- An entry that is malformed, or lifts nothing in the run, fails the run.
  An entry for a host that the run does not reach is left alone, so `--limit` still works.

[Respond to a refusal](respond-to-a-refusal.md#a-held-record) has the steps.

### Replicate into an edge that has no license yet

The baseline neither creates nor authorizes a relationship into an edge until that edge's license and clamp apply.
This holds for a target in `qumulo_edges` that declares `qumulo_capacity`.
The run does not fail: it prints a hand-off that names `ansible-playbook qumulo.cluster.edge_license_complete`.
Put the license where [License an edge](licensing.md) says, then run the edge's second pass:

```sh
ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l <target edge>
```

The second pass applies the license and the clamp, then creates and authorizes the relationships that name the edge.
An edge that declares `qumulo_capacity.license: none` passes the license check without one.
The clamp check applies to every edge.

### Drive the two modules yourself

Use this to replicate from a play of your own.
The relationship is two modules, one on each cluster:
[`replication_relationship`](../../plugins/modules/replication_relationship.py) creates it on the source, and
[`replication_target`](../../plugins/modules/replication_target.py) authorizes it on the target.
The file has three plays.
Each runs on one cluster's host, which gives the modules that cluster's connection through `module_defaults`, as in
[Verify](#verify), so a host with a token or without a CA file works as it does in the baseline:

```yaml
# replicate-projects.yml
- name: Check the target, then make its directory
  hosts: dr01
  gather_facts: false
  module_defaults: &connection
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
    - name: Stop here if the target would refuse the authorize
      qumulo.cluster.replication_target:
        source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
        source_path: /projects
        target_path: /replicas/edge01-projects
        source_creates: true
        directory_pending: true
      check_mode: true
      diff: true

    - name: Make the target directory, path only
      qumulo.cluster.directory:
        path: /replicas/edge01-projects

- name: Create the relationship on the source
  hosts: edge01
  gather_facts: false
  module_defaults: *connection
  tasks:
    - name: Create the relationship
      qumulo.cluster.replication_relationship:
        source_path: /projects
        target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
        target_path: /replicas/edge01-projects
        target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
      register: created

- name: Authorize it on the target, and wait until it is ESTABLISHED
  hosts: dr01
  gather_facts: false
  module_defaults: *connection
  tasks:
    - name: Authorize the relationship
      qumulo.cluster.replication_target:
        source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
        source_path: /projects
        target_path: /replicas/edge01-projects
        relationship_id: "{{ hostvars['edge01'].created.relationship.id }}"
        source_empty: "{{ hostvars['edge01'].created.source_empty }}"
        source_files: "{{ hostvars['edge01'].created.source_files }}"
        source_bytes: "{{ hostvars['edge01'].created.source_bytes }}"
```

```sh
ansible-playbook replicate-projects.yml -i inventory
```

- The first task is a preview of the authorize before the source has created the relationship: `source_creates: true`
  stands in for `relationship_id`, and `directory_pending: true` counts the target directory that the next task, a
  [`directory`](../../plugins/modules/directory.py) task, makes.
  In check mode the module fails on the same unmet rows as the real run, so the play stops before any write.
  Add `check_mode: true` and `diff: true` to any other task to preview it.
- Pass `relationship.id` and the three `source_*` values from the create to the authorize.
  The target uses them for its checks.
- The preview covers the target's rows only.
  The source's own rows (O3 and O8) are checked when the create runs, after the target directory exists.
  The role checks both sides before it writes either, which two modules in sequence do not.
  Prefer the baseline unless you need the modules apart.
- The source learns of the authorize on its next poll.
  Poll `replication_info` with `gather_subset: [source]` on `edge01` until its record leaves `AWAITING_AUTHORIZATION`.
- A create and an authorize are one-shot on the cluster: a repeated create makes a second relationship, and a repeated
  authorize fails (G-REPL-ONESHOT).
  The modules match by key before a create, and reconcile a lost answer by listing, never by sending again.
  A result with `maybe_applied: true` means a write was sent and not confirmed; the next run reads the cluster and
  decides ([why](../explanation/state-management.md#maybe-applied)).

`replication_relationship` and `replication_target` document every option and result.

## Remove a relationship

Remove it on the source first, then on the target.
The source tells the target of a delete only once, so the order matters (G-REPL-NOTIFY, see
[Gaps](../reference/gaps.md)).
The baseline does both in that order.

1. Set the entry's `state` to `absent`, and leave the entry in place:

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_replication_relationships:
     - source_path: /projects
       target: dr01
       target_path: /replicas/edge01-projects
       state: absent
   ```

2. Preview and apply as above:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ```

3. Read both clusters with the play from [Verify](#verify).
   Neither lists the relationship.

- Deleting the entry from the inventory removes nothing: the baseline acts only on the entries it finds.
- The target directory keeps its data.
  It becomes writable once no record holds it.
- The removal leaves the target's last recovery-point snapshot.
  Nothing owns it and nothing expires it, so delete it yourself when you no longer want the copy
  (G-REPL-SNAPSHOT).
  The modules name the snapshots in their `snapshots` result, and
  [`snapshot_info`](../../plugins/modules/snapshot_info.py) lists them.
- Setting the entry back to `present` later, with `allow_non_empty_target: true`, replaces the data the target kept.
- For a target gone for good, add `target_gone: true`.
  Only the source's records are deleted, and no connection to the target is made.
  Once the target host has left the inventory, add `target_cluster_uuid` with its UUID, or
  `previous_target_cluster_uuids`, a list of its earlier UUIDs.
- For a source gone for good, run `replication_target` with `state: absent` against the target cluster.
  Add this task to a play on `dr01` with the `module_defaults` header from [Verify](#verify):

  ```yaml
  - name: Remove a relationship whose source cluster is gone for good
    qumulo.cluster.replication_target:
      source_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
      source_path: /projects
      target_path: /replicas/old-edge-projects
      state: absent
  ```

## Next

- [Restore a rebuilt edge's data from its replica](restore-replica.md): bring a replica back into a source edge that
  was formed again.
- [Fail over to a replica, and fail back](fail-over-replication.md): make the target writable when the source is lost,
  and return to the source.
- [Respond to a refusal](respond-to-a-refusal.md): the release and consent flags in full.
- [Pair an edge with its hub through a portal](pair-edge-with-hub.md): a target path never lies inside a portal root on
  the target.
- [Inventory variables](../reference/variables.md#replication): every `qumulo_replication_*` variable.
- [Replication](../explanation/replication.md): the overwrite table, releases and why authorizing needs consent.
