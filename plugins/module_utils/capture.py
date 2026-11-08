# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""capture_info: a cluster's configuration as the inventory variables that declare it.

Each area reads what one or more of the collection's modules write, through the readers their info modules use, and
writes it in the shape the cluster_baseline role passes to those modules, so that a check run of the role from the
captured variables changes nothing. The area names are the role's, from roles/cluster_baseline/vars/main.yml, plus
replication, which the replication roles will read.

No secret is written. The one read, the keytab's keys in the kerberos area, names the principals and is dropped. Each
secret the inventory must supply to manage an area is listed in supply instead, and what was read but not written, with
the reason, in left_out.

A share, an export or a network sits under the tenant that holds it, in qumulo_tenants, when the tenants area is captured
(spec 014, FR-022): the network, smb and nfs areas hand what a tenant holds to the tenants area, which runs after them.
Otherwise they write the flat forms of FR-022's tenants-left-out rule.
"""

from __future__ import annotations

import json
import ssl
from typing import NamedTuple

from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy, tls
from ansible_collections.qumulo.cluster.plugins.module_utils.auth import IDENTITY_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import (
    CAPACITY_CLAMP,
    LICENSE_STATUS,
    NOT_OFFERED_STATUS,
    PROTECTION,
    SETTINGS,
    SSL_CIPHERS,
    UNPROTECTED_EDGE,
    WEB_UI,
    SslCiphers,
    WebUiSettings,
    bytes_or_none,
    get_offered,
    node_state,
    product,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.dirsvc import JOINED, read_ad
from ansible_collections.qumulo.cluster.plugins.module_utils.files import acl_beyond_mode, attributes, directory_options, inside
from ansible_collections.qumulo.cluster.plugins.module_utils.filesystem import PERMISSIONS_MODES, SETTINGS_DOCUMENTS, settings_options
from ansible_collections.qumulo.cluster.plugins.module_utils.ftp import FTP_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import IdentityResolver, render
from ansible_collections.qumulo.cluster.plugins.module_utils.kerberos import MAPPING, keytab_entries
from ansible_collections.qumulo.cluster.plugins.module_utils.licensing import ACTIVE
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import (
    AUDIT_CLOUDWATCH_AREA,
    AUDIT_DESTINATIONS,
    CLOUDWATCH,
    MONITORING_FIELDS,
    NEXUS_CONNECTION,
    NEXUS_CONNECTION_FIELDS,
    NEXUS_REGISTRATION,
    SUPPORT_SETTINGS,
    audit_destination_options,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.network import (
    AUTHORITATIVE_DNS,
    DNS,
    FQDN_AREA,
    FQDN_OPTIONS,
    LOOKUP_OVERRIDES,
    NETWORK,
    TIME,
    AuthoritativeDns,
    DnsLookupOverrides,
    DnsSettings,
    TimeSettings,
    backend_networks,
    canonical_document,
    duplicated_addresses,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import NFS_INFO_SUBSETS, NfsSettings
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import ACCEPTED, DELETING, EVICTION, PORTAL_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import ACTIVE as PORTAL_ACTIVE
from ansible_collections.qumulo.cluster.plugins.module_utils.quotas import quota_statuses
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import DEFAULT_PORT, ENDED, ESTABLISHED, REPLICATION_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import NO_POLICY, S3Settings, all_keys, as_trustee, bucket_options, list_buckets, read_policy
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import SETTINGS as S3_SETTINGS
from ansible_collections.qumulo.cluster.plugins.module_utils.saml import FACTORY as SAML_FACTORY
from ansible_collections.qumulo.cluster.plugins.module_utils.security_keys import list_keys
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SMB_INFO_SUBSETS, SmbSettings, tenant_settings
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import SNAPSHOT_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_path import CLOUD, host_kind

AREAS = (
    "cluster",
    "filesystem",
    "web_ui",
    "network",
    "dns",
    "time",
    "ssl",
    "monitoring",
    "audit",
    "capacity",
    "local_groups",
    "local_users",
    "ad",
    "kerberos",
    "saml",
    "identity_mappings",
    "roles",
    "portal",
    "directories",
    "smb",
    "nfs",
    "tenants",  # after network, smb and nfs, which hand it what each tenant holds
    "s3",
    "ftp",
    "quotas",
    "snapshots",
    "replication",
)
FLOATING_IPS = "network.floating_ips"
PARTS = (FLOATING_IPS, FQDN_AREA, AUDIT_CLOUDWATCH_AREA)  # the parts of an area that leave_out can name on their own

# Every module that writes configuration, and the area that reads back what it writes. A module that writes none is in
# NOT_CAPTURED, with the reason. tests/unit/test_capture_coverage.py fails on a module in neither.
CAPTURED = {
    "ad_domain": "ad",
    "audit_destination": "audit",
    "authoritative_dns": "dns",
    "capacity_clamp": "capacity",
    "cluster_settings": "cluster",
    "directory": "directories",
    "directory_quota": "quotas",
    "dns_lookup_overrides": "dns",
    "dns_settings": "dns",
    "file_acl": "directories",
    "filesystem_settings": "filesystem",
    "ftp_settings": "ftp",
    "kerberos_keytab": "kerberos",
    "kerberos_settings": "kerberos",
    "license": "capacity",
    "local_group": "local_groups",
    "local_user": "local_users",
    "monitoring_settings": "monitoring",
    "network_config": "network",
    "nexus_connection": "monitoring",
    "nexus_registration": "monitoring",
    "nfs_export": "nfs",
    "nfs_settings": "nfs",
    "portal_eviction_settings": "capacity",
    "portal_hub": "portal",
    "portal_spoke": "portal",
    "replication_relationship": "replication",
    "replication_target": "replication",
    "role": "roles",
    "role_member": "roles",
    "s3_access_key": "s3",
    "s3_bucket": "s3",
    "s3_bucket_policy": "s3",
    "s3_settings": "s3",
    "saml_settings": "saml",
    "smb_settings": "smb",
    "smb_share": "smb",
    "snapshot_policy": "snapshots",
    "tenant": "tenants",
    "ssl_certificate": "ssl",
    "ssl_ciphers": "ssl",
    "time_settings": "time",
    "user_defined_mappings": "identity_mappings",
    "web_ui_settings": "web_ui",
}
NOT_CAPTURED = {
    "access_token": "the baseline takes no access tokens yet, so there is no inventory variable for capture to write",
    "auth_cache_clear": "it clears a cache, a one-shot action that leaves no configuration",
    "dns_cache_clear": "it clears the DNS cache, a one-shot action that leaves no configuration on the cluster",
    "encryption_key_rotation": "it rotates the at-rest encryption key, a one-shot action guarded by the key's age that leaves no setting",
    "identity_attributes": "the cluster lists no identity that has attributes, so capture cannot find them (G-ID-ATTRIBUTES)",
    "nexus_cluster": "it retires a cluster in Nexus, a one-shot action that leaves no configuration on the cluster",
    "replication_restore_lock": "it holds a restore's lock, a file on the controller that leaves no configuration on the cluster",
    "replication_reverse": ("it sends a failover verb to one relationship, a one-shot action; the records it leaves are what capture reads, as replication"),
    "snapshot": "it takes a snapshot, a one-shot action; capture declares the policies that take snapshots, not the snapshots",
    "security_key": (
        "the cluster registers a key only with its name signed by its private key, which it does not return; capture lists each key in left_out by name and id"
    ),
    "snapshot_lock": "a lock belongs to one snapshot, which capture does not declare, since a snapshot is taken by a one-shot action",
    "unprotected_edge_create": "it forms the cluster, a one-shot action; the formed cluster is what capture reads",
    "upgrade": "it upgrades the cluster, a one-shot action whose outcome is the running release, which cluster_info reads",
}

# The settings of a source relationship replication_relationship writes; the timezone is written only once set.
SOURCE_SETTINGS = ("replication_enabled", "map_local_ids_to_nfs_ids", "source_root_read_only", "blackout_windows")
FACTORY_SHARE = "Files"
FACTORY_EXPORT = "/"
TENANTS_AREA = "tenants"
INHERIT = "inherit"  # a tenant's SMB or NFS settings when it has no override
PER_TENANT_IDENTITY_WHY = (
    "it differs from tenant 1's, and choosing a tenant's identity configuration is per-tenant identity, which the "
    "collection does not support yet; "
    "the tenant's services, networks, settings, shares and exports are written"
)
NO_TENANT_1 = "the cluster has no tenant 1, which formation creates as Default with the factory Files share and / export"
# The factory items, each with the qumulo_factory_defaults key that claims it.
FACTORY_ITEMS = (("smb_files_share", "Files share"), ("nfs_root_export", "/ export"))
UNBOUND_WHY = (
    "no tenant holds it, and with more than one tenant the baseline refuses a new network without one, so a replay onto a "
    "rebuilt cluster with several tenants refuses it; list it under the tenant that is to hold it"
)
BACKEND_HELD_WHY = (
    "it is on the backend bond, which takes no tenant, so it is written in qumulo_network.frontend_networks; tenant {tenant} "
    "holds it, and with two tenants a tenant network off the frontend interfaces aborts every node (G-TENANT-BACKEND). "
    "Unbind it with a tenant PATCH of networks that leaves it out"
)
POLICY_LOCK_KEY_WHY = (
    "the policy locks its snapshots with the key of id {key} here, whose name capture could not read or which has none "
    "(QUIRK-120), and an id names another key on another cluster; declare the key in the policy's lock_key, by its name, "
    "or by its id on the cluster the inventory is applied to when it has no name"
)
KEY_WHY = (
    "the cluster registers a key only with its name signed by its private key, and returns neither; register it with "
    "qumulo.cluster.security_key, which takes the private key or that signature"
)
REGISTERED = "REGISTERED"
LICENSE_NONE = "LICENSE_STATE_NONE"
GUEST = "guest"  # the built-in user, which has no password until one is set for it
PORTAL_HOSTS = "qumulo_portal_hosts"
REPLICATION_ADDRESS = "qumulo_replication_address"
KEYTAB_NOTE = "the keytab"  # what the kerberos area's note is about, which a clone restates
KEYTAB_UNREADABLE_WHY = (
    "the API user may not read it, which needs PRIVILEGE_KERBEROS_KEYTAB_READ; if the cluster holds one, give "
    "qumulo_kerberos.keytab, encrypted with ansible-vault, to install it again"
)
PASSWORD_WHY = (
    "the cluster never returns a password; give the user's password, encrypted with ansible-vault, to set it when the "
    "user is created, or on every run with update_password: always"
)


class Placement(NamedTuple):
    """Where the smb and nfs areas put their shares or exports and the tenants' overrides."""

    variable: str  # the flat list, which holds the shared entries
    entry_key: str  # the list under a tenant entry
    settings_key: str  # the override under a tenant entry
    global_variable: str  # the global settings
    name_key: str
    fold: bool  # whether names match without regard to case
    factory: str  # the share or export formation creates in tenant 1
    protocol: str
    plural: str


