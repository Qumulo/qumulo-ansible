# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""tenancy_plan: the cluster_baseline role's step 2 for spec 014, computed from the inventory and the cluster's reads.

It sends no request. It owns the rules of the inventory's qumulo_tenants, the pairs each share and export entry claims,
the network document of step 3, and the NFSv4.1 end state of each tenant.
"""

from __future__ import annotations

import difflib
import functools
import ipaddress
from dataclasses import dataclass, field

from ansible_collections.qumulo.cluster.plugins.module_utils import continuity, network, nfs, smb, tenancy, upgrade_path
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import listed

# A tenant's networks take network_config's options but the tenant ones, since the entry they sit under names the
# tenant, and vlan_id and mtu, which put the network on a frontend VLAN of its own.
TENANT_NETWORK_OPTIONS = {
    **{key: option for key, option in network.NETWORK_OPTIONS.items() if key not in ("tenant", "tenant_id")},
    "vlan_id": {"type": "int"},
    "mtu": {"type": "int"},
}

# qumulo_factory_defaults, which tenancy_plan and the edge_rebuild preflight both take.
FACTORY_DEFAULTS_OPTIONS = {
    "smb_files_share": {"type": "str", "choices": ["present", "absent"]},
    "nfs_root_export": {"type": "str", "choices": ["present", "absent"]},
}

# qumulo_network.frontend_networks takes network_config's options with tenant_id not marked for removal: the plan warns
# once for each entry that sets it (spec US2-11), so Ansible's own deprecation warning would be a second.
FLAT_NETWORK_OPTIONS = {**network.NETWORK_OPTIONS, "tenant_id": {"type": "int"}}


@dataclass(frozen=True)
class Refusal:
    message: str
    names: tuple = ()  # the tenants, networks and entries the message names, as text


@dataclass
class Findings:
    """What the stages found: the refusals, and the warnings, each once in the order found."""

    refusals: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def refuse(self, message, *names):
        self.refusals.append(Refusal(message, tuple(str(name) for name in names)))

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)


@dataclass(frozen=True)
class Entry:
    """One qumulo_tenants entry, as the argument spec filled it."""

    index: int
    name: str
    previous_name: str | None  # None on an absent entry, whose previous_name tenant ignores
    present: bool
    switches: dict  # only the switches the entry sets
    networks: list | None  # None: the entry leaves networks out
    smb_settings: object  # None, "inherit" or a dict
    nfs_settings: object
    smb_shares: tuple
    nfs_exports: tuple
    identity: object

    @property
    def place(self):
        return f"qumulo_tenants[{self.index}] ({self.name})"


@dataclass(frozen=True)
class Read:
    """What step 2 read from the cluster."""

    tenants: tuple
    live: dict  # the network document in the wire shape desired_document reads
    current: dict  # the canonical network document, without network_info's tenant names
    status: object  # the status list, a continuity.Unavailable or a continuity.NotRead
    protection_type: str | None
    smb_shares: tuple
    nfs_exports: tuple
    nfs_overrides: dict  # a tenant's current name -> its override
    nfs_settings: dict


@dataclass(frozen=True)
class Inputs:
    entries: tuple
    read: Read
    flat_networks: list | None  # qumulo_network.frontend_networks; None when unset
    interfaces: dict | None  # qumulo_network.managed_interfaces; None when unset
    smb_shares: tuple
    nfs_exports: tuple
    factory: dict
    nfs_settings: dict | None
    declared: frozenset | None  # qumulo_management_network; None when unset
    cluster_port: int | None
    api_port: int
    cluster_kind: str | None
    allow: bool
    api_host: str
    api_host_after: str | None
    owned: frozenset
    params: dict


def tenant_entries(tenants):
    """qumulo_tenants as Entry items; an item without state is present, as the argument spec fills it."""

    def entry(index, item):
        present = (item.get("state") or "present") == "present"
        return Entry(
            index=index,
            name=item["name"],
            previous_name=item.get("previous_name") if present else None,
            present=present,
            switches={switch: item[switch] for switch in tenancy.SWITCHES if item.get(switch) is not None},
            networks=item.get("networks"),
            smb_settings=item.get("smb_settings"),
            nfs_settings=item.get("nfs_settings"),
            smb_shares=tuple(item.get("smb_shares") or ()),
            nfs_exports=tuple(item.get("nfs_exports") or ()),
            identity=item.get("identity"),
        )

    return tuple(entry(index, item) for index, item in enumerate(tenants))


def read_inputs(params):
    cluster = params["cluster"]
    if cluster.get("status") is not None and cluster.get("status_unavailable") is not None:
        raise QumuloError("internal error: cluster.status and cluster.status_unavailable are both given; the role passes one")
    if cluster.get("status") is not None:
        status = cluster["status"]
    elif cluster.get("status_unavailable") is not None:
        status = continuity.Unavailable(cluster["status_unavailable"])
    else:
        status = continuity.NotRead("the cluster_baseline role's step 2")
    live = network.wire_document(cluster["network"])
    read = Read(
        tenants=tuple(cluster["tenants"]),
        live=live,
        current=network.canonical_document(live),
        status=status,
        protection_type=cluster.get("protection_type"),
        smb_shares=tuple(cluster["smb_shares"]),
        nfs_exports=tuple(cluster["nfs_exports"]),
        nfs_overrides=cluster["nfs_overrides"],
        nfs_settings=cluster["nfs_settings"],
    )
    entries = tenant_entries(params["tenants"])
    flat = params.get("network") or {}
    declared = params.get("management_network")
    return Inputs(
        entries=entries,
        read=read,
        flat_networks=flat.get("frontend_networks"),
        interfaces=flat.get("managed_interfaces"),
        smb_shares=tuple(params["smb_shares"]),
        nfs_exports=tuple(params["nfs_exports"]),
        factory=params["factory_defaults"] or {},
        nfs_settings=params.get("nfs_settings"),
        declared=None if declared is None else frozenset(declared),
        cluster_port=params.get("cluster_port"),
        api_port=params["api_port"],
        cluster_kind=params.get("cluster_kind"),
        allow=params["allow_management_disruption"],
        api_host=params["api_host"],
        api_host_after=params.get("api_host_after"),
        owned=frozenset(params["owned_parts"]),
        params=params,
    )


@dataclass(frozen=True)
class Names:
    """The tenants by name after the renames step 3a makes."""

    renames: dict  # name on the cluster -> its new name, for each rename the run makes
    by_id: dict  # every cluster tenant's id -> its name after the renames
    creates: tuple  # present entries' names the run creates at 3a, in inventory order
    deletes: tuple  # absent entries' names the cluster holds after the renames, in inventory order
    declared: frozenset  # every entry's name, present or absent
    absent: frozenset  # the names of the absent entries
    present: tuple  # present entries' names, in inventory order

    def held(self):
        """The names on the cluster after the renames."""
        return frozenset(self.by_id.values())

    def at_step_3(self):
        """The tenants from step 3a to 20a: those the cluster holds after the renames, then the creates."""
        return (*sorted(self.held()), *self.creates)

    def final(self):
        """The tenants once the run is done."""
        return tuple(name for name in self.at_step_3() if name not in self.deletes)

    def known(self, name):
        """Whether a reference to the tenant name is valid: an entry declares it, or the cluster holds it after the renames."""
        return name in self.declared or name in self.held()

    def id_of(self, name):
        """The id of the tenant of name after the renames, or a tenancy.Pending for one the run creates."""
        return next((tenant_id for tenant_id, held in self.by_id.items() if held == name), tenancy.Pending(name))

    def name_of(self, tenant_id):
        """The name after the renames of the tenant of tenant_id, a Pending's name, or None for no tenant."""
        if isinstance(tenant_id, tenancy.Pending):
            return str(tenant_id)
        return None if tenant_id is None else self.by_id.get(tenant_id)


def rename_plan(entries, cluster_tenants, findings):
    """The names after the renames, refusing a rename still to be done whose new name the cluster holds (FR-004, FR-026).

    cluster_tenants is tenant_info's tenants.
    """
    before = {tenant["name"] for tenant in cluster_tenants}
    present = [entry for entry in entries if entry.present]
    for entry in present:
        if entry.previous_name is None or entry.name not in before or entry.previous_name not in before:
            continue
        follower = next((other for other in present if other is not entry and other.previous_name == entry.name), None)
        if follower is None or follower.index < entry.index and follower.name == entry.previous_name:
            continue  # without a follower it records a rename already done; a swap is refused once, from its first entry
        findings.refuse(
            f"qumulo_tenants[{entry.index}] renames {entry.previous_name} to {entry.name} and qumulo_tenants[{follower.index}] renames "
            f"{follower.previous_name} to {follower.name}, but the cluster holds both {entry.previous_name} and {entry.name}, so the "
            f"first rename would find {entry.name} there and rename nothing. Make each step of a swap or a chain of renames in a run "
            "of its own, through a name the cluster does not hold.",
            entry.previous_name,
            entry.name,
            follower.name,
        )
    renaming = {}
    for entry in present:
        if entry.previous_name is not None and entry.name not in before and entry.previous_name in before:
            renaming.setdefault(entry.previous_name, []).append(entry)
    for old, by in renaming.items():
        if len(by) > 1:
            findings.refuse(
                f"{listed(entry.place for entry in by)} both rename tenant {old}; a tenant takes one new name.",
                old,
                *(entry.name for entry in by),
            )
    renames = {old: by[0].name for old, by in renaming.items()}
    by_id = {tenant["id"]: renames.get(tenant["name"], tenant["name"]) for tenant in cluster_tenants}
    held = set(by_id.values())
    return Names(
        renames=renames,
        by_id=by_id,
        creates=tuple(entry.name for entry in present if entry.name not in held),
        deletes=tuple(entry.name for entry in entries if not entry.present and entry.name in held),
        declared=frozenset(entry.name for entry in entries),
        absent=frozenset(entry.name for entry in entries if not entry.present),
        present=tuple(entry.name for entry in present),
    )


