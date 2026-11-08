# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.replication_restore_lock: one restore per edge at a time on a controller."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from ansible.errors import AnsibleActionFail
from ansible_collections.qumulo.cluster.plugins.action import replication_restore_lock
from ansible_collections.qumulo.cluster.plugins.action.replication_restore_lock import ActionModule, Controller
from ansible_collections.qumulo.cluster.plugins.module_utils import restore_record

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
HOUR = 3600
HOST = "builder01"
OTHER_HOST = "builder02"
PID = 4242  # this run's ansible-playbook process
BOOT_ID = "8c3f0d2e-5b1a-4c7d-9e6f-1a2b3c4d5e6f"
PID_NAMESPACE = "pid:[4026531836]"
OTHER_NAMESPACE = "pid:[4026532718]"  # a container's, on the same machine and boot
HZ = 100
BOOTED = NOW - 10 * HOUR
STARTED_TICKS = 9 * HOUR * HZ
STARTED = "2027-01-15T07:00:00.000Z"
OTHER_TICKS = 8 * HOUR * HZ  # another run's process, started at 2027-01-15T06:00:00Z


def iso(seconds):
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class FakeController:
    """A controller whose clock, host name, boot, PID namespace and processes' start ticks the test sets."""

    def __init__(self, host=HOST, proc=True, processes=None):
        self.host = host
        self.proc = proc
        self.boot = BOOT_ID
        self.namespace = PID_NAMESPACE
        self.processes = {PID: STARTED_TICKS} if processes is None else processes
        self.now = NOW

    def clock(self):
        return self.now

    def has_proc(self):
        return self.proc

    def boot_id(self):
        return self.boot if self.proc else None

    def pid_namespace(self):
        return self.namespace if self.proc else None

    def start_ticks(self, pid):
        return self.processes.get(pid) if self.proc else None

    def start_time(self, pid):
        ticks = self.start_ticks(pid)
        return None if ticks is None else iso(BOOTED + ticks / HZ)


@pytest.fixture
def controller(monkeypatch):
    fake = FakeController()
    monkeypatch.setattr(ActionModule, "controller", fake)
    monkeypatch.setattr(replication_restore_lock.os, "getppid", lambda: PID)
    return fake


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "artifacts" / "edge01" / "restore" / "lock"


def run(args, check_mode=False, task_vars=None):
    """The plugin's result, with a refusal as Ansible reports one: failed, with its message."""
    task = MagicMock(args=args, check_mode=check_mode, async_val=0, action="qumulo.cluster.replication_restore_lock")
    plugin = ActionModule(task, MagicMock(), MagicMock(), loader=None, templar=None, shared_loader_obj=None)
    try:
        return plugin.run(task_vars={"inventory_hostname": "edge01", **(task_vars or {})})
    except AnsibleActionFail as e:
        return {"failed": True, "msg": e.message}


def take(lock_path, lease=HOUR, holder=None, token=None, **kwargs):
    args = {"path": str(lock_path), "lease": lease, **({"holder": holder} if holder else {}), **({"token": token} if token else {})}
    return run(args, **kwargs)


def remove(lock_path, token, **kwargs):
    return run({"path": str(lock_path), "state": "absent", "token": token}, **kwargs)


def held(lock_path):
    return json.loads(lock_path.read_text(encoding="utf-8"))


def other_lock(
    lock_path,
    token="f" * 32,
    controller=HOST,
    boot_id=BOOT_ID,
    pid_namespace=PID_NAMESPACE,
    pid=1717,
    start_ticks=OTHER_TICKS,
    lease_expiry=NOW + HOUR,
    holder="restore",
):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = {
        "format": 1,
        "holder": holder,
        "token": token,
        "controller": controller,
        "boot_id": boot_id,
        "pid_namespace": pid_namespace,
        "pid": pid,
        "start_ticks": start_ticks,
        "start_time": "2027-01-15T06:00:00.000Z",
        "lease_expiry": iso(lease_expiry),
    }
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock


def refusal(lock_path, lock, host="edge01"):
    return (
        f"{lock_path} shows {'an' if lock['holder'][0] in 'aeiou' else 'a'} {lock['holder']} of {host} under way since "
        f"{lock['start_time']}, by ansible-playbook process "
        f"{lock['pid']} on {lock['controller']}, with a lease until {lock['lease_expiry']}. Run again once it ends or its "
        "lease runs out; remove the file only if that process is gone."
    )


def lost(lock_path, lock, host="edge01"):
    """The refusal of a renewal that finds another run's lock where its own was."""
    return (
        f"Could not renew {lock_path}, which now shows {'an' if lock['holder'][0] in 'aeiou' else 'a'} {lock['holder']} of "
        f"{host} under way since {lock['start_time']}, by ansible-playbook process {lock['pid']} on {lock['controller']}, "
        f"with a lease until {lock['lease_expiry']}. Run again once it ends or its lease runs out."
    )


