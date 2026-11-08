# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Check mode, diff, create-only, write-only and verify-after-write for every resource module.

A module's area class fills in the hooks; run() is the only place that decides whether to write.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, QumuloMaybeApplied, Route


@dataclass
class Current:
    id: str | None
    data: dict
    etag: str | None = None


class Digest(str):
    """A SECRET_READBACK value already in its comparison form, as digest() returns it."""


def digest(value):
    """The form a SECRET_READBACK value is compared in. An area may also normalize a readable secret to it."""
    if isinstance(value, Digest):
        return value
    raw = value.encode() if isinstance(value, str) else json.dumps(value, sort_keys=True).encode()
    return Digest("sha256:" + hashlib.sha256(raw).hexdigest())


def _multiset(values):
    return sorted(json.dumps(v, sort_keys=True) for v in values or [])


class Resource:
    RETURN_KEY = ""
    CREATE_ONLY: ClassVar[frozenset[str]] = frozenset()  # drift is fixed only by recreate=true
    WRITE_ONLY: ClassVar[frozenset[str]] = frozenset()  # never readable: sent on create, or always with update_password=always
    SECRET_READBACK: ClassVar[frozenset[str]] = frozenset()  # readable secrets: compared by digest, never returned or diffed
    READ_ONLY: ClassVar[frozenset[str]] = frozenset({"id"})
    UNORDERED: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.notes = []
        self.wrote = True  # update() sets it False when a re-read finds nothing left to write
        self.reread = None  # and then keeps that re-read, normalized, as the state before the task

    # Hooks

    def describe(self):
        return self.RETURN_KEY

    def fetch(self):
        """The object named by the module's stable key (never a server id), as a Current, or None."""
        raise NotImplementedError

    def normalize(self, current):
        """The server's shape converted to the module's option shape."""
        raise NotImplementedError

    def desired(self, before):
        """The option-shaped state the task asks for; options left unset keep before's value."""
        raise NotImplementedError

    def create(self, after):
        """after holds no READ_ONLY keys."""
        raise NotImplementedError

    def update(self, current, after, keys):
        raise NotImplementedError

    def delete(self, current):
        raise NotImplementedError

    def same(self, key, a, b):
        if key in self.SECRET_READBACK:
            return digest(a) == digest(b)
        if key in self.UNORDERED:
            return _multiset(a) == _multiset(b)
        return a == b

    def settle(self, after):
        """Verify after a write by reading back; returns the observed state. Asynchronous resources override this with a wait."""
        current = self.fetch()
        if current is None:
            raise QumuloError(f"{self.describe()} is missing after the write")
        observed = self.normalize(current)
        drift = sorted(k for k in after if k not in self.WRITE_ONLY | self.READ_ONLY and not self.same(k, observed.get(k), after[k]))
        if drift:
            shown = [k for k in drift if k not in self.SECRET_READBACK]
            raise QumuloError(
                f"{self.describe()} did not converge on {', '.join(drift)}",
                expected={k: after[k] for k in shown},
                observed={k: observed.get(k) for k in shown},
            )
        return observed

    def settle_absent(self):
        """Wait for a delete to finish; a synchronous delete has nothing to wait for."""

    def check_delete(self, current):
        """Refuse a delete the cluster would refuse, before it is sent and in check mode too; most deletes have nothing to check."""

    # Template

    def changes(self, before, after, creating):
        always = self.params.get("update_password") == "always"
        keys = []
        for k, v in after.items():
            if k in self.READ_ONLY:
                continue
            if k in self.WRITE_ONLY:
                if v is not None and (creating or always):
                    keys.append(k)
            elif creating or not self.same(k, before.get(k), v):
                keys.append(k)
        return sorted(keys)

    def run(self):
        check = self.module.check_mode
        current = self.fetch()
        before = self.normalize(current) if current else {}
        if self.params.get("state") == "absent":
            if current:
                self.check_delete(current)
            if current and not check:
                self._delete(current)
            return self.result(current is not None, before, {}, [])
        after = self.desired(before)
        creating = current is None
        keys = self.changes(before, after, creating)
        blocked = [] if creating else sorted(set(keys) & self.CREATE_ONLY)
        if blocked:
            self._check_recreate(blocked, after)
        if (keys or creating) and not check:
            after = self._write(current, after, keys, creating, blocked)
        if not self.wrote:
            before = self.reread
        return self.result((bool(keys) or creating) and self.wrote, before, after, keys)

    def _check_recreate(self, blocked, after):
        if not self.params.get("recreate"):
            raise QumuloError(f"{self.describe()}: {', '.join(blocked)} cannot change in place; set recreate=true to delete and recreate it")
        unknown = sorted(k for k in self.WRITE_ONLY if after.get(k) is None)
        if unknown:
            raise QumuloError(f"{self.describe()}: recreate=true needs {', '.join(unknown)}, which cannot be read back from the existing object")

    def _writable(self, after):
        return {k: v for k, v in after.items() if k not in self.READ_ONLY}

    def _write(self, current, after, keys, creating, blocked):
        """Write, then verify. A lost connection is reconciled by reading back, never by writing again."""
        if not blocked:
            return self._write_and_settle(current, after, keys, creating)
        try:
            self.delete(current)
        except QumuloMaybeApplied as lost:
            raise QumuloMaybeApplied(f"{lost}; this delete was for recreate, so the create was not attempted") from None
        try:
            self.settle_absent()
            return self._write_and_settle(None, after, keys, creating=True)
        except QumuloError as e:
            raise QumuloError(f"deleted {self.describe()} for recreate but did not recreate it: {e}", **{**e.details, "changed": True}) from None

    def _write_and_settle(self, current, after, keys, creating):
        try:
            if creating:
                self.create(self._writable(after))
            else:
                self.update(current, after, keys)
        except QumuloMaybeApplied as lost:
            return self._reconcile(after, lost, keys, creating)
        try:
            return self.settle(after)
        except QumuloError as e:
            e.details.setdefault("changed", True)
            raise

    def _reconcile(self, after, lost, keys, creating):
        # An object that exists after a lost create was made by that create; a lost update of a field
        # that cannot be read back leaves nothing to compare.
        unreadable = [] if creating else sorted(set(keys) & self.WRITE_ONLY)
        if unreadable:
            raise QumuloError(
                f"{lost}; {', '.join(unreadable)} cannot be read back, so whether the change took effect is unknown", changed=True, maybe_applied=True
            )
        try:
            observed = self.settle(after)
        except QumuloError as e:
            raise QumuloError(f"{lost}; reading back did not confirm it: {e}", changed=True, maybe_applied=True) from None
        self.notes.append("write reconciled after a lost connection")
        return observed

    def _delete(self, current):
        try:
            self.delete(current)
        except QumuloMaybeApplied as lost:
            try:
                self.settle_absent()
                gone = self.fetch() is None
            except QumuloError as e:
                raise QumuloError(f"{lost}; reading back failed: {e}", changed=True, maybe_applied=True) from None
            if not gone:
                raise QumuloError(f"{lost}; reading back shows {self.describe()} still exists", changed=True, maybe_applied=True) from None
            self.notes.append("write reconciled after a lost connection")
            return
        try:
            self.settle_absent()
        except QumuloError as e:
            e.details.setdefault("changed", True)
            raise

    def result(self, changed, before, after, keys):
        hidden = self.WRITE_ONLY | self.SECRET_READBACK

        def public(state):
            return {k: v for k, v in state.items() if k not in hidden}

        result = {"changed": changed, self.RETURN_KEY: public(after)}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": public(before), "after": public(after)}
            secret = [k for k in keys if k in hidden]
            if secret:
                result["diff"]["after_header"] = "changed, not shown: " + ", ".join(secret)
        return result


