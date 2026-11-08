<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Your first cluster

In this tutorial you bring one Qumulo cluster that you already run under the control of an inventory. You install the
collection, connect to the cluster and read it, capture what it holds into the inventory, and check that the inventory
and the cluster agree. If you choose to, you then make one small change by changing the inventory, and undo it.

It works on an edge (virtual or hardware), a Cloud Native Qumulo (CNQ) cluster or a hardware cluster. The steps are the
same for all three. Where one differs, the step says so.

The order protects the cluster. Steps 1 to 7 change nothing on it. You decide at step 8 whether to change anything, and
step 9 undoes what step 8 did.

| Step | What reaches the cluster |
|---|---|
| 1. Install the collection | Nothing. |
| 2. Describe the cluster | One request that needs no login, for the cluster's UUID. |
| 3. Read the cluster | Reads that need no login, then a login and reads. No setting changes. |
| 4. Meet the identity guard | Reads that need no login. The run stops before it sends a credential. |
| 5. Capture the configuration | Reads. The capture is a file on your control host. |
| 6. Adopt the configuration | Reads. The result is a file in your inventory. |
| 7. Check that the inventory and the cluster agree | Reads. A check run writes nothing. |
| 8. Make one change (optional) | Creates one empty directory and one SMB share. |
| 9. Undo the change | Removes them. |

## Before you start

You need:

- a Qumulo cluster that is already formed and that you can reach: an edge, a CNQ cluster or a hardware cluster. This
  tutorial does not form one;
- Qumulo Core 7.10.3 or later on the cluster. [Tested Qumulo Core versions](../reference/tested-versions.md) lists the
  builds the collection has passed against. On an older release the baseline in step 7 warns and goes on;
- the cluster's address (a name or an IP address that serves its REST API) and the port, 8000 unless you changed it;
- the password of the cluster's `admin` user, or an access token that the cluster issued. A user other than `admin`
  works only if its role grants every privilege the calls need, and a missing one fails with HTTP 403;
- a control host that reaches the address and port over HTTPS, reaches Ansible Galaxy, and has ansible-core 2.20 or
  later and `curl`. The modules need no Python package beyond what ansible-core installs;
