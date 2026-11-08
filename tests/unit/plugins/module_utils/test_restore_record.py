# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The restore's files on the controller: the key's hash, the file names, the record's format, the stamp, and the
rebuild report's claim."""

from __future__ import annotations

import hashlib
import os

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils import restore_record
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import RestoreRecordError

UUID = "6f1c3d1e-0b4a-4e7e-9c55-2b8f0a7d4e10"
KEY = {"source_path": "/projects", "target_host": "hub01", "target_cluster_uuid": UUID, "target_path": "/replicas/edge01"}
RECORD = {
    "format": 1,
    "key": KEY,
    "r_id": "r-1",
    "x_id": "x-1",
    "x_source_cluster_uuid": "0e2d4c6a-8b1f-4d3e-a5c7-9f0b2d4e6a8c",
    "x_source_path": "/projects",
    "x_snapshot_ids": [3, 7],
    "recovery_point": "2026-11-01T10:00:00Z",
    "recovery_point_snapshot_id": 12,
    "written_at": "2026-11-01T10:05:00Z",
}


def lines_digest(source_path, uuid, target_path):
    return hashlib.sha256(f"{source_path}\n{uuid}\n{target_path}".encode()).hexdigest()


def test_the_key_drops_trailing_slashes_keeps_the_root_and_lowers_the_uuid():
    key = {"source_path": "/projects/", "target_host": "Hub01", "target_cluster_uuid": UUID.upper(), "target_path": "/"}

    assert restore_record.normalized_key(key) == {"source_path": "/projects", "target_host": "Hub01", "target_cluster_uuid": UUID, "target_path": "/"}


def test_a_relative_path_in_the_key_fails_naming_its_field():
    with pytest.raises(restore_record.QumuloError, match="source_path 'projects' must be an absolute path"):
        restore_record.normalized_key({**KEY, "source_path": "projects"})


def test_the_hash_is_the_sha256_of_the_three_normalized_lines():
    assert restore_record.key_hash(KEY) == lines_digest("/projects", UUID, "/replicas/edge01")


def test_a_slash_or_the_uuids_case_gives_the_same_hash_and_the_target_host_none():
    variant = {**KEY, "source_path": "/projects//", "target_cluster_uuid": UUID.upper(), "target_path": "/replicas/edge01/", "target_host": "hub02"}

    assert restore_record.key_hash(variant) == restore_record.key_hash(KEY)


def test_swapped_paths_give_another_hash():
    swapped = {**KEY, "source_path": KEY["target_path"], "target_path": KEY["source_path"]}

    assert restore_record.key_hash(swapped) != restore_record.key_hash(KEY)


def test_the_files_live_under_the_hosts_restore_directory(tmp_path):
    directory = restore_record.restore_dir(str(tmp_path), "edge01")
    digest = restore_record.key_hash(KEY)

    assert directory == str(tmp_path / "edge01" / "restore")
    assert restore_record.record_path(directory, KEY) == str(tmp_path / "edge01" / "restore" / f"{digest}.yml")
    assert restore_record.stamp_path(directory, KEY) == str(tmp_path / "edge01" / "restore" / f"done-{digest}.yml")


def test_a_record_of_format_1_parses_with_its_mark_optional():
    assert restore_record.parse_record(yaml.safe_dump(RECORD), "/a/r.yml") == RECORD
    marked = {**RECORD, "reconnected": True, "recovery_point": None, "recovery_point_snapshot_id": None}
    assert restore_record.parse_record(yaml.safe_dump(marked), "/a/r.yml") == marked


UNKNOWN = "The restore record /a/r.yml is of an unknown format: "


@pytest.mark.parametrize("format_", [2, "1", None, True])
def test_a_record_of_another_format_is_unknown_naming_the_file(format_):
    with pytest.raises(RestoreRecordError) as raised:
        restore_record.parse_record(yaml.safe_dump({**RECORD, "format": format_}), "/a/r.yml")

    assert str(raised.value) == UNKNOWN + "it is not format 1."


