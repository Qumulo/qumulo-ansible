<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->
# cluster_baseline

Brings a Qumulo cluster to the baseline its inventory declares: the tenants, the network document, DNS, time, the cluster's name, the TLS certificate and cipher suites, cloud monitoring and Nexus, the audit log destinations, the license check, the capacity clamp and eviction threshold, local groups and users, the Active Directory join, role members, the portal to a hub, directories, SMB and NFS settings, each tenant's SMB and NFS settings, shares and exports, the S3 server, buckets, bucket policies and access keys, the FTP server settings, quotas and snapshot policies. Each area is applied only when its variable is set; an unset area is left alone. A second run changes nothing, apart from local passwords declared with `update_password: always`. An S3 key is made by the run that finds none meeting its entry; set the entry's `access_key_id` to the new id before the next run, since an entry that still names the old id then fails, naming the key the user holds.

## Order

Before step 1, the role's argument specification refuses each key that a variable does not take, as "What it refuses" says. The steps then run in the order the server's own rules need:

1. `qumulo.cluster.cluster_guard`, unless `cluster_baseline_guard: false`, as when a rebuild has just checked the cluster itself.
2. An ownership check, which fails when a declared area belongs to Terraform, or a declared CloudWatch audit destination does while `qumulo_terraform_owned_areas` lists `audit.cloudwatch`, unless `qumulo_take_over_from_terraform` hands the area back, and a check that no declared quota, snapshot policy or S3 bucket lies under a spoke root of `qumulo_portal`, since the cluster refuses the first two there (G-SPOKE-PATHS) and a bucket there would serve the hub's data, and a check that no path has both a mode in `qumulo_directories` and an ACL in `qumulo_file_acls` (G-FS-ACL-MODE); and a check that the file system key store holds each `lock_key` that `qumulo_snapshot_policies` declares, which reads the key store once and, unless `-e qumulo_allow_deferred_policy_lock_keys=true` is given, refuses the whole run, so that it writes nothing it would have to finish later; and a check that no keytab is declared to install beside a domain join (G-KRB-AD), which a `qumulo_kerberos.keytab_state` of `absent` satisfies; all five fail before anything is written. The ownership check also finds the keys inside `qumulo_tenants`: a tenant's `networks` is in the area `network`, its networks' `floating_ip_ranges` in `network.floating_ips`, its `smb_settings` and `smb_shares` in `smb`, and its `nfs_settings` and `nfs_exports` in `nfs`. `tenants` covers `qumulo_tenants`, and each entry of `qumulo_smb_shares` and `qumulo_nfs_exports` that names its tenants with `tenants` or `tenant_id`. `qumulo_take_over_from_terraform` hands back a tenant network's floating ranges, but not a tenant's `networks` while `network` is Terraform's.

   Then, whenever the inventory declares `qumulo_tenants`, `qumulo_network`, `qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_nfs_settings` or `qumulo_factory_defaults`, step 2 reads the cluster once and plans steps 3a to 20a with `tenancy_plan`: the tenants with `tenant_info`, the network document with `network_info`, and its status only when the cluster has two or more tenants or the run creates one; the shares with `smb_info`; the exports, the NFS settings and each tenant's NFS settings with `nfs_info`; and the protection type with `cluster_info` when `qumulo_tenants` is declared and `qumulo_cluster_kind` is not. Every such run makes these reads, so the user the play connects as needs read access to each, `PRIVILEGE_TENANT_READ` for the tenant list included, even for an inventory written before tenants. The plan refuses, before any write, what the inventory breaks and any tenant write or network change that would cut the play off from the cluster; "What it refuses" lists them. When step 3a will write and step 3's document differs from the cluster's, `network_config` checks that document with the cluster in check mode first. With two or more tenants once the run is done, while Terraform owns `network` or `network.floating_ips`, the role warns that a Terraform apply that sends the networks without their tenants unbinds them.
