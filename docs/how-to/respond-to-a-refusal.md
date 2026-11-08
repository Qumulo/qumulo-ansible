<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Respond to a refusal

This page says, for each kind of refusal, what to read, what lifts it and how to give that for one run.
It recognises a message by a few words only; [Troubleshooting](../reference/troubleshooting.md) has the full text.
For why the collection refuses instead of guessing, see [Refusals and guards](../explanation/refusals-and-guards.md).

## Before you start

- You have run a playbook of this collection and it stopped with a refusal.
  [Your first cluster](../tutorials/first-cluster.md) shows the first one.
- You have the whole message. The way out is in it.

## What a refusal tells you

Every refusal works the same way:

- It comes before the write it guards. That write has not been sent; steps before it in the same run may have applied.
- It names what it found and why that stops it.
- It says what would lift it: a fix to the inventory, a consent flag, or a step to run first. Where nothing lifts it, it
  says so.

Find the kind in the table, then go to its section. The message text for each kind is in
[Troubleshooting](../reference/troubleshooting.md); the ids in the messages, such as `G-CLAMP-SHRINK`, are entries of
the [gap register](../reference/gaps.md).

| The message says | Kind | What lifts it |
|---|---|---|
| `identity guard: ...; no credentials were sent` | [The wrong cluster](#the-wrong-cluster) | A fix to `qumulo_api_host` or `qumulo_cluster_uuid`. No flag. |
| Names a flag to set: `set allow_...`, `..._allow_data_loss`, `set recreate=true`, `needs allow_decrease=true` | [A consent flag](#a-consent-flag) | The named flag, for one run (for the clamp, see [Lower a clamp](#lower-a-clamp)). |
| `needs -e qumulo_rebuild=true`, `belong to Terraform by qumulo_terraform_owned_areas`, `belong to Terraform by qumulo_managed_by: terraform_provider` | [A declaration](#a-declaration) | The declaration, or a change to the inventory. |
| `Validation of arguments failed:`, then `<variable>[.<path>].<key>. Supported parameters include: ...`; at step 2, `... sets smb_settings.<key>, which smb_settings does not take` | [A key the variable does not take](#a-key-the-variable-does-not-take) | A fix to the inventory: correct the key or remove it. |
| `... would lose its connection`, `... carries this play's connection`, `The cluster's network status is unavailable`, `set qumulo_management_network` or `set qumulo_cluster_port`, `qumulo_management_network is ..., which is ...` | [A tenant or network write that would cut the play off](#a-tenant-or-network-write-that-would-cut-the-play-off) | A declaration, `qumulo_management_network` or `qumulo_cluster_port`, or a fix to the tenants' REST switches or bindings. The consent `qumulo_allow_management_disruption` skips the rule. |
| `... holds ...; a release bound to ... naming O2 ...` | [A held record](#a-held-record) | A `release` entry for that record, for one run. |
| `Put the license document ... then run ...` | [A hand-off](#a-hand-off) | The step the message names. |
| `maybe_applied: true` in the result | [A run that stopped in the middle](#a-run-that-stopped-in-the-middle) | Run the task again. |

Refusals that belong to one task have their own sections of Troubleshooting:
[an upgrade](../reference/troubleshooting.md#upgrades), [a rebuild](../reference/troubleshooting.md#rebuilds),
[an adoption](../reference/troubleshooting.md#adoption).

## The wrong cluster

Every module reads the cluster's UUID before it sends a password. A refusal that starts `identity guard:` means the
address reached a different cluster than the inventory names, or a node that is not formed.

1. Compare the two UUIDs in the message: the one found, and the one expected.
2. Decide which is wrong. If the host entry's `qumulo_api_host` points at the wrong cluster, correct the address. If the
   cluster is right and the inventory is stale, correct `qumulo_cluster_uuid`. For a rebuilt edge, apply the
   `host_vars_patch` from its rebuild report rather than editing the UUID by hand
   ([Rebuild an unprotected edge](edge-rebuild.md#work-through-the-report)).
3. Run the playbook again.

No flag lifts this refusal. If the message says `cannot read the node state`, check the address, the port and that the
cluster is up instead.

A host with no `qumulo_cluster_uuid` does not refuse. It warns `identity guard disabled`, and no task checks which
cluster it reaches. Add the UUID.
[The identity guard](../explanation/authentication.md#the-identity-guard) explains the check.
The messages are in [Troubleshooting](../reference/troubleshooting.md#connecting).

## A consent flag

A change that loses something waits for you to say yes. The message names the flag.
A flag is an option of the module that refused, so how you give it depends on what ran the module.
In every case, read what the message says you lose, set the flag for the run that needs it, and remove it afterwards.
The messages are in [Troubleshooting](../reference/troubleshooting.md#refusals-that-wait-for-consent).

### Change the connection

A change that would cut the run off from the cluster names `allow_management_disruption`. The baseline takes it as the
run-level variable `qumulo_allow_management_disruption`. Pass it with `-e` to the one run that needs it, never in the
inventory, where it would apply to every later run too. The same flag covers a change to the password or role of the
user the run connects as.

A network change that drops `qumulo_api_host` also needs `qumulo_api_host_after`, the address later tasks connect to:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 \
  -e qumulo_allow_management_disruption=true \
  -e qumulo_api_host_after=10.0.0.11
```

The same flag covers a certificate the pinned CA would not verify. For that, `qumulo_ssl.ca_path_after` naming the new
chain's authority is the alternative; see [Manage the cluster's TLS certificate](manage-certificates.md).

### A tenant or network write that would cut the play off

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

A write that leaves the network the play connects on in a tenant without REST is refused, and so is one the collection
cannot judge. With one tenant, turning REST off on the only tenant is refused too; with two or more, the network the play
connects on is looked up. The message says which, and what to set:

1. A message that names `qumulo_management_network` or `qumulo_cluster_port` asks for a declaration. Give the id of the
   network your play reaches the cluster on, or the port the cluster listens on for it
   ([Declare the management network and the cluster port](tenants.md#declare-the-management-network-and-the-cluster-port)).
   Put them in the host's file, since they describe the path to the cluster and stay true for every run.
2. A message that says a tenant would be left with REST off asks for a change to the inventory: keep
   `rest_api_enabled`, or `web_ui_enabled` where the cluster port is 443, on the tenant that holds the network, or list
   the network under a tenant that has it.
3. A message that names `qumulo_allow_management_disruption` is the consent: the write drops the network that carries
   the play, and the message says to set it with `qumulo_api_host_after`, as in the section above.

Setting `qumulo_allow_management_disruption` for a tenant write skips the rule for the whole run, and a mistake can end
the connection. [Regain REST access](tenants.md#regain-rest-access) says how to recover. Every message is in
[Troubleshooting](../reference/troubleshooting.md#the-plays-connection).

### Change the password of admin

Changing the password of `admin` also changes the console and SSH password of every node
([G-ADMIN-OS](../reference/gaps.md)).
For `admin`, run `qumulo.cluster.admin_password_rotate`; it sets both flags the change needs.
[Rotate the admin password](rotate-admin-password.md) gives the steps.

To change it from the baseline instead, `allow_admin_password_change` is a key of the user's item in
`qumulo_local_users`:

```yaml
qumulo_local_users:
  - name: admin
    password: "{{ qumulo_api_password_after }}"
    update_password: always
    allow_admin_password_change: true
```

When the run connects as `admin`, also pass `-e qumulo_allow_management_disruption=true`, because `admin` is then the
user the run connects as.
The baseline switches to the new password only for the rest of that run. Afterwards make the new password the host's
`qumulo_api_password`, and remove `allow_admin_password_change` and `update_password: always`.

### Delete a portal that cannot drain

Deleting a portal the hub sees DISCONNECTED discards spoke writes the hub has not received. The role names
`portal_pair_allow_data_loss`; a rebuild names `edge_rebuild_allow_data_loss`. Neither is part of the baseline, which
never removes a portal.

1. Check that the edge is really gone. The status can be brief, or the hub's `spoke_hosts` can be wrong, so if the edge
   is up, run again later.
2. If the edge is gone for good and its unreceived writes can be lost, pass the flag with `-e` to the run that
   refused:
   - The removal of a portal with `portal_pair_state: absent`: add `-e portal_pair_allow_data_loss=true` to the removal
     play in [Pair an edge with its hub](pair-edge-with-hub.md#the-hub-sees-the-edge-disconnected).
   - The drain of a rebuild, after you fixed the cause and the phase failed again:

     ```sh
     ansible-playbook qumulo.cluster.edge_predestroy -i inventory -l edge01 -e edge_rebuild_allow_data_loss=true
     ```

   - The replay of a rebuild, which refuses to delete the hub's DISCONNECTED record of the old edge's UUID. The old
     edge has been reinstalled by then, so step 1 does not apply:

     ```sh
     ansible-playbook qumulo.cluster.edge_rebuild -i inventory -l edge01 -e qumulo_rebuild=true -e edge_rebuild_allow_data_loss=true
     ```

For the rebuild, see [Rebuild an unprotected edge](edge-rebuild.md#if-the-drain-fails).

### Lower a clamp

The baseline refuses a clamp below the stored one. No flag of the baseline lifts it, because provisioned capacity never
shrinks (`G-CLAMP-SHRINK`). Either put the stored value back in `qumulo_capacity.clamp`, or make the cache smaller by
the route `qumulo.cluster.edge_cache_resize` chooses. [Size and resize an edge's cache](edge-sizing.md) gives the steps.
On the `below_floor` route, set `qumulo_capacity.free_threshold` instead.

### Replace a non-empty replication target

Authorizing a relationship into a directory that already holds entries replaces them with the source's content.
Set `allow_non_empty_target: true` on the relationship's entry in `qumulo_replication_relationships` only if that is
what you want. It counts only when the target host declares that directory in its `qumulo_directories`.
[Replace the content of a target directory](replicate-a-directory.md#replace-the-content-of-a-target-directory) has
the steps.

## A declaration

Some runs go ahead only when you declare what you mean.

- Rebuilding an edge replaces a cluster, so the replay refuses until the run carries `-e qumulo_rebuild=true`. The
  message lists whatever else the replay needs. Give the declaration for that run only, and follow
  [Rebuild an unprotected edge](edge-rebuild.md).
- The baseline refuses to manage an area that Terraform owns: one that `qumulo_terraform_owned_areas` lists, or, with
  no such list, on a provider-formed cluster of kind `cnq` or unset, the floating addresses. Remove the variables or
  keys the message names, or remove the area from the list when there is one. For `audit`, `dns.fqdn` or
  `network.floating_ips`, the message also offers `qumulo_take_over_from_terraform: true`, which hands the baseline
  the audit destinations, `dns.fqdn` and the floating addresses together. With no list and no `qumulo_cluster_kind`,
  it also offers the kind: an edge or bare-metal cluster declares it, which leaves the floating addresses to the
  baseline.
  [Who owns which area](../explanation/terraform-ownership.md) explains the choice.
- A `floating_ip_ranges` in `qumulo_network` that Terraform owns has its own message, which names where each range is
  declared and the owner. Remove the ranges, or `network.floating_ips` from the list when one is given, or set
  `qumulo_take_over_from_terraform: true` as above, or, with no list and no kind, declare an on-premises cluster's
  kind. A list that leaves `network.floating_ips` out gives the floating addresses to the baseline, so the message
  does not arise.
- A provider-formed `cnq` host whose list leaves out `network.floating_ips` is refused, as the Terraform inventory
  plugin refuses the same output. Add the area to the list, or set `qumulo_managed_by: none` when the provider did not form the cluster.

The messages are in
[Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree).

## A key the variable does not take

The baseline checks each variable's keys against its argument specification before its first task, and checks a tenant's
`smb_settings` and `nfs_settings` keys at step 2. Either way the run stops before it writes. The rebuild runs the first
check before it drains the edge.

1. Find the key in the message. The first kind starts `Validation of arguments failed:` and gives the dotted path to the
   key, then the keys taken where the key sits. The second names the tenant and says `sets smb_settings.<key>`.
2. Search the inventory for the key. A message about a list does not say which entry holds it.
3. Correct the key, to one the message lists or suggests, or remove it. A connection option such as `request_timeout` is
   not a key of an entry, since the play's `module_defaults` set it.
4. Run the playbook again.

No flag lifts this refusal. The messages are in
[Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree), and for a tenant's settings in
[The inventory's tenants](../reference/troubleshooting.md#the-inventorys-tenants).

## A held record

When another object already holds what you asked for, the collection names the holder and stops.
The usual case is a replication relationship: authorizing into a directory that another relationship holds is refused
with that relationship's id, and every unmet row is in the result's `unmet`.

1. Read the holder and the row the refusal names. A release lifts only the rows it names.
2. Copy the release the refusal prints. Its form is `<target host>:<target path>@<record id>:<rows>`, bound to the
   record's id. It lifts the refusal only while that record exists.
3. Give it for one run with `-e`, never in the inventory. To the baseline it is a list. Run it on the source host, the
   one whose `qumulo_replication_relationships` declares the relationship:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 \
     -e '{"qumulo_replication_release": ["hub01:/replicas/edge01-projects@4f0e...:O2"]}'
   ```

   A playbook of your own passes it to the `release` option of `qumulo.cluster.replication_target`.

An entry for a target host this run reaches must name one of the run's relationships, or the run fails.
Some rows no release lifts, such as a live relationship at a path that contains the target path or lies inside it:
remove that relationship first, or change the paths.
A relationship from a rebuilt source is held rather than refused, and the message names the restore; see
[Restore a rebuilt edge's data from its replica](restore-replica.md).
The overwrite rows and what a release is are in [Replication](../explanation/replication.md); the message is in
[Troubleshooting](../reference/troubleshooting.md#refusals-that-wait-for-a-release).

## A hand-off

Some steps need something only a person can supply, such as a license document Qumulo issues to the cluster.
The collection then says what to get, where to put it and what to run next, and goes on without it, or stops that host
and goes on with the others.

Do what the message says, then run the playbook it names.

- A license document: [License an edge](licensing.md).
- An edge's cache on the `blocked_license` or `rebuild` route: [Size and resize an edge's cache](edge-sizing.md).
- The report of a rebuild lists what is left to do by hand under `hand_offs`; see
  [Rebuild an unprotected edge](edge-rebuild.md).

The messages are in [Troubleshooting](../reference/troubleshooting.md#hand-offs).

## A run that stopped in the middle

A write whose connection drops after the request was sent may or may not have reached the cluster. When the task fails
with `maybe_applied: true` in its result:

1. Read the task's result. If the cluster could not be read back, check the object on the cluster.
2. Run the task again. It reads first, and writes only what still differs.

A task that reads the change back and finds it took effect succeeds, with the note `write reconciled after a lost
connection`. For a registration with a join key, a single-use key is spent: follow the message's last clause.
[State management](../explanation/state-management.md#maybe-applied) says why the collection
never resends such a write, and
[Troubleshooting](../reference/troubleshooting.md#a-change-that-may-or-may-not-have-been-applied) lists the messages.

## Verify

Run the playbook that refused again with `--check` (and `--diff` where the playbook shows one). The refusal is gone when
that task predicts a change or reports none, instead of failing. The example is for the baseline:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
```

A flag you gave with `-e` is not in the next run unless you pass it again.

## See also

- [Troubleshooting](../reference/troubleshooting.md): every message, with what to do.
- [Gaps](../reference/gaps.md): the operations the REST API does not let the collection make idempotent.
- [Refusals and guards](../explanation/refusals-and-guards.md): why a module refuses.
- [Rotate the admin password](rotate-admin-password.md), [Size and resize an edge's cache](edge-sizing.md) and
  [Replicate a directory to another cluster](replicate-a-directory.md): the pages the consent sections point to.
