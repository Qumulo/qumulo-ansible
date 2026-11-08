#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: upgrade
short_description: Upgrade a Qumulo cluster by one hop, from verify to its reboots
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Upgrades one cluster to the release of one image, through Qumulo Core's own upgrade routes. O(state) sets how far the
    task goes, through verify, prepare, commit or the commit's reboots. Each phase reads back before it writes, so a rerun after
    a failure picks up where the cluster is.
  - An upgrade is an action, not a setting (G-ACTIONS). A cluster that already runs the target release, and the declared
    O(build) when one is given, with no reboot under way, is left alone. A cluster PREPARED with the same image, reboot
    settings and waivers is not prepared again; with other settings or another image it is prepared again, which
    replaces the earlier prepare.
  - Before any write the module reads the cluster's UUID, its version, the upgrade status, the preflight, the protection
    status, the reboot status and the nodes. A reboot under way is waited out first, up to O(reboot_timeout). A reboot
    paused outside this run, or stopped on an error, fails at once.
  - The commit's outcome is decided from the running build, not from an IDLE status (G-UPGRADE-RECORD). A commit that did
    not finish fails at once with RV(unfinished_commit), and the module sends nothing more.
  - With O(commit_record), the module writes a record on the controller just before the commit request, and decides any
    record it finds there before any write, as M(qumulo.cluster.upgrade_plan) does.
  - Check mode sends only reads, and verify when O(verify_in_check_mode) is set, and waits for nothing. It reports the
    phases the task would run and the waits it would make. In check mode C(changed) is true whenever a phase would run,
    even a readiness check of a cluster already PREPARED, whose real run changes nothing.
  - This module runs one hop of one cluster. The C(qumulo.cluster.cluster_upgrade) role runs a fleet.
