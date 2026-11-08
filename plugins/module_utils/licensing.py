# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The licensing area: whether a cluster holds a license for its current UUID, and applying one.

Nexus issues licenses, and a customer organization's users, its administrators included, cannot see
them there, so the collection neither creates nor reads them in Nexus. The operator obtains the license
document and gives it to the collection, which applies it through the cluster API; the license is
active at once, so nothing waits for one (G-LICENSE).
"""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils.capacity import EDGE_FLOOR, FLOOR_MARGIN
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import CLUSTER_INFO_SUBSETS, LICENSE_STATUS, license_facts, node_state

# Both take the license document as Nexus issued it, and both need PRIVILEGE_DEBUG.
LICENSE_VALIDATE = Route(
    "/v1/debug/license/validate",
    ("POST",),
    replay=frozenset({"POST"}),
    why="validating a license document only checks it and changes nothing on the cluster",
)
LICENSE_ACTIVATE = Route("/v1/debug/license/activate", ("POST",))
ACTIVE = "LICENSE_STATE_ACTIVE"
# The fields of a license document. The key signs all the others, so a document is sent with exactly these.
DOCUMENT_KEYS = ("cluster_uuid", "details", "expiration_date", "earliest_expiration_date", "license_key")


def limits_above(reference, cap, clamp):
    """True when the cap is set and the smaller of the cap and the clamp, an unset clamp limiting nothing, is more than
    FLOOR_MARGIN above reference."""
    if cap is None:
        return False
    return (cap if clamp is None else min(cap, clamp)) > reference + FLOOR_MARGIN


def license_argument_spec():
    """The document option, also named license: a document, with only its key kept out of logs and results."""
    fields = {key: {"type": "raw", "required": True} for key in DOCUMENT_KEYS}
    fields["license_key"] = {"type": "str", "required": True, "no_log": True}
    return {"type": "dict", "aliases": ["license"], "options": fields}


def activation_routes(client):
    """The routes that validate and activate a license document on this cluster.

    Qumulo Core offers only these debug routes today. A release route, once a version offers one, is
    chosen here, so that nothing else changes.
    """
    return LICENSE_VALIDATE, LICENSE_ACTIVATE


def read_license(client):
    """The cluster's license facts, and the license_key of the document it holds, which is never returned."""
    status = client.get(LICENSE_STATUS).data
    return license_facts(client, status), (status.get("license") or {}).get("license_key")


