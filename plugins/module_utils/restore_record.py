# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The restore's files on the controller, under <artifact dir>/<host>/restore/: the restore record, the restore stamp
and the lock, and the rebuild report's claim on a relationship.

A record and a stamp are named by the SHA-256 of the relationship's normalized key, so another controller, or an
inventory edit that adds a trailing slash or renames the target host, finds the same files; each file holds the full
key, the target host included.

The lock's shape, is_lock, and what a lock's liveness reads of this controller, Controller, are here too, so the lock
plugin and the scan of the restore files judge a lock alike.
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import time
from datetime import UTC, datetime

from ansible.module_utils.common.yaml import HAS_YAML, yaml_load
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_path

FORMAT = 1
KEY_FIELDS = ("source_path", "target_host", "target_cluster_uuid", "target_path")
RECORD_FIELDS = (
    "format",
    "key",
    "r_id",
    "x_id",
    "x_source_cluster_uuid",
    "x_source_path",
    "x_snapshot_ids",
    "recovery_point",
    "recovery_point_snapshot_id",
    "written_at",
)  # and reconnected, once the record is marked
RESTORE = "restore"
# The lock's files, which a reader of the restore directory, as specs/017's upgrade plan, tells from the records.
LOCK = "lock"
LOCK_GUARD = "lock.guard"
LOCK_TEMPORARY = "lock.tmp-"  # followed by the run's token
REPORT_PREFIX, REPORT_SUFFIX = "rebuild-", ".yml"
STAMP = re.compile(r"done-([0-9a-f]{64})\.yml")

if HAS_YAML:
    from yaml import YAMLError
else:  # without PyYAML, yaml_load itself fails, as on any host that reads no record
    YAMLError = ImportError


class RestoreRecordError(QumuloError):
    """A restore record of an unknown format."""


def normalized_key(key):
    """The key with each path canonical, without its trailing slash ("/" stays "/"), and the UUID in lower case."""
    return {
        "source_path": canonical_path(key["source_path"], "source_path"),
        "target_host": key.get("target_host"),
        "target_cluster_uuid": key["target_cluster_uuid"].lower(),
        "target_path": canonical_path(key["target_path"], "target_path"),
    }


def key_hash(key):
    """The SHA-256 hex digest of the normalized key's three lines; the target host is not hashed."""
    key = normalized_key(key)
    lines = "\n".join((key["source_path"], key["target_cluster_uuid"], key["target_path"]))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def restore_dir(artifact_dir, host):
    return os.path.join(artifact_dir, host, RESTORE)


def record_path(directory, key):
    return os.path.join(directory, f"{key_hash(key)}.yml")


def stamp_path(directory, key):
    return os.path.join(directory, f"done-{key_hash(key)}.yml")


def parse_record(text, path):
    """The record in text, read from path; one whose format is not 1, or that lacks a field, fails naming path."""
    try:
        record = yaml_load(text)
    except YAMLError as e:
        raise RestoreRecordError(f"The restore record {path} is of an unknown format: {e}") from e
    if not isinstance(record, dict) or record.get("format") != FORMAT or isinstance(record["format"], bool):
        raise RestoreRecordError(f"The restore record {path} is of an unknown format: it is not format {FORMAT}.")
    missing = [field for field in RECORD_FIELDS if field not in record]
    key = record.get("key")
    missing += [f"key.{field}" for field in KEY_FIELDS if not isinstance(key, dict) or field not in key]
    if missing:
        raise RestoreRecordError(f"The restore record {path} is of an unknown format: it lacks {', '.join(missing)}.")
    return record


def stamp(key, r_id, report, written_at):
    """The restore stamp that retires report's claim on the relationship of key."""
    return {"format": FORMAT, "key": normalized_key(key), "r_id": r_id, "report": report, "written_at": written_at}


def read_yaml(path):
    """The file's YAML, or None when it is missing or cannot be read as YAML."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml_load(f)
    except (OSError, UnicodeDecodeError, YAMLError):
        return None


def report_claims(host_dir):
    """{report, claimed, stamped}: the newest rebuild-*.yml in host_dir by name, as roles/edge_rebuild/tasks/previous.yml
    picks it, the normalized keys of its held: list that no stamp naming that report retires, and the key hashes of every
    stamp that names it, which shows a relationship restored or paired since that rebuild.

    A report with no held: list, as one written before the restore existed, claims nothing, and so does no report, a report that cannot
    be read as YAML, or a held: entry without a key of the restore record's shape. A host_dir that cannot be listed
    raises OSError.
    """
    listed = os.listdir(host_dir) if host_dir and os.path.isdir(host_dir) else []
    reports = sorted(name for name in listed if name.startswith(REPORT_PREFIX) and name.endswith(REPORT_SUFFIX))
    if not reports:
        return {"report": None, "claimed": [], "stamped": []}
    newest = reports[-1]
    report = read_yaml(os.path.join(host_dir, newest))
    held = (report.get("held") if isinstance(report, dict) else None) or []
    directory = os.path.join(host_dir, RESTORE)
    claimed = []
    for entry in held if isinstance(held, list) else []:
        key = _held_key(entry)
        if key is None:
            continue
        if not _names_report(read_yaml(stamp_path(directory, key)), newest):
            claimed.append(key)
    stamps = sorted(name for name in (os.listdir(directory) if os.path.isdir(directory) else []) if STAMP.fullmatch(name))
    stamped = [STAMP.fullmatch(name)[1] for name in stamps if _names_report(read_yaml(os.path.join(directory, name)), newest)]
    return {"report": newest, "claimed": claimed, "stamped": stamped}


def _names_report(written, report):
    return isinstance(written, dict) and written.get("format") == FORMAT and written.get("report") == report


def _held_key(entry):
    """The normalized key of a held: entry, or None when it has no key of the restore record's shape."""
    key = entry.get("key") if isinstance(entry, dict) else None
    if not isinstance(key, dict) or not all(isinstance(key.get(field), str) for field in KEY_FIELDS if field != "target_host"):
        return None
    try:
        return normalized_key(key)
    except QumuloError:
        return None


