#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: upgrade_report
short_description: Turn one hop's upgrade results into the hop's report
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Takes one hop of M(qumulo.cluster.upgrade_plan)'s plan and each host's registered M(qumulo.cluster.upgrade) results
    for the hop, and returns the hop's report, which the C(qumulo.cluster.cluster_upgrade) role writes after each hop.
  - A pure computation. It sends no request, takes no connection options, and is not in the C(qumulo.cluster.qumulo)
    action group, so C(module_defaults) for that group do not reach it.
  - Each host's entry gives the release and build it ran and ends on, the upgrade type, each phase's start and end, the
    prepare's duration, the commit's start, the seconds the REST API did not answer during the commit's restart, the
    reboot, the outcome and its lines.
  - Each portal component the hop moves gets its mismatch window, from its first member's commit start to its last
    member's commit done, and whether the hop left it on two releases.
  - Only the results' return fields are read, never their C(invocation), and every text passes through the same URL
    redaction as the modules' own messages.
options:
  hop:
    description: The hop, one of M(qumulo.cluster.upgrade_plan)'s RV(qumulo.cluster.upgrade_plan#module:plan.hops).
    type: dict
    required: true
  results:
    description:
      - A mapping of host name to that host's registered M(qumulo.cluster.upgrade) results for this hop, a list in the
        order the tasks ran, or one result.
      - A failure is its registered result, with C(failed) and C(msg). The module leaves out skipped results.
    type: dict
    default: {}
  held:
    description: The hosts the run held out of this hop's commits.
    type: list
    elements: str
    default: []
  lock_held:
    description: The hosts a restore lock the run could not take holds out of every hop.
    type: list
    elements: str
    default: []
  prepare_failed:
    description:
      - The hosts whose first prepare failed with a refusal a second prepare may lift, which the run kept for its second
        prepare.
      - Such a host whose failed first prepare is its last result, its second prepare skipped because its unit was held,
        is reported as held, with the host whose failure held it.
    type: list
    elements: str
    default: []
  check_mode_run:
    description: Whether the run was a check run, whose results predict and change nothing.
    type: bool
    default: false
  peers:
    description: The peer steps' readings, before the reboots and after them.
    type: dict
    default: {}
    suboptions:
      commits:
        description:
          - The readings after the commits. A portal's is C({component, host, active, seconds, line}), with C(active)
            V(null) when the step skipped the component; a replication relationship's is C({source, target, error, line}).
        type: list
        elements: dict
        default: []
      reboots:
        description: The readings after the reboots, in the same shape.
        type: list
        elements: dict
        default: []
  window:
    description: The window form's answer for the hop, as M(qumulo.cluster.upgrade_plan) returns it in RV(qumulo.cluster.upgrade_plan#module:window).
    type: dict
  lines:
    description:
      - The caller's own lines for the hop, such as why the run stopped before it. They come first among the hop's
        lines.
    type: list
    elements: str
    default: []
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
seealso:
  - module: qumulo.cluster.upgrade
  - module: qumulo.cluster.upgrade_plan
notes:
  - "API: none."
"""

EXAMPLES = r"""
# upgrade_report takes no connection options. In a play over many hosts every host computes the same report, so run_once
# keeps it to one run, and delegate_to: localhost puts that run on the controller.
- name: Report a hop from every host's results
  # upgrade_plan_result is the registered result of qumulo.cluster.upgrade_plan, and edge01_prepare and edge01_commit
  # are the registered results of two qumulo.cluster.upgrade tasks run against edge01.
  qumulo.cluster.upgrade_report:
    hop: "{{ upgrade_plan_result.plan.hops[0] }}"
    results:
      edge01:
        - "{{ edge01_prepare }}"
        - "{{ edge01_commit }}"
  run_once: true
  register: upgrade_report_result

- name: Report a hop of a check run
  # upgrade_plan_result is the registered result of qumulo.cluster.upgrade_plan, and upgrade_prediction is the registered
  # result of a qumulo.cluster.upgrade task run against edge01 in check mode.
  qumulo.cluster.upgrade_report:
    hop: "{{ upgrade_plan_result.plan.hops[0] }}"
    results:
      edge01:
        - "{{ upgrade_prediction }}"
    check_mode_run: true
  run_once: true
  register: upgrade_report_check

- name: Report a hop that held a host out of its commits
  # upgrade_hop_results maps each host to its registered qumulo.cluster.upgrade results; upgrade_held lists the hosts the
  # run held; upgrade_plan_window is the registered result of qumulo.cluster.upgrade_plan with its window option.
  qumulo.cluster.upgrade_report:
    hop: "{{ upgrade_plan_result.plan.hops[0] }}"
    results: "{{ upgrade_hop_results }}"
    held: "{{ upgrade_held }}"
    window: "{{ upgrade_plan_window.window | default(omit) }}"
    lines:
      - The run stopped before the second wave.
  run_once: true
  register: upgrade_report_held

- name: Show the lines of the hop's report
  ansible.builtin.debug:
    var: upgrade_report_held.report.lines