options:
  image:
    description:
      - The image, as an C(http://) or C(https://) URL every node can reach without its query string and user
        information, or an absolute path, which each node reads from its own disk or else from the cluster's file
        system.
      - A URL that carries a query string or user information is refused before any request; pass those parts in
        O(image_secret).
      - A plain URL or a path reads in full in the result and in every message.
      - Required, except with O(state=rebooted) and O(commit=false), which only waits for the reboots and needs
        O(version) instead.
    type: str
  image_secret:
    description:
      - The parts of the image's URL that are secret, joined back to O(image) inside the module.
      - The filter C(qumulo.cluster.url_secret) produces it from the full URL, and C(qumulo.cluster.redact_images)
        produces O(image) from the same URL.
    type: dict
    suboptions:
      userinfo:
        description: The URL's user information, as C(user:password).
        type: str
      query:
        description: The URL's query string, without its C(?), as a presigned URL's signature.
        type: str
  version:
    description:
      - The release the image holds, such as V(7.11.0) or V(7.10.0.3), as a string.
      - Unset, it is read from an image file named C(qumulo_core_<version>.qimg) or C(cloud_qumulo_core_<version>.qimg),
        and otherwise learned from verify.
      - The prepare compares it with the release Qumulo Core reads in the image, and refuses a commit when they differ
        (G-UPGRADE-PATH).
    type: str
  build:
    description:
      - The build the cluster must end on, as Qumulo Care names it. Once the cluster runs a new build of O(version),
        another build fails at once.
      - Needed for O(allow_upgrade_from_limited_release) on a source below 7.11.0.
    type: str
  state:
    description:
      - V(verified) runs verify, which every node runs on the image and which installs nothing.
      - V(prepared) leaves the cluster PREPARED with the image, the reboot settings and the waivers. With
        O(ready_to_commit), it then decides with reads and waits only whether the cluster may commit now.
      - V(committed) prepares first when needed, then commits, and returns once the commit is done. On a rolling platform
        upgrade, the commit is done once the new build forms a quorum and the status reads REBOOTING; O(state=rebooted)
        waits for the rest.
      - V(rebooted) is V(committed), and then waits for the platform upgrade's reboots to finish.
    type: str
    choices: [verified, prepared, committed, rebooted]
    default: committed
  ready_to_commit:
    description:
      - With O(state=prepared), also check that the cluster may commit now. The preflight must clear, and the image must
        hold O(version). On a platform upgrade, the current node fault tolerance must reach O(reboot_set_size), and with
        O(reboot=all) it must be back at the configured tolerance. Each of those is waited for up to O(wait_timeout).
    type: bool
    default: false
  prepare:
    description:
      - With O(state=prepared) or O(state=committed), prepare a cluster that is not PREPARED with the image, the reboot
        settings and the waivers. V(false) refuses such a cluster instead, and sends only reads before the commit.
    type: bool
    default: true
  commit:
    description:
      - With O(state=rebooted), V(false) refuses a cluster that does not already run the target release, so the task only
        waits for the reboots and never commits.
    type: bool
    default: true
  reboot:
    description:
      - How a platform upgrade reboots its hosts. V(rolling) reboots them in groups, each once the cluster has
        reprotected; V(all) reboots every host at once.
      - Unset, the reboots are rolling.
      - A cluster whose configured node fault tolerance is 0 reboots every host at once whatever this says, with a
        warning when a declaration is ignored.
    type: str
    choices: [rolling, all]
  reboot_set_size:
    description:
      - How many nodes a rolling reboot takes at once, at least 1. Unset, Qumulo Core uses its own, the current tolerance
        minus one, at least one (G-UPGRADE-COUNT).
      - The prepare waits up to O(wait_timeout) for the current node fault tolerance to reach it.
      - Ignored, with a warning, when the reboot is not rolling.
    type: int
  verify_in_check_mode:
    description:
      - In check mode, also run verify, which downloads the whole image on every node and installs nothing.
    type: bool
    default: false
  allow_upgrade_from_limited_release:
    description:
      - On a cluster that runs a limited (letter) release, such as 7.10.3A, send Qumulo Core's waiver of the rule that
        refuses such a source (G-UPGRADE-LETTER). From 7.11.0 that is the named rule
        C(allow_upgrade_from_limited_release); below 7.11.0 it is C(override_compatibility_check), which lifts every
        compatibility rule, so it is sent only with O(build) and only after a verify, sent with it, reads O(version).
      - On a cluster that runs no letter release, nothing is sent.
    type: bool
    default: false
  host_name:
    description:
      - The name this module's messages and returns give the cluster. Defaults to O(api_host).
      - The C(qumulo.cluster.cluster_upgrade) role always sets it, to the inventory host's name. When it is set, the
        refusals name the role's and the inventory's settings, such as C(cluster_upgrade_wait_timeout) and
        C(qumulo_upgrade_reboot_set_size), in place of O(wait_timeout) and O(reboot_set_size).
      - When it is set, the module does not warn that a reboot declaration is ignored; M(qumulo.cluster.upgrade_plan) gives
        that warning instead.
    type: str
  commit_record:
    description:
      - The path, on the controller, of this cluster's commit record. The C(qumulo.cluster.cluster_upgrade) role passes
        C(<cluster_upgrade_artifact_dir>/<host>/upgrade/commit.yml).
      - The module writes it as pending just before the commit request, and flushes it to disk first. It removes it once
        the commit is done or refused, and marks it unfinished when the commit did not finish.
      - A record already there is decided before any write. One this cluster's state shows done, or that belongs to
        another cluster, is removed, with a warning. One the cluster's state shows was never committed is removed too.
        One of a commit still under way, or one that did not finish, is refused. A file that is not a commit record is
        refused.
      - Unset, no record is written, and the failure messages say so.
    type: path
  wait_timeout:
    description:
      - Seconds each phase waits, in turn, for the preflight, the tolerance, the prepare and the commit.
    type: int
    default: 3600
  reboot_timeout:
    description:
      - Seconds a wait for a reboot may take, before the prepare and at O(state=rebooted).
    type: int
    default: 43200
  maintenance_windows:
    description:
      - Time ranges inside which the commit may begin, in O(window_timezone). Outside them the module still prepares,
        then refuses the commit, naming when the window next opens; the cluster stays prepared.
      - A range whose end is at or before its start runs past midnight, and its O(maintenance_windows[].on_days) names
        the day it starts. Unset, the commit may begin at any time.
      - A timezone that is no IANA name, an hour outside 0 to 23, a minute outside 0 to 59 or a day name outside the week
        is refused before any request.
      - The C(qumulo.cluster.cluster_upgrade) role checks the window itself, once per hop, and never passes this option.
    type: list
    elements: dict
    suboptions:
      start_hour:
        description: The hour the range starts, 0 to 23.
        type: int
        required: true
      start_minute:
        description: The minute the range starts, 0 to 59.
        type: int
        required: true
      end_hour:
        description: The hour the range ends, 0 to 23.
        type: int
        required: true
      end_minute:
        description: The minute the range ends, 0 to 59.
        type: int
        required: true
      on_days:
        description: The days the range starts on, as V(MON) to V(SUN), or V(EVERY_DAY).
        type: list
        elements: str
        required: true
  window_timezone:
    description:
      - The IANA timezone of O(maintenance_windows), such as V(America/Los_Angeles). Required with them.
    type: str
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
      - A cluster at the target, or PREPARED with the same image and settings, is left alone. A commit is an action that
        cannot be undone or sent twice (G-UPGRADE-ONESHOT).
seealso:
  - module: qumulo.cluster.upgrade_plan
  - module: qumulo.cluster.upgrade_report
  - module: qumulo.cluster.cluster_info
notes:
  - "API: GET /v1/node/state and /v1/version, which need no login; GET /v3/upgrade/status, never the deprecated v2 status,
    /v2/upgrade/blocked, /v1/cluster/protection/status, /v1/shutdown/reboot/status and /v1/cluster/nodes/; POST
    /v2/upgrade/verify-image, /v2/upgrade/prepare and /v2/upgrade/commit."
  - Verify and prepare are sent with a request timeout of their own, 2700 seconds on a source of 7.10.3 or later and 4500
    below. The API host, and any load balancer or NAT gateway in front of it, must hold an idle connection that long; one
    closed sooner loses the answer, which the module reads back.
  - None of verify, prepare and commit is sent again after a lost answer; the module reads back instead. The one
    exception is a commit found still PREPARED, with the REST API answering throughout, which is sent once more.
  - While a reboot is under way, the preflight reads degraded for the nodes the reboot has taken, and that is waited
    out as the reboot (G-UPGRADE-BLOCKED). A degraded preflight outside a reboot fails at once.
  - Qumulo Core's own refusals, such as an unlicensed edge's (G-UPGRADE-LICENSE), are passed on with its words, and with
    a line saying every node must reach a URL image when the refusal could come from an unreachable URL.
  - "Registers: G-ACTIONS, G-UPGRADE-ONESHOT, G-UPGRADE-RECORD, G-UPGRADE-LETTER, G-UPGRADE-PATH, G-UPGRADE-COUNT,
    G-UPGRADE-BLOCKED, G-UPGRADE-LICENSE."
"""

EXAMPLES = r"""
# edge01_connection holds the connection options, such as {api_host: edge01.example.com, api_username: admin,
# api_password: "{{ vault_edge01_password }}"}. image_url is the image's full URL, presigned query string included.
- name: Upgrade one cluster to 7.11.0 and wait for its reboots
  qumulo.cluster.upgrade:
    image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    state: rebooted
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Preview the upgrade without sending anything but reads
  qumulo.cluster.upgrade:
    image: /upgrades/qumulo_core_7.11.0.qimg
    version: "7.11.0"
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"
  check_mode: true
  diff: true
  register: upgrade_prediction

- name: Preview the upgrade and run verify on every node
  qumulo.cluster.upgrade:
    image: /upgrades/qumulo_core_7.11.0.qimg
    version: "7.11.0"
    verify_in_check_mode: true
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"
  check_mode: true

- name: Verify the image on every node, installing nothing
  qumulo.cluster.upgrade:
    image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    state: verified
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Prepare from a presigned URL, and check the cluster may commit now
  qumulo.cluster.upgrade:
    image: "{{ [image_url] | qumulo.cluster.redact_images | first }}"
    image_secret: "{{ image_url | qumulo.cluster.url_secret }}"
    version: "7.11.0"
    state: prepared
    ready_to_commit: true
    commit_record: "{{ playbook_dir }}/artifacts/{{ inventory_hostname }}/upgrade/commit.yml"
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Commit a cluster that an earlier task prepared, and refuse to prepare it again
  # image, image_secret, version, commit_record, reboot, reboot_set_size and allow_upgrade_from_limited_release must be the
  # ones the prepare sent, or the commit is refused. This task repeats the prepare example above.
  qumulo.cluster.upgrade:
    image: "{{ [image_url] | qumulo.cluster.redact_images | first }}"
    image_secret: "{{ image_url | qumulo.cluster.url_secret }}"
    version: "7.11.0"
    state: committed
    prepare: false
    commit_record: "{{ playbook_dir }}/artifacts/{{ inventory_hostname }}/upgrade/commit.yml"
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Reboot every node at once, in one outage, instead of in groups
  qumulo.cluster.upgrade:
    image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    state: rebooted
    reboot: all
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Reboot two nodes at a time and give the reboots a day
  # Leave reboot_set_size unset to take Qumulo Core's own size, which suits most clusters.
  qumulo.cluster.upgrade:
    image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    state: rebooted
    reboot: rolling
    reboot_set_size: 2
    reboot_timeout: 86400
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Commit only inside a weekend maintenance window
  # Outside the window the task still prepares, then refuses the commit and leaves the cluster prepared.
  qumulo.cluster.upgrade:
    image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    maintenance_windows:
      - start_hour: 22
        start_minute: 0
        end_hour: 4
        end_minute: 0
        on_days: [SAT, SUN]
    window_timezone: America/Los_Angeles
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Wait for the reboots of an upgrade that was already committed
  qumulo.cluster.upgrade:
    version: "7.11.0"
    state: rebooted
    commit: false
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Upgrade a cluster that runs a limited release, at the direction of Qumulo Care
  # Set this only when Qumulo Care directs it. Below 7.11.0 the waiver needs the build Qumulo Care names.
  qumulo.cluster.upgrade:
    image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    version: "7.11.0"
    build: "393623.1.1"
    allow_upgrade_from_limited_release: true
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"

- name: Upgrade, and on a failure say whether a write may have been applied
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ edge01_connection }}"
  block:
    - name: Upgrade edge01 to 7.11.0
      qumulo.cluster.upgrade:
        image: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
  rescue:
    - name: Fail with the module's message and whether its write may have been applied
      ansible.builtin.fail:
        msg: "{{ ansible_failed_result.msg }} Maybe applied: {{ ansible_failed_result.maybe_applied | default(false) }}."
