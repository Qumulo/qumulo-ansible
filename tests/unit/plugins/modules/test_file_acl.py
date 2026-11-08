# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.file_acl, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import file_acl
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import (
    ADMIN,
    AUTHENTICATED_USERS,
    EVERYONE,
    FILE_GROUP_OWNER,
    FILE_OWNER,
    FOREIGN_SID,
    ORPHANED_SID,
    UNNAMEABLE,
    UNSEEN_DOMAINS,
    UNSEEN_SID_DOMAIN,
    USERS,
    acl_path,
    attributes,
    attributes_path,
    error,
    fake_with_identities,
    file_acl_entry,
    file_acl_response,
)

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
SPOKES = "/v2/portal/spokes/"
ALL = [
    "READ",
    "READ_EA",
    "READ_ATTR",
    "READ_ACL",
    "WRITE_EA",
    "WRITE_ATTR",
    "WRITE_ACL",
    "CHANGE_OWNER",
    "WRITE_GROUP",
    "DELETE",
    "EXECUTE",
    "MODIFY",
    "EXTEND",
    "DELETE_CHILD",
    "SYNCHRONIZE",
]
READ = ["READ", "READ_EA", "READ_ATTR", "READ_ACL", "EXECUTE", "SYNCHRONIZE"]
INHERIT = ["OBJECT_INHERIT", "CONTAINER_INHERIT"]
STORED = file_acl_response(
    [
        file_acl_entry(ADMIN, ALL, flags=INHERIT),
        file_acl_entry(EVERYONE, READ),
    ]
)
DECLARED = [
    {"trustee": "local:admin", "rights": ["ALL"], "flags": ["CONTAINER_INHERIT", "OBJECT_INHERIT"]},
    {"trustee": "Everyone", "rights": list(reversed(READ))},
]
NOT_FOUND = error("fs_no_such_entry_error")
NO_SPOKES = {"entries": []}


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("PUT", "PATCH", "POST", "DELETE") and not e.background]


def read(fake, acl=None, etag='"r1"', path="/data"):
    fake.expect("GET", attributes_path(path), response=attributes(path), etag='"a1"')
    fake.expect("GET", acl_path(path), response=acl or STORED, etag=etag)


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(file_acl, {**CONNECTION, "path": "/data", **args}, check_mode=check_mode, diff=diff)


