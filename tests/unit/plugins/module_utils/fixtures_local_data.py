# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Response bodies and identities shared by the protocol and local-data module tests, written from the public qumulo-api SDK."""

from __future__ import annotations

from urllib.parse import quote

LOCAL_SID = "S-1-5-21-1603110191-4223960225-1866107978"
ADMIN = {"domain": "LOCAL", "auth_id": "500", "uid": None, "gid": None, "sid": f"{LOCAL_SID}-500", "name": "admin"}
GUEST = {"domain": "LOCAL", "auth_id": "501", "uid": None, "gid": None, "sid": f"{LOCAL_SID}-501", "name": "guest"}
USERS = {"domain": "LOCAL", "auth_id": "513", "uid": None, "gid": None, "sid": f"{LOCAL_SID}-513", "name": "Users"}
EVERYONE = {"domain": "WORLD", "auth_id": "8589934592", "uid": None, "gid": None, "sid": "S-1-1-0", "name": "Everyone"}
UID_1000 = {"domain": "POSIX_USER", "auth_id": "12884902888", "uid": 1000, "gid": None, "sid": "S-1-5-88-1-1000", "name": None}
# Authenticated Users as 7.11.0 returns it, joined to a domain or not: in ACTIVE_DIRECTORY, its name without a domain.
AUTHENTICATED_USERS = {"domain": "ACTIVE_DIRECTORY", "auth_id": "21474836491", "uid": None, "gid": None, "sid": "S-1-5-11", "name": "Authenticated Users"}
# The internal domain's two identities, as 7.11.0 returns them in an ACL: a name, but no SID, uid or gid.
FILE_OWNER = {"domain": "API_INTERNAL_DOMAIN", "auth_id": "18446744065119617025", "uid": None, "gid": None, "sid": None, "name": "File Owner"}
FILE_GROUP_OWNER = {"domain": "API_INTERNAL_DOMAIN", "auth_id": "18446744065119617026", "uid": None, "gid": None, "sid": None, "name": "File Group Owner"}
IDENTITIES = [ADMIN, GUEST, USERS, EVERYONE, UID_1000, AUTHENTICATED_USERS, FILE_OWNER, FILE_GROUP_OWNER]
# QUIRK-100: a node that has not found a name in S-1-5 since it started answers the find of Authenticated Users' SID so.
UNSEEN_SID_DOMAIN = ({"sid": "S-1-5-11"}, 400, {"error_class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain S-1-5."})
# SIDs a node refuses to find (QUIRK-100) though an ACL holds them, as 7.11.0 reads them back: a deleted account of a
# domain the node has not found a name in since it started, and an account of a domain the cluster does not trust.
ORPHANED_SID = {"domain": "ACTIVE_DIRECTORY", "auth_id": "25769804881", "uid": None, "gid": None, "sid": "S-1-5-21-9-8-7-1105", "name": None}
FOREIGN_SID = {"domain": "ACTIVE_DIRECTORY", "auth_id": "30064771572", "uid": None, "gid": None, "sid": "S-1-5-21-77-88-99-500", "name": None}
UNSEEN_DOMAINS = [
    ({"sid": ident["sid"]}, 400, {"error_class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain."})
    for ident in (ORPHANED_SID, FOREIGN_SID)
]
# An identity the trustee grammar has no form for: a name in a domain it does not name, and no SID, uid or gid.
UNNAMEABLE = {"domain": "API_INTERNAL_DOMAIN", "auth_id": "18446744065119617031", "uid": None, "gid": None, "sid": None, "name": "Nobody"}

DIRECTORY = "FS_FILE_TYPE_DIRECTORY"
DETAILS = {"LOCAL": ("LOCAL_USER", "name"), "POSIX_USER": ("NFS_UID", "uid")}


def ref(path):
    """A path as one URL component, the way the client sends it."""
    return quote(path, safe="")


def attributes_path(path):
    return f"/v1/files/{ref(path)}/info/attributes"


def acl_path(path):
    return f"/v2/files/{ref(path)}/info/acl"


def entries_path(path):
    return f"/v1/files/{ref(path)}/entries/"


def attributes(path, *, file_id="100", mode="0755", owner=None, group=None, kind=DIRECTORY):
    """GET /v1/files/{ref}/info/attributes as the server answers it: a directory's path ends with a slash.

    owner and group default to admin and Users, which own the root of a newly formed cluster.
    """
    owner, group = owner or ADMIN, group or USERS
    owner_type, owner_key = DETAILS[owner["domain"]]
    return {
        "path": path if path == "/" or kind != DIRECTORY else path + "/",
        "name": path.rsplit("/", 1)[-1],
        "type": kind,
        "id": file_id,
        "file_number": file_id,
        "mode": mode,
        "owner": owner["auth_id"],
        "owner_details": {"id_type": owner_type, "id_value": str(owner[owner_key])},
        "group": group["auth_id"],
        "group_details": {"id_type": "LOCAL_GROUP", "id_value": group["name"]},
        "child_count": 0,
    }


def file_acl_entry(trustee, rights, *, kind="ALLOWED", flags=()):
    """One entry of GET /v2/files/{ref}/info/acl: the trustee is the whole identity, rights in the server's order."""
    return {"type": kind, "flags": list(flags), "trustee": dict(trustee), "rights": list(rights)}


def file_acl_response(aces, *, control=("PRESENT",), special=()):
    return {"control": list(control), "posix_special_permissions": list(special), "aces": list(aces)}


def error(error_class, description="test"):
    return {"error_class": error_class, "description": description}


def fake_with_identities(fake_api, **kwargs):
    fake = fake_api(**kwargs)
    fake.identities = list(IDENTITIES)
    return fake
