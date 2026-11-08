# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The capacity area: the clamp on a cluster's usable capacity, and the route to an edge's cache size."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, QumuloMaybeApplied
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import (
    ACTIVE,
    CAPACITY_CLAMP,
    FILE_SYSTEM,
    POLL_INTERVAL,
    Readiness,
    bytes_or_none,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.normalize import parse_size

MAX_INCREASE = 5 * 10**15  # the most one PUT may raise the clamp by, 5 PB (decimal)
# The PUT can be answered before the node abandons quorum, so a node still ACTIVE right after it is
# watched this long for the bounce before the readiness wait may trust its ACTIVE state.
BOUNCE_GRACE = 30.0
# The provisioning unit the server rounds a clamp up to is not published (QUIRK-012). On a 7.10.3A docker edge it
# was 8 GiB: a 53.33 GiB request was stored as 56 GiB. So the default rounding tolerance never goes below it.
MIN_ROUNDING_TOLERANCE = 8 * 2**30
GROW, DECREASE = "grow", "decrease"  # a write that does not lower the clamp, and one that does


class Clamp:
    """capacity_clamp: write only when the clamp must change, since every PUT re-forms quorum. Run by common.run_resource."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.requested = parse_size(self.params["capacity_clamp"])
        tolerance = self.params.get("rounding_tolerance")
        self.tolerance = default_rounding_tolerance(self.requested) if tolerance is None else parse_size(tolerance)
        self.notes = []

    def run(self):
        stored = bytes_or_none(self.client.get(CAPACITY_CLAMP).data["capacity_clamp"])
        action = self.plan(stored)
        result = {"changed": action is not None, "capacity_clamp": stored, "requested": self.requested}
        if self.module._diff:
            result["diff"] = {"before": {"capacity_clamp": stored}, "after": {"capacity_clamp": self.requested if action else stored}}
        if action is None or self.module.check_mode:
            return result
        result["capacity_clamp"] = self.write(action)
        if self.notes:
            result["notes"] = self.notes
        return result

    def plan(self, stored):
        """GROW, DECREASE, or None when the stored clamp is the request, possibly rounded up to a provisioning unit.

        An unset clamp limits nothing, so a first clamp is measured against the provisioned capacity, read as the
        file system's size, and is always written.
        """
        if stored is None:
            baseline = bytes_or_none(self.client.get(FILE_SYSTEM).data["total_size_bytes"])
            shown = f"no clamp is set, and the provisioned capacity is {baseline} bytes"
        else:
            baseline, shown = stored, f"the stored clamp is {stored} bytes"
        if self.requested - baseline > MAX_INCREASE:
            raise QumuloError(
                f"one write can raise the clamp by at most 5 PB (decimal), but {shown} and {self.requested} bytes is "
                f"{self.requested - baseline} above it; raise it in steps of at most 5 PB (decimal), each of which re-forms quorum"
            )
        if self.requested >= baseline:
            return GROW if stored is None or self.requested > stored else None
        if baseline - self.requested <= self.tolerance:
            return GROW if stored is None else None
        if not self.params["allow_decrease"]:
            raise QumuloError(
                f"{shown}, more than rounding_tolerance ({self.tolerance} bytes) above the requested {self.requested}; "
                "lowering the clamp needs allow_decrease=true, and the provisioned capacity does not shrink even then (G-CLAMP-SHRINK)"
            )
        self.client.warnings.append(
            f"lowering the clamp to {self.requested} bytes, while {shown}: the provisioned capacity does not shrink; the "
            "cluster only stops provisioning more (G-CLAMP-SHRINK)"
        )
        return DECREASE

    def write(self, action):
        """PUT the clamp once, never again; then wait for the quorum bounce and read the clamp back."""
        try:
            self.client.put(CAPACITY_CLAMP, body={"capacity_clamp": self.requested})
        except QumuloMaybeApplied as lost:
            return self._reconcile(lost, action)
        if not self.params["wait"]:
            return self.requested
        try:
            self._await_bounce_and_ready()
            observed = bytes_or_none(self.client.get(CAPACITY_CLAMP).data["capacity_clamp"])
        except QumuloError as e:
            e.details.setdefault("changed", True)
            raise
        if not self._applied(observed, action):
            raise QumuloError(f"the clamp did not converge: requested {self.requested} bytes, stored {_shown(observed)}", changed=True)
        if observed - self.requested > self.tolerance:
            self.client.warnings.append(
                f"the cluster stored the clamp as {observed} bytes, {observed - self.requested} above the request and more than "
                f"rounding_tolerance ({self.tolerance} bytes); set rounding_tolerance to at least {observed - self.requested}, "
                "or the next run reports a decrease"
            )
        return observed

    def _reconcile(self, lost, action):
        """Wait for the cluster even without wait, since only reading the clamp back tells whether the PUT took effect."""
        try:
            self._await_bounce_and_ready()
            observed = bytes_or_none(self.client.get(CAPACITY_CLAMP).data["capacity_clamp"])
        except QumuloError as e:
            raise QumuloError(f"{lost}; reading back failed: {e}", changed=True, maybe_applied=True) from None
        if not self._applied(observed, action):
            raise QumuloError(
                f"{lost}; reading back shows the clamp is {_shown(observed)}, not the requested {self.requested} bytes; it was not sent again",
                changed=True,
                maybe_applied=True,
            ) from None
        self.notes.append("write reconciled after a lost connection")
        return observed

    def _applied(self, observed, action):
        """A grow is applied at or above the request; a decrease only within rounding_tolerance of it, since the old clamp is above it too."""
        if observed is None or observed < self.requested:
            return False
        return action == GROW or observed - self.requested <= self.tolerance

    def _await_bounce_and_ready(self):
        ready = Readiness(self.client, self.params["wait_timeout"])
        self._await_bounce(ready)
        ready.state({ACTIVE}, self._check_identity)
        ready.root()

    def _await_bounce(self, ready):
        """Return once the node is seen to leave ACTIVE or stop answering, or after BOUNCE_GRACE, at most until the wait's deadline."""
        clock = self.client.clock
        grace_end = clock() + min(BOUNCE_GRACE, ready.budget())
        while clock() < grace_end:
            try:
                _cluster_id, state = self.client.node_state(retry_timeout=min(POLL_INTERVAL, grace_end - clock()))
            except QumuloError:
                return
            if state != ACTIVE:
                return
            self.client.sleep(min(POLL_INTERVAL, max(0.0, grace_end - clock())))

    def _check_identity(self, cluster_id, state):
        expected = self.client.expected_uuid
        if expected and cluster_id and cluster_id.lower() != expected.lower():
            raise QumuloError(f"{self.client.base} came back as cluster '{cluster_id}' (state {state}), expected '{expected}'")


def default_rounding_tolerance(requested):
    """The larger of 1% of the request and MIN_ROUNDING_TOLERANCE."""
    return max(requested // 100, MIN_ROUNDING_TOLERANCE)


def _shown(clamp):
    return "unset" if clamp is None else f"{clamp} bytes"


# The routes of the edge_cache_resize role, as edge_cache_route() chooses them.
WITHIN, GROW_ROUTE, PLATFORM_LIMITED = "within", "grow", "platform_limited"
BLOCKED_LICENSE, BELOW_FLOOR, REBUILD = "blocked_license", "below_floor", "rebuild"
EDGE_FLOOR = 10**12  # an unlicensed edge provisions min(platform, 1 TB) when it is formed, and no more: G-EDGE-FLOOR
# The server rounds the file system, a license's cap and the clamp to units the collection cannot read, each below this.
FLOOR_MARGIN = 10**11


def edge_cache_route(target, stored, license_cap, total, unprotected_edge, margin=None, floor=EDGE_FLOOR):
    """How to bring an edge's cache to target bytes: a dict with the route, the numbers it used in bytes, and the reason.

    total, the file system's size, stands in for the provisioned capacity, which no API reports (G-CLAMP-TOL).
    An unset clamp (stored None) limits nothing. Without a license cap, an unprotected edge is held to EDGE_FLOOR,
    whatever floor says; floor only decides which smaller targets a rebuild could reach.
    """
    t = parse_size(target)
    m = t // 10 if margin is None else parse_size(margin)
    ceiling = license_cap if license_cap is not None else (EDGE_FLOOR if unprotected_edge else None)
    plan = {
        "target": t,
        "stored": stored,
        "license_cap": license_cap,
        "ceiling": ceiling,
        "total": total,
        "margin": m,
        "floor": floor,
        "rounding_tolerance": default_rounding_tolerance(t),
    }

    def route(name, reason):
        return {**plan, "route": name, "reason": reason}

    if abs(total - t) <= m:
        return route(WITHIN, f"the file system ({total} bytes) is within {m} bytes of the target")
    if total > t + m:
        if t < floor:
            return route(
                BELOW_FLOOR,
                f"the target is below {floor} bytes, which a rebuilt edge provisions again (G-EDGE-FLOOR); a free_threshold of "
                f"about {1 - t / total:.2f} keeps about the target in use",
            )
        return route(
            REBUILD,
            f"the target is more than {m} bytes below the file system ({total} bytes), and provisioned capacity never shrinks "
            "(G-CLAMP-SHRINK); a rebuilt edge has a new UUID, which needs a new license from Nexus (G-UUID, G-LICENSE)",
        )
    if ceiling is not None and ceiling < t:
        if license_cap is not None:
            return route(BLOCKED_LICENSE, f"the license caps usable capacity at {license_cap} bytes, below the target (G-LICENSE)")
        return route(BLOCKED_LICENSE, f"an unlicensed edge is limited to {ceiling} bytes, below the target (G-EDGE-FLOOR, G-LICENSE)")
    if stored is not None and stored >= t:
        return route(
            PLATFORM_LIMITED,
            f"the stored clamp ({stored} bytes) already allows the target, but the file system holds {total} bytes. Either "
            "the platform's disks cannot provision more, since they cannot be resized or added after formation, so a larger "
            "edge is a rebuild; or a raised license cap has not applied yet: a cap applies at the next quorum formation, "
            "which a clamp write would cause, but a clamp that already allows the target is not written (G-LICENSE)",
        )
    return route(GROW_ROUTE, f"the target is more than {m} bytes above the file system ({total} bytes), and the clamp ({_shown(stored)}) is below it")
