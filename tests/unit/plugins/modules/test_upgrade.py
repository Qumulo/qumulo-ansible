# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.upgrade, one cluster and one hop, driven through FakeQumulo's ordered script (specs/017-cluster-upgrade).

Response shapes follow research R1 and R6, and the 7.10.3A container's answers in tests/unit/fixtures/upgrade/.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

import pytest
from ansible.module_utils.testing import patch_module_args
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade as upgrade_utils
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade_record, version
from ansible_collections.qumulo.cluster.plugins.modules import upgrade
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

HOST = "qumulo.example.com"
CONNECTION = {"api_host": HOST, "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
STATUS = "/v3/upgrade/status"
BLOCKED = "/v2/upgrade/blocked"
PROTECTION = "/v1/cluster/protection/status"
REBOOT = "/v1/shutdown/reboot/status"
NODES = "/v1/cluster/nodes/"
VERIFY = "/v2/upgrade/verify-image"
PREPARE = "/v2/upgrade/prepare"
COMMIT = "/v2/upgrade/commit"
VERSION = "/v1/version"
ROOT = "/v1/files/%2F/info/attributes"
IMAGE = "https://images.example.com/qumulo/qumulo_core_7.11.0.qimg"
PATH_IMAGE = "/upgrades/qumulo_core_7.11.0.qimg"
SIGNED = IMAGE + "?X-Amz-Signature=0f1e2d3c4b5a&X-Amz-Expires=3600"
SECRET = {"userinfo": None, "query": "X-Amz-Signature=0f1e2d3c4b5a&X-Amz-Expires=3600"}
PLATFORM = "SOFTWARE_AND_PLATFORM"
SOFTWARE = "SOFTWARE_ONLY"
OLD_BUILD = "build-7.10.3"
NEW_BUILD = "build-7.11.0"
LIMIT = f"leave {HOST} out of the run with --limit"
REACH = f"Every node of {HOST} must reach {IMAGE} from its own network; check each node's route to it."
NOT_BLOCKED = {"is_blocked": False, "cluster_degraded": False, "blocked_reason": ""}
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "upgrade"
EDGE = json.loads((FIXTURES / "edge-7.10.3A.json").read_text(encoding="utf-8"))["exchanges"]


def edge(what):
    return next(e for e in EDGE if e["what"] == what)


def status(state="IDLE", image=IMAGE, target="7.11.0", kind=PLATFORM, auto_commit=False, rolling=True, count=None, overrides=None, error=None, settings=None):
    """GET /v3/upgrade/status; IDLE has no settings unless asked for, as after a new quorum (research R2)."""
    if settings is None:
        settings = state != "IDLE"
    found = {"install_path": image, "target_version": target, "upgrade_type": kind, "auto_commit": auto_commit, "do_rolling_reboot": rolling}
    found["num_nodes_to_reboot"] = count
    if overrides is not None:
        found["compatibility_overrides"] = overrides
    return {"state": f"UPGRADE_STATE_{state}", "progress": None, "settings": found if settings else None, "error_info": error}


def protection(configured=2, remaining=None, kind="PROTECTION_SYSTEM_TYPE_EC", pending=None):
    return {
        "protection_system_type": kind,
        "max_node_failures": configured,
        "remaining_node_failures": configured if remaining is None else remaining,
        "pending_configuration": pending,
    }


def reboot(state="REBOOT_IDLE", rebooted=0, total=0, error=None):
    return {
        "state": state,
        "rolling": state != "REBOOT_IDLE",
        "upgrade_initiated": state != "REBOOT_IDLE",
        "total_node_count": total,
        "rebooted_node_count": rebooted,
        "error_message": error,
    }


def nodes(count=4, offline=()):
    return [{"id": n, "node_status": "offline" if n in offline else "online"} for n in range(1, count + 1)]


def running(release, build):
    return {"revision_id": f"Qumulo Core {release}", "build_id": build, "flavor": "release", "build_date": "2026-11-01T00:00:00Z"}


@pytest.fixture
def fake(fake_api):
    """A cluster on 7.10.3; every script must read GET /v1/node/state before its first write (FR-005)."""
    found = fake_api(version="7.10.3", build_id=OLD_BUILD)
    found.serve(ROOT, {"id": "2", "path": "/"}, optional=True)
    yield found
    first = next((i for i, e in enumerate(found.log) if e.method != "GET" and e.path != "/v1/session/login"), None)
    if first is not None:
        assert any(e.path == "/v1/node/state" for e in found.log[:first]), "a write before the node state was read"


def on(fake, release, build=None):
    fake.version.update(running(release, build or f"build-{release}"))


def expect_reads(fake, found=None, blocked=None, protect=None, reboots=None, nodes_found=None):
    """The reads before any write, in the module's order."""
    fake.expect("GET", STATUS, response=found or status())
    fake.expect("GET", BLOCKED, response=blocked or NOT_BLOCKED)
    fake.expect("GET", PROTECTION, response=protect or protection())
    fake.expect("GET", REBOOT, response=reboots or reboot())
    fake.expect("GET", NODES, response=nodes_found or nodes())


def expect_prepare(fake, body=None, query="", status_code=202, response=None, raises=None, **sent):
    path = PREPARE + (f"?{query}" if query else "")
    body = body or {"image_path": IMAGE, "auto_commit": False, "do_rolling_reboot": True, **sent}
    fake.expect(
        "POST", path, body=body, status=status_code, response=response if response is not None else {"monitor_uri": "/v2/upgrade/status"}, raises=raises
    )


def expect_prepared(fake, **kw):
    fake.expect("GET", STATUS, response=status("PREPARING", **kw))
    fake.expect("GET", STATUS, response=status("PREPARED", **kw))


def expect_commit(fake, status_code=202, response=None, raises=None):
    fake.expect("POST", COMMIT, status=status_code, response=response if response is not None else {"monitor_uri": "/v2/upgrade/status"}, raises=raises)


def expect_rolling_done(fake, release="7.11.0", build=NEW_BUILD):
    fake.expect("GET", VERSION, response=running(release, build))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 0, 4))


def args(**kw):
    return {**CONNECTION, "image": IMAGE, **kw}


# A refusal names the setting the user has: under the cluster_upgrade role, which always sets host_name, the role's and
# the inventory's settings; on its own, the module's options (User Story 5's refusals).
UNDER_ROLE = pytest.mark.parametrize("under_role", [False, True], ids=["on-its-own", "under-the-role"])


def run_as(under_role):
    return {"host_name": HOST} if under_role else {}


def wait_setting(under_role):
    return "cluster_upgrade_wait_timeout" if under_role else "wait_timeout"


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def requests(fake, path):
    return [e for e in fake.log if e.path.split("?")[0] == path]


def assert_ok(result):
    assert not result.get("failed"), result.get("msg")


# The module as an Action


def test_at_the_target_with_the_reboots_done_plans_nothing_and_changes_nothing(fake, run_module):
    on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake)

    result = run_module(upgrade, args(state="rebooted"))

    assert_ok(result)
    assert result["changed"] is False
    assert "plan" not in result
    assert result["cluster_uuid"] == CLUSTER_ID
    assert result["from"] == {"version": "7.11.0", "build": NEW_BUILD}
    assert writes(fake) == []


def test_check_mode_returns_the_plan_without_a_write(fake, run_module, fake_clock):
    expect_reads(fake)

    result = run_module(upgrade, args(state="rebooted"), check_mode=True, diff=True)

    assert_ok(result)
    assert result["changed"] is True
    assert result["plan"]["phases"] == ["prepare", "commit", "reboots"]
    assert result["plan"]["from"] == {"version": "7.10.3", "build": OLD_BUILD, "state": "IDLE"}
    assert result["plan"]["to"] == {"version": "7.11.0", "build": None}
    assert result["diff"] == {"before": {}, "after": result["plan"]}
    assert writes(fake) == []
    assert fake_clock.sleeps == []


def test_a_readiness_check_of_a_cluster_already_prepared_changes_nothing(fake, run_module):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)

    result = run_module(upgrade, args(state="prepared", ready_to_commit=True))

    assert_ok(result)
    assert result["changed"] is False
    assert result["ready"] is True
    assert result["prepared"] is False
    assert result["from"] == {"version": "7.10.3", "build": OLD_BUILD}
    assert result["cluster_uuid"] == CLUSTER_ID
    assert writes(fake) == []


# Each state from IDLE


def test_state_verified_sends_one_verify_with_its_timeout_and_installs_nothing(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, body={"image_path": IMAGE}, response={"target_version": "7.11.0", "upgrade_type": PLATFORM, "error": None, "error_class": None})

    result = run_module(upgrade, args(state="verified"))

    assert_ok(result)
    assert result["changed"] is False
    assert result["upgrade_type"] == PLATFORM
    assert set(result["phases"]) == {"verify"}
    assert requests(fake, VERIFY)[0].timeout == 2700


def test_state_prepared_from_idle_prepares_and_waits_for_prepared(fake, run_module, fake_clock):
    expect_reads(fake)
    expect_prepare(fake)
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert result["changed"] is True
    assert result["prepared"] is True
    assert result["commit_sent"] is False
    assert result["status"]["state"] == "UPGRADE_STATE_PREPARED"
    assert result["upgrade_type"] == PLATFORM
    assert requests(fake, PREPARE)[0].timeout == 2700
    assert result["phases"]["prepare"]["ended"]
    assert fake_clock.sleeps == [5.0]


def test_state_committed_from_idle_prepares_then_commits_at_the_first_rebooting_quorum(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake)
    expect_prepared(fake)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["changed"] is True
    assert result["commit_sent"] is True
    assert result["to"] == {"version": "7.11.0", "build": NEW_BUILD}
    assert result["reboot"] == {"mode": "rolling", "set_size": None, "seconds": None}
    assert all(result["phases"][phase]["ended"] for phase in ("prepare", "ready", "commit"))
    assert requests(fake, COMMIT)[0].timeout is None
    assert len(requests(fake, ROOT)) == 1


def test_a_done_commit_whose_file_system_never_answers_in_the_time_left_is_done_with_a_note(fake, run_module, fake_clock):
    fake.serve(ROOT, {"error_class": "unavailable", "description": "the API's node is rebooting"}, status=503)
    expect_commit_from_prepared(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args(wait_timeout=60))

    assert_ok(result)
    assert (result["changed"], result["to"]) == (True, {"version": "7.11.0", "build": NEW_BUILD})
    assert any(note.startswith(f"{HOST}'s commit is done; its file system did not answer in the 60 seconds left of wait_timeout") for note in result["notes"])
    assert len(requests(fake, ROOT)) > 1


def test_each_phase_starts_a_deadline_of_its_own(fake, run_module, fake_clock):
    expect_reads(fake)
    expect_prepare(fake)
    for _poll in range(3):
        fake.expect("GET", STATUS, response=status("PREPARING"))
    fake.expect("GET", STATUS, response=status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake)
    for _poll in range(3):
        fake.expect("GET", VERSION, raises=URLError(ConnectionRefusedError(111, "refused")))
    expect_rolling_done(fake)

    result = run_module(upgrade, args(wait_timeout=16))

    assert_ok(result)
    assert fake_clock.sleeps == [5.0] * 6


def test_every_returned_url_and_server_text_is_redacted(fake, run_module):
    old = "https://old.example.com/qumulo/qumulo_core_7.10.9.qimg?sig=feedface"
    expect_reads(fake, status("PREPARED", image=old, error=None))
    expect_prepare(fake, body={"image_path": SIGNED, "auto_commit": False, "do_rolling_reboot": True})
    fake.expect("GET", STATUS, response=status("PREPARING", image=SIGNED))
    fake.expect("GET", STATUS, response=status("PREPARED", image=SIGNED))

    result = run_module(upgrade, args(image=IMAGE, image_secret=SECRET, state="prepared"), diff=True)

    assert_ok(result)
    assert result["image_redacted"] == IMAGE
    assert result["status"]["settings"]["install_path"] == IMAGE
    assert any("https://old.example.com/qumulo/qumulo_core_7.10.9.qimg" in note for note in result["notes"])
    shown = json.dumps({k: v for k, v in result.items() if k != "invocation"})
    assert "0f1e2d3c4b5a" not in shown
    assert "feedface" not in shown


def test_a_server_text_that_echoes_the_full_url_has_it_replaced_exactly_whatever_its_query_holds(fake, run_module):
    full = IMAGE + "?sig=ab,cd"
    expect_reads(fake)
    fake.expect(
        "POST", VERIFY, body={"image_path": full}, status=500, response={"error_class": "upgrade_error", "description": f"Download from {full},retrying"}
    )

    result = run_module(upgrade, args(image_secret={"query": "sig=ab,cd"}, state="verified"))

    assert result["msg"] == f"{HOST}: POST {VERIFY} failed: HTTP 500 upgrade_error: Download from {IMAGE},retrying. {REACH}"


