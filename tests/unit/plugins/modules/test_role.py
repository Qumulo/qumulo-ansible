# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.role, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import role
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
ROLES = "/v1/auth/roles/"
ROLE = "/v1/auth/roles/Backup-Operators"
MEMBERS = "/v1/auth/roles/Backup-Operators/members?limit=1000"
PRIVILEGES = "/v1/auth/privileges/"
KNOWN = {
    "PRIVILEGE_QUOTA_READ": "View quotas",
    "PRIVILEGE_SNAPSHOT_READ": "View snapshots",
    "PRIVILEGE_SNAPSHOT_WRITE": "Create, modify, and delete snapshots",
}
BACKUP = {"description": "Backups", "privileges": ["PRIVILEGE_QUOTA_READ", "PRIVILEGE_SNAPSHOT_READ"]}
OBSERVERS = {"description": "Read-only access", "privileges": ["PRIVILEGE_QUOTA_READ"]}
BUILT_IN = {"Administrators": {"description": "All", "privileges": list(KNOWN)}, "Observers": OBSERVERS}
ARGS = {"name": "Backup-Operators", "description": "Backups", "privileges": ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_QUOTA_READ"]}


def listing(**custom):
    return {**BUILT_IN, **custom}


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


def test_absent_role_is_created(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())
    fake.expect("GET", PRIVILEGES, response=KNOWN)
    fake.expect("POST", ROLES, body={**ARGS, "privileges": sorted(ARGS["privileges"])}, response={"name": "Backup-Operators", **BACKUP}, etag='"AQAAAA"')
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))

    result = run_module(role, {**CONNECTION, **ARGS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["role"] == {"name": "Backup-Operators", **BACKUP}


def test_a_new_role_without_description_or_privileges_gets_empty_ones(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())
    fake.expect("POST", ROLES, body={"name": "Empty", "description": "", "privileges": []}, response={"name": "Empty", "description": "", "privileges": []})
    fake.expect("GET", ROLES, response=listing(Empty={"description": "", "privileges": []}))

    result = run_module(role, {**CONNECTION, "name": "Empty"})

    assert result["changed"] is True, result.get("msg")
    assert result["role"] == {"name": "Empty", "description": "", "privileges": []}


def test_matching_in_another_case_and_order_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))

    result = run_module(role, {**CONNECTION, **ARGS, "name": "backup-operators"})

    assert result["changed"] is False, result.get("msg")
    assert result["role"] == {"name": "Backup-Operators", **BACKUP}
    assert writes(fake) == []


def test_a_role_is_found_in_the_list_never_by_its_own_get(fake_api, run_module):
    """QUIRK-030: a GET of a missing role answers 404 http_not_found_error, as a missing route does; the list tells them apart."""
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())
    fake.expect("GET", PRIVILEGES, response=KNOWN)

    result = run_module(role, {**CONNECTION, **ARGS}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert [e.path for e in fake.log if not e.background] == [ROLES, PRIVILEGES]


def test_changes_are_patched_with_the_role_s_etag(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", PRIVILEGES, response=KNOWN)
    fake.expect("GET", ROLE, response=BACKUP, etag='"AQAAAA"')
    fake.expect("PATCH", ROLE, body={"privileges": ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_SNAPSHOT_WRITE"]}, if_match='"AQAAAA"', response=BACKUP)
    changed = {**BACKUP, "privileges": ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_SNAPSHOT_WRITE"]}
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": changed}))

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "privileges": ["PRIVILEGE_SNAPSHOT_WRITE", "PRIVILEGE_SNAPSHOT_READ"]})

    assert result["changed"] is True, result.get("msg")
    assert result["role"]["privileges"] == ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_SNAPSHOT_WRITE"]


def test_a_description_alone_is_patched_without_reading_privileges(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", ROLE, response=BACKUP, etag='"AQAAAA"')
    fake.expect("PATCH", ROLE, body={"description": "Nightly backups"}, if_match='"AQAAAA"', response=BACKUP)
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": {**BACKUP, "description": "Nightly backups"}}))

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "description": "Nightly backups"})

    assert result["changed"] is True, result.get("msg")


def test_unknown_privileges_fail_before_any_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())
    fake.expect("GET", PRIVILEGES, response=KNOWN)

    result = run_module(role, {**CONNECTION, **ARGS, "privileges": ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_NOPE", "PRIVILEGE_ZAP"]}, check_mode=True)

    assert result["failed"] is True
    assert "PRIVILEGE_NOPE, PRIVILEGE_ZAP" in result["msg"]
    assert writes(fake) == []


def test_check_mode_and_diff_show_the_role_and_send_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "description": "Nightly"}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"] == {"name": "Backup-Operators", **BACKUP}
    assert result["diff"]["after"] == {"name": "Backup-Operators", **BACKUP, "description": "Nightly"}
    assert writes(fake) == []


def test_a_built_in_role_that_matches_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())

    result = run_module(role, {**CONNECTION, "name": "observers", "privileges": ["PRIVILEGE_QUOTA_READ"]})

    assert result["changed"] is False, result.get("msg")
    assert result["role"] == {"name": "Observers", **OBSERVERS}


def test_a_built_in_role_is_never_changed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())

    result = run_module(role, {**CONNECTION, "name": "Observers", "description": "Mine now"}, check_mode=True)

    assert result["failed"] is True
    assert "built-in" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("name", ["Administrators", "data-administrators", "Qumulo-Support", "Observers"])
