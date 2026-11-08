<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Refusals and guards

Sooner or later the collection stops a run and declines to make a change the inventory asked for. It does so on
purpose. Some changes lose data, cut the run off from the cluster, or cannot be undone, and an inventory that says so
is not enough reason to make one. This page explains why the collection refuses instead of guessing, the shapes a
refusal takes, and why the refusals that no option lifts have no option.

To read one and act on it, see [Respond to a refusal](../how-to/respond-to-a-refusal.md). The message texts are in
[Troubleshooting](../reference/troubleshooting.md).

## A refusal is the safety

A playbook run against the wrong inventory, or an inventory with one wrong line, does exactly what it says. A
declarative tool has no second opinion of its own, so the collection supplies one at the points where the cost of a
wrong line is highest: a write that loses data, a write that ends the connection, and a write the cluster cannot take
back.

Every refusal has the same three properties, so that it is useful as well as safe:

- It comes before the write it guards. That write has not been sent. Steps before it in the same run may have applied.
- It names the object, what it found, and why that stops the run.
- It says what would lift it: a fix to the inventory, a consent, or a step to run first. Where nothing lifts it, it says
  so.

A run that stops partway, with a lost connection, is a different case, because a write may have reached the cluster.
[State management](state-management.md#maybe-applied) explains how the collection handles it.

## Refuse, don't guess

When the collection cannot be sure a change does what the inventory means, it fails instead of choosing. The
alternatives are worse. A module that guessed would report success for a change that did something else, and the next
run would report no change, so nothing would show it.

Four cases show the pattern.

**The cluster would ignore the field.** [`nfs_export`](../../plugins/modules/nfs_export.py) takes `restrictions[].require_tls` only on Qumulo Core 7.11.0 and
later. A release before that drops an unknown restriction field without an error, so the write would look like it
succeeded and the export would not require TLS. The module checks the release (and probes the cluster's API where it
can) and fails on an older one.

**The cluster would refuse, with a message that does not name the option.** Four modules check the server's own rule
first, so that the message names the option to change instead of repeating the server's error:

- [`cluster_settings`](../../plugins/modules/cluster_settings.py) reads the domain state before a rename and fails while the cluster is joined, because the name is
  the cluster's computer account. The message says to leave the domain, rename, and join again.
- [`role`](../../plugins/modules/role.py) fails a delete while the role has members, naming `role_member`, which removes them.
- [`nfs_settings`](../../plugins/modules/nfs_settings.py) fails turning on NFSv4.1 while one export lies inside another, naming the pair. While NFSv4.1 is on,
  the cluster also refuses a new export whose path is inside another's or contains one.
- [`time_settings`](../../plugins/modules/time_settings.py) checks the server's rules before any write, against the
  settings the task would leave. HYPERVISOR time, for one, needs `ntp_servers: []` and `use_ad_for_primary: false`, and
  the cluster would refuse it with an error that names neither.

These refusals appear in check mode too. The check run is where an operator wants to learn that a rename cannot work,
not the run after it. `role` is the exception, and the reason is ordering. In check mode it reports the delete with a
note, because an earlier task in the same play may remove the members, and a check run does not run that task's write.

**The change belongs to a step of its own.** [`ad_domain`](../../plugins/modules/ad_domain.py) never leaves a domain
in order to join another, and the message names the step to run first: `state: absent`. A leave deletes the cluster's
computer account only when it has credentials, and whether an account left behind can be reused depends on the
domain's policy (G-AD-JOIN). Leaving is a decision that takes its own credentials, so a changed domain name in the
inventory does not trigger it as a side effect.

**The change would never be undone.** [`snapshot_lock`](../../plugins/modules/snapshot_lock.py) locks a snapshot that
never expires only when the task gives the matching `private_key`. A snapshot that expires frees itself, so locking it needs nothing more. A snapshot that never
expires, once locked, can never be given an expiration; it stays locked unless the private key unlocks it. The module
therefore locks one only when it can prove the lock can be undone, by checking the key against the public key in the
key store. No consent flag lifts it. The way out is the matching `private_key`, or an expiration given to the snapshot
first.

The module signs with `openssl` on the controller because the collection's modules import no Python package
beyond ansible-core. It hands the key to `openssl` in a file only the controller's user can read, and removes the file
when `openssl` exits. The key is never logged, returned or shown in a diff.

## The shapes of refusal

Five shapes cover the refusals that something other than a corrected inventory lifts. The shape tells you what, if
anything, lifts it.

### A guard

A guard protects something the inventory cannot be allowed to override. The identity guard is the main one: every
module that connects reads the cluster's UUID before it sends any credential, and fails if `qumulo_api_host` reaches
another cluster.
[Authentication](authentication.md#the-identity-guard) describes the mechanism.

No flag lifts it. A consent accepts a named loss on the cluster the inventory describes. A mismatch means the run is
talking to a cluster the inventory does not describe, so there is no loss to name and accept: every task after it would
apply one cluster's declarations to another. The only correct response is to fix `qumulo_api_host` or
`qumulo_cluster_uuid`, whichever is wrong.

Other guards follow the same rule:

- [`cluster_guard`](../../plugins/modules/cluster_guard.py) in `rebuilt` mode passes only a node that is UNCONFIGURED or
  an ACTIVE edge of the expected name and UUID that holds nothing yet, and a node that belongs to any other cluster
  fails at once. The replay writes the whole inventory to the cluster, so the guard lets through only a newly formed,
  empty edge. A working edge, or an old edge that was never destroyed, fails. In `match` mode an empty
  `expected_cluster_uuid` fails instead of switching the guard off, so this is the one module where emptying the UUID is
  no way past it. [Rebuilds](rebuilds.md#the-guard-on-the-new-edge) describes the guard on the new edge.
- [`clone_host_vars`](../../plugins/modules/clone_host_vars.py) fails when the clone's UUID is the source's, when its
  name is the source's or one already in use, and, unless `allow_configured_target`, when the target holds what
  formation does not create. A clone must never claim to be another cluster. A name is also the cluster's computer
  account in the domain, so a reused name would collide with another cluster's account. A target that holds a local
  user other than `admin` and `guest`, a domain join, a share or export other than the factory ones, or an S3 bucket or
  key is a cluster already in use, not a fresh one.
- Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants through it on those releases, and a later Qumulo Core release may change it.
  [`tenant`](../../plugins/modules/tenant.py) and `network_config` apply the continuity rule, and step 2 of the baseline
  applies it to every judged write of the run before the first write: a tenant create, a change of a REST or web UI
  switch, a delete, or a network write that would leave the network the play connects on in no tenant that serves REST,
  or leave no tenant. The rule is a guard because a write that breaks it cuts off the controller that made it, and the
  run could not say what it left undone. Step 2 also refuses every tenant write to a cloud cluster, which Qumulo Core
  refuses, so that the inventory fails where the cluster would. [Tenants and the continuity
  rule](multitenancy.md#the-continuity-rule) gives the rule, and the
  [troubleshooting rows](../reference/troubleshooting.md#the-plays-connection) the messages.
- [`unprotected_edge_create`](../../plugins/modules/unprotected_edge_create.py) fails, before any credentials are sent,
  when the node is ACTIVE under a UUID other than `expected_cluster_uuid`. A stale inventory would otherwise adopt the
  wrong cluster.
- [`edge_rebuild_preflight`](../../plugins/modules/edge_rebuild_preflight.py) only reads. It runs before the drain,
  the first step that changes anything, so a refusal costs nothing, and a check run of predestroy writes no record and
  can be repeated.

### A consent

A change that loses something waits for the operator to say yes. The refusal names the flag, and the flag names the
loss. A consent is an option of the module that refused, so its name says what it accepts:

- `allow_management_disruption` accepts a change that would cut the play off from the cluster.
  [`network_config`](../../plugins/modules/network_config.py) refuses a network document that drops the address in
  `api_host` from the static or floating ranges. [`ssl_certificate`](../../plugins/modules/ssl_certificate.py) refuses a
  chain that would not verify against `ca_path` for `api_host` when `validate_certs` is true. It checks by serving the
  new chain to itself before installing it. [`local_user`](../../plugins/modules/local_user.py) refuses a password
  change for the user the task connects as. In each case later tasks must connect differently, so each tells the play
  how to go on: `network_config` needs `api_host_after`; after `ssl_certificate`, later tasks set `ca_path` to the new
  chain's authority, which the baseline does from `qumulo_ssl.ca_path_after`; and `local_user` returns
  `connection_password_changed: true` so that the play switches to the new password.
  [`access_token`](../../plugins/modules/access_token.py) refuses to remove, disable or expire the token the task
  connects with, and, when it removed that token, reports `connection_token_changed` so that later tasks connect with a
  password or with the new token's `bearer_token`. [`role_member`](../../plugins/modules/role_member.py) refuses to
  remove the user the task connects as from a role, which can take away the privileges later tasks need; it detects
  only a direct assignment of that user, not one through a group.
- `allow_management_disruption` on `tenant` and `network_config`, and `qumulo_allow_management_disruption` in the
  baseline, accepts a tenant or network write that the continuity rule would refuse, and skips the rule for the whole
  run. It does not lift the refusals that the cluster itself would make, the cloud refusal, the second tenant while a
  tenant holds a backend network, the delete of a tenant that holds a network, share or export, or a write that would
  leave no tenant.
- `allow_admin_password_change` accepts a change to the password of `admin`. The server also applies it to the operating
  system's console and SSH password of every node (G-ADMIN-OS), which is a larger change than one cluster user's
  password. [`playbooks/admin_password_rotate.yml`](../../playbooks/admin_password_rotate.yml) sets it for the run that
  rotates the password.
- `allow_data_loss` accepts a delete that discards writes. A DISCONNECTED portal cannot be drained, so deleting it
  discards the spoke writes the hub has not received, and on the hub a delete that is not forced never finishes.
  [`portal_hub`](../../plugins/modules/portal_hub.py) and [`portal_spoke`](../../plugins/modules/portal_spoke.py)
  therefore fail before any request that changes the cluster until the operator accepts the loss.
  [`file_acl`](../../plugins/modules/file_acl.py) uses the same name for a path inside a spoke root: the spoke passes
  the write through to the hub, so it replaces the hub's ACL, which the hub's own inventory may declare, and the two
  would then undo each other. Declaring the ACL on the hub is the better answer where it is possible. The same name
  lifts the delete of an S3 bucket with unfinished uploads and a replication revert.
- `qumulo_allow_deferred_policy_lock_keys` accepts a snapshot policy applied without its lock key. A rebuilt or cloned
  cluster has an empty key store, and the cluster refuses a policy that names a key it does not hold. Step 2 of the
  baseline therefore refuses the run, before it writes anything, when a declared `lock_key` is not in the key store, and
  the edge rebuild refuses it before the predestroy changes anything, in either form of the rebuild. With the flag, the
  baseline's step 20 applies the policy without the key and lists it in `cluster_baseline_policy_lock_key_hand_offs`.
  The loss it accepts is that the policy's snapshots are not locked until a later run sets the key: the key goes on once
  it is registered with `security_key` and the baseline runs again. The roles set the flag `false` in their vars, which
  outrank group_vars and host_vars, so a value in the inventory has no effect: give it with `-e`, for the run that needs
  it. The edge rebuild's report lists each such policy.
- `allow_decrease` accepts lowering the capacity clamp. Lowering the clamp never shrinks the capacity already
  provisioned (G-CLAMP-SHRINK), so it cannot do what it looks like. [`capacity_clamp`](../../plugins/modules/capacity_clamp.py)
  refuses it, and even with the flag the cluster only stops provisioning more. To make an edge's cache smaller, rebuild
  the edge, or keep more of it free with `portal_eviction_settings`. The
  [`edge_cache_resize`](../../roles/edge_cache_resize/) role chooses between them. The baseline gives no flag for a
  decrease, so the decrease is a job for
  [`playbooks/edge_cache_resize.yml`](../../playbooks/edge_cache_resize.yml), not for the baseline with a flag.
- `allow_non_empty_directory` on [`replication_target`](../../plugins/modules/replication_target.py) (in the
  inventory, `allow_non_empty_target` on the relationship entry) accepts that the source's content replaces a target
  directory that already holds entries, and that the first job deletes whatever the source lacks.
- `allow_configured_target` on `clone_host_vars` accepts cloning onto a cluster that is already configured.

How long a consent lasts depends on what it names. A run-level flag, such as `qumulo_allow_management_disruption` in
the baseline or `edge_rebuild_allow_data_loss` in a rebuild, applies to whatever that run meets. Left in the inventory
it would apply to the next run as well, which may hold a different change that the operator never looked at, so it goes
to the run that needs it with `-e`. An item-level consent names one object and stays with it while the decision stands:
`allow_data_loss` on a `qumulo_file_acls` or `qumulo_s3_buckets` entry, or `allow_non_empty_target` on a replication
entry. `allow_admin_password_change` comes out of the inventory afterwards because it accepts one change, not a
standing state. [Run the playbooks from a pipeline](../how-to/pipeline.md) shows how a pipeline passes a run-level
consent to a single job.

### A declaration

Some runs go ahead only when the operator states what they mean. A declaration is not an acceptance of a loss. It says
which of two meanings of the inventory applies.

Rebuilding an edge replaces a cluster: it forms a new one with a new UUID, and everything keyed on the old UUID goes.
The replay deletes the hub's record of the old UUID, retires that UUID in Nexus and moves the inventory to a new one,
so it waits until `-e qumulo_rebuild=true` says that is the intent. An inventory alone never starts it.

The baseline refuses to manage an area that Terraform owns. Two owners of one setting undo each other's work, so the
inventory has to say which one applies: remove the variables, or take the area off `qumulo_terraform_owned_areas`.
[Terraform ownership](terraform-ownership.md) explains who owns what. For the areas that can be handed back,
`qumulo_take_over_from_terraform` is the declaration.

The continuity rule, which Qumulo's preview multitenancy REST API makes necessary, asks for two more declarations, because only the operator knows what lies between the play and the
cluster. `qumulo_management_network` names the network a play behind NAT or a load balancer reaches the cluster on, and
`qumulo_cluster_port` the port the cluster listens on for a play whose port a balancer or tunnel maps. Without them the
collection refuses a write it cannot judge, and the message names the variable to set; a play at port 443 with no
cluster port fails closed, since the collection cannot tell which listener it reaches.

### A held record

When another object already holds what the task asked for, the collection names the holder and stops. The case that
matters is replication. Authorizing a relationship makes the target directory read-only and replaces it with the
source's content, and a live relationship of another source may hold that directory.

A release lifts the refusal. It is one entry of the module's `release` option, bound to the id of the holder's record
and naming the rows of the overwrite table that it lifts. It is bound to the id so that a release written for one
holder cannot delete a different record later. It lifts only the rows it names, and only while that record exists.
Like a run-level consent, it belongs to one run, so the playbook passes it through a variable that the operator sets
with `-e`.
The refusal prints the releases that would lift it, and an operator should copy one only after reading what it allows.
[Replication](replication.md) explains the overwrite table and why a release is bound.

### A hand-off

Some steps need something only a person can supply, such as a license document that Qumulo issues. The collection then
says what to get, where to put it and what to run next, and goes on without it, or stops that host and goes on with the
others. A hand-off is a refusal in the sense that the host does not reach its final state, but nothing is written
that should not be, and running again after the step finishes the work.
[Licensing and capacity](licensing-and-capacity.md) covers the most common one.

## Why a rebuild refuses so much

A rebuild has more refusals than any other task because it destroys a cluster and must be sure to rebuild the right
one. Each check sits where a mistake would first become irreversible.

- The identity guard checks the old UUID during the predestroy phase and the new one after formation, before any
  credentials are sent. A rebuild that reached the wrong cluster would drain and destroy it.
- With `qumulo_ad`, the replay refuses to join under the old name unless a predestroy record shows the old edge left
  the domain with credentials, or `edge_rebuild_old_edge_destroyed` says it is gone. A join resets the computer
  account's password, which cuts a live old edge off from the domain. In a rebuild in one run, `edge_rebuild_ad_leave`
  with the join credentials stands in for the record, and the check comes before the drain.
- The hub's record of the old UUID is deleted before the edge forms, so that a refusal leaves the node UNCONFIGURED
  and the replay can run again. A PENDING record holds no writes and goes at once, and so does a DELETING one whose
  predestroy record shows the portal was drained. A DISCONNECTED one needs `edge_rebuild_allow_data_loss`. One the
  hub still reaches means the old edge is up, and the replay stops.
- The replay refuses an edge whose UUID is the one the inventory names as the old edge. Once `host_vars` holds the new
  UUID, a replay would retire the new edge in Nexus and report it as replaced. `playbooks/baseline.yml` finishes such
  an edge.
- The replay takes neither `qumulo_replication_release` nor `qumulo_replication_restore`, because both belong to later
  runs. After the edge forms, its source directories are new and empty, and a relationship whose replica may be the only
  copy of their data is held and reported, not overwritten.

The [`edge_rebuild`](../../roles/edge_rebuild/) role carries these checks. [Rebuilds](rebuilds.md) explains what a
formation takes with it.

## Learn more

- [Respond to a refusal](../how-to/respond-to-a-refusal.md) says what to read and do for each kind.
- [Troubleshooting](../reference/troubleshooting.md) lists the messages by symptom.
- The [gap register](../reference/gaps.md) lists the operations the REST API does not let the collection make
  idempotent. The ids in the messages, such as G-CLAMP-SHRINK, are its entries.
- [Authentication](authentication.md#the-identity-guard) describes the identity guard's mechanism.
- [State management](state-management.md#check-and-diff) explains why check mode reports the same refusals.
- [Run the playbooks from a pipeline](../how-to/pipeline.md) covers giving a consent to one job.
