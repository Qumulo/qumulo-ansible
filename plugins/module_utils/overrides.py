# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""A tenant's SMB or NFS override of the global settings: smb_settings and nfs_settings with tenant (spec 014, D5).

An override is its settings class with another route: it shares the fields, normalization, comparison and change.
Its absence is an error answer of its own class, which reads as inherit (QUIRK-112); a create is a PUT of every field
without If-Match (QUIRK-113), seeded from the global settings (QUIRK-115).
"""

from __future__ import annotations

from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import NfsSettings
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Settings
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import SmbSettings

SMB_OVERRIDE = Route("/v1/multitenancy/smb/settings/{tenant_id}", ("GET", "PUT", "PATCH", "DELETE"))
NFS_OVERRIDE = Route("/v1/multitenancy/nfs/settings/{tenant_id}", ("GET", "PUT", "PATCH", "DELETE"))


class TenantOverride(Settings):
    """One tenant's override of a Settings document; state inherit deletes it, so the tenant uses the global settings.

    A subclass lists TenantOverride before its settings class, so these methods take the settings class's place.
    """

    GLOBAL: ClassVar[Route]  # the global settings' route
    MISSING: ClassVar[str]  # the error class of an override that does not exist
    PROTOCOL: ClassVar[str]

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["tenant"]
        self.tenant_id = None
        self.global_document = None  # the global settings as the server returned them, read at most once

    def describe(self):
        return f"the {self.PROTOCOL} override of tenant '{self.name}'"

    def fetch(self):
        ids = tenancy.tenant_ids(self.client)
        if self.name in ids.by_name:
            self.tenant_id = ids.by_name[self.name]
        elif self.params["state"] == "inherit":
            return None  # no tenant, so no override: already inherit
        else:
            self.tenant_id = tenancy.resolve(self.client, self.name, self.module.check_mode)
        if isinstance(self.tenant_id, tenancy.Pending):
            self.notes.append(tenancy.pending_note(self.name))
            return None
        self.route_params = {"tenant_id": self.tenant_id}
        try:
            self._response = self.client.get(self.ROUTE, params=self.route_params)
        except QumuloApiError as e:
            if e.is_(self.MISSING):  # QUIRK-112: only this class means no override; a missing tenant answers another
                return None
            raise
        return Current(self.tenant_id, self._response.data, self._response.etag)

    def _global_document(self):
        if self.global_document is None:
            self.global_document = self.client.get(self.GLOBAL).data
        return self.global_document

    def _global(self):
        return self.options(self._global_document())

    def seed(self, before):
        # QUIRK-115: the NFS PUT takes factory defaults for the fields it leaves out, and the SMB PUT needs every field, so
        # a new override starts from the global settings the tenant used until now.
        return before or self._global()

    def create(self, after):
        # QUIRK-113: a creating PUT with If-Match is refused as if the override did not exist, so it carries none. The
        # body is the server's global document with the task's keys over it, so a field the module does not model keeps
        # its global value (QUIRK-115).
        self.client.put(self.ROUTE, params=self.route_params, body={**self._global_document(), **self.wire(after, after)})

    def delete(self, current):
        self.client.delete(self.ROUTE, params={"tenant_id": current.id}, if_match=current.etag)

    def run(self):
        if self.params["state"] == "inherit":
            current = self.fetch()
            before = self.normalize(current) if current else {}
            if current:
                self.check_inherit(before)
                if not self.module.check_mode:
                    self._delete(current)
            return self.result(current is not None, before, {}, [])
        return super().run()

    def check_inherit(self, before):
        """Refuse an inherit the cluster would refuse, before it is sent and in check mode too."""

    def result(self, changed, before, after, keys):
        result = super().result(changed, before, after, keys)
        if "diff" in result:
            result["diff"]["before_header"] = result["diff"]["after_header"] = f"tenant {self.name}"
        return result


class SmbOverride(TenantOverride, SmbSettings):
    ROUTE = SMB_OVERRIDE
    GLOBAL = SmbSettings.ROUTE
    MISSING = "api_smb_tenant_settings_do_not_exist_error"
    PROTOCOL = "SMB"


class NfsOverride(TenantOverride, NfsSettings):
    ROUTE = NFS_OVERRIDE
    GLOBAL = NfsSettings.ROUTE
    MISSING = "api_nfs_tenant_settings_do_not_exist_error"
    PROTOCOL = "NFS"

    def check_inherit(self, before):
        # QUIRK-114: an override's DELETE skips the NFSv4.1 nesting check the global settings then apply to this tenant.
        if self._global().get("v4_enabled") and not before.get("v4_enabled"):
            self.check_nesting("inherit, which turns NFSv4.1 on from the global settings")

    def check_nesting(self, change):
        if isinstance(self.tenant_id, tenancy.Pending):
            return  # a tenant an earlier task creates holds no export yet
        super().check_nesting(change, tenant_id=self.tenant_id, exports=f"every export of tenant '{self.name}'")
