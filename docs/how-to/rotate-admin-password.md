<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Rotate the admin password

Change `admin`'s password on each cluster and keep the play connected, so that the next run is not locked out.
When you finish, the cluster accepts the new password, the inventory holds it in place of the old one, and
`qumulo_api_password_after` is gone.

The cluster applies `admin`'s password to every node's console and SSH password too ([G-ADMIN-OS](../reference/gaps.md)), so the rotation
changes those as well.
For why the change needs these flags, see
[Refusals and guards](../explanation/refusals-and-guards.md#a-consent).

## Before you start

- Have an inventory in which the host connects as `admin` with `qumulo_api_password`:
  [Set up an inventory for your clusters](set-up-inventory.md).
  The playbook runs on every host of `qumulo_clusters`.
- Keep the password in the vault, as `vault_qumulo_api_password` in the host's `host_vars` file, the way
  [Keep secrets out of the inventory](store-secrets.md#encrypt-a-password-and-refer-to-it) sets it up.
- Know who else signs in to the nodes' console or over SSH as `admin`: they need the new password too.
- Choose one host to start with. The commands below use `edge01`.
  Run the playbook with `--check` before the real run, and on one host with `-l` before you widen it.

## Rotate the password

1. Encrypt the new password as `qumulo_api_password_after`.

   ```sh
   ansible-vault encrypt_string --prompt
   ```

   It asks for the vault password, then for the variable name, then for the secret.
   Give the vault password the inventory's other values use, since the run passes only one.
   Give `qumulo_api_password_after` as the name, and paste the output into the host's `host_vars` file.

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_api_password_after: !vault |
     $ANSIBLE_VAULT;1.1;AES256
     ...
   ```

   Keep it in the host's `host_vars` rather than passing it with `-e`, so that the value stays encrypted.
   No module reads it; only this playbook does.

2. Check what the rotation would do, then rotate.

   ```sh
   ansible-playbook qumulo.cluster.admin_password_rotate -i inventory -l edge01 --ask-vault-pass --check
   ansible-playbook qumulo.cluster.admin_password_rotate -i inventory -l edge01 --ask-vault-pass
   ```

   Use `--vault-password-file` in place of `--ask-vault-pass` if you keep the vault password in a file.
   The playbook sets `allow_admin_password_change` and `allow_management_disruption` on the task itself, so you pass
   neither.

   The check run logs in with the new password and, when the cluster rejects it, reports `Change admin's password` as
   changed and writes nothing.
   The real run changes the password, then connects the rest of the run with the new one and logs in once more to
   confirm it.
   No task shows either password.
   To confirm, run it again without `--check`: `Change admin's password` is `skipping`.

3. Make the new password the host's `qumulo_api_password`, and remove `qumulo_api_password_after`.
   Usually that means moving the `!vault` block from `qumulo_api_password_after` to `vault_qumulo_api_password` in the
   same file, in place of the old block.
   The block doesn't record its variable name, so it can move.

   Until you do, every other playbook still logs in with the old password, and fails.

## Verify

Run the read-only play from [Check the connection](set-up-inventory.md#check-the-connection) against the host.
The host reports `ok` and the debug task prints the cluster's name, UUID and version, which shows that the inventory
now holds the password the cluster accepts.

## Variants

- **Rotate several clusters.**
  Put `qumulo_api_password_after` in each host's `host_vars`, finish one host, then drop `-l` or widen it.
  Each host decides for itself whether `admin` already logs in with its new password.
- **Do it in your own play.**
  Use [local_user](../../plugins/modules/local_user.py) with `update_password: always`,
  `allow_admin_password_change: true` and `allow_management_disruption: true`, register the result, and set
  `qumulo_api_password` to the new value when `connection_password_changed` is true.
  Copy the `Rotate the admin password` and `Connect with the new admin password from here on` tasks from its examples.
  Without the playbook's first login, the task changes the password on every run, and a second run fails to log in until
  the inventory holds the new password.
- **A host that connects as another user.**
  The playbook still works: `connection_password_changed` isn't set, so the connection isn't switched, and the confirm
  login runs.
  In step 3, leave `qumulo_api_password` as it is, and keep `admin`'s new password wherever you store it.

## If it fails

- `set qumulo_api_password_after to admin's new password` means the host has no value for the variable.
  Check that the encrypted entry is in that host's `host_vars` and that the name is spelled exactly.
- A refusal that names `allow_admin_password_change` or `allow_management_disruption` comes from your own task, not
  from this playbook.
  [Respond to a refusal](respond-to-a-refusal.md#change-the-password-of-admin) says how to give the flag.
- Any other failure of `Log in as admin with the new password...` is a connection problem;
  [Troubleshooting](../reference/troubleshooting.md#connecting) lists those messages.
- A decryption error at the start of the run means `qumulo_api_password_after` was encrypted with a different vault
  password than the one the run was given.
- `... password cannot be read back, so whether the change took effect is unknown` (`maybe_applied: true`) means the
  connection dropped after the change was sent.
  Run the playbook again: its first login finds out whether the new password took.
  The cluster never shows the password back ([G-PASSWORD](../reference/gaps.md)), so the login is the only test.
  See [Troubleshooting](../reference/troubleshooting.md#a-change-that-may-or-may-not-have-been-applied) and
  [State management](../explanation/state-management.md#maybe-applied).
- Other messages are in [Troubleshooting](../reference/troubleshooting.md).

## See also

- [admin_password_rotate.yml](../../playbooks/admin_password_rotate.yml), the playbook, with the same steps in its
  header comment.
- [local_user](../../plugins/modules/local_user.py) and [cluster_guard](../../plugins/modules/cluster_guard.py), the
  modules the playbook uses.
- [Variables](../reference/variables.md#connection) lists `qumulo_api_password_after` in its connection table, as a
  per-host variable of this playbook.
- [Keep secrets out of the inventory](store-secrets.md) covers vault passwords and lookups.
- [Authentication](../explanation/authentication.md#passwords-the-cluster-does-not-show-back) explains why the playbook
  logs in with the new password first, and what `connection_password_changed` is for.
- [Declare common settings in the inventory](inventory-recipes.md) covers the passwords of other local users, which the
  playbook doesn't change.
