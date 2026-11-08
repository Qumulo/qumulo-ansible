# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The access tokens of the cluster's users: the routes, the expiration form, the access_token module's work, and the
identity_info subset.

The cluster stores no name for a token, so a user's tokens are managed as one set. It returns a token's bearer only in
its answer to the create, holds at most two tokens per user, expired ones counted, and returns no ETag on any token
route (specs/023-access-tokens/plan.md R2).
"""

from __future__ import annotations

import calendar
import os
import re
import time

from ansible_collections.qumulo.cluster.plugins.module_utils import client as qumulo_client
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import connection_token_id, require_continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import IdentityResolver, Pending, parse_trustee, render
from ansible_collections.qumulo.cluster.plugins.module_utils.secret_files import secret_directory, secret_file_name, write_secret_file

ACCESS_TOKENS = Route("/v1/auth/access-tokens/", ("GET", "POST"))
ACCESS_TOKEN = Route("/v1/auth/access-tokens/{id}", ("GET", "PATCH", "DELETE"), etag="omit", why="the token routes return no ETag and ignore an If-Match")
MOST_PER_USER = 2  # the cluster refuses a third token with too_many_access_tokens_error, expired tokens counted
NEVER = "never"  # expiration_time's value for no expiration, which the cluster reads back as null
NEW_TOKEN = "(a new token)"  # the id the diff shows in check mode for a token the run would make
NOT_FOUND = "unknown_access_token_error"
# An expiration this soon on the token the task connects with counts as cutting it off: a play runs on for a while after
# the task, and the controller's clock may be behind the cluster's.
CUT_OFF_MARGIN = 3600

# RFC 3339 with an offset, as the cluster takes it: it also takes a space for T, and refuses a time without an offset.
TIME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[Tt ](?P<clock>\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d{1,9}))?"
    r"(?:(?P<utc>[Zz])|(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2}))$"
)


def parse_time(text, read_back=None):
    """The instant text names, as (seconds, nanoseconds) since the epoch; QumuloError when it is not RFC 3339 with an offset,
    naming the expiration_time option, or with read_back the field of the cluster's answer that held text."""
    match = TIME.match(text)
    try:
        if not match:
            raise ValueError(text)
        parsed = time.strptime(f"{match['date']} {match['clock']}", "%Y-%m-%d %H:%M:%S")
        offset = 0 if match["utc"] else int(match["hours"]) * 3600 + int(match["minutes"]) * 60
        if offset >= 24 * 3600 or int(match["minutes"] or 0) >= 60:
            raise ValueError(text)
    except ValueError:
        if read_back:
            raise QumuloError(f"the cluster's {read_back} {text!r} is not an RFC 3339 time with a time zone offset or Z") from None
        raise QumuloError(f"expiration_time {text!r} is not an RFC 3339 time with a time zone offset or Z, such as 2027-06-01T00:00:00Z, nor {NEVER}") from None
    seconds = calendar.timegm(parsed) - (offset if match["sign"] != "-" else -offset)
    return seconds, int((match["fraction"] or "").ljust(9, "0"))


def same_time(declared, read_back):
    """Whether expiration_time as declared names the expiration as the cluster reads it back: an instant, or None for never."""
    if declared == NEVER or read_back is None:
        return declared == NEVER and read_back is None
    return parse_time(declared) == parse_time(read_back, "expiration_time")


def _wire_time(declared):
    """expiration_time as sent: null for never, else the instant in UTC with Z, a form the cluster was seen to take (R2)."""
    if declared == NEVER:
        return None
    seconds, nanoseconds = parse_time(declared)
    fraction = f".{nanoseconds:09d}".rstrip("0") if nanoseconds else ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + fraction + "Z"


# How to delete one token of a user's two, which the module cannot: it takes no token id (G-TOKEN).
DELETE_BY_ID = "qq auth_delete_access_token {id} or DELETE /v1/auth/access-tokens/{id}"

