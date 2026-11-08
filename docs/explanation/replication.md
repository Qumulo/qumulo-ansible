<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Replication

A replication relationship copies a directory on one cluster, the source, to a directory on another, the target. The
inventory declares it on the source host, in `qumulo_replication_relationships`, and `cluster_baseline` makes it at
step 21 through the [replication_pair](../../roles/replication_pair/) role. This page explains the model behind that
role and the modules it runs: why a relationship takes two steps, what the overwrite table decides, what a release
is, why some relationships are held, why the restore of a rebuilt edge keeps one record and turns another around, and
how a failover, a resync and a failback move a relationship between its two clusters.

To declare a relationship, see [Replicate a directory to another cluster](../how-to/replicate-a-directory.md). To
bring back a rebuilt edge's data, see [Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md). To
fail over to a replica and back, see [Fail over to a replica, and fail back](../how-to/fail-over-replication.md).

## Two steps, on two clusters

A relationship has a record on each cluster, and each cluster can read only its own. The source's create registers the
relationship on the target in the same request, where it waits for authorization. The target then authorizes it, which
makes the target directory read-only and lets the source's content replace what the directory holds. That can destroy
data, so the authorize is the step the collection guards.

The collection has a module for each side, [replication_relationship](../../plugins/modules/replication_relationship.py)
on the source and [replication_target](../../plugins/modules/replication_target.py) on the target, because each runs
against one cluster, and neither can see the other's records. A third module,
[replication_pair_plan](../../plugins/modules/replication_pair_plan.py), joins what the two report. It sends no request.

The role runs the two modules in check mode first, with the options their real passes take, and joins the results. It
writes nothing on either cluster until the joined decision is to go ahead. The ordering follows from the create: it
registers the relationship on the target at once, so a refusal found after the create would leave records on both
clusters. The target directory is held to the same rule. The role creates it, path only, after the checks pass and
before the create. Replication sets a target directory's attributes and what lies in it from the source, so the role
declares nothing inside it, and a capture records only its path.

When the target is an edge that declares `qumulo_capacity`, one more check comes before the first write. A full sync
must not start into an edge whose license and clamp do not apply yet, such as the 1 TB a first pass leaves. When they
do not apply, the role defers the relationship and hands off to the edge's second pass, which creates it once the
license and clamp apply. An edge that declares `license: none` waits only for its clamp.
[Licensing and capacity](licensing-and-capacity.md) explains the two passes.

The role then makes the directory, creates the relationship, authorizes it, and waits until both sides read
ESTABLISHED. The source learns of the authorize on its next poll of the target, so the wait covers both records.

