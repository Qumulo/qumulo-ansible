# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.license, driven through FakeQumulo. The module talks only to the cluster, never to Nexus."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.common import parameters
from ansible.module_utils.testing import patch_module_args
from ansible_collections.qumulo.cluster.plugins.module_utils.capacity import FLOOR_MARGIN
from ansible_collections.qumulo.cluster.plugins.module_utils.licensing import limits_above
from ansible_collections.qumulo.cluster.plugins.modules import license as license_module
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_nexus import NEXUS_TOKEN
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
OLD_ID = "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02"
STATUS = "/v1/license/status"
ENFORCEMENT = "/v1/license/enforcement"
FILE_SYSTEM = "/v1/file-system"
CLAMP = "/v1/capacity/clamp"
VALIDATE = "/v1/debug/license/validate"
ACTIVATE = "/v1/debug/license/activate"
TB = 10**12
W = 10**11  # capacity.py's FLOOR_MARGIN
# File system sizes an unlicensed edge formed with, just below its 1 TB floor.
FORMED, FORMED_LOWER = 996_432_412_672, 987_794_903_040
KEY = "c2lnbmVkIGJ5IE5leHVz"
OTHER_KEY = "YW5vdGhlciBsaWNlbnNl"
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))


def document(cluster_id=CLUSTER_ID, cap=4 * TB, expires="2027-10-31T00:00:00Z", key=KEY):
    """A license document as Nexus issues it: its license_data, and the file a user downloads."""
    details = {"usable_capacity_cap": None if cap is None else str(cap), "raw_capacity_cap": None, "per_node_throughput_cap": None}
    return {"cluster_uuid": cluster_id, "expiration_date": expires, "earliest_expiration_date": expires, "details": details, "license_key": key}


DOCUMENT = document()
NONE = {"state": "LICENSE_STATE_NONE", "license": None}


def active(**kw):
    """/v1/license/status for a cluster holding document(**kw), which the cluster echoes with object_bridges added."""
    held = document(**kw)
    return {"state": "LICENSE_STATE_ACTIVE", "license": {**held, "details": {**held["details"], "object_bridges": "OFF"}}}


def facts(status):
    held = status.get("license") or {}
    cap = (held.get("details") or {}).get("usable_capacity_cap")
    return {
        "state": status["state"],
        "cluster_uuid": held.get("cluster_uuid"),
        "usable_capacity_cap": None if cap is None else int(cap),
        "expiration_date": held.get("expiration_date"),
        "enforced": True,
    }


def expect_status(fake, status):
    fake.expect("GET", STATUS, response=status)
    fake.expect("GET", ENFORCEMENT, response={"license_is_enforced": True})


def expect_file_system(fake, total, clamp=None, cap=4 * TB):
    """The file system's size; when it is more than W below the license's cap, also the clamp, which may be what sized it."""
    expect_size(fake, total)
    if total + W < cap:
        expect_clamp(fake, clamp)


def expect_size(fake, total):
    fake.expect("GET", FILE_SYSTEM, response={"block_size_bytes": 4096, "total_size_bytes": str(total), "free_size_bytes": "0"})


def expect_clamp(fake, clamp):
    fake.expect("GET", CLAMP, response={"capacity_clamp": None if clamp is None else str(clamp)})


def expect_activation(fake, body=None):
    body = DOCUMENT if body is None else body
    fake.expect("POST", VALIDATE, body=body, response=None)
    fake.expect("POST", ACTIVATE, body=body, response=None)


def cluster_writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


def requests(fake):
    return [e for e in fake.log if not e.background]


@pytest.fixture
def every_sleep(fake_clock, monkeypatch):
    """conftest's fake_clock, also recording a bare time.sleep, so its sleeps list every sleep the module takes."""
    monkeypatch.setattr(time, "sleep", fake_clock.sleep)
    return fake_clock


@pytest.fixture
def run_module(run_module):
    """conftest's run_module, also failing any test whose result or message carries a license key."""

    def run(*args, **kwargs):
        result = run_module(*args, **kwargs)
        text = json.dumps(result)
        assert KEY not in text and OTHER_KEY not in text, "a result carries a license key"
        return result

    return run


