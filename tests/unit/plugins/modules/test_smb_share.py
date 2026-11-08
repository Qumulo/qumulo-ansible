# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.smb_share, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import smb_share
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import (
    ADMIN,
    EVERYONE,
    FOREIGN_SID,
    GUEST,
    ORPHANED_SID,
    UNSEEN_DOMAINS,
    error,
    fake_with_identities,
)

TENANTS = "/v1/multitenancy/tenants/"
TENANT_ENTRIES = [{"id": 1, "name": "Default", "networks": [], "identity_config_id": 1}, {"id": 2, "name": "eng", "networks": [], "identity_config_id": 1}]


@pytest.fixture
def fake_api(fake_api):
    """A cluster with the tenants Default (1) and eng (2), whose list each run reads to name tenants."""

    def make(*args, tenants=None, **kwargs):
        fake = fake_api(*args, **kwargs)
        fake.serve(TENANTS, {"entries": list(TENANT_ENTRIES if tenants is None else tenants)}, optional=True)
        return fake

    return make


CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SHARES = "/v3/smb/shares/"
ALL = ["READ", "WRITE", "CHANGE_PERMISSIONS"]


def ace(trustee, rights, kind="ALLOWED"):
    return {"type": kind, "trustee": dict(trustee), "rights": list(rights)}


def share(share_id="3", name="projects", tenant_id=1, **overrides):
    """A share as GET /v3/smb/shares/{id} returns it: trustees carry their names."""
    return {
        "id": share_id,
        "share_name": name,
        "tenant_id": tenant_id,
        "fs_path": "/data/projects",
        "description": "",
        "permissions": [ace(ADMIN, ALL), ace(EVERYONE, ["READ"])],
        "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ALL}],
        "access_based_enumeration_enabled": False,
        "default_file_create_mode": "0644",
        "default_directory_create_mode": "0755",
        "require_encryption": False,
        "allow_fs_path_create": False,
        "expand_fs_path_variables": False,
        "offline_files_caching_mode": "NO_CACHING",
        **overrides,
    }