[State management](state-management.md#check-and-diff) explains why a check run reports the same refusals as a real
one.

## How a relationship is identified

The source identifies a relationship by its source path, the target cluster's UUID and the target path. The target
identifies it by the source cluster's UUID and the same two paths. Paths compare without a trailing slash and UUIDs
without regard to case. The server's relationship id is not part of the key: the server generates it, so the
inventory cannot carry it. [State management](state-management.md#keys-not-ids) explains why the collection keys on
what the inventory can state.

The address and port the source reaches the target at are settings, like the blackout windows. A relationship made by
hand at another address of the same target is adopted with one change, and a move to a floating IP is one change too.
The collection takes an IP literal and does nothing about NAT. [Which address to
choose](edges-and-portals.md#which-address-to-choose) is in Edges and portals.

Two things follow from putting the target's UUID in the key.

**A rebuilt target is a different relationship.** A target formed again has a new UUID. The source's record of the old
one stays ESTABLISHED, pinned to the old certificate, with an error from its last job, and never reaches ENDED, so
waiting for the server to end it never finishes (G-REPL-PEER). The collection finds that record, called a changed peer,
by the declared address and port, or by one of the target host's `qumulo_previous_cluster_uuids` at any address.
Replacement is create-first: the module creates the new relationship, reads back which cluster the address reached, and
only then deletes the changed peer. A replacement that fails halfway leaves the old record in place. While the role
defers a relationship into an edge, it deletes only the changed peers from a previous UUID of the target that cannot be
replicating to an old edge: those at the declared address, and those whose last job cannot establish a secure
connection. Any other changed peer is left for the later create-first replacement, since it may still replicate to an
old cluster kept beside the new one.

**The address is checked after the create.** Nothing says which cluster an address reaches until a record exists. After
every create the source module reads the new record, and if the address reached a cluster other than the declared
target, it deletes the record at once and fails, naming the address and the UUID it reached. A record already at the
declared address whose UUID belongs to another inventory host is never replaced as a changed peer; the run fails, since
the address reaches the wrong cluster.

## The overwrite table

Authorizing into a directory that holds data replaces that data. The first job deletes whatever the source lacks, and a
directory that holds only subdirectories counts as non-empty (G-REPL-OVERWRITE). The overwrite table lists each case
the collection distinguishes and says what, if anything, lifts it. Each row has a number, and the modules name it in
their results as `row` in `unmet`.

| Row | Evaluated on | The case | What lifts it |
|---|---|---|---|
| O1 | Target | The target directory holds any entry, a subdirectory included. | `allow_non_empty_target` on the relationship's entry. |
| O2 | Target | A live record of another relationship holds the target path. | A release naming O2, bound to that record. The module deletes the holder; its data stays in the directory. |
| O3 | Source | Every record of the key on the source is ENDED. | `recreate` and a release naming O3. |
| O4 | Target | The source holds no file, and the target directory does. | A release naming O4. |
| O5 | Target | An ENDED record lies over a target directory that holds files. | A release naming O5, bound to that record. |
| O6 | Target | The target holds a live record of the key that the source does not. | Nothing. Only `state: absent` removes it. |
| O7 | Target | A live relationship holds a path that contains the target path or lies inside it. | Nothing. Remove that relationship first. |
| O8 | Source | This cluster is a target of the declared target's relationship at a path equal to or nested with the source path. | Nothing. |
| O9 | Target | This cluster replicates to the declared source at a path equal to or nested with the target path. | Nothing. |

O8 and O9 are reversals. A relationship that failed over has its direction turned around, and the baseline leaves it
for the [replication_failover](../../roles/replication_failover/) role's failback to turn back. O7 is a different
relationship over a nested path, and it has to be removed first. O6 is
what a lost delete notification leaves (see [Removing a relationship](#removing-a-relationship)). A release can lift O2,
O3, O4 and O5, and O3 also needs `recreate`. The entry's consent lifts O1. The restore lifts O1 by
`allow_directories_only` for a directory that holds only directories.

Several rules in the table need a reason.

**Emptiness comes from entries.** The module decides whether a directory is empty by listing its entries, never from
the directory's aggregates, which trail writes and deletes and so can misstate whether a directory holds anything. The
aggregates still supply the file counts and sizes the capacity comparison uses.

**O1 counts differently from O4 and O5.** O1 counts every entry, a subdirectory included, because the server refuses an
authorize into a directory holding only directories unless it is sent a flag. O4 and O5 ask whether data would be lost,
so they count only files, links and other objects at any depth. The restore of a rebuilt edge relies on the
difference: it accepts an edge source directory that holds only directories (`allow_directories_only`), which is R's
target in the restore, and refuses one that holds a file.

**One ENDED record is O3 on one side and O5 on the other.** The source reports its ENDED record of the key as O3. The
target reports its copy of the same record, which has the same id, as O5, because the target cannot see the source.
The record is O3's, so the plan leaves the O5 entry out, and gives the target's pass each release that names O3 with
O5 added. The release printed for O3 alone therefore lifts both.

**The rows are reported in a fixed order, O8, O3, O7, O9, O2, O6, O5, O4, O1.** The modules report every unmet row at
once, not the first. The order puts a holder (O2) and an empty source over files (O4) before the plain non-empty
directory (O1) that they explain, so that a held relationship reads as held and not as a call for
`allow_non_empty_target`.

**The capacity comparison warns.** When the source looks larger than the space the target can reuse, the target module
warns, and refuses only with `strict_capacity`. The comparison is approximate. Whether the free space the cluster
reports leaves out an edge's evictable cache is inferred, not observed.

## Releases

A release lifts a refusal that has a holder behind it. It is one entry,
`<target host>:<target path>@<record id>:<rows>`, as in `dr01:/replicas/edge01-projects@<id>:O2,O4`.

It is bound to the id of the record it lifts, so a release written for one holder cannot delete a different record that
takes its place later. It lifts only the rows it names, and only while the record with that id exists. An O4 with no
record behind it is bound to the target directory's file id, so a release for one directory cannot pass for another that
takes its path. The refusal prints each release that would lift it, and the operator reads what it allows before copying
one.

A release belongs to one run, so it goes in with `-e` and not in the inventory. An entry that lifts nothing in a run
fails the run, which keeps a release from sitting in a file until the case it was written for comes up again. An
entry for a target host the run does not reach is left alone, so `--limit` still works. [Refusals and
guards](refusals-and-guards.md#a-held-record) puts the release among the other kinds of refusal.

The consent for O1 is a different mechanism. `allow_non_empty_target` belongs to the relationship's entry, it carries
no id, and the role accepts it only for a target directory the target host declares in `qumulo_directories`.

## Removing a relationship

The source tells the target about a delete once, from memory (G-REPL-NOTIFY). A target that is unreachable at that
moment, or a source that is gone, leaves the target's record live and its directory read-only. So a removal goes
source first, then target, and each module deletes only what its own cluster holds. The target keeps the directory and
its data, and the directory becomes writable once no record holds it.

Declaring the relationship `present` again later needs `allow_non_empty_target` again, since O1 then applies, and
replaces that data. Two cases have a way out because one
end is gone: `replication_target` with `state: absent` on the target alone, for a source gone for good, and
`target_gone` on the source, which deletes only there, for a target gone for good.

A removal leaves snapshots. The target's last recovery point and a make-writable's revert snapshot lose their owner
when the relationship goes, never expire, and can read back with an empty path (G-REPL-SNAPSHOT). The modules name
them in their results and leave them, since each is the last consistent copy. Deleting them is the operator's call.

## Failing over

A failover, by hand, by the restore or by the [replication_failover](../../roles/replication_failover/) role, uses
the three verbs of [replication_reverse](../../plugins/modules/replication_reverse.py), which the inventory does not
declare. The restore of a rebuilt source from its replica runs the three in turn, and a reversal by hand takes the same
three.

- `make_writable`, on the target, sends the directory back to the relationship's last recovery point and makes it
  writable. The relationship goes DISCONNECTED on both sides.
- `reverse`, on the current target, turns the records around: this cluster's target record becomes a source record of
  the same id, to the current source's address, and the current source's record becomes a target record. This is why a
  cluster can hold either a source or a target record of one id after a reverse, and why
  [replication_info](../../plugins/modules/replication_info.py) takes a `side`.
- `reconnect`, on the relationship's target, sends the directory back to its recovery point, read-only, and goes
  RECONNECTING and then ESTABLISHED once the source reaches it.

A reverse carries the id, the paths, the address and `replication_enabled`. The blackout windows and their timezone,
`map_local_ids_to_nfs_ids` and `source_root_read_only` come back at their defaults. The current source's directory
keeps its read-only state. It stays read-only after the reverse while another relationship's record holds it, until that
record is deleted, and when `source_root_read_only` made it read-only. This is why the restore deletes X before the
reconnect, not before the reverse.

`make_writable` and `reconnect` send the directory back to a recovery point, which discards whatever was written there
after it. `make_writable` needs `allow_data_loss`, and compares nothing: its target is read-only, so all it can discard
is the partial changes of a job. `reconnect` without `allow_data_loss` first compares its directory with the recovery
point and is sent only when nothing was written there since. With `allow_data_loss` it is sent without comparing, as the
restore sends it. `reverse` moves records and changes no data, so it takes no consent.

None of the verbs can be sent twice. The module sends each only from the state it starts from, reports a record
already in the state the verb leaves as unchanged, and reconciles a lost answer by reading the record. When the record
shows no sign of the verb, it fails with `maybe_applied` set and the next run reads the record and decides.
[State management](state-management.md#maybe-applied) explains why the collection never resends a write blindly.

A relationship found in a failover state is left alone by the source module: it changes none of the state, still sends
the settings, and warns.

### The role's three actions

The role splits the return trip into three actions, each a separate run, because only the operator knows when clients
can move.

- `failover` makes the target's copy writable and sends nothing to the source, so it works with the source gone.
- `resync` turns the relationship around, so the target replicates the failover's writes to the source, and reconnects
  it. Clients stay on the target meanwhile, and a resync after a long failover can take hours.
- `failback` makes the source writable from a recovery point that holds what the target held, turns the relationship
  back and reconnects the target, so the source is the writable copy again. It needs the clients off the target before
  its last sync, which only the operator can arrange. A failback that finds the relationship still failed over runs the
  resync's steps first, so one run does the whole return for an operator who has already stopped the writes.

Qumulo's own failback has two forms: copy the target's writes back, which `resync` and `failback` do, or discard them
and reconnect. The role leaves the second to `replication_reverse`, which does it in one task, since a role that
discarded writes by default would make the discard the easy path.

### One step at a time, from fresh reads

The role does not run a fixed list of tasks. Before each step it reads both clusters with `replication_info` and asks
[replication_failover_plan](../../plugins/modules/replication_failover_plan.py) for the next step, then runs only that
step, as the restore runs the answer of `replication_restore_step`. A run that stops anywhere, by a timeout, a lost
connection or a killed job, therefore resumes from what the records show and not from where the last run got to. The
plan module is its own module because the restore's records and report claims have no counterpart in a failover.

The plan's first answer lists every step left, which is what a check run prints as the dry run. The collection's dry
run is check mode everywhere ([State management](state-management.md#check-and-diff)), so the role takes no `dry_run`
variable, which would be a second owner of the same fact. The role plans every named relationship, and refuses all of
them for the plan's refusals, before the first write of any. A step that is a verb includes the waits that follow it, so
that each step ends where the next starts.

The role takes no lock, unlike the restore. Each verb is sent only from its state, so neither of two runs at once can
send a verb twice. Each reconnect compares, so neither can discard writes it did not compare, except in a reconnect to a
host that `replication_failover_allow_newer_writes` names, which does not compare.

### Why a reconnect compares, and why the consent names a host

A reconnect sends its directory back to the recovery point. In a resync that directory is the source's, which clients
may have written after the failover. In a failback it is the target's, which clients may have written after the last
sync. The comparison finds both. It takes a snapshot of the directory and reads the changes since the recovery point's
snapshot, which lists every change, an edit in place included. The directory's aggregates cannot stand in for it: they
miss an edit in place and the data of a small file. A check run cannot compare, since a snapshot must be taken to
compare, so it says that the real run does, or, for a host that `replication_failover_allow_newer_writes` names, that it
does not compare first.

The comparison has a window. It takes its snapshot a few requests before the reconnect, and a write made between the
two is discarded without being compared. Stopping the writes on the directory first closes the window.

A job in flight can leave writes for the comparison to find. After a reverse, a record names the last completed job's
snapshot as its recovery point, never the snapshot of a job still running. So a job that was running at the failover, or
at the failback's make-writable, carried writes that the recovery point lacks, and the reconnect's comparison finds them
on the source in a resync and on the target in a failback.

The role's consent has three parts. Two follow from the two kinds of loss.
`replication_failover_allow_data_loss` accepts the loss every run of an action carries, which the plan lists. It
never accepts a loss the run found.
`replication_failover_allow_newer_writes` accepts a loss the run found and named, and it names a host, as a release
names a record ([Releases](#releases)): the host is the one a reconnect is sent to. The two reconnects need different
answers. The source's writes found by a resync can be copied to the target, or discarded. The target's writes found by
a failback are the clients' latest, and the way on is to run the failback again, which reads the relationship as failed
over and resyncs them to the source first. Naming the target there would let the rerun's own last reconnect skip its
comparison, so the role's message does not offer it.
`replication_failover_allow_partial_job` is the third, for a failover only: it accepts the loss of the partial job the
answering source is still running, which the plan would otherwise refuse
([Why a failover goes on without the source](#why-a-failover-goes-on-without-the-source)).

The consents are run-level. The role sets all three in its own `vars/main.yml`, so a consent in `group_vars`,
`host_vars`, a play's `vars` or `vars_files` is ignored. Anything a playbook sets closer to the role or during the run
lifts it: `-e`, the include's `vars`, a block's `vars`, role parameters, `set_fact` and `include_vars`. The next run,
which may hold a relationship nobody looked at, has to ask again.

### Which record says where a relationship is

A relationship has a record on each cluster, and a verb moves the record of the cluster it is sent to at once. The
peer's record follows within seconds, or not while the peer cannot be reached. Observed lags run up to 25 seconds.
The role therefore reads each direction's state from the record a verb moves first. In the declared direction, which
is the source to the target, that is the target's record. After a reverse, in the reversed direction, it is the
source's. The other record may lag, and a lag shows as a wait only where the next step needs the peer: before the
resync's reverse, and before the relationship counts as replicating or resynced. A failover reads the target's record
alone, since the source's may lag, be ENDED or not answer.

The role finds the relationship by its declared key, in either direction. In the declared direction the target holds a
target record from the source's UUID with the two paths. After a reverse it holds a source record of the target path to
the source's UUID. An ENDED record counts only when no live record of the key is left. Live records in both directions,
or two in one, are refused, since a failover acts on one relationship. A live target record from one of the source
host's previous UUIDs is the replica a restore brings back, and the role refuses it and names the restore.

### Two states that read the same

Two pairs of states read the same from the records. The plan reads each as the one whose next step loses nothing, unless
the run's last verb says otherwise.

- The source's target record DISCONNECTED after a reverse is both the resync's state before its reconnect and the
  failback's state after its make-writable on the source. The plan reads it as the resync's, a reconnect on the source.
  A reverse on the source in the first state would make the stale source the writer. A reconnect on the source in the
  second state compares the source's directory first, and only an empty comparison lets it discard anything. A finished
  wait for the source's record to read DISCONNECTED counts as the make-writable, since only the failback's
  make-writable leaves that record DISCONNECTING.
- The target's record DISCONNECTED in the declared direction is both failed over and the failback's state after its
  reverse on the source. The plan reads it as failed over, which resyncs. A reconnect on the target in the first state
  would discard the failover's writes if the comparison did not stop it. A second reverse in the second state is
  accepted and changes no data.

A run that stops between those steps and runs again therefore repeats part of the resync, at the cost of a sync, and
loses nothing.

### Why a failover goes on without the source

A failover is the step taken because the source is gone, so it must not need the source. It reads the source with a
short timeout, which belongs to the failover alone. In any action, a source that does not answer is not read again in
the run, and a resync or a failback, which needs the source, then refuses every relationship at once. A read that gives
up, or that ends in a 502, 503 or 504 after its retries, as from a gateway in front of a lost cluster or a node that is
still starting, counts as not answering. Any other failure, such as a refused login or an identity mismatch, fails the
run, since a cluster that answers and refuses is not a cluster that is gone.

The plan refuses a failover for a job running on a source that answers, since the make-writable would discard its
changes, and the operator can wait for it. `replication_failover_allow_partial_job` lifts the refusal by accepting the
loss of that partial job, as for a job that makes no progress; the plan then notes the job by its start time. With the
source gone, the target's record keeps reading a job as running for minutes (QUIRK-125), and the server still accepts
the make-writable and discards the partial job. A refusal then would block every failover after a source died mid-job,
which is the case the role exists for, so the plan goes on and notes the job by its start time. A relationship whose
first job has not finished has no recovery point, and the server refuses its make-writable, so the plan refuses it
first.

### A restore under way

A rebuilt source's restore runs the three verbs itself, and past the delete of the old record its relationship reads
as failed over, so a resync or a failback would turn it away from the restore. The role refuses those two while the
restore's record is in the artifact directory. A failover cannot turn the restore away: before the delete the old
record refuses it, and after the reconnect the restore is done. So for a failover the role notes the record and goes on.

### Why the failback waits for two moves of the recovery point

The failback makes the source writable from a recovery point, so the recovery point has to hold what the target held
when the writes stopped. The replicating cluster's continuous job runs about once a minute even with nothing to copy,
and each job moves the recovery point to its own start, so the recovery point means "data as of" a time on that
cluster's clock. The role reads the target's recovery point and waits until it has moved twice. The first move may come
from a job that began before the read. The second comes from a job that began after it, and so after the writes
stopped. That needs no clock outside the cluster. The wait has its own timeout, like the restore's full-sync wait, and a
job that reports an error does not end it, since the error stays set while a later attempt succeeds.

A failback of several relationships brings each to its wait, then cuts each over once the waits end. The relationships
on one target host are polled together and the target hosts one after another, within one timeout from the start of the
waits, so the writes on a target stay stopped for one wait and not one per relationship. A write made after the wait is the
failback's reconnect to find.

### What the reverses reset, and what the role applies again

The first reverse gives the target's new source record the server's defaults, among them no id mapping, and a copy back
of a file owned by a local user fails without the mapping (G-REPL-IDMAP). So when the declared entry maps local ids, the
resync turns the mapping on in that record right after its reverse. The reverse back to the source returned its own
settings on the one release it was seen on, which the module's documentation does not promise. So a failback that finds
the relationship replicating, and the source's record different from a declared setting, applies the declared settings.
That step comes from the records, so it runs even after a run that stopped once the relationship was ESTABLISHED.

Each reverse makes a cluster the target at its own replication address, which the server refuses with a connection error
that names neither the tenant nor the switch when the tenant that serves the address has replication off. So the plan
applies the check the baseline applies at step 21
([The replication address](../reference/troubleshooting.md#the-replication-address)) to each new target before the
action's first write.

## Held relationships

A rebuild destroys an edge's data. When the edge was a replication source, the replica on its target may be the only
copy. The target's record of the old relationship is then the server's own lock on that replica, and the collection
keeps it. While a source has `qumulo_previous_cluster_uuids`, the plan calls a relationship held when row O2 is unmet
and not lifted and the holder is a record from one of those UUIDs, or when row O4 is unmet and not lifted. It reports it
as held and not refused because the replica may be the only copy of the rebuilt source's data. The message names the
restore when X allows it, and otherwise the copy back into the source directory and the release that lets the
relationship go ahead. The rebuild's replay sets `replication_pair_on_held` to `report`, so the role records a held
relationship, writes nothing on either cluster and goes on, where a refusal would stop the run.
`edge_rebuild_allow_data_loss` never lifts a hold.

Every later baseline run refuses a held relationship before any write, until there is a way out. There are three:

- Restore the data from the replica ([Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md)).
  [Why a restore keeps X and turns R around](#why-x-is-kept-and-r-turned-around) explains its shape.
- [Copy the replica back](../how-to/restore-replica.md#copy-the-replica-back) over a protocol, then run the baseline
  with the release the refusal printed and `allow_non_empty_target` on the entry, so the source creates the relationship
  and the target authorizes it. The release and the consent lift the rows on purpose, once the data is back.
- [Keep the replica as plain data](../how-to/restore-replica.md#keep-the-replica-as-plain-data), with the entry set to
  `state: absent` and the same release. The release deletes the holder's record and leaves the directory writable with
  its files.

The same relationship seen from the other end is different. When the edge is the target and its record is
ESTABLISHED, read-only and matched by the source's own ESTABLISHED record, the replica is rebuilt by a full sync, so
the rebuild does not treat the directory as local data. In any other state, after a failover by hand or with an ENDED
or pending record, it is local data, and the rebuild refuses until `edge_rebuild_acknowledge` lists `local_data`. A
record that names a previous UUID of the source is `only_copy`: the edge's replica may be the only copy of data the
source had, the edge's record is the hold that keeps the source's own relationship held, and rebuilding the edge
destroys both. The rebuild then refuses until `edge_rebuild_acknowledge` lists both `local_data` and `replication`.
[Rebuilds](rebuilds.md) covers the rest.

The replay of a rebuild creates no relationship into the edge, for the reason the capacity check gives above. The
report lists each as a hand-off of the second pass.

## The restore

The restore runs nine steps across the target, the edge and the controller. [The restore's records and
steps](../reference/playbooks-and-roles.md#replication-restore) lists them. This section explains why it is shaped as it
is, then what it leaves on the controller and how it locks.

### Why X is kept and R turned around

The restore's records have names. X is the old record on the target, from a previous UUID of the edge, that holds the
replica. R is the restore relationship, from the target's replica directory to the edge's declared source path.
The reference lists both.

**X stays until the edge has the data.** The restore deletes X at step 7 and not before, after R's full sync has
reached the edge, the edge's directory has been made writable, and R has been reversed. Until then X holds the replica
in place on the target, and a run that stops at any earlier step leaves the data where it was. The reconnect that
ends the restore comes after X's delete, because the server refuses a reconnect while another record still holds the
target's directory.

**R is turned around and not replaced.** The restore does not create the declared relationship from the edge into the
replica directory. That would be the held case above. X holds the replica directory (O2), and the edge's empty directory
would replace the replica's files (O4). Lifting those rows would destroy the only copy. So the restore runs the data the
other way first. It builds R from the target to the edge, lets the full sync carry the replica to the edge, reverses R
so its records match the declared direction, and reconnects it. The relationship that ends up in place is R, with its id
kept through the reverse, and the last step applies the declared settings to it. R is created with the server's defaults
and no blackout window, which is why the declared windows and mapping come only at the end.

**Each step is chosen from fresh reads.** The role calls
[replication_restore_step](../../plugins/modules/replication_restore_step.py) before every step, with new reads of both
clusters, and runs only the step it returns. A restore is nine steps across two clusters and a controller's files, any
of which can stop. Choosing from what the records show, and not from where the previous run got to, makes a rerun
resume correctly after a timeout, a lost connection or a killed run.

**File owners come back through ids.** Replication carries a file owned by a local user or group, other than the
built-in ones, as an NFS uid or gid. A source with `map_local_ids_to_nfs_ids` off fails every job on such a file. With
it on, every such user needs a uid and every such group a gid. The target then holds the file owned by an NFS uid, and
the restore writes it back to the edge the same way, whatever the restore relationship's own mapping. The built-in admin
user and Users group come back as themselves. The edge's baseline recreates its local users and groups with their
declared uids and gids, so a restored file reaches its owner again (G-REPL-IDMAP). A uid set on the old edge outside the
inventory comes back as an NFS uid no local user has.

### The restore's records and stamps

A restore leaves two files on the controller under `<artifact dir>/<edge>/restore/`. The restore record lets a stopped
restore resume. The restore stamp retires the newest rebuild report's claim on the relationship, so the report stops
listing it as held.

Both are named by the digest of the relationship's key: the source path, the target cluster's UUID and the target path,
each path without a trailing slash and the UUID in lower case. A different controller finds the same files, and so does
an inventory that adds a trailing slash to a path. The target host's name is in the record but is not hashed, so an
inventory that renames the target host finds them too. [restore_key_hash](../../plugins/filter/restore_key_hash.yml)
defines the digest.

The record is for one controller. To resume from another, move it, and delete it from the first: two controllers
holding the same record could both act on the relationship.

### The restore's lock

A restore holds a lock on the edge so that a second restore cannot start on the same controller while one runs.
[replication_restore_lock](../../plugins/modules/replication_restore_lock.py) takes it, and the role removes it however
the run ends. Four cases shaped it.

**The lock survives a crash.** The module writes the lock to a temporary name, syncs it to disk and links it into place.
A crash of the process or of the machine leaves either no lock or a whole one, never an empty or partial file. A lock
that is empty or not JSON, which an older crash or a hand edit can leave, refuses and names the file to remove by hand
once no run is under way.

**Liveness is judged on the controller.** A lock records the controller's host name, boot id and PID namespace, the
`ansible-playbook` process's id and its start in clock ticks since boot. A lock from this controller, meaning the same
host name, boot id and PID namespace, is taken over as soon as its process is gone. A lock from anywhere else, or from a
controller without `/proc`, waits for its lease, because nothing on this machine can say whether that process is alive:
another machine can share a host name, a lock from before a reboot has another boot id and so cannot be told from one
taken on another machine of the same name, and another container on this machine shares its boot and can share its host
name. The process is matched by its start ticks as well as its id, and no step of the clock moves them.

**Two takeovers cannot both win.** A takeover holds an exclusive `flock` on a guard file while it reads the lock again
and replaces it, so of two runs only one takes it over. The kernel drops the `flock` when its process dies, so a
crash leaves nothing to clean up. A run waits at most 60 seconds for the guard before it refuses, as when the run
holding it was stopped. The guard file itself is kept.

**A run that lost its lock stops.** A renewal never takes a new lock. If the lock is gone, holds another token, or
holds the run's token but was taken by another process, the renewal refuses, and the run stops instead of going on
without the protection it thought it had.

The lease is sized from the run: for each relationship the authorize wait, the reverse and reconnect polls and 600
seconds of reads, plus the full-sync wait once. With the default full-sync wait of 86400 seconds, the lease is over a
day. A run killed at a pipeline job's time limit leaves its lock, and a later job that does not share the controller,
such as one in another container, waits out that lease. A full-sync wait that ends before the job's limit lets the
restore stop on its own and remove its lock. [Restore a rebuilt edge's data from its
replica](../how-to/restore-replica.md) says how to set it.

The lock is the controller's alone. Two controllers can both restore one edge, so keep to one restore per edge at a
time across controllers.

## Replication across upgrades

Replication works across releases up to about eight quarters apart. The upgrade plan warns about each declared
relationship whose ends would be further apart before the run or after any hop. A newer source can hold new kinds of
state back from an older target until each file changes again, so the plan commits a relationship's target before its
source where it can. That is a preference and never a refusal. Of two clusters that replicate to each other, it keeps
one order and drops the other with a note, and `qumulo_upgrade_after` wins over it. [Upgrades](upgrades.md) covers the
order.

## What the collection leaves alone

- It does not manage object relationships, which copy between a directory and an S3 bucket on one cluster.
  `replication_info` reports them without their credentials.
- It never compares or sends a relationship's `replication_mode` or `snapshot_policies`, so an adopted relationship
  keeps them.
- It does not delete the snapshots a removal or a failover leaves.
- It moves no data itself. The clusters replicate between them, and the controller only declares, authorizes and reads.

## Learn more

- [Replicate a directory to another cluster](../how-to/replicate-a-directory.md) declares, releases and removes a
  relationship.
- [Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md) runs the restore and fixes what stops
  it.
- [Fail over to a replica, and fail back](../how-to/fail-over-replication.md) runs the failover, the resync and the
  failback.
- [Variables](../reference/variables.md) lists the replication variables.
- [The restore's records and steps](../reference/playbooks-and-roles.md#replication-restore) lists X, R and the nine
  steps.
- [The failover role](../reference/playbooks-and-roles.md#replication-failover) lists its states, steps and facts, and
  [Troubleshooting](../reference/troubleshooting.md#failing-over) its messages.
- [The gap register](../reference/gaps.md) holds the G-REPL entries named above.
- [State management](state-management.md) explains check mode, keys and ids, and a write that may have been applied.
- [Rebuilds](rebuilds.md) and [Edges and portals](edges-and-portals.md) explain the cases a relationship meets around an
  edge.