3. The tenants and the network document, in three parts, so that every service turned off comes after every network is bound:
   - 3a. `qumulo_tenants` with `tenant`: the renames, then the services turned on, then the creates. The first create of a second tenant rebinds every listener on every node and ends NFS and replication connections, and every create removes the static routes of every node; the module warns of both.
   - 3\. The network document the plan built from the tenants' `networks` and `qumulo_network`, with `network_config`, when the plan has one to apply; DNS, time and the AD join depend on addressing.
   - 3b. `qumulo_tenants` with `tenant`: the services turned off.
4. `qumulo_dns` with `dns_settings`, clearing the DNS cache with `dns_cache_clear` when the servers changed, and its `lookup_overrides` with `dns_lookup_overrides`; then `qumulo_authoritative_dns` with `authoritative_dns`, after step 3 because the server answers with the floating addresses that step sets; then the source and NTP servers of `qumulo_time` with `time_settings`. The AD join needs DNS and time, and the lookup overrides can name its domain controllers.
5. `qumulo_cluster_name` with `cluster_settings`; the name becomes the AD computer account, and the server refuses a rename while joined. Then `qumulo_filesystem_settings` with `filesystem_settings`, and `qumulo_web_ui_settings` with `web_ui_settings`.
6. `qumulo_ssl` with `ssl_certificate`, before anything that verifies the new certificate, then `qumulo_ssl_ciphers` with `ssl_ciphers`.
7. `qumulo_monitoring` with `monitoring_settings`, its `nexus` with `nexus_connection`, then `nexus_registration` when the Nexus connection is on and there is a join key, or `qumulo_nexus_api_token` to mint one. Nexus sees the cluster's UUID once it is registered, and no other monitoring write runs between the registration and its result. Then `qumulo_audit_destinations` with `audit_destination`, each entry in turn.
8. Qumulo issues the license for the cluster's UUID in Nexus, outside the role; it needs the registration. The collection never creates a license. How Qumulo delivers the license document to a customer is not yet defined (G-LICENSE); the document goes in `qumulo_capacity.license_document`, encrypted with ansible-vault.
9. `qumulo.cluster.license`, whenever `qumulo_capacity` is declared, in `tasks/license.yml`, which `playbooks/edge_license_complete.yml` runs too: it applies `qumulo_capacity.license_document` when the document is for the cluster's UUID, and otherwise reads the license. Nothing waits for a license. Without an active license the role hands it off, naming the cluster's UUID, where the document goes and what to run next. On an edge that declares `qumulo_capacity.license: none`, which runs without a license, it hands nothing off and says the edge runs without a license; its first task refuses `none` beside a license document, before anything applies the document.
10. `qumulo_capacity.clamp` with `capacity_clamp`. Its write re-forms quorum, which is also when a new license's capacity cap applies, so while a license is expected and not active the clamp waits for it, and the role says so (G-CLAMP-BOUNCE); it still plans the clamp, only to read the stored one, and when a clamp is set already that the declared one would not raise, the hand-off says the license's cap applies at the next quorum formation after the license arrives. A `license: none` edge's clamp is written with no license, and limits a license that arrives later; the warning before the write then leaves out the license's cap. With a license, the clamp is planned in check mode first, and the role fails before the write when the clamp would change and the license's cap is below it. When the license this run applied raises the cap and the clamp did not change, the role says that the cap waits for the next quorum formation. On an edge with a declared clamp, it also says so for a license held already whose cap waits on a file system still the size an edge formed without a license has, as `qumulo.cluster.license` reports it in `cap_waits_for_formation`; a `none` edge that holds a license gets its stale-`none` warning instead. The second pass, once the document is in the inventory or the cluster holds the license, applies the license and then sets the clamp: `playbooks/edge_license_complete.yml` on an edge, or the role again on any cluster; both run this same step.
11. `qumulo_capacity.free_threshold` with `portal_eviction_settings`.
12. The encryption key store with `encryption_key_store`, which is not in this release.
13. `qumulo_local_groups` without members, then `qumulo_local_users`, then the groups' members; a primary group must exist before its users.
14. The keytab of `qumulo_kerberos` with `kerberos_keytab`, before the join, which the cluster refuses while it holds a keytab (G-KRB-AD); a keytab to install beside `qumulo_ad` fails the role before its first write, and `keytab_state: absent` deletes the keytab before the join. Then `qumulo_ad` with `ad_domain`, then `qumulo_time.use_ad_for_primary`, then the setting of `qumulo_kerberos` with `kerberos_settings`, then `qumulo_user_defined_mappings` with `user_defined_mappings` and `qumulo_identity_attributes` with `identity_attributes`, which need the join, with `auth_cache_clear` after either changed, even when a later step of the two fails (after a run whose clear failed, run `auth_cache_clear` by hand), then `qumulo_saml` with `saml_settings`.
15. `qumulo_roles` with `role`, then `qumulo_role_members` with `role_member`, then the roles declared absent, whose members the inventory removes in `qumulo_role_members`, since the cluster refuses to delete a role that still has members; `ad:` trustees resolve only after the join, except `ad:NT AUTHORITY\Authenticated Users`, which resolves joined to a domain or not, and deleting a user drops its role assignments.
16. `qumulo_portal` with the `portal_pair` role, this cluster as the spoke and the inventory host `qumulo_portal.hub` as the hub: one Qumulo Core version on both, the spoke portal and its roots, the hub's accept and authorization, and a wait until the portal is ACTIVE.
17. `qumulo_directories` with `directory`, then `qumulo_file_acls` with `file_acl`; paths may lie under the portal's authorized roots.
18. SMB, from the plan: first the shares the run removes, those declared `state: absent`, a deleted tenant's, and the factory `Files` share when `qumulo_factory_defaults.smb_files_share` is `absent`; then `qumulo_smb_settings` with `smb_settings`; then each tenant's `smb_settings`, or their removal for `inherit`; then the shares, each in the tenants its entry names, with `smb_share`.
19. NFS, in the same order: the exports the run removes, the factory `/` export when `qumulo_factory_defaults.nfs_root_export` is `absent` among them; then `qumulo_nfs_settings`; then each tenant's `nfs_settings`; then the exports. NFSv4.1 cannot be on for a tenant while two of its export paths nest, so the removals go first, and a run can turn it on while it removes the nested exports. Then S3: `qumulo_s3_settings` with `s3_settings`, `qumulo_s3_buckets` with `s3_bucket` and `qumulo_s3_bucket_policies` with `s3_bucket_policy`. Buckets and the base path need the directories of step 17, and policies the users and the domain join of steps 13 to 15. Then the FTP server: `qumulo_ftp_settings` with `ftp_settings`, after the directories and ACLs of step 17, so that a new cluster serves nothing over FTP before they are in place, and after the local users of step 13, one of which the anonymous user names.
20. `qumulo_quotas` with `directory_quota`, then `qumulo_snapshot_policies` with `snapshot_policy`, on local paths only; step 2 has already refused one under a spoke root. The user the play connects as needs `PRIVILEGE_FS_KEY_MANAGEMENT_READ` when a policy declares a `lock_key`. Under `qumulo_allow_deferred_policy_lock_keys`, a policy whose key the store does not hold is applied without it and listed in `cluster_baseline_policy_lock_key_hand_offs`, for a later run to set. `snapshot_policy` refuses a disabled key there, unless the policy already holds it, so enable it before the run.
    - 20a. Each tenant declared `state: absent`: its SMB and NFS settings return to the global ones, whether or not the inventory declares them, then `tenant` deletes it. Step 2 refuses the delete of a tenant that holds a network, share or export the run does not remove, so its entry declares each share and export the tenant holds, or a shared entry lists the tenant, and its networks are left out of the inventory or listed under a tenant that stays. Steps 3, 18 and 19 then remove them, and the entry's `state: absent` deletes the tenant.