- the certificate of the CA that signed the cluster's TLS certificate, as a file, or that CA already trusted by the
  control host. The cluster's certificate must name the address you use, as a DNS name or an IP address in its subject
  alternative names, or verification fails. If the cluster still serves its factory certificate, read
  [The factory certificate](#the-factory-certificate) below before step 2.

Check the control host:

```sh
ansible --version | head -n 1
```

The output starts `ansible [core 2.20` or a later version.

### Where the cluster types differ

- A cluster that the Terraform provider formed, CNQ or hardware, gets its address and UUID from your Terraform root's
  outputs. If the root publishes the `qumulo_ansible_inventory` output, they are its `api_host` and `cluster_uuid`
  fields. Step 2 also asks you to tell the collection what Terraform formed and owns.
  [Use a Terraform root's output as the inventory](../how-to/terraform.md) shows how to read the cluster straight from
  that output instead of writing a host by hand. The rest of this tutorial is the same with the host name the output
  gives.
- A cluster that Terraform did not form, such as an edge from the installer or a hardware cluster you set up by hand,
  is a host you write by hand, as step 2 does.
- An edge may run without a license. A hardware or CNQ cluster normally holds one. Step 3 shows which yours is, and
  step 7 reacts to it.

### The factory certificate

A cluster that still serves its factory certificate has no CA that can verify it. Verification matters because the
collection sends the admin password. If `curl --cacert` in step 2 fails with "self-signed certificate", the cluster
serves its factory certificate. You have two ways on:

- Install a certificate your CA signed first, as
  [Replace the factory certificate](../how-to/manage-certificates.md#replace-the-factory-certificate) shows. That
  changes the cluster, so it is not part of this tutorial.
- For a lab cluster, set `qumulo_validate_certs: false` in step 2, and use `curl -k` for the one request that reads the
  UUID. The password then crosses the network unverified in every step that logs in, steps 3 to 7. The collection
  treats that as acceptable only for a lab cluster ([TLS](../explanation/authentication.md#tls)). Stop at step 7, and
  do not make the change in step 8 with verification off.

## 1. Install the collection

Install it from Ansible Galaxy:

```sh
ansible-galaxy collection install qumulo.cluster
```

The last line of the output reads:

```text
qumulo.cluster:0.1.0 was installed successfully
```

Check that Ansible finds it:

```sh
ansible-galaxy collection list qumulo.cluster
ansible-doc -l qumulo.cluster | head -n 3
```

```text
# /home/you/.ansible/collections/ansible_collections
Collection     Version
-------------- -------
qumulo.cluster 0.1.0
```

```text
qumulo.cluster.ad_domain                Join a Qumulo cluster to an Active ...
qumulo.cluster.audit_destination        Manage a Qumulo cluster's audit log...
qumulo.cluster.auth_cache_clear         Clear a Qumulo cluster's cache of i...
```

The path on the first line of the first output depends on your `collections_path`. This installs the newest
release, so the version you see may be later than the 0.1.0 shown. For a project you keep, pin a version:
[Install and pin the collection](../how-to/install-the-collection.md#pin-a-version-in-a-project) shows how.

## 2. Describe the cluster

Make a project directory. Everything that is not inventory stays outside `inventory/`, because given a directory,
Ansible tries to read each file in it as an inventory.

```sh
mkdir -p qumulo-project/files qumulo-project/inventory/group_vars && cd qumulo-project
```

By the end of the tutorial it holds:

```text
qumulo-project/
  files/
    qumulo-ca.pem             the CA that signed the cluster's certificate
  inventory/
    hosts.yml                 the cluster, its address and its UUID
    group_vars/
      qumulo_clusters.yml     how to connect
    host_vars/
      cluster01.yml           written by adoption in step 6
  captures/                   written in step 5
  first-run.yml               the playbook in step 3
```

Put the CA certificate in `files/qumulo-ca.pem`. If the control host already trusts the CA, skip this, drop the
`--cacert` option below, and delete the `qumulo_ca_path` line from the connection file.

Say where the cluster is, with your own address and port:

```sh
export QUMULO_CLUSTER_ADDRESS=10.0.0.10
export QUMULO_CLUSTER_PORT=8000
```

Read the cluster's UUID. `GET /v1/node/state` needs no login, and `--cacert` checks the cluster's certificate against
your CA:

```sh
curl --cacert files/qumulo-ca.pem "https://$QUMULO_CLUSTER_ADDRESS:$QUMULO_CLUSTER_PORT/v1/node/state"
```

The answer is JSON that includes the node's `state` and the cluster's UUID as `cluster_id`:

```text
{"state": "ACTIVE", "cluster_id": "6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11", ...}
```

A node that belongs to a cluster reports a `cluster_id`. A node that reports `UNCONFIGURED` has an empty one and belongs
to no cluster. Point `QUMULO_CLUSTER_ADDRESS` at a node of your formed cluster. Copy your `cluster_id` into a
variable:

```sh
export QUMULO_CLUSTER_UUID=6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
```

Name the cluster in `hosts.yml`. Every cluster the collection manages is a host in the `qumulo_clusters` group:

```sh
cat > inventory/hosts.yml <<EOF
all:
  children:
    qumulo_clusters:
      hosts:
        cluster01:
          qumulo_api_host: $QUMULO_CLUSTER_ADDRESS
          qumulo_api_port: $QUMULO_CLUSTER_PORT
          qumulo_cluster_uuid: $QUMULO_CLUSTER_UUID
EOF
```

If Terraform formed the cluster, add two more lines under `cluster01`, at the same indent, so that the collection
leaves what Terraform owns to Terraform. Copy the values of the output's `managed_by` and `owned_areas`:

```yaml
          qumulo_managed_by: terraform_provider
          qumulo_terraform_owned_areas: [network.floating_ips]
```

[Terraform ownership](../explanation/terraform-ownership.md) says why. Without the lines, adoption writes
`qumulo_managed_by: none` and an empty `qumulo_terraform_owned_areas`.

Every module reads the cluster's UUID before it sends a password, and stops if it is not `qumulo_cluster_uuid`
([The identity guard](../explanation/authentication.md#the-identity-guard) explains why). Without a UUID the check is
off and the modules warn.

Then say how to connect. The modules talk to the REST API, so they run on your control host. This file also reads the
password from the environment, so that it is never written into the project:

```sh
cat > inventory/group_vars/qumulo_clusters.yml <<'EOF'
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
qumulo_api_username: admin
qumulo_api_password: "{{ lookup('ansible.builtin.env', 'QUMULO_ADMIN_PASSWORD') }}"
qumulo_validate_certs: true
qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
EOF
```

- To use an access token, delete the `qumulo_api_password` line and add
  `qumulo_api_token: "{{ lookup('ansible.builtin.env', 'QUMULO_API_TOKEN') }}"`. Set exactly one of the two, and
  read the token into the environment as the next paragraph does for the password:
  `read -rsp 'API token: ' QUMULO_API_TOKEN && echo && export QUMULO_API_TOKEN`. A token is bound to one cluster
  ([Password or token](../explanation/authentication.md#password-or-token)).
- With the factory certificate, set `qumulo_validate_certs: false` and delete the `qumulo_ca_path` line.

Read the password into the environment, hidden, in the shell you keep using. Keep it there for the rest of the
tutorial, because steps 7 to 9 log in through it too. Adoption in step 6 writes `host_vars/cluster01.yml` and refuses to
replace a file that exists, so a vaulted password cannot go there until afterwards.

```sh
read -rsp 'admin password: ' QUMULO_ADMIN_PASSWORD && echo && export QUMULO_ADMIN_PASSWORD
```

For a project you keep, [Keep secrets out of the inventory](../how-to/store-secrets.md) moves it into Ansible Vault or
a secret store afterwards.

## 3. Read the cluster

A first run that only reads shows that the address, the certificate and the password work. Save this as
`first-run.yml`:

```sh
cat > first-run.yml <<'EOF'
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
    - name: Read the cluster's identity, version, license, capacity and nodes
      qumulo.cluster.cluster_info:
        gather_subset: [node, version, settings, license, capacity, product, nodes]
      register: first_run

    - name: Show what it read
      ansible.builtin.debug:
        msg: >-
          {{ first_run.cluster.cluster_name }} is {{ first_run.cluster.state }},
          Qumulo Core {{ first_run.cluster.version }},
          {{ first_run.cluster.product.cluster_type | default('not offered') }},
          {{ first_run.cluster.node_count | default('not offered') }} node(s),
          license {{ first_run.cluster.license.state | default('not offered') }}

    - name: Read the spoke portals' roots
      qumulo.cluster.portal_info:
        gather_subset: [spokes]
      register: first_run_portals

    - name: Show the spoke portals' roots
      ansible.builtin.debug:
        msg: >-
          Spoke roots on this cluster:
          {{ first_run_portals.portals.spokes | default([]) | map(attribute='roots') | flatten
             | map(attribute='spoke_root') | list }}
EOF
```

The `module_defaults` block is the header every playbook in the collection starts with. It hands the inventory's
connection variables to every module, so no task repeats them. The tasks use
[cluster_info](../../plugins/modules/cluster_info.py), which reads the cluster's identity and settings, and
[portal_info](../../plugins/modules/portal_info.py), which reads the roots of the cluster's spoke portals. Both send
only `GET` requests after their login. Step 8 uses the roots. Run it:

```sh
ansible-playbook -i inventory first-run.yml
```

The output reads like this, with your cluster's values:

```text
TASK [Read the cluster's identity, version, license, capacity and nodes] *******
ok: [cluster01]

TASK [Show what it read] *******************************************************
ok: [cluster01] => {
    "msg": "prod-nas is ACTIVE, Qumulo Core 7.10.3, protected_cluster, 4 node(s), license LICENSE_STATE_ACTIVE"
}

TASK [Read the spoke portals' roots] *******************************************
ok: [cluster01]

TASK [Show the spoke portals' roots] *******************************************
ok: [cluster01] => {
    "msg": "Spoke roots on this cluster: []"
}

PLAY RECAP *********************************************************************
cluster01                  : ok=4    changed=0    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
```

What differs by cluster:

- The product type is `unprotected_edge` for an unprotected edge and `protected_cluster` otherwise. It reads
  `unknown` if the cluster's metrics do not carry it.
- The license state is `LICENSE_STATE_ACTIVE` for a cluster that holds a license for its UUID. An unprotected edge may
  run without one, as `LICENSE_STATE_NONE`. [License an edge](../how-to/licensing.md) covers an edge that needs one;
  [Licensing and capacity](../explanation/licensing-and-capacity.md) covers a hardware or CNQ cluster.
- The node count is the number of nodes your cluster has.
- A subset the cluster does not offer reads `not offered`.
- The spoke roots are empty unless this cluster is an edge paired with a hub. Note them: step 8 needs them.

If the run fails, the message says what to fix. These four are the common ones:

- `set exactly one of api_password or api_token`: this shell has no `QUMULO_ADMIN_PASSWORD` (or `QUMULO_API_TOKEN`).
  Export it again.
- `TLS certificate verification failed`: `qumulo_ca_path` does not name the CA that signed the cluster's certificate.
- `HTTP 401 ... (check api_username and api_password)`: the cluster refused the password.
- `identity guard: ... expected '...'; no credentials were sent`: the address answers with another UUID than the
  inventory names. Step 4 shows this on purpose.

[Troubleshooting](../reference/troubleshooting.md#connecting) lists the others.

## 4. Meet the identity guard

The collection stops a run on purpose when it cannot be sure a step is safe. The refusal you can meet without risk is
the wrong cluster. Run the same playbook with a UUID that is not your cluster's:

```sh
ansible-playbook -i inventory first-run.yml -e qumulo_cluster_uuid=00000000-0000-4000-8000-000000000000
```

The first task fails, and the recap shows `failed=1`. The `msg` of the failure reads:

```text
fatal: [cluster01]: FAILED! => {"changed": false, "msg": "identity guard: https://10.0.0.10:8000 is cluster '6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11' (state ACTIVE), expected '00000000-0000-4000-8000-000000000000'; no credentials were sent"}
```

Only reads that need no login reached the cluster. The collection sent no password, because the address answered with
a different UUID than the inventory names. The same stop protects you if an address is later
repointed to another cluster. The `-e` value applied to this one run, so the playbook succeeds again without it.

Every refusal comes before the write it guards and says what would lift it; no flag lifts this one.
[Refusals and guards](../explanation/refusals-and-guards.md) explains why the collection refuses instead of guessing,
and [Respond to a refusal](../how-to/respond-to-a-refusal.md) covers the other kinds.

## 5. Capture the configuration

The inventory is the source of truth for a cluster, and a cluster you already run holds settings the inventory does not
know yet. The capture playbook, [capture.yml](../../playbooks/capture.yml), reads everything the collection configures
and writes it as inventory variables. It changes nothing on the cluster. Run it without adoption first, so that you read
the result before it goes into the inventory:

```sh
ansible-playbook qumulo.cluster.capture -i inventory -l cluster01
```

It ends with a recap that shows `failed=0`. Its `changed` count is the files and directories it wrote on your control
host. List the variables it captured:

```sh
grep -E '^qumulo_[a-z0-9_]+:' captures/cluster01/host_vars.captured.yml | cut -d: -f1
```

```text
qumulo_audit_destinations
qumulo_cluster_name
qumulo_cluster_uuid
qumulo_directories
qumulo_dns
qumulo_factory_defaults
...
```

The file lists the variables in alphabetical order, and the list is longer than this. It differs by cluster, and a
variable appears only when the cluster has that setting: a
cluster joined to Active Directory has `qumulo_ad`, and the parts of a cluster that Terraform owns are left out. An
edge paired with a hub gets `qumulo_portal` only when the hub is also a host in the inventory, and this inventory has
one host. The portal then shows in the file's comments as "the spoke portal to hub ...", which is why step 3 printed
the roots.
[What capture and clone read and write](../reference/capture-and-clone.md) lists every variable and when it is written.

The file ends with comments. Read them:

```sh
tail -n 25 captures/cluster01/host_vars.captured.yml
```

They name each secret you would have to supply to manage what it guards, such as the passwords of local users, and each
value that was read but left out, with the reason. No secret is ever written to the file: the cluster returns none but
the keytab's keys, which capture reads only to name the keytab's principals.

## 6. Adopt the configuration

Adoption writes the capture into the inventory once, as the `host_vars` file for the host. It is the same playbook with
one more variable. It still only reads the cluster:

```sh
ansible-playbook qumulo.cluster.capture -i inventory -l cluster01 -e qumulo_adopt=true
```

List what exists now:

```sh
find captures inventory/host_vars -type f
```

```text
captures/cluster01/host_vars.captured.yml
inventory/host_vars/cluster01.yml
```

`captures/cluster01/host_vars.captured.yml` is the whole capture. `inventory/host_vars/cluster01.yml` holds every
captured variable the inventory did not already define, which is all of them except the UUID you wrote in `hosts.yml`.
Adoption never rewrites a `host_vars` file, so a second adoption fails; delete `inventory/host_vars/cluster01.yml`
before you repeat this step.

Look at what it wrote about the cluster's name and ownership:

```sh
grep -E '^qumulo_(cluster_name|managed_by|terraform_owned_areas):' inventory/host_vars/cluster01.yml
```

```text
qumulo_cluster_name: !unsafe prod-nas
qumulo_managed_by: !unsafe none
qumulo_terraform_owned_areas: []
```

Each string in the file is tagged `!unsafe`, so Ansible reads a value taken from the cluster as written and never
treats it as a template. The last two lines declare that no Terraform provider owns any area. Adoption writes them
only when the inventory says nothing about ownership. If you added `qumulo_managed_by: terraform_provider` in step 2,
they are not in the file, and the areas Terraform owns are left out of the capture.

Then look at the license marker:

```sh
grep -A4 '^qumulo_capacity:' inventory/host_vars/cluster01.yml
```

On a cluster that holds a license for its UUID, one of the lines is `license: !unsafe required`. On an unprotected edge
without a license it is `license: !unsafe none`. On any other cluster without a license there is no `license` line, and
the file's comments name the license document to supply. If the file has no `qumulo_capacity`, the command prints
nothing.

In a real project you would review `host_vars/cluster01.yml` now and commit it.
[Adopt a cluster you already run](../how-to/adopt-a-cluster.md) covers adopting your own clusters.

## 7. Check that the inventory and the cluster agree

Before you let the collection change the cluster, check that it agrees with it. A check run reads the cluster, compares
it with the inventory and reports what it would change, without changing anything:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01 --check --diff
```

It makes many read requests, so it can take a few minutes on a large cluster.

[baseline.yml](../../playbooks/baseline.yml) runs the [cluster_baseline](../../roles/cluster_baseline/) role, which
brings a cluster to what its inventory declares, area by area. In a check run, each step reports:

- `ok` when the cluster already matches its variable;
- `skipping` when the inventory leaves the variable unset, because an unset variable leaves its area alone;
- `changed`, with a diff, when the step would change the cluster.

The recap at the end reads:

```text
PLAY RECAP *********************************************************************
cluster01                  : ok=<n>   changed=0    unreachable=0    failed=0    skipped=<n>   rescued=0    ignored=0
```

`changed=0` is the zero-diff gate ([the zero-diff gate](../explanation/state-management.md#the-zero-diff-gate) says
why): an inventory that shows no change against the cluster it came from is one you can trust to change it. The counts
of `ok` and `skipped` depend on what your cluster holds. See
[Check and diff](../explanation/state-management.md#check-and-diff) for how a check run works.

One step prints a message that is not a change, the license step. It runs when the file declares `qumulo_capacity`:

- An unprotected edge adopted with `license: none` prints `cluster01: runs without a license.`
- A cluster that holds its license has nothing to hand off, and the step reports `ok`.
- A cluster that holds no license for its UUID prints a message that says how to get one. A check run applies nothing.
  [Licensing and capacity](../explanation/licensing-and-capacity.md) explains it.

If a step shows `changed`, stop and read its diff. The cluster has changed since the capture, or the module cannot
compare the value in the form the cluster reads back. Fix the inventory and check again.
[Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree) lists the messages.

This is the end of the part that cannot change your cluster. You can stop here with a captured, checked inventory.

## 8. Make one change (optional)

Go on only if you want to change the cluster. If you set `qumulo_validate_certs: false`, stop here: replace the
factory certificate first, as
[Replace the factory certificate](../how-to/manage-certificates.md#replace-the-factory-certificate) shows, and set `qumulo_ca_path`. The change is small and easy to undo: it creates one empty directory,
`/qumulo-tutorial`, and one SMB share on it. Only the local `admin` user can read the share. Removing the share leaves
the directory and its contents in place, and the directory can be removed while it is empty.

First make sure the names are free on your cluster:

- No share `qumulo-tutorial` exists. This command prints nothing when none does:

  ```sh
  grep -n 'share_name: !unsafe qumulo-tutorial' inventory/host_vars/cluster01.yml
  ```

- No directory `/qumulo-tutorial` exists. Adoption lists only the directories that something uses, so the file cannot
  tell you. The check below can: a new directory shows an empty `before` side in its diff, and an existing one shows
  the values it holds now. If the directory's diff shows values before, remove the items and stop.
- The path lies outside every spoke root that step 3 printed, because the data in a spoke root belongs to the hub. If
  `/qumulo-tutorial` lies under one, stop at step 7.

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

Open `inventory/host_vars/cluster01.yml`. Adoption already wrote `qumulo_directories` there, with an item for each
directory that a share, export, quota, snapshot policy or bucket uses, and it wrote the cluster's shares under its
tenant, in `qumulo_tenants`: each tenant is an entry, and a tenant's shares are its `smb_shares`. Every cluster has a
tenant, and a cluster that has only one has one entry, usually named `Default`. Add the directory item below to
`qumulo_directories`, and the share item to the `smb_shares` list of the entry of the cluster's tenant. If the cluster
has several tenants, use the entry of a tenant with `smb_enabled: true`. The flat `qumulo_smb_shares` list holds only
shares that several tenants share, so on a cluster with one tenant it is `[]`: leave it alone either way. Do not add a second `qumulo_directories:` key,
or a second `smb_shares:` key in the same entry, because the second one would silently replace the first. If a list is
written as `[]`, replace the `[]` with the item; if an entry has no `smb_shares:` key, add one.

```yaml
# in qumulo_directories
- path: /qumulo-tutorial
  mode: '0750'

# in the smb_shares of the cluster's tenant entry, under qumulo_tenants
- share_name: qumulo-tutorial
  fs_path: /qumulo-tutorial
  description: Created by the collection tutorial
  permissions:
    - trustee: local:admin
      rights: [READ]
```

```yaml
# inventory/host_vars/cluster01.yml, in part: where the share goes
qumulo_tenants:
  - name: Default
    smb_shares:
      # keep the items adoption wrote, the Files share among them, as they are
      - share_name: qumulo-tutorial
        fs_path: /qumulo-tutorial
        description: Created by the collection tutorial
        permissions:
          - trustee: local:admin
            rights: [READ]
```

The items use [directory](../../plugins/modules/directory.py) and [smb_share](../../plugins/modules/smb_share.py). Two
rules matter here:

- The mode is quoted because YAML reads an unquoted `0750` as a decimal number, and the collection refuses it.
- A trustee is written as `local:admin`, `local:svc-backup`, `ad:CORP\Engineering` or `Everyone`. A bare user name is
  refused.

A list adds to what the cluster has: the directories and shares you leave out stay as they are
([Lists add, dictionaries set the keys they give](../explanation/state-management.md#lists-add-dictionaries-set-the-keys-they-give)
explains the rule).

Check first:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01 --check --diff
```

Two steps report `changed`, each with its diff, and nothing else does. Each loop also prints the items adoption
captured, as `ok`, and which ones depends on your cluster:

```text
changed: [cluster01] => (item=/qumulo-tutorial)
changed: [cluster01] => (item=qumulo-tutorial (Default))
```

The share's label gives the name of the tenant it is in, in brackets: `Default` here, or your tenant's name.

The recap shows `changed=2`. If it shows more, do not apply. Something else on the cluster differs from the inventory.
Read that diff, and stop here. Nothing has been sent: a check run stops before the write.

If the check shows exactly the two changes you expect, apply it, and run again:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01
```

The first run creates the directory and the share and ends with `changed=2`. The second ends with `changed=0`: each
module reads before it writes and writes only what differs, so a run against a cluster that matches its inventory
changes nothing.

From now on you change the cluster by changing its inventory and running the baseline, with `--check --diff` first.
Before a change to a directory that holds data,
[Take a snapshot before a change](../how-to/snapshot-before-a-change.md) shows how to take one.

## 9. Undo the change

Deleting the items from the inventory does not undo the change. A list adds to what the cluster has and never removes
anything, so the share would stay. To remove an object, declare it with `state: absent`.

The baseline applies directories before shares. Remove the share first, in its own run, so that the share never points
at a missing directory. In `inventory/host_vars/cluster01.yml`, replace the share's item in the tenant entry's `smb_shares` with:

```yaml
- share_name: qumulo-tutorial
  state: absent
```

Check, then apply:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01 --check --diff
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01
```

The check shows one `changed`, for the share. Then replace the directory's item with:

```yaml
- path: /qumulo-tutorial
  state: absent
```

Check and apply the same way. The module removes a directory only if it is empty and fails on one that holds anything,
so it cannot remove data. Run the check once more:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01 --check --diff
```

It ends with `changed=0`, the same as at the gate in step 7. The two `state: absent` items can stay in the inventory,
where they keep the objects absent, or you can delete them: removing an item never creates or deletes anything.

## What you learned

- The collection runs on your control host and talks to the cluster's REST API. Every module reads the cluster's UUID
  before it sends a credential and stops on the wrong cluster, so a wrong address sends nothing.
- `cluster_info` reads a cluster without changing it, and `capture.yml` reads everything the collection configures.
  Adoption writes that once into the inventory, which is the source of truth from then on.
- An unset variable leaves its area alone, so an inventory can describe only part of a cluster.
- A check run with `--diff` shows what a run would change, and `changed=0` right after adoption is the gate that says
  the inventory can be trusted to change the cluster.
- A change is a change to the inventory, checked first and applied by the baseline. A list adds, and `state: absent`
  removes.

## Next

- [Your first fleet](first-fleet.md) adopts two clusters, a hub and an edge, into an inventory of two hosts.
- [Install and pin the collection](../how-to/install-the-collection.md),
  [Set up an inventory for your clusters](../how-to/set-up-inventory.md) and
  [Keep secrets out of the inventory](../how-to/store-secrets.md) set up your own project.
- [Adopt a cluster you already run](../how-to/adopt-a-cluster.md) adopts the clusters you keep, and
  [Use a Terraform root's output as the inventory](../how-to/terraform.md) reads CNQ clusters from Terraform.
- [State management](../explanation/state-management.md) explains the model behind the check run and the gate.
  [Authentication, trust and secrets](../explanation/authentication.md) and
  [Refusals and guards](../explanation/refusals-and-guards.md) explain why the collection refuses.