OPTIONS = {
    "share_name": "projects",
    "tenant_id": 1,
    "fs_path": "/data/projects",
    "description": "",
    "permissions": [
        {"type": "ALLOWED", "trustee": "local:admin", "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]},
        {"type": "ALLOWED", "trustee": "world:Everyone", "rights": ["READ"]},
    ],
    "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]}],
    "access_based_enumeration_enabled": False,
    "require_encryption": False,
    "default_file_create_mode": "0644",
    "default_directory_create_mode": "0755",
    "expand_fs_path_variables": False,
    "offline_files_caching_mode": "NO_CACHING",
}
# What the module returns: the options with the tenant by name beside its id.
NAMED = {**OPTIONS, "tenant": "Default"}
DESIRED = {
    "share_name": "projects",
    "fs_path": "/data/projects",
    "permissions": [{"trustee": "local:admin", "rights": ["ALL"]}, {"trustee": "Everyone", "rights": ["READ"]}],
}


def listed(*shares):
    """GET /v3/smb/shares/ leaves trustee names out unless asked for them."""
    return {"entries": [{**s, "permissions": [{**a, "trustee": {**a["trustee"], "name": None}} for a in s["permissions"]]} for s in shares]}


def expect_found(fake, stored, etag='"1"', others=()):
    fake.expect("GET", SHARES, response=listed(*others, stored))
    fake.expect("GET", f"{SHARES}{stored['id']}", response=stored, etag=etag)


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(smb_share, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_absent_share_is_created_with_v3_post(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("2", "Files")))
    fake.expect(
        "POST",
        SHARES,
        body={
            "share_name": "projects",
            "fs_path": "/data/projects",
            "permissions": [
                {"type": "ALLOWED", "trustee": {"auth_id": ADMIN["auth_id"]}, "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]},
                {"type": "ALLOWED", "trustee": {"auth_id": EVERYONE["auth_id"]}, "rights": ["READ"]},
            ],
            "allow_fs_path_create": False,
        },
        response=share(),
        etag='"1"',
    )
    expect_found(fake, share(), others=[share("2", "Files")])

    result = run(run_module, **DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["smb_share"] == NAMED


def test_matching_share_is_unchanged_and_sends_no_write(fake_api, run_module):
    """ALL is the three aggregate rights, and Everyone and admin's SID name the identities the server stores."""
    fake = fake_with_identities(fake_api)
    expect_found(fake, share())

    result = run(
        run_module,
        **{
            **DESIRED,
            "fs_path": "/data/projects/",
            "permissions": [{"trustee": f"sid:{ADMIN['sid']}", "rights": ["ALL"]}, {"trustee": "Everyone", "rights": ["READ", "READ"]}],
        },
        default_file_create_mode="644",
    )

    assert result["changed"] is False
    assert result["smb_share"] == NAMED
    assert writes(fake) == []


def test_share_name_is_matched_without_regard_to_case(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share("2", "Files", fs_path="/"))

    result = run(run_module, share_name="FILES", fs_path="/")

    assert result["changed"] is False
    assert result["smb_share"]["share_name"] == "Files"


def test_same_name_in_two_tenants_without_a_tenant_fails_naming_them(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("3", tenant_id=1), share("4", tenant_id=2)))

    result = run(run_module, **DESIRED)

    assert result["failed"] is True
    assert result["msg"] == "SMB share 'projects' exists in tenants 'Default', 'eng'; set tenant to choose one"
    assert writes(fake) == []


def test_tenant_id_chooses_the_share_of_that_tenant(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("3", tenant_id=1), share("4", tenant_id=2)))
    fake.expect("GET", f"{SHARES}4", response=share("4", tenant_id=2), etag='"1"')

    result = run(run_module, **DESIRED, tenant_id=2)

    assert result["changed"] is False
    assert result["smb_share"]["tenant_id"] == 2


def test_differing_description_patches_with_if_match_from_get(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(), etag='"5"')
    fake.expect("PATCH", f"{SHARES}3", body={"description": "Project data", "allow_fs_path_create": False}, if_match='"5"', response=share())
    expect_found(fake, share(description="Project data"), etag='"6"')

    result = run(run_module, **DESIRED, description="Project data")

    assert result["changed"] is True
    assert result["smb_share"]["description"] == "Project data"


# QUIRK-007: the server keeps allow_fs_path_create from an earlier write and applies it to a later fs_path change.
def test_fs_path_change_always_sends_create_fs_path(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(allow_fs_path_create=True), etag='"5"')
    fake.expect("PATCH", f"{SHARES}3", body={"fs_path": "/data/other", "allow_fs_path_create": False}, if_match='"5"', response=share())
    expect_found(fake, share(fs_path="/data/other"))

    result = run(run_module, **{**DESIRED, "fs_path": "/data/other"})

    assert result["changed"] is True


def test_create_fs_path_is_sent_when_asked_for(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(), etag='"5"')
    fake.expect("PATCH", f"{SHARES}3", body={"fs_path": "/data/new", "allow_fs_path_create": True}, response=share())
    expect_found(fake, share(fs_path="/data/new", allow_fs_path_create=True))

    result = run(run_module, **{**DESIRED, "fs_path": "/data/new"}, create_fs_path=True)

    assert result["changed"] is True
    assert "allow_fs_path_create" not in result["smb_share"]


def test_ace_order_is_significant(fake_api, run_module):
    reordered = [{"trustee": "Everyone", "rights": ["READ"]}, {"trustee": "local:admin", "rights": ["ALL"]}]
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(), etag='"5"')
    fake.expect(
        "PATCH",
        f"{SHARES}3",
        body={
            "permissions": [
                {"type": "ALLOWED", "trustee": {"auth_id": EVERYONE["auth_id"]}, "rights": ["READ"]},
                {"type": "ALLOWED", "trustee": {"auth_id": ADMIN["auth_id"]}, "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]},
            ],
            "allow_fs_path_create": False,
        },
        response=share(),
    )
    expect_found(fake, share(permissions=[ace(EVERYONE, ["READ"]), ace(ADMIN, ALL)]))

    result = run(run_module, **{**DESIRED, "permissions": reordered})

    assert result["changed"] is True
    assert [p["trustee"] for p in result["smb_share"]["permissions"]] == ["world:Everyone", "local:admin"]


REFUSED = pytest.mark.parametrize("refused", [ORPHANED_SID, FOREIGN_SID], ids=["orphaned SID", "foreign-domain SID"])
# Every spelling of a SID the grammar takes: as render writes it, bare as Windows tools print it, and in lower case.
SPELLINGS = pytest.mark.parametrize("spell", ["sid:{}".format, str, str.lower], ids=["sid:", "bare", "bare lower"])


@REFUSED
@SPELLINGS
def test_a_permission_whose_sid_the_find_refuses_compares_by_the_identity_the_cluster_gave(fake_api, run_module, refused, spell):
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    expect_found(fake, share(permissions=[ace(ADMIN, ALL), ace(refused, ["READ"])]))

    result = run(run_module, **{**DESIRED, "permissions": [DESIRED["permissions"][0], {"trustee": spell(refused["sid"]), "rights": ["READ"]}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


@REFUSED
@SPELLINGS
def test_a_permission_whose_sid_the_find_refuses_is_kept_while_another_changes(fake_api, run_module, refused, spell):
    """The write takes the kept permission's auth_id from what the cluster read back, not from a find."""
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    expect_found(fake, share(permissions=[ace(ADMIN, ALL), ace(refused, ["READ"])]), etag='"5"')
    fake.expect(
        "PATCH",
        f"{SHARES}3",
        if_match='"5"',
        body={
            "permissions": [
                {"type": "ALLOWED", "trustee": {"auth_id": ADMIN["auth_id"]}, "rights": ["READ"]},
                {"type": "ALLOWED", "trustee": {"auth_id": refused["auth_id"]}, "rights": ["READ"]},
            ],
            "allow_fs_path_create": False,
        },
        response=share(),
    )
    expect_found(fake, share(permissions=[ace(ADMIN, ["READ"]), ace(refused, ["READ"])]))

    result = run(
        run_module, **{**DESIRED, "permissions": [{"trustee": "local:admin", "rights": ["READ"]}, {"trustee": spell(refused["sid"]), "rights": ["READ"]}]}
    )

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@REFUSED
def test_permissions_holding_a_sid_the_find_refuses_can_be_replaced(fake_api, run_module, refused):
    fake = fake_with_identities(fake_api)
    fake.find_errors.extend(UNSEEN_DOMAINS)
    # Everyone takes the refused entry's place with the same rights, so the comparison reaches the trustees.
    expect_found(fake, share(permissions=[ace(ADMIN, ALL), ace(refused, ["READ"])]), etag='"5"')
    fake.expect("PATCH", f"{SHARES}3", if_match='"5"', response=share())
    expect_found(fake, share())

    result = run(run_module, **DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize(
    ("stored", "wanted", "changed"),
    [
        ([], ["NONE"], False),
        (ALL, ["READ", "WRITE", "CHANGE_PERMISSIONS", "ALL"], False),
        (["READ"], ["READ", "CHANGE_PERMISSIONS"], True),
        (["MODIFY", "READ"], ["READ", "WRITE"], True),
    ],
    ids=["none_is_empty", "all_with_duplicates", "one_right_more", "finer_rights_from_another_tool"],
)
def test_rights_are_compared_as_sets_after_expansion(fake_api, run_module, stored, wanted, changed):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(permissions=[ace(GUEST, stored, "DENIED")]))

    result = run(run_module, check_mode=True, share_name="projects", permissions=[{"type": "DENIED", "trustee": "local:guest", "rights": wanted}])

    assert result["changed"] is changed


@pytest.mark.parametrize(
    ("wanted", "changed"),
    [
        ([{"type": "DENIED", "address_ranges": ["10.0.0.9"], "rights": ["ALL"]}, {"address_ranges": ["10.0.0.0/8"], "rights": ["READ"]}], False),
        ([{"address_ranges": ["10.0.0.0/8"], "rights": ["READ"]}, {"type": "DENIED", "address_ranges": ["10.0.0.9"], "rights": ["ALL"]}], True),
    ],
    ids=["same", "swapped"],
)
def test_network_permissions_are_compared_in_order_with_rights_expanded(fake_api, run_module, wanted, changed):
    stored = [{"type": "DENIED", "address_ranges": ["10.0.0.9"], "rights": ALL}, {"type": "ALLOWED", "address_ranges": ["10.0.0.0/8"], "rights": ["READ"]}]
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(network_permissions=stored))

    result = run(run_module, check_mode=True, share_name="projects", network_permissions=wanted)

    assert result["changed"] is changed


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed())

    result = run(run_module, check_mode=True, **DESIRED)

    assert result["changed"] is True
    assert writes(fake) == []


def test_check_mode_trustee_that_does_not_exist_yet_is_a_change(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share())

    result = run(run_module, check_mode=True, **{**DESIRED, "permissions": [{"trustee": "ad:CORP\\Engineering", "rights": ["ALL"]}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert writes(fake) == []


def test_trustee_that_does_not_exist_fails_outside_check_mode(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share())

    result = run(run_module, **{**DESIRED, "permissions": [{"trustee": "local:svc_backup", "rights": ["READ"]}]})

    assert result["failed"] is True
    assert result["msg"].startswith("trustee 'local:svc_backup': POST /v1/identity/find failed: HTTP 404")
    assert writes(fake) == []


def test_diff_returns_canonical_before_and_after(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share())

    result = run(run_module, check_mode=True, diff=True, share_name="projects", permissions=[{"trustee": "Everyone", "rights": ["ALL"]}])

    assert result["diff"]["before"] == NAMED
    assert result["diff"]["after"] == {
        **NAMED,
        "permissions": [{"type": "ALLOWED", "trustee": "Everyone", "rights": ["CHANGE_PERMISSIONS", "READ", "WRITE"]}],
    }


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"permissions": [{"trustee": "alice", "rights": ["READ"]}]}, "trustee 'alice' needs a prefix"),
        (
            {"permissions": [{"trustee": "Everyone", "rights": ["WRITE"]}]},
            "permissions[1]: an ALLOWED entry with WRITE needs READ too; the cluster refuses write-only access to a share",
        ),
        ({"network_permissions": [{"rights": ["WRITE", "CHANGE_PERMISSIONS"]}]}, "network_permissions[1]: an ALLOWED entry with WRITE needs READ too"),
        ({"fs_path": "data"}, "fs_path 'data' must be an absolute path"),
        ({"default_file_create_mode": "0888"}, "default_file_create_mode '0888' is not an octal mode"),
        # YAML reads an unquoted 0644 as 420 and 0770 as 504; written as modes, those would be 0420 and 0504.
        ({"default_file_create_mode": 420}, "default_file_create_mode 420 is a number; quote the mode, such as '0755'"),
        ({"default_directory_create_mode": 504}, "default_directory_create_mode 504 is a number; quote the mode, such as '0755'"),
    ],
    ids=["bare_trustee", "write_only", "write_only_network", "relative_fs_path", "bad_mode", "unquoted_file_mode", "unquoted_directory_mode"],
)
def test_bad_options_fail_before_any_request(fake_api, run_module, args, message):
    fake = fake_with_identities(fake_api)

    result = run(run_module, share_name="projects", **args)

    assert result["failed"] is True
    assert result["msg"].startswith(message), result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("missing", ["fs_path", "permissions"])
def test_create_without_fs_path_or_permissions_fails(fake_api, run_module, missing):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed())

    result = run(run_module, **{k: v for k, v in DESIRED.items() if k != missing})

    assert result["failed"] is True
    assert result["msg"] == "SMB share 'projects' does not exist; set fs_path and permissions to create it (an empty permissions list denies everyone)"
    assert writes(fake) == []


