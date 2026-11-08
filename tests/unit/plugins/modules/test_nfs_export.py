# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.nfs_export, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import nfs_export
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

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
EXPORTS = "/v3/nfs/exports/"
ROOT_SQUASH = {"user_mapping": "NFS_MAP_ROOT", "map_to_user": {"id_type": "LOCAL_USER", "id_value": "guest"}}


def restriction(hosts=(), read_only=False, **extra):
    """A restriction as the server returns it, with its defaults filled in."""
    return {
        "host_restrictions": list(hosts),
        "read_only": read_only,
        "require_privileged_port": False,
        "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
        "user_mapping": "NFS_MAP_NONE",
        **extra,
    }


def export(export_id="2", path="/projects", tenant_id=1, **overrides):
    return {
        "id": export_id,
        "export_path": path,
        "tenant_id": tenant_id,
        "fs_path": "/data/projects",
        "description": "",
        "restrictions": [restriction(["10.0.0.0/8"], **ROOT_SQUASH), restriction(read_only=True)],
        "fields_to_present_as_32_bit": [],
        **overrides,
    }


OPTION_RESTRICTIONS = [
    {
        "host_restrictions": ["10.0.0.0/8"],
        "read_only": False,
        "require_privileged_port": False,
        "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
        "user_mapping": "NFS_MAP_ROOT",
        "map_to_user": "local:guest",
    },
    {
        "host_restrictions": [],
        "read_only": True,
        "require_privileged_port": False,
        "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
        "user_mapping": "NFS_MAP_NONE",
    },
]
OPTIONS = {
    "export_path": "/projects",
    "tenant_id": 1,
    "fs_path": "/data/projects",
    "description": "",
    "restrictions": OPTION_RESTRICTIONS,
    "fields_to_present_as_32_bit": [],
}
# What the module returns: the options with the tenant by name beside its id.
NAMED = {**OPTIONS, "tenant": "Default"}
DESIRED = {
    "export_path": "/projects",
    "fs_path": "/data/projects",
    "restrictions": [
        {"host_restrictions": ["10.0.0.0/8"], "user_mapping": "NFS_MAP_ROOT", "map_to_user": "local:guest"},
        {"read_only": True},
    ],
}
WIRE_RESTRICTIONS = [restriction(["10.0.0.0/8"], **ROOT_SQUASH), restriction(read_only=True)]


def expect_found(fake, stored, etag='"1"', others=()):
    fake.expect("GET", EXPORTS, response={"entries": [*others, stored]})
    fake.expect("GET", f"{EXPORTS}{stored['id']}", response=stored, etag=etag)


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(nfs_export, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def openapi(*restriction_fields):
    """The part of /openapi.json the require_tls probe reads."""
    restriction_schema = {"type": "object", "properties": {name: {"type": "boolean"} for name in restriction_fields}}
    body = {"type": "object", "properties": {"restrictions": {"type": "array", "items": restriction_schema}}}
    return {"paths": {EXPORTS: {"post": {"requestBody": {"content": {"application/json": {"schema": body}}}}}}}


@pytest.fixture(autouse=True)
def cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


def test_absent_export_is_created_with_v3_post(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("1", "/", fs_path="/")]})
    fake.expect(
        "POST",
        f"{EXPORTS}?allow-fs-path-create=false",
        body={"export_path": "/projects", "fs_path": "/data/projects", "description": "", "restrictions": WIRE_RESTRICTIONS},
        response=export(),
        etag='"1"',
    )
    expect_found(fake, export())

    result = run(run_module, **DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["nfs_export"] == NAMED


def test_matching_export_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export())

    result = run(run_module, **{**DESIRED, "fs_path": "/data/projects/"}, description="", fields_to_present_as_32_bit=["NONE"])

    assert result["changed"] is False
    assert result["nfs_export"] == NAMED
    assert writes(fake) == []


