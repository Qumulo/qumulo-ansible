# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Files that hold a secret the cluster shows once, such as an S3 key's or an access token's: readable only by their
owner, written whole and flushed to disk before their name appears, and never replacing another file."""

from __future__ import annotations

import errno
import json
import os
import re
import tempfile

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError

STAGING_PREFIX = ".secret-"


def secret_directory(directory, unmade):
    """Make directory, or the one that exists there, readable only by its owner, and check that a file can be written in
    it; QumuloError when it cannot be, ending with unmade, such as "no key was made". A symbolic link's target is the
    directory."""
    try:
        if os.path.lexists(directory) and not os.path.isdir(directory):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if os.stat(directory).st_mode & 0o077:
            os.chmod(directory, 0o700)
            if os.stat(directory).st_mode & 0o077:
                raise PermissionError(errno.EPERM, "its mode stays open to others after a chmod, as on some mounts")
        descriptor, probe = tempfile.mkstemp(dir=directory, prefix=STAGING_PREFIX, suffix=".probe")
        os.close(descriptor)
        os.unlink(probe)
        os.close(os.open(directory, os.O_RDONLY))  # write_secret_file syncs the directory through a descriptor like this one
    except OSError as e:
        raise QumuloError(f"secret_destination {directory} cannot hold a secret readable only by its owner ({e.strerror}); {unmade}") from None


# Where a hard link cannot be made, a rename moves the staged file into place instead. Both names are in one
# directory, so no EXDEV.
NO_HARD_LINKS = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP})


def _place(staged, path):
    """Give staged's file the name path, which must not exist."""
    try:
        os.link(staged, path)
    except OSError as e:
        if e.errno not in NO_HARD_LINKS:
            raise
        if os.path.lexists(path):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path) from None
        os.rename(staged, path)


def write_secret_file(directory, name, comment, fields):
    """Write fields, (key, value) pairs, as YAML under the comment line to the file name in directory, readable only by its
    owner, and return the file's path.

    The file is written whole and flushed to disk before its name appears, and never replaces a file of that name. An
    error after the name appears removes the file, since the caller then deletes what the secret opens.
    """
    path = os.path.join(directory, name)
    lines = [f"# {comment}", *(f"{key}: {json.dumps(value)}" for key, value in fields)]
    descriptor, staged = tempfile.mkstemp(dir=directory, prefix=STAGING_PREFIX, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as staging:
            staging.write("\n".join(lines) + "\n")
            staging.flush()
            os.fsync(staging.fileno())
        _place(staged, path)
    except BaseException:
        if os.path.lexists(staged):
            os.unlink(staged)
        raise
    try:
        if os.path.lexists(staged):  # a hard link leaves the staged name too
            os.unlink(staged)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        os.unlink(path)
        raise
    return path


def secret_file_name(prefix, user, ident):
    """The name of the file that holds the secret of ident, which belongs to user: <prefix>-<user>-<ident>.yml, with each run
    of characters in user other than letters, digits, ".", "_" and "-" replaced by one "_"."""
    return f"{prefix}-{re.sub(r'[^A-Za-z0-9._-]+', '_', user)}-{ident}.yml"
