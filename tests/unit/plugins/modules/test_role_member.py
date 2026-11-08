# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.role_member, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import role_member
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
MEMBERS = "/v1/auth/roles/Observers/members?limit=1000"
ADMIN_MEMBERS = "/v1/auth/roles/Administrators/members?limit=1000"
SID = "S-1-5-21-1534280521-3995163514-3722741250-"
NO_SUCH_ROUTE = {"error_class": "http_not_found_error", "description": "it-missing"}


def local(name, auth_id):
    return {"domain": "LOCAL", "auth_id": auth_id, "uid": None, "gid": None, "sid": SID + auth_id, "name": name}


ADMIN = local("admin", "500")
BACKUP = local("svc-backup", "1001")
MONITOR = local("svc-monitor", "1002")
AD_ADMINS = {"domain": "ACTIVE_DIRECTORY", "auth_id": "12884902889", "uid": None, "gid": None, "sid": "S-1-5-21-9-9-9-1110", "name": "CORP\\Qumulo-Admins"}


def page(*identities, next_page=None):
    return {"members": {i["auth_id"]: i for i in identities}, "paging": {"next": next_page}}


def setup(fake_api, *known):
    fake = fake_api()
    fake.identities = list(known)
    return fake


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("POST", "DELETE") and not e.background]


def test_absent_member_is_assigned_by_auth_id(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page())
    fake.expect("POST", "/v1/auth/roles/Observers/members", body={"auth_id": "1001"}, response=BACKUP)
    fake.expect("GET", MEMBERS, response=page(BACKUP))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["role_member"] == {"role": "Observers", "members": ["local:svc-backup"]}


def test_assigned_member_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page(BACKUP, MONITOR))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup"]})

    assert result["changed"] is False, result.get("msg")
    assert result["role_member"]["members"] == ["local:svc-backup", "local:svc-monitor"]
    assert writes(fake) == []


def test_same_ad_trustee_written_another_way_is_the_same_member(fake_api, run_module):
    fake = setup(fake_api)
    fake.identities = [{**AD_ADMINS, "name": "Qumulo-Admins@corp.example.com"}]
    fake.expect("GET", ADMIN_MEMBERS, response=page(ADMIN, AD_ADMINS))

    result = run_module(role_member, {**CONNECTION, "role": "Administrators", "members": ["ad:Qumulo-Admins@corp.example.com"]})

    assert result["changed"] is False, result.get("msg")


def test_members_are_followed_across_pages(fake_api, run_module):
    fake = setup(fake_api, MONITOR)
    fake.expect("GET", MEMBERS, response=page(BACKUP, next_page="/v1/auth/roles/Observers/members?after=1001&limit=1000"))
    fake.expect("GET", "/v1/auth/roles/Observers/members?after=1001&limit=1000", response=page(MONITOR))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-monitor"]})

    assert result["changed"] is False, result.get("msg")
    assert result["role_member"]["members"] == ["local:svc-backup", "local:svc-monitor"]


def test_purge_removes_unlisted_members(fake_api, run_module):
    fake = setup(fake_api, MONITOR)
    fake.expect("GET", MEMBERS, response=page(BACKUP, MONITOR))
    fake.expect("DELETE", "/v1/auth/roles/Observers/members/1001")
    fake.expect("GET", MEMBERS, response=page(MONITOR))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-monitor"], "purge": True})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == ["DELETE /v1/auth/roles/Observers/members/1001"]


def test_purge_keeps_admin_in_administrators(fake_api, run_module):
    fake = setup(fake_api, AD_ADMINS)
    fake.expect("GET", ADMIN_MEMBERS, response=page(ADMIN, AD_ADMINS))

    result = run_module(
        role_member, {**CONNECTION, "api_username": "svc-ansible", "role": "Administrators", "members": ["ad:CORP\\Qumulo-Admins"], "purge": True}
    )

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_purge_keeps_the_system_user_in_administrators(fake_api, run_module):
    fake = setup(fake_api, AD_ADMINS)
    fake.expect("GET", ADMIN_MEMBERS, response=page(ADMIN, local("system", "501"), AD_ADMINS))

    result = run_module(
        role_member, {**CONNECTION, "api_username": "svc-ansible", "role": "Administrators", "members": ["ad:CORP\\Qumulo-Admins"], "purge": True}
    )

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_purge_removes_an_ad_user_named_admin(fake_api, run_module):
    ad_admin = {**AD_ADMINS, "auth_id": "12884902890", "sid": "S-1-5-21-9-9-9-500", "name": "admin"}
    fake = setup(fake_api, AD_ADMINS)
    fake.expect("GET", ADMIN_MEMBERS, response=page(ADMIN, ad_admin, AD_ADMINS))
    fake.expect("DELETE", "/v1/auth/roles/Administrators/members/12884902890")
    fake.expect("GET", ADMIN_MEMBERS, response=page(ADMIN, AD_ADMINS))

    result = run_module(
        role_member, {**CONNECTION, "api_username": "svc-ansible", "role": "Administrators", "members": ["ad:CORP\\Qumulo-Admins"], "purge": True}
    )

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == ["DELETE /v1/auth/roles/Administrators/members/12884902890"]


