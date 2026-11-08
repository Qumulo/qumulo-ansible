# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.s3_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import s3_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import GUEST

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = {"enabled": True, "base_path": "/s3/", "multipart_upload_expiry_interval": "2weeks", "secure": True}
LOCKED = {"enabled": True, "default_retention": {"units": "days", "value": 30}}
POLICY = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["world:Everyone"]}}]}


def bucket(name, path, versioning="Unversioned", lock=None):
    return {
        "name": name,
        "creation_time": "2026-11-01T00:00:00Z",
        "path": path,
        "anonymous_access_enabled": False,
        "versioning": versioning,
        "lock_config": lock or {"enabled": False, "default_retention": None},
    }


def serve_all(fake):
    fake.serve("/v1/s3/settings", SETTINGS)
    fake.serve("/v1/s3/buckets/", {"buckets": [bucket("photos", "/s3/photos/", "Enabled", LOCKED), bucket("scratch", "/s3/scratch")]})
    fake.serve("/v1/s3/buckets/photos/policy", POLICY)
    fake.serve("/v1/s3/buckets/scratch/policy", {})
    fake.serve(
        "/v1/s3/access-keys/?limit=1000",
        # A listing carries no secret; one here shows that the info shape would drop it anyway.
        {
            "entries": [{"access_key_id": "00000000000001f5a001", "owner": dict(GUEST), "creation_time": "2026-11-01T00:00:00Z", "secret_access_key": "x"}],
            "paging": {"next": None},
        },
    )


def test_every_subset_in_the_option_shapes_of_the_s3_modules(fake_api, run_module):
    serve_all(fake_api())

    result = run_module(s3_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["s3"] == {
        "settings": {"enabled": True, "base_path": "/s3", "multipart_upload_expiry_interval": "2weeks", "secure": True},
        "buckets": [
            {"name": "photos", "path": "/s3/photos", "versioning": "Enabled", "object_lock": True, "default_retention": {"value": 30, "units": "days"}},
            {"name": "scratch", "path": "/s3/scratch", "versioning": "Unversioned", "object_lock": False, "default_retention": None},
        ],
        # A bucket without a policy has none here.
        "policies": [{"bucket": "photos", "policy": POLICY}],
        "access_keys": [{"user": "local:guest", "access_key_id": "00000000000001f5a001", "creation_time": "2026-11-01T00:00:00Z"}],
    }
    assert "secret" not in str(result["s3"])


def test_one_subset_reads_only_its_routes(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v1/s3/settings", SETTINGS)

    result = run_module(s3_info, {**CONNECTION, "gather_subset": ["settings"]})

    assert not result.get("failed"), result.get("msg")
    assert list(result["s3"]) == ["settings"]
    assert [e.path for e in fake.log if e.path.startswith("/v1/s3")] == ["/v1/s3/settings"]