"""

RETURN = r"""
plan:
  description: The phases the task runs, or in check mode would run, and the waits it would make.
  returned: when anything is to be done, in check mode or a real run; not on a failure
  type: dict
  contains:
    from:
      description: The running release, its build and the upgrade status's state.
      type: dict
    to:
      description: The image's release, as declared or read, and the declared build.
      type: dict
    image_redacted:
      description: The image without its URL's query string and user information.
      type: str
    phases:
      description: The phases in order, of V(verify), V(prepare), V(ready), V(commit) and V(reboots).
      type: list
      elements: str
    waits:
      description: Each wait a real run would make, which check mode predicts and does not refuse.
      type: list
      elements: str
    reboot:
      description: The reboot mode the prepare sends, C(mode), and the set size, C(set_size), if any.
      type: dict
    waiver:
      description: The limited-release waiver the task sends, as its C(parameter), and the rules it C(lifts).
      type: dict
    window:
      description:
        - Where the commit falls against O(maintenance_windows), as C(inside), C(next_open) and C(message).
      type: dict
    record:
      description: A commit record the task removes, with its C(action), V(retire) or V(drop), its C(path) and its C(line).
      type: dict
  sample:
    from: {version: "7.10.3", build: "385145.1.82.1.73", state: IDLE}
    to: {version: "7.11.0", build: null}
    image_redacted: https://images.example.com/qumulo/qumulo_core_7.11.0.qimg
    phases: [prepare, commit, reboots]
    waits: []
    reboot: {mode: rolling, set_size: null}
