<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Licensing and capacity

The license's cap, the capacity clamp the inventory declares and the platform's disks bound how large an unprotected
edge's cache can grow.
What the formation provisioned, which never shrinks, bounds how small it can be.
This page explains how they interact, why a license takes two passes of the same step, and why the collection handles
Nexus registration the way it does.
The ids in parentheses, such as G-LICENSE and QUIRK-011, are entries in the [gap register](../reference/gaps.md) and the
[quirk register](../reference/quirks.md).

To do the work, see [License an edge](../how-to/licensing.md), [Size and resize an edge's cache](../how-to/edge-sizing.md),
[Register a cluster with Nexus](../how-to/nexus.md) and [Rebuild an unprotected edge](../how-to/edge-rebuild.md).

## A license belongs to one UUID

A license is bound to a cluster UUID.
A new cluster has a UUID no license names yet, and so does an edge that was formed again: creating an unprotected edge
accepts no UUID, so a rebuilt edge always gets a new one (G-UUID).
Qumulo issues the license in Nexus, which sees the UUID once the cluster is registered there.
A customer organization's users, its administrators included, cannot create or see licenses in Nexus.

That fixes what the collection can do.
It never creates a license, never reads one from Nexus, and never waits for one (G-LICENSE).
The [`license`](../../plugins/modules/license.py) module talks only to the cluster.
It reads the license the cluster holds, applies a license document when it is given one, and reports the license and
the UUID one must be issued for.
The document reaches the collection as a value the operator puts in the inventory, `qumulo_capacity.license_document`.
How Qumulo delivers the document to a customer is not yet defined (G-LICENSE).

A document cannot exist before the cluster is registered, because Nexus issues it for a UUID it has seen.
So a new UUID cannot be licensed in one run.
Issuing the document is Qumulo's step, outside any run, with no defined delivery, so a run that waited would have
nothing to wait on with a bound.
The collection instead ends the pass, says what to get, and lets a second run continue.

## Two passes

The step that does this is the license step of the [`cluster_baseline`](../../roles/cluster_baseline/) role.
[`playbooks/edge_license_complete.yml`](../../playbooks/edge_license_complete.yml) runs the same step on its own, so the
baseline and the playbook do the same work.

### The first pass

The baseline's monitoring step comes before the license step, and registers the cluster with Nexus when
`qumulo_monitoring` turns the connection on and gives a key or a token to mint one.
Nexus sees the UUID only once the cluster is registered.
When the step finds no license for the UUID and no document in the inventory, it succeeds.
In the baseline it prints a hand-off naming the UUID and goes on, so the areas after it still apply.
In `edge_license_complete.yml` the same condition fails the edge, naming the UUID, because finishing the license is that
playbook's whole job.

The first pass also leaves the clamp alone.
A license's cap applies only at the next quorum formation, and within the license step, writing the clamp is the only
thing that causes one (G-CLAMP-BOUNCE).
A clamp written before the license arrives would spend that formation on a cap that is not there yet.
Deferred until the second pass, one write does both jobs: it sets the limit and applies the cap.

A document in the inventory that names another UUID is not applied.
After a rebuild it is the old edge's, and the replay is always the first pass, so the step hands off and says the
document is for another UUID instead of failing the pass.

### The second pass

The document is active as soon as it is applied.
The cluster takes a document only at its debug license routes, which need the `PRIVILEGE_DEBUG` privilege; the built-in
`admin` user holds it.
Qumulo Core offers no other route today, and the collection will use a release route once a version offers one
(G-LICENSE).

The step then sets the clamp.
Before it writes, it checks that the license's cap allows the clamp, when the clamp would change.
A cap below the clamp fails there, before anything re-forms quorum, because the write would pause the cluster, and an
edge's portal on its hub, for under 30 seconds to apply nothing.
The write is what applies the cap.

Once the cluster holds the license, the second pass needs no document, and a run on a cluster whose clamp is set changes
nothing.
The module knows a license by its key, and also compares the document's cap and expiry with what the cluster reports.
So a renewed license, a new document for the same UUID, is applied once, a document the cluster already holds is left
alone, and an altered document under a held key goes to the cluster, which refuses it.
The key signs every other field of the document, so the module sends exactly the fields the document has and rejects a
document that lacks one or adds one, instead of dropping it.

### When no write follows

If the clamp is already at its value, the second pass writes nothing, and a cap that was applied waits for whatever
quorum formation comes next, such as the one an upgrade's commit causes.
The step cannot cause the formation without writing a clamp, so it says that the cap waits, in the run that applied the
document.
On an edge that declares a clamp it also says so in a run that applies nothing, in the one case it can tell: the file
system still has the size an edge formed without a license has, and the cap and the clamp both allow more.

The comparison goes through a margin, `FLOOR_MARGIN`, 10^11 bytes.
The server rounds the file system, the cap and the clamp to units whose size the collection cannot read, so a raise
smaller than the margin could be rounding.
Such a raise draws no warning, and still applies at the next quorum formation.
On a platform whose capacity falls between 1 TB less the margin and 1 TB, the file system stays in that window for good,
so the warning prints on every run.
Elsewhere a run that applies nothing cannot tell a cap that waits for a formation from a file system the platform keeps
smaller, and says nothing.

## The clamp

The clamp limits the usable capacity the cluster provisions, and on an edge that is the size of the cache.
Provisioning follows the smaller of the license's cap and the clamp, and an unset clamp limits nothing.
That makes the clamp the one limit the operator controls, and it limits a license that has not arrived: a license
that applies later cannot grow the edge past a clamp that is already stored.

Four properties of the cluster shape how the module handles it:

- **Every write re-forms quorum.**
  A write of the clamp makes the cluster abandon quorum to reprovision, even when it writes the value already stored
  (G-CLAMP-BOUNCE).
  [`capacity_clamp`](../../plugins/modules/capacity_clamp.py) therefore writes only on a difference, never sends a write
  twice, and by default waits for the cluster to be active again before it reads the clamp back.
  A write whose answer is lost is always waited for, because only the read-back tells whether it applied.
- **The cluster rounds the clamp up** to a whole provisioning unit whose size it does not publish (QUIRK-012, G-CLAMP-TOL).
  The module counts a stored clamp as the request already applied when it is no more than `rounding_tolerance` above it.
  The default tolerance is the larger of 1% of the request and 8 GiB, because the unit was 8 GiB on a Qumulo Core 7.10.3A
  edge, where a request of 53.33 GiB was stored as 56 GiB.
- **Provisioning never shrinks** (G-CLAMP-SHRINK).
  A lower clamp only stops the cluster from provisioning more, so the module treats it as a decrease and refuses it
  unless `allow_decrease` says the operator knows.
- **One write raises the clamp by at most 5 PB** (decimal), measured from the stored clamp, or from the file system's
  size when no clamp is set.
  A larger increase is refused before any write and is taken in steps, each of them a write and so a quorum formation.

The module does not check the license's cap or the size an unlicensed edge was formed with.
The license step checks the cap before it writes the clamp, and `edge_cache_route` applies the 1 TB unlicensed ceiling
and the floor.

## The 1 TB floor

An edge formed without a license provisions the smaller of its platform's capacity and 1 TB (G-EDGE-FLOOR).
Formation does this before any clamp can be set, and the formation call has no parameter for one.
Three consequences follow, and the rest of the collection's handling of edge capacity comes from them.

**An unlicensed unprotected edge is held to 1 TB.**
Without a license cap, 1 TB is the most such an edge can reach, and a target above it is blocked until a license allows
it.
The product type decides whether that ceiling applies, which is why [`edge_cache_route`](../../plugins/modules/edge_cache_route.py) takes it as an input with no
default and the `edge_cache_resize` role fails when it cannot read the type from the cluster.
A wrong guess would either block a valid target or spend a quorum formation on a clamp the license does not allow.

**A target below the floor is met by eviction.**
A rebuilt edge would provision the floor again, so a rebuild cannot make the cache smaller than it, and the role keeps
part of the cache free with `free_threshold` instead.
The route's reason suggests a threshold.

**A rebuild refuses a declared clamp below the floor.**
The edge forms unlicensed at the floor, provisioning never shrinks, and the baseline could not then write a clamp more
than its rounding tolerance below it.
The [`edge_rebuild`](../../roles/edge_rebuild/) role refuses such a clamp, whatever the license value, before anything
is lost ([Rebuilds](rebuilds.md#the-first-pass-runs-on-a-floor) says when).
The bound is the smaller of 1 TB and `edge_cache_resize_floor_bytes`.
That variable exists to describe a platform smaller than 1 TB, which forms smaller; it does not move the 1 TB ceiling.

## Thin disks and the routes to a size

Once an edge is formed, its disks cannot be resized and none can be added.
A larger edge than its disks allow is a rebuild onto larger disks, and provisioned capacity never shrinks, so a smaller
cache is a rebuild too.
Both facts point one way: give the edge a large, thin-provisioned disk, which costs nothing for space the edge does not
use, and set the clamp to the size needed now.
An edge with no clamp could provision the whole disk when a cap applies, and that cannot be undone.
Growing the cache is then a clamp write within the license's cap, with no rebuild.

The routes of `edge_cache_resize` are the answers to the questions those facts raise, in the order the role asks them:

- **Is the file system already the target size?**
  `within`, if it is inside the margin.
  The route margin is its own thing, 10% of the target unless set; it is a placeholder not yet calibrated on a licensed
  edge, and is not `FLOOR_MARGIN`.
- **Is the file system larger than the target?**
  Then provisioning would have to shrink, which it never does.
  `below_floor` if the target is under the floor, since a rebuild would not make the cache smaller than that; otherwise
  `rebuild`.
- **Does the license, or the unlicensed ceiling, allow the target?**
  `blocked_license` if not, and the role fails naming the license this edge's UUID needs.
- **Does the stored clamp already allow it?**
  If so, the clamp is not what keeps the file system smaller, and the route is `platform_limited`.
  If not, `grow`: write the clamp, wait out the formation, read the size again.

`platform_limited` has two possible causes that the collection cannot tell apart.
Either the platform's disks cannot provision more, or a raised license cap has not applied yet.
A cap applies at the next quorum formation, which a clamp write would cause, but the role writes no clamp when the
stored clamp already allows the target.
So it hands the case off and does not write.

### A failed automatic rebuild ends the run

`playbooks/edge_cache_resize.yml` goes on to the next edge when a route fails, so that one edge's failure does not stop
the others.
A failed automatic rebuild is the exception: the run stops there and names the edge.
A rebuild drains and destroys its edge before it forms the new one, so if whatever failed one edge's rebuild also holds
for the next edge, going on would drain and destroy that edge too.

## What `pending`, `required` and `none` mean

`qumulo_capacity.license` states what the inventory expects, and none of its values makes the collection wait.

- **`pending`**, the default, expects that the license may come after the first pass.
  An edge whose license Qumulo issues to the cluster directly declares nothing special: it stays pending until the
  license is active, and the step reads it when it arrives.
- **`required`** behaves like `pending` and also sets `cluster_baseline_license_missing` while the cluster has no active
  license, so that a later task can fail on the final state.
- **`none`** is for an edge that runs without a license, within the capacity its formation gave it.

`none` changes only what happens while the cluster holds no active license.
The step neither hands a license off nor fails, and it writes the declared clamp with no license, in the first pass and
the second.
That clamp limits capacity at every later quorum formation, so a license that arrives afterwards cannot grow the edge
past it.
A clamp above 1 TB is stored and provisions nothing more until a license arrives, and its write costs a quorum
formation that provisions nothing.
Provisioning never shrinks, so a clamp more than its rounding tolerance below the file system fails as a decrease.
A relationship from another host into a `none` edge waits only for the clamp.
The capacity gate, which keeps a full sync from starting into an edge whose license and clamp do not apply yet, passes
such an edge once its clamp applies.

`none` goes with a clamp and never with a license document, because a document means a license is coming.
The step refuses the pair as its first task, before it applies anything.
It tests the document's value, so an inventory whose group layout resolves `license_document` to none on a host without
a vaulted document passes.

If an edge declares `none` and holds an active license after all, the step runs as `pending` does and warns that `none`
is stale.
The wording of the warning depends on whether a clamp is declared, whether the file system has reached it, and whether
a cap is waiting for a formation, because those decide what the stale value costs.
With no clamp, nothing limits the license's cap, and the capacity provisioned under it never shrinks.

[Capture](../reference/capture-and-clone.md) writes `none` for an unprotected edge that holds no license and `required`
for any cluster that holds an active license for its UUID.

## Why a rebuild needs a new license

Formation gives the edge a new UUID, and the license, the Nexus registration and the hub's portal record are all keyed
on the old one.
A rebuild therefore always finishes in two passes: the replay is the first, and the second applies the new UUID's
document.
[Rebuilds](rebuilds.md) explains what a formation takes with it, and when the floor check and the relationships happen.

## Nexus and registration

Nexus is Qumulo's service for cluster registration, remote support and licenses.
A registered cluster is one Qumulo can see, and a new cluster's registration is what lets Qumulo issue its license.
The collection uses Nexus to register a cluster and to retire a UUID.
It does not use cloud monitoring to deliver a license: [`license`](../../plugins/modules/license.py) applies the document
an operator obtained.

### Monitoring, connection, registration

Registration needs the Nexus connection on, and the connection needs cloud monitoring on.
The baseline applies them in that order, and turning monitoring off also turns the connection off.
The cluster's support settings route refuses to turn the connection on while monitoring is off, but the connection
route accepts it.
[`nexus_connection`](../../plugins/modules/nexus_connection.py) refuses it too, before any write, so the two agree
(QUIRK-015).
[`monitoring_settings`](../../plugins/modules/monitoring_settings.py) owns the monitoring half, and warns when turning
monitoring off turns the connection off.
The registration comes before the license step so that Nexus sees the UUID by the time the hand-off names it.

### Join keys and tokens

A join key is single-use and registers one cluster (G-NEXUS-KEY), so a rebuilt edge needs a new one.
A key spent on a failed registration is gone as well.
So the module sends a key only to a cluster that is not registered, never returns it, and leaves a registered cluster
alone: a second run neither spends nor mints a key.

With a Nexus API token the module mints a key of its own for each cluster, so no person makes keys by hand.
Minting needs a Nexus role that may create API tokens.
The license step never uses the token.
[Authentication](authentication.md#the-nexus-token-and-join-key) explains the token as a credential.

### Why registration is synchronous

The cluster's registration status never reports `ERROR_REGISTERING`, so a failed registration looks like a lasting
`PENDING_REGISTRATION` (QUIRK-011).
Reading the status afterwards cannot show the failure.
[`nexus_registration`](../../plugins/modules/nexus_registration.py) therefore posts rotate after it stores the key.
That registers at once and answers with Nexus's reason when it fails, so the module fails with Nexus's reason and not
with a timeout.
A rotate whose answer is lost is never sent again; the registration status says whether it registered.

The key is single-use and Nexus sits behind a gateway that can fail, so each retry rule separates a call that did not
happen from one that did:

- A cluster that stores a join key while its connection is on registers by itself within about two seconds.
  A rotate sent in that window answers HTTP 412 and does nothing, so the module reads the status and sends rotate again,
  up to three times in all, only while the key is still pending (QUIRK-018).
- When the gateway fails the cluster's call with a 5xx error page, rotate answers HTTP 400 `decode_error` with the page.
  The module reads the status and sends rotate again while the key is pending, backing off from two seconds to fifteen,
  until `retry_timeout` runs out (QUIRK-022).
- A mint that the gateway answers with HTTP 502, 503 or 504, or whose connection is lost, is sent again until
  `retry_timeout` runs out, because nothing is stored on the cluster before the mint answers.
  A mint whose answer was lost may leave a spare key in the organization, which nothing uses.

### Retiring a UUID

A rebuilt edge gets a new UUID, and the old one stays listed in Nexus.
[`nexus_cluster`](../../plugins/modules/nexus_cluster.py) marks it decommissioned, and Nexus offers no way back.
A cluster that still connects is usually still in use, so the module refuses to retire one unless `allow_connected` says
otherwise.
The rebuild sets it, because the old edge is about to be destroyed and Nexus may keep reporting it connected for a while
after its registration is removed.

Retiring does not stop a cluster that still has the UUID: given a new join key it registers again, and Nexus keeps
listing it as decommissioned (QUIRK-018).
That is why only a UUID no cluster will use again is retired, and why its cluster's registration is removed first.
When the cluster being retired is the one the module connects to, the module checks that the registration is gone.

The module sits in the `qumulo.cluster.qumulo` action group, and its guard checks the cluster at `api_host`, so
retiring a UUID needs a reachable cluster's credentials even when the cluster that had the UUID no longer exists
(G-NEXUS-RETIRE).
With `qumulo_nexus_api_token` the predestroy phase retires the UUID while the old edge still answers, and the replay
retires it again against the new edge, which covers an old edge that is already gone.
Without a token the rebuild report hands the old UUID off to be retired by hand.

## Upgrading an edge without a license

Qumulo Core refuses to upgrade an edge whose license has expired, or that has none and was formed more than 30 days ago,
and only Qumulo Care can waive it (G-UPGRADE-LICENSE).
An edge that runs without a license can therefore be upgraded only in its first 30 days.
The collection cannot lift the refusal, so the upgrade plan warns about each edge whose license reads none or expired and
goes on; the refusal itself is Qumulo Core's.
Licensing the edge before the upgrade avoids it, and [License an edge](../how-to/licensing.md) gives the steps.
[Upgrades](upgrades.md) explains how a held-out edge affects the rest of a fleet.

## Learn more

- [License an edge](../how-to/licensing.md) gives the two passes as steps.
- [Size and resize an edge's cache](../how-to/edge-sizing.md) gives the commands behind the routes.
- [Register a cluster with Nexus](../how-to/nexus.md) gives the registration steps.
- [Troubleshooting](../reference/troubleshooting.md#hand-offs) has the text of the hand-offs and warnings.
- [Edges and portals](edges-and-portals.md) explains the cache and the hub it fills from.
- [State management](state-management.md) explains why a second run changes nothing.
