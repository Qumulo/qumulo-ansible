# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

import pytest
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.testing import patch_module_args
from ansible_collections.qumulo.cluster.plugins.module_utils import client, nexus
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_nexus import fake_nexus_session
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import fake_api_session


@pytest.fixture
def fake_api(monkeypatch):
    """fake_api(version=..., cluster_id=..., state=...) installs a FakeQumulo; unused expectations fail at teardown."""
    yield from fake_api_session(monkeypatch)


@pytest.fixture
def fake_nexus(monkeypatch):
    """fake_nexus() installs a FakeNexus in place of the Nexus transport; unused expectations fail at teardown."""
    yield from fake_nexus_session(monkeypatch)


@pytest.fixture(autouse=True)
def no_real_nexus(monkeypatch):
    """Unit tests never reach Nexus: a test that sends a Nexus request without fake_nexus fails."""

    def refuse(params):
        pytest.fail("a unit test tried to reach Nexus; script the exchange with the fake_nexus fixture")

    monkeypatch.setattr(nexus, "NexusTransport", refuse)


class FakeClock:
    """Virtual time: sleep() returns at once and moves monotonic() forward."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(client.QumuloClient, "sleep", staticmethod(clock.sleep), raising=False)
    monkeypatch.setattr(client.QumuloClient, "clock", staticmethod(clock.monotonic), raising=False)
    monkeypatch.setattr(nexus.NexusClient, "sleep", staticmethod(clock.sleep), raising=False)
    monkeypatch.setattr(nexus.NexusClient, "clock", staticmethod(clock.monotonic), raising=False)
    return clock


class ModuleExit(Exception):
    """Raised in place of AnsibleModule.exit_json and fail_json; carries the result."""

    def __init__(self, result):
        super().__init__(result)
        self.result = result


@pytest.fixture
def run_module(monkeypatch):
    """run_module(module_or_main, args, check_mode=False, diff=False) returns the exit_json or fail_json result."""

    def warn(self, msg):
        self.__dict__.setdefault("_test_warnings", []).append(msg)

    def exit_json(self, **result):
        raise ModuleExit({**result, "warnings": self.__dict__.get("_test_warnings", [])})

    def fail_json(self, msg, **result):
        raise ModuleExit({"failed": True, "msg": msg, **result, "warnings": self.__dict__.get("_test_warnings", [])})

    monkeypatch.setattr(AnsibleModule, "warn", warn)
    monkeypatch.setattr(AnsibleModule, "exit_json", exit_json)
    monkeypatch.setattr(AnsibleModule, "fail_json", fail_json)

    def run(module, args, check_mode=False, diff=False):
        main = getattr(module, "main", module)
        with patch_module_args({**args, "_ansible_check_mode": check_mode, "_ansible_diff": diff}):
            try:
                main()
            except ModuleExit as done:
                return done.result
        pytest.fail("the module returned without calling exit_json or fail_json")

    return run


@pytest.fixture
def node_state_budgets(monkeypatch):
    """The retry_timeout of each QumuloClient.node_state() call, in order, recorded as the calls go through."""
    budgets = []
    node_state = client.QumuloClient.node_state

    def recording(self, retry_timeout=None):
        budgets.append(retry_timeout)
        return node_state(self, retry_timeout=retry_timeout)

    monkeypatch.setattr(client.QumuloClient, "node_state", recording)
    return budgets
