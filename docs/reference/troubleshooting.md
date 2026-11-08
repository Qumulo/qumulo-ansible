<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Troubleshooting

The messages a run can stop with, grouped by what went wrong, with the cause and the fix for each. The quoted text is
what the modules and roles print; `...` stands for the parts that name your hosts, paths and values.

Most stops are refusals: the collection stops on purpose, before the write it guards, and says what would lift it.
[Respond to a refusal](../how-to/respond-to-a-refusal.md) gives the procedure, and
[Refusals and guards](../explanation/refusals-and-guards.md) gives the reasons.

| Symptom | Section |
|---|---|
| The run cannot reach the cluster, or the login or certificate fails | [Connecting](#connecting) |
| A write failed with "may or may not have been applied", or with HTTP 412 | [A change that may or may not have been applied](#a-change-that-may-or-may-not-have-been-applied) |
| A message names a flag, such as `allow_decrease` or `recreate` | [Refusals that wait for consent](#refusals-that-wait-for-consent) |
| A replication relationship cannot be authorized, or is held | [Refusals that wait for a release](#refusals-that-wait-for-a-release) |
| The inventory declares something the cluster or Terraform cannot take | [The inventory and the cluster disagree](#the-inventory-and-the-cluster-disagree) |
| A message about tenants, a network's tenant, a tenant's shares or the play's connection to a multi-tenant cluster | [Tenants](#tenants) |
| A message about a license, the capacity clamp or the edge's cache | [Hand-offs](#hand-offs) |
| A rebuild refuses, or a replay or restore will not run | [Rebuilds](#rebuilds) |
| A failover, resync or failback refuses or stops | [Failing over](#failing-over) |
| The upgrade refuses (F codes) or warns (W codes) | [Upgrades](#upgrades) |
| A capture or adoption refuses | [Adoption](#adoption) |
| A clone refuses | [Cloning](#cloning) |

## Connecting

On a host with `qumulo_cluster_uuid`, the first request is the identity guard's, so the TLS, name and unreachable
messages below start with `identity guard: ` and end with `; no credentials were sent`.

| Message | Cause and fix |
|---|---|
| `identity guard: https://...:8000 is cluster '...' (state ...), expected '...'; no credentials were sent` | The address reaches another cluster than the inventory names, or a node that is not formed (`UNCONFIGURED`). Fix `qumulo_api_host` or `qumulo_cluster_uuid`. |
| `identity guard: cannot read the node state of ... (...); no credentials were sent` | The cluster answered `GET /v1/node/state` with an HTTP error, which the parentheses quote. Check the address and the port. |
| `identity guard: unexpected node-state response from ...; no credentials were sent` | The address answers, but it is not a Qumulo REST API. Check `qumulo_api_host` and the port. |
| `identity guard disabled: expected_cluster_uuid is empty` (a warning) | The host has no `qumulo_cluster_uuid`, so no task checks which cluster it reaches. Add the UUID. |
| `https://...: TLS certificate verification failed (...); set ca_path to the cluster's CA, or validate_certs: false for a lab cluster` | `qumulo_ca_path` does not name the CA that signed the cluster's certificate. A newly formed edge serves a factory certificate nothing can verify ([G-FACTORY-TLS](gaps.md)); see [Replace the factory certificate](../how-to/manage-certificates.md#replace-the-factory-certificate). |
| `https://...: cannot resolve ... (...); check api_host` | The name in `qumulo_api_host` does not resolve on the controller. |
| `https://... unreachable for ...s: ...` | The cluster did not answer within `retry_timeout` (default 300 seconds). Any request is resent after a refused connection. GETs, and writes whose route declares them safe to resend, are also resent after a dropped connection or HTTP 502, 503 or 504. |
| `set exactly one of api_password or api_token for ...` | The module has neither a password nor a token, or has both, and fails before any request. A password looked up from an unset environment variable is empty, and counts as none. |
| `... failed: HTTP 401 ... (check api_username and api_password)` | The login was refused. Check the password, or the vault entry it comes from. |
| `... failed: HTTP 401 ... (api_token was rejected; access tokens are bound to one cluster and do not survive a rebuild)` | The access token is for another cluster, or for an edge before its rebuild ([G-TOKEN](gaps.md)). Use a password, or a token issued by this cluster. |
| `... failed: HTTP 403 ... (the API user lacks the RBAC privilege for this call)` | The user lacks a privilege. The playbooks connect as `admin`. |
| `... (redirected to ...; set api_host to the address that serves the REST API)` | `qumulo_api_host` answers with a redirect. Give the address of the REST API itself. |
| `... (this route does not exist on this Qumulo Core version)` | The cluster runs a version that lacks a route the module needs. The collection is tested against 7.10.3 and later. |
| `... runs Qumulo Core ..., older than 7.10.3, the oldest release qumulo.cluster is tested against` (a warning) | The cluster runs a version below the collection's tested floor. |

A call to Nexus names `nexus_api_token` or `nexus_api_url` in place of the cluster's options:

| Message | Cause and fix |
|---|---|
| `set nexus_api_token, or the QUMULO_NEXUS_API_TOKEN environment variable, to a Nexus user's API token` | The task has no Nexus token. It fails before any request. |
| `nexus_api_url must be an https URL such as ..., not ...; the token is never sent over plain HTTP` | Give `nexus_api_url` as an `https://` URL, or leave it unset for the default. It fails before any request. |
| `Nexus ... failed: HTTP 502: ... (the gateway in front of Nexus, or Nexus itself, failed every try for ...s; retry_timeout sets how long to keep trying)` | Nexus, or the gateway in front of it, answered 502, 503 or 504 to every try within `retry_timeout` (default 300 seconds). Nexus reads, retirements and join-key mints are retried until then; a mint retried this way may leave spare join keys in the Nexus organization, which nothing uses. Run the task again later. |
| `Nexus ... failed: HTTP 401: ... (nexus_api_token was rejected; it may have expired or been revoked)` | Issue a new token. |
| `Nexus ... failed: HTTP 403: ... (nexus_api_token's Nexus role does not allow this call)` | The token's role lacks the permission the call needs. |
| `https://...: TLS certificate verification failed (...); check nexus_api_url` | The controller does not trust the certificate that `nexus_api_url` serves. |
| `cannot resolve the host of ... (...); check nexus_api_url` | The host of `nexus_api_url` does not resolve on the controller. |
| `Nexus at ... unreachable for ...s: ...` | Nexus did not answer within `retry_timeout` (default 300 seconds). |

## A change that may or may not have been applied

A write whose connection drops after the request was sent, or that a gateway answers with HTTP 502 or 504, may or may
not have reached the cluster. The collection never resends such a write. It reads the cluster back instead
([Maybe applied](../explanation/state-management.md#maybe-applied) gives the reasons):

- When the read shows the change took effect, the task succeeds, and its `notes` say `write reconciled after a lost
  connection`.
- When it cannot tell, the task fails with `changed: true` and `maybe_applied: true` in its result, and a message that
  starts with the request and ends with the reason. The last row is the one failure that has `changed: true` alone:

| Message | Meaning |
|---|---|
| `PUT ...: the connection was lost after the request was sent; the change may or may not have been applied` | The connection dropped and the read back failed or was not possible. |
| `...: HTTP 502 from a gateway; the change may or may not have been applied` | A proxy or load balancer answered, perhaps after forwarding the request. The same message with HTTP 504 exists. |
| `...; reading back did not confirm it: ...` | The read back shows the object not yet as declared. |
| `...; ... cannot be read back, so whether the change took effect is unknown` | The change was to a field the cluster never returns, such as a password. |
| `...; reading back shows the clamp is ..., not the requested ... bytes; it was not sent again` | A clamp write was lost. A clamp write re-forms quorum, so the module waited for the cluster before reading. |
| `...; the registration status is ...; a join key is single-use, so set join_key to a new key, or nexus_api_token to mint one` | A Nexus registration was lost, and the key it used is spent ([G-NEXUS-KEY](gaps.md)). |
| `...; this delete was for recreate, so the create was not attempted` | A recreate's delete was lost; the object may be gone and was not created again. |
| `...; the comparison's snapshot of <path> may exist, and expires by itself a day after it was taken. Nothing was sent to ...; run again to compare and reconnect` | A `replication_reverse` reconnect lost the answer to the snapshot it takes to compare the directory with the recovery point. The result has `changed: true` and no `maybe_applied`, since no verb was sent. Run the task again. |

In every case, run the task again. It reads first and writes only what still differs. Forming an edge is the one write
that says so itself: `the connection was lost as the create was sent, so the node may never have received it, and
running the task again is safe`.

`... failed: HTTP 412 ... (the resource changed between read and write; re-run the task)` means another writer changed
the object between the module's read and its write, and the module's retries ran out. Run the task again.

## Refusals that wait for consent

Each of these names the flag that lifts it. The flag belongs to the one run that needs it, after reading what the
message says you lose.

| Message | Flag | What it allows |
|---|---|---|
| `...; lowering the clamp needs allow_decrease=true, and the provisioned capacity does not shrink even then (G-CLAMP-SHRINK)` | `allow_decrease` on [capacity_clamp](../../plugins/modules/capacity_clamp.py) | A lower clamp, which only stops further provisioning. A smaller edge is a rebuild ([Size and resize an edge's cache](../how-to/edge-sizing.md)). |
| `changing the password of admin also changes the console and SSH password of every node (G-ADMIN-OS); set allow_admin_password_change: true to do it` | `allow_admin_password_change` on [local_user](../../plugins/modules/local_user.py) | Changing `admin`'s password. `playbooks/admin_password_rotate.yml` sets it ([Rotate the admin password](../how-to/rotate-admin-password.md)). |
| `..., which would cut this play off from the cluster; set allow_management_disruption: true, and ...` | `allow_management_disruption` on `local_user`, [role_member](../../plugins/modules/role_member.py), [network_config](../../plugins/modules/network_config.py) or [ssl_certificate](../../plugins/modules/ssl_certificate.py); `qumulo_allow_management_disruption` for the baseline | A change that cuts the run's own connection: the address it connects to, the password or role of the user it connects as, or a certificate its CA does not sign. The message's last clause says what the run must do after. |
| `... is DISCONNECTED, so deleting it discards spoke writes the hub has not received; set allow_data_loss=true to force the delete` | `allow_data_loss` on [portal_spoke](../../plugins/modules/portal_spoke.py) or [portal_hub](../../plugins/modules/portal_hub.py); `portal_pair_allow_data_loss` on the role; `edge_rebuild_allow_data_loss` in a rebuild | Deleting a portal that cannot be drained. |
| `make_writable sends the target directory of relationship ... back to its recovery point, discarding whatever was written there after it; set allow_data_loss to allow it` | `allow_data_loss` on [replication_reverse](../../plugins/modules/replication_reverse.py) | Sending the target directory back to the relationship's recovery point. `reverse` needs no consent. A `reconnect` without it compares first and is refused only for writes it finds (`<path> changed since the recovery point ...`); with it the reconnect is sent without comparing. |
| `The <action> above changes which copy of each relationship clients may write, ...`; `<name>: <host>'s <path> changed since the recovery point ...` | `replication_failover_allow_data_loss`; `replication_failover_allow_newer_writes` naming the host; `replication_failover_allow_partial_job` | The failover role's consents, in [Failing over](#failing-over). |
| `... lies in the spoke root ... of the portal to hub ...; the spoke passes the write through to the hub, so it would replace the hub's ACL, ...; declare the ACL on the hub, or set allow_data_loss=true to replace it from here` | `allow_data_loss` on [file_acl](../../plugins/modules/file_acl.py) | Replacing, from the spoke, the ACL the hub holds. |
| `... has ... unfinished upload..., and the cluster removes no bucket with one; set allow_data_loss to abort them and remove the bucket (its objects stay)` | `allow_data_loss` on [s3_bucket](../../plugins/modules/s3_bucket.py) | Aborting the unfinished uploads so the bucket can be removed. |
| `cluster ... is still connected to Nexus (last seen ...); a cluster that still connects is usually in use, so set allow_connected: true to retire it anyway` | `allow_connected` on [nexus_cluster](../../plugins/modules/nexus_cluster.py) | Retiring a cluster that Nexus still sees. |
| `qumulo_snapshot_policies declares lock keys for ... and the key store holds no such key, so the policies' snapshots would not be locked; register each key first with qumulo.cluster.security_key, or pass -e qumulo_allow_deferred_policy_lock_keys=true ...` (the baseline's step 2), or `...: qumulo_snapshot_policies declares lock keys for ..., and the rebuilt edge's key store is empty until each key is registered again ...; pass -e qumulo_allow_deferred_policy_lock_keys=true to the rebuild ...` (the edge rebuild, before the predestroy changes anything) | `qumulo_allow_deferred_policy_lock_keys` on the baseline or the rebuild | Applying each such policy without its key, so its snapshots are not locked until the key is registered and the baseline runs again ([A rebuilt or cloned cluster](../how-to/manage-lock-keys.md#a-rebuilt-or-cloned-cluster)). |
| `...: ... cannot change in place; set recreate=true to delete and recreate it` | `recreate` on [snapshot_policy](../../plugins/modules/snapshot_policy.py) and [replication_relationship](../../plugins/modules/replication_relationship.py) | Deleting and creating the object again. |
| `... holds entries, subdirectories included, and the source's content replaces them: ...; set allow_non_empty_directory (allow_non_empty_target on the relationship's entry) to allow it` | `allow_non_empty_directory` on [replication_target](../../plugins/modules/replication_target.py) | Letting a replication source replace a target directory's content. |
| `...: the source directory holds ... bytes, more than the ... bytes this cluster can reuse (...); .... The comparison is approximate; strict_capacity=true refuses it` (a warning) | `strict_capacity` on `replication_target` | Turns the capacity warning into a refusal. |
| `... holds ..., and allow_directories_only lets the source's content replace only subdirectories; the authorize would replace it` | `allow_directories_only` on `replication_target` | Letting the source's content replace a target directory that holds only subdirectories. A directory that holds objects is refused. |

## Refusals that wait for a release

A replication relationship that another record holds is refused with the holder's id:

```text
... cannot be authorized: relationship ... from ...:... holds ... (ESTABLISHED); a release bound to ... naming O2
deletes it, and the directory keeps its data
```

Every unmet row of the overwrite table is in the result's `unmet`, and the releases that would lift them are in
`releases`. A release, `<target host>:<target path>@<record id>:<rows>`, lifts only the rows it names, and only while
the record with that id exists. It is passed with `-e` for one run, never in the inventory. Some rows no release lifts: a
live relationship at a path that contains the target path or lies inside it, one replicating back to the source, and a
live record of the relationship that the source does not hold. `ansible-doc qumulo.cluster.replication_target`
describes every row. [Replicate a directory](../how-to/replicate-a-directory.md#if-the-preview-refuses) shows the
procedure.

A relationship from a rebuilt source is held, not refused, while its replica may be the only copy of the source's
data: `<target host>:<target path> is held: ...`. When the holder allows it, the message names the restore, the
source edge's second pass or `playbooks/replication_restore.yml`, and prints the release only for keeping the replica
as plain data; otherwise it names the copy back and the release. The restore's records and steps are in
[Replication restore](playbooks-and-roles.md#replication-restore).

## The inventory and the cluster disagree

| Message | Cause and fix |
|---|---|
| `Validation of arguments failed:` followed by `<variable>[.<path>].<key>. Supported parameters include: <keys>.` | A variable of the baseline holds a key that it does not take, such as `qumulo_smb_settings.sesion_encryption` for `session_encryption`, a connection option such as `request_timeout` in an entry of `qumulo_smb_shares`, which the play's `module_defaults` set, or `tenant`, `state` or `declared_access_key_ids`, which the role sets itself. Ansible stops the role before its first task, so the role wrote nothing. The message names the dotted path to the key and lists the keys taken where the key sits; with keys in more than one place, the list is for one of them. It does not say which entry of a list holds the key, so search the inventory for the key. Correct it or remove it. `qumulo_network.managed_interfaces`, `qumulo_s3_bucket_policies[].policy` and `qumulo_capacity.license_document` take any key, so a misspelling in them is not caught here; step 2 refuses `qumulo_tenants[].identity`. A tenant's `smb_settings` and `nfs_settings` are checked at step 2 instead ([The inventory's tenants](#the-inventorys-tenants)). A rebuild gives the same message before it drains the edge ([Rebuilds](#rebuilds)). |
| `...: ... belong to Terraform by qumulo_terraform_owned_areas; remove the variables or keys named, or the areas from that list` | A declared variable, or a key of one such as `qumulo_authoritative_dns.fqdn` for `dns.fqdn`, is in an area Terraform owns. Remove the key or variable named, or the area from the list ([Declare the areas the root owns](../how-to/terraform.md#declare-the-areas-the-root-owns)). When `audit`, `dns.fqdn` or `network.floating_ips` is among the areas, the message adds `; or, for <areas>, set qumulo_take_over_from_terraform: true, which hands audit, dns.fqdn and the floating addresses back to this role together`. |
| `...: network.floating_ips (...) belong to Terraform by qumulo_managed_by: terraform_provider, since the provider applies its own floating addresses; remove the variables or keys named<kind remedy>; or, for network.floating_ips, set qumulo_take_over_from_terraform: true, which hands audit, dns.fqdn and the floating addresses back to this role together` | The inventory gives no `qumulo_terraform_owned_areas`, the cluster was formed by the Terraform provider and its `qumulo_cluster_kind` is `cnq` or unset, and a variable or key in the area `network.floating_ips` is declared, such as `floating_ip_ranges` in a tenant's `networks`. With the kind unset, `<kind remedy>` is `; or, on an edge or other on-premises cluster, set qumulo_cluster_kind: edge or baremetal, which leaves the floating addresses to this role`; with `cnq` it is left out. Remove the variable or key named, or take the floating addresses over from Terraform. On an edge or bare-metal cluster, declare its `qumulo_cluster_kind` instead. |
| `...: <places> declare floating addresses, which belong to Terraform by <owner>; remove them<remedy>, or set qumulo_take_over_from_terraform: true, which hands audit, dns.fqdn and the floating addresses back to this role together` | `floating_ip_ranges` in `qumulo_network.frontend_networks[<index>]` is declared, and Terraform owns the floating addresses. `<owner>` is `qumulo_terraform_owned_areas` when the inventory gives the list, which then names `network.floating_ips`, and `qumulo_managed_by: terraform_provider, since the provider applies its own floating addresses` when it does not and the cluster's `qumulo_cluster_kind` is `cnq` or unset. A tenant's floating ranges are refused by the area row above. Remove the ranges, or, with a list, `network.floating_ips` from it, which `<remedy>` says as `, or network.floating_ips from qumulo_terraform_owned_areas`, or take the floating addresses over. With no list and the kind unset, `<remedy>` is `, or, on an edge or other on-premises cluster, set qumulo_cluster_kind: edge or baremetal, which leaves the floating addresses to this role`, and on an edge or bare-metal cluster declaring the kind is the fix; with `cnq` it is left out. |
| `...: qumulo_terraform_owned_areas must list network.floating_ips for a cnq cluster with qumulo_managed_by: terraform_provider, which re-applies them; add network.floating_ips to it, or set qumulo_managed_by: none` | The host's `qumulo_cluster_kind` is `cnq`, its `qumulo_managed_by` is `terraform_provider`, and its `qumulo_terraform_owned_areas` leaves out `network.floating_ips`. The Terraform inventory plugin refuses the same output entry. Add the area to the list, or set `qumulo_managed_by: none` when the provider did not form the cluster. |
| `...: qumulo_audit_destinations declares the CloudWatch destinations ..., which belong to Terraform by qumulo_terraform_owned_areas through audit.cloudwatch; remove them, or set qumulo_take_over_from_terraform: true, which also hands the floating addresses back to this role` | The inventory declares CloudWatch audit destinations in an area Terraform owns. Remove them, or take the area over from Terraform. |
| `...: qumulo_portal.hub must name an inventory host with qumulo_cluster_uuid, and both the spoke and the hub need qumulo_portal_hosts, ...` | Complete the portal's declaration ([Declare and apply the portal](../how-to/pair-edge-with-hub.md#declare-and-apply-the-portal)). |
| `...: ...: the cluster refuses quotas and snapshot policies under a portal's spoke root (G-SPOKE-PATHS), and an S3 bucket there would serve the hub's data; declare them on local directories, or on the hub` | Move the quota, snapshot policy or S3 bucket off the spoke root ([G-SPOKE-PATHS](gaps.md)). A rebuild runs the same check before its drain. |
| `trustee '...': ... api_identity_unsupported_domain_error: Unknown SID domain ...` | The node has not found a name in that SID's domain since it started, so it refuses the SID, joined to a domain or not ([QUIRK-100](quirks.md)). The message names a trustee you declared; an entry the cluster reads back never causes it in `file_acl`, `smb_share` or `directory`. `S-1-5-11`, the SID of Authenticated Users, cannot cause it, because the collection looks it up by its name ([why](../explanation/identity.md#trustees-a-prefix-says-where-a-name-is-defined)). For another SID, find a name in its domain first, such as `ad:<NETBIOS>\Domain Users`, in an `identity_info` `find_trustees` task before the failing one, then run again ([Look up identities, DNS names and paths](../how-to/look-up-identities-and-paths.md)). |
| `trustee '...': the internal domain holds only File Owner and File Group Owner` | An `internal:` trustee names something else. The cluster's internal domain holds only those two names, which the collection takes in any letter case. Write `internal:File Owner` or `internal:File Group Owner`, or another form for the identity you meant ([Trustees](../explanation/identity.md#trustees-a-prefix-says-where-a-name-is-defined)). |
| `...identity {...} cannot be written as a trustee: it has no SID, uid or gid, and no name the trustee grammar holds` | The cluster returned an identity that `capture_info`, `filesystem_info` or `s3_info` reads back, in an ACL, a directory's owner or group, share permissions, a bucket policy or an access key's owner, and the grammar has no form for it. Open an issue on the collection's public repository, Qumulo/qumulo-ansible, with the identity from the message, and meanwhile manage it outside the collection. `file_acl`, `smb_share` and `directory` report such an entry by its name and can replace it, but cannot keep it, because no declaration names it. |
| `...: local group '...' does not exist; create it with qumulo.cluster.local_group first` | Declare the group in `qumulo_local_groups`; the baseline creates groups before users. |
| `the cluster is joined to ..., not ...; leave it first with ad_domain state=absent` | The cluster is in another domain. See [Rejoin Active Directory](../how-to/active-directory.md#rejoin-active-directory). |
| `... is set only by a join: the cluster has '...', not '...'; leave with ad_domain state=absent, then join again` | `ou` or `domain_netbios` changed. Leave and join again, as above. |
| `cannot rename '...' to '...' while it is joined to ...: the name is its computer account; ...` | Leave the domain, rename, then join again. |
| `joining ... needs join_user and join_password` | Give both in `qumulo_ad`. |
| `the cluster is joined to ..., and a joined cluster takes no keytab: its Kerberos keys are its computer account's (G-KRB-AD); leave with ad_domain state=absent first, or declare no keytab` | `kerberos_keytab` would install or replace a keytab on a cluster that is in a domain, or joining or leaving one, in check mode too. Leave the domain, or declare no keytab ([G-KRB-AD](gaps.md); [Join a cluster to Active Directory](../how-to/active-directory.md#if-the-join-is-refused)). |
| `qumulo_kerberos declares a keytab to install and qumulo_ad a domain to join, and the cluster takes no keytab while joined (G-KRB-AD); ...` | The baseline's opening check found a keytab to install and `qumulo_ad` in one inventory, and made no write. Remove the keytab, or set `qumulo_kerberos.keytab_state` to `absent`. |
| `ad_domain_standalone_kdc_error` (HTTP 409), from the join | The cluster holds a keytab, and refuses a join while it does. Delete the keytab, with `keytab_state: absent` or `kerberos_keytab` with `state: absent`, then join ([G-KRB-AD](gaps.md)). |
| `keytab_path ... is encrypted with Ansible Vault; a module reads the file as it is and cannot decrypt it; encrypt the keytab's base64 instead and give it as keytab: "{{ lookup('ansible.builtin.file', 'FILE') }}", which decrypts it` | `keytab_path` names a vaulted file, and the module fails before any request. Use the recipe in [Encrypt a binary secret such as a keytab](../how-to/store-secrets.md#encrypt-a-binary-secret-such-as-a-keytab). |
| `Networks ... each carry an IPv4 default gateway, but a cluster on chassis nodes takes one IPv4 default gateway, and the module refuses a second on every cluster. Keep the gateway on one network and set default_gateway: "" on the others; a new network left without default_gateway has none.` | Two or more STATIC networks carry a default gateway of one IP version (the message says `IPv6` for a gateway written with colons). The baseline refuses it at step 2, before any write, step 3a's tenant writes included, and `network_config` on its own refuses it before its write, on every cluster and in check mode too. Keep the gateway on one network and set `default_gateway: ""` on the others. When the cluster already gives each of those networks a gateway of that version, the module warns and goes on. |

## Tenants

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

The baseline makes these refusals at step 2, before any write, and the modules `tenant` and `network_config` make the
continuity ones for a write run alone. `<variable>` is `qumulo_management_network` in the role and
`management_network` in a module, and `<port variable>` is `qumulo_cluster_port` or `cluster_port`. A module run alone
words messages 1 to 4 for its own options. [Manage tenants](../how-to/tenants.md) has the
procedures, and [Tenants and the continuity rule](../explanation/multitenancy.md#the-continuity-rule) the reasons.

A refusal of the plan fails with `msg`, which holds each refusal's message on a line, and `refusals`, each message with
the names it gives, such as tenants, networks, entries and the play's `api_host`. The plan behind the refusal appears
only at verbosity 3 or more (`-vvv`), under `plan`. At `-v` or more Ansible prints each task's result, and the role's
network read at step 2 returns the network document, so a log kept at `-v` or more holds every address range of the
cluster, with or without `plan`.

### The play's connection

The continuity rule judges a tenant create, a change of `rest_api_enabled` or `web_ui_enabled`, a tenant delete and a
network write. Each message below is the first that applies, in this order.

| Message | Cause and fix |
|---|---|
| `The cluster's networking is host-managed and it has no frontend network yet, so with a second tenant REST would listen on no network. List its HOST network under one tenant in qumulo_tenants and run once before adding the second tenant, ...` | Message 1. The cluster has no frontend network to carry REST. Declare its HOST network under the one tenant and run the baseline once, then add the second tenant ([On networking the host manages](../how-to/tenants.md#on-networking-the-host-manages)). The message ends with what to do when the cluster's network status is unavailable: set `qumulo_management_network` to that network's id. |
| `This run removes network ..., which carries this play's connection. List it under the networks of a tenant in qumulo_tenants that is not state: absent, or set qumulo_allow_management_disruption: true and qumulo_api_host_after to the address the play uses after the run.` | Message 2. The network document would drop a network that carries `qumulo_api_host`. Keep the network under a present tenant, or consent and name the address to use afterwards. |
| `<variable> is ..., which is neither on the cluster nor in this inventory.` (or `... which is not on the cluster`; with a create at step 3a, `Add network ... first, in a run whose inventory does not yet list tenant ...`) | Message 3. `qumulo_management_network` names a network the cluster and the inventory lack. Correct the id, or add the network first, in a run before the one that adds the tenant. |
| `Network ... carries this play's connection but belongs to no tenant. List it under the networks of its tenant in qumulo_tenants.` | Message 4. Something unbound the network, such as a tenant PATCH of `networks` or `--clear-tenant-id` with two tenants. Declare it under its tenant and run again. A module run alone says `Pass it with its tenant to network_config, or run qq network_mod_network --network-id <network id> --tenant-id <tenant id>`. |
| `<api_host> resolves to no address. Set <variable> to the id of the network this play reaches the cluster on.` | Message 5, first text. The name does not resolve on the controller. Fix the name, or declare the network. |
| `No network on the cluster carries <api_host> (<addresses>). If this play reaches the cluster through NAT or a load balancer, set <variable> to the id of the network <api_host> is forwarded to.` | Message 5, second text. The address is on no network's ranges. Declare the network, as a play behind NAT does ([Declare the management network and the cluster port](../how-to/tenants.md#declare-the-management-network-and-the-cluster-port)). |
| `The cluster's network status is unavailable (G-NET-APPLY), so the collection cannot tell which network carries <api_host> (<addresses>), or whether a HOST network serves it. Set <variable> to the id of the network this play reaches the cluster on.` | Message 5, third text. The network status answered HTTP 500 or is not offered, and the address needs it. Declare the network. |
| `HOST network ... is not on the cluster yet, so it cannot carry this play's connection. Add it in a run that leaves <variable> on the network this play uses now, then set <variable> to ... once status shows it on a device.` | Message 6, first text. The run creates the HOST network that would carry the play. Add it in one run, then move the variable. |
| `Status shows HOST network ... on no device. A HOST network serves only on an interface the host associates with its id, ...` | Message 6, second text. The HOST network is in the document but on no device of any node. Run again once status shows it applied, or declare the id the host associates with an interface. |
| `... would leave network ... in tenant ... with ... off, and this play would lose its connection. Set ...: true on ..., or list ... under a tenant that has ...: true.` | The plain breach. The write leaves the play's network in a tenant without REST. Keep REST on that tenant, or list the network under a tenant that has it. With one tenant after the write the message is `... would leave tenant ..., the only tenant, with ... off, ...`. Where `qumulo_cluster_port` is 443 the switch is `web_ui_enabled`, and `rest_api_enabled` otherwise. |
| `... would leave tenant ..., which holds this play's connection, with ... off. This play connects on port 443 and declares no cluster port, so the collection cannot tell whether it reaches the REST listener or the web UI listener, and needs both on. ...` | The fail-closed breach. The play connects on 443 with no `<port variable>`, and exactly one of the two switches is on. Set `<port variable>: 443` if nothing between the play and the cluster changes the port, or the port a balancer maps 443 to. This covers a network write on a single-tenant cluster too. With both switches off, the plain breach applies. |
| `This run would delete every tenant (...). Remove state: absent from one of them.` (a module: `Deleting tenant ... would leave the cluster with no tenant.`) | The write would leave no tenant. Keep one tenant. |

Every message above is lifted for the whole run by `qumulo_allow_management_disruption: true`, except the no-tenant one.
The consent skips the rule and nothing else, and a connection it cuts is regained as
[Regain REST access](../how-to/tenants.md#regain-rest-access) says.

### The inventory's tenants

These are the baseline's refusals of what `qumulo_tenants`, `qumulo_network`, `qumulo_smb_shares`,
`qumulo_nfs_exports`, `qumulo_nfs_settings` and `qumulo_factory_defaults` declare. Each message names the entries.

| Message | Cause and fix |
|---|---|
| `qumulo_tenants[N] and qumulo_tenants[M] both declare tenant ...; declare each tenant once.` | Two entries of one name. Remove one. |
| `qumulo_tenants[N] renames ... to ... and qumulo_tenants[M] renames ... to ..., but the cluster holds both ..., so the first rename would find ... there and rename nothing.` (and `... both rename tenant ...; a tenant takes one new name.`) | A swap or chain of renames, or two entries that rename one tenant. Make each step in a run of its own, through a name the cluster does not hold. |
| `... sets identity, which is reserved for per-tenant identity, which is not supported yet. Remove it.` | A tenant entry has `identity`. Remove it. |
| `... names tenant ..., which qumulo_tenants declares state: absent.` / `network ... is listed under tenant ..., which qumulo_tenants declares state: absent, and kept in qumulo_network.frontend_networks[N].` | A network, share or export refers to a tenant being deleted, or an absent tenant's network is kept. List it under a tenant that stays, or leave it out so the run removes it. |
| `... names no tenant, but this run has more than one tenant (...). Name them with tenants, such as tenants: all.` | A flat share or export has neither `tenants` nor `tenant_id`, with two or more tenants. Add `tenants`. |
| `... is a new network with no tenant, but this run has more than one tenant (...), so the cluster would hold it in no tenant ...` | A new network outside the backend bond has no tenant. Give it `tenant`, or list it under its tenant. A rebuild preflight gives it too, prefixed as [Rebuilds](#rebuilds) says. |
| `... sets tenants: all, but qumulo_tenants declares no present tenant. ...` | Declare the tenants, or name them in `tenants`. |
| `... sets tenants to ...; give a list of tenant names, or all.` / `... tenants is empty; name at least one tenant.` / `... tenants names ... more than once.` | `tenants` takes `all` or a non-empty list of distinct names. |
| `... sets tenant, which a qumulo_smb_shares entry does not take; write tenants: [...].` / `... sets both tenants and tenant_id; name its tenants once, with tenants.` / `... sets both tenant and tenant_id; give tenant alone.` | An entry names its tenant one way. Write `tenants` on a flat share or export, `tenant` on a flat network. |
| `... sets tenants, but an entry under a tenant belongs to that tenant. Remove tenants.` (`tenant`, `tenant_id` likewise) | A nested share or export takes no tenant key. Remove it, or move the entry to the tenant it names. |
| `...'s share ... comes from both ... and ...; ...` | One (tenant, name) pair is declared by two entries. Declare it once. |
| `tenant ... is state: absent but holds ... that no entry declares or claims for it. Declare each under ..., or list ... in the shared entry that has it, so the run removes it before the tenant.` | A tenant to delete holds a share or export no entry names. Declare each under it, or add it to a shared entry. |
| `tenant ... is state: absent but holds network ..., ...` | A tenant to delete holds a network the run would not remove. Leave it out of the inventory, or list it under a tenant that stays. |
| `This run removes network ..., which tenant ... holds, but qumulo_tenants does not declare .... Declare ... and list the network under it, or keep the network in qumulo_network.frontend_networks.` | The document would remove a network of an undeclared tenant. Declare the tenant, or keep the network. |
| `... leaves out networks, but ... declares them. Once a tenant declares networks, ...` | Once one present tenant declares `networks`, every present tenant must, with `[]` for none. |
| `network ... is listed ...; list each network once.` | A network is under two tenants, or under a tenant and in `frontend_networks`. List it once. |
| `VLAN ... carries ... networks, ...; a VLAN carries one network of each address family.` / `VLAN ... is given ... mtu values, ...; give its networks one mtu.` / `VLAN ... and is the vlan_id of ...; a VLAN is declared in one place. ...` | A VLAN with two networks of one family, two `mtu` values, or its `vlan_id` declared by a tenant network and in `frontend_vlans`, as in a capture of a VLAN that carries a tenant's network and an unbound one. Correct the layout: list the unbound network under the VLAN's tenant with the VLAN's `vlan_id` and remove the VLAN from `frontend_vlans`, or give each network a VLAN of its own. |
| `VLAN ... carries network ..., which no qumulo_tenants entry lists, beside ..., so it would take network ... off VLAN .... List network ... under ... with vlan_id ..., or give each network a VLAN of its own.` | A VLAN on the cluster carries a tenant's network and a network that no `qumulo_tenants` entry lists, and the document the run writes puts that network on no interface: neither `frontend_vlans` nor the bond carries it, as when `frontend_vlans` is unset or `[]`, or when the tenant's network moves to another `vlan_id`. The run would take it off the VLAN. List the network under the same tenant's `networks` with the VLAN's `vlan_id`, or give each network a VLAN of its own. A `tenant` on its `qumulo_network.frontend_networks` entry does not clear this. |
| `VLAN ... carries network ..., which the run keeps but puts on no interface, so it would take network ... off VLAN .... Keep VLAN ... with network ... in qumulo_network.managed_interfaces.frontend_vlans, or list it under a tenant with vlan_id ....` | A VLAN on the cluster carries a network the run keeps, none of the VLAN's networks is listed under a tenant, and the document the run writes puts the network on no interface: neither the bond nor any VLAN of the document carries it. This happens with `frontend_vlans` unset when the run retires the tenant network beside it (`networks: []`) or removes the other of two flat networks on the VLAN, and whenever `frontend_vlans` is given without the VLAN. The run would take the network off the VLAN. Keep the VLAN with its network in `qumulo_network.managed_interfaces.frontend_vlans`, or list the network under a tenant with the VLAN's `vlan_id`. |
| `... has vlan_id ..., but the host manages this cluster's networking, ...` / `... is STATIC, but the host manages this cluster's networking, so a tenant network is HOST. Give it type: HOST.` | A host-managed cluster takes HOST networks only, with no VLAN. |
| `... carries ..., but the networks of one interface or VLAN belong to one tenant. Put each tenant's networks on an interface or VLAN of their own` | Two tenants have networks on one interface or VLAN. Give each tenant an interface of its own. |
| `... names tenant ..., which qumulo_tenants does not declare and the cluster does not hold.` (with `This run renames ... to ...; name ....` after a rename) | A share, export or network names a tenant that neither the inventory nor the cluster has. Declare the tenant, or correct the name. After a rename, name the new one: a check run still finds the tenant under its old name, and the message gives the new. |
| `... sets smb_settings to ...; give the options of smb_settings, or inherit.` (`nfs_settings` likewise) | A tenant's `smb_settings` or `nfs_settings` is neither a dict of the module's options nor `inherit`. |
| `... sets smb_settings.<key>, which smb_settings does not take; did you mean <key>?` or `...; it takes <keys>.` (`nfs_settings` likewise); for `state`, `...; to give the tenant the global settings, write smb_settings: inherit.`; for `tenant`, `...; the settings are those of the tenant whose entry holds them, so leave tenant out.` | A key in a tenant's `smb_settings` or `nfs_settings` dict is not one the global `qumulo_smb_settings` or `qumulo_nfs_settings` takes, so step 18 or 19 would drop it. The step refuses it even under a tenant declared absent. Correct the key, to the one the message suggests when it finds a near one, or remove it. `tenant` and `state` are refused as well: write `smb_settings: inherit` to give the tenant the global settings, and leave `tenant` out, since the entry names the tenant. A rebuild preflight gives it too, prefixed as [Rebuilds](#rebuilds) says. |
| `... declares networks, but owned_parts leaves out networks, so the run cannot give a network to a tenant. Add networks to owned_parts, or leave networks out of the entry.` | `tenancy_plan` run on its own, with `owned_parts` leaving out `networks`. The role never does, because it refuses a tenant's `networks` while Terraform owns `network`, before step 2. Add `networks` to `owned_parts`, or leave `networks` out of the entry. |
| `...: network ... is not on the cluster, and this run does not own networks, so it adds no network.` | `tenancy_plan` run on its own, with `owned_parts` leaving out `networks`, so it sets floating addresses only and each network listed must exist. Add `networks` to `owned_parts`, or leave the network out. |
| `tenant ... is state: absent but holds network ..., which this run keeps: owned_parts leaves out networks, so the run neither removes a network nor moves one to another tenant. ...` | `tenancy_plan` run on its own, with `owned_parts` leaving out `networks`, so the plan neither removes a network nor moves one. Add `networks` to `owned_parts`. |
| `... is on the backend bond, and a backend network takes no tenant: with two tenants a tenant network without a frontend interface aborts every node (G-TENANT-BACKEND).` | A tenant lists a backend-bond network, or a flat entry gives one a `tenant`. Leave backend networks without a tenant, in `qumulo_network.frontend_networks`. |
| `creating tenant ... would make a second tenant while tenant ... holds network ..., which is not on the frontend bond, a frontend VLAN or a HOST network ...` | A backend network sits in a tenant (G-TENANT-BACKEND). Unbind it with a tenant PATCH of `networks` that leaves it out, then run straight away ([Prepare backend networks](../how-to/tenants.md#prepare-backend-networks-before-the-second-tenant)). |
| `NFSv4.1 needs every export of tenant ... outside every other, but ...` | A tenant would serve NFSv4.1 with one export inside another, after the run or when the cluster-wide setting turns on. Remove or move one export, or keep `v4_enabled` off for the tenant. |
| `... sets tenant_id ..., which is tenant ..., but an entry under a tenant belongs to that tenant. Move it under ..., or remove tenant_id.` | A nested entry's `tenant_id` names another tenant. Move the entry or remove the key. |
| `Networks ... each carry an IPv4 default gateway, ...` | See [The inventory and the cluster disagree](#the-inventory-and-the-cluster-disagree). |

The baseline warns, without refusing, once for each `tenant_id` it translates (`... tenant_id ... is translated to tenant
...; tenant_id is deprecated, ...`), for a `tenant_id` that is no tenant on the cluster (`... skipped` for a share or
export, `... dropped` for a network), for a `tenant_id` on a backend network, and when a tenant it deletes takes the
factory `Files` share or `/` export with it.

### The tenant list

| Message | Cause and fix |
|---|---|
| `GET /v1/multitenancy/tenants/ failed: HTTP 403 ...` from step 2 or `tenant_info` | The user the play connects as lacks `PRIVILEGE_TENANT_READ`. Every run whose inventory declares one of the six variables reads the list, and a failure stops it. Give the user the privilege. |
| `the tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ (...)` (a warning; `the document's tenant names are null: ...` from `network_info`) | An info module could not read the list for want of the privilege, so it returns `tenant` as null beside `tenant_id`. Give the user the privilege. |

### Tenant writes the cluster refuses

| Message | Cause and fix |
|---|---|
| `this cluster refuses tenant writes (HTTP ... multitenancy_api_platform_not_allowed_error): Qumulo Core does not allow creating, changing or deleting a tenant on a cloud platform ...` | The cluster is on AWS, Azure, GCP or OCI, where no tenant write is allowed (G-TENANT-CLOUD). Remove the tenant change. |
| `This is a cloud cluster (...), and no cloud cluster supports tenancy: Qumulo Core refuses ... there. Remove them from qumulo_tenants. A cluster on premises whose storage is object-backed declares qumulo_cluster_kind: baremetal.` | The baseline's refusal for a `cnq` cluster, or one with an object-backed protection type. Remove the tenant writes, or declare `qumulo_cluster_kind: baremetal` if the cluster is on premises. A check run of `tenant` on such a cluster warns instead. |
| `a new tenant takes the identity configuration of tenant 1, or once tenant 1 is gone the one every tenant shares, but tenant 1 is gone and the tenants use different ones ...` | Tenant 1 is gone and the remaining tenants differ in identity, which needs per-tenant identity. Not supported yet. |
| `tenant ... still holds .... The cluster deletes a tenant's shares, exports and SMB and NFS overrides with it (G-TENANT-CASCADE), so the module deletes only a tenant that holds none: ...` | `tenant` run alone refuses a delete while the tenant holds a network, share or export. Remove its shares and exports, and move its networks to another tenant with `network_config`, first. The baseline does this itself. |
| `no tenant on this cluster is named '...'; its tenants are ...` | A task names a tenant the cluster lacks. Create it first, or correct the name. |

### The replication address

At step 21 the baseline asks the target whether the tenant that serves the target's `qumulo_replication_address` has
replication on. `replication_target` makes the check on every run of a relationship that is not `state: absent`, an
established one included. The user the play connects to the target as needs `PRIVILEGE_TENANT_READ`, and with two or
more tenants `PRIVILEGE_NETWORK_READ`.

| Message | Cause and fix |
|---|---|
| `Replication into ... connects to ..., but ..., the only tenant, has replication_enabled: false. Set replication_enabled: true on ....` | The target has one tenant, with replication off. Set `replication_enabled: true` in that tenant's entry of the target host's `qumulo_tenants`. |
| `Replication into ... connects to ..., on network ... of tenant ..., which has replication_enabled: false. Set replication_enabled: true on ..., or set qumulo_replication_address to an address on a network of a tenant that has it on.` | The address lies on a network whose tenant has replication off. Turn it on, or move the address. |
| `replication_address ... is not an IP address; give the address the source connects to` | The target host's `qumulo_replication_address` is not an IP address. Give one. |
| `Replication into ... connects to ..., which no network's ranges on this cluster hold, as for a HOST network's host address, a DHCP network's node address or an address behind NAT; whether its tenant has replication_enabled is not checked.` (a warning) | The address is on no network's ranges, so the tenant is not judged. Check by hand that the tenant serving it has replication on. |
| `Replication into ... connects to ..., on network ..., which no tenant holds; whether replication is enabled for it is not checked. Bind network ... to a tenant to have it checked.` (a warning) | A network holds the address but no tenant holds the network. Bind the network to a tenant. |

## Hand-offs

A hand-off is a step the collection cannot take for you. Its message says what to get, where to put it and what to run
next.

### The license

A cluster without a license for its UUID gets this from the baseline's license step, which then leaves the clamp for the
second pass and goes on. A host in `qumulo_edges` is told to run `qumulo.cluster.edge_license_complete`; any other host
is told to run `qumulo.cluster.baseline`.

```text
...: The cluster holds no license for its UUID, .... ... Put the license document issued for ... in
qumulo_capacity.license_document for ..., encrypted with ansible-vault, then run ansible-playbook
qumulo.cluster.edge_license_complete -i <inventory> -l ..., which applies it, then sets the capacity clamp, whose write
applies the license's cap. Once the cluster holds the license, that run needs no document (G-LICENSE).
Until then the capacity clamp is not set.
```

The message varies as follows:

| Variation | Text |
|---|---|
| The run registered the cluster with Nexus | The second sentence reads `This run registered the cluster with Nexus, so Qumulo can issue the license for its UUID there.` |
| The cluster is not registered | `Qumulo issues the license for the UUID in Nexus, which sees the UUID only once the cluster is registered there.` |
| `qumulo_capacity.license: required` | The message starts with `qumulo_capacity.license is required, and the cluster has none.`, and the baseline sets `cluster_baseline_license_missing`. |
| The inventory's document is for another UUID | After the UUID sentence: `The license document in qumulo_capacity.license_document is for <old UUID>, not this cluster.` The instruction reads `Replace it with the license document issued for <new UUID>` in place of `Put the license document issued for ...`. |
| No clamp is declared | The message omits `, then sets the capacity clamp, whose write applies the license's cap` and the last sentence, `Until then the capacity clamp is not set.` |
| A clamp is stored already that the declared one would not raise, as on an edge moved from `license: none` to `pending` | The message omits `, then sets the capacity clamp, whose write applies the license's cap` and the last sentence, and ends with `Its clamp is already set, so the license's cap applies at the next quorum formation after the license arrives.` |
| The edge declares `qumulo_capacity.license: none` and holds no license | No hand-off: the host gets `...: runs without a license.` and its clamp is written with no license. |

`playbooks/edge_license_complete.yml` fails an edge that has neither an active license nor a document with the same
message. [License an edge](../how-to/licensing.md) gives the procedure, and
[Licensing and capacity](../explanation/licensing-and-capacity.md) gives the reasons.

Other license messages:

| Message | Cause and fix |
|---|---|
| `...: the license caps usable capacity at ... bytes, below the clamp of ..., so the clamp was not written. ...` | Get a license whose cap allows the clamp, or lower `qumulo_capacity.clamp`. Nothing re-formed quorum. |
| `...: the license allows more capacity than the file system has, and the clamp did not change, so the cap applies at the next quorum formation` | The cap applies at the next quorum formation; raising the clamp causes one. It prints only after this run applied the license document, and the clamp did not change, when the cap, and the clamp when set, are more than `FLOOR_MARGIN`, 10^11 bytes, above the file system. |
| `...: its clamp is already set, so no clamp write re-forms quorum, and the license's cap applies at the next quorum formation, unless the platform's disks already limit the file system.` | The edge formed without a license, at 1 TB or a little less, and holds a license whose cap, and its clamp, allow more. The cap applies at the next quorum formation, which nothing in the collection causes while the clamp is as declared. On a platform between 1 TB less `FLOOR_MARGIN` and 1 TB the disks keep the file system there for good, so this prints on every run and is true as it stands; lowering the clamp to silence it fails as a decrease. |
| `...: declares qumulo_capacity.license: none and a license document. Set qumulo_capacity.license to pending or required, keeping its other keys, such as clamp and free_threshold, to apply the document, or remove the document if it is another cluster's.` | A document means a license is coming. Set `qumulo_capacity.license` to `pending` or `required` to apply it, or remove the document if it is another cluster's. Nothing was applied. A rebuild refuses the same before its drain. |
| `...: declares qumulo_capacity.license: none but holds an active license...` | The edge holds a license after all, and the step ran as `pending` does. Set `qumulo_capacity.license` to `pending` or `required`. The four forms are in the next table. |
| `the license document is for cluster ..., but the cluster at ... is ...; replace it with the license document issued for ...` | The document is another cluster's, as an old edge's after a rebuild. |
| `the cluster refused the license document: ...` | The cluster's own reason. A document must be exactly the file Nexus issued. |
| `...; applying a license document uses ... and ..., which need the PRIVILEGE_DEBUG privilege (G-LICENSE)` | Apply it as `admin`, which holds the privilege. |

The warning for a `none` edge that holds an active license starts `...: declares qumulo_capacity.license: none but holds
an active license` and ends in one of four forms. The first that matches applies:

| When | Text after `holds an active license` |
|---|---|
| The stored-clamp message above would print | `. Its clamp is already set, so no clamp write re-forms quorum, and the license's cap applies at the next quorum formation, unless the platform's disks already limit the file system. Set qumulo_capacity.license to pending or required.` |
| No clamp is declared | `, and declares no clamp, so nothing limits the license's cap, which applies at a quorum formation, and the capacity provisioned under it never shrinks. Declare qumulo_capacity.clamp, and set qumulo_capacity.license to pending or required.` |
| A clamp is declared, and the file system is at or above it | `. Its clamp limits the licensed capacity. Set qumulo_capacity.license to pending or required.` |
| A clamp is declared otherwise | `. Set qumulo_capacity.license to pending or required.` |

### The edge's cache

`playbooks/edge_cache_resize.yml` fails an edge on purpose on three routes, each naming what to do, and when it cannot
choose a route.

| Message | Cause and fix |
|---|---|
| `...: blocked_license: ...; the target clamp is ... bytes. Once Qumulo has issued a license with a usable capacity of at least ... bytes for cluster ..., put its document in qumulo_capacity.license_document, encrypted with ansible-vault, and run ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l ..., which applies it and then sets the clamp. A raised cap applies at the next quorum formation, ... (G-LICENSE).` | The license's cap is below the target. Get a license with a larger cap, then do as the message says. The `...` after `formation,` is `which the clamp's own write causes`, or, when the stored clamp already allows the target, `which this role does not cause, since the stored clamp already allows the target; re-forming quorum then is a hand-off to the cluster's owner`. On a `license: none` edge the message adds `It declares qumulo_capacity.license: none; set it to pending or required, keeping its other keys, before supplying the document. ...`. |
| `...: below_floor: .... Neither the clamp nor a rebuild makes the cache that small; set qumulo_capacity.free_threshold to keep part of it free instead.` | The target is below what the clamp can reach and a rebuild would form. Set `qumulo_capacity.free_threshold`. |
| `...: rebuild: .... Only a rebuild makes the cache that small, ... Run playbooks/edge_predestroy.yml, reinstall the edge, then run playbooks/edge_rebuild.yml with -e qumulo_rebuild=true, ...; or set edge_cache_resize_allow_rebuild: true and edge_rebuild_reinstall_tasks to have this role run all three.` | Only a rebuild reaches the target. Rebuild the edge by hand, or set `edge_cache_resize_allow_rebuild` and `edge_rebuild_reinstall_tasks`. |
| `...: rebuild: edge_cache_resize_allow_rebuild needs edge_rebuild_reinstall_tasks, ...` | `edge_cache_resize_allow_rebuild` is set without the tasks file that reinstalls the edge. Set `edge_rebuild_reinstall_tasks`. |
| `...: cannot choose a route: the cluster did not answer ...` | The read of the clamp, the license, the file system or the product failed. The message names what was missing. Check that the cluster is up, then run again. |
| `...: cannot choose a route: its product type is unknown, ...` | The cluster's metrics carry no `qumulo_info` sample with a `cluster_type` label, and the product type decides whether the 1 TB unlicensed ceiling applies. |

[Playbooks and roles](playbooks-and-roles.md#edge-cache-resize-routes) lists every route.

### A rebuild

The rebuild report's `hand_offs` list what is left to do by hand, among them: applying `host_vars_patch` first, the
license for the new UUID, a spent Nexus join key, retiring the old UUID in Nexus when there is no
`qumulo_nexus_api_token` ([G-NEXUS-RETIRE](gaps.md)), `admin`'s changed password, DNS records, Terraform state that
names the old UUID, the domain's computer account, a hub record the predestroy phase could not reach, the failing
identity guard of sources that replicate into the edge until the patch is applied, the second pass and held
relationships, each acknowledged area, and the S3 access keys made or failed.
[Work through the report](../how-to/edge-rebuild.md#work-through-the-report) explains each.

## Rebuilds

| Message | Cause and fix |
|---|---|
| `...: a replay forms or adopts a new cluster, so it needs -e qumulo_rebuild=true, the declaration that this edge is being rebuilt; ...` | Declare the rebuild, and give whatever else the message lists. |
| `Validation of arguments failed:` followed by `<variable>[.<path>].<key>. Supported parameters include: <keys>.` | The inventory holds a key that a baseline variable does not take. A rebuild checks the inventory against the baseline's argument specification first, in every phase and before it contacts an edge, so the key is refused before anything is drained or reinstalled, not at the replay's baseline after the destroy. Correct or remove the key, as [The inventory and the cluster disagree](#the-inventory-and-the-cluster-disagree) says. |
| `...: ... belong to Terraform by ...` or `...: qumulo_terraform_owned_areas must list network.floating_ips ...` (the baseline's ownership messages) | The inventory declares an area that Terraform owns. A rebuild's predestroy phase runs the baseline's ownership check before the preflight, so this refusal comes before the drain. Do as the message says; the rows under [The inventory and the cluster disagree](#the-inventory-and-the-cluster-disagree) explain each message. |
| `the replay onto the rebuilt edge would refuse the inventory: ...` | The inventory rules of the baseline's tenancy step (renames, entries and tenant references, claims, and the network rules that need no read) would refuse the inventory the edge is replayed from, and the rest of the message is that step's refusal, such as a flat share or export with neither `tenants` nor `tenant_id` once the replay has a second tenant, two `qumulo_tenants` entries of one name, a key in a tenant's `smb_settings` or `nfs_settings` that the global settings do not take, an inventory that leaves no tenant, an entry that declares tenant 1's `Files` share while `qumulo_factory_defaults.smb_files_share` is `absent`, three inventory network rules (once a present tenant declares `networks` every present tenant must, a network is listed once, and an absent tenant's network is neither kept flat nor named by a flat `tenant_id`), tenant 1 declared `state: absent` while no network is listed, or a network listed with no tenant while tenant 1 is declared `state: absent`. For tenant 1 declared absent with no network listed, the message says to declare networks under every present tenant, or to set `qumulo_network.frontend_networks`, listing each network under a tenant that stays. In that message, the cluster is the newly formed edge, which holds only tenant 1, `Default`. With `network` in `qumulo_terraform_owned_areas` that remedy is refused by the ownership check too: take `network` out of `qumulo_terraform_owned_areas` for the rebuild, and afterwards put it back and remove the listing in the same change, as the `the replay would create tenant` row says. On an edge that forms host-managed again, the rest of the message can instead be the baseline's host-managed refusal of a tenant network, which the preflight makes before the drain and before any drift: `network N of tenant X has vlan_id V, but the host manages this cluster's networking, so a tenant network takes no VLAN. Remove vlan_id.`, or, for a network of `type: STATIC`, `... is STATIC, but the host manages this cluster's networking, so a tenant network is HOST. Give it type: HOST.` The preflight leaves this check to the replay when `qumulo_network` declares `managed_interfaces` and the tenant 1 pass runs. A network in `qumulo_network.frontend_networks` sent with no tenant (no `tenant`, or a `tenant_id` the newly formed edge lacks), which the newly formed edge does not hold, gives the step 2 message `qumulo_network.frontend_networks[N] (network <id>) is a new network with no tenant, but this run has more than one tenant (...), so the cluster would hold it in no tenant ...`: name its tenant with `tenant`, or list it under its tenant in `qumulo_tenants`. [A network sent with no tenant](../explanation/rebuilds.md#a-network-sent-with-no-tenant) says which networks the formed edge holds. Fix the inventory as it says; `edge_rebuild_ignore_drift` does not waive this. The network document's own checks (its validation, the backend checks, and the interfaces) and the NFS and continuity checks need the new edge and run only at the replay, and so do its other host-managed checks. |
| `qumulo_tenants[N] gives no name`, `qumulo_tenants[N] (<name>) sets state to '...'; give present or absent`, `qumulo_tenants[N].networks[M] gives no id`, `qumulo_network.frontend_networks[M] gives no id` | An entry of `qumulo_tenants` has no `name`, or a `state` other than `present` or `absent`, or a listed network has no integer `id`. Fix that entry; `edge_rebuild_ignore_drift` does not waive this. |
| `the replay would create tenant <name> on the newly formed edge, which like this edge is host-managed and forms with no frontend network, but the inventory lists no network for a tenant that stays, ...` | The edge's network document has no managed interfaces and only HOST networks, so it forms again with no frontend network, and the inventory creates a tenant but lists no network for a tenant that stays. The replay's tenant 1 pass writes tenant 1's own networks, or every listed network that stays when tenant 1 holds none, in tenant 1 before the baseline creates the other tenants, which step 2 refuses on a cluster with no frontend network; with none to write, the replay would fail after the destroy. List the edge's HOST network under a tenant's entry in `qumulo_tenants`, or in `qumulo_network.frontend_networks`. When `qumulo_terraform_owned_areas` lists `network`, the message instead says to take `network` out of `qumulo_terraform_owned_areas` for the rebuild, list the HOST network as above, and take that listing out in the same change that puts `network` back, since putting it back first makes the next run's ownership check refuse the listing; that check refuses `qumulo_network` and a `networks` key under a tenant while `network` is owned. `edge_rebuild_ignore_drift` does not waive this. |
| `the replay's tenant 1 pass would write networks <ids> into tenant 1 on the newly formed edge before it creates tenant <name>, and with more than one HOST network the baseline's step 2 cannot tell which one this play reaches the edge on, nor can the preflight tell from this edge, since <why>, so step 2 would refuse the create after the destroy. ...` | The edge forms host-managed again, the replay creates a tenant, the tenant 1 pass would write two or more networks into tenant 1 (tenant 1 holds two or more, or holds none while the inventory lists two or more), `qumulo_management_network` is unset or null, and the preflight could not place the play's address on the old edge. `<why>` says which: `this edge does not answer its network status` (G-NET-APPLY), `this edge's network status read failed: <error>`, `this edge holds HOST networks N and M and the play reaches it on the host's own address, A, which the network status does not place on any one of them`, `this edge's network status places the play's address on no one network` (an address the status does not show), `this edge's network status shows network N served on no device`, or `qumulo_api_host resolves to no address`. Set `qumulo_management_network` to the id of the network `qumulo_api_host` is on. An old edge that holds two HOST networks, with the play on the host's own address, gives the `holds HOST networks` reason, since the rule places an address from the status only on the document's one HOST network. Where the preflight can place the address it needs no variable, and says which network the replay uses in `management_network`. Tenant 1 on HOST 0 beside a second tenant on its own HOST 1 rebuilds without the variable when the play reaches the edge on tenant 1's network and the newly formed edge answers its network status; a play on the second tenant's network takes the remedy of the `but the replay's tenant 1 pass writes only` row below. The message comes after any drift, in the place of the previous row's, and `edge_rebuild_ignore_drift` does not waive it. |
| `the play reaches this edge on network <id>, which the replay's tenant 1 pass does not write before it creates tenant <name>, so the baseline's step 2 would refuse the create after the destroy. Set qumulo_api_host to an address on one of networks <ids> for the rebuild and qumulo_management_network to that network's id, or, if the inventory lists network <id> under another id, set qumulo_management_network to that id` | The edge forms host-managed again, the replay creates a tenant, the tenant 1 pass would write two or more networks, and the old edge carries the play's address on a network the pass does not write, because the inventory lists it under no tenant that stays or under a second tenant. Set `qumulo_api_host` to an address on one of the networks the message names, which the pass writes, for the rebuild, and `qumulo_management_network` to that network's id. If the inventory lists the same network under another id, set `qumulo_management_network` to that id; setting it to the old edge's id is refused as the next row says. Listing the network under tenant 1 is not the remedy: it is `tenant_networks` drift for a network the old edge binds to another tenant. The message comes after any drift, in the place of the previous rows' messages, and `edge_rebuild_ignore_drift` does not waive it. |
| `qumulo_management_network names network<s> <ids>, which the inventory lists for no tenant that stays, and the replay's tenant 1 pass writes only network<s> <ids> before it creates tenant <name>, so the baseline's step 2 could not find the network this play reaches the edge on and would refuse the create after the destroy. ...` | The edge forms host-managed again, the replay creates a tenant, and `qumulo_management_network` names a network the inventory lists for no tenant that stays, such as a typo, or the old edge's id for a network the inventory lists under another id, however many networks the tenant 1 pass writes. The message ends "Set qumulo_management_network to the id the inventory gives the network qumulo_api_host is on". That id is the one the pass writes, so for a network the inventory renumbers it is the new id, not the old edge's. The message comes after any drift, in the place of the previous rows' messages, and `edge_rebuild_ignore_drift` does not waive it. |
| `qumulo_management_network names network<s> <ids>, but the replay's tenant 1 pass writes only network<s> <ids> before it creates tenant <name>, and the baseline's step 3 adds network<s> <ids> only after that create, so the baseline's step 2 would refuse the create after the destroy. ...` | The edge forms host-managed again, the replay creates a tenant, and `qumulo_management_network` names a network the inventory lists for a tenant that stays, such as a second tenant's own network, which the baseline's step 3 adds only after the first create, however many networks the tenant 1 pass writes. Step 2 judges that create against the document the pass leaves. Name the network of tenant 1 that `qumulo_api_host` is on, or, if the play reaches the edge on the network the message names, set `qumulo_api_host` to an address on a network the pass writes for the rebuild and `qumulo_management_network` to that network's id; the message ends "... set qumulo_api_host to an address on network 0 for the rebuild and qumulo_management_network to that network's id". Set both back after the rebuild. The remedy of reaching the edge at an address on a network the pass writes does not apply when tenant 1's REST switch is off (`rest_api_enabled`, or `web_ui_enabled` behind 443) or the controller has no route to tenant 1's networks; such a rebuild is not supported yet. The message comes after any drift, in the place of the previous rows' messages, and `edge_rebuild_ignore_drift` does not waive it. |
| `qumulo_management_network is declared with no network, which the baseline's step 2 would refuse at the create after the destroy. Set qumulo_management_network to the id of the network qumulo_api_host is on, or leave it unset` | The edge forms host-managed again, the replay creates a tenant, the tenant 1 pass writes a network, and the inventory sets `qumulo_management_network` to an empty list. Set it to the id of the network `qumulo_api_host` is on, which the tenant 1 pass must write, or leave it unset. The message comes after any drift, in the place of the previous rows' messages, and `edge_rebuild_ignore_drift` does not waive it. |
| `the rebuilt edge would gain tenant <name>, which this edge lacks: ...` | The old edge has no tenant 1, and a newly formed edge always has tenant 1, `Default`, with the `Files` share and `/` export, which the replay keeps, or renames to `<name>` and keeps, unless the inventory declares it `state: absent`. A capture of the edge writes that entry, or on host-managed networking the rename of `Default` to the one tenant that holds a network, unless another tenant is named `Default` or a replay could not delete it, and its notes say which. Declare `Default` `state: absent` in `qumulo_tenants` (or drop the entry for `<name>`, or its `previous_name`, and declare `Default`), and claim the factory share and export for removal, with `qumulo_factory_defaults` `smb_files_share` and `nfs_root_export` set to `absent` or by declaring `Files` and `/` under the absent `Default` entry, and list each network under a tenant that stays, so the replay removes them and the tenant; or set `edge_rebuild_ignore_drift` to rebuild with it. |
| `the rebuilt edge would put network <id> (<name>), which this edge holds in no tenant, in tenant <name>: ...` (several: `network <id> (<name>) and network <id> (<name>), which ...`) | The inventory lists the network in `qumulo_network.frontend_networks` with no tenant (no `tenant`, or a `tenant_id` the newly formed edge lacks), and the replay leaves such a network in tenant 1; or, when the message says "the inventory lists no network", the inventory lists none and the replay keeps the formed edge's network 1 in tenant 1. The old edge held it in no tenant. As the message says, name its tenant with `tenant`, or list it under its tenant in `qumulo_tenants`. When the inventory lists no network, listing network 1 means listing networks, so list every other network the edge holds too, as the `tenant_networks` row below says. Or set `edge_rebuild_ignore_drift` to rebuild with it in tenant 1. [A network sent with no tenant](../explanation/rebuilds.md#a-network-sent-with-no-tenant) says which networks this covers. |
| `the edge holds state its inventory does not declare, which a rebuild would lose: .... Declare it in the inventory, or set ignore_drift to rebuild without it` | Declare the drift, or set `edge_rebuild_ignore_drift` to lose it. A `tenant_networks` entry, such as `tenant_networks '2 (eng-net)' in tenant eng`, is a network the old edge binds to a tenant the replay builds again, that the replay would not bind to it; list it under that tenant's `networks` in `qumulo_tenants`. Once any network is listed, the replay keeps only the listed networks and removes the rest: list each network the edge holds, under its tenant, or in `qumulo_network.frontend_networks` for a network no tenant holds or on the backend bond, and give every present tenant `networks`, `[]` for none. When tenant 1 was renamed by hand, the message ends with a sentence that starts "Tenant 1 is <name> on the edge" and gives the fix, such as `previous_name: Default` on tenant 1's `qumulo_tenants` entry. |
| `the edge still holds state its inventory declares absent: .... Apply the inventory to the edge first, or set ignore_drift to rebuild without it` | Run the baseline on the edge first. |
| `the edge holds state the replay cannot create again: .... List ... in acknowledge to rebuild without it and recreate it by hand afterwards` | List each area in `edge_rebuild_acknowledge`, and recreate it by hand after the rebuild. |
| `the target directory of ..., which may hold the only copy of the data ... had before it was rebuilt. Restore ... from it first: ...` | The edge holds the replica a rebuilt source can still restore from. Run the source's restore it names, after which the relationship is replayable, or acknowledge both `local_data` and `replication` to give the replica up. |
| `...: the replay takes neither qumulo_replication_release nor qumulo_replication_restore; both belong to later runs. ...` | Run the replay without them. A held relationship's way out is the second pass's restore where the report names it, or the copy and the release. |
| `... waits for the restore of ..., which fills ... with its last replica. Once that restore is done, set allow_non_empty_target: true on the entry and run ansible-playbook qumulo.cluster.baseline -i <inventory> -l ....` | A relationship into the edge lies in a directory the edge's restore fills. Let the restore finish, then do as it says. |
| `...: no restore, since the license play did not finish the license and the clamp.` | The edge's license is still pending, as while a license Qumulo issues to the cluster directly is on its way, or its license play failed. The message continues with `Run the second pass again once the license applies, or supply the license document`, and for an edge that runs without a license, with how to declare `license: none`. Run the second pass again once the license applies; nothing is lost meanwhile. For an edge that declares `license: none` the message reads `the clamp` in place of `the license and the clamp`. |
| `...: no restore, since this run did not include the license play, which finishes the license and the clamp first. ...` | The run had `--tags restore`, which skips the license play. On a `license: none` edge the message reads `the clamp first` in place of `the license and the clamp first`. Run the second pass without the tag, or `ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l ...` for the restore alone. |
| `No restore ran: this run carries qumulo_replication_release, which is for the baseline. Run the second pass again without it.` | A release belongs to the baseline. Run the second pass again without it. |
| `R ... did not read ESTABLISHED on both ... and ... within ... seconds of the reconnect. The likeliest cause is that ... cannot reach ... at ...` | The edge cannot reach the target at the address and port named. Fix that path, then run again; the next run only polls. |
| `...: declares qumulo_capacity.license: none and a clamp of ... bytes, below 1 TB. ...` | The rebuild's refusal for a `none` edge, below. |
| `...: declares a clamp of ... bytes, below 1 TB. ...` | The rebuild's refusal for any other edge, below. |

A declared clamp more than its rounding tolerance below min(1 TB, `edge_cache_resize_floor_bytes`) is refused on every
rebuilt edge, whatever its license value. The refusal comes before the drain in a rebuild in one run, and before the
formation in the replay phase of a rebuild in two runs. A rebuild in two runs drains and destroys the edge in its
predestroy run, which checks none of this. To fix it, set `qumulo_capacity.clamp` to `1TB`, or remove it where the
platform is smaller than 1 TB, and keep the cache smaller with `free_threshold`. When `edge_cache_resize_floor_bytes`
lowers the bound below 1 TB, the message reads `below <bound> bytes` in place of `below 1 TB`, and `Set
qumulo_capacity.clamp to <bound>` in place of `Set qumulo_capacity.clamp to 1TB`. [The 1 TB
floor](../explanation/licensing-and-capacity.md#the-1-tb-floor) gives the reason. The message for a `none` edge:

```text
edge01: declares qumulo_capacity.license: none and a clamp of 950000000000 bytes, below 1 TB. An unlicensed
edge forms with the smaller of its platform's capacity and 1 TB, and provisioning never shrinks, so the baseline
cannot write such a clamp (G-EDGE-FLOOR, G-CLAMP-SHRINK). Set qumulo_capacity.clamp to 1TB, so that a license that
arrives later cannot grow the edge past it, or remove it on a platform smaller than 1 TB, and keep the cache smaller
with free_threshold.
```

and for any other edge:

```text
edge01: declares a clamp of 950000000000 bytes, below 1 TB. A rebuilt edge forms unlicensed with the smaller
of its platform's capacity and 1 TB, and provisioning never shrinks, so the baseline cannot write such a clamp
(G-EDGE-FLOOR, G-CLAMP-SHRINK). Set qumulo_capacity.clamp to 1TB, so that a license that arrives later cannot grow
the edge past it, or remove it when the platform or the license's cap is smaller than 1 TB, and keep the cache
smaller with free_threshold.
```

[Rebuild an unprotected edge](../how-to/edge-rebuild.md) gives the procedure, and
[Replication restore](playbooks-and-roles.md#replication-restore) names the restore's records and steps, such as R, X
and step 7.

## Failing over

The `replication_failover` role plans every named relationship before its first write, so a refusal of one stops the run
before anything changes.
The first rows are those refusals, and the last rows are stops in the middle of a run, which the next run resumes from
the records.
`<name>` is `<target host>:<target path>`.
[Fail over to a replica, and fail back](../how-to/fail-over-replication.md) gives the procedures, and
[Replication failover](playbooks-and-roles.md#replication-failover) lists the states and steps.

| Message | Cause and fix |
|---|---|
| `The <action> above changes which copy of each relationship clients may write, and sends directories back to the recovery point. Read the plan, then run again with -e replication_failover_allow_data_loss=true.` | A real run with a step left needs the run's consent. Read the plan printed above the message, then run again with the consent on the command line. A check run and a run with nothing left need none. |
| `<name>: <host>'s <path> changed since the recovery point ...: N changes (...). The reconnect sends <path> back to the recovery point ..., discarding whatever was written there after it. To keep them, copy them to <target host> over a protocol first; to discard them, run again with -e '{"replication_failover_allow_newer_writes": ["<host>"]}'.` | A resync's reconnect found writes on the source host after the recovery point. Nothing was changed. Copy the files to the target, then run again with the source host named in `replication_failover_allow_newer_writes`, or name it to discard them. |
| `<name>: <host>'s <path> changed since the recovery point ...: N changes (...). ... Run the failback again, which resyncs them to <source host> first.` | A failback's reconnect on the target found writes made after the wait. Stop the writes on the target and run the failback again. Do not name the target in `replication_failover_allow_newer_writes`. |
| `<name>: ... and the record names no recovery point snapshot of <path> to compare it with; run again with -e '{"replication_failover_allow_newer_writes": ["<host>"]}' to reconnect without comparing` | The reconnect cannot compare, since the record names no snapshot of that directory. Check by hand that nothing was written there, then run again with the host named in `replication_failover_allow_newer_writes`. |
| `<source host> is running a job of <name>, started ...; a failover now discards the changes it carries. Run again once the job ends; ... run with -e replication_failover_allow_partial_job=true` | The source answers and shows a job running. Run again once the job ends, or lift the refusal when the job makes no progress. With the source not answering the failover goes on and notes the job instead. |
| `the relationship <name> has no recovery point on <target host>: its first job has not finished, so its target cannot be made writable` | The first job never finished, so the target holds no consistent copy. There is nothing to fail over to. |
| `<target host> holds no record of <name>: no target record from <source host>'s UUID ... of ..., and no source record of ... to it; there is nothing to fail over` | The target has no record of the declared key in either direction. Check the two paths and `qumulo_cluster_uuid` of both hosts against `replication_info` on the target. |
| `<target host> holds a record of <path> from a previous UUID of <source host> (id ...): <source host> was rebuilt, and that record holds the replica a restore brings back. Restore it with playbooks/replication_restore.yml, not a failover` | The source was formed again and its old record holds the replica. Run the restore ([Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md)). |
| `<target host> holds records of <name> in both directions (ids ...); a failover acts on one relationship, so delete the one that is not wanted first` | Both directions are live, or one direction holds two records. Delete the record that is not wanted. |
| `the relationship <name> is <state>; a failover acts only on a relationship that has replicated` | The target's record, or in a reversed relationship the source's, is AWAITING_AUTHORIZATION or ENDED. Authorize it, or declare it again, as [Replicate a directory](../how-to/replicate-a-directory.md) shows. |
| `the relationship <name> is <state>, which <action> does not start from; run <action>` | The action does not start from that state. The message names the one that does, such as `failover` from `replicating` or `failback` from `resynced`. |
| `<action> needs <source host>, which did not answer: ...` | A resync or failback needs both clusters. The run reads the source that did not answer once and refuses every relationship at once. Bring the source back and run again. A failover goes on without it. |
| `<source host>'s source record of <name> is <state>, while <target host>'s target record reads ESTABLISHED; <source host>'s record cannot follow it back, so the <action> has nothing it can move. To replicate again, ...` | A resync or failback meets a source record that is ENDED or AWAITING_AUTHORIZATION beside a target record that reads ESTABLISHED. The source's record cannot follow the target's back to ESTABLISHED, so the role refuses instead of waiting for it. For ENDED, declare the relationship again: the baseline then reports row O3 on it, which `recreate` on the entry and a release naming O3 lift ([Replicate a directory to another cluster](../how-to/replicate-a-directory.md)). For AWAITING_AUTHORIZATION, delete that record on the source host, then run the baseline, which creates the relationship again. |
| `A restore of <names> is under way: its restore record is in <dir>/<source host>/restore/. Finish it with playbooks/replication_restore.yml before a <action>. If the relationship was reconnected by hand, remove the record.` | A resync or failback refuses a relationship whose restore record is there: the restore is under way from this controller, and past the old record's delete the relationship reads as failed over, so the action would turn it away from the restore. Finish the restore with `playbooks/replication_restore.yml`, which removes the record. When you reconnected the relationship by hand instead, [remove the record](../how-to/restore-replica.md#find-and-clear-restore-records-and-locks). A failover notes the record and goes on ([why](../explanation/replication.md#a-restore-under-way)). `<dir>` is `replication_failover_artifact_dir`. |
| `<source host> holds no record of the relationship <name>; a <action> reverses both records, so it needs both` | The source lost its record, as after it was formed again. Restore or declare the relationship, then run again. |
| `<host>'s qumulo_replication_address, '...', is not an IP address; the <action>'s reverse makes <host> the target and sends it` | A reverse is sent to that address, so it must be an IP literal. A resync needs the source host's. A failback needs the target host's, and the source host's too when it runs the resync's steps first. Give it. A failover needs neither. |
| `Replication into <path> connects to <address>, ... has replication_enabled: false. ... The <action> makes <host> the target of <name>.` | The tenant that serves that host's replication address has replication off. The action would make that host a target, and the server would refuse the reverse with a connection error. Turn replication on in the tenant, as [The replication address](#the-replication-address) says. |
| `replication_failover_relationships names no relationship.` | The variable is empty. Name each relationship as `<target host>:<target path>`. |
| `Each name is <target host>:<target path> of a present entry of <host>'s qumulo_replication_relationships, and ... matches none.` | A name in `replication_failover_relationships` matches no `present` entry of the source host. Correct it. |
| `The target hosts ... are not in the inventory.` | The `target` of a declared entry names no inventory host. Add the host, or correct the entry. |
| `The hosts ... have no qumulo_cluster_uuid, which the relationship's records are matched by.` | Give the source and each target host its UUID. |
| `The run acts on one source host's relationships, and this run names <N>: ... Limit it with -l <source host>.` | The playbook's first task. Pass `-l` with one source host. |
| `<N> of the <M> relationships were refused, and nothing was changed. ...` | One or more plans refused. Each reason follows, and nothing was written for any relationship. |
| `<name>: a step is due that the plan printed before the run did not list; run again to read it` | The first plan listed no step, so the run needed no consent, and a later read found a verb due, as when the relationship changed under the run. The run checks the consent before each verb, and sent nothing. Run again: the plan prints the step, and the run then needs `replication_failover_allow_data_loss`. |
| `<name>: the wait "..." ended after <N> seconds; <cluster>'s <side> record read ..., job ..., waiting for ... Nothing was sent; run again to resume from the records.` | A record did not reach its state within `replication_failover_wait_timeout`. Look at the job state named, as a source that cannot reach its target explains it, then run again. |
| `<N> relationships on <host> had no recovery point taken after the writes stopped within <N> seconds: <name> at ..., job ..., last error ... Nothing was sent; ...` | A failback's wait for the recovery point to move twice ran out. Nothing was sent. Check why the jobs do not complete, as the last error says, and run again. |
| `<name> still had steps left after twelve rounds, the next <step>; run again to resume from the records.` | The relationship kept changing under the run. Run again. |
| A source read fails with a login or identity error in a failover. | Only a source that does not answer, or answers 502, 503 or 504 after its retries, is skipped. A refused login or an identity mismatch fails the run, since the cluster answered. Fix the credentials or the UUID. |
| `... the relationship is reversed, a failover state that the replication_failover role's failback turns back, and nothing here lifts it` (rows O8 and O9) | The baseline met a relationship that runs from the target to the source. Run the failback; until then the baseline fails on that relationship. |

## Upgrades

The upgrade's refusals have codes, F1 to F34, and its warnings W1 to W12; the report gives each beside its message.
Every URL in a message is printed without its query string or user information. [The upgrade](upgrade.md) lists each
setting a message names.

A message names the setting to change as the playbook sets it: the role's `cluster_upgrade_wait_timeout` and
`cluster_upgrade_reboot_timeout`, and the inventory's `qumulo_upgrade_reboot_set_size`,
`qumulo_upgrade_maintenance_windows` and `qumulo_upgrade_window_timezone`. The [upgrade](../../plugins/modules/upgrade.py) module run on its
own, without `host_name`, names its own options in their place: `wait_timeout`, `reboot_timeout`, `reboot_set_size`,
`maintenance_windows` and `window_timezone`. The tables quote the playbook's form.
Many refusals end with the limit that lets the rest of the fleet go on: `To upgrade the rest of the fleet meanwhile,
leave ... out of the run with --limit.` For a member of a portal component it reads `leave ...'s whole portal component
out of the run with --limit`, since a limit that splits a component is refused (F13).

### Refusals before anything is sent

The plan refuses these from the inventory and the play's reads, all at once, and the run stops before its first
prepare. The module run on its own refuses the ones that concern its cluster.

| Code | Message | What lifts it |
|---|---|---|
| F1 | `... runs Qumulo Core ..., a limited release, and Qumulo Core refuses to upgrade from a limited release unless the rule is waived. Contact Qumulo Care. Set qumulo_upgrade_allow_limited_release: true for ... only when Qumulo Care directs it. To upgrade the rest of the fleet meanwhile, ...` | Qumulo Care's direction, then the option for that host, with the first hop's build below 7.11.0 ([Upgrade from a limited release](../how-to/upgrade.md#upgrade-from-a-limited-release)). |
| F2 | `...: the image ... is neither an http:// or https:// URL nor an absolute path. Give a URL every node can reach, or an absolute path in the cluster's file system.` | Correct the entry in `qumulo_upgrade_images`. |
| F3 | `... runs Qumulo Core ..., above the target .... The collection never downgrades: set qumulo_upgrade_target to ... or later, or leave ... out of the run with --limit.` | A target at or above the running release. For a portal member the message reads `set qumulo_upgrade_target for ... to ... or later, or leave ...'s whole portal component out of the run with --limit`. |
| F4 | `... on ... cannot reach ... with this images list: .... ....` | The break and its fix, below. |
| F6 | `... is ... with the image ..., started outside this run. Wait for it to finish, then run the upgrade again, or, to upgrade the rest of the fleet meanwhile, ...` | An upgrade started from the Web UI, `qq` or another controller is preparing, committing or committed with another image. Let it finish. |
| F6 | `... is preparing an upgrade that commits itself as soon as it is prepared, outside this run's order. Wait for it to finish, then run the upgrade again, or, to upgrade the rest of the fleet meanwhile, ...` | As above: an upgrade started elsewhere commits itself when prepared. |
| F10 | `... is an on-premises cluster, but its image for ..., ..., is a cloud image. Give an on-premises image, or correct qumulo_cluster_kind for ....` | The list's entry of the host's kind for that release, or the right `qumulo_cluster_kind`. The kinds may be the other way round: `a cloud cluster ... an on-premises image`. |
| F11 | `qumulo_upgrade_reboot is aggressive in the inventory for .... Aggressive reboots are chosen for one run only, with -e qumulo_upgrade_aggressive=true; set qumulo_upgrade_reboot to rolling or all.` | `rolling` or `all` in the inventory. |
| F11 | `Aggressive reboots have not yet run live on a cluster they apply to, so this collection refuses qumulo_upgrade_aggressive. Run without it, and the reboots run as rolling.` | Run without `-e qumulo_upgrade_aggressive=true`. The refusal stands until a live run lifts the hold. |
| F12 | `...'s portal component would end the hop to ... on two releases: ... on ..., ... on .... Give every member of the component an image of the same release for this hop, and the same qumulo_upgrade_target.` | One target and the same releases in every member's list. |
| F13 | `...'s portal component has members outside this run: ... (...). Add them to the limit, or fix what stopped their read; members already at the target are skipped.` | The whole component in the limit, or the failed read fixed. |
| F14 | `... declares no qumulo_upgrade_target, while ... other hosts of this run do. Set qumulo_upgrade_target for ..., or leave ... out of the run with --limit.` | A target for the host, or a limit without it. For a portal member the message adds `and it belongs to ...'s portal component` and says to leave the whole component out. |
| F15 | `The images for ... include both kinds, and ... has no qumulo_cluster_kind. Set qumulo_cluster_kind for ... to cnq, edge or baremetal.` | The host's kind. It is asked for only when the host's protection could not be read. |
| F16 | `... runs ..., below 7.11.0, where Qumulo Core's waiver lifts every compatibility rule, so the collection sends it only for a declared build. Declare the build Qumulo Care names for ...'s first hop as build in that hop's mapping entry, in a qumulo_upgrade_images list of ...'s own. To upgrade the rest of the fleet meanwhile, ...` | The build Qumulo Care names, in a mapping entry of the host's own list ([Upgrade from a limited release](../how-to/upgrade.md#upgrade-from-a-limited-release)). |
| F18 | `qumulo_upgrade_after on ... puts ... before its spoke .... Change qumulo_upgrade_after on ....` | An order that keeps spokes before their hub. |
| F18 | `qumulo_upgrade_after on ... orders ... and ..., members of one portal component, whose order the portal graph sets. Change qumulo_upgrade_after on ....` | No `qumulo_upgrade_after` between members of one component. |
| F18 | `qumulo_upgrade_after on ... closes a cycle, .... Change qumulo_upgrade_after on ....` | Break the cycle among the hosts named. |
| F18 | `qumulo_upgrade_after on ... names ..., which is no inventory host. Change qumulo_upgrade_after on ....` | Name an inventory host. |
| F19 | `...: a commit record, ..., shows that its commit to ... on ... did not finish, and some nodes may be armed. Nothing was sent. Contact Qumulo Care. Once they have cleared ..., remove the record; it also goes by itself once ... runs a new build of ... or a later release. To upgrade the rest of the fleet meanwhile, ...` | Qumulo Care first, then the record removed ([A commit that did not finish](../how-to/resume-an-upgrade.md#a-commit-that-did-not-finish)). An `unfinished` record never goes by itself, and its message has no `it also goes by itself` clause. |
| F20 | `...: qumulo_upgrade_reboot_set_size is ..., above ...'s configured node fault tolerance of ..., which Qumulo Core would refuse at the prepare. Set it to ... or lower for ..., or leave it unset so the server sizes the groups.` | Leave the set size unset, or lower it. |
| F22 | `... shows a restore of ... under way since ..., by ansible-playbook process ... on ..., with a lease until .... Run again once it ends or its lease runs out; remove the file only if that process is gone.` | The restore lock's own refusal: another run holds the host's restore lock, a restore, or `an upgrade of ...` for another upgrade run. Wait for it; that host and the hosts it replicates to are held, and the rest of the fleet goes on ([A restore lock another run holds](../how-to/resume-an-upgrade.md#a-restore-lock-another-run-holds)). |
| F30 | `...'s commit to ..., recorded at ..., is under way (it reads ...). Wait for it to finish, then run the upgrade again; the record goes by itself once ... runs ... on a new build. To upgrade the rest of the fleet meanwhile, ...` | The commit to finish. `no answer` in the parentheses means the read failed: check the cluster is up. |
| F31 | `... is not a commit record this collection can read: .... Check whether a commit of ... is unfinished with Qumulo Care, then remove the file. To upgrade the rest of the fleet meanwhile, ...` | Qumulo Care's answer, then the file removed. |
| F33 | `...: the image ... carries a query string or user information in image. Pass those parts in image_secret and the rest in image.` | The module run on its own: pass the URL through `qumulo.cluster.redact_images` as `image` and `qumulo.cluster.url_secret` as `image_secret`, as its examples show. |
| F34 | `... declares qumulo_upgrade_maintenance_windows but no qumulo_upgrade_window_timezone. Set qumulo_upgrade_window_timezone for ... to an IANA name, such as America/Los_Angeles.` | The windows' timezone. |
| F34 | `... declares qumulo_upgrade_window_timezone ..., which is no IANA timezone name. Set qumulo_upgrade_window_timezone for ... to an IANA name, such as America/Los_Angeles.` | A name from the controller's timezone database. |
| F34 | `... declares qumulo_upgrade_maintenance_windows with ... ..., which is no whole number from 0 to .... Set each range's ... for ... to a whole number from 0 to ....` | An hour from 0 to 23 or a minute from 0 to 59 in each range's `start_hour`, `start_minute`, `end_hour` and `end_minute`. |
| F34 | `... declares qumulo_upgrade_maintenance_windows with on_days ..., which is no day name. Name each range's days for ... from SUN, MON, TUE, WED, THU, FRI, SAT and EVERY_DAY.` | Day names from the list. |

F4's break and fix, after the colon, are one of:

- `the quarter ... has no image the rule accepts. Add an image of ... or one of its dot releases.`: the list skips a
  quarter, whether or not any cluster runs it or any entry names it. Add the quarter's quarterly build.
- `the only entry for ... is a later patch. Add ... or one of its dot releases.`: a hop into a quarter lands on its
  x.y.0 release or a dot release of it, never on a later patch.
- `the only acceptable entry is a letter release before the last hop. Use the release without the letter.`
- `the entries are out of order. List them as ....`: each kind ascending by release.
- `two entries are ... on-premises images. Keep one.`, or cloud images.
- `no entry is the target. Add an image of ....`
- `... already runs ... on build ..., not the build ... its mapping entry declares. Correct the entry's build with
  Qumulo Care; the collection does not move a cluster between builds of one release.`: a build declared in the group's
  list reaches every host of that release. Declare a letter host's build in a list of its own.

An entry the plan cannot read is refused before F4, with these messages:

| Message | Cause and fix |
|---|---|
| `the qumulo_upgrade_images entry ... names no release: its file name is not qumulo_core_<version>.qimg or cloud_qumulo_core_<version>.qimg. Give it as a mapping, {version, image, kind, build}.` | Give the entry as a mapping. |
| `the qumulo_upgrade_images entry ... is neither a URL, a path, nor a mapping {version, image, kind, build}.` | Give a string or a mapping. |
| `the qumulo_upgrade_images entry for version ... needs image, a URL or a path.` | Add `image` to the mapping. |
| `the qumulo_upgrade_images entry ... gives version ... as a number, which YAML may change; quote it, as "...".` | Quote the version: YAML reads `7.10` as `7.1`. |
| `the qumulo_upgrade_images entry ... needs kind, on-prem or cloud, since a path's name may not carry it.` | Add `kind` to the mapping. |
| `... declares qumulo_cluster_kind ..., which is none of cnq, edge or baremetal. Set qumulo_cluster_kind for ... to cnq, edge or baremetal.` | Correct the kind. |

### Refusals during a hop

| Code | Message | What lifts it |
|---|---|---|
| F5 | `... cannot be upgraded now: .... Bring the node or disk back into the cluster, then run the upgrade again.` | The preflight names a node offline or a disk failed or missing, outside a reboot. Bring it back. |
| F5 | `... still reads blocked after ... seconds: .... On a large file system the background work can take longer; run the upgrade again, which picks up where it stopped, or raise cluster_upgrade_unblock_timeout.` | Run again later. Step 1's wait names `cluster_upgrade_unblock_timeout`; the module's own wait, at a prepare, names `cluster_upgrade_wait_timeout`. |
| F7 | `...: Rolling reboot must reboot ... or fewer nodes at once due to the current node fault tolerance. The tolerance did not return within ... seconds. Run the upgrade again once the cluster has finished reprotecting, raise cluster_upgrade_wait_timeout, or lower qumulo_upgrade_reboot_set_size for ....` | The declared set size is above the current fault tolerance. Wait for the cluster to reprotect, or leave the set size unset. A prepare Qumulo Core refused for the count reads `...: Rolling reboot must reboot ... ` with Qumulo Core's own words, then the same advice. |
| F8 | `... cannot upgrade in band: Qumulo Core reports that this deployment has no installer for it. The collection does not upgrade such a deployment; upgrade it as its deployment documents.` | None in the collection: a Qumulo Core container is replaced, not upgraded. |
| F9 | `... were not committed: the hop to ... is outside their maintenance window, which next opens ... .... They stay prepared. Run the upgrade again inside the window.` | Run again inside the window ([Keep commits inside a maintenance window](../how-to/upgrade.md#keep-commits-inside-a-maintenance-window)). In a check run the report gives it as a prediction. |
| F17 | `... is not PREPARED with ..., its reboot settings and its waivers (it reads ...), so it is not committed. Run the upgrade again; it prepares ... first.` | The prepare was lost, as at a quorum change, before the cluster's commit. Run again. If a host with `qumulo_upgrade_reboot_set_size` unset refuses F17 again right after its own prepare, set `qumulo_upgrade_reboot_set_size` for it for the run, and tell the collection's maintainers. |
| F21 | `...: verify reads the image ... as Qumulo Core ..., but the images list gives it as .... Nothing was prepared. Correct the entry's file name or version, then run the upgrade again.` | The image is another release than its entry says. Correct the entry. |
| F21 | `...: the image ... holds Qumulo Core ..., but the images list gives it as .... Nothing was committed, and ... stays PREPARED with that image until its next prepare or a quorum change. Correct the entry's file name or version, then run the upgrade again.` | As above, found once the prepare read the image. |
| F23 | `...: its current node fault tolerance reads ... of its configured ... after ... seconds, and an all-at-once reboot waits for a full reprotect with every node armed. Nothing was committed. Run the upgrade again once the cluster has finished reprotecting, or raise cluster_upgrade_wait_timeout.` | Wait for the cluster to reprotect. With `, while its protection changes to ...` after the seconds, the cluster is changing its stripe configuration, which can keep the tolerance below the configured one after reprotect; run again once the change is done. |
| F24 | `... still reads REBOOTING after ... seconds, with ... of ... nodes rebooted. Wait for the reboot to finish, then run the upgrade again, or raise cluster_upgrade_reboot_timeout.` | Let the reboot finish. |
| F25 | `...'s reboot is paused, and this run did not pause it. Follow "Resume a paused reboot" in the collection's docs/how-to/resume-an-upgrade.md, then run the upgrade again.` | [Resume a paused reboot](../how-to/resume-an-upgrade.md#resume-a-paused-reboot). |
| F26 | `...'s reboot stopped on an error: .... Contact Qumulo Care before running anything else on ....` | Qumulo Care. Do not resume the reboot. |
| F27 | (none: reserved) | Reserved for aggressive reboots, which are refused for now (F11). |
| F28 | `...: the prepare's answer was lost, and after ... seconds Qumulo Core still reads ..., so the prepare may or may not have been applied. A verify that Qumulo Core refused leaves the same status. Every node of ... must reach ... from its own network. Run the upgrade again, which reads the status before it prepares.` | Check that every node reaches the image, then run again. The prepare is never sent twice. |
| F29 | `...: verify's answer was lost; nothing was installed. Run the upgrade again.` | Run again. |
| F32 | `...'s commit to ... was sent, and after ... seconds it reads ... on build .... Nothing more was sent. The record ... stays: the next run waits for the commit to finish, or retires the record. Run the upgrade again once ... reads ..., or raise cluster_upgrade_wait_timeout.` | The commit may still finish. Run again once the cluster reads the target. Without `commit_record`, the middle sentence reads `No commit record was written, since commit_record was not set.` |

A refusal Qumulo Core makes itself, such as an unlicensed edge's, reads `...: Qumulo Core refused the image ...: ...`
with Qumulo Core's words. For a URL image, when the words could come from a node that did not reach the URL, as
"Malformed qimg", a download timeout or a server error, the module adds:

```text
Every node of ... must reach ... from its own network; check each node's route to it.
```

Each node must resolve the URL's host and reach it from its own network, not only the controller.

Other messages of a hop:

| Message | Cause and fix |
|---|---|
| `...: the prepare of ... stopped: Qumulo Core reads IDLE with no error, as after a quorum change. Run the upgrade again.` | A quorum change cancelled the prepare. Run again. |
| `...: the commit record ... could not be written (...). Nothing was committed: make its directory writable, then run the upgrade again.` | Fix the directory under `cluster_upgrade_artifact_dir`. |
| `... commits outside the maintenance window, to close the split of ...'s portal component.` | A report line, not a refusal: the member brings a component that an earlier run left on two releases back to one. |
| `...'s portal component is held by the maintenance window, and stays split: the hop to ... would not close its split.` | A report line: the component stays on two releases until a run inside the window. Run again inside the window. |
| `The upgrade stopped in hop ..., to ..., and ran no later hop: ... failed; ... held. The report under ... gives each host's outcome.` | The playbook's last message when a hop stopped the run. After the colon it names the hosts that failed and the hosts held, or `the hop was outside the maintenance window`; after the last hop, `, and ran no later hop` is left out. When a restore lock the run held could not be renewed, the message is the role's reason instead: `The run stopped before the hop to ...: ...'s restore lock could not be renewed: .... The report under ... gives each host's outcome.` Read the hop in the report, then follow [Resume an upgrade that stopped](../how-to/resume-an-upgrade.md#resume-an-upgrade-that-stopped). |
| `The cluster_upgrade role runs only in a linear play without serial, but this play's batch is ... of .... A hop commits a portal component's waves and holds a failed host's unit across every host of the play at once; run it without serial.` | A play of your own that calls the [cluster_upgrade](../../roles/cluster_upgrade/) role: remove `serial`. |
| `qumulo_upgrade_reboot_set_size is ... for ...; it must be at least 1, or unset so Qumulo Core sizes the groups.` | Leave it unset. |

A host given with `-i` as a list has no artifact directory for its commit record, its restore lock and the report, and
the run refuses it before it reads any file:

```text
... has no artifact directory, as for a host given with -i as a list. Run from an inventory file or directory, or set
edge_rebuild_artifact_dir, where the restore locks are, and cluster_upgrade_artifact_dir, where the commit records and
the reports are.
```

### Restore locks

Besides its refusal (F22), the restore lock prints these in an upgrade run. A renewal's refusal follows `...'s restore
lock could not be renewed: ` in the run's last message and in the hop's report.

| Message | Cause and fix |
|---|---|
| `Took over ..., left by ansible-playbook process ... on ..., which started at ... and is gone.` | A warning: a run on this controller ended without releasing the lock, as one stopped with Ctrl-C does, and this run holds it now. Nothing to do. |
| `Took over ..., left by ansible-playbook process ... on ..., whose lease ran out at ....` | A warning: the lock's run, on another controller or one without `/proc`, did not renew it within its lease, and this run holds it now. Check that the run has ended. |
| `Could not renew ...: this run's lock is gone, removed while the run held it. Run again once no other run on ... is under way.` | The lock file was removed during the run, and the run stopped before the hop. Check that no other run or restore of that host is under way, then run again. |
| `Could not renew ..., which now shows ... of ... under way since ..., by ansible-playbook process ... on ..., with a lease until .... Run again once it ends or its lease runs out.` | Another run took the lock over, as one on another controller can once this run's lease has run out, and the run stopped before the hop. Wait for that run to end, then run again. |
| `F22, predicted: ... The run would hold ....` | A check run's prediction: the run would fail that host with F22 and hold the hosts named. Any other failure of the take, as a lock it cannot read, reads `Predicted: ... The run would hold ....`, and fails the host the same way. |
| `...: the run would take over ..., whose ansible-playbook process is gone or whose lease has run out.` | A check run's prediction: the run would take the lock over with one of the warnings above. Nothing to do. |
| `Could not list ...: ...` | Before the plan, the scan of the restore files could not list an artifact directory or a `<host>/restore/` directory under it, and the run stopped. Give the controller's user read access to it, or correct `edge_rebuild_artifact_dir`. |
| `... cannot be read as a restore record or lock: ...` | A warning before the plan: a file under `<host>/restore/` named like a record is not one this collection reads, or a `lock` is not a lock of format 1. The upgrade goes on, and it is not W1; a restore that reads the file stops on it. [Find and clear restore records and locks](../how-to/restore-replica.md#find-and-clear-restore-records-and-locks) says what to do with it. |

### A commit that did not finish, or ended on another build

The commit is decided from the build the cluster runs, never from its upgrade status alone. Two failures come after the
commit was sent, and both say the commit cannot be taken back:

```text
...: the commit did not finish. The cluster still runs ... and its upgrade status is IDLE (...). Some nodes may be
armed to start ... at their next restart, and Qumulo Core's own guidance for this state is manual intervention.
Contact Qumulo Care before running anything else on .... The collection has recorded this at ..., and the next run
stops on it until Qumulo Care has cleared ... and the record is removed. To upgrade the rest of the fleet meanwhile,
...
```

Contact Qumulo Care before running anything else on that cluster, then follow [A commit that did not
finish](../how-to/resume-an-upgrade.md#a-commit-that-did-not-finish). Run without `commit_record`, the module ends the
message with `No commit record was written, since commit_record was not set; run the next upgrade of ... only once
Qumulo Care has cleared it.` When the record could not be marked unfinished, as when its directory cannot be written,
the failure is still the commit's, and its message ends with this in place of `The collection has recorded this ...`:

```text
The collection could not record this at ... (...), but the record written there before the commit stays, and the
next run stops on it until Qumulo Care has cleared ... and the record is removed. To upgrade the rest of the fleet
meanwhile, ...
```

The record written before the commit is the guard then: the next run refuses the host (F19) until Qumulo Care has
cleared it and the record is removed. The directory must be made writable as well.

```text
... runs Qumulo Core ... build ..., not the declared build .... The commit is done and cannot be undone. Check the
build in qumulo_upgrade_images' entry for ..., and the image, with Qumulo Care.
```

The cluster is on the release it was meant to reach, on another build than the images list's mapping entry declares.
The entry and the image are for Qumulo Care to check.

### Warnings

None of these stops the run. Each is printed and kept in the report.

| Code | Message | Cause and fix |
|---|---|---|
| W1 | `...: an open restore record, ..., shows that the restore of ... on ... has not finished. The upgrade goes on, and restarts ... between two of the restore's steps. Afterwards, run ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l ..., which finishes the restore and removes the record. If the relationship was reconnected by hand, remove the record.` | A restore record is left under `<artifact dir>/<edge>/restore/` ([The restore's files](playbooks-and-roles.md#the-restores-files)), and the run moves or reboots the edge, the record's target host or a target of the edge's present relationships. Printed once for each such host. Finish the restore after the upgrade, as the message says. [Find and clear restore records and locks](../how-to/restore-replica.md#find-and-clear-restore-records-and-locks) shows how to read the record and when to remove it by hand. |
| W1 | `...: a live restore lock, ..., shows a restore of ... under way, by ansible-playbook process ... on ..., with a lease until .... The upgrade goes on, and may restart ... between two of the restore's steps. Afterwards, if that run stopped, run ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l ..., which finishes the restore and removes the record.` | A run holds the restore lock of a host outside this run, and this run moves or reboots one of that host's `present` targets (W9 names the same pair). Another holder's name stands in place of `restore`, after `a` or `an`. Let that run end, or finish its restore as the message says if it stopped. A lock whose run is gone or whose lease has run out is taken over, not warned about here. [Find and clear restore records and locks](../how-to/restore-replica.md#find-and-clear-restore-records-and-locks) shows how to tell which. |
| W1 | `...: a live restore lock, ..., shows another upgrade of ... under way, by ansible-playbook process ... on ..., with a lease until .... This run upgrades ... meanwhile.` | Another upgrade run holds the lock of an edge this run does not lock, and this run moves or reboots one of its hosts. An upgrade has no restore steps and leaves no restore record, so nothing is left to finish; check that the other run is meant to go on, as [A lock another run holds](../how-to/restore-replica.md#a-lock-another-run-holds) describes. |
| W2 | `... reads license .... Qumulo Core refuses to upgrade an unlicensed edge formed more than 30 days ago, and then its portal component waits for it. License it with ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l ... before the run.` | License the edge first ([License an edge](../how-to/licensing.md#license-an-edge-before-an-upgrade)). |
| W3 | `... replicates to ..., which would be ... quarterly releases apart .... Replication refuses clusters about eight quarters apart.` | Upgrade the far end in the same run, or accept that the relationship stops until both ends are closer. |
| W4 | `... has ... live hub portals, and the inventory declares ... spokes for it. A spoke the inventory does not declare is not ordered before its hub, and its portal stays down until it matches.` | Declare the spoke's `qumulo_portal`, or upgrade it yourself before the hub. |
| W5 | `... runs ..., older than ..., the oldest release the collection's tests have upgraded from (...).` | The upgrade goes on; Qumulo Core's verify is the real check. Tell Qumulo Care. |
| W6 | `forks is ..., below this hop's ... clusters or its largest commit step of .... Prepares and a step's commits run in batches, which lengthens the hop and its components' mismatch windows. Raise forks to at least ....` | Raise `forks`. With `cluster_upgrade_prepare_throttle` set below it, the warning names the throttle. The role prints it at the hop's start, and the report does not keep it. |
| W7 | `... has ... live spoke portals, and the inventory declares .... Its hub is not ordered after it, and the portal stays down until the two match.` | Declare the host's `qumulo_portal`. |
| W8 | `Aggressive reboots do not apply to ...'s hop to ...: .... Its reboots run as ....` | Printed only with aggressive reboots, which are refused for now (F11). |
| W9 | `..., outside this run, replicates to ..., which this run upgrades. The run cannot hold ...'s restore lock, so a restore of ... started meanwhile is not held off, and the upgrade would restart ... between two of its steps.` | Start no restore of that source during the run, or add it to the run. |
| W10 | `...'s configured node fault tolerance is 0, so a platform upgrade of ... reboots every node at once, whatever qumulo_upgrade_reboot says; its qumulo_upgrade_reboot_set_size of ... is ignored.` | Nothing: an unprotected edge, or a cluster of fewer than three nodes on object storage, reboots all at once. Without a declared set size the message ends at `says.` |
| W10 | `qumulo_upgrade_reboot is all for ..., so its qumulo_upgrade_reboot_set_size of ... is ignored.` | Remove the set size. |
| W11 | `The maintenance windows of ... share no time, so a hop that moves them all can never begin its commits. Give them a common range, or upgrade them in separate runs with --limit.` | A range the hosts share, or separate runs. |
| W12 | `... replicates to ..., which this run leaves out, so ... may upgrade ahead of its target and hold new kinds of state back from it until each file changes again. Add ... to the limit, or upgrade it first.` | Upgrade the target first, or add it to the limit. |

[Upgrade a fleet](../how-to/upgrade.md) and [Recover an upgrade that stopped](../how-to/resume-an-upgrade.md) give the
procedures, and [Upgrades](../explanation/upgrades.md) gives the reasons.

## Adoption

| Message | Cause and fix |
|---|---|
| `...: ... already holds host_vars, and adoption never rewrites them; copy what you keep from ... yourself` | Copy from the capture by hand, or remove the file and adopt again. |
| `...: it is in each of ... and ..., so adoption cannot tell which host_vars directory to write; give -i the one source that defines it` | Give `-i` only one of the sources. When no source defines the host, `no inventory file or directory defines it` replaces `it is in each of ... and ...`; give `-i` an inventory file or directory that defines it. |
| `... comes from no inventory file or directory; set qumulo_capture_dir` | The host is not from an inventory file or directory, as with a host given to `-i` as a list, so the capture has nowhere to go beside. Run from an inventory file or directory, or set `qumulo_capture_dir`. |
| `... is ...: it belongs to no cluster yet, so there is nothing to capture; form the cluster first` | The node is not formed, so it has no UUID or settings to capture. Form the cluster first. |
| `...: the inventory contradicts what adoption would write, so no host_vars file was written: ...` | The inventory contradicts what adoption would write. The message lists each entry: a network both declare; a share or export the inventory declares with other values, as "with other values of ..."; and, when the inventory defines `qumulo_tenants`, a tenant it declares with other switches or settings than the capture, as "which the inventory declares in qumulo_tenants[...] and the capture with other values of ...", or a captured tenant network it does not list for that tenant, or lists with other values. For a network both declare, take it out of the inventory variable the message names. For a tenant network the inventory does not list, or lists otherwise, list it under that tenant in the inventory's `qumulo_tenants` as the capture holds it. When `qumulo_tenants` is `[]` in a group's variables, remove it from them, so that adoption writes the captured one; when the group's list is one other hosts need, give this host its own `qumulo_tenants` in its inventory entry, starting from the one in the capture file, which replaces the group's for this host. Beside `qumulo_tenants: []`, a network of a tenant refuses unless the inventory lists it, and a single-tenant cluster's networks belong to `Default`. For each share, export or tenant, give the inventory's entry the values the capture file holds, change it on the cluster to the inventory's values, or take the entry out of the inventory. Then adopt again. No `host_vars` file was written; the capture file was. |

[Adopt a cluster you already run](../how-to/adopt-a-cluster.md#when-adoption-refuses) gives the procedure.

## Cloning

`playbooks/clone_edge.yml` refuses before it changes anything on the target. The one refusal that follows a write is
the missing secret, which comes after the host_vars file is written.

| The clone refuses | Message | Cause and fix |
|---|---|---|
| A run without the source, the target or the name | `give the cluster to clone in clone_edge_source, the fresh cluster's inventory host in clone_edge_target, and the clone's new name in clone_edge_name` | Give all three. |
| A target outside `qumulo_clusters`, or the source as the target | `clone_edge_target must name the fresh cluster's own host in the qumulo_clusters group, not ...` | Name the fresh cluster's own host in `qumulo_clusters`. |
| A source outside the inventory without an address | `... is no inventory host; add it to the inventory, or give its address in clone_edge_source_api_host and its credentials in the other clone_edge_source_* variables` | Add the source to the inventory, or give its address and credentials in the `clone_edge_source_*` variables. |
| A target that is not formed | `...: it is ... and belongs to no cluster yet; form it first, as Terraform or the installer does` | Form it first. |
| A target whose UUID is another inventory host's | `...: its UUID, ..., is the qumulo_cluster_uuid of ..., so it is not a fresh cluster; give the fresh cluster as the target` | Give the fresh cluster's host as the target. |
| A target whose UUID is the source's | `the target's UUID, ..., is the source's: the target is the source itself; give a fresh cluster as the target` | Give a fresh cluster as the target. |
| A source that is not formed | `... is ...: it belongs to no cluster, so there is nothing to clone; give a formed cluster as the source` | Give a formed cluster as the source. |
| A target that holds a local user other than `admin` and `guest`, a domain join, an SMB share other than `Files`, an NFS export other than `/`, a tenant beyond the one formation creates, an S3 bucket or an S3 access key | `the target holds ..., so it is not a fresh cluster; give a fresh cluster as the target, or allow a configured one to clone onto it anyway` | Give a fresh cluster as the target, or set `clone_edge_allow_configured_target=true` to clone onto it anyway. |
| A name that is the source's | `the name ... is the source's, and a name is also the cluster's computer account in the domain; give the clone a name of its own` | Give the target a name of its own in `clone_edge_name`. |
| A name that is another inventory cluster's | `the name ... is already another inventory cluster's, and a name is also the cluster's computer account in the domain; give the clone a name of its own` | As above. |
| An inventory `qumulo_cluster_name` for the target other than `clone_edge_name` | `...: the inventory names it ..., not ...; give that name in clone_edge_name, or take qumulo_cluster_name out of the inventory` | Make the two agree. |
| A target defined in more than one `-i` source, or in none | `...: it is in each of ... and ..., so the clone cannot tell which host_vars directory to write; give -i the one source that defines it, or set clone_edge_host_vars_dir`; for none, `no inventory file or directory defines it` replaces `it is in each of ... and ...` | Give `-i` the one source that defines it, or set `clone_edge_host_vars_dir`. |
| A relative `clone_edge_host_vars_dir` | `clone_edge_host_vars_dir is ...; give it as an absolute path` | Give it as an absolute path. |
| A target with `host_vars` in another file or directory | `...: ... already holds host_vars, and the clone writes only ...; move what you keep into that file once the clone has written it, or into the inventory's host entry or group_vars, remove the rest, then clone again` | Move what you keep into the clone's file once it is written, or into the host entry or `group_vars`, and remove the rest. |
| `clone_edge_check_only` on a target whose inventory declares a portal or replication relationships | `...: its inventory declares a portal, which clone_edge_check_only cannot predict; run the clone with --check --diff instead, which writes no file, or without clone_edge_check_only` (`replication relationships` in place of `a portal` when it declares those) | Run the clone with `--check --diff`, which writes no file and changes nothing, or without `clone_edge_check_only`. The check happens before anything is read or written. |
| A share or export that the target's inventory declares with other values than the file would, or without a key the source's entry sets | `...: the inventory already declares what the clone would write, in a way it cannot leave out, and the baseline refuses a share or export declared twice: ...` | The message lists the keys that differ, as "with other values of ...", and the keys the source's entry sets that the inventory's entry omits, as "with other values of ..., which the inventory's entry leaves out". The target creates each share or export from the inventory's entry alone, so such a key takes its create default. For each entry the message names, give the inventory's entry each such key with the value the source holds, change the share or export on the source to the inventory's values, or take the entry out of the inventory. Then clone again. Nothing was written and nothing changed on the target. |
| A file that the clone would write differently | `...: ... holds other host_vars than this clone writes, and the clone never replaces them; remove the file and pass -e clone_edge_allow_configured_target=true to clone again, since the target is no longer fresh, or manage the target with playbooks/baseline.yml` | Remove the file and run again with `clone_edge_allow_configured_target=true`, or manage the target with the baseline. The file is left as it was. |
| A key in `clone_edge_supply` that no supply line names | `no supply line of the clone's file names ...; give only the secrets its supply lines name` | Remove the key. |
| A first pass that lacks a secret it cannot do without | See below. | Add each secret the message names to `clone_edge_supply`. |

The message for a missing secret names the variables and the file, and comes after the file is written:

```text
edge02: the first baseline needs qumulo_local_users[svc-backup].password, which neither clone_edge_supply nor the
inventory gives; give each in clone_edge_supply, keyed as the supply lines in
/srv/project/inventory/host_vars/edge02.yml name them; nothing changed on the target
```

A run without `clone_edge_supply` writes the file, then stops with this message listing each secret the first pass
needs.

[Clone an edge's configuration onto a new edge](../how-to/clone-edge.md#if-the-clone-refuses) gives the procedure, and
[What capture and clone read and write](capture-and-clone.md) lists what the clone copies and replaces.

## See also

- [Respond to a refusal](../how-to/respond-to-a-refusal.md): how to read a refusal and which flag or record lifts it.
- [Refusals and guards](../explanation/refusals-and-guards.md): why the collection stops before a write.
- [Gap register](gaps.md): the operations the API does not let the collection make idempotent, cited above as `G-...`.
- [Quirk register](quirks.md): server behaviour that differs from what the API documents.
