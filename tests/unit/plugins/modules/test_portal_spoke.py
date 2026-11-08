# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.portal_spoke, driven through FakeQumulo. Response bodies follow what Qumulo Core 7.10.3 returns."""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import portal_spoke
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "edge01.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HUB_UUID = "7c1e0d4a-2b3c-4d5e-8f90-a1b2c3d4e5f6"
OTHER_UUID = "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02"
HUB_HOST = {"address": "10.0.0.10", "port": 3713}
ROOT = {"spoke_root": "/hub-data", "hub_root": "/data"}
ARGS = {"hub_cluster_uuid": HUB_UUID, "hub_hosts": [{"address": "10.0.0.10"}], "roots": [ROOT]}
SPOKES = "/v2/portal/spokes/"
SPOKE_ROOT_ID = "100"
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))


def spoke(*, hub=HUB_UUID, hosts=(HUB_HOST,), type_="PORTAL_READ_WRITE", state="PENDING", status="INACTIVE", roots=()):
    return {
        "id": 1,
        "type": type_,
        "state": state,
        "status": status,
        "hub_hosts": list(hosts),
        "hub_id": 3,
        "hub_cluster_uuid": hub,
        "hub_cluster_name": "hub01",
        "filesystem_uuid": hub,
        "roots": list(roots),
    }


def root(local=SPOKE_ROOT_ID, remote="200", remote_path="/data/", authorized=False):
    return {"local_root": local, "remote_root": remote, "remote_root_path": remote_path, "authorized": authorized}


def attributes(path, file_id="100", kind="FS_FILE_TYPE_DIRECTORY"):
    """GET /v1/files/{ref}/info/attributes, reduced to what the module reads; directory paths end in a slash."""
    return {"path": path, "id": file_id, "type": kind}


def ref(path):
    return "/v1/files/" + path.replace("/", "%2F") + "/info/attributes"


MISSING = {"error_class": "fs_no_such_entry_error", "description": "no such entry"}


def expect_root_checks(fake, spoke_root="/hub-data", parent="/"):
    """The spoke root must not exist yet, and its parent must be a directory."""
    fake.expect("GET", ref(spoke_root), status=404, response=MISSING)
    fake.expect("GET", ref(parent), response=attributes(parent if parent == "/" else parent + "/", "2"))


def expect_listed(fake, *entries):
    fake.expect("GET", SPOKES, response={"entries": list(entries)})


def expect_root_path(fake, file_id=SPOKE_ROOT_ID, path="/hub-data/"):
    fake.expect("GET", f"/v1/files/{file_id}/info/attributes", response=attributes(path, file_id))


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


ACCEPTED = {"state": "ACCEPTED", "status": "ACTIVE"}


def test_absent_spoke_is_created_and_its_root_proposed(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    expect_root_checks(fake)
    fake.expect("POST", SPOKES, body={"type": "PORTAL_READ_WRITE", "hub_hosts": [HUB_HOST]}, response=spoke())
    fake.expect("POST", "/v2/portal/spokes/1/roots/", body={"spoke_root_path": "/hub-data", "hub_root_path": "/data"}, response=spoke(roots=[root()]))
    expect_listed(fake, spoke(roots=[root(remote_path=None)]))
    expect_root_path(fake)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["portal"] == {
        "hub_cluster_uuid": HUB_UUID,
        "spoke_cluster_uuid": CLUSTER_ID,
        "hub_hosts": [HUB_HOST],
        "spoke_hosts": None,
        "type": "read_write",
        "state": "PENDING",
        "status": "INACTIVE",
        "roots": [{"spoke_root": "/hub-data", "hub_root": None, "authorized": False}],
    }


def test_read_only_type_is_sent_on_create(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    fake.expect("POST", SPOKES, body={"type": "PORTAL_READ_ONLY", "hub_hosts": [HUB_HOST]}, response=spoke(type_="PORTAL_READ_ONLY"))
    expect_listed(fake, spoke(type_="PORTAL_READ_ONLY"))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [], "type": "read_only"})

    assert result["changed"] is True, result.get("msg")
    assert result["portal"]["type"] == "read_only"


