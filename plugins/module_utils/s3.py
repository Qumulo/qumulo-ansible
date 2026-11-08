# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The s3 area: the S3 server settings, buckets, bucket policies and access keys.

No S3 route returns an ETag, so every write follows a diff and the last writer wins. The S3 routes answer a missing
directory, bucket or key with HTTP 404 and http_not_found_error, the class of a route the release lacks (QUIRK-051), so
the modules check a directory they name, and list buckets and keys, before any request that names one: a 404 on a read
or a create then means a missing route, and a DELETE's 404 right after a listing is another client's delete.
"""

from __future__ import annotations

import os
import re
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils import client as qumulo_client
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import SPOKES, get_offered
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.files import attributes, canonical_path, inside, require_directory
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import FIND, INTERNAL_NAMES, IdentityResolver, Pending, parse_trustee, render
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import path_of
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Field, Resource, Settings
from ansible_collections.qumulo.cluster.plugins.module_utils.secret_files import secret_directory, secret_file_name, write_secret_file

NO_ETAG = "the S3 routes return no ETag, so writes follow a diff and the last writer wins"
SETTINGS = Route("/v1/s3/settings", ("GET", "PATCH"), etag="omit", why=NO_ETAG)
BUCKETS = Route("/v1/s3/buckets/", ("GET", "POST"))
BUCKET = Route("/v1/s3/buckets/{name}", ("PATCH", "DELETE"), etag="omit", why=NO_ETAG)
UPLOADS = Route("/v1/s3/buckets/{name}/uploads/", ("GET",))
UPLOAD = Route("/v1/s3/buckets/{name}/uploads/{id}", ("DELETE",))
POLICY = Route("/v1/s3/buckets/{name}/policy", ("GET", "PUT"), etag="omit", why=NO_ETAG)
ACCESS_KEYS = Route("/v1/s3/access-keys/", ("GET", "POST"))
ACCESS_KEY = Route("/v1/s3/access-keys/{id}", ("DELETE",))

UNVERSIONED, ENABLED = "Unversioned", "Enabled"

NEVER = "never"
# The units the cluster reads an interval back in, largest first, in hours; a month is four weeks (QUIRK-050).
UNITS = {"months": 672, "weeks": 168, "days": 24, "hours": 1}
INTERVAL = re.compile(r"^(?P<count>[1-9][0-9]?)(?P<unit>months|weeks|days|hours)$")


def canonical_interval(value):
    """A multipart upload expiry interval in the form the cluster reads it back, refusing one it would read back shorter."""
    if value in ("", NEVER):
        return NEVER
    match = INTERVAL.match(value)
    if not match:
        raise QumuloError(
            f"multipart_upload_expiry_interval '{value}' must be never, or a number from 1 to 99 followed by hours, days, weeks or months, such as 1days"
        )
    hours = int(match["count"]) * UNITS[match["unit"]]
    # The cluster reads it back in the largest unit it fits, rounding down.
    unit, size = next((unit, size) for unit, size in UNITS.items() if hours >= size)
    shown = f"{hours // size}{unit}"
    if hours % size:
        raise QumuloError(
            f"multipart_upload_expiry_interval '{value}' reads back as {shown}, a shorter interval, so the module could never tell "
            f"whether it is set (QUIRK-050); give a whole number of the largest unit it fits, such as {shown}"
        )
    return shown


def check_directory(client, check_mode, notes, path, option):
    """Fail unless path is a directory; in check mode a missing one is only noted, since an earlier task may create it."""
    found = attributes(client, path)
    if found is not None:
        require_directory(path, found.data)
    elif check_mode:
        notes.append(f"{option} {path} does not exist yet; an earlier task must create it before this one runs")
    else:
        raise QumuloError(f"{option} {path} does not exist; create it first, for example with qumulo.cluster.directory")


class S3Settings(Settings):
    RETURN_KEY = "s3_settings"
    ROUTE = SETTINGS
    FIELDS: ClassVar[dict[str, Field]] = {
        "enabled": Field("enabled"),
        "secure": Field("secure"),
        "base_path": Field("base_path", read=canonical_path),
        "multipart_upload_expiry_interval": Field("multipart_upload_expiry_interval"),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        self.wanted = {}
        if self.params.get("base_path") is not None:
            self.wanted["base_path"] = canonical_path(self.params["base_path"], "base_path")
        if self.params.get("multipart_upload_expiry_interval") is not None:
            self.wanted["multipart_upload_expiry_interval"] = canonical_interval(self.params["multipart_upload_expiry_interval"])

    def describe(self):
        return "the S3 server settings"

    def desired(self, before):
        after = {**super().desired(before), **self.wanted}
        if after.get("base_path") != before.get("base_path"):
            check_directory(self.client, self.module.check_mode, self.notes, after["base_path"], "base_path")
        return after


def bucket_options(data):
    """A bucket as the cluster lists it, in the option shape of s3_bucket."""
    lock = data.get("lock_config") or {}
    retention = lock.get("default_retention")
    return {
        "name": data["name"],
        "path": canonical_path(data["path"], "path"),
        "versioning": data["versioning"],
        "object_lock": bool(lock.get("enabled")),
        "default_retention": {"value": retention["value"], "units": retention["units"]} if retention else None,
    }


def list_buckets(client):
    """Every bucket; the cluster has no read of one bucket."""
    return client.get(BUCKETS).data["buckets"]


def spoke_roots(client):
    """The paths of this cluster's spoke roots; none on a cluster that offers no portals."""
    try:
        spokes = get_offered(client, SPOKES)["entries"]
    except SubsetUnavailable:
        return []
    return [path for spoke in spokes for path in (path_of(client, root["local_root"]) for root in spoke["roots"]) if path]