"""

RETURN = r"""
report:
  description: The hop's report.
  returned: always
  type: dict
  contains:
    hop:
      description: The hop's C(number), C(release), C(lease) and the hosts it C(moves).
      type: dict
    check_mode_run:
      description: Whether the results are a check run's predictions.
      type: bool
    hosts:
      description:
        - Each host of the hop, with C(from) and C(to), each C({version, build}), C(upgrade_type), C(phases), each
          C({started, ended}) in UTC, C(prepare_seconds), C(commit_started), C(downtime_seconds), C(reboot), C({mode,
          set_size, seconds}), C(commit_sent), C(committed) and C(lines).
        - C(outcome) is V(committed), V(rebooted) once a platform upgrade's reboots are done, V(failed) with its
          C(message), C(maybe_applied) and C(unfinished_commit), V(held) with C(held_by), the host whose failure held it,
          and C(lock_held), V(at target), V(predicted) in a check run, with the module's C(prediction), or V(not
          committed).
      type: dict
    components:
      description:
        - Each portal component the hop moves, with its C(members), its C(moving) members, C(split), its mismatch
          C(window), C({started, ended, seconds, open, uncommitted}) or V(null) when no member's commit started, and
          C(two_releases), whether the hop left it on two releases.
      type: dict
    peers:
      description: The peer steps' readings, O(peers) as given, as C(commits) and C(reboots) lists.
      type: dict
    window:
      description: The window form's answer, O(window) as given, or V(null).
      type: dict
    warnings:
      description: Each warning the hop's results gave, C({host, message}).
      type: list
      elements: dict
    notes:
      description: The hop's notes from the plan.
      type: list
      elements: str
    lines:
      description:
        - The hop's own lines, O(lines) first, then the window's, the peer steps' and each component left on two
          releases.
      type: list
      elements: str
  sample:
    hop: {number: 1, release: "7.11.0", lease: 151200, moves: [edge01]}
    check_mode_run: false
    hosts:
      edge01:
        from: {version: "7.10.3", build: "385145.1.82.1.73"}
        to: {version: "7.11.0", build: "393623.1.1"}
        upgrade_type: SOFTWARE_AND_PLATFORM
        phases: {prepare: {started: "2026-11-04T10:00:00+00:00", ended: "2026-11-04T10:31:12+00:00"}}
        prepare_seconds: 1872
        commit_started: "2026-11-04T10:40:00+00:00"
        downtime_seconds: 41.5
        reboot: {mode: rolling, set_size: null, seconds: 2400}
        commit_sent: true
        committed: true
        outcome: rebooted
        lines: []
    components: {}
    peers: {commits: [], reboots: []}
    window: null
    warnings: []
    notes: []
    lines: []
"""

from datetime import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import redact_text
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_fleet import mismatch_windows

ARGUMENT_SPEC = {
    "hop": {"type": "dict", "required": True},
    "results": {"type": "dict", "default": {}},
    "held": {"type": "list", "elements": "str", "default": []},
    "lock_held": {"type": "list", "elements": "str", "default": []},
    "prepare_failed": {"type": "list", "elements": "str", "default": []},
    "check_mode_run": {"type": "bool", "default": False},
    "peers": {
        "type": "dict",
        "default": {},
        "options": {
            "commits": {"type": "list", "elements": "dict", "default": []},
            "reboots": {"type": "list", "elements": "dict", "default": []},
        },
    },
    "window": {"type": "dict"},
    "lines": {"type": "list", "elements": "str", "default": []},
}


def _registered(entries):
    """A host's results that ran, in order; a skipped task registers no return fields."""
    entries = [entries] if isinstance(entries, dict) else entries or []
    return [r for r in entries if isinstance(r, dict) and not r.get("skipped")]


def _last(results, name):
    return next((r[name] for r in reversed(results) if r.get(name) is not None), None)


def _seconds(phase):
    if not (phase or {}).get("started") or not phase.get("ended"):
        return None
    return int((datetime.fromisoformat(phase["ended"]) - datetime.fromisoformat(phase["started"])).total_seconds())


def _reboot(results):
    """The reboot the commit set up, with the seconds of the last wait for it; step 7 reads the mode back after the fact."""
    setting = next((r.get("reboot") for r in reversed(results) if "commit" in (r.get("phases") or {}) and r.get("reboot")), None)
    reboot = dict(setting or _last(results, "reboot") or {})
    seconds = [r["reboot"]["seconds"] for r in results if (r.get("reboot") or {}).get("seconds") is not None]
    if seconds:
        reboot["seconds"] = seconds[-1]
    return reboot or None


def _unique(entries):
    found = []
    for entry in entries:
        if entry not in found:
            found.append(entry)
    return found