21. `qumulo_replication_relationships` with the `replication_pair` role. A relationship from a rebuilt source is held while its replica may be the only copy of the source's data, and its refusal names the source's restore when the holder allows one, or the copy back and the release. Before each relationship that is not `state: absent`, the role reads the target's tenants, and with two or more tenants its network document, to check that the tenant serving the target's `qumulo_replication_address` has replication on, so the user the play connects to the target as needs `PRIVILEGE_TENANT_READ`, and with two or more tenants `PRIVILEGE_NETWORK_READ`. A check run takes that tenant's switch from the target host's own steps 3a and 3b when the target host is in the play.
22. `qumulo_s3_access_keys` with `s3_access_key`, the removals first, last of all, so that no later step's failure can stop it once a key is made; it fails after the last entry, naming each that failed.

When a step moves the connection, the role switches the play's facts for the steps after it: `qumulo_api_host_after` replaces `qumulo_api_host` after the network step, `qumulo_ssl.ca_path_after` replaces `qumulo_ca_path` and turns `qumulo_validate_certs` on after the certificate step, and the new password of the user the play connects as replaces `qumulo_api_password`. A check run switches none of them for a change it only predicts.

In a check run, a tenant that step 3a renames is still under its old name on the cluster, so its shares, exports and settings show as creates under the new name, and its removals as no change.