SMB_PLACEMENT = Placement("qumulo_smb_shares", "smb_shares", "smb_settings", "qumulo_smb_settings", "share_name", True, FACTORY_SHARE, "SMB", "SMB shares")
NFS_PLACEMENT = Placement(
    "qumulo_nfs_exports", "nfs_exports", "nfs_settings", "qumulo_nfs_settings", "export_path", False, FACTORY_EXPORT, "NFS", "NFS exports"
)
# A tenant entry's keys, in the order the inventory writes them.
TENANT_KEYS = ("name", "previous_name", *tenancy.SWITCHES, "networks", "smb_settings", "nfs_settings", "smb_shares", "nfs_exports")


def _factory_named(placement, name):
    """Whether name is the factory share's or export's, compared as the cluster compares it."""
    return name.casefold() == placement.factory.casefold() if placement.fold else name == placement.factory


def _set(entry):
    """entry without its unset keys: the role passes an unset key as omit, which keeps the cluster's value."""
    return {key: value for key, value in entry.items() if value is not None}


def _restriction(restriction):
    """An NFS export restriction without require_tls when it is false.

    From 7.11.0 the cluster stores false in every restriction written without require_tls, so leaving it out declares
    the same export. nfs_export refuses require_tls on a release before 7.11.0, even false, so writing it would tie the
    inventory to 7.11.0 or later.
    """
    if restriction.get("require_tls") is False:
        return {key: value for key, value in restriction.items() if key != "require_tls"}
    return restriction


def _ancestors(path):
    """path and every directory above it, the root included."""
    parts = path.strip("/").split("/") if path != "/" else []
    return {"/"} | {"/" + "/".join(parts[: depth + 1]) for depth in range(len(parts))}


def _vlan_networks(vlan):
    """The ids of the networks a frontend VLAN carries."""
    return {vlan.get(key) for key in ("network_id", "secondary_network_id")} - {None}


def _comparable(item, name_key, fold):
    """item as two tenants' copies compare: without its tenant, an SMB name casefolded (spec 014, FR-022)."""
    folded = {**item, name_key: item[name_key].casefold() if fold else item[name_key]}
    return json.dumps(folded, sort_keys=True)


def _unique(entries, key):
    """entries sorted by key, each told once: two relationships to one target note its address twice."""
    unique = {(entry[key], entry["why"]): entry for entry in entries}
    return [unique[pair] for pair in sorted(unique)]


def _unique_hosts(hosts):
    unique = {(host["address"], host["port"]): host for host in hosts}
    return [unique[key] for key in sorted(unique)]