def test_an_active_license_for_this_cluster_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active())

    result = run_module(license_module, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert (result["changed"], result["license_status"], result["license_pending"]) == (False, "active", False)
    assert result["license"] == facts(active())
    assert result["cluster_uuid"] == CLUSTER_ID


def test_no_license_is_reported_absent_at_once_and_unchanged(fake_api, run_module, every_sleep):
    """The module never waits, and reads nothing but the license: the fake fails any other request, such as one for the monitoring settings."""
    fake = fake_api()
    expect_status(fake, NONE)

    result = run_module(license_module, CONNECTION)

    assert (result["changed"], result["license_status"], result["license_pending"]) == (False, "absent", True), result.get("msg")
    assert (result["activated"], result["needs_quorum_bounce"]) == (False, False)
    assert result["license"]["state"] == "LICENSE_STATE_NONE"
    assert result["cluster_uuid"] == CLUSTER_ID, "the hand-off names the UUID the license must be issued for"
    assert cluster_writes(fake) == []
    assert every_sleep.sleeps == []


def test_a_license_for_the_old_uuid_counts_as_none(fake_api, run_module):
    """A license is bound to the cluster UUID, so a rebuilt edge's license for its previous UUID does not count."""
    fake = fake_api()
    expect_status(fake, active(cluster_id=OLD_ID))

    result = run_module(license_module, CONNECTION)

    assert (result["license_status"], result["license_pending"]) == ("absent", True), result.get("msg")
    assert result["cluster_uuid"] == CLUSTER_ID, "the hand-off names the UUID a new license must be issued for, not the old one"


def test_a_result_has_only_the_keys_return_documents(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)
    expect_activation(fake)
    expect_status(fake, active())
    expect_file_system(fake, 1 * TB)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT}, diff=True)

    assert set(result) <= set(yaml.safe_load(license_module.RETURN)) | {"changed", "warnings", "diff"}, result.get("msg")


def test_a_document_is_validated_then_activated(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active(cap=1 * TB, key=OTHER_KEY))
    expect_activation(fake)
    expect_status(fake, active())
    expect_file_system(fake, 1 * TB)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT}, diff=True)

    assert (result["changed"], result["activated"], result["license_status"]) == (True, True, "active"), result.get("msg")
    assert cluster_writes(fake) == [f"POST {VALIDATE}", f"POST {ACTIVATE}"]
    assert result["needs_quorum_bounce"] is True
    assert result["license"]["usable_capacity_cap"] == 4 * TB
    assert result["diff"] == {
        "before": {"state": "LICENSE_STATE_ACTIVE", "usable_capacity_cap": 1 * TB, "expiration_date": "2027-10-31T00:00:00Z"},
        "after": {"state": "LICENSE_STATE_ACTIVE", "usable_capacity_cap": 4 * TB, "expiration_date": "2027-10-31T00:00:00Z"},
    }


def test_a_document_the_cluster_holds_is_unchanged_and_needs_no_bounce(fake_api, run_module):
    """The bounce is reported once, by the run the license arrived in; later runs read nothing more."""
    fake = fake_api()
    expect_status(fake, active())

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert (result["changed"], result["activated"], result["license_status"]) == (False, False, "active"), result.get("msg")
    assert result["needs_quorum_bounce"] is False
    assert cluster_writes(fake) == []


def test_a_document_given_as_json_text_is_taken(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active())

    result = run_module(license_module, {**CONNECTION, "document": json.dumps(DOCUMENT)})

    assert (result.get("failed"), result["changed"]) == (None, False), result.get("msg")


def test_license_is_another_name_for_document(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active())

    result = run_module(license_module, {**CONNECTION, "license": DOCUMENT})

    assert (result.get("failed"), result["changed"], result["license_status"]) == (None, False, "active"), result.get("msg")


def test_a_held_key_with_another_body_is_sent_to_validate(fake_api, run_module):
    """The key signs the body, so a changed body under a held key is an altered document, which the cluster refuses."""
    fake = fake_api()
    expect_status(fake, active())
    altered = document(cap=8 * TB)
    refused = {"error_class": "api_license_signature_mismatch_error", "description": "license parameters do not match those used to generate the license key"}
    fake.expect("POST", VALIDATE, body=altered, status=400, response=refused)

    result = run_module(license_module, {**CONNECTION, "document": altered})

    assert (result["failed"], result["changed"]) == (True, False)
    assert "the cluster refused the license document" in result["msg"]


