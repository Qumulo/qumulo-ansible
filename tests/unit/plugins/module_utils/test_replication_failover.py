# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""replication_failover.plan(): the state, the steps left and the refusals, from both clusters' described records.

A, edge01, is the declared source of /projects; B, dr01, the declared target of /replicas/projects (specs/025).
"""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import replication
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.replication_failover import plan

A_UUID, B_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd", "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
A_ADDRESS, B_ADDRESS = "203.0.113.11", "203.0.113.10"
RID = "9465828e-7dcf-466e-a550-78ed0bd6b79b"
POINT = "2026-11-08T06:26:34.190479565Z"
ENTRY = {"source_path": "/projects", "target": "dr01", "target_path": "/replicas/projects", "blackout_windows": [], "replication_enabled": True}
ONE_TENANT = {"tenants": [{"id": 1, "name": "Default", "replication_enabled": True}], "document": {}}


def record(source_uuid, source_path, target_uuid, target_path, state, **changes):
    return {
        **dict.fromkeys(replication.SHAPE),
        "id": RID,
        "source_cluster_uuid": source_uuid,
        "target_cluster_uuid": target_uuid,
        "source_path": source_path,
        "target_path": target_path,
        "state": state,
        "job_state": "REPLICATION_NOT_RUNNING",
        "recovery_point": POINT,
        "replication_enabled": True,
        "blackout_windows": [],
        **changes,
    }


def declared(b_state, a_state, **a_changes):
    """B's target record and A's source record, in the declared direction."""
    b = {"source_relationships": [], "target_relationships": [record(A_UUID, "/projects", B_UUID, "/replicas/projects", b_state)]}
    a = {"source_relationships": [record(A_UUID, "/projects", B_UUID, "/replicas/projects", a_state, **a_changes)], "target_relationships": []}
    return a, b


def reversed_(b_state, a_state):
    """B's source record and A's target record, after a reverse."""
    b = {"source_relationships": [record(B_UUID, "/replicas/projects", A_UUID, "/projects", b_state)], "target_relationships": []}
    a = {"source_relationships": [], "target_relationships": [record(B_UUID, "/replicas/projects", A_UUID, "/projects", a_state)]}
    return a, b


def params(action, records, **changes):
    a, b = records
    return {
        "action": action,
        "relationship": ENTRY,
        "source_host": "edge01",
        "target_host": "dr01",
        "source_cluster_uuid": A_UUID,
        "target_cluster_uuid": B_UUID.upper(),
        "source_replication_address": A_ADDRESS,
        "source_replication_port": 3712,
        "target_replication_address": B_ADDRESS,
        "target_replication_port": 3712,
        "source_relationships": a,
        "source_error": None,
        "target_relationships": b,
        "source_tenancy": ONE_TENANT,
        "target_tenancy": ONE_TENANT,
        "source_previous_cluster_uuids": [],
        "last_verb": None,
        "allow_partial_job": False,
        "newer_writes_hosts": [],
        "synced": False,
        **changes,
    }


def names(result):
    return [step["name"] for step in result["steps"]]


# The state table and each action's steps (plan.md)

CUTOVER = ["await_sync", "make_writable_source", "reverse_to_target", "reconnect_target", "apply_settings"]


@pytest.mark.parametrize(
    ("action", "records", "last_verb", "state", "steps"),
    [
        ("failover", declared("ESTABLISHED", "ESTABLISHED"), None, "replicating", ["make_writable_target"]),
        ("failover", declared("DISCONNECTING", "ESTABLISHED"), None, "failing over", ["wait_target_writable"]),
        # B moved first; A's record lags (research F13).
        ("failover", declared("DISCONNECTED", "ESTABLISHED"), "make_writable_target", "failed over", []),
        ("failover", declared("DISCONNECTED", "DISCONNECTED"), None, "failed over", []),
        # A failover reads B's record alone: A lagging or ENDED does not hold it up.
        ("failover", declared("ESTABLISHED", "DISCONNECTED"), None, "replicating", ["make_writable_target"]),
        ("failover", declared("ESTABLISHED", "ENDED"), None, "replicating", ["make_writable_target"]),
        ("resync", declared("DISCONNECTED", "ESTABLISHED"), None, "failed over", ["wait_source_disconnected", "reverse_to_source", "reconnect_source"]),
        ("resync", declared("DISCONNECTED", "DISCONNECTED"), None, "failed over", ["reverse_to_source", "reconnect_source"]),
        ("resync", reversed_("DISCONNECTED", "DISCONNECTED"), "reverse_to_source", "reversed", ["reconnect_source"]),
        # A moved first at the reconnect; B's source record lags.
        ("resync", reversed_("DISCONNECTED", "ESTABLISHED"), "reconnect_source", "resyncing", ["wait_resynced"]),
        ("resync", reversed_("DISCONNECTED", "RECONNECTING"), None, "resyncing", ["wait_resynced"]),
        ("resync", reversed_("ESTABLISHED", "ESTABLISHED"), None, "resynced", []),
        ("failback", reversed_("ESTABLISHED", "ESTABLISHED"), None, "resynced", CUTOVER),
        # A moved first at the make-writable; B's source record lags.
        ("failback", reversed_("ESTABLISHED", "DISCONNECTING"), "make_writable_source", "failing back, source disconnecting",
         ["wait_source_writable", "reverse_to_target", "reconnect_target", "apply_settings"]),
        ("failback", reversed_("ESTABLISHED", "DISCONNECTED"), "make_writable_source", "failing back, source writable",
         ["reverse_to_target", "reconnect_target", "apply_settings"]),
        ("failback", declared("DISCONNECTED", "DISCONNECTED"), "reverse_to_target", "failing back, reversed", ["reconnect_target", "apply_settings"]),
        # B moved first at the reconnect; A's source record lags, so the relationship is not yet replicating.
        ("failback", declared("ESTABLISHED", "DISCONNECTED"), "reconnect_target", "reconnecting", ["wait_replicating", "apply_settings"]),
        ("failback", declared("RECONNECTING", "DISCONNECTED"), None, "reconnecting", ["wait_replicating", "apply_settings"]),
        ("failback", declared("ESTABLISHED", "ESTABLISHED"), None, "replicating", []),
    ],
)  # fmt: skip
def test_each_state_gives_the_steps_its_action_has_left(action, records, last_verb, state, steps):
    result = plan(params(action, records, last_verb=last_verb))

    assert (result["state"], names(result)) == (state, steps)
    assert result["done"] is (steps == [])
    assert result["step"] == (result["steps"][0] if steps else None)
    assert result["relationship_id"] == RID


def test_a_failback_from_failed_over_runs_the_resync_first():
    result = plan(params("failback", declared("DISCONNECTED", "DISCONNECTED")))

    assert names(result) == ["reverse_to_source", "reconnect_source", *CUTOVER]


def test_a_finished_sync_wait_is_not_listed_again():
    result = plan(params("failback", reversed_("ESTABLISHED", "ESTABLISHED"), synced=True))

    assert names(result)[0] == "make_writable_source"


def test_a_replicating_relationship_whose_settings_differ_gets_the_settings_step_from_a_failback():
    a, b = declared("ESTABLISHED", "ESTABLISHED", blackout_windows=[{"start_hour": 1, "start_minute": 0, "end_hour": 2, "end_minute": 0, "on_days": ["SUN"]}])

    assert names(plan(params("failback", (a, b)))) == ["apply_settings"]


def test_a_reversed_relationship_whose_mapping_the_reverse_turned_off_gets_it_back_before_the_reconnect():
    entry = {**ENTRY, "map_local_ids_to_nfs_ids": True}
    a, b = reversed_("DISCONNECTED", "DISCONNECTED")
    b["source_relationships"][0]["map_local_ids_to_nfs_ids"] = False

    result = plan(params("resync", (a, b), relationship=entry))

    assert names(result) == ["apply_mapping", "reconnect_source"]
    assert result["step"]["options"] == {
        "source_path": "/replicas/projects", "target_cluster_uuid": A_UUID, "target_path": "/projects", "target_address": A_ADDRESS,
        "target_port": 3712, "relationship_id": RID, "map_local_ids_to_nfs_ids": True,
    }  # fmt: skip
    assert names(plan(params("resync", declared("DISCONNECTED", "DISCONNECTED"), relationship=entry))) == [
        "reverse_to_source", "apply_mapping", "reconnect_source",
    ]  # fmt: skip


# The two ambiguities (spec Decision 4)


def test_the_source_s_target_record_disconnected_after_a_reverse_reads_as_the_resync_s_state_unless_the_run_made_the_source_writable():
    records = reversed_("DISCONNECTED", "DISCONNECTED")

    assert names(plan(params("failback", records)))[0] == "reconnect_source"
    assert names(plan(params("failback", records, last_verb="make_writable_source")))[0] == "reverse_to_target"
    assert names(plan(params("failback", reversed_("ESTABLISHED", "DISCONNECTED"))))[0] == "reconnect_source"


def test_both_records_disconnected_in_the_declared_direction_read_as_failed_over_unless_the_run_reversed_back():
    records = declared("DISCONNECTED", "DISCONNECTED")

    assert names(plan(params("failback", records)))[0] == "reverse_to_source"
    assert names(plan(params("failback", records, last_verb="reverse_to_target")))[0] == "reconnect_target"


# The steps' data


def test_the_verb_steps_name_their_cluster_verb_and_address():
    steps = {step["name"]: step for step in plan(params("failback", declared("DISCONNECTED", "DISCONNECTED")))["steps"]}

    assert {k: steps["reverse_to_source"][k] for k in ("cluster", "host", "kind", "verb", "source_address", "source_port")} == {
        "cluster": "target", "host": "dr01", "kind": "verb", "verb": "reverse", "source_address": A_ADDRESS, "source_port": 3712,
    }  # fmt: skip
    assert {k: steps["reverse_to_target"][k] for k in ("cluster", "host", "verb", "source_address")} == {
        "cluster": "source", "host": "edge01", "verb": "reverse", "source_address": B_ADDRESS,
    }  # fmt: skip
    assert (steps["reconnect_source"]["host"], steps["reconnect_target"]["host"]) == ("edge01", "dr01")
    assert steps["reconnect_source"]["polls"] == [
        {"cluster": "source", "side": "target", "until": ["ESTABLISHED"]},
        {"cluster": "target", "side": "source", "until": ["ESTABLISHED"]},
    ]
    assert steps["await_sync"]["recovery_point"] == POINT
    assert steps["make_writable_source"]["polls"] == [{"cluster": "source", "side": "target", "until": ["DISCONNECTED"]}]
    assert steps["apply_settings"]["options"] == {
        "source_path": "/projects", "target_cluster_uuid": B_UUID, "target_path": "/replicas/projects", "target_address": B_ADDRESS,
        "target_port": 3712, "relationship_id": RID, "blackout_windows": [], "replication_enabled": True,
    }  # fmt: skip


def test_each_step_has_a_line_naming_its_cluster():
    result = plan(params("failover", declared("ESTABLISHED", "ESTABLISHED")))

    assert result["steps"][0]["text"] == (
        f"dr01: make /replicas/projects writable. It goes back to the recovery point {POINT}; writes on edge01 after it stay behind on edge01"
    )


# The refusals (FR-011)


def test_no_record_on_the_target_is_refused():
    with pytest.raises(QumuloError, match="dr01 holds no record of dr01:/replicas/projects"):
        plan(params("failover", ({"source_relationships": [], "target_relationships": []}, {"source_relationships": [], "target_relationships": []})))


def test_records_in_both_directions_are_refused():
    a, b = declared("ESTABLISHED", "ESTABLISHED")
    b["source_relationships"] = reversed_("DISCONNECTED", "DISCONNECTED")[1]["source_relationships"]

    with pytest.raises(QumuloError, match="in both directions"):
        plan(params("failover", (a, b)))


def test_two_records_of_the_key_on_the_target_are_refused():
    a, b = declared("ESTABLISHED", "ESTABLISHED")
    b["target_relationships"].append({**b["target_relationships"][0], "id": "other"})

    with pytest.raises(QumuloError, match="2 records match"):
        plan(params("failover", (a, b)))


@pytest.mark.parametrize("state", ["AWAITING_AUTHORIZATION", "ENDED"])
def test_a_relationship_that_has_not_replicated_or_ended_is_refused(state):
    with pytest.raises(QumuloError, match=f"is {state}; a failover acts only on a relationship that has replicated"):
        plan(params("failover", declared(state, "ESTABLISHED")))


@pytest.mark.parametrize(
    ("action", "records", "names_action"),
    [
        ("failover", reversed_("DISCONNECTED", "DISCONNECTED"), "resync or failback"),
        ("failover", reversed_("ESTABLISHED", "ESTABLISHED"), "failback"),
        ("resync", declared("ESTABLISHED", "ESTABLISHED"), "failover"),
        ("resync", declared("RECONNECTING", "ESTABLISHED"), "failback, which waits until both records read ESTABLISHED"),
    ],
)
def test_an_action_from_a_state_it_does_not_start_from_is_refused_naming_the_one_that_does(action, records, names_action):
    with pytest.raises(QumuloError, match=f"which {action} does not start from; run {names_action}$"):
        plan(params(action, records))


def test_a_make_writable_without_a_recovery_point_is_refused():
    a, b = declared("ESTABLISHED", "ESTABLISHED")
    b["target_relationships"][0]["recovery_point"] = None

    with pytest.raises(QumuloError, match="has no recovery point on dr01: its first job has not finished"):
        plan(params("failover", (a, b)))


def test_a_job_running_on_a_source_that_answers_refuses_the_failover():
    records = declared("ESTABLISHED", "ESTABLISHED", job_state="REPLICATION_RUNNING", job_start_time="2026-11-08T06:35:49Z")

    with pytest.raises(QumuloError, match="edge01 is running a job of dr01:/replicas/projects, started 2026-11-08T06:35:49Z; .* Run again once the job ends"):
        plan(params("failover", records))


def test_the_target_s_running_job_with_the_source_gone_is_noted_and_the_failover_goes_on():
    # QUIRK-125: the target's job_state stays REPLICATION_RUNNING while its source does not answer (research F9).
    b = declared("ESTABLISHED", "ESTABLISHED")[1]
    b["target_relationships"][0].update(job_state="REPLICATION_RUNNING", job_start_time="2026-11-08T06:44:58Z")

    result = plan(params("failover", (None, b), source_error="edge01 unreachable for 30s"))

    assert names(result) == ["make_writable_target"]
    assert result["notes"] == [
        "edge01 did not answer (edge01 unreachable for 30s); the plan goes on from dr01's record alone",
        (
            "dr01's record shows a job started 2026-11-08T06:44:58Z, which cannot finish while edge01 does not answer (QUIRK-125); the failover "
            "discards its partial changes"
        ),
    ]


@pytest.mark.parametrize("action", ["resync", "failback"])
def test_a_resync_or_failback_without_the_source_is_refused(action):
    b = declared("DISCONNECTED", "DISCONNECTED")[1]

    with pytest.raises(QumuloError, match=f"^{action} needs edge01, which did not answer: unreachable$"):
        plan(params(action, (None, b), source_error="unreachable"))


def test_a_resync_whose_source_holds_no_record_is_refused():
    b = declared("DISCONNECTED", "DISCONNECTED")[1]

    with pytest.raises(QumuloError, match="edge01 holds no record of the relationship dr01:/replicas/projects"):
        plan(params("resync", ({"source_relationships": [], "target_relationships": []}, b)))


def test_a_resync_into_a_source_whose_only_tenant_has_replication_off_is_refused():
    off = {"tenants": [{"id": 1, "name": "Default", "replication_enabled": False}], "document": {}}

    with pytest.raises(QumuloError) as refused:
        plan(params("resync", declared("DISCONNECTED", "DISCONNECTED"), source_tenancy=off))

    assert str(refused.value) == (
        f"Replication into /projects connects to {A_ADDRESS}, but Default, the only tenant, has replication_enabled: false. Set replication_enabled: "
        "true on Default. The resync makes edge01 the target of dr01:/replicas/projects."
    )


def test_a_failback_checks_both_new_targets_tenants_before_its_first_step():
    off = {"tenants": [{"id": 1, "name": "Default", "replication_enabled": False}], "document": {}}

    with pytest.raises(QumuloError, match="The failback makes dr01 the target"):
        plan(params("failback", declared("DISCONNECTED", "DISCONNECTED"), target_tenancy=off))


def test_an_address_on_a_network_of_another_tenant_is_judged_by_that_tenant():
    tenancy = {
        "tenants": [{"id": 1, "name": "Default", "replication_enabled": True}, {"id": 2, "name": "finance", "replication_enabled": False}],
        "document": {
            "frontend_networks": [
                {"id": 2, "name": "fin", "type": "STATIC", "tenant_id": 2, "ip_ranges": ["203.0.113.11-203.0.113.12"], "floating_ip_ranges": [],
                 "netmask": "255.255.255.0", "gateway": "203.0.113.1", "dns_servers": [], "dns_search_domains": [], "mtu": 1500, "vlan_id": 0},
            ],
            "managed_interfaces": [],
        },
    }  # fmt: skip

    with pytest.raises(QumuloError, match="on network 2 \\(fin\\) of tenant finance, which has replication_enabled: false"):
        plan(params("resync", declared("DISCONNECTED", "DISCONNECTED"), source_tenancy=tenancy))


def test_a_failover_needs_no_tenancy_and_no_address():
    result = plan(params("failover", declared("ESTABLISHED", "ESTABLISHED"), source_tenancy=None, target_tenancy=None, source_replication_address=None))

    assert names(result)[0] == "make_writable_target"


def test_a_stuck_job_on_a_source_that_answers_is_discarded_only_with_its_consent():
    records = declared("ESTABLISHED", "ESTABLISHED", job_state="REPLICATION_RUNNING", job_start_time="2026-11-08T06:35:49Z")

    result = plan(params("failover", records, allow_partial_job=True))

    assert names(result) == ["make_writable_target"]
    assert result["notes"] == ["edge01's job started 2026-11-08T06:35:49Z is discarded, as replication_failover_allow_partial_job allows"]


def test_a_live_record_from_a_previous_source_uuid_refuses_naming_the_restore():
    a, b = declared("ESTABLISHED", "ESTABLISHED")
    b["target_relationships"].append(record("0e2d4c6a-8b1f-4d3e-a5c7-9f0b2d4e6a8c", "/projects", B_UUID, "/replicas/projects", "ESTABLISHED", id="x-1"))

    with pytest.raises(QumuloError, match="from a previous UUID of edge01 \\(id x-1\\).*playbooks/replication_restore.yml"):
        plan(params("failover", (a, b), source_previous_cluster_uuids=["0E2D4C6A-8B1F-4D3E-A5C7-9F0B2D4E6A8C"]))


def test_an_ended_leftover_beside_the_live_record_is_ignored():
    a, b = declared("ESTABLISHED", "ESTABLISHED")
    b["target_relationships"].append({**b["target_relationships"][0], "id": "old", "state": "ENDED"})

    assert plan(params("failover", (a, b)))["relationship_id"] == RID


def test_a_failover_whose_source_answers_with_no_record_goes_on_with_a_note():
    b = declared("ESTABLISHED", "ESTABLISHED")[1]

    result = plan(params("failover", ({"source_relationships": [], "target_relationships": []}, b)))

    assert result["notes"] == ["edge01 holds no record of dr01:/replicas/projects; the plan goes on from dr01's record alone"]


def test_a_resync_whose_source_address_is_not_an_ip_literal_is_refused():
    with pytest.raises(QumuloError, match="edge01's qumulo_replication_address, 'edge01.example.com', is not an IP address"):
        plan(params("resync", declared("DISCONNECTED", "DISCONNECTED"), source_replication_address="edge01.example.com"))


@pytest.mark.parametrize("action", ["resync", "failback"])
def test_a_source_record_ended_beside_an_established_target_is_refused_naming_both_records_and_o3(action):
    with pytest.raises(QumuloError) as refused:
        plan(params(action, declared("ESTABLISHED", "ENDED")))

    assert str(refused.value) == (
        "edge01's source record of dr01:/replicas/projects is ENDED, while dr01's target record reads ESTABLISHED; edge01's record cannot "
        f"follow it back, so the {action} has nothing it can move. To replicate again, declare the relationship again: the baseline then "
        "reports row O3 on it, which recreate on the entry and a release naming O3 lift (Replicate a directory to another cluster)"
    )


@pytest.mark.parametrize("action", ["resync", "failback"])
def test_a_source_record_awaiting_authorization_beside_an_established_target_is_refused(action):
    with pytest.raises(QumuloError, match=f"is AWAITING_AUTHORIZATION, while dr01's target record reads ESTABLISHED.* the {action} has nothing"):
        plan(params(action, declared("ESTABLISHED", "AWAITING_AUTHORIZATION")))


def test_a_declared_timezone_with_spaces_is_the_timezone_the_record_holds():
    entry = {**ENTRY, "blackout_window_timezone": " UTC "}
    records = declared("ESTABLISHED", "ESTABLISHED", blackout_window_timezone="UTC")

    assert names(plan(params("failback", records, relationship=entry))) == []


def test_a_reconnect_whose_host_is_named_in_the_consent_says_it_does_not_compare():
    steps = {s["name"]: s for s in plan(params("failback", declared("DISCONNECTED", "DISCONNECTED"), newer_writes_hosts=["edge01"]))["steps"]}

    assert steps["reconnect_source"]["text"].endswith("it does not compare first, since replication_failover_allow_newer_writes names edge01")
    assert steps["reconnect_target"]["text"].endswith("stops when anything was written there since")