def test_differing_description_patches_with_if_match_from_get(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export(), etag='"4"')
    fake.expect("PATCH", f"{EXPORTS}2", body={"description": "Project data"}, if_match='"4"', response=export(description="Project data"))
    expect_found(fake, export(description="Project data"), etag='"5"')

    result = run(run_module, **DESIRED, description="Project data")

    assert result["changed"] is True
    assert result["nfs_export"]["description"] == "Project data"


def test_check_mode_reports_changed_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": []})

    result = run(run_module, check_mode=True, **DESIRED)

    assert result["changed"] is True
    assert writes(fake) == []


def test_diff_returns_canonical_before_and_after(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export())

    result = run(run_module, check_mode=True, diff=True, export_path="/projects", fields_to_present_as_32_bit=["ALL"])

    assert result["diff"] == {"before": NAMED, "after": {**NAMED, "fields_to_present_as_32_bit": ["FILE_IDS", "FILE_SIZES", "FS_SIZE"]}}


@pytest.mark.parametrize(
    ("stored", "wanted"),
    [
        ({"fields_to_present_as_32_bit": ["FILE_IDS", "FILE_SIZES", "FS_SIZE"]}, {"fields_to_present_as_32_bit": ["FS_SIZE", "ALL"]}),
        (
            {
                "restrictions": [
                    restriction(
                        user_mapping="NFS_MAP_ALL", map_to_user={"id_type": "NFS_UID", "id_value": "53"}, map_to_group={"id_type": "NFS_GID", "id_value": "7"}
                    )
                ]
            },
            {"restrictions": [{"user_mapping": "NFS_MAP_ALL", "map_to_user": "uid:053", "map_to_group": "gid:7"}]},
        ),
        ({"restrictions": [restriction(require_tls=False)]}, {"restrictions": [{}]}),
    ],
    ids=["all_is_every_field", "uid_without_leading_zeros", "require_tls_false_when_unset"],
)
def test_server_defaults_are_canonicalized_before_compare(fake_api, run_module, stored, wanted):
    fake = fake_api()
    expect_found(fake, export(**stored))

    result = run(run_module, export_path="/projects", **wanted)

    assert result["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize("path", ["/projects/", "projects", "/data//projects"], ids=["trailing_slash", "relative", "empty_component"])
def test_bad_export_path_fails_before_any_request(fake_api, run_module, path):
    fake = fake_api()

    result = run(run_module, **{**DESIRED, "export_path": path})

    assert result["failed"] is True
    assert result["msg"].startswith(f"export_path '{path}'"), result["msg"]
    assert fake.log == []


def test_require_tls_below_7_11_fails_with_version_message(fake_api, run_module):
    fake = fake_api(version="7.10.3")
    fake.expect("GET", "/openapi.json", response=openapi("read_only"))

    result = run(run_module, **{**DESIRED, "restrictions": [{"require_tls": True}]})

    assert result["failed"] is True
    assert result["msg"] == "restrictions[].require_tls requires Qumulo Core 7.11.0 or later; qumulo.example.com runs 7.10.3"
    assert writes(fake) == []


def test_require_tls_on_a_cluster_that_supports_it_is_sent(fake_api, run_module):
    fake = fake_api(version="7.11.0")
    fake.expect("GET", "/openapi.json", response=openapi("read_only", "require_tls"))
    expect_found(fake, export(restrictions=[restriction(require_tls=False)]), etag='"4"')
    fake.expect("PATCH", f"{EXPORTS}2", body={"restrictions": [restriction(require_tls=True)]}, if_match='"4"', response=export())
    expect_found(fake, export(restrictions=[restriction(require_tls=True)]))

    result = run(run_module, export_path="/projects", restrictions=[{"require_tls": True}])

    assert result["changed"] is True
    assert result["nfs_export"]["restrictions"][0]["require_tls"] is True


def test_same_path_in_two_tenants_without_a_tenant_fails_naming_them(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("2", tenant_id=1), export("3", tenant_id=2)]})

    result = run(run_module, **DESIRED)

    assert result["failed"] is True
    assert result["msg"] == "NFS export '/projects' exists in tenants 'Default', 'eng'; set tenant to choose one"
    assert writes(fake) == []


