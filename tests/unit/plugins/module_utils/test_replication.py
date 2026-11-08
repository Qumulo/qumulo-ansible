# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The replication area's shared pieces: the shape both sides report, matching by key, unmet entries, releases and the wait.

The status records come from fixtures_replication.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import replication
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, client_params
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import (
    ADDRESS,
    REL_ID,
    SOURCE_STATUS,
    SOURCE_UUID,
    TARGET_STATUS,
    TARGET_UUID,
    WINDOW,
)

OLD_TARGET_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
OTHER_HOST_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
UNKNOWN_UUID = "0f6b0c39-3f43-4c1b-9b7e-2a3c4d5e6f70"


def described(**changes):
    """A described source record, the key's by default."""
    return {**replication.describe_source(SOURCE_STATUS), **changes}


def key():
    return replication.source_key("/src", TARGET_UUID, "/dst")


# The shape


def test_a_source_record_is_described_with_canonical_paths_uuids_and_days():
    assert replication.describe_source(SOURCE_STATUS) == {
        "id": REL_ID,
        "source_cluster_uuid": SOURCE_UUID,
        "target_cluster_uuid": TARGET_UUID,
        "source_path": "/src",
        "target_path": "/dst",
        "target_address": ADDRESS,
        "target_port": 3712,
        "state": "ESTABLISHED",
        "end_reason": None,
        "job_state": "REPLICATION_NOT_RUNNING",
        "job_start_time": None,
        "recovery_point": "2026-11-01T08:21:54.728656093Z",
        "recovery_point_snapshot_id": None,
        "error_from_last_job": None,
        "target_root_read_only": True,
        "replication_enabled": True,
        "map_local_ids_to_nfs_ids": False,
        "source_root_read_only": False,
        "blackout_windows": [{**WINDOW, "on_days": ["MON", "FRI"]}],
        "blackout_window_timezone": "America/Los_Angeles",
    }


@pytest.mark.parametrize("describe", [replication.describe_source, replication.describe_target])
def test_the_recovery_point_snapshot_is_reported_by_its_id_on_both_sides(describe):
    record = {**SOURCE_STATUS, "recovery_point_snapshot": {"id": 42, "name": "42_replication_from_edge01", "timestamp": "2026-11-01T08:21:54Z"}}

    assert describe(record)["recovery_point_snapshot_id"] == 42
    assert describe({**record, "recovery_point_snapshot": None})["recovery_point_snapshot_id"] is None


def test_a_target_record_has_the_source_s_keys_with_null_for_what_only_the_source_records():
    target = replication.describe_target(TARGET_STATUS)
    source = replication.describe_source(SOURCE_STATUS)

    assert list(target) == list(source) == list(replication.SHAPE)
    assert {key: target[key] for key in ("id", "source_cluster_uuid", "target_cluster_uuid", "source_path", "target_path")} == {
        key: source[key] for key in ("id", "source_cluster_uuid", "target_cluster_uuid", "source_path", "target_path")
    }
    only_source = ("target_address", "target_port", *replication.SETTINGS)
    assert {key: target[key] for key in only_source} == dict.fromkeys(only_source)
    assert (target["state"], target["recovery_point"], target["end_reason"]) == ("AWAITING_AUTHORIZATION", None, None)


# QUIRK-020: the source reports target_root_path as sent until its first poll of the target, then with a slash.
@pytest.mark.parametrize("sent", ["/dst", "/dst/"])
def test_a_target_path_with_or_without_its_slash_is_one_key(sent):
    record = replication.describe_source({**SOURCE_STATUS, "target_root_path": sent})

    assert replication.key_of_source(record) == key()


def test_a_path_that_is_not_absolute_is_kept_as_read_and_matches_no_key():
    record = replication.describe_target({**TARGET_STATUS, "state": "ENDED", "target_root_path": ""})

    assert record["target_path"] == ""
    assert replication.match_target([record], replication.target_key(SOURCE_UUID, "/src", "/dst")).stale == []


