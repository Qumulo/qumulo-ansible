<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# State management

This page explains how a declaration in the inventory becomes a change on a cluster, and why the collection works that way.
It covers what an unset variable means, how lists and dictionaries are read, how a capture turns a running cluster into an inventory, what check and diff mode show, how an object is found without its id, how a write is sent, what happens when a connection drops, and why some operations and some values need a rule of their own.

## The model: declare, read, compare, write, read back

The inventory says what a cluster should hold.
The cluster says what it holds.
The collection keeps no third record of what it applied.
Each run reads the cluster, compares what it read with the declaration, writes only what differs, and reads the object back to check the write took effect.
The records the collection does keep, such as a rebuild's or an upgrade's, describe a procedure in progress and not the configuration ([the artifacts directory](architecture.md#the-artifacts-directory) says where they go).

Most modules that manage an object share one implementation of this loop in [resource.py](../../plugins/module_utils/resource.py).
A module only says how to find its object, how to turn the cluster's answer into the shape of its own options, and how to write.
The shared code decides whether a write is needed, so check mode, diff and the read-back behave the same for every such module.

Without a record of past runs, every comparison needs two things.
One is a key that finds the same object on every run, which [Keys, not ids](#keys-not-ids) covers.
The other is a form of each value that the cluster returns the way it was sent, which [Comparing in the form the cluster reads back](#comparing-in-the-form-the-cluster-reads-back) covers.
Where either is missing, the collection says so in a rule, in a refusal, or in an entry of the [gap register](../reference/gaps.md) or the [quirk register](../reference/quirks.md).

The order in which [cluster_baseline](../../roles/cluster_baseline/) applies the areas is a separate matter, and [The order of areas](architecture.md#the-order-of-areas) owns it.

## Unset means unmanaged

An area whose `qumulo_*` variable is unset is left alone, and so is an option a module is not given.
`cluster_baseline` skips the step of an undeclared area.
[nfs_export](../../plugins/modules/nfs_export.py), [smb_share](../../plugins/modules/smb_share.py) and [snapshot_policy](../../plugins/modules/snapshot_policy.py) each say that options left unset are not managed, so an existing object keeps the values it has.
[dns_settings](../../plugins/modules/dns_settings.py) keeps the servers and search domains it is not given.

The inventory is rarely the whole truth about a cluster, and the rule lets it be partial without harm.
A new cluster already holds objects nobody declared: the users `admin` and `guest`, the groups `Users` and `Guests`, the SMB share `Files` and the NFS export `/`.
Terraform may own part of the cluster ([Terraform ownership](terraform-ownership.md) says which parts and why).
On an edge, the data inside a spoke root belongs to the hub.
If silence meant "remove", declaring one share would put all of these at risk.

The rule trades away one thing.
Omission carries no intent.
A setting that is left unset and that someone changes by hand stays changed, and no run reports it, because the collection never compared it.
To have the collection hold a value, declare it.

Unset is not empty.
An empty list is a declaration: `dns_settings` clears a list it is given empty, and `user_defined_mappings` with `[]` removes every mapping.
Which one a variable holds is a decision about intent, and the collection never infers it.

Where a declaration meets an area that belongs to Terraform, the baseline stops before it writes anything.
That check is step 2 of the role.

## Lists add, dictionaries set the keys they give

The same idea decides how a list or a dictionary is read.

A **list of objects** adds to what the cluster holds.
A share, export, quota, snapshot policy, directory, bucket, policy, key, user or group that the list leaves out stays as it is.
To remove one, declare it with `state: absent`.
Removal is a statement in the inventory, so a deleted line in a YAML file never deletes an object on a cluster.
Deleting is also where data is lost, and the collection asks for it by name.

Two lists can take members away, and only when asked.
An item of `qumulo_local_groups` or `qumulo_role_members` with `purge: true` makes its `members` the whole set.
A membership is a relation between objects that already exist, so removing one deletes no data, but the cluster may hold members that something else put there.
Hence the flag is per item and opt-in, and the modules keep what the server requires.
A user whose primary group this is always stays in a local group, and the local `admin` and `system` users always stay in `Administrators`.

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants through it on those releases, and a later Qumulo Core release may change it.
Tenants are objects of that kind: a tenant that `qumulo_tenants` leaves out stays, and `state: absent` deletes one, after
the networks, shares, exports and overrides declared under it.
A share or export that several tenants carry is declared once, in a shared entry with `tenants`, and the entry acts
only on the pairs of a tenant and a name that it claims.
A tenant that leaves an explicit `tenants` list keeps its copy until a `state: absent` entry names that pair, so the
list adds like every other, and `tenants: all` is the form in which a tenant leaves through its own `state: absent`.
A tenant's `smb_settings` and `nfs_settings` follow the dictionary rule below, with one more word: `inherit` removes the
override, and leaving the key out leaves it as it is.
[Tenants and the continuity rule](multitenancy.md#why-the-baseline-removes-a-deleted-tenants-content-first) gives the
reasons.

A few lists are **complete sets**: what the list leaves out is removed.
They are the network list in `qumulo_network`, which becomes the whole set together with the networks that the tenants
declare once one present tenant declares `networks`, `qumulo_user_defined_mappings`, the DNS servers and search domains, `qumulo_dns.lookup_overrides`, the cipher suites of `qumulo_ssl_ciphers`, and a bucket's policy, which is one document whose missing statements are removed.
Each of these is one document or one ordered value in the REST API, which reads and writes it as a whole.
An entry has no route or id of its own to add or remove it by, so the declared list is the value the module writes.
Order is part of three of them: the DNS servers, compared in the order the cluster asks them, the search domains, in the order they are appended, and the cipher suites, in the order the cluster prefers them.

A **dictionary variable** such as `qumulo_smb_settings` sets only the keys it gives.
The cluster keeps its values for the rest, which is the same rule as an unset option.
A list inside such a dictionary is one setting, so `qumulo_dns.servers` and `qumulo_dns.lookup_overrides` are each the whole list.

Do not confuse that rule with Ansible's.
Across inventory files, Ansible's default is that a dictionary set at a higher precedence replaces the lower one whole.
A host that sets `qumulo_capacity` must therefore give every key it needs, whatever its group gave.
The first rule decides what a module sends to the cluster, and the second decides what the module is given.
[Inventory variables](../reference/variables.md#precedence) lists the precedence, and [Set up an inventory for your clusters](../how-to/set-up-inventory.md) shows where to put a variable.

## Capture and adoption

A cluster you already run holds settings the inventory does not know yet.
[capture_info](../../plugins/modules/capture_info.py) reads them and returns them as the `qumulo_*` variables of the baseline, in the shape the role passes to its modules.
Because the shape is the baseline's own input, a check run of the baseline from a capture changes nothing.
That is the point of the shape, and it is why [identity_info](../../plugins/modules/identity_info.py) and the other `*_info` modules return each area in the shape of the module that manages it, and list users and groups by name and role members as trustee strings, never by server id.

A capture declares only what something names.
It declares a directory when a share, an export, a quota, a snapshot policy, an S3 bucket, the S3 base path or a replication target names it, and the directories above one, the root included.
No other directory can be told from data the cluster merely holds.
Declaring every directory would turn data the cluster merely holds into configuration.
A directory whose mode cannot express its ACL is captured with the ACL in place of the mode, for the reason given in [One fact, declared once](#one-fact-declared-once).

A capture writes no secret.
Some values the cluster never returns, and the capture names each one under `supply` for the inventory to provide: local passwords, the certificate and its private key, the Active Directory join credentials, the keytab of a standalone Kerberos KDC, a Nexus join key and a license document the cluster does not hold.
One-shot operations are not configuration and are not captured: forming a cluster, applying a license document, clearing a cache, taking a snapshot.
Neither are the home directories and attributes of identities the cluster does not own, because the cluster lists no identity that has attributes ([G-ID-ATTRIBUTES](../reference/gaps.md)).

**Adoption is a one-time import.**
[playbooks/capture.yml](../../playbooks/capture.yml) with `qumulo_adopt=true` writes the capture into the inventory once.
From then on the inventory is the source of truth, and nothing replays a capture.
Adoption never rewrites an existing `host_vars` file, because that file is yours by then.
A `host_vars` file outranks the variables of the Terraform inventory plugin, so adoption leaves out every variable the plugin defines and every area Terraform owns.
[Adopt a cluster you already run](../how-to/adopt-a-cluster.md) has the procedure.

An edge's rebuild replays the inventory and never state read from the edge it destroys.
Because the inventory is the only source, the rebuild can check, before it destroys anything, that the inventory declares everything the edge holds.
A replay that read the old edge would leave nothing to check against.
The preflight fails when the edge has tenants, shares, exports, quotas, snapshot policies, users, groups or portal roots that the inventory does not declare, unless `edge_rebuild_ignore_drift` accepts losing them.
[Rebuilds](rebuilds.md) explains the replay.

### Cloning

A clone reads a source cluster's capture and derives the `host_vars` of a new cluster that is to take the source's configuration.
[clone_host_vars](../../plugins/modules/clone_host_vars.py) connects to nothing.
It copies configuration and no data, and a replication relationship is the way to copy data.
[Architecture](architecture.md#reading-deriving-and-then-writing-the-clone) shows how the playbook strings the pieces together, and [What capture and clone read and write](../reference/capture-and-clone.md) lists what each variable becomes.

The module gives each variable a rule of its own, and a variable without a rule fails the module.
The failure is deliberate.
A variable that a later release of the collection adds to the capture is never copied unseen, because the first clone that meets it stops and names it.

What the module leaves out, it leaves out because the value belongs to the source.

- The portal, each replication relationship and the network are left out.
  The peer's record of a portal or a replication relationship is the source's, and so are the addresses.
- The CloudWatch audit destinations name a log group and region in the source's AWS account.
- `qumulo_authoritative_dns` is copied without `enabled` and `fqdn`, because the name is the source's.
- `qumulo_saml` is left out, because its `cluster_dns_name` is the source's Web UI address, for which the identity provider's application was set up.
  When the source requires Active Directory users to sign in through the identity provider, the clone's users sign in with a password until the clone's own `qumulo_saml` is declared.
- `qumulo_s3_access_keys` is left out, with a note for each key, because the source's S3 clients hold the source's secrets.
  The clone's keys are its own to declare ([Values the cluster shows once](#values-the-cluster-shows-once)).

A variable that the clone's inventory already defines outside the file `clone_host_vars` writes is left out too, with a note.
A `host_vars` file outranks the inventory, so writing it would mask the inventory's value from then on.
One exception keeps the rest: a variable whose inventory mapping holds only the variable's secrets, such as a `qumulo_capacity` holding only `license_document`, is kept.
Leaving it out would lose the source's other settings, so the module keeps it and says that the file's mapping now outranks the inventory's.

## The zero-diff gate

The collection is safe to let change a cluster once a check run shows it agrees with the cluster.
A check run reads the cluster, compares it with the inventory and reports what it would change, without changing anything.
Right after adoption it should report nothing.
That is the zero-diff gate: an inventory that shows no change against the cluster it came from is one you can trust to change it.

The gate exists because of the rule that unset means unmanaged.
An inventory may be partial, but whatever it does declare is a claim about the cluster, and the first write acts on every claim that is wrong.
The gate turns each wrong claim into a diff that you read before anything is sent.
Once the gate is passed, a change to the cluster is a change to the inventory, the check diff shows exactly that change, and the real run writes that change, provided nothing else changed the cluster in between.

A diff at the gate means one of three things.
You edited the inventory since the capture.
The cluster changed since the capture.
Or the module cannot compare the value, which [Comparing in the form the cluster reads back](#comparing-in-the-form-the-cluster-reads-back) covers.

Two things can look like a diff and are not one.
The license step reads the license when `qumulo_capacity` is declared, and a cluster without a license for its UUID gets a message that says what the license needs.
That is a hand-off and not a change ([Licensing and capacity](licensing-and-capacity.md) explains it).
And a step whose variable is unset reports `skipping`, which is the rule above made visible.

[Your first cluster](../tutorials/first-cluster.md) walks the gate on a cluster you already run.

## Check and diff

Check mode stops before the write.
The module reads, compares and builds the same result it would build for a real run.
It skips only the request that changes the cluster, so `changed` in a check run says what a real run would change.
Diff mode adds the before and after, in the option shape, to the result.
A value the cluster never returns is not shown: the diff says `changed, not shown` and lists the keys.

Because every module compares before it writes, a real run against a cluster that already matches its inventory changes nothing, apart from the exceptions below, and a rerun after a failure picks up where the first stopped.
This is the property that lets a pipeline run the check in one job and the apply in a later one, after someone has read the check's output ([Run the playbooks from a pipeline](../how-to/pipeline.md)).
It has a consequence.
The apply is not a replay of the check, because nothing is saved between them.
The apply reads the cluster again and writes whatever differs then, so a cluster that changed between the two jobs gets the change that is due at the second read.

A check run is a prediction, not a rehearsal.
Each module reads the cluster as it is, and a step that depends on an earlier step's write sees the cluster without that write.
A module then reports what it cannot know and does not fail on it.
[s3_settings](../../plugins/modules/s3_settings.py) notes a base path that does not exist yet and that an earlier task must create, [role_member](../../plugins/modules/role_member.py) notes a role that an earlier task may create, and [local_group](../../plugins/modules/local_group.py) reports a user who does not exist yet as a change with a note.
These notes come back in the `notes` key of the module's result.
A role predicts where it can: in a check run, the replication step takes the edge's license step's prediction in place of reading a license that the license step has not applied yet.

There are two kinds of check run, and the roles tell them apart.
`--check` sets `ansible_check_mode`.
The `check_mode:` keyword on a play or a task does not.
`cluster_baseline` takes `cluster_baseline_check_mode` for the keyword's case, and [playbooks/clone_edge.yml](../../playbooks/clone_edge.yml) sets it from `clone_edge_check_only`.
The [portal_pair](../../roles/portal_pair/) and [replication_pair](../../roles/replication_pair/) roles read `ansible_check_mode` alone and skip the pairing only under `--check`, so the clone playbook refuses a check-only run for a target that declares a portal or replication relationships and points to `--check --diff`, which skips the pairing correctly.
The distinction matters because some steps would otherwise wait for a result that a check run never produces.

A check run can be the whole of what a playbook offers.
The [edge_cache_resize](../../roles/edge_cache_resize/) role under `--check` only reports the route it would take and the inputs it read.
The `edge_predestroy` playbook under `--check` drains nothing and writes no record: the record says an edge was safe to destroy, and a run that changed nothing must not leave evidence that it did.

Diff mode shows state that is hard to compare by eye.
A check run with diff shows an ACL that drifted from the declared one, with the entries the cluster holds.
Replication runs both of its modules in check mode first and reports every unmet condition of both sides before any write on either cluster ([Replication](replication.md) explains why).

## Comparing in the form the cluster reads back

A write is only as idempotent as the comparison that decides it.
The cluster often returns a value in another form than the one it was sent in, and a module that compared the two as strings would report a change on every run.
So each module compares in the form the cluster reads back, and many of them also verify the write in that form.
The shared code reads the object after a write and fails with `did not converge` when the cluster shows other values than the declared ones.
A module that passes its own read-back therefore reports no change the next time.

Examples, from the plain to the specific:

- Address ranges are compared as sets of addresses, so `10.1.1.20-21`, `10.1.1.20-10.1.1.21` and the two single addresses are the same.
  A netmask of `24` is `255.255.255.0`.
- The cluster adds one TLS suite of its own to the end of each cipher list that leaves it out.
  [ssl_ciphers](../../plugins/modules/ssl_ciphers.py) adds them the same way before it compares, so a second run changes nothing ([QUIRK-060](../reference/quirks.md)).
- An NFS user id with leading zeros, such as `uid:0065`, is written as the decimal 65.
  The module drops the zeros before it compares and sends, because the cluster would read a leading zero as octal and store a different user.
- The cluster reads a multipart upload interval back in the largest unit it fits, rounding down.
  `s3_settings` compares in that form.
  It refuses an interval that would read back as a shorter one, such as `36hours`, which reads back as `1days`, before it sends any request, because the module could never tell whether that value is set.
- The cluster reads an S3 bucket policy back with its own names for the principals, and may name a principal outside the trustee grammar.
  [s3_bucket_policy](../../plugins/modules/s3_bucket_policy.py) sends each principal as the identity it resolves to, so `uid:1000` is stored and read back as `sid:S-1-5-88-1-1000`.
  It then finds each principal that comes back through the identity routes, and fails, naming the principal and the cluster's answer, when it cannot resolve it, as for a name no identity holds or more than one identity source holds.
  It resolves identities again on every run, because local identities change when a cluster is rebuilt.
  The actions in a statement compare as a set, and the principals by the identity they resolve to, so `Everyone` and the cluster's `world:Everyone` are one principal.
  A declaration in either form therefore reports no change.

Some values cannot be compared at all, because the cluster never returns them.
A local user's password is written and not read.
The collection cannot tell whether the stored password matches the declared one ([G-PASSWORD](../reference/gaps.md)).
With `update_password: on_create`, the default, the password is sent when the user is created.
With `update_password: always` it is sent on every run and reported as a change each time, so a baseline that declares it never reaches `changed=0`.
That is the only thing a baseline sends on every run.

## One fact, declared once

Two views of one fact must not both be declared, because each would undo the other on every run.

An ACL and a POSIX mode are the example.
Setting a mode rewrites the directory's ACL to match it, and writing an ACL changes the mode the cluster reports ([G-FS-ACL-MODE](../reference/gaps.md)).
If an inventory declared a mode with [directory](../../plugins/modules/directory.py) and a different ACL for the same path with [file_acl](../../plugins/modules/file_acl.py), one run would write the mode and a second would restore the ACL, and neither would converge.
So a path gets a mode or an ACL, never both, and the baseline refuses a path that has both before it writes anything.
A capture declares an ACL in place of the mode only when setting the mode would leave a different ACL.
A mode is enough for owner, group and everyone.
Deny entries, inheritance and more trustees need an ACL.

The rule that an area has one owner is the same idea at a larger scale.
Terraform and the collection must never fight over one setting, so a variable is refused when its area belongs to the other side.

## Keys, not ids

The cluster assigns ids to the objects it holds, and the ids change when a cluster is rebuilt and differ between clusters.
An inventory that named an object by id would be wrong after the first rebuild, and a capture that wrote ids would write values that mean nothing on the clone.
So each module finds its object by a key that the inventory can state and that survives a rebuild, and resolves the cluster's id from the key on every run.

Tenants are among the objects found this way. Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants through it on those releases, and a later Qumulo Core release may change it.

- A share is found by its name, without regard to case as the cluster matches it, and by its tenant when given.
  An export is found by its export path, and by its tenant when given.
- A tenant is found by its name, compared exactly, and never by its id.
  The cluster numbers tenants in the order it creates them and never reuses a number, so a tenant deleted and made again
  has a new id and a rebuilt cluster numbers them from the start.
  A rename is declared with `previous_name`, since a name that no tenant has would otherwise be a create.
  A network's tenant is its tenant's name, `tenant`, and the deprecated `tenant_id` is translated to it with a warning.
  A share or export with `tenant_id` is read as `tenants: [<that tenant's name>]`, an id that no tenant has skips the
  entry, and a capture never writes an id.
- A local user and a local group are found by name.
  Trustees, in role members and in bucket policies, are named and resolved on every run.
  [identity_attributes](../../plugins/modules/identity_attributes.py) names the identity and resolves it on every run.
- A snapshot policy is found by its name.
  [snapshot_info](../../plugins/modules/snapshot_info.py) returns a policy in the option shape of `snapshot_policy`, so it carries its name and no id.
- A quota is found by the path of its directory.
  The REST API names a quota by the directory's id, so the module resolves the path to the id on every run and never keeps it.
- A replication relationship is found by its source path, the target cluster's UUID and the target path, never by a server id.
  The address and port the source reaches the target at are settings, so a relationship made by hand at another address of the same target is adopted with one change.
  After a create, the module reads which cluster the address reached and deletes the new record at once if it is not the declared target.
- A frontend network is the exception.
  Its id is chosen by the client and is stable across runs, so the inventory states it.

A relationship also names its other side by inventory host.
A portal names its hub, and a replication relationship names its `target`, as an inventory host and never as a UUID or an address.
The host entry carries the UUID and the addresses, so when an edge is rebuilt and gets a new UUID, the relationships that name the host follow the host entry.
This is why a capture writes a portal's hub and a replication target as inventory hosts, and leaves out one that no inventory host names.
The addresses a portal or a replication source reaches the other side at are declared as settings, because the cluster records only the other side's addresses ([Edges and portals](edges-and-portals.md) and [Replication](replication.md) go into which address to choose).

A snapshot has no key the inventory can state.
The cluster prefixes each snapshot's name with an id it generates, `<id>_<name>`, so a name alone never finds a snapshot.
For that reason a run of [snapshot](../../plugins/modules/snapshot.py) takes another snapshot every time, which [Actions are not settings](#actions-are-not-settings) explains.

The one identity the collection does check by value is a cluster's UUID.
Every module that connects reads it before it sends credentials, when `expected_cluster_uuid` is set, as the collection's play header sets it from `qumulo_cluster_uuid` ([The identity guard](authentication.md#the-identity-guard)).
A rebuilt edge has a new one, and the rebuild report gives it as a `host_vars` patch to apply.

## How a write is sent

A comparison that finds a difference leads to a request, and the form of the request is also part of the model.

**If-Match.**
By default a module reads an object together with its ETag and sends its change with that ETag as `If-Match`.
If another client changed the object between the read and the write, the cluster refuses the write with HTTP 412.
The module then reads again, builds its change again, and tries again, up to three attempts.
If the read after a 412 shows that another client already set every key, nothing is written.

Where the ETag drifts, the module tries up to five times.
The monitoring settings share one ETag with `/v1/nexus/connection` and `/v1/nexus/registration`, and the cluster's background Nexus secret rotation changes it.
A 412 there does not mean that someone else edited the settings, so [monitoring_settings](../../plugins/modules/monitoring_settings.py) and [nexus_connection](../../plugins/modules/nexus_connection.py) read and send again, up to five times.
The network document's ETag hashes the nodes' addresses, which change as they are allocated, so it changes without any client editing the document.
A directory's ETag is its revision, which any change to its children bumps, so `directory` and `file_acl` also try up to five times.

**No If-Match.**
Some routes return an ETag that cannot be used for a write, and the module sends those writes without one.
The SMB settings route does when the two `hide_shares` options differ, because the ETag the cluster returns never matches what it checks ([QUIRK-001](../reference/quirks.md)).
The authoritative DNS route does once the settings were written (QUIRK-024).
The snapshot policy DELETE accepts `If-Match` and ignores it (QUIRK-008).
The S3 routes and the portal spoke and hub routes return no ETag.
On these routes the last writer wins, and a change another client makes between the module's read and its write is overwritten.
The module's diff still decides whether a write happens, so the exposure is the short window between the read and the write and not the whole run.

**PATCH, PUT and the whole document.**
A PATCH carries only the keys that differ, which leaves unmanaged keys alone and agrees with the rule that unset means unmanaged.
A PUT carries the whole document, so a module that uses one has to read the rest and send it back.
Some documents have no PATCH, and then the module sends the whole document, with every unmanaged key as it read it.
Two modules show the choice.
`monitoring_settings` always PATCHes and never PUTs, because a PUT also writes the pending Nexus join key and clears it when it is null ([QUIRK-010](../reference/quirks.md)).
[audit_destination](../../plugins/modules/audit_destination.py) always PUTs the destination's whole configuration, because before Qumulo Core 7.11.0 a PATCH of a destination answers success and changes only `enabled`, and the module supports 7.10.3 (QUIRK-062).

**Writes that must not depend on earlier ones.**
Some servers remember what an earlier write set.
`smb_share` sends `create_fs_path` with every create and every change, because the cluster otherwise keeps the value of an earlier write and applies it to a later change of the path ([QUIRK-007](../reference/quirks.md)).
Others restart something when they receive a field, whatever its value.
`snapshot_policy` sends the schedule only when it differs, because any schedule the cluster receives restarts its timing (QUIRK-006).
[network_config](../../plugins/modules/network_config.py) writes only when the document differs, because the cluster reallocates addresses on every write (QUIRK-002), and [capacity_clamp](../../plugins/modules/capacity_clamp.py) writes only on a difference and never resends, because every PUT of the clamp abandons quorum to reprovision ([G-CLAMP-BOUNCE](../reference/gaps.md)).
The two policies look opposite, to always send and to send only on a difference, and each follows from how the cluster treats that field.

**Confirming a write.**
The read-back after a write is the module's check that the cluster took the change.
Where the cluster's own pointer to that check is wrong, the module uses the route that works.
A PATCH of the monitoring settings names `/v1/support/status` as the place to follow the change, and the server does not serve it.
The module reads `/v1/support/status/` instead ([QUIRK-003](../reference/quirks.md)).

The [quirk register](../reference/quirks.md) lists every quirk with the module that carries its workaround.

## Maybe applied

A write whose connection drops after the request was sent may or may not have reached the cluster.
The collection never sends such a write again blindly.
It reads the cluster back instead.

The shared client resends a request after a lost connection or an HTTP 502, 503 or 504 only when the route declares that method safe to repeat, and a route's GET is safe to repeat by default.
A few POST, PUT and PATCH routes are declared safe too, each with the reason in the code, such as a login, a read-only lookup sent as a POST, and a DNS cache clear, since clearing a cache twice does no more than clearing it once.
A refused connection is the one failure that proves nothing was sent, so any request is sent again after one.
Every other write that loses its connection after it was sent, or that a gateway answers with 502 or 504 after perhaps forwarding it, raises an error marked `maybe_applied`, and the module goes to the cluster to find out.

Three outcomes follow.

- **The read shows the change.**
  The task succeeds and notes `write reconciled after a lost connection`.
  An object that exists after a lost create was made by that create.
- **The read shows the old state.**
  The task fails with `maybe_applied: true` and a message that says `the change may or may not have been applied`.
  The read could not confirm the change.
- **The module cannot tell.**
  A value that cannot be read back, such as a password, leaves nothing to compare, and the module says so and fails with `maybe_applied: true`.

In the second and third cases, a second run of a module that compares state is safe.
It reads first and writes only what still differs, so it does the right thing whether or not the first write arrived.
For an action, such as `snapshot` without a `name`, a second run repeats the action, so look at the cluster first.
[Troubleshooting](../reference/troubleshooting.md#a-change-that-may-or-may-not-have-been-applied) lists what each message means by symptom.

The rule exists because a repeated write is not always harmless.
A second snapshot is a second snapshot.
A second clamp PUT bounces quorum again.
A second create of a replication relationship can leave two records of one key.
A second S3 key create uses up the second of the two keys a user may hold.
So `snapshot` never requests a snapshot again, and with a `name` it lists the directory's snapshots to find the one the lost request took.
[encryption_key_rotation](../../plugins/modules/encryption_key_rotation.py) never sends the rotation twice and confirms it by reading the key store.
[replication_relationship](../../plugins/modules/replication_relationship.py) never sends a create twice and reconciles by listing, and two records of the key fail the module, naming both ids.
[s3_access_key](../../plugins/modules/s3_access_key.py) removes a key it cannot account for and fails, as when the connection is lost after the key is made or the listing after the make does not show it, so that a rerun starts clean.

The [gap register](../reference/gaps.md) says which operations the REST API does not let the collection make idempotent.

## Actions are not settings

A setting has a state that a module can read, compare and write.
An action has none.
Clearing the DNS cache, taking a snapshot, rotating the encryption key, running an upgrade and starting a replication job are things the cluster does, not things it holds, so running them again repeats them ([G-ACTIONS](../reference/gaps.md)).
There is nothing to read back that says whether the cache holds anything, so a module for a clear cannot tell that the work is already done.

The collection handles an action in one of three ways.

**Report a change on every run.**
[dns_cache_clear](../../plugins/modules/dns_cache_clear.py) and [auth_cache_clear](../../plugins/modules/auth_cache_clear.py) always report a change, and in check mode report the change they would make without sending it.
The baseline does not run them on every run.
It clears the DNS cache when the DNS servers changed, including when the DNS step fails after a write that landed, since the next run would see matching servers and never clear the cache.
It clears the authentication cache when the user-defined mappings or identity attributes changed.

The DNS case shows why the trigger is exact.
The cluster checks its lookup overrides before its cache on every lookup, and never caches their answers, so a change to `dns_lookup_overrides` needs no clearing.
A change to the servers does, because the cache still holds what the old servers answered, failed lookups included, and the domain join that follows would meet it.
A clear that a lost connection interrupts is sent again, since clearing twice does no harm, which is why its route is declared safe to repeat.

**Let the cluster say whether it is due.**
`encryption_key_rotation` rotates the key only when it was last rotated more than `max_age_days` ago.
The time of the last rotation comes from the cluster, so a scheduled run rotates once per period and changes nothing between.
With a KMS key store, the cluster rotates only to another key the KMS holds, so the module rotates when `key_id` differs from the key in use.
A schedule with one fixed `key_id` therefore rotates once and never again.
[Rotate the encryption key](../how-to/rotate-encryption-key.md) has the play.

**Find the result of the last run by name.**
A snapshot is a record the cluster keeps, so a module can look for one.
Since a name alone never finds a snapshot ([Keys, not ids](#keys-not-ids)), `reuse_existing` makes `snapshot` look for a snapshot of the directory whose name is the given one after its id, returns it and takes none.
A rerun of a play then finds the snapshot it took before.
The trade is that a rerun never takes a fresh snapshot, so the name is what ties a snapshot to one change ([Take a snapshot before a change](../how-to/snapshot-before-a-change.md)).

The baseline takes no snapshots and rotates no keys, and the reason is the model.
A declaration is something the collection can hold true, and "a snapshot was taken" is true only at one moment.
What the inventory declares is a snapshot policy, which is a setting with a schedule, and the cluster takes the snapshots.

## Values the cluster shows once

Some values exist only in the response that creates them, and the collection manages two: S3 access keys and access tokens.
For an S3 access key, the cluster makes both the key's id and its secret, returns the secret only in the response to the request that makes the key, holds at most two keys per user, and offers no route that imports a key you already have ([G-S3-KEY](../reference/gaps.md)).
An access token is the same case: the cluster returns its bearer only in the response to the create, holds at most two tokens per user, and imports none ([G-TOKEN](../reference/gaps.md)).
A write-only password is a different case, because the inventory chooses it.
Here the cluster chooses the value, so a declaration can ask for a key or a token and can never supply one.

Each rule of the S3 key step follows from that, and `access_token` follows the same rules of the file and `no_log`; [Authentication](authentication.md#access-tokens-the-collection-makes) gives the token's own.

**The secret goes to a file, and the module refuses to make a key without a safe place for it.**
`s3_access_key` writes the secret at once to a file of its own in `secret_destination`, readable only by its owner, and returns it in that run's result only.
Without `no_log`, Ansible would print that result, secret included, to the terminal and to every log and callback.
So the module refuses to make a key in a task without `no_log`, or without `secret_destination`.
The baseline writes the file under `cluster_baseline_artifact_dir`, names it in the run's output, and lists each key made in `cluster_baseline_s3_access_keys` without its secret.

**The key step runs last.**
It is step 22, after every other step, so that no later step's failure can stop the run once a key is made.
Within the step the removals come first.
Removals go first so that a user at the limit of two can be given a new key in the same run.
The step runs every entry and then fails, naming each entry that failed.

**Declare the key's id once the key exists.**
An entry with only `user` means that the user holds a key.
Such a declaration cannot tell a key whose secret its consumers hold from one whose secret was lost.
The declaration names the key's id once the key exists.
The id makes the declaration precise in the way a rebuild needs.
A rebuilt cluster has none of the old keys, so the replay's baseline makes a key for each declared id, and only while the user holds fewer keys than its entries declare.
A run after the rebuild that still names the old ids fails and names the keys the user holds, and does not make more of them.
An entry without an id makes a key only if the user holds none.

**A key cannot be replaced without an outage for its consumers.**
The cluster picks a key's id when it makes the key and takes no name for it, so a declaration can name only a key that exists, never a second key to hand out before the first goes ([G-S3-ROTATE](../reference/gaps.md)).
To replace a key, you remove it with `state: absent` and its `access_key_id`.
The next run of the entry that names the removed id makes a new key, unless the user already holds as many keys as its entries declare.
The entry then needs the new id.
Its consumers lose access until they hold the new secret.
[Serve a directory as an S3 bucket](../how-to/s3.md) has the procedure.

A capture cannot read a key's secret either.
It names each key by its user and id, and each secret under `supply`.
The edge rebuild preflight counts the keys a cluster holds and refuses to destroy it unless `s3_keys` is acknowledged, since the replay can only make keys again.
Every S3 client needs its new secret after a rebuild, and the rebuild report names each key it made and each entry that failed.

Other values the cluster never returns are supplied by the inventory instead: the certificate and its private key (G-SSL-READBACK), and a Nexus join key, which registers one cluster only ([G-NEXUS-KEY](../reference/gaps.md)).
They go in encrypted with ansible-vault, and the capture's `supply` comments name each one.

## What this model will not do

The collection does not guess what you meant.
It will not remove an object because a line is gone, copy a value from a cluster it is destroying, resend a write it cannot account for, or turn an action into a setting by pretending it can be compared.
Where it cannot decide, it stops and says what would settle it, and [Refusals and guards](refusals-and-guards.md) describes the shapes those stops take.

## Learn more

- [Your first cluster](../tutorials/first-cluster.md) takes a cluster you already run through adoption and the zero-diff gate.
- [Adopt a cluster you already run](../how-to/adopt-a-cluster.md), [Declare common settings in the inventory](../how-to/inventory-recipes.md) and [Set up an inventory for your clusters](../how-to/set-up-inventory.md) put these rules to work.
- [Serve a directory as an S3 bucket](../how-to/s3.md), [Rotate the encryption key](../how-to/rotate-encryption-key.md) and [Take a snapshot before a change](../how-to/snapshot-before-a-change.md) are the recipes behind the sections on shown-once values and actions.
- [Respond to a refusal](../how-to/respond-to-a-refusal.md) says what to do when a run stops, including after a lost connection.
- [Inventory variables](../reference/variables.md) lists every variable and its step, and [What capture and clone read and write](../reference/capture-and-clone.md) lists what a capture holds.
- [Replication](replication.md), [Edges and portals](edges-and-portals.md) and [Identity](identity.md) apply the keys-not-ids and check-and-diff rules to their own objects.
