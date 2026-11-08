# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The fleet's upgrade plan (FR-010 to FR-019): its hops, the commit schedule, the maintenance window and the hop's
lease."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import OBJECT_BACKED
from ansible_collections.qumulo.cluster.plugins.module_utils.normalize import WEEK, canonical_days
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import redact_text, redact_url
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import with_article
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_path import (
    Refusal,
    align_split,
    host_hops,
    next_quarter,
    parse_images,
    same_release,
    split_at_start,
    the_limit,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_record import STATE_PREFIX, decide_record
from ansible_collections.qumulo.cluster.plugins.module_utils.version import GATES, ClusterVersion

# Verify and prepare answer only after every node's downloads: above the installer's 1800-second budget, and twice that
# on a source below 7.10.3, whose verify downloads the image once more (research R3; QUIRK-093).
REQUEST_TIMEOUT = 2700
OLD_SOURCE_REQUEST_TIMEOUT = 4500
SINGLE_DOWNLOAD_SINCE = (7, 10, 3)
WAIVER_GATE = "upgrade.compatibility_overrides"  # the waiver's form follows the source's release (FR-020)


# The fleet (FR-010 to FR-019)


AGGRESSIVE_HELD = True  # Decision 46: until T1732 has run aggressive reboots live, qumulo_upgrade_aggressive is F11
# (release, date) of the oldest source FR-026's lane runs have upgraded from, for W5; None until those runs set it.
OLDEST_TESTED_SOURCE = None
REPLICATION_QUARTERS = 8  # replication keeps this many quarters of compatibility between its ends (Decision 2)
UNLICENSED = frozenset({"LICENSE_STATE_NONE", "LICENSE_STATE_EXPIRED"})
STARTED_ELSEWHERE = frozenset({"PREPARING", "COMMITTING", "COMMITTED"})  # the states F6 compares the image in
DEFAULT_TIMEOUTS = {"wait": 3600, "reboot": 43200, "unblock": 14400, "peer": 1800}

F11_INVENTORY = (
    "qumulo_upgrade_reboot is aggressive in the inventory for {host}. Aggressive reboots are chosen for one run only, with "
    "-e qumulo_upgrade_aggressive=true; set qumulo_upgrade_reboot to rolling or all."
)
F11_HELD = (
    "Aggressive reboots have not yet run live on a cluster they apply to, so this collection refuses qumulo_upgrade_aggressive. "
    "Run without it, and the reboots run as rolling."
)


def request_timeout(source):
    """verify's and prepare's own timeout, since each answers only after the server's downloads (FR-007)."""
    return REQUEST_TIMEOUT if source.release >= SINGLE_DOWNLOAD_SINCE else OLD_SOURCE_REQUEST_TIMEOUT


def portal_components(hubs):
    """{root hub: [members]} from every inventory host's qumulo_portal.hub, given as {host: hub or None}."""
    components = {}
    for host, hub in hubs.items():
        if hub:
            components.setdefault(_root(hubs, host), {host}).update((host, hub))
    return {root: sorted(members | {root}) for root, members in sorted(components.items())}


def _root(hubs, host):
    seen = set()
    while hubs.get(host) and host not in seen:
        seen.add(host)
        host = hubs[host]
    return host


def _ancestors(hubs, host):
    """The hubs above host, nearest first."""
    found = []
    while hubs.get(host) and hubs[host] not in found and hubs[host] != host:
        host = hubs[host]
        found.append(host)
    return found


def quarters_apart(a, b, named=()):
    """How many quarterly releases lie from the earlier of a and b to the later, by next_quarter.

    Both count as named, so a walk across a major's end always finds the later major and ends.
    """
    low, high = sorted((a.quarter, b.quarter))
    named = (*named, a, b)
    count = 0
    while low < high:
        low = next_quarter(low, named)
        count += 1
    return count


def short_state(status):
    """The upgrade status's state without its UPGRADE_STATE_ prefix, as PREPARED."""
    return ((status or {}).get("state") or "").removeprefix(STATE_PREFIX)


def is_url_or_path(image):
    """An http:// or https:// URL with a host, or an absolute path (F2)."""
    parts = urlsplit(image)
    return (parts.scheme in ("http", "https") and bool(parts.netloc)) or (not parts.scheme and image.startswith("/"))


# The refusals and warnings the plan and the module both give, each worded once, as the spec's tables give them.


def f1_message(host, current, limit):
    return (
        f"{host} runs Qumulo Core {current}, a limited release, and Qumulo Core refuses to upgrade from a limited release unless "
        f"the rule is waived. Contact Qumulo Care. Set qumulo_upgrade_allow_limited_release: true for {host} only when Qumulo "
        f"Care directs it. To upgrade the rest of the fleet meanwhile, {limit}."
    )


def f2_message(host, image):
    return (
        f"{host}: the image {redact_url(image)} is neither an http:// or https:// URL nor an absolute path. Give a URL every "
        "node can reach, or an absolute path in the cluster's file system."
    )


def f6_message(host, state, image, limit):
    """F6 for PREPARING, COMMITTING or COMMITTED with another image, which is printed as given, already redacted."""
    return (
        f"{host} is {state} with the image {image}, started outside this run. Wait for it to finish, then run the upgrade "
        f"again, or, to upgrade the rest of the fleet meanwhile, {limit}."
    )


def f6_auto_commit_message(host, limit):
    return (
        f"{host} is preparing an upgrade that commits itself as soon as it is prepared, outside this run's order. Wait for it to "
        f"finish, then run the upgrade again, or, to upgrade the rest of the fleet meanwhile, {limit}."
    )


def f16_message(host, current, limit):
    return (
        f"{host} runs {current}, below 7.11.0, where Qumulo Core's waiver lifts every compatibility rule, so the collection sends "
        f"it only for a declared build. Declare the build Qumulo Care names for {host}'s first hop as build in that hop's mapping "
        f"entry, in a qumulo_upgrade_images list of {host}'s own. To upgrade the rest of the fleet meanwhile, {limit}."
    )


def w10_message(host, configured, declared, size):
    """W10 for a declaration a platform upgrade ignores, or None: a set size, or rolling where every node reboots at once."""
    if configured == 0:
        if not (size or declared == "rolling"):
            return None
        ignored = f"; its qumulo_upgrade_reboot_set_size of {size} is ignored" if size else ""
        return (
            f"{host}'s configured node fault tolerance is 0, so a platform upgrade of {host} reboots every node at once, whatever "
            f"qumulo_upgrade_reboot says{ignored}."
        )
    if declared == "all" and size:
        return f"qumulo_upgrade_reboot is all for {host}, so its qumulo_upgrade_reboot_set_size of {size} is ignored."
    return None


# A maintenance window range's clock fields, each with its largest value.
RANGE_FIELDS = (("start_hour", 23), ("start_minute", 59), ("end_hour", 23), ("end_minute", 59))

# The inventory settings that hold a host's maintenance windows and their timezone.
WINDOW_SETTINGS = ("qumulo_upgrade_maintenance_windows", "qumulo_upgrade_window_timezone")


def window_refusals(host, ranges, timezone, settings=WINDOW_SETTINGS):
    """F34's messages for a host's maintenance windows and their timezone, each once, naming the two as settings gives them."""
    windows, zone = settings
    found = []
    if ranges and not timezone:
        found.append(f"{host} declares {windows} but no {zone}. Set {zone} for {host} to an IANA name, such as America/Los_Angeles.")
    elif timezone and not _known_timezone(timezone):
        found.append(f"{host} declares {zone} {timezone}, which is no IANA timezone name. Set {zone} for {host} to an IANA name, such as America/Los_Angeles.")
    for r in ranges or ():
        for name, top in RANGE_FIELDS:
            value = r.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
                found.append(
                    f"{host} declares {windows} with {name} {value}, which is no whole number from 0 to "
                    f"{top}. Set each range's {name} for {host} to a whole number from 0 to {top}."
                )
        days = r.get("on_days")
        for day in [days] if isinstance(days, str) or days is None else days:
            if not (isinstance(day, str) and day.upper() in (*WEEK, "EVERY_DAY")):
                found.append(
                    f"{host} declares {windows} with on_days {day}, which is no day name. Name each "
                    f"range's days for {host} from {', '.join(WEEK)} and EVERY_DAY."
                )
    return list(dict.fromkeys(found))


def _named_list(names):
    return ", ".join(names)


@dataclass
class Plan:
    """What fleet_hops gathers: the refusals stop the whole plan; the warnings and notes go to the report."""

    refusals: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def refuse(self, host, code, message):
        self.refusals.append({"host": host, "code": code, "message": redact_text(message)})

    def warn(self, code, host, message):
        self.warnings.append({"code": code, "host": host, "message": redact_text(message)})

    def note(self, host, message):
        self.notes.append({"host": host, "message": redact_text(message)})


class _Fleet:
    """The inventory as fleet_hops reads it: every inventory host, which of them the run reaches, and the play."""

    def __init__(self, hosts, limit):
        self.hosts = {h["name"]: h for h in hosts}
        names = set(self.hosts) if limit is None else set(limit)
        self.reached = {n for n, h in self.hosts.items() if n in names and h.get("reached", True)}
        self.play = {n for n in self.reached if self.hosts[n].get("in_play", True)}
        self.hubs = {n: h.get("portal_hub") or None for n, h in self.hosts.items()}
        self.components = portal_components(self.hubs)
        self.root = {m: root for root, members in self.components.items() for m in members}
        self.targets = {
            n: sorted({e["target"] for e in h.get("relationships") or () if e.get("target") and e.get("state", "present") == "present"})
            for n, h in self.hosts.items()
        }

    def limit(self, host):
        return the_limit(host, self.root.get(host))

    def version(self, host):
        h = self.hosts[host]
        return ClusterVersion.parse(str(h["version"]), str(h.get("build") or ""))


def fleet_hops(hosts, *, limit=None, aggressive=False, timeouts=None, forks=None, throttle=None, now=None, restore_files=None):
    """The upgrade plan of a fleet (FR-010): its hops, each with its schedule and lease, the run's locks, and the records.

    hosts gives every inventory host, each a mapping: name; reached, whether the play reaches it (default true), and
    in_play, whether it is still in the play after its reads (default true); cluster_uuid, version, build, status (the
    upgrade status, its images redacted), protection ({type, configured_node_failures, remaining_node_failures}),
    license_state, unprotected_edge, cluster_kind, live_hub_portals, live_spoke_portals and unavailable_subsets, from
    its reads; target, images (already redacted), reboot, reboot_set_size, maintenance_windows, window_timezone, after
    and allow_limited_release, its qumulo_upgrade_* variables; portal_hub; relationships, its
    qumulo_replication_relationships; and commit_record, {path, text} when a record is on disk. limit names the hosts
    of ansible_limit, or None for the whole inventory. timeouts holds wait, reboot, unblock and peer, the role's
    cluster_upgrade_*_timeout, and forks and throttle are the play's forks and cluster_upgrade_prepare_throttle, for
    each hop's lease and its forks_warning, W6, which the role prints and the plan's warnings leave out. now is the
    run's start, as an aware datetime or an ISO 8601 string, for W11, which is not checked without it.

    restore_files is {records, locks}, as qumulo.cluster.replication_restore_info lists them, for W1 (FR-016).
    """
    fleet, plan = _Fleet(hosts, limit), Plan()
    if aggressive and AGGRESSIVE_HELD:
        plan.refuse(None, "F11", F11_HELD)
    records = _decide_records(fleet, plan)
    _check_declarations(fleet, plan)
    paths = _host_paths(fleet, plan)
    modes = {host: _reboot_mode(fleet, plan, host) for host in sorted(paths) if paths[host].hops}
    _check_reads(fleet, plan, paths)
    output = {"hops": [], "lock_hosts": {}, "records": records, "skipped": [], "refusals": plan.refusals, "warnings": plan.warnings, "notes": plan.notes}
    if plan.refusals:
        return output
    paths = align_split(paths, list(fleet.components.values()))
    hops = _hops(fleet, plan, paths, modes, aggressive and not AGGRESSIVE_HELD)
    if plan.refusals:
        return output
    moved = {h for hop in hops for h in hop["moves"]}
    rebooted = {h for hop in hops for h, entry in hop["hosts"].items() if entry["reboots_only"]}
    output["lock_hosts"] = _lock_hosts(fleet, plan, moved, rebooted)
    _replication_range(fleet, plan, paths, hops)
    _restore_warnings(fleet, plan, moved | rebooted, output["lock_hosts"], restore_files or {})
    timeouts = {**DEFAULT_TIMEOUTS, **(timeouts or {})}
    for hop in hops:
        sources = [paths[h].current for h in hop["moves"]]
        request = max((request_timeout(s) for s in sources), default=request_timeout(ClusterVersion.parse(hop["release"])))
        hop["lease"] = hop_lease(hop, {"request": request, **timeouts}, forks, throttle)
        hop["forks_warning"] = forks_warning(hop, forks, throttle)
        if now is not None and not windows_overlap(hop["windows"], now):
            plan.warn(
                "W11",
                None,
                f"The maintenance windows of {_named_list(sorted(hop['windows']))} share no time, so a hop that moves them all can never "
                "begin its commits. Give them a common range, or upgrade them in separate runs with --limit.",
            )
    output["hops"] = hops
    output["skipped"] = sorted(h for h, p in paths.items() if not p.hops and not p.reboots_only)
    output["warnings"] = _unique(plan.warnings)
    return output


def _unique(entries):
    found = []
    for entry in entries:
        if entry not in found:
            found.append(entry)
    return found


def _decide_records(fleet, plan):
    """FR-034's table over every record of a host the run reaches; a host the limit leaves out keeps its record."""
    records = {"retire": [], "drop": []}
    for host in sorted(fleet.reached):
        record = fleet.hosts[host].get("commit_record")
        if not record:
            continue
        h = fleet.hosts[host]
        found = None
        if host in fleet.play:
            found = {"cluster_uuid": h.get("cluster_uuid"), "version": h.get("version"), "build": h.get("build"), "state": (h.get("status") or {}).get("state")}
        decision = decide_record(host, record["path"], record.get("text"), found, fleet.root.get(host))
        if decision.action == "refuse":
            plan.refuse(host, decision.code, decision.message)
        else:
            records[decision.action].append({"host": host, "path": record["path"], "digest": decision.digest, "line": decision.message})
    return records


def _check_declarations(fleet, plan):
    """The refusals the inventory and the limit decide alone: F11, F13, F14, F34, and F18 for an after naming no host."""
    declared = [n for n in fleet.play if fleet.hosts[n].get("target")]
    for host in sorted(fleet.play):
        h = fleet.hosts[host]
        for name in _as_list(h.get("after")):
            if name not in fleet.hosts:
                plan.refuse(host, "F18", unknown_after(host, name))
        if h.get("reboot") == "aggressive":
            plan.refuse(host, "F11", F11_INVENTORY.format(host=host))
        for message in window_refusals(host, h.get("maintenance_windows"), h.get("window_timezone")):
            plan.refuse(host, "F34", message)
        if declared and not h.get("target"):
            hub = fleet.root.get(host)
            if hub:
                message = (
                    f"{host} declares no qumulo_upgrade_target, while {len(declared)} other hosts of this run do, and it belongs to "
                    f"{hub}'s portal component. Set qumulo_upgrade_target for {host}, or leave the whole component out of the run with --limit."
                )
            else:
                message = (
                    f"{host} declares no qumulo_upgrade_target, while {len(declared)} other hosts of this run do. Set "
                    f"qumulo_upgrade_target for {host}, or leave {host} out of the run with --limit."
                )
            plan.refuse(host, "F14", message)
    for root, members in fleet.components.items():
        outside = [m for m in members if m not in fleet.play]
        if outside and len(outside) < len(members):
            reasons = [f"{m} ({_outside_reason(fleet, m)})" for m in outside]
            plan.refuse(
                root,
                "F13",
                f"{root}'s portal component has members outside this run: {_named_list(reasons)}. Add them to the limit, or fix what "
                "stopped their read; members already at the target are skipped.",
            )


def _outside_reason(fleet, host):
    if host not in fleet.hosts:
        return "no inventory host"
    if host not in fleet.reached:
        return "left out by the limit"
    return "its read failed"


def _host_paths(fleet, plan):
    """Each host's own hops to its target (Decision 42): F3, F4, F10, F15 and the images list's own refusals."""
    parsed = {}
    for host in sorted(fleet.play):
        h = fleet.hosts[host]
        if not h.get("target"):
            continue
        try:
            parsed[host] = (fleet.version(host), parse_images(h.get("images")), ClusterVersion.parse(str(h["target"])))
        except Refusal as e:
            plan.refuse(host, e.code, str(e))
        except QumuloError as e:
            plan.refuse(host, "target", f"{host}: {e}")
    named = [r for current, entries, target in parsed.values() for r in (current, target, *(i.release for i in entries))]
    paths = {}
    for host, (current, entries, target) in parsed.items():
        h = fleet.hosts[host]
        try:
            paths[host] = host_hops(
                host,
                current,
                entries,
                target,
                cluster_kind=h.get("cluster_kind"),
                protection_type=(h.get("protection") or {}).get("type"),
                rebooting=short_state(h.get("status")) == "REBOOTING",
                hub=fleet.root.get(host),
                named=named,
            )
        except Refusal as e:
            plan.refuse(host, e.code, str(e))
    return paths


def _check_reads(fleet, plan, paths):
    """The refusals and warnings from each moving host's reads: F1, F2, F6, F16, W2, W4, W5 and W7."""
    for host in sorted(fleet.play):
        h, path = fleet.hosts[host], paths.get(host)
        _portal_counts(fleet, plan, host)
        if path is None or not path.hops:
            continue
        current, limit, first = path.current, fleet.limit(host), path.hops[0]
        if current.respin and not h.get("allow_limited_release"):
            plan.refuse(host, "F1", f1_message(host, current, limit))
        elif current.respin and current.release < GATES[WAIVER_GATE].since and not first.build:
            plan.refuse(host, "F16", f16_message(host, current, limit))
        for image in sorted({hop.image for hop in path.hops}):
            if not is_url_or_path(image):
                plan.refuse(host, "F2", f2_message(host, image))
        _started_elsewhere(plan, host, h.get("status") or {}, first, limit)
        if h.get("license_state") in UNLICENSED and (h.get("unprotected_edge") or h.get("cluster_kind") == "edge"):
            plan.warn(
                "W2",
                host,
                f"{host} reads license {h['license_state']}. Qumulo Core refuses to upgrade an unlicensed edge formed more than 30 days "
                "ago, and then its portal component waits for it. License it with ansible-playbook "
                f"qumulo.cluster.edge_license_complete -i <inventory> -l {host} before the run.",
            )
        if OLDEST_TESTED_SOURCE:
            oldest, date = OLDEST_TESTED_SOURCE
            if current < ClusterVersion.parse(oldest):
                plan.warn("W5", host, f"{host} runs {current}, older than {oldest}, the oldest release the collection's tests have upgraded from ({date}).")


def _started_elsewhere(plan, host, status, first, limit):
    """F6, from the redacted images; an IDLE status's settings and error are an earlier attempt's, and only a note."""
    state, settings = short_state(status), status.get("settings") or {}
    if state == "PREPARING" and settings.get("auto_commit"):
        plan.refuse(host, "F6", f6_auto_commit_message(host, limit))
    elif state in STARTED_ELSEWHERE and redact_url(settings.get("install_path")) != redact_url(first.image):
        plan.refuse(host, "F6", f6_message(host, state, redact_url(settings.get("install_path")), limit))
    elif state == "IDLE" and status.get("error_info"):
        plan.note(
            host,
            f"{host} reads IDLE with an earlier attempt's settings and error, as a failed Web UI or qq upgrade leaves them: "
            f"{status['error_info']}. This run prepares it afresh.",
        )


def _portal_counts(fleet, plan, host):
    """W4 and W7: live portals the inventory does not declare, skipped where the read could not list portals."""
    h = fleet.hosts[host]
    if "portals" in (h.get("unavailable_subsets") or ()):
        return
    spokes = sum(1 for hub in fleet.hubs.values() if hub == host)
    if (h.get("live_hub_portals") or 0) > spokes:
        plan.warn(
            "W4",
            host,
            f"{host} has {h['live_hub_portals']} live hub portals, and the inventory declares {spokes} spokes for it. A spoke the "
            "inventory does not declare is not ordered before its hub, and its portal stays down until it matches.",
        )
    declared = 1 if fleet.hubs.get(host) else 0
    if (h.get("live_spoke_portals") or 0) > declared:
        plan.warn(
            "W7",
            host,
            f"{host} has {h['live_spoke_portals']} live spoke portals, and the inventory declares {declared}. Its hub is not ordered "
            "after it, and the portal stays down until the two match.",
        )


def _reboot_mode(fleet, plan, host):
    """The host's reboot mode without aggressive: F20 for a rolling prepare's count, W10 for a declaration ignored."""
    h = fleet.hosts[host]
    configured = (h.get("protection") or {}).get("configured_node_failures")
    declared, size = h.get("reboot"), h.get("reboot_set_size")
    warning = w10_message(host, configured, declared, size)
    if warning:
        plan.warn("W10", host, warning)
    if configured == 0:
        if not warning and declared != "all":
            plan.note(host, f"{host} reboots every node at once on a platform upgrade, since its configured node fault tolerance is 0.")
        return "all"
    if declared == "all":
        return "all"
    if size and configured is not None and size > configured:
        plan.refuse(
            host,
            "F20",
            f"{host}: qumulo_upgrade_reboot_set_size is {size}, above {host}'s configured node fault tolerance of {configured}, which "
            f"Qumulo Core would refuse at the prepare. Set it to {configured} or lower for {host}, or leave it unset so the server "
            "sizes the groups.",
        )
    return "rolling"


def aggressive_scope(host, protection):
    """None when aggressive reboots apply to a host's hop as far as the plan can tell, or W8's reason they do not.

    The upgrade type, the first condition, is read only once the prepare reads PREPARING, so the module checks it.
    """
    protection = protection or {}
    if protection.get("type") in OBJECT_BACKED:
        return f"{host} is object-backed"
    configured = protection.get("configured_node_failures")
    if configured is None or configured < 2:
        return f"{host}'s configured node fault tolerance is {configured}, below 2"
    return None


def _hops(fleet, plan, paths, modes, aggressive):
    """The fleet's hops in lock-step (Decision 44), each with its components' marks, its schedule and its hosts; F12, F18."""
    releases = []
    for path in paths.values():
        for release in [hop.release for hop in path.hops] + ([path.target] if path.reboots_only else []):
            if not any(same_release(release, r) for r in releases):
                releases.append(release)
    position = {host: path.current for host, path in paths.items()}
    taken = dict.fromkeys(paths, 0)
    after = {host: _as_list(fleet.hosts[host].get("after")) for host in fleet.play}
    replication = [(s, t) for s in sorted(fleet.play) for t in fleet.targets[s]]
    hops, refused = [], set()
    for number, release in enumerate(sorted(releases), start=1):
        moving = sorted(h for h, p in paths.items() if taken[h] < len(p.hops) and same_release(p.hops[taken[h]].release, release))
        start = dict(position)
        entries = {h: paths[h].hops[taken[h]] for h in moving}
        for h in moving:
            position[h] = entries[h].release
            taken[h] += 1
        notes = []
        components = _component_marks(fleet, plan, release, moving, start, position, notes)
        schedule = commit_schedule(moving, fleet.hubs, after, replication, inventory=set(fleet.hosts), release=release)
        for host, message in schedule.refusals:
            if message not in refused:
                refused.add(message)
                plan.refuse(host, "F18", message)
        notes += schedule.notes
        notes += [
            f"{h} does not move in the hop to {release}; its next hop is {paths[h].hops[taken[h]].release}."
            for h in sorted(paths)
            if h not in moving and taken[h] < len(paths[h].hops)
        ]
        hosts = {}
        for host in moving:
            mode = modes[host]
            if aggressive:
                reason = aggressive_scope(host, fleet.hosts[host].get("protection"))
                if reason is None:
                    mode = "aggressive"
                else:
                    plan.warn("W8", host, f"Aggressive reboots do not apply to {host}'s hop to {release}: {reason}. Its reboots run as {mode}.")
            hosts[host] = _hop_host(entries[host], paths[host].kind, mode, False, schedule.hosts[host])
        for host in sorted(h for h, p in paths.items() if p.reboots_only and same_release(p.target, release)):
            hosts[host] = _hop_host(paths[host].last, paths[host].kind, _reboot_mode(fleet, Plan(), host), True, None, version=str(release))
        windows = {
            h: {"ranges": fleet.hosts[h]["maintenance_windows"], "timezone": fleet.hosts[h].get("window_timezone")}
            for h in moving
            if fleet.hosts[h].get("maintenance_windows")
        }
        hops.append(
            {
                "number": number,
                "release": str(release),
                "lease": None,
                "forks_warning": None,
                "moves": moving,
                "components": components,
                "steps": schedule.steps,
                "units": schedule.units,
                "hosts": dict(sorted(hosts.items())),
                "windows": windows,
                "notes": notes,
            }
        )
    return hops


def _as_list(value):
    return [value] if isinstance(value, str) else list(value or ())


def _hop_host(image, kind, mode, reboots_only, placed, version=None):
    """A host's entry in a hop; a reboots_only host whose list holds no target entry has none, only the target's version."""
    placed = placed or {"unit": None, "round": None, "wave": None, "step": None, "holds": []}
    return {
        "image_index": image.index if image else None,
        "image_redacted": redact_url(image.image) if image else None,
        "version": str(image.release) if image else version,
        "build": image.build if image else None,
        "kind": image.kind if image else kind,
        "reboot": mode,
        **placed,
        "reboots_only": reboots_only,
    }


def _component_marks(fleet, plan, release, moving, start, end, notes):
    """split and closes for each component the hop moves (FR-017); F12 for a component the hop would split."""
    marks = {}
    for root, members in fleet.components.items():
        movers = [m for m in members if m in moving]
        if not movers:
            continue
        known = [m for m in members if m in start]
        split = split_at_start(start, known)
        closes = not split_at_start(end, known)
        if split:
            notes.append(f"{root}'s portal component starts the hop to {release} split: " + _named_list(f"{m} on {start[m]}" for m in known) + ".")
        elif not closes:
            plan.refuse(
                root,
                "F12",
                f"{root}'s portal component would end the hop to {release} on two releases: "
                + _named_list(f"{m} on {end[m]}" for m in known)
                + ". Give every member of the component an image of the same release for this hop, and the same qumulo_upgrade_target.",
            )
        marks[root] = {"members": members, "moving": movers, "split": split, "closes": split and closes}
    return marks


def _lock_hosts(fleet, plan, moved, rebooted):
    """FR-031's locks for the whole run, each with the hosts it protects; W9 and W12 for what no lock of the run holds."""
    touched = moved | rebooted
    locks = set(touched) | {s for s in fleet.play if any(t in touched for t in fleet.targets[s])}
    for source in sorted(set(fleet.hosts) - fleet.play):
        for target in fleet.targets[source]:
            if target in touched:
                plan.warn(
                    "W9",
                    target,
                    f"{source}, outside this run, replicates to {target}, which this run upgrades. The run cannot hold {source}'s restore "
                    f"lock, so a restore of {source} started meanwhile is not held off, and the upgrade would restart {target} between two "
                    "of its steps.",
                )
    for source in sorted(moved):
        for target in fleet.targets[source]:
            if target in fleet.hosts and target not in fleet.play:
                plan.warn(
                    "W12",
                    source,
                    f"{source} replicates to {target}, which this run leaves out, so {source} may upgrade ahead of its target and hold new "
                    f"kinds of state back from it until each file changes again. Add {target} to the limit, or upgrade it first.",
                )
    return {host: sorted({host, *fleet.targets[host]}) for host in sorted(locks)}


def _restore_warnings(fleet, plan, touched, lock_hosts, restore_files):
    """W1 for each open restore record, and each live restore lock on a host outside lock_hosts, once for each host
    the plan moves or reboots among those it names (FR-016, Decision 48); a lock on a lock host is the lock step's
    (F22).

    A record names its edge, its key's target host and the edge's present targets, since the key's hash leaves the
    target host out, so a record outlives a rename of it, and the restore acts on the inventory's target. A lock names
    its host and that host's present targets.
    """
    for record in restore_files.get("records") or ():
        edge, key = record["host"], record["key"]
        named = {edge, *fleet.targets.get(edge, ()), *([key["target_host"]] if key.get("target_host") else [])}
        for host in sorted(named & touched):
            plan.warn(
                "W1",
                host,
                f"{host}: an open restore record, {record['path']}, shows that the restore of {key['source_path']} on {edge} has not finished. "
                f"The upgrade goes on, and restarts {host} between two of the restore's steps. Afterwards, run ansible-playbook "
                f"qumulo.cluster.replication_restore -i <inventory> -l {edge}, which finishes the restore and removes the record. If the "
                "relationship was reconnected by hand, remove the record.",
            )
    for lock in restore_files.get("locks") or ():
        edge = lock["host"]
        if lock["liveness"] != "live" or edge in lock_hosts:
            continue
        for host in sorted({edge, *fleet.targets.get(edge, ())} & touched):
            held = (
                f"{host}: a live restore lock, {lock['path']}, shows {'another upgrade' if lock['holder'] == 'upgrade' else with_article(lock['holder'])} "
                f"of {edge} under way, by ansible-playbook process {lock['pid']} on {lock['controller']}, with a lease until {lock['lease_expiry']}."
            )
            if lock["holder"] == "upgrade":
                # An upgrade has no restore steps, and a stopped one leaves a commit record (FR-034), not a restore record.
                then = f" This run upgrades {host} meanwhile."
            else:
                then = (
                    f" The upgrade goes on, and may restart {host} between two of the restore's steps. Afterwards, if that run stopped, run "
                    f"ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l {edge}, which finishes the restore and removes the record."
                )
            plan.warn("W1", host, held + then)


def _replication_range(fleet, plan, paths, hops):
    """W3, once for each present relationship whose ends lie more than eight quarters apart before the run or after a hop."""
    position = {h: p.current for h, p in paths.items()}
    named = [r for p in paths.values() for r in p.named]
    moments = [("before the run", dict(position))]
    for hop in hops:
        for host in hop["moves"]:
            position[host] = ClusterVersion.parse(hop["hosts"][host]["version"])
        moments.append((f"after the hop to {hop['release']}", dict(position)))
    for source in sorted(fleet.play):
        for target in fleet.targets[source]:
            for when, at in moments:
                if source in at and target in at and (apart := quarters_apart(at[source], at[target], named)) > REPLICATION_QUARTERS:
                    plan.warn(
                        "W3",
                        source,
                        f"{source} replicates to {target}, which would be {apart} quarterly releases apart {when}. Replication refuses "
                        "clusters about eight quarters apart.",
                    )
                    break


# The commit schedule (FR-015)


@dataclass(frozen=True)
class Schedule:
    units: list  # [{name, round, waves, follows, reason}], by round and then name
    steps: list  # [{round, wave, hosts}], wave None for a round's standalone step
    hosts: dict  # {host: {unit, round, wave, step, holds}}
    notes: list
    refusals: list  # [(host, F18's message)]


def commit_schedule(moving, hubs, after=None, replication=(), *, inventory=None, release=None):
    """One hop's units, their order, their rounds and their steps.

    moving is the hosts the hop moves; hubs is {host: qumulo_portal.hub or None} for every inventory host, and
    inventory their names when it holds more; after is {host: qumulo_upgrade_after}; replication is the (source,
    target) pairs of the present relationships.
    """
    moving, after, inventory = set(moving), after or {}, set(hubs) if inventory is None else set(inventory)
    root = {m: r for r, members in portal_components(hubs).items() for m in members}
    spokes = {}
    for host, hub in hubs.items():
        if hub:
            spokes.setdefault(hub, []).append(host)
    members = {}
    for host in sorted(moving):
        members.setdefault(root.get(host, host), []).append(host)
    unit_of = {host: name for name, hosts in members.items() for host in hosts}
    waves = {name: _waves(hosts, spokes) if name in root else [hosts] for name, hosts in members.items()}
    edges = {name: {} for name in members}  # {before: {after: why}}
    refusals, notes, reasons = [], [], {name: [] for name in members}
    for host in sorted(moving):
        for name in after.get(host) or []:
            _hard_edge(host, name, moving, hubs, root, unit_of, inventory, edges, refusals, notes, release)
    for source, target in sorted(set(replication)):
        if source not in moving or target not in moving or unit_of[source] == unit_of[target]:
            continue
        first, then = unit_of[target], unit_of[source]
        if then in edges[first]:
            continue
        cycle = _path(edges, then, first)
        if cycle is None:
            edges[first][then] = ("replication", source, target)
            continue
        declared = sorted({edge[2][1] for edge in cycle if edge[2][0] == "after"})
        if len(declared) == len(cycle):
            notes.append(
                f"{source} replicates to {target}, but qumulo_upgrade_after on {_named_list(declared)} orders {target} after {source}: the "
                "declaration drops the replication preference for its target first."
            )
        else:
            notes.append(f"{source} replicates to {target}, but the order already kept puts {target} after {source}, so this replication edge is dropped.")
    before = {name: {b for b, nexts in edges.items() if name in nexts} for name in members}
    rounds = {}
    for name in sorted(members):
        _round(name, before, rounds)
    follows = {name: _closure(name, before) for name in members}
    for first, nexts in edges.items():
        for then, why in nexts.items():
            if why[0] == "after":
                reasons[then].append(f"after {first}, by qumulo_upgrade_after on {why[1]}")
            else:
                reasons[first].append(f"a replication target before its source, {then}")
                reasons[then].append(f"after {first}, which it replicates to")
    units, steps, placed = [], [], {}
    for number in sorted(set(rounds.values())):
        names = sorted(n for n in members if rounds[n] == number)
        joined = [n for n in names if n in root]
        for wave in range(max((len(waves[n]) for n in joined), default=0)):
            steps.append({"round": number, "wave": wave, "hosts": sorted(h for n in joined if wave < len(waves[n]) for h in waves[n][wave])})
            for host in steps[-1]["hosts"]:
                placed[host] = (number, wave, len(steps) - 1)
        alone = [n for n in names if n not in root]
        if alone:
            steps.append({"round": number, "wave": None, "hosts": sorted(h for n in alone for h in members[n])})
            for host in steps[-1]["hosts"]:
                placed[host] = (number, None, len(steps) - 1)
        for name in names:
            kind = (
                ["spokes together before their hub"]
                if len(waves[name]) > 1
                else [f"{name}'s portal component"]
                if name in root
                else ["a cluster in no portal component the hop moves"]
            )
            units.append({"name": name, "round": number, "waves": waves[name], "follows": sorted(follows[name]), "reason": "; ".join(kind + reasons[name])})
    hosts = {}
    for host in sorted(moving):
        name = unit_of[host]
        held = sorted(h for n in members if n == name or name in follows[n] for h in members[n])
        number, wave, step = placed[host]
        hosts[host] = {"unit": name, "round": number, "wave": wave, "step": step, "holds": held}
    return Schedule(units, steps, hosts, notes, refusals)


def _waves(hosts, spokes):
    """A component's moving members by height: wave 0 has no moving spoke below it, and the root hub is last (Decision 22)."""
    moving, heights = set(hosts), {}

    def below(host, seen):
        found = -1
        for spoke in spokes.get(host, ()):
            if spoke not in seen:
                found = max(found, height(spoke, seen | {spoke}) if spoke in moving else below(spoke, seen | {spoke}))
        return found

    def height(host, seen):
        if host not in heights:
            heights[host] = below(host, seen) + 1
        return heights[host]

    for host in hosts:
        height(host, {host})
    return [sorted(h for h in hosts if heights[h] == level) for level in range(max(heights.values()) + 1)]


def unknown_after(host, name):
    """F18's message for an after naming no inventory host."""
    return f"qumulo_upgrade_after on {host} names {name}, which is no inventory host. Change qumulo_upgrade_after on {host}."


def _hard_edge(host, name, moving, hubs, root, unit_of, inventory, edges, refusals, notes, release):
    declaration = f"qumulo_upgrade_after on {host}"
    if name not in inventory:
        refusals.append((host, unknown_after(host, name)))
    elif name in _ancestors(hubs, host):
        refusals.append((host, f"{declaration} puts {name} before its spoke {host}. Change qumulo_upgrade_after on {host}."))
    elif root.get(name) is not None and root.get(name) == root.get(host):
        refusals.append(
            (
                host,
                (
                    f"{declaration} orders {host} and {name}, members of one portal component, whose order the portal graph sets. "
                    f"Change qumulo_upgrade_after on {host}."
                ),
            )
        )
    elif name not in moving:
        notes.append(f"{declaration} names {name}, which the hop to {release} does not move, so it orders nothing in that hop.")
    else:
        first, then = unit_of[name], unit_of[host]
        cycle = _path(edges, then, first)
        if cycle is not None:
            hosts = sorted({host, *(edge[2][1] for edge in cycle)})
            refusals.append((host, f"{declaration} closes a cycle, {_named_list(hosts)}. Change qumulo_upgrade_after on {_named_list(hosts)}."))
        else:
            edges[first].setdefault(then, ("after", host))


def _path(edges, start, goal):
    """The edges of a path from start to goal, [(from, to, why)], or None; a unit reaches itself by no edge."""
    if start == goal:
        return []
    trails, seen = [(start, [])], {start}
    while trails:
        node, trail = trails.pop(0)
        for nxt, why in sorted(edges[node].items()):
            if nxt not in seen:
                if nxt == goal:
                    return [*trail, (node, nxt, why)]
                seen.add(nxt)
                trails.append((nxt, [*trail, (node, nxt, why)]))
    return None


def _round(name, before, rounds):
    if name not in rounds:
        rounds[name] = 1 + max((_round(b, before, rounds) for b in before[name]), default=0)
    return rounds[name]


def _closure(name, before):
    found, pending = set(), list(before[name])
    while pending:
        unit = pending.pop()
        if unit not in found:
            found.add(unit)
            pending.extend(before[unit])
    return found


# The maintenance window (FR-017)


def _instant(now):
    """now as an aware UTC datetime, from a datetime or an ISO 8601 string such as 2026-11-04T05:00:00Z."""
    if isinstance(now, str):
        now = datetime.fromisoformat(now)
    return (now if now.tzinfo else now.replace(tzinfo=UTC)).astimezone(UTC)


def _known_timezone(name):
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _openings(ranges, timezone, around, days=8):
    """The ranges' times as UTC (open, close) pairs, over the days from the one before around to days after it."""
    zone, spans = ZoneInfo(timezone), []
    local = around.astimezone(zone).date()
    for offset in range(-1, days + 1):
        day = local + timedelta(days=offset)
        name = WEEK[(day.weekday() + 1) % 7]  # WEEK starts on Sunday, and weekday() on Monday
        for r in ranges:
            days_named = [r["on_days"]] if isinstance(r["on_days"], str) else r["on_days"]
            if name not in canonical_days([d.upper() for d in days_named]):
                continue
            start = datetime.combine(day, time(r["start_hour"], r["start_minute"]))
            minutes = (r["end_hour"] * 60 + r["end_minute"] - r["start_hour"] * 60 - r["start_minute"]) % 1440 or 1440
            spans.append((start.replace(tzinfo=zone).astimezone(UTC), (start + timedelta(minutes=minutes)).replace(tzinfo=zone).astimezone(UTC)))
    return _merge(spans)


def _merge(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _intersect(a, b):
    found, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        start, end = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if start < end:
            found.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return found


def window_covers(ranges, timezone, now):
    """Whether now falls in one of the ranges, in timezone; a host that declares no ranges is inside at every time.

    A range whose end is at or before its start runs past midnight, and its on_days names the day it starts.
    """
    if not ranges:
        return True
    now = _instant(now)
    return any(start <= now < end for start, end in _openings(ranges, timezone, now))


def _common(windows, now):
    """The UTC times, over the coming week, at which every host of windows, {host: {ranges, timezone}}, is inside."""
    common = None
    for window in windows.values():
        if window.get("ranges"):
            spans = _openings(window["ranges"], window["timezone"], now)
            common = spans if common is None else _intersect(common, spans)
    return common


def windows_overlap(windows, now):
    """Whether the hosts that declare ranges, of {host: {ranges, timezone}}, share any time in the week from now (W11)."""
    common = _common(windows, _instant(now))
    return common is None or bool(common)


def window_form(hop, held, now):
    """The window step's answer for a hop (FR-017's table): {declared, inside, next_open, outside_hosts, held, lines, message}.

    hop is the plan's hop; held is the role's upgrade_held, whose hosts do not decide the clock. held in the answer is
    the hosts the window holds, and message is F9's when it holds any.
    """
    now, held = _instant(now), set(held or ())
    deciding = {h: w for h, w in hop["windows"].items() if h in hop["moves"] and h not in held}
    answer = {"declared": bool(deciding), "inside": True, "next_open": None, "outside_hosts": [], "held": [], "lines": [], "message": None}
    outside = sorted(h for h, w in deciding.items() if not window_covers(w["ranges"], w["timezone"], now))
    if not outside:
        return answer
    units = sorted(hop["units"], key=lambda u: (u["round"], u["name"]))
    stopped, lines, windowed = {u["name"] for u in units if any(h in held for wave in u["waves"] for h in wave)}, [], []
    for unit in units:
        name, hosts = unit["name"], [h for wave in unit["waves"] for h in wave]
        marks = hop["components"].get(name)
        if name in stopped:
            continue
        if marks and marks["split"] and marks["closes"]:
            followed = [f for f in unit["follows"] if f in stopped]
            if followed:
                lines.append(f"{name}'s portal component is held, though the hop would close its split: it follows {_named_list(followed)}, which is held.")
            else:
                lines += [f"{h} commits outside the maintenance window, to close the split of {name}'s portal component." for h in hosts]
                continue
        elif marks and marks["split"]:
            lines.append(
                f"{name}'s portal component is held by the maintenance window, and stays split: the hop to {hop['release']} would not close its split."
            )
        stopped.add(name)
        windowed += hosts
    opening = _next_open(deciding, outside, now)
    zone = hop["windows"][outside[0]]["timezone"]
    shown = opening.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M") if opening else None
    answer.update(
        inside=False,
        next_open=opening.strftime("%Y-%m-%dT%H:%M:%SZ") if opening else None,
        outside_hosts=outside,
        held=sorted(windowed),
        lines=lines,
        message=(
            f"{_named_list(sorted(windowed))} were not committed: the hop to {hop['release']} is outside their maintenance window, which "
            f"next opens {shown} {zone}. They stay prepared. Run the upgrade again inside the window."
        )
        if windowed
        else None,
    )
    return answer


def _next_open(deciding, outside, now):
    """When every deciding host is next inside; when their ranges share no time (W11), when an outside host's next opens."""
    common = _common(deciding, now) or []
    later = [span[0] for span in common if span[0] > now]
    if later:
        return later[0]
    own = [span[0] for h in outside for span in _openings(deciding[h]["ranges"], deciding[h]["timezone"], now) if span[0] > now]
    return min(own, default=None)


# The hop's numbers (FR-031, FR-035)


def mismatch_windows(hop, results):
    """Each moved component's mismatch window, from its first commit's start to its last member's commit done (FR-035).

    results is {host: the upgrade module's result}, whose phases hold {started, ended} in UTC for each phase run; the
    commit's is phases.commit, ended at its done. A component some of whose moving members have no commit done is left
    on two releases: its window is open, and uncommitted names those members.
    """
    windows = {}
    for name, marks in hop["components"].items():
        commits = {h: ((results.get(h) or {}).get("phases") or {}).get("commit") or {} for h in marks["moving"]}
        started = [_instant(c["started"]) for c in commits.values() if c.get("started")]
        if not started:
            continue
        uncommitted = sorted(h for h, c in commits.items() if not c.get("ended"))
        ended = None if uncommitted else max(_instant(c["ended"]) for c in commits.values())
        windows[name] = {
            "started": min(started).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended": ended.strftime("%Y-%m-%dT%H:%M:%SZ") if ended else None,
            "seconds": int((ended - min(started)).total_seconds()) if ended else None,
            "open": bool(uncommitted),
            "uncommitted": uncommitted,
        }
    return windows


def _batches(count, width):
    return math.ceil(count / width) if width and count else min(count, 1)


def hop_lease(hop, timeouts, forks=None, throttle=None):
    """The hop's bound in seconds, the restore lock's lease for it (FR-031).

    timeouts holds wait, reboot, unblock and peer, the role's cluster_upgrade_*_timeout, and request, the prepare's own
    request timeout. Each step's part counts the batches forks makes of its hosts; step 2 and step 3's prepare-again
    task count theirs from the smaller of forks and throttle.
    """
    wait, reboot, request = timeouts["wait"], timeouts["reboot"], timeouts["request"]
    moved = len(hop["moves"])
    prepares = min(w for w in (forks, throttle) if w) if forks or throttle else None
    # A prepare: its request, the wait for PREPARED, the lost answer's read-back, the wait for a count's tolerance, and
    # the wait after a 409 "still rebooting".
    prepare = request + wait + request + wait + reboot
    lease = (reboot + timeouts["unblock"]) * _batches(moved, forks) + 2 * prepare * _batches(moved, prepares) + wait * _batches(moved, forks)
    split = {h for marks in hop["components"].values() if marks["split"] for h in marks["moving"]}
    for step in hop["steps"]:
        prepares_again = (step["wave"] or 0) > 0 or any(h in split for h in step["hosts"])
        lease += (wait + (request + wait if prepares_again else 0) + wait) * _batches(len(step["hosts"]), forks)
    return lease + 2 * timeouts["peer"] + reboot


def forks_warning(hop, forks, throttle=None):
    """W6 for the role, or None: forks, or the prepare throttle, below the hop's size or its largest commit step."""
    moved, largest = len(hop["moves"]), max((len(s["hosts"]) for s in hop["steps"]), default=0)
    if throttle and (not forks or throttle < forks):
        name, width = "cluster_upgrade_prepare_throttle", throttle
    else:
        name, width = "forks", forks
    if not width or (width >= moved and (not forks or forks >= largest)):
        return None
    return (
        f"{name} is {width}, below this hop's {moved} clusters or its largest commit step of {largest}. Prepares and a step's "
        f"commits run in batches, which lengthens the hop and its components' mismatch windows. Raise {name} to at least "
        f"{max(moved, largest)}."
    )