@dataclass(frozen=True)
class Field:
    """How one option of a Settings document maps to its wire key."""

    wire: str
    read: Callable[[Any], Any] | None = None  # wire value -> option value
    write: Callable[[Any], Any] | None = None  # option value -> wire value
    compare: Callable[[Any, Any], bool] | None = None  # equality, for example with a float tolerance
    unordered: bool = False


class Settings(Resource):
    """A singleton document read with GET and written with PATCH (changed keys only) or PUT (the whole document)."""

    ROUTE: ClassVar[Route]
    WRITE = "PATCH"
    FIELDS: ClassVar[dict[str, Field]] = {}
    READ_ONLY: ClassVar[frozenset[str]] = frozenset()
    route_params: dict | None = None  # ROUTE's path parameters, for a document with one per tenant

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.UNORDERED = cls.UNORDERED | frozenset(name for name, field in cls.FIELDS.items() if field.unordered)

    def fetch(self):
        self._response = self.client.get(self.ROUTE)
        return Current(None, self._response.data, self._response.etag)

    @classmethod
    def options(cls, data):
        """A document the server returned, in the module's option shape, as the info modules and capture report it.

        It is normalize() without an instance, so a subclass that changes how it reads a document overrides this, not
        normalize() alone.
        """
        return {name: field.read(data[field.wire]) if field.read else data[field.wire] for name, field in cls.FIELDS.items() if field.wire in data}

    def normalize(self, current):
        return self.options(current.data)

    def seed(self, before):
        """The state the task's options go over: the document as read. A tenant's override the task creates has none to read."""
        return before

    def desired(self, before):
        return {**self.seed(before), **{name: self.params[name] for name in self.FIELDS if self.params.get(name) is not None}}

    def same(self, key, a, b):
        field = self.FIELDS.get(key)
        if field and field.compare and a is not None and b is not None:
            return field.compare(a, b)
        return super().same(key, a, b)

    def wire(self, after, keys):
        """The wire keys and values of keys of after, an option-shaped state."""
        return {self.FIELDS[k].wire: self.FIELDS[k].write(after[k]) if self.FIELDS[k].write else after[k] for k in keys}

    def update(self, current, after, keys):
        def build(data):
            seen = self.normalize(Current(None, data))
            changed = self.changes(seen, after, creating=False)
            if not changed:
                self.reread = seen
                return None
            wire = self.wire(after, changed)
            return wire if self.WRITE == "PATCH" else {**data, **wire}

        # After a 412, the re-read may show another writer already set every key; then nothing is written.
        self.wrote = self.client.read_modify_write(self.ROUTE, build, method=self.WRITE, params=self.route_params, first=self._response) is not None


class Action:
    """A one-shot operation: plan() says what would happen (None for nothing); perform() does it outside check mode.

    perform() owns any reconciling after a lost connection (QumuloMaybeApplied); run() does not re-read.
    """

    RETURN_KEY = "plan"

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params

    def plan(self):
        raise NotImplementedError

    def perform(self, plan):
        raise NotImplementedError

    def run(self):
        plan = self.plan()
        if plan is None:
            return {"changed": False}
        result = {"changed": True, self.RETURN_KEY: plan}
        if self.module._diff:
            result["diff"] = {"before": {}, "after": plan}
        if not self.module.check_mode:
            result.update(self.perform(plan) or {})
        return result
