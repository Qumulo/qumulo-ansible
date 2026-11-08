<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Declare common settings in the inventory

This page has short recipes for the areas of a cluster that take a few lines of inventory each: DNS, the network, roles and users, directories and ACLs, the factory share and export, quotas, snapshot policies, FTP, the Web UI and the audit log.
Each recipe says which file the lines go in, what to run, and what the run shows when it worked.
The [`cluster_baseline`](../../roles/cluster_baseline/) role applies every variable here, and an area whose variable is unset is left alone.

## Before you start

- Have an inventory that reaches your clusters: [Set up an inventory for your clusters](set-up-inventory.md).
- Every recipe puts its lines in `host_vars/<host>.yml` or `group_vars/<group>.yml`.
  A variable in a host's file replaces the same variable in its group's file whole, so a setting that every cluster shares goes in the group's file and one that differs per cluster goes in the host's.
- [Inventory variables](../reference/variables.md) lists every variable the baseline reads, with its step and module.
  The notes in each recipe cover only the facts that decide whether the recipe works.
- When the file already declares the variable, add the recipe's entries to its list or keys.
  A second copy of the variable in one file replaces the first.
- A list in the inventory adds to what the cluster has, except where a recipe says the list is the whole set.
  [State management](../explanation/state-management.md#lists-add-dictionaries-set-the-keys-they-give) explains the rule.

## Preview, apply and verify

Every recipe ends in the same three runs.
Replace `edge01` with the host you changed.
For a change in a group's file, run the three steps on one host of the group first, then with `-l` and the group's name, such as `-l qumulo_clusters`.

1. Preview the change.
   On a cluster that already matches the rest of the inventory, only the steps for the area you edited show `changed`, each with a diff of what it would write.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ```

2. Apply it.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ```

3. Run the same command again.
   The recap shows `changed=0`.
   The one exception is a local user declared with `update_password: always`, which is sent and reported as a change on every run.

If a run stops with a refusal, [Respond to a refusal](respond-to-a-refusal.md) says what each kind needs.
[Check and diff](../explanation/state-management.md#check-and-diff) says what a preview does and does not prove.

Add `--ask-vault-pass` or `--vault-password-file` to all three when the inventory holds vault values.

## Network and DNS

### Answer DNS queries for the cluster's own name

The cluster can answer DNS queries for its own name with its floating addresses, one node's address in each answer.

1. Declare the name in the host's file.
   Each cluster has its own name, so this belongs in `host_vars`.

   ```yaml
   # host_vars/edge01.yml
   qumulo_authoritative_dns:
     enabled: true
     fqdn: edge01.corp.example.com
     host_restrictions: [10.0.0.0/8]   # the clients that may ask; [] lets any client ask
     record_ttl_seconds: 0             # seconds a resolver may keep an answer, 0 to 3600
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).
3. Delegate the name to the cluster's floating addresses at your site's DNS.
   The collection does not do this, and clients do not reach the cluster's answers until it is done.

To verify the setting itself, read it back with [network_info](../../plugins/modules/network_info.py), in a play that starts with the `module_defaults` header from [Set up an inventory for your clusters](set-up-inventory.md#check-the-connection):

```yaml
- name: Read the authoritative DNS settings
  qumulo.cluster.network_info:
    gather_subset: [authoritative_dns]
  register: dns_read
```

`dns_read.network.authoritative_dns` holds `enabled`, `fqdn`, `host_restrictions` and `record_ttl_seconds`.
The cluster stores the name with a final dot, so `fqdn` reads back as `edge01.corp.example.com.`.

Variants:

- **Stop answering.**
  Set `enabled: false`.
  Once a name is set the cluster cannot clear it, so leave `fqdn` as it is or leave it out (G-DNS-FQDN-CLEAR in [Gaps](../reference/gaps.md)).
- **A cluster has no floating addresses.**
  It has no address to answer with, so declare its floating ranges first, as in the floating-addresses variant of [Change the network without cutting the play off](#change-the-network-without-cutting-the-play-off).
- **The Terraform provider sets the name.**
  A cluster the provider formed with `cluster_fqdn` gets its `enabled` and `fqdn` from the provider on every run.
  List `dns.fqdn` as Terraform's and leave `enabled` and `fqdn` out of `qumulo_authoritative_dns`; `host_restrictions` and `record_ttl_seconds` stay yours.
  A list replaces the default `[network.floating_ips]` of a provider-formed cluster of kind `cnq` or unset, so keep that entry there:

  ```yaml
  # host_vars/edge01.yml
  qumulo_terraform_owned_areas: [network.floating_ips, dns.fqdn]
  ```

  [Use a Terraform root's output as the inventory](terraform.md) has the setup, and [Terraform ownership](../explanation/terraform-ownership.md#parts-of-an-area-dnsfqdn-and-auditcloudwatch) explains why the areas are split.

### Change the network without cutting the play off

A change that drops `qumulo_api_host` from the cluster's addresses would cut the play off from the cluster, so the baseline refuses it until you accept the move and name the address to use afterwards.

1. Declare the new network in the host's file.
   The list of networks is the whole set: a network the cluster has and the list leaves out is removed.
   An option you leave out of an entry keeps that network's current value.

   ```yaml
   # host_vars/edge01.yml   (edge01's qumulo_api_host is 10.20.0.5)
   qumulo_network:
     frontend_networks:
       - id: 1
         name: Default
         type: STATIC
         netmask: '24'
         default_gateway: 10.20.0.1
         ip_ranges: [10.20.0.6-10.20.0.9]
   ```

2. If the host's `qumulo_portal_hosts`, or another host's `qumulo_replication_address`, names the address you are dropping, change it in the same edit.
   [Pair an edge with its hub through a portal](pair-edge-with-hub.md#choose-the-addresses) says which address to give.
   The portal step runs after the network step, so the same run sends the host's new `qumulo_portal_hosts` to its hub.
   When the changed host is a hub, its spokes learn the new addresses on their own next runs, so run each of them (`-l` with the spoke's name) afterwards.
   A host that declares a replication relationship to the changed host needs its own run too.
3. Preview with the two consent variables on the command line.
   A preview refuses the move without them, too.
   Pass them to the one run that needs them and keep them out of the inventory, where they would apply to every later run.
   Keep `-l` to the one host: a variable given with `-e` applies to every host the run reaches, and `qumulo_api_host_after` moves the connection of any host whose network document changes.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff \
     -e qumulo_allow_management_disruption=true -e qumulo_api_host_after=10.20.0.6
   ```

4. Apply it with the same two variables and the same `-l edge01`.
   The role connects to `qumulo_api_host_after` for every step after the network step.
5. Change `qumulo_api_host` for `edge01` in `hosts.yml` to the new address, so that the next run connects there.

Verify with a run that carries neither variable: it connects at the new address and shows `changed=0`.

Variants:

- **Add floating addresses.**
  Add one key to the network's entry: `floating_ip_ranges: [10.20.0.20-10.20.0.23]`, the addresses that clients and the DNS answers use.
  A cluster the Terraform provider formed, of kind `cnq` or with `qumulo_cluster_kind` unset, with no `qumulo_terraform_owned_areas`, or any cluster whose list names `network.floating_ips`, needs the take-over flag below.

Notes:

- The check compares `qumulo_api_host` with the address ranges, so it can only check a host given as an IP address.
  When `qumulo_api_host` is a name, make sure it resolves to the cluster after the change.
- On a cluster whose network the host manages, such as a container or a host-managed cloud cluster, every network has type `HOST`, and only its name, tenant, netmask and floating ranges can change.
- On a cluster the Terraform provider formed, of kind `cnq` or with `qumulo_cluster_kind` unset, with no `qumulo_terraform_owned_areas`, or on one whose list names `network.floating_ips`, the floating ranges are Terraform's: an entry that gives `floating_ip_ranges` fails before anything is written.
  Set `qumulo_take_over_from_terraform: true` to manage them here.
  The same flag also hands `dns.fqdn` and the audit destinations back to the role.
- [Troubleshooting](../reference/troubleshooting.md#refusals-that-wait-for-consent) quotes the refusal.

## Identity and access

### Declare custom roles and give them to trustees

A custom role is a name with a set of privileges.
`qumulo_roles` declares the roles and `qumulo_role_members` gives them to trustees.
The role creates the roles first, then applies the members, then deletes the roles declared `state: absent`.

1. Declare the role and its members in the group's file or the host's.

   ```yaml
   # group_vars/qumulo_clusters.yml
   qumulo_roles:
     - name: Backup-Operators
       description: Reads snapshots and quotas for the backup service
       privileges: [PRIVILEGE_SNAPSHOT_READ, PRIVILEGE_QUOTA_READ]
   qumulo_role_members:
     - {role: Backup-Operators, members: ['ad:CORP\Backup-Service']}
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).

To read the result back, use [identity_info](../../plugins/modules/identity_info.py) with `gather_subset: [roles, role_members]`.

Rules for the entries:

- Write each member as a trustee in the collection's grammar: `local:svc-backup`, `ad:CORP\Qumulo-Admins`, `ldap_group:admins`, `uid:1000`, `sid:S-1-5-21-...` or `Everyone`.
  A bare name is refused.
  [Identity](../explanation/identity.md#trustees-a-prefix-says-where-a-name-is-defined) explains the prefixes.
- A `local:` member that this run creates, such as a user from `qumulo_local_users`, can fail to resolve for about a minute after a preview looked it up (QUIRK-017 in [Quirks](../reference/quirks.md)).
  Wait a minute between the preview and the apply, or run the apply again.
- An `ad:` trustee resolves only after the cluster has joined the domain, which the role does earlier in the same run.
  `ad:NT AUTHORITY\Authenticated Users` resolves joined or not.
  [Join a cluster to Active Directory](active-directory.md) covers the join.
- `privileges` is the role's whole set.
  The privilege names differ between Qumulo Core versions, and a name the cluster does not know fails before anything is written.
- The built-in roles `Administrators`, `Observers`, `Data-Administrators` and `Qumulo-Support` cannot be changed.
  Name them only in `qumulo_role_members`.

Variants:

- **Remove a role.**
  The cluster refuses to delete a role that still has members, so empty it in `qumulo_role_members` with `purge: true` in the same declaration, then mark the role absent ([why members go first](../explanation/identity.md#roles-and-why-members-go-before-a-delete)):

  ```yaml
  qumulo_roles:
    - {name: Old-Operators, state: absent}
  qumulo_role_members:
    - {role: Old-Operators, members: [], purge: true}
  ```

- **Make a list the whole membership.**
  `purge: true` on an entry makes its `members` the whole set for that role.
  The local `admin` and `system` users always stay in `Administrators`, and `purge` is refused for `Qumulo-Support`.

### Choose when a local user's password is sent

A local password cannot be read back from the cluster (G-PASSWORD), so the collection cannot tell whether the stored one matches.
`update_password` decides when the declared password is sent.

1. Declare the user in the host's file.
   Take the password from a vault value or a lookup: [Keep secrets out of the inventory](store-secrets.md).

   ```yaml
   # host_vars/edge01.yml
   qumulo_local_users:
     - name: svc-backup
       primary_group: Users
       password: "{{ vault_svc_backup_password }}"
       update_password: on_create
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).
   The first apply creates the user.

   With `on_create`, the default, the password is sent only when the user is created, and later runs report no change for it.
   Changing the password of an existing user then takes `update_password: always` for one run.

Variants:

- **Send the password on every run.**
  Set `update_password: always`.
  The module cannot compare the password, so it reports a change for that user on every run, the verification run included.
  Use it for the run that changes the password, then set it back.
- **Change the password of `admin`.**
  The server applies the new password to every node's console and SSH password too (G-ADMIN-OS), and the play connects as `admin`, so the baseline needs both `allow_admin_password_change: true` on the item and `-e qumulo_allow_management_disruption=true`.
  [Rotate the admin password](rotate-admin-password.md) has the playbook that sets both and keeps the play connected. Use it instead.
- **Add a group.**
  Declare it in `qumulo_local_groups`:

  ```yaml
  qumulo_local_groups:
    - name: edge-admins
      members: [svc-backup]   # purge: true makes this the whole membership
  ```

  The role creates the group before the users, since a user's primary group must exist, and sets its members after them ([why](../explanation/identity.md#groups-then-users-then-members)).

## Files, shares and policies

### Set a directory's owner and its ACL

Declare a directory's owner and group in `qumulo_directories`.
Declare an ACL in `qumulo_file_acls`, which is the whole ACL of a path, entries in order.
A mode and an ACL are one fact: setting a mode rewrites the ACL, so give a path one or the other.
The baseline refuses a path that has both before it writes anything (G-FS-ACL-MODE).

Use a mode where it says all you need.
Use an ACL for deny entries, inheritance, or anything beyond owner, group and everyone.

1. Declare the directory and its ACL in the host's file.

   ```yaml
   # host_vars/edge01.yml
   qumulo_directories:
     - {path: /data/projects, owner: 'ad:CORP\svc-projects', group: 'ad:CORP\Engineering'}
   qumulo_file_acls:
     - path: /data/projects
       aces:
         - {type: DENIED, trustee: 'ad:CORP\Contractors', rights: [DELETE, DELETE_CHILD]}
         - {trustee: 'ad:CORP\Engineering', rights: [ALL], flags: [OBJECT_INHERIT, CONTAINER_INHERIT]}
         - {trustee: Everyone, rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]}
   ```

   The first entry that grants or denies a right decides it, so the order matters.
   An entry's `type` is `ALLOWED` unless it says otherwise.
   An owner or trustee written as `local:` that this run creates can fail to resolve for about a minute after a preview looked it up (QUIRK-017 in [Quirks](../reference/quirks.md)).
   Wait a minute between the preview and the apply, or run the apply again.

2. [Preview, apply and verify](#preview-apply-and-verify).
   Once the ACL is in place, a preview with `--diff` shows any drift from it, with the entries the cluster holds ([Check and diff](../explanation/state-management.md#check-and-diff)).

Variants:

- **A mode instead of an ACL.**
  Quote the mode: YAML reads an unquoted `0775` as a decimal number, and the collection refuses it.

  ```yaml
  qumulo_directories:
    - {path: /data/scratch, mode: '0775', owner: 'local:svc-backup', group: 'local:Users'}
  ```

- **Inheritance.**
  The cluster applies a new ACL to the path alone.
  Entries with inheritance flags reach what is created beneath the path afterwards, not what is already there.
- **A path inside a spoke root.**
  On an edge, a path inside a portal's spoke root is the hub's data, so writing its ACL replaces the hub's ACL.
  The baseline refuses it unless the entry sets `allow_data_loss: true`.
  Declare the ACL on the hub instead where you can: [Spoke roots hold the hub's data](../explanation/edges-and-portals.md#spoke-roots-hold-the-hubs-data).

### Set the filesystem permissions mode

The permissions mode is a cluster-wide setting of how the cluster reconciles a POSIX mode change from NFS with the ACL a file carries for SMB, and the reverse.
Choose it before you declare directories and ACLs: a new mode applies to later changes only, and the cluster rewrites no ACL it already holds.
The baseline applies it at step 5 and the directories at step 17.

1. Declare the mode in the group's file.

   ```yaml
   # group_vars/qumulo_clusters.yml
   qumulo_filesystem_settings:
     permissions_mode: CROSS_PROTOCOL   # NATIVE, CROSS_PROTOCOL or CROSS_PROTOCOL_POSIX_PRIORITY
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).

`qumulo_filesystem_settings` also takes `atime_enabled`, `atime_granularity` and `notify_recursive_mode`.
A setting you leave out keeps the cluster's value.

### Remove the factory share and export

Every formation creates the SMB share `Files` and the NFS export `/` (G-FACTORY).
`qumulo_factory_defaults` says what becomes of each: `absent` removes it, `present` leaves it.

1. Declare both keys.

   ```yaml
   # group_vars/qumulo_edges.yml
   qumulo_factory_defaults:
     smb_files_share: absent
     nfs_root_export: absent
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).

To confirm, read `smb.shares` with [smb_info](../../plugins/modules/smb_info.py) (`gather_subset: [shares]`) and `nfs.exports` with [nfs_info](../../plugins/modules/nfs_info.py) (`gather_subset: [exports]`).
Neither lists `Files` or `/`.

Notes:

- Declare both keys, even when one of them is `present`: an edge rebuild refuses to start without both, because a new formation creates both again.
- The cluster refuses to turn on NFSv4.1 (`v4_enabled` in `qumulo_nfs_settings`) while the export at `/` exists, because every other export's path is inside it.
  Remove the export first; the role removes it before it applies the NFS settings.

### Put quotas and snapshot policies on the right directories

A quota and a snapshot policy each belong to a directory.
Declare the directory in `qumulo_directories`, then the quota and the policy for its path.
The role creates the directories first.

1. Declare them in the host's file.

   ```yaml
   # host_vars/edge01.yml
   qumulo_directories:
     - {path: /data/projects}
   qumulo_quotas:
     - {path: /data/projects, limit: 2TiB}
   qumulo_snapshot_policies:
     - policy_name: projects-daily
       path: /data/projects
       expiration_time_to_live: 30days
       schedule:
         frequency: SCHEDULE_DAILY_OR_WEEKLY
         hour: 2
         minute: 0
         on_days: [MON, TUE, WED, THU, FRI]
         timezone: America/Los_Angeles
   ```

   To lock each snapshot a policy takes, add `lock_key`, the name of a key in the cluster's file system key store, to
   its entry, as `lock_key: legal-hold`.
   Register the key first ([Manage the keys that lock snapshots](manage-lock-keys.md)): a key the store does not hold
   makes the baseline refuse at step 2, before it writes anything
   ([A rebuilt or cloned cluster](manage-lock-keys.md#a-rebuilt-or-cloned-cluster) has the two ways on).

2. [Preview, apply and verify](#preview-apply-and-verify).

To read them back, use [filesystem_info](../../plugins/modules/filesystem_info.py) for `filesystem.quotas` and [snapshot_info](../../plugins/modules/snapshot_info.py) for `snapshots.policies`.

Notes:

- The cluster refuses a quota or a snapshot policy under a portal's spoke root (G-SPOKE-PATHS).
  The baseline checks this before it writes anything and names the path and the root.
  Declare them on a local directory outside every spoke root, or on the hub, on the hub root that the spoke root shows ([why](../explanation/edges-and-portals.md#spoke-roots-hold-the-hubs-data)).
- A policy cannot move to another directory.
  A different `path` needs `recreate: true` on the entry, which deletes and recreates the policy and keeps the snapshots it took.
  The new policy keeps the lock key of the old one, unless the entry's `lock_key` names another.
- A policy entry without `lock_key` keeps the key the policy has, and `lock_key: ""` takes it off.
- A change to `expiration_time_to_live` applies only to snapshots taken after it (G-SNAP-TTL).
- Remove a quota or a policy by declaring it with `state: absent`; a list that leaves it out does not remove it.

### Serve FTP over TLS with an anonymous user

The cluster has one FTP server, which answers on every network.
Anonymous logins act as a local user, so the user is declared with the other local users and the FTP settings name it.

1. Declare the user and the settings.

   ```yaml
   # host_vars/edge01.yml
   qumulo_local_users:
     - name: ftp-guest
       primary_group: Users
       password: "{{ vault_ftp_guest_password }}"
   qumulo_ftp_settings:
     enabled: true
     allow_unencrypted_connections: false
     chroot_users: true
     anonymous_user: local:ftp-guest
     greeting: |
       Authorized use only.
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).
   One run does it in order: the baseline creates the user at step 13 and applies the FTP settings at step 19.

To check that the server runs, read the status with [ftp_info](../../plugins/modules/ftp_info.py) (`gather_subset: [status]`).
Each node's `status` is `running` once the server is on.

Notes:

- The anonymous user must be a local user, written `local:<name>`.
  Any other form fails before any request.
  The cluster refuses a local group's name, and a user it does not hold: when neither the cluster nor `qumulo_local_users` has the user, the apply fails at step 19 naming `qumulo.cluster.local_user`.
  The preview sends no write, so it does not fail.
  In the preview of a first run, when the cluster does not hold the user yet, step 19 reports the change with a note that an earlier task must create it.
  A name in another case is the same user, and the run shows the name as the cluster holds it.
- Declare `anonymous_user: ""` to turn anonymous logins off.
  When the local user is deleted while the setting names it, the cluster keeps the setting with an empty name, which `ftp_settings` and `ftp_info` read as `local:`; declaring `""` clears it, and declaring the user again, once step 13 has made it, sets it back.
- The greeting is 1 to 1024 bytes of UTF-8 and is compared exactly, trailing newline included.
  The block scalar above ends with a newline, so declaring the same text without it is a change.
- The cluster cannot set the factory greeting back once the greeting is changed (G-FTP-GREETING).
  To keep a way back, [capture the cluster](adopt-a-cluster.md#capture-and-adopt) before you change the greeting: `qumulo_ftp_settings.greeting` in the captured file holds the greeting as the cluster has it.
  Declare that text as `greeting` to put it back.

## Cluster settings and logs

### Set a login banner and an inactivity timeout

1. Declare both in the group's file.

   ```yaml
   # group_vars/qumulo_clusters.yml
   qumulo_web_ui_settings:
     login_banner: Authorized use only. Activity is logged.
     inactivity_timeout: 30   # minutes, 2 to 600; 0 turns the timeout off
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).

Notes:

- `login_banner` is the text the Web UI shows before login, at most 32 KiB.
  An empty string removes the banner.
- A setting you leave out keeps the cluster's value.

### Send the audit log to syslog

The cluster can send its audit log to a syslog server, to a CloudWatch log group in AWS, or to its own local log.
Each destination has a name; the names `syslog`, `cloudwatch` and `local` are reserved for a destination of that type, and a `LOCAL` destination must be named `local`.

1. Declare the destination in the group's file.

   ```yaml
   # group_vars/qumulo_clusters.yml
   qumulo_audit_destinations:
     - name: siem
       type: SYSLOG
       server_address: siem.example.com
       server_port: 514
       log_format: json   # csv or json
   ```

2. [Preview, apply and verify](#preview-apply-and-verify).
   The role does not wait for the destination to connect, so also confirm on the syslog server that records arrive.

Variants:

- **Remove a destination.**
  A destination the list leaves out is left alone.
  Declare `{name: siem, state: absent}` to delete one.
- **Change a destination.**
  Change the entry.
  A partial entry does not reset the rest: the module sends the whole configuration, with the cluster's values for the options you left out (QUIRK-062 in [Quirks](../reference/quirks.md)).
- **CloudWatch.**
  Use `type: CLOUDWATCH` with `log_group_name` and `region`.
  The cluster refuses a CloudWatch destination outside AWS, and the step fails with its reason.
- **The Terraform provider sets the CloudWatch destination.**
  On a CNQ cluster in AWS the provider can set it.
  List `audit.cloudwatch` as Terraform's, and on a cluster of kind `cnq` or unset keep the default `network.floating_ips`, since a list replaces it:

  ```yaml
  # host_vars/edge01.yml
  qumulo_terraform_owned_areas: [network.floating_ips, audit.cloudwatch]
  ```

  The baseline then refuses a declared `CLOUDWATCH` entry before any step writes, even one that would change nothing, so leave the CloudWatch destination out of `qumulo_audit_destinations`.
  List `audit` instead to leave every destination to Terraform.
  `qumulo_take_over_from_terraform: true` hands either back to the role, and the floating addresses with them.
  [Terraform ownership](../explanation/terraform-ownership.md#parts-of-an-area-dnsfqdn-and-auditcloudwatch) explains why the areas are split.

## See also

- [Inventory variables](../reference/variables.md) lists every variable, its precedence and its owner.
- [Register a cluster with Nexus](nexus.md) covers monitoring and the Nexus connection, which sit beside the audit log in the baseline but are a separate task.
- [Manage certificates](manage-certificates.md) covers `qumulo_ssl` and `qumulo_ssl_ciphers`.
- [Join a cluster to Active Directory](active-directory.md) covers `qumulo_ad`, the identity mappings, home directories and SAML.
- [Rotate the admin password](rotate-admin-password.md) covers `admin`.
