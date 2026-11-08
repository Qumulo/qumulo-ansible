# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The file system key store: the public keys that lock snapshots (spec 022)."""

from __future__ import annotations

import re
from contextlib import contextmanager

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Resource
from ansible_collections.qumulo.cluster.plugins.module_utils.signing import Signer, bare_public_key

KEYS = Route("/v1/file-system/security/keys/", ("GET", "POST"))
KEY = Route("/v1/file-system/security/keys/{ref}", ("GET", "PATCH", "DELETE"))  # ref: a key's name or id
USAGES = Route("/v1/file-system/security/keys/{ref}/usages", ("GET",))
# The same until the key changes in any way; the replace takes the read's ETag, so a stale challenge is never sent.
CHALLENGE = Route("/v1/file-system/security/keys/{ref}/key-replacement-challenge", ("GET",))
REPLACE = Route("/v1/file-system/security/keys/{ref}/replace", ("POST",))

# The usages that keep the cluster from disabling a key. A snapshot lock does not: a disabled key still unlocks it.
BLOCKS_DISABLE = frozenset({"snapshot_policy", "replication_target"})
# A key_ref of digits only names a key by its id, so the cluster refuses such a name.
DIGITS = re.compile(r"[0-9]+")
# What frees each kind of usage; the collection sets no lock key on a replication target, so qq does.
CLEAR_LOCK_KEY = {
    "snapshot": "unlock each snapshot with qumulo.cluster.snapshot_lock",
    "snapshot_policy": 'take the key off each policy with lock_key: "" on qumulo.cluster.snapshot_policy',
    "replication_target": ("take the key off each replication target with qq replication_set_target_relationship_lock --relationship-id <id> --clear-lock-key"),
}
NAMED_USAGES = 10  # a refusal names this many usages, then counts the rest: a policy can give a key hundreds


def key_options(data):
    """A key as the cluster returns it, in the option shape of security_key, with its id."""
    return {"id": data["id"], "name": data["name"], "comment": data["comment"], "enabled": not data["disabled"], "public_key": data["public_key"]}


def _usage_order(entry):
    ident = entry["id"]
    return (entry["type"], 0, int(ident), "") if ident.isdigit() else (entry["type"], 1, 0, ident)


def usages(client, key_id):
    """What uses the key, each {type, id}, sorted by type and then id; the cluster's order differs between releases.

    Read by the key's id: its name may be empty, which no key_ref names (QUIRK-120).
    """
    found = [{"type": entry["type"], "id": entry["id"]} for entry in client.iterate(USAGES, "usages", params={"ref": key_id})]
    return sorted(found, key=_usage_order)


def describe_usages(entries):
    named = ", ".join(f"{entry['type']} {entry['id']}" for entry in entries[:NAMED_USAGES])
    more = len(entries) - NAMED_USAGES
    return f"{named} and {more} more" if more > 0 else named


@contextmanager
def _readable():
    """A 403 on a key store read is SubsetUnavailable: the list and the usages need PRIVILEGE_FS_KEY_MANAGEMENT_READ, and
    snapshot_info's all must not fail for an API user who held only the snapshot privileges before this subset."""
    try:
        yield
    except QumuloApiError as e:
        if e.status == 403:
            raise SubsetUnavailable(f"the API user may not read the key store: {e.description}") from None
        raise


def list_keys(client):
    """Every key as the cluster returns it; SubsetUnavailable when the API user may not read the key store."""
    with _readable():
        return client.get(KEYS).data["entries"]


def key_facts(client):
    """snapshot_info's security_keys subset: each key with what uses it, leaving out a key deleted while it is read."""
    facts = []
    for key in list_keys(client):
        try:
            with _readable():
                facts.append({**key_options(key), "usages": usages(client, key["id"])})
        except QumuloApiError as e:
            if e.status != 404 or e.is_("http_not_found_error"):
                raise
    # Sorted, as the usages are: the edge rebuild's record holds this list.
    return {"security_keys": sorted(facts, key=lambda key: (key["name"], key["id"]))}


