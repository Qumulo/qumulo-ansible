# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.monitoring_info, driven through FakeQumulo. Bodies are as Qumulo Core 7.10.3 serves them."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import MonitoringSettings
from ansible_collections.qumulo.cluster.plugins.modules import monitoring_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SUPPORT = {
    "enabled": True,
    "mq_host": "api.missionq.qumulo.com",
    "mq_port": 443,
    "mq_proxy_host": "",
    "mq_proxy_port": 0,
    "s3_proxy_host": "monitor.qumulo.com",
    "s3_proxy_port": 443,
    "s3_proxy_disable_https": False,
    "vpn_enabled": False,
    "vpn_host": "ep1.qumulo.com",
    "period": 60,
    "nexus_enabled": True,
    "nexus_host": "api.nexus.qumulo.com",
    "nexus_port": 443,
    "nexus_interval": 30,
    "nexus_registration_key": "PENDING-JOIN-KEY",
    "nexus_secret_created_at": None,
}
STATUS = [{"node_id": 1, "monitoring": "CONNECTED", "file_upload": "DISABLED", "vpn_connection": "DISABLED"}]
CONNECTION_SETTINGS = {
    "nexus_enabled": True,
    "nexus_host": "api.nexus.qumulo.com",
    "nexus_port": 443,
    "nexus_interval": 30,
    "nexus_capability_remote_support": True,
    "nexus_capability_sso": False,
}
REGISTRATION = {"nexus_enabled": True, "registration_status": "PENDING_REGISTRATION", "secret_created_at": None}
# nexus_enabled belongs to the nexus_connection subset, so the registration reports only its own fields.
REGISTRATION_FACTS = {"registration_status": "PENDING_REGISTRATION", "secret_created_at": None}
SYSLOG = {"enabled": False, "server_address": "", "server_port": 0, "local_enabled": None, "format": "csv"}
SYSLOG_STATUS = {"connection_status": "AUDIT_LOG_DISABLED", "error_message": "", "error_details": ""}
CLOUDWATCH = {"enabled": True, "log_group_name": "edge01-audit", "region": "us-west-2"}
CLOUDWATCH_STATUS = {"connection_status": "AUDIT_LOG_CONNECTED"}
NOT_ON_THIS_PLATFORM = {"error_class": "audit_to_cloudwatch_platform_error", "description": "not supported on this platform"}
KEY_STORE = {
    "type": "Local",
    "status": "Encrypted",
    "ca_cert_expiry": None,
    "client_cert_expiry": None,
    "last_key_rotation_time": "2026-10-31T02:40:19.357806408Z",
    "last_status_update_time": None,
}


def expect_all(fake, cloudwatch=True):
    fake.expect("GET", "/v1/support/settings", response=SUPPORT, etag='"a"')
    fake.expect("GET", "/v1/support/status/", response=STATUS)
    fake.expect("GET", "/v1/nexus/connection", response=CONNECTION_SETTINGS, etag='"a"')
    fake.expect("GET", "/v1/nexus/registration", response=REGISTRATION, etag='"a"')
    fake.expect("GET", "/v1/audit/syslog/config", response=SYSLOG)
    fake.expect("GET", "/v1/audit/syslog/status", response=SYSLOG_STATUS)
    if cloudwatch:
        fake.expect("GET", "/v1/audit/cloudwatch/config", response=CLOUDWATCH)
        fake.expect("GET", "/v1/audit/cloudwatch/status", response=CLOUDWATCH_STATUS)
    else:
        fake.expect("GET", "/v1/audit/cloudwatch/config", status=403, response=NOT_ON_THIS_PLATFORM)
    fake.expect("GET", "/v2/encryption/key-store/status", response=KEY_STORE)


def test_default_gathers_every_subset(fake_api, run_module):
    fake = fake_api()
    expect_all(fake)

    result = run_module(monitoring_info, CONNECTION)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["monitoring"] == {
        "settings": {key: SUPPORT[key] for key in MonitoringSettings.FIELDS},
        "status": STATUS,
        "nexus_connection": CONNECTION_SETTINGS,
        "nexus_registration": REGISTRATION_FACTS,
        "audit": {
            "syslog": {**SYSLOG, "status": SYSLOG_STATUS},
            "cloudwatch": {**CLOUDWATCH, "status": CLOUDWATCH_STATUS},
        },
        "encryption": KEY_STORE,
    }