def test_member_still_missing_after_the_post_fails_reporting_the_change(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page())
    fake.expect("POST", "/v1/auth/roles/Observers/members", body={"auth_id": "1001"}, response=BACKUP)
    fake.expect("GET", MEMBERS, response=page())

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup"]})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "role 'Observers' did not converge: 1 not assigned, 0 not removed" in result["msg"]


def test_removing_an_ad_member_named_like_the_connecting_user_needs_no_disruption(fake_api, run_module):
    ad_backup = {**AD_ADMINS, "auth_id": "12884902891", "sid": "S-1-5-21-9-9-9-1001", "name": "svc-backup"}
    fake = setup(fake_api, MONITOR)
    fake.expect("GET", MEMBERS, response=page(ad_backup, MONITOR))
    fake.expect("DELETE", "/v1/auth/roles/Observers/members/12884902891")
    fake.expect("GET", MEMBERS, response=page(MONITOR))

    result = run_module(role_member, {**CONNECTION, "api_username": "svc-backup", "role": "Observers", "members": ["local:svc-monitor"], "purge": True})

    assert result["changed"] is True, result.get("msg")


def test_purge_of_qumulo_support_is_refused_before_any_request(fake_api, run_module):
    fake = setup(fake_api)

    result = run_module(role_member, {**CONNECTION, "role": "Qumulo-Support", "members": [], "purge": True})

    assert result["failed"] is True
    assert "purge is refused for Qumulo-Support" in result["msg"]
    assert fake.log == []


def test_removing_the_connecting_user_needs_allow_management_disruption(fake_api, run_module):
    fake = setup(fake_api, MONITOR)
    fake.expect("GET", MEMBERS, response=page(BACKUP, MONITOR))

    result = run_module(role_member, {**CONNECTION, "api_username": "svc-backup", "role": "Observers", "members": ["local:svc-monitor"], "purge": True})

    assert result["failed"] is True
    assert "would remove svc-backup, the user this task connects as, from Observers" in result["msg"]
    assert "allow_management_disruption" in result["msg"]
    assert writes(fake) == []


def test_state_absent_removes_only_listed_members(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page(BACKUP, MONITOR))
    fake.expect("DELETE", "/v1/auth/roles/Observers/members/1001")
    fake.expect("GET", MEMBERS, response=page(MONITOR))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup"], "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["role_member"]["members"] == ["local:svc-monitor"]


def test_state_absent_on_a_non_member_is_unchanged(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page(MONITOR))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup"], "state": "absent"})

    assert result["changed"] is False, result.get("msg")


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page())

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup"]}, check_mode=True)

    assert result["changed"] is True
    assert writes(fake) == []


def test_check_mode_reports_an_unresolved_trustee_as_a_change_with_a_note(fake_api, run_module):
    fake = setup(fake_api)
    fake.expect("GET", MEMBERS, response=page())

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["ad:CORP\\Qumulo-Admins"]}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert "ad:CORP\\Qumulo-Admins (resolves after an earlier task creates or joins it)" in result["role_member"]["members"]


def test_unresolved_trustee_fails_outside_check_mode(fake_api, run_module):
    fake = setup(fake_api)
    fake.expect("GET", MEMBERS, response=page())

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:it-missing"]})

    assert result["failed"] is True
    assert "trustee 'local:it-missing'" in result["msg"]


def test_bare_name_fails_before_any_request(fake_api, run_module):
    fake = setup(fake_api)

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["svc-backup"]})

    assert result["failed"] is True
    assert "a bare name is ambiguous" in result["msg"]
    assert fake.log == []