def _host_entry(host, results, check_mode_run, prepare_failed):
    phases = {}
    for r in results:
        phases.update(r.get("phases") or {})
    last = results[-1] if results else {}
    origin, to = next((r["from"] for r in results if r.get("from")), None), _last(results, "to")
    committed = bool((phases.get("commit") or {}).get("ended"))
    entry = {
        "from": origin,
        "to": to,
        "upgrade_type": _last(results, "upgrade_type"),
        "phases": phases,
        "prepare_seconds": _seconds(phases.get("prepare")),
        "commit_started": (phases.get("commit") or {}).get("started"),
        "downtime_seconds": _last(results, "downtime_seconds"),
        "reboot": _reboot(results),
        "commit_sent": any(r.get("commit_sent") for r in results),
        "committed": committed,
        "lines": [note for r in results for note in r.get("notes") or ()],
    }
    if any(r.get("ready") and r.get("prepared") for r in results):
        entry["lines"].append(f"{host} was prepared again at its commit step's readiness check, inside its component's mismatch window.")
    kept_failure = host in prepare_failed and [r for r in results if r.get("failed")] == [last]
    if kept_failure:
        entry["lines"].append(f"{host}'s first prepare failed and was not tried again, since its unit was held: {last.get('msg')}")
    elif last.get("failed"):
        entry.update(
            outcome="failed", message=last.get("msg"), maybe_applied=bool(last.get("maybe_applied")), unfinished_commit=bool(last.get("unfinished_commit"))
        )
    elif (phases.get("reboots") or {}).get("ended"):
        entry["outcome"] = "rebooted"
    elif committed:
        entry["outcome"] = "committed"
    elif origin and to and origin.get("version") == to.get("version") and not phases:
        entry["outcome"] = "at target"
    elif check_mode_run and results:
        entry.update(outcome="predicted", prediction=last.get("plan"))
    return entry


def _held(hop, entries, held, lock_held):
    """Mark the hosts the run held with the host whose failure held them, from the plan's holds."""
    failed = sorted(h for h, e in entries.items() if e.get("outcome") == "failed")
    for host, entry in entries.items():
        if "outcome" in entry:
            continue
        if host not in held and host not in lock_held:
            entry["outcome"] = "not committed"
            continue
        by = next((f for f in failed if host in hop["hosts"][f].get("holds", ())), None)
        entry.update(outcome="held", held_by=by, lock_held=host in lock_held)
        if by:
            entry["lines"].append(f"{host} was not committed: the failure of {by} holds it.")
        elif host in lock_held:
            entry["lines"].append(f"{host} was not committed: a restore lock the run could not take protects it.")
        else:
            entry["lines"].append(f"{host} was not committed: the run held it.")


def _components(hop, merged):
    found, lines = {}, []
    windows = mismatch_windows(hop, merged)
    for name, marks in hop["components"].items():
        window = windows.get(name)
        if window is None:
            two = marks["split"]
        elif window["open"]:
            two = True
        else:
            two = marks["split"] and not marks["closes"]
        found[name] = {"members": marks["members"], "moving": marks["moving"], "split": marks["split"], "window": window, "two_releases": two}
        if two and window and window["uncommitted"]:
            lines.append(f"{name}'s portal component was left on two releases: {', '.join(window['uncommitted'])} did not commit to {hop['release']}.")
        elif two:
            lines.append(f"{name}'s portal component stays split after the hop to {hop['release']}.")
    return found, lines


def _redacted(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


def hop_report(hop, results, held=(), lock_held=(), check_mode_run=False, peers=None, window=None, prepare_failed=(), lines=()):
    """The hop's report from each host's registered results, their return fields only (FR-021, FR-035)."""
    registered = {host: _registered(results.get(host)) for host in sorted(hop["hosts"])}
    entries = {host: _host_entry(host, found, check_mode_run, set(prepare_failed)) for host, found in registered.items()}
    _held(hop, entries, set(held), set(lock_held))
    components, component_lines = _components(hop, {host: {"phases": entry["phases"]} for host, entry in entries.items()})
    peers = {"commits": list((peers or {}).get("commits") or []), "reboots": list((peers or {}).get("reboots") or [])}
    lines = list(lines)
    if window:
        lines += ([window["message"]] if window.get("message") else []) + list(window.get("lines") or [])
    lines += [reading["line"] for step in ("commits", "reboots") for reading in peers[step] if reading.get("line")]
    for entry in entries.values():
        entry["lines"] = _unique(entry["lines"])
    report = {
        "hop": {"number": hop.get("number"), "release": hop["release"], "lease": hop.get("lease"), "moves": hop.get("moves", [])},
        "check_mode_run": check_mode_run,
        "hosts": entries,
        "components": components,
        "peers": peers,
        "window": window or None,
        "warnings": _unique([{"host": host, "message": w} for host, found in registered.items() for r in found for w in r.get("warnings") or ()]),
        "notes": list(hop.get("notes") or []),
        "lines": _unique(lines + component_lines),
    }
    return _redacted(report)


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    p = module.params
    module.exit_json(
        changed=False,
        report=hop_report(p["hop"], p["results"], p["held"], p["lock_held"], p["check_mode_run"], p["peers"], p["window"], p["prepare_failed"], p["lines"]),
    )


if __name__ == "__main__":
    main()
