<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Tenants and the continuity rule

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

A tenant is a named set of a cluster's networks with six service switches, and a cluster with more than one tenant
serves each service only where its tenant allows it. That rebinds every listener on the cluster, and a mistake in it
cuts off the controller that made it. This page explains what a tenant partitions, why creating the second tenant is
different from creating the third, why the network document holds every binding, and the rule by which the collection
refuses a write that would cut its own connection. The reasons for several refusals and for the shape of capture and
clone follow.

To do the work, see [Manage tenants](../how-to/tenants.md). The variables are in
[Inventory variables](../reference/variables.md#tenants) and the messages in
[Troubleshooting](../reference/troubleshooting.md#tenants).

## What a tenant partitions

A tenant holds networks and decides which services answer on them. The six switches are `web_ui_enabled`,
`rest_api_enabled`, `ssh_enabled`, `replication_enabled`, `nfs_enabled` and `smb_enabled`.

| Scoped to a tenant | Cluster-wide |
|---|---|
| Whether the web UI, REST, SSH, replication, NFS and SMB answer, on the tenant's networks | Time, the certificate, monitoring, capacity, quotas, snapshots and replication relationships |
| Each SMB share and NFS export, whose name or path is unique within the tenant, not across the cluster | Local users and groups, roles and their members, which cannot be limited to a tenant |
| An optional override of the SMB settings and of the NFS settings, which replaces the cluster's settings for that tenant | S3 and FTP, which answer on every network |
| | The portal, which listens on TCP 3713 whatever the tenant |

Management is not scoped. A client that reaches REST, the web UI or SSH on any tenant sees and changes the whole
cluster, so a tenant's `rest_api_enabled` decides where management answers, not what it reaches.

A tenant's override is the tenant's own full copy of the settings, as the cluster keeps it. Once it exists, later
changes to the cluster's settings do not reach it, and an override equal to the cluster's settings is still an override.
Declaring `inherit` removes it, and leaving the key out leaves it as it is.

Per-tenant identity, a tenant with its own DNS configuration and Active Directory domain, is not supported yet. The
`identity` key of a tenant entry is reserved for it, and the baseline refuses it.

## The second tenant changes how every service listens

With exactly one tenant, every service that tenant enables listens on all interfaces, and a network is as good as
another. With two or more, each service listens only on the interfaces of the networks whose tenant enables it. The
switch happens at the create that makes the second tenant, even when that tenant has no network and no service on, and
it is undone at the delete that leaves one. It is cluster-wide: the Default tenant's services rebind too.

The switch has four effects.

- **Connections end.** Every listener on every node rebinds. NFS and replication connections end at once, SMB and REST
  connections drain, and the web UI closes its own. After a network moves to another tenant, SMB connections keep their
  old tenant until the client reconnects. `tenant` warns of the rebind in the run that makes it and in a check run that
  predicts it (G-TENANT-LISTEN, [Gaps](../reference/gaps.md)).
- **REST narrows.** After the switch, port 8000 answers only on the networks of tenants with `rest_api_enabled`. REST
  also listens on loopback and on a local socket, which a controller cannot use. With two or more tenants, a cluster
  where no tenant with REST enabled holds a network, as when the Default tenant holds none, answers a controller on no
  network at all.
- **Replies need a route.** A listener bound to an interface sends its replies out of that interface. A tenant on a
  VLAN with no gateway of its own serves only clients on the VLAN's subnet, and a client on another subnet gets no reply
  unless a route out of the VLAN to it exists. Qumulo Core takes one IPv4 and one IPv6 default gateway on chassis nodes,
  and a client cannot tell a chassis cluster from another, so `network_config` and step 2 of the baseline refuse a
  second STATIC network with a gateway of one IP version on every cluster. A tenant for routed clients needs static
  routes, which the collection does not manage.
- **Static routes go.** Creating a tenant, and binding a network to a tenant, removes the static routes of every node,
  and an operator must apply them again (G-TENANT-ROUTES). `tenant` and `network_config` warn of it.

A replication target answers only on the networks of tenants with `replication_enabled`, so a source's declared address
must lie on one. The baseline checks the tenant that serves each target's `qumulo_replication_address` for that reason.

## Why the network document holds every binding

A network belongs to at most one tenant, and the binding shows from both sides: the tenant lists its networks, and the
network names its tenant. The collection writes it from one side only. The network document, which
[`network_config`](../../plugins/modules/network_config.py) writes with one PUT, carries each frontend network's tenant,
and the tenant module never sends `networks`.

The reason is what the PUT does with a network it receives without a tenant. With one tenant the network goes to that
tenant. With two or more it goes to no tenant, serves no client, and nothing on the cluster says so. A tenant PATCH that
moves a network, followed by a network PUT that carries the old tenant, moves it back. Only one writer can own the
binding on the wire, and the collection makes it the network document.

Every writer of that document therefore sends every network's tenant. The collection's own PUT carries over the tenant
the cluster reports for each existing network, so it never unbinds one. The writers that do are the others: an operator
in the web UI or with `qq`, and a Terraform root whose apply omits the tenants. Once two tenants exist, each of them
leaves the networks it sends unassigned. The next baseline binds again each network the inventory lists under a tenant.
A network the inventory leaves out of every tenant stays unassigned, and capture notes it.

A network on the backend bond is the exception. The tenant API refuses one, but the network PUT accepts a tenant on it,
and with one tenant puts every network sent without a tenant, backend ones included, in that tenant. Once two tenants
exist, a tenant holding a network with no frontend interface aborts every node at each quorum start (G-TENANT-BACKEND).
The collection therefore never sends a tenant for a backend network, refuses to declare one, and refuses to create the
second tenant while any tenant holds one.

The tenant API also checks what the network PUT does not: the networks of one interface or VLAN belong to one tenant,
and two tenants cannot share a VLAN. The collection makes the check, so on managed networking two tenants need at least
two interfaces, the untagged bond and a VLAN, or two VLANs.

## The continuity rule

The collection reaches a cluster through one of its networks. A write that leaves that network in no tenant that serves
REST ends the controller's connection in the middle of a run, with the rest of the run unapplied. The continuity rule
refuses such a write before it is sent.

The rule judges four writes: a tenant create, a change of `rest_api_enabled` or `web_ui_enabled`, a tenant delete, and a
network PUT. It judges each on the network and the bindings as they will be immediately after that write, and it
requires that the network the play connects on belongs to a tenant with REST enabled, and that at least one tenant
remains. A rename, and a change of any other switch, cannot cut the connection, so neither is judged. A run that makes
none of the four is not judged. `qumulo_allow_management_disruption`, or `allow_management_disruption` on a module run
alone, skips the rule for the whole run, and the operator then carries the risk.

With one tenant after the write, every service listens on all interfaces, so the rule asks only that the tenant has
REST enabled and never looks the network up. A cluster behind a load balancer needs nothing more, unless the port the
play connects on, or the port the balancer maps it onto, is 443.

With two or more tenants after the write, the rule needs the management network: the set of networks the play reaches
the cluster on. The collection finds it in one of three ways, in this order.

1. The inventory declares it. A play behind NAT or a load balancer must, since only the operator knows what the address
   is forwarded to. `qumulo_management_network` holds a network id, or two for an address that has an IPv4 and an IPv6
   network. Network id 0 is a real id.
2. Otherwise the collection resolves `api_host` to its addresses and takes, for each one, the network whose node or
   floating address ranges hold it in the document in force after the write.
3. For an address no range holds, as a HOST network's address is, the collection reads the cluster's network status,
   which places the address on a network.

The management network is undetermined when `api_host` resolves to no address, when no network carries any of its
addresses, when the declared id is not a network of the document, or when status is needed and unavailable. A write
judged while it is undetermined is refused with a message that says which case applies and what to set.

### Status, and why a declared network is trusted without it

`GET /v3/network/status` answers HTTP 500 on some clusters whose network the host manages (G-NET-APPLY). The collection
reads it only where the rule needs it, and treats a 500, or a status the cluster does not offer, as unavailable instead
of failing the run. With status unavailable, a declared management network is trusted, since the declaration is the
operator's word, as it is behind NAT. An undeclared address that ranges place on a STATIC or DHCP network needs no
status. An undeclared one that no range holds, or that ranges place on a HOST network, is refused with a message that
names `qumulo_management_network`.

Status is read in two places only: at step 2 of the baseline, once, when the cluster has two or more tenants or the run
creates one, and by `tenant` or `network_config` run alone, for a judged write that leaves two or more tenants. The role
passes the management network to every writer it runs, so a writer the role runs never reads it. A cluster that stays
single-tenant never has its status read, which is why a CNQ cluster that stays single-tenant is never read.

### HOST networks

On networking its host manages, every network has type `HOST`, and a HOST network serves only on an interface the host
associates with the network's id. A node reads that association when it starts. With one tenant this never shows,
since every service listens everywhere. With two it does: a HOST network the host does not associate is skipped, and its
tenant's services listen on no interface for it. The collection therefore checks, whenever status is available, that a
HOST network shows on a device of some node, and it refuses a second tenant on a cluster that has no frontend network
yet, because the REST listener would then be on no network. The first run declares the HOST network under one tenant,
and the second tenant follows once the network is applied.

The check has a limit. What status shows is that some HOST network is served by an entry assigned by HOST, not which one
(a limit of the cluster's status report). With one HOST network that is enough. With two it passes once
either is served, and status places an address that no range holds on a HOST network only when the document has exactly
one. A declared HOST network beside a second one therefore passes on the operator's word, and an undeclared address with
two HOST networks on the cluster is refused with the message about NAT or a load balancer. The operator of a
host-managed cluster with two tenants declares `qumulo_management_network`, whatever status answers. When status is
unavailable and the operator declares the network, the check is skipped.

### The web UI port, and the port 443 case

The web UI listener on TCP 443 forwards requests under `/api` to REST over loopback. Wherever a tenant has the web UI
on, the API answers on 443 even with `rest_api_enabled: false`. The collection's client calls the port in
`qumulo_api_port`, 8000 by default, so "REST enabled" means `rest_api_enabled`. When the port the cluster listens on for
the play, `qumulo_cluster_port`, is 443, the play reaches REST through the web UI listener and the rule looks at
`web_ui_enabled` instead.

A play whose client port is 443 and that declares no cluster port cannot tell which of the two listeners it reaches,
since a load balancer may map 443 to 8000. The rule then fails closed and asks for both switches on the tenant that
holds the management network, or on the only tenant. That covers a network PUT on a single-tenant cluster too. A play
whose client port is mapped onto cluster port 443 by a tunnel or a balancer therefore declares
`qumulo_cluster_port: 443`; with any other client port the cluster port is taken to be the client port.

### Planned bindings in check mode

A check run changes nothing, so the tenants and networks that the run would create do not exist. The role judges each
write on the bindings as that write would leave them, by passing the planned networks and tenants to the writer. The
check of a run that moves the management network to a tenant with REST on, and then turns REST off on the old one,
therefore passes, as the real run does, because the move comes first. A module run alone judges the cluster as it is
and its own input.

## Why the baseline removes a deleted tenant's content first

Deleting a tenant on the cluster also deletes its shares, its exports and its overrides, and leaves stale entries in
the export tree that can abort a node in a later NFSv4.1 check. The tenant's version tag does not change when a share
is added, so a delete races with a write that it cannot see (G-TENANT-CASCADE).

The collection therefore never relies on the cascade. The `tenant` module refuses a delete while the tenant holds a
network, a share or an export. The baseline removes what an absent tenant's entry declares, and the pairs shared entries
claim for it, before the tenant: its networks at step 3, its shares and exports at the start of steps 18 and 19, its
overrides, and then the tenant, at step 20a. It refuses at step 2 a tenant declared absent that holds anything no entry
declares or claims, so no share or export is lost to a delete the operator did not read.

The same ordering makes a delete one line. A shared entry whose tenants include the absent one, or are `all`, claims
that pair for removal, so no shared entry is edited when a tenant goes.

## Why no cloud cluster has a second tenant

Qumulo Core refuses every tenant write on AWS, Azure, GCP and OCI, so a cloud cluster keeps its one tenant
(G-TENANT-CLOUD). It also refuses STATIC networks and VLANs there. A Cloud Native Qumulo cluster behind a multi-AZ load
balancer is therefore the single-tenant case by construction, and the rule asks it only that its tenant has REST
enabled.

Step 2 of the baseline refuses every tenant write the run would send to a cloud cluster, before any write, so the
inventory fails where the cluster would. It knows a cluster is cloud from `qumulo_cluster_kind: cnq` or, with the kind
unset, from an object-backed protection type, so an unset kind falls back to the protection type for tenancy. An
on-premises cluster whose storage is object-backed declares `qumulo_cluster_kind: baremetal`. The kind has a second use
on a cluster the Terraform provider formed, where an unset kind counts as `cnq` for the areas Terraform owns by default
([Terraform ownership](terraform-ownership.md)), so declaring it settles both. Reads, capture and check mode work on
every cluster.

## The DHCP rule

A managed network document that holds a DHCP network holds no other network: the cluster allows one DHCP network and,
when it is used, no other (G-NET-DHCP). The rule is on the document, not on the order of changes, so one PUT may replace
a DHCP network with a STATIC one and add a VLAN network in the same write. A second tenant's VLAN needs STATIC
addressing, so a cluster whose untagged network is DHCP moves it to STATIC. The move drops the address the play connects
to, which `qumulo_allow_management_disruption` and `qumulo_api_host_after` carry across, and that consent skips the
continuity rule for the whole run. The how-to therefore makes the move in a run of its own, while the cluster has one
tenant, and adds the tenant in a later run that the rule still guards.

## Why capture writes tenants by name, and nests what they hold

A tenant's id is generated and never reused. A tenant deleted and created again gets a new id, and a rebuilt cluster
numbers its tenants from the start. The collection therefore keys a tenant on its name, and an id never appears in an
inventory that capture writes.

Capture writes `qumulo_tenants` on every cluster that offers the tenant list, one with a single tenant included. Each
entry holds the tenant's switches, its networks, its settings and the shares and exports it holds, so what a tenant
serves is declared where the tenant is. A single-tenant cluster is a cluster with one entry, and adding a second tenant
to its inventory needs no change to the entries it already has. The factory share and export stay in tenant 1, and a
tenant 1 that was renamed is written with `previous_name: Default`, so that a replay onto a newly formed cluster
renames that cluster's `Default`. A cluster that has no tenant 1 is written as a `Default` entry with `state: absent`,
or, on networking the host manages, as the same rename for the one tenant that holds a network;
[Rebuilds](rebuilds.md) says why.

Two things go elsewhere. A share or export that is identical in several tenants is written once, in the flat list, with
`tenants: all` or the list of tenants, so a copy that the operator keeps in step is declared once. A network that the
backend bond lists, or that no tenant holds, is written in `qumulo_network.frontend_networks`, since a tenant cannot
hold the first and no tenant holds the second. Capture notes a backend network that a tenant holds on a multi-tenant
cluster and an unbound network on one. The baseline of an inventory it wrote shows no change in the ordinary case: where
the cluster already holds a layout the baseline would refuse in a new document, such as two tenants on one VLAN, it warns
and goes on. A VLAN that carries a tenant's network and an unbound one is the exception, which the check run refuses as
declared in two places. A rebuild whose replay has several tenants refuses, in its preflight, an unbound network that the
newly formed edge lacks (on managed networking, any network but 1; on host-managed networking, none, since the replay's
tenant 1 pass writes every network sent with no tenant first), and otherwise the rebuild puts it in tenant 1, which the
preflight reports as drift;
[A network sent with no tenant](rebuilds.md#a-network-sent-with-no-tenant) says when.

## Why a clone drops a tenant's networks

A clone copies one cluster's configuration onto another, fresh one. The networks of a tenant hold the source's own
addresses, as the network document does, and the clone drops `qumulo_network` for that reason. It drops every tenant's
`networks` for the same reason, and notes it once for each tenant that had any. It keeps each tenant's name,
`previous_name`, switches and settings, its nested shares and exports, filtered as the flat lists are, and the shared
entries.

On managed networking the first pass of the clone's baseline then creates the source's other tenants with no network,
and the operator declares the target's own networks afterwards. On networking the host manages, the first pass refuses
before any write because the target has no frontend network yet, and the operator adds its HOST network first. The
clone also refuses a target that is not fresh: one that already has a second tenant, or a share or export other than
the factory `Files` and `/`.

## Learn more

- [Manage tenants](../how-to/tenants.md): the tasks.
- [Inventory variables](../reference/variables.md#tenants): `qumulo_tenants`, `qumulo_management_network` and
  `qumulo_cluster_port`.
- [Troubleshooting](../reference/troubleshooting.md#tenants): every refusal of this page, with its remedy.
- [Refusals and guards](refusals-and-guards.md): why the collection refuses before a write that cuts the connection.
- [State management](state-management.md#keys-not-ids): why tenants are keyed by name.
- [Terraform ownership](terraform-ownership.md): what a Terraform apply that omits tenants does to the bindings.
- [Gap register](../reference/gaps.md): G-TENANT-LISTEN, G-TENANT-ROUTES, G-TENANT-BACKEND, G-TENANT-CASCADE,
  G-TENANT-CLOUD, G-NET-APPLY and G-NET-DHCP.
