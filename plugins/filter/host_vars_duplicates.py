# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.host_vars_duplicates, which plugins/filter/host_vars_duplicates.yml documents."""

from __future__ import annotations

import copy
from typing import NamedTuple

from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy
from ansible_collections.qumulo.cluster.plugins.module_utils.acl import share_rights
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_mode, canonical_path
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import parse_trustee
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import _restriction, _thirty_two_bit
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import MODES, _network_entry
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import NFS, SMB

FILE = "the file"
INVENTORY = "the inventory"
# Each variable whose entries claim a share, an export or a network, as the baseline's step 2 counts them.
CLAIMING = ("qumulo_tenants", "qumulo_smb_shares", "qumulo_nfs_exports", "qumulo_factory_defaults", "qumulo_network")
# A tenant entry's keys that are not its switches or settings.
NOT_TENANT_SETTINGS = ("name", "previous_name", "state", "networks", "smb_shares", "nfs_exports")
ONE_TENANT = "the cluster's one tenant"  # what a flat entry with no tenant claims while qumulo_tenants is unset
# An entry's keys that say where it goes or what a write does, not what the entry is.
NOT_COMPARED = ("tenants", "tenant_id", "tenant", "state", "create_fs_path")
ALL_RIGHTS = ["CHANGE_PERMISSIONS", "READ", "WRITE"]
ALL_REWRITTEN = "its tenants: all is written as the list of the other tenants, which does not cover a tenant added to the inventory later"
# What a share or export created with only its name, path and permissions or restrictions reads back with, in the
# canonical form below, as observed on Qumulo Core 7.10.3. A key without a value here has none to fall back to.
CREATE_DEFAULTS = {
    SMB.noun: {
        "description": "",
        "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ALL_RIGHTS}],
        "access_based_enumeration_enabled": False,
        "require_encryption": False,
        "expand_fs_path_variables": False,
        "offline_files_caching_mode": "NO_CACHING",
        "default_file_create_mode": "0644",
        "default_directory_create_mode": "0755",
    },
    NFS.noun: {"description": "", "fields_to_present_as_32_bit": []},
}
# nfs_export's defaults for the keys of a restriction.
RESTRICTION_DEFAULTS = {
    "host_restrictions": [],
    "read_only": False,
    "require_privileged_port": False,
    "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
    "user_mapping": "NFS_MAP_NONE",
}


class Claim(NamedTuple):
    """A share, export or network that one entry of the file or of the inventory declares."""

    key: tuple  # what is claimed, as the baseline tells two claims apart
    what: str
    origin: str  # FILE or INVENTORY
    place: str  # the entry, worded for a note or a refusal
    item: dict | None  # the entry; None for a factory removal
    removal: bool  # whether the entry removes what it claims
    at: tuple  # where the entry is in the file's variables, for leaving it out
    words: object = None  # SMB or NFS; None for a network