def test_state_absent_deletes_with_if_match(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share(), etag='"5"')
    fake.expect("DELETE", f"{SHARES}3", if_match='"5"')

    result = run(run_module, share_name="Projects", state="absent")

    assert result["changed"] is True
    assert result["smb_share"] == {}


def test_state_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("2", "Files")))

    result = run(run_module, share_name="projects", state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


def test_share_removed_between_list_and_read_is_absent(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share()))
    fake.expect("GET", f"{SHARES}3", status=404, response=error("smb_share_doesnt_exist_error"))

    result = run(run_module, share_name="projects", state="absent")

    assert result["changed"] is False


# Tenants by name (spec 014, FR-017, FR-031; plan "smb_share, nfs_export, smb_info, nfs_info")


def list_reads(fake):
    return [e for e in fake.log if e.method == "GET" and e.path == SHARES]


def test_tenant_by_name_chooses_the_share_of_that_tenant(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share("4", tenant_id=2), others=[share("3", tenant_id=1)])

    result = run(run_module, **DESIRED, tenant="eng")

    assert result["changed"] is False, result.get("msg")
    assert (result["smb_share"]["tenant"], result["smb_share"]["tenant_id"]) == ("eng", 2)


def test_tenant_and_tenant_id_together_are_accepted_when_they_agree_and_refused_when_they_differ(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share("4", tenant_id=2), others=[share("3", tenant_id=1)])

    agreed = run(run_module, **DESIRED, tenant="eng", tenant_id=2)
    differ = run(run_module, **DESIRED, tenant="eng", tenant_id=1)

    assert agreed["changed"] is False, agreed.get("msg")
    assert differ["msg"] == "tenant 'eng' and tenant_id 1, which is tenant 'Default', differ; give tenant alone"


