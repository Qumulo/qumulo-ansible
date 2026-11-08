<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Recover an upgrade that stopped

Use this page when `qumulo.cluster.upgrade` stopped before the fleet reached its target.
You read the report, lift the cause and run again.
Three stops need more than a rerun: a commit that did not finish, a paused reboot, and a restore lock another run holds.
When you finish, the fleet is on its target and a further run changes nothing.

## Before you start

- Have the message the run ended with, or the run's report.
  The report is `artifacts/upgrade-<UTC>.yml` beside the inventory, or in `cluster_upgrade_artifact_dir` when you set it.
  It holds the plan and every hop so far, a failed hop included ([The report](../reference/upgrade.md#the-report)).
- Run from the controller that ran the upgrade, with the same `cluster_upgrade_artifact_dir` (commit records and
  reports) and `edge_rebuild_artifact_dir` (restore locks).
  Both default to `artifacts/` beside the inventory.
  Another controller, or a run with either directory moved, doesn't see the records or locks, so keep one artifact
  directory that every controller running the upgrade shares.
- A refusal from the plan carries its code in the report's `plan.refusals`, and a warning its code in `plan.warnings`.
  A failure during a hop carries only its message.
  Find the message in [Troubleshooting](../reference/troubleshooting.md#upgrades), whose tables give each code, `F` for a
  refusal and `W` for a warning, and what lifts it.

## Resume an upgrade that stopped

A run stops before its next hop when a host of the hop failed or was held, when the hop's commits were to begin outside
the maintenance window (F9), or when a restore lock the run held could not be renewed.
It then exits non-zero and names the hop and the reason.

1. Read the hop in the report.
   Each host has an `outcome`.
   A `failed` host has its `message`; find it in [Troubleshooting](../reference/troubleshooting.md#upgrades) for what
   lifts it.
   A `held` host names the failed host that held it in `held_by`.
   `lock_held: true` means a restore lock the run could not take held it instead
   ([A restore lock another run holds](#a-restore-lock-another-run-holds)).
   `lines` says what else the hop did, such as a portal component it left on two releases.

2. Lift the cause:

   | The report says | Do |
   |---|---|
   | A node is offline or a disk is missing (F5) | Bring it back into the cluster. |
   | The cluster still reads blocked after a wait (F5) | Run again later, or raise `cluster_upgrade_unblock_timeout`. |
   | A reboot is still under way (F24) | Wait for it to finish. |
   | The image does not match its entry (F21) | Correct the entry's file name or version in `qumulo_upgrade_images`. |
   | The hop is outside the maintenance window (F9) | Wait for the window. |

   The other causes are in [Troubleshooting](../reference/troubleshooting.md#upgrades).

3. Check, then run again:

   ```sh
   ansible-playbook qumulo.cluster.upgrade -i inventory --check
   ansible-playbook qumulo.cluster.upgrade -i inventory
   ```

   The plan starts again from each cluster's running release, so the rerun needs no change to the inventory.
   The report names the hops already done.
   A cluster already on its target with its reboots done is skipped, one still rebooting is waited for, and one already
   `PREPARED` with the hop's image, reboot settings and waivers is not prepared again; the report says so.
   A portal component the failure left on two releases is brought back to one release in the next hop, and outside the
   maintenance window that is all the hop commits.

4. Verify.
   When every cluster is on its target, a further `--check` run changes nothing: its report holds the plan alone, and
   `skipped` lists each cluster as at its target.

### Go on with the rest of the fleet

To upgrade the other clusters while one waits, leave the waiting one out with `-l`.
For a cluster in no portal component:

```sh
ansible-playbook qumulo.cluster.upgrade -i inventory -l 'qumulo_clusters:!cluster01'
```

For a spoke, leave out the hub and every one of its spokes, the whole component:

```sh
ansible-playbook qumulo.cluster.upgrade -i inventory -l 'qumulo_clusters:!hub01:!edge01:!edge02'
```

- A limit that leaves out part of a portal component is refused (F13): leaving out only `edge02` leaves `hub01` in the
  run with a member outside it.
- Leave out any cluster that replicates to the waiting one, or it upgrades ahead of its target (W12).

### Stops that want more than a rerun

| The stop | Do |
|---|---|
| A commit that did not finish (F19) | [A commit that did not finish](#a-commit-that-did-not-finish), before anything else runs on that cluster. |
| A commit still under way (F30) | Wait for it to finish, then run again. Its record goes by itself once the cluster runs the target on a new build. |
| A reboot paused by hand (F25) | [Resume a paused reboot](#resume-a-paused-reboot). |
| A reboot stopped on an error (F26) | Contact Qumulo Care before running anything else on the cluster. Do not resume it. |
| A restore lock another run holds (F22) | [A restore lock another run holds](#a-restore-lock-another-run-holds). |

## A commit that did not finish

The run ends with this message when a cluster's commit did not finish:

```text
edge02: the commit did not finish. The cluster still runs 7.10.3 and its upgrade status is IDLE (no error). Some nodes
may be armed to start 7.11.0 at their next restart, and Qumulo Core's own guidance for this state is manual
intervention. Contact Qumulo Care before running anything else on edge02. The collection has recorded this at ...
```

The next run's plan refuses the cluster (F19) until the record is removed.
A record left `pending` by a killed run is decided from what the cluster reads
([Commit records](../reference/upgrade.md#commit-records)).
[Why a commit is recorded](../explanation/upgrades.md#the-record-on-the-controller) explains the record.

1. Contact Qumulo Care before running anything else on the cluster: a prepare or a restart of an armed node can start it
   on the new build ([why](../explanation/upgrades.md#why-the-clusters-own-status-is-not-enough)).
   Give them the message and the commit record, `artifacts/<host>/upgrade/commit.yml` beside the inventory
   ([its fields](../reference/upgrade.md#commit-records)).

2. Meanwhile, upgrade the rest of the fleet with `-l`, leaving out the host or its whole portal component, as the
   refusal's last sentence says ([Go on with the rest of the fleet](#go-on-with-the-rest-of-the-fleet)).
   The plan does not read the record of a host the limit leaves out.

3. When Qumulo Care has cleared the cluster, remove the record:

   ```sh
   rm artifacts/edge02/upgrade/commit.yml
   ```

   A `pending` record also goes by itself once the cluster runs a new build of the target or a later release.
   An `unfinished` record stays until you remove it, unless the host has become a different cluster, as after a rebuild.

4. Run the upgrade again, with `--check` first.
   A portal component the failure left on two releases is brought to one release first.

5. Verify.
   The check run no longer refuses the cluster, and the real run's report shows it `committed` or `at target`.

Other forms of this stop are in [Troubleshooting](../reference/troubleshooting.md#upgrades):

- `The collection could not record this at ...`: the record written before the commit is the guard.
  Follow the steps above, and make the directory under `cluster_upgrade_artifact_dir` writable before the next run.
- The commit was sent and the cluster doesn't yet read the target (F32): run again once it does, or raise
  `cluster_upgrade_wait_timeout`.
  The next run waits on the record (F30) or retires it.
- The file is not a record the collection can read (F31).
  Ask Qumulo Care whether a commit of the cluster is unfinished, then remove the file.
- A module run on its own without `commit_record` writes no record.
  Its message says so; run the next upgrade of that cluster only once Qumulo Care has cleared it.

## Resume a paused reboot

A platform upgrade's reboot can be paused, with `qq reboot_pause` for example.
The upgrade never pauses one, so a run that finds a paused reboot fails that host at once, at the first step of its hop or at its
reboot step, with a message that begins:

```text
edge01's reboot is paused, and this run did not pause it. Follow ...
```

1. Read the reboot's state, logged in to the cluster with `qq`:

   ```sh
   qq reboot_status     # state: REBOOT_PAUSED
   ```

   If the state is `REBOOT_PAUSED_DUE_TO_ERROR`, stop here: the reboot stopped on an error (F26).
   Do not resume it.
   Contact Qumulo Care before running anything else on the cluster.

2. Resume it, with `qq` or with `POST /v1/shutdown/reboot/resume`:

   ```sh
   qq reboot_resume
   qq reboot_status     # REBOOT_IN_PROGRESS until every node has rebooted, then REBOOT_IDLE
   ```

3. Run the upgrade again.
   Its first step waits for the rest of the reboot, up to `cluster_upgrade_reboot_timeout`, and a cluster already on its
   target waits only for its reboots.
   If the wait runs out, the run ends with F24: let the reboot finish, then run again, or raise the timeout.

4. Verify.
   `qq reboot_status` reads `REBOOT_IDLE`, and the run's report shows the cluster `rebooted` or `at target`.

## A restore lock another run holds

The run takes each host's restore lock, `<edge_rebuild_artifact_dir>/<host>/restore/lock`, for every host it moves or
reboots and every host whose replication targets one of them.
By default that directory is `artifacts/` beside the inventory.
A lock held by a restore or by another upgrade run fails that host (F22), and the run holds that host and every host it
replicates to, in every hop that moves them.
The rest of the fleet goes on, and the run ends after the first hop that moves one of those hosts.
[Restore locks](../reference/upgrade.md#restore-locks) gives the path and the takeover rules, and
[why a lock is taken](../explanation/upgrades.md#restore-locks) the reason.

1. Read the refusal.
   It names the holder, the `ansible-playbook` process, the controller and the lease's end.

2. Wait for that run to end or its lease to run out, then run the upgrade again.
   Remove the lock file only if that process is gone from the controller the refusal names.

   ```sh
   rm artifacts/edge02/restore/lock
   ```

A run stopped with Ctrl-C, or killed, never reaches its end and leaves its locks.
You need not remove them in the usual case:

- On the same controller, the next upgrade or restore takes such a lock over, with a warning, once the `ansible-playbook`
  process that took it is gone.
  It refuses the lock while that process is alive.
- From another controller that shares the artifact directory, a lock is taken over only once its lease runs out, which
  for a hop is many hours.
  The lock file is JSON, and its `lease_expiry` gives the lease's end.
  Check that the run has ended before you rely on that, or remove the file by hand once it has.

When the run itself stops with `...'s restore lock could not be renewed`, another run took the lock over or removed it.
Check that no other run or restore of that host is under way, then run again.
[Troubleshooting](../reference/troubleshooting.md#restore-locks) quotes each lock message.

## What next

- Upgrade the fleet from the start: [Upgrade a fleet](upgrade.md).
- Every variable, report field and file of the upgrade: [Upgrade reference](../reference/upgrade.md).
- Message text and what lifts each refusal: [Troubleshooting](../reference/troubleshooting.md#upgrades).
- Why the upgrade records a commit, holds a failed host's whole portal component and runs hops in lock-step:
  [Upgrades](../explanation/upgrades.md).
