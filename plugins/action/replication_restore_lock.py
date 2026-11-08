# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.replication_restore_lock, which plugins/modules/replication_restore_lock.py documents.

One restore runs per edge at a time on a controller. The lock is a JSON file, written whole to a temporary name, synced,
and linked to its own, so a crash never leaves an empty or partial lock and a link that finds a lock fails with EEXIST.
A lock from this controller, the same host name, boot and PID namespace, lives as long as the ansible-playbook
process that took it; one from another host, boot or PID namespace, or from a controller without /proc, lives until
its lease runs out. A takeover holds an exclusive flock on the guard, which the kernel drops when the process dies, so
of two runs only the one holding it decides. The run that holds the
lock renews it under the guard too, renaming its new lock over the old one, so no other run can take it meanwhile; a
renewal that finds its lock gone or another run's refuses, and never takes a new one.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import (
    FORMAT,
    LOCK,
    LOCK_GUARD,
    LOCK_TEMPORARY,
    Controller,
    is_lock,
    iso,
    liveness,
    with_article,
)
from ansible_collections.qumulo.cluster.plugins.modules.replication_restore_lock import ARGUMENT_SPEC, REQUIRED_IF

ATTEMPTS = 10  # links lost to a lock that then vanished or changed before the run could decide on it
# The guard and the temporaries are named after the lock's path, which restore_record names for its readers.
GUARD_SUFFIX = LOCK_GUARD.removeprefix(LOCK)
TEMPORARY_SUFFIX = LOCK_TEMPORARY.removeprefix(LOCK)
GUARD_WAIT = 60  # seconds to wait for the guard, which another run holds for a few calls


class RestoreLock:
    def __init__(self, path, host, controller, pid, token, check_mode=False, holder="restore", held_token=None):
        """held_token is the token of the lock this run already holds, which a take renews."""
        self.path = path
        self.directory = os.path.dirname(path)
        self.guard = f"{path}{GUARD_SUFFIX}"
        self.temporary = f"{path}{TEMPORARY_SUFFIX}{token}"
        self.host = host
        self.controller = controller
        self.pid = pid
        self.token = token
        self.held_token = held_token
        self.holder = holder
        self.check_mode = check_mode
        self.took_over = False
        self.warnings = []

    def take(self, lease):
        """The lock this run holds, taking over a dead or expired one; refuses a live one.

        With held_token, the take renews this run's lock, and refuses when the lock no longer holds it.
        """
        now = self.controller.clock()
        mine = {
            "format": FORMAT,
            "holder": self.holder,
            "token": self.token,
            "controller": self.controller.host,
            "boot_id": self.controller.boot_id(),
            "pid_namespace": self.controller.pid_namespace(),
            "pid": self.pid,
            "start_ticks": self.controller.start_ticks(self.pid),
            # Without /proc the process's start time cannot be read; the lock is under way since it was taken.
            "start_time": self.controller.start_time(self.pid) or iso(now),
            "lease_expiry": iso(now + lease),
        }
        if self.held_token:
            return self.renew({**mine, "token": self.held_token})
        if self.check_mode:
            found = self.read()
            if found is not None:
                self.judge(found, now)
                self.took_over = True
            return mine
        self.ensure_directory()
        self.remove_leftovers(now, lease)
        try:
            self.write_temporary(mine)
            for _attempt in range(ATTEMPTS):
                if self.link():
                    return mine
                found = self.read()
                if found is None:
                    continue
                if found["token"] == self.token:  # a retransmitted link on NFS answers EEXIST for one that succeeded
                    return mine
                warning = self.judge(found, now)
                if self.take_over(found, warning):
                    return mine
            raise AnsibleActionFail(f"{self.path} changed {ATTEMPTS} times while this run decided on it; run again.")
        finally:
            unlink(self.temporary)

    def renew(self, renewal):
        """Renames the renewal over this run's lock under the guard; refuses when the lock no longer holds it."""
        self.check_held(self.read())
        if self.check_mode:
            return renewal
        temporary = f"{self.path}{TEMPORARY_SUFFIX}{renewal['token']}"
        unlink(temporary)
        try:
            write_new(temporary, renewal)
            with Guard(self.guard):
                self.check_held(self.read())
                os.replace(temporary, self.path)
        finally:
            unlink(temporary)
        return renewal

    def check_held(self, found):
        """Refuses the renewal unless found is the lock this process took with held_token, for the same holder."""
        if found is None:
            raise AnsibleActionFail(
                f"Could not renew {self.path}: this run's lock is gone, removed while the run held it. Run again once no other run on {self.host} is under way."
            )
        if found["token"] != self.held_token or not self.taken_here(found):
            raise AnsibleActionFail(f"Could not renew {self.path}, which now shows {self.holding(found)}. Run again once it ends or its lease runs out.")
        if found["holder"] != self.holder:
            raise self.refusal(found)

    def taken_here(self, found):
        """Whether this ansible-playbook process took found; without /proc, its host name and process id alone tell."""
        controller = self.controller
        process = (controller.host, controller.boot_id(), controller.pid_namespace(), self.pid, controller.start_ticks(self.pid))
        return (found["controller"], found.get("boot_id"), found.get("pid_namespace"), found["pid"], found.get("start_ticks")) == process

    def holding(self, found):
        """found's holder, process and lease, as the refusals name them."""
        return (
            f"{with_article(found['holder'])} of {self.host} under way since {found['start_time']}, by ansible-playbook process "
            f"{found['pid']} on {found['controller']}, with a lease until {found['lease_expiry']}"
        )

    def refusal(self, found):
        return AnsibleActionFail(
            f"{self.path} shows {self.holding(found)}. Run again once it ends or its lease runs out; remove the file only if that process is gone."
        )

    def judge(self, found, now):
        """The takeover's warning for a lock that may be taken over; refuses one that may not."""
        judged = liveness(found, self.controller, now)
        if judged == "gone":
            return (
                f"Took over {self.path}, left by ansible-playbook process {found['pid']} on {found['controller']}, "
                f"which started at {found['start_time']} and is gone."
            )
        if judged == "expired":
            return (
                f"Took over {self.path}, left by ansible-playbook process {found['pid']} on {found['controller']}, "
                f"whose lease ran out at {found['lease_expiry']}."
            )
        raise self.refusal(found)

    def take_over(self, judged, warning):
        """Replaces the judged lock under the guard; False when it changed meanwhile, to be decided again."""
        with Guard(self.guard):
            found = self.read()
            if found is None or found["token"] != judged["token"]:
                return False
            os.unlink(self.path)
            if not self.link():
                return False
        self.took_over = True
        self.warnings.append(warning)
        return True

    def remove(self):
        """Removes the lock when this process took it with this run's token, read again under the guard; whether it did."""
        if not self.mine(self.read()):
            return False
        if self.check_mode:
            return True
        with Guard(self.guard):
            if not self.mine(self.read()):
                return False
            os.unlink(self.path)
        return True

    def mine(self, found):
        return found is not None and bool(self.token) and found["token"] == self.token and self.taken_here(found)

    def ensure_directory(self):
        """Creates each missing directory down to the lock's with mode 0700; never changes an existing one's mode."""
        missing = []
        directory = self.directory
        while directory and not os.path.isdir(directory):
            missing.append(directory)
            directory = os.path.dirname(directory)
        for directory in reversed(missing):
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                continue
            os.chmod(directory, 0o700)

    def remove_leftovers(self, now, lease):
        """Removes the temporaries of runs that crashed between their write and their unlink, once older than lease."""
        prefix = f"{os.path.basename(self.path)}{TEMPORARY_SUFFIX}"
        for name in os.listdir(self.directory):
            path = os.path.join(self.directory, name)
            try:
                if name.startswith(prefix) and now - os.stat(path).st_mtime > lease:
                    os.unlink(path)
            except FileNotFoundError:
                continue

    def write_temporary(self, lock):
        write_new(self.temporary, lock)

    def link(self):
        """Links the temporary to the lock's name; False when a lock is there."""
        try:
            os.link(self.temporary, self.path)
        except FileExistsError:
            return False
        return True

    def read(self):
        """The lock at path, or None when there is none."""
        try:
            with open(self.path, encoding="utf-8") as f:
                found = json.load(f)
        except FileNotFoundError:
            return None
        except ValueError:
            # The parser's own words, such as "Expecting value", tell the operator nothing; the refusal says what to do.
            raise AnsibleActionFail(
                f"Could not read {self.path}: it is empty or holds no whole JSON document, as a crash of the controller that wrote it can "
                f"leave it. Confirm that no run on {self.host} is under way, then remove the file by hand."
            ) from None
        except OSError as e:
            raise AnsibleActionFail(f"Could not read {self.path}: {e}") from e
        if not is_lock(found):
            raise AnsibleActionFail(f"{self.path} is not a lock of format {FORMAT}. Remove it only if no run on {self.host} holds it.")
        return found