def test_version_and_build_are_taken_as_strings():
    assert upgrade.ARGUMENT_SPEC["version"]["type"] == "str"
    assert upgrade.ARGUMENT_SPEC["build"]["type"] == "str"


# The image and its secret (FR-002, FR-032)


def test_the_image_and_its_secret_are_joined_for_the_request(fake, run_module):
    expect_reads(fake)
    expect_prepare(
        fake, body={"image_path": "https://reader:pw@images.example.com/qumulo/qumulo_core_7.11.0.qimg?sig=1", "auto_commit": False, "do_rolling_reboot": True}
    )
    full = "https://reader:pw@images.example.com/qumulo/qumulo_core_7.11.0.qimg?sig=1"
    expect_prepared(fake, image=full)

    result = run_module(upgrade, args(image_secret={"userinfo": "reader:pw", "query": "sig=1"}, state="prepared"))

    assert_ok(result)
    assert result["status"]["settings"]["install_path"] == IMAGE


def run_main(capsys, module_args):
    """main() through AnsibleModule's own exit_json and fail_json, as ansible-core 2.21 masks no_log values in them."""
    with patch_module_args({**module_args, "_ansible_check_mode": False, "_ansible_diff": False}), pytest.raises(SystemExit):
        upgrade.main()
    return json.loads(capsys.readouterr().out)


def test_only_the_secret_parts_are_masked_through_exit_json(fake, capsys):
    full = "https://reader:hunter2@images.example.com/qumulo/qumulo_core_7.11.0.qimg?X-Amz-Signature=0f1e2d3c4b5a"
    expect_reads(fake)
    expect_prepare(fake, body={"image_path": full, "auto_commit": False, "do_rolling_reboot": True})
    expect_prepared(fake, image=full)

    out = run_main(capsys, args(image_secret={"userinfo": "reader:hunter2", "query": "X-Amz-Signature=0f1e2d3c4b5a"}, state="prepared"))

    # From ansible-core 2.22, exit_json hands the values the module registered as secrets to the controller in
    # _ansible_new_secrets, which the controller removes from the result and masks everywhere else; every other field
    # must hold neither secret part on every release.
    registered = out.pop("_ansible_new_secrets", None)
    if registered is not None:
        assert {"reader:hunter2", "X-Amz-Signature=0f1e2d3c4b5a"} <= set(registered)
    text = json.dumps(out)
    assert "hunter2" not in text and "0f1e2d3c4b5a" not in text
    assert out["image_redacted"] == IMAGE
    assert out["status"]["settings"]["install_path"] == IMAGE


@pytest.mark.parametrize("image", [IMAGE, PATH_IMAGE])
def test_below_2_22_a_plain_url_or_a_path_reads_in_full_through_exit_json(fake, capsys, image):
    expect_reads(fake, status("PREPARED", image=image, target="7.11.1"))

    failed = run_main(capsys, args(image=image, state="prepared", prepare=False, version="7.11.0"))
    expect_reads(fake, status("PREPARED", image=image))
    done = run_main(capsys, args(image=image, state="prepared", prepare=False))

    assert failed["failed"] is True
    assert failed["msg"].startswith(f"{HOST}: the image {image} holds Qumulo Core 7.11.1")
    assert done["image_redacted"] == image
    assert done["status"]["settings"]["install_path"] == image


def test_from_2_22_each_secret_part_is_registered(fake, run_module, monkeypatch):
    registered = []
    monkeypatch.setattr(upgrade_utils, "register_secret", registered.append)
    expect_reads(fake, status("PREPARED", image="https://u:p@images.example.com/qumulo/qumulo_core_7.11.0.qimg?sig=1"))

    result = run_module(upgrade, args(image_secret={"userinfo": "u:p", "query": "sig=1"}, state="prepared"))

    assert_ok(result)
    assert registered == ["u:p", "sig=1"]


@UNDER_ROLE
@pytest.mark.parametrize(
    "image",
    [
        SIGNED,
        "https://reader:pw@images.example.com/qumulo/qumulo_core_7.11.0.qimg",
        "https://reader:pa/ss@images.example.com/qumulo/qumulo_core_7.11.0.qimg",
        "https://reader:pa?ss@images.example.com/qumulo/qumulo_core_7.11.0.qimg",
        "https://reader:pa#ss@images.example.com/qumulo/qumulo_core_7.11.0.qimg",
    ],
    ids=["query", "userinfo", "unencoded-slash", "unencoded-question-mark", "unencoded-hash"],
)
def test_an_image_that_carries_a_query_string_or_user_information_is_f33(fake, run_module, image, under_role):
    result = run_module(upgrade, args(image=image, **run_as(under_role)))

    assert result["failed"] is True
    filled = ", which the playbook fills from qumulo_upgrade_images," if under_role else ""
    assert result["msg"] == (
        f"{HOST}: the image {IMAGE} carries a query string or user information in image. Pass those parts in image_secret{filled} and the rest in image."
    )
    assert fake.log == []


@pytest.mark.parametrize(
    "image", ["upgrades/qumulo_core_7.11.0.qimg", "ftp://images.example.com/qumulo_core_7.11.0.qimg", "https:/images.example.com/qumulo_core_7.11.0.qimg"]
)
def test_an_image_neither_a_url_nor_an_absolute_path_is_f2(fake, run_module, image):
    result = run_module(upgrade, args(image=image))

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == (
        f"{HOST}: the image {image} is neither an http:// or https:// URL nor an absolute path. Give a URL every node can "
        "reach, or an absolute path in the cluster's file system."
    )
    assert fake.log == []


def test_f2_prints_a_url_of_another_scheme_without_its_user_information_or_query(fake, run_module):
    result = run_module(upgrade, args(image="ftp://reader:hunter2@images.example.com/qumulo_core_7.11.0.qimg?sig=0f1e2d"))

    shown = "ftp://images.example.com/qumulo_core_7.11.0.qimg"
    assert result["msg"] == (
        f"{HOST}: the image {shown} is neither an http:// or https:// URL nor an absolute path. Give a URL every node can "
        "reach, or an absolute path in the cluster's file system."
    )
    assert result["image_redacted"] == shown


def test_a_reboot_set_size_of_0_is_refused_before_any_request(fake, run_module):
    result = run_module(upgrade, args(reboot_set_size=0))

    assert result["failed"] is True
    assert "reboot_set_size must be at least 1" in result["msg"]
    assert fake.log == []


# The commit record (FR-034)


def record_text(state="pending", uuid=CLUSTER_ID, build=OLD_BUILD, target="7.11.0", error=None):
    fields = {"state": state, "cluster_uuid": uuid, "build": build, "target_version": target, "time": "2026-11-04T10:00:00+00:00"}
    if error:
        fields["error_info"] = error
    return upgrade_record.format_record(fields)


@pytest.fixture
def record(tmp_path):
    return tmp_path / "artifacts" / "qumulo01" / "upgrade" / "commit.yml"


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_node_state_is_read_before_any_other_request(fake, run_module):
    expect_reads(fake, status("PREPARED"))

    run_module(upgrade, args(state="prepared"))

    assert fake.log[0].path == "/v1/node/state"


@pytest.mark.parametrize("check_mode", [False, True])
def test_an_unfinished_record_refuses_f19_before_any_write_and_is_left_as_it_is(fake, run_module, record, check_mode):
    put(record, record_text("unfinished", error="a node left the quorum"))
    expect_reads(fake)

    result = run_module(upgrade, args(commit_record=str(record)), check_mode=check_mode)

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == (
        f"{HOST}: a commit record, {record}, shows that its commit to 7.11.0 on 2026-11-04T10:00:00+00:00 did not finish, and "
        f"some nodes may be armed. Nothing was sent. Contact Qumulo Care. Once they have cleared {HOST}, remove the record. To "
        f"upgrade the rest of the fleet meanwhile, {LIMIT}."
    )
    assert record.read_text(encoding="utf-8") == record_text("unfinished", error="a node left the quorum")
    assert writes(fake) == []


def test_a_record_of_another_cluster_at_the_target_makes_a_plan_whose_run_retires_it(fake, run_module, record):
    on(fake, "7.11.0", NEW_BUILD)
    put(record, record_text(uuid="00000000-0000-4000-8000-0000000000aa"))
    expect_reads(fake)

    result = run_module(upgrade, args(commit_record=str(record)))

    assert_ok(result)
    assert result["changed"] is False
    assert result["plan"]["record"]["action"] == "retire"
    assert not record.exists()
    assert any(w.startswith(f"{HOST}: retired the commit record {record}") for w in result["warnings"])


def test_check_mode_predicts_a_retirement_and_removes_nothing(fake, run_module, record):
    on(fake, "7.11.0", NEW_BUILD)
    put(record, record_text(uuid="00000000-0000-4000-8000-0000000000aa"))
    expect_reads(fake)

    result = run_module(upgrade, args(commit_record=str(record)), check_mode=True)

    assert result["plan"]["record"]["action"] == "retire"
    assert record.exists()


def test_a_pending_record_over_prepared_on_its_build_is_dropped(fake, run_module, record):
    put(record, record_text())
    expect_reads(fake, status("PREPARED"))

    result = run_module(upgrade, args(commit_record=str(record), state="prepared"))

    assert_ok(result)
    assert result["plan"]["record"]["action"] == "drop"
    assert not record.exists()
    assert any("dropped the commit record" in w for w in result["warnings"])


def test_a_pending_record_over_committing_is_f30(fake, run_module, record):
    put(record, record_text())
    expect_reads(fake, status("COMMITTING"))

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"].startswith(f"{HOST}'s commit to 7.11.0, recorded at {record}, is under way (it reads COMMITTING).")
    assert writes(fake) == []


def test_a_record_that_cannot_be_read_is_f31(fake, run_module, record):
    record.mkdir(parents=True)
    expect_reads(fake)

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == upgrade_record.unreadable_record(HOST, str(record), "Is a directory")


def spy_record_writes(monkeypatch, fake):
    """Each write_record call with the record's text and how many requests had been logged when it was made."""
    written = []
    write = upgrade_record.write_record

    def spy(path, fields):
        write(path, fields)
        written.append((Path(path).read_text(encoding="utf-8"), len(fake.log)))

    monkeypatch.setattr(upgrade_utils, "write_record", spy)
    return written


def test_the_pending_record_is_written_just_before_the_commit_request_and_removed_once_done(fake, run_module, record, monkeypatch):
    written = spy_record_writes(monkeypatch, fake)
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args(commit_record=str(record)))

    assert_ok(result)
    ((text, logged),) = written
    fields = upgrade_record.parse_record(text)
    assert (fields["state"], fields["cluster_uuid"], fields["build"], fields["target_version"]) == ("pending", CLUSTER_ID, OLD_BUILD, "7.11.0")
    assert fake.log[logged - 1].path == BLOCKED
    assert (fake.log[logged].method, fake.log[logged].path) == ("POST", COMMIT)
    assert not record.exists()


@pytest.mark.parametrize(
    ("status_code", "error_class", "description"),
    [
        (409, "upgrade_commit_when_not_prepared_error", "An upgrade has not yet been prepared."),
        (503, "upgrade_without_full_quorum_error", "Full quorum is required for upgrades."),
    ],
)
def test_an_answered_commit_refusal_removes_the_record_and_sends_no_commit(fake, run_module, record, status_code, error_class, description):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, status_code, {"error_class": error_class, "description": description})

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["failed"] is True
    assert description in result["msg"]
    assert result["commit_sent"] is False
    assert result["changed"] is False
    assert not record.exists()


def test_a_record_that_cannot_be_written_fails_naming_its_path_and_sends_no_commit(fake, run_module, record, monkeypatch):
    def refused(**kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(upgrade_record.tempfile, "mkstemp", refused)
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["failed"] is True
    assert result["msg"] == (
        f"{HOST}: the commit record {record} could not be written (Permission denied). Nothing was committed: make its "
        "directory writable, then run the upgrade again."
    )
    assert (result["changed"], result["commit_sent"]) == (False, False)
    assert writes(fake) == []


def test_a_crash_between_the_record_and_the_answer_leaves_it_pending(fake, run_module, record):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, raises=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_module(upgrade, args(commit_record=str(record)))

    assert upgrade_record.parse_record(record.read_text(encoding="utf-8"))["state"] == "pending"


def test_the_record_is_staged_flushed_put_in_place_and_its_directory_flushed(record, monkeypatch):
    synced, replaced = [], []
    fsync, replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(stat.S_ISDIR(os.fstat(fd).st_mode)), fsync(fd)))
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((a, b)), replace(a, b)))

    upgrade_record.write_record(str(record), {"state": "pending", "cluster_uuid": CLUSTER_ID, "build": OLD_BUILD, "target_version": "7.11.0", "time": "t"})

    assert synced == [False, True]
    assert [Path(b) for _a, b in replaced] == [record]
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert stat.S_IMODE(record.parent.stat().st_mode) == 0o700
    assert upgrade_record.parse_record(record.read_text(encoding="utf-8"))["state"] == "pending"


