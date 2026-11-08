<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Rotate the access token a host connects with

Replace the access token a host's `qumulo_api_token` holds, keep the play connected, and put the new bearer in the vault.
When you finish, the cluster accepts the new token, the vault holds its bearer in place of the old one, and no plain-text
copy of it is left on the controller.

The collection makes the new token before it deletes the old one, and deletes the old one at the end of the task.
Anything else that connects with that user's token loses access then, until it holds the new bearer.
[Authentication](../explanation/authentication.md#access-tokens-the-collection-makes) explains why.

## Before you start

- Have a host that connects with `qumulo_api_token`, encrypted as `vault_qumulo_api_token` the way
  [Set up an inventory](set-up-inventory.md#use-an-access-token) shows.
- Use a user for that token that no other consumer shares.
  This page calls it `local:svc-ansible`.
  A user holds at most two tokens, and the rotation is refused before any write when the user holds two.
- Choose a directory for the new bearer's file, outside version control.
  The play below uses `artifacts/<host>/secrets` beside the inventory.
- A token of a user in Administrators is a full administrator's credential.
  Treat its file as you treat the admin password.

## Rotate the token

1. Save this as `rotate-token.yml` in the project directory.

   ```yaml
   - name: Rotate the access token each cluster is reached with
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
       - name: Rotate the token this play connects with
         qumulo.cluster.access_token:
           user: local:svc-ansible
           state: rotated
           allow_management_disruption: true
           secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
         when: qumulo_api_token | default('') | length > 0
         register: own_token
         no_log: true

       - name: Check that this host connects with a token of that user
         ansible.builtin.assert:
           that: own_token.bearer_token is not defined or own_token.connection_token_changed | default(false)
           fail_msg: >-
             {{ inventory_hostname }}: this host does not connect with a token of local:svc-ansible; a token was made,
             and its bearer is in {{ own_token.secret_file | default('') }}; the rotation deleted any old token
             of local:svc-ansible; see If it fails

       - name: Connect with the new token from here on
         ansible.builtin.set_fact:
           qumulo_api_token: "{{ own_token.bearer_token }}"
         when: own_token.connection_token_changed | default(false)
         no_log: true

       - name: Say where the new bearer is
         ansible.builtin.debug:
           msg: "{{ inventory_hostname }}: the new bearer is in {{ own_token.secret_file }}"
         when: own_token.secret_file is defined

       - name: Read the cluster with the new token
         qumulo.cluster.cluster_info:
         when: own_token.connection_token_changed | default(false)
   ```

   `no_log` is required on the rotation, and `secret_destination` too: the module refuses to make a token without
   either.
   `allow_management_disruption` lets the task delete the token the play connects with.
   It deletes that token last, so the play is still connected until the task ends.
   The `when` skips a host that connects with a password, so the rotation leaves it alone, and the `assert` passes for
   it because a skipped rotation makes no bearer.
   The `assert` stops the play when the rotation made a token for a host that does not connect with one of that user's
   tokens, such as a host that uses another user's token.
   By then the rotation has deleted the user's old tokens; the message says so and names the file that holds the new
   bearer, and the "If it fails" list below says what to do.
   The `set_fact` makes every later task of the play connect with the new bearer.
   A check run reports the rotation as changed and makes no token, so the `assert` passes and the `set_fact`, the
   message and the read are skipped; `no_log` hides the rotation's diff.

2. Preview the run on one host, then run it.

   ```sh
   ansible-playbook rotate-token.yml -i inventory -l edge01 --check --ask-vault-pass
   ansible-playbook rotate-token.yml -i inventory -l edge01 --ask-vault-pass
   ```

   The preview shows only that the rotation changes something, because `no_log` hides its diff.
   The real run ends with the path of the file that holds the new bearer, and the last task reads the cluster with it.
   Use `--vault-password-file` in place of `--ask-vault-pass` if you keep the vault password in a file.

3. Put the new bearer in the vault.
   Until you do, every other playbook still connects with the old token, which the cluster has deleted.
   Open the file the run named, which holds `user`, `access_token_id` and `bearer_token`, and copy the bearer.

   ```sh
   ansible-vault encrypt_string --prompt
   ```

   Give the vault password the inventory's other values use, `vault_qumulo_api_token` as the name, and paste the bearer
   as the secret.
   Replace the `vault_qumulo_api_token` block in the host's `host_vars` file with the output.

4. Check that the vault's bearer logs in.
   Run the read-only play from [Check the connection](set-up-inventory.md#check-the-connection) with the vault option,
   and `-l edge01` as in step 2.
   The host reports `ok`, which shows that the vault holds a bearer the cluster accepts.
   If the host fails with `api_token was rejected`, the copy in the vault is wrong: copy the bearer from the file again,
   which still exists, put it in the vault as in step 3, and check again.

5. Remove the plain-text copies, once step 4 reports `ok`.
   The module leaves each bearer's file in `secret_destination`, so the new bearer's file and the files of earlier,
   deleted tokens stay there.
   Delete the new file; the others hold bearers that no longer work.

## If it fails

- A refusal's message is printed in the `[ERROR]` line even with `no_log`.
  The module's messages never hold a bearer.
- `holds ... the most access tokens a user can hold` means the user already holds two tokens.
  The message names each token and marks the one in use and any that expired.
  To keep the token marked in use, whose bearer the vault holds, delete the other by its id, outside the collection:
  `qq auth_delete_access_token <id>` or `DELETE /v1/auth/access-tokens/<id>`, then run the rotation again.
  `qq` needs a login of its own; it cannot read the module's file.
  For the REST call, use a bearer of that user that logs in: the vault's, or, after a failed rotation, the new one once
  step 4 reports `ok`.
  Put the line `Authorization: Bearer <bearer>` in a file in the `secret_destination` directory, which the module made
  readable only by its owner; that keeps the file, and any editor swap file, private whatever the umask.
  Then run `curl -X DELETE -H @<file> https://<host>:8000/v1/auth/access-tokens/<id>`, adding `--cacert <file>` when the
  cluster's certificate is not in the system's store.
  The bearer stays out of the shell history and the process list.
  Delete the header file after the call.
  To replace both, run `state: absent`, then `state: present`, in a play that connects with a password, and put the new
  bearer in the vault as in steps 3 to 5.
- A failure that says `a token whose bearer is lost may be left` means the answer to the create was lost and the tokens
  could not be listed after it.
  The message gives `state: absent`, then `state: present`, which needs a play that connects with a password, and
  putting the new bearer in the vault as in step 3.
  The cheaper path is to run the rotation again.
  If the user still holds only the old token, it succeeds.
  If it is refused at two tokens, delete the token not marked in use, the newer one, by its id outside the collection as
  in the first bullet, then run it again.
- A failure that says `made access token ... whose bearer is in ...` means the new token exists and its bearer is in the
  file the message names, but a later step failed.
  The user may hold two tokens, and no option deletes only the old one ([G-TOKEN](../reference/gaps.md)).
  Unless the message says the old token may have been deleted, both tokens log in until one is removed, so the host
  keeps connecting.
  Put the new bearer from the file in the vault and check that it logs in (steps 3 and 4).
  If the message says instead that the old token may have been deleted, it names the check and the command: list the
  user's tokens, with the `access_tokens` subset of `identity_info`, first, and delete the old one only if it is still
  listed.
  Delete only the old token, by the id the message names, outside the collection:
  `qq auth_delete_access_token <id>` or `DELETE /v1/auth/access-tokens/<id>`, as in the first bullet.
  The new token stays. Remove its file as in step 5.
- `does not connect with a token of local:svc-ansible` means the host connects with another user's token, which still
  works.
  The rotation made a new token for `svc-ansible` and deleted any old one.
  If something else used `svc-ansible`'s token, give it the bearer from the file the message names.
  Otherwise remove the new token with `state: absent` for `svc-ansible`; the play's own token is another user's, so
  continuity does not block it.
  Delete the file once that consumer's copy logs in, or at once if you removed the token.
  Then change `user:` to the user whose token the host holds.
- `a rotation with enabled: false would replace it` means the task connects with the token it would rotate and sets
  `enabled: false`, which would leave the play no token to connect with.
  Rotate without `enabled: false`.

## See also

- [Keep secrets out of the inventory](store-secrets.md) encrypts the token and the other secrets an inventory holds.
- [Set up an inventory for your clusters](set-up-inventory.md#use-an-access-token) connects a host with a token.
- [Authentication, trust and secrets](../explanation/authentication.md#access-tokens-the-collection-makes) explains
  where the bearer appears and why a rotation makes the new token first.
- [access_token](../../plugins/modules/access_token.py) has every option, and `ansible-doc qumulo.cluster.access_token`
  shows them.
