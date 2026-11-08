# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Rights algebra for SMB share access control entries and for file ACL entries.

A share entry takes the aggregate rights READ, WRITE and CHANGE_PERMISSIONS. The server stores ALL as the
three of them and NONE as no rights, drops duplicates, and lists rights in no fixed order, so rights compare
as sets. Entries keep their order, because the first entry that matches a user decides.
"""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError

SHARE_RIGHTS = ("READ", "WRITE", "CHANGE_PERMISSIONS")
ALIASES = {"ALL": SHARE_RIGHTS, "NONE": ()}


def share_rights(rights, where):
    """The sorted set of aggregate rights that rights stand for."""
    found = set()
    for right in rights:
        name = right.strip().upper()
        if name in ALIASES:
            found.update(ALIASES[name])
        elif name in SHARE_RIGHTS:
            found.add(name)
        else:
            raise QumuloError(f"{where}: '{right}' is not one of {', '.join((*SHARE_RIGHTS, *ALIASES))}")
    return sorted(found)


def check_not_write_only(kind, rights, where):
    """The server refuses an ALLOWED entry that grants WRITE without READ."""
    if kind == "ALLOWED" and "WRITE" in rights and "READ" not in rights:
        raise QumuloError(f"{where}: an ALLOWED entry with WRITE needs READ too; the cluster refuses write-only access to a share")


# A file ACL entry takes the fine-grained rights below, listed in the order the server lists them. The server stores
# ALL and ACCESS_RIGHTS_ALL as all of them, ADD_FILE as MODIFY and ADD_SUBDIR as EXTEND, and drops duplicates. Rights
# and flags compare as sets, and entries keep their order, because the first entry that matches a right decides.
FILE_RIGHTS = (
    "READ",
    "READ_EA",
    "READ_ATTR",
    "READ_ACL",
    "WRITE_EA",
    "WRITE_ATTR",
    "WRITE_ACL",
    "CHANGE_OWNER",
    "WRITE_GROUP",
    "DELETE",
    "EXECUTE",
    "MODIFY",
    "EXTEND",
    "DELETE_CHILD",
    "SYNCHRONIZE",
)
FILE_ALIASES = {"ALL": FILE_RIGHTS, "ACCESS_RIGHTS_ALL": FILE_RIGHTS, "ADD_FILE": ("MODIFY",), "ADD_SUBDIR": ("EXTEND",)}
ACE_TYPES = ("ALLOWED", "DENIED")
ACE_FLAGS = ("OBJECT_INHERIT", "CONTAINER_INHERIT", "NO_PROPAGATE_INHERIT", "INHERIT_ONLY", "INHERITED")
CONTROL_FLAGS = ("PRESENT", "DEFAULTED", "TRUSTED", "AUTO_INHERIT", "PROTECTED")
SPECIAL_PERMISSIONS = ("SET_UID", "SET_GID", "STICKY_BIT")


def file_rights(rights, where):
    """The rights that rights stand for, aliases expanded, in the server's order."""
    found = set()
    for right in rights:
        name = right.strip().upper()
        if name in FILE_ALIASES:
            found.update(FILE_ALIASES[name])
        elif name in FILE_RIGHTS:
            found.add(name)
        else:
            raise QumuloError(f"{where}: '{right}' is not one of {', '.join((*FILE_RIGHTS, *FILE_ALIASES))}")
    return in_order(found, FILE_RIGHTS)


def in_order(values, order):
    """values without duplicates, in the order the server lists its enum, then any value the enum lacks, as given."""
    known = [value for value in order if value in set(values)]
    return known + [value for value in dict.fromkeys(values) if value not in order]