def test_matching_spoke_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED, roots=[root(authorized=True)]))
    expect_root_path(fake)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["portal"] == {
        "hub_cluster_uuid": HUB_UUID,
        "spoke_cluster_uuid": CLUSTER_ID,
        "hub_hosts": [HUB_HOST],
        "spoke_hosts": None,
        "type": "read_write",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "roots": [{"spoke_root": "/hub-data", "hub_root": "/data", "authorized": True}],
    }
    assert writes(fake) == []


def test_hub_uuid_matches_whatever_its_case(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "hub_cluster_uuid": HUB_UUID.upper(), "roots": []})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_hub_uuid_is_reported_in_lower_case(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(hub=HUB_UUID.upper(), **ACCEPTED))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": []})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"]["hub_cluster_uuid"] == HUB_UUID


def test_spoke_to_another_hub_is_not_this_portal(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(hub=OTHER_UUID, **ACCEPTED))
    fake.expect("POST", SPOKES, response=spoke())
    expect_listed(fake, spoke(hub=OTHER_UUID, **ACCEPTED), spoke())

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": []})

    assert result["changed"] is True, result.get("msg")
    assert result["portal"]["hub_cluster_uuid"] == HUB_UUID


def test_same_hub_hosts_in_another_order_send_no_patch(fake_api, run_module):
    """QUIRK-005: a PATCH that repeats the stored hosts would still rewrite the record and re-check connectivity."""
    second = {"address": "10.0.0.11", "port": 3713}
    fake = fake_api()
    expect_listed(fake, spoke(hosts=[HUB_HOST, second], **ACCEPTED))

    hosts = [{"address": "10.0.0.11"}, {"address": "10.0.0.10", "port": 3713}]
    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "hub_hosts": hosts, "roots": []})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_differing_hub_hosts_are_patched_alone(fake_api, run_module):
    moved = {"address": "10.0.0.20", "port": 3713}
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    fake.expect("PATCH", "/v2/portal/spokes/1", body={"hub_hosts": [moved]}, response=spoke(hosts=[moved], **ACCEPTED))
    expect_listed(fake, spoke(hosts=[moved], **ACCEPTED))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "hub_hosts": [{"address": "10.0.0.20"}], "roots": []}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"]["hub_hosts"] == [HUB_HOST]
    assert result["diff"]["after"]["hub_hosts"] == [moved]


def test_unset_hub_hosts_keep_the_stored_ones(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"]["hub_hosts"] == [HUB_HOST]


def test_new_root_is_proposed_on_an_existing_spoke(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    expect_root_checks(fake, "/cache/hub-data", "/cache")
    fake.expect("POST", "/v2/portal/spokes/1/roots/", body={"spoke_root_path": "/cache/hub-data", "hub_root_path": "/data"})
    expect_listed(fake, spoke(**ACCEPTED, roots=[root()]))
    expect_root_path(fake, path="/cache/hub-data/")

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [{"spoke_root": "/cache/hub-data/", "hub_root": "/data/"}]})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == ["POST /v2/portal/spokes/1/roots/"]


def test_adding_a_root_proposes_only_the_new_one(fake_api, run_module):
    """G-PORTAL-ONESHOT: proposing a root that exists already would fail, and cannot be retried."""
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED, roots=[root(authorized=True)]))
    expect_root_path(fake)
    expect_root_checks(fake, "/cache/hub-data", "/cache")
    fake.expect("POST", "/v2/portal/spokes/1/roots/", body={"spoke_root_path": "/cache/hub-data", "hub_root_path": "/data"})
    expect_listed(fake, spoke(**ACCEPTED, roots=[root(authorized=True), root(local="101", authorized=True)]))
    expect_root_path(fake, file_id="101", path="/cache/hub-data/")

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [ROOT, {"spoke_root": "/cache/hub-data", "hub_root": "/data"}]})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == ["POST /v2/portal/spokes/1/roots/"]


