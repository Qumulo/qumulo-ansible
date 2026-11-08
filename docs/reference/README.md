<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Reference

Where to look up the collection's modules, roles, plugins, variables, return values, registers and messages. The
[documentation index](../README.md) lists the tutorials, how-to guides and explanations.

## Pages

| Page | What it holds |
|---|---|
| [Inventory variables](variables.md) | Which group reads what, the connection variables, the variable for each area and the step that applies it, the list and precedence rules, ownership, and the variables passed for one run. `roles/*/meta/argument_specs.yml` is the schema. |
| [The Terraform inventory plugin](terraform-inventory.md) | The fields of the Terraform output, the hosts and groups the plugin produces, and its source options. |
| [What capture and clone read and write](capture-and-clone.md) | The variables a capture writes and leaves out, the file's shape, and what a clone copies and replaces. |
| [The upgrade: variables, steps and files](upgrade.md) | The upgrade's variables, plan, steps, records and supported sources. |
| [Playbooks and roles](playbooks-and-roles.md) | Each playbook in `playbooks/` with its hosts and effect, the roles, the replication restore's records and steps, and the replication failover's states and steps. |
| [Tested Qumulo Core versions](tested-versions.md) | Minimum requirements, what each column of the README's tested-versions table means, and what the test lanes cannot show. |
| [Troubleshooting](troubleshooting.md) | Messages by symptom, with what each means and what to do. |
| [Gap register](gaps.md) | Operations the REST API does not let the collection make idempotent, or perform at all. |
| [Quirk register](quirks.md) | Server behaviours that differ from what the API documents, and the workaround for each. |

## Modules, roles and plugins

