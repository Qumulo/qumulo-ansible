# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The cluster area: facts about the cluster as a whole, and the checks that it is the cluster a play expects."""

from __future__ import annotations

import re
import ssl
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils import tls
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, QumuloUnreachable, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import require_continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.dirsvc import joined_domain
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Field, Settings
from ansible_collections.qumulo.cluster.plugins.module_utils.version import TESTED_FLOOR, cluster_version

SETTINGS = Route("/v1/cluster/settings", ("GET", "PUT"))
NODES = Route("/v1/cluster/nodes/", ("GET",))
LICENSE_STATUS = Route("/v1/license/status", ("GET",))
LICENSE_ENFORCEMENT = Route("/v1/license/enforcement", ("GET",))
CAPACITY_CLAMP = Route("/v1/capacity/clamp", ("GET", "PUT"))  # every PUT re-forms quorum, so it is never resent
FILE_SYSTEM = Route("/v1/file-system", ("GET",))
METRICS = Route("/v2/metrics/endpoints/default/data", ("GET",))  # offers OpenMetrics text or text/plain, never JSON
PROTECTION = Route("/v1/cluster/protection/status", ("GET",))
SPOKES = Route("/v2/portal/spokes/", ("GET", "POST"))  # portal.py creates spokes through it
HUBS = Route("/v2/portal/hubs/", ("GET",))