def host_vars_duplicates(variables, inventory, lacks_tenant_1=False, exclude=(), cluster_holds_entries=True, captured_tenants=None):
    """The file's variables without each share or export entry the inventory declares too, the note on each, and the
    refusal of each network both declare and of each entry the inventory declares with other values.

    The inventory's value of a variable counts only where the file leaves the variable out, since the file outranks it.
    A claim the baseline refuses for a reason of its own, such as an entry with no name, or no tenant on a run of
    several, or a tenant_id other than 1, which only the cluster can resolve, is not judged here. cluster_holds_entries
    is false for a fresh cluster, where the baseline creates each entry from the inventory's alone. captured_tenants is
    the captured qumulo_tenants when the file leaves it out because the inventory defines it.
    """
    effective = {}
    for name in CLAIMING:
        if name in variables:
            effective[name] = (variables[name], FILE)
        elif name not in exclude and inventory.get(name) is not None:
            effective[name] = (inventory[name], INVENTORY)
    tenants = effective.get("qumulo_tenants", (None,))[0]
    entries = [(position, entry) for position, entry in enumerate(tenants or []) if isinstance(entry, dict) and entry.get("name")]
    left_out, refusals, dropped, hidden = [], [], [], []
    if captured_tenants is not None and "qumulo_tenants" not in variables:
        # The file cannot hold the captured qumulo_tenants, so the cluster's tenants come from it, as the baseline reads
        # them from the cluster.
        view = _cluster_view(captured_tenants, entries, lacks_tenant_1)
        first, only = view.first, view.only
        if sorted(view.present) != sorted(view.kept):
            variables, notes = _all_as_listed(variables, view.kept)
            left_out += notes
        effective.update({name: (variables[name], FILE) for name in (SMB.variable, NFS.variable) if name in variables})
        hidden, notes, refused = _hidden_claims(captured_tenants, effective, view)
        left_out += notes
        refusals += refused
    else:
        first = _tenant_1(entries, lacks_tenant_1)
        present = [entry["name"] for _position, entry in entries if entry.get("state", "present") != "absent"]
        only = ONE_TENANT if not entries else (present[0] if len(present) == 1 else None)
    claims = _share_claims(effective, entries, first, only, SMB, "smb_shares") + _share_claims(effective, entries, first, only, NFS, "nfs_exports")
    claims += _network_claims(effective, entries)
    groups = {}
    for claim in claims + hidden:
        groups.setdefault(claim.key, []).append(claim)
    for group in groups.values():
        theirs = [claim for claim in group if claim.origin == INVENTORY]
        for ours in (claim for claim in group if claim.origin == FILE and theirs):
            verdict = _judge(ours, theirs[0], cluster_holds_entries)
            if isinstance(verdict, str):
                refusals.append(verdict)
                continue
            if ours.at[0] == "flat" and ours.item.get("tenants") == "all":
                verdict = {**verdict, "why": f"{verdict['why']}; {ALL_REWRITTEN}"}
            left_out.append(verdict)
            dropped.append(ours.at)
    names = [entry["name"] for _position, entry in entries if entry.get("state", "present") != "absent"]
    out = _without(variables, dropped, names)
    # A captured share or export the inventory does not declare goes in the file's flat list, for its tenant.
    for claim in hidden:
        variable, tenant = claim.at[1], claim.at[2]
        if any(other.origin == INVENTORY for other in groups[claim.key]):
            continue
        if variable not in variables:
            why = (
                f"the inventory defines both qumulo_tenants and {variable}, so a host_vars file can hold it in neither; declare it under "
                f"tenant {tenant} in the inventory"
            )
            left_out.append({"what": claim.what, "why": why})
            continue
        if out is variables:
            out = dict(variables)
        out[variable] = [*out[variable], {**claim.item, "tenants": [tenant]}]
    return {"variables": out, "left_out": left_out, "refusals": refusals}


class View(NamedTuple):
    """The cluster's tenants as the baseline reads them beside the inventory's qumulo_tenants, from the capture."""

    entry: dict  # each captured tenant's name: the inventory's entry for it, by name or by previous_name, or None
    names: dict  # each captured tenant's name: its name once the inventory's renames are done
    kept: list  # the captured tenants' names after the renames, less those the inventory removes
    first: str | None  # tenant 1's name, None when the cluster lacks it
    only: str | None  # the run's one tenant, which a flat entry with no tenants names; None with more
    present: list  # the present tenants the inventory declares, which tenants: all stands for beside it


def _cluster_view(captured_tenants, entries, lacks_tenant_1):
    inventory = [entry for _position, entry in entries]
    held = [tenant for tenant in captured_tenants or [] if isinstance(tenant, dict) and tenant.get("name") and tenant.get("state", "present") != "absent"]
    before = {tenant["name"] for tenant in held}
    by_name = {entry["name"]: entry for entry in inventory}
    # A rename the baseline makes: from a name the cluster holds to one it does not, as rename_plan reads an entry.
    by_previous = {entry["previous_name"]: entry for entry in inventory if entry.get("previous_name") and entry["name"] not in before}
    entry = {tenant["name"]: by_name.get(tenant["name"]) or by_previous.get(tenant["name"]) for tenant in held}
    names = {name: (found or {"name": name})["name"] for name, found in entry.items()}
    kept = [names[name] for name, found in entry.items() if found is None or found.get("state", "present") != "absent"]
    matched = [found for found in entry.values() if found is not None]
    created = [found["name"] for found in inventory if found.get("state", "present") != "absent" and not any(found is other for other in matched)]
    # The tenants the run holds from step 3, a tenant it removes included, which a flat entry with no tenants must be one of.
    run = list(names.values()) + [name for name in created if name not in names.values()]
    first = None
    if not lacks_tenant_1:
        # Tenant 1 is the cluster's; an inventory rename of it reaches it through names.
        default = tenancy.FORMATION_TENANT_NAME
        held_renamed = [names[tenant["name"]] for tenant in held if tenant.get("previous_name") == default]
        held_named = [names[tenant["name"]] for tenant in held if tenant["name"] == default and not tenant.get("previous_name")]
        first = (held_renamed + held_named + [None])[0]
    present = [name for name in by_name if by_name[name].get("state", "present") != "absent"]
    return View(entry, names, kept, first, run[0] if len(run) == 1 else None, present)


