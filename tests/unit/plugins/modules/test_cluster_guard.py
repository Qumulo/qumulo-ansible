# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.cluster_guard, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import ssl
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import cluster_guard
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
NEW_ID = "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02"
REBUILT = {**CONNECTION, "mode": "rebuilt", "new_cluster_uuid": NEW_ID, "cluster_name": "edge01"}
ROOT = "/v1/files/%2F/info/attributes"
METRICS = "/v2/metrics/endpoints/default/data"
FILES_SHARE = {"id": "2", "share_name": "Files", "fs_path": "/", "tenant_id": 1}
ROOT_EXPORT = {"id": "1", "export_path": "/", "fs_path": "/", "tenant_id": 1}
PRODUCT_INTENT = {"unprotected_edge": "edge_accelerator", "protected_cluster": "default"}
REFUSED = URLError(ConnectionRefusedError(111, "Connection refused"))


def metrics(cluster_type):
    """An OpenMetrics page; with cluster_type None it has no qumulo_info sample."""
    info = f'qumulo_info{{cluster_type="{cluster_type}",product_intent="{PRODUCT_INTENT[cluster_type]}"}} 1\n' if cluster_type else ""
    return f"# TYPE qumulo_info gauge\n{info}# EOF\n".encode()


def rebuilt_reads(*, cluster_type="unprotected_edge", name="edge01", spokes=(), hubs=(), shares=(FILES_SHARE,), exports=(ROOT_EXPORT,)):
    """The reads of an ACTIVE rebuilt cluster, named by the check each one serves, in the order the guard makes them."""
    return [
        ("root", ROOT, {"path": "/", "type": "FS_FILE_TYPE_DIRECTORY"}),
        ("product", METRICS, metrics(cluster_type)),
        ("name", "/v1/cluster/settings", {"cluster_name": name}),
        ("spokes", "/v2/portal/spokes/", {"entries": list(spokes)}),
        ("hubs", "/v2/portal/hubs/", {"entries": list(hubs)}),
        ("shares", "/v3/smb/shares/", {"entries": list(shares)}),
        ("exports", "/v3/nfs/exports/", {"entries": list(exports)}),
    ]


def expect_reads(fake, reads, through):
    """Script reads in order, up to and including the one named through: the check where the guard stops."""
    for step, path, response in reads:
        if through is None:
            return
        fake.expect("GET", path, response=response)
        if step == through:
            return


def requests(fake):
    return [f"{e.method} {e.path}" for e in fake.log]


def test_match_passes(fake_api, run_module):
    fake = fake_api(version="7.10.3")
    fake.expect("GET", ROOT, response={"path": "/", "type": "FS_FILE_TYPE_DIRECTORY"})

    result = run_module(cluster_guard, CONNECTION)

    assert result == {"changed": False, "cluster_id": CLUSTER_ID, "state": "ACTIVE", "version": "7.10.3", "warnings": []}


def test_mismatch_fails_closed(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)

    result = run_module(cluster_guard, CONNECTION)

    assert result["failed"] is True
    assert NEW_ID in result["msg"]
    assert CLUSTER_ID in result["msg"]
    assert "no credentials were sent" in result["msg"]
    assert requests(fake) == ["GET /v1/node/state"]


def test_match_on_unconfigured_node_fails_closed(fake_api, run_module, fake_clock):
    fake = fake_api(cluster_id="", state="UNCONFIGURED")

    result = run_module(cluster_guard, CONNECTION)

    assert result["failed"] is True
    assert "UNCONFIGURED" in result["msg"]
    assert fake.logins == 0
    assert fake_clock.sleeps == []


def test_match_needs_expected_cluster_uuid(fake_api, run_module):
    fake = fake_api()

    result = run_module(cluster_guard, {**CONNECTION, "expected_cluster_uuid": ""})

    assert result["failed"] is True
    assert "expected_cluster_uuid" in result["msg"]
    assert requests(fake) == []


def test_match_without_wait_reads_node_state_only(fake_api, run_module):
    fake = fake_api(state="STOPPED")

    result = run_module(cluster_guard, {**CONNECTION, "wait": False})

    assert (result["cluster_id"], result["state"]) == (CLUSTER_ID, "STOPPED")
    assert fake.logins == 0
    assert requests(fake) == ["GET /v1/node/state", "GET /v1/version"]


