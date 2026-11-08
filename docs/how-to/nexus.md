<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Register a cluster with Nexus

This page turns on cloud monitoring and the Nexus connection for a cluster, registers it with Nexus, and retires a
cluster UUID that no cluster will use again.
When you finish, `monitoring_info` reports the cluster's registration as `REGISTERED`, and a second baseline run
reports no change for the registration.
Nexus is Qumulo's service for registration, remote support and licenses; a new cluster's license is issued for the
UUID Nexus sees once the cluster is registered
([why](../explanation/licensing-and-capacity.md#nexus-and-registration)).

## Before you start

- Set up the inventory and a working connection: [Set up an inventory for your clusters](set-up-inventory.md).
- Choose how the cluster registers, and have what it needs:
  - A join key made in the Nexus web interface, one for each cluster.
  - Or a Nexus user's API token.
    The user needs a Nexus role that may create API tokens in the organization.
- Plan to encrypt the key or token ([Keep secrets out of the inventory](store-secrets.md)).
- Turn cloud monitoring on in the same declaration, unless the cluster already has it on.
  The Nexus connection needs it, and `nexus_connection` fails before it writes when monitoring is off.

## Declare monitoring and the Nexus connection

1. Declare `qumulo_monitoring` for the clusters, in `group_vars/qumulo_clusters.yml`.
   The baseline applies it at step 7, in this order: `monitoring_settings`, `nexus_connection`, then
   `nexus_registration`.
   A key you leave out keeps the cluster's value.

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_monitoring:
     enabled: true
     nexus:
       nexus_enabled: true
       nexus_capability_remote_support: true
   ```

   [`examples/inventory/group_vars/qumulo_clusters.yml`](../../examples/inventory/group_vars/qumulo_clusters.yml)
   declares the same.
   The keys are in [Variables](../reference/variables.md#monitoring-and-nexus).

   A host whose `host_vars` declares its own `qumulo_monitoring`, as an adopted or cloned host does, ignores the
   group's.
   Change that host's dict instead.

2. Give the baseline a way to register.
   The baseline registers a cluster only when `qumulo_monitoring.nexus.nexus_enabled` is true and the inventory gives
   a join key or an API token.
   With neither, it applies the settings and skips the registration without a message.
   Pick one of the two sections below.

### Register with a join key

A join key registers one cluster only ([G-NEXUS-KEY](../reference/gaps.md)), so each cluster has its own, and a rebuilt
edge needs a new one.

1. Encrypt the key, giving `vault_nexus_join_key` at the variable name prompt.

   ```sh
   ansible-vault encrypt_string --prompt
   ```

2. Paste the output into the host's file.

   ```yaml
   # inventory/host_vars/edge01.yml
   vault_nexus_join_key: !vault |
     $ANSIBLE_VAULT;1.1;AES256
     ...
   ```

3. Refer to it from the group's `qumulo_monitoring`, once.

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_monitoring:
     enabled: true
     nexus:
       nexus_enabled: true
       nexus_capability_remote_support: true
     nexus_join_key: "{{ vault_nexus_join_key | default('') }}"
   ```

A host that declares its own `qumulo_monitoring` ignores the group's, so put `nexus_join_key` inside that dict, as an
encrypted value
([Keep secrets out of the inventory](store-secrets.md#encrypt-a-value-inside-a-dict-or-a-list-item)).

```yaml
# inventory/host_vars/edge01.yml
qumulo_monitoring:
  enabled: true
  nexus:
    nexus_enabled: true
    nexus_capability_remote_support: true
  nexus_join_key: !vault |
    $ANSIBLE_VAULT;1.1;AES256
    ...
```

### Register with an API token

With a token, the registration mints a single-use join key for each cluster, so no key is kept in the inventory.

1. Encrypt the token, giving `qumulo_nexus_api_token` at the variable name prompt, and paste the output into the group
   file.

   ```sh
   ansible-vault encrypt_string --prompt
   ```

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_nexus_api_token: !vault |
     $ANSIBLE_VAULT;1.1;AES256
     ...
   ```

2. When the token's user belongs to more than one Nexus organization, say which one the cluster joins.
   Add `nexus_account_id` to `qumulo_monitoring`.

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_monitoring:
     enabled: true
     nexus:
       nexus_enabled: true
       nexus_capability_remote_support: true
     nexus_account_id: 1234
   ```

3. To use a Nexus API other than the modules' default, set `qumulo_nexus_api_url` to its `https` URL.

## Apply the registration

1. Preview the change for one cluster.
   The check run predicts the registration and mints no key.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ```

   The run reports every change the baseline would make for the host, not only these.
   With cloud monitoring or the Nexus connection off on the cluster, the check run warns that the earlier task in the
   same run turned nothing on, and still predicts the later one.

2. Apply it, with the vault option your inventory needs.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --ask-vault-pass
   ```

   When the registration fails, the task fails with Nexus's reason
   ([why](../explanation/licensing-and-capacity.md#why-registration-is-synchronous)).

A cluster that is already registered is left alone, so a later run neither spends a join key nor mints one.
Registering does not issue a license: [License an edge](licensing.md) is the next step for a new edge.

## Verify

Read the registration back with `monitoring_info`, which returns no secret.
Save this as `nexus-check.yml` in the project directory; its header is the one in
[Set up an inventory for your clusters](set-up-inventory.md#check-the-connection).

```yaml
- name: Check the Nexus registration, changing nothing
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
    - name: Read the Nexus connection and registration
      qumulo.cluster.monitoring_info:
        gather_subset: [nexus_connection, nexus_registration]
      register: nexus_state

    - name: Show them
      ansible.builtin.debug:
        msg: >-
          Nexus connection on: {{ nexus_state.monitoring.nexus_connection.nexus_enabled }},
          registration: {{ nexus_state.monitoring.nexus_registration.registration_status }}
```

```sh
ansible-playbook nexus-check.yml -i inventory -l edge01 --ask-vault-pass
```

The registration status is `REGISTERED`.
Then run the baseline again with `--check --diff`: it reports no change for the registration.

If registration fails, or the status stays `PENDING_REGISTRATION`, a join key may be spent: replace
`vault_nexus_join_key` with a new key, or delete it and set `qumulo_nexus_api_token`.
A join key in the inventory is always tried before the token.
[Troubleshooting](../reference/troubleshooting.md#a-change-that-may-or-may-not-have-been-applied) lists the message
for a registration whose answer was lost.

## Variants

### Change what the cluster allows through Nexus

`nexus_capability_remote_support` lets Qumulo Care reach the cluster through Nexus, and `nexus_capability_sso` lets
users sign in through Nexus single sign-on.
`nexus_host`, `nexus_port` and `nexus_interval` set where the cluster connects and how often.
Change them under `qumulo_monitoring.nexus` and apply the baseline as above.

### Disconnect a cluster from Nexus

Set `qumulo_monitoring.nexus.nexus_enabled: false`.
The baseline then skips the registration step, and the cluster's registration stays.
To turn cloud monitoring off too, set `qumulo_monitoring.enabled: false` and
`qumulo_monitoring.nexus.nexus_enabled: false` together.
With `nexus_enabled` still true, the connection task fails once monitoring is off, and a check run shows only a
warning.

### Let Terraform own monitoring

If a Terraform root configures monitoring, list `monitoring` in `qumulo_terraform_owned_areas` and remove
`qumulo_monitoring` from the inventory.
The baseline refuses it at step 2 otherwise, and without it the baseline neither connects nor registers the cluster.
On a cluster of kind `cnq` or unset that the Terraform provider formed, keep `network.floating_ips` in the list,
because a list replaces that default.
[Variables](../reference/variables.md#ownership) lists the areas, and
[how Terraform and the collection share a cluster](../explanation/terraform-ownership.md) says why.

## Retire a UUID in Nexus

A rebuilt edge gets a new UUID, and its old one stays listed in Nexus
([G-NEXUS-RETIRE](../reference/gaps.md); [Rebuild an unprotected edge](edge-rebuild.md)).
If the rebuild ran with `qumulo_nexus_api_token`, its predestroy phase or its replay already retired the old UUID.
Skip this section.
Otherwise the rebuild's report names the UUID.
Retire it with the play below, which needs a Nexus API token, or ask Qumulo Care to retire it.

Retire a UUID that no cluster will use again.
Nexus offers no way back, and a cluster given a new join key under a retired UUID registers again while Nexus keeps
listing it as decommissioned.

1. Save this as `nexus-retire.yml` in the project directory.
   The connection options must name a reachable cluster with its credentials, such as the new edge, although the
   cluster that had the UUID no longer exists.
   The token's user needs a Nexus role that may decommission clusters in the cluster's organization.

   ```yaml
   - name: Retire a UUID in Nexus
     hosts: edge01
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
       - name: Retire the UUID the edge had before its rebuild
         qumulo.cluster.nexus_cluster:
           cluster_uuid: "{{ nexus_retire_uuid }}"
           nexus_api_token: "{{ qumulo_nexus_api_token | default(omit) }}"
           nexus_api_url: "{{ qumulo_nexus_api_url | default(omit) }}"
   ```

2. Give the play a token: put it in the inventory, encrypted as in
   [Register with an API token](#register-with-an-api-token), or export `QUMULO_NEXUS_API_TOKEN` for the run.
   The `nexus_cluster` module reads that variable when `nexus_api_token` is unset.

3. Preview it with the old UUID.
   Check mode reads Nexus, so it needs the token too, and it sends nothing.

   ```sh
   ansible-playbook nexus-retire.yml -i inventory -e nexus_retire_uuid=<old UUID> --ask-vault-pass --check --diff
   ```

   If the preview or the run fails because Nexus still reports the cluster connected, which it may for a short while
   after the registration was removed, add `allow_connected: true` to the task.

4. Run it without `--check`.

   ```sh
   ansible-playbook nexus-retire.yml -i inventory -e nexus_retire_uuid=<old UUID> --ask-vault-pass
   ```

To verify, run step 4 again: Nexus lists the UUID as decommissioned, so the task reports no change.

To retire a cluster that still exists, remove its registration first with `nexus_registration` and `state: absent`;
`nexus_cluster` checks that the registration is gone when the connection names the cluster being retired.
Removing the registration does not retire the UUID.

## See also

- Reference: [Variables](../reference/variables.md#monitoring-and-nexus) for the `qumulo_monitoring` keys,
  [`nexus_connection`](../../plugins/modules/nexus_connection.py),
  [`nexus_registration`](../../plugins/modules/nexus_registration.py),
  [`nexus_cluster`](../../plugins/modules/nexus_cluster.py) and
  [`monitoring_info`](../../plugins/modules/monitoring_info.py), and the registers
  [G-NEXUS-KEY and G-NEXUS-RETIRE](../reference/gaps.md).
- Why: [Licensing and capacity](../explanation/licensing-and-capacity.md).
- Next: [License an edge](licensing.md).
- Related: [Rebuild an unprotected edge](edge-rebuild.md),
  [Send the audit log to syslog](inventory-recipes.md#send-the-audit-log-to-syslog).
