# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.s3_bucket, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import s3_bucket
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
BUCKETS = "/v1/s3/buckets/"
BUCKET = "/v1/s3/buckets/photos"
SPOKES = "/v2/portal/spokes/"
NO_SPOKES = {"entries": []}
UNLOCKED = {"enabled": False, "default_retention": None}


def bucket(name="photos", path="/s3/photos", versioning="Unversioned", lock=None):
    """A bucket as GET /v1/s3/buckets/ lists it."""
    return {
        "name": name,
        "creation_time": "2026-11-01T00:00:00Z",
        "path": path,
        "anonymous_access_enabled": False,
        "versioning": versioning,
        "lock_config": lock or dict(UNLOCKED),
    }


def listed(*buckets):
    return {"buckets": list(buckets)}


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(s3_bucket, {**CONNECTION, "name": "photos", **args}, check_mode=check_mode, diff=diff)


def expect_new_path(fake, path="/s3/photos"):
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("GET", attributes_path(path), response=attributes(path))


def test_absent_bucket_is_created_at_its_path(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket("other", "/s3/other")))
    expect_new_path(fake)
    fake.expect("POST", BUCKETS, body={"name": "photos", "path": "/s3/photos", "create_fs_path": False, "object_lock_enabled": False}, response=bucket())
    fake.expect("GET", BUCKETS, response=listed(bucket("other", "/s3/other"), bucket()))

    result = run(run_module, path="/s3/photos/")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket"] == {"name": "photos", "path": "/s3/photos", "versioning": "Unversioned", "object_lock": False, "default_retention": None}


