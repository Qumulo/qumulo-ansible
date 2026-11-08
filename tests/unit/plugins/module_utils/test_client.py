# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

import errno
import io
import socket
import ssl
from email.message import Message
from typing import ClassVar
from urllib.error import HTTPError, URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import client
from ansible_collections.qumulo.cluster.plugins.module_utils.client import (
    QumuloApiError,
    QumuloClient,
    QumuloError,
    QumuloMaybeApplied,
    Response,
    Route,
)
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER, client_params

THING = Route("/v1/thing", ("GET",))


@pytest.fixture
def registered(monkeypatch):
    """The values the client registers as secrets."""
    values = []
    monkeypatch.setattr(client, "register_secret", values.append)
    return values


def test_login_posts_credentials_and_sends_bearer(fake_api, registered):
    fake = fake_api().expect("GET", "/v1/thing", response={"x": 1})

    response = QumuloClient(client_params(api_password="secret")).get(THING)

    assert response.data == {"x": 1}
    login, get = fake.log
    assert (login.method, login.path, login.body) == ("POST", "/v1/session/login", {"username": "admin", "password": "secret"})
    assert "Authorization" not in login.headers
    assert get.headers["Authorization"] == "Bearer session-1"
    assert registered == ["session-1"]


def test_access_token_skips_login(fake_api, registered):
    fake = fake_api().expect("GET", "/v1/thing")

    QumuloClient(client_params(api_token="access-1", api_password="")).get(THING)

    [get] = fake.log
    assert get.headers["Authorization"] == "Bearer access-1"
    assert registered == ["access-1"]


@pytest.mark.parametrize(
    ("api_password", "api_token"),
    [("secret", "access-1"), (None, None), ("", ""), ("", None)],
)
def test_both_or_neither_credential_fails(fake_api, api_password, api_token):
    fake = fake_api()

    with pytest.raises(QumuloError, match="exactly one of api_password or api_token"):
        QumuloClient(client_params(api_password=api_password, api_token=api_token))

    assert fake.log == []


@pytest.mark.parametrize(
    ("status", "body", "hint"),
    [
        (404, {"error_class": "http_not_found_error", "description": "no route"}, "this route does not exist on this Qumulo Core version"),
        (403, {"error_class": "api_access_denied_error", "description": "denied"}, "the API user lacks the RBAC privilege for this call"),
        (
            403,
            {"error_class": "fs_access_denied_error", "description": "denied={ FS_ACCESS_READ_ATTR }"},
            "the file system's permissions deny the API user this access",
        ),
    ],
    ids=["a missing route", "an RBAC denial", "a file system permission denial"],
)
def test_error_body_maps_to_error_class_and_hint(fake_api, status, body, hint):
    fake_api().expect("GET", "/v1/thing", status=status, response=body)

    with pytest.raises(QumuloApiError) as caught:
        QumuloClient(client_params(api_token="access-1")).get(THING)

    error = caught.value
    assert (error.status, error.error_class, error.description) == (status, body["error_class"], body["description"])
    assert str(error) == f"GET /v1/thing failed: HTTP {status} {body['error_class']}: {body['description']} ({hint})"
    assert error.details == {"status": status, "error_class": body["error_class"]}


def test_error_body_that_is_not_json_has_unknown_error_class(fake_api):
    fake_api().expect("GET", "/v1/thing", status=500, response=b"<html>Internal Server Error</html>")

    with pytest.raises(QumuloApiError, match=r"^GET /v1/thing failed: HTTP 500 unknown: $") as caught:
        QumuloClient(client_params(api_token="access-1")).get(THING)

    assert caught.value.error_class == "unknown"


def test_redirect_fails_naming_the_location(fake_api):
    fake_api().expect("GET", "/v1/thing", status=301, headers={"location": "https://other.example.com/v1/thing"})

    with pytest.raises(QumuloApiError, match=r"HTTP 301 .*redirected to https://other\.example\.com/v1/thing; set api_host") as caught:
        QumuloClient(client_params(api_token="access-1")).get(THING)

    assert caught.value.status == 301


@pytest.mark.parametrize(
    ("credential", "hint"),
    [
        ({"api_token": "access-1"}, "api_token was rejected; access tokens are bound to one cluster"),
        ({"api_password": "wrong"}, "check api_username and api_password"),
    ],
)
def test_401_hint_names_the_credential_in_use(fake_api, credential, hint):
    rejected = {"error_class": "api_authentication_failed", "description": "rejected"}
    path = "/v1/thing" if "api_token" in credential else "/v1/session/login"
    fake_api().expect("POST" if "api_password" in credential else "GET", path, status=401, response=rejected)

    with pytest.raises(QumuloApiError, match="HTTP 401") as caught:
        QumuloClient(client_params(**credential)).get(THING)

    assert hint in str(caught.value)


