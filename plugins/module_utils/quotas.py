# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The quotas area: hard capacity quotas on directories, keyed by path."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, Response, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.files import attributes, canonical_path, require_directory
from ansible_collections.qumulo.cluster.plugins.module_utils.normalize import parse_size
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Resource

QUOTAS = Route("/v1/files/quotas/", ("POST",))
QUOTA = Route("/v1/files/quotas/{id}", ("GET", "PUT", "DELETE"))
# Paged; the last page is empty and its paging.next is "" (QUIRK-004), which client.paginate stops on.
QUOTA_STATUSES = Route("/v1/files/quotas/status/", ("GET",))


def quota_statuses(client):
    """Every quota with its directory's path and usage, in the option shape of directory_quota plus usage."""
    return [
        {"path": canonical_path(entry["path"]), "limit": int(entry["limit"]), "capacity_usage": int(entry["capacity_usage"])}
        for entry in client.paginate(QUOTA_STATUSES, "quotas")
    ]


class DirectoryQuota(Resource):
    RETURN_KEY = "quota"
    READ_ONLY = frozenset({"path"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.path = canonical_path(self.params["path"])
        self.limit = None if self.params.get("limit") is None else parse_size(self.params["limit"])
        self.directory_id = None

    def describe(self):
        return f"the quota of {self.path}"

    def fetch(self):
        found = attributes(self.client, self.path)
        if found is None:
            if self.params["state"] == "present" and not self.module.check_mode:
                raise QumuloError(f"{self.path} does not exist; create it first, for example with qumulo.cluster.directory")
            if self.params["state"] == "present":
                self.notes.append(f"{self.path} does not exist yet; the quota is set once an earlier task creates it")
            return None
        require_directory(self.path, found.data)
        self.directory_id = found.data["id"]
        try:
            response = self.client.get(QUOTA, params={"id": self.directory_id})
        except QumuloApiError as e:
            if e.is_("api_quotas_quota_limit_not_found_error"):
                return None
            # The cluster refuses even the read inside a spoke's root, where no quota can exist.
            if e.is_("fs_operation_unsupported_error") and self.params["state"] == "absent":
                return None
            self._raise_refused(e)
            raise
        return Current(self.directory_id, response.data, response.etag)

    def normalize(self, current):
        return {"path": self.path, "limit": int(current.data["limit"])}

    def desired(self, before):
        return {**before, "path": self.path, "limit": self.limit}

    def create(self, after):
        self._send_limit(lambda: self.client.post(QUOTAS, body=self._body(after)))

    def update(self, current, after, keys):
        def build(data):
            return None if int(data["limit"]) == after["limit"] else self._body(after)

        first = Response(200, current.data, current.etag)
        self._send_limit(lambda: self.client.read_modify_write(QUOTA, build, method="PUT", params={"id": current.id}, first=first))

    def _body(self, after):
        return {"id": self.directory_id, "limit": str(after["limit"])}

    def _send_limit(self, send):
        try:
            send()
        except QumuloApiError as e:
            self._raise_refused(e)
            raise

    def _raise_refused(self, e):
        if e.is_("fs_operation_unsupported_error"):
            raise QumuloError(f"the cluster refuses a quota on {self.path}, as it does on a directory inside a portal spoke's root: {e}") from None

    def delete(self, current):
        self.client.delete(QUOTA, params={"id": current.id})