_unlink = os.unlink  # a seam: unit tests make removing a secret's file fail


def _now():
    nanoseconds = time.time_ns()
    return nanoseconds // 1_000_000_000, nanoseconds % 1_000_000_000


def token_options(token):
    """A token as a result shows it: never its bearer, which no read returns."""
    return {
        "user": render(token["user"]),
        "id": token["id"],
        "creator": render(token["creator"]),
        "creation_time": token["creation_time"],
        "expiration_time": token.get("expiration_time"),
        "enabled": token.get("enabled", True),
    }


def user_tokens(client, auth_id):
    """A user's tokens, oldest first, in the listing's order when made at the same instant."""
    tokens = client.paginate(ACCESS_TOKENS, "entries", query={"user": f"auth_id:{auth_id}"})
    return sorted(tokens, key=lambda token: parse_time(token["creation_time"], "creation_time"))


def access_tokens_subset(client):
    """identity_info's access_tokens: every token, by user and then oldest first."""
    tokens = [token_options(token) for token in client.paginate(ACCESS_TOKENS, "entries")]
    return {"access_tokens": sorted(tokens, key=lambda token: (token["user"], parse_time(token["creation_time"], "creation_time")))}


def _diff_entry(token):
    return {"id": token["id"], "enabled": token["enabled"], "expiration_time": token["expiration_time"]}