def test_get_or_none_distinguishes_missing_object_from_missing_route(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/thing", status=404, response={"error_class": "fs_no_such_entry_error", "description": "gone"})
    fake.expect("GET", "/v1/thing", status=404, response={"error_class": "http_not_found_error", "description": "no route"})
    qumulo = QumuloClient(client_params(api_token="access-1"))

    assert qumulo.get_or_none(THING) is None
    with pytest.raises(QumuloApiError, match="http_not_found_error"):
        qumulo.get_or_none(THING)


def test_undeclared_method_raises_before_any_request(fake_api):
    fake = fake_api()

    with pytest.raises(ValueError, match="DELETE is not declared for /v1/thing"):
        QumuloClient(client_params(api_token="access-1")).delete(THING)

    assert fake.log == []


def test_empty_success_body_decodes_to_none(fake_api):
    fake_api().expect("GET", "/v1/thing", status=200)

    assert QumuloClient(client_params(api_token="access-1")).get(THING).data is None


def test_accept_defaults_to_json_and_a_text_accept_returns_the_text(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/thing", response={"a": 1})
    fake.expect("GET", "/v1/thing", response=b'{"a": 1}')
    api = QumuloClient(client_params(api_token="access-1"))

    as_json = api.get(THING).data
    as_text = api.get(THING, accept="text/plain").data

    assert (as_json, as_text) == ({"a": 1}, '{"a": 1}')
    assert [e.headers["Accept"] for e in fake.log] == ["application/json", "text/plain"]


FILE = Route("/v1/file", ("PUT",))


def test_a_raw_body_is_sent_as_its_bytes_with_the_octet_stream_type(fake_api):
    fake = fake_api()
    fake.expect("PUT", "/v1/file", body=b"\x05\x02 not json", response={"stored": True})

    answer = QumuloClient(client_params(api_token="access-1")).put(FILE, raw=b"\x05\x02 not json")

    assert answer.data == {"stored": True}
    assert fake.log[0].headers["Content-Type"] == "application/octet-stream"


def test_a_raw_body_is_sent_again_whole_after_a_401_and_a_new_login(fake_api):
    """The one resend of a write the client makes; the bytes it sends are the request's, not the 401's body."""
    fake = fake_api()
    fake.expect("PUT", "/v1/file", body=b"\x05\x02 keytab", status=401, response={"error_class": "session_expired"})
    fake.expect("PUT", "/v1/file", body=b"\x05\x02 keytab", response={"stored": True})

    answer = QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)).put(FILE, raw=b"\x05\x02 keytab")

    puts = [e for e in fake.log if e.method == "PUT"]
    assert answer.data == {"stored": True}
    assert [(e.body, e.headers["Content-Type"]) for e in puts] == [(b"\x05\x02 keytab", "application/octet-stream")] * 2
    assert fake.logins == 2


def test_a_raw_body_with_a_json_body_is_refused_before_any_request(fake_api):
    fake = fake_api()

    with pytest.raises(ValueError, match="raw or body"):
        QumuloClient(client_params(api_token="access-1")).put(FILE, raw=b"x", body={"x": 1})

    assert fake.log == []


def test_error_body_is_decoded_whatever_the_accept(fake_api):
    fake_api().expect("GET", "/v1/thing", status=400, response={"error_class": "bad_thing", "description": "no"})

    with pytest.raises(QumuloApiError) as raised:
        QumuloClient(client_params(api_token="access-1")).get(THING, accept="text/plain")

    assert raised.value.error_class == "bad_thing"


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ({"methods": ("GET",), "etag": "sometimes"}, "etag must be send, omit or drift"),
        ({"methods": ("GET",), "replay": frozenset({"GET", "PUT"}), "why": "x"}, "replay names PUT, which the route does not declare"),
        ({"methods": ("GET", "PATCH"), "etag": "omit"}, "needs a why"),
        ({"methods": ("GET", "POST"), "replay": frozenset({"GET", "POST"})}, "needs a why"),
    ],
)
def test_route_rejects_an_inconsistent_declaration(declaration, message):
    with pytest.raises(ValueError, match=message):
        Route("/v1/thing", **declaration)


def test_route_coerces_replay_to_a_frozenset():
    assert Route("/v1/thing", ("GET", "DELETE"), replay={"GET", "DELETE"}, why="x").replay == frozenset({"GET", "DELETE"})
    assert isinstance(Route("/v1/thing", ("GET",), replay=["GET"]).replay, frozenset)


def test_route_replays_get_only_when_declared():
    assert Route("/v1/thing", ("GET", "PUT")).replay == frozenset({"GET"})
    assert Route("/v1/thing", ("POST",)).replay == frozenset()


OTHER_ID = "0b6c7c51-2d5e-4b8c-8f60-4c1f7e2a9d03"


def test_guard_mismatch_fails_before_login(fake_api):
    fake = fake_api(cluster_id=OTHER_ID)

    with pytest.raises(QumuloError) as caught:
        QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)).get(THING)

    assert str(caught.value) == (
        f"identity guard: https://qumulo.example.com:8000 is cluster '{OTHER_ID}' (state ACTIVE), expected '{CLUSTER_ID}'; no credentials were sent"
    )
    assert caught.value.details == {"live_cluster_uuid": OTHER_ID, "expected_cluster_uuid": CLUSTER_ID}
    assert [(e.method, e.path) for e in fake.log] == [("GET", "/v1/node/state")]
    assert "Authorization" not in fake.log[0].headers