cluster_uuid:
  description: The cluster's UUID, read from GET /v1/node/state before any write. V(null) on a failure before it was read.
  returned: always, except on a refusal before the first request
  type: str
image_redacted:
  description: The image without its URL's query string and user information. V(null) when no O(image) is given.
  returned: always, except on a refusal of a missing O(image), of O(maintenance_windows) or of O(reboot_set_size)
  type: str
from:
  description: The release and build the cluster ran when the task started. V(null) on a failure before the version was read.
  returned: always, except on a refusal before the first request
  type: dict
  contains:
    version:
      description: The release, such as V(7.10.3).
      type: str
    build:
      description: The build_id.
      type: str
to:
  description: The release and build the cluster ends on, once the commit is done; before, O(version), or the release the
    image's file name or verify gives, and O(build).
  returned: always, except on a refusal before the first request
  type: dict
  contains:
    version:
      description: The release.
      type: str
    build:
      description: The build_id, or the declared build.
      type: str
upgrade_type:
  description: V(SOFTWARE_ONLY) or V(SOFTWARE_AND_PLATFORM), as Qumulo Core reads the image, once verify, the prepare or
    the upgrade status has read it. V(null) before then.
  returned: always, except on a refusal before the first request
  type: str
phases:
  description: Each phase the task ran, with its start and end in UTC; the commit ends when it is done.
  returned: always, except on a refusal before the first request
  type: dict
  sample: {prepare: {started: "2026-11-04T10:00:00+00:00", ended: "2026-11-04T10:31:12+00:00"}}
