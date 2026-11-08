# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The portal area's shared pieces: the shape both sides report, path and type forms, and the version-gated states."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import portal
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.plugins.modules import portal_hub
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, client_params

SPOKE_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
HOST = {"address": "10.0.0.10", "port": 3713}
SHAPE = {"hub_cluster_uuid", "spoke_cluster_uuid", "hub_hosts", "spoke_hosts", "type", "state", "status", "roots"}


def client():
    return QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID))


def attributes(fake, file_id, path):
    fake.expect("GET", f"/v1/files/{file_id}/info/attributes", response={"path": path, "id": file_id, "type": "FS_FILE_TYPE_DIRECTORY"})


SPOKE = {
    "id": 1,
    "type": "PORTAL_READ_ONLY",
    "state": "ACCEPTED",
    "status": "ACTIVE",
    "hub_hosts": [HOST],
    "hub_id": 3,
    "hub_cluster_uuid": HUB_UUID,
    "hub_cluster_name": "hub01",
    "filesystem_uuid": HUB_UUID,
    "roots": [{"local_root": "100", "remote_root": "200", "remote_root_path": "/data/", "authorized": True}],
}
HUB = {
    "id": 3,
    "type": "PORTAL_READ_ONLY",
    "state": "ACCEPTED",
    "status": "ACTIVE",
    "spoke_hosts": [HOST],
    "spoke_cluster_uuid": SPOKE_UUID,
    "spoke_cluster_name": "edge01",
    "filesystem_uuid": CLUSTER_ID,
    "pending_roots": ["201"],
    "authorized_roots": ["200"],
}


def test_a_spoke_is_described_by_paths_and_option_types(fake_api):
    fake = fake_api()
    attributes(fake, "100", "/hub-data/")

    spoke = portal.describe_spoke(client(), SPOKE)

    assert set(spoke) == SHAPE
    assert spoke == {
        "hub_cluster_uuid": HUB_UUID,
        "spoke_cluster_uuid": CLUSTER_ID,
        "hub_hosts": [HOST],
        "spoke_hosts": None,
        "type": "read_only",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "roots": [{"spoke_root": "/hub-data", "hub_root": "/data", "authorized": True}],
    }


def test_a_hub_is_described_in_the_spoke_shape(fake_api):
    fake = fake_api()
    attributes(fake, "201", "/archive/")
    attributes(fake, "200", "/data/")

    hub = portal.describe_hub(client(), HUB)

    assert set(hub) == SHAPE
    assert hub == {
        "hub_cluster_uuid": CLUSTER_ID,
        "spoke_cluster_uuid": SPOKE_UUID,
        "hub_hosts": None,
        "spoke_hosts": [HOST],
        "type": "read_only",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "roots": [
            {"spoke_root": None, "hub_root": "/archive", "authorized": False},
            {"spoke_root": None, "hub_root": "/data", "authorized": True},
        ],
    }


def test_a_root_whose_directory_is_gone_has_no_path(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/files/200/info/attributes", status=404, response={"error_class": "fs_no_such_entry_error"})

    assert portal.path_of(client(), "200") is None


def test_a_path_is_read_once_per_file_id(fake_api):
    fake = fake_api()
    attributes(fake, "200", "/data/")
    qumulo = client()

    assert [portal.path_of(qumulo, "200"), portal.path_of(qumulo, "200")] == ["/data", "/data"]


@pytest.mark.parametrize(("given", "canonical"), [("/data", "/data"), ("/data/", "/data"), ("/a/b//", "/a/b"), ("/", "/")])
def test_paths_compare_without_a_trailing_slash(given, canonical):
    assert portal.canonical_path(given, "root") == canonical


def test_a_relative_path_is_refused():
    with pytest.raises(QumuloError, match="root must be an absolute path, not 'data'"):
        portal.canonical_path("data", "root")


@pytest.mark.parametrize(("wire", "option"), [("PORTAL_READ_WRITE", "read_write"), ("PORTAL_READ_ONLY", "read_only")])
def test_types_convert_both_ways(wire, option):
    assert (portal.option_type(wire), portal.wire_type(option)) == (option, wire)


@pytest.mark.parametrize(
    ("version", "suspending"),
    [("7.10.3", False), ("7.10.3A", False), ("7.11.0", True)],
)
def test_suspending_is_a_transition_from_7_11(fake_api, version, suspending):
    fake_api(version=version)

    states = portal.transitional_states(client())

    assert {"QUIESCING", "PAUSING", "DELETING"} <= states
    assert ("SUSPENDING" in states) is suspending


# QUIRK-005: a PATCH that repeats the stored type still rewrites the hub record and re-runs its connectivity checks,
# so the module sends no PATCH when the type matches.
def test_same_type_sends_no_patch(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v2/portal/hubs/", response={"entries": [{**HUB, "pending_roots": [], "authorized_roots": []}]})

    result = run_module(portal_hub, {"api_host": "hub01", "api_password": "secret", "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only"})

    assert result["changed"] is False, result.get("msg")
    assert [e.method for e in fake.log if not e.background] == ["GET"]


def test_spoke_roots_map_each_root_that_still_exists_to_its_hub(fake_api):
    fake = fake_api()
    gone = {**SPOKE, "id": 2, "hub_cluster_uuid": HUB_UUID.upper(), "roots": [{"local_root": "101", "remote_root": "201", "authorized": True}]}
    fake.expect("GET", "/v2/portal/spokes/", response={"entries": [SPOKE, gone]})
    attributes(fake, "100", "/edge-data/")
    fake.expect("GET", "/v1/files/101/info/attributes", status=404, response={"error_class": "fs_no_such_entry_error"})

    assert portal.spoke_roots(client()) == {"/edge-data": HUB_UUID}