# The commit's outcome (FR-009)

UNANSWERED = URLError(ConnectionRefusedError(111, "Connection refused"))


def expect_commit_from_prepared(fake, found=None, protect=None, blocked=None, **reads):
    """A PREPARED cluster's reads, the commit's re-check and its 202."""
    found = found or status("PREPARED")
    expect_reads(fake, found, protect=protect, **reads)
    fake.expect("GET", BLOCKED, response=blocked or NOT_BLOCKED)
    if (found["settings"] or {}).get("upgrade_type") == PLATFORM and not found["settings"]["do_rolling_reboot"]:
        fake.expect("GET", PROTECTION, response=protect or protection())
    expect_commit(fake)


def test_a_software_only_commit_is_done_with_every_node_online_on_the_new_build(fake, run_module):
    expect_commit_from_prepared(fake, status("PREPARED", kind=SOFTWARE))
    fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", NODES, response=nodes(offline=(3,)))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", NODES, response=nodes())

    result = run_module(upgrade, args(state="rebooted"))

    assert_ok(result)
    assert result["to"] == {"version": "7.11.0", "build": NEW_BUILD}
    assert result["reboot"] == {"mode": "none", "set_size": None, "seconds": 0}


def test_a_rolling_platform_commit_is_done_at_rebooting_with_a_node_offline(fake, run_module):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 0, 4))

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["commit_sent"] is True
    assert requests(fake, NODES)[-1].background is False  # only the reads before any write; the done check needs none


def test_an_all_at_once_platform_commit_is_done_with_every_node_online_on_the_new_build(fake, run_module):
    expect_commit_from_prepared(fake, status("PREPARED", rolling=False), protect=protection(1))
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("COMMITTED", rolling=False))
    for _poll in range(3):
        fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", NODES, response=nodes())

    result = run_module(upgrade, args(reboot="all"))

    assert_ok(result)
    assert result["reboot"]["mode"] == "all"
    assert result["downtime_seconds"] == 15.0


@pytest.mark.parametrize("kind", [PLATFORM, SOFTWARE])
def test_a_new_build_reading_idle_and_reboot_idle_with_every_node_online_is_done_for_any_type(fake, run_module, kind):
    expect_commit_from_prepared(fake, status("PREPARED", kind=kind))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", NODES, response=nodes())

    result = run_module(upgrade, args())

    assert_ok(result)


def test_a_new_build_other_than_the_declared_one_fails_at_once_naming_both(fake, run_module):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.11.0", "build-other"))

    result = run_module(upgrade, args(build=NEW_BUILD))

    assert result["failed"] is True
    assert result["changed"] is True
    assert "maybe_applied" not in result
    assert result["msg"] == (
        f"{HOST} runs Qumulo Core 7.11.0 build build-other, not the declared build {NEW_BUILD}. The commit is done and cannot be "
        "undone. Check the build in qumulo_upgrade_images' entry for 7.11.0, and the image, with Qumulo Care."
    )


def test_a_commit_done_on_another_build_than_declared_removes_its_record(fake, run_module, record):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.11.0", "build-other"))

    result = run_module(upgrade, args(build=NEW_BUILD, commit_record=str(record)))

    assert result["msg"].startswith(f"{HOST} runs Qumulo Core 7.11.0 build build-other, not the declared build {NEW_BUILD}.")
    assert not record.exists()


def idle_failure(record_clause, error="no error"):
    return (
        f"{HOST}: the commit did not finish. The cluster still runs 7.10.3 and its upgrade status is IDLE ({error}). Some nodes may "
        "be armed to start 7.11.0 at their next restart, and Qumulo Core's own guidance for this state is manual intervention. "
        f"Contact Qumulo Care before running anything else on {HOST}. {record_clause}"
    )


# Pins QUIRK-091: IDLE after the restart reads as after success, so the build decides.
def test_an_idle_status_after_the_restart_is_decided_by_the_build(fake, run_module, record):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE", error="node 2 left the quorum"))
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["failed"] is True
    assert (result["changed"], result["maybe_applied"], result["unfinished_commit"], result["commit_sent"]) == (True, True, True, True)
    assert "final" not in result
    assert result["msg"] == idle_failure(
        f"The collection has recorded this at {record}, and the next run stops on it until Qumulo Care has cleared {HOST} and the "
        f"record is removed. To upgrade the rest of the fleet meanwhile, {LIMIT}.",
        "node 2 left the quorum",
    )
    fields = upgrade_record.parse_record(record.read_text(encoding="utf-8"))
    assert (fields["state"], fields["error_info"]) == ("unfinished", "node 2 left the quorum")


def test_an_unfinished_record_that_cannot_be_written_keeps_the_idle_failure_and_names_its_path(fake, run_module, record, monkeypatch):
    def pending_only(path, fields):
        if fields["state"] != "pending":
            raise PermissionError(13, "Permission denied")
        write(path, fields)

    write = upgrade_record.write_record
    monkeypatch.setattr(upgrade_utils, "write_record", pending_only)
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE", error="node 2 left the quorum"))
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["failed"] is True
    assert (result["changed"], result["maybe_applied"], result["unfinished_commit"], result["commit_sent"]) == (True, True, True, True)
    assert result["msg"] == idle_failure(
        f"The collection could not record this at {record} (Permission denied), but the record written there before the commit "
        f"stays, and the next run stops on it until Qumulo Care has cleared {HOST} and the record is removed. To upgrade the rest "
        f"of the fleet meanwhile, {LIMIT}.",
        "node 2 left the quorum",
    )
    assert upgrade_record.parse_record(record.read_text(encoding="utf-8"))["state"] == "pending"


def test_without_commit_record_none_is_written_and_the_idle_failure_says_so(fake, run_module):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))

    result = run_module(upgrade, args())

    assert result["msg"] == idle_failure(
        f"No commit record was written, since commit_record was not set; run the next upgrade of {HOST} only once Qumulo Care has cleared it."
    )


def test_an_idle_read_that_straddles_the_restart_is_read_again_with_the_version(fake, run_module):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)


def f32(record_clause, state="COMMITTED", seconds=10, under_role=False):
    return (
        f"{HOST}'s commit to 7.11.0 was sent, and after {seconds} seconds it reads {state} on build {OLD_BUILD}. Nothing more was "
        f"sent. {record_clause} Run the upgrade again once {HOST} reads 7.11.0, or raise {wait_setting(under_role)}."
    )


@UNDER_ROLE
def test_an_all_at_once_commit_still_waiting_for_reprotect_at_wait_timeout_is_f32(fake, run_module, record, under_role):
    object_backed = protection(1, kind="PROTECTION_SYSTEM_TYPE_OBJECT_BACKED")
    expect_commit_from_prepared(fake, status("PREPARED", rolling=False), protect=object_backed)
    for _poll in range(3):
        fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
        fake.expect("GET", STATUS, response=status("COMMITTED", rolling=False))

    result = run_module(upgrade, args(reboot="all", wait_timeout=10, commit_record=str(record), **run_as(under_role)))

    assert result["failed"] is True
    assert (result["changed"], result["maybe_applied"], result["commit_sent"]) == (True, True, True)
    clause = f"The record {record} stays: the next run waits for the commit to finish, or retires the record."
    assert result["msg"] == f32(clause, under_role=under_role)
    assert upgrade_record.parse_record(record.read_text(encoding="utf-8"))["state"] == "pending"


def test_f32_without_commit_record_says_none_was_written(fake, run_module):
    expect_commit_from_prepared(fake)
    for _poll in range(3):
        fake.expect("GET", VERSION, raises=UNANSWERED)

    result = run_module(upgrade, args(wait_timeout=10))

    assert result["msg"] == f32("No commit record was written, since commit_record was not set.", state="PREPARED")


@pytest.mark.parametrize(
    "lost",
    [
        {"raises": URLError(ConnectionResetError(104, "reset"))},
        {"status_code": 502, "response": b"<html>bad gateway</html>"},
        {"status_code": 504, "response": b"<html>gateway timeout</html>"},
    ],
    ids=["lost", "502", "504"],
)
def test_a_lost_commit_answer_is_read_back_from_the_build(fake, run_module, lost):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, **lost)
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["commit_sent"] is True
    assert len(requests(fake, COMMIT)) == 1


def test_a_lost_commit_found_still_prepared_with_the_api_answering_is_sent_once_more_and_its_409_is_a_wait(fake, run_module):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, raises=URLError(ConnectionResetError(104, "reset")))
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("PREPARED"))
    expect_commit(fake, 409, {"error_class": "upgrade_already_in_progress_error", "description": "A previous upgrade is already being committed."})
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("PREPARED"))
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert len(requests(fake, COMMIT)) == 2


def test_a_lost_commit_is_not_sent_again_once_the_api_has_stopped_answering(fake, run_module):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, raises=URLError(ConnectionResetError(104, "reset")))
    fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("PREPARED"))
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert len(requests(fake, COMMIT)) == 1


def test_the_commit_wait_reads_the_version_past_its_cache(fake, run_module):
    expect_commit_from_prepared(fake)
    expect_rolling_done(fake)

    run_module(upgrade, args())

    assert [e.background for e in requests(fake, VERSION)] == [True, False]


def test_downtime_counts_unanswered_polls_until_the_new_build_answers_and_not_the_api_nodes_reboot(fake, run_module):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IDLE"))
    fake.expect("GET", NODES, response=nodes(offline=(1,)))
    fake.expect("GET", VERSION, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", NODES, response=nodes(offline=(1,)))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", NODES, response=nodes(offline=(1,)))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", NODES, response=nodes(offline=(1,)))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", NODES, response=nodes(offline=(1,)))
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 1, 4))

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["downtime_seconds"] == 10.0


# The waits before any prepare (FR-005, FR-025a)