def test_tenants_runs_each_tenant_from_one_list_read_and_keys_the_result_and_diff_by_name(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    stored = listed(share("3", tenant_id=1), share("4", tenant_id=2, description="old"))
    fake.expect("GET", SHARES, response=stored)
    fake.expect("GET", f"{SHARES}3", response=share("3", tenant_id=1), etag='"1"')
    fake.expect("GET", f"{SHARES}4", response=share("4", tenant_id=2, description="old"), etag='"2"')

    result = run(run_module, check_mode=True, diff=True, **DESIRED, description="", tenants=["Default", "eng"])

    assert result["changed"] is True, result.get("msg")
    assert sorted(result["smb_share"]) == ["Default", "eng"]
    assert result["diff"]["before"]["eng"]["description"] == "old"
    assert result["diff"]["after"]["eng"]["description"] == ""
    assert result["diff"]["before"]["Default"] == result["diff"]["after"]["Default"]
    assert len(list_reads(fake)) == 1
    assert writes(fake) == []


def test_tenants_writes_only_the_tenant_whose_copy_differs(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("3", tenant_id=1), share("4", tenant_id=2, description="old")))
    fake.expect("GET", f"{SHARES}3", response=share("3", tenant_id=1), etag='"1"')
    fake.expect("GET", f"{SHARES}4", response=share("4", tenant_id=2, description="old"), etag='"2"')
    fake.expect("PATCH", f"{SHARES}4", body={"description": "", "allow_fs_path_create": False}, if_match='"2"', response=share("4", tenant_id=2))
    expect_found(fake, share("4", tenant_id=2), others=[share("3", tenant_id=1)])

    result = run(run_module, **DESIRED, description="", tenants=["Default", "eng"])

    assert result["changed"] is True, result.get("msg")
    assert [e.path for e in writes(fake)] == [f"{SHARES}4"]


