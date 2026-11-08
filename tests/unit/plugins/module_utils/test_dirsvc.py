# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The Active Directory object lookup: its terms, its reads, and the trustee each object is given."""

from __future__ import annotations

from urllib.parse import quote

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import dirsvc
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import client_params

DOMAIN_SID = "S-1-5-21-2655871419-3825547956-2164736386"
# Objects as the AD object reads answer them, in the shape T2001p saw on the lane's Samba DC.
QJOIN = {
    "dn": "CN=qjoin,CN=Users,DC=qtest,DC=local",
    "sid": f"{DOMAIN_SID}-1103",
    "uid": None,
    "gid": None,
    "name": "qjoin",
    "groups": ["CN=Domain Admins,CN=Users,DC=qtest,DC=local"],
    "primary_group_sid": f"{DOMAIN_SID}-513",
    "classes": ["top", "organizationalPerson", "person", "user"],
}
DOMAIN_USERS = {
    "dn": "CN=Domain Users,CN=Users,DC=qtest,DC=local",
    "sid": f"{DOMAIN_SID}-513",
    "uid": None,
    "gid": None,
    "name": "Domain Users",
    "groups": ["CN=Users,CN=Builtin,DC=qtest,DC=local"],
    "primary_group_sid": None,
    "classes": ["top", "group"],
}
# An object of a trusted domain, which a dn: or sid: read can answer for.
TRUSTED = {**QJOIN, "dn": "CN=bob,CN=Users,DC=other,DC=example", "sid": "S-1-5-21-7-8-9-1105", "name": "bob"}
STATUS = {"status": "JOINED_TO_DOMAIN", "domain": "QTEST.LOCAL", "domain_netbios": "QTEST", "base_dn": "CN=Users,DC=QTEST,DC=LOCAL"}
NOT_JOINED = {"error_class": "ad_domain_not_joined_error", "description": "The cluster has not joined any AD domains yet."}


def by_name(name):
    return f"/v1/ad/usernames/{quote(name, safe='')}/objects/"


def by_dn(dn):
    return f"/v1/ad/distinguished-names/{quote(dn, safe='')}/object"


def by_sid(sid):
    return f"/v1/ad/sids/{sid}/object"


def entry(obj, trustee):
    return {**obj, "trustee": trustee}


def client():
    return QumuloClient(client_params(api_token="t"))


def reads(fake):
    return [e.path for e in fake.log if not e.background]


@pytest.mark.parametrize(
    ("term", "parsed"),
    [
        ("name:qjoin", ("name", "qjoin")),
        ("dn:CN=qjoin,CN=Users,DC=qtest,DC=local", ("dn", "CN=qjoin,CN=Users,DC=qtest,DC=local")),
        ("sid:s-1-5-21-1-2-3-1103", ("sid", "S-1-5-21-1-2-3-1103")),
        ("Name: qjoin ", ("name", "qjoin")),
    ],
    ids=["a name", "a DN", "a SID, upper-cased", "the prefix in any case, the value stripped"],
)
def test_parse_ad_term_gives_the_kind_and_the_value(term, parsed):
    assert dirsvc.parse_ad_term(term) == parsed


@pytest.mark.parametrize(
    "term",
    ["CN=alice,DC=corp", "qjoin", "upn:qjoin@qtest.local", "name:", "sid:alice", "sid:S-1-5", "dn:alice", "name:QTEST\\qjoin", "name:qjoin@qtest.local"],
    ids=[
        "a DN without its prefix",
        "no prefix",
        "an unknown prefix",
        "an empty value",
        "a sid: that is not a SID",
        "a SID without sub-authorities",
        "a dn: without =",
        "a name: with a domain",
        "a name: in UPN form",
    ],
)
def test_a_term_outside_the_grammar_is_refused_naming_the_forms(term):
    with pytest.raises(QumuloError, match=r"find_ad_objects term .*accepted forms: dn:<distinguished name>, sid:<SID> or name:<sAMAccountName>"):
        dirsvc.parse_ad_term(term)