def leftovers(lock_path):
    return sorted(name for name in os.listdir(lock_path.parent) if ".tmp-" in name)


def test_the_take_writes_the_lock_through_a_link_and_reports_no_change(controller, lock_path):
    result = take(lock_path, lease=2 * HOUR)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["took_over"] is False
    assert "warnings" not in result
    token = result["token"]
    assert len(token) == 32
    assert "ansible_facts" not in result
    lock = {
        "format": 1,
        "holder": "restore",
        "token": token,
        "controller": HOST,
        "boot_id": BOOT_ID,
        "pid_namespace": PID_NAMESPACE,
        "pid": PID,
        "start_ticks": STARTED_TICKS,
        "start_time": STARTED,
        "lease_expiry": iso(NOW + 2 * HOUR),
    }
    assert held(lock_path) == lock
    assert result["lock"] == lock
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert lock_path.stat().st_nlink == 1  # the temporary name is unlinked
    assert leftovers(lock_path) == []


def test_each_token_is_new(controller, lock_path):
    first = take(lock_path)
    assert remove(lock_path, first["token"])["removed"] is True

    assert take(lock_path)["token"] != first["token"]


def test_the_missing_directories_are_made_0700_and_an_existing_ones_mode_is_kept(controller, lock_path):
    artifacts = lock_path.parent.parent.parent
    artifacts.mkdir(mode=0o755)
    os.chmod(artifacts, 0o755)

    assert not take(lock_path).get("failed")

    assert stat.S_IMODE(artifacts.stat().st_mode) == 0o755
    assert stat.S_IMODE(lock_path.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(lock_path.parent.stat().st_mode) == 0o700


def test_an_existing_restore_directory_keeps_its_mode(controller, lock_path):
    lock_path.parent.mkdir(parents=True)
    os.chmod(lock_path.parent, 0o750)

    assert not take(lock_path).get("failed")

    assert stat.S_IMODE(lock_path.parent.stat().st_mode) == 0o750


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_directory_that_cannot_be_written_fails_the_take(controller, lock_path):
    lock_path.parent.mkdir(parents=True)
    os.chmod(lock_path.parent, 0o500)
    try:
        result = take(lock_path)
    finally:
        os.chmod(lock_path.parent, 0o700)

    assert result["failed"] is True
    assert result["msg"].startswith(f"Could not take {lock_path}: ")
    assert not lock_path.exists()


def test_a_live_lock_on_this_controller_refuses(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path)

    result = take(lock_path)

    assert result == {"failed": True, "msg": refusal(lock_path, lock)}
    assert held(lock_path) == lock
    assert leftovers(lock_path) == []


def test_a_live_lock_on_this_controller_is_never_taken_over_past_its_lease(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path, lease_expiry=NOW - 10 * HOUR)

    assert take(lock_path) == {"failed": True, "msg": refusal(lock_path, lock)}
    assert held(lock_path) == lock


def test_a_second_import_in_this_process_finds_its_own_process_live(controller, lock_path):
    """The token tells the runs apart: the same process's lock of another token is another run's, and alive."""
    first = take(lock_path)

    second = take(lock_path)

    assert second == {"failed": True, "msg": refusal(lock_path, first["lock"])}


@pytest.mark.parametrize("processes", [{}, {1717: OTHER_TICKS + 1}], ids=["gone", "pid-reused"])
def test_a_lock_on_this_controller_whose_process_is_gone_is_taken_over(controller, lock_path, processes):
    controller.processes.update(processes)
    other_lock(lock_path, lease_expiry=NOW + 10 * HOUR)

    result = take(lock_path)

    assert not result.get("failed"), result.get("msg")
    assert result["warnings"] == [
        f"Took over {lock_path}, left by ansible-playbook process 1717 on {HOST}, which started at 2027-01-15T06:00:00.000Z and is gone."
    ]
    assert result["took_over"] is True
    assert result["changed"] is False
    assert held(lock_path)["token"] == result["token"]
    assert leftovers(lock_path) == []


def test_a_lock_from_another_host_is_taken_over_once_its_lease_ran_out(controller, lock_path):
    other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW - 1)

    result = take(lock_path)

    assert not result.get("failed"), result.get("msg")
    assert result["warnings"] == [f"Took over {lock_path}, left by ansible-playbook process 1717 on {OTHER_HOST}, whose lease ran out at {iso(NOW - 1)}."]
    assert held(lock_path)["token"] == result["token"]