@pytest.mark.parametrize(
    ("tenants", "message"),
    [([], "tenants is empty"), (["all"], "tenants: all is not a tenant's name here"), (["eng", "Default", "eng"], "tenants names 'eng' more than once")],
)
def test_tenants_empty_all_or_twice_is_refused_before_any_write(fake_api, run_module, tenants, message):
    fake = fake_with_identities(fake_api)

    result = run(run_module, **DESIRED, tenants=tenants)

    assert result["failed"] is True
    assert result["msg"].startswith(message)
    assert writes(fake) == []


def test_a_tenant_an_earlier_task_creates_is_pending_in_check_mode(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("3", tenant_id=1)))

    result = run(run_module, check_mode=True, **DESIRED, tenant="finance")

    assert result["changed"] is True, result.get("msg")
    assert (result["smb_share"]["tenant"], result["smb_share"]["tenant_id"]) == ("finance", None)
    assert result["notes"] == ["tenant 'finance' does not exist yet; an earlier task is expected to create it"]
    assert writes(fake) == []


def test_tenants_note_a_tenant_an_earlier_task_creates_under_its_name(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share("4", tenant_id=2), others=[share("3", tenant_id=1)])

    result = run(run_module, check_mode=True, **DESIRED, tenants=["eng", "finance"])

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["finance: tenant 'finance' does not exist yet; an earlier task is expected to create it"]
    assert writes(fake) == []


def test_a_tenant_the_cluster_lacks_fails_outside_check_mode(fake_api, run_module):
    fake_with_identities(fake_api)

    result = run(run_module, **DESIRED, tenant="finance")

    assert result["msg"] == "no tenant on this cluster is named 'finance'; its tenants are 'Default' and 'eng'"