def test_find_ad_objects_reads_each_kind_and_names_each_object_by_its_trustee(fake_api):
    fake = fake_api()
    fake.expect("GET", by_name("qjoin"), response=[QJOIN])
    fake.expect("GET", "/v1/ad/status", response=STATUS)
    fake.expect("GET", by_dn(DOMAIN_USERS["dn"]), response=DOMAIN_USERS)
    fake.expect("GET", by_sid(QJOIN["sid"]), response=QJOIN)

    found, errors = dirsvc.find_ad_objects(client(), ["name:qjoin", f"dn:{DOMAIN_USERS['dn']}", f"sid:{QJOIN['sid']}"])

    assert found == {
        "name:qjoin": {"found": True, "objects": [entry(QJOIN, "ad:QTEST\\qjoin")]},
        f"dn:{DOMAIN_USERS['dn']}": {"found": True, "objects": [entry(DOMAIN_USERS, "ad:QTEST\\Domain Users")]},
        f"sid:{QJOIN['sid']}": {"found": True, "objects": [entry(QJOIN, "ad:QTEST\\qjoin")]},
    }
    assert errors == []


def test_a_dn_is_sent_as_one_percent_encoded_segment(fake_api):
    fake = fake_api()
    fake.expect("GET", by_dn(DOMAIN_USERS["dn"]), response=DOMAIN_USERS)
    fake.expect("GET", "/v1/ad/status", response=STATUS)

    dirsvc.find_ad_objects(client(), [f"dn:{DOMAIN_USERS['dn']}"])

    assert reads(fake)[0] == "/v1/ad/distinguished-names/CN%3DDomain%20Users%2CCN%3DUsers%2CDC%3Dqtest%2CDC%3Dlocal/object"


def test_each_object_a_name_names_is_listed_once_by_its_sid(fake_api):
    fake = fake_api()
    group = {**DOMAIN_USERS, "name": "qjoin", "dn": "CN=qjoin,OU=Groups,DC=qtest,DC=local", "sid": f"{DOMAIN_SID}-1200"}
    fake.expect("GET", by_name("qjoin"), response=[QJOIN, group, QJOIN])
    fake.expect("GET", "/v1/ad/status", response=STATUS)

    found, errors = dirsvc.find_ad_objects(client(), ["name:qjoin"])

    assert errors == []
    assert [obj["sid"] for obj in found["name:qjoin"]["objects"]] == [QJOIN["sid"], group["sid"]]


def test_the_status_is_read_once_and_only_once_an_object_is_found(fake_api):
    fake = fake_api()
    fake.expect("GET", by_name("nosuch"), response=[])
    fake.expect("GET", by_name("qjoin"), response=[QJOIN])
    fake.expect("GET", "/v1/ad/status", response=STATUS)
    fake.expect("GET", by_sid(QJOIN["sid"]), response=QJOIN)

    dirsvc.find_ad_objects(client(), ["name:nosuch", "name:qjoin", f"sid:{QJOIN['sid']}"])

    assert reads(fake) == [by_name("nosuch"), by_name("qjoin"), "/v1/ad/status", by_sid(QJOIN["sid"])]


@pytest.mark.parametrize(
    ("status", "trustee"),
    [(STATUS, None), ({**STATUS, "domain_netbios": ""}, None)],
    ids=["an object of another domain", "a status with no NetBIOS name"],
)
def test_an_object_the_joined_domain_does_not_name_has_no_trustee(fake_api, status, trustee):
    fake = fake_api()
    obj = TRUSTED if status is STATUS else QJOIN
    fake.expect("GET", by_sid(obj["sid"]), response=obj)
    fake.expect("GET", "/v1/ad/status", response=status)

    found, errors = dirsvc.find_ad_objects(client(), [f"sid:{obj['sid']}"])

    assert errors == []
    assert found[f"sid:{obj['sid']}"]["objects"][0]["trustee"] is trustee


