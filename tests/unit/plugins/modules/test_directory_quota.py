# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.directory_quota, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import directory_quota
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
# Directory ids are 128-bit numbers the server writes as decimal strings; the quota routes take them verbatim.
DIR_ID = "126542065494555443589290131466"
QUOTA = f"/v1/files/quotas/{DIR_ID}"
TB, TIB = 10**12, 2**40


def quota(limit):
    return {"id": DIR_ID, "limit": str(limit)}


def expect_directory(fake, path="/data"):
    fake.expect("GET", attributes_path(path), response=attributes(path, file_id=DIR_ID), etag='"9"')


def expect_no_quota(fake):
    fake.expect("GET", QUOTA, status=404, response=error("api_quotas_quota_limit_not_found_error", "This quota no longer exists."))


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("POST", "PUT", "DELETE") and not e.background]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(directory_quota, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_absent_quota_is_created_with_the_id_the_server_returned(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    expect_no_quota(fake)
    fake.expect("POST", "/v1/files/quotas/", body=quota(TB), response=quota(TB), etag='"a"')
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(TB), etag='"a"')

    result = run(run_module, path="/data", limit="1TB")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["quota"] == {"path": "/data", "limit": TB}


@pytest.mark.parametrize("limit", ["1TB", "1000000000000", "1 tb", "0.001PB"])
def test_matching_limit_is_unchanged_and_sends_no_write(fake_api, run_module, limit):
    fake = fake_api()
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(TB), etag='"a"')

    result = run(run_module, path="/data/", limit=limit)

    assert result["changed"] is False
    assert writes(fake) == []


def test_differing_limit_puts_with_if_match_from_get(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(TB), etag='"a"')
    fake.expect("PUT", QUOTA, body=quota(2 * TIB), if_match='"a"', response=quota(2 * TIB), etag='"b"')
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(2 * TIB), etag='"b"')

    result = run(run_module, path="/data", limit="2TiB")

    assert result["changed"] is True
    assert result["quota"]["limit"] == 2 * TIB


def test_limit_changed_by_another_writer_is_read_again_after_a_412(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(TB), etag='"a"')
    fake.expect("PUT", QUOTA, body=quota(2 * TIB), if_match='"a"', status=412, response=error("http_precondition_failed_error"))
    fake.expect("GET", QUOTA, response=quota(3 * TB), etag='"c"')
    fake.expect("PUT", QUOTA, body=quota(2 * TIB), if_match='"c"', response=quota(2 * TIB), etag='"d"')
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(2 * TIB), etag='"d"')

    result = run(run_module, path="/data", limit="2TiB")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize(("stored", "wanted"), [(TB - 1, TB), (TB + 1, TB)], ids=["one_byte_below", "one_byte_above"])
def test_limit_one_byte_off_is_a_change(fake_api, run_module, stored, wanted):
    fake = fake_api()
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(stored), etag='"a"')

    result = run(run_module, check_mode=True, path="/data", limit=str(wanted))

    assert result["changed"] is True


def test_check_mode_reports_changed_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    expect_no_quota(fake)

    result = run(run_module, check_mode=True, path="/data", limit="1TB")

    assert result["changed"] is True
    assert writes(fake) == []


def test_check_mode_on_a_directory_an_earlier_task_creates_is_a_change(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", attributes_path("/data"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, check_mode=True, path="/data", limit="1TB")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["notes"] == ["/data does not exist yet; the quota is set once an earlier task creates it"]


def test_missing_directory_fails_outside_check_mode(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", attributes_path("/data"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, path="/data", limit="1TB")

    assert result["failed"] is True
    assert result["msg"] == "/data does not exist; create it first, for example with qumulo.cluster.directory"


def test_file_at_the_path_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", kind="FS_FILE_TYPE_FILE"))

    result = run(run_module, path="/data", limit="1TB")

    assert result["failed"] is True
    assert result["msg"] == "/data exists and is not a directory (FS_FILE_TYPE_FILE)"


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(TB), etag='"a"')

    result = run(run_module, check_mode=True, diff=True, path="/data", limit="2TB")

    assert result["diff"] == {"before": {"path": "/data", "limit": TB}, "after": {"path": "/data", "limit": 2 * TB}}


def test_quota_the_cluster_refuses_inside_a_spoke_root_fails_with_the_reason(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, "/spoke/data")
    expect_no_quota(fake)
    fake.expect("POST", "/v1/files/quotas/", status=400, response=error("fs_operation_unsupported_error"))

    result = run(run_module, path="/spoke/data", limit="1TB")

    assert result["failed"] is True
    assert result["msg"] == (
        "the cluster refuses a quota on /spoke/data, as it does on a directory inside a portal spoke's root: "
        "POST /v1/files/quotas/ failed: HTTP 400 fs_operation_unsupported_error: test"
    )


def test_quota_the_cluster_refuses_to_read_inside_a_spoke_root_fails_with_the_reason(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, "/spoke/data")
    fake.expect("GET", QUOTA, status=400, response=error("fs_operation_unsupported_error"))

    result = run(run_module, path="/spoke/data", limit="1TB")

    assert result["failed"] is True
    assert result["msg"] == (
        "the cluster refuses a quota on /spoke/data, as it does on a directory inside a portal spoke's root: "
        f"GET {QUOTA} failed: HTTP 400 fs_operation_unsupported_error: test"
    )


def test_state_absent_inside_a_spoke_root_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake, "/spoke/data")
    fake.expect("GET", QUOTA, status=400, response=error("fs_operation_unsupported_error"))

    result = run(run_module, path="/spoke/data", state="absent")

    assert not result.get("failed"), result["msg"]
    assert result["changed"] is False


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"path": "/data", "limit": "1 TBX"}, "'1 TBX' is not a size"),
        ({"path": "/data", "limit": "-5"}, "'-5' is not a size"),
        ({"path": "/data", "limit": "1.5"}, "'1.5' is not a whole number of bytes"),
        ({"path": "data", "limit": "1TB"}, "path 'data' must be an absolute path"),
    ],
    ids=["unknown_unit", "negative", "fraction", "relative"],
)
def test_bad_options_fail_before_any_request(fake_api, run_module, args, message):
    fake = fake_api()

    result = run(run_module, **args)

    assert result["failed"] is True
    assert result["msg"].startswith(message), result["msg"]
    assert fake.log == []


def test_state_present_needs_a_limit(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, path="/data")

    assert result["failed"] is True
    assert "limit" in result["msg"]
    assert fake.log == []


def test_state_absent_deletes(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    fake.expect("GET", QUOTA, response=quota(TB), etag='"a"')
    fake.expect("DELETE", QUOTA)

    result = run(run_module, path="/data", state="absent")

    assert result["changed"] is True
    assert result["quota"] == {}


def test_state_absent_without_a_quota_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_directory(fake)
    expect_no_quota(fake)

    result = run(run_module, path="/data", state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


def test_state_absent_on_a_missing_directory_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", attributes_path("/data"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, path="/data", state="absent")

    assert result["changed"] is False
    assert writes(fake) == []
