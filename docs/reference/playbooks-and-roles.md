<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Playbooks and roles

The collection ships eleven playbooks in `playbooks/` and seven roles in `roles/`.
This page lists what each one targets and does, the variables the playbooks read beyond the inventory's, the facts and
files the roles produce, the edge cache resize route table, the replication restore's records, files and nine
steps, and the replication failover's states and steps.
The task pages say when to run each one: [Set up an inventory](../how-to/set-up-inventory.md),
[Adopt a cluster](../how-to/adopt-a-cluster.md), [Rotate the admin password](../how-to/rotate-admin-password.md),
[License an edge](../how-to/licensing.md), [Size and resize an edge's cache](../how-to/edge-sizing.md),
[Rebuild an edge](../how-to/edge-rebuild.md),
[Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md),
[Fail over to a replica, and fail back](../how-to/fail-over-replication.md),
[Clone an edge's configuration onto a new edge](../how-to/clone-edge.md) and [Upgrade a fleet](../how-to/upgrade.md).
[Architecture](../explanation/architecture.md) says how the pieces fit together.

## Playbooks

Each playbook in `playbooks/` is a worked example, with its usage in its header comment.
Run one by name, as `ansible-playbook qumulo.cluster.<name>`, or copy it as the start of a playbook of your own.

All of them read the connection variables of [the inventory](variables.md) (`qumulo_api_host`, `qumulo_api_port`,
`qumulo_api_username`, `qumulo_api_password`, `qumulo_api_token`, `qumulo_validate_certs`, `qumulo_ca_path` and
`qumulo_cluster_uuid`) and hand them to the modules through `module_defaults` for `group/qumulo.cluster.qumulo`.
`edge_license_complete.yml`, `replication_failover.yml` and `replication_restore.yml` take them from
`cluster_baseline_connections`, which the
`cluster_baseline` role's `tasks/connections.yml` records for the edge and for the hosts its relationships target.
`upgrade.yml` connects to `qumulo_upgrade_api_host` in place of `qumulo_api_host` when it is set.

No playbook targets the `qumulo_hubs` group itself.
Hubs are in `qumulo_clusters` like every cluster.
The group names the hubs that edges pair with, and a rebuild finds an edge's hub there.

| Playbook | Hosts | What it does |
|---|---|---|
| [`admin_password_rotate.yml`](../../playbooks/admin_password_rotate.yml) | `qumulo_clusters` | Changes `admin`'s password to `qumulo_api_password_after`, unless `admin` already logs in with it. |
| [`baseline.yml`](../../playbooks/baseline.yml) | `qumulo_clusters` | Brings each cluster to its inventory with the `cluster_baseline` role. A second run changes nothing. |
| [`capture.yml`](../../playbooks/capture.yml) | `qumulo_clusters` | Reads each cluster and writes its settings in inventory form; with `-e qumulo_adopt=true`, its `host_vars` file. |
| [`clone_edge.yml`](../../playbooks/clone_edge.yml) | Two plays: `localhost` checks the variables; then the one host named by `clone_edge_target`, which is in `qumulo_clusters` | Gives a fresh cluster another cluster's configuration under its own UUID and name: writes its `host_vars` file, then runs the baseline on it. Configuration only, no data. |
| [`edge_cache_resize.yml`](../../playbooks/edge_cache_resize.yml) | `qumulo_edges`, one at a time | Brings each edge's cache to `qumulo_capacity.clamp` by the route the `edge_cache_resize` role chooses ([routes](#edge-cache-resize-routes)). A last play fails the run, naming each edge that failed. |
| [`edge_license_complete.yml`](../../playbooks/edge_license_complete.yml) | `qumulo_edges`, one at a time, in three plays | The second pass. The license play applies `qumulo_capacity.license_document`, then sets the clamp, then declares the relationships other inventory hosts have into the edge. An edge with neither an active license nor a document for its UUID fails at once, unless it declares `qumulo_capacity.license: none`; the other edges go on. The restore play, tagged `restore`, runs the [replication restore](#replication-restore). The report play, tagged `always`, fails the run, naming each edge that failed. |
| [`edge_predestroy.yml`](../../playbooks/edge_predestroy.yml) | `qumulo_edges`, one at a time | Phase `predestroy` of the `edge_rebuild` role: drains an edge before a rebuild and records that it is safe to destroy. |
| [`edge_rebuild.yml`](../../playbooks/edge_rebuild.yml) | `qumulo_edges`, one at a time | Phase `replay` of the `edge_rebuild` role: forms the reinstalled edge, replays its inventory and pairs it with its hub again. |
| [`replication_failover.yml`](../../playbooks/replication_failover.yml) | `replication_failover_hosts`, default `qumulo_clusters`; the play refuses a run that names more than one host | Runs the [replication failover](#replication-failover) action `replication_failover_action` names, for the relationships `replication_failover_relationships` names on one source host, and prints each one's outcome. |
| [`replication_restore.yml`](../../playbooks/replication_restore.yml) | `replication_restore_hosts`, default `qumulo_clusters`; the play refuses a run that names more than one host | Runs the [replication restore](#replication-restore) for one rebuilt edge. |
| [`upgrade.yml`](../../playbooks/upgrade.yml) | `qumulo_clusters`, every host at once, `strategy: linear` | Upgrades each cluster to its `qumulo_upgrade_target`, hop by hop through each quarterly release, in the order its portals and replication need, and writes a report after each hop. [The upgrade](upgrade.md) lists its variables, steps and files. |

### Behaviour under `--check`

| Playbook | `--check` |
|---|---|
| `admin_password_rotate.yml` | Reports the change and writes nothing. |
| `baseline.yml` | Reports what each area would change. With `--diff` it shows the difference. Step 2 reads and refuses as in a real run, so a refusal appears in the check. |
| `capture.yml` | Reads each cluster and writes no file. Without `--check` it changes nothing on a cluster either. |
| `clone_edge.yml` | With `--check --diff`, writes no file and changes nothing anywhere. `-e clone_edge_check_only=true` writes the file, then runs the baseline in check mode; it refuses a target whose inventory declares a portal or replication relationships, before it reads or writes anything. |
| `edge_cache_resize.yml` | The role reports the route and its inputs and writes nothing. |
| `edge_license_complete.yml` | The license play predicts the license step, the clamp and the relationships into the edge. The restore play takes no lock and reports each relationship as `predicted`, with its steps. |
| `edge_predestroy.yml` | Predicts the changes, drains nothing and writes no record. |
| `edge_rebuild.yml` | A replay onto an UNCONFIGURED node stops after it predicts the formation. With `-e edge_rebuild_phase=all` the run stops before the reinstall. |
| `replication_failover.yml` | The dry run. Reads both clusters, prints each relationship's state and every step left, with the refusals a real run would make, and changes nothing. It needs no consent. |
| `replication_restore.yml` | Takes no lock, changes nothing and reports each relationship's predicted steps. |
| `upgrade.yml` | Predicts each host's first hop and the locks, and changes nothing on the clusters. |

### Variables the playbooks read

These are the variables a playbook reads beyond the connection variables above and the area variables of the
`cluster_baseline` role.
[Inventory variables](variables.md) lists the area variables, and [The upgrade](upgrade.md) the `qumulo_upgrade_*`
variables.
The variables of `capture.yml` are in [Capture and clone](capture-and-clone.md#capture-playbook-variables) and those of
`clone_edge.yml` in [Capture and clone](capture-and-clone.md#clone-playbook-variables).

| Playbook | Variable | Meaning |
|---|---|---|
| `admin_password_rotate.yml` | `qumulo_api_password_after` | `admin`'s new password. The play fails for a host that does not set it. |
| `edge_cache_resize.yml` | `qumulo_capacity.clamp`, `edge_cache_resize_*`, `edge_rebuild_reinstall_tasks` | The target and the role's variables. See [Other roles' variables](variables.md#other-roles-variables). The automatic `rebuild` route runs `edge_rebuild` with phase `all`, which needs that role's variables, `-e qumulo_rebuild=true` and `edge_rebuild_eula_accepted` among them. |
| `edge_license_complete.yml` | `qumulo_capacity` | `license_document`, `license`, `clamp` and `free_threshold`. |
| `edge_license_complete.yml` | `qumulo_replication_restore`, `replication_restore_*` | As for `replication_restore.yml`. A run that carries `qumulo_replication_release` restores nothing, and fails for each relationship with a restore to do. |
| `edge_predestroy.yml` | `edge_rebuild_allow_data_loss`, `edge_rebuild_ad_leave`, `edge_rebuild_nexus_deregister`, `edge_rebuild_drain_timeout`, `edge_rebuild_ignore_drift`, `edge_rebuild_acknowledge`, `edge_rebuild_artifact_dir` | The `edge_rebuild` role's variables. |
| `edge_rebuild.yml` | `qumulo_rebuild` | Must be `true`, given with `-e`. The replay fails without it. |
| `edge_rebuild.yml` | `qumulo_new_cluster_uuid` | The new edge's UUID, for an edge formed by hand. |
| `edge_rebuild.yml` | `edge_rebuild_eula_accepted`, `edge_rebuild_admin_password`, `edge_rebuild_old_edge_destroyed`, `edge_rebuild_validate_factory_certs`, and the variables of `edge_predestroy.yml` | The `edge_rebuild` role's variables. `-e edge_rebuild_phase=all` runs the predestroy phase, the tasks file in `edge_rebuild_reinstall_tasks` and the replay in one run. |
| `replication_failover.yml` | `replication_failover_action`, `replication_failover_relationships` | Required. The action and the relationships it acts on. See [Variables](variables.md#replication-failover-variables). |
| `replication_failover.yml` | `replication_failover_allow_data_loss`, `replication_failover_allow_newer_writes`, `replication_failover_allow_partial_job` | The run's consents. The role sets them in its own `vars/main.yml`, so a value in `group_vars`, `host_vars`, a play's `vars` or `vars_files` is ignored. Anything a playbook sets closer to the role or during the run lifts them: `-e`, the include's `vars`, a block's `vars`, role parameters, `set_fact` and `include_vars`. |
| `replication_failover.yml` | `replication_failover_artifact_dir` | Default: `edge_rebuild_artifact_dir`. Where the restore's records are. See [Failover refusals before the first write](#failover-refusals-before-the-first-write). |
| `replication_failover.yml` | `replication_failover_wait_timeout`, `replication_failover_sync_timeout`, `replication_failover_source_timeout`, `replication_failover_hosts` | See [Replication failover timeouts](#replication-failover-timeouts) and the play's hosts. |
| `replication_restore.yml` | `qumulo_replication_restore` | Which relationships to restore, each `<target host>:<target path>`. Default: every `present` relationship out of the edge. |
| `replication_restore.yml` | `replication_restore_hosts` | The play's hosts. Default: `qumulo_clusters`. |
| `replication_restore.yml`, `edge_license_complete.yml` | `replication_restore_wait_timeout`, `replication_restore_poll_delay`, `replication_restore_reverse_timeout`, `replication_restore_reconnect_timeout`, `replication_restore_resume_window` | See [Timeouts](#timeouts). |
| `upgrade.yml` | `qumulo_upgrade_api_host` | The address to connect to in place of `qumulo_api_host`. A prepare holds one request open for up to 45 minutes, which a load balancer or NAT gateway that closes idle connections cuts. |
| `upgrade.yml` | `cluster_upgrade_artifact_dir`, `edge_rebuild_artifact_dir` | Where the commit records and reports go, and where the restore locks are. |

### Files the playbooks write

`<artifact dir>` is the `artifacts` directory beside the outermost inventory source on disk that holds the file defining
the host, never inside the source.
The [`artifact_dir` filter](../../plugins/filter/artifact_dir.yml) computes it.
It is empty for a host that comes from no inventory file, as for `-i edge01,`.
`cluster_baseline_artifact_dir`, `edge_rebuild_artifact_dir` and `cluster_upgrade_artifact_dir` override it for their
roles, and `replication_pair_artifact_dir` defaults to `edge_rebuild_artifact_dir`.
The files `capture.yml` writes are in [Capture and clone](capture-and-clone.md#capture-files) and those `clone_edge.yml`
writes in [Capture and clone](capture-and-clone.md#clone-files).

| Written by | File | Contents |
|---|---|---|
| `edge_predestroy.yml` | `<artifact dir>/<edge>/captured-<UTC>.yml` | The facts of the info modules, a record for people that nothing replays. |
| `edge_predestroy.yml` | `<artifact dir>/<edge>/predestroy-<UTC>.yml` | The old UUID, each portal and whether it was drained, deleted or forced, the drift (what the rebuild loses, tenant networks among it, and a tenant it gains or a network tenant 1 gains that the edge held in no tenant, each marked `added: true`) and the acknowledged areas, the `management_network` the preflight returned and the `management_api_host` it was found by, and `safe_to_destroy`. |
| `edge_rebuild.yml` | `<artifact dir>/<edge>/rebuild-<UTC>.yml` | The `host_vars` patch and what is left to do by hand, including the held relationships. |
| `replication_restore.yml`, `edge_license_complete.yml`, `upgrade.yml` | `<artifact dir>/<edge>/restore/` | The restore's lock, record and stamp. `upgrade.yml` takes and renews only the lock. See [The restore's files](#the-restores-files). |
| `upgrade.yml` | `<cluster_upgrade_artifact_dir>/<host>/upgrade/commit.yml` and the reports | See [The upgrade](upgrade.md). |
| the `cluster_baseline` role (`baseline.yml`, `clone_edge.yml`, `edge_rebuild.yml`) | `<cluster_baseline_artifact_dir>/<host>/secrets/s3-<user>-<access_key_id>.yml`, one for each new S3 access key | The secret, which the cluster shows once. The directory and the file are readable only by their owner. |

## Roles

`ansible-doc -t role qumulo.cluster.<role>` lists a role's entry points and variables from `meta/argument_specs.yml`.
Each role directory has a README.
[The role catalog](../../README.md#roles) in the README gives each role's one-line summary.

| Role | Entry points | Called by | What it does |
|---|---|---|---|
| [`cluster_baseline`](../../roles/cluster_baseline/) | `main`; `connections.yml`, `license.yml`, `replication.yml`, `spoke_paths.yml`, `mode_and_acl.yml`, `ownership.yml` and `tenants.yml` (`tasks_from`) | `main`: `baseline.yml`, `clone_edge.yml`, the `edge_rebuild` role. The other entry points: see below | Brings a cluster to the baseline its inventory declares, in the order of the 22 numbered steps in its README, with the tenant steps 3a, 3b and 20a, area by area, in the order the server needs. An unset area is left alone. |
| [`portal_pair`](../../roles/portal_pair/) | `main` | `cluster_baseline` (step 16), `edge_rebuild` (the drain) | Pairs a spoke with its hub through a portal, or, with `portal_pair_state: absent`, drains and deletes the pairing. |
| [`replication_pair`](../../roles/replication_pair/) | `main`; `restore.yml`, `restore_targets.yml` and `restore_claims.yml` (`tasks_from`) | `cluster_baseline` (step 21) for `main`; `replication_restore.yml` and the second pass for `restore`; see below for the others | `main` declares a replication relationship between two clusters, or removes it, refusing before any write. `restore.yml`, the `restore` entry point, is the [replication restore](#replication-restore). |
| [`replication_failover`](../../roles/replication_failover/) | `main` | `replication_failover.yml` | Fails a source host's named relationships over to their targets, copies the targets' writes back, or fails them back, one step at a time from fresh reads of both clusters ([states and steps](#replication-failover)). |
| [`edge_cache_resize`](../../roles/edge_cache_resize/) | `main` | `edge_cache_resize.yml` | Brings an edge's cache to a target size by the least disruptive route ([routes](#edge-cache-resize-routes)). |
| [`edge_rebuild`](../../roles/edge_rebuild/) | `main`, with `edge_rebuild_phase` set to `predestroy`, `replay` or `all` | `edge_predestroy.yml`, `edge_rebuild.yml`, the `edge_cache_resize` role (its `rebuild` route) | Drains an edge and records that it is safe to destroy; after the edge is reinstalled, forms it again, replays its inventory, pairs it with its hub and reports what is left to do by hand. |
| [`cluster_upgrade`](../../roles/cluster_upgrade/) | `main` | `upgrade.yml` | Runs an upgrade plan's hops across a fleet in lock-step. It runs only in a linear play without `serial`. |

Entry points other than `main`, with the files that run them:

| Role | `tasks_from` | Run by |
|---|---|---|
| `cluster_baseline` | `connections.yml` | `replication_restore.yml`, `replication_failover.yml`, `edge_license_complete.yml`, the `edge_rebuild` role |
| `cluster_baseline` | `license.yml`, `replication.yml` | `edge_license_complete.yml` |
| `cluster_baseline` | `spoke_paths.yml`, `mode_and_acl.yml` | the `edge_rebuild` role (`preconditions.yml`) |
| `cluster_baseline` | `ownership.yml` | the `edge_rebuild` role (`predestroy.yml`, and `replay.yml`'s tenant 1 pass) |
| `cluster_baseline` | `tenants.yml` | the `edge_rebuild` role (`replay.yml`'s tenant 1 pass) |
| `replication_pair` | `restore.yml` | `replication_restore.yml`, `edge_license_complete.yml` |
| `replication_pair` | `restore_targets.yml`, `restore_claims.yml` | `edge_license_complete.yml` |

`connections.yml` takes `cluster_baseline_connection_hosts`, the list of inventory hosts whose connection options it
records in `cluster_baseline_connections` ([Facts the roles set](#facts-the-roles-set)). It reads each host's
`qumulo_api_host`, which the host must define, and its other `qumulo_api_*`, `qumulo_validate_certs`,
`qumulo_ca_path` and `qumulo_cluster_uuid`.

`portal_pair` and `replication_pair` take role arguments, `portal_pair_*` and `replication_pair_*`, and no `qumulo_*`
inventory variable.
`cluster_baseline` and `edge_rebuild` build their arguments from `qumulo_portal`, `qumulo_portal_hosts` and
`qumulo_replication_relationships`, and from the target host's variables.
`replication_pair`'s `main` entry point also reads the `qumulo_replication_address` and `qumulo_capacity` of the source
host named in `replication_pair_source_host`, that host's `edge_rebuild_artifact_dir`, and that host's membership in
`qumulo_edges`.
The `replication_failover` role takes role arguments, `replication_failover_*`, and reads the source host's
`qumulo_cluster_uuid`, `qumulo_previous_cluster_uuids`, `qumulo_replication_relationships`, `qumulo_replication_address`
and `qumulo_replication_port`, and each target host's `qumulo_cluster_uuid`, `qumulo_replication_address` and
`qumulo_replication_port`.
It also looks for a restore record in `<replication_failover_artifact_dir>/<source host>/restore/`, and writes nothing
there.
The `restore` entry point reads the edge's own `qumulo_cluster_uuid`, `qumulo_previous_cluster_uuids`,
`qumulo_directories`, `qumulo_replication_relationships` and `qumulo_capacity`, its `qumulo_replication_address` and
`qumulo_replication_port`, its membership in `qumulo_edges`, `qumulo_replication_restore` and
`qumulo_replication_release`.
It reads the `qumulo_replication_address` and `qumulo_replication_port` of each target host, and the
`qumulo_cluster_uuid` of every inventory host.

### Facts the roles set

| Role | Fact | Holds |
|---|---|---|
| `cluster_baseline` | `cluster_baseline_connections` | The connection options of each host that `connections.yml` was run for, by inventory host. |
| `cluster_baseline` | `cluster_baseline_s3_access_keys` | Each new S3 access key with the file that holds its secret, never the secret. |
| `portal_pair` | `portal_pair_portals` | The portal as each side reports it, under `spoke` and `hub`, in the shape of `portal_info`. After a decommission both are `null`. |
| `replication_pair` (`main`) | `replication_pair_result` | `status` of `paired`, `failover`, `deferred`, `held` or `removed`, with `hand_off` for `deferred` and `message`, `releases`, `holders`, `key` and `restore_form` for `held`. |
| `replication_pair` (`restore`) | `replication_pair_restore_outcomes` | One entry for each relationship. See [Outcomes](#outcomes). |
| `replication_failover` | `replication_failover_results` | One entry for each relationship. See [Outcomes](#failover-outcomes). |
| `edge_cache_resize` | `edge_cache_resize_route`, `edge_cache_resize_plan`, `edge_cache_resize_rebuilding` | The route taken, the result of `edge_cache_route`, and whether the `rebuild` route has started the rebuild. |
| `cluster_upgrade` | See [The upgrade](upgrade.md#facts-the-role-sets) | The hop's state. |

### The tenancy steps of `cluster_baseline`

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

The role's `tasks/tenants.yml` runs steps 3a, 3 and 3b, and `tasks/tenants_absent.yml` runs step 20a, both included from
`tasks/main.yml`. [`tenancy_plan`](../../plugins/modules/tenancy_plan.py), which sends no request, computes what they
send.

| Step | Runs | Reads and writes |
|---|---|---|
| 2 | The ownership check, then, when the inventory declares `qumulo_tenants`, `qumulo_network`, `qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_nfs_settings` or `qumulo_factory_defaults`, one read of the cluster and `tenancy_plan` | Reads the tenants (`tenant_info`), the network document (`network_info`), its status only when the cluster has two or more tenants or the run creates one, the shares (`smb_info`), the exports and the NFS settings and overrides (`nfs_info`), and `cluster_info`'s protection type when `qumulo_tenants` is declared and `qumulo_cluster_kind` is not. Refuses before any write. When step 3a will write and step 3's document differs from the cluster's, checks that document with `network_config` in check mode. Writes nothing. |
| 3a | `tenant`: the renames, then the services turned on, then the creates | Tenant PATCH and POST |
| 3 | `network_config`, with the document the plan built from the tenants' `networks` and `qumulo_network` | One network PUT, each network with its tenant |
| 3b | `tenant`: the services turned off | Tenant PATCH |
| 18, 19 | `smb_settings`, `smb_share`, `nfs_settings` and `nfs_export`, from the plan: removals first, then the cluster's settings, then each tenant's override, then the shares and exports in each tenant their entry names | Share and export writes, and each override's PUT, PATCH or DELETE |
| 20a | `smb_settings` and `nfs_settings` with `state: inherit` for each tenant declared absent, then `tenant` with `state: absent` | Override DELETEs, then the tenant DELETE |
| 21 | `replication_pair`, whose `replication_target` check reads the target's tenants, and with two or more tenants its network document | Reads only, for the replication address ([Replication](variables.md#replication)) |

The order of 3a, 3 and 3b has its reason in
[The order of areas](../explanation/architecture.md#the-order-of-areas). In a check run, `tenant` and `network_config` take the
planned networks and tenants from the plan, so each write is judged as it will be immediately after it.
A tenant that step 3a renames is still under its old name in a check run, so its shares, exports and settings show as
creates under the new name.

`tenancy_plan` is not in the `qumulo.cluster.qumulo` action group and takes no connection options. A run that passes
returns an empty `refusals` and the items of `step_3a`, `step_3b` and `step_20a`, step 3's `network` document with
`network_changes`, `bindings` and `validate`, step 18's `smb` and step 19's `nfs` lists, the `tenants` that remain, and
the continuity `verdicts`. A run that refuses fails with `msg`, which holds each refusal's message on a line, and
`refusals`, each message with the names it gives, such as tenants, networks, entries and the play's `api_host`, and
nothing else of the plan. At verbosity 3 or more (`-vvv`) the failure also carries `plan`, the keys the module had
computed when it stopped: none after the renames, the cloud refusal, or the entry, claim and NFSv4.1 rules, the share,
export and tenant lists after the network rules, and every key, the network document and its address ranges included, after the continuity rule.
[Troubleshooting](troubleshooting.md#tenants) quotes its messages.

## Edge cache resize routes

The `edge_cache_resize` role reads the edge's file system size, its stored clamp, its license ceiling and its product
type, and the [`edge_cache_route`](../../plugins/modules/edge_cache_route.py) module chooses one route.
The role fails when the product type is unknown, or when the cluster does not answer for one of these reads.

The route is the first that applies, in the order of the table.
The module compares sizes with a margin, `edge_cache_resize_margin_bytes`, which defaults to 10% of the target.

| Input | Meaning |
|---|---|
| T | The target, `qumulo_capacity.clamp`. |
| R | The stored clamp. `unset` when none is stored. |
| L | The license ceiling: the license's usable capacity cap. On an unprotected edge without a license it is 1 TB. `none` when there is no ceiling. |
| C | The file system's size, which stands in for the provisioned capacity because no API reports it ([G-CLAMP-TOL](gaps.md)). |
| floor | `edge_cache_resize_floor_bytes`, default 1000000000000: what a rebuilt edge provisions at formation, if its platform is larger. |

| Route | When | What the role does |
|---|---|---|
| `within` | C is within the margin of T. | Applies `qumulo_capacity.free_threshold` if declared. |
| `below_floor` | C is more than the margin above T, and T is below the floor. | Fails unless `qumulo_capacity.free_threshold` is declared; applies it when it is. |
| `rebuild` | C is more than the margin above T, and T is not below the floor. | Fails with the plan to rebuild the edge. With `edge_cache_resize_allow_rebuild: true` and `edge_rebuild_reinstall_tasks`, runs the `edge_rebuild` role with phase `all` instead, which needs that role's variables, `-e qumulo_rebuild=true` and `edge_rebuild_eula_accepted` among them. |
| `blocked_license` | C is more than the margin below T, and L is below T. | Fails. The message names T, the cluster's UUID, `qumulo_capacity.license_document` and the `edge_license_complete` playbook. |
| `platform_limited` | C is more than the margin below T, and R already allows T. | Writes no clamp and reports why. Applies `free_threshold` if declared. |
| `grow` | Otherwise: C is more than the margin below T, R does not allow T yet, and L allows T. | Raises the clamp to T with `capacity_clamp`, which re-forms quorum once, then reads the file system size again. If C is still more than the margin below T, the route becomes `platform_limited` and the role reports it. Applies `free_threshold` if declared. |

The role prints `<route>: <reason> (T=…, R=…, L=…, C=…, margin=…)` before it takes the route.
With `edge_cache_resize_plan_only`, which defaults to `ansible_check_mode`, it prints that and goes no further.

A clamp write re-forms quorum, which pauses the edge, and its portal on its hub, for less than 30 seconds.
`edge_cache_resize.yml` runs one edge at a time.
A route that fails ends only that edge, and the last play fails the run naming each edge that failed.
A failed automatic rebuild ends the run.

The message of each failing route is in [Troubleshooting](troubleshooting.md#the-edges-cache).
The registers explain the routes: [G-CLAMP-BOUNCE, G-CLAMP-SHRINK, G-EDGE-FLOOR, G-LICENSE and G-UUID](gaps.md).
[Licensing and capacity](../explanation/licensing-and-capacity.md) says why provisioned capacity never shrinks, what
the floor is, and
[why a failed automatic rebuild ends the run](../explanation/licensing-and-capacity.md#a-failed-automatic-rebuild-ends-the-run).

## Replication restore

The restore brings back the replicas of a rebuilt edge.
For each `present` relationship out of the edge whose old record on the target, from one of the edge's
`qumulo_previous_cluster_uuids`, still holds the replica, it syncs the replica back into the edge's source directory,
turns the relationship around and reconnects it, so that it runs from the edge's current UUID with its declared
settings.
The `replication_pair` role's `restore` entry point runs it, from `playbooks/replication_restore.yml` and from the
restore play of `playbooks/edge_license_complete.yml`.
The messages of the restore name the records and steps below.
[Restore a rebuilt edge's data from its replica](../how-to/restore-replica.md) has the task and
[Replication](../explanation/replication.md) says why a restore keeps X and reverses R.

### Records

| Name | Is |
|---|---|
| X | The old record on the target that holds the replica: a live record at the target path from one of the edge's `qumulo_previous_cluster_uuids`. |
| R | The restore relationship, from the target's replica directory to the edge's declared source path. Before step 6 its source record is on the target and its target record on the edge. After step 6 it is the other way round, in the declared relationship's orientation. |
| The restore record | A file on the controller under `<artifact dir>/<edge>/restore/`. Step 7 writes it after X's delete, step 8 marks it before the reconnect, and step 9 removes it. |
| The restore stamp | A file in the same directory that step 9 writes. It retires the newest rebuild report's claim on the relationship. |

### The restore's files

All of them are under `<artifact dir>/<edge>/restore/`, with mode 0700 for the directory and 0600 for the files.
A record and a stamp are named by the SHA-256 of the relationship's key: its source path, the target's UUID in lower
case and its target path, each path without a trailing slash.
The target host's name is not hashed.
Another controller, or an inventory that adds a trailing slash or renames the target host, finds the same files.

| File | Contents |
|---|---|
| `<digest>.yml` | The restore record, `format: 1`: `key` (`source_path`, `target_host`, `target_cluster_uuid`, `target_path`), `r_id`, `x_id`, `x_source_cluster_uuid`, `x_source_path`, `x_snapshot_ids`, `recovery_point`, `recovery_point_snapshot_id`, `written_at`, and `reconnected: true` once step 8 has marked it. A record of another format fails the edge's run, naming the file. |
| `done-<digest>.yml` | The restore stamp: `format`, `key`, `r_id`, `report`, `written_at`. |
| `lock` | The controller's lock on the edge's restore. A JSON file, `format` 1, with the holder, a random token for the run, the controller's host name, boot id and PID namespace, the `ansible-playbook` process's id, its start in clock ticks and its start time, and the lease's expiry. |
| `lock.guard` | The file a takeover or a removal holds an exclusive `flock` on while it reads the lock again. The file is kept. A run waits at most 60 seconds for it, then refuses. |
| `lock.tmp-<token>` | A lock being written. The take removes any older than its lease. |

The lock keeps a second restore, or a restore and an upgrade, of the same host from running at once on the same
controller.
A restore takes it with holder `restore`, and [an upgrade](upgrade.md#restore-locks) with holder `upgrade`.
It belongs to the controller: two controllers can both restore one edge.
A real run takes it before its first step and removes it however the run ends; check mode takes none, and neither does
a run whose artifact directory is empty.
The lease is the longest the restore of the picked relationships can take: the number of picked relationships times
(`replication_pair_wait_timeout` + `replication_restore_reverse_timeout` + `replication_restore_reconnect_timeout` + 600)
seconds, plus `replication_restore_wait_timeout`.
The run renews the lock to the full lease before phase 2.
Another run's lock is taken over, with a warning, when its process is gone, if it was taken on this controller (the same
host name, boot id and PID namespace), or once its lease has run out, if it was taken on another controller or on one without `/proc`.
A lock whose process is alive on this controller is never taken over.
Any other lock refuses, naming its holder, its process, its controller and the end of its lease.
A run that is killed leaves its lock.
[Troubleshooting](troubleshooting.md#restore-locks) lists the lock's messages.

### Steps

Phase 1 takes each picked relationship in turn up to step 4, before the next relationship's step 1.
Phase 2 waits for the full syncs together, in rounds spaced `replication_restore_poll_delay` seconds apart, and finishes
each relationship as its sync lands.

| Step | Runs on | What it does |
|---|---|---|
| 1 | Edge, controller | Checks that the restore can start (the [refusals](#refusals-before-step-1)). Creates the source path and its missing declared ancestors on the edge, and the restore directory on the controller. |
| 2 | Target | Creates R on the target: source path the target's replica directory, target path the edge's source path, target address and port the edge's `qumulo_replication_address` and `qumulo_replication_port`, enabled, no blackout windows. If the target cannot reach the edge there, nothing is written on either cluster and the restore stops with a hand-off. |
| 3 | Edge | Authorizes R on the edge, which waits up to `replication_pair_wait_timeout` seconds (default 600) until R reads ESTABLISHED on the edge. |
| 4 | Edge | Waits for R's first recovery point, which is the full sync of the replica, up to `replication_restore_wait_timeout` seconds. |
| 5 | Edge | Makes the edge's directory writable, then waits up to `replication_restore_reverse_timeout` seconds until R reads DISCONNECTED on the edge. |
| 6 | Edge | Reverses R, sending the address and port at which the edge reaches the target: the target's `qumulo_replication_address` and `qumulo_replication_port`. If the edge cannot reach the target there, the reverse fails, neither record changes, and the restore stops. |
| 7 | Target, controller | Requires R DISCONNECTED on both clusters. Deletes X on the target, reads R's record on the target, and writes the restore record. |
| 8 | Target | Marks the restore record, reconnects R on the target, and waits up to `replication_restore_reconnect_timeout` seconds until R reads ESTABLISHED on both clusters. |
| 9 | Edge | Applies the declared relationship's settings to R (`replication_enabled`, `map_local_ids_to_nfs_ids`, `source_root_read_only`, `blackout_windows` and `blackout_window_timezone`, as the entry sets them), writes the restore stamp, and removes the restore record. |

The restore sets no blackout window on R until step 9.

Before every step the role calls
[`replication_restore_step`](../../plugins/modules/replication_restore_step.py) with fresh reads of both clusters and runs
only the step it returns, so a later run resumes where the records show.
The module decides in this order, and the first that applies wins.

| Reads show | Next |
|---|---|
| The target did not answer | `failed` while the relationship has a restore to do, otherwise `not applicable` with a warning. |
| A restore record whose R is ENDED, or missing on a cluster | The record is removed, and the decision goes on without it. |
| A restore record, unmarked, with R reconnected | The record is marked, and the decision goes on. |
| A marked record, R ESTABLISHED on both clusters in the declared orientation | Step 9. |
| A marked record, R in the declared orientation, at least one side RECONNECTING, REESTABLISHING or ESTABLISHED, and the other one of those or DISCONNECTED | Step 8's wait. |
| Any other marked record | A refusal that names the record. A marked record never sends a reconnect. |
| The declared relationship ESTABLISHED, and no record | Outcome `done`. |
| R's pair exists before the reverse, X ESTABLISHED, PENDING on the edge | Step 3. |
| The same pair, ESTABLISHED on the edge with no recovery point | Step 4. |
| The same pair, ESTABLISHED with a recovery point | Step 5. |
| The same pair, DISCONNECTING | Step 5's wait. |
| The same pair, DISCONNECTED | Step 6. |
| R reversed, X exists, both sides DISCONNECTED | Step 7. |
| R reversed, X deleted, both sides DISCONNECTED, with an unmarked restore record whose guards hold | Step 8. |
| R reversed, no X, no record, both sides DISCONNECTED | No step: the stranded pair (see [Outcomes](#outcomes)). |
| X exists and R has no pair | Step 1 when the source path or a declared ancestor is missing on the edge, otherwise step 2, once the refusals pass. |
| Anything else | A stop or a refusal, with its way on. |

An unmarked record resumes step 8 only within `replication_restore_resume_window` seconds of its write time, and only
while R's recovery point and recovery-point snapshot are those the record holds.
Otherwise the restore stops and says to remove the record.

### Refusals before step 1

The restore refuses a relationship, before step 1 and before any write, with a message that names its way on, when:

- X is not both ESTABLISHED and read-only, an ENDED record lies at the target path beside X, or X has no recovery point;
- the edge's source directory holds a file;
- the edge's or the target's `qumulo_replication_address` is missing or not an IP literal;
- `edge_rebuild_artifact_dir` is empty, or the restore directory cannot be written;
- the edge is in `qumulo_edges`, expects a license (`qumulo_capacity.license` other than `none`) and holds no active
  license;
- the edge declares a `qumulo_capacity.clamp` that differs from the stored clamp, or the clamp plan fails;
- the target path, or the edge's source path, is, lies under or holds a portal spoke root;
- the edge has a live relationship record whose source path is the same as or nested with this one's, or whose target
  path on the edge overlaps it;
- leftover records with R's paths exist;
- the check of the create on the target or of the authorize on the edge refuses.

Step 1's refusals are per relationship, with outcome `refused`, on both entry points.
`replication_restore.yml` (entry point `playbook`) also refuses the whole run, before any relationship, for a release in
the run, an edge with no `qumulo_previous_cluster_uuids`, a name in `qumulo_replication_restore` that is not
`<target host>:<target path>` or matches no `present` relationship, and nested source paths among the relationships to
restore whose X exists.
On the second pass (entry point `second_pass`), nested relationships are each `refused`, and a name that matches nothing
fails the run in the report play.

### Outcomes

`replication_pair_restore_outcomes` holds one entry for each relationship: `name`, `outcome`, `fails`, `message`,
`summary` and `note`, in check mode `sequence`, and for `restored` also `snapshots_left`.

| Outcome | Fails the run | Meaning |
|---|---|---|
| `restored` | No | Step 9 finished. `snapshots_left` lists the replication snapshots X left on the target, each with `host` and `id` ([G-REPL-SNAPSHOT](gaps.md)). |
| `done` | No | The declared relationship is ESTABLISHED and has no record. Nothing to restore. |
| `not applicable` | No | The relationship has nothing to restore. `note` says why. |
| `predicted` | No | Check mode: `sequence` holds the steps the run would take. |
| `refused` | Yes | A condition refuses it before any write. |
| `stopped` | Yes | The restore stopped at a step and a later run resumes it. |
| `not restored` | Yes | The relationship has a restore to do and none can run, as for a held relationship with no replica left or a stranded pair on the second pass. `message` gives the way on. |
| `refused by step 21` | Yes | Step 21 of the baseline (the `replication_pair` role) refuses the declared relationship in its check pass. |
| `failed` | Yes | A failure inside this relationship. The other relationships go on. |

Any failure that is not inside one relationship, such as a lock another run holds, fails the task.
The fact keeps the outcomes decided before it.

### Timeouts

| Variable | Default | Bounds |
|---|---|---|
| `replication_pair_wait_timeout` | 600 | Step 3's authorize. |
| `replication_restore_wait_timeout` | 86400 | Step 4, the full sync, from the end of the relationship's step 3 in this run, or from the end of phase 1 for one resumed at step 4. A full sync of a large replica takes hours. |
| `replication_restore_poll_delay` | 30 | The seconds between phase 2's reads of the edge's target list. |
| `replication_restore_reverse_timeout` | 300 | Step 5's poll after the make-writable. |
| `replication_restore_reconnect_timeout` | 1800 | Step 8's two polls together. A timeout leaves the restore record marked, so the next run only polls. |
| `replication_restore_resume_window` | 86400 | How long after its write time an unmarked restore record still resumes step 8. |

[Troubleshooting](troubleshooting.md#rebuilds) lists the messages the steps print.
[G-REPL-IDMAP, G-REPL-ONESHOT and G-REPL-PEER](gaps.md) record the server limits the restore works around.

## Replication failover

The `replication_failover` role fails a declared relationship over to its target, copies the target's writes back to the
source, and fails it back.
`playbooks/replication_failover.yml` runs it for the relationships of one source host, named in
`replication_failover_relationships`, each `<target host>:<target path>`.
The source and the target are the declared ones, whatever direction the records run in.
Before each step the role reads both clusters with `replication_info` and
[`replication_failover_plan`](../../plugins/modules/replication_failover_plan.py) names the relationship's state and
the steps its action has left.
[Fail over to a replica, and fail back](../how-to/fail-over-replication.md) has the task, and
[Replication](../explanation/replication.md#failing-over) says why the role works as it does.

### Failover states

The plan names the state from the records.
`A` is the source's record, `B` the target's.
Each direction's state is read from the record a verb moves first, so the other may lag.
Two states, marked with an asterisk, read the same as another; the plan reads such records as the asterisked state
unless the run's last verb says otherwise.

| State | The records | `failover` starts from it | `resync` starts from it | `failback` starts from it |
|---|---|---|---|---|
| `replicating` | Declared direction, `B` ESTABLISHED; for `resync` and `failback`, `A` ESTABLISHED or absent. | Yes | No | Only to apply settings, when `A` differs from a declared setting |
| `failing over` | Declared direction, `B` DISCONNECTING. | Yes | Yes | Yes |
| `failed over` * | Declared direction, `B` DISCONNECTED, and the run's last verb is not the failback's reverse on the source. | Yes, nothing left | Yes | Yes |
| `reconnecting` | Declared direction, `B` RECONNECTING or REESTABLISHING, or `B` ESTABLISHED while `A` is present and neither ESTABLISHED, AWAITING_AUTHORIZATION nor ENDED. | No | No | Yes |
| `reversed` * | Reversed direction, `A` absent or DISCONNECTED, and the run's last verb is not the failback's make-writable on the source. | No | Yes | Yes |
| `resyncing` | Reversed direction, `A` in any other state. | No | Yes | Yes |
| `resynced` | Reversed direction, `A` and `B` ESTABLISHED. | No | Yes | Yes |
| `failing back, source disconnecting` | Reversed direction, `A` DISCONNECTING. | No | No | Yes |
| `failing back, source writable` | Reversed direction, `A` DISCONNECTED after the failback's make-writable on the source. | No | No | Yes |
| `failing back, reversed` | Declared direction, `B` DISCONNECTED after the failback's reverse on the source. | No | No | Yes |

A record AWAITING_AUTHORIZATION or ENDED is refused by every action, and so is a relationship with no record on `B`.
A resync or a failback also refuses an `A` that is AWAITING_AUTHORIZATION or ENDED beside a `B` that reads ESTABLISHED
in the declared direction, since a wait for `A` would never end. The message names both records and the way on
([Troubleshooting](troubleshooting.md#failing-over)).
A state an action does not start from is refused, naming the action that does.

### Failover steps

A verb step runs the polls beside it after the verb, each read every 5 seconds within `replication_failover_wait_timeout`.
Each step is the plan's answer for the records at that moment, so a step already done is dropped.

| Step | Runs on | What it does | Polls until |
|---|---|---|---|
| `make_writable_target` | Target | `replication_reverse` make-writable, with the run's consent. The directory goes back to the recovery point. | Target's record DISCONNECTED |
| `wait_target_writable` | Target | Waits for a make-writable that is under way. | Target's record DISCONNECTED |
| `wait_source_disconnected` | Source | Waits, only while the source's record is not DISCONNECTED. | Source's record DISCONNECTED |
| `reverse_to_source` | Target | `replication_reverse` reverse, toward the source's `qumulo_replication_address` and port. | none |
| `apply_mapping` | Target | `replication_relationship` turns `map_local_ids_to_nfs_ids` on in the target's source record. Only when the declared entry maps ids and the record does not. | none |
| `reconnect_source` | Source | `replication_reverse` reconnect. It compares the directory with the recovery point first, unless the source host is in `replication_failover_allow_newer_writes`, where the plan's line says it does not compare. | Both records ESTABLISHED |
| `wait_resynced` | Source | Waits for both records. | Both records ESTABLISHED |
| `await_sync` | Target | A failback's wait for the target's recovery point to move twice past the one the wait reads first, within `replication_failover_sync_timeout`. The waiting relationships of one target host are polled together. Dropped once the run's wait has finished. | The recovery point has moved twice |
| `make_writable_source` | Source | `replication_reverse` make-writable. The directory goes back to the recovery point. | Source's record DISCONNECTED |
| `wait_source_writable` | Source | Waits for a make-writable that is under way. | Source's record DISCONNECTED |
| `reverse_to_target` | Source | `replication_reverse` reverse, toward the target's `qumulo_replication_address` and port. | none |
| `reconnect_target` | Target | `replication_reverse` reconnect, compared as `reconnect_source`, unless the target host is in `replication_failover_allow_newer_writes`. | Both records ESTABLISHED |
| `wait_replicating` | Target | Waits for both records. | Both records ESTABLISHED |
| `apply_settings` | Source | `replication_relationship` applies the declared settings where the source's record differs. | none |

What each action has left, from each state it starts from:

| Action | From | Steps |
|---|---|---|
| `failover` | `replicating` | `make_writable_target` |
| `failover` | `failing over` | `wait_target_writable` |
| `resync` | `failing over` | `wait_target_writable`, then the next row's steps |
| `resync` | `failed over` | `wait_source_disconnected`, `reverse_to_source`, `apply_mapping`, `reconnect_source` |
| `resync` | `reversed` | `apply_mapping`, `reconnect_source` |
| `resync` | `resyncing` | `apply_mapping`, `wait_resynced` |
| `resync` | `resynced` | `apply_mapping` |
| `failback` | `failing over`, `failed over`, `reversed`, `resyncing`, `resynced` | The resync's steps from that state, then `await_sync`, `make_writable_source`, `reverse_to_target`, `reconnect_target`, `apply_settings` |
| `failback` | `failing back, source disconnecting` | `wait_source_writable`, `reverse_to_target`, `reconnect_target`, `apply_settings` |
| `failback` | `failing back, source writable` | `reverse_to_target`, `reconnect_target`, `apply_settings` |
| `failback` | `failing back, reversed` | `reconnect_target`, `apply_settings` |
| `failback` | `reconnecting` | `wait_replicating`, `apply_settings` |
| `failback` | `replicating` | `apply_settings`, only when the source's record differs from a declared setting |

A failback runs each relationship up to `await_sync`, waits for the waiting relationships, those on one target host
together and the target hosts one after another, then runs each to the end.
The role runs at most twelve rounds of read, plan and step for a relationship in one pass, and fails naming the next
step when they run out; run it again to resume.

### Failover refusals before the first write

The role reads and plans every named relationship before the first write of any, and a refusal of one stops the run.
[Troubleshooting](troubleshooting.md#failing-over) lists the messages.
The role refuses:

- an empty `replication_failover_relationships`, a name that matches no `present` entry of the source host's
  relationships, a target host outside the inventory, and a host without a `qumulo_cluster_uuid`;
- a relationship with no record on the target, records in both directions or two in one, and a live target record from
  one of the source host's `qumulo_previous_cluster_uuids`;
- a relationship AWAITING_AUTHORIZATION or ENDED, and, for a resync or failback, a source record AWAITING_AUTHORIZATION
  or ENDED beside a target record that reads ESTABLISHED, and a state the action does not start from;
- a `resync` or `failback` when the source did not answer (read once in the run, then every relationship is refused) or
  holds no record of the relationship;
- a `resync` or `failback` of a relationship whose restore record is in
  `<replication_failover_artifact_dir>/<source host>/restore/`, which `playbooks/replication_restore.yml` finishes; a
  `failover` notes the record and goes on;
- a make-writable on a target with no recovery point, and a failover for a job running on a source that answers, unless
  `replication_failover_allow_partial_job` lifts it;
- a reverse toward an address that is not an IP literal, and a reverse into a tenant that has `replication_enabled` off
  ([The replication address](troubleshooting.md#the-replication-address));
- a real run with a step left and no `replication_failover_allow_data_loss`.

### Failover outcomes

`replication_failover_results` holds one entry for each relationship: `name`, `id`, `state_before`, `state_after`,
`steps`, the names of the steps run, `plan`, the lines of the steps the first plan listed, and `notes`.
A check run reports `steps` empty and `state_after` equal to `state_before`.
The playbook prints each as `<name>: <state_before> to <state_after>, steps <steps>`, then its notes.

### Replication failover timeouts

| Variable | Default | What it bounds |
|---|---|---|
| `replication_failover_wait_timeout` | 1800 | Each wait for a record to read a state: a verb's polls and the wait steps. A timeout sends nothing, and the next run resumes. |
| `replication_failover_sync_timeout` | 86400 | `await_sync`, from when the waits begin, for all the target hosts together: the relationships on one target host are polled together, and the hosts one after another. |
| `replication_failover_source_timeout` | 30 | A failover's read of the source, as `retry_timeout` and `request_timeout`. In any action, a source that has not answered is not read again in the run. |

[QUIRK-125](quirks.md) records the server behaviour the role works around, and
[G-REPL-ONESHOT and G-REPL-SNAPSHOT](gaps.md) the limits of the verbs it sends.

## See also

- [Inventory variables](variables.md): the area variables the baseline reads, and the `edge_rebuild_*` and
  `edge_cache_resize_*` variables.
- [The upgrade](upgrade.md): `upgrade.yml`'s variables, steps and files.
- [Capture and clone](capture-and-clone.md): what `capture.yml` reads and what `clone_edge.yml` copies.
- [Architecture](../explanation/architecture.md): how modules, roles and playbooks fit together.
- [Rebuilds](../explanation/rebuilds.md) and [Replication](../explanation/replication.md): why a rebuild and a restore
  work as they do.
- [Troubleshooting](troubleshooting.md): the messages, by symptom.