def test_tenant_id_chooses_the_export_of_that_tenant(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("2", tenant_id=1), export("3", tenant_id=2)]})
    fake.expect("GET", f"{EXPORTS}3", response=export("3", tenant_id=2), etag='"1"')

    result = run(run_module, **DESIRED, tenant_id=2)

    assert result["changed"] is False
    assert result["nfs_export"]["tenant_id"] == 2


def test_empty_host_list_is_not_the_star_wildcard(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export(restrictions=[restriction(["*"])]))

    result = run(run_module, check_mode=True, export_path="/projects", restrictions=[{"host_restrictions": []}])

    assert result["changed"] is True


def test_restrictions_are_compared_in_order(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export(), etag='"4"')
    fake.expect("PATCH", f"{EXPORTS}2", body={"restrictions": list(reversed(WIRE_RESTRICTIONS))}, if_match='"4"', response=export())
    expect_found(fake, export(restrictions=list(reversed(WIRE_RESTRICTIONS))))

    result = run(run_module, export_path="/projects", restrictions=list(reversed(DESIRED["restrictions"])))

    assert result["changed"] is True


def test_uid_mapping_is_sent_as_nfs_uid_and_gid(fake_api, run_module):
    mapped = restriction(
        user_mapping="NFS_MAP_ALL", map_to_user={"id_type": "NFS_UID", "id_value": "65534"}, map_to_group={"id_type": "NFS_GID", "id_value": "65534"}
    )
    fake = fake_api()
    expect_found(fake, export(), etag='"4"')
    fake.expect("PATCH", f"{EXPORTS}2", body={"restrictions": [mapped]}, response=export(restrictions=[mapped]))
    expect_found(fake, export(restrictions=[mapped]))

    result = run(run_module, export_path="/projects", restrictions=[{"user_mapping": "NFS_MAP_ALL", "map_to_user": "uid:65534", "map_to_group": "gid:65534"}])

    assert result["changed"] is True
    assert result["nfs_export"]["restrictions"][0]["map_to_user"] == "uid:65534"


# QUIRK-013: the cluster reads an id_value with a leading zero as octal, so 0065 would be stored as 53.
def test_uid_and_gid_with_leading_zeros_are_written_in_decimal(fake_api, run_module):
    mapped = restriction(user_mapping="NFS_MAP_ALL", map_to_user={"id_type": "NFS_UID", "id_value": "65"}, map_to_group={"id_type": "NFS_GID", "id_value": "7"})
    fake = fake_api()
    expect_found(fake, export(), etag='"4"')
    fake.expect("PATCH", f"{EXPORTS}2", body={"restrictions": [mapped]}, if_match='"4"', response=export(restrictions=[mapped]))
    expect_found(fake, export(restrictions=[mapped]))

    result = run(run_module, export_path="/projects", restrictions=[{"user_mapping": "NFS_MAP_ALL", "map_to_user": "uid:0065", "map_to_group": "gid:007"}])

    assert result["changed"] is True
    assert result["nfs_export"]["restrictions"][0]["map_to_user"] == "uid:65"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"map_to_user": "local:guest"}, "restrictions[1]: map_to_user applies only when user_mapping is NFS_MAP_ROOT or NFS_MAP_ALL"),
        ({"user_mapping": "NFS_MAP_ALL"}, "restrictions[1]: user_mapping NFS_MAP_ALL needs map_to_user"),
        ({"user_mapping": "NFS_MAP_ALL", "map_to_user": "uid:65534"}, "restrictions[1]: a uid map_to_user needs map_to_group, as gid:<n>"),
        (
            {"user_mapping": "NFS_MAP_ALL", "map_to_user": "local:guest", "map_to_group": "gid:7"},
            "restrictions[1]: map_to_group applies only with a uid map_to_user",
        ),
        ({"user_mapping": "NFS_MAP_ALL", "map_to_user": "ad:CORP\\alice"}, "restrictions[1]: map_to_user 'ad:CORP\\alice' must be local:<name> or uid:<n>"),
        ({"user_mapping": "NFS_MAP_ALL", "map_to_user": "guest"}, "trustee 'guest' needs a prefix"),
        (
            {"user_mapping": "NFS_MAP_ALL", "map_to_user": ""},
            "restrictions[1]: map_to_user is empty, which is how the cluster reports a mapping to a local user that no longer exists; name an existing user",
        ),
        (
            {"user_mapping": "NFS_MAP_ALL", "map_to_user": "uid:1", "map_to_group": "local:Users"},
            "restrictions[1]: map_to_group 'local:Users' must be gid:<n>",
        ),
    ],
    ids=["user_without_mapping", "mapping_without_user", "uid_without_group", "group_with_local_user", "ad_user", "bare_name", "deleted_user", "local_group"],
)
def test_bad_user_mapping_fails_before_any_request(fake_api, run_module, entry, message):
    fake = fake_api()

    result = run(run_module, export_path="/projects", restrictions=[entry])

    assert result["failed"] is True
    assert result["msg"].startswith(message), result["msg"]
    assert fake.log == []


