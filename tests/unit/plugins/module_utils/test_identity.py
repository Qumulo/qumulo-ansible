# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Trustee grammar, rendering, resolution through POST /v1/identity/find, and the identity lookups."""

from __future__ import annotations

import re

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import identity as identities_area
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import IdentityResolver, Pending, parse_trustee, render
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import client_params
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import AUTHENTICATED_USERS, UNSEEN_SID_DOMAIN

FIND = "/v1/identity/find"


def identity(domain, auth_id, name=None, sid=None, uid=None, gid=None):
    return {"domain": domain, "auth_id": str(auth_id), "name": name, "sid": sid, "uid": uid, "gid": gid}


ALICE = identity("LOCAL", 1001, name="alice", sid="S-1-5-21-111-222-333-1001")
BOB = identity("ACTIVE_DIRECTORY", 8589934600, name="CORP\\bob", sid="S-1-5-21-10-20-30-1105")
ERIN = identity("ACTIVE_DIRECTORY", 8589934602, name="erin@corp.example.com", sid="S-1-5-21-10-20-30-1107")
# An AD identity the server names without its domain: only its SID says which one it is.
DAVE = identity("ACTIVE_DIRECTORY", 8589934601, name="dave", sid="S-1-5-21-10-20-30-1106")
CAROL = identity("POSIX_USER", (3 << 32) + 2001, name="carol", sid="S-1-5-88-1-2001", uid=2001)
ENG = identity("POSIX_GROUP", (4 << 32) + 3001, name="eng", sid="S-1-5-88-2-3001", gid=3001)
UID_ONLY = identity("POSIX_USER", (3 << 32) + 4242, sid="S-1-5-88-1-4242", uid=4242)
GID_ONLY = identity("POSIX_GROUP", (4 << 32) + 4343, sid="S-1-5-88-2-4343", gid=4343)
EVERYONE = identity("WORLD", 0x200000000, name="Everyone", sid="S-1-1-0")
SYSTEM = identity("API_INTERNAL_DOMAIN", 1, name="SYSTEM", sid="S-1-5-18")
# The member every cluster registered with Nexus has in the Qumulo-Support role: a name, but no SID, uid or gid.
SUPPORT = identity("API_QUMULO_SUPPORT_DOMAIN", 18446744056529682432, name="qumulosupport:everyone")
# The two identities of the internal domain, as 7.11.0 returns them in an ACL: a name, but no SID, uid or gid.
FILE_OWNER = identity("API_INTERNAL_DOMAIN", 0xFFFFFFFE00000001, name="File Owner")
FILE_GROUP_OWNER = identity("API_INTERNAL_DOMAIN", 0xFFFFFFFE00000002, name="File Group Owner")
IDENTITIES = [ALICE, BOB, ERIN, DAVE, CAROL, ENG, UID_ONLY, GID_ONLY, EVERYONE, SYSTEM, SUPPORT, FILE_OWNER, FILE_GROUP_OWNER]


@pytest.fixture
def cluster(fake_api):
    fake = fake_api()
    fake.identities = [dict(i) for i in IDENTITIES]
    return fake


def finds(fake):
    return [e.body for e in fake.log if e.path == FIND]


def resolver(check_mode=False):
    return IdentityResolver(QumuloClient(client_params(api_token="t")), check_mode=check_mode)


@pytest.mark.parametrize(
    "spec",
    [
        "alice",
        "CORP\\alice",
        "alice@corp.example.com",
        "CN=Alice,OU=users,DC=example,DC=com",
        "name:alice",
        "unix:alice",
        "",
    ],
)
def test_bare_name_rejected(cluster, spec):
    with pytest.raises(QumuloError) as failure:
        resolver().resolve(spec)

    assert f"trustee '{spec}' needs a prefix" in str(failure.value)
    assert finds(cluster) == []