def test_check_mode_asks_the_cluster_to_validate_and_predicts_the_activation(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)
    fake.expect("POST", VALIDATE, body=DOCUMENT, response=None)
    expect_file_system(fake, 1 * TB)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT}, check_mode=True, diff=True)

    assert (result["changed"], result["activated"], result["license_status"]) == (True, True, "active"), result.get("msg")
    assert result["needs_quorum_bounce"] is True
    assert result["diff"]["after"] == {"state": "LICENSE_STATE_ACTIVE", "usable_capacity_cap": 4 * TB, "expiration_date": "2027-10-31T00:00:00Z"}
    assert cluster_writes(fake) == [f"POST {VALIDATE}"]


def test_check_mode_predicts_a_refused_document_as_a_failure(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)
    fake.expect("POST", VALIDATE, status=400, response={"error_class": "api_license_expired_error", "description": "License has expired."})

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT}, check_mode=True)

    assert (result["failed"], result["changed"]) == (True, False)
    assert "the cluster refused the license document: License has expired." in result["msg"]
    assert cluster_writes(fake) == [f"POST {VALIDATE}"]


def test_a_document_the_cluster_refuses_fails_before_activation(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)
    refused = {"error_class": "api_license_signature_mismatch_error", "description": "license parameters do not match those used to generate the license key"}
    fake.expect("POST", VALIDATE, status=400, response=refused)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert (result["failed"], result["changed"]) == (True, False)
    assert "the cluster refused the license document" in result["msg"]
    assert "license parameters do not match those used to generate the license key" in result["msg"]
    assert cluster_writes(fake) == [f"POST {VALIDATE}"]


def test_activation_without_the_debug_privilege_fails_naming_it(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)
    fake.expect("POST", VALIDATE, status=403, response={"error_class": "permission_denied_error", "description": "denied"})

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert (result["failed"], result["changed"]) == (True, False)
    assert "PRIVILEGE_DEBUG" in result["msg"]


def test_a_version_without_the_license_routes_fails_naming_the_gap(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)
    fake.expect("POST", VALIDATE, status=404, response={"error_class": "http_not_found_error", "description": "not found"})

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert (result["failed"], result["changed"]) == (True, False)
    assert "offers no route to apply a license document" in result["msg"]
    assert "G-LICENSE" in result["msg"]
    assert "wait" not in result["msg"]


def test_a_lost_validate_answer_is_asked_again(fake_api, run_module, fake_clock):
    """Validating changes nothing, so a lost answer is no possible change."""
    fake = fake_api()
    expect_status(fake, NONE)
    fake.expect("POST", VALIDATE, body=DOCUMENT, raises=LOST)
    expect_activation(fake)
    expect_status(fake, active())
    expect_file_system(fake, 4 * TB)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert (result.get("failed"), result["changed"], result["activated"]) == (None, True, True), result.get("msg")


def test_a_document_for_another_cluster_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(license_module, {**CONNECTION, "document": document(cluster_id=OLD_ID)})

    assert result["failed"] is True
    assert f"the license document is for cluster {OLD_ID}, but the cluster at qumulo.example.com is {CLUSTER_ID}" in result["msg"]
    assert f"replace it with the license document issued for {CLUSTER_ID}" in result["msg"]
    assert requests(fake) == []


def test_a_document_in_another_case_of_the_uuid_is_this_cluster_s(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active())

    result = run_module(license_module, {**CONNECTION, "document": document(cluster_id=CLUSTER_ID.upper())})

    assert (result.get("failed"), result["changed"]) == (None, False), result.get("msg")


