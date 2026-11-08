# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""FakeNexus stands in for nexus.NexusTransport in unit tests, as FakeQumulo does for the cluster.

It plays the same strict, ordered script, but answers nothing in the background: every Nexus
request a module sends must be scripted. Unit tests never reach Nexus; without this fake, the
transport fails the test (see the no_real_nexus fixture in conftest.py).
"""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.module_utils import nexus
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import FakeQumulo

NEXUS_TOKEN = "nexus-user-token"
ACCOUNT_ID = 4242


def ok(data, code=200):
    """A successful Nexus response: every one carries the {success, code, data} envelope."""
    return {"success": True, "code": code, "data": data}


class FakeNexus(FakeQumulo):
    def __init__(self):
        super().__init__()
        self.urls = []  # FakeQumulo's log keeps only the path; this keeps the whole URL each request went to

    def __call__(self, method, url, data, headers):
        self.urls.append(url)
        return super().__call__(method, url, data, headers)

    def _background(self, method, path, body):
        return None

    def bearer_tokens(self):
        return [exchange.headers.get("Authorization") for exchange in self.log]


def fake_nexus_session(monkeypatch):
    """Body of the fake_nexus fixture: yields a factory, then verifies every fake it made."""
    fakes = []

    def make():
        fake = FakeNexus()
        monkeypatch.setattr(nexus, "NexusTransport", fake.transport)
        fakes.append(fake)
        return fake

    yield make
    for fake in fakes:
        fake.verify()


def nexus_params(**overrides):
    """Nexus options and the connection's timeouts as AnsibleModule would fill them in, for NexusClient(params)."""
    base = {name: option.get("default") for name, option in nexus.nexus_argument_spec().items()}
    return {**base, "nexus_api_token": NEXUS_TOKEN, "request_timeout": 60, "retry_timeout": 300, **overrides}
