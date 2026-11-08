<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Inventory variables

The variables the collection's playbooks and roles read from an inventory: the groups, the connection, the variable
for each area of a cluster, ownership, the variables passed for one run, and the variables of the other roles.

The keys, types and defaults of each variable are in the role's argument specification, which
`ansible-doc -t role qumulo.cluster.cluster_baseline` prints and the doc site renders from
[`roles/cluster_baseline/meta/argument_specs.yml`](../../roles/cluster_baseline/meta/argument_specs.yml). This page does
not copy them. It gives what the specification does not: which group reads what, which step applies which variable,
and the rules that cut across variables. A key that the specification does not list for a variable, such as a misspelt
one, is refused before the baseline's first task, with a message that names the path to the key; a tenant's
`smb_settings` and `nfs_settings`, which the specification cannot list, are checked at step 2. [`examples/inventory/`](../../examples/inventory/) is a complete inventory
with a comment on every variable it sets; it does not show tenants yet, which
[Manage tenants](../how-to/tenants.md) does. [Set up an inventory for your clusters](../how-to/set-up-inventory.md) builds one, and
[Declare common settings in the inventory](../how-to/inventory-recipes.md) has recipes for most areas.

Ids such as `G-TOKEN` and `QUIRK-060` are entries of the [gap register](gaps.md) and the [quirk register](quirks.md).

## Groups

| Group | Holds | Read by |
|---|---|---|
| `qumulo_clusters` | Every cluster | `baseline.yml`, `capture.yml`, `upgrade.yml`, `admin_password_rotate.yml`; `replication_restore.yml` and `replication_failover.yml` by default; `clone_edge.yml` requires the target in it |
| `qumulo_edges` | Unprotected edges | `edge_predestroy.yml`, `edge_rebuild.yml`, `edge_cache_resize.yml`, `edge_license_complete.yml` |
| `qumulo_hubs` | Hubs that edges pair with through a portal | The edge rebuild: it requires `qumulo_portal.hub` to name a host in this group, and finds each portal's hub in it by `qumulo_cluster_uuid`. The baseline's portal step reads the host `qumulo_portal.hub` names, in any group |

The example inventory makes `qumulo_edges` and `qumulo_hubs` children of `qumulo_clusters`. The playbooks are in
[`playbooks/`](../../playbooks/); [Playbooks and roles](playbooks-and-roles.md) lists what each does.

The [`qumulo.cluster.terraform`](../../plugins/inventory/terraform.py) inventory plugin puts each host in one group for
its kind (`qumulo_edges`, `qumulo_cnq` or `qumulo_baremetal`) and one `qumulo_managed_by_<value>` group, all under
`qumulo_clusters`. It never puts a host in `qumulo_hubs`. No playbook or role reads the `qumulo_cnq`,
`qumulo_baremetal` or `qumulo_managed_by_*` groups; they select `group_vars`.

## Connection

The hosts are REST endpoints, so the modules run on the controller:

| Variable | Value |
|---|---|
| `ansible_connection` | `local` |
| `ansible_python_interpreter` | `"{{ ansible_playbook_python }}"` |

Every playbook passes the connection variables to every module through `module_defaults` for
`group/qumulo.cluster.qumulo`, with the header in [`playbooks/baseline.yml`](../../playbooks/baseline.yml).

| Variable | Module option | Default | Set by |
|---|---|---|---|
| `qumulo_api_host` | `api_host` | none; required | you, or the Terraform plugin |
| `qumulo_api_port` | `api_port` | `8000` | you |
| `qumulo_api_username` | `api_username` | `admin` | you |
| `qumulo_api_password` | `api_password` | none | you, usually from a vault or a lookup |
| `qumulo_api_token` | `api_token` | none | you; set exactly one of the password and the token |
| `qumulo_validate_certs` | `validate_certs` | `true` | you |
| `qumulo_ca_path` | `ca_path` | the system's trust store | you |
| `qumulo_cluster_uuid` | `expected_cluster_uuid` | none | you, the Terraform plugin, or `capture.yml` on adoption |
| `qumulo_api_password_after` | none | none | you, encrypted, in the host's `host_vars`; read by `admin_password_rotate.yml` only, which logs in with it for the rest of its run |

- A module reads the cluster's UUID, which needs no login, before it sends credentials, and fails when it is not
  `qumulo_cluster_uuid`. An empty UUID turns the check off, and a module that logs in warns.
- An access token is bound to one cluster and does not survive a rebuild (G-TOKEN).
- A rebuilt edge has a new UUID. The rebuild report gives the `host_vars` patch.
- `request_timeout` and `retry_timeout` have no inventory variable. The collection's playbooks leave them unset, so
  `QUMULO_REQUEST_TIMEOUT` and `QUMULO_RETRY_TIMEOUT` set them; a play of your own can also set them in
  `module_defaults`.
- Each module option falls back to a `QUMULO_*` environment variable when it is not given
  ([`plugins/doc_fragments/connection.py`](../../plugins/doc_fragments/connection.py) names them). The collection's
  playbooks give every connection option except `ca_path`, `request_timeout` and `retry_timeout`, so under them only
  `QUMULO_CA_PATH` (while `qumulo_ca_path` is unset), `QUMULO_REQUEST_TIMEOUT` and `QUMULO_RETRY_TIMEOUT` take effect.

### Variables the baseline replaces during a run

When a step moves the connection, `cluster_baseline` replaces these variables for the steps after it. A check run
replaces none of them for a change it only predicts.

| Variable | Replaced by | After |
|---|---|---|
| `qumulo_api_host` | `qumulo_api_host_after` | the network step |
| `qumulo_ca_path` | `qumulo_ssl.ca_path_after`; `qumulo_validate_certs` is also set to `true` | the certificate step |
| `qumulo_api_password` | the new password of the user the play connects as | the local users step (13), when it changes that user's password |

### Variables the Terraform plugin sets

| Variable | Value |
|---|---|
| `qumulo_api_host` | the output's `api_host` |
| `qumulo_cluster_uuid` | the output's `cluster_uuid` |
| `qumulo_cluster_kind` | `edge`, `cnq` or `baremetal`; the upgrade reads it to pick a host's images ([The upgrade](upgrade.md)), and the baseline to default the areas Terraform owns ([Ownership](#ownership)) |
| `qumulo_managed_by` | `none`, `terraform_provider` or `legacy_provisioner` |
| `qumulo_terraform_owned_areas` | the output's `owned_areas` |
| `qumulo_password_ref` | where the password lives, unless the output gives null; no role or playbook reads it, and `group_vars` resolve it into `qumulo_api_password` |
| `qumulo_portal_hosts`, `qumulo_replication_address` | only when the output gives the addresses or a load balancer name |

[The Terraform inventory plugin](terraform-inventory.md) lists the output's fields.

## Areas

`cluster_baseline` applies the areas in this order. An area whose variable is unset is left alone. The step numbers are
those of the role's documentation.