class Capture:
    def __init__(self, client, params):
        self.client = client
        self.wanted = set(AREAS) if "all" in params["gather_subset"] else set(params["gather_subset"])
        self.leave_out = set(params["leave_out"])
        self.inventory = {uuid.lower(): host for uuid, host in (params["inventory_clusters"] or {}).items()}
        self.host_vars, self.supply, self.left_out = {}, [], []
        # What this cluster records of each peer's own variables, and the variables of its own it needs from peers.
        self.peer_values, self.from_peers = [], set()
        # The source's spoke roots and incoming replication target directories, which a clone of it drops what lies in.
        self.spoke_roots, self.replication_targets = [], []
        self.unavailable, self.memo = {}, {}
        # What each tenant holds, by tenant id, as the network, smb and nfs areas hand it to the tenants area.
        self.held = {}

    def run(self):
        uuid = node_state(self.client)[0]
        if uuid is None:
            raise QumuloError(f"{self.client.params['api_host']} belongs to no cluster yet, so there is nothing to capture; form the cluster first")
        self.host_vars["qumulo_cluster_uuid"] = uuid
        for area in AREAS:
            if area not in self.wanted:
                continue
            if area in self.leave_out:
                self.note(f"the {area} area", "leave_out names it, as an area Terraform owns")
                continue
            try:
                getattr(self, f"_{area}")()
            except SubsetUnavailable as e:
                self.unavailable[area] = str(e)
                self.note(f"the {area} area", f"this cluster does not offer it: {e}")
        result = {
            "changed": False,
            "capture": {
                "host_vars": dict(sorted(self.host_vars.items())),
                "supply": _unique(self.supply, "variable"),
                "left_out": _unique(self.left_out, "what"),
                "peer_values": sorted(self.peer_values, key=lambda record: (record["uuid"], record["variable"], str(record["value"]))),
                "from_peers": sorted(self.from_peers),
                "spoke_roots": self.spoke_roots,
                "replication_targets": self.replication_targets,
            },
        }
        if self.unavailable:
            result["unavailable_subsets"] = self.unavailable
        return result

    def record_peer(self, uuid, variable, value, unusable):
        """What this cluster records of a peer's variable; unusable says why the record must not be copied, or is None."""
        self.peer_values.append(
            {"uuid": uuid, "variable": variable, "value": value, "recorded_by": self.host_vars["qumulo_cluster_uuid"], "unusable": unusable}
        )

    def need(self, variable, why):
        self.supply.append({"variable": variable, "why": why})

    def note(self, what, why):
        self.left_out.append({"what": what, "why": why})

    def read(self, name, reader):
        """reader(), called once per run: several areas need the same records."""
        if name not in self.memo:
            self.memo[name] = reader()
        return self.memo[name]

    def tenant_list(self):
        """The cluster's tenants as the list answers them, or None where the cluster does not offer the list."""

        def reader():
            try:
                return tenancy.tenant_entries(self.client)  # the read smb_info's and nfs_info's override readers share
            except QumuloApiError as e:
                if e.status not in NOT_OFFERED_STATUS:
                    raise
                self.memo["tenants_unavailable"] = str(e)
                return None

        return self.read("tenant_list", reader)

    def tenant_names(self):
        return {tenant["id"]: tenant["name"] for tenant in self.tenant_list() or []}

    def several_tenants(self):
        return len(self.tenant_list() or []) > 1

    def nesting(self):
        """Whether what a tenant holds goes under its entry in qumulo_tenants, which only the tenants area writes."""
        return TENANTS_AREA in self.wanted and TENANTS_AREA not in self.leave_out and self.tenant_list() is not None

    def why_tenants_left_out(self):
        """Why the tenants area is not written, for the notes of the flat forms."""
        if TENANTS_AREA in self.leave_out:
            return f"leave_out names {TENANTS_AREA}, as an area Terraform owns"
        if TENANTS_AREA not in self.wanted:
            return f"gather_subset leaves out the {TENANTS_AREA} area"
        return f"this cluster does not offer the tenant list: {self.memo.get('tenants_unavailable')}"

    def hand_over(self, tenant_id, key, value):
        """Give the tenants area what tenant_id holds under key of its entry."""
        self.held.setdefault(tenant_id, {})[key] = value

    def cloud_protection_type(self):
        """The protection type when it marks a cloud cluster, else None; a type that cannot be read marks none."""

        def reader():
            try:
                kind = get_offered(self.client, PROTECTION).get("protection_system_type")
            except SubsetUnavailable:
                return None
            return kind if host_kind(None, kind) == CLOUD else None

        return self.read("cloud", reader)

    def shares(self):
        return self.read("shares", lambda: SMB_INFO_SUBSETS["shares"](self.client)["shares"])

    def exports(self):
        return self.read("exports", lambda: NFS_INFO_SUBSETS["exports"](self.client)["exports"])

    def quotas(self):
        return self.read("quotas", lambda: quota_statuses(self.client))

    def policies(self):
        return self.read("policies", lambda: SNAPSHOT_INFO_SUBSETS["policies"](self.client)["policies"])

    def buckets(self):
        return self.read("buckets", lambda: [bucket_options(bucket) for bucket in list_buckets(self.client)])

    def s3_settings(self):
        return self.read("s3_settings", lambda: S3Settings.options(get_offered(self.client, S3_SETTINGS)))

    def spokes(self):
        return self.read("spokes", lambda: PORTAL_INFO_SUBSETS["spokes"](self.client)["spokes"])

    def relationships(self, side):
        """side is source or target."""
        return self.read(side, lambda: REPLICATION_INFO_SUBSETS[side](self.client)[f"{side}_relationships"])

    def spoke_root_of(self, path):
        return next((root["spoke_root"] for spoke in self.spokes() for root in spoke["roots"] if root["spoke_root"] and inside(path, root["spoke_root"])), None)

    def in_spoke_root(self, what, path):
        """Whether path lies in a spoke root, noting it if so: that data is the hub's, and the hub's capture declares it."""
        root = self.spoke_root_of(path)
        if root:
            self.note(what, f"it lies in the spoke root {root}, whose data the hub holds; the hub's capture declares it")
        return root is not None

    def _cluster(self):
        self.host_vars["qumulo_cluster_name"] = get_offered(self.client, SETTINGS)["cluster_name"]

    def _filesystem(self):
        settings = settings_options({route.path: get_offered(self.client, route) for route, _method in SETTINGS_DOCUMENTS})
        if settings.get("permissions_mode") not in (None, *PERMISSIONS_MODES):
            self.note("the permissions mode", f"the cluster uses {settings.pop('permissions_mode')}, which filesystem_settings does not offer")
        self.host_vars["qumulo_filesystem_settings"] = settings

    def _web_ui(self):
        settings = WebUiSettings.options(get_offered(self.client, WEB_UI))
        if not float(settings.get("inactivity_timeout", 0)).is_integer():
            # Set in seconds elsewhere; web_ui_settings takes whole minutes.
            minutes = settings.pop("inactivity_timeout")
            self.note("the Web UI's inactivity timeout", f"it is {minutes} minutes, and web_ui_settings takes whole minutes")
        self.host_vars["qumulo_web_ui_settings"] = settings

    def _network(self):
        document = canonical_document(get_offered(self.client, NETWORK))
        names, several, nesting = self.tenant_names(), self.several_tenants(), self.nesting()
        interfaces = document["managed_interfaces"]
        backend = backend_networks(document)
        vlans = {network_id: vlan for vlan in (interfaces or {}).get("frontend_vlans") or [] for network_id in _vlan_networks(vlan)}
        flat, nested = [], {}
        for network in document["frontend_networks"]:
            item = _set({key: value for key, value in network.items() if value != [] and key != "tenant_id"})
            if FLOATING_IPS in self.leave_out:
                item.pop("floating_ip_ranges", None)
            tenant = network["tenant_id"] if network["tenant_id"] in names else None
            place = f"network {network['id']} ({network['name']})"
            if network["id"] in backend:
                if several and tenant is not None:
                    self.note(f"the tenant of {place}", BACKEND_HELD_WHY.format(tenant=names[tenant]))
                flat.append(item)
            elif tenant is None:
                # A cluster without tenant 1 rebuilds with the Default a newly formed one has beside its own tenants.
                if several or (nesting and names and tenancy.FORMATION_TENANT_ID not in names):
                    self.note(f"the tenant of {place}", UNBOUND_WHY)
                flat.append(item)
            elif nesting:
                vlan = vlans.get(network["id"])
                if vlan is not None:
                    item["vlan_id"] = vlan["vlan_id"]
                    if vlan.get("mtu") is not None:
                        item["mtu"] = vlan["mtu"]
                nested.setdefault(tenant, []).append(item)
            else:
                # With one tenant the collection's network PUT carries each network's tenant over as the cluster reports it.
                if several:
                    item["tenant"] = names[tenant]
                flat.append(item)
        if FLOATING_IPS in self.leave_out:
            self.note("the floating IP ranges", f"leave_out names {FLOATING_IPS}, as Terraform owns them")
        if several or nesting:
            self.note_shared_vlans(document, backend, names)
        self.memo["backend_networks"] = backend
        self.memo["host_managed"] = interfaces is None and all(network["type"] == "HOST" for network in document["frontend_networks"])
        if nesting:
            interfaces = self.without_tenant_networks(interfaces, {item["id"] for items in nested.values() for item in items})
            for tenant_id in names:
                self.hand_over(tenant_id, "networks", nested.get(tenant_id, []))
        self.host_vars["qumulo_network"] = _set({"frontend_networks": flat, "managed_interfaces": interfaces})

    @staticmethod
    def without_tenant_networks(interfaces, tenant_networks):
        """managed_interfaces without the VLANs that carry only tenant networks, and a frontend bond that lists none: a
        tenant network carries its VLAN, and tenancy_plan derives both from the tenants' networks.

        A VLAN that also carries a network no tenant holds stays, so step 2 refuses it as declared in two places rather
        than derive it from the tenant network alone, which would take the other network off it.
        """
        if interfaces is None or not tenant_networks:
            return interfaces
        vlans = [vlan for vlan in interfaces.get("frontend_vlans") or [] if not _vlan_networks(vlan) <= tenant_networks]
        out = {**interfaces, "frontend_vlans": vlans}
        bond = interfaces.get("frontend_bond_config")
        if bond:
            out["frontend_bond_config"] = {**bond, "networks": [network_id for network_id in bond.get("networks") or [] if network_id not in tenant_networks]}
        return out

    def note_shared_vlans(self, document, backend, names):
        """Note a VLAN that carries two tenants' networks, or a tenant's and one no tenant holds, and what a replay does."""
        tenant_of = {network["id"]: names.get(network["tenant_id"]) for network in document["frontend_networks"] if network["id"] not in backend}
        for vlan in (document["managed_interfaces"] or {}).get("frontend_vlans") or []:
            carried = sorted(_vlan_networks(vlan) & set(tenant_of))
            tenants = sorted({tenant_of[network_id] for network_id in carried} - {None})
            unbound = [network_id for network_id in carried if tenant_of[network_id] is None]
            if len(tenants) > 1:
                self.note(
                    f"VLAN {vlan['vlan_id']}",
                    f"it carries networks of the tenants {', '.join(tenants)}; the baseline warns of it on this cluster and refuses it on a "
                    "cluster that lacks it, as a rebuilt one; give each tenant a VLAN of its own",
                )
            elif tenants and unbound and self.nesting():
                self.note(
                    f"VLAN {vlan['vlan_id']}",
                    f"it carries a network of tenant {tenants[0]} and network {unbound[0]}, which no tenant holds; it is written in "
                    "qumulo_network.managed_interfaces and as the tenant network's vlan_id, and a check run from this capture refuses it "
                    f"as declared in two places; list network {unbound[0]} under {tenants[0]} with vlan_id {vlan['vlan_id']} and remove "
                    "the VLAN from qumulo_network.managed_interfaces.frontend_vlans, or give each network a VLAN of its own",
                )
            elif tenants and unbound:
                self.note(
                    f"VLAN {vlan['vlan_id']}",
                    f"it carries a network of tenant {tenants[0]} and network {unbound[0]}, which no tenant holds; a replay onto this "
                    f"cluster keeps it, and one onto a rebuilt cluster with several tenants refuses network {unbound[0]}; give it a tenant",
                )

    def _dns(self):
        table = get_offered(self.client, LOOKUP_OVERRIDES)
        dns = {**DnsSettings.options(get_offered(self.client, DNS)), "lookup_overrides": DnsLookupOverrides.options(table)["overrides"]}
        # Noted once both reads have answered, so an area left out as unavailable carries no note of its own.
        for address in duplicated_addresses(table["lookup_overrides"]):
            self.note(
                f"the lookup overrides of {address}",
                "the cluster lists the address in more than one entry; they are captured as one, names in table order",
            )
        try:
            authoritative = AuthoritativeDns.options(get_offered(self.client, AUTHORITATIVE_DNS))
        except SubsetUnavailable as e:
            # The DNS settings are still worth capturing on a cluster without the authoritative server.
            authoritative = None
            self.note("the authoritative DNS settings", f"this cluster does not offer them: {e}")
        if authoritative is not None and FQDN_AREA in self.leave_out:
            for option in FQDN_OPTIONS:
                authoritative.pop(option, None)
            self.note("the authoritative DNS name and whether it answers", f"leave_out names {FQDN_AREA}, as Terraform owns them")
        self.host_vars["qumulo_dns"] = dns
        if authoritative is not None:
            self.host_vars["qumulo_authoritative_dns"] = authoritative

    def _time(self):
        self.host_vars["qumulo_time"] = TimeSettings.options(get_offered(self.client, TIME))

    def _ssl(self):
        try:
            self.host_vars["qumulo_ssl_ciphers"] = SslCiphers.options(get_offered(self.client, SSL_CIPHERS))
        except SubsetUnavailable as e:
            self.note("the TLS cipher suites", f"this cluster does not offer them: {e}")
        params = self.client.params
        try:
            leaf = tls.served_leaf(params["api_host"], params["api_port"], params["request_timeout"])
            served = f"serves a certificate whose SHA-256 is {tls.sha256(leaf)}"
        except (OSError, ssl.SSLError) as e:
            served = f"serves a certificate that could not be read ({e})"
        why = f"the cluster {served}, and the API returns neither the certificate nor its key (G-SSL-READBACK)"
        self.need("qumulo_ssl.certificate", why)
        self.need("qumulo_ssl.private_key", why)

    def _monitoring(self):
        settings = get_offered(self.client, SUPPORT_SETTINGS)
        monitoring = _set({name: settings[name] for name in MONITORING_FIELDS if name in settings})
        try:
            connection = get_offered(self.client, NEXUS_CONNECTION)
            registration = get_offered(self.client, NEXUS_REGISTRATION)
        except SubsetUnavailable as e:
            self.note("the Nexus connection", f"this cluster does not offer it: {e}")
        else:
            monitoring["nexus"] = _set({name: connection[name] for name in NEXUS_CONNECTION_FIELDS if name in connection})
            status = registration.get("registration_status")
            if connection.get("nexus_enabled") and status != REGISTERED:
                self.need(
                    "qumulo_monitoring.nexus_join_key",
                    f"the cluster's Nexus registration is {status}, and a join key is single-use and never read back; give a new one, "
                    "or qumulo_nexus_api_token to mint one (G-NEXUS-KEY)",
                )
        self.host_vars["qumulo_monitoring"] = monitoring

    def _audit(self):
        destinations = []
        try:
            entries = get_offered(self.client, AUDIT_DESTINATIONS)["destinations"]
        except SubsetUnavailable as e:
            self.note("the audit log destinations", f"this cluster does not offer them: {e}")
            return
        for destination in sorted((audit_destination_options(entry["config"]) for entry in entries), key=lambda options: options["name"]):
            if destination["type"] == CLOUDWATCH and AUDIT_CLOUDWATCH_AREA in self.leave_out:
                self.note(f"the CloudWatch audit destination {destination['name']}", f"leave_out names {AUDIT_CLOUDWATCH_AREA}, as Terraform owns it")
            else:
                destinations.append(destination)
        self.host_vars["qumulo_audit_destinations"] = destinations

    def _capacity(self):
        capacity = {}
        clamp = bytes_or_none(get_offered(self.client, CAPACITY_CLAMP)["capacity_clamp"])
        if clamp is not None:
            capacity["clamp"] = clamp
        try:
            capacity["free_threshold"] = get_offered(self.client, EVICTION)["free_threshold"]
        except SubsetUnavailable as e:
            self.note("the eviction threshold", f"this cluster does not offer it: {e}")
        status = get_offered(self.client, LICENSE_STATUS)
        uuid = self.host_vars["qumulo_cluster_uuid"]
        licensed_to = ((status.get("license") or {}).get("cluster_uuid") or "").lower()
        if status["state"] == ACTIVE and licensed_to == uuid.lower():
            capacity["license"] = "required"
        elif status["state"] == LICENSE_NONE and self.unprotected_edge():
            capacity["license"] = "none"  # it runs without one, within the capacity its formation gave it (G-EDGE-FLOOR)
        else:
            self.need(
                "qumulo_capacity.license_document",
                f"the cluster holds no active license for its UUID, {uuid}; give the document Qumulo issues for that UUID, "
                "encrypted with ansible-vault (G-LICENSE)",
            )
        if capacity:
            self.host_vars["qumulo_capacity"] = capacity

    def unprotected_edge(self):
        try:
            return product(self.client)["cluster_type"] == UNPROTECTED_EDGE
        except SubsetUnavailable:
            return False

    def _local_groups(self):
        self.host_vars["qumulo_local_groups"] = [_set(group) for group in IDENTITY_INFO_SUBSETS["local_groups"](self.client)["local_groups"]]

    def _local_users(self):
        users = [_set(user) for user in IDENTITY_INFO_SUBSETS["local_users"](self.client)["local_users"]]
        self.host_vars["qumulo_local_users"] = users
        for user in users:
            if user["name"] != GUEST:
                self.need(f"qumulo_local_users[{user['name']}].password", PASSWORD_WHY)

    def _ad(self):
        ad = read_ad(self.client)
        if ad["status"] != JOINED:
            return
        if ad["domain_controllers"] is None:
            self.note("the configured domain controllers", "the cluster does not read them back for the joined domain (G-AD-DCS)")
        self.host_vars["qumulo_ad"] = _set({key: value for key, value in ad.items() if key != "status"})
        why = "the cluster keeps neither the join user nor its password; the role sends them only to join again, as after a rebuild"
        self.need("qumulo_ad.join_user", why)
        self.need("qumulo_ad.join_password", why)

    def _kerberos(self):
        settings = IDENTITY_INFO_SUBSETS["kerberos"](self.client)["kerberos"]
        self.host_vars["qumulo_kerberos"] = {MAPPING: bool(settings.get(MAPPING))}
        try:
            principals = sorted({entry["principal"] for entry in keytab_entries(self.client)})
        except QumuloApiError as e:
            if e.status != 403:
                raise
            # The read feeds only the supply line, so a user who may not read the keytab gets a note and the capture
            # goes on, as the key store's 403 does (spec 024 Decision 11, spec 022 Decision 6).
            self.note(KEYTAB_NOTE, KEYTAB_UNREADABLE_WHY)
            return
        if principals:
            self.need(
                "qumulo_kerberos.keytab",
                f"the cluster holds a keytab for {', '.join(principals)}; capture never writes a keytab or its keys, so give it, "
                "encrypted with ansible-vault, to install it again, as after a rebuild",
            )

    def _saml(self):
        saml = IDENTITY_INFO_SUBSETS["saml"](self.client)["saml"]
        if saml != SAML_FACTORY:  # declared only once SAML is set up; the certificate is public, so it is captured whole
            self.host_vars["qumulo_saml"] = saml

    def _identity_mappings(self):
        mappings = IDENTITY_INFO_SUBSETS["user_defined_mappings"](self.client)["user_defined_mappings"]["mappings"]
        if mappings:
            self.host_vars["qumulo_user_defined_mappings"] = sorted(mappings, key=lambda mapping: mapping["down_level_logon_name"])

    def _roles(self):
        custom = IDENTITY_INFO_SUBSETS["roles"](self.client)["roles"]
        if custom:
            self.host_vars["qumulo_roles"] = custom
        self.host_vars["qumulo_role_members"] = IDENTITY_INFO_SUBSETS["role_members"](self.client)["role_members"]

    def _portal(self):
        # Each side records only the other side's addresses (G-PORTAL-HOSTS); the playbook gives them to the peer's host
        # when every record of them agrees and comes from an ACCEPTED, ACTIVE portal.
        hubs = PORTAL_INFO_SUBSETS["hubs"](self.client)["hubs"]
        self.spoke_roots = sorted({root["spoke_root"] for spoke in self.spokes() for root in spoke["roots"] if root["spoke_root"]})
        records = [(spoke["hub_cluster_uuid"], spoke["hub_hosts"], spoke) for spoke in self.spokes()]
        records += [(hub["spoke_cluster_uuid"], hub["spoke_hosts"], hub) for hub in hubs]
        for peer, hosts, record in records:
            if record["state"] == DELETING:
                continue
            self.from_peers.add(PORTAL_HOSTS)
            active = record["state"] == ACCEPTED and record["status"] == PORTAL_ACTIVE
            self.record_peer(peer, PORTAL_HOSTS, _unique_hosts(hosts), None if active else f"{record['state']} and {record['status']}")
        declared = False
        for spoke in sorted(self.spokes(), key=lambda spoke: spoke["hub_cluster_uuid"]):
            what = f"the spoke portal to hub {spoke['hub_cluster_uuid']}"
            hub = self.inventory.get(spoke["hub_cluster_uuid"])
            if spoke["state"] != ACCEPTED:
                self.note(what, f"it is {spoke['state']}, not ACCEPTED")
            elif declared:
                self.note(what, "qumulo_portal declares one portal, and another is declared")
            elif hub is None:
                self.note(what, "no inventory host has that qumulo_cluster_uuid; add the hub to the inventory and capture again")
            elif any(root["spoke_root"] is None for root in spoke["roots"]):
                self.note(what, "a root's directory on this cluster no longer exists")
            elif any(root["hub_root"] is None for root in spoke["roots"]):
                self.note(what, "the hub has not reported the path of every root; capture again once the portal is ACTIVE")
            else:
                roots = [{"spoke_root": root["spoke_root"], "hub_root": root["hub_root"]} for root in spoke["roots"]]
                self.host_vars["qumulo_portal"] = {"hub": hub, "type": spoke["type"], "roots": roots}
                declared = True

    def _directories(self):
        named = {share["fs_path"] for share in self.shares()} | {export["fs_path"] for export in self.exports()}
        named |= {quota["path"] for quota in self.quotas()} | {policy["path"] for policy in self.policies()}
        named |= {bucket["path"] for bucket in self.buckets()}
        # s3_settings requires its base path to exist, and no bucket need lie under it.
        named |= {self.s3_settings()["base_path"]} - {"/"}
        targets = sorted({record["target_path"] for record in self.relationships("target") if record["state"] != ENDED})
        self.replication_targets = targets
        paths = {ancestor for path in named | set(targets) for ancestor in _ancestors(path)}
        # A replication target directory is declared by its path alone: replication sets its attributes and its content's.
        directories = [{"path": path} for path in targets]
        acls = []
        identities = IdentityResolver(self.client)
        for path in sorted(paths):
            if any(inside(path, target) for target in targets) or self.in_spoke_root(f"the directory {path}", path):
                continue
            found = attributes(self.client, path)
            if found is None:
                self.note(f"the directory {path}", "a share, an export, a quota or a snapshot policy names it, but it does not exist")
                continue
            directory = directory_options(identities, path, found.data)
            # A mode and an ACL are one fact (G-FS-ACL-MODE): an ACL its mode cannot express is declared in its place.
            acl = acl_beyond_mode(self.client, path, directory["mode"])
            if acl:
                del directory["mode"]
                acls.append(acl)
            directories.append(directory)
        self.host_vars["qumulo_directories"] = sorted(directories, key=lambda directory: directory["path"])
        if acls:
            self.host_vars["qumulo_file_acls"] = acls

    def _smb(self):
        self.host_vars["qumulo_smb_settings"] = SMB_INFO_SUBSETS["settings"](self.client)["settings"]
        shares = [_set(share) for share in self.shares()]
        self.place(SMB_PLACEMENT, shares, self.read("smb_overrides", lambda: self.overrides(tenancy.SMB_OVERRIDES, SmbSettings.options)))
        present = any(share["share_name"] == FACTORY_SHARE and share["tenant_id"] == tenancy.FORMATION_TENANT_ID for share in shares)
        self.host_vars.setdefault("qumulo_factory_defaults", {})["smb_files_share"] = "present" if present else "absent"

    def _nfs(self):
        self.host_vars["qumulo_nfs_settings"] = NFS_INFO_SUBSETS["settings"](self.client)["settings"]
        names, kept = self.tenant_names(), []
        for export in self.exports():
            if any(restriction.get(key) == "" for restriction in export["restrictions"] for key in ("map_to_user", "map_to_group")):
                of = f" of tenant {names[export['tenant_id']]}" if self.nesting() and export["tenant_id"] in names else ""
                self.note(
                    f"the NFS export {export['export_path']}{of}",
                    "a restriction maps to a user or group that no longer exists (G-MAPTO); declare the export with an existing one",
                )
            else:
                kept.append(_set({**export, "restrictions": [_restriction(restriction) for restriction in export["restrictions"]]}))
        self.place(NFS_PLACEMENT, kept, self.read("nfs_overrides", lambda: self.overrides(tenancy.NFS_OVERRIDES, NfsSettings.options)))
        present = any(export["export_path"] == FACTORY_EXPORT and export["tenant_id"] == tenancy.FORMATION_TENANT_ID for export in self.exports())
        self.host_vars.setdefault("qumulo_factory_defaults", {})["nfs_root_export"] = "present" if present else "absent"

    def overrides(self, route, options):
        """Each tenant's override by tenant name; none where the tenant list is not offered."""
        return tenant_settings(self.client, route, options) if self.tenant_list() else {}

    def place(self, placement, items, overrides):
        """Write the shares or exports placement names, each with its tenant_id, and the tenants' overrides, where FR-022 puts them."""
        names = self.tenant_names()
        if self.nesting():
            shared, nested = self.merged(placement, items, names)
            self.host_vars[placement.variable] = shared
            for tenant_id, name in names.items():
                self.hand_over(tenant_id, placement.settings_key, overrides.get(name, INHERIT))
                if nested.get(tenant_id):
                    self.hand_over(tenant_id, placement.entry_key, sorted(nested[tenant_id], key=lambda item: item[placement.name_key]))
            return
        why = self.why_tenants_left_out()
        if not self.several_tenants():
            # The flat form is exact on one tenant; its override has no flat form, and its clients see it.
            flat = [{key: value for key, value in item.items() if key != "tenant_id"} for item in items]
            self.host_vars[placement.variable] = sorted(flat, key=lambda item: item[placement.name_key])
            for name in sorted(overrides):
                self.note(
                    f"the {placement.protocol} settings of tenant {name}",
                    f"the tenant's own {placement.protocol} settings, which its clients see in place of {placement.global_variable}, have no "
                    f"flat form, and {why}",
                )
            return
        self.host_vars[placement.variable] = []
        scoped = f"they are the tenant's, which only the {TENANTS_AREA} area writes on a cluster with several tenants, and {why}"
        for tenant_id in sorted({item["tenant_id"] for item in items if item["tenant_id"] in names}, key=lambda tenant_id: names[tenant_id]):
            self.note(f"the {placement.plural} of tenant {names[tenant_id]}", scoped)
        for name in sorted(overrides):
            self.note(f"the {placement.protocol} settings of tenant {name}", scoped)

    @staticmethod
    def merged(placement, items, names):
        """The shared entries, and each tenant's other shares or exports, by FR-022's merge.

        Copies of one name are grouped by identical content; a group of two or more tenants becomes one shared entry,
        with tenants: all when it is every tenant, spelled as the first of its tenants by name spells it. The factory
        share or export in tenant 1 never merges.
        """
        nested, by_name = {}, {}
        for item in items:
            tenant_id = item["tenant_id"]
            if tenant_id not in names:
                continue
            copy = {key: value for key, value in item.items() if key != "tenant_id"}
            if tenant_id == tenancy.FORMATION_TENANT_ID and copy[placement.name_key] == placement.factory:
                nested.setdefault(tenant_id, []).append(copy)
                continue
            name = copy[placement.name_key].casefold() if placement.fold else copy[placement.name_key]
            by_name.setdefault(name, {}).setdefault(_comparable(copy, placement.name_key, placement.fold), []).append((names[tenant_id], tenant_id, copy))
        shared = []
        for groups in by_name.values():
            for copies in groups.values():
                if len(copies) == 1:
                    _name, tenant_id, copy = copies[0]
                    nested.setdefault(tenant_id, []).append(copy)
                    continue
                copies.sort(key=lambda found: found[0])
                tenants = [found[0] for found in copies]
                first = copies[0][2]
                entry = {placement.name_key: first[placement.name_key], "tenants": "all" if len(tenants) == len(names) else tenants}
                sort_key = (first[placement.name_key].casefold() if placement.fold else first[placement.name_key], tenants[0])
                shared.append((sort_key, {**entry, **{key: value for key, value in first.items() if key != placement.name_key}}))
        return [entry for _key, entry in sorted(shared, key=lambda found: found[0])], nested

    def _tenants(self):
        tenants = self.tenant_list()
        if tenants is None:
            raise SubsetUnavailable(self.memo["tenants_unavailable"])
        cloud = self.cloud_protection_type()
        if cloud:
            self.note(
                "the services of every tenant",
                f"this cluster's protection type, {cloud}, marks a cloud cluster, where Qumulo Core refuses every tenant write (G-TENANT-CLOUD)",
            )
        configs = {tenant["identity_config_id"] for tenant in tenants}
        reference = next((tenant["identity_config_id"] for tenant in tenants if tenant["id"] == tenancy.FORMATION_TENANT_ID), None)
        if reference is None and len(configs) == 1:
            reference = next(iter(configs))
        heir = self.default_heir(tenants)
        entries = []
        for tenant in sorted(tenants, key=lambda tenant: tenant["name"]):
            entry = {"name": tenant["name"]}
            if tenant is heir or tenant["id"] == tenancy.FORMATION_TENANT_ID and tenant["name"] != tenancy.FORMATION_TENANT_NAME:
                entry["previous_name"] = tenancy.FORMATION_TENANT_NAME
            if not cloud:
                entry.update({switch: bool(tenant.get(switch)) for switch in tenancy.SWITCHES})
            entry.update(self.held.get(tenant["id"], {}))
            entries.append({key: entry[key] for key in TENANT_KEYS if key in entry})
            if tenant["identity_config_id"] != reference:
                self.note(f"the identity configuration of tenant {tenant['name']}", PER_TENANT_IDENTITY_WHY)
        if heir is not None:
            self.rename_default(heir)
        elif all(tenant["id"] != tenancy.FORMATION_TENANT_ID for tenant in tenants):
            entries += self.missing_tenant_1(tenants)
        self.host_vars["qumulo_tenants"] = sorted(entries, key=lambda entry: entry["name"])

    def default_heir(self, tenants):
        """The tenant a cluster without tenant 1 is written to rename Default to, or None.

        On networking the host manages, the rename keeps the HOST network, which the play reaches REST on, in tenant 1
        through a rebuild, where with Default absent the replay's tenant 1 pass puts it in Default and the baseline's step
        3 moves it to its tenant. The rename needs one tenant that holds a network, every network held by a tenant, the
        factory keys read, no tenant named Default, and no / export of that tenant's that the capture leaves out: the old
        edge's export is drift under either form, but a rebuild that waives it would leave the renamed tenant 1 the
        factory / export; otherwise missing_tenant_1 writes Default absent, with the reason in its note.
        """
        if any(tenant["id"] == tenancy.FORMATION_TENANT_ID or tenant["name"] == tenancy.FORMATION_TENANT_NAME for tenant in tenants):
            return None
        if not self.memo.get("host_managed") or not all("networks" in self.held.get(tenant["id"], {}) for tenant in tenants):
            return None
        factory = self.host_vars.get("qumulo_factory_defaults", {})
        if self.flat_networks(backend=False) or any(key not in factory for key, _item in FACTORY_ITEMS):
            return None
        holders = [tenant for tenant in tenants if self.held[tenant["id"]]["networks"]]
        if len(holders) != 1:
            if len(holders) > 1:
                self.memo["no_heir_why"] = (
                    f"the host manages its networking and {len(holders)} tenants hold networks, so the replay's tenant 1 pass "
                    "writes them all into tenant 1, and a rebuild needs qumulo_management_network, which the capture does not "
                    "write, to name the network the play reaches the edge on"
                )
            return None
        heir = holders[0]
        if self.holds_factory_name(NFS_PLACEMENT, heir) and not self.writes_factory_name(NFS_PLACEMENT, heir):
            self.memo["no_heir_why"] = (
                f"the host manages its networking, but {heir['name']}'s / export, which the capture leaves out, is not written "
                f"as the rename of Default to {heir['name']}, whose rebuilt tenant 1 would keep the factory / export in its "
                "place, so a rebuild that waives the export's drift would end with the factory export; with Default absent it "
                "ends with no / export"
            )
            return None
        return heir

    def rename_default(self, heir):
        """Note the rename of Default to heir, and keep each factory item heir declares under its own name: the rebuilt
        edge's tenant 1 is heir, so absent would claim that item twice."""
        factory = self.host_vars["qumulo_factory_defaults"]
        for (key, _item), placement in zip(FACTORY_ITEMS, (SMB_PLACEMENT, NFS_PLACEMENT)):
            if self.writes_factory_name(placement, heir):
                factory[key] = "present"
        name = heir["name"]
        why = (
            f"{NO_TENANT_1}; the host manages its networking, so {name}, the one tenant that holds a network, is written with "
            "previous_name: Default, which keeps the HOST network, the one a play reaches REST on, in tenant 1 through a "
            "rebuild: a cluster formed from this inventory, as a rebuild forms one, renames the Default it forms with to "
            f"{name}"
        )
        dropped = [item for key, item in FACTORY_ITEMS if factory[key] == "absent"]
        kept = [item for key, item in FACTORY_ITEMS if factory[key] == "present"]
        if dropped:
            why += f" and drops the factory {' and '.join(dropped)}, which qumulo_factory_defaults declares absent"
        if kept:
            why += f"{',' if dropped else ''} and keeps the factory {' and '.join(kept)} as {name}'s own, which qumulo_factory_defaults declares present"
        self.note("tenant 1", why)

    def holds_factory_name(self, placement, tenant):
        """Whether tenant holds a share or export named as the factory one on the cluster, written or not."""
        held = self.shares() if placement is SMB_PLACEMENT else self.exports()
        return any(item["tenant_id"] == tenant["id"] and _factory_named(placement, item[placement.name_key]) for item in held)

    def writes_factory_name(self, placement, tenant):
        """Whether the capture gives tenant a share or export named as the factory one, under its entry or shared."""
        nested = self.held.get(tenant["id"], {}).get(placement.entry_key) or []
        shared = [
            item for item in self.host_vars.get(placement.variable) or [] if item.get("tenants") == "all" or tenant["name"] in (item.get("tenants") or [])
        ]
        return any(_factory_named(placement, item[placement.name_key]) for item in nested + shared)

    def missing_tenant_1(self, tenants):
        """The entry that has a replay delete the Default a newly formed cluster has, which this one lacks (issue #287), or
        none where no entry may delete it or a replay of what the capture wrote could not; noted either way."""
        factory = self.host_vars.get("qumulo_factory_defaults", {})
        claimed = [item for key, item in FACTORY_ITEMS if factory.get(key) == "absent"]
        unread = [(key, item) for key, item in FACTORY_ITEMS if key not in factory]
        rebuilds = "a cluster formed from this inventory, as a rebuild forms one,"
        if any(tenant["name"] == tenancy.FORMATION_TENANT_NAME for tenant in tenants):
            kept = f"{rebuilds} keeps the Default it forms with as that tenant"
            if claimed:
                kept += f", drops the factory {' and '.join(claimed)}, which qumulo_factory_defaults declares absent"
            if unread:
                kept += f", and keeps the factory {' and '.join(item for _key, item in unread)}, which the capture does not read"
            self.note("tenant 1", f"{NO_TENANT_1}, and another tenant is named Default, so no entry declares Default absent; {kept}")
            return []
        network_read = all("networks" in self.held.get(tenant["id"], {}) for tenant in tenants)
        unbound = self.flat_networks(backend=False) if network_read else []
        skipped = self.why_default_stays(network_read, unread)
        if skipped and not unbound:
            self.note(
                "tenant 1",
                f"{NO_TENANT_1}, and {skipped}, so no entry declares Default absent; {rebuilds} keeps that Default, which the rebuild "
                "preflight reports as drift",
            )
            return []
        why = f"{NO_TENANT_1}; it is written as Default with state: absent"
        if claimed:
            why += f" and the factory {' and '.join(claimed)} absent"
        why += f", so that {rebuilds} drops " + {0: "it", 1: "both", 2: "all three"}[len(claimed)]
        gathered = [key for key, _item in unread if key.split("_")[0] not in self.leave_out]
        if gathered:
            why += f"; the replay refuses it until qumulo_factory_defaults declares {' and '.join(gathered)} absent"
        if not network_read:
            why += "; the replay refuses it until the network area is captured or each network is listed under a tenant that stays"
        if unbound:
            verb = "is" if len(unbound) == 1 else "are"
            why += f"; the rebuild preflight refuses the inventory until {', '.join(unbound)} {verb} listed under a tenant that stays"
        if skipped:
            has = "it has" if len(unbound) == 1 else "they have"
            why += (
                f"; it is written although {skipped}, since without it the rebuilt cluster's step 2 would refuse "
                f"{', '.join(unbound)} beside that Default after the old cluster is gone; capture again once {has} a tenant"
            )
        if self.memo.get("no_heir_why"):
            why += f"; {self.memo['no_heir_why']}"
        self.note("tenant 1", why)
        return [{"name": tenancy.FORMATION_TENANT_NAME, "state": "absent"}]

    def why_default_stays(self, network_read, unread):
        """Why a replay of what the capture wrote could not delete Default, whatever the inventory adds, or None: an area
        Terraform owns, so the inventory cannot declare it, or a backend-bond network, which takes no tenant."""
        owned = [area for area in ("smb", "nfs") if area in self.leave_out and area in {key.split("_")[0] for key, _item in unread}]
        if owned:
            keys = " and ".join(f"qumulo_factory_defaults.{key}" for key, _item in unread if key.split("_")[0] in owned)
            items = " and ".join(item for key, item in unread if key.split("_")[0] in owned)
            return (
                f"leave_out names {' and '.join(owned)}, as Terraform owns {'it' if len(owned) == 1 else 'them'}, so {keys} cannot be "
                f"declared, and a replay would refuse deleting Default for the factory {items} it holds"
            )
        if not network_read and "network" in self.leave_out:
            return (
                "leave_out names network, as Terraform owns it, so the inventory lists no network and a replay would refuse "
                "deleting Default for the networks it holds"
            )
        backend = self.flat_networks(backend=True) if network_read else []
        if backend:
            return (
                f"{', '.join(backend)}, on the backend bond, {'stays' if len(backend) == 1 else 'stay'} in "
                "qumulo_network.frontend_networks, which a replay leaves in tenant 1, so it would refuse deleting Default"
            )
        return None

    def flat_networks(self, backend):
        """The networks written in qumulo_network.frontend_networks that are on the backend bond, or that are not, named."""
        on_bond = self.memo.get("backend_networks") or set()
        return [
            f"network {item['id']} ({item['name']})"
            for item in (self.host_vars.get("qumulo_network") or {}).get("frontend_networks") or []
            if (item["id"] in on_bond) == backend
        ]

    def _s3(self):
        self.host_vars["qumulo_s3_settings"] = self.s3_settings()
        buckets = sorted(self.buckets(), key=lambda bucket: bucket["name"])
        kept = [bucket for bucket in buckets if not self.in_spoke_root(f"the S3 bucket {bucket['name']}", bucket["path"])]
        self.host_vars["qumulo_s3_buckets"] = [_set(bucket) for bucket in kept]
        policies = []
        for bucket in kept:
            policy = read_policy(self.client, bucket["name"])
            if policy == NO_POLICY:
                continue
            try:
                policies.append({"bucket": bucket["name"], "policy": self.trustee_policy(policy)})
            except QumuloError as e:
                self.note(f"the policy of the S3 bucket {bucket['name']}", f"{e}; declare it by hand")
        self.host_vars["qumulo_s3_bucket_policies"] = policies
        keys = sorted(
            ({"user": render(key["owner"]), "access_key_id": key["access_key_id"]} for key in all_keys(self.client)), key=lambda key: tuple(key.values())
        )
        self.host_vars["qumulo_s3_access_keys"] = keys
        for key in keys:
            self.need(
                f"qumulo_s3_access_keys[{key['access_key_id']}]",
                f"the cluster returned the secret of {key['user']}'s S3 access key {key['access_key_id']} only when it made the key, and "
                "its consumers hold it; after a rebuild the baseline makes a new key and writes its secret to a file under the host's "
                "artifact directory, for its consumers (G-S3-KEY)",
            )

    def trustee_policy(self, policy):
        """policy with each principal in the trustee grammar, as the inventory declares it."""
        statements = []
        for statement in policy.get("Statement", []):
            principal = statement.get("Principal")
            if isinstance(principal, dict) and "Qumulo" in principal:
                names = [principal["Qumulo"]] if isinstance(principal["Qumulo"], str) else principal["Qumulo"]
                statement = {**statement, "Principal": {**principal, "Qumulo": [as_trustee(self.client, name) for name in names]}}
            statements.append(statement)
        return {**policy, "Statement": statements}

    def _ftp(self):
        settings = FTP_INFO_SUBSETS["settings"](self.client)["settings"]
        anonymous = settings.get("anonymous_user", "")
        if anonymous == "local:":
            del settings["anonymous_user"]
            self.note("the FTP anonymous user", "it names a local user that was deleted, which ftp_settings cannot declare")
        elif anonymous and not anonymous.startswith("local:"):
            del settings["anonymous_user"]
            self.note("the FTP anonymous user", f"it is {anonymous}, which is not a local user, the one kind ftp_settings declares")
        self.host_vars["qumulo_ftp_settings"] = settings

    def _quotas(self):
        quotas = sorted(self.quotas(), key=lambda quota: quota["path"])
        kept = [quota for quota in quotas if not self.in_spoke_root(f"the quota on {quota['path']}", quota["path"])]
        self.host_vars["qumulo_quotas"] = [{"path": quota["path"], "limit": quota["limit"]} for quota in kept]

    def _snapshots(self):
        policies = sorted(self.policies(), key=lambda policy: policy["policy_name"])
        kept = [policy for policy in policies if not self.in_spoke_root(f"the snapshot policy {policy['policy_name']}", policy["path"])]
        self.host_vars["qumulo_snapshot_policies"] = [self.policy_entry(policy) for policy in kept]
        try:
            keys = list_keys(self.client)
        except SubsetUnavailable as e:
            self.note("the security keys", str(e))
            keys = []
        for key in sorted(keys, key=lambda key: key["name"]):
            self.note(f"the security key {key['name']} (id {key['id']})", KEY_WHY)

    def policy_entry(self, policy):
        """A policy as qumulo_snapshot_policies declares it: its lock key by name, since a rebuilt or cloned cluster gives
        the key a new id. A key name is never digits only, so digits are the id policy_options falls back to."""
        entry = {key: value for key, value in policy.items() if key != "lock_key"}
        held = policy["lock_key"]
        if held is not None and held.isdigit():
            self.note(f"the lock key of the snapshot policy {policy['policy_name']}", POLICY_LOCK_KEY_WHY.format(key=held))
        elif held is not None:
            entry["lock_key"] = held
        return entry

    def _replication(self):
        declared = []
        for record in sorted(self.relationships("source"), key=lambda record: (record["source_path"], record["target_path"])):
            what = f"the replication of {record['source_path']} to {record['target_cluster_uuid']}:{record['target_path']}"
            target = self.inventory.get(record["target_cluster_uuid"])
            if record["state"] != ENDED:
                # The address the source reaches the target at is the target host's to declare, as portal hosts are.
                address = {"address": record["target_address"], "port": record["target_port"] or DEFAULT_PORT}
                self.record_peer(record["target_cluster_uuid"], REPLICATION_ADDRESS, address, None if record["state"] == ESTABLISHED else record["state"])
            if record["state"] == ENDED:
                self.note(what, "it has ENDED")
            elif target is None:
                self.note(what, "no inventory host has the target's qumulo_cluster_uuid; add the target to the inventory and capture again")
            elif record["blackout_windows"] and not record["blackout_window_timezone"]:
                # A window set without a timezone, outside the collection; the timezone reads back "" (QUIRK-019).
                self.note(what, "its blackout windows have no timezone, which the replication roles refuse; declare it with one")
            else:
                relationship = {"source_path": record["source_path"], "target": target, "target_path": record["target_path"]}
                relationship.update({key: record[key] for key in SOURCE_SETTINGS})
                if record["blackout_window_timezone"]:  # "" when none was ever set (QUIRK-019)
                    relationship["blackout_window_timezone"] = record["blackout_window_timezone"]
                declared.append(relationship)
        if declared:
            self.host_vars["qumulo_replication_relationships"] = declared
        for record in self.relationships("target"):
            if record["state"] == ENDED:
                continue
            self.from_peers.add(REPLICATION_ADDRESS)
            if record["source_cluster_uuid"] not in self.inventory:
                self.note(
                    f"the replication from {record['source_cluster_uuid']}:{record['source_path']} to {record['target_path']}",
                    "the source host declares it in qumulo_replication_relationships, and no inventory host has the source's UUID",
                )
