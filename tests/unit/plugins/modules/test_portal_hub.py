# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.portal_hub, driven through FakeQumulo. Response bodies follow what Qumulo Core 7.10.3 returns."""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import portal_hub
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "hub01.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SPOKE_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
SPOKE_HOST = {"address": "10.0.1.20", "port": 3713}
ARGS = {"spoke_cluster_uuid": SPOKE_UUID, "spoke_hosts": [{"address": "10.0.1.20"}], "authorized_roots": ["/data"]}
HUBS = "/v2/portal/hubs/"
HUB = "/v2/portal/hubs/3"
DATA_ID = "200"
ARCHIVE_ID = "201"
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))
GONE = {"error_class": "fs_portal_not_found_error", "description": "Hub 3 not found"}


def hub(*, spoke=SPOKE_UUID, hosts=(), type_="PORTAL_READ_WRITE", state="PENDING", status="INACTIVE", pending=(), authorized=()):
    return {
        "id": 3,
        "type": type_,
        "state": state,
        "status": status,
        "spoke_hosts": list(hosts),
        "spoke_cluster_uuid": spoke,
        "spoke_cluster_name": "edge01",
        "filesystem_uuid": CLUSTER_ID,
        "pending_roots": list(pending),
        "authorized_roots": list(authorized),
    }


ACCEPTED = {"state": "ACCEPTED", "status": "ACTIVE", "hosts": [SPOKE_HOST]}


def expect_listed(fake, *entries):
    fake.expect("GET", HUBS, response={"entries": list(entries)})


def expect_root_path(fake, file_id=DATA_ID, path="/data/"):
    fake.expect("GET", f"/v1/files/{file_id}/info/attributes", response={"path": path, "id": file_id, "type": "FS_FILE_TYPE_DIRECTORY"})


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method != "GET" and not e.background]


def test_pending_proposal_is_accepted_with_its_spoke_hosts_and_root(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID]))
    expect_root_path(fake)
    fake.expect(
        "POST", f"{HUB}/accept", body={"spoke_hosts": [SPOKE_HOST], "authorized_roots": [DATA_ID]}, response=hub(state="ACCEPTED", status="DISCONNECTED")
    )
    fake.expect("GET", HUB, response=hub(state="ACCEPTED", status="DISCONNECTED", hosts=[SPOKE_HOST], authorized=[DATA_ID]))
    fake.expect("GET", HUB, response=hub(**ACCEPTED, authorized=[DATA_ID]))

    result = run_module(portal_hub, {**CONNECTION, **ARGS}, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["portal"] == {
        "hub_cluster_uuid": CLUSTER_ID,
        "spoke_cluster_uuid": SPOKE_UUID,
        "hub_hosts": None,
        "spoke_hosts": [SPOKE_HOST],
        "type": "read_write",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "roots": [{"spoke_root": None, "hub_root": "/data", "authorized": True}],
    }
    assert (result["diff"]["before"]["state"], result["diff"]["after"]["state"]) == ("PENDING", "ACCEPTED")
    assert fake_clock.sleeps == [5.0]


def test_accept_authorizes_only_the_listed_roots(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID, ARCHIVE_ID]))
    expect_root_path(fake)
    expect_root_path(fake, ARCHIVE_ID, "/archive/")
    fake.expect("POST", f"{HUB}/accept", body={"spoke_hosts": [SPOKE_HOST], "authorized_roots": [DATA_ID]})
    fake.expect("GET", HUB, response=hub(**ACCEPTED, pending=[ARCHIVE_ID], authorized=[DATA_ID]))

    result = run_module(portal_hub, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["portal"]["roots"] == [
        {"spoke_root": None, "hub_root": "/archive", "authorized": False},
        {"spoke_root": None, "hub_root": "/data", "authorized": True},
    ]


def test_accept_then_sets_a_differing_type(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID]))
    expect_root_path(fake)
    fake.expect("POST", f"{HUB}/accept", response=hub(state="ACCEPTED", status="DISCONNECTED"))
    fake.expect("PATCH", HUB, body={"type": "PORTAL_READ_ONLY"}, response=hub(state="ACCEPTED", status="DISCONNECTED", type_="PORTAL_READ_ONLY"))
    fake.expect("GET", HUB, response=hub(**ACCEPTED, type_="PORTAL_READ_ONLY", authorized=[DATA_ID]))

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "type": "read_only"})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST {HUB}/accept", f"PATCH {HUB}"]


def test_type_refused_after_the_accept_reports_the_change(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID]))
    expect_root_path(fake)
    fake.expect("POST", f"{HUB}/accept")
    fake.expect("PATCH", HUB, status=400, response={"error_class": "http_bad_request_error", "description": "type not allowed"})

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "type": "read_only"})

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["msg"].endswith("; this task had already accepted the portal")