A rebuild's replay meets a factory certificate that cannot be verified (G-FACTORY-TLS), so it starts with `qumulo_validate_certs: false` and relies on `qumulo_ssl.ca_path_after` to verify the cluster from the certificate step on, the steps that send passwords among them.

## Variables

`meta/argument_specs.yml` is the reference. A variable for an area of a module takes that module's options for each entry, apart from the connection options (`api_host`, `validate_certs`, `request_timeout` and the rest), which the play's `module_defaults` set, and the options the role sets itself: `tenant` and `state` of the SMB and NFS settings, and `declared_access_key_ids` of an S3 access key. The spec refuses a key it does not list; see "What it refuses".

| Variable | Default | Meaning |
|---|---|---|
| `cluster_baseline_guard` | `true` | run `cluster_guard` first, checking the UUID and waiting until the cluster is ready |
| `cluster_baseline_replication_on_held` | `fail` | `report` keeps a held relationship as a result and writes nothing, as the rebuild's replay does, instead of failing step 21 |
| `qumulo_managed_by` | `none` | who formed the cluster; with `terraform_provider`, and no `qumulo_terraform_owned_areas`, the provider owns the floating addresses of a `cnq` cluster, or one of unset kind, and none of an `edge` or `baremetal` one |
| `qumulo_terraform_owned_areas` | `[network.floating_ips]` with `terraform_provider` unless `qumulo_cluster_kind` is `edge` or `baremetal`, else none; a list the inventory gives is all Terraform owns, an empty one included | areas the role refuses to manage: `network`, `network.floating_ips`, `dns`, `dns.fqdn`, `time`, `cluster`, `filesystem`, `web_ui`, `ssl`, `monitoring`, `audit`, `audit.cloudwatch`, `capacity`, `local_users`, `local_groups`, `ad`, `kerberos`, `saml`, `identity_mappings`, `roles`, `portal`, `directories`, `smb`, `nfs`, `tenants`, `s3`, `ftp`, `quotas`, `snapshots`, `replication` |
| `qumulo_take_over_from_terraform` | `false` | manage the floating addresses and the authoritative DNS `enabled` and `fqdn` although Terraform owns them, and the audit destinations although `audit` or `audit.cloudwatch` is Terraform's; one flag for both |
| `qumulo_allow_management_disruption` | `false` | allow a step that moves the connection |
| `qumulo_allow_deferred_policy_lock_keys` | `false` | apply a snapshot policy whose `lock_key` is not in the key store without the key, and hand it off, in place of refusing at step 2; has no effect in group_vars or host_vars, since the role's vars set it `false`: give it with `-e` |
| `qumulo_api_host_after` | unset | the address to connect to after the network step, when it drops `qumulo_api_host` |
| `qumulo_tenants` | unset | the tenants by name, each with `previous_name`, `state`, the six service switches, `networks`, `smb_settings`, `nfs_settings`, `smb_shares` and `nfs_exports`; a tenant the list leaves out stays. Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0; the role manages tenants through it on those releases, and a later Qumulo Core release may change it |
| `qumulo_management_network` | unset | the id of the network the play reaches the cluster on, or a list of ids, such as two for an address with an IPv4 and an IPv6 network; used when two or more tenants remain after a write; declare it for a play behind NAT or a load balancer, or where the network status is unavailable; `0` is a network id |
| `qumulo_cluster_port` | unset | the port the cluster listens on for the play, when a load balancer or tunnel maps the port the play connects to |
| `qumulo_cluster_kind` | unset | `cnq`, `edge` or `baremetal`; step 2 refuses every tenant write to a `cnq` cluster, and with it unset, to a cluster whose protection type is object-backed; with `terraform_provider` and no `qumulo_terraform_owned_areas`, `edge` or `baremetal` leaves the floating addresses to the role, and `cnq` or unset leaves them to Terraform; with `cnq`, a `qumulo_terraform_owned_areas` that leaves out `network.floating_ips` is refused |
| `qumulo_network` | unset | `frontend_networks` and `managed_interfaces`, as `network_config` takes them; a flat network names its tenant with `tenant`, and `tenant_id` is translated to it with a warning |
| `qumulo_dns` | unset | `servers` and `search_domains`, in order, and `lookup_overrides`, the whole table as `dns_lookup_overrides` takes it |
| `qumulo_authoritative_dns` | unset | `enabled`, `fqdn`, `host_restrictions` and `record_ttl_seconds`, as `authoritative_dns` takes them |
| `qumulo_time` | unset | `source`, `ntp_servers` and `use_ad_for_primary` |
| `qumulo_cluster_name` | unset | the cluster's name |
| `qumulo_filesystem_settings` | unset | `permissions_mode`, `atime_enabled`, `atime_granularity` and `notify_recursive_mode` |
| `qumulo_web_ui_settings` | unset | `login_banner` and `inactivity_timeout`, in minutes |
| `qumulo_ssl` | unset | `certificate`, `private_key`, `probe` and `ca_path_after` |
| `qumulo_ssl_ciphers` | unset | `tls_1_2` and `tls_1_3`, each in order of preference, as `ssl_ciphers` takes them |
| `qumulo_local_groups` | unset | groups, each as `local_group` takes it |
| `qumulo_local_users` | unset | users, each as `local_user` takes it |
| `qumulo_ad` | unset | the domain to join and its settings, as `ad_domain` takes them |
| `qumulo_kerberos` | unset | the keytab of a standalone KDC, as `keytab` (base64, vaulted) or `keytab_path` (an absolute path to a plain file on the controller) with `keytab_state` (`absent` deletes it), as `kerberos_keytab` takes them, and `use_alt_security_identities_mapping`, as `kerberos_settings` takes it; a keytab to install is refused beside `qumulo_ad` |
| `qumulo_user_defined_mappings` | unset | every mapping from an AD user to an LDAP user, as `user_defined_mappings` takes them |
| `qumulo_identity_attributes` | unset | home directories of AD, LDAP and POSIX identities, each as `identity_attributes` takes it |
| `qumulo_saml` | unset | SAML sign-in, as `saml_settings` takes it |
| `qumulo_roles` | unset | custom roles, each as `role` takes it |
| `qumulo_role_members` | unset | role assignments, each as `role_member` takes them |
| `qumulo_monitoring` | unset | `monitoring_settings`' options, `nexus` with `nexus_connection`'s, and `nexus_join_key` |
| `qumulo_nexus_api_token` | unset | a Nexus user's API token, which mints a join key |
| `qumulo_audit_destinations` | unset | audit log destinations, each as `audit_destination` takes it; one the list leaves out is left alone |
| `qumulo_capacity` | unset | `license_document`, the license document issued for the cluster's UUID, vaulted; `license` (`pending`, the default, or `required`); `clamp` and `free_threshold` |
| `qumulo_portal` | unset | `hub`, the hub's inventory host; `type`; `roots`, each a `spoke_root` and a `hub_root` |
| `qumulo_portal_hosts` | unset | the IP addresses the other side of a portal targets: the floating IPs, or for a multi-AZ cluster in AWS the load balancer's IPs, as Terraform outputs them, or the node IPs of a cluster without floating IPs; required on the spoke and on its hub, and always declared, never derived from the cluster's network status; behind NAT, making them reachable is yours to do |
| `qumulo_factory_defaults` | unset | `smb_files_share` and `nfs_root_export`, each `present` or `absent` |
| `qumulo_directories` | unset | directories, each as `directory` takes it |
| `qumulo_file_acls` | unset | ACLs, each as `file_acl` takes it; a path here declares no `mode` in `qumulo_directories` |
| `qumulo_smb_settings`, `qumulo_smb_shares` | unset | as `smb_settings` and `smb_share` take them, without the settings' `tenant` and `state`; step 2 refuses a share entry's `tenant`, and a share entry's `tenants`, a list of names or `all`, gives the tenants it is in, and is needed when the cluster, with the tenants the run creates, holds two or more |
| `qumulo_nfs_settings`, `qumulo_nfs_exports` | unset | as `nfs_settings` and `nfs_export` take them, without the settings' `tenant` and `state`; an export entry takes `tenants`, and not `tenant`, as a share entry does |
| `qumulo_s3_settings`, `qumulo_s3_buckets`, `qumulo_s3_bucket_policies`, `qumulo_s3_access_keys` | unset | as `s3_settings`, `s3_bucket`, `s3_bucket_policy` and `s3_access_key` take them, without `declared_access_key_ids`, which the role sets |
| `qumulo_ftp_settings` | unset | as `ftp_settings` takes its options; an unset option keeps the cluster's value |
| `cluster_baseline_artifact_dir` | `artifacts` beside the inventory source that defines the host | where each new S3 key's secret is written, under `<host>/secrets/` |
| `qumulo_quotas`, `qumulo_snapshot_policies` | unset | as `directory_quota` and `snapshot_policy` take them |

