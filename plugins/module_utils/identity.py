# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Trustees: the string grammar the collection accepts, and their resolution to cluster identities.

POST /v1/identity/find takes part of an identity and returns all of it: domain, auth_id, name, sid,
uid and gid. A bare name is looked up in every identity source, and once AD or LDAP is configured a
name found in more than one fails, so the grammar requires a domain prefix. AD names resolve only
after the cluster joins the domain, except Authenticated Users' NT AUTHORITY name, and local auth_ids
change when a cluster is rebuilt, so every module run resolves its trustees again and auth_ids never
leave the process.
"""

from __future__ import annotations

import re

from ansible_collections.qumulo.cluster.plugins.module_utils.client import READ_ONLY_WHY, QumuloApiError, QumuloError, Route, term_error

FIND = Route("/v1/identity/find", ("POST",), replay=frozenset({"POST"}), why=READ_ONLY_WHY)
EXPAND = Route("/v1/identity/expand", ("POST",), replay=frozenset({"POST"}), why=READ_ONLY_WHY)

SID_RE = re.compile(r"^S-\d+-\d+(?:-\d+)+$", re.IGNORECASE)
# API_QUMULO_SUPPORT_DOMAIN holds the member Nexus registration puts in the Qumulo-Support role,
# qumulosupport:everyone, which has a name but no SID, uid or gid.
NAME_DOMAINS = {
    "local": "LOCAL",
    "ad": "ACTIVE_DIRECTORY",
    "ldap_user": "POSIX_USER",
    "ldap_group": "POSIX_GROUP",
    "qumulo_support": "API_QUMULO_SUPPORT_DOMAIN",
}
PREFIXES = {domain: prefix for prefix, domain in NAME_DOMAINS.items()}
WORLD = {"domain": "WORLD", "name": "Everyone"}
# The cluster returns Authenticated Users in ACTIVE_DIRECTORY with no domain in its name, joined or not. An ad: trustee
# needs a domain, and the cluster finds the name with NT AUTHORITY, the domain Windows gives the group, joined to a domain
# or not; it refuses the group's SID until it has found a name in S-1-5 (QUIRK-100).
AUTHENTICATED_USERS_SID = "S-1-5-11"
AUTHENTICATED_USERS_NAME = "NT AUTHORITY\\Authenticated Users"
AUTHENTICATED_USERS = f"ad:{AUTHENTICATED_USERS_NAME}"
# The internal domain holds these two, with a name but no SID, uid or gid; the find matches the name in this case only.
INTERNAL_NAMES = {name.casefold(): name for name in ("File Owner", "File Group Owner")}
FORMS = (
    "local:<name>, ad:<DOMAIN>\\<name>, ad:<name>@<domain>, ldap_user:<name>, ldap_group:<name>, "
    "qumulo_support:<name>, Everyone (or world:Everyone), internal:File Owner, internal:File Group Owner, uid:<n>, "
    "gid:<n>, sid:S-1-... or a bare SID"
)


class Pending(str):
    """A trustee that only exists after an earlier task runs, in check mode. Never equal to anything, itself included."""

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    __hash__ = str.__hash__


def parse_trustee(spec):
    """The /v1/identity/find request for a trustee string; QumuloError for anything outside the grammar."""
    text = spec.strip()
    if text.lower() == "everyone":
        return dict(WORLD)
    if SID_RE.match(text):
        return _sid_query(text.upper())
    prefix, sep, value = text.partition(":")
    prefix = prefix.lower()
    if not sep or prefix not in (*NAME_DOMAINS, "world", "internal", "uid", "gid", "sid"):
        raise QumuloError(f"trustee '{spec}' needs a prefix naming where it is defined; a bare name is ambiguous. Accepted forms: {FORMS}")
    value = value.strip()
    if prefix == "world":
        if value.lower() != "everyone":
            raise QumuloError(f"trustee '{spec}': the world domain holds only Everyone")
        return dict(WORLD)
    if prefix == "internal":
        if value.casefold() not in INTERNAL_NAMES:
            raise QumuloError(f"trustee '{spec}': the internal domain holds only File Owner and File Group Owner")
        return {"domain": "API_INTERNAL_DOMAIN", "name": INTERNAL_NAMES[value.casefold()]}
    if prefix in ("uid", "gid"):
        if not re.fullmatch(r"[0-9]+", value):
            raise QumuloError(f"trustee '{spec}': {prefix} must be a non-negative integer")
        return {prefix: int(value)}
    if prefix == "sid":
        if not SID_RE.match(value):
            raise QumuloError(f"trustee '{spec}': not a SID such as S-1-5-21-1-2-3-1001")
        return _sid_query(value.upper())
    if not value:
        raise QumuloError(f"trustee '{spec}': the name is empty")
    if prefix == "ad" and not _qualified_ad_name(value):
        hint = f"; Authenticated Users is {AUTHENTICATED_USERS}" if value.casefold() == "authenticated users" else ""
        raise QumuloError(f"trustee '{spec}': write an AD name as ad:<DOMAIN>\\<name> or ad:<name>@<domain>{hint}")
    return {"domain": NAME_DOMAINS[prefix], "name": value}


def _sid_query(sid):
    """The find for a SID: Authenticated Users' by its name, which names the same identity joined to a domain or not, where
    the SID's find may be refused (QUIRK-100)."""
    if sid == AUTHENTICATED_USERS_SID:
        return {"domain": "ACTIVE_DIRECTORY", "name": AUTHENTICATED_USERS_NAME}
    return {"sid": sid}


