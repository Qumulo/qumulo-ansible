<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->
# replication_failover

Fails a replication relationship over to its target, copies the target's writes back to the source, or fails it back, one step at a time from fresh reads of both clusters. The role runs from the source host's context, for the relationships `replication_failover_relationships` names among the host's `present` entries of `qumulo_replication_relationships`. The source and the target are the declared ones, whatever direction the records run in now. `playbooks/replication_failover.yml` runs it; `docs/how-to/fail-over-replication.md` is the runbook, and `docs/explanation/replication.md` says why it works as it does.

## What it does

| `replication_failover_action` | Steps |
|---|---|
| `failover` | makes the target's copy writable, from its recovery point, with `qumulo.cluster.replication_reverse`, and waits until the target's record reads DISCONNECTED; sends nothing to the source, so it works with the source gone |
| `resync` | reverses the relationship on the target toward the source's replication address; turns `map_local_ids_to_nfs_ids` on in the target's new source record when the declared entry maps ids; reconnects it on the source, which compares the source's directory with the recovery point first; waits until both records read ESTABLISHED. The target then replicates the failover's writes to the source while clients still use it |
| `failback` | resyncs when the relationship has not been resynced; waits until the target's recovery point has moved twice; makes the source writable; reverses the relationship on the source toward the target's replication address; reconnects it on the target, which compares the target's directory with the recovery point first; waits until both records read ESTABLISHED; applies the declared settings where the source's record differs |

Before each step the role reads both clusters with `qumulo.cluster.replication_info` and asks `qumulo.cluster.replication_failover_plan` for the next step, then runs only that step. A run that stops anywhere resumes from what the records show. The plan names the relationship's state, lists every step the action has left, and refuses, before the first step, what a step would be refused for: a relationship with no record or with records in both directions, a live record from one of the source host's previous UUIDs, a target with no recovery point, a job running on a source that answers, a source that did not answer for a resync or a failback, a reverse toward an address that is not an IP literal, and a new target whose tenant has replication off. `docs/reference/playbooks-and-roles.md` lists the states and steps.

Before any read or plan, the role refuses a resync or failback of a relationship whose restore record is in `<replication_failover_artifact_dir>/<source host>/restore/`, since a restore of the rebuilt source is under way and the action would turn the relationship away from it, and names `playbooks/replication_restore.yml`. A failover cannot do that, so it notes the record and goes on.

The role reads and plans every named relationship, prints each one's state and every step left, and refuses on any refusal, before the first write of any. A real run with a step left then fails without `replication_failover_allow_data_loss`. Check mode needs no `replication_failover_allow_data_loss`, prints the plan and writes nothing: it is the dry run. In check mode too, a failover is refused without `replication_failover_allow_partial_job` while the source is running a job of the relationship.

Each reconnect is sent without `allow_data_loss` unless `replication_failover_allow_newer_writes` names the host it is sent to, so `qumulo.cluster.replication_reverse` first compares the directory with the recovery point and refuses when anything was written there since. A write made in the few requests between the comparison's snapshot and the reconnect is discarded without being compared, so stop the writes first. A failback runs each relationship up to its wait, then waits for the waiting relationships, those on one target host together and the target hosts one after another, within one `replication_failover_sync_timeout` from the start of the waits, then cuts each over.

In a failover the source is read with `replication_failover_source_timeout` seconds as its `retry_timeout` and `request_timeout`. In any action, a source that does not answer (it gives up, or a 502, 503 or 504 after its retries) is noted and not read again in the run, and a resync or a failback then refuses every relationship. Any other failure of the read fails the run.

## Variables

`meta/argument_specs.yml` is the reference; the main ones:

| Variable | Default | Meaning |
|---|---|---|
| `replication_failover_action` | required | `failover`, `resync` or `failback` |
| `replication_failover_relationships` | required | the relationships to act on, each `<target host>:<target path>`, compared without the path's trailing slash |
| `replication_failover_allow_data_loss` | `false` | the run's consent, which every real run with a step left needs; what it accepts depends on the action |
| `replication_failover_allow_newer_writes` | `[]` | the inventory hosts whose directory a reconnect may send back to the recovery point although the comparison found writes there; a string is split at commas into host names, and a name matches a whole host name only |
| `replication_failover_allow_partial_job` | `false` | let a failover discard the changes of a job the source, which answers, is running |
| `replication_failover_wait_timeout` | `1800` | seconds each wait for a record's state may take |
| `replication_failover_sync_timeout` | `86400` | seconds a failback's wait for a current recovery point may take, for every named relationship together |
| `replication_failover_source_timeout` | `30` | seconds a failover's read of the source waits before it goes on without the source |
| `replication_failover_artifact_dir` | `edge_rebuild_artifact_dir`, else the `edge_rebuild` role's default | where a rebuilt source's restore keeps its records, `<dir>/<source host>/restore/`; a resync or failback of a relationship whose restore record is there is refused, naming `playbooks/replication_restore.yml`, and a failover notes the record and goes on |
| `replication_failover_connections` | required | the connection options of the source host and of each target host, by inventory host, as `cluster_baseline_connections` holds them; the playbook fills it |

The role sets the three consents in its own `vars/main.yml`, so a value in `group_vars`, `host_vars`, a play's `vars` or `vars_files` is ignored. Anything a playbook sets closer to the role or during the run lifts them: `-e`, the include's `vars` (as in the example below), a block's `vars`, role parameters, `set_fact` and `include_vars`. Each new target's tenants are read once in the run, not once for each relationship, and its network document only when it has more than one tenant. The role reads the source host's `qumulo_cluster_uuid`, `qumulo_previous_cluster_uuids`, `qumulo_replication_address` and `qumulo_replication_port`, and each target host's `qumulo_cluster_uuid`, `qumulo_replication_address` and `qumulo_replication_port`. A resync needs the source host's address as an IP literal, and a failback the target host's, and the source host's too when it runs the resync's steps first. The tenant that serves each address that a reverse is sent to must have `replication_enabled`, which is read with `PRIVILEGE_TENANT_READ` on that cluster and, with two or more tenants, `PRIVILEGE_NETWORK_READ`.

The role sets the fact `replication_failover_results`, one entry per relationship with `name`, `id`, `state_before`, `state_after`, `steps`, the names of the steps run, `plan`, the lines of the steps the first plan listed, and `notes`.

## Example

```yaml
- name: Fail the edge's projects over to the DR cluster
  hosts: edge01
  gather_facts: false
  tasks:
    - name: Record how to connect to the edge and to the DR cluster
      ansible.builtin.include_role:
        name: qumulo.cluster.cluster_baseline
        tasks_from: connections.yml
      vars:
        cluster_baseline_connection_hosts: [edge01, dr01]

    - name: Make dr01's copy of the projects directory writable
      ansible.builtin.include_role:
        name: qumulo.cluster.replication_failover
      vars:
        replication_failover_action: failover
        replication_failover_relationships: ["dr01:/replicas/edge01-projects"]
        # Set here, it lifts the consent for every run of this play; the playbook takes it from -e instead.
        replication_failover_allow_data_loss: true
        replication_failover_connections: "{{ cluster_baseline_connections }}"
```

Run with `--check` first: the role then prints the plan and writes nothing. The inventory declares `edge01` and `dr01` with `qumulo_cluster_uuid` and `qumulo_replication_address`, for example `203.0.113.10` and `203.0.113.40`, and the relationship in `edge01`'s `qumulo_replication_relationships`.

## Requirements

Qumulo Core 7.10.3 or later on both clusters, compatible versions rather than equal ones, and ansible-core 2.20 or later. The account the role connects as can make a target writable, reverse and reconnect a relationship, and create and delete a snapshot of the target directory and read the differences between snapshots, since the reconnect's comparison does so. The server behaviour it works around is recorded in `docs/reference/quirks.yml` (QUIRK-125) and `docs/reference/gaps.md` (G-REPL-ONESHOT, G-REPL-SNAPSHOT).