def _lock_body(state):
    # QUIRK-052: a lock written without its default retention clears it, so the lock is always sent whole.
    return {"enabled": state["object_lock"], "default_retention": state["default_retention"]}


def _retention(option):
    """default_retention in its option shape: None for {}, which removes it."""
    value, units = option.get("value"), option.get("units")
    if value is None and units is None:
        return None
    if value is None or units is None:
        raise QumuloError("default_retention needs both value and units, or {} to remove it")
    if value <= 0:
        raise QumuloError(f"default_retention.value must be greater than zero, not {value}")
    return {"value": value, "units": units}


class S3Bucket(Resource):
    RETURN_KEY = "s3_bucket"
    READ_ONLY = frozenset({"name"})
    LOCK = ("object_lock", "default_retention")

    def __init__(self, module, client):
        super().__init__(module, client)
        params = self.params
        self.name = params["name"]
        wanted = {key: params[key] for key in ("versioning", "object_lock") if params.get(key) is not None}
        if params.get("path") is not None:
            wanted["path"] = canonical_path(params["path"], "path")
        if params.get("default_retention") is not None:
            wanted["default_retention"] = _retention(params["default_retention"])
        self.wanted = wanted
        self.uploads = []  # check_delete lists them

    def describe(self):
        return f"S3 bucket '{self.name}'"

    def fetch(self):
        found = next((bucket for bucket in list_buckets(self.client) if bucket["name"] == self.name), None)
        return Current(self.name, found) if found else None

    def normalize(self, current):
        return bucket_options(current.data)

    def desired(self, before):
        if before:
            after = {**before, **self.wanted}
            if after["path"] != before["path"]:
                raise QumuloError(
                    f"{self.describe()} serves {before['path']}, and a bucket's directory cannot change (G-S3-PATH); remove the bucket, "
                    f"which keeps its directory and objects, and create it again at {after['path']}"
                )
        elif "path" not in self.wanted:
            raise QumuloError(f"{self.describe()} does not exist; set path to create it")
        else:
            after = {"name": self.name, "versioning": UNVERSIONED, "object_lock": False, "default_retention": None, **self.wanted}
            if after["object_lock"] and "versioning" not in self.wanted:
                after["versioning"] = ENABLED  # as the cluster does for a bucket created with a lock
        self._check_transition(before, after)
        if not before:
            self._check_new_path(after["path"])
        return after

    def _check_transition(self, before, after):
        refusal = None
        if before.get("versioning", UNVERSIONED) != UNVERSIONED and after["versioning"] == UNVERSIONED:
            refusal = f"versioning is {before['versioning']} and can never return to {UNVERSIONED}"
        elif before.get("object_lock") and not after["object_lock"]:
            refusal = "an object lock can never be removed"
        elif after["object_lock"] and after["versioning"] != ENABLED:
            refusal = f"a locked bucket needs versioning: {ENABLED}, not {after['versioning']}"
        elif after["default_retention"] and not after["object_lock"]:
            refusal = "default_retention needs object_lock: true"
        if refusal:
            raise QumuloError(f"{self.describe()}: {refusal} (G-S3-LOCK)")

    def _check_new_path(self, path):
        root = next((root for root in spoke_roots(self.client) if inside(path, root)), None)
        if root:
            raise QumuloError(f"{self.describe()}: path {path} lies in the spoke root {root}, whose data the hub holds; serve it from the hub")
        if not self.params["create_fs_path"]:
            check_directory(self.client, self.module.check_mode, self.notes, path, "path")

    def _changes(self, before, after):
        body = {}
        if after["versioning"] != before["versioning"]:
            body["versioning"] = after["versioning"]
        if any(after[key] != before[key] for key in self.LOCK):
            body["lock_config"] = _lock_body(after)
        return body

    def create(self, after):
        body = {"name": self.name, "path": after["path"], "create_fs_path": self.params["create_fs_path"], "object_lock_enabled": after["object_lock"]}
        created = bucket_options(self.client.post(BUCKETS, body=body).data)
        remaining = self._changes(created, after)
        if remaining:
            self.client.patch(BUCKET, params={"name": self.name}, body=remaining)

    def update(self, current, after, keys):
        self.client.patch(BUCKET, params={"name": self.name}, body=self._changes(self.normalize(current), after))

    def check_delete(self, current):
        """The cluster removes no bucket with an unfinished upload, so allow_data_loss aborts them first.

        delete() aborts the uploads this lists, so it relies on this running first, as Resource.run() does.
        """
        self.uploads = self.client.paginate(UPLOADS, "uploads", params={"name": self.name})
        if self.uploads and not self.params["allow_data_loss"]:
            count = f"{len(self.uploads)} unfinished upload{'' if len(self.uploads) == 1 else 's'}"
            raise QumuloError(
                f"{self.describe()} has {count}, and the cluster removes no bucket with one; set allow_data_loss to abort them "
                "and remove the bucket (its objects stay)"
            )

    def delete(self, current):
        for upload in self.uploads:
            try:
                self.client.delete(UPLOAD, params={"name": self.name, "id": upload["id"]})
            except QumuloApiError as e:
                if e.status != 404:  # listed a moment ago, so a 404 is an upload that finished or was aborted since
                    raise
        # The directory and its objects stay: the bucket may be created there again.
        self.client.delete(BUCKET, params={"name": self.name}, query={"delete-root-dir": False})