def test_a_reboot_under_way_is_waited_for_before_the_prepare_and_its_degraded_preflight_is_the_reboot(fake, run_module, fake_clock):
    degraded = {"is_blocked": True, "cluster_degraded": True, "blocked_reason": "Cannot upgrade due to offline nodes: 3."}
    expect_reads(fake, status("REBOOTING", image="/old/qumulo_core_7.10.3.qimg", target="7.10.3"), blocked=degraded, reboots=reboot("REBOOT_IN_PROGRESS", 2, 4))
    fake.expect("GET", STATUS, response=status("REBOOTING", image="/old/qumulo_core_7.10.3.qimg", target="7.10.3"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 3, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IDLE"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_prepare(fake)
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert fake_clock.sleeps == [30.0, 30.0, 5.0]


def test_the_reboot_status_is_read_only_while_a_reboot_is_under_way(fake, run_module, fake_clock):
    expect_reads(fake, status("REBOOTING", target="7.10.3"), reboots=reboot("REBOOT_IDLE"))
    fake.expect("GET", STATUS, response=status("REBOOTING", target="7.10.3"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IDLE"))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_prepare(fake)
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert len(requests(fake, REBOOT)) == 2


def f25():
    return (
        f'{HOST}\'s reboot is paused, and this run did not pause it. Follow "Resume a paused reboot" in the '
        "collection's docs/how-to/resume-an-upgrade.md, then run the upgrade again."
    )


def f26():
    return f"{HOST}'s reboot stopped on an error: node 3 did not come back. Contact Qumulo Care before running anything else on {HOST}."


@pytest.mark.parametrize(("state", "message"), [("REBOOT_PAUSED", f25), ("REBOOT_PAUSED_DUE_TO_ERROR", f26)])
@pytest.mark.parametrize("at_target", [False, True])
def test_a_paused_reboot_fails_at_once(fake, run_module, fake_clock, state, message, at_target):
    if at_target:
        on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake, status("REBOOTING"), reboots=reboot(state, 1, 4, error="node 3 did not come back"))

    result = run_module(upgrade, args())

    assert result["failed"] is True
    assert result["msg"] == message()
    assert fake_clock.sleeps == []


def test_a_reboot_paused_while_waited_for_fails_at_once(fake, run_module):
    on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake, status("REBOOTING"), reboots=reboot("REBOOT_IN_PROGRESS", 1, 4))
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_PAUSED", 2, 4))

    result = run_module(upgrade, args(state="rebooted"))

    assert result["msg"] == f25()


@UNDER_ROLE
def test_a_reboot_that_outlasts_reboot_timeout_is_f24(fake, run_module, under_role):
    expect_reads(fake, status("REBOOTING", target="7.10.3"), reboots=reboot("REBOOT_IN_PROGRESS", 1, 4))
    for count in (2, 3):
        fake.expect("GET", STATUS, response=status("REBOOTING", target="7.10.3"))
        fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", count, 4))

    result = run_module(upgrade, args(reboot_timeout=60, **run_as(under_role)))

    assert result["failed"] is True
    setting = "cluster_upgrade_reboot_timeout" if under_role else "reboot_timeout"
    assert result["msg"] == (
        f"{HOST} still reads REBOOTING after 60 seconds, with 3 of 4 nodes rebooted. Wait for the reboot to finish, then run the "
        f"upgrade again, or raise {setting}."
    )
    assert writes(fake) == []


def test_a_degraded_preflight_outside_a_reboot_fails_at_once_as_f5(fake, run_module, fake_clock):
    degraded = {"is_blocked": True, "cluster_degraded": True, "blocked_reason": "Cannot upgrade due to failed or missing disks: 2."}
    expect_reads(fake, blocked=degraded)

    result = run_module(upgrade, args())

    assert result["failed"] is True
    assert result["msg"] == (
        f"{HOST} cannot be upgraded now: Cannot upgrade due to failed or missing disks: 2. Bring the node or disk back into the "
        "cluster, then run the upgrade again."
    )
    assert fake_clock.sleeps == []


@UNDER_ROLE
def test_a_blocked_preflight_is_waited_out_and_then_f5(fake, run_module, under_role):
    work = {
        "is_blocked": True,
        "cluster_degraded": False,
        "blocked_reason": "In progress background upgrade work needs to complete before the software can be upgraded.",
    }
    expect_reads(fake, blocked=work)
    for _poll in range(2):
        fake.expect("GET", BLOCKED, response=work)

    result = run_module(upgrade, args(wait_timeout=10, **run_as(under_role)))

    assert result["failed"] is True
    assert result["msg"] == (
        f"{HOST} still reads blocked after 10 seconds: In progress background upgrade work needs to complete before the software "
        "can be upgraded. On a large file system the background work can take longer; run the upgrade again, which picks up where "
        f"it stopped, or raise {wait_setting(under_role)}."
    )
    assert writes(fake) == []


# Idempotence (FR-004)


def test_at_the_target_build_still_rebooting_the_reboot_is_waited_for(fake, run_module, fake_clock):
    on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake, status("REBOOTING"), reboots=reboot("REBOOT_IN_PROGRESS", 2, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IDLE"))

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["changed"] is False
    assert result["plan"]["phases"] == ["reboots"]
    assert writes(fake) == []
    assert fake_clock.sleeps == [30.0]


def test_prepared_with_every_field_matching_is_committed_without_a_prepare(fake, run_module):
    expect_commit_from_prepared(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["prepared"] is False
    assert requests(fake, PREPARE) == []


@pytest.mark.parametrize(
    "found",
    [
        status("PREPARED", rolling=False),
        status("PREPARED", count=1),
        status("PREPARED", auto_commit=True),
        status("PREPARED", image="https://images.example.com/qumulo/old/qumulo_core_7.11.0.qimg"),
    ],
    ids=["mode", "count", "auto_commit", "image"],
)
def test_prepared_with_other_settings_or_another_image_is_prepared_again_noting_the_old_image(fake, run_module, found):
    expect_reads(fake, found)
    expect_prepare(fake)
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert result["prepared"] is True
    assert result["notes"] == [f"{HOST} was PREPARED with {found['settings']['install_path']}, which this run prepares again"]


@pytest.mark.parametrize(
    ("source", "allow", "overrides", "again"),
    [
        ("7.11.0", False, ["ALLOW_UPGRADE_FROM_LIMITED_RELEASE"], True),
        ("7.11.0A", True, [], True),
        ("7.11.0A", True, ["ALLOW_UPGRADE_FROM_LIMITED_RELEASE"], False),
        ("7.11.0", False, [], False),
    ],
    ids=["one-not-sent", "one-sent-missing", "the-one-sent", "none"],
)
def test_on_7_11_0_the_status_compatibility_overrides_must_be_the_ones_sent(fake, run_module, source, allow, overrides, again):
    on(fake, source)
    expect_reads(fake, status("PREPARED", image="/upgrades/qumulo_core_7.11.1.qimg", target="7.11.1", overrides=overrides))
    if again:
        query = "compatibility_overrides=allow_upgrade_from_limited_release" if allow else ""
        expect_prepare(fake, body={"image_path": "/upgrades/qumulo_core_7.11.1.qimg", "auto_commit": False, "do_rolling_reboot": True}, query=query)
        expect_prepared(fake, image="/upgrades/qumulo_core_7.11.1.qimg", target="7.11.1", overrides=["ALLOW_UPGRADE_FROM_LIMITED_RELEASE"] if allow else [])

    result = run_module(upgrade, args(image="/upgrades/qumulo_core_7.11.1.qimg", state="prepared", allow_upgrade_from_limited_release=allow))

    assert_ok(result)
    assert result["prepared"] is again


BOOLEAN_WAIVER = {"allow_upgrade_from_limited_release": True, "build": NEW_BUILD}


def test_a_call_that_may_prepare_with_the_boolean_waiver_accepts_a_matching_prepared_and_sends_no_verify_or_prepare(fake, run_module):
    on(fake, "7.10.3A", "385145.1.82.1.73")
    expect_reads(fake, status("PREPARED"))

    result = run_module(upgrade, args(state="prepared", **BOOLEAN_WAIVER))

    assert_ok(result)
    assert (result["prepared"], result["changed"]) == (False, False)
    assert writes(fake) == []


def test_the_readiness_check_with_the_boolean_waiver_accepts_the_prepared_an_earlier_call_left_and_sends_no_verify(fake, run_module):
    on(fake, "7.10.3A", "385145.1.82.1.73")
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)

    result = run_module(upgrade, args(state="prepared", ready_to_commit=True, prepare=False, **BOOLEAN_WAIVER))

    assert_ok(result)
    assert (result["ready"], result["prepared"], result["changed"]) == (True, False, False)
    assert writes(fake) == []


def test_the_commit_with_the_boolean_waiver_commits_the_prepared_an_earlier_call_left_and_sends_no_verify(fake, run_module):
    on(fake, "7.10.3A", "385145.1.82.1.73")
    expect_commit_from_prepared(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args(prepare=False, **BOOLEAN_WAIVER))

    assert_ok(result)
    assert (result["commit_sent"], result["prepared"]) == (True, False)
    assert [e.path for e in writes(fake)] == [COMMIT]


def test_preparing_with_the_image_is_waited_for_and_not_prepared_again(fake, run_module):
    expect_reads(fake, status("PREPARING"))
    fake.expect("GET", STATUS, response=status("PREPARING"))
    fake.expect("GET", STATUS, response=status("PREPARED"))

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert result["changed"] is False
    assert requests(fake, PREPARE) == []


def test_committing_with_the_image_is_waited_for_without_a_commit(fake, run_module):
    expect_reads(fake, status("COMMITTING"))
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("COMMITTED"))
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["commit_sent"] is False
    assert writes(fake) == []


def test_preparing_a_new_presigned_url_of_the_same_image_is_waited_for_then_prepared_again(fake, run_module):
    earlier = IMAGE + "?X-Amz-Signature=earlier"
    expect_reads(fake, status("PREPARING", image=earlier))
    fake.expect("GET", STATUS, response=status("PREPARED", image=earlier))
    expect_prepare(fake, body={"image_path": SIGNED, "auto_commit": False, "do_rolling_reboot": True})
    expect_prepared(fake, image=SIGNED)

    result = run_module(upgrade, args(image_secret=SECRET, state="prepared"))

    assert_ok(result)
    assert result["prepared"] is True
    assert "earlier" not in json.dumps({k: v for k, v in result.items() if k != "invocation"})


OTHER = "https://images.example.com/qumulo/qumulo_core_7.11.1.qimg"


@pytest.mark.parametrize("state", ["PREPARING", "COMMITTING", "COMMITTED"])
def test_an_upgrade_of_another_image_started_outside_this_run_is_f6(fake, run_module, state):
    expect_reads(fake, status(state, image=OTHER + "?sig=x", target="7.11.1"))

    result = run_module(upgrade, args())

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == (
        f"{HOST} is {state} with the image {OTHER}, started outside this run. Wait for it to finish, then run the upgrade again, "
        f"or, to upgrade the rest of the fleet meanwhile, {LIMIT}."
    )
    assert writes(fake) == []


def test_a_preparing_that_commits_itself_is_f6(fake, run_module):
    expect_reads(fake, status("PREPARING", auto_commit=True))

    result = run_module(upgrade, args())

    assert result["msg"] == (
        f"{HOST} is preparing an upgrade that commits itself as soon as it is prepared, outside this run's order. Wait for it to "
        f"finish, then run the upgrade again, or, to upgrade the rest of the fleet meanwhile, {LIMIT}."
    )
    assert result["final"] is True


def test_an_idle_status_a_failed_web_ui_upgrade_left_is_a_note_and_a_lost_prepare_reads_past_its_error(fake, run_module):
    left = status("IDLE", image=OTHER, auto_commit=True, error="Upgrade from prohibited release 7.10.3 is not supported.", settings=True)
    expect_reads(fake, left)
    expect_prepare(fake, raises=URLError(ConnectionResetError(104, "reset")))
    fake.expect("GET", STATUS, response=left)
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert result["notes"][0].startswith(f"{HOST} reads IDLE with the last attempt's image {OTHER}, auto_commit True")
    assert result["prepared"] is True
    assert len(requests(fake, PREPARE)) == 1


def test_a_lost_prepare_whose_status_shows_a_new_error_is_its_refusal(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake, raises=URLError(ConnectionResetError(104, "reset")))
    fake.expect("GET", STATUS, response=status("IDLE", error="Malformed qimg", settings=True))

    result = run_module(upgrade, args(state="prepared"))

    assert result["failed"] is True
    assert result["msg"] == f"{HOST}: Qumulo Core refused the image {IMAGE}: Malformed qimg. {REACH}"


# The readiness check (FR-003) and the commit's own re-check


def f7_action(under_role=False):
    size = "qumulo_upgrade_reboot_set_size" if under_role else "reboot_set_size"
    return f"Run the upgrade again once the cluster has finished reprotecting, raise {wait_setting(under_role)}, or lower {size} for {HOST}."


def f7(remaining, seconds=10, under_role=False):
    return (
        f"{HOST}: Rolling reboot must reboot {remaining} or fewer nodes at once due to the current node fault tolerance. The "
        f"tolerance did not return within {seconds} seconds. {f7_action(under_role)}"
    )


def f23(remaining, configured, seconds=10, changes="", under_role=False):
    return (
        f"{HOST}: its current node fault tolerance reads {remaining} of its configured {configured} after {seconds} seconds{changes}, "
        "and an all-at-once reboot waits for a full reprotect with every node armed. Nothing was committed. Run the upgrade again "
        f"once the cluster has finished reprotecting, or raise {wait_setting(under_role)}."
    )


def ready_args(**kw):
    return args(state="prepared", ready_to_commit=True, prepare=False, wait_timeout=10, **kw)


def test_the_readiness_check_waits_out_a_blocked_preflight_on_any_type(fake, run_module):
    work = {"is_blocked": True, "cluster_degraded": False, "blocked_reason": "In progress background upgrade work needs to complete."}
    expect_reads(fake, status("PREPARED", kind=SOFTWARE))
    fake.expect("GET", BLOCKED, response=work)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)

    result = run_module(upgrade, ready_args())

    assert_ok(result)
    assert result["ready"] is True


@pytest.mark.parametrize(
    "found", [status("IDLE"), status("PREPARED", image=OTHER), status("PREPARED", count=2), status("PREPARING")], ids=["idle", "image", "settings", "preparing"]
)
def test_prepare_false_on_a_cluster_not_prepared_with_its_image_and_settings_is_f17(fake, run_module, found):
    expect_reads(fake, found)

    result = run_module(upgrade, ready_args())

    assert result["failed"] is True
    assert result["msg"] == (
        f"{HOST} is not PREPARED with {IMAGE}, its reboot settings and its waivers (it reads {found['state'].removeprefix('UPGRADE_STATE_')}), "
        f"so it is not committed. Run the upgrade again; it prepares {HOST} first."
    )
    assert writes(fake) == []


def test_prepare_false_on_7_11_0_without_the_waiver_sent_is_f17(fake, run_module):
    on(fake, "7.11.0A")
    expect_reads(fake, status("PREPARED", image="/u/qumulo_core_7.11.1.qimg", target="7.11.1", overrides=[]))

    result = run_module(upgrade, ready_args(image="/u/qumulo_core_7.11.1.qimg", allow_upgrade_from_limited_release=True))

    assert result["msg"].startswith(f"{HOST} is not PREPARED with /u/qumulo_core_7.11.1.qimg")


def f21(found="7.11.1", declared="7.11.0", image=IMAGE):
    return (
        f"{HOST}: the image {image} holds Qumulo Core {found}, but the images list gives it as {declared}. Nothing was committed, and "
        f"{HOST} stays PREPARED with that image until its next prepare or a quorum change. Correct the entry's file name or version, "
        "then run the upgrade again."
    )


def test_f21_at_the_readiness_check(fake, run_module):
    expect_reads(fake, status("PREPARED", target="7.11.1"))

    result = run_module(upgrade, ready_args())

    assert result["msg"] == f21()
    assert result["final"] is True


def test_f21_once_the_prepare_reads_preparing(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake)
    fake.expect("GET", STATUS, response=status("PREPARING", target="7.11.1"))

    result = run_module(upgrade, args(state="prepared"))

    assert result["msg"] == f21()
    assert result["changed"] is True


def test_f21_before_the_commit(fake, run_module, record):
    expect_reads(fake, status("PREPARED", target="7.11.1"))

    result = run_module(upgrade, args(commit_record=str(record)))

    assert result["msg"] == f21()
    assert not record.exists()
    assert writes(fake) == []


@UNDER_ROLE
def test_a_platform_count_above_the_current_tolerance_is_waited_for_and_then_f7(fake, run_module, under_role):
    expect_reads(fake, status("PREPARED", count=2), protect=protection(2, 1))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    for _poll in range(3):
        fake.expect("GET", PROTECTION, response=protection(2, 1))

    result = run_module(upgrade, ready_args(reboot_set_size=2, **run_as(under_role)))

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == f7(1, under_role=under_role)


def test_a_platform_count_that_the_tolerance_returns_to_is_ready(fake, run_module):
    expect_reads(fake, status("PREPARED", count=2), protect=protection(2, 1))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    fake.expect("GET", PROTECTION, response=protection(2, 1))
    fake.expect("GET", PROTECTION, response=protection(2, 2))

    result = run_module(upgrade, ready_args(reboot_set_size=2))

    assert_ok(result)
    assert result["ready"] is True


@pytest.mark.parametrize(
    ("pending", "changes"),
    [
        (None, ""),
        ({"blocks_per_stripe": 8, "data_blocks_per_stripe": 6}, ", while its protection changes to 6+2"),
        ({"stripe": "8+2"}, ', while its protection changes to {"stripe": "8+2"}'),
    ],
)
@UNDER_ROLE
def test_reboot_all_on_a_platform_upgrade_waits_for_the_configured_tolerance_and_then_f23(fake, run_module, pending, changes, under_role):
    expect_reads(fake, status("PREPARED", rolling=False), protect=protection(2, 1, pending=pending))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    for _poll in range(3):
        fake.expect("GET", PROTECTION, response=protection(2, 1, pending=pending))

    result = run_module(upgrade, ready_args(reboot="all", **run_as(under_role)))

    assert result["msg"] == f23(1, 2, changes=changes, under_role=under_role)
    assert result["final"] is True


def test_a_rolling_reboot_without_a_count_at_a_current_tolerance_of_0_is_not_waited_on(fake, run_module, fake_clock):
    expect_commit_from_prepared(fake, protect=protection(2, 0))
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["reboot"]["mode"] == "rolling"
    assert requests(fake, PROTECTION)[-1].background is False and len(requests(fake, PROTECTION)) == 1
    assert fake_clock.sleeps == []


def test_a_software_only_upgrade_waits_on_no_tolerance(fake, run_module):
    expect_reads(fake, status("PREPARED", kind=SOFTWARE, count=2), protect=protection(2, 0))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)

    result = run_module(upgrade, ready_args(reboot_set_size=2))

    assert_ok(result)
    assert len(requests(fake, PROTECTION)) == 1


