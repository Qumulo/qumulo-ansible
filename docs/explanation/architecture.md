<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Architecture of the collection

This page explains how the collection is built and why it runs where it does.
It covers what the parts are and how they fit, why the modules run on the controller, how connection settings reach
them, which modules never connect to a cluster, why the baseline applies areas in the order it does, and why the
Terraform inventory plugin, the artifacts directory and the `warn` filter are shaped as they are.

## The parts

Every request to a cluster goes through one module, `client.py`; modules build on it, roles put modules in an order, and
playbooks give the roles a connection and hosts.

- **`module_utils`** holds the shared code.
  [client.py](../../plugins/module_utils/client.py) is the only HTTP path to a cluster's REST API: login, the identity
  guard, retries, paging and `If-Match`.
  [resource.py](../../plugins/module_utils/resource.py) is the shared base of the resource modules: read, compare,
  check mode, diff, and the read-back after a write.
  The rest, such as `acl.py`, `identity.py`, `normalize.py` and `version.py`, each own one fact about the API,
  so a module that needs it calls the owner and does not carry its own copy.
- **Modules** are one task each: one object, one read, one action, or one pure computation.
  A resource module declares what the cluster should hold, and the shared code decides whether a write is needed.
- **Roles** compose modules in an order.
  [cluster_baseline](../../roles/cluster_baseline/) applies the inventory's areas.
  [portal_pair](../../roles/portal_pair/) and [replication_pair](../../roles/replication_pair/) set up one relationship
  between two clusters, and [replication_failover](../../roles/replication_failover/) fails relationships over to their
  replicas and back ([Replication](replication.md#failing-over)).
  [edge_rebuild](../../roles/edge_rebuild/) and [edge_cache_resize](../../roles/edge_cache_resize/) replace or resize
  an edge, and [cluster_upgrade](../../roles/cluster_upgrade/) follows an upgrade plan.
- **Playbooks** set the connection for their plays and call the roles or modules, and the header comment of each says how
  to run it. [Playbooks and roles](../reference/playbooks-and-roles.md) lists them.
  A playbook of your own copies the `module_defaults` block from [baseline.yml](../../playbooks/baseline.yml), as [Set up
  an inventory](../how-to/set-up-inventory.md) shows.
- **The inventory plugin** [terraform](../../plugins/inventory/terraform.py) turns a Terraform root's output into hosts.
  The **filters** (`artifact_dir`, `warn`, and helpers for the upgrade, the restore, tenancy, lock keys, the
  ownership check and capture) and one **action plugin**, `replication_restore_lock`, serve the roles and playbooks.

The inventory is the source of truth for what the baseline applies.
[State management](state-management.md) explains how a declaration in it becomes a change.

## Why the modules run on the controller

The hosts in the inventory are REST endpoints.
Ansible's default connection copies a module to a managed machine over SSH and runs it there, and the collection does
not reach a cluster that way.
Each module runs on the controller and makes HTTPS requests to the address in `api_host`.

The inventory records this in two variables that [examples/inventory/group_vars/qumulo_clusters.yml](../../examples/inventory/group_vars/qumulo_clusters.yml) sets for every
cluster:

```yaml
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
```

`ansible_connection: local` runs the module on the controller, and the interpreter variable runs it under the same
Python as `ansible-playbook`, the one that already has ansible-core and its libraries.
A cluster's inventory host is then a name and a set of variables: its address, its UUID, the credentials it logs in
with, and the areas it should hold.

Three consequences follow.

- **One play can address many clusters.** The play's hosts are the clusters, each task runs once per cluster, and
  `--limit` selects clusters in the usual way.
  A task that needs two clusters, such as pairing a spoke with its hub, reads the other cluster's connection from
  `hostvars` and passes it to the modules that talk to that side.
- **A play's facts can change the connection between tasks.** A network change can move the address, a certificate step
  can change the CA, and a password change can change the login.
  The baseline records the new values as facts after the step, and the next task's connection follows them.
  [The order of areas](#the-order-of-areas) shows where this matters.
- **The records and locks a run keeps are on the controller.** They sit beside the inventory, not on the clusters.
  [The artifacts directory](#the-artifacts-directory) explains where.

## How connection settings reach the modules

Every module that talks to a cluster takes the same connection options: `api_host`, `api_port`, `api_username`,
`api_password`, `api_token`, `validate_certs`, `ca_path`, `request_timeout`, `retry_timeout` and
`expected_cluster_uuid`.
`client.py` defines them once, and each can also come from a `QUMULO_*`
environment variable.
[Authentication](authentication.md) explains what each one does and what the module checks before it sends a password.

A task does not repeat them.
[meta/runtime.yml](../../meta/runtime.yml) declares an action group, `qumulo`, that holds the modules that talk to a
cluster, and a play sets the options for the whole group once:

```yaml
module_defaults:
  group/qumulo.cluster.qumulo:
    api_host: "{{ qumulo_api_host }}"
    api_password: "{{ qumulo_api_password | default('') }}"
    expected_cluster_uuid: "{{ qumulo_cluster_uuid | default('') }}"
    # ...
```

[playbooks/baseline.yml](../../playbooks/baseline.yml) has the full block.
Ansible templates `module_defaults` for each task, so a fact the baseline sets in the middle of a run, such as the new
address after a network change, reaches the tasks after it.
`cluster_baseline`, `edge_rebuild`, `edge_cache_resize` and `cluster_upgrade` run under the play's defaults for the
play's own cluster, so one of them and a task of your own in the same play share one setup.
A role that works on a pair of clusters takes one set of connection options per side, and runs once per pair.
`portal_pair` takes the spoke and the hub, and `replication_pair` the source and the target.
Each of its tasks sets `module_defaults` for the side it addresses.
The baseline builds both sets from `hostvars` (`connections.yml`).

The inventory variables are named `qumulo_*` and the module options are not, and the play's `module_defaults` block joins the two.
[Inventory variables](../reference/variables.md) lists the connection variables and the option each feeds.

## Modules that connect to nothing

Eleven modules are not in the action group: `upgrade_plan`, `upgrade_report`, `edge_cache_route`,
`replication_pair_plan`, `replication_failover_plan`, `replication_restore_step`, `replication_restore_lock`,
`replication_restore_info`, `tenancy_plan`, `clone_host_vars` and `host_vars_text`.
Each runs on the controller and sends no request.
They do not take connection options, and `module_defaults` for the `qumulo` group do not reach them.
Most take the results of other tasks, such as `cluster_info`, `capture_info` or a module run in check mode, and return a
decision: a plan, a route or a status.
The others return a record, a file's text, a lock on the controller, or a listing of the restore records and locks
there.

The split gives the roles a pattern.
Modules that connect read and write; a module that does not connect decides, from what the reads returned.

- **A decision can span clusters.** `upgrade_plan` needs every inventory host's reads and variables at once, so it runs
  once for the play and not once per cluster.
  `replication_pair_plan` joins the check-mode result on the source with the one on the target into one status.
  A module that talks to one cluster sees only that cluster.
- **A decision can refuse before the first change.** The upgrade plan's refusals come from the inventory and the reads
  alone, so the whole fleet is refused before a request leaves that changes anything.
  The `replication_pair` role runs both checks before it writes on either cluster, passes both results to
  `replication_pair_plan` and acts on its status.
- **A decision can hold a whole inventory's rules.** `tenancy_plan` takes the inventory's tenants, networks, shares and
  exports with what step 2 read from the cluster, and returns the writes of steps 3a to 20a or the refusals, so every
  rule of the tenancy steps is a unit test with no cluster.
  It judges the continuity rule on each write as it will be immediately after it, which a module run in check mode
  cannot see for a tenant or network that an earlier write in the same run makes.
- **A lock belongs to the run on the controller.** `replication_restore_lock` is an action plugin, so it runs inside the
  `ansible-playbook` process's worker and records that process; on this controller the lock lives as long as the run
  that took it.
  A module would run in a child process of its own and could not name the run.
  Because the lock is a file on the controller, it does not stop a second controller.

`host_vars_text` is the one whose design is about the controller's own safety.
It writes a `host_vars` file from values read from a cluster, and it renders the file so that nothing in it can run on
the controller.
Ansible templates every string it reads from an inventory file, so a captured string that holds a Jinja expression,
such as a share's description, would run on the controller the next time a play reads the file.
The module therefore tags every string value `!unsafe`, so Ansible reads it as the string itself.
Only strings are tagged, so a template that a person adds to the file later, such as a reference to a vaulted secret,
still evaluates.
Two further rules follow from the same concern.

- Ansible reads an `!unsafe` scalar's type again as if it had no tag, so a string that would read as another type, such
  as `0777` or `true`, would come back as a number or a boolean.
  The module writes such a string quoted and without the tag, which is safe because a string that resolves to another type
  cannot hold a template.
  The empty string is written the same way.
- Every comment line is wrapped and starts with `#`, so a newline in a captured path cannot end a comment and begin
  a line that Ansible reads as a variable.

[playbooks/capture.yml](../../playbooks/capture.yml) and the clone playbook write their files with it, and [What capture and clone read and
write](../reference/capture-and-clone.md) lists what they contain.

## The pieces of an upgrade

The upgrade shows how the layers divide the work.
Four pieces do it, and each can be used alone: the `upgrade` module, which takes one cluster through one hop,
`upgrade_plan`, `upgrade_report` and the `cluster_upgrade` role.
[One hop, one plan, one fleet run](upgrades.md#one-hop-one-plan-one-fleet-run) says what each does.

The playbook [upgrade.yml](../../playbooks/upgrade.yml) reads each cluster with `cluster_info`, plans once with
`run_once`, takes the run's locks, and calls the role.
The plan is a separate step for two reasons.
It sees the whole fleet, which the order of commits needs: a portal's spokes before their hub, and, where the plan can
arrange it, a replication target before its source.
It connects to nothing, and it can refuse before anything but reads has gone to a cluster.
[Upgrades](upgrades.md) explains the hop rules, and [the upgrade reference](../reference/upgrade.md) lists the
variables, steps and files.

## Reading, deriving and then writing: the clone

The clone playbook follows the same pattern at a smaller scale.
[clone_edge.yml](../../playbooks/clone_edge.yml) reads the source with `capture_info`, derives the target's variables
with `clone_host_vars`, renders them with `host_vars_text`, writes the file, and only then calls the
`cluster_baseline` role on the target.
Reading the source changes nothing on it, and deriving and rendering are computations on the controller, so the clone's
refusals come before anything changes on the target.
The change to the target is the last step, and it goes through the baseline every cluster uses.
The clone therefore has no way of its own to configure a cluster.
It produces an inventory entry, and the baseline applies it.

## The order of areas

`cluster_baseline` applies the areas in an order the server's rules fix.
An area whose `qumulo_*` variable is unset is skipped, and the rest keep their relative order.
This section gives each dependency that places a step.
The step numbers are those in the role's documentation,
`ansible-doc -t role qumulo.cluster.cluster_baseline`.
[Inventory variables](../reference/variables.md) maps each variable to its step.

**Checks come first, and all of them before a write.**
The guard (step 1) confirms the cluster is the one the inventory names and waits until it is ready.
The role skips it when the `edge_rebuild` role has just checked the cluster itself.
Step 2 runs five checks, and all of them fail before anything is written: that no declared area belongs to Terraform,
that no quota, snapshot policy or S3 bucket lies under a spoke root of the portal (the cluster refuses the first two
there, and a bucket there would serve the hub's data), that no path declares both a mode and an ACL, that no keytab is
declared to install beside a domain join (a joined cluster takes no keytab; [Where Kerberos for NFS gets its
keys](identity.md#where-kerberos-for-nfs-gets-its-keys)), and that the key store holds the lock key of each snapshot
policy that declares one, unless `qumulo_allow_deferred_policy_lock_keys` consents ([A
consent](refusals-and-guards.md#a-consent)).
Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.
When the inventory declares tenants, networks, shares, exports, NFS settings or factory defaults, step 2 also reads the
cluster's tenants, network document, shares and exports once, and `tenancy_plan` judges every write of steps 3a to 20a against them: the inventory's
own rules, and the continuity rule for each write that could cut the play off.
A run that is going to refuse therefore leaves the cluster as it was.
[Refusals and guards](refusals-and-guards.md) explains why a module refuses instead of guessing, [Terraform
ownership](terraform-ownership.md) explains the first check, and [Tenants and the continuity
rule](multitenancy.md#the-continuity-rule) explains the tenancy checks.

**Tenants around the network (steps 3a, 3 and 3b).**
Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.
The tenants and the network document are written in three parts, so that every service turned off comes after every
network is bound.
Step 3a renames the tenants, turns services on and creates tenants. Step 3 writes the one network document, with each
network's tenant. Step 3b turns services off.
A service turned on comes before the network write so that a tenant that will hold a network already serves, and a
service turned off comes after it so that the network the play connects on is in its new tenant before the old one loses
REST.
A tenant that is created with no network and nothing on needs no other order.
[Tenants and the continuity rule](multitenancy.md#why-the-network-document-holds-every-binding) explains why one write
carries every binding.

**Addressing before everything that uses it (steps 3 and 4).**
The network goes first, since DNS, time and the domain join all depend on addressing, and the hub accepts a portal by the spoke's
addresses.
DNS comes next, then the lookup overrides, then authoritative DNS, then the time source.
The domain join needs DNS and time.
The lookup overrides can name its domain controllers, so they are applied before it.
Authoritative DNS comes after the network because the server answers with the floating addresses the network step sets.
The lookup overrides need no cache clear, since the cluster checks them before its cache; [Actions are not
settings](state-management.md#actions-are-not-settings) explains when the DNS cache is cleared and why.

**The name before the join (step 5).**
The server refuses a rename while the cluster is joined to a domain, and the name becomes the computer account's name, so
it is set before the join in step 14.
[Identity](identity.md) explains the account.
The file system and Web UI settings are cluster-wide and depend on nothing before them.

**The certificate before whatever verifies it (step 6).**
The SSL certificate and ciphers come before any step that verifies the new certificate.
When `qumulo_ssl.ca_path_after` is set, the role switches the play to that CA and turns verification on after the
certificate step, so the passwords the later steps send go over verified TLS.
A rebuild's replay meets a factory certificate that cannot be verified (G-FACTORY-TLS), so it starts with
`qumulo_validate_certs: false` and relies on `ca_path_after` to verify the cluster from the certificate step on;
[Authentication](authentication.md#tls) explains the sequence.

**Monitoring and registration (step 7).**
Monitoring is enabled before the Nexus connection can be, and the cluster is registered once the connection is on and a
join key exists or the token to mint one.
The registration comes before the license because Nexus sees the cluster's UUID, which a license is issued for, only
once it is registered.
No other monitoring write runs between the registration and its result, because the cluster rotates a secret in the
background and a write in that window races with it.
[Licensing, capacity and Nexus](licensing-and-capacity.md) explains the registration.

**The license, then the clamp (steps 8 to 11).**
Qumulo issues the license outside the collection (step 8), so on a new UUID the document arrives between the first pass
and the second.
Step 9 applies the document when it is for this cluster's UUID and otherwise reads the license; nothing waits for one.
The clamp (step 10) follows the license.
A license that raises usable capacity takes effect only at the next quorum formation, and every write of the clamp makes
the cluster abandon and re-form quorum, so the license goes in first.
While a license is expected and not active the clamp waits for it.
The second pass, which is the role again or `playbooks/edge_license_complete.yml`, runs the same step.
The eviction threshold (step 11) is cluster-wide and safe before the spoke exists.
Step 12, the encryption key store (`encryption_key_store`), is not in this release.
[Licensing, capacity and Nexus](licensing-and-capacity.md) explains why the clamp waits.

**Users, then the join, then everything that names a domain user (steps 13 to 15).**
A primary group must exist before its users, so groups come first without members, then the users, then the members.
The keytab of a standalone Kerberos KDC goes before the join, because the cluster refuses a join while it holds a keytab
and a keytab once it is joined (G-KRB-AD), so a keytab declared absent beside a join is deleted first.
The Kerberos setting comes after the keytab, since deleting the keytab turns that setting's mapping off (QUIRK-161).
The domain join (step 14) needs DNS, time and the name from the steps above.
Time's `use_ad_for_primary` takes effect only once the cluster has joined a domain, so it is set after the join.
The user-defined mappings need the join, and so do identity attributes whose trustees name the domain (`ad:`).
The authentication cache is cleared after them ([Actions are not
settings](state-management.md#actions-are-not-settings) explains when and why).
SAML follows, since only users of the joined domain sign in through the identity provider.
Roles and their members come after users and the join (step 15): an `ad:` trustee resolves only after the join (bar `ad:NT AUTHORITY\Authenticated Users`), a role must
exist before its members, and the cluster refuses to delete a role that still has members, so the roles declared absent
are removed last.

**The portal before what lies beneath it (step 16).**
A spoke's portal proposes its roots, and the hub accepts and authorizes them.
The directories, shares, exports and buckets that lie under an authorized root need the portal to exist, so they come
after it.
Hub and spoke must run the same Qumulo Core version, which the `portal_pair` role checks first.
[Edges and portals](edges-and-portals.md) explains the model.

**Directories, then shares, exports and buckets (steps 17 to 19).**
Directories and ACLs come first (step 17), with paths that may lie under the portal's authorized roots.
SMB shares come next, since a share needs its directory (step 18).
Each of steps 18 and 19 starts with the removals of the run, the shares and exports declared `state: absent` and those of
a tenant being deleted, because NFSv4.1 cannot be on for a tenant while two of its export paths nest, and a run that
turns it on can then remove the nested exports in the same pass.
A tenant's override follows the cluster-wide settings and comes before the shares and exports it governs.
NFS comes next, where NFSv4 cannot be turned on while the nested `/` export exists, which is why the role sets the factory
export before the NFS settings and the exports.
S3 comes with it: buckets and the base path need the directories of step 17, and bucket policies name principals
that the users and the domain join of steps 13 to 15 provide.
FTP follows the directories and ACLs of step 17, so that a new cluster serves nothing over FTP before they are in place,
and the local users of step 13, one of which the anonymous user names.

**Quotas and snapshot policies (step 20).**
They apply on local paths only, because the server refuses a spoke path for both, and step 2 has already refused one.

**A tenant is deleted last (step 20a).**
Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.
The baseline deletes a tenant declared absent after everything that was declared under it is gone: its networks at step 3,
its shares and exports at the start of steps 18 and 19, and then at step 20a its SMB and NFS overrides and the tenant.
The cluster would delete the content with the tenant, but it leaves stale entries in the export tree, so the baseline
never relies on that.

**Replication after the directories (step 21).**
A replication relationship replicates directories, and its target host declares the target path in its own
directories, so it comes after them.
The role checks every relationship before it runs any, then runs the removals and then the others, and a relationship
that fails does not stop the next one; the step fails after the last, naming each.
[Replication](replication.md) explains the model.

**S3 access keys last (step 22).**
The cluster returns a new key's secret once.
Each new secret is written to a file the moment the key is made, and this step is last so that no later failure
interrupts the role between making a key and naming its file.
[State management](state-management.md#values-the-cluster-shows-once) explains values shown once.

The role's documentation keeps the order and the constraints on the cluster, and this section keeps the reasons.
A second run of the baseline changes nothing, apart from local passwords declared with `update_password=always`.

## The inventory drives every role

The `portal_pair` role, and `replication_pair`'s main entry point, take no inventory variables.
`cluster_baseline` builds their arguments from `qumulo_portal` and `qumulo_portal_hosts`, and from
`qumulo_replication_relationships` and the target host's variables.
Declaring a relationship in the inventory therefore makes the baseline pair it.
`edge_rebuild` drains a portal with `portal_pair` directly, and pairs again by replaying the baseline.
`edge_cache_resize` calls `edge_rebuild` when a smaller cache needs a rebuild.
The restore entry point of `replication_pair` reads the edge's own `qumulo_replication_relationships`, so it too works
from the inventory.
The result is that the inventory is the one place a configuration lives, and the roles are ways of applying it.
[Rebuilds](rebuilds.md) explains the replay.

## The artifacts directory

The roles keep a host's records and secrets in an `artifacts` directory.
The [artifact_dir](../../plugins/filter/artifact_dir.yml) filter picks it.
It is the directory beside the outermost inventory source on disk that holds the file defining the host, and it is
never inside that source.
The filter's result is the default of `cluster_baseline_artifact_dir`, `cluster_upgrade_artifact_dir` and
`edge_rebuild_artifact_dir`, and through the last, of `replication_pair_artifact_dir`.

It sits beside the inventory and not inside it because Ansible reads every `.yml` file in an inventory directory as an
inventory, and a record or a secret kept there would be read as one.
The example project keeps the CA file outside the inventory directory for the same reason.

A host from no inventory file or directory, such as `-i edge01,`, has no such place.
The filter returns an empty string, and a step that needs a directory, such as one that would make an S3 key, fails.
The upgrade playbook therefore refuses such a host before it plans, rather than losing its commit records.

## The Terraform inventory plugin

The `qumulo.cluster.terraform` plugin reads the `qumulo_ansible_inventory` output of a Terraform root and adds a host
for each entry.
[Terraform inventory plugin reference](../reference/terraform-inventory.md) lists its fields, and [Terraform
ownership](terraform-ownership.md) explains which areas Terraform owns.
Four choices in it follow from the design above.

- **The hosts carry no `ansible_connection`.** The plugin sets the facts about a cluster, such as its address and UUID,
  and not how Ansible reaches it.
  The collection's modules run on the controller, so `group_vars` set `ansible_connection: local`, as the example
  inventory does.
- **The plugin refuses a file that lists every output.** `terraform output -json` without a name prints every
  output of the root, sensitive ones in plain text.
  The plugin takes the one output it needs, from `terraform output` or from a file that holds only that output, and
  refuses a file that holds the whole listing.
- **The plugin removes the `TF_LOG` variables from the environment Terraform runs in.** A failure's message quotes
  Terraform's standard error, and with a trace log on it would carry the trace into Ansible's output.
- **The output never holds the password.** An entry gives a `password_ref`, a pointer, and `group_vars` resolve it with a
  lookup.
  A field whose name looks like a secret is refused, and its value is neither read into a variable, nor cached, nor shown.

A file read with `output_file` has one further trap.
The `ansible.builtin.yaml` inventory plugin reads `.json` files in an inventory directory as inventories of their own.
A saved output kept in a directory given to `-i` is parsed as one, which is why a saved output belongs outside the
inventory directory, for the same reason as the artifacts.
[Use a Terraform root's output as the inventory](../how-to/terraform.md) shows where to save it.

## How roles report

A role that has something to tell the operator without failing, such as the `replication_pair` role when it cannot write a
restore stamp, does it through the [warn](../../plugins/filter/warn.yml) filter.
The filter shows the text as an Ansible warning, which the run lists with the other tasks' warnings, and returns the
text, so it can sit inside the expression that records it.
`ansible.builtin.debug` would print the text as that task's output only, where it scrolls past.

## Where to go next

- [Playbooks and roles](../reference/playbooks-and-roles.md) lists each playbook and role and what it does.
- [Inventory variables](../reference/variables.md) lists every variable and the area and module it feeds.
- [Set up an inventory](../how-to/set-up-inventory.md) lays out an inventory directory and its connection.
- [State management](state-management.md), [Authentication](authentication.md) and [Refusals and
  guards](refusals-and-guards.md) explain the behaviour the layers above produce.
- The [cluster_baseline](../../roles/cluster_baseline/) role's reference lists the baseline's steps in order.