def check_entries(inputs, names, findings):
    """The rules on qumulo_tenants' entries themselves (FR-026)."""
    seen = {}
    for entry in inputs.entries:
        if entry.name in seen:
            findings.refuse(
                f"qumulo_tenants[{seen[entry.name]}] and qumulo_tenants[{entry.index}] both declare tenant {entry.name}; declare each tenant once.", entry.name
            )
        seen.setdefault(entry.name, entry.index)
    for entry in inputs.entries:
        if entry.identity is not None:
            findings.refuse(
                f"{entry.place} sets identity, which is reserved for per-tenant identity, which is not supported yet. Remove it.",
                entry.name,
            )
        _check_settings_keys(entry, findings)
    if not names.final():
        findings.refuse(continuity.no_tenant_left(True, None, names.deletes), *names.deletes)
    if not names.creates:
        return
    first = names.creates[0]
    try:
        tenancy.create_identity_config(inputs.read.tenants)
    except QumuloError as e:
        findings.refuse(f"creating tenant {first}: {e}", first, *(tenant["name"] for tenant in inputs.read.tenants))
    if len(inputs.read.tenants) == 1:
        try:
            tenancy.check_second_tenant(first, inputs.read.current, inputs.read.tenants)
        except QumuloError as e:
            findings.refuse(str(e), first, inputs.read.tenants[0]["name"])


# The keys a tenant's override takes: those of the global settings. The argument spec cannot list them, since the
# override may also be inherit.
SETTINGS_KEYS = {"smb_settings": tuple(smb.SmbSettings.FIELDS), "nfs_settings": tuple(nfs.NfsSettings.FIELDS)}


def _check_settings_keys(entry, findings):
    """Refuse each key of a tenant's smb_settings or nfs_settings dict that the settings do not take, which step 18 or 19
    would otherwise drop."""
    for name, keys in SETTINGS_KEYS.items():
        settings = getattr(entry, name)
        if not isinstance(settings, dict):
            continue
        for key in sorted(set(settings) - set(keys)):
            if key == "state":
                hint = f"to give the tenant the global settings, write {name}: inherit."
            elif key == "tenant":
                hint = "the settings are those of the tenant whose entry holds them, so leave tenant out."
            else:
                near = difflib.get_close_matches(key, keys, n=1)
                hint = f"did you mean {near[0]}?" if near else f"it takes {listed(sorted(keys))}."
            findings.refuse(f"{entry.place} sets {name}.{key}, which {name} does not take; {hint}", entry.name)


@dataclass(frozen=True)
class Words:
    """An area's words for its shares or exports: SMB's or NFS's."""

    key: str  # the name of an entry, share_name or export_path
    noun: str  # the module's name for one, for a message
    variable: str  # the flat inventory variable; without its qumulo_ prefix, the option and the Entry field of the list
    items: str  # the key of the area's present items in the result
    settings: str  # the tenant's override, as the Entry field and the module that takes it
    factory: str  # the qumulo_factory_defaults key
    factory_name: str  # the factory share or export tenant 1 holds
    one: str  # the pair rule's remedy

    @property
    def field(self):
        return self.variable.removeprefix("qumulo_")

    def key_of(self, name):
        """The name as the cluster compares it: an SMB share's casefolded, an export path as the cluster stores it."""
        return name.casefold() if self.key == "share_name" else nfs.canonical_export_path(name)


SMB = Words("share_name", "SMB share", "qumulo_smb_shares", "shares", "smb_settings", "smb_files_share", "Files", "a share comes from one entry only")
NFS = Words("export_path", "NFS export", "qumulo_nfs_exports", "exports", "nfs_settings", "nfs_root_export", "/", "an export comes from one entry only")


def nested_place(words, item, tenant):
    return f"the {words.noun} {item.get(words.key)} of tenant {tenant}"


def flat_place(words, index, item):
    return f"{words.variable}[{index}] ({item.get(words.key)})"


def network_place(index, item):
    return f"qumulo_network.frontend_networks[{index}] (network {item['id']})"


def tenant_network_place(tenant, item):
    return f"network {item['id']} of tenant {tenant}"


def _unknown(place, name, names):
    """The refusal of a reference to a tenant no entry declares and the cluster does not hold, or None for a valid name."""
    if names.known(name):
        return None
    message = f"{place} names tenant {name}, which qumulo_tenants does not declare and the cluster does not hold."
    if name in names.renames:
        message += f" This run renames {name} to {names.renames[name]}; name {names.renames[name]}."
    return message


def check_references(inputs, names, findings):
    """The rules on how shares, exports and networks name their tenants (FR-026, FR-031)."""
    for entry in inputs.entries:
        for words, items in ((SMB, entry.smb_shares), (NFS, entry.nfs_exports)):
            for item in items:
                _check_nested(words, item, entry.name, names, findings)
    for words, items in ((SMB, inputs.smb_shares), (NFS, inputs.nfs_exports)):
        for index, item in enumerate(items):
            place = flat_place(words, index, item)
            if item.get("tenant") is not None:
                findings.refuse(f"{place} sets tenant, which a {words.variable} entry does not take; write tenants: [{item['tenant']}].", place)
                continue
            if item.get("tenants") is not None and item.get("tenant_id") is not None:
                findings.refuse(f"{place} sets both tenants and tenant_id; name its tenants once, with tenants.", place)
                continue
            if isinstance(item.get("tenants"), list):
                for name in item["tenants"]:
                    unknown = _unknown(place, name, names)
                    if unknown:
                        findings.refuse(unknown, place, name)
    for index, item in enumerate(inputs.flat_networks or []):
        place = network_place(index, item)
        name = item.get("tenant")
        if name is None:
            continue
        unknown = _unknown(place, name, names)
        if item.get("tenant_id") is not None:
            findings.refuse(f"{place} sets both tenant and tenant_id; give tenant alone.", place)
        elif unknown:
            findings.refuse(unknown, place, name)
        elif name in names.absent:
            findings.refuse(
                f"{place} names tenant {name}, which qumulo_tenants declares state: absent. List the network under a tenant "
                "that stays, or leave it out so the run removes it.",
                place,
                name,
            )


def _check_nested(words, item, tenant, names, findings):
    """A share or export under the entry of tenant: its tenant is the entry's (Question 7 of the build design)."""
    place = nested_place(words, item, tenant)
    for key in ("tenants", "tenant"):
        if item.get(key) is not None:
            findings.refuse(f"{place} sets {key}, but an entry under a tenant belongs to that tenant. Remove {key}.", place)
    tenant_id = item.get("tenant_id")
    if tenant_id is None:
        return
    other = names.by_id.get(tenant_id)
    if other is None or other == tenant:
        which = "no tenant on the cluster" if other is None else f"tenant {other}, the tenant it is under"
        findings.warn(f"{place}: tenant_id {tenant_id} is {which}, so tenant_id is dropped; remove it.")
        return
    findings.refuse(
        f"{place} sets tenant_id {tenant_id}, which is tenant {other}, but an entry under a tenant belongs to that tenant. "
        f"Move it under {other}, or remove tenant_id.",
        place,
        other,
    )


@dataclass(frozen=True)
class Claim:
    """A (tenant, name) pair an entry claims, present or for removal (FR-037)."""

    tenant: str | None  # None: the only tenant, for a flat entry with no tenant on a single-tenant run
    key: str  # the name as the cluster compares it
    present: bool
    place: str  # the entry, worded for a message
    name: str  # the name as the entry gives it


@dataclass(frozen=True)
class Area:
    """One area's claims, and the lists step 18 or 19 takes in order: removals, overrides, then the present items."""

    claims: tuple
    removals: list
    overrides: list
    items: list


NOT_PASSED = ("tenants", "tenant_id", "tenant")  # an entry's keys that name its tenants, which the item replaces


