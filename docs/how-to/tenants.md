<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Manage tenants

Qumulo marks the multitenancy REST API as preview in Qumulo Core 7.10.3 and 7.11.0. The collection manages tenants
through it on those releases, and a later Qumulo Core release may change it.

This page declares tenants in the inventory and has the baseline create them, bind their networks, give them their own
settings, shares and exports, and delete them. Each section starts with a run in check mode, which writes nothing and
reports every refusal the real run would make. When you finish, each tenant serves the services you turned on, on the
networks you listed under it, and a second run changes nothing.

For what a tenant partitions, why the second tenant is different from the third, and the rule that keeps your play
connected, see [Tenants and the continuity rule](../explanation/multitenancy.md).

## Before you start

- Set up the inventory with the cluster as a host, as [Set up an inventory for your clusters](set-up-inventory.md)
  shows. The examples manage `cluster01`. If the inventory holds vault values, add `--ask-vault-pass` or
  `--vault-password-file` to every `ansible-playbook` command ([Keep secrets out of the inventory](store-secrets.md)).
- Have an on-premises cluster: a hardware cluster or a virtual edge. No cloud cluster supports a second tenant, and the
  baseline refuses every tenant write to one ([Why no cloud cluster has a second tenant](../explanation/multitenancy.md#why-no-cloud-cluster-has-a-second-tenant)).
- Connect as a user that can read and write tenants and networks. Every baseline run whose inventory declares
  `qumulo_tenants`, `qumulo_network`, `qumulo_smb_shares`, `qumulo_nfs_exports`, `qumulo_nfs_settings` or
  `qumulo_factory_defaults` reads the tenant list first, which needs `PRIVILEGE_TENANT_READ`.
- Declare `qumulo_cluster_kind: edge` or `baremetal` for a cluster on premises. For tenants, a cluster with no kind is
  cloud only when its protection type is object-backed, and the kind settles that. The same kind also settles which
  areas Terraform owns on a provider-formed cluster, where an unset kind reads as `cnq`
  ([Terraform ownership](../explanation/terraform-ownership.md)).
- Adopt the cluster first if it has tenants already, so the inventory starts from what the cluster holds
  ([Adopt a cluster you already run](adopt-a-cluster.md)).
- Know how the play reaches the cluster. When it goes through NAT or a load balancer, or to a port the cluster maps
  onto another, [Declare the management network and the cluster port](#declare-the-management-network-and-the-cluster-port)
  comes first.

Check each change before you apply it:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l cluster01 --check --diff
```

## Add a tenant

The Default tenant, which formation creates, is an entry like any other. A tenant with no network and no service on is a
valid entry, and a service the entry leaves out is off on a new tenant and unchanged on an existing one. On networking
the host manages, with no frontend network yet, this first run is refused; start from
[On networking the host manages](#on-networking-the-host-manages).

1. Add the tenant to the host's `qumulo_tenants`. A tenant that the list leaves out stays as it is, so the list may
   name only the new one:

   ```yaml
   # inventory/host_vars/cluster01.yml
   qumulo_tenants:
     - name: finance
       smb_enabled: true
       nfs_enabled: true
   ```

2. Run the check. It predicts the create, and warns that the first create of a second tenant rebinds every listener,
   which ends NFS and replication connections, and that every create removes the static routes of every node. Read both
   warnings before you apply.
3. Apply it, then run the check again: it reports no change.

The tenant serves nothing until a network is bound to it. Until then, with two tenants, the Default tenant's services
listen only on the Default tenant's networks.

## Bind a network to a tenant

A network belongs to one tenant, and you declare the binding by listing the network under its tenant. The baseline builds
one network document from every tenant's networks and `qumulo_network`, so a network moves between tenants in one write.

Once one present tenant declares `networks`, every present tenant must, with `[]` for none. The tenants' networks and
`qumulo_network.frontend_networks` together are then the whole set, and a network that none of them lists is removed.
Three consequences follow:

- Keep the backend-bond networks, and any network no tenant holds, in `qumulo_network.frontend_networks`, or the run
  removes them or the cluster refuses the document.
  A network that shares a cluster VLAN with a tenant's network needs more: kept flat, it is refused. List it under that tenant with the VLAN's `vlan_id`, or give each network a VLAN of its own; a `tenant` on its `frontend_networks` entry does not clear the refusal. When the run drops the other network of a VLAN, keep the VLAN with the network in `qumulo_network.managed_interfaces.frontend_vlans`, or list the network under a tenant with the VLAN's `vlan_id` ([the refusals](../reference/troubleshooting.md#the-inventorys-tenants)).
- Move a network out of `qumulo_network.frontend_networks` when you list it under a tenant. A network listed twice, or
  under two present tenants, is refused.
- A run that would remove a network held by a tenant the inventory does not declare is refused, naming both: declare
  that tenant and list the network under it, or keep the network in `qumulo_network.frontend_networks`.

### On managed networking

A second tenant needs its own interface: the untagged bond has one tenant's networks, and a VLAN, with STATIC
addresses, serves the other. This example gives `finance` a network on VLAN 200:

```yaml
# inventory/host_vars/cluster01.yml
qumulo_tenants:
  - name: Default
    networks:
      - {id: 1, name: Default, type: STATIC, netmask: 255.255.255.0, default_gateway: 192.0.2.1,
         ip_ranges: [192.0.2.11-14], floating_ip_ranges: [192.0.2.21-24]}
  - name: finance
    smb_enabled: true
    nfs_enabled: true
    networks:
      - {id: 2, name: finance, type: STATIC, vlan_id: 200, mtu: 1500, netmask: 255.255.255.0,
         ip_ranges: [198.51.100.11-14], floating_ip_ranges: [198.51.100.21-24]}
```

1. Run the check. It creates `finance`, if the cluster does not have it yet, and the network in the order the cluster
   accepts, validates the document with the cluster, and reports the network step as pending the tenant's create. When
   `finance` exists already, the network step is reported as a change of its own.
2. Apply it. One network write binds network 2 to `finance` and leaves network 1 in `Default`.

Four rules shape the document. Each refusal names the entry to change.

- **One tenant per VLAN.** The IPv4 and IPv6 networks of one VLAN belong to one tenant, and two networks of one address
  family, or two `mtu` values, on one VLAN are refused. Two tenants on the untagged bond are refused too.
- **STATIC on the VLAN.** A managed document that holds a DHCP network holds no other network (G-NET-DHCP). On a
  cluster whose untagged network is DHCP, move that network to STATIC first, in a run of its own, while the cluster has
  one tenant. The move drops the address the play connects to, so that run takes `-e
  qumulo_allow_management_disruption=true -e qumulo_api_host_after=<the new address>`, as [Change the network without
  cutting the play off](inventory-recipes.md#change-the-network-without-cutting-the-play-off) shows. Add the tenant and
  its VLAN in a later run without the consent, so that the continuity rule still guards the create.
- **One default gateway per IP version.** At most one STATIC network, across the tenants and
  `qumulo_network.frontend_networks`, carries an IPv4 `default_gateway`, and at most one an IPv6 one. The step refuses a
  second on every cluster, and the message says to set `default_gateway: ""` on the others. Keep the gateway on the
  network the play connects on.
- **A tenant serves clients on its own subnet.** A VLAN network with no gateway of its own answers only clients on its
  subnet. A tenant whose clients are on other subnets needs routes out of the VLAN, and the collection does not manage
  static routes. Creating a tenant, and binding a network to a tenant, removes the static routes of every node
  (G-TENANT-ROUTES), so apply them again after every run that does either.

A network that stays in `qumulo_network.frontend_networks` and names no tenant keeps the tenant the cluster reports. Name
its tenant there with `tenant: <name>`. The tenant need not be declared in `qumulo_tenants` when the cluster has it.

### On networking the host manages

On networking its host manages, every network has type `HOST` and takes neither `vlan_id` nor STATIC addressing. A second
tenant needs the cluster to have a frontend network already, so the order is fixed. The second tenant's network
serves nothing until the host associates its id with an interface, such as a second NIC, which the collection cannot do.

1. Declare the HOST network under the one tenant, and run the baseline once:

   ```yaml
   # inventory/host_vars/cluster01.yml
   qumulo_tenants:
     - name: Default
       networks:
         - {id: 1, name: Default, type: HOST}   # the id your host associates with the interface
   ```

   A run that adds the second tenant first stops at step 2 with "The cluster's networking is host-managed and it has no
   frontend network yet".
2. Add the second tenant and its HOST network, and declare `qumulo_management_network` as the id of the HOST network
   the play connects on, 1 in the example, before you run the check:

   ```yaml
   qumulo_management_network: 1
   ```

   Declare it on every host-managed cluster with two tenants, whatever the network status answers. Once the second HOST
   network exists the document has two, and status shows only that some HOST network is served, not which one
   (a limit of the cluster's status report), so an undeclared address is refused with the message about
   NAT or a load balancer, which does not mention HOST networks. A declared network is taken on your word, and the
   declaration also covers a cluster whose network status answers HTTP 500 (G-NET-APPLY).

3. Apply it, then run the check again: it reports no change.

## Declare the management network and the cluster port

With two or more tenants, the baseline refuses a write that would leave the network the play connects on in a tenant
without REST. It finds that network from `qumulo_api_host` and the network document, and the two variables below tell it
what it cannot see. Declare them in the host's file, with `qumulo_api_host` and `qumulo_api_port`:

- **Behind NAT or a load balancer**, set `qumulo_management_network` to the id of the network `qumulo_api_host` is
  forwarded to, or a list of two ids for a name that has an IPv4 and an IPv6 network. Network id 0 is a real id.
- **To a port the cluster maps onto another**, set `qumulo_cluster_port` to the port the cluster listens on for the play.
  At port 443 the play reaches REST through the web UI listener, so set `qumulo_cluster_port: 443` for a tunnel or a
  balancer that maps your port onto 443. Without it, a play that connects on 443 fails closed: the refusal asks for
  `rest_api_enabled` and `web_ui_enabled` both on the tenant that holds the play's network, on a single-tenant cluster
  too.

```yaml
# inventory/host_vars/cluster01.yml
qumulo_api_host: 203.0.113.10          # the load balancer's address
qumulo_api_port: 18000                 # the port the play connects to on it
qumulo_management_network: 1           # the network the balancer forwards to
qumulo_cluster_port: 443               # the port the balancer maps it onto on the cluster
```

A single-tenant cluster behind a load balancer needs neither, unless the port the play connects on, or the port the
balancer maps it onto, is 443. Run the check with the
variables set, and read the refusals that remain: each says which variable to set.

## Give a tenant its own SMB and NFS settings

A tenant's override replaces the cluster's SMB or NFS settings for that tenant, and it is a full copy: once it exists, a
later change to the cluster's settings does not reach it.

1. Declare the override under the tenant. A dict sets only the keys it gives, and the keyword `inherit` removes the
   override:

   ```yaml
   qumulo_tenants:
     - name: finance
       smb_settings: {session_encryption: REQUIRED}
       nfs_settings: inherit
   ```

2. Run the check and apply it. A tenant with no override gets one, built from the cluster's settings with your keys
   changed. A second run changes nothing.

Leaving `smb_settings` out leaves the override as it is. Turning NFSv4.1 on for a tenant, or removing an override that
keeps it off while the cluster has it on, is refused when two of the tenant's export paths nest, and the message gives
the pair. Remove or move one export first, or declare one `state: absent` in the same run: the removals run first.

## Declare shares and exports

A share or export is declared under its tenant, in `smb_shares` and `nfs_exports`, or once for several tenants in the
flat `qumulo_smb_shares` and `qumulo_nfs_exports`:

```yaml
qumulo_tenants:
  - name: finance
    smb_shares:
      - {share_name: ledger, fs_path: /finance/ledger}
    nfs_exports:
      - {export_path: /ledger, fs_path: /finance/ledger}

qumulo_smb_shares:
  - {share_name: home, tenants: all, fs_path: /home, expand_fs_path_variables: true}
qumulo_nfs_exports:
  - {export_path: /scratch, tenants: [Default, finance], fs_path: /scratch}
```

Run the check and apply it. `tenants: all` gives each tenant the inventory declares present a copy, and a list gives
each named tenant one. Declare every tenant that should carry the entry, if only by name: a tenant left out of
`qumulo_tenants`, such as `Default` when the list names only `finance`, gets no copy from `all`. A second run changes
nothing.

- An entry of the flat lists needs `tenants` once the inventory or the cluster has more than one tenant. A tenant's
  nested entry takes neither `tenants` nor `tenant`.
- A shared entry acts only on the pairs it claims. A tenant that leaves an explicit list keeps its copy, so take one
  tenant out by declaring that pair `state: absent`, under the tenant or in the flat list:

  ```yaml
  qumulo_smb_shares:
    - {share_name: forms, tenants: [hr], state: absent}
  ```

- A pair comes from one entry only. A share declared both under `finance` and in a shared entry that lists `finance` is
  refused, naming both.
- Declare `tenants: all` for a share every tenant carries. A tenant leaves `all` through its own `state: absent`, which
  removes its copy without an edit to the shared entry.
- The modules never move a share or export between tenants. Declare a removal in one tenant and a create in the other.

## Rename a tenant

Rename with `previous_name`, which names the tenant's name on the cluster now:

```yaml
qumulo_tenants:
  - name: accounts
    previous_name: finance
```

1. Run the check. It reports one rename, and the tenant keeps its networks, shares and exports. A check run of the
   rename still finds the tenant under its old name, so its shares, exports and settings show as creates under the new
   name and its removals as no change. A shared entry, network or settings entry that still names `finance` is refused
   at step 2, and the message gives the old name: change it to `accounts`.
2. Apply it.

Keep `previous_name` in the inventory after the rename: a run that finds the tenant already renamed ignores it. A swap
of two names, or a chain of renames, is refused, because each step needs a name the cluster does not hold; make each
step in a run of its own.

## Delete a tenant

Deleting a tenant is one line: its entry's `state: absent`. Keep what is declared under it, so that the baseline removes
it before the tenant:

```yaml
qumulo_tenants:
  - name: finance
    state: absent
    smb_shares:
      - {share_name: ledger, fs_path: /finance/ledger}
    # ...and every other share, export and network declared under finance
```

1. Run the check. It predicts every removal and the delete. A tenant that holds a share, an export or a network that no
   entry declares or claims for it, and the networks the run would not remove, are refused: declare each share and
   export under the tenant, or list the tenant in the shared entry that has it. Its networks go when the inventory
   declares the network set, through every present tenant's `networks` or `qumulo_network.frontend_networks`, and the
   tenant's own are left out of it or listed under a tenant that stays. When no present tenant declares `networks` and
   `qumulo_network.frontend_networks` is unset, the run is refused instead, since nothing removes the network.
2. Apply it. The baseline removes the tenant's networks at the network step, its shares and exports at the start of the
   share and export steps, its SMB and NFS overrides, and then the tenant, so the cluster's cascade removes nothing.
3. Run it again. An entry for a tenant the cluster lacks is already satisfied, and so is everything under it.

Every shared entry that lists the tenant, or is `all`, removes that tenant's copy with it. No shared entry is edited.

- The last tenant is never deleted. An inventory that declares every tenant `state: absent` is refused.
- A delete that leaves two or more tenants is judged on the management network, so a play behind NAT or a load
  balancer declares `qumulo_management_network` first. A delete that leaves one tenant is judged on the survivor alone,
  and rebinds every listener, which ends NFS and replication connections.
- Deleting tenant 1 removes the factory `Files` share and `/` export with it. `qumulo_factory_defaults` has no effect on
  a tenant declared absent, and the run says so.

## Prepare backend networks before the second tenant

A cluster whose network document lists a backend-bond network holds it in the Default tenant, because the collection's
own network write leaves a network in the only tenant. With a second tenant such a network aborts every node. The
baseline refuses the second tenant at step 2 while any tenant holds one, and the message gives the network
(G-TENANT-BACKEND).

1. Unbind the backend network from the Default tenant with a tenant PATCH of `networks` that leaves it out. On a
   node, open the node's Qumulo shell with `sudo /opt/qumulo/bin/qsh`, which has `qq` and needs no `--host`. From
   another machine, install the `qumulo-api` package and give each `qq` command `--host <address>` before its subcommand.
   Run `qq login -u <user>`, then `qq multitenancy_list_tenants` for the tenant's id, then
   `qq multitenancy_modify_tenant --id <tenant id> --network-id <ids>`, where `<ids>` lists every network the tenant
   keeps, separated by spaces, and not the backend network. The list must be complete: a network it leaves out is
   unassigned, and a call without `--network-id` leaves the networks as they are.
   Do not use `qq network_mod_network --clear-tenant-id`: on a single-tenant cluster it puts the network back in the one
   tenant.
2. Run the baseline that adds the second tenant straight away, with no network change before it. Any network write on a
   single-tenant cluster binds the backend network again.

Keep the backend networks out of every tenant's `networks`, and declare them in `qumulo_network.frontend_networks`
without a `tenant`.

## Update an inventory that names `tenant_id`

`tenant_id` on a network, share or export is deprecated, and release 0.3.0 removes it. Until then the baseline accepts it,
translates the id to the tenant's name, and warns once for each entry. Write `tenants: [<name>]` or `tenant: <name>`
instead.

1. Run the check and read the warnings. Each gives the name to use.
2. On a share or export, write `tenants: [<name>]` in place of `tenant_id`. On a flat network, write `tenant: <name>`. An
   entry names its tenant once: `tenant` beside `tenant_id`, or `tenants` beside `tenant_id`, is refused.
3. A `tenant_id` the cluster lacks skips a share or export with a warning, because ids are never reused: name the tenant.

## Regain REST access

A change that leaves the network the play connects on in a tenant without REST ends the connection mid-run. The baseline
refuses that write before it sends it, so you reach this section only after `qumulo_allow_management_disruption`, a
change made outside the collection, or a mistaken `qumulo_management_network`.

REST still answers on loopback on every node. Reach a node from its console, or over SSH on a tenant that has
`ssh_enabled: true`, and fix it there with `qq`:

1. On the node, open the node's Qumulo shell with `sudo /opt/qumulo/bin/qsh`, which has `qq` and needs no `--host`.
   From another machine that reaches an address where REST answers, install the `qumulo-api` package and give each
   command `--host <address>` before its subcommand.
2. Run `qq login -u <user>`, and `qq multitenancy_list_tenants` for the tenants' ids.
3. Turn REST back on for the tenant that holds your network with
   `qq multitenancy_modify_tenant --id <tenant id> --enable-rest-api`, which leaves every other setting as it is, or bind
   the network to a tenant that serves REST with `qq network_mod_network --network-id <id> --tenant-id <tenant id>`.
4. Run the check from the controller.

## What is tested

[Tested Qumulo Core versions](../reference/tested-versions.md#tier-3-tenants-and-managed-networking) says which tenant
cases are tested, and how: on a hardware cluster with VLAN-bound tenants, those cases include the `cluster_baseline`
runs and the capture this page relies on.

## If a run refuses

[Troubleshooting](../reference/troubleshooting.md#tenants) quotes each refusal with its remedy, and
[Respond to a refusal](respond-to-a-refusal.md) says how to read one. The refusals that name
`qumulo_management_network` and `qumulo_cluster_port` are in
[Declare the management network and the cluster port](#declare-the-management-network-and-the-cluster-port).

## Next

- [Tenants and the continuity rule](../explanation/multitenancy.md): why the rule asks what it asks.
- [Inventory variables](../reference/variables.md#tenants): every key of a tenant entry.
- [Rebuild an unprotected edge](edge-rebuild.md): what the rebuild's drift check does with tenants, and
  `previous_name: Default`.
- [What capture and clone read and write](../reference/capture-and-clone.md): how adoption writes tenants.
- [Terraform ownership](../explanation/terraform-ownership.md#tenants-and-the-network-document): what a Terraform root
  that omits tenants does to the bindings.