def _all_as_listed(variables, kept):
    """variables with each flat entry's tenants: all written as the cluster's tenants, with a note on each: beside the
    inventory's qumulo_tenants, all would mean the tenants the inventory declares. When the inventory removes every
    captured tenant, the entry is left out, since all would mean only tenants that never held it."""
    out, notes = variables, []
    for words in (SMB, NFS):
        items = variables.get(words.variable)
        if not isinstance(items, list) or not any(isinstance(item, dict) and item.get("tenants") == "all" for item in items):
            continue
        if out is variables:
            out = dict(variables)
        out[words.variable] = [
            {**item, "tenants": list(kept)} if isinstance(item, dict) and item.get("tenants") == "all" else item
            for item in items
            if kept or not (isinstance(item, dict) and item.get("tenants") == "all")
        ]
        for item in items:
            if not (isinstance(item, dict) and item.get("tenants") == "all"):
                continue
            if kept:
                why = (
                    "beside the inventory's qumulo_tenants, tenants: all would mean the tenants the inventory declares, so it is written as the "
                    f"cluster's tenants, {_listed(kept)}"
                )
                notes.append({"what": f"the tenants of the {words.noun} {item.get(words.key)}", "why": why})
            else:
                why = "its tenants: all would mean the tenants the inventory declares, and every tenant that holds it is one the inventory removes"
                notes.append({"what": f"the {words.noun} {item.get(words.key)}", "why": why})
    return out, notes


def _hidden_claims(captured_tenants, effective, view):
    """The claims of the shares and exports under each captured tenant, which the file cannot hold under it beside the
    inventory's qumulo_tenants, with the notes on the captured tenants the inventory does not declare or removes, and the
    refusals of a tenant the inventory declares with other values and of a tenant network it does not list as captured."""
    claims, notes, refusals = [], [], []
    declared = {entry["name"] for entry in (effective.get("qumulo_tenants", ([],))[0] or []) if isinstance(entry, dict) and entry.get("name")}
    for tenant in captured_tenants or []:
        if not isinstance(tenant, dict) or not tenant.get("name"):
            continue
        if tenant.get("state", "present") == "absent":
            if tenant["name"] not in declared:
                why = (
                    "it has a rebuild delete the Default a newly formed cluster has, and a host_vars file cannot hold qumulo_tenants beside the "
                    "inventory's, so it is not written; declare it in the inventory's qumulo_tenants"
                )
                notes.append({"what": f"the capture's {tenant['name']} with state: absent", "why": why})
            continue
        theirs, name = view.entry[tenant["name"]], view.names[tenant["name"]]
        if theirs is not None and theirs.get("state", "present") == "absent":
            why = (
                "the inventory declares it state: absent, so the next baseline deletes it once the inventory declares or claims each share and "
                "export it holds; what the capture holds under it is not written"
            )
            notes.append({"what": f"tenant {name}", "why": why})
            continue
        if theirs is None:
            why = (
                "the inventory's qumulo_tenants does not declare it, and a host_vars file cannot hold qumulo_tenants beside the inventory's, so "
                "its services and settings stay as the cluster has them; each share and export of it that the inventory does not declare is "
                "written in the flat lists, naming it in tenants"
            )
            # A rebuild forms tenant 1 as Default, and no other tenant.
            if name != tenancy.FORMATION_TENANT_NAME:
                why += "; a rebuild from the inventory refuses the entries that name it until qumulo_tenants declares it"
                if tenant.get("previous_name") == tenancy.FORMATION_TENANT_NAME:
                    why += " with previous_name: Default"
            notes.append({"what": f"tenant {name}", "why": why})
        else:
            # A key the capture does not hold, as a cloud cluster's switches, has nothing to compare, and null is unset.
            differ = [
                key
                for key, value in theirs.items()
                if key not in NOT_TENANT_SETTINGS and value is not None and key in tenant and not _settings_agree(value, tenant[key])
            ]
            if differ:
                refusals.append(
                    f"tenant {name}, which the inventory declares in qumulo_tenants[{name}] and the capture with other values of {', '.join(differ)}"
                )
        refusals += _hidden_networks(tenant, theirs, name, effective)
        for words, field in ((SMB, "smb_shares"), (NFS, "nfs_exports")):
            for index, item in enumerate(tenant.get(field) or []):
                key = _key(words, item.get(words.key)) if isinstance(item, dict) else None
                if key is not None:
                    place = f"qumulo_tenants[{tenant['name']}].{field}[{index}]"
                    what = f"the {words.noun} {item[words.key]} of tenant {name}"
                    claims.append(Claim((words.noun, name, key), what, FILE, place, item, False, ("hidden", words.variable, name), words))
    return claims, notes, refusals