def claims(inputs, names, findings):
    """The SMB and NFS areas' claims and items, refusing a pair two entries claim and an absent tenant's leftovers."""
    areas = (_area(inputs, names, SMB, findings), _area(inputs, names, NFS, findings))
    only = _only(names)
    for name in names.deletes:
        left = []
        for words, area, held in ((SMB, areas[0], inputs.read.smb_shares), (NFS, areas[1], inputs.read.nfs_exports)):
            claimed = {claim.key for claim in area.claims if (claim.tenant or only) == name}
            left += [
                f"{words.noun} {item[words.key]!r}"
                for item in held
                if names.by_id.get(item.get("tenant_id")) == name and words.key_of(item[words.key]) not in claimed
            ]
        if left:
            findings.refuse(
                f"tenant {name} is state: absent but holds {listed(left)} that no entry declares or claims for it. Declare each "
                f"under {name}, or list {name} in the shared entry that has it, so the run removes it before the tenant.",
                name,
                *left,
            )
    return areas


@dataclass(frozen=True)
class Replay:
    """Step 2's inventory rules on a newly formed cluster, as a replay onto a rebuilt edge applies them."""

    names: Names
    only: str | None  # the tenant a flat entry with no tenant names; None with more than one
    smb: Area | None  # None once an earlier stage refuses
    nfs: Area | None
    findings: Findings
    bindings: dict | None  # network id -> tenant name as the inventory declares them; None when it declares no networks
    host_managed: tuple = ()  # what step 2's host-managed check refuses of the listed networks, which apply only on such an edge
    listings: tuple = ()  # the networks the inventory lists, as listed_networks gives them
    backend: frozenset = frozenset()  # the networks the inventory's owned managed_interfaces put on the backend bond

    def refusals_on(self, formed):
        """Step 2's refusal of a new network with no tenant on a run of more than one tenant, judged on formed, the ids of
        the frontend networks the newly formed edge holds when the replay's step 2 runs, which only a read of the old
        edge tells. A newly formed edge's backend bond is not known before it forms, so only the inventory's counts."""
        findings = Findings()
        _refuse_new_without_a_tenant(self.listings, self.names.at_step_3(), set(formed) | self.backend, findings)
        return findings.refusals


def replay(tenants, smb_shares, nfs_exports, factory_defaults, flat_network=None, owned_parts=None):
    """What the baseline's step 2 makes of the inventory's qumulo_tenants, qumulo_smb_shares, qumulo_nfs_exports,
    qumulo_factory_defaults and qumulo_network on a newly formed cluster, which holds tenant 1 as Default with the
    factory share and export and nothing else.

    The edge_rebuild preflight reads it, so it judges the old edge against what the replay will build. It runs plan()'s
    stages 1 to 3, the renames, the entries and references, and the claims, then the network rules that need no read,
    wanted_networks' checks of each listed network among them, and stops as plan() does after the first that refuses.
    owned_parts is the parts of the network document the baseline owns, cluster_baseline_owned_parts, which those
    network rules read. It returns the tenant step 3 leaves each listed network in, though not the network document
    itself. Step 2's host-managed check of the listed networks is returned apart, in host_managed, since whether the new
    edge is host-managed is the old edge's to say. The cloud refusal, the NFSv4.1 end state, the network document and
    the continuity verdicts read the new edge's network and NFS state, which only the replay has, and so does
    check_entries' second-tenant check: the formed tenant holds no network here, so that check passes. Step 2's refusal
    of a new network with no tenant needs the formed edge's frontend networks too, which the caller gives
    Replay.refusals_on. It models a fresh formation; a replay run again on a part-built edge sees the tenants the first
    run created.
    """
    formed = {"id": tenancy.FORMATION_TENANT_ID, "name": tenancy.FORMATION_TENANT_NAME, "identity_config_id": 1, "networks": []}
    held = {"tenant_id": tenancy.FORMATION_TENANT_ID}
    read = Read(
        tenants=(formed,),
        live={},
        current={"managed_interfaces": None, "frontend_networks": []},
        status=continuity.NotRead("a newly formed cluster"),
        protection_type=None,
        smb_shares=({SMB.key: SMB.factory_name, **held},),
        nfs_exports=({NFS.key: NFS.factory_name, **held},),
        nfs_overrides={},
        nfs_settings={},
    )
    flat_network = flat_network or {}
    inputs = Inputs(
        entries=tenant_entries(tenants),
        read=read,
        flat_networks=flat_network.get("frontend_networks"),
        interfaces=flat_network.get("managed_interfaces"),
        smb_shares=tuple(smb_shares),
        nfs_exports=tuple(nfs_exports),
        factory=factory_defaults or {},
        nfs_settings=None,
        declared=None,
        cluster_port=None,
        api_port=8000,
        cluster_kind=None,
        allow=False,
        api_host="",
        api_host_after=None,
        owned=frozenset(network.PARTS if owned_parts is None else owned_parts),
        params={},
    )
    findings = Findings()
    names = rename_plan(inputs.entries, read.tenants, findings)
    if not findings.refusals:
        check_entries(inputs, names, findings)
        check_references(inputs, names, findings)
    only = _only(names)
    if findings.refusals:
        return Replay(names=names, only=only, smb=None, nfs=None, findings=findings, bindings=None)
    smb, nfs_area = claims(inputs, names, findings)
    if findings.refusals:
        return Replay(names=names, only=only, smb=smb, nfs=nfs_area, findings=findings, bindings=None)
    union = listed_networks(inputs, names, frozenset(), findings)  # a newly formed edge has no backend bond
    if not findings.refusals and union:
        try:
            # Its checks of each listed network, such as a key in a part the run does not own, need no read.
            network.wanted_networks([_sent(listing) for listing in union], inputs.owned, place=lambda index: union[index].where)
        except QumuloError as e:
            findings.refuse(str(e))
    bindings = None if findings.refusals else _replayed_bindings(inputs, names, union, findings)
    host_managed = Findings()
    _check_host_managed(read, union, host_managed)
    # Step 2 plans the inventory's interfaces only when the run owns them.
    backend = frozenset(network.backend_networks({"managed_interfaces": inputs.interfaces}) if "interfaces" in inputs.owned else ())
    return Replay(
        names=names,
        only=only,
        smb=smb,
        nfs=nfs_area,
        findings=findings,
        bindings=bindings,
        host_managed=tuple(host_managed.refusals),
        listings=tuple(union),
        backend=backend,
    )


def _replayed_bindings(inputs, names, union, findings):
    """Each network the inventory lists to the tenant step 3 leaves it in, or None when it lists none.

    A listed network that step 3 sends with no tenant keeps the one the newly formed edge gives it, tenant 1, as
    merge_network keeps the cluster's tenant. With no network listed step 3 sends no document, and the formed edge's
    networks, all tenant 1's, stay tenant 1's, so deleting tenant 1 then keeps them in a tenant the run deletes.
    """
    first = names.by_id[tenancy.FORMATION_TENANT_ID]
    if inputs.flat_networks is None and all(entry.networks is None for entry in inputs.entries if entry.present):
        if first in names.deletes:
            findings.refuse(
                f"tenant {first} is state: absent but holds the newly formed edge's networks, which this run keeps: no present "
                "tenant declares networks and qumulo_network.frontend_networks is unset, so the run sends no network document. "
                "Declare networks under every present tenant, or set qumulo_network.frontend_networks, listing each network "
                "under a tenant that stays.",
                first,
            )
        return None
    if first in names.deletes:
        for listing in union:
            if listing.tenant is None:
                findings.refuse(
                    f"tenant {first} is state: absent but holds network {listing.item['id']}, kept in "
                    f"qumulo_network.frontend_networks[{listing.flat}]. Give the network a tenant that stays, or leave it out so the "
                    "run removes it.",
                    first,
                    listing.item["id"],
                )
        if findings.refusals:
            return None
    return {listing.item["id"]: listing.tenant or first for listing in union}


def names_after_renames(tenants, cluster_tenants):
    """Each of cluster_tenants' ids to its name after the renames qumulo_tenants declares, as rename_plan reads them."""
    return rename_plan(tenant_entries(tenants), cluster_tenants, Findings()).by_id


def _only(names):
    """The run's one tenant at steps 3 to 19, which a flat entry with no tenant names; None with more."""
    tenants = names.at_step_3()
    return tenants[0] if len(tenants) == 1 else None


def _key(words, item, place, findings):
    """The entry's name as the cluster compares it, or None when it is refused."""
    if not isinstance(item.get(words.key), str):
        findings.refuse(f"{place} gives no {words.key}.", place)
        return None
    try:
        return words.key_of(item.get(words.key))
    except QumuloError as e:
        findings.refuse(f"{place}: {e}", place)
        return None


