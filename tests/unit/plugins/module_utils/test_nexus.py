# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The Nexus client seam, driven through FakeNexus. Nexus is never called from unit tests."""

from __future__ import annotations

import ast
import socket
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils import client, nexus
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.nexus import NexusApiError, NexusClient
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_nexus import ACCOUNT_ID, NEXUS_TOKEN, nexus_params, ok
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

ROOT = Path(__file__).resolve().parents[4]
CLUSTERS = f"/api/v1/clusters?cluster_uuid={CLUSTER_ID}"
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))
REFUSED = URLError(ConnectionRefusedError(111, "Connection refused"))


@pytest.fixture
def registered(monkeypatch):
    values = []
    monkeypatch.setattr(nexus, "register_secret", values.append)
    return values


def test_request_sends_the_token_as_bearer_and_unwraps_the_envelope(fake_nexus, registered):
    fake = fake_nexus().expect("GET", CLUSTERS, response=ok([{"cluster_uuid": CLUSTER_ID}]))

    data = NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert data == [{"cluster_uuid": CLUSTER_ID}]
    [get] = fake.log
    assert get.headers["Authorization"] == f"Bearer {NEXUS_TOKEN}"
    assert get.headers["Accept"] == "application/json"
    assert registered == [NEXUS_TOKEN]


def test_default_url_is_production_nexus(fake_nexus):
    fake = fake_nexus().expect("GET", CLUSTERS, response=ok([]))

    NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert fake.urls == [f"https://api.nexus.qumulo.com{CLUSTERS}"]
    assert fake.params["validate_certs"] is True
    assert fake.params["ca_path"] is None


def test_nexus_is_always_verified_whatever_the_cluster_connection_says(fake_nexus):
    fake = fake_nexus().expect("GET", CLUSTERS, response=ok([]))

    NexusClient(nexus_params(validate_certs=False, ca_path="/cluster/ca.pem")).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert (fake.params["validate_certs"], fake.params["ca_path"]) == (True, None)


def test_a_trailing_slash_on_the_url_is_dropped(fake_nexus):
    fake = fake_nexus().expect("GET", CLUSTERS, response=ok([]))

    client_ = NexusClient(nexus_params(nexus_api_url="https://nexus.example.com/"))
    client_.get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert client_.base == "https://nexus.example.com"
    assert fake.log[0].path == CLUSTERS


@pytest.mark.parametrize("url", ["http://api.nexus.qumulo.com", "api.nexus.qumulo.com", "https://"])
def test_url_that_is_not_https_fails_before_any_request(fake_nexus, url):
    fake = fake_nexus()

    with pytest.raises(QumuloError, match="nexus_api_url must be an https URL"):
        NexusClient(nexus_params(nexus_api_url=url))

    assert fake.log == []


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_fails_naming_the_option_and_variable(fake_nexus, token):
    with pytest.raises(QumuloError, match="nexus_api_token.*QUMULO_NEXUS_API_TOKEN"):
        NexusClient(nexus_params(nexus_api_token=token))


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"message": "token expired"}, "HTTP 401: token expired (nexus_api_token was rejected; it may have expired or been revoked)"),
        (403, {"description": "not allowed"}, "HTTP 403: not allowed (nexus_api_token's Nexus role does not allow this call)"),
        (404, {"error": "no such cluster"}, "HTTP 404: no such cluster"),
        (500, {"success": False, "code": 500}, "HTTP 500: "),
    ],
)
def test_error_message_names_the_call_status_message_and_hint(fake_nexus, status, body, expected):
    fake_nexus().expect("GET", CLUSTERS, status=status, response=body)

    with pytest.raises(NexusApiError) as raised:
        NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert str(raised.value) == f"Nexus GET {CLUSTERS} failed: {expected}"
    assert raised.value.status == status


def test_a_body_that_is_not_json_is_reported_trimmed(fake_nexus):
    fake_nexus().expect("GET", CLUSTERS, status=502, response=b"<html>" + b"x" * 600)

    with pytest.raises(NexusApiError) as raised:
        NexusClient(nexus_params(retry_timeout=0)).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert "x" * 194 + "... (" in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [{"success": False, "code": 400, "message": "bad filter"}, {"success": False, "code": 400, "message": "bad filter", "data": None}],
    ids=["no_data", "with_data"],
)
def test_a_2xx_envelope_that_says_no_success_is_an_error(fake_nexus, body):
    """The HTTP status stays the one Nexus sent; the envelope's code is reported beside it."""
    fake_nexus().expect("GET", CLUSTERS, response=body)

    with pytest.raises(NexusApiError) as raised:
        NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert str(raised.value) == f"Nexus GET {CLUSTERS} failed: HTTP 200 (Nexus code 400): bad filter"
    assert raised.value.status == 200


