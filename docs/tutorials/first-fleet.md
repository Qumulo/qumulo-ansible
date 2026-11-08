<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Your first fleet

In this tutorial you bring two Qumulo clusters you already run, a hub and an edge, under one Ansible inventory. You
connect to both and read them, adopt what they hold into the inventory, check that the inventory and the clusters
agree, and make one small change that you can undo.

The tutorial starts from clusters that exist. It does not build one. The edge is a virtual or hardware edge, which the
collection calls an unprotected edge. The hub can be any cluster: an edge, a Cloud Native Qumulo (CNQ) cluster or a
hardware cluster. The edge may already be paired with the hub through a portal. Where a step differs by kind of
cluster, it says so.

These are real clusters, so the steps are ordered by how much they change:

| Steps | What they do | Change a cluster? |
|---|---|---|
| 1 to 5 | Install the collection, write an inventory, connect, and read both clusters | No |
| 6 and 7 | Adopt the clusters into the inventory, and check that the two agree | No: capture and a check run only read |
| 8 | Create one empty directory on the edge | Yes, if you choose to |
| 9 | Check that the baseline changes nothing | No: a check run only reads |

You can stop after any step. Step 8 says how to undo what it does.

It follows [Your first cluster](first-cluster.md), which adopts one cluster and explains the check run in more detail.
You do not need to have done it.

## Before you start

You need:

- **A hub and an edge.** The edge is a virtual or hardware edge, an unprotected edge. The hub can be any Qumulo
  cluster. Both run Qumulo Core 7.10.3 or later.
  [Tested Qumulo Core versions](../reference/tested-versions.md) lists the builds the collection has passed against.
- **The admin password** of each cluster, or an access token for each. The playbooks connect as `admin` unless you
  say otherwise. Use the same kind for both clusters.
- **Network reach from the Ansible control host** to each cluster's REST API over HTTPS, on port 8000 unless the cluster
  answers on another.