def well_known_trustee(sid):
    """The trustee of a well-known identity the collection names other than by its SID, or None."""
    return AUTHENTICATED_USERS if sid == AUTHENTICATED_USERS_SID else None


def _qualified_ad_name(name):
    domain, backslash, user = name.partition("\\")
    if backslash:
        return bool(domain and user)
    user, at, domain = name.partition("@")
    return bool(at and user and domain)


def render(ident):
    """The canonical trustee string for an identity the server returned; parse_trustee() of it finds the same identity."""
    domain, name = ident.get("domain"), ident.get("name")
    if domain == "WORLD":
        return "world:Everyone"
    if domain == "ACTIVE_DIRECTORY" and well_known_trustee(ident.get("sid")):
        return well_known_trustee(ident["sid"])
    if domain == "API_INTERNAL_DOMAIN" and name in INTERNAL_NAMES.values():
        return f"internal:{name}"
    if name and domain in PREFIXES and (domain != "ACTIVE_DIRECTORY" or _qualified_ad_name(name)):
        return f"{PREFIXES[domain]}:{name}"
    if domain == "POSIX_USER" and ident.get("uid") is not None:
        return f"uid:{ident['uid']}"
    if domain == "POSIX_GROUP" and ident.get("gid") is not None:
        return f"gid:{ident['gid']}"
    if ident.get("sid"):
        return f"sid:{ident['sid']}"
    raise QumuloError(f"identity {ident!r} cannot be written as a trustee: it has no SID, uid or gid, and no name the trustee grammar holds")


# The find's 404 classes that are a trustee no source holds, as T2000p and T2001p saw them on 7.10.3 and 7.11.0. Any
# other 404 fails, whatever its suffix: with no domain controller in reach the find answers one ending
# _not_found_error too (QUIRK-102).
MISS_CLASSES = frozenset({"api_identity_name_not_found_error"})


def _not_found(error):
    return error.status == 404 and error.error_class in MISS_CLASSES


