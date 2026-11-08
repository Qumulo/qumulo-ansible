# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Every module that writes configuration is read back by capture_info, or says why not.

Adoption captures everything the collection can configure, so a new module that writes something lands with the
capture area that reads it back, in plugins/module_utils/capture.py's CAPTURED, or in its NOT_CAPTURED with the reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils.capture import AREAS, CAPTURED, NOT_CAPTURED, PARTS
from ansible_collections.qumulo.cluster.tests.unit.test_collection_contract import MODULES, documentation

ROOT = Path(__file__).resolve().parents[2]
INFO_FRAGMENT = "qumulo.cluster.attributes.info_module"
# Modules that write configuration: every module that is not documented as an info module.
WRITERS = [name for name in MODULES if INFO_FRAGMENT not in documentation(name).get("extends_documentation_fragment", [])]
# The captured modules test_capture_info.py does not run in check mode from what capture read, and why.
NOT_RUN_BACK = {
    "ssl_certificate": "the cluster returns neither the certificate nor its key, so capture holds nothing to run it with",
    "kerberos_keytab": "capture never holds the keytab, only a supply line that names its principals",
}
CAPTURE_TESTS = ROOT / "tests" / "unit" / "plugins" / "modules" / "test_capture_info.py"


@pytest.mark.parametrize("name", WRITERS)
def test_each_module_that_writes_is_captured_or_says_why_not(name):
    assert (name in CAPTURED) != (name in NOT_CAPTURED), (
        f"{name} writes configuration: add it to CAPTURED in plugins/module_utils/capture.py with the area that reads it back, "
        "or to NOT_CAPTURED with the reason"
    )


def test_the_lists_name_only_modules_that_write():
    named = set(CAPTURED) | set(NOT_CAPTURED)
    assert named <= set(WRITERS), f"{sorted(named - set(WRITERS))} are not modules that write configuration"


def test_each_module_is_read_back_by_an_area_capture_has():
    assert set(CAPTURED.values()) <= set(AREAS)


def test_each_reason_says_why():
    assert all(reason.strip() for reason in NOT_CAPTURED.values())


def test_capture_areas_are_the_baseline_roles_areas_and_replication():
    role_areas = yaml.safe_load((ROOT / "roles" / "cluster_baseline" / "vars" / "main.yml").read_text(encoding="utf-8"))["cluster_baseline_area_variables"]
    # A dotted area is a part of an area, which capture reads with its area and leave_out can name on its own.
    parts = {area for area in role_areas if "." in area}
    assert parts <= set(PARTS)
    assert set(AREAS) == {*(set(role_areas) - parts), "replication"}


@pytest.mark.parametrize("name", sorted(CAPTURED))
def test_each_captured_module_is_run_back_in_check_mode_from_the_capture(name):
    run_back = set(re.findall(r"unchanged\(\s*run_module,\s*(\w+)", CAPTURE_TESTS.read_text(encoding="utf-8")))
    assert (name in run_back) != (name in NOT_RUN_BACK), (
        f"{name}: run it in check mode from the captured values in test_capture_info.py with unchanged(), or say in NOT_RUN_BACK why it cannot be"
    )
