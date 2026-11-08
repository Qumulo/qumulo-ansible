# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.ftp_info, driven through FakeQumulo. Bodies follow the OpenAPI document's FTP schemas."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import ftp_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = {
    "enabled": True,
    "check_remote_host": True,
    "log_operations": True,
    "chroot_users": False,
    "allow_unencrypted_connections": True,
    "expand_wildcards": False,
    "anonymous_user": {"id_type": "LOCAL_USER", "id_value": "ftp-guest"},
    "greeting": "Welcome.\n",
}
OPTIONS = {**SETTINGS, "anonymous_user": "local:ftp-guest"}
STATUS = {"settings": SETTINGS, "statuses": [{"node_id": 2, "status": "running"}, {"node_id": 1, "status": "stopped"}]}


def test_all_gives_the_settings_in_the_modules_shape_and_each_nodes_status(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v0/ftp/settings", SETTINGS)
    fake.serve("/v0/ftp/status", STATUS)

    result = run_module(ftp_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    # The status answer's copy of the settings is dropped, and the nodes keep the cluster's order.
    assert result["ftp"] == {"settings": OPTIONS, "status": [{"node_id": 2, "status": "running"}, {"node_id": 1, "status": "stopped"}]}


def test_the_status_subset_reads_only_the_status(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v0/ftp/status", STATUS)

    result = run_module(ftp_info, {**CONNECTION, "gather_subset": ["status"]})

    assert list(result["ftp"]) == ["status"]
    assert "/v0/ftp/settings" not in [e.path for e in fake.log]


def test_no_anonymous_user_reads_as_empty(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v0/ftp/settings", {**SETTINGS, "anonymous_user": None})

    result = run_module(ftp_info, {**CONNECTION, "gather_subset": ["settings"]})

    assert result["ftp"]["settings"]["anonymous_user"] == ""


def test_a_cluster_that_does_not_offer_ftp_reports_the_subsets_unavailable(fake_api, run_module):
    fake = fake_api()
    fake.serve("/v0/ftp/settings", {"error_class": "http_not_found_error", "description": "no such route"}, status=404)
    fake.serve("/v0/ftp/status", {"error_class": "http_not_found_error", "description": "no such route"}, status=404)

    result = run_module(ftp_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["ftp"] == {}
    assert sorted(result["unavailable_subsets"]) == ["settings", "status"]


def test_a_local_group_never_reads_as_a_local_user(fake_api, run_module):
    """The cluster refuses to store a group today; if it ever did, the group must not compare equal to a user."""
    fake = fake_api()
    fake.serve("/v0/ftp/settings", {**SETTINGS, "anonymous_user": {"id_type": "LOCAL_GROUP", "id_value": "Users"}})

    result = run_module(ftp_info, {**CONNECTION, "gather_subset": ["settings"]})

    assert result["ftp"]["settings"]["anonymous_user"] == "local_group:Users"