def test_the_lock_guard_and_temporary_are_the_names_restore_record_gives_them(controller, lock_path, monkeypatch):
    """specs/017's upgrade tells the lock's files from the records by these names."""
    other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW - 1)
    linked = []
    link = os.link
    monkeypatch.setattr(replication_restore_lock.os, "link", lambda source, destination: linked.append(os.path.basename(source)) or link(source, destination))

    take(lock_path)

    assert sorted(os.listdir(lock_path.parent)) == sorted([restore_record.LOCK, restore_record.LOCK_GUARD])
    assert linked and all(name.startswith(restore_record.LOCK_TEMPORARY) for name in linked)


def test_a_lock_from_another_host_within_its_lease_refuses_whatever_its_pid(controller, lock_path):
    lock = other_lock(lock_path, controller=OTHER_HOST, pid=PID, lease_expiry=NOW + 1)

    assert take(lock_path) == {"failed": True, "msg": refusal(lock_path, lock)}


def test_a_lock_from_this_host_name_on_another_boot_refuses_within_its_lease(controller, lock_path):
    """Another machine of the same host name, or this one before a reboot: its process cannot be read here."""
    lock = other_lock(lock_path, boot_id="0d9c8b7a-6f5e-4d3c-8b2a-1f0e9d8c7b6a", lease_expiry=NOW + 1)

    assert take(lock_path) == {"failed": True, "msg": refusal(lock_path, lock)}


def test_a_lock_from_this_host_name_on_another_boot_is_taken_over_once_its_lease_ran_out(controller, lock_path):
    other_lock(lock_path, boot_id="0d9c8b7a-6f5e-4d3c-8b2a-1f0e9d8c7b6a", lease_expiry=NOW - 1)

    result = take(lock_path)

    assert result["warnings"] == [f"Took over {lock_path}, left by ansible-playbook process 1717 on {HOST}, whose lease ran out at {iso(NOW - 1)}."]


def test_a_lock_from_another_pid_namespace_refuses_within_its_lease_whatever_this_proc_says(controller, lock_path):
    """A container on this machine shares its host name and boot, but its process ids are not this /proc's.

    Two ansible-playbook processes in PID namespaces of their own, as `unshare -Urpf --mount-proc` makes, show the
    takeover this prevents. That needs unprivileged user namespaces, which the CI runners (ubuntu-24.04, where AppArmor
    restricts them) do not allow, so no tools test runs it; this faked namespace stands in for it.
    """
    lock = other_lock(lock_path, pid_namespace=OTHER_NAMESPACE, lease_expiry=NOW + 1)

    assert take(lock_path) == {"failed": True, "msg": refusal(lock_path, lock)}
    assert held(lock_path) == lock


def test_a_lock_from_another_pid_namespace_is_taken_over_once_its_lease_ran_out(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS  # this namespace's process 1717 is not that lock's
    other_lock(lock_path, pid_namespace=OTHER_NAMESPACE, lease_expiry=NOW - 1)

    result = take(lock_path)

    assert result["warnings"] == [f"Took over {lock_path}, left by ansible-playbook process 1717 on {HOST}, whose lease ran out at {iso(NOW - 1)}."]


def test_a_lock_from_another_pid_namespace_with_this_runs_pid_and_start_is_not_this_runs(controller, lock_path):
    lock = other_lock(lock_path, pid_namespace=OTHER_NAMESPACE, pid=PID, start_ticks=STARTED_TICKS)

    assert remove(lock_path, lock["token"])["removed"] is False
    assert held(lock_path) == lock


def test_without_proc_this_controllers_lock_refuses_until_its_lease_runs_out(controller, lock_path):
    controller.proc = False
    lock = other_lock(lock_path, lease_expiry=NOW + HOUR)

    assert take(lock_path) == {"failed": True, "msg": refusal(lock_path, lock)}

    controller.now = NOW + HOUR
    result = take(lock_path)

    assert result["warnings"] == [f"Took over {lock_path}, left by ansible-playbook process 1717 on {HOST}, whose lease ran out at {iso(NOW + HOUR)}."]


def test_without_proc_the_lock_is_under_way_since_its_take(controller, lock_path):
    controller.proc = False

    lock = take(lock_path)["lock"]

    assert (lock["start_time"], lock["boot_id"], lock["pid_namespace"], lock["start_ticks"]) == (iso(NOW), None, None, None)


def test_without_proc_the_holder_renews_and_removes_its_lock(controller, lock_path):
    controller.proc = False
    first = take(lock_path)
    controller.now = NOW + 1

    renewed = take(lock_path, token=first["token"])

    assert not renewed.get("failed"), renewed.get("msg")
    assert renewed["token"] == first["token"]
    assert remove(lock_path, first["token"])["removed"] is True


def test_the_race_for_the_guard_is_decided_by_its_holder(controller, lock_path, monkeypatch):
    """A takeover waiting on the guard reads the lock again once it holds it: a lock replaced meanwhile is decided anew."""
    other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW - 1)
    guard = os.open(f"{lock_path}.guard", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(guard, fcntl.LOCK_EX)
    waiting = threading.Event()
    flock = fcntl.flock

    def signalling_flock(fd, operation):
        waiting.set()
        flock(fd, operation)

    monkeypatch.setattr(replication_restore_lock.fcntl, "flock", signalling_flock)
    results = []
    racer = threading.Thread(target=lambda: results.append(take(lock_path)))
    racer.start()
    assert waiting.wait(10)
    winner = other_lock(lock_path, token="e" * 32, controller=OTHER_HOST, lease_expiry=NOW + HOUR)
    fcntl.flock(guard, fcntl.LOCK_UN)
    os.close(guard)
    racer.join(10)

    assert results == [{"failed": True, "msg": refusal(lock_path, winner)}]
    assert held(lock_path) == winner


def test_a_crash_inside_the_takeover_leaves_nothing_to_clean_up(controller, lock_path, monkeypatch):
    """The kernel drops a dead process's flock, so the next run takes the lock over as if the crash had not happened."""
    other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW - 1)
    unlink = os.unlink

    def crashing_unlink(path):
        if str(path) == str(lock_path):
            os._exit(3)
        unlink(path)

    child = os.fork()
    if child == 0:
        try:
            monkeypatch.setattr(replication_restore_lock.os, "unlink", crashing_unlink)
            take(lock_path)
        finally:
            os._exit(4)
    status = os.waitpid(child, 0)[1]
    assert os.waitstatus_to_exitcode(status) == 3

    guard = os.open(f"{lock_path}.guard", os.O_RDWR)
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(guard)
    result = take(lock_path)

    assert not result.get("failed"), result.get("msg")
    assert held(lock_path)["token"] == result["token"]


