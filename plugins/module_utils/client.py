# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The only HTTP path from qumulo.cluster to a Qumulo cluster's REST API."""

from __future__ import annotations

import errno
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from ansible.module_utils.basic import env_fallback
from ansible.module_utils.urls import Request
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import redact_text

try:
    from ansible.module_utils.secrets import register_secret
except ImportError:  # before ansible-core 2.22, no_log on the options is the only masking

    def register_secret(value):
        pass


def connection_argument_spec():
    def option(type_, env, **kw):
        return {"type": type_, "fallback": (env_fallback, [env]), **kw}

    return {
        "api_host": option("str", "QUMULO_API_HOST", required=True),
        "api_port": option("int", "QUMULO_API_PORT", default=8000),
        "api_username": option("str", "QUMULO_API_USERNAME", default="admin"),
        "api_password": option("str", "QUMULO_API_PASSWORD", no_log=True),
        "api_token": option("str", "QUMULO_API_TOKEN", no_log=True),
        "validate_certs": option("bool", "QUMULO_VALIDATE_CERTS", default=True),
        "ca_path": option("path", "QUMULO_CA_PATH"),
        "request_timeout": option("int", "QUMULO_REQUEST_TIMEOUT", default=60),
        "retry_timeout": option("int", "QUMULO_RETRY_TIMEOUT", default=300),
        "expected_cluster_uuid": option("str", "QUMULO_EXPECTED_CLUSTER_UUID"),
    }


class QumuloError(Exception):
    """A failure common.run() reports as fail_json(msg=str(error), **error.details)."""

    def __init__(self, msg, **details):
        super().__init__(msg)
        self.details = details


class QumuloMaybeApplied(QumuloError):
    """A write that must not be replayed lost its connection after it was sent; re-read to decide."""

    def __init__(self, msg, **details):
        super().__init__(msg, **{"changed": True, "maybe_applied": True, **details})


class QumuloUnreachable(QumuloError):
    """The cluster did not answer before the retry deadline; a caller with a deadline of its own may try again.

    Its result carries cluster_unanswered: true, so that a caller can tell a cluster that did not answer from one that
    refused. The key is not unreachable, which Ansible would take for the host's connection failing and end the host's play.
    """

    def __init__(self, msg, **details):
        super().__init__(msg, **{"cluster_unanswered": True, **details})


JSON = "application/json"
OCTET_STREAM = "application/octet-stream"
RETRYABLE_STATUS = frozenset({502, 503, 504})
FORWARDING_STATUS = frozenset({502, 504})  # a gateway answered, perhaps after forwarding the request
MAX_BACKOFF = 15.0

ERROR_HINTS = {
    "http_not_found_error": "this route does not exist on this Qumulo Core version",
    "fs_access_denied_error": "the file system's permissions deny the API user this access",
}
STATUS_HINTS = {
    403: "the API user lacks the RBAC privilege for this call",
    412: "the resource changed between read and write; re-run the task",
}


def _guard_details(error):
    """What a failed node-state read passes on through the identity guard: whether the cluster did not answer, and a 5xx
    status, so a caller can tell a cluster that is down or starting from one that refused. A 4xx is left out: the
    upgrade's reboot wait keeps waiting through a guard failure without a status below 500, as when a gateway answers
    404 while a node restarts."""
    details = {key: error.details[key] for key in ("cluster_unanswered",) if key in error.details}
    if error.details.get("status", 0) >= 500:
        details.update({key: error.details[key] for key in ("status", "error_class") if key in error.details})
    return details


class QumuloApiError(QumuloError):
    def __init__(self, method, path, status, body, hint=None, urls=()):
        """urls are full URLs to replace by their redacted form before any other URL in the description is redacted."""
        error = body if isinstance(body, dict) else {}
        self.status = status
        self.error_class = str(error.get("error_class") or "unknown")
        self.description = redact_text(str(error.get("description") or ""), *urls)
        hint = ERROR_HINTS.get(self.error_class) or hint or STATUS_HINTS.get(status)
        msg = f"{method} {path} failed: HTTP {status} {self.error_class}: {self.description}"
        super().__init__(f"{msg} ({hint})" if hint else msg, status=status, error_class=self.error_class)

    def is_(self, *error_classes):
        return self.error_class in error_classes


def term_error(error, classes):
    """A lookup term's own error, {class, description}, when error is a 400 whose class is one of classes; None otherwise."""
    if isinstance(error, QumuloApiError) and error.status == 400 and error.error_class in classes:
        return {"class": error.error_class, "description": error.description}
    return None