@pytest.mark.parametrize(
    ("given", "named"),
    [
        ({key: value for key, value in DOCUMENT.items() if key != "license_key"}, "license_key"),
        ({key: value for key, value in DOCUMENT.items() if key != "earliest_expiration_date"}, "earliest_expiration_date"),
        ({**DOCUMENT, "usable_capacity_cap": "4000000000000"}, "usable_capacity_cap"),
    ],
    ids=["no_key", "no_earliest_expiration", "a_license_request"],
)
def test_a_document_without_a_field_or_with_an_unknown_one_fails_before_any_request(fake_api, run_module, given, named):
    """A missing field would be filled with null and an unknown one dropped, either of which breaks the signature."""
    fake = fake_api()

    result = run_module(license_module, {**CONNECTION, "document": given})

    assert result["failed"] is True
    assert named in result["msg"]
    assert requests(fake) == []


@pytest.mark.parametrize("as_text", [False, True], ids=["dict", "json_text"])
def test_the_key_never_reaches_the_module_log_or_the_invocation(fake_api, monkeypatch, capsys, as_text):
    """ansible-core logs every option that is not no_log, and returns them all in the result's invocation.

    A document given as JSON text, as from a file lookup, is masked too: the key is a no_log sub-option either way.

    ansible-core 2.20 always returns the invocation and takes no flag for it; later versions return it when asked.
    2.20 and 2.21 mask no_log values in the module; from 2.22 the module lists them in _ansible_new_secrets and the
    controller masks them, so there the key must be among them. That list holds only the secrets new to the process,
    so each case uses a key no other test in this process has registered.
    """
    key = "bG9nIHRlc3Q6IHRleHQ=" if as_text else "bG9nIHRlc3Q6IGRpY3Q="
    given = json.dumps(document(key=key)) if as_text else document(key=key)
    logged = []
    monkeypatch.setattr(AnsibleModule, "log", lambda self, msg, log_args=None: logged.append(msg))
    fake = fake_api()
    expect_status(fake, active(key=key))
    asks_for_invocation = {"_ansible_inject_invocation": True} if "inject_invocation" in parameters.PASS_VARS else {}

    with patch_module_args({**CONNECTION, "document": given, **asks_for_invocation}), pytest.raises(SystemExit):
        license_module.main()

    assert len(logged) == 1 and "document=" in logged[0], logged
    assert key not in logged[0]
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result.get("failed") is not True, result.get("msg")
    if "invocation" not in result:
        pytest.skip("this ansible-core returns no invocation")
    assert result["invocation"]["module_args"]["document"]["cluster_uuid"] == CLUSTER_ID
    if "_ansible_new_secrets" in result:
        assert key in result["_ansible_new_secrets"], "the controller masks only the secrets the module lists"
    else:
        assert key not in json.dumps(result)


@pytest.mark.parametrize(("status", "settled"), [(active(), True), (NONE, False)], ids=["it_arrived", "it_did_not"])
def test_an_activation_whose_answer_is_lost_is_settled_by_the_license(fake_api, run_module, status, settled):
    fake = fake_api()
    expect_status(fake, NONE)
    fake.expect("POST", VALIDATE, body=DOCUMENT, response=None)
    fake.expect("POST", ACTIVATE, body=DOCUMENT, raises=LOST)
    expect_status(fake, status)
    if settled:
        expect_file_system(fake, 4 * TB)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert result["changed"] is True
    assert result.get("failed") is (None if settled else True), result.get("msg")
    if not settled:
        assert "may or may not have been applied" in result["msg"]


@pytest.mark.parametrize("read_back", [NONE, active(key=OTHER_KEY)], ids=["no_license", "another_license"])
def test_an_activation_the_cluster_does_not_take_fails_as_a_change(fake_api, run_module, read_back):
    fake = fake_api()
    expect_status(fake, NONE if read_back is NONE else active(key=OTHER_KEY))
    expect_activation(fake)
    expect_status(fake, read_back)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT})

    assert (result["failed"], result["changed"], result["license_pending"], result["license_status"]) == (True, True, True, "absent")
    assert f"the cluster accepted the license document but reports {read_back['state']}" in result["msg"]
    assert "not that license" in result["msg"]


