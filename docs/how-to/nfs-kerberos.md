<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Serve NFS with Kerberos from a standalone KDC

This page installs the keytab of a standalone Kerberos KDC on a cluster outside Active Directory, turns the Kerberos
flavours of NFS on, and requires one of them on an export.
When you finish, the cluster holds the keytab, its NFS server accepts the flavours you chose, the export refuses clients
that use anything else, and a second baseline run changes nothing.
A cluster joined to Active Directory needs none of this, because its computer account holds its Kerberos keys
([why](../explanation/identity.md#where-kerberos-for-nfs-gets-its-keys)).

## Before you start

- Have an inventory that reaches the cluster: [Set up an inventory for your clusters](set-up-inventory.md).
  The examples manage `edge01`.
- Have a cluster that is not joined to Active Directory, and declare no `qumulo_ad` for it.
  A joined cluster refuses a keytab, a cluster that holds one refuses a join, and the baseline refuses a keytab beside
  `qumulo_ad` before it writes anything ([G-KRB-AD](../reference/gaps.md)).
  To move a cluster between the two, see [If the join is refused](active-directory.md#if-the-join-is-refused).
- Have the keytab that the KDC's administrator exported for the cluster's NFS service principal, such as
  `nfs/edge01.corp.example.com@CORP.EXAMPLE.COM`.
  The cluster stores any well-formed keytab without asking the KDC, so a keytab for another host, or one whose keys the
  KDC has since changed, installs and NFS with Kerberos then fails ([G-KRB-KEYTAB](../reference/gaps.md)).
  The cluster's name and clock must also agree with the KDC's.
- Have a user that can write the keytab and the NFS settings.
  The keytab step needs `PRIVILEGE_KERBEROS_KEYTAB_READ`, `PRIVILEGE_KERBEROS_KEYTAB_WRITE` and, for a write,
  `PRIVILEGE_AD_READ`.
  Removing the keytab also needs `PRIVILEGE_KERBEROS_SETTINGS_READ` and `PRIVILEGE_KERBEROS_SETTINGS_WRITE`.
- If the inventory holds vault values, add `--ask-vault-pass` or `--vault-password-file` to every
  `ansible-playbook` command below.

## Install the keytab and turn the flavours on

1. Encrypt the keytab, and read it from the inventory.
   A keytab is binary, so [Encrypt a binary secret such as a keytab](store-secrets.md#encrypt-a-binary-secret-such-as-a-keytab)
   encodes it as base64, encrypts the file, and reads it with the file lookup.

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_kerberos:
     keytab: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01.keytab.b64') }}"
   ```

2. Turn on the flavours the clients will use, and require one of them on the export.
   `krb5` authenticates, `krb5i` also checks the integrity of each request, and `krb5p` also encrypts it.
   A flag you leave out keeps the cluster's value.
   A tenant's `nfs_settings` takes the same three flags ([Manage tenants](tenants.md)).

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_nfs_settings:
     krb5_enabled: false
     krb5i_enabled: false
     krb5p_enabled: true
   qumulo_nfs_exports:
     - export_path: /finance
       fs_path: /data/finance
       restrictions:
         - host_restrictions: []
           required_authentication_mode: AUTHENTICATION_MODE_KRB5P
   ```

   `required_authentication_mode` takes `AUTHENTICATION_MODE_NONE` (the default), `AUTHENTICATION_MODE_KRB5`,
   `AUTHENTICATION_MODE_KRB5I` or `AUTHENTICATION_MODE_KRB5P`.
   An empty `host_restrictions` matches every host.
   The list replaces every restriction the export has, so give each restriction the export keeps.

3. Preview, apply and verify, as [Preview, apply and verify](inventory-recipes.md#preview-apply-and-verify) shows.
   The baseline installs the keytab at step 14, and applies the NFS settings and the export at step 19.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ```

   The preview shows each keytab entry's principal, key version and encryption type, and never a key.
   The last run reports `changed=0`.

## Check what the cluster holds

Read the flags and the export back with [nfs_info](../../plugins/modules/nfs_info.py), using `gather_subset: [settings]`
and `gather_subset: [exports]`.
`nfs.settings` shows the three `krb5*_enabled` flags, and each restriction of each export in
`nfs.exports` shows its `required_authentication_mode`.

The preview's diff of the keytab step lists the entries before and after, as principal, key version and encryption type.
A run that reports `changed=0` shows that the cluster holds the keytab the inventory declares.

No test lane runs an NFS client with Kerberos, so nothing here shows that a client can sign in
([NFS with Kerberos](../reference/tested-versions.md#nfs-with-kerberos)).
The last check is a client's: mount the export with `sec=krb5p` from a host that holds a ticket for a principal the
cluster knows.

## Notes

- To rotate the keytab after the KDC changes the service principal's keys, replace the encrypted file and run the
  baseline again.
  The module compares each entry's principal, key version and encryption type, and the keys through a digest, so a
  change of keys alone is a change.
- `use_alt_security_identities_mapping` under `qumulo_kerberos` maps a principal to an Active Directory user through the
  user's `altSecurityIdentities` attribute; the cluster takes it joined or not.
- To remove the keytab, set `keytab_state: absent` under `qumulo_kerberos`.
  The role then ignores `keytab` and `keytab_path`.
  Deleting the keytab turns `use_alt_security_identities_mapping` off on the cluster, and `kerberos_keytab` turns it back
  on when it was on ([QUIRK-161](../reference/quirks.md)).
- A cluster is moved from a standalone KDC to Active Directory in one run: `keytab_state: absent` beside `qumulo_ad`
  deletes the keytab, then joins ([Join a cluster to Active Directory](active-directory.md#if-the-join-is-refused)).
- `keytab_path` takes a plain file on the controller instead of base64, and cannot read a vaulted file
  ([Keep secrets out of the inventory](store-secrets.md#encrypt-a-binary-secret-such-as-a-keytab)).
