# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The base of resources that wait for the server, or send more than one write in one task.

A wait owns its timing: it reads with no client retry, sleeps between reads until wait_timeout has passed, and treats
a read the cluster did not answer as one more poll. A write that fails after an earlier write of the same task still
reports changed, naming what the task had already done.
"""

from __future__ import annotations

from contextlib import contextmanager

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, QumuloMaybeApplied, QumuloUnreachable
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Resource

POLL_INTERVAL = 5.0
UNANSWERED = object()  # a poll the cluster did not answer, as while a change briefly takes it away


class Waits:
    """The timing of one task's waits: a deadline, the polls, and the record of what the task's writes have done.

    restart() sets a new deadline, for a task that waits in phases, each with a wait_timeout of its own.
    """

    def __init__(self, client, timeout):
        self.client = client
        self.done = []  # what this task's writes have changed so far, for a request that fails after them
        self.restart(timeout)

    def restart(self, timeout):
        self.timeout = timeout
        self.deadline = self.client.clock() + timeout

    def pause(self, interval=POLL_INTERVAL):
        """Sleep before the next poll, at most interval; False once the deadline has passed."""
        left = max(0.0, self.deadline - self.client.clock())
        if left <= 0:
            return False
        self.client.sleep(min(interval, left))
        return True

    def poll(self, route, **params):
        """One read for a wait: the record, None once it is gone, or UNANSWERED.

        The wait owns the timing, so the client does not retry the read.
        """
        try:
            found = self.client.get_or_none(route, params=params, retry_timeout=0)
        except QumuloUnreachable:
            return UNANSWERED
        except QumuloApiError as e:
            if e.status >= 500:
                return UNANSWERED
            raise
        return None if found is None else found.data

    @contextmanager
    def after_writes(self):
        """A request that fails after an earlier write of this task still leaves the cluster changed, by self.done."""
        try:
            yield
        except QumuloMaybeApplied:
            raise  # the caller reads back after a lost write
        except QumuloError as e:
            if not self.done:
                raise
            raise QumuloError(f"{e}; this task had already {', '.join(self.done)}", **{**e.details, "changed": True}) from None


class PollingResource(Resource):
    """A Resource with a deadline from wait_timeout, the polls of a wait, and the record of what its writes have done."""

    def __init__(self, module, client):
        super().__init__(module, client)
        self.waits = Waits(client, self.params["wait_timeout"])

    @property
    def timeout(self):
        return self.waits.timeout

    @property
    def deadline(self):
        return self.waits.deadline

    @property
    def done(self):
        return self.waits.done

    def _pause(self):
        """Sleep before the next poll; False once wait_timeout has passed."""
        return self.waits.pause()

    def _poll(self, route, **params):
        return self.waits.poll(route, **params)

    def _after_writes(self):
        return self.waits.after_writes()
