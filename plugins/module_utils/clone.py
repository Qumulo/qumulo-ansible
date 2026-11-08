# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""clone_host_vars: the host_vars of a fresh cluster that takes another cluster's configuration.

Every variable a capture can hold has a rule in RULES: it is copied, filtered, replaced or dropped with a note, so that
no hand edit is left to make it right and a variable capture adds later fails here until it has a rule. In short:

- the UUID is the clone's own, never the source's or one the source took over in a rebuild;
- the name is the one the caller gives, which is also the clone's computer account in the domain, so its AD join is a
  fresh join;
- the supply lines ask for a Nexus join key of the clone's own and a license document for the clone's UUID;
- portals, replication relationships and the network document are dropped, since the peer's record of each, and the
  addresses, are the source's, and so is what lies in the source's spoke roots or receives its incoming replication;
- the tenants are kept without their networks, which hold the source's addresses as the network document does, and
  the shares and exports nested under them are filtered as the flat lists are (spec 014, FR-038).
- S3 access keys are dropped, since the source's S3 clients hold their secrets; the clone's own keys are declared anew.

The clone reads and writes no secret; the supply lines name each one the clone needs.
"""

from __future__ import annotations

import re

from ansible_collections.qumulo.cluster.plugins.module_utils.capture import KEYTAB_NOTE
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError

UUID = "qumulo_cluster_uuid"
NAME = "qumulo_cluster_name"
LOCAL_USERS = "qumulo_local_users"
AD = "qumulo_ad"
MONITORING = "qumulo_monitoring"
CAPACITY = "qumulo_capacity"
PORTAL = "qumulo_portal"
RELATIONSHIPS = "qumulo_replication_relationships"
DIRECTORIES = "qumulo_directories"
FILE_ACLS = "qumulo_file_acls"
SHARES = "qumulo_smb_shares"
EXPORTS = "qumulo_nfs_exports"
QUOTAS = "qumulo_quotas"
POLICIES = "qumulo_snapshot_policies"
AUTHORITATIVE_DNS = "qumulo_authoritative_dns"
AUDIT_DESTINATIONS = "qumulo_audit_destinations"
S3_SETTINGS = "qumulo_s3_settings"
BUCKETS = "qumulo_s3_buckets"
BUCKET_POLICIES = "qumulo_s3_bucket_policies"
ACCESS_KEYS = "qumulo_s3_access_keys"
SAML = "qumulo_saml"
KERBEROS = "qumulo_kerberos"
TENANTS = "qumulo_tenants"

COPY, REPLACE, FILTER, DROP = "copy", "replace", "filter", "drop"
# What the clone does with each variable a capture can hold. A variable without a rule fails the clone;
# tests/unit/plugins/modules/test_clone_host_vars.py fails on a variable capture writes that has none.
RULES = {
    UUID: REPLACE,
    NAME: REPLACE,
    "qumulo_dns": COPY,
    AUTHORITATIVE_DNS: COPY,
    "qumulo_ssl_ciphers": COPY,
    "qumulo_time": COPY,
    "qumulo_filesystem_settings": COPY,
    "qumulo_web_ui_settings": COPY,
    "qumulo_ftp_settings": COPY,
    MONITORING: COPY,
    AUDIT_DESTINATIONS: FILTER,
    CAPACITY: COPY,
    "qumulo_local_groups": COPY,
    LOCAL_USERS: COPY,
    AD: COPY,
    KERBEROS: COPY,
    SAML: DROP,
    "qumulo_user_defined_mappings": COPY,
    "qumulo_identity_attributes": COPY,
    "qumulo_roles": COPY,
    "qumulo_role_members": COPY,
    DIRECTORIES: FILTER,
    FILE_ACLS: FILTER,
    "qumulo_smb_settings": COPY,
    SHARES: FILTER,
    "qumulo_nfs_settings": COPY,
    EXPORTS: FILTER,
    "qumulo_factory_defaults": COPY,
    TENANTS: FILTER,
    QUOTAS: FILTER,
    POLICIES: FILTER,
    S3_SETTINGS: COPY,
    BUCKETS: FILTER,
    BUCKET_POLICIES: FILTER,
    "qumulo_network": DROP,
    PORTAL: DROP,
    RELATIONSHIPS: DROP,
    ACCESS_KEYS: DROP,
    "qumulo_previous_cluster_uuids": DROP,
    "qumulo_portal_hosts": DROP,
    "qumulo_replication_address": DROP,
    "qumulo_replication_port": DROP,
    "qumulo_ssl": DROP,
    "qumulo_managed_by": DROP,
    "qumulo_terraform_owned_areas": DROP,
}
# Why each dropped variable that one note covers is the source's alone. The portal and the relationships get a note each.
DROP_WHY = {
    "qumulo_network": "it holds the source's own addresses, its floating IPs among them; Terraform or you declare the clone's network",
    "qumulo_previous_cluster_uuids": "it names the clusters the source took over from in a rebuild, and the clone took over from none",
    "qumulo_portal_hosts": "it holds the addresses the source's portal peers reach the source at",
    "qumulo_replication_address": "it holds the address the source's replication sources reach the source at",
    "qumulo_replication_port": "it holds the port the source's replication sources reach the source at",
    "qumulo_ssl": "it holds the source's certificate, and the clone serves its own, for the names its clients use",
    "qumulo_managed_by": "it says what formed the source; the target's inventory says what formed the target",
    "qumulo_terraform_owned_areas": "it names the areas Terraform owns on the source; the target's inventory names the target's",
    SAML: (
        "its cluster_dns_name is the source's Web UI address, for which the IdP's application is set up; declare the "
        "clone's own qumulo_saml, with the IdP's application for the clone"
    ),
}
DROP_WHAT = {"qumulo_network": "the network document", SAML: "the SAML settings"}
# The keys of each copied variable that hold a secret, which a clone's file never holds. capture_info returns none of them.
SECRET_KEYS = {
    AD: ("join_user", "join_password"),
    MONITORING: ("nexus_join_key",),
    CAPACITY: ("license_document",),
    KERBEROS: ("keytab", "keytab_path"),
}
# The keys of each copied variable that belong to the source alone, which the clone leaves out: (keys, what, why). The
# note is given only when the source set one of them, so a source that never had a name and never answered gets none.
ONE_CLUSTER = {
    AUTHORITATIVE_DNS: (
        ("enabled", "fqdn"),
        "the authoritative DNS name and whether it answers",
        "the name is the source's, which the clone must not answer for; declare the clone's own fqdn, and enabled, in qumulo_authoritative_dns",
    ),
}
BUILT_IN_USERS = ("admin", "guest")  # every cluster has them from its formation
FACTORY_SHARE, FACTORY_EXPORT = "Files", "/"  # every formation creates them, in the tenant it creates
FORMATION_TENANT = "Default"  # that tenant's name until it is renamed
TENANT_1_NOTE = "tenant 1"  # what capture's note on a missing tenant 1 is about
JOIN_SECRETS = ("qumulo_ad.join_user", "qumulo_ad.join_password")
NEXUS_JOIN_KEY = "qumulo_monitoring.nexus_join_key"
LICENSE_DOCUMENT = "qumulo_capacity.license_document"
SSL_SECRETS = ("qumulo_ssl.certificate", "qumulo_ssl.private_key")
KEYTAB = "qumulo_kerberos.keytab"
# How the capture's notes on the source's portals, relationships and spoke-root data start; the clone drops them all.
PORTAL_NOTE = "the spoke portal "
REPLICATION_NOTES = ("the replication of ", "the replication from ")
SPOKE_ROOT_NOTE = "it lies in the spoke root "

CLOUDWATCH_WHY = "its log group and region are in the source's AWS account; declare the target's own, or leave it to Terraform"
PORTAL_WHY = (
    "the hub's record of the portal is the source's, and the clone must not claim it; declare the clone's own portal in "
    "qumulo_portal, with spoke roots of its own"
)
REPLICATION_WHY = (
    "the peer's record of the relationship is the source's, and the clone must not claim it; declare the clone's own "
    "relationships, with target directories of their own"
)
ABSENT_TENANT_WHY = (
    "the source has no tenant 1, so its capture declares Default absent, which deletes the Default a cluster forms with; the "
    "target's Default holds the target's own networks, which the clone declares under no tenant, so the target keeps it. "
    "Declare it state: absent once its networks are under a tenant that stays"
)
TENANT_NETWORKS_WHY = "they hold the source's own addresses, its floating IPs among them; declare the clone's networks under its tenants"
SPOKE_ROOT_WHY = "it lies in a spoke root of the source's portal, which the clone does not take; declare it once the clone has a portal of its own"
TARGET_WHY = "it receives the source's incoming replication, which the clone does not take; declare it with the clone's own relationship"
KEYTAB_WHY = (
    "the source's keytab holds the source's principals, for the source's names; export the clone's own from the KDC, for the principals of the clone's names"
)
KEYTAB_UNREADABLE_WHY = (
    "the source's capture could not read its keytab, so whether it holds one is unknown; if the clone serves NFS with "
    "Kerberos, export the clone's own keytab from the KDC, for the principals of the clone's names"
)
SSL_WHY = "the API returns neither the certificate nor its key (G-SSL-READBACK); give the clone's own, for the names its clients use"
NEXUS_WHY = (
    "a join key registers one cluster only, and the source's registration is the source's; give a new key for the clone, "
    "or qumulo_nexus_api_token to mint one (G-NEXUS-KEY)"
)
ACCESS_KEY_WHY = (
    "the key, its id and its secret are the source's, whose S3 clients hold the secret; declare the clone's own keys in "
    "qumulo_s3_access_keys, and the baseline makes each and writes its secret under the clone's artifact directory (G-S3-KEY)"
)
# The clone takes the roles, their members and the AD join, but not the SAML settings that kept their password away.
SSO_WHY = "the source requires AD users to sign in through the IdP; until qumulo_saml is declared for the clone, they sign in to it with a password"
INVENTORY_WHY = "the target's inventory, or -e, defines it, and a host_vars file would outrank it, so that value applies"


def _root(variable):
    """The inventory variable a supply line's name lies in: qumulo_ad for qumulo_ad.join_user."""
    return re.split(r"[.\[]", variable, maxsplit=1)[0]


