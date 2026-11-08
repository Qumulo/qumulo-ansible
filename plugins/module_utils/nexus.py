# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The only HTTP path from qumulo.cluster to Qumulo's Nexus service, and the Nexus calls the modules make.

Nexus authenticates a user's API token as a bearer token and wraps every answer in
{"success": ..., "code": ..., "data": ...}. Unit tests replace NexusTransport with FakeNexus,
so they never reach Nexus.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
from http.client import HTTPException
from urllib.parse import urlsplit

from ansible.module_utils.basic import env_fallback
from ansible_collections.qumulo.cluster.plugins.module_utils.client import (
    FORWARDING_STATUS,
    MAX_BACKOFF,
    RETRYABLE_STATUS,
    QumuloError,
    QumuloMaybeApplied,
    QumuloUnreachable,
    Route,
    UrlsTransport,
    _refused,
)

try:
    from ansible.module_utils.secrets import register_secret
except ImportError:  # before ansible-core 2.22, no_log on the options is the only masking

    def register_secret(value):
        pass


NEXUS_URL = "https://api.nexus.qumulo.com"
JSON = "application/json"

CLUSTERS = Route("/api/v1/clusters", ("GET",))  # ?cluster_uuid= filters to one cluster
CLUSTER_STATUS = Route(
    "/api/v1/clusters/{uuid}/status",
    ("PATCH",),
    replay=frozenset({"PATCH"}),
    why="Nexus answers a PATCH to DECOMMISSIONED on an already decommissioned cluster without a change",
)
FLEET_CONNECTION = Route("/api/v1/clusters/{uuid}/fleet/connection", ("GET",))
API_TOKENS = Route(
    "/api/v1/api_tokens",
    ("POST",),
    replay=frozenset({"POST"}),
    why="a mint creates a single-use join key that nothing has stored yet, so a key whose answer was lost is never used",
)

STATUS_HINTS = {
    401: "nexus_api_token was rejected; it may have expired or been revoked",
    403: "nexus_api_token's Nexus role does not allow this call",
}
MAX_MESSAGE = 200


def nexus_argument_spec():
    """The options of a module that calls Nexus. Each module says whether it needs the token."""
    return {
        "nexus_api_token": {"type": "str", "no_log": True, "fallback": (env_fallback, ["QUMULO_NEXUS_API_TOKEN"])},
        "nexus_api_url": {"type": "str", "default": NEXUS_URL, "fallback": (env_fallback, ["QUMULO_NEXUS_API_URL"])},
    }


class NexusApiError(QumuloError):
    """status is the HTTP status; code is the envelope's, when Nexus sent a different one, as it does inside a 2xx.
    retried_for is how long the call was sent again on a 502, 503 or 504, when it was."""

    def __init__(self, method, path, status, body, code=None, retried_for=None):
        self.status = status
        code = None if code == status else code
        message = _message(body)
        hint = STATUS_HINTS.get(code or status)
        if retried_for is not None and code is None and status in RETRYABLE_STATUS:
            hint = f"the gateway in front of Nexus, or Nexus itself, failed every try for {retried_for}s; retry_timeout sets how long to keep trying"
        where = f"HTTP {status}" if code is None else f"HTTP {status} (Nexus code {code})"
        text = f"Nexus {method} {path} failed: {where}: {message}"
        super().__init__(f"{text} ({hint})" if hint else text, status=status)


def _message(body):
    if isinstance(body, dict):
        for key in ("description", "message", "error"):
            if body.get(key):
                return str(body[key])
        return ""
    text = str(body or "").strip()
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "..."


class NexusTransport(UrlsTransport):
    """Sends one request to Nexus. Unit tests replace this class with FakeNexus."""


