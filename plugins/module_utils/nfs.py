# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The nfs area: the NFS server settings and NFS exports."""

from __future__ import annotations

from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_path
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import parse_trustee
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Field, Resource, Settings
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import tenant_names, tenant_settings, with_tenant
from ansible_collections.qumulo.cluster.plugins.module_utils.version import require

SETTINGS = Route("/v2/nfs/settings", ("GET", "PATCH"))  # returns no ETag, so writes carry no If-Match
EXPORTS = Route("/v3/nfs/exports/", ("GET", "POST"))


def _inside(outer, inner):
    return outer != inner and (outer == "/" or inner.startswith(outer + "/"))


def nested_pair(paths):
    """The first (outer, inner) pair of export paths where one contains the other, or None."""
    paths = sorted(paths)
    return next(((outer, inner) for outer in paths for inner in paths if _inside(outer, inner)), None)


def nested_export(client, tenant_id=None):
    """The first (outer, inner) pair of export paths of one tenant where one contains the other, or None.

    NFSv4.1 refuses nesting within a tenant only, so paths of two tenants never pair (spec 014, FR-016). tenant_id
    limits the check to that tenant's exports, as an override's check is; unset, every tenant is checked, as the
    global settings' check is, which covers a tenant with an override too.
    """
    by_tenant = {}
    for entry in client.get(EXPORTS).data["entries"]:
        if tenant_id is None or entry.get("tenant_id") == tenant_id:
            by_tenant.setdefault(entry.get("tenant_id"), set()).add(entry["export_path"])
    for tenant in sorted(by_tenant, key=str):
        pair = nested_pair(by_tenant[tenant])
        if pair:
            return pair
    return None


class NfsSettings(Settings):
    RETURN_KEY = "nfs_settings"
    ROUTE = SETTINGS
    FIELDS: ClassVar[dict[str, Field]] = {
        "v4_enabled": Field("v4_enabled"),
        "krb5_enabled": Field("krb5_enabled"),
        "krb5i_enabled": Field("krb5i_enabled"),
        "krb5p_enabled": Field("krb5p_enabled"),
        "auth_sys_enabled": Field("auth_sys_enabled"),
        # The server returns null when no domain is set, and takes null to clear it.
        "idmap_domain": Field("idmap_domain", read=lambda value: value or "", write=lambda value: value or None),
    }

    def describe(self):
        return "the NFS server settings"

    def desired(self, before):
        after = super().desired(before)
        if after.get("v4_enabled") and not before.get("v4_enabled"):
            self.check_nesting("setting v4_enabled")
        return after

    def check_nesting(self, change, tenant_id=None, exports="every export"):
        """Refuse change, which turns NFSv4.1 on, while an export contains another; tenant_id limits it to one tenant's exports."""
        pair = nested_export(self.client, tenant_id=tenant_id)
        if pair:
            raise QumuloError(
                f"NFSv4.1 needs {exports} outside every other, but export '{pair[0]}' contains '{pair[1]}'; remove or move one of them before {change}"
            )


EXPORT = Route("/v3/nfs/exports/{id}", ("GET", "PATCH", "DELETE"))
THIRTY_TWO_BIT = ("FILE_IDS", "FILE_SIZES", "FS_SIZE")
RESTRICTION_KEYS = ("host_restrictions", "read_only", "require_privileged_port", "required_authentication_mode", "user_mapping")
# The NFS identities a restriction maps users to, and the trustee prefix of each.
NFS_IDENTITIES = {"LOCAL_USER": "local", "NFS_UID": "uid", "NFS_GID": "gid"}
ID_TYPES = {prefix: id_type for id_type, prefix in NFS_IDENTITIES.items()}


def canonical_export_path(path):
    """The export path exactly as the cluster stores it; it refuses a trailing slash on anything but /."""
    if isinstance(path, str) and path != "/" and path.endswith("/"):
        raise QumuloError(f"export_path '{path}' ends with a slash, which the cluster accepts only for /")
    return canonical_path(path, "export_path")


def _identity_option(identity):
    """A map_to_user or map_to_group as a trustee string; "" for a mapping to a user that no longer exists (G-MAPTO)."""
    prefix, value = NFS_IDENTITIES[identity["id_type"]], identity["id_value"]
    return "" if value == "" else f"{prefix}:{value}"


def _identity_wire(option):
    prefix, value = option.split(":", 1)
    return {"id_type": ID_TYPES[prefix], "id_value": value}


