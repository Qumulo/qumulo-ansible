# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The directory services area: Active Directory membership, and the lookup of the directory's objects."""

from __future__ import annotations

import re

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route, term_error
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import SID_RE

AD_STATUS = Route("/v1/ad/status", ("GET",))
AD_MONITOR = Route("/v1/ad/monitor", ("GET",))
AD_JOIN = Route("/v1/ad/join", ("POST",))
AD_RECONFIGURE = Route("/v1/ad/reconfigure", ("POST",))
AD_LEAVE = Route("/v1/ad/leave", ("POST",))
AD_OBJECT_BY_DN = Route("/v1/ad/distinguished-names/{dn}/object", ("GET",))
AD_OBJECT_BY_SID = Route("/v1/ad/sids/{sid}/object", ("GET",))
AD_OBJECTS_BY_NAME = Route("/v1/ad/usernames/{username}/objects/", ("GET",))
NOT_IN_DOMAIN, JOINED = "NOT_IN_DOMAIN", "JOINED_TO_DOMAIN"
# Every AD route answers one of these 409s before anything else on a cluster it cannot ask a single domain.
NOT_JOINED = frozenset({"ad_domain_not_joined_error", "ad_domain_cannot_infer_domain_error"})


def not_joined(error):
    """Whether an AD route refused because the cluster is in no domain, or in more than one."""
    return isinstance(error, QumuloApiError) and error.status == 409 and error.error_class in NOT_JOINED


ACCOUNT_EXISTS = "AD_DOMAIN_COMPUTER_ACCOUNT_ALREADY_EXISTS"
IN_PLACE = ("search_trusted_domains", "use_ad_posix_attributes", "base_dn", "domain_controllers")
POLL_INTERVAL = 2.0


def joined_domain(client):
    """The domain the cluster is joined to, or None. Read before a rename, which the server refuses while joined."""
    status = client.get(AD_STATUS).data
    return None if status["status"] == NOT_IN_DOMAIN else status.get("domain") or status["status"]


def same_domain(a, b):
    """The server looks domains up without regard to case."""
    return bool(a) and bool(b) and a.lower() == b.lower()


def ad_facts(status, monitor):
    """The AD membership in ad_domain's option shape, from GET /v1/ad/status and GET /v1/ad/monitor.

    The status does not hold the configured domain controllers. The monitor does, but it describes only the last
    join, leave or reconfigure, so its list counts only when it is for the joined domain (G-AD-DCS).
    """
    domain = status.get("domain") or None
    dcs = monitor.get("configured_dcs") if same_domain(monitor.get("domain"), domain) else None
    return {
        "status": status["status"],
        "domain": domain,
        "domain_netbios": status.get("domain_netbios") or None,
        "ou": status.get("ou") or None,
        "search_trusted_domains": status.get("search_trusted_domains"),
        "use_ad_posix_attributes": status.get("use_ad_posix_attributes"),
        "base_dn": status.get("base_dn") or None,
        "domain_controllers": dcs,
    }


def read_ad(client):
    status = client.memo["ad_status"] = client.get(AD_STATUS).data  # read fresh, and kept for find_ad_objects' trustees
    return ad_facts(status, client.get(AD_MONITOR).data)


def default_base_dn(domain):
    """The base DN the cluster stores for an empty one: CN=Users,DC=corp,DC=example,DC=com for corp.example.com."""
    return ",".join(["CN=Users", *(f"DC={label}" for label in domain.split("."))])


def _dn(text):
    """A distinguished name, or a ;-separated list of them, compared without case or the spaces around separators."""
    return ";".join(",".join(part.strip().lower() for part in dn.split(",")) for dn in (text or "").split(";") if dn.strip())


