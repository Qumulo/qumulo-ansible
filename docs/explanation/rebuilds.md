<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Rebuilds

A rebuild replaces an unprotected edge with a newly formed one that has the same name and the same inventory entry.
It is not a repair of the old edge: the [edge_rebuild](../../roles/edge_rebuild/) role drains the old edge, which is then
destroyed and formed again, and the new edge is configured from the inventory.
This page explains why a rebuild is a replay of the inventory and not a copy of the edge, what a formation takes with it,
why the role checks and refuses as much as it does, and why it ends with a list of things for a person to do.

To rebuild an edge, see [Rebuild an unprotected edge](../how-to/edge-rebuild.md).
The variables, files and report fields are in [Playbooks and roles](../reference/playbooks-and-roles.md), and the
message texts are under [Rebuilds](../reference/troubleshooting.md#rebuilds) in the troubleshooting reference.

## Rebuild, clone and resize

Three operations can look alike, and each answers a different need.

- A rebuild replaces an edge that you keep running under the same name.
  It is needed when the cache has to shrink, because provisioned capacity never shrinks (G-CLAMP-SHRINK), when the edge
  needs a new platform, or when it needs a larger disk, because once the edge is formed its disk cannot be resized and
  no disk can be added.
- A [clone](../how-to/clone-edge.md) adds a second edge beside one you run.
  It is a separate cluster with a name and a UUID of its own, and it never claims to be the source: the source's UUID is
  never copied, nor are the UUIDs the source took over in a rebuild.
  It copies configuration only, no data.
- A [resize](../how-to/edge-sizing.md) raises the capacity clamp within the disk the edge already has, and forms
  nothing.
  A resize that would shrink the cache is a rebuild, and the `edge_cache_resize` role can start one.
  [Licensing and capacity](licensing-and-capacity.md) explains why the clamp is the lever and why an edge starts with a
  small one.

The rest of this page is about the first of the three.

## A formation makes a new cluster

[unprotected_edge_create](../../plugins/modules/unprotected_edge_create.py), the module that forms an unprotected edge,
accepts no UUID, so every formation gives the cluster a new one (G-UUID).
A "rebuilt edge" is therefore a different cluster that carries the old edge's name.
Everything the rest of the system keyed on the old UUID does not follow it.

| Keyed on the old UUID | What the rebuild does | Why |
|---|---|---|
| The hub's record of the edge's portal | Predestroy drains the portal and deletes both records. The replay deletes any record of the old UUID that the declared hub still holds, then the baseline pairs the new edge again. | The hub keys the record on the spoke's UUID, never on a name, which its portals need not keep unique. The new edge has a different UUID, so it cannot take over the old record. |
| The license | The replay is the first of two passes, and the license for the new UUID comes in the second. | A license is bound to one UUID and only Nexus issues it (G-LICENSE). See [Licensing and capacity](licensing-and-capacity.md). |
| The Nexus registration | Predestroy removes it and, with `qumulo_nexus_api_token`, retires the old UUID. The replay retires it again, and the baseline registers the new edge. | A registration belongs to one cluster, and a join key is single-use (G-NEXUS-KEY). |
| The domain's computer account | Predestroy leaves the domain. The replay joins under the same name. | The cluster name is the account name, and it does not change. See [A computer account that outlives the edge](#a-computer-account-that-outlives-the-edge). |
| Access tokens | They are dead on the new edge. The preflight makes you acknowledge them, and the report lists them. | A token is bound to one cluster, and its secret was shown only once (G-TOKEN). |
| S3 access keys | The baseline makes a new key for each declared one. The report names each, with the file of its secret. | The cluster makes both the key's id and its secret and shows the secret once, and no route imports a known key (G-S3-KEY). |
| Replication relationships | A relationship the inventory declares is kept or held. One it does not declare is lost. | The target's UUID is part of the source's key (G-REPL-PEER). See [What a rebuild does to replication](#what-a-rebuild-does-to-replication). |
| Terraform's state of the edge | It still names the old UUID unless Terraform formed the new edge. | Terraform records the UUID when it forms the cluster (G-TF-STALE). See [Terraform forms the new edge](#terraform-forms-the-new-edge). |

A formation also creates things the old edge had no say in: the factory `/` export, the `Files` share, and the built-in
users and groups.
The replay cannot ask the formation to skip the export and the share (G-FACTORY), so the inventory has to say whether
each one stays, and a rebuild requires `qumulo_factory_defaults` to say it.
A rebuild never counts them as lost state, because every formation brings them back.

Every client of the edge's S3 keys then needs the new secret, and the report is where it is found.
A secret can only be handed over once, which is why the role writes it to a file and names the file, and does not
try to carry the old one across.
[State management](state-management.md#values-the-cluster-shows-once) explains the values the cluster shows once.

### How the inventory learns the new UUID

The inventory names the old edge by `qumulo_cluster_uuid`, and every record the role keeps is keyed on it.
After the formation the same variable has to name the new edge, so the replay ends by writing a report whose
`host_vars_patch` holds the new UUID and the old edge's UUID added to `qumulo_previous_cluster_uuids`.
The role does not edit `host_vars`.
It switches the play to the new UUID in memory, so that the rest of the run is checked against the right cluster, and the
file change is yours to apply.

The previous UUIDs are the memory of the rebuild.
A replication record made before the rebuild names the old UUID of its peer, and the role finds it by that list, at
whatever address the peer now has.
Until the patch is applied, a source host whose relationship points at the edge fails at the edge's identity guard,
because it still expects the old UUID.

Because one variable is the old edge before the patch and the new edge after it, a replay refuses a cluster whose UUID
is the one `qumulo_cluster_uuid` names: with the patch applied, that is the new edge, and replaying onto it would retire
it in Nexus and report it as replaced.
A replay that failed after the edge formed is finished by the baseline instead.

## The replay model: the inventory, never the edge

The role replays the inventory, never the state of the edge it destroys.
Three things make that the right model.

- The edge is gone by the time the replay runs.
  Whatever the replay reads has to come from somewhere that survives the reinstall, and the inventory is the place the
  collection treats as the source of truth.
- Some of what the old edge holds cannot be read back.
  Passwords, token secrets and S3 secrets are shown once or never, so a copy from the edge would be incomplete in the
  same places that matter most.
- A capture is a one-time import, and nothing replays one.
  After an [adoption](../how-to/adopt-a-cluster.md) the inventory leads, and an edge you may rebuild must declare every
  tenant, share, export, quota, snapshot policy and portal root it holds, as a fresh adoption does.

The consequence is a rule, and the role checks it before it drains anything: everything the edge holds must be in the
inventory first.

### The preflight

[edge_rebuild_preflight](../../plugins/modules/edge_rebuild_preflight.py) runs on the old edge before the drain.
It only reads, and it fails the rebuild, naming what it found, in two cases.

- **Drift** is state the edge holds that the inventory does not declare, or declares absent.
  The edge and the inventory disagree, and a rebuild would settle the disagreement by destroying the edge's side.
  The check makes you look first.
  The inventory can settle it by declaring the item or applying its absence, and `edge_rebuild_ignore_drift` accepts the
  loss.
- **Unreplayable state** is state the replay cannot create again.
  Nothing waives it except naming its area in `edge_rebuild_acknowledge`.
  `edge_rebuild_ignore_drift` never covers it.

The two have separate switches because they mean different things.
Drift is something the inventory could have said and did not, and one switch says you accept the loss, or the one gain
the preflight reports, a tenant 1 the old edge lacked.
Unreplayable state is something the inventory cannot say, so the acknowledgement is by area, and each acknowledged area
reaches the report as a hand-off.
A blanket flag would lose the names.

Tenant 1's factory `Files` share and `/` export, whatever tenant 1's name, the built-in `admin` and `guest` users and
the built-in `Users` and `Guests` groups are never drift, since every formation creates them again. A `Files` share or
`/` export in another tenant is drift like any other.

Tenants need care in a rebuild because the rebuilt edge is not the old one. Every formation creates tenant 1 as
`Default`, and the replay renames it only for a `qumulo_tenants` entry with `previous_name: Default`, so tenant 1
renamed by hand is drift until the inventory says so. Tenant ids are numbered again in the order the replay creates the
tenants, so the inventory names a tenant by its name, not by `tenant_id`. The preflight reads `qumulo_tenants`,
`qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_factory_defaults` and `qumulo_network` as the baseline will on a
newly formed edge, matches each share and export within its tenant, and, before it reads the edge, refuses an inventory
that the baseline's tenancy step would refuse by its inventory rules, network rules that need no read among them. One
network rule needs a read of the edge's network document, which tells how the edge forms again: the preflight refuses a
network sent with no tenant that the newly formed edge does not hold, once the replay has more than one tenant, as
[A network sent with no tenant](#a-network-sent-with-no-tenant) describes. The network document's own checks (its
validation, the backend checks, and the interfaces) and the NFS and continuity checks need the new edge, and run only at
the replay, as do its host-managed checks apart from the one on tenant networks that the next paragraphs give.

The replay also decides which tenant each network is bound to. A network listed under a `qumulo_tenants` entry's
`networks` is that tenant's. One in `qumulo_network.frontend_networks` is the tenant its `tenant` names, and tenant 1's
for `tenant_id: 1`. A listed network sent with no tenant comes back in tenant 1, or the preflight refuses it; [A network
sent with no tenant](#a-network-sent-with-no-tenant) says which. When the inventory sets neither
`qumulo_network.frontend_networks` nor `networks` on any present `qumulo_tenants` entry, not even as an empty list, the
replay keeps the newly formed edge's networks, which are all tenant 1's, and does not replay a network the formed edge
lacks. Once any network is listed, the replay keeps only the listed networks and removes the rest, so the listing has to
hold every network the edge keeps: under its tenant, in `qumulo_network.frontend_networks` for a network no tenant holds
or on the backend bond, and as `networks: []` on a present tenant that holds none. A network the old edge binds to a
tenant the replay builds again, that the replay would not bind to it, is drift in the `tenant_networks` area, since the
inventory does not declare that binding; a tenant that is itself drift is reported without its networks.

An edge on host-managed networking forms again with no frontend network, and the baseline creates tenants before it
writes the network document, so on its own it would refuse the second tenant: with two tenants and no frontend network,
REST would listen on no network. So when the replay creates a tenant on such an edge, it first runs the baseline's
tenancy step with tenant 1 alone, holding tenant 1's own networks. Those are its entry's `networks` and the networks in
`qumulo_network.frontend_networks` that step 2 resolves to tenant 1 or to no tenant, such as a `tenant_id` the newly
formed edge lacks. Each is sent with no tenant, so each lands in tenant 1. The baseline then creates the other tenants,
and its step 3 adds their own networks and binds each network to its tenant. Step 2 places the play on the one HOST
network the pass leaves, from the newly formed edge's network status, so tenant 1 on HOST 0 beside a second tenant on
its own HOST 1 needs nothing inferred from the old edge when the play reaches the edge on tenant 1's network. A play on
the second tenant's network is not on the network step 2 places it on: the preflight refuses a
`qumulo_management_network` that names the second tenant's network, and with the variable unset step 2 takes tenant 1's
network for the play's ([Rebuilds](../reference/troubleshooting.md#rebuilds) in the troubleshooting reference has the
remedy). The remedy of reaching the edge at an address on a network the pass writes does not apply when tenant 1's REST
switch is off (`rest_api_enabled`, or `web_ui_enabled` behind 443) or the controller has no route to tenant 1's
networks; such a rebuild is not supported yet. Where the newly formed edge doesn't answer its network status
(G-NET-APPLY), step 2 cannot place the play and refuses the create after the destroy unless `qumulo_management_network`
is set, and the preflight does not check that case. A host-managed cluster with two tenants declares the variable anyway
([Multitenancy](multitenancy.md)), and a rebuild keeps it when it names a network the pass writes.

Only when tenant 1 holds no network, as when it is declared absent, left undeclared or given `networks: []`, does the
pass send every network the inventory lists for a tenant that stays: those in `qumulo_network.frontend_networks` that
belong to no tenant or to a tenant that stays, and each present tenant's own `networks`. Every network then exists
before any create, and step 3 only binds each to its tenant. Tenant 1 is the tenant that holds them in the pass: the
entry renamed by `previous_name: Default`, or, when no present entry names it, as when it is declared absent, the
`Default` the edge forms with, which step 20a deletes, when the inventory declares it absent, after step 3 has bound the
networks. So an inventory that declares tenant 1 absent needs no rename. An inventory that lists no network for a tenant
that stays leaves the pass nothing to write, so the preflight, which decides with the replay's own rule, refuses it
before the drain, whatever `edge_rebuild_ignore_drift` says.

The pass writes two or more networks only when tenant 1 holds two or more, or holds none while the inventory lists two
or more. With two or more networks in tenant 1 after the pass, each is HOST, and the baseline's step 2 on the newly
formed edge may not be able to place the play's address among them. Then it needs `qumulo_management_network`. The old
edge can answer what the new one cannot, so, with the variable unset, the preflight reads the old edge. Where the pass
writes two or more networks, the replay creates a tenant and the edge forms host-managed again, the preflight places the
play's address on the old edge as the tenancy continuity rule does ([Multitenancy](multitenancy.md)): on the network
whose ranges hold the address, or, from the network status, on the document's one HOST network.

That covers an old edge with one HOST network where the inventory adds a second, as long as the old edge answers its
network status (G-NET-APPLY). It does not cover an old edge that holds two HOST networks and is reached on the host's
own address, nor one that doesn't answer its network status. There the variable is still needed. A floating
`qumulo_api_host` doesn't escape the refusal. A rebuild reaches the newly formed edge at `qumulo_api_host` before the
pass, and a host-managed edge that is newly formed or UNCONFIGURED has no Qumulo network, so it serves no floating
address; `qumulo_api_host` is a host address in a rebuild.

The network the preflight finds on the old edge is the one the replay needs because two things carry over to the rebuilt
edge. The rebuilt edge's ids are the inventory's, which the pass and the baseline's step 3 write. The host associates a
HOST network id with an interface when the node starts, and that association holds while the reinstall keeps the host's
interface setup. The preflight's binding-drift check holds the inventory's ids to the old edge's for every tenant that
stays.

The module returns it as `management_network`, which is `qumulo_management_network` when the inventory sets it, else the
inferred ids, else null. A null `qumulo_management_network` counts as unset, as the baseline reads it, so the inferred
network is used. The predestroy record keeps it as `management_network`, with the address the inference used as
`management_api_host`, which is `qumulo_api_host`. The replay takes the recorded network as `qumulo_management_network`
for the tenant 1 pass and the baseline when the inventory sets none, and only while `qumulo_api_host` is still the
recorded `management_api_host`. A reinstall that changes the address may land the play on another network, and a wrong
id could pass step 2's check on the wrong network. With another address, or a record without `management_api_host`, the
replay passes no network and step 2 decides, as it does for a baseline. The report's `host_vars_patch` carries the
network when the inventory lacked it, so later baseline runs have it too. The variable overrides the inference. When the
two disagree, the preflight warns and the replay uses the variable.

The preflight refuses when it has neither, before the drain and whatever `edge_rebuild_ignore_drift` says, because step
2 would refuse the create after the old edge is gone. The refusal gives the reason it cannot tell, which is one of
these:

- The old edge may not answer its network status (G-NET-APPLY), or its network status read failed.
- The old edge may hold two HOST networks while the play reaches it on the host's own address, which no network's ranges
  hold. The rule places that address from the status only on the document's one HOST network, since it does not read
  the network ids the status reports. For an address the status doesn't show, it places the address on no network.
- The HOST network it would name may be one the status shows served on no device, as step 2 would refuse it.
- `qumulo_api_host` may resolve to no address.

Where the pass writes two or more networks, the play may instead reach the old edge on a network the pass does not
write: network 0, say, which the inventory lists for no tenant that stays, or lists under a second tenant. The
baseline's step 2 judges the first create against the document the pass leaves, and step 3 adds the other networks only
after that create, so step 2 would refuse the create of the tenant after the destroy. The preflight refuses this with a
message of its own, not one of the reasons above, because setting `qumulo_management_network` to 0 would meet the
refusal of a variable that names a network the pass does not write. Where the inventory lists network 0 under a second
tenant, listing it under tenant 1 would instead be `tenant_networks` drift for a network the old edge binds to another
tenant. The [Rebuilds](../reference/troubleshooting.md#rebuilds) rows of the troubleshooting reference give each message
and its remedy.

Whenever the pass runs and the replay creates a tenant, the preflight also refuses a `qumulo_management_network` that
names a network the pass does not write, however many networks the pass writes. Step 2 judges the first create against
the document the pass leaves, so a network that is not in it is not on the cluster there. There are two cases:

- An id that no tenant lists, such as a typo, or the old edge's id for a network the inventory lists under another id.
- An id that step 3 adds later, such as a second tenant's own network, which step 3 adds only after the create.

The preflight refuses one declared with no network as well, `qumulo_management_network: []`, which step 2 refuses at the
create after the destroy. Each case has its own message, and its remedy, under
[Rebuilds](../reference/troubleshooting.md#rebuilds) in the troubleshooting reference.

Renaming tenant 1 to the tenant that holds the HOST network, with `previous_name: Default`, is still valid, and it keeps
that network, the one the play reaches REST on, in tenant 1 through the rebuild instead of moving it at step 3. A rename
leaves no `Default` entry to carry claims on the factory share and export, so the claims move to
`qumulo_factory_defaults`, which says for each factory item, a share named `Files` in any letter case and an export at
`/`, whether the renamed tenant keeps it. [Rebuild an unprotected
edge](../how-to/edge-rebuild.md#check-the-inventory-against-the-edge) says how to set each.

On that same edge the replay refuses a tenant network that takes a VLAN or has STATIC addressing, since the host manages
the networking and a tenant network is HOST. That is the baseline's step 2, which would refuse it only after the old
edge is destroyed, so the preflight makes the same check as soon as it has read the edge's network document, before
drift, and `edge_rebuild_ignore_drift` does not waive it. The replay's own rules cannot make it, since they model a
newly formed edge, and only the old edge's network document says whether the new one forms host-managed. The check
waits for the replay when `qumulo_network` declares `managed_interfaces` and the tenant 1 pass runs, since the pass
writes them first.

Terraform ownership can make that refusal impossible to satisfy as it stands. With `network` in
`qumulo_terraform_owned_areas`, the baseline's ownership check refuses `qumulo_network` and a `networks` key under a
tenant, so the inventory cannot list the HOST network the pass needs while Terraform owns the area. The preflight, which
the role gives the areas Terraform owns, ends its refusal with the way out. The way back has an order: the ownership
check refuses the listing as soon as `network` is in `qumulo_terraform_owned_areas` again, so the listing and the
ownership have to change together. [Rebuild an unprotected
edge](../how-to/edge-rebuild.md#check-the-inventory-against-the-edge) has the steps.

An old edge can lack tenant 1, as after its owner deleted it. A newly formed edge always has tenant 1, `Default`, with
the factory `Files` share and `/` export, and the replay keeps it unless the inventory declares it `state: absent`.
Capture declares a missing tenant 1 absent: it writes `Default` with `state: absent` and the factory share and export as
`absent`, so a rebuild from that inventory drops them. On host-managed networking it can write the rename above instead,
`previous_name: Default` on the one tenant that holds a network and no `Default` entry, which keeps the HOST network in
tenant 1 through the rebuild, where with `Default` absent the pass puts it in `Default` and step 3 moves it. A factory
key of that capture is `present` where the capture writes the item for that tenant, since the rebuilt edge's tenant 1 is
that tenant and `absent` would claim the item twice. A `/` export that capture leaves out, as one whose mapped user is
gone, shows as drift under either form. The rename, though, would have the rebuilt tenant 1 keep the factory `/` export
in its place, so a rebuild that waives the drift with `edge_rebuild_ignore_drift` would end with the factory export,
which the preflight does not report. Capture therefore writes `Default` absent, and a rebuild that waives the drift ends
with no `/` export. Capture writes no entry when another tenant is named `Default` or a replay of what it wrote could
not delete `Default`. Its `tenant 1` note in `left_out` says which of these it did; [What capture and clone read and
write](../reference/capture-and-clone.md) and the `capture_info` module's `tenants` area give the cases. A network no
tenant holds always gets the entry, so the preflight refuses the inventory until the network has a tenant that stays,
since the rebuilt edge would otherwise refuse it at its baseline's step 2, after the old edge is gone. An inventory
written by hand, or captured before this rule, can lack the entry too. Deleting a tenant is its entry's `state: absent`,
and the role removes the tenant's declared and claimed content first. So for an inventory without the entry, the
preflight treats the tenant the rebuilt edge would gain as drift, with `added: true`, and `edge_rebuild_ignore_drift`
accepts the gain as it accepts a loss. The inventory clears it by saying what becomes of the tenant the rebuilt edge
would gain: `Default` declared absent, its factory share and export claimed for removal, and each network listed under a
tenant that stays. [Rebuild an unprotected edge](../how-to/edge-rebuild.md#check-the-inventory-against-the-edge) has the
keys.

### A network sent with no tenant

A network in `qumulo_network.frontend_networks` is sent with no tenant when it has no `tenant`, or has a `tenant_id`
that the newly formed edge lacks. The baseline's step 3 then sends the network document without a tenant for it, and the
cluster keeps the tenant it already gives that network. On a newly formed edge that is tenant 1, for a network the edge
holds when step 2 runs. A network it does not hold is new, and step 2 refuses a new network with no tenant once the run
has more than one tenant, counting the tenants it creates and those it deletes only at the end, since the cluster would
hold it in no tenant.

Step 2 runs after the old edge is gone, so the preflight refuses the same inventory before the drain. It needs to know
which networks the formed edge holds, so it reads the old edge's network document first, and the refusal comes straight
after that read, before any other read and before drift. The message is step 2's, "... is a new network with no tenant,
but this run has more than one tenant ...", with the prefix "the replay onto the rebuilt edge would refuse the
inventory", and `edge_rebuild_ignore_drift` does not waive it.

The formed edge holds two kinds of network:

- The formation network, which is network 1 on managed networking and none on host-managed networking, where the edge
  forms with no frontend network.
- Each network the replay's tenant 1 pass writes before step 2. The pass runs only on host-managed networking today, and
  it writes every network sent with no tenant, so nothing is refused there; the check is a guard on what the pass sends.

A network that `qumulo_network.managed_interfaces` puts on the backend bond is not refused, but only when
`cluster_baseline_owned_parts` holds `interfaces`. Without it, step 2 keeps the formed edge's own interfaces, and the
inventory's backend bond does not apply, so such a network is refused like any other.

Where the refusal does not fire, a frontend network comes back in tenant 1 of the rebuilt edge. That is the case for
network 1 on managed networking, for any such network on host-managed networking, and for any such network when the
replay has one tenant at step 3. The old edge may have held the network in no tenant, and the preflight reports that
change as drift in the `tenant_networks` area, with `added: true`, `tenant` the name tenant 1 has after the replay's
renames, and `name` the network's id and name. A network on a backend bond, the old edge's or the inventory's when
`cluster_baseline_owned_parts` holds `interfaces`, takes no tenant and is not reported, nor is a listed network the old
edge lacks. Its `tenant` decides where it lands, tenant 1 or another tenant; [Rebuild an unprotected
edge](../how-to/edge-rebuild.md#check-the-inventory-against-the-edge) says how to name one. The network cannot stay in
no tenant on the rebuilt edge: the formed edge holds network 1 in tenant 1, the tenant 1 pass writes into tenant 1, and
a network sent with no tenant keeps the tenant the cluster gives it. Only `edge_rebuild_ignore_drift`, which waives the
report with the rest of the drift, followed by unbinding the network by hand after the rebuild, keeps it unbound.

With no network listed, in `qumulo_network.frontend_networks` or under a tenant, the replay keeps the formed edge's
networks, all in tenant 1. On managed networking that is network 1, so when the old edge holds network 1 in no tenant,
the preflight reports it in the same `tenant_networks` area, with `added: true`, and its message says that the
inventory lists no network. On host-managed networking the formed edge holds no network, so nothing is reported.

### What cannot be replayed

| Area | What it is | Why the replay cannot create it again |
|---|---|---|
| `network` | A static frontend network or a VLAN, when the inventory declares no `qumulo_network` | There is no declaration to replay. With `qumulo_network` declared, the replay applies it. |
| `kmip` | A key store other than the local one | The replay does not configure a key store, and the report lists it. G-KMS records that every write of the key store rotates the key, even when nothing changed. |
| `replication` | A relationship the inventory does not declare, any object relationship, and a declared one out of the edge whose record is ENDED or pending | The inventory cannot recreate what it does not name, and each peer keeps its own record of it (G-REPL-PEER). |
| `s3_keys` | Any S3 access key | The secret was shown once (G-S3-KEY). |
| `access_tokens` | Any access token | The secret was shown once, and the token dies with the cluster (G-TOKEN). |
| `local_data` | Any file, link or other object outside the portal's spoke roots, and any snapshot | The drain moves only the spoke roots' writes to the hub. The rebuild destroys the rest. |

Acknowledging `local_data` gives up the files and the snapshots together.

## What survives: the drain

The drain sends the writes in the portal's spoke roots to the hub.
It makes the hub's portal read-only until the edge's outstanding writes reach the hub, and then deletes both records.
Everything else on the edge, its local directories, the files in them and their snapshots, is destroyed with it.

That is why the preflight counts local data as unreplayable.
The check reads the edge at one moment, so a file a client writes to a local directory after the check is lost without a
word.
The clients that write there have to be stopped before the predestroy phase for the check to hold.

The role drains every portal the edge holds, declared or not, since the rebuild destroys the edge's writes either way.
A portal whose hub no host in the `qumulo_hubs` group names by `qumulo_cluster_uuid` cannot be reached, so its drain cannot run.
The role stops before it changes anything, unless `edge_rebuild_allow_data_loss` says to delete it without a drain.

The drain can fail halfway, so the role records its progress.
It writes the predestroy record after each portal, with `safe_to_destroy: false` until the phase ends, so a run that
fails later still records a drain that finished.
A rerun counts a portal as drained from that record.
If the drain finished and only the hub's record remains, in state DELETING, the writes are on the hub and a rerun only
waits for the record to go.
`edge_rebuild_allow_data_loss` forces the deletion of whatever is left, and what it loses is only the writes the hub
had not received.

## The first pass runs on a floor

A rebuilt edge forms unlicensed, with the smaller of its platform's capacity and 1 TB, and provisioning never shrinks
(G-EDGE-FLOOR, G-CLAMP-SHRINK).
The formation provisions that capacity before any clamp can be set.
So the baseline can never write a clamp more than a rounding tolerance below `min(1 TB, edge_cache_resize_floor_bytes)`,
whatever the license value is, and the role refuses such a clamp instead of failing to write it later.

The role refuses such a clamp as early as it can check it.
In a rebuild in one run, it comes before the drain.
In a rebuild in two runs, the predestroy run drains and destroys the edge and checks none of this, so the refusal comes
in the replay, before the formation.
Nothing is lost when it does, since the hub holds the data from the spoke roots.
`edge_cache_resize_floor_bytes` exists to describe a platform smaller than 1 TB.

The same reasoning explains the shape of the license step.
The replay is the first pass: the edge forms with its new UUID and registers with Nexus, and it holds no license for
that UUID yet.
The baseline therefore leaves the clamp for the license, and the second pass applies the license and sets the clamp.
An edge that declares `qumulo_capacity.license: none` expects no license, so its replay writes the clamp itself, and
[Licensing and capacity](licensing-and-capacity.md) explains that case.
[Licensing and capacity](licensing-and-capacity.md) explains why a license is a hand-off and not a step.

## The guard on the new edge

The replay writes the whole inventory to a cluster, so the first thing it needs is certainty about which cluster that is.
[cluster_guard](../../plugins/modules/cluster_guard.py) in `rebuilt` mode passes only a node that is UNCONFIGURED, or an ACTIVE unprotected edge that has the
new UUID and the inventory's name, no portals, and no SMB share or NFS export other than the factory ones.
That is the definition of a newly formed, empty edge, and nothing else is let through.
A working edge that someone pointed the playbook at, or the old edge that was not destroyed, fails the guard.

The replay connects as `admin` with the password the edge forms with, `edge_rebuild_admin_password` or else
`qumulo_api_password`, and with no token.
A new edge has exactly one user, and a token or another user in the inventory belongs to the old edge.
The new edge also serves a factory certificate nothing can verify (G-FACTORY-TLS), so the role can switch verification off
for the new edge only, until the baseline installs `qumulo_ssl`.
[Authentication](authentication.md#the-factory-certificate) explains why that is safer than `-e qumulo_validate_certs=false`.

The per-request identity guard keeps running under all of this.
It checks the old UUID during predestroy and the new one after formation, before any credentials are sent.
[Refusals and guards](refusals-and-guards.md#why-a-rebuild-refuses-so-much) lists the refusals this adds up to and why
none of them is optional.

### Terraform forms the new edge

When a Terraform root forms the edge, Terraform forms the new one too, and the inventory plugin then reads the new UUID
from the root's output.
The replay takes that UUID as the new edge's because it differs from the old one, and the guard checks it as it would a
typed `qumulo_new_cluster_uuid`.
A typed `qumulo_new_cluster_uuid` still wins over the inventory's.

On this path the report's patch holds only the previous UUIDs, because a UUID or a replication address in `host_vars`
would outrank the plugin's from then on, so the role leaves both to the plugin.
Terraform's state needs no fix here (G-TF-STALE), since Terraform made the cluster.

The same variable can carry a stale answer.
If the saved output still names the old UUID after the reinstall, the replay has no new UUID to check the formed edge
against, so the guard refuses it and says to save the output again.
Running the whole rebuild again at that point would run the predestroy phase against the new edge.
The way on is a replay on its own once the output is saved again, which
[A Terraform-formed edge](../how-to/edge-rebuild.md#a-terraform-formed-edge) describes.

A replay on its own, whose inventory UUID no predestroy record names, finds the old edge the other way: from the newest
predestroy record that says the edge was safe to destroy, of a UUID that no rebuild report has already replaced.
The report names that record as `previous_record`.

## A computer account that outlives the edge

With `qumulo_ad`, the cluster name is also the edge's computer account in the domain, and the name does not change in a
rebuild.
Two facts follow.

A join under a name that a live old edge still uses resets the account's password and cuts that edge off from the
domain.
So the replay refuses to join under the old name unless a predestroy record shows the old edge left with credentials, or
`edge_rebuild_old_edge_destroyed` says it is gone.
In a rebuild in one run, the role checks this before it drains anything.

A leave without credentials does not delete the account (G-AD-JOIN).
Only a leave with `join_user` and `join_password` does, and only then does the record say the old edge left.
The report tells you which happened.
If the leave deleted the old account, its group memberships and delegations went with it and have to be granted again.
If it was reused, they may be intact, and the report says to check them.

[Identity](identity.md) covers what the collection can see of the domain.

## What a rebuild does to replication

A rebuild keeps every replica the inventory declares.
Before the drain, the role reads each peer's record of every relationship the edge holds, through the peer's own
inventory host, and the predestroy record lists each relationship with what the rebuild will do with it.
[Replication](replication.md) explains relationships, releases and the overwrite table.
These are the parts that belong to the rebuild.

- **The edge is a target.**
  A relationship from another host into the edge is replayable while the edge's record is ESTABLISHED, its directory is
  read-only, and the source's record of the same id is ESTABLISHED.
  The target directory and its snapshots are then not local data, because the second pass creates the relationship
  again from its source and a full sync fills the new edge.
  In any other state the directory is local data, and the preflight refuses until you acknowledge it.
- **The replay creates no relationship into the edge.**
  A full sync must not start into an edge whose license and clamp do not apply yet, and the first pass ends unlicensed.
  Each such relationship is a hand-off of the second pass, and until then its source has no replica.
  An edge that declares `license: none` is the exception: its replay writes its clamp, so a source's baseline run after
  the replay creates the relationship.
- **The edge is a source.**
  The rebuild destroys the source directory, so the replica on the target may be the only copy of its data.
  The target's record of the relationship is the server's own lock on that replica, and the collection keeps it.
  The replay's baseline holds the relationship and writes nothing on either cluster, and `edge_rebuild_allow_data_loss`
  never lifts the hold.
  The way out is a restore, a copy back and a release, or a release that keeps the replica as plain data, and
  [Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md) covers each.
- **A record from before a peer's own rebuild.**
  A record that names a previous UUID of the peer was made before the peer was rebuilt.
  With the edge as its source, the relationship is held, and there may be no replica to restore from.
  With the edge as its target, its replica may be the only copy of the data the source had before.
  The edge's record is also the hold that keeps the source's own relationship held, and rebuilding the edge destroys
  both.
  The preflight calls this outcome `only_copy`, lists the directory as local data whatever it holds, and requires both
  `local_data` and `replication` to be acknowledged.
  When the edge's record still allows the source's own restore, the preflight names that restore first, and once it is
  done the relationship is replayable.
  Otherwise the second pass creates the relationship from the source directory as it is, so copy back first any copy of
  that data you need.
- **A peer whose UUID the inventory does not hold.**
  This happens on the Terraform path before the peer's own patch is applied.
  A record whose peer UUID the peer's inventory host does not hold, current or previous, needs `replication`
  acknowledged, and the preflight says to apply that patch.

## Why the order is what it is

Most of a rebuild's checks and writes sit where a mistake first becomes irreversible.
These are the placements that are not obvious.

| Placement | Why |
|---|---|
| The inventory-only checks run before the drain in a rebuild in one run. | They read only the inventory and the records on the controller. A refusal there leaves the old edge untouched. |
| The hub's record of the old UUID is deleted before the edge forms. | A refusal leaves the node UNCONFIGURED, so the replay can run again. |
| The old UUID is retired in Nexus before the edge forms. | A formation that fails then leaves no old UUID behind in Nexus. |
| The replay writes the report as soon as the edge has its new UUID, and again at the end. | A replay that fails later still leaves the UUID and the patch behind. |
| The report is written however the baseline ends. | A failed step still leaves it naming each S3 key the last step made, with its secret's file, and each key entry that failed. |
| The role retires the old UUID in Nexus twice when it has a token. | Retiring, which [nexus_cluster](../../plugins/modules/nexus_cluster.py) does by marking the UUID decommissioned in Nexus, needs the credentials of a reachable cluster, even when the cluster that had the UUID no longer exists (G-NEXUS-RETIRE). Predestroy has the old edge. The replay has the new one. |

The hub's record of the old UUID is treated by its state, because the replay has to tell a lost edge from one that is
still up.
A PENDING record was never accepted and holds no writes, so it goes at once.
A DELETING record goes at once when a predestroy record shows its portal was drained.
A DISCONNECTED record needs `edge_rebuild_allow_data_loss`.
A record the hub still reaches means the old edge is up, and the replay stops.
Right after a lost edge is reinstalled, the hub may still show its portal connected, so the replay stops until the hub
reports it DISCONNECTED.

## What the report hands off

The replay ends with a report, `rebuild-<UTC>.yml`, whose `hand_offs` list is what a person has to do.
Each item is there because the collection either cannot do it or must not guess.

| Hand-off | Why it is left to you |
|---|---|
| `host_vars_patch`, applied first | Until it is applied, the inventory names the old UUID, or none of the edge's previous ones. The role switches the UUID in memory only. |
| The license for the new UUID, and the second pass | The collection never creates a license and never waits for one (G-LICENSE). |
| A capacity cap that waits for a quorum formation | A raised cap applies at the next quorum formation (G-LICENSE), and the report says so. |
| A spent Nexus join key, and the old UUID in Nexus | A join key registers one cluster only (G-NEXUS-KEY). Without an API token the rebuild cannot retire the old UUID (G-NEXUS-RETIRE). |
| The edge's DNS A and PTR records | They live outside the cluster, and the role does not manage them. |
| Terraform state that still names the old UUID | The collection does not edit another tool's state (G-TF-STALE). |
| The computer account's group memberships and delegations | They belong to the domain. The leave deleted them with the account, or the old account was reused and they may still stand (G-AD-JOIN). |
| A hub record of the old UUID that predestroy could not reach | The replay deletes only the declared hub's record. The record on any other hub is deleted by hand, with `portal_hub`, `state: absent` and `allow_data_loss` (G-PORTAL-STALE). |
| Each relationship into the edge, and each held one | They wait for the second pass or for a restore. |
| The source hosts whose replication fails until the patch is applied | The sources still expect the old UUID. |
| `admin`'s password on the new edge, when `edge_rebuild_admin_password` differs from `qumulo_api_password` | The new edge formed with it, and an inventory that connects as `admin` must use it. |
| Each lock key the old edge held | A rebuild does not carry a key: the cluster registers one only with its name signed by its private key, and returns neither. Register each again with `security_key`, from its private key or a signature of its name. |
| The old edge's key store, when the predestroy phase could not read it | The record then names no lock key, and the report says so. Register each key the inventory's snapshot policies declare. |
| Each snapshot policy the baseline applied without its lock key | The key was not registered when step 20 ran, and `qumulo_allow_deferred_policy_lock_keys` accepted the policy without it. Its snapshots are not locked until you register the key and run the baseline again, which sets it. |
| Each acknowledged area | Tokens to issue again to their consumers (G-TOKEN), S3 keys to hand to theirs (G-S3-KEY), replication the inventory does not declare (G-REPL-PEER), the key store (G-KMS), the network, and local data. |

The list is the other half of the acknowledgements: what you agreed to lose before the drain comes back as a thing to
do after it.

## Why the records persist

The role keeps its records in the edge's artifacts directory, beside the inventory
([The artifacts directory](architecture.md#the-artifacts-directory) says where and why).
They are the continuity between phases that may run hours or jobs apart.

- The predestroy record says an edge is safe to destroy, and a replay on its own reads it for the old edge's UUID.
- The report's `held` list names the relationships the replay held, and the restore reads it.
- The baseline and the second pass write stamps that retire those holds, and the second pass writes a restore record and a lock while it restores.

Losing the directory is not neutral.
A runner discarded after a restore deleted the old record and before its last step strands the pair, with the record that
says how to finish it gone.
A lost stamp leaves the report's claim armed, so a later second pass fails where it would only warn.
A runner without the directory has no report, so it warns where a stranded restore should fail.
So the directory has to persist across every job that runs the replay, the baseline or the second pass.
[Run the playbooks from a pipeline](../how-to/pipeline.md) says how.

## Learn more

- [Rebuild an unprotected edge](../how-to/edge-rebuild.md) gives the steps, including the Terraform variant.
- [Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md) covers the relationships a rebuild
  holds.
- [License an edge](../how-to/licensing.md) and [Size and resize an edge's cache](../how-to/edge-sizing.md) cover the
  second pass and the clamp.
- [Licensing and capacity](licensing-and-capacity.md) and [Replication](replication.md) explain the two subjects this
  page touches.
- [Refusals and guards](refusals-and-guards.md) explains why the rebuild refuses so much.
- The [gap register](../reference/gaps.md) lists the ids cited above.