@pytest.mark.parametrize(
    ("field", "missing"),
    [(field, field) for field in sorted(set(RECORD) - {"format", "key"})]
    + [("key", "key, key.source_path, key.target_host, key.target_cluster_uuid, key.target_path")],
)
def test_a_record_that_lacks_a_field_is_unknown_naming_the_file(field, missing):
    lacking = {name: value for name, value in RECORD.items() if name != field}

    with pytest.raises(RestoreRecordError) as raised:
        restore_record.parse_record(yaml.safe_dump(lacking), "/a/r.yml")

    assert str(raised.value) == UNKNOWN + f"it lacks {missing}."


@pytest.mark.parametrize("field", restore_record.KEY_FIELDS)
def test_a_record_whose_key_lacks_a_field_is_unknown_naming_the_file(field):
    key = {name: value for name, value in KEY.items() if name != field}

    with pytest.raises(RestoreRecordError) as raised:
        restore_record.parse_record(yaml.safe_dump({**RECORD, "key": key}), "/a/r.yml")

    assert str(raised.value) == UNKNOWN + f"it lacks key.{field}."


@pytest.mark.parametrize("text", ["", "- a list\n"])
def test_a_record_that_is_no_mapping_is_unknown_naming_the_file(text):
    with pytest.raises(RestoreRecordError) as raised:
        restore_record.parse_record(text, "/a/r.yml")

    assert str(raised.value) == UNKNOWN + "it is not format 1."


def test_a_record_that_is_not_yaml_is_unknown_naming_the_file_and_the_error():
    with pytest.raises(RestoreRecordError) as raised:
        restore_record.parse_record("format: [1\n", "/a/r.yml")

    assert str(raised.value) == UNKNOWN + str(raised.value.__cause__)
    assert str(raised.value.__cause__).startswith("while parsing a flow sequence")


def test_the_stamp_holds_its_format_key_id_report_and_time():
    written = restore_record.stamp({**KEY, "source_path": "/projects/"}, "r-1", "rebuild-20261101T100000.yml", "2026-11-01T11:00:00Z")

    assert written == {"format": 1, "key": KEY, "r_id": "r-1", "report": "rebuild-20261101T100000.yml", "written_at": "2026-11-01T11:00:00Z"}
    assert restore_record.stamp(KEY, None, "rebuild-1.yml", "2026-11-01T11:00:00Z")["r_id"] is None


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


OTHER = {**KEY, "source_path": "/home", "target_path": "/replicas/edge01-home"}


def held(key):
    return {"key": key, "holders": ["x-1"]}


def test_the_newest_report_by_name_claims_each_held_entry(tmp_path):
    write_yaml(tmp_path / "rebuild-20261101T100000.yml", {"held": [held(OTHER)]})
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [held({**KEY, "source_path": "/projects/"}), held(OTHER)]})
    write_yaml(tmp_path / "predestroy-20261103T100000.yml", {"held": []})

    claims = restore_record.report_claims(str(tmp_path))

    assert claims == {"report": "rebuild-20261102T100000.yml", "claimed": [KEY, OTHER], "stamped": []}


def test_a_stamp_naming_the_newest_report_retires_only_its_own_key(tmp_path):
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [held(KEY), held(OTHER)]})
    directory = restore_record.restore_dir(str(tmp_path.parent), tmp_path.name)
    write_yaml(tmp_path / "restore" / f"done-{restore_record.key_hash(KEY)}.yml", restore_record.stamp(KEY, "r-1", "rebuild-20261102T100000.yml", "t"))

    assert directory == str(tmp_path / "restore")
    assert restore_record.report_claims(str(tmp_path))["claimed"] == [OTHER]


def test_a_stamp_naming_an_older_report_leaves_the_newer_claim(tmp_path):
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [held(KEY)]})
    write_yaml(tmp_path / "restore" / f"done-{restore_record.key_hash(KEY)}.yml", restore_record.stamp(KEY, "r-1", "rebuild-20261101T100000.yml", "t"))

    assert restore_record.report_claims(str(tmp_path))["claimed"] == [KEY]


def test_a_stamp_of_another_format_retires_nothing(tmp_path):
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [held(KEY)]})
    stamp = {**restore_record.stamp(KEY, "r-1", "rebuild-20261102T100000.yml", "t"), "format": 2}
    write_yaml(tmp_path / "restore" / f"done-{restore_record.key_hash(KEY)}.yml", stamp)

    assert restore_record.report_claims(str(tmp_path))["claimed"] == [KEY]


