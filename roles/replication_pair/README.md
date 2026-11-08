<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->
# replication_pair

Declares a replication relationship from a directory on a source cluster to a directory on a target cluster, or removes it. The role reaches both clusters from one host, with one set of connection options for each, and identifies the relationship by the two paths and the target's UUID, never by a server id or the address. The `cluster_baseline` role runs it at step 21 for each relationship a source host declares in `qumulo_replication_relationships`; `docs/reference/variables.md` describes those variables.

## What it does

| `state` | Steps |
|---|---|
| `present` | runs `qumulo.cluster.replication_relationship` on the source and `qumulo.cluster.replication_target` on the target in check mode, with the options their real passes take, the target's check including that the tenant serving the target's replication address has replication on; joins both with `qumulo.cluster.replication_pair_plan` and stops, before any write on either cluster, on any unmet condition nothing lifts; for a target edge whose capacity the inventory declares, defers a create or an authorize until its license and clamp apply; creates the target directory's declared ancestors, then the target directory, path only; creates the relationship on the source, or PATCHes its settings; authorizes it on the target and waits until both sides are ESTABLISHED |
| `absent` | deletes the relationship on the source, with the records of the target's previous UUIDs, then on the target, keeping the target directory and its data, and names the snapshots left |

Every refusal names what would lift it and reports every unmet condition of both sides at once: the rows of the overwrite table, which `docs/explanation/replication.md` explains, and the conditions outside it, such as a missing source directory. A row a release lifts is printed with its release entry, `<target host>:<target path>@<record id>:<rows>`, to pass with `-e` for one run. An entry that lifts nothing in the run fails it, so one left in an inventory never waits for a case to lift.

A relationship is held while the source's previous UUIDs are set and its replica may be the only copy of the rebuilt source's data: a holder from a previous source UUID (row O2), or an empty source over a target directory that holds files (row O4). When the holder allows a restore, a live record from a previous source UUID, ESTABLISHED, read-only and with a recovery point, over a source directory that holds no file, from a source host (`replication_pair_source_host`) with an IP-literal `qumulo_replication_address`, the message names the restore: the source edge's second pass, in its form for an edge that expects a license or one that declares `qumulo_capacity.license: none`, or `playbooks/replication_restore.yml` for a host outside `qumulo_edges`, and `replication_pair_result.restore_form` is true. Otherwise the way out is a copy of the replica back into the source directory, then a run with the release the refusal printed. Either message prints the release that keeps the replica as plain data.

A relationship in a failover state, after a make-writable by hand, is left as it is. Its settings are still PATCHed, the wait is skipped, and both modules warn.

The capacity gate applies only when `replication_pair_target_capacity` is set, which step 21 does for a target in `qumulo_edges` that declares `qumulo_capacity`, and only when a create or an authorize is due. Without an active license, or with the declared clamp still to write, the role deletes only the source's records of the target's previous UUIDs that cannot be replicating to an old edge kept beside the new one, and hands off to `ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l <edge>`, the edge's second pass, which applies the license and writes the clamp, then creates the relationship; the hand-off's opening names the half that deferred. An edge that declares `qumulo_capacity.license: none` runs without a license, so it passes the license half while it holds none; the clamp half applies to it as to every edge. A deferral does not fail. In check mode the gate takes the edge's license step's prediction when the edge is in the play, its `cluster_baseline_license_pending` set and not none.

The target's check of the replication address's tenant reads the target's tenants, which needs `PRIVILEGE_TENANT_READ`, and with two or more tenants its network document, which needs `PRIVILEGE_NETWORK_READ`. In check mode it takes the tenant's switch from the target host's own `cluster_baseline` steps 3a and 3b, when that host planned them earlier in the play, since a real run has written them by then. It reads the networks as they are, so a check run that also moves the address's network to another tenant judges the tenant before the move.

## Variables

`meta/argument_specs.yml` is the reference; the main ones:

| Variable | Default | Meaning |
|---|---|---|
| `replication_pair_source`, `replication_pair_target` | required | each side's connection options, as `module_defaults` for `group/qumulo.cluster.qumulo` takes them, with `expected_cluster_uuid` set |
| `replication_pair_relationship` | required | one entry: `source_path`, `target_path`, the settings, `allow_non_empty_target`, `recreate`, `strict_capacity`, `state` |
| `replication_pair_target_host` | required | the target's inventory host, as release entries and hand-offs name it |
| `replication_pair_target_address`, `replication_pair_target_port` | none, `3712` | the IP address and port the source reaches the target at, from the target host's `qumulo_replication_address` and `qumulo_replication_port`; `replication_target` checks the tenant that serves the address, on every run |
| `replication_pair_target_directories` | `[]` | the target host's `qumulo_directories`; `allow_non_empty_target` counts only for a target directory declared here |
| `replication_pair_target_capacity` | none | the target edge's `qumulo_capacity`, which turns the capacity gate on |
| `replication_pair_release` | `[]` | the release entries that name this relationship |
| `replication_pair_previous_target_uuids`, `replication_pair_previous_source_uuids` | `[]` | each side's previous UUIDs |
| `replication_pair_target_gone` | `false` | with `state: absent`, delete on the source only, for a target gone for good |
| `replication_pair_on_held` | `fail` | `report` records a held relationship and writes nothing, as the rebuild's replay does |
| `replication_pair_source_host` | `""` | the source's inventory host; a real pass that pairs a relationship from a source with previous UUIDs, while that host's newest rebuild report claims it, writes the restore stamp that retires the claim, and only warns when it cannot; a held relationship's message names the restore when its holder allows one, by this host's `qumulo_replication_address`, `qumulo_capacity.license` and `qumulo_edges` membership |
| `replication_pair_artifact_dir` | `edge_rebuild_artifact_dir`, else the `edge_rebuild` role's default | where the restore's files go, under a directory named after the edge's inventory host |

