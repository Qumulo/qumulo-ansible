# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_restore_info: the restore records and locks left on the controller (specs/017 T1750a).

The files are written with restore_record.py's own helpers and the lock's own fields, so a change of either shows here.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils import restore_record
from ansible_collections.qumulo.cluster.plugins.modules import replication_restore_info

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
HOUR = 3600
HOST = "builder01"
BOOT_ID = "8c3f0d2e-5b1a-4c7d-9e6f-1a2b3c4d5e6f"
PID_NAMESPACE = "pid:[4026531836]"
LIVE_PID, LIVE_TICKS = 1717, 900_000
UUID = "6f1c3d1e-0b4a-4e7e-9c55-2b8f0a7d4e10"
KEY = {"source_path": "/projects", "target_host": "hub01", "target_cluster_uuid": UUID, "target_path": "/replicas/edge01"}


def iso(seconds):
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeController:
    """A controller whose clock, host name, boot, PID namespace and processes the test sets, as the lock's tests do."""

    def __init__(self, proc=True):
        self.host = HOST
        self.proc = proc
        self.processes = {LIVE_PID: LIVE_TICKS}

    def clock(self):
        return NOW

    def has_proc(self):
        return self.proc

    def boot_id(self):
        return BOOT_ID if self.proc else None

    def pid_namespace(self):
        return PID_NAMESPACE if self.proc else None

    def start_ticks(self, pid):
        return self.processes.get(pid) if self.proc else None


@pytest.fixture
def controller(monkeypatch):
    fake = FakeController()
    monkeypatch.setattr(replication_restore_info, "controller", fake)
    return fake


@pytest.fixture
def artifacts(tmp_path):
    return tmp_path / "artifacts"


def restore_dir(artifacts, host):
    directory = artifacts / host / restore_record.RESTORE
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_record(artifacts, host="edge01", key=None, name=None, **fields):
    """A record of key under host's restore directory, named by record_path unless name is given; returns its path."""
    key = key or KEY
    record = {
        "format": restore_record.FORMAT,
        "key": key,
        "r_id": "12",
        "x_id": "7",
        "x_source_cluster_uuid": "0e2d4c6a-8b1f-4d3e-a5c7-9f0b2d4e6a8c",
        "x_source_path": "/projects",
        "x_snapshot_ids": [3],
        "recovery_point": "2027-01-14T07:00:00Z",
        "recovery_point_snapshot_id": 12,
        "written_at": iso(NOW - 2 * HOUR),
        **fields,
    }
    directory = restore_dir(artifacts, host)
    path = directory / name if name else restore_record.record_path(str(directory), key)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(record, f, sort_keys=False)
    return str(path)


def write_lock(artifacts, host="edge01", **fields):
    lock = {
        "format": restore_record.FORMAT,
        "holder": "restore",
        "token": "f" * 32,
        "controller": HOST,
        "boot_id": BOOT_ID,
        "pid_namespace": PID_NAMESPACE,
        "pid": LIVE_PID,
        "start_ticks": LIVE_TICKS,
        "start_time": iso(NOW - 3 * HOUR),
        "lease_expiry": iso(NOW + HOUR),
        **fields,
    }
    path = restore_dir(artifacts, host) / restore_record.LOCK
    path.write_text(json.dumps(lock), encoding="utf-8")
    return str(path)


def scan(run_module, artifacts, check_mode=False):
    return run_module(replication_restore_info, {"path": str(artifacts)}, check_mode=check_mode)


def test_a_record_is_listed_with_its_key_the_hosts_it_acts_on_and_its_age(run_module, controller, artifacts):
    path = write_record(artifacts)

    result = scan(run_module, artifacts)

    assert result["changed"] is False
    assert result["path"] == str(artifacts)
    assert result["records"] == [
        {
            "host": "edge01",
            "path": path,
            "key": KEY,
            "r_id": "12",
            "written_at": iso(NOW - 2 * HOUR),
            "reconnected": False,
            "age_seconds": 2 * HOUR,
            "hosts": ["edge01", "hub01"],
        }
    ]
    assert (result["locks"], result["unreadable"]) == ([], [])


def test_a_marked_record_says_so(run_module, controller, artifacts):
    write_record(artifacts, reconnected=True)

    assert scan(run_module, artifacts)["records"][0]["reconnected"] is True


