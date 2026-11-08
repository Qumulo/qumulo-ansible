<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# How upgrades work

The upgrade playbook brings every cluster that declares a target to that release, across as many quarterly releases as
that takes, in an order that keeps portal components and replication pairs within the releases they tolerate.
This page explains the model behind it: why the whole fleet is planned before anything is sent, how a cluster's hops are
chosen, why every hop finishes on every cluster before the next starts, what decides the order of commits inside a
hop, why a commit leaves a record on the controller, and why the collection refuses to do several things that look
convenient.

To run an upgrade, see [Upgrade a fleet](../how-to/upgrade.md). To recover from a stop, see
[Recover an upgrade that stopped](../how-to/resume-an-upgrade.md). The variables, steps and files are in
[The upgrade: variables, steps and files](../reference/upgrade.md), and the refusal and warning messages are in
[Troubleshooting](../reference/troubleshooting.md#upgrades).

## One hop, one plan, one fleet run

The work is split into four pieces because each one answers a different question.

- The [upgrade](../../plugins/modules/upgrade.py) module takes one cluster through one hop: verify, prepare, a
  readiness check, the commit and its reboots. It knows nothing about any other cluster.
- [upgrade_plan](../../plugins/modules/upgrade_plan.py) decides what the fleet does. It runs on the controller and
  connects to nothing.
- [upgrade_report](../../plugins/modules/upgrade_report.py) turns one hop's results into the report.
- The [cluster_upgrade](../../roles/cluster_upgrade/) role runs the plan's hops, and
  [upgrade.yml](../../playbooks/upgrade.yml) reads the clusters, plans, takes the run's locks and calls the role.

A module that took one cluster through one hop could be called in a loop, and a loop is not enough. Whether one cluster
may commit depends on its portal peers, on the clusters it replicates to, and on where every other cluster in the fleet
will be after the same hop. Those facts live in the inventory and in other clusters' reads, and no single module call
sees them. So the plan reads everything first, from every inventory host and not only the hosts of the play, and decides
the hops, the order and every reason to stop before the first write. The plan and the module share one wording for the
refusals both can give, so a hand run of the module and a fleet run say the same thing. The role passes the inventory
host's name as the module's `host_name`, because a cluster is usually reached through an address. The plan gives the
warning that a reboot declaration is ignored once per host, and the module stays quiet about it. [The architecture
page](architecture.md#the-pieces-of-an-upgrade) shows how this fits the collection's layers.

The plan refuses whole. Any refusal stops the run before anything but reads has gone to a cluster, and there is no mode
that plans the part of the fleet that is fine. The guarantees the plan gives, that a hub does not commit ahead of its
spokes and that a replication target does not commit behind its source, hold only over the hosts it sees.
A plan that dropped the hosts it could not place would quietly drop those guarantees with them. Running part of a fleet
is something you ask for with `--limit`, and the plan then checks that the limit does not split a portal component.

## How a cluster reaches its target

### The rule the hops come from

Qumulo Core accepts an upgrade only from some releases. A quarterly release, `x.y.0` or one of its dot releases
`x.y.0.w`, accepts every release of the previous quarter. Any later release accepts only the earlier releases of its own
quarter. Any build accepts a later build of its own main release. So a cluster several quarters behind cannot go
straight to the target: it goes through a quarterly release of each quarter on the way, and a patch of a later quarter
is not a stepping stone to anything outside its own quarter.

The collection cannot read this rule from the cluster ahead of time (G-UPGRADE-PATH in the
[gap register](../reference/gaps.md)). Whether a later hop is accepted
cannot be asked before the cluster has made the hop before it, and no build's accepted range is published beside its
image. The plan therefore applies the documented rule to the whole images list in advance and refuses a list that breaks
it, naming the host, the break and the entry to add. Each hop's verify remains the server's own check, and the module
compares the release verify reads in the image with the one the list gave it.

The plan counts quarters by arithmetic. The next quarter after `x.y` is `x.(y+1)`, whatever the fleet runs and whatever
the list names, so a list that skips a quarter that nobody happens to run is still refused. Only at the end of a major
release, where arithmetic cannot know how many quarters there were, does the plan look at the releases the fleet names
and take the first quarter of the next major; the server checks that hop again at verify.

### Fewest hops, never a letter

Each host takes its own hops to its own target. From its running release the plan looks at the entries of its kind above
that release and at or below its target, and from each position hops to the furthest of them that the rule accepts.
Every hop costs an outage at the commit and a round of image downloads, so fewer is better.

- A hop out of quarter `x.y` can land on `x.(y+1).0` or one of its dot releases. It cannot land on a later patch of that
  quarter, which accepts only releases of its own quarter.
- A hop inside a quarter lands on a later release of the same quarter.
- No hop before the last is a letter release such as `7.10.3A`. A letter release is a limited build, and Qumulo Core
  refuses it as an upgrade source unless a waiver is sent, so the next hop would be refused. A letter release may be
  the final target.

A fleet is not a set of independent paths. The fleet's hops are the sorted releases the hosts' paths use, and a host
that does not move in a hop waits and takes its next hop in a later one. A cluster that is already part of the way
joins at the hop it needs. A host whose target is below the group's last release stops there whatever the group's list
holds above it. The plan never downgrades: a host that runs a release above its target is refused.

### Why a list out of order is refused

The images list is in release order, each kind ascending, with no release twice for one kind. The plan could sort it,
and does not. A list out of order is much more often a wrong version or a wrong file name than a wish for a different
order, and sorting would turn that mistake into an upgrade to the wrong release. A refusal costs one edit.

One list holds both kinds of image, on-premises and cloud. A host takes the kind its `qumulo_cluster_kind` names, or
else the kind its protection type implies: object-backed means cloud, erasure-coded means on-premises. Only a host whose
protection could not be read, with both kinds in the list, needs the kind declared. A mapping entry carries its `kind`
explicitly, because an image kept under a name of the operator's choice cannot show it. Where the list holds only one
kind, a host whose kind comes from its protection takes that kind as given. Qumulo Core's verify refuses a wrong kind
before anything is installed, so such a host is stopped, not damaged.

### Where the images come from

Qumulo Care provides each hop's images. Qumulo's upgrade mode reference marks one build of each quarter as that
quarter's quarterly upgrade, such as 7.9.0.5 or 7.10.0.3. That is the build to list for a quarter the fleet only passes
through. The collection never reads Qumulo's release tree and does not decide which build is the quarterly one. Qumulo
asks customers to contact Qumulo Care before an upgrade across several releases, and Qumulo Care names the builds then.

### What a URL costs, and why only part of it is secret

A node reads the image from a URL or a path, and the choice is a trade. A URL saves copying the image into the cluster
and costs bandwidth: every node downloads the whole image once to verify it and once to prepare it, and three times on a
source below 7.10.3, so a four-node cluster moves about 30 GB per hop and the image server meets every node of every
cluster that prepares at once. A path in the cluster's file system is how Qumulo's own procedure stages an image. It
costs a copy of each image into the cluster, and no node reaches outside the cluster for it.

A URL image is the only secret in the images list, and only part of it. The image itself is a release file, never a
credential, so a plain URL or a path is shown in full in the result and in every message: an operator reading a message
has to be able to tell which image it is about. The query string and the user information of a URL, which is where a
presigned signature or a password lives, are the secret. The playbook passes every host's `qumulo_upgrade_images`
through [redact_images](../../plugins/filter/redact_images.yml), so the plan and its report never hold a secret, and
each call to the module gets the secret parts apart from the rest, through
[url_secret](../../plugins/filter/url_secret.yml). The module joins them back inside itself and never prints them.

This has one cost. A presigned URL differs on every run, and a cluster that an earlier run left PREPARED with the old
signature no longer matches it, so it is prepared again, with its downloads. An upgrade that spans several runs should
use a stable URL or a path.

## Hops in lock-step

Every host's hop finishes, with its reboots and the portal and replication readings that follow, before any host's next
hop starts. Two reasons make this the rule rather than letting each host run ahead.

The first is replication. Replication works only between clusters within about eight quarters of each other, and a
newer source can hold new state back from an older target. Moving every host one hop at a time keeps any two clusters
within the larger of their starting gap and one quarter. The second is
that the plan stays true. It was built from reads taken before the first hop; a host that ran
ahead would change facts the later hops rest on, such as which clusters share a release.

The cost is that the slowest cluster of each hop sets the pace of the whole fleet at every hop. A platform upgrade's
reboots, and the background work that holds the next preflight blocked afterward, can take hours on a large file system.
The collection accepts this price because the alternative is a plan that cannot say what any two clusters are doing at
the same time.

### Why the play is linear

The role runs only in a linear play without `serial`, and its first task refuses a play that runs in batches. The hops'
barriers, the waves of a portal component and the way a failed host's unit is held across the whole play all depend on
every host being at the same task at the same time. With `serial`, a host of a component could be several tasks ahead of
its spoke. The role also runs its hosts with a local connection, because each module call writes its commit record on
the controller.

Two pacing options exist for what lock-step puts on shared resources. The role reads a cluster's reboot status
every `cluster_upgrade_poll_delay` seconds while step 1 waits out a reboot, and each read asks every node for its boot
identity. The default of 30 seconds is the shortest interval meant for use outside tests, since a shorter one loads the
cluster the role is waiting for. Outside a reboot, the wait for a hop to unblock reads only the upgrade preflight, which
costs the cluster little. The tolerances a rolling reboot needs are read on every cluster type, object-backed ones
included, because Qumulo Core's reboot manager checks the remaining node failures on every cluster type.
`cluster_upgrade_prepare_throttle` bounds how many prepares, and so how many clusters' image downloads, run at once,
for an image server that cannot serve every cluster together. It limits the prepares only, never the commits, and
without it the prepares run as wide as `forks`.

### What a failure stops

A host that fails a step leaves the play. It also holds its unit: its whole portal component, and every unit that
follows it by `qumulo_upgrade_after` or by a kept replication edge. The commits already sent in its wave finish, and the
rest of the hop goes on. Then the run stops before the next hop, even for units with no link to the failure, and exits
non-zero.

A failure holds whatever follows it so that a source never commits ahead of a target that stayed behind. The run stops
before the next hop because the plan was built on every cluster taking every hop; a fleet that went on would have
clusters in a state that no part of the plan described. The failed cluster's portal peers may be on two releases. A
portal connects only clusters on the same release, so leaving a component split is not a state to build on. The report
names every component a hop left on two releases. The run's job is to stop in a state the next run can pick up from, not
to go as far as it can.

A rerun starts from where the clusters are. A cluster on its target with its reboots done is skipped, a cluster that is
PREPARED with the hop's image, reboot settings and waivers is not prepared again, and a component the failure left
split is brought to one release in the next hop. [Recover an upgrade that stopped](../how-to/resume-an-upgrade.md)
walks through a rerun.

## The order of commits inside a hop

### Portal components

A portal connects only clusters on one release. From a portal component's first commit to its last, its portals are
down, and a spoke serves nothing from its portal roots. The window in which the component is on two releases is the
cost, and the plan's ordering inside a hop exists to make it as short as the commits allow.

A hub and every cluster that reaches it through `qumulo_portal.hub`, its spokes and theirs, form one portal component,
read from every inventory host. A component commits in waves. Every member with no moving spokes of its own commits
first, together, then each member whose spokes have all committed, up to the hub. For a hub whose spokes are all edges,
that is every spoke at once and then the hub.

Spokes commit before their hub because Qumulo's own upgrade procedure for portals upgrades the spoke clusters first.
Qumulo Core itself enforces neither order. Nothing in a commit flushes a spoke's portal writes first. A spoke's
committed writes stay on its disks while the component is on two releases, and flush to the hub once the hub matches it.
Every member's prepare is finished before the first commit, so the mismatch window holds only the commits and their
readiness checks.

The plan refuses a hop that would leave two members of a component on different releases when they started the hop on
one (F12), and it refuses a component with a member outside the run (F13), for the same reason: a component that is only
partly moved is a component that is split. A live portal that the inventory does not declare is warned about rather than
refused, since the plan cannot place it; its spoke is not ordered before its hub.

### Replication is a preference

A relationship's target commits before its source where the plan can arrange it, because a newer source can hold new
kinds of state back from an older target until each file changes again. This is a preference and never a refusal. Of two
clusters that replicate to each other, one order is kept and the other is dropped with a note. A warning, not a
refusal, tells you when a declared relationship's ends would be more than eight quarters apart before the run or after
any hop (W3). The plan cannot tell whether that is what you want; it can say that Qumulo Core's range is exceeded.
[Replication](replication.md) explains what a relationship carries.

### Ordering by hand

`qumulo_upgrade_after` puts a unit after named hosts, for an order the plan cannot infer from the inventory. It may not
put a hub before one of its spokes, order two members of one component, close a cycle or name a host that is not in the
inventory. Where the declaration disagrees with a replication preference, the declaration wins, with a note. A hard
order is yours, and a preference is the plan's.

### Rounds, waves and the schedule

Within a hop the plan cuts the commits into units, a portal component or one cluster that is in none, and runs them in
rounds. Every unit with nothing to wait for commits in round 1, and every later unit in the round after the last unit it
follows. Within a round the components advance wave by wave together, and the round's standalone clusters commit after
the last component wave, so no standalone commit falls inside a component's window. Components that share a round share
each wave's slowest member, readiness waits included.

The result is that a hop takes about as many commit times as its longest chain of waves, not one per cluster. A hub
with many edge spokes commits its spokes together and then itself, two commit times however many spokes there are.

## What clients see

The structure of a hop follows from what each phase does to the clusters.

- Reads, the plan, verify and the prepare change nothing a client can see. A URL image's downloads load the network and
  the image server.
- The commit restarts the file system on every node of the cluster at once. Clients see an outage until the new build's
  quorum forms. It is one outage per cluster per hop, and the report gives each host's `downtime_seconds`, how long the
  REST API did not answer.
- A software-only upgrade is done at that point. Verify tells the upgrade's type, and a hop's type depends on its
  source, so the same release can be either.
- A platform upgrade also reboots the hosts.

### Rolling reboots, and all at once

A rolling reboot takes the hosts in groups, each once the cluster has reprotected after the last. The cluster serves
throughout, at a lower fault tolerance than configured, and the whole reboot is slow because it waits for reprotect
between groups. Rebooting all at once (`all`) takes every host together and costs one longer outage. Rolling is the
default.

A cluster whose configured node fault tolerance is 0, such as an unprotected edge or a CNQ cluster of fewer than
three nodes, has no tolerance to roll with, so it reboots every host at once whatever `qumulo_upgrade_reboot` says. The
plan warns where a declaration is ignored (W10).

Qumulo Core sizes each rolling group at the current tolerance minus one, and at least one. This is the choice the
collection recommends, and `qumulo_upgrade_reboot_set_size` is best left unset, for the reason G-UPGRADE-COUNT records:
Qumulo Core checks a declared count only after every node has downloaded the image, and again after every node is armed
to start the new build. A count above the configured tolerance is refused at plan time, and one above the current
tolerance is waited for before the prepare and at each readiness check. A drop in the tolerance after the last of
those checks still gets through, and then Qumulo Core refuses at the commit's own check, which leaves every node armed
and nothing restarted. Only an unset count avoids the state, because the server then makes no check of its own.
With `all`, Qumulo Core reboots only once the cluster has reprotected fully with every node armed, so the readiness
checks first wait for the current tolerance to equal the configured one.

Aggressive reboots would start each group as soon as the cluster has a full quorum, instead of waiting for reprotect.
The rolling reboot always waits for reprotect between groups, and no public route names the nodes still to reboot, so
the aggressive form has to pause the server's reboot and reboot the rest in sets itself (G-UPGRADE-REBOOT). The
collection holds it back: the plan refuses `qumulo_upgrade_aggressive=true` until aggressive reboots have run live on a
cluster they apply to, one that is erasure-coded and has a configured tolerance of at least 2.

A reboot can also be paused by hand. The upgrade never pauses one and cannot tell why it was paused, so a run that finds
a paused reboot fails at once rather than resume it on a guess. A reboot paused on an error is not resumed at all: it
goes to Qumulo Care. [Resume a paused reboot](../how-to/resume-an-upgrade.md#resume-a-paused-reboot) has the steps.

## Maintenance windows

`qumulo_upgrade_maintenance_windows` is the collection's own list of ranges inside which a hop's commits may begin;
Qumulo Core is not asked about it. It bounds when the commit may begin, because the commit is
the step clients see.

The window is asked once per hop, before the first commit, about every host the hop would commit that declares one.
Inside, the hop commits all its rounds, and the later ones may pass the window's end if the hop runs long. A check per
commit would stop a hop in the middle, with some of a portal component committed and the rest not, which is the split
the plan exists to avoid. A fleet that must stay inside its window upgrades in smaller runs with `--limit`, each
portal component whole.

Outside the window nothing is committed: the hosts stay prepared, the report says when the window next opens, and the
run ends after the hop with a refusal and a non-zero exit. The prepares run whenever the playbook runs, so a run begun
before the window stages the images and then stops. This costs something. A prepared upgrade is lost at any quorum
change, and a presigned URL differs at the next run, so a run inside the window may prepare again with its downloads. A
prepare changes nothing a client sees, so it is not held.

One thing does commit outside a window. A rerun commits what closes a split: the moving members of a portal component
whose members run two releases at the hop's start, as an earlier run's failed commit leaves them, when the hop brings
every member to one release and the component follows no held unit. Everything else is held. A component stuck on two
releases is the worse state, and the window's purpose is served by closing it.

## Slow answers and idle connections

Verify and prepare answer only once every node has downloaded and checked the image, so Qumulo Core's answer arrives
after the longest download in the cluster, with the upgrade status reading IDLE meanwhile (QUIRK-093 in the [quirk
register](../reference/quirks.md)). The module therefore sends these two requests with a timeout of their own, 2700
seconds on a source of 7.10.3 or later and 4500 seconds below, where verify downloads the image once more.

The address the playbook connects to has to hold an idle connection for that long, and many do not. A multi-AZ CNQ
cluster's `qumulo_api_host` is an AWS network load balancer, which closes a connection idle for 350 seconds by default,
and a NAT gateway or a firewall can do the same. A connection closed before the answer loses it. The module cannot then
resend: it reads the build and the status back and decides from those, never sending verify, prepare or commit again
after a lost answer (G-UPGRADE-ONESHOT). The one exception is a commit found still PREPARED while the REST API answered
throughout, which is sent once more because nothing happened. `qumulo_upgrade_api_host` lets a host be reached at a
node's address for the upgrade while the rest of the inventory keeps the load balancer.

A prepare also holds one of Ansible's forks while it waits. A hop with more clusters than `forks` prepares in batches,
and a commit step with more hosts than `forks` commits in batches, which lengthens the hop and every component's
mismatch window. The run warns when `forks` is below the hop's size (W6).

## Limited releases

A letter release is a limited build that Qumulo Care gives a few customers. Qumulo Core refuses it as an upgrade source
unless the rule is waived (G-UPGRADE-LETTER). `qumulo_upgrade_allow_limited_release` sends the waiver on that host's
first hop, and only there, because a letter release is never an intermediate hop. It is a support-directed action: the
collection has no way to know that waiving the rule is safe for a given cluster, and Qumulo Care does.

What the waiver lifts depends on the source. On a source of 7.11.0 or later it lifts the limited-release rule alone. On
a source below 7.11.0 the only waiver Qumulo Core has lifts every compatibility rule it checks, the upgrade window and
the license check among them. The collection sends that one only when the host's first hop declares the exact build
Qumulo Care names (F16), verifies with it first, and prepares only when verify reads the declared release.
Once the cluster runs the new build, any other build fails at once, because a commit cannot be undone and the cluster
now runs something other than what was agreed.

The build is declared in the host's own images list and not in the group's. A build in the group's list would refuse
every host already on that release with another build.

## A commit that cannot be undone

A commit cannot be cancelled, and a prepared upgrade is abandoned only by not committing it or by preparing another
(G-UPGRADE-ONESHOT). That fact shapes more of the module than any other. A lost answer is read back from the build and
the status, and nothing is sent again, except a commit found still PREPARED while the REST API answered throughout,
which is sent once more. A cluster that already runs the target release, and the declared build, is left alone. And once
the cluster runs a new build of the version, a different build fails at once.

### Why the cluster's own status is not enough

The commit restarts every node of the cluster at once, and a quorum change in the middle of it can leave the cluster on
its old build with some nodes armed to start the new build at their next restart. Nothing on the cluster records that
(G-UPGRADE-RECORD). After a quorum change the upgrade status reads IDLE, as it does after a commit that finished.

So the module decides the commit's outcome from the running build and never from an IDLE status. A commit that did not
finish fails at once, and the module sends nothing more. A cluster in that state goes to Qumulo Care before anything
else runs on it. A prepare rewrites the image an armed node would boot, and a restart of an armed node starts it on the
new build, so even the usual next step, running the upgrade again, can do harm.

### The record on the controller

Because the cluster cannot say, the controller does. The module writes a record at
`<cluster_upgrade_artifact_dir>/<host>/upgrade/commit.yml`, mode 0600, and flushes it to disk before it sends the
commit. It removes the record once the commit is done or refused, and marks it `unfinished` when the commit did not
finish. A run killed meanwhile leaves it `pending`. The write comes first so that a crash between the write and the
request leaves a record for a commit that was never sent, which is the cheaper mistake: the cluster still reads PREPARED
on the recorded build, which a commit never leaves, and the next plan drops the record.

The two states mean different things. Either record goes by itself when its host is now another cluster, as after a
rebuild. `pending` says that a commit was about to be sent and nobody knows what came of it, and the next plan settles
that from what the cluster reads. The record goes by itself when the host runs a new build of the target or a later
release, or reads PREPARING or PREPARED on the recorded build, which a commit never leaves. The plan asks you to wait
while the cluster still commits or reboots on the recorded build. A cluster that is back on its recorded build with
nothing under way stops the plan, since that is the signature of a commit that did not finish. `unfinished` says the
module saw it fail, and never clears itself while the host is the same cluster: the cluster's version is one node's
answer, and armed nodes can boot the new build while the cluster is mixed, so no reading of the cluster is good evidence
that it has recovered. Only Qumulo Care's word does, and removing the file is how you give it. A file that the plan
cannot read as a record stops the run as well, rather than being ignored.

The playbook keeps a record for every commit. A hand run of the module keeps one only when `commit_record` is set. The
records hold only on one controller, or on an artifact directory that every controller running the upgrade shares. A
run that moves `cluster_upgrade_artifact_dir` loses the records under the old directory and the protection with them.
That is a limit of the design, not an oversight: a guard that lives on the controller is only as wide as the
controller.

[A commit that did not finish](../how-to/resume-an-upgrade.md#a-commit-that-did-not-finish) says what to do when one
stands.

## Restore locks

A replication restore is a sequence of steps against a source and its targets, and a restart of either between two steps
leaves the restore half done. An upgrade restarts clusters. Both need the same cluster for hours, so the run takes the
lock a restore takes, `<edge_rebuild_artifact_dir>/<host>/restore/lock`, on every host the plan moves or reboots, and on
every host whose declared replication targets one of them, since a restore acts on its targets too.

The locks are held from the plan to the run's end, renewed at the start of every hop to a lease sized from that hop's
own waits, and released at every end the run itself reaches: hops done, a hop that failed or stopped, a lost lock, or a
host that failed after its take. A restore of a moved host, or of a source of one, therefore waits for the whole run,
which can be days for a fleet several quarters behind. The alternative would be a restore that races a commit, which
is the worse failure and a quieter one.

A lock held by another run fails that host, and holds what the lock protects, the host and every host it replicates
to, in every hop that moves them, so that no host a live restore acts on is restarted between two of the restore's
steps. The rest of the fleet goes on, and the run ends after the first hop that moves one of them.

The run cannot lock what it does not move. A source outside the run cannot be locked by it, and the plan warns that a
restore of it started meanwhile is not held off (W9). The plan does warn of an open restore record, which shows a
restore that has not finished, and of a live lock on a host the run does not lock, when either names a host the run
moves or reboots (W1). It warns and goes on, because the restore can finish after the upgrade, and a lock whose run is
gone is taken over, not warned about. [Before you start](../how-to/upgrade.md#before-you-start) in Upgrade a fleet has
the look to take before a run.

A run stopped with Ctrl-C, or killed, never reaches its end and leaves its locks behind. The next run or restore on the
same controller takes such a lock over, with a warning, once the `ansible-playbook` process that took it is gone, and
refuses it while that process lives. From another controller that shares the artifact directory, a lock is taken over
only once its lease runs out, which for a hop is many hours. The lease is long because a hop is long; a short one would
let a second run take a lock from a first that was still waiting on a reboot.
[Troubleshooting](../reference/troubleshooting.md#restore-locks) quotes the lock's warnings and refusals.

## What the upgrade does not do

- **It does not downgrade.** A host that runs a release above its target is refused. A snapshot before a run keeps a way
  back for the data that matters most ([Snapshot before a change](../how-to/snapshot-before-a-change.md)); nothing keeps
  a way back for the cluster's software.
- **It has no canary mechanism.** The plan has no notion of a first cluster whose result gates the rest. A canary is a
  run limited to one standalone cluster, then a run of the fleet, which skips the canary as being at its target. Pick a
  canary in no portal component, since a limit that leaves out part of a component is refused (F13), and one that
  replicates to no other cluster, since a source upgraded ahead of its target holds new state back from it (W12). [Try a
  canary first](../how-to/upgrade.md#try-a-canary-first) has the commands.
- **It does not decide which releases to use.** The images list is yours, from Qumulo Care.
- **It does not lift an unlicensed edge's refusal.** Qumulo Core refuses to upgrade an edge whose license has expired,
  or that has none and was formed more than 30 days ago, and only Qumulo Care can waive it (G-UPGRADE-LICENSE). The plan
  warns about each such edge (W2). Once Qumulo Core refuses it, the edge leaves the play and holds its portal component,
  and with it whatever follows the component, and the run ends after that hop. See [Upgrading an edge without a
  license](licensing-and-capacity.md#upgrading-an-edge-without-a-license).
- **It does not change what Terraform recorded.** The Terraform provider's `cluster_version` is the version it deployed,
  not the one the cluster runs after this playbook upgrades it. A Cloud Native Qumulo cluster is upgraded as any other
  cluster is, with the cloud image, and the provider's value stays as it was.
- **It does not apply the collection's 7.10.3 floor.** The upgrade runs from older sources and prints no floor warning.
  Which sources are supported is what backwards testing finds, and [the
  reference](../reference/upgrade.md#supported-sources) records it. Backwards testing upgrades one cluster through
  every quarterly release rather than forming a cluster at each, because an upgraded cluster is what customers run.

## Learn more

- [Upgrade a fleet](../how-to/upgrade.md) declares, checks and runs an upgrade, and covers the variants named above.
- [Recover an upgrade that stopped](../how-to/resume-an-upgrade.md) reads the report and handles a commit that did not
  finish, a paused reboot and a lock.
- [The upgrade: variables, steps and files](../reference/upgrade.md) lists the variables, the nine steps of a hop, the
  files and the supported sources.
- [Troubleshooting](../reference/troubleshooting.md#upgrades) quotes every refusal and warning with what lifts it.
- [Edges, hubs and portals](edges-and-portals.md) explains why a portal needs both ends on one release, and
  [Replication](replication.md) the range replication tolerates.
- [Refusals and guards](refusals-and-guards.md) explains why the collection refuses rather than guesses, and the
  [gap register](../reference/gaps.md) holds the ids cited above.
