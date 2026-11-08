# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The auth area: local users and groups, roles and their members, user-defined mappings, identity attributes and the
authentication cache.

Users and groups are found by name: their ids and SIDs change when a cluster is rebuilt.
"""

from __future__ import annotations

from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils import kerberos
from ansible_collections.qumulo.cluster.plugins.module_utils.access_tokens import access_tokens_subset
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, Route, term_warning
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import connecting_user, require_continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.dirsvc import JOINED, find_ad_objects, not_joined, parse_ad_term, read_ad
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import (
    IdentityResolver,
    Pending,
    expand_trustees,
    find_trustees,
    parse_terms,
    parse_trustee,
    render,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Action, Current, Resource
from ansible_collections.qumulo.cluster.plugins.module_utils.saml import read_saml

USERS = Route("/v1/users/", ("GET", "POST"))
USER = Route("/v1/users/{id}", ("GET", "PUT", "DELETE"))
GROUPS = Route("/v1/groups/", ("GET", "POST"))
GROUP = Route("/v1/groups/{id}", ("GET", "PUT", "DELETE"))
GROUP_MEMBERS = Route("/v1/groups/{id}/members/", ("GET", "POST"))
GROUP_MEMBER = Route("/v1/groups/{id}/members/{member_id}", ("DELETE",))
BUILT_IN_GROUPS = frozenset({"Users", "Guests"})
BUILT_IN_USERS = frozenset({"admin", "guest"})
ROLE_MEMBERS = Route("/v1/auth/roles/{role}/members", ("GET", "POST"))
ROLE_MEMBER = Route("/v1/auth/roles/{role}/members/{auth_id}", ("DELETE",))
SUPPORT_ROLE = "Qumulo-Support"
ROLES = Route("/v1/auth/roles/", ("GET", "POST"))
ROLE = Route("/v1/auth/roles/{name}", ("GET", "PATCH", "DELETE"))
PRIVILEGES = Route("/v1/auth/privileges/", ("GET",))
# The static roles of Qumulo Core 7.10.3, which the cluster refuses to change or delete; the list of roles does not mark them.
BUILT_IN_ROLES = frozenset({"administrators", "observers", "data-administrators", "qumulo-support"})
LDAP_SETTINGS = Route("/v2/ldap/settings", ("GET",))
DEFAULT_PRIMARY_GROUP = "Users"
USER_DEFINED_MAPPINGS = Route("/v1/auth/user-defined-mappings/", ("GET", "PUT"))
IDENTITY_ATTRIBUTES = Route("/v1/auth/identities/{auth_id}/attributes", ("GET", "PUT", "DELETE"))
CLEAR_CACHE = Route("/v1/auth/clear-cache", ("POST",), replay=frozenset({"POST"}), why="clearing the cache again does no more than clearing it once")
WILDCARD = "*"  # DOMAIN\* maps every user of the domain to the LDAP user of the same name


def _number(value):
    """uid and gid arrive as decimal strings; "" means none."""
    return None if value in (None, "") else int(value)


def _wire_number(value):
    return "" if value is None else str(value)


def find_by_name(entries, name):
    return next((entry for entry in entries if entry["name"] == name), None)


def normalize_user(user, group_names):
    """group_names maps a group id to its name."""
    return {
        "name": user["name"],
        "primary_group": group_names.get(user["primary_group"], user["primary_group"]),
        "uid": _number(user.get("uid")),
        "home_directory": user.get("home_directory") or None,
    }


def normalize_group(group, members):
    return {"name": group["name"], "gid": _number(group.get("gid")), "members": sorted(member["name"] for member in members)}


class LocalGroup(Resource):
    RETURN_KEY = "local_group"
    UNORDERED: ClassVar[frozenset[str]] = frozenset({"members"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["name"]
        if self.params["state"] == "absent" and self.name in BUILT_IN_GROUPS:
            raise QumuloError(f"'{self.name}' is a built-in group, which the cluster never deletes")
        self._users = None
        self._primary = []  # names of members whose primary group this is; the server owns those edges

    def describe(self):
        return f"local group '{self.name}'"

    def fetch(self):
        group = find_by_name(self.client.get(GROUPS).data, self.name)
        if group is None:
            return None
        members = self.client.get(GROUP_MEMBERS, params={"id": group["id"]}).data
        self._primary = [member["name"] for member in members if member.get("primary_group") == group["id"]]
        return Current(group["id"], {"group": group, "members": members})

    def normalize(self, current):
        return normalize_group(current.data["group"], current.data["members"])

    def desired(self, before):
        gid = self.params.get("gid")
        after = {"name": self.name, "gid": before.get("gid") if gid is None else gid, "members": list(before.get("members", []))}
        wanted = self.params.get("members")
        if wanted is not None:
            keep = set(self._primary) if self.params["purge"] else set(after["members"])
            after["members"] = sorted(set(wanted) | keep)
            self._check_users(set(after["members"]) - set(before.get("members", [])))
        return after

    def create(self, after):
        group = self.client.post(GROUPS, body={"name": self.name, "gid": _wire_number(after["gid"])}).data
        self._add(group["id"], after["members"])

    def update(self, current, after, keys):
        if "gid" in keys:
            seen = {}

            def build(data):
                if _number(data.get("gid")) == after["gid"]:
                    seen["group"] = data
                    return None
                return {"id": current.id, "name": self.name, "gid": _wire_number(after["gid"])}

            # After a 412, the re-read may show another writer already set the gid; then nothing is written.
            if self.client.read_modify_write(GROUP, build, method="PUT", params={"id": current.id}) is None and keys == ["gid"]:
                self.wrote = False
                self.reread = normalize_group(seen["group"], current.data["members"])
        if "members" in keys:
            before = {member["name"] for member in current.data["members"]}
            self._add(current.id, sorted(set(after["members"]) - before))
            self._remove(current.id, sorted(before - set(after["members"])))

    def delete(self, current):
        self.client.delete(GROUP, params={"id": current.id})

    def _users_by_name(self):
        if self._users is None:
            self._users = {user["name"]: user for user in self.client.get(USERS).data}
        return self._users

    def _check_users(self, names):
        if not names:
            return
        missing = sorted(set(names) - set(self._users_by_name()))
        for name in missing:
            if not self.module.check_mode:
                raise QumuloError(f"{self.describe()}: local user '{name}' does not exist; create it with qumulo.cluster.local_user first")
            self.notes.append(f"local user '{name}' does not exist yet; an earlier task is expected to create it")

    def _add(self, group_id, names):
        for name in names:
            try:
                self.client.post(GROUP_MEMBERS, params={"id": group_id}, body={"member_id": self._users_by_name()[name]["id"]})
            except QumuloApiError as e:
                if not e.is_("auth_user_already_in_group_error"):  # added by another writer since the read
                    raise

    def _remove(self, group_id, names):
        users = self._users_by_name()
        for name in names:
            self.client.delete(GROUP_MEMBER, params={"id": group_id, "member_id": users[name]["id"]})


class LocalUser(Resource):
    RETURN_KEY = "local_user"
    WRITE_ONLY: ClassVar[frozenset[str]] = frozenset({"password"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["name"]
        if self.params["state"] == "absent" and self.name in BUILT_IN_USERS:
            raise QumuloError(f"'{self.name}' is a built-in user, which the cluster never deletes")
        self._groups = None
        self.connection_changed = False  # the task would change the password of the user it connects as
        self.connection_password_written = False  # and it did, so later tasks must connect with the new one

    def describe(self):
        return f"local user '{self.name}'"

    def fetch(self):
        user = find_by_name(self.client.get(USERS).data, self.name)
        if self._groups is None:
            self._groups = self.client.get(GROUPS).data
        return None if user is None else Current(user["id"], user)

    def normalize(self, current):
        return normalize_user(current.data, {group["id"]: group["name"] for group in self._groups})

    def desired(self, before):
        creating = not before
        after = {
            "name": self.name,
            "primary_group": self._primary_group(before),
            "uid": self._option("uid", before),
            "home_directory": self._option("home_directory", before) or None,
            "password": self.params.get("password"),
        }
        if creating and after["password"] is None:
            raise QumuloError(f"password is needed to create local user '{self.name}'")
        if not creating and after["password"] is not None and self.params["update_password"] == "always":
            self._check_password_change()
        return after

    def _option(self, name, before):
        value = self.params.get(name)
        return before.get(name) if value is None else value

    def _primary_group(self, before):
        name = self.params.get("primary_group") or before.get("primary_group") or DEFAULT_PRIMARY_GROUP
        if find_by_name(self._groups, name) is not None:
            return name
        if not self.module.check_mode:
            raise QumuloError(f"{self.describe()}: local group '{name}' does not exist; create it with qumulo.cluster.local_group first")
        self.notes.append(f"local group '{name}' does not exist yet; an earlier task is expected to create it")
        return Pending(name)

    def _check_password_change(self):
        if self.name == "admin" and not self.params["allow_admin_password_change"]:
            raise QumuloError(
                "changing the password of admin also changes the console and SSH password of every node (G-ADMIN-OS); "
                "set allow_admin_password_change: true to do it"
            )
        if self.name == connecting_user(self.client):
            require_continuity(
                self.params,
                f"local user '{self.name}' is the user this task connects as, and its password would change",
                "connect later tasks with the new password",
            )
            self.connection_changed = True

    def _body(self, after):
        return {
            "name": self.name,
            "primary_group": find_by_name(self._groups, after["primary_group"])["id"],
            "uid": _wire_number(after["uid"]),
            "home_directory": after["home_directory"],
        }

    def create(self, after):
        self.client.post(USERS, body={**self._body(after), "password": after["password"]})

    def update(self, current, after, keys):
        password = after["password"] if "password" in keys else None
        body = {"id": current.id, **self._body(after), "password": password}
        self.client.read_modify_write(USER, lambda data: body, method="PUT", params={"id": current.id})
        if password is not None and self.connection_changed:
            self.connection_password_written = True
            if not self.client.params.get("api_token"):
                self.client.params["api_password"] = password  # a later login in this run uses the new password

    def delete(self, current):
        self.client.delete(USER, params={"id": current.id})

    def result(self, changed, before, after, keys):
        result = super().result(changed, before, after, keys)
        if self.connection_password_written:  # never in check mode, which writes nothing
            result["connection_password_changed"] = True
        return result


class RoleMissing(QumuloError):
    """The role a members route was read for does not exist."""


def role_members(client, role):
    """{auth_id: identity} of every member of role, across pages; QumuloError naming a role that does not exist."""
    members, url, seen = {}, ROLE_MEMBERS.url({"role": role}, {"limit": 1000}), set()
    while url:
        if url in seen:
            raise QumuloError(f"pagination loop at {url}")
        seen.add(url)
        try:
            page = client.get(url).data
        except QumuloApiError as e:
            # A missing role answers http_not_found_error, as a missing route does (QUIRK-030); the list tells them apart.
            if e.status == 404 and find_role(client, role) is None:
                raise RoleMissing(f"role '{role}' does not exist; create it with qumulo.cluster.role first") from None
            raise
        members.update(page.get("members") or {})
        url = (page.get("paging") or {}).get("next")
    return members


def _protected(role, identity):
    """The server's fixed assignments: admin and the system user in Administrators; purge never removes them."""
    return role == "Administrators" and identity.get("domain") == "LOCAL" and identity.get("name") in ("admin", "system")