@pytest.mark.parametrize("body", [["not", "a", "dict"], {"state": "ACTIVE"}, {"cluster_id": 7}])
def test_guard_fails_cleanly_on_a_malformed_node_state(fake_api, body):
    fake = fake_api()
    fake.node_state = body

    with pytest.raises(QumuloError, match=r"identity guard: unexpected node-state response from https://qumulo\.example\.com:8000"):
        QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)).get(THING)

    assert [e.path for e in fake.log] == ["/v1/node/state"]


def test_guard_reports_unknown_state_when_the_body_has_none(fake_api):
    fake_api().node_state = {"cluster_id": OTHER_ID}

    with pytest.raises(QumuloError, match=rf"is cluster '{OTHER_ID}' \(state unknown\)"):
        QumuloClient(client_params(api_token="access-1", expected_cluster_uuid=CLUSTER_ID)).get(THING)


def test_guard_strips_whitespace_from_the_expected_id(fake_api):
    fake = fake_api().expect("GET", "/v1/thing")

    QumuloClient(client_params(api_token="access-1", expected_cluster_uuid=f"  {CLUSTER_ID}\n")).get(THING)

    assert [e.path for e in fake.log] == ["/v1/node/state", "/v1/thing"]


def test_guard_reports_a_failed_node_state_read_as_a_guard_failure(fake_api):
    fake = fake_api().expect("GET", "/v1/node/state", status=500, response={"error_class": "internal_error", "description": "boom"})

    with pytest.raises(
        QumuloError, match=r"^identity guard: cannot read the node state of https://qumulo\.example\.com:8000 .*; no credentials were sent$"
    ) as caught:
        QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)).get(THING)

    assert not isinstance(caught.value, QumuloApiError)
    assert caught.value.details == {"status": 500, "error_class": "internal_error"}, "a caller tells a refusal from no answer by the status"
    assert [e.path for e in fake.log] == ["/v1/node/state"]


def test_guard_passes_on_no_4xx_status_of_the_node_state_read(fake_api):
    # cluster_upgrade's reboot wait keeps waiting through a guard failure whose status is not below 500, as when a gateway
    # answers 404 while a node restarts; a 4xx status passed on would end that wait.
    fake_api().expect("GET", "/v1/node/state", status=404, response={"error_class": "http_not_found_error", "description": "gateway"})

    with pytest.raises(QumuloError, match=r"^identity guard: cannot read the node state") as caught:
        QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)).get(THING)

    assert caught.value.details == {}


def test_guard_compares_cluster_ids_ignoring_case(fake_api):
    fake = fake_api(cluster_id=CLUSTER_ID.upper()).expect("GET", "/v1/thing")

    QumuloClient(client_params(api_token="access-1", expected_cluster_uuid=CLUSTER_ID)).get(THING)

    assert [e.path for e in fake.log] == ["/v1/node/state", "/v1/thing"]


def test_guard_unconfigured_message(fake_api):
    fake_api(cluster_id="", state="UNCONFIGURED")

    with pytest.raises(QumuloError, match=rf"is cluster 'UNCONFIGURED' \(state UNCONFIGURED\), expected '{CLUSTER_ID}'"):
        QumuloClient(client_params(api_token="access-1", expected_cluster_uuid=CLUSTER_ID)).get(THING)


def test_guard_match_checks_node_state_once_before_login(fake_api):
    fake = fake_api().expect("GET", "/v1/thing").expect("GET", "/v1/thing")
    qumulo = QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID))

    qumulo.get(THING)
    qumulo.get(THING)

    assert [e.path for e in fake.log] == ["/v1/node/state", "/v1/session/login", "/v1/thing", "/v1/thing"]
    assert qumulo.warnings == []


@pytest.mark.parametrize("expected", ["", None])
def test_empty_expected_uuid_warns(fake_api, expected):
    fake = fake_api().expect("GET", "/v1/thing")
    qumulo = QumuloClient(client_params(api_token="access-1", expected_cluster_uuid=expected))

    qumulo.get(THING)

    assert qumulo.warnings == ["identity guard disabled: expected_cluster_uuid is empty"]
    assert [e.path for e in fake.log] == ["/v1/thing"]


def test_empty_expected_uuid_does_not_warn_until_credentials_are_sent(fake_api):
    fake_api()
    qumulo = QumuloClient(client_params(api_password="secret", expected_cluster_uuid=""))

    qumulo.node_state()

    assert qumulo.warnings == []


def test_node_state_returns_the_cluster_id_and_state_without_login(fake_api):
    fake = fake_api(cluster_id="", state="UNCONFIGURED")

    assert QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)).node_state() == ("", "UNCONFIGURED")
    assert [(e.path, "Authorization" in e.headers) for e in fake.log] == [("/v1/node/state", False)]


def test_node_state_refuses_a_malformed_response(fake_api):
    fake_api().expect("GET", "/v1/node/state", response={"state": "ACTIVE"})

    with pytest.raises(QumuloError, match=r"^unexpected node-state response from https://qumulo\.example\.com:8000: \{'state': 'ACTIVE'\}$"):
        QumuloClient(client_params(api_token="t")).node_state()


