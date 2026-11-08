# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The files area: paths, directories and their attributes.

The REST API names a file by its absolute path or by its id. Ids change when a cluster is rebuilt, so
modules take paths, and resolve them to ids only for the length of one run.
"""

from __future__ import annotations

import re
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.acl import ACE_FLAGS, CONTROL_FLAGS, FILE_RIGHTS, SPECIAL_PERMISSIONS, file_rights, in_order
from ansible_collections.qumulo.cluster.plugins.module_utils.client import READ_ONLY_WHY, QumuloApiError, QumuloError, Response, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import IdentityResolver, parse_trustee, render, well_known_trustee
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import spoke_roots
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Resource

ATTRIBUTES = Route(
    "/v1/files/{ref}/info/attributes",
    ("GET", "PATCH"),
    etag="drift",
    why="the ETag is the file's revision, which a change to any of its children also bumps",
)
ENTRIES = Route("/v1/files/{ref}/entries/", ("GET", "POST"))
AGGREGATES = Route("/v1/files/{ref}/aggregates/", ("GET",))
FILE = Route("/v1/files/{ref}", ("DELETE",))
ACL = Route(
    "/v2/files/{ref}/info/acl",
    ("GET", "PUT"),
    etag="drift",
    why="the ETag is the file's revision, which a change to any of its children also bumps",
)
EXPLAIN_SET_MODE = Route("/v1/files/{ref}/info/acl/explain-set-mode", ("POST",), replay=frozenset({"POST"}), why=READ_ONLY_WHY)

DIRECTORY = "FS_FILE_TYPE_DIRECTORY"
MODE = re.compile(r"^[0-7]{3,4}$")
# owner_details and group_details name an identity in a form the trustee grammar also has.
DETAIL_PREFIXES = {"LOCAL_USER": "local", "LOCAL_GROUP": "local", "NFS_UID": "uid", "NFS_GID": "gid", "SMB_SID": "sid"}


def canonical_path(path, option="path"):
    """An absolute path without a trailing slash ("/" stays "/"); a relative path or an empty, "." or ".." component fails."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise QumuloError(f"{option} '{path}' must be an absolute path, such as /data/projects")
    parts = path.rstrip("/").split("/")[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise QumuloError(f"{option} '{path}' has an empty, '.' or '..' component")
    return "/" + "/".join(parts)


def inside(path, root):
    """Whether path is root or lies under it; both canonical."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def canonical_mode(mode, option="mode"):
    """Four octal digits, the way the server returns a mode, from a mode given as a string."""
    if not isinstance(mode, str):
        raise QumuloError(f"{option} {mode!r} is a number; quote the mode, such as '0755', because YAML reads an unquoted 0755 as the decimal 493")
    text = mode.strip()
    if not MODE.match(text):
        raise QumuloError(f"{option} '{mode}' is not an octal mode such as '0755'")
    return text.zfill(4)


def parent(path):
    return path.rsplit("/", 1)[0] or "/"


def attributes(client, path, misses=frozenset({"fs_no_such_entry_error"})):
    """The Response of GET attributes for path, or None when nothing is there: a 404 whose class is one of misses."""
    try:
        return client.get(ATTRIBUTES, params={"ref": path})
    except QumuloApiError as e:
        if e.status == 404 and e.error_class in misses:
            return None
        raise


# The 404 classes of the attributes read that find_files takes as naming nothing, as T2000p saw them on 7.10.3 and
# 7.11.0: a missing path, a path through a file, a deleted or never-issued id, and an id of a file system this cluster
# lacks.
FIND_FILES_MISSES = frozenset({"fs_no_such_entry_error", "fs_no_such_path_error", "fs_no_such_inode_error", "fs_no_such_filesystem_error"})
# An id the server cannot place answers the 404 of a missing route (QUIRK-101). The route is in every release the
# collection supports, so for an id that answer is a miss; for a path it still fails.
ID_MISSES = FIND_FILES_MISSES | {"http_not_found_error"}
FILE_TERM_FORMS = "an absolute path, such as /data/projects, or a file id of decimal digits, such as 1003"
NO_FILE = {"found": False, "path": None, "id": None, "type": None}


def _file_ref(term):
    """The ref the attributes read takes for a find_files term: its canonical path, or the id as given."""
    if isinstance(term, str) and term.startswith("/"):
        return canonical_path(term, option="find_files term")
    if isinstance(term, str) and re.fullmatch(r"[0-9]+", term):
        return term
    raise QumuloError(f"find_files term '{term}' is neither an absolute path nor a file id; accepted forms: {FILE_TERM_FORMS}")


def check_file_terms(terms):
    """Raise a QumuloError naming the first term that is neither an absolute path nor a decimal file id."""
    for term in terms:
        _file_ref(term)


def find_files(client, terms):
    """{term: entry} for absolute paths and decimal file ids, each read through the attributes route."""
    refs = {term: _file_ref(term) for term in terms}
    found = {}
    for term, ref in refs.items():
        try:
            found[term] = _file_entry(attributes(client, ref, misses=FIND_FILES_MISSES if ref.startswith("/") else ID_MISSES))
        except QumuloError as e:  # the client's errors, unreachable included, and a path the cluster answers that is not absolute
            raise QumuloError(f"find_files term '{term}': {e}", term=term, **e.details) from None
    return found


def _file_entry(response):
    if response is None:
        return dict(NO_FILE)
    data = response.data
    return {"found": True, "path": canonical_path(data["path"], option="the cluster's path"), "id": data["id"], "type": data["type"]}


def require_directory(path, data):
    if data.get("type") != DIRECTORY:
        raise QumuloError(f"{path} exists and is not a directory ({data.get('type')})")


def file_id(client, path):
    """The id of the directory at path, as the server spells it; QumuloError when there is none."""
    found = attributes(client, path)
    if found is None:
        raise QumuloError(f"{path} does not exist; create it first, for example with qumulo.cluster.directory")
    require_directory(path, found.data)
    return found.data["id"]


def objects_under(client, directory):
    """Each file, link or other object at any depth under directory, read from entries, which never trail a write or a delete.

    It walks each subdirectory as it meets it and reads one page at a time, so a caller that stops at the first object
    usually reads one page.
    """
    for entry in client.iterate(ENTRIES, "files", params={"ref": directory}):
        if entry["type"] == DIRECTORY:
            yield from objects_under(client, canonical_path(entry["path"]))
        else:
            yield entry


def holds_objects(client, directory):
    """Whether any file, link or other object lies under directory; a directory holding only directories holds none."""
    return next(objects_under(client, directory), None) is not None


def make_directories(client, path):
    """Create the missing directory path and any missing parents, as mkdir -p does; returns the leaf's attributes."""
    missing = [path]
    while (above := parent(missing[-1])) != "/":
        found = attributes(client, above)
        if found is not None:
            require_directory(above, found.data)
            break
        missing.append(above)
    for each in reversed(missing):
        created = _create_directory(client, each)
    return created


def _create_directory(client, path):
    try:
        return client.post(ENTRIES, params={"ref": parent(path)}, body={"name": path.rsplit("/", 1)[1], "action": "CREATE_DIRECTORY"}).data
    except QumuloApiError as e:
        if not e.is_("fs_entry_exists_error"):
            raise
    # Another client made it first; that is only a success if it made a directory.
    found = attributes(client, path)
    if found is None:
        raise QumuloError(f"{path} was reported to exist, but cannot be read")
    require_directory(path, found.data)
    return found.data


def details_trustee(details):
    """The trustee string for an owner_details or group_details value, or None for a kind the grammar cannot name directly."""
    prefix = DETAIL_PREFIXES.get((details or {}).get("id_type"))
    if prefix == "sid" and well_known_trustee(details["id_value"]):
        return well_known_trustee(details["id_value"])
    return f"{prefix}:{details['id_value']}" if prefix else None


def trustee_of(identities, data, key, managed=False):
    """The owner or group of a file's attributes as a trustee string, which identities resolves to the auth_id the server gave.

    managed, for the directory module, names an identity render has no form for as the server gave it; otherwise, as for
    capture, render must name it.
    """
    trustee = details_trustee(data.get(f"{key}_details"))
    if trustee is not None:
        return identities.known({"auth_id": data[key]}, trustee)
    found = identities.resolve({"auth_id": data[key]})
    return identities.known(found) if managed else render(found)


def directory_options(identities, path, data, managed=False):
    """A directory's attributes in the option shape of the directory module; managed as trustee_of says."""
    return {"path": path, "mode": data["mode"], **{key: trustee_of(identities, data, key, managed) for key in ("owner", "group")}}


class Directory(Resource):
    RETURN_KEY = "directory"
    READ_ONLY = frozenset({"path"})
    TRUSTEES = ("owner", "group")
    MANAGED = ("mode", *TRUSTEES)

    def __init__(self, module, client):
        super().__init__(module, client)
        self.path = canonical_path(self.params["path"])
        if self.params["state"] == "absent" and self.path == "/":
            raise QumuloError("the root directory cannot be removed")
        self.mode = None if self.params.get("mode") is None else canonical_mode(self.params["mode"])
        for key in self.TRUSTEES:
            if self.params.get(key) is not None:
                parse_trustee(self.params[key])
        self.identities = IdentityResolver(client, check_mode=module.check_mode)

    def describe(self):
        return f"directory {self.path}"

    def fetch(self):
        found = attributes(self.client, self.path)
        if found is None:
            return None
        require_directory(self.path, found.data)
        return Current(found.data["id"], found.data, found.etag)

    def normalize(self, current):
        return directory_options(self.identities, self.path, current.data, managed=True)

    def desired(self, before):
        wanted = {"path": self.path, "mode": self.mode, **{key: self.params.get(key) for key in self.TRUSTEES}}
        return {**before, **{key: value for key, value in wanted.items() if value is not None}}

    def same(self, key, a, b):
        if key in self.TRUSTEES and a is not None and b is not None:
            return a == b or self.identities.same(a, b)
        return super().same(key, a, b)

    def create(self, after):
        make_directories(self.client, self.path)
        self._set(after)

    def update(self, current, after, keys):
        self._set(after, first=Response(200, current.data, current.etag))

    def _set(self, after, first=None):
        managed = [key for key in self.MANAGED if key in after]
        if not managed:
            return

        def build(data):
            before = self.normalize(Current(None, data))
            changed = [key for key in managed if not self.same(key, before.get(key), after[key])]
            return {key: after[key] if key == "mode" else self.identities.resolve(after[key])["auth_id"] for key in changed} or None

        self.client.read_modify_write(ATTRIBUTES, build, method="PATCH", params={"ref": self.path}, first=first)

    def delete(self, current):
        try:
            self.client.delete(FILE, params={"ref": self.path})
        except QumuloApiError as e:
            if e.is_("fs_directory_not_empty_error"):
                raise QumuloError(f"{self.path} is not empty; directory removes only an empty directory and never deletes data") from None
            raise


def acl_options(path, data, identities=None):
    """GET /v2/files/{ref}/info/acl in the option shape of file_acl; the server lists rights and flags in a fixed order.

    With identities, each trustee resolves to the identity the server gave with the entry, and one render has no form for is
    named as the server gave it; without, render must name every trustee.
    """
    trustee = identities.known if identities else render
    return {
        "path": path,
        "control": in_order(data["control"], CONTROL_FLAGS),
        "posix_special_permissions": in_order(data["posix_special_permissions"], SPECIAL_PERMISSIONS),
        "aces": [
            {
                "type": ace["type"],
                "trustee": trustee(ace["trustee"]),
                "rights": in_order(ace["rights"], FILE_RIGHTS),
                "flags": in_order(ace["flags"], ACE_FLAGS),
            }
            for ace in data["aces"]
        ],
    }


def _ace(entry, where):
    parse_trustee(entry["trustee"])
    return {
        "type": entry["type"],
        "trustee": entry["trustee"].strip(),
        "rights": file_rights(entry["rights"], where),
        "flags": in_order(entry.get("flags") or [], ACE_FLAGS),
    }


def acl_beyond_mode(client, path, mode):
    """The ACL of path in the option shape of file_acl, or None when setting its mode would leave that same ACL.

    The cluster previews the ACL a mode change would leave; when it is the ACL the path has, the mode declares it whole.
    """
    acl = acl_options(path, client.get(ACL, params={"ref": path}).data)
    from_mode = client.post(EXPLAIN_SET_MODE, params={"ref": path}, body={"mode": mode}).data["result_acl"]
    return None if acl_options(path, from_mode) == acl else acl


class FileAcl(Resource):
    RETURN_KEY = "acl"
    READ_ONLY = frozenset({"path"})
    FLAG_SETS: ClassVar[dict[str, tuple[str, ...]]] = {"control": CONTROL_FLAGS, "posix_special_permissions": SPECIAL_PERMISSIONS}

    def __init__(self, module, client):
        super().__init__(module, client)
        self.path = canonical_path(self.params["path"])
        self.wanted = {"aces": [_ace(entry, f"aces[{index}]") for index, entry in enumerate(self.params["aces"], 1)]}
        for key, order in self.FLAG_SETS.items():
            if self.params.get(key) is not None:
                self.wanted[key] = in_order(self.params[key], order)
        self.identities = IdentityResolver(client, check_mode=module.check_mode)

    def describe(self):
        return f"the ACL of {self.path}"

    def fetch(self):
        if attributes(self.client, self.path) is None:
            if not self.module.check_mode:
                raise QumuloError(f"{self.path} does not exist; create it first, for example with qumulo.cluster.directory")
            self.notes.append(f"{self.path} does not exist yet; its ACL is set once an earlier task creates it")
            return None
        response = self.client.get(ACL, params={"ref": self.path})
        return Current(None, response.data, response.etag)

    def normalize(self, current):
        return acl_options(self.path, current.data, self.identities)

    def desired(self, before):
        after = {"control": [], "posix_special_permissions": [], **before, "path": self.path, **self.wanted}
        if "aces" in before and self.same("aces", before["aces"], after["aces"]):
            after["aces"] = before["aces"]  # the same entries; report them as the cluster names them
        return after

    def same(self, key, a, b):
        if key == "aces" and a is not None and b is not None:
            return len(a) == len(b) and all(
                x["type"] == y["type"] and x["rights"] == y["rights"] and x["flags"] == y["flags"] and self.identities.same(x["trustee"], y["trustee"])
                for x, y in zip(a, b)
            )
        return super().same(key, a, b)

    def changes(self, before, after, creating):
        keys = super().changes(before, after, creating)
        if keys and not creating:
            self._refuse_spoke_root()
        return keys

    def _refuse_spoke_root(self):
        for root, hub in spoke_roots(self.client).items():
            if not inside(self.path, root):
                continue
            if self.params["allow_data_loss"]:
                self.notes.append(f"{self.path} lies in the spoke root {root}; allow_data_loss lets this task replace the ACL the hub {hub} holds")
            else:
                raise QumuloError(
                    f"{self.path} lies in the spoke root {root} of the portal to hub {hub}; the spoke passes the write through to the "
                    "hub, so it would replace the hub's ACL, which the hub's own inventory may declare; declare the ACL on the hub, or "
                    "set allow_data_loss=true to replace it from here"
                )

    def update(self, current, after, keys):
        def build(data):
            seen = self.normalize(Current(None, data))
            if not Resource.changes(self, seen, after, creating=False):
                self.reread = seen
                return None
            return {
                "control": after["control"],
                "posix_special_permissions": after["posix_special_permissions"],
                "aces": [
                    {
                        "type": ace["type"],
                        "flags": ace["flags"],
                        "trustee": {"auth_id": self.identities.resolve(ace["trustee"])["auth_id"]},
                        "rights": ace["rights"],
                    }
                    for ace in after["aces"]
                ],
            }

        self.wrote = (
            self.client.read_modify_write(ACL, build, method="PUT", params={"ref": self.path}, first=Response(200, current.data, current.etag)) is not None
        )
