<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Edges, hubs and portals

An edge holds a cache of data that lives on a hub, and a portal is what connects the two. This page explains the model
the collection implements: what a portal carries, why some paths on an edge are not the edge's to manage, how the cache
is sized, why the inventory declares every address by hand, and why a portal is drained before it is deleted.

To pair an edge with its hub, see [Pair an edge with its hub through a portal](../how-to/pair-edge-with-hub.md). To
bring a hub and an edge you already run under one inventory first, see [Your first fleet](../tutorials/first-fleet.md).

## The model: a hub, its spokes and the roots between them

A hub is a cluster that holds data. A spoke is a cluster that reaches some of that data through a portal and caches
what it reads. An unprotected edge is a single-node cluster that is usually a spoke. The collection calls a spoke an edge. The portal
modules use the cluster's own terms, spoke and hub.

A portal links directories, not clusters as a whole. Each link is a root pair: a spoke root on the spoke and a hub root
on the hub. Under the spoke root the spoke shows the hub root's tree. Whether the spoke may write through the portal is
the portal's type: `read_write`, or `read_only`. The hub decides the type of an accepted portal, so changing it is a
hub-side change ([portal_hub](../../plugins/modules/portal_hub.py)), not a spoke-side one
([portal_spoke](../../plugins/modules/portal_spoke.py)).