@pytest.mark.parametrize(
    ("state", "kind"),
    [
        ("AWAITING_AUTHORIZATION", "pending"),
        ("ESTABLISHED", "established"),
        ("DISCONNECTING", "failover"),
        ("DISCONNECTED", "failover"),
        ("RECONNECTING", "failover"),
        ("REESTABLISHING", "failover"),
        ("ENDED", "ended"),
    ],
)
def test_each_state_falls_in_one_row_of_the_state_table(state, kind):
    assert replication.state_kind(state) == kind
    assert (state in replication.LIVE) is (kind in ("established", "failover"))


def test_an_unknown_state_is_refused_by_name():
    with pytest.raises(QumuloError, match="state 'PAUSED'"):
        replication.state_kind("PAUSED")


# Matching on the source


def test_the_key_matches_without_regard_to_slashes_or_case():
    spelled = replication.source_key("/src/", TARGET_UUID.upper(), "/dst/")
    record = described(target_cluster_uuid=TARGET_UUID)

    found = replication.match_source([record], spelled, address=ADDRESS)

    assert spelled == key()
    assert (found.matches, found.changed_peers, found.misdirected) == ([record], [], [])


def test_two_matches_fail_naming_both_ids():
    found = replication.match_source([described(id="a"), described(id="b")], key(), address=ADDRESS)

    with pytest.raises(QumuloError, match=r"2 records match .* \(ids a, b\)"):
        replication.only_one(found.matches, "the relationship")


def test_an_ended_record_of_the_key_is_its_match_for_row_o3():
    ended = described(state="ENDED")

    found = replication.match_source([ended], key(), address=ADDRESS)

    assert (found.matches, found.ended) == ([ended], [])


def test_beside_a_live_record_of_the_key_an_ended_one_is_reported_apart():
    # O3's create leaves the new record and the ENDED one side by side until its delete; the live one is the match.
    ended, live = described(id="old", state="ENDED"), described(id="new")

    found = replication.match_source([ended, live], key(), address=ADDRESS)

    assert (found.matches, found.ended) == ([live], [ended])


def test_a_record_of_other_paths_is_not_the_key_s():
    records = [described(source_path="/other"), described(target_path="/dst/sub")]

    found = replication.match_source(records, key(), address=ADDRESS)

    assert (found.matches, found.changed_peers, found.misdirected) == ([], [], [])


def test_another_uuid_at_the_declared_address_and_port_is_a_changed_peer():
    record = described(target_cluster_uuid=UNKNOWN_UUID)

    found = replication.match_source([record], key(), address=ADDRESS)

    assert found.changed_peers == [record]


def test_an_address_spelled_another_way_is_the_declared_address():
    record = described(target_cluster_uuid=UNKNOWN_UUID, target_address="2001:db8::40")

    found = replication.match_source([record], key(), address="2001:DB8:0:0::40")

    assert found.changed_peers == [record]


def test_a_previous_uuid_is_a_changed_peer_at_any_address():
    record = described(target_cluster_uuid=OLD_TARGET_UUID, target_address="198.51.100.7", target_port=13712)

    found = replication.match_source([record], key(), address=ADDRESS, previous_uuids=[OLD_TARGET_UUID.upper()])

    assert found.changed_peers == [record]


def test_a_changed_peer_beside_a_key_match_is_reported_with_it():
    match, peer = described(), described(id="old", target_cluster_uuid=OLD_TARGET_UUID)

    found = replication.match_source([peer, match], key(), address=ADDRESS, previous_uuids=[OLD_TARGET_UUID])

    assert (found.matches, found.changed_peers) == ([match], [peer])


def test_another_inventory_host_s_uuid_is_never_a_changed_peer():
    at_address = described(id="here", target_cluster_uuid=OTHER_HOST_UUID)
    elsewhere = described(id="there", target_cluster_uuid=OTHER_HOST_UUID, target_address="198.51.100.7")

    found = replication.match_source(
        [at_address, elsewhere], key(), address=ADDRESS, previous_uuids=[OTHER_HOST_UUID], inventory_uuids=[OTHER_HOST_UUID, TARGET_UUID]
    )

    assert (found.changed_peers, found.misdirected) == ([], [at_address])