class IdentityResolver:
    """Resolves trustees for one module run, caching by auth_id and by the find a trustee string makes, so every spelling the
    grammar reads alike shares one entry. known() seeds that cache with what the server read back, which is why a declared
    trustee can resolve with no find.

    An auth_id is meaningful only on the cluster that issued it: the caller must never compare
    identities resolved on different clusters, or carry an auth_id from one run to the next.
    """

    def __init__(self, client, check_mode=False):
        self.client = client
        self.check_mode = check_mode
        self._by_spec = {}
        self._by_auth_id = {}

    def resolve(self, spec):
        """The full identity for a trustee string, or for an identity the server returned (such as an ACL entry's trustee).

        In check mode a trustee that does not exist yet becomes a Pending.
        """
        if isinstance(spec, dict):
            return self._resolve_auth_id(spec)
        key = self._key(spec)
        if key not in self._by_spec:
            self._by_spec[key] = self._find(dict(key), spec)
        return self._by_spec[key]

    def resolve_existing(self, spec):
        """The full identity for a trustee string, or None when none exists, in check mode too.

        For a task that only removes: a trustee that does not exist holds nothing to remove, as after its account is deleted.
        """
        try:
            return self.find_existing(spec)
        except QumuloApiError as e:
            raise QumuloError(f"trustee '{spec}': {e}", trustee=spec, **e.details) from None

    def find_existing(self, spec):
        """The full identity for a trustee string, or None when none exists, in check mode too; the server's error as it came."""
        key = self._key(spec)
        found = self._by_spec.get(key)
        if found is not None and not isinstance(found, Pending):
            return found
        try:
            found = self.client.post(FIND, body=dict(key)).data
        except QumuloApiError as e:
            if _not_found(e):
                return None
            raise
        self._by_spec[key] = self._by_auth_id.setdefault(found["auth_id"], found)
        return self._by_spec[key]

    def resolve_all(self, specs):
        return [self.resolve(spec) for spec in specs]

    def known(self, ident, trustee=None):
        """The trustee for an identity the server returned with its auth_id, which then resolves to that identity without a
        find, in any spelling the grammar reads alike.

        The find may refuse what the server returned, as a SID of a domain the node has not seen (QUIRK-100), so a read-back
        entry compares by the identity the server gave, not by finding its trustee again. trustee, when given, is the form the
        server gave beside the auth_id; otherwise the identity is rendered, or, where render has no form for it, named as the
        server gave it, which no declared trustee can match.
        """
        try:
            trustee = trustee or render(ident)
            key = self._key(trustee)
        except QumuloError:
            trustee = ident.get("name") or ident["domain"]
            key = ("as_given", trustee)  # its own key, so a declared trustee spelled alike cannot answer to it
        if "domain" in ident:  # a whole identity; one given as its auth_id alone stays out of the cache by auth_id
            ident = self._by_auth_id.setdefault(ident["auth_id"], ident)
        self._by_spec[key] = self._by_auth_id.get(ident["auth_id"], ident)
        return trustee

    def same(self, a, b):
        """Whether two trustees (strings or identities) are one identity. A Pending is never the same as anything."""
        a, b = (self.resolve(x) if isinstance(x, str) and not isinstance(x, Pending) else x for x in (a, b))
        if isinstance(a, Pending) or isinstance(b, Pending):
            return False
        return a.get("auth_id") is not None and a.get("auth_id") == b.get("auth_id")

    def _key(self, spec):
        """The cache key of a trustee string: the find it makes; or, for a name only known() gives, the name itself.

        A spec outside the grammar fails as parse_trustee does, unless known() named an identity by it.
        """
        try:
            return tuple(sorted(parse_trustee(spec).items()))
        except QumuloError:
            if ("as_given", spec) in self._by_spec:
                return ("as_given", spec)
            raise

    def _resolve_auth_id(self, ident):
        auth_id = ident.get("auth_id")
        if auth_id is None:
            raise QumuloError(f"identity {ident!r} has no auth_id to resolve")
        if auth_id in self._by_auth_id:
            return self._by_auth_id[auth_id]
        return self._find({"auth_id": auth_id}, f"auth_id:{auth_id}")

    def _find(self, query, spec):
        try:
            found = self.client.post(FIND, body=query).data
        except QumuloApiError as e:
            if self.check_mode and _not_found(e):
                return Pending(f"{spec} (resolves after an earlier task creates or joins it)")
            raise QumuloError(f"trustee '{spec}': {e}", trustee=spec, **e.details) from None
        return self._by_auth_id.setdefault(found["auth_id"], found)