def test_a_fresh_link_that_wins_after_the_takeovers_unlink_is_decided_anew(controller, lock_path, monkeypatch):
    """A fresh run's first link takes no guard, so it can win between a takeover's unlink and its link."""
    other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW - 1)
    unlink = os.unlink
    winner = []

    def unlink_then_lose_the_race(path):
        unlink(path)
        if str(path) == str(lock_path) and not winner:
            winner.append(other_lock(lock_path, token="e" * 32, controller=OTHER_HOST, lease_expiry=NOW + HOUR))

    monkeypatch.setattr(replication_restore_lock.os, "unlink", unlink_then_lose_the_race)

    result = take(lock_path)

    assert result == {"failed": True, "msg": refusal(lock_path, winner[0])}
    assert held(lock_path) == winner[0]
    assert leftovers(lock_path) == []


def test_a_lock_that_vanishes_between_the_link_and_its_read_is_decided_anew(controller, lock_path, monkeypatch):
    lock = other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW + HOUR)
    read = replication_restore_lock.RestoreLock.read
    reads = []

    def vanished_once(self):
        reads.append(self.path)
        return None if len(reads) == 1 else read(self)

    monkeypatch.setattr(replication_restore_lock.RestoreLock, "link", lambda self: False)
    monkeypatch.setattr(replication_restore_lock.RestoreLock, "read", vanished_once)

    result = take(lock_path)

    assert result == {"failed": True, "msg": refusal(lock_path, lock)}
    assert len(reads) == 2


def test_a_lock_that_vanishes_on_every_attempt_fails_the_take(controller, lock_path, monkeypatch):
    monkeypatch.setattr(replication_restore_lock.RestoreLock, "link", lambda self: False)
    monkeypatch.setattr(replication_restore_lock.RestoreLock, "read", lambda self: None)

    result = take(lock_path)

    assert result == {"failed": True, "msg": f"{lock_path} changed 10 times while this run decided on it; run again."}
    assert leftovers(lock_path) == []


@pytest.mark.parametrize("text", [b"", b"{", b'{"format": 1, "holder": "rest', b"\xff\xfe"], ids=["empty", "brace", "cut-short", "not-utf-8"])
def test_a_lock_that_is_not_json_refuses_with_the_way_on_and_is_kept(controller, lock_path, text):
    """As a crash of the machine can leave a lock whose link reached the disk before its data."""
    lock_path.parent.mkdir(parents=True)
    lock_path.write_bytes(text)

    result = take(lock_path)

    assert result == {
        "failed": True,
        "msg": (
            f"Could not read {lock_path}: it is empty or holds no whole JSON document, as a crash of the controller that "
            "wrote it can leave it. Confirm that no run on edge01 is under way, then remove the file by hand."
        ),
    }
    assert lock_path.read_bytes() == text
    assert leftovers(lock_path) == []


