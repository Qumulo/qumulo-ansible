# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.capacity_clamp, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import capacity_clamp
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
OTHER_ID = "0b6d2a41-77c1-4b8e-9a51-2f0e6c3d9e02"
CLAMP = "/v1/capacity/clamp"
FILE_SYSTEM = "/v1/file-system"
NODE_STATE = "/v1/node/state"
ROOT = "/v1/files/%2F/info/attributes"
TB, GB, PB, TIB, GIB = 10**12, 10**9, 10**15, 2**40, 2**30
# Illustrative, not measured: what a stored clamp may carry above the request (QUIRK-012) until
# quirk_clamp_rounding pins the provisioning unit on a live cluster.
ROUNDING = 159_744_000
LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))
REFUSED = URLError(ConnectionRefusedError(111, "Connection refused"))


def stored(value):
    """The clamp as GET /v1/capacity/clamp returns it: bytes as a decimal string, or null."""
    return {"capacity_clamp": None if value is None else str(value)}


def file_system(total):
    return {"block_size_bytes": 4096, "total_size_bytes": str(total), "free_size_bytes": "0", "snapshot_size_bytes": "0"}


def node(state, cluster_id=CLUSTER_ID):
    return {"node_id": 1, "cluster_id": cluster_id, "state": state}


def expect_bounce_and_ready(fake):
    """The node leaves ACTIVE while quorum re-forms, comes back, and serves its file system."""
    fake.expect("GET", NODE_STATE, response=node("STOPPED"))
    fake.expect("GET", NODE_STATE, response=node("ACTIVE"))
    fake.expect("GET", ROOT, response={"path": "/"})


def expect_grow(fake, before, after, read_back):
    fake.expect("GET", CLAMP, response=stored(before))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": after})
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, response=stored(read_back))


def writes(fake):
    return [e for e in fake.log if e.method == "PUT"]


