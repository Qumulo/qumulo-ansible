# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.smb_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import smb_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/smb/settings"
ALL_DIALECTS = ["SMB2_DIALECT_2_002", "SMB2_DIALECT_2_1", "SMB2_DIALECT_3_0", "SMB2_DIALECT_3_11"]
STORED = {
    "session_encryption": "NONE",
    "supported_dialects": ALL_DIALECTS,
    "hide_shares_from_unauthorized_users": False,
    "hide_shares_from_unauthorized_hosts": False,
    "snapshot_directory_mode": "VISIBLE",
    "bypass_traverse_checking": False,
    "signing_required": False,
}


def writes(fake):
    return [e for e in fake.log if e.method == "PATCH"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(smb_settings, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"e1"')

    result = run(run_module, signing_required=False, supported_dialects=list(reversed(ALL_DIALECTS)))

    assert result["changed"] is False
    assert result["smb_settings"] == STORED
    assert writes(fake) == []


def test_dialects_the_server_lists_in_another_order_are_the_same_set(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response={**STORED, "supported_dialects": list(reversed(ALL_DIALECTS))}, etag='"e1"')

    result = run(run_module, supported_dialects=ALL_DIALECTS)

    assert result["changed"] is False
    assert writes(fake) == []


def test_change_patches_only_the_changed_keys(fake_api, run_module):
    after = {**STORED, "signing_required": True, "supported_dialects": ALL_DIALECTS[2:]}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"e1"')
    fake.expect("PATCH", SETTINGS, body={"signing_required": True, "supported_dialects": ALL_DIALECTS[2:]}, response=after)
    fake.expect("GET", SETTINGS, response=after, etag='"e2"')

    result = run(run_module, signing_required=True, supported_dialects=["SMB2_DIALECT_3_11", "SMB2_DIALECT_3_0"], session_encryption="NONE")

    assert result["changed"] is True
    assert result["smb_settings"] == after


# QUIRK-001: with the two hide_shares flags different, the GET ETag never matches what PATCH checks.
def test_smb_settings_never_sends_if_match(fake_api, run_module):
    stored = {**STORED, "hide_shares_from_unauthorized_users": True}
    after = {**stored, "signing_required": True}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=stored, etag='"I2BDRw"')
    fake.expect("PATCH", SETTINGS, body={"signing_required": True}, if_match=NO_HEADER, response=after)
    fake.expect("GET", SETTINGS, response=after, etag='"other"')

    result = run(run_module, signing_required=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"e1"')

    result = run(run_module, check_mode=True, snapshot_directory_mode="HIDDEN")

    assert result["changed"] is True
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"e1"')

    result = run(run_module, check_mode=True, diff=True, session_encryption="REQUIRED")

    assert result["diff"] == {"before": STORED, "after": {**STORED, "session_encryption": "REQUIRED"}}


def test_empty_dialect_list_is_sent_as_given(fake_api, run_module):
    """The cluster takes no dialects to mean SMB off; the module does not second-guess it."""
    after = {**STORED, "supported_dialects": []}
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=STORED, etag='"e1"')
    fake.expect("PATCH", SETTINGS, body={"supported_dialects": []}, response=after)
    fake.expect("GET", SETTINGS, response=after)

    result = run(run_module, supported_dialects=[])

    assert result["changed"] is True
    assert result["smb_settings"]["supported_dialects"] == []


# A tenant's override (spec 014, FR-015, decision D5; research R13, P3 and S2)

TENANTS = "/v1/multitenancy/tenants/"
TENANT_LIST = {
    "entries": [{"id": 1, "name": "Default", "networks": [], "identity_config_id": 1}, {"id": 2, "name": "eng", "networks": [], "identity_config_id": 1}]
}
OVERRIDE = "/v1/multitenancy/smb/settings/2"
MISSING = {"error_class": "api_smb_tenant_settings_do_not_exist_error", "description": "no settings for tenant 2"}


def override_writes(fake):
    return [e for e in fake.log if e.method in ("PUT", "PATCH", "DELETE")]


def tenant_cluster(fake_api):
    fake = fake_api()
    fake.serve(TENANTS, TENANT_LIST)
    return fake


# QUIRK-113: a creating PUT with If-Match is refused as if the override did not exist.
def test_present_creates_the_override_by_put_without_if_match_from_the_global_settings(fake_api, run_module):
    fake = tenant_cluster(fake_api)
    fake.expect("GET", OVERRIDE, status=404, response=MISSING)
    fake.expect("GET", SETTINGS, response={**STORED, "signing_required": True}, etag='"g"')
    want = {**STORED, "signing_required": True, "session_encryption": "REQUIRED"}
    fake.expect("PUT", OVERRIDE, body=want, if_match=NO_HEADER, response=want, etag='"o1"')
    fake.expect("GET", OVERRIDE, response=want, etag='"o1"')

    result = run(run_module, diff=True, tenant="eng", session_encryption="REQUIRED")

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"] == {}
    assert result["diff"]["after"] == want
    assert result["diff"]["before_header"] == result["diff"]["after_header"] == "tenant eng"


def test_a_new_override_keeps_a_global_field_the_module_does_not_model(fake_api, run_module):
    """QUIRK-113 and QUIRK-115: the creating PUT starts from the server's global document, so a field a later release adds keeps its global value."""
    fake = tenant_cluster(fake_api)
    fake.expect("GET", OVERRIDE, status=404, response=MISSING)
    fake.expect("GET", SETTINGS, response={**STORED, "future_setting": "GLOBAL"}, etag='"g"')
    want = {**STORED, "future_setting": "GLOBAL", "signing_required": True}
    fake.expect("PUT", OVERRIDE, body=want, if_match=NO_HEADER, response=want, etag='"o1"')
    fake.expect("GET", OVERRIDE, response=want, etag='"o1"')

    result = run(run_module, tenant="eng", signing_required=True)

    assert result["changed"] is True, result.get("msg")
    assert "future_setting" not in result["smb_settings"]


def test_present_changes_the_override_by_one_read_and_a_patch_with_if_match_of_the_differing_keys(fake_api, run_module):
    fake = tenant_cluster(fake_api)
    fake.expect("GET", OVERRIDE, response=STORED, etag='"o1"')
    fake.expect("PATCH", OVERRIDE, body={"signing_required": True}, if_match='"o1"', response={**STORED, "signing_required": True})
    fake.expect("GET", OVERRIDE, response={**STORED, "signing_required": True}, etag='"o2"')

    result = run(run_module, tenant="eng", signing_required=True)

    assert result["changed"] is True, result.get("msg")


def test_inherit_deletes_the_override_with_if_match(fake_api, run_module):
    fake = tenant_cluster(fake_api)
    fake.expect("GET", OVERRIDE, response=STORED, etag='"o1"')
    fake.expect("DELETE", OVERRIDE, if_match='"o1"', status=200)

    result = run(run_module, diff=True, tenant="eng", state="inherit")

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["after"] == {}
    assert result["smb_settings"] == {}


def test_inherit_without_an_override_or_without_the_tenant_is_satisfied(fake_api, run_module):
    fake = tenant_cluster(fake_api)
    fake.expect("GET", OVERRIDE, status=404, response=MISSING)

    result = run(run_module, tenant="eng", state="inherit")
    gone = run(run_module, tenant="finance", state="inherit")

    assert result["changed"] is False and gone["changed"] is False
    assert override_writes(fake) == []


def test_only_the_missing_override_class_reads_as_no_override(fake_api, run_module):
    """QUIRK-112: a missing tenant answers the same 404 with another class, which must not read as inherit."""
    fake = tenant_cluster(fake_api)
    fake.expect("GET", OVERRIDE, status=404, response={"error_class": "tenant_does_not_exist_error", "description": "no tenant 2"})

    result = run(run_module, tenant="eng", state="inherit")

    assert result["failed"] is True
    assert "tenant_does_not_exist_error" in result["msg"]


def test_a_tenant_an_earlier_task_creates_is_pending_in_check_mode_seeded_from_the_global_settings(fake_api, run_module):
    fake = tenant_cluster(fake_api)
    fake.expect("GET", SETTINGS, response=STORED, etag='"g"')

    result = run(run_module, check_mode=True, diff=True, tenant="finance", signing_required=True)
    refused = run(run_module, tenant="finance", signing_required=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["after"] == {**STORED, "signing_required": True}
    assert result["notes"] == ["tenant 'finance' does not exist yet; an earlier task is expected to create it"]
    assert refused["msg"] == "no tenant on this cluster is named 'finance'; its tenants are 'Default' and 'eng'"


def test_inherit_needs_a_tenant(fake_api, run_module):
    result = run(run_module, state="inherit")

    assert result["failed"] is True
    assert "state is inherit but all of the following are missing: tenant" in result["msg"]
