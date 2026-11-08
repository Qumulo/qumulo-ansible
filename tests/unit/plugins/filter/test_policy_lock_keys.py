# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.policy_lock_keys: what cluster_baseline's step 20 applies of the snapshot policies (spec 026, Decision 4).

A policy whose lock key is not in the key store is refused by default; with qumulo_allow_deferred_policy_lock_keys it is
applied without the key, and handed off.
"""

from __future__ import annotations

import pytest
from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.filter.policy_lock_keys import policy_lock_keys

LOCKED = {"policy_name": "projects-daily", "path": "/data/projects", "lock_key": "legal-hold"}
PLAIN = {"policy_name": "scratch-hourly", "path": "/scratch"}
CLEARED = {"policy_name": "archive-monthly", "path": "/archive", "lock_key": ""}
KEYS = [{"id": "7", "name": "legal-hold", "comment": "", "enabled": True, "public_key": "MFkwEwexample", "usages": []}]


@pytest.mark.parametrize("ref", ["legal-hold", "7"], ids=["name", "id"])
def test_a_key_in_the_store_by_name_or_id_leaves_the_policies_as_declared(ref):
    policies = [{**LOCKED, "lock_key": ref}, PLAIN, CLEARED]

    assert policy_lock_keys(policies, KEYS) == {"policies": policies, "hand_offs": [], "refusal": ""}


def test_a_missing_key_is_refused_by_default_naming_the_policy_the_key_and_both_remedies():
    plan = policy_lock_keys([LOCKED, PLAIN], [])

    assert plan["policies"] == [LOCKED, PLAIN]
    assert plan["hand_offs"] == []
    assert "projects-daily (key legal-hold)" in plan["refusal"]
    assert "qumulo.cluster.security_key" in plan["refusal"] and "-e qumulo_allow_deferred_policy_lock_keys=true" in plan["refusal"]


def test_a_key_store_that_could_not_be_read_is_refused_with_the_reason():
    plan = policy_lock_keys([LOCKED], None, unreadable="the API user may not read the key store: no")

    assert "the key store could not be read: the API user may not read the key store: no" in plan["refusal"]


def test_with_the_consent_a_missing_key_is_left_out_and_handed_off():
    plan = policy_lock_keys([LOCKED, PLAIN], [], allow_deferred=True)

    assert plan["refusal"] == ""
    assert plan["policies"] == [{"policy_name": "projects-daily", "path": "/data/projects"}, PLAIN]
    (hand_off,) = plan["hand_offs"]
    assert "projects-daily" in hand_off and "legal-hold" in hand_off and "run the baseline again" in hand_off


def test_no_policies_plan_nothing():
    assert policy_lock_keys(None, None) == {"policies": [], "hand_offs": [], "refusal": ""}


def test_an_entry_without_a_name_fails_naming_the_variable():
    with pytest.raises(AnsibleFilterError, match="qumulo_snapshot_policies"):
        policy_lock_keys([{"lock_key": "legal-hold"}], [])


def test_with_the_consent_a_key_store_that_could_not_be_read_hands_off_the_privilege():
    plan = policy_lock_keys([LOCKED], None, allow_deferred=True, unreadable="no privilege")

    (hand_off,) = plan["hand_offs"]
    assert "could not be read (no privilege)" in hand_off and "PRIVILEGE_FS_KEY_MANAGEMENT_READ" in hand_off


def test_an_absent_entry_s_stale_lock_key_is_not_missing():
    absent = {**LOCKED, "state": "absent"}

    assert policy_lock_keys([absent], []) == {"policies": [absent], "hand_offs": [], "refusal": ""}


@pytest.mark.parametrize(("given", "deferred"), [("false", False), ("no", False), ("true", True), ("yes", True)])
def test_the_consent_is_read_as_ansible_reads_a_boolean(given, deferred):
    """Python's bool() would take the string "false" for true."""
    plan = policy_lock_keys([LOCKED], [], allow_deferred=given)

    assert (plan["refusal"] == "") is deferred


def test_a_consent_that_is_not_a_boolean_fails_naming_it():
    with pytest.raises(AnsibleFilterError, match="qumulo_allow_deferred_policy_lock_keys"):
        policy_lock_keys([LOCKED], [], allow_deferred="maybe")