def test_node_state_takes_a_retry_timeout(fake_api, fake_clock):
    fake = fake_api()
    fake.expect("GET", "/v1/node/state", raises=REFUSED)
    fake.expect("GET", "/v1/node/state", raises=REFUSED)

    with pytest.raises(QumuloError, match=r"unreachable for 1s"):
        QumuloClient(client_params(api_token="t")).node_state(retry_timeout=1)


WRITABLE = Route("/v1/thing", ("GET", "PUT", "POST", "DELETE"))
REPLAYABLE_DELETE = Route("/v1/thing", ("GET", "DELETE"), replay=frozenset({"GET", "DELETE"}), why="test")
RESET = URLError(ConnectionResetError(104, "Connection reset by peer"))
REFUSED = URLError(ConnectionRefusedError(111, "Connection refused"))
UNAVAILABLE = {"error_class": "service_unavailable", "description": "quorum is forming"}


def test_get_retried_on_503_until_retry_timeout(fake_api, fake_clock):
    fake = fake_api()
    for _attempt in range(6):  # at t = 0, 1, 3, 7, 15 and 30
        fake.expect("GET", "/v1/thing", status=503, response=UNAVAILABLE)

    with pytest.raises(QumuloApiError) as caught:
        QumuloClient(client_params(api_token="t", retry_timeout=30)).get(THING)

    assert caught.value.status == 503
    assert fake_clock.sleeps == [1, 2, 4, 8, 15]


def test_get_retried_on_gateway_errors_and_lost_connections(fake_api, fake_clock):
    fake = fake_api()
    fake.expect("GET", "/v1/thing", status=502)
    fake.expect("GET", "/v1/thing", raises=RESET)
    fake.expect("GET", "/v1/thing", raises=TimeoutError("timed out"))
    fake.expect("GET", "/v1/thing", status=504)
    fake.expect("GET", "/v1/thing", raises=REFUSED)
    fake.expect("GET", "/v1/thing", response={"x": 1})

    assert QumuloClient(client_params(api_token="t")).get(THING).data == {"x": 1}
    assert fake_clock.sleeps == [1, 2, 4, 8, 15]


def test_unreachable_until_retry_timeout_fails(fake_api, fake_clock):
    fake = fake_api()
    for _attempt in range(3):  # at t = 0, 1 and 3
        fake.expect("GET", "/v1/thing", raises=REFUSED)

    with pytest.raises(QumuloError, match=r"https://qumulo.example.com:8000 unreachable for 3s: .*Connection refused"):
        QumuloClient(client_params(api_token="t", retry_timeout=3)).get(THING)


def test_retry_timeout_override_bounds_one_request(fake_api, fake_clock):
    fake = fake_api()
    for _attempt in range(3):  # at t = 0, 1 and 3
        fake.expect("GET", "/v1/thing", raises=REFUSED)

    with pytest.raises(QumuloError, match=r"unreachable for 3s"):
        QumuloClient(client_params(api_token="t", retry_timeout=300)).get(THING, retry_timeout=3)

    assert fake_clock.now == 3


def test_unreachable_has_its_own_error_class(fake_api, fake_clock):
    fake_api().expect("GET", "/v1/thing", raises=REFUSED)

    with pytest.raises(client.QumuloUnreachable, match=r"unreachable for 0s") as caught:
        QumuloClient(client_params(api_token="t")).get(THING, retry_timeout=0)

    assert caught.value.details == {"cluster_unanswered": True}


@pytest.mark.parametrize(("method", "path"), [("GET", "/v1/node/state"), ("POST", "/v1/session/login")])
def test_retry_timeout_bounds_the_identity_check_and_the_login(fake_api, fake_clock, method, path):
    fake = fake_api()
    for _attempt in range(3):  # at t = 0, 1 and 3
        fake.expect(method, path, raises=REFUSED)

    with pytest.raises(QumuloError, match=r"unreachable for 3s") as caught:
        QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID, retry_timeout=300)).get(THING, retry_timeout=3)

    assert fake_clock.now == 3
    assert caught.value.details["cluster_unanswered"] is True, "the identity check keeps the mark that the cluster did not answer"


@pytest.mark.parametrize("lost", [RESET, TimeoutError("timed out")])
def test_put_not_replayed_after_lost_connection_raises_maybe_applied(fake_api, fake_clock, lost):
    fake_api().expect("PUT", "/v1/thing", body={"x": 1}, raises=lost)

    with pytest.raises(
        QumuloMaybeApplied, match=r"PUT /v1/thing: the connection was lost after the request was sent; the change may or may not have been applied"
    ) as caught:
        QumuloClient(client_params(api_token="t")).put(WRITABLE, body={"x": 1})

    assert caught.value.details == {"changed": True, "maybe_applied": True}
    assert fake_clock.sleeps == []


def test_write_not_retried_on_503_unless_route_allows_replay(fake_api, fake_clock):
    fake = fake_api()
    fake.expect("PUT", "/v1/thing", status=503, response=UNAVAILABLE)
    fake.expect("DELETE", "/v1/thing", raises=RESET)
    fake.expect("DELETE", "/v1/thing", status=503, response=UNAVAILABLE)
    fake.expect("DELETE", "/v1/thing")
    qumulo = QumuloClient(client_params(api_token="t"))

    with pytest.raises(QumuloApiError, match="HTTP 503"):
        qumulo.put(WRITABLE, body={})
    assert fake_clock.sleeps == []
    assert qumulo.delete(REPLAYABLE_DELETE).status == 200
    assert fake_clock.sleeps == [1, 2]