def test_match_without_wait_still_fails_closed_on_a_mismatch(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)

    result = run_module(cluster_guard, {**CONNECTION, "wait": False})

    assert result["failed"] is True
    assert "no credentials were sent" in result["msg"]
    assert requests(fake) == ["GET /v1/node/state"]


def test_match_warns_below_tested_floor(fake_api, run_module):
    fake = fake_api(version="7.9.0")
    fake.expect("GET", ROOT, response={"path": "/"})

    result = run_module(cluster_guard, CONNECTION)

    assert "failed" not in result
    assert any("7.9.0" in w and "7.10.3" in w for w in result["warnings"])


def test_wait_polls_until_active_then_login_then_root(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", "/v1/node/state", response={"cluster_id": CLUSTER_ID, "state": "STOPPED", "node_id": 1})
    fake.expect("GET", "/v1/node/state", response={"cluster_id": CLUSTER_ID, "state": "ACTIVE", "node_id": 1})
    fake.expect("GET", ROOT, status=500, response={"error_class": "test_not_ready", "description": "the file system is starting"})
    fake.expect("GET", ROOT, response={"path": "/"})

    result = run_module(cluster_guard, CONNECTION)

    assert "failed" not in result
    sent = requests(fake)
    assert sent[:2] == ["GET /v1/node/state", "GET /v1/node/state"]
    assert sent.index("POST /v1/session/login") < sent.index(f"GET {ROOT}")
    assert sent.count(f"GET {ROOT}") == 2
    assert len(fake_clock.sleeps) == 2


def test_wait_rides_out_a_refused_connection(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", "/v1/node/state", raises=REFUSED)
    fake.expect("GET", "/v1/node/state", response={"cluster_id": CLUSTER_ID, "state": "ACTIVE", "node_id": 1})
    fake.expect("GET", ROOT, response={"path": "/"})

    result = run_module(cluster_guard, {**CONNECTION, "wait_timeout": 30})

    assert "failed" not in result
    assert fake_clock.now < 30


def test_wait_against_a_dead_api_ends_at_wait_timeout(fake_api, run_module, fake_clock):
    fake = fake_api()
    for _attempt in range(30):
        fake.expect("GET", "/v1/node/state", raises=REFUSED)

    result = run_module(cluster_guard, {**CONNECTION, "wait_timeout": 30})
    fake.script.clear()  # the wait stops on its own clock, not after a set number of attempts

    assert result["failed"] is True
    assert "unreachable" in result["msg"]
    assert "not ACTIVE after 30s" in result["msg"]
    assert fake_clock.now == pytest.approx(30)
    assert set(requests(fake)) == {"GET /v1/node/state"}


def test_wait_fails_at_once_on_a_certificate_that_does_not_verify(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", "/v1/node/state", raises=URLError(ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate")))

    result = run_module(cluster_guard, CONNECTION)

    assert result["failed"] is True
    assert "TLS certificate verification failed" in result["msg"]
    assert fake_clock.sleeps == []