class AdDomain:
    """ad_domain: explicit join and leave; idempotent only as far as "joined to this domain under its own name" (G-AD-JOIN)."""

    RETURN_KEY = "ad_domain"

    def __init__(self, module, client):
        self.module, self.client, self.params = module, client, module.params
        self.domain = self.params["domain"]
        self.notes = []

    def run(self):
        before, monitor = self._read()
        if self.params["state"] == "absent":
            changed, after = self._leave(before)
        else:
            changed, after = self._join(before, monitor)
        result = {"changed": changed, self.RETURN_KEY: after}
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": before, "after": after}
        return result

    def _read(self):
        """The membership facts, after any operation already running on the cluster has finished."""
        status = self.client.get(AD_STATUS).data
        monitor = self.client.get(AD_MONITOR).data
        if "IN_PROGRESS" in monitor["status"]:
            self._wait("an AD operation already running", changed=False)
            status = self.client.get(AD_STATUS).data
            monitor = self.client.get(AD_MONITOR).data
        return ad_facts(status, monitor), monitor

    def _join(self, before, monitor):
        if before["status"] == JOINED:
            if not same_domain(before["domain"], self.domain):
                raise QumuloError(f"the cluster is joined to {before['domain']}, not {self.domain}; leave it first with ad_domain state=absent")
            return self._reconfigure(before, monitor)
        if not (self.params.get("join_user") and self.params.get("join_password")):
            raise QumuloError(f"joining {self.domain} needs join_user and join_password")
        predicted = {
            "status": JOINED,
            "domain": self.domain,
            "domain_netbios": self.params.get("domain_netbios"),
            "ou": self.params.get("ou"),
            "search_trusted_domains": self.params.get("search_trusted_domains") is not False,  # the server's default is true
            "use_ad_posix_attributes": bool(self.params.get("use_ad_posix_attributes")),
            "base_dn": self.params.get("base_dn") or default_base_dn(self.domain),
            "domain_controllers": self.params.get("domain_controllers"),
        }
        if self.module.check_mode:
            return True, predicted
        body = {
            "domain": self.domain,
            "domain_netbios": self.params.get("domain_netbios") or "",
            "user": self.params["join_user"],
            "password": self.params["join_password"],
            "ou": self.params.get("ou") or "",
            "use_ad_posix_attributes": bool(self.params.get("use_ad_posix_attributes")),
            "base_dn": self.params.get("base_dn") or "",
        }
        if self.params.get("search_trusted_domains") is not None:
            body["search_trusted_domains"] = self.params["search_trusted_domains"]
        if self.params.get("domain_controllers") is not None:
            body["domain_controllers"] = ",".join(self.params["domain_controllers"])
        self._post(AD_JOIN, body)
        if not self.params["wait"]:
            return True, predicted
        done = self._wait(f"joining {self.domain}")
        if done["status"] != "JOIN_SUCCEEDED":
            error = done.get("last_error") or {}
            cause = error.get("error_class") or done["status"]
            hint = ""
            if ACCOUNT_EXISTS in cause:
                hint = "; a computer account of this cluster's name already exists, and the join user may not take it over (G-AD-JOIN)"
            raise QumuloError(f"joining {self.domain} failed: {cause}: {error.get('description', '')}{hint}", changed=True)
        return True, self._read()[0]

    def _reconfigure(self, before, monitor):
        for key in ("ou", "domain_netbios"):
            want = self.params.get(key)
            if want is not None and (_dn(want) != _dn(before[key]) if key == "ou" else want.lower() != (before[key] or "").lower()):
                raise QumuloError(
                    f"{key} is set only by a join: the cluster has {before[key]!r}, not {want!r}; leave with ad_domain state=absent, then join again"
                )
        changes = {}
        for key in IN_PLACE:
            want = self.params.get(key)
            if want is None:
                continue
            have = before[key]
            if key == "domain_controllers" and have is None:
                self.notes.append(f"the configured domain controllers of {self.domain} cannot be read back, so they were not compared (G-AD-DCS)")
                continue
            if (_dn(want or default_base_dn(self.domain)) != _dn(have)) if key == "base_dn" else want != have:
                changes[key] = ",".join(want) if key == "domain_controllers" else want
        if not changes:
            return False, before
        after = {**before, **{key: self.params[key] for key in changes}}
        if self.module.check_mode:
            return True, after
        self._post(AD_RECONFIGURE, changes)
        if not self.params["wait"]:
            return True, after
        done = self._wait(f"changing the settings of {self.domain}")
        error = (done.get("last_error") or {}).get("error_class")
        # A failed reconfigure goes back to JOIN_SUCCEEDED and records only a new last_error.
        if error and done.get("last_action_time") != monitor.get("last_action_time"):
            raise QumuloError(f"changing the settings of {self.domain} failed: {error}: {done['last_error'].get('description', '')}", changed=True)
        return True, self._read()[0]

    def _leave(self, before):
        if before["status"] != JOINED:
            return False, before
        if not same_domain(before["domain"], self.domain):
            self.notes.append(f"the cluster is joined to {before['domain']}, not {self.domain}; left as it is")
            return False, before
        user, password = self.params.get("join_user") or "", self.params.get("join_password") or ""
        if not (user and password):
            self.client.warnings.append(f"leaving {self.domain} without join_user and join_password: the cluster's computer account stays in Active Directory")
        after = ad_facts({"status": NOT_IN_DOMAIN}, {})
        if self.module.check_mode:
            return True, after
        self._post(AD_LEAVE, {"domain": before["domain"], "user": user, "password": password})
        if not self.params["wait"]:
            return True, after
        done = self._wait(f"leaving {self.domain}")
        error = (done.get("last_error") or {}).get("error_class")
        if error:
            self.client.warnings.append(
                f"left {self.domain}, but its computer account could not be deleted: {error}: {done['last_error'].get('description', '')}"
            )
        return True, self._read()[0]

    def _post(self, route, body):
        try:
            self.client.post(route, body=body)
        except QumuloMaybeApplied:
            if not self.params["wait"]:
                raise
            # Never sent twice: the monitor shows whether the operation ran.

    def _wait(self, what, changed=True):
        """Poll the monitor until its operation is no longer in progress; returns the monitor."""
        deadline = self.client.clock() + self.params["wait_timeout"]
        while True:
            monitor = self.client.get(AD_MONITOR).data
            if "IN_PROGRESS" not in monitor["status"]:
                return monitor
            left = deadline - self.client.clock()
            if left <= 0:
                raise QumuloError(f"{what} is still {monitor['status']} after {self.params['wait_timeout']}s", changed=changed)
            self.client.sleep(min(POLL_INTERVAL, left))


