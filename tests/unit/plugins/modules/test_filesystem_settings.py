# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.filesystem_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import filesystem_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
PERMISSIONS = "/v1/file-system/settings/permissions"
ATIME = "/v1/file-system/settings/atime"
NOTIFY = "/v1/file-system/settings/notify"
STORED = {"permissions_mode": "CROSS_PROTOCOL", "atime_enabled": True, "atime_granularity": "DAY", "notify_recursive_mode": "ENABLED"}


def stored(fake, permissions="CROSS_PROTOCOL", atime=None, notify="ENABLED", etags=('"a1"', '"n1"')):
    """The three GETs, in the order the module reads them; the permissions document has no ETag."""
    fake.expect("GET", PERMISSIONS, response={"mode": permissions})
    fake.expect("GET", ATIME, response=atime or {"enabled": True, "granularity": "DAY"}, etag=etags[0])
    fake.expect("GET", NOTIFY, response={"recursive_mode": notify}, etag=etags[1])


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("PUT", "PATCH")]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(filesystem_settings, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    stored(fake)

    result = run(run_module, permissions_mode="CROSS_PROTOCOL", atime_enabled=True, atime_granularity="DAY", notify_recursive_mode="ENABLED")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["filesystem_settings"] == STORED
    assert writes(fake) == []


def test_unset_options_keep_the_clusters_values(fake_api, run_module):
    fake = fake_api()
    stored(fake)

    result = run(run_module)

    assert result["changed"] is False
    assert result["filesystem_settings"] == STORED


def test_atime_change_patches_only_the_changed_key_with_if_match_from_get(fake_api, run_module):
    fake = fake_api()
    stored(fake)
    fake.expect("PATCH", ATIME, body={"granularity": "HOUR"}, if_match='"a1"', response={"enabled": True, "granularity": "HOUR"})
    stored(fake, atime={"enabled": True, "granularity": "HOUR"}, etags=('"a2"', '"n1"'))

    result = run(run_module, atime_enabled=True, atime_granularity="HOUR")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["filesystem_settings"]["atime_granularity"] == "HOUR"
    assert writes(fake) == [f"PATCH {ATIME}"]


def test_notify_change_puts_the_whole_document_with_if_match_from_get(fake_api, run_module):
    fake = fake_api()
    stored(fake)
    fake.expect("PUT", NOTIFY, body={"recursive_mode": "DISABLED_ERROR"}, if_match='"n1"', response={"recursive_mode": "DISABLED_ERROR"})
    stored(fake, notify="DISABLED_ERROR")

    result = run(run_module, notify_recursive_mode="DISABLED_ERROR")

    assert result["changed"] is True
    assert writes(fake) == [f"PUT {NOTIFY}"]


def test_permissions_change_puts_the_mode_without_if_match(fake_api, run_module):
    """The permissions document has no ETag, so nothing can make its PUT conditional."""
    fake = fake_api()
    stored(fake)
    fake.expect("PUT", PERMISSIONS, body={"mode": "NATIVE"}, if_match=NO_HEADER, response={"mode": "NATIVE"})
    stored(fake, permissions="NATIVE")

    result = run(run_module, permissions_mode="NATIVE")

    assert result["changed"] is True
    assert result["filesystem_settings"]["permissions_mode"] == "NATIVE"


def test_changes_to_two_documents_write_each_once(fake_api, run_module):
    fake = fake_api()
    stored(fake)
    fake.expect("PUT", PERMISSIONS, body={"mode": "NATIVE"}, response={"mode": "NATIVE"})
    fake.expect("PATCH", ATIME, body={"enabled": False}, if_match='"a1"', response={"enabled": False, "granularity": "DAY"})
    stored(fake, permissions="NATIVE", atime={"enabled": False, "granularity": "DAY"})

    result = run(run_module, permissions_mode="NATIVE", atime_enabled=False, notify_recursive_mode="ENABLED")

    assert result["changed"] is True
    assert writes(fake) == [f"PUT {PERMISSIONS}", f"PATCH {ATIME}"]


def test_a_412_rereads_the_document_and_writes_again(fake_api, run_module):
    fake = fake_api()
    stored(fake)
    fake.expect("PATCH", ATIME, if_match='"a1"', status=412, response={"error_class": "http_precondition_failed_error", "description": "etag"})
    fake.expect("GET", ATIME, response={"enabled": True, "granularity": "WEEK"}, etag='"a2"')
    fake.expect("PATCH", ATIME, body={"granularity": "HOUR"}, if_match='"a2"', response={"enabled": True, "granularity": "HOUR"})
    stored(fake, atime={"enabled": True, "granularity": "HOUR"})

    result = run(run_module, atime_granularity="HOUR")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_412_whose_reread_already_holds_the_change_writes_nothing_more(fake_api, run_module):
    """Another writer set the granularity between the read and the write, so the module changed nothing."""
    fake = fake_api()
    stored(fake)
    fake.expect("PATCH", ATIME, if_match='"a1"', status=412, response={"error_class": "http_precondition_failed_error", "description": "etag"})
    fake.expect("GET", ATIME, response={"enabled": True, "granularity": "HOUR"}, etag='"a2"')
    stored(fake, atime={"enabled": True, "granularity": "HOUR"}, etags=('"a2"', '"n1"'))
    stored(fake, atime={"enabled": True, "granularity": "HOUR"}, etags=('"a2"', '"n1"'))

    result = run(run_module, atime_granularity="HOUR")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == [f"PATCH {ATIME}"]


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    stored(fake)

    result = run(run_module, check_mode=True, diff=True, notify_recursive_mode="DISABLED_IGNORE")

    assert result["changed"] is True
    assert result["diff"] == {"before": STORED, "after": {**STORED, "notify_recursive_mode": "DISABLED_IGNORE"}}
    assert writes(fake) == []


def test_a_write_that_does_not_read_back_fails_and_reports_the_change(fake_api, run_module):
    fake = fake_api()
    stored(fake)
    fake.expect("PUT", PERMISSIONS, body={"mode": "NATIVE"}, response={"mode": "NATIVE"})
    stored(fake)

    result = run(run_module, permissions_mode="NATIVE")

    assert result["failed"] is True
    assert result["changed"] is True
    assert "did not converge on permissions_mode" in result["msg"]