def test_root_whose_hub_path_is_not_reported_yet_counts_as_matching(fake_api, run_module):
    """Until the hub reports it, the spoke shows remote_root_path null: the spoke root alone matches."""
    fake = fake_api()
    expect_listed(fake, spoke(roots=[root(remote_path=None)]))
    expect_root_path(fake)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_root_mapped_to_another_hub_root_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED, roots=[root(remote_path="/archive/")]))
    expect_root_path(fake)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["msg"] == (
        "spoke root /hub-data already maps to hub root /archive, not /data; a root cannot change in place, so delete the spoke portal and propose it again"
    )
    assert writes(fake) == []


def test_existing_spoke_root_fails_before_any_write(fake_api, run_module):
    """G-PORTAL-ONESHOT: the server creates the spoke root, and a propose that fails is not safe to retry."""
    fake = fake_api()
    expect_listed(fake)
    fake.expect("GET", ref("/hub-data"), response=attributes("/hub-data/"))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["msg"] == "spoke root /hub-data already exists on this cluster; the server creates it, so choose a path that does not exist"
    assert writes(fake) == []


def test_missing_parent_of_a_spoke_root_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    fake.expect("GET", ref("/cache/hub-data"), status=404, response=MISSING)
    fake.expect("GET", ref("/cache"), status=404, response=MISSING)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [{"spoke_root": "/cache/hub-data", "hub_root": "/data"}]})

    assert result["failed"] is True
    assert result["msg"] == "the parent of spoke root /cache/hub-data, /cache, is not a directory on this cluster"
    assert writes(fake) == []


def test_parent_that_is_a_file_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    fake.expect("GET", ref("/cache/hub-data"), status=404, response=MISSING)
    fake.expect("GET", ref("/cache"), response=attributes("/cache", kind="FS_FILE_TYPE_FILE"))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [{"spoke_root": "/cache/hub-data", "hub_root": "/data"}]})

    assert result["failed"] is True
    assert result["msg"] == "the parent of spoke root /cache/hub-data, /cache, is not a directory on this cluster"
    assert writes(fake) == []


@pytest.mark.parametrize(
    ("spoke_root", "hub_root", "msg"),
    [
        ("hub-data", "/data", "roots[].spoke_root must be an absolute path, not 'hub-data'"),
        ("/hub-data", "data", "roots[].hub_root must be an absolute path, not 'data'"),
        ("/", "/data", "roots[].spoke_root cannot be /: the server creates the spoke root"),
    ],
)
def test_relative_or_root_paths_fail_before_any_request(fake_api, run_module, spoke_root, hub_root, msg):
    fake = fake_api()

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [{"spoke_root": spoke_root, "hub_root": hub_root}]})

    assert result["failed"] is True
    assert result["msg"] == msg
    assert fake.log == []


def test_hub_hosts_reaching_another_cluster_withdraw_the_proposal_and_fail(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    fake.expect("POST", SPOKES, response=spoke(hub=OTHER_UUID))
    fake.expect("DELETE", "/v2/portal/spokes/1", status=202)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": []})

    assert result["failed"] is True
    assert result["msg"] == (f"hub_hosts reach cluster {OTHER_UUID}, not hub_cluster_uuid {HUB_UUID}; the spoke portal this task proposed was withdrawn")
    assert writes(fake) == ["POST /v2/portal/spokes/", "DELETE /v2/portal/spokes/1"]


NO_HUB_ROOT = {"error_class": "fs_no_such_entry_error", "description": "/data not found on hub"}


def test_root_refused_after_the_create_reports_the_change_and_names_the_spoke(fake_api, run_module):
    """The proposed spoke holds a hub slot, PENDING, until it is accepted or deleted."""
    fake = fake_api()
    expect_listed(fake)
    expect_root_checks(fake)
    fake.expect("POST", SPOKES, response=spoke())
    fake.expect("POST", "/v2/portal/spokes/1/roots/", status=404, response=NO_HUB_ROOT)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["msg"] == (
        "POST /v2/portal/spokes/1/roots/ failed: HTTP 404 fs_no_such_entry_error: /data not found on hub; "
        f"this task had already proposed the spoke portal to hub {HUB_UUID} (spoke id 1)"
    )


def test_root_refused_after_a_hub_hosts_patch_reports_the_change(fake_api, run_module):
    moved = {"address": "10.0.0.20", "port": 3713}
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    expect_root_checks(fake)
    fake.expect("PATCH", "/v2/portal/spokes/1", body={"hub_hosts": [moved]})
    fake.expect("POST", "/v2/portal/spokes/1/roots/", status=404, response=NO_HUB_ROOT)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "hub_hosts": [{"address": "10.0.0.20"}]})

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["msg"].endswith("; this task had already changed hub_hosts")


