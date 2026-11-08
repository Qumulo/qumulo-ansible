# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.user_defined_mappings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import user_defined_mappings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
MAPPINGS = "/v1/auth/user-defined-mappings/"
AD_STATUS = "/v1/ad/status"
AD_MONITOR = "/v1/ad/monitor"
ALICE = {"down_level_logon_name": "CORP\\alice", "ldap_name": "alice"}
BOB = {"down_level_logon_name": "CORP\\bob", "ldap_name": "robert"}
JOINED = {"status": "JOINED_TO_DOMAIN", "domain": "corp.example.com", "domain_netbios": "CORP", "use_ad_posix_attributes": False}
NOT_JOINED = {"status": "NOT_IN_DOMAIN", "domain": "", "use_ad_posix_attributes": False}


def ad(fake, status):
    fake.expect("GET", AD_STATUS, response=status)
    fake.expect("GET", AD_MONITOR, response={"domain": status["domain"], "configured_dcs": []})


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_in_another_order_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[ALICE, BOB], etag='"AQAAAA"')

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [BOB, ALICE]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["user_defined_mappings"] == {"mappings": [ALICE, BOB]}
    assert writes(fake) == []


def test_the_whole_list_is_put_with_if_match_in_the_order_given(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[ALICE], etag='"AQAAAA"')
    ad(fake, JOINED)
    fake.expect("PUT", MAPPINGS, body=[BOB, ALICE], if_match='"AQAAAA"', response=[BOB, ALICE])
    fake.expect("GET", MAPPINGS, response=[BOB, ALICE], etag='"AgAAAA"')

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [BOB, ALICE]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["user_defined_mappings"] == {"mappings": [BOB, ALICE]}


def test_an_empty_list_removes_every_mapping_without_reading_ad(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[ALICE, BOB], etag='"AQAAAA"')
    fake.expect("PUT", MAPPINGS, body=[], if_match='"AQAAAA"', response=[])
    fake.expect("GET", MAPPINGS, response=[], etag='"AAAAAA"')

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": []})

    assert result["changed"] is True, result.get("msg")
    assert result["user_defined_mappings"] == {"mappings": []}


def test_outside_a_domain_a_list_is_refused_before_the_put(fake_api, run_module):
    """QUIRK-031: outside a domain the cluster answers such a PUT with 500 ldap_dc_not_found_error, so it is never sent."""
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[], etag='"AAAAAA"')
    ad(fake, NOT_JOINED)

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [ALICE]})

    assert result["failed"] is True
    assert "not joined" in result["msg"] and "ad_domain" in result["msg"]
    assert writes(fake) == []


def test_outside_a_domain_check_mode_notes_the_join_it_expects(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[], etag='"AAAAAA"')
    ad(fake, NOT_JOINED)

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [ALICE]}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert any("not joined" in note for note in result["notes"]), result.get("notes")
    assert writes(fake) == []


def test_posix_attributes_from_ad_refuse_a_list(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[], etag='"AAAAAA"')
    ad(fake, {**JOINED, "use_ad_posix_attributes": True})

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [ALICE]})

    assert result["failed"] is True
    assert "use_ad_posix_attributes" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize(
    ("mappings", "problem"),
    [
        ([ALICE, {**ALICE, "ldap_name": "alice2"}], "more than once"),
        ([ALICE, {**ALICE, "down_level_logon_name": "corp\\ALICE"}], "more than once"),
        ([{"down_level_logon_name": "alice", "ldap_name": "alice"}], "DOMAIN\\name"),
        ([{"down_level_logon_name": "CORP\\", "ldap_name": "alice"}], "DOMAIN\\name"),
        ([{"down_level_logon_name": "\\alice", "ldap_name": "alice"}], "DOMAIN\\name"),
        ([{"down_level_logon_name": "CORP\\*", "ldap_name": "*"}, BOB], "only mapping"),
        ([{"down_level_logon_name": "CORP\\*", "ldap_name": "users"}], "ldap_name '*'"),
    ],
    ids=["a name twice", "a name twice in another case", "no domain", "no user", "an empty domain", "a wildcard beside another", "a wildcard to a name"],
)
def test_a_list_the_cluster_would_refuse_fails_before_any_request(fake_api, run_module, mappings, problem):
    fake = fake_api()

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": mappings})

    assert result["failed"] is True
    assert problem in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_wildcard_alone_is_accepted(fake_api, run_module):
    wildcard = {"down_level_logon_name": "CORP\\*", "ldap_name": "*"}
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[wildcard], etag='"AQAAAA"')

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [wildcard]})

    assert result["changed"] is False, result.get("msg")


def test_check_mode_and_diff_show_the_list_and_send_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[ALICE], etag='"AQAAAA"')
    ad(fake, JOINED)

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [ALICE, BOB]}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"] == {"before": {"mappings": [ALICE]}, "after": {"mappings": [ALICE, BOB]}}
    assert writes(fake) == []


def test_a_put_another_writer_made_needless_after_a_412_is_not_sent_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[], etag='"AAAAAA"')
    ad(fake, JOINED)
    fake.expect("PUT", MAPPINGS, body=[ALICE], if_match='"AAAAAA"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", MAPPINGS, response=[ALICE], etag='"AQAAAA"')
    fake.expect("GET", MAPPINGS, response=[ALICE], etag='"AQAAAA"')

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [ALICE]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert [e.method for e in writes(fake)] == ["PUT"]


def test_a_lost_put_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", MAPPINGS, response=[], etag='"AAAAAA"')
    ad(fake, JOINED)
    fake.expect("PUT", MAPPINGS, raises=TimeoutError("timed out"))
    fake.expect("GET", MAPPINGS, response=[ALICE], etag='"AQAAAA"')

    result = run_module(user_defined_mappings, {**CONNECTION, "mappings": [ALICE]})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]
