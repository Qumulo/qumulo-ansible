# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.replication_failover_plan: the module around replication_failover.plan(), which
tests/unit/plugins/module_utils/test_replication_failover.py drives through every state; here, its options, its
answer, its warnings and its refusal."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import replication_failover_plan
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.test_replication_failover import B_ADDRESS, declared, params

TENANTS = {
    "tenants": [{"id": 1, "name": "Default", "replication_enabled": True}, {"id": 2, "name": "finance", "replication_enabled": True}],
    "document": {"frontend_networks": [], "managed_interfaces": []},
}


def args(action, records, **changes):
    given = params(action, records, **changes)
    return {key: value for key, value in given.items() if value is not None}


def test_the_module_answers_the_plan_and_changes_nothing(run_module):
    result = run_module(replication_failover_plan, args("failover", declared("ESTABLISHED", "ESTABLISHED")))

    assert result["changed"] is False, result.get("msg")
    assert result["state"] == "replicating"
    assert [step["name"] for step in result["steps"]] == ["make_writable_target"]
    assert result["step"]["verb"] == "make_writable"
    assert "warnings" not in result or result["warnings"] == []


def test_the_module_answers_in_check_mode_as_in_a_real_run(run_module):
    real = run_module(replication_failover_plan, args("failover", declared("ESTABLISHED", "ESTABLISHED")))
    check = run_module(replication_failover_plan, args("failover", declared("ESTABLISHED", "ESTABLISHED")), check_mode=True)

    assert check["steps"] == real["steps"]


def test_the_tenant_rule_s_warning_is_the_module_s(run_module):
    result = run_module(replication_failover_plan, args("failback", declared("DISCONNECTED", "DISCONNECTED"), target_tenancy=TENANTS, source_tenancy=TENANTS))

    assert result["changed"] is False, result.get("msg")
    assert any(f"connects to {B_ADDRESS}, which no network's ranges on this cluster hold" in warning for warning in result["warnings"])


def test_a_refusal_fails_the_module_with_its_message(run_module):
    result = run_module(replication_failover_plan, args("resync", declared("ESTABLISHED", "ESTABLISHED")))

    assert result["failed"] is True
    assert result["msg"].endswith("which resync does not start from; run failover")
