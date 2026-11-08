<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# The qumulo.cluster documentation

The pages are grouped by what you want from them:

- To learn the collection by using it, start with a tutorial.
- To get one task done, find its how-to guide.
- To look up a variable, a module option or a message, go to the reference.
- To understand why the collection works the way it does, read an explanation.

## Tutorials

- [Your first cluster](tutorials/first-cluster.md): adopt a cluster you already run and pass the zero-diff gate.
- [Your first fleet](tutorials/first-fleet.md): adopt a hub and an edge you already run into one inventory.

## How-to guides

Getting started:

- [Install and pin the collection](how-to/install-the-collection.md): install it on a controller and pin the version.
- [Set up an inventory for your clusters](how-to/set-up-inventory.md): lay out hubs and edges, and connect to each.
- [Keep secrets out of the inventory](how-to/store-secrets.md): encrypt passwords with Ansible Vault, or look them up.
- [Adopt a cluster you already run](how-to/adopt-a-cluster.md): capture a formed cluster's settings into `host_vars`.
- [Use a Terraform root's output as the inventory](how-to/terraform.md): read hosts from the `terraform` plugin.
- [Respond to a refusal](how-to/respond-to-a-refusal.md): what to read, and what lifts each kind of refusal.

Configuring clusters:

- [Declare common settings in the inventory](how-to/inventory-recipes.md): network, DNS, roles, users, directories, ACLs,
  quotas, FTP, cluster settings and audit logs.
- [Manage tenants](how-to/tenants.md): declare tenants, bind networks to them, give them settings, shares and exports,
  rename and delete them.
- [Join a cluster to Active Directory](how-to/active-directory.md): the join, user mappings, home directories and SAML.
- [Manage the cluster's TLS certificate](how-to/manage-certificates.md): replace the factory certificate, renew it, and
  set the cipher suites.
- [Serve NFS with Kerberos from a standalone KDC](how-to/nfs-kerberos.md): install the KDC's keytab, turn on `krb5`, `krb5i` or
  `krb5p`, and require one on an export.
- [Serve a directory as an S3 bucket](how-to/s3.md): S3 settings, a bucket, its policy and an access key.
- [Register a cluster with Nexus](how-to/nexus.md): monitoring, the Nexus connection and registration, and retiring a
  UUID.
- [Look up identities, DNS names and paths](how-to/look-up-identities-and-paths.md): check trustees before an ACL or a
  mapping names them, read an Active Directory user's SID, NFS uid and groups, check a name with the cluster's resolver
  before an export names it, and turn a path into a file id and back.

Edges, hubs and replication:

- [Pair an edge with its hub through a portal](how-to/pair-edge-with-hub.md): declare, apply, check and remove a portal.
- [Replicate a directory to another cluster](how-to/replicate-a-directory.md): declare a relationship and have the
  baseline make it.
- [License an edge](how-to/licensing.md): from no license to a license the cluster holds, with its clamp set.
- [Size and resize an edge's cache](how-to/edge-sizing.md): set the clamp, grow it, and shrink by rebuilding.
- [Clone an edge's configuration onto a new edge](how-to/clone-edge.md): a fresh edge with an existing edge's settings.
- [Rebuild an unprotected edge](how-to/edge-rebuild.md): drain, form again and replay the inventory.
- [Restore a rebuilt edge's data from its replica](how-to/restore-replica.md): bring the data back from the target.
- [Fail over to a replica, and fail back](how-to/fail-over-replication.md): make the target writable when the source is
  lost, copy its writes back, and return to the source.

Upgrades and routine changes:

- [Upgrade a fleet](how-to/upgrade.md): bring every cluster to its target release with the upgrade playbook.
- [Recover an upgrade that stopped](how-to/resume-an-upgrade.md): read the report, lift the cause and run again.
- [Rotate the admin password](how-to/rotate-admin-password.md): change `admin`'s password and keep the play connected.
- [Rotate the access token a host connects with](how-to/rotate-access-token.md): replace the token, keep the play connected
  and put the new bearer in the vault.
- [Rotate the encryption key](how-to/rotate-encryption-key.md): rotate the master key for data at rest.
- [Take a snapshot before a change](how-to/snapshot-before-a-change.md): a snapshot you can find again and lock.
- [Manage the keys that lock snapshots](how-to/manage-lock-keys.md): register, inspect, replace, disable and remove a
  key in the file system key store, and lock a snapshot policy's snapshots with one.
- [Run the playbooks from a pipeline](how-to/pipeline.md): pin the collection, pass secrets, and split check from apply.

## Reference

- [Reference](reference/README.md): `ansible-doc`, the return-value rules, and the pages below.
- [Inventory variables](reference/variables.md): the groups, the connection, each area's variable and the run variables.
- [Playbooks and roles](reference/playbooks-and-roles.md): what each playbook and role does, reads and writes.
- [The Terraform inventory plugin](reference/terraform-inventory.md): the output's fields and the hosts the plugin adds.
- [What capture and clone read and write](reference/capture-and-clone.md): the areas, files and refusals of each.
- [The upgrade: variables, steps and files](reference/upgrade.md): the upgrade playbook's inputs, steps, report and
  records.
- [Tested Qumulo Core versions](reference/tested-versions.md): what each column of the tested-versions table means, and
  what the test lanes cannot show.
- [Troubleshooting](reference/troubleshooting.md): the messages a run can stop with, with the cause and the fix.
- [Gap register](reference/gaps.md): what the REST API does not let the collection make idempotent, and what each module
  does about it.
- [Quirk register](reference/quirks.md): server behaviour the collection works around.

## Explanation

- [Architecture of the collection](explanation/architecture.md): the parts, why the modules run on the controller, and
  the order of areas.
- [Authentication, trust and secrets](explanation/authentication.md): passwords and tokens, the identity guard, TLS and
  secrets.
- [State management](explanation/state-management.md): unset means unmanaged, capture, the zero-diff gate, check and
  diff, keys not ids, and maybe applied.
- [Refusals and guards](explanation/refusals-and-guards.md): why a module refuses instead of guessing.
- [Edges, hubs and portals](explanation/edges-and-portals.md): the hub and spoke model, spoke roots and portal
  addresses.
- [Licensing and capacity](explanation/licensing-and-capacity.md): a license per UUID, the two passes, the clamp and
  Nexus registration.
- [Rebuilds](explanation/rebuilds.md): why a rebuild replays the inventory and never repairs the old edge.
- [Replication](explanation/replication.md): relationships, releases, failing over, failing back and the restore.
- [How upgrades work](explanation/upgrades.md): hops, the order of commits across portals and replication, reboots and
  maintenance windows.
- [Terraform ownership](explanation/terraform-ownership.md): how Terraform and the collection share a cluster.
- [Tenants and the continuity rule](explanation/multitenancy.md): what a tenant partitions, the listener switch at the
  second tenant, and the rule that keeps the play connected.
- [Identity and Active Directory](explanation/identity.md): trustees, the computer account, mappings, home directories
  and SAML.
