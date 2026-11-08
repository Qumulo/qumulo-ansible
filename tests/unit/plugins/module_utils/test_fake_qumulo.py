# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json

import pytest
from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient, Route
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import (
    CLUSTER_ID,
    NO_HEADER,
    FakeQumulo,
    client_params,
    fake_api_session,
)

BASE = "https://qumulo.example.com:8000"
THING = Route("/v1/thing", ("GET", "PUT"))


def send(fake, method, path, body=None, headers=None):
    status, response_headers, raw = fake(method, BASE + path, None if body is None else json.dumps(body).encode(), headers or {})
    return status, response_headers, json.loads(raw) if raw else None


def test_unexpected_request_fails():
    fake = FakeQumulo().expect("GET", "/v1/a").expect("GET", "/v1/b")

    with pytest.raises(pytest.fail.Exception, match=r"unexpected request GET /v1/c; next expected GET /v1/a"):
        send(fake, "GET", "/v1/c")
    with pytest.raises(pytest.fail.Exception, match=r"out-of-order request GET /v1/b; next expected GET /v1/a"):
        send(fake, "GET", "/v1/b")


def test_body_and_if_match_expectations_are_checked():
    fake = FakeQumulo()
    fake.expect("PUT", "/v1/a", body={"x": 1}, if_match='"3"', response={"x": 1}, etag='"4"')
    fake.expect("PUT", "/v1/a", if_match=NO_HEADER)
    fake.expect("PUT", "/v1/a", body={"x": 2})

    assert send(fake, "PUT", "/v1/a", {"x": 1}, {"If-Match": '"3"'}) == (200, {"etag": '"4"'}, {"x": 1})
    with pytest.raises(pytest.fail.Exception, match=r"PUT /v1/a: expected no If-Match, got '\"3\"'"):
        send(fake, "PUT", "/v1/a", {"x": 1}, {"If-Match": '"3"'})
    with pytest.raises(pytest.fail.Exception, match=r"PUT /v1/a: expected body \{'x': 2\}, got \{'x': 3\}"):
        send(fake, "PUT", "/v1/a", {"x": 3})


def test_query_parameters_match_in_any_order():
    fake = FakeQumulo().expect("GET", "/v1/a?limit=2&after=x")

    assert send(fake, "GET", "/v1/a?after=x&limit=2")[0] == 200
    assert fake.log[0].path == "/v1/a?after=x&limit=2"


def test_response_headers_and_raw_bodies_are_sent_as_given():
    fake = FakeQumulo().expect("GET", "/v1/a", status=301, response=b"<html>moved</html>", headers={"location": "https://elsewhere/"})

    assert fake("GET", BASE + "/v1/a", None, {}) == (301, {"location": "https://elsewhere/"}, b"<html>moved</html>")


def test_unused_expectation_fails_at_teardown(monkeypatch):
    session = fake_api_session(monkeypatch)
    make = next(session)
    make().expect("GET", "/v1/a", status=503)

    with pytest.raises(pytest.fail.Exception, match=r"unused expectations: GET /v1/a -> 503"):
        next(session, None)


def test_background_calls_answered_in_any_order(fake_api):
    fake = fake_api(version="7.11.0").expect("GET", "/v1/thing", response={"x": 1})
    fake.identities.append({"auth_id": "500", "name": "alice", "domain": "LOCAL"})

    assert send(fake, "GET", "/v1/version")[2]["revision_id"] == "Qumulo Core 7.11.0"
    assert QumuloClient(client_params(api_password="secret")).get(THING).data == {"x": 1}
    assert send(fake, "GET", "/v1/node/state")[2] == {"node_id": 1, "cluster_id": CLUSTER_ID, "state": "ACTIVE"}
    assert send(fake, "POST", "/v1/session/login", {"username": "admin", "password": "secret"})[2] == {"bearer_token": "session-2"}
    assert send(fake, "POST", "/v1/identity/find", {"domain": "LOCAL", "name": "alice"})[2]["auth_id"] == "500"
    status, _headers, body = send(fake, "POST", "/v1/identity/find", {"domain": "LOCAL", "name": "bob"})
    assert (status, body["error_class"]) == (404, "api_identity_name_not_found_error")

    assert [(e.method, e.path, e.background) for e in fake.log] == [
        ("GET", "/v1/version", True),
        ("POST", "/v1/session/login", True),
        ("GET", "/v1/thing", False),
        ("GET", "/v1/node/state", True),
        ("POST", "/v1/session/login", True),
        ("POST", "/v1/identity/find", True),
        ("POST", "/v1/identity/find", True),
    ]
    assert fake.log[2].headers["Authorization"] == "Bearer session-1"
    assert fake.params["api_host"] == "qumulo.example.com"