@pytest.mark.parametrize("report", [{"hand_offs": ["Apply host_vars_patch first."]}, {"held": []}])
def test_an_r5_era_report_or_an_empty_held_list_claims_nothing(tmp_path, report):
    write_yaml(tmp_path / "rebuild-20261101T100000.yml", {"held": [held(KEY)]})
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", report)

    assert restore_record.report_claims(str(tmp_path)) == {"report": "rebuild-20261102T100000.yml", "claimed": [], "stamped": []}


@pytest.mark.parametrize(
    "text",
    [
        "held: [\n",
        "- a list\n",
        "held: a string\n",
        "held:\n- not a mapping\n- key: {source_path: /projects}\n- key: {source_path: projects, target_cluster_uuid: u, target_path: /r}\n",
    ],
    ids=["not YAML", "not a mapping", "held not a list", "entries without a key of the record's shape"],
)
def test_a_report_or_an_entry_that_cannot_be_read_claims_nothing(tmp_path, text):
    write_yaml(tmp_path / "rebuild-20261101T100000.yml", {"held": [held(KEY)]})
    (tmp_path / "rebuild-20261102T100000.yml").write_text(text, encoding="utf-8")

    assert restore_record.report_claims(str(tmp_path)) == {"report": "rebuild-20261102T100000.yml", "claimed": [], "stamped": []}


def test_a_malformed_entry_leaves_the_others_claimed(tmp_path):
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [{"holders": ["x-1"]}, held(KEY)]})

    assert restore_record.report_claims(str(tmp_path))["claimed"] == [KEY]


def test_an_unreadable_stamp_retires_nothing(tmp_path):
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [held(KEY)]})
    stamp = tmp_path / "restore" / f"done-{restore_record.key_hash(KEY)}.yml"
    stamp.parent.mkdir()
    stamp.write_text("format: [1\n", encoding="utf-8")

    assert restore_record.report_claims(str(tmp_path))["claimed"] == [KEY]


def test_no_report_or_no_directory_claims_nothing(tmp_path):
    assert restore_record.report_claims(str(tmp_path)) == {"report": None, "claimed": [], "stamped": []}
    assert restore_record.report_claims(str(tmp_path / "gone")) == {"report": None, "claimed": [], "stamped": []}
    assert restore_record.report_claims("") == {"report": None, "claimed": [], "stamped": []}


# The lock's live-lock rule, which replication_restore_lock applies and another lock holder can apply alike.

BOOT = "8c3f0d2e-5b1a-4c7d-9e6f-1a2b3c4d5e6f"
OTHER_BOOT = "0d9c8b7a-6f5e-4d3c-8b2a-1f0e9d8c7b6a"
NAMESPACE = "pid:[4026531836]"
OTHER_NAMESPACE = "pid:[4026532718]"
LOCK = {
    "controller": "ctl01",
    "boot_id": BOOT,
    "pid_namespace": NAMESPACE,
    "pid": 1717,
    "start_ticks": 2_880_000,
    "start_time": "2027-01-15T06:00:00.000Z",
    "lease_expiry": "2027-01-15T08:00:00.000Z",
}
AT_EXPIRY = 1_800_000_000.0  # 2027-01-15T08:00:00Z
AFTER_EXPIRY = AT_EXPIRY + 1
BEFORE_EXPIRY = AT_EXPIRY - 1