class License:
    """license: the cluster holds an active license for its current UUID; the given document's, when given."""

    def __init__(self, module, client):
        self.module = module
        self.client = client
        self.params = module.params
        self.cluster_id = None
        self.document = None  # the document to apply, when given

    def run(self):
        self.cluster_id = node_state(self.client)[0]
        if self.params.get("document") is not None:
            self.document = self._checked(self.params["document"])
        before, key = read_license(self.client)
        held = self._holds(before, key)
        if self.document is not None and not held:
            return self._apply(before)
        return self._result(False, before, held=held)

    def _checked(self, document):
        missing = [key for key in DOCUMENT_KEYS if key not in document]
        if missing:
            raise QumuloError(f"document must be the license document Nexus issued for the cluster; it lacks {', '.join(missing)}", changed=False)
        named = str(document["cluster_uuid"])
        if named.lower() != self.cluster_id.lower():
            raise QumuloError(
                f"the license document is for cluster {named}, but the cluster at {self.params['api_host']} is {self.cluster_id}; "
                f"replace it with the license document issued for {self.cluster_id}",
                changed=False,
            )
        return {key: document[key] for key in DOCUMENT_KEYS}

    def _holds(self, facts, key):
        """Active for the cluster's UUID, and, when a document is given, that document's license.

        A license is known by its key. A given document is also compared with what the cluster reports, so an
        altered document under a held key goes to the cluster, which refuses it.
        """
        if facts["state"] != ACTIVE or str(facts.get("cluster_uuid") or "").lower() != self.cluster_id.lower():
            return False
        if self.document is None:
            return True
        predicted = _facts_of(self.document)
        return (key, facts.get("usable_capacity_cap"), facts.get("expiration_date")) == (
            self.document["license_key"],
            predicted["usable_capacity_cap"],
            predicted["expiration_date"],
        )

    def _apply(self, before):
        validate, activate = activation_routes(self.client)
        self._validate(validate, activate)
        if self.module.check_mode:
            predicted = {**before, **_facts_of(self.document)}
            return self._result(True, before, predicted, held=True, activated=True)
        lost = None
        try:
            self.client.post(activate, body=self.document)
        except QumuloMaybeApplied as e:
            lost = e  # settled by the license the cluster reports
        after, key = read_license(self.client)
        if not self._holds(after, key):
            if lost:
                raise lost
            raise QumuloError(
                f"the cluster accepted the license document but reports {after['state']} for {after.get('cluster_uuid') or 'no cluster'}, not that license",
                changed=True,
                cluster_uuid=self.cluster_id,
                license=after,
                license_pending=True,
                license_status="absent",
            )
        return self._result(True, before, after, held=True, activated=True)

    def _validate(self, validate, activate):
        """Ask the cluster whether it takes the document; check mode asks too, since the answer changes nothing."""
        try:
            self.client.post(validate, body=self.document)
        except QumuloApiError as e:
            if e.status == 403:
                raise QumuloError(
                    f"{e}; applying a license document uses {validate.path} and {activate.path}, which need the PRIVILEGE_DEBUG privilege (G-LICENSE)",
                    changed=False,
                ) from None
            if e.status == 404:
                raise QumuloError(
                    f"this Qumulo Core version offers no route to apply a license document ({validate.path} answered 404) (G-LICENSE)",
                    changed=False,
                ) from None
            if e.status == 400:
                raise QumuloError(f"the cluster refused the license document: {e.description or e}", changed=False) from None
            raise

    def _result(self, changed, before, after=None, held=False, activated=False):
        after = before if after is None else after
        result = {
            "changed": changed,
            "cluster_uuid": self.cluster_id,
            "license": after,
            "license_status": "active" if held else "absent",
            "license_pending": not held,
            "activated": activated,
            "needs_quorum_bounce": activated and self._grows_past(after),
            "cap_waits_for_formation": False,
        }
        if held and not activated and self.params.get("check_waiting_cap"):
            total = result["file_system_size_bytes"] = self._file_system_size()
            # An edge formed without a license has the smaller of its platform and EDGE_FLOOR: the window is the second.
            formed_at_the_floor = total is not None and EDGE_FLOOR - FLOOR_MARGIN < total <= EDGE_FLOOR
            result["cap_waits_for_formation"] = formed_at_the_floor and self._grows_past(after, EDGE_FLOOR)
        if self.module._diff:
            shown = ("state", "usable_capacity_cap", "expiration_date")
            result["diff"] = {"before": {key: before.get(key) for key in shown}, "after": {key: after.get(key) for key in shown}}
        return result

    def _file_system_size(self):
        return CLUSTER_INFO_SUBSETS["filesystem"](self.client)["filesystem"].get("total_size_bytes")

    def _grows_past(self, facts, reference=None):
        """Whether the license in facts would grow the file system past reference, its own size by default, at the next
        quorum formation.

        Provisioning follows the smaller of the cap and the clamp, so the file system is read only for a cap, and the
        clamp only when the cap alone would grow it.
        """
        cap = facts.get("usable_capacity_cap")
        if cap is None:
            return False
        if reference is None:
            reference = self._file_system_size()
        if reference is None or not limits_above(reference, cap, None):
            return False
        return limits_above(reference, cap, CLUSTER_INFO_SUBSETS["capacity"](self.client)["capacity_clamp"])


def _cap(document):
    cap = (document.get("details") or {}).get("usable_capacity_cap")
    return None if cap in (None, "") else int(cap)


def _facts_of(document):
    """The license facts a cluster holding document would report."""
    return {
        "state": ACTIVE,
        "cluster_uuid": document["cluster_uuid"],
        "usable_capacity_cap": _cap(document),
        "expiration_date": document.get("expiration_date"),
    }
