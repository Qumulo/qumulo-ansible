# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.identity_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import identity_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SID = "S-1-5-21-1534280521-3995163514-3722741250-"
GROUPS = [{"id": "513", "name": "Users", "sid": SID + "513", "gid": ""}, {"id": "1000", "name": "edge-admins", "sid": SID + "1000", "gid": "5000"}]
ADMIN = {"id": "500", "name": "admin", "primary_group": "513", "sid": SID + "500", "uid": "0", "home_directory": None, "can_change_password": True}
BACKUP = {"id": "1001", "name": "svc-backup", "primary_group": "1000", "sid": SID + "1001", "uid": "5001", "home_directory": "", "can_change_password": True}
AD_GROUP = {"domain": "ACTIVE_DIRECTORY", "auth_id": "12884902889", "uid": None, "gid": None, "sid": "S-1-5-21-9-9-9-1110", "name": "CORP\\Qumulo-Admins"}
LOCAL_ADMIN = {"domain": "LOCAL", "auth_id": "500", "uid": None, "gid": None, "sid": SID + "500", "name": "admin"}
NOT_JOINED = {
    "status": "NOT_IN_DOMAIN",
    "domain": "",
    "ou": "",
    "search_trusted_domains": False,
    "use_ad_posix_attributes": False,
    "base_dn": "",
    "domain_netbios": "",
    "dcs": [],
}
JOINED = {
    "status": "JOINED_TO_DOMAIN",
    "domain": "corp.example.com",
    "ou": "OU=Edges,DC=corp,DC=example,DC=com",
    "search_trusted_domains": False,
    "use_ad_posix_attributes": False,
    "base_dn": "CN=Users,DC=corp,DC=example,DC=com",
    "domain_netbios": "CORP",
    "dcs": [{"name": "dc1", "address": "10.0.0.2"}],
}
EMPTY_ERROR = {"module": "", "error_class": "", "description": "", "stack": "", "user_visible": False}


def monitor(domain="", dcs=(), status="LEAVE_SUCCEEDED"):
    return {"status": status, "last_error": EMPTY_ERROR, "domain": domain, "configured_dcs": list(dcs), "last_action_time": "2026-10-30T00:00:00Z"}


def test_local_users_and_groups_have_the_shape_their_modules_take(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/users/", response=[BACKUP, ADMIN])
    fake.expect("GET", "/v1/groups/", response=GROUPS)
    fake.expect("GET", "/v1/groups/", response=GROUPS)
    fake.expect("GET", "/v1/groups/513/members/", response=[ADMIN])
    fake.expect("GET", "/v1/groups/1000/members/", response=[BACKUP])

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["local_users", "local_groups"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["identity"] == {
        "local_users": [
            {"name": "admin", "primary_group": "Users", "uid": 0, "home_directory": None},
            {"name": "svc-backup", "primary_group": "edge-admins", "uid": 5001, "home_directory": None},
        ],
        "local_groups": [{"name": "Users", "gid": None, "members": ["admin"]}, {"name": "edge-admins", "gid": 5000, "members": ["svc-backup"]}],
    }


def test_role_members_are_trustee_strings_per_role(fake_api, run_module):
    fake = fake_api()
    fake.expect(
        "GET", "/v1/auth/roles/", response={"Observers": {"description": "", "privileges": []}, "Administrators": {"description": "", "privileges": []}}
    )
    fake.expect(
        "GET",
        "/v1/auth/roles/Administrators/members?limit=1000",
        response={"members": {"500": LOCAL_ADMIN, AD_GROUP["auth_id"]: AD_GROUP}, "paging": {"next": None}},
    )
    fake.expect("GET", "/v1/auth/roles/Observers/members?limit=1000", response={"members": {}, "paging": {"next": None}})

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["role_members"]})

    assert result["identity"]["role_members"] == [
        {"role": "Administrators", "members": ["ad:CORP\\Qumulo-Admins", "local:admin"]},
        {"role": "Observers", "members": []},
    ]