EFFECTS = ("Allow", "Deny")
NO_POLICY = {}  # the cluster reads a bucket without a policy back as {}, and a PUT of exactly {} removes one


def _as_list(value):
    return [value] if isinstance(value, str) else list(value)


def check_policy(policy):
    """Refuse, before any request, a policy document the module cannot compare or the cluster refuses."""
    statements = policy.get("Statement")
    if not isinstance(statements, list):
        raise QumuloError("policy needs Statement, a list of statements")
    for number, statement in enumerate(statements, 1):
        where = f"policy Statement[{number}]"
        if not isinstance(statement, dict):
            raise QumuloError(f"{where} must be a dict")
        if "Resource" in statement:
            raise QumuloError(f"{where}: the cluster refuses Resource; a statement applies to its own bucket")
        if statement.get("Effect") not in EFFECTS:
            raise QumuloError(f"{where}: Effect must be Allow or Deny, not {statement.get('Effect')!r}")
        principal = statement.get("Principal")
        if not isinstance(principal, dict) or "Qumulo" not in principal:
            raise QumuloError(f"{where}: Principal must be a dict whose Qumulo key holds trustees, such as {{Qumulo: [Everyone]}}")
        for trustee in _as_list(principal["Qumulo"]):
            parse_trustee(trustee)
        if "Action" not in statement:
            raise QumuloError(f"{where} needs Action")


def read_policy(client, bucket):
    return client.get(POLICY, params={"name": bucket}).data or NO_POLICY


AUTH_ID = re.compile(r"^auth_id:(?P<auth_id>[0-9]+)$")