def test_a_built_in_role_is_never_deleted(fake_api, run_module, name):
    fake = fake_api()

    result = run_module(role, {**CONNECTION, "name": name, "state": "absent"})

    assert result["failed"] is True
    assert "built-in" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_absent_deletes_the_role_with_its_etag(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", MEMBERS, response={"members": {}, "paging": {"next": None}})
    fake.expect("GET", ROLE, response=BACKUP, etag='"AgAAAA"')
    fake.expect("DELETE", ROLE, if_match='"AgAAAA"')

    result = run_module(role, {**CONNECTION, "name": "backup-operators", "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["role"] == {}
    assert writes(fake) == [f"DELETE {ROLE}"]


MEMBER = {"domain": "LOCAL", "auth_id": "1001", "uid": None, "gid": None, "sid": "S-1-5-21-1-2-3-1001", "name": "svc-backup"}


def test_absent_with_members_fails_before_the_delete(fake_api, run_module):
    """The cluster answers such a DELETE with non_empty_role_error; the module refuses first, naming what removes them."""
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", MEMBERS, response={"members": {"1001": MEMBER}, "paging": {"next": None}})

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "state": "absent"})

    assert result["failed"] is True
    assert "1 member" in result["msg"] and "role_member" in result["msg"]
    assert writes(fake) == []


def test_in_check_mode_absent_with_members_is_a_delete_with_a_note(fake_api, run_module):
    """A check run cannot see an earlier role_member task remove the members, so it predicts the delete."""
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", MEMBERS, response={"members": {"1001": MEMBER}, "paging": {"next": None}})

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "state": "absent"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert any("1 member" in note and "earlier task" in note for note in result["notes"]), result.get("notes")
    assert writes(fake) == []


def test_a_patch_another_writer_made_needless_after_a_412_is_not_sent_again(fake_api, run_module):
    changed = {**BACKUP, "description": "Nightly backups"}
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", ROLE, response=BACKUP, etag='"AQAAAA"')
    fake.expect(
        "PATCH", ROLE, body={"description": "Nightly backups"}, if_match='"AQAAAA"', status=412, response={"error_class": "http_precondition_failed_error"}
    )
    fake.expect("GET", ROLE, response=changed, etag='"AgAAAA"')
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": changed}))

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "description": "Nightly backups"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == [f"PATCH {ROLE}"]


def test_every_privilege_removed_is_patched_without_reading_the_privileges(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))
    fake.expect("GET", ROLE, response=BACKUP, etag='"AQAAAA"')
    fake.expect("PATCH", ROLE, body={"privileges": []}, if_match='"AQAAAA"', response=BACKUP)
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": {**BACKUP, "privileges": []}}))

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "privileges": []})

    assert result["changed"] is True, result.get("msg")
    assert result["role"]["privileges"] == []


def test_absent_role_that_does_not_exist_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())

    result = run_module(role, {**CONNECTION, "name": "Backup-Operators", "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_a_lost_create_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", ROLES, response=listing())
    fake.expect("GET", PRIVILEGES, response=KNOWN)
    fake.expect("POST", ROLES, raises=TimeoutError("timed out"))
    fake.expect("GET", ROLES, response=listing(**{"Backup-Operators": BACKUP}))

    result = run_module(role, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]