def test_create_refused_reports_no_change(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    fake.expect("POST", SPOKES, status=400, response={"error_class": "portal_multi_paired_cluster_error", "description": "already paired"})

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": []})

    assert result["failed"] is True
    assert result.get("changed", False) is False
    assert writes(fake) == ["POST /v2/portal/spokes/"]


def test_create_without_hub_hosts_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID})

    assert result["failed"] is True
    assert result["msg"] == f"hub_hosts is needed to create the spoke portal to hub {HUB_UUID}"
    assert writes(fake) == []


def test_check_mode_reports_the_create_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    expect_root_checks(fake)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"] == {}
    assert result["diff"]["after"]["roots"] == [{"spoke_root": "/hub-data", "hub_root": "/data", "authorized": False}]
    assert writes(fake) == []


def test_check_mode_reports_a_new_root_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    expect_root_checks(fake)

    result = run_module(portal_spoke, {**CONNECTION, **ARGS}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


def test_differing_type_on_an_existing_spoke_is_noted_not_patched(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": [], "type": "read_only"})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"]["type"] == "read_write"
    assert result["notes"] == ["the hub decides the type of this portal, read_write; change it to read_only with qumulo.cluster.portal_hub"]
    assert writes(fake) == []


def test_two_spokes_to_one_hub_fail(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(), {**spoke(), "id": 2})

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["msg"] == f"this cluster has 2 spoke portals to hub {HUB_UUID} (ids 1, 2); delete all but one"
    assert writes(fake) == []


def test_lost_create_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    fake.expect("POST", SPOKES, raises=LOST)
    expect_listed(fake, spoke())

    result = run_module(portal_spoke, {**CONNECTION, **ARGS, "roots": []})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]


def test_lost_create_with_a_root_reads_back_the_root_as_not_proposed(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)
    expect_root_checks(fake)
    fake.expect("POST", SPOKES, raises=LOST)
    expect_listed(fake, spoke())

    result = run_module(portal_spoke, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert (result["changed"], result["maybe_applied"]) == (True, True)
    assert "reading back did not confirm it: the spoke portal to hub" in result["msg"]
    assert "did not converge on roots" in result["msg"]
    assert writes(fake) == ["POST /v2/portal/spokes/"]


def test_absent_deletes_and_waits_until_gone(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED, type_="PORTAL_READ_ONLY"))
    fake.expect("DELETE", "/v2/portal/spokes/1", status=202)
    fake.expect("GET", "/v2/portal/spokes/1", response=spoke(state="DELETING", status="ACTIVE", type_="PORTAL_READ_ONLY"))
    fake.expect("GET", "/v2/portal/spokes/1", status=404, response={"error_class": "fs_portal_not_found_error", "description": "Spoke 1 not found"})

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["portal"] == {}
    assert result["diff"]["before"]["state"] == "ACCEPTED"
    assert fake_clock.sleeps == [5.0]


def test_absent_without_wait_returns_after_the_delete(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    fake.expect("DELETE", "/v2/portal/spokes/1", status=202)

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent", "wait": False})

    assert result["changed"] is True, result.get("msg")


@pytest.mark.parametrize(("readback", "status"), [(spoke(state="DELETING", status="ACTIVE"), 200), ({"error_class": "fs_portal_not_found_error"}, 404)])
def test_lost_delete_without_wait_is_reconciled_by_one_read(fake_api, run_module, fake_clock, readback, status):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    fake.expect("DELETE", "/v2/portal/spokes/1", raises=LOST)
    fake.expect("GET", "/v2/portal/spokes/1", status=status, response=readback)

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent", "wait": False})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]
    assert fake_clock.sleeps == []


def test_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(hub=OTHER_UUID))

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_absent_in_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