def read_back_identity(client, text):
    """The identity of a principal as the cluster may read one back outside the trustee grammar: auth_id:N; ad:<name>,
    found by that name in ACTIVE_DIRECTORY; File Owner or File Group Owner, found in the internal domain; or a name such as
    a bare AD name, which /v1/identity/find looks up in every identity source."""
    match = AUTH_ID.match(text)
    prefix, _colon, name = text.partition(":")
    if match:
        query = {"auth_id": match["auth_id"]}
    elif prefix == "ad" and name:
        query = {"domain": "ACTIVE_DIRECTORY", "name": name}  # as 7.11.0 reads Authenticated Users back
    elif text.casefold() in INTERNAL_NAMES:
        query = {"domain": "API_INTERNAL_DOMAIN", "name": INTERNAL_NAMES[text.casefold()]}  # as 7.11.0 reads File Owner back
    else:
        query = {"name": text}
    try:
        return client.post(FIND, body=query).data
    except QumuloApiError as e:
        raise QumuloError(f"the cluster names the policy principal '{text}', which /v1/identity/find could not resolve: {e}") from None


def as_trustee(client, text):
    """A principal the cluster read back, as a trustee of the collection's grammar."""
    try:
        parse_trustee(text)
    except QumuloError:
        return render(read_back_identity(client, text))
    return text


class S3BucketPolicy(Resource):
    RETURN_KEY = "s3_bucket_policy"
    READ_ONLY = frozenset({"bucket"})

    def __init__(self, module, client):
        super().__init__(module, client)
        self.bucket = self.params["bucket"]
        self.policy = self.params.get("policy")
        if self.params["state"] == "present":
            if self.policy is None:
                raise QumuloError("set policy, the bucket's whole policy document, or state: absent to remove it")
            check_policy(self.policy)
        self.identities = IdentityResolver(client, check_mode=module.check_mode)
        self.bucket_exists = True

    def describe(self):
        return f"the policy of S3 bucket '{self.bucket}'"

    def fetch(self):
        # QUIRK-051: the policy of a missing bucket answers like a missing route, so the bucket is found first.
        if not any(bucket["name"] == self.bucket for bucket in list_buckets(self.client)):
            self.bucket_exists = False
            return None
        policy = read_policy(self.client, self.bucket)
        return None if policy == NO_POLICY else Current(self.bucket, policy)

    def normalize(self, current):
        return {"bucket": self.bucket, "policy": current.data}

    def desired(self, before):
        if not self.bucket_exists:
            if not self.module.check_mode:
                raise QumuloError(f"S3 bucket '{self.bucket}' does not exist; create it first with qumulo.cluster.s3_bucket")
            self.notes.append(f"S3 bucket '{self.bucket}' does not exist yet; an earlier task must create it before this one runs")
        if before and self.same("policy", before["policy"], self.policy):
            return before  # the same policy; report it as the cluster reads it
        return {"bucket": self.bucket, "policy": self.policy}

    def _principal(self, trustee):
        """The identity a principal names, declared or read back, as a comparable key; None for one that exists only after
        an earlier task."""
        try:
            parse_trustee(trustee)
        except QumuloError:
            return f"auth_id:{read_back_identity(self.client, trustee)['auth_id']}"
        found = self.identities.resolve(trustee)
        return None if isinstance(found, Pending) else f"auth_id:{found['auth_id']}"

    def _canonical(self, policy):
        """policy with each statement's actions as a sorted set and its principals as sorted identities; None while one is Pending."""
        statements = []
        for statement in policy.get("Statement", []):
            canonical = dict(statement)
            if "Action" in canonical:
                canonical["Action"] = sorted(set(_as_list(canonical["Action"])))
            principal = canonical.get("Principal")
            if isinstance(principal, dict) and "Qumulo" in principal:
                keys = [self._principal(trustee) for trustee in _as_list(principal["Qumulo"])]
                if None in keys:
                    return None
                canonical["Principal"] = {**principal, "Qumulo": sorted(set(keys))}
            statements.append(canonical)
        return {**policy, "Statement": statements}

    def same(self, key, a, b):
        if key == "policy" and a is not None and b is not None:
            canonical = self._canonical(a)
            return canonical is not None and canonical == self._canonical(b)
        return super().same(key, a, b)

    def _wire(self, policy):
        """policy with each principal sent as the auth_id it resolves to, which the cluster takes for any kind of identity."""
        statements = []
        for statement in policy["Statement"]:
            names = _as_list(statement["Principal"]["Qumulo"])
            principal = {**statement["Principal"], "Qumulo": [f"auth_id:{self.identities.resolve(name)['auth_id']}" for name in names]}
            statements.append({**statement, "Principal": principal})
        return {**policy, "Statement": statements}

    def _put(self, body):
        query = {"allow-remove-self": True} if self.params["allow_remove_self"] else None
        self.client.put(POLICY, params={"name": self.bucket}, query=query, body=body)

    def create(self, after):
        self._put(self._wire(after["policy"]))

    def update(self, current, after, keys):
        self._put(self._wire(after["policy"]))

    def delete(self, current):
        self._put(NO_POLICY)


