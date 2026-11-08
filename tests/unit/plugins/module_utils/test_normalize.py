# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""parse_size: the human sizes inventory may hold, in decimal and binary units; canonical_days: the day sets schedules and blackout windows name."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.normalize import canonical_days, parse_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1B", 1),
        ("1KB", 10**3),
        ("1MB", 10**6),
        ("1GB", 10**9),
        ("4TB", 4 * 10**12),
        ("1PB", 10**15),
        ("1EB", 10**18),
    ],
)
def test_decimal_units(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1KiB", 2**10),
        ("1MiB", 2**20),
        ("1GiB", 2**30),
        ("4TiB", 4 * 2**40),
        ("1PiB", 2**50),
        ("1EiB", 2**60),
    ],
)
def test_binary_units(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (4000000000000, 4 * 10**12),
        ("4000000000000", 4 * 10**12),
        (" 4 TB ", 4 * 10**12),
        ("4tb", 4 * 10**12),
        ("4tib", 4 * 2**40),
        ("1.5TB", 1500 * 10**9),
        ("0.5KiB", 512),
    ],
)
def test_plain_bytes_spacing_case_and_fractions(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "not a size"),
        ("TB", "not a size"),
        ("4XB", "not a size"),
        ("4T", "not a size"),
        ("-1TB", "not a size"),
        (-1, "negative"),
        ("1.5B", "not a whole number of bytes"),
        ("0.0001KB", "not a whole number of bytes"),
        (True, "not a size"),
        (1.5, "not a size"),
        (None, "not a size"),
    ],
)
def test_bad_sizes_are_refused_with_the_value(value, reason):
    with pytest.raises(QumuloError, match=reason) as raised:
        parse_size(value)
    assert repr(value) in str(raised.value)


@pytest.mark.parametrize(
    ("days", "canonical"),
    [
        (["EVERY_DAY"], ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]),
        (["MON", "EVERY_DAY"], ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]),
        (["FRI", "MON", "WED"], ["MON", "WED", "FRI"]),
        (["SAT", "SUN", "SAT"], ["SUN", "SAT"]),
        ([], []),
    ],
)
def test_days_read_back_in_the_server_s_order_with_every_day_expanded(days, canonical):
    assert canonical_days(days) == canonical
