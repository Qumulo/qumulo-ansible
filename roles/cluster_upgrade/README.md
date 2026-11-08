<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->
# cluster_upgrade

Runs the hops of an upgrade plan across a fleet of Qumulo clusters. The plan, from `qumulo.cluster.upgrade_plan`, decides each hop's release, the order of its commits and what a failure holds; `qumulo.cluster.upgrade` decides whether each cluster is ready. The role runs the hops in lock-step: every host's hop finishes before any host's next hop starts, and a hop that ends with a host held or out of the play, or that was outside the maintenance window, stops the run.

The role runs only in a linear play without `serial`. A portal component's spokes commit together and then their hub because each task of a linear play finishes on every host before the next starts, and the held units reach every host through `run_once` facts, which the `free` and `host_pinned` strategies would run once per host. Set `strategy: linear` on the play, which wins over `ANSIBLE_STRATEGY` and ansible.cfg; the role's first task refuses a play whose batch is not all its hosts, as one with `serial`. Set `any_errors_fatal: false`, so a failure holds only its own unit, and `timeout: 0`, so no task timeout cuts a prepare or a commit's wait. The playbook that calls this role sets all three, and before it reads the clusters, plans with `qumulo.cluster.upgrade_plan` and takes the restore locks.

## A hop

| Step | Runs on | A host that fails |
|---|---|---|
| 1, wait out a reboot under way, then the preflight | the hosts the hop moves and does not hold | leaves the play; its unit is held |
| 2, prepare | the same | stays in the play for step 3's second prepare, unless no second prepare can lift the refusal (the result's `final`) |
| 3, prepare again, then the readiness check | the hosts whose first prepare failed, then every host of step 2 | leaves the play; its unit is held |
| 4, the maintenance window | once, when a host of the hop declares a window | outside it, the window's answer holds every unit except the moving members of a split component it closes |
| 5, commit each step of the schedule in order | each step's hosts that are ready and not held | leaves the play; its unit and every unit that follows it are held |
| 6, the peers | each moved component's hub, or its spokes when the hub is outside the play; the sources of the replication relationships that touch a moved host | never fails: a timeout, or a source too old for the route, is a report line |
| 7, the reboots | the hosts that committed, and those the plan only reboots | leaves the play |
| 8, the peers again | as step 6 | never fails |
| 9, the report | once, also when the hop fails | – |

A plan with no hop, as when every host is at its target, gets a report of the plan alone.

Step 5's readiness check prepares again, inside the mismatch window, only a later wave's host or a member of a component that started the hop split; any other host sends only reads, and a lost prepare holds its component before any member commits. Every call that can prepare, and every commit, passes the host's commit record path, `<cluster_upgrade_artifact_dir>/<host>/upgrade/commit.yml`, and the module writes and resolves the record itself.

At each hop's start, outside check mode, each host of `upgrade_lock_hosts` renews its restore lock to the hop's lease with `qumulo.cluster.replication_restore_lock`, passing back `upgrade_lock_token`, the token the calling play's take returned. A renewal that fails stops the run before the hop.

## Facts

Each host's `upgrade_moves`, `upgrade_reboots_only`, `upgrade_prepare_failed`, `upgrade_ready` and `upgrade_committed` are reset at each hop's start. `upgrade_held`, the hosts out of the hop's commits, is one list on every host: it starts each hop as the plan's `holds` of each host in `upgrade_lock_held` the hop moves, and grows after steps 1 to 4 and each commit step. `upgrade_stop` is one fact for the run, which every task of a later hop reads, and `cluster_upgrade_stopped_in` names the hop it stopped in; when a lock renewal stopped the run, `upgrade_stop_reason` says why, and the hop's report gives it as a line. The play's own end releases the locks and fails the run when `upgrade_stop` is set, naming the held hosts and `upgrade_stop_reason`; the role never ends the play.

## Variables

`meta/argument_specs.yml` is the reference.

| Variable | Default | Meaning |
|---|---|---|
| `cluster_upgrade_plan` | required | the plan |
| `upgrade_lock_hosts`, `upgrade_lock_held` | `[]` | the hosts that hold the run's locks, and the hosts a refused lock protects |
| `cluster_upgrade_artifact_dir` | beside the inventory source that defines the host, as edge_rebuild's | where the commit records and `upgrade-<UTC>.yml`, mode 0600 in a 0700 directory, are written |
| `cluster_upgrade_wait_timeout` | `3600` | each upgrade call's `wait_timeout` |
| `cluster_upgrade_reboot_timeout` | `43200` | each upgrade call's `reboot_timeout`, and step 1's reboot wait |
| `cluster_upgrade_unblock_timeout` | `14400` | step 1's preflight wait |
| `cluster_upgrade_peer_timeout` | `1800` | each portal wait of steps 6 and 8 |
| `cluster_upgrade_poll_delay` | `30` | seconds between step 1's reads, and between the portal reads of steps 6 and 8; below 30, step 1 reads the reboot status more often than FR-025a allows, so leave it at 30 outside tests |
| `cluster_upgrade_prepare_throttle` | unset | how many prepares run at once |

Under `--check` the role makes one `qumulo.cluster.upgrade` call per host, for its first hop, with `state: rebooted`, which predicts every phase and sends only reads; asks the window once; and writes the report, marked as a check run's. Nothing waits and no lock is renewed.