def term_warning(lookup, terms):
    """The one warning a lookup gives, naming its terms whose entries hold an error; None when there are none."""
    if not terms:
        return None
    had = "1 term had an error" if len(terms) == 1 else f"{len(terms)} terms had errors"
    return f"{lookup}: {had}: {', '.join(f"'{term}'" for term in terms)}"


ETAG_POLICIES = frozenset({"send", "omit", "drift"})
# The why of a POST that only reads, such as a lookup or a preview, which a route then declares replayable.
READ_ONLY_WHY = "a read sent as a POST writes nothing, so one lost to a 5xx or a dropped connection is sent again"


@dataclass(frozen=True)
class Route:
    path: str  # "/v3/smb/shares/{id}"
    methods: tuple[str, ...]
    etag: str = "send"  # send | omit (the GET ETag is unusable for writes) | drift (spurious 412s)
    replay: frozenset[str] | None = None  # methods safe to resend after a 5xx or a lost connection; GET if declared
    why: str = ""  # the observed server behaviour behind a non-default etag or a replayable write

    def __post_init__(self):
        replay = frozenset({"GET"}) & frozenset(self.methods) if self.replay is None else frozenset(self.replay)
        object.__setattr__(self, "replay", replay)
        if self.etag not in ETAG_POLICIES:
            raise ValueError(f"{self.path}: etag must be send, omit or drift, not {self.etag!r}")
        if not self.replay <= frozenset(self.methods):
            raise ValueError(f"{self.path}: replay names {', '.join(sorted(self.replay - set(self.methods)))}, which the route does not declare")
        if (self.etag != "send" or self.replay - {"GET"}) and not self.why:
            raise ValueError(f"{self.path}: a non-default etag policy or a replayable write needs a why")

    def url(self, params=None, query=None):
        # Each parameter is one path component: a file path ref keeps its slashes only as %2F.
        path = self.path.format(**{name: quote(str(value), safe="") for name, value in (params or {}).items()})
        if query:
            path += "?" + urlencode({name: _query_value(value) for name, value in query.items() if value is not None})
        return path


def _query_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


@dataclass
class Response:
    status: int
    data: object
    etag: str | None


class UrlsTransport:
    """Sends one request. Unit tests replace this class with FakeQumulo."""

    def __init__(self, params):
        self._request = Request(
            validate_certs=params["validate_certs"],
            ca_path=params.get("ca_path"),
            timeout=params["request_timeout"],
            follow_redirects="none",
            use_netrc=False,
            http_agent="qumulo.cluster",
        )

    def __call__(self, method, url, data, headers, timeout=None):
        """timeout, when given, replaces request_timeout for this request only."""
        try:
            response = self._request.open(method, url, data=data, headers=headers, **({} if timeout is None else {"timeout": timeout}))
            return response.status, _lower_keys(response.headers), response.read()
        except HTTPError as e:  # every status >= 300, since redirects are not followed
            return e.code, _lower_keys(e.headers or {}), e.read()


def base_url(host, port):
    """https://host:port, with an IPv6 address in the brackets a URL needs around it."""
    try:
        bracket = ipaddress.ip_address(host).version == 6
    except ValueError:  # a name, an IPv4 address, or an IPv6 address already in brackets
        bracket = False
    return f"https://[{host}]:{port}" if bracket else f"https://{host}:{port}"


def _lower_keys(headers):
    return {name.lower(): value for name, value in headers.items()}


LOGIN = Route("/v1/session/login", ("POST",), replay=frozenset({"POST"}), why="logging in again has no side effect")
NODE_STATE = Route("/v1/node/state", ("GET",))  # needs no authentication