def _user_password(name):
    return f"{LOCAL_USERS}[{name}].password"


def _inside(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def _without_secrets(variable, value):
    if variable == LOCAL_USERS:
        return [{key: item for key, item in user.items() if key != "password"} for user in value]
    if variable in SECRET_KEYS:
        return {key: item for key, item in value.items() if key not in SECRET_KEYS[variable]}
    return value


def _sorted(entries, key):
    unique = {(entry[key], entry["why"]): entry for entry in entries}
    return [unique[pair] for pair in sorted(unique)]


class Clone:
    """The clone's host_vars, supply lines and notes, from the source's capture. Sends no request."""

    def __init__(self, params):
        self.capture = params["capture"]
        self.uuid = params["cluster_uuid"].lower()
        self.name = params["cluster_name"]
        # Each variable the target's inventory defines, with its keys when it is a mapping, else None.
        self.inventory_defines = dict(params["inventory_defines"] or {})
        self.names_in_use = params["names_in_use"] or []
        self.target = (params["target"] or {}).get("host_vars") or {}
        # Capture notes a missing tenant 1 whatever it writes for it, and the clone's read of the target writes no entry.
        self.target_lacks_tenant_1 = any(entry.get("what") == TENANT_1_NOTE for entry in (params["target"] or {}).get("left_out") or [])
        self.allow_configured_target = params["allow_configured_target"]
        self.supplied = set(params["supplied"] or [])
        self.inventory_supplied = set()  # the secrets of a kept variable the inventory gives, which the first pass takes
        self.host_vars, self.supply, self.left_out = {}, [], []
        source_vars = self.capture.get("host_vars") or {}
        portal_roots = [root["spoke_root"] for root in (source_vars.get(PORTAL) or {}).get("roots", [])]
        self.spoke_roots = sorted({*portal_roots, *(self.capture.get("spoke_roots") or [])})
        self.replication_targets = sorted(self.capture.get("replication_targets") or [])
        # A bucket policy goes where its bucket goes.
        self.bucket_paths = {bucket["name"]: bucket["path"] for bucket in source_vars.get(BUCKETS) or []}

    def run(self):
        source_vars = self.capture.get("host_vars") or {}
        source_uuid = str(source_vars.get(UUID) or "").lower()
        if not source_uuid:
            raise QumuloError("the capture holds no qumulo_cluster_uuid; give the capture that qumulo.cluster.capture_info returns")
        unruled = sorted(set(source_vars) - set(RULES))
        if unruled:
            raise QumuloError(f"the capture holds {', '.join(unruled)}, which the clone has no rule for; it copies nothing it has no rule for")
        self.check_identity(source_uuid, source_vars.get(NAME))
        self.check_target_is_fresh()
        self.copy(source_vars)
        self.replace_supply()
        self.replace_notes()
        self.leave_out_what_the_inventory_defines()
        supply = _sorted(self.supply, "variable")
        unknown = sorted(self.supplied - {entry["variable"] for entry in supply})
        if unknown:
            raise QumuloError(f"no supply line of the clone's file names {', '.join(unknown)}; give only the secrets its supply lines name")
        return {
            "changed": False,
            "host_vars": dict(sorted(self.host_vars.items())),
            "supply": supply,
            "left_out": _sorted(self.left_out, "what"),
            "first_pass_needs": self.first_pass_needs(),
            "source_uuid": source_uuid,
        }

    def check_identity(self, source_uuid, source_name):
        """The clone never claims to be the source, or any other cluster the inventory names."""
        if self.uuid == source_uuid:
            raise QumuloError(f"the target's UUID, {self.uuid}, is the source's: the target is the source itself; give a fresh cluster as the target")
        if source_name is not None and self.name.casefold() == str(source_name).casefold():
            raise QumuloError(
                f"the name {self.name} is the source's, and a name is also the cluster's computer account in the domain; give the clone a name of its own"
            )
        if any(self.name.casefold() == str(name).casefold() for name in self.names_in_use):
            raise QumuloError(
                f"the name {self.name} is already another inventory cluster's, and a name is also the cluster's computer "
                "account in the domain; give the clone a name of its own"
            )

    def check_target_is_fresh(self):
        """A fresh cluster holds only what formation creates; anything else means the target is another cluster in use."""
        if self.allow_configured_target:
            return
        held = [f"the local user {user['name']}" for user in self.target.get(LOCAL_USERS) or [] if user["name"] not in BUILT_IN_USERS]
        if self.target.get(AD):
            held.append(f"a join to {self.target[AD]['domain']}")
        held += [f"the SMB share {share['share_name']}" for share in self.target.get(SHARES) or [] if share["share_name"] != FACTORY_SHARE]
        held += [f"the NFS export {export['export_path']}" for export in self.target.get(EXPORTS) or [] if export["export_path"] != FACTORY_EXPORT]
        held += self.held_by_tenants(self.target.get(TENANTS) or [], self.target_lacks_tenant_1)
        held += [f"the S3 bucket {bucket['name']}" for bucket in self.target.get(BUCKETS) or []]
        held += [f"the S3 access key {key['access_key_id']} of {key['user']}" for key in self.target.get(ACCESS_KEYS) or []]
        if held:
            raise QumuloError(
                f"the target holds {', '.join(held)}, so it is not a fresh cluster; give a fresh cluster as the target, or "
                "allow a configured one to clone onto it anyway"
            )

    @staticmethod
    def held_by_tenants(tenants, lacks_tenant_1):
        """What the target's tenants hold beyond formation's one tenant and its factory share and export.

        The formation tenant is the only one, or the one named Default or renamed from it. A target whose capture notes
        a missing tenant 1, or declares a tenant absent, as it does then, holds no formation tenant.
        """
        formation_gone = lacks_tenant_1 or any(tenant.get("state") == "absent" for tenant in tenants)
        tenants = [tenant for tenant in tenants if tenant.get("state") != "absent"]
        named = next((t for t in tenants if FORMATION_TENANT in (t["name"], t.get("previous_name"))), None)
        formed = None if formation_gone else (tenants[0] if len(tenants) == 1 else named)
        held = []
        for tenant in tenants:
            factory = tenant is formed
            if not factory:
                held.append(f"the tenant {tenant['name']}")
            of = f"of tenant {tenant['name']}"
            held += [
                f"the SMB share {share['share_name']} {of}"
                for share in tenant.get("smb_shares") or []
                if not (factory and share["share_name"] == FACTORY_SHARE)
            ]
            held += [
                f"the NFS export {export['export_path']} {of}"
                for export in tenant.get("nfs_exports") or []
                if not (factory and export["export_path"] == FACTORY_EXPORT)
            ]
        return held

    def copy(self, source_vars):
        for variable, value in source_vars.items():
            rule = RULES[variable]
            if variable == S3_SETTINGS:
                self.host_vars[variable] = self.without_a_dropped_base_path(value)
            elif rule == COPY:
                self.host_vars[variable] = self.without_one_cluster_keys(variable, _without_secrets(variable, value))
            elif rule == FILTER:
                self.host_vars[variable] = self.filtered(variable, value)
            elif variable == PORTAL:
                self.note(f"the spoke portal to hub {value['hub']}", PORTAL_WHY)
            elif variable == RELATIONSHIPS:
                for relationship in value:
                    what = f"the replication of {relationship['source_path']} to {relationship['target']}:{relationship['target_path']}"
                    self.note(what, REPLICATION_WHY)
            elif variable == ACCESS_KEYS:
                for key in value:
                    self.note(f"the S3 access key {key['access_key_id']} of {key['user']}", ACCESS_KEY_WHY)
            elif rule == DROP:
                why = DROP_WHY[variable]
                if variable == SAML and value.get("enabled") and value.get("require_sso"):
                    why += f"; {SSO_WHY}"
                self.note(DROP_WHAT.get(variable, variable), why)
        self.host_vars[UUID] = self.uuid
        self.host_vars[NAME] = self.name

    def without_one_cluster_keys(self, variable, value):
        if variable not in ONE_CLUSTER:
            return value
        keys, what, why = ONE_CLUSTER[variable]
        if any(value.get(key) for key in keys):
            self.note(what, why)
        return {key: item for key, item in value.items() if key not in keys}

    def filtered(self, variable, entries):
        """entries without those in the source's spoke roots, or receiving its incoming replication, each noted; for the
        audit destinations, without the CloudWatch ones; for the tenants, without their networks."""
        if variable == AUDIT_DESTINATIONS:
            return self.filtered_audit_destinations(entries)
        if variable == TENANTS:
            return [self.filtered_tenant(tenant) for tenant in entries if not self.absent_tenant(tenant)]
        return self.filtered_entries(variable, entries)

    def absent_tenant(self, tenant):
        """Whether tenant is declared absent, which the clone leaves out with a note."""
        if tenant.get("state") != "absent":
            return False
        self.note(f"tenant {tenant['name']}, declared absent", ABSENT_TENANT_WHY)
        return True

    def filtered_tenant(self, tenant):
        """A tenant without its networks, noted when it had any, and with its shares and exports filtered."""
        kept = {key: value for key, value in tenant.items() if key != "networks"}
        if tenant.get("networks"):
            self.note(f"the networks of tenant {tenant['name']}", TENANT_NETWORKS_WHY)
        for key, variable in (("smb_shares", SHARES), ("nfs_exports", EXPORTS)):
            if key in tenant:
                kept[key] = self.filtered_entries(variable, tenant[key], f" of tenant {tenant['name']}")
                if not kept[key]:
                    del kept[key]  # as capture leaves an empty list out of an entry
        return kept

    def filtered_entries(self, variable, entries, of=""):
        """entries without those in the source's spoke roots or receiving its incoming replication, each noted; of
        names the tenant a nested share or export is under."""
        kept = []
        for entry in entries:
            if variable == SHARES:
                path, what = entry["fs_path"], f"the SMB share {entry['share_name']}{of}"
            elif variable == EXPORTS:
                path, what = entry["fs_path"], f"the NFS export {entry['export_path']}{of}"
            elif variable == QUOTAS:
                path, what = entry["path"], f"the quota on {entry['path']}"
            elif variable == POLICIES:
                path, what = entry["path"], f"the snapshot policy {entry['policy_name']}"
            elif variable == BUCKETS:
                path, what = entry["path"], f"the S3 bucket {entry['name']}"
            elif variable == BUCKET_POLICIES:
                path, what = self.bucket_paths.get(entry["bucket"], ""), f"the policy of the S3 bucket {entry['bucket']}"
            elif variable == FILE_ACLS:
                path, what = entry["path"], f"the ACL of {entry['path']}"
            else:
                path, what = entry["path"], f"the directory {entry['path']}"
            why = self.dropped_why(path)
            if why:
                self.note(what, why)
            else:
                kept.append(entry)
        return kept

    def filtered_audit_destinations(self, destinations):
        kept = []
        for destination in destinations:
            if destination["type"] == "CLOUDWATCH":
                self.note(f"the CloudWatch audit destination {destination['name']}", CLOUDWATCH_WHY)
            else:
                kept.append(destination)
        return kept

    def without_a_dropped_base_path(self, settings):
        """The S3 settings, without a base path where the clone has no directory: the target keeps its own."""
        why = self.dropped_why(settings.get("base_path", ""))
        if not why:
            return settings
        self.note(f"the S3 base path {settings['base_path']}", why)
        return {key: value for key, value in settings.items() if key != "base_path"}

    def dropped_why(self, path):
        """Why the clone has no directory at path, or None when it has one."""
        if any(_inside(path, root) for root in self.spoke_roots):
            return SPOKE_ROOT_WHY
        if any(_inside(path, target) for target in self.replication_targets):
            return TARGET_WHY
        return None

    def replace_supply(self):
        """The capture's supply lines, with those whose reason was the source's own restated for the clone."""
        replaced = {*JOIN_SECRETS, NEXUS_JOIN_KEY, LICENSE_DOCUMENT, *SSL_SECRETS, KEYTAB}
        captured = self.capture.get("supply") or []
        self.supply = [entry for entry in captured if entry["variable"] not in replaced and _root(entry["variable"]) != ACCESS_KEYS]
        captured_names = {entry["variable"] for entry in captured}
        if AD in self.host_vars:
            why = (
                f"the clone joins {self.host_vars[AD]['domain']} as a new computer account, {self.name.upper()}$, and the baseline sends "
                "them with that join; the cluster keeps neither"
            )
            self.supply += [{"variable": variable, "why": why} for variable in JOIN_SECRETS]
        if (self.host_vars.get(MONITORING) or {}).get("nexus", {}).get("nexus_enabled"):
            self.supply.append({"variable": NEXUS_JOIN_KEY, "why": NEXUS_WHY})
        if (self.host_vars.get(CAPACITY) or {}).get("license") or LICENSE_DOCUMENT in captured_names:
            why = (
                f"a license is bound to one UUID, and the source's is not the clone's; Qumulo issues the clone's for {self.uuid} once "
                "it is registered with Nexus. The first baseline hands it off, and ansible-playbook "
                "qumulo.cluster.edge_license_complete -i <inventory> -l <host> applies it (G-LICENSE)"
            )
            self.supply.append({"variable": LICENSE_DOCUMENT, "why": why})
        if captured_names & set(SSL_SECRETS):
            self.supply += [{"variable": variable, "why": SSL_WHY} for variable in SSL_SECRETS]
        if KEYTAB in captured_names:
            self.supply.append({"variable": KEYTAB, "why": KEYTAB_WHY})

    def replace_notes(self):
        """The capture's notes, with those on the source's portals, relationships and spoke-root data restated, since the
        clone drops them all, and the one on the source's keytab, since the clone needs a keytab of its own."""
        for entry in self.capture.get("left_out") or []:
            if entry["what"].startswith(PORTAL_NOTE):
                self.note(entry["what"], PORTAL_WHY)
            elif entry["what"].startswith(REPLICATION_NOTES):
                self.note(entry["what"], REPLICATION_WHY)
            elif entry["why"].startswith(SPOKE_ROOT_NOTE):
                self.note(entry["what"], SPOKE_ROOT_WHY)
            elif entry["what"] == KEYTAB_NOTE:
                self.note(entry["what"], KEYTAB_UNREADABLE_WHY)
            else:
                self.left_out.append(entry)

    def leave_out_what_the_inventory_defines(self):
        """A host_vars file outranks the inventory's own variables, so it leaves out each one the inventory defines,
        unless the inventory's is a mapping of secrets alone: the source's other settings are then kept, and the supply
        lines name the secrets to add beside them."""
        dropped = set()
        for variable in sorted(set(self.inventory_defines) & set(self.host_vars)):
            keys = self.inventory_defines[variable]
            secrets = SECRET_KEYS.get(variable, ())
            if keys is not None and isinstance(self.host_vars[variable], dict) and set(keys) <= set(secrets):
                self.note(
                    variable,
                    f"the target's inventory gives only {', '.join(sorted(keys))} of it, so the source's other settings are kept "
                    f"here. The first pass takes {', '.join(sorted(keys))} from the inventory, but this file's {variable} "
                    "outranks the inventory's from now on, so move them into this file, as its supply lines say",
                )
                self.inventory_supplied |= {f"{variable}.{key}" for key in keys}
                continue
            del self.host_vars[variable]
            dropped.add(variable)
            self.note(variable, INVENTORY_WHY)
        self.supply = [entry for entry in self.supply if _root(entry["variable"]) not in dropped]

    def first_pass_needs(self):
        """The supply lines the first baseline cannot do without and was not given: a password for each local user
        the target lacks, since a user is created with one, and the join credentials while the target is not joined to
        the domain. The others guard what the first pass skips or hands off."""
        needs = []
        on_target = {*BUILT_IN_USERS, *(user["name"] for user in self.target.get(LOCAL_USERS) or [])}
        for user in self.host_vars.get(LOCAL_USERS) or []:
            if user["name"] not in on_target and user.get("state", "present") == "present":
                needs.append(_user_password(user["name"]))
        joined = (self.target.get(AD) or {}).get("domain", "").lower()
        if AD in self.host_vars and joined != self.host_vars[AD]["domain"].lower():
            needs += JOIN_SECRETS
        return sorted(set(needs) - self.supplied - self.inventory_supplied)

    def note(self, what, why):
        self.left_out.append({"what": what, "why": why})