def test_wait_fails_at_once_on_a_host_that_is_not_a_qumulo_cluster(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("GET", "/v1/node/state", status=404, response=b"<html>not found</html>")

    result = run_module(cluster_guard, CONNECTION)

    assert result["failed"] is True
    assert "HTTP 404" in result["msg"]
    assert fake_clock.sleeps == []


def test_wait_gives_up_a_refused_login_at_its_own_deadline(fake_api, run_module, fake_clock):
    fake = fake_api()
    for _attempt in range(30):
        fake.expect("POST", "/v1/session/login", raises=REFUSED)

    result = run_module(cluster_guard, {**CONNECTION, "wait_timeout": 30})
    fake.script.clear()  # the login is retried until the wait's deadline, not for a set number of attempts

    assert result["failed"] is True
    assert "unreachable for 30" in result["msg"]
    assert fake_clock.now == pytest.approx(30)


def test_wait_times_out(fake_api, run_module, fake_clock):
    fake = fake_api(state="STOPPED")

    result = run_module(cluster_guard, {**CONNECTION, "wait_timeout": 30})

    assert result["failed"] is True
    assert "in state STOPPED, not ACTIVE after 30s" in result["msg"]
    assert fake.logins == 0
    assert fake_clock.now <= 30


def test_wait_bounds_each_read_by_the_poll_interval_and_the_last_sleep_by_the_deadline(fake_api, run_module, fake_clock, node_state_budgets):
    """A wait_timeout that is not a multiple of the poll interval shows both bounds."""
    fake_api(state="STOPPED")

    result = run_module(cluster_guard, {**CONNECTION, "wait_timeout": 12})

    assert result["failed"] is True
    assert node_state_budgets and all(budget <= 5 for budget in node_state_budgets)
    assert fake_clock.sleeps == [5, 5, 2]
    assert fake_clock.now == 12


def test_root_read_wait_times_out(fake_api, run_module, fake_clock):
    fake = fake_api()
    for _attempt in range(3):  # at t = 0, 5 and 10
        fake.expect("GET", ROOT, status=500, response={"error_class": "test_not_ready", "description": "starting"})

    result = run_module(cluster_guard, {**CONNECTION, "wait_timeout": 10})

    assert result["failed"] is True
    assert "file system is not ready after 10s" in result["msg"]
    assert fake_clock.now == 10


@pytest.mark.parametrize(("status", "hint"), [(401, "check api_username and api_password"), (403, "RBAC privilege")])
def test_wait_reraises_a_credential_error_from_the_root_read(fake_api, run_module, fake_clock, status, hint):
    fake = fake_api()
    attempts = 2 if status == 401 else 1  # the client logs in again once after a 401
    for attempt in range(attempts):
        fake.expect("GET", ROOT, status=status, response={"error_class": "test_denied", "description": f"attempt {attempt + 1}"})

    result = run_module(cluster_guard, CONNECTION)

    assert result["failed"] is True
    assert f"HTTP {status}" in result["msg"]
    assert hint in result["msg"]
    assert "not ready" not in result["msg"]
    assert fake_clock.sleeps == []


def test_wait_reraises_a_rejected_login(fake_api, run_module, fake_clock):
    fake = fake_api()
    fake.expect("POST", "/v1/session/login", status=401, response={"error_class": "test_denied", "description": "bad password"})

    result = run_module(cluster_guard, CONNECTION)

    assert result["failed"] is True
    assert "check api_username and api_password" in result["msg"]
    assert fake_clock.sleeps == []


def test_rebuilt_accepts_unconfigured(fake_api, run_module):
    fake = fake_api(cluster_id="", state="UNCONFIGURED")

    result = run_module(cluster_guard, {**CONNECTION, "mode": "rebuilt"})

    assert result == {"changed": False, "cluster_id": None, "state": "UNCONFIGURED", "version": "7.10.3", "warnings": []}
    assert fake.logins == 0


@pytest.mark.parametrize(
    ("args", "reads", "through", "message"),
    [
        pytest.param({}, {}, "exports", None, id="passes"),
        pytest.param({"new_cluster_uuid": None}, {}, None, "set new_cluster_uuid and cluster_name", id="no-new-uuid"),
        pytest.param({"new_cluster_uuid": CLUSTER_ID}, {}, None, f"not the new cluster '{CLUSTER_ID}'; no credentials were sent", id="other-uuid"),
        pytest.param({"cluster_name": None}, {}, None, "set new_cluster_uuid and cluster_name", id="no-name"),
        pytest.param({}, {"cluster_type": "protected_cluster"}, "product", "cluster_type is 'protected_cluster', not 'unprotected_edge'", id="protected"),
        pytest.param({}, {"cluster_type": None}, "product", "metrics carry no qumulo_info sample", id="no-sample"),
        pytest.param({}, {"name": "old-name"}, "name", "cluster name is 'old-name', not 'edge01'", id="renamed"),
        pytest.param({}, {"hubs": [{"id": 1, "state": "ACTIVE"}]}, "hubs", "the cluster has 1 hub portal;", id="hub"),
    ],
)
def test_rebuilt_active_needs_new_uuid_name_product_and_no_portals(fake_api, run_module, args, reads, through, message):
    fake = fake_api(cluster_id=NEW_ID)
    expect_reads(fake, rebuilt_reads(**reads), through)

    result = run_module(cluster_guard, {**REBUILT, **args})

    if message is None:
        assert result == {"changed": False, "cluster_id": NEW_ID, "state": "ACTIVE", "version": "7.10.3", "warnings": []}
        return
    assert result["failed"] is True
    assert result["msg"].startswith("rebuilt: ")
    assert message in result["msg"]
    if through is None:
        assert fake.logins == 0


def test_rebuilt_active_with_spokes_fails(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    expect_reads(fake, rebuilt_reads(spokes=[{"id": 1, "state": "ACTIVE"}, {"id": 2, "state": "PENDING"}]), "spokes")

    result = run_module(cluster_guard, REBUILT)

    assert result["failed"] is True
    assert "2 spoke portals" in result["msg"]


@pytest.mark.parametrize(
    ("reads", "through", "message"),
    [
        ({"shares": (FILES_SHARE, {"id": "3", "share_name": "projects", "fs_path": "/projects"})}, "shares", "SMB share 'projects'"),
        ({"exports": (ROOT_EXPORT, {"id": "2", "export_path": "/projects", "fs_path": "/projects"})}, "exports", "NFS export '/projects'"),
    ],
)
def test_rebuilt_active_with_extra_share_fails(fake_api, run_module, reads, through, message):
    fake = fake_api(cluster_id=NEW_ID)
    expect_reads(fake, rebuilt_reads(**reads), through)

    result = run_module(cluster_guard, REBUILT)

    assert result["failed"] is True
    assert message in result["msg"]


def test_rebuilt_metrics_not_offered_fails_closed_and_says_why(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    fake.expect("GET", ROOT, response={"path": "/"})
    fake.expect("GET", METRICS, status=404, response={"error_class": "http_not_found_error", "description": "no route"})

    result = run_module(cluster_guard, REBUILT)

    assert result["failed"] is True
    assert result["msg"].startswith("rebuilt: cannot tell whether the cluster is an unprotected edge: ")
    assert "HTTP 404" in result["msg"]


def test_rebuilt_metrics_permission_denied_fails_with_the_hint(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    fake.expect("GET", ROOT, response={"path": "/"})
    fake.expect("GET", METRICS, status=403, response={"error_class": "http_forbidden", "description": "no"})

    result = run_module(cluster_guard, REBUILT)

    assert result["failed"] is True
    assert "HTTP 403" in result["msg"]
    assert "RBAC privilege" in result["msg"]


def test_rebuilt_checks_the_new_identity_before_login(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    expect_reads(fake, rebuilt_reads(), "exports")

    run_module(cluster_guard, REBUILT)

    sent = requests(fake)
    assert sent.index("GET /v1/node/state") < sent.index("POST /v1/session/login")


def test_rebuilt_switches_the_client_guard_to_the_stripped_new_uuid(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)
    expect_reads(fake, rebuilt_reads(), "exports")

    result = run_module(cluster_guard, {**REBUILT, "expected_cluster_uuid": "", "new_cluster_uuid": f"  {NEW_ID}\n"})

    assert result == {"changed": False, "cluster_id": NEW_ID, "state": "ACTIVE", "version": "7.10.3", "warnings": []}


def test_rebuilt_wrong_new_uuid_sends_no_login_when_expected_uuid_is_empty(fake_api, run_module):
    fake = fake_api(cluster_id=NEW_ID)

    result = run_module(cluster_guard, {**REBUILT, "expected_cluster_uuid": "", "new_cluster_uuid": CLUSTER_ID})

    assert result["failed"] is True
    assert "no credentials were sent" in result["msg"]
    assert fake.logins == 0
    assert result["warnings"] == []


@pytest.mark.parametrize("state", ["STOPPED", "PAUSED"])
def test_rebuilt_node_of_another_cluster_fails_at_once_in_any_state(fake_api, run_module, fake_clock, state):
    fake = fake_api(cluster_id=CLUSTER_ID, state=state)

    result = run_module(cluster_guard, REBUILT)

    assert result["failed"] is True
    assert f"is cluster '{CLUSTER_ID}', not the new cluster '{NEW_ID}'" in result["msg"]
    assert fake_clock.sleeps == []
    assert fake.logins == 0


@pytest.mark.parametrize("state", ["STOPPED", "PAUSED"])
def test_rebuilt_without_wait_refuses_a_node_that_is_neither_unconfigured_nor_active(fake_api, run_module, state):
    fake = fake_api(cluster_id=NEW_ID, state=state)

    result = run_module(cluster_guard, {**REBUILT, "wait": False})

    assert result["failed"] is True
    assert f"is in state {state}, neither UNCONFIGURED nor ACTIVE" in result["msg"]
    assert fake.logins == 0