class AccessToken:
    """A user's access tokens. Not a Resource: a token is made, changed or deleted as one of the user's set, and its
    bearer is returned once (G-TOKEN)."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.user = self.params["user"]
        self.state = self.params["state"]
        parse_trustee(self.user)
        declared = self.params.get("expiration_time")
        if declared is not None and declared != NEVER:
            parse_time(declared)
        self.identities = IdentityResolver(client, check_mode=module.check_mode)
        self.notes = []

    def run(self):
        if self.state == "absent":
            owner = self.identities.resolve_existing(self.user)
            if owner is None:
                return self._result(False, self.user, [], [])
            return self._remove(render(owner), self._listed(owner))
        owner = self.identities.resolve(self.user)
        pending = isinstance(owner, Pending)
        name = self.user if pending else render(owner)
        tokens = [] if pending else self._listed(owner)
        if pending:
            self.notes.append(f"user {self.user} does not exist yet; an earlier task must create it, and this task then makes its token")
        if self.state == "rotated":
            return self._rotate(owner, name, tokens)
        if tokens:
            return self._update(name, tokens)
        return self._make_one(owner, name, tokens, old=[])

    def _listed(self, owner):
        return [token_options(token) for token in user_tokens(self.client, owner["auth_id"])]

    # present, with tokens: set enabled and expiration_time on each.

    def _changes(self, token):
        body = {}
        enabled, expiration = self.params.get("enabled"), self.params.get("expiration_time")
        if enabled is not None and enabled != token["enabled"]:
            body["enabled"] = enabled
        if expiration is not None and not same_time(expiration, token["expiration_time"]):
            body["expiration_time"] = _wire_time(expiration)
        return body

    def _update(self, name, tokens):
        changes = {token["id"]: self._changes(token) for token in tokens}
        for token in tokens:
            if self._cuts_off(changes[token["id"]]) and token["id"] == self._in_use():
                self._require_continuity(token["id"], name, "would stop logging in")
        patched = {}
        if not self.module.check_mode:
            # A PATCH that cuts off the token in use goes last, so that nothing is sent after it.
            for token in sorted(tokens, key=lambda token: token["id"] == self._in_use() and self._cuts_off(changes[token["id"]])):
                body = changes[token["id"]]
                if not body:
                    continue
                try:
                    patched[token["id"]] = token_options(self.client.patch(ACCESS_TOKEN, params={"id": token["id"]}, body=body).data)
                except QumuloError as e:
                    if not patched:
                        raise
                    raise QumuloError(f"changed access tokens {', '.join(patched)} of {name}, but then {e}", **{**e.details, "changed": True}) from None
        after = [patched.get(token["id"], {**token, **changes[token["id"]]}) for token in tokens]
        self._note_expired(name, after)
        return self._result(any(changes.values()), name, tokens, after)

    def _cuts_off(self, body):
        """Whether a PATCH body stops the token from logging in: disabled, or expiring within CUT_OFF_MARGIN by the controller's clock."""
        if body.get("enabled") is False:
            return True
        expiration = body.get("expiration_time")
        if expiration is None:
            return False
        seconds, nanoseconds = _now()
        return parse_time(expiration) <= (seconds + CUT_OFF_MARGIN, nanoseconds)

    def _note_expired(self, name, tokens):
        now = _now()
        for token in tokens:
            if token["expiration_time"] is not None and parse_time(token["expiration_time"], "expiration_time") <= now:
                # A warning, since the no_log a task that may make a token needs hides notes, and keeps warnings.
                self.client.warnings.append(
                    f"access token {token['id']} of {name} expired at {token['expiration_time']} and cannot log in; give expiration_time a "
                    f"later time or {NEVER} to make it log in again, or run state: rotated"
                )

    # rotated: make, store and confirm the new token, then delete the old ones, the one in use last.

    def _rotate(self, owner, name, tokens):
        if len(tokens) >= MOST_PER_USER:
            held = ", ".join(
                f"{token['id']}{' (expired)' if self._expired(token) else ''}{' (in use)' if token['id'] == self._in_use() else ''}" for token in tokens
            )
            raise QumuloError(
                f"{name} holds {held}, the most access tokens a user can hold, so a new one cannot be made before an old one is "
                f"removed. To keep the one whose bearer its consumers hold, delete the other by its id outside the collection: "
                f"{DELETE_BY_ID.format(id='<id>')}; to replace both, run state: absent, which removes the token in use too, then "
                "state: present (G-TOKEN)"
            )
        for token in tokens:
            if token["id"] == self._in_use():
                if self.params.get("enabled") is False:
                    raise QumuloError(
                        f"access token {token['id']} of {name} is the token this task connects with, and a rotation with enabled: false would "
                        "replace it with a disabled token, so no later task could connect with either; rotate without enabled: false"
                    )
                self._require_continuity(token["id"], name, "would be removed by the rotation")
        return self._make_one(owner, name, tokens, old=tokens)

    def _expired(self, token):
        return token["expiration_time"] is not None and parse_time(token["expiration_time"], "expiration_time") <= _now()

    # Making a token, for present with none and for rotated.

    def _make_one(self, owner, name, tokens, old):
        expiration = self.params.get("expiration_time")
        if expiration not in (None, NEVER) and parse_time(expiration) <= _now():
            raise QumuloError(f"expiration_time {expiration} is not after now, so the token {name} would be given could never log in")
        self._check_sinks()
        enabled = self.params.get("enabled")
        planned = {
            "user": name,
            "id": None,
            "creator": None,
            "creation_time": None,
            "expiration_time": _wire_time(expiration) if expiration is not None else None,
            "enabled": enabled is not False,
        }
        if self.module.check_mode:
            kept = [token for token in tokens if token not in old]
            return self._result(True, name, tokens, [*kept, planned], new_id=NEW_TOKEN)
        made = self._make(owner, name, tokens, expiration)
        qumulo_client.register_secret(made["bearer_token"])
        secret_file = self._store(name, made)
        made_keys = {"bearer_token": made["bearer_token"], "access_token_id": made["id"], "secret_file": secret_file}
        after = self._confirm(owner, name, made, secret_file, made_keys, [token["id"] for token in old])
        deleted, lost = set(), None
        connection_changed = False
        try:
            if enabled is False:
                confirmed = next(token for token in after if token["id"] == made["id"])
                disabled = token_options(self.client.patch(ACCESS_TOKEN, params={"id": made["id"]}, body={"enabled": False}).data)
                after = [disabled if token is confirmed else token for token in after]
            for token in sorted(old, key=lambda token: token["id"] == self._in_use()):  # the token in use last
                lost = token["id"]
                self._delete(token["id"])
                deleted.add(token["id"])
                lost = None
                connection_changed = connection_changed or token["id"] == self._in_use()
        except QumuloError as e:
            maybe_gone = lost if isinstance(e, QumuloMaybeApplied) else None
            kept = [token["id"] for token in old if token["id"] not in deleted and token["id"] != maybe_gone]
            self._fail_after_store(name, made_keys, str(e), kept, e.details, maybe_gone)
        after = [token for token in after if token["id"] not in deleted]
        result = self._result(True, name, tokens, after)
        result.update(made_keys)
        if connection_changed:
            result["connection_token_changed"] = True
        return result

    def _check_sinks(self):
        """A new token's bearer is shown once, so it must have somewhere to go that prints nothing, made private before any token is."""
        if not self.module.no_log:
            raise QumuloError("set no_log: true on this task: a new token's bearer is returned once, in this task's result, and never again (G-TOKEN)")
        if not self.params.get("secret_destination"):
            raise QumuloError("set secret_destination, the directory where a new token's bearer is written, since the cluster never shows it again (G-TOKEN)")
        if not self.module.check_mode:
            secret_directory(self.params["secret_destination"], "no token was made")

    def _make(self, owner, name, tokens, expiration):
        body = {"user": {"auth_id": owner["auth_id"]}}
        if expiration not in (None, NEVER):
            body["expiration_time"] = _wire_time(expiration)
        try:
            return self.client.post(ACCESS_TOKENS, body=body).data
        except QumuloMaybeApplied as lost:
            known = {token["id"] for token in tokens}
            try:
                listed = user_tokens(self.client, owner["auth_id"])
            except QumuloError as e:
                raise QumuloMaybeApplied(
                    f"{lost}; the tokens of {name} could not be read after it either ({e}), so a token whose bearer is lost may be left. "
                    "With no token before, run the task with state: rotated, which replaces it; with one, run state: absent, then state: present"
                ) from None
            orphans = [token["id"] for token in listed if token["id"] not in known]
            if not orphans:
                raise QumuloError(
                    f"{lost}; the listing after it shows no new token for {name}, so no token was made unless the create lands later; run "
                    f"the task again, and if {name} then holds a token whose bearer nobody has, run state: rotated"
                ) from None
            for orphan in orphans:
                try:
                    self._delete(orphan)
                except QumuloError as e:
                    raise QumuloError(
                        f"{lost}; the token {orphan} was made but its bearer was lost, and deleting it failed ({e}); remove {orphan} by hand",
                        changed=True,
                    ) from None
            raise QumuloError(
                f"{lost}; the token was made but its bearer was lost, so deleted {', '.join(orphans)}, and kept the old tokens; run the task again",
                changed=True,
            ) from None

    def _store(self, name, made):
        token_id = made["id"]
        try:
            return write_secret_file(
                self.params["secret_destination"],
                secret_file_name("access-token", name, token_id),
                "The bearer of an access token, which the cluster returns only when it makes the token.",
                (("user", name), ("access_token_id", token_id), ("bearer_token", made["bearer_token"])),
            )
        except OSError as e:
            problem = f"could not write the bearer of the new token {token_id} to {self.params['secret_destination']} ({e.strerror})"
        try:
            self._delete(token_id)
        except QumuloError as e:
            raise QumuloError(f"{problem}, and could not delete the token, whose bearer is lost: {e}; remove {token_id} by hand", changed=True) from None
        raise QumuloError(f"{problem}, so deleted {token_id}, whose bearer would be lost, and kept the old tokens; run the task again", changed=True)

    def _fail_after_store(self, name, made_keys, problem, kept, details=None, maybe_gone=None):
        """Fail a run whose new token's bearer is safe in its file. The same words go out as a warning, which no_log keeps."""
        said = f"made access token {made_keys['access_token_id']} of {name}, whose bearer is in {made_keys['secret_file']}, but then {problem}"
        if maybe_gone:
            said += (
                f"; the old token {maybe_gone} may have been deleted. Once the token's consumers hold the new bearer and it logs in, "
                f"list the user's tokens, and if {maybe_gone} is still listed, delete it by its id outside the collection: "
                f"{DELETE_BY_ID.format(id=maybe_gone)} (G-TOKEN)"
            )
        if kept:
            said += f"; kept the old tokens {', '.join(kept)}"
            said += (
                "; once the token's consumers hold the new bearer and it logs in, delete each old token by its id outside the "
                f"collection: {'; '.join(DELETE_BY_ID.format(id=token_id) for token_id in kept)} (G-TOKEN)"
            )
        self.client.warnings.append(said)
        raise QumuloError(said, **{**(details or {}), "changed": True, **made_keys})

    def _confirm(self, owner, name, made, secret_file, made_keys, old_ids):
        """The user's tokens after the make. A token the listing does not show is deleted with its bearer's file."""
        token_id = made["id"]
        try:
            after = self._listed(owner)
        except QumuloError as e:
            self._fail_after_store(name, made_keys, f"the tokens of {name} could not be read to confirm it ({e})", old_ids)
        if any(token["id"] == token_id for token in after):
            return after
        try:
            self._delete(token_id)
        except QumuloError as e:
            raise QumuloError(
                f"access token {token_id} is missing after it was made, and deleting it failed: {e}; its bearer is kept in {secret_file}", changed=True
            ) from None
        try:
            _unlink(secret_file)
        except OSError as e:
            raise QumuloError(
                f"access token {token_id} is missing after it was made; deleted it, but not its bearer's file {secret_file} ({e.strerror})",
                changed=True,
            ) from None
        raise QumuloError(f"access token {token_id} is missing after it was made; deleted it and its bearer's file; run the task again", changed=True)

    def _delete(self, token_id):
        try:
            self.client.delete(ACCESS_TOKEN, params={"id": token_id})
        except QumuloApiError as e:
            if not (e.status == 404 and e.is_(NOT_FOUND)):  # listed a moment ago, so a 404 is another client's delete
                raise

    # absent

    def _remove(self, name, tokens):
        for token in tokens:
            if token["id"] == self._in_use():
                self._require_continuity(token["id"], name, "would be removed")
        connection_changed = False
        if not self.module.check_mode:
            deleted = []
            for token in sorted(tokens, key=lambda token: token["id"] == self._in_use()):  # the token in use last
                try:
                    self._delete(token["id"])
                except QumuloError as e:
                    if not deleted:
                        raise
                    raise QumuloError(f"deleted access tokens {', '.join(deleted)} of {name}, but then {e}", **{**e.details, "changed": True}) from None
                deleted.append(token["id"])
                connection_changed = connection_changed or token["id"] == self._in_use()
        result = self._result(bool(tokens), name, tokens, [])
        if connection_changed:
            result["connection_token_changed"] = True
        return result

    # The token in use.

    def _in_use(self):
        """The id of the token this task connects with; None with a password."""
        return connection_token_id(self.client)

    def _require_continuity(self, token_id, name, what):
        require_continuity(
            self.params,
            f"access token {token_id} of {name} is the token this task connects with, and {what}",
            "connect later tasks with a password or with the new token's bearer_token",
        )

    def _result(self, changed, name, before, after, new_id=None):
        result = {"changed": changed, "access_tokens": after}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {
                "before": {"user": name, "access_tokens": [_diff_entry(token) for token in before]},
                "after": {"user": name, "access_tokens": [_diff_entry({**token, "id": token["id"] or new_id}) for token in after]},
            }
        return result