@pytest.mark.parametrize(
    ("found", "options", "message"),
    [
        (status("PREPARED", count=2), {"reboot_set_size": 2}, f7(1)),
        (status("PREPARED", rolling=False), {"reboot": "all"}, f23(1, 2)),
    ],
    ids=["count", "all"],
)
def test_the_commits_own_re_check_reads_once_and_refuses_at_once(fake, run_module, fake_clock, record, monkeypatch, found, options, message):
    written = spy_record_writes(monkeypatch, fake)
    expect_reads(fake, found, protect=protection(2, 1))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    fake.expect("GET", PROTECTION, response=protection(2, 1))

    result = run_module(upgrade, args(wait_timeout=10, commit_record=str(record), **options))

    assert result["msg"] == message
    assert result["commit_sent"] is False
    assert fake_clock.sleeps == []
    assert written == []
    assert writes(fake) == []


def test_the_count_is_read_once_more_just_before_the_commit_with_no_wait(fake, run_module, fake_clock):
    expect_reads(fake, status("PREPARED", count=2))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    fake.expect("GET", PROTECTION, response=protection(2, 2))
    expect_commit(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args(reboot_set_size=2))

    assert_ok(result)
    assert fake_clock.sleeps == []


def test_rebooted_with_commit_false_on_a_cluster_not_on_the_hops_release_is_refused_with_only_reads(fake, run_module):
    expect_reads(fake, status("PREPARED"))

    result = run_module(upgrade, args(state="rebooted", commit=False))

    assert result["failed"] is True
    assert "commit is false" in result["msg"]
    assert writes(fake) == []


# The count before the prepare (FR-005, FR-022)


def test_a_prepare_that_sends_a_count_waits_for_the_tolerance_first(fake, run_module):
    expect_reads(fake, protect=protection(2, 1))
    fake.expect("GET", PROTECTION, response=protection(2, 2))
    expect_prepare(fake, num_nodes_to_reboot=2)
    expect_prepared(fake, count=2)

    result = run_module(upgrade, args(state="prepared", reboot_set_size=2))

    assert_ok(result)


@UNDER_ROLE
def test_a_count_the_tolerance_never_reaches_is_f7_with_no_prepare_sent(fake, run_module, under_role):
    expect_reads(fake, protect=protection(2, 1))
    for _poll in range(2):
        fake.expect("GET", PROTECTION, response=protection(2, 1))

    result = run_module(upgrade, args(state="prepared", reboot_set_size=2, wait_timeout=10, **run_as(under_role)))

    assert result["msg"] == f7(1, under_role=under_role)
    assert requests(fake, PREPARE) == []


@UNDER_ROLE
def test_a_prepare_answered_with_the_servers_count_refusal_is_f7_in_its_words_without_the_reach_line(fake, run_module, under_role):
    expect_reads(fake)
    words = "Rolling reboot must reboot 1 or fewer nodes at once due to the current node fault tolerance"
    expect_prepare(fake, status_code=500, response={"error_class": "reboot_error", "description": words}, num_nodes_to_reboot=2)

    result = run_module(upgrade, args(state="prepared", reboot_set_size=2, **run_as(under_role)))

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == f"{HOST}: {words}. {f7_action(under_role)}"
    assert "Every node" not in result["msg"]


# Check mode (FR-006)


def test_check_mode_sends_only_reads_and_predicts_each_wait_as_a_wait(fake, run_module, fake_clock):
    work = {"is_blocked": True, "cluster_degraded": True, "blocked_reason": "Cannot upgrade due to offline nodes: 3."}
    expect_reads(fake, status("REBOOTING", target="7.10.3"), blocked=work, protect=protection(2, 1), reboots=reboot("REBOOT_IN_PROGRESS", 2, 4))

    result = run_module(upgrade, args(state="rebooted", reboot_set_size=2), check_mode=True)

    assert_ok(result)
    assert result["plan"]["phases"] == ["prepare", "commit", "reboots"]
    assert result["plan"]["waits"] == [
        "the reboot under way, 2 of 4 nodes rebooted, up to reboot_timeout",
        "remaining_node_failures 1 to reach 2, up to wait_timeout",
    ]
    assert writes(fake) == []
    assert fake_clock.sleeps == []


def test_check_mode_predicts_the_readiness_waits_of_a_prepared_cluster(fake, run_module):
    work = {"is_blocked": True, "cluster_degraded": False, "blocked_reason": "In progress background upgrade work needs to complete."}
    expect_reads(fake, status("PREPARED", rolling=False), blocked=work, protect=protection(2, 1))

    result = run_module(upgrade, args(reboot="all"), check_mode=True)

    assert result["plan"]["waits"] == [
        "the preflight's 'In progress background upgrade work needs to complete.', up to wait_timeout",
        "remaining_node_failures 1 to equal the configured 2, up to wait_timeout",
    ]


def test_check_mode_with_verify_in_check_mode_sends_one_verify(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, body={"image_path": IMAGE}, response={"target_version": "7.11.0", "upgrade_type": SOFTWARE, "error": None})

    result = run_module(upgrade, args(verify_in_check_mode=True), check_mode=True)

    assert_ok(result)
    assert result["upgrade_type"] == SOFTWARE
    assert [e.path for e in writes(fake)] == [VERIFY]


# The refusals before any write (FR-005, FR-008)


def test_a_letter_release_source_without_the_waiver_is_f1(fake, run_module):
    on(fake, "7.10.3A")
    expect_reads(fake)

    result = run_module(upgrade, args())

    assert result["final"] is True
    assert result["msg"] == (
        f"{HOST} runs Qumulo Core 7.10.3A, a limited release, and Qumulo Core refuses to upgrade from a limited release unless "
        f"the rule is waived. Contact Qumulo Care. Set qumulo_upgrade_allow_limited_release: true for {HOST} only when Qumulo "
        f"Care directs it. To upgrade the rest of the fleet meanwhile, {LIMIT}."
    )
    assert writes(fake) == []


def f3(release, target, under_role=False):
    fix = f"set qumulo_upgrade_target to {release} or later" if under_role else f"give an image of {release} or later"
    return f"{HOST} runs Qumulo Core {release}, above the target {target}. The collection never downgrades: {fix}, or leave {HOST} out of the run with --limit."


@UNDER_ROLE
def test_a_downgrade_is_f3(fake, run_module, under_role):
    on(fake, "7.11.1")
    expect_reads(fake)

    result = run_module(upgrade, args(**run_as(under_role)))

    assert result["final"] is True
    assert result["msg"] == f3("7.11.1", "7.11.0", under_role)


def test_a_downgrade_learned_from_verify_is_f3(fake, run_module):
    on(fake, "7.11.1")
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": "7.11.0", "upgrade_type": SOFTWARE, "error": None})

    result = run_module(upgrade, args(image="/upgrades/latest.qimg", state="verified"))

    assert result["msg"] == f3("7.11.1", "7.11.0")