def test_the_qumulo_support_member_is_a_trustee_by_its_name(fake_api, run_module):
    support = {"domain": "API_QUMULO_SUPPORT_DOMAIN", "auth_id": "18446744056529682432", "uid": None, "gid": None, "sid": None}
    support["name"] = "qumulosupport:everyone"
    fake = fake_api()
    fake.expect("GET", "/v1/auth/roles/", response={"Qumulo-Support": {"description": "", "privileges": []}})
    fake.expect("GET", "/v1/auth/roles/Qumulo-Support/members?limit=1000", response={"members": {support["auth_id"]: support}, "paging": {"next": None}})

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["role_members"]})

    assert result["identity"]["role_members"] == [{"role": "Qumulo-Support", "members": ["qumulo_support:qumulosupport:everyone"]}]


def test_ad_when_not_joined(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/ad/status", response=NOT_JOINED)
    fake.expect("GET", "/v1/ad/monitor", response=monitor())

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["ad"]})

    assert result["identity"]["ad"] == {
        "status": "NOT_IN_DOMAIN",
        "domain": None,
        "domain_netbios": None,
        "ou": None,
        "search_trusted_domains": False,
        "use_ad_posix_attributes": False,
        "base_dn": None,
        "domain_controllers": None,
    }


def test_ad_domain_controllers_come_from_the_monitor_of_the_joined_domain(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/ad/status", response=JOINED)
    fake.expect("GET", "/v1/ad/monitor", response=monitor("CORP.EXAMPLE.COM", ["10.0.0.2", "10.0.0.3"], "JOIN_SUCCEEDED"))

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["ad"]})

    ad = result["identity"]["ad"]
    assert ad["domain"] == "corp.example.com"
    assert ad["domain_netbios"] == "CORP"
    assert ad["domain_controllers"] == ["10.0.0.2", "10.0.0.3"]


def test_ad_domain_controllers_are_unknown_when_the_monitor_describes_another_domain(fake_api, run_module):
    """The monitor keeps only the last operation, so its DCs say nothing about a domain joined before it (G-AD-DCS)."""
    fake = fake_api()
    fake.expect("GET", "/v1/ad/status", response=JOINED)
    fake.expect("GET", "/v1/ad/monitor", response=monitor("other.example.com", ["10.9.9.9"], "JOIN_SUCCEEDED"))

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["ad"]})

    assert result["identity"]["ad"]["domain_controllers"] is None


def test_ldap_settings_never_carry_a_password(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v2/ldap/settings", response={"use_ldap": True, "bind_uri": "ldap://ldap.example.com", "user": "cn=bind", "password": "hunter2"})

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["ldap"]})

    assert result["identity"]["ldap"] == {"use_ldap": True, "bind_uri": "ldap://ldap.example.com", "user": "cn=bind"}


def test_kerberos_settings(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/auth/kerberos-settings", response={"use_alt_security_identities_mapping": False})

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["kerberos"]})

    assert result["identity"]["kerberos"] == {"use_alt_security_identities_mapping": False}


def test_custom_roles_have_the_shape_role_takes_without_the_built_in_ones(fake_api, run_module):
    fake = fake_api()
    fake.expect(
        "GET",
        "/v1/auth/roles/",
        response={
            "Observers": {"description": "Read-only", "privileges": ["PRIVILEGE_QUOTA_READ"]},
            "Zone-Operators": {"description": "Zones", "privileges": []},
            "Backup-Operators": {"description": "Backups", "privileges": ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_QUOTA_READ"]},
        },
    )

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["roles"]})

    assert result["identity"]["roles"] == [
        {"name": "Backup-Operators", "description": "Backups", "privileges": ["PRIVILEGE_QUOTA_READ", "PRIVILEGE_SNAPSHOT_READ"]},
        {"name": "Zone-Operators", "description": "Zones", "privileges": []},
    ]


def test_user_defined_mappings_have_the_shape_their_module_takes(fake_api, run_module):
    alice = {"down_level_logon_name": "CORP\\alice", "ldap_name": "alice"}
    fake = fake_api()
    fake.expect("GET", "/v1/auth/user-defined-mappings/", response=[alice])

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["user_defined_mappings"]})

    assert result["identity"]["user_defined_mappings"] == {"mappings": [alice]}