def test_bare_name_message_lists_the_accepted_forms():
    with pytest.raises(QumuloError) as failure:
        parse_trustee("alice")

    assert str(failure.value) == (
        "trustee 'alice' needs a prefix naming where it is defined; a bare name is ambiguous. Accepted forms: "
        "local:<name>, ad:<DOMAIN>\\<name>, ad:<name>@<domain>, ldap_user:<name>, ldap_group:<name>, "
        "qumulo_support:<name>, Everyone (or world:Everyone), internal:File Owner, internal:File Group Owner, uid:<n>, "
        "gid:<n>, sid:S-1-... or a bare SID"
    )


@pytest.mark.parametrize(
    "spec",
    [
        "local:",
        "ad:bob",
        "ad:CORP\\",
        "ad:@corp.example.com",
        "uid:abc",
        "uid:-1",
        "gid:",
        "sid:S-1",
        "sid:alice",
        "world:alice",
        "internal:Creator Owner",
        "internal:",
        "ldap_user: ",
        "qumulo_support:",
    ],
)
def test_malformed_spec_rejected(cluster, spec):
    with pytest.raises(QumuloError, match=re.escape(f"trustee '{spec}'")):
        resolver().resolve(spec)
    assert finds(cluster) == []


def test_the_internal_domain_names_its_two_identities_in_the_refusal():
    with pytest.raises(QumuloError, match=r"^trustee 'internal:Creator Owner': the internal domain holds only File Owner and File Group Owner$"):
        parse_trustee("internal:Creator Owner")


@pytest.mark.parametrize(
    ("ident", "canonical"),
    [
        (ALICE, "local:alice"),
        (BOB, "ad:CORP\\bob"),
        (ERIN, "ad:erin@corp.example.com"),
        (DAVE, "sid:S-1-5-21-10-20-30-1106"),
        (CAROL, "ldap_user:carol"),
        (ENG, "ldap_group:eng"),
        (UID_ONLY, "uid:4242"),
        (GID_ONLY, "gid:4343"),
        (EVERYONE, "world:Everyone"),
        (SYSTEM, "sid:S-1-5-18"),
        (SUPPORT, "qumulo_support:qumulosupport:everyone"),
        (FILE_OWNER, "internal:File Owner"),
        (FILE_GROUP_OWNER, "internal:File Group Owner"),
    ],
    ids=lambda value: value if isinstance(value, str) else value["domain"],
)
def test_grammar_round_trips_through_render(cluster, ident, canonical):
    assert render(ident) == canonical
    assert resolver().resolve(render(ident))["auth_id"] == ident["auth_id"]


@pytest.mark.parametrize("ident", [identity("API_INTERNAL_DOMAIN", 7), identity("API_INTERNAL_DOMAIN", 9, name="Nobody")], ids=["no name", "a name"])
def test_render_without_sid_or_posix_id_fails(ident):
    """The message holds for an identity with a name the grammar has no form for, too."""
    with pytest.raises(QumuloError, match="cannot be written as a trustee") as failure:
        render(ident)
    assert "has no name" not in str(failure.value)


