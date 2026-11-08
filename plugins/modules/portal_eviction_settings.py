#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: portal_eviction_settings
short_description: Manage the free-capacity threshold at which a cluster evicts cached spoke data
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Manages the cluster's eviction setting, the fraction of the file system's capacity that the cluster keeps free by
    evicting data it caches for its spoke portals. A cluster has one such setting, so nothing selects it.
  - The setting exists on every cluster and takes effect on clusters that host spoke portals.
options:
  free_threshold:
    description:
      - The fraction of capacity to keep free, from V(0.0) to V(1.0).
      - A value outside that range fails before any request is sent.
      - Values that differ by less than one millionth are treated as equal, so a value the server stores with a
        different float rounding is not reported as a change.
      - A newly formed cluster stores V(0.05), not the V(0.15) that Qumulo's API documentation gives (QUIRK-009).
    type: float
    required: true
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
    support: full
notes:
  - "API: GET and PATCH /v1/portal/spokes/eviction-settings, with If-Match."
  - Like the portal hub and spoke routes, the eviction-settings route may return no ETag (G-PORTAL-ETAG); the PATCH is
    then sent without If-Match, and the last writer wins.
  - "Registers: QUIRK-009, G-PORTAL-ETAG."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Keep 15% of the edge's capacity free
  qumulo.cluster.portal_eviction_settings:
    free_threshold: 0.15

- name: Make the edge's cache smaller without a rebuild, by keeping a quarter of its capacity free
  # Evicting more needs neither a change to the capacity clamp (qumulo.cluster.capacity_clamp) nor a rebuild.
  qumulo.cluster.portal_eviction_settings:
    free_threshold: 0.25

- name: Preview the edge's eviction threshold from the inventory
  qumulo.cluster.portal_eviction_settings:
    free_threshold: "{{ qumulo_capacity.free_threshold }}"
  check_mode: true
  diff: true
  register: eviction_preview

- name: Show the threshold before and after
  ansible.builtin.debug:
    msg: >-
      {{ eviction_preview.diff.before.free_threshold }} -> {{ eviction_preview.eviction_settings.free_threshold }}
  when: eviction_preview.changed

- name: Set the threshold back to what a newly formed cluster stores
  qumulo.cluster.portal_eviction_settings:
    free_threshold: 0.05

- name: Read the eviction threshold that is in force
  qumulo.cluster.portal_info:
    gather_subset: [eviction]
  register: eviction_now
"""

RETURN = r"""
eviction_settings:
  description: The settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    free_threshold:
      description: The fraction of capacity kept free.
      type: float
  sample: {free_threshold: 0.15}
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the settings back did not confirm
    it. The task fails with C(changed) true.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the options that did not converge.
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {free_threshold: 0.25}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {free_threshold: 0.05}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.portal import EvictionSettings

ARGUMENT_SPEC = {
    "free_threshold": {"type": "float", "required": True},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), EvictionSettings)


if __name__ == "__main__":
    main()