def test_matching_bucket_is_unchanged_and_sends_no_write(fake_api, run_module):
    lock = {"enabled": True, "default_retention": {"units": "days", "value": 30}}
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock=lock)))

    result = run(run_module, path="/s3/photos", versioning="Enabled", object_lock=True, default_retention={"value": 30, "units": "days"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_create_with_versioning_and_retention_patches_after_the_post(fake_api, run_module):
    lock = {"enabled": True, "default_retention": {"units": "years", "value": 1}}
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    expect_new_path(fake)
    fake.expect(
        "POST",
        BUCKETS,
        body={"name": "photos", "path": "/s3/photos", "create_fs_path": False, "object_lock_enabled": True},
        response=bucket(versioning="Enabled", lock={"enabled": True, "default_retention": None}),
    )
    fake.expect("PATCH", BUCKET, body={"lock_config": {"enabled": True, "default_retention": {"value": 1, "units": "years"}}}, if_match=NO_HEADER)
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock=lock)))

    result = run(run_module, path="/s3/photos", object_lock=True, default_retention={"value": 1, "units": "years"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket"]["versioning"] == "Enabled"
    assert result["s3_bucket"]["default_retention"] == {"value": 1, "units": "years"}


def test_create_fs_path_creates_the_directory_without_checking_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("POST", BUCKETS, body={"name": "photos", "path": "/s3/photos", "create_fs_path": True, "object_lock_enabled": False}, response=bucket())
    fake.expect("GET", BUCKETS, response=listed(bucket()))

    result = run(run_module, path="/s3/photos", create_fs_path=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_creating_without_a_path_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())

    result = run(run_module)

    assert result["failed"] is True
    assert "set path" in result["msg"]
    assert writes(fake) == []


# QUIRK-051: a bucket created at a missing directory is answered with the error class of a missing route.
def test_missing_path_fails_before_the_post(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("GET", attributes_path("/s3/photos"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, path="/s3/photos")

    assert result["failed"] is True
    assert "path /s3/photos does not exist" in result["msg"]
    assert writes(fake) == []


def test_missing_path_in_check_mode_is_a_change_with_a_note(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("GET", attributes_path("/s3/photos"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, check_mode=True, diff=True, path="/s3/photos")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert any("does not exist yet" in note for note in result["notes"])
    assert result["diff"]["before"] == {}
    assert writes(fake) == []


@pytest.mark.parametrize("path", ["/edge/projects", "/edge/projects/photos"])
def test_a_path_in_a_spoke_root_is_refused_before_any_write(fake_api, run_module, path):
    spoke = {
        "id": "1",
        "hub_cluster_uuid": "4b3c802f-8201-42b7-bb41-28f562cd8dfd",
        "hub_hosts": [{"address": "10.0.0.1", "port": 3713}],
        "type": "PORTAL_READ_WRITE",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "roots": [{"local_root": "200", "remote_root": "300", "remote_root_path": "/projects/", "authorized": True}],
    }
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, response={"entries": [spoke]})
    fake.expect("GET", attributes_path("200"), response=attributes("/edge/projects", file_id="200"))

    result = run(run_module, path=path, create_fs_path=True)

    assert result["failed"] is True
    assert "spoke root /edge/projects" in result["msg"]
    assert writes(fake) == []


def test_a_path_that_only_starts_like_a_spoke_root_is_outside_it(fake_api, run_module):
    spoke = {"id": "1", "roots": [{"local_root": "200", "remote_root": "300", "remote_root_path": "/projects/", "authorized": True}]}
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, response={"entries": [spoke]})
    fake.expect("GET", attributes_path("200"), response=attributes("/edge/projects", file_id="200"))
    fake.expect("POST", BUCKETS, response=bucket(path="/edge/projects2"))
    fake.expect("GET", BUCKETS, response=listed(bucket(path="/edge/projects2")))

    result = run(run_module, path="/edge/projects2", create_fs_path=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_cluster_without_portals_has_no_spoke_roots(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, status=404, response=error("http_not_found_error"))
    fake.expect("POST", BUCKETS, response=bucket())
    fake.expect("GET", BUCKETS, response=listed(bucket()))

    result = run(run_module, path="/s3/photos", create_fs_path=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_new_path_for_an_existing_bucket_is_refused(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))

    result = run(run_module, path="/s3/elsewhere")

    assert result["failed"] is True
    assert "G-S3-PATH" in result["msg"]
    assert writes(fake) == []


def test_versioning_change_patches_only_versioning(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))
    fake.expect("PATCH", BUCKET, body={"versioning": "Enabled"}, if_match=NO_HEADER)
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled")))

    result = run(run_module, path="/s3/photos", versioning="Enabled")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


# QUIRK-052: a PATCH of lock_config replaces it whole, so leaving out default_retention would clear it.
def test_lock_config_is_always_sent_whole(fake_api, run_module):
    stored = {"enabled": True, "default_retention": {"units": "days", "value": 30}}
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock=stored)))
    fake.expect("PATCH", BUCKET, body={"lock_config": {"enabled": True, "default_retention": {"value": 7, "units": "days"}}})
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock={"enabled": True, "default_retention": {"units": "days", "value": 7}})))

    result = run(run_module, default_retention={"value": 7, "units": "days"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_an_empty_default_retention_clears_it(fake_api, run_module):
    stored = {"enabled": True, "default_retention": {"units": "days", "value": 30}}
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock=stored)))
    fake.expect("PATCH", BUCKET, body={"lock_config": {"enabled": True, "default_retention": None}})
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock={"enabled": True, "default_retention": None})))

    result = run(run_module, default_retention={})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket"]["default_retention"] is None


