<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Look up identities, DNS names and paths

This page asks a cluster questions before a change relies on the answer: whether it knows a trustee, what an Active
Directory account is to it, whether its own resolver finds a DNS name, and which file a path or a file id names.
Every lookup only reads.
When you finish, a play stops before an ACL names a trustee the cluster does not know, you can read an Active Directory
user's SID, NFS uid and groups, and the account you looked up names itself as a trustee in the next task.
The same play stops before an NFS export names a host the cluster cannot resolve, and turns a path into a file id and
an id back into a path.

## Before you start

- Have a cluster that is already formed and running, with its address, the port its REST API answers on (8000 unless you
  changed it) and the password of its `admin` user.
  A user that is not `admin` needs `PRIVILEGE_IDENTITY_READ` for the trustee lookups, and `PRIVILEGE_AD_USE` and
  `PRIVILEGE_AD_READ` for the Active Directory object lookup.
- Have the controller reach the cluster over HTTPS, and the inventory give each host its connection:
  [Set up an inventory for your clusters](set-up-inventory.md).
  The plays below use the example inventory's `hub01`.
- For the Active Directory recipes, have a cluster that has joined the domain ([Join a cluster to Active Directory](active-directory.md)).
  Outside a domain `ad:` trustees are not found, except `ad:NT AUTHORITY\Authenticated Users`, and the object lookup is
  unavailable.
- Use users, groups, domains, directories, SIDs, DNs and host names of your own for `svc-backup`, `CORP`, `corp.example.com`,
  `alice`, `Engineering`, `S-1-5-21-1-2-3-1104`, `S-1-5-21-1-2-3-1108`,
  `CN=alice,CN=Users,DC=corp,DC=example,DC=com`, `/data/archive`, `/data/builds`, `/data/projects` and
  `build01.corp.example.com`.

Every play on this page starts with the header that hands the inventory's connection variables to each module:

```yaml
# lookups.yml
- name: Look things up before a change
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
  tasks: []
```

Replace `tasks: []` with the tasks of the sections below, indented under `tasks:`.

## Check trustees before an ACL names them