def _area(inputs, names, words, findings):
    found, staying, items = [], [], []
    leaving = {name: [] for name in names.deletes}  # each absent tenant the cluster holds: its removals, in order
    absent_in_order = [entry.name for entry in inputs.entries if not entry.present]
    for entry in inputs.entries:
        for item in getattr(entry, words.field):
            place = nested_place(words, item, entry.name)
            key = _key(words, item, place, findings)
            if key is None:
                continue
            present = entry.present and item.get("state", "present") == "present"
            found.append(Claim(entry.name, key, present, place, item[words.key]))
            removal = {words.key: item[words.key], "tenant": entry.name, "state": "absent"}
            if not entry.present:
                if entry.name in leaving:  # an absent tenant the cluster lacks has nothing to remove
                    leaving[entry.name].append(removal)
            elif not present:
                staying.append(removal)
            else:
                items.append({**{k: v for k, v in item.items() if k not in NOT_PASSED}, "tenant": entry.name})
    shared_leaving = []
    for index, item in enumerate(getattr(inputs, words.field)):
        place = flat_place(words, index, item)
        tenants = item.get("tenants")
        if tenants is None and item.get("tenant_id") is not None:
            tenant_id = item["tenant_id"]
            if tenant_id not in names.by_id:
                findings.warn(
                    f"{place}: tenant_id {tenant_id} is no tenant on the cluster, since tenant ids are never reused, so the entry is "
                    "skipped; name its tenants with tenants."
                )
                continue
            tenants = [names.by_id[tenant_id]]
            findings.warn(f"{place}: tenant_id {tenant_id} is translated to tenant {tenants[0]}; tenant_id is deprecated, so write tenants: [{tenants[0]}].")
        key = _key(words, item, place, findings)
        if key is None:
            continue
        name, present = item[words.key], item.get("state", "present") == "present"
        passed = {k: v for k, v in item.items() if k not in NOT_PASSED}
        if tenants is None:
            if _only(names) is None:
                findings.refuse(
                    f"{place} names no tenant, but this run has more than one tenant ({listed(names.at_step_3())}). Name them with "
                    "tenants, such as tenants: all.",
                    place,
                )
                continue
            found.append(Claim(None, key, present, place, name))
            if present:
                items.append(passed)
            else:
                staying.append({words.key: name, "state": "absent"})
            continue
        effective, gone = _listed(tenants, place, names, absent_in_order, findings)
        if effective is None:
            continue
        found += [Claim(tenant, key, present, place, name) for tenant in effective]
        found += [Claim(tenant, key, False, place, name) for tenant in gone]
        if effective and present:
            items.append({**passed, "tenants": effective})
        elif effective:
            staying.append({words.key: name, "tenants": effective, "state": "absent"})
        deleted = [tenant for tenant in names.deletes if tenant in gone]
        if deleted:
            shared_leaving.append({words.key: name, "tenants": deleted, "state": "absent"})
    factory = _factory(inputs, names, words, found, findings)
    removals = list(staying)
    for tenant in names.deletes:
        removals += leaving[tenant]
        removals += [removal for removal in shared_leaving if removal["tenants"][0] == tenant]
    removals += factory
    _check_pairs(words, found, _only(names), findings)
    return Area(tuple(found), removals, _overrides(inputs, words, findings), items)


def _listed(tenants, place, names, absent_in_order, findings):
    """A shared entry's effective list and the absent tenants it claims for removal; (None, None) when it is refused."""
    if tenants == "all":
        if not names.present:
            findings.refuse(f"{place} sets tenants: all, but qumulo_tenants declares no present tenant. Declare the tenants, or name them in tenants.", place)
            return None, None
        return list(names.present), absent_in_order
    if not isinstance(tenants, list):
        findings.refuse(f"{place} sets tenants to {tenants!r}; give a list of tenant names, or all.", place)
        return None, None
    if not tenants:
        findings.refuse(f"{place}: tenants is empty; name at least one tenant.", place)
        return None, None
    repeated = sorted({tenant for tenant in tenants if tenants.count(tenant) > 1})
    if repeated:
        findings.refuse(f"{place}: tenants names {listed(repeated)} more than once.", place, *repeated)
        return None, None
    return [tenant for tenant in tenants if tenant not in names.absent], [tenant for tenant in tenants if tenant in names.absent]


def _factory(inputs, names, words, found, findings):
    """The factory share or export's removal, claimed for tenant 1 (FR-028); [] when there is none to make."""
    option = f"qumulo_factory_defaults.{words.factory}"
    first = names.by_id.get(tenancy.FORMATION_TENANT_ID)
    if inputs.factory.get(words.factory) == "absent":
        if first is None:
            findings.warn(f"{option}: absent does nothing, since the cluster has no tenant 1, which held the factory {words.noun} {words.factory_name}.")
            return []
        found.append(Claim(first, words.key_of(words.factory_name), False, option, words.factory_name))
        return [{words.key: words.factory_name, "tenant": first, "state": "absent"}]
    held = inputs.read.smb_shares if words is SMB else inputs.read.nfs_exports
    fixed = words.key_of(words.factory_name)
    if (
        inputs.factory.get(words.factory) == "present"
        and first in names.deletes
        and any(item.get("tenant_id") == tenancy.FORMATION_TENANT_ID and words.key_of(item[words.key]) == fixed for item in held)
    ):
        findings.warn(
            f"tenant {first} is state: absent, so the factory {words.noun} {words.factory_name} goes with it; {option} has no "
            "effect on a tenant declared absent."
        )
    return []


def _check_pairs(words, found, only, findings):
    """FR-026: a (tenant, name) pair comes from one entry, nested, shared or factory, present or absent."""
    groups = {}
    for claim in found:
        groups.setdefault((claim.tenant or only, claim.key), []).append(claim)
    for group in groups.values():
        if len(group) > 1:
            tenant = group[0].tenant or only
            places = [claim.place for claim in group]
            findings.refuse(
                f"{tenant}'s {words.noun} {group[0].name} comes from {'both ' if len(group) == 2 else ''}{listed(places)}; {words.one}.",
                tenant,
                *places,
            )


def _overrides(inputs, words, findings):
    """Each present tenant's override, in inventory order: inherit removes it, and a dict sets the keys it gives."""
    out = []
    for entry in inputs.entries:
        settings = getattr(entry, words.settings)
        if not entry.present or settings is None:
            continue
        if settings == "inherit":
            out.append({"tenant": entry.name, "state": "inherit"})
        elif isinstance(settings, dict):
            out.append({"tenant": entry.name, "state": "present", **settings})
        else:
            findings.refuse(f"{entry.place} sets {words.settings} to {settings!r}; give the options of {words.settings}, or inherit.", entry.name)
    return out


def nfs_end_state(inputs, names, area, findings):
    """FR-016: refuse, before any write, an end state in which a tenant serves NFSv4.1 over two nested exports.

    Every tenant the run keeps is judged, declared or not (US2-19); an absent one is not, since step 19 removes its
    exports first (US2-18). A global enable is judged on the exports left after step 19's removals, and a tenant whose
    NFSv4.1 setting or exports the run changes on its exports at the end.
    """
    read, only = inputs.read, _only(names)
    entries = {entry.name: entry for entry in inputs.entries if entry.present}
    # The overrides are read under the names before the run, where a created tenant may reuse a renamed one's name.
    current_name = {new: old for old, new in names.renames.items()}
    global_now = bool(read.nfs_settings.get("v4_enabled"))
    given = (inputs.nfs_settings or {}).get("v4_enabled")
    global_end = global_now if given is None else bool(given)
    judged = []
    for tenant in names.final():
        now = {item["export_path"] for item in read.nfs_exports if names.by_id.get(item.get("tenant_id")) == tenant}
        mine = [claim for claim in area.claims if (claim.tenant or only) == tenant]
        after_removals = now - {claim.key for claim in mine if not claim.present}
        end = after_removals | {claim.key for claim in mine if claim.present}
        override = None if tenant in names.creates else read.nfs_overrides.get(current_name.get(tenant, tenant))
        v4_now = global_now if override is None else bool(override.get("v4_enabled"))
        v4_end = _v4_at_the_end(entries.get(tenant), override, global_end)
        judged.append((tenant, after_removals, end, v4_end and (v4_end != v4_now or end != now)))
    refused = set()
    if global_end and not global_now:
        for tenant, after_removals, _end, _turned_on in judged:
            pair = nfs.nested_pair(after_removals)
            if pair:
                refused.add(tenant)
                findings.refuse(
                    f"NFSv4.1 needs every export of tenant {tenant} outside every other, but qumulo_nfs_settings turns NFSv4.1 on "
                    f"while export '{pair[0]}' of tenant {tenant} contains '{pair[1]}'; the cluster checks every tenant, one whose "
                    "override keeps NFSv4.1 off included. Remove or move one of them first.",
                    tenant,
                    *pair,
                )
    for tenant, _after_removals, end, turned_on in judged:
        pair = nfs.nested_pair(end) if turned_on and tenant not in refused else None
        if pair:
            findings.refuse(
                f"NFSv4.1 needs every export of tenant {tenant} outside every other, but after this run tenant {tenant} serves "
                f"NFSv4.1 with export '{pair[0]}' containing '{pair[1]}'. Remove or move one of them, or keep v4_enabled off for {tenant}.",
                tenant,
                *pair,
            )


def _v4_at_the_end(entry, override, global_end):
    """Whether a tenant serves NFSv4.1 after step 19, which seeds a new override from the global settings it has just applied."""
    settings = None if entry is None else entry.nfs_settings
    if settings == "inherit":
        return global_end
    if isinstance(settings, dict) and settings.get("v4_enabled") is not None:
        return bool(settings["v4_enabled"])
    return global_end if override is None else bool(override.get("v4_enabled"))


def deletes(names):
    """Step 20a's items: each absent tenant the cluster holds, after its overrides."""
    return [{"name": name, "state": "absent"} for name in names.deletes]


