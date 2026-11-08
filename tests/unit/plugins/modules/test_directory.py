# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.directory, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import directory
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import (
    ADMIN,
    AUTHENTICATED_USERS,
    FOREIGN_SID,
    GUEST,
    UID_1000,
    UNNAMEABLE,
    UNSEEN_DOMAINS,
    UNSEEN_SID_DOMAIN,
    attributes,
    attributes_path,
    entries_path,
    error,
    fake_with_identities,
)

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
NOT_FOUND = error("fs_no_such_entry_error")


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def identity_lookups(fake):
    return [e.body for e in fake.log if e.path == "/v1/identity/find"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(directory, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_absent_directory_is_created_with_its_missing_parents(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data/projects"), status=404, response=NOT_FOUND)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)
    fake.expect("POST", entries_path("/"), body={"name": "data", "action": "CREATE_DIRECTORY"}, response=attributes("/data"))
    fake.expect("POST", entries_path("/data"), body={"name": "projects", "action": "CREATE_DIRECTORY"}, response=attributes("/data/projects"))
    fake.expect("GET", attributes_path("/data/projects"), response=attributes("/data/projects"), etag='"1"')

    result = run(run_module, path="/data/projects")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["directory"] == {"path": "/data/projects", "mode": "0755", "owner": "local:admin", "group": "local:Users"}


def test_matching_directory_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", mode="0775"), etag='"1"')

    result = run(run_module, path="/data/", mode="775", owner="local:admin", group="local:Users")

    assert result["changed"] is False
    assert writes(fake) == []


def test_owner_is_compared_by_the_identity_it_resolves_to(fake_api, run_module):
    """sid:<admin's SID> names the same identity as the local:admin the server reports."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')

    result = run(run_module, path="/data", owner=f"sid:{ADMIN['sid']}")

    assert result["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize("spelling", ["ad:NT AUTHORITY\\Authenticated Users", "ad:nt authority\\authenticated users", "sid:S-1-5-11"])
def test_a_group_of_authenticated_users_is_unchanged_on_a_node_that_refuses_its_sid(fake_api, run_module, spelling):
    """The cluster gives the group's details as its SID; the module names it by its NT AUTHORITY name."""
    stored = {**attributes("/data"), "group": AUTHENTICATED_USERS["auth_id"], "group_details": {"id_type": "SMB_SID", "id_value": "S-1-5-11"}}
    fake = fake_with_identities(fake_api)
    fake.find_errors.append(UNSEEN_SID_DOMAIN)
    fake.expect("GET", attributes_path("/data"), response=stored, etag='"1"')

    result = run(run_module, path="/data", group=spelling)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert {"sid": "S-1-5-11"} not in identity_lookups(fake)


def test_a_group_whose_sid_the_find_refuses_can_be_replaced(fake_api, run_module):
    """The cluster gives the group's details as its SID, which the find refuses; the comparison uses the group's auth_id."""
    stored = {**attributes("/data"), "group": FOREIGN_SID["auth_id"], "group_details": {"id_type": "SMB_SID", "id_value": FOREIGN_SID["sid"]}}
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    fake.expect("GET", attributes_path("/data"), response=stored, etag='"7"')
    fake.expect("PATCH", attributes_path("/data"), body={"group": "513"}, if_match='"7"', response=attributes("/data"))
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"8"')

    result = run(run_module, path="/data", group="local:Users")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert {"sid": FOREIGN_SID["sid"]} not in identity_lookups(fake)


def test_an_owner_the_grammar_cannot_name_is_reported_by_its_name(fake_api, run_module):
    stored = {**attributes("/data"), "owner": UNNAMEABLE["auth_id"], "owner_details": {"id_type": "INTERNAL", "id_value": UNNAMEABLE["auth_id"]}}
    fake = fake_with_identities(fake_api)
    fake.identities.append(UNNAMEABLE)
    fake.expect("GET", attributes_path("/data"), response=stored, etag='"1"')

    result = run(run_module, check_mode=True, diff=True, path="/data", owner="local:admin")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["diff"]["before"]["owner"] == "Nobody"


def test_differing_mode_patches_only_the_mode_with_if_match_from_get(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", mode="0755"), etag='"7"')
    fake.expect("PATCH", attributes_path("/data"), body={"mode": "0700"}, if_match='"7"', response=attributes("/data", mode="0700"))
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", mode="0700"), etag='"8"')

    result = run(run_module, path="/data", mode="0700", owner="local:admin")

    assert result["changed"] is True
    assert result["directory"]["mode"] == "0700"


def test_owner_change_sends_the_resolved_identity(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"7"')
    fake.expect("PATCH", attributes_path("/data"), body={"owner": GUEST["auth_id"]}, response=attributes("/data", owner=GUEST))
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", owner=GUEST), etag='"8"')

    result = run(run_module, path="/data", owner="local:guest")

    assert result["changed"] is True
    assert result["directory"]["owner"] == "local:guest"


def test_uid_owner_reads_back_as_the_same_uid(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", owner=UID_1000), etag='"1"')

    result = run(run_module, path="/data", owner="uid:1000")

    assert result["changed"] is False
    assert result["directory"]["owner"] == "uid:1000"
    assert identity_lookups(fake) == []


def test_mode_only_task_looks_up_no_owner_or_group(fake_api, run_module):
    """The owner and group the task leaves unset are not managed, so nothing resolves them."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", mode="0775"), etag='"1"')

    result = run(run_module, path="/data", mode="0775")

    assert result["changed"] is False
    assert identity_lookups(fake) == []


@pytest.mark.parametrize(("unquoted", "meant"), [(420, "0644"), (504, "0770"), (457, "0711"), (365, "0555")])
def test_unquoted_octal_mode_fails_before_any_request(fake_api, run_module, unquoted, meant):
    """YAML reads an unquoted mode such as 0644 as the decimal 420, whose digits are another valid mode."""
    fake = fake_with_identities(fake_api)

    result = run(run_module, path="/data", mode=unquoted)

    assert result["failed"] is True
    assert int(meant, 8) == unquoted
    assert result["msg"] == f"mode {unquoted} is a number; quote the mode, such as '0755', because YAML reads an unquoted 0755 as the decimal 493"
    assert fake.log == []


def test_new_directory_gets_the_requested_mode_and_owner(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)
    fake.expect("POST", entries_path("/"), body={"name": "data", "action": "CREATE_DIRECTORY"}, response=attributes("/data"))
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"2"')
    fake.expect("PATCH", attributes_path("/data"), body={"mode": "0700", "owner": GUEST["auth_id"]}, if_match='"2"', response={})
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", mode="0700", owner=GUEST), etag='"3"')

    result = run(run_module, path="/data", mode="0700", owner="local:guest")

    assert result["changed"] is True
    assert result["directory"] == {"path": "/data", "mode": "0700", "owner": "local:guest", "group": "local:Users"}


def test_check_mode_reports_changed_and_sends_no_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)

    result = run(run_module, check_mode=True, path="/data", mode="0700")

    assert result["changed"] is True
    assert writes(fake) == []


def test_check_mode_owner_that_does_not_exist_yet_is_a_change(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')

    result = run(run_module, check_mode=True, path="/data", owner="local:svc_backup")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert writes(fake) == []


def test_owner_that_does_not_exist_fails_outside_check_mode(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')

    result = run(run_module, path="/data", owner="local:svc_backup")

    assert result["failed"] is True
    assert result["msg"].startswith("trustee 'local:svc_backup': POST /v1/identity/find failed: HTTP 404")
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')

    result = run(run_module, check_mode=True, diff=True, path="/data", mode="0700")

    assert result["diff"] == {
        "before": {"path": "/data", "mode": "0755", "owner": "local:admin", "group": "local:Users"},
        "after": {"path": "/data", "mode": "0700", "owner": "local:admin", "group": "local:Users"},
    }


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"path": "data/projects"}, "path 'data/projects' must be an absolute path"),
        ({"path": "/data//projects"}, "path '/data//projects' has an empty, '.' or '..' component"),
        ({"path": "/data/../etc"}, "path '/data/../etc' has an empty, '.' or '..' component"),
        ({"path": "/data", "mode": "0999"}, "mode '0999' is not an octal mode such as '0755'"),
        ({"path": "/data", "mode": "rwxr-xr-x"}, "mode 'rwxr-xr-x' is not an octal mode such as '0755'"),
        ({"path": "/data", "mode": "17777"}, "mode '17777' is not an octal mode such as '0755'"),
        ({"path": "/data", "owner": "alice"}, "trustee 'alice' needs a prefix"),
        ({"path": "/data", "group": "Users"}, "trustee 'Users' needs a prefix"),
        ({"path": "/", "state": "absent"}, "the root directory cannot be removed"),
    ],
    ids=["relative", "empty_component", "dot_dot", "not_octal", "symbolic", "too_long", "bare_owner", "bare_group", "root_absent"],
)
def test_bad_options_fail_before_any_request(fake_api, run_module, args, message):
    fake = fake_with_identities(fake_api)

    result = run(run_module, **args)

    assert result["failed"] is True
    assert result["msg"].startswith(message), result["msg"]
    assert fake.log == []


def test_file_at_the_path_fails(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", kind="FS_FILE_TYPE_FILE"), etag='"1"')

    result = run(run_module, path="/data")

    assert result["failed"] is True
    assert result["msg"] == "/data exists and is not a directory (FS_FILE_TYPE_FILE)"


def test_file_in_place_of_a_parent_fails_before_any_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data/projects"), status=404, response=error("fs_not_a_directory_error"))

    result = run(run_module, path="/data/projects")

    assert result["failed"] is True
    assert "HTTP 404 fs_not_a_directory_error" in result["msg"]
    assert writes(fake) == []


def test_parent_that_is_a_file_fails_before_any_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data/projects"), status=404, response=NOT_FOUND)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", kind="FS_FILE_TYPE_FILE"))

    result = run(run_module, path="/data/projects")

    assert result["failed"] is True
    assert result["msg"] == "/data exists and is not a directory (FS_FILE_TYPE_FILE)"
    assert writes(fake) == []


def test_directory_another_client_creates_first_is_accepted(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)
    fake.expect("POST", entries_path("/"), status=409, response=error("fs_entry_exists_error", "data"))
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')

    result = run(run_module, path="/data")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_file_another_client_creates_first_fails(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)
    fake.expect("POST", entries_path("/"), status=409, response=error("fs_entry_exists_error", "data"))
    fake.expect("GET", attributes_path("/data"), response=attributes("/data", kind="FS_FILE_TYPE_FILE"))

    result = run(run_module, path="/data")

    assert result["failed"] is True
    assert result["msg"] == "/data exists and is not a directory (FS_FILE_TYPE_FILE)"


def test_state_absent_removes_an_empty_directory(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')
    fake.expect("DELETE", "/v1/files/%2Fdata")

    result = run(run_module, path="/data", state="absent")

    assert result["changed"] is True
    assert result["directory"] == {}


def test_state_absent_on_a_missing_directory_is_unchanged(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)

    result = run(run_module, path="/data", state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


def test_state_absent_on_a_directory_that_holds_data_fails(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')
    fake.expect("DELETE", "/v1/files/%2Fdata", status=400, response=error("fs_directory_not_empty_error", "System error"))

    result = run(run_module, path="/data", state="absent")

    assert result["failed"] is True
    assert result["msg"] == "/data is not empty; directory removes only an empty directory and never deletes data"


def test_state_absent_in_check_mode_sends_no_delete(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), response=attributes("/data"), etag='"1"')

    result = run(run_module, check_mode=True, path="/data", state="absent")

    assert result["changed"] is True
    assert writes(fake) == []