@pytest.mark.parametrize(
    ("spec", "request_body", "found"),
    [
        ("local:alice", {"domain": "LOCAL", "name": "alice"}, ALICE),
        ("LOCAL:alice", {"domain": "LOCAL", "name": "alice"}, ALICE),
        ("ad:CORP\\bob", {"domain": "ACTIVE_DIRECTORY", "name": "CORP\\bob"}, BOB),
        ("ldap_user:carol", {"domain": "POSIX_USER", "name": "carol"}, CAROL),
        ("ldap_group:eng", {"domain": "POSIX_GROUP", "name": "eng"}, ENG),
        ("qumulo_support:qumulosupport:everyone", {"domain": "API_QUMULO_SUPPORT_DOMAIN", "name": "qumulosupport:everyone"}, SUPPORT),
        ("uid:2001", {"uid": 2001}, CAROL),
        ("gid:3001", {"gid": 3001}, ENG),
        ("sid:S-1-5-21-111-222-333-1001", {"sid": "S-1-5-21-111-222-333-1001"}, ALICE),
        ("S-1-5-21-111-222-333-1001", {"sid": "S-1-5-21-111-222-333-1001"}, ALICE),
        ("s-1-5-18", {"sid": "S-1-5-18"}, SYSTEM),
        ("Everyone", {"domain": "WORLD", "name": "Everyone"}, EVERYONE),
        ("everyone", {"domain": "WORLD", "name": "Everyone"}, EVERYONE),
        ("world:Everyone", {"domain": "WORLD", "name": "Everyone"}, EVERYONE),
        ("internal:File Owner", {"domain": "API_INTERNAL_DOMAIN", "name": "File Owner"}, FILE_OWNER),
        # The server matches these names in their own case only, so the grammar sends them so.
        ("INTERNAL:file group owner", {"domain": "API_INTERNAL_DOMAIN", "name": "File Group Owner"}, FILE_GROUP_OWNER),
        (" uid:4242 ", {"uid": 4242}, UID_ONLY),
    ],
)
def test_uid_gid_sid_and_world_forms(cluster, spec, request_body, found):
    assert parse_trustee(spec) == request_body
    assert resolver().resolve(spec)["auth_id"] == found["auth_id"]
    assert finds(cluster) == [request_body]


def test_ad_user_principal_name_is_sent_as_the_name():
    assert parse_trustee("ad:bob@corp.example.com") == {"domain": "ACTIVE_DIRECTORY", "name": "bob@corp.example.com"}


def test_resolver_caches_by_spec_and_auth_id(cluster):
    identities = resolver()

    alice = identities.resolve("local:alice")
    assert identities.resolve("local:alice") is alice
    assert identities.resolve({"auth_id": ALICE["auth_id"]}) is alice  # an identity read from the server, such as an ACL trustee
    assert identities.resolve("sid:S-1-5-21-111-222-333-1001") is alice  # another spec for an identity already seen
    carol = identities.resolve({"auth_id": CAROL["auth_id"], "domain": "POSIX_USER"})
    assert carol["name"] == "carol"
    assert identities.resolve("ldap_user:carol") is carol
    assert finds(cluster) == [
        {"domain": "LOCAL", "name": "alice"},
        {"sid": "S-1-5-21-111-222-333-1001"},
        {"auth_id": CAROL["auth_id"]},
        {"domain": "POSIX_USER", "name": "carol"},
    ]

    resolver().resolve("local:alice")  # a new module run resolves again: local auth_ids change when a cluster is rebuilt
    assert len(finds(cluster)) == 5


def test_resolve_all_keeps_order(cluster):
    found = resolver().resolve_all(["uid:2001", "Everyone", "local:alice"])
    assert [i["auth_id"] for i in found] == [CAROL["auth_id"], EVERYONE["auth_id"], ALICE["auth_id"]]


def test_same_compares_auth_ids(cluster):
    identities = resolver()

    assert identities.same("local:alice", "sid:S-1-5-21-111-222-333-1001")
    assert identities.same(ALICE, {"auth_id": ALICE["auth_id"], "name": "renamed"})
    assert not identities.same("local:alice", "uid:2001")
    assert not identities.same({"name": "alice"}, {"name": "alice"})  # no auth_id, no answer


def test_check_mode_unresolved_trustee_is_pending(cluster):
    identities = resolver(check_mode=True)

    pending = identities.resolve("local:ghost")

    assert isinstance(pending, Pending)
    assert (pending == "local:ghost (resolves after an earlier task creates or joins it)") is False
    assert str.__eq__(pending, "local:ghost (resolves after an earlier task creates or joins it)")
    itself = pending
    assert itself != pending
    assert len({pending}) == 1
    assert not identities.same(pending, pending)
    assert not identities.same(pending, "local:alice")
    assert [type(i) for i in identities.resolve_all(["local:alice", "local:ghost"])] == [dict, Pending]


