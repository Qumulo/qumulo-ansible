# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.item_areas, which plugins/filter/item_areas.yml documents."""

from __future__ import annotations

from collections.abc import Mapping


def item_areas(matchers, **variables):
    """Each (area, key) that matchers find declared in variables, as the ownership check's pairs.

    matchers maps an area to names such as 'qumulo_tenants[].networks'; variables maps a variable's name to its value,
    None when unset. A key counts when an item has it with any value but None, [] included, as a declared variable
    counts in the area map. Each pair's variable names the item by its id, else its name, share name or export path, else
    its index, as in 'qumulo_tenants[finance].networks[2].floating_ip_ranges'.
    """
    pairs = []
    for area, names in matchers.items():
        for name in names:
            for variable in _declared(variables, "", name.split(".")):
                pair = {"area": area, "variable": variable}
                if pair not in pairs:
                    pairs.append(pair)
    return pairs


def _declared(value, label, segments):
    """The labels of the keys that segments name in value, a dict; a segment's [] stands for each item of its list."""
    if not segments:
        yield label
        return
    segment, rest = segments[0], segments[1:]
    key = segment.removesuffix("[]")
    if not isinstance(value, Mapping) or value.get(key) is None:
        return
    child = value[key]
    label = f"{label}.{key}" if label else key
    if key == segment:
        yield from _declared(child, label, rest)
    elif isinstance(child, list):
        for index, item in enumerate(child):
            yield from _declared(item, f"{label}[{_item_label(item, index)}]", rest)


def _item_label(item, index):
    for key in ("id", "name", "share_name", "export_path"):
        if isinstance(item, Mapping) and item.get(key) is not None:
            return item[key]
    return index


class FilterModule:
    def filters(self):
        return {"item_areas": item_areas}