UNCONFIGURED = "UNCONFIGURED"
UNPROTECTED_EDGE = "unprotected_edge"
# The protection status of an object-backed cluster describes erasure coding it does not use.
OBJECT_BACKED = frozenset({"PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "PROTECTION_SYSTEM_TYPE_COLD_OBJECT_BACKED"})
# The statuses that mean this cluster does not offer a route: missing on this version (404), the method (405),
# not served in the requested type (406), or not on this product (501). Anything else is a failure to report.
NOT_OFFERED_STATUS = frozenset({404, 405, 406, 501})
INFO_SAMPLE = re.compile(r"^qumulo_info\{(?P<labels>.*)\}", re.MULTILINE)
LABEL = re.compile(r'(?P<name>[A-Za-z_][A-Za-z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')


def node_state(client):
    """client.node_state(), read once per module run, with an UNCONFIGURED node's empty cluster_id as None."""
    if "node_state" not in client.memo:
        cluster_id, state = client.node_state()
        client.memo["node_state"] = (cluster_id or None, state)
    return client.memo["node_state"]


def get_offered(client, route, **kw):
    """The data of GET route; an error that says this cluster does not offer the route raises SubsetUnavailable."""
    try:
        return client.get(route, **kw).data
    except QumuloApiError as e:
        if e.status in NOT_OFFERED_STATUS:
            raise SubsetUnavailable(str(e)) from None
        raise


def product(client):
    """The product_intent and cluster_type labels of the qumulo_info metric, and whether cluster_type is an unprotected edge.

    The labels are "unknown", and unprotected_edge None, when the metrics carry no qumulo_info sample.
    """
    match = INFO_SAMPLE.search(get_offered(client, METRICS, accept="text/plain") or "")
    labels = {m["name"]: m["value"] for m in LABEL.finditer(match["labels"])} if match else {}
    found = {name: labels.get(name, "unknown") for name in ("product_intent", "cluster_type")}
    return {**found, "unprotected_edge": None if found["cluster_type"] == "unknown" else found["cluster_type"] == UNPROTECTED_EDGE}


def _require_configured(client):
    """A subset that needs a login is unavailable on an UNCONFIGURED node, which has no users yet."""
    if node_state(client)[1] != UNCONFIGURED:
        return
    if not client.memo.get("warned_unconfigured"):
        client.memo["warned_unconfigured"] = True
        client.warnings.append(f"{client.params['api_host']} is UNCONFIGURED: no subset that needs a login can be read before a cluster is formed")
    raise SubsetUnavailable("the node is UNCONFIGURED")


def _read(client, route):
    _require_configured(client)
    return get_offered(client, route)


def bytes_or_none(value):
    """Byte counts arrive as decimal strings; an empty value means unset."""
    return None if value in (None, "") else int(value)


def _node(client):
    cluster_id, state = node_state(client)
    return {"cluster_id": cluster_id, "state": state}


def _version(client):
    version = cluster_version(client)  # served without login, even by an UNCONFIGURED node
    return {"version": str(version), "build_id": version.build_id}


def _settings(client):
    return {"cluster_name": _read(client, SETTINGS)["cluster_name"]}


def _license(client):
    return {"license": license_facts(client, _read(client, LICENSE_STATUS))}


def license_facts(client, status):
    """The facts of GET /v1/license/status's answer, leaving out the document's license_key."""
    data = status.get("license") or {}  # the license document as Nexus issued it
    try:
        enforced = get_offered(client, LICENSE_ENFORCEMENT)["license_is_enforced"]
    except SubsetUnavailable:
        enforced = None
    return {
        "state": status["state"],
        "cluster_uuid": data.get("cluster_uuid"),
        "usable_capacity_cap": bytes_or_none((data.get("details") or {}).get("usable_capacity_cap")),
        "expiration_date": data.get("expiration_date"),
        "enforced": enforced,
    }


def _capacity(client):
    return {"capacity_clamp": bytes_or_none(_read(client, CAPACITY_CLAMP)["capacity_clamp"])}


def _product(client):
    _require_configured(client)
    return {"product": product(client)}


def _filesystem(client):
    return {"filesystem": {key: bytes_or_none(value) for key, value in _read(client, FILE_SYSTEM).items() if key.endswith("_bytes")}}


def _nodes(client):
    nodes = _read(client, NODES)
    return {"node_count": len(nodes), "nodes": nodes}


def _portals(client):
    return {"portals": {"spokes": len(_read(client, SPOKES)["entries"]), "hubs": len(_read(client, HUBS)["entries"])}}


def _protection(client):
    status = _read(client, PROTECTION)
    kind = status.get("protection_system_type")
    return {"protection": {"protection_system_type": kind} if kind in OBJECT_BACKED else status}


def _upgrade(client):
    from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade  # it imports this module

    return _upgrade_read(client, "upgrade", upgrade.upgrade_facts)


def _upgrade_blocked(client):
    from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade  # it imports this module

    return _upgrade_read(client, "upgrade_blocked", upgrade.blocked_facts)


def _upgrade_read(client, name, read):
    _require_configured(client)
    try:
        return {name: read(client)}
    except QumuloApiError as e:
        if e.status in NOT_OFFERED_STATUS:
            raise SubsetUnavailable(str(e)) from None
        raise


# gather_subset name -> function of the client returning a dict of that subset's facts, in gathering order
CLUSTER_INFO_SUBSETS = {
    "node": _node,
    "version": _version,
    "settings": _settings,
    "license": _license,
    "capacity": _capacity,
    "product": _product,
    "filesystem": _filesystem,
    "nodes": _nodes,
    "portals": _portals,
    "protection": _protection,
    "upgrade": _upgrade,
    "upgrade_blocked": _upgrade_blocked,
}
DEFAULT_SUBSETS = ["node", "version", "settings", "license", "capacity", "product", "filesystem"]


FILE_ATTRIBUTES = Route("/v1/files/{ref}/info/attributes", ("GET",))
SMB_SHARES = Route("/v3/smb/shares/", ("GET",))
NFS_EXPORTS = Route("/v3/nfs/exports/", ("GET",))
ACTIVE = "ACTIVE"
FACTORY_SHARE, FACTORY_EXPORT = "Files", "/"  # what a newly formed cluster serves
POLL_INTERVAL = 5.0


class Readiness:
    """Waits, against one deadline of timeout seconds, until a cluster is ACTIVE and its file system serves requests.

    With wait=False, state() reads the node state once and root() is not called.
    """

    def __init__(self, client, timeout, wait=True):
        self.client = client
        self.host = client.params["api_host"]
        self.wait = wait
        self.timeout = timeout
        self.deadline = client.clock() + timeout

    def state(self, wanted, check):
        """Poll the node state, without logging in, until it is one of wanted; check() fails a wrong cluster at once.

        Each read is bounded by the wait's own deadline, so an unreachable API costs wait_timeout, not retry_timeout.
        Only an unreachable cluster or a server error is waited out; any other failure, such as a certificate that
        does not verify or a host that is not a Qumulo cluster, fails at once.
        """
        while True:
            try:
                cluster_id, state = self.client.node_state(retry_timeout=self.budget(POLL_INTERVAL) if self.wait else None)
            except (QumuloUnreachable, QumuloApiError) as e:
                if not self.wait or (isinstance(e, QumuloApiError) and e.status < 500):
                    raise
                last = f"unreachable ({e})"
            else:
                cluster_id = cluster_id or None
                check(cluster_id, state)
                if state in wanted or not self.wait:
                    return cluster_id, state
                last = f"in state {state}"
            if not self.pause():
                raise QumuloError(f"{self.host} is {last}, not {' or '.join(sorted(wanted))} after {self.timeout}s")

    def root(self):
        """Log in and read the root directory's attributes: the file system then serves requests."""
        while True:
            try:
                self.client.get(FILE_ATTRIBUTES, params={"ref": "/"}, retry_timeout=self.budget())
                return
            except QumuloApiError as e:
                if e.status in (401, 403):
                    raise
                error = e
            if not self.pause():
                raise QumuloError(f"{self.host} is ACTIVE but its file system is not ready after {self.timeout}s: {error}")

    def budget(self, cap=None):
        """Seconds left before the deadline, at most cap."""
        left = max(0.0, self.deadline - self.client.clock())
        return left if cap is None else min(cap, left)

    def pause(self):
        """Sleep before the next poll; False once the wait has timed out."""
        left = self.budget()
        if left <= 0:
            return False
        self.client.sleep(min(POLL_INTERVAL, left))
        return True


class Guard:
    """cluster_guard: read-only checks that fail closed. Run by common.run_resource."""

    def __init__(self, module, client):
        self.client = client
        self.params = module.params
        self.host = module.params["api_host"]
        self.wait = module.params["wait"]
        self.ready = Readiness(client, module.params["wait_timeout"], wait=self.wait)
        self.expected_id = (module.params.get("expected_cluster_uuid") or "").strip() or None
        self.new_id = (module.params.get("new_cluster_uuid") or "").strip() or None

    def run(self):
        cluster_id, state = self.match() if self.params["mode"] == "match" else self.rebuilt()
        version = cluster_version(self.client)  # served without login, even by an UNCONFIGURED node
        if version.release < TESTED_FLOOR:
            floor = ".".join(str(n) for n in TESTED_FLOOR)
            self.client.warnings.append(f"{self.host} runs Qumulo Core {version}, older than {floor}, the oldest release qumulo.cluster is tested against")
        return {"changed": False, "cluster_id": cluster_id, "state": state, "version": str(version)}

    def match(self):
        if not self.expected_id:
            raise QumuloError("mode=match needs expected_cluster_uuid")
        cluster_id, state = self.ready.state({ACTIVE}, self._check_match)
        if self.wait:
            self.ready.root()
        return cluster_id, state

    def rebuilt(self):
        cluster_id, state = self.ready.state({ACTIVE, UNCONFIGURED}, self._check_rebuilt)
        if state == UNCONFIGURED:
            return cluster_id, state
        if state != ACTIVE:
            raise self._refused(f"{self.host} is in state {state}, neither UNCONFIGURED nor ACTIVE")
        self.client.expected_uuid = self.new_id  # the client's per-request guard now checks the new cluster
        if self.wait:
            self.ready.root()
        self._check_empty_edge()
        return cluster_id, state

    def _check_match(self, cluster_id, state):
        if not _same_id(cluster_id, self.expected_id):
            raise QumuloError(
                f"identity guard: {self.host} is cluster '{cluster_id or UNCONFIGURED}' (state {state}), "
                f"expected '{self.expected_id}'; no credentials were sent",
                live_cluster_uuid=cluster_id,
                expected_cluster_uuid=self.expected_id,
            )

    def _check_rebuilt(self, cluster_id, state):
        """A node that belongs to a cluster never becomes UNCONFIGURED by waiting, so only the new cluster may pass."""
        if not cluster_id:
            return
        if not self.new_id or not self.params.get("cluster_name"):
            raise self._refused(f"{self.host} is {state} as cluster '{cluster_id}'; set new_cluster_uuid and cluster_name to the rebuilt cluster's")
        if not _same_id(cluster_id, self.new_id):
            raise self._refused(f"{self.host} is cluster '{cluster_id}', not the new cluster '{self.new_id}'; no credentials were sent")

    def _check_empty_edge(self):
        try:
            found = product(self.client)
        except SubsetUnavailable as e:
            raise self._refused(f"cannot tell whether the cluster is an unprotected edge: {e}") from None
        if found["unprotected_edge"] is None:
            raise self._refused("cannot tell whether the cluster is an unprotected edge: its metrics carry no qumulo_info sample with a cluster_type label")
        if not found["unprotected_edge"]:
            raise self._refused(f"cluster_type is '{found['cluster_type']}', not '{UNPROTECTED_EDGE}'")
        live_name, name = self.client.get(SETTINGS).data["cluster_name"], self.params["cluster_name"]
        if live_name != name:
            raise self._refused(f"cluster name is '{live_name}', not '{name}'")
        for route, kind in ((SPOKES, "spoke"), (HUBS, "hub")):
            count = len(self.client.get(route).data["entries"])
            if count:
                raise self._refused(f"the cluster has {count} {kind} portal{'s' if count > 1 else ''}; a rebuilt edge has none")
        for share in self.client.get(SMB_SHARES).data["entries"]:
            if share["share_name"] != FACTORY_SHARE:
                raise self._refused(f"the cluster has SMB share '{share['share_name']}'; a rebuilt edge has only '{FACTORY_SHARE}'")
        for export in self.client.get(NFS_EXPORTS).data["entries"]:
            if export["export_path"] != FACTORY_EXPORT:
                raise self._refused(f"the cluster has NFS export '{export['export_path']}'; a rebuilt edge has only '{FACTORY_EXPORT}'")

    @staticmethod
    def _refused(reason):
        return QumuloError(f"rebuilt: {reason}")


def _same_id(live, expected):
    return (live or "").lower() == expected.lower()


CLUSTER_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,13}[A-Za-z0-9])$")


