<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# What capture and clone read and write

Capture reads a running cluster and writes the inventory variables that declare it.
A clone derives those variables for a second, fresh cluster from the first cluster's capture.
This page lists the variables each writes, what each leaves out, the secrets each names instead of reading, and the
shape of the files.

The tasks are in [Adopt a cluster you already run](../how-to/adopt-a-cluster.md) and
[Clone an edge's configuration onto a new edge](../how-to/clone-edge.md).
The reasons are in [State management](../explanation/state-management.md#capture-and-adoption) and
[Rebuilds](../explanation/rebuilds.md).

## Parts

| Part | Kind | Runs on | Does |
|---|---|---|---|
| [capture.yml](../../playbooks/capture.yml) | playbook, hosts `qumulo_clusters` | the controller | Reads each cluster, writes the capture file, and with `qumulo_adopt` the host's `host_vars` file. Changes nothing on a cluster. |
| [capture_info](../../plugins/modules/capture_info.py) | module | the controller, over the REST API | Returns the cluster's configuration as `qumulo_*` variables, with the secrets to supply and what was left out. Changes nothing. |
| [host_vars_text](../../plugins/modules/host_vars_text.py) | module | the controller | Renders variables as the text of a `host_vars` file. Sends no request. |
| [clone_edge.yml](../../playbooks/clone_edge.yml) | playbook, hosts the clone's target | the controller | Reads the source, writes the target's `host_vars` file, and runs the [`cluster_baseline`](../../roles/cluster_baseline/) role on the target. |
| [clone_host_vars](../../plugins/modules/clone_host_vars.py) | module | the controller | Derives the target's variables from a source's capture. Sends no request. |

## Capture

### Capture files

| File | Written | Location | Mode | Holds |
|---|---|---|---|---|
| Capture | every run | `<qumulo_capture_dir>/<host>/host_vars.captured.yml` | directory `<host>` 0700, file 0600 | Every variable the cluster returns, the secrets to supply and what was left out. |
| Host file | with `qumulo_adopt=true` | `host_vars/<host>.yml` beside the inventory source that defines the host | file 0644, a new `host_vars` directory 0755 | Every captured variable the host does not already have, with the same trailing comments. |

- `qumulo_capture_dir` defaults to a `captures` directory beside the inventory source that defines the host: in the
  source's parent when the source is a directory, in the file's directory when it is a file.
  When several sources define the host, the first in sorted order is used.
  A host that comes from no inventory file or directory needs `qumulo_capture_dir`.
- The default capture directory is never inside a directory given to `-i` in that run, because Ansible reads every
  `.yml` and `.json` file in an inventory directory as inventory.
  With a file source such as `-i inventory/hosts.yml` the default is `inventory/captures/`, which a later run with
  `-i inventory` reads as inventory; set `qumulo_capture_dir` in that layout.
- The host file is written once and never rewritten: the write uses `force: false`, and a host that already has
  `host_vars` fails first (see [Adoption refusals](#adoption-refusals)).
- The capture file holds `qumulo_cluster_uuid`.
  The host file does not when the inventory defines it, as `hosts.yml` does.
- Capture uses the connection variables (`qumulo_api_host`, `qumulo_api_password` and the rest) to reach the cluster
  and never writes them, so no file holds one.
- When the host defines `qumulo_cluster_uuid`, the capture fails on a cluster with another UUID (the identity guard).

### Capture playbook variables

| Variable | Default | Meaning |
|---|---|---|
| `qumulo_adopt` | `false` | When true, also write the host file. |
| `qumulo_capture_dir` | see above | The directory that holds one subdirectory per host. |

Capture also reads `qumulo_managed_by` and `qumulo_terraform_owned_areas` to choose what to leave out; see
[Areas Terraform owns](#areas-terraform-owns).
[Inventory variables](variables.md) lists the run-level variables with the others.

### Adoption refusals

A node that belongs to no cluster (state `UNCONFIGURED`) has nothing to capture, and the playbook fails for it with or
without `qumulo_adopt`.
With `qumulo_adopt=true` the playbook also fails for a host when:

- `host_vars/<host>.yml`, `.yaml` or `.json`, or a `host_vars/<host>/` directory, exists beside any `-i` source.
  The capture file is still written.
- the host is in more than one `-i` source, so there is no single `host_vars` directory to write.
- the host is in no `-i` source, even with `qumulo_capture_dir` set: the message is "no inventory file or directory
  defines it".
- the `host_vars` file would hold a network the inventory also declares, or a share or export the inventory declares
  with other values, or the inventory defines `qumulo_tenants` and contradicts a captured tenant. The `host_vars` file is
  not written; the capture file still is. See [Declared again by the inventory](#declared-again-by-the-inventory).

[Troubleshooting: adoption](troubleshooting.md#adoption) quotes the messages.

#### Declared again by the inventory

The baseline refuses an SMB share, NFS export or network that two entries declare.
A variable the file holds outranks the inventory's, so the inventory's `qumulo_tenants`, `qumulo_smb_shares`,
`qumulo_nfs_exports`, `qumulo_factory_defaults` and `qumulo_network` count only where the file leaves them out, and each
stays in force beside the file's variables.
The adoption and the clone compare the file's entries with the inventory's before they write the file.
The [`host_vars_duplicates`](../../plugins/filter/host_vars_duplicates.yml) filter decides each case.

What counts as the same entry:

- An SMB share is the same one when its `share_name` is the same ignoring case, and an NFS export when its
  `export_path` is the same, each within one tenant.
  A network is the same one when its `id` is the same, whichever tenant holds it.
  A network under a tenant declared absent is not counted.
- A flat entry claims the tenants it names in `tenants`, every declared tenant for `tenants: all`, and, when it names
  none, the only present tenant.
  With `tenant_id: 1` it claims tenant 1.
- Tenant 1 is the present entry renamed from `Default`, else the present entry named `Default`.
  The cluster has none when the capture notes it has no tenant 1, as it does when it declares `Default` with
  `state: absent`.
  Then `tenant_id: 1` and a factory removal claim no tenant.
- With `qumulo_tenants` unset or `[]`, the cluster's one tenant is what a flat entry with no tenants, `tenant_id: 1` and
  a factory removal claim.
- A `qumulo_factory_defaults` that sets `smb_files_share` or `nfs_root_export` to `absent` claims tenant 1's factory
  `Files` share or `/` export.

An entry of the file is left out, with a comment at the end of the file that names the inventory's entry, when the
inventory:

- declares the same share or export with the same values.
  The entries are compared in the spelling the modules compare them in
  ([what is compared](#what-is-compared-as-the-same-values)).
  For other values, only the keys the inventory's entry gives are compared.
  What the file's entry gives beyond them depends on who creates the share
  ([a key the inventory's entry leaves out](#a-key-the-inventorys-entry-leaves-out)).
- removes the share or export, with `state: absent` or with `qumulo_factory_defaults`.
  The captured factory `Files` share and `/` export are then left out, and the next baseline removes them from the
  cluster.

A removal in the file's own `qumulo_factory_defaults` of a share or export the inventory declares is left out too, and
`qumulo_factory_defaults` itself is left out when that empties it.
A shared entry of the file, in a flat list, loses only the tenants the inventory declares it for.
When it sets `tenants: all`, it is written as the list of the present tenants that remain, and its comment says so:
"its tenants: all is written as the list of the other tenants, which does not cover a tenant added to the inventory
later".

##### When the inventory defines `qumulo_tenants`

The file cannot hold the captured `qumulo_tenants` then, so adoption judges what each captured tenant holds against the
inventory's, entry by entry, and writes what the inventory does not declare.

The baseline reads the cluster's tenants from the cluster, not from `qumulo_tenants`, so adoption reads them from the
captured tenants:

- Tenant 1 is the cluster's: the captured tenant renamed from `Default`, else the captured `Default`.
  An inventory entry with `previous_name: Default` renames it only when the cluster still holds `Default`, since the
  baseline renames a tenant only from a name the cluster holds.
- The one tenant that a flat entry with no `tenants` names is the one tenant the run holds, which counts a tenant the
  inventory removes, as the baseline counts it until it deletes the tenant.
- A captured tenant that the inventory renames is matched to the inventory's entry by `previous_name`, and written
  under the new name.
  The match applies only when the cluster does not already hold the entry's name, as the baseline renames nothing
  then.
- `tenants: all` means the captured tenants, not the tenants the inventory declares.
  A captured shared entry with `tenants: all` is rewritten as the list of the cluster's tenants, with a comment that
  says why, only when the inventory's present tenants differ from the captured ones after its renames and removals;
  when it declares them, `all` means the same and is left as it is.

What adoption does for each captured tenant:

- Each share and export of the tenant that the inventory does not declare is written in the file's flat
  `qumulo_smb_shares` or `qumulo_nfs_exports` with `tenants: [<tenant>]`.
  When the inventory defines that flat variable too, the file cannot hold it, and a comment says to declare the entry
  under the tenant in the inventory.
- A share or export the inventory declares with the same values is left out with the usual comment.
  One it declares with other values is refused, and the message names the capture's entry after "and the capture in".
- A tenant the inventory does not declare gets a comment: its services and settings stay as the cluster has them.
  For a tenant other than one still named `Default` the comment adds that a rebuild from the inventory refuses the
  entries that name it until `qumulo_tenants` declares it.
  That includes a renamed tenant 1, since a rebuild forms `Default` and nothing renames it, and for it the comment
  says the inventory must declare it with `previous_name: Default`.
- A tenant the inventory declares `state: absent` gets a comment, nothing of it is written, and none of its networks
  are refused.
  The baseline deletes it only once the inventory declares or claims each share and export it holds.
- A tenant whose switches or settings the inventory declares with other values is refused.
  Settings compare as written, by the keys the inventory gives, not in the modules' form.
  A null switch or setting in the inventory is unset, and one the capture does not hold, as on a cloud cluster, is not
  compared.
- A network the tenant holds is refused unless the inventory lists it for that tenant, under the tenant's entry or flat
  in `qumulo_network.frontend_networks` with `tenant:` naming the tenant, with the same values for the keys the
  inventory gives.
  The values compare as written, so a netmask of `24` against `255.255.255.0` differs.
  Under a tenant's entry an omitted `vlan_id` counts as untagged, so the entry refuses a captured network on a VLAN; a
  flat listing takes no `vlan_id`.
  The baseline's network document would otherwise remove or rewrite it.
- The capture's `Default` with `state: absent`, which it writes for a cluster that has no tenant 1, is not written.
  A comment says that a rebuild deletes the `Default` that a new cluster forms, and that the inventory's
  `qumulo_tenants` has to declare it, unless the inventory declares `Default` already.

A network of a tenant the inventory does not declare is refused too, unless the inventory lists it, so beside
`qumulo_tenants: []` adoption refuses a cluster whose frontend networks belong to a tenant, as a single-tenant cluster's
belong to `Default`, unless the inventory lists them.
[When adoption refuses](../how-to/adopt-a-cluster.md#when-adoption-refuses) gives the remedy.

The clone leaves out `qumulo_tenants` when the target's inventory defines it, with a comment, and writes none of the
tenants' shares, exports or networks.
The documentation of the [`host_vars_duplicates`](../../plugins/filter/host_vars_duplicates.yml) filter says how the
filter takes the captured tenants.

##### What is compared as the same values

The `host_vars_duplicates` filter compares two entries in the form the modules accept, but with no directory lookup:
two spellings of one AD identity are a difference.
Its documentation lists each key it compares or skips and how it compares a trustee.

##### A key the inventory's entry leaves out

The file's entry can give a key the inventory's entry omits.
What happens depends on who creates the share or export:

- Adoption: the cluster holds the share or export already and keeps the value of each key an entry omits.
  The entry is still left out.
  When such a key holds a value that a create does not give, its comment names the key and says the cluster keeps it.
- Clone: the target is fresh, so the baseline creates each share or export from the inventory's entry alone, and each
  key it omits takes the create default.
  A key whose value in the source differs from that default refuses, as "with other values of `<keys>`, which the
  inventory's entry leaves out".
  A key equal to its default does not.

A key set to null counts as left out.

The `host_vars_duplicates` documentation lists the create defaults, as observed on Qumulo Core 7.10.3.
A share's `permissions`, an export's `restrictions` and `fs_path` have no create default, so a clone refuses an
inventory entry that leaves out the one the source's entry gives.

##### What the adoption and the clone refuse

The adoption and the clone refuse, before they write the `host_vars` file, when the file would hold:

- a network the inventory also declares;
- a share or export the inventory declares with other values, which the message lists as the keys that differ;
- for the clone only, a share or export whose entry gives a key the inventory's entry leaves out, with a value other
  than a create gives;
- for the adoption only, beside an inventory that defines `qumulo_tenants`, a captured tenant whose switches or settings
  the inventory declares with other values, and a captured tenant network the inventory does not list for that tenant, or
  lists with other values
  ([when the inventory defines `qumulo_tenants`](#when-the-inventory-defines-qumulo_tenants)).

The adoption has written `captures/<host>/host_vars.captured.yml` by then, and the clone writes nothing.
Each refusal entry reads as "the SMB share Projects of tenant Default, which the inventory declares in
qumulo_smb_shares[0] and the file in qumulo_tenants[Default].smb_shares[1] with other values of fs_path".
The messages begin as [Troubleshooting](troubleshooting.md#adoption) quotes them.

Two declarations both in the inventory, or both in the file, are not compared.
A claim the baseline refuses for a reason of its own, such as an entry with no name, or with no tenant on a cluster of
several, is left to the baseline, and so is a `tenant_id` other than 1, which only the cluster can resolve.

### Areas and the variables they write

An area is a choice of `gather_subset` and of `leave_out` in `capture_info`.
The names are those of the `cluster_baseline` role's areas, plus `replication`.
`capture.yml` reads every area.
`capture_info` reads the areas `gather_subset` names, `all` by default.
Each variable has the shape the role passes to the module that writes it, so a check run of the baseline from the file
changes nothing.
Lists are sorted and values canonical, so a second capture of an unchanged cluster writes the same file.

`qumulo_cluster_uuid` is always written.

| Area | Variables written | Holds | Reads back | Written when |
|---|---|---|---|---|
| `cluster` | `qumulo_cluster_name` | The cluster's name | `cluster_settings` | always |
| `filesystem` | `qumulo_filesystem_settings` | The permissions mode, access time updates and change notification | `filesystem_settings` | always. A permissions mode that `filesystem_settings` does not offer is left out. |
| `web_ui` | `qumulo_web_ui_settings` | The login banner and inactivity timeout | `web_ui_settings` | always. An inactivity timeout that is not whole minutes is left out. |
| `network` | `qumulo_network` (`frontend_networks`, `managed_interfaces`) | The network document, with its floating IP ranges unless Terraform owns them. A tenant's networks are written under the tenant ([Tenants](#tenants)). | `network_config` | always |
| `dns` | `qumulo_dns` (with `lookup_overrides`), `qumulo_authoritative_dns` | DNS servers, search domains and lookup overrides; the authoritative DNS settings | `dns_settings`, `dns_lookup_overrides`, `authoritative_dns` | `qumulo_authoritative_dns` only when the cluster offers the authoritative server. A lookup-override address that appears in more than one entry is captured as one entry, with the names in table order. |
| `time` | `qumulo_time` | The time source, the NTP servers and whether AD is the primary source | `time_settings` | always |
| `ssl` | `qumulo_ssl_ciphers` | The TLS cipher suites the cluster accepts | `ssl_ciphers`, `ssl_certificate` | the ciphers when the cluster offers them. The certificate and its key are named under the secrets to supply. `qumulo_ssl` is never written. |
| `monitoring` | `qumulo_monitoring`, with the Nexus connection under `nexus` | Cloud monitoring and the Nexus connection | `monitoring_settings`, `nexus_connection`, `nexus_registration` | always. `nexus` only when the cluster offers it. |
| `audit` | `qumulo_audit_destinations` | The audit log destinations | `audit_destination` | when the cluster offers them. Sorted by name. |
| `capacity` | `qumulo_capacity`: `clamp`, `free_threshold`, `license` | The capacity clamp when one is set, the eviction threshold, and the license marker | `capacity_clamp`, `portal_eviction_settings`, `license` | only when at least one key is set. `clamp` when a clamp is set. `free_threshold` only when the cluster offers portal eviction settings. `license: required` when an active license is bound to the cluster's UUID, `license: none` on an unprotected edge that holds none. |
| `local_groups` | `qumulo_local_groups` | The local groups and their members | `local_group` | always |
| `local_users` | `qumulo_local_users`, without passwords | The local users, without passwords | `local_user` | always |
| `ad` | `qumulo_ad`, without the join credentials | The Active Directory membership | `ad_domain` | only when the cluster is joined |
| `kerberos` | `qumulo_kerberos`: `use_alt_security_identities_mapping` | Whether a Kerberos principal maps to an AD user through the user's `altSecurityIdentities` attribute | `kerberos_settings` | always. The keytab is named under the secrets to supply when the cluster holds one, or noted in `left_out` when the user may not read it, and no key is ever written. |
| `saml` | `qumulo_saml`, the identity provider's certificate included | The SAML sign-in settings | `saml_settings` | only once SAML is set up |
| `identity_mappings` | `qumulo_user_defined_mappings`, sorted by `down_level_logon_name` | The mappings from AD users to LDAP users | `user_defined_mappings` | only when there are mappings. `qumulo_identity_attributes` is never written. |
| `roles` | `qumulo_roles`, `qumulo_role_members` | The custom roles and their privileges; the members of each role | `role`, `role_member` | `qumulo_roles` only when custom roles exist; `qumulo_role_members` always |
| `portal` | `qumulo_portal`: `hub`, `type`, `roots` | The spoke portal, its hub named by inventory host, and its roots | `portal_spoke`, `portal_hub` | for one ACCEPTED spoke portal whose hub is in the inventory (see [Left out](#what-capture-leaves-out)) |
| `directories` | `qumulo_directories`, `qumulo_file_acls` | Each directory that something names, and the ACLs the mode cannot express | `directory`, `file_acl` | always. See [Directories](#directories). |
| `smb` | `qumulo_smb_settings`, `qumulo_smb_shares`, `qumulo_factory_defaults.smb_files_share` | SMB settings and the shares that several tenants share; whether the factory `Files` share exists. A tenant's shares and its SMB override are written under the tenant ([Tenants](#tenants)). | `smb_settings`, `smb_share` | always. The factory key is `present` when tenant 1 holds a share named `Files`, else `absent`. |
| `nfs` | `qumulo_nfs_settings`, `qumulo_nfs_exports`, `qumulo_factory_defaults.nfs_root_export` | NFS settings and the exports that several tenants share; whether the factory export at `/` exists. A tenant's exports and its NFS override are written under the tenant ([Tenants](#tenants)). | `nfs_settings`, `nfs_export` | always. The factory key is `present` when tenant 1 holds an export at `/`, else `absent`. |
| `tenants` | `qumulo_tenants` | Each tenant's name, services, networks, SMB and NFS overrides, shares and exports | `tenant`, `network_config`, `smb_settings`, `nfs_settings`, `smb_share`, `nfs_export` | when the cluster offers the tenant list, on every such cluster, one with a single tenant included. See [Tenants](#tenants). |
| `s3` | `qumulo_s3_settings`, `qumulo_s3_buckets`, `qumulo_s3_bucket_policies`, `qumulo_s3_access_keys` | The S3 server settings, the buckets, each bucket's policy, and each access key by its user and id | `s3_settings`, `s3_bucket`, `s3_bucket_policy`, `s3_access_key` | always. See [S3](#s3). |
| `ftp` | `qumulo_ftp_settings` | The FTP server settings, with every option | `ftp_settings` | when the cluster offers the FTP settings. `anonymous_user` is left out, with a note, when it names a deleted local user or anything but a local user. `greeting` is written as read, the factory text included. |
| `quotas` | `qumulo_quotas`: `path`, `limit` | Directory quotas | `directory_quota` | always |
| `snapshots` | `qumulo_snapshot_policies` | Snapshot policies, each with its `lock_key` by the key's name; `left_out` notes each key of the file system key store by name and id | `snapshot_policy` | always |
| `replication` | `qumulo_replication_relationships` | The relationships this cluster is the source of, each target named by inventory host | `replication_relationship`, `replication_target` | only for relationships this cluster is the source of that are not ENDED and whose target is in the inventory |

An area that the cluster does not offer is skipped, listed in `unavailable_subsets` and noted under what was left out.

`qumulo_replication_relationships` entries hold `source_path`, `target` (an inventory host), `target_path`,
`replication_enabled`, `map_local_ids_to_nfs_ids`, `source_root_read_only`, `blackout_windows`, and
`blackout_window_timezone` once one is set.
`qumulo_portal.roots` entries hold `spoke_root` and `hub_root`.

#### Directories

`qumulo_directories` holds each directory that an SMB share, an NFS export, a quota, a snapshot policy, an S3 bucket,
the S3 base path (unless it is `/`) or a replication target names, and each directory above one, the root included.
Each entry has the options `directory` takes.

- A replication target directory is declared by its path alone, and no directory inside it is written: replication sets
  its attributes and its content's.
- A directory carries its ACL in `qumulo_file_acls` in place of its `mode` when the mode cannot express the ACL
  ([G-FS-ACL-MODE](gaps.md)).
  The entry in `qumulo_directories` then has no `mode`.
- A directory in a spoke root, and a directory that a share, export, quota or policy names but that does not exist,
  is left out.

#### Tenants

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

The `tenants` area runs after `network`, `smb` and `nfs`, which hand it what each tenant holds. It writes
`qumulo_tenants` on every cluster that offers the tenant list, by tenant name and never by id, sorted by name. A cluster
with one tenant has one entry. No tenant id is written anywhere in a capture. Each entry holds:

- `name`, `previous_name: Default` when tenant 1 has another name, so that a replay onto a newly formed cluster renames
  its `Default`, and the six switches. A cluster without tenant 1 is noted, and what is written depends on its networking:
  - On networking the host manages (no managed interfaces, only HOST networks), the one tenant that holds a network gets
    `previous_name: Default` and no `Default` entry is written, when every network has a tenant, no tenant is named
    `Default`, the `network`, `smb` and `nfs` areas are read, and that tenant has no `/` export that the capture leaves
    out ([G-MAPTO](gaps.md)). A rebuild from the capture then renames the `Default` the new edge forms with to that
    tenant, which keeps the HOST network in tenant 1 through the rebuild. `smb_files_share` in
    `qumulo_factory_defaults` is `present` instead of `absent` when the capture writes a share named `Files` (any case)
    for that tenant, and `nfs_root_export` when it writes that tenant's export at `/`. A clone keeps the
    `previous_name`, so a fresh target's `Default` is renamed to that tenant.
  - Otherwise, a `Default` entry with `state: absent`, which has a rebuild delete the `Default` the new edge forms with.
    A cluster whose one tenant that holds a network has a `/` export that the capture leaves out (G-MAPTO) gets this
    form too; [Rebuilds](../explanation/rebuilds.md#the-preflight) says why.
    No entry is written when another tenant is named `Default`, or when a replay of what the capture wrote could not
    delete `Default`.
  - The `tenant 1` note in `left_out` says which of these the capture did, which factory keys it set, and, when the rename's other
    conditions hold but two or more tenants hold networks or that tenant has a `/` export the capture leaves out, why it
    wrote `Default` absent instead; in the first case it adds that a rebuild needs `qumulo_management_network`, which the
    capture does not write.
    [Rebuilds](../explanation/rebuilds.md) says why a rebuild keeps the HOST network in tenant 1.
- `networks`, the tenant's frontend networks without a tenant id, `[]` for a tenant with none, when the `network` area
  is read. A network on a frontend VLAN has `vlan_id`, and `mtu` when the VLAN sets one. `managed_interfaces` is written
  without the VLANs that carry only tenant networks, and the frontend bond's network list without tenant networks;
- `smb_settings` and `nfs_settings`, when the `smb` or `nfs` area is read: `inherit` for a tenant with no override, and
  the override's full record for one with it, an override equal to the global settings included;
- `smb_shares` and `nfs_exports`, each left out when empty. The factory `Files` share and `/` export stay under tenant 1.
  `qumulo_factory_defaults` is still written, `present` when tenant 1 holds the factory share or export.

Three things go elsewhere:

- A share or export identical in two or more tenants becomes one entry of `qumulo_smb_shares` or `qumulo_nfs_exports`.
  Copies compare as the module's input without the tenant or the id, SMB names without regard to case, and each name's
  copies group by identical content. A group of every tenant on the cluster is written with `tenants: all`, and any
  other group with its tenants as a list sorted by name, and the name spelled as its first tenant spells it. Shared
  entries are sorted by name, then by their first tenant. Every other copy goes under its tenant, and the factory share
  and export never merge.
- A network of the backend bond, or that no tenant holds, goes in `qumulo_network.frontend_networks`. Capture notes a
  backend network a tenant holds on a cluster with two or more tenants, which a replay onto a rebuilt cluster refuses
  while a tenant holds it, and an unbound network on such a cluster. A rebuild whose replay has several tenants
  refuses, in its preflight, an unbound network that the newly formed edge lacks (on managed networking, any network
  but 1; on host-managed networking, none, since the replay's tenant 1 pass writes every network sent with no tenant
  first), and otherwise puts it in tenant 1, which the preflight reports as drift;
  [A network sent with no tenant](../explanation/rebuilds.md#a-network-sent-with-no-tenant) says when.
- On a cloud cluster, which capture knows from the protection type, the six switches are left out of every entry and
  noted once, since no cloud cluster supports a tenant write.

Capture also notes a VLAN that carries the networks of two tenants, or a tenant's network and one no tenant holds.
For two tenants on one VLAN, the baseline warns about the layout on the cluster that was captured and goes on, and
refuses it on a cluster that does not hold it yet, as a rebuild. A VLAN that carries a tenant's network and an unbound
one stays in `managed_interfaces`, beside the `vlan_id` on the tenant's network, so a check run from a capture with the
`tenants` area refuses it as a VLAN declared in two places; without the area, the baseline keeps it on the captured
cluster. A rebuild whose replay has several tenants refuses the unbound network in its preflight when the newly formed
edge lacks it (on managed networking, any network but 1), and otherwise puts it in tenant 1
([why](../explanation/rebuilds.md#a-network-sent-with-no-tenant)). A tenant on an identity
configuration other than tenant 1's is written in full, and only its identity is noted, as per-tenant identity is not
supported yet. Capture never writes `identity`.

The `network`, `smb`, `nfs` and `tenants` areas read the tenant list, and the `smb` and `nfs` areas read each tenant's
override, so the user the play connects as needs `PRIVILEGE_TENANT_READ`. A refusal of the list with HTTP 403 fails the
capture, as a 403 does on every read but the key store's and the keytab's. A cluster that does not offer the list, which
a release without the multitenancy API does not, lists `tenants` in `unavailable_subsets` and notes it, and the other
areas write the flat forms below.

The `kerberos` area reads the keytab, and the cluster returns every key of it to the user that reads it, which needs
`PRIVILEGE_KERBEROS_KEYTAB_READ`. When the user the play connects as lacks it, as the built-in Observers role does
(Qumulo Core 7.11.0), the area still writes `qumulo_kerberos`, leaves out the keytab's supply line, and says so in one
entry under `left_out`. Capture writes and returns no key.

`leave_out` and `gather_subset` change the placement:

| Left out | Effect |
|---|---|
| `network` | No tenant entry has `networks`. |
| `network.floating_ips` | The floating ranges of every tenant network are dropped. |
| `smb` | Every share and every SMB override, nested or shared, is dropped. |
| `nfs` | Every export and every NFS override, nested or shared, is dropped. |
| `tenants` | `qumulo_tenants` is not written, and every network is written in `qumulo_network.frontend_networks`, with its VLAN in `managed_interfaces`. On a cluster with one tenant the networks carry no tenant, the shares and exports are written flat with no tenant, and the tenant's overrides, which have no flat form, are noted. On a cluster with two or more, each network a tenant holds, except a backend-bond network, carries its `tenant` by name, and every tenant-scoped share, export and override is dropped, with a note. |

A `gather_subset` that names `network`, `smb` or `nfs` without `tenants` places them as `leave_out: tenants` does, and a
cluster that does not offer the tenant list does the same. Every case is noted under what was left out, and a check run
of the baseline from the file changes nothing. A baseline run that takes `tenant` on a flat network from a capture of a
multi-tenant cluster refuses it with the stale name when another owner of `tenants` has renamed the tenant since.

#### S3

- `qumulo_s3_settings` holds the base path, and `qumulo_directories` holds the base path's directory.
- `qumulo_s3_buckets` holds the buckets outside the spoke roots, and `qumulo_directories` their directories.
- `qumulo_s3_bucket_policies` holds one entry per kept bucket that has a policy, with each principal in the trustee
  grammar the inventory uses.
  A policy whose principals cannot be converted is left out with a note.
- `qumulo_s3_access_keys` holds each key by its `user` and `access_key_id`.
  The cluster returns a key's secret only to the request that makes the key, so each key is named under the secrets to
  supply ([G-S3-KEY](gaps.md)).
  After a rebuild the baseline makes a new key and writes its secret under the host's artifact directory.

[Serve a directory as an S3 bucket](../how-to/s3.md) has the steps.

### Areas Terraform owns

The playbook reads `cluster_baseline_owned_areas` from the
[role's vars](../../roles/cluster_baseline/vars/main.yml) and passes it as `leave_out`.
That list is `qumulo_terraform_owned_areas`, which defaults to `[network.floating_ips]` when `qumulo_managed_by` is
`terraform_provider`, unless `qumulo_cluster_kind` is `edge` or `baremetal`, and to nothing otherwise.

| `leave_out` entry | Effect |
|---|---|
| any area name | The area is not read. The file notes it under what was left out. |
| `network.floating_ips` | `qumulo_network` is written without its floating IP ranges. |
| `dns.fqdn` | `qumulo_authoritative_dns` is written without `enabled` and `fqdn`. |
| `audit.cloudwatch` | `qumulo_audit_destinations` is written without the CloudWatch destinations. |

A tenant's keys follow the areas they are in; the table in [Tenants](#tenants) gives the effect of each.

Any other name fails `capture_info` before a request is sent.
An area that is left out raises none of its secrets, so a left-out `ssl` area names no certificate to supply.

With `qumulo_adopt=true`, the host file gets `qumulo_managed_by: none` and `qumulo_terraform_owned_areas: []` when the
inventory defines neither variable, and neither otherwise.
A hand-written `qumulo_managed_by: terraform_provider` therefore keeps the floating addresses Terraform's, unless the
host's `qumulo_cluster_kind` is `edge` or `baremetal`.
The adoption of a host from the [Terraform inventory plugin](terraform-inventory.md) leaves out every variable the
plugin defines, because a `host_vars` file outranks the plugin's variables.
[Terraform ownership](../explanation/terraform-ownership.md) gives the reasons.

### What capture leaves out

Each entry below, except directories inside a replication target, is listed with its reason in the comments at the end
of the file, under "Read from the cluster but left out".

| Left out | When |
|---|---|
| An area | The area is in `leave_out`, or the cluster does not offer it. |
| A directory, quota, snapshot policy or S3 bucket, and the policy of a left-out bucket | It lies in a spoke root. The data there is the hub's, and the hub's capture declares it. |
| A directory inside a replication target directory | The target directory is declared by its path alone, and replication sets what is inside it. No comment is written for it. |
| A spoke portal | It is not ACCEPTED; or another portal is already declared (`qumulo_portal` declares one); or no inventory host has the hub's `qumulo_cluster_uuid`; or a root's directory no longer exists; or the hub has not reported every root's path. |
| A replication relationship | It has ENDED; or no inventory host has the target's `qumulo_cluster_uuid`; or its blackout windows have no timezone ([QUIRK-019](quirks.md)), which the replication roles refuse. |
| The target side of a replication | The source's UUID is in no inventory host. The source host declares the relationship. |
| An NFS export | A restriction maps to a user or group that no longer exists ([G-MAPTO](gaps.md)). |
| A tenant's identity, and a network layout the baseline refuses | The tenant uses an identity configuration other than tenant 1's, or a VLAN carries two tenants' networks, or a backend network or an unbound network sits on a multi-tenant cluster. The entry is still written. See [Tenants](#tenants). |
| An S3 bucket policy | Its principals cannot be converted to trustees. |
| Each key of the file system key store | Always. The cluster registers a key only with its name signed by its private key, and keeps neither. A key is named by name and id. When the API user may not read the key store, one entry says so instead. |
| The `lock_key` of a snapshot policy | Only when the API user may not read the key store, or the cluster stored the key with no name (QUIRK-120). Capture otherwise writes it by the key's name. Without a name it cannot name the key, and an id here can name another key elsewhere, so the entry has no `lock_key` and a note names the key's id and says to declare the key by its name, or, when it has no name, by its id on the cluster the inventory is applied to. |
| The configured domain controllers | The cluster does not read them back for the joined domain ([G-AD-DCS](gaps.md)). |
| The permissions mode, the Web UI inactivity timeout | The cluster uses a value the module does not take: a mode `filesystem_settings` does not offer, or a timeout that is not whole minutes. |
| The authoritative DNS settings, the Nexus connection, the audit destinations, the TLS cipher suites, the eviction threshold | The cluster does not offer the feature. |
| The floating IP ranges, the authoritative DNS name and whether it answers, a CloudWatch audit destination | `leave_out` names `network.floating_ips`, `dns.fqdn` or `audit.cloudwatch` (see [Areas Terraform owns](#areas-terraform-owns)). |
| The separate lookup overrides of an address | The cluster lists the address in more than one entry. They are captured as one, with the names in table order. |

Shares, exports, quotas, snapshot policies and buckets on a path inside a replication target are still captured; a clone
drops them (see [What the clone does with each variable](#what-the-clone-does-with-each-variable)).

A portal's hub and a replication target are named by inventory host.
`capture_info` finds the host from `inventory_clusters`, a map of cluster UUID to host, which the playbook builds from
every `qumulo_clusters` host that defines `qumulo_cluster_uuid`.

### Secrets to supply

Capture writes no secret.
It reads one, the keytab's keys, only to name the principals (see the `kerberos` area above), and never writes them.
It names each one that the inventory must supply to manage what the secret guards, in comments at the end of the
file, grouped by reason and sorted.

| Variable | Named when |
|---|---|
| `qumulo_local_users[<name>].password` | for each local user except `guest` |
| `qumulo_ad.join_user`, `qumulo_ad.join_password` | the cluster is joined to a domain |
| `qumulo_kerberos.keytab` | the cluster holds a keytab. The reason names its principals, never a key. Give the keytab file in base64, encrypted ([Encrypt a binary secret such as a keytab](../how-to/store-secrets.md#encrypt-a-binary-secret-such-as-a-keytab)). |
| `qumulo_ssl.certificate`, `qumulo_ssl.private_key` | the `ssl` area is read; the cluster returns neither ([G-SSL-READBACK](gaps.md)). The reason gives the SHA-256 of the certificate the cluster serves. |
| `qumulo_monitoring.nexus_join_key` | the Nexus connection is on and the registration is not `REGISTERED`; a join key is single-use and never read back ([G-NEXUS-KEY](gaps.md)) |
| `qumulo_capacity.license_document` | the cluster holds no active license bound to its UUID, and is not an unprotected edge without one ([G-LICENSE](gaps.md)) |
| `qumulo_s3_access_keys[<access_key_id>]` | for each S3 access key (G-S3-KEY) |
| `qumulo_portal_hosts`, `qumulo_replication_address` | the cluster is in a portal or is a replication target, and the value cannot be taken from its peers (see [Values only a peer records](#values-only-a-peer-records)) |

[Keep secrets out of the inventory](../how-to/store-secrets.md) shows how to encrypt each.

### Values only a peer records

A spoke records the hub's addresses but not its own, a hub records the spoke's but not its own, and a replication
source records the address it reaches the target at ([G-PORTAL-HOSTS](gaps.md)).
`capture_info` therefore returns, for each peer, what this cluster records of the peer's own variable in
`capture.peer_values`.
`capture.from_peers` lists the variables of this cluster that only its peers record.

`capture.yml` writes such a variable on a host when all of these hold:

- the peers' records of it were captured in the same run (the `-l` limit includes the peers);
- at least one record exists, and none is unusable;
- every record gives the same value.

A record is unusable when its portal is not ACCEPTED and ACTIVE, or its replication is not ESTABLISHED.
A portal being deleted, and a replication relationship that has ENDED, is ignored.

| Variable | Written as |
|---|---|
| `qumulo_portal_hosts` | the list of `address` and `port` the peers record |
| `qumulo_replication_address` | the `address` the sources record |
| `qumulo_replication_port` | the `port`, written only when it is not 3712 |

Otherwise the variable is named under the secrets to supply, with the reason: no peer was captured, a record is
unusable, or the peers record different values.

### What capture never writes

| Item | Why |
|---|---|
| `qumulo_identity_attributes` | The cluster lists no identity that has attributes, so none can be found ([G-ID-ATTRIBUTES](gaps.md)). |
| `qumulo_ssl` | The cluster returns neither the certificate nor its key. |
| Connection variables | Capture reads the cluster through them and does not write them. |
| The edge rebuild's records | They are state the rebuild keeps, not configuration. |

One-shot actions are not configuration and leave nothing to read.
`capture_info` has a reason for each module it does not capture:

| Module | Why it is not captured |
|---|---|
| `auth_cache_clear`, `dns_cache_clear` | They clear a cache. |
| `encryption_key_rotation` | It rotates the at-rest encryption key and leaves no setting. |
| `identity_attributes` | See above. |
| `nexus_cluster` | It retires a cluster in Nexus. |
| `replication_restore_lock` | It holds a restore's lock, a file on the controller. |
| `replication_reverse` | It sends a failover verb to one relationship. The records it leaves are what capture reads, as `replication`. |
| `security_key` | The cluster registers a key only with its name signed by its private key, which it does not return. Capture lists each key in `left_out` by name and id. |
| `snapshot`, `snapshot_lock` | A snapshot is taken by a one-shot action. Capture declares the policies that take snapshots. |
| `unprotected_edge_create` | It forms the cluster, which is what capture reads. |
| `upgrade` | Its outcome is the running release, which `cluster_info` reads. |

### Shape of the files

`host_vars_text` renders both files.

- The file starts with the header as comments, then the variables sorted by name, then the secrets to supply, then what
  was left out.
- Every comment line is wrapped at 112 characters of text and starts with `# `.
- Every string value is tagged `!unsafe`, so Ansible reads it as written and never templates it.
  A share description with a Jinja expression in it therefore does not run on the controller.
- Mapping keys, mappings and sequences carry no tag.
- A string that would read as another type without its tag, such as `0777` or `true`, is written quoted without the
  tag.
  The empty string is written as `''` without the tag.
- A template you add to the file yourself, such as a reference to a vaulted secret, is evaluated as usual.
- Each secret to supply is a comment: `# <variables>:` and then the reason, indented.
  Lines with the same reason share one heading.
- Each item left out is a comment: `# <what>:` and then the reason, indented.

The host file's header is the first three lines of the sample.
The capture file's header reads "Captured from `<host>` by playbooks/capture.yml" and says that it is a one-time
import.

```yaml
# inventory/host_vars/cluster01.yml, in part
# Adopted from cluster01 by playbooks/capture.yml: Qumulo Core 7.10.3,
# cluster cbb6f13a-d92f-4dfb-a7f0-d0a6785da65d. Variables the inventory already defined are left out.
# Review it, check it with playbooks/baseline.yml --check, and commit it.
qumulo_authoritative_dns:
  enabled: false
  fqdn: ''
  host_restrictions: []
  record_ttl_seconds: 0
qumulo_cluster_name: !unsafe cluster01
qumulo_dns:
  lookup_overrides: []
  search_domains:
  - !unsafe corp.example.com
  servers:
  - !unsafe 10.0.0.2
qumulo_factory_defaults:
  nfs_root_export: !unsafe present
  smb_files_share: !unsafe present
qumulo_local_users:
- name: !unsafe admin
  primary_group: !unsafe Users
  uid: 0
- name: !unsafe guest
  primary_group: !unsafe Guests
  uid: 65534
qumulo_managed_by: !unsafe none
qumulo_tenants:
- name: !unsafe Default
  networks: []
  nfs_enabled: true
  nfs_exports:
  - export_path: !unsafe /
    fs_path: !unsafe /
    # ...and the export's other options
  nfs_settings: !unsafe inherit
  rest_api_enabled: true
  replication_enabled: true
  smb_enabled: true
  smb_settings: !unsafe inherit
  smb_shares:
  - share_name: !unsafe Files
    fs_path: !unsafe /
    # ...and the share's other options
  ssh_enabled: true
  web_ui_enabled: true
qumulo_terraform_owned_areas: []
qumulo_time:
  ntp_servers:
  - !unsafe ntp1.corp.example.com
  source: !unsafe API_TIME_SOURCE_NTP
  use_ad_for_primary: false

# Never captured. Supply each of these to manage what it guards, a secret encrypted with ansible-vault:
#
# qumulo_local_users[admin].password:
#   the cluster never returns a password; give the user's password, encrypted with ansible-vault, to set it when the
#   user is created, or on every run with update_password: always
```

The sample is of a cluster that has no frontend network yet, so `Default` has `networks: []`; on a cluster with one, the
network is written under the tenant that holds it. The factory `Files` share and `/` export are under tenant 1.

A capture is a one-time import.
The inventory is the source of truth afterwards, and nothing replays a capture.
[State management](../explanation/state-management.md#capture-and-adoption) gives the reason.

## Clone

A clone gives a fresh cluster (the target) the configuration of another cluster (the source).
It reads the source, changes nothing on it, writes the target's `host_vars` file, and runs the `cluster_baseline` role
on the target.
It copies configuration only, never data.
The target is a second cluster with a name of its own, and the clone never claims to be the source.

### Clone playbook variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `clone_edge_source` | yes | | The source: an inventory host, or the name of a source outside the inventory, which then only names it in messages and in the file's header. |
| `clone_edge_target` | yes | | The target: an inventory host in `qumulo_clusters`, other than the source. |
| `clone_edge_name` | yes | | The target's name. It is also the target's computer account in the domain, so it must differ from the source's name and from the `qumulo_cluster_name` of every other inventory host that defines one. Names compare without regard to case. |
| `clone_edge_supply` | no | `{}` | The secrets for the first pass: a map from the name of a supply line to the secret. The values are used for that run only and are never written. |
| `clone_edge_check_only` | no | `false` | Write the file, then run the baseline in check mode. Needs the same secrets. |
| `clone_edge_allow_configured_target` | no | `false` | Clone onto a target that holds configuration formation does not create. True in effect when this clone's own file exists. |
| `clone_edge_host_vars_dir` | no | `host_vars/` beside the one inventory source that defines the target | The absolute directory to write `<target>.yml` in. A relative path is refused. |
| `clone_edge_source_api_host` | for a source outside the inventory | | The source's address. |
| `clone_edge_source_api_port` | no | `8000` | |
| `clone_edge_source_api_username` | no | `admin` | |
| `clone_edge_source_api_password` | no | empty | |
| `clone_edge_source_api_token` | no | empty | |
| `clone_edge_source_validate_certs` | no | `true` | |
| `clone_edge_source_ca_path` | no | unset | |
| `clone_edge_source_cluster_uuid` | no | empty | The UUID the source must have, for the identity guard. |

A key of `clone_edge_supply` is `<variable>.<key>`, such as `qumulo_ad.join_user`, or
`qumulo_local_users[<name>].password`.
A key that no supply line of the file names fails the clone.
Ansible cannot tell a variable given with `-e` from one the inventory defines.
A captured variable given with `-e` counts as one the inventory defines, and the clone leaves it out of the file.

[Inventory variables](variables.md) lists the run-level variables with the others.

### Clone files

| File | Location | Mode |
|---|---|---|
| The target's `host_vars` | `clone_edge_host_vars_dir/<target>.yml` | 0644; the directory is created if missing and set to 0755 on every run |

- The clone writes only this file.
  A target that has `host_vars` in another file or directory beside any `-i` source fails.
- When the file exists and the clone would write different text, the clone fails and leaves it as it was.
  The text is the same on a second run with an unchanged source, so a repeat run succeeds.
- The file is written before the check for missing secrets, so a missing secret leaves the file in place.
- The file starts with a header that names the source, its UUID and the target's UUID, and ends with the same comments
  as a capture: the secrets to supply, then what was left out.
  [Shape of the files](#shape-of-the-files) gives the format rules.

```yaml
# Cloned from edge01, cluster cbb6f13a-d92f-4dfb-a7f0-d0a6785da65d, by playbooks/clone_edge.yml
# onto cluster 1b2c33f0-9a8b-4c56-9d2e-3f8c1b0a7e22. Configuration only: no data is copied.
# Variables the inventory already defines are left out. A second clone writes the same file, and refuses to
# replace it once it differs.
qumulo_cluster_name: !unsafe edge02
qumulo_cluster_uuid: !unsafe 1b2c33f0-9a8b-4c56-9d2e-3f8c1b0a7e22
qumulo_dns:
  lookup_overrides: []
  search_domains:
  - !unsafe corp.example.com
  servers:
  - !unsafe 10.20.0.2
# ...

# Never captured. Supply each of these to manage what it guards, a secret encrypted with ansible-vault:
#
# qumulo_monitoring.nexus_join_key:
#   a join key registers one cluster only, and the source's registration is the source's; give a new key for the
#   clone, or qumulo_nexus_api_token to mint one (G-NEXUS-KEY)
# ...

# Read from the cluster but left out:
#
# the network document:
#   it holds the source's own addresses, its floating IPs among them; Terraform or you declare the clone's network
# ...
```

### What the clone does with each variable

Every variable a capture can hold has a rule in `clone_host_vars`.
A variable without a rule fails the clone, so the clone copies nothing it has no rule for.
The clone reads the source with the same `capture_info` call as a capture, with the areas the target's inventory gives
to Terraform left out.

| Variable | Rule | What the clone does |
|---|---|---|
| `qumulo_cluster_uuid` | replace | The target's own UUID, as the target reports it. Never the source's. |
| `qumulo_cluster_name` | replace | `clone_edge_name`. |
| `qumulo_ad` | copy | Copied without the join credentials. The target joins as a new computer account, `<NAME>$` for the clone's name, so the join needs `qumulo_ad.join_user` and `qumulo_ad.join_password`. |
| `qumulo_kerberos` | copy | Copied without `keytab` and `keytab_path`. The keytab holds the source's principals, for the source's names, so the target needs a keytab exported from the KDC for the principals of its own names. The capture's note that the source's keytab could not be read is restated for the target in the same terms. |
| `qumulo_monitoring` | copy | Copied. A join key registers one cluster only, so the target needs a new key, or `qumulo_nexus_api_token` to mint one. |
| `qumulo_capacity` | copy | Copied without `license_document`. A license is bound to one UUID, so the target needs a license issued for its own. |
| `qumulo_authoritative_dns` | copy | Copied without `enabled` and `fqdn`, with a note when the source had a name or answered. |
| `qumulo_audit_destinations` | filter | Copied without the CloudWatch destinations, whose log group and region are in the source's AWS account. |
| `qumulo_ftp_settings` | copy | Copied. The anonymous user is a local user, which `qumulo_local_users` carries to the target. |
| `qumulo_s3_settings` | copy | Copied. `base_path` is left out, and the target keeps its own, when the base path lies in a spoke root of the source or in a replication target directory. |
| `qumulo_directories`, `qumulo_file_acls`, `qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_quotas`, `qumulo_snapshot_policies`, `qumulo_s3_buckets` | filter | Each entry whose path lies in a spoke root of the source, or in a directory the source receives replication into, is left out with a note. |
| `qumulo_tenants` | filter | Every tenant is kept, with its `name`, `previous_name`, switches, `smb_settings` and `nfs_settings`. `networks` is dropped from every entry, with one note for each tenant that had any, since the networks hold the source's own addresses. Each nested share and export passes the filter the flat lists pass, so one in a spoke root or a replication target directory is left out with the note a flat one gets, naming its tenant. The shared entries of the flat lists are filtered as those lists are. |
| `qumulo_s3_bucket_policies` | filter | A policy goes where its bucket goes. |
| `qumulo_portal` | drop | The hub's record of the portal is the source's. Declare the target's own portal, with spoke roots of its own. |
| `qumulo_replication_relationships` | drop | The peer's record of each relationship is the source's. Declare the target's own, with target directories of its own. One note per relationship. |
| `qumulo_s3_access_keys` | drop | The source's S3 clients hold each key's secret. Declare the target's own keys. One note per key. |
| `qumulo_network` | drop | It holds the source's own addresses. |
| `qumulo_saml` | drop | Its `cluster_dns_name` is the source's Web UI address, for which the identity provider's application is set up. When the source requires AD users to sign in through the identity provider, the note says they sign in to the clone with a password until its own `qumulo_saml` is declared. |
| `qumulo_dns`, `qumulo_ssl_ciphers`, `qumulo_time`, `qumulo_filesystem_settings`, `qumulo_web_ui_settings`, `qumulo_local_groups`, `qumulo_local_users`, `qumulo_user_defined_mappings`, `qumulo_roles`, `qumulo_role_members`, `qumulo_smb_settings`, `qumulo_nfs_settings`, `qumulo_factory_defaults` | copy | Copied as captured. A local user's `password`, the join credentials, a keytab, a join key and a license document are removed wherever they appear. |

Each dropped or filtered item is listed in the file under what was left out, with its reason.

#### Rules for variables `capture_info` never returns

These rules apply when `clone_host_vars` is given a capture built some other way, such as a capture file with peer
values or an adopted file.
`clone_edge.yml` passes `capture_info`'s own capture, which holds none of these variables, so these rules never produce
a note there.
The SSL supply lines are still written (see [Supply lines the clone writes](#supply-lines-the-clone-writes)).

| Variable | Rule | What the clone does |
|---|---|---|
| `qumulo_ssl` | drop | The target serves its own certificate, for the names its clients use. |
| `qumulo_previous_cluster_uuids` | drop | It names clusters the source took over from in a rebuild. |
| `qumulo_portal_hosts`, `qumulo_replication_address`, `qumulo_replication_port` | drop | They hold the addresses the source's peers reach the source at. |
| `qumulo_managed_by`, `qumulo_terraform_owned_areas` | drop | The target's inventory says what formed the target and which areas Terraform owns. |
| `qumulo_identity_attributes` | copy | Copied as captured. |

### Supply lines the clone writes

The clone keeps the capture's supply lines for local user passwords.
It replaces the lines whose reason was the source's own, and removes those for S3 access keys.

| Line | Written when |
|---|---|
| `qumulo_ad.join_user`, `qumulo_ad.join_password` | the file holds `qumulo_ad` |
| `qumulo_monitoring.nexus_join_key` | the source's Nexus connection is on |
| `qumulo_capacity.license_document` | the file holds a `license` key in `qumulo_capacity`, or the capture asked for a document (G-LICENSE) |
| `qumulo_kerberos.keytab` | the capture named it |
| `qumulo_ssl.certificate`, `qumulo_ssl.private_key` | the capture named them |
| `qumulo_local_users[<name>].password` | for each local user except `guest` |

### Variables the target's inventory defines

A `host_vars` file outranks the inventory's other variables, so the clone leaves out each variable the target's
inventory already defines, with a note, and removes its supply lines.

The exception is a mapping that holds only the variable's secrets.
`qumulo_ad` with only `join_user` and `join_password`, `qumulo_kerberos` with only `keytab` or `keytab_path`,
`qumulo_monitoring` with only `nexus_join_key`, and `qumulo_capacity` with only `license_document` each keep the
source's other settings in the file.
The first pass takes those secrets from the inventory, but the file's variable outranks the inventory's from then on,
and the note says to move the secrets into the file.

- A mapping key counts only when its value is not empty (an empty string, list or mapping, `false` and `0` are empty).
  An empty mapping, or one that holds only empty placeholders, therefore counts as holding only the variable's secrets,
  and the source's settings are kept.
- A variable that the clone's own existing file holds does not count as one the inventory defines. A second run
  therefore writes the same file.

The target's `qumulo_cluster_name` in the inventory must equal `clone_edge_name`, or the clone fails.

A variable the inventory defines that the file does not hold stays in force beside the file, so the clone leaves out
each share or export entry the inventory also declares, and refuses one it declares with other values; see
[Declared again by the inventory](#declared-again-by-the-inventory).
The clone drops `qumulo_network` and each tenant's `networks`, so it never declares a network twice.
On a re-run, the variables an earlier clone's file holds are that file's, not the inventory's, so the second run
decides as the first did.

### What the first pass needs

`clone_host_vars` returns `first_pass_needs`: the supply lines the first baseline cannot do without, minus those named in
`clone_edge_supply` and those the inventory gives.

- a password for each local user the target lacks and that is present in the file, since a user is created with one;
- the join credentials while the target is not joined to the file's domain.

The other secrets guard what the first pass skips or hands off.
The clone writes the file, then fails naming each missing secret and changing nothing on the target.
A Nexus join key given in `clone_edge_supply` registers the target in the same pass.
Without a join key or `qumulo_nexus_api_token`, the first pass skips registration without a message.

### What the clone checks

Every refusal comes before anything changes on the target, except the missing-secret one, which follows the write of
the file.
A `clone_edge_check_only` run refuses a target whose inventory declares `qumulo_portal` or
`qumulo_replication_relationships`.

The check that the target is fresh reads the shares and exports nested under the target's tenant entries and the flat
lists' shared entries, beside the flat entries. The target's capture reads the `tenants` area for it. The tenant
formation creates is the target's only tenant, or else the one named `Default` or renamed from it. Only that tenant's
factory `Files` share and `/` export are exempt. Every other tenant counts as configuration the target holds, as does
every other share or export under any tenant, so a target with a second tenant is refused unless
`clone_edge_allow_configured_target` is true. The refusal names each, as "the tenant finance" or "the SMB share ledger
of tenant finance".

After the target's checks and before the file is rendered, the clone leaves out the shares and exports the inventory
declares too, and refuses one the inventory declares with other values
([Declared again by the inventory](#declared-again-by-the-inventory)); nothing is written.

[Troubleshooting: cloning](troubleshooting.md#cloning) lists each refusal with its message.

## Related

- Tasks: [Adopt a cluster you already run](../how-to/adopt-a-cluster.md),
  [Clone an edge's configuration onto a new edge](../how-to/clone-edge.md),
  [Serve a directory as an S3 bucket](../how-to/s3.md).
- Reasons: [State management](../explanation/state-management.md#capture-and-adoption)
  (also [cloning](../explanation/state-management.md#cloning)), [Rebuilds](../explanation/rebuilds.md),
  [Terraform ownership](../explanation/terraform-ownership.md).
- Registers: [gaps](gaps.md) and [quirks](quirks.md).
- Modules: `ansible-doc qumulo.cluster.capture_info`, `ansible-doc qumulo.cluster.clone_host_vars`,
  `ansible-doc qumulo.cluster.host_vars_text`.