def test_a_2xx_answer_that_is_not_json_is_an_error(fake_nexus):
    """A proxy's HTML page is not an answer from Nexus, so it never reads as an empty list or a missing row."""
    fake_nexus().expect("GET", CLUSTERS, response=b"<html>sign in</html>")

    with pytest.raises(NexusApiError) as raised:
        NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})

    assert str(raised.value) == f"Nexus GET {CLUSTERS} failed: HTTP 200: the answer is not JSON: <html>sign in</html>"


def test_a_response_without_an_envelope_is_returned_whole(fake_nexus):
    fake_nexus().expect("GET", CLUSTERS, response=[{"cluster_uuid": CLUSTER_ID}])

    assert NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID}) == [{"cluster_uuid": CLUSTER_ID}]


def test_get_retried_on_503_and_lost_connections_until_it_answers(fake_nexus, fake_clock):
    fake = fake_nexus()
    fake.expect("GET", CLUSTERS, status=503, response={"message": "busy"})
    fake.expect("GET", CLUSTERS, raises=LOST)
    fake.expect("GET", CLUSTERS, response=ok([]))

    assert NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID}) == []
    assert fake_clock.sleeps == [pytest.approx(1, rel=0.25), pytest.approx(2, rel=0.25)]


def test_get_gives_up_at_retry_timeout(fake_nexus, fake_clock):
    fake = fake_nexus()
    for _attempt in range(3):
        fake.expect("GET", CLUSTERS, raises=LOST)

    with pytest.raises(QumuloError, match=r"Nexus at https://api.nexus.qumulo.com unreachable for 3s"):
        NexusClient(nexus_params(retry_timeout=3)).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})


# A Nexus call that applies something and must not be sent twice. No route the collection uses is one today.
WRITE = Route("/api/v1/example", ("POST",))


def test_a_write_is_never_resent_after_a_lost_connection(fake_nexus, fake_clock):
    fake_nexus().expect("POST", WRITE.path, raises=LOST)

    with pytest.raises(QumuloMaybeApplied, match="may or may not have been applied"):
        NexusClient(nexus_params()).post(WRITE)


def test_a_write_is_resent_when_the_connection_was_refused(fake_nexus, fake_clock):
    fake = fake_nexus()
    fake.expect("POST", WRITE.path, raises=REFUSED)
    fake.expect("POST", WRITE.path, response=ok({}))

    assert NexusClient(nexus_params()).post(WRITE) == {}


@pytest.mark.parametrize("status", [502, 504])
def test_a_gateway_error_on_a_write_may_have_been_applied(fake_nexus, status):
    fake_nexus().expect("POST", WRITE.path, status=status, response=b"")

    with pytest.raises(QumuloMaybeApplied, match=f"HTTP {status} from a gateway"):
        NexusClient(nexus_params()).post(WRITE)


def test_a_write_answered_503_is_not_resent(fake_nexus):
    fake_nexus().expect("POST", WRITE.path, status=503, response={"message": "busy"})

    with pytest.raises(NexusApiError) as raised:
        NexusClient(nexus_params()).post(WRITE)

    assert str(raised.value) == "Nexus POST /api/v1/example failed: HTTP 503: busy"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_mint_answered_by_the_gateway_is_sent_again(fake_nexus, fake_clock, status):
    """A mint applies nothing to the cluster: a key the gateway lost the answer for is a spare key nothing stores."""
    fake = fake_nexus()
    fake.expect("POST", "/api/v1/api_tokens", status=status, response=b"<html><title>Bad Gateway</title></html>")
    fake.expect("POST", "/api/v1/api_tokens", response=ok({"otp_code": "OTP1"}))

    assert nexus.mint_join_key(NexusClient(nexus_params()), CLUSTER_ID) == "OTP1"
    assert fake_clock.sleeps == [pytest.approx(1, rel=0.25)]


def test_a_mint_is_sent_again_after_a_lost_connection(fake_nexus, fake_clock):
    fake = fake_nexus()
    fake.expect("POST", "/api/v1/api_tokens", raises=LOST)
    fake.expect("POST", "/api/v1/api_tokens", response=ok({"otp_code": "OTP1"}))

    assert nexus.mint_join_key(NexusClient(nexus_params()), CLUSTER_ID) == "OTP1"


def test_a_mint_the_gateway_fails_until_retry_timeout_fails_naming_the_gateway(fake_nexus, fake_clock):
    fake = fake_nexus()
    for _attempt in range(3):
        fake.expect("POST", "/api/v1/api_tokens", status=502, response=b"<html><title>502 Bad Gateway</title></html>")

    with pytest.raises(NexusApiError) as raised:
        nexus.mint_join_key(NexusClient(nexus_params(retry_timeout=3)), CLUSTER_ID)

    assert str(raised.value) == (
        "Nexus POST /api/v1/api_tokens failed: HTTP 502: <html><title>502 Bad Gateway</title></html> "
        "(the gateway in front of Nexus, or Nexus itself, failed every try for 3s; retry_timeout sets how long to keep trying)"
    )
    assert not isinstance(raised.value, QumuloMaybeApplied)
    assert raised.value.status == 502