def test_a_verify_that_reads_another_release_than_declared_is_refused(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": "7.11.1", "upgrade_type": SOFTWARE, "error": None})

    result = run_module(upgrade, args(state="verified"))

    assert result["final"] is True
    assert result["msg"] == (
        f"{HOST}: verify reads the image {IMAGE} as Qumulo Core 7.11.1, but the images list gives it as 7.11.0. Nothing was "
        "prepared. Correct the entry's file name or version, then run the upgrade again."
    )


F8 = (
    f"{HOST} cannot upgrade in band: Qumulo Core reports that this deployment has no installer for it. The collection does not "
    "upgrade such a deployment; upgrade it as its deployment documents."
)


# Pins QUIRK-092: the container's verify and prepare answer 500 os_error_ENOENT naming the installer's verify script.
@pytest.mark.parametrize("what", ["verify, no waiver", "prepare, all at once, no count"])
def test_the_containers_missing_installer_is_f8(fake, run_module, what):
    answer = edge(what)
    image = answer["body"]["image_path"]
    expect_reads(fake, edge("read")["response"], protect=edge("read")["response"] and protection(0), nodes_found=nodes(1))
    route = VERIFY if what.startswith("verify") else PREPARE
    fake.expect("POST", route, body=answer["body"], status=answer["status"], response=answer["response"])

    result = run_module(upgrade, args(image=image, state="verified" if route == VERIFY else "prepared"))

    assert result["failed"] is True
    assert result["final"] is True
    assert result["msg"] == F8


def test_an_externally_managed_deployment_is_f8(fake, run_module):
    expect_reads(fake)
    fake.expect(
        "POST",
        VERIFY,
        status=400,
        response={"error_class": "upgrade_externally_managed_error", "description": "Software upgrades are externally managed for this deployment."},
    )

    result = run_module(upgrade, args(state="verified"))

    assert result["msg"] == F8


def f16(release):
    return (
        f"{HOST} runs {release}, below 7.11.0, where Qumulo Core's waiver lifts every compatibility rule, so the collection sends "
        f"it only for a declared build. Declare the build Qumulo Care names for {HOST}'s first hop as build in that hop's mapping "
        f"entry, in a qumulo_upgrade_images list of {HOST}'s own. To upgrade the rest of the fleet meanwhile, {LIMIT}."
    )


def test_the_boolean_waiver_without_a_declared_build_is_f16(fake, run_module):
    on(fake, "7.10.3A")
    expect_reads(fake)

    result = run_module(upgrade, args(allow_upgrade_from_limited_release=True))

    assert result["final"] is True
    assert result["msg"] == f16("7.10.3A")
    assert writes(fake) == []


# The limited-release waiver (FR-020)


def test_no_waiver_is_sent_by_default_or_from_a_source_without_a_letter(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": "7.11.0", "upgrade_type": SOFTWARE, "error": None})

    result = run_module(upgrade, args(state="verified", allow_upgrade_from_limited_release=True))

    assert_ok(result)
    assert result["waiver"] is None
    assert "?" not in requests(fake, VERIFY)[0].path


def test_from_a_7_11_letter_release_the_named_rule_goes_on_verify_and_prepare_and_never_on_commit(fake, run_module):
    on(fake, "7.11.0A")
    image = "/u/qumulo_core_7.11.1.qimg"
    query = "compatibility_overrides=allow_upgrade_from_limited_release"
    expect_reads(fake)
    fake.expect("POST", f"{VERIFY}?{query}", response={"target_version": "7.11.1", "upgrade_type": PLATFORM, "error": None})

    check = run_module(upgrade, args(image=image, allow_upgrade_from_limited_release=True, verify_in_check_mode=True), check_mode=True)

    expect_reads(fake)
    expect_prepare(fake, body={"image_path": image, "auto_commit": False, "do_rolling_reboot": True}, query=query)
    expect_prepared(fake, image=image, target="7.11.1", overrides=["ALLOW_UPGRADE_FROM_LIMITED_RELEASE"])
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake)
    expect_rolling_done(fake, "7.11.1", "build-7.11.1")

    result = run_module(upgrade, args(image=image, allow_upgrade_from_limited_release=True))

    assert_ok(check)
    assert_ok(result)
    assert result["waiver"] == {"parameter": query, "lifts": ["a limited release"]}
    assert "?" not in requests(fake, COMMIT)[0].path


NINE = [
    "a prohibited release",
    "a limited release",
    "a developer source",
    "a source off a release branch",
    "a downgrade",
    "the upgrade window",
    "a developer target build",
    "a build not later than the running one",
    "the license check",
]


@pytest.mark.parametrize("source", ["7.10.3A", "7.10.3.1A"])
def test_below_7_11_0_the_boolean_goes_with_a_verify_first_and_the_prepare_only_at_the_declared_release(fake, run_module, source):
    on(fake, source, "385145.1.82.1.73")
    expect_reads(fake)
    fake.expect(
        "POST",
        VERIFY + "?override_compatibility_check=true",
        body={"image_path": IMAGE},
        response={"target_version": "7.11.0", "upgrade_type": PLATFORM, "error": None},
    )
    expect_prepare(fake, query="override_compatibility_check=true")
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared", allow_upgrade_from_limited_release=True, build=NEW_BUILD))

    assert_ok(result)
    assert result["waiver"] == {"parameter": "override_compatibility_check=true", "lifts": NINE}


def test_below_7_11_0_a_verify_that_reads_another_release_sends_no_prepare(fake, run_module):
    on(fake, "7.10.3A", "385145.1.82.1.73")
    expect_reads(fake)
    fake.expect("POST", VERIFY + "?override_compatibility_check=true", response={"target_version": "7.11.1", "upgrade_type": PLATFORM, "error": None})

    result = run_module(upgrade, args(state="prepared", allow_upgrade_from_limited_release=True, build=NEW_BUILD))

    assert result["failed"] is True
    assert requests(fake, PREPARE) == []


def test_below_7_11_0_the_boolean_waits_for_a_hop_the_quarterly_rule_allows(fake, run_module):
    on(fake, "7.9.0.5A", "1")
    expect_reads(fake)

    result = run_module(upgrade, args(allow_upgrade_from_limited_release=True, build=NEW_BUILD))

    assert result["failed"] is True
    assert result["msg"].startswith(f"{HOST} on 7.9.0.5A cannot reach 7.11.0 with this images list")


def test_the_waivers_form_follows_the_version_gate(fake, run_module, monkeypatch):
    monkeypatch.setitem(version.GATES, "upgrade.compatibility_overrides", version.Gate((7, 10, 0)))
    on(fake, "7.10.3A")
    expect_reads(fake)
    fake.expect(
        "POST",
        VERIFY + "?compatibility_overrides=allow_upgrade_from_limited_release",
        response={"target_version": "7.11.0", "upgrade_type": SOFTWARE, "error": None},
    )

    result = run_module(upgrade, args(state="verified", allow_upgrade_from_limited_release=True))

    assert_ok(result)


def test_the_waiver_gate_is_by_release_from_7_11_0():
    gate = version.GATES["upgrade.compatibility_overrides"]
    assert (gate.since, gate.probe) == ((7, 11, 0), None)
    assert gate.why


# Lost answers and the server's refusals (FR-007)

LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))


def test_a_lost_verify_answer_is_f29_with_nothing_changed(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, raises=LOST)

    result = run_module(upgrade, args(state="verified"))

    assert result["failed"] is True
    assert result["changed"] is False
    assert "maybe_applied" not in result
    assert result["msg"] == f"{HOST}: verify's answer was lost; nothing was installed. Run the upgrade again."


def test_a_lost_prepare_answer_that_finds_preparing_waits_for_prepared(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake, raises=LOST)
    fake.expect("GET", STATUS, response=status("IDLE"))
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert result["changed"] is True
    assert result["prepared"] is True


def f28(earlier="IDLE", seconds=2700):
    return (
        f"{HOST}: the prepare's answer was lost, and after {seconds} seconds Qumulo Core still reads {earlier}, so the prepare "
        "may or may not have been applied. A verify that Qumulo Core refused leaves the same status. Every node of "
        f"{HOST} must reach {IMAGE} from its own network. Run the upgrade again, which reads the status before it prepares."
    )


# Pins QUIRK-093: the prepare answers only after every node's verify, so a lost answer is read back until the request's own
# timeout, and the prepare is never sent again.
def test_a_lost_prepare_answer_is_read_back_and_never_sent_again(fake, run_module, fake_clock):
    expect_reads(fake)
    expect_prepare(fake, raises=LOST)
    fake.serve(STATUS, status("IDLE"))

    result = run_module(upgrade, args(state="prepared"))

    assert result["failed"] is True
    assert (result["changed"], result["maybe_applied"]) == (True, True)
    assert "final" not in result
    assert result["msg"] == f28()
    assert len(requests(fake, PREPARE)) == 1
    assert sum(fake_clock.sleeps) == 2700


def test_a_lost_re_prepare_waits_past_the_old_prepared_until_the_new_one_matches(fake, run_module):
    expect_reads(fake, status("PREPARED", rolling=False))
    expect_prepare(fake, raises=LOST)
    fake.expect("GET", STATUS, response=status("PREPARED", rolling=False))
    fake.expect("GET", STATUS, response=status("PREPARED", rolling=False))
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert len(requests(fake, PREPARE)) == 1


def test_a_lost_re_prepare_that_stays_on_the_old_prepared_is_f28_naming_it(fake, run_module):
    old = "https://images.example.com/qumulo/old/qumulo_core_7.11.0.qimg"
    expect_reads(fake, status("PREPARED", image=old))
    expect_prepare(fake, raises=LOST)
    fake.serve(STATUS, status("PREPARED", image=old + "?sig=old"))

    result = run_module(upgrade, args(state="prepared", version="7.11.0"))

    assert result["msg"] == f28(f"the earlier prepare of {old}")


def still_preparing(seconds=10):
    return (
        f"{HOST} still reads PREPARING after {seconds} seconds of preparing {IMAGE}. Run the upgrade again, which reads the status "
        f"before it prepares; if it reads the same, check every node's route to {IMAGE}."
    )


def another_prepare_under_way(seconds=10):
    return (
        f"{HOST} still reads PREPARING with {IMAGE} after {seconds} seconds; another prepare of this image is still under way. Wait "
        "for it to finish, then run the upgrade again."
    )


def test_a_lost_prepare_answer_that_finds_preparing_records_the_prepare_and_waits_on_wait_timeout(fake, run_module, fake_clock):
    expect_reads(fake)
    expect_prepare(fake, raises=LOST)
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.serve(STATUS, status("PREPARING"))

    result = run_module(upgrade, args(state="prepared", wait_timeout=10))

    assert result["failed"] is True
    assert result["msg"] == still_preparing()
    assert (result["changed"], result["prepared"]) == (True, True)
    assert "maybe_applied" not in result
    assert sum(fake_clock.sleeps) == 15  # one poll past IDLE on the request's deadline, then wait_timeout from PREPARING


def test_a_lost_prepare_answer_that_finds_preparing_of_another_release_is_f21_and_changed(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake, raises=LOST)
    fake.expect("GET", STATUS, response=status("PREPARING", target="7.11.1"))

    result = run_module(upgrade, args(state="prepared"))

    assert result["msg"].startswith(f"{HOST}: the image {IMAGE} holds Qumulo Core 7.11.1, but the images list gives it as 7.11.0.")
    assert (result["changed"], result["prepared"], result["final"]) == (True, True, True)


def test_a_prepare_409_already_preparing_is_read_back_and_waited_for(fake, run_module):
    expect_reads(fake)
    expect_prepare(
        fake, status_code=409, response={"error_class": "upgrade_already_in_progress_error", "description": "A previous upgrade is already preparing."}
    )
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)


def test_a_prepare_409_whose_preparing_outlasts_the_request_timeout_fails(fake, run_module):
    expect_reads(fake)
    expect_prepare(
        fake, status_code=409, response={"error_class": "upgrade_already_in_progress_error", "description": "A previous upgrade is already preparing."}
    )
    fake.serve(STATUS, status("PREPARING"))

    result = run_module(upgrade, args(state="prepared"))

    assert result["failed"] is True
    assert result["msg"] == another_prepare_under_way(2700)


def test_a_prepare_409_that_reads_back_another_image_is_f6(fake, run_module):
    expect_reads(fake)
    expect_prepare(
        fake, status_code=409, response={"error_class": "upgrade_already_in_progress_error", "description": "A previous upgrade is already preparing."}
    )
    fake.expect("GET", STATUS, response=status("PREPARING", image=OTHER, target="7.11.1"))

    result = run_module(upgrade, args(state="prepared"))

    assert result["msg"].startswith(f"{HOST} is PREPARING with the image {OTHER}, started outside this run.")


STILL_REBOOTING = {"error_class": "upgrade_already_in_progress_error", "description": "Cluster is still rebooting from previous upgrade"}