BLOCKED = {"error_class": "blocked_by_portal_transition_error", "description": "portal is quiescing"}


def test_absent_retries_while_a_portal_transition_blocks_the_delete(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, spoke(state="QUIESCING", status="ACTIVE"))
    fake.expect("DELETE", "/v2/portal/spokes/1", status=409, response=BLOCKED)
    fake.expect("DELETE", "/v2/portal/spokes/1", status=409, response=BLOCKED)
    fake.expect("DELETE", "/v2/portal/spokes/1", status=202)
    fake.expect("GET", "/v2/portal/spokes/1", status=404, response={"error_class": "fs_portal_not_found_error"})

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert fake_clock.sleeps == [5.0, 5.0]


def test_absent_blocked_past_the_timeout_fails_with_the_reason(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(state="QUIESCING", status="ACTIVE"))
    for _attempt in range(3):
        fake.expect("DELETE", "/v2/portal/spokes/1", status=409, response=BLOCKED)

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent", "wait_timeout": 10})

    assert result["failed"] is True
    assert result["msg"].startswith(f"the spoke portal to hub {HUB_UUID} could not be deleted within 10s: ")
    assert "blocked_by_portal_transition_error" in result["msg"]
    assert result["changed"] is False


def test_absent_that_never_finishes_fails_with_the_last_state(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    fake.expect("DELETE", "/v2/portal/spokes/1", status=202)
    for _attempt in range(3):
        fake.expect("GET", "/v2/portal/spokes/1", response=spoke(state="DELETING", status="ACTIVE"))

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent", "wait_timeout": 7})

    assert result["failed"] is True
    assert result["msg"] == f"the spoke portal to hub {HUB_UUID} is still DELETING (status ACTIVE) after 7s"
    assert result["changed"] is True
    assert fake_clock.sleeps == [5.0, 2.0]


def test_absent_on_a_disconnected_spoke_fails_without_allow_data_loss(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(state="ACCEPTED", status="DISCONNECTED"))

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent"}, check_mode=True)

    assert result["failed"] is True
    assert result["msg"] == (
        f"the spoke portal to hub {HUB_UUID} is DISCONNECTED, so deleting it discards spoke writes the hub has not received; "
        "set allow_data_loss=true to force the delete"
    )
    assert writes(fake) == []


def test_absent_on_a_disconnected_spoke_forces_with_allow_data_loss(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, spoke(state="ACCEPTED", status="DISCONNECTED"))
    fake.expect("DELETE", "/v2/portal/spokes/1?force=true", status=202)
    fake.expect("GET", "/v2/portal/spokes/1", status=404, response={"error_class": "fs_portal_not_found_error"})

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent", "allow_data_loss": True})

    assert result["changed"] is True, result.get("msg")


@pytest.mark.parametrize("unanswered", [{"status": 503, "response": None}, {"raises": LOST}], ids=["503", "connection-reset"])
def test_unreachable_cluster_while_waiting_is_waited_out(fake_api, run_module, fake_clock, unanswered):
    """Deleting a portal can make the cluster unavailable for a short while; the wait carries on."""
    fake = fake_api()
    expect_listed(fake, spoke(**ACCEPTED))
    fake.expect("DELETE", "/v2/portal/spokes/1", status=202)
    fake.expect("GET", "/v2/portal/spokes/1", **unanswered)
    fake.expect("GET", "/v2/portal/spokes/1", status=404, response={"error_class": "fs_portal_not_found_error"})

    result = run_module(portal_spoke, {**CONNECTION, "hub_cluster_uuid": HUB_UUID, "state": "absent", "retry_timeout": 0})

    assert result["changed"] is True, result.get("msg")
