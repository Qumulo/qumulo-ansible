# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The rebuild area: what an edge holds that its rebuild would lose, or could not create again."""

from __future__ import annotations

from functools import cached_property
from typing import NamedTuple

from ansible_collections.qumulo.cluster.plugins.module_utils import continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.access_tokens import ACCESS_TOKENS
from ansible_collections.qumulo.cluster.plugins.module_utils.auth import BUILT_IN_GROUPS, BUILT_IN_USERS, GROUPS, USERS
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import FACTORY_EXPORT, FACTORY_SHARE
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import listed
from ansible_collections.qumulo.cluster.plugins.module_utils.files import AGGREGATES, DIRECTORY, canonical_path, objects_under
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import KEY_STORE_STATUS
from ansible_collections.qumulo.cluster.plugins.module_utils.network import NETWORK, backend_networks, canonical_document, named_network, read_status
from ansible_collections.qumulo.cluster.plugins.module_utils.network import _untold as untold_network
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import EXPORTS
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import PORTAL_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.quotas import quota_statuses
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import (
    ENDED,
    ESTABLISHED,
    LIVE,
    OBJECTS,
    PENDING,
    SOURCE_STATUSES,
    TARGET_STATUSES,
    describe_source,
    describe_target,
    is_ip_literal,
    lower,
    snapshot_owners,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import ACCESS_KEYS as S3_ACCESS_KEYS
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SHARES
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import POLICY_STATUSES, SNAPSHOT_STATUSES
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy import FORMATION_TENANT_ID, FORMATION_TENANT_NAME, tenant_entries
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import SMB, first_pass, names_after_renames, replay

LOCAL_KEY_STORE = "Local"
UNREPLAYABLE_NETWORK = "STATIC"
TENANT_AREAS = ("smb_shares", "nfs_exports", "tenant_networks")  # the drift areas whose items are (tenant, name) pairs
REPLAY_REFUSES = "the replay onto the rebuilt edge would refuse the inventory: "
# The frontend network a cluster on managed networking forms with, Default on DHCP. The lab's lane-formed hardware holds
# its untagged network as id 1 (the findings register, item 37, and the lab put-back's model of a formed cluster); an
# edge VM on managed networking is not recorded.
FORMATION_NETWORK_ID = 1


def _networks(ids):
    """ids, worded for a message: network 0, networks 0 and 1."""
    return f"network{'' if len(ids) == 1 else 's'} {listed(str(network_id) for network_id in ids)}"


class Inference(NamedTuple):
    """The management network the preflight infers from this edge: networks, or why it cannot, and unwritten, the ids
    it found that the tenant 1 pass does not write."""

    networks: list | None
    why: str | None = None
    unwritten: tuple = ()


def _unplaced(status, document, addresses):
    """Why the network status places none of addresses on one network of this edge's document."""
    for address in addresses:
        untold = untold_network(status, document, address)
        if untold is not None and untold[0] == "HOST":
            return (
                f"this edge holds HOST {_networks(untold[1])} and the play reaches it on the host's own address, {address}, "
                "which the network status does not place on any one of them"
            )
    return "this edge's network status places the play's address on no one network"


def _plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


class Preflight:
    """edge_rebuild_preflight: read-only, fails when the edge holds drift or unacknowledged unreplayable state."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.replay = None
        self.document = None  # the edge's network document
        self.hints = []  # sentences the drift reason ends with
        self.declared = [_declared(entry) for entry in self.params["replication_declared"]]
        self.spoke_roots = []
        self.relationships = []

    def run(self):
        self._replay()
        self.document = self.client.get(NETWORK).data
        refusals = [*(self.replay.host_managed if self._host_managed_at_step_2() else ()), *self._formed_network_refusals()]
        if refusals:
            raise QumuloError(REPLAY_REFUSES + " ".join(refusal.message for refusal in refusals))
        drift = self._drift()
        unreplayable = self._unreplayable()
        acknowledged = sorted({entry["area"] for entry in unreplayable} & set(self.params["acknowledge"]))
        result = {"changed": False, "drift": drift, "unreplayable": unreplayable, "acknowledged": acknowledged, "replication": self.relationships}
        reasons = []
        lost = [entry for entry in drift if not entry.get("added")]
        gained_tenants = [entry for entry in drift if entry.get("added") and entry["area"] == "tenants"]
        gained_networks = [entry for entry in drift if entry.get("added") and entry["area"] == "tenant_networks"]
        if lost and not self.params["ignore_drift"]:
            reasons.append(". ".join([_drift_reason(lost), *self.hints]))
        if gained_tenants and not self.params["ignore_drift"]:
            reasons.append(_added_reason(gained_tenants))
        if gained_networks and not self.params["ignore_drift"]:
            reasons.append(_gained_networks_reason(gained_networks, self.replay.bindings is not None))
        refused = [entry for entry in unreplayable if entry["area"] not in acknowledged]
        if refused:
            reasons.append(_unreplayable_reason(refused))
        first_network = self._first_network_reason() or self._management_network_reason()
        if first_network:
            reasons.append(first_network)
        result["management_network"] = self._management_network()
        if reasons:
            raise QumuloError(". ".join(reasons), **result)
        return result

    def _replay(self):
        """What the replay's baseline builds from the inventory on the newly formed edge; fails on what it would refuse."""
        for index, tenant in enumerate(self.params["tenants"]):
            if not isinstance(tenant.get("name"), str):
                raise QumuloError(f"qumulo_tenants[{index}] gives no name")
            if tenant.get("state", "present") not in ("present", "absent"):
                raise QumuloError(f"qumulo_tenants[{index}] ({tenant['name']}) sets state to {tenant['state']!r}; give present or absent")
            _require_ids(tenant.get("networks") or [], f"qumulo_tenants[{index}].networks")
        _require_ids((self.params["network"] or {}).get("frontend_networks") or [], "qumulo_network.frontend_networks")
        params = self.params
        self.replay = replay(
            params["tenants"], params["smb_shares"], params["nfs_exports"], params["factory_defaults"], params["network"], params["owned_parts"]
        )
        refusals = self.replay.findings.refusals
        if refusals:
            raise QumuloError(REPLAY_REFUSES + " ".join(refusal.message for refusal in refusals))
        for warning in self.replay.findings.warnings:
            self.module.warn(f"the replay onto the rebuilt edge: {warning}")

    def _formed_network_refusals(self):
        """What the replay's step 2 would refuse once it knows the newly formed edge's frontend networks.

        The edge forms as this one is networked: with network 1 on managed networking, with none on host-managed
        networking. The replay's tenant 1 pass, where it runs, writes the networks it declares before the baseline's
        step 2, so those count as the formed edge's too. Today the pass runs only on host-managed networking and writes
        every network sent with no tenant, which step 2 leaves in tenant 1, so there this check cannot refuse; it guards
        a change in what the pass sends.
        """
        formed = set(self._pass_networks) if self._forms_host_managed() else {FORMATION_NETWORK_ID}
        return self.replay.refusals_on(formed)

    def _forms_host_managed(self):
        """Whether the edge forms again on host-managed networking: its document has no managed interfaces and only HOST networks."""
        networks = self.document.get("frontend_networks") or []
        return not self.document.get("managed_interfaces") and all((network.get("addresses") or {}).get("type") == "HOST" for network in networks)

    def _first_network_reason(self):
        """Why the replay would fail after the destroy: it creates a tenant on a host-managed edge but would run no tenant 1
        pass, which first_pass decides as the replay does; None otherwise. ignore_drift does not waive it.

        The edge forms again with no frontend network, so the replay writes tenant 1's networks, or every listed network
        when tenant 1 holds none, before the baseline's create, which step 2 otherwise refuses; with none listed it has
        nothing to write. With Terraform owning network the inventory can list none, so the remedy starts with taking
        network back.
        """
        if not self._forms_host_managed():
            return None
        built = self.replay.names
        if not built.creates or self._first_pass:
            return None
        created = f"tenant{'' if len(built.creates) == 1 else 's'} {listed(built.creates)}"
        opening = (
            f"the replay would create {created} on the newly formed edge, which like this edge is host-managed and forms with no "
            "frontend network, but the inventory lists no network for a tenant that stays, so the baseline's step 2 would refuse "
            "the create after the destroy. "
        )
        listing = "list the edge's HOST network under a tenant in qumulo_tenants, or in qumulo_network.frontend_networks"
        if "network" in self.params["terraform_owned_areas"]:
            return opening + (
                "Terraform owns network by qumulo_terraform_owned_areas, whose ownership check refuses qumulo_network and a "
                f"networks key under a tenant, so take network out of qumulo_terraform_owned_areas for the rebuild and {listing}; "
                "when network goes back into qumulo_terraform_owned_areas, take that listing out in the same change"
            )
        return opening + listing[0].upper() + listing[1:]

    def _management_network_reason(self):
        """Why the baseline after the tenant 1 pass would refuse after the destroy, unable to tell which network this play
        reaches the edge on; None otherwise. With no create the baseline runs one tenant and judges no management network.

        qumulo_management_network must name networks the pass writes, since step 2 judges the first create against the
        document the pass leaves. Without it, a pass of two or more HOST networks leaves step 2 no way to place the play's
        address among them, so the preflight infers the network from the old edge, the one that carries the play's address
        there, and refuses only when it cannot.
        """
        creates = self.replay.names.creates
        if not self._forms_host_managed() or not creates or not self._pass_networks:
            return None
        written = self._pass_networks
        created = f"tenant{'' if len(creates) == 1 else 's'} {listed(creates)}"
        writes = _networks(written)
        remedy = "Set qumulo_management_network to the id of the network qumulo_api_host is on"
        declared = self.params["management_network"]
        if declared is not None and not declared:
            return (
                "qumulo_management_network is declared with no network, which the baseline's step 2 would refuse at the "
                f"create after the destroy. {remedy}, or leave it unset"
            )
        unlisted = [network_id for network_id in declared or [] if network_id not in self._listed_networks]
        if unlisted:
            return (
                f"qumulo_management_network names {_networks(unlisted)}, which the inventory lists for no tenant that stays, "
                f"and the replay's tenant 1 pass writes only {writes} before it creates {created}, so the baseline's step 2 "
                f"could not find the network this play reaches the edge on and would refuse the create after the destroy. "
                "Set qumulo_management_network to the id the inventory gives the network qumulo_api_host is on"
            )
        later = [network_id for network_id in declared or [] if network_id not in written]
        if later:
            found = _networks(later)
            return (
                f"qumulo_management_network names {found}, but the replay's tenant 1 pass writes only {writes} before it "
                f"creates {created}, and the baseline's step 3 adds {found} only after that create, so the baseline's step 2 "
                f"would refuse the create after the destroy. Name the network of tenant 1 that qumulo_api_host is on, or, if "
                f"the play reaches the edge on {found}, set qumulo_api_host to an address on {_networks(written)} for the "
                "rebuild and qumulo_management_network to that network's id"
            )
        if len(written) < 2:
            return None
        inference = self._inferred_management
        if declared:
            if inference.networks is not None and sorted(declared) != inference.networks:
                self.module.warn(
                    f"qumulo_management_network names {_networks(declared)}, but the play reaches the old edge on "
                    f"{_networks(inference.networks)}; the replay uses qumulo_management_network"
                )
            return None
        if inference.networks is not None:
            return None
        if inference.unwritten:
            found = _networks(inference.unwritten)
            return (
                f"the play reaches this edge on {found}, which the replay's tenant 1 pass does not write before it creates "
                f"{created}, so the baseline's step 2 would refuse the create after the destroy. Set qumulo_api_host to an "
                f"address on one of {_networks(written)} for the rebuild and qumulo_management_network to that network's id, "
                f"or, if the inventory lists {found} under another id, set qumulo_management_network to that id"
            )
        return (
            f"the replay's tenant 1 pass would write {writes} into tenant 1 on the newly formed edge before it creates "
            f"{created}, and with more than one HOST network the baseline's step 2 cannot tell which one this play reaches the "
            f"edge on, nor can the preflight tell from this edge, since {inference.why}, so step 2 would refuse the create "
            f"after the destroy. {remedy}"
        )

    def _management_network(self):
        """The management network the replay gives its tenant 1 pass and baseline: qumulo_management_network when set,
        else the one inferred from this edge when the pass writes two or more networks, else None."""
        if self.params["management_network"] is not None:
            return sorted(self.params["management_network"])
        if not self._forms_host_managed() or not self.replay.names.creates or len(self._pass_networks) < 2:
            return None
        return self._inferred_management.networks

    @cached_property
    def _inferred_management(self):
        """The ids of this edge's networks that carry the play's address, as the continuity rule places it, or why it
        cannot place them, or the ids it found that the tenant 1 pass does not write.

        The rule places an address on a network whose ranges hold it, a HOST network's floating range among them, or,
        from the network status, on the document's one HOST network; with two HOST networks and the host's own address it
        cannot tell. A HOST network the status shows served on no device carries nothing, as step 2 checks.
        """
        document = canonical_document(self.document)
        addresses = continuity.api_host_addresses(self.client.params["api_host"])
        try:
            status = read_status(self.client)
        except QumuloError as e:
            return Inference(None, f"this edge's network status read failed: {e}")
        found = continuity.management_network(None, document, status, addresses, current=document)
        if found.undetermined == continuity.NO_ADDRESS:
            return Inference(None, "qumulo_api_host resolves to no address")
        if found.undetermined == continuity.STATUS_UNAVAILABLE:
            return Inference(None, "this edge does not answer its network status")
        if found.undetermined is not None or not found.networks:
            return Inference(None, _unplaced(status, document, addresses))
        off = continuity.hosts_on_device(found.networks, None, document, document, status)
        if off:
            return Inference(None, f"this edge's network status shows {_networks([item.network for item in off])} served on no device")
        inferred = sorted(found.networks)
        unwritten = tuple(network_id for network_id in inferred if network_id not in self._pass_networks)
        return Inference(None, None, unwritten) if unwritten else Inference(inferred)

    @cached_property
    def _first_pass(self):
        """What the replay's tenant 1 pass declares on the newly formed host-managed edge, as replay.yml asks for it."""
        formed = [{"id": FORMATION_TENANT_ID, "name": FORMATION_TENANT_NAME}]
        return first_pass(self.params["tenants"], formed, {"managed_interfaces": None, "frontend_networks": []}, self.params["network"])

    @cached_property
    def _listed_networks(self):
        """The ids of the networks the inventory lists for the tenants that stay, sorted. The baseline's step 3 writes them
        all, but its step 2 judges the creates against the document the tenant 1 pass leaves, so a management network must
        be one the pass writes; this tells an id no tenant lists from one step 3 adds only after the creates."""
        return sorted({listing.item["id"] for listing in self.replay.listings})

    @cached_property
    def _pass_networks(self):
        """The ids of the networks the tenant 1 pass writes on the newly formed host-managed edge, sorted; [] with no
        pass. The pass sends every network flat."""
        return sorted({item["id"] for item in (self._first_pass.get("network") or {}).get("frontend_networks") or []})

    def _host_managed_at_step_2(self):
        """Whether the newly formed edge is still host-managed when the baseline's step 2 judges the tenant networks: it
        forms so when this one is, unless the tenant 1 pass runs and writes managed interfaces the inventory declares."""
        if not self._forms_host_managed():
            return False
        return not ((self.params["network"] or {}).get("managed_interfaces") and self._first_pass)

    def _drift(self):
        client, params = self.client, self.params
        tenants = tenant_entries(client)
        names = names_after_renames(params["tenants"], tenants)
        drift = self._tenant_drift(tenants, names)
        live = {
            "smb_shares": (
                (names[share["tenant_id"]], share["share_name"])
                for share in client.get(SHARES).data["entries"]
                if not _factory_item(share, "share_name", FACTORY_SHARE)
            ),
            "nfs_exports": (
                (names[export["tenant_id"]], export["export_path"])
                for export in client.get(EXPORTS).data["entries"]
                if not _factory_item(export, "export_path", FACTORY_EXPORT)
            ),
            "quotas": (quota["path"] for quota in quota_statuses(client)),
            "snapshot_policies": (policy["policy_name"] for policy in client.get(POLICY_STATUSES).data["entries"]),
            "local_users": (user["name"] for user in client.get(USERS).data if user["name"] not in BUILT_IN_USERS),
            "local_groups": (group["name"] for group in client.get(GROUPS).data if group["name"] not in BUILT_IN_GROUPS),
            "portal_roots": self._live_spoke_roots(),
        }
        claimed = {"smb_shares": _claimed(self.replay.smb, self.replay.only), "nfs_exports": _claimed(self.replay.nfs, self.replay.only)}
        for area, items in live.items():
            key = _key(area)
            if area in claimed:
                declared_keys, absent_keys = claimed[area]
            else:
                absent_keys = {key(name) for name in params["declared_absent"].get(area) or []}  # declared_absent takes no portal_roots
                declared_keys = {key(name) for name in params[area]} - absent_keys
            for item in sorted(set(items), key=key):
                if key(item) in absent_keys or key(item) not in declared_keys:
                    drift.append({"area": area, **_named(area, item), "declared_absent": key(item) in absent_keys})
        return drift

    def _tenant_drift(self, tenants, names):
        """The tenants the replay does not build again as the edge holds them, or that the inventory declares absent; the
        networks of the others that the replay does not bind to them again; and tenant 1 when the replay adds it.

        Tenant 1 comes back only under the name the replay gives the newly formed edge's tenant 1; another tenant comes
        back when the replay ends with a tenant of its name.
        """
        built = self.replay.names
        first, final = built.by_id[FORMATION_TENANT_ID], set(built.final())
        drift, kept = [], []
        for tenant in sorted(tenants, key=lambda tenant: names[tenant["id"]]):
            name, is_first = names[tenant["id"]], tenant["id"] == FORMATION_TENANT_ID
            absent = name in built.absent
            if not absent and (name == first if is_first else name in final):
                kept.append(tenant)
                continue
            if is_first and not absent:
                self.hints.append(self._first_tenant_hint(name, first))
            drift.append({"area": "tenants", "name": name, "declared_absent": absent})
        if first in final and FORMATION_TENANT_ID not in names and first not in names.values():
            drift.append({"area": "tenants", "name": first, "declared_absent": False, "added": True})
        drift.extend(self._binding_drift(kept, names, first))
        drift.extend(self._unbound_drift(tenants, first))
        return drift

    def _unbound_drift(self, tenants, first):
        """The networks the edge holds in no tenant that the replay leaves in tenant 1, first.

        With networks listed, those are the ones the inventory lists with no tenant: step 3 leaves such a network in the
        tenant the newly formed edge or its tenant 1 pass gives it, or, with one tenant at step 3, in the only tenant. The
        refusal of a new network with no tenant has already failed the preflight for one the formed edge lacks on a replay
        of more than one tenant. With none listed, the replay keeps the formed edge's networks, which on managed
        networking is network 1, in tenant 1. The old edge's backend bond is left out here, though the formed edge's is
        not known (Replay.refusals_on), since a network on it takes no tenant on the old edge.
        """
        if self.replay.bindings is None:
            candidates = set() if self._forms_host_managed() else {FORMATION_NETWORK_ID}
        else:
            candidates = {listing.item["id"] for listing in self.replay.listings if listing.flat is not None and listing.tenant is None}
        bound = {network_id for tenant in tenants for network_id in tenant.get("networks") or []}
        frontend = {network["id"]: network["name"] for network in self.document.get("frontend_networks") or []}
        backend = backend_networks(self.document) | self.replay.backend
        return [
            {"area": "tenant_networks", "tenant": first, "name": named_network(network_id, frontend), "declared_absent": False, "added": True}
            for network_id in sorted(candidates)
            if network_id in frontend and network_id not in bound | backend
        ]

    def _binding_drift(self, kept, names, first):
        """The networks of each kept tenant that the replay binds elsewhere.

        With no network in the inventory, the replay keeps the newly formed edge's networks, all of them tenant 1's.
        """
        bound = self.replay.bindings
        titles = {network["id"]: network["name"] for network in self.document.get("frontend_networks") or []}
        drift = []
        for tenant in kept:
            name = names[tenant["id"]]
            for network_id in sorted(tenant.get("networks") or []):
                replayed = name == first if bound is None else bound.get(network_id) == name
                if not replayed:
                    drift.append({"area": "tenant_networks", "tenant": name, "name": named_network(network_id, titles), "declared_absent": False})
        return drift

    def _first_tenant_hint(self, name, first):
        """Why tenant 1, name on the edge, comes back as first, and what in the inventory changes that."""
        if first != FORMATION_TENANT_NAME:
            return f"Tenant 1 is {name} on the edge, but the replay renames the newly formed edge's tenant 1, {FORMATION_TENANT_NAME}, to {first}"
        if name in self.replay.names.present:
            return (
                f"Tenant 1 is {name} on the edge, and a newly formed edge names it {FORMATION_TENANT_NAME}; add previous_name: "
                f"{FORMATION_TENANT_NAME} to qumulo_tenants' entry for {name}, so the replay renames it"
            )
        return (
            f"Tenant 1 is {name} on the edge, but the rebuilt edge's tenant 1 will be {first}: a newly formed edge names it "
            f"{FORMATION_TENANT_NAME}, and the replay renames it only for a qumulo_tenants entry with previous_name: "
            f"{FORMATION_TENANT_NAME}"
        )

    def _live_spoke_roots(self):
        """The spoke roots of every portal the edge holds, kept for the local data check."""
        spokes = PORTAL_INFO_SUBSETS["spokes"](self.client)["spokes"]
        self.spoke_roots = [root["spoke_root"] for spoke in spokes for root in spoke["roots"] if root["spoke_root"]]
        return self.spoke_roots

    def _unreplayable(self):
        network = _network_details(self.document)
        # A declared network document is one the replay applies, whatever it holds.
        found = [] if self.params["network_declared"] else [{"area": "network", "detail": detail} for detail in network]
        key_store = self.client.get(KEY_STORE_STATUS).data.get("type")
        if key_store != LOCAL_KEY_STORE:
            found.append({"area": "kmip", "detail": f"the key store is {key_store}, not {LOCAL_KEY_STORE}"})
        found.extend({"area": "replication", "detail": detail} for detail in self._replication())
        for route, area, noun in ((S3_ACCESS_KEYS, "s3_keys", "S3 access key"), (ACCESS_TOKENS, "access_tokens", "access token")):
            count = len(self.client.paginate(route, "entries"))
            if count:
                found.append({"area": area, "detail": _plural(count, noun)})
        found.extend({"area": "local_data", "detail": detail} for detail in self._local_data())
        return found

    def _replication(self):
        """Classify each relationship the edge holds (spec User Story 3, scenario 8), and return the unreplayable ones' details.

        Fills self.relationships with each one's outcome: replayable, a declared target the replay rebuilds from its source
        in the second pass; local_data, a declared target in any other state, whose directory is then local data; held, a
        declared source whose record is live, whose replica on the target the replay holds; only_copy, a declared target
        whose source was rebuilt since, whose replica may be the only copy of that source's data and whose record is the
        hold on the source's side, so it needs both local_data and replication acknowledged; or unreplayable, every other
        relationship, which needs replication acknowledged.
        """
        sources = [describe_source(record) for record in self.client.get(SOURCE_STATUSES).data or []]
        targets = [describe_target(record) for record in self.client.get(TARGET_STATUSES).data or []]
        objects = self.client.get(OBJECTS).data or []
        details = []
        for side, records in (("source", sources), ("target", targets)):
            for record in records:
                declared, previous = self._declared_for(side, record)
                if not declared:
                    outcome = _undeclared(side, record, self._same_paths(side, record))
                elif side == "source" and record["state"] in (ENDED, PENDING):
                    outcome = _ended_or_pending(record)
                elif previous:
                    outcome = _rebuilt_peer(side, record, declared, previous)
                else:
                    outcome = _classify(side, record, declared)
                self.relationships.append(outcome)
                if outcome["outcome"] in ("unreplayable", "only_copy"):
                    details.append(outcome["detail"])
        for record in objects:
            detail = f"object relationship {record.get('id')}"
            self.relationships.append({"id": str(record.get("id")), "side": "object", "outcome": "unreplayable", "detail": detail})
            details.append(detail)
        return details

    def _declared_for(self, side, record):
        """The entry that declares record, and the peer's previous UUID the record names when it names no current one.

        A record made before its peer was rebuilt names the peer's old UUID, which the peer host's
        qumulo_previous_cluster_uuids holds.
        """
        peer = record["target_cluster_uuid"] if side == "source" else record["source_cluster_uuid"]
        key = (side, record["source_path"], record["target_path"])
        matches = [entry for entry in self.declared if entry["key"] == key]
        current = next((entry for entry in matches if entry["peer_cluster_uuid"] == peer), None)
        if current:
            return current, None
        return next(((entry, peer) for entry in matches if peer in entry["peer_previous_cluster_uuids"]), (None, None))

    def _same_paths(self, side, record):
        """An entry with record's side and paths, whatever peer it names: the declaration of a record whose peer was
        rebuilt, before that peer's host_vars_patch gives its inventory the UUID the record names."""
        return next((entry for entry in self.declared if entry["key"] == (side, record["source_path"], record["target_path"])), None)

    def _local_data(self):
        """Files outside the spoke roots and the replicas the replay rebuilds, then snapshots: the drain moves only the
        spoke roots' data to the hub, and a replica comes back with its full sync."""
        exempt = {canonical_path(root) for root in self.spoke_roots}
        exempt |= {item["target_path"] for item in self.relationships if item["outcome"] == "replayable"}
        # The target directories of declared relationships the replay cannot rebuild, listed whatever they hold.
        named = {item["target_path"]: item["detail"] for item in self.relationships if _replica_lost(item)}
        stops = exempt | set(named)
        details, directories = [], ["/"]
        while directories:
            directory = directories.pop(0)
            for child in self.client.get(AGGREGATES, params={"ref": directory}).data["files"]:
                path = canonical_path(directory.rstrip("/") + "/" + child["name"])
                if path in exempt:
                    continue
                if path in named:
                    details.append(f"files: {path} holds {_plural(self._count(child, path), 'file')}, {named[path]}")
                elif child["type"] != DIRECTORY:
                    details.append(f"files: the file {path}")
                elif any(stop.startswith(path + "/") for stop in stops):
                    directories.append(path)
                else:
                    count = self._count(child, path)
                    if count:
                        details.append(f"files: {path} holds {_plural(count, 'file')}")
        # A snapshot only relationships the replay keeps own, while a live replica stays on their peer, comes back with
        # the full sync, or goes with the source's local data (research R13); any other is a snapshot like any other.
        kept = {item["id"] for item in self.relationships if item["outcome"] == "replayable" or _held_with_live_replica(item)}
        snapshots = sum(1 for snapshot in self.client.paginate(SNAPSHOT_STATUSES, "entries") if not _owned_only_by(snapshot, kept))
        if snapshots:
            details.append(f"snapshots: {_plural(snapshots, 'snapshot')}")
        return details

    def _count(self, child, path):
        """The objects under a directory the aggregates list as child; listed when they count none, since they trail a write by
        some seconds."""
        count = sum(int(child[key]) for key in ("num_files", "num_symlinks", "num_other_objects"))
        return count or sum(1 for entry in objects_under(self.client, path))


def _declared(entry):
    """A replication_declared entry, keyed as the edge's records are matched against it, apart from the peer's UUID."""
    source_path = canonical_path(entry["source_path"], "a declared source_path")
    target_path = canonical_path(entry["target_path"], "a declared target_path")
    return {
        "key": (entry["side"], source_path, target_path),
        "peer_cluster_uuid": lower(entry["peer_cluster_uuid"]),
        "peer_previous_cluster_uuids": {lower(uuid) for uuid in entry.get("peer_previous_cluster_uuids") or []},
        "peer_host": entry["peer_host"],
        "peer_replication_address": entry.get("peer_replication_address"),
        "peer_in_edges": bool(entry.get("peer_in_edges")),
        # As replication_info returns them; only the id, the state, the recovery point and the last error are read.
        "peer_relationships": entry.get("peer_relationships"),
    }


def _name(side, record):
    if side == "source":
        return f"source relationship {record['id']} from {record['source_path']} to {record['target_cluster_uuid']}:{record['target_path']}"
    return f"target relationship {record['id']} from {record['source_cluster_uuid']}:{record['source_path']} into {record['target_path']}"


def _summary(side, record, outcome, detail, peer=None):
    summary = {
        "id": str(record["id"]),
        "side": side,
        "peer_cluster_uuid": record["target_cluster_uuid"] if side == "source" else record["source_cluster_uuid"],
        "source_path": record["source_path"],
        "target_path": record["target_path"],
        "state": record["state"],
        "outcome": outcome,
        "detail": detail,
    }
    if peer is not None:
        summary["peer"] = peer
    return summary


def _undeclared(side, record, same_paths):
    detail = f"{_name(side, record)}, which the inventory does not declare present ({record['state']})"
    if same_paths:
        peer = record["target_cluster_uuid"] if side == "source" else record["source_cluster_uuid"]
        host = same_paths["peer_host"]
        detail += (
            f"; an entry declares these paths with {host}, whose UUID and previous UUIDs do not include {peer}: if {host} was "
            f"rebuilt, apply {host}'s host_vars_patch first"
        )
    return _summary(side, record, "unreplayable", detail)


def _ended_or_pending(record):
    """A declared source whose record is ENDED or pending: the declaration explains only a live one, the lock on a
    replica the replay holds, so this one needs replication acknowledged (spec User Story 3, scenario 8)."""
    return _summary("source", record, "unreplayable", f"{_name('source', record)}, which the inventory declares, but whose record is {record['state']}")


def _peer_record(declared, record):
    """The peer host's record of the same id, None when it holds none, or the string 'unread' when the peer did not answer."""
    if declared["peer_relationships"] is None:
        return "unread"
    return next((peer for peer in declared["peer_relationships"] if str(peer["id"]) == str(record["id"])), None)


def _peer_text(host, peer):
    if peer == "unread":
        return f"{host} did not answer"
    if peer is None:
        return f"{host} holds no record of it"
    return f"{host}'s record is {peer['state']}"


def _classify(side, record, declared):
    """A relationship the inventory declares with its peer's current UUID, a source's record live: replayable, local_data or held."""
    host = declared["peer_host"]
    peer = _peer_record(declared, record)
    facts = None if isinstance(peer, str) or peer is None else {key: peer[key] for key in ("state", "recovery_point", "error_from_last_job")}
    if side == "source":
        if facts and peer["state"] in LIVE:
            detail = (
                f"{_name(side, record)} is held while {host} holds its replica, which may be the only copy of the source "
                f"directory: {host}'s record is {peer['state']}, recovery point {peer['recovery_point'] or 'none'}, "
                f"last error {peer['error_from_last_job'] or 'none'}"
            )
        else:
            detail = f"{_name(side, record)} is held while {host} holds a replica, but there may be none to restore from: {_peer_text(host, peer)}"
        return _summary(side, record, "held", detail, facts)
    read_only = record["target_root_read_only"] is True
    if record["state"] == ESTABLISHED and read_only and facts and peer["state"] == ESTABLISHED:
        detail = (
            f"{_name(side, record)} is replayable: the second pass creates it again from {host}, which holds it ESTABLISHED; "
            f"from the destroy until then, {host} has no replica of its source directory"
        )
        return _summary(side, record, "replayable", detail, facts)
    edge = f"the edge's record is {record['state']}" + ("" if read_only else ", its directory writable")
    detail = f"the target directory of {_name(side, record)}, which the replay cannot rebuild from its source: {edge}; {_peer_text(host, peer)}"
    return _summary(side, record, "local_data", detail, facts)


def _rebuilt_peer(side, record, declared, previous):
    """A declared relationship whose record names a previous UUID of its peer, which was rebuilt since the record was made.

    The peer's records belong to its new cluster, so none of them is this relationship's, whatever its id.
    """
    host = declared["peer_host"]
    rebuilt = f"{host} was rebuilt since, and the record names its previous UUID {previous}"
    if side == "source":
        return _summary(side, record, "held", f"{_name(side, record)} is held while {host} holds a replica, but there may be none to restore from: {rebuilt}")
    if _allows_restore(record, declared):
        if declared["peer_in_edges"]:
            restore = (
                f"ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l {host}, once {host}'s license applies, or at once if "
                f"{host} declares qumulo_capacity.license: none"
            )
        else:
            restore = f"ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l {host}"
        detail = (
            f"the target directory of {_name(side, record)}, which may hold the only copy of the data {host} had before it was rebuilt. "
            f"Restore {host} from it first: {restore}, which copies this replica back into {host}:{record['source_path']} and reverses the "
            "relationship, after which this rebuild finds it replayable. To give the replica up instead, acknowledge both local_data and "
            "replication."
        )
        return _summary(side, record, "only_copy", detail)
    detail = (
        f"the target directory of {_name(side, record)}, which may hold the only copy of the data {host} had before: {rebuilt}. "
        f"The edge's record is the hold on {host}'s side; rebuilding the edge ends it, and the second pass then creates the "
        f"relationship from {host}'s source directory as it is"
    )
    return _summary(side, record, "only_copy", detail)


def _allows_restore(record, declared):
    """Whether the source's restore can come back from the edge's record (spec User Story 8, scenario 13); the restore's
    own step 1 checks the rest."""
    readable = record["state"] == ESTABLISHED and record["target_root_read_only"] is True and bool(record.get("recovery_point"))
    return readable and is_ip_literal(declared["peer_replication_address"])


def _replica_lost(item):
    """Whether item is a declared replica on the edge that the replay cannot rebuild, whose directory is local data whatever it holds."""
    return item["side"] == "target" and item["outcome"] in ("local_data", "only_copy")


def _held_with_live_replica(item):
    return item["side"] == "source" and item["outcome"] == "held" and (item.get("peer") or {}).get("state") in LIVE


def _owned_only_by(snapshot, relationship_ids):
    owners = snapshot_owners(snapshot)
    return bool(owners) and owners <= relationship_ids


def _factory_item(item, field, factory_name):
    """The factory share or export, which only tenant 1 holds."""
    return item["tenant_id"] == FORMATION_TENANT_ID and item[field] == factory_name


def _claimed(area, only):
    """The (tenant, name) keys of an area's present claims, and of its claims for removal; a flat entry with no tenant
    claims for the only tenant."""
    present = {(claim.tenant or only, claim.key) for claim in area.claims if claim.present}
    return present, {(claim.tenant or only, claim.key) for claim in area.claims if not claim.present}


def _named(area, item):
    """A drift entry's name, and for a share or export its tenant."""
    if area in TENANT_AREAS:
        tenant, name = item
        return {"tenant": tenant, "name": name}
    return {"name": item}


def _key(area):
    """How the server compares names in an area: share names without case, paths without a trailing slash, each share and
    export within its tenant."""
    if area == "smb_shares":
        return lambda pair: (pair[0], SMB.key_of(pair[1]))
    if area == "nfs_exports":
        return lambda pair: (pair[0], canonical_path(pair[1]))
    if area in ("quotas", "portal_roots"):
        return canonical_path
    return str


def _network_details(document):
    details = [
        f"frontend network {network['id']} '{network['name']}' is {UNREPLAYABLE_NETWORK}"
        for network in document.get("frontend_networks") or []
        if network["addresses"]["type"] == UNREPLAYABLE_NETWORK
    ]
    vlans = ((document.get("managed_interfaces") or {}).get("frontend_vlans")) or []
    details.extend(f"VLAN {vlan['vlan_id']}" for vlan in vlans)
    return details


def _drift_reason(drift):
    undeclared = _listed(entry for entry in drift if not entry["declared_absent"])
    absent = _listed(entry for entry in drift if entry["declared_absent"])
    reasons = []
    if undeclared:
        reasons.append(
            f"the edge holds state its inventory does not declare, which a rebuild would lose: {undeclared}. "
            "Declare it in the inventory, or set ignore_drift to rebuild without it"
        )
    if absent:
        reasons.append(
            f"the edge still holds state its inventory declares absent: {absent}. "
            "Apply the inventory to the edge first, or set ignore_drift to rebuild without it"
        )
    return ". ".join(reasons)


def _require_ids(networks, place):
    """Refuses a listed network without an integer id, which every network rule keys on."""
    for index, item in enumerate(networks):
        if not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool):
            raise QumuloError(f"{place}[{index}] gives no id")