class ClusterSettings(Settings):
    RETURN_KEY = "cluster_settings"
    ROUTE = SETTINGS
    WRITE = "PUT"
    FIELDS: ClassVar[dict[str, Field]] = {"cluster_name": Field("cluster_name")}

    def __init__(self, module, client):
        super().__init__(module, client)
        name = self.params["cluster_name"]
        if not CLUSTER_NAME.match(name):
            raise QumuloError(f"cluster_name '{name}' must be 2 to 15 letters, digits or hyphens, starting and ending with a letter or digit")

    def desired(self, before):
        after = super().desired(before)
        if after["cluster_name"] != before.get("cluster_name"):
            domain = joined_domain(self.client)
            if domain:
                raise QumuloError(
                    f"cannot rename '{before.get('cluster_name')}' to '{after['cluster_name']}' while it is joined to {domain}: "
                    "the name is its computer account; leave with ad_domain state=absent, rename, then join again"
                )
        return after


WEB_UI = Route("/v1/web-ui/settings", ("GET", "PUT"))
NANOSECONDS_PER_MINUTE = 60 * 10**9
TIMEOUT_MINUTES = (2, 600)  # the bounds the cluster enforces
BANNER_BYTES = 32 * 1024


def timeout_minutes(timeout):
    """The Web UI's {nanoseconds: "<n>"} timeout in minutes, 0 for none; a timeout set in seconds elsewhere reads as a fraction."""
    if timeout is None:
        return 0
    nanoseconds = int(timeout["nanoseconds"])
    return nanoseconds // NANOSECONDS_PER_MINUTE if nanoseconds % NANOSECONDS_PER_MINUTE == 0 else nanoseconds / NANOSECONDS_PER_MINUTE