def test_accepted_matching_hub_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED, authorized=[DATA_ID]))
    expect_root_path(fake)

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "type": "read_write"})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"] == {
        "hub_cluster_uuid": CLUSTER_ID,
        "spoke_cluster_uuid": SPOKE_UUID,
        "hub_hosts": None,
        "spoke_hosts": [SPOKE_HOST],
        "type": "read_write",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "roots": [{"spoke_root": None, "hub_root": "/data", "authorized": True}],
    }
    assert writes(fake) == []


def test_same_spoke_hosts_in_another_order_send_no_patch(fake_api, run_module):
    """QUIRK-005: a PATCH that repeats the stored hosts would still rewrite the record and re-check connectivity."""
    second = {"address": "10.0.1.21", "port": 3713}
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "hosts": [SPOKE_HOST, second]}))

    hosts = [{"address": "10.0.1.21"}, {"address": "10.0.1.20", "port": 3713}]
    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "spoke_hosts": hosts})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_root_whose_directory_is_gone_is_compared_without_failing(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED, authorized=[DATA_ID, ARCHIVE_ID]))
    expect_root_path(fake)
    fake.expect("GET", f"/v1/files/{ARCHIVE_ID}/info/attributes", status=404, response={"error_class": "fs_no_such_entry_error"})

    result = run_module(portal_hub, {**CONNECTION, **ARGS})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"]["roots"] == [
        {"spoke_root": None, "hub_root": None, "authorized": True},
        {"spoke_root": None, "hub_root": "/data", "authorized": True},
    ]


def test_spoke_uuid_matches_whatever_its_case(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID.upper()})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_spoke_uuid_is_reported_in_lower_case(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED, spoke=SPOKE_UUID.upper()))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"]["spoke_cluster_uuid"] == SPOKE_UUID


def test_differing_type_drains_and_waits_until_accepted_as_read_only(fake_api, run_module, fake_clock):
    """The hub shows the old type for a while after the PATCH, then QUIESCING until the spoke's writes reach it."""
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED))
    fake.expect("PATCH", HUB, body={"type": "PORTAL_READ_ONLY"}, response=hub(**{**ACCEPTED, "state": "QUIESCING"}))
    fake.expect("GET", HUB, response=hub(**ACCEPTED))
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "QUIESCING", "status": "INACTIVE"}))
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "QUIESCING", "status": "ACTIVE"}, type_="PORTAL_READ_ONLY"))
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "status": "INACTIVE"}, type_="PORTAL_READ_ONLY"))
    fake.expect("GET", HUB, response=hub(**ACCEPTED, type_="PORTAL_READ_ONLY"))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only"})

    assert result["changed"] is True, result.get("msg")
    assert result["portal"]["type"] == "read_only"
    assert fake_clock.sleeps == [5.0, 5.0, 5.0, 5.0]


@pytest.mark.parametrize("unsettled", [{"state": "QUIESCING"}, {"status": "DISCONNECTED"}], ids=["transition", "not-active"])
def test_unchanged_hub_still_settling_is_waited_out(fake_api, run_module, fake_clock, unsettled):
    """A re-run during a drain finds nothing to change, and waits for the drain as the first run did."""
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, **unsettled}, type_="PORTAL_READ_ONLY"))
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, **unsettled}, type_="PORTAL_READ_ONLY"))
    fake.expect("GET", HUB, response=hub(**ACCEPTED, type_="PORTAL_READ_ONLY"))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only"})

    assert result["changed"] is False, result.get("msg")
    assert (result["portal"]["state"], result["portal"]["status"]) == ("ACCEPTED", "ACTIVE")
    assert writes(fake) == []
    assert fake_clock.sleeps == [5.0]


def test_unchanged_hub_settling_without_wait_is_read_once(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "state": "QUIESCING"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "wait": False})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"]["state"] == "QUIESCING"
    assert fake_clock.sleeps == []


def test_unchanged_hub_that_never_settles_fails_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "status": "DISCONNECTED"}))
    for _attempt in range(3):
        fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "status": "DISCONNECTED"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "wait_timeout": 10})

    assert result["failed"] is True
    assert result["changed"] is False
    assert result["msg"] == (
        f"the hub portal for spoke {SPOKE_UUID} is ACCEPTED (status DISCONNECTED, type read_write) after 10s; waited for ACCEPTED and ACTIVE"
    )


def test_differing_spoke_hosts_and_type_are_patched_together(fake_api, run_module):
    moved = {"address": "10.0.1.30", "port": 3713}
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED))
    fake.expect("PATCH", HUB, body={"spoke_hosts": [moved], "type": "PORTAL_READ_ONLY"})
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "hosts": [moved]}, type_="PORTAL_READ_ONLY"))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "spoke_hosts": [{"address": "10.0.1.30"}], "type": "read_only"})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"PATCH {HUB}"]