NONE = type(None)
# The lock's fields and their types; format is checked on its own, since a bool is an int.
LOCK_FIELDS = {
    "holder": str,
    "token": str,
    "controller": str,
    "boot_id": (str, NONE),
    "pid_namespace": (str, NONE),
    "pid": int,
    "start_ticks": (int, NONE),
    "start_time": str,
    "lease_expiry": str,
}


def iso(seconds):
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Controller:
    """What the lock reads of this controller: its name, the clock, and through /proc its boot, its PID namespace and its
    processes' starts."""

    def __init__(self, proc="/proc", host=None, clock=time.time):
        self.proc = proc
        self.host = host or socket.gethostname()
        self.clock = clock

    def has_proc(self):
        return os.path.isfile(os.path.join(self.proc, "stat"))

    def boot_id(self):
        """The id the kernel gave this boot, or None when this controller has no /proc."""
        try:
            with open(os.path.join(self.proc, "sys", "kernel", "random", "boot_id"), encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def pid_namespace(self):
        """This process's PID namespace, whose process ids /proc shows, or None when this controller has no /proc."""
        return own_pid_namespace(self.proc)

    def start_ticks(self, pid):
        """When the process started, in clock ticks since boot, or None when it is gone or this controller has no /proc."""
        try:
            with open(os.path.join(self.proc, str(pid), "stat"), encoding="utf-8") as f:
                # The command name, field 2, is in parentheses and may hold spaces; start time is field 22.
                return int(f.read().rpartition(")")[2].split()[19])
        except (OSError, ValueError, IndexError):
            return None

    def start_time(self, pid):
        """When the process started, in ISO 8601 UTC, or None as for start_ticks.

        btime is the wall-clock time of boot, which a step of the clock moves, so this is for the messages alone.
        """
        ticks = self.start_ticks(pid)
        if ticks is None:
            return None
        try:
            with open(os.path.join(self.proc, "stat"), encoding="utf-8") as f:
                boot = next(int(line.split()[1]) for line in f if line.startswith("btime "))
        except (OSError, ValueError, IndexError, StopIteration):
            return None
        return iso(boot + ticks / os.sysconf("SC_CLK_TCK"))


def is_lock(found):
    """Whether found is a lock of format 1: each field there with its type, and a lease expiry that parses."""
    if not isinstance(found, dict):
        return False
    lock_format = found.get("format")
    if isinstance(lock_format, bool) or not isinstance(lock_format, int) or lock_format != FORMAT:
        return False
    if any(field not in found or isinstance(found[field], bool) or not isinstance(found[field], kind) for field, kind in LOCK_FIELDS.items()):
        return False
    try:
        datetime.fromisoformat(found["lease_expiry"])
    except ValueError:
        return False
    return True


def own_pid_namespace(proc="/proc"):
    """This process's PID namespace, as "pid:[4026531836]", or None when proc cannot be read."""
    try:
        return os.readlink(os.path.join(proc, "self", "ns", "pid"))
    except OSError:
        return None


def lock_liveness(lock, host, boot_id, now, start_ticks, pid_namespace=None):
    """Whether lock, read on the controller named host whose boot is boot_id, at now (epoch seconds), still holds:
    "live", "gone" or "expired".

    A lock taken on this controller, the same host name, boot and PID namespace, lives while its process does, with
    the start ticks it recorded, whatever its lease says, and is "gone" otherwise. One taken on another host or another
    boot, or in another PID namespace on this machine, as another container's, whose process ids this /proc does not
    show, or read where boot_id and start_ticks are None because this controller has no /proc, lives until its lease
    runs out, and is "expired" from then. start_ticks(pid) gives a process's start on this controller, in clock ticks
    since boot, which no step of the clock moves, or None when the process is gone. pid_namespace is the one
    start_ticks reads in; None stands for this process's own; when neither can be read, the lease alone decides.
    """
    namespace = pid_namespace or own_pid_namespace()
    here = (host, boot_id, namespace)
    if None not in here and start_ticks is not None and (lock["controller"], lock.get("boot_id"), lock.get("pid_namespace")) == here:
        return "live" if start_ticks(lock["pid"]) == lock.get("start_ticks") else "gone"
    expiry = datetime.fromisoformat(lock["lease_expiry"]).timestamp()
    return "expired" if now >= expiry else "live"


def liveness(lock, controller, now):
    """lock_liveness of lock read on controller at now: without /proc, neither the boot nor a process can be read, so the
    lease alone decides."""
    boot_id, start_ticks = (controller.boot_id(), controller.start_ticks) if controller.has_proc() else (None, None)
    return lock_liveness(lock, controller.host, boot_id, now, start_ticks, pid_namespace=controller.pid_namespace())


def with_article(word):
    """word after its indefinite article, which its first letter decides: "a restore", "an upgrade"."""
    return f"{'an' if word[:1].lower() in 'aeiou' else 'a'} {word}"
