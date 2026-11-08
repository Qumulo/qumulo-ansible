# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.nexus_cluster, driven through FakeNexus and FakeQumulo. Nexus is never called."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import nexus_cluster
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_nexus import NEXUS_TOKEN, ok
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID, "nexus_api_token": NEXUS_TOKEN}
OLD_ID = "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02"
CLUSTERS = f"/api/v1/clusters?cluster_uuid={OLD_ID}"
FLEET = f"/api/v1/clusters/{OLD_ID}/fleet/connection"
STATUS = f"/api/v1/clusters/{OLD_ID}/status"
REGISTRATION = "/v1/nexus/registration"
ROW = {"cluster_uuid": OLD_ID, "cluster_name": "edge01", "account_id": 4242, "status": "ACTIVE"}
RETIRED = {**ROW, "status": "DECOMMISSIONED"}
OFFLINE = {"connected": False, "node_count": 1, "last_seen": "2026-10-30T00:00:00Z"}
ONLINE = {"connected": True, "node_count": 1, "last_seen": "2026-10-31T02:00:00Z"}


def nexus_writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET"]


def expect_retire(nexus_fake, fleet=None, row=None, after=None):
    """The reads and the PATCH of a retirement; by default an offline ACTIVE cluster that ends DECOMMISSIONED."""
    after = after or RETIRED
    nexus_fake.expect("GET", CLUSTERS, response=ok([row or ROW]))
    nexus_fake.expect("GET", FLEET, response=ok(fleet or OFFLINE))
    nexus_fake.expect("PATCH", STATUS, body={"status": "DECOMMISSIONED"}, response=ok(after))
    nexus_fake.expect("GET", CLUSTERS, response=ok([after]))


def test_an_active_cluster_is_retired(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    expect_retire(nexus_fake)

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["nexus_cluster"] == {"cluster_uuid": OLD_ID, "cluster_name": "edge01", "status": "DECOMMISSIONED", "connected": False}
    assert nexus_writes(nexus_fake) == [f"PATCH {STATUS}"]
    assert set(nexus_fake.bearer_tokens()) == {f"Bearer {NEXUS_TOKEN}"}


def test_a_retired_cluster_is_unchanged_and_sends_no_patch(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([RETIRED]))

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert result["changed"] is False, result.get("msg")
    assert result["nexus_cluster"]["status"] == "DECOMMISSIONED"
    assert nexus_writes(nexus_fake) == []


def test_a_uuid_nexus_does_not_know_is_unchanged_with_a_note(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([]))

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert result["changed"] is False, result.get("msg")
    assert result["nexus_cluster"] is None
    assert result["notes"] == [f"Nexus has no cluster {OLD_ID}, so there is nothing to retire"]


def test_a_connected_cluster_is_refused_without_allow_connected(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([ROW]))
    nexus_fake.expect("GET", FLEET, response=ok(ONLINE))

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert result["failed"] is True
    assert "still connected to Nexus (last seen 2026-10-31T02:00:00Z)" in result["msg"]
    assert "allow_connected" in result["msg"]
    assert nexus_writes(nexus_fake) == []


def test_a_connected_cluster_is_retired_with_allow_connected(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    expect_retire(nexus_fake, fleet=ONLINE)

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID, "allow_connected": True})

    assert result["changed"] is True, result.get("msg")
    assert result["nexus_cluster"]["connected"] is True


@pytest.mark.parametrize("status", ["REGISTERED", "PENDING_REGISTRATION"])
def test_the_cluster_at_api_host_is_refused_while_it_is_registered(fake_api, fake_nexus, run_module, status):
    """A stored key would later register the cluster into the UUID this retired, so PENDING_REGISTRATION counts too."""
    fake = fake_api(cluster_id=OLD_ID)
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([ROW]))
    fake.expect("GET", REGISTRATION, response={"nexus_enabled": True, "registration_status": status, "secret_created_at": "x"})

    result = run_module(nexus_cluster, {**CONNECTION, "expected_cluster_uuid": OLD_ID, "cluster_uuid": OLD_ID, "allow_connected": True})

    assert result["failed"] is True
    assert "is the cluster at qumulo.example.com, and it is still registered with Nexus" in result["msg"]
    assert "qumulo.cluster.nexus_registration (state: absent)" in result["msg"]
    assert nexus_writes(nexus_fake) == []


