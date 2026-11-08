#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: upgrade_plan
short_description: Plan a fleet's Qumulo Core upgrade, hop by hop, before anything is sent
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Turns every inventory host's reads and upgrade variables into the run's plan, which the
    C(qumulo.cluster.cluster_upgrade) role follows. The plan is a list of hops. A hop takes the hosts it moves to one
    release; each host takes its own hops to its own target, each the furthest entry of its images list that Qumulo
    Core's quarterly rule accepts (G-UPGRADE-PATH). A host whose next hop is to another release does not move in the hop,
    and the plan notes that.
  - A pure computation. It sends no request, takes no connection options, and is not in the C(qumulo.cluster.qumulo)
    action group, so C(module_defaults) for that group do not reach it.
  - Within a hop the commits are cut into units, a portal component's moving members in waves, its spokes before their
    hub, or one cluster alone. The units are ordered by C(qumulo_upgrade_after) and by replication, a target before its
    source.
  - Any refusal stops the whole plan. The module then fails with every refusal's message, and still returns RV(plan). The
    refusals come from the inventory and the reads alone, such as a limited release without the waiver
    (G-UPGRADE-LETTER), a reboot set size above the configured tolerance (G-UPGRADE-COUNT), or a commit record a commit
    that did not finish left (G-UPGRADE-RECORD).
  - Each warning, such as an unlicensed edge's (G-UPGRADE-LICENSE), is also printed, and none stops the run.
  - Each host's images reach the module through the filter C(qumulo.cluster.redact_images), so no field of the result
    holds a URL's query string or user information. The module has no C(no_log) option, so host names, releases and
    numbers read in full.
  - With O(window), the module answers only whether a hop's commits may begin now, by the hop's maintenance windows.
options:
  hosts:
    description:
      - Every inventory host, not only the play's, each a mapping.
      - C(name), the inventory host; C(reached), whether the play reaches it, and C(in_play), whether it is still in the
        play after its reads, both true when unset.
      - From its reads, when the play read it, C(cluster_uuid), the C(cluster_id) of M(qumulo.cluster.cluster_info)'s
        C(node) subset; C(version) and C(build), its C(version) and C(build_id) from the C(version) subset; C(status),
        the upgrade status with its URLs redacted, C(protection), the C(upgrade) subset's C({type,
        configured_node_failures, remaining_node_failures}), C(license_state), C(unprotected_edge), C(live_hub_portals),
        C(live_spoke_portals) and C(unavailable_subsets).
      - From its variables, C(target), C(images), already through C(qumulo.cluster.redact_images), C(reboot),
        C(reboot_set_size), C(maintenance_windows), C(window_timezone), C(after) and C(allow_limited_release), its
        C(qumulo_upgrade_*) variables; C(cluster_kind), its C(qumulo_cluster_kind); C(portal_hub), its
        C(qumulo_portal.hub); and C(relationships), its C(qumulo_replication_relationships).
      - C(commit_record), C({path, text}) when the host's commit record is on disk.
      - Required unless O(window) is given.
    type: list
    elements: dict
  limit:
    description: The hosts of the run's C(--limit), or V(null) for the whole inventory.
    type: list
    elements: str
  aggressive:
    description:
      - The run's C(qumulo_upgrade_aggressive).
      - The plan refuses V(true) in this release. With V(false), each host reboots as its C(reboot) gives.
    type: bool
    default: false
  timeouts:
    description: The waits each hop's lease is counted from, in seconds.
    type: dict
    suboptions:
      wait:
        description: Each phase's wait, the role's C(cluster_upgrade_wait_timeout); 3600 when unset.
        type: int
      reboot:
        description: A reboot's wait, the role's C(cluster_upgrade_reboot_timeout); 43200 when unset.
        type: int
      unblock:
        description: The wait for the preflight to clear, the role's C(cluster_upgrade_unblock_timeout); 14400 when unset.
        type: int
      peer:
        description: The wait for the portals, the role's C(cluster_upgrade_peer_timeout); 1800 when unset.
        type: int
  forks:
    description: The play's C(forks), by which each hop's lease counts its batches.
    type: int
  throttle:
    description: The prepares' throttle, the role's C(cluster_upgrade_prepare_throttle), for each hop's lease.
    type: int
  now:
    description:
      - The run's start, as an ISO 8601 date and time, such as V(2026-11-04T17:00:00Z). A time with an offset is converted
        to UTC, and one without an offset is read as UTC. A value that is not ISO 8601 is refused.
      - It is the clock of the O(window) answer, and the start of the week in which the hop's maintenance windows must
        share some time.
      - Defaults to the controller's clock.
    type: str
  restore_files:
    description:
      - The restore records and restore locks on the controller, C({records, locks}), each list as
        M(qumulo.cluster.replication_restore_info) returns it.
      - Each record, and each C(live) lock on a host the run does not lock itself, is a warning (W1) for each host the
        plan moves or reboots among the hosts it names. A record names its edge (C(host)), its key's C(target_host) and
        every target of the edge's C(present) relationships. A lock names its host and that host's C(present) targets.
        A lock that is C(gone) or C(expired) is not a warning.
    type: dict
    default: {}
    suboptions:
      records:
        description: The records, each read for C(host), C(path) and C(key).
        type: list
        elements: dict
        default: []
      locks:
        description: The locks, each read for C(host), C(path), C(holder), C(pid), C(controller), C(lease_expiry) and
          C(liveness).
        type: list
        elements: dict
        default: []
  window:
    description:
      - Answer only whether the hop's commits may begin now, in RV(window), and plan nothing.
    type: dict
    suboptions:
      hop:
        description: One of RV(plan.hops).
        type: dict
        required: true
      held:
        description: The hosts the run has already held in this hop, which do not decide the clock.
        type: list
        elements: str
        default: []
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
seealso:
  - module: qumulo.cluster.upgrade
  - module: qumulo.cluster.upgrade_report
  - module: qumulo.cluster.cluster_info