def test_an_unknown_uuid_at_another_address_or_port_is_left_alone():
    records = [described(target_cluster_uuid=UNKNOWN_UUID, target_address="198.51.100.7"), described(target_cluster_uuid=UNKNOWN_UUID, target_port=13712)]

    found = replication.match_source(records, key(), address=ADDRESS)

    assert (found.matches, found.changed_peers, found.misdirected) == ([], [], [])


def test_previous_uuids_holding_the_declared_uuid_are_refused():
    with pytest.raises(QumuloError, match=f"previous target UUIDs hold the declared target UUID {TARGET_UUID}"):
        replication.match_source([], key(), address=ADDRESS, previous_uuids=[TARGET_UUID.upper()])


# Matching on the target


def test_the_target_finds_the_source_s_record_and_calls_the_key_s_others_stale():
    records = [replication.describe_target({**TARGET_STATUS, "id": rid}) for rid in ("old", REL_ID)]
    other = replication.describe_target({**TARGET_STATUS, "id": "x", "source_cluster_uuid": UNKNOWN_UUID})
    key = replication.target_key(SOURCE_UUID.upper(), "/src/", "/dst")

    found = replication.match_target([*records, other], key, source_id=REL_ID)

    assert (found.own["id"], [record["id"] for record in found.stale]) == (REL_ID, ["old"])


def test_on_the_target_other_paths_are_not_the_key_s_and_an_ended_record_of_it_is_stale():
    records = [
        replication.describe_target({**TARGET_STATUS, "id": "holder", "source_root_path": "/other/"}),
        replication.describe_target({**TARGET_STATUS, "id": "nested", "target_root_path": "/dst/sub"}),
        replication.describe_target({**TARGET_STATUS, "id": "ended", "state": "ENDED"}),
        replication.describe_target(TARGET_STATUS),
    ]

    found = replication.match_target(records, replication.target_key(SOURCE_UUID, "/src", "/dst"), source_id=REL_ID)

    assert (found.own["id"], [record["id"] for record in found.stale]) == (REL_ID, ["ended"])


def test_every_record_of_the_key_is_stale_when_the_source_creates():
    record = replication.describe_target(TARGET_STATUS)

    found = replication.match_target([record], replication.target_key(SOURCE_UUID, "/src", "/dst"))

    assert (found.own, found.stale) == (None, [record])


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [("/r", "/r", True), ("/r", "/r/a", True), ("/r/a", "/r", True), ("/r", "/rb", False), ("/", "/r", True), ("/a", "/b", False)],
)
def test_paths_overlap_when_equal_or_nested(a, b, overlap):
    assert replication.overlaps(a, b) is overlap


# Unmet entries


def test_an_unmet_entry_carries_its_row_binding_facts_and_what_lifts_it():
    entry = replication.unmet("O2", "held by another source", binds_to=7, facts={"source_cluster_uuid": OLD_TARGET_UUID, "state": "ESTABLISHED"})

    assert entry == {
        "row": "O2",
        "message": "held by another source",
        "binds_to": "7",
        "lifted_by": "release",
        "lifted": False,
        "pending": False,
        "facts": {"source_cluster_uuid": OLD_TARGET_UUID, "state": "ESTABLISHED"},
    }


@pytest.mark.parametrize(
    ("row", "lifted_by"),
    [("O1", "allow_non_empty_target"), ("O3", "recreate and release"), ("O6", None), ("O9", None), ("changed_peer", "recreate_changed_peer")],
)
def test_each_row_names_what_lifts_it(row, lifted_by):
    binds_to = "1" if row in replication.RELEASABLE else None

    assert replication.unmet(row, "m", binds_to=binds_to)["lifted_by"] == lifted_by