# G-MAPTO: a restriction mapped to a deleted local user reads back empty, and the cluster refuses any PATCH.
def test_restriction_mapped_to_a_deleted_user_fails_unless_restrictions_are_replaced(fake_api, run_module):
    orphaned = export(restrictions=[restriction(user_mapping="NFS_MAP_ALL", map_to_user={"id_type": "LOCAL_USER", "id_value": ""})])
    fake = fake_api()
    expect_found(fake, orphaned)

    result = run(run_module, export_path="/projects", description="Project data")

    assert result["failed"] is True
    assert result["msg"] == (
        "NFS export '/projects': restriction 1 maps users to a local user that no longer exists, and the cluster refuses "
        "every change to the export until its restrictions are replaced; set restrictions"
    )
    assert writes(fake) == []


def test_unchanged_export_mapped_to_a_deleted_user_is_ok(fake_api, run_module):
    orphaned = export(restrictions=[restriction(user_mapping="NFS_MAP_ALL", map_to_user={"id_type": "LOCAL_USER", "id_value": ""})])
    fake = fake_api()
    expect_found(fake, orphaned)

    result = run(run_module, export_path="/projects", fs_path="/data/projects", description="")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_replacing_the_restrictions_of_a_deleted_user_mapping_patches(fake_api, run_module):
    orphaned = export(restrictions=[restriction(user_mapping="NFS_MAP_ALL", map_to_user={"id_type": "LOCAL_USER", "id_value": ""})])
    fake = fake_api()
    expect_found(fake, orphaned, etag='"4"')
    fake.expect("PATCH", f"{EXPORTS}2", body={"description": "x", "restrictions": [restriction()]}, response=export())
    expect_found(fake, export(description="x", restrictions=[restriction()]))

    result = run(run_module, export_path="/projects", description="x", restrictions=[{}])

    assert result["changed"] is True


@pytest.mark.parametrize("missing", ["fs_path", "restrictions"])
def test_create_without_fs_path_or_restrictions_fails(fake_api, run_module, missing):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": []})

    result = run(run_module, **{k: v for k, v in DESIRED.items() if k != missing})

    assert result["failed"] is True
    assert result["msg"] == "NFS export '/projects' does not exist; set fs_path and restrictions to create it"


