# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.s3_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import s3_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/s3/settings"
STORED = {"enabled": False, "base_path": "/", "multipart_upload_expiry_interval": "1days", "secure": True}


def writes(fake):
    return [e for e in fake.log if e.method == "PATCH"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(s3_settings, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_matching_settings_are_unchanged_and_send_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)

    result = run(run_module, enabled=False, secure=True, base_path="/", multipart_upload_expiry_interval="1days")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["s3_settings"] == STORED
    assert writes(fake) == []


def test_change_patches_only_the_changed_keys_without_if_match(fake_api, run_module):
    after = {**STORED, "enabled": True, "multipart_upload_expiry_interval": "1weeks"}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("PATCH", SETTINGS, body={"enabled": True, "multipart_upload_expiry_interval": "1weeks"}, if_match=NO_HEADER, response=after)
    fake.expect("GET", SETTINGS, response=after)

    result = run(run_module, enabled=True, secure=True, multipart_upload_expiry_interval="7days")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_settings"] == after


# QUIRK-050: the cluster reads the interval back in the largest unit it fits, so 14days is 2weeks.
@pytest.mark.parametrize(("given", "stored"), [("14days", "2weeks"), ("28days", "1months"), ("48hours", "2days"), ("4weeks", "1months"), ("", "never")])
def test_interval_compares_in_the_form_the_cluster_reads_back(fake_api, run_module, given, stored):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "multipart_upload_expiry_interval": stored})

    result = run(run_module, multipart_upload_expiry_interval=given)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["s3_settings"]["multipart_upload_expiry_interval"] == stored
    assert writes(fake) == []


@pytest.mark.parametrize(("given", "shown"), [("36hours", "1days"), ("30days", "1months"), ("10days", "1weeks"), ("99hours", "4days")])
def test_interval_that_reads_back_shorter_fails_before_any_request(fake_api, run_module, given, shown):
    fake = fake_api()

    result = run(run_module, multipart_upload_expiry_interval=given)

    assert result["failed"] is True
    assert f"reads back as {shown}" in result["msg"]
    assert "QUIRK-050" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


@pytest.mark.parametrize("given", ["0days", "100hours", "1day", "2 weeks", "1minutes", "soon"])
def test_interval_outside_the_cluster_s_grammar_fails_before_any_request(fake_api, run_module, given):
    fake = fake_api()

    result = run(run_module, multipart_upload_expiry_interval=given)

    assert result["failed"] is True
    assert "multipart_upload_expiry_interval" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_base_path_compares_without_its_trailing_slash(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "base_path": "/s3/"})

    result = run(run_module, base_path="/s3")

    assert result["changed"] is False
    assert result["s3_settings"]["base_path"] == "/s3"
    assert writes(fake) == []


def test_new_base_path_is_checked_then_patched(fake_api, run_module):
    after = {**STORED, "base_path": "/s3"}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", attributes_path("/s3"), response=attributes("/s3"))
    fake.expect("PATCH", SETTINGS, body={"base_path": "/s3"}, response=after)
    fake.expect("GET", SETTINGS, response=after)

    result = run(run_module, base_path="/s3/")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


# QUIRK-051: the cluster answers a missing base_path with the error class of a missing route.
def test_missing_base_path_fails_before_the_patch(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", attributes_path("/s3"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, base_path="/s3")

    assert result["failed"] is True
    assert "/s3 does not exist" in result["msg"]
    assert writes(fake) == []


def test_base_path_that_is_a_file_fails_before_the_patch(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", attributes_path("/s3"), response=attributes("/s3", kind="FS_FILE_TYPE_FILE"))

    result = run(run_module, base_path="/s3")

    assert result["failed"] is True
    assert "not a directory" in result["msg"]
    assert writes(fake) == []


def test_missing_base_path_in_check_mode_is_a_change_with_a_note(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)
    fake.expect("GET", attributes_path("/s3"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, check_mode=True, base_path="/s3")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert any("/s3 does not exist yet" in note for note in result["notes"])
    assert writes(fake) == []


def test_check_mode_with_diff_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED)

    result = run(run_module, check_mode=True, diff=True, enabled=True, secure=False)

    assert result["changed"] is True
    assert result["diff"] == {"before": STORED, "after": {**STORED, "enabled": True, "secure": False}}
    assert writes(fake) == []