# The cluster answers the members of a missing role with the error class of a missing route (QUIRK-030), so the module
# reads the list of roles to tell the two apart.
def test_missing_role_fails_naming_it_and_the_module_that_creates_it(fake_api, run_module):
    fake = setup(fake_api)
    fake.expect("GET", "/v1/auth/roles/it-missing/members?limit=1000", status=404, response=NO_SUCH_ROUTE)
    fake.expect("GET", "/v1/auth/roles/", response={"Observers": {}, "Administrators": {}})

    result = run_module(role_member, {**CONNECTION, "role": "it-missing", "members": []})

    assert result["failed"] is True
    assert "role 'it-missing' does not exist" in result["msg"] and "qumulo.cluster.role" in result["msg"]


def test_in_check_mode_a_role_that_does_not_exist_yet_has_no_members_and_a_note(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", "/v1/auth/roles/it-missing/members?limit=1000", status=404, response=NO_SUCH_ROUTE)
    fake.expect("GET", "/v1/auth/roles/", response={"Observers": {}})

    result = run_module(role_member, {**CONNECTION, "role": "it-missing", "members": ["local:svc-backup"]}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["role_member"] == {"role": "it-missing", "members": ["local:svc-backup"]}
    assert any("earlier task" in note for note in result["notes"]), result.get("notes")


@pytest.mark.parametrize(
    "args",
    [{"members": ["local:svc-backup"], "state": "absent"}, {"members": [], "purge": True}],
    ids=["absent", "purged to none"],
)
def test_a_role_that_does_not_exist_already_lacks_the_members_a_removal_asks_away(fake_api, run_module, args):
    """An inventory that empties a role and then deletes it runs both again: the second time the role is gone."""
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", "/v1/auth/roles/it-missing/members?limit=1000", status=404, response=NO_SUCH_ROUTE)
    fake.expect("GET", "/v1/auth/roles/", response={"Observers": {}})

    result = run_module(role_member, {**CONNECTION, "role": "it-missing", **args})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["role_member"] == {"role": "it-missing", "members": []}
    assert any("does not exist, so it holds none" in note for note in result["notes"]), result.get("notes")
    # The members need not exist either, so none is looked up.
    assert [e for e in fake.log if e.path == "/v1/identity/find"] == []


RETIREE = "local:it-retiree"


@pytest.mark.parametrize("check_mode", [False, True], ids=["real run", "check mode"])
def test_retiring_a_user_and_its_role_first_removes_a_member_that_is_gone(fake_api, run_module, check_mode):
    """The first run deletes the user, which drops its assignment, then removes it from the role: nothing is left to do."""
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page(BACKUP))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": [RETIREE], "state": "absent"}, check_mode=check_mode)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["role_member"]["members"] == ["local:svc-backup"]
    assert any(f"trustee '{RETIREE}' does not exist" in note for note in result["notes"]), result.get("notes")
    assert writes(fake) == []


def test_retiring_a_user_and_its_role_later_finds_the_role_gone(fake_api, run_module):
    fake = setup(fake_api)
    fake.expect("GET", "/v1/auth/roles/it-old/members?limit=1000", status=404, response=NO_SUCH_ROUTE)
    fake.expect("GET", "/v1/auth/roles/", response={"Observers": {}})

    result = run_module(role_member, {**CONNECTION, "role": "it-old", "members": [RETIREE], "state": "absent"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_absent_removes_the_members_that_exist_and_notes_the_one_that_does_not(fake_api, run_module):
    fake = setup(fake_api, BACKUP)
    fake.expect("GET", MEMBERS, response=page(BACKUP, MONITOR))
    fake.expect("DELETE", "/v1/auth/roles/Observers/members/1001")
    fake.expect("GET", MEMBERS, response=page(MONITOR))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-backup", RETIREE], "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == ["DELETE /v1/auth/roles/Observers/members/1001"]
    assert any(RETIREE in note for note in result["notes"]), result.get("notes")


def test_a_404_for_a_role_that_exists_is_reported_as_the_cluster_gave_it(fake_api, run_module):
    fake = setup(fake_api)
    fake.expect("GET", MEMBERS, status=404, response=NO_SUCH_ROUTE)
    fake.expect("GET", "/v1/auth/roles/", response={"Observers": {}})

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": []})

    assert result["failed"] is True
    assert "http_not_found_error" in result["msg"]


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = setup(fake_api, MONITOR)
    fake.expect("GET", MEMBERS, response=page(BACKUP))

    result = run_module(role_member, {**CONNECTION, "role": "Observers", "members": ["local:svc-monitor"], "purge": True}, check_mode=True, diff=True)

    assert result["diff"] == {
        "before": {"role": "Observers", "members": ["local:svc-backup"]},
        "after": {"role": "Observers", "members": ["local:svc-monitor"]},
    }
