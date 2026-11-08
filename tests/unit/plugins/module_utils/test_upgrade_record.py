# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""upgrade_record.py's commit records, through FR-034's table (specs/017-cluster-upgrade)."""

from __future__ import annotations

import hashlib

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade_record
from ansible_collections.qumulo.cluster.plugins.module_utils.version import ClusterVersion


def v(text, build_id=""):
    return ClusterVersion.parse(text, build_id)


# Commit records, through decide_record: FR-034's table, one case per row in its order. The plan and the module both
# call it, so the two never disagree.

PATH = "/inv/artifacts/edge01/upgrade/commit.yml"
UUID = "6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11"
OLD, NEW = "385145.1.82", "393623.1.1"


def record(state="pending", **overrides):
    fields = {
        "format": 1,
        "state": state,
        "cluster_uuid": UUID,
        "build": OLD,
        "target_version": "7.11.0",
        "declared_build": None,
        "time": "2026-11-04T05:00:00Z",
        **overrides,
    }
    return "\n".join(f"{k}: {'null' if v is None else (v if isinstance(v, int) else repr(str(v)))}" for k, v in fields.items()) + "\n"


def read(version="7.10.3", build=OLD, state="UPGRADE_STATE_IDLE", uuid=UUID):
    return {"cluster_uuid": uuid, "version": version, "build": build, "state": state}


def decide(text, found, hub=None):
    return upgrade_record.decide_record("edge01", PATH, text, found, hub=hub)


F19_PENDING = (
    f"edge01: a commit record, {PATH}, shows that its commit to 7.11.0 on 2026-11-04T05:00:00Z did not finish, and some "
    "nodes may be armed. Nothing was sent. Contact Qumulo Care. Once they have cleared edge01, remove the record; it "
    "also goes by itself once edge01 runs a new build of 7.11.0 or a later release. To upgrade the rest of the fleet "
    "meanwhile, leave edge01 out of the run with --limit."
)


@pytest.mark.parametrize("text", ["format: 2\nstate: pending\n", "state: [unclosed\n", "just text\n", ""])
def test_row_1_a_file_that_is_not_format_1_or_cannot_be_parsed_is_f31_naming_the_path(text):
    decision = decide(text, read())

    assert (decision.action, decision.code) == ("refuse", "F31")
    assert decision.message.startswith(f"{PATH} is not a commit record this collection can read: ")
    assert decision.message.endswith(
        ". Check whether a commit of edge01 is unfinished with Qumulo Care, then remove the file. To upgrade the rest of "
        "the fleet meanwhile, leave edge01 out of the run with --limit."
    )


@pytest.mark.parametrize("state", ["pending", "unfinished"])
def test_row_2_a_record_of_another_cluster_is_retired(state):
    decision = decide(record(state), read(uuid="0d4c1d38-0000-4000-8000-00000000abcd"))

    assert (decision.action, decision.code) == ("retire", None)
    assert "0d4c1d38-0000-4000-8000-00000000abcd" in decision.message


@pytest.mark.parametrize("version", ["7.11.0", "7.11.1"])
def test_row_3_a_pending_record_at_a_new_build_on_the_target_or_later_is_retired(version):
    decision = decide(record(), read(version=version, build=NEW))

    assert decision.action == "retire"


def test_row_3_never_retires_an_unfinished_record_on_a_new_build():
    decision = decide(record("unfinished", error_info="node 2 left the quorum"), read(version="7.11.0", build=NEW))

    assert (decision.action, decision.code) == ("refuse", "F19")


@pytest.mark.parametrize("state", ["UPGRADE_STATE_PREPARING", "UPGRADE_STATE_PREPARED"])
def test_row_4_a_pending_record_over_preparing_or_prepared_on_the_recorded_build_is_dropped(state):
    decision = decide(record(), read(state=state))

    assert decision.action == "drop"


@pytest.mark.parametrize(
    ("state", "shown"),
    [("UPGRADE_STATE_COMMITTING", "COMMITTING"), ("UPGRADE_STATE_COMMITTED", "COMMITTED"), ("UPGRADE_STATE_REBOOTING", "REBOOTING")],
)
def test_row_5_a_pending_record_over_a_commit_under_way_is_f30(state, shown):
    decision = decide(record(), read(state=state))

    assert (decision.action, decision.code) == ("refuse", "F30")
    assert decision.message == (
        f"edge01's commit to 7.11.0, recorded at {PATH}, is under way (it reads {shown}). Wait for it to finish, then run "
        "the upgrade again; the record goes by itself once edge01 runs 7.11.0 on a new build. To upgrade the rest of the "
        "fleet meanwhile, leave edge01 out of the run with --limit."
    )


def test_row_5_a_pending_record_whose_hosts_read_failed_is_f30_never_row_2():
    decision = decide(record(), None)

    assert (decision.action, decision.code) == ("refuse", "F30")
    assert "(it reads no answer)" in decision.message


def test_row_6_a_pending_record_over_idle_on_the_recorded_build_is_f19_with_the_retirement_clause():
    decision = decide(record(), read())

    assert (decision.action, decision.code, decision.message) == ("refuse", "F19", F19_PENDING)


@pytest.mark.parametrize("found", [read(), read(version="7.11.0", build=NEW), None], ids=["idle", "new build", "read failed"])
def test_row_7_an_unfinished_record_is_f19_without_the_retirement_clause(found):
    decision = decide(record("unfinished", error_info="node 2 left the quorum"), found)

    assert (decision.action, decision.code) == ("refuse", "F19")
    assert "remove the record. To upgrade" in decision.message
    assert "goes by itself" not in decision.message


def test_row_8_a_pending_record_on_a_new_build_below_the_target_is_f19():
    decision = decide(record(), read(version="7.10.3.1", build=NEW))

    assert (decision.action, decision.code, decision.message) == ("refuse", "F19", F19_PENDING)


def test_a_portal_members_refusal_names_its_whole_component():
    decision = decide(record(), read(), hub="hub01")

    assert decision.message.endswith("leave hub01's whole portal component out of the run with --limit.")


@pytest.mark.parametrize("found", [read(), None])
def test_each_decision_carries_the_files_digest(found):
    text = record()

    assert decide(text, found).digest == "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def test_record_text_round_trips_through_format_record():
    fields = {"state": "pending", "cluster_uuid": UUID, "build": OLD, "target_version": "7.11.0", "declared_build": None, "time": "2026-11-04T05:00:00Z"}

    assert upgrade_record.parse_record(upgrade_record.format_record(fields)) == {"format": 1, **fields}
