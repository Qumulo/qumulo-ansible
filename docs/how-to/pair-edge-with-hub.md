<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Pair an edge with its hub through a portal

Declare a portal from an edge to its hub in the inventory, apply it in the right order, check that both clusters show it,
and remove it.
When you finish, the edge links the hub directories you named as roots, and both sides report the portal ACCEPTED and
ACTIVE.
A portal is what lets an edge cache a hub's data ([the model](../explanation/edges-and-portals.md)).

## Before you start

- Put the edge and the hub in the inventory, each with `qumulo_api_host` and `qumulo_cluster_uuid`, and bring both to a
  state where the baseline runs against them:
  [Set up an inventory for your clusters](set-up-inventory.md), or the walk-through in
  [Your first fleet](../tutorials/first-fleet.md).
- Check that both clusters run the same Qumulo Core version.
  The `portal_pair` role refuses to pair clusters on different versions.
- Choose the root pairs.
  Each `hub_root` is a directory on the hub. Step 3 creates it if it doesn't exist yet.
  Each `spoke_root` is a path that does not exist on the edge, inside a parent that does.
  The portal creates it.
- Put the hub in the `qumulo_hubs` group and the edge in `qumulo_edges`.
  Steps 3 and 4 select them by group, and an edge rebuild finds the hub in `qumulo_hubs`.
  The Terraform inventory plugin never puts a host in `qumulo_hubs`:
  [Put a host in `qumulo_hubs`](terraform.md#put-a-host-in-qumulo_hubs).
- Have no quota, snapshot policy or S3 bucket declared under a spoke root.
  The cluster refuses quotas and snapshot policies there ([G-SPOKE-PATHS](../reference/gaps.md)), and the baseline
  refuses a bucket there, which would serve the hub's data.
  The baseline fails before it writes anything when it finds one.
  Declare them on local directories on the edge, or on the hub.

## Choose the addresses

Each side of a portal reaches the other at IP addresses that you declare.
The collection does not read them from the cluster's network status, and it does not translate them.

| Cluster | Address to declare |
|---|---|
| Has floating IPs | The floating IPs, which the collection recommends. |
| Bare metal or edge without floating IPs | Its node IPs. |
| Multi-AZ CNQ cluster in AWS | Its network load balancer's IPs
([why](../explanation/edges-and-portals.md#addresses-behind-a-load-balancer);
[the task](terraform.md#publish-a-multi-az-cluster-in-aws)). |

- Give them as `qumulo_portal_hosts`, a list of items with an `address` and an optional `port` (3713 by default).
- Never use `qumulo_api_host` for this, since it may be a name.
  A portal takes IP addresses only.
- Behind NAT, making the addresses reachable from the other side is your job.
  The collection does nothing about NAT.
- If you use the `qumulo.cluster.terraform` inventory plugin, it sets `qumulo_portal_hosts` from the output's
  `portal_hosts`, or from the addresses its `load_balancer_name` resolves to, and a `host_vars` value outranks it.
  [The Terraform inventory plugin](../reference/terraform-inventory.md) lists the output's format, and
  [Use a Terraform root's output as the inventory](terraform.md) shows the task.

[Why these addresses](../explanation/edges-and-portals.md#which-address-to-choose).

## Declare and apply the portal

1. Give the hub its addresses in `host_vars/hub01.yml`.
   List the addresses that every edge pairing with it can reach it at:

   ```yaml
   # host_vars/hub01.yml
   qumulo_portal_hosts:
     - address: 10.0.0.10
     - address: 10.0.0.11
     - address: 10.0.0.12
   ```

2. Declare the portal and the edge's own address in `host_vars/edge01.yml`:

   ```yaml
   # host_vars/edge01.yml
   qumulo_portal:
     hub: hub01
     type: read_write
     roots:
       - spoke_root: /projects
         hub_root: /projects
   qumulo_portal_hosts:
     - address: 10.20.0.5
   ```

   - `hub` names an inventory host, and that host needs a `qumulo_cluster_uuid`.
   - `type` is `read_write` (the default) or `read_only`.
   - Each item of `roots` pairs one `spoke_root` on the edge with one `hub_root` on the hub.

3. Bring the hub to its baseline first, so that every `hub_root` exists on it before the edge pairs.
   Check, then apply:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l qumulo_hubs --check --diff
   ansible-playbook qumulo.cluster.baseline -i inventory -l qumulo_hubs
   ```

   Declare each `hub_root` in the hub's `qumulo_directories`, so that this run creates it.
   Don't pair an edge in the same run that creates its `hub_root`
   ([why the hub goes first](../explanation/edges-and-portals.md#why-the-hub-goes-first)).

4. Check and apply the edge:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l qumulo_edges --check --diff
   ansible-playbook qumulo.cluster.baseline -i inventory -l qumulo_edges
   ```

   Step 16 runs the [portal_pair](../../roles/portal_pair/) role from the edge.
   It checks that both clusters run the same Qumulo Core version, proposes the portal and its roots on the edge with
   `portal_spoke`, accepts it and authorizes the roots on the hub with `portal_hub`, and waits until the edge shows the
   portal ACCEPTED and ACTIVE with its roots authorized.
   The check run predicts each side's change and waits for nothing.
   Accepting the portal makes both clusters unavailable briefly, and the run waits until they serve requests again.

   To pair one edge, give `-l hub01` and `-l edge01` instead.

If the edge's `qumulo_portal.hub` names no inventory host, or a host without a `qumulo_cluster_uuid`, or either side
lacks `qumulo_portal_hosts`, the run fails before anything changes with this message:

```text
edge01: qumulo_portal.hub must name an inventory host with qumulo_cluster_uuid, and both the spoke and the hub need
qumulo_portal_hosts, the IP addresses the other side reaches them at
```

[Troubleshooting](../reference/troubleshooting.md#the-inventory-and-the-cluster-disagree) lists the other messages.

## Verify

Run the edge's baseline again with `--check --diff`.
It reports no change when the inventory and both clusters agree.

To read each cluster's own record, run [portal_info](../../plugins/modules/portal_info.py) on both:

```yaml
# read_portals.yml
- name: Read the portals on both sides
  hosts: hub01,edge01
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
    - name: Read this cluster's spoke and hub portals
      qumulo.cluster.portal_info:
        gather_subset: [spokes, hubs]
      register: portals

    - name: Show them
      ansible.builtin.debug:
        var: portals.portals
```

Run it with `ansible-playbook -i inventory read_portals.yml`.
The edge lists the portal under `spokes`, and the hub lists it under `hubs`.
Both entries show `state: ACCEPTED` and `status: ACTIVE`, with every root `authorized: true`.

## Variants

### Pair a read-only portal

Set `type: read_only` in the edge's `qumulo_portal`.
The edge then reads the hub's data and writes none through the portal.
A later change of `type` is applied on the hub, and changing `read_write` to `read_only` drains the edge first.
The change makes both clusters unavailable briefly.

### Pair clusters that are not in the inventory

The `portal_pair` role takes no inventory variables.
To pair two clusters outside the inventory, run it from a playbook of your own, once per pairing.
It takes each cluster's connection options, with `expected_cluster_uuid` set on each, both address lists and the roots:

```yaml
- name: Pair the edge with the hub
  hosts: localhost
  gather_facts: false
  roles:
    - role: qumulo.cluster.portal_pair
      vars:
        portal_pair_spoke:
          api_host: edge01.example.com
          api_password: "{{ edge01_password }}"
          expected_cluster_uuid: "{{ edge01_cluster_uuid }}"
        portal_pair_hub:
          api_host: hub01.example.com
          api_password: "{{ hub01_password }}"
          expected_cluster_uuid: "{{ hub01_cluster_uuid }}"
        portal_pair_hub_hosts: [{address: 10.0.0.10}]
        portal_pair_spoke_hosts: [{address: 10.20.0.5}]
        portal_pair_roots:
          - {spoke_root: /projects, hub_root: /projects}
```

`ansible-doc -t role qumulo.cluster.portal_pair` lists every variable.

## Remove a portal

`portal_pair_state: absent` drains the portal before it deletes it.
The role sets the portal read-only on the hub, waits until both sides show it, deletes the edge's portal, and waits
until the hub's record is gone too.
The drain hands the edge's outstanding writes to the hub first
([why](../explanation/edges-and-portals.md#removing-a-portal-drain-then-delete)).

1. Remove `qumulo_portal` from the edge's `host_vars`.
   While it stays, the next baseline run pairs the edge again.
   Leave `qumulo_portal_hosts` on the hub if another edge pairs with it.

2. Run the role with the same connection options and `portal_pair_state: absent`:

   ```yaml
   # remove_portal.yml
   - name: Remove the edge's portal to the hub
     hosts: localhost
     gather_facts: false
     roles:
       - role: qumulo.cluster.portal_pair
         vars:
           portal_pair_state: absent
           portal_pair_spoke:
             api_host: "{{ hostvars['edge01'].qumulo_api_host }}"
             api_port: "{{ hostvars['edge01'].qumulo_api_port | default(8000) }}"
             api_username: "{{ hostvars['edge01'].qumulo_api_username | default('admin') }}"
             api_password: "{{ hostvars['edge01'].qumulo_api_password | default('') }}"
             api_token: "{{ hostvars['edge01'].qumulo_api_token | default('') }}"
             validate_certs: "{{ hostvars['edge01'].qumulo_validate_certs | default(true) }}"
             ca_path: "{{ hostvars['edge01'].qumulo_ca_path | default(omit) }}"
             expected_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
           portal_pair_hub:
             api_host: "{{ hostvars['hub01'].qumulo_api_host }}"
             api_port: "{{ hostvars['hub01'].qumulo_api_port | default(8000) }}"
             api_username: "{{ hostvars['hub01'].qumulo_api_username | default('admin') }}"
             api_password: "{{ hostvars['hub01'].qumulo_api_password | default('') }}"
             api_token: "{{ hostvars['hub01'].qumulo_api_token | default('') }}"
             validate_certs: "{{ hostvars['hub01'].qumulo_validate_certs | default(true) }}"
             ca_path: "{{ hostvars['hub01'].qumulo_ca_path | default(omit) }}"
             expected_cluster_uuid: "{{ hostvars['hub01'].qumulo_cluster_uuid }}"
   ```

   Run it with `ansible-playbook -i inventory remove_portal.yml --check` first, and then the same command without
   `--check`.

3. Check that neither side holds the portal.
   Run `read_portals.yml` again: neither cluster lists a portal to the other.

### The hub sees the edge DISCONNECTED

A portal whose edge the hub sees DISCONNECTED cannot be drained.
The role fails before any change, and the message says so.
If the edge is up, the status may be brief: run the role again later.
If the hub reaches the edge at a wrong address, correct the edge's `qumulo_portal_hosts`, put `qumulo_portal` back, and
run the edge's baseline.
Step 16 changes the hub's addresses for the edge in place ([portal_hub](../../plugins/modules/portal_hub.py)
documents `spoke_hosts` as "Changed in place when they differ"), though this has not been run against a DISCONNECTED
portal.
Then remove the portal again.
To delete the portal anyway, set `portal_pair_allow_data_loss: true`.
That loses the edge's writes that the hub has not received.

A PENDING portal holds no writes, and the role deletes it without a drain.

### Remove only the hub's record of an edge that no longer exists

When an edge was rebuilt with a new UUID or destroyed, the role cannot reach it.
Delete the hub's record of the old UUID with `portal_hub`, run against the hub:

```yaml
# delete_edge_record.yml
- name: Delete the hub's record of the old edge
  hosts: hub01
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
    - name: Delete the hub's record of the old edge
      qumulo.cluster.portal_hub:
        # The UUID the edge had before it was rebuilt or destroyed.
        spoke_cluster_uuid: 625d63a2-9663-4b0e-9d45-ec119a5a9a49
        state: absent
        allow_data_loss: true
```

Run it with `ansible-playbook -i inventory delete_edge_record.yml`.

`allow_data_loss` forces the delete and loses edge writes the hub never received.
An [edge rebuild](edge-rebuild.md) drains the old portal and deletes this record for you.

## What next

- [Replicate a directory to another cluster](replicate-a-directory.md) copies data the portal does not carry.
- [Inventory variables](../reference/variables.md) lists `qumulo_portal` and `qumulo_portal_hosts`.
- [Rebuild an unprotected edge](edge-rebuild.md) and
  [Use a Terraform root's output as the inventory](terraform.md) cover an edge's other lifecycle tasks.