# The find's 400 classes that a term the grammar accepts can still earn, each the term's own fault: a SID of a domain the
# node has not seen (QUIRK-100), a uid or gid past 32 bits, and a SID part out of range, observed on 7.10.3 and 7.11.0;
# and an ldap_user: or ldap_group: name more than one LDAP entry carries, or one the LDAP search refuses, read from Qumulo
# Core's source, since no lane runs an LDAP server.
FIND_TERM_CLASSES = frozenset(
    {
        "api_identity_unsupported_domain_error",
        "decode_error",
        "ms_sid_decode_error",
        "api_identity_mismatch_error",
        "api_identity_ldap_search_filter_error",
    }
)
NO_IDENTITY = {"trustee": None, "domain": None, "name": None, "sid": None, "uid": None, "gid": None}


def identity_fields(ident):
    """trustee, domain, name, sid, uid and gid of an identity the server returned; trustee None where render has no form."""
    if ident is None:
        return None
    try:
        trustee = render(ident)
    except QumuloError:
        trustee = None
    return {"trustee": trustee, **{key: ident.get(key) for key in ("domain", "name", "sid", "uid", "gid")}}


def parse_terms(lookup, terms):
    """{term: find request} for a lookup's trustee terms; QumuloError naming the lookup and the term otherwise."""
    queries = {}
    for term in terms:
        try:
            queries[term] = parse_trustee(term)
        except QumuloError as e:
            raise QumuloError(f"{lookup}: {e}") from None
    return queries


def find_trustees(client, terms):
    """({term: entry}, [terms with errors]) for trustee strings, each found through POST /v1/identity/find."""
    parse_terms("find_trustees", terms)
    resolver, found, errors = IdentityResolver(client), {}, []
    for term in dict.fromkeys(terms):
        ident, error = _find_term(resolver, "find_trustees", term)
        if error is not None:
            found[term] = {"found": False, **NO_IDENTITY, "error": error}
            errors.append(term)
        else:
            found[term] = {"found": ident is not None, **(identity_fields(ident) or NO_IDENTITY)}
    return found, errors


def _find_term(resolver, lookup, term):
    """(identity or None, None), or (None, the term's error); any other failure raises naming the term."""
    try:
        return resolver.find_existing(term), None
    except QumuloError as e:  # the server's errors, and the client's, an unreachable cluster among them
        error = term_error(e, FIND_TERM_CLASSES)
        if error is None:
            raise QumuloError(f"{lookup} term '{term}': {e}", term=term, **e.details) from None
        return None, error


NO_EXPANSION = {"found": False, "type": None, "identity": None, "nfs_identity": None, "smb_identity": None, "equivalents": [], "groups": []}


def expand_trustees(client, terms):
    """({term: entry}, [terms with errors]) for trustee strings, each found, then expanded through POST /v1/identity/expand.

    A term not found sends no expand. A 400 to the expand fails: its body is the collection's, built from the find.
    """
    parse_terms("expand_trustees", terms)
    resolver, found, errors = IdentityResolver(client), {}, []
    for term in dict.fromkeys(terms):
        ident, error = _find_term(resolver, "expand_trustees", term)
        if error is not None:
            found[term] = {**NO_EXPANSION, "error": error}
            errors.append(term)
        elif ident is None:
            found[term] = dict(NO_EXPANSION)
        else:
            try:
                expansion = client.post(EXPAND, body={"id": ident}).data
            except QumuloError as e:
                raise QumuloError(f"expand_trustees term '{term}': {e}", term=term, **e.details) from None
            found[term] = _expansion(expansion)
    return found, errors


def _expansion(answer):
    return {
        "found": True,
        "type": answer.get("type"),
        "identity": identity_fields(answer.get("id")),
        "nfs_identity": identity_fields(answer.get("nfs_id")),
        "smb_identity": identity_fields(answer.get("smb_id")),
        "equivalents": _in_order(answer.get("equivalent_ids")),
        "groups": _in_order(answer.get("group_ids")),
    }


def _in_order(identities):
    # The server gives an expansion's groups in no fixed order (T2000p), so they are sorted for a result that compares.
    entries = [identity_fields(ident) for ident in identities or ()]
    return sorted(entries, key=lambda entry: (entry["trustee"] or "", entry["sid"] or ""))