def test_post_retried_only_when_nothing_was_sent(fake_api, fake_clock):
    fake = fake_api()
    fake.expect("POST", "/v1/thing", raises=REFUSED)
    fake.expect("POST", "/v1/thing", raises=URLError(OSError(errno.ECONNREFUSED, "Connection refused")))
    fake.expect("POST", "/v1/thing", response={"id": "1"})
    fake.expect("POST", "/v1/thing", status=503, response=UNAVAILABLE)
    fake.expect("POST", "/v1/thing", raises=RESET)
    qumulo = QumuloClient(client_params(api_token="t"))

    assert qumulo.post(WRITABLE, body={}).data == {"id": "1"}
    assert fake_clock.sleeps == [1, 2]
    with pytest.raises(QumuloApiError, match="HTTP 503"):
        qumulo.post(WRITABLE, body={})
    with pytest.raises(QumuloMaybeApplied):
        qumulo.post(WRITABLE, body={})
    assert fake_clock.sleeps == [1, 2]


@pytest.mark.parametrize("code", [errno.EHOSTUNREACH, errno.ENETUNREACH])
def test_unreachable_host_on_a_write_may_have_been_applied(fake_api, fake_clock, code):
    fake_api().expect("POST", "/v1/thing", raises=URLError(OSError(code, "unreachable")))

    with pytest.raises(QumuloMaybeApplied, match="the change may or may not have been applied"):
        QumuloClient(client_params(api_token="t")).post(WRITABLE, body={})

    assert fake_clock.sleeps == []


@pytest.mark.parametrize("status", [502, 504])
def test_gateway_error_on_a_write_may_have_been_applied(fake_api, fake_clock, status):
    fake_api().expect("PUT", "/v1/thing", status=status)

    with pytest.raises(QumuloMaybeApplied, match=rf"PUT /v1/thing: HTTP {status} from a gateway; the change may or may not have been applied"):
        QumuloClient(client_params(api_token="t")).put(WRITABLE, body={})

    assert fake_clock.sleeps == []


def test_dns_failure_fails_at_once(fake_api, fake_clock):
    fake_api().expect("GET", "/v1/thing", raises=URLError(socket.gaierror(-2, "Name or service not known")))

    with pytest.raises(QumuloError, match=r"cannot resolve qumulo\.example\.com .*; check api_host$"):
        QumuloClient(client_params(api_token="t")).get(THING)

    assert fake_clock.sleeps == []


@pytest.mark.parametrize(
    ("api_host", "netloc"),
    [
        ("fd00::5", "[fd00::5]:8000"),
        ("[fd00::5]", "[fd00::5]:8000"),
        ("10.20.0.5", "10.20.0.5:8000"),
        ("qumulo.example.com", "qumulo.example.com:8000"),
    ],
    ids=["bare IPv6 bracketed", "bracketed IPv6 kept", "IPv4 unchanged", "name unchanged"],
)
def test_requests_go_to_api_host_with_an_ipv6_address_in_brackets(fake_api, api_host, netloc):
    fake = fake_api().expect("GET", "/v1/thing")

    QumuloClient(client_params(api_host=api_host, api_token="t")).get(THING)

    [get] = fake.log
    assert get.netloc == netloc


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_certificate_error_fails_at_once(fake_api, fake_clock, method):
    error = URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate"))
    fake_api().expect(method, "/v1/thing", raises=error)

    with pytest.raises(QumuloError, match=r"TLS certificate verification failed .*set ca_path") as caught:
        QumuloClient(client_params(api_token="t")).request(method, WRITABLE)

    assert not isinstance(caught.value, QumuloMaybeApplied)
    assert fake_clock.sleeps == []


def test_401_relogin_once_for_session_tokens(fake_api):
    expired = {"error_class": "api_session_expired_error", "description": "expired"}
    fake = fake_api()
    fake.expect("PUT", "/v1/thing", status=401, response=expired)
    fake.expect("PUT", "/v1/thing", response={"x": 1})
    fake.expect("GET", "/v1/thing", status=401, response=expired)
    fake.expect("GET", "/v1/thing", status=401, response=expired)
    fake.expect("GET", "/v1/thing", status=401, response={"error_class": "api_auth_error", "description": "bad token"})
    qumulo = QumuloClient(client_params(api_password="secret"))

    assert qumulo.put(WRITABLE, body={"x": 1}).data == {"x": 1}
    with pytest.raises(QumuloApiError, match="HTTP 401"):
        qumulo.get(THING)

    assert [(e.path, e.headers.get("Authorization")) for e in fake.log] == [
        ("/v1/session/login", None),
        ("/v1/thing", "Bearer session-1"),
        ("/v1/session/login", None),
        ("/v1/thing", "Bearer session-2"),
        ("/v1/thing", "Bearer session-2"),
        ("/v1/session/login", None),
        ("/v1/thing", "Bearer session-3"),
    ]

    with pytest.raises(QumuloApiError, match="access tokens are bound to one cluster"):
        QumuloClient(client_params(api_token="t")).get(THING)
    assert fake.log[-1].headers["Authorization"] == "Bearer t"
    assert fake.logins == 3