def test_pending_root_is_authorized_on_an_accepted_hub(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED, pending=[DATA_ID]))
    expect_root_path(fake)
    fake.expect("POST", f"{HUB}/roots/{DATA_ID}")
    fake.expect("GET", HUB, response=hub(**ACCEPTED, authorized=[DATA_ID]))

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "authorized_roots": ["/data/"]}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"]["roots"] == [{"spoke_root": None, "hub_root": "/data", "authorized": False}]
    assert result["diff"]["after"]["roots"] == [{"spoke_root": None, "hub_root": "/data", "authorized": True}]
    assert writes(fake) == [f"POST {HUB}/roots/{DATA_ID}"]


def test_only_the_pending_root_is_authorized_beside_an_authorized_one(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED, pending=[ARCHIVE_ID], authorized=[DATA_ID]))
    expect_root_path(fake, ARCHIVE_ID, "/archive/")
    expect_root_path(fake)
    fake.expect("POST", f"{HUB}/roots/{ARCHIVE_ID}")
    fake.expect("GET", HUB, response=hub(**ACCEPTED, authorized=[DATA_ID, ARCHIVE_ID]))

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "authorized_roots": ["/data", "/archive"]})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST {HUB}/roots/{ARCHIVE_ID}"]


def test_root_not_proposed_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID]))
    expect_root_path(fake)

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "authorized_roots": ["/archive"]})

    assert result["failed"] is True
    assert result["msg"] == (f"hub root /archive is not proposed by spoke {SPOKE_UUID}; propose it with qumulo.cluster.portal_spoke on the spoke first")
    assert writes(fake) == []


def test_relative_root_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(portal_hub, {**CONNECTION, **ARGS, "authorized_roots": ["data"]})

    assert result["failed"] is True
    assert result["msg"] == "authorized_roots[] must be an absolute path, not 'data'"
    assert fake.log == []


def test_accept_without_spoke_hosts_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub())

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID})

    assert result["failed"] is True
    assert result["msg"] == f"spoke_hosts is needed to accept the hub portal for spoke {SPOKE_UUID}"
    assert writes(fake) == []


def test_missing_proposal_fails_and_names_portal_spoke(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(spoke=CLUSTER_ID))

    result = run_module(portal_hub, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["msg"] == f"spoke {SPOKE_UUID} has proposed no portal to this hub; run qumulo.cluster.portal_spoke on the spoke first"
    assert writes(fake) == []


def test_missing_proposal_in_check_mode_is_predicted_with_a_note(fake_api, run_module):
    """In a check-mode run of the pairing, the spoke's proposal is predicted too, so the hub predicts the accept."""
    fake = fake_api()
    expect_listed(fake)

    result = run_module(portal_hub, {**CONNECTION, **ARGS}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == [f"spoke {SPOKE_UUID} has proposed no portal to this hub yet; qumulo.cluster.portal_spoke on the spoke proposes it"]
    assert result["portal"]["roots"] == [{"spoke_root": None, "hub_root": "/data", "authorized": True}]


def test_check_mode_reports_the_accept_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID]))
    expect_root_path(fake)

    result = run_module(portal_hub, {**CONNECTION, **ARGS}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["after"]["state"] == "ACCEPTED"
    assert writes(fake) == []


def test_hub_being_deleted_fails(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "state": "DELETING"}))

    result = run_module(portal_hub, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["msg"] == f"the hub portal for spoke {SPOKE_UUID} is being deleted; wait until it is gone, then propose it again from the spoke"
    assert writes(fake) == []


def test_two_hubs_for_one_spoke_fail(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(), {**hub(), "id": 4})

    result = run_module(portal_hub, {**CONNECTION, **ARGS})

    assert result["failed"] is True
    assert result["msg"] == f"this cluster has 2 hub portals for spoke {SPOKE_UUID} (ids 3, 4); delete all but one"


def test_suspending_on_7_10_3_fails_as_a_state_that_release_lacks(fake_api, run_module):
    fake = fake_api(version="7.10.3")
    expect_listed(fake, hub(**ACCEPTED))
    fake.expect("PATCH", HUB, body={"type": "PORTAL_READ_ONLY"})
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "SUSPENDING"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only"})

    assert result["failed"] is True
    assert result["msg"] == f"the hub portal for spoke {SPOKE_UUID} reports state SUSPENDING, which Qumulo Core 7.10.3 does not have"


def test_suspending_on_7_11_is_waited_out(fake_api, run_module):
    fake = fake_api(version="7.11.0")
    expect_listed(fake, hub(**ACCEPTED))
    fake.expect("PATCH", HUB, body={"type": "PORTAL_READ_ONLY"})
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "SUSPENDING"}))
    fake.expect("GET", HUB, response=hub(**ACCEPTED, type_="PORTAL_READ_ONLY"))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only"})

    assert result["changed"] is True, result.get("msg")