downtime_seconds:
  description: The seconds the REST API did not answer during the commit's restart, until the new build first answered.
    V(null) when the task did not wait for a commit.
  returned: always, except on a refusal before the first request
  type: float
ready:
  description: V(true) when the readiness check found the cluster ready to commit; V(null) when no check ran or it did
    not clear.
  returned: always, except on a refusal before the first request
  type: bool
prepared:
  description: Whether this task prepared the cluster.
  returned: always, except on a refusal before the first request
  type: bool
commit_sent:
  description: Whether the commit may have been applied, after a 202 or a lost answer. An answered refusal leaves it
    false.
  returned: always, except on a refusal before the first request
  type: bool
reboot:
  description: The reboot this task's settings give. C(mode) is V(rolling) or V(all), or V(none)
    once the image reads software-only; C(set_size) is the rolling set size sent, or V(null); C(seconds) is the seconds
    the task waited for the reboots, or V(null) when it waited for none. V(null) on a failure before the version was read.
  returned: always, except on a refusal before the first request
  type: dict
waiver:
  description: The limited-release waiver sent, as its C(parameter), and the rules it C(lifts). V(null) when none was sent.
  returned: always, except on a refusal before the first request
  type: dict
status:
  description:
    - The last upgrade status read, with every URL in it redacted, or V(null) before it was read.
    - On a failure that Qumulo Core answered with an HTTP error, the HTTP status code instead.
  returned: always, except on a refusal before the first request
  type: raw
error_class:
  description: Qumulo Core's error class, on a failure it answered with an error, or on its refusal of the image, where it
    may be V(null).
  returned: on such a failure
  type: str
notes:
  description: What the module noticed, such as the last attempt's settings an IDLE status still shows, or the earlier
    image a new prepare replaced.
  returned: when there is something to note
  type: list
  elements: str
final:
  description: True on a refusal that a second prepare cannot lift, on which the C(qumulo.cluster.cluster_upgrade) role
    holds the host's component.
  returned: on such a failure
  type: bool
maybe_applied:
  description: True when a write was sent and the read-back did not confirm it, as a lost prepare answer, a commit whose
    wait ran out, or a commit that did not finish.
  returned: on such a failure
  type: bool
unfinished_commit:
  description: True when the commit did not finish and the cluster is back on its old build, IDLE.
  returned: on that failure
  type: bool
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade import Upgrade

ARGUMENT_SPEC = {
    "image": {"type": "str"},
    "image_secret": {"type": "dict", "no_log": True, "options": {"userinfo": {"type": "str"}, "query": {"type": "str"}}},
    "version": {"type": "str"},
    "build": {"type": "str"},
    "state": {"type": "str", "choices": ["verified", "prepared", "committed", "rebooted"], "default": "committed"},
    "ready_to_commit": {"type": "bool", "default": False},
    "prepare": {"type": "bool", "default": True},
    "commit": {"type": "bool", "default": True},
    "reboot": {"type": "str", "choices": ["rolling", "all"]},
    "reboot_set_size": {"type": "int"},
    "verify_in_check_mode": {"type": "bool", "default": False},
    "allow_upgrade_from_limited_release": {"type": "bool", "default": False},
    "host_name": {"type": "str"},
    "commit_record": {"type": "path"},
    "wait_timeout": {"type": "int", "default": 3600},
    "reboot_timeout": {"type": "int", "default": 43200},
    "maintenance_windows": {
        "type": "list",
        "elements": "dict",
        "options": {
            "start_hour": {"type": "int", "required": True},
            "start_minute": {"type": "int", "required": True},
            "end_hour": {"type": "int", "required": True},
            "end_minute": {"type": "int", "required": True},
            "on_days": {"type": "list", "elements": "str", "required": True},
        },
    },
    "window_timezone": {"type": "str"},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC, required_by={"maintenance_windows": "window_timezone"})
    size = module.params["reboot_set_size"]
    if size is not None and size < 1:
        # Qumulo Core refuses 0 only after every node has downloaded the image.
        module.fail_json(msg=f"reboot_set_size must be at least 1, not {size}; leave it unset so Qumulo Core sizes the groups")
    run_resource(module, Upgrade)


if __name__ == "__main__":
    main()