def expect_still_rebooting(fake, **sent):
    """A prepare refused while a reboot runs, and the reboot read until it ends (FR-007)."""
    expect_prepare(fake, status_code=409, response=STILL_REBOOTING, **sent)
    fake.expect("GET", STATUS, response=status("REBOOTING", target="7.10.3"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 3, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot())


def test_a_prepare_409_still_rebooting_waits_for_the_reboot_and_sends_the_prepare_once_more(fake, run_module):
    expect_reads(fake)
    expect_still_rebooting(fake)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_prepare(fake)
    expect_prepared(fake)

    result = run_module(upgrade, args(state="prepared"))

    assert_ok(result)
    assert len(requests(fake, PREPARE)) == 2


def test_the_prepare_after_a_409_still_rebooting_waits_for_the_preflight_and_the_count_again(fake, run_module):
    """The reboot leaves the background work and the tolerance recovering, and the server checks the count only after
    every node has downloaded the image (FR-005)."""
    work = {"is_blocked": True, "cluster_degraded": False, "blocked_reason": "In progress background upgrade work needs to complete."}
    expect_reads(fake, protect=protection(2, 2))
    expect_still_rebooting(fake, num_nodes_to_reboot=2)
    fake.expect("GET", BLOCKED, response=work)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    fake.expect("GET", PROTECTION, response=protection(2, 1))
    fake.expect("GET", PROTECTION, response=protection(2, 2))
    expect_prepare(fake, num_nodes_to_reboot=2)
    expect_prepared(fake, count=2)

    result = run_module(upgrade, args(state="prepared", reboot_set_size=2))

    assert_ok(result)
    assert len(requests(fake, PREPARE)) == 2


def test_a_count_that_does_not_return_after_a_409_still_rebooting_is_f7_before_the_second_prepare(fake, run_module):
    expect_reads(fake, protect=protection(2, 2))
    expect_still_rebooting(fake, num_nodes_to_reboot=2)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    fake.serve(PROTECTION, protection(2, 1))

    result = run_module(upgrade, args(state="prepared", reboot_set_size=2, wait_timeout=10))

    assert result["msg"] == f7(1)
    assert len(requests(fake, PREPARE)) == 1


def test_a_write_followed_by_a_refusal_reports_changed(fake, run_module):
    expect_reads(fake, protect=protection(2, 2))
    expect_prepare(fake, num_nodes_to_reboot=2)
    expect_prepared(fake, count=2)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    fake.expect("GET", PROTECTION, response=protection(2, 1))

    result = run_module(upgrade, args(reboot_set_size=2, wait_timeout=10))

    assert result["failed"] is True
    assert result["msg"] == f7(1)
    assert result["changed"] is True
    assert result["prepared"] is True


def test_the_failure_fields_say_which_refusals_are_final(fake, run_module):
    expect_reads(fake, status("PREPARING", image=OTHER))

    final = run_module(upgrade, args())
    expect_reads(fake)
    fake.expect("POST", VERIFY, raises=LOST)
    lost = run_module(upgrade, args(state="verified"))

    assert final["final"] is True and final["changed"] is False
    assert "final" not in lost


# Pins QUIRK-095: a URL no node reaches fails verify without naming the URL's reach, so the module adds the reach line.
def test_the_reach_line_is_added_to_a_malformed_qimg(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": None, "upgrade_type": None, "error": "Malformed qimg", "error_class": "upgrade_verify_error"})

    result = run_module(upgrade, args(state="verified"))

    assert result["failed"] is True
    assert result["msg"] == f"{HOST}: Qumulo Core refused the image {IMAGE}: Malformed qimg. {REACH}"
    assert result["error_class"] == "upgrade_verify_error"


@pytest.mark.parametrize(
    ("status_code", "response", "said"),
    [
        (
            400,
            {"error_class": "upgrade_verify_error", "description": "The provided upgrade is invalid: Download of the image timed out"},
            f"{HOST}: Qumulo Core refused the image {IMAGE}: Download of the image timed out.",
        ),
        (
            500,
            {"error_class": "http_server_error", "description": "Internal server error"},
            f"{HOST}: POST /v2/upgrade/prepare failed: HTTP 500 http_server_error: Internal server error.",
        ),
    ],
    ids=["download-timeout", "server-error"],
)
def test_the_reach_line_is_added_to_a_download_timeout_and_a_server_error(fake, run_module, status_code, response, said):
    expect_reads(fake)
    expect_prepare(fake, status_code=status_code, response=response)

    result = run_module(upgrade, args(state="prepared"))

    assert result["msg"] == f"{said} {REACH}"


def test_a_path_image_gets_no_reach_line(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"error": "Malformed qimg"})

    result = run_module(upgrade, args(image=PATH_IMAGE, state="verified"))

    assert result["msg"] == f"{HOST}: Qumulo Core refused the image {PATH_IMAGE}: Malformed qimg."


# Pins QUIRK-094: verify answers 200 with error set, and prepare 400 upgrade_verify_error, for the same refused image.
def test_verify_200_with_an_error_and_prepare_400_are_one_refusal(fake, run_module):
    refused = "Upgrade from prohibited release 7.10.3 is not supported with this version."
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": None, "upgrade_type": None, "error": refused, "error_class": "upgrade_verify_error"})
    verified = run_module(upgrade, args(image=PATH_IMAGE, state="verified"))
    expect_reads(fake)
    expect_prepare(
        fake,
        body={"image_path": PATH_IMAGE, "auto_commit": False, "do_rolling_reboot": True},
        status_code=400,
        response={"error_class": "upgrade_verify_error", "description": f"The provided upgrade is invalid: {refused}"},
    )
    prepared = run_module(upgrade, args(image=PATH_IMAGE, state="prepared"))

    assert verified["msg"] == prepared["msg"] == f"{HOST}: Qumulo Core refused the image {PATH_IMAGE}: {refused}"


# Pins QUIRK-090: the deprecated v2 status reads REBOOTING as IDLE, so the state comes from the v3 status only.
def test_the_state_is_read_from_v3_status_never_v2(fake, run_module):
    on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake, status("REBOOTING"), reboots=reboot("REBOOT_IN_PROGRESS", 3, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot())

    result = run_module(upgrade, args(state="rebooted"))

    assert_ok(result)
    assert requests(fake, "/v2/upgrade/status") == []
    assert len(requests(fake, STATUS)) == 2


def test_verify_and_prepare_wait_4500_seconds_on_a_source_below_7_10_3(fake, run_module):
    on(fake, "7.10.0.3")
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": "7.11.0", "upgrade_type": SOFTWARE, "error": None})
    run_module(upgrade, args(state="verified"))
    expect_reads(fake)
    expect_prepare(fake)
    expect_prepared(fake)
    run_module(upgrade, args(state="prepared"))

    assert [e.timeout for e in writes(fake)] == [4500, 4500]


# Reboots (FR-022, FR-025, FR-025a)


def test_a_rolling_prepare_sends_a_declared_count_and_none_without_one(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake, num_nodes_to_reboot=1)
    expect_prepared(fake, count=1)
    with_count = run_module(upgrade, args(state="prepared", reboot_set_size=1))
    expect_reads(fake)
    expect_prepare(fake)
    expect_prepared(fake)
    without = run_module(upgrade, args(state="prepared", reboot="rolling"))

    assert with_count["reboot"]["set_size"] == 1
    assert without["reboot"]["set_size"] is None
    assert "num_nodes_to_reboot" not in requests(fake, PREPARE)[1].body
    assert without["warnings"] == []


def test_reboot_all_prepares_all_at_once_and_ignores_a_count_with_w10(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake, body={"image_path": IMAGE, "auto_commit": False, "do_rolling_reboot": False})
    expect_prepared(fake, rolling=False)

    result = run_module(upgrade, args(state="prepared", reboot="all", reboot_set_size=2))

    assert_ok(result)
    assert result["warnings"] == [f"qumulo_upgrade_reboot is all for {HOST}, so its qumulo_upgrade_reboot_set_size of 2 is ignored."]
    assert result["reboot"] == {"mode": "all", "set_size": None, "seconds": None}


W10 = f"{HOST}'s configured node fault tolerance is 0, so a platform upgrade of {HOST} reboots every node at once, whatever qumulo_upgrade_reboot says"


@pytest.mark.parametrize(
    ("protect", "count_nodes"),
    [(protection(0), 4), (protection(1, 1, kind="PROTECTION_SYSTEM_TYPE_OBJECT_BACKED"), 2)],
    ids=["max_node_failures", "node-count-rule"],
)
@pytest.mark.parametrize(
    ("options", "warnings"),
    [({}, []), ({"reboot": "rolling"}, [W10 + "."]), ({"reboot_set_size": 1}, [W10 + "; its qumulo_upgrade_reboot_set_size of 1 is ignored."])],
    ids=["nothing-declared", "rolling-declared", "count-declared"],
)
def test_a_configured_tolerance_of_0_reboots_all_at_once(fake, run_module, protect, count_nodes, options, warnings):
    expect_reads(fake, protect=protect, nodes_found=nodes(count_nodes))
    expect_prepare(fake, body={"image_path": IMAGE, "auto_commit": False, "do_rolling_reboot": False})
    expect_prepared(fake, rolling=False)

    result = run_module(upgrade, args(state="prepared", **options))

    assert_ok(result)
    assert result["reboot"]["mode"] == "all"
    assert result["warnings"] == warnings


def test_rebooted_waits_for_rebooting_to_end_reading_the_reboot_status_every_30_seconds(fake, run_module, fake_clock):
    expect_commit_from_prepared(fake)
    expect_rolling_done(fake)
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 2, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot())

    result = run_module(upgrade, args(state="rebooted"))

    assert_ok(result)
    assert result["reboot"]["seconds"] == 60.0
    assert fake_clock.sleeps == [30.0, 30.0]
    assert result["phases"]["reboots"]["ended"]


@pytest.mark.parametrize(("state", "message"), [("REBOOT_PAUSED", f25), ("REBOOT_PAUSED_DUE_TO_ERROR", f26)])
def test_a_reboot_after_the_commit_that_pauses_fails_at_once(fake, run_module, state, message):
    expect_commit_from_prepared(fake)
    expect_rolling_done(fake)
    fake.expect("GET", STATUS, response=status("REBOOTING"))
    fake.expect("GET", REBOOT, response=reboot(state, 2, 4, error="node 3 did not come back"))

    result = run_module(upgrade, args(state="rebooted"))

    assert result["msg"] == message()
    assert result["changed"] is True


def test_rebooted_with_commit_false_on_the_target_build_only_waits(fake, run_module):
    on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake, status("REBOOTING"), reboots=reboot("REBOOT_IN_PROGRESS", 3, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot())

    result = run_module(upgrade, args(state="rebooted", commit=False))

    assert_ok(result)
    assert writes(fake) == []