The [README's catalog](../../README.md#modules-and-roles) lists every module and role, what each manages, and the
gaps that apply to it.

The doc site renders each module's, filter's and the inventory plugin's `DOCUMENTATION`, `EXAMPLES` and `RETURN`, and
each role's `meta/argument_specs.yml`, as one reference page each.

`ansible-doc` gives the same content from the installed collection:

```sh
ansible-doc -l qumulo.cluster                          # every module
ansible-doc qumulo.cluster.smb_share                   # a module: options, examples, return values
ansible-doc -t role qumulo.cluster.cluster_baseline    # a role: its variables
ansible-doc -t inventory qumulo.cluster.terraform      # the inventory plugin
ansible-doc -t filter -l qumulo.cluster                # the filters
```

A module's source, for example [smb_share](../../plugins/modules/smb_share.py), has the three blocks at the top. A
role's variables are in its `meta/argument_specs.yml`, for example
[the baseline's](../../roles/cluster_baseline/meta/argument_specs.yml).

## Check mode and diff mode

| Mode | Support |
|---|---|
| Check mode | Every module. |
| Diff mode | Every module that writes to a cluster. Not these, which only read, plan or compute: the fifteen `*_info` modules, `cluster_guard`, `edge_rebuild_preflight`, `tenancy_plan`, `upgrade_plan`, `upgrade_report`, `replication_pair_plan`, `replication_failover_plan`, `edge_cache_route`, `clone_host_vars`, `host_vars_text` and `replication_restore_step`. Not `replication_restore_lock`, which writes a file on the controller. |

Each module's `attributes` block states its `check_mode` and `diff_mode` support. A module with diff mode has an
example that previews its change with `check_mode: true` and `diff: true`.
[State management](../explanation/state-management.md#check-and-diff) explains what a check run reports.

## Return values

Every module returns `changed`. The modules also follow these rules:

| Key | Returned | Holds |
|---|---|---|
| A key for the object, such as `smb_share` | Always, by a module that manages one object | The object after the task, in the shape its options take; an empty dict after `state: absent`. In check mode, the object the task would leave. Options the cluster does not return, or returns only as a secret, are left out. |
| `diff` | When the task runs with diff mode on | `before` and `after`. Most modules give them in the shape of the object's key, without those options; `after_header` names any of them that changed. Some give another shape, listed below. |
| `notes` | When the module noticed something it did not fail on | A list of texts, such as `write reconciled after a lost connection`, or, in check mode, that an object an earlier task is expected to create does not exist yet. |
| `maybe_applied` | On a failure after a write whose connection was lost, or that a gateway answered with 502 or 504, when reading back could not confirm the outcome. Never from `network_config`, which reads back and fails with `expected` and `observed` instead. Not from `replication_reverse` after a lost answer to its comparison's snapshot, which fails with `changed: true` alone, since no verb was sent | `true`. The result also has `changed: true`. |
| `expected`, `observed` | On a failure when the read-back after a write differs from what was written | The values written, and the values the cluster returned, for the options that differ, except options the cluster returns only as a secret. The result also has `changed: true`. |
| `cluster_unanswered` | On a failure because a cluster or Nexus did not answer before the retry deadline | `true`. A caller can tell a cluster or Nexus that did not answer from one that refused. The identity guard keeps it when the guard's read of the node state did not answer. The key is not `unreachable`, which Ansible takes for the host's connection failing. |
| `status`, `error_class` | On a failure the cluster's API answered with an error | The HTTP status, and the error class the cluster named, or `unknown`. A failure of the identity guard keeps the node-state read's `status` and `error_class` when that status is 5xx, and only then. |
| `status` | On a failure Nexus answered with an error, in `nexus_cluster` and `nexus_registration` | The HTTP status. |
| `live_cluster_uuid`, `expected_cluster_uuid` | On a failure of the identity guard, when the cluster at `api_host` is not the one `expected_cluster_uuid` names | The UUID the node reported, empty for an unconfigured node, and the one expected. No credentials were sent. |

The key is the module's name except for `file_acl` (`acl`), `directory_quota` (`quota`), `portal_hub` and
`portal_spoke` (`portal`), `replication_relationship`, `replication_target` and `replication_reverse` (`relationship`),
`unprotected_edge_create` (`unprotected_edge`), `encryption_key_rotation` (`rotation`), `portal_eviction_settings`
(`eviction_settings`), `nexus_registration` (`registration`), `access_token` (`access_tokens`), and `auth_cache_clear`
and `upgrade` (`plan`).

Diffs in another shape:

| Module | `before` and `after` hold |
|---|---|
| `access_token` | `user` and `access_tokens`, each token's `id`, `enabled` and `expiration_time`; `(a new token)` as the id of a token that check mode would make |
| `auth_cache_clear`, `dns_cache_clear`, `encryption_key_rotation`, `snapshot`, `upgrade` | `before` is empty; `after` is what the module does, or in check mode would do |
| `capacity_clamp` | `capacity_clamp`: the stored clamp before, and the requested clamp after when the task writes one |
| `license` | `state`, `usable_capacity_cap` and `expiration_date` of the license |
| `nexus_cluster` | `status` only |
| `nexus_registration` | `registration_status` only |
| `replication_relationship`, `replication_target` | `relationship`, in the shape of the `relationship` key, and `records`, the records the task deletes or in check mode would delete; `after`'s `records` is always empty |
| `replication_reverse` | `relationship`, in the shape of the `relationship` key |
| `s3_access_key` | `user` and `access_key_ids`, the ids of the user's keys |
| `ssl_certificate` | Each probed `host:port`, mapped to the SHA-256 digest it serves |
| `unprotected_edge_create` | `before` is `state: UNCONFIGURED`; `after` is the cluster the task forms, with `cluster_name` and `state: ACTIVE`, in check mode too |

An `*_info` module returns each area in the shape that area's module takes, so what it reads can go into the
inventory as it is.

A failure that carries `maybe_applied: true` means the collection did not resend the write.
[Troubleshooting](troubleshooting.md#a-change-that-may-or-may-not-have-been-applied) lists the messages and what to do
about each, and [State management](../explanation/state-management.md#maybe-applied) explains why the collection reads
back instead of retrying.

Each module's `RETURN` block lists the keys it returns, with their types and samples.

## Known limits

Some operations the Qumulo REST API does not let the collection make idempotent, or does not let it perform at all.
The [gap register](gaps.md) lists each one: the operation, why it cannot be idempotent, what the collection does about
it, and the change to Qumulo Core that would close it. Its ids, such as G-UUID and G-LICENSE, appear in module
documentation and in messages. A module whose idempotency is partial cites the gaps that explain why.

## Server behaviour the collection works around

The [quirk register](quirks.md) lists server behaviours that differ from what the API documents or a client would
expect, and the workaround for each. Each quirk names the code that owns its workaround and the tests that pin the
behaviour.

Both registers are generated from [gaps.yml](gaps.yml) and [quirks.yml](quirks.yml).
