#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: edge_cache_route
short_description: Choose how to bring a Qumulo edge's cache to a target size
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Compares a target capacity clamp with an edge's file system size, its stored clamp and its license ceiling, and
    returns the route the C(qumulo.cluster.edge_cache_resize) role takes. The inputs are options, read with
    M(qumulo.cluster.cluster_info).
  - A pure computation from its options. It sends no request, takes no connection options, and is not in
    the C(qumulo.cluster.qumulo) action group, so C(module_defaults) for that group do not reach it.
  - The file system's size stands in for the provisioned capacity, which no API reports, so the comparison is
    approximate (G-CLAMP-TOL).
  - V(within) when the file system is within O(margin) of the target.
  - V(below_floor) when the file system is more than O(margin) above the target and the target is below O(floor)
    (G-EDGE-FLOOR). The reason suggests a C(free_threshold) for M(qumulo.cluster.portal_eviction_settings).
  - V(rebuild) when the file system is more than O(margin) above the target otherwise (G-CLAMP-SHRINK, G-UUID, G-LICENSE).
  - V(blocked_license) when the file system is more than O(margin) below the target and the license ceiling is below the
    target (G-LICENSE). The ceiling is O(license_cap); on an unprotected edge without one, it is 1 TB (G-EDGE-FLOOR).
  - V(platform_limited) when the file system is more than O(margin) below the target but O(stored) already allows it.
    Either the platform's disks cannot provision more, or a raised license cap has not applied yet; the module cannot
    tell which (G-LICENSE).
  - V(grow) otherwise; raising the clamp then re-forms quorum once (G-CLAMP-BOUNCE).
options:
  target:
    description: The target clamp, in bytes or as a size with a unit such as C(4TB) (10^12 bytes) or C(4TiB) (2^40 bytes).
    type: raw
    required: true
  stored:
    description: The stored capacity clamp in bytes, or V(null) when none is set; an unset clamp limits nothing.
    type: int
  license_cap:
    description: The license's usable capacity cap in bytes, or V(null) when there is none.
    type: int
  total:
    description: The file system's C(total_size_bytes).
    type: int
    required: true
  unprotected_edge:
    description:
      - Whether the cluster is an unprotected edge, as M(qumulo.cluster.cluster_info) reports it in C(product.unprotected_edge).
      - Required. An unprotected edge without a license cap is held to 1 TB.
    type: bool
    required: true
  margin:
    description:
      - How close the file system must be to the target to count as that size, in bytes or as a size with a unit.
      - Defaults to 10% of the target.
    type: raw
  floor:
    description:
      - The capacity a rebuilt edge provisions at formation, if its platform is larger (G-EDGE-FLOOR). A target below it
        routes to V(below_floor) rather than V(rebuild).
      - It does not move the 1 TB ceiling of an unlicensed edge.
    type: int
    default: 1000000000000
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - "API: none."
  - "Registers: G-CLAMP-BOUNCE, G-CLAMP-TOL, G-CLAMP-SHRINK, G-EDGE-FLOOR, G-LICENSE, G-UUID."
"""

EXAMPLES = r"""
# The module runs on the controller and takes no connection options. Only the cluster_info read and the clamp write
# connect: set their options once for the play with module_defaults for group/qumulo.cluster.qumulo, or in the
# QUMULO_* environment variables.
- name: Read the edge's clamp, license, size and product type
  qumulo.cluster.cluster_info:
    gather_subset: [capacity, license, filesystem, product]
  register: info

- name: Choose the route to a 4 TB cache
  qumulo.cluster.edge_cache_route:
    target: 4TB
    stored: "{{ info.cluster.capacity_clamp }}"
    license_cap: "{{ info.cluster.license.usable_capacity_cap }}"
    total: "{{ info.cluster.filesystem.total_size_bytes }}"
    unprotected_edge: "{{ info.cluster.product.unprotected_edge }}"
  register: plan

- name: Grow the cache only when the route is a plain clamp write
  # Uses the route registered by the task above. Any other route needs a decision: a rebuild, a license, or a free threshold.
  qumulo.cluster.capacity_clamp:
    capacity_clamp: 4TB
  when: plan.route == 'grow'

- name: Ask whether a 500 GB cache on a 2 TB licensed edge needs a rebuild
  # The inputs are plain numbers, so this answers a what-if without reading a cluster.
  qumulo.cluster.edge_cache_route:
    target: 500GB
    stored: 2000000000000
    total: 2000000000000
    license_cap: 2000000000000
    unprotected_edge: true
  register: what_if

- name: Treat a file system within 80 GB of the target as that size
  qumulo.cluster.edge_cache_route:
    target: 4TB
    stored: 4000000000000
    total: 3950000000000
    unprotected_edge: false
    margin: 80GB
  register: tight_plan
"""

RETURN = r"""
route:
  description: V(within), V(grow), V(platform_limited), V(blocked_license), V(below_floor) or V(rebuild).
  returned: success
  type: str
  sample: grow
reason:
  description: Why this route, in one sentence.
  returned: success
  type: str
  sample: the target is more than 400000000000 bytes above the file system (1000000000000 bytes), and the clamp (1000000000000 bytes) is below it
target:
  description: The target in bytes.
  returned: success
  type: int
  sample: 4000000000000
stored:
  description: The stored clamp in bytes, or V(null).
  returned: success
  type: int
  sample: 1000000000000
license_cap:
  description: O(license_cap), or V(null).
  returned: success
  type: int
  sample: 4000000000000
ceiling:
  description: The most the license allows in bytes, or V(null) when nothing caps it.
  returned: success
  type: int
  sample: 4000000000000
total:
  description: The file system's size in bytes.
  returned: success
  type: int
  sample: 1000000000000
margin:
  description: The margin in bytes.
  returned: success
  type: int
  sample: 400000000000
floor:
  description: The floor in bytes.
  returned: success
  type: int
  sample: 1000000000000
rounding_tolerance:
  description:
    - How far the stored clamp may sit from the target once written, in bytes, the larger of 1% of the target and
      8 GiB, as M(qumulo.cluster.capacity_clamp) takes it by default (QUIRK-012).
  returned: success
  type: int
  sample: 40000000000
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.capacity import EDGE_FLOOR, edge_cache_route
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError

ARGUMENT_SPEC = {
    "target": {"type": "raw", "required": True},
    "stored": {"type": "int"},
    "license_cap": {"type": "int"},
    "total": {"type": "int", "required": True},
    "unprotected_edge": {"type": "bool", "required": True},
    "margin": {"type": "raw"},
    "floor": {"type": "int", "default": EDGE_FLOOR},
}


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        plan = edge_cache_route(**module.params)
    except QumuloError as e:
        module.fail_json(msg=str(e))
    module.exit_json(changed=False, **plan)


if __name__ == "__main__":
    main()
