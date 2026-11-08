<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Fail over to a replica, and fail back

This page makes a replica writable when its source cluster is lost, brings the source back as the replica's copy, and
returns the data to the source.
When you finish, clients write to the source again and the target replicates it, as the inventory declares.
The variants cover a source that is still running a job, writes the role finds and refuses to discard, discarding the
failover's writes on purpose, and a target lost while the relationship runs the other way.
For why the role works one step at a time, and why it asks for two kinds of consent, see
[Replication](../explanation/replication.md#failing-over).

The page uses `edge01` for the source, `dr01` for the target, and one relationship from `edge01:/projects` to
`dr01:/replicas/edge01-projects`.
The role is [replication_failover](../../roles/replication_failover/), and
[`replication_failover.yml`](../../playbooks/replication_failover.yml) runs it for one source host.
A run does one of three actions, named by `replication_failover_action`:

| Action | What it does |
|---|---|
| `failover` | Makes the target's copy writable, from its last recovery point. It sends nothing to the source, so it works with the source gone. |
| `resync` | Turns the relationship around, so the target replicates the failover's writes to the source while clients still use the target. |
| `failback` | Makes the source writable again, turns the relationship back and reconnects the target, so the source is the writable copy and the target its replica. It runs the resync's steps first when the relationship has not been resynced. |

Moving clients between the clusters is yours: the role changes no DNS name, mount, share or export.

## Before a disaster

Do this while both clusters work.

- Declare the relationship on the source host, as [Replicate a directory to another
  cluster](replicate-a-directory.md) shows, and let the baseline make it.
  The role acts only on a `present` entry of the source host's `qumulo_replication_relationships`.
- Give both hosts a `qumulo_cluster_uuid`.
  The role finds the relationship's records by the two UUIDs and the two paths.
- Give both hosts a `qumulo_replication_address` that is an IP address, not a name, for example `203.0.113.10` for
  `edge01` and `203.0.113.40` for `dr01`.
  A failover alone needs neither.
  A resync needs the source's address, and its reverse is sent there.
  A failback needs the target's address, and the source's too when it runs the resync's steps first.
  A run that needs an address it lacks stops before it writes anything.
- Check that the tenant serving each replication address has `replication_enabled`.
  The role applies the check the baseline applies at step 21 to each cluster a reverse makes the target, before its
  first write ([The replication address](../reference/troubleshooting.md#the-replication-address)).
- Keep `edge01`'s connection variables in the inventory even while it is down.
  The play reads `edge01`'s connection variables, and a failover sends `edge01` only reads and logins.
- Use an account on both clusters that can make a replication target writable, reverse and reconnect a relationship,
  create and delete a snapshot of the target directory and read the differences between snapshots, since the
  reconnect compares the directory through a snapshot.
  A resync or a failback also reads each new target's tenants, once in the run and not once for each relationship, which needs
  `PRIVILEGE_TENANT_READ`, and the network document of a target with two or more tenants, which needs
  `PRIVILEGE_NETWORK_READ`.
- Finish a restore before a resync or a failback.
  When you rebuilt `edge01` and its restore has not ended, its record is in
  `<replication_failover_artifact_dir>/edge01/restore/`, a directory that defaults to the `edge_rebuild` role's
  `edge_rebuild_artifact_dir`.
  A resync or a failback refuses a relationship that has one, naming `playbooks/replication_restore.yml`, which
  finishes it ([Restore a rebuilt edge's data from its replica](restore-replica.md)).
  When you reconnected the relationship by hand instead,
  [remove the record](restore-replica.md#find-and-clear-restore-records-and-locks).
  A failover notes the record and goes on
  ([why](../explanation/replication.md#a-restore-under-way)).
- Practise with a check run, which is the dry run:

  ```sh
  ansible-playbook qumulo.cluster.replication_failover -i inventory -l edge01 --check \
    -e replication_failover_action=failover \
    -e '{"replication_failover_relationships": ["dr01:/replicas/edge01-projects"]}'
  ```

  Each name is `<target host>:<target path>`, compared without a trailing slash.
  The run reads both clusters, prints each relationship's state and every step left, and writes nothing.
  It needs no consent and prints the refusals a real run would make.
  The play runs one source host at a time and stops when `-l` leaves more than one.
  A check run of `failover` on a healthy relationship prints these lines, which the play shows as a list:

  ```text
  dr01:/replicas/edge01-projects: replicating, recovery point 2026-11-08T06:26:34.190479565Z
  dr01: make /replicas/edge01-projects writable. It goes back to the recovery point 2026-11-08T06:26:34.190479565Z; writes on edge01 after it stay behind on edge01
  ```

  Run it again for `resync` and `failback` to read their steps.
  A check run cannot compare a directory with its recovery point, so the lines of a reconnect say that the real run
  compares and stops when anything was written there since.
  The line of a reconnect to a host named in `replication_failover_allow_newer_writes` says instead that it does not
  compare first.

## Fail over with the source down

1. Run the check run above.
   When `edge01` does not answer, the run waits for it once, up to `replication_failover_source_timeout` seconds
   (default 30), and then plans from `dr01`'s record alone.
   The plan adds a line such as `edge01 did not answer (...); the plan goes on from dr01's record alone`.
   Read the recovery point it names: the copy on `dr01` starts from it, and whatever clients wrote on `edge01` after it
   stays behind there.

2. Run it for real, with the consent:

   ```sh
   ansible-playbook qumulo.cluster.replication_failover -i inventory -l edge01 \
     -e replication_failover_action=failover \
     -e '{"replication_failover_relationships": ["dr01:/replicas/edge01-projects"]}' \
     -e replication_failover_allow_data_loss=true
   ```

   Without `replication_failover_allow_data_loss=true`, a real run prints the plan and fails before it writes anything.
   Pass the consent with `-e`: the role ignores a value in the inventory or a play's `vars`, so the next run, which may
   hold a relationship you never read, has to ask again.
   The run makes `dr01`'s copy writable and waits until its record reads DISCONNECTED, then prints this line:

   ```text
   dr01:/replicas/edge01-projects: replicating to failed over, steps make_writable_target. edge01 did not answer (...); the plan goes on from dr01's record alone
   ```

   When `dr01`'s record shows a job that was running when `edge01` stopped, the line also says that the failover
   discards its partial changes.
   The role does not refuse for that job when the source does not answer
   ([why](../explanation/replication.md#why-a-failover-goes-on-without-the-source), QUIRK-125).

3. Move clients to `dr01:/replicas/edge01-projects`.
   Create the share or export there, point DNS at it, or remount, as your site does.

4. Run the same command again.
   It reads the relationship as failed over, sends nothing, needs no consent and reports it unchanged.

A source that answers with a job running is a different case: see
[If the source is running a job](#if-the-source-is-running-a-job).

## Resync once the source is back

Run this when `edge01` is up again and reachable.
Clients stay on `dr01`.

1. Preview the run with `replication_failover_action=resync` and `--check`.
   The plan lists the steps left:
   a wait until `edge01`'s record reads DISCONNECTED, if it does not yet;
   the reverse on `dr01`, toward `edge01`'s replication address, which moves records and changes no data;
   when the relationship declares `map_local_ids_to_nfs_ids`, turning it on again in `dr01`'s new source record, since
   the reverse resets it;
   and the reconnect on `edge01`, which sends `/projects` back to the recovery point and makes it read-only.
   Stop the writes on `edge01` first: the reconnect compares `/projects` through a snapshot taken a few requests before
   it, and a write made between the two is discarded without being compared
   ([why](../explanation/replication.md#why-a-reconnect-compares-and-why-the-consent-names-a-host)).

2. Run it with the consent:

   ```sh
   ansible-playbook qumulo.cluster.replication_failover -i inventory -l edge01 \
     -e replication_failover_action=resync \
     -e '{"replication_failover_relationships": ["dr01:/replicas/edge01-projects"]}' \
     -e replication_failover_allow_data_loss=true
   ```

   The consent accepts that `edge01`'s directory becomes read-only under its clients.
   The run ends when both records read ESTABLISHED, within `replication_failover_wait_timeout` seconds for each wait
   (default 1800).
   It does not wait for the first job: `dr01` then replicates the writes made during the failover to `edge01`, which
   takes as long as the data takes.
   The failback waits for that copy.

3. When the reconnect refuses, because it found writes on `edge01`, see
   [If the reconnect finds writes](#if-the-reconnect-finds-writes).

You can stay in this state as long as you need.
`dr01` serves clients, `edge01` holds a read-only copy of what they wrote, and a second run changes nothing.
While the relationship runs this way, [the baseline refuses it](#the-baseline-refuses-the-relationship-while-it-runs-reversed).

### If the reconnect finds writes

A reconnect sends its directory back to the recovery point, which discards whatever was written there since.
Before it sends one, the role compares the directory with the recovery point and stops when anything changed.
A resync's reconnect is on `edge01`, so it finds what clients wrote on `edge01` after the recovery point:

```text
dr01:/replicas/edge01-projects: edge01's /projects changed since the recovery point ...: 2 changes (CREATE /projects/new.txt, MODIFY /projects/).
The reconnect sends /projects back to the recovery point ..., discarding whatever was written there after it.
To keep them, copy them to dr01 over a protocol first; to discard them, run again with -e '{"replication_failover_allow_newer_writes": ["edge01"]}'.
```

Nothing was changed by the refusal.
The relationship is turned around and both records read DISCONNECTED, and the next run resumes at the reconnect.

- To keep the writes, copy them from `edge01:/projects` to `dr01:/replicas/edge01-projects` over SMB or NFS, then run
  again with `edge01` named as below.
  The comparison still finds the files on `edge01`, and naming the host lets the reconnect discard the copies there.
- To discard them, run the resync again with the host named:

  ```sh
  ansible-playbook qumulo.cluster.replication_failover -i inventory -l edge01 \
    -e replication_failover_action=resync \
    -e '{"replication_failover_relationships": ["dr01:/replicas/edge01-projects"]}' \
    -e replication_failover_allow_data_loss=true \
    -e '{"replication_failover_allow_newer_writes": ["edge01"]}'
  ```

`replication_failover_allow_newer_writes` names the inventory hosts whose directory a reconnect may discard writes
from.
It lifts the refusal only for a reconnect sent to a named host, and, like the other consents, it belongs on the command
line of the one run: the role ignores it in the inventory.
`-e replication_failover_allow_newer_writes=edge01` works too, and a list of names is split at commas, as in
`-e replication_failover_allow_newer_writes=edge01,edge02`.
A name matches a whole host name and never part of another.

## Fail back

Run this after the resync, or let the failback copy the writes first.
It needs the writes on `dr01` to stop, which the role cannot do.

1. Stop the writes on `dr01`: take clients off `dr01:/replicas/edge01-projects`, or make the share or export
   read-only.
   A write made after the wait below is found by the failback's reconnect and refused, and it costs a second run.
   A write made in the few requests between the reconnect's comparison and the reconnect is discarded without being
   compared ([why](../explanation/replication.md#why-a-reconnect-compares-and-why-the-consent-names-a-host)).

2. Preview the run with `replication_failover_action=failback` and `--check`.
   A relationship that is still failed over lists the resync's steps first, then the failback's: a wait for a current
   recovery point, the make-writable on `edge01`, the reverse on `edge01` toward `dr01`'s replication address, the
   reconnect on `dr01`, and applying the declared settings where `edge01`'s record differs.

3. Run it with the consent:

   ```sh
   ansible-playbook qumulo.cluster.replication_failover -i inventory -l edge01 \
     -e replication_failover_action=failback \
     -e '{"replication_failover_relationships": ["dr01:/replicas/edge01-projects"]}' \
     -e replication_failover_allow_data_loss=true
   ```

   The consent accepts that `dr01`'s directory becomes read-only under its clients, and that `edge01`'s copy starts
   from the last recovery point.

   The run first waits for `dr01`'s recovery point to move twice
   ([why](../explanation/replication.md#why-the-failback-waits-for-two-moves-of-the-recovery-point)).
   On an idle relationship that takes about two minutes.
   A failback of several relationships brings each to this wait, then waits for all of them together.
   The relationships on one target host are polled together, the target hosts one after another, and the wait stops
   `replication_failover_sync_timeout` seconds after the waits begin (default 86400).
   A job that reports an error does not end it.
   A wait that runs out names each lagging relationship's job state and last error and sends nothing, and the next run waits
   again.

   Then the run makes `edge01` writable, reverses the relationship toward `dr01`, reconnects `dr01` and waits until
   both records read ESTABLISHED.
   For a relationship that was already resynced it ends with a line such as
   `dr01:/replicas/edge01-projects: resynced to replicating, steps make_writable_source, reverse_to_target, reconnect_target`.
   The last step, `apply_settings`, appears only when a reverse reset a declared setting on `edge01`.

4. Move clients back to `edge01:/projects`.

A run that stops anywhere, by a timeout or a killed job, resumes from what the records show: run the same command.
A run killed after the reverse on `edge01`, or after the make-writable on `edge01`, makes the rerun repeat part of the
resync, at the cost of a sync, and lose nothing
([why](../explanation/replication.md#two-states-that-read-the-same)).

When the reconnect on `dr01` finds writes, a client wrote on `dr01` after the wait:

```text
dr01:/replicas/edge01-projects: dr01's /replicas/edge01-projects changed since the recovery point ...: 1 changes (CREATE /replicas/edge01-projects/late.txt).
The reconnect sends /replicas/edge01-projects back to the recovery point ..., discarding whatever was written there after it.
Run the failback again, which resyncs them to edge01 first.
```

Make sure the writes on `dr01` have stopped, then run the failback again.
It reads the relationship as failed over, resyncs the late writes to `edge01` first, waits for a current recovery point
and finishes.
Don't name `dr01` in `replication_failover_allow_newer_writes`, which would make the rerun's own reconnect on `dr01`
skip its comparison
([why](../explanation/replication.md#why-a-reconnect-compares-and-why-the-consent-names-a-host)).

## If the source is running a job

A failover discards the changes of a job that `edge01` is running, since the make-writable goes back to the last
recovery point.
When `edge01` answers and its record shows a job running, the role refuses before it writes anything:

```text
edge01 is running a job of dr01:/replicas/edge01-projects, started ...; a failover now discards the changes it carries. Run again once the job ends; with writes stopped on the source, the next job carries the rest. ...
```

Wait for the job and run again.
If the job makes no progress, as when its target cannot be reached, add
`-e replication_failover_allow_partial_job=true`: the failover then goes on and notes the job it discards.
The writes that job carried stay on `edge01`, and the resync's reconnect there finds them, as
[If the reconnect finds writes](#if-the-reconnect-finds-writes) says.
A relationship whose first job has not finished has no recovery point, and the role refuses it at once: there is
nothing to fail over to.

## The baseline refuses the relationship while it runs reversed

From the resync's reverse to the failback's last reconnect, the relationship runs from `dr01` to `edge01`, and the
inventory declares it the other way.
The baseline's step 21 meets rows O8 and O9 of
[the overwrite table](../explanation/replication.md#the-overwrite-table) and refuses, naming the role's failback as the
way back.
Don't run the baseline on `edge01` or `dr01` until the failback, or expect step 21 to fail for this entry.
Don't set the entry to `state: absent` meanwhile, which has the baseline delete the relationship's records, and don't
remove the entry, which leaves the role nothing to act on.
A relationship that is only failed over, not yet resynced, is left alone with a warning.

## Discard the failover's writes instead

A disaster-recovery test, or a failover that wrote nothing worth keeping, needs no copy back.
The role does not do this.
Reconnect `dr01` in the declared direction by hand with
[replication_reverse](../../plugins/modules/replication_reverse.py), once the source is back and the relationship reads
as failed over, DISCONNECTED on both clusters.
Run it against `dr01` and give it the relationship's id, read from `dr01`.
The tasks on this page that read `cluster_baseline_connections` come after the play includes the baseline role's
`connections.yml`, as the playbook does:

```yaml
- name: Record how to connect to edge01 and dr01
  ansible.builtin.include_role:
    name: qumulo.cluster.cluster_baseline
    tasks_from: connections.yml
  vars:
    cluster_baseline_connection_hosts: [edge01, dr01]
```

The tasks:

```yaml
- name: Find the failed-over relationship on dr01
  qumulo.cluster.replication_info:
    gather_subset: [target]
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['dr01'] }}"
  register: dr01_records

- name: Reconnect dr01 to edge01, discarding what clients wrote on dr01
  # Without allow_data_loss the module compares /replicas/edge01-projects with the recovery point and fails, naming
  # the changes, when it would discard any. With it, the reconnect is sent without comparing.
  qumulo.cluster.replication_reverse:
    relationship_id: "{{ (dr01_records.replication.target_relationships | selectattr('target_path', 'equalto', '/replicas/edge01-projects') | first).id }}"
    verb: reconnect
    allow_data_loss: true
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['dr01'] }}"
```

Run the reconnect without `allow_data_loss` first to read what it would discard.
The directory on `dr01` becomes read-only and goes back to the recovery point, and the writes made during the failover
are gone.
Wait until both records read ESTABLISHED, as [Verify](replicate-a-directory.md#verify) reads them.

## If the target is lost while the relationship runs reversed

After a resync, `edge01` is the target and holds the only other copy of the failover's writes.
When `dr01` is lost then, make `edge01`'s copy writable by hand, remove its record, and declare the relationship again.

1. Make `edge01`'s directory writable from its last recovery point, with `replication_reverse` against `edge01`.
   These tasks read `cluster_baseline_connections` too, so include `connections.yml` first as above:

   ```yaml
   - name: Make edge01's copy of the reversed relationship writable
     qumulo.cluster.replication_reverse:
       relationship_id: "{{ reversed_relationship_id }}"
       verb: make_writable
       allow_data_loss: true
     module_defaults:
       group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['edge01'] }}"
   ```

   `reversed_relationship_id` is the id of `edge01`'s target record, which `replication_info` lists with
   `gather_subset: [target]`.

2. Delete that record on `edge01` with `replication_target`, since no source is left to tell it:

   ```yaml
   - name: Remove edge01's record of the relationship from the lost dr01
     qumulo.cluster.replication_target:
       source_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
       source_path: /replicas/edge01-projects
       target_path: /projects
       state: absent
     module_defaults:
       group/qumulo.cluster.qumulo: "{{ cluster_baseline_connections['edge01'] }}"
   ```

3. Declare the relationship again to the new `dr01`, as [Replicate a directory to another
   cluster](replicate-a-directory.md) shows.
   Put the new cluster's UUID in the inventory.

## What is left

Each make-writable and each reverse leaves replication snapshots that nothing owns and nothing expires, on both
clusters, and the role names none of them (G-REPL-SNAPSHOT in [Gaps](../reference/gaps.md)).
List them with [snapshot_info](../../plugins/modules/snapshot_info.py) and delete the ones you no longer want once the
failback is done.
The reconnect's comparison takes one snapshot of its own and deletes it, and sets it to expire a day later should the
run stop first.

## See also

- [Replication](../explanation/replication.md#failing-over): why the role works as it does.
- [Replicate a directory to another cluster](replicate-a-directory.md): declare the relationship the role acts on.
- [Restore a rebuilt edge's data from its replica](restore-replica.md): the way back after the source was formed again,
  not lost.
- [Playbooks and roles](../reference/playbooks-and-roles.md#replication-failover) and
  [Inventory variables](../reference/variables.md#replication-failover-variables): the role's variables and facts.
- [Troubleshooting](../reference/troubleshooting.md#failing-over): each message the role stops with.
