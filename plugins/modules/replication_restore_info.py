#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_restore_info
short_description: List the restore records and restore locks left on the controller, with their age
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Lists, under one artifact directory, every C(<host>/restore/) directory's restore records and restore locks, the
    files a restore of a rebuilt edge from its replica keeps on the controller, and the files there it cannot read.
    It writes nothing and sends no request.
  - A restore record is a file named by 64 hexadecimal digits and C(.yml) that holds a restore record of format 1, whose
    name is the SHA-256 of the key it holds. The C(replication_pair) role's restore writes it before it deletes the old
    relationship and removes it once the relationship reads ESTABLISHED on both clusters, so a record left behind is a
    restore that has not finished, or one whose relationship was reconnected by hand.
  - A restore lock is the file C(lock), which a restore holds with holder C(restore) and the upgrade playbook with holder
    C(upgrade). Each is read on this controller as M(qumulo.cluster.replication_restore_lock) reads it, C(live), C(gone) or C(expired).
  - Restore stamps, C(done-<digest>.yml), the guard, C(lock.guard), and a lock being written, C(lock.tmp-<token>), are
    left out.
  - C(playbooks/upgrade.yml) runs it before its plan and warns of each record and live lock that names a host the run
    upgrades (W1).
  - It talks to no cluster, so it takes no connection options, and is not in the C(qumulo.cluster.qumulo) action group,
    so C(module_defaults) for that group do not reach it. It reads the files where the task runs, the controller under
    the inventory's C(ansible_connection=local).
options:
  path:
    description:
      - The artifact directory, a host's C(edge_rebuild_artifact_dir), which holds C(<host>/restore/) for each host. That
        is C(artifacts) beside the host's inventory source unless the variable sets another.
      - A directory that does not exist holds no restore files, so nothing is listed. A directory that exists and cannot
        be listed fails the module with a message that starts C(Could not list) and names the directory.
    type: path
    required: true
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - "API: none."
  - The age of a record is from its C(written_at), or from the file's modification time when C(written_at) is null or
    is not an ISO 8601 time. The age of a lock is from the start of the run that took it.
  - The scan cannot tell a restore that stopped from one that is still running without the clusters; a C(live) lock
    says a run holds the edge now. The C(replication_restore) playbook reads both clusters and either finishes a
    record's restore or says why it cannot.
seealso:
  - module: qumulo.cluster.replication_restore_lock
"""

EXAMPLES = r"""
# Run it on its own before an upgrade or a restore: it reads files on the controller and sends nothing to a cluster.
# From a shell, the same call is
#   ansible localhost -m qumulo.cluster.replication_restore_info -a path=artifacts
# A restore reads only its host's own artifact directory, so list each directory the inventory's hosts use.
- name: List the restore files beside the inventory
  qumulo.cluster.replication_restore_info:
    path: "{{ ansible_inventory_sources | qumulo.cluster.artifact_dir(inventory_file) }}"
  register: restore_files
  run_once: true

