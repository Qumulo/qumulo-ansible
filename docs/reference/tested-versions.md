<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Tested Qumulo Core versions

What the collection is tested against, what each column of the README's tested-versions table means, and what the
test lanes cannot show.

## Requirements

| Requirement | Minimum |
|---|---|
| Qumulo Core | 7.10.3 |
| ansible-core | 2.20 (`requires_ansible: ">=2.20.0"` in `meta/runtime.yml`) |

[Install and pin the collection](../how-to/install-the-collection.md) lists the other prerequisites. `cluster_guard`,
which the baseline runs first unless `cluster_baseline_guard` is `false`, warns when a cluster runs a release older
than 7.10.3. Nothing refuses one. The upgrade
also runs on older sources: see [Supported sources](upgrade.md#supported-sources). The VM edge lane's records reach
back to 7.6.0; see [VM edge](#vm-edge-lane).

## Where the results are recorded

| What | Where |
|---|---|
| The table, one row per Core version | [the README's table](../../README.md#tested-qumulo-core-versions), between the `tested-versions` markers |
| Every build and every lane's results, release candidates included | The collection's source repository, which is not published |

The table is generated from those records.

Every build of a Qumulo Core release branch is tested from the moment the branch is cut, so a build is tested before
it ships. Tier 1 runs for every build. The CNQ, Hardware and VM edge lanes run for release candidate (RC) and
general availability (GA) builds, and when a past release is backfilled by hand. The VM edge lane runs only while it is
switched on.

## The table

One row per Core version, newest first.

### Row columns

| Column | Content |
|---|---|
| Qumulo Core | The base version. Rows group builds by base version, so a limited build such as 7.10.3A sits under 7.10.3. A row shows one build's results; a limited build's results show only when it is that build, and the full records, kept in the source repository, hold the rest. |
| Build | The Core build shown. For a version with a GA (or backfilled past release) result, that build. Otherwise the newest Core build recorded, which before a release is normally its newest release candidate. A version recorded only by its release tag shows `release <tag>`. A build whose posted version differs from the row's version, such as a limited build, shows the posted version in parentheses after the build. |
| Tested | The date of the newest result shown in the lane columns. |

The lane and job columns follow the rules under [Result values](#result-values), across every record the row draws on,
whatever kind of run produced it.

### Lane columns

Each build is tested on up to four lanes, each with a column.

| Column | Lane | What runs |
|---|---|---|
| Docker edge | tier 1 | Single-node Qumulo Core containers, in the five groups of tests below |
| CNQ | tier 2 | A single-node Cloud Native Qumulo cluster in AWS that the Terraform provider forms |
| Hardware | tier 3 | A four-node cluster formed on leased lab hardware |
| VM edge | tier 3, VMs | Qumulo Core's package installed on virtual machines, each forming one unprotected edge, and the edge's tests |

A lane's cell is its result, plain text with no link. The CNQ column shows the release matrix's `cnq` lane. A CNQ run
outside the release matrix is not recorded, so its results are not in the table.

### VM edge lane

The VM edge column shows the release matrix's `vm-edge` lane, which installs Qumulo Core from the build's package,
the package installer the Docker and Hardware lanes do not use. `Edge` is the Docker lane's job column; `VM edge` is
this lane, and the two are unrelated.

The lane's entries are `<os>/<mode>`: an operating system, in an install mode of the package. Each entry runs on two
virtual machines, each with a share of the edge tests. An entry's result is `fail` with the reason `install failed`
when its package did not install, otherwise the result of the edge tests on the edges it formed. The cell is the worst
result over the entries whose install did not fail, in the order `fail`, `not-tested`, `cancelled`, `skipped`,
`superseded`, `pass`, and `fail` when every entry's install failed.

Under the cell, whatever it reads, a note names each entry that did not pass, so one entry's pass never hides another's
result:

- `<os>/<mode>: install failed`;
- `<os>/<mode>: <failed tests>`;
- `<os>/<mode>: not tested: <reason>`, or `cancelled`, `skipped` or `superseded` with its reason, for an entry with no
  pass or fail.

The note is plain text with no link.

The lane is advisory. A fail in it does not make a build red, and a collection release needs the Docker, CNQ and
Hardware lanes only. A backfill of the lane reaches back to 7.6.0, below the 7.10.3 the collection supports; such a row
fills the VM edge column alone, and its other lane columns read `not-tested`. It is history: a `pass` there says the edge tests ran clean on
that release, not that the collection supports it.

### Tier 1 job columns

The Docker lane runs five groups of tests on separate containers, each with a column of its own.

| Column | What runs | Blocks the Docker edge result |
|---|---|---|
| Edge | The API route check, the modules' integration tests and the single-edge Molecule scenarios, on a formed unprotected edge | Yes |
| Formation | Forming an unprotected edge from an unconfigured node | Yes |
| Pair | A hub and an edge: portal pairing, replication between them, and the pair's Molecule scenarios | No |
| AD | Joining a Samba Active Directory domain | No |
| Rebuild | A whole edge rebuild on a hub and an edge, including the reinstall | No |

Three of the columns each gather several jobs, and each job starts containers of its own:

| Column | Jobs |
|---|---|
| Edge | `edge (1)`, `edge (2)` and `edge (3)`, each with a share of the integration tests, and `edge (scenarios)` and `edge (delete-tenant1)`, each with a group of the Molecule scenarios |
| Pair | `pair (<group>)`, one for each of four groups of tests |
| Rebuild | `rebuild (<group>)`, one for each of five groups of scenarios |

Such a column shows the worst of its jobs' results: `fail`, then `cancelled`, then `skipped`, else `pass`.

The Docker edge cell is `pass` when Edge and Formation both pass, and `fail` when either fails. Pair, AD and Rebuild
do not change it. Each job column shows `pass`, `fail`, `skipped` or `cancelled`; a blank cell means no result is
recorded.

### Result values

| Value | Meaning |
|---|---|
| `pass` | The lane ran and every test passed. |
| `fail` | The lane ran and a test failed. |
| `not-tested` | The lane did not run, or ran and could not test the build, for example when no lab hardware was free, or when the VM edge lane's switch was off. |
| `superseded` | A newer build of the same version was tested instead. |
| `skipped` | The job was skipped. |
| `cancelled` | The job was cancelled. |

Rules:

- A cell shows the lane's latest `pass` or `fail` for the build. Only a lane with neither shows its latest
  `not-tested`, `superseded`, `skipped` or `cancelled`.
- A lane with no recorded result reads `not-tested`.
- Under a row with a `fail`, a note row names the failing tests under each failed column other than Docker edge, or the
  job when it named no test. The names are plain text with no link. The Edge and Formation notes name the Docker lane's
  failures.
- The VM edge column's note appears under whatever its cell reads, and names each entry that did not pass, as
  [VM edge lane](#vm-edge-lane) says.

## What the lanes cannot test

### Tier 1 containers

These are not tested there:

- Floating addresses, and a managed network.
- Authoritative DNS answers. The image runs no DNS server, so the `authoritative_dns` integration target checks the
  stored settings only.
- The cluster's OpenAPI document. The Qumulo Core 7.10.3 image answers 500 on `/openapi.json` and 404 on
  `/v1/openapi.json`, so the live contract tests skip ([G-CONTAINER-OPENAPI](gaps.md)).
- An upgrade. The image has no installer, so no container is ever upgraded.
- Node operations, KMIP and RDMA ([G-CONTAINER](gaps.md)).
- An LDAP server, so `ldap_user:` and `ldap_group:` trustees, and the lookups of them, are tested in unit tests only.
- A VLAN-bound tenant, and service isolation. The container has one host-managed interface and no VLAN, so a network
  bound to a second tenant serves no client there; only tier 3 tests them
  ([Tier 3 tenants and managed networking](#tier-3-tenants-and-managed-networking)).
- A second tenant without a declared management network. The container answers the network status with HTTP 500
  ([G-NET-APPLY](gaps.md)), so every two-tenant case there declares `management_network`, on a HOST network whose id the
  container's host associates with its interface.
- Tenants behind a cloud cluster's load balancer. Tier 2 tests that a cloud cluster refuses every tenant write and that
  its tenants read ([G-TENANT-CLOUD](gaps.md)), and its capture, and no second tenant is ever made there.

Tier 2 reads the floating addresses the Terraform provider owns. Tier 3's `network_config` target reads the network and
writes nothing. Its `tenancy` target is the only one that writes networks and floating addresses, as
[Tier 3 tenants and managed networking](#tier-3-tenants-and-managed-networking) lists. No lane yet checks authoritative
DNS answers or runs the OpenAPI check against a served document. For upgrades, see [Upgrades](#upgrades).

### NFS with Kerberos

No lane runs an NFS client with Kerberos, so no lane shows that a client can sign in against an installed keytab. The
cluster stores any well-formed keytab without asking a KDC ([G-KRB-KEYTAB](gaps.md)), so the `kerberos_keytab` and
`kerberos_settings` integration targets test a keytab as stored and compared: install, a second run with no change, a
rotation, a change of keys alone, removal, every refusal but the one on a joined cluster, which the AD job's target
checks, and that no result holds a key. They run on the Docker edge for
each version the lanes run, and on the VM edge, with keytabs the target writes at run time.

The AD job's `kerberos_keytab_ad` target uses the lane's Samba domain controller as a standalone KDC. It exports a
keytab for an NFS principal of a service account, installs it on the edge outside the domain, and checks that the join
and the keytab refuse each other ([G-KRB-AD](gaps.md)). That shows that a keytab a KDC writes reads in the module as it
does in the cluster, and not that a client can use it.

### Tier 3 tenants and managed networking

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

The `tenancy` target runs only on tier 3's cluster: four nodes on managed networking, formed with one tenant and one
DHCP network on the frontend bond, with two VLANs that the nodes' ports carry tagged. Each module write runs in check
mode first. It writes:

- The untagged network moves from DHCP to STATIC on the lane's node and floating addresses, in the tenant that holds
  it, with [network_config](../../plugins/modules/network_config.py)'s `allow_management_disruption` and
  `api_host_after`.
- A second tenant, with SMB on, and a third, with NFS on. Both have every other service off: REST, the web UI, SSH and
  replication. The first create warns that it rebinds every listener (G-TENANT-LISTEN), and each create that it removes
  the static routes (G-TENANT-ROUTES).
- SMB and NFS turned off on the tenant that holds the untagged network.
- Each test VLAN's network, a STATIC network on a tagged VLAN, bound to one of the two new tenants in one network
  write. Before the tenants exist, a check run of that write reports the change pending both creates, with each network
  under its tenant's name and no tenant id yet. After the creates, the check run predicts each network in its tenant,
  and the cluster validates the document.
- A share in the SMB tenant, an export in the NFS tenant, one share of one name in both, and in each an override of its
  own protocol's settings (SMB signing required; an NFS ID-mapping domain).

It checks isolation from the configuration the API reports, never from a client on a network:

- Each network's tenant, read from the tenant's list of networks and from the network's own tenant.
- Each tenant's switches, and from them which networks serve REST on port 8000, the web UI, SMB and NFS: REST and the
  web UI on the untagged network only, SMB on test VLAN 1's network only, NFS on test VLAN 2's network only.
- Every node's managed interfaces applied, and the nodes holding exactly the lane's addresses of the three networks
  (the untagged network and test VLANs 1 and 2).
- Each tenant's shares, exports and overrides, which are its own.

It runs these scenarios against its own connection. Each runs in check mode and, except where a bullet says otherwise,
then for real; a refusal must read the same in both:

- REST off, or the web UI off behind a declared `cluster_port` of 443, on the tenant that holds the network the play
  connects to: refused.
- REST off on the tenant that holds the management network, judged in check mode only on planned bindings
  (`planned_networks`, `planned_tenants`): allowed on a plan that moves the network to a tenant with REST on, refused on
  one that does not.
- The management network moved to a tenant with REST off: refused. Moved to a tenant with REST on, and back: allowed.
- The play's address moved into the ranges of a network whose tenant has REST off: refused.
- A declared management network whose tenant has REST off: refused.
- A new network that names no tenant: refused.
- One VLAN carrying the networks of two tenants: refused.
- The delete of a tenant that holds a network and shares: refused, naming them.
- A test VLAN's network moved to the other new tenant in one network write, with no tenant's switches changed, then
  moved back.
- When the lane formed the cluster with a network on the backend bond: the create of a second tenant, refused while the
  only tenant holds that network ([G-TENANT-BACKEND](gaps.md)); the target then unbinds it through the API. No lab
  cluster has had such a network yet, as the backend-bond case below says, so this has not run there; unit tests cover it.

A second run of every declaration reports no change, in check mode and for real.

It also runs the [cluster_baseline](../../roles/cluster_baseline/) role and
[capture_info](../../plugins/modules/capture_info.py) on the same cluster. Each baseline run that writes runs in check
mode first, and a refusal must read the same in both. Where only the play's recap shows that a run changed nothing, the
target runs [playbooks/baseline.yml](../../playbooks/baseline.yml) against an inventory it writes, and the recap must
show no change.

While the cluster has one tenant, before the module scenarios above:

- The management network unbound by hand while the only tenant holds another network: a baseline run that adds a
  tenant and moves the play's address into the other network's ranges is refused before any write, since the create is
  judged on the network as the cluster has it, which no tenant holds. The target then binds the network again.
- When the lane formed the cluster with a network on the backend bond: capture writes that network in
  `qumulo_network`, not under the tenant, and a check run of the baseline from the capture changes nothing. No lab
  cluster has been formed with a network on the backend bond yet, so this has not run there; unit tests cover it.

On the three tenants, these baseline runs are refused before any write:

- A shared share that names a tenant neither the inventory declares nor the cluster holds.
- An inventory that leaves out a tenant that holds a network, so the run would remove the network.
- A tenant network that the declared backend bond lists.
- A network change judged on an `api_host` name that does not resolve.
- A network change judged on an `api_host` name with an IPv4 and an IPv6 address, from a hosts entry on the controller:
  the untagged node address the play uses, and an address of an IPv6 network the run would add to test VLAN 1 under
  the SMB tenant, which has REST off. A check run with REST on there passes, judged on both networks. This needs sudo
  without a password on the controller, and is skipped with a note otherwise.
- The delete of a tenant that holds an export no entry declares or claims.

A replication address on test VLAN 1's network, whose tenant has replication off, is refused too:
`replication_target`, given the address as the role's replication step passes it, reads the tenants and the network
document and refuses in check mode and for real before it reads the relationship or writes anything, naming the network
and the tenant. No relationship is made, since the lane leases
one cluster.

Then these baseline runs write:

- The NFS tenant's whole entry marked `state: absent`. The run removes the tenant's network and VLAN, its copy of the
  shared share, its export and its override, and then the tenant. The check run predicts each removal and the delete
  and writes nothing. A second run changes nothing, in check mode and for real.
- The NFS tenant declared again. One run creates it with its network and VLAN, its override, its export and its copy
  of the shared share, and the target checks isolation and each tenant's content again. A second run sends no write.
- The untagged network declared under a new id with the same addresses, in a run that also creates a fourth tenant.
  The create is judged on the network's id on the cluster and the network write on its new id, and the run converges.
  A second run changes nothing, and a baseline run deletes the fourth tenant again.

Capture, from the three tenants:

- Each tenant by name, with its network and VLAN, its override, its shares and its export, and the shared share once. A
  check run of the baseline from the capture changes nothing.
- With a network that no tenant holds, made through the API: the network is written in `qumulo_network` without a
  tenant, and noted.
- With test VLAN 1 also carrying an IPv6 network that no tenant holds, made through the API on documentation addresses,
  one for each node, as Qumulo Core requires: the VLAN is kept in `qumulo_network.managed_interfaces` and noted, and a
  check run of the baseline from the capture refuses it as declared in two places. The target then removes the IPv6
  network. When Qumulo Core refuses such a network, the target notes the refusal's error class and skips this case.

The target ends with the untagged network STATIC, under its new id, and both new tenants in place, with their networks,
shares, export and overrides. Before the lab releases the cluster, it returns the untagged network to DHCP. That does
not restore the static routes that the second tenant's create removed, and on a cluster formed with a network on the
backend bond, that network stays in no tenant.

These are not tested on tier 3:

- A client on a test VLAN. The target connects only on the untagged network, so which services answer on each test VLAN
  is read from the configuration, not observed.
- A replication relationship into a tenant with replication off. The lane leases one cluster, so the target checks the
  refusal without one, as listed above.
- A VLAN that carries two tenants' networks. Qumulo Core cannot put one VLAN on two tenants, so no cluster has one, and
  unit tests cover capture's note for it.

### A rebuild in a container

Each of the five Rebuild jobs runs its group's rebuild scenarios on a hub and an edge container of its own, including
the reinstall, which starts the edge's container again from a fresh state. A licensed edge, its retirement in Nexus and the second pass of licensing are shown only by
`edge_rebuild_replica` and `edge_rebuild_restore`, and only in a run with the Nexus token. Only `edge_rebuild_restore`
shows the second pass's restore of a relationship out of the edge. Apart from a rebuild, the `nexus_*`, `license` and
`edge_license_complete` targets show licensing and Nexus on a single edge, also only with the token.

These paths are not verified against a cluster within a rebuild; they wait for the tier 2 and tier 3 jobs:

- An Active Directory leave and rejoin (the AD job covers the `ad_domain` module alone).
- Managed networking.
- A cache larger than the container's disk.
- A drain that fails after the spoke delete.
- A portal to a hub the inventory does not declare.

### Upgrades

No lane has yet measured the oldest source an upgrade supports ([Supported sources](upgrade.md#supported-sources)).

The range is measured by backwards testing:

- It is a run by hand of a lane's upgrade-from mode.
- The run installs the oldest source the lane can install. Tier 2 starts only from a source that has a cloud image.
- It runs one chain from that source to the current GA through every quarterly release on the way, and records each
  hop's result.
- A failed hop ends the chain. The lane installs that hop's target afresh and runs on from there.
- The supported range starts after the last failure.
- The chain tests clusters upgraded to each release, not clusters formed at it.

What the tests cover:

| Level | Covered |
|---|---|
| Unit tests | The module's refusals, waits and outcomes; the plan's arithmetic over every quarterly release; the schedule; the report |
| Tier 1 integration targets | The reads, the refusals before verify, the module's check mode, and the limited-release waiver, which the container answers with F8 |
| Tier 1 Molecule scenario `cluster_upgrade_plan` | `playbooks/upgrade.yml --check` on the edge: the plan's hop, the module's prediction and W1, with nothing written but the report |
| `ansible-playbook` against stub modules | The playbook's and the role's hop loop, scripted per host, for the step rules a fleet hop rests on; the restore locks run against the real lock module |
| Tier 3, by hand | One hop of a four-node lab cluster with the `upgrade` module, below |

The one live hop, on 2026-11-08, took a four-node lab cluster from 7.10.3, build 385145.1.82, to 7.11.0, build
393623.1.21:

| What | Observed |
|---|---|
| Upgrade type and reboot | `SOFTWARE_ONLY`; no reboot |
| Prepare, the image's download and verify on every node included | 121 seconds |
| Commit, from the request until every node is online on the new build | 22 seconds |
| REST unanswered during the commit | 5 seconds |
| After the hop | the same cluster on 7.11.0 and build 393623.1.21, its upgrade status IDLE; a second run changed nothing |

What no lane has run:

- A fleet hop.
- A portal component or a replication pair across an upgrade.
- A platform upgrade's reboots, rolling or all at once, or a declared set size: the live hop needed no reboot.
- A prepare of a platform upgrade. Whether a 7.10.x cluster reads back no reboot count after a prepare that sent none,
  which the module's wait for PREPARED needs, is unverified. If every host refuses F17 right after its own prepare, see
  [Refusals during a hop](troubleshooting.md#refusals-during-a-hop).

The hop paths with no live run are covered by the unit tests and the stubbed role test only.

## See also

- [Install and pin the collection](../how-to/install-the-collection.md)
- [Run the playbooks from a pipeline](../how-to/pipeline.md)
- [Upgrade a fleet](../how-to/upgrade.md)
- [The upgrade: variables, steps and files](upgrade.md#supported-sources)