Step 1 is `cluster_guard`, unless `cluster_baseline_guard` is `false`. Step 2 holds five checks that fail before
anything is written: the ownership check, no quota, snapshot policy or S3 bucket under a spoke root (G-SPOKE-PATHS),
no path with both a mode and an ACL (G-FS-ACL-MODE), every `lock_key` in `qumulo_snapshot_policies` held by the file
system key store unless `-e qumulo_allow_deferred_policy_lock_keys=true` is given, and no keytab to install beside a
domain join (G-KRB-AD). Whenever the inventory declares `qumulo_tenants`, `qumulo_network`, `qumulo_smb_shares`,
`qumulo_nfs_exports`, `qumulo_nfs_settings` or `qumulo_factory_defaults`, step 2 also reads the cluster's tenants and plans steps 3a to 20a ([Tenants](#tenants)). Step 8 is the license Qumulo
issues outside the role (G-LICENSE), and step 12, the encryption key store, is not in this release.

| Step | Variable | Module | Area name |
|---|---|---|---|
| 3a, 3b, 20a | `qumulo_tenants` | `tenant`; 3a renames, turns services on and creates, 3b turns services off, 20a deletes | `tenants` |
| 3 | `qumulo_network`, and the `networks` of each tenant in `qumulo_tenants` | `network_config` | `network`, and `network.floating_ips` for its `floating_ip_ranges` |
| 4 | `qumulo_dns` | `dns_settings`, and `dns_lookup_overrides` for its `lookup_overrides` | `dns` |
| 4 | `qumulo_authoritative_dns` | `authoritative_dns` | `dns`, and `dns.fqdn` for its `enabled` and `fqdn` |
| 4, 14 | `qumulo_time` | `time_settings`; `use_ad_for_primary: true` after the join | `time` |
| 5 | `qumulo_cluster_name` | `cluster_settings` | `cluster` |
| 5 | `qumulo_filesystem_settings` | `filesystem_settings` | `filesystem` |
| 5 | `qumulo_web_ui_settings` | `web_ui_settings` | `web_ui` |
| 6 | `qumulo_ssl`, `qumulo_ssl_ciphers` | `ssl_certificate`, `ssl_ciphers` | `ssl` |
| 7 | `qumulo_monitoring`, `qumulo_nexus_api_token`, `qumulo_nexus_api_url` | `monitoring_settings`, `nexus_connection`, `nexus_registration` | `monitoring` |
| 7 | `qumulo_audit_destinations` | `audit_destination` | `audit`, and `audit.cloudwatch` for its CloudWatch destinations |
| 9 to 11 | `qumulo_capacity` | `license`, `capacity_clamp`, `portal_eviction_settings` | `capacity` |
| 13 | `qumulo_local_groups`, `qumulo_local_users` | `local_group`, `local_user` | `local_groups`, `local_users` |
| 14 | `qumulo_kerberos` | `kerberos_keytab`, before the join; `kerberos_settings`, after the join and the domain-time step | `kerberos` |
| 14 | `qumulo_ad` | `ad_domain` | `ad` |
| 14 | `qumulo_user_defined_mappings`, `qumulo_identity_attributes` | `user_defined_mappings`, `identity_attributes`, then `auth_cache_clear` when either changed | `identity_mappings` |
| 14 | `qumulo_saml` | `saml_settings` | `saml` |
| 15 | `qumulo_roles`, `qumulo_role_members` | `role`, then `role_member`, then `role` for the roles declared absent | `roles` |
| 16 | `qumulo_portal`, `qumulo_portal_hosts` | the `portal_pair` role | `portal` |
| 17 | `qumulo_directories`, `qumulo_file_acls` | `directory`, `file_acl` | `directories` |
| 18 | `qumulo_smb_settings`, `qumulo_smb_shares`, `qumulo_factory_defaults.smb_files_share`, and each tenant's `smb_settings` and `smb_shares` | `smb_settings`, `smb_share`; removals first | `smb` |
| 19 | `qumulo_factory_defaults.nfs_root_export`, `qumulo_nfs_settings`, `qumulo_nfs_exports`, and each tenant's `nfs_settings` and `nfs_exports` | `nfs_export`, `nfs_settings`; removals first | `nfs` |
| 19 | `qumulo_s3_settings`, `qumulo_s3_buckets`, `qumulo_s3_bucket_policies` | `s3_settings`, `s3_bucket`, `s3_bucket_policy` | `s3` |
| 19 | `qumulo_ftp_settings` | `ftp_settings` | `ftp` |
| 20 | `qumulo_quotas`, `qumulo_snapshot_policies` | `directory_quota`, `snapshot_policy` | `quotas`, `snapshots` |
| 21 | `qumulo_replication_relationships` | the `replication_pair` role | `replication` |
| 22 | `qumulo_s3_access_keys` | `s3_access_key` | `s3` |

`qumulo_nexus_api_token`, `qumulo_nexus_api_url`, `qumulo_portal_hosts`, `qumulo_replication_address`,
`qumulo_replication_port` and `qumulo_previous_cluster_uuids` belong to no area; the ownership check ignores them.