When `qumulo_capacity` is declared the role sets `cluster_baseline_license_pending`, `cluster_baseline_clamp_deferred`, `cluster_baseline_needs_quorum_bounce`, `cluster_baseline_license_hand_off` (the hand-off text, empty when the license is active) and `cluster_baseline_license_missing` (true when `license: required` and the cluster still has no active license), for a report of what is left to do; the `edge_rebuild` role's report reads them. `cluster_baseline_license_pending` and `cluster_baseline_clamp_deferred` are false for `license: none`. No value of `license` makes the role wait or fail. A play that must end with the license in place checks `cluster_baseline_license_missing` itself, after the second pass:

```yaml
- name: Require the license once the second pass has run
  ansible.builtin.assert:
    that: not cluster_baseline_license_missing | bool
    fail_msg: "{{ cluster_baseline_license_hand_off }}"
```

When step 20 applies a snapshot policy without its `lock_key` under `qumulo_allow_deferred_policy_lock_keys`, the role sets `cluster_baseline_policy_lock_key_hand_offs`, a list with one line for each such policy that says it does not lock its snapshots with that key and names what to do; the `edge_rebuild` role's report reads it. It is empty when no policy was deferred, and a run after the keys are registered sets each key.

When step 22 makes a key, because no key meets an entry of `qumulo_s3_access_keys`, as on a rebuilt cluster, the cluster returns its secret that once (G-S3-KEY). The module writes it at once to `<cluster_baseline_artifact_dir>/<host>/secrets/s3-<user>-<access_key_id>.yml`, in a directory and a file readable only by their owner. The role prints each new key's user and id with that file's path, and lists them, without the secret, in `cluster_baseline_s3_access_keys`. Give each secret to the key's consumers, keep or delete the file as your secret store needs, and set the entry's `access_key_id` to the new id before the next run. The step passes each entry the ids its user's other entries declare, so a rebuild makes each of a user's declared keys again, and the run after it, before the inventory names the new keys, makes no more. Without an artifact directory, as for a host from no inventory file, an entry that would make a key fails; set `cluster_baseline_artifact_dir`.