- **The certificate of the CA that signed each cluster's TLS certificate**, in PEM. If the certificates chain to a CA
  the control host already trusts, you can leave this out. A cluster that still serves its factory certificate cannot be
  verified: see [The factory certificate](#the-factory-certificate) below before step 2.
- **A control host** that reaches Ansible Galaxy, with ansible-core 2.20 or later, `curl`, `jq`, GNU `sed`, and bash
  or zsh. The modules run on the control host and talk to the clusters over the REST API, so nothing is installed on
  a cluster.

### The factory certificate

A cluster that still serves its factory certificate has no CA that can verify it. Verification matters because the
collection sends the admin password. If `curl --cacert` in step 3 fails with "self-signed certificate", the cluster
serves its factory certificate. You have two ways on:

- Install a certificate your CA signed first. That changes the cluster, so it is not part of this tutorial.
  [Replace the factory certificate](../how-to/manage-certificates.md#replace-the-factory-certificate) shows how, but it
  writes a `host_vars` file, and step 6 refuses to replace one. Do it in a separate project, or delete the file once the
  certificate is installed and before step 6.
- For a lab cluster, set `qumulo_validate_certs: false` in step 3, delete the `qumulo_ca_path` line there, and use
  `curl -k` for the request that reads the UUID. The password then crosses the network unverified in every step that
  logs in, steps 4 to 7. The collection treats that as acceptable only for a lab cluster
  ([TLS](../explanation/authentication.md#tls)). Stop at step 7, and do not make the change in step 8 with
  verification off.

## 1. Install the collection

Install it from Ansible Galaxy:

```sh
ansible --version | head -n 1
ansible-galaxy collection install qumulo.cluster
ansible-galaxy collection list qumulo.cluster
```

```text
ansible [core 2.20.x]
...
Collection     Version
-------------- -------
qumulo.cluster 0.1.0
```

The first line shows your ansible-core version, 2.20 or later, in place of `2.20.x`. The install prints a line ending
`was installed successfully`. It installs the newest release, so the version you see may be later than the 0.1.0
shown.
[Install and pin the collection](../how-to/install-the-collection.md#pin-a-version-in-a-project) covers pinning a
version for a project you keep.

## 2. Gather what you know about your clusters

Make a project directory, and write down five facts about each cluster:

```sh
mkdir -p ~/qumulo-fleet/inventory/group_vars ~/qumulo-fleet/files
cd ~/qumulo-fleet
```

| Fact | Used for |
|---|---|
| The address the REST API answers at | `qumulo_api_host`. The cluster's certificate must name it. |
| The REST API's port | `qumulo_api_port`, 8000 unless the cluster answers elsewhere. |
| The admin password, or a token | The login. |
| The CA certificate | `files/qumulo-ca.pem` in the project directory. |
| The cluster's UUID | `qumulo_cluster_uuid`. Step 3 reads it. |

Where the facts come from depends on the kind of cluster:

| Kind of cluster | Address | Password |
|---|---|---|
| Virtual or hardware edge | The IP address of the node, or a floating IP if it has one | The one set when the edge was formed |
| CNQ cluster | A floating IP, or the address you reach the Web UI at; for a multi-AZ cluster in AWS, the load balancer's name. If your Terraform root publishes `qumulo_ansible_inventory`, its `api_host` | The admin password set when the cluster was deployed |
| Hardware cluster | A floating IP, or the IP address of a node | The admin password |

If your Terraform root publishes the `qumulo_ansible_inventory` output, [Use a Terraform root's output as the
inventory](../how-to/terraform.md) shows it and the plugin that reads it. This tutorial writes the hosts by hand.

Put the CA certificate in `files/qumulo-ca.pem`. Keep it outside `inventory/`, because Ansible reads every file in an
inventory directory as inventory.

Set the addresses and ports as shell variables. The commands in the rest of this tutorial read them, so run these in the
shell you keep using. Replace the values:

```sh
export HUB_ADDRESS=hub.example.com HUB_PORT=8000
export EDGE_ADDRESS=edge.example.com EDGE_PORT=8000
```

Then give the passwords to the environment, so that no file holds them. Type each password and press Enter; nothing is
shown as you type:

```sh
read -rs QUMULO_PASSWORD_HUB01 && export QUMULO_PASSWORD_HUB01
read -rs QUMULO_PASSWORD_EDGE01 && export QUMULO_PASSWORD_EDGE01
```

To use a token in place of a password, read each token into `QUMULO_TOKEN_HUB01` and `QUMULO_TOKEN_EDGE01` the same
way. Step 3 says what else to change. Keep these variables in the shell for the rest of the tutorial, because every
step that logs in reads them. Step 6 writes `host_vars` files and refuses to replace one that exists, so a vaulted
password cannot go there until afterwards.

## 3. Write the inventory

Read each cluster's UUID. `GET /v1/node/state` reports it as `cluster_id` and needs no login, and `--cacert` checks the
cluster's certificate against your CA:

```sh
hub_uuid=$(curl -fsS --cacert files/qumulo-ca.pem "https://$HUB_ADDRESS:$HUB_PORT/v1/node/state" | jq -r .cluster_id)
edge_uuid=$(curl -fsS --cacert files/qumulo-ca.pem "https://$EDGE_ADDRESS:$EDGE_PORT/v1/node/state" | jq -r .cluster_id)
echo "$hub_uuid" "$edge_uuid"
```

```text
11111111-aaaa-4bbb-8ccc-111111111111 22222222-dddd-4eee-8fff-222222222222
```

You get two different UUIDs of this form. If `curl` fails on the certificate, the cluster's certificate does not verify
against `files/qumulo-ca.pem`: fix that before you go on, or see [The factory certificate](#the-factory-certificate).
If the CA is one the control host already trusts, leave out `--cacert files/qumulo-ca.pem`.

Write the hosts file. Every cluster is a host in the `qumulo_clusters` group. The hub is also in `qumulo_hubs` and the
edge in `qumulo_edges`, the groups you limit a run with later. `hub01` and `edge01` are inventory names, so they need
not match the clusters' own names:

```sh
cat > inventory/hosts.yml <<EOF
all:
  children:
    qumulo_clusters:
      children:
        qumulo_hubs:
          hosts:
            hub01:
              qumulo_api_host: $HUB_ADDRESS
              qumulo_api_port: $HUB_PORT
              qumulo_cluster_uuid: $hub_uuid
        qumulo_edges:
          hosts:
            edge01:
              qumulo_api_host: $EDGE_ADDRESS
              qumulo_api_port: $EDGE_PORT
              qumulo_cluster_uuid: $edge_uuid
EOF
```

Before a module sends a password, it reads the cluster's UUID and fails when it is not `qumulo_cluster_uuid`
([The identity guard](../explanation/authentication.md#the-identity-guard)). That protects you from a wrong address.

If Terraform's provider formed a cluster, say so in its host entry: under `qumulo_cluster_uuid`, at the same indent,
add the line `qumulo_managed_by: terraform_provider`, and the line `qumulo_cluster_kind: edge`, or `baremetal` for
another on-premises cluster. The baseline then manages that cluster's floating addresses, and capture in step 6 reads
them. On a CNQ cluster, add `qumulo_cluster_kind: cnq` instead: Terraform keeps its floating addresses, and capture
leaves them out ([Terraform ownership](../explanation/terraform-ownership.md) says why). For a cluster Terraform did
not form, add nothing.

Then say how to connect. The modules talk to the REST API, so they run on the control host:

```sh
cat > inventory/group_vars/qumulo_clusters.yml <<'EOF'
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
qumulo_api_username: admin
qumulo_api_password: "{{ lookup('ansible.builtin.env', 'QUMULO_PASSWORD_' ~ inventory_hostname | upper) }}"
qumulo_validate_certs: true
qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
EOF
```

The password comes from `QUMULO_PASSWORD_HUB01` or `QUMULO_PASSWORD_EDGE01`, by host name, so it is never written to a
file. If the control host already trusts your CA, delete the `qumulo_ca_path` line. To use tokens, delete the
`qumulo_api_password` line and add this one in its place; set exactly one of the two:

```yaml
qumulo_api_token: "{{ lookup('ansible.builtin.env', 'QUMULO_TOKEN_' ~ inventory_hostname | upper) }}"
```

A token is bound to one cluster ([Password or token](../explanation/authentication.md#password-or-token)), which is why
each host reads its own. Check that Ansible reads the inventory as you meant it:

```sh
ansible-inventory -i inventory --graph
```

```text
@all:
  |--@ungrouped:
  |--@qumulo_clusters:
  |  |--@qumulo_hubs:
  |  |  |--hub01
  |  |--@qumulo_edges:
  |  |  |--edge01
```

## 4. Connect and read each cluster

A first run that only reads shows that the addresses, the certificates and the passwords work. Save this as
`read-clusters.yml`:

```sh
cat > read-clusters.yml <<'EOF'
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
          {{ first_run.cluster.cluster_name }} is {{ first_run.cluster.state }},
          Qumulo Core {{ first_run.cluster.version }},
          {{ first_run.cluster.product.cluster_type }},
          license {{ first_run.cluster.license.state }}
EOF
```

The `module_defaults` block is the header every playbook in the collection starts with. It hands the inventory's
connection variables to every module, so no task repeats them. The task uses
[cluster_info](../../plugins/modules/cluster_info.py), which sends only reads.

```sh
ansible-playbook -i inventory read-clusters.yml
```

Each host ends with `failed=0`, and the debug task prints one line for each cluster:

```text
ok: [hub01] => {
    "msg": "<hub's name> is ACTIVE, Qumulo Core <version>, <type>, license <state>"
}
ok: [edge01] => {
    "msg": "<edge's name> is ACTIVE, Qumulo Core <version>, <type>, license <state>"
}
```

What differs by kind of cluster is the last two items:

- `<type>` is `unprotected_edge` for your edge, and `protected_cluster` for a cluster that is not one. It reads
  `unknown` when the cluster's metrics do not say.
- `<state>` is `LICENSE_STATE_ACTIVE` for a licensed cluster. An unprotected edge may run without a license, and then it
  is `LICENSE_STATE_NONE`. [License an edge](../how-to/licensing.md) covers an edge that needs one.

Check that both versions are 7.10.3 or later. If the run fails, the message says what to fix:
`set exactly one of api_password or api_token` means this shell lacks a password variable from step 2, and
[Troubleshooting](../reference/troubleshooting.md#connecting) lists the other connection failures.

## 5. See whether the edge is already paired

The edge may already have a portal to the hub, and either cluster may already replicate a directory. Read both, so
that you know before anything is adopted. Save this as `read-fleet.yml` and run it:

```sh
cat > read-fleet.yml <<'EOF'
- name: Read the portals and the replication relationships
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
    - name: Read the portals
      qumulo.cluster.portal_info:
        gather_subset: [spokes, hubs]
      register: fleet_portals

    - name: Read the replication relationships
      qumulo.cluster.replication_info:
        gather_subset: [source, target]
      register: fleet_replication

    - name: Report what this cluster holds
      ansible.builtin.debug:
        msg:
          - "spoke portal: state={{ spokes | map(attribute='state') | join(',') }} status={{ spokes | map(attribute='status') | join(',') }} hub={{ spokes | map(attribute='hub_cluster_uuid') | join(',') }}"
          - "hub portal: state={{ hubs | map(attribute='state') | join(',') }} status={{ hubs | map(attribute='status') | join(',') }} spoke={{ hubs | map(attribute='spoke_cluster_uuid') | join(',') }}"
          - >-
            replication source: path={{ sources | map(attribute='source_path') | join(',') }}
            target_path={{ sources | map(attribute='target_path') | join(',') }}
            target_address={{ sources | map(attribute='target_address') | join(',') }}
            state={{ sources | map(attribute='state') | join(',') }}
            error={{ sources | map(attribute='error_from_last_job') | map('string') | join(',') }}
            id={{ sources | map(attribute='id') | join(',') }}
          - >-
            replication target: path={{ targets | map(attribute='target_path') | join(',') }}
            state={{ targets | map(attribute='state') | join(',') }}
            id={{ targets | map(attribute='id') | join(',') }}
      vars:
        spokes: "{{ fleet_portals.portals.spokes }}"
        hubs: "{{ fleet_portals.portals.hubs }}"
        sources: "{{ fleet_replication.replication.source_relationships }}"
        targets: "{{ fleet_replication.replication.target_relationships }}"
EOF
ansible-playbook -i inventory read-fleet.yml
```

The playbook runs [portal_info](../../plugins/modules/portal_info.py) and
[replication_info](../../plugins/modules/replication_info.py) on each cluster, and both only read. Each cluster lists
the records it holds, and an empty value means it holds none. A cluster can hold several, and the playbook joins them
with commas, so a hub that serves other edges lists their portals too.

If the edge is already paired with the hub, the edge shows a spoke portal and the hub shows a hub portal, both
`ACCEPTED` and `ACTIVE`, and each names the other cluster's UUID:

```text
ok: [hub01] => {
    "msg": [
        "spoke portal: state= status= hub=",
        "hub portal: state=ACCEPTED status=ACTIVE spoke=<the edge's UUID>",
        ...
ok: [edge01] => {
    "msg": [
        "spoke portal: state=ACCEPTED status=ACTIVE hub=<the hub's UUID>",
        "hub portal: state= status= spoke=",
        ...
```

Compare those UUIDs with `echo "$hub_uuid" "$edge_uuid"`. A portal to a different cluster also reads `ACCEPTED` and
`ACTIVE`, and capture in step 6 leaves out a portal whose peer is not an inventory host. If the edge is not paired,
every portal line is empty. Either way, step 6 adopts what the clusters hold.

## 6. Adopt both clusters

Each cluster is already running, so its settings are not yet in the inventory. The capture playbook,
[capture.yml](../../playbooks/capture.yml), reads both clusters once and writes what it finds into the project. It sends
only reads to the clusters. Run it for both hosts together, because a portal and a replication relationship name their
peer, and capture writes the addresses only a peer records when it reads the peer in the same run:

```sh
ansible-playbook qumulo.cluster.capture -i inventory -l qumulo_clusters -e qumulo_adopt=true
```

Both hosts end with `failed=0`. The `changed` count is the files and directories written on this host, and the clusters
are unchanged. List what it wrote:

```sh
ls inventory/host_vars
ls captures
```

```text
edge01.yml  hub01.yml
edge01  hub01
```

Your project now looks like this:

```text
qumulo-fleet/
  files/
    qumulo-ca.pem             the CA that signed your clusters' certificates
  inventory/
    hosts.yml                 the two hosts, their addresses, ports and UUIDs
    group_vars/
      qumulo_clusters.yml     the connection both clusters share
    host_vars/
      edge01.yml              what capture read from the edge
      hub01.yml               what capture read from the hub
  captures/                   the full capture of each cluster, outside the inventory
  read-clusters.yml           the playbook from step 4
  read-fleet.yml              the playbook from step 5
```

`captures/` sits beside `inventory/`, not inside it, for the same reason `files/` does. Adoption never rewrites a
`host_vars` file, so a second run fails; to repeat this step, delete `captures/` and `inventory/host_vars/` first.

Read what it wrote before you rely on it. Each `host_vars` file ends with comments that name each secret the inventory
must supply to manage what the secret guards, such as a local user's password or the cluster's TLS certificate and key,
and each value that was read but left out, with the reason:

```sh
tail -n 40 inventory/host_vars/edge01.yml
```

Nothing in this tutorial needs those secrets, and leaving them unset leaves that area unmanaged.
[What capture and clone read and write](../reference/capture-and-clone.md) lists every variable and every omission.
Pick out the lines that show what adoption decided:

```sh
grep -E '^(qumulo_cluster_name|qumulo_managed_by|qumulo_portal):|^  license:' inventory/host_vars/*.yml
```

```text
inventory/host_vars/edge01.yml:  license: !unsafe <none or required, or no line>
inventory/host_vars/edge01.yml:qumulo_cluster_name: !unsafe <the edge's name>
inventory/host_vars/edge01.yml:qumulo_managed_by: !unsafe none
inventory/host_vars/hub01.yml:  license: !unsafe <none or required, or no line>
inventory/host_vars/hub01.yml:qumulo_cluster_name: !unsafe <the hub's name>
inventory/host_vars/hub01.yml:qumulo_managed_by: !unsafe none
```

Your output depends on your clusters:

- `license: none` appears for an unprotected edge that holds no license. A cluster with an active license for its UUID
  gets `license: required`. A cluster with neither gets no `license` line: it holds no active license for its UUID, and
  the file's closing comments name `qumulo_capacity.license_document`.
- `qumulo_managed_by: none` appears unless you set `terraform_provider` in step 3.
- An edge that step 5 showed paired also has a `qumulo_portal:` line, and both clusters have `qumulo_portal_hosts`. An
  edge that is not paired has neither.

Each string in the files is tagged `!unsafe`, so Ansible reads a value taken from the cluster as written and never
treats it as a template. In a real project you would review these files and commit them now.
[Adopt a cluster you already run](../how-to/adopt-a-cluster.md) covers what adoption refuses, and
[Keep secrets out of the inventory](../how-to/store-secrets.md) covers the secrets.

## 7. Check that the inventory and the clusters agree

Before you let the collection change a cluster, check that it agrees with the cluster. A check run reads each cluster,
compares it with the inventory and reports what it would change, without changing anything:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l qumulo_clusters --check --diff
```

The run reads every area the inventory declares, so it takes a minute or more on each cluster.

[baseline.yml](../../playbooks/baseline.yml) runs the [cluster_baseline](../../roles/cluster_baseline/) role, which
brings a cluster to what its inventory declares, area by area. Each step reports `ok` when the cluster already matches
its variable, and `skipping` when the inventory leaves its variable unset. A step that would change a cluster reports
`changed` with a diff. The recap at the end reads, for each host:

```text
hub01   : ok=<n>  changed=0  unreachable=0  failed=0  skipped=<n>  ...
edge01  : ok=<n>  changed=0  unreachable=0  failed=0  skipped=<n>  ...
```

`changed=0` is the zero-diff gate ([the zero-diff gate](../explanation/state-management.md#the-zero-diff-gate) says
why): an inventory that shows no change against the cluster it came from is one you can trust to change it. The counts
of `ok` and `skipped` depend on what each cluster holds.

The license step may print a message, and that is not a change:

- An edge that runs without a license prints `edge01: runs without a license.`
- A cluster that holds an active license has nothing to hand off.
- A cluster with no `license` line in step 6 prints `<host>: The cluster holds no license for its UUID, ...`. That is a
  hand-off, not a change. See [License an edge](../how-to/licensing.md).

If any step shows a change, or the run stops, do not go on to step 8. Read the diff or the message from the top: the
inventory was edited after the capture, the cluster changed since it, or the collection cannot compare a value in the
form the cluster reads back. [Respond to a refusal](../how-to/respond-to-a-refusal.md) and
[Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree) cover the common causes. See
[Check and diff](../explanation/state-management.md#check-and-diff) for how a check run works.

## 8. Make one small change

This is the first step that changes a cluster. To stop here, skip it. Skip it too if you set
`qumulo_validate_certs: false` in step 3.

You create one empty directory on the edge, `/ansible-tutorial-local`. A directory is a small change: it holds no data,
nothing refers to it, and the baseline can remove it again. The path must not exist on the edge yet and must lie outside
the roots of any portal.

Capture wrote a `qumulo_directories` list for the edge, and writes an empty one as `qumulo_directories: []`. Make sure
the line is the plain key, then insert the directory as the list's first item, which keeps the YAML valid. The second
command uses GNU `sed`; on another system, add the line by hand:

```sh
sed -i 's/^qumulo_directories: \[\]$/qumulo_directories:/' inventory/host_vars/edge01.yml
sed -i '/^qumulo_directories:$/a - path: /ansible-tutorial-local' inventory/host_vars/edge01.yml
grep -c '^- path: /ansible-tutorial-local$' inventory/host_vars/edge01.yml
```

The last command prints `1`. If it prints anything else, the file is not what capture wrote: restore it by deleting
`captures/` and `inventory/host_vars/` and running step 6 again. Look at the result:

```sh
grep -A1 '^qumulo_directories:$' inventory/host_vars/edge01.yml
```

```text
qumulo_directories:
- path: /ansible-tutorial-local
```

The entry names only a path, so the directory gets the cluster's default mode, owner and group, and a path that already
exists keeps what it has ([directory](../../plugins/modules/directory.py) leaves unset options alone).

Check first, on the edge only:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
```

One step reports `changed`, the directory, with a diff that shows it being created, and nothing else does. The loop
prints each directory in the list, so the new one reads `changed` and the others `ok`:

```text
changed: [edge01] => (item=/ansible-tutorial-local)
```

The recap shows `changed=1`. If the new item reads `ok` and the recap shows `changed=0`, the directory exists already:
remove the line you added, and choose another name. If the diff shows an existing directory being modified, or any other
step changes, stop and choose another path. Every module reads before it writes, and a check run stops before the write.
When you are ready, apply the change, and run again:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01
```

The first run creates the directory and ends with `changed=1`. The second ends with `changed=0`: each module writes only
what differs, so a run against a cluster that already matches its inventory changes nothing.

**To undo it**, set the entry's `state` to `absent` and leave it in the list. Deleting the line removes nothing, because
the baseline acts only on the entries it finds:

```yaml
qumulo_directories:
- path: /ansible-tutorial-local
  state: absent
```

Run the edge's baseline with `--check --diff`, then without `--check`. The module removes only an empty directory and
fails on one that holds anything, so it cannot remove data.

From now on you change a cluster by changing its inventory and running the baseline, with `--check --diff` first.

## 9. Run the baseline again

Run the baseline on both clusters, with nothing changed since the last run, first as a check:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l qumulo_clusters --check --diff
```

Each module writes only what differs, so a cluster that already matches its inventory changes nothing. Both hosts show
`changed=0`:

```text
hub01   : ok=<n>  changed=0  unreachable=0  failed=0  ...
edge01  : ok=<n>  changed=0  unreachable=0  failed=0  ...
```

Your inventory now describes a fleet, and the baseline holds both of its clusters to it. If you stopped earlier, the
inventory describes the part you brought under control.

## Pair and replicate

This tutorial stops short of two changes that reach across clusters. Pairing an edge with a hub makes both clusters
unavailable briefly, and a replication relationship makes its target directory read-only. Each also needs the clusters
to reach each other at addresses only you know: portals use port 3713 in both directions, and replication uses port 3712
from the edge to the hub. Do them on clusters that are not serving users, or at a quiet time.

Your project is ready for both. If the edge was already paired, capture adopted its `qumulo_portal` and
`qumulo_portal_hosts`, and the baseline you just ran holds them. To pair it, or to replicate a directory to the hub,
follow [Pair an edge with its hub through a portal](../how-to/pair-edge-with-hub.md) and
[Replicate a directory to another cluster](../how-to/replicate-a-directory.md). Each ends with a way to remove what it
made.

## What you learned

- The collection runs on the control host and reaches each cluster over its REST API. It needs an address, a port, a
  login, a CA and the cluster's UUID, which the identity guard checks before it sends a password.
- A first run can only read: [cluster_info](../../plugins/modules/cluster_info.py),
  [portal_info](../../plugins/modules/portal_info.py) and [replication_info](../../plugins/modules/replication_info.py)
  change nothing, and neither does capture.
- Adoption turns what a cluster holds into inventory, one `host_vars` file for each cluster, and the zero-diff gate
  shows whether the two agree. You change a cluster only after the gate passes, and by changing the inventory.
- A check run with `--diff` tells you what a real run would change, and a second real run changes nothing.
- A hub and its edge are two hosts. Capture reads both in one run, so that a portal or a relationship names its peer,
  and the gate passes on both before either changes.

## Where to go next

- [Set up an inventory for your clusters](../how-to/set-up-inventory.md) and
  [Keep secrets out of the inventory](../how-to/store-secrets.md) turn this project into one you keep and commit.
- [Pair an edge with its hub through a portal](../how-to/pair-edge-with-hub.md) covers the address choices for a real
  fleet, the order the two clusters go in, and how to remove a portal.
- [Replicate a directory to another cluster](../how-to/replicate-a-directory.md) covers the other side of a
  relationship: consent, releases and removal.
- [Use a Terraform root's output as the inventory](../how-to/terraform.md) reads CNQ clusters from Terraform in place of
  `hosts.yml`.
- [License an edge](../how-to/licensing.md) and [Upgrade a fleet](../how-to/upgrade.md) cover two tasks a fleet needs.
- To see why the collection treats a hub and an edge as it does, read
  [Edges and portals](../explanation/edges-and-portals.md), [Replication](../explanation/replication.md),
  [State management](../explanation/state-management.md) and [Architecture](../explanation/architecture.md).

A message you do not recognise is in [Troubleshooting](../reference/troubleshooting.md).
