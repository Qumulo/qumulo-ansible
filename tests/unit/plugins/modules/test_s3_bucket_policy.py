# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.s3_bucket_policy, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import s3_bucket_policy
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import (
    ADMIN,
    EVERYONE,
    GUEST,
    UNSEEN_SID_DOMAIN,
    fake_with_identities,
)

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
BUCKETS = "/v1/s3/buckets/"
POLICY = "/v1/s3/buckets/photos/policy"
PHOTOS = {
    "name": "photos",
    "creation_time": "2026-11-01T00:00:00Z",
    "path": "/s3/photos",
    "anonymous_access_enabled": False,
    "versioning": "Unversioned",
    "lock_config": {"enabled": False, "default_retention": None},
}
DESIRED = {
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "read", "Effect": "Allow", "Principal": {"Qumulo": ["Everyone", "local:guest"]}, "Action": ["s3:ListBucket", "s3:GetObject"]},
        {"Sid": "admin", "Effect": "Allow", "Principal": {"Qumulo": ["local:admin"]}, "Action": "s3:*"},
    ],
}
# The same policy as the cluster reads it back: principals by its own names and sorted, actions sorted.
STORED = {
    "Version": "2012-10-17",
    "Statement": [
        {"Sid": "read", "Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"], "Principal": {"Qumulo": ["local:guest", "world:Everyone"]}},
        {"Sid": "admin", "Effect": "Allow", "Action": ["s3:*"], "Principal": {"Qumulo": ["local:admin"]}},
    ],
}
# What the module sends: each principal as the auth_id it resolves to.
SENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "read",
            "Effect": "Allow",
            "Principal": {"Qumulo": [f"auth_id:{EVERYONE['auth_id']}", f"auth_id:{GUEST['auth_id']}"]},
            "Action": ["s3:ListBucket", "s3:GetObject"],
        },
        {"Sid": "admin", "Effect": "Allow", "Principal": {"Qumulo": [f"auth_id:{ADMIN['auth_id']}"]}, "Action": "s3:*"},
    ],
}


def writes(fake):
    return [e for e in fake.log if e.method == "PUT"]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(s3_bucket_policy, {**CONNECTION, "bucket": "photos", **args}, check_mode=check_mode, diff=diff)


def expect_policy(fake, stored):
    fake.expect("GET", BUCKETS, response={"buckets": [PHOTOS]})
    fake.expect("GET", POLICY, response=stored)


def test_a_bucket_without_a_policy_gets_one_with_principals_sent_as_auth_ids(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, {})
    fake.expect("PUT", POLICY, body=SENT, if_match=NO_HEADER)
    fake.expect("GET", BUCKETS, response={"buckets": [PHOTOS]})
    fake.expect("GET", POLICY, response=STORED)

    result = run(run_module, policy=DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket_policy"] == {"bucket": "photos", "policy": STORED}


def test_the_same_policy_read_back_in_the_cluster_s_form_is_unchanged(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, STORED)

    result = run(
        run_module,
        policy={**DESIRED, "Statement": [{**DESIRED["Statement"][0], "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetObject"]}, DESIRED["Statement"][1]]},
    )

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["s3_bucket_policy"]["policy"] == STORED
    assert writes(fake) == []


def test_a_principal_the_cluster_names_by_another_form_is_the_same_identity(fake_api, run_module):
    stored = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["sid:S-1-5-88-1-1000"]}}]}
    fake = fake_with_identities(fake_api)
    expect_policy(fake, stored)

    result = run(run_module, policy={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": "uid:1000"}, "Action": "s3:GetObject"}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_another_principal_is_a_change(fake_api, run_module):
    stored = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["local:admin"]}}]}
    fake = fake_with_identities(fake_api)
    expect_policy(fake, stored)
    sent = {"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": [f"auth_id:{GUEST['auth_id']}"]}, "Action": ["s3:GetObject"]}]}
    fake.expect("PUT", POLICY, body=sent)
    fake.expect("GET", BUCKETS, response={"buckets": [PHOTOS]})
    fake.expect("GET", POLICY, response={"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["local:guest"]}}]})

    result = run(run_module, policy={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["local:guest"]}, "Action": ["s3:GetObject"]}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


ALICE = {"domain": "ACTIVE_DIRECTORY", "auth_id": "17179869185", "uid": None, "gid": None, "sid": "S-1-5-21-7-8-9-1105", "name": "CORP\\alice"}


@pytest.mark.parametrize("read_back", ["CORP\\alice", f"auth_id:{ALICE['auth_id']}"], ids=["a bare AD name", "an auth_id"])
def test_a_principal_the_cluster_reads_back_outside_the_grammar_is_found_by_identity(fake_api, run_module, read_back):
    """The cluster may name a principal in a form the trustee grammar refuses; the identity routes still find it, so the write converges."""
    stored = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": [read_back]}}]}
    fake = fake_with_identities(fake_api)
    fake.identities.append(ALICE)
    expect_policy(fake, {})
    fake.expect("PUT", POLICY, body={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": [f"auth_id:{ALICE['auth_id']}"]}, "Action": ["s3:GetObject"]}]})
    fake.expect("GET", BUCKETS, response={"buckets": [PHOTOS]})
    fake.expect("GET", POLICY, response=stored)

    result = run(run_module, policy={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["ad:CORP\\alice"]}, "Action": ["s3:GetObject"]}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket_policy"]["policy"] == stored


def test_authenticated_users_read_back_as_ad_without_a_domain_is_the_same_principal(fake_api, run_module):
    """7.11.0 reads a principal of Authenticated Users back as ad:Authenticated Users, which the grammar refuses."""
    stored = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["ad:Authenticated Users"]}}]}
    fake = fake_with_identities(fake_api)
    fake.find_errors.append(UNSEEN_SID_DOMAIN)
    expect_policy(fake, stored)

    result = run(run_module, policy={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["sid:S-1-5-11"]}, "Action": ["s3:GetObject"]}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_file_owner_read_back_as_its_bare_name_is_found_in_the_internal_domain(fake_api, run_module):
    """7.11.0 reads a principal of File Owner back as the bare name; the find is pinned to its domain, so an account of
    that name in a directory cannot answer it."""
    stored = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["File Owner"]}}]}
    fake = fake_with_identities(fake_api)
    expect_policy(fake, stored)

    result = run(run_module, policy={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["internal:file owner"]}, "Action": ["s3:GetObject"]}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    finds = [e.body for e in fake.log if e.path == "/v1/identity/find"]
    assert {"domain": "API_INTERNAL_DOMAIN", "name": "File Owner"} in finds
    assert {"name": "File Owner"} not in finds


