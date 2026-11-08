# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The smb area: the SMB server settings and SMB shares."""

from __future__ import annotations

from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.acl import check_not_write_only, share_rights
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import NOT_OFFERED_STATUS
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_mode, canonical_path
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import IdentityResolver, parse_trustee, render
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Field, Resource, Settings

SETTINGS = Route(
    "/v1/smb/settings",
    ("GET", "PATCH"),
    etag="omit",
    why="QUIRK-001: when the two hide_shares flags differ, the GET's ETag never matches what PATCH checks",
)


# The order the server lists dialects in; it keeps them as a sorted set, whatever order they were sent in.
DIALECTS = ("SMB2_DIALECT_2_002", "SMB2_DIALECT_2_1", "SMB2_DIALECT_3_0", "SMB2_DIALECT_3_11")


def _same_set(a, b):
    return set(a) == set(b)


class SmbSettings(Settings):
    RETURN_KEY = "smb_settings"
    ROUTE = SETTINGS
    FIELDS: ClassVar[dict[str, Field]] = {
        "session_encryption": Field("session_encryption"),
        "supported_dialects": Field("supported_dialects", compare=_same_set),
        "hide_shares_from_unauthorized_users": Field("hide_shares_from_unauthorized_users"),
        "hide_shares_from_unauthorized_hosts": Field("hide_shares_from_unauthorized_hosts"),
        "snapshot_directory_mode": Field("snapshot_directory_mode"),
        "bypass_traverse_checking": Field("bypass_traverse_checking"),
        "signing_required": Field("signing_required"),
    }

    def describe(self):
        return "the SMB server settings"

    def desired(self, before):
        after = super().desired(before)
        if self.params.get("supported_dialects") is not None:
            after["supported_dialects"] = sorted(set(after["supported_dialects"]), key=DIALECTS.index)
        return after


SHARES = Route("/v3/smb/shares/", ("GET", "POST"))
SHARE = Route("/v3/smb/shares/{id}", ("GET", "PATCH", "DELETE"))
SCALARS = ("description", "access_based_enumeration_enabled", "require_encryption", "expand_fs_path_variables", "offline_files_caching_mode")
MODES = ("default_file_create_mode", "default_directory_create_mode")


def share_options(data, identities=None):
    """A share as the server returns it, in the option shape of smb_share; trustees need their names populated.

    With identities, each trustee resolves to the identity the server gave with the entry, as files.acl_options says.
    """
    trustee = identities.known if identities else render
    return {
        "share_name": data["share_name"],
        "tenant_id": data["tenant_id"],
        "fs_path": canonical_path(data["fs_path"], "fs_path"),
        "permissions": [{"type": e["type"], "trustee": trustee(e["trustee"]), "rights": sorted(e["rights"])} for e in data["permissions"]],
        "network_permissions": [
            {"type": e["type"], "address_ranges": list(e["address_ranges"]), "rights": sorted(e["rights"])} for e in data["network_permissions"]
        ],
        **{key: data[key] for key in (*SCALARS, *MODES) if key in data},
    }


def _share_entry(entry, where):
    parse_trustee(entry["trustee"])
    rights = share_rights(entry["rights"], where)
    check_not_write_only(entry["type"], rights, where)
    return {"type": entry["type"], "trustee": entry["trustee"].strip(), "rights": rights}


def _network_entry(entry, where):
    rights = share_rights(entry["rights"], where)
    check_not_write_only(entry["type"], rights, where)
    return {"type": entry["type"], "address_ranges": list(entry.get("address_ranges") or []), "rights": rights}