def test_the_pending_join_key_and_the_vpn_keys_are_never_read_into_the_result(fake_api, run_module):
    fake = fake_api()
    expect_all(fake)

    result = run_module(monitoring_info, CONNECTION)

    assert "PENDING-JOIN-KEY" not in str(result)
    assert not [e for e in fake.log if "vpn" in e.path]


def test_the_registration_reports_only_its_known_fields(fake_api, run_module):
    """A field a later release adds to the registration, such as a key, is never passed through unseen."""
    fake = fake_api()
    fake.expect("GET", "/v1/nexus/registration", response={**REGISTRATION, "join_key": "LATER-RELEASE-KEY"})

    result = run_module(monitoring_info, {**CONNECTION, "gather_subset": ["nexus_registration"]})

    assert result["monitoring"]["nexus_registration"] == REGISTRATION_FACTS, result.get("msg")


def test_cloudwatch_this_platform_lacks_is_null(fake_api, run_module):
    fake = fake_api()
    expect_all(fake, cloudwatch=False)

    result = run_module(monitoring_info, CONNECTION)

    assert result["monitoring"]["audit"]["cloudwatch"] is None, result.get("msg")
    assert "unavailable_subsets" not in result


@pytest.mark.parametrize("status", [403, 500])
def test_other_cloudwatch_errors_fail(fake_api, run_module, status):
    fake = fake_api()
    fake.expect("GET", "/v1/audit/syslog/config", response=SYSLOG)
    fake.expect("GET", "/v1/audit/syslog/status", response=SYSLOG_STATUS)
    fake.expect("GET", "/v1/audit/cloudwatch/config", status=status, response={"error_class": "api_access_denied_error", "description": "denied"})

    result = run_module(monitoring_info, {**CONNECTION, "gather_subset": ["audit"]})

    assert result["failed"] is True
    assert "api_access_denied_error" in result["msg"]


@pytest.mark.parametrize(
    ("subset", "path", "response"),
    [
        ("settings", "/v1/support/settings", SUPPORT),
        ("status", "/v1/support/status/", STATUS),
        ("nexus_connection", "/v1/nexus/connection", CONNECTION_SETTINGS),
        ("nexus_registration", "/v1/nexus/registration", REGISTRATION),
        ("encryption", "/v2/encryption/key-store/status", KEY_STORE),
    ],
)
def test_one_subset_reads_only_its_route(fake_api, run_module, subset, path, response):
    fake = fake_api()
    fake.expect("GET", path, response=response)

    result = run_module(monitoring_info, {**CONNECTION, "gather_subset": [subset]})

    assert list(result["monitoring"]) == [subset], result.get("msg")
    assert [e.path for e in fake.log if not e.background] == [path]


def test_a_route_this_cluster_does_not_offer_makes_its_subset_unavailable(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v2/encryption/key-store/status", status=404, response={"error_class": "http_not_found_error", "description": "no"})

    result = run_module(monitoring_info, {**CONNECTION, "gather_subset": ["encryption"]})

    assert result["monitoring"] == {}, result.get("msg")
    assert "HTTP 404" in result["unavailable_subsets"]["encryption"]


def test_an_unconfigured_node_has_no_monitoring_to_read(fake_api, run_module):
    fake = fake_api(cluster_id="", state="UNCONFIGURED")

    result = run_module(monitoring_info, {**CONNECTION, "expected_cluster_uuid": ""})

    assert result["monitoring"] == {}, result.get("msg")
    assert sorted(result["unavailable_subsets"]) == ["audit", "encryption", "nexus_connection", "nexus_registration", "settings", "status"]
    assert any("UNCONFIGURED" in w for w in result["warnings"])
    assert [e for e in fake.log if not e.background] == []


def test_check_mode_reads_the_same_and_reports_no_change(fake_api, run_module):
    fake = fake_api()
    expect_all(fake)

    result = run_module(monitoring_info, CONNECTION, check_mode=True)

    assert result["changed"] is False, result.get("msg")
    assert result["monitoring"]["status"] == STATUS