def test_dns_failure_fails_at_once_naming_nexus_api_url(fake_nexus):
    fake_nexus().expect("GET", CLUSTERS, raises=URLError(socket.gaierror(-2, "Name or service not known")))

    with pytest.raises(QumuloError, match="cannot resolve the host of https://api.nexus.qumulo.com.*check nexus_api_url"):
        NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})


def test_mint_posts_cluster_otp_with_a_description_and_registers_the_key(fake_nexus, registered):
    fake = fake_nexus().expect(
        "POST",
        "/api/v1/api_tokens",
        body={"token_type": "CLUSTER_OTP", "description": f"qumulo.cluster: register cluster {CLUSTER_ID}"},
        response=ok({"otp_code": "OTP1"}, code=201),
    )

    key = nexus.mint_join_key(NexusClient(nexus_params()), CLUSTER_ID)

    assert key == "OTP1"
    assert registered == [NEXUS_TOKEN, "OTP1"]
    assert fake.log[0].headers["Content-Type"] == "application/json"


def test_mint_names_the_account_when_one_is_given(fake_nexus):
    fake_nexus().expect(
        "POST",
        "/api/v1/api_tokens",
        body={"token_type": "CLUSTER_OTP", "description": f"qumulo.cluster: register cluster {CLUSTER_ID}", "account_id": ACCOUNT_ID},
        response=ok({"otp_code": "OTP1"}),
    )

    assert nexus.mint_join_key(NexusClient(nexus_params()), CLUSTER_ID, account_id=ACCOUNT_ID) == "OTP1"


def test_mint_without_a_code_in_the_answer_fails(fake_nexus):
    fake_nexus().expect("POST", "/api/v1/api_tokens", response=ok({}))

    with pytest.raises(QumuloError, match="Nexus returned no join key"):
        nexus.mint_join_key(NexusClient(nexus_params()), CLUSTER_ID)


def test_find_cluster_returns_the_row_with_this_uuid(fake_nexus):
    other = {"cluster_uuid": "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02", "status": "ACTIVE"}
    mine = {"cluster_uuid": CLUSTER_ID.upper(), "status": "ACTIVE", "cluster_name": "edge01"}
    fake_nexus().expect("GET", CLUSTERS, response=ok([other, mine]))

    assert nexus.find_cluster(NexusClient(nexus_params()), CLUSTER_ID) == mine


@pytest.mark.parametrize("rows", [[], [{"cluster_uuid": "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02"}], None])
def test_find_cluster_returns_none_when_nexus_has_no_row_for_the_uuid(fake_nexus, rows):
    fake_nexus().expect("GET", CLUSTERS, response=ok(rows))

    assert nexus.find_cluster(NexusClient(nexus_params()), CLUSTER_ID) is None


def test_routes_resend_only_what_is_safe():
    resendable = {route.path: sorted(route.replay) for route in vars(nexus).values() if isinstance(route, Route)}

    assert resendable == {
        "/api/v1/clusters": ["GET"],
        "/api/v1/clusters/{uuid}/status": ["PATCH"],
        "/api/v1/clusters/{uuid}/fleet/connection": ["GET"],
        "/api/v1/api_tokens": ["POST"],
    }


def test_nothing_reads_or_creates_a_license_in_nexus():
    """A customer organization's users cannot see licenses in Nexus, so the collection never asks it for one (G-LICENSE)."""
    routes = [route.path for route in vars(nexus).values() if isinstance(route, Route) and "licen" in route.path]
    helpers = [name for name in vars(nexus) if "licen" in name.lower()]

    assert (routes, helpers) == ([], [])


def test_nexus_fragment_documents_the_nexus_argument_spec():
    tree = ast.parse((ROOT / "plugins" / "doc_fragments" / "nexus.py").read_text(encoding="utf-8"))
    [doc] = [
        node.value.value for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "DOCUMENTATION" for t in node.targets)
    ]
    documented = yaml.safe_load(doc)["options"]
    spec = nexus.nexus_argument_spec()

    assert sorted(documented) == sorted(spec)
    for name, option in spec.items():
        description = " ".join(documented[name]["description"])
        assert documented[name]["type"] == option["type"], name
        assert documented[name].get("default") == option.get("default"), name
        assert documented[name].get("no_log", False) is False, "no_log is not a documentation key"
        assert f"E({option['fallback'][1][0]})" in description, name
    assert spec["nexus_api_token"]["no_log"] is True


def test_nexus_options_do_not_shadow_the_connection_options():
    assert not set(nexus.nexus_argument_spec()) & set(client.connection_argument_spec())


def test_without_fake_nexus_a_unit_test_cannot_reach_nexus():
    with pytest.raises(pytest.fail.Exception, match="tried to reach Nexus"):
        NexusClient(nexus_params()).get(nexus.CLUSTERS, query={"cluster_uuid": CLUSTER_ID})
