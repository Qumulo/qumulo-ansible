#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: capacity_clamp
short_description: Set a Qumulo cluster's capacity clamp, writing only when it must change
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Manages the capacity clamp, which limits how much usable capacity the cluster provisions. On an edge it sets the
    size of the cache. A cluster has one clamp, so nothing selects it.
  - Every write of the clamp makes the cluster abandon and re-form quorum, even a write of the value already stored,
    so clients pause for the time that takes. The module writes only when the clamp has to change, and then waits
    until the cluster is ACTIVE and serves its file system again (G-CLAMP-BOUNCE).
  - The cluster rounds the clamp up to a whole provisioning unit, whose size is not published (QUIRK-012). A stored
    clamp at or above O(capacity_clamp), by no more than O(rounding_tolerance), is the request already applied and is
    not a change (G-CLAMP-TOL).
  - Lowering the clamp never shrinks the capacity already provisioned (G-CLAMP-SHRINK). A stored clamp more than
    O(rounding_tolerance) above the request is a decrease, which fails unless O(allow_decrease=true).
  - An unset clamp limits nothing. A first clamp is a write unless it is more than O(rounding_tolerance) below the
    provisioned capacity, read as the file system's size, which makes it a decrease; that size is also the base of the
    5 PB step below.
  - One write can raise the clamp by at most 5 PB (decimal), 5 x 10^15 bytes. A larger increase fails before any
    write.
  - The module does not check the license's usable capacity cap or the size an unlicensed edge is formed with
    (G-EDGE-FLOOR).
options:
  capacity_clamp:
    description:
      - The clamp to set, in bytes, as an integer or as a size with a unit.
      - Decimal units are C(KB) to C(EB), where C(1TB) is 10^12 bytes; binary units are C(KiB) to C(EiB), where
        C(1TiB) is 2^40 bytes. Units are matched without regard to case.
    type: raw
    required: true
  rounding_tolerance:
    description:
      - How far above O(capacity_clamp) the stored clamp may be and still count as O(capacity_clamp) rounded up to a
        provisioning unit, in bytes or as a size with a unit.
      - A stored clamp further above the request than this is a decrease, which needs O(allow_decrease).
      - Defaults to the larger of 1% of O(capacity_clamp) and 8 GiB (QUIRK-012).
    type: raw
  allow_decrease:
    description:
      - Allow a write that lowers the clamp.
      - The provisioned capacity stays as it is; a lower clamp only stops the cluster from provisioning more.
    type: bool
    default: false
  wait:
    description:
      - After a write, wait until the cluster is ACTIVE, a login succeeds and the root directory's attributes can be read.
      - With V(false) the module returns after the write while the cluster re-forms quorum, and RV(capacity_clamp) is
        the requested clamp, not read back. A write whose connection was lost is still waited for, because only
        reading the clamp back can tell whether it was applied.
    type: bool
    default: true
  wait_timeout:
    description: Seconds to wait after a write, including while the cluster's REST API does not answer.
    type: int
    default: 600
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - Writes only when the clamp has to change, but each write re-forms quorum, and the cluster stores the clamp
        rounded up to a provisioning unit whose size is not published (G-CLAMP-BOUNCE, G-CLAMP-TOL, QUIRK-012).
notes:
  - "API: GET and PUT /v1/capacity/clamp; GET /v1/file-system when no clamp is set; after a write, GET /v1/node/state (no login)
    and GET /v1/files/%2F/info/attributes."
  - "Registers: G-CLAMP-BOUNCE, G-CLAMP-TOL, G-CLAMP-SHRINK, G-EDGE-FLOOR, QUIRK-012."
  - The PUT body carries the clamp as a JSON integer, as Qumulo's Python SDK sends it, although the OpenAPI document
    types it as a string.
  - If the connection is lost after the write is sent, the module never sends it again. It waits for the cluster and
    reads the clamp back, and reports the write as applied only if the stored clamp is at or above the request.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Set a first capacity clamp on a cluster that has none
  # An unset clamp limits nothing, so a first clamp at or above the file system's size is always written. Each write
  # makes the cluster re-form quorum, and clients pause meanwhile (G-CLAMP-BOUNCE).
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 4TB

- name: Grow the edge's cache to 6 TB in the maintenance window
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 6TB

- name: Preview whether growing the cache to 6 TB would write the clamp
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 6TB
  check_mode: true
  diff: true
  register: clamp_preview

- name: Show the stored and requested clamp
  ansible.builtin.debug:
    msg: >-
      stored {{ clamp_preview.capacity_clamp }}, requested {{ clamp_preview.requested }};
      {{ 'a write, which re-forms quorum' if clamp_preview.changed else 'no write' }}

- name: Raise the clamp to 8 PB in two steps
  # One write raises the clamp by at most 5 PB (decimal), measured from the stored clamp, or from the file system's
  # size when none is set. Each step re-forms quorum.
  qumulo.cluster.capacity_clamp:
    capacity_clamp: "{{ item }}"
  loop: [5PB, 8PB]

- name: Start the clamp write and return before quorum has re-formed
  # The returned capacity_clamp is then the requested value, not read back.
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 6TB
    wait: false

- name: Accept a stored clamp up to 20 GB above the request as already applied
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 4TB
    rounding_tolerance: 20GB

- name: Lower the clamp, knowing that the provisioned capacity stays as it is
  # A lower clamp never shrinks the cache (G-CLAMP-SHRINK). To make the cache smaller, rebuild the edge, or keep
  # more of it free with qumulo.cluster.portal_eviction_settings; the edge_cache_resize role chooses between them.
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 2TiB
    allow_decrease: true
"""

RETURN = r"""
capacity_clamp:
  description:
    - The stored clamp in bytes after the task, or V(null) when none is set.
    - In check mode, and when nothing changed, the clamp as stored before the task. It may be slightly above
      RV(requested), because the cluster rounds the clamp up to a whole provisioning unit.
    - With O(wait=false) and a write, the requested clamp, not read back.
  returned: always
  type: int
  sample: 4000159744000
requested:
  description: O(capacity_clamp) in bytes.
  returned: always
  type: int
  sample: 4000000000000
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: True when the connection was lost after the write was sent, and reading the clamp back did not show it applied.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.capacity import Clamp
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "capacity_clamp": {"type": "raw", "required": True},
    "rounding_tolerance": {"type": "raw"},
    "allow_decrease": {"type": "bool", "default": False},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 600},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), Clamp)


if __name__ == "__main__":
    main()
