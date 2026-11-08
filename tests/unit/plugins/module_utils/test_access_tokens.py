# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""module_utils/access_tokens: the expiration form, and the identity_info subset."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import access_tokens
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.secret_files import secret_file_name
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import ADMIN, GUEST


@pytest.mark.parametrize(
    ("text", "instant"),
    [
        ("1970-01-01T00:00:00Z", (0, 0)),
        ("1970-01-01T00:00:01.5Z", (1, 500_000_000)),
        ("1970-01-01T01:00:00+01:00", (0, 0)),
        ("1969-12-31T16:00:00-08:00", (0, 0)),
        ("1970-01-01 00:00:00.000000001z", (0, 1)),
    ],
)
def test_parse_time_gives_the_instant_to_the_nanosecond(text, instant):
    assert access_tokens.parse_time(text) == instant


@pytest.mark.parametrize("text", ["1970-01-01T00:00:00", "1970-01-01", "1970-02-30T00:00:00Z", "1970-01-01T24:00:00Z", "1970-01-01T00:00:00+25:00", "never"])
def test_parse_time_refuses_anything_outside_rfc_3339_with_an_offset(text):
    with pytest.raises(QumuloError, match="RFC 3339"):
        access_tokens.parse_time(text)


def test_same_time_compares_never_with_null():
    assert access_tokens.same_time("never", None)
    assert not access_tokens.same_time("never", "2030-01-01T00:00:00Z")
    assert not access_tokens.same_time("2030-01-01T00:00:00Z", None)
    assert access_tokens.same_time("2030-01-01T00:00:00+00:00", "2030-01-01T00:00:00Z")


class Lister:
    """A client whose paginate returns the tokens given, recording the route and query."""

    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def paginate(self, route, items_key, *, params=None, query=None, limit=1000):
        self.calls.append((route.path, items_key, query))
        return list(self.entries)


def test_the_subset_lists_every_token_by_user_and_never_a_bearer():
    entries = [
        {"id": "2", "user": dict(GUEST), "creator": dict(ADMIN), "creation_time": "2026-11-02T00:00:00Z", "expiration_time": None, "enabled": True},
        {
            "id": "1",
            "user": dict(ADMIN),
            "creator": dict(ADMIN),
            "creation_time": "2026-11-03T00:00:00Z",
            "expiration_time": "2030-01-01T00:00:00Z",
            "enabled": False,
        },
        {"id": "3", "user": dict(GUEST), "creator": dict(ADMIN), "creation_time": "2026-11-01T00:00:00Z", "expiration_time": None, "enabled": True},
    ]
    lister = Lister(entries)

    facts = access_tokens.access_tokens_subset(lister)

    assert lister.calls == [("/v1/auth/access-tokens/", "entries", None)]
    assert [(entry["user"], entry["id"]) for entry in facts["access_tokens"]] == [("local:admin", "1"), ("local:guest", "3"), ("local:guest", "2")]
    assert facts["access_tokens"][0] == {
        "user": "local:admin",
        "id": "1",
        "creator": "local:admin",
        "creation_time": "2026-11-03T00:00:00Z",
        "expiration_time": "2030-01-01T00:00:00Z",
        "enabled": False,
    }
    assert "bearer" not in repr(facts)


def test_a_secret_file_s_name_replaces_each_run_of_other_characters_in_the_user_by_one_underscore():
    assert secret_file_name("access-token", "ad:CORP\\svc backup", "42") == "access-token-ad_CORP_svc_backup-42.yml"


def test_a_read_back_time_the_module_cannot_read_names_its_field_not_an_option():
    entries = [{"id": "1", "user": dict(GUEST), "creator": dict(ADMIN), "creation_time": "yesterday", "expiration_time": None, "enabled": True}]

    with pytest.raises(QumuloError, match="creation_time") as raised:
        access_tokens.access_tokens_subset(Lister(entries))

    assert "expiration_time" not in str(raised.value)


def test_a_read_back_expiration_the_module_cannot_read_is_blamed_on_the_cluster():
    with pytest.raises(QumuloError, match="the cluster's expiration_time") as raised:
        access_tokens.same_time("2030-01-01T00:00:00Z", "soon")

    assert "nor never" not in str(raised.value)
