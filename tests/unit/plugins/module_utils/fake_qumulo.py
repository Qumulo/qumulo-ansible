# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""FakeQumulo stands in for client.UrlsTransport in unit tests.

Scripted exchanges must arrive in order, and any request that is neither the next scripted
one nor a background call fails the test; that is how a test proves no write was sent.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import client

ANY = object()  # the expectation does not check this part of the request
NO_HEADER = object()  # the request must not carry this header
CLUSTER_ID = "6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11"


@dataclass
class Expectation:
    method: str
    path: str
    body: object = ANY
    if_match: object = ANY
    status: int = 200
    response: object = None
    etag: str | None = None
    headers: dict | None = None  # further response headers, such as location
    raises: BaseException | None = None  # a transport error to raise instead of answering

    def __str__(self):
        return f"{self.method} {self.path}"


@dataclass
class Exchange:
    method: str
    path: str
    body: object
    headers: dict = field(repr=False)
    background: bool
    netloc: str = ""  # the host and port the request went to, as the URL wrote them
    timeout: float | None = None  # the request's own timeout, when the client gave one; None for request_timeout


class FakeQumulo:
    def __init__(self, version="7.10.3", cluster_id=CLUSTER_ID, state="ACTIVE", build_id=None):
        self.script = deque()
        self.log = []
        self.params = None
        self.node_state = {"node_id": 1, "cluster_id": cluster_id, "state": state}
        self.version = {
            "revision_id": f"Qumulo Core {version}",
            "build_id": build_id or f"build-{version}",
            "flavor": "release",
            "build_date": "2026-01-01T00:00:00Z",
        }
        self.identities = []  # POST /v1/identity/find returns the first entry matching every request field
        # (request body, status, error body): the find answers that body with that error, before fake.identities
        self.find_errors = []
        self.logins = 0
        self.state = {}  # GETs serve() answers in any order and as often as asked, by canonical path
        self.unread = {}  # served paths no request has read yet, which verify() reports unless they are optional

    def serve(self, path, response, status=200, optional=False, method="GET", body=ANY):
        """Answer every GET of path with response, in any order, as a cluster whose state does not change would.

        method names another method only for a read-only query the API takes as a POST, such as an ACL explanation;
        body, when given, is the only body that query may carry, and any other fails the test.
        verify() fails on a served path no request read, unless it is optional, as in a state several tests share.
        """
        key = _state_key(path, method)
        self.state[key] = (status, response, body)
        if not optional:
            self.unread[key] = path
        return self

    def expect(self, method, path, *, body=ANY, if_match=ANY, status=200, response=None, etag=None, headers=None, raises=None):
        """response may be bytes, sent verbatim; anything else is sent as JSON."""
        self.script.append(Expectation(method, path, body, if_match, status, response, etag, headers, raises))
        return self

    def transport(self, params):
        """Replaces the UrlsTransport class: called with the client's params, returns the sender."""
        self.params = params
        return self

    def __call__(self, method, url, data, headers, timeout=None):
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        # A raw body (application/octet-stream) is kept as its bytes; every other body is JSON.
        body = (data if headers.get("Content-Type") == "application/octet-stream" else json.loads(data)) if data else None
        head = self.script[0] if self.script else None
        if head and _same_request(head, method, path):
            self.log.append(Exchange(method, path, body, dict(headers), background=False, netloc=parts.netloc, timeout=timeout))
            return self._play(self.script.popleft(), body, headers)
        answer = self._background(method, path, body)
        if answer:
            self.log.append(Exchange(method, path, body, dict(headers), background=True, netloc=parts.netloc, timeout=timeout))
            return answer
        expected = f"next expected {head}" if head else "the script is empty"
        if any(_same_request(e, method, path) for e in self.script):
            pytest.fail(f"out-of-order request {method} {path}; {expected}")
        pytest.fail(f"unexpected request {method} {path}; {expected}")

    def verify(self):
        if self.script:
            pytest.fail("unused expectations: " + ", ".join(f"{e} -> {e.raises or e.status}" for e in self.script))
        if self.unread:
            pytest.fail("served paths nothing read: " + ", ".join(sorted(self.unread.values())))

    def _play(self, expectation, body, headers):
        if expectation.body is not ANY and body != expectation.body:
            pytest.fail(f"{expectation}: expected body {expectation.body!r}, got {body!r}")
        sent = headers.get("If-Match")
        if expectation.if_match is NO_HEADER and sent is not None:
            pytest.fail(f"{expectation}: expected no If-Match, got {sent!r}")
        if expectation.if_match not in (ANY, NO_HEADER) and sent != expectation.if_match:
            pytest.fail(f"{expectation}: expected If-Match {expectation.if_match!r}, got {sent!r}")
        if expectation.raises is not None:
            raise expectation.raises
        return _answer(expectation.status, expectation.response, expectation.etag, expectation.headers)

    def _background(self, method, path, body):
        if (method, path) == ("GET", "/v1/node/state"):
            return _answer(200, self.node_state)
        if (method, path) == ("GET", "/v1/version"):
            # Answered in every node state: the server serves it without login, even on an UNCONFIGURED node.
            return _answer(200, self.version)
        if (method, path) == ("POST", "/v1/session/login"):
            self.logins += 1
            return _answer(200, {"bearer_token": f"session-{self.logins}"})
        if (method, path) == ("POST", "/v1/identity/find"):
            for request, status, response in self.find_errors:
                if body == request:
                    return _answer(status, response)
            query = _well_known_query(body)
            for identity in self.identities:
                if all(identity.get(k) == v for k, v in query.items()):
                    return _answer(200, identity)
            return _answer(404, {"error_class": "api_identity_name_not_found_error", "description": f"no identity matches {body}"})
        if _state_key(path, method) in self.state:
            self.unread.pop(_state_key(path, method), None)
            status, response, served_body = self.state[_state_key(path, method)]
            if served_body is not ANY and body != served_body:
                pytest.fail(f"{method} {path}: expected body {served_body!r}, got {body!r}")
            return _answer(status, response)
        return None


