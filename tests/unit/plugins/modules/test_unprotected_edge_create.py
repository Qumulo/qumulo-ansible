# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.unprotected_edge_create, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import unprotected_edge_create
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

NEW_ID = "1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11"
CONNECTION = {"api_host": "edge01.example.com"}
ARGS = {"cluster_name": "edge01", "admin_password": "Qa1-new-admin", "eula_accepted": True}
NODE_STATE, CREATE, SETTINGS = "/v1/node/state", "/v2/cluster/unprotected-edge/create", "/v1/cluster/settings"
ROOT = "/v1/files/%2F/info/attributes"


def node(state, cluster_id=""):
    return {"node_id": 1, "cluster_id": cluster_id, "state": state}


def unconfigured(fake_api):
    return fake_api(cluster_id="", state="UNCONFIGURED")


def expect_ready(fake, cluster_id=NEW_ID):
    """The guard's read before the first login, then the root directory once the file system serves it."""
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", cluster_id))
    fake.expect("GET", ROOT, response={"path": "/"})


def authenticated(fake):
    return [e for e in fake.log if "Authorization" in e.headers]


def test_unconfigured_node_is_formed_and_its_new_uuid_returned(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    body = {"eula_accepted": True, "name": "edge01", "admin_password": "Qa1-new-admin"}
    fake.expect("POST", CREATE, body=body, if_match=NO_HEADER, status=202, response={"monitor_uri": "/v1/node/state"})
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    expect_ready(fake)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["unprotected_edge"] == {"cluster_id": NEW_ID, "cluster_name": "edge01", "state": "ACTIVE"}
    create = next(e for e in fake.log if e.method == "POST" and e.path == CREATE)
    assert "Authorization" not in create.headers
    assert "Qa1-new-admin" not in json.dumps(result)


def test_api_password_defaults_to_the_admin_password(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, status=202)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    expect_ready(fake)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS})

    assert not result.get("failed"), result.get("msg")
    (login,) = [e for e in fake.log if e.path == "/v1/session/login"]
    assert login.body == {"username": "admin", "password": "Qa1-new-admin"}


def test_formation_ignores_an_expected_uuid_the_new_cluster_cannot_have(fake_api, run_module):
    """Every formation mints a new UUID (G-UUID), so the guard switches to the cluster this task formed."""
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, status=202)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    expect_ready(fake)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "expected_cluster_uuid": CLUSTER_ID})

    assert result["changed"] is True, result.get("msg")
    assert result["unprotected_edge"]["cluster_id"] == NEW_ID


def test_active_cluster_of_that_name_is_unchanged(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    fake.expect("GET", SETTINGS, response={"cluster_name": "edge01"})
    expect_ready(fake)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "expected_cluster_uuid": NEW_ID})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["unprotected_edge"] == {"cluster_id": NEW_ID, "cluster_name": "edge01", "state": "ACTIVE"}
    assert [e.path for e in fake.log if e.method == "POST" and e.path == CREATE] == []


def test_active_cluster_with_another_name_fails(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    fake.expect("GET", SETTINGS, response={"cluster_name": "qedge"})

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert f"edge01.example.com is already ACTIVE as cluster '{NEW_ID}' named 'qedge', not 'edge01'" in result["msg"]
    assert authenticated(fake) == []


def test_active_cluster_with_another_uuid_than_expected_fails_before_any_login(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    fake.expect("GET", SETTINGS, response={"cluster_name": "edge01"})

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "expected_cluster_uuid": CLUSTER_ID})

    assert result["failed"] is True
    assert f"is ACTIVE as cluster '{NEW_ID}', but expected_cluster_uuid is '{CLUSTER_ID}'" in result["msg"]
    assert "cluster_guard mode=rebuilt" in result["msg"]
    assert fake.logins == 0


def test_check_mode_predicts_the_formation_and_sends_nothing(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS}, check_mode=True, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["unprotected_edge"] == {"cluster_id": None, "cluster_name": "edge01", "state": "UNCONFIGURED"}
    assert result["diff"] == {"before": {"state": "UNCONFIGURED"}, "after": {"cluster_name": "edge01", "state": "ACTIVE"}}
    assert [e for e in fake.log if e.method == "POST"] == []


def test_without_wait_the_module_returns_once_the_request_is_accepted(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, status=202)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "wait": False})

    assert result["changed"] is True, result.get("msg")
    assert result["unprotected_edge"] == {"cluster_id": None, "cluster_name": "edge01", "state": "UNCONFIGURED"}


def test_node_that_never_becomes_active_times_out_reporting_the_change(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, status=202)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "wait_timeout": 30})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "not ACTIVE after 30s" in result["msg"]


def test_lost_connection_after_the_create_waits_for_the_node(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", NEW_ID))
    expect_ready(fake)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["unprotected_edge"]["cluster_id"] == NEW_ID
    assert len([e for e in fake.log if e.path == CREATE]) == 1


def test_lost_connection_without_wait_fails_saying_the_formation_is_unknown(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "wait": False})

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["maybe_applied"] is True


def test_lost_create_that_never_formed_says_it_may_not_have_been_received(fake_api, run_module):
    fake = unconfigured(fake_api)
    fake.expect("GET", NODE_STATE, response=node("UNCONFIGURED"))
    fake.expect("POST", CREATE, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "wait_timeout": 30})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)
    assert "not ACTIVE after 30s" in result["msg"]
    assert "may never have received it" in result["msg"]
    assert len([e for e in fake.log if e.path == CREATE]) == 1


def test_eula_not_accepted_fails_before_any_request(fake_api, run_module):
    fake = unconfigured(fake_api)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "eula_accepted": False})

    assert result["failed"] is True
    assert "eula_accepted must be true" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("name", ["e", "edge_01", "-edge"])
def test_name_outside_the_rules_fails_before_any_request(fake_api, run_module, name):
    fake = unconfigured(fake_api)

    result = run_module(unprotected_edge_create, {**CONNECTION, **ARGS, "cluster_name": name})

    assert result["failed"] is True
    assert f"cluster_name '{name}' must be 2 to 15 letters" in result["msg"]
    assert fake.log == []