class SecurityKey(Resource):
    """A key by its name. A different public key is replaced through the challenge, never by delete and create, so that
    what the key locks moves to the new key."""

    RETURN_KEY = "security_key"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.name = self.params["name"]
        if self.name == "" or DIGITS.fullmatch(self.name):
            # The cluster stores an empty name, which no key_ref can name afterwards (QUIRK-120), and refuses one of
            # digits only.
            raise QumuloError(f"name {self.name!r} is not a key name: give one that is neither empty nor digits only")
        self.private_key = self.params.get("private_key")
        self.old_private_key = self.params.get("old_private_key")
        present = self.params["state"] == "present"
        # Removal signs nothing, so a task that passes the same private key to absent needs no openssl.
        self.signer = Signer(module, "private_key") if present and (self.private_key or self.old_private_key) else None
        self.declared_key = self._declared_key() if present else None

    def describe(self):
        return f"key {self.name}"

    def _declared_key(self):
        """The public key the task declares, as base64 DER, or None for the key's own; checked against private_key."""
        given = self.params.get("public_key")
        derived = self.signer.public_key(self.private_key) if self.private_key else None
        if given is not None and derived is not None and bare_public_key(given) != derived:
            raise QumuloError("public_key and private_key do not match: public_key is not the public half of private_key")
        if derived is not None:
            return derived
        return None if given is None else bare_public_key(given)

    def fetch(self):
        found = self.client.get_or_none(KEY, params={"ref": self.name})
        if found is None:
            return None
        if found.data["name"] != self.name:
            # A key_ref the cluster reads as an id names another key; never change or remove that one.
            raise QumuloError(f"name {self.name!r} named key {found.data['id']}, {found.data['name']!r}, not a key of that name")
        return Current(found.data["id"], found.data, found.etag)

    def normalize(self, current):
        return key_options(current.data)

    def desired(self, before):
        """Refusals are raised here, before any write, so that check mode reports them too."""
        after = {} if not before else {"id": before["id"]}
        after["name"] = self.name
        after["comment"] = self.params["comment"] if self.params.get("comment") is not None else before.get("comment", "")
        after["enabled"] = self.params["enabled"] if self.params.get("enabled") is not None else before.get("enabled", True)
        after["public_key"] = self.declared_key or before.get("public_key")
        if not before:
            self._check_create(after)
            return after
        if after["public_key"] != before["public_key"]:
            self._check_replace(before)
        if before["enabled"] and not after["enabled"]:
            blocking = [entry for entry in usages(self.client, before["id"]) if entry["type"] in BLOCKS_DISABLE]
            if blocking:
                raise QumuloError(f"{self.describe()} cannot be disabled while it is used by {describe_usages(blocking)}, which the cluster refuses")
        return after

    def _check_create(self, after):
        if after["public_key"] is None:
            raise QumuloError(f"{self.describe()} is not in the key store; to register it, give public_key or private_key")
        if not self.private_key and not self.params.get("verification_signature"):
            raise QumuloError(
                f"{self.describe()} is not in the key store, and the cluster registers a key only with its name signed by its "
                "private key; give private_key, with which the module signs the name, or verification_signature, the "
                "name signed elsewhere"
            )
        if self.old_private_key:
            # A note, not a refusal: replayed onto a rebuilt cluster, a rotation task must register the key.
            self.notes.append(f"old_private_key was not used: no key {self.name} was in the key store, so the module registers it")

    def _check_replace(self, before):
        if not self.old_private_key:
            raise QumuloError(
                f"{self.describe()} holds another public key, and the cluster replaces a key only with the replacement challenge "
                "signed by both private keys; give old_private_key, the private key of the public key it holds, and "
                "private_key, the new key's"
            )
        if self.signer.public_key(self.old_private_key) != before["public_key"]:
            raise QumuloError(f"old_private_key does not match the public key of {self.name} in the key store")

    def check_delete(self, current):
        used = usages(self.client, current.id)
        if used:
            steps = [CLEAR_LOCK_KEY[kind] for kind in CLEAR_LOCK_KEY if any(entry["type"] == kind for entry in used)]
            raise QumuloError(f"{self.describe()} cannot be removed while it is used by {describe_usages(used)}, which the cluster refuses; {'; '.join(steps)}")

    def create(self, after):
        signature = self.params.get("verification_signature") or self.signer.sign(self.private_key, after["name"])
        body = {"name": after["name"], "public_key": after["public_key"], "comment": after["comment"], "verification_signature": signature}
        if after["enabled"]:
            self.client.post(KEYS, body=body)
            return
        # The create takes no disabled field, and its answer carries no ETag for the PATCH that disables the key.
        try:
            key_id = self.client.post(KEYS, body=body).data["id"]
        except QumuloMaybeApplied as lost:
            key_id, etag = self._first_write_took(after, lost, "create")
        else:
            etag = None
        with self._after("registered the key"):
            if etag is None:
                etag = self.client.get(KEY, params={"ref": key_id}).etag
            self.client.patch(KEY, params={"ref": key_id}, body={"disabled": True}, if_match=etag)

    def update(self, current, after, keys):
        patch = {}
        if "comment" in keys:
            patch["comment"] = after["comment"]
        if "enabled" in keys:
            patch["disabled"] = not after["enabled"]
        etag = current.etag
        if "public_key" in keys:
            if not patch:
                self._replace(current, after)
                return
            try:
                self._replace(current, after)
            except QumuloMaybeApplied as lost:
                _key_id, etag = self._first_write_took(after, lost, "replace")
            else:
                etag = None  # the replace moved the key's ETag, and its answer carries none for the PATCH
            with self._after("replaced the key's public key"):
                if etag is None:
                    etag = self.client.get(KEY, params={"ref": current.id}).etag
                self.client.patch(KEY, params={"ref": current.id}, body=patch, if_match=etag)
            return
        self.client.patch(KEY, params={"ref": current.id}, body=patch, if_match=etag)

    @contextmanager
    def _after(self, done):
        """A request that fails after the first of two writes took still leaves the key changed, as polling.Waits.after_writes
        says; a lost one is settled by Resource reading back."""
        try:
            yield
        except QumuloMaybeApplied:
            raise
        except QumuloError as e:
            raise QumuloError(f"{e}; this task had already {done}", **{**e.details, "changed": True}) from None

    def _first_write_took(self, after, lost, write):
        """The key's id and ETag when the first of two writes, whose answer was lost, took; otherwise lost, re-raised for
        Resource to settle by reading back. The second write was never sent, so sending it now is no resend."""
        try:
            found = self.fetch()
        except QumuloError as e:
            raise QumuloError(f"{lost}; reading back failed: {e}", changed=True, maybe_applied=True) from None
        if found is None or found.data["public_key"] != after["public_key"]:
            raise lost
        self.notes.append(f"the {write} was confirmed by reading back after a lost connection")
        return found.id, found.etag

    def _replace(self, current, after):
        challenge = self.client.get(CHALLENGE, params={"ref": current.id}).data["challenge"]
        body = {
            "replacement_key": after["public_key"],
            "old_key_verification_signature": self.signer.sign(self.old_private_key, challenge),
            "replacement_key_verification_signature": self.signer.sign(self.private_key, challenge),
        }
        self.client.post(REPLACE, params={"ref": current.id}, body=body, if_match=current.etag)

    def delete(self, current):
        self.client.delete(KEY, params={"ref": current.id})
