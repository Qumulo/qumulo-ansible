<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Adopt a cluster you already run

This page captures the settings of a formed cluster into a `host_vars` file in your inventory, and fixes what stops an
adoption.
When you finish, the inventory holds what the cluster holds, and a check run of the baseline from that file is ready to
show whether the two agree.
Capture only reads the cluster and changes nothing on it.
It is a one-time import: from then on the inventory is the source of truth
([why](../explanation/state-management.md)).

## Before you start

- Set up the inventory: [Set up an inventory for your clusters](set-up-inventory.md).
  The cluster needs a host entry in `hosts.yml`, in a group under `qumulo_clusters`, with `qumulo_api_host`, and its
  password in `group_vars` or in the
  environment.
  The entry may carry `qumulo_cluster_uuid`, and then the identity guard checks it on every run.
  Without one, the run warns that the identity guard is disabled.
- Have no `host_vars` file for the host.
  Adoption never rewrites one.
- Use a node that belongs to a formed cluster.
  A node that is not part of a cluster has no UUID, and the playbook stops on it.
- For a cluster the Terraform provider formed, set `qumulo_managed_by: terraform_provider` and its
  `qumulo_cluster_kind`, `cnq`, `edge` or `baremetal`, in the host entry first.
  On a CNQ cluster the floating addresses then stay Terraform's, and the capture leaves them out; on an edge or
  bare-metal cluster they are the baseline's, and the capture reads them.
  [Adopt a host from the Terraform inventory plugin](#adopt-a-host-from-the-terraform-inventory-plugin) covers hosts
  that come from the plugin.

## Capture and adopt

1. Run [`capture.yml`](../../playbooks/capture.yml) with `qumulo_adopt=true`, limited to the host:

   ```sh
   ansible-playbook qumulo.cluster.capture -i inventory -l cluster01 -e qumulo_adopt=true
   ```

   Give `-i` one source that defines the host, such as the `inventory` directory, and not two, as in
   `-i inventory -i inventory/hosts.yml`.
   Pass the inventory directory rather than a file in it.
   With `-i inventory/hosts.yml` the capture lands in `inventory/captures/`, where a later `-i inventory` reads it as
   inventory; set `-e qumulo_capture_dir=...` if you must pass a file.

2. Read the two files it wrote:

   | File | Holds |
   |---|---|
   | `captures/cluster01/host_vars.captured.yml` | The whole capture. |
   | `inventory/host_vars/cluster01.yml` | Every captured variable the inventory does not already define. |

   - The capture directory is `captures` in the inventory directory's parent, beside the inventory.
     Set `-e qumulo_capture_dir=/path/to/captures` to put it elsewhere.
   - A variable the inventory defines is left out of the host file.
     The UUID you wrote in `hosts.yml` stays there and is not repeated.
   - An SMB share or NFS export entry that the inventory already declares is left out of the host file too, because
     the baseline refuses an entry declared twice.
     A flat `qumulo_smb_shares` or `qumulo_nfs_exports` in `group_vars` can declare one the cluster holds under a
     tenant, and a `qumulo_factory_defaults` there can remove the factory `Files` share or `/` export that the cluster
     still holds.
     Each such entry becomes a comment at the end of the file that names the inventory's entry, and the inventory's
     entry applies.
     The two entries are compared in the spelling the modules compare them in, so `644` and `0644` are the same mode, for
     example, and only the keys the inventory's entry gives are compared for other values.
     A key the captured entry gives that the inventory's leaves out stays on the cluster.
     When its value is not what a create gives, no entry declares it any more and a rebuild from the inventory would
     not restore it, so the comment names it
     ([the rules](../reference/capture-and-clone.md#declared-again-by-the-inventory)).
   - When the inventory defines `qumulo_tenants`, even as `[]`, the host file cannot hold the captured one, so it writes
     each captured tenant's shares and exports in the flat lists with `tenants` naming the tenant, and leaves a comment
     for each tenant the inventory does not declare.
     A rebuild from the inventory refuses the entries that name such a tenant until `qumulo_tenants` declares it.
     Beside `qumulo_tenants: []`, adoption refuses a network of a tenant, as a single-tenant cluster's networks belong to
     `Default`, unless the inventory lists it for that tenant
     ([the rules](../reference/capture-and-clone.md#when-the-inventory-defines-qumulo_tenants)).
   - Adoption writes no host file, and says why, when the file would hold a network the inventory also declares, a
     share or export the inventory declares with other values, or, beside an inventory's `qumulo_tenants`, a captured
     tenant whose switches or settings the inventory declares with other values or a captured tenant network that the
     inventory does not list for that tenant, or lists with other values
     ([When adoption refuses](#when-adoption-refuses)).
   - Capture never reads the connection variables, so the file holds none.
   - The file ends with comments that name each secret to supply and each value that was read but left out, with the
     reason.
     [What capture and clone read and write](../reference/capture-and-clone.md) lists both.

3. Review the file, then commit it.
   - Supply each secret the comments at the end of the file name, encrypted with `ansible-vault`.
   - If the admin password came from the environment, encrypt it into `host_vars/cluster01.yml` as
     `vault_qumulo_api_password` and point `qumulo_api_password` at it in `group_vars`, as
     [Encrypt a password and refer to it](store-secrets.md#encrypt-a-password-and-refer-to-it) shows.

4. Check that the inventory and the cluster agree:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01 --check --diff
   ```

   Read the output from the top.
   When the inventory removes a share or export the cluster holds, with `qumulo_factory_defaults` or an entry with
   `state: absent`, the check shows the baseline removing it; when it declares one the cluster lacks, the check shows it
   created.
   Adoption left the captured entry out of the file when the inventory removes it, and the capture's removal of the
   factory `Files` share or `/` export out of the file when the inventory declares the one the cluster lacks.
   A refusal here usually names a declaration to add or a consent to give; see
   [Respond to a refusal](respond-to-a-refusal.md).

## Variants

### Capture without adopting

Leave out `-e qumulo_adopt=true`.
The playbook then writes only `captures/cluster01/host_vars.captured.yml`, and you copy into the inventory what you
want from it.

### Adopt a host from the Terraform inventory plugin

A host from the [`qumulo.cluster.terraform`](../../plugins/inventory/terraform.py) inventory plugin is adopted the
same way, for the settings Terraform does not hold.
The plugin defines the host's connection, UUID and ownership variables, among them `qumulo_cluster_uuid`,
`qumulo_managed_by` and `qumulo_terraform_owned_areas`.
A `host_vars` file would override them, so adoption leaves out every variable the plugin defines, and it does not read
the areas that `qumulo_terraform_owned_areas` lists.

1. When the Terraform root also configures the networks, add `network` to the host's `owned_areas` in the
   `qumulo_ansible_inventory` output, and apply the root.
   The capture then leaves the whole network document out, not only the floating addresses.
2. Run the same command with `-l` set to the plugin's host name.

[Use a Terraform root's output as the inventory](terraform.md) sets the plugin up;
[Terraform ownership](../explanation/terraform-ownership.md) explains who owns which area.

### Adopt a cluster with tenants

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

Capture writes `qumulo_tenants` on every cluster that offers the tenant list, one with a single tenant included. The
user the play connects as needs `PRIVILEGE_TENANT_READ`, because the network, SMB, NFS and tenants areas all read the
list, and a capture without it stops with the cluster's refusal. Read the file's `qumulo_tenants` before you commit it:

- Each tenant is an entry by name, with its services, its `networks` (`[]` for none, each with `vlan_id` and `mtu` when
  it is on a VLAN), its `smb_settings` and `nfs_settings`, and its shares and exports under `smb_shares` and
  `nfs_exports`. A tenant without an override has `inherit`, and one with an override has its full record. No tenant id
  is written.
- A share or export that is identical in several tenants is written once in `qumulo_smb_shares` or
  `qumulo_nfs_exports`, with `tenants: all` or the list of its tenants. The factory `Files` share and `/` export stay
  under tenant 1.
- Tenant 1 renamed by hand gets `previous_name: Default`, so that a rebuilt edge's replay renames its own `Default`.
- A backend-bond network, and a network no tenant holds, are in `qumulo_network.frontend_networks` and not under a
  tenant. The comments at the end of the file note each network and VLAN layout that a replay onto a rebuilt cluster
  refuses, and a VLAN that carries a tenant's network and an unbound one also fails the check run;
  [Tenants](../reference/capture-and-clone.md#tenants) gives each case and its fix.
- On a cloud cluster the entry has no service switches, since no cloud cluster supports a tenant write.
- A tenant that uses an identity configuration of its own is written in full, and the comments note its identity, which
  the collection does not manage yet.

Run the check as above, and then see [Manage tenants](tenants.md) to change them. An inventory that uses `tenant_id` on
its flat shares, exports or networks converges, with one warning for each entry, until release 0.3.0 removes the option;
[Update an inventory that names tenant_id](tenants.md#update-an-inventory-that-names-tenant_id) says how to move it.

### Adopt a hub and its peers together

Give each peer a host entry with its `qumulo_cluster_uuid`, and capture them in one run, as with `-l hub01,edge01`.
A portal hub or replication target that no inventory host names is left out, and the addresses only a peer records
(`qumulo_portal_hosts`, `qumulo_replication_address`) are written only when that peer is captured in the same run;
otherwise the file names them as values to supply.
[State management](../explanation/state-management.md#keys-not-ids) explains why a peer is named by inventory host and
never by id.

## When adoption refuses

[Troubleshooting](../reference/troubleshooting.md#adoption) quotes the messages.

| Refusal | Lift it |
|---|---|
| `host_vars` for the host exists beside an `-i` source (`host_vars/cluster01.yml`, `.yaml`, `.json`, or a `host_vars/cluster01/` directory) | Copy what you keep from the capture by hand, or remove the file and adopt again. |
| The host's entry is in two `-i` sources, as with `-i inventory -i inventory/hosts.yml` | Give `-i` only one of them. |
| The inventory contradicts what adoption would write: a network both declare, a share or export the inventory declares with other values, or, when it defines `qumulo_tenants`, a captured tenant it declares with other switches or settings or a captured tenant network it does not list for that tenant, or lists with other values. The message reads "cluster01: the inventory contradicts what adoption would write, so no host_vars file was written: ...", then gives each entry, where the inventory declares it and where the file or the capture does, and for a share, export or tenant the keys that differ | For a network both declare, take it out of the inventory variable the message names. For a tenant network the inventory does not list, or lists otherwise, list it under that tenant in the inventory's `qumulo_tenants` as the capture holds it. When `qumulo_tenants` is `[]` in a group's variables, remove it from them, so that adoption writes the captured one; when the group's list is one other hosts need, give this host its own `qumulo_tenants` in its inventory entry, starting from the one in `captures/cluster01/host_vars.captured.yml`, which replaces the group's for this host. For each share, export or tenant, give the inventory's entry the values `captures/cluster01/host_vars.captured.yml` holds, change it on the cluster to the inventory's values, or take the entry out of the inventory. Then adopt again. No `host_vars` file was written; the capture file was. |
| The node is not part of a cluster | Form the cluster first, then adopt. |

A run that stops with `identity guard: ...; no credentials were sent` before the first two means `qumulo_cluster_uuid`
in the inventory is not the UUID of the cluster at `qumulo_api_host`; see
[The wrong cluster](respond-to-a-refusal.md#the-wrong-cluster).

## Verify

- `captures/cluster01/host_vars.captured.yml` exists with mode 0600 inside a 0700 directory.
- `inventory/host_vars/cluster01.yml` exists, and the baseline's check run from it shows `changed=0` in the recap.
  A license message for a cluster without a license for its UUID is not a change.
  Any step that shows `changed` means the file and the cluster disagree: read the diff and fix the file.
- An unprotected edge that holds no license is captured with `license: none`.
  Any other cluster without a license for its UUID gets a comment that names the license document to supply; see
  [License an edge](licensing.md).
- A cluster with a second tenant has an entry for each, and the check run of the baseline from the file still shows `changed=0`, unless a VLAN carries a tenant's
  network and an unbound one.
- Capture again without `-e qumulo_adopt=true`: `captures/cluster01/host_vars.captured.yml` does not change.

## Next

- [Respond to a refusal](respond-to-a-refusal.md) if the first check run stopped.
- [Troubleshooting: adoption](../reference/troubleshooting.md#adoption).
- [What capture and clone read and write](../reference/capture-and-clone.md) for every variable capture reads, what it never writes and a
  sample adopted file.
- [Rebuilds](../explanation/rebuilds.md): an edge you may rebuild must declare everything the edge holds.
  A fresh adoption does, and the rebuild checks it.
- [Clone an edge's configuration onto a new edge](clone-edge.md) for a new edge that starts from a running one.
- [State management](../explanation/state-management.md#keys-not-ids): why a replication target or portal hub is named
  by inventory host and never by id.
