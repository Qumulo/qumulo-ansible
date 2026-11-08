# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The ftp area: the FTP server settings and each node's FTP server status.

The cluster has one FTP server, which answers on every network: no tenant has FTP settings of its own.
"""

from __future__ import annotations

from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.auth import USERS
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import get_offered
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import parse_trustee
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Field, Settings

SETTINGS = Route("/v0/ftp/settings", ("GET", "PATCH"))
STATUS = Route("/v0/ftp/status", ("GET",))

GREETING_BYTES = 1024  # the longest greeting the cluster takes, in bytes of UTF-8
ANONYMOUS_FORMS = 'anonymous_user must be a local user, written local:<name>, or "" for none; the cluster takes no other identity'


def anonymous_option(identity):
    """The anonymous user as the option reads it: "" for none, local:<name>, and local: for a user since deleted.

    Any other identity type, which the cluster refuses to store, reads as <type>:<value>, so that a read never fails on
    it and never takes a local group for a local user.
    """
    if identity is None:
        return ""
    prefix = "local" if identity["id_type"] == "LOCAL_USER" else identity["id_type"].lower()
    return f"{prefix}:{identity['id_value']}"


def anonymous_wire(option):
    if option == "":
        return None
    return {"id_type": "LOCAL_USER", "id_value": option.partition(":")[2]}


def same_local_user(a, b):
    """The cluster finds a local user by its name in any case, and refuses a second user whose name differs only in case."""
    if a.startswith("local:") and b.startswith("local:"):
        return a.casefold() == b.casefold()
    return a == b


def canonical_anonymous_user(option):
    """The option as local:<name>, or ""; anything else fails before any request."""
    if option == "":
        return ""
    try:
        identity = parse_trustee(option)
    except QumuloError:
        raise QumuloError(f"{ANONYMOUS_FORMS}, not '{option}'") from None
    if identity.get("domain") != "LOCAL":
        raise QumuloError(f"{ANONYMOUS_FORMS}, not '{option}'")
    return f"local:{identity['name']}"


class FtpSettings(Settings):
    RETURN_KEY = "ftp_settings"
    ROUTE = SETTINGS
    FIELDS: ClassVar[dict[str, Field]] = {
        "enabled": Field("enabled"),
        "check_remote_host": Field("check_remote_host"),
        "log_operations": Field("log_operations"),
        "chroot_users": Field("chroot_users"),
        "allow_unencrypted_connections": Field("allow_unencrypted_connections"),
        "expand_wildcards": Field("expand_wildcards"),
        "anonymous_user": Field("anonymous_user", read=anonymous_option, write=anonymous_wire, compare=same_local_user),
        "greeting": Field("greeting"),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        greeting = self.params.get("greeting")
        if greeting == "":
            raise QumuloError("greeting is empty, which the cluster refuses; it also cannot restore its factory greeting (G-FTP-GREETING)")
        if greeting is not None and len(greeting.encode()) > GREETING_BYTES:
            raise QumuloError(f"greeting is {len(greeting.encode())} bytes; the cluster takes at most {GREETING_BYTES}")
        declared = self.params.get("anonymous_user")
        self.anonymous_user = None if declared is None else canonical_anonymous_user(declared)

    def describe(self):
        return "the FTP settings"

    def desired(self, before):
        after = super().desired(before)
        if self.anonymous_user is None:
            return after
        if "anonymous_user" in before and same_local_user(before["anonymous_user"], self.anonymous_user):
            # The cluster's spelling of the same user, so that the result and the diff show the name it holds.
            after["anonymous_user"] = before["anonymous_user"]
        elif self.anonymous_user and self.module.check_mode:
            after["anonymous_user"] = self._predicted_user(self.anonymous_user)
        else:
            after["anonymous_user"] = self.anonymous_user
        return after

    def _predicted_user(self, option):
        """The user as the cluster would hold it, from its user list; a check run only, since a write reads it back.

        Outside check mode the cluster's refusal of a missing user names it, so no list is read.
        """
        wanted = option.partition(":")[2].casefold()
        name = next((user["name"] for user in self.client.get(USERS).data if user["name"].casefold() == wanted), None)
        if name is None:
            self.notes.append(f"local user '{option.partition(':')[2]}' does not exist yet; an earlier task must create it")
            return option
        return f"local:{name}"

    def update(self, current, after, keys):
        try:
            super().update(current, after, keys)
        except QumuloApiError as e:
            if not e.is_("auth_identity_not_found_error"):
                raise
            raise QumuloError(
                f"anonymous_user '{after['anonymous_user']}': the cluster has no such local user ({e.error_class}); "
                "create it with qumulo.cluster.local_user first",
                status=e.status,
                error_class=e.error_class,
            ) from None


def _settings(client):
    return {"settings": FtpSettings.options(get_offered(client, SETTINGS))}


def _status(client):
    return {"status": [{"node_id": entry["node_id"], "status": entry["status"]} for entry in get_offered(client, STATUS)["statuses"]]}


# gather_subset name -> function of the client returning a dict of that subset's facts; capture reads settings
FTP_INFO_SUBSETS = {"settings": _settings, "status": _status}