class RoleMember:
    RETURN_KEY = "role_member"

    def __init__(self, module, client):
        self.module, self.client, self.params = module, client, module.params
        self.role = self.params["role"]
        if self.params["purge"] and self.role == SUPPORT_ROLE:
            raise QumuloError(f"purge is refused for {SUPPORT_ROLE}, whose members include the assignment Qumulo's support access relies on")
        for spec in self.params["members"]:
            parse_trustee(spec)  # a bare name fails here, before any request
        self.identities = IdentityResolver(client, check_mode=module.check_mode)
        self.notes = []

    def run(self):
        removing = self.params["state"] == "absent" or (self.params["purge"] and not self.params["members"])
        try:
            current = role_members(self.client, self.role)
        except RoleMissing:
            if removing:
                # A role that does not exist holds none of the members a removal asks away, as on the run after its
                # delete, and its members need not exist either.
                self.notes.append(f"role '{self.role}' does not exist, so it holds none of these members")
                return self._result(False, [], [])
            if not self.module.check_mode:
                raise
            self.notes.append(f"role '{self.role}' does not exist yet; an earlier task is expected to create it")
            current = {}
        wanted = self._removed() if self.params["state"] == "absent" else {spec: self.identities.resolve(spec) for spec in self.params["members"]}
        resolved = {ident["auth_id"]: ident for ident in wanted.values() if not isinstance(ident, Pending)}
        if self.params["state"] == "absent":
            add, remove = [], [auth_id for auth_id in resolved if auth_id in current]
        else:
            add = [ident for ident in wanted.values() if isinstance(ident, Pending) or ident["auth_id"] not in current]
            remove = []
            if self.params["purge"]:
                remove = [auth_id for auth_id, ident in current.items() if auth_id not in resolved and not _protected(self.role, ident)]
        self._check_continuity([current[auth_id] for auth_id in remove])
        before = self.members(current.values())
        after = self.members([*(ident for auth_id, ident in current.items() if auth_id not in remove), *add])
        changed = bool(add or remove)
        if changed and not self.module.check_mode:
            after = self._apply(add, remove)
        return self._result(changed, before, after)

    def _removed(self):
        """The members a removal names, by spec; one that does not exist is no member, as after its account is deleted."""
        wanted = {}
        for spec in self.params["members"]:
            ident = self.identities.resolve_existing(spec)
            if ident is None:
                self.notes.append(f"trustee '{spec}' does not exist, so it is not a member of role '{self.role}'")
            else:
                wanted[spec] = ident
        return wanted

    def _result(self, changed, before, after):
        result = {"changed": changed, self.RETURN_KEY: {"role": self.role, "members": after}}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": {"role": self.role, "members": before}, "after": {"role": self.role, "members": after}}
        return result

    def _check_continuity(self, removed):
        me = None
        for ident in removed:
            if ident.get("domain") != "LOCAL":
                continue
            me = me or connecting_user(self.client)
            if ident.get("name") == me:
                require_continuity(
                    self.params,
                    f"the task would remove {me}, the user this task connects as, from {self.role}",
                    "connect later tasks as a user that keeps the privileges they need",
                )

    def _apply(self, add, remove):
        for ident in add:
            self.client.post(ROLE_MEMBERS, params={"role": self.role}, body={"auth_id": ident["auth_id"]})
        for auth_id in remove:
            self.client.delete(ROLE_MEMBER, params={"role": self.role, "auth_id": auth_id})
        current = role_members(self.client, self.role)
        missing = [ident for ident in add if ident["auth_id"] not in current]
        left = [auth_id for auth_id in remove if auth_id in current]
        if missing or left:
            raise QumuloError(f"role '{self.role}' did not converge: {len(missing)} not assigned, {len(left)} not removed", changed=True)
        return self.members(current.values())

    @staticmethod
    def members(identities):
        return sorted(str(ident) if isinstance(ident, Pending) else render(ident) for ident in identities)