@pytest.mark.parametrize(
    ("cap", "total", "clamp", "bounce"),
    [
        (4 * TB, 4 * TB - W - 1, None, True),
        (4 * TB, 4 * TB - W, None, False),
        (4 * TB, 1 * TB, 2 * TB, True),
        (4 * TB, 4 * TB, None, False),
        (4 * TB, 1 * TB, 1 * TB, False),
        (None, 1 * TB, None, False),
        (1 * TB, FORMED, None, False),
        (4 * TB, FORMED, None, True),
    ],
    ids=[
        "cap_above_file_system",
        "cap_within_the_margin",
        "clamp_above_file_system",
        "cap_equal",
        "clamp_sized_the_file_system",
        "no_cap",
        "1_tb_cap_over_a_formed_edge",
        "4_tb_cap_over_a_formed_edge",
    ],
)
def test_needs_quorum_bounce_when_the_new_cap_would_grow_the_file_system(fake_api, run_module, cap, total, clamp, bounce):
    """Provisioning follows the smaller of the cap and the clamp, so a clamp that sized the file system needs no bounce.

    The server rounds the file system, the cap and the clamp to units the collection cannot read, so a limit counts as
    above the file system only when it is more than W above it, and the clamp is read only then.
    """
    fake = fake_api()
    expect_status(fake, NONE)
    expect_activation(fake, body=document(cap=cap))
    expect_status(fake, active(cap=cap))
    if cap:  # without a cap there is nothing to compare with the file system
        expect_file_system(fake, total, clamp, cap=cap)

    result = run_module(license_module, {**CONNECTION, "document": document(cap=cap)})

    assert result["needs_quorum_bounce"] is bounce, result.get("msg")


@pytest.mark.parametrize(
    ("cap", "total", "clamp", "waits"),
    [
        (4 * TB, FORMED, 2 * TB, True),
        (4 * TB, FORMED_LOWER, 2 * TB, True),
        (4 * TB, FORMED, None, True),
        (4 * TB, TB, 2 * TB, True),
        (1 * TB, FORMED, 2 * TB, False),
        (4 * TB, FORMED, 1_005_022_347_264, False),
        (4 * TB, FORMED, TB + W, False),
        (4 * TB, FORMED_LOWER, 1_090_000_000_000, False),
        (4 * TB, 57_263_472_640, 2 * TB, False),
        (4 * TB, TB - W, 2 * TB, False),
        (4 * TB, TB + 1, 2 * TB, False),
        (None, FORMED, 2 * TB, False),
    ],
    ids=[
        "formed_at_the_floor",
        "formed_lower",
        "unset_clamp_limits_nothing",
        "window_top",
        "cap_of_1_tb",
        "clamp_stored_at_the_floor",
        "limit_at_the_margin_above_1_tb",
        "clamp_within_the_margin_of_the_floor_not_of_the_file_system",
        "small_platform",
        "window_bottom",
        "above_the_window",
        "no_cap",
    ],
)
def test_a_held_cap_waits_for_the_formation_of_an_edge_formed_unlicensed(fake_api, run_module, cap, total, clamp, waits):
    """An edge formed unlicensed has min(platform, 1 TB); a cap that would grow it waits for a quorum formation.

    The window is a file system of at most 10^12 bytes and more than 10^12 - W, and the limit, min(cap, clamp), must be
    more than W above 10^12, not above the file system. The clamp is read only inside the window, and only for a cap
    more than W above 10^12.
    """
    fake = fake_api()
    expect_status(fake, active(cap=cap))
    expect_size(fake, total)
    if cap is not None and cap > TB + W and TB - W < total <= TB:
        expect_clamp(fake, clamp)

    result = run_module(license_module, {**CONNECTION, "check_waiting_cap": True})

    assert (result["cap_waits_for_formation"], result["file_system_size_bytes"]) == (waits, total), result.get("msg")
    assert (result["changed"], result["activated"], result["needs_quorum_bounce"]) == (False, False, False)
    assert cluster_writes(fake) == []


def test_a_held_document_given_again_tells_whether_its_cap_waits(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active())
    expect_size(fake, FORMED)
    expect_clamp(fake, 2 * TB)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT, "check_waiting_cap": True})

    assert (result["changed"], result["activated"], result["cap_waits_for_formation"]) == (False, False, True), result.get("msg")


def test_without_check_waiting_cap_nothing_more_is_read(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, active())

    result = run_module(license_module, CONNECTION)

    assert result["cap_waits_for_formation"] is False, result.get("msg")
    assert "file_system_size_bytes" not in result