NEW_KEY = "(a new key)"  # the id check mode shows for a key the run would make


def user_keys(client, auth_id):
    """A user's keys, oldest first. QUIRK-053: the list refuses a page size with the user, so it is one request."""
    keys = client.get(ACCESS_KEYS, query={"user": f"auth_id:{auth_id}"}).data["entries"]
    return sorted(keys, key=lambda key: (key["creation_time"], key["access_key_id"]))


def all_keys(client):
    return client.paginate(ACCESS_KEYS, "entries")


def key_options(key):
    return {"user": render(key["owner"]), "access_key_id": key["access_key_id"], "creation_time": key["creation_time"]}


def write_secret(directory, user, made):
    """Write a new key's secret to a file of its own in directory, readable only by its owner, and return the file's path."""
    return write_secret_file(
        directory,
        secret_file_name("s3", user, made["access_key_id"]),
        "The secret of an S3 access key, which the cluster returns only when it makes the key.",
        (("user", user), ("access_key_id", made["access_key_id"]), ("secret_access_key", made["secret_access_key"])),
    )


class S3AccessKey:
    """A user's S3 keys. Not a Resource: a key is made, never updated, and its secret is returned once (G-S3-KEY)."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.user = self.params["user"]
        self.key_id = self.params.get("access_key_id")
        # The ids of every key the inventory declares for this user, this entry's among them.
        self.declared = set(self.params.get("declared_access_key_ids") or []) | ({self.key_id} if self.key_id else set())
        parse_trustee(self.user)
        self.identities = IdentityResolver(client, check_mode=module.check_mode)
        self.notes = []

    def run(self):
        owner = self.identities.resolve(self.user)
        pending = isinstance(owner, Pending)
        name = self.user if pending else render(owner)
        keys = [] if pending else user_keys(self.client, owner["auth_id"])
        if self.params["state"] == "absent":
            return self._remove(name, keys)
        kept = [key for key in keys if key["access_key_id"] == self.key_id] if self.key_id else keys[-1:]
        if kept:
            return self._result(False, name, keys, keys, key_options(kept[0]))
        if self.key_id:
            self._check_replacement(name, keys)
        self._check_sinks()
        if self.module.check_mode:
            if pending:
                self.notes.append(f"user {self.user} does not exist yet; an earlier task must create it, and this task then makes its key")
            elif self.key_id:
                self.notes.append(f"access key {self.key_id} of {name} does not exist, as after a rebuild; a new key would be made in its place")
            return self._result(True, name, keys, [*keys, {"access_key_id": NEW_KEY}], {"user": name, "access_key_id": None, "creation_time": None})
        made = self._make(owner, name, keys)
        qumulo_client.register_secret(made["secret_access_key"])
        secret_file = self._store(name, made)
        after = self._confirm(owner, name, keys, made, secret_file)
        if self.key_id:
            self.notes.append(
                f"access key {self.key_id} of {name} does not exist, as after a rebuild; made {made['access_key_id']} in its place, whose "
                f"secret is in {secret_file}: give it to the key's consumers, and set access_key_id to {made['access_key_id']}"
            )
        result = self._result(True, name, keys, after, key_options(made))
        result.update(secret_access_key=made["secret_access_key"], secret_file=secret_file)
        return result

    def _check_replacement(self, name, keys):
        """A declared key that is gone is made again only while the user holds fewer keys than the inventory declares for
        it, as after a rebuild; then a run before the inventory names the new keys makes no more."""
        held = {key["access_key_id"] for key in keys}
        if len(held) >= len(self.declared):
            raise QumuloError(
                f"access key {self.key_id} of {name} does not exist, and {name} holds {', '.join(sorted(held))}. A declared key is made "
                "again only while the user holds fewer keys than its entries declare, as after a rebuild. Set access_key_id to the key "
                f"its consumers hold, or remove {name}'s keys with state: absent and run again (G-S3-KEY)"
            )

    def _check_sinks(self):
        """A new key's secret is shown once, so it must have somewhere to go that prints nothing, made private before any key is."""
        if not self.module.no_log:
            raise QumuloError("set no_log: true on this task: a new key's secret is returned once, in this task's result, and never again (G-S3-KEY)")
        if not self.params.get("secret_destination"):
            raise QumuloError("set secret_destination, the directory where a new key's secret is written, since the cluster never shows it again (G-S3-KEY)")
        if not self.module.check_mode:
            secret_directory(self.params["secret_destination"], "no key was made")

    def _make(self, owner, name, keys):
        try:
            return self.client.post(ACCESS_KEYS, body={"user": {"auth_id": owner["auth_id"]}}).data
        except QumuloMaybeApplied as lost:
            known = {key["access_key_id"] for key in keys}
            orphans = [key["access_key_id"] for key in user_keys(self.client, owner["auth_id"]) if key["access_key_id"] not in known]
            if not orphans:
                raise QumuloError(f"{lost}; no key was made for {name}; run the task again") from None
            for orphan in orphans:
                self._delete(orphan)
            raise QumuloError(f"{lost}; the key was made but its secret was lost, so deleted {', '.join(orphans)}; run the task again", changed=True) from None

    def _store(self, name, made):
        key_id = made["access_key_id"]
        try:
            return write_secret(self.params["secret_destination"], name, made)
        except OSError as e:
            problem = f"could not write the secret of the new key {key_id} to {self.params['secret_destination']} ({e.strerror})"
        try:
            self._delete(key_id)
        except QumuloError as e:
            raise QumuloError(f"{problem}, and could not delete the key, whose secret is lost: {e}; remove {key_id} by hand", changed=True) from None
        raise QumuloError(f"{problem}, so deleted {key_id}, whose secret would be lost; run the task again", changed=True)

    def _confirm(self, owner, name, before, made, secret_file):
        """The user's keys after the make. A key the listing does not show is deleted with its secret's file; one the listing
        cannot be read for is kept, since its secret is safe in its file."""
        key_id = made["access_key_id"]
        try:
            after = user_keys(self.client, owner["auth_id"])
        except QumuloError as e:
            self.notes.append(f"made {key_id}, whose secret is in {secret_file}, but it could not be confirmed: the keys of {name} could not be read ({e})")
            return [*before, made]
        if any(key["access_key_id"] == key_id for key in after):
            return after
        try:
            self._delete(key_id)
        except QumuloError as e:
            raise QumuloError(
                f"access key {key_id} is missing after it was made, and deleting it failed: {e}; its secret is kept in {secret_file}", changed=True
            ) from None
        os.unlink(secret_file)
        raise QumuloError(f"access key {key_id} is missing after it was made; deleted it and its secret's file; run the task again", changed=True)

    def _delete(self, key_id):
        try:
            self.client.delete(ACCESS_KEY, params={"id": key_id})
        except QumuloApiError as e:
            if e.status != 404:  # listed a moment ago, so a 404 is another client's delete
                raise

    def _remove(self, name, keys):
        doomed = [key for key in keys if self.key_id in (None, key["access_key_id"])]
        if doomed and not self.module.check_mode:
            for key in doomed:
                self._delete(key["access_key_id"])
        left = [key for key in keys if key not in doomed]
        return self._result(bool(doomed), name, keys, left, {})

    def _result(self, changed, name, before, after, key):
        result = {"changed": changed, "s3_access_key": key}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {
                "before": {"user": name, "access_key_ids": [entry["access_key_id"] for entry in before]},
                "after": {"user": name, "access_key_ids": [entry["access_key_id"] for entry in after]},
            }
        return result


def _settings(client):
    return {"settings": S3Settings.options(client.get(SETTINGS).data)}


def _buckets(client):
    return {"buckets": [bucket_options(bucket) for bucket in list_buckets(client)]}


def _policies(client):
    policies = ({"bucket": bucket["name"], "policy": read_policy(client, bucket["name"])} for bucket in list_buckets(client))
    return {"policies": [entry for entry in policies if entry["policy"] != NO_POLICY]}


def _access_keys(client):
    return {"access_keys": [key_options(key) for key in all_keys(client)]}


# gather_subset name -> function of the client returning a dict of that subset's facts
S3_INFO_SUBSETS = {"settings": _settings, "buckets": _buckets, "policies": _policies, "access_keys": _access_keys}