Both clusters keep a record of the portal. The spoke's record holds the hub's addresses and the hub's record holds the
spoke's, and neither holds its own ([G-PORTAL-HOSTS](../reference/gaps.md)). The collection identifies a portal by the pair of
cluster UUIDs, one portal per pair, and each root by its path on its side, never by a server id. [State management](state-management.md#keys-not-ids)
explains why identity is built from keys the inventory can state.

The inventory mirrors this. The edge declares the pairing in `qumulo_portal`: its hub, an inventory host, and the root
pairs. The hub declares only its own addresses. The edge declares the pairing because the baseline run on the edge is
the run that pairs: it gives the [portal_pair](../../roles/portal_pair/) role a connection to both clusters, so the role reaches the hub from the
edge's play.

Which hosts are hubs is a fact about your fleet, not about a cluster, so `qumulo_hubs` is a group you fill. The
Terraform inventory plugin groups a host by its kind and by who formed it, and never puts a host in `qumulo_hubs`.
The edge rebuild requires the hub to be in that group and finds each portal's hub there by UUID. The baseline's portal
step and the upgrade plan read `qumulo_portal.hub` from the host entries, whatever group the hub is in. A hub may itself
be another hub's spoke. The upgrade plan treats a hub and every cluster that reaches it through `qumulo_portal.hub`, its
spokes and theirs, as one portal component.

## Spoke roots hold the hub's data

A path inside a spoke root looks like a path on the edge, but the data in it belongs to the hub. The spoke serves it
from its cache and passes writes to the hub.

| On an edge, for a path inside a spoke root | What the collection does | Why |
|---|---|---|
| An ACL change | Refuses unless the entry sets `allow_data_loss: true`, in check mode too | The spoke passes the write to the hub, so it replaces the hub's ACL whole. The hub's own inventory may declare another one. |
| A quota or a snapshot policy | The baseline fails before it writes anything | The cluster refuses both ([G-SPOKE-PATHS](../reference/gaps.md)). Without the preflight the refusal would come at step 20, after the portal and every earlier step had applied. |
| A snapshot | The snapshot module refuses | The cluster takes no snapshot on or under a spoke root (G-SPOKE-PATHS). |
| An S3 bucket | Refused by the baseline's preflight and by the bucket module | A bucket there would serve the hub's data from the spoke's cache. |
| Capture and clone | Capture notes the path as the hub's, which the hub's capture declares; a clone leaves it out and leaves out `qumulo_portal` | The clone has no portal of its own yet, and the data is not the source's. The hub's record of the portal is keyed on the source's UUID, so the clone declares a portal of its own. |
| An edge rebuild | The drain moves exactly this data to the hub; nothing else survives | Everything outside the roots exists only on the edge. |
| A replication target | The replication step's preflight refuses a target path inside a portal root, on the spoke that owns the root and on the hub that a spoke's root links. It checks once, before any relationship, so every refusal comes before any write on either cluster. | Authorizing a relationship makes the target read-only and replaces its content with the source's. Inside a spoke root that content is the hub's; inside a hub root it is what the spokes read and write through the portal. |

The refusals leave the declaration to a place where it works. Declare ACLs, quotas, snapshot policies and buckets on the
hub, whose paths they are, or on a directory of the edge outside every spoke root. A hub's own roots may carry quotas
and snapshot policies.

The same boundary is why a rebuild says "local data". A directory outside the spoke roots, and its snapshots, exist
only on the edge. The portal can't carry them to the hub, so the rebuild stops until you list `local_data` in
`edge_rebuild_acknowledge` and accept losing them. See [Rebuilds](rebuilds.md).

### Why a root has to be absent, and its parent present

The server creates a spoke root when the spoke proposes it, so the path must not exist beforehand and its parent must.
`portal_spoke` checks both on the spoke before it proposes. The hub root must be an existing directory on the hub,
which the spoke-side module can't see, so the order of runs has to guarantee it ([below](#why-the-hub-goes-first)).
Creating a spoke portal and proposing a root fail when repeated, and a failed proposal leaves an orphaned root on the hub
([G-PORTAL-ONESHOT](../reference/gaps.md)). The modules therefore read and match first, and check the paths before sending, so that a retry
finds nothing to repeat.

## The edge's cache and its size

On a spoke the file system is the cache for the hub's data. Three things set how much of the hub's data an edge holds: the
capacity clamp, the eviction threshold and the disk the edge was formed on. The clamp and the disk are capacity
questions, and [Licensing and capacity](licensing-and-capacity.md#the-clamp) explains them: writing the clamp re-forms
quorum, provisioning never shrinks, and a larger disk is a rebuild. The eviction threshold,
`qumulo_capacity.free_threshold`, is the portal's own lever, and the one that needs neither a clamp change nor a
rebuild. It is the fraction of capacity the cluster keeps free by evicting data it caches for its spoke portals.

The eviction setting is one value per cluster ([portal_eviction_settings](../../plugins/modules/portal_eviction_settings.py)).
Every cluster has it and it takes effect on one that hosts spoke portals. A newly formed cluster stores 0.05 ([QUIRK-009](../reference/quirks.md)).
The baseline applies it at step 11, after the license and the clamp. The cache the cluster uses for spoke data is
readable through `portal_info`'s `cache` subset, which reports it in bytes.

Eviction is also how a cache smaller than the formation floor is kept. A new edge provisions the smaller of its
platform's limit and 1 TB at formation, before any clamp can be written
([G-EDGE-FLOOR](../reference/gaps.md)), so a cache below that is kept by telling the cluster to evict until the rest is
free. [The 1 TB floor](licensing-and-capacity.md#the-1-tb-floor) explains the floor. To act on any of this, see
[Size and resize an edge's cache](../how-to/edge-sizing.md).

## Addresses are declared, never derived

Each side of a portal reaches the other at IP addresses, and the inventory declares them. A hub gives `qumulo_portal_hosts`
as the addresses its spokes reach it at, and an edge gives its own as the addresses its hub reaches it at. Each is a
list of items with an `address` and an optional port, 3713 by default. A replication target adds
`qumulo_replication_address`, one address, and `qumulo_replication_port`, 3712 by default. None of them is
`qumulo_api_host`, which may be a name and is the address your controller uses, and none is an FQDN: the portal modules
and a relationship's target address take IP addresses only.

The collection reads none of them from the cluster's network status, and does not translate or second-guess what you
declare. There are two reasons:

- No cluster reports the address it is reached at. A portal record holds the peer's addresses only (G-PORTAL-HOSTS), so
  there is nothing on either cluster to read them back from.
- The right address depends on the path between the two clusters, which neither can see. A cluster's network status
  lists its own addresses, not the ones a peer reaches it at through NAT or a load balancer, and the address your
  controller uses is not the one the peer uses.

Making the addresses reachable is yours to do. Behind NAT, declare the addresses that reach the cluster from the other
side. The collection does nothing about NAT.

A hub host holds one address list for all its spokes. Spokes that reach the hub at different addresses can't each have
their own, so declare the one that all of them reach.

### Which address to choose

Floating IPs are the recommended addresses. A floating IP moves to another node when its node goes down, so the other
side keeps reaching the cluster. A node's own IP goes with the node. A bare-metal cluster or an edge without floating
IPs gives its node IPs, which is the best it has. A multi-AZ cluster in AWS has no floating IPs, and its targets are
the IPs of its network load balancer, covered [below](#addresses-behind-a-load-balancer).

Floating addresses are also what the cluster's own DNS name answers with. With authoritative DNS on, each answer gives
one node's floating address, which spreads clients across the nodes. A record TTL of 0, the cluster's default, makes a
resolver ask again each time so that it never hands one cached address to many clients. That spreading is for clients.
A portal peer is given the fixed list above, not a name. To turn the DNS server on, see
[Declare common settings in the inventory](../how-to/inventory-recipes.md#answer-dns-queries-for-the-clusters-own-name).

### Where the values come from

You can write them in `host_vars`, or let the Terraform inventory plugin ([terraform](../../plugins/inventory/terraform.py))
set them from the output's `portal_hosts` and `replication_address`. A value in `host_vars` outranks the plugin's. The
plugin publishes what the output gives, or what a `load_balancer_name` resolves to, and derives nothing from the
cluster. It sets `qumulo_portal_hosts` from the list and `qumulo_replication_address` from a single address.

### Addresses behind a load balancer

A multi-AZ cluster in AWS has no floating IPs. Its node IPs don't move when a node goes down, so publishing them gives
the other side an address that dies with its node. Its targets are the network load balancer's IPs. The provider forms
no load balancer, and a root's `aws_lb` exports a DNS name and no IP address. The portal modules and a relationship
take IP addresses only, so a name has to become addresses before the collection sees it ([G-TF-NLB-ADDRESS](../reference/gaps.md)).

There are two ways to do that, and they differ in what the addresses mean:

- The root reads the load balancer's network interfaces, one in each of its subnets, and publishes their IPs. They are
  every zone's IP, whatever the health of its targets when the inventory is read, and each stays the same for the load
  balancer's life. This is a fixed, complete list.
- The root publishes the name as `load_balancer_name`, and the plugin resolves it on the controller each time it reads
  the output, a cached read included. This is a snapshot of what the name answers at that moment.

The snapshot can be shorter than the fixed list. AWS documents that, with cross-zone load balancing off, a load
balancer's name stops answering with a zone's IP while that zone has no healthy target. With cross-zone load balancing
on, AWS keeps every zone's IP in the name while any target is healthy. A run during an outage would publish fewer
addresses. The portal modules would then take the missing zone out of the portal's host list until a
later run put it back, and `replication_address` could move to another zone and back.

The plugin guards against this where it can. With the inventory cache on, it fails the source when a name resolves to
fewer addresses than the cache recorded, and it names the missing ones. Publishing the interfaces' IPs avoids the
snapshot altogether, and the portal's host list holds every zone through an outage, so Ansible leaves it alone.

Neither way is failover for a connection. The portal's host list and a relationship's target address are stored as IP
literals, and the collection never resolves a name per connection, so what matters is that the stored list is complete
and stable. A relationship takes one address, so it reaches one zone's node either way. An outage of that zone stalls
replication until the zone recovers or you change `replication_address`.

A load balancer formed again comes back with other IPs. The next run publishes them, the portal modules change their
hosts in place, and the baseline's replication step changes the relationship's address in place.

To publish the addresses in either form, or to clear a failed source, see
[Use a Terraform root's output as the inventory](../how-to/terraform.md).

## Why the hub goes first

A pairing is two records made by two calls: the spoke proposes the portal and its roots, and the hub accepts them and
authorizes the roots. Accepting is one-shot, so the hub-side module accepts only a PENDING portal. The `portal_pair`
role also asserts that both clusters run the same Qumulo Core version before it proposes
([below](#one-release-one-writer-at-a-time-on-the-hub)). Accepting a portal, or changing its type, makes both clusters unavailable briefly. The modules
wait through that, reading until the portal is ACCEPTED and ACTIVE.

Each hub root must exist when the edge proposes. Directories come after the portal in the baseline's order of areas, so
that a directory may lie under the roots the portal authorized ([the order of
areas](architecture.md#the-order-of-areas)). With Ansible's linear strategy, the default, every host finishes the
portal step (step 16) before any host starts the directories (step 17). A `hub_root` that the same run declares would
therefore not exist yet when its edge pairs. So bring the hub to its baseline first, and then its edges.

Under `--check` the role predicts each side's change and waits for nothing, and the hub-side module predicts the accept
of a proposal that an earlier task in check mode would have made, with a note. [State
management](state-management.md#check-and-diff) says what a check run can and can't tell you.

## One release, one writer at a time on the hub

A portal connects only clusters on one release, which is why the `portal_pair` role asserts, before it proposes, that
both clusters run the same Qumulo Core version.

Only the hub-side calls take turns. Hub portal writes carry no ETag ([G-PORTAL-ETAG](../reference/gaps.md)), so two edges
pairing with the same hub at once could overwrite each other without either noticing. The role sends the hub-side calls
one host at a time, and that protects only within a single play.

The same-release rule reaches into upgrades. A portal is down from its component's first commit to its last, and a
spoke serves nothing from its portal roots meanwhile. The upgrade plan therefore commits a component in waves, with
every spoke before its hub, and refuses a hop that would leave members that began on one release on two.
[Upgrades](upgrades.md) covers the ordering.

## Removing a portal: drain, then delete

A read-write portal can hold writes that the spoke has accepted and the hub has not. Deleting the portal then would
strand them. The collection drains the portal before it deletes it.

The drain is a type change on the hub. Setting the portal to `read_only` stops the spoke's clients writing through it,
and the portal reports QUIESCING until the writes already accepted reach the hub. The hub-side module waits for that by
default, and so does the `portal_pair` role, which also waits until the spoke shows the portal read-only. Only then
does the role delete the spoke portal, and after that it waits until the hub's record is gone too. A record that stays
on the hub keeps occupying one of the hub's portal slots.

A portal needs no drain in three cases:

- **PENDING.** The hub has not accepted the portal, so it holds no spoke writes.
- **DELETING.** The spoke portal is already deleted by an earlier run, and the hub refuses a type change on it. The role
  skips the drain and waits for the record to go.
- **DISCONNECTED.** The hub can't reach the spoke, so nothing can be drained. Deleting the portal loses whatever the
  spoke accepted that the hub did not receive.

A DISCONNECTED portal is the one the collection refuses to guess about. The spoke refuses its own delete only when it
sees itself disconnected, so a hub that sees the spoke DISCONNECTED while the spoke sees itself ACTIVE would let a
read-write spoke go without a drain. The role checks the hub's view first and fails before any change, unless
`portal_pair_allow_data_loss` consents to the loss. The status can be brief, or the hub's address for the spoke can be
wrong, so the failure advises running the role again later before consenting. See [Refusals and
guards](refusals-and-guards.md) for why a loss waits for consent.

A rebuilt edge leaves a hub record that no drain can reach. A rebuild gives the edge a new UUID, and the hub's record is
keyed on the old one. The hub has no marker for a record whose spoke is gone, and a delete without force never finishes
([G-PORTAL-STALE](../reference/gaps.md)). Removing it takes `allow_data_loss` on the hub-side module, which forces the
delete. The rebuild deletes the stale record before the edge forms. It forces it without asking only when the record
holds no writes: a PENDING record, or a DELETING one whose predestroy record shows the portal was drained. A
DISCONNECTED record, left by an edge that was lost without a drain, needs `edge_rebuild_allow_data_loss`, and the writes
the hub had not received are lost. A hub that still reaches the old edge means the old edge is up, and the replay stops.
[Rebuilds](rebuilds.md) has the whole sequence.

## What the collection leaves alone

- It does not derive, translate or second-guess an address, and it does not manage NAT.
- It does not change a root in place. A spoke root that already maps to another hub root fails, since the server can't
  change it.
- It does not let a spoke change an accepted portal's type. `portal_spoke` applies its `type` only when it creates the
  portal, and a different value later adds a note and changes nothing. The hub decides.
- It does not touch roots that the spoke proposed outside `qumulo_portal.roots` (the
  `portal_pair` role's `portal_pair_roots`), or wait for them.

## Learn more

- [Pair an edge with its hub through a portal](../how-to/pair-edge-with-hub.md) declares, applies, checks and removes a
  portal.
- [Size and resize an edge's cache](../how-to/edge-sizing.md) and [Rebuild an unprotected edge](../how-to/edge-rebuild.md)
  act on the cache and the drain described here.
- [Use a Terraform root's output as the inventory](../how-to/terraform.md) publishes the addresses, and [The Terraform
  inventory plugin](../reference/terraform-inventory.md) lists the output fields and the expression for each resource.
- [Replication](replication.md) covers the relationship that shares the addresses above, and [Upgrades](upgrades.md) the
  order in which a hub and its spokes commit.
- [Variables](../reference/variables.md) lists `qumulo_portal`, `qumulo_portal_hosts` and
  `qumulo_replication_address`, and the [gap register](../reference/gaps.md) holds the G- entries and the
  [quirk register](../reference/quirks.md) QUIRK-009.
- `portal_pair` is the role that pairs and decommissions, and
  [portal_info](../../plugins/modules/portal_info.py) reads each side's record.
