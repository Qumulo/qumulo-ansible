#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: tenancy_plan
short_description: Check a cluster's tenants, networks, shares and exports before the baseline writes any of them
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Computes step 2 of the C(qumulo.cluster.cluster_baseline) role for network tenants. From the inventory's tenants,
    networks, shares and exports and what step 2 read from the cluster, it works out what steps 3a, 3, 3b, 18, 19 and
    20a will send, and which rules the inventory breaks or the continuity rule would.
  - A pure computation. It sends no request, takes no connection options, and is not in the C(qumulo.cluster.qumulo)
    action group, so C(module_defaults) for that group do not reach it.
  - Every name is judged as it will be after the renames of step 3a, which O(tenants[].previous_name) declares.
  - The module judges the inventory in six stages, in that order, and stops after the first stage that refuses,
    because each stage reads what the one before it built. The stage reports every refusal it finds, in RV(refusals),
    and the module then fails with their messages, one to a line, in C(msg), beside RV(refusals), and carries nothing
    else of the plan. At Ansible verbosity 3 or more (C(-vvv)) the failure also carries RV(plan), what the module had
    computed before it stopped. A refusal of a later stage shows only once the earlier stages pass.
  - Stage 1, renames. An entry that renames a tenant to a name the cluster holds is refused when another present entry
    renames from that name, as in a swap or a chain, since the first rename would find the name taken. An entry
    without such a follower records a rename already done, and is not refused. Two present entries that rename one
    cluster tenant are refused.
  - The cloud refusal comes straight after stage 1, before every other rule. The module refuses every tenant write
    that the run would send to a cloud cluster, whether a rename, a create, a switch change or a delete, in one
    message that lists them, since no cloud cluster supports tenancy (G-TENANT-CLOUD). A cluster is cloud when
    O(cluster_kind) is V(cnq), or, with it unset, when O(cluster.protection_type) is object-backed. V(edge) and
    V(baremetal), another protection type, and none are not cloud. O(allow_management_disruption) does not skip this
    refusal. It ends the plan, so no other rule and no verdict runs, and RV(plan) is empty. The network
    PUT is no tenant write, and a run that makes only that is not refused.
  - Stage 2, entries and references. A tenant declared twice, a tenant with O(tenants[].identity), a key in a dict
    O(tenants[].smb_settings) or O(tenants[].nfs_settings) other than the server settings of
    M(qumulo.cluster.smb_settings) or M(qumulo.cluster.nfs_settings), so C(tenant) and C(state) too, and a run that
    would delete every tenant are refused. So is a create of a tenant while tenant 1 is gone and the cluster's tenants use
    different identity configurations, since the new tenant takes tenant 1's or the one they share. So is a create that
    makes the cluster's second tenant while a tenant holds a network that is not on the frontend bond, a frontend VLAN
    or a HOST network (G-TENANT-BACKEND). A share, export or network that names a tenant that neither
    O(tenants) declares nor the cluster holds is refused, and so is a network that names a tenant declared absent.
  - Stage 3, shares and exports. A (tenant, name) pair that two entries claim is refused, whether the entries are
    nested under a tenant, shared, or the factory share or export that O(factory_defaults) removes, and whether they
    claim it present or for removal. A tenant declared absent that holds a share or export that no entry declares or
    claims for it is refused, and the message lists each (G-TENANT-CASCADE). The rules of O(smb_shares),
    O(nfs_exports), O(tenants[].smb_shares) and O(tenants[].nfs_exports) on an entry's own form, such as a missing
    name or a malformed C(tenants), and of O(tenants[].smb_settings) and O(tenants[].nfs_settings), refuse in this
    stage too. The rules on which tenants an entry names refuse in stage 2.
  - Stage 4, NFSv4.1. The module refuses a global NFSv4.1 enable while a tenant the run keeps, declared or not, has two
    export paths that nest after the run's removals, and a tenant that serves NFSv4.1 at the end of the run while two
    of its export paths nest, when the run changes that tenant's NFSv4.1 setting or exports. A tenant declared absent
    is not judged, since the run removes its exports first.
  - Stage 5, networks. A network listed more than once, or listed under a tenant declared absent and also kept in
    O(network.frontend_networks) without a tenant, is refused. So is a tenant on a network of the backend bond
    (G-TENANT-BACKEND), and a document that gives networks of two tenants to one interface or VLAN, two networks of
    one address family to one VLAN, two O(tenants[].networks[].mtu) values to one VLAN, or a VLAN that is declared in
    more than one place. On networking its host manages, a tenant network with O(tenants[].networks[].vlan_id) or of
    type V(STATIC) is refused. So is a new flat network with no tenant when the run has more than one tenant. So is a
    cluster VLAN that carries a network the run keeps but the derived interfaces put on no interface, as when a tenant
    network's VLAN, derived from its O(tenants[].networks[].vlan_id), would leave out the other network the VLAN
    carries. A removed network that a tenant holds which O(tenants) does not declare is refused only once a present
    entry of O(tenants) declares O(tenants[].networks). With O(network.frontend_networks) alone the run removes a network as
    M(qumulo.cluster.network_config) does. A network that a tenant declared absent holds and the run keeps is refused,
    also when the run builds no document or does not own networks. Without V(networks) in O(owned_parts), a present
    tenant whose O(tenants[].networks) is not empty is refused, and so is a listed network that the cluster lacks,
    with the inventory entry named, since such a run sets only floating addresses. A conflict that the cluster already
    holds, as another writer left it, is warned of and not refused. When the run writes a network document
    (RV(network_changes) is true), the stage also refuses a planned document in which two or more V(STATIC) networks
    carry a default gateway of one IP version, as M(qumulo.cluster.network_config) does, with the same message. The
    cluster refuses it on chassis nodes at step 3, after step 3a has created the tenants. Networks that the cluster
    already gives each a gateway of that version are warned of, not refused. The checks of this stage run in groups, and
    a group that refuses ends the stage.
  - Stage 6, writes and continuity. The module lists the tenant writes and the network PUT that the run makes, in the
    order of the baseline's steps. They are step 3a's renames, then its enables, then its creates, step 3's network PUT
    when RV(network_changes) is true, step 3b's disables, and step 20a's deletes, each group in the order of
    O(tenants). It then judges each judged write on the management network and the bindings as they will be
    immediately after that write, so that a run which would cut the play off from the cluster is refused before any
    write.
  - A create, a delete, the network PUT and a change of C(rest_api_enabled) or C(web_ui_enabled) are judged. A rename
    and a change of any other switch are not, except that a rename that also turns on a REST switch is one item,
    judged as that change. A run that makes no judged write is not judged, and needs neither the management network
    nor the status.
  - After a judged write the management network must belong to a tenant with REST enabled. With one tenant after the
    write the management network is not looked up, and the only tenant must have REST enabled. The document in force
    after a write is the cluster's current one for a write at step 3a, the planned one for step 3 and every later
    write, and the current one when the run sends no network PUT.
  - REST enabled depends on the port the cluster listens on for the play, O(cluster_port). With V(443) it means
    C(web_ui_enabled), since the play then reaches REST through the web UI listener, and with another port it means
    C(rest_api_enabled). Without O(cluster_port) the cluster-side port is taken to be O(api_port), except that
    O(api_port) V(443) fails closed, so REST enabled then means that both switches are on.
  - The management network is a set of network ids within the document in force. It is O(management_network) when that
    is set, which the module takes as the operator's word. Otherwise it is, for each address that O(api_host) resolves
    to on the controller, the network whose ranges hold it in the document in force. An address that no range holds,
    or that a HOST network's ranges hold, needs O(cluster.status), which places the address on a network. With two or
    more tenants after the write, a HOST network in the set must also show on a device of a node in O(cluster.status),
    unless O(management_network) declares it and the status is unavailable.
  - When the management network is needed, with two or more tenants after the write, and it is undetermined, or a
    network in it is held by no tenant or is on no device, the refusal is the first of these that applies. (1) The
    cluster is host-managed and has no frontend network yet. (2) The run removes a network of the set. (3)
    O(management_network) names a network that is neither on the cluster nor in the inventory; when the run itself
    adds that network and the write is judged before step 3, as a create at step 3a is, the message says to add the
    network in an earlier run. (4) A network of the set belongs to no tenant. (5) O(api_host) resolves to no address,
    no network carries it, or the status is needed and unavailable (G-NET-APPLY). (6) A HOST network of the set is not
    on the cluster yet, or the status shows it on no device.
  - The other refusals of a judged write are the plain breach, where the management network after the write belongs to
    a tenant without REST enabled, or the only tenant lacks it, and the fail-closed breach, where O(cluster_port) is
    unset, O(api_port) is V(443) and exactly one of the two switches is on. Each message gives its remedy in the
    inventory's words, such as C(qumulo_management_network) for O(management_network) and C(qumulo_cluster_port) for
    O(cluster_port).
  - A caller passes the cluster's network status in O(cluster.status), or why it is unavailable in
    O(cluster.status_unavailable), whenever the cluster holds two or more tenants or the run creates a tenant, a
    present entry whose C(name) and C(previous_name) the cluster both lack, which covers every run that can need it.
    A run that needs the status and has neither fails with an internal error that asks for a bug report.
  - When step 3 writes a network document, the module also checks that the document keeps the address of O(api_host),
    when that is an IP address that a range of the current document holds, as M(qumulo.cluster.network_config) does at
    step 3. A document that drops it is refused, unless O(allow_management_disruption) is set and O(api_host_after)
    names the address that later tasks connect to. The message names step 3, asks for C(qumulo_api_host_after), and
    for C(qumulo_allow_management_disruption) when that is not set, and says that this play connects to the address. A
    name in O(api_host) is not checked here.
  - Each warning is printed, and none stops the run.