def test_a_license_activated_in_this_run_does_not_wait_for_a_formation(fake_api, run_module):
    """Its own run reports the bounce instead, and reads nothing more for the waiting cap."""
    fake = fake_api()
    expect_status(fake, NONE)
    expect_activation(fake)
    expect_status(fake, active())
    expect_file_system(fake, FORMED, None)

    result = run_module(license_module, {**CONNECTION, "document": DOCUMENT, "check_waiting_cap": True})

    assert (result["activated"], result["needs_quorum_bounce"], result["cap_waits_for_formation"]) == (True, True, False), result.get("msg")
    assert "file_system_size_bytes" not in result


def test_no_license_reads_nothing_for_the_waiting_cap(fake_api, run_module):
    fake = fake_api()
    expect_status(fake, NONE)

    result = run_module(license_module, {**CONNECTION, "check_waiting_cap": True})

    assert (result["license_status"], result["cap_waits_for_formation"]) == ("absent", False), result.get("msg")
    assert "file_system_size_bytes" not in result


@pytest.mark.parametrize(
    ("reference", "cap", "clamp", "above"),
    [
        (TB, 4 * TB, None, True),
        (TB, 4 * TB, TB + W + 1, True),
        (TB, 4 * TB, TB + W, False),
        (TB, TB + W, None, False),
        (TB, None, 4 * TB, False),
        (FORMED_LOWER, 4 * TB, 1_090_000_000_000, True),
    ],
)
def test_limits_above_compares_the_smaller_limit_through_the_margin(reference, cap, clamp, above):
    assert limits_above(reference, cap, clamp) is above


def test_the_margin_is_10_to_the_11_bytes():
    assert FLOOR_MARGIN == W


@pytest.mark.parametrize(
    "option",
    [{"fetch": True}, {"nexus_api_token": NEXUS_TOKEN}, {"nexus_api_url": "https://nexus.example.com"}],
    ids=["fetch", "nexus_api_token", "nexus_api_url"],
)
def test_the_module_takes_no_nexus_option(fake_api, run_module, option):
    """A customer organization's users cannot see licenses in Nexus, so the module never reads them there (G-LICENSE)."""
    fake = fake_api()

    result = run_module(license_module, {**CONNECTION, **option})

    assert result["failed"] is True
    assert "Unsupported parameters" in result["msg"] and next(iter(option)) in result["msg"]
    assert requests(fake) == []


def test_a_nexus_token_in_the_environment_is_not_used(fake_api, run_module, monkeypatch):
    """no_real_nexus fails any Nexus request, so this passes only when the module never reads the token."""
    monkeypatch.setenv("QUMULO_NEXUS_API_TOKEN", NEXUS_TOKEN)
    fake = fake_api()
    expect_status(fake, NONE)

    result = run_module(license_module, CONNECTION)

    assert (result.get("failed"), result["changed"], result["license_status"]) == (None, False, "absent"), result.get("msg")


@pytest.mark.parametrize("option", [{"wait": True}, {"wait": False}, {"wait_timeout": 60}], ids=["wait", "no_wait", "wait_timeout"])
def test_the_module_takes_no_wait_option(fake_api, run_module, option, every_sleep):
    """A license applied through the API is active at once, so the module never waits for one."""
    fake = fake_api()

    result = run_module(license_module, {**CONNECTION, **option})

    assert result["failed"] is True
    assert "Unsupported parameters" in result["msg"] and next(iter(option)) in result["msg"]
    assert requests(fake) == []
    assert every_sleep.sleeps == []


def test_license_status_is_active_or_absent():
    assert yaml.safe_load(license_module.RETURN)["license_status"]["choices"] == ["active", "absent"]


def test_return_documents_nothing_read_from_nexus():
    assert "nexus_license" not in yaml.safe_load(license_module.RETURN)


def test_the_rename_left_no_redirect_from_nexus_license():
    """No release shipped nexus_license, so the collection has one name for the module and routes nothing to it."""
    runtime = yaml.safe_load((Path(license_module.__file__).parents[2] / "meta" / "runtime.yml").read_text(encoding="utf-8"))
    assert "nexus_license" not in (runtime.get("plugin_routing") or {}).get("modules", {})
