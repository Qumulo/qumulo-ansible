# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Network multitenancy: the tenant routes, and the lookup of a tenant by name that every module with tenant asks.

Tenant ids are generated and never reused, so a task names a tenant and the id is looked up on each run.
"""

from __future__ import annotations

from typing import ClassVar, NamedTuple

from ansible.module_utils.parsing.convert_bool import boolean
from ansible_collections.qumulo.cluster.plugins.module_utils import continuity, identity, nfs, smb
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import PROTECTION, SubsetUnavailable, get_offered
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import listed
from ansible_collections.qumulo.cluster.plugins.module_utils.network import NETWORK, canonical_document, named_network, read_status
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import EXPORTS
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Resource
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SHARES
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_path import CLOUD, host_kind

TENANTS = Route("/v1/multitenancy/tenants/", ("GET", "POST"))
TENANT = Route("/v1/multitenancy/tenants/{tenant_id}", ("GET", "PATCH", "DELETE"))
FORMATION_TENANT_ID = 1  # the tenant a cluster forms with, which holds the factory share and export
FORMATION_TENANT_NAME = "Default"  # its name on a newly formed cluster
PLATFORM_REFUSED = "multitenancy_api_platform_not_allowed_error"
SMB_OVERRIDES = smb.TENANT_SETTINGS  # a list of {key: tenant id, value: the override}
NFS_OVERRIDES = nfs.TENANT_SETTINGS


class Pending(identity.Pending):
    """A tenant an earlier task creates, in check mode: its name, never equal to a tenant id or to anything else."""


class TenantIds(NamedTuple):
    by_name: dict
    by_id: dict


def tenant_entries(client):
    """The cluster's tenants as the list answers them, from one read per module run.

    A writer that creates, renames or deletes a tenant calls forget_tenants afterwards, so the next call reads again.
    """
    if "tenant_entries" not in client.memo:
        client.memo["tenant_entries"] = client.get(TENANTS).data["entries"]
    return client.memo["tenant_entries"]


def tenant_ids(client):
    """The cluster's tenants, name to id and id to name, from tenant_entries' read."""
    entries = tenant_entries(client)
    return TenantIds({t["name"]: t["id"] for t in entries}, {t["id"]: t["name"] for t in entries})


def forget_tenants(client):
    """Drop the tenant list tenant_entries read, after a write that changed it."""
    client.memo.pop("tenant_entries", None)


def pending_note(name):
    """The note of a run whose tenant resolved to a Pending."""
    return f"tenant '{name}' does not exist yet; an earlier task is expected to create it"


def resolve(client, name, check_mode):
    """The id of the tenant named exactly name; in check mode a Pending for one the cluster lacks, else an error."""
    ids = tenant_ids(client)
    if name in ids.by_name:
        return ids.by_name[name]
    if check_mode:
        return Pending(name)
    raise QumuloError(f"no tenant on this cluster is named {name!r}; its tenants are {listed(repr(n) for n in sorted(ids.by_name))}")


def create_identity_config(tenants):
    """The identity_config_id a new tenant is created with: tenant 1's, or once it is gone the one every tenant shares."""
    for tenant in tenants:
        if tenant["id"] == FORMATION_TENANT_ID:
            return tenant["identity_config_id"]
    configs = {tenant["identity_config_id"] for tenant in tenants}
    if len(configs) == 1:
        return configs.pop()
    each = [f"{t['name']} (identity configuration {t['identity_config_id']})" for t in sorted(tenants, key=lambda t: t["name"])]
    raise QumuloError(
        "a new tenant takes the identity configuration of tenant 1, or once tenant 1 is gone the one every tenant shares, "
        f"but tenant 1 is gone and the tenants use different ones: {listed(each)}. Choosing one for the new tenant is "
        "per-tenant identity, which the collection does not support yet"
    )


def listener_switch(before, after):
    """Whether a change of the tenant count from before to after crosses between one tenant and two or more.

    With one tenant every service listens on all interfaces; with more, each listens only on its tenants' networks, so
    the crossing rebinds every listener on every node.
    """
    return (before > 1) != (after > 1)