@pytest.mark.parametrize("written_at", [None, "yesterday"])
def test_a_record_without_a_time_it_wrote_is_aged_from_the_file(run_module, controller, artifacts, written_at):
    path = write_record(artifacts, written_at=written_at)
    os.utime(path, (NOW - 5 * HOUR, NOW - 5 * HOUR))

    record = scan(run_module, artifacts)["records"][0]

    assert (record["written_at"], record["age_seconds"]) == (written_at, 5 * HOUR)


def test_a_record_without_a_target_host_acts_on_its_edge_alone(run_module, controller, artifacts):
    write_record(artifacts, key={**KEY, "target_host": None})

    assert scan(run_module, artifacts)["records"][0]["hosts"] == ["edge01"]


def test_records_are_listed_by_host_then_by_name(run_module, controller, artifacts):
    other = {**KEY, "target_path": "/replicas/other"}
    paths = [write_record(artifacts, "edge02"), write_record(artifacts, "edge01", other), write_record(artifacts, "edge01")]

    listed = [record["path"] for record in scan(run_module, artifacts)["records"]]

    assert listed == sorted(paths[1:]) + [paths[0]]


def test_stamps_the_guard_temporaries_and_other_files_are_left_out(run_module, controller, artifacts):
    directory = restore_dir(artifacts, "edge01")
    stamp = restore_record.stamp(KEY, "12", "rebuild-20270101T000000Z.yml", iso(NOW))
    (directory / f"done-{restore_record.key_hash(KEY)}.yml").write_text(yaml.safe_dump(stamp), encoding="utf-8")
    (directory / restore_record.LOCK_GUARD).write_text("", encoding="utf-8")
    (directory / f"{restore_record.LOCK_TEMPORARY}{'a' * 32}").write_text("{}", encoding="utf-8")
    (directory / "notes.txt").write_text("hello", encoding="utf-8")
    (artifacts / "edge01" / "rebuild-20270101T000000Z.yml").write_text("held: []\n", encoding="utf-8")

    result = scan(run_module, artifacts)

    assert (result["records"], result["locks"], result["unreadable"]) == ([], [], [])


def test_a_record_of_an_unknown_format_is_unreadable_with_the_reason(run_module, controller, artifacts):
    path = write_record(artifacts, format=2)

    result = scan(run_module, artifacts)

    assert result["records"] == []
    assert result["unreadable"] == [{"host": "edge01", "path": path, "reason": f"The restore record {path} is of an unknown format: it is not format 1."}]


def test_a_record_under_another_name_than_its_key_s_is_unreadable(run_module, controller, artifacts):
    path = write_record(artifacts, name=f"{'0' * 64}.yml")

    result = scan(run_module, artifacts)

    assert result["records"] == []
    assert result["unreadable"] == [
        {"host": "edge01", "path": path, "reason": f"its name is not {restore_record.key_hash(KEY)}.yml, the SHA-256 of the key it holds"}
    ]


def test_a_record_whose_key_has_a_relative_path_is_unreadable(run_module, controller, artifacts):
    path = write_record(artifacts, key={**KEY, "source_path": "projects"}, name=f"{'1' * 64}.yml")

    assert scan(run_module, artifacts)["unreadable"] == [
        {"host": "edge01", "path": path, "reason": "its key cannot be read: source_path 'projects' must be an absolute path, such as /data/projects"}
    ]


def test_a_live_lock_of_this_controller_is_listed_with_its_age(run_module, controller, artifacts):
    path = write_lock(artifacts)

    assert scan(run_module, artifacts)["locks"] == [
        {
            "host": "edge01",
            "path": path,
            "holder": "restore",
            "controller": HOST,
            "pid": LIVE_PID,
            "start_time": iso(NOW - 3 * HOUR),
            "lease_expiry": iso(NOW + HOUR),
            "liveness": "live",
            "age_seconds": 3 * HOUR,
        }
    ]