@dataclass(frozen=True)
class NetworkPlan:
    """Step 3's network document, and what it does."""

    options: dict  # network_config's frontend_networks and managed_interfaces, each left out when unset
    planned: dict  # the canonical document after step 3's PUT
    changes: bool  # the PUT changes the cluster's document
    in_force: dict  # the document from step 3 on: planned when it changes, else the cluster's
    bindings: dict  # network id -> the name of its tenant after step 3, or None


@dataclass(frozen=True)
class Listed:
    """A network the inventory lists, in qumulo_network.frontend_networks or under a tenant."""

    item: dict  # as the argument spec filled it
    tenant: str | None  # the tenant it is sent with; None keeps the one the cluster reports
    place: str  # the network, worded for a message
    where: str  # its path in the inventory, which wanted_networks' messages start with
    vlan_id: int | None  # a tenant network's VLAN; None for the bond or a flat network
    flat: int | None  # its index in qumulo_network.frontend_networks; None under a tenant


BACKEND_TENANT = (
    "is on the backend bond, and a backend network takes no tenant: with two tenants a tenant network without a frontend "
    "interface aborts every node (G-TENANT-BACKEND)."
)
NOT_SENT = ("tenant", "tenant_id", "vlan_id", "mtu")  # the keys of an inventory network that step 3 sends another way, or not at all


def network_plan(inputs, names, findings):
    """Step 3's document, built from the tenants' networks and qumulo_network (the plan's "Networks"); None once refused.

    The document is built when a present tenant declares networks or the flat list is set; otherwise network_config
    keeps the live networks. Its interfaces are derived only on managed networking, once an entry declares networks.
    """
    read, present = inputs.read, [entry for entry in inputs.entries if entry.present]
    built = any(entry.networks is not None for entry in present) or inputs.flat_networks is not None
    backend = network.backend_networks(network.desired_document(read.live, None, inputs.interfaces, inputs.owned))
    union = listed_networks(inputs, names, backend, findings)
    host_managed = read.current["managed_interfaces"] is None and all(item["type"] == "HOST" for item in read.current["frontend_networks"])
    if host_managed:
        _check_host_managed(read, union, findings)
    if findings.refusals:
        return None

    def where(index):
        return union[index].where

    wanted = None
    if built:
        try:
            wanted = network.wanted_networks([_sent(listing) for listing in union], inputs.owned, place=where)
        except QumuloError as e:
            findings.refuse(str(e))
            return None
        for entry, listing in zip(wanted, union):
            entry["tenant_id"] = None if listing.tenant is None else names.id_of(listing.tenant)
        if "networks" not in inputs.owned:
            _check_existing(read, union, findings)  # before desired_document, whose error names no inventory place
            if findings.refusals:
                return None
    interfaces = inputs.interfaces
    if built and not host_managed and "interfaces" in inputs.owned and any(entry.networks is not None for entry in inputs.entries):
        try:
            merged = network.canonical_document(network.desired_document(read.live, wanted, None, inputs.owned, where))
        except QumuloError as e:
            findings.refuse(str(e))
            return None
        interfaces = _interfaces(inputs, union, merged, findings)
    if findings.refusals:
        return None
    options = {}
    if built:
        options["frontend_networks"] = [_sent(listing) for listing in union]
    if interfaces is not None:
        options["managed_interfaces"] = interfaces
    try:
        planned = network.canonical_document(network.planned_document(read.live, wanted, interfaces, inputs.owned, where))
    except QumuloError as e:
        findings.refuse(str(e))
        return None
    changes = not network.same_document(planned, read.current)
    in_force = planned if changes else read.current
    held = {network_id: names.by_id.get(tenant["id"]) for tenant in read.tenants for network_id in tenant.get("networks") or []}
    if changes:
        bindings = network.bindings(planned, names.at_step_3(), names.name_of)
    else:
        bindings = {item["id"]: held.get(item["id"]) for item in in_force["frontend_networks"]}
    _check_document(inputs, names, union if built else None, planned, changes, in_force, bindings, held, findings)
    return NetworkPlan(options, planned, changes, in_force, bindings)


def listed_networks(inputs, names, backend, findings):
    """The networks the inventory lists, each with the tenant step 3 sends it with, and FR-026's rules on them that need
    no read beyond backend, the ids of the backend bond's networks: once a present tenant declares networks every present
    tenant does, owned_parts owns networks for a tenant that lists one, a network is listed once, and an absent tenant's
    network is never kept flat."""
    present = [entry for entry in inputs.entries if entry.present]
    declaring = [entry for entry in present if entry.networks is not None]
    missing = [entry for entry in present if entry.networks is None]
    if declaring and missing:
        findings.refuse(
            f"{listed(entry.place for entry in missing)} {'leaves' if len(missing) == 1 else 'leave'} out networks, but "
            f"{declaring[0].place} declares them. Once a tenant declares networks, the tenants' networks and "
            "qumulo_network.frontend_networks are the whole set of frontend networks, so each present tenant declares its "
            "own, networks: [] for none.",
            *(entry.name for entry in missing),
        )
    if "networks" not in inputs.owned:
        for entry in present:
            if entry.networks:
                findings.refuse(
                    f"{entry.place} declares networks, but owned_parts leaves out networks, so the run cannot give a network "
                    "to a tenant. Add networks to owned_parts, or leave networks out of the entry.",
                    entry.name,
                )
    union = [_flat_network(index, item, names, backend, findings) for index, item in enumerate(inputs.flat_networks or [])]
    for entry in present:
        for index, item in enumerate(entry.networks or []):
            place = tenant_network_place(entry.name, item)
            union.append(Listed(item, entry.name, place, f"qumulo_tenants[{entry.index}].networks[{index}]", item.get("vlan_id"), None))
            if item["id"] in backend:
                findings.refuse(f"{place} {BACKEND_TENANT} List it in qumulo_network.frontend_networks instead.", place, entry.name)
    _check_listed(inputs, union, findings)
    return union


def _flat_network(index, item, names, backend, findings):
    """A qumulo_network.frontend_networks entry with the tenant it is sent with: its tenant, or its tenant_id's by name."""
    place = network_place(index, item)
    tenant, tenant_id = item.get("tenant"), item.get("tenant_id")
    if item["id"] in backend:
        if tenant is not None:
            findings.refuse(f"{place} {BACKEND_TENANT} Leave tenant out.", place, tenant)
        elif tenant_id is not None:
            findings.warn(f"{place}: network {item['id']} is on the backend bond, which takes no tenant, so tenant_id {tenant_id} is dropped.")
        tenant = None
    elif tenant is None and tenant_id is not None:
        tenant = names.by_id.get(tenant_id)
        if tenant is None:
            findings.warn(
                f"{place}: tenant_id {tenant_id} is no tenant on the cluster, since tenant ids are never reused, so it is dropped "
                "and the network keeps the tenant the cluster reports; name its tenant with tenant."
            )
        elif tenant in names.absent:
            findings.refuse(
                f"{place} sets tenant_id {tenant_id}, which is tenant {tenant}, and qumulo_tenants declares {tenant} state: absent. "
                "List the network under a tenant that stays, or leave it out so the run removes it.",
                place,
                tenant,
            )
        else:
            findings.warn(f"{place}: tenant_id {tenant_id} is translated to tenant {tenant}; tenant_id is deprecated, so write tenant: {tenant}.")
    return Listed(item, tenant, place, f"qumulo_network.frontend_networks[{index}]", None, index)


def _sent(listing):
    """The network as step 3 passes network_config: the inventory's keys, with the tenant by name when there is one."""
    sent = {key: value for key, value in listing.item.items() if value is not None and key not in NOT_SENT}
    return sent if listing.tenant is None else {**sent, "tenant": listing.tenant}


def _times(count):
    return "twice" if count == 2 else f"{count} times"


def _check_listed(inputs, union, findings):
    """FR-026: a network is listed once; one under an absent tenant moves to a present one, and is never kept flat."""
    where = {}
    for listing in union:
        found = f"under tenant {listing.tenant}" if listing.flat is None else f"in qumulo_network.frontend_networks[{listing.flat}]"
        where.setdefault(listing.item["id"], []).append(found)
    for network_id, found in where.items():
        if len(found) > 1:
            counted = [place if found.count(place) == 1 else f"{_times(found.count(place))} {place}" for place in dict.fromkeys(found)]
            findings.refuse(f"network {network_id} is listed {listed(counted)}; list each network once.", network_id)
    kept = {listing.item["id"]: listing for listing in union if listing.flat is not None and listing.tenant is None}
    for entry in inputs.entries:
        for item in [] if entry.present else entry.networks or []:
            if item["id"] in kept:
                findings.refuse(
                    f"network {item['id']} is listed under tenant {entry.name}, which qumulo_tenants declares state: absent, and "
                    f"kept in qumulo_network.frontend_networks[{kept[item['id']].flat}]. Leave it out of qumulo_network.frontend_networks "
                    "so the run removes it, or give it a tenant that stays.",
                    item["id"],
                    entry.name,
                )