@pytest.mark.parametrize(
    ("host", "boot_id", "namespace", "now", "start_ticks", "expected"),
    [
        # On the controller that took it, the same host name, boot and PID namespace, with /proc: its process's start
        # ticks alone decide, whatever the lease says.
        ("ctl01", BOOT, NAMESPACE, AFTER_EXPIRY, {1717: 2_880_000}.get, "live"),
        ("ctl01", BOOT, NAMESPACE, BEFORE_EXPIRY, {}.get, "gone"),
        ("ctl01", BOOT, NAMESPACE, BEFORE_EXPIRY, {1717: 2_880_001}.get, "gone"),
        # From another host, from this host name on another boot, or from another PID namespace on this machine, as
        # another container's: the lease alone.
        ("ctl02", BOOT, NAMESPACE, BEFORE_EXPIRY, {}.get, "live"),
        ("ctl02", BOOT, NAMESPACE, AT_EXPIRY, {1717: 2_880_000}.get, "expired"),
        ("ctl01", OTHER_BOOT, NAMESPACE, BEFORE_EXPIRY, {}.get, "live"),
        ("ctl01", OTHER_BOOT, NAMESPACE, AT_EXPIRY, {}.get, "expired"),
        ("ctl01", BOOT, OTHER_NAMESPACE, BEFORE_EXPIRY, {}.get, "live"),
        ("ctl01", BOOT, OTHER_NAMESPACE, AFTER_EXPIRY, {1717: 2_880_000}.get, "expired"),
        # On a controller without /proc, its own lock too: the lease alone.
        ("ctl01", None, None, BEFORE_EXPIRY, None, "live"),
        ("ctl01", None, None, AFTER_EXPIRY, None, "expired"),
    ],
)
def test_a_lock_lives_by_its_process_on_its_controller_and_by_its_lease_elsewhere(host, boot_id, namespace, now, start_ticks, expected):
    assert restore_record.lock_liveness(LOCK, host, boot_id, now, start_ticks, pid_namespace=namespace) == expected


def test_without_a_pid_namespace_the_rule_reads_this_processs_own():
    """As a caller written before the namespace joined the controller's identity calls it."""
    if not os.path.exists("/proc/self/ns/pid"):
        pytest.skip("no /proc on this host")
    ours = {**LOCK, "pid_namespace": os.readlink("/proc/self/ns/pid")}
    theirs = {**LOCK, "pid_namespace": OTHER_NAMESPACE}

    assert restore_record.lock_liveness(ours, "ctl01", BOOT, BEFORE_EXPIRY, {}.get) == "gone"
    assert restore_record.lock_liveness(theirs, "ctl01", BOOT, BEFORE_EXPIRY, {}.get) == "live"


@pytest.mark.parametrize(("now", "expected"), [(BEFORE_EXPIRY, "live"), (AT_EXPIRY, "expired")])
def test_a_lock_taken_without_proc_lives_by_its_lease_on_its_own_controller(now, expected):
    lock = {**LOCK, "boot_id": None, "pid_namespace": None, "start_ticks": None}

    assert restore_record.lock_liveness(lock, "ctl01", BOOT, now, {}.get, pid_namespace=NAMESPACE) == expected


@pytest.mark.parametrize(("word", "expected"), [("restore", "a restore"), ("upgrade", "an upgrade"), ("Upgrade", "an Upgrade")])
def test_a_holder_takes_its_article(word, expected):
    assert restore_record.with_article(word) == expected


def write_stamp(tmp_path, key, report, **extra):
    write_yaml(tmp_path / "restore" / f"done-{restore_record.key_hash(key)}.yml", {**restore_record.stamp(key, "r-1", report, "t"), **extra})


def test_stamped_holds_the_hash_of_each_stamp_naming_the_newest_report_whether_or_not_it_claims_the_key(tmp_path):
    write_yaml(tmp_path / "rebuild-20261102T100000.yml", {"held": [held(OTHER)]})
    write_stamp(tmp_path, KEY, "rebuild-20261102T100000.yml")
    write_stamp(tmp_path, OTHER, "rebuild-20261101T100000.yml")
    write_stamp(tmp_path, {**KEY, "target_path": "/replicas/v2"}, "rebuild-20261102T100000.yml", format=2)
    write_yaml(tmp_path / "restore" / f"{restore_record.key_hash({**KEY, 'target_path': '/replicas/recorded'})}.yml", RECORD)
    (tmp_path / "restore" / "lock").write_text("{}", encoding="utf-8")

    claims = restore_record.report_claims(str(tmp_path))

    assert claims["stamped"] == [restore_record.key_hash(KEY)]
    assert claims["claimed"] == [OTHER]


def test_with_no_report_no_stamp_is_stamped_even_one_naming_none(tmp_path):
    write_stamp(tmp_path, KEY, None)

    assert restore_record.report_claims(str(tmp_path)) == {"report": None, "claimed": [], "stamped": []}