class Guard:
    """An exclusive flock on the guard file while the with block runs; the kernel drops it if the process dies."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + GUARD_WAIT
        try:
            while not self.try_lock():
                if time.monotonic() >= deadline:
                    raise AnsibleActionFail(
                        f"{self.path} stayed locked by another process for {GUARD_WAIT} seconds, as by a run stopped while it "
                        "decided on the lock. Run again once that run resumes or ends."
                    )
                time.sleep(0.1)
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def try_lock(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def __exit__(self, *exc):
        os.close(self.fd)


def write_new(path, lock):
    """Writes lock to a new file at path and syncs it, so a link or a rename to it never reaches the disk before it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(lock, f, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def unlink(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class ActionModule(ActionBase):
    TRANSFERS_FILES = False
    _VALID_ARGS = frozenset(ARGUMENT_SPEC)
    controller = Controller()

    def run(self, tmp=None, task_vars=None):
        task_vars = task_vars or {}
        result = super().run(tmp, task_vars)
        del tmp
        args = self.validate_argument_spec(argument_spec=ARGUMENT_SPEC, required_if=REQUIRED_IF)[1]
        path, state = args["path"], args["state"]
        host = args["host"] or task_vars.get("inventory_hostname", "")
        # The worker that runs this plugin is a fork of the ansible-playbook process.
        pid = os.getppid()
        if state == "present":
            if args["lease"] < 1:
                raise AnsibleActionFail(f"lease must be at least 1 second, not {args['lease']}.")
            lock = RestoreLock(path, host, self.controller, pid, secrets.token_hex(16), self._task.check_mode, args["holder"], args["token"])
            try:
                written = lock.take(args["lease"])
            except OSError as e:
                raise AnsibleActionFail(f"Could not take {path}: {e}") from e
            result.update(lock=written, token=written["token"], took_over=lock.took_over)
        else:
            lock = RestoreLock(path, host, self.controller, pid, args["token"], self._task.check_mode)
            try:
                result.update(removed=lock.remove())
            except OSError as e:
                raise AnsibleActionFail(f"Could not remove {path}: {e}") from e
        result.update(changed=False, path=path)
        if lock.warnings:
            result["warnings"] = lock.warnings
        return result