def test_create_fs_path_is_sent_as_the_query_parameter(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": []})
    fake.expect("POST", f"{EXPORTS}?allow-fs-path-create=true", response=export())
    expect_found(fake, export())

    result = run(run_module, **DESIRED, create_fs_path=True)

    assert result["changed"] is True


def test_state_absent_deletes(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export(), etag='"4"')
    fake.expect("DELETE", f"{EXPORTS}2", if_match='"4"')

    result = run(run_module, export_path="/projects", state="absent")

    assert result["changed"] is True
    assert result["nfs_export"] == {}


def test_state_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("1", "/")]})

    result = run(run_module, export_path="/projects", state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


# Tenants by name (spec 014, FR-017, FR-031)


def test_tenant_by_name_chooses_the_export_of_that_tenant(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export("4", tenant_id=2), others=[export("2", tenant_id=1)])

    result = run(run_module, **DESIRED, tenant="eng")

    assert result["changed"] is False, result.get("msg")
    assert (result["nfs_export"]["tenant"], result["nfs_export"]["tenant_id"]) == ("eng", 2)


def test_tenants_reads_the_list_once_and_keys_the_result_by_name(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("2", tenant_id=1), export("4", tenant_id=2)]})
    fake.expect("GET", f"{EXPORTS}2", response=export("2", tenant_id=1), etag='"1"')
    fake.expect("GET", f"{EXPORTS}4", response=export("4", tenant_id=2), etag='"2"')

    result = run(run_module, diff=True, **DESIRED, tenants=["Default", "eng"])

    assert result["changed"] is False, result.get("msg")
    assert {name: e["tenant_id"] for name, e in result["nfs_export"].items()} == {"Default": 1, "eng": 2}
    assert sorted(result["diff"]["before"]) == ["Default", "eng"]


def test_an_export_in_a_tenant_an_earlier_task_creates_is_pending_in_check_mode(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("2", tenant_id=1)]})

    result = run(run_module, check_mode=True, **DESIRED, tenant="finance")

    assert result["changed"] is True, result.get("msg")
    assert (result["nfs_export"]["tenant"], result["nfs_export"]["tenant_id"]) == ("finance", None)
    assert result["notes"] == ["tenant 'finance' does not exist yet; an earlier task is expected to create it"]
    assert writes(fake) == []


def test_tenants_refuses_before_any_write_a_tenant_that_lacks_the_export_without_the_options_to_create_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", EXPORTS, response={"entries": [export("4", tenant_id=2, description="old")]})

    result = run(run_module, export_path="/projects", description="", tenants=["eng", "Default"])

    assert result["failed"] is True
    assert result.get("changed") is not True
    assert result["msg"] == "tenant 'Default': NFS export '/projects' does not exist; set fs_path and restrictions to create it"
    assert writes(fake) == []


def test_tenant_and_tenant_id_together_are_accepted_when_they_agree_and_refused_when_they_differ(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, export("4", tenant_id=2), others=[export("2", tenant_id=1)])

    agreed = run(run_module, **DESIRED, tenant="eng", tenant_id=2)
    differ = run(run_module, **DESIRED, tenant="eng", tenant_id=1)

    assert agreed["changed"] is False, agreed.get("msg")
    assert differ["msg"] == "tenant 'eng' and tenant_id 1, which is tenant 'Default', differ; give tenant alone"
    assert writes(fake) == []


@pytest.mark.parametrize("status", [403, 404])
def test_a_run_without_tenant_names_no_tenant_where_the_tenant_list_cannot_be_read(fake_api, run_module, status):
    """A user without PRIVILEGE_TENANT_READ (403), or a release without multitenancy (404), still manages its exports."""
    fake = fake_api()
    fake.serve(TENANTS, {"error_class": "api_access_denied_error", "description": "test"}, status=status)
    expect_found(fake, export())

    result = run(run_module, **DESIRED)

    assert result["changed"] is False, result.get("msg")
    assert result["nfs_export"] == {**OPTIONS, "tenant": None}
    assert not result.get("warnings")


def test_a_tenant_id_alone_that_no_tenant_holds_is_refused_before_the_export_is_read(fake_api, run_module):
    fake = fake_api()

    result = run(run_module, **DESIRED, tenant_id=7)

    assert result["msg"] == "tenant_id 7 is no tenant on the cluster; name its tenant with tenant"
    assert [e for e in fake.log if e.path == EXPORTS] == []