class SmbShare(Resource):
    RETURN_KEY = "smb_share"
    READ_ONLY = frozenset({"share_name", "tenant_id"})

    def __init__(self, module, client, tenant_id=None):
        super().__init__(module, client)
        params = self.params
        self.name = params["share_name"]
        self.tenant_id = tenant_id  # the tenant's id, a Pending for one an earlier task creates, or None for any
        self.identities = IdentityResolver(client, check_mode=module.check_mode)
        wanted = {key: params[key] for key in SCALARS if params.get(key) is not None}
        if params.get("fs_path") is not None:
            wanted["fs_path"] = canonical_path(params["fs_path"], "fs_path")
        for key in MODES:
            if params.get(key) is not None:
                wanted[key] = canonical_mode(params[key], key)
        if params.get("permissions") is not None:
            wanted["permissions"] = [_share_entry(e, f"permissions[{i}]") for i, e in enumerate(params["permissions"], 1)]
        if params.get("network_permissions") is not None:
            wanted["network_permissions"] = [_network_entry(e, f"network_permissions[{i}]") for i, e in enumerate(params["network_permissions"], 1)]
        self.wanted = wanted

    def describe(self):
        return f"SMB share '{self.name}'"

    def _listed(self):
        """This share's entries in the share list, of its tenant when tenant_id is set."""
        folded = self.name.casefold()
        if "smb_shares" not in self.client.memo:  # one list read serves the tenants of a run until a write, which reads it again
            self.client.memo["smb_shares"] = self.client.get(SHARES).data["entries"]
        return [
            entry
            for entry in self.client.memo["smb_shares"]
            if entry["share_name"].casefold() == folded and (self.tenant_id is None or entry["tenant_id"] == self.tenant_id)
        ]

    def check_creatable(self):
        """Refuse, from the share list alone, a share that does not exist and that the options cannot create."""
        if not self._listed():
            self._check_create_options()

    def _check_create_options(self):
        if "fs_path" not in self.wanted or "permissions" not in self.wanted:
            raise QumuloError(f"SMB share '{self.name}' does not exist; set fs_path and permissions to create it (an empty permissions list denies everyone)")

    def fetch(self):
        found = self._listed()
        if len(found) > 1:
            names = tenant_names(self.client)
            tenants = ", ".join(repr(names[entry["tenant_id"]]) if entry["tenant_id"] in names else str(entry["tenant_id"]) for entry in found)
            raise QumuloError(f"SMB share '{self.name}' exists in tenants {tenants}; set tenant to choose one")
        response = self.client.get_or_none(SHARE, params={"id": found[0]["id"]}) if found else None
        return Current(response.data["id"], response.data, response.etag) if response else None

    def normalize(self, current):
        return share_options(current.data, self.identities)

    def desired(self, before):
        if not before:
            self._check_create_options()
            before = {"share_name": self.name, **({} if self.tenant_id is None else {"tenant_id": self.tenant_id})}
        after = {**before, **self.wanted}
        if "permissions" in before and self.same("permissions", before["permissions"], after["permissions"]):
            after["permissions"] = before["permissions"]  # the same entries; report them as the cluster names them
        return after

    def same(self, key, a, b):
        if key == "permissions" and a is not None and b is not None:
            return len(a) == len(b) and all(
                x["type"] == y["type"] and x["rights"] == y["rights"] and self.identities.same(x["trustee"], y["trustee"]) for x, y in zip(a, b)
            )
        return super().same(key, a, b)

    def _wire(self, key, value):
        if key == "permissions":
            return [{"type": e["type"], "trustee": {"auth_id": self.identities.resolve(e["trustee"])["auth_id"]}, "rights": e["rights"]} for e in value]
        return value

    def _body(self, after, keys):
        # QUIRK-007: the server keeps allow_fs_path_create from earlier writes, so every write states it.
        return {**{key: self._wire(key, after[key]) for key in keys}, "allow_fs_path_create": self.params["create_fs_path"]}

    def create(self, after):
        tenant = {} if self.tenant_id is None else {"tenant_id": self.tenant_id}
        self.client.memo.pop("smb_shares", None)
        self.client.post(SHARES, body={"share_name": self.name, **tenant, **self._body(after, after)})

    def update(self, current, after, keys):
        # No tenant move: tenant_id is read-only, so a PATCH never carries it.
        self.client.memo.pop("smb_shares", None)
        self.client.patch(SHARE, params={"id": current.id}, body=self._body(after, keys), if_match=current.etag)

    def delete(self, current):
        self.client.memo.pop("smb_shares", None)
        self.client.delete(SHARE, params={"id": current.id}, if_match=current.etag)


SESSIONS = Route("/v1/smb/sessions/", ("GET",))
TENANT_SETTINGS = Route("/v1/multitenancy/smb/settings/", ("GET",))  # a list of {key: tenant id, value: the override}


def tenant_names(client, warn=False):
    """Tenant id to name, from tenancy's one list read; empty where the list is not offered or not readable (403).

    warn, for the info modules, says once per run why a 403 leaves the tenants unnamed, as network_info does.
    """
    from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy  # it imports this module

    try:
        return {entry["id"]: entry["name"] for entry in tenancy.tenant_entries(client)}
    except QumuloApiError as e:
        if e.status not in {403, *NOT_OFFERED_STATUS}:
            raise
        warning = f"the tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ ({e.error_class})"
        if warn and e.status == 403 and warning not in client.warnings:
            client.warnings.append(warning)
        return {}


def with_tenant(entry, names):
    """An entry with tenant, its tenant's name, before its tenant_id."""
    out = {}
    for key, value in entry.items():
        if key == "tenant_id":
            out["tenant"] = names.get(value)
        out[key] = value
    return out


def tenant_settings(client, route, options):
    """Each tenant's override by tenant name, from a list of {key: tenant id, value: override}; a tenant without one is left out.

    Empty, with the list route not read, where the tenant list cannot be read; empty where the list route is not offered.
    """
    names = tenant_names(client, warn=True)
    if not names:
        return {}
    try:
        items = client.get(route).data
    except QumuloApiError as e:
        if e.status not in NOT_OFFERED_STATUS:
            raise
        return {}
    return {names[item["key"]]: options(item["value"]) for item in items if item["key"] in names}


def _shares(client):
    entries = client.get(SHARES, query={"populate-trustee-names": True}).data["entries"]
    return {"shares": [share_options(entry) for entry in entries]}


def _settings(client):
    return {"settings": SmbSettings.options(client.get(SETTINGS).data)}


def _sessions(client):
    return {"sessions": client.paginate(SESSIONS, "session_infos")}


# gather_subset name -> function of the client returning a dict of that subset's facts; capture reads these
SMB_INFO_SUBSETS = {"shares": _shares, "settings": _settings, "sessions": _sessions}


def _named_shares(client):
    names = tenant_names(client, warn=True)
    return {"shares": [with_tenant(share, names) for share in _shares(client)["shares"]]}


# smb_info's subsets: the shares name their tenant, and each tenant's override is by its name
SMB_INFO_MODULE_SUBSETS = {
    "shares": _named_shares,
    "settings": _settings,
    "tenant_settings": lambda client: {"tenant_settings": tenant_settings(client, TENANT_SETTINGS, SmbSettings.options)},
    "sessions": _sessions,
}