def test_check_mode_unknown_auth_id_is_pending(cluster):
    assert isinstance(resolver(check_mode=True).resolve({"auth_id": "999"}), Pending)


def test_identity_without_auth_id_rejected(cluster):
    with pytest.raises(QumuloError, match="has no auth_id"):
        resolver().resolve({"name": "alice"})
    assert finds(cluster) == []


# The miss is the one class T2000p and T2001p saw for a name no source holds. Any other 404 is not, whatever its suffix:
# QUIRK-102's unreachable domain controller ends _not_found_error too.
@pytest.mark.parametrize(
    ("status", "error_class", "pending"),
    [
        (404, "api_identity_name_not_found_error", True),
        (404, "api_identity_sid_not_found_error", False),
        (404, "http_not_found_error", False),  # the route is missing: no trustee can resolve
        (400, "api_identity_name_not_found_error", False),
        # QUIRK-102: with no domain controller in reach, a name the node has not cached answers this 404.
        (404, "ad_domain_controller_not_found_error", False),
        (404, "ldap_dc_not_found_error", False),  # an unreachable LDAP server, a class the cluster has, unseen on the find
    ],
)
def test_check_mode_pending_needs_a_404_not_found_error_class(cluster, status, error_class, pending):
    cluster.expect("POST", FIND, status=status, response={"error_class": error_class, "description": "not found"})

    if pending:
        assert isinstance(resolver(check_mode=True).resolve("local:ghost"), Pending)
    else:
        with pytest.raises(QumuloError, match="trustee 'local:ghost'"):
            resolver(check_mode=True).resolve("local:ghost")


def test_not_found_outside_check_mode_fails_with_the_spec(cluster):
    with pytest.raises(QumuloError) as failure:
        resolver().resolve("ad:CORP\\ghost")

    assert "trustee 'ad:CORP\\ghost'" in str(failure.value)
    assert failure.value.details["trustee"] == "ad:CORP\\ghost"
    assert failure.value.details["status"] == 404


@pytest.mark.parametrize("check_mode", [False, True], ids=["real run", "check mode"])
def test_resolve_existing_is_none_for_a_trustee_that_does_not_exist(cluster, check_mode):
    assert resolver(check_mode=check_mode).resolve_existing("ad:CORP\\ghost") is None


def test_resolve_existing_finds_and_caches_an_identity(cluster):
    identities = resolver()

    first = identities.resolve_existing("local:alice")

    assert first["auth_id"] == identities.resolve("local:alice")["auth_id"]
    assert len(finds(cluster)) == 1


# QUIRK-102: an unreachable domain controller is not a missing trustee, so a task that only removes must not take it for one.
def test_resolve_existing_fails_when_no_domain_controller_is_in_reach(cluster):
    cluster.expect("POST", FIND, status=404, response={"error_class": "ad_domain_controller_not_found_error", "description": "No DCs found"})

    with pytest.raises(QumuloError, match=r"trustee 'ad:CORP\\ghost'.*ad_domain_controller_not_found_error"):
        resolver().resolve_existing("ad:CORP\\ghost")


def test_resolve_existing_still_fails_on_another_error(cluster):
    cluster.expect("POST", FIND, status=400, response={"error_class": "api_identity_mismatch_error", "description": "two"})

    with pytest.raises(QumuloError, match="trustee 'local:alice'"):
        resolver().resolve_existing("local:alice")


def test_find_is_retried_on_503(cluster):
    cluster.expect("POST", FIND, status=503)

    assert resolver().resolve("local:alice")["auth_id"] == ALICE["auth_id"]
    assert len(finds(cluster)) == 2


