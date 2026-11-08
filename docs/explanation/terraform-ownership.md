<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Terraform ownership

This page explains how Terraform and this collection share a cluster without writing the same setting.

## Two tools, one cluster

Terraform forms a cluster, and this collection configures it afterwards.
The Terraform provider also keeps writing some of what it formed.
On a cluster of kind `cnq` it applies the floating addresses again every time it runs.
With a `cluster_fqdn` set, it does the same for the cluster's DNS name and for whether the authoritative DNS server
answers.
A setting that two tools write is undone by whichever ran last, so the collection needs to know which settings are
Terraform's and must stay out of them.

The collection does not find that out for itself.
The baseline reads neither Terraform's state nor its configuration.
Ownership is something you declare in the inventory, and the collection acts on the declaration.
It does not check the declaration against the Terraform root, so a list that is wrong stays wrong until someone notices
the two tools undoing each other.

## One output is the only meeting point

The two tools meet in one Terraform output, `qumulo_ansible_inventory`, which the inventory plugin
[terraform](../../plugins/inventory/terraform.py) turns into hosts.
Three reasons make one output better than variables that you copy into the inventory.

- **Nobody copies an address or a UUID.**
  The root that forms a cluster is the one place that knows its address and its UUID.
  The output carries them, so a cluster that Terraform forms again appears in the inventory with its new facts on the
  next run.
  The plugin reads the output on each run unless a cache holds it, so the floating addresses, which the provider
  documents as able to change when a cluster scales or is reconciled, follow the next `terraform apply`.
