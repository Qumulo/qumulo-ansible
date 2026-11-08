<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->
# edge_cache_resize

Brings a Qumulo edge's cache to a target size by the least disruptive route. The role reads the edge's file system size, its stored capacity clamp, its license ceiling and its product type, asks `qumulo.cluster.edge_cache_route` for the route, and then either raises the clamp (one quorum re-formation), applies the declared eviction threshold, or fails on purpose with the hand-off that a license or a rebuild needs.

## Routes

| Route | When | What the role does |
|---|---|---|
| `within` | the file system is within the margin of the target | applies the eviction threshold, if declared |
| `grow` | the target is above the file system, below the stored clamp's reach and allowed by the license | raises the clamp with `qumulo.cluster.capacity_clamp`, waits out the quorum bounce, reads the size again |
| `platform_limited` | the file system stays below the target although the clamp allows it: before any write, when the stored clamp already does, or after a `grow` | writes nothing; reports the platform's disks, which cannot grow in place or take another disk once the edge is formed, so a bigger edge is a rebuild with a larger thin disk, which the role cannot tell from the size it reads and so does not route to; or a license cap that applies at the next quorum formation |
| `blocked_license` | the license ceiling is below the target | fails naming the license this edge's UUID needs from Nexus |
| `below_floor` | the target is below the size a rebuilt edge provisions | fails unless `qumulo_capacity.free_threshold` is declared, and suggests a value |
| `rebuild` | the target is below the file system, since provisioned capacity never shrinks | fails with the plan to rebuild the edge (new UUID, which needs a new license from Nexus); with `edge_cache_resize_allow_rebuild` and `edge_rebuild_reinstall_tasks`, rebuilds it with the `edge_rebuild` role |

## Variables

`meta/argument_specs.yml` is the reference; the main ones:

| Variable | Default | Meaning |
|---|---|---|
| `qumulo_capacity.clamp` | required | the target, in bytes or as a size such as `4TB` or `4TiB` |
| `qumulo_capacity.license` | `pending` | read by the baseline's license step, not by this role; `none` is for an edge that runs without a license, whose `blocked_license` failure then says to set `pending` or `required` before supplying the document |
| `qumulo_capacity.free_threshold` | unset | the fraction of capacity kept free by eviction; applied on every route that does not fail |
| `edge_cache_resize_margin_bytes` | 10% of the target | how close the file system must be to the target to count as that size |
| `edge_cache_resize_rounding_tolerance` | `capacity_clamp`'s default, the larger of 1% of the target and 8 GiB | how far above the target a stored clamp may be and still count as the target |
| `edge_cache_resize_floor_bytes` | `1000000000000` | the capacity a rebuilt edge provisions at formation; the `edge_rebuild` role refuses, on every rebuilt edge, a declared clamp more than its rounding tolerance below min(1 TB, this), in either of its two forms (for `license: none`, and for any other), before the drain in one run and before the formation in the replay phase of two. Lower it only to describe a platform smaller than 1 TB |
| `edge_cache_resize_plan_only` | true under `--check` | report the route and its inputs, change nothing |

On the `rebuild` route the role runs the `edge_rebuild` role with phase `all`, which needs that role's own variables, `-e qumulo_rebuild=true` among them. A rebuild that fails, from a broken reinstall tasks file or a hub that refuses the pairing, would fail the same way on every edge after draining and destroying it, so a failed rebuild must end the run. In a play that runs several edges at once, `any_errors_fatal` on the rebuild does that. With `serial: 1`, any failed edge ends the play for the edges after it, whatever `max_fail_percentage` says, the hand-off routes' failures included; `playbooks/edge_cache_resize.yml` therefore runs each edge in a block whose rescue ends only that edge, fails again when `edge_cache_resize_rebuilding` is true, and fails in its last play, naming each edge that failed. `serial: 1` only keeps the rebuilds from running at the same time.

The role sets the facts `edge_cache_resize_route`, `edge_cache_resize_plan` (the module's result: route, reason and the sizes it compared) and `edge_cache_resize_rebuilding`, true once the `rebuild` route has started the rebuild.

## Example

```yaml
- name: Resize the cache of each edge
  hosts: qumulo_edges
  serial: 1
  module_defaults:
    group/qumulo.cluster.qumulo:
      api_host: "{{ qumulo_api_host }}"
      api_password: "{{ qumulo_api_password }}"
      expected_cluster_uuid: "{{ qumulo_cluster_uuid }}"
  pre_tasks:
    - name: Check that the edge is the expected cluster, and wait until it is ready
      qumulo.cluster.cluster_guard:
  roles:
    - role: qumulo.cluster.edge_cache_resize
      vars:
        qumulo_capacity:
          clamp: 4TB
          free_threshold: 0.2
```

Run it with `--check` first: the role then reports each edge's route and inputs without changing anything. This play stops at the first edge that fails; `playbooks/edge_cache_resize.yml` runs the same role and goes on to the next edge, as described above.

## Requirements

Qumulo Core 7.10.3 or later and ansible-core 2.20 or later. The license, rebuild and floor behaviours it works around are recorded in `docs/reference/gaps.md` (G-LICENSE, G-UUID, G-CLAMP-SHRINK, G-EDGE-FLOOR, G-CLAMP-TOL) and `docs/reference/quirks.md` (QUIRK-012).