NOBODY = identity("API_INTERNAL_DOMAIN", 7)  # no name, SID, uid or gid to write a trustee from
NO_IDENTITY = {"trustee": None, "domain": None, "name": None, "sid": None, "uid": None, "gid": None}
UNKNOWN_DOMAIN = {"error_class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain S-1-5-21-9-9-9."}


def fields(ident, trustee):
    return {"trustee": trustee, **{key: ident[key] for key in ("domain", "name", "sid", "uid", "gid")}}


@pytest.mark.parametrize(
    ("ident", "trustee"),
    [
        (ALICE, "local:alice"),
        (BOB, "ad:CORP\\bob"),
        (DAVE, "sid:S-1-5-21-10-20-30-1106"),
        (CAROL, "ldap_user:carol"),
        (ENG, "ldap_group:eng"),
        (UID_ONLY, "uid:4242"),
        (GID_ONLY, "gid:4343"),
        (EVERYONE, "world:Everyone"),
        (SUPPORT, "qumulo_support:qumulosupport:everyone"),
        (AUTHENTICATED_USERS, "ad:NT AUTHORITY\\Authenticated Users"),
        (NOBODY, None),
    ],
    ids=lambda value: value if isinstance(value, str) or value is None else value["domain"],
)
def test_identity_fields_name_an_identity_by_trustee_and_never_by_auth_id(ident, trustee):
    assert identities_area.identity_fields(ident) == fields(ident, trustee)


@pytest.mark.parametrize("spelling", ["ad:NT AUTHORITY\\Authenticated Users", "ad:nt authority\\authenticated users", "sid:S-1-5-11", "s-1-5-11"])
def test_authenticated_users_resolves_by_name_on_a_node_that_refuses_its_sid(cluster, spelling):
    """QUIRK-100 refuses S-1-5-11 until the node has found a name in S-1-5; the name answers joined to a domain or not,
    so the SID's find is sent as the name's."""
    cluster.identities.append(dict(AUTHENTICATED_USERS))
    cluster.find_errors.append(UNSEEN_SID_DOMAIN)

    found = resolver()

    assert found.resolve(render(AUTHENTICATED_USERS)) == AUTHENTICATED_USERS
    assert found.resolve(spelling) == AUTHENTICATED_USERS
    assert {"sid": "S-1-5-11"} not in finds(cluster)


def test_ad_authenticated_users_without_a_domain_is_refused_naming_its_spelling():
    with pytest.raises(QumuloError, match=re.escape("Authenticated Users is ad:NT AUTHORITY\\Authenticated Users")):
        parse_trustee("ad:Authenticated Users")


def test_identity_fields_keep_a_null_identity_null():
    assert identities_area.identity_fields(None) is None


def lookup_client():
    return QumuloClient(client_params(api_token="t"))


@pytest.mark.parametrize("check_mode", [False, True], ids=["real run", "check mode"])
def test_find_existing_is_the_identity_or_none(cluster, check_mode):
    found = IdentityResolver(lookup_client(), check_mode=check_mode)

    assert found.find_existing("local:alice") == ALICE
    assert found.find_existing("local:ghost") is None


def test_find_existing_raises_the_servers_error_as_it_came(cluster):
    cluster.find_errors.append(({"sid": "S-1-5-21-9-9-9-1104"}, 400, UNKNOWN_DOMAIN))

    with pytest.raises(QumuloApiError) as raised:
        IdentityResolver(lookup_client()).find_existing("sid:S-1-5-21-9-9-9-1104")
    assert raised.value.error_class == "api_identity_unsupported_domain_error"


def test_find_trustees_keys_each_entry_by_the_term_as_given(cluster):
    found, errors = identities_area.find_trustees(lookup_client(), ["local:alice", "ad:CORP\\bob", "local:ghost", "uid:4242", "Everyone"])

    assert found == {
        "local:alice": {"found": True, **fields(ALICE, "local:alice")},
        "ad:CORP\\bob": {"found": True, **fields(BOB, "ad:CORP\\bob")},
        "local:ghost": {"found": False, **NO_IDENTITY},
        "uid:4242": {"found": True, **fields(UID_ONLY, "uid:4242")},
        "Everyone": {"found": True, **fields(EVERYONE, "world:Everyone")},
    }
    assert errors == []
    assert all("auth_id" not in entry for entry in found.values())