def recording(monkeypatch, events):
    """Records each fsync, link and replace the plugin makes, in order, by the file's name."""
    fsync, link, replace = os.fsync, os.link, os.replace

    def recorded_fsync(fd):
        events.append(("fsync", os.path.basename(os.readlink(f"/proc/self/fd/{fd}"))))
        fsync(fd)

    def recorded_link(source, destination):
        events.append(("link", os.path.basename(source)))
        link(source, destination)

    def recorded_replace(source, destination):
        events.append(("replace", os.path.basename(source)))
        replace(source, destination)

    monkeypatch.setattr(replication_restore_lock.os, "fsync", recorded_fsync)
    monkeypatch.setattr(replication_restore_lock.os, "link", recorded_link)
    monkeypatch.setattr(replication_restore_lock.os, "replace", recorded_replace)


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="names an fd through /proc")
def test_the_temporary_reaches_the_disk_before_its_link_or_rename(controller, lock_path, monkeypatch):
    """Without the fsync, a crash of the machine can keep the link and lose the data, leaving an empty lock."""
    events = []
    recording(monkeypatch, events)

    token = take(lock_path)["token"]
    take(lock_path, token=token)

    temporary = f"lock.tmp-{token}"
    assert events == [("fsync", temporary), ("link", temporary), ("fsync", temporary), ("replace", temporary)]


def test_a_leftover_temporary_older_than_the_lease_is_removed(controller, lock_path):
    lock_path.parent.mkdir(parents=True)
    old, young = lock_path.parent / f"lock.tmp-{'a' * 32}", lock_path.parent / f"lock.tmp-{'b' * 32}"
    for path, age in ((old, HOUR + 1), (young, HOUR - 1)):
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (NOW - age, NOW - age))
    unrelated = lock_path.parent / "done-0.yml"
    unrelated.write_text("{}", encoding="utf-8")
    os.utime(unrelated, (0, 0))

    assert not take(lock_path, lease=HOUR).get("failed")

    assert leftovers(lock_path) == [young.name]
    assert unrelated.exists()


def test_a_link_that_answers_eexist_for_its_own_lock_has_won(controller, lock_path, monkeypatch):
    """As on NFS, where a retransmitted link can answer EEXIST for a link that succeeded."""
    link = os.link

    def retransmitted(source, destination):
        link(source, destination)
        raise FileExistsError(destination)

    monkeypatch.setattr(replication_restore_lock.os, "link", retransmitted)

    result = take(lock_path)

    assert not result.get("failed"), result.get("msg")
    assert held(lock_path)["token"] == result["token"]
    assert "warnings" not in result


def without(field):
    def lock(lock_path):
        found = other_lock(lock_path)
        del found[field]
        return found

    return lock


def having(field, value):
    return lambda lock_path: {**other_lock(lock_path), field: value}


@pytest.mark.parametrize(
    "lock",
    [
        lambda _: {"format": 2},
        lambda _: {"format": 1, "token": "f" * 32},
        lambda _: [1],
        without("pid_namespace"),
        without("token"),
        without("controller"),
        without("pid"),
        without("lease_expiry"),
        having("format", True),
        having("pid", "1717"),
        having("pid", True),
        having("token", None),
        having("lease_expiry", "soon"),
    ],
    ids=[
        "format-2",
        "no-holder",
        "not-a-mapping",
        "no-pid-namespace",
        "no-token",
        "no-controller",
        "no-pid",
        "no-lease-expiry",
        "format-true",
        "pid-a-string",
        "pid-true",
        "token-null",
        "lease-expiry-not-a-time",
    ],
)
def test_a_lock_of_another_format_refuses(controller, lock_path, lock):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock(lock_path)), encoding="utf-8")

    result = take(lock_path)

    assert result == {"failed": True, "msg": f"{lock_path} is not a lock of format 1. Remove it only if no run on edge01 holds it."}


def test_the_lock_names_its_holder(controller, lock_path):
    result = take(lock_path, holder="upgrade")

    assert (result["lock"]["holder"], held(lock_path)["holder"]) == ("upgrade", "upgrade")


def test_another_holders_live_lock_refuses_naming_that_holder(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path, holder="upgrade")

    result = take(lock_path)

    assert result == {"failed": True, "msg": refusal(lock_path, lock)}
    assert result["msg"].startswith(f"{lock_path} shows an upgrade of edge01 under way since ")


def test_the_holder_renews_its_lock_extending_the_lease(controller, lock_path):
    first = take(lock_path, lease=HOUR)
    controller.now = NOW + HOUR / 2

    renewed = take(lock_path, lease=2 * HOUR, token=first["token"])

    assert not renewed.get("failed"), renewed.get("msg")
    assert (renewed["changed"], renewed["took_over"]) == (False, False)
    assert "renewed" not in renewed
    assert "warnings" not in renewed
    assert renewed["token"] == first["token"]
    assert held(lock_path) == renewed["lock"] == {**first["lock"], "lease_expiry": iso(NOW + HOUR / 2 + 2 * HOUR)}
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert leftovers(lock_path) == []
    assert "renewed" not in first