@pytest.mark.parametrize(
    ("fields", "liveness"),
    [
        pytest.param({"pid": 4242, "start_ticks": 1}, "gone", id="this controller, its process gone, within its lease"),
        pytest.param({"start_ticks": LIVE_TICKS + 1}, "gone", id="this controller, its process id reused"),
        pytest.param({"controller": "builder02", "lease_expiry": iso(NOW + 1)}, "live", id="another controller, within its lease"),
        pytest.param({"controller": "builder02", "lease_expiry": iso(NOW - 1)}, "expired", id="another controller, past its lease"),
        pytest.param({"boot_id": "another-boot", "lease_expiry": iso(NOW - 1)}, "expired", id="an earlier boot, past its lease"),
        pytest.param({"holder": "upgrade", "lease_expiry": iso(NOW - HOUR)}, "live", id="the upgrade's, alive whatever its lease"),
    ],
)
def test_a_lock_s_liveness_is_read_as_the_lock_plugin_reads_it(run_module, controller, artifacts, fields, liveness):
    write_lock(artifacts, **fields)

    lock = scan(run_module, artifacts)["locks"][0]

    assert lock["liveness"] == liveness
    assert lock["holder"] == fields.get("holder", "restore")


def test_without_proc_the_lease_alone_decides(run_module, controller, artifacts):
    controller.proc = False
    write_lock(artifacts, lease_expiry=iso(NOW - 1))
    write_lock(artifacts, "edge02", pid=4242, start_ticks=1)

    assert [lock["liveness"] for lock in scan(run_module, artifacts)["locks"]] == ["expired", "live"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("", "it is empty or holds no whole JSON document", id="empty"),
        pytest.param('{"format": 1', "it is empty or holds no whole JSON document", id="cut short"),
        pytest.param(json.dumps({"format": 2}), "it is not a lock of format 1", id="another format"),
    ],
)
def test_a_lock_that_cannot_be_read_is_unreadable(run_module, controller, artifacts, text, reason):
    path = restore_dir(artifacts, "edge01") / restore_record.LOCK
    path.write_text(text, encoding="utf-8")

    result = scan(run_module, artifacts)

    assert result["locks"] == []
    assert result["unreadable"] == [{"host": "edge01", "path": str(path), "reason": reason}]


def test_a_missing_directory_lists_nothing(run_module, controller, artifacts):
    result = scan(run_module, artifacts)

    assert "failed" not in result
    assert (result["records"], result["locks"], result["unreadable"]) == ([], [], [])


def test_a_host_directory_without_a_restore_directory_lists_nothing(run_module, controller, artifacts):
    (artifacts / "edge01").mkdir(parents=True)
    (artifacts / "edge01" / "upgrade").mkdir()
    (artifacts / "commit.yml").write_text("format: 1\n", encoding="utf-8")

    assert scan(run_module, artifacts)["records"] == []


def test_a_directory_that_cannot_be_listed_fails_naming_it(run_module, controller, artifacts, monkeypatch):
    write_record(artifacts)
    denied = os.path.join(str(artifacts), "edge01", restore_record.RESTORE)
    listdir = os.listdir

    def refusing(path):
        if os.fspath(path) == denied:
            raise PermissionError(13, "Permission denied", denied)
        return listdir(path)

    monkeypatch.setattr(os, "listdir", refusing)

    result = scan(run_module, artifacts)

    assert result["failed"] is True
    assert result["msg"] == f"Could not list {denied}: [Errno 13] Permission denied: '{denied}'"


def test_check_mode_lists_the_same(run_module, controller, artifacts):
    write_record(artifacts)
    write_lock(artifacts)

    checked = scan(run_module, artifacts, check_mode=True)

    assert (len(checked["records"]), len(checked["locks"])) == (1, 1)
    assert checked == scan(run_module, artifacts)


@pytest.mark.parametrize(("field", "value"), [("target_cluster_uuid", None), ("source_path", 7), ("target_host", 12)])
def test_a_record_whose_key_holds_a_value_that_is_not_a_string_is_unreadable(run_module, controller, artifacts, field, value):
    path = write_record(artifacts, key={**KEY, field: value}, name=f"{'2' * 64}.yml")

    result = scan(run_module, artifacts)

    assert result["records"] == []
    assert result["unreadable"] == [{"host": "edge01", "path": path, "reason": f"its key's {field} is not a string"}]


def test_each_unreadable_file_is_a_warning(run_module, controller, artifacts):
    path = restore_dir(artifacts, "edge01") / restore_record.LOCK
    path.write_text("", encoding="utf-8")

    result = scan(run_module, artifacts)

    assert result["warnings"] == [f"{path} cannot be read as a restore record or lock: it is empty or holds no whole JSON document"]