@pytest.mark.parametrize("stored_clamp", [4 * TB, 4 * TB + ROUNDING], ids=["exact", "rounded_up"])
def test_stored_at_or_above_request_is_unchanged(fake_api, run_module, stored_clamp):
    """A stored clamp a little above the request is the request rounded up to a provisioning unit (QUIRK-012)."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(stored_clamp))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["capacity_clamp"] == stored_clamp
    assert result["requested"] == 4 * TB
    assert writes(fake) == []


def test_grow_puts_int_body_and_waits_for_active(fake_api, run_module):
    fake = fake_api()
    expect_grow(fake, 1 * TB, 4 * TB, 4 * TB + ROUNDING)

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["capacity_clamp"] == 4 * TB + ROUNDING
    assert result["requested"] == 4 * TB
    (put,) = writes(fake)
    assert isinstance(put.body["capacity_clamp"], int) and not isinstance(put.body["capacity_clamp"], bool)
    sent = [f"{e.method} {e.path}" for e in fake.log]
    after_put = sent[sent.index(f"PUT {CLAMP}") + 1 :]
    assert after_put == [f"GET {NODE_STATE}", f"GET {NODE_STATE}", f"GET {ROOT}", f"GET {CLAMP}"]


def test_first_clamp_is_measured_against_the_provisioned_capacity(fake_api, run_module):
    """An unset clamp limits nothing, so it is not 0: a first clamp is compared with the file system's size."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(None))
    fake.expect("GET", FILE_SYSTEM, response=file_system(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, response=stored(4 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB}, diff=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": {"capacity_clamp": None}, "after": {"capacity_clamp": 4 * TB}}


@pytest.mark.parametrize(
    ("total", "wanted"),
    [(4 * TB, 4 * TB), (4 * TB + 40 * GB, 4 * TB)],
    ids=["equal", "within_default_tolerance"],
)
def test_first_clamp_at_or_just_below_the_provisioned_capacity_is_written(fake_api, run_module, total, wanted):
    """Default tolerance 1% of the request: 4 TB is 40 GB, so 4.04 TB provisioned is still within it."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(None))
    fake.expect("GET", FILE_SYSTEM, response=file_system(total))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": wanted}, check_mode=True)

    assert result["changed"] is True, result.get("msg")


def test_first_clamp_one_byte_past_the_tolerance_below_the_provisioned_capacity_is_refused(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(None))
    fake.expect("GET", FILE_SYSTEM, response=file_system(4 * TB + 40 * GB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB - 1})

    assert result["failed"] is True
    assert "allow_decrease" in result["msg"]
    assert "no clamp is set" in result["msg"]


def test_first_clamp_below_the_provisioned_capacity_is_a_decrease(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(None))
    fake.expect("GET", FILE_SYSTEM, response=file_system(4 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 1 * TB})

    assert result["failed"] is True
    assert "no clamp is set, and the provisioned capacity is 4000000000000 bytes" in result["msg"]
    assert "allow_decrease" in result["msg"]
    assert writes(fake) == []


def test_bounce_not_seen_waits_out_the_grace_then_checks_readiness(fake_api, run_module, fake_clock):
    """The PUT can answer before quorum is abandoned; a node that stays ACTIVE is watched for a while first."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})
    fake.expect("GET", ROOT, response={"path": "/"})
    fake.expect("GET", CLAMP, response=stored(4 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert result["changed"] is True
    assert fake_clock.now == pytest.approx(30)


def test_bounce_grace_ends_at_wait_timeout(fake_api, run_module, fake_clock, node_state_budgets):
    """A wait_timeout below the grace and not a multiple of the poll interval bounds each read and the last sleep."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})
    fake.expect("GET", ROOT, response={"path": "/"})
    fake.expect("GET", CLAMP, response=stored(4 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB, "wait_timeout": 12})

    assert result["changed"] is True
    assert fake_clock.sleeps == [5, 5, 2]
    after_put = node_state_budgets[1:]  # the first read is the identity guard's, before the PUT
    assert after_put and all(budget <= 5 for budget in after_put)


def test_unreachable_node_after_the_put_is_the_bounce(fake_api, run_module, fake_clock):
    """A node that stops answering has left ACTIVE: the grace ends there, and readiness takes over."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})
    for _attempt in range(4):  # at t = 0, 1, 3 and 5, within the read's 5 s budget
        fake.expect("GET", NODE_STATE, raises=REFUSED)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE"))
    fake.expect("GET", ROOT, response={"path": "/"})
    fake.expect("GET", CLAMP, response=stored(4 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert result["changed"] is True
    assert fake_clock.now == pytest.approx(5)


def test_failed_wait_after_the_write_reports_the_change(fake_api, run_module, fake_clock):
    fake = fake_api(state="STOPPED")
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB, "wait_timeout": 20})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "not ACTIVE after 20s" in result["msg"]
    assert len(writes(fake)) == 1


def test_another_cluster_after_the_bounce_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})
    fake.expect("GET", NODE_STATE, response=node("STOPPED"))
    fake.expect("GET", NODE_STATE, response=node("ACTIVE", cluster_id=OTHER_ID))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert (result["failed"], result["changed"]) == (True, True)
    assert OTHER_ID in result["msg"]
    assert CLUSTER_ID in result["msg"]


@pytest.mark.parametrize(
    ("before", "after", "read_back", "allow_decrease"),
    [(1 * TB, 4 * TB, 1 * TB, False), (8 * TB, 4 * TB, 8 * TB, True)],
    ids=["grow_read_back_below", "decrease_read_back_beyond_tolerance"],
)
def test_read_back_that_is_not_the_request_did_not_converge(fake_api, run_module, before, after, read_back, allow_decrease):
    fake = fake_api()
    expect_grow(fake, before, after, read_back)

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": after, "allow_decrease": allow_decrease})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "did not converge" in result["msg"]
    assert len(writes(fake)) == 1


def test_read_back_above_the_tolerance_warns_with_the_excess(fake_api, run_module):
    fake = fake_api()
    expect_grow(fake, 1 * TB, 4 * TB, 4 * TB + 50 * GB)

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert result["changed"] is True
    assert any("50000000000" in w and "rounding_tolerance" in w for w in result["warnings"])


def expect_baseline(fake, before, total):
    fake.expect("GET", CLAMP, response=stored(before))
    if before is None:
        fake.expect("GET", FILE_SYSTEM, response=file_system(total))


@pytest.mark.parametrize(("before", "total", "wanted"), [(None, 1 * TB, 1 * TB + 5 * PB), (2 * PB, None, 7 * PB)], ids=["unset", "stored"])
def test_one_write_may_raise_the_clamp_by_exactly_5_pb(fake_api, run_module, before, total, wanted):
    fake = fake_api()
    expect_baseline(fake, before, total)

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": wanted}, check_mode=True)

    assert result["changed"] is True, result.get("msg")


@pytest.mark.parametrize(("before", "total", "wanted"), [(None, 1 * TB, 1 * TB + 5 * PB + 1), (2 * PB, None, 7 * PB + 1)], ids=["unset", "stored"])
def test_one_write_may_not_raise_the_clamp_by_more_than_5_pb(fake_api, run_module, before, total, wanted):
    fake = fake_api()
    expect_baseline(fake, before, total)

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": wanted}, check_mode=True)

    assert result["failed"] is True
    assert "at most 5 PB (decimal)" in result["msg"]


def test_decrease_refused_without_allow_decrease(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(8 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert result["failed"] is True
    assert "allow_decrease" in result["msg"]
    assert "provisioned capacity does not shrink" in result["msg"]
    assert writes(fake) == []


def test_default_tolerance_of_one_percent_counts_as_rounded_up(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(4 * TB + 40 * GB))

    assert run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": "4TB"})["changed"] is False


def test_one_byte_past_the_default_tolerance_is_a_decrease(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(4 * TB + 40 * GB + 1))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": "4TB"})

    assert result["failed"] is True
    assert "allow_decrease" in result["msg"]


def test_rounding_seen_on_a_small_edge_counts_as_the_request(fake_api, run_module):
    """On a 7.10.3A docker edge, a first clamp one byte above the file system (53.33 GiB) was stored as 56 GiB (QUIRK-012)."""
    total, wanted, kept = 57_263_472_640, 57_263_472_641, 60_126_646_272
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(None))
    fake.expect("GET", FILE_SYSTEM, response=file_system(total))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": wanted})
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, response=stored(kept))
    fake.expect("GET", CLAMP, response=stored(kept))

    first = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": wanted})
    again = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": wanted})

    assert (first["changed"], first["capacity_clamp"], first["warnings"]) == (True, kept, [])
    assert not again.get("failed"), again.get("msg")
    assert again["changed"] is False


def test_default_tolerance_is_at_least_8_gib(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(50 * GIB + 8 * GIB))

    assert run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 50 * GIB})["changed"] is False