def test_a_number_nothing_carries_is_found_with_no_name(cluster):
    found, errors = identities_area.find_trustees(lookup_client(), ["uid:4242"])

    assert errors == []
    assert found["uid:4242"]["found"] is True
    assert found["uid:4242"]["name"] is None


def test_a_term_given_twice_is_one_key_and_one_find(cluster):
    found, errors = identities_area.find_trustees(lookup_client(), ["local:alice", "local:alice"])

    assert errors == []
    assert list(found) == ["local:alice"]
    assert len(finds(cluster)) == 1


def test_two_spellings_of_one_identity_are_two_keys_holding_it(cluster):
    # The server answers both spellings with the NetBIOS form, which the background find, matching on the name, would not.
    cluster.expect("POST", FIND, body={"domain": "ACTIVE_DIRECTORY", "name": "CORP\\bob"}, response=BOB)
    cluster.expect("POST", FIND, body={"domain": "ACTIVE_DIRECTORY", "name": "bob@corp.example.com"}, response=BOB)

    found, errors = identities_area.find_trustees(lookup_client(), ["ad:CORP\\bob", "ad:bob@corp.example.com"])

    assert errors == []
    assert found["ad:CORP\\bob"] == found["ad:bob@corp.example.com"]


@pytest.mark.parametrize("term", ["alice", "CORP\\alice", "ad:alice", "uid:x"])
def test_a_term_outside_the_grammar_is_refused_before_any_request(cluster, term):
    with pytest.raises(QumuloError, match=r"^find_trustees: trustee"):
        identities_area.find_trustees(lookup_client(), ["local:alice", term])
    assert cluster.log == []


# QUIRK-100: until the node has found a name in a SID's domain, the find of that SID answers 400 "Unknown SID domain".
def test_an_unseen_domains_sid_is_that_terms_error_and_the_other_terms_are_found(cluster):
    cluster.find_errors.append(({"sid": "S-1-5-21-9-9-9-1104"}, 400, UNKNOWN_DOMAIN))

    found, errors = identities_area.find_trustees(lookup_client(), ["sid:S-1-5-21-9-9-9-1104", "local:alice"])

    assert found["sid:S-1-5-21-9-9-9-1104"] == {
        "found": False,
        **NO_IDENTITY,
        "error": {"class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain S-1-5-21-9-9-9."},
    }
    assert found["local:alice"]["found"] is True
    assert errors == ["sid:S-1-5-21-9-9-9-1104"]


# A term the grammar accepts can still earn these 400s, each its own fault: a uid past 32 bits and an oversized SID part
# (observed on 7.11.0), and an ldap_user: name more than one LDAP entry carries or the LDAP search refuses (read from
# Qumulo Core's source, since no lane runs an LDAP server).
@pytest.mark.parametrize(
    ("term", "body", "error_class"),
    [
        ("uid:4294967296", {"uid": 4294967296}, "decode_error"),
        ("sid:S-1-5-21-99999999999-1-2-3", {"sid": "S-1-5-21-99999999999-1-2-3"}, "ms_sid_decode_error"),
        ("ldap_user:alice", {"domain": "POSIX_USER", "name": "alice"}, "api_identity_mismatch_error"),
        ("ldap_user:a*(b", {"domain": "POSIX_USER", "name": "a*(b"}, "api_identity_ldap_search_filter_error"),
    ],
    ids=["a uid past 32 bits", "an oversized SID part", "an ambiguous LDAP name", "an LDAP name the search refuses"],
)
def test_a_400_a_valid_term_can_earn_is_that_terms_error(cluster, term, body, error_class):
    cluster.find_errors.append((body, 400, {"error_class": error_class, "description": "test"}))

    found, errors = identities_area.find_trustees(lookup_client(), [term, "local:alice"])

    assert found[term]["error"] == {"class": error_class, "description": "test"}
    assert found["local:alice"]["found"] is True
    assert errors == [term]


