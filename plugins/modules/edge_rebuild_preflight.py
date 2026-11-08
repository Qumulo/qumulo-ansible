#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: edge_rebuild_preflight
short_description: Check that a Qumulo edge's live state can be replayed from its inventory before a rebuild destroys it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Reads only, and never reports a change. The C(qumulo.cluster.edge_rebuild) role runs it on the old edge before it
    drains anything.
  - B(Drift.) Fails when the edge has tenants, tenant networks, SMB shares, NFS exports, quotas, snapshot policies,
    local users, local groups or portal spoke roots that the inventory does not declare, or that the inventory
    declares absent, unless O(ignore_drift) is set. The inventory declares a tenant, share or export absent with
    C(state) V(absent) in its own entry, and the other areas in O(declared_absent). A share or export is matched
    within its tenant, so a share of the same name in another tenant is a different share. Tenant 1's factory C(Files)
    share and C(/) export, whatever tenant 1's name, the built-in users C(admin) and C(guest), and the built-in groups
    C(Users) and C(Guests) are never drift; a C(Files) share or a C(/) export in any other tenant is drift like any
    other.
  - B(Tenants.) The module judges the old edge against what the replay will build. It reads O(tenants), O(smb_shares),
    O(nfs_exports), O(factory_defaults) and O(network) as the baseline reads them on a newly formed edge, which holds
    tenant 1 named C(Default), the factory share and export, and nothing else. It reads the old edge's tenants by
    their names after the renames the inventory declares, that is, an entry whose C(previous_name) the edge holds
    while the edge lacks its C(name). A tenant the inventory declares absent and the edge holds is drift, tenant 1
    included. Otherwise tenant 1 is replayed only under the name the replay gives the new tenant 1, V(Default) or the
    name of the entry with C(previous_name) V(Default), so tenant 1 renamed by hand to another name is drift unless an
    entry names it with a C(previous_name) of V(Default). Another tenant is replayed when the replay ends with a
    tenant of its name.
  - B(A tenant gained.) A newly formed edge has tenant 1, V(Default), with the factory C(Files) share and C(/) export,
    and the replay keeps it unless O(tenants) declares it V(absent). When the edge has no tenant 1 and the replay ends
    with tenant 1 under a name the edge does not hold, that tenant is drift with C(added) V(true), which O(ignore_drift)
    waives; its message begins "the rebuilt edge would gain tenant". An edge that holds a tenant of that name gains
    nothing. An entry that declares that tenant V(absent) is refused, as below, unless the factory share and export are
    claimed for its removal, by both keys of O(factory_defaults) set to V(absent) or by declaring them under that entry.
    C(docs/explanation/rebuilds.md) says why the gain is drift.
  - B(Tenant networks.) The module works out which tenant the replay binds each network to. A network listed in the
    C(networks) of an entry of O(tenants) is that tenant's. A network in the C(frontend_networks) of O(network) is the
    tenant's named by its C(tenant), and tenant 1's for C(tenant_id) V(1). A listed network sent with no tenant, that
    is, a flat network with no C(tenant), or a C(tenant_id) the newly formed edge lacks, stays in tenant 1, the formed
    edge's tenant after any rename by C(previous_name) of V(Default), unless the module refuses it, as below. When the
    edge holds that network in no tenant, and on neither backend bond, it is drift in the V(tenant_networks) area with
    C(added) V(true), which O(ignore_drift) waives; its message begins "the rebuilt edge would put network". When
    O(network) has no C(frontend_networks) and no present entry of O(tenants) has C(networks), not even an empty list,
    the replay keeps the newly formed edge's networks, which are all tenant 1's, and does not replay a network the
    formed edge lacks. On managed networking that is network 1, so network 1 that the edge holds in no tenant is that
    drift too. Once any network is listed, the replay keeps only the listed networks and removes the rest, so each
    network the edge holds must be listed, under its tenant or in C(frontend_networks) for a network no tenant holds or
    on the backend bond, and every present tenant must have C(networks), V([]) for none. For each tenant the replay
    builds again, each network the edge binds to it that the replay would not is drift in the V(tenant_networks) area; a
    tenant that is itself drift is reported without its networks.
  - On an edge whose network document has no managed interfaces and only HOST networks, which forms again with no
    frontend network, fails when the replay creates a tenant but the inventory lists no network for a tenant that
    stays. The replay's tenant 1 pass writes tenant 1's own networks, or every listed network that stays when tenant 1
    holds none, in tenant 1 before the baseline creates the other tenants; with none to write, the replay would fail
    after the destroy, and C(docs/explanation/rebuilds.md) says why. Tenant 1's own
    networks are those under its entry and the flat ones that step 2 resolves to tenant 1 or to no tenant, and the
    baseline's step 3 adds the other tenants' networks. The module decides with the replay's own rule, the
    P(qumulo.cluster.tenancy_first_pass#filter) filter on the newly formed edge, so a network listed under any present
    tenant or in C(frontend_networks) of O(network), for a tenant that stays or for none, is enough. Tenant 1 declared
    V(absent) takes part as the C(Default) the edge forms with, so it needs no rename. The message starts "the replay
    would create tenant", comes after any drift, and O(ignore_drift) does not waive it. With V(network) in
    O(terraform_owned_areas), the message ends with the remedy that option describes.
  - On the same kind of edge, when the replay creates a tenant and the tenant 1 pass would write two or more networks
    into tenant 1, which it does only when tenant 1 holds two or more, or holds none while the inventory lists two or
    more, the baseline's step 2 needs to know which HOST network the play reaches the edge on. Where tenant 1 holds a
    network, a second tenant's own network is not in the pass; the baseline's step 3 adds it. With O(management_network)
    unset, the module infers it from this edge, as the network that carries the play's address there, and returns it as
    RV(management_network); O(management_network) says when it can. It fails when it cannot place the address. The
    message starts "the replay's tenant 1 pass would write networks", names them, says why the module cannot tell, and
    says to set C(qumulo_management_network) to the id of the network C(qumulo_api_host) is on. The reasons are that
    this edge does not answer its network status, that its network status
    read failed (the reason is "this edge's network status read failed", then the error), that it holds two HOST
    networks and the play reaches it on the host's own address ("this edge holds HOST networks 0 and 1 and the play
    reaches it on the host's own address, A, which the network status does not place on any one of them"), that the
    status does not show the address ("this edge's network status places the play's address on no one network"), that
    the status shows the HOST network it would name served on no device ("this edge's network status shows network N
    served on no device"), and that C(qumulo_api_host) resolves to no address.
  - On the same kind of edge, when the tenant 1 pass would write two or more networks, fails when the play reaches this
    edge on a network the pass does not write, which is one the inventory lists for no tenant that stays or lists under
    a second tenant. The message starts "the play reaches this edge on network N, which the replay's tenant 1 pass does
    not write before it creates tenant X, so the baseline's step 2 would refuse the create after the destroy", and says
    to set C(qumulo_api_host) to an address on one of the networks the pass writes for the rebuild and
    C(qumulo_management_network) to that network's id, or, when the inventory lists network N under another id, to set
    C(qumulo_management_network) to that id.
  - Each of those two messages comes after any drift, in the place of the "the replay would create tenant" message, and
    O(ignore_drift) does not waive either.
  - On the same kind of edge, when the replay creates a tenant and the tenant 1 pass writes a network, fails when
    O(management_network) names a network the pass does not write, however many networks the pass writes, whether
    the inventory lists it for no tenant that stays or for a tenant whose network the baseline's step 3 adds only
    after that create, and when it is declared with no network, as O(management_network) describes.
  - On the same kind of edge, fails when a network listed in the C(networks) of an entry of O(tenants) has C(vlan_id) or
    is C(type) V(STATIC), which step 2's host-managed check refuses of a tenant network. The module makes this check
    once it has read the edge's network document, before drift, and O(ignore_drift) does not waive it. The message
    starts "the replay onto the rebuilt edge would refuse the inventory" and goes on with the baseline's own, such as
    "network 0 of tenant it-one has vlan_id 10, but the host manages this cluster's networking, so a tenant network
    takes no VLAN. Remove vlan_id." A network in C(frontend_networks) of O(network) is not checked here. The check
    waits for the replay when O(network) declares C(managed_interfaces) and the tenant 1 pass runs, which writes them
    first.
  - Fails before it reads the edge when the inventory rules of the baseline's tenancy step would refuse the inventory,
    that is, its renames, its entries and tenant references, its claims, and its network rules that need no read.
    Examples are a flat share or export with neither C(tenants) nor C(tenant_id) once the replay has more than one
    tenant, counting the tenants the inventory creates, two entries of O(tenants) with one name, a key in a tenant's
    C(smb_settings) or C(nfs_settings) dict that the global settings do not take, an inventory that
    leaves no tenant, and two entries, or an entry and O(factory_defaults), that claim one share or export in one
    tenant. The network rules are that once a present tenant declares C(networks) every present tenant must, that a
    network is listed once, and that an absent tenant's network is neither kept flat nor named by a flat C(tenant_id).
    Tenant 1 declared V(absent) while no network is listed is refused too, since it holds the newly formed edge's
    networks, which the run keeps, and so is a network listed with no tenant while tenant 1 is declared V(absent). The
    network document's own checks (its validation, the backend checks, and the interfaces) and the
    NFS and continuity checks need the new edge and run only at the replay, as do its host-managed checks apart from
    the one of a tenant network above. The message starts "the replay onto the rebuilt edge would refuse the
    inventory", and O(ignore_drift) does not waive it. The warnings of that step come
    back as module warnings prefixed "the replay onto the rebuilt edge". An entry of O(tenants) with no name, or with
    a C(state) other than V(present) or V(absent), also fails before any read, with a message that names the entry,
    such as C(qumulo_tenants[0] gives no name). So does a listed network with no integer C(id), such as
    C(qumulo_network.frontend_networks[0] gives no id); these messages do not carry the replay prefix. A flat entry's
    deprecated C(tenant_id) is read only for tenant 1; any other id is skipped with a warning, so the entry's share or
    export on the old edge is drift.
  - Fails after it reads the edge's network document, before any other read and before drift, on a listed network sent
    with no tenant that the newly formed edge does not hold, when the replay has more than one tenant at the baseline's
    step 3, counting the tenants it creates and those it deletes only at the end. The message starts "the replay onto
    the rebuilt edge would refuse the inventory" and is the baseline's step 2 refusal; O(ignore_drift) does not waive
    it. The newly formed edge holds network 1 on managed networking, and on host-managed networking the networks the
    replay's tenant 1 pass writes. A network that the C(managed_interfaces) of O(network) puts on the backend bond is
    not refused, unless O(owned_parts) leaves out V(interfaces). C(docs/explanation/rebuilds.md) says which networks the
    newly formed edge holds.
  - B(Unreplayable state.) Fails when the edge has state that the replay cannot create again, unless O(acknowledge) lists
    its area. V(network) is a STATIC frontend network or a VLAN while O(network_declared) is false. V(kmip) is a key
    store other than the local one (G-KMS). V(replication) is a source or target relationship that
    O(replication_declared) does not declare, and any object relationship, each listed by its id, its peer's UUID and
    both paths (G-REPL-PEER). V(s3_keys) is any S3 access key and V(access_tokens) any access token (G-S3-KEY, G-TOKEN).
    V(local_data) is any file, link or other object outside the portal's spoke roots, and any snapshot. The files are
    counted from the cluster's aggregates, and a directory they count empty, as they may for some seconds after a
    write, is listed instead. Its details say C(files:) or C(snapshots:), and acknowledging the area gives up both.
  - B(Declared relationships.) A record matches an entry of O(replication_declared) by its side, its paths, and the
    peer's UUID, the current one or one of its previous ones. A relationship it declares with the edge as its target is
    replayable only while the edge's record is ESTABLISHED with its directory read-only and the source host's record of
    the same id is ESTABLISHED; its target directory is then not local data. In any other state, its directory is local
    data, listed whatever it holds, with a detail naming both records' states. A relationship it declares with the edge
    as its source, whose record is live, is held at the replay while the target holds a replica; its source directory is
    local data as any other, and the detail says whether the target host still holds a live replica. One whose record is
    ENDED or pending needs V(replication) acknowledged, since a declaration explains only a live record.
  - B(A rebuilt peer.) A record that names a previous UUID of its peer was made before the peer was rebuilt, and the
    detail says which previous UUID it names. With the edge as its source it is held. With the edge as its target, its
    outcome is V(only_copy). Its directory is local data, listed whatever it holds, and it is listed under
    V(replication) too, so both areas must be acknowledged. When the edge's record allows the source's restore
    (ESTABLISHED, read-only, with a recovery point) and the entry's C(peer_replication_address) is an IP literal, the
    detail names that restore first, the source's second pass for a source in C(qumulo_edges),
    C(playbooks/replication_restore.yml) for any other.
  - A record of a declared relationship whose peer UUID the peer host's inventory does not hold, current or previous,
    as before a rebuilt peer's host_vars_patch is applied, needs V(replication), and the detail says to apply that
    patch.
  - A snapshot whose owners are all replayable relationships, or held relationships out of the edge whose target holds
    a live replica, is not local data.
  - O(ignore_drift) never waives an area. Each acknowledged area that holds such state is returned in RV(acknowledged).
  - Names compare as the server compares them. Share names ignore case, and export paths the edge reports ignore a
    trailing slash, each within its tenant. Quota and portal root paths ignore a trailing slash. Tenant names compare
    exactly.
options:
  tenants:
    description:
      - The inventory's C(qumulo_tenants), as it is, present or absent. The entries are as the variables of
        C(qumulo.cluster.cluster_baseline) describe them, and the baseline's tenancy step reads them the same way.
      - A tenant the edge holds that the replay does not build again as the edge holds it is drift. An entry whose
        C(previous_name) the edge holds, while the edge does not hold its C(name), is a rename the inventory declares,
        so the edge's tenant of the old name is read by the new name, and so are its shares and exports. An entry whose
        name the edge already holds records a rename already done, and its previous name is ignored. An entry with
        C(state) of V(absent) renames nothing.
    type: list
    elements: dict
    default: []
  smb_shares:
    description:
      - The inventory's C(qumulo_smb_shares), as it is, present or absent. The entries are those of
        M(qumulo.cluster.smb_share), plus C(tenants) as the baseline reads it. The shares nested in an entry of
        O(tenants) are read with it, and are not passed here.
      - A flat entry is in each tenant its C(tenants) lists, in every tenant the inventory declares present for V(all),
        and absent in each tenant declared absent. One with neither C(tenants) nor C(tenant_id) is in the rebuilt
        edge's only tenant; once the replay has more than one tenant, counting the tenants the inventory creates, the
        module refuses it, as the baseline does. Its deprecated C(tenant_id) is read only for tenant 1; any other id is
        skipped with a warning.
    type: list
    elements: dict
    default: []
  nfs_exports:
    description:
      - The inventory's C(qumulo_nfs_exports), as it is, present or absent. The entries are those of
        M(qumulo.cluster.nfs_export), plus C(tenants) as the baseline reads it. The exports nested in an entry of
        O(tenants) are read with it, and are not passed here.
      - A flat entry is placed in a tenant as for O(smb_shares).
    type: list
    elements: dict
    default: []
  factory_defaults:
    description:
      - The inventory's C(qumulo_factory_defaults), as it is. The replay reads it as the baseline does. V(absent) for
        O(factory_defaults.smb_files_share) or O(factory_defaults.nfs_root_export) claims tenant 1's C(Files) share or
        C(/) export for removal, so an entry that also declares that share or export in tenant 1 claims one pair twice,
        and the module fails as the replay would.
    type: dict
    default: {}
    suboptions:
      smb_files_share:
        description: What becomes of the factory C(Files) share in tenant 1.
        type: str
        choices: [present, absent]
      nfs_root_export:
        description: What becomes of the factory C(/) export in tenant 1.
        type: str
        choices: [present, absent]
  network:
    description:
      - The inventory's C(qumulo_network), as it is, or null when the inventory declares none. The replay reads it as
        the baseline does, and the module uses it for the V(tenant_networks) drift area.
    type: dict
  owned_parts:
    description:
      - The parts of the network document the replay's baseline writes. The C(qumulo.cluster.edge_rebuild) role passes
        the baseline's C(cluster_baseline_owned_parts), the list the baseline gives M(qumulo.cluster.tenancy_plan).
      - An entry of O(tenants) that declares C(networks) while this leaves out V(networks) is refused before any read,
        since the run cannot give a network to a tenant. So is a listed network with C(floating_ip_ranges) while this
        leaves out V(floating_ips).
    type: list
    elements: str
    choices: [interfaces, networks, floating_ips]
    default: [interfaces, networks, floating_ips]
  management_network:
    description:
      - The ids of the networks this play reaches the edge on, as the baseline reads C(qumulo_management_network). The
        C(qumulo.cluster.edge_rebuild) role passes C(cluster_baseline_management_network), null when the variable is
        unset. A null value counts as unset.
      - On an edge that forms host-managed again, when the tenant 1 pass writes two or more networks, which it does only
        when tenant 1 holds two or more, or holds none while the inventory lists two or more, and the replay creates a
        tenant, this overrides the network the module infers from the old edge, which RV(management_network) returns
        when this is unset. The module infers it only for an old edge with one HOST network whose network status answers,
        as when the inventory adds a network. When the two disagree, the module warns "qumulo_management_network names
        network 1, but the play reaches the old edge on network 0; the replay uses qumulo_management_network". With
        neither this nor an inference the module fails, with a message that starts "the replay's tenant 1 pass would
        write networks", or, when the play reaches the old edge on a network the pass does not write, "the play reaches
        this edge on network".
      - On that edge, when the replay creates a tenant and the tenant 1 pass writes a network, an id that the pass does
        not write fails too, and C(docs/explanation/rebuilds.md) says why. An id that the inventory lists for no tenant that
        stays gets a message that starts "qumulo_management_network names" and goes on "which the inventory lists for no
        tenant that stays". An id that step 3 adds later, such as a second tenant's own network, gets one that starts
        "qumulo_management_network names" and goes on "but the replay's tenant 1 pass writes only". A value declared
        with no network, V([]), fails as well, with a message that starts "qumulo_management_network is declared with no
        network", since step 2 would refuse it at the create after the destroy. Like the one above, each comes after any
        drift and O(ignore_drift) does not waive it.
    type: list
    elements: int
  terraform_owned_areas:
    description:
      - The areas Terraform owns, as the baseline's ownership step records them from C(qumulo_terraform_owned_areas), or,
        without it, from C(qumulo_managed_by). The C(qumulo.cluster.edge_rebuild) role passes them as
        C(cluster_baseline_owned).
      - Only V(network) is read. With it listed, the refusal of an inventory that lists no network for a tenant that
        stays, on an edge whose networking the host manages, ends by saying to take V(network) out of
        C(qumulo_terraform_owned_areas) for the rebuild, to list the edge's HOST network under a tenant in O(tenants) or
        in C(qumulo_network.frontend_networks), and to put V(network) back in C(qumulo_terraform_owned_areas) and remove
        the listing in the same change. The baseline's ownership step refuses C(qumulo_network) and a C(networks) key under a tenant while
        Terraform owns the area. Without V(network), the refusal says to list the HOST network.
    type: list
    elements: str
    default: []
  quotas:
    description: The paths of the directories whose quotas the inventory declares, present or absent.
    type: list
    elements: str
    default: []
  snapshot_policies:
    description: The snapshot policy names the inventory declares, present or absent.
    type: list
    elements: str
    default: []
  local_users:
    description: The local user names the inventory declares, present or absent.
    type: list
    elements: str
    default: []
  local_groups:
    description: The local group names the inventory declares, present or absent.
    type: list
    elements: str
    default: []
  portal_roots:
    description: The spoke root paths the inventory declares for the edge's portal.
    type: list
    elements: str
    default: []
  declared_absent:
    description:
      - What the inventory declares absent, each area under its own key, as in the example. One that the edge still
        holds is drift, since the edge then differs from its inventory. Tenants, shares and exports say C(state)
        V(absent) in their own entries instead.
    type: dict
    default: {}
    suboptions:
      quotas:
        description: The paths of the directories whose quotas the inventory declares absent.
        type: list
        elements: str
        default: []
      snapshot_policies:
        description: The snapshot policy names the inventory declares absent.
        type: list
        elements: str
        default: []
      local_users:
        description: The local user names the inventory declares absent.
        type: list
        elements: str
        default: []
      local_groups:
        description: The local group names the inventory declares absent.
        type: list
        elements: str
        default: []
  network_declared:
    description: Whether the inventory declares the network document (C(qumulo_network)), which the replay then applies.
    type: bool
    default: false
  acknowledge:
    description: Areas of unreplayable state to rebuild without. The rebuild report lists what each needs by hand afterwards.
    type: list
    elements: str
    choices: [network, kmip, replication, s3_keys, access_tokens, local_data]
    default: []
  replication_declared:
    description:
      - The replication relationships the inventory declares present with this edge as their source or their target,
        each with what its peer's inventory host reports. The C(qumulo.cluster.edge_rebuild) role reads the peers with
        C(qumulo.cluster.replication_info), once per peer host.
    type: list
    elements: dict
    default: []
    suboptions:
      side:
        description: The edge's side of the relationship.
        type: str
        required: true
        choices: [source, target]
      peer_host:
        description: The peer's inventory host, for the messages.
        type: str
        required: true
      peer_cluster_uuid:
        description: The peer's cluster UUID, the target's for a V(source) entry and the source's for a V(target) one.
        type: str
        required: true
      source_path:
        description: The relationship's source path.
        type: str
        required: true
      target_path:
        description: The relationship's target path.
        type: str
        required: true
      peer_previous_cluster_uuids:
        description:
          - The peer's previous cluster UUIDs, from its host's C(qumulo_previous_cluster_uuids). A record that names one
            of them is the record of a relationship made before the peer was rebuilt.
        type: list
        elements: str
        default: []
      peer_relationships:
        description:
          - The peer's status records on its own side, as C(qumulo.cluster.replication_info) returns them, its
            C(source_relationships) for a V(target) entry and its C(target_relationships) for a V(source) one. Null
            when the peer did not answer, which makes a V(target) entry local data and gives a V(source) one no
            replica to restore from.
        type: list
        elements: dict
      peer_replication_address:
        description:
          - The peer host's C(qumulo_replication_address). With V(peer_in_edges), it decides whether a V(target) entry's
            V(only_copy) detail names the source's restore first; that restore needs an IP literal.
        type: str
      peer_in_edges:
        description: Whether the peer host is in C(qumulo_edges), whose restore is its second pass.
        type: bool
        default: false
  ignore_drift:
    description: Report drift without failing. Unreplayable state still fails unless O(acknowledge) lists its area.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: GET /v1/multitenancy/tenants/, /v3/smb/shares/, /v3/nfs/exports/, /v1/files/quotas/status/,
    /v3/snapshots/policies/status/, /v1/users/, /v1/groups/, /v2/portal/spokes/ with the attributes of each spoke root,
    /v3/network, /v2/encryption/key-store/status, /v2/replication/source-relationships/status/,
    /v2/replication/target-relationships/status/, /v3/replication/object-relationships/, /v1/s3/access-keys/,
    /v1/auth/access-tokens/, /v1/files/%2F/aggregates/ and those of each directory above a spoke root,
    /v1/files/{ref}/entries/ of a directory the aggregates count empty, and /v4/snapshots/status/."
  - Needs the tenant list, which needs PRIVILEGE_TENANT_READ; a failure to read it fails the task.
  - The V(tenants) drift area, and the tenant each share and export is in, resolve through the multitenancy REST API,
    which Qumulo marks as preview; a later Qumulo Core release may change it.
  - "Registers: G-KMS, G-REPL-PEER, G-S3-KEY, G-TOKEN."
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task names another cluster. Set them once for the play
# with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Check that the edge holds nothing its inventory does not declare
  # The edge_rebuild role passes the inventory's tenants, shares, exports, factory defaults and network as they are, so
  # the preflight reads them as the replay's baseline will. Nested shares and exports stay inside their tenant's entry.
  qumulo.cluster.edge_rebuild_preflight:
    tenants: "{{ qumulo_tenants | default([]) }}"
    smb_shares: "{{ qumulo_smb_shares | default([]) }}"
    nfs_exports: "{{ qumulo_nfs_exports | default([]) }}"
    factory_defaults: "{{ qumulo_factory_defaults | default({}) }}"
    network: "{{ qumulo_network | default(none) }}"
    quotas: "{{ qumulo_quotas | default([]) | map(attribute='path') }}"
    snapshot_policies: "{{ qumulo_snapshot_policies | default([]) | map(attribute='policy_name') }}"
    local_users: "{{ qumulo_local_users | default([]) | map(attribute='name') }}"
    local_groups: "{{ qumulo_local_groups | default([]) | map(attribute='name') }}"
    declared_absent:
      quotas: >-
        {{ qumulo_quotas | default([]) | selectattr('state', 'defined') | selectattr('state', 'equalto', 'absent')
           | map(attribute='path') }}
      snapshot_policies: >-
        {{ qumulo_snapshot_policies | default([]) | selectattr('state', 'defined') | selectattr('state', 'equalto', 'absent')
           | map(attribute='policy_name') }}
      local_users: >-
        {{ qumulo_local_users | default([]) | selectattr('state', 'defined') | selectattr('state', 'equalto', 'absent')
           | map(attribute='name') }}
      local_groups: >-
        {{ qumulo_local_groups | default([]) | selectattr('state', 'defined') | selectattr('state', 'equalto', 'absent')
           | map(attribute='name') }}
    portal_roots: "{{ qumulo_portal.roots | default([]) | map(attribute='spoke_root') }}"
    network_declared: "{{ qumulo_network is defined }}"
    acknowledge: [access_tokens]

- name: Check an edge of two tenants whose inventory declares a shared share and removes one
  # Each entry is as qumulo_tenants, qumulo_smb_shares and qumulo_nfs_exports hold it. projects is nested in
  # engineering, backups is in both tenants, and scratch is declared absent there. The edge's engineering tenant may
  # still be named eng; the preflight reads it as engineering, since the entry renames it. A tenant the edge holds and
  # these entries do not declare is drift.
  qumulo.cluster.edge_rebuild_preflight:
    tenants:
      - name: Default
      - name: engineering
        previous_name: eng
        smb_shares:
          - {share_name: projects, fs_path: /engineering/projects}
        nfs_exports:
          - {export_path: /builds, fs_path: /engineering/builds}
    smb_shares:
      - {share_name: backups, fs_path: /backups, tenants: [Default, engineering]}
      - {share_name: scratch, fs_path: /scratch, tenants: [engineering], state: absent}

- name: Check an edge of one tenant whose inventory removes the scratch share and an old user
  # A name declared absent is still listed with the others; the edge still holding it is drift. A flat share or export
  # with no tenants is in the rebuilt edge's only tenant; once the replay has a second tenant the baseline refuses
  # it, and so does the preflight, so name the tenants there.
  qumulo.cluster.edge_rebuild_preflight:
    smb_shares:
      - {share_name: projects, fs_path: /projects}
      - {share_name: scratch, fs_path: /scratch, state: absent}
    local_users: [svc_backup, build_old]
    portal_roots: [/hub-data]
    declared_absent:
      local_users: [build_old]

- name: Rebuild without the edge's snapshots and its files outside the spoke roots
  # Acknowledging local_data gives up both the files and the snapshots.
  qumulo.cluster.edge_rebuild_preflight:
    smb_shares:
      - {share_name: projects, fs_path: /projects}
    portal_roots: [/hub-data]
    acknowledge: [local_data]
  register: preflight

- name: List what the rebuild gives up, for the hand-off
  ansible.builtin.debug:
    msg: "{{ preflight.unreplayable | selectattr('area', 'in', preflight.acknowledged) | list }}"

- name: Read the peer's replication records, through the peer's own connection
  qumulo.cluster.replication_info:
    gather_subset: [source, target]
    api_host: hq01.example.com
    api_password: "{{ vault_hq01_admin_password }}"
    expected_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
  register: hq01_replication

- name: Check an edge that is the target of a replication relationship the inventory declares
  qumulo.cluster.edge_rebuild_preflight:
    smb_shares:
      - {share_name: projects, fs_path: /projects}
    portal_roots: [/hub-data]
    replication_declared:
      - side: target
        peer_host: hq01
        peer_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd
        source_path: /projects
        target_path: /replicas/projects
        peer_replication_address: 10.0.0.10
        peer_in_edges: false
        peer_relationships: "{{ hq01_replication.replication.source_relationships }}"
  register: replication_preflight

- name: Show what the rebuild does with each replication relationship
  ansible.builtin.debug:
    msg: "{{ item.side }} {{ item.id }}: {{ item.outcome }}"
  loop: "{{ replication_preflight.replication }}"

- name: List what the inventory misses, without failing on it
  # Unreplayable state still fails unless acknowledge lists its area. This survey only reads, so it acknowledges
  # every area.
  qumulo.cluster.edge_rebuild_preflight:
    ignore_drift: true
    acknowledge: [network, kmip, replication, s3_keys, access_tokens, local_data]
  register: drift_survey

- name: Show each item the inventory misses
  ansible.builtin.debug:
    msg: "{{ item.area }}: {{ item.name }}{{ (' in tenant ' ~ item.tenant) if item.tenant is defined else '' }}"
  loop: "{{ drift_survey.drift }}"

- name: Collect what blocks a rebuild without stopping the play
  # A failure on drift, unreplayable state, the tenant 1 pass or the management network still returns drift,
  # unreplayable, acknowledged and replication; a failure on an inventory the replay would refuse returns none of them.
  qumulo.cluster.edge_rebuild_preflight:
    smb_shares:
      - {share_name: projects, fs_path: /projects}
  register: blockers
  ignore_errors: true

- name: Show the areas that would block the rebuild
  ansible.builtin.debug:
    msg: "{{ blockers.unreplayable | map(attribute='area') | unique | list }}"
  when: blockers is failed and blockers.unreplayable is defined
"""

RETURN = r"""
drift:
  description:
    - What the edge has that the inventory does not declare, or declares absent, each with its C(area), its C(name) and
      C(declared_absent). An entry of the V(smb_shares) or V(nfs_exports) area also has C(tenant), the name of the
      tenant the share or export is in, and its C(name) is the share's name or the export's path. An entry of the
      V(tenants) area has the tenant's name in C(name), and C(added) V(true) when it is a tenant the rebuilt edge would
      gain and the edge lacks. An entry of the V(tenant_networks) area has C(tenant), the tenant's name, and C(name),
      the network's id and its name, as in V(2 (eng-net\)). It also has C(added) V(true) when the edge holds the network
      in no tenant and the rebuilt edge would put it in that tenant, whether the inventory lists it or lists no network. A
      tenant that the inventory's C(previous_name) renames, and its shares and exports, are named by the new name.
  returned: success, and when the module fails on drift, unacknowledged unreplayable state, the tenant 1 pass or the management network
  type: list
  elements: dict
  sample:
    - {area: tenants, name: Corp, declared_absent: false}
    - {area: tenants, name: Default, declared_absent: false, added: true}
    - {area: tenant_networks, tenant: eng, name: 2 (eng-net), declared_absent: false}
    - {area: tenant_networks, tenant: Default, name: 1 (Default), declared_absent: false, added: true}
    - {area: smb_shares, tenant: Default, name: scratch, declared_absent: false}
unreplayable:
  description:
    - State the replay cannot create again, each with its C(area) and a C(detail), acknowledged or not.
  returned: success, and when the module fails on drift, unacknowledged unreplayable state, the tenant 1 pass or the management network
  type: list
  elements: dict
  sample: [{area: access_tokens, detail: 2 access tokens}]
replication:
  description:
    - Each replication relationship the edge holds and what the rebuild does with it. Its C(outcome) is V(replayable),
      V(local_data), V(held), V(only_copy) or V(unreplayable); C(detail) says why.
    - Each has C(id), C(side), C(outcome) and C(detail). A source or target record also has C(peer_cluster_uuid),
      C(source_path), C(target_path) and C(state). An object relationship has C(side) V(object) and no more.
    - A declared one also has C(peer), the peer's record of the same id (C(state), C(recovery_point),
      C(error_from_last_job)), when the peer holds one and the record names the peer's current UUID.
  returned: success, and when the module fails on drift, unacknowledged unreplayable state, the tenant 1 pass or the management network
  type: list
  elements: dict
  sample:
    - {id: "4", side: target, peer_cluster_uuid: 4b3c802f-8201-42b7-bb41-28f562cd8dfd, source_path: /projects,
       target_path: /replicas/projects, state: ESTABLISHED, outcome: replayable, detail: "...",
       peer: {state: ESTABLISHED, recovery_point: "2026-11-01T10:00:00Z", error_from_last_job: null}}
acknowledged:
  description:
    - The areas in RV(unreplayable) that O(acknowledge) lists, sorted.
  returned: success, and when the module fails on drift, unacknowledged unreplayable state, the tenant 1 pass or the management network
  type: list
  elements: str
  sample: [access_tokens]
management_network:
  description:
    - The ids of the networks the replay uses as C(qumulo_management_network) for its tenant 1 pass and its baseline.
      They are O(management_network) when set, else the ids inferred from this edge, else null.
    - The inferred ids are the networks that carry the address of C(qumulo_api_host) on this edge. O(management_network)
      says when the module infers them. Null means it did not infer one.
    - With O(management_network) unset, it is null when the module fails on the tenant 1 pass, as when the inventory
      lists no network for a tenant that stays, and when it fails on the management network without having found one.
      With O(management_network) set, it is that value whenever it is returned.
    - The C(qumulo.cluster.edge_rebuild) role records it in the predestroy record as C(management_network), with the
      C(qumulo_api_host) the module found it by as C(management_api_host).
  returned: success, and when the module fails on drift, unacknowledged unreplayable state, the tenant 1 pass or the management network
  type: list
  elements: int
  sample: [1]
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import PARTS
from ansible_collections.qumulo.cluster.plugins.module_utils.rebuild import Preflight
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import FACTORY_DEFAULTS_OPTIONS


def _names():
    return {"type": "list", "elements": "str", "default": []}


ARGUMENT_SPEC = {
    # The inventory's own entries, which tenancy_plan reads as the replay's baseline does.
    "tenants": {"type": "list", "elements": "dict", "default": []},
    "smb_shares": {"type": "list", "elements": "dict", "default": []},
    "nfs_exports": {"type": "list", "elements": "dict", "default": []},
    "factory_defaults": {"type": "dict", "default": {}, "options": FACTORY_DEFAULTS_OPTIONS},
    "network": {"type": "dict"},
    "owned_parts": {"type": "list", "elements": "str", "choices": list(PARTS), "default": list(PARTS)},
    "terraform_owned_areas": {"type": "list", "elements": "str", "default": []},
    "management_network": {"type": "list", "elements": "int"},
    "quotas": _names(),
    "snapshot_policies": _names(),
    "local_users": _names(),
    "local_groups": _names(),
    "portal_roots": _names(),
    "declared_absent": {
        "type": "dict",
        "default": {},
        "options": {"quotas": _names(), "snapshot_policies": _names(), "local_users": _names(), "local_groups": _names()},
    },
    "network_declared": {"type": "bool", "default": False},
    "acknowledge": {"type": "list", "elements": "str", "choices": ["network", "kmip", "replication", "s3_keys", "access_tokens", "local_data"], "default": []},
    "ignore_drift": {"type": "bool", "default": False},
    "replication_declared": {
        "type": "list",
        "elements": "dict",
        "default": [],
        "options": {
            "side": {"type": "str", "required": True, "choices": ["source", "target"]},
            "peer_host": {"type": "str", "required": True},
            "peer_cluster_uuid": {"type": "str", "required": True},
            "peer_previous_cluster_uuids": {"type": "list", "elements": "str", "default": []},
            "source_path": {"type": "str", "required": True},
            "target_path": {"type": "str", "required": True},
            "peer_relationships": {"type": "list", "elements": "dict"},
            "peer_replication_address": {"type": "str"},
            "peer_in_edges": {"type": "bool", "default": False},
        },
    },
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), Preflight)


if __name__ == "__main__":
    main()