def test_saml_settings_with_the_certificate_as_canonical_pem(fake_api, run_module):
    certificate = "-----BEGIN CERTIFICATE-----\nAAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=\n-----END CERTIFICATE-----\n"
    saml = {
        "enabled": True,
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_certificate": certificate.replace("\n", "\r\n"),
        "idp_entity_id": "https://idp.example.com/metadata",
        "cluster_dns_name": "qumulo.example.com",
        "require_sso": False,
    }
    fake = fake_api()
    fake.expect("GET", "/v1/saml/settings", response=saml)

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["saml"]})

    assert result["identity"]["saml"] == {**saml, "idp_certificate": certificate}


def test_all_gathers_every_subset_in_order(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/users/", response=[ADMIN])
    fake.expect("GET", "/v1/groups/", response=GROUPS[:1])
    fake.expect("GET", "/v1/groups/", response=GROUPS[:1])
    fake.expect("GET", "/v1/groups/513/members/", response=[ADMIN])
    fake.expect("GET", "/v1/auth/roles/", response={"Administrators": {"description": "", "privileges": []}})
    fake.expect("GET", "/v1/auth/roles/Administrators/members?limit=1000", response={"members": {"500": LOCAL_ADMIN}, "paging": {"next": None}})
    fake.expect("GET", "/v1/ad/status", response=NOT_JOINED)
    fake.expect("GET", "/v1/ad/monitor", response=monitor())
    fake.expect("GET", "/v2/ldap/settings", response={"use_ldap": False})
    fake.expect("GET", "/v1/auth/kerberos-settings", response={"use_alt_security_identities_mapping": False})
    fake.expect("GET", "/v1/auth/roles/", response={"Administrators": {"description": "", "privileges": []}})
    fake.expect("GET", "/v1/auth/user-defined-mappings/", response=[])
    fake.expect("GET", "/v1/saml/settings", response={"enabled": False, "idp_certificate": ""})
    fake.expect("GET", "/v1/auth/access-tokens/?limit=1000", response={"entries": [], "paging": {"next": None}})

    result = run_module(identity_info, CONNECTION)

    assert list(result["identity"]) == [
        "local_users",
        "local_groups",
        "role_members",
        "ad",
        "ldap",
        "kerberos",
        "roles",
        "user_defined_mappings",
        "saml",
        "access_tokens",
    ]


def test_access_tokens_are_listed_by_user_without_a_bearer(fake_api, run_module):
    fake = fake_api()
    token = {"id": "1000000000000000000001", "user": LOCAL_ADMIN, "creator": LOCAL_ADMIN, "creation_time": "2026-06-01T09:30:00.5Z"}
    fake.expect(
        "GET",
        "/v1/auth/access-tokens/?limit=1000",
        response={"entries": [{**token, "expiration_time": None, "enabled": True}], "paging": {"next": None}},
    )

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["access_tokens"]})

    assert result["identity"] == {
        "access_tokens": [
            {
                "user": "local:admin",
                "id": "1000000000000000000001",
                "creator": "local:admin",
                "creation_time": "2026-06-01T09:30:00.5Z",
                "expiration_time": None,
                "enabled": True,
            }
        ]
    }


# The lookups. LOCAL_ADMIN is local:admin as the find answers it; the AD reads answer as T2001p saw them.
FIND = "/v1/identity/find"
EXPAND = "/v1/identity/expand"
UID_2001 = {"domain": "POSIX_USER", "auth_id": "12884903889", "uid": 2001, "gid": None, "sid": "S-1-5-88-1-2001", "name": None}
ROOT_UID = {"domain": "POSIX_USER", "auth_id": "12884901888", "uid": 0, "gid": None, "sid": "S-1-5-88-1-0", "name": None}
USERS_GROUP = {"domain": "LOCAL", "auth_id": "513", "uid": None, "gid": None, "sid": SID + "513", "name": "Users"}
ADMIN_EXPANDED = {"id": LOCAL_ADMIN, "type": "USER", "smb_id": LOCAL_ADMIN, "nfs_id": ROOT_UID, "equivalent_ids": [ROOT_UID], "group_ids": [USERS_GROUP]}
UNKNOWN_DOMAIN = {"error_class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain S-1-5-21-9-9-9."}
AD_NOT_JOINED = {"error_class": "ad_domain_not_joined_error", "description": "The cluster has not joined any AD domains yet."}
ADMIN_FIELDS = {"trustee": "local:admin", "domain": "LOCAL", "name": "admin", "sid": SID + "500", "uid": None, "gid": None}