def test_locking_an_existing_bucket_turns_versioning_on_in_the_same_patch(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))
    fake.expect("PATCH", BUCKET, body={"versioning": "Enabled", "lock_config": {"enabled": True, "default_retention": None}})
    fake.expect("GET", BUCKETS, response=listed(bucket(versioning="Enabled", lock={"enabled": True, "default_retention": None})))

    result = run(run_module, versioning="Enabled", object_lock=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize(
    ("stored", "args", "says"),
    [
        (bucket(versioning="Suspended"), {"versioning": "Unversioned"}, "Unversioned"),
        (bucket(versioning="Enabled", lock={"enabled": True, "default_retention": None}), {"object_lock": False}, "object lock"),
        (bucket(versioning="Enabled", lock={"enabled": True, "default_retention": None}), {"versioning": "Suspended"}, "Enabled"),
        (bucket(), {"object_lock": True}, "versioning: Enabled"),
        (bucket(), {"default_retention": {"value": 1, "units": "days"}}, "object_lock"),
    ],
)
def test_a_transition_the_cluster_refuses_fails_before_any_write(fake_api, run_module, stored, args, says):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(stored))

    result = run(run_module, check_mode=True, **args)

    assert result["failed"] is True
    assert says in result["msg"]
    assert "G-S3-LOCK" in result["msg"]
    assert writes(fake) == []


def test_check_mode_with_diff_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))

    result = run(run_module, check_mode=True, diff=True, versioning="Suspended")

    assert result["changed"] is True
    assert result["diff"]["before"]["versioning"] == "Unversioned"
    assert result["diff"]["after"]["versioning"] == "Suspended"
    assert writes(fake) == []


def test_state_absent_deletes_and_keeps_the_directory(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))
    fake.expect("GET", f"{BUCKET}/uploads/?limit=1000", response={"uploads": [], "paging": {"next": None}})
    fake.expect("DELETE", f"{BUCKET}?delete-root-dir=false", if_match=NO_HEADER)

    result = run(run_module, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket"] == {}


def test_state_absent_on_a_missing_bucket_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket("other", "/s3/other")))

    result = run(run_module, state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check mode"])
def test_unfinished_uploads_block_the_delete_without_allow_data_loss(fake_api, run_module, check_mode):
    """The cluster removes no bucket with an unfinished upload, so check mode predicts the refusal too."""
    uploads = {"uploads": [{"id": "u1", "key": "big.iso", "initiated": "2026-11-01T00:00:00Z"}], "paging": {"next": None}}
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))
    fake.expect("GET", f"{BUCKET}/uploads/?limit=1000", response=uploads)

    result = run(run_module, check_mode=check_mode, state="absent")

    assert result["failed"] is True
    assert "1 unfinished upload" in result["msg"]
    assert "allow_data_loss to abort" in result["msg"]
    assert writes(fake) == []


def test_allow_data_loss_aborts_the_unfinished_uploads_then_deletes(fake_api, run_module):
    """The cluster refuses to remove a bucket with unfinished uploads, so allow_data_loss aborts them first."""
    uploads = {
        "uploads": [{"id": "u1", "key": "big.iso", "initiated": "2026-11-01T00:00:00Z"}, {"id": "u2", "key": "big.iso", "initiated": "2026-11-01T00:00:01Z"}],
        "paging": {"next": None},
    }
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))
    fake.expect("GET", f"{BUCKET}/uploads/?limit=1000", response=uploads)
    fake.expect("DELETE", f"{BUCKET}/uploads/u1")
    fake.expect("DELETE", f"{BUCKET}/uploads/u2", status=404, response=error("http_not_found_error", "NoSuchUpload"))
    fake.expect("DELETE", f"{BUCKET}?delete-root-dir=false")

    result = run(run_module, state="absent", allow_data_loss=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_lost_delete_is_reconciled_by_listing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed(bucket()))
    fake.expect("GET", f"{BUCKET}/uploads/?limit=1000", response={"uploads": [], "paging": {"next": None}})
    fake.expect("DELETE", f"{BUCKET}?delete-root-dir=false", raises=ConnectionResetError("reset"))
    fake.expect("GET", BUCKETS, response=listed())

    result = run(run_module, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["notes"] == ["write reconciled after a lost connection"]


def test_a_lost_create_is_reconciled_by_listing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", BUCKETS, response=listed())
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("POST", BUCKETS, raises=ConnectionResetError("reset"))
    fake.expect("GET", BUCKETS, response=listed(bucket()))

    result = run(run_module, path="/s3/photos", create_fs_path=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["notes"] == ["write reconciled after a lost connection"]