notes:
  - "API: none."
  - "Registers: G-UPGRADE-PATH, an upgrade path cannot be read ahead; G-UPGRADE-LETTER, a letter release as a source;
    G-UPGRADE-COUNT, a reboot count checked late; G-UPGRADE-RECORD, the commit records it decides;
    G-UPGRADE-LICENSE, the unlicensed edge it warns about."
"""

EXAMPLES = r"""
# edge01_connection holds the connection options, such as {api_host: edge01.example.com, api_username: admin,
# api_password: "{{ vault_edge01_password }}"}.
# upgrade_plan takes no connection options. In a play over many hosts every host computes the same plan, so run_once
# keeps it to one run, and delegate_to: localhost puts that run on the controller.
- name: Read an edge's version and upgrade status for the plan
  # The plan sends nothing; these reads are its input. The upgrade subset gives the status and the protection.
  qumulo.cluster.cluster_info:
    gather_subset: [node, version, upgrade]
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"
  register: edge01_info

- name: Plan the run from every inventory host
  qumulo.cluster.upgrade_plan:
    hosts:
      - name: edge01
        cluster_uuid: "{{ edge01_info.cluster.cluster_id }}"
        version: "{{ edge01_info.cluster.version }}"
        build: "{{ edge01_info.cluster.build_id }}"
        status: "{{ edge01_info.cluster.upgrade.status }}"
        protection: "{{ edge01_info.cluster.upgrade.protection }}"
        cluster_kind: edge
        target: "7.11.0"
        images: "{{ hostvars['edge01'].qumulo_upgrade_images | qumulo.cluster.redact_images }}"
    # The play targets every inventory host, so its hosts are the run's --limit.
    limit: "{{ ansible_play_hosts_all }}"
    forks: "{{ ansible_forks }}"
  check_mode: true
  run_once: true
  register: upgrade_plan_result

