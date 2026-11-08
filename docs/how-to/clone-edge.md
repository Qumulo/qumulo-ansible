<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Clone an edge's configuration onto a new edge

This page gives a fresh edge the configuration of an edge you already run, under its own UUID and name.
The source keeps running as it was: [`clone_edge.yml`](../../playbooks/clone_edge.yml) only reads it.
The playbook writes the new edge's `host_vars` file, then runs the baseline on it.

A clone copies configuration only.
No data is copied, and the new edge starts with empty directories.
To copy data, [replicate a directory](replicate-a-directory.md).
To replace a failed edge under the same name, [rebuild it](edge-rebuild.md) instead; a clone is a second cluster
beside the source ([why they differ](../explanation/rebuilds.md)).

## Before you start

- The source is an inventory host, as [Adopt a cluster you already run](adopt-a-cluster.md) leaves it.
  A cluster outside the inventory also works; see [Clone a source outside the inventory](#clone-a-source-outside-the-inventory).
- The target is a cluster that is formed and ACTIVE, as Terraform or Qumulo's installer leaves it, and holds nothing
  else yet.
  Give it a host entry of its own in `qumulo_edges`, with its address and credentials, and no `host_vars` file.
  You can leave out its UUID, because the clone reads it.
  If Terraform formed the target, its host entry or `group_vars` gives `qumulo_managed_by` and
  `qumulo_terraform_owned_areas`. The clone copies neither.
- The target needs a name that differs from the source's and from every other cluster's in the inventory.
  The name is also its computer account in the domain.
- You have the secrets the first baseline needs: a password for each local user the target lacks, other than `admin`
  and `guest`, and, if the source is joined to a domain, the join user and password.
- Each lock key a snapshot policy of the source uses is registered on the target, or you pass
  `-e qumulo_allow_deferred_policy_lock_keys=true` to step 3 ([why](manage-lock-keys.md#a-rebuilt-or-cloned-cluster)).
- If the source is registered with Nexus, you have a join key for the target, or `qumulo_nexus_api_token` in the
  inventory.
  With either, the first pass registers the target, and Qumulo can then issue its license.

## Clone the edge

1. Add the target to the inventory:

   ```yaml
   # inventory/hosts.yml, in part
   qumulo_clusters:
     children:
       qumulo_edges:
         hosts:
           edge01:
             qumulo_api_host: 10.20.0.5
             qumulo_cluster_uuid: cbb6f13a-d92f-4dfb-a7f0-d0a6785da65d
           edge02:
             qumulo_api_host: 10.20.0.6
   ```

2. Put the secrets in a vars file of their own, under `clone_edge_supply`, and encrypt it
   ([Keep secrets out of the inventory](store-secrets.md)).
   Each key is the name the clone's file gives the secret in its comments:

   ```yaml
   # clone-secrets.yml, encrypted with ansible-vault
   clone_edge_supply:
     "qumulo_local_users[svc-backup].password": "..."
     qumulo_ad.join_user: svc-qjoin
     qumulo_ad.join_password: "..."
     qumulo_monitoring.nexus_join_key: "..."
   ```

   Give only the keys the file's supply lines name. The clone refuses any other key.
   When `group_vars` defines `qumulo_ad` or `qumulo_monitoring` for the target, as `examples/inventory` does, the file
   has no supply line for their keys.
   Leave those keys out: the target joins with the group's credentials, and registers with `qumulo_nexus_api_token`
   ([Register a cluster with Nexus](nexus.md#register-with-an-api-token)).

   If you are unsure of the keys, run step 3 once with `-e clone_edge_check_only=true` and without
   `-e @clone-secrets.yml`.
   That run writes the file and changes nothing on the target.
   The supply lines at the end of the file name every key, and the run fails, naming those the first pass can't do
   without.
   `clone_edge_check_only` refuses a target whose inventory declares a portal or replication relationships; a target
   before its first clone normally declares neither.

3. Run the clone:

   ```sh
   ansible-playbook qumulo.cluster.clone_edge -i inventory \
     -e clone_edge_source=edge01 -e clone_edge_target=edge02 -e clone_edge_name=edge02 \
     -e @clone-secrets.yml --ask-vault-pass
   ```

   The playbook:

   - checks that the target is formed and fresh, and that no other inventory host has its UUID;
   - reads everything the collection configures on the source, as capture does;
   - derives the target's variables, replacing what belongs to the source alone;
   - writes them to `inventory/host_vars/edge02.yml`;
   - runs the baseline on the target, with the secrets from `clone_edge_supply`.

   The secrets are used for that run only and are never written to the file.
   Don't pass a variable the clone copies, such as `qumulo_dns`, with `-e`.
   Ansible cannot tell it from one the inventory defines, so the clone leaves it out of the file.

4. Read `inventory/host_vars/edge02.yml`.
   The file starts with a header that names the source, and ends with comments that list each secret to supply and
   each value that was read but left out, with the reason.
   [What capture and clone read and write](../reference/capture-and-clone.md) shows the file and lists what the clone
   copies, replaces and leaves out.

   The clone also leaves out each SMB share or NFS export entry that the target's inventory already declares, because
   the baseline refuses an entry declared twice.
   A flat `qumulo_smb_shares` or `qumulo_nfs_exports` in the target's `group_vars` can declare one the source holds under
   a tenant, and a `qumulo_factory_defaults` there can remove the factory `Files` share or `/` export.
   Each such entry is a note at the end of the file that names the inventory's entry, and the inventory's entry applies
   to the target.
   The target creates the share or export from the inventory's entry alone, so the clone refuses an entry that leaves
   out a key the source's entry sets to other than what a create gives
   ([the rules](../reference/capture-and-clone.md#declared-again-by-the-inventory)).

The file goes in `host_vars/` beside the inventory source that defines the target.
To write it elsewhere, add `-e clone_edge_host_vars_dir=/absolute/path`.
When `-i` names more than one source that defines the target, give the one, or set `clone_edge_host_vars_dir`.

## Declare what the clone leaves out

The baseline has now run with what the clone could copy.
Declare the rest in the target's own `host_vars` or `group_vars`:

- **A portal and replication relationships.**
  The hub's and the peers' records are the source's, and so are `qumulo_portal_hosts`, `qumulo_replication_address`
  and `qumulo_replication_port`, which hold where peers reach the source.
  Declare the target's own with spoke roots and target directories of its own:
  [Pair an edge with its hub](pair-edge-with-hub.md) and [Replicate a directory](replicate-a-directory.md).
- **What lies in the source's spoke roots or in the directories it receives replication into:** shares, exports,
  directories and their ACLs, quotas, snapshot policies, S3 buckets and their policies, and an S3 base path there.
  Their data is the hub's or a peer's, and the target has no portal yet.
  Declare them once it has one.
- **Lock keys.**
  The file names each key of the source's key store under what was left out, by name and id.
  The cluster registers a key only with its name signed by the key's private key, and keeps neither that signature nor
  the private key, so register each on the target with `security_key`, giving its private key or a signature of its
  name ([Manage the keys that lock snapshots](manage-lock-keys.md)).
- **A snapshot policy's lock key.**
  The file declares each policy's `lock_key` by the key's name, which holds on the target, where the key gets a new id.
  Where capture could not read the key store, the policy has no `lock_key` and a note in the file names the key's id;
  declare the key by its name.
  Register the keys on the target with `security_key` before step 3, which leaves the target fresh, since the freshness
  check does not read the key store, or pass `-e qumulo_allow_deferred_policy_lock_keys=true` to step 3;
  without either the baseline refuses the run at step 2, before it writes anything.
  If the clone stopped there, register each key and run step 3 again ([Clone again](#clone-again)).
  [A rebuilt or cloned cluster](manage-lock-keys.md#a-rebuilt-or-cloned-cluster) has the two ways on.
- **S3 access keys.**
  The baseline makes each key you declare and writes its secret under the target's artifact directory
  ([Serve a directory as an S3 bucket](s3.md)).
- **The network.**
  It holds the source's addresses.
  The target keeps the network it was formed with.
  To manage it, declare the target's own `qumulo_network`
  ([Change the network without cutting the play off](inventory-recipes.md#change-the-network-without-cutting-the-play-off)),
  or leave it to Terraform ([Use a Terraform root's output as the inventory](terraform.md)).
- **A tenant's networks.**
  Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
  through it on those releases, and a later Qumulo Core release may change it.
  The clone copies each tenant's entry in `qumulo_tenants`, with its name, switches, settings, shares and exports, and
  the shared entries of the flat lists, and drops each tenant's `networks`, since they hold the source's addresses; the
  file's comments give one note for each tenant that had any.
  The first pass creates the source's other tenants with no network. Declare the target's own tenant networks afterwards, as
  [Manage tenants](tenants.md#bind-a-network-to-a-tenant) shows.
  On networking its host manages, the first pass refuses before any write, with "The cluster's networking is
  host-managed and it has no frontend network yet": add the target's HOST network under one tenant and run the baseline
  once, set `qumulo_management_network` to its id if the target's network status is unavailable, then run the clone's
  baseline again ([On networking the host manages](tenants.md#on-networking-the-host-manages)).
- **CloudWatch audit destinations, and authoritative DNS `enabled` and `fqdn`.**
  A CloudWatch destination names a log group and region of the source's AWS account, and the DNS name is the source's.
- **The TLS certificate and key** for the names the target's clients use ([Manage certificates](manage-certificates.md)).
- **SAML sign-in.**
  `qumulo_saml` names the source's Web UI address, which the IdP's application is set up for.
  Declare the target's own, with an IdP application for the target
  ([Sign in through an identity provider](active-directory.md#sign-in-through-an-identity-provider)).
  Until you do, if the source requires SSO, AD users sign in to the target with a password.

## Finish the first pass

Two things may be left, in this order:

1. **Register with Nexus.**
   Without a join key or an API token, the first pass skips the registration, and nothing fails or warns.
   Add one and run the baseline again ([Register a cluster with Nexus](nexus.md)).
2. **License the target.**
   The target holds no license for its UUID, and Qumulo issues one only once the target is registered.
   The baseline's license hand-off names the UUID, and the second pass applies the document Qumulo issues for it
   ([License an edge](licensing.md#run-the-second-pass)).

Then move the secrets you gave in `clone_edge_supply` to where the file's supply lines point, encrypted: a user's
password into that user's entry in `qumulo_local_users`, and the others under the variable they name, then commit the
file.
From now on the inventory is the source of truth for the target, and
[`baseline.yml`](../../playbooks/baseline.yml) manages it as it does every cluster.

## Variants

### Preview what the baseline would change

Add `-e clone_edge_check_only=true --diff` to step 3.
The run writes the file, then runs the baseline in check mode, which changes nothing on the target.
It needs the same secrets.

It refuses a target whose inventory declares a portal or replication relationships, before it reads or writes anything:
the roles that pair them skip the pairing only under `--check`.
For such a target, run step 3 with `--check --diff` instead, which writes no file and changes nothing anywhere.

### Clone a source outside the inventory

Give the source's address and credentials instead of an inventory host.
`clone_edge_source` then only names the source, in messages and in the file's header:

```sh
ansible-playbook qumulo.cluster.clone_edge -i inventory \
  -e clone_edge_source=edge-old -e clone_edge_source_api_host=10.30.0.5 \
  -e clone_edge_source_ca_path=/srv/project/files/qumulo-ca.pem \
  -e clone_edge_target=edge02 -e clone_edge_name=edge02 -e @clone-secrets.yml --ask-vault-pass
```

Put `clone_edge_source_api_password` in the vaulted vars file too.
The header of the playbook lists every `clone_edge_source_*` variable.

### Keep the join credentials in `group_vars`

When `group_vars` defines `qumulo_ad` for the target, give no join secret in `clone_edge_supply`.
The target joins with the group's.

If the group's `qumulo_ad` holds only `join_user` and `join_password`, the file keeps the source's `qumulo_ad` without
them.
Move the two into the file's `qumulo_ad` afterwards, as its note says.
[Variables the target's inventory defines](../reference/capture-and-clone.md#variables-the-targets-inventory-defines)
gives the rule.

### Clone again

Until you edit the file, a second run with the same source writes the same file, and the baseline changes nothing.
The target is no longer fresh by then, but the clone allows it, because it wrote the file.

When the file would differ, as when the source has changed or you edited the file, the clone refuses and leaves the
file as it was.
To clone again, remove the file and run step 3 with `-e clone_edge_allow_configured_target=true`.
The same variable clones onto a target that holds configuration of its own.

On a second run, the clone does not count what the earlier clone's file holds as the inventory's, so it leaves out and
refuses the same entries the first run did.

## Verify

- `inventory/host_vars/edge02.yml` exists, and its header names the source and the target's UUID.
- Run the baseline on the target with `--check --diff`
  (`ansible-playbook qumulo.cluster.baseline -i inventory -l edge02 --check --diff --ask-vault-pass`).
  It reports no change you do not expect.

## If the clone refuses

Every refusal comes before anything changes on the target, and says what to do.
The ones you are most likely to meet:

- **The target is not fresh.**
  It holds a local user other than `admin` and `guest`, a domain join, an SMB share other than `Files`, an NFS export
  other than `/`, an S3 bucket, an S3 access key, or a tenant beyond the one formation creates.
  Give a fresh cluster as the target, or set `clone_edge_allow_configured_target=true`.
- **The file exists and differs.**
  See [Clone again](#clone-again).
- **A secret is missing.**
  The clone has written the file and names each secret the first pass cannot do without.
  Add each to `clone_edge_supply`, keyed as the supply lines in the file name them.
- **The inventory declares a share or export with other values.**
  The target's inventory declares a share or export that the file the clone would write holds too, and the message
  lists the keys whose values differ, or, as "which the inventory's entry leaves out", the keys the source's entry sets
  that the inventory's entry omits.
  Give the inventory's entry each such key with the value the source holds, change the share or export on the source to
  the inventory's values, or take the entry out of the inventory, then clone again.
  Nothing was written and nothing changed on the target.
- **The name is taken.**
  Give the target a name of its own in `clone_edge_name`.
- **The target's UUID is the source's or another inventory host's.**
  Name the fresh cluster's own host in `clone_edge_target`.
- **A key in `clone_edge_supply` names no supply line.**
  Remove it.

[Troubleshooting: cloning](../reference/troubleshooting.md#cloning) lists every refusal with its message.

## Next

- [What capture and clone read and write](../reference/capture-and-clone.md): what the clone copies, replaces and leaves
  out, and the file's shape.
- [Rebuilds](../explanation/rebuilds.md): why a clone is a second cluster and a rebuild replaces one.
- [Register a cluster with Nexus](nexus.md) and [License an edge](licensing.md): finish the first pass.