def platform_refusal(error):
    """The server's refusal of a tenant write on a cloud platform as one message; any other error as it is."""
    if not error.is_(PLATFORM_REFUSED):
        return error
    return QumuloError(
        f"this cluster refuses tenant writes (HTTP {error.status} {PLATFORM_REFUSED}): Qumulo Core does not allow creating, "
        "changing or deleting a tenant on a cloud platform (AWS, Azure, GCP or OCI), so a cloud cluster keeps its one "
        "tenant. Remove the tenant change from this cluster's tasks",
        status=error.status,
        error_class=error.error_class,
    )


def cloud_warning(client):
    """The check-mode warning for a cluster whose protection type marks it as cloud, where a tenant write is refused; else None.

    The warning is advice, so a protection status that cannot be read gives none rather than failing the check run.
    """
    try:
        kind = get_offered(client, PROTECTION).get("protection_system_type")
    except (SubsetUnavailable, QumuloApiError):
        return None
    if host_kind(None, kind) != CLOUD:
        return None
    return (
        f"this cluster's protection type, {kind}, marks a cloud cluster, and Qumulo Core refuses every tenant write on a "
        "cloud platform: the run fails where this check predicts a change"
    )


SWITCHES = ("web_ui_enabled", "rest_api_enabled", "ssh_enabled", "replication_enabled", "nfs_enabled", "smb_enabled")
REST_SWITCHES = ("rest_api_enabled", "web_ui_enabled")  # the switches the continuity rule judges; the others are not


def tenant_options(entry):
    """A tenant in tenant's option shape: its name and switches, with the ids of its networks, which tenant never writes."""
    return {"name": entry["name"], **{switch: bool(entry.get(switch)) for switch in SWITCHES}, "networks": sorted(entry.get("networks") or [])}


def check_second_tenant(name, document, entries):
    """Spec FR-006: once there are two tenants, a tenant network off the frontend interfaces aborts every node.

    name is the tenant a create would add to the one in entries, the tenant list; document is the canonical network
    document.
    """
    interfaces = document["managed_interfaces"] or {}
    frontend = set((interfaces.get("frontend_bond_config") or {}).get("networks") or [])
    for vlan in interfaces.get("frontend_vlans") or []:
        frontend |= {vlan.get("network_id"), vlan.get("secondary_network_id")}
    names = {network["id"]: network["name"] for network in document["frontend_networks"]}
    frontend |= {network["id"] for network in document["frontend_networks"] if network["type"] == "HOST"}
    for holder in entries:
        for network_id in sorted(holder.get("networks") or []):
            if network_id in frontend:
                continue
            raise QumuloError(
                f"creating tenant {name} would make a second tenant while tenant {holder['name']} holds network "
                f"{named_network(network_id, names)}, which is not on the frontend bond, a frontend VLAN or a HOST network, and "
                "with two tenants such a network aborts every node (G-TENANT-BACKEND). Unbind it first with a tenant "
                "PATCH of networks that leaves it out (qq multitenancy_modify_tenant --network-id), "
                "since qq network_mod_network --clear-tenant-id puts it back in the only tenant"
            )


def _has_override(client, route, tenant_id):
    return any(override.get("key") == tenant_id for override in client.get(route).data)