- name: Plan a host that commits only inside a maintenance window
  # Reads edge01_info from the task that registered it above.
  qumulo.cluster.upgrade_plan:
    hosts:
      - name: edge01
        cluster_uuid: "{{ edge01_info.cluster.cluster_id }}"
        version: "{{ edge01_info.cluster.version }}"
        build: "{{ edge01_info.cluster.build_id }}"
        status: "{{ edge01_info.cluster.upgrade.status }}"
        protection: "{{ edge01_info.cluster.upgrade.protection }}"
        cluster_kind: edge
        target: "7.11.0"
        images: "{{ hostvars['edge01'].qumulo_upgrade_images | qumulo.cluster.redact_images }}"
        maintenance_windows:
          - start_hour: 22
            start_minute: 0
            end_hour: 4
            end_minute: 0
            on_days: [SAT, SUN]
        window_timezone: America/Los_Angeles
    now: "2026-11-04T17:00:00Z"
  run_once: true
  register: upgrade_plan_windowed

- name: Ask whether the first hop's commits may begin now
  # Needs upgrade_plan_windowed from the plan above, whose host declares a maintenance window. Against a hop that declares
  # none, the answer is always declared false and inside true.
  qumulo.cluster.upgrade_plan:
    window:
      hop: "{{ upgrade_plan_windowed.plan.hops[0] }}"
      held: []
  run_once: true
  register: upgrade_plan_window

- name: Keep the plan's refusals when it refuses
  # Reads edge01_info from the task that registered it above.
  qumulo.cluster.upgrade_plan:
    hosts:
      - name: edge01
        cluster_uuid: "{{ edge01_info.cluster.cluster_id }}"
        version: "{{ edge01_info.cluster.version }}"
        build: "{{ edge01_info.cluster.build_id }}"
        status: "{{ edge01_info.cluster.upgrade.status }}"
        protection: "{{ edge01_info.cluster.upgrade.protection }}"
        cluster_kind: edge
        target: "7.11.0"
        images: "{{ hostvars['edge01'].qumulo_upgrade_images | qumulo.cluster.redact_images }}"
  run_once: true
  register: upgrade_plan_refused
  failed_when: false

- name: Show each refusal the plan made
  # The list is empty when the plan did not refuse.
  ansible.builtin.debug:
    msg: "{{ item.host }} {{ item.code }}: {{ item.message }}"
  loop: "{{ upgrade_plan_refused.plan.refusals | default([]) }}"
  run_once: true
"""

RETURN = r"""
plan:
  description: The run's plan; on a refusal, its refusals and warnings, and no hops.
  returned: without O(window), and on a failure from a refusal
  type: dict
  contains:
    hops:
      description:
        - The hops in order, each with its C(number), C(release), C(lease) in seconds, the hosts it C(moves), its portal
          C(components) with their C(split) and C(closes) marks, its commit C(steps), its C(units), its C(hosts), its
          C(windows) and its C(notes).
        - Each hop's C(forks_warning) says that O(forks), or O(throttle), is below the hop's size or its largest commit
          step, for the C(qumulo.cluster.cluster_upgrade) role to print, or is V(null). It is not among
          RV(plan.warnings).
        - Each of C(hosts) gives the C(image_index) of the host's entry in its own C(qumulo_upgrade_images), the entry's
          C(image_redacted), C(version), C(build) and C(kind), the C(reboot) mode, its C(unit), C(round), C(wave) and
          C(step), the hosts a failure of it C(holds), and C(reboots_only) for a host on the target build still
          rebooting.
      type: list
      elements: dict
    lock_hosts:
      description: Each host the run locks, with the hosts its lock protects, itself and its replication targets.
      type: dict
    records:
      description:
        - The commit records to C(retire) and to C(drop), each C({host, path, digest, line}). Nothing is removed here.
      type: dict
    skipped:
      description: The hosts already at their target.
      type: list
      elements: str
    refusals:
      description: Each refusal, C({host, code, message}).
      type: list
      elements: dict
    warnings:
      description: Each warning, C({code, host, message}).
      type: list
      elements: dict
    notes:
      description: What the plan noticed, each C({host, message}).
      type: list
      elements: dict
  sample:
    hops:
      - number: 1
        release: "7.11.0"
        lease: 151200
        forks_warning: null
        moves: [edge01]
        components: {}
        steps: [{round: 1, wave: null, hosts: [edge01]}]
        units:
          - name: edge01
            round: 1
            waves: [[edge01]]
            follows: []
            reason: a cluster in no portal component the hop moves
        hosts:
          edge01:
            image_index: 0
            image_redacted: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
            version: "7.11.0"
            build: null
            kind: on-prem
            reboot: rolling
            unit: edge01
            round: 1
            wave: null
            step: 0
            holds: [edge01]
            reboots_only: false
        windows: {}
        notes: []
    lock_hosts: {edge01: [edge01]}
    records: {retire: [], drop: []}
    skipped: []
    refusals: []
    warnings: []
    notes: []