def _check_existing(read, union, findings):
    """Without networks in owned_parts the run sets only floating addresses, so each listed network must be on the cluster."""
    existing = {item["id"] for item in read.current["frontend_networks"]}
    for listing in union:
        if listing.item["id"] not in existing:
            findings.refuse(
                f"{listing.where}: network {listing.item['id']} is not on the cluster, and this run does not own networks, so it adds no network.",
                listing.place,
            )


def _check_host_managed(read, union, findings):
    """US1-21: on networking the host manages, a tenant network takes neither a VLAN nor STATIC addressing."""
    live = {item["id"]: item["type"] for item in read.current["frontend_networks"]}
    for listing in union:
        if listing.flat is not None:
            continue
        if listing.vlan_id is not None:
            findings.refuse(
                f"{listing.place} has vlan_id {listing.vlan_id}, but the host manages this cluster's networking, so a tenant "
                "network takes no VLAN. Remove vlan_id.",
                listing.place,
            )
        if (listing.item.get("type") or live.get(listing.item["id"])) == "STATIC":
            findings.refuse(
                f"{listing.place} is STATIC, but the host manages this cluster's networking, so a tenant network is HOST. Give it type: HOST.",
                listing.place,
            )


def _family(item):
    """The IP version of a canonical network: its first node address, else its first floating one, else its netmask's."""
    for text in item["ip_ranges"] + item["floating_ip_ranges"]:
        return ipaddress.ip_address(text.partition("-")[0]).version
    return 6 if ":" in (item["netmask"] or "") else 4


def _count(count):
    return "two" if count == 2 else str(count)


def _interfaces(inputs, union, merged, findings):
    """managed_interfaces with the frontend VLANs and the bond's networks derived from the tenants' networks."""
    given, current = inputs.interfaces or {}, inputs.read.current["managed_interfaces"] or {}
    family = {item["id"]: _family(item) for item in merged["frontend_networks"]}
    named = {item["id"]: f"{item['id']} ({item['name']})" for item in merged["frontend_networks"]}
    tenant_networks = [listing for listing in union if listing.flat is None]
    by_vlan = {}
    for listing in tenant_networks:
        if listing.vlan_id is not None:
            by_vlan.setdefault(listing.vlan_id, []).append(listing)
    live_vlans = {vlan["vlan_id"]: vlan for vlan in current.get("frontend_vlans") or []}
    vlans = [_tenant_vlan(vlan_id, on, family, named, live_vlans.get(vlan_id), findings) for vlan_id, on in by_vlan.items()]
    flat_ids = {listing.item["id"] for listing in union if listing.flat is not None}
    if given.get("frontend_vlans") is not None:
        kept = list(given["frontend_vlans"])
        declared = "is in qumulo_network.managed_interfaces.frontend_vlans"
        remedy = "Remove it from frontend_vlans, or give the network another vlan_id."
    else:
        kept = [vlan for vlan in current.get("frontend_vlans") or [] if _ids(vlan) and _ids(vlan) <= flat_ids]
        declared, remedy = None, None
    for vlan in kept:
        on = by_vlan.get(vlan["vlan_id"])
        if not on:
            continue
        places = listed(listing.place for listing in on)
        if declared is None:
            networks = listed(f"network {network_id}" for network_id in sorted(_ids(vlan)))
            findings.refuse(
                f"VLAN {vlan['vlan_id']} carries {networks} of qumulo_network.frontend_networks, so the run keeps it, and is the "
                f"vlan_id of {places}; a VLAN is declared in one place. List the VLAN's networks under {on[0].tenant} with "
                f"vlan_id {vlan['vlan_id']}, or give the network another vlan_id.",
                vlan["vlan_id"],
                *(listing.place for listing in on),
            )
        else:
            # A VLAN that also carries a network outside the tenants' would be derived without it, so removing it from
            # frontend_vlans is no remedy there.
            outside = sorted(_ids(vlan) - {listing.item["id"] for listing in tenant_networks})
            if outside:
                remedy = _shared_vlan_remedy(outside, named, on[0].tenant, vlan["vlan_id"], " and remove the VLAN from frontend_vlans")
            findings.refuse(f"VLAN {vlan['vlan_id']} {declared} and is the vlan_id of {places}; a VLAN is declared in one place. {remedy}", vlan["vlan_id"])
    given_bond = given.get("frontend_bond_config") or {}
    base = given_bond.get("networks")
    if base is None:  # Question 9 of the build design: an empty base would drop every flat network from the bond
        base = (current.get("frontend_bond_config") or {}).get("networks") or []
    union_ids = {listing.item["id"] for listing in union}
    tenant_ids = {listing.item["id"] for listing in tenant_networks}
    untagged = {listing.item["id"] for listing in tenant_networks if listing.vlan_id is None}
    bond = {**given_bond, "networks": sorted((set(base) & union_ids) - tenant_ids | untagged)}
    frontend_vlans = sorted([*kept, *(vlan for vlan in vlans if vlan)], key=lambda vlan: vlan["vlan_id"])
    _check_live_vlans(current, merged, tenant_networks, bond["networks"], frontend_vlans, named, findings)
    return {**given, "frontend_bond_config": bond, "frontend_vlans": frontend_vlans}


def _shared_vlan_remedy(outside, named, tenant, vlan_id, also=""):
    """The remedy for VLAN vlan_id carrying tenant's network beside the networks outside, which no qumulo_tenants entry
    lists. A flat entry's tenant is no remedy: only a tenant network takes its VLAN from vlan_id."""
    return f"List {listed(f'network {named.get(n, n)}' for n in outside)} under {tenant} with vlan_id {vlan_id}{also}, or give each network a VLAN of its own."


def _check_live_vlans(current, merged, tenant_networks, bond, frontend_vlans, named, findings):
    """Refuse a cluster VLAN that carries a network the document keeps but puts on no interface.

    With frontend_vlans unset, the run keeps only the cluster's VLANs whose networks are all flat, and derives a tenant
    network's VLAN from its vlan_id alone, so a VLAN that also carries another network would lose it.
    """
    tenant_ids = {listing.item["id"] for listing in tenant_networks}
    kept_ids = {item["id"] for item in merged["frontend_networks"]}
    placed = set(bond).union(*(_ids(vlan) for vlan in frontend_vlans)) | network.backend_networks(merged)
    for vlan in current.get("frontend_vlans") or []:
        vlan_id = vlan["vlan_id"]
        dropped = sorted((_ids(vlan) & kept_ids) - tenant_ids - placed)
        if not dropped:
            continue
        on = [listing for listing in tenant_networks if listing.item["id"] in _ids(vlan)]
        networks = listed(f"network {named[network_id]}" for network_id in dropped)
        if on:
            (network_id,) = dropped  # a VLAN carries two networks at most, and one of them is a tenant's
            findings.refuse(
                f"VLAN {vlan_id} carries network {named[network_id]}, which no qumulo_tenants entry lists, beside "
                f"{listed(listing.place for listing in on)}, and the run derives a tenant network's VLAN from its vlan_id and "
                f"puts network {network_id} on no interface, so it would take network {network_id} off VLAN {vlan_id}. "
                + _shared_vlan_remedy(dropped, named, on[0].tenant, vlan_id),
                vlan_id,
                network_id,
            )
            continue
        ids = listed(f"network {network_id}" for network_id in dropped)
        findings.refuse(
            f"VLAN {vlan_id} carries {networks}, which the run keeps but puts on no interface, so it would take {ids} off "
            f"VLAN {vlan_id}. Keep VLAN {vlan_id} with {ids} in qumulo_network.managed_interfaces.frontend_vlans, or list "
            f"{'it' if len(dropped) == 1 else 'them'} under a tenant with vlan_id {vlan_id}.",
            vlan_id,
            *dropped,
        )


def _ids(vlan):
    return {vlan.get(key) for key in ("network_id", "secondary_network_id")} - {None}


def _tenant_vlan(vlan_id, on, family, named, live, findings):
    """The VLAN the tenant networks of vlan_id make, or None when refused (US1-11)."""
    ids = [listing.item["id"] for listing in on]
    refused = False
    for version in (4, 6):
        same = [network_id for network_id in ids if family[network_id] == version]
        if len(same) > 1:
            refused = True
            findings.refuse(
                f"VLAN {vlan_id} carries {_count(len(same))} IPv{version} networks, {listed(named[network_id] for network_id in same)}; "
                "a VLAN carries one network of each address family.",
                vlan_id,
            )
    mtus = [(listing.item["mtu"], listing.item["id"]) for listing in on if listing.item.get("mtu") is not None]
    values = {mtu for mtu, network_id in mtus}
    if len(values) > 1:
        refused = True
        findings.refuse(
            f"VLAN {vlan_id} is given {_count(len(values))} mtu values, "
            f"{listed(f'{mtu} by network {named[network_id]}' for mtu, network_id in mtus)}; give its networks one mtu.",
            vlan_id,
        )
    if refused:
        return None
    if len(ids) == 1:
        first, second = ids[0], None
    elif live is not None and _ids(live) == set(ids):
        first, second = live["network_id"], live["secondary_network_id"]  # the cluster's order, which a new VLAN need not keep
    else:
        first, second = sorted(ids, key=lambda network_id: family[network_id])
    vlan = {"vlan_id": vlan_id, "network_id": first, "secondary_network_id": second}
    return {**vlan, "mtu": mtus[0][0]} if mtus else vlan


