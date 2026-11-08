# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Conversions from the forms users write in inventory to the forms the REST API takes."""

from __future__ import annotations

import re
from decimal import Decimal

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError

# Matched without regard to case; a bare number is bytes.
SIZE_UNITS = {
    "": 1,
    "b": 1,
    **{f"{prefix}b": 1000**power for power, prefix in enumerate("kmgtpe", start=1)},
    **{f"{prefix}ib": 1024**power for power, prefix in enumerate("kmgtpe", start=1)},
}
SIZE = re.compile(r"^(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>[a-z]*)$")
# The days a schedule or a blackout window names, in the order the server lists them.
WEEK = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")


def parse_size(value):
    """Bytes as an int, from an int or a string such as "4TB" (10**12 bytes each) or "4TiB" (2**40 bytes each)."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise QumuloError(f"{value!r} is negative; a size is a number of bytes")
        return value
    match = SIZE.match(value.strip().lower()) if isinstance(value, str) else None
    if not match or match["unit"] not in SIZE_UNITS:
        raise QumuloError(f"{value!r} is not a size; write bytes, or a number with a unit such as 4TB (10**12 bytes) or 4TiB (2**40 bytes)")
    size = Decimal(match["number"]) * SIZE_UNITS[match["unit"]]
    if size != size.to_integral_value():
        raise QumuloError(f"{value!r} is not a whole number of bytes")
    return int(size)


def canonical_days(days):
    """The days in WEEK's order without repeats, with EVERY_DAY written as the seven days the server reads it back as."""
    found = set(WEEK) if "EVERY_DAY" in days else set(days)
    return [day for day in WEEK if day in found]