def with_identities(fake_api):
    fake = fake_api()
    fake.identities = [LOCAL_ADMIN, UID_2001, USERS_GROUP]
    return fake


def test_with_no_subsets_only_the_lookup_runs(fake_api, run_module):
    with_identities(fake_api)

    result = run_module(identity_info, {**CONNECTION, "gather_subset": [], "find_trustees": ["local:admin", "local:ghost", "uid:2001"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert list(result["identity"]) == ["found_trustees"]
    assert result["identity"]["found_trustees"]["local:admin"] == {"found": True, **ADMIN_FIELDS}
    assert result["identity"]["found_trustees"]["local:ghost"]["found"] is False
    assert result["identity"]["found_trustees"]["uid:2001"]["name"] is None
    assert result["warnings"] == []


def test_a_lookup_runs_beside_the_subsets_it_is_given(fake_api, run_module):
    fake = with_identities(fake_api)
    fake.expect("GET", "/v1/ad/status", response=NOT_JOINED)
    fake.expect("GET", "/v1/ad/monitor", response=monitor())

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["ad"], "find_trustees": ["local:admin"]})

    assert list(result["identity"]) == ["ad", "found_trustees"]


def test_the_three_lookups_run_in_one_task(fake_api, run_module):
    fake = with_identities(fake_api)
    fake.expect("POST", EXPAND, body={"id": LOCAL_ADMIN}, response=ADMIN_EXPANDED)
    fake.expect("GET", "/v1/ad/usernames/qjoin/objects/", status=409, response=AD_NOT_JOINED)

    result = run_module(
        identity_info,
        {**CONNECTION, "gather_subset": [], "find_trustees": ["local:admin"], "expand_trustees": ["local:admin"], "find_ad_objects": ["name:qjoin"]},
    )

    assert not result.get("failed"), result.get("msg")
    assert list(result["identity"]) == ["found_trustees", "expanded_trustees"]
    assert result["identity"]["expanded_trustees"]["local:admin"]["groups"] == [{**ADMIN_FIELDS, "trustee": "local:Users", "name": "Users", "sid": SID + "513"}]


def test_an_empty_lookup_runs_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/auth/kerberos-settings", response={"use_alt_security_identities_mapping": False})

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["kerberos"], "find_trustees": [], "expand_trustees": [], "find_ad_objects": []})

    assert list(result["identity"]) == ["kerberos"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"find_trustees": ["local:admin", "alice"]}, "find_trustees: trustee 'alice' needs a prefix"),
        ({"expand_trustees": ["CORP\\alice"]}, "expand_trustees: trustee 'CORP\\alice' needs a prefix"),
        ({"find_trustees": ["local:admin"], "find_ad_objects": ["CN=alice"]}, "find_ad_objects term 'CN=alice'"),
    ],
    ids=["a bare name beside the default subsets", "a bare name to expand", "a bad AD term beside a good trustee"],
)
def test_a_bad_term_fails_before_any_request_and_any_subset(fake_api, run_module, args, message):
    fake = fake_api()

    result = run_module(identity_info, {**CONNECTION, **args})

    assert result["failed"] is True
    assert message in result["msg"]
    assert fake.log == []