def test_a_matching_acl_is_unchanged_and_sends_no_write(fake_api, run_module):
    """ALL is every right, rights and flags are sets, and Everyone is world:Everyone."""
    fake = fake_with_identities(fake_api)
    read(fake)

    result = run(run_module, aces=DECLARED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert result["acl"] == {
        "path": "/data",
        "control": ["PRESENT"],
        "posix_special_permissions": [],
        "aces": [
            {"type": "ALLOWED", "trustee": "local:admin", "rights": ALL, "flags": INHERIT},
            {"type": "ALLOWED", "trustee": "world:Everyone", "rights": READ, "flags": []},
        ],
    }


def test_a_trustee_is_compared_by_the_identity_it_resolves_to(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake)

    result = run(run_module, aces=[{**DECLARED[0], "trustee": f"sid:{ADMIN['sid']}"}, DECLARED[1]])

    assert result["changed"] is False
    assert writes(fake) == []


def test_authenticated_users_is_unchanged_on_a_node_that_refuses_its_sid(fake_api, run_module):
    """The cluster names the group without a domain; any case of its NT AUTHORITY name declares it."""
    fake = fake_with_identities(fake_api)
    fake.find_errors.append(UNSEEN_SID_DOMAIN)
    read(fake, acl=file_acl_response([file_acl_entry(AUTHENTICATED_USERS, READ)]))

    result = run(run_module, aces=[{"trustee": "ad:nt authority\\authenticated users", "rights": READ}])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert [ace["trustee"] for ace in result["acl"]["aces"]] == ["ad:NT AUTHORITY\\Authenticated Users"]


def test_file_owner_and_file_group_owner_are_unchanged_and_named_by_the_internal_prefix(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake, acl=file_acl_response([file_acl_entry(FILE_OWNER, ALL, flags=INHERIT), file_acl_entry(FILE_GROUP_OWNER, READ, flags=INHERIT)]))

    result = run(
        run_module,
        aces=[
            {"trustee": "internal:File Owner", "rights": ["ALL"], "flags": INHERIT},
            {"trustee": "internal:file group owner", "rights": READ, "flags": INHERIT},
        ],
    )

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert [ace["trustee"] for ace in result["acl"]["aces"]] == ["internal:File Owner", "internal:File Group Owner"]


def test_file_owner_is_written_by_its_auth_id(fake_api, run_module):
    after = file_acl_response([file_acl_entry(FILE_OWNER, ALL, flags=INHERIT)])
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect(
        "PUT",
        acl_path("/data"),
        if_match='"r1"',
        body={
            "control": ["PRESENT"],
            "posix_special_permissions": [],
            "aces": [{"type": "ALLOWED", "flags": INHERIT, "trustee": {"auth_id": FILE_OWNER["auth_id"]}, "rights": ALL}],
        },
        response=after,
    )
    read(fake, acl=after, etag='"r2"')

    result = run(run_module, aces=[{"trustee": "internal:File Owner", "rights": ["ALL"], "flags": INHERIT}])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["acl"]["aces"][0]["trustee"] == "internal:File Owner"


def test_a_declared_sid_of_authenticated_users_is_written_on_a_node_that_refuses_it(fake_api, run_module):
    """A first write has nothing read back to resolve before the declared SID, so the SID itself must resolve."""
    after = file_acl_response([file_acl_entry(AUTHENTICATED_USERS, READ)])
    fake = fake_with_identities(fake_api)
    fake.find_errors.append(UNSEEN_SID_DOMAIN)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect(
        "PUT",
        acl_path("/data"),
        if_match='"r1"',
        body={
            "control": ["PRESENT"],
            "posix_special_permissions": [],
            "aces": [{"type": "ALLOWED", "flags": [], "trustee": {"auth_id": AUTHENTICATED_USERS["auth_id"]}, "rights": READ}],
        },
        response=after,
    )
    read(fake, acl=after, etag='"r2"')

    result = run(run_module, aces=[{"trustee": "sid:S-1-5-11", "rights": READ}])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert [ace["trustee"] for ace in result["acl"]["aces"]] == ["ad:NT AUTHORITY\\Authenticated Users"]


REFUSED = pytest.mark.parametrize("refused", [ORPHANED_SID, FOREIGN_SID], ids=["orphaned SID", "foreign-domain SID"])
# Every spelling of a SID the grammar takes: as render writes it, bare as Windows tools print it, and in lower case.
SPELLINGS = pytest.mark.parametrize(
    "spell", ["sid:{}".format, str, lambda sid: f"sid:{sid.lower()}", str.lower], ids=["sid:", "bare", "sid: lower", "bare lower"]
)


@REFUSED
@SPELLINGS
def test_an_entry_whose_sid_the_find_refuses_compares_by_the_identity_the_cluster_gave(fake_api, run_module, refused, spell):
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    read(fake, acl=file_acl_response([file_acl_entry(ADMIN, ALL, flags=INHERIT), file_acl_entry(refused, READ)]))

    result = run(run_module, aces=[DECLARED[0], {"trustee": spell(refused["sid"]), "rights": READ}])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []
    assert {"sid": refused["sid"]} not in [e.body for e in fake.log if e.path == "/v1/identity/find"]


@REFUSED
def test_an_acl_holding_a_sid_the_find_refuses_can_be_replaced(fake_api, run_module, refused):
    # Everyone takes the refused entry's place with the same rights, so the comparison reaches the trustees.
    stored = file_acl_response([file_acl_entry(ADMIN, ALL, flags=INHERIT), file_acl_entry(refused, READ)])
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    read(fake, acl=stored)
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("PUT", acl_path("/data"), if_match='"r1"', response=STORED)
    read(fake, acl=STORED, etag='"r2"')

    result = run(run_module, aces=DECLARED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert writes(fake) == ["PUT /v2/files/%2Fdata/info/acl"]


@REFUSED
@SPELLINGS
def test_an_entry_whose_sid_the_find_refuses_is_kept_while_another_changes(fake_api, run_module, refused, spell):
    """The write takes the kept entry's auth_id from what the cluster read back, not from a find."""
    after = file_acl_response([file_acl_entry(ADMIN, READ), file_acl_entry(refused, READ)])
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    read(fake, acl=file_acl_response([file_acl_entry(ADMIN, ALL, flags=INHERIT), file_acl_entry(refused, READ)]))
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect(
        "PUT",
        acl_path("/data"),
        if_match='"r1"',
        body={
            "control": ["PRESENT"],
            "posix_special_permissions": [],
            "aces": [
                {"type": "ALLOWED", "flags": [], "trustee": {"auth_id": ADMIN["auth_id"]}, "rights": READ},
                {"type": "ALLOWED", "flags": [], "trustee": {"auth_id": refused["auth_id"]}, "rights": READ},
            ],
        },
        response=after,
    )
    read(fake, acl=after, etag='"r2"')

    result = run(run_module, aces=[{"trustee": "local:admin", "rights": READ}, {"trustee": spell(refused["sid"]), "rights": READ}])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert {"sid": refused["sid"]} not in [e.body for e in fake.log if e.path == "/v1/identity/find"]


def test_an_entry_the_grammar_cannot_name_is_replaced(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake, acl=file_acl_response([file_acl_entry(UNNAMEABLE, READ)]))
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("PUT", acl_path("/data"), if_match='"r1"', response=STORED)
    read(fake, acl=STORED, etag='"r2"')

    result = run(run_module, aces=DECLARED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert writes(fake) == ["PUT /v2/files/%2Fdata/info/acl"]


def test_an_entry_the_grammar_cannot_name_is_reported_by_its_name_in_a_check_diff(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake, acl=file_acl_response([file_acl_entry(UNNAMEABLE, READ)]))
    fake.expect("GET", SPOKES, response=NO_SPOKES)

    result = run(run_module, check_mode=True, diff=True, aces=DECLARED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert [ace["trustee"] for ace in result["diff"]["before"]["aces"]] == ["Nobody"]
    assert writes(fake) == []


def test_a_different_acl_is_put_whole_with_if_match_from_the_read(fake_api, run_module):
    after = file_acl_response([file_acl_entry(USERS, ["READ", "EXECUTE"]), file_acl_entry(ADMIN, ALL, flags=INHERIT)])
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect(
        "PUT",
        acl_path("/data"),
        if_match='"r1"',
        body={
            "control": ["PRESENT"],
            "posix_special_permissions": [],
            "aces": [
                {"type": "ALLOWED", "flags": [], "trustee": {"auth_id": USERS["auth_id"]}, "rights": ["READ", "EXECUTE"]},
                {"type": "ALLOWED", "flags": INHERIT, "trustee": {"auth_id": ADMIN["auth_id"]}, "rights": ALL},
            ],
        },
        response=after,
    )
    read(fake, acl=after, etag='"r2"')

    result = run(run_module, aces=[{"trustee": "local:Users", "rights": ["EXECUTE", "READ"]}, DECLARED[0]])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert [entry["trustee"] for entry in result["acl"]["aces"]] == ["local:Users", "local:admin"]


def test_the_same_entries_in_another_order_are_a_change(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)

    result = run(run_module, check_mode=True, aces=list(reversed(DECLARED)))

    assert result["changed"] is True
    assert writes(fake) == []


def test_check_mode_with_diff_shows_the_drift_and_sends_no_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)

    result = run(run_module, check_mode=True, diff=True, aces=[DECLARED[0], {"type": "DENIED", "trustee": "Everyone", "rights": ["DELETE"]}])

    assert result["changed"] is True
    assert result["diff"]["before"]["aces"][1] == {"type": "ALLOWED", "trustee": "world:Everyone", "rights": READ, "flags": []}
    assert result["diff"]["after"]["aces"][1] == {"type": "DENIED", "trustee": "Everyone", "rights": ["DELETE"], "flags": []}
    assert writes(fake) == []


def test_unset_control_and_special_bits_keep_the_stored_ones(fake_api, run_module):
    stored = file_acl_response([file_acl_entry(ADMIN, ALL, flags=INHERIT)], control=["PRESENT", "PROTECTED"], special=["STICKY_BIT"])
    fake = fake_with_identities(fake_api)
    read(fake, acl=stored)

    result = run(run_module, aces=[DECLARED[0]])

    assert result["changed"] is False
    assert result["acl"]["control"] == ["PRESENT", "PROTECTED"]
    assert result["acl"]["posix_special_permissions"] == ["STICKY_BIT"]


def test_control_flags_compare_as_a_set(fake_api, run_module):
    stored = file_acl_response([file_acl_entry(ADMIN, ALL, flags=INHERIT)], control=["PRESENT", "AUTO_INHERIT"])
    fake = fake_with_identities(fake_api)
    read(fake, acl=stored)

    result = run(run_module, aces=[DECLARED[0]], control=["AUTO_INHERIT", "PRESENT"], posix_special_permissions=[])

    assert result["changed"] is False


def test_a_trustee_that_does_not_exist_yet_is_a_change_in_check_mode(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)

    result = run(run_module, check_mode=True, aces=[{"trustee": "local:it-new-group", "rights": ["READ"]}])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_bare_trustee_name_fails_before_any_request(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, aces=[{"trustee": "admin", "rights": ["READ"]}])

    assert result["failed"] is True
    assert "needs a prefix" in result["msg"]
    assert fake.log == []


def test_a_missing_path_fails_naming_the_directory_module(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)

    result = run(run_module, aces=DECLARED)

    assert result["failed"] is True
    assert "/data does not exist" in result["msg"] and "qumulo.cluster.directory" in result["msg"]


def test_a_missing_path_is_a_change_in_check_mode(fake_api, run_module):
    """An earlier task in the same check run would create it."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", attributes_path("/data"), status=404, response=NOT_FOUND)

    result = run(run_module, check_mode=True, aces=DECLARED)

    assert result["changed"] is True
    assert any("does not exist yet" in note for note in result["notes"])


def spoke(local_root="200"):
    return {
        "entries": [
            {
                "id": 1,
                "hub_cluster_uuid": HUB_UUID,
                "hub_hosts": [],
                "type": "PORTAL_READ_WRITE",
                "state": "ACCEPTED",
                "status": "ACTIVE",
                "roots": [{"local_root": local_root, "remote_root": "300", "remote_root_path": "/hub-data/", "authorized": True}],
            }
        ]
    }


def test_a_change_inside_a_spoke_root_is_refused_without_allow_data_loss(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake, path="/edge-data/sub")
    fake.expect("GET", SPOKES, response=spoke())
    fake.expect("GET", "/v1/files/200/info/attributes", response=attributes("/edge-data", file_id="200"))

    result = run(run_module, check_mode=True, path="/edge-data/sub", aces=[DECLARED[1]])

    assert result["failed"] is True
    assert "spoke root /edge-data" in result["msg"] and HUB_UUID in result["msg"]
    assert "allow_data_loss" in result["msg"] and "the hub's" in result["msg"]
    assert writes(fake) == []


def test_allow_data_loss_replaces_the_acl_inside_a_spoke_root(fake_api, run_module):
    after = file_acl_response([file_acl_entry(EVERYONE, READ)])
    fake = fake_with_identities(fake_api)
    read(fake, path="/edge-data")
    fake.expect("GET", SPOKES, response=spoke())
    fake.expect("GET", "/v1/files/200/info/attributes", response=attributes("/edge-data", file_id="200"))
    fake.expect("PUT", acl_path("/edge-data"), if_match='"r1"', response=after)
    read(fake, acl=after, path="/edge-data")

    result = run(run_module, path="/edge-data", aces=[DECLARED[1]], allow_data_loss=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert any("spoke root /edge-data" in note and HUB_UUID in note for note in result["notes"])


def test_an_unchanged_acl_inside_a_spoke_root_needs_no_consent(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    read(fake, path="/edge-data")

    result = run(run_module, path="/edge-data", aces=DECLARED)

    assert result["changed"] is False


def test_a_stale_etag_rereads_and_puts_with_the_new_one(fake_api, run_module):
    """The ETag is the file's revision, which a change beneath the directory bumps between read and write."""
    after = file_acl_response([file_acl_entry(EVERYONE, READ)])
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("PUT", acl_path("/data"), if_match='"r1"', status=412, response=error("http_precondition_failed_error"))
    fake.expect("GET", acl_path("/data"), response=STORED, etag='"r2"')
    fake.expect("PUT", acl_path("/data"), if_match='"r2"', response=after)
    read(fake, acl=after)

    result = run(run_module, aces=[DECLARED[1]])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_stale_etag_whose_reread_already_matches_writes_nothing_more(fake_api, run_module):
    after = file_acl_response([file_acl_entry(EVERYONE, READ)])
    fake = fake_with_identities(fake_api)
    read(fake)
    fake.expect("GET", SPOKES, response=NO_SPOKES)
    fake.expect("PUT", acl_path("/data"), if_match='"r1"', status=412, response=error("http_precondition_failed_error"))
    fake.expect("GET", acl_path("/data"), response=after, etag='"r2"')
    read(fake, acl=after, etag='"r2"')

    result = run(run_module, aces=[DECLARED[1]])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == [f"PUT {acl_path('/data')}"]