## What it refuses

The role fails, before it writes, rather than make these changes:

- Before step 1, a key that a variable's options do not list, such as a misspelt `session_encryption` in `qumulo_smb_settings`, or a connection option such as `request_timeout` in an entry of `qumulo_smb_shares`. Ansible stops with `Validation of arguments failed:` and a line naming the dotted path to the key, `qumulo_smb_settings.sesion_encryption. Supported parameters include: ...`, which lists the keys taken where the key sits; with keys in more than one place, the list is for one of them. For a list it does not say which entry holds the key. Correct the key, or remove it. A tenant's `smb_settings` and `nfs_settings` go in its entry of `qumulo_tenants`, so `tenant` and `state` are refused in `qumulo_smb_settings` and `qumulo_nfs_settings`, and so is `declared_access_key_ids` in an entry of `qumulo_s3_access_keys`. A dict whose options the spec leaves open takes any key: `qumulo_network.managed_interfaces`, which `network_config` sends as the document's interfaces, `qumulo_s3_bucket_policies[].policy`, `qumulo_capacity.license_document` and `qumulo_tenants[].identity`, which step 2 refuses.
- At step 2, a key in a tenant's `smb_settings` or `nfs_settings` dict that the global `qumulo_smb_settings` or `qumulo_nfs_settings` does not take, as in `qumulo_tenants[0] (Default) sets smb_settings.sesion_encryption, which smb_settings does not take; did you mean session_encryption?`. A key with no near match gets `it takes <the keys>.` instead. `state` is refused with `write smb_settings: inherit` as the way to give the tenant the global settings, and `tenant` with `leave tenant out`, since the entry names the tenant. The key is refused even under a tenant declared `absent`; otherwise the value is ignored there. The `edge_rebuild` preflight runs the same check before it drains the edge, with the message `the replay onto the rebuilt edge would refuse the inventory: ...`.
- A `qumulo_terraform_owned_areas` that leaves out `network.floating_ips` on a provider-formed cluster of kind `cnq`, as the Terraform inventory plugin refuses it. Add the area, or set `qumulo_managed_by: none`.
- A declared area that `qumulo_terraform_owned_areas` gives to Terraform, or, with no such list, on a provider-formed cluster of kind `cnq` or unset, the floating addresses. Remove the variable or the key the message names, or the area from the list when there is one. With the kind unset, the message also offers declaring an edge or bare-metal cluster's kind. This covers the keys of a tenant entry in the areas step 2 names.
- At step 2, from `tenancy_plan`, anything the inventory's tenants, networks, shares and exports break, with a message that names the entries: among them two entries of one tenant, a rename still to be done onto a name the cluster holds, `identity` on a tenant, a share or export entry with neither `tenants` nor `tenant_id`, or a new network without `tenant`, when the cluster, with the tenants the run creates, holds two or more, a tenant declared absent that holds a share, export or network nothing declares, a run that would leave no tenant, and NFSv4.1 on for a tenant whose export paths nest.
- At step 2, a tenant write or network change after which the network the play reaches the cluster on would belong to a tenant with REST off, or the management network cannot be determined while it is needed; each message gives its remedy, such as `qumulo_management_network` or `qumulo_cluster_port`.
- At step 2, every tenant write to a cloud cluster, which no cloud cluster supports; a cluster on premises whose storage is object-backed declares `qumulo_cluster_kind: baremetal`.
- At step 2, a network document for step 3 that the cluster's validation refuses, when step 3a would write first.
- At step 2, before anything is written, a `qumulo_snapshot_policies` entry whose `lock_key` is not in the file system key store, in a check run too, unless `-e qumulo_allow_deferred_policy_lock_keys=true` is given. Register the key with `security_key`, or pass `-e qumulo_allow_deferred_policy_lock_keys=true`, which applies the policy without the key and lists it in `cluster_baseline_policy_lock_key_hand_offs`.
- At step 21, on every run and an established relationship included, a relationship whose target address, the target host's `qumulo_replication_address`, is served by a tenant with `replication_enabled` false. Set `replication_enabled: true` in that tenant's entry of the target host's `qumulo_tenants`, or set `qumulo_replication_address` to an address on a network of a tenant that has it on.
- Any entry of `qumulo_audit_destinations` while `qumulo_terraform_owned_areas` lists `audit`, and a CloudWatch entry while it lists `audit.cloudwatch`, unless `qumulo_take_over_from_terraform` is set, which also hands the floating addresses back. An entry without a type that would write an existing CloudWatch destination is refused by `audit_destination` at step 7, before its own write.
- A network document that drops the address in `qumulo_api_host`, when that is an IP address the current ranges hold, at step 2, unless `qumulo_allow_management_disruption` is set and `qumulo_api_host_after` names the address later tasks connect to.
- A certificate the pinned CA would not verify, unless `qumulo_allow_management_disruption` is set or `qumulo_ssl.ca_path_after` names the new chain's authority.
- Floating ranges, `floating_ip_ranges` in `qumulo_network`, that Terraform owns: `qumulo_terraform_owned_areas` names `network.floating_ips`, or it is not given and the cluster is a provider-formed one of kind `cnq` or unset, since the provider applies its own floating addresses there. A tenant's `networks` with floating ranges are refused by the area bullet above. The role refuses them before step 2 reads the cluster, so before any write, and the message names where each range is declared and the owner. Remove the ranges, or `network.floating_ips` from the list when one is given, or set `qumulo_take_over_from_terraform`, which hands the role the audit destinations, `dns.fqdn` and the floating addresses together.
- A rename while the cluster is joined to a domain.
- A new password for `admin` without `allow_admin_password_change`, since it also becomes every node's console and SSH password (G-ADMIN-OS).
- A join while the cluster is joined to another domain, or a change of `ou` or `domain_netbios` on a joined cluster; leave first.
- `purge` on the `Qumulo-Support` role.
- A clamp below the one the cluster stores, since provisioned capacity never shrinks; the `edge_cache_resize` role chooses the route to a smaller cache.
- A clamp that would change and that the license's capacity cap is below, since its write would re-form quorum for nothing.
- A portal between clusters on different Qumulo Core versions, and a quota, snapshot policy or S3 bucket under a spoke root, which it refuses before its first write.
- An S3 bucket moved to another directory (G-S3-PATH), or with versioning or its lock turned off (G-S3-LOCK).

