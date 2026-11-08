# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.filesystem_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils import files
from ansible_collections.qumulo.cluster.plugins.modules import filesystem_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
FILE_SYSTEM = {"block_size_bytes": 4096, "total_size_bytes": "1000", "free_size_bytes": "600", "snapshot_size_bytes": "0", "portal_cache_size_bytes": "0"}
CAPACITY = {"block_size_bytes": 4096, "total_size_bytes": 1000, "free_size_bytes": 600, "snapshot_size_bytes": 0, "portal_cache_size_bytes": 0}
QUOTAS = "/v1/files/quotas/status/"


def quota(path, limit=10**9, usage=4096):
    return {"id": "1", "path": path, "limit": str(limit), "capacity_usage": str(usage)}


def test_default_subsets(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/file-system", response=FILE_SYSTEM)
    fake.expect("GET", attributes_path("/"), response=attributes("/", mode="0777"), etag='"r"')
    fake.expect("GET", f"{QUOTAS}?limit=1000", response={"quotas": [quota("/data/")], "paging": {"next": ""}})

    result = run_module(filesystem_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["filesystem"] == {
        "capacity": CAPACITY,
        "root": {"path": "/", "mode": "0777", "owner": "local:admin", "group": "local:Users"},
        "quotas": [{"path": "/data", "limit": 10**9, "capacity_usage": 4096}],
    }


def test_quotas_subset_follows_the_next_link(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", f"{QUOTAS}?limit=1000", response={"quotas": [quota("/a/")], "paging": {"next": f"{QUOTAS}?after=3&limit=1000"}})
    fake.expect("GET", f"{QUOTAS}?after=3&limit=1000", response={"quotas": [quota("/b/")], "paging": {"next": ""}})

    result = run_module(filesystem_info, {**CONNECTION, "gather_subset": ["quotas"]})

    assert [q["path"] for q in result["filesystem"]["quotas"]] == ["/a", "/b"]


LOOKUPS = "/lookups"
LOOKUPS_ID = "1003"
FOUND = {"found": True, "path": LOOKUPS, "id": LOOKUPS_ID, "type": files.DIRECTORY}
MISS = {"found": False, "path": None, "id": None, "type": None}


def lookup_reads(fake):
    fake.expect("GET", attributes_path(LOOKUPS), response=attributes(LOOKUPS, file_id=LOOKUPS_ID))
    fake.expect("GET", attributes_path("/missing"), status=404, response=error("fs_no_such_entry_error"))
    fake.expect("GET", attributes_path(LOOKUPS_ID), response=attributes(LOOKUPS, file_id=LOOKUPS_ID))


def test_with_no_subsets_only_the_lookup_runs(fake_api, run_module):
    lookup_reads(fake_api())

    result = run_module(filesystem_info, {**CONNECTION, "gather_subset": [], "find_files": [f"{LOOKUPS}/", "/missing", LOOKUPS_ID]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["filesystem"] == {"found_files": {f"{LOOKUPS}/": FOUND, "/missing": MISS, LOOKUPS_ID: FOUND}}
    assert result["warnings"] == []


def test_the_lookup_runs_beside_the_subsets_it_is_given(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/file-system", response=FILE_SYSTEM)
    fake.expect("GET", attributes_path(LOOKUPS), response=attributes(LOOKUPS, file_id=LOOKUPS_ID))

    result = run_module(filesystem_info, {**CONNECTION, "gather_subset": ["capacity"], "find_files": [LOOKUPS]})

    assert result["filesystem"] == {"capacity": CAPACITY, "found_files": {LOOKUPS: FOUND}}


def test_an_empty_lookup_runs_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/file-system", response=FILE_SYSTEM)

    result = run_module(filesystem_info, {**CONNECTION, "gather_subset": ["capacity"], "find_files": []})

    assert list(result["filesystem"]) == ["capacity"]


def test_a_bad_term_fails_before_any_request_and_any_subset(fake_api, run_module):
    fake = fake_api()

    result = run_module(filesystem_info, {**CONNECTION, "find_files": [LOOKUPS, "relative/path"]})

    assert result["failed"] is True
    assert "find_files term 'relative/path'" in result["msg"]
    assert "accepted forms" in result["msg"]
    assert fake.log == []


def test_check_mode_sends_the_same_reads_and_returns_the_same_result(fake_api, run_module):
    args = {**CONNECTION, "gather_subset": [], "find_files": [f"{LOOKUPS}/", "/missing", LOOKUPS_ID]}
    first = fake_api()
    lookup_reads(first)
    run = run_module(filesystem_info, args)
    second = fake_api()
    lookup_reads(second)

    checked = run_module(filesystem_info, args, check_mode=True)

    assert checked == run
    assert checked["changed"] is False
    assert [(e.method, e.path) for e in second.log] == [(e.method, e.path) for e in first.log]