def test_ad_objects_outside_a_domain_are_unavailable_and_the_rest_is_gathered(fake_api, run_module):
    fake = with_identities(fake_api)
    fake.expect("GET", "/v1/auth/kerberos-settings", response={"use_alt_security_identities_mapping": False})
    fake.expect("GET", "/v1/ad/usernames/qjoin/objects/", status=409, response=AD_NOT_JOINED)

    result = run_module(
        identity_info,
        {**CONNECTION, "gather_subset": ["kerberos"], "find_trustees": ["local:admin"], "find_ad_objects": ["name:qjoin", "name:Domain Users"]},
    )

    assert not result.get("failed"), result.get("msg")
    assert result["unavailable_subsets"] == {"found_ad_objects": "The cluster has not joined any AD domains yet."}
    assert "found_ad_objects" not in result["identity"]
    assert list(result["identity"]) == ["kerberos", "found_trustees"]
    assert [e.path for e in fake.log if e.path.startswith("/v1/ad/")] == ["/v1/ad/usernames/qjoin/objects/"]


def test_the_terms_with_errors_are_warned_once_per_lookup_by_name(fake_api, run_module):
    fake = with_identities(fake_api)
    fake.find_errors.append(({"sid": "S-1-5-21-9-9-9-1104"}, 400, UNKNOWN_DOMAIN))
    fake.find_errors.append(({"sid": "S-1-5-21-9-9-9-1105"}, 400, UNKNOWN_DOMAIN))

    result = run_module(
        identity_info,
        {
            **CONNECTION,
            "gather_subset": [],
            "find_trustees": ["sid:S-1-5-21-9-9-9-1104", "local:admin", "sid:S-1-5-21-9-9-9-1105"],
            "expand_trustees": ["sid:S-1-5-21-9-9-9-1104"],
        },
    )

    assert not result.get("failed"), result.get("msg")
    assert result["identity"]["found_trustees"]["sid:S-1-5-21-9-9-9-1104"]["error"]["class"] == "api_identity_unsupported_domain_error"
    assert result["warnings"] == [
        "find_trustees: 2 terms had errors: 'sid:S-1-5-21-9-9-9-1104', 'sid:S-1-5-21-9-9-9-1105'",
        "expand_trustees: 1 term had an error: 'sid:S-1-5-21-9-9-9-1104'",
    ]


def test_check_mode_sends_the_same_requests_and_returns_the_same_result(fake_api, run_module):
    args = {
        **CONNECTION,
        "gather_subset": [],
        "find_trustees": ["local:admin", "local:ghost"],
        "expand_trustees": ["local:admin"],
        "find_ad_objects": ["name:qjoin"],
    }

    def script():
        fake = with_identities(fake_api)
        fake.expect("POST", EXPAND, body={"id": LOCAL_ADMIN}, response=ADMIN_EXPANDED)
        fake.expect("GET", "/v1/ad/usernames/qjoin/objects/", status=409, response=AD_NOT_JOINED)
        return fake

    first = script()
    run = run_module(identity_info, args)
    second = script()

    checked = run_module(identity_info, args, check_mode=True)

    assert checked == run
    assert checked["changed"] is False
    assert [(e.method, e.path, e.body) for e in second.log] == [(e.method, e.path, e.body) for e in first.log]


QJOIN = {
    "dn": "CN=alice,CN=Users,DC=corp,DC=example,DC=com",
    "sid": "S-1-5-21-9-9-9-1104",
    "uid": None,
    "gid": None,
    "name": "alice",
    "groups": [],
    "primary_group_sid": "S-1-5-21-9-9-9-513",
    "classes": ["top", "person", "organizationalPerson", "user"],
}


def test_the_ad_subset_and_the_ad_lookup_read_the_status_once(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/ad/status", response=JOINED)
    fake.expect("GET", "/v1/ad/monitor", response=monitor("corp.example.com", status="JOIN_SUCCEEDED"))
    fake.expect("GET", "/v1/ad/usernames/alice/objects/", response=[QJOIN])

    result = run_module(identity_info, {**CONNECTION, "gather_subset": ["ad"], "find_ad_objects": ["name:alice"]})

    assert not result.get("failed"), result.get("msg")
    assert result["identity"]["found_ad_objects"]["name:alice"]["objects"][0]["trustee"] == "ad:CORP\\alice"
    assert [e.path for e in fake.log if e.path == "/v1/ad/status"] == ["/v1/ad/status"]