def test_login_retried_after_lost_connection(fake_api, fake_clock):
    fake = fake_api()
    fake.expect("POST", "/v1/session/login", raises=RESET)
    fake.expect("POST", "/v1/session/login", status=503, response=UNAVAILABLE)
    fake.expect("GET", "/v1/thing")

    QumuloClient(client_params(api_password="secret")).get(THING)

    assert fake_clock.sleeps == [1, 2]
    assert fake.log[-1].headers["Authorization"] == "Bearer session-1"


QUOTAS = Route("/v1/files/quotas/", ("GET",))
PRECONDITION_FAILED = {"error_class": "http_precondition_failed_error", "description": "etag mismatch"}


# QUIRK-004: quota lists end with an empty-string next, possibly after an empty page.
def test_paginate_stops_on_empty_string_next_and_empty_page(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/files/quotas/?limit=2", response={"quotas": [{"id": "1"}, {"id": "2"}], "paging": {"next": "/v1/files/quotas/?after=2&limit=2"}})
    fake.expect("GET", "/v1/files/quotas/?after=2&limit=2", response={"quotas": [{"id": "3"}], "paging": {"next": ""}})
    fake.expect("GET", "/v1/files/quotas/?limit=2", response={"quotas": [{"id": "1"}], "paging": {"next": "/v1/files/quotas/?after=1&limit=2"}})
    fake.expect("GET", "/v1/files/quotas/?after=1&limit=2", response={"quotas": [], "paging": {"next": "/v1/files/quotas/?after=9&limit=2"}})
    qumulo = QumuloClient(client_params(api_token="t"))

    assert qumulo.paginate(QUOTAS, "quotas", limit=2) == [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    assert qumulo.paginate(QUOTAS, "quotas", limit=2) == [{"id": "1"}]


def test_iterate_reads_a_page_only_when_its_items_are_wanted(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/files/quotas/?limit=2", response={"quotas": [{"id": "1"}, {"id": "2"}], "paging": {"next": "/v1/files/quotas/?after=2&limit=2"}})

    items = QumuloClient(client_params(api_token="t")).iterate(QUOTAS, "quotas", limit=2)

    assert [next(items), next(items)] == [{"id": "1"}, {"id": "2"}]
    assert [e.path for e in fake.log if not e.background] == ["/v1/files/quotas/?limit=2"]


def test_paginate_refuses_repeated_cursor(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/files/quotas/?limit=1000", response={"quotas": [{"id": "1"}], "paging": {"next": "/v1/files/quotas/?after=1"}})
    fake.expect("GET", "/v1/files/quotas/?after=1", response={"quotas": [{"id": "2"}], "paging": {"next": "/v1/files/quotas/?after=1"}})

    with pytest.raises(QumuloError, match=r"pagination loop at /v1/files/quotas/\?after=1"):
        QumuloClient(client_params(api_token="t")).paginate(QUOTAS, "quotas")


@pytest.mark.parametrize(("etag_policy", "attempts"), [("send", 3), ("drift", 5)])
def test_rmw_gives_up_after_3_attempts_or_5_on_drift_routes(fake_api, fake_clock, etag_policy, attempts):
    route = Route("/v1/settings", ("GET", "PUT"), etag=etag_policy, why="test")
    fake = fake_api()
    for n in range(attempts):
        fake.expect("GET", "/v1/settings", response={"x": n}, etag=f'"{n}"')
        fake.expect("PUT", "/v1/settings", body={"x": n + 100}, if_match=f'"{n}"', status=412, response=PRECONDITION_FAILED)

    with pytest.raises(QumuloApiError) as caught:
        QumuloClient(client_params(api_token="t")).read_modify_write(route, lambda data: {"x": data["x"] + 100}, method="PUT")

    assert caught.value.status == 412
    assert len(fake_clock.sleeps) == attempts - 1


def test_rmw_retries_after_412_and_skips_the_write_when_nothing_changes(fake_api):
    route = Route("/v1/settings", ("GET", "PATCH"))
    fake = fake_api()
    fake.expect("GET", "/v1/settings", response={"x": 1}, etag='"1"')
    fake.expect("PATCH", "/v1/settings", if_match='"1"', status=412, response=PRECONDITION_FAILED)
    fake.expect("GET", "/v1/settings", response={"x": 1}, etag='"2"')
    fake.expect("PATCH", "/v1/settings", body={"x": 2}, if_match='"2"', response={"x": 2}, etag='"3"')
    fake.expect("GET", "/v1/settings", response={"x": 2}, etag='"3"')
    qumulo = QumuloClient(client_params(api_token="t"))

    def build(data):
        return None if data["x"] == 2 else {"x": 2}

    assert qumulo.read_modify_write(route, build, method="PATCH").etag == '"3"'
    assert qumulo.read_modify_write(route, build, method="PATCH") is None


def test_rmw_omits_if_match_for_omit_routes(fake_api):
    route = Route("/v1/settings", ("GET", "PATCH"), etag="omit", why="test")
    current = Response(200, {"x": 1}, '"1"')
    fake = fake_api()
    fake.expect("PATCH", "/v1/settings", body={"x": 2}, if_match=NO_HEADER)

    QumuloClient(client_params(api_token="t")).read_modify_write(route, lambda data: {"x": 2}, method="PATCH", first=current)

    assert [e.method for e in fake.log] == ["PATCH"]


def test_path_ref_is_one_percent_encoded_component(fake_api):
    route = Route("/v1/files/{ref}/info/attributes", ("GET", "POST"))
    fake = fake_api()
    fake.expect("GET", "/v1/files/%2Fhome%2Fa%20b%2F%3F%25/info/attributes")
    fake.expect("POST", "/v1/files/7/info/attributes?allow-fs-path-create=false&name=x%2Fy")

    qumulo = QumuloClient(client_params(api_token="t"))
    qumulo.get(route, params={"ref": "/home/a b/?%"})
    qumulo.post(route, params={"ref": 7}, query={"allow-fs-path-create": False, "skip": None, "name": "x/y"})


def test_server_returned_path_takes_no_params_or_query(fake_api):
    fake = fake_api()

    with pytest.raises(ValueError, match="params and query apply to a Route"):
        QumuloClient(client_params(api_token="t")).get("/v1/files/quotas/?after=1", query={"limit": 2})

    assert fake.log == []


def test_connection_argument_spec_defaults_secrets_and_env_fallbacks():
    spec = client.connection_argument_spec()

    assert spec["api_host"]["required"] is True
    assert spec["api_port"]["default"] == 8000
    assert spec["api_username"]["default"] == "admin"
    assert spec["validate_certs"]["default"] is True
    assert (spec["request_timeout"]["default"], spec["retry_timeout"]["default"]) == (60, 300)
    assert spec["api_password"]["no_log"] is True
    assert spec["api_token"]["no_log"] is True
    assert not {"username", "password", "access_token"} & set(spec)  # free for resource options such as a user's password
    for name, option in spec.items():
        assert option["fallback"][1] == [f"QUMULO_{name.upper()}"], name


class FakeRequest:
    """Stands in for ansible.module_utils.urls.Request."""

    outcomes: ClassVar[list] = []  # what each open() returns, or raises when it is an exception

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    opened: ClassVar[list] = []  # the keyword arguments of each open()

    def open(self, method, url, data=None, headers=None, **kwargs):
        self.opened.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeHttpResponse:
    status = 200

    def __init__(self):
        self.headers = {"ETag": '"7"', "Content-Type": "application/json"}

    def read(self):
        return b'{"x": 1}'


def test_urls_transport_returns_status_lowercase_headers_and_body(monkeypatch):
    monkeypatch.setattr(client, "Request", FakeRequest)
    transport = client.UrlsTransport(client_params(api_token="t", validate_certs=False, ca_path="/ca.pem", request_timeout=5))

    assert transport._request.kwargs == {
        "validate_certs": False,
        "ca_path": "/ca.pem",
        "timeout": 5,
        "follow_redirects": "none",
        "use_netrc": False,
        "http_agent": "qumulo.cluster",
    }

    FakeRequest.outcomes.append(FakeHttpResponse())
    assert transport("GET", "https://h:8000/v1/x", None, {}) == (200, {"etag": '"7"', "content-type": "application/json"}, b'{"x": 1}')

    headers = Message()
    headers["ETag"] = '"8"'
    FakeRequest.outcomes.append(HTTPError("https://h:8000/v1/x", 412, "Precondition Failed", headers, io.BytesIO(b'{"error_class": "e"}')))
    assert transport("PUT", "https://h:8000/v1/x", b"{}", {}) == (412, {"etag": '"8"'}, b'{"error_class": "e"}')


UPGRADE = Route("/v2/upgrade/prepare", ("POST",))


def test_a_requests_own_timeout_reaches_the_transport_for_that_request_only(fake_api):
    fake = fake_api().expect("POST", "/v2/upgrade/prepare").expect("GET", "/v1/thing")
    api = QumuloClient(client_params(api_token="access-1"))

    api.post(UPGRADE, body={"image_path": "/upgrade.qimg"}, timeout=2700)
    api.get(THING)

    assert [(e.path, e.timeout) for e in fake.log if not e.background] == [("/v2/upgrade/prepare", 2700), ("/v1/thing", None)]


def test_the_transport_keeps_request_timeout_unless_a_request_gives_its_own(monkeypatch):
    monkeypatch.setattr(client, "Request", FakeRequest)
    FakeRequest.opened.clear()
    transport = client.UrlsTransport(client_params(api_token="t", request_timeout=60))

    FakeRequest.outcomes.extend([FakeHttpResponse(), FakeHttpResponse()])
    transport("POST", "https://h:8000/v2/upgrade/prepare", b"{}", {}, timeout=4500)
    transport("GET", "https://h:8000/v1/x", None, {})

    assert transport._request.kwargs["timeout"] == 60
    assert FakeRequest.opened == [{"timeout": 4500}, {}]


def test_an_error_that_quotes_a_url_quotes_it_redacted(fake_api):
    url = "https://user:pw@mirror.example.com/qumulo_core_7.11.0.qimg?X-Amz-Signature=abc"
    fake_api().expect(
        "POST", "/v2/upgrade/prepare", status=500, response={"error_class": "os_error_ENOENT", "description": f"exec([ verify.py, --path, {url} ])"}
    )

    with pytest.raises(QumuloApiError) as caught:
        QumuloClient(client_params(api_token="access-1")).post(UPGRADE, body={"image_path": url})

    assert "X-Amz-Signature" not in str(caught.value) and "user:pw" not in str(caught.value)
    assert "https://mirror.example.com/qumulo_core_7.11.0.qimg" in str(caught.value)
    assert "X-Amz-Signature" not in caught.value.description


def test_an_error_replaces_a_url_the_module_handed_over_exactly_before_any_other(fake_api):
    url = "https://mirror.example.com/qumulo_core_7.11.0.qimg?sig=ab,cd"
    fake_api().expect("POST", "/v2/upgrade/prepare", status=500, response={"error_class": "upgrade_error", "description": f"Download from {url},retrying"})
    qumulo = QumuloClient(client_params(api_token="access-1"))
    qumulo.redact_urls.append(url)

    with pytest.raises(QumuloApiError) as caught:
        qumulo.post(UPGRADE, body={"image_path": url})

    assert caught.value.description == "Download from https://mirror.example.com/qumulo_core_7.11.0.qimg,retrying"
    assert "sig=" not in str(caught.value) and "cd" not in caught.value.description


@pytest.mark.parametrize(
    ("url", "redacted"),
    [
        ("https://mirror.example.com/qumulo_core_7.11.0.qimg?sig=ab,cd", "https://mirror.example.com/qumulo_core_7.11.0.qimg"),
        ("https://user:p(w)@mirror.example.com/qumulo_core_7.11.0.qimg", "https://mirror.example.com/qumulo_core_7.11.0.qimg"),
        ("https://[::1]:8443/qumulo_core_7.11.0.qimg?q=1", "https://[::1]:8443/qumulo_core_7.11.0.qimg"),
    ],
)
def test_an_error_quotes_a_url_it_was_not_handed_redacted_whatever_its_secret_or_host_holds(fake_api, url, redacted):
    fake_api().expect(
        "POST", "/v2/upgrade/prepare", status=500, response={"error_class": "os_error_ENOENT", "description": f"exec([ verify.py, --path, {url} ])"}
    )

    with pytest.raises(QumuloApiError) as caught:
        QumuloClient(client_params(api_token="access-1")).post(UPGRADE, body={"image_path": url})

    assert caught.value.description == f"exec([ verify.py, --path, {redacted} ])"


def test_a_redirects_location_is_quoted_redacted(fake_api):
    fake_api().expect("GET", "/v1/thing", status=302, response=None, headers={"location": "https://u:p@elsewhere.example.com/v1/thing?token=1"})

    with pytest.raises(QumuloApiError) as caught:
        QumuloClient(client_params(api_token="access-1")).get(THING)

    assert "redirected to https://elsewhere.example.com/v1/thing;" in str(caught.value)
    assert "token=1" not in str(caught.value) and "u:p" not in str(caught.value)


TERM_CLASSES = frozenset({"decode_error"})


def test_a_400_of_a_terms_class_is_that_terms_error():
    e = QumuloApiError("GET", "/v1/files/x/info/attributes", 400, {"error_class": "decode_error", "description": "cannot decode 'x'"})

    assert client.term_error(e, TERM_CLASSES) == {"class": "decode_error", "description": "cannot decode 'x'"}


@pytest.mark.parametrize(
    ("status", "error_class"),
    [(400, "api_bad_request_error"), (401, "decode_error"), (403, "decode_error"), (404, "decode_error"), (409, "decode_error"), (503, "decode_error")],
    ids=["a 400 of another class", "401", "403", "404", "409", "503"],
)
def test_any_other_api_error_is_not_a_terms_error(status, error_class):
    e = QumuloApiError("GET", "/v1/files/x/info/attributes", status, {"error_class": error_class, "description": "test"})

    assert client.term_error(e, TERM_CLASSES) is None


def test_an_error_that_is_not_an_api_error_is_not_a_terms_error():
    assert client.term_error(QumuloError("unreachable"), TERM_CLASSES) is None


@pytest.mark.parametrize(
    ("terms", "warning"),
    [
        ([], None),
        (["sid:S-1-5-21-9-9-9-1104"], "find_trustees: 1 term had an error: 'sid:S-1-5-21-9-9-9-1104'"),
        (["sid:S-1-5-21-9-9-9-1104", "ad:CORP\\alice"], "find_trustees: 2 terms had errors: 'sid:S-1-5-21-9-9-9-1104', 'ad:CORP\\alice'"),
    ],
    ids=["no term", "one term", "two terms"],
)
def test_a_lookup_warns_once_naming_its_terms_with_errors(terms, warning):
    assert client.term_warning("find_trustees", terms) == warning