@pytest.mark.parametrize(
    ("status", "error_class", "message"),
    [
        (400, "api_identity_error", "HTTP 400 api_identity_error"),
        (403, "access_denied_error", "the API user lacks the RBAC privilege"),
        (409, "identity_config_cannot_be_inferred_error", "HTTP 409"),
        (404, "ad_domain_controller_not_found_error", "HTTP 404 ad_domain_controller_not_found_error"),  # QUIRK-102
    ],
    ids=["a 400 of a class not recorded", "a 403", "more than one identity configuration", "no domain controller in reach"],
)
def test_any_other_error_fails_naming_the_term(cluster, status, error_class, message):
    cluster.find_errors.append(({"domain": "LOCAL", "name": "alice"}, status, {"error_class": error_class, "description": "test"}))

    with pytest.raises(QumuloError, match=rf"^find_trustees term 'local:alice': .*{message}") as raised:
        identities_area.find_trustees(lookup_client(), ["local:alice"])
    assert raised.value.details["term"] == "local:alice"


def test_a_find_that_cannot_reach_the_cluster_fails_naming_the_term(fake_api):
    fake = fake_api()
    fake.expect("POST", FIND, raises=ConnectionResetError("reset by peer"))
    unreachable = QumuloClient(client_params(api_token="t", retry_timeout=0))

    with pytest.raises(QumuloError, match=r"^find_trustees term 'local:alice': .*unreachable"):
        identities_area.find_trustees(unreachable, ["local:alice"])


EXPAND = "/v1/identity/expand"
ROOT_UID = identity("POSIX_USER", 12884901888, sid="S-1-5-88-1-0", uid=0)
USERS = identity("LOCAL", 513, name="Users", sid="S-1-5-21-111-222-333-513")
# POST /v1/identity/expand of a local user, as T2000p saw it for local:admin: every entry a whole identity, the groups
# in no fixed order.
ALICE_EXPANDED = {
    "id": ALICE,
    "type": "USER",
    "smb_id": ALICE,
    "nfs_id": ROOT_UID,
    "equivalent_ids": [ROOT_UID],
    "group_ids": [USERS, AUTHENTICATED_USERS],
}
NO_EXPANSION = {"found": False, "type": None, "identity": None, "nfs_identity": None, "smb_identity": None, "equivalents": [], "groups": []}


def expands(fake):
    return [e.body for e in fake.log if e.path == EXPAND]


def test_expand_trustees_finds_then_expands_the_found_identity_alone(cluster):
    cluster.expect("POST", EXPAND, body={"id": ALICE}, response=ALICE_EXPANDED)

    found, errors = identities_area.expand_trustees(lookup_client(), ["local:alice"])

    assert found == {
        "local:alice": {
            "found": True,
            "type": "USER",
            "identity": fields(ALICE, "local:alice"),
            "nfs_identity": fields(ROOT_UID, "uid:0"),
            "smb_identity": fields(ALICE, "local:alice"),
            "equivalents": [fields(ROOT_UID, "uid:0")],
            "groups": [fields(AUTHENTICATED_USERS, "ad:NT AUTHORITY\\Authenticated Users"), fields(USERS, "local:Users")],
        }
    }
    assert errors == []
    assert finds(cluster) == [{"domain": "LOCAL", "name": "alice"}]


def test_the_groups_come_in_one_order_whatever_order_the_server_gives(cluster):
    cluster.expect("POST", EXPAND, response=ALICE_EXPANDED)
    cluster.expect("POST", EXPAND, response={**ALICE_EXPANDED, "group_ids": [AUTHENTICATED_USERS, USERS]})

    first, first_errors = identities_area.expand_trustees(lookup_client(), ["local:alice"])
    second, second_errors = identities_area.expand_trustees(lookup_client(), ["local:alice"])

    assert first == second
    assert first_errors == second_errors == []