class Tenant(Resource):
    """tenant: one tenant, found by name, written by POST, PATCH and DELETE; its networks belong to the network document."""

    RETURN_KEY = "tenant"
    READ_ONLY: ClassVar[frozenset[str]] = frozenset({"networks"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["name"]
        self.previous_name = None if self.params["state"] == "absent" else self.params.get("previous_name")
        self.entries = []
        self.removed_overrides = []
        self.document = None  # the network document, read at most once per run

    def describe(self):
        return f"tenant '{self.name}'"

    def _network(self):
        if self.document is None:
            self.document = self.client.get(NETWORK).data
        return self.document

    def fetch(self):
        forget_tenants(self.client)
        self.entries = self.client.get(TENANTS).data["entries"]
        for wanted in (self.name, self.previous_name):
            found = next((entry for entry in self.entries if wanted is not None and entry["name"] == wanted), None)
            if found is not None:
                return Current(found["id"], found)
        return None

    def normalize(self, current):
        return tenant_options(current.data)

    def desired(self, before):
        after = {"name": self.name}
        for switch in SWITCHES:
            given = self.params.get(switch)
            after[switch] = before.get(switch, False) if given is None else given
        after["networks"] = before.get("networks", [])
        return after

    def run(self):
        check = self.module.check_mode
        current = self.fetch()
        before = self.normalize(current) if current else {}
        if self.params["state"] == "absent":
            if current is None:
                return self.result(False, {}, {}, [])
            self.check_delete(current)
            self._judge(current, before, None, f"deleting tenant {self.name}")
            count = len(self.entries)
            if not check:
                self._delete(current)
            self._warn(f"deleting tenant {self.name}", count, count - 1)  # after the write, so a refused one warns of nothing
            return self.result(True, before, {}, [])
        after = self.desired(before)
        creating = current is None
        keys = self.changes(before, after, creating)
        if creating and len(self.entries) == 1:
            check_second_tenant(self.name, canonical_document(self._network()), self.entries)
        changed_rest = [switch for switch in REST_SWITCHES if switch in keys]
        if creating:
            self._judge(current, before, after, f"creating tenant {self.name}")
        elif changed_rest:
            settings = " and ".join(f"{switch}: {str(after[switch]).lower()}" for switch in changed_rest)
            self._judge(current, before, after, f"setting {settings} on tenant {self.name}")
        count = len(self.entries)
        if (keys or creating) and check:
            self._cloud_warning()
        if (keys or creating) and not check:
            after = self._write(current, after, keys, creating, [])
        if creating:
            self._warn(f"creating tenant {self.name}", count, count + 1)  # after the write, so a refused one warns of nothing
        if not self.wrote:
            before = self.reread
        return self.result((bool(keys) or creating) and self.wrote, before, after, keys)

    def _warn(self, write, before, after):
        """Spec FR-008: the first create of a second tenant and the delete that leaves one rebind every listener.

        Qumulo Core's documentation says every tenant create removes the static routes of every node, so a create warns of
        that at any tenant count.
        """
        parts = []
        if listener_switch(before, after):
            parts.append(
                f"changes the cluster from {before} tenant{'s' if before > 1 else ''} to {after}: every listener on every node "
                "rebinds and every NFS and replication connection ends (G-TENANT-LISTEN)"
            )
        if after > before:
            parts.append("removes the static routes of every node (G-TENANT-ROUTES)")
        if parts:
            self.client.warnings.append(f"{write} {', and it '.join(parts)}")

    def _cloud_warning(self):
        warning = cloud_warning(self.client)
        if warning:
            self.client.warnings.append(warning)

    def _judge(self, current, before, after, write):
        """Refuse a judged write that would cut this play off from the cluster (spec 014, "The continuity rule")."""
        if self.params["allow_management_disruption"]:
            return
        tenants, bindings = self._after_write(current, after)
        declared = self.params.get("management_network")
        declared = None if declared is None else set(declared)

        def documents():
            document = canonical_document(self._network())
            return document, document  # a tenant write leaves the document as it is

        # In check mode the role's planned networks are those of the planned document, which step 3 has not written yet.
        planned = self.module.check_mode and self.params.get("planned_networks") is not None

        _management, message = continuity.verdict(
            write=write,
            in_role=False,
            api_host=self.params["api_host"],
            declared=declared,
            tenants=tenants,
            bindings=bindings,
            cluster_port=self.params.get("cluster_port"),
            api_port=self.params["api_port"],
            documents=documents,
            addresses=lambda: continuity.api_host_addresses(self.params["api_host"]),
            status=continuity.status_on_demand(declared, lambda: read_status(self.client), "tenant"),
            planned_ids=frozenset(bindings) if planned else frozenset(),
            tenant=self.name,
        )
        if message:
            raise QumuloError(message)

    def _after_write(self, current, after):
        """The tenants' switches and the network bindings as they will be immediately after this write.

        In check mode the role passes them as planned_tenants and planned_networks; otherwise they are the cluster's,
        with this write applied. after is None for a delete.
        """
        check = self.module.check_mode
        planned_tenants, planned_networks = self.params.get("planned_tenants"), self.params.get("planned_networks")
        if check and planned_tenants is not None:
            needs = continuity.rest_switches(self.params.get("cluster_port"), self.params["api_port"])
            tenants = {name: dict.fromkeys(needs, boolean(enabled)) for name, enabled in planned_tenants.items()}
        else:
            tenants = {entry["name"]: entry for entry in self.entries if current is None or entry["id"] != current.id}
            if after is not None:
                tenants[after["name"]] = after
        if check and planned_networks is not None:
            bindings = {int(network_id): name for network_id, name in planned_networks.items()}
        else:
            bindings = {}
            for entry in self.entries:
                mine = current is not None and entry["id"] == current.id
                if mine and after is None:
                    continue
                for network_id in entry.get("networks") or []:
                    bindings[network_id] = after["name"] if mine else entry["name"]
        return tenants, bindings

    def result(self, changed, before, after, keys):
        result = super().result(changed, before, after, keys)
        if self.removed_overrides:
            result["removed_overrides"] = self.removed_overrides
        return result

    def check_delete(self, current):
        if len(self.entries) == 1:
            raise QumuloError(continuity.no_tenant_left(False, self.name, ()))
        held = [] if self.module.check_mode and self.params["content_removed"] else self._content(current)
        if held:
            raise QumuloError(
                f"tenant {self.name} still holds {listed(held)}. The cluster deletes a tenant's shares, exports and SMB and NFS "
                "overrides with it (G-TENANT-CASCADE), so the module deletes only a tenant that holds none: remove its shares "
                "and exports, and move its networks to another tenant with network_config, first"
            )
        self.removed_overrides = [kind for kind, route in (("smb", SMB_OVERRIDES), ("nfs", NFS_OVERRIDES)) if _has_override(self.client, route, current.id)]

    def _content(self, current):
        held = []
        if current.data.get("networks"):
            names = {network["id"]: network["name"] for network in self._network().get("frontend_networks") or []}
            held += [f"network {named_network(network_id, names)}" for network_id in sorted(current.data["networks"])]
        held += [f"SMB share {s['share_name']!r}" for s in self.client.get(SHARES).data["entries"] if s.get("tenant_id") == current.id]
        held += [f"NFS export {e['export_path']!r}" for e in self.client.get(EXPORTS).data["entries"] if e.get("tenant_id") == current.id]
        return held

    def delete(self, current):
        item = self.client.get(TENANT, params={"tenant_id": current.id})
        self._send(lambda: self.client.delete(TENANT, params={"tenant_id": current.id}, if_match=item.etag))

    def create(self, after):
        body = {**{key: after[key] for key in ("name", *SWITCHES)}, "networks": [], "identity_config_id": create_identity_config(self.entries)}
        self._send(lambda: self.client.post(TENANTS, body=body))

    def update(self, current, after, keys):
        seen = {}

        def build(data):
            seen["tenant"] = data
            return {key: after[key] for key in keys if data.get(key) != after[key]} or None

        # A re-read after a 412, or the item read itself, may already hold the change; then nothing is written.
        if self._send(lambda: self.client.read_modify_write(TENANT, build, method="PATCH", params={"tenant_id": current.id})) is None:
            self.wrote = False
            self.reread = tenant_options(seen["tenant"])

    def _send(self, send):
        try:
            return send()
        except QumuloApiError as e:
            raise platform_refusal(e) from None
        finally:
            forget_tenants(self.client)


def tenants_facts(client):
    """tenant_info's facts: every tenant, by name, in tenant's option shape with its read-only id, networks and identity configuration."""
    entries = client.get(TENANTS).data["entries"]
    return {
        "changed": False,
        "tenants": [
            {"id": entry["id"], **tenant_options(entry), "identity_config_id": entry.get("identity_config_id")}
            for entry in sorted(entries, key=lambda entry: entry["name"])
        ],
    }


def run_scoped(module, client, cls):
    """smb_share's and nfs_export's run: the object in the tenant O(tenant) names, or one run per tenant of O(tenants).

    cls is the area's Resource, built with tenant_id fixed, with check_creatable() to refuse from its list read alone.
    tenant_id, deprecated, is accepted beside tenant only when both name one tenant. Alone, it is refused where the
    tenant list shows no tenant holds it, and used as given where the list cannot be read. A tenant the cluster lacks
    satisfies state: absent, and in check mode is pending for a create. Results name each tenant beside its id, where the tenant list can be read: a run without
    tenant or tenants still works for a user without PRIVILEGE_TENANT_READ, or on a release without multitenancy.
    """
    params = module.params
    cls(module, client)  # checks the options before any request
    if params.get("tenant") is None and params.get("tenants") is None:
        names = smb.tenant_names(client)  # before the write, so a failure to read it cannot follow one
        tenant_id = params.get("tenant_id")
        if tenant_id is not None and names and tenant_id not in names:
            # Tenant ids are never reused, so this one names no tenant: nothing in it to remove, and none to create in.
            if params["state"] == "absent":
                return {"changed": False, cls.RETURN_KEY: {}, **_empty_diff(module)}
            raise QumuloError(f"tenant_id {tenant_id} is no tenant on the cluster; name its tenant with tenant")
        return _named_result(cls.RETURN_KEY, cls(module, client, tenant_id=tenant_id).run(), names)
    ids = tenant_ids(client)
    absent = params["state"] == "absent"

    def resolved(name):
        if name in ids.by_name:
            return ids.by_name[name]
        return None if absent else resolve(client, name, module.check_mode)

    names = params.get("tenants")
    if names is None:
        tenant_id = params.get("tenant_id")
        name = params.get("tenant")
        found = resolved(name)
        # Under state: absent a tenant the cluster lacks and a tenant_id no tenant holds both name nothing, so they agree.
        if tenant_id is not None and found != tenant_id and not (found is None and tenant_id not in ids.by_id):
            other = ids.by_id.get(tenant_id)
            raise QumuloError(
                f"tenant {name!r} and tenant_id {tenant_id}, which is {f'tenant {other!r}' if other else 'no tenant on the cluster'}, differ; give tenant alone"
            )
        if found is None:
            return _named_result(cls.RETURN_KEY, {"changed": False, cls.RETURN_KEY: {}, **_empty_diff(module)}, ids.by_id)
        return _named_result(cls.RETURN_KEY, _scoped(module, client, cls, name, found).run(), ids.by_id)
    if not names:
        raise QumuloError("tenants is empty; name at least one tenant, or leave tenants out")
    if "all" in names:
        raise QumuloError("tenants: all is not a tenant's name here; name the tenants")
    if len(set(names)) < len(names):
        raise QumuloError(f"tenants names {listed(repr(n) for n in sorted({n for n in names if names.count(n) > 1}))} more than once")
    found = {name: resolved(name) for name in names}  # every name before the first write
    if not absent:
        # Also before the first write, from the one list read: a tenant that lacks the object and that the options cannot create it in.
        for name in names:
            try:
                cls(module, client, tenant_id=found[name]).check_creatable()
            except QumuloError as e:
                raise QumuloError(f"tenant {name!r}: {e}", **e.details) from e
    results = {}
    for name in names:
        try:
            result = (
                {"changed": False, cls.RETURN_KEY: {}, **_empty_diff(module)} if found[name] is None else _scoped(module, client, cls, name, found[name]).run()
            )
        except QumuloError as e:
            written = [] if module.check_mode else [n for n, r in results.items() if r["changed"]]
            if not written:
                raise QumuloError(f"tenant {name!r}: {e}", **e.details) from e
            raise QumuloError(
                f"tenant {name!r}: {e}; the task had already changed {'tenant' if len(written) == 1 else 'tenants'} {listed(repr(n) for n in written)}",
                **{**e.details, "changed": True},
            ) from e
        results[name] = _named_result(cls.RETURN_KEY, result, ids.by_id)
    combined = {"changed": any(result["changed"] for result in results.values()), cls.RETURN_KEY: {n: r[cls.RETURN_KEY] for n, r in results.items()}}
    notes = [f"{name}: {note}" for name, result in results.items() for note in result.get("notes", ())]
    if notes:
        combined["notes"] = notes
    if module._diff:
        combined["diff"] = {side: {n: r["diff"][side] for n, r in results.items()} for side in ("before", "after")}
    return combined


def _scoped(module, client, cls, name, tenant_id):
    """cls in the tenant named name, whose id is tenant_id, noting a tenant an earlier task creates."""
    scoped = cls(module, client, tenant_id=tenant_id)
    if isinstance(tenant_id, Pending):
        scoped.notes.append(pending_note(name))
    return scoped


def _empty_diff(module):
    return {"diff": {"before": {}, "after": {}}} if module._diff else {}


def _named_result(key, result, names):
    """A result whose object, and diff sides, carry tenant by name before tenant_id; a pending tenant has a null id.

    names maps tenant ids to names; an id it lacks is named None.
    """

    def named(state):
        if not state or "tenant_id" not in state:
            return state
        tenant_id = state["tenant_id"]
        if isinstance(tenant_id, Pending):
            return smb.with_tenant({**state, "tenant_id": None}, {None: str(tenant_id)})
        return smb.with_tenant(state, names)

    result = {**result, key: named(result[key])}
    if "diff" in result:
        result["diff"] = {**result["diff"], "before": named(result["diff"]["before"]), "after": named(result["diff"]["after"])}
    return result
