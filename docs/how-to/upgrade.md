<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Upgrade a fleet

Bring every cluster that declares `qumulo_upgrade_target` to that release with the upgrade playbook, one quarterly
release at a time.
The playbook plans the whole fleet before it sends anything but reads, then runs each hop on every cluster before the
next hop starts, and writes a report after each hop.
When you finish, each cluster runs its target, and a second check run finds nothing left to move.
How the plan chooses hops and orders portals and replication is in [How upgrades work](../explanation/upgrades.md).

## Before you start

- Set up the inventory, with every cluster as a host in `qumulo_clusters`:
  [Set up an inventory for your clusters](set-up-inventory.md).
  The playbook connects to each cluster with the connection variables the inventory already holds.
- Declare every portal (`qumulo_portal`) and replication relationship (`qumulo_replication_relationships`) the fleet
  already has, on every host, so the plan can order the commits.
  A live portal the inventory does not declare is warned about, and its spoke is not ordered before its hub.
  [Pair an edge with its hub through a portal](pair-edge-with-hub.md) shows the declaration.
- Contact Qumulo Care before an upgrade across several releases.
- License any edge that runs without a license first, because Qumulo Core refuses to upgrade an edge whose license has
  expired, or that has none and was formed more than 30 days ago:
  [License an edge before an upgrade](licensing.md#license-an-edge-before-an-upgrade).
- Take a snapshot of the data that matters most before the first run:
  [Snapshot before a change](snapshot-before-a-change.md).
  The collection never downgrades a cluster.

## Upgrade across several quarters

1. Get each hop's images from Qumulo Care.
   For every quarter between the fleet's oldest release and the target, you need that quarter's quarterly upgrade
   build, such as 7.9.0.5 or 7.10.0.3, and the target's own image.
   Stage each image where every node can reach it, at a URL that stays the same between runs, or in each cluster's
   file system ([choose where an image lives](#choose-where-an-image-lives)).

2. Declare the target and the images in `group_vars/qumulo_clusters.yml`.
   Quote every version, because YAML reads `7.10` as a number, and list the images oldest first:

   ```yaml
   qumulo_upgrade_target: "7.10.3"
   qumulo_upgrade_images:
     - https://mirror.example.com/qumulo/qumulo_core_7.9.0.5.qimg
     - https://mirror.example.com/qumulo/qumulo_core_7.10.0.3.qimg
     - https://mirror.example.com/qumulo/qumulo_core_7.10.3.qimg
   ```

   Add each release's cloud image (`cloud_qumulo_core_<version>.qimg`) beside its on-premises one when the fleet
   holds Cloud Native Qumulo clusters.
   One list holds both kinds, and the plan gives each host the images of its kind.
   The entry forms, for an image under another name, are in [The images list](../reference/upgrade.md#the-images-list).

   To change a setting for one cluster, set it in that host's `host_vars`.
   A `qumulo_upgrade_*` variable is one setting, so a host's file changes it without dropping the group's others.
   A host that declares its own `qumulo_upgrade_images` replaces the group's list whole.

3. Look for an unfinished restore before the run.
   Open `<edge_rebuild_artifact_dir>/<host>/restore/` for each host the run moves and for each source that replicates
   to one.
   By default that is `artifacts/<host>/restore/` beside the inventory.
   A file named by 64 hexadecimal digits and `.yml` is a restore that has not finished.
   Finish that restore before the run, or remove the file if you reconnected the relationship by hand.
   [Find and clear restore records and locks](restore-replica.md#find-and-clear-restore-records-and-locks) lists them
   all at once.
   The plan, the check run of the next step included, warns of each open record that names a host it moves or
   reboots (W1), and goes on.

4. Check:

   ```sh
   ansible-playbook qumulo.cluster.upgrade -i inventory --check
   ```

   A check run sends only reads and changes nothing.
   It plans the whole run and prints every refusal and warning.
   Fix every refusal, and read each warning before you go on.
   [Troubleshooting](../reference/troubleshooting.md#upgrades) quotes each one with what lifts it.
   The plan is in the report, `artifacts/upgrade-<UTC>.yml` beside the inventory.
   It holds each hop's release, the clusters it moves, and each cluster's round, wave and step.
   A cluster already part of the way joins at the hop it needs.
   A list that leaves a quarter out is refused before anything is sent (F4), with the quarter and the clusters it strands.

   A check run does not verify the images, since verify downloads the whole image on every node.
   Add `-e qumulo_upgrade_verify_in_check_mode=true` to run verify too, which installs nothing.

5. Upgrade:

   ```sh
   ansible-playbook qumulo.cluster.upgrade -i inventory
   ```

   Each hop finishes on every cluster, its reboots and the preflight after them included, before the next hop
   starts.
   A platform upgrade's reboots, and the background work after them, can take hours on a large file system, and the
   slowest cluster sets each hop's pace.

6. Verify.
   Run the check again:

   ```sh
   ansible-playbook qumulo.cluster.upgrade -i inventory --check
   ```

   When every cluster runs its target, the run plans no hop.
   The new report holds the plan alone, and its `skipped` list names each cluster as at its target.

When a hop stops, the run ends before the next one and exits non-zero, naming the hop and the reason.
[Recover an upgrade that stopped](resume-an-upgrade.md) walks through the rerun.
To upgrade the rest of the fleet while one cluster waits, see
[Go on with the rest of the fleet](resume-an-upgrade.md#go-on-with-the-rest-of-the-fleet).

## Variants

### Try a canary first

The collection has no canary mechanism of its own.
Run the check and the upgrade on one cluster, then on the fleet:

```sh
ansible-playbook qumulo.cluster.upgrade -i inventory -l dr01 --check
ansible-playbook qumulo.cluster.upgrade -i inventory -l dr01
ansible-playbook qumulo.cluster.upgrade -i inventory --check
ansible-playbook qumulo.cluster.upgrade -i inventory
```

Pick a canary that belongs to no portal component and replicates to no other cluster.
A `--limit` that leaves out part of a portal component is refused (F13), and a source upgraded ahead of its replication
target holds new state back from it, which the run warns about (W12).
The fleet run skips the canary as at its target.

### Keep commits inside a maintenance window

Declare the windows and their timezone in the group's variables:

```yaml
qumulo_upgrade_maintenance_windows:
  - {start_hour: 22, start_minute: 0, end_hour: 4, end_minute: 0, on_days: [SAT, SUN]}
qumulo_upgrade_window_timezone: America/Los_Angeles
```

- `on_days` takes `MON` to `SUN` or `EVERY_DAY`.
  A range whose end is at or before its start runs past midnight, and `on_days` names the day it starts.
- `qumulo_upgrade_window_timezone` is required with the windows, as an IANA name (F34).
- A host that declares no window may commit at any time.

The window is checked once per hop, before its first commit.
Inside it, the hop commits all its rounds, and a long hop goes on past the window's end.
To keep a fleet's restarts inside its window, upgrade it in smaller runs with `-l`, each portal component whole and
replication targets before their sources.

Outside the window nothing is committed (F9), the clusters stay prepared, and the run ends after the hop with a
non-zero exit.
Start the run again inside the window.
The prepares run whenever the playbook runs, so a run started before the window downloads the images and may have to
prepare again inside it ([Maintenance windows](../explanation/upgrades.md#maintenance-windows)).
Use a stable URL or a path for such a run, as in [Choose where an image lives](#choose-where-an-image-lives).

### Choose how a platform upgrade reboots

Verify reads whether a hop is a software-only upgrade or a platform upgrade, which also reboots every host.
Only a platform upgrade reboots, so these settings change only a platform upgrade.
The plan still checks them on every hop, and refuses the values below on any upgrade.

- Leave `qumulo_upgrade_reboot` unset or `rolling` to reboot the hosts in groups.
  Leave `qumulo_upgrade_reboot_set_size` unset: Qumulo Core then sizes each group itself.
  A declared size above the configured node fault tolerance is refused before anything is sent (F20).
  A declared size above the current tolerance is waited for.
  With any declared size, a drop in the tolerance after the last check can leave every node armed and nothing restarted
  (G-UPGRADE-COUNT, in [Gaps](../reference/gaps.md)), so leave it unset.
- Set `qumulo_upgrade_reboot: all` to reboot every host together, which is one longer outage.
  On a platform upgrade, the run first waits for the cluster to reprotect fully, up to `cluster_upgrade_wait_timeout`.
- Do not set `qumulo_upgrade_reboot: aggressive` or pass `-e qumulo_upgrade_aggressive=true`.
  Both are refused (F11).

```yaml
# host_vars/cluster01.yml
qumulo_upgrade_reboot: all
```

A cluster whose configured node fault tolerance is 0, such as an unprotected edge, reboots every host at once whatever
you declare, and the plan warns where it ignores a declaration.
Why a set size is checked late, and what rolling costs, is in
[Rolling reboots, and all at once](../explanation/upgrades.md#rolling-reboots-and-all-at-once).

### Choose where an image lives

An image is an `http://` or `https://` URL, or an absolute path that each node reads from its own disk or else from
the cluster's file system.

- **A URL** needs no copy into the cluster, but every node downloads the whole image twice per hop, and three times on a
  source below 7.10.3.
  A four-node cluster moves about 30 GB per hop.
  Check that every node reaches the URL from its own network.
  The image server serves up to `forks` clusters' nodes at once, and `cluster_upgrade_prepare_throttle` bounds how many
  clusters prepare at a time:

  ```yaml
  cluster_upgrade_prepare_throttle: 2
  ```

  Prepares then run in batches of that size, which lengthens the hop, and the run warns about it (W6).

- **A path in the cluster's file system** is how Qumulo's own procedure stages an image.
  Copy the image into the cluster over NFS or SMB, and give its absolute path.

A URL's query string and user information, such as a presigned URL's signature, are never printed.
A presigned URL changes on every run, so a cluster an earlier run left prepared no longer matches it and prepares
again, with its downloads.
Use a stable URL or a path when an upgrade spans several runs.

### Upgrade a cluster behind a load balancer or a NAT gateway

Verify and the prepare answer only after every node has downloaded and checked the image, so the playbook holds one
request open for up to 45 minutes on a source of 7.10.3 or later, and up to 75 minutes below.
An address that closes an idle connection sooner cuts the run.
A multi-AZ Cloud Native Qumulo cluster's `qumulo_api_host` is an AWS network load balancer, which closes a connection
idle for 350 seconds by default.
Give such a host a node's address for this playbook alone:

```yaml
# host_vars/cluster01.yml
qumulo_upgrade_api_host: 10.0.1.21
```

The playbook connects to `qumulo_upgrade_api_host` in place of `qumulo_api_host`.
A NAT gateway or a firewall between the controller and the cluster can close an idle connection the same way.

A Cloud Native Qumulo cluster otherwise upgrades like any other, with the cloud image.
The Terraform provider's `cluster_version` is the version it deployed, and an upgrade does not change it.

### Commit one cluster after another

Name the clusters a host's commit follows in that host's `host_vars`:

```yaml
# host_vars/dr01.yml
qumulo_upgrade_after: [hub01]
```

It may not put a hub before one of its spokes, order two members of one portal component, close a cycle, or name a host
outside the inventory (F18).
Why the plan orders commits as it does is in [Ordering by hand](../explanation/upgrades.md#ordering-by-hand).

### Set forks for the number of clusters

Each prepare holds one of Ansible's forks while it waits.
A hop of more clusters than `forks` prepares in batches, and a commit step of more hosts than `forks` commits in
batches, which lengthens the hop.
Set `forks` to at least the number of clusters in the run, with `-f` on the command line or in `ansible.cfg`:

```sh
ansible-playbook qumulo.cluster.upgrade -i inventory -f 12
```

The run warns when `forks` is lower (W6).

### Raise a timeout

The role's waits are variables you set in the inventory or with `-e`:

```yaml
cluster_upgrade_unblock_timeout: 28800
```

After a platform upgrade's reboots, background work can keep the next hop's preflight blocked for longer than the
default four hours on a large file system, and the hop stops (F5).
Raise `cluster_upgrade_unblock_timeout`, or run the upgrade again, which picks up where it stopped.
[Role variables](../reference/upgrade.md#role-variables) lists each timeout, its default and what it bounds.

### Upgrade from a limited release

A letter release, such as 7.10.3A, is a limited build that Qumulo Care gives a few customers.
Qumulo Core refuses it as an upgrade source unless the rule is waived.
Set `qumulo_upgrade_allow_limited_release: true` only when Qumulo Care directs it, and only for that host.

Below 7.11.0 the collection sends the waiver only when the host's first hop declares the exact build Qumulo Care names
(F16).
There the waiver lifts every compatibility rule Qumulo Core checks, not only this one, so the declared build is what
keeps the hop to the release Qumulo Care named.
Declare it in a mapping entry of a `qumulo_upgrade_images` list in that host's own `host_vars`, and do not put the build
in the group's list:

```yaml
# host_vars/edge01.yml
qumulo_upgrade_allow_limited_release: true
qumulo_upgrade_images:
  - version: "7.10.0.3"
    kind: on-prem
    build: "<the build Qumulo Care names>"
    image: https://mirror.example.com/qumulo/qumulo_core_7.10.0.3.qimg
  - https://mirror.example.com/qumulo/qumulo_core_7.10.3.qimg
```

A letter release is never an intermediate hop, and may be a host's final target.
What the waiver lifts at each source version is in [A limited release](../reference/upgrade.md#a-limited-release).

## See also

- [Recover an upgrade that stopped](resume-an-upgrade.md): read the report, rerun, and handle a commit that did not
  finish or a paused reboot.
- [The upgrade: variables, steps and files](../reference/upgrade.md): every variable, the nine steps of a hop, the
  report and the files the run writes.
- [Troubleshooting](../reference/troubleshooting.md#upgrades): each refusal and warning, with what lifts it.
- [How upgrades work](../explanation/upgrades.md): hop rules, lock-step, ordering and why the cluster records no
  commit.