AD_TERM_FORMS = "dn:<distinguished name>, sid:<SID> or name:<sAMAccountName>"
# The 400 classes of the AD object reads that are a term's own fault, as T2001p saw them on 7.11.0.
AD_TERM_CLASSES = frozenset({"distinguished_name_decode_error", "ms_sid_decode_error"})
NO_OBJECTS = {"found": False, "objects": []}


def parse_ad_term(term):
    """(kind, value) for a dn:, sid: or name: term; QumuloError naming the term and the forms otherwise."""
    prefix, sep, value = str(term).partition(":")
    kind, value = prefix.strip().lower(), value.strip()
    problem = None
    if not sep or kind not in ("dn", "sid", "name"):
        problem = "needs one of the prefixes dn:, sid: or name:"
    elif not value:
        problem = "is empty"
    elif kind == "sid" and not SID_RE.match(value):
        problem = "is not a SID such as S-1-5-21-1-2-3-1104"
    elif kind == "dn" and "=" not in value:
        problem = "is not a distinguished name such as CN=alice,CN=Users,DC=corp,DC=example,DC=com"
    elif kind == "name" and ("\\" in value or "@" in value):
        problem = "names a domain; the read searches the joined domain, so give the bare sAMAccountName"
    if problem:
        raise QumuloError(f"find_ad_objects term '{term}' {problem}; accepted forms: {AD_TERM_FORMS}")
    return kind, value.upper() if kind == "sid" else value


def find_ad_objects(client, terms):
    """({term: entry}, [terms with errors]) for AD terms, each read through the AD object route of its kind.

    A 409 of NOT_JOINED is raised as it came, at the first term, since every AD route answers it before anything else.
    """
    parsed = {term: parse_ad_term(term) for term in terms}
    found, errors = {}, []
    for term, (kind, value) in parsed.items():
        try:
            objects = _read_objects(client, kind, value)
            if objects:
                status = _status(client)
                objects = [{**obj, "trustee": _trustee(obj, status)} for obj in _by_sid(objects)]
        except QumuloApiError as e:
            if not_joined(e):
                raise
            error = term_error(e, AD_TERM_CLASSES)
            if error is None:
                raise QumuloError(f"find_ad_objects term '{term}': {e}", term=term, **e.details) from None
            found[term] = {**NO_OBJECTS, "error": error}
            errors.append(term)
            continue
        except QumuloError as e:  # the client's own, an unreachable cluster among them
            raise QumuloError(f"find_ad_objects term '{term}': {e}", term=term, **e.details) from None
        found[term] = {"found": bool(objects), "objects": objects}
    return found, errors


def _read_objects(client, kind, value):
    """The objects a term names; [] when it names none, as the object reads' 404 ad_not_found_error and the name read's []."""
    if kind == "name":
        return list(client.get(AD_OBJECTS_BY_NAME, params={"username": value}).data or [])
    route, param = (AD_OBJECT_BY_DN, "dn") if kind == "dn" else (AD_OBJECT_BY_SID, "sid")
    try:
        return [client.get(route, params={param: value}).data]
    except QumuloApiError as e:
        if e.status == 404 and e.is_("ad_not_found_error"):
            return []
        raise


def _by_sid(objects):
    """Each object once, by its SID, in the order the server gave; every AD security principal carries one."""
    seen, unique = set(), []
    for obj in objects:
        if obj.get("sid") not in seen:
            seen.add(obj.get("sid"))
            unique.append(obj)
    return unique


def _status(client):
    # One status read per task, for the joined domain and its NetBIOS name, and only once an object is found; an ad
    # subset gathered first has read it already.
    if "ad_status" not in client.memo:
        client.memo["ad_status"] = client.get(AD_STATUS).data
    return client.memo["ad_status"]


def _trustee(obj, status):
    """ad:<NETBIOS>\\<name> for an object of the joined domain; None for one of a trusted domain, or with no NetBIOS name."""
    netbios = status.get("domain_netbios")
    if not netbios or not obj.get("name") or not same_domain(_dn_domain(obj.get("dn")), status.get("domain")):
        return None
    return f"ad:{netbios}\\{obj['name']}"


def _dn_domain(dn):
    """The domain a DN's DC= components spell, such as corp.example.com; an escaped comma does not split a component."""
    parts = [part.partition("=") for part in re.split(r"(?<!\\),", dn or "")]
    return ".".join(value.strip() for attr, _sep, value in parts if attr.strip().lower() == "dc")
