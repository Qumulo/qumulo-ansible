# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.portal_info, driven through FakeQumulo. Response bodies follow what Qumulo Core 7.10.3 returns."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import portal_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
SPOKE_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
HOST = {"address": "10.0.0.10", "port": 3713}
SPOKE = {
    "id": 1,
    "type": "PORTAL_READ_WRITE",
    "state": "ACCEPTED",
    "status": "ACTIVE",
    "hub_hosts": [HOST],
    "hub_id": 3,
    "hub_cluster_uuid": HUB_UUID,
    "hub_cluster_name": "hub01",
    "filesystem_uuid": HUB_UUID,
    "roots": [{"local_root": "100", "remote_root": "900", "remote_root_path": "/data/", "authorized": True}],
}
HUB = {
    "id": 3,
    "type": "PORTAL_READ_ONLY",
    "state": "PENDING",
    "status": "INACTIVE",
    "spoke_hosts": [],
    "spoke_cluster_uuid": SPOKE_UUID,
    "spoke_cluster_name": "edge02",
    "filesystem_uuid": CLUSTER_ID,
    "pending_roots": ["200"],
    "authorized_roots": [],
}
FILE_SYSTEM = {
    "block_size_bytes": 4096,
    "total_size_bytes": "57263472640",
    "free_size_bytes": "56079409152",
    "snapshot_size_bytes": "0",
    "portal_cache_size_bytes": "1048576",
}


def attributes(fake, file_id, path):
    fake.expect("GET", f"/v1/files/{file_id}/info/attributes", response={"path": path, "id": file_id, "type": "FS_FILE_TYPE_DIRECTORY"})


def expect_everything(fake):
    fake.expect("GET", "/v2/portal/spokes/", response={"entries": [SPOKE]})
    attributes(fake, "100", "/hub-data/")
    fake.expect("GET", "/v2/portal/hubs/", response={"entries": [HUB]})
    attributes(fake, "200", "/archive/")
    fake.expect("GET", "/v1/portal/spokes/eviction-settings", response={"free_threshold": 0.05}, etag='"NFlHng"')
    fake.expect("GET", "/v1/file-system", response=FILE_SYSTEM)


def test_default_gathers_spokes_hubs_eviction_and_cache(fake_api, run_module):
    expect_everything(fake_api())

    result = run_module(portal_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["portals"] == {
        "spokes": [
            {
                "hub_cluster_uuid": HUB_UUID,
                "spoke_cluster_uuid": CLUSTER_ID,
                "hub_hosts": [HOST],
                "spoke_hosts": None,
                "type": "read_write",
                "state": "ACCEPTED",
                "status": "ACTIVE",
                "roots": [{"spoke_root": "/hub-data", "hub_root": "/data", "authorized": True}],
            }
        ],
        "hubs": [
            {
                "hub_cluster_uuid": CLUSTER_ID,
                "spoke_cluster_uuid": SPOKE_UUID,
                "hub_hosts": None,
                "spoke_hosts": [],
                "type": "read_only",
                "state": "PENDING",
                "status": "INACTIVE",
                "roots": [{"spoke_root": None, "hub_root": "/archive", "authorized": False}],
            }
        ],
        "eviction_settings": {"free_threshold": 0.05},
        "portal_cache_size_bytes": 1048576,
    }


def test_gather_subset_reads_only_what_it_names(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v2/portal/hubs/", response={"entries": []})

    result = run_module(portal_info, {**CONNECTION, "gather_subset": ["hubs"]})

    assert result["portals"] == {"hubs": []}
    assert [e.path for e in fake.log if not e.background] == ["/v2/portal/hubs/"]


def test_a_cluster_without_portals_reports_empty_lists(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v2/portal/spokes/", response={"entries": []})
    fake.expect("GET", "/v2/portal/hubs/", response={"entries": []})

    result = run_module(portal_info, {**CONNECTION, "gather_subset": ["spokes", "hubs"]})

    assert result["portals"] == {"spokes": [], "hubs": []}


def test_a_file_system_without_a_cache_size_reports_null(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/file-system", response={key: value for key, value in FILE_SYSTEM.items() if key != "portal_cache_size_bytes"})

    result = run_module(portal_info, {**CONNECTION, "gather_subset": ["cache"]})

    assert result["portals"] == {"portal_cache_size_bytes": None}
