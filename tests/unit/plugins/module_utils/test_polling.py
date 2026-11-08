# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The polling base: a wait's deadline and polls, and a failure after an earlier write of the same task."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloClient, QumuloError, QumuloMaybeApplied, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.polling import POLL_INTERVAL, UNANSWERED, PollingResource
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, client_params

THING = Route("/v1/things/{id}", ("GET",))


def resource(wait_timeout=12):
    module = SimpleNamespace(params={"wait_timeout": wait_timeout}, check_mode=False, _diff=False)
    return PollingResource(module, QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID)))


def test_pause_sleeps_in_steps_until_wait_timeout_has_passed(fake_clock):
    waiting = resource(wait_timeout=12)

    paused = [waiting._pause() for _attempt in range(4)]

    assert paused == [True, True, True, False]
    assert fake_clock.sleeps == [POLL_INTERVAL, POLL_INTERVAL, 2.0]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"id": "7", "state": "ESTABLISHED"}, {"id": "7", "state": "ESTABLISHED"}),
        (404, {"error_class": "relationship_manager_relationship_not_found_error"}, None),
        (503, {"error_class": "service_unavailable_error"}, UNANSWERED),
    ],
    ids=["record", "gone", "unanswered"],
)
def test_a_poll_reads_once_without_client_retry(fake_api, status, body, expected):
    fake = fake_api()
    fake.expect("GET", "/v1/things/7", status=status, response=body)

    assert resource()._poll(THING, id="7") == expected


def test_a_poll_refused_for_another_reason_fails_rather_than_waiting(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/things/7", status=403, response={"error_class": "permission_denied_error"})

    with pytest.raises(QumuloApiError, match="permission_denied_error"):
        resource()._poll(THING, id="7")


def test_a_poll_the_cluster_does_not_answer_is_unanswered(fake_api):
    fake = fake_api()
    fake.expect("GET", "/v1/things/7", raises=ConnectionRefusedError())

    assert resource()._poll(THING, id="7") is UNANSWERED


def test_a_failure_after_an_earlier_write_reports_changed_and_names_it():
    writing = resource()
    writing.done.append("created the thing")

    with pytest.raises(QumuloError, match="boom; this task had already created the thing") as raised, writing._after_writes():
        raise QumuloError("boom", status=400)

    assert raised.value.details == {"status": 400, "changed": True}


def test_a_failure_before_any_write_passes_through():
    writing = resource()

    with pytest.raises(QumuloError, match="^boom$") as raised, writing._after_writes():
        raise QumuloError("boom")

    assert "changed" not in raised.value.details


def test_a_lost_write_passes_through_for_run_to_read_back():
    writing = resource()
    writing.done.append("created the thing")

    with pytest.raises(QumuloMaybeApplied, match="^lost$"), writing._after_writes():
        raise QumuloMaybeApplied("lost")