def test_without_wait_returns_the_portal_read_just_after_the_write(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED))
    fake.expect("PATCH", HUB, body={"type": "PORTAL_READ_ONLY"})
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "QUIESCING"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only", "wait": False})

    assert result["changed"] is True, result.get("msg")
    assert result["portal"]["state"] == "QUIESCING"
    assert fake_clock.sleeps == []


def test_wait_past_the_timeout_names_the_last_state(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED))
    fake.expect("PATCH", HUB, body={"type": "PORTAL_READ_ONLY"})
    for _attempt in range(3):
        fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "QUIESCING", "status": "DISCONNECTED"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "type": "read_only", "wait_timeout": 10})

    assert result["failed"] is True
    assert result["msg"] == (
        f"the hub portal for spoke {SPOKE_UUID} is QUIESCING (status DISCONNECTED, type read_write) after 10s; waited for ACCEPTED and ACTIVE as read_only"
    )
    assert result["changed"] is True


def test_lost_accept_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(pending=[DATA_ID]))
    expect_root_path(fake)
    fake.expect("POST", f"{HUB}/accept", raises=LOST)
    fake.expect("GET", HUB, response=hub(**ACCEPTED, authorized=[DATA_ID]))

    result = run_module(portal_hub, {**CONNECTION, **ARGS})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]


def test_absent_deletes_and_waits_until_gone(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED, type_="PORTAL_READ_ONLY"))
    fake.expect("DELETE", HUB, status=202, response=hub(**{**ACCEPTED, "state": "DELETING"}))
    fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "DELETING"}))
    fake.expect("GET", HUB, status=404, response=GONE)

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["portal"] == {}
    assert fake_clock.sleeps == [5.0]


def test_absent_on_a_hub_already_deleting_waits_without_another_delete(fake_api, run_module):
    """Deleting the spoke portal removes the hub portal too; the hub then only has to be waited out."""
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "state": "DELETING"}))
    fake.expect("GET", HUB, status=404, response=GONE)

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


def test_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake)

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent"})

    assert result["changed"] is False, result.get("msg")


def test_absent_in_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**ACCEPTED))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == []


STALE = (
    f"the hub portal for spoke {SPOKE_UUID} is DISCONNECTED, so a delete without force never finishes, and a forced one discards "
    "spoke writes the hub has not received (G-PORTAL-STALE); set allow_data_loss=true to force the delete"
)


def test_absent_on_a_disconnected_hub_fails_without_allow_data_loss(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "status": "DISCONNECTED"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent"})

    assert result["failed"] is True
    assert result["msg"] == STALE
    assert writes(fake) == []


def test_absent_on_a_disconnected_hub_forces_with_allow_data_loss(fake_api, run_module):
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "status": "DISCONNECTED"}))
    fake.expect("DELETE", f"{HUB}?force=true", status=202)
    fake.expect("GET", HUB, status=404, response=GONE)

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent", "allow_data_loss": True})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"DELETE {HUB}?force=true"]


def test_absent_on_a_deleting_disconnected_hub_waits_then_refuses_without_allow_data_loss(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "state": "DELETING", "status": "DISCONNECTED"}))
    for _attempt in range(2):
        fake.expect("GET", HUB, response=hub(**{**ACCEPTED, "state": "DELETING", "status": "DISCONNECTED"}))

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent", "wait_timeout": 10})

    assert result["failed"] is True
    assert result["msg"] == STALE
    assert writes(fake) == []
    assert fake_clock.sleeps == [5.0, 5.0]


def test_absent_on_a_deleting_disconnected_hub_that_goes_is_unchanged(fake_api, run_module):
    """After the spoke's delete, the hub portal may read DISCONNECTED for the few seconds it is DELETING."""
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "state": "DELETING", "status": "DISCONNECTED"}))
    fake.expect("GET", HUB, status=404, response=GONE)

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert result["portal"] == {}
    assert writes(fake) == []


def test_absent_on_a_stuck_delete_of_a_disconnected_hub_forces_it(fake_api, run_module):
    """A delete without force of a DISCONNECTED hub portal is accepted and then stays DELETING."""
    fake = fake_api()
    expect_listed(fake, hub(**{**ACCEPTED, "state": "DELETING", "status": "DISCONNECTED"}))
    fake.expect("DELETE", f"{HUB}?force=true", status=202)
    fake.expect("GET", HUB, status=404, response=GONE)

    result = run_module(portal_hub, {**CONNECTION, "spoke_cluster_uuid": SPOKE_UUID, "state": "absent", "allow_data_loss": True})

    assert result["changed"] is True, result.get("msg")
