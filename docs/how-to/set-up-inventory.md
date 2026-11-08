<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Set up an inventory for your clusters

This page lays out an inventory directory for hubs and edges, gives every host its connection, and checks with a
read-only run that the modules reach each cluster.
When you finish, `ansible-playbook -i inventory` can log in to every cluster you listed, and nothing has changed on
any of them.

## Before you start

- Install the collection: [Install and pin the collection](install-the-collection.md).
- For each cluster, have its address, the port its REST API answers on (8000 unless you changed it) and the password
  of its `admin` user.
- Have the certificate of the CA that signed each cluster's TLS certificate.
  A cluster that still serves its factory certificate cannot be verified yet (G-FACTORY-TLS, see
  [Gaps](../reference/gaps.md)).
  Set up its inventory with these steps, read its UUID with `curl -k` in step 3, and skip "Check the connection" for it.
  Then follow [Replace the factory certificate](manage-certificates.md#replace-the-factory-certificate), which installs
  the certificate in a run of its own, with verification off for that run, before you apply the baseline.
- Check that the controller reaches each cluster over HTTPS.

## Copy the example inventory

[`examples/inventory/`](../../examples/inventory/) is a complete inventory for one hub and one edge, with a comment on
every variable.
An installed collection carries it in `qumulo/cluster/examples/inventory`, under the directory that
`ansible-galaxy collection list qumulo.cluster` prints on its `# ...ansible_collections` line.

1. Make a project directory and copy the example into it as `inventory/`:

   ```sh
   mkdir -p project/files
   cp -r <path that ansible-galaxy prints>/qumulo/cluster/examples/inventory project/inventory
   cd project
   ```

   The result looks like this.
   `files/` holds what is not inventory and is yours to create:

   ```text
   project/
     files/                      certificates you add; outside the inventory directory
       qumulo-ca.pem
       edge01-chain.pem
     inventory/
       hosts.yml                 the hosts, their addresses and UUIDs
       group_vars/
         qumulo_clusters.yml     the connection, and what every cluster shares
         qumulo_edges.yml        what every edge shares
         qumulo_hubs.yml         what every hub shares
       host_vars/
         edge01.yml              what one edge holds
         hub01.yml               what one hub holds
   ```

   Keep certificates, captures, rebuild records and every other file that is not inventory outside `inventory/`.
   Given a directory, Ansible tries to read every file in it as an inventory.

2. Put the CA that signed your clusters' certificates in `files/qumulo-ca.pem`.
   To keep it elsewhere, set `qumulo_ca_path` in `inventory/group_vars/qumulo_clusters.yml` to its path.
   The example ships no certificates.
   `host_vars/edge01.yml` also reads `files/edge01-chain.pem` for the certificate the baseline installs on that edge.
   Provide that file, or delete `qumulo_ssl` from the host's file.

3. Read each cluster's UUID.
   `GET /v1/node/state` reports it as `cluster_id` and needs no login.
   `--cacert` checks the cluster's certificate against your CA:

   ```sh
   curl --cacert files/qumulo-ca.pem https://10.0.0.10:8000/v1/node/state
   ```

   For a cluster that still serves its factory certificate, `--cacert` fails.
   Use `curl -k` for this one read, which skips the check, and do not send a password with it.

4. In `inventory/hosts.yml`, replace each host's `qumulo_api_host` and `qumulo_cluster_uuid` with your clusters'.
   Rename the hosts to your own names, and rename their `host_vars/` files to match.
   A cluster that does not answer on port 8000 also needs `qumulo_api_port` on its host.
   Each host sits under the group for its kind:

   ```yaml
   # inventory/hosts.yml
   all:
     children:
       qumulo_clusters:
         children:
           qumulo_hubs:
             hosts:
               hub01: {qumulo_api_host: 10.0.0.10, qumulo_cluster_uuid: aaa55f64-ed03-4d6d-a600-6889d7b20ee5}
           qumulo_edges:
             hosts:
               edge01: {qumulo_api_host: 10.20.0.5, qumulo_cluster_uuid: cbb6f13a-d92f-4dfb-a7f0-d0a6785da65d}
   ```

   Put each edge under `qumulo_edges` and each hub under `qumulo_hubs`.
   Add a cluster by adding a host under its group, with a `host_vars` file of the same name.
   The edge rebuild finds each portal's hub in `qumulo_hubs` by its `qumulo_cluster_uuid`, so list every hub there
   ([Pair an edge with its hub through a portal](pair-edge-with-hub.md)).
   [Inventory variables](../reference/variables.md#groups) says which playbook reads each group.

   In each `host_vars` file, set `qumulo_cluster_name` to the cluster's current name, or delete it.
   Otherwise the baseline renames the cluster to the example's name.
   When you rename a hub, change the host names that refer to it in the edges' files: `qumulo_portal.hub` and each
   replication `target`.

   For a cluster you will [adopt](adopt-a-cluster.md), delete its `host_vars` file instead, and take its password from
   the environment as in [Start from one cluster and no example](#start-from-one-cluster-and-no-example).
   Adoption writes the file.

5. Replace the example's secrets.
   They are placeholders, encrypted with the vault password `example`.
   Each host's admin password is in `host_vars/<host>.yml`, edge01's certificate key, its local user's password and its
   license document are there too, and the Active Directory join password is in `group_vars/qumulo_clusters.yml`.
   [Keep secrets out of the inventory](store-secrets.md) shows how to make each, including the option of reading a
   password from the environment.
   [Encrypt a license document](store-secrets.md#encrypt-a-license-document) covers the license document.

6. Delete every variable for an area you do not want to manage yet.
   An unset variable leaves its area alone, so an inventory can declare only DNS and time and grow from there.
   The areas and their variables are in [Inventory variables](../reference/variables.md#areas).

## Connect the hosts

`inventory/group_vars/qumulo_clusters.yml` in the example already holds the connection.
The hosts are REST endpoints, so the modules run on the controller
([Architecture](../explanation/architecture.md)).
Every playbook in the collection hands these variables to every module with `module_defaults` for
`group/qumulo.cluster.qumulo`.

1. Check that the group file has these lines, or write them:

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   ansible_connection: local
   ansible_python_interpreter: "{{ ansible_playbook_python }}"
   qumulo_api_port: 8000
   qumulo_api_username: admin
   qumulo_api_password: "{{ vault_qumulo_api_password }}"
   qumulo_validate_certs: true
   qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
   ```

   Each host's own `host_vars` file can set any of these for that host, and outranks the group.
   The table of connection variables, with each module option and default, is in
   [Inventory variables](../reference/variables.md#connection).

2. Set exactly one of `qumulo_api_password` and `qumulo_api_token` for each host.

3. Keep `qumulo_cluster_uuid` set on every host.
   A module reads the cluster's UUID before it sends a password and fails when it is not that value; without one the
   check is off and the modules warn ([the identity guard](../explanation/authentication.md#the-identity-guard)).

4. Set connection variables in the inventory, never with `-e`: an extra variable outranks the facts the certificate
   step sets during a run, and reaches the connection to a hub too, not only the cluster the play runs on.
   Do not pass `qumulo_validate_certs`, `qumulo_api_host` or another connection variable with `-e`.

Give Ansible the inventory directory, `-i inventory`, not `hosts.yml`, so that it also reads `group_vars/` and
`host_vars/` beside the hosts file.

## Check the connection

Save this as `first-run.yml` in the project directory.
It reads each cluster and changes nothing.

```yaml
- name: Read each cluster, changing nothing
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
    - name: Read the cluster's identity, version, license and capacity
      qumulo.cluster.cluster_info:
      register: first_run

    - name: Show what it read
      ansible.builtin.debug:
        msg: >-
          {{ first_run.cluster.cluster_name }} is {{ first_run.cluster.cluster_id }},
          Qumulo Core {{ first_run.cluster.version }}
```

The `module_defaults` block is the header every playbook of the collection starts with.
It is copied from [`playbooks/baseline.yml`](../../playbooks/baseline.yml).
Copy it into any playbook of your own.

Run it, adding `--ask-vault-pass` or `--vault-password-file` when the inventory holds vault values:

```sh
ansible-playbook -i inventory first-run.yml --ask-vault-pass
```

Verify the result: each host reports `ok`, and the debug task prints its cluster's name, UUID and version.
If a host fails, its message says what to fix.
[Troubleshooting](../reference/troubleshooting.md#connecting) lists the connection messages and what each means.

## Variants

### Start from one cluster and no example

Make `inventory/hosts.yml` and `inventory/group_vars/qumulo_clusters.yml` by hand, with the layout above and only the
lines you need.
Every cluster the collection manages is a host in the `qumulo_clusters` group:

```yaml
# inventory/hosts.yml
all:
  children:
    qumulo_clusters:
      hosts:
        cluster01:
          qumulo_api_host: 10.0.0.10
          qumulo_cluster_uuid: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
```

In `group_vars/qumulo_clusters.yml`, take the password from the environment so that it is never written down:

```yaml
# inventory/group_vars/qumulo_clusters.yml
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
qumulo_api_username: admin
qumulo_api_password: "{{ lookup('ansible.builtin.env', 'QUMULO_PASSWORD_' ~ inventory_hostname | upper) }}"
qumulo_validate_certs: true
qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
```

```sh
read -rs QUMULO_PASSWORD_CLUSTER01 && export QUMULO_PASSWORD_CLUSTER01
```

Keep the password in the environment until you have [adopted the cluster](adopt-a-cluster.md).
Adoption writes `host_vars/cluster01.yml` and fails instead of replacing a file that already exists.
Afterwards, move the password into the project, encrypted ([Keep secrets out of the inventory](store-secrets.md)).

### Use an access token

Encrypt the token as `vault_qumulo_api_token` in the host's `host_vars` file
([Keep secrets out of the inventory](store-secrets.md)).
Then set `qumulo_api_token: "{{ vault_qumulo_api_token }}"` and `qumulo_api_password: ""` on that host, or move the
password line out of `group_vars` if no cluster uses one.
The client refuses a host that has both set.
A token is bound to one cluster and dies with it in a rebuild (G-TOKEN, see [Gaps](../reference/gaps.md)), so a
password is the safer choice for an edge.
[Authentication](../explanation/authentication.md) explains the trade-off.
To replace the token with a new one and keep the play connected, see
[Rotate the access token a host connects with](rotate-access-token.md).

### Add a hub you formed yourself

The example's `hub01` is a Cloud Native Qumulo cluster that the Terraform provider formed, and its group file says so.
For a hub formed any other way, set these in `inventory/group_vars/qumulo_hubs.yml`:

```yaml
qumulo_managed_by: none
qumulo_terraform_owned_areas: []
```

Then give the network's real type in `host_vars/hub01.yml`, or delete `qumulo_network` there.
If your hubs are of both kinds, set the two variables in each hub's `host_vars` file instead of the group file.

### Read the clusters from a Terraform root

The `qumulo.cluster.terraform` inventory plugin sets `qumulo_api_host` and `qumulo_cluster_uuid` for each host from the
root's output, and puts each host in a group for its kind (`qumulo_edges`, `qumulo_cnq` or `qumulo_baremetal`) and a
`qumulo_managed_by_<value>` group, all under `qumulo_clusters`.
It never puts a host in `qumulo_hubs`: add hubs with the plugin's `groups` option or a static inventory file
([Put a host in `qumulo_hubs`](terraform.md#put-a-host-in-qumulo_hubs)).
[Use a Terraform root's output as the inventory](terraform.md) has the setup.

### Update the UUID of a rebuilt edge

A rebuilt edge has a new UUID.
Apply the `host_vars` patch the rebuild report gives, in `hosts.yml` or in `host_vars/<host>.yml`
([Rebuild an unprotected edge](edge-rebuild.md)).

## Next

- [Adopt a cluster you already run](adopt-a-cluster.md) captures what a cluster holds into `host_vars`.
- [Keep secrets out of the inventory](store-secrets.md) covers the vault forms and lookups.
- [Declare common settings in the inventory](inventory-recipes.md) has a recipe for each area.
- [Inventory variables](../reference/variables.md) lists every variable, its precedence and its owner.
- [Authentication](../explanation/authentication.md) explains the UUID check and TLS.
