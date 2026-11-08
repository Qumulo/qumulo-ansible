# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.edge_cache_route: which way the edge_cache_resize role takes an edge's cache to its target."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import edge_cache_route

TB, GB = 10**12, 10**9
M = 400 * GB  # the default margin of a 4 TB target


def route(run_module, target, total, stored=None, license_cap=None, unprotected_edge=False, **extra):
    args = {"target": target, "stored": stored, "license_cap": license_cap, "total": total, "unprotected_edge": unprotected_edge}
    return run_module(edge_cache_route, {**args, **extra})


@pytest.mark.parametrize(
    ("target", "stored", "license_cap", "total", "expected", "reason"),
    [
        # within: the file system is within the margin (10% of the target) of it, whatever the clamp.
        (4 * TB, 4 * TB, None, 4 * TB, "within", "within 400000000000 bytes of the target"),
        (4 * TB, None, None, 4 * TB, "within", "within"),
        (4 * TB, 1 * TB, None, 4 * TB, "within", "within"),
        # The margin's ends are inside it; one byte past either end is outside.
        (4 * TB, None, None, 4 * TB + M, "within", "within"),
        (4 * TB, None, None, 4 * TB - M, "within", "within"),
        (4 * TB, None, None, 4 * TB + M + 1, "rebuild", "G-CLAMP-SHRINK"),
        (4 * TB, None, None, 4 * TB - M - 1, "grow", "above the file system"),
        # grow: the file system is short of the target and the clamp is below it.
        (4 * TB, 1 * TB, None, 1 * TB, "grow", "the clamp (1000000000000 bytes) is below it"),
        (4 * TB, None, 4 * TB, 1 * TB, "grow", "the clamp (unset) is below it"),
        # platform_limited: the clamp already allows the target, so writing it would change nothing.
        (4 * TB, 4 * TB, None, 1 * TB, "platform_limited", "a clamp that already allows the target is not written"),
        (4 * TB, 4 * TB + 4 * TB // 20, None, 1 * TB, "platform_limited", "cannot be resized or added after formation, so a larger edge is a rebuild"),
        # blocked_license: the license caps usable capacity below the target.
        (4 * TB, 1 * TB, 2 * TB, 1 * TB, "blocked_license", "the license caps usable capacity at 2000000000000 bytes"),
        (4 * TB, 8 * TB, 2 * TB, 1 * TB, "blocked_license", "G-LICENSE"),
        # below_floor: well below the file system, and below the 1 TB a rebuilt edge provisions again.
        (500 * GB, None, None, 900 * GB, "below_floor", "a free_threshold of about 0.44"),
        (500 * GB, 4 * TB, None, 4 * TB, "below_floor", "G-EDGE-FLOOR"),
        # rebuild: well below the file system otherwise; the new edge needs a new license.
        (2 * TB, 4 * TB, None, 4 * TB, "rebuild", "provisioned capacity never shrinks (G-CLAMP-SHRINK)"),
        (1 * TB, 4 * TB, 8 * TB, 4 * TB, "rebuild", "new UUID, which needs a new license from Nexus (G-UUID, G-LICENSE)"),
    ],
)
def test_route_and_its_reason(run_module, target, stored, license_cap, total, expected, reason):
    result = route(run_module, target, total, stored=stored, license_cap=license_cap)

    assert not result.get("failed"), result.get("msg")
    assert result["route"] == expected, result["reason"]
    assert reason in result["reason"]
    assert result["changed"] is False
    assert (result["target"], result["stored"], result["license_cap"], result["total"]) == (target, stored, license_cap, total)
    assert (result["margin"], result["floor"]) == (target // 10, TB)


@pytest.mark.parametrize(
    ("target", "total", "expected", "reason"),
    [
        (4 * TB, 500 * GB, "blocked_license", "an unlicensed edge is limited to 1000000000000 bytes"),
        (1 * TB, 500 * GB, "grow", "the clamp (unset) is below it"),
        (500 * GB, 500 * GB, "within", "within 50000000000 bytes of the target"),
    ],
)
def test_unlicensed_unprotected_edge_is_held_to_1_tb(run_module, target, total, expected, reason):
    result = route(run_module, target, total, unprotected_edge=True)

    assert result["route"] == expected, result["reason"]
    assert reason in result["reason"]
    assert result["ceiling"] == TB


def test_custom_floor_does_not_move_the_unlicensed_ceiling(run_module):
    result = route(run_module, 1500 * GB, 500 * GB, unprotected_edge=True, floor=2 * TB)

    assert (result["route"], result["ceiling"]) == ("blocked_license", TB)


def test_license_cap_overrides_the_unlicensed_ceiling(run_module):
    result = route(run_module, 4 * TB, 500 * GB, license_cap=8 * TB, unprotected_edge=True)

    assert (result["route"], result["ceiling"]) == ("grow", 8 * TB)


def test_protected_cluster_without_a_license_cap_has_no_ceiling(run_module):
    result = route(run_module, 4 * TB, 500 * GB)

    assert (result["route"], result["ceiling"]) == ("grow", None)


def test_sizes_are_parsed_and_margin_and_floor_can_be_set(run_module):
    result = route(run_module, "4TB", 4 * TB - 150 * GB, stored=1 * TB, margin="100GB", floor=2 * TB)

    assert (result["target"], result["margin"], result["floor"]) == (4 * TB, 100 * GB, 2 * TB)
    assert result["route"] == "grow"  # 150 GB short of the target is outside a 100 GB margin


@pytest.mark.parametrize(("floor", "expected"), [(2 * TB, "below_floor"), (TB, "rebuild")])
def test_floor_decides_between_below_floor_and_rebuild(run_module, floor, expected):
    assert route(run_module, 1500 * GB, 4 * TB, stored=4 * TB, floor=floor)["route"] == expected


def test_unprotected_edge_is_required(run_module):
    result = run_module(edge_cache_route, {"target": 4 * TB, "total": TB})

    assert result["failed"] is True
    assert "unprotected_edge" in result["msg"]


def test_bad_target_fails(run_module):
    result = route(run_module, "4 bananas", TB)

    assert result["failed"] is True
    assert "is not a size" in result["msg"]


def test_sends_no_request(fake_api, run_module):
    fake = fake_api()

    result = route(run_module, 4 * TB, TB)

    assert result["route"] == "grow"
    assert fake.log == []


GIB = 2**30


@pytest.mark.parametrize(
    ("target", "tolerance"),
    [(4 * TB, 40 * GB), (500 * GB, 8 * GIB), (990_099_009_900, 9_900_990_099), ("950GB", 9_500_000_000), ("931GiB", 9_996_536_381)],
)
def test_rounding_tolerance_is_the_larger_of_1_percent_and_8_gib(run_module, target, tolerance):
    """capacity_clamp's default tolerance, which edge_rebuild's floor refusal reads from here."""
    assert route(run_module, target, TB)["rounding_tolerance"] == tolerance


@pytest.mark.parametrize(
    ("clamp", "refused"),
    [
        ("950GB", True),
        ("900GiB", True),
        (990_099_009_900, True),
        (990_099_009_901, False),
        ("995GB", False),
        ("1TB", False),
        ("931GiB", False),
        ("950GiB", False),
    ],
)
def test_a_clamp_more_than_the_tolerance_below_the_floor_bound(run_module, clamp, refused):
    """roles/edge_rebuild/tasks/preconditions.yml reads target, floor and rounding_tolerance of a call with total 0."""
    result = route(run_module, clamp, 0, unprotected_edge=True)

    assert (min(TB, result["floor"]) - result["target"] > result["rounding_tolerance"]) is refused


def test_a_clamp_with_a_unit_parse_size_does_not_know_fails_with_its_text(run_module):
    result = route(run_module, "1T", 0, unprotected_edge=True)

    assert result["failed"] is True
    assert result["msg"].startswith("'1T' is not a size; write bytes, or a number with a unit such as 4TB")


def test_a_floor_that_is_not_an_int_fails_the_argument_check(run_module):
    result = route(run_module, "1TB", 0, unprotected_edge=True, floor="1TB")

    assert result["failed"] is True
    assert "floor" in result["msg"]