# The AD names the server answers with a well-known identity joined to a domain or not, in any case, as 7.11.0 does.
WELL_KNOWN_AD_NAMES = {"authenticated users": "S-1-5-11", "nt authority\\authenticated users": "S-1-5-11"}


def _well_known_query(body):
    """The find of a well-known AD name as the find of its SID; any other find as it came."""
    name = body.get("name")
    sid = WELL_KNOWN_AD_NAMES.get(name.casefold()) if body.get("domain") == "ACTIVE_DIRECTORY" and isinstance(name, str) else None
    return {"domain": "ACTIVE_DIRECTORY", "sid": sid} if sid else body


def _same_request(expectation, method, path):
    """Query parameters match in any order."""
    return expectation.method == method and _canonical(expectation.path) == _canonical(path)


def _canonical(path):
    parts = urlsplit(path)
    return parts.path, sorted(parse_qsl(parts.query, keep_blank_values=True))


def _state_key(path, method="GET"):
    found, query = _canonical(path)
    return method, found, tuple(query)


def _answer(status, body, etag=None, extra_headers=None):
    headers = {**(extra_headers or {}), **({} if etag is None else {"etag": etag})}
    if body is None or isinstance(body, bytes):  # bytes are sent verbatim, as a proxy's HTML error page would be
        return status, headers, body or b""
    return status, headers, json.dumps(body).encode()


def fake_api_session(monkeypatch):
    """Body of the fake_api fixture: yields a factory, then verifies every fake it made."""
    fakes = []

    def make(**kwargs):
        fake = FakeQumulo(**kwargs)
        monkeypatch.setattr(client, "UrlsTransport", fake.transport)
        fakes.append(fake)
        return fake

    yield make
    for fake in fakes:
        fake.verify()


def client_params(**overrides):
    """Connection parameters as AnsibleModule would fill them in, for QumuloClient(params)."""
    base = {name: option.get("default") for name, option in client.connection_argument_spec().items()}
    return {**base, "api_host": "qumulo.example.com", **overrides}