class NexusClient:
    sleep = staticmethod(time.sleep)  # unit tests replace both with a virtual clock
    clock = staticmethod(time.monotonic)

    def __init__(self, params):
        token = params.get("nexus_api_token") or None
        if token is None:
            raise QumuloError("set nexus_api_token, or the QUMULO_NEXUS_API_TOKEN environment variable, to a Nexus user's API token")
        url = (params.get("nexus_api_url") or NEXUS_URL).rstrip("/")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            raise QumuloError(f"nexus_api_url must be an https URL such as {NEXUS_URL}, not {url!r}; the token is never sent over plain HTTP")
        register_secret(token)
        self._token = token
        self.base = url
        self.retry_timeout = params["retry_timeout"]
        # Nexus has a publicly trusted certificate: the cluster's validate_certs and ca_path do not apply to it.
        self._transport = NexusTransport({**params, "validate_certs": True, "ca_path": None})

    def get(self, route, **kw):
        return self.request("GET", route, **kw)

    def post(self, route, **kw):
        return self.request("POST", route, **kw)

    def patch(self, route, **kw):
        return self.request("PATCH", route, **kw)

    def request(self, method, route, *, params=None, query=None, body=None):
        """The data inside Nexus's envelope, or the whole body when it carries none."""
        if method not in route.methods:
            raise ValueError(f"{method} is not declared for {route.path}")
        path = route.url(params, query)
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": JSON, "Authorization": "Bearer " + self._token}
        if data is not None:
            headers["Content-Type"] = JSON
        replayable = method in route.replay
        status, _headers, raw = self._send(method, path, data, headers, replayable)
        payload = _decode(raw)
        if status >= 300:
            raise NexusApiError(method, path, status, payload, retried_for=self.retry_timeout if replayable else None)
        if isinstance(payload, str):
            raise NexusApiError(method, path, status, f"the answer is not JSON: {payload}")
        if isinstance(payload, dict) and payload.get("success") is False:
            raise NexusApiError(method, path, status, payload, code=payload.get("code"))
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def _send(self, method, path, data, headers, replayable):
        """client.QumuloClient._send's retry policy, with messages that name Nexus and its options."""
        deadline, delay = self.clock() + self.retry_timeout, 1.0
        while True:
            try:
                status, response_headers, raw = self._transport(method, self.base + path, data, headers)
            except (OSError, HTTPException) as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise QumuloError(f"{self.base}: TLS certificate verification failed ({reason}); check nexus_api_url") from None
                if isinstance(reason, socket.gaierror):
                    raise QumuloError(f"cannot resolve the host of {self.base} ({reason}); check nexus_api_url") from None
                if not replayable and not _refused(reason):
                    raise QumuloMaybeApplied(
                        f"Nexus {method} {path}: the connection was lost after the request was sent; the change may or may not have been applied"
                    ) from None
                if self.clock() >= deadline:
                    raise QumuloUnreachable(f"Nexus at {self.base} unreachable for {self.retry_timeout}s: {reason}") from None
            else:
                if not replayable and status in FORWARDING_STATUS:
                    raise QumuloMaybeApplied(f"Nexus {method} {path}: HTTP {status} from a gateway; the change may or may not have been applied")
                if not replayable or status not in RETRYABLE_STATUS or self.clock() >= deadline:
                    return status, response_headers, raw
            self.sleep(max(0.0, min(delay, MAX_BACKOFF, deadline - self.clock())))
            delay *= 2


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:  # a proxy's HTML error page, for example
        return raw.decode(errors="replace")


def _rows(data):
    """A list answer, whether Nexus sent it bare or as one page ({"data": [...], "meta": ...})."""
    if isinstance(data, dict):
        data = data.get("data")
    return data if isinstance(data, list) else []


def find_cluster(nexus, cluster_uuid):
    """Nexus's row for the cluster, or None when Nexus has none for that UUID."""
    for row in _rows(nexus.get(CLUSTERS, query={"cluster_uuid": cluster_uuid})):
        if str(row.get("cluster_uuid", "")).lower() == cluster_uuid.lower():
            return row
    return None


def mint_join_key(nexus, cluster_uuid, account_id=None):
    """A single-use join key (a CLUSTER_OTP token) for registering the cluster. Every call mints a new one."""
    body = {"token_type": "CLUSTER_OTP", "description": f"qumulo.cluster: register cluster {cluster_uuid}"}
    if account_id is not None:
        body["account_id"] = account_id
    key = (nexus.post(API_TOKENS, body=body) or {}).get("otp_code")
    if not key:
        raise QumuloError("Nexus returned no join key (otp_code) for the CLUSTER_OTP token it was asked to mint")
    register_secret(key)
    return key