options:
  tenants:
    description:
      - The inventory's C(qumulo_tenants).
      - Entries are matched to the cluster's tenants by name. A present tenant that the cluster lacks counts as one that
        step 3a creates.
    type: list
    elements: dict
    default: []
    suboptions:
      name:
        description: The tenant's name, compared exactly.
        type: str
        required: true
      previous_name:
        description:
          - The tenant's name on the cluster before a rename. It is ignored when O(tenants[].state=absent).
          - The stage 1 rules decide which entries are renames still to be done.
        type: str
      state:
        description:
          - V(absent) plans the tenant's delete as an item of RV(step_20a), after the removals of its shares and exports
            in RV(smb.removals) and RV(nfs.removals). An absent tenant that the cluster lacks gets no item.
        type: str
        choices: [present, absent]
        default: present
      web_ui_enabled:
        description: Whether the tenant's networks serve the web UI.
        type: bool
      rest_api_enabled:
        description: Whether the tenant's networks serve the REST API.
        type: bool
      ssh_enabled:
        description: Whether the tenant's networks serve SSH.
        type: bool
      replication_enabled:
        description: Whether the tenant's networks serve replication.
        type: bool
      nfs_enabled:
        description: Whether the tenant's networks serve NFS.
        type: bool
      smb_enabled:
        description: Whether the tenant's networks serve SMB.
        type: bool
      networks:
        description:
          - The tenant's frontend networks, each in the shape of
            O(qumulo.cluster.network_config#module:frontend_networks), without C(tenant) and C(tenant_id), since the
            entry names the tenant, and with C(vlan_id) and C(mtu).
          - Once one present tenant declares it, every present tenant must, with V([]) for none. The tenants' networks
            and O(network.frontend_networks) are then the whole set of frontend networks.
          - A non-empty list needs V(networks) in O(owned_parts).
        type: list
        elements: dict
        suboptions:
          id:
            description: The network's id.
            type: int
            required: true
          name:
            description: The network's name.
            type: str
          type:
            description: How nodes get their addresses.
            type: str
            choices: [DHCP, STATIC, HOST]
          netmask:
            description: The netmask.
            type: str
          default_gateway:
            description: The default gateway of a V(STATIC) network.
            type: str
          ip_ranges:
            description: The static address ranges of a V(STATIC) network.
            type: list
            elements: str
          floating_ip_ranges:
            description: The floating address ranges.
            type: list
            elements: str
          vlan_id:
            description:
              - The frontend VLAN the network is on. Unset puts it on the frontend bond.
              - The module derives the VLANs and the bond's networks from these and from the live VLANs, on managed
                networking and when O(owned_parts) has V(interfaces).
            type: int
          mtu:
            description: The MTU of the network's VLAN.
            type: int
      smb_settings:
        description:
          - The tenant's SMB override. A dict is the options of M(qumulo.cluster.smb_settings), and RV(smb.overrides)
            gets an item with V(present) for it. V(inherit) removes the override, with an item for it too.
          - Unset leaves the override alone. Anything else is refused, and so is a key of a dict other than the server
            settings of M(qumulo.cluster.smb_settings), so C(tenant) and C(state) too, with the nearest key in the
            message when there is one. A key refused so is refused under a tenant declared absent as well; otherwise
            the value is ignored there.
        type: raw
      nfs_settings:
        description:
          - The tenant's NFS override, as O(tenants[].smb_settings) with the options of
            M(qumulo.cluster.nfs_settings), reported in RV(nfs.overrides).
          - A key of a dict other than the server settings of M(qumulo.cluster.nfs_settings), so C(tenant) and C(state)
            too, is refused, under a tenant declared absent as well.
          - The stage 4 rules judge the NFSv4.1 setting it gives. Otherwise ignored under a tenant declared absent.
        type: raw
      smb_shares:
        description:
          - The tenant's SMB shares, each the options of M(qumulo.cluster.smb_share). A share is the tenant's own, so
            C(tenants) and C(tenant) in it are refused.
          - A C(tenant_id) that names another tenant is refused. One that names the tenant the share is under, or no
            tenant on the cluster, is dropped with a warning.
          - Under a tenant declared absent a share is claimed for removal. A share with C(state=absent) under a present
            tenant removes it.
        type: list
        elements: dict
        default: []
      nfs_exports:
        description:
          - The tenant's NFS exports, each the options of M(qumulo.cluster.nfs_export), with the rules of
            O(tenants[].smb_shares).
        type: list
        elements: dict
        default: []
      identity:
        description: Reserved for per-tenant identity. Any value is refused.
        type: raw
  network:
    description: The inventory's C(qumulo_network).
    type: dict
    suboptions:
      frontend_networks:
        description:
          - The flat frontend networks, each in the shape of
            O(qumulo.cluster.network_config#module:frontend_networks).
          - A network keeps the tenant it names with C(tenant). Without one it keeps the tenant the cluster reports.
        type: list
        elements: dict
        suboptions:
          id:
            description: The network's id.
            type: int
            required: true
          name:
            description: The network's name.
            type: str
          type:
            description: How nodes get their addresses.
            type: str
            choices: [DHCP, STATIC, HOST]
          tenant:
            description:
              - The tenant the network belongs to, by name, which must be a tenant that O(tenants) declares present, or
                that the cluster holds after the renames and O(tenants) does not declare absent.
              - Refused with C(tenant_id).
            type: str
          tenant_id:
            description:
              - Deprecated, and removed in release 0.3.0 of this collection. The id of the network's tenant.
              - It counts as C(tenant) naming that tenant, with one warning for the entry. An id that no tenant has is
                dropped with a warning, and so is one on a network of the backend bond. An id whose tenant O(tenants)
                declares absent is refused.
            type: int
          netmask:
            description: The netmask.
            type: str
          default_gateway:
            description: The default gateway of a V(STATIC) network.
            type: str
          ip_ranges:
            description: The static address ranges of a V(STATIC) network.
            type: list
            elements: str
          floating_ip_ranges:
            description: The floating address ranges.
            type: list
            elements: str
      managed_interfaces:
        description: The bond and VLAN interfaces, as M(qumulo.cluster.network_config) takes them.
        type: dict
  smb_shares:
    description:
      - The inventory's C(qumulo_smb_shares), each entry the options of M(qumulo.cluster.smb_share), with C(tenants)
        naming the tenants it applies to.
      - C(tenants) is a list of tenant names, or V(all) for every tenant O(tenants) declares present. The entry claims
        the share in each listed tenant that is not declared absent, and for removal in each that is, which for V(all)
        is every absent tenant. A tenant that leaves an explicit list keeps its copy until an entry with
        C(state=absent) claims it.
      - C(tenant_id), deprecated, counts as C(tenants) naming that tenant, with a warning, and an id that no tenant has
        skips the entry, with a warning. It is refused beside C(tenants). C(tenant) is refused.
      - An entry with neither names the cluster's only tenant. It is refused when the run has more than one tenant,
        which are the cluster's after the renames and the run's creates.
      - An entry that gives no C(share_name), an empty C(tenants), one that names a tenant twice, one that is neither a
        list nor V(all), and V(all) with no present tenant declared are refused.
    type: list
    elements: dict
    default: []
  nfs_exports:
    description:
      - The inventory's C(qumulo_nfs_exports), each entry as in O(smb_shares) with the options of
        M(qumulo.cluster.nfs_export) and C(export_path) in place of C(share_name).
    type: list
    elements: dict
    default: []
  factory_defaults:
    description: The inventory's C(qumulo_factory_defaults).
    type: dict
    default: {}
    suboptions:
      smb_files_share:
        description:
          - V(absent) adds the removal of tenant 1's factory C(Files) share to RV(smb.removals), and claims that pair,
            also when tenant 1 is declared absent. The module only warns when the cluster has no tenant 1.
          - V(present), or unset, does nothing. When tenant 1 is declared absent and the cluster holds the share, the
            share counts as tenant 1's content, which tenant 1's entry must declare, or the stage 3 leftover rule
            refuses the run, and V(present) then also draws a warning that the option has no effect.
        type: str
        choices: [present, absent]
      nfs_root_export:
        description: As O(factory_defaults.smb_files_share), for tenant 1's factory C(/) export and RV(nfs.removals).
        type: str
        choices: [present, absent]
  nfs_settings:
    description:
      - The inventory's C(qumulo_nfs_settings). Only C(v4_enabled) matters to this module, to the stage 4 rules.
    type: dict
  management_network:
    description:
      - The inventory's C(qumulo_management_network), the ids of the networks that the play reaches the cluster on.
      - Stage 6 takes it as the management network of each judged write that leaves two or more tenants, as the
        operator's word, so it needs no status. An id the document in force lacks is refused, and an empty list fails
        the module when a judged write needs the management network.
    type: list
    elements: int
  cluster_port:
    description:
      - The inventory's C(qumulo_cluster_port), the port the cluster listens on for the play.
      - It picks the switch that REST enabled means in stage 6, C(web_ui_enabled) for V(443) and C(rest_api_enabled)
        for another port. Unset, the cluster-side port is O(api_port), and V(443) fails closed.
    type: int
  cluster_kind:
    description:
      - The inventory's C(qumulo_cluster_kind). V(cnq) marks a cloud cluster, and V(edge) and V(baremetal) an
        on-premises one.
      - Unset, O(cluster.protection_type) tells. The module refuses every tenant write to a cloud cluster, straight
        after stage 1.
    type: str
    choices: [cnq, edge, baremetal]
  allow_management_disruption:
    description:
      - The inventory's C(qumulo_allow_management_disruption).
      - V(true) skips every verdict of stage 6 for the whole run, so RV(verdicts) is empty and no item carries
        C(management_network), C(planned_networks) or C(planned_tenants). A delete still carries C(content_removed).
      - It does not skip the cloud refusal, or the address check of step 3, which, when the document drops the address
        of O(api_host), then needs O(api_host_after).
    type: bool
    default: false
  api_host_after:
    description:
      - The inventory's C(qumulo_api_host_after), the address that later tasks connect to once step 3 has changed the
        address of O(api_host).
      - With O(allow_management_disruption) it lets step 3's document drop the address of O(api_host).
    type: str
  api_host:
    description:
      - The address or name the play connects to, from C(qumulo_api_host).
      - The module resolves a name on the controller, where modules run, to find the management network when
        O(management_network) is unset and a judged write needs it. A name that resolves to no address is refused
        then. For an IP address, step 3's address check asks that the new document keeps it.
    type: str
    required: true
  api_port:
    description:
      - The port the play connects to, from C(qumulo_api_port), which is V(8000) unless the play sets another.
      - Without O(cluster_port) it is also the cluster-side port, and V(443) then fails closed, so REST enabled means
        both switches on.
    type: int
    default: 8000
  owned_parts:
    description:
      - The parts of the network document the role passes M(qumulo.cluster.network_config) in
        O(qumulo.cluster.network_config#module:owned_parts). The module merges step 3's document for these parts, and
        derives the frontend VLANs and the frontend bond's networks only when V(interfaces) is among them.
      - The tenants' networks, and any removal or move of a network, need V(networks).
    type: list
    elements: str
    choices: [interfaces, networks, floating_ips]
    default: [interfaces, networks, floating_ips]
  cluster:
    description: What step 2 read from the cluster.
    type: dict
    required: true
    suboptions:
      tenants:
        description: M(qumulo.cluster.tenant_info)'s RV(qumulo.cluster.tenant_info#module:tenants).
        type: list
        elements: dict
        required: true
      network:
        description: M(qumulo.cluster.network_info)'s V(document) subset.
        type: dict
        required: true
      status:
        description:
          - M(qumulo.cluster.network_info)'s V(status) subset, a list with one entry for each node, when step 2 read
            it and it answered.
          - Stage 6 reads from it the network that carries an address no range holds, and the HOST networks that show
            on a device. A caller passes it, or O(cluster.status_unavailable), whenever the cluster holds two or more
            tenants or the run creates one. A create is a present entry whose C(name) the cluster lacks once the run's
            renames are made, so a rename is not one, and an entry that takes the name a rename frees is.
          - Giving it with O(cluster.status_unavailable) fails the module. A run that needs the status and has neither
            fails with an internal error.
        type: list
        elements: dict
      status_unavailable:
        description:
          - Why M(qumulo.cluster.network_info)'s V(status) subset is unavailable, when step 2 read it, as that module
            lists it in RV(qumulo.cluster.network_info#module:unavailable_subsets). The Qumulo Core container answers
            HTTP 500, and some releases do not offer the status (G-NET-APPLY). The text is not
            used in a message.
          - With it, a declared O(management_network) is trusted without the HOST device check, and an undeclared one
            that needs the status is refused.
        type: str
      protection_type:
        description:
          - M(qumulo.cluster.cluster_info)'s C(protection.protection_system_type). The module reads it only when
            O(cluster_kind) is unset.
          - V(PROTECTION_SYSTEM_TYPE_OBJECT_BACKED) and V(PROTECTION_SYSTEM_TYPE_COLD_OBJECT_BACKED) mark a cloud
            cluster. Any other value, and none, do not.
        type: str
      smb_shares:
        description: M(qumulo.cluster.smb_info)'s V(shares) subset, which the stage 3 leftover rule reads.
        type: list
        elements: dict
        required: true
      nfs_exports:
        description: M(qumulo.cluster.nfs_info)'s V(exports) subset, which the stage 3 leftover rule and stage 4 read.
        type: list
        elements: dict
        required: true
      nfs_overrides:
        description: M(qumulo.cluster.nfs_info)'s V(tenant_settings) subset, by tenant name.
        type: dict
        required: true
      nfs_settings:
        description: M(qumulo.cluster.nfs_info)'s V(settings) subset.
        type: dict
        required: true
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
seealso:
  - module: qumulo.cluster.tenant
  - module: qumulo.cluster.tenant_info
  - module: qumulo.cluster.network_config
notes:
  - "API: none."
  - This module plans writes to the multitenancy REST API, which Qumulo marks as preview; a later Qumulo Core release
    may change it.
  - "Registers: G-TENANT-BACKEND, a tenant on a backend network, which stages 2 and 5 refuse; G-TENANT-CASCADE, the
    shares and exports a deleted tenant takes with it, which stage 3 refuses to leave unclaimed; G-TENANT-LISTEN, the
    rebind of every listener, which stage 6 judges on each write; G-NET-APPLY, the network status that the Qumulo Core
    container answers with HTTP 500, which stage 6 reads as unavailable; G-TENANT-CLOUD, the cloud platforms that
    refuse every tenant write, which the module refuses straight after stage 1."
"""

EXAMPLES = r"""
# tenancy_plan takes no connection options. The first four tasks read the cluster as step 2 does, so set the connection
# once for the play with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the cluster's tenants
  qumulo.cluster.tenant_info:
  register: tenant_facts

- name: Read the cluster's network document and its network status
  qumulo.cluster.network_info:
    gather_subset: [document, status]
  register: network_facts

- name: Read the cluster's shares
  qumulo.cluster.smb_info:
    gather_subset: [shares]
  register: smb_facts

- name: Read the cluster's exports and NFS settings
  qumulo.cluster.nfs_info:
  register: nfs_facts

# The cluster already holds Default and finance as the inventory declares them, so refusals is empty, network_changes is
# false and step 3 sends no network document. smb.shares and smb.overrides list what step 18 applies: the home share in
# both tenants, and finance's own SMB override. A second run gives the same plan. Only Default carries a default
# gateway: the cluster takes one IPv4 default gateway, and the module refuses a second.
- name: Plan the two tenants that the inventory and the cluster already agree on
  qumulo.cluster.tenancy_plan:
    tenants:
      - name: Default
        networks:
          - id: 1
            name: Default
            type: STATIC
            netmask: 255.255.255.0
            default_gateway: 192.0.2.1
            ip_ranges: [192.0.2.11-14]
            floating_ip_ranges: [192.0.2.21-24]
      - name: finance
        web_ui_enabled: false
        rest_api_enabled: false
        ssh_enabled: false
        replication_enabled: false
        networks:
          - id: 2
            name: finance
            type: STATIC
            vlan_id: 20
            netmask: 255.255.255.0
            ip_ranges: [198.51.100.11-14]
            floating_ip_ranges: [198.51.100.21-24]
        smb_settings:
          session_encryption: REQUIRED
    smb_shares:
      - share_name: home
        fs_path: /home
        tenants: all
        permissions:
          - trustee: Everyone
            rights: [READ]
    api_host: "{{ qumulo_api_host }}"
    cluster:
      tenants: "{{ tenant_facts.tenants }}"
      network: "{{ network_facts.network.document }}"
      status: "{{ network_facts.network.status | default(omit) }}"
      status_unavailable: "{{ network_facts.unavailable_subsets.status | default(omit) }}"
      smb_shares: "{{ smb_facts.smb.shares }}"
      nfs_exports: "{{ nfs_facts.nfs.exports }}"
      nfs_overrides: "{{ nfs_facts.nfs.tenant_settings }}"
      nfs_settings: "{{ nfs_facts.nfs.settings }}"
  register: tenancy

- name: Show whether step 3 would change the network document
  ansible.builtin.debug:
    msg: "network_changes is {{ tenancy.network_changes }}; the networks are held by {{ tenancy.bindings }}"

# The retired tenant holds no network. Its archive share and export go first, in smb.removals and nfs.removals, and the
# tenant last, in step_20a. When the cluster holds a share or export of the retired tenant that no entry declares or
# claims for it, the module fails and lists it, so nothing is deleted. The registers are those of the tasks above.
- name: Plan the removal of the retired tenant with its archive share and export
  qumulo.cluster.tenancy_plan:
    tenants:
      - name: Default
      - name: retired
        state: absent
        smb_shares:
          - share_name: archive
        nfs_exports:
          - export_path: /archive
    api_host: "{{ qumulo_api_host }}"
    cluster:
      tenants: "{{ tenant_facts.tenants }}"
      network: "{{ network_facts.network.document }}"
      status: "{{ network_facts.network.status | default(omit) }}"
      status_unavailable: "{{ network_facts.unavailable_subsets.status | default(omit) }}"
      smb_shares: "{{ smb_facts.smb.shares }}"
      nfs_exports: "{{ nfs_facts.nfs.exports }}"
      nfs_overrides: "{{ nfs_facts.nfs.tenant_settings }}"
      nfs_settings: "{{ nfs_facts.nfs.settings }}"
  register: retirement

- name: Show the order in which the baseline removes the retired tenant
  ansible.builtin.debug:
    msg: "{{ retirement.smb.removals + retirement.nfs.removals + retirement.step_20a }}"

# Behind NAT or a load balancer the play's address belongs to no network, so the run says which network the play reaches
# the cluster on, as management_network. A play whose client port is mapped onto cluster port 443 declares cluster_port.
# With two tenants, the cluster port 443 means REST enabled is web_ui_enabled, and the network 1 that Default holds is
# the play's. This run turns Default's web UI off, so the module refuses it before any write. With failed_when false the
# registered result keeps the refusals for a report, and the play goes on. The status comes from the network read above.
- name: Check whether turning off Default's web UI would cut a play behind a load balancer off
  qumulo.cluster.tenancy_plan:
    tenants:
      - name: Default
        web_ui_enabled: false
      - name: finance
    api_host: "{{ qumulo_api_host }}"
    api_port: 443
    cluster_port: 443
    management_network: [1]
    cluster:
      tenants: "{{ tenant_facts.tenants }}"
      network: "{{ network_facts.network.document }}"
      status: "{{ network_facts.network.status | default(omit) }}"
      status_unavailable: "{{ network_facts.unavailable_subsets.status | default(omit) }}"
      smb_shares: "{{ smb_facts.smb.shares }}"
      nfs_exports: "{{ nfs_facts.nfs.exports }}"
      nfs_overrides: "{{ nfs_facts.nfs.tenant_settings }}"
      nfs_settings: "{{ nfs_facts.nfs.settings }}"
  register: judged
  failed_when: false

- name: Show why the continuity rule refused the run
  ansible.builtin.debug:
    msg: "{{ judged.refusals | map(attribute='message') | list }}"
"""

RETURN = r"""
refusals:
  description:
    - The refusals of the first stage that refuses, each with its message and the names it gives. Empty when no stage
      refuses.
    - When it is not empty the module fails with the messages, one to a line, in C(msg). The failure carries this key,
      and RV(plan) at verbosity 3 or more, and no other key of the plan.
  returned: unless the module fails on its options or on an internal error
  type: list
  elements: dict
  contains:
    message:
      description: The refusal.
      type: str
    names:
      description: The tenants, networks and entries the message names.
      type: list
      elements: str
  sample:
    - message: "qumulo_tenants[1] and qumulo_tenants[2] both declare tenant finance; declare each tenant once."
      names: [finance]
plan:
  description:
    - What the module had computed when a stage refused, as a dict of keys that a run with no refusal returns, apart
      from RV(refusals).
    - After a refusal of stages 1 to 4, or the cloud refusal, which the module makes straight after stage 1, it is
      empty.
    - After a refusal of stage 5 it holds RV(smb), RV(nfs), RV(step_20a) and RV(tenants), as those keys describe them.
      Their share and export items carry any address ranges that the entries declare.
    - After a refusal of stage 6 it holds every key a run with no refusal returns, apart from RV(refusals), each as that
      key describes it. RV(network) holds every address range of the cluster's network document.
  returned: when a stage refuses, at Ansible verbosity 3 or more
  type: dict
  sample:
    smb: {removals: [], overrides: [], shares: []}
    nfs: {removals: [], overrides: [], exports: []}
    step_20a: []
    tenants: [Default, finance]
smb:
  description:
    - Step 18's lists, each in run order, by the tenants' names after the renames.
    - A flat entry of O(smb_shares) with no tenant on a run with one tenant names no tenant in its items.
  returned: when no stage refuses
  type: dict
  contains:
    removals:
      description:
        - The M(qumulo.cluster.smb_share) items with C(state=absent), run first. Those of the present tenants and of
          the flat entries come first, then each absent tenant's in entry order, then the factory share's.
        - An item from a tenant's entry or from O(factory_defaults) names its tenant in C(tenant). One from a flat
          entry names its tenants in C(tenants), or none for a flat entry with no tenant on a run with one tenant.
      type: list
      elements: dict
    overrides:
      description:
        - The M(qumulo.cluster.smb_settings) items with C(tenant), in entry order, one for each present tenant that sets
          O(tenants[].smb_settings). C(state=present) carries the options, and C(state=inherit) removes the override.
      type: list
      elements: dict
    shares:
      description:
        - The M(qumulo.cluster.smb_share) items that are present. A tenant's own share carries C(tenant). A flat entry
          carries C(tenants), the tenants it applies to, which for V(all) are the present tenants, and no C(tenant_id).
      type: list
      elements: dict
  sample:
    removals: []
    overrides: [{tenant: finance, state: present, session_encryption: REQUIRED}]
    shares: [{share_name: home, fs_path: /home, tenants: [Default, finance]}]
nfs:
  description: Step 19's lists, each in run order, by the tenants' names after the renames, shaped as RV(smb) is.
  returned: when no stage refuses
  type: dict
  contains:
    removals:
      description:
        - The M(qumulo.cluster.nfs_export) items with C(state=absent), run first, in the order of RV(smb.removals) and
          with the factory C(/) export last. An item names its tenants as the items of RV(smb.removals) do.
      type: list
      elements: dict
    overrides:
      description: The M(qumulo.cluster.nfs_settings) items with C(tenant), as RV(smb.overrides).
      type: list
      elements: dict
    exports:
      description: The M(qumulo.cluster.nfs_export) items that are present, as RV(smb.shares).
      type: list
      elements: dict
  sample:
    removals: [{export_path: /, tenant: Default, state: absent}]
    overrides: []
    exports: [{export_path: /scratch, fs_path: /scratch, tenants: [Default, finance]}]
tenants:
  description:
    - The names of the tenants once the run is done, which are the names the cluster holds after the renames, sorted,
      then the tenants the run creates in the order of O(tenants), less those it deletes, which RV(step_20a) lists.
    - The C(cluster_baseline) role counts them to warn, when Terraform owns the network document, that a Terraform
      apply that sends the networks without their tenants unbinds them.
  returned: when no stage refuses
  type: list
  elements: str
  sample: [Default, finance, hr]
step_20a:
  description:
    - The M(qumulo.cluster.tenant) items that delete each tenant declared absent that the cluster holds, in the order
      of O(tenants).
    - Each item carries C(content_removed), since steps 3, 18 and 19 remove the tenant's networks, shares and exports
      first, and, when the delete is judged, the inputs that RV(step_3a) describes. After a refusal of stage 5 an item
      of this key in RV(plan) has only C(name) and C(state).
  returned: when no stage refuses
  type: list
  elements: dict
  contains:
    name:
      description: The tenant to delete.
      type: str
    state:
      description: Always V(absent).
      type: str
    content_removed:
      description: Always V(true), for M(qumulo.cluster.tenant)'s check mode.
      type: bool
    management_network:
      description: As in RV(step_3a), when the delete is judged and leaves two or more tenants.
      type: list
      elements: int
    planned_networks:
      description: As in RV(step_3a), when the delete is judged.
      type: dict
    planned_tenants:
      description: As in RV(step_3a), when the delete is judged.
      type: dict
  sample: [{name: retired, state: absent, content_removed: true, planned_networks: {"1": Default}, planned_tenants: {Default: true}}]
network:
  description:
    - Step 3's network document, as the options of M(qumulo.cluster.network_config).
    - "It also carries the inputs of step 3's own judged write, when there are any: C(management_network), and
      C(planned_tenants), which M(qumulo.cluster.network_config) reads only in check mode. It is an empty dict when no
      present tenant declares O(tenants[].networks), O(network) gives neither C(frontend_networks) nor
      C(managed_interfaces), and step 3 sends no such input."
  returned: when no stage refuses
  type: dict
  contains:
    frontend_networks:
      description:
        - The flat networks, then each present tenant's networks in entry order, each with C(tenant), its tenant's name,
          when it has one. C(tenant_id), C(vlan_id) and C(mtu) are not sent, since the VLANs are in
          RV(network.managed_interfaces).
        - Left out when no present tenant declares O(tenants[].networks) and O(network.frontend_networks) is unset.
      type: list
      elements: dict
      returned: when the inventory declares networks
    managed_interfaces:
      description:
        - O(network.managed_interfaces), with the frontend VLANs and the frontend bond's networks derived from the
          tenants' networks on managed networking, when O(owned_parts) has V(interfaces).
        - Left out when the inventory gives none and derives none.
      type: dict
      returned: when the inventory gives or derives them
    management_network:
      description:
        - The network ids of the management network after step 3's write, for M(qumulo.cluster.network_config)'s
          O(qumulo.cluster.network_config#module:management_network).
        - Left out when step 3 writes nothing, when its write leaves one tenant, and with
          O(allow_management_disruption).
      type: list
      elements: int
      returned: when step 3's write is judged and leaves two or more tenants
    planned_tenants:
      description:
        - Each tenant's name to whether it has REST enabled after step 3's write, under the port rule of
          O(cluster_port), for M(qumulo.cluster.network_config)'s
          O(qumulo.cluster.network_config#module:planned_tenants) in check mode.
        - Left out when step 3 writes nothing, and with O(allow_management_disruption).
      type: dict
      returned: when step 3's write is judged
  sample:
    frontend_networks:
      - {id: 1, name: Default, type: STATIC, ip_ranges: [192.0.2.11-14], floating_ip_ranges: [192.0.2.21-24], tenant: Default}
      - {id: 2, name: finance, type: STATIC, ip_ranges: [198.51.100.11-14], floating_ip_ranges: [198.51.100.21-24], tenant: finance}
    managed_interfaces:
      frontend_bond_config: {mtu: 9000, networks: [1]}
      frontend_vlans: [{vlan_id: 20, network_id: 2, secondary_network_id: null, mtu: 1500}]
network_changes:
  description: Whether step 3's document differs from the cluster's.
  returned: when no stage refuses
  type: bool
  sample: true
bindings:
  description:
    - Each network's tenant by name once step 3 has run, keyed by network id as text. A network that no tenant holds
      has V(null).
    - It names a tenant that step 3a creates too, by its name.
  returned: when no stage refuses
  type: dict
  sample: {"1": Default, "2": finance}
validate:
  description:
    - Whether step 2 runs M(qumulo.cluster.network_config) in check mode with RV(network), to validate step 3's
      document before step 3a writes.
    - True when step 3's document differs from the cluster's and step 3a writes something first. The check takes the
      same RV(network.management_network) and RV(network.planned_tenants) as step 3's write, so it never reads the
      status itself.
  returned: when no stage refuses
  type: bool
  sample: true
step_3a:
  description:
    - Step 3a's M(qumulo.cluster.tenant) items in run order. The renames come first, then the enables, then the
      creates, each group in the order of O(tenants).
    - Each item carries C(name) and the switches it sends. A rename carries C(previous_name) and the switches the
      entry turns on, an enable the switches it turns on, and a create every switch its entry sets, with a switch it
      leaves out created off.
    - "A judged item, a create or a write that turns on C(rest_api_enabled) or C(web_ui_enabled), also carries the
      inputs of M(qumulo.cluster.tenant)'s own judgment, each as it will be immediately after that write and not at
      the end of the run: C(management_network) when the write leaves two or more tenants, which
      M(qumulo.cluster.tenant) reads in every run, and C(planned_tenants) and C(planned_networks), which it reads only
      in check mode. The module gives them in either mode."
    - The items of RV(step_3b) and RV(step_20a) carry the same inputs, and so does the network PUT in RV(network),
      less C(planned_networks). With O(allow_management_disruption) no item carries them.
  returned: when no stage refuses
  type: list
  elements: dict
  contains:
    name:
      description: The tenant, by the name it has after the write.
      type: str
    previous_name:
      description: The tenant's name before a rename, on a rename.
      type: str
    management_network:
      description:
        - The network ids of the management network after the write, in order, for M(qumulo.cluster.tenant)'s
          O(qumulo.cluster.tenant#module:management_network).
      type: list
      elements: int
    planned_networks:
      description:
        - Each network id as text, with the name of the tenant that holds it after the write, for each network a
          tenant holds, for M(qumulo.cluster.tenant)'s O(qumulo.cluster.tenant#module:planned_networks).
      type: dict
    planned_tenants:
      description:
        - Each tenant's name, with whether it has REST enabled after the write, under the port rule of
          O(cluster_port), for M(qumulo.cluster.tenant)'s O(qumulo.cluster.tenant#module:planned_tenants).
      type: dict
  sample:
    - {name: finance, smb_enabled: true, nfs_enabled: true, management_network: [1], planned_networks: {"1": Default},
       planned_tenants: {Default: true, finance: false}}
step_3b:
  description:
    - Step 3b's M(qumulo.cluster.tenant) items, the disables, in the order of O(tenants), each with the switches it
      turns off.
    - An item for a tenant that step 3a renamed carries C(previous_name).
    - A judged item, one that turns off C(rest_api_enabled) or C(web_ui_enabled), carries C(management_network),
      C(planned_networks) and C(planned_tenants) as on RV(step_3a), as the bindings and the services stand after step
      3 and this write.
  returned: when no stage refuses
  type: list
  elements: dict
  sample: [{name: Default, ssh_enabled: false}]
verdicts:
  description:
    - The continuity rule's verdict on each judged write, in run order. A write that is not judged has none.
    - Empty with O(allow_management_disruption).
    - A verdict's C(message) is also a refusal in RV(refusals), once, so the module fails when any is not null.
  returned: when no stage refuses
  type: list
  elements: dict
  contains:
    step:
      description: The step of the write, V(3a), V(3), V(3b) or V(20a).
      type: str
    write:
      description:
        - "The write as M(qumulo.cluster.tenant) and M(qumulo.cluster.network_config) word it in a refusal, such as
          C(creating tenant finance), C(setting web_ui_enabled: false on tenant Default), C(deleting tenant retired)
          or C(this network change)."
      type: str
    tenant:
      description: The tenant written; null for the network PUT.
      type: str
    management_network:
      description: The network ids of the management network after the write; null when the write leaves one tenant.
      type: list
      elements: int
    message:
      description: The refusal; null when the write keeps the play's connection.
      type: str
  sample:
    - {step: 3a, write: creating tenant finance, tenant: finance, management_network: [1], message: null}
    - {step: "3", write: this network change, tenant: null, management_network: [1], message: null}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.network import PARTS
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy import SWITCHES
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import (
    FACTORY_DEFAULTS_OPTIONS,
    FLAT_NETWORK_OPTIONS,
    TENANT_NETWORK_OPTIONS,
    plan,
)

TENANT_OPTIONS = {
    "name": {"type": "str", "required": True},
    "previous_name": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    **{switch: {"type": "bool"} for switch in SWITCHES},
    "networks": {"type": "list", "elements": "dict", "options": TENANT_NETWORK_OPTIONS},
    "smb_settings": {"type": "raw"},
    "nfs_settings": {"type": "raw"},
    "smb_shares": {"type": "list", "elements": "dict", "default": []},
    "nfs_exports": {"type": "list", "elements": "dict", "default": []},
    "identity": {"type": "raw"},
}
CLUSTER_OPTIONS = {
    "tenants": {"type": "list", "elements": "dict", "required": True},
    "network": {"type": "dict", "required": True},
    "status": {"type": "list", "elements": "dict"},
    "status_unavailable": {"type": "str"},
    "protection_type": {"type": "str"},
    "smb_shares": {"type": "list", "elements": "dict", "required": True},
    "nfs_exports": {"type": "list", "elements": "dict", "required": True},
    "nfs_overrides": {"type": "dict", "required": True},
    "nfs_settings": {"type": "dict", "required": True},
}
ARGUMENT_SPEC = {
    "tenants": {"type": "list", "elements": "dict", "default": [], "options": TENANT_OPTIONS},
    "network": {
        "type": "dict",
        "options": {"frontend_networks": {"type": "list", "elements": "dict", "options": FLAT_NETWORK_OPTIONS}, "managed_interfaces": {"type": "dict"}},
    },
    "smb_shares": {"type": "list", "elements": "dict", "default": []},
    "nfs_exports": {"type": "list", "elements": "dict", "default": []},
    "factory_defaults": {"type": "dict", "default": {}, "options": FACTORY_DEFAULTS_OPTIONS},
    "nfs_settings": {"type": "dict"},
    "management_network": {"type": "list", "elements": "int"},
    "cluster_port": {"type": "int"},
    "cluster_kind": {"type": "str", "choices": ["cnq", "edge", "baremetal"]},
    "allow_management_disruption": {"type": "bool", "default": False},
    "api_host_after": {"type": "str"},
    "api_host": {"type": "str", "required": True},
    "api_port": {"type": "int", "default": 8000},
    "owned_parts": {"type": "list", "elements": "str", "choices": list(PARTS), "default": list(PARTS)},
    "cluster": {"type": "dict", "required": True, "options": CLUSTER_OPTIONS},
}


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        result = plan(module.params)
    except QumuloError as e:  # an internal error, such as the role passing status both read and unavailable
        module.fail_json(msg=str(e))
    for warning in result.pop("warnings"):
        module.warn(warning)
    refusals = result.pop("refusals")
    if refusals:
        # Past the network stage the plan holds step 3's document, every range included, so a failure gives the plan
        # only at -vvv, Ansible's debugging level. A log kept at -v or more holds the ranges anyway: it prints every
        # task's result, step 2's network_info read among them.
        shown = {"plan": result} if module._verbosity >= 3 else {}
        module.fail_json(msg="\n".join(refusal["message"] for refusal in refusals), refusals=refusals, **shown)
    module.exit_json(changed=False, refusals=refusals, **result)


if __name__ == "__main__":
    main()