The role takes the address as given: it never derives it from `api_host` or the cluster's network status, and behind NAT, making it reachable is yours to do.

The role sets the fact `replication_pair_result`, whose `status` is `paired`, `failover`, `deferred`, `held` or `removed`, with each side's record, the hand-off, or the held message, releases, holders, key and `restore_form`.

## The restore

`tasks_from: restore.yml` restores a rebuilt edge's relationships from their replicas, from the edge's host context: for each `present` relationship out of the edge whose old record on the target, from one of the edge's `qumulo_previous_cluster_uuids`, still holds the replica, it syncs the replica back into the edge's source directory, turns the relationship around and reconnects it, so it runs from the edge's current UUID with its declared settings. `qumulo.cluster.replication_restore_step` decides which relationships the run picks and, before every step, the next one, from fresh reads of both clusters; the role runs only its answer. Phase 1 takes each relationship in turn up to the full sync's wait; phase 2 waits for the syncs together and finishes each as it lands. A real run holds the edge's lock, `<artifact dir>/<edge>/restore/lock`, so one restore runs per edge at a time on a controller. `playbooks/replication_restore.yml` and the second pass, `playbooks/edge_license_complete.yml`, run it; `meta/argument_specs.yml`'s `restore` entry point documents each variable. The playbook runs on `replication_restore_hosts`, `qumulo_clusters` by default, so a Molecule converge that imports it names the edge there. The restore's messages name its records, X and R, and its steps by number, which the Replication restore section of `docs/reference/playbooks-and-roles.md` lists.

| Variable | Default | What it is |
|---|---|---|
| `replication_restore_entry_point` | required | `playbook` or `second_pass`, the entry point each way on names |
| `replication_pair_connections` | required | the edge's and each target host's connection options, as `cluster_baseline_connections` holds them |
| `replication_restore_targets`, `replication_restore_report_claims` | read | the second pass's target read and report claim; without them the restore reads both |
| `qumulo_replication_restore` | every `present` relationship | the `<target host>:<target path>` names to restore, passed with `-e` |
| `replication_restore_wait_timeout` | `86400` | seconds step 4 waits for the full sync's first recovery point |
| `replication_restore_poll_delay` | `30` | seconds between phase 2's reads of the edge's target list |
| `replication_restore_reverse_timeout` | `300` | seconds step 5 polls until R reads DISCONNECTED on the edge |
| `replication_restore_reconnect_timeout` | `1800` | seconds step 8's polls take until R reads ESTABLISHED on both clusters |
| `replication_restore_resume_window` | `86400` | seconds within which an unmarked restore record still resumes the reconnect |

The restore sets the fact `replication_pair_restore_outcomes`, one entry per relationship with its `outcome`, such as `restored`, `done`, `refused`, `stopped`, `not restored`, `not applicable` or `failed`, whether it `fails` the run, and its `message` and `summary`.

## Example

```yaml
- name: Replicate the edge's projects to the DR cluster
  hosts: localhost
  gather_facts: false
  roles:
    - role: qumulo.cluster.replication_pair
      vars:
        replication_pair_source:
          api_host: edge01.example.com
          api_password: "{{ edge01_password }}"
          expected_cluster_uuid: "{{ edge01_cluster_uuid }}"
        replication_pair_target:
          api_host: dr01.example.com
          api_password: "{{ dr01_password }}"
          expected_cluster_uuid: "{{ dr01_cluster_uuid }}"
        replication_pair_target_host: dr01
        replication_pair_target_address: 203.0.113.40
        replication_pair_target_directories: [{path: /replicas/edge01-projects}]
        replication_pair_relationship:
          source_path: /projects
          target_path: /replicas/edge01-projects
```

Run with `--check` first: the role then predicts each side's change and waits for nothing.

## Requirements

Qumulo Core 7.10.3 or later on both clusters, compatible versions rather than equal ones, and ansible-core 2.20 or later. The idempotency gaps it works within are recorded in `docs/reference/gaps.md` (G-REPL-PEER, G-REPL-ONESHOT, G-REPL-OVERWRITE, G-REPL-NOTIFY, G-REPL-SNAPSHOT, G-REPL-TZ).
