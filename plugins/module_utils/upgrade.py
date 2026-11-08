# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Upgrades: the routes, cluster_info's reads of the upgrade status, the preflight and the tolerances, and the upgrade
module's action, which takes one cluster through one hop."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from ansible_collections.qumulo.cluster.plugins.module_utils.client import (
    QumuloApiError,
    QumuloError,
    QumuloMaybeApplied,
    QumuloUnreachable,
    Route,
    register_secret,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import NODES, OBJECT_BACKED, PROTECTION, Readiness, node_state
from ansible_collections.qumulo.cluster.plugins.module_utils.polling import UNANSWERED, Waits
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import _parts, join_url, redact_text, redact_url, url_secret
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Action
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_fleet import (
    STARTED_ELSEWHERE,
    WAIVER_GATE,
    f1_message,
    f2_message,
    f6_auto_commit_message,
    f6_message,
    f16_message,
    is_url_or_path,
    request_timeout,
    short_state,
    w10_message,
    window_form,
    window_refusals,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_path import ON_PREM, Image, Refusal, host_hops, parse_images, same_release, the_limit
from ansible_collections.qumulo.cluster.plugins.module_utils.upgrade_record import (
    PENDING,
    STATE_PREFIX,
    UNFINISHED,
    decide_record,
    remove_record,
    unreadable_record,
    write_record,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.version import VERSION, ClusterVersion, cluster_version, supports

# The routes (research R1). Verify, prepare and commit are never sent again after a lost answer (G-UPGRADE-ONESHOT):
# the upgrade module reads back instead, and sends one more commit only when it finds the cluster still PREPARED with
# the REST API answering throughout, since the server refuses a second commit while the first runs. That is the
# collection's one write-again exception, and it lives in the module, not in a route's replay.
STATUS = Route("/v3/upgrade/status", ("GET",))  # never /v2/upgrade/status, which reads REBOOTING as IDLE (QUIRK-090)
BLOCKED = Route("/v2/upgrade/blocked", ("GET",))
VERIFY = Route("/v2/upgrade/verify-image", ("POST",))
PREPARE = Route("/v2/upgrade/prepare", ("POST",))
COMMIT = Route("/v2/upgrade/commit", ("POST",))
REBOOT_STATUS = Route("/v1/shutdown/reboot/status", ("GET",))


# Reads


def configured_node_failures(protection, node_count):
    """The configured node tolerance (Decision 29): max_node_failures on an erasure-coded cluster; on an object-backed one,
    whose max_node_failures describes a stripe it does not use, the node-count rule, 0 below three nodes and 1 from three.
    """
    if protection.get("protection_system_type") in OBJECT_BACKED:
        return 0 if node_count < 3 else 1
    return protection.get("max_node_failures")


def redact_status(status, *urls):
    """The upgrade status with its install_path and error_info redacted (FR-032)."""
    if not isinstance(status, dict):
        return status
    found = dict(status)
    if isinstance(found.get("settings"), dict):
        found["settings"] = {**found["settings"], "install_path": redact_text(found["settings"].get("install_path"), *urls)}
    found["error_info"] = redact_text(found.get("error_info"), *urls)
    return found


def blocked_facts(client):
    """cluster_info's upgrade_blocked subset: the preflight alone, which reads nothing FR-025a keeps to a reboot."""
    return client.get(BLOCKED).data


def upgrade_facts(client):
    """cluster_info's upgrade subset: the status, the preflight, the reboot status and the tolerances.

    The protection subset hides an object-backed cluster's tolerances, which describe erasure coding it does not use; the
    reboot manager checks remaining_node_failures on every cluster type, so the upgrade reads it here.
    """
    status = client.get(STATUS).data
    blocked = client.get(BLOCKED).data
    reboot = client.get(REBOOT_STATUS).data
    protection = client.get(PROTECTION).data
    kind = protection.get("protection_system_type")
    node_count = len(client.get(NODES).data) if kind in OBJECT_BACKED else None
    return {
        "status": redact_status(status),
        "blocked": blocked,
        "reboot": reboot,
        "protection": {
            "type": kind,
            "configured_node_failures": configured_node_failures(protection, node_count),
            "remaining_node_failures": protection.get("remaining_node_failures"),
        },
    }


# The upgrade module (FR-001 to FR-009a)

PLATFORM = "SOFTWARE_AND_PLATFORM"
REBOOT_IDLE = "REBOOT_IDLE"
REBOOT_IN_PROGRESS = "REBOOT_IN_PROGRESS"
REBOOT_PAUSED = "REBOOT_PAUSED"
REBOOT_PAUSED_DUE_TO_ERROR = "REBOOT_PAUSED_DUE_TO_ERROR"
REBOOT_POLL = 30.0  # each reboot status read asks every node for its boot identity (FR-025a)
POLL_SECONDS = 5.0  # the least time the file system's read after the commit is given
LIMITED_RELEASE_RULE = "allow_upgrade_from_limited_release"
NAMED_RULE_LIFTS = ("a limited release",)
BOOLEAN_LIFTS = (
    "a prohibited release",
    "a limited release",
    "a developer source",
    "a source off a release branch",
    "a downgrade",
    "the upgrade window",
    "a developer target build",
    "a build not later than the running one",
    "the license check",
)
INSTALLER_SCRIPT = "install/verify.py"  # the installer verify and prepare run; the container's image has none (QUIRK-092)
VERIFY_PREFIX = "The provided upgrade is invalid: "  # prepare's 400 around verify's own refusal (QUIRK-094)
STILL_REBOOTING = "Cluster is still rebooting from previous upgrade"
COUNT_REFUSAL = "Rolling reboot must reboot"
# How the prepare was answered, which decides how its status is read back (FR-007).
ACCEPTED, LOST_ANSWER, IN_PROGRESS = "accepted", "lost answer", "in progress"
UNREACHED = re.compile(r"Malformed qimg|timed? ?out|timeout", re.IGNORECASE)  # how a URL no node reached fails (QUIRK-095)
# A refusal that a second prepare cannot lift, which the role holds its unit on (FR-009a). F4 is not in FR-009a's list,
# but its break is in the images list or in the declared build, and no second prepare changes either.
FINAL = frozenset({"F1", "F2", "F3", "F4", "F6", "F7", "F8", "F16", "F19", "F21", "F23", "F30", "F31"})
# What a refusal names under the cluster_upgrade role, in place of the module's own option (User Story 5's refusals).
ROLE_SETTINGS = {
    "wait_timeout": "cluster_upgrade_wait_timeout",
    "reboot_timeout": "cluster_upgrade_reboot_timeout",
    "reboot_set_size": "qumulo_upgrade_reboot_set_size",
    "maintenance_windows": "qumulo_upgrade_maintenance_windows",
    "window_timezone": "qumulo_upgrade_window_timezone",
}


def refusal(code, msg, **details):
    return Refusal(code, msg, **({"final": True} if code in FINAL else {}), **details)


def utc_now():
    return datetime.now(UTC)


def _sentence(text):
    text = (text or "").strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _pending(configuration):
    """A pending stripe configuration as F23 names it, such as 6+2."""
    if isinstance(configuration, dict) and {"blocks_per_stripe", "data_blocks_per_stripe"} <= set(configuration):
        data = configuration["data_blocks_per_stripe"]
        return f"{data}+{configuration['blocks_per_stripe'] - data}"
    return json.dumps(configuration, sort_keys=True)


def _release_of(image):
    """The release a Qumulo image's file name gives, or None."""
    try:
        return parse_images([image])[0].release
    except QumuloError:
        return None


class Upgrade(Action):
    """upgrade: one cluster, one hop, verify, prepare, a readiness check, the commit and its reboots (G-ACTIONS).

    plan() reads, refuses what it can before any write, and predicts each phase; perform() runs them and reconciles a
    lost answer by reading back.
    """

    RETURN_KEY = "plan"

    def __init__(self, module, client):
        super().__init__(module, client)
        p = self.params
        self.host = p.get("host_name") or p["api_host"]
        self.under_role = bool(p.get("host_name"))  # the cluster_upgrade role always sets host_name
        self.image = p["image"]
        self.shown = redact_url(self.image)
        self._check_image()
        self._check_window()
        self.secret = {k: v for k, v in (p.get("image_secret") or {}).items() if v}
        for part in self.secret.values():
            register_secret(part)
        self.full = join_url(self.image, self.secret)
        if self.full != self.image:
            client.redact_urls.append(self.full)
        self.is_url = _parts(self.image) is not None
        self.release = ClusterVersion.parse(p["version"]) if p.get("version") else _release_of(self.image)
        self.build = p.get("build") or None
        self.record_path = p.get("commit_record") or None
        self.limit = the_limit(self.host)
        self.waits = Waits(client, p["wait_timeout"])
        self.notes = []
        self.phases = {}
        self.uuid = None
        self.current = None
        self.status = None
        self.reboot_status = None
        self.record = None
        self.waiver = None
        self.upgrade_type = None
        self.prepared = False
        self.ready = None
        self.commit_sent = False
        self.downtime = None
        self.reboot_seconds = None
        self.to = None
        self.configured = None
        self.reboot_read_at = None

    def _check_image(self):
        """F2 and F33, before any request (FR-002); no image only for a call that waits for the reboots of version."""
        if self.image is None:
            if self.params["state"] == "rebooted" and not self.params["commit"] and self.params.get("version"):
                return
            raise QumuloError(
                f"{self.host}: image is required, except with state rebooted and commit false, which only waits for the reboots "
                "and takes the target from version."
            )
        if not is_url_or_path(self.image):
            raise refusal("F2", f2_message(self.host, self.image), image_redacted=self.shown)
        if any(url_secret(self.image).values()):
            filled = ", which the playbook fills from qumulo_upgrade_images," if self.under_role else ""
            raise refusal(
                "F33",
                f"{self.host}: the image {self.shown} carries a query string or user information in image. Pass those parts in "
                f"image_secret{filled} and the rest in image.",
                image_redacted=self.shown,
            )

    def _check_window(self):
        """F34, before any request: the timezone, clock fields and day names of maintenance_windows, as the plan checks the
        inventory's (FR-017)."""
        p = self.params
        found = window_refusals(
            self.host, p["maintenance_windows"], p["window_timezone"], (self._setting("maintenance_windows"), self._setting("window_timezone"))
        )
        if found:
            raise refusal("F34", " ".join(found))

    # The task

    def run(self):
        try:
            result = super().run()
        except QumuloError as e:
            if self.waits.done:
                e.details["changed"] = True
            e.details.setdefault("changed", False)
            for name, value in self.returns().items():
                e.details.setdefault(name, value)
            raise
        return {**result, **self.returns()}

    def _setting(self, option):
        """The name a refusal gives option: the role's or the inventory's setting under the role, and option on its own."""
        return ROLE_SETTINGS[option] if self.under_role else option

    def returns(self):
        result = {
            "cluster_uuid": self.uuid,
            "image_redacted": self.shown,
            "from": None if self.current is None else {"version": str(self.current), "build": self.current.build_id},
            "to": self.to or {"version": None if self.release is None else str(self.release), "build": self.build},
            "upgrade_type": self.upgrade_type,
            "phases": self.phases,
            "downtime_seconds": self.downtime,
            "ready": self.ready,
            "prepared": self.prepared,
            "commit_sent": self.commit_sent,
            "reboot": self.reboot_used(),
            "waiver": None if self.waiver is None else {"parameter": self.waiver["parameter"], "lifts": list(self.waiver["lifts"])},
            "status": redact_status(self.status, self.full),
        }
        if self.notes:
            result["notes"] = self.notes
        return result

    def reboot_used(self):
        if self.current is None:
            return None
        if self.upgrade_type and self.upgrade_type != PLATFORM:
            mode = "none"
        else:
            mode = "rolling" if self.rolling_sent() else "all"
        return {"mode": mode, "set_size": self.count_sent(), "seconds": self.reboot_seconds}

    # Reads and the plan

    def plan(self):
        self.uuid = node_state(self.client)[0]
        self.current = cluster_version(self.client)
        self.status = self.client.get(STATUS).data or {}
        self.blocked = self.client.get(BLOCKED).data or {}
        self.protection = self.client.get(PROTECTION).data or {}
        self.reboot_status = self.client.get(REBOOT_STATUS).data or {}
        self.nodes = self.client.get(NODES).data or []
        self.configured = configured_node_failures(self.protection, len(self.nodes))
        self.error_before = self.status.get("error_info")
        self.record = self._decide_record()
        self._refuse_paused(self.reboot_status)
        plan = {
            "from": {"version": str(self.current), "build": self.current.build_id, "state": short_state(self.status)},
            "to": {"version": None if self.release is None else str(self.release), "build": self.build},
            "image_redacted": self.shown,
            "phases": [],
            "waits": [],
        }
        if self.record:
            plan["record"] = {"action": self.record.action, "path": self.record_path, "line": self.record.message}
        if self._at_target():
            if self._rebooting(self.status, self.reboot_status):
                plan["phases"].append("reboots")
                plan["waits"].append(f"the reboot under way, {self._rebooted_text(self.reboot_status)}, up to reboot_timeout")
            elif not self.record:
                return None
            self.at_target = True
            return plan
        self.at_target = False
        if self.params["state"] == "rebooted" and not self.params["commit"]:
            raise QumuloError(
                f"{self.host} runs Qumulo Core {self.current} build {self.current.build_id}, not {self._target_text()}, and commit "
                "is false, so nothing was committed and its reboots were not waited for. Run the upgrade with commit: true."
            )
        self._refuse_source()
        self.waiver = self._waiver()
        plan["reboot"] = {"mode": "rolling" if self.rolling_sent() else "all", "set_size": self.count_sent()}
        if self.waiver:
            plan["waiver"] = {"parameter": self.waiver["parameter"], "lifts": list(self.waiver["lifts"])}
        self._warn_ignored_reboot()
        if self.params["state"] == "verified":
            plan["phases"].append("verify")
        else:
            self._plan_prepare(plan)
        if self.params["maintenance_windows"] and self.params["state"] in ("committed", "rebooted"):
            window = self._window()
            plan["window"] = {"inside": window["inside"], "next_open": window["next_open"], "message": window["message"]}
        if self.module.check_mode and self.params["verify_in_check_mode"]:
            self.verify()
        return plan

    def _plan_prepare(self, plan):
        waits = plan["waits"]
        rebooting = self._rebooting(self.status, self.reboot_status)
        if rebooting:
            waits.append(f"the reboot under way, {self._rebooted_text(self.reboot_status)}, up to reboot_timeout")
        found = self._decide_status(self.status)
        if found != "prepared" and not self.params["prepare"]:
            raise self._f17(self.status)
        if found in ("prepare", "wait_then_prepare", "wait_prepared"):
            if not rebooting:
                self._refuse_degraded(self.blocked)
            if self.blocked.get("is_blocked") and not self.blocked.get("cluster_degraded"):
                waits.append(f"the preflight's {self.blocked.get('blocked_reason')!r}, up to wait_timeout")
            if self.count_sent() and not self._count_fits(self.protection):
                waits.append(f"remaining_node_failures {self.protection.get('remaining_node_failures')} to reach {self.count_sent()}, up to wait_timeout")
            if found == "wait_then_prepare":
                waits.append("the PREPARING started with another URL of this image, up to wait_timeout")
            if found == "wait_prepared":
                waits.append("PREPARING with this image, up to wait_timeout")
            if self.waiver_boolean() and found != "wait_prepared":
                plan["phases"].append("verify")
            plan["phases"].append("prepare")
        elif found == "prepared":
            self._refuse_other_release(self.status)
            if not rebooting:
                self._refuse_degraded(self.blocked)
        state, ready = self.params["state"], self.params["ready_to_commit"]
        if found == "committing":
            plan["phases"].append("commit")
            waits.append("the commit under way with this image, up to wait_timeout")
        elif state in ("committed", "rebooted") or (state == "prepared" and ready):
            plan["phases"].append("ready" if state == "prepared" else "commit")
            if found == "prepared":
                waits.extend(self._readiness_waits(self.status))
        if state == "rebooted":
            plan["phases"].append("reboots")

    def _readiness_waits(self, status):
        found = []
        if self.blocked.get("is_blocked"):
            found.append(f"the preflight's {self.blocked.get('blocked_reason')!r}, up to wait_timeout")
        if (status.get("settings") or {}).get("upgrade_type") == PLATFORM:
            remaining = self.protection.get("remaining_node_failures")
            if self.count_sent() and not self._count_fits(self.protection):
                found.append(f"remaining_node_failures {remaining} to reach {self.count_sent()}, up to wait_timeout")
            if not self.rolling_sent() and not self._reprotected(self.protection):
                found.append(f"remaining_node_failures {remaining} to equal the configured {self.configured}, up to wait_timeout")
        return found

    def perform(self, plan):
        if self.record:
            self._retire_record()
        if self.at_target:
            if self._rebooting(self.status, self.reboot_status):
                self.reboot_seconds = self._wait_reboot("reboots")
            return {"changed": False}
        if self.params["state"] == "verified":
            self.verify()
            return {"changed": False}
        committing = self._ensure_prepared()
        state = self.params["state"]
        if state == "prepared" and not committing:
            if self.params["ready_to_commit"]:
                self._ready(wait=True)
            return {"changed": bool(self.waits.done)}
        if committing:
            self._commit(send=False)
        else:
            self._refuse_outside_window()
            self._commit(send=True)
        if state == "rebooted":
            self.reboot_seconds = self._wait_reboot("reboots") if self.upgrade_type == PLATFORM else 0
        return {"changed": bool(self.waits.done)}

    # What the plan reads

    def _decide_record(self):
        if not self.record_path:
            return None
        try:
            with open(self.record_path, encoding="utf-8") as found:
                text = found.read()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:
            raise refusal("F31", unreadable_record(self.host, self.record_path, getattr(e, "strerror", None) or str(e))) from None
        found = {"cluster_uuid": self.uuid, "version": str(self.current), "build": self.current.build_id, "state": self.status.get("state")}
        decision = decide_record(self.host, self.record_path, text, found)
        if decision.action == "refuse":
            raise refusal(decision.code, decision.message)
        return decision

    def _retire_record(self):
        if remove_record(self.record_path, self.record.digest):
            self.client.warnings.append(self.record.message)

    def _at_target(self):
        if self.release is None or not same_release(self.current, self.release):
            return False
        if self.build and self.current.build_id != self.build:
            raise refusal(
                "F4",
                f"{self.host} on {self.current} cannot reach {self.release} with this images list: {self.host} already runs "
                f"{self.release} on build {self.current.build_id}, not the build {self.build} its mapping entry declares. Correct the "
                "entry's build with Qumulo Care; the collection does not move a cluster between builds of one release.",
            )
        return True

    def _target_text(self):
        target = "the image's release" if self.release is None else str(self.release)
        return f"{target} build {self.build}" if self.build else target

    def _refuse_source(self):
        """F1, F3 and F16, before any write."""
        if self.release is not None:
            self._refuse_downgrade(self.release)
        if self.current.respin and not self.params["allow_upgrade_from_limited_release"]:
            raise refusal("F1", f1_message(self.host, self.current, self.limit))

    def _refuse_downgrade(self, release):
        if release < self.current:
            raise_to = f"set qumulo_upgrade_target to {self.current} or later" if self.under_role else f"give an image of {self.current} or later"
            raise refusal(
                "F3",
                f"{self.host} runs Qumulo Core {self.current}, above the target {release}. The collection never downgrades: {raise_to}, "
                f"or leave {self.host} out of the run with --limit.",
            )

    def _waiver(self):
        """The server's waiver of the limited-release rule, in the form the source's release takes (FR-020), or None."""
        if not (self.params["allow_upgrade_from_limited_release"] and self.current.respin):
            return None
        if supports(self.client, WAIVER_GATE):
            return {
                "parameter": f"compatibility_overrides={LIMITED_RELEASE_RULE}",
                "query": {"compatibility_overrides": LIMITED_RELEASE_RULE},
                "lifts": NAMED_RULE_LIFTS,
                "names": [LIMITED_RELEASE_RULE],
            }
        # Below 7.11.0 the boolean lifts every rule, so it goes only with the release and build Qumulo Care named, and only
        # on a hop the quarterly rule allows, which the waiver would otherwise lift too (Decision 25).
        if not self.build or self.release is None:
            raise refusal("F16", f16_message(self.host, self.current, self.limit))
        entry = Image(0, self.release, ON_PREM, self.shown, self.build)
        host_hops(self.host, self.current, [entry], self.release, cluster_kind=None, protection_type=None)
        return {"parameter": "override_compatibility_check=true", "query": {"override_compatibility_check": True}, "lifts": BOOLEAN_LIFTS, "names": []}

    def waiver_boolean(self):
        return bool(self.waiver) and "override_compatibility_check" in self.waiver["query"]

    def _query(self):
        return None if self.waiver is None else self.waiver["query"]

    def rolling_sent(self):
        """do_rolling_reboot: true unless reboot is all or the configured tolerance is 0 (FR-025)."""
        return (self.params["reboot"] or "rolling") == "rolling" and bool(self.configured)

    def count_sent(self):
        return self.params["reboot_set_size"] if self.rolling_sent() else None

    def _warn_ignored_reboot(self):
        """W10, where a declaration is ignored, unless the module runs under the role, whose plan has given it."""
        warning = w10_message(self.host, self.configured, self.params["reboot"], self.params["reboot_set_size"])
        if warning and not self.under_role:
            self.client.warnings.append(warning)

    def _request_timeout(self):
        return request_timeout(self.current)

    # The status (FR-004)

    def _own(self, status):
        return (status.get("settings") or {}).get("install_path") == self.full

    def _matches(self, status):
        """PREPARED with every field this call would send: the image, auto_commit, the reboot settings and the waivers.

        A 7.10.3 status cannot show the boolean waiver (research R1), so a PREPARED of this image matches whichever waiver
        prepared it: the target's preflight refuses any rule the prepare did not lift (FR-004).
        """
        settings = status.get("settings") or {}
        if short_state(status) != "PREPARED" or not self._own(status):
            return False
        if settings.get("auto_commit") is not False or bool(settings.get("do_rolling_reboot")) != self.rolling_sent():
            return False
        if self.rolling_sent() and settings.get("num_nodes_to_reboot") != self.count_sent():
            return False
        if "compatibility_overrides" in settings:
            sent = sorted((self.waiver or {}).get("names", []))
            return sorted(str(name).lower() for name in settings["compatibility_overrides"] or []) == sent
        return True

    def _decide_status(self, status):
        """What the status asks of this run: prepare, wait_prepared, wait_then_prepare, prepared or committing; F6 refuses."""
        state, settings = short_state(status), status.get("settings") or {}
        if state == "IDLE" and (settings or status.get("error_info")):
            self._note(
                f"{self.host} reads IDLE with the last attempt's image {redact_text(settings.get('install_path'), self.full)}, "
                f"auto_commit {settings.get('auto_commit')} and error {redact_text(status.get('error_info'), self.full) or 'none'}; "
                "Qumulo Core keeps them until a new prepare or quorum, so they are history, not an upgrade under way."
            )
        if state == "PREPARING" and settings.get("auto_commit"):
            raise refusal("F6", f6_auto_commit_message(self.host, self.limit))
        if state in STARTED_ELSEWHERE and not self._own(status):
            if state == "PREPARING" and redact_url(settings.get("install_path")) == self.shown:
                return "wait_then_prepare"  # a rerun with a new presigned URL of the same image (Decision 40)
            raise self._f6(status)
        if state == "PREPARING":
            return "wait_prepared"
        if state in ("COMMITTING", "COMMITTED"):
            return "committing"
        if self._matches(status):
            return "prepared"
        if state == "PREPARED":
            self._note(f"{self.host} was PREPARED with {redact_text(settings.get('install_path'), self.full)}, which this run prepares again")
        return "prepare"

    def _note(self, line):
        if line not in self.notes:
            self.notes.append(line)

    def _f6(self, status):
        image = redact_text((status.get("settings") or {}).get("install_path"), self.full) or "unknown"
        return refusal("F6", f6_message(self.host, short_state(status), image, self.limit))

    def _f17(self, status):
        return refusal(
            "F17",
            f"{self.host} is not PREPARED with {self.shown}, its reboot settings and its waivers (it reads {short_state(status)}), so it "
            f"is not committed. Run the upgrade again; it prepares {self.host} first.",
        )

    def _learn_release(self, found, verified=False):
        """The release the server reads in the image: the declared one, or, with none declared, the one learned now."""
        if not found:
            return
        found = ClusterVersion.parse(found)
        if self.release is None:
            self._refuse_downgrade(found)
            self.release = found
        elif not same_release(found, self.release):
            if verified:
                raise refusal(
                    "F21",
                    f"{self.host}: verify reads the image {self.shown} as Qumulo Core {found}, but the images list gives it as "
                    f"{self.release}. Nothing was prepared. Correct the entry's file name or version, then run the upgrade again.",
                )
            raise refusal(
                "F21",
                f"{self.host}: the image {self.shown} holds Qumulo Core {found}, but the images list gives it as {self.release}. "
                f"Nothing was committed, and {self.host} stays PREPARED with that image until its next prepare or a quorum change. "
                "Correct the entry's file name or version, then run the upgrade again.",
            )

    def _refuse_other_release(self, status):
        settings = status.get("settings") or {}
        self._learn_release(settings.get("target_version"))
        self.upgrade_type = settings.get("upgrade_type") or self.upgrade_type

    # The preflight, the reboot and the tolerance

    def _rebooting(self, status, reboot):
        return short_state(status) == "REBOOTING" or (reboot or {}).get("state", REBOOT_IDLE) != REBOOT_IDLE

    def _refuse_paused(self, reboot):
        """F25 and F26, at once in any reboot wait (FR-025a)."""
        state = (reboot or {}).get("state")
        if state == REBOOT_PAUSED:
            raise QumuloError(
                f'{self.host}\'s reboot is paused, and this run did not pause it. Follow "Resume a paused reboot" in the '
                "collection's docs/how-to/resume-an-upgrade.md, then run the upgrade again."
            )
        if state == REBOOT_PAUSED_DUE_TO_ERROR:
            raise QumuloError(
                f"{self.host}'s reboot stopped on an error: {reboot.get('error_message') or 'no message'}. Contact Qumulo Care before "
                f"running anything else on {self.host}."
            )

    def _rebooted_text(self, reboot):
        return f"{(reboot or {}).get('rebooted_node_count')} of {(reboot or {}).get('total_node_count')} nodes rebooted"

    def _wait_reboot(self, phase):
        """Wait while the status reads REBOOTING or the reboot status is not REBOOT_IDLE, up to reboot_timeout; its seconds.

        The reboot status is read every 30 seconds, and only while a reboot is under way; offline nodes are the reboot.
        """
        self._start(phase)
        self.waits.restart(self.params["reboot_timeout"])
        started = self.client.clock()
        status, reboot = self.status, self.reboot_status
        while self._rebooting(status, reboot):
            self._refuse_paused(reboot)
            if not self.waits.pause(REBOOT_POLL):
                raise QumuloError(
                    f"{self.host} still reads REBOOTING after {self.params['reboot_timeout']} seconds, with "
                    f"{self._rebooted_text(reboot)}. Wait for the reboot to finish, then run the upgrade again, or raise "
                    f"{self._setting('reboot_timeout')}."
                )
            found = self.waits.poll(STATUS)
            if isinstance(found, dict):
                status = found
            if short_state(status) == "REBOOTING" or reboot.get("state") != REBOOT_IDLE:
                found = self.waits.poll(REBOOT_STATUS)
                if isinstance(found, dict):
                    reboot = found
        self.status, self.reboot_status = status, reboot
        self._end(phase)
        return self.client.clock() - started

    def _refuse_degraded(self, blocked):
        if blocked.get("is_blocked") and blocked.get("cluster_degraded"):
            raise refusal(
                "F5",
                f"{self.host} cannot be upgraded now: {_sentence(blocked.get('blocked_reason')).removesuffix('.')}. Bring the node or "
                "disk back into the cluster, then run the upgrade again.",
            )

    def _preflight(self, wait, blocked=None):
        """The preflight: a degraded reason fails at once (F5), and any other is waited out up to wait_timeout.

        blocked is a read just made, or None to read it now.
        """
        blocked = blocked if blocked is not None else self.client.get(BLOCKED).data or {}
        self.waits.restart(self.params["wait_timeout"])
        while blocked.get("is_blocked"):
            self._refuse_degraded(blocked)
            if not wait or not self.waits.pause():
                raise refusal(
                    "F5",
                    f"{self.host} still reads blocked after {self.params['wait_timeout']} seconds: "
                    f"{_sentence(blocked.get('blocked_reason')).removesuffix('.')}. On a large file system the background work can "
                    f"take longer; run the upgrade again, which picks up where it stopped, or raise {self._setting('wait_timeout')}.",
                )
            found = self.waits.poll(BLOCKED)
            if isinstance(found, dict):
                blocked = found
        self.blocked = blocked

    def _count_fits(self, protection):
        return (protection.get("remaining_node_failures") or 0) >= self.count_sent()

    def _reprotected(self, protection):
        return protection.get("remaining_node_failures") == self.configured

    def _f7(self, protection):
        return refusal(
            "F7",
            f"{self.host}: Rolling reboot must reboot {protection.get('remaining_node_failures')} or fewer nodes at once due to the "
            f"current node fault tolerance. The tolerance did not return within {self.params['wait_timeout']} seconds. "
            f"{self._f7_action()}",
        )

    def _f7_action(self):
        return (
            f"Run the upgrade again once the cluster has finished reprotecting, raise {self._setting('wait_timeout')}, or lower "
            f"{self._setting('reboot_set_size')} for {self.host}."
        )

    def _f23(self, protection):
        pending = protection.get("pending_configuration")
        changes = f", while its protection changes to {_pending(pending)}" if pending else ""
        return refusal(
            "F23",
            f"{self.host}: its current node fault tolerance reads {protection.get('remaining_node_failures')} of its configured "
            f"{self.configured} after {self.params['wait_timeout']} seconds{changes}, and an all-at-once reboot waits for a full "
            "reprotect with every node armed. Nothing was committed. Run the upgrade again once the cluster has finished reprotecting, "
            f"or raise {self._setting('wait_timeout')}.",
        )

    def _wait_protection(self, fits, refuse, wait, protection=None):
        """Read the protection status until fits(it), polling up to wait_timeout when wait; refuse(it) otherwise."""
        protection = protection if protection is not None else self.client.get(PROTECTION).data or {}
        self.waits.restart(self.params["wait_timeout"])
        while not fits(protection):
            if not wait or not self.waits.pause():
                raise refuse(protection)
            found = self.waits.poll(PROTECTION)
            if isinstance(found, dict):
                protection = found
        self.protection = protection

    # Verify and prepare (FR-007)

    def verify(self):
        """verify-image, which installs nothing: its release and upgrade type, or the server's refusal."""
        self._start("verify")
        try:
            answer = self.client.post(VERIFY, body={"image_path": self.full}, query=self._query(), timeout=self._request_timeout()).data or {}
        except QumuloMaybeApplied:
            raise QumuloError(f"{self.host}: verify's answer was lost; nothing was installed. Run the upgrade again.", changed=False) from None
        except QumuloApiError as e:
            raise self._server_refusal(e) from None
        if answer.get("error"):
            raise self._image_refused(answer["error"], answer.get("error_class"))
        self._learn_release(answer.get("target_version"), verified=True)
        self.upgrade_type = answer.get("upgrade_type") or self.upgrade_type
        self._end("verify")

    def _reach(self, text):
        return f"{text} Every node of {self.host} must reach {self.shown} from its own network; check each node's route to it."

    def _image_refused(self, text, error_class=None, server_error=False):
        """Verify's 200 with an error and prepare's 400 for the same image are one refusal (QUIRK-094)."""
        msg = f"{self.host}: Qumulo Core refused the image {self.shown}: {_sentence(redact_text(text, self.full))}"
        if self.is_url and (server_error or UNREACHED.search(text or "")):
            msg = self._reach(msg)
        return QumuloError(msg, error_class=error_class)

    def _server_refusal(self, e):
        description = redact_text(e.description, self.full)
        if (e.status == 500 and e.is_("os_error_ENOENT") and INSTALLER_SCRIPT in description) or e.is_("upgrade_externally_managed_error"):
            return refusal(
                "F8",
                f"{self.host} cannot upgrade in band: Qumulo Core reports that this deployment has no installer for it. The collection "
                "does not upgrade such a deployment; upgrade it as its deployment documents.",
                status=e.status,
                error_class=e.error_class,
            )
        if e.is_("reboot_error") and description.startswith(COUNT_REFUSAL):
            return refusal(
                "F7",
                f"{self.host}: {_sentence(description)} {self._f7_action()}",
                status=e.status,
                error_class=e.error_class,
            )
        if e.status == 400 and e.is_("upgrade_verify_error"):
            return self._image_refused(description.removeprefix(VERIFY_PREFIX), e.error_class)
        msg = f"{self.host}: {_sentence(redact_text(str(e), self.full))}"
        if self.is_url and (e.status >= 500 or UNREACHED.search(description)):
            msg = self._reach(msg)
        return QumuloError(msg, status=e.status, error_class=e.error_class)

    def _ensure_prepared(self):
        """Wait out a reboot, then leave the cluster PREPARED with this run's settings; True when a commit is already under way."""
        waited = self._rebooting(self.status, self.reboot_status)
        if waited:
            self._wait_reboot("unblock")
        found = self._decide_status(self.status)
        if found in ("wait_prepared", "wait_then_prepare"):
            self._await_preparing()
            found = self._decide_status(self.status)
        if found == "committing" and self.params["prepare"]:
            return True
        if found == "prepared":
            self._refuse_other_release(self.status)
            return False
        if not self.params["prepare"]:
            raise self._f17(self.status)
        self._await_prepare_allowed(blocked=None if waited else self.blocked, protection=None if waited else self.protection)
        if self.waiver_boolean():
            self.verify()
        self._prepare()
        return False

    def _await_prepare_allowed(self, blocked=None, protection=None):
        """Wait out the preflight and, for a prepare that sends a count, the count (FR-005); blocked and protection are reads
        just made, or None to read them now."""
        self._preflight(wait=True, blocked=blocked)
        if self.count_sent():
            self._wait_protection(self._count_fits, self._f7, wait=True, protection=protection)

    def _await_preparing(self):
        """A PREPARING started outside this run with this image, or with another URL of it as a rerun with a new presigned URL
        (Decision 40), waited for before the status is decided again; F21 for this image's PREPARING of another release.
        """
        self.waits.restart(self.params["wait_timeout"])
        status = self.status
        while short_state(status) == "PREPARING":
            if self._own(status):
                self._refuse_other_release(status)
            if not self.waits.pause():
                raise QumuloError(
                    f"{self.host} still reads PREPARING with {self.shown} after {self.params['wait_timeout']} seconds; another prepare "
                    "of this image is still under way. Wait for it to finish, then run the upgrade again."
                )
            found = self.waits.poll(STATUS)
            if isinstance(found, dict):
                status = found
        self.status = status

    def _prepare(self):
        body = {"image_path": self.full, "auto_commit": False, "do_rolling_reboot": self.rolling_sent()}
        if self.count_sent():
            body["num_nodes_to_reboot"] = self.count_sent()
        self._start("prepare")
        for attempt in (1, 2):
            try:
                self.client.post(PREPARE, body=body, query=self._query(), timeout=self._request_timeout())
            except QumuloMaybeApplied:
                self._await_prepared(self._request_timeout(), LOST_ANSWER)
                return
            except QumuloApiError as e:
                if e.status == 409 and STILL_REBOOTING in e.description and attempt == 1:
                    # The 409 means nothing was applied, so the prepare goes again once the reboot ends, and once the
                    # preflight and the count have recovered from it.
                    self.status = {**self.status, "state": STATE_PREFIX + "REBOOTING"}
                    self._wait_reboot("unblock")
                    self._await_prepare_allowed()
                    continue
                if e.status == 409 and e.is_("upgrade_already_in_progress_error"):
                    self._await_prepared(self._request_timeout(), IN_PROGRESS)
                    return
                raise self._server_refusal(e) from None
            break
        self._prepared_here()
        self._await_prepared(self.params["wait_timeout"], ACCEPTED)

    def _prepared_here(self, how=""):
        self.prepared = True
        self.waits.done.append(f"prepared {self.host} with {self.shown}{how}")

    def _await_prepared(self, timeout, answer):
        """Poll the status until PREPARED matches this call's prepare (FR-007), after the prepare's answer:

        - ACCEPTED, a 202: the status reads PREPARING, and IDLE with no error is a prepare stopped by a quorum change;
        - LOST_ANSWER: IDLE and a PREPARED that does not match, the old prepare still in place while the new one's verify
          runs, are waited past until timeout; PREPARING with this image shows the prepare applied, and from there it is
          waited for as after a 202;
        - IN_PROGRESS, a 409 for another prepare: PREPARING with this image is waited for, and anything but a matching
          PREPARED is F6 at once.

        After ACCEPTED, an IDLE with any error is the prepare's refusal, even the error the status held before the
        request, which a second prepare of an image no node can reach repeats. After LOST_ANSWER, which cannot tell that
        earlier error from this prepare's, only another error is.
        """
        self.waits.restart(timeout)
        status = None
        while True:
            found = self.waits.poll(STATUS)
            if isinstance(found, dict):
                status = found
                state, own = short_state(status), self._own(status)
                if state == "PREPARING" and own:
                    if answer == LOST_ANSWER:
                        answer, timeout = ACCEPTED, self.params["wait_timeout"]
                        self._prepared_here(", its answer lost")
                        self.waits.restart(timeout)
                    self._refuse_other_release(status)
                elif self._matches(status):
                    self._refuse_other_release(status)
                    if answer == LOST_ANSWER:
                        self._prepared_here(", its answer lost")
                    self.status = status
                    self._end("prepare")
                    return
                elif answer == IN_PROGRESS or (state in STARTED_ELSEWHERE and not own):
                    raise self._f6(status)
                elif state == "IDLE" and status.get("error_info") and (answer == ACCEPTED or status.get("error_info") != self.error_before):
                    raise self._image_refused(status["error_info"], server_error=True)
                elif state == "IDLE" and answer == ACCEPTED:
                    raise QumuloError(
                        f"{self.host}: the prepare of {self.shown} stopped: Qumulo Core reads IDLE with no error, as after a quorum "
                        "change. Run the upgrade again.",
                        changed=True,
                    )
            if not self.waits.pause():
                break
        self.status = status or self.status
        if answer == LOST_ANSWER:
            # Only IDLE or the old PREPARED is read here: PREPARING with this image has become ACCEPTED's wait.
            earlier = (
                f"the earlier prepare of {redact_text((self.status.get('settings') or {}).get('install_path'), self.full)}"
                if short_state(self.status) == "PREPARED"
                else "IDLE"
            )
            raise QumuloError(
                f"{self.host}: the prepare's answer was lost, and after {timeout} seconds Qumulo Core still reads {earlier}, so the "
                "prepare may or may not have been applied. A verify that Qumulo Core refused leaves the same status. Every node of "
                f"{self.host} must reach {self.shown} from its own network. Run the upgrade again, which reads the status before it prepares.",
                changed=True,
                maybe_applied=True,
            )
        if answer == IN_PROGRESS:
            raise QumuloError(
                f"{self.host} still reads {short_state(self.status)} with {self.shown} after {timeout} seconds; another prepare of this "
                "image is still under way. Wait for it to finish, then run the upgrade again."
            )
        raise QumuloError(
            f"{self.host} still reads {short_state(self.status)} after {timeout} seconds of preparing {self.shown}. Run the upgrade "
            f"again, which reads the status before it prepares; if it reads the same, check every node's route to {self.shown}.",
            changed=True,
        )

    # The readiness check and the commit (FR-003, FR-009)

    def _ready(self, wait):
        """With wait, the readiness check; without, the commit's own re-check, one read of each and no wait.

        Either way F5, F7 and F23 give wait_timeout as their seconds. At the re-check that is the wait of the readiness
        check just before it, as F7's row asks, since the role runs both calls with the same wait_timeout.
        """
        self._refuse_other_release(self.status)
        self._start("ready")
        self._preflight(wait)
        if self.upgrade_type == PLATFORM:
            if self.count_sent():
                self._wait_protection(self._count_fits, self._f7, wait)
            if not self.rolling_sent():
                self._wait_protection(self._reprotected, self._f23, wait)
        self.ready = True
        self._end("ready")

    def _window(self):
        """window_form's answer for this cluster alone, as the module run on its own checks its window (FR-017)."""
        hop = {
            "release": "the image's release" if self.release is None else str(self.release),
            "moves": [self.host],
            "windows": {self.host: {"ranges": self.params["maintenance_windows"], "timezone": self.params["window_timezone"]}},
            "units": [{"name": self.host, "round": 1, "waves": [[self.host]], "follows": []}],
            "components": {},
        }
        return window_form(hop, (), utc_now())

    def _refuse_outside_window(self):
        if not self.params["maintenance_windows"]:
            return
        window = self._window()
        if not window["inside"]:
            with self.waits.after_writes():
                raise Refusal("F9", window["message"])

    def _commit(self, send):
        target = self.release
        recorded = self.current.build_id
        lost = False
        if send:
            self._ready(wait=False)
            target = ClusterVersion.parse(self.status["settings"]["target_version"])
            if self.record_path:
                try:
                    write_record(
                        self.record_path,
                        {
                            "state": PENDING,
                            "cluster_uuid": self.uuid,
                            "build": recorded,
                            "target_version": str(target),
                            "declared_build": self.build,
                            "time": utc_now().isoformat(timespec="seconds"),
                        },
                    )
                except OSError as e:
                    raise QumuloError(
                        f"{self.host}: the commit record {self.record_path} could not be written ({e.strerror or e}). Nothing was "
                        "committed: make its directory writable, then run the upgrade again."
                    ) from None
            self._start("commit")
            try:
                self.client.post(COMMIT)
            except QumuloMaybeApplied:
                lost = True
            except QumuloApiError:
                if self.record_path:
                    remove_record(self.record_path)
                raise
            self.commit_sent = True
            self.waits.done.append(f"sent the commit of {self.host} to {target}")
        else:
            self._start("commit")
            self.upgrade_type = (self.status.get("settings") or {}).get("upgrade_type") or self.upgrade_type
            self._learn_release((self.status.get("settings") or {}).get("target_version"))
            target = self.release
        self._await_commit(target, recorded, lost, wrote_record=bool(self.record_path and send))
        if self.record_path and send:
            remove_record(self.record_path)
        self._end("commit")
        self._await_root()

    def _await_root(self):
        """Once the commit is done, read the file system's root within what is left of wait_timeout, unanswered reads polled.

        A rolling reboot can take the REST API's own node meanwhile, so a root read the time left does not reach is a
        note: the commit is done by FR-009's conditions, which the root read is not one of.
        """
        left = max(self.waits.deadline - self.client.clock(), POLL_SECONDS)
        try:
            Readiness(self.client, left).root()
        except QumuloError as e:
            self._note(
                f"{self.host}'s commit is done; its file system did not answer in the {left:.0f} seconds left of wait_timeout, as "
                f"while a reboot has the REST API's own node: {redact_text(str(e), self.full)}"
            )

    def _version(self):
        """GET /v1/version with no login, read directly rather than through version.py's cache; UNANSWERED meanwhile."""
        try:
            data = self.client.get(VERSION, auth=False, retry_timeout=0).data
        except QumuloUnreachable:
            return UNANSWERED
        except QumuloApiError as e:
            if e.status >= 500:
                return UNANSWERED
            raise
        found = ClusterVersion.from_revision(data.get("revision_id"), data.get("build_id") or "")
        return UNANSWERED if found is None else found

    def _await_commit(self, target, recorded, lost, wrote_record):
        """Decide the commit from the running build, then the status (FR-009), up to wait_timeout; F32 otherwise.

        wrote_record says this call wrote the pending record, which goes once the commit is done, on any build.
        """
        self.waits.restart(self.params["wait_timeout"])
        self.downtime = 0.0
        down_since, new_build, answered, resent = None, False, True, False
        last_state, last_build = short_state(self.status), recorded
        while True:
            now = self.client.clock()
            running = self._version()
            if running is UNANSWERED:
                answered = False
                if not new_build and down_since is None:
                    down_since = now
            else:
                if down_since is not None and not new_build:
                    self.downtime += now - down_since
                    down_since = None
                last_build = running.build_id
                if running.build_id != recorded and same_release(running, target):
                    if not new_build:
                        new_build = True
                        self.to = {"version": str(running), "build": running.build_id}
                        if self.build and running.build_id != self.build:
                            if wrote_record:
                                remove_record(self.record_path)
                            raise QumuloError(
                                f"{self.host} runs Qumulo Core {running} build {running.build_id}, not the declared build {self.build}. The "
                                f"commit is done and cannot be undone. Check the build in qumulo_upgrade_images' entry for {running}, and "
                                "the image, with Qumulo Care.",
                                changed=True,
                            )
                    if self._commit_done():
                        break
                    last_state = short_state(self.status)
                elif running.build_id == recorded:
                    status = self.waits.poll(STATUS)
                    if not isinstance(status, dict):
                        answered = False
                    else:
                        self.status, last_state = status, short_state(status)
                        if last_state == "IDLE":
                            again = self._version()
                            if again is not UNANSWERED and again.build_id == recorded:
                                raise self._unfinished(target, status)
                        elif last_state == "PREPARED" and lost and answered and not resent:
                            resent = True
                            self._resend_commit()
            if not self.waits.pause():
                raise self._f32(target, last_state or "no answer", last_build)

    def _resend_commit(self):
        """The collection's one write-again: a lost commit found still PREPARED with the API answering throughout.

        The server refuses a second commit while the first runs, with a 409 that means "wait".
        """
        try:
            self.client.post(COMMIT)
        except QumuloMaybeApplied:
            return
        except QumuloApiError as e:
            if not (e.status == 409 and e.is_("upgrade_already_in_progress_error")):
                raise

    def _commit_done(self):
        """On the new build: whether the commit is done, by the upgrade type and the reboot mode prepared."""
        status = self.waits.poll(STATUS)
        if not isinstance(status, dict):
            return False
        self.status = status
        settings_type = self.upgrade_type
        state = short_state(status)
        due = self.reboot_read_at is None or self.client.clock() - self.reboot_read_at >= REBOOT_POLL
        if (state == "REBOOTING" or self.reboot_status.get("state") != REBOOT_IDLE) and due:
            self.reboot_read_at = self.client.clock()
            reboot = self.waits.poll(REBOOT_STATUS)
            if isinstance(reboot, dict):
                self.reboot_status = reboot
                self._refuse_paused(reboot)
        reboot_state = self.reboot_status.get("state")
        if settings_type == PLATFORM and self.rolling_sent() and state == "REBOOTING" and reboot_state == REBOOT_IN_PROGRESS:
            return True
        nodes = self.waits.poll(NODES)
        online = isinstance(nodes, list) and bool(nodes) and all(node.get("node_status") == "online" for node in nodes)
        if not online:
            return False
        if state == "IDLE" and reboot_state == REBOOT_IDLE:
            return True
        return not (settings_type == PLATFORM and self.rolling_sent())

    def _record_clause_idle(self, unwritten=None):
        """unwritten is the OSError that kept the record from becoming unfinished, if one did."""
        if unwritten is not None:
            # A failed write leaves the pending record in place, or the unfinished one not yet flushed, and FR-034's table
            # refuses either F19 on this IDLE status and old build.
            return (
                f"The collection could not record this at {self.record_path} ({unwritten.strerror or unwritten}), but the record "
                f"written there before the commit stays, and the next run stops on it until Qumulo Care has cleared {self.host} "
                f"and the record is removed. To upgrade the rest of the fleet meanwhile, {self.limit}."
            )
        if self.record_path:
            return (
                f"The collection has recorded this at {self.record_path}, and the next run stops on it until Qumulo Care has cleared "
                f"{self.host} and the record is removed. To upgrade the rest of the fleet meanwhile, {self.limit}."
            )
        return f"No commit record was written, since commit_record was not set; run the next upgrade of {self.host} only once Qumulo Care has cleared it."

    def _unfinished(self, target, status):
        error = redact_text(status.get("error_info"), self.full) or "no error"
        unwritten = None
        if self.record_path:
            try:
                write_record(
                    self.record_path,
                    {
                        "state": UNFINISHED,
                        "cluster_uuid": self.uuid,
                        "build": self.current.build_id,
                        "target_version": str(target),
                        "declared_build": self.build,
                        "time": utc_now().isoformat(timespec="seconds"),
                        "error_info": status.get("error_info") and error,
                    },
                )
            except OSError as e:
                unwritten = e
        return QumuloError(
            f"{self.host}: the commit did not finish. The cluster still runs {self.current} and its upgrade status is IDLE ({error}). "
            f"Some nodes may be armed to start {target} at their next restart, and Qumulo Core's own guidance for this state is manual "
            f"intervention. Contact Qumulo Care before running anything else on {self.host}. {self._record_clause_idle(unwritten)}",
            changed=True,
            maybe_applied=True,
            unfinished_commit=True,
        )

    def _f32(self, target, state, build):
        if self.record_path:
            clause = f"The record {self.record_path} stays: the next run waits for the commit to finish, or retires the record."
        else:
            clause = "No commit record was written, since commit_record was not set."
        return QumuloError(
            f"{self.host}'s commit to {target} was sent, and after {self.params['wait_timeout']} seconds it reads {state} on build "
            f"{build}. Nothing more was sent. {clause} Run the upgrade again once {self.host} reads {target}, or raise "
            f"{self._setting('wait_timeout')}.",
            changed=True,
            maybe_applied=True,
            commit_sent=True,
        )

    # Phase times

    def _start(self, phase):
        self.phases.setdefault(phase, {"started": utc_now().isoformat(timespec="seconds"), "ended": None})

    def _end(self, phase):
        if phase in self.phases:
            self.phases[phase]["ended"] = utc_now().isoformat(timespec="seconds")