- **The identity guard checks what Terraform recorded.**
  The UUID in the output becomes `qumulo_cluster_uuid`, and every module compares it with the cluster at the address
  before it sends a credential ([the identity guard](authentication.md#the-identity-guard)).
  An address that Terraform repointed at another cluster is then a failure that sent nothing.
- **The output carries a pointer, never a secret.**
  An entry names where the password lives in `password_ref`, so the output need not be marked `sensitive`;
  [Authentication, trust and secrets](authentication.md) explains why.

Beside the address, the UUID and the password's pointer, the output carries the two facts about ownership.
Nothing passes between the tools outside this output.
[Use a Terraform root's output as the inventory](../how-to/terraform.md) shows how to publish it, and
[The Terraform inventory plugin](../reference/terraform-inventory.md) lists its fields.

## What the two variables say

`qumulo_managed_by` records what formed the cluster: `none`, `terraform_provider` or `legacy_provisioner`.
The plugin sets it from the output's `managed_by`.
The baseline reads it in two places: with `terraform_provider` and no list given, the list defaults to
`[network.floating_ips]` unless `qumulo_cluster_kind` is `edge` or `baremetal`, and the network step passes it to
`network_config` as `managed_by`. An unset kind counts as `cnq`. Capture and clone derive the same
default, so a provider-formed CNQ host adopted with `qumulo_managed_by: terraform_provider` set is captured without
its floating ranges, and an edge or bare-metal one with them.
Adoption writes the variable only when the inventory leaves it unset.
The plugin also puts each host in a `qumulo_managed_by_<value>` group.
`none` and `legacy_provisioner` behave alike: neither gives the baseline an area on its own.

`qumulo_terraform_owned_areas` lists the areas that Terraform owns.
The plugin sets it from the output's `owned_areas`.
The baseline refuses to manage an area on the list, and the list is empty unless the inventory gives one or the cluster
is a provider-formed one of kind `cnq` or unset.
The names are those of the baseline's areas, which [Inventory variables](../reference/variables.md#area-map) maps to
the variables each guards.
Three of them name a part of an area, `network.floating_ips`, `dns.fqdn` and `audit.cloudwatch`, which the sections
below explain.

The plugin checks that each name looks like an area name, and that a `cnq` entry with `terraform_provider` lists
`network.floating_ips`.
It does not check a name against the baseline's areas.
A name the baseline does not know matches nothing in a baseline run, and `capture_info` fails on it, because its
`leave_out` accepts only the areas and parts it knows.
A misspelt area therefore protects nothing, and a capture or a clone is where it shows.

## Everything that is not listed is the collection's

The list names what Terraform owns, and the collection treats every other area as its own.
That includes the shares, exports, quotas, snapshot policies, the portal and the capacity clamp, which the provider's
default does not claim.
The collection cannot tell which of them a root also manages through its own resources, so the default is the one
thing that is always true of a provider-formed cluster of kind `cnq` or unset, the floating addresses, and the root's
author adds the rest.
A root that manages monitoring, the networks or a share too would otherwise fight the baseline over the same objects,
and its author lists that area.
[Use a Terraform root's output as the inventory](../how-to/terraform.md#declare-the-areas-the-root-owns) has the
steps.

## Why a declared area is refused, not skipped

When the inventory declares a variable that belongs to an owned area, the baseline fails at its ownership check, the
second step, before any step writes.
The message names each area and the variables that declare it.
The run could skip the variable and carry on, and it does not, for two reasons.

- **The inventory would say what the cluster does not hold.**
  A declared `qumulo_dns` that is silently ignored reads as a setting that applies.
  Someone who changes it, and sees a run succeed, believes the cluster follows.
- **A refusal leaves the cluster as it was.**
  The check runs before step 3, the first step that writes, so a run that is going to refuse changes nothing.
  [Refusals and guards](refusals-and-guards.md) explains why the collection refuses in preference to guessing.

The message offers two ways out, and they are different decisions.
Removing the variable leaves the area to Terraform.
Removing the area from the list gives it to the collection, and the list does not make Terraform stop writing it.
With the plugin the list is part of the Terraform output, beside the resources it describes, so a change that gives an
area up belongs in the root, together with the change that stops Terraform setting it.

## The floating addresses are Terraform's by default on CNQ

On a CNQ cluster the provider applies the cluster's floating addresses again every time it runs.
A collection that wrote them there would be undone on the next apply, and the two would differ without either failing.
The floating addresses are therefore Terraform's on a provider-formed CNQ cluster, and three rules say so.

- The plugin refuses an entry with `cluster_kind: cnq` and `managed_by: terraform_provider` whose `owned_areas` does not
  list `network.floating_ips`, and the baseline refuses a hand-written host with the same three facts. An edge or
  bare-metal entry may leave it out.
- Without a list in the inventory, the baseline defaults `qumulo_terraform_owned_areas` to `[network.floating_ips]` for
  a provider-formed cluster whose `qumulo_cluster_kind` is `cnq` or unset, and to nothing when it is `edge` or
  `baremetal`. An unset kind counts as `cnq`, the cautious reading.
- A list that the inventory gives rules. It is all that Terraform owns, an empty list included, and the network step
  manages the floating addresses unless the list names `network.floating_ips`.

To the baseline, the kind matters to the default, and to the refusal of a provider-formed `cnq` list that leaves out the floating addresses.
On an edge or bare-metal cluster the provider forms the cluster but does not apply the floating addresses again, so the
collection may manage them there.
The Terraform output's `owned_areas` states what Terraform owns, so the baseline follows a list the inventory gives,
and `qumulo_managed_by` does not override it.
A host that sets `qumulo_terraform_owned_areas: [dns.fqdn]` to add a part therefore hands the floating addresses to the
baseline, and lists `network.floating_ips` as well to keep them with Terraform; a provider-formed `cnq` host must list
it.
`network_config` takes no kind: told `managed_by: terraform_provider`, it refuses the floating addresses unless
`take_over_from_terraform` is set, so the network step sets it for the module when the list leaves them out.
Capture and clone read the same list as the baseline, with the same default.

The address is a part of the `network` area, not the whole of it.
With `network.floating_ips` owned, `qumulo_network` may still declare the networks and the interfaces.
The step keeps the floating address ranges the cluster holds, and a `floating_ip_ranges` in the declaration fails
before any request, because that part is not one the step manages.
A root that also configures the networks lists `network`, and then any `qumulo_network` is refused.

## Parts of an area: `dns.fqdn` and `audit.cloudwatch`

An area is the collection's unit of ownership, but the provider can write only a slice of one.
Two more parts are named for the same reason, because giving away the whole area would give away settings that Terraform does not
write.

**`dns.fqdn`** is the authoritative DNS server's name and whether it answers, that is, `enabled` and `fqdn` of
`qumulo_authoritative_dns`.
When the root sets the provider's `cluster_fqdn`, the provider applies both again every time it runs.
It writes nothing else of the server's settings, so `host_restrictions` and `record_ttl_seconds` stay the collection's.
Listing `dns` instead would hand over those two as well, and all of `qumulo_dns` with them: the DNS servers, search
domains and lookup overrides.
Unlike the floating addresses, `dns.fqdn` is never owned by default, since only the roots that set `cluster_fqdn` write
it.
Once a name is set the cluster cannot clear it (G-DNS-FQDN-CLEAR in the [gap register](../reference/gaps.md)).
A name Terraform set therefore stays, and `authoritative_dns` refuses an inventory's empty `fqdn` with a message that
points at listing `dns.fqdn`.
`authoritative_dns` refuses `enabled` and `fqdn` when its `terraform_owned_areas` lists `dns.fqdn`, before it sends a
request.

**`audit.cloudwatch`** is the CloudWatch destinations of `qumulo_audit_destinations`, which the provider can set on a
cluster in AWS.
Syslog and local destinations stay the collection's.
Listing `audit` gives all the destinations to Terraform.
A destination that the list leaves out is left alone in any case, so leaving the CloudWatch destination out of the
inventory is how the collection stays out of it.

The check for these two sits at two levels, and they differ on purpose.

- The baseline's check compares declarations.
  It refuses a declared entry of type `CLOUDWATCH` while `audit.cloudwatch` is owned, and any entry while `audit` is
  owned, even an entry that would change nothing.
  It runs at step 2, before the network, DNS and the other steps that come ahead of the audit step write anything.
  An entry without a `type` is left to the module, since the declaration alone does not say which type it names.
- The module's check compares effects.
  `audit_destination` refuses a task that would create, change or delete a CloudWatch destination, or change a
  destination to or from that type, while `audit.cloudwatch` is owned, and any write while `audit` is owned.
  A task that would change nothing passes, and so does `state: absent` for a destination that does not exist.
  It refuses in check mode too, before any write.

A module called on its own has no inventory to read.
Its `terraform_owned_areas` defaults to an empty list, so it refuses nothing until the task passes the host's list, as
the baseline passes its own, default included.

## What a take-over hands back

`qumulo_take_over_from_terraform: true` hands the collection what the provider writes, for one inventory or one run.
It covers four things: the floating addresses, `dns.fqdn`, `audit` and `audit.cloudwatch`.
It covers no other area.
Giving the collection `network`, `dns`, `monitoring` or any other area means taking it off the list, and the flag does
not do that.

The flag is one flag for every hand-over, so a take-over of the audit destinations hands back the floating addresses
too, and the refusals for `audit` and `audit.cloudwatch` say so.
The list stays as the output wrote it.
The cluster is still described as Terraform's, and the collection manages these settings anyway.

The take-over is explicit because it has a cost that the inventory cannot see.
If the Terraform root still sets these settings, its next apply writes its own values again, and the collection's next
run writes the inventory's back.
The flag says that the person running the playbook has weighed that, and a variable that is declared is not enough to
say so.
The same flag reaches `network_config`, `authoritative_dns` and `audit_destination` as `take_over_from_terraform`, which
defaults to `false` in each.

## Adoption and cloning leave the owned areas out

A capture writes the variables the baseline would read, so a capture of an owned area would write a file that the
baseline then refuses.
The capture playbook therefore passes the owned areas to `capture_info` as `leave_out`, and the file notes each area
that was left out.
With `network.floating_ips` owned, the network document is written without its floating address ranges.
With `dns.fqdn` owned, the authoritative DNS settings are written without `enabled` and `fqdn`.
With `audit.cloudwatch` owned, the audit destinations are written without the CloudWatch ones.
[What capture and clone read and write](../reference/capture-and-clone.md#areas-terraform-owns) lists them.

A host from the plugin is adopted for the settings Terraform does not hold, and adoption leaves out every variable the
plugin defines.
A `host_vars` file outranks the plugin's variables, so a copy of the UUID, the ownership or the addresses in it would
pin values the plugin refreshes from the output on each run.
For the same reason adoption writes `qumulo_managed_by: none` and `qumulo_terraform_owned_areas: []` only when the
inventory defines neither.
A hand-written `qumulo_managed_by: terraform_provider` therefore keeps its derived list, which follows the host's
`qumulo_cluster_kind`.
[Adopt a cluster you already run](../how-to/adopt-a-cluster.md#adopt-a-host-from-the-terraform-inventory-plugin) has the
steps.

A clone reads the same way, with the target's list.
It does not copy the source's `qumulo_managed_by` or `qumulo_terraform_owned_areas`, since they say what formed the
source, and the target's inventory says the same of the target.
It leaves out the source's network document, the authoritative DNS name and whether it answers, and the CloudWatch
destinations, because each holds a fact that is the source's own.
Terraform or you declare the target's.
[Clone an edge's configuration onto a new edge](../how-to/clone-edge.md) has the steps.

## Tenants and the network document

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

A tenant's networks are part of the network document, so the ownership check reads the keys inside `qumulo_tenants` as it
reads the variables outside it.
A tenant's `networks` is in the area `network`, and the `floating_ip_ranges` of those networks are in
`network.floating_ips`.
A tenant's SMB settings and shares are in `smb`, and its NFS settings and exports are in `nfs`, as well as in `tenants`,
so a tenant-scoped share is refused while either area is Terraform's.
A flat share or export that names its tenants, with `tenants` or the deprecated `tenant_id`, is in `tenants` as well as
`smb` or `nfs`, and one that names none is in `smb` or `nfs` alone.
A flat network's `tenant` is in no extra area, because capture writes it when `tenants` is among the areas Terraform
owns, and refusing it would refuse capture's own output.
[Inventory variables](../reference/variables.md#area-map) lists the matchers.

The take-over flag lifts only what it lifts elsewhere: the dotted parts and `audit`.
It hands back a tenant network's floating ranges while `network.floating_ips` is Terraform's, and it does not hand back a
tenant's `networks` while `network` is Terraform's.
The reason is that lifting it would let the baseline write a network document that Terraform owns, every network of it.

A Terraform apply that sends the networks without their tenants is the one other writer this page has to name.
With one tenant such an apply leaves each network in that tenant.
With two or more, the network write puts a network that carries no tenant in no tenant, where it serves no client
([Why the network document holds every binding](multitenancy.md#why-the-network-document-holds-every-binding)).
The baseline therefore warns, when the owned list holds `network` or `network.floating_ips` and the cluster has two or
more tenants once the run is done, that an apply that omits the tenants unbinds the networks.
The owned list is the one the inventory gives, as the Terraform output does, and with none it is the default for the
cluster's kind ([The floating addresses are Terraform's by default on CNQ](#the-floating-addresses-are-terraforms-by-default-on-cnq)).
A provider-formed `edge` or `baremetal` cluster with no list owns nothing, so it is warned only when its output lists
`network` or `network.floating_ips`, and the baseline writes its whole network document, tenants' networks included.
A provider-formed cluster with no list and no kind reads as `cnq`, so it is warned at two tenants.
The next baseline run binds again each network the inventory lists under a tenant.

## What the collection does not do

- It does not read Terraform's state or its configuration, so it cannot tell that a list is incomplete.
- It does not change Terraform's resources.
  The plugin runs `terraform output` or reads a file saved from it, and neither writes.
  No role applies Terraform.
  An `edge_rebuild` run reads the inventory again after reinstall tasks you supply, which may apply Terraform so that
  the new edge's UUID reaches the output.
  [Rebuild an unprotected edge](../how-to/edge-rebuild.md#a-terraform-formed-edge) has that case.
- It does not decide between the two tools when both write a setting.
  The ownership check refuses a declared area, and a setting that Terraform writes and the inventory does not declare is
  not seen at all.
- It does not manage the addresses in front of a cluster.
  The portal and replication addresses come from the output, and the collection derives none of them
  ([Edges and portals](edges-and-portals.md) explains why).

## Learn more

- [Use a Terraform root's output as the inventory](../how-to/terraform.md): publish the output and declare the areas the
  root owns.
- [Adopt a cluster you already run](../how-to/adopt-a-cluster.md): adopt a host from the plugin.
- [Clone an edge's configuration onto a new edge](../how-to/clone-edge.md): clone onto a Terraform-formed edge.
- [Declare common settings in the inventory](../how-to/inventory-recipes.md#send-the-audit-log-to-syslog): the audit
  destinations beside a CloudWatch destination Terraform owns.
- [What capture and clone read and write](../reference/capture-and-clone.md): what each leaves out for Terraform.
- [Inventory variables](../reference/variables.md#ownership): the two variables, the take-over flag and the area map.
- [The Terraform inventory plugin](../reference/terraform-inventory.md): the output's fields and the variables and
  groups it produces.
- [Refusals and guards](refusals-and-guards.md): why the baseline refuses an owned area instead of skipping it.
- [Tenants and the continuity rule](multitenancy.md): why a network write carries every network's tenant.
- [State management](state-management.md): how a declaration in the inventory becomes a change.
- [Architecture of the collection](architecture.md#the-order-of-areas): where the ownership check sits among the
  baseline's steps.
