# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The filesystem area: the file system settings, and facts about the file system, each read through the area that owns it."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils.client import Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import CLUSTER_INFO_SUBSETS
from ansible_collections.qumulo.cluster.plugins.module_utils.files import attributes, check_file_terms, directory_options, find_files
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import IdentityResolver
from ansible_collections.qumulo.cluster.plugins.module_utils.quotas import quota_statuses
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Resource


def _capacity(client):
    return {"capacity": CLUSTER_INFO_SUBSETS["filesystem"](client)["filesystem"]}


def _root(client):
    return {"root": directory_options(IdentityResolver(client), "/", attributes(client, "/").data)}


def _quotas(client):
    return {"quotas": quota_statuses(client)}


# gather_subset name -> function of the client returning a dict of that subset's facts
FILESYSTEM_INFO_SUBSETS = {"capacity": _capacity, "root": _root, "quotas": _quotas}


def filesystem_lookups(params):
    """The lookups filesystem_info was asked for, as more subsets; a term outside its grammar raises QumuloError here."""
    terms = params["find_files"]
    if not terms:
        return {}
    check_file_terms(terms)
    return {"found_files": lambda client: {"found_files": find_files(client, terms)}}


# The file system settings live in three documents; each option is one wire key of one of them.
PERMISSIONS = Route("/v1/file-system/settings/permissions", ("GET", "PUT"))  # carries no ETag
ATIME = Route("/v1/file-system/settings/atime", ("GET", "PATCH"))
NOTIFY = Route("/v1/file-system/settings/notify", ("GET", "PUT"))
SETTINGS_DOCUMENTS = ((PERMISSIONS, "PUT"), (ATIME, "PATCH"), (NOTIFY, "PUT"))
SETTINGS_FIELDS = {
    "permissions_mode": (PERMISSIONS, "mode"),
    "atime_enabled": (ATIME, "enabled"),
    "atime_granularity": (ATIME, "granularity"),
    "notify_recursive_mode": (NOTIFY, "recursive_mode"),
}
PERMISSIONS_MODES = ("NATIVE", "CROSS_PROTOCOL", "CROSS_PROTOCOL_POSIX_PRIORITY")


def settings_options(documents):
    """{route path: document} in the option shape of filesystem_settings."""
    return {option: documents[route.path][wire] for option, (route, wire) in SETTINGS_FIELDS.items() if wire in documents[route.path]}


class FilesystemSettings(Resource):
    RETURN_KEY = "filesystem_settings"
    READ_ONLY = frozenset()

    def describe(self):
        return "the file system settings"

    def fetch(self):
        self._responses = {route.path: self.client.get(route) for route, _method in SETTINGS_DOCUMENTS}
        return Current(None, {path: response.data for path, response in self._responses.items()})

    def normalize(self, current):
        return settings_options(current.data)

    def desired(self, before):
        return {**before, **{option: self.params[option] for option in SETTINGS_FIELDS if self.params.get(option) is not None}}

    def update(self, current, after, keys):
        written = False
        for route, method in SETTINGS_DOCUMENTS:
            options = [option for option in keys if SETTINGS_FIELDS[option][0] is route]
            if options:
                written |= self._write_document(route, method, options, after)
        if not written:  # after a 412, the re-reads showed another writer had set every key
            self.wrote, self.reread = False, self.normalize(self.fetch())

    def _write_document(self, route, method, options, after):
        def build(data):
            wire = {SETTINGS_FIELDS[option][1]: after[option] for option in options if data.get(SETTINGS_FIELDS[option][1]) != after[option]}
            if not wire:
                return None
            return wire if method == "PATCH" else {**data, **wire}

        return self.client.read_modify_write(route, build, method=method, first=self._responses[route.path]) is not None