@pytest.mark.parametrize(
    ("row", "kwargs", "problem"),
    [
        ("O10", {}, "neither an overwrite-table row nor a known condition"),
        ("O2", {}, "a releasable row binds to an id"),
        ("O1", {"binds_to": "1"}, "a releasable row binds to an id"),
        ("O6", {"lifted": True}, "cannot be lifted"),
        ("O4", {"binds_to": "1", "pending": True}, "cannot be pending"),
    ],
)
def test_a_malformed_unmet_entry_is_a_programming_error(row, kwargs, problem):
    with pytest.raises(ValueError, match=problem):
        replication.unmet(row, "m", **kwargs)


def test_unmet_entries_are_reported_in_the_table_s_order_with_o4_before_o1():
    shuffled = ["O1", "changed_peer", "O5", "O9", "O4", "O8", "source_missing", "O6", "O2", "O3", "O7"]
    entries = [replication.unmet(row, "m", binds_to="1" if row in replication.RELEASABLE else None) for row in shuffled]

    assert [entry["row"] for entry in replication.reporting_order(entries)] == [
        "changed_peer",
        "source_missing",
        "O8",
        "O3",
        "O7",
        "O9",
        "O2",
        "O6",
        "O5",
        "O4",
        "O1",
    ]


def test_only_entries_neither_lifted_nor_pending_block():
    entries = [replication.unmet("O1", "m", lifted=True), replication.unmet("target_missing", "m", pending=True), replication.unmet("O7", "m")]

    assert [entry["row"] for entry in replication.blocking(entries)] == ["O7"]


# Releases


def test_a_release_entry_splits_at_the_first_colon_the_last_at_and_the_last_colon():
    assert replication.parse_release("dr01:/r/a@b:c/@id-7:O4,O2") == {
        "target_host": "dr01",
        "target_path": "/r/a@b:c",
        "record_id": "id-7",
        "rows": ["O2", "O4"],
    }


def test_only_o2_to_o5_are_releasable():
    assert replication.RELEASABLE == {"O2", "O3", "O4", "O5"}


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ("dr01:/r", "is not"),
        ("dr01:/r@7", "is not"),
        (":/r@7:O2", "is not"),
        ("dr01:@7:O2", "is not"),
        ("dr01:/r@:O2", "is not"),
        ("dr01:/r@7:", "is not"),
        ("dr01:/r@7:O2,", "its rows hold an empty name"),
        ("dr01:/r@7:O1", "names O1"),
        ("dr01:/r@7:O2,O6", "names O6"),
        ("dr01:/r@7:O7", "names O7"),
        ("dr01:/r@7:O8", "names O8"),
        ("dr01:/r@7:O9", "names O9"),
    ],
)
def test_a_malformed_release_entry_is_refused_by_name(entry, problem):
    with pytest.raises(QumuloError, match=problem):
        replication.parse_release(entry)


def test_a_release_lifts_only_the_rows_it_names_on_the_record_it_binds_to():
    releases = [replication.parse_release("dr01:/r@7:O2")]

    assert replication.release_lifts(releases, "O2", 7) is True
    assert replication.release_lifts(releases, "O4", 7) is False
    assert replication.release_lifts(releases, "O2", 8) is False


def test_spaces_repeats_and_a_colon_in_the_id_are_accepted():
    assert replication.parse_release("dr01:/r@7: O4 ,O2,O2")["rows"] == ["O2", "O4"]
    assert replication.parse_release("dr01:/r@a:b:O2")["record_id"] == "a:b"


def test_an_o4_binds_to_a_holder_s_uuid_or_a_directory_s_file_id():
    holder, file_id = "9465828e-7dcf-466e-a550-78ed0bd6b79b", "106505742056649366299408859139"
    by_holder = [replication.parse_release(f"dr01:/r@{holder}:O4")]
    by_file = [replication.parse_release(f"dr01:/r@{file_id}:O4")]

    assert replication.release_lifts(by_holder, "O4", holder) is True
    assert replication.release_lifts(by_file, "O4", file_id) is True
    assert replication.release_lifts(by_holder, "O4", file_id) is False