def _check_document(inputs, names, union, planned, changes, in_force, bindings, held, findings):
    """The refusals of FR-026 on the document in force from step 3; union is None when the document is not built."""
    read = inputs.read
    warnings = []
    try:
        network.check_interfaces(read.current, in_force, names.name_of, warnings)
    except QumuloError as e:
        findings.refuse(str(e))
    if changes:  # with no network PUT the gateways are the cluster's own
        for message, ids in network.gateway_refusals(read.current, planned, warnings):
            findings.refuse(message, *ids)
    for warning in warnings:
        findings.warn(warning)
    existing = {item["id"] for item in read.current["frontend_networks"]}
    _refuse_new_without_a_tenant(union or [], names.at_step_3(), existing | network.backend_networks(planned), findings)
    flat = [listing for listing in union or [] if listing.flat is not None]
    named = {item["id"]: f"{item['id']} ({item['name']})" for item in [*read.current["frontend_networks"], *in_force["frontend_networks"]]}
    if any(entry.present and entry.networks is not None for entry in inputs.entries):
        # Only a present entry's networks brings this rule, so a flat list alone removes a network as network_config does.
        kept = {item["id"] for item in planned["frontend_networks"]}
        for item in read.current["frontend_networks"]:
            tenant = held.get(item["id"])
            if item["id"] not in kept and tenant is not None and tenant not in names.declared:
                findings.refuse(
                    f"This run removes network {named[item['id']]}, which tenant {tenant} holds, but qumulo_tenants does not "
                    f"declare {tenant}. Declare {tenant} and list the network under it, or keep the network in "
                    "qumulo_network.frontend_networks.",
                    item["id"],
                    tenant,
                )
    flat_index = {listing.item["id"]: listing.flat for listing in flat}
    for network_id, tenant in sorted(bindings.items()):
        if tenant not in names.deletes:
            continue
        if network_id in flat_index:
            kept = (
                f"kept in qumulo_network.frontend_networks[{flat_index[network_id]}]. "
                "Give the network a tenant that stays, or leave it out so the run removes it."
            )
        elif "networks" not in inputs.owned:
            kept = (
                "which this run keeps: owned_parts leaves out networks, so the run neither removes a network nor moves one "
                "to another tenant. Add networks to owned_parts."
            )
        else:
            # Only an unbuilt document gets here: a built one that owns networks holds just the listed networks, and a
            # network listed under a tenant is bound to that tenant.
            kept = (
                "which this run keeps: no present tenant declares networks and qumulo_network.frontend_networks is unset, so "
                "the run sends no network document. Declare networks under every present tenant, or set "
                "qumulo_network.frontend_networks, leaving the network out so the run removes it, or giving it a tenant that "
                "stays to move it."
            )
        findings.refuse(f"tenant {tenant} is state: absent but holds network {named[network_id]}, {kept}", tenant, network_id)


def _refuse_new_without_a_tenant(listings, tenants, existing, findings):
    """Refuse each flat network sent with no tenant that is not in existing, once the run has more than one tenant."""
    if len(tenants) < 2:
        return
    for listing in listings:
        if listing.flat is not None and listing.tenant is None and listing.item["id"] not in existing:
            findings.refuse(
                f"{listing.place} is a new network with no tenant, but this run has more than one tenant ({listed(tenants)}), "
                "so the cluster would hold it in no tenant and it would serve no client. Name its tenant with tenant, or "
                "list it under its tenant in qumulo_tenants.",
                listing.place,
            )


@dataclass(frozen=True)
class Write:
    """One tenant write or the network PUT that the run makes, in the step table's order."""

    step: str  # "3a", "3", "3b" or "20a"
    kind: str  # "rename", "enable", "create", "network", "disable" or "delete"
    tenant: str | None  # the tenant written, by its name after the write; None for the network PUT
    previous_name: str | None  # the tenant's name on the cluster, for a tenant the run renames
    switches: dict  # the switches the write sends

    @property
    def judged(self):
        """Whether the continuity rule judges the write: a create, a delete, the PUT, or a change of a REST switch."""
        return self.kind in ("create", "delete", "network") or any(switch in self.switches for switch in tenancy.REST_SWITCHES)

    @property
    def text(self):
        """A judged write as its writer words it in a refusal, so a refusal at step 2 reads like the writer's."""
        if not self.judged:
            raise AssertionError(f"Write.text words a judged write only, not {self}")
        if self.kind == "network":
            return "this network change"
        if self.kind in ("create", "delete"):
            return f"{'creating' if self.kind == 'create' else 'deleting'} tenant {self.tenant}"
        sent = " and ".join(f"{switch}: {str(self.switches[switch]).lower()}" for switch in tenancy.REST_SWITCHES if switch in self.switches)
        return f"setting {sent} on tenant {self.tenant}"

    def item(self):
        """The write as step 3a, 3b or 20a passes it to tenant, less the check-mode inputs."""
        if self.kind == "delete":
            return {"name": self.tenant, "state": "absent"}
        previous = {} if self.previous_name is None else {"previous_name": self.previous_name}
        return {"name": self.tenant, **previous, **self.switches}


@dataclass(frozen=True)
class State:
    """The tenants and the document immediately after a write."""

    tenants: dict  # each tenant's name -> its six switches
    bindings: dict  # network id -> the name of its tenant, or None
    in_force: dict  # the canonical document in force


def run_writes(inputs, names, step_3=None):
    """The run's writes in order, each with the state immediately after it (the plan's step table, FR-024).

    Enables go at 3a and disables at 3b, so a network moved at step 3 is in its new tenant before the old one loses a
    switch; a create sends every switch its entry sets, and tenant creates an unset one off. Without step_3, before the
    network stage has run, it lists the tenant writes alone.
    """
    read = inputs.read
    tenants = {names.by_id[tenant["id"]]: {switch: bool(tenant.get(switch)) for switch in tenancy.SWITCHES} for tenant in read.tenants}
    bindings = {network_id: names.by_id[tenant["id"]] for tenant in read.tenants for network_id in tenant.get("networks") or []}
    in_force = read.current
    previous = {new: old for old, new in names.renames.items()}
    existing = [entry for entry in inputs.entries if entry.present and entry.name not in names.creates]
    writes = []

    def add(write):
        nonlocal bindings, in_force
        if write.kind == "create":
            tenants[write.tenant] = {switch: bool(write.switches.get(switch)) for switch in tenancy.SWITCHES}
        elif write.kind == "delete":
            del tenants[write.tenant]
            bindings = {network_id: name for network_id, name in bindings.items() if name != write.tenant}
        elif write.kind == "network":
            bindings, in_force = dict(step_3.bindings), step_3.in_force
        else:
            tenants[write.tenant] = {**tenants[write.tenant], **write.switches}
        writes.append((write, State({name: dict(switches) for name, switches in tenants.items()}, dict(bindings), in_force)))

    def changed(entry, value):
        return {switch: value for switch, given in entry.switches.items() if given is value and tenants[entry.name][switch] is not value}

    for entry in existing:
        if entry.name in previous:
            add(Write("3a", "rename", entry.name, previous[entry.name], changed(entry, True)))
    for entry in existing:
        enables = changed(entry, True)
        if entry.name not in previous and enables:
            add(Write("3a", "enable", entry.name, None, enables))
    entries = {entry.name: entry for entry in inputs.entries if entry.present}
    for name in names.creates:
        add(Write("3a", "create", name, None, dict(entries[name].switches)))
    if step_3 is not None and step_3.changes:
        add(Write("3", "network", None, None, {}))
    for entry in existing:
        disables = changed(entry, False)
        if disables:
            add(Write("3b", "disable", entry.name, previous.get(entry.name), disables))
    for name in names.deletes:
        add(Write("20a", "delete", name, None, {}))
    return writes


def status_due(tenants, cluster_tenants):
    """Whether step 2 reads the cluster's network status (decision D26).

    tenants is the inventory's qumulo_tenants and cluster_tenants tenant_info's tenants. Status is read when the cluster
    holds two or more tenants or the run creates one: a present entry whose name the cluster lacks once the run's
    renames are made, as rename_plan counts creates. The role makes this test before tenancy_plan runs, so
    tenancy_plan runs once.
    """
    if len(cluster_tenants) >= 2:
        return True
    return bool(rename_plan(tenant_entries(tenants), cluster_tenants, Findings()).creates)