def _check_mappings(mappings):
    """Refuse, before any request, a list the cluster would refuse."""
    seen = set()
    for mapping in mappings:
        name = mapping["down_level_logon_name"]
        domain, backslash, user = name.partition("\\")
        if not (backslash and domain and user):
            raise QumuloError(f"mappings: '{name}' is not an AD name written DOMAIN\\name")
        if name.casefold() in seen:
            raise QumuloError(f"mappings: '{name}' appears more than once; each AD name maps to one LDAP user")
        seen.add(name.casefold())
        if user == WILDCARD:
            if len(mappings) > 1:
                raise QumuloError(f"mappings: '{name}' maps every user of {domain}, so it must be the only mapping")
            if mapping["ldap_name"] != WILDCARD:
                raise QumuloError(f"mappings: '{name}' maps every user of {domain} to the LDAP user of the same name, so it needs ldap_name '*'")


def normalize_mappings(mappings):
    return [{"down_level_logon_name": m["down_level_logon_name"], "ldap_name": m["ldap_name"]} for m in mappings]


class UserDefinedMappings(Resource):
    RETURN_KEY = "user_defined_mappings"
    READ_ONLY: ClassVar[frozenset[str]] = frozenset()
    UNORDERED: ClassVar[frozenset[str]] = frozenset({"mappings"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.wanted = normalize_mappings(self.params["mappings"])
        _check_mappings(self.wanted)

    def describe(self):
        return "the user-defined mappings"

    def fetch(self):
        self._response = self.client.get(USER_DEFINED_MAPPINGS)
        return Current(None, self._response.data, self._response.etag)

    def normalize(self, current):
        return {"mappings": normalize_mappings(current.data)}

    def desired(self, before):
        if self.same("mappings", before["mappings"], self.wanted):
            return before  # in the cluster's order, which is the one it reports
        if self.wanted:
            self._check_ad()
        return {"mappings": self.wanted}

    def _check_ad(self):
        # Outside a domain the cluster answers the PUT with 500 ldap_dc_not_found_error (QUIRK-031).
        ad = read_ad(self.client)
        if ad["status"] != JOINED:
            why = "the cluster is not joined to Active Directory, which user-defined mappings need"
            if not self.module.check_mode:
                raise QumuloError(f"{why}; join it with qumulo.cluster.ad_domain first")
            self.notes.append(f"{why}; an earlier task is expected to join it")
        elif ad["use_ad_posix_attributes"]:
            raise QumuloError("the cluster refuses user-defined mappings while it reads POSIX ids from AD; set use_ad_posix_attributes: false with ad_domain")

    def update(self, current, after, keys):
        def build(data):
            seen = self.normalize(Current(None, data))
            if self.same("mappings", seen["mappings"], after["mappings"]):
                self.reread = seen
                return None
            return after["mappings"]

        # After a 412, the re-read may show another writer already set the list; then nothing is written.
        self.wrote = self.client.read_modify_write(USER_DEFINED_MAPPINGS, build, method="PUT", first=self._response) is not None


class IdentityAttributes(Resource):
    RETURN_KEY = "identity_attributes"
    READ_ONLY: ClassVar[frozenset[str]] = frozenset({"trustee"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.trustee = self.params["trustee"]
        if parse_trustee(self.trustee).get("domain") == "LOCAL":
            raise QumuloError(self._local_refusal())
        self.identities = IdentityResolver(client, check_mode=module.check_mode)
        self.identity = None

    def _local_refusal(self):
        return (
            f"trustee '{self.trustee}' is a local identity, whose home directory is qumulo.cluster.local_user's home_directory; "
            "identity_attributes manages Active Directory, LDAP and POSIX identities"
        )

    def describe(self):
        return f"the attributes of '{self.trustee}'"

    def fetch(self):
        if self.params["state"] == "absent":
            self.identity = self.identities.resolve_existing(self.trustee)
            if self.identity is None:
                self.notes.append(f"trustee '{self.trustee}' does not exist, so it has no attributes to remove")
                return None
        else:
            self.identity = self.identities.resolve(self.trustee)
        if isinstance(self.identity, Pending):
            self.notes.append(f"trustee '{self.trustee}' does not exist yet; an earlier task is expected to create it or join its domain")
            return None
        if self.identity.get("domain") == "LOCAL":
            raise QumuloError(self._local_refusal())
        found = self.client.get_or_none(IDENTITY_ATTRIBUTES, params={"auth_id": self.identity["auth_id"]})
        return None if found is None else Current(self.identity["auth_id"], found.data)

    def _name(self):
        return self.trustee if isinstance(self.identity, Pending) else render(self.identity)

    def normalize(self, current):
        return {"trustee": self._name(), "home_directory": current.data.get("home_directory")}

    def desired(self, before):
        return {"trustee": self._name(), "home_directory": self.params["home_directory"]}

    def create(self, after):
        self.client.put(IDENTITY_ATTRIBUTES, params={"auth_id": self.identity["auth_id"]}, body=after)

    def update(self, current, after, keys):
        self.create({key: value for key, value in after.items() if key not in self.READ_ONLY})

    def delete(self, current):
        self.client.delete(IDENTITY_ATTRIBUTES, params={"auth_id": current.id})


def find_role(client, name):
    """(the name the cluster keeps, the role) for name in any case, or None.

    Found in the list, since a GET of a missing role answers http_not_found_error, as a missing route does (QUIRK-030).
    """
    return next(((found, role) for found, role in client.get(ROLES).data.items() if found.casefold() == name.casefold()), None)


def normalize_role(name, role):
    return {"name": name, "description": role.get("description", ""), "privileges": sorted(role.get("privileges") or [])}


class Role(Resource):
    RETURN_KEY = "role"
    READ_ONLY: ClassVar[frozenset[str]] = frozenset({"name"})
    UNORDERED: ClassVar[frozenset[str]] = frozenset({"privileges"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["name"]
        self.built_in = self.name.casefold() in BUILT_IN_ROLES
        if self.built_in and self.params["state"] == "absent":
            raise QumuloError(f"'{self.name}' is a built-in role, which the cluster never deletes")

    def describe(self):
        return f"role '{self.name}'"

    def fetch(self):
        found = find_role(self.client, self.name)
        if found is None:
            return None
        if self.params["state"] == "absent":
            self._check_no_members(found[0])
        return Current(found[0], found[1])

    def _check_no_members(self, name):
        held = role_members(self.client, name)
        if not held:
            return
        members = f"{len(held)} member{'' if len(held) == 1 else 's'}"
        if self.module.check_mode:
            self.notes.append(f"role '{name}' still has {members}; an earlier task is expected to remove them")
            return
        raise QumuloError(
            f"role '{name}' has {members}, and the cluster refuses to delete a role that still has members; remove them with qumulo.cluster.role_member first"
        )

    def normalize(self, current):
        return normalize_role(current.id, current.data)

    def desired(self, before):
        after = {
            "name": before.get("name", self.name),
            "description": self._option("description", before, ""),
            "privileges": sorted(self._option("privileges", before, [])),
        }
        keys = self.changes(before, after, creating=not before)
        if self.built_in and keys:
            raise QumuloError(f"'{before.get('name', self.name)}' is a built-in role, which the cluster never changes; it would change {', '.join(keys)}")
        if "privileges" in keys and after["privileges"]:
            self._check_privileges(after["privileges"])
        return after

    def _option(self, name, before, default):
        value = self.params.get(name)
        if value is not None:
            return value
        return before.get(name, default)

    def _check_privileges(self, wanted):
        unknown = sorted(set(wanted) - set(self.client.get(PRIVILEGES).data))
        if unknown:
            raise QumuloError(
                f"{self.describe()}: this cluster knows no privilege {', '.join(unknown)}; GET /v1/auth/privileges/ lists the ones its version has"
            )

    def create(self, after):
        self.client.post(ROLES, body={"name": self.name, "description": after["description"], "privileges": after["privileges"]})

    def update(self, current, after, keys):
        def build(data):
            seen = normalize_role(current.id, data)
            changed = self.changes(seen, after, creating=False)
            if not changed:
                self.reread = seen
                return None
            return {key: after[key] for key in changed}

        # After a 412, the re-read may show another writer already made the change; then nothing is written.
        self.wrote = self.client.read_modify_write(ROLE, build, method="PATCH", params={"name": current.id}) is not None

    def delete(self, current):
        etag = self.client.get(ROLE, params={"name": current.id}).etag
        self.client.delete(ROLE, params={"name": current.id}, if_match=etag)


class AuthCacheClear(Action):
    """auth_cache_clear: every run clears the cache, so there is always something to do (G-ACTIONS)."""

    def plan(self):
        return {"cache": "authentication"}

    def perform(self, plan):
        self.client.post(CLEAR_CACHE)


def _local_users(client):
    users = client.get(USERS).data
    names = {group["id"]: group["name"] for group in client.get(GROUPS).data}
    return {"local_users": sorted((normalize_user(user, names) for user in users), key=lambda user: user["name"])}


def _local_groups(client):
    groups = client.get(GROUPS).data
    facts = [normalize_group(group, client.get(GROUP_MEMBERS, params={"id": group["id"]}).data) for group in groups]
    return {"local_groups": sorted(facts, key=lambda group: group["name"])}


def _role_members(client):
    roles = sorted(client.get(ROLES).data)
    return {"role_members": [{"role": role, "members": RoleMember.members(role_members(client, role).values())} for role in roles]}


def _ad(client):
    return {"ad": read_ad(client)}


def _ldap(client):
    return {"ldap": {key: value for key, value in client.get(LDAP_SETTINGS).data.items() if "password" not in key}}


def _kerberos(client):
    return {"kerberos": client.get(kerberos.SETTINGS).data}


def _roles(client):
    roles = [normalize_role(name, role) for name, role in client.get(ROLES).data.items() if name.casefold() not in BUILT_IN_ROLES]
    return {"roles": sorted(roles, key=lambda role: role["name"])}


def _user_defined_mappings(client):
    return {"user_defined_mappings": {"mappings": normalize_mappings(client.get(USER_DEFINED_MAPPINGS).data)}}


def _saml(client):
    return {"saml": read_saml(client)}


# gather_subset name -> function of the client returning a dict of that subset's facts, in gathering order
IDENTITY_INFO_SUBSETS = {
    "local_users": _local_users,
    "local_groups": _local_groups,
    "role_members": _role_members,
    "ad": _ad,
    "ldap": _ldap,
    "kerberos": _kerberos,
    "roles": _roles,
    "user_defined_mappings": _user_defined_mappings,
    "saml": _saml,
    "access_tokens": access_tokens_subset,
}


def identity_lookups(params):
    """The lookups identity_info was asked for, as more subsets; a term outside its grammar raises QumuloError here."""
    finds, expands, ad_terms = params["find_trustees"], params["expand_trustees"], params["find_ad_objects"]
    parse_terms("find_trustees", finds)
    parse_terms("expand_trustees", expands)
    for term in ad_terms:
        parse_ad_term(term)
    lookups = {}
    if finds:
        lookups["found_trustees"] = lambda client: {"found_trustees": _warned(client, "find_trustees", find_trustees(client, finds))}
    if expands:
        lookups["expanded_trustees"] = lambda client: {"expanded_trustees": _warned(client, "expand_trustees", expand_trustees(client, expands))}
    if ad_terms:
        lookups["found_ad_objects"] = lambda client: {"found_ad_objects": _found_ad_objects(client, ad_terms)}
    return lookups


def _warned(client, lookup, result):
    found, errors = result
    warning = term_warning(lookup, errors)
    if warning:
        client.warnings.append(warning)
    return found


def _found_ad_objects(client, terms):
    try:
        return _warned(client, "find_ad_objects", find_ad_objects(client, terms))
    except QumuloApiError as e:
        if not_joined(e):
            raise SubsetUnavailable(e.description) from None
        raise