def test_a_renewal_by_another_holder_refuses_naming_the_locks_holder(controller, lock_path):
    first = take(lock_path, holder="upgrade")

    result = take(lock_path, token=first["token"])

    assert result == {"failed": True, "msg": refusal(lock_path, first["lock"])}
    assert held(lock_path) == first["lock"]


def test_a_renewal_rereads_the_lock_under_the_guard(controller, lock_path, monkeypatch):
    """A lock replaced between the read and the guard is no longer this run's: the renewal refuses, naming it."""
    first = take(lock_path)
    flock = fcntl.flock
    replaced = []

    def replacing_flock(fd, operation):
        if not replaced:
            replaced.append(other_lock(lock_path, token="e" * 32, controller=OTHER_HOST, lease_expiry=NOW + HOUR))
        flock(fd, operation)

    monkeypatch.setattr(replication_restore_lock.fcntl, "flock", replacing_flock)

    result = take(lock_path, token=first["token"])

    assert result == {"failed": True, "msg": lost(lock_path, replaced[0])}
    assert held(lock_path) == replaced[0]
    assert leftovers(lock_path) == []


def test_a_renewal_whose_lock_is_gone_refuses_and_takes_none(controller, lock_path):
    """As after an operator removed the lock by hand while this run held it: the run no longer holds the edge alone."""
    first = take(lock_path)
    lock_path.unlink()

    result = take(lock_path, token=first["token"])

    assert result == {
        "failed": True,
        "msg": f"Could not renew {lock_path}: this run's lock is gone, removed while the run held it. Run again once no other run on edge01 is under way.",
    }
    assert not lock_path.exists()
    assert leftovers(lock_path) == []


def test_a_renewal_whose_lock_is_gone_under_the_guard_refuses(controller, lock_path, monkeypatch):
    first = take(lock_path)
    flock = fcntl.flock

    def removing_flock(fd, operation):
        if lock_path.exists():
            lock_path.unlink()
        flock(fd, operation)

    monkeypatch.setattr(replication_restore_lock.fcntl, "flock", removing_flock)

    result = take(lock_path, token=first["token"])

    assert result["failed"] is True
    assert result["msg"].startswith(f"Could not renew {lock_path}: this run's lock is gone")
    assert not lock_path.exists()
    assert leftovers(lock_path) == []


@pytest.mark.parametrize(("lease_expiry", "live"), [(NOW + HOUR, True), (NOW - 1, False)], ids=["within-its-lease", "lease-ran-out"])
def test_a_renewal_that_finds_another_runs_lock_refuses_and_keeps_it(controller, lock_path, lease_expiry, live):
    """As after another host took the lock over once this run's lease ran out: even an expired lock is not taken over."""
    first = take(lock_path)
    lock = other_lock(lock_path, controller=OTHER_HOST, lease_expiry=lease_expiry)

    result = take(lock_path, token=first["token"])

    assert result == {"failed": True, "msg": lost(lock_path, lock)}
    assert held(lock_path) == lock
    assert leftovers(lock_path) == []


def test_the_renewals_refusal_names_the_lock_it_found(controller, lock_path):
    first = take(lock_path)
    other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW + HOUR, holder="upgrade")

    result = take(lock_path, token=first["token"])

    assert result["msg"] == (
        f"Could not renew {lock_path}, which now shows an upgrade of edge01 under way since 2027-01-15T06:00:00.000Z, by "
        "ansible-playbook process 1717 on builder02, with a lease until 2027-01-15T09:00:00.000Z. Run again once it ends or "
        "its lease runs out."
    )