class QumuloClient:
    sleep = staticmethod(time.sleep)  # unit tests replace both with a virtual clock
    clock = staticmethod(time.monotonic)

    def __init__(self, params):
        self.params = params
        self.base = base_url(params["api_host"], params["api_port"])
        self._transport = UrlsTransport(params)
        # '' counts as unset because module_defaults templates both keys for every play.
        token, password = params.get("api_token") or None, params.get("api_password") or None
        if (token is None) == (password is None):
            raise QumuloError(f"set exactly one of api_password or api_token for {params['api_host']}")
        self._token = token
        self._uses_token = token is not None  # an access token cannot be renewed by logging in
        if token:
            register_secret(token)
        self.warnings = []
        self.redact_urls = []  # full URLs a module hands over, which every error replaces exactly before any other URL
        self.memo = {}  # facts read once per module run, such as the cluster version
        self.expected_uuid = (params.get("expected_cluster_uuid") or "").strip() or None
        self._guarded = False

    def get(self, route, **kw):
        return self.request("GET", route, **kw)

    def post(self, route, **kw):
        return self.request("POST", route, **kw)

    def put(self, route, **kw):
        return self.request("PUT", route, **kw)

    def patch(self, route, **kw):
        return self.request("PATCH", route, **kw)

    def delete(self, route, **kw):
        return self.request("DELETE", route, **kw)

    def get_or_none(self, route, **kw):
        """None when the object is missing; a missing route still raises."""
        try:
            return self.get(route, **kw)
        except QumuloApiError as e:
            if e.status == 404 and not e.is_("http_not_found_error"):
                return None
            raise

    def node_state(self, retry_timeout=None):
        """(cluster_id, state) from GET /v1/node/state, which needs no login; cluster_id is "" on an UNCONFIGURED node."""
        live = self.request("GET", NODE_STATE, auth=False, retry_timeout=retry_timeout).data
        if not isinstance(live, dict) or not isinstance(live.get("cluster_id"), str):
            raise QumuloError(f"unexpected node-state response from {self.base}: {live!r}")
        return live["cluster_id"], live.get("state", "unknown")

    def request(self, method, route, *, params=None, query=None, body=None, raw=None, if_match=None, auth=True, accept=JSON, retry_timeout=None, timeout=None):
        """route is a Route, or a path the server returned (paging.next), which is sent verbatim with GET only.

        body is sent as JSON; raw, bytes such as a file a route takes as it is, is sent as application/octet-stream.

        The server answers 406 when a route does not offer the Accept type. With an accept other than JSON,
        a successful response's data is its body as text. retry_timeout overrides the connection option for
        this request, so that a caller polling against its own deadline is not held for the whole retry_timeout.
        timeout overrides request_timeout for this request only, for a request the server answers only after long work.
        """
        if isinstance(route, str):
            if params or query:
                raise ValueError(f"params and query apply to a Route, not to the server-returned path {route}")
            route, path = Route(route, ("GET",)), route
        else:
            path = route.url(params, query)
        if method not in route.methods:
            raise ValueError(f"{method} is not declared for {route.path}")
        if raw is not None and body is not None:
            raise ValueError(f"{method} {route.path}: send raw or body, not both")
        data, content_type = (raw, OCTET_STREAM) if raw is not None else (None if body is None else json.dumps(body).encode(), JSON)
        for attempt in (1, 2):
            headers = {"Accept": accept}
            if data is not None:
                headers["Content-Type"] = content_type
            if if_match is not None:
                headers["If-Match"] = if_match
            if auth:
                self._guard(retry_timeout)
                headers["Authorization"] = "Bearer " + self._bearer(retry_timeout)
            status, response_headers, response_body = self._send(method, path, data, headers, method in route.replay, retry_timeout, timeout)
            if status == 401 and auth and attempt == 1 and not self._uses_token:
                self._token = None  # session tokens expire: log in again, once
                continue
            break
        payload = _decode(response_body) if accept == JSON or status >= 300 else response_body.decode(errors="replace")
        if status >= 300:
            raise QumuloApiError(method, path, status, payload, hint=self._hint(status, response_headers), urls=self.redact_urls)
        return Response(status, payload, response_headers.get("etag"))

    def paginate(self, route, items_key, *, params=None, query=None, limit=1000):
        """All items of a paged list, as iterate() reads them."""
        return list(self.iterate(route, items_key, params=params, query=query, limit=limit))

    def iterate(self, route, items_key, *, params=None, query=None, limit=1000):
        """The items of a paged list, one page read at a time, so a caller that stops early reads no further page.

        Stops on an empty next (some lists end with "") or an empty page; fails on a repeated cursor.
        """
        url, seen = route.url(params, {**(query or {}), "limit": limit}), set()
        while url:
            if url in seen:
                raise QumuloError(f"pagination loop at {url}")
            seen.add(url)
            page = self.get(url).data
            batch = page.get(items_key) or []
            if not batch:
                break
            yield from batch
            url = (page.get("paging") or {}).get("next")

    def read_modify_write(self, route, build, *, method, params=None, first=None):
        """GET, then write build(current data) with If-Match per route.etag; build returns None when nothing changes.

        first is a Response the caller already read. A 412 re-reads and rebuilds: 3 attempts, 5 where the ETag drifts.
        """
        retries = 4 if route.etag == "drift" else 2
        current = first
        while True:
            current = current or self.get(route, params=params)
            body = build(current.data)
            if body is None:
                return None
            if_match = None if route.etag == "omit" else current.etag
            try:
                return self.request(method, route, params=params, body=body, if_match=if_match)
            except QumuloApiError as e:
                if e.status != 412 or retries == 0:
                    raise
            retries -= 1
            current = None
            self.sleep(0.5)

    def _hint(self, status, response_headers):
        if 300 <= status < 400:
            location = redact_text(response_headers.get("location", "an unnamed location"), *self.redact_urls)
            return f"redirected to {location}; set api_host to the address that serves the REST API"
        if status == 401 and self._uses_token:
            return "api_token was rejected; access tokens are bound to one cluster and do not survive a rebuild"
        if status == 401:
            return "check api_username and api_password"
        return None

    def _send(self, method, path, data, headers, replayable, retry_timeout=None, timeout=None):
        retry_timeout = self.params["retry_timeout"] if retry_timeout is None else retry_timeout
        deadline = self.clock() + retry_timeout
        delay = 1.0
        while True:
            try:
                sent = (method, self.base + path, data, headers)
                status, response_headers, raw = self._transport(*sent) if timeout is None else self._transport(*sent, timeout=timeout)
            except (OSError, HTTPException) as e:
                reason = getattr(e, "reason", e)  # URLError wraps the socket error
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise QumuloError(
                        f"{self.base}: TLS certificate verification failed ({reason}); set ca_path to the cluster's CA, "
                        "or validate_certs: false for a lab cluster"
                    ) from None
                if isinstance(reason, socket.gaierror):
                    raise QumuloError(f"{self.base}: cannot resolve {self.params['api_host']} ({reason}); check api_host") from None
                if not replayable and not _refused(reason):
                    raise QumuloMaybeApplied(
                        f"{method} {path}: the connection was lost after the request was sent; the change may or may not have been applied"
                    ) from None
                if self.clock() >= deadline:
                    raise QumuloUnreachable(f"{self.base} unreachable for {retry_timeout}s: {reason}") from None
            else:
                if not replayable and status in FORWARDING_STATUS:
                    raise QumuloMaybeApplied(f"{method} {path}: HTTP {status} from a gateway; the change may or may not have been applied")
                if not replayable or status not in RETRYABLE_STATUS or self.clock() >= deadline:
                    return status, response_headers, raw
            self.sleep(max(0.0, min(delay, MAX_BACKOFF, deadline - self.clock())))
            delay *= 2

    def _guard(self, retry_timeout=None):
        """Before the first credentials leave this process, check that api_host is the expected cluster.

        retry_timeout is the one of the request being sent, so a caller's deadline also bounds this read and the login.
        """
        if self._guarded:
            return
        if not self.expected_uuid:
            # Warned here, not when the client is made, so that a module that never logs in does not warn.
            self.warnings.append("identity guard disabled: expected_cluster_uuid is empty")
            self._guarded = True
            return
        try:
            live_id, state = self.node_state(retry_timeout)
        except QumuloApiError as e:
            raise QumuloError(f"identity guard: cannot read the node state of {self.base} ({e}); no credentials were sent", **_guard_details(e)) from None
        except QumuloError as e:
            raise QumuloError(f"identity guard: {e}; no credentials were sent", **_guard_details(e)) from None
        if live_id.lower() != self.expected_uuid.lower():
            raise QumuloError(
                f"identity guard: {self.base} is cluster '{live_id or 'UNCONFIGURED'}' (state {state}), "
                f"expected '{self.expected_uuid}'; no credentials were sent",
                live_cluster_uuid=live_id,
                expected_cluster_uuid=self.expected_uuid,
            )
        self._guarded = True

    def _bearer(self, retry_timeout=None):
        if self._token is None:
            credentials = {"username": self.params["api_username"], "password": self.params["api_password"]}
            self._token = self.request("POST", LOGIN, body=credentials, auth=False, retry_timeout=retry_timeout).data["bearer_token"]
            register_secret(self._token)
        return self._token


def _refused(reason):
    """A refused connection is the one failure that proves nothing was sent, so any method may be resent after it."""
    return isinstance(reason, ConnectionRefusedError) or (isinstance(reason, OSError) and reason.errno == errno.ECONNREFUSED)


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:  # a proxy's HTML error page, for example
        return raw.decode(errors="replace")