def restriction_options(data):
    out = {key: data[key] for key in RESTRICTION_KEYS}
    out.update({key: _identity_option(data[key]) for key in ("map_to_user", "map_to_group") if key in data})
    if "require_tls" in data:
        out["require_tls"] = data["require_tls"]
    return out


def export_options(data):
    """An export as the server returns it, in the option shape of nfs_export."""
    return {
        "export_path": data["export_path"],
        "tenant_id": data["tenant_id"],
        "fs_path": canonical_path(data["fs_path"], "fs_path"),
        "description": data["description"],
        "restrictions": [restriction_options(entry) for entry in data["restrictions"]],
        "fields_to_present_as_32_bit": sorted(data.get("fields_to_present_as_32_bit") or []),
    }


def _thirty_two_bit(values):
    found = set()
    for value in values:
        found.update(THIRTY_TWO_BIT if value == "ALL" else () if value == "NONE" else (value,))
    return sorted(found)


def _mapping(entry, where):
    """map_to_user and map_to_group in trustee form, checked against the pairings the cluster accepts."""
    user, group = entry.get("map_to_user"), entry.get("map_to_group")
    if entry["user_mapping"] == "NFS_MAP_NONE":
        if user is not None or group is not None:
            raise QumuloError(f"{where}: map_to_user applies only when user_mapping is NFS_MAP_ROOT or NFS_MAP_ALL")
        return {}
    if user is None:
        raise QumuloError(f"{where}: user_mapping {entry['user_mapping']} needs map_to_user")
    if user == "":
        raise QumuloError(
            f"{where}: map_to_user is empty, which is how the cluster reports a mapping to a local user that no longer exists; name an existing user"
        )
    query = parse_trustee(user)
    if query.get("domain") == "LOCAL":
        if group is not None:
            raise QumuloError(f"{where}: map_to_group applies only with a uid map_to_user")
        return {"map_to_user": f"local:{query['name']}"}
    if "uid" not in query:
        raise QumuloError(f"{where}: map_to_user '{user}' must be local:<name> or uid:<n>")
    if group is None:
        raise QumuloError(f"{where}: a uid map_to_user needs map_to_group, as gid:<n>")
    group_query = parse_trustee(group)
    if "gid" not in group_query:
        raise QumuloError(f"{where}: map_to_group '{group}' must be gid:<n>")
    return {"map_to_user": f"uid:{query['uid']}", "map_to_group": f"gid:{group_query['gid']}"}


def _restriction(entry, where):
    out = {key: entry[key] for key in RESTRICTION_KEYS}
    out["host_restrictions"] = list(entry["host_restrictions"] or [])
    out.update(_mapping(entry, where))
    if entry.get("require_tls") is not None:
        out["require_tls"] = entry["require_tls"]
    return out


def _restriction_wire(option):
    wire = {key: option[key] for key in RESTRICTION_KEYS}
    wire.update({key: _identity_wire(option[key]) for key in ("map_to_user", "map_to_group") if key in option})
    if "require_tls" in option:
        wire["require_tls"] = option["require_tls"]
    return wire


def _same_restriction(stored, wanted):
    # A release with require_tls stores false whenever restrictions are written without it.
    if "require_tls" in stored and "require_tls" not in wanted:
        wanted = {**wanted, "require_tls": False}
    return stored == wanted