Each variable takes its module's options, so `ansible-doc qumulo.cluster.<module>` documents the keys of its items.
The reasons for the order are in [Architecture](../explanation/architecture.md#the-order-of-areas).

### The baseline's own variables

| Variable | Default | Meaning |
|---|---|---|
| `cluster_baseline_guard` | `true` | Run `cluster_guard` first, checking the UUID and waiting until the cluster is ready. |
| `cluster_baseline_check_mode` | `ansible_check_mode` | Whether the role's tasks run in check mode. Defaults to `ansible_check_mode`, which is true only under `--check` and stays false under the `check_mode` keyword; `clone_edge.yml` sets this variable from `clone_edge_check_only`. |
| `cluster_baseline_replication_on_held` | `fail` | What step 21 does with a held relationship: `fail` fails the step after its other relationships, `report` keeps the relationship as a result with status `held` and writes nothing. |
| `cluster_baseline_artifact_dir` | an `artifacts` directory beside the inventory source that defines the host | Where a new S3 key's secret is written. |

## Notes by variable

### Network

Variable: `qumulo_network`. Step 3.

- `frontend_networks` is the whole set: a network the cluster has and the list leaves out is removed.
- A change that drops `qumulo_api_host` from the cluster's addresses fails unless
  `qumulo_allow_management_disruption` is `true` and `qumulo_api_host_after` names the address to use afterwards.
- On a cluster whose network the host manages, such as a host-managed cloud cluster, every network has type `HOST`,
  and only its name, tenant, netmask and floating ranges can change.
- A flat network names its tenant with `tenant`, a tenant's name. `tenant_id` is deprecated: the role accepts it for one
  release, with a warning, and translates it to the tenant's name, and a network entry that sets both `tenant` and
  `tenant_id` is refused. A network of the backend bond takes no tenant, and a `tenant_id` there is dropped with a
  warning.
- With more than one tenant, a new network that is not on the backend bond needs a `tenant`.
- At most one STATIC network, across `frontend_networks` and the tenants' `networks`, carries an IPv4 `default_gateway`,
  and at most one an IPv6 one. Step 2 refuses a second on every cluster, before any write. Networks the cluster already
  gives a gateway of that version warn and pass.

### Tenants

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

Variables: `qumulo_tenants`, `qumulo_management_network`, `qumulo_cluster_port`, `qumulo_cluster_kind`. Steps 2, 3a, 3, 3b
and 20a, and the tenant keys of steps 18 and 19.

`qumulo_tenants` is a list of tenants, each found by its `name`, compared exactly. A tenant the list leaves out stays as
it is. `ansible-doc -t role qumulo.cluster.cluster_baseline` lists the keys.

- `previous_name` is ignored when `state` is `absent`.
- A service flag (`web_ui_enabled`, `rest_api_enabled`, `ssh_enabled`, `replication_enabled`, `nfs_enabled`,
  `smb_enabled`) that is unset is off on a new tenant and unchanged on an existing one.
- `smb_settings` and `nfs_settings` set only the keys given, or `inherit` removes the override. Leaving the key out
  leaves the override as it is.
- `identity` is reserved for per-tenant identity, which is not supported yet. Step 2 refuses it.
- A tenant's `networks` is in the area `network` and its `floating_ip_ranges` in `network.floating_ips`. Its overrides
  and its shares and exports are in `smb` or `nfs` as well, and the tenants are in `tenants` ([Ownership](#ownership)).
- `qumulo_management_network` is the id of the network the play reaches the cluster on, or a list of ids, such as two
  for a name with an IPv4 and an IPv6 network. It is used when two or more tenants remain after a write. Declare it for a
  play behind NAT or a load balancer, or when the cluster's network status is unavailable. `0` is a network id.
- `qumulo_cluster_port` is the port the cluster listens on for the play, when a load balancer or tunnel maps the port the
  play connects to onto another. Unset, the rule takes `qumulo_api_port`, and at port 443 it needs both
  `rest_api_enabled` and `web_ui_enabled` on the tenant that holds the play's network. Declare `443` when your port is
  mapped onto 443.
- `qumulo_cluster_kind` is `cnq`, `edge` or `baremetal`. Step 2 refuses every tenant write to a cloud cluster: `cnq`,
  or, with the kind unset, a cluster whose protection type is object-backed. An on-premises cluster whose storage is
  object-backed declares `baremetal`. Null counts as unset. An unset kind falls back to the protection type for tenancy,
  and counts as `cnq` for the areas Terraform owns by default on a provider-formed cluster ([Ownership](#ownership)), so
  declaring the kind of an on-premises cluster settles both.
- `qumulo_allow_management_disruption` skips the continuity rule for the whole run.
- The baseline, in a check run too, reads the cluster at step 2 whenever the inventory declares `qumulo_tenants`,
  `qumulo_network`, `qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_nfs_settings` or `qumulo_factory_defaults`: the
  tenant list, which needs `PRIVILEGE_TENANT_READ`, the network document, the shares and the exports, the NFS settings
  and each tenant's. A failure to read the tenant list stops the run.
- A flat entry of `qumulo_smb_shares` or `qumulo_nfs_exports` takes `tenants`, a list of tenant names or `all`. It
  is needed once the inventory or the cluster, with the tenants the run creates, has more than one tenant; which pairs
  it claims is under [Lists add, unless they purge](#lists-add-unless-they-purge). A flat entry's `tenant_id` is
  deprecated, counts as `tenants: [<that tenant's name>]` with a warning, and cannot be given beside `tenants`.
- The factory `Files` share and `/` export are tenant 1's. `qumulo_factory_defaults` applies there, and does nothing,
  saying so, when tenant 1 is gone.

[Manage tenants](../how-to/tenants.md) has the procedures, and
[Tenants and the continuity rule](../explanation/multitenancy.md) the reasons.
[Troubleshooting](troubleshooting.md#tenants) has the messages.

### DNS

Variable: `qumulo_dns`. Step 4.

- `servers` and `search_domains` are in the order the cluster asks. Each is the whole list.
- `lookup_overrides` is the cluster's lookup table, its `/etc/hosts`: names the cluster resolves without asking DNS.
  Each entry gives an `ip_address` and its `aliases`, the first of which is the name a lookup of the address returns.
  The table is the whole set: an address the cluster has and the table leaves out is removed.
- The role applies DNS before the domain join, so an entry can name a domain controller that the site's DNS does not.
- When the role changes the servers, it clears the cluster's DNS cache afterwards. It also clears it when the DNS step
  changed something and then failed. A change of `lookup_overrides` needs no clear.

### Authoritative DNS

Variable: `qumulo_authoritative_dns`. Step 4.

- The cluster answers DNS queries for its own name with its floating addresses, one node's per answer. A cluster
  without floating addresses has no address to answer with.
- Clients reach the answers only once the site's DNS delegates the name to the floating addresses. The collection does
  not set that up.
- `host_restrictions: []` lets any client query the server.
- Once a name is set, the cluster cannot clear it (G-DNS-FQDN-CLEAR). `enabled: false` stops the answers.
- When `qumulo_terraform_owned_areas` lists `dns.fqdn`, declaring `enabled` or `fqdn` fails at the ownership check
  ([Ownership](#ownership)). `host_restrictions` and `record_ttl_seconds` still apply.

### Time

Variable: `qumulo_time`. Steps 4 and 14.

Step 4 applies the source, the NTP servers and `use_ad_for_primary: false`, which the hypervisor source needs.
`use_ad_for_primary: true` is applied at step 14, after the join.

### Name

Variable: `qumulo_cluster_name`. Step 5.

The name is also the cluster's computer account in Active Directory. The server refuses a rename while the cluster is
joined. The reason the role sets it first is in [Identity](../explanation/identity.md).

### File system settings

Variable: `qumulo_filesystem_settings`. Step 5.

A new `permissions_mode` applies to later operations only; the cluster rewrites no ACL it already holds. The role sets
it before the directories and ACLs of step 17.

### Web UI

Variable: `qumulo_web_ui_settings`. Step 5.

`inactivity_timeout` is in minutes, from 2 to 600; `0` turns the timeout off. An empty `login_banner` removes the
banner.

### Certificate

Variable: `qumulo_ssl`. Step 6.

- `certificate` and `private_key` are PEM. `probe` lists the addresses to compare, as `host` or `host:port`; a host alone
  uses `qumulo_api_port`, and an empty list compares `qumulo_api_host` only. The cluster offers no way to read the
  installed certificate back (G-SSL-READBACK), so the step installs when any probed address serves a certificate other
  than the first of `certificate`. An address that is not probed is not compared, and keys are never compared.
- `ca_path_after` is the CA to verify the cluster with once the certificate is installed. Declaring it also lets the
  role install a chain that does not verify against the current `qumulo_ca_path`.
- A newly formed edge serves a factory certificate that cannot be verified (G-FACTORY-TLS), and a run that connects
  to it with `qumulo_validate_certs: false` sends the `admin` password unverified.
  [Manage certificates](../how-to/manage-certificates.md#replace-the-factory-certificate) installs the certificate in a
  run of its own that changes nothing else; the baseline that follows runs with verification on, and its certificate
  step reports `ok`.

### Cipher suites

Variable: `qumulo_ssl_ciphers`. Step 6.

- `tls_1_2` and `tls_1_3` are lists of suites in the order the cluster prefers them. The order is part of the setting.
- The cluster adds `ECDHE-RSA-AES128-SHA256` to the end of a `tls_1_2` list and `TLS_AES_128_GCM_SHA256` to the end of
  a `tls_1_3` list that leaves it out. `ssl_ciphers` adds the same suite before it compares, so a second run changes nothing (QUIRK-060).
- An empty list fails (QUIRK-061).
- The cluster keeps a TLS 1.2 name it does not know and never uses it (QUIRK-061). The role cannot tell such a name
  from a valid one.

### Monitoring and Nexus

Variables: `qumulo_monitoring`, `qumulo_nexus_api_token`, `qumulo_nexus_api_url`. Step 7.

| Keys of `qumulo_monitoring` | Module | What it sets |
|---|---|---|
| `enabled`, `mq_host`, `mq_port`, the proxy keys, `vpn_enabled`, `vpn_host`, `period` | `monitoring_settings` | Cloud monitoring and the support VPN |
| `nexus` | `nexus_connection` | Whether the cluster connects to Nexus, where, how often, and which capabilities it allows |
| `nexus_join_key`, `nexus_account_id` | `nexus_registration` | The cluster's registration |

- A key left unset keeps the cluster's value. Turning `enabled` off also turns the Nexus connection off.
- The role registers the cluster only when `qumulo_monitoring.nexus.nexus_enabled` is true and there is a join key or
  `qumulo_nexus_api_token`. With the token, `nexus_registration` mints its own single-use key for each cluster.
  `nexus_account_id` names the organization the minted key registers to. `qumulo_nexus_api_url` names a Nexus API
  other than the modules' default.
- A join key registers one cluster only (G-NEXUS-KEY).
- A registered cluster is left alone.
- The license step never uses the token. The collection never reads licenses in Nexus
  ([Licensing and capacity](../explanation/licensing-and-capacity.md)).

[Register a cluster with Nexus](../how-to/nexus.md) has the procedure.

### Audit logs

Variable: `qumulo_audit_destinations`. Step 7.

- Destinations are listed by name. A destination the list leaves out is left alone, and `state: absent` deletes one.
- When `qumulo_terraform_owned_areas` lists `audit.cloudwatch`, the ownership check at step 2 refuses every declared
  entry whose `type` is `CLOUDWATCH` before anything is written, unless `qumulo_take_over_from_terraform` is `true`.
  An entry that would change nothing is refused too.

### Capacity

Variable: `qumulo_capacity`. Steps 9 to 11.

- The license step runs whenever `qumulo_capacity` is declared, and nothing in it waits for a license.
- A host's `qumulo_capacity` replaces the group's whole ([Precedence](#precedence)). The example inventory keeps the
  license document in a variable of its own, `vault_qumulo_license_document`, which the group's `qumulo_capacity`
  refers to.
- A clamp below the stored one fails. `playbooks/edge_cache_resize.yml` chooses the route to a smaller cache
  (G-CLAMP-SHRINK).

| `license` | Missing license | Clamp |
|---|---|---|
| `pending` (default) | Handed off, naming the cluster's UUID | Waits for the license |
| `required` | As `pending`, and the role sets `cluster_baseline_license_missing` while the cluster has no active license | Waits for the license |
| `none` | Nothing is handed off; the role says the edge runs without a license | Written with no license, by the first pass and the second |

- `none` goes with a clamp and never with `license_document`; the license step refuses the pair.
- With a license active, `none` runs as `pending` and warns that `none` is stale.
- An edge with `none` provisions nothing above 1 TB until a license arrives (G-EDGE-FLOOR).

[License an edge](../how-to/licensing.md) has the procedure for the two passes.

### Local users and groups

Variables: `qumulo_local_groups`, `qumulo_local_users`. Step 13.

- The role creates the groups without members, then the users, then sets the groups' members. A primary group must
  exist before its users.
- A local password cannot be read back (G-PASSWORD). `update_password: on_create`, the default, sends it only when the
  user is created. `always` sends it on every run and reports a change each time.
- Changing the password of `admin` needs `allow_admin_password_change`, because the server applies it to every node's
  console and SSH password too (G-ADMIN-OS).
- `qumulo_api_password_after` is not a key of these variables ([Connection](#connection)); [Rotate the admin
  password](../how-to/rotate-admin-password.md) has the procedure.

### Active Directory

Variable: `qumulo_ad`. Step 14.

On an edge, `join_user` and `join_password` are what let the rebuild's predestroy phase delete the computer account and
record that the old edge left the domain. A replay with `qumulo_ad` refuses to join under the old name without that
record, unless `edge_rebuild_old_edge_destroyed` is `true`.

### Kerberos

Variable: `qumulo_kerberos`. Step 14: the keytab before the join, and the setting after it and after the domain-time
step.

The keys, their types and which are `no_log` are in `ansible-doc -t role qumulo.cluster.cluster_baseline`.

- `keytab` is base64, and whitespace in it is ignored. `keytab_state: absent` deletes the cluster's keytab, and the role
  then ignores `keytab` and `keytab_path`.
- The keytab step runs when `keytab`, `keytab_path` or `keytab_state` is defined, with `kerberos_keytab`. The settings
  step runs when `use_alt_security_identities_mapping` is defined, with `kerberos_settings`. Each receives the keys of
  `qumulo_kerberos`, never the variable whole.
- A keytab to install, with `keytab` or `keytab_path` and no `keytab_state: absent`, fails the role before its first
  write when `qumulo_ad` is also declared (G-KRB-AD). `keytab_state: absent` beside `qumulo_ad` deletes the keytab, then
  joins.
- The role never leaves a domain. A cluster that is already joined, with no `qumulo_ad` declared, passes the opening
  check, and the keytab step then fails in `kerberos_keytab` (G-KRB-AD). To move a joined cluster to a standalone KDC,
  run `ad_domain` with `state: absent`, then the baseline with the keytab.
- `capture_info` writes `use_alt_security_identities_mapping` and never a key ([Capture
  areas](capture-and-clone.md#areas-and-the-variables-they-write)).

[Keep secrets out of the inventory](../how-to/store-secrets.md#encrypt-a-binary-secret-such-as-a-keytab) shows how
to keep the keytab encrypted, and [Join a cluster to Active Directory](../how-to/active-directory.md#if-the-join-is-refused)
has the order of a keytab and a join.

### Mappings, home directories and SAML

Variables: `qumulo_user_defined_mappings`, `qumulo_identity_attributes`, `qumulo_saml`. Step 14, after the join.

[Join a cluster to Active Directory](../how-to/active-directory.md) has the procedures.

### Roles

Variables: `qumulo_roles`, `qumulo_role_members`. Step 15.

- Members are trustees in the collection's grammar: `local:svc-backup`, `ad:CORP\Qumulo-Admins`, `ldap_group:admins`,
  `sid:S-1-5-21-...`, `uid:1000` or `Everyone`. A bare name is refused. `ad:` trustees resolve only after the join,
  except `ad:NT AUTHORITY\Authenticated Users`.
- The role creates custom roles, then applies `qumulo_role_members`, then deletes the roles declared `state: absent`.
  The cluster refuses to delete a role that still has members, so `qumulo_role_members` removes them first, with
  `purge: true` or `state: absent`.
- `privileges` is the role's whole set. The privileges differ between Qumulo Core versions, and a name the cluster
  does not know fails before anything is written.
- The built-in roles `Administrators`, `Observers`, `Data-Administrators` and `Qumulo-Support` cannot be changed. Name
  them in `qumulo_role_members` only.

### Portal

Variables: `qumulo_portal`, `qumulo_portal_hosts`. Step 16.

- `qumulo_portal` is declared on the spoke. `hub` names an inventory host with a `qumulo_cluster_uuid`; the edge
  rebuild also requires it to be in `qumulo_hubs`.
- Step 16 checks that both clusters run one Qumulo Core version, proposes the spoke portal and its roots, accepts and
  authorizes them on the hub, and waits until the portal is ACTIVE.
- A `spoke_root` must not exist on the spoke yet, and its parent directory must. A `hub_root` must exist on the hub.

[Pair an edge with its hub](../how-to/pair-edge-with-hub.md) has the procedure.

### Directories and ACLs

Variables: `qumulo_directories`, `qumulo_file_acls`. Step 17.

- A path may have a `mode` in `qumulo_directories` or an ACL in `qumulo_file_acls`, not both. Setting a mode rewrites
  the ACL, and the role refuses a path with both before it writes anything (G-FS-ACL-MODE).
- `qumulo_file_acls` gives a path its whole ACL, entries in order, with the trustees in the grammar shares take.
- The cluster applies a new ACL to the path alone. Entries with inheritance flags reach what is created beneath it
  afterwards.
- On an edge, a path inside a spoke root is the hub's data. The role refuses to change its ACL unless the entry sets
  `allow_data_loss: true`, because the write replaces the hub's ACL whole.

### Replication

Variables: `qumulo_replication_relationships`, `qumulo_replication_address`, `qumulo_replication_port`,
`qumulo_previous_cluster_uuids`. Step 21.

`qumulo_replication_relationships` is declared on the source host. Each entry has a `source_path` there, a `target`
that is an inventory host, and a `target_path` on it. A relationship is the two paths and the target's UUID; the
address and the port are settings of the target host.

The step makes these checks once, before it touches any relationship, and fails when one is unmet:

- The target is an inventory host with a `qumulo_cluster_uuid` and a `qumulo_replication_address`.
- The target host declares the target path in its `qumulo_directories`, path only, with no entry inside it. The
  source host's run creates the directory when the play does not include the target.
- No target path is `/`, declared twice or nested on one target host, or inside a portal root on it.
- No two hosts declare one replication address and port.
- No host's `qumulo_previous_cluster_uuids` holds its own UUID.

`qumulo_replication_address` is required on every target; `qumulo_replication_port` defaults to 3712. Step 21 also
checks the tenant that serves the address, on every run, an established relationship included. With one tenant, that
tenant must have `replication_enabled`. With two or more, the address must lie on a network, in the target's network
document, whose tenant has it on; an address that no network's ranges hold, or that lies on a network no tenant holds,
is not judged and gets a warning. The user the play connects to the target as needs `PRIVILEGE_TENANT_READ`, and with
two or more tenants `PRIVILEGE_NETWORK_READ`. Status is never read for this. While `qumulo_previous_cluster_uuids` is
set on a source, a relationship whose replica may be the only copy of the source's data is held.

Entry keys with a rule:

- `allow_non_empty_target: true` lets the source's content replace a target directory that already holds entries; the
  first job deletes whatever the source lacks. Without it, such a directory is refused.
- `state: absent` deletes the relationship on the source, then on the target, and the target directory keeps its data.
  Setting the entry back to `present` later with `allow_non_empty_target` replaces that data.

A relationship into an edge that declares `qumulo_capacity` is not created or authorized until the edge's license and
clamp apply. The run reports a hand-off naming `ansible-playbook qumulo.cluster.edge_license_complete`, which creates
the relationship once they do, and does not fail.

The grammar of a release entry is `<target host>:<target path>@<record id>:<rows>`, for example
`dr01:/replicas/edge01-projects@<record id>:O2,O4`. The rows are those of
[the overwrite table](../explanation/replication.md#the-overwrite-table). A refusal prints each entry that would lift it. A release is
passed with `-e` for one run ([Run-level variables](#run-level-variables)). A release that lifts nothing in a run
fails it, and so does one that is malformed or that names a target host the run reaches but no relationship of the
run. An entry for a host the run does not reach is left alone, so `--limit` still works.

[Replicate a directory](../how-to/replicate-a-directory.md) and [Restore a replica](../how-to/restore-replica.md)
have the procedures.

### Factory share and export

Variable: `qumulo_factory_defaults`. Steps 18 and 19.

Every formation creates the SMB share `Files` and the NFS export `/` (G-FACTORY). `smb_files_share` and
`nfs_root_export` each say what becomes of one: `absent` removes it and `present` leaves it. A rebuild needs both
keys. NFSv4 cannot be turned on while the `/` export exists.

### Quotas and snapshot policies

Variables: `qumulo_quotas`, `qumulo_snapshot_policies`. Step 20.

The cluster refuses both under a spoke root (G-SPOKE-PATHS), so step 2 fails before it writes anything when one is
declared there. A hub's roots may carry both.

A policy entry takes the options of `snapshot_policy`, among them `lock_key`: the name or id of a key in the cluster's
file system key store, which locks each snapshot the policy takes. Declare it by the key's name: the name holds on a
rebuilt or cloned cluster, where the key gets a new id. An entry without it keeps the policy's key, and `""` takes the
key off. Step 2 refuses the run, before anything is written, when a `lock_key` names a key the key store does not hold,
unless `-e qumulo_allow_deferred_policy_lock_keys=true` is given. A null `lock_key` is read as unset. The check names
each policy, its key and the two ways on. An entry with `state: absent` is not checked. The user the play connects as
needs `PRIVILEGE_FS_KEY_MANAGEMENT_READ` to read the key store.

### S3

Variables: `qumulo_s3_settings`, `qumulo_s3_buckets`, `qumulo_s3_bucket_policies`, `qumulo_s3_access_keys`. Steps 19
and 22.

- Buckets and the base path need the directories of step 17. Policies need the users and the domain join of steps 13 to
  15.
- A bucket under a spoke root fails at step 2, before anything is written.
- The cluster shows a key's secret only when it makes the key (G-S3-KEY). Step 22, the role's last, writes the secret
  at once to `<cluster_baseline_artifact_dir>/<inventory host>/secrets/s3-<user>-<access_key_id>.yml`, a directory
  and a file readable only by their owner. The role names each new key and its file in
  `cluster_baseline_s3_access_keys`, never the secret.
- An entry of `qumulo_s3_access_keys` that would make a key fails when the host has no artifact directory.

[Serve a directory as an S3 bucket](../how-to/s3.md) has the procedure.

### FTP

Variable: `qumulo_ftp_settings`. Step 19, after the S3 tasks.

- The keys are the options of `ftp_settings`. A key the variable leaves out keeps the cluster's value.
- The cluster has one FTP server for every network, so the variable has no tenant.
- `anonymous_user` is `local:<name>`, the name of a local user, or `""` for no anonymous logins. The cluster takes no
  other identity. Step 13 applies `qumulo_local_users` first, so the anonymous user may be one the same run creates.
  The name is compared in any case, and the result shows it as the cluster spells it; a check run notes a user the
  cluster does not hold yet.
- A local user that is deleted leaves the setting as a local user with an empty name, which the modules read as `local:`.
  A declared `""` replaces it, and capture leaves `anonymous_user` out.
- `greeting` takes 1 to 1024 bytes of UTF-8, compared exactly, trailing newline included. The cluster cannot set the
  factory greeting back (G-FTP-GREETING), so a capture taken before the change holds the text to go back to.
- The FTP settings are applied after the directories and ACLs of step 17.

[Serve FTP over TLS with an anonymous user](../how-to/inventory-recipes.md#serve-ftp-over-tls-with-an-anonymous-user)
has the procedure.

## Lists add, unless they purge

| Kind | Rule | Variables |
|---|---|---|
| List variable | Adds to what the cluster has. An item the list leaves out stays as it is; `state: absent` removes it. | `qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_quotas`, `qumulo_snapshot_policies`, `qumulo_directories`, `qumulo_file_acls` (a path the list leaves out keeps its ACL), `qumulo_s3_buckets`, `qumulo_s3_bucket_policies`, `qumulo_s3_access_keys`, `qumulo_local_users`, `qumulo_local_groups`, `qumulo_roles`, `qumulo_role_members`, `qumulo_audit_destinations`, `qumulo_identity_attributes`, `qumulo_replication_relationships`, `qumulo_tenants` |
| List with `purge: true` | An item with `purge: true` makes its `members` the whole set. | `qumulo_local_groups`, `qumulo_role_members` |
| Complete set | The list is everything the cluster has, and an entry left out is removed. | `qumulo_network.frontend_networks`, `qumulo_user_defined_mappings`, `qumulo_dns.lookup_overrides` |
| Whole value | One setting: the whole list. This is each item's own list, such as `qumulo_file_acls[].aces`, a role's `privileges` and a policy's `policy`, and each list inside a dict variable. | `qumulo_dns.servers`, `qumulo_dns.search_domains`, `qumulo_ssl_ciphers.tls_1_2`, `qumulo_ssl_ciphers.tls_1_3`, `qumulo_time.ntp_servers`, `qumulo_authoritative_dns.host_restrictions` |
| Dict variable | Sets only the keys it gives; the cluster keeps its values for the rest. | `qumulo_smb_settings`, `qumulo_nfs_settings`, `qumulo_s3_settings`, `qumulo_ftp_settings`, `qumulo_filesystem_settings`, `qumulo_web_ui_settings`, `qumulo_dns`, `qumulo_time`, `qumulo_monitoring`, `qumulo_authoritative_dns`, `qumulo_ssl_ciphers`, `qumulo_saml`, `qumulo_kerberos` |

For tenants, a tenant that `qumulo_tenants` leaves out stays, and `state: absent` removes one. A shared entry of
`qumulo_smb_shares` or `qumulo_nfs_exports` has an effective list: the tenants it lists that are not declared absent,
or every tenant the inventory declares present for `all`. For each tenant in that list, it claims that tenant's copy,
present or `state: absent` as the entry says. For each tenant declared absent whose name it lists, or for any under
`all`, it claims that tenant's copy for removal, so no shared entry is edited when a tenant goes. A tenant that leaves
an explicit list keeps its copy until a `state: absent` entry claims that pair. A (tenant, name) pair may come from one
entry only, whether nested, shared or the factory entry.

The reasons are in [State management](../explanation/state-management.md#lists-add-dictionaries-set-the-keys-they-give).

## Precedence

Ansible's usual precedence applies, from lowest to highest:

1. `group_vars/qumulo_clusters.yml`, then the more specific `group_vars/qumulo_edges.yml` or `qumulo_hubs.yml`.
2. Variables on the host in the hosts file, or from the Terraform plugin.
3. `host_vars/<host>.yml`.
4. Facts the roles set during a run, such as a new address or password.
5. `-e` on the command line.

- A dict is replaced whole, never merged. A host that sets `qumulo_capacity` gives every key it needs.
- A `host_vars` file outranks the Terraform plugin's variables.
- A variable given with `-e` applies to every host of the run, a portal's hub included, and outranks the facts of
  level 4. This holds for the connection variables such as `qumulo_validate_certs`.

## Ownership

Two variables say who owns what:

| Variable | Values | Default |
|---|---|---|
| `qumulo_managed_by` | `none`, `terraform_provider` or `legacy_provisioner`: what formed the cluster | `none` |
| `qumulo_terraform_owned_areas` | The areas Terraform owns, by the area names below | `[network.floating_ips]` with `terraform_provider`, unless `qumulo_cluster_kind` is `edge` or `baremetal`; empty otherwise |

- `cluster_baseline` fails at step 2, before it writes anything, when a declared variable belongs to an owned area.
- A `qumulo_terraform_owned_areas` the inventory gives is all that Terraform owns, an empty list included: the role
  manages every area the list leaves out, the floating addresses among them, whatever `qumulo_managed_by` says.
  Without one, a cluster the provider formed gives Terraform `network.floating_ips` when `qumulo_cluster_kind` is
  `cnq` or unset, since the provider applies its floating addresses again on CNQ, and nothing when it is `edge` or
  `baremetal`. The Terraform plugin always passes the output's list, and for a `cnq` entry with `terraform_provider`
  refuses a list that does not name `network.floating_ips`; the role refuses such a list in a hand-written inventory.
- With `network.floating_ips` owned, `qumulo_network` may still declare the networks, and the role keeps the cluster's
  floating addresses. Declaring `floating_ip_ranges` in `qumulo_network` is refused before any write, and the message
  names where each range is declared and the owner; a tenant's `networks` with `floating_ip_ranges` are refused by the
  area check. `qumulo_take_over_from_terraform: true` manages them as the inventory declares them.
- With `dns.fqdn` owned, `qumulo_authoritative_dns` may still set `host_restrictions` and `record_ttl_seconds`.
  Declaring `enabled` or `fqdn` fails, unless `qumulo_take_over_from_terraform` is `true`. `dns.fqdn` is never owned
  by default.
- `qumulo_take_over_from_terraform: true` hands back the floating addresses, including those in a tenant's `networks`,
  `dns.fqdn`, the `audit` area and the CloudWatch entries of `qumulo_audit_destinations`. No other area, and not a
  tenant's `networks` while `network` is Terraform's.

### Area map

`cluster_baseline_area_variables` in `roles/cluster_baseline/vars/main.yml` is the map. An area declared by several
variables is declared by any of them. A dotted name is one key of the variable before the dot.

| Area | Variables it guards |
|---|---|
| `network.floating_ips` | `floating_ip_ranges` in `qumulo_network`, which the ownership check's floating-range task refuses, and in a tenant's `networks` |
| `network` | `qumulo_network` |
| `dns` | `qumulo_dns`, `qumulo_authoritative_dns` |
| `dns.fqdn` | `enabled` and `fqdn` in `qumulo_authoritative_dns` |
| `time` | `qumulo_time` |
| `cluster` | `qumulo_cluster_name` |
| `filesystem` | `qumulo_filesystem_settings` |
| `web_ui` | `qumulo_web_ui_settings` |
| `ssl` | `qumulo_ssl`, `qumulo_ssl_ciphers` |
| `local_users`, `local_groups` | `qumulo_local_users`, `qumulo_local_groups` |
| `ad` | `qumulo_ad` |
| `kerberos` | `qumulo_kerberos` |
| `saml` | `qumulo_saml` |
| `identity_mappings` | `qumulo_user_defined_mappings`, `qumulo_identity_attributes` |
| `roles` | `qumulo_roles`, `qumulo_role_members` |
| `monitoring` | `qumulo_monitoring` |
| `audit` | `qumulo_audit_destinations` |
| `audit.cloudwatch` | The entries of `qumulo_audit_destinations` whose `type` is `CLOUDWATCH`, which step 2 checks itself |
| `capacity` | `qumulo_capacity` |
| `portal` | `qumulo_portal` |
| `directories` | `qumulo_directories`, `qumulo_file_acls` |
| `smb` | `qumulo_smb_settings`, `qumulo_smb_shares`, `qumulo_factory_defaults.smb_files_share` |
| `nfs` | `qumulo_nfs_settings`, `qumulo_nfs_exports`, `qumulo_factory_defaults.nfs_root_export` |
| `s3` | `qumulo_s3_settings`, `qumulo_s3_buckets`, `qumulo_s3_bucket_policies`, `qumulo_s3_access_keys` |
| `ftp` | `qumulo_ftp_settings` |
| `quotas` | `qumulo_quotas` |
| `snapshots` | `qumulo_snapshot_policies` |
| `replication` | `qumulo_replication_relationships` |
| `tenants` | `qumulo_tenants`, so the tenants themselves |

`cluster_baseline_item_areas` in the same file holds the keys inside the items of a list variable, which a dotted name
cannot reach. It maps each area to matchers such as `qumulo_tenants[].networks`, where `[]` stands for each item of the
list before it. The [item_areas](../../plugins/filter/item_areas.yml) filter applies them, and the ownership check
joins what it finds to what the map above finds, so a key inside an item guards its area as a variable does. `capture`
reads only `cluster_baseline_area_variables`, so these matchers add no area. They guard these keys inside the items of
`qumulo_tenants` and of the flat share and export lists:

| Area | Keys it guards |
|---|---|
| `network` | `networks` of a `qumulo_tenants` item |
| `network.floating_ips` | `floating_ip_ranges` in each of a tenant's `networks` |
| `smb` | `smb_settings` and `smb_shares` of a `qumulo_tenants` item |
| `nfs` | `nfs_settings` and `nfs_exports` of a `qumulo_tenants` item |
| `tenants` | `tenants` and `tenant_id` of each entry of `qumulo_smb_shares` and `qumulo_nfs_exports` |

A tenant's override, shares and exports are in `smb` or `nfs` and in `tenants`, since `qumulo_tenants` as a whole is in
`tenants` through the area map. A flat share or export that names its tenants, with `tenants` or the deprecated
`tenant_id`, is in `tenants` as well as in `smb` or `nfs`; a flat entry that names none is in `smb` or `nfs` alone.
`qumulo_network.frontend_networks[].tenant` is not matched: capture writes it when `tenants` is among the areas Terraform
owns, so matching it would refuse capture's own output. Each key is named in a refusal with the item's `id`, `name`,
`share_name` or `export_path`, else its index.

The reasons are in [Terraform ownership](../explanation/terraform-ownership.md).
[Use a Terraform root's output as the inventory](../how-to/terraform.md) has the procedure.

## Run-level variables

Variables given with `-e` for one run. Ansible cannot tell a variable given with `-e` from one the inventory defines, so
a value in the inventory is read too, and `-e` outranks it ([Precedence](#precedence)). `qumulo_rebuild`,
`qumulo_new_cluster_uuid` and `qumulo_replication_release` belong on the command line only: in the inventory they apply
to every later run, and a release that lifts nothing fails the run.

| Variable | Read by | Meaning |
|---|---|---|
| `qumulo_allow_management_disruption` | `cluster_baseline`: the network, certificate, local-user and role-member steps, and the continuity rule of the tenant and network steps | Allow a step that moves the connection. For tenants it skips the continuity rule for the whole run. Default `false`. |
| `qumulo_allow_deferred_policy_lock_keys` | `cluster_baseline`: steps 2 and 20; `edge_rebuild`: its predestroy phase and the checks before the replay | Apply a snapshot policy whose `lock_key` is not in the file system key store without the key, and list it in `cluster_baseline_policy_lock_key_hand_offs`, in place of refusing the run at step 2. The policy's snapshots are not locked until a run after the key is registered sets it. It also lets an edge rebuild go on whose inventory declares a lock key. The roles set it `false` in their vars, which outrank group_vars and host_vars, so a value in the inventory has no effect; give it with `-e`. Default `false` ([A consent](../explanation/refusals-and-guards.md#a-consent)). |
| `qumulo_api_host_after` | `cluster_baseline`: the network step | The address to connect to after a change that drops `qumulo_api_host`. |
| `qumulo_rebuild` | `edge_rebuild` replay; `edge_cache_resize` on its rebuild route | `true` declares that this edge is being rebuilt. The replay refuses without it. |
| `qumulo_new_cluster_uuid` | `edge_rebuild` replay | The new UUID of an edge formed by hand. |
| `edge_rebuild_phase` | `edge_rebuild` | `predestroy`, `replay` or `all`. `edge_predestroy.yml` and `edge_rebuild.yml` set the first two; `-e edge_rebuild_phase=all` runs both with the reinstall tasks between. |
| `edge_rebuild_allow_data_loss` | `edge_rebuild` | When the drain fails, force the deletion of the portal on both sides, losing the edge's writes the hub has not received. Default `false`. |
| `qumulo_replication_release` | `cluster_baseline` step 21; `edge_license_complete.yml`; `replication_pair` | Entries that lift a replication refusal for this run ([Replication](#replication)). The edge rebuild's replay refuses it. |
| `qumulo_replication_restore` | `replication_restore.yml`; `edge_license_complete.yml`; `replication_pair` | The relationships a restore run restores, each `<target host>:<target path>`. Without it the run restores every one it can. The edge rebuild's replay refuses it. |
| `qumulo_adopt` | `capture.yml` | `true` also writes `host_vars/<host>.yml` beside the inventory source that defines the host. Default `false`. |
| `qumulo_capture_dir` | `capture.yml` | Where the capture goes, `<qumulo_capture_dir>/<host>/host_vars.captured.yml`. Default a `captures` directory beside the inventory source that defines the host. |
| `clone_edge_source`, `clone_edge_target`, `clone_edge_name` | `clone_edge.yml` | Required. The cluster to clone, the fresh cluster's inventory host, and the clone's new name, which is also its computer account in the domain. |
| `clone_edge_supply` | `clone_edge.yml` | The secrets the first pass cannot do without. Default `{}`. |
| `clone_edge_check_only` | `clone_edge.yml` | Write the file, then run the baseline in check mode. Default `false`. |
| `clone_edge_allow_configured_target` | `clone_edge.yml` | Clone onto a target that holds configuration. Default `false`. |
| `clone_edge_host_vars_dir` | `clone_edge.yml` | The absolute path to write `<target>.yml` in. Default `host_vars/` beside the inventory source that defines the target. |
| `clone_edge_source_api_host`, `clone_edge_source_api_port`, `clone_edge_source_api_username`, `clone_edge_source_api_password`, `clone_edge_source_api_token`, `clone_edge_source_validate_certs`, `clone_edge_source_ca_path`, `clone_edge_source_cluster_uuid` | `clone_edge.yml` | A source outside the inventory, read as the `qumulo_*` connection variables are. |

[What capture and clone read and write](capture-and-clone.md) describes `capture.yml` and `clone_edge.yml`. [Adopt a cluster](../how-to/adopt-a-cluster.md) and [Clone an edge](../how-to/clone-edge.md) use them.

## Replication failover variables

The `replication_failover` role and `playbooks/replication_failover.yml` read these, besides the inventory's
`qumulo_cluster_uuid`, `qumulo_replication_address`, `qumulo_replication_port` and `qumulo_replication_relationships`
of the source host and its targets, and the source host's `qumulo_previous_cluster_uuids`. The role sets the three
consents in its own `vars/main.yml`, so a value in `group_vars`, `host_vars`, a play's `vars` or `vars_files` is
ignored. Anything a playbook sets closer to the role or during the run lifts them: `-e`, the include's `vars`, a block's
`vars`, role parameters, `set_fact` and `include_vars`.
`ansible-doc -t role qumulo.cluster.replication_failover` lists the same variables.
[Fail over to a replica, and fail back](../how-to/fail-over-replication.md) uses them.

| Variable | Default | What it does |
|---|---|---|
| `replication_failover_action` | none, required | `failover`, `resync` or `failback`. |
| `replication_failover_relationships` | none, required | The relationships to act on, each `<target host>:<target path>` of a `present` entry of the source host's `qumulo_replication_relationships`, compared without a trailing slash. |
| `replication_failover_allow_data_loss` | `false` | The run's consent. Every real run with a step left needs it. For `failover` it accepts that the target's copy starts from the recovery point, that writes on the source after it stay behind, and that a job the source was running when it stopped answering is discarded. For `resync` it accepts that the source's directory becomes read-only under its clients. For `failback` it accepts that the target's directory becomes read-only under its clients and that the source's copy starts from the last recovery point. A check run needs no consent. |
| `replication_failover_allow_newer_writes` | `[]` | Inventory hosts whose directory a reconnect may send back to the recovery point although the comparison found writes there after it. A reconnect is sent to the source host in a resync and to the target host in a failback. A reconnect to a host not named compares first, and fails, naming the writes, only when it finds any. A string, as `-e replication_failover_allow_newer_writes=edge01` gives it, is split at commas into host names, and a name matches a whole host name only. |
| `replication_failover_allow_partial_job` | `false` | Let a failover discard the changes of a job that the source, which answers, is running. |
| `replication_failover_wait_timeout` | `1800` | Seconds each wait for a record's state may take. |
| `replication_failover_sync_timeout` | `86400` | Seconds a failback's wait for a current recovery point may take, for every named relationship together. |
| `replication_failover_source_timeout` | `30` | Seconds a failover's read of the source waits, as `retry_timeout` and `request_timeout`, before it goes on without the source. In any action, a source that did not answer is not read again in the run. |
| `replication_failover_artifact_dir` | `edge_rebuild_artifact_dir`, else the `edge_rebuild` role's default | Where a rebuilt source's restore keeps its records, `<dir>/<source host>/restore/`. A resync or failback of a relationship whose restore record is there is refused, naming `playbooks/replication_restore.yml`; a failover notes the record and goes on. |
| `replication_failover_connections` | none, required | The connection options of the source host and of each target host, by inventory host, as `cluster_baseline_connections` holds them. The playbook fills it. |
| `replication_failover_hosts` | `qumulo_clusters` | The playbook's hosts. The play refuses a run that names more than one. |

## Other roles' variables

The `edge_rebuild` and `edge_cache_resize` roles read the same `qumulo_*` variables and some of their own.
`ansible-doc -t role qumulo.cluster.edge_rebuild` and `qumulo.cluster.edge_cache_resize` list every variable.
[Rebuild an edge](../how-to/edge-rebuild.md) uses them.

| Variable | Role | Default | What it does |
|---|---|---|---|
| `edge_rebuild_eula_accepted` | `edge_rebuild` | none | Accepts the end user license agreement a new formation needs. Required for the replay. |
| `edge_rebuild_validate_factory_certs` | `edge_rebuild` | `true` | `false` connects to the new edge without verifying its factory certificate until the certificate step. Read from the edge's `host_vars`. |
| `edge_rebuild_admin_password` | `edge_rebuild` | `qumulo_api_password` | The new edge's `admin` password. |
| `edge_rebuild_acknowledge` | `edge_rebuild` | `[]` | Areas of state the replay cannot create again to rebuild without, such as `local_data`. The report lists what each needs by hand. |
| `edge_rebuild_old_edge_destroyed` | `edge_rebuild` | `false` | Declares that the old edge is gone, for a replay with `qumulo_ad` that finds no predestroy record of a domain leave. |
| `edge_rebuild_ignore_drift` | `edge_rebuild` | `false` | Rebuild although the edge holds state the inventory does not declare, tenant networks among it; it is lost. It also accepts a tenant 1 the rebuilt edge gains, and a network the old edge holds in no tenant that the rebuilt edge puts in tenant 1. |
| `edge_rebuild_reinstall_tasks` | `edge_rebuild`, `edge_cache_resize` | none | The absolute path of a tasks file that reinstalls the edge, for a rebuild in one run. |
| `edge_cache_resize_margin_bytes` | `edge_cache_resize` | 10% of the target clamp | How close the cache must be to the clamp to count as that size. |
| `edge_cache_resize_allow_rebuild` | `edge_cache_resize` | `false` | Rebuild the edge when a smaller cache needs it, instead of failing with the plan. |

The `portal_pair` role reads no `qumulo_*` variable: `cluster_baseline` and `edge_rebuild` build its arguments from
`qumulo_portal` and `qumulo_portal_hosts`. `cluster_baseline` builds the arguments of the `replication_pair` role from
`qumulo_replication_relationships` and the target host's variables. [Playbooks and roles](playbooks-and-roles.md)
lists the roles and the playbooks that run them.

## Upgrade variables

The upgrade playbook reads `qumulo_upgrade_*` variables of its own, such as `qumulo_upgrade_target` and
`qumulo_upgrade_images`, and the `qumulo_cluster_kind` the Terraform plugin sets. [The upgrade](upgrade.md) lists them.
