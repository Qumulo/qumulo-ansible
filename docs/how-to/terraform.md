<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Use a Terraform root's output as the inventory

This page publishes one output from a Terraform root that forms Qumulo clusters, points the `qumulo.cluster.terraform`
inventory plugin at it, and resolves the admin password.
When you finish, each cluster the root forms is a host in your inventory, with its address, UUID and ownership set from
the output, and a check run of the baseline reaches it.
Terraform forms the clusters and this collection configures them afterwards; the two meet only in this output
([why](../explanation/terraform-ownership.md)).

## Before you start

- Set up the inventory directory: [Set up an inventory for your clusters](set-up-inventory.md).
  The plugin's source file goes in it, in place of the `hosts.yml` entries for these clusters.
- Have a Terraform root that forms the clusters, and run Terraform 1.11 or later if you use the write-only password
  argument below.
- Have Terraform on the controller, or a saved copy of the output (see
  [Read a saved output](#read-a-saved-output-instead-of-running-terraform)).
- Install the collection that holds the lookup for the store your passwords are in.
  This collection depends on none.
- For a cluster Terraform did not form, such as an edge formed by Qumulo's installer, write its host entry by hand and
  [adopt it](adopt-a-cluster.md) instead.

## Publish the output

This section uses a cluster formed by the `qumulo_filesystem_aws` resource.
Other resources are under [Variants](#variants).

1. Keep the admin password in an SSM SecureString, written write-only so that it is in neither the plan nor the state:

   ```hcl
   resource "aws_ssm_parameter" "cnq01" {
     name             = "/qumulo/cnq01/admin-password"
     type             = "SecureString"
     value_wo         = var.admin_password
     value_wo_version = 1
   }
   ```

   `value_wo` needs Terraform 1.11 or later and an AWS provider release that has it.

2. Form the cluster with the same password, and publish one output named `qumulo_ansible_inventory`, a map from the
   Ansible host name to the cluster's fields:

   ```hcl
   resource "qumulo_filesystem_aws" "cnq01" {
     cluster_name   = "cnq01"
     admin_password = var.admin_password
     # region, subnet_ids, node_count, instance_type and the rest, as the root needs them
   }

   output "qumulo_ansible_inventory" {
     description = "Hosts for the qumulo.cluster.terraform inventory plugin. No secrets."
     value = {
       cnq01 = {
         api_host            = qumulo_filesystem_aws.cnq01.floating_ips[0]
         cluster_uuid        = qumulo_filesystem_aws.cnq01.cluster_uuid
         cluster_kind        = "cnq"
         managed_by          = "terraform_provider"
         owned_areas         = ["network.floating_ips"]
         password_ref        = { type = "aws_ssm_parameter", id = aws_ssm_parameter.cnq01.name }
         portal_hosts        = length(qumulo_filesystem_aws.cnq01.floating_ips) > 0 ? qumulo_filesystem_aws.cnq01.floating_ips : null
         replication_address = length(qumulo_filesystem_aws.cnq01.floating_ips) > 0 ? qumulo_filesystem_aws.cnq01.floating_ips[0] : null
       }
     }
   }
   ```

   - The output carries no secret, so it need not be marked `sensitive`.
   - `api_host` as written needs `floating_ip_count` of at least 1 on the resource.
   - Publish `portal_hosts` and `replication_address` as null, not as an empty list, when the cluster has no addresses
     for them.
     Indexing an empty list fails the root's own output, so check its length first, as above.
   - The plugin publishes the addresses you give it and derives none.
     To declare them in `host_vars` instead, leave both fields out; a `host_vars` value outranks the plugin's.
     [The Terraform inventory plugin](../reference/terraform-inventory.md) lists each field, the expression for each
     resource, and what the plugin refuses.

3. Apply, and read the output back:

   ```sh
   terraform apply
   terraform output -json qumulo_ansible_inventory
   ```

## Point the plugin at the output

1. Create the inventory source.
   Its file name must end in `qumulo_terraform.yml` or `qumulo_terraform.yaml`:

   ```yaml
   # inventory/prod.qumulo_terraform.yml: runs `terraform output -json qumulo_ansible_inventory` in the root
   plugin: qumulo.cluster.terraform
   project_path: ../terraform/prod      # relative to this file
   ```

   `ansible-doc -t inventory qumulo.cluster.terraform` lists every option, and
   [the plugin](../../plugins/inventory/terraform.py) carries examples for each.

2. Give the hosts their connection in `group_vars`.
   The hosts are REST endpoints and have no `ansible_connection`, so the modules run on the controller:

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   ansible_connection: local
   ansible_python_interpreter: "{{ ansible_playbook_python }}"
   qumulo_api_username: admin
   qumulo_api_password: >-
     {{ lookup('amazon.aws.ssm_parameter', qumulo_password_ref.id)
        if qumulo_password_ref.type == 'aws_ssm_parameter'
        else lookup('ansible.builtin.env', qumulo_password_ref.id) }}
   qumulo_validate_certs: true
   qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
   ```

   Ansible templates the password only when a task uses it, and the modules never log it.
   [Set up an inventory](set-up-inventory.md) covers the other connection variables.

3. Resolve the password with the lookup for your `password_ref.type`:

   | `password_ref.type` | `password_ref.id` | Lookup in `qumulo_api_password` |
   |---|---|---|
   | `env` | An environment variable's name | `lookup('ansible.builtin.env', qumulo_password_ref.id)` |
   | `aws_ssm_parameter` | An SSM parameter's name | `lookup('amazon.aws.ssm_parameter', qumulo_password_ref.id)` |
   | `aws_secrets_manager` | A Secrets Manager secret's name or ARN | `lookup('amazon.aws.secretsmanager_secret', qumulo_password_ref.id)` |
   | `azure_key_vault` | A secret identifier, `https://<vault>.vault.azure.net/secrets/<name>` | `lookup('azure.azcollection.azure_keyvault_secret', qumulo_password_ref.id.split('/secrets/')[1], vault_url=qumulo_password_ref.id.split('/secrets/')[0])` |
   | `gcp_secret_manager` | `projects/<project>/secrets/<name>` | `lookup('google.cloud.gcp_secret_manager', key=qumulo_password_ref.id.split('/')[3], project=qumulo_password_ref.id.split('/')[1])` |
   | `hashicorp_vault` | A KV v2 API path, `<mount>/data/<path>`, such as `secret/data/qumulo/edge01` | `lookup('community.hashi_vault.vault_kv2_get', qumulo_password_ref.id.split('/', 2)[2], engine_mount_point=qumulo_password_ref.id.split('/')[0]).secret.password` |

   - A root that uses one store sets `qumulo_api_password` to that one lookup.
   - For `hashicorp_vault`, the secret holds a map; `.secret.password` reads its `password` field, so change it to the
     field your root writes.
     The expression takes the mount as the id's first segment.
   - The legacy CNQ modules for AWS store a JSON map of `username` and `password` in the secret.
     Read its `password`: `(lookup('amazon.aws.secretsmanager_secret', qumulo_password_ref.id) | from_json).password`.
   - Which type the output names depends on where the root keeps the password: the legacy CNQ modules use AWS Secrets
     Manager, Azure Key Vault or GCP Secret Manager, and the provider uses Azure Key Vault on Azure.
     Elsewhere the provider takes the password as an argument and the root decides.

4. If the store throttles, read each password once.
   The play header of the collection's playbooks, such as [`baseline.yml`](../../playbooks/baseline.yml), passes
   `qumulo_api_password` to every module through `module_defaults`, so the lookup runs once for each task on each host.
   A baseline run makes dozens of requests to the store, and a store that throttles, as SSM's `GetParameter` does, can
   fail a large run.
   Either export the passwords into the environment before the run and use the `env` type, or wrap the playbook in
   one of your own:

   ```yaml
   # read-passwords-then-baseline.yml
   - name: Read each cluster's password once
     hosts: qumulo_clusters
     gather_facts: false
     tasks:
       - name: Resolve the admin password
         ansible.builtin.set_fact:
           # the lookup for your password_ref.type, from the table in step 3
           qumulo_api_password: "{{ lookup('amazon.aws.ssm_parameter', qumulo_password_ref.id) }}"
         no_log: true

   - ansible.builtin.import_playbook: qumulo.cluster.baseline
   ```

   A fact outranks `group_vars`, so the lookups in step 2 no longer run.

## Declare the areas the root owns

`owned_areas` lists the areas of the cluster that Terraform configures, and the `cluster_baseline` role refuses to
manage them.

1. With `managed_by` set to `terraform_provider` on a CNQ cluster, list `network.floating_ips`.
   The plugin refuses the entry otherwise.
   On an edge or bare-metal cluster the plugin does not ask for it. Leave it out when the baseline should manage the
   floating addresses, since the list you give is all that Terraform owns.
2. List every other area the root also configures, such as `dns.fqdn` when it sets the provider's `cluster_fqdn`, or
   `network` when it configures the networks.
3. Run the baseline.
   It fails before the first write on any `qumulo_*` variable that declares an owned area.

[Variables](../reference/variables.md) maps each area to the variables it guards, and
[Terraform ownership](../explanation/terraform-ownership.md) explains the rules and how to take an area over.

## Variants

### Read a saved output instead of running Terraform

Use this in CI, or where the controller has no Terraform.
Save the output where the source can read it, and name that file in `output_file`:

```sh
terraform -chdir=terraform/prod output -json qumulo_ansible_inventory > terraform-output/qumulo_ansible_inventory.json
```

```yaml
# inventory/ci.qumulo_terraform.yml
plugin: qumulo.cluster.terraform
output_file: ../terraform-output/qumulo_ansible_inventory.json   # relative to this file
```

- Name the output in the command.
  The plugin refuses a file that holds what `terraform output -json` prints without a name, because that lists every
  output of the root, sensitive ones in plain text.
- Keep the file outside the inventory directory.
  Given a directory, Ansible's `yaml` inventory plugin reads every `.json` file in it as an inventory.

### Select a workspace, cache the output or change the binary

Set these in the source file:

```yaml
plugin: qumulo.cluster.terraform
project_path: ../terraform/prod
workspace: prod                        # selected through TF_WORKSPACE for the one command
binary_path: /opt/terraform/1.11.0/terraform
timeout: 300                           # seconds; the default is 120
cache: true
cache_plugin: ansible.builtin.jsonfile
cache_connection: ~/.cache/qumulo-inventory
cache_timeout: 3600
```

- Without `workspace`, a `TF_WORKSPACE` in the environment applies, and otherwise the root's selected workspace.
  `workspace`, `binary_path` and `timeout` are for `project_path`.
  `workspace` with `output_file` fails the source.
- With `cache: true`, the plugin caches the hosts it read, after checking them, never the raw output.
  A workspace switched with `terraform workspace select` is read once the cache times out.
- `compose`, `groups` and `keyed_groups` work as for any constructed inventory.

### Put a host in `qumulo_hubs`

The plugin never adds a host to `qumulo_hubs`.
Add it with the `groups` option, or put it in a static inventory file:

```yaml
plugin: qumulo.cluster.terraform
project_path: ../terraform/prod
groups:
  qumulo_hubs: "inventory_hostname == 'hub01'"
```

### Publish a bare-metal edge

The `qumulo_filesystem_edge_baremetal` resource is create-only, so `managed_by` is `none`, and the edge's `endpoint`
is a URL, which `api_host` refuses:

```hcl
output "qumulo_ansible_inventory" {
  value = {
    edge01 = {
      api_host     = qumulo_filesystem_edge_baremetal.edge01.node_ips[0]
      cluster_uuid = qumulo_filesystem_edge_baremetal.edge01.cluster_uuid
      cluster_kind = "edge"
      managed_by   = "none"
      owned_areas  = []
      password_ref = { type = "env", id = "QUMULO_PASSWORD_EDGE01" }
      # The edge's node IP; give its floating IP instead when the root gives it one.
      portal_hosts = qumulo_filesystem_edge_baremetal.edge01.node_ips
    }
  }
}
```

Export `QUMULO_PASSWORD_EDGE01` before the run, or use another `password_ref.type`.

### Publish a multi-AZ cluster in AWS

A multi-AZ `qumulo_filesystem_aws` cluster has no floating IPs, so `floating_ips[0]` fails.
Its `endpoint_ips` are node IPs, which stay on their node when it goes down. Don't publish them.
Publish its network load balancer's addresses instead ([why](../explanation/edges-and-portals.md#addresses-behind-a-load-balancer)).
The provider forms no load balancer, so form one in the root, with TCP listeners on 8000 for the REST API
(`qumulo_api_port`) when `api_host` is its name, 3713 for portals and 3712 for replication.

Choose one of two routes.

#### Publish the load balancer's IPs

1. Read the IPs from the load balancer's network interfaces, one in each of its subnets.
   `count` follows the subnets, which the plan knows, so the first apply plans before the load balancer exists:

   ```hcl
   # One network interface per subnet; AWS describes it as "ELB" and the load balancer's ARN suffix.
   data "aws_network_interface" "nlb" {
     count = length(local.subnet_ids)
     filter {
       name   = "description"
       values = ["ELB ${aws_lb.qumulo.arn_suffix}"]
     }
     filter {
       name   = "subnet-id"
       values = [local.subnet_ids[count.index]]
     }
   }

   locals {
     nlb_ips = sort(data.aws_network_interface.nlb[*].private_ip)
   }
   ```

2. Publish them, with the load balancer's `dns_name` as `api_host`:

   ```hcl
   output "qumulo_ansible_inventory" {
     value = {
       cnq01 = {
         api_host            = aws_lb.qumulo.dns_name
         cluster_uuid        = qumulo_filesystem_aws.cluster.cluster_uuid
         cluster_kind        = "cnq"
         managed_by          = "terraform_provider"
         owned_areas         = ["network.floating_ips"]
         password_ref        = { type = "aws_ssm_parameter", id = aws_ssm_parameter.cnq01.name }
         portal_hosts        = length(local.nlb_ips) > 0 ? local.nlb_ips : null
         replication_address = length(local.nlb_ips) > 0 ? local.nlb_ips[0] : null
       }
     }
   }
   ```

   - A module that outputs its load balancer's interface ids, as an `nlb_eni_ids` output does, can give each
     `aws_network_interface` its `id` instead of the filters.
   - A root that pins the IPs with `subnet_mapping`'s `private_ipv4_address` can publish its own list instead.

#### Publish the load balancer's name

When the root has only the name, publish it as `load_balancer_name` in place of the two address fields, and the plugin
resolves it on the controller each time it reads the output.

1. Set `load_balancer_name` to the load balancer's fully qualified DNS name:

   | Root | `load_balancer_name` |
   |---|---|
   | A root that forms the load balancer as in the provider's NLB guide | `aws_lb.qumulo.dns_name`; with `count` on the load balancer, `one(aws_lb.qumulo[*].dns_name)`, which is null when it forms none, and then `api_host` needs another value |
   | The legacy CNQ modules | The value of their `qumulo_nlb_dns` output, or your module's equivalent, such as `module.cnq.qumulo_nlb_dns` from a root that calls them as `module.cnq`; null when they provision no load balancer |

2. Set `ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true` for runs that read a name.
   A name that fails takes every host of the source out of the run, and Ansible only warns about that by default.
3. With `cache: true`, expect a failure when the name resolves to fewer addresses than the cache recorded.
   Give the addresses in `portal_hosts` and `replication_address`, or run once with `--flush-cache` to take the shorter
   list.

The plugin fills only the fields the entry leaves null, with every address the name resolves to
([the rules](../reference/terraform-inventory.md#load-balancer-names)).
Give `portal_hosts` yourself when the other side cannot reach IPv6.
After the load balancer is formed again, the next run publishes its new IPs, and the portal modules and the baseline's
replication step change the cluster's addresses in place.
The resolved addresses are a snapshot, not failover
([why](../explanation/edges-and-portals.md#addresses-behind-a-load-balancer)).
The collection was tested on a three-node cluster across three zones, behind a load balancer with cross-zone load
balancing off: each zonal IP reached its own zone's node on every connection, and a relationship created with a zonal IP
as its `target_address` reached the target through it.

### Rebuild an edge that Terraform formed

Run the `qumulo.cluster.edge_rebuild` playbook in one run, with `-e qumulo_rebuild=true`, `edge_rebuild_phase=all` and
reinstall tasks that replace the edge's Terraform resource.
Terraform then forms the new edge, the output names the new UUID, and the replay takes it from the plugin.
Set `ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true` for the run.
With `output_file`, the reinstall tasks also save the output again.
The tasks file, the commands and the checks are under
[A Terraform-formed edge](edge-rebuild.md#a-terraform-formed-edge).

An edge the collection forms again without Terraform leaves Terraform's state with the old UUID
([G-TF-STALE](../reference/gaps.md)).
Replace the resource, or override `qumulo_cluster_uuid` in `host_vars`.

### Adopt a host from this plugin

A host from the plugin is adopted for the settings Terraform does not hold, with the same capture as any other.
[Adopt a cluster you already run](adopt-a-cluster.md#adopt-a-host-from-the-terraform-inventory-plugin) has the steps.

## Verify

1. List the hosts and groups the plugin produced:

   ```sh
   ansible-inventory -i inventory --graph
   ```

   Expect each cluster in `qumulo_clusters`, in one group for its kind and one for who formed it:

   ```text
   @all:
     |--@ungrouped:
     |--@qumulo_clusters:
     |  |--@qumulo_cnq:
     |  |  |--cnq01
     |  |--@qumulo_managed_by_terraform_provider:
     |  |  |--cnq01
   ```

2. Read a host's variables:

   ```sh
   ansible-inventory -i inventory --host cnq01
   ```

   Expect these variables from the plugin, next to the ones `group_vars` sets: `qumulo_api_host`,
   `qumulo_cluster_uuid`, `qumulo_cluster_kind`, `qumulo_managed_by`, `qumulo_terraform_owned_areas` and
   `qumulo_password_ref`, and, when the entry gives addresses, `qumulo_portal_hosts` as a list of `address` items and
   `qumulo_replication_address`:

   ```json
   {
       "qumulo_api_host": "10.0.1.20",
       "qumulo_cluster_kind": "cnq",
       "qumulo_cluster_uuid": "7c1e0d4a-77c1-4b8e-9a51-2f0e6c3d9e02",
       "qumulo_managed_by": "terraform_provider",
       "qumulo_password_ref": {
           "id": "/qumulo/cnq01/admin-password",
           "type": "aws_ssm_parameter"
       },
       "qumulo_portal_hosts": [
           {
               "address": "10.0.1.20"
           },
           {
               "address": "10.0.1.21"
           },
           {
               "address": "10.0.1.22"
           }
       ],
       "qumulo_replication_address": "10.0.1.20",
       "qumulo_terraform_owned_areas": [
           "network.floating_ips"
       ]
   }
   ```

   `qumulo_api_password` shows as its unrendered lookup, because `ansible-inventory` does not run lookups, so this step
   does not read the store.

3. Run the baseline as a check, which changes nothing:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l cnq01 --check --diff
   ```

   The run reaches the cluster, passes the identity guard with the UUID Terraform recorded, and, while `host_vars`
   declare nothing yet, reports `changed=0`.

An entry with a missing, unknown or malformed field fails the whole source, and the message names every bad entry and
field.
A field whose name looks like a secret, such as `password` or `api_token`, fails it the same way; the message never
quotes a value.
[Troubleshooting](../reference/troubleshooting.md) has the messages.

## Next

- [Adopt a cluster you already run](adopt-a-cluster.md) to capture the settings Terraform does not hold.
- [Run the playbooks from a pipeline](pipeline.md) to run these checks and the apply from CI.
- [Pair an edge with its hub through a portal](pair-edge-with-hub.md) once the hosts publish `portal_hosts`.
- [The Terraform inventory plugin](../reference/terraform-inventory.md) for the output's fields and the plugin's
  options.
