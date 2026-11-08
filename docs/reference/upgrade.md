<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# The upgrade: variables, steps and files

Lookup tables for the upgrade playbook, `ansible-playbook qumulo.cluster.upgrade`: its inventory variables, the variables
for one run, the role variables, the images list, the steps of a run and of a hop, the report and the other files a run writes, and the
sources the upgrade supports.
To run an upgrade, see [Upgrade a fleet](../how-to/upgrade.md).
To recover from a stopped one, see [Recover an upgrade that stopped](../how-to/resume-an-upgrade.md).
For the reasons behind the hop rules, the ordering and the records, see [How upgrades work](../explanation/upgrades.md).
[Troubleshooting](troubleshooting.md#upgrades) quotes every refusal (F1 to F34) and warning (W1 to W12).

## Parts

| Part | Kind | Runs | Does |
|---|---|---|---|
| [`playbooks/upgrade.yml`](../../playbooks/upgrade.yml) | playbook | on `qumulo_clusters`, `strategy: linear`, `gather_facts: false`, `any_errors_fatal: false`, `timeout: 0` | Reads each cluster, scans the restore files, plans, takes the restore locks, calls the role, releases the locks. |
| [`upgrade`](../../plugins/modules/upgrade.py) | module | per cluster | Takes one cluster through one hop: verify, prepare, a readiness check, the commit and its reboots. Decides the outcome from the running build. |
| [`upgrade_plan`](../../plugins/modules/upgrade_plan.py) | module | on the controller, connects to nothing | Plans the fleet from every inventory host's reads and variables. Also answers whether a hop's commits may begin inside the maintenance window. |
| [`upgrade_report`](../../plugins/modules/upgrade_report.py) | module | on the controller, connects to nothing | Turns one hop's results into the hop's report. |
| [`replication_restore_info`](../../plugins/modules/replication_restore_info.py) | module | on the controller, connects to nothing | Lists the restore records, restore locks and unreadable files under one artifact directory, with their ages. The playbook runs it before the plan, for W1. |
| [`cluster_upgrade`](../../roles/cluster_upgrade/) | role | in a linear play without `serial` | Runs the plan's hops in lock-step. |
| [`redact_images`](../../plugins/filter/redact_images.yml), [`url_secret`](../../plugins/filter/url_secret.yml) | filters | on the controller | `redact_images` returns image URLs without their query string and user information, and `url_secret` returns those parts. |
| [`artifact_dir`](../../plugins/filter/artifact_dir.yml) | filter | on the controller | Gives the `artifacts` directory beside the inventory source that defines a host. |
| [`replication_restore_lock`](../../plugins/modules/replication_restore_lock.py) | module | on the controller | Takes, renews and releases a host's restore lock. |

`upgrade_plan`, `upgrade_report` and `replication_restore_info` are not in the `qumulo.cluster.qumulo` action group, so
`module_defaults` for that group do not reach them.
`ansible-doc qumulo.cluster.upgrade`, `ansible-doc qumulo.cluster.upgrade_plan`,
`ansible-doc qumulo.cluster.upgrade_report` and `ansible-doc -t role qumulo.cluster.cluster_upgrade` give the options of
each.

## Inventory variables

Each is one setting, not a key of one dict. A host's `host_vars` value replaces the group's value whole.

| Variable | Type | Default | Declares |
|---|---|---|---|
| `qumulo_upgrade_target` | string, quoted | unset | The release the host ends on. YAML reads an unquoted `7.10` as the number 7.1, and the plan then reads release 7.1. While any host of a run declares one, every host of the run must: the plan refuses a host without it (F14). A target below the host's running release is refused (F3). |
| `qumulo_upgrade_images` | list of strings or mappings | `[]` | The host's images, in release order, both kinds in one list. [The images list](#the-images-list) gives the entry forms. |
| `qumulo_upgrade_reboot` | string: `rolling` or `all` | `rolling` | How a platform upgrade reboots the hosts ([Reboots](#reboots)). `aggressive` is refused (F11). |
| `qumulo_upgrade_reboot_set_size` | integer, at least 1 | unset | How many nodes a rolling reboot takes at once. Unset, Qumulo Core sizes the groups. |
| `qumulo_upgrade_maintenance_windows` | list of mappings | unset | The time ranges inside which a hop's commits may begin ([The maintenance window](#the-maintenance-window)). |
| `qumulo_upgrade_window_timezone` | string, an IANA name | unset | The timezone of the ranges. Required with `qumulo_upgrade_maintenance_windows` (F34). |
| `qumulo_upgrade_after` | list of inventory host names | unset | Hosts whose units commit, in each hop, before this host's. It may not put a hub before one of its spokes, order two members of one portal component, close a cycle or name a host that is not in the inventory (F18). |
| `qumulo_upgrade_allow_limited_release` | boolean | `false` | Sends Qumulo Core's waiver of the rule that refuses a limited (letter) release as an upgrade source ([A limited release](#a-limited-release)). |
| `qumulo_upgrade_api_host` | string | `qumulo_api_host` | The address the playbook connects to in place of `qumulo_api_host` ([Connections](#connections)). |
| `qumulo_cluster_kind` | string: `cnq`, `edge` or `baremetal` | unset | Picks the host's images from a list that holds both kinds. The [Terraform inventory plugin](../../plugins/inventory/terraform.py) sets it. |

The plan also reads two variables of other areas: `qumulo_portal.hub`, which groups hosts into portal components, and
the `target` and `state` of each entry of `qumulo_replication_relationships`, which orders a relationship's target before
its source.
[Variables](variables.md) lists them with the connection variables.

## Variables for one run

Given with `-e`.

| Variable | Type | Default | Declares |
|---|---|---|---|
| `qumulo_upgrade_verify_in_check_mode` | boolean | `false` | In a check run, also runs verify, which downloads the whole image on every node and installs nothing. |
| `qumulo_upgrade_aggressive` | boolean | `false` | Chooses aggressive reboots for the run. `true` is refused (F11). |

## Role variables

The role's inputs and defaults are in [`meta/argument_specs.yml`](../../roles/cluster_upgrade/meta/argument_specs.yml).

Set by the playbook; do not set them.
An extra variable outranks the playbook's own value, so `-e upgrade_lock_hosts=[]` would skip every lock renewal.

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `cluster_upgrade_plan` | dict, required | | The output of `upgrade_plan`. The playbook passes it. |
| `upgrade_lock_hosts` | list of strings | `[]` | The hosts that hold one of the run's restore locks. Each renews its lock at each hop's start, outside check mode. |
| `upgrade_lock_token` | string | | On each host of `upgrade_lock_hosts`, the token its take of the lock returned. |
| `upgrade_lock_held` | list of strings | `[]` | The hosts a lock that was refused protects. In each hop, every host of this list that the hop moves holds the hosts its plan entry's `holds` names. |
| `upgrade_stop` | boolean | `false` | `true` runs no hop. The role sets it once a hop ends with a host held or out of the play. |

Set in the inventory or with `-e`:

| Variable | Type | Default | Meaning |
|---|---|---|---|
| `cluster_upgrade_artifact_dir` | string | the `artifacts` directory beside the inventory source that defines the host | Where the commit records and the report are written, on the controller. Refused empty. |
| `edge_rebuild_artifact_dir` | string | the same expression | Where the restore locks are. The `edge_rebuild` role's own variable. |
| `cluster_upgrade_wait_timeout` | seconds | `3600` | Each phase's wait in every `upgrade` call: the preflight, the tolerance, the prepare and the commit. |
| `cluster_upgrade_reboot_timeout` | seconds | `43200` | A reboot already under way before a hop, and the reboots at step 7. |
| `cluster_upgrade_unblock_timeout` | seconds | `14400` | Step 1's wait for the preflight to clear. After a platform upgrade's reboots, background work can keep the preflight blocked longer than this on a large file system (F5). |
| `cluster_upgrade_peer_timeout` | seconds | `1800` | Each portal wait of steps 6 and 8. A timeout is reported and never fails the hop. |
| `cluster_upgrade_poll_delay` | seconds | `30` | The delay between step 1's reads, and between the portal reads of steps 6 and 8. Each read of the reboot status asks every node for its boot identity; a value below 30 is for tests. |
| `cluster_upgrade_prepare_throttle` | integer | unset | How many prepares, and so how many clusters' image downloads, run at once. It bounds step 2 and step 3's second prepare, never the commits. Unset, they run as wide as `forks`. |

The plan sizes each hop's lease from the four timeouts and the prepare's request timeout ([Connections](#connections)),
and counts batches from `forks` and the throttle.

## The images list

`qumulo_upgrade_images` holds entries of two forms.

| Form | Gives |
|---|---|
| A URL or an absolute path whose file name is `qumulo_core_<version>.qimg` (on-premises) or `cloud_qumulo_core_<version>.qimg` (cloud) | The image. The plan reads the release and the kind from the file name. A URL is `http://` or `https://`. Anything else, such as a relative path or another URL scheme, is refused (F2). |
| A mapping `{version, image, kind, build}`, for an image under another name | See below. |

| Mapping key | Required | Value |
|---|---|---|
| `version` | yes | The release the image holds, as a quoted string such as `"7.10.0.3"`. A number is refused. |
| `image` | yes | A URL or an absolute path. |
| `kind` | yes | `on-prem` or `cloud`. |
| `build` | no | The build the cluster must end on, for the entry's own release. Only a mapping entry can declare one. A host whose target is that release and that already runs it on another build is refused (F4). A commit that ends on another build fails, after the commit, which cannot be undone. |

Rules the plan applies to the list:

- The whole list ascends by release. An on-premises and a cloud entry of one release may sit in either order, but a list
  that groups all of one kind before the other is out of order. The plan refuses a list out of order rather than
  sorting it (F4).
- No release appears twice for one kind (F4).
- A URL's query string and user information are passed to the module apart from the rest of the URL. The plan, the
  report and every message hold the URL without them. A path and a plain URL are printed in full.
- An image file name that names no release, and an entry that is not a string or a mapping, are refused before F4.

Which kind a host takes:

| Host | Takes |
|---|---|
| `qumulo_cluster_kind: cnq` | The `cloud` entries. |
| `qumulo_cluster_kind: edge` or `baremetal` | The `on-prem` entries. |
| No `qumulo_cluster_kind`, the entries in its range all of one kind | Those entries, whatever its protection. Qumulo Core's verify refuses an image of the wrong kind. |
| No `qumulo_cluster_kind`, both kinds in range, protection object-backed | The `cloud` entries. |
| No `qumulo_cluster_kind`, both kinds in range, any other protection | The `on-prem` entries. |
| No `qumulo_cluster_kind`, both kinds in range, protection not read | Refused (F15). |
| `qumulo_cluster_kind` of another value | Refused. |

A host with `qumulo_cluster_kind` whose next hop exists only as an entry of the other kind is refused (F10).

From the entries of its kind above its running release and at or below its target, the plan picks each hop as the
furthest entry the quarterly rule accepts:

- A hop out of quarter `x.y` lands on `x.(y+1).0` or one of its dot releases, `x.(y+1).0.w`, never on a later patch of
  that quarter. The next quarter is `x.(y+1)` by arithmetic, whatever the list names.
- A hop inside a quarter lands on a later release of that quarter.
- No hop before the last is a letter release.
- The last hop is the host's target.

With the list 7.9.0.5, 7.10.0.3 and 7.10.3 of [Upgrade a fleet](../how-to/upgrade.md):

| Running release | Target | Hops |
|---|---|---|
| 7.8.4 | 7.10.3 | 7.9.0.5, 7.10.0.3, 7.10.3 |
| 7.10.0.1 | 7.10.3 | 7.10.3, in the same hop as the hosts on 7.8.4 take their last |
| 7.8.4 | 7.10.0.3 | 7.9.0.5, 7.10.0.3, whatever the list holds above 7.10.0.3 |

A 7.9.3.1 entry between 7.9.0.5 and 7.10.0.3 adds no hop for a host on 7.8.4, because 7.10.0.3 accepts any 7.9
release. A break in a host's chain is refused before anything is sent, naming the host, the break and the entry to add
(F4).
[How upgrades work](../explanation/upgrades.md#fewest-hops-never-a-letter) gives the reasons for the rule.

## A run

`ansible-playbook qumulo.cluster.upgrade -i <inventory>` runs these on `qumulo_clusters`, in order:

1. **Reads.** Each cluster's `cluster_info` with the subsets `node`, `version`, `license`, `product`, `upgrade` and
   `portals`: UUID, release and build, license, product, portals, and the upgrade's status, preflight, reboot status and
   protection. A cluster whose read fails leaves the play, and the plan treats it as outside the run.
2. **The artifact directory.** A host whose `edge_rebuild_artifact_dir` or `cluster_upgrade_artifact_dir` is empty,
   as a host given with `-i` as a list is, fails.
3. **The commit records.** The record of every inventory host, if it has one, is read from its
   `cluster_upgrade_artifact_dir`.
4. **The plan.** `upgrade_plan`, once, from every inventory host and not only the play's: their reads,
   `qumulo_upgrade_*` variables, `qumulo_portal.hub`, `qumulo_replication_relationships` and commit records.
   Every refusal fails the run here, together, before anything is written. Every warning is printed. A `--limit` run
   plans its own hosts, and a limit that leaves out part of a portal component is refused (F13).
5. **The locks.** Each host in the plan's `lock_hosts` takes its restore lock ([Restore locks](#restore-locks)). Then the
   commit records the plan decided can go are removed: a record is removed only on a host still in the play, and only
   while its file still has the digest the plan read.
6. **The hops**, by the `cluster_upgrade` role, one after another, each on every host at once.
7. **The end.** Each lock is released however the hops ended. A run that stopped before its last hop then fails, naming
   the hop and the reason, and exits non-zero.

### The nine steps of a hop

| Step | Runs on | What happens | A host that fails |
|---|---|---|---|
| 1 | The hosts the hop moves and does not hold | Waits out a reboot still under way from an earlier hop or run, then waits for the upgrade's preflight to clear, up to `cluster_upgrade_unblock_timeout`. | Leaves the play; its unit is held. |
| 2 | The same | Every host prepares at once: verify, then the prepare, which installs the image beside the running build. | Stays in the play for step 3's second prepare, unless no second prepare can lift the refusal. |
| 3 | The hosts whose first prepare failed, then every host of step 2 | A host whose first prepare failed prepares once more. Then the `upgrade` module checks that each host may commit: the preflight, the image's release, and the tolerance its reboot needs. | Leaves the play; its unit is held. |
| 4 | Once, when a host of the hop declares a window | The hop's commits may begin only inside the maintenance window ([The maintenance window](#the-maintenance-window)). | Outside the window, every unit is held except the moving members of a split component the hop closes. |
| 5 | Each commit step's hosts that are ready and not held | The commits, step by step of the plan's schedule. Each host's readiness is checked again just before its own commit. | Leaves the play; its unit and every unit that follows it are held. |
| 6 | Each moved component's hub, or its spokes when the hub is outside the play; the sources of the replication relationships that touch a moved host | Each moved portal component's portals are read until `ACTIVE`, up to `cluster_upgrade_peer_timeout`. Each replication relationship that touches a moved host is read once, with no wait. | Never fails. A portal timeout, a read that fails, a relationship the source does not list, and a last job's error are each a report line. |
| 7 | The hosts that committed, and those the plan only reboots | Waits for a platform upgrade's reboots, up to `cluster_upgrade_reboot_timeout`. | Leaves the play. |
| 8 | As step 6 | Reads the portals and relationships again. | Never fails. |
| 9 | Once, also when the hop fails | Writes the report. | |

Step 6 does not wait on a component the hop left on two releases, on a component that stays split without closing,
or on one whose members took a rolling platform reboot; each gets a report line instead.
A plan with no hop, as when every cluster is at its target, runs none of this, and the report holds the plan alone.

At each hop's start, outside check mode, each host of `upgrade_lock_hosts` renews its restore lock to the hop's lease.
A renewal that fails stops the run before the hop.

### A check run

`ansible-playbook qumulo.cluster.upgrade -i <inventory> --check`:

| Part | In a check run |
|---|---|
| Reads | Sent. |
| The plan | Made in full, with every refusal and warning. |
| Restore locks | Judged, not written. A refusal is reported as a prediction with the hosts the run would hold, and a takeover is reported as one. |
| Commit records | Reported as the records the run would retire or drop, not removed. |
| First hop | One `upgrade` call per host with `state: rebooted` predicts each phase the hop would run and each wait it would make. Later hops come from the plan. |
| Verify | Not run, because it downloads the whole image on every node. `qumulo_upgrade_verify_in_check_mode=true` runs it. |
| The maintenance window | Asked once, when the first hop declares a window, whether the hop could commit now. |
| Waits and lock renewals | None. |
| The report | Written, marked as a check run's. |

### Facts the role sets

| Fact | Scope | Meaning |
|---|---|---|
| `upgrade_moves`, `upgrade_reboots_only`, `upgrade_prepare_failed`, `upgrade_ready`, `upgrade_committed` | each host, reset at each hop's start | Whether the hop moves the host, only reboots it, had its first prepare fail, found it ready, or saw it commit. |
| `upgrade_held` | every host | The hosts out of the hop's commits. |
| `upgrade_stop` | the run | `true` once the run must stop; every task of a later hop reads it. |
| `cluster_upgrade_stopped_in` | the run | The number of the hop it stopped in. |
| `upgrade_stop_reason` | the run | Why a failed lock renewal stopped the run. |

## Reboots

Verify reads whether a hop is a software-only upgrade or a platform upgrade, `SOFTWARE_AND_PLATFORM`, which also reboots
every host. The report gives each host's `upgrade_type`. A hop's type depends on its source, so one release can be either.

| Setting | Effect |
|---|---|
| `qumulo_upgrade_reboot` unset or `rolling` | The hosts reboot in groups, each once the cluster has reprotected after the last. |
| `qumulo_upgrade_reboot: all` | Every host reboots together, one longer outage. On a platform upgrade, Qumulo Core reboots only once the cluster has reprotected fully with every node armed, so the readiness checks first wait for the current node fault tolerance to equal the configured one, up to `cluster_upgrade_wait_timeout` (F23). |
| `qumulo_upgrade_reboot_set_size` unset | Qumulo Core sizes each group at the current node fault tolerance minus one, at least one. |
| `qumulo_upgrade_reboot_set_size` above the configured node fault tolerance | Refused before anything is sent (F20). |
| `qumulo_upgrade_reboot_set_size` above the current node fault tolerance | Waited for before the prepare and at each readiness check, up to `cluster_upgrade_wait_timeout` (F7). Qumulo Core checks the count once more after every node is armed, and a drop in the tolerance after the last check leaves every node armed and nothing restarted (G-UPGRADE-COUNT, in [Gaps](gaps.md)). |
| `qumulo_upgrade_reboot_set_size` below 1 | Refused. |
| `qumulo_upgrade_reboot_set_size` with `qumulo_upgrade_reboot: all` | Ignored, with a warning (W10). |
| Configured node fault tolerance 0, as on an unprotected edge or a Cloud Native Qumulo cluster of fewer than three nodes | Every host reboots at once whatever `qumulo_upgrade_reboot` says, with a warning where a declaration is ignored (W10). |
| `qumulo_upgrade_reboot: aggressive` in the inventory, or `qumulo_upgrade_aggressive=true` | Refused (F11). |

A reboot that was paused outside the run fails at once, at step 1 or step 7 (F25).
One that stopped on an error fails at once (F26).
One that outlasts `cluster_upgrade_reboot_timeout` fails (F24).

## The maintenance window

`qumulo_upgrade_maintenance_windows` is a list of ranges. A host that declares none may commit at any time.

| Range key | Type | Value |
|---|---|---|
| `start_hour`, `end_hour` | integer | 0 to 23. |
| `start_minute`, `end_minute` | integer | 0 to 59. |
| `on_days` | a string or a list of strings | `MON`, `TUE`, `WED`, `THU`, `FRI`, `SAT`, `SUN`, or `EVERY_DAY`. Names are not case-sensitive. |

A range whose end is at or before its start runs past midnight, and `on_days` names the day it starts.
A value outside these limits, a name that is not an IANA timezone, and ranges without a timezone are refused (F34).

| Case | What the run does |
|---|---|
| Whether a hop may commit | Checked once per hop, at step 4, before its first commit, against every host the hop moves that declares a window and is not held. The hop is inside only when every such host is inside one of its own ranges. |
| Inside the window | The hop commits all its rounds, the later ones past the window's end if the hop runs long. |
| Outside the window | Nothing is committed. The hosts stay prepared, the report gives when the window next opens, and the run ends after the hop with F9 and a non-zero exit. |
| Prepares | Run whenever the playbook runs, so a run started before the window stages the images and then stops with F9. A prepared upgrade is lost at any quorum change, and a presigned URL differs at the next run, so the run inside the window may prepare again. |
| A rerun outside the window | Commits only what closes a split: the moving members of a portal component whose members run two releases at the hop's start, when the hop brings every member to one release and the component follows no held unit. Each gets a report line. F9 holds everything else. |
| Hosts whose windows share no time | A warning (W11). |

## Connections

| Setting | Effect |
|---|---|
| Request timeout of verify and prepare | `upgrade` sends each with a request timeout of its own, 2700 seconds on a source of 7.10.3 or later and 4500 seconds below. The address the playbook connects to must hold an idle connection that long. |
| `qumulo_upgrade_api_host` | The address the playbook connects to in place of `qumulo_api_host`. A multi-AZ Cloud Native Qumulo cluster's `qumulo_api_host` is a network load balancer that closes a connection idle for 350 seconds by default; a node's address avoids that. |
| `forks` | Each prepare holds one fork while it waits. A hop of more clusters than `forks`, or a commit step of more hosts, runs in batches. The run warns when `forks`, or `cluster_upgrade_prepare_throttle` when lower, is below the hop's size or its largest commit step (W6). Set with `-f` or in `ansible.cfg`. |

## A limited release

A letter release, such as 7.10.3A, is refused as an upgrade source unless the rule is waived (F1).
`qumulo_upgrade_allow_limited_release: true` sends Qumulo Core's waiver on that host's first hop, with verify and the
prepare.

| Source release | Waiver sent | What it lifts | Requires |
|---|---|---|---|
| 7.11.0 or later | `compatibility_overrides=allow_upgrade_from_limited_release` | The limited-release rule alone. | Nothing more. |
| Below 7.11.0 | `override_compatibility_check=true` | Every compatibility rule Qumulo Core checks: a prohibited release, a limited release, a developer source, a source off a release branch, a downgrade, the upgrade window, a developer target build, a build not later than the running one, and the license check. | The first hop declares the exact `build` in a mapping entry (F16). The module verifies with the waiver first and prepares only when verify reads the declared release. Once the cluster runs the new build, another build fails at once. |

A mapping entry's `build` applies to every host whose hop lands on that entry.
For a build for one limited-release host, see [Upgrade from a limited release](../how-to/upgrade.md#upgrade-from-a-limited-release).
A letter release is never an intermediate hop, and may be a host's final target.

## Files the run writes

All paths are on the controller.
`<artifacts>` is `cluster_upgrade_artifact_dir`, and `<restore-artifacts>` is `edge_rebuild_artifact_dir`.
Both default to the `artifacts` directory beside the outermost inventory source on disk that defines the host.
A host given with `-i` as a list has none, and the run refuses it.

| File | Written by | Holds |
|---|---|---|
| `<artifacts>/upgrade-<UTC>.yml`, `<UTC>` as `20261104T170000Z` | The role, at the end of each hop | [The report](#the-report). |
| `<artifacts>/<host>/upgrade/commit.yml` | `upgrade` | [A commit record](#commit-records). |
| `<restore-artifacts>/<host>/restore/lock` | `replication_restore_lock` | [A restore lock](#restore-locks). |
| `<restore-artifacts>/<host>/restore/<64 hexadecimal digits>.yml` | The replication restore | An open restore record. |

The report and the commit record are mode 0600, in a directory of mode 0700.

### The report

The file is rewritten at the end of each hop, a failed hop included.
Its first line is a comment that says it was written by the role, and in a check run that nothing was changed.

| Key | Holds |
|---|---|
| `check_mode` | `true` for a check run. |
| `plan` | The plan, as `upgrade_plan` returns it. |
| `hops` | One report per hop run so far, as `upgrade_report` returns it. A run with no hop writes the plan alone, whose `skipped` lists the clusters already at their target. |

Each hop's report:

| Key | Holds |
|---|---|
| `hop` | The hop's `number`, `release`, `lease` in seconds, and the hosts it `moves`. |
| `check_mode_run` | Whether the results are a check run's predictions. |
| `hosts` | One entry per host, below. |
| `components` | One entry per portal component the hop moves: `members`, `moving`, `split`, `window` and `two_releases`. |
| `peers` | The readings of steps 6 and 8, as `commits` and `reboots` lists. |
| `window` | The maintenance window's answer, or `null`. |
| `warnings` | Each warning the hop's results gave, as `{host, message}`. |
| `notes` | The hop's notes from the plan. |
| `lines` | The hop's own lines: why the run stopped before it, the window's, the peer steps' and each component left on two releases. |

A host's entry:

| Key | Holds |
|---|---|
| `from`, `to` | `{version, build}` it ran and ends on. |
| `upgrade_type` | The upgrade's type. |
| `phases` | Each phase's `{started, ended}` in UTC. |
| `prepare_seconds` | The prepare's duration. |
| `commit_started` | The commit's start. |
| `downtime_seconds` | How long the REST API did not answer during the commit's restart. |
| `reboot` | `{mode, set_size, seconds}`. |
| `commit_sent`, `committed` | Whether the commit request was sent, and whether the commit is done. |
| `outcome` | One of the values below. |
| `message`, `maybe_applied`, `unfinished_commit` | With `failed`: the failure, whether the failed task may have been applied, and whether it was a commit that did not finish. |
| `held_by`, `lock_held` | With `held`: the host whose failure held it, and whether a restore lock the run could not take did. |
| `prediction` | With `predicted`: the module's prediction. |
| `lines` | Notes about the host. |

| `outcome` | Meaning |
|---|---|
| `rebooted` | The commit is done and a platform upgrade's reboots are done. |
| `committed` | The commit is done. |
| `at target` | The host already ran the hop's release and had no phase to run. |
| `failed` | A step failed. |
| `held` | Held out of the hop's commits: by a failed host's unit (`held_by`), by a restore lock the run could not take (`lock_held`), or otherwise by the run, as by a closed maintenance window. |
| `not committed` | Neither failed nor held, and its commit did not happen. |
| `predicted` | A check run's prediction. |

A component's `window` is `{started, ended, seconds, open, uncommitted}`, from its first member's commit start to its
last member's commit done, or `null` when no member's commit started.
A peer reading of a portal is `{component, host, active, seconds, line}`, with `active` `null` when the step skipped the
component. A reading of a replication relationship is `{source, target, error, line}`.

### The plan

The plan, in `plan` of the playbook's `upgrade_plan` result and of the report:

| Key | Holds |
|---|---|
| `hops` | The hops in order. Each has `number`, `release`, `lease`, `moves`, `components`, `steps`, `units`, `hosts`, `windows`, `notes` and `forks_warning`. A host's entry gives its `image_index`, `image_redacted`, `version`, `build`, `kind`, `reboot`, `unit`, `round`, `wave`, `step`, the hosts a failure of it `holds`, and `reboots_only` for a host on the target build still rebooting. |
| `lock_hosts` | Each host the run locks, with the hosts its lock protects: itself and its replication targets. |
| `records` | The commit records to `retire` and to `drop`, each `{host, path, digest, line}`. |
| `skipped` | The hosts already at their target. |
| `refusals` | Each `{host, code, message}`. |
| `warnings` | Each `{code, host, message}`. |
| `notes` | Each `{host, message}`. |

A hop's `lease`, in seconds, bounds how long the hop can take. The plan computes it from the four timeouts, the prepare's
request timeout ([Connections](#connections)), `forks` and the throttle, and each restore lock is renewed to it at the hop's start.
`forks_warning` is W6's text, or `null`, and is printed by the role.

### Commit records

The record holds one field per line, each value in JSON.

| Field | Holds |
|---|---|
| `format` | `1`. |
| `state` | `pending` or `unfinished`. |
| `cluster_uuid` | The cluster's UUID. |
| `build` | The build the cluster ran when the record was written. |
| `target_version` | The release being committed to. |
| `declared_build` | The mapping entry's `build`, or `null`. |
| `time` | When the record was written, in UTC. |
| `error_info` | With `unfinished`, the upgrade status's error text. |

| Event | Record |
|---|---|
| `upgrade` is about to send the commit | Written as `pending`, and flushed to disk first. |
| The commit is done, or refused | Removed. |
| The commit did not finish | Rewritten as `unfinished`. |
| A run is killed during the commit | Stays `pending`. |

The playbook gives every commit a record. A hand run of `upgrade` writes one only with `commit_record` set.

The next run's plan decides each record before anything is sent:

| Record found | Decision |
|---|---|
| Any state, and the host is another cluster now, as after a rebuild | Retired, with a report line. |
| `pending`, and the cluster runs another build at or above the target release | Retired. |
| `pending`, and the cluster reads `PREPARING` or `PREPARED` on the recorded build, which a commit never leaves | Dropped. |
| `pending`, and the cluster still commits or reboots on the recorded build, or its read failed | Refused: wait for it (F30). |
| Any other `pending` record, as when the cluster is back on the recorded build with nothing under way | Refused (F19). |
| `unfinished` | Refused (F19), until Qumulo Care has cleared the cluster and the record is removed. |
| A file that cannot be read as a record | Refused (F31). |

The records hold on one controller, or on an artifact directory every controller that runs the upgrade shares.
A run that moves `cluster_upgrade_artifact_dir` loses the records under the old directory, and with them the guard.

### Restore locks

The lock is the one a replication restore takes, with the holder `upgrade`.

| Property | Value |
|---|---|
| Path | `<restore-artifacts>/<host>/restore/lock` |
| Taken on | Each host the plan moves or reboots, and each host in the play whose declared replication targets one of them. |
| Taken | Once, after the plan, with a lease of the first hop's `lease`. |
| Renewed | At the start of each hop, outside check mode, to that hop's `lease`. |
| Released | At every end the run itself reaches: hops done, a hop that failed or stopped, a lost lock, or a host that failed after its take. |
| A lock another run holds | Fails that host with the lock's own message (F22). The host and every host it replicates to are held in every hop that moves them, and the run ends after the first hop that moves one of them. A check run reports it as a prediction. |
| A source outside the run | Cannot be locked by the run. The plan warns that a restore of it started meanwhile is not held off (W9). |

A run stopped with Ctrl-C, or killed, leaves its locks. A lock is taken over, with a warning:

| Lock taken on | Taken over when |
|---|---|
| This controller (same host name, boot ID and PID namespace) | The `ansible-playbook` process that took it is gone. A lock whose process is alive is never taken over. |
| Another controller, or one without `/proc` | Its lease has run out. |

For a hop the lease is many hours.
[Troubleshooting](troubleshooting.md#restore-locks) quotes the lock's warnings and refusals.

Before the plan, the playbook runs `replication_restore_info` once for each distinct `edge_rebuild_artifact_dir` of the
`qumulo_clusters` hosts, as the `edge_rebuild` role computes it, `artifacts` beside the host's inventory source by
default, and passes the records and locks it finds to `upgrade_plan` as `restore_files`.
It scans in check mode too, since it only reads.
The plan warns W1 for a host it moves or reboots, once for each record or lock that names the host:

| Found | Names |
|---|---|
| A restore record | Its edge, the `target_host` of its key, and every target of the edge's `present` replication relationships. |
| A `live` restore lock | Its host, and every target of that host's `present` replication relationships. |

A lock on a host the run locks itself is left to the lock step (F22).
A lock that is `gone` or `expired`, and a file the scan cannot read, are not W1: the next restore or upgrade of the host
takes a lock over with a warning, and a restore that reads an unreadable file stops on it, naming it, and so does
this run's lock step on an unreadable `lock` of a host it locks.
[Troubleshooting](troubleshooting.md#upgrades) quotes W1, and
[Find and clear restore records and locks](../how-to/restore-replica.md#find-and-clear-restore-records-and-locks) reads
the scan's result.

An open restore record is a file named by 64 hexadecimal digits and `.yml` in `<restore-artifacts>/<host>/restore/`.
A file named `done-` followed by the digits is a finished restore's stamp.

## Supported sources

The upgrade does not keep the collection's floor of Qumulo Core 7.10.3 ([Tested Qumulo Core versions](tested-versions.md)).
The upgrade modules, the role and the reads the playbook makes run on older sources, and the playbook prints no floor
warning.
Which sources the collection supports is what backwards testing finds.

| Lane | Oldest source upgraded from | Date of the run |
|---|---|---|
| Hardware, tier 3 | not yet measured | |
| Cloud Native Qumulo, tier 2 | not yet measured | |

Once a run has set the range, the plan warns about a source older than it (W5).
[How the lanes are tested](tested-versions.md#upgrades) says how the range is measured and what each lane cannot show.

## See also

- [Upgrade a fleet](../how-to/upgrade.md): declare, check, run, a canary, several quarters, a window.
- [Recover an upgrade that stopped](../how-to/resume-an-upgrade.md): read the report, rerun, a commit that did not
  finish, a paused reboot, a lock.
- [How upgrades work](../explanation/upgrades.md): hop rules, lock-step, ordering, and why the cluster records no commit.
- [Troubleshooting](troubleshooting.md#upgrades): every refusal and warning, with what lifts it.
- [Variables](variables.md): the inventory variables of every area.
- [Playbooks and roles](playbooks-and-roles.md): the playbook and role tables.
