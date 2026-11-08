<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Rebuild an unprotected edge

Replace an unprotected edge with a newly formed one that keeps its name and its inventory entry.
Rebuild when the cache has to shrink, when the edge needs a new platform, or when a larger cache needs a larger disk.
A rebuild gives the edge a new UUID and replays the inventory onto it; [why it works this way](../explanation/rebuilds.md).
To add an edge configured like one you run, while that one keeps running, [clone it](clone-edge.md) instead.
To grow the cache within the disk, [resize it](edge-sizing.md).

The `qumulo.cluster.edge_rebuild` role does the work in two phases, run by two playbooks:

| Phase | Playbook | Runs on | What it does |
|---|---|---|---|
| predestroy | `qumulo.cluster.edge_predestroy` | the old edge | Checks the inventory, drains the edge into its hub, leaves the domain and Nexus, writes the record that says the edge is safe to destroy. |
| replay | `qumulo.cluster.edge_rebuild` | the new edge | Forms the edge when it is UNCONFIGURED, replays the inventory, pairs the edge with its hub, writes the report. |

Between them you reinstall the edge.
`-e edge_rebuild_phase=all` runs both phases and your reinstall in one run ([Rebuild in one run](#rebuild-in-one-run)).

## Before you start

Every playbook below runs for one edge at a time with `-l edge01`.

- The edge's `host_vars` declare everything the edge holds, in the variables the [cluster_baseline](../../roles/cluster_baseline/) role reads.
  The rebuild replays the inventory, never the edge it destroys.
- The edge's `host_vars` also carry what a formation needs:
  - `qumulo_cluster_name`, the name the edge forms with, which is also its computer account in the domain.
  - `qumulo_cluster_uuid`, the old edge's UUID, which the hub's record and the predestroy records are keyed on.
    On the Terraform path the inventory plugin supplies it.
  - `qumulo_factory_defaults` with both `nfs_root_export` and `smb_files_share`, each `present` or `absent` (G-FACTORY).
  - The `admin` password the new edge forms with, as `edge_rebuild_admin_password` or else `qumulo_api_password`.
    The replay connects as `admin` with it, because a new edge has no other user and no token.
  - `qumulo_portal` with `hub`, an inventory host in the `qumulo_hubs` group that has its `qumulo_cluster_uuid`, and `qumulo_portal_hosts` on both the edge and the hub.
  - `qumulo_ssl` with `ca_path_after`, and `edge_rebuild_validate_factory_certs: false`, because the new edge serves a factory certificate nothing can verify until the replay installs `qumulo_ssl` (G-FACTORY-TLS).
  - `qumulo_capacity` with `clamp` for the cache size.
    The replay is then the first of [two license passes](#finish-the-license).
  - A new `qumulo_monitoring.nexus_join_key`, or `qumulo_nexus_api_token` to mint one, because a join key registers one cluster (G-NEXUS-KEY).
  - `edge_rebuild_eula_accepted: true`.
  - With `qumulo_ad`, `join_user` and `join_password`, so that the leave deletes the computer account and the replay can join under the same name.
- Stop the clients that write to local directories, the ones outside the portal's spoke roots.
  The drain moves only the spoke roots' data to the hub, and a file written after the predestroy check is lost.
  Copy off what you need before you run the phase, or list `local_data` in `edge_rebuild_acknowledge` to give up the files and the snapshots.
- A Terraform-formed edge also needs what [A Terraform-formed edge](#a-terraform-formed-edge) lists.
- The edge's `qumulo_capacity.clamp`, if declared, is not below min(1 TB, `edge_cache_resize_floor_bytes`), within its rounding tolerance.
  A rebuild in one run refuses a smaller clamp before it drains anything ([the refusal](../reference/troubleshooting.md#rebuilds)).
  In two runs the predestroy phase doesn't check it, and the replay refuses before it forms the edge, so check the clamp before you run the predestroy phase.
- No entry of `qumulo_snapshot_policies` declares a `lock_key`, or you pass `-e qumulo_allow_deferred_policy_lock_keys=true` to the predestroy and the replay, or to the rebuild in one run.
  The rebuild refuses a declared `lock_key` without it, before the predestroy changes anything, because the new edge's key store is empty ([the message](manage-lock-keys.md#when-it-fails)).
- A host that comes from no inventory file, such as one given as a host list with `-i`, must set `edge_rebuild_artifact_dir`.

The variable names and defaults are in the role's [argument_specs.yml](../../roles/edge_rebuild/meta/argument_specs.yml) and [Variables](../reference/variables.md).

### Check the inventory against the edge

Run the predestroy phase with `--check` first.
It drains nothing and writes no record, and it fails when the edge holds what the inventory does not cover:

```sh
ansible-playbook qumulo.cluster.edge_predestroy -i inventory -l edge01 --check
```

- A key that a variable of the baseline does not take, such as a misspelt one.
  Every phase checks the inventory against the baseline's argument specification first, so the run stops with `Validation of arguments failed:` and the path to the key before it contacts the edge.
  That check reads every variable of the baseline, so a vaulted value among them needs its vault password in this run too.
  Correct the key or remove it.
- Drift: tenants, tenant networks, shares, exports, quotas, snapshot policies, users, groups or portal roots that the inventory does not declare, or declares absent.
  Declare them and apply the inventory, or set `edge_rebuild_ignore_drift: true` to lose them.
  For the cases that need a particular key:
  - Tenant 1 renamed by hand: give its `qumulo_tenants` entry `previous_name: Default`.
  - A `tenant_networks` entry, a network the old edge binds to a tenant the replay builds again: list it under that tenant's `networks` in `qumulo_tenants`.
    Once any network is listed, list every network the edge holds: under its tenant, or in `qumulo_network.frontend_networks` for a network no tenant holds or on the backend bond, and give every present tenant `networks`, `[]` for none.
  - A network in `qumulo_network.frontend_networks` with no tenant that the old edge holds in no tenant: name its tenant with `tenant`, or list it under its tenant in `qumulo_tenants`.
  - A tenant 1 the old edge lacks, which the rebuilt edge would gain: declare `Default` with `state: absent`, claim its `Files` share and `/` export for removal (set `qumulo_factory_defaults` `smb_files_share` and `nfs_root_export` to `absent`, or declare `Files` and `/` under the absent `Default` entry), and list each network under a tenant that stays.
    A capture of the edge declares `Default` absent or writes its rename, or its `tenant 1` note in `left_out` says why it could not.

  [Troubleshooting](../reference/troubleshooting.md#rebuilds) has each message and its fix, and [The preflight](../explanation/rebuilds.md#the-preflight) says why the edge is checked this way.
- An inventory that the tenancy step's inventory rules refuse, those that need no read of the cluster, fails before the edge is read, and `edge_rebuild_ignore_drift` does not waive it.
  Change what the message names.
  A network in `qumulo_network.frontend_networks` with no tenant, which the newly formed edge does not hold, is refused once the replay has more than one tenant: name its tenant with `tenant`, or list it under that tenant's `networks`.
  [A network sent with no tenant](../explanation/rebuilds.md#a-network-sent-with-no-tenant) says which networks the new edge holds, and [Troubleshooting](../reference/troubleshooting.md#rebuilds) has the message.
- On host-managed networking, an inventory that creates a tenant must list the edge's HOST network for a tenant that stays: under that tenant's entry in `qumulo_tenants`, or in `qumulo_network.frontend_networks`.
  The preflight refuses an inventory that lists none, whatever `edge_rebuild_ignore_drift` says.
  - To keep the HOST network in tenant 1 through the rebuild, give the tenant that holds it `previous_name: Default`, and set `qumulo_factory_defaults` `smb_files_share` and `nfs_root_export` for it, since claims under the `Default` entry no longer reach the factory share and export once tenant 1 is renamed.
    Set each to `present` for each factory item the inventory declares for the renamed tenant under the factory's name (a share named `Files` in any letter case, or an export at `/`), and to `absent` for each it does not.
  - When `qumulo_terraform_owned_areas` lists `network`, the baseline refuses `qumulo_network` and a `networks` key under a tenant, so take `network` out of the list for the rebuild and list the HOST network.
    Afterwards put `network` back and remove the listing in the same change; putting `network` back first makes the next run's ownership check refuse the listing.

  [Rebuilds](../explanation/rebuilds.md#the-preflight) says why, and [Troubleshooting](../reference/troubleshooting.md#rebuilds) has the message.
- On host-managed networking, set `qumulo_management_network` when the replay creates a tenant and either the tenant 1 pass writes two or more networks, which the preflight refuses without it when it cannot tell which one `qumulo_api_host` is on, or the newly formed edge does not answer its network status (G-NET-APPLY), which the preflight does not check.
  Set it to the id of the network `qumulo_api_host` is on, which must be one the tenant 1 pass writes (tenant 1's own, or every listed network when tenant 1 holds none), and never to `[]`.
  Where the play reaches the edge on a network the pass does not write, set `qumulo_api_host` to an address on one that it does write and `qumulo_management_network` to that network's id for the rebuild, and set both back after it.
  This does not work when tenant 1's REST switch is off (`rest_api_enabled`, or `web_ui_enabled` behind 443) or the controller has no route to tenant 1's networks; such a rebuild is not supported yet.
  The preflight records its answer in the predestroy record as `management_network`, with the `qumulo_api_host` it was found by as `management_api_host`.
  The replay takes the recorded network only while `qumulo_api_host` is unchanged, so after a reinstall that changes it, set `qumulo_management_network` in the inventory.
  [Why](../explanation/rebuilds.md#the-preflight), and [Troubleshooting](../reference/troubleshooting.md#rebuilds) has the message.
- On an edge that forms host-managed, a network under a `qumulo_tenants` entry's `networks` takes no `vlan_id` and is not `type: STATIC`.
  Remove `vlan_id`, or give the network `type: HOST`, as the message says.
  [Troubleshooting](../reference/troubleshooting.md#rebuilds) has the message.
- State the replay cannot create again, by area:
  - `network`: a STATIC network or VLAN while `qumulo_network` is not declared.
  - `kmip`: a KMIP key store.
  - `replication`: replication relationships the inventory does not declare.
  - `s3_keys` and `access_tokens`: S3 keys and access tokens, whose secrets the replay cannot recover.
  - `local_data`: files, links and other objects outside the spoke roots, and snapshots.

  List each area you will recreate by hand in `edge_rebuild_acknowledge`.
  The report then lists it.
- Replication relationships that the inventory declares are kept in some states and not in others; [Restore the replicas](#restore-the-replicas) says which.

Repeat the check until it passes.
A check run never changes the edge.

## Rebuild the edge

1. Drain and retire the old edge.

   ```sh
   ansible-playbook qumulo.cluster.edge_predestroy -i inventory -l edge01
   ```

   The phase:
   - makes each portal the edge holds, declared or not, read-only on its hub until the edge's outstanding writes arrive, then deletes both portal records;
   - leaves the domain (`edge_rebuild_ad_leave`, on when `qumulo_ad` is set);
   - removes the Nexus registration, and with `qumulo_nexus_api_token` retires the UUID in Nexus;
   - writes `artifacts/edge01/predestroy-<UTC>.yml`, which says whether the edge is safe to destroy, and `captured-<UTC>.yml` beside it, which records what the edge held.

   A portal whose hub no inventory host in `qumulo_hubs` names by its `qumulo_cluster_uuid` stops the phase before anything changes.
   Go on only when the phase has finished and the predestroy record says `safe_to_destroy: true`.
   The record is written as each portal finishes, with `safe_to_destroy: false` until the phase ends.
   If the phase fails, see [If the drain fails](#if-the-drain-fails).

2. Reinstall the edge, out of band: Qumulo's installer, a reimage, or Terraform.
   Leave it UNCONFIGURED, or let the installer form it.
   For an edge that Terraform forms, follow [A Terraform-formed edge](#a-terraform-formed-edge).

3. Replay the inventory onto the new edge.

   ```sh
   ansible-playbook qumulo.cluster.edge_rebuild -i inventory -l edge01 -e qumulo_rebuild=true
   ```

   `-e qumulo_rebuild=true` declares the rebuild.
   Without it, or against a cluster that is not newly formed and empty, the replay fails closed.
   An edge the installer formed needs its new UUID too: add `-e qumulo_new_cluster_uuid=<UUID>`.

   The replay deletes the hub's record of the old UUID, forms the edge when it is UNCONFIGURED, switches the play to the new UUID, and runs the `cluster_baseline` role, which pairs the edge with its hub again.
   It writes `artifacts/edge01/rebuild-<UTC>.yml` as soon as the edge has its new UUID, and again at the end.

   If a snapshot policy declares a `lock_key`, add `-e qumulo_allow_deferred_policy_lock_keys=true`; the replay then applies each such policy without its key and lists it in the report.
   Register the keys with `security_key`, then run `qumulo.cluster.baseline` on the edge, which sets each key ([A rebuilt or cloned cluster](manage-lock-keys.md#a-rebuilt-or-cloned-cluster)).

   With `edge_rebuild_validate_factory_certs: false` in the edge's `host_vars`, the replay connects without verifying the factory certificate until its certificate step, then verifies the edge with `qumulo_ssl.ca_path_after`.
   The hub stays verified throughout.
   Don't pass `-e qumulo_validate_certs=false` instead: an extra variable applies to the hub too and outranks the certificate step, so every password the run sends would go over unverified TLS ([why](../explanation/authentication.md#why-not--e-qumulo_validate_certsfalse)).

4. Apply `host_vars_patch` from the report to `host_vars/edge01.yml`.
   The patch holds the new UUID and `qumulo_previous_cluster_uuids` with the old UUID added.
   It is the whole list, because applying the patch replaces it.
   When the replay read the new UUID from the inventory, the patch holds the list alone.
   When the replay took the management network from the predestroy record (`qumulo_api_host` unchanged) and the inventory lacked `qumulo_management_network`, the patch holds it too, so that later baseline runs have it.
   From then on `playbooks/baseline.yml` manages the edge.

   If `edge_rebuild_admin_password` differs from the `qumulo_api_password` the inventory connects with, the report says so.
   An inventory that connects as `admin` must set its password to it.

5. [Finish the license](#finish-the-license), which runs the second pass, then [work through the report's other hand-offs](#work-through-the-report).

### If the drain fails

The failure message says whether the spoke delete already ran.

- Before the spoke delete, nothing is deleted.
  Fix the cause, such as an unreachable hub, and run the phase again.
- After the spoke delete, the edge's writes are on the hub, the record says the portal was drained, and only the hub's record is left, DELETING.
  Run the phase again to wait for that record to go.
  A rerun counts the portal as drained from the record.
- `-e edge_rebuild_allow_data_loss=true` forces the deletions instead, at once for a DELETING record.
  It loses the edge's writes that the hub has not received, if any.
- If the hub had already made the portal read-only when the drain failed, the edge's clients can't write through it until a rerun finishes or `playbooks/baseline.yml` sets its type back.

### If the old edge was lost

When the old edge is gone before you could drain it, the replay needs a way to know that:

- With `qumulo_ad`, set `edge_rebuild_old_edge_destroyed: true`, or the replay refuses to join under the old name.
  The join resets the computer account, which a live old edge still uses.
- A DISCONNECTED hub record of the old edge, with no predestroy record that shows the portal drained, needs `edge_rebuild_allow_data_loss`.
  A PENDING record, or a DELETING one whose predestroy record shows the portal drained, goes without it.
  A record the hub still reaches means the old edge is up, and the replay stops.

Right after a lost edge is reinstalled, the hub may still show its portal connected.
Wait until the hub reports it DISCONNECTED, then run the replay again.

### Finish a replay that failed after the edge formed

`playbooks/baseline.yml` finishes it.
Until the baseline's identity steps have run, the new edge has only `admin`, with `edge_rebuild_admin_password` (or `qumulo_api_password` when that is unset), and no token.

1. In the edge's `host_vars`, set `qumulo_api_username: admin`, that password as `qumulo_api_password`, and no `qumulo_api_token`.
   If the replay failed before its certificate step, so that the edge still serves its factory certificate, also set `qumulo_validate_certs: false`.
2. Apply `host_vars_patch` from the report, so that `host_vars` holds the new UUID.
3. Run the baseline:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
   ```

4. Take the connection variables out again.

Never pass them with `-e`, which would apply to the hub's connection too.
Don't run the replay again for that edge: it refuses a cluster whose UUID `host_vars` already holds, which it would take for the old edge, and it names the report that recorded it.

On host-managed networking, a replay that stopped before its tenant 1 pass wrote the networks leaves an edge with no frontend network, so the baseline refuses the second tenant: "The cluster's networking is host-managed and it has no frontend network yet ...".
Do what the pass does, as [On networking the host manages](tenants.md#on-networking-the-host-manages) shows: run the baseline once with tenant 1 alone in `qumulo_tenants` (its entry, or `Default` when the inventory declares tenant 1 absent or leaves it out), holding tenant 1's own networks, or every network the inventory lists for a tenant that stays when tenant 1 holds none, and no shares or exports of the other tenants; then run it with the inventory as it is.

## Rebuild in one run

`-e edge_rebuild_phase=all` runs the predestroy phase, your reinstall tasks and the replay in one run.
It checks everything the replay needs from the inventory before the predestroy phase drains anything.

1. Write a tasks file that reinstalls the edge, as your site does.
   It runs on the edge's host, and when it returns the node must answer at `qumulo_api_host`, UNCONFIGURED or newly formed.
2. Run it with the file's absolute path:

   ```sh
   ansible-playbook qumulo.cluster.edge_rebuild -i inventory -l edge01 \
     -e qumulo_rebuild=true -e edge_rebuild_phase=all -e edge_rebuild_reinstall_tasks=/srv/ansible/reinstall-edge01.yml
   ```

3. Apply `host_vars_patch` and work through the report as in steps 4 and 5 above.

Under `--check`, the run predicts the predestroy phase and stops before the reinstall.

The `edge_cache_resize` role runs the same rebuild on its `rebuild` route when `edge_cache_resize_allow_rebuild` is set ([Size and resize an edge's cache](edge-sizing.md)).
A failed automatic rebuild ends the run ([why](../explanation/licensing-and-capacity.md#a-failed-automatic-rebuild-ends-the-run)).
`qumulo.cluster.edge_cache_resize` goes on past an edge that failed only with a hand-off, and names it at the end.

## A Terraform-formed edge

Use this variant when a Terraform root forms the edge, such as one with a `qumulo_filesystem_edge_baremetal` resource.
The [terraform](../../plugins/inventory/terraform.py) inventory plugin reads the new UUID from the root's output ([Use a Terraform root's output as the inventory](terraform.md)), so the replay needs no `qumulo_new_cluster_uuid`.

Before the rebuild, check that the root forms the new edge as the replay expects:

- The root forms the edge with the name in `qumulo_cluster_name`, which the replay's guard checks.
- The root forms it with the `admin` password the replay connects with, `edge_rebuild_admin_password` or else `qumulo_api_password`.

Either one wrong fails the replay after the old edge is drained and the node reinstalled.

### Rebuild in one run with Terraform

1. Write reinstall tasks that reinstall the node as your site does, then replace the edge's resource.
   `-target` keeps the apply to the edge, so that no other pending change in the root lands in the middle of a rebuild.
   Without it, run `terraform plan` first and check that the replacement is the only change.

   ```yaml
   # reinstall-edge01.yml, given as edge_rebuild_reinstall_tasks with its absolute path
   - name: Form the reinstalled edge with Terraform
     ansible.builtin.command:
       argv: [terraform, -chdir=/srv/terraform/edges, apply, -auto-approve, -input=false,
              "-target=qumulo_filesystem_edge_baremetal.edge01", "-replace=qumulo_filesystem_edge_baremetal.edge01"]
     delegate_to: localhost
     changed_when: true
   ```

2. If the inventory source reads a saved output (`output_file`), add a task that saves the output again, as `terraform -chdir=/srv/terraform/edges output -json qumulo_ansible_inventory` prints it.
   With `project_path` the plugin runs that itself.

3. Run the rebuild with `ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true`, so that a source that fails to parse fails the run instead of leaving the edge without its UUID:

   ```sh
   ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true ansible-playbook qumulo.cluster.edge_rebuild -i inventory -l edge01 \
     -e qumulo_rebuild=true -e edge_rebuild_phase=all -e edge_rebuild_reinstall_tasks=/srv/ansible/reinstall-edge01.yml
   ```

After the reinstall tasks, the replay reads the inventory again and takes the edge's `qumulo_cluster_uuid` from it.
When it differs from the old edge's UUID, it is the new UUID, and `cluster_guard` checks it as it checks a typed `qumulo_new_cluster_uuid`.
A typed `qumulo_new_cluster_uuid` still wins.

Because the inventory already names the new UUID, the report's `host_vars_patch` holds `qumulo_previous_cluster_uuids`, and `qumulo_management_network` when the replay took it from the predestroy record, and Terraform state has nothing to fix (G-TF-STALE).
Apply the patch before the second pass, as the report's first hand-off says.
Until you apply it, a baseline run of the edge refuses a held relationship without saying that the replica may be the only copy, so don't release anything before the patch is in.
Leave the UUID and the replication address to the plugin, which reads them from the output's `cluster_uuid` and `replication_address`: a value in `host_vars` would outrank them.

If the inventory still names the old UUID after the reinstall tasks, as when the saved output was not saved again, the guard refuses the formed edge and says to save the output again.
Save it, then finish with a replay on its own as below.
Don't run the rebuild in one run again: it would run the predestroy phase against the new edge.

### Rebuild in separate runs with Terraform

1. Run the predestroy phase.
2. Reinstall and replace the resource: `terraform apply -replace=qumulo_filesystem_edge_baremetal.edge01`, and save the output again if the source reads a saved file.
3. Run the replay on its own, without `qumulo_new_cluster_uuid`:

   ```sh
   ansible-playbook qumulo.cluster.edge_rebuild -i inventory -l edge01 -e qumulo_rebuild=true
   ```

The inventory then names only the new edge, and no predestroy record names its UUID.
The replay takes the old edge from the newest predestroy record in the edge's artifacts directory that says it was safe to destroy, of a UUID that no rebuild report has already replaced.
The guard checks the new edge as above, and the report names the record in `previous_record`.
The records must be on the controller that runs the replay.

[Why the guard checks the new edge](../explanation/rebuilds.md#the-guard-on-the-new-edge).

## Finish the license

A new UUID needs a new license.
The collection never creates one or waits for one (G-LICENSE), so a rebuild finishes in two passes.
The replay is the first pass; [License an edge](licensing.md) describes both, and [why the license waits](../explanation/licensing-and-capacity.md#why-a-rebuild-needs-a-new-license).

1. Apply `host_vars_patch` to `host_vars/edge01.yml`, if you haven't.
2. Get the license for the new UUID by one of two routes:
   - A license file from Qumulo support: replace the old edge's document in `qumulo_capacity.license_document` with it, encrypted as [Keep secrets out of the inventory](store-secrets.md) shows.
     The replay doesn't apply the document still in the inventory, which is the old edge's, and a hand-off says to replace it.
     The second pass applies the new document and restores in the same run ([Replace a document for another UUID](licensing.md#replace-a-document-for-another-uuid)).
   - A license Qumulo issues to the cluster directly: no document is needed, and the old edge's document is not applied.
     The license reaches the cluster on its own, which can take hours.
     Run the second pass once it has; until then it fails with the license hand-off and no restore.
     Nothing is lost meanwhile, because the hub keeps its copy under the held relationship.
3. Run the second pass:

   ```sh
   ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l edge01
   ```

   It applies the document, sets the clamp, creates the relationships into the edge, and last restores the edge's held relationships.
   Add `--skip-tags restore` to run it without the restore play.

### An edge that runs without a license

Declare `qumulo_capacity.license: none` for an edge that never gets a license.
Its replay writes the declared clamp with no license, and the report says `edge01 runs without a license.` in place of a license hand-off.
Its second pass has no license to apply, and writes the clamp if the replay hasn't.

`none` can't sit beside a license document.
The rebuild refuses the pair before it drains the edge.

### If a clamp is refused

The rebuild refuses a declared clamp more than its rounding tolerance below min(1 TB, `edge_cache_resize_floor_bytes`), whatever `qumulo_capacity.license` says.
It refuses before the drain in a rebuild in one run, and before the formation in the replay phase of a rebuild in two runs.
[Troubleshooting](../reference/troubleshooting.md#rebuilds) has the two messages.

- Set `qumulo_capacity.clamp` to the value the message names, `1TB` unless `edge_cache_resize_floor_bytes` lowers it.
  Or remove the clamp: for an edge with `license: none`, on a platform smaller than 1 TB; for any other edge, when the platform or the license's cap is smaller than 1 TB.
  Keep the cache smaller with `free_threshold`.
- Lower `edge_cache_resize_floor_bytes` only to describe a platform smaller than 1 TB.
- In a rebuild in two runs, the predestroy run drains and destroys the edge without checking this.
  Nothing is lost when the replay then refuses, because the hub holds the data.

[Why the floor is 1 TB](../explanation/licensing-and-capacity.md#the-1-tb-floor).

## Work through the report

`artifacts/edge01/rebuild-<UTC>.yml` lists what is left to do by hand under `hand_offs`.
Work through them in order.
The first is always `host_vars_patch`.
They cover:

- the license for the new UUID, the second pass, and a capacity cap that waits for a quorum formation;
- the new `admin` password, when it differs from the one the inventory connects with;
- a spent Nexus join key, and the old UUID in Nexus when there is no API token to retire it (G-NEXUS-RETIRE);
- the DNS A and PTR records of the edge;
- each lock key the old edge held, by name, which the replay does not carry: register it again with `security_key`, from its private key or a signature of its name ([Manage the keys that lock snapshots](manage-lock-keys.md)); when the old edge's key store could not be read, the report says so and names no key, so register each key its snapshot policies declare;
- each snapshot policy that the baseline applied without its lock key, because the key was not registered and `-e qumulo_allow_deferred_policy_lock_keys=true` was given: its snapshots are not locked until you register the key and run the baseline again, which sets it;
- Terraform state that still names the old UUID (G-TF-STALE), unless the replay read the new UUID from the inventory;
- the domain's computer account: a leave with credentials deleted the old account with its group memberships and delegations, so grant them again; without credentials the old account was reused, so check them;
- a hub record of the old UUID that the predestroy phase couldn't reach (G-PORTAL-STALE);
- each replication relationship into the edge, which waits for the second pass, and each held relationship, with the way out ([Restore the replicas](#restore-the-replicas));
- the source hosts whose replication step fails at the edge's identity guard until `host_vars_patch` is applied;
- each acknowledged area:
  - access tokens to issue again to their consumers (G-TOKEN);
  - S3 keys, which the replay's baseline makes again for each declared entry.
    Each is named with the file of its secret under `<edge_rebuild_artifact_dir>/<host>/secrets/`.
    Hand the secret to its consumers, then change the entry's `access_key_id` to the new id (G-S3-KEY).
    Each entry that failed is named too, and the next baseline run after the patch tries it again;
  - replication the inventory doesn't declare, whose peers keep their records (G-REPL-PEER);
  - the key store (G-KMS), the network, and local data to restore.

## Restore the replicas

A rebuild keeps every replica that `qumulo_replication_relationships` declares, and what it does depends on the edge's side.
The predestroy record lists each relationship with what the rebuild does with it.

- **The edge is a target.**
  The relationship is replayable while the edge's record is ESTABLISHED with its directory read-only and the source host holds the same relationship ESTABLISHED.
  In any other state the directory is local data, and the predestroy phase refuses until `edge_rebuild_acknowledge` lists `local_data`.
  If the record is `only_copy`, the edge's replica may be the only copy of the source's data: copy it back to the source first, then list both `local_data` and `replication`.
  If the predestroy detail names a restore of the source first, run it, then check again: the relationship is then replayable and needs no acknowledgement.
  If the detail says to apply the source host's `host_vars_patch`, apply it and check again.
  The replay creates no relationship into the edge; each is a hand-off of the second pass.
  With `qumulo_capacity.license: none`, a baseline run of the source creates it after the replay.
  Without `qumulo_capacity`, a baseline run of the source creates it.
- **The edge is a source.**
  The target's replica may be the only copy of the edge's data, so the collection holds the relationship: the replay's baseline writes nothing on either cluster, `edge_rebuild_allow_data_loss` never lifts the hold, and every later `playbooks/baseline.yml` refuses it until you choose a way out.
  The report and the refusal name the ways that apply.

[Restore a rebuilt edge's data from its replica](restore-replica.md) covers the three ways out of a held relationship: restore it (the second pass does this once the license and clamp apply), copy the replica back, or keep the replica as plain data.
To finish a restore that the second pass stopped, or to restore a host outside `qumulo_edges`, use the restore playbook ([Restore with the restore playbook](restore-replica.md#restore-with-the-restore-playbook)).
On an edge in `qumulo_edges` it refuses while the license or the clamp the inventory declares hasn't applied, and names the second pass.

To delete a relationship whose peer is gone for good, see [Delete a relationship](restore-replica.md#delete-a-relationship).

The replay refuses `qumulo_replication_release` and `qumulo_replication_restore` at its first step, because both belong to later runs.
The restore's records and steps are in [Playbooks and roles](../reference/playbooks-and-roles.md#replication-restore).
[Why a replica is held](../explanation/replication.md#held-relationships).

## Keep the rebuild records

A rebuild writes its records under `artifacts/edge01/` beside the inventory source, or under `<edge_rebuild_artifact_dir>/edge01/`.
The predestroy record says the edge is safe to destroy, and a replay run on its own reads it.
The records must be on the controller that runs the replay and later the second pass.
In a pipeline, keep the directory between jobs, or run the rebuild in one run ([Run the playbooks from a pipeline](pipeline.md)).

## Verify

After the second pass:

1. Check that the newest `artifacts/edge01/rebuild-<UTC>.yml` says `status: replayed` and that its `hand_offs` are done.
   Any other status, `formed; the baseline failed`, `formed; the baseline has not run yet` or `formed; the formation failed afterwards`, means the replay didn't finish; see [Finish a replay that failed after the edge formed](#finish-a-replay-that-failed-after-the-edge-formed).
2. Run the baseline in check mode with a diff:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
   ```

The check run shows what still differs from the inventory.
A relationship the restore didn't finish, or a hold you haven't chosen a way out of, still refuses; [Respond to a refusal](respond-to-a-refusal.md) says how to read it.

## Related

- Before: [Pair an edge with its hub through a portal](pair-edge-with-hub.md), [License an edge](licensing.md).
- Next: [Restore a rebuilt edge's data from its replica](restore-replica.md).
- See also: [Use a Terraform root's output as the inventory](terraform.md), [Clone an edge's configuration onto a new edge](clone-edge.md), [Size and resize an edge's cache](edge-sizing.md).
- Reference: [Playbooks and roles](../reference/playbooks-and-roles.md), [Variables](../reference/variables.md), [Troubleshooting](../reference/troubleshooting.md#rebuilds).
- Why: [Rebuilds](../explanation/rebuilds.md), [Licensing and capacity](../explanation/licensing-and-capacity.md), [Replication](../explanation/replication.md), [State management](../explanation/state-management.md#check-and-diff).