window:
  description:
    - Whether the hop's commits may begin now.
    - C(declared) is false when no host the hop commits declares a window. C(inside) is false when one is outside.
      C(next_open) is the UTC time every such host is next inside, and C(outside_hosts) the hosts outside.
    - C(held) is the hosts the window holds, C(lines) the split components that commit to close their split, and
      C(message) the refusal for the held hosts, or V(null).
  returned: with O(window)
  type: dict
  sample:
    declared: true
    inside: false
    next_open: "2026-11-08T06:00:00Z"
    outside_hosts: [edge01]
    held: [edge01]
    lines: []
    message: "edge01 were not committed: the hop to 7.11.0 is outside their maintenance window, which next opens 2026-11-07 22:00
      America/Los_Angeles. They stay prepared. Run the upgrade again inside the window."
"""

from datetime import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade import utc_now
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_fleet import fleet_hops, window_form

ARGUMENT_SPEC = {
    "hosts": {"type": "list", "elements": "dict"},
    "limit": {"type": "list", "elements": "str"},
    "aggressive": {"type": "bool", "default": False},
    "timeouts": {
        "type": "dict",
        "options": {"wait": {"type": "int"}, "reboot": {"type": "int"}, "unblock": {"type": "int"}, "peer": {"type": "int"}},
    },
    "forks": {"type": "int"},
    "throttle": {"type": "int"},
    "now": {"type": "str"},
    "restore_files": {
        "type": "dict",
        "default": {},
        "options": {"records": {"type": "list", "elements": "dict", "default": []}, "locks": {"type": "list", "elements": "dict", "default": []}},
    },
    "window": {
        "type": "dict",
        "options": {"hop": {"type": "dict", "required": True}, "held": {"type": "list", "elements": "str", "default": []}},
    },
}


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, required_one_of=[("hosts", "window")], supports_check_mode=True)
    params = module.params
    now = params["now"] or utc_now()
    if isinstance(now, str):
        try:
            datetime.fromisoformat(now)
        except ValueError:
            module.fail_json(msg=f"now must be an ISO 8601 time, such as 2026-11-04T17:00:00Z, not {now}")
    if params["window"] is not None:
        module.exit_json(changed=False, window=window_form(params["window"]["hop"], params["window"]["held"], now))
    plan = fleet_hops(
        params["hosts"],
        limit=params["limit"],
        aggressive=params["aggressive"],
        timeouts={name: value for name, value in (params["timeouts"] or {}).items() if value is not None},
        forks=params["forks"],
        throttle=params["throttle"],
        now=now,
        restore_files=params["restore_files"],
    )
    for warning in plan["warnings"]:
        module.warn(warning["message"])
    if plan["refusals"]:
        module.fail_json(msg="\n".join(refusal["message"] for refusal in plan["refusals"]), plan=plan)
    module.exit_json(changed=False, plan=plan)


if __name__ == "__main__":
    main()