A refusal at step 2 from `tenancy_plan` fails with the messages, one to a line, in `msg`, and `refusals`. Only at verbosity 3 or more (`-vvv`) does it add `plan`, what the module had computed, which after a refusal of the continuity rule holds the network document and its address ranges.

## Example

```yaml
- name: Bring the edge to its baseline
  hosts: qumulo_clusters
  gather_facts: false
  module_defaults:
    group/qumulo.cluster.qumulo:
      api_host: "{{ qumulo_api_host }}"
      api_password: "{{ qumulo_api_password }}"
      validate_certs: "{{ qumulo_validate_certs | default(true) }}"
      ca_path: "{{ qumulo_ca_path | default(omit) }}"
      expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"
  roles:
    - role: qumulo.cluster.cluster_baseline
      vars:
        qumulo_cluster_name: edge01
        qumulo_dns:
          servers: [10.0.0.2, 10.0.0.3]
          search_domains: [corp.example.com]
        qumulo_time:
          source: API_TIME_SOURCE_NTP
          ntp_servers: [ntp.corp.example.com]
        qumulo_local_groups:
          - name: backup
        qumulo_local_users:
          - name: svc-backup
            primary_group: backup
            password: "{{ vault_svc_backup_password }}"
        qumulo_role_members:
          - role: Observers
            members: ['local:svc-backup']
```

Run it with `--check --diff` first. `playbooks/baseline.yml` runs the role against every host in `qumulo_clusters`, with the variables from the inventory.

## Requirements

Qumulo Core 7.10.3 or later and ansible-core 2.20 or later. The behaviours the modules work around are recorded in `docs/reference/gaps.md` and `docs/reference/quirks.md`.
