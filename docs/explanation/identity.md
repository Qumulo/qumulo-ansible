<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Identity and Active Directory

This page explains the model behind the collection's identity areas: how it names a user or a group, why a cluster's name
is also its Active Directory computer account, why mappings, home directories and SAML follow the join, and what the
collection cannot see of a domain.
The steps are in the pages under Learn more, and the options are in [Inventory variables](../reference/variables.md) and
in each module's `ansible-doc` page.

Three ideas run through it.
A name is resolved against an identity source the inventory has to name, so a name never stands alone.
Some identity state lives outside the cluster, in the domain or in the identity provider, and the collection reports
what it can verify and says where it stops.
And the areas depend on each other in a fixed order, which the sections below give one reason at a time.
[The order of areas](architecture.md#the-order-of-areas) gives the whole order.

## Where an identity comes from

A cluster looks a name up in several places: the users and groups it keeps itself (local), the Active Directory domain it
has joined, and LDAP.
A uid, gid or SID names an identity directly, whatever its source.
The collection manages the local users and groups and the domain membership through their own modules; it configures no
LDAP server. [local_user](../../plugins/modules/local_user.py) and
[local_group](../../plugins/modules/local_group.py) manage what the cluster keeps,
[ad_domain](../../plugins/modules/ad_domain.py) manages the membership of a domain, and
[user_defined_mappings](../../plugins/modules/user_defined_mappings.py) and
[identity_attributes](../../plugins/modules/identity_attributes.py) manage what links an identity from one place to
another.
[role](../../plugins/modules/role.py) and [role_member](../../plugins/modules/role_member.py) manage what an identity may
do on the cluster.

## Trustees: a prefix says where a name is defined

Wherever a module takes an identity from any source, such as the members of a role, the owner of a directory, an entry of
an ACL or of a share's permissions, or a principal of a bucket policy, it takes a trustee.
The modules that manage local users and groups name them by their plain local names.
A trustee is a string whose prefix names the place the name is defined: `local:svc-backup`, `ad:CORP\alice` or
`ad:alice@corp.example.com`, `ldap_user:alice`, `ldap_group:admins`, `qumulo_support:<name>`, `internal:File Owner`,
`internal:File Group Owner`, `uid:1000`, `gid:1000` and `sid:S-1-5-21-...`.
`Everyone` and a bare SID need no prefix, since each names exactly one identity.
`internal:File Owner` and `internal:File Group Owner`, in any letter case, are the owner and the group of whichever file
the entry is on.
They have a name and no SID, uid or gid, so only their name can write them; the cluster holds no other name in that
domain, and any other `internal:` name is refused.
They belong in ACL entries and bucket policy principals.
The grammar accepts them wherever a trustee goes, but the cluster refuses them as a directory's owner or group, and
accepts File Owner as a role member, where it means nothing.

A bare name is refused, because it is ambiguous once Active Directory or LDAP is configured.
`alice` can be a local user, a domain user and an LDAP user.
The cluster looks a bare name up in every identity source, and once Active Directory or LDAP is configured, it fails a
name found in more than one.
A bare name that resolves on a cluster with only local users would start failing the day a domain is joined, with no
change to the inventory.
This is the same choice [Refusals and guards](refusals-and-guards.md#refuse-dont-guess) describes: when the collection
cannot be sure what a line means, it fails instead of choosing.

Authenticated Users, the group of every identity that has signed in, is written `ad:NT AUTHORITY\Authenticated Users`.
An `ad:` trustee needs a domain, and the cluster names the group in the Active Directory domain with none, so the trustee
carries `NT AUTHORITY`, the domain Windows gives the group; the cluster finds the name with it in front, whether or not
the cluster has joined a domain.
A node that has not found a name in `S-1-5` since it started refuses the group's SID, `S-1-5-11`, with
`api_identity_unsupported_domain_error` ([QUIRK-100](../reference/quirks.md)), a freshly started node included, so the
collection looks `S-1-5-11` up by that name.
Every module that reads a trustee back from the cluster writes the group this way, so a captured inventory resolves again
and compares unchanged, and any letter case of the name declares the same entry.
The exception is the policy documents `s3_info` and `s3_bucket_policy` return, which are as the cluster reads them, such
as `ad:Authenticated Users`; a capture still writes the group's principal as `ad:NT AUTHORITY\Authenticated Users`.
`sid:S-1-5-11` is accepted too, but `ad:NT AUTHORITY\Authenticated Users` is the form the collection writes.

The collection resolves a trustee on every run and never stores the identity's id.
Two spellings of one domain user, `ad:CORP\alice` and `ad:alice@corp.example.com`, are one member of a role, because both
resolve to the same identity.
[State management](state-management.md#keys-not-ids) explains why a name, not an id, is the key.
`file_acl`, `smb_share` and `directory` compare what the cluster reads back by the identity the cluster gives with each
entry, so they look up only the trustees they are given; a declared SID matches such an entry in any spelling the
grammar takes.
An entry whose SID the node refuses to look up ([QUIRK-100](../reference/quirks.md)), or which no trustee form names,
does not stop them from comparing or replacing it.
An entry no form names cannot be declared, so a `file_acl` or `smb_share` task that declares that ACL or those permissions
always replaces it.
`s3_bucket_policy` still looks up each principal it reads back, since a policy carries no such identity, so a principal
whose SID the node refuses still fails it.
`capture_info`, `filesystem_info` and `s3_info` still fail on an entry no trustee form names, and `identity_info` returns
its trustee as null.
Resolution has three consequences for the order of a run.

- An `ad:` trustee resolves only once the cluster has joined the domain, so every area that takes one comes after the join.
  An exception is `ad:NT AUTHORITY\Authenticated Users`, which resolves joined or not.
- In check mode an earlier task may create the user or join the domain that a later task names.
  A trustee that does not exist yet is reported as a change with a note, not as a failure.
- The cluster caches a failed lookup for about a minute ([QUIRK-017](../reference/quirks.md)), so a run that creates a
  local user and assigns it a role within about a minute of a check run that looked the user up can fail to resolve it.

To check that the cluster resolves a trustee, and what it is to the cluster, before a task names it, see
[Look up identities, DNS names and paths](../how-to/look-up-identities-and-paths.md).

## Groups, then users, then members

The baseline creates the local groups without members, then the users, then sets the groups' members.
A user's primary group must exist before the user, which puts groups first.
A member must exist before it can be added to a group, which puts the membership last, once the users are in place.
Creating a group with its members in one step would fail for any member that is itself new.

Every user whose primary group is a group counts as a member of it, and the server keeps that membership itself.
`local_group` never adds or removes it, and it never counts as an extra member when a task lists the membership with
`purge`.
Users and groups share one set of names on a cluster, so a user and a group cannot have the same name.

## Roles, and why members go before a delete

A role says what its holders may do. `role` owns the role and its privileges, and `role_member` owns who holds it.
The four built-in roles, `Administrators`, `Observers`, `Data-Administrators` and `Qumulo-Support`, cannot be changed or
deleted, so they appear in `qumulo_role_members` only.
A custom role's `privileges` list is its whole set. The privileges differ between Qumulo Core versions, so a name the
cluster does not know fails before anything is written, instead of being dropped.

In the role area the baseline creates and changes the custom roles first, since a role must exist before its members, then
assigns the members, and deletes the roles declared `state: absent` last.

The cluster deletes a role only when the role has no members.
The delete comes last for that reason, and the inventory, not the collection, removes the members first: an entry of
`qumulo_role_members` with `purge: true` and no members does it.
`role` refuses a delete while the role still has members, names `role_member` in the message, and does so before any write.
In check mode it reports the delete with a note instead, since an earlier task in the same play may remove the members and
a check run does not run that task's write.
Neither `role` nor the baseline removes a member as a side effect of a delete.

The join comes first because an `ad:` trustee resolves only once the cluster has joined the domain, bar
`ad:NT AUTHORITY\Authenticated Users`.
The users come first because a `local:` member resolves only once the user exists, and because deleting a user drops its
role assignments, so the users have to be settled before membership is applied to them.

Two members stay whatever the inventory says. The local `admin` and `system` users always remain in `Administrators`, as
the server requires, and `purge` is refused for `Qumulo-Support`, which holds the assignment that Qumulo's support access
relies on.

## The name is the computer account

A cluster joined to a domain has a computer account in it, and the account's name is the cluster's name, written in upper
case with a `$` after it: `qumulo_cluster_name: edge01` is the account `EDGE01$`.
The server refuses a rename while the cluster is joined, so the name and the account cannot drift apart.

### Why the name is set before the join

The account is created under the name the cluster has at the moment it joins.
The baseline therefore sets the name with [cluster_settings](../../plugins/modules/cluster_settings.py) before
[ad_domain](../../plugins/modules/ad_domain.py), because a name set after the join would be refused.
`cluster_settings` reads the domain state before a rename and fails while the cluster is joined, in check mode too, with a
message that says to leave the domain, rename, and join again.
It fails there, before any write, so that the message can name the way out: leave, rename, join again.

The join needs two more facts about the cluster, and the baseline sets both earlier in the same run.
The cluster has to resolve the domain's names, which is [dns_settings](../../plugins/modules/dns_settings.py), and its
clock has to be within Kerberos's tolerance of the domain's, which is
[time_settings](../../plugins/modules/time_settings.py).
The baseline applies both before the join, so the join finds them already in place.

### Settings only a join can set

`domain_netbios` and `ou` are set when the account is created and cannot be changed in place.
A different value for either on a joined cluster fails the task, and so does a different domain.
`search_trusted_domains`, `use_ad_posix_attributes`, `base_dn` and `domain_controllers` change in place, and `ad_domain`
changes them.

`ad_domain` never leaves a domain to join another, or to change what only a join sets.
It fails and names the step to run.
The reason is what a leave does. A leave with `join_user` and `join_password` deletes the computer account together with
its group memberships and delegations in the domain, and a leave without them leaves the account in Active Directory and
warns.
A baseline run that did either as a side effect of a changed `ou` would remove access in the domain that nothing in the
inventory describes.

### What the collection can and cannot see

The module can verify that the cluster is joined to the domain under its own name.
It cannot verify the rest, and each limit is a registered gap, so a clean run says less about the domain than about the
cluster.

- **The credentials.** The cluster keeps neither the join user nor its password and returns neither, so no run can
  compare them. The module sends them only to join or leave, and capture marks both as values you supply
  ([G-AD-JOIN](../reference/gaps.md)).
- **The account.** The computer account lives in Active Directory, where the module cannot see it. Whether an existing
  account of the same name can be reused depends on the domain's policy ([G-AD-JOIN](../reference/gaps.md)).
  A join under a name whose computer account already exists, by a user who is allowed to reuse it, takes the account over
  and resets its password, which breaks any other cluster still using it. When the user is not allowed to, the join fails
  and the message says an account of that name exists.
- **The domain controllers.** The cluster reports the configured domain controllers only in the record of its last
  Active Directory operation, which a later operation replaces. When that record is for another domain,
  `domain_controllers` cannot be compared, so it is sent only with a join, and on a joined cluster the result carries a
  note ([G-AD-DCS](../reference/gaps.md)).

### A clone and a rebuilt edge

The account is why a clone is refused the source's name.
A clone joins the domain under its own name as a new account, and a name that matches the source's, or another inventory
cluster's, would take that cluster's account over and reset its password.
[Clone an edge's configuration onto a new edge](../how-to/clone-edge.md) gives the clone its own name, and
[clone_host_vars](../../plugins/modules/clone_host_vars.py) asks for `join_user` and `join_password`, since the clone
joins as a new account and the cluster keeps neither.

A rebuilt edge has the same problem from the other side.
Formation gives the edge a new UUID, so the new edge is a different cluster that carries the old edge's name.
It joins under that name, and a join under the old name resets the password of an account that a live old edge may still
use.
The [edge_rebuild](../../roles/edge_rebuild/) role therefore leaves the domain in its predestroy phase, which deletes the
account only when `join_user` and `join_password` are in the inventory, and records that it did.
The replay refuses to join under the old name unless that record shows the leave, or `edge_rebuild_old_edge_destroyed`
says the old edge is gone.
[Rebuilds](rebuilds.md#a-computer-account-that-outlives-the-edge) explains what a leave with and without credentials
leaves behind, and [Rebuild an unprotected edge](../how-to/edge-rebuild.md) has the steps.

## Mappings, home directories and the identity cache

Two areas describe how an identity from one source relates to another.
`qumulo_user_defined_mappings` says which Active Directory user is the same person as which LDAP user, and the cluster
then treats the two as one identity.
`qumulo_identity_attributes` gives an Active Directory, LDAP or POSIX identity a home directory.
Both follow the join in the baseline: the cluster takes mappings only while it is joined, and an `ad:` identity resolves
only then.

### Mappings

The cluster accepts a mapping only while Active Directory is joined and POSIX attributes from AD are off.
Outside a domain it answers the write with a server error, not a client error
([QUIRK-031](../reference/quirks.md)), which gives no hint that the join is missing.
`user_defined_mappings` therefore reads the domain state before it writes a list that is not empty and refuses, naming
`ad_domain`.
In check mode it notes that an earlier task is expected to join the domain instead of refusing.
While the cluster reads POSIX ids from Active Directory, it refuses the list in check mode too, since no earlier task in
the baseline turns that off.
The list is the whole set, so a mapping the cluster has and the list leaves out is removed, and an empty list removes them
all.
A mapping names the AD user with the domain's NetBIOS name, as `CORP\alice`, and the cluster checks that each such NetBIOS
name resolves through the joined domain.

### Home directories

A local user's home directory is an attribute of the user, set with `local_user`.
The cluster keeps a local identity's home directory and the attributes of other identities in one place, so
`identity_attributes` refuses a trustee that resolves to a local identity and points to `local_user`.

The cluster reads and writes the attributes of one identity at a time, by id, and lists no identity that has any
([G-ID-ATTRIBUTES](../reference/gaps.md)).
Capture therefore cannot read `qumulo_identity_attributes` back, and no adoption writes it.
The inventory names each identity, and the module manages those and no others.
An identity absent from the inventory keeps whatever home directory the cluster holds for it.

### The identity cache

The cluster caches the identities it has looked up and their group memberships, from Active Directory, LDAP and its own
configuration.
A changed mapping or home directory reaches a user the cluster has already cached only once the cache is cleared or
expires.
[auth_cache_clear](../../plugins/modules/auth_cache_clear.py) clears it.

The baseline clears the cache only when a mapping or a home directory changed.
Clearing is an action, not a setting, so the module reports a change on every run
([G-ACTIONS](../reference/gaps.md); [State management](state-management.md#actions-are-not-settings) explains the
difference), and lookups are slower until the cache fills again.
The clear sits in an `always` section of the role, so the cache is still cleared after a mapping change when the
identity-attributes step that follows it fails.
A run whose clear itself failed leaves the cache as it was, and the next run, finding nothing to change, skips it, so
that case is the one that needs the module run by hand.

## Where Kerberos for NFS gets its keys

NFS with `krb5`, `krb5i` or `krb5p` needs the cluster to hold keys for its NFS service principal, and a cluster has them
from one of two places.
A cluster joined to Active Directory has them through its computer account, so the join is all it needs.
A cluster outside Active Directory, whose clients get tickets from a standalone KDC (MIT Kerberos, FreeIPA, or a domain
controller used only as a KDC), holds the keytab that KDC exported, which
[kerberos_keytab](../../plugins/modules/kerberos_keytab.py) installs.

The two exclude each other.
A joined cluster refuses a keytab, and a cluster that holds one refuses a join ([G-KRB-AD](../reference/gaps.md)).
The module therefore fails before any keytab write on a joined cluster, in check mode too, and the baseline installs the
keytab before the join, so that a keytab declared absent is deleted first.
The cluster stores a well-formed keytab without asking the KDC whether its principals or keys are right
([G-KRB-KEYTAB](../reference/gaps.md)), so the module reports what it holds, as principal, key version and encryption
type, and cannot report whether a client can sign in with it.
The keys are a secret that the cluster returns, so the module compares them through a digest and shows none
([why](authentication.md#where-secrets-live)).
[kerberos_settings](../../plugins/modules/kerberos_settings.py) sets the one Kerberos setting, which maps a principal to
an Active Directory user through the user's `altSecurityIdentities` attribute.
The keytab route needs four work-arounds. Its ETag is the same for any keytab, so the module sends no `If-Match`
(QUIRK-160). A PUT keeps only the low 8 bits of a key version, so the module compares versions by them and warns about
one above 255 (QUIRK-162). A PUT in the JSON form that GET returns, when the server cannot decode an entry, is refused
with a message that quotes the entries, keys included, so the module sends the keytab file instead and reports a
failure by its status and error class only (QUIRK-163). A DELETE also turns the setting above off, so the module turns
it back on afterwards when it was on (QUIRK-161).

## SAML sign-in

`qumulo_saml` lets the users of the joined domain sign in to the Web UI through an identity provider.
It follows the join because sign-in works only for those users: before the join, the settings would describe a sign-in
that no one can use.
Turning sign-in on warns when the cluster is not joined, and in check mode notes it instead, since an earlier task may
join the domain.

### What the cluster stores and what it checks

The cluster stores the settings without reaching the identity provider, and signing in is an interactive browser flow, so
no call shows that the settings work ([G-ID-SAML](../reference/gaps.md)).
A successful run shows that the cluster holds the values the inventory gave, and not that a user can sign in.
That is why the first run is followed by one sign-in by hand
([Sign in through an identity provider](../how-to/active-directory.md#sign-in-through-an-identity-provider)).

The identity provider's certificate is public, so unlike a password the module reads it back and compares it.
The cluster stores the certificate re-encoded and without any text around it, so `saml_settings` compares the
certificates themselves and not the text.
It uses PATCH only and never PUT, since PUT validates the certificate even when none is given.

### The certificate cannot be removed

Once a certificate is set, the cluster keeps it and refuses an empty one as an invalid certificate ([G-ID-SAML-CERT](../reference/gaps.md)).
`saml_settings` therefore refuses `idp_certificate: ""` before any write when one is set, and sign-in is turned off with
`enabled: false`, which leaves the certificate in place and unused.

This is why the module wants the certificate declared.
Left out, the certificate the cluster holds is kept, and that one may have been set by anyone.
Turning sign-in on without declaring one warns, and giving the certificate lets the module check it.
Turning sign-in on with no certificate anywhere, neither in the task nor on the cluster, is refused, although the cluster
itself would accept it and then have nothing to check an identity provider's response against ([QUIRK-032](../reference/quirks.md)).

### `require_sso`

While `require_sso` and `enabled` are both true, Active Directory users can no longer sign in to the Web UI, `qq` or the
REST API with a password; only the identity provider admits them.
Local users, such as `admin`, still can.
A task that makes both true warns.
The warning names a local user that signs in with a password because, with `require_sso` on, that user is the way in that
does not depend on the identity provider.

## Learn more

- [Join a cluster to Active Directory](../how-to/active-directory.md) joins, changes the join in place, leaves and joins
  again, adds mappings, home directories and SAML, and prepares an edge for a rebuild.
- [Declare common settings in the inventory](../how-to/inventory-recipes.md#declare-custom-roles-and-give-them-to-trustees)
  declares custom roles and their members, and
  [the same page](../how-to/inventory-recipes.md#choose-when-a-local-users-password-is-sent) says when a local user's
  password is sent.
- [Inventory variables](../reference/variables.md#active-directory) lists `qumulo_ad`, `qumulo_local_users`,
  `qumulo_roles` and the other variables of these areas.
- [Encrypt a binary secret such as a keytab](../how-to/store-secrets.md#encrypt-a-binary-secret-such-as-a-keytab)
  keeps the keytab of a standalone KDC encrypted.
- [The gap register](../reference/gaps.md) holds G-AD-JOIN, G-AD-DCS, G-ACTIONS, G-ID-ATTRIBUTES, G-ID-SAML,
  G-ID-SAML-CERT, G-KRB-AD and G-KRB-KEYTAB, and [the quirk register](../reference/quirks.md) holds QUIRK-017,
  QUIRK-031, QUIRK-032, QUIRK-100 and QUIRK-160 to QUIRK-163.
- [Architecture](architecture.md#the-order-of-areas) gives the order of all the areas, [Rebuilds](rebuilds.md) covers the
  computer account across a rebuild, and [Refusals and guards](refusals-and-guards.md) covers why a module refuses.
- [cluster_baseline](../../roles/cluster_baseline/) is the role that applies these areas.