def test_a_dn_matches_the_domain_without_regard_to_case(fake_api):
    fake = fake_api()
    lower = {**QJOIN, "dn": "cn=qjoin,cn=users,dc=QTEST,dc=Local"}
    fake.expect("GET", by_sid(QJOIN["sid"]), response=lower)
    fake.expect("GET", "/v1/ad/status", response=STATUS)

    found, errors = dirsvc.find_ad_objects(client(), [f"sid:{QJOIN['sid']}"])

    assert errors == []
    assert found[f"sid:{QJOIN['sid']}"]["objects"][0]["trustee"] == "ad:QTEST\\qjoin"


@pytest.mark.parametrize(
    ("term", "path", "status", "response"),
    [
        ("name:nosuch", by_name("nosuch"), 200, []),
        (f"sid:{DOMAIN_SID}-99999", by_sid(f"{DOMAIN_SID}-99999"), 404, {"error_class": "ad_not_found_error", "description": "SID not found"}),
        ("dn:CN=nosuch,DC=qtest,DC=local", by_dn("CN=nosuch,DC=qtest,DC=local"), 404, {"error_class": "ad_not_found_error", "description": "DN not found"}),
    ],
    ids=["a name nothing carries", "a SID nothing carries", "a DN nothing has"],
)
def test_a_term_that_names_nothing_is_a_miss(fake_api, term, path, status, response):
    fake = fake_api()
    fake.expect("GET", path, status=status, response=response)

    found, errors = dirsvc.find_ad_objects(client(), [term])

    assert found == {term: {"found": False, "objects": []}}
    assert errors == []


def test_a_404_of_another_class_fails_naming_the_term(fake_api):
    fake = fake_api()
    fake.expect("GET", by_sid(QJOIN["sid"]), status=404, response={"error_class": "ad_domain_controller_not_found_error", "description": "No DCs found"})

    with pytest.raises(QumuloError, match=rf"^find_ad_objects term 'sid:{QJOIN['sid']}': .*ad_domain_controller_not_found_error") as raised:
        dirsvc.find_ad_objects(client(), [f"sid:{QJOIN['sid']}"])
    assert raised.value.details["term"] == f"sid:{QJOIN['sid']}"


def test_a_400_of_a_term_class_is_that_terms_error_and_the_other_terms_are_still_read(fake_api):
    fake = fake_api()
    fake.expect("GET", by_dn("=x,"), status=400, response={"error_class": "distinguished_name_decode_error", "description": "not a DN"})
    fake.expect("GET", by_name("qjoin"), response=[QJOIN])
    fake.expect("GET", "/v1/ad/status", response=STATUS)

    found, errors = dirsvc.find_ad_objects(client(), ["dn:=x,", "name:qjoin"])

    assert found["dn:=x,"] == {"found": False, "objects": [], "error": {"class": "distinguished_name_decode_error", "description": "not a DN"}}
    assert found["name:qjoin"]["found"] is True
    assert errors == ["dn:=x,"]


def test_a_cluster_outside_a_domain_raises_the_409_as_it_came_and_reads_no_more(fake_api):
    fake = fake_api()
    fake.expect("GET", by_name("qjoin"), status=409, response=NOT_JOINED)

    with pytest.raises(QumuloApiError) as raised:
        dirsvc.find_ad_objects(client(), ["name:qjoin", "name:Domain Users"])
    assert raised.value.error_class == "ad_domain_not_joined_error"
    assert reads(fake) == [by_name("qjoin")]


def test_a_term_outside_the_grammar_is_refused_before_any_read(fake_api):
    fake = fake_api()

    with pytest.raises(QumuloError, match="find_ad_objects term 'CN=alice'"):
        dirsvc.find_ad_objects(client(), ["name:qjoin", "CN=alice"])
    assert fake.log == []