[identity_info](../../plugins/modules/identity_info.py) takes `find_trustees`, a list of terms.
A term is a trustee in the form every module of the collection takes, such as `local:svc-backup`, `ad:CORP\Engineering`,
`ldap_user:alice`, `uid:1001` or `Everyone`; a bare name is refused
([why](../explanation/identity.md#trustees-a-prefix-says-where-a-name-is-defined)).
Each answer is in `identity.found_trustees`, under the term as you wrote it.

1. Add the lookup, the check and the ACL:

   ```yaml
       - name: Find the trustees the ACL will name
         qumulo.cluster.identity_info:
           gather_subset: []
           find_trustees:
             - 'ad:CORP\Engineering'
             - local:svc-backup
         register: trustees

       - name: Stop unless the cluster knows every trustee
         ansible.builtin.assert:
           that: trustees.identity.found_trustees | dict2items | rejectattr('value.found') | list | length == 0
           fail_msg: >-
             The cluster does not know
             {{ trustees.identity.found_trustees | dict2items | rejectattr('value.found') | map(attribute='key') | join(', ') }}

       - name: Make engineering and the backup account the archive's only readers
         qumulo.cluster.file_acl:
           path: /data/archive
           aces:
             - trustee: 'ad:CORP\Engineering'
               rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]
             - trustee: local:svc-backup
               rights: [READ, READ_EA, READ_ATTR, READ_ACL, EXECUTE, SYNCHRONIZE]
   ```

   `aces` is the whole ACL of the path, so list every entry it should keep, and read the check run's diff before the real
   run.
   `gather_subset: []` makes the task gather nothing but the lookup.
   Without it, `identity_info` also reads every identity subset.
   Each entry has `found`, `trustee`, `domain`, `name`, `sid`, `uid` and `gid`.
   `trustee` is the canonical form, which the next task can name; it is the same identity whichever way the term wrote
   it, such as `ad:CORP\alice` or `ad:alice@corp.example.com`.
   Authenticated Users is written `ad:NT AUTHORITY\Authenticated Users`, which resolves joined to a domain or not;
   `sid:S-1-5-11` is looked up by that name and finds the same identity, though the cluster's own find of the SID is refused by a
   node that has not found a name in `S-1-5` since it started ([QUIRK-100](../reference/quirks.md)).
   Each term is one request, sent one after another, so list the terms the task uses and no more.
   A trustee that no source holds has `found: false` and null in the other keys; it does not fail the task.
   That includes an `ad:` name other than `ad:NT AUTHORITY\Authenticated Users` on a cluster that has not joined a domain,
   and an `ldap_user:` name while LDAP is off.
2. Read `error` in an entry.
   An entry that has an `error` is `found: false` too, so the assert above stops on it, but its cause is not a missing
   trustee: the cluster refused the term.
   `error.class` and `error.description` are the cluster's own.
   The task does not fail; it gives one warning that names the terms with an error, and the terms after it are still
   found.
   List them in a task between the lookup and the assert, because the assert ends the play:

   ```yaml
       - name: Show the terms the cluster refused
         ansible.builtin.debug:
           msg: "{{ item.key }}: {{ item.value.error.description }}"
         loop: "{{ trustees.identity.found_trustees | dict2items | selectattr('value.error', 'defined') | list }}"
         loop_control:
           label: "{{ item.key }}"
   ```
3. If the error class is `api_identity_unsupported_domain_error`, the term is a SID of a domain the node has not yet
   found a name in since it started ([QUIRK-100](../reference/quirks.md)).
   That holds on a joined cluster too.
   `S-1-5-11`, the SID of Authenticated Users, is the exception, because the collection finds it by its name.
   For a domain's own SIDs, put a name of the domain before the SID in the list, and the SID is found:

   ```yaml
       - name: Find a domain group by its SID, after a name of its domain
         qumulo.cluster.identity_info:
           gather_subset: []
           find_trustees:
             - 'ad:CORP\Domain Users'
             - sid:S-1-5-21-1-2-3-1108
         register: group
   ```
4. Preview the run, then run it:

   ```sh
   ansible-playbook -i inventory lookups.yml -l hub01 --check --diff --ask-vault-pass
   ansible-playbook -i inventory lookups.yml -l hub01 --ask-vault-pass
   ```

   Look up only trustees that exist before the play, and a local name only to check one that should exist already.
   A trustee an earlier task creates is a miss in a check run, and for a local name the cluster caches that miss for about
   a minute, so a real run started within the minute cannot resolve it, here or in the task that names it
   ([QUIRK-017](../reference/quirks.md)).

A `uid:` or a `gid:` term, and a SID of a domain the cluster knows, are always `found: true`, whether or not an account
carries the number.
The cluster makes an identity of its own from the number without asking a directory, so `found` there means that the
cluster can name an identity by the term.
`name` is null when nothing names it, also for the uid of a local user made with that NFS uid.
To check that an account exists, use its name.

## Find an Active Directory user's SID, NFS uid and groups

`find_trustees` gives the SID and `expand_trustees` gives the NFS uid and the groups.
The expand shows the uid the cluster uses, which the directory's own attribute may not be.
The cluster has to have joined the domain.

1. Find and expand the user in one task:

   ```yaml
       - name: Read an Active Directory user's SID, NFS uid and groups
         vars:
           alice: 'ad:CORP\alice'
         qumulo.cluster.identity_info:
           gather_subset: []
           find_trustees: ["{{ alice }}"]
           expand_trustees: ["{{ alice }}"]
         register: alice_info

       - name: Show the SID, the NFS uid and the groups
         vars:
           alice: 'ad:CORP\alice'
           found: "{{ alice_info.identity.found_trustees[alice] }}"
           expansion: "{{ alice_info.identity.expanded_trustees[alice] }}"
         ansible.builtin.debug:
           msg: >-
             {{ found.sid }}, NFS uid {{ expansion.nfs_identity.uid | default('none') }},
             groups {{ expansion.groups | map(attribute='trustee') | list }}
         when: expansion.found
   ```

   A term with a backslash is read through a variable, since the key is the term as you wrote it.
   The SID is `found.sid`, the uid the cluster uses over NFS is `expansion.nfs_identity.uid`, and the groups are
   `expansion.groups`; `ansible-doc qumulo.cluster.identity_info` describes every key.
2. If `nfs_identity` is null, the cluster has no uid for the user.
   On the Active Directory lane, a user with no RFC 2307 attributes and no mapping expanded so, as `UNKNOWN`, with
   `Authenticated Users` its only group and none of its directory groups; read those with `find_ad_objects`, below.
   To give the user a uid, have the cluster read the directory's RFC 2307 attributes with
   `use_ad_posix_attributes: true`, or map the user
   ([Join a cluster to Active Directory](active-directory.md#map-active-directory-users-to-ldap-users)).
   Either adds a uid to the expansion, though no lane has shown it.
   To see a change just made, run `qumulo.cluster.auth_cache_clear` in a task before the lookup, because the cluster caches
   Active Directory and LDAP answers; in a check run the clear clears nothing, so check the change in a real run.
3. Run the play:

   ```sh
   ansible-playbook -i inventory lookups.yml -l hub01 --ask-vault-pass
   ```

## Look an account up by name or DN, and name it as a trustee

`find_ad_objects` reads the directory's record behind a name, a DN or a SID.
A term is `name:<sAMAccountName>`, `dn:<distinguished name>` or `sid:<SID>`.
A `name:` term is the bare sAMAccountName, with no `CORP\` and no `@domain`, because the read searches the joined domain
only; a term with either is refused before any request.
Each answer is in `identity.found_ad_objects`.

1. Look the account up and stop unless it is the one account you mean:

   ```yaml
       - name: Look up alice in the directory
         qumulo.cluster.identity_info:
           gather_subset: []
           find_ad_objects: [name:alice]
         register: directory

       - name: Stop unless the directory has one account named alice
         ansible.builtin.assert:
           that:
             - "'found_ad_objects' not in (directory.unavailable_subsets | default({}))"
             - directory.identity.found_ad_objects['name:alice'].objects | length == 1
             - directory.identity.found_ad_objects['name:alice'].objects[0].trustee is not none
           fail_msg: The directory does not have exactly one account named alice, or the cluster cannot read the directory

       - name: Give alice her home directory
         qumulo.cluster.identity_attributes:
           trustee: "{{ directory.identity.found_ad_objects['name:alice'].objects[0].trustee }}"
           home_directory: /home/alice
   ```

   Each object in `objects` has `dn`, `sid`, `uid`, `gid`, `name`, `groups`, `primary_group_sid`, `classes` and `trustee`.
   `trustee` is `ad:<NETBIOS>\<name>` for an account of the joined domain, such as `ad:CORP\alice`, and null for any other.
   `groups` are the DNs of the direct groups, `primary_group_sid` the SID of a user's primary group, and `classes` the
   object classes, which tell a user from a group.
   `uid` and `gid` are the directory's own attributes, null when it has none, and not what the cluster maps the account
   to: use `expand_trustees` for that.
   To read a change just made on the domain controller, such as a group added, run `qumulo.cluster.auth_cache_clear` in a
   task first; the lookup is answered from a cache otherwise; in a check run the clear clears nothing, so read the change
   in a real run.
2. To look up by DN or by SID, give `dn:CN=alice,CN=Users,DC=corp,DC=example,DC=com` or `sid:S-1-5-21-1-2-3-1104`.
   Each names the same object as the name does.
   A term the directory has nothing for has `found: false` and an empty `objects`; it is not an error.
3. A cluster that has not joined a domain, or has joined more than one, cannot answer.
   The task does not fail: `found_ad_objects` is then listed in `unavailable_subsets` with the cluster's description, and
   the key is absent from `identity`.
   The first condition of the assert reads it behind that check, so a cluster outside a domain stops the play there.
   In a check run a domain that an earlier task would join is not joined yet, so the lookup is unavailable then too.
4. Run the play:

   ```sh
   ansible-playbook -i inventory lookups.yml -l hub01 --ask-vault-pass
   ```

## Check an LDAP user before a mapping names her

[user_defined_mappings](../../plugins/modules/user_defined_mappings.py) maps an Active Directory user to an LDAP user by
name.
Check both names before the mapping writes, with `find_trustees` and `ldap_user:`.
The cluster needs an LDAP server configured, and has to have joined the domain
([the mapping's conditions](active-directory.md#map-active-directory-users-to-ldap-users)).

1. Add the lookup, the check and the mapping:

   ```yaml
       - name: Find the Active Directory user and her LDAP user
         qumulo.cluster.identity_info:
           gather_subset: []
           find_trustees:
             - 'ad:CORP\alice'
             - ldap_user:alice
         register: names

       - name: Stop unless both names exist
         ansible.builtin.assert:
           that: names.identity.found_trustees | dict2items | rejectattr('value.found') | list | length == 0
           fail_msg: >-
             The cluster does not know
             {{ names.identity.found_trustees | dict2items | rejectattr('value.found') | map(attribute='key') | join(', ') }}

       - name: Map alice to her LDAP user
         qumulo.cluster.user_defined_mappings:
           mappings:
             - {down_level_logon_name: 'CORP\alice', ldap_name: alice}
   ```

   While LDAP is off, an `ldap_user:` name is not found, so the assert stops the play.
   `mappings` is the whole set, so list every mapping the cluster should keep.
2. Preview and run the play as in the first recipe.
   To check that the mapping took effect, see
   [Check that a mapping took effect](active-directory.md#check-that-a-mapping-took-effect).

No lane of this collection's tests runs an LDAP server, so `ldap_user:` and `ldap_group:` trustees are tested in unit
tests only ([What the lanes cannot test](../reference/tested-versions.md#what-the-lanes-cannot-test)).

## Check a DNS name before an export's host restriction names it

An export's `host_restrictions` may name a host by its DNS name.
[network_info](../../plugins/modules/network_info.py) asks the cluster's own resolver to look the name up, so you see
what the cluster sees, which can differ from what the controller sees.
The play stops before [nfs_export](../../plugins/modules/nfs_export.py) writes if the cluster does not resolve a name.

1. Add the lookup, the check and the export:

   ```yaml
       - name: Look up the build host's name with the cluster's resolver
         qumulo.cluster.network_info:
           gather_subset: []
           resolve_names: [build01.corp.example.com]
         register: name_check

       - name: Stop unless the cluster resolved every name
         ansible.builtin.assert:
           that: name_check.network.resolved_names | rejectattr('result', 'equalto', 'OK') | list | length == 0
           fail_msg: >-
             The cluster did not resolve
             {{ name_check.network.resolved_names | rejectattr('result', 'equalto', 'OK') | map(attribute='hostname') | join(', ') }}

       - name: Export the build directory to the build host
         qumulo.cluster.nfs_export:
           export_path: /builds
           fs_path: /data/builds
           restrictions:
             - host_restrictions: [build01.corp.example.com]
   ```

   `gather_subset: []` makes the task gather nothing but the lookup.
   Without it, `network_info` also reads every network subset.
   Each name of `resolve_names` comes back once in `network.resolved_names`, even if you give it twice, with its
   `ip_addresses` and a `result` of `OK`, `NOT_FOUND`, `TIMEOUT` or `ERROR`.
   The assert passes only when every `result` is `OK`.
   Add more names to the list; one request looks them all up.
2. Preview the run:

   ```sh
   ansible-playbook -i inventory lookups.yml -l hub01 --check --diff --ask-vault-pass
   ```

   Every lookup on this page runs in a check run, since it only reads, so the assert checks the name as a real run
   would.
   `nfs_export` reports the export it would make.
   `restrictions` replaces every restriction of an export, so read the preview before you run this against an export
   that clients already mount.
3. Run it without `--check`.

To look an address up instead, give `resolve_addresses: [10.0.0.10]`.
Each address comes back in `network.resolved_addresses` with its `hostname` and `result`.

## Turn a path into a file id, and an id back into a path

[filesystem_info](../../plugins/modules/filesystem_info.py) takes `find_files`, a list of terms.
A term is an absolute path or a file id of decimal digits.
Each answer is in `filesystem.found_files`, under the term as you wrote it.

1. Turn two paths into ids:

   ```yaml
       - name: Turn the paths of two directories into file ids
         qumulo.cluster.filesystem_info:
           gather_subset: []
           find_files: [/data/projects, /data/builds]
         register: ids

       - name: Show the ids, or that a directory does not exist
         ansible.builtin.debug:
           msg: "{{ item.key }}: {{ item.value.id if item.value.found else 'not found' }}"
         loop: "{{ ids.filesystem.found_files | dict2items }}"
         loop_control:
           label: "{{ item.key }}"
   ```

   Each entry has `found`, `path`, `id` and `type`.
   `path` is the file's canonical path without a trailing slash, except the root, `/`.
   `id` is a string of decimal digits.
   `type` is the cluster's name for the kind of file, such as `FS_FILE_TYPE_DIRECTORY`.
   A term that names no file has `found: false` and null `path`, `id` and `type`; none of these fails the task: a path
   that does not exist, a path that runs through a file, a deleted or never-issued id, an id of a file system this cluster
   does not have, and an id the cluster cannot place (QUIRK-101, [Quirks](../reference/quirks.md)).
   `find_files` sends one read per term, one after another.
   Keep `gather_subset: []`, or the module also reads the capacity, the root directory and every quota.
2. Turn an id back into its path, in the same run that read the id:

   ```yaml
       - name: Turn the project directory's id back into its path
         qumulo.cluster.filesystem_info:
           gather_subset: []
           find_files: ["{{ ids.filesystem.found_files['/data/projects'].id }}"]
         register: by_id
         when: ids.filesystem.found_files['/data/projects'].found

       - name: Show the path
         ansible.builtin.debug:
           msg: "{{ by_id.filesystem.found_files[ids.filesystem.found_files['/data/projects'].id].path }}"
         when: by_id is not skipped
   ```

   The key is the id as you gave it, so a term from a variable is also the key to read the answer by.
   A file id holds only on the cluster that issued it, and only until that cluster is rebuilt.
   An id from another cluster may name nothing here, or an unrelated file.
   Use an id within the run that read it, and don't store it in an inventory, a variable file or a report
   ([why](../explanation/state-management.md#keys-not-ids)).
   Name a file by its path anywhere that outlives the run.
3. Stop a play when a path is missing:

   ```yaml
       - name: Look up the projects directory
         qumulo.cluster.filesystem_info:
           gather_subset: []
           find_files: [/data/projects]
         register: projects

       - name: Stop unless the projects directory exists
         ansible.builtin.assert:
           that: projects.filesystem.found_files['/data/projects'].found
           fail_msg: /data/projects does not exist on this cluster
   ```

   A path that does not exist is a miss, not an error, so the lookup passes and the assert stops the play.
   In a check run a directory that an earlier task of the play would create is not there yet.
   When the play makes the directory it then looks up, give the assert `when: not ansible_check_mode`.
4. Run the play:

   ```sh
   ansible-playbook -i inventory lookups.yml -l hub01 --ask-vault-pass
   ```

## When it fails

- `find_trustees: trustee 'alice' needs a prefix naming where it is defined; a bare name is ambiguous.`, and the same for
  `expand_trustees`: the term has no prefix.
  The message lists the accepted forms, and the task fails before it sends a request.
  Write `local:alice`, `ad:CORP\alice` or `ldap_user:alice`.
- `find_ad_objects term '...' needs one of the prefixes dn:, sid: or name:`: the term is outside the grammar of the
  Active Directory lookup.
  A `name:` term that holds `\` or `@` names a domain and is refused; give the bare sAMAccountName.
- `find_trustees term '<term>': ...`, `expand_trustees term '<term>': ...` or `find_ad_objects term '<term>': ...`
  naming an HTTP status: the cluster answered the term's request with an error the lookup does not take for the term's
  own.
  A 403 names the privilege the user lacks.
  A 400 to the expand fails the task.
  The task's `term` key holds the term.
  Any other failure of a term's read, such as a cluster that cannot be reached, fails the task the same way.
- `HTTP 404 ad_domain_controller_not_found_error` from `find_trustees` or `expand_trustees`: the node has no domain
  controller in reach and had not found the name ([QUIRK-102](../reference/quirks.md)).
  A domain controller out of reach is not a miss for these two lookups: the task fails.
  For a name the node had cached, the cluster answers `HTTP 500 network_timeout_error` after about a minute.
  If that comes within `request_timeout`, the task fails with it.
  With the default of 60 seconds it usually does not, so the client sends the find again until `retry_timeout` and the
  task fails with `<address> unreachable for 300s: ...`, the number being `retry_timeout` (300 by default) and the reason
  ending in `timed out`.
  Restore the route to the domain controller and run again.
  `find_ad_objects` answered an object the node had cached with the domain controller away; an object it had not cached
  was not read.
- `find_files term '...' is neither an absolute path nor a file id`: the term is a relative path, or an id with
  something other than digits, such as a hex id.
  The task fails before it sends a request.
  Use an absolute path such as `/data/projects`, or an id such as `1003`.
- `find_files term '/data/../x' has an empty, '.' or '..' component`: the path has an empty, `.` or `..` component, as in
  `/data//x`, `/data/./x` or `/data/../x`.
  Write the path out in full, such as `/data/x`.
  A trailing slash is fine.
- `find_files term '<term>': GET /v1/files/.../info/attributes failed: HTTP 403 fs_access_denied_error: ...`, naming the
  access right denied: the API user may not read the path, or a directory above it.
  Reading a file's attributes needs no role, but the file system's permissions still apply.
  The message ends with the hint that the file system's permissions deny the API user this access.
  Read the path as a user that may, or change the path's permissions with
  [directory](../../plugins/modules/directory.py) or [file_acl](../../plugins/modules/file_acl.py).
- Any other failure of a term's read, such as a cluster that cannot be reached, starts `find_files term '<term>':` and
  fails the task.

Every option and return value is in `ansible-doc qumulo.cluster.identity_info`,
`ansible-doc qumulo.cluster.filesystem_info` and `ansible-doc qumulo.cluster.network_info`.