def _hidden_networks(tenant, theirs, name, effective):
    """The refusal of each network the captured tenant holds that the inventory does not list for it, under its entry or
    flat with its name in tenant, or lists with other values: the baseline's network document would leave it out or
    rewrite it."""
    listed = {}
    for index, network in enumerate((theirs or {}).get("networks") or []):
        if isinstance(network, dict) and network.get("id") is not None:
            listed[network["id"]] = (network, f"qumulo_tenants[{theirs['name']}].networks[{index}]")
    flat, origin = effective.get("qumulo_network", (None, None))
    if origin == INVENTORY and isinstance(flat, dict):
        for index, network in enumerate(flat.get("frontend_networks") or []):
            if isinstance(network, dict) and network.get("id") is not None and network.get("tenant") == name:
                listed.setdefault(network["id"], (network, f"qumulo_network.frontend_networks[{index}]"))
    refusals = []
    for network in tenant.get("networks") or []:
        if not isinstance(network, dict) or network.get("id") is None:
            continue
        if network["id"] not in listed:
            refusals.append(f"network {network['id']}, which tenant {name} holds and the inventory's qumulo_tenants does not list under {name}")
            continue
        given, place = listed[network["id"]]
        differ = [key for key, value in given.items() if key not in ("id", "tenant") and value is not None and network.get(key) != value]
        # Under a tenant's entry an omitted vlan_id means untagged, not the cluster's VLAN; a flat network takes none.
        if place.startswith("qumulo_tenants[") and given.get("vlan_id") is None and network.get("vlan_id") is not None:
            differ.append("vlan_id (omitted under a tenant's entry, which is untagged)")
        if differ:
            refusals.append(
                f"network {network['id']} of tenant {name}, which the inventory lists in {place} and the capture with other values of {', '.join(differ)}"
            )
    return refusals


def _settings_agree(given, captured):
    """Whether a tenant's switch or settings agree: a mapping by the keys the inventory gives, anything else exactly."""
    if isinstance(given, dict) and isinstance(captured, dict):
        return all(captured.get(key) == value for key, value in given.items())
    return given == captured


def _judge(ours, theirs, cluster_holds_entries):
    """The note that leaves the file's claim out for the inventory's, or the refusal of it."""
    holder = "the capture" if ours.at[:1] == ("hidden",) else "the file"
    refusal = f"{ours.what}, which the inventory declares in {theirs.place} and {holder} in {ours.place}"
    if ours.words is None:
        return refusal
    if theirs.removal:
        why = f"the inventory removes it{' too' if ours.removal else ''}, in {theirs.place}, so the next baseline removes it from the cluster"
        return {"what": ours.what, "why": why}
    if ours.removal:
        why = f"the inventory declares the {ours.words.noun} in {theirs.place}, which the next baseline applies instead"
        return {"what": f"the removal of {ours.what}", "why": why}
    mine, given = _canonical(ours.words, ours.item), _canonical(theirs.words, theirs.item)
    differ = [key for key, value in given.items() if mine.get(key) != value]
    if differ:
        return f"{refusal} with other values of {', '.join(differ)}"
    # The keys the file's entry gives beyond the inventory's: the cluster keeps them, but one that creates the entry from
    # the inventory's alone gives each its create default.
    defaults = CREATE_DEFAULTS[ours.words.noun]
    undeclared = [key for key, value in mine.items() if key not in given and (key not in defaults or defaults[key] != value)]
    if undeclared and not cluster_holds_entries:
        return f"{refusal} with other values of {', '.join(undeclared)}, which the inventory's entry leaves out"
    why = f"the inventory declares it in {theirs.place}, and the baseline refuses an {ours.words.noun} declared twice; the inventory's entry applies"
    if undeclared:
        them = "it" if len(undeclared) == 1 else "them"
        why += f". The cluster keeps its {_listed(undeclared)}, which no entry declares now, so a rebuild from the inventory would not restore {them}"
    return {"what": ours.what, "why": why}


