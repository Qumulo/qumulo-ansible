# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The key store's helpers in security_keys.py, over FakeQumulo where they read the cluster."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import security_keys
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import client_params

KEY = {"id": "2", "name": "legal-hold", "comment": "holds", "disabled": True, "public_key": "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEexample"}


def client(fake_api):
    fake = fake_api()
    return fake, QumuloClient(client_params(api_token="t"))


def test_a_key_in_the_option_shape_says_enabled_where_the_cluster_says_disabled():
    assert security_keys.key_options(KEY) == {
        "id": "2",
        "name": "legal-hold",
        "comment": "holds",
        "enabled": False,
        "public_key": KEY["public_key"],
    }


def test_usages_are_read_page_by_page_and_sorted_by_type_and_id(fake_api):
    fake, qumulo = client(fake_api)
    fake.expect(
        "GET",
        "/v1/file-system/security/keys/2/usages?limit=1000",
        response={
            "usages": [{"type": "snapshot_policy", "id": "1"}, {"type": "snapshot", "id": "12"}],
            "paging": {"next": "/v1/file-system/security/keys/2/usages?after=7&limit=1000"},
        },
    )
    fake.expect(
        "GET", "/v1/file-system/security/keys/2/usages?after=7&limit=1000", response={"usages": [{"type": "snapshot", "id": "3"}], "paging": {"next": None}}
    )

    found = security_keys.usages(qumulo, "2")

    assert found == [{"type": "snapshot", "id": "3"}, {"type": "snapshot", "id": "12"}, {"type": "snapshot_policy", "id": "1"}]


def test_usages_are_described_by_type_and_id():
    assert security_keys.describe_usages([{"type": "snapshot", "id": "12"}, {"type": "replication_target", "id": "5"}]) == "snapshot 12, replication_target 5"


def test_a_key_list_the_user_may_not_read_is_an_unavailable_subset(fake_api):
    fake, qumulo = client(fake_api)
    fake.expect("GET", "/v1/file-system/security/keys/", status=403, response={"error_class": "http_forbidden_error", "description": "no privilege"})

    with pytest.raises(SubsetUnavailable, match="key store"):
        security_keys.key_facts(qumulo)


def test_another_error_on_the_key_list_is_not_hidden(fake_api):
    fake, qumulo = client(fake_api)
    fake.expect("GET", "/v1/file-system/security/keys/", status=400, response={"error_class": "bad", "description": "boom"})

    with pytest.raises(Exception, match="boom"):
        security_keys.key_facts(qumulo)


def test_a_refusal_names_ten_usages_and_counts_the_rest():
    entries = [{"type": "snapshot", "id": str(ident)} for ident in range(1, 14)]

    assert security_keys.describe_usages(entries) == ", ".join(f"snapshot {ident}" for ident in range(1, 11)) + " and 3 more"


def test_a_key_with_an_empty_name_has_its_usages_read_by_its_id(fake_api):
    """QUIRK-120: another client can store a key with an empty name, which no key_ref names."""
    fake, qumulo = client(fake_api)
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": [{**KEY, "id": "6", "name": ""}]})
    fake.expect("GET", "/v1/file-system/security/keys/6/usages?limit=1000", response={"usages": [], "paging": {"next": None}})

    facts = security_keys.key_facts(qumulo)

    assert [(key["id"], key["name"]) for key in facts["security_keys"]] == [("6", "")]


def test_a_key_deleted_while_the_keys_are_read_is_left_out(fake_api):
    fake, qumulo = client(fake_api)
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": [KEY, {**KEY, "id": "3", "name": "archive"}]})
    fake.expect(
        "GET",
        "/v1/file-system/security/keys/2/usages?limit=1000",
        status=404,
        response={"error_class": "fs_change_lock_key_store_no_such_key_error", "description": "gone"},
    )
    fake.expect("GET", "/v1/file-system/security/keys/3/usages?limit=1000", response={"usages": [], "paging": {"next": None}})

    facts = security_keys.key_facts(qumulo)

    assert [key["name"] for key in facts["security_keys"]] == ["archive"]


def test_usage_ids_that_are_not_numbers_sort_after_the_numbers_of_their_type(fake_api):
    """A replication target relationship's id is a UUID."""
    fake, qumulo = client(fake_api)
    target = "8a5cbf3b-3a6f-4a8e-9c35-0c1e2f9b7d41"
    fake.expect(
        "GET",
        "/v1/file-system/security/keys/2/usages?limit=1000",
        response={"usages": [{"type": "replication_target", "id": target}, {"type": "snapshot", "id": "3"}], "paging": {"next": None}},
    )

    assert security_keys.usages(qumulo, "2") == [{"type": "replication_target", "id": target}, {"type": "snapshot", "id": "3"}]


def test_a_usage_read_the_user_may_not_make_is_an_unavailable_subset(fake_api):
    fake, qumulo = client(fake_api)
    fake.expect("GET", "/v1/file-system/security/keys/", response={"entries": [KEY]})
    fake.expect("GET", "/v1/file-system/security/keys/2/usages?limit=1000", status=403, response={"error_class": "access_denied_error", "description": "no"})

    with pytest.raises(SubsetUnavailable, match="key store"):
        security_keys.key_facts(qumulo)