def test_the_cluster_at_api_host_is_retired_once_it_is_deregistered(fake_api, fake_nexus, run_module):
    fake = fake_api(cluster_id=OLD_ID.upper())
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([ROW]))
    fake.expect("GET", REGISTRATION, response={"nexus_enabled": True, "registration_status": "NONE", "secret_created_at": None})
    nexus_fake.expect("GET", FLEET, response=ok(OFFLINE))
    nexus_fake.expect("PATCH", STATUS, body={"status": "DECOMMISSIONED"}, response=ok(RETIRED))
    nexus_fake.expect("GET", CLUSTERS, response=ok([RETIRED]))

    result = run_module(nexus_cluster, {**CONNECTION, "expected_cluster_uuid": OLD_ID, "cluster_uuid": OLD_ID})

    assert result["changed"] is True, result.get("msg")


def test_check_mode_predicts_the_retirement_and_sends_no_patch(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([ROW]))
    nexus_fake.expect("GET", FLEET, response=ok(OFFLINE))

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["nexus_cluster"]["status"] == "DECOMMISSIONED"
    assert result["diff"] == {"before": {"status": "ACTIVE"}, "after": {"status": "DECOMMISSIONED"}}
    assert nexus_writes(nexus_fake) == []


def test_a_patch_that_does_not_retire_the_cluster_fails(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    expect_retire(nexus_fake, after=ROW)

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "Nexus reports cluster" in result["msg"]
    assert "ACTIVE, not DECOMMISSIONED" in result["msg"]


BARE = {key: value for key, value in ROW.items() if key != "status"}


@pytest.mark.parametrize("row", [BARE, ROW], ids=["no_status_before", "active_before"])
def test_a_read_back_without_a_status_fails_after_the_patch(fake_api, fake_nexus, run_module, row):
    """Without a status Nexus cannot show that the cluster is retired, so every later run would PATCH again."""
    fake_api()
    nexus_fake = fake_nexus()
    expect_retire(nexus_fake, row=row, after=BARE)

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert (result["failed"], result["changed"]) == (True, True)
    assert f"Nexus accepted retiring cluster {OLD_ID}, but its listing reports no status" in result["msg"]


def test_a_cluster_nexus_stops_listing_once_retired_fails_after_the_patch(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([ROW]))
    nexus_fake.expect("GET", FLEET, response=ok(OFFLINE))
    nexus_fake.expect("PATCH", STATUS, body={"status": "DECOMMISSIONED"}, response=ok(RETIRED))
    nexus_fake.expect("GET", CLUSTERS, response=ok([]))

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert (result["failed"], result["changed"]) == (True, True)
    assert f"Nexus no longer lists cluster {OLD_ID} after retiring it" in result["msg"]


@pytest.mark.parametrize("token", [None, ""])
def test_without_a_token_fails_before_any_request(fake_api, run_module, token):
    fake = fake_api()

    result = run_module(nexus_cluster, {**CONNECTION, "nexus_api_token": token, "cluster_uuid": OLD_ID})

    assert result["failed"] is True
    assert "nexus_api_token" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_role_without_the_right_fails_naming_it(fake_api, fake_nexus, run_module):
    fake_api()
    nexus_fake = fake_nexus()
    nexus_fake.expect("GET", CLUSTERS, response=ok([ROW]))
    nexus_fake.expect("GET", FLEET, response=ok(OFFLINE))
    nexus_fake.expect("PATCH", STATUS, status=403, response={"message": "Forbidden"})

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": OLD_ID})

    assert result["failed"] is True
    assert "HTTP 403: Forbidden (nexus_api_token's Nexus role does not allow this call)" in result["msg"]


def test_a_malformed_uuid_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(nexus_cluster, {**CONNECTION, "cluster_uuid": "edge01"})

    assert result["failed"] is True
    assert "cluster_uuid must be a cluster UUID, not 'edge01'" in result["msg"]
    assert [e for e in fake.log if not e.background] == []