def first_pass(tenants, cluster_tenants, document, network=None):
    """What the edge_rebuild replay's tenant 1 pass declares on a newly formed edge, or {} when it runs none.

    On a document with no managed interfaces and no frontend network, step 2 refuses a second tenant's create (the
    continuity rule's first message), because the creates at 3a come before the network document at step 3. When the run
    creates a tenant on such a cluster, the pass gives the tenancy step tenant 1 alone, by its name after the renames
    (its entry's name and previous_name, or its name alone when no present entry declares it, as when it is declared
    absent), and tenant 1's own networks: those under its entry, and the flat networks step 2 resolves to tenant 1 or
    to no tenant. The baseline's step 3 then adds the other tenants' networks, and its step 2 places the play's address
    on tenant 1's. Only when tenant 1 holds none, as when it is declared absent, does the pass send every network the
    inventory lists that stays: each flat network not left to an absent tenant, and each present tenant's own networks.
    Every network goes flat with no tenant, which in a run of one tenant is tenant 1. It runs none when the cluster has
    managed interfaces or a frontend network, when the run creates no tenant, or when the inventory lists no network
    that stays, since step 2 would then refuse the create after the pass too.
    """
    if document.get("managed_interfaces") or document.get("frontend_networks"):
        return {}
    findings = Findings()
    entries = tenant_entries(tenants)
    names = rename_plan(entries, cluster_tenants, findings)
    first = names.by_id.get(tenancy.FORMATION_TENANT_ID)
    if not names.creates or first is None:
        return {}
    entry = next((item for item in entries if item.name == first and item.present), None)
    declared = {"name": first} if entry is None or entry.previous_name is None else {"name": first, "previous_name": entry.previous_name}
    network = dict(network or {})
    flat = [
        (_flat_network(index, item, names, frozenset(), findings).tenant, {key: value for key, value in item.items() if key not in ("tenant", "tenant_id")})
        for index, item in enumerate(network.get("frontend_networks") or [])
    ]
    # A tenant's network sent flat is named qumulo_network.frontend_networks[i] in the pass's own refusals; the preflight
    # makes the same checks first, naming the inventory's places.
    nested = {
        present.name: [{key: value for key, value in item.items() if key not in NOT_SENT} for item in present.networks or []]
        for present in entries
        if present.present
    }
    sent = [item for tenant, item in flat if tenant in (None, first)] + nested.get(first, [])
    if not sent:
        sent = [item for tenant, item in flat if tenant not in names.absent] + [item for items in nested.values() for item in items]
    if not sent:
        return {}
    network["frontend_networks"] = sent
    return {"tenants": [declared], "network": network}


def cloud_refusal(inputs, names, findings):
    """Refuse every tenant write on a cloud cluster, which no cloud cluster supports (FR-026).

    The cluster is cloud as spec 017's upgrade knows a cloud host: qumulo_cluster_kind, else an object-backed protection
    type. The network PUT is no tenant write. It runs straight after the renames, so the rules on a tenant the cloud
    cannot have never refuse first.
    """
    if upgrade_path.host_kind(inputs.cluster_kind, inputs.read.protection_type) != upgrade_path.CLOUD:
        return
    refused = [write for write, _state in run_writes(inputs, names)]
    if not refused:
        return
    why = f"qumulo_cluster_kind: {inputs.cluster_kind}" if inputs.cluster_kind else f"its protection type, {inputs.read.protection_type}, is object-backed"
    findings.refuse(
        f"This is a cloud cluster ({why}), and no cloud cluster supports tenancy: Qumulo Core refuses {listed(_described(write) for write in refused)} "
        "there. Remove them from qumulo_tenants. A cluster on premises whose storage is object-backed declares qumulo_cluster_kind: baremetal.",
        *dict.fromkeys(write.tenant for write in refused),
    )


def _described(write):
    """A tenant write in full, its rename and every switch it sends, where the judged text gives only the REST ones."""
    sent = " and ".join(f"{switch}: {str(value).lower()}" for switch, value in write.switches.items())
    if write.kind == "rename":
        return f"renaming tenant {write.previous_name} to {write.tenant}" + (f" with {sent}" if sent else "")
    if write.kind in ("enable", "disable"):
        return f"setting {sent} on tenant {write.tenant}"
    return write.text


@dataclass(frozen=True)
class Verdict:
    write: Write
    management: frozenset | None  # the management network after the write; None when it is not needed
    message: str | None


def judge(write, state, inputs, step_3, addresses):
    """The continuity rule on one judged write, as network_config's _judge makes it, with the role's inputs.

    addresses gives api_host's addresses, resolved once per run on the first write that needs them.
    """
    management, message = continuity.verdict(
        write=write.text,
        in_role=True,
        api_host=inputs.api_host,
        declared=inputs.declared,
        tenants=state.tenants,
        bindings=state.bindings,
        cluster_port=inputs.cluster_port,
        api_port=inputs.api_port,
        documents=lambda: (inputs.read.current, state.in_force),
        addresses=addresses,
        # Read or not, step 2's status is at hand, so a declared HOST network gets the device check too.
        status=lambda _in_force, _addresses: inputs.read.status,
        planned=step_3.planned if write.step == "3a" and step_3.changes else None,
        tenant=write.tenant,
    )
    return Verdict(write, management, message)


def continuity_checks(inputs, writes, step_3, findings):
    """Each write's verdict, None for a write not judged, with its refusal and step 3's address check in run order.

    allow_management_disruption skips the verdicts but not the address check, which then asks for api_host_after
    (FR-010, FR-026).
    """
    judging = not inputs.allow
    addresses = functools.cache(lambda: continuity.api_host_addresses(inputs.api_host))  # resolved on first need, once
    verdicts = []
    for write, state in writes:
        verdict = judge(write, state, inputs, step_3, addresses) if write.judged and judging else None
        verdicts.append(verdict)
        if verdict is not None and verdict.message and verdict.message not in (refusal.message for refusal in findings.refusals):
            findings.refuse(verdict.message, *([write.tenant] if write.tenant else []), *sorted(verdict.management or ()))
        if write.kind == "network":
            try:
                network.check_api_host_kept(inputs.params, inputs.read.current, step_3.planned, in_role=True)
            except QumuloError as e:
                findings.refuse(str(e), inputs.api_host)
    return verdicts


def writer_inputs(write, state, verdict, inputs):
    """The options a writer takes from step 2 for its own judgment, as they will be immediately after its write (FR-010).

    A judged write gets the management network, when its write leaves two or more tenants, and in check mode the
    tenants' REST and, for tenant, the bindings; with allow_management_disruption no write is judged, and none is
    given. A delete always gets content_removed, since steps 3, 18 and 19 remove the tenant's networks, shares and
    exports first, which tenant's check-mode delete reads whatever the rule says.
    """
    given = {}
    if verdict is not None:
        if verdict.management is not None:
            given["management_network"] = sorted(verdict.management)
        if write.kind != "network":
            given["planned_networks"] = {str(network_id): name for network_id, name in sorted(state.bindings.items()) if name is not None}
        needs = continuity.rest_switches(inputs.cluster_port, inputs.api_port)
        given["planned_tenants"] = {name: all(switches[switch] for switch in needs) for name, switches in state.tenants.items()}
    if write.kind == "delete":
        given["content_removed"] = True
    return given


def done(result, findings):
    return {**result, "refusals": [{"message": r.message, "names": list(r.names)} for r in findings.refusals], "warnings": list(findings.warnings)}


def plan(params):
    """Step 2's answer for the inventory and the cluster's reads in params, the module's.

    Each stage reads the model the earlier ones built, which a refusal may leave ill-defined, so the plan stops after
    the first stage that refuses.
    """
    findings, result = Findings(), {}
    inputs = read_inputs(params)
    names = rename_plan(inputs.entries, inputs.read.tenants, findings)
    if not findings.refusals:
        cloud_refusal(inputs, names, findings)
    if not findings.refusals:
        check_entries(inputs, names, findings)
        check_references(inputs, names, findings)
    if findings.refusals:
        return done(result, findings)
    smb, nfs_area = claims(inputs, names, findings)
    if findings.refusals:
        return done(result, findings)
    nfs_end_state(inputs, names, nfs_area, findings)
    if findings.refusals:
        return done(result, findings)
    result["smb"] = {"removals": smb.removals, "overrides": smb.overrides, SMB.items: smb.items}
    result["nfs"] = {"removals": nfs_area.removals, "overrides": nfs_area.overrides, NFS.items: nfs_area.items}
    result["step_20a"] = deletes(names)
    result["tenants"] = list(names.final())
    step_3 = network_plan(inputs, names, findings)
    if findings.refusals:
        return done(result, findings)
    result["network"] = step_3.options
    result["network_changes"] = step_3.changes
    result["bindings"] = {str(network_id): tenant for network_id, tenant in step_3.bindings.items()}
    writes = run_writes(inputs, names, step_3)
    verdicts = continuity_checks(inputs, writes, step_3, findings)
    steps = {"3a": [], "3b": [], "20a": []}
    for (write, state), verdict in zip(writes, verdicts):
        given = writer_inputs(write, state, verdict, inputs)
        if write.kind == "network":
            result["network"] = {**result["network"], **given}
        else:
            steps[write.step].append({**write.item(), **given})
    result["step_3a"], result["step_3b"], result["step_20a"] = steps["3a"], steps["3b"], steps["20a"]
    result["validate"] = step_3.changes and bool(steps["3a"])
    result["verdicts"] = [
        {
            "step": verdict.write.step,
            "write": verdict.write.text,
            "tenant": verdict.write.tenant,
            "management_network": None if verdict.management is None else sorted(verdict.management),
            "message": verdict.message,
        }
        for verdict in verdicts
        if verdict is not None
    ]
    return done(result, findings)