def test_check_mode_reports_a_renewal_and_writes_nothing(controller, lock_path):
    first = take(lock_path, lease=HOUR)

    result = take(lock_path, lease=2 * HOUR, token=first["token"], check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert (result["token"], result["took_over"]) == (first["token"], False)
    assert held(lock_path) == first["lock"]


def test_check_mode_refuses_a_renewal_whose_lock_is_gone(controller, lock_path):
    first = take(lock_path)
    lock_path.unlink()

    result = take(lock_path, token=first["token"], check_mode=True)

    assert result["failed"] is True
    assert result["msg"].startswith(f"Could not renew {lock_path}: this run's lock is gone")


def test_the_removal_keeps_a_lock_holding_another_token(controller, lock_path):
    token = take(lock_path)["token"]

    kept = remove(lock_path, "d" * 32)

    assert (kept["changed"], kept["removed"]) == (False, False)
    assert held(lock_path)["token"] == token


def test_the_removal_removes_a_lock_holding_this_runs_token(controller, lock_path):
    token = take(lock_path)["token"]

    removed = remove(lock_path, token)

    assert (removed["changed"], removed["removed"]) == (False, True)
    assert not lock_path.exists()


def test_a_token_in_the_host_fact_renews_nothing(controller, lock_path):
    """As from a fact cache that kept another run's token: only the token option renews."""
    first = take(lock_path)

    result = run({"path": str(lock_path), "lease": HOUR}, task_vars={"replication_restore_lock_token": first["token"]})

    assert result == {"failed": True, "msg": refusal(lock_path, first["lock"])}


def test_a_lock_holding_this_token_from_another_live_process_refuses(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path)

    result = take(lock_path, token=lock["token"])

    assert result == {"failed": True, "msg": lost(lock_path, lock)}
    assert held(lock_path) == lock


def test_a_lock_holding_this_token_from_a_process_that_is_gone_refuses_the_renewal(controller, lock_path):
    """Only a take without a token takes a dead run's lock over; a renewal never takes a new lock."""
    lock = other_lock(lock_path)

    result = take(lock_path, token=lock["token"])

    assert result == {"failed": True, "msg": lost(lock_path, lock)}
    assert held(lock_path) == lock


def test_the_removal_keeps_a_lock_holding_this_token_from_another_process(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path)

    assert remove(lock_path, lock["token"])["removed"] is False
    assert held(lock_path) == lock


def test_the_removal_needs_a_token(controller, lock_path):
    result = run({"path": str(lock_path), "state": "absent"})

    assert result["failed"] is True
    assert "token" in result["msg"]


def test_the_refusal_names_the_host_option(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path)

    result = run({"path": str(lock_path), "lease": HOUR, "host": "edge02"})

    assert result == {"failed": True, "msg": refusal(lock_path, lock, host="edge02")}


def test_a_run_that_found_another_runs_lock_never_removes_it(controller, lock_path):
    controller.processes[1717] = OTHER_TICKS
    lock = other_lock(lock_path)
    assert take(lock_path)["failed"] is True

    assert remove(lock_path, "")["removed"] is False
    assert held(lock_path) == lock


def test_the_removal_rereads_the_lock_under_the_guard(controller, lock_path, monkeypatch):
    """A lock taken over between the removal's read and its unlink is the new holder's, and is kept."""
    token = take(lock_path)["token"]
    flock = fcntl.flock
    replaced = []

    def replacing_flock(fd, operation):
        if not replaced:
            replaced.append(other_lock(lock_path, token="e" * 32, controller=OTHER_HOST, lease_expiry=NOW + HOUR))
        flock(fd, operation)

    monkeypatch.setattr(replication_restore_lock.fcntl, "flock", replacing_flock)

    result = remove(lock_path, token)

    assert result["removed"] is False
    assert held(lock_path) == replaced[0]


def test_check_mode_creates_nothing(controller, lock_path):
    result = take(lock_path, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert (result["changed"], result["took_over"]) == (False, False)
    assert not lock_path.parent.exists()


def test_check_mode_reports_a_takeover_and_changes_nothing(controller, lock_path):
    lock = other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW - 1)

    result = take(lock_path, check_mode=True)

    assert (result["took_over"], "warnings" in result) == (True, False)
    assert held(lock_path) == lock
    assert leftovers(lock_path) == []


def test_check_mode_reports_a_refusal_and_changes_nothing(controller, lock_path):
    lock = other_lock(lock_path, controller=OTHER_HOST, lease_expiry=NOW + 1)

    assert take(lock_path, check_mode=True) == {"failed": True, "msg": refusal(lock_path, lock)}
    assert held(lock_path) == lock
    assert leftovers(lock_path) == []


def test_check_mode_keeps_this_runs_lock(controller, lock_path):
    token = take(lock_path)["token"]

    assert remove(lock_path, token, check_mode=True)["removed"] is True
    assert held(lock_path)["token"] == token


@pytest.mark.parametrize("lease", [0, -5])
def test_a_lease_below_one_second_refuses_and_creates_nothing(controller, lock_path, lease):
    """A lease of 0 would also let the leftover sweep remove another run's temporary between its write and its link."""
    result = take(lock_path, lease=lease)

    assert result == {"failed": True, "msg": f"lease must be at least 1 second, not {lease}."}
    assert not lock_path.parent.exists()


@pytest.mark.parametrize("renewing", [False, True], ids=["take", "renewal"])
def test_a_write_that_fails_partway_leaves_no_temporary(controller, lock_path, monkeypatch, renewing):
    token = take(lock_path)["token"] if renewing else None
    lock = held(lock_path) if renewing else None

    def partial_dump(obj, f, **kwargs):
        f.write('{"format": 1, ')
        f.flush()
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(replication_restore_lock.json, "dump", partial_dump)

    result = take(lock_path, token=token)

    assert result == {"failed": True, "msg": f"Could not take {lock_path}: [Errno 28] No space left on device"}
    assert leftovers(lock_path) == []
    assert (held(lock_path) if renewing else lock_path.exists()) == (lock if renewing else False)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_removal_that_cannot_write_the_directory_refuses_naming_the_lock(controller, lock_path):
    token = take(lock_path)["token"]
    os.chmod(lock_path.parent, 0o500)
    try:
        result = remove(lock_path, token)
    finally:
        os.chmod(lock_path.parent, 0o700)

    assert result == {"failed": True, "msg": f"Could not remove {lock_path}: [Errno 13] Permission denied: '{lock_path}.guard'"}
    assert held(lock_path)["token"] == token


def test_a_guard_held_past_the_wait_refuses(controller, lock_path, monkeypatch):
    """As when a run is stopped, by Ctrl-Z or a hung NFS client, while it holds the guard."""
    token = take(lock_path)["token"]
    guard = os.open(f"{lock_path}.guard", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(guard, fcntl.LOCK_EX)
    monkeypatch.setattr(replication_restore_lock, "GUARD_WAIT", 0.2)
    try:
        result = take(lock_path, token=token)
    finally:
        os.close(guard)

    assert result == {
        "failed": True,
        "msg": (
            f"{lock_path}.guard stayed locked by another process for 0.2 seconds, as by a run stopped while it decided on the "
            "lock. Run again once that run resumes or ends."
        ),
    }
    assert held(lock_path)["token"] == token


def test_the_take_needs_a_lease(controller, lock_path):
    result = run({"path": str(lock_path)})

    assert result["failed"] is True
    assert "lease" in result["msg"]


def write_proc(proc, pid, start_ticks, name="ansible-playbook"):
    (proc / str(pid)).mkdir(parents=True)
    fields = ["S", "1", "1", "1", "0", "-1", "4194560"] + ["0"] * 12 + [str(start_ticks), "0"]
    (proc / str(pid) / "stat").write_text(f"{pid} ({name}) {' '.join(fields)}\n", encoding="utf-8")


def write_boot(proc, btime):
    (proc / "stat").write_text(f"cpu  1 2 3\nbtime {btime}\nprocesses 7\n", encoding="utf-8")


def test_the_controller_reads_its_boot_and_a_processs_start_from_proc(tmp_path):
    proc = tmp_path / "proc"
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text(f"{BOOT_ID}\n", encoding="utf-8")
    (proc / "self" / "ns").mkdir(parents=True)
    os.symlink(PID_NAMESPACE, proc / "self" / "ns" / "pid")
    write_boot(proc, 1800000000)
    hz = os.sysconf("SC_CLK_TCK")
    write_proc(proc, 1717, 30 * hz, name="ansible-playbook (x) y")
    reader = Controller(proc=str(proc), host=HOST)

    assert reader.has_proc() is True
    assert reader.boot_id() == BOOT_ID
    assert reader.pid_namespace() == PID_NAMESPACE
    assert reader.start_ticks(1717) == 30 * hz
    assert reader.start_time(1717) == iso(NOW + 30)
    assert (reader.start_ticks(1718), reader.start_time(1718)) == (None, None)


def test_a_clock_step_moves_a_processs_start_time_but_not_its_start_ticks(tmp_path):
    """The kernel's btime is the wall-clock time of boot, so a step of the clock moves it; the ticks since boot stay."""
    proc = tmp_path / "proc"
    proc.mkdir()
    write_boot(proc, 1800000000)
    write_proc(proc, 1717, 3000)
    reader = Controller(proc=str(proc), host=HOST)
    before = (reader.start_ticks(1717), reader.start_time(1717))

    write_boot(proc, 1800000002)

    assert reader.start_ticks(1717) == before[0]
    assert reader.start_time(1717) != before[1]


def test_without_proc_the_controller_reads_no_boot_and_no_start(tmp_path):
    reader = Controller(proc=str(tmp_path / "none"), host=HOST)

    assert (reader.has_proc(), reader.boot_id(), reader.pid_namespace(), reader.start_ticks(1717), reader.start_time(1717)) == (False, None, None, None, None)


def test_this_processs_start_reads_the_same_each_time():
    if not os.path.isfile("/proc/stat"):
        pytest.skip("no /proc on this host")
    reader = Controller()

    assert reader.start_ticks(os.getpid()) is not None
    assert reader.start_ticks(os.getpid()) == reader.start_ticks(os.getpid())
    assert reader.boot_id()
    assert reader.pid_namespace() == os.readlink("/proc/self/ns/pid")
    assert reader.host