# A record is a restore that has not finished. Finish it with ansible-playbook qumulo.cluster.replication_restore, which
# removes the record, and remove it by hand only after reconnecting the relationship by hand.
- name: Show each record left behind, with its age and the relationship it holds
  ansible.builtin.debug:
    msg: >-
      {{ item.host }}: {{ item.path }}, written {{ item.age_seconds // 3600 }} hours ago, restores {{ item.key.source_path }}
      from {{ item.key.target_host }}:{{ item.key.target_path }}
  loop: "{{ restore_files.records }}"
  loop_control:
    label: "{{ item.path }}"
  run_once: true

# A live lock is a run that holds the edge now: leave it alone. The next restore or upgrade of the host takes over a gone
# or expired lock with a warning, so remove one by hand only once the run that took it is gone.
- name: Show each lock whose run is gone or whose lease has run out
  ansible.builtin.debug:
    msg: "{{ item.host }}: {{ item.path }}, held by {{ item.holder }} since {{ item.start_time }}, is {{ item.liveness }}"
  loop: "{{ restore_files.locks | rejectattr('liveness', 'equalto', 'live') | list }}"
  loop_control:
    label: "{{ item.path }}"
  run_once: true
"""

RETURN = r"""
path:
  description: O(path).
  returned: success
  type: str
  sample: /srv/ansible/artifacts
records:
  description: Each restore record, by host and then by name.
  returned: success
  type: list
  elements: dict
  sample:
    - host: edge01
      path: /srv/ansible/artifacts/edge01/restore/0c1f9a7e4b2d8c6a3e5f7b9d1c3a5e7f9b1d3c5a7e9f1b3d5c7a9e1f3b5d7c9a.yml
      key:
        source_path: /edge01
        target_host: dr01
        target_cluster_uuid: 7c2a9f4e-1b3d-4e5f-8a6b-9c0d1e2f3a4b
        target_path: /replicas/edge01
      r_id: "12"
      written_at: "2026-11-05T09:14:02Z"
      reconnected: false
      age_seconds: 86400
      hosts: [edge01, dr01]
  contains:
    host:
      description: The host whose directory holds the record, the edge being restored.
      type: str
    path:
      description: The record's path.
      type: str
    key:
      description: The relationship the record is for, C(source_path) on the edge, C(target_host), C(target_cluster_uuid)
        and C(target_path).
      type: dict
    r_id:
      description: The id of the relationship the restore made from the target back to the edge.
      type: str
    written_at:
      description: When the restore wrote the record, as it wrote it; null when it did not say.
      type: str
    reconnected:
      description: Whether the restore had marked the record before it reconnected the relationship, its last step.
      type: bool
    age_seconds:
      description: Seconds since C(written_at), or since the file was last modified when C(written_at) is null or does
        not parse.
      type: int
    hosts:
      description: The hosts a restore of the record acts on, the edge and the key's C(target_host) when it has one.
      type: list
      elements: str
locks:
  description: Each restore lock, by host.
  returned: success
  type: list
  elements: dict
  sample:
    - host: edge01
      path: /srv/ansible/artifacts/edge01/restore/lock
      holder: restore
      controller: ctl01
      pid: 41872
      start_time: "2026-11-05T09:14:02.000Z"
      lease_expiry: "2026-11-06T10:14:02.000Z"
      liveness: gone
      age_seconds: 90000
  contains:
    host:
      description: The host whose directory holds the lock.
      type: str
    path:
      description: The lock's path.
      type: str
    holder:
      description: What took the lock, C(restore) or C(upgrade), or the holder another caller named.
      type: str
    controller:
      description: The host name of the controller the lock was taken on.
      type: str
    pid:
      description: The id of the C(ansible-playbook) process that took it.
      type: int
    start_time:
      description: When that process started, in ISO 8601 UTC.
      type: str
    lease_expiry:
      description: When the lock's lease runs out, in ISO 8601 UTC.
      type: str
    liveness:
      description:
        - V(live) when a run holds it. On this controller, the same host name, boot id and PID namespace, that is while
          its process is alive, whatever its lease says; from any other controller, until its lease runs out.
        - V(gone) when it was taken on this controller and its process is gone; V(expired) when it was taken elsewhere
          and its lease has run out. The next restore or upgrade of the host takes either over, with a warning.
      type: str
      choices: [live, gone, expired]
    age_seconds:
      description: Seconds since C(start_time); null when C(start_time) is not an ISO 8601 time.
      type: int
unreadable:
  description: Each file named like a record that is not a restore record this collection reads, and each C(lock) that
    is not a lock of format 1, with the reason. Each is also a warning, C(<path> cannot be read as a restore record or
    lock) followed by the reason. A restore stops on a C(lock) here, and on a record whose name is that
    of the relationship it restores, naming the file; an upgrade that locks the host stops on the C(lock).
  returned: success
  type: list
  elements: dict
  sample:
    - host: edge02
      path: /srv/ansible/artifacts/edge02/restore/lock
      reason: it is empty or holds no whole JSON document
  contains:
    host:
      description: The host whose directory holds the file.
      type: str
    path:
      description: The file's path.
      type: str
    reason:
      description: Why it was not read as a record or a lock.
      type: str
msg:
  description: Why the module failed, a message that starts C(Could not list) and names the directory and the error,
    when O(path) or a directory under it exists and cannot be listed. The module then returns C(failed) true and none
    of the other keys.
  returned: failure
  type: str
  sample: "Could not list /srv/ansible/artifacts: [Errno 13] Permission denied: '/srv/ansible/artifacts'"
"""

import json
import os
import re
from datetime import UTC, datetime

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import (
    LOCK,
    RESTORE,
    Controller,
    RestoreRecordError,
    is_lock,
    liveness,
    parse_record,
    record_path,
)

ARGUMENT_SPEC = {"path": {"type": "path", "required": True}}
RECORD_NAME = re.compile(r"[0-9a-f]{64}\.yml")
controller = Controller()


class Unreadable(Exception):
    """A file named like a record or a lock that is neither; the message is the reason."""


def scan(artifact_dir, controller, now):
    """{records, locks, unreadable} under artifact_dir's <host>/restore/ directories, each list by host, then by name."""
    found = {"records": [], "locks": [], "unreadable": []}
    for host in listed(artifact_dir):
        directory = os.path.join(artifact_dir, host, RESTORE)
        if not os.path.isdir(directory):
            continue
        for name in listed(directory):
            path = os.path.join(directory, name)
            try:
                if name == LOCK:
                    found["locks"].append({"host": host, "path": path, **read_lock(path, controller, now)})
                elif RECORD_NAME.fullmatch(name):
                    found["records"].append({"host": host, "path": path, **read_record(path, directory, host, now)})
            except Unreadable as e:
                found["unreadable"].append({"host": host, "path": path, "reason": str(e)})
    return found


def listed(directory):
    """directory's entries by name; none when it does not exist."""
    try:
        return sorted(os.listdir(directory))
    except FileNotFoundError:
        return []


def read_record(path, directory, host, now):
    try:
        with open(path, encoding="utf-8") as f:
            record = parse_record(f.read(), path)
    except (OSError, UnicodeDecodeError) as e:
        raise Unreadable(f"it cannot be read: {e}") from e
    except RestoreRecordError as e:
        raise Unreadable(str(e)) from e
    key = record["key"]
    for field in ("source_path", "target_host", "target_cluster_uuid", "target_path"):
        if not isinstance(key[field], str) and not (field == "target_host" and key[field] is None):
            raise Unreadable(f"its key's {field} is not a string")
    try:
        expected = record_path(directory, key)
    except QumuloError as e:
        raise Unreadable(f"its key cannot be read: {e}") from e
    if expected != path:
        raise Unreadable(f"its name is not {os.path.basename(expected)}, the SHA-256 of the key it holds")
    written = parsed_time(record["written_at"])
    try:
        since = written.timestamp() if written else os.stat(path).st_mtime
    except OSError as e:
        raise Unreadable(f"it cannot be read: {e}") from e
    return {
        "key": {field: key[field] for field in ("source_path", "target_host", "target_cluster_uuid", "target_path")},
        "r_id": record["r_id"],
        "written_at": record["written_at"],
        "reconnected": bool(record.get("reconnected")),
        "age_seconds": age(now, since),
        "hosts": [host] + ([key["target_host"]] if key["target_host"] and key["target_host"] != host else []),
    }


def read_lock(path, controller, now):
    try:
        with open(path, encoding="utf-8") as f:
            lock = json.load(f)
    except ValueError as e:
        raise Unreadable("it is empty or holds no whole JSON document") from e
    except OSError as e:
        raise Unreadable(f"it cannot be read: {e}") from e
    if not is_lock(lock):
        raise Unreadable("it is not a lock of format 1")
    started = parsed_time(lock["start_time"])
    return {
        "holder": lock["holder"],
        "controller": lock["controller"],
        "pid": lock["pid"],
        "start_time": lock["start_time"],
        "lease_expiry": lock["lease_expiry"],
        "liveness": liveness(lock, controller, now),
        "age_seconds": age(now, started.timestamp()) if started else None,
    }


def parsed_time(text):
    """An ISO 8601 time as an aware datetime, a time without a zone counting as UTC; None when text is not one."""
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def age(now, since):
    return max(0, int(now - since))


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    path = module.params["path"]
    try:
        found = scan(path, controller, controller.clock())
    except OSError as e:
        module.fail_json(msg=f"Could not list {e.filename}: {e}")
    for entry in found["unreadable"]:
        module.warn(f"{entry['path']} cannot be read as a restore record or lock: {entry['reason']}")
    module.exit_json(changed=False, path=path, **found)


if __name__ == "__main__":
    main()
