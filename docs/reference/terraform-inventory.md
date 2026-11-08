<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# The Terraform inventory plugin

The inventory plugin [`qumulo.cluster.terraform`](../../plugins/inventory/terraform.py) adds one inventory host for each
entry of a Terraform output named `qumulo_ansible_inventory`.
This page lists the plugin's source file and options, the output's fields and formats, the values each Terraform
resource gives, and the hosts, variables and groups the plugin produces.
[Use a Terraform root's output as the inventory](../how-to/terraform.md) has the steps, and
[Terraform ownership](../explanation/terraform-ownership.md) says why the output is the only
place the two meet.

`ansible-doc -t inventory qumulo.cluster.terraform` lists the plugin's options and examples from the installed
collection.

## Summary

| | |
|---|---|
| Plugin name | `qumulo.cluster.terraform` |
| Source file name | Ends in `qumulo_terraform.yml` or `qumulo_terraform.yaml`. |
| Output read | `qumulo_ansible_inventory`, a map from the Ansible host name to one entry per cluster. |
| Source of the output | `terraform output -json qumulo_ansible_inventory` run in `project_path`, or a saved copy of its result in `output_file`. |
| Hosts produced | One for each entry, in `qumulo_clusters` and in two child groups. |
| Connection variables set | None. The hosts have no `ansible_connection`. |
| Needs | Terraform on the controller with `project_path`; nothing with `output_file`. |

## Source file options

A source file is a YAML file that names the plugin and one source for the output.

```yaml
# inventory/prod.qumulo_terraform.yml
plugin: qumulo.cluster.terraform
project_path: ../terraform/prod
workspace: prod
```

```yaml
# inventory/ci.qumulo_terraform.yml
plugin: qumulo.cluster.terraform
output_file: ../terraform-output/qumulo_ansible_inventory.json
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `plugin` | string, required | | `qumulo.cluster.terraform`. |
| `project_path` | string | | The Terraform root to run `terraform output -json qumulo_ansible_inventory` in. |
| `output_file` | string | | A file holding what `terraform output -json qumulo_ansible_inventory` prints. |
| `binary_path` | string | `terraform` | The executable that runs with `project_path`. |
| `workspace` | string | | The Terraform workspace to read, through `TF_WORKSPACE` for the one command. Only with `project_path`. |
| `timeout` | integer | `120` | Seconds to wait for the command before the source fails. Only with `project_path`. |
| `cache` | boolean | `false` | Keep the checked hosts between runs, with a persistent cache plugin ([Cache](#cache)). |
| `cache_plugin` | string | `memory` | The cache plugin. `memory` lives for one process and keeps nothing between runs; a persistent plugin such as `ansible.builtin.jsonfile` does. |
| `cache_connection`, `cache_timeout`, `cache_prefix` | | | The other inventory cache options of Ansible's `inventory_cache` fragment. `cache_timeout` defaults to 3600 seconds. |
| `compose`, `groups`, `keyed_groups`, `strict`, `use_extra_vars`, `leading_separator` | | | The options of Ansible's `constructed` fragment ([Constructed groups and variables](#constructed-groups-and-variables)). |

Rules for the source options:

- Set exactly one of `project_path` and `output_file`. Setting both, or neither, fails the source.
- `workspace` with `output_file` fails the source. `timeout` and `binary_path` with `output_file` have no effect.
- A relative `project_path` or `output_file` is relative to the directory of the source file.
  A leading `~` and environment variables such as `$HOME` are expanded first.
  A `binary_path` without a directory separator is looked up on `PATH` as written, without expansion.
  One with a separator is expanded and made relative to the source file's directory like the other two.
- `project_path` must be a directory.
- The plugin runs `<binary_path> -chdir=<project_path> output -no-color -json qumulo_ansible_inventory` with standard
  input from `/dev/null`.
  Every environment variable whose name starts with `TF_LOG` is removed from the command's environment, so a failure
  message, which quotes terraform's standard error, never carries a trace log.
- With `workspace`, `TF_WORKSPACE` is set to it for that command, and the root's selected workspace is not changed.
  Without it, a `TF_WORKSPACE` already in the environment applies, and otherwise the root's selected workspace.
- The command's failure fails the source, whether terraform is missing or cannot be run, does not finish within
  `timeout`, exits non-zero (the message quotes its standard error), or prints text that is not JSON.
- `output_file` fails the source when it cannot be read, is not JSON, or holds what `terraform output -json` prints
  without an output name.
  The plugin recognizes that listing by every top-level value being a map with `value` and `type` keys.
  The listing carries every output of the root, sensitive ones in plain text.
- Given a directory with `-i`, Ansible's `ansible.builtin.yaml` inventory plugin reads every `.json` file in it as an
  inventory of its own, so a saved output kept in that directory is parsed twice.
- A source file that fails is skipped with a warning, and the run continues without its hosts.
  `ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true` makes the run fail instead.

## Cache

With `cache: true` and a persistent cache plugin such as `ansible.builtin.jsonfile`, the plugin keeps the hosts it has read.
The default `cache_plugin`, `memory`, keeps nothing between runs, so with `cache: true` alone the plugin reads the output on
every run and records no addresses for the subset check.

- What is cached is the checked hosts, never the raw output.
  A cached read checks them again.
- The cache key covers the source file's path, `project_path`, `output_file`, and the workspace, from `workspace` or from
  `TF_WORKSPACE`.
  A cached source is read again when any of them changes.
  A workspace switched with `terraform workspace select` is read once `cache_timeout` passes or the cache is refreshed.
- A second entry, under another key with the same parts, records the addresses each `load_balancer_name` last
  resolved to ([Load balancer names](#load-balancer-names)).
- `--flush-cache` reads the source again and writes the cache, and ignores the recorded addresses.

Without `cache`, the plugin reads the output on every run.

## The output

The output is a map from the Ansible host name to an entry.
A host name is a string with no whitespace and no `/`.
`terraform output -json qumulo_ansible_inventory` prints it as JSON:

```json
{
  "cnq01": {
    "api_host": "10.0.1.20",
    "cluster_uuid": "7c1e0d4a-77c1-4b8e-9a51-2f0e6c3d9e02",
    "cluster_kind": "cnq",
    "managed_by": "terraform_provider",
    "owned_areas": ["network.floating_ips"],
    "password_ref": {"type": "aws_ssm_parameter", "id": "/qumulo/cnq01/admin-password"},
    "portal_hosts": ["10.0.1.20", "10.0.1.21", "10.0.1.22"],
    "replication_address": "10.0.1.20"
  }
}
```

The output carries no secret, so it need not be marked `sensitive`.

### Fields of an entry

| Field | Required | Type | Meaning | Variable it sets |
|---|---|---|---|---|
| `api_host` | yes | string | The host name or IP address the REST API answers on. | `qumulo_api_host` |
| `cluster_uuid` | yes | string | The cluster's UUID, which every module checks before it sends credentials. | `qumulo_cluster_uuid` |
| `cluster_kind` | yes | `edge`, `cnq` or `baremetal` | An unprotected edge, a Cloud Native Qumulo cluster, or another on-premises cluster. | `qumulo_cluster_kind` |
| `managed_by` | yes | `none`, `terraform_provider` or `legacy_provisioner` | What formed the cluster and may re-apply settings: nothing, the Terraform provider, or the older modules. | `qumulo_managed_by` |
| `owned_areas` | yes | list of area names | The areas Terraform owns and the collection leaves alone. The baseline takes it as the whole list, an empty one included. | `qumulo_terraform_owned_areas` |
| `password_ref` | yes | map of `type` and `id`, or null | Where the admin password lives. Never the password. | `qumulo_password_ref`, unless null |
| `portal_hosts` | no | list of IP addresses, or null | The addresses the other side of a portal reaches the cluster at. | `qumulo_portal_hosts` |
| `replication_address` | no | IP address, or null | The address a replication source reaches the cluster at. | `qumulo_replication_address` |
| `load_balancer_name` | no | fully qualified DNS name, or null | A load balancer's name, which the plugin resolves into the two fields above where they are null. | Neither itself; see [Load balancer names](#load-balancer-names) |

A required field must be present.
Only `password_ref` may be null.
An optional field may be absent or null, and the two mean the same.

### Strictness

The plugin checks every entry before it adds any host, and one bad entry fails the whole source.
The message names each bad entry and each bad field, and quotes no value from the output.
The exceptions are a `load_balancer_name` that has already passed the fully qualified DNS check, and the addresses the
cache recorded for it that it no longer resolves to.

The plugin fails the source for:

- an output that is not a map;
- an entry that is not a map;
- a missing required field;
- a field outside the nine above, and in `password_ref` outside `type` and `id`;
- a field whose name matches `pass`, `secret`, `token`, `credential`, `private`, `key` or `value`, in any case, as a
  substring, and is not one of the nine fields.
  The message calls it a secret, and the plugin never reads its value, puts it in a variable or the cache, or shows it.
  Names such as `password`, `admin_password`, `api_token` and `join_key` are refused this way;
- two entries with the same `cluster_uuid`, compared without regard to case, since that would make one cluster two
  hosts;
- an entry name with whitespace or `/` in it;
- a value in the formats below that the plugin refuses.

### Formats

| Field | Accepted | Refused |
|---|---|---|
| `api_host` | An IP address, or a DNS name of at most 253 characters (letters, digits, `-`, `_`, optionally a trailing dot; no label starts or ends with `-`; one label is enough). An IPv6 address is given bare, as `fd00::5`; the modules put it in brackets. | A URL (anything with `://`, such as the `endpoint` the provider's bare-metal resources export), a value with `@` (a user name), a leading `[`, a `%` (a zone id such as `fe80::1%eth0`), a run of digits and dots that is no IP address, such as `10.20.0.256`, and any other value with a scheme, port or path. |
| `cluster_uuid` | A UUID, in any case. The plugin sets the variable as written. | Any other string. |
| `cluster_kind` | `edge`, `cnq`, `baremetal`. | Any other value. |
| `managed_by` | `none`, `terraform_provider`, `legacy_provisioner`. | Any other value. |
| `owned_areas` | A list of strings, each made of parts joined by `.`, where a part is lower-case letters and `_`, such as `network.floating_ips` or `dns`. An empty list. | A value that is not a list of such strings. For `cluster_kind: cnq` with `managed_by: terraform_provider`, a list without `network.floating_ips`. |
| `password_ref` | Null, or a map of `type` and `id` ([Password references](#password-references)). | A map with another key, a `type` outside the table below, or an `id` that is not a string with at least one non-space character. |
| `portal_hosts` | Null, or a non-empty list of IP addresses. | A host name, a port, brackets, a zone id, a URL, an unspecified, loopback or multicast address, an empty list, and an address given twice. |
| `replication_address` | Null, or one IP address. | The same as for one entry of `portal_hosts`. |
| `load_balancer_name` | Null, or a DNS name with at least two labels and not all digits. | An IP address, a name with a scheme, port or path, and a single-label name. |

The plugin checks the form of an area name but not whether the collection manages the area.
[Inventory variables](variables.md) lists the areas and the variable each one guards.

For `portal_hosts` and `replication_address`, two addresses are the same when their canonical forms are.
The canonical form writes an IPv6 address compressed and in lower case, and an IPv4-mapped IPv6 address, such as `::ffff:10.0.1.20`,
as the IPv4 address it maps.

### Password references

`password_ref.type` is one of the following, and `id` identifies the secret in that store.
The plugin requires `id` to be a string with at least one non-space character and checks nothing else about it.

| Type | `id` | Example |
|---|---|---|
| `env` | The name of an environment variable. | `QUMULO_PASSWORD_EDGE01` |
| `aws_ssm_parameter` | An SSM parameter's name. | `/qumulo/edge01/admin-password` |
| `aws_secrets_manager` | A Secrets Manager secret's name or ARN. | `qumulo/edge01/admin` |
| `azure_key_vault` | A Key Vault secret identifier. | `https://<vault>.vault.azure.net/secrets/<name>` |
| `gcp_secret_manager` | A Secret Manager secret. | `projects/<project>/secrets/<name>` |
| `hashicorp_vault` | A Vault path. | `secret/data/qumulo/edge01` |

Nothing in the collection reads `qumulo_password_ref`.
Your `group_vars` resolve it into `qumulo_api_password` with a lookup.
The lookup for each type is in
[Point the plugin at the output](../how-to/terraform.md#point-the-plugin-at-the-output).

## Values by Terraform resource

The provider's cluster resources give the kind and the address as follows.
This section records what the provider's resources export; the plugin does not check which resource an entry came from.

| Resource | `cluster_kind` | `api_host` |
|---|---|---|
| `qumulo_filesystem_edge_baremetal` | `edge` | `node_ips[0]`. Not `endpoint`, which is an `https://` URL. |
| `qumulo_filesystem_aws` | `cnq` | `floating_ips[0]`, which needs `floating_ip_count` of at least 1. On a multi-AZ cluster, which has no floating IPs, the load balancer's `dns_name`. |
| `qumulo_filesystem_azure`, `qumulo_filesystem_gcp` | `cnq` | `endpoint_ips[0]`, which is the first floating IP when there are any. `floating_ips` is a set on these resources. |
| `qumulo_filesystem_baremetal` | `baremetal` | `node_ips[0]`. Not `endpoint`. |

`cluster_uuid` is the resource's `cluster_uuid`.
On GCP that attribute holds the deployment name until Nexus pre-registration is enabled, and the plugin refuses a value
that is not a UUID.

A list that is empty must be published as null, because the plugin refuses an empty list and indexing an empty list
fails the root's own output.
The expressions below check the length first.
`x` is the resource's address.

| Resource | `portal_hosts` | `replication_address` |
|---|---|---|
| `qumulo_filesystem_aws` | `length(x.floating_ips) > 0 ? x.floating_ips : null` | `length(x.floating_ips) > 0 ? x.floating_ips[0] : null` |
| `qumulo_filesystem_azure`, `qumulo_filesystem_gcp` | `length(x.endpoint_ips) > 0 ? x.endpoint_ips : null`; the floating IPs when there are any | `length(x.endpoint_ips) > 0 ? x.endpoint_ips[0] : null` |
| `qumulo_filesystem_edge_baremetal`, `qumulo_filesystem_baremetal` | `x.node_ips`, or the cluster's floating IPs when it has them | One of the same |

A multi-AZ cluster in AWS has no floating IPs.
Its root publishes the load balancer's IP addresses in these two fields, or the load balancer's name in
`load_balancer_name`.

The plugin publishes only the addresses the output gives or a `load_balancer_name` resolves to, and reads none from the cluster.
[Edges, hubs and portals](../explanation/edges-and-portals.md#addresses-are-declared-never-derived) says why these are the addresses to publish.

## Hosts, variables and groups

### Variables

For each entry the plugin adds a host named by the entry's key, with these variables.

| Variable | Value | Set |
|---|---|---|
| `qumulo_api_host` | `api_host`, as written | Always. |
| `qumulo_cluster_uuid` | `cluster_uuid`, as written | Always. |
| `qumulo_cluster_kind` | `cluster_kind` | Always. The upgrade playbook reads it to choose the image kind. |
| `qumulo_managed_by` | `managed_by` | Always. |
| `qumulo_terraform_owned_areas` | `owned_areas` | Always. The plugin passes the list as given, so a provider-managed `cnq` entry's list names the floating IPs. |
| `qumulo_password_ref` | `password_ref`, a map of `type` and `id` | Unless `password_ref` is null. |
| `qumulo_portal_hosts` | A list of `{address: <canonical address>}` items, on the default portal port | When `portal_hosts` is given, or `load_balancer_name` fills it. |
| `qumulo_replication_address` | `replication_address`, canonical | When `replication_address` is given, or `load_balancer_name` fills it. |

A value that `host_vars` declares for the same variable outranks the plugin's.
When the output gives neither the field nor a `load_balancer_name`, the plugin does not set `qumulo_portal_hosts` or
`qumulo_replication_address`.
The `cluster_baseline` role reads `qumulo_portal_hosts` in its portal step and `qumulo_replication_address` in its
replication step; [Inventory variables](variables.md) lists both.

The hosts have no `ansible_connection`.
The modules run on the controller.
[Point the plugin at the output](../how-to/terraform.md#point-the-plugin-at-the-output) sets `ansible_connection: local` in
`group_vars`.

### Groups

Each host joins `qumulo_clusters` through two child groups.
A group exists only when at least one host is in it.

| Group | Members |
|---|---|
| `qumulo_edges` | Hosts with `cluster_kind: edge`. |
| `qumulo_cnq` | Hosts with `cluster_kind: cnq`. |
| `qumulo_baremetal` | Hosts with `cluster_kind: baremetal`. |
| `qumulo_managed_by_none` | Hosts with `managed_by: none`. |
| `qumulo_managed_by_terraform_provider` | Hosts with `managed_by: terraform_provider`. |
| `qumulo_managed_by_legacy_provisioner` | Hosts with `managed_by: legacy_provisioner`. |
| `qumulo_clusters` | The groups above that have a member, as children. |

The plugin creates no other groups apart from those `groups` and `keyed_groups` create.
The example entry above gives, as `ansible-inventory --list --yaml` prints it:

```yaml
all:
  children:
    qumulo_clusters:
      children:
        qumulo_cnq:
          hosts:
            cnq01:
              qumulo_api_host: 10.0.1.20
              qumulo_cluster_kind: cnq
              qumulo_cluster_uuid: 7c1e0d4a-77c1-4b8e-9a51-2f0e6c3d9e02
              qumulo_managed_by: terraform_provider
              qumulo_password_ref:
                id: /qumulo/cnq01/admin-password
                type: aws_ssm_parameter
              qumulo_portal_hosts:
              - address: 10.0.1.20
              - address: 10.0.1.21
              - address: 10.0.1.22
              qumulo_replication_address: 10.0.1.20
              qumulo_terraform_owned_areas:
              - network.floating_ips
        qumulo_managed_by_terraform_provider:
          hosts:
            cnq01: {}
```

### Constructed groups and variables

`compose`, `groups` and `keyed_groups` work as they do for any constructed inventory.
The plugin evaluates them for each host after it has set the variables above, so their expressions can read those
variables and the host name.
`strict` makes an expression that fails an error instead of a skipped entry.

## Load balancer names

An entry may give `load_balancer_name` instead of `portal_hosts` and `replication_address`.
The plugin resolves the name on the controller, with the system resolver, each time it reads the output.
That includes a read from the cache, which keeps the name and not the addresses.

| Rule | Detail |
|---|---|
| When it resolves | The entry has `load_balancer_name` and leaves out `portal_hosts` or `replication_address`. An entry that gives both fields is not resolved. |
| What it fills | `portal_hosts` gets every address the name resolves to. `replication_address` gets the first of them. Each field is filled only where the entry leaves it out; an address the entry gives wins. |
| Address form | IPv4 and IPv6 addresses, canonical, without repeats, IPv4 first, then IPv6, each in ascending numeric order. |
| The name itself | Never published as an address. Portals and replication take IP addresses only. |
| Failure | The source fails, naming the entry, when the name resolves to no address on the controller, or to any loopback, unspecified, multicast or zoned address. |
| Failure with the cache | With `cache` on and a persistent cache plugin, and addresses recorded for the name on an earlier run, the source fails when the name now resolves to a proper subset of them, naming the addresses missing. |
| Ways out of that failure | Give the addresses in `portal_hosts` and `replication_address`, or run once with `--flush-cache` to take the shorter list. |
| Recorded addresses | After a run that resolved a name with `cache` on, the cache holds the name's addresses from that run. A name that resolves to other addresses, not to a subset of the recorded ones, is accepted and recorded. The record expires `cache_timeout` seconds after it was last written. A run after that records whatever the name resolves to, a shorter list included. `cache_timeout: 0` keeps the record until `--flush-cache`. |

A load balancer with cross-zone load balancing off leaves a zone with no healthy target out of its name's answers.
[Edges, hubs and portals](../explanation/edges-and-portals.md#addresses-behind-a-load-balancer) explains what that does to the addresses a run publishes.

## Failure summary

Every failure below fails the source, and Ansible by default only warns and goes on without the source's hosts.

| Condition | Where it is checked |
|---|---|
| Both or neither of `project_path` and `output_file`; `workspace` with `output_file` | Source options. |
| `project_path` is not a directory; terraform cannot be run, times out or exits non-zero; its output is not JSON | Running terraform. |
| `output_file` cannot be read, is not JSON, or lists every output of the root | Reading the file. |
| An output that is not a map; a field or value outside [The output](#the-output); two entries with one `cluster_uuid` | Checking the entries, before any host is added. |
| A `load_balancer_name` that resolves to nothing, to a loopback, unspecified, multicast or zoned address, or to fewer addresses than the cache recorded | Resolving the names, after the entries pass. |

## See also

- [Use a Terraform root's output as the inventory](../how-to/terraform.md): publish the output, point the plugin at it,
  resolve the password, and the multi-AZ and bare-metal cases.
- [Terraform ownership](../explanation/terraform-ownership.md): why the output carries
  `owned_areas` and `managed_by`.
- [Inventory variables](variables.md): `qumulo_terraform_owned_areas`, `qumulo_portal_hosts`, `qumulo_replication_address` and
  the areas the roles guard.
- [Edges, hubs and portals](../explanation/edges-and-portals.md): why the published addresses are the floating IPs or a load
  balancer's IPs.
- [Gap register](gaps.md): G-TF-NLB-ADDRESS (multi-AZ addresses) and G-TF-STALE (state after a rebuild).
