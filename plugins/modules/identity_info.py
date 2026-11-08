#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: identity_info
short_description: Read a Qumulo cluster's local users and groups, roles, directory services and sign-in settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the identity configuration of one Qumulo cluster and changes nothing.
  - Each subset has the shape of the options of the module that manages it. Users and groups are listed by name and
    role members as trustee strings, never by server ids.
  - No secret is returned. Local passwords cannot be read; the LDAP bind password and the Kerberos keytab are not read.
  - With O(find_trustees), O(expand_trustees) and O(find_ad_objects) it also looks up trustees and Active Directory
    objects, and returns what each term names. A lookup runs whatever O(gather_subset) lists, and in check mode too,
    since it changes nothing.
  - A subset or a lookup the cluster cannot answer is listed in RV(unavailable_subsets) with the reason, and the other
    subsets and lookups are still gathered.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V([]) gathers every subset too, unless a lookup option is given; then only the lookups run.
      - "V(local_users): the local users, as M(qumulo.cluster.local_user) takes them."
      - "V(local_groups): the local groups and their members, as M(qumulo.cluster.local_group) takes them."
      - "V(role_members): the members of every role, one entry per role, as M(qumulo.cluster.role_member) takes them."
      - "V(ad): the Active Directory membership, as M(qumulo.cluster.ad_domain) takes it, with its join status."
      - "V(ldap): the LDAP settings."
      - "V(kerberos): the Kerberos settings."
      - "V(roles): the custom roles, as M(qumulo.cluster.role) takes them."
      - "V(user_defined_mappings): the mappings, as M(qumulo.cluster.user_defined_mappings) takes them."
      - "V(saml): the SAML settings, as M(qumulo.cluster.saml_settings) takes them, the IdP certificate included, which is public."
      - "V(access_tokens): every access token, by its user, as M(qumulo.cluster.access_token) returns them, never with a bearer."
    type: list
    elements: str
    choices: [all, local_users, local_groups, role_members, ad, ldap, kerberos, roles, user_defined_mappings, saml, access_tokens]
    default: [all]
  find_trustees:
    description:
      - Trustees to find, each answered in RV(identity.found_trustees) under the term as given.
      - A term is a trustee in any form the collection's modules take, V(local:<name>), V(ad:<DOMAIN>\\<name>),
        V(ad:<name>@<domain>), V(ldap_user:<name>), V(ldap_group:<name>), V(qumulo_support:<name>), V(Everyone) or
        V(world:Everyone), V(internal:File Owner), V(internal:File Group Owner), V(uid:<n>), V(gid:<n>), and a SID with the
        prefix V(sid:) or without it. A bare name, a V(sid:) term that is not a SID, a uid or gid that is not a non-negative
        integer, and any other term outside that grammar fails the
        task before any request is sent. The message names the term and the accepted forms.
      - A term given twice is one key and is found once. Two spellings of one identity, such as V(ad:CORP\\alice) and
        V(ad:alice@corp.example.com), are two keys holding the same identity.
      - A term that no identity source holds is not an error. Its entry has V(false) in C(found) and null in every field
        after it, and the task neither fails nor warns. That includes a local, AD or LDAP name nothing holds, an
        V(ad:) name other than V(ad:NT AUTHORITY\\Authenticated Users) on a cluster that is not joined to a domain, and an
        V(ldap_user:) name while LDAP is off.
      - A V(uid:) or V(gid:) term, and a SID of a domain the cluster knows, have V(true) in C(found) whether or not an
        account carries the number or the SID. The cluster turns the number or the SID into an identity of its own
        without asking a directory, so C(found) there means that the cluster can name an identity by the term, and not
        that an account holds it. C(name) is null when nothing names the identity. The find answers the same, with a null
        C(name), for the uid of a local user made with that NFS uid; it does not name the local user.
      - A SID of a domain the node has not found a name in since it started is not found by SID, whether or not the cluster
        is joined (QUIRK-100). The term's entry has V(false) in C(found) and the server's C(class) and C(description) in
        C(error), and the other terms are still found. A find of a name in the domain, such as V(ad:CORP\\Domain Users),
        lifts it, and a later find of the SID then succeeds.
      - The SID of Authenticated Users, V(S-1-5-11), is the exception. The module looks it up by the name
        V(ad:NT AUTHORITY\\Authenticated Users), which the cluster finds joined to a domain or not, so both terms are
        found.
      - An error that belongs to a term is recorded in its entry, and the task gives one warning naming the terms with an
        entry that has an C(error). A uid of 2^32 or more, a gid past 32 bits and an oversized part of a SID earn a 400 that is
        recorded the same way, and so does, by Qumulo Core's source, an V(ldap_user:) or V(ldap_group:) name that more than
        one LDAP entry carries or that the LDAP search refuses (not observed, since no lane runs an LDAP server). Any
        other 400 fails the task.
      - With no Active Directory domain controller in reach, the find of an AD name fails the task. For a name the node
        has not found, the server answers 404 C(ad_domain_controller_not_found_error), which is not a miss (QUIRK-102).
        For a name the node has cached, the server answers 500 C(network_timeout_error) after about a minute. The client
        never sends a 500 again. If that answer comes within O(request_timeout) the task fails with it at once. With the
        default O(request_timeout) of 60 seconds it usually does not, so the client sends the find again until
        O(retry_timeout) is spent and the task fails with the message that the address is unreachable for O(retry_timeout) seconds, with a reason that ends
        in "timed out".
      - The cluster caches a miss of a local name for about a minute, and creating the name does not lift it (QUIRK-017).
        A local name looked up just before it is created stays unresolvable for about a minute, to every module that
        resolves it.
      - The answers for AD and LDAP names are cached too, and may be older than a change just made; see the notes.
      - In check mode a trustee that an earlier task of the same run would create, or that needs a join an earlier task
        would make, is not there yet, so its term is a miss.
      - A local identity's SID is the cluster's own, and changes when the cluster is rebuilt.
    type: list
    elements: str
    default: []
  expand_trustees:
    description:
      - Trustees to expand, each answered in RV(identity.expanded_trustees) under the term as given.
      - A term has the grammar of O(find_trustees), and is found first, with the same key, miss and error rules. A term
        that is not found sends no expand, and its entry has V(false) in C(found). A term whose find has an error has
        that C(error) in its entry.
      - The expansion is what the cluster itself works out for the identity. C(type) is V(USER), V(GROUP) or V(UNKNOWN),
        the last for an identity that nothing maps to a POSIX identity. C(identity) is the identity found, and
        C(nfs_identity) and C(smb_identity) are the identities the cluster uses over NFS and over SMB, each null where
        the cluster gives none. C(nfs_identity) is null for V(UNKNOWN), and set for a V(USER) or a V(GROUP) from the
        identity's POSIX mapping. C(equivalents) are the identities the cluster treats as the same principal, and
        C(groups) are the groups it counts the identity a member of, sorted by trustee and SID. The cluster gives the
        groups in no fixed order, so the sort is the collection's.
      - A local user expands as V(USER), with its NFS uid in C(nfs_identity) and among C(equivalents), and with V(Users)
        and V(Authenticated Users) among its C(groups). The cluster adds V(Authenticated Users) to the groups of every
        identity but the guest, and C(groups) holds nested groups, since the cluster follows each group and its
        equivalents (read from Qumulo Core's source; the guest's groups were observed).
      - An AD user's C(equivalents) hold the uid and gid from the directory's RFC 2307 attributes when the cluster reads
        them, and from a user-defined mapping when mappings are on (read from the source, not observed). An AD user with
        neither expanded as V(UNKNOWN) on the AD lane, with a null C(nfs_identity), no C(equivalents), and
        V(Authenticated Users) its only group. None of its directory groups were in C(groups); O(find_ad_objects) shows
        them in C(groups) and C(primary_group_sid).
      - A 400 to the expand itself fails the task, naming the term, since the collection built its body from the find.
      - A mapping, a POSIX attribute or a group the directory changed just now may not show in the expansion until the
        caches are cleared or expire; see the notes.
    type: list
    elements: str
    default: []
  find_ad_objects:
    description:
      - Active Directory objects to read, each answered in RV(identity.found_ad_objects) under the term as given.
      - A term is V(dn:<distinguished name>), V(sid:<SID>) or V(name:<sAMAccountName>). A DN has an V(=) in it, and a SID
        is a whole SID. A V(name:) term is a bare sAMAccountName, and searches the joined domain's configured base DNs
        only, not its trusted domains (read from the source), so a term that holds V(\\) or V(@) is refused. Any other term outside that grammar
        fails the task before any request is sent. The message names the term and the accepted forms.
      - A term given twice is one key and is read once.
      - A term that names no object is not an error. Its entry has V(false) in C(found) and no objects, and the task neither
        fails nor warns. That is a V(dn:) or V(sid:) read the server answers with 404 C(ad_not_found_error), and a
        V(name:) read that answers an empty list.
      - A V(name:) term can name more than one object; each object is listed once, by its SID.
      - A 400 C(distinguished_name_decode_error) or C(ms_sid_decode_error) to a term's read is that term's C(error), as
        for O(find_trustees), and the other terms are still read. Any other 400 fails the task.
      - A cluster that is not joined to a domain, or is joined to more than one, answers every AD read with a 409. The
        lookup is then unavailable. It is listed in RV(unavailable_subsets) under V(found_ad_objects) with the server's
        description, no more AD reads are sent, its key is absent from RV(identity), and the other subsets and lookups are
        still gathered. Outside a domain the server's description is "The cluster has not joined any AD domains yet.". The
        answer for a cluster joined to more than one domain was read from the source, not observed.
      - In check mode a domain that an earlier task of the same run would join is not joined yet, so the lookup is
        unavailable, and an account that task would create is a miss.
      - Each object's C(trustee) is V(ad:<NETBIOS>\\<name>) for an object of the joined domain, the form that
        M(qumulo.cluster.user_defined_mappings) takes in C(down_level_logon_name), and null for any other object. That
        is an object of a trusted domain, which a V(dn:) or V(sid:) read can answer for (read from the source, not
        observed), and any object when the cluster reports no NetBIOS name for the domain. A find of the trustee also
        makes the domain known to the cluster, as O(find_trustees) describes for a SID.
      - The object's C(uid) and C(gid) are the directory's own attributes, C(uidNumber) and C(gidNumber), null when the
        object has none. They are not what the cluster maps the object to, since the read applies no id mapping and no
        user-defined mapping, and ignores C(use_ad_posix_attributes) (read from the source, not observed). The
        C(nfs_identity) of O(expand_trustees) shows the uid the cluster uses.
      - A read of an object the cluster has read before is answered from its directory cache, so it still answers while
        the domain controller is out of reach, and may be older than a change just made on the domain controller; see the
        notes. An object the node had not cached was not read with the domain controller away.
    type: list
    elements: str
    default: []
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(local_users) GET /v1/users/ and /v1/groups/; V(local_groups) GET /v1/groups/ and /v1/groups/{id}/members/;
    V(role_members) GET /v1/auth/roles/ and /v1/auth/roles/{role}/members (paginated); V(ad) GET /v1/ad/status and
    /v1/ad/monitor; V(ldap) GET /v2/ldap/settings; V(kerberos) GET /v1/auth/kerberos-settings; V(roles) GET
    /v1/auth/roles/; V(user_defined_mappings) GET /v1/auth/user-defined-mappings/; V(saml) GET /v1/saml/settings;
    V(access_tokens) GET /v1/auth/access-tokens/ (paginated), which lists every user's tokens and needs PRIVILEGE_ACCESS_TOKENS_READ."
  - "API of the lookups: O(find_trustees) POST /v1/identity/find, which needs the RBAC privilege PRIVILEGE_IDENTITY_READ;
    O(expand_trustees) the same find for each term, then POST /v1/identity/expand for each term found, which needs
    PRIVILEGE_IDENTITY_READ too; O(find_ad_objects) GET /v1/ad/distinguished-names/{dn}/object for a V(dn:) term,
    GET /v1/ad/sids/{sid}/object for a V(sid:) term and GET /v1/ad/usernames/{username}/objects/ for a V(name:) term,
    which need PRIVILEGE_AD_USE, and GET /v1/ad/status once per task after the first object is found, unless the V(ad)
    subset of the same task read it, which needs PRIVILEGE_AD_READ. A task whose user lacks a privilege fails with the server's 403, which names
    it. The privileges of the expand and of the AD reads are read from Qumulo Core's source, not observed."
  - The lookups send one request per term, one after another through one login, and keep nothing past the task.
    O(expand_trustees) sends a find and then an expand for each term it finds. A hundred terms are a hundred requests, or
    up to two hundred for O(expand_trustees).
  - Leaving O(gather_subset) unset also gathers every subset, which lists every user, group, role member and role, and
    reads the AD, LDAP, Kerberos, mapping and SAML settings and lists every access token, each with the privileges of its
    own reads. With a lookup, give V([]) to run only the lookups.
  - An unavailable lookup's key is absent from RV(identity), and is listed in RV(unavailable_subsets).
  - The cluster caches the answers of AD and LDAP, a miss among them, per node, with a soft lifetime of 15 minutes, after
    which the cached answer is served stale and refreshed in the background, and a hard lifetime of 24 hours (read from
    Qumulo Core's source, not observed). A change made in the domain, on an LDAP server, or by
    M(qumulo.cluster.user_defined_mappings) may not show in a lookup that follows it. M(qumulo.cluster.auth_cache_clear)
    clears the identity cache of the node it reaches, and emptied the directory cache of that node on the AD lane, so
    that an object read after it showed a group just added on the domain controller. Whether it reaches every node of a
    cluster was not seen.
  - The configured domain controllers are read from the monitor of the last AD operation, which a later operation
    replaces, so V(ad.domain_controllers) is V(null) when that record is not for the joined domain (G-AD-DCS).
  - "Registers: G-AD-DCS, QUIRK-017, QUIRK-100, QUIRK-102."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the identity configuration
  qumulo.cluster.identity_info:
  register: identity

- name: Read only who is in which role
  qumulo.cluster.identity_info:
    gather_subset: [role_members]
  register: roles

- name: Read the local users and the groups they belong to
  qumulo.cluster.identity_info:
    gather_subset: [local_users, local_groups]
  register: local_identities

- name: Read the Active Directory membership
  qumulo.cluster.identity_info:
    gather_subset: [ad]
  register: ad_membership

- name: Show the domain the cluster is joined to
  ansible.builtin.debug:
    msg: "{{ ad_membership.identity.ad.domain | default('not joined', true) }}"

- name: Read the custom roles and their privileges
  qumulo.cluster.identity_info:
    gather_subset: [roles]
  register: custom_roles

- name: Read the single sign-on and mapping settings
  qumulo.cluster.identity_info:
    gather_subset: [saml, user_defined_mappings]
  register: sign_in

# gather_subset: [] makes a lookup the only thing the task reads. Without it the task also reads every subset.
# Look a local name up only to check one that should exist already, never just before creating it: the cluster caches a
# miss for about a minute (QUIRK-017).
- name: Find the trustees an ACL is about to name
  qumulo.cluster.identity_info:
    gather_subset: []
    find_trustees:
      - local:svc-backup
      - 'ad:CORP\Engineering'
      - ad:alice@corp.example.com
      - uid:1001
  register: trustees

# A trustee nothing holds is a miss, not an error, so the find passes and this task stops the play. It also stops
# on a term whose entry has an error, which has found: false.
- name: Stop unless every trustee exists
  ansible.builtin.assert:
    that: trustees.identity.found_trustees | dict2items | rejectattr('value.found') | list | length == 0
    fail_msg: >-
      The cluster does not know
      {{ trustees.identity.found_trustees | dict2items | rejectattr('value.found') | map(attribute='key') | join(', ') }}

# The entry's trustee is the canonical form, which another task can name. A term is a key as given, so a name with a
# backslash is read through a variable.
- name: Find the SID of an Active Directory user
  vars:
    alice: 'ad:CORP\alice'
  qumulo.cluster.identity_info:
    gather_subset: []
    find_trustees: ["{{ alice }}"]
  register: alice_sid

- name: Show the user's SID
  vars:
    alice: 'ad:CORP\alice'
  ansible.builtin.debug:
    msg: "{{ alice_sid.identity.found_trustees[alice].sid }}"

# The SID of a domain the node has not found a name in since it started is the term's error. Finding a name of the
# domain first, in the same list, lifts it (QUIRK-100).
- name: Find an Active Directory group by its SID, after a name of its domain
  qumulo.cluster.identity_info:
    gather_subset: []
    find_trustees:
      - 'ad:CORP\Domain Users'
      - sid:S-1-5-21-1-2-3-1108
  register: group_sid

# A uid nothing carries is still found: found means the cluster can name an identity by it, and name is null.
- name: Find the identity the cluster makes of a uid
  qumulo.cluster.identity_info:
    gather_subset: []
    find_trustees: [uid:2001]
  register: by_uid

- name: Show the uid's identity, which has no name unless something names it
  ansible.builtin.debug:
    msg: "{{ by_uid.identity.found_trustees['uid:2001'] }}"

# A mapping is read through the identity cache, so clear the cache first when the mapping was just made.
- name: Clear the authentication cache before checking a mapping
  qumulo.cluster.auth_cache_clear:

- name: Expand an Active Directory user to see its NFS identity and groups
  vars:
    alice: 'ad:CORP\alice'
  qumulo.cluster.identity_info:
    gather_subset: []
    expand_trustees: ["{{ alice }}"]
  register: expansion

- name: Show the uid the cluster uses over NFS, and the groups it counts the user in
  vars:
    alice: 'ad:CORP\alice'
  ansible.builtin.debug:
    msg: "{{ expansion.identity.expanded_trustees[alice].nfs_identity.uid | default('none') }}
      {{ expansion.identity.expanded_trustees[alice].groups | map(attribute='trustee') | list }}"
  when: expansion.identity.expanded_trustees[alice].found

# The lookup is unavailable on a cluster that is not joined to a domain, or is joined to more than one. Its key is then
# absent, so read it only when it is not listed in unavailable_subsets.
- name: Look up an Active Directory account by name and by DN
  qumulo.cluster.identity_info:
    gather_subset: []
    find_ad_objects:
      - name:alice
      - dn:CN=bob,CN=Users,DC=corp,DC=example,DC=com
  register: ad_objects

- name: Show the trustee of the account found by name
  ansible.builtin.debug:
    msg: "{{ ad_objects.identity.found_ad_objects['name:alice'].objects | map(attribute='trustee') | list }}"
  when: "'found_ad_objects' not in (ad_objects.unavailable_subsets | default({}))"

# A lookup only reads, so a check run answers as a real run does. What an earlier task of the play would create is not
# there yet, so its term is a miss.
- name: Find a user in a check run
  qumulo.cluster.identity_info:
    gather_subset: []
    find_trustees: [local:svc-backup]
  check_mode: true
  register: trustees_preview

- name: Show whether the check run found the user
  ansible.builtin.debug:
    msg: "{{ trustees_preview.identity.found_trustees['local:svc-backup'].found }}"
"""

RETURN = r"""
identity:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    local_users:
      description: Every local user, sorted by name, with C(name), C(primary_group), C(uid) and C(home_directory).
      type: list
      elements: dict
      sample: [{name: admin, primary_group: Users, uid: 0, home_directory: null}]
    local_groups:
      description: Every local group, sorted by name, with C(name), C(gid) and C(members).
      type: list
      elements: dict
      sample: [{name: Users, gid: null, members: [admin]}]
    role_members:
      description: Every role, sorted by name, with C(role) and C(members) as trustee strings.
      type: list
      elements: dict
      sample: [{role: Administrators, members: ['local:admin']}]
    ad:
      description:
        - The AD membership, with C(status), C(domain), C(domain_netbios), C(ou), C(search_trusted_domains),
          C(use_ad_posix_attributes), C(base_dn) and C(domain_controllers).
        - C(domain) is V(null) when the cluster is not joined.
      type: dict
      sample: {status: JOINED_TO_DOMAIN, domain: corp.example.com, domain_netbios: CORP, ou: null, search_trusted_domains: true,
               use_ad_posix_attributes: false, base_dn: "CN=Users,DC=corp,DC=example,DC=com", domain_controllers: null}
    ldap:
      description: The LDAP settings, without the bind password.
      type: dict
      sample: {use_ldap: true, bind_uri: "ldap://ldap.example.com", user: "cn=bind"}
    kerberos:
      description: The Kerberos settings, without the keytab.
      type: dict
      sample: {use_alt_security_identities_mapping: false}
    roles:
      description: Every custom role, sorted by name, with C(name), C(description) and C(privileges).
      type: list
      elements: dict
      sample: [{name: Backup-Operators, description: Reads snapshots, privileges: [PRIVILEGE_SNAPSHOT_READ]}]
    user_defined_mappings:
      description: The mappings from AD users to LDAP users, as C(mappings).
      type: dict
      sample: {mappings: [{down_level_logon_name: 'CORP\alice', ldap_name: alice}]}
    saml:
      description: The SAML settings, the IdP certificate as canonical PEM.
      type: dict
      sample: {enabled: false, idp_sso_url: "", idp_certificate: "", idp_entity_id: "", cluster_dns_name: "", require_sso: false}
    access_tokens:
      description:
        - Every access token, sorted by user and creation time, with C(user) and C(creator) as trustees, C(id),
          C(creation_time), C(expiration_time), null for never, and C(enabled). A token's bearer is never read.
      type: list
      elements: dict
      sample: [{user: local:svc-backup, id: "1000000000000000000001", creator: local:admin, creation_time: "2026-06-01T09:30:00.000000000Z",
                expiration_time: null, enabled: true}]
    found_trustees:
      description:
        - Each term of O(find_trustees), under the term as given, with the identity it names.
        - Each value holds the keys below, on every path. A term that is not found, or whose find has an C(error), has
          V(false) in C(found) and null in the other identity keys. No value holds the cluster's own id of an identity.
      returned: when O(find_trustees) is given
      type: dict
      contains:
        found:
          description:
            - Whether the cluster can name an identity by the term.
            - V(true) for a V(uid:), a V(gid:) or a SID of a known domain even when no account carries it; see O(find_trustees).
          returned: always
          type: bool
          sample: true
        trustee:
          description:
            - The identity as a trustee in the collection's grammar, which a later task can name. It is null when the term
              is not found, and when the identity has no form to write it in.
            - An identity with no name is written by its uid, gid or SID.
            - Authenticated Users is written V(ad:NT AUTHORITY\\Authenticated Users), a name the cluster finds joined to a
              domain or not. It is found by that name or by its SID, V(S-1-5-11).
          returned: always
          type: str
          sample: local:admin
        domain:
          description: The identity's domain as the cluster names it, such as V(LOCAL), V(ACTIVE_DIRECTORY),
            V(POSIX_USER) or V(WORLD), or null when the term is not found.
          returned: always
          type: str
          sample: LOCAL
        name:
          description: The identity's name, V(NETBIOS\\name) for an AD identity, or null when the term is not found or nothing names the identity.
          returned: always
          type: str
          sample: admin
        sid:
          description: The identity's SID, or null when the term is not found or the identity has none. A local
            identity's SID is the cluster's own, and changes when the cluster is rebuilt.
          returned: always
          type: str
          sample: S-1-5-21-1-2-3-500
        uid:
          description: The identity's NFS uid, or null when the term is not found or the identity has none.
          returned: always
          type: int
          sample: null
        gid:
          description: The identity's NFS gid, or null when the term is not found or the identity has none.
          returned: always
          type: int
          sample: null
        error:
          description:
            - The server's answer to the term, when it answered 400 with a class that is the term's own fault. Then C(found) is V(false).
            - That is a SID of a domain the node has not found a name in (QUIRK-100), a uid of 2^32 or more, a gid past 32 bits, an oversized
              SID part, and, read from the source, an ambiguous or refused LDAP name.
          returned: when the term has an error
          type: dict
          contains:
            class:
              description: The server's error class.
              type: str
              sample: api_identity_unsupported_domain_error
            description:
              description: The server's description of it.
              type: str
              sample: Unknown SID domain S-1-5-21-1-2-3
      sample:
        local:admin: {found: true, trustee: 'local:admin', domain: LOCAL, name: admin, sid: S-1-5-21-1-2-3-500, uid: null, gid: null}
        local:nobody: {found: false, trustee: null, domain: null, name: null, sid: null, uid: null, gid: null}
        uid:2001: {found: true, trustee: 'uid:2001', domain: POSIX_USER, name: null, sid: S-1-5-88-1-2001, uid: 2001, gid: null}
        sid:S-1-5-21-9-9-9-1104:
          found: false
          trustee: null
          domain: null
          name: null
          sid: null
          uid: null
          gid: null
          error: {class: api_identity_unsupported_domain_error, description: Unknown SID domain S-1-5-21-9-9-9}
    expanded_trustees:
      description:
        - Each term of O(expand_trustees), under the term as given, with its expansion.
        - Each value holds the keys below, on every path. A term that is not found, or whose find has an C(error), has
          V(false) in C(found), null in C(type) and in the three identities, and empty lists.
      returned: when O(expand_trustees) is given
      type: dict
      contains:
        found:
          description: Whether the cluster can name an identity by the term, as in RV(identity.found_trustees).
          returned: always
          type: bool
          sample: true
        type:
          description: The kind of principal the cluster makes of the identity, V(USER), V(GROUP) or V(UNKNOWN), or null when the term is not found.
          returned: always
          type: str
          sample: USER
        identity:
          description: The identity expanded, with the keys of an entry of RV(identity.found_trustees) other than
            C(found) and C(error). Null when the term is not found.
          returned: always
          type: dict
          contains:
            trustee:
              description: The identity as a trustee, or null when it has no form to write it in.
              type: str
              sample: local:admin
            domain:
              description: The identity's domain.
              type: str
              sample: LOCAL
            name:
              description: The identity's name, or null.
              type: str
              sample: admin
            sid:
              description: The identity's SID, or null.
              type: str
              sample: S-1-5-21-1-2-3-500
            uid:
              description: The identity's NFS uid, or null.
              type: int
              sample: null
            gid:
              description: The identity's NFS gid, or null.
              type: int
              sample: null
        nfs_identity:
          description: The identity the cluster uses over NFS, with the keys of C(identity). Null when the term is not found, and for a V(UNKNOWN) identity.
          returned: always
          type: dict
          sample: {trustee: 'uid:0', domain: POSIX_USER, name: null, sid: S-1-5-88-1-0, uid: 0, gid: null}
        smb_identity:
          description: The identity the cluster uses over SMB, with the keys of C(identity). Null when the term is not found, and where the cluster gives none.
          returned: always
          type: dict
          sample: {trustee: 'local:admin', domain: LOCAL, name: admin, sid: S-1-5-21-1-2-3-500, uid: null, gid: null}
        equivalents:
          description: The identities the cluster treats as the same principal, each with the keys of C(identity),
            sorted by trustee and SID. A local user's include its NFS uid.
          returned: always
          type: list
          elements: dict
          sample: [{trustee: 'uid:0', domain: POSIX_USER, name: null, sid: S-1-5-88-1-0, uid: 0, gid: null}]
        groups:
          description:
            - The groups the cluster counts the identity a member of, each with the keys of C(identity), sorted by trustee
              and SID. Empty when the term is not found.
            - Authenticated Users is written V(ad:NT AUTHORITY\\Authenticated Users), a name the cluster finds joined to a
              domain or not. It is found by that name or by its SID, V(S-1-5-11).
          returned: always
          type: list
          elements: dict
          sample: [{trustee: 'ad:NT AUTHORITY\Authenticated Users', domain: ACTIVE_DIRECTORY, name: Authenticated Users, sid: S-1-5-11, uid: null, gid: null}]
        error:
          description: The server's answer to the term's find, as in RV(identity.found_trustees). Then C(found) is V(false).
          returned: when the term's find has an error
          type: dict
          contains:
            class:
              description: The server's error class.
              type: str
              sample: api_identity_unsupported_domain_error
            description:
              description: The server's description of it.
              type: str
              sample: Unknown SID domain S-1-5-21-1-2-3
      sample:
        local:admin:
          found: true
          type: USER
          identity: {trustee: 'local:admin', domain: LOCAL, name: admin, sid: S-1-5-21-1-2-3-500, uid: null, gid: null}
          nfs_identity: {trustee: 'uid:0', domain: POSIX_USER, name: null, sid: S-1-5-88-1-0, uid: 0, gid: null}
          smb_identity: {trustee: 'local:admin', domain: LOCAL, name: admin, sid: S-1-5-21-1-2-3-500, uid: null, gid: null}
          equivalents: [{trustee: 'uid:0', domain: POSIX_USER, name: null, sid: S-1-5-88-1-0, uid: 0, gid: null}]
          groups: [{trustee: 'ad:NT AUTHORITY\Authenticated Users', domain: ACTIVE_DIRECTORY, name: Authenticated Users, sid: S-1-5-11, uid: null, gid: null}]
        local:nobody:
          found: false
          type: null
          identity: null
          nfs_identity: null
          smb_identity: null
          equivalents: []
          groups: []
    found_ad_objects:
      description:
        - Each term of O(find_ad_objects), under the term as given, with the objects it names.
        - Each value holds the keys below, on every path.
      returned: when O(find_ad_objects) is given and it is not in RV(unavailable_subsets)
      type: dict
      contains:
        found:
          description: Whether the term names at least one object.
          returned: always
          type: bool
          sample: true
        objects:
          description: The objects the term names, each once, in the order the server gave them. Empty when the term names none.
          returned: always
          type: list
          elements: dict
          contains:
            dn:
              description: The object's distinguished name.
              type: str
              sample: CN=alice,CN=Users,DC=corp,DC=example,DC=com
            sid:
              description: The object's SID.
              type: str
              sample: S-1-5-21-1-2-3-1104
            uid:
              description: The directory's C(uidNumber) attribute, or null when the object has none. It is not the uid
                the cluster maps the object to; see RV(identity.expanded_trustees).
              type: int
              sample: null
            gid:
              description: The directory's C(gidNumber) attribute, or null when the object has none. It is not the gid the cluster maps the object to.
              type: int
              sample: null
            name:
              description: The object's sAMAccountName, without the domain.
              type: str
              sample: alice
            groups:
              description: The distinguished names of the groups the object is a direct member of.
              type: list
              elements: str
              sample: ["CN=Domain Admins,CN=Users,DC=corp,DC=example,DC=com"]
            primary_group_sid:
              description: The SID of the user's primary group, or null for an object that is not a user.
              type: str
              sample: S-1-5-21-1-2-3-513
            classes:
              description: The object's LDAP object classes.
              type: list
              elements: str
              sample: [top, organizationalPerson, person, user]
            trustee:
              description:
                - V(ad:<NETBIOS>\\<name>) for an object of the joined domain, which another task can name as a trustee.
                - Null for an object of any other domain, and for any object when the cluster reports no NetBIOS name for the domain.
              type: str
              sample: 'ad:CORP\alice'
        error:
          description: The server's answer to the term's read, when it answered 400 with a class that is the term's own
            fault. Then C(found) is V(false) and C(objects) is empty.
          returned: when the term has an error
          type: dict
          contains:
            class:
              description: The server's error class, V(distinguished_name_decode_error) or V(ms_sid_decode_error).
              type: str
              sample: distinguished_name_decode_error
            description:
              description: The server's description of it.
              type: str
              sample: The distinguished name could not be decoded.
      sample:
        name:alice:
          found: true
          objects:
            - dn: CN=alice,CN=Users,DC=corp,DC=example,DC=com
              sid: S-1-5-21-1-2-3-1104
              uid: null
              gid: null
              name: alice
              groups: ["CN=Domain Admins,CN=Users,DC=corp,DC=example,DC=com"]
              primary_group_sid: S-1-5-21-1-2-3-513
              classes: [top, organizationalPerson, person, user]
              trustee: 'ad:CORP\alice'
        name:nobody: {found: false, objects: []}
  sample:
    local_users: [{name: admin, primary_group: Users, uid: 0, home_directory: null}]
    local_groups: [{name: Users, gid: null, members: [admin]}]
    role_members: [{role: Administrators, members: ['local:admin']}]
    ad: {status: NOT_IN_DOMAIN, domain: null}
unavailable_subsets:
  description:
    - Each requested subset or lookup this cluster could not answer, with the reason.
    - The key of an unavailable lookup is absent from RV(identity). V(found_ad_objects) is unavailable on a cluster that
      is not joined to a domain, or is joined to more than one, with the server's description.
  returned: when a subset or a lookup was unavailable
  type: dict
  sample: {found_ad_objects: "The cluster has not joined any AD domains yet."}
term:
  description: The term of a lookup whose read failed, as given. It is absent when the task failed before a request, on a term outside the grammar.
  returned: when the read of a term fails
  type: str
  sample: 'ad:CORP\alice'
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import IDENTITY_INFO_SUBSETS, identity_lookups
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info

ARGUMENT_SPEC = {
    "gather_subset": {
        "type": "list",
        "elements": "str",
        "choices": [
            "all",
            "local_users",
            "local_groups",
            "role_members",
            "ad",
            "ldap",
            "kerberos",
            "roles",
            "user_defined_mappings",
            "saml",
            "access_tokens",
        ],
        "default": ["all"],
    },
    "find_trustees": {"type": "list", "elements": "str", "default": []},
    "expand_trustees": {"type": "list", "elements": "str", "default": []},
    "find_ad_objects": {"type": "list", "elements": "str", "default": []},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC)
    try:
        lookups = identity_lookups(module.params)
    except QumuloError as e:  # a bad term, refused before run_info sends anything
        module.fail_json(msg=str(e), **e.details)
    # A lookup asked for runs whatever gather_subset lists, so its name joins the subsets to gather.
    module.params["gather_subset"] = [*module.params["gather_subset"], *lookups]
    run_info(module, "identity", {**IDENTITY_INFO_SUBSETS, **lookups})


if __name__ == "__main__":
    main()
