<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Restore a rebuilt edge's data from its replica

This page brings an edge's data back after the edge was formed again, from the replica that its old UUID left on a
target cluster.
When you finish, the edge's source directory holds the replica's content, the relationship runs from the edge's new UUID
to the same target with its declared settings, and the old record on the target is gone.
The variants restore only some relationships, resume a restore that stopped, and cover the cases where the restore
cannot run: copying the replica back by hand, and keeping it as plain data.
For why the collection holds such a relationship instead of creating it again, see
[Replication](../explanation/replication.md) and [Rebuilds](../explanation/rebuilds.md).

Two names appear in the run's messages.
X is the old record on the target that holds the replica, and R is the restore relationship, from the target's replica
directory to the edge's source path.
[The restore's records and steps](../reference/playbooks-and-roles.md#replication-restore) lists both and the nine steps
that the messages number.

## Before you start

- Rebuild the edge ([Rebuild an unprotected edge](edge-rebuild.md)) and apply the `host_vars_patch` from its report to
  `host_vars/edge01.yml`.
  The restore finds the replica through `qumulo_previous_cluster_uuids`, which the patch sets next to the new
  `qumulo_cluster_uuid`.
  An edge formed again some other way needs its old UUID added to that list by hand.
- Declare the relationship in the edge's `qumulo_replication_relationships` with its `target` host and `target_path`, as
  [Replicate a directory to another cluster](replicate-a-directory.md) shows.
  The restore takes every relationship out of the edge whose `state` is not `absent`.
- Give the edge and the target a `qumulo_replication_address` that is an IP address, not a name.
  The target reaches the edge at its address for the restore, and the reverse sends the edge the target's.
- Set `map_local_ids_to_nfs_ids: true` on the declared relationship, and give each local user and group that owns files,
  other than admin and Users, a `uid` or `gid` in the inventory.
  Otherwise every job on such a file fails, or the file comes back owned by an NFS id that no local user has
  (G-REPL-IDMAP in [Gaps](../reference/gaps.md)).
- Have the edge's license and clamp applied, or `qumulo_capacity.license: none` declared.
  The second pass applies them before it restores; [License an edge](licensing.md) describes it.
- Run from the controller that holds the rebuild's records.
  They are under `artifacts/edge01/` beside the inventory source, or under `edge_rebuild_artifact_dir`, and the
  restore writes its own files into `artifacts/edge01/restore/`.
  Keep that directory between runs.
- Leave no file in the edge's source directory.
  Subdirectories are fine.
  The restore fills the directory and will not replace files.

## Restore in the second pass

The second pass, `qumulo.cluster.edge_license_complete`, applies the license and the clamp, creates the relationships
into the edge, and then restores the edge's relationships in a second play tagged `restore`.
It runs one edge at a time.
The license document is vault-encrypted, so the commands below ask for the vault password.
Drop `--ask-vault-pass` when the inventory holds no encrypted values.

1. Preview the run:

   ```sh
   ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l edge01 --ask-vault-pass --check
   ```

   A check run takes no lock and changes nothing.
   It reports the steps each relationship would run.
   A refusal appears now, and [When the restore refuses or stops](#when-the-restore-refuses-or-stops) lists the causes.

2. Run it:

   ```sh
   ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l edge01 --ask-vault-pass
   ```

   For each relationship whose target still holds the replica under its old record, the restore syncs the replica
   back into the edge's source directory, turns the relationship around, deletes the old record and reconnects.
   The full sync of a large replica takes hours, and the run waits for it.
   R has no blackout window until the restore's last step applies the declared settings, so the full sync runs through
   any declared window.
   If the license has not applied yet, the run fails for that edge with the license hand-off and no restore.
   Nothing is lost meanwhile, since the target keeps the replica.
   Run it again once the license applies.

3. Read the report at the end of the run.
   Each restored relationship prints a line such as:

   ```text
   edge01:/projects to dr01:/replicas/edge01-projects: restored from dr01, and the relationship runs from <the edge's UUID>.
   ```

   The summary may add a command: run `ansible-playbook qumulo.cluster.baseline -i inventory -l edge01`.
   The first full sync replaces the declared subdirectories of the source path with the replica's content, or deletes
   them where the replica lacks them, and the baseline puts back what the inventory declares.
   The report also names any replication snapshots that the restore left on the target.
   They belong to no one and never expire, so delete them once you no longer want the copy
   (G-REPL-SNAPSHOT in [Gaps](../reference/gaps.md)).

## Restore with the restore playbook

Use `qumulo.cluster.replication_restore` for an edge that is not in `qumulo_edges`, to finish a restore the second pass
stopped, or to restore one edge without running the license play again.
It runs on one host: name it with `-l`, or the run stops before it sends anything.
Its hosts are `qumulo_clusters`, or the group that `replication_restore_hosts` names.

```sh
ansible-playbook qumulo.cluster.replication_restore -i inventory -l edge01 --check
ansible-playbook qumulo.cluster.replication_restore -i inventory -l edge01
```

The playbook refuses the whole run before any relationship when the edge has no `qumulo_previous_cluster_uuids`, when
the run carries `qumulo_replication_release`, or when `qumulo_replication_restore` names a relationship the edge does not
declare.
It applies no license and no clamp.
When an edge in `qumulo_edges` expects a license that has not applied, or its clamp differs from the declared one, the
restore refuses and names the second pass.

`--tags restore` on the second pass is not a way to restore alone.
It skips the license play, and the run fails with "no restore" for each rebuilt edge.
Use this playbook for that.

## Variants

### Restore some relationships

Pass `qumulo_replication_restore` with `-e` for one run.
Each entry is `<target host>:<target path>` of a relationship the edge declares, compared without a trailing slash:

```sh
ansible-playbook qumulo.cluster.replication_restore -i inventory -l edge01 \
  -e '{"qumulo_replication_restore": ["dr01:/replicas/edge01-projects"]}'
```

Without it the run picks every relationship it can.
The second pass takes the same variable.
A named relationship that has nothing to restore is refused.
Don't pass this variable to the rebuild's replay.
The replay refuses `qumulo_replication_restore` and `qumulo_replication_release` at its first step, since both belong
to later runs.

### Restore relationships whose source paths nest

Only one replica can fill a directory.
When the replicas of two relationships exist and one source path holds the other, the restore playbook refuses the run
and names the outer relationship.
Restore the outer one first by naming it, as above.
The inner relationship then takes the copy-back route with a release, which replaces its replica from the restored
directory ([Copy the replica back](#copy-the-replica-back)).
The second pass restores neither: it refuses both and prints the restore playbook command that names the outer one.

Two replicas of one source path, on two targets or at two target paths, are a variant of this.
The restore takes the one with the newer recovery point, and the older one is not restored.
Release the older one the same way.

### Restore several edges

After every edge's license play, the second pass restores one edge at a time and waits for each edge's full syncs before
the next, so that the target, usually the same for every edge, carries one edge's syncs at a time.
A run over many rebuilt edges takes as long as all their restores in sequence.
To restore edges in parallel, run one second pass per edge with `-l`.
[Run many edges](pipeline.md#run-many-edges) says what a failure does to the other edges.

### Skip the restore in a second pass

A run that must not restore, such as a license renewal, adds `--skip-tags restore`
([Skip the restore](licensing.md#skip-the-restore)).

### Run the restore from a pipeline

A restore outlasts most job limits, so plan it as a run that stops and resumes.

- Set `replication_restore_wait_timeout` below the job's time limit.
  The wait, 86400 seconds by default, covers the full syncs of all the picked relationships together.
  Leave room after it for each relationship's steps 3, 5 and 8: by default up to 600, 300 and 1800 seconds.
  A restore that reaches its timeout stops, and the next run resumes it.
  A job killed at its limit leaves the lock instead.
- Keep `edge_rebuild_artifact_dir` across every job that runs the replay, the baseline or the second pass.
  It holds the rebuild report, the restore record, the restore stamps and the lock.
  [Keep the records between jobs](pipeline.md#keep-the-records-between-jobs) says what goes wrong without the directory.
- Run the restore again in the next job, with the same command.

[Run the playbooks from a pipeline](pipeline.md#keep-the-records-between-jobs) covers the rest of the pipeline.

### Tune the other waits

`replication_pair_wait_timeout` (step 3's authorize), `replication_restore_poll_delay`,
`replication_restore_reverse_timeout` and `replication_restore_reconnect_timeout` bound the restore's other waits, and `replication_restore_resume_window` how long a restore stopped before its
reconnect may still resume from its record.
Pass them with `-e` or set them in the inventory.
The `replication_pair` role's `restore` entry point documents each:

```sh
ansible-doc -t role qumulo.cluster.replication_pair -e restore
```

## Resume a restore that stopped

Run the same command again.
Before each step the restore reads both clusters afresh and runs only the step the records show is next, so a rerun
continues where the restore stopped.
A full sync that did not land within `replication_restore_wait_timeout` resumes at the wait.
A reconnect that did not read ESTABLISHED on both clusters within `replication_restore_reconnect_timeout` leaves the
restore record marked, and the next run only polls.
The message names the likeliest cause: the edge cannot reach the target at the address and port that R's source record
on the edge uses.
Fix that path first.

### A lock another run holds

A restore holds a lock in `artifacts/edge01/restore/lock` while it runs.
A run killed in the middle leaves it.

- On the same controller, the next run takes the lock over once the killed run's process is gone, and warns.
- On another controller, a run takes it over only after its lease ends.
  The lease covers the restore's waits, `replication_restore_wait_timeout` among them.
- A lock whose process is still alive on its own controller is never taken over, whatever its lease says.

The lock belongs to one controller.
Two controllers can both restore the same edge, so keep to one restore per edge at a time.
Remove a lock file by hand only after the run that took it is gone.
[Find and clear restore records and locks](#find-and-clear-restore-records-and-locks) shows how to list the locks and
tell which ones are left.

### Resume from another controller

The restore record, `artifacts/edge01/restore/<digest>.yml`, is how a stopped restore resumes.
Move it into `artifacts/edge01/restore/` (or `edge_rebuild_artifact_dir`) on the other controller, keeping mode 0600,
and delete it on the first.
Never copy it: two controllers holding it could both act on the relationship.
Its name doesn't depend on the target's inventory host name, so the record still matches after the target host is
renamed.

## Find and clear restore records and locks

An upgrade warns of a restore record or a live lock that names a host it moves (W1), and a restore stops on a file it
cannot read.
To see every restore record, lock and unreadable file on the controller before either, list them with
[replication_restore_info](../../plugins/modules/replication_restore_info.py).
It reads the files and nothing else: it sends no request to a cluster and changes nothing.

1. List the files under the artifact directory, `artifacts` beside the inventory by default or your
   `edge_rebuild_artifact_dir`:

   ```sh
   ansible localhost -m qumulo.cluster.replication_restore_info -a path=artifacts
   ```

   Run it from the inventory's directory, or give the full path.

   The result has three lists, `records`, `locks` and `unreadable`, each by host.
   A directory that does not exist holds nothing, so all three are empty.
   The module's examples show the same call in a play, with a task that prints each record's age.

2. Read each record.
   `host` is the edge being restored, and `key` is the relationship: its `source_path` on the edge, and the
   `target_host`, `target_cluster_uuid` and `target_path` of the replica.
   `age_seconds` runs from the time the restore wrote the record, or from the file's last change when it did not say.
   `hosts` lists the hosts a restore of the record acts on, the edge and the key's `target_host`.
   `reconnected: true` means the restore had reached its last step, the reconnect.
   Finish the restore with the restore playbook, which removes the record once the relationship reads ESTABLISHED:

   ```sh
   ansible-playbook qumulo.cluster.replication_restore -i inventory -l edge01
   ```

   The playbook reads both clusters, and either continues from the step the record shows or says why it cannot.
   Remove a record by hand only once you have reconnected the relationship by hand, as
   [A stop or a failure](#a-stop-or-a-failure) describes.

3. Read each lock.
   `holder` is `restore` for a restore and `upgrade` for an upgrade run, and `controller` and `pid` name the
   `ansible-playbook` process that took it.
   `liveness` is `live`, `gone` or `expired`:

   - `live`: for a lock taken on this controller, its process is alive, so a run holds the edge now, as
     [A lock another run holds](#a-lock-another-run-holds) describes; leave it alone, and let that run end.
     For a lock taken on another controller, in another container, or before this controller's last boot, `live`
     means only that its lease has not run out, so check whether that run is still going.
   - `gone`: the run was on this controller and its process is no longer there.
     The next restore or upgrade of the host takes the lock over and warns.
     Removing it by hand is safe, since `gone` says its run has ended, but the next run does it for you.
   - `expired`: the lock was taken on another controller, in another container or before this controller's last
     boot, and its lease has run out.
     The next restore or upgrade of the host takes it over the same way.
     Check that the run on that controller has ended before you remove the file by hand.

4. Read each unreadable file.
   `unreadable` lists a file named like a record that is not a restore record this collection reads, and a `lock` that
   is not a lock of format 1, with the reason.
   A restore stops on the file when it reads it, naming it: a `lock` always, and a record when its name is that of a
   relationship the restore handles. An upgrade that locks the host also stops on the `lock`.
   Move such a file out of the `restore/` directory only once no restore or upgrade of the host is under way on any
   controller.
   A record moved aside cannot resume its restore's reconnect, so reconnect that relationship by hand, as
   [A stop or a failure](#a-stop-or-a-failure) describes.

5. Check where each file is.
   The restore reads only its host's own `edge_rebuild_artifact_dir`, `artifacts/edge01/restore/` by default, so it
   does not find a record under another directory.
   That happens with two inventory sources, which give two artifact directories, or after a host is renamed.
   Move the record into the host's own `restore/` directory, keeping mode 0600, and delete it from the old place, or
   remove it if you reconnected the relationship by hand.
   [Resume from another controller](#resume-from-another-controller) gives the same move.
   Never copy it.

## Verify

Read the relationship from both clusters with [replication_info](../../plugins/modules/replication_info.py), using the
play in [Replicate a directory to another cluster](replicate-a-directory.md#verify) against `edge01` and `dr01`.
Then check:

- the edge's `source_relationships` list the relationship for `/projects` with `state: ESTABLISHED`,
  `target_path: /replicas/edge01-projects`, the target's `qumulo_replication_address` as `target_address`, and
  `error_from_last_job: null`;
- `dr01`'s `target_relationships` list a relationship with the same `id` and `state: ESTABLISHED`, whose
  `source_cluster_uuid` is the edge's new UUID;
- `dr01`'s `target_relationships` list no relationship at that target path from a previous UUID of the edge;
- `artifacts/edge01/restore/` holds no `<digest>.yml` record and no `lock`, and holds the restore stamp,
  `done-<digest>.yml`.

Then run `ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff`.
It reports no change when the inventory and both clusters agree.

## When the restore refuses or stops

Each message names its way on.
[Troubleshooting](../reference/troubleshooting.md#rebuilds) quotes the messages.

### A refusal before the first step

A refusal comes before any write.
Where the way on is to copy the replica back, [Copy the replica back](#copy-the-replica-back) shows how.
Otherwise fix the cause and run again.

| Cause | What to do |
|---|---|
| The old record X is not ESTABLISHED, or its directory is not read-only, as after a failover by hand. | Copy the replica back. The reconnect reverts the directory to R's last recovery point, so writes made there since would be lost. |
| An ENDED record from another source lies beside X on the target. | Copy the replica back. The directory may hold writes made since that record was released. |
| X has no recovery point. | Copy the replica back. The replica is incomplete. |
| The target's replica directory lies inside or holds a portal spoke root on the target. | Copy the replica back. The spoke root holds cached hub data, which the restore cannot copy as the replica. |
| The edge's source path lies inside or holds a portal spoke root on the edge. | Move the relationship's source path off the portal in the inventory, or copy the replica back around the portal's root. |
| The edge's source directory holds a file. | Move the files out and run again, then move them back once the relationship runs from the edge. Or copy the replica in around them and release. |
| The edge or the target has no `qumulo_replication_address`, or it is a name. | Set it to an IP address. |
| `edge_rebuild_artifact_dir` is empty, or its directory cannot be written. | Set it to a directory on the controller, and make it writable. |
| The edge expects a license and holds none. | Apply the license through the second pass. An edge that runs without one declares `license: none` in `qumulo_capacity`, keeping its other keys. |
| The edge's clamp differs from `qumulo_capacity.clamp`. | Run the second pass, which sets the clamp and then restores. |
| The clamp plan fails. | Fix the cause the message gives, and run again. |
| Another relationship out of the edge has the same source path or a nested one. | If the replay created it from the empty directory, delete it on both clusters, run the restore again, and the next baseline creates it again from the restored data. Otherwise copy the replica back. |
| A relationship into the edge replicates into the source path. | Move one of the two paths in the inventory, or copy the replica back. |
| The check of R's create on the target, or of its authorize on the edge, refuses. | The message quotes the check's reasons. Fix them and run again. |
| R's records from an earlier try lie at the restore's paths. | Delete them, on the target first and then on the edge, and run again. |

### A stop or a failure

The second pass can fail on an edge with `qumulo_previous_cluster_uuids`.
It can also fail on an edge that has a `present` relationship whose target path holds a live record from a UUID the
inventory doesn't name:

- For a relationship with a restore to do, it fails on:
  - the refusals above;
  - a stop at a later step, such as a full sync that did not land within `replication_restore_wait_timeout`, which a later
    run resumes;
  - a target that does not answer;
  - a pair left DISCONNECTED on both sides with no restore record;
  - a run that carries `qumulo_replication_release`, which restores nothing.
- For any relationship, with or without a restore to do, it fails as the baseline does on a held relationship with no
  replica left to restore from, and on a foreign record at a declared target path.
  On an edge with no previous UUIDs, the foreign record only warns.
- For the edge, it fails when another run holds its restore lock, or when `edge_rebuild_artifact_dir` is empty.

An edge whose relationships an earlier run restored or released passes, in a release run too.
It only warns on a target that doesn't answer or on a failover by hand.

A pair left DISCONNECTED on both sides with no restore record reads as a failover by hand.
It is also what a crash between the old record's delete and the record's write leaves, what a resume from a
controller without the record leaves, and what a delete of the old record by hand after the reverse leaves.
The message names three ways on.
The first is to move the record here from the controller that wrote it, deleting it there, and run again.
The second, once you have checked that nobody has written to the target directory (the reconnect reverts it), is to
reconnect R by id with `qumulo.cluster.replication_reverse` and `allow_data_loss`.
Then remove any restore record of it on the controller that wrote it, or run the restore there once while R is
ESTABLISHED, which removes it.
The third, when the old record was deleted by hand before step 7, is to delete R on both clusters.

### Step 3's authorize fails on an edge without a license

On an edge that declares `qumulo_capacity.license: none`, a restore is not yet verified on a real cluster.
If the edge refuses the full sync, step 3's authorize fails and the relationship's outcome is failed, with the
authorize's error.
R then waits, pending on the edge.
That is not a refusal.
Once the cause is fixed, run the second pass again, and it resumes at the authorize.

## When the restore cannot run

When the restore refuses a replica it cannot trust, or you do not want it, two routes remain.
Each uses the release that the baseline's held refusal prints, so run the baseline on the edge with `--check` first and
read it.
A release lifts only the rows it names, so a check run with a release that names O2 alone still refuses an empty source
over a full target (O4; see [The overwrite table](../explanation/replication.md#the-overwrite-table)).
[Respond to a refusal](respond-to-a-refusal.md#a-held-record) shows how to read and give one.

### Copy the replica back

1. Copy the target directory's content into the edge's source directory over NFS or SMB.
2. Add `allow_non_empty_target: true` to the relationship's entry in the edge's host vars, since the target directory
   holds the replica:

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_replication_relationships:
     - source_path: /projects
       target: dr01
       target_path: /replicas/edge01-projects
       allow_non_empty_target: true
   ```

3. Run the baseline on the edge once, with the release that the held refusal prints:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 \
     -e '{"qumulo_replication_release": ["dr01:/replicas/edge01-projects@<record id>:O2,O4"]}'
   ```

   Use the rows the refusal names where the command shows `O2,O4`.
   The target deletes the holder's record, the source creates the relationship from the directory as it is, and the
   target authorizes it.

### Keep the replica as plain data

Set the entry to `state: absent` and run the baseline with the same release:

```yaml
qumulo_replication_relationships:
  - source_path: /projects
    target: dr01
    target_path: /replicas/edge01-projects
    state: absent
```

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 \
  -e '{"qumulo_replication_release": ["dr01:/replicas/edge01-projects@<record id>:O2,O4"]}'
```

The release deletes the holder's record on the target.
The directory keeps its files and becomes writable.

### Delete a relationship

- A relationship that nothing holds is deleted on both sides by `state: absent`, and the target directory keeps its data.
- When the target is gone for good, set `state: absent` and `target_gone: true`, and add `target_cluster_uuid`, and
  `previous_target_cluster_uuids` if the target was formed again, once the target host has left the inventory.
  It deletes only the source's records and makes no connection to the target.

## See also

- [Rebuild an unprotected edge](edge-rebuild.md): the rebuild that leaves the relationship held.
- [License an edge](licensing.md): the second pass, its license play and its clamp.
- [Replicate a directory to another cluster](replicate-a-directory.md): the relationship the restore turns around.
- [Fail over to a replica, and fail back](fail-over-replication.md): the failover of a source that is lost and
  expected back, which the restore does not do.
- [Run the playbooks from a pipeline](pipeline.md): keeping the artifact directory between jobs.
- [The restore's records and steps](../reference/playbooks-and-roles.md#replication-restore): X, R and steps 1 to 9.
- [Troubleshooting](../reference/troubleshooting.md#rebuilds): the messages of a rebuild.
- [Replication](../explanation/replication.md#why-x-is-kept-and-r-turned-around): why a restore keeps X and turns
  R around.
- [Rebuilds](../explanation/rebuilds.md#what-a-rebuild-does-to-replication): what a rebuild does to replication.