def _added_reason(added):
    """The reason the rebuilt edge would gain tenant 1, under the name the replay gives it, when the edge lacks one."""
    name, formed = added[0]["name"], FORMATION_TENANT_NAME
    if name == formed:
        kept, remedy = "the replay keeps it", f"Declare {formed}"
    else:
        kept, remedy = f"the replay renames it to {name} and keeps it", f"Drop qumulo_tenants' entry for {name}, or its previous_name, and declare {formed}"
    return (
        f"the rebuilt edge would gain tenant {name}, which this edge lacks: a newly formed edge has tenant 1, {formed}, with the "
        f"factory Files share and / export, and {kept}. {remedy} with state absent in qumulo_tenants, claim the factory share "
        "and export for removal, with qumulo_factory_defaults smb_files_share and nfs_root_export set to absent or by "
        "declaring them under that entry, and list each network under a tenant that stays; or set ignore_drift to rebuild "
        "with it"
    )


def _gained_networks_reason(gained, listed_networks):
    """The reason the rebuilt edge would put networks this edge holds in no tenant in tenant 1; listed_networks is
    whether the inventory lists any network."""
    first = gained[0]["tenant"]
    networks = listed(f"network {entry['name']}" for entry in gained)
    them, its = ("it", "its") if len(gained) == 1 else ("them", "each one's")
    opening = f"the rebuilt edge would put {networks}, which this edge holds in no tenant, in tenant {first}: "
    if not listed_networks:
        return opening + (
            f"the inventory lists no network, so the replay keeps the newly formed edge's network 1 in tenant 1, {first}. "
            "List it under its tenant in qumulo_tenants, or in qumulo_network.frontend_networks with tenant, with every "
            "other network the edge holds; or set ignore_drift to rebuild with it there"
        )
    return opening + (
        f"the inventory lists {them} with no tenant, and the replay leaves such a network in tenant 1, {first}. Name {its} "
        f"tenant with tenant, or list {them} under {its} tenant in qumulo_tenants; or set ignore_drift to rebuild with {them} there"
    )


def _listed(entries):
    areas = {}
    for entry in entries:
        tenant = f" in tenant {entry['tenant']}" if "tenant" in entry else ""
        areas.setdefault(entry["area"], []).append(f"'{entry['name']}'{tenant}")
    return "; ".join(f"{area} {', '.join(names)}" for area, names in areas.items())


def _unreplayable_reason(refused):
    areas = {}
    for entry in refused:
        areas.setdefault(entry["area"], []).append(entry["detail"])
    listed = "; ".join(f"{area} ({', '.join(details)})" for area, details in areas.items())
    names = " and ".join(areas)
    reason = (
        f"the edge holds state the replay cannot create again: {listed}. List {names} in acknowledge to rebuild without it and recreate it by hand afterwards"
    )
    if "network" in areas:
        reason += ", or declare qumulo_network"
    if "local_data" in areas:
        reason += (
            "; the drain moves only the portal's spoke roots to the hub, so copy local data off the edge first, and know that"
            " acknowledging local_data gives up both its files and its snapshots"
        )
    return reason