def test_an_identity_the_server_cannot_place_expands_with_no_nfs_identity(cluster):
    unknown = {**ALICE_EXPANDED, "id": BOB, "type": "UNKNOWN", "smb_id": BOB, "nfs_id": None, "equivalent_ids": [], "group_ids": [AUTHENTICATED_USERS]}
    cluster.expect("POST", EXPAND, body={"id": BOB}, response=unknown)

    found, errors = identities_area.expand_trustees(lookup_client(), ["ad:CORP\\bob"])

    assert errors == []
    assert found["ad:CORP\\bob"]["type"] == "UNKNOWN"
    assert found["ad:CORP\\bob"]["nfs_identity"] is None
    assert found["ad:CORP\\bob"]["equivalents"] == []


def test_a_trustee_not_found_is_a_miss_and_sends_no_expand(cluster):
    found, errors = identities_area.expand_trustees(lookup_client(), ["local:ghost"])

    assert found == {"local:ghost": NO_EXPANSION}
    assert errors == []
    assert expands(cluster) == []


def test_a_terms_find_error_is_its_error_and_sends_no_expand(cluster):
    cluster.find_errors.append(({"sid": "S-1-5-21-9-9-9-1104"}, 400, UNKNOWN_DOMAIN))

    found, errors = identities_area.expand_trustees(lookup_client(), ["sid:S-1-5-21-9-9-9-1104"])

    assert found["sid:S-1-5-21-9-9-9-1104"] == {
        **NO_EXPANSION,
        "error": {"class": "api_identity_unsupported_domain_error", "description": "Unknown SID domain S-1-5-21-9-9-9."},
    }
    assert errors == ["sid:S-1-5-21-9-9-9-1104"]
    assert expands(cluster) == []


def test_a_400_to_the_expand_fails_naming_the_term(cluster):
    cluster.expect("POST", EXPAND, status=400, response={"error_class": "api_identity_unsupported_domain_error", "description": "test"})

    with pytest.raises(QumuloError, match=r"^expand_trustees term 'local:alice': POST /v1/identity/expand failed: HTTP 400") as raised:
        identities_area.expand_trustees(lookup_client(), ["local:alice"])
    assert raised.value.details["term"] == "local:alice"


def test_an_expand_answered_503_is_sent_again(cluster):
    cluster.expect("POST", EXPAND, status=503, response={"error_class": "service_unavailable_error", "description": "busy"})
    cluster.expect("POST", EXPAND, response=ALICE_EXPANDED)

    found, errors = identities_area.expand_trustees(lookup_client(), ["local:alice"])

    assert errors == []
    assert found["local:alice"]["found"] is True


def test_a_term_outside_the_grammar_is_refused_before_any_expand(cluster):
    with pytest.raises(QumuloError, match=r"^expand_trustees: trustee 'alice'"):
        identities_area.expand_trustees(lookup_client(), ["local:alice", "alice"])
    assert cluster.log == []


# QUIRK-102: with no domain controller in reach, the find before the expand fails, and the term is not reported a miss.
def test_an_expand_whose_find_meets_no_domain_controller_fails_naming_the_term(cluster):
    no_dc = {"error_class": "ad_domain_controller_not_found_error", "description": "No DCs found"}
    cluster.find_errors.append(({"domain": "ACTIVE_DIRECTORY", "name": "CORP\\ghost"}, 404, no_dc))

    with pytest.raises(QumuloError, match=r"^expand_trustees term 'ad:CORP\\ghost': .*ad_domain_controller_not_found_error"):
        identities_area.expand_trustees(lookup_client(), ["ad:CORP\\ghost"])
    assert expands(cluster) == []