def test_the_releases_to_print_give_one_entry_per_record_with_exactly_its_rows():
    entries = [
        replication.unmet("O3", "m", binds_to="ended-on-source"),
        replication.unmet("O2", "m", binds_to="holder"),
        replication.unmet("O4", "m", binds_to="holder"),
        replication.unmet("O5", "m", binds_to="done", lifted=True),
        replication.unmet("O1", "m"),
    ]

    printed = replication.releases_to_print(entries)

    assert printed == [{"record_id": "ended-on-source", "rows": ["O3"]}, {"record_id": "holder", "rows": ["O2", "O4"]}]
    assert replication.format_release("dr01", "/r/", "holder", ["O2", "O4"]) == "dr01:/r@holder:O2,O4"


def test_a_printed_release_keeps_the_rows_already_lifted_on_its_record():
    # The operator passes back only what was printed, so an entry bound to the holder carries every row its check
    # reported there, O2 included, even when this run's release already lifts O2.
    entries = [replication.unmet("O2", "m", binds_to="holder", lifted=True), replication.unmet("O4", "m", binds_to="holder")]

    assert replication.releases_to_print(entries) == [{"record_id": "holder", "rows": ["O2", "O4"]}]


def test_nothing_is_printed_once_every_row_on_a_record_is_lifted():
    entries = [replication.unmet("O2", "m", binds_to="holder", lifted=True), replication.unmet("O4", "m", binds_to="holder", lifted=True)]

    assert replication.releases_to_print(entries) == []


# The wait by id


class Waiting(replication.Relationship):
    def describe(self):
        return "the relationship"


def waiting(wait_timeout=12):
    module = SimpleNamespace(params={"wait_timeout": wait_timeout}, check_mode=False, _diff=False)
    return Waiting(module, QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)))


def status_path(rid=REL_ID):
    return f"/v2/replication/target-relationships/{rid}/status"


def test_the_wait_polls_by_id_through_unanswered_reads_until_done(fake_api, fake_clock):
    fake = fake_api()
    fake.expect("GET", status_path(), response=TARGET_STATUS)
    fake.expect("GET", status_path(), status=503, response={"error_class": "service_unavailable_error"})
    fake.expect("GET", status_path(), response={**TARGET_STATUS, "state": "ESTABLISHED"})

    record = waiting().wait_by_id(replication.TARGET_STATUS, REL_ID, lambda r: r["state"] == "ESTABLISHED", "ESTABLISHED")

    assert record["state"] == "ESTABLISHED"
    assert fake_clock.sleeps == [5.0, 5.0]


def test_the_wait_fails_at_once_when_the_record_is_gone(fake_api):
    fake = fake_api()
    fake.expect("GET", status_path(), status=404, response={"error_class": "relationship_manager_relationship_not_found_error"})

    with pytest.raises(QumuloError, match=f"the relationship \\(id {REL_ID}\\) is gone; waited for ESTABLISHED"):
        waiting().wait_by_id(replication.TARGET_STATUS, REL_ID, lambda r: r["state"] == "ESTABLISHED", "ESTABLISHED")


def test_the_wait_times_out_saying_the_cluster_never_answered(fake_api):
    fake = fake_api()
    for _poll in range(3):
        fake.expect("GET", status_path(), status=503, response={"error_class": "service_unavailable_error"})

    with pytest.raises(QumuloError, match="is not answering after 10s; waited for ESTABLISHED"):
        waiting(wait_timeout=10).wait_by_id(replication.TARGET_STATUS, REL_ID, lambda r: r["state"] == "ESTABLISHED", "ESTABLISHED")


def test_the_wait_times_out_naming_the_last_state_read(fake_api):
    fake = fake_api()
    for _poll in range(3):
        fake.expect("GET", status_path(), response=TARGET_STATUS)

    with pytest.raises(QumuloError, match="is still AWAITING_AUTHORIZATION after 10s; waited for ESTABLISHED"):
        waiting(wait_timeout=10).wait_by_id(replication.TARGET_STATUS, REL_ID, lambda r: r["state"] == "ESTABLISHED", "ESTABLISHED")