def test_a_find_error_answers_its_request_body_only_and_before_the_identities():
    fake = FakeQumulo()
    fake.identities.append({"auth_id": "500", "name": "alice", "domain": "LOCAL"})
    unknown_domain = {"error_class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain S-1-5-21-9-9-9"}
    fake.find_errors.append(({"sid": "S-1-5-21-9-9-9-1104"}, 400, unknown_domain))
    fake.find_errors.append(({"domain": "LOCAL", "name": "alice"}, 503, {"error_class": "unavailable_error", "description": "test"}))

    assert send(fake, "POST", "/v1/identity/find", {"sid": "S-1-5-21-9-9-9-1104"})[:3:2] == (400, unknown_domain)
    assert send(fake, "POST", "/v1/identity/find", {"domain": "LOCAL", "name": "alice"})[0] == 503
    status, _headers, body = send(fake, "POST", "/v1/identity/find", {"domain": "LOCAL", "name": "bob"})
    assert (status, body["error_class"]) == (404, "api_identity_name_not_found_error")


def test_served_paths_answer_every_get_in_any_order_and_query_order():
    fake = FakeQumulo().serve("/v1/a?x=1&y=2", {"a": 1}).serve("/v1/b", {"b": 2}, status=404)

    assert send(fake, "GET", "/v1/b") == (404, {}, {"b": 2})
    assert send(fake, "GET", "/v1/a?y=2&x=1")[2] == {"a": 1}
    assert send(fake, "GET", "/v1/a?x=1&y=2")[2] == {"a": 1}
    fake.verify()


def test_a_scripted_expectation_answers_before_a_served_path():
    fake = FakeQumulo().serve("/v1/a", {"served": True}).expect("GET", "/v1/a", response={"scripted": True})

    assert send(fake, "GET", "/v1/a")[2] == {"scripted": True}
    assert send(fake, "GET", "/v1/a")[2] == {"served": True}


def test_a_write_to_a_served_path_fails():
    fake = FakeQumulo().serve("/v1/a", {"a": 1})

    with pytest.raises(pytest.fail.Exception, match=r"unexpected request PATCH /v1/a"):
        send(fake, "PATCH", "/v1/a", {"a": 2})


def test_a_read_only_query_served_as_a_post_answers_only_that_method():
    fake = FakeQumulo().serve("/v1/explain", {"result": 1}, method="POST")

    assert send(fake, "POST", "/v1/explain", {"mode": "0755"})[2] == {"result": 1}
    with pytest.raises(pytest.fail.Exception, match=r"unexpected request GET /v1/explain"):
        send(fake, "GET", "/v1/explain")
    fake.verify()


def test_a_served_query_with_a_body_fails_on_another_body():
    fake = FakeQumulo().serve("/v1/explain", {"result": 1}, method="POST", body={"mode": "0755"})

    assert send(fake, "POST", "/v1/explain", {"mode": "0755"})[2] == {"result": 1}
    with pytest.raises(pytest.fail.Exception, match=r"expected body \{'mode': '0755'\}, got \{'mode': '0000'\}"):
        send(fake, "POST", "/v1/explain", {"mode": "0000"})


def test_a_served_path_nothing_read_fails_at_teardown_unless_optional():
    fake = FakeQumulo().serve("/v1/read", {}).serve("/v1/unread", {}).serve("/v1/shared", {}, optional=True)
    send(fake, "GET", "/v1/read")

    with pytest.raises(pytest.fail.Exception, match=r"served paths nothing read: /v1/unread$"):
        fake.verify()


def toy_main():
    module = AnsibleModule(argument_spec={"name": {"type": "str"}, "fail": {"type": "bool", "default": False}}, supports_check_mode=True)
    module.warn("careful")
    if module.params["fail"]:
        module.fail_json(msg="it broke", detail=1)
    module.exit_json(changed=not module.check_mode, name=module.params["name"], diff_mode=module._diff)


def test_run_module_returns_exit_and_fail_results(run_module):
    assert run_module(toy_main, {"name": "x"}) == {"changed": True, "name": "x", "diff_mode": False, "warnings": ["careful"]}
    assert run_module(toy_main, {"name": "x"}, check_mode=True, diff=True)["changed"] is False
    assert run_module(toy_main, {"name": "x"}, diff=True)["diff_mode"] is True
    assert run_module(toy_main, {"fail": True}) == {"failed": True, "msg": "it broke", "detail": 1, "warnings": ["careful"]}