def test_state_absent_in_a_tenant_the_cluster_lacks_is_satisfied(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("3", tenant_id=1)))

    result = run(run_module, share_name="projects", state="absent", tenants=["finance", "eng"], diff=True)

    assert result["changed"] is False, result.get("msg")
    assert result["smb_share"] == {"finance": {}, "eng": {}}
    assert writes(fake) == []


@pytest.mark.parametrize("status", [403, 404])
def test_a_run_without_tenant_names_no_tenant_where_the_tenant_list_cannot_be_read(fake_api, run_module, status):
    """A user without PRIVILEGE_TENANT_READ (403), or a release without multitenancy (404), still manages its shares."""
    fake = fake_with_identities(fake_api)
    fake.serve(TENANTS, error("api_access_denied_error"), status=status)
    expect_found(fake, share())

    result = run(run_module, **DESIRED)

    assert result["changed"] is False, result.get("msg")
    assert result["smb_share"] == {**OPTIONS, "tenant": None}
    assert not result.get("warnings")


def test_the_result_names_the_tenant_before_its_id(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_found(fake, share("4", tenant_id=2), others=[share("3", tenant_id=1)])

    result = run(run_module, **DESIRED, tenant="eng")

    keys = list(result["smb_share"])
    assert keys.index("tenant") == keys.index("tenant_id") - 1, keys


def test_state_absent_with_a_tenant_and_a_tenant_id_that_neither_exist_is_satisfied(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, share_name="projects", state="absent", tenant="finance", tenant_id=7)

    assert result["changed"] is False, result.get("msg")
    assert result["smb_share"] == {}
    assert writes(fake) == []


def test_a_tenant_id_alone_that_no_tenant_holds_is_refused_before_the_share_is_read(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, **DESIRED, tenant_id=7)
    gone = run(run_module, share_name="projects", state="absent", tenant_id=7)

    assert result["msg"] == "tenant_id 7 is no tenant on the cluster; name its tenant with tenant"
    assert gone["changed"] is False, gone.get("msg")
    assert list_reads(fake) == []


@pytest.mark.parametrize("status", [403, 404])
def test_a_tenant_id_alone_is_used_as_given_where_the_tenant_list_cannot_be_read(fake_api, run_module, status):
    fake = fake_with_identities(fake_api)
    fake.serve(TENANTS, error("api_access_denied_error"), status=status)
    fake.expect("GET", SHARES, response=listed(share("3", tenant_id=1)))

    result = run(run_module, check_mode=True, **DESIRED, tenant_id=7)

    assert result["changed"] is True, result.get("msg")
    assert (result["smb_share"]["tenant"], result["smb_share"]["tenant_id"]) == (None, 7)


def test_tenants_resolves_every_name_before_the_first_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module, **DESIRED, description="", tenants=["eng", "finance"])

    assert result["msg"] == "no tenant on this cluster is named 'finance'; its tenants are 'Default' and 'eng'"
    assert writes(fake) == []


def test_tenants_refuses_before_any_write_a_tenant_that_lacks_the_share_without_the_options_to_create_it(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", SHARES, response=listed(share("4", tenant_id=2, description="old")))

    result = run(run_module, share_name="projects", description="", tenants=["eng", "Default"])

    assert result["failed"] is True
    assert result.get("changed") is not True
    assert result["msg"] == (
        "tenant 'Default': SMB share 'projects' does not exist; set fs_path and permissions to create it (an empty permissions list denies everyone)"
    )
    assert writes(fake) == []


def test_a_tenants_write_failure_names_the_tenant_and_the_tenants_already_changed(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    old = (share("3", tenant_id=1, description="old"), share("4", tenant_id=2, description="old"))
    fake.expect("GET", SHARES, response=listed(*old))
    fake.expect("GET", f"{SHARES}4", response=old[1], etag='"2"')
    fake.expect("PATCH", f"{SHARES}4", body={"description": "", "allow_fs_path_create": False}, if_match='"2"', response=share("4", tenant_id=2))
    expect_found(fake, share("4", tenant_id=2), others=[old[0]])
    fake.expect("GET", f"{SHARES}3", response=old[0], etag='"1"')
    fake.expect("PATCH", f"{SHARES}3", status=409, response=error("smb_share_conflict_error"))

    result = run(run_module, share_name="projects", description="", tenants=["eng", "Default"])

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["msg"].startswith("tenant 'Default': ")
    assert result["msg"].endswith("; the task had already changed tenant 'eng'")