def test_one_byte_past_the_8_gib_default_is_a_decrease(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(50 * GIB + 8 * GIB + 1))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 50 * GIB})

    assert result["failed"] is True
    assert f"rounding_tolerance ({8 * GIB} bytes)" in result["msg"]


@pytest.mark.parametrize(("excess", "changed"), [(2 * TIB // 100, False), (2 * TIB // 100 + 1, None)], ids=["at_1_percent", "past_1_percent"])
def test_one_percent_is_the_default_above_800_gib(fake_api, run_module, excess, changed):
    """Above 800 GiB, 1% of the request is more than 8 GiB, and is the default."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(2 * TIB + excess))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 2 * TIB})

    if changed is None:
        assert result["failed"] is True
        assert f"rounding_tolerance ({2 * TIB // 100} bytes)" in result["msg"]
    else:
        assert result["changed"] is changed


def test_explicit_tolerance_counts_up_to_its_end(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(4 * TB + 200 * GB))

    assert run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": "4TB", "rounding_tolerance": "200GB"})["changed"] is False


def test_one_byte_past_an_explicit_tolerance_is_a_decrease(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(4 * TB + 200 * GB + 1))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": "4TB", "rounding_tolerance": "200GB"})

    assert result["failed"] is True
    assert "rounding_tolerance (200000000000 bytes)" in result["msg"]


def test_decrease_with_allow_decrease_puts_and_warns(fake_api, run_module):
    fake = fake_api()
    expect_grow(fake, 8 * TB, 4 * TB, 4 * TB)

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB, "allow_decrease": True})

    assert result["changed"] is True
    assert result["capacity_clamp"] == 4 * TB
    assert any("provisioned capacity does not shrink" in w for w in result["warnings"])


def test_lost_connection_after_put_reconciles_by_reading(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB}, raises=LOST)
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, response=stored(4 * TB + ROUNDING))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["capacity_clamp"] == 4 * TB + ROUNDING
    assert result["notes"] == ["write reconciled after a lost connection"]
    assert len(writes(fake)) == 1


def test_lost_connection_waits_for_the_bounce_before_trusting_active(fake_api, run_module, fake_clock):
    """The PUT may have reached a node that has not yet left ACTIVE; reading back then would race the bounce."""
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB}, raises=LOST)
    fake.expect("GET", NODE_STATE, response=node("ACTIVE"))
    fake.expect("GET", NODE_STATE, response=node("ACTIVE"))
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, response=stored(4 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert result["notes"] == ["write reconciled after a lost connection"]
    assert fake_clock.now == pytest.approx(10)


def test_lost_connection_after_put_that_did_not_apply_fails_without_resending(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB}, raises=LOST)
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, response=stored(1 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB, "wait": False})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)
    assert "the connection was lost" in result["msg"]
    assert str(1 * TB) in result["msg"]
    assert len(writes(fake)) == 1


def test_lost_connection_whose_read_back_fails_reports_maybe_applied(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB}, raises=LOST)
    expect_bounce_and_ready(fake)
    fake.expect("GET", CLAMP, status=403, response={"error_class": "http_forbidden", "description": "test"})

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)
    assert "reading back failed" in result["msg"]
    assert len(writes(fake)) == 1


@pytest.mark.parametrize(("size", "expected"), [("4TiB", 4 * TIB), ("4 tb", 4 * TB), ("4000000000000", 4 * TB)])
def test_human_size_is_parsed(fake_api, run_module, size, expected):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": size}, check_mode=True)

    assert not result.get("failed"), result.get("msg")
    assert result["requested"] == expected


def test_bad_size_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": "4 bananas"})

    assert result["failed"] is True
    assert "is not a size" in result["msg"]
    assert fake.log == []


def test_check_mode_sends_no_put(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB}, check_mode=True, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["capacity_clamp"] == 1 * TB
    assert result["requested"] == 4 * TB
    assert result["diff"] == {"before": {"capacity_clamp": 1 * TB}, "after": {"capacity_clamp": 4 * TB}}
    assert [e.method for e in fake.log if not e.background] == ["GET"]


def test_without_wait_returns_after_the_put(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CLAMP, response=stored(1 * TB))
    fake.expect("PUT", CLAMP, body={"capacity_clamp": 4 * TB})

    result = run_module(capacity_clamp, {**CONNECTION, "capacity_clamp": 4 * TB, "wait": False})

    assert result["changed"] is True
    assert result["capacity_clamp"] == 4 * TB
    assert [e.path for e in fake.log if not e.background] == [CLAMP, CLAMP]
