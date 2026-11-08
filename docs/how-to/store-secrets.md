<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Keep secrets out of the inventory

This page encrypts the secrets an inventory holds with Ansible Vault, or looks them up when a run starts, so that you
can commit the inventory without committing a password.
When you finish, no plain-text password, license document, private key or join credential is in the project, and the
playbooks read each one at run time.

The secrets an inventory holds are:

| Secret | Variable | Where the example inventory keeps it |
|---|---|---|
| The `admin` password of each cluster | `qumulo_api_password` | `vault_qumulo_api_password` in `host_vars/<host>.yml` |
| An access token | `qumulo_api_token` | not in the example; [rotate it](rotate-access-token.md) |
| The license document for an edge | `qumulo_capacity.license_document` | `vault_qumulo_license_document` in `host_vars/edge01.yml` |
| The private key of the TLS certificate | `qumulo_ssl.private_key` | encrypted in place in `host_vars/edge01.yml` |
| The password of a local user | `qumulo_local_users[].password` | encrypted in place in `host_vars/edge01.yml` |
| The keytab of a standalone Kerberos KDC | `qumulo_kerberos.keytab` | not in the example; [Encrypt a binary secret such as a keytab](#encrypt-a-binary-secret-such-as-a-keytab) |
| The Active Directory join password | `qumulo_ad.join_password` | `vault_qumulo_ad_join_password` in `group_vars/qumulo_clusters.yml` |
| A Nexus API token | `qumulo_nexus_api_token` | commented out in `group_vars/qumulo_clusters.yml`, encrypted in place |
| A Nexus join key | `qumulo_monitoring.nexus_join_key` | not in the example |
| The `admin` password of the new edge in an `edge_rebuild` replay | `edge_rebuild_admin_password` | not in the example; defaults to `qumulo_api_password` |
| The new admin password during a rotation | `qumulo_api_password_after` | in `host_vars/<host>.yml` during a rotation only ([Rotate the admin password](rotate-admin-password.md)) |

Every module or role option that takes one of these is marked `no_log`, so Ansible does not print it in task output.

## Before you start

- Have an inventory with a working connection: [Set up an inventory for your clusters](set-up-inventory.md).
- Choose a vault password, and decide where it lives.
  Keep it outside the repository, in a password manager or a file with the permissions of a private key.
  The example inventory uses the vault password `example`; replace each of its encrypted values with your own.
  `ansible-vault` comes with ansible-core.

## Encrypt a password and refer to it

The example inventory holds each cluster's password under a `vault_` name in the host's `host_vars` file.
The group file maps it to the variable the modules read, so that every host uses the same line.

1. Encrypt the password.

   ```sh
   ansible-vault encrypt_string --prompt
   ```

   It asks for a vault password twice (give the one the rest of the inventory uses; it does not check), then for the
   variable name, then for the secret, which it hides.
   With `--vault-password-file ~/.vault-pass` it asks for no vault password.
   Give `vault_qumulo_api_password` as the name.

2. Paste the output into the host's file.

   ```yaml
   # inventory/host_vars/edge01.yml
   vault_qumulo_api_password: !vault |
     $ANSIBLE_VAULT;1.1;AES256
     ...
   ```

3. Refer to it from the group, once.

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_api_password: "{{ vault_qumulo_api_password }}"
   ```

4. Pass the vault password to every run, from a prompt or from a file outside the repository.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory --ask-vault-pass --check --diff
   ansible-playbook qumulo.cluster.baseline -i inventory --vault-password-file ~/.vault-pass --check --diff
   ```

   Encrypt every value with the same vault password; a run with one `--ask-vault-pass` cannot decrypt a value made with
   another.

To verify, run the read-only `first-run.yml` from [Check the connection](set-up-inventory.md#check-the-connection)
with the same option.
Each host reports `ok`, and the debug task prints the cluster's name, UUID and version.
A run without the right vault password fails with a decryption error.

Set exactly one of `qumulo_api_password` and `qumulo_api_token` on a host.
An empty string counts as unset.

If you adopt a cluster, keep its password in the environment until the adoption has run, and move it into the vault
afterwards: adoption writes `host_vars/<host>.yml` and refuses to replace one that exists
([Adopt a cluster you already run](adopt-a-cluster.md)).

## Encrypt a value inside a dict or a list item

A value inside a dict or a list item, such as `qumulo_ssl.private_key` or the `password` of an item in
`qumulo_local_users`, can be encrypted in place, with no `vault_` variable:

1. Run `ansible-vault encrypt_string --prompt`, press Enter at the variable name prompt to give none, and enter the
   secret.
2. Paste the `!vault |` block under the key, indented deeper than the key.

```yaml
# inventory/host_vars/edge01.yml
qumulo_ssl:
  certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-chain.pem') }}"
  private_key: !vault |
    $ANSIBLE_VAULT;1.1;AES256
    ...
qumulo_local_users:
  - name: svc-backup
    primary_group: Users
    password: !vault |
      $ANSIBLE_VAULT;1.1;AES256
      ...
```

Verify with `baseline --check --diff` and the vault option from the steps above.
A local password cannot be read back from the cluster ([G-PASSWORD](../reference/gaps.md);
[why](../explanation/authentication.md#passwords-the-cluster-does-not-show-back)), so with the default
`update_password: on_create` it is sent only when the user is created.

## Encrypt a license document

A license document is a JSON file of several lines.
Encrypt the whole file as one value with `--stdin-name`, because `--prompt` reads only the first line:

```sh
ansible-vault encrypt_string --stdin-name vault_qumulo_license_document < edge01-license.json
```

Paste the output into `host_vars/edge01.yml`.
The group file hands it to `qumulo_capacity`:

```yaml
# inventory/group_vars/qumulo_edges.yml, in part
qumulo_capacity:
  license_document: "{{ vault_qumulo_license_document | default(none) }}"
  license: pending
  clamp: 4TB
  # free_threshold: 0.15, as in the example file
```

A host's `qumulo_capacity` replaces the group's whole, so the document keeps a variable of its own.
Pass the vault option to the second pass as well ([Run the second pass](licensing.md#run-the-second-pass)).

## Encrypt a binary secret such as a keytab

A keytab is a binary file, and a module option takes text, so `keytab` takes the file's base64.
Encrypt the base64 as a file, and read it with the file lookup, which decrypts a file encrypted with Ansible Vault.
This is for the keytab of a standalone KDC, on a cluster outside Active Directory
([why](../explanation/identity.md#where-kerberos-for-nfs-gets-its-keys)).

1. Encode the keytab and encrypt it in one step, into a `files/` directory beside the inventory.
   `base64 -w0` writes the file on one line, and `ansible-vault encrypt` reads it from the pipe.
   The directory must exist first.

   ```sh
   mkdir -p files
   base64 -w0 edge01.keytab | ansible-vault encrypt --output files/edge01.keytab.b64
   ```

   It asks for the vault password, or reads it from `--vault-password-file ~/.vault-pass`; give the one the rest of the
   inventory uses.
   Delete `edge01.keytab`, which is not encrypted, once `files/edge01.keytab.b64` exists.

2. Read the file where the keytab is declared.
   `inventory_dir` is the directory of the inventory file that defines the host.

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_kerberos:
     keytab: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01.keytab.b64') }}"
   ```

   In a playbook, a relative name resolves under the playbook's or the role's `files/` directory:

   ```yaml
   - name: Install the edge's keytab for NFS with Kerberos
     qumulo.cluster.kerberos_keytab:
       keytab: "{{ lookup('ansible.builtin.file', 'edge01.keytab.b64') }}"
   ```

3. Pass the vault option to every run, as in the steps above, and preview.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --vault-password-file ~/.vault-pass --check --diff
   ```

   The diff shows each entry's principal, key version and encryption type, and never a key.
   After a run without `--check`, the same command reports no change.

`keytab_path` takes a plain file on the controller, such as one a pipeline step wrote from its secret store, as an
absolute path.
The module reads the file as it is and decrypts nothing, so a file encrypted with Ansible Vault fails there and the
message names this recipe.
A capture names the keytab a cluster holds under its secrets to supply, and writes no key
([Secrets to supply](../reference/capture-and-clone.md#secrets-to-supply)).

## Share one secret across clusters

A secret that every cluster in a group uses, such as the Active Directory join password, is encrypted once in a group
file and referred to by the group variables that need it:

```yaml
# inventory/group_vars/qumulo_clusters.yml
vault_qumulo_ad_join_password: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

```yaml
# inventory/group_vars/qumulo_edges.yml, in part (qumulo_hubs.yml is the same with its own ou)
qumulo_ad:
  domain: corp.example.com
  join_user: svc-qjoin
  join_password: "{{ vault_qumulo_ad_join_password }}"
  # domain_netbios and ou: as in the example file
```

## Encrypt a whole vars file

A cloned edge needs secrets that its captured file does not hold, and the clone playbook takes them for one run.
Put them in a vars file under `clone_edge_supply`, encrypt the file as a whole, and pass it with `-e @` and the vault
option.
Any playbook run that takes secrets from a vars file, or a vault-protected file next to the inventory, works the same
way.

```sh
ansible-vault encrypt clone-secrets.yml
ansible-vault edit clone-secrets.yml
ansible-playbook qumulo.cluster.clone_edge -i inventory ... -e @clone-secrets.yml --ask-vault-pass
```

The secrets are never written to the cloned file.
[Clone an edge's configuration onto a new edge](clone-edge.md) has the keys and the full command.

## Look a password up instead

A lookup keeps the password out of the project altogether.

### From the environment

In place of the `vault_` line of step 3 above, read the password from a variable named for the host:

```yaml
# inventory/group_vars/qumulo_clusters.yml
qumulo_api_password: "{{ lookup('ansible.builtin.env', 'QUMULO_PASSWORD_' ~ inventory_hostname | upper) }}"
```

Set it in the shell that runs Ansible, without echoing it or writing it to the shell history:

```sh
read -rs QUMULO_PASSWORD_EDGE01 && export QUMULO_PASSWORD_EDGE01
read -rs QUMULO_PASSWORD_HUB01 && export QUMULO_PASSWORD_HUB01
```

Host names must be valid in an environment variable name: letters, digits and `_`.

A variable that is unset reads as an empty string, and the first task that connects fails with
`set exactly one of api_password or api_token for <api_host>`
([Troubleshooting](../reference/troubleshooting.md#connecting)).
A pipeline sets these variables from its secret store, as [Run the playbooks from a pipeline](pipeline.md) shows.

### From a secret store a Terraform root names

A host from the `qumulo.cluster.terraform` inventory plugin carries `qumulo_password_ref` when the root's output names
one; a host with a null `password_ref` takes its password from the vault or the environment as above.
`group_vars` resolves the reference with a lookup for the type of store it names.
[Use a Terraform root's output as the inventory](terraform.md#point-the-plugin-at-the-output) has the lookup for each
type.

## See also

- [Set up an inventory for your clusters](set-up-inventory.md) places the connection variables.
- [Rotate the admin password](rotate-admin-password.md) changes a password that is already vaulted.
- [Inventory variables](../reference/variables.md#connection) lists the connection variables and their defaults.
- [Authentication, trust and secrets](../explanation/authentication.md#where-secrets-live) explains where each secret
  lives and why the modules check the UUID before they send a password.
