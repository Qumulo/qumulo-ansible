# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The commit record (FR-034): its file format, FR-034's table that decides a record found, and the record's writes."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_path import the_limit
from ansible_collections.qumulo.cluster.plugins.module_utils.version import ClusterVersion

# Commit records (FR-034)


RECORD_FORMAT = 1
RECORD_FIELDS = ("format", "state", "cluster_uuid", "build", "target_version", "declared_build", "time", "error_info")
PENDING, UNFINISHED = "pending", "unfinished"
STATE_PREFIX = "UPGRADE_STATE_"
UNDER_WAY = frozenset({"COMMITTING", "COMMITTED", "REBOOTING"})


class RecordUnreadable(Exception):
    """A commit record this collection cannot read; the message is the reason F31 gives."""


@dataclass(frozen=True)
class RecordDecision:
    action: str  # retire, drop or refuse
    code: str | None  # F19, F30 or F31 for a refusal
    message: str  # the refusal, or the report line of a record retired or dropped
    digest: str  # of the file as read, so a removal can check that nobody has written it since


def format_record(fields):
    """The record's text: one field a line, each value in JSON, which YAML also reads."""
    record = {"format": RECORD_FORMAT, **fields}
    return "".join(f"{name}: {json.dumps(record[name])}\n" for name in RECORD_FIELDS if name in record)


def parse_record(text):
    """The record's fields, or RecordUnreadable with the reason."""
    fields = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, colon, value = line.partition(":")
        if not colon or not re.fullmatch(r"[a-z_]+", name):
            raise RecordUnreadable(f"the line {line.strip()[:60]!r} is not a field")
        fields[name] = _scalar(value.strip())
    if not fields:
        raise RecordUnreadable("it holds no fields")
    if fields.get("format") != RECORD_FORMAT:
        raise RecordUnreadable(f"its format is {fields.get('format')!r}, not {RECORD_FORMAT}")
    if fields.get("state") not in (PENDING, UNFINISHED):
        raise RecordUnreadable(f"its state is {fields.get('state')!r}, neither {PENDING} nor {UNFINISHED}")
    for name in ("cluster_uuid", "build", "target_version", "time"):
        if not isinstance(fields.get(name), str) or not fields[name]:
            raise RecordUnreadable(f"it has no {name}")
    try:
        ClusterVersion.parse(fields["target_version"])
    except QumuloError as e:
        raise RecordUnreadable(f"its target_version: {e}") from None
    return fields


def _scalar(value):
    if value in ("", "null", "~"):
        return None
    if value[0] in "'" and value.endswith("'") and len(value) > 1:
        return value[1:-1].replace("''", "'")
    if value[0] in '"[{' or re.fullmatch(r"-?[0-9]+", value) or value in ("true", "false"):
        try:
            return json.loads(value)
        except ValueError:
            raise RecordUnreadable(f"the value {value[:40]!r} cannot be parsed") from None
    return value


def unreadable_record(host, path, reason, hub=None):
    """F31's message, for a record that does not parse or a file that cannot be read."""
    return (
        f"{path} is not a commit record this collection can read: {reason}. Check whether a commit of {host} is unfinished with "
        f"Qumulo Care, then remove the file. To upgrade the rest of the fleet meanwhile, {the_limit(host, hub)}."
    )


def record_digest(text):
    return "sha256:" + hashlib.sha256((text or "").encode()).hexdigest()


def decide_record(host, path, text, found, hub=None):
    """FR-034's table, the first row that matches: the plan and the module both decide a record here.

    found is the host's read, {cluster_uuid, version, build, state}, or None when the read failed; state is the upgrade
    status's, as UPGRADE_STATE_IDLE. hub names the host's portal component for <the limit>.
    """
    digest, limit = record_digest(text), the_limit(host, hub)
    try:
        record = parse_record(text)
    except RecordUnreadable as e:
        return RecordDecision("refuse", "F31", unreadable_record(host, path, e, hub), digest)
    target, recorded, pending = record["target_version"], record["build"], record["state"] == PENDING
    if found and found.get("cluster_uuid") and found["cluster_uuid"].lower() != record["cluster_uuid"].lower():
        return RecordDecision(
            "retire",
            None,
            f"{host}: retired the commit record {path}: {host} is now cluster {found['cluster_uuid']}, not {record['cluster_uuid']}, as after a rebuild",
            digest,
        )
    state = (found or {}).get("state") or ""
    state = state.removeprefix(STATE_PREFIX)
    if pending and found:
        running = ClusterVersion.parse(str(found["version"]))
        if found["build"] != recorded and running >= ClusterVersion.parse(target):
            return RecordDecision(
                "retire",
                None,
                f"{host}: retired the commit record {path}: {host} runs {running} build {found['build']}, so its commit to {target} is done",
                digest,
            )
        if found["build"] == recorded and state in ("PREPARING", "PREPARED"):
            return RecordDecision(
                "drop", None, f"{host}: dropped the commit record {path}: {host} reads {state} on build {recorded}, which a commit never leaves", digest
            )
    if pending and (found is None or (found["build"] == recorded and state in UNDER_WAY)):
        return RecordDecision(
            "refuse",
            "F30",
            f"{host}'s commit to {target}, recorded at {path}, is under way (it reads {state if found else 'no answer'}). Wait for "
            f"it to finish, then run the upgrade again; the record goes by itself once {host} runs {target} on a new build. "
            f"To upgrade the rest of the fleet meanwhile, {limit}.",
            digest,
        )
    clause = f"; it also goes by itself once {host} runs a new build of {target} or a later release" if pending else ""
    return RecordDecision(
        "refuse",
        "F19",
        f"{host}: a commit record, {path}, shows that its commit to {target} on {record['time']} did not finish, and some nodes "
        f"may be armed. Nothing was sent. Contact Qumulo Care. Once they have cleared {host}, remove the record{clause}. "
        f"To upgrade the rest of the fleet meanwhile, {limit}.",
        digest,
    )


def write_record(path, fields):
    """Write the commit record durably: staged, flushed, put in place and its directory flushed, as s3.write_secret does.

    The record replaces any earlier one at path, since a pending record becomes unfinished in place. The file is mode
    0600, in a directory of mode 0700.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, staged = tempfile.mkstemp(dir=directory, prefix=".commit-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as staging:
            staging.write(format_record(fields))
            staging.flush()
            os.fsync(staging.fileno())
        os.replace(staged, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(staged)
        raise
    _sync_directory(directory)


def remove_record(path, digest=None):
    """Remove the record at path, and only while its text still has digest when one is given; whether it was removed."""
    try:
        if digest is not None:
            with open(path, encoding="utf-8") as found:
                if record_digest(found.read()) != digest:
                    return False
        os.unlink(path)
    except FileNotFoundError:
        return False
    _sync_directory(os.path.dirname(os.path.abspath(path)))
    return True


def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
