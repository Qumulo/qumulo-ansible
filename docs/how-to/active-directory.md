<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Join a cluster to Active Directory

This page joins a cluster to an Active Directory domain with the `qumulo_ad` variable, changes the join afterwards, and
adds the settings that depend on it: user mappings, home directories and SAML sign-in.
The baseline applies all of them with the `cluster_baseline` role, in the order the join needs
([why](../explanation/identity.md#why-the-name-is-set-before-the-join)).

## Before you start

- Set up the inventory: [Set up an inventory for your clusters](set-up-inventory.md).
  The cluster needs a host entry with a working connection.
- Declare what the join needs, in the same inventory.
  The baseline applies `qumulo_network`, `qumulo_dns`, `qumulo_time` and `qumulo_cluster_name` before `qumulo_ad`
  ([the order of areas](../explanation/architecture.md#the-order-of-areas)).
  The cluster must resolve the domain's names through its DNS servers, and its time must be within Kerberos's tolerance
  of the domain's.
- Choose the cluster's name now.
  The name is its computer account in the domain, and a rename later needs a leave and a join
  ([Rejoin Active Directory](#rejoin-active-directory)).
  Check that no other cluster or machine uses a computer account of that name, because a join under an existing name
  takes the account over ([why](../explanation/identity.md#what-the-collection-can-and-cannot-see)).
- Have the password of a domain user that may join machines.
  Keep it in a vault, as [Store secrets](store-secrets.md) shows.

## Join the domain

1. Declare `qumulo_ad` in the cluster's group or host variables.
   Only `domain` is required; `join_user` and `join_password` are needed for the cluster to join.

   ```yaml
   # group_vars/qumulo_clusters.yml
   qumulo_ad:
     domain: corp.example.com
     join_user: svc-qumulo-join
     join_password: "{{ vault_qumulo_ad_join_password }}"
   ```

2. Preview the run.
   The check run predicts the join from the cluster's state.
   Whether Active Directory accepts it is known only by doing it.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ```

3. Apply it.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ```

4. Verify.
   Run the check run again: the `Join the domain` task reports `ok`.
   To read the membership, gather the `ad` subset of [identity_info](../../plugins/modules/identity_info.py).
   Run these tasks in a play that starts with the `module_defaults` header from
   [Set up an inventory for your clusters](set-up-inventory.md#check-the-connection):

   ```yaml
   - name: Read the Active Directory membership
     qumulo.cluster.identity_info:
       gather_subset: [ad]
     register: ad_membership

   - name: Show the domain the cluster is joined to
     ansible.builtin.debug:
       msg: "{{ ad_membership.identity.ad.domain | default('not joined', true) }}"
   ```

   `identity.ad.status` is `JOINED_TO_DOMAIN` after a join and `NOT_IN_DOMAIN` before one.

`qumulo_ad` takes these keys, each as [ad_domain](../../plugins/modules/ad_domain.py) takes it:
`domain`, `domain_netbios`, `ou`, `join_user`, `join_password`, `search_trusted_domains`, `use_ad_posix_attributes`,
`base_dn` and `domain_controllers`.
`ansible-doc qumulo.cluster.ad_domain` describes each, and
[Inventory variables](../reference/variables.md#active-directory) lists the variable among the others.

### Variants

- Name the organizational unit and the domain's NetBIOS name at the join:
  add `ou: OU=Edges,DC=corp,DC=example,DC=com` and `domain_netbios: CORP` to `qumulo_ad`.
  Only a join sets these two ([Change the join](#change-the-join-in-place);
  [why](../explanation/identity.md#settings-only-a-join-can-set)).
- Take time from the domain controller once joined: set `use_ad_for_primary: true` under `qumulo_time`.
  The baseline applies it right after the join.
- Read POSIX ids from Active Directory: set `use_ad_posix_attributes: true`.
  The cluster takes user-defined mappings only while this is off, so leave it off when you declare any.

### If the join is refused

Messages and what to do for each are in
[Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree).
A join without `join_user` and `join_password` is refused.
A join that fails because a computer account of the cluster's name already exists, and the join user may not take it
over, is [G-AD-JOIN](../reference/gaps.md): the module cannot see the account, because it lives in Active Directory.
If no running machine uses that account, have a domain administrator delete it, then run the baseline again.
Otherwise give this cluster a name of its own in `qumulo_cluster_name`.

A cluster that holds a Kerberos keytab cannot join, and a joined cluster takes no keytab
([G-KRB-AD](../reference/gaps.md); [why](../explanation/identity.md#where-kerberos-for-nfs-gets-its-keys)).
The cluster answers the join with `ad_domain_standalone_kdc_error`.
The baseline refuses a keytab declared under `qumulo_kerberos` beside `qumulo_ad` before it writes anything.

- To join a cluster that holds a keytab, delete the keytab first: set `keytab_state: absent` under `qumulo_kerberos`,
  and the step `Install or delete the Kerberos keytab`, which runs before `Join the domain`, deletes it in the same
  run.
  Outside the baseline, run [kerberos_keytab](../../plugins/modules/kerberos_keytab.py) with `state: absent`.
- To move a joined cluster to a standalone KDC, leave the domain first with `ad_domain` and `state: absent`
  ([Rejoin Active Directory](#rejoin-active-directory) shows the leave), then run the baseline with the keytab and
  without `qumulo_ad`.

## Change the join in place

Edit `qumulo_ad` and run the baseline, with `--check --diff` first.
These keys change on a joined cluster without credentials:
`search_trusted_domains`, `use_ad_posix_attributes`, `base_dn` and `domain_controllers`.

```yaml
# group_vars/qumulo_clusters.yml
qumulo_ad:
  domain: corp.example.com
  join_user: svc-qumulo-join
  join_password: "{{ vault_qumulo_ad_join_password }}"
  domain_controllers: [dc1.corp.example.com, dc2.corp.example.com]
  search_trusted_domains: false
```

`domain_netbios`, `ou` and `domain` are set only by a join.
A different value for any of them on a joined cluster fails the task.
Leave the domain and join again ([Rejoin Active Directory](#rejoin-active-directory);
[why](../explanation/identity.md#settings-only-a-join-can-set)).

If the result's notes say the domain controllers were not compared ([G-AD-DCS](../reference/gaps.md)), they were not
sent.
To set them, leave and join again ([Rejoin Active Directory](#rejoin-active-directory)): a join sends them
([why](../explanation/identity.md#what-the-collection-can-and-cannot-see)).

## Rejoin Active Directory

Do this to move the cluster to another domain, to change `domain_netbios` or `ou`, or to rename a joined cluster.
The baseline never leaves a domain, so you leave with a task of your own and then let the baseline join again.

1. Write the play that leaves, under the collection's play header.
   With `join_user` and `join_password` the leave deletes the computer account.
   Without them it leaves the account in Active Directory and warns, so a cluster joined without credentials can use the
   same play.

   ```yaml
   # leave-domain.yml
   - name: Leave the domain
     hosts: qumulo_clusters
     gather_facts: false
     module_defaults:
       group/qumulo.cluster.qumulo:
         api_host: "{{ qumulo_api_host }}"
         api_port: "{{ qumulo_api_port | default(8000) }}"
         api_username: "{{ qumulo_api_username | default('admin') }}"
         api_password: "{{ qumulo_api_password | default('') }}"
         api_token: "{{ qumulo_api_token | default('') }}"
         validate_certs: "{{ qumulo_validate_certs | default(true) }}"
         ca_path: "{{ qumulo_ca_path | default(omit) }}"
         expected_cluster_uuid: "{{ qumulo_cluster_uuid | default('') }}"
     tasks:
       - name: Leave the domain, deleting the computer account when the credentials are given
         qumulo.cluster.ad_domain:
           domain: "{{ qumulo_ad.domain }}"
           join_user: "{{ qumulo_ad.join_user | default(omit) }}"
           join_password: "{{ qumulo_ad.join_password | default(omit) }}"
           state: absent
   ```

   The header is the one [playbooks/baseline.yml](../../playbooks/baseline.yml) carries; a play of your own copies it.

2. Leave.
   Run the leave play before you edit the inventory, because it reads the domain from `qumulo_ad`.

   ```sh
   ansible-playbook -i inventory leave-domain.yml -l edge01 --check
   ansible-playbook -i inventory leave-domain.yml -l edge01
   ```

3. Change the inventory: the new `qumulo_ad` values, or the new `qumulo_cluster_name`.

4. Join again with the baseline.
   It sets the cluster's name before the join, so a rename and a join happen in one run.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ```

5. Verify as in [Join the domain](#join-the-domain): `identity.ad.domain`, `identity.ad.domain_netbios` and
   `identity.ad.ou` show the new values.

6. If the leave deleted the computer account, grant the new account the group memberships and delegations the old one
   had, in Active Directory.

To leave a domain and not join another, run steps 1 and 2, then remove `qumulo_ad` from the inventory together with what
builds on the join: `qumulo_user_defined_mappings`, the `ad:` entries of `qumulo_identity_attributes`,
`qumulo_role_members` and any ACL or share permissions, and `qumulo_saml`.
Outside a domain those `ad:` trustees, except `ad:NT AUTHORITY\Authenticated Users`, do not resolve, and the tasks that
name them fail.
Then run the baseline with `--check --diff`.

## Add mappings, home directories and SAML

These variables build on the join, so the baseline applies them after `ad_domain`.
Declare any of them, preview with `--check --diff`, and apply:

```yaml
# group_vars/qumulo_clusters.yml
qumulo_user_defined_mappings:
  - {down_level_logon_name: 'CORP\alice', ldap_name: alice}
qumulo_identity_attributes:
  - {trustee: 'ad:CORP\alice', home_directory: /home/alice}
qumulo_saml:
  enabled: true
  idp_sso_url: https://idp.example.com/saml2/sso
  idp_entity_id: https://idp.example.com/metadata
  idp_certificate: "{{ lookup('ansible.builtin.file', 'files/idp-signing.crt') }}"
  cluster_dns_name: qumulo.corp.example.com
```

`ad:` trustees, such as `ad:CORP\Engineering` in a share's permissions or in `qumulo_role_members`, resolve only after
the join, which the baseline runs first; `ad:NT AUTHORITY\Authenticated Users` resolves joined or not.

### Map Active Directory users to LDAP users

- `qumulo_user_defined_mappings` is the whole set.
  Add an entry to add a mapping, remove an entry to remove it, and declare `[]` to remove every mapping.
- The cluster takes mappings only while it is joined and `use_ad_posix_attributes` is off.
  Otherwise `user_defined_mappings` refuses before it writes, and in a check run it notes that an earlier task is
  expected to join ([QUIRK-031](../reference/quirks.md)).
- To check a user and her LDAP user before the mapping names them, see
  [Check an LDAP user before a mapping names her](look-up-identities-and-paths.md#check-an-ldap-user-before-a-mapping-names-her).
  To check that the mapping took effect, see [Check that a mapping took effect](#check-that-a-mapping-took-effect).

### Check that a mapping took effect

Clear the cache, then expand the mapped Active Directory user and read `nfs_identity`.
Run these tasks in a play with the `module_defaults` header from
[Set up an inventory for your clusters](set-up-inventory.md#check-the-connection):

```yaml
- name: Clear the authentication cache before the check
  qumulo.cluster.auth_cache_clear:

- name: Expand the mapped Active Directory user
  vars:
    alice: 'ad:CORP\alice'
  qumulo.cluster.identity_info:
    gather_subset: []
    expand_trustees: ["{{ alice }}"]
  register: mapped_alice

- name: Show the identity the cluster uses over NFS
  vars:
    alice: 'ad:CORP\alice'
  ansible.builtin.debug:
    msg: "{{ mapped_alice.identity.expanded_trustees[alice].nfs_identity }}"
```

- A mapping adds the LDAP user's uid and gid to the user's `equivalents`, and `nfs_identity` then comes from that uid.
  No lane runs an LDAP server for a mapping to name, so this is not tested end to end
  ([What the lanes cannot test](../reference/tested-versions.md#what-the-lanes-cannot-test)).
- Without a mapping, and without RFC 2307 attributes that the cluster reads, the expansion is `UNKNOWN`:
  [Find an Active Directory user's SID, NFS uid and groups](look-up-identities-and-paths.md#find-an-active-directory-users-sid-nfs-uid-and-groups)
  shows it.
- The clear matters.
  The cluster caches identities ([why](../explanation/identity.md#the-identity-cache)), so without the clear the
  expansion may show the answer from before the mapping.
  The clear empties the cache of the node it reaches; whether it reaches every node of a cluster has not been seen.
  In a check run the clear clears nothing, so check the change in a real run.
- `expand_trustees` finds the user first, so a user the cluster does not know has `found: false`.
  [Look up identities, DNS names and paths](look-up-identities-and-paths.md) has the lookups.

### Give an Active Directory identity a home directory

- Declare one entry per identity in `qumulo_identity_attributes`: the `trustee` in the collection's trustee grammar,
  such as `ad:CORP\alice`, and its `home_directory`.
  Set `state: absent` on an entry to remove an identity's attributes.
- Declare it by hand.
  The cluster cannot list which identities have attributes, so capture never writes this variable
  ([G-ID-ATTRIBUTES](../reference/gaps.md)).
- A local user's home directory is the `home_directory` key of its `qumulo_local_users` entry
  ([local_user](../../plugins/modules/local_user.py)); a local trustee here is refused.

### Clear the authentication cache

When a mapping or a home directory changes, the baseline clears the cache with `qumulo.cluster.auth_cache_clear`, so a
user the cluster has already seen gets the new value.
A run that changes neither skips the clear.
The clear runs after a mapping or a home directory changed, even when a later step of the two fails, and a failed clear
fails the run
([why](../explanation/identity.md#the-identity-cache)).

When a clear failed, the next run finds nothing to change and skips it, so clear by hand.
Run [auth_cache_clear](../../plugins/modules/auth_cache_clear.py) on that cluster as a task in a play with the
`module_defaults` header from
[Set up an inventory for your clusters](set-up-inventory.md#check-the-connection):

```yaml
- name: Clear the authentication cache
  qumulo.cluster.auth_cache_clear:
```

The task always reports a change ([G-ACTIONS](../reference/gaps.md)).

### Sign in through an identity provider

Join the domain first: only its users sign in through the identity provider, and turning sign-in on outside a domain
warns.

1. Declare `qumulo_saml` with the certificate.
   Give the identity provider's signing certificate as PEM text in `idp_certificate`.
   Text around the PEM block is ignored.
2. Run the baseline.
3. Sign in once through the identity provider, in a browser.
   The cluster stores the settings without reaching the provider, so a successful run does not show that sign-in works
   ([G-ID-SAML](../reference/gaps.md)).

Variants:

- Keep the certificate the cluster holds by leaving `idp_certificate` out.
  Turning sign-in on then warns.
  With no certificate on the cluster either, `saml_settings` refuses ([QUIRK-032](../reference/quirks.md)).
- Turn sign-in off with `enabled: false`.
  Setting `idp_certificate: ""` to remove the certificate fails when one is set
  ([G-ID-SAML-CERT](../reference/gaps.md);
  [why](../explanation/identity.md#the-certificate-cannot-be-removed)).
- Require sign-in through the provider with `require_sso: true`.
  Active Directory users can then no longer sign in with a password to the Web UI, `qq` or the REST API while sign-in is
  on, and a task that makes both true warns.
  Keep a local user that signs in with a password, such as `admin`.

## Join an edge so that it can be rebuilt

Give `join_user` and `join_password` in `qumulo_ad` on an edge.
A rebuild's predestroy phase then leaves the domain with them, which deletes the computer account, and the replay joins
again under the same name.
The leave deletes the old computer account, so grant the new account the group memberships and delegations the old one
had, in Active Directory.
Without credentials the leave keeps the computer account, and the replay refuses to join under the old name unless a
predestroy record shows the old edge left with credentials, or you set `edge_rebuild_old_edge_destroyed: true` to say
the old edge is gone ([Rebuild an unprotected edge](edge-rebuild.md#if-the-old-edge-was-lost)).

## See also

- [Identity and Active Directory](../explanation/identity.md): why the name is the computer account, what a join
  sets, what the collection cannot see, and why mappings and SAML follow the join.
- [Inventory variables](../reference/variables.md#active-directory): `qumulo_ad`;
  [mappings, home directories and SAML](../reference/variables.md#mappings-home-directories-and-saml).
- [Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree): the Active Directory
  refusals, with their exact text.
- [Rebuild an unprotected edge](edge-rebuild.md): the predestroy leave and the replay's join.
- [Inventory recipes](inventory-recipes.md): custom roles and their members, and when a local user's password is sent.
- [Respond to a refusal](respond-to-a-refusal.md): reading any refusal the collection gives.
- [Look up identities, DNS names and paths](look-up-identities-and-paths.md): check trustees, an Active Directory
  user's SID, NFS uid and groups, and an LDAP user, before a task names them.