def test_rebooted_with_commit_false_needs_no_image_only_the_targets_version(fake, run_module):
    """The role's reboot step for a host whose list holds no entry of the target, which the plan gives no image."""
    on(fake, "7.11.0", NEW_BUILD)
    expect_reads(fake, status("REBOOTING"), reboots=reboot("REBOOT_IN_PROGRESS", 3, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot())

    result = run_module(upgrade, {**CONNECTION, "state": "rebooted", "commit": False, "version": "7.11.0"})

    assert_ok(result)
    assert (result["image_redacted"], result["to"]["version"]) == (None, "7.11.0")
    assert writes(fake) == []


@pytest.mark.parametrize("options", [{"state": "committed"}, {"state": "rebooted", "commit": False}], ids=["a call that may commit", "without version"])
def test_no_image_is_refused_before_any_request_unless_the_call_only_waits_for_the_reboots_of_a_named_version(fake, run_module, options):
    result = run_module(upgrade, {**CONNECTION, **options})

    assert result["failed"] is True
    assert result["msg"] == (
        f"{HOST}: image is required, except with state rebooted and commit false, which only waits for the reboots and takes the target from version."
    )
    assert fake.log == []


# The edges of each wait


def test_a_record_rewritten_since_the_plan_read_it_is_not_removed(record):
    put(record, record_text())
    digest = upgrade_record.record_digest(record_text())
    put(record, record_text("unfinished"))

    assert upgrade_record.remove_record(str(record), digest) is False
    assert upgrade_record.remove_record(str(record.with_name("absent.yml"))) is False
    assert record.exists()


def test_a_record_write_that_fails_leaves_no_staged_file(record, monkeypatch):
    def refuse(a, b):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(OSError):
        upgrade_record.write_record(str(record), {"state": "pending"})

    assert list(record.parent.iterdir()) == []


def test_a_cluster_on_the_target_release_on_another_build_than_declared_is_f4(fake, run_module):
    on(fake, "7.11.0", "build-other")
    expect_reads(fake)

    result = run_module(upgrade, args(build=NEW_BUILD))

    assert result["final"] is True
    assert result["msg"] == (
        f"{HOST} on 7.11.0 cannot reach 7.11.0 with this images list: {HOST} already runs 7.11.0 on build build-other, not the "
        f"build {NEW_BUILD} its mapping entry declares. Correct the entry's build with Qumulo Care; the collection does not move "
        "a cluster between builds of one release."
    )


def test_an_image_whose_name_gives_no_release_learns_it_from_verify(fake, run_module):
    expect_reads(fake)
    fake.expect("POST", VERIFY, response={"target_version": "7.11.0", "upgrade_type": SOFTWARE, "error": None})

    result = run_module(upgrade, args(image="/upgrades/latest.qimg", state="verified"))

    assert_ok(result)
    assert result["to"] == {"version": "7.11.0", "build": None}


def test_a_reboot_that_ends_on_a_cluster_no_longer_prepared_is_f17_with_prepare_false(fake, run_module):
    expect_reads(fake, status("PREPARED"), reboots=reboot("REBOOT_IN_PROGRESS", 3, 4))
    fake.expect("GET", STATUS, response=status("IDLE"))
    fake.expect("GET", REBOOT, response=reboot())

    result = run_module(upgrade, args(prepare=False))

    assert result["msg"].startswith(f"{HOST} is not PREPARED with {IMAGE}")


def test_a_preparing_that_outlasts_wait_timeout_fails(fake, run_module):
    expect_reads(fake, status("PREPARING"))
    for _poll in range(2):
        fake.expect("GET", STATUS, response=status("PREPARING"))

    result = run_module(upgrade, args(state="prepared", wait_timeout=10))

    assert result["msg"] == another_prepare_under_way()
    assert writes(fake) == []


def test_a_prepare_that_falls_back_to_idle_with_no_error_after_its_202_fails_as_changed(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake)
    fake.expect("GET", STATUS, response=status("IDLE"))

    result = run_module(upgrade, args(state="prepared"))

    assert result["changed"] is True
    assert result["msg"] == f"{HOST}: the prepare of {IMAGE} stopped: Qumulo Core reads IDLE with no error, as after a quorum change. Run the upgrade again."


@pytest.mark.parametrize("preparing", [True, False], ids=["after-preparing", "at-once"])
def test_a_prepare_that_fails_with_the_error_the_status_held_before_its_202_is_its_refusal(fake, run_module, preparing):
    """The role's second prepare of an image no node can reach meets the first prepare's error in the status."""
    failed = status("IDLE", error="Download of the image timed out", settings=True)
    expect_reads(fake, failed)
    expect_prepare(fake)
    if preparing:
        fake.expect("GET", STATUS, response=status("PREPARING"))
    fake.expect("GET", STATUS, response=failed)

    result = run_module(upgrade, args(state="prepared"))

    assert result["failed"] is True
    assert result["msg"] == f"{HOST}: Qumulo Core refused the image {IMAGE}: Download of the image timed out. {REACH}"
    assert result["changed"] is True


def test_a_prepare_still_preparing_at_wait_timeout_fails_as_changed(fake, run_module):
    expect_reads(fake)
    expect_prepare(fake)
    fake.serve(STATUS, status("PREPARING"))

    result = run_module(upgrade, args(state="prepared", wait_timeout=10))

    assert result["changed"] is True
    assert result["msg"] == still_preparing()


@pytest.mark.parametrize(
    ("found", "shown"),
    [(status("IDLE"), "unknown"), (status("IDLE", image=OTHER, settings=True), OTHER), (status("PREPARED", rolling=False), IMAGE)],
    ids=["idle", "idle with the other prepare's settings", "prepared other settings"],
)
def test_a_prepare_409_whose_read_back_finds_idle_or_a_prepared_that_does_not_match_is_f6_at_once(fake, run_module, fake_clock, found, shown):
    expect_reads(fake)
    expect_prepare(
        fake, status_code=409, response={"error_class": "upgrade_already_in_progress_error", "description": "A previous upgrade is already preparing."}
    )
    fake.expect("GET", STATUS, response=found)

    result = run_module(upgrade, args(state="prepared"))

    state = found["state"].removeprefix("UPGRADE_STATE_")
    assert result["msg"] == (
        f"{HOST} is {state} with the image {shown}, started outside this run. Wait for it to finish, then run the upgrade again, "
        f"or, to upgrade the rest of the fleet meanwhile, {LIMIT}."
    )
    assert (result["changed"], result["final"]) == (False, True)
    assert fake_clock.sleeps == []


def test_the_commit_wait_reads_a_revision_of_five_parts_as_cluster_version_does(fake, run_module):
    expect_commit_from_prepared(fake)
    fake.expect("GET", VERSION, response=running("7.10.3.0.1", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("COMMITTING"))
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert result["to"] == {"version": "7.11.0", "build": NEW_BUILD}


def test_the_commit_wait_rides_out_unanswered_reads_on_both_builds(fake, run_module):
    expect_commit_from_prepared(fake, status("PREPARED", rolling=False), protect=protection(1))
    fake.expect("GET", VERSION, status=503, response={"error_class": "upgrade_without_full_quorum_error", "description": "Full quorum is required."})
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, raises=UNANSWERED)
    fake.expect("GET", VERSION, response=running("7.11.0", NEW_BUILD))
    fake.expect("GET", STATUS, response=status("REBOOTING", rolling=False))
    fake.expect("GET", REBOOT, response=reboot("REBOOT_IN_PROGRESS", 4, 4))
    fake.expect("GET", NODES, response=nodes())

    result = run_module(upgrade, args(reboot="all"))

    assert_ok(result)


def test_a_resent_commit_that_is_refused_otherwise_fails(fake, run_module):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, raises=LOST)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("PREPARED"))
    expect_commit(fake, 503, {"error_class": "upgrade_without_full_quorum_error", "description": "Full quorum is required for upgrades."})

    result = run_module(upgrade, args())

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["commit_sent"] is True


def test_a_resent_commit_whose_answer_is_lost_too_is_waited_on(fake, run_module):
    expect_reads(fake, status("PREPARED"))
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake, raises=LOST)
    fake.expect("GET", VERSION, response=running("7.10.3", OLD_BUILD))
    fake.expect("GET", STATUS, response=status("PREPARED"))
    expect_commit(fake, raises=LOST)
    expect_rolling_done(fake)

    result = run_module(upgrade, args())

    assert_ok(result)
    assert len(requests(fake, COMMIT)) == 2


# The module's own maintenance window (FR-005, FR-017): run on its own, it checks the window before its own commit.

WEEKEND = {"start_hour": 22, "start_minute": 0, "end_hour": 4, "end_minute": 0, "on_days": ["SAT", "SUN"]}
WINDOW = {"maintenance_windows": [WEEKEND], "window_timezone": "America/Los_Angeles"}
WEDNESDAY_MORNING = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)  # 04:00 in Los Angeles, outside
SATURDAY_NIGHT = datetime(2026, 11, 8, 7, 0, tzinfo=UTC)  # 23:00 on Saturday in Los Angeles, inside
F9 = (
    f"{HOST} were not committed: the hop to 7.11.0 is outside their maintenance window, which next opens 2026-11-07 22:00 "
    "America/Los_Angeles. They stay prepared. Run the upgrade again inside the window."
)


def test_outside_its_window_the_module_prepares_and_refuses_f9_before_the_commit(fake, run_module, monkeypatch):
    monkeypatch.setattr(upgrade_utils, "utc_now", lambda: WEDNESDAY_MORNING)
    expect_reads(fake)
    expect_prepare(fake)
    expect_prepared(fake)

    result = run_module(upgrade, args(**WINDOW))

    assert result["failed"] is True
    assert result["msg"].startswith(F9)
    assert result["changed"] is True  # it prepared
    assert result.get("final") is not True
    assert requests(fake, COMMIT) == []


def test_inside_its_window_the_module_commits(fake, run_module, monkeypatch):
    monkeypatch.setattr(upgrade_utils, "utc_now", lambda: SATURDAY_NIGHT)
    expect_reads(fake)
    expect_prepare(fake)
    expect_prepared(fake)
    fake.expect("GET", BLOCKED, response=NOT_BLOCKED)
    expect_commit(fake)
    expect_rolling_done(fake)

    result = run_module(upgrade, args(**WINDOW))

    assert_ok(result)
    assert result["commit_sent"] is True


def test_check_mode_predicts_f9_without_a_write(fake, run_module, monkeypatch):
    monkeypatch.setattr(upgrade_utils, "utc_now", lambda: WEDNESDAY_MORNING)
    expect_reads(fake)

    result = run_module(upgrade, args(**WINDOW), check_mode=True)

    assert_ok(result)
    assert result["plan"]["window"] == {"inside": False, "next_open": "2026-11-08T06:00:00Z", "message": F9}
    assert writes(fake) == []


def test_a_window_without_its_timezone_is_refused_by_the_argument_spec(fake, run_module):
    result = run_module(upgrade, args(maintenance_windows=[WEEKEND]))

    assert result["failed"] is True
    assert "window_timezone" in result["msg"]
    assert writes(fake) == []


def window_setting(name, under_role):
    return f"qumulo_upgrade_{name}" if under_role else name


@UNDER_ROLE
@pytest.mark.parametrize("zone", ["America/Las_Angeles", "UTC ", "../../etc/passwd"])
def test_f34_a_window_timezone_that_is_no_iana_name_is_refused_before_any_request(fake, run_module, zone, under_role):
    result = run_module(upgrade, args(maintenance_windows=[WEEKEND], window_timezone=zone, **run_as(under_role)))

    timezone = window_setting("window_timezone", under_role)
    assert result["failed"] is True
    assert result["msg"] == (
        f"{HOST} declares {timezone} {zone}, which is no IANA timezone name. Set {timezone} for {HOST} to an IANA name, such as America/Los_Angeles."
    )
    assert fake.log == []


@UNDER_ROLE
@pytest.mark.parametrize(
    ("ranges", "found"),
    [
        (
            [{**WEEKEND, "start_hour": 25}],
            "with start_hour 25, which is no whole number from 0 to 23. Set each range's start_hour for {host} to a whole number from 0 to 23.",
        ),
        (
            [{**WEEKEND, "end_minute": 60}],
            "with end_minute 60, which is no whole number from 0 to 59. Set each range's end_minute for {host} to a whole number from 0 to 59.",
        ),
        (
            [{**WEEKEND, "on_days": ["SATURDAY"]}],
            "with on_days SATURDAY, which is no day name. Name each range's days for {host} from SUN, MON, TUE, WED, THU, FRI, SAT and EVERY_DAY.",
        ),
    ],
)
def test_f34_a_range_outside_the_clock_or_the_week_is_refused_before_any_request(fake, run_module, ranges, found, under_role):
    result = run_module(upgrade, args(maintenance_windows=ranges, window_timezone="America/Los_Angeles", **run_as(under_role)))

    assert result["failed"] is True
    assert result["msg"] == f"{HOST} declares {window_setting('maintenance_windows', under_role)} {found.format(host=HOST)}"
    assert fake.log == []


def test_f34_gives_every_bad_field_in_one_message(fake, run_module):
    ranges = [{**WEEKEND, "start_hour": 25, "on_days": ["SATURDAY"]}]

    result = run_module(upgrade, args(maintenance_windows=ranges, window_timezone="America/Las_Angeles"))

    assert result["failed"] is True
    assert [line.split(",")[0] for line in result["msg"].split(f"{HOST} declares ")[1:]] == [
        "window_timezone America/Las_Angeles",
        "maintenance_windows with start_hour 25",
        "maintenance_windows with on_days SATURDAY",
    ]
    assert fake.log == []


# host_name: the name the messages give the cluster, which the role sets to the inventory host (FR-002).


def test_the_messages_name_host_name_in_place_of_api_host(fake, run_module):
    result = run_module(upgrade, args(image="upgrades/qumulo_core_7.11.0.qimg", host_name="edge01"))

    assert result["msg"] == (
        "edge01: the image upgrades/qumulo_core_7.11.0.qimg is neither an http:// or https:// URL nor an absolute path. "
        "Give a URL every node can reach, or an absolute path in the cluster's file system."
    )
    assert fake.log == []


def test_a_refusal_after_the_reads_names_host_name(fake, run_module):
    on(fake, "7.11.1")
    expect_reads(fake)

    result = run_module(upgrade, args(host_name="edge01"))

    assert result["msg"].startswith("edge01 runs Qumulo Core 7.11.1, above the target 7.11.0.")
    assert HOST not in result["msg"]


@pytest.mark.parametrize("options", [{"reboot_set_size": 1}, {"reboot": "rolling"}], ids=["count-declared", "rolling-declared"])
def test_with_host_name_set_as_the_role_sets_it_w10_is_left_to_the_plan(fake, run_module, options):
    expect_reads(fake, protect=protection(0))
    expect_prepare(fake, body={"image_path": IMAGE, "auto_commit": False, "do_rolling_reboot": False})
    expect_prepared(fake, rolling=False)

    result = run_module(upgrade, args(state="prepared", host_name="edge01", **options))

    assert_ok(result)
    assert result["reboot"]["mode"] == "all"
    assert result.get("warnings", []) == []