def _listed(words):
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _canonical(words, item):
    """item as its module compares it, without the keys that place it or say what a write does: paths, modes, rights,
    trustees, the 32-bit fields and restrictions in one spelling, with the module's defaults filled in. A value the
    module would refuse stays as it is given."""
    out = {}
    for key, value in item.items():
        if key in NOT_COMPARED or key == words.key or value is None:  # the modules take a null option as not given
            continue
        try:
            out[key] = _canonical_value(words, key, value)
        except (QumuloError, KeyError, TypeError, AttributeError):
            out[key] = value
    return out


def _canonical_value(words, key, value):
    if key == "fs_path":
        return canonical_path(value, key)
    if words is SMB and key in MODES:
        return canonical_mode(value, key)
    if words is SMB and key == "permissions":
        return [
            {"type": entry.get("type") or "ALLOWED", "trustee": parse_trustee(entry["trustee"]), "rights": share_rights(entry["rights"], key)}
            for entry in value
        ]
    if words is SMB and key == "network_permissions":
        return [_network_entry({**entry, "type": entry.get("type") or "ALLOWED"}, key) for entry in value]
    if words is NFS and key == "fields_to_present_as_32_bit":
        return _thirty_two_bit(value)
    if words is NFS and key == "restrictions":
        restrictions = [_restriction({**RESTRICTION_DEFAULTS, **{k: v for k, v in entry.items() if v is not None}}, key) for entry in value]
        # A release with require_tls stores false for a restriction written without it, so false and unset are one.
        return [{k: v for k, v in restriction.items() if not (k == "require_tls" and v is False)} for restriction in restrictions]
    return value


def _tenant_1(entries, lacks_tenant_1):
    """The name of the present entry that is tenant 1: the one renamed from Default, else the one named Default; None
    when the cluster lacks tenant 1, and ONE_TENANT when qumulo_tenants declares no tenant."""
    if lacks_tenant_1:
        return None
    if not entries:
        return ONE_TENANT
    present = [entry for _position, entry in entries if entry.get("state", "present") != "absent"]
    renamed = [entry["name"] for entry in present if entry.get("previous_name") == tenancy.FORMATION_TENANT_NAME]
    named = [entry["name"] for entry in present if entry["name"] == tenancy.FORMATION_TENANT_NAME and not entry.get("previous_name")]
    return (renamed + named + [None])[0]


def _flat_tenants(item, entries, first, only):
    """The tenants a flat entry claims, by name; [] when only the baseline can tell, or it refuses the entry."""
    tenants = item.get("tenants")
    if tenants == "all":
        return [entry["name"] for _position, entry in entries]
    if isinstance(tenants, list):
        return [tenant for tenant in tenants if isinstance(tenant, str)]
    if tenants is not None:
        return []
    if item.get("tenant_id") is not None:
        return [first] if item["tenant_id"] == tenancy.FORMATION_TENANT_ID and first is not None else []
    return [only] if only is not None else []


def _key(words, name):
    """The name as the cluster compares it, or None for one the baseline refuses."""
    try:
        return words.key_of(name) if isinstance(name, str) else None
    except QumuloError:
        return None