class NfsExport(Resource):
    RETURN_KEY = "nfs_export"
    READ_ONLY = frozenset({"export_path", "tenant_id"})

    def __init__(self, module, client, tenant_id=None):
        super().__init__(module, client)
        params = self.params
        self.export_path = canonical_export_path(params["export_path"])
        self.tenant_id = tenant_id  # the tenant's id, a Pending for one an earlier task creates, or None for any
        wanted = {}
        if params.get("fs_path") is not None:
            wanted["fs_path"] = canonical_path(params["fs_path"], "fs_path")
        if params.get("description") is not None:
            wanted["description"] = params["description"]
        if params.get("fields_to_present_as_32_bit") is not None:
            wanted["fields_to_present_as_32_bit"] = _thirty_two_bit(params["fields_to_present_as_32_bit"])
        if params.get("restrictions") is not None:
            wanted["restrictions"] = [_restriction(entry, f"restrictions[{i}]") for i, entry in enumerate(params["restrictions"], 1)]
        self.wanted = wanted

    def describe(self):
        return f"NFS export '{self.export_path}'"

    def _listed(self):
        """This export's entries in the export list, of its tenant when tenant_id is set."""
        if "nfs_exports" not in self.client.memo:  # one list read serves the tenants of a run until a write, which reads it again
            self.client.memo["nfs_exports"] = self.client.get(EXPORTS).data["entries"]
        return [
            entry
            for entry in self.client.memo["nfs_exports"]
            if entry["export_path"] == self.export_path and (self.tenant_id is None or entry["tenant_id"] == self.tenant_id)
        ]

    def check_creatable(self):
        """Refuse, from the export list alone, an export that does not exist and that the options cannot create."""
        if not self._listed():
            self._check_create_options()

    def _check_create_options(self):
        if "fs_path" not in self.wanted or "restrictions" not in self.wanted:
            raise QumuloError(f"NFS export '{self.export_path}' does not exist; set fs_path and restrictions to create it")

    def fetch(self):
        if any("require_tls" in entry for entry in self.wanted.get("restrictions", ())):
            require(self.client, "nfs_export.require_tls", "restrictions[].require_tls")
        found = self._listed()
        if len(found) > 1:
            names = tenant_names(self.client)
            tenants = ", ".join(repr(names[entry["tenant_id"]]) if entry["tenant_id"] in names else str(entry["tenant_id"]) for entry in found)
            raise QumuloError(f"NFS export '{self.export_path}' exists in tenants {tenants}; set tenant to choose one")
        response = self.client.get_or_none(EXPORT, params={"id": found[0]["id"]}) if found else None
        return Current(response.data["id"], response.data, response.etag) if response else None

    def normalize(self, current):
        return export_options(current.data)

    def desired(self, before):
        if not before:
            self._check_create_options()
            before = {"export_path": self.export_path, **({} if self.tenant_id is None else {"tenant_id": self.tenant_id}), "description": ""}
        self._check_orphaned_mapping(before)
        return {**before, **self.wanted}

    def _check_orphaned_mapping(self, before):
        """G-MAPTO: an export mapped to a deleted user accepts no change but new restrictions."""
        if "restrictions" in self.wanted or all(self.same(key, before.get(key), value) for key, value in self.wanted.items()):
            return
        for number, entry in enumerate(before.get("restrictions", ()), 1):
            if entry.get("map_to_user") == "":
                raise QumuloError(
                    f"NFS export '{self.export_path}': restriction {number} maps users to a local user that no longer exists, and the cluster "
                    "refuses every change to the export until its restrictions are replaced; set restrictions"
                )

    def same(self, key, a, b):
        if key == "restrictions" and a is not None and b is not None:
            return len(a) == len(b) and all(_same_restriction(x, y) for x, y in zip(a, b))
        return super().same(key, a, b)

    def _body(self, after, keys):
        return {key: [_restriction_wire(entry) for entry in after[key]] if key == "restrictions" else after[key] for key in keys}

    def create(self, after):
        tenant = {} if self.tenant_id is None else {"tenant_id": self.tenant_id}
        query = {"allow-fs-path-create": self.params["create_fs_path"]}
        self.client.memo.pop("nfs_exports", None)
        self.client.post(EXPORTS, query=query, body={"export_path": self.export_path, **tenant, **self._body(after, after)})

    def update(self, current, after, keys):
        # No tenant move: tenant_id is read-only, so a PATCH never carries it.
        self.client.memo.pop("nfs_exports", None)
        self.client.patch(EXPORT, params={"id": current.id}, body=self._body(after, keys), if_match=current.etag)

    def delete(self, current):
        self.client.memo.pop("nfs_exports", None)
        self.client.delete(EXPORT, params={"id": current.id}, if_match=current.etag)


def _exports(client):
    return {"exports": [export_options(entry) for entry in client.get(EXPORTS).data["entries"]]}


def _settings(client):
    return {"settings": NfsSettings.options(client.get(SETTINGS).data)}


# gather_subset name -> function of the client returning a dict of that subset's facts; capture reads these
NFS_INFO_SUBSETS = {"exports": _exports, "settings": _settings}
TENANT_SETTINGS = Route("/v1/multitenancy/nfs/settings/", ("GET",))  # a list of {key: tenant id, value: the override}


def _named_exports(client):
    names = tenant_names(client, warn=True)
    return {"exports": [with_tenant(export, names) for export in _exports(client)["exports"]]}


# nfs_info's subsets: the exports name their tenant, and each tenant's override is by its name
NFS_INFO_MODULE_SUBSETS = {
    "exports": _named_exports,
    "settings": _settings,
    "tenant_settings": lambda client: {"tenant_settings": tenant_settings(client, TENANT_SETTINGS, NfsSettings.options)},
}