def _wire_timeout(minutes):
    return None if minutes == 0 else {"nanoseconds": str(minutes * NANOSECONDS_PER_MINUTE)}


class WebUiSettings(Settings):
    RETURN_KEY = "web_ui_settings"
    ROUTE = WEB_UI
    WRITE = "PUT"
    # The cluster refuses an empty banner and stores none as null, so "" stands for no banner and 0 for no timeout.
    FIELDS: ClassVar[dict[str, Field]] = {
        "login_banner": Field("login_banner", read=lambda banner: banner or "", write=lambda banner: banner or None),
        "inactivity_timeout": Field("inactivity_timeout", read=timeout_minutes, write=_wire_timeout),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        minutes, banner = self.params.get("inactivity_timeout"), self.params.get("login_banner")
        low, high = TIMEOUT_MINUTES
        if minutes is not None and minutes != 0 and not low <= minutes <= high:
            raise QumuloError(f"inactivity_timeout {minutes} must be {low} to {high} minutes, or 0 for none")
        if banner is not None and len(banner.encode()) > BANNER_BYTES:
            raise QumuloError(f"login_banner is {len(banner.encode())} bytes; the cluster takes at most 32 KiB")

    def describe(self):
        return "the Web UI settings"


SSL_CIPHERS = Route("/v2/cluster/settings/ssl/ciphers", ("GET", "PUT"))
# The suite of each TLS version that the cluster adds to the end of a list leaving it out (QUIRK-060).
REQUIRED_CIPHERS = {"tls_1_2": "ECDHE-RSA-AES128-SHA256", "tls_1_3": "TLS_AES_128_GCM_SHA256"}


def stored_ciphers(version, ciphers):
    """ciphers as the cluster stores them for version: with its required suite at the end unless the list has it."""
    required = REQUIRED_CIPHERS[version]
    return list(ciphers) if required in ciphers else [*ciphers, required]


class SslCiphers(Settings):
    RETURN_KEY = "ssl_ciphers"
    ROUTE = SSL_CIPHERS
    WRITE = "PUT"
    FIELDS: ClassVar[dict[str, Field]] = {version: Field(version) for version in REQUIRED_CIPHERS}

    def __init__(self, module, client):
        super().__init__(module, client)
        for version in self.FIELDS:
            suites = self.params.get(version)
            if suites == []:
                # The cluster would store the empty list as its required suite alone (QUIRK-061).
                raise QumuloError(f"{version} is empty; list the suites to accept, or leave it unset to keep the cluster's")
            twice = next((suite for index, suite in enumerate(suites or []) if suite in suites[:index]), None)
            if twice:
                raise QumuloError(f"{version} names {twice} twice; list each suite once, in order of preference")

    def desired(self, before):
        given = {version: stored_ciphers(version, self.params[version]) for version in self.FIELDS if self.params.get(version) is not None}
        return {**before, **given}


SSL_CERTIFICATE = Route("/v2/cluster/settings/ssl/certificate", ("PUT",), etag="omit", why="no GET exists to read an ETag from")
PROBE_INTERVAL = 2.0


class SslCertificate:
    """ssl_certificate: compared by the certificate each probed address serves, since the cluster offers no read (G-SSL-READBACK)."""

    RETURN_KEY = "ssl_certificate"

    def __init__(self, module, client):
        self.module, self.client, self.params = module, client, module.params
        try:
            self.leaf = tls.leaf_sha256(self.params["certificate"])
        except ValueError:
            raise QumuloError("certificate holds no PEM certificate; give the chain in PEM, the cluster's own certificate first") from None
        if tls.self_signed_but_not_ca(self.params["certificate"]):
            client.warnings.append(
                "the certificate is self-signed but is not a certificate authority (no basicConstraints CA:TRUE), so Python 3.13 and later "
                "refuse it as ca_path; make it a CA to pin it"
            )
        self.probes = [self._address(probe) for probe in self.params["probe"]] or [(self.params["api_host"], self.params["api_port"])]

    def _address(self, probe):
        """(host, port) of host, host:port or [IPv6]:port; a bare IPv6 address names no port."""
        host, sep, port = probe.rpartition(":")
        if sep and port.isdigit() and (":" not in host or host.endswith("]")):
            return host.strip("[]"), int(port)
        return probe.strip("[]"), self.params["api_port"]

    def run(self):
        before = {f"{host}:{port}": self._read(host, port) for host, port in self.probes}
        changed = any(served != self.leaf for served in before.values())
        served = before
        if changed:
            self._check_continuity()
            served = {address: self.leaf for address in before}
            if not self.module.check_mode:
                self._install()
                served = self._wait()
        result = {"changed": changed, self.RETURN_KEY: {"sha256": self.leaf, "served": served}}
        if self.module._diff:
            result["diff"] = {"before": before, "after": {address: self.leaf for address in before}}
        return result

    def _read(self, host, port):
        try:
            return tls.sha256(tls.served_leaf(host, port, self.params["request_timeout"]))
        except (OSError, ssl.SSLError) as e:
            raise QumuloError(f"cannot read the certificate {host}:{port} serves: {e}") from None

    def _check_continuity(self):
        if not self.params["validate_certs"]:
            return
        host = self.params["api_host"]
        error = tls.chain_error(self.params["certificate"], self.params["private_key"], host, self.params.get("ca_path"))
        if error:
            require_continuity(
                self.params,
                f"the new certificate would not verify against ca_path for {host} ({error})",
                "connect later tasks with ca_path set to the new chain's certificate authority",
            )

    def _install(self):
        body = {"certificate": self.params["certificate"], "private_key": self.params["private_key"]}
        try:
            self.client.put(SSL_CERTIFICATE, body=body)
        except QumuloMaybeApplied:
            pass  # the wait below shows whether the cluster took it

    def _wait(self):
        """Probe until every address serves the new certificate; the cluster reloads it a moment after the PUT."""
        deadline = self.client.clock() + self.params["wait_timeout"]
        while True:
            served = {}
            for host, port in self.probes:
                try:
                    served[f"{host}:{port}"] = tls.sha256(tls.served_leaf(host, port, self.params["request_timeout"]))
                except (OSError, ssl.SSLError) as e:
                    served[f"{host}:{port}"] = f"unreachable ({e})"
            stale = sorted(address for address, digest in served.items() if digest != self.leaf)
            if not stale:
                return served
            left = deadline - self.client.clock()
            if left <= 0:
                raise QumuloError(
                    f"installed the certificate, but {', '.join(stale)} still serves {served[stale[0]]} after {self.params['wait_timeout']}s",
                    changed=True,
                    served=served,
                )
            self.client.sleep(min(PROBE_INTERVAL, left))


EDGE_CREATE = Route("/v2/cluster/unprotected-edge/create", ("POST",))


def with_admin_password(module):
    """The create request needs no login; afterwards the module logs in as admin, so api_password defaults to admin_password."""
    if not module.params.get("api_password") and not module.params.get("api_token"):
        module.params["api_password"] = module.params["admin_password"]
    return module


class EdgeCreate:
    """unprotected_edge_create: forms an edge from an UNCONFIGURED node; every formation mints a new UUID (G-UUID)."""

    RETURN_KEY = "unprotected_edge"

    def __init__(self, module, client):
        self.module, self.client, self.params = module, client, module.params
        self.host = self.params["api_host"]
        self.name = self.params["cluster_name"]
        if self.params["eula_accepted"] is not True:
            raise QumuloError("eula_accepted must be true: forming a cluster accepts Qumulo's end user license agreement")
        if not CLUSTER_NAME.match(self.name):
            raise QumuloError(f"cluster_name '{self.name}' must be 2 to 15 letters, digits or hyphens, starting and ending with a letter or digit")
        self.ready = Readiness(client, self.params["wait_timeout"], wait=self.params["wait"])
        self.expected_id = (self.params.get("expected_cluster_uuid") or "").strip() or None

    def run(self):
        cluster_id, state = self.ready.state({ACTIVE, UNCONFIGURED}, lambda cluster_id, state: None)
        if state == ACTIVE:
            return self._existing(cluster_id)
        if state != UNCONFIGURED:
            raise QumuloError(f"{self.host} is in state {state}; a cluster can be formed only from an UNCONFIGURED node")
        result = {"changed": True, self.RETURN_KEY: {"cluster_id": None, "cluster_name": self.name, "state": UNCONFIGURED}}
        if self.module._diff:
            result["diff"] = {"before": {"state": UNCONFIGURED}, "after": {"cluster_name": self.name, "state": ACTIVE}}
        if self.module.check_mode:
            return result
        lost = self._create()
        if self.params["wait"]:
            try:
                cluster_id, state = self._formed(lost)
                self._ready(cluster_id)
            except QumuloError as e:
                e.details.setdefault("changed", True)
                raise
            result[self.RETURN_KEY] = {"cluster_id": cluster_id, "cluster_name": self.name, "state": state}
        return result

    def _existing(self, cluster_id):
        """An ACTIVE node: unchanged when it is the cluster this task would have formed."""
        live_name = self.client.get(SETTINGS, auth=False).data["cluster_name"]  # served without login
        if live_name != self.name:
            raise QumuloError(f"{self.host} is already ACTIVE as cluster '{cluster_id}' named '{live_name}', not '{self.name}'")
        if self.expected_id and not _same_id(cluster_id, self.expected_id):
            raise QumuloError(
                f"{self.host} is ACTIVE as cluster '{cluster_id}', but expected_cluster_uuid is '{self.expected_id}'; no credentials were sent. "
                "To adopt a cluster formed elsewhere, check it with cluster_guard mode=rebuilt and set its UUID",
                live_cluster_uuid=cluster_id,
                expected_cluster_uuid=self.expected_id,
            )
        if self.params["wait"]:
            self._ready(cluster_id)
        return {"changed": False, self.RETURN_KEY: {"cluster_id": cluster_id, "cluster_name": self.name, "state": ACTIVE}}

    def _create(self):
        """Send the create once; True when its connection was lost, so whether the node received it is unknown."""
        body = {"eula_accepted": True, "name": self.name, "admin_password": self.params["admin_password"]}
        try:
            self.client.post(EDGE_CREATE, body=body, auth=False)
        except QumuloMaybeApplied:
            if not self.params["wait"]:
                raise
            return True  # never sent twice: the wait shows whether the node formed
        return False

    def _formed(self, lost):
        try:
            return self.ready.state({ACTIVE}, lambda cluster_id, state: None)
        except QumuloError as e:
            if not lost:
                raise
            raise QumuloError(
                f"{e}; the connection was lost as the create was sent, so the node may never have received it, and running the task again is safe",
                **{**e.details, "maybe_applied": True},
            ) from None

    def _ready(self, cluster_id):
        self.client.expected_uuid = cluster_id  # the identity guard now checks the cluster this node belongs to
        self.ready.root()