def test_a_read_back_principal_no_identity_answers_to_fails_naming_it(fake_api, run_module):
    stored = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["CORP\\gone"]}}]}
    fake = fake_with_identities(fake_api)
    expect_policy(fake, stored)

    result = run(run_module, policy={"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["local:guest"]}, "Action": ["s3:GetObject"]}]})

    assert result["failed"] is True
    assert "CORP\\gone" in result["msg"]
    assert writes(fake) == []


def test_statement_order_is_part_of_the_policy(fake_api, run_module):
    swapped = {**STORED, "Statement": list(reversed(STORED["Statement"]))}
    fake = fake_with_identities(fake_api)
    expect_policy(fake, swapped)
    fake.expect("PUT", POLICY, body=SENT)
    fake.expect("GET", BUCKETS, response={"buckets": [PHOTOS]})
    fake.expect("GET", POLICY, response=STORED)

    result = run(run_module, policy=DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_allow_remove_self_is_sent_as_the_cluster_s_flag(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, {})
    fake.expect("PUT", f"{POLICY}?allow-remove-self=true", body=SENT)
    fake.expect("GET", BUCKETS, response={"buckets": [PHOTOS]})
    fake.expect("GET", POLICY, response=STORED)

    result = run(run_module, policy=DESIRED, allow_remove_self=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_state_absent_puts_the_empty_policy(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, STORED)
    fake.expect("PUT", POLICY, body={})

    result = run(run_module, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_bucket_policy"] == {}


def test_state_absent_without_a_policy_is_unchanged(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, {})

    result = run(run_module, state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


def test_state_absent_on_a_missing_bucket_is_unchanged(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", BUCKETS, response={"buckets": []})

    result = run(run_module, state="absent")

    assert result["changed"] is False
    assert writes(fake) == []


# QUIRK-051: the policy of a missing bucket is answered with the error class of a missing route, so the bucket is listed first.
def test_a_missing_bucket_fails_before_its_policy_is_read(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", BUCKETS, response={"buckets": []})

    result = run(run_module, policy=DESIRED)

    assert result["failed"] is True
    assert "S3 bucket 'photos' does not exist" in result["msg"]
    assert writes(fake) == []


def test_a_missing_bucket_in_check_mode_is_a_change_with_a_note(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", BUCKETS, response={"buckets": []})

    result = run(run_module, check_mode=True, policy=DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert any("does not exist yet" in note for note in result["notes"])


def test_a_principal_that_does_not_exist_yet_is_a_change_in_check_mode(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, STORED)

    result = run(run_module, check_mode=True, policy={**DESIRED, "Statement": [{**DESIRED["Statement"][0], "Principal": {"Qumulo": ["local:svc-new"]}}]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert writes(fake) == []


@pytest.mark.parametrize(
    ("policy", "says"),
    [
        ({"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["guest"]}, "Action": ["s3:GetObject"]}]}, "bare name"),
        ({"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": ["local:guest"]}, "Action": ["s3:GetObject"], "Resource": "*"}]}, "Resource"),
        ({"Statement": [{"Effect": "Maybe", "Principal": {"Qumulo": ["local:guest"]}, "Action": ["s3:GetObject"]}]}, "Effect"),
        ({"Statement": [{"Effect": "Allow", "Principal": "*", "Action": ["s3:GetObject"]}]}, "Qumulo"),
        ({"Statement": {"Effect": "Allow"}}, "Statement"),
        ({}, "Statement"),
    ],
)
def test_a_malformed_policy_fails_before_any_request(fake_api, run_module, policy, says):
    fake = fake_with_identities(fake_api)

    result = run(run_module, policy=policy)

    assert result["failed"] is True
    assert says in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_present_without_a_policy_fails_before_any_request(fake_api, run_module):
    fake = fake_with_identities(fake_api)

    result = run(run_module)

    assert result["failed"] is True
    assert "policy" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_check_mode_with_diff_shows_both_documents_and_sends_no_write(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    expect_policy(fake, {})

    result = run(run_module, check_mode=True, diff=True, policy=DESIRED)

    assert result["changed"] is True
    assert result["diff"] == {"before": {}, "after": {"bucket": "photos", "policy": DESIRED}}
    assert writes(fake) == []