def _share_claims(effective, entries, first, only, words, field):
    """The claim of each share or export under a tenant, in the flat list, or removed by qumulo_factory_defaults."""

    def claim(tenant, name, origin, place, item, removal, at):
        key = _key(words, name)
        if key is None or tenant is None:
            return []
        of = ONE_TENANT if tenant == ONE_TENANT else f"tenant {tenant}"
        return [Claim((words.noun, tenant, key), f"the {words.noun} {name} of {of}", origin, place, item, removal, at, words)]

    claims = []
    if "qumulo_tenants" in effective:
        origin = effective["qumulo_tenants"][1]
        for position, entry in entries:
            gone = entry.get("state", "present") == "absent"
            for index, item in enumerate(entry.get(field) or []):
                if isinstance(item, dict):
                    place = f"qumulo_tenants[{entry['name']}].{field}[{index}]"
                    removal = gone or item.get("state", "present") == "absent"
                    claims += claim(entry["name"], item.get(words.key), origin, place, item, removal, ("nested", position, field, index))
    flat, origin = effective.get(words.variable, (None, None))
    for index, item in enumerate(flat or []):
        if isinstance(item, dict):
            removal = item.get("state", "present") == "absent"
            for tenant in _flat_tenants(item, entries, first, only):
                claims += claim(tenant, item.get(words.key), origin, f"{words.variable}[{index}]", item, removal, ("flat", words.variable, index, tenant))
    factory, origin = effective.get("qumulo_factory_defaults", (None, None))
    if isinstance(factory, dict) and factory.get(words.factory) == "absent":
        place = f"qumulo_factory_defaults.{words.factory}"
        claims += claim(first, words.factory_name, origin, place, None, True, ("factory", words.factory))
    return claims


def _network_claims(effective, entries):
    """The claim of each network listed under a present tenant or in qumulo_network.frontend_networks; the baseline
    lists a network under an absent tenant only to move it."""
    listed = []
    if "qumulo_tenants" in effective:
        origin = effective["qumulo_tenants"][1]
        listed += [
            (item, origin, f"qumulo_tenants[{entry['name']}].networks[{index}]")
            for _position, entry in entries
            if entry.get("state", "present") != "absent"
            for index, item in enumerate(entry.get("networks") or [])
        ]
    network, origin = effective.get("qumulo_network", (None, None))
    if isinstance(network, dict):
        listed += [(item, origin, f"qumulo_network.frontend_networks[{index}]") for index, item in enumerate(network.get("frontend_networks") or [])]
    return [
        Claim(("network", item["id"]), f"network {item['id']}", origin, place, item, False, ())
        for item, origin, place in listed
        if isinstance(item, dict) and item.get("id") is not None
    ]


def _without(variables, dropped, names):
    """A copy of variables without each dropped entry: a nested share or export, a tenant of a flat one, or a factory
    removal. names are the present declared tenants, which a flat entry's tenants: all is written as, less those dropped."""
    if not dropped:
        return variables
    out = copy.deepcopy(dict(variables))
    nested = {at[1:] for at in dropped if at[0] == "nested"}
    for position, entry in enumerate(out.get("qumulo_tenants") or []):
        for field in ("smb_shares", "nfs_exports"):
            items = entry.get(field) if isinstance(entry, dict) else None
            if items and any((position, field, index) in nested for index in range(len(items))):
                entry[field] = [item for index, item in enumerate(items) if (position, field, index) not in nested]
                if not entry[field]:
                    del entry[field]  # as capture leaves an empty list out of an entry
    flat = {}
    for at in dropped:
        if at[0] == "flat":
            flat.setdefault(at[1], {}).setdefault(at[2], set()).add(at[3])
    for variable, by_index in flat.items():
        kept = []
        for index, item in enumerate(out[variable]):
            tenants = names if item.get("tenants") == "all" else item.get("tenants")
            if index not in by_index:
                kept.append(item)
            elif isinstance(tenants, list) and [tenant for tenant in tenants if tenant not in by_index[index]]:
                kept.append({**item, "tenants": [tenant for tenant in tenants if tenant not in by_index[index]]})
        out[variable] = kept
    for at in dropped:
        if at[0] == "factory":
            out["qumulo_factory_defaults"] = {key: value for key, value in out["qumulo_factory_defaults"].items() if key != at[1]}
            if not out["qumulo_factory_defaults"]:
                del out["qumulo_factory_defaults"]
    return out


class FilterModule:
    def filters(self):
        return {"host_vars_duplicates": host_vars_duplicates}
