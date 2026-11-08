# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.audit_destination, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import audit_destination
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
DESTINATIONS = "/v2/audit/destinations/"
SIEM = "/v2/audit/destinations/1"
NULLS = {"server_address": None, "server_port": None, "log_format": None, "log_group_name": None, "region": None}


def syslog(name="siem", enabled=True, address="siem.example.com", port=514, log_format="csv"):
    return {**NULLS, "type": "SYSLOG", "name": name, "enabled": enabled, "server_address": address, "server_port": port, "log_format": log_format}


def cloudwatch(name="audit-cw", enabled=True, group="qumulo-audit", region="us-east-1"):
    return {**NULLS, "type": "CLOUDWATCH", "name": name, "enabled": enabled, "log_group_name": group, "region": region}


def listing(*configs):
    return {"destinations": [{"id": str(number), "config": config} for number, config in enumerate(configs, start=1)]}


def expect_found(fake, config, etag='"AQAAAA"', others=()):
    fake.expect("GET", DESTINATIONS, response=listing(config, *others))
    fake.expect("GET", SIEM, response={"id": "1", "config": config}, etag=etag)


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


SIEM_OPTIONS = {"name": "siem", "type": "SYSLOG", "server_address": "siem.example.com", "server_port": 514}


def test_a_missing_destination_is_created_with_its_config_wrapped(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing(syslog("other")))
    body = {"config": {"type": "SYSLOG", "name": "siem", "enabled": True, "server_address": "siem.example.com", "server_port": 514}}
    fake.expect("POST", DESTINATIONS, body=body, response={"id": "2", "config": syslog()})
    fake.expect("GET", DESTINATIONS, response=listing(syslog("other"), syslog()))
    fake.expect("GET", "/v2/audit/destinations/2", response={"id": "2", "config": syslog()}, etag='"AQAAAA"')

    result = run_module(audit_destination, {**CONNECTION, **SIEM_OPTIONS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["audit_destination"] == {**SIEM_OPTIONS, "enabled": True, "log_format": "csv"}


def test_the_same_destination_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog())

    result = run_module(audit_destination, {**CONNECTION, **SIEM_OPTIONS, "enabled": True, "log_format": "csv"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


# QUIRK-062: a PATCH changes only enabled, so every change is a PUT of the whole configuration.
def test_a_change_puts_the_whole_configuration_with_if_match_never_a_patch(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog(log_format="json"), etag='"AwAAAA"')
    body = syslog(port=601, log_format="json")  # the cluster's whole configuration, the other type's nulls included
    fake.expect("PUT", SIEM, body=body, if_match='"AwAAAA"', response={"id": "1", "config": syslog(port=601, log_format="json")})
    expect_found(fake, syslog(port=601, log_format="json"), etag='"BAAAAA"')

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "server_port": 601})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["audit_destination"]["server_port"] == 601


def test_a_field_the_module_does_not_model_is_sent_back_as_the_cluster_holds_it(fake_api, run_module):
    held = {**syslog(), "tls_enabled": True}  # a field a later release might add
    fake = fake_api()
    expect_found(fake, held)
    fake.expect("PUT", SIEM, body={**held, "enabled": False}, response={"id": "1", "config": {**held, "enabled": False}})
    expect_found(fake, {**held, "enabled": False})

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "enabled": False})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_change_of_type_drops_the_old_type_s_options(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog(name="audit-cw"))
    body = cloudwatch()  # the syslog options null
    fake.expect("PUT", SIEM, body=body, response={"id": "1", "config": cloudwatch()})
    expect_found(fake, cloudwatch())

    result = run_module(audit_destination, {**CONNECTION, "name": "audit-cw", "type": "CLOUDWATCH", "log_group_name": "qumulo-audit", "region": "us-east-1"})

    assert not result.get("failed"), result.get("msg")
    assert result["audit_destination"] == {"name": "audit-cw", "type": "CLOUDWATCH", "enabled": True, "log_group_name": "qumulo-audit", "region": "us-east-1"}


def test_check_mode_and_diff_show_a_new_destination_without_writing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing())

    result = run_module(audit_destination, {**CONNECTION, **SIEM_OPTIONS}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": {}, "after": {**SIEM_OPTIONS, "enabled": True}}
    assert writes(fake) == []


def test_absent_deletes_with_if_match(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog(), etag='"BQAAAA"')
    fake.expect("DELETE", SIEM, if_match='"BQAAAA"')

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "state": "absent"})

    assert result["changed"] is True
    assert result["audit_destination"] == {}


def test_absent_when_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing(syslog("other")))

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "state": "absent"})

    assert result["changed"] is False
    assert writes(fake) == []


def test_creating_without_a_type_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing())

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "server_address": "siem.example.com"})

    assert result["failed"] is True
    assert "set type to create it" in result["msg"]
    assert writes(fake) == []


def test_an_option_of_another_type_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog())

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "region": "us-east-1"})

    assert result["failed"] is True
    assert "region applies only to CLOUDWATCH" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize(
    "name, kind, why",
    [
        ("local", "SYSLOG", "'local' is reserved for a LOCAL destination"),
        ("syslog", "CLOUDWATCH", "'syslog' is reserved for a SYSLOG destination"),
        ("audit-local", "LOCAL", "a LOCAL destination must be named 'local'"),
    ],
)
def test_a_reserved_name_of_another_type_fails_before_any_request(fake_api, run_module, name, kind, why):
    fake = fake_api()

    result = run_module(audit_destination, {**CONNECTION, "name": name, "type": kind})

    assert result["failed"] is True
    assert why in result["msg"]
    assert fake.log == []


CW_OPTIONS = {"name": "audit-cw", "type": "CLOUDWATCH", "log_group_name": "qumulo-audit", "region": "us-east-1"}
# Either area hands the CloudWatch destination to Terraform; the floating IPs beside them change nothing here.
OWNED_AREAS = pytest.mark.parametrize("owned", [["network.floating_ips", "audit.cloudwatch"], ["audit"]], ids=["audit.cloudwatch", "audit"])


@OWNED_AREAS
@pytest.mark.parametrize("check_mode", [False, True])
def test_creating_a_cloudwatch_destination_terraform_owns_is_refused(fake_api, run_module, owned, check_mode):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing())

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": owned, **CW_OPTIONS}, check_mode=check_mode)

    assert result["failed"] is True
    assert "terraform_owned_areas" in result["msg"] and "take_over_from_terraform" in result["msg"]
    assert writes(fake) == []


@OWNED_AREAS
def test_changing_a_cloudwatch_destination_terraform_owns_is_refused(fake_api, run_module, owned):
    fake = fake_api()
    expect_found(fake, cloudwatch(name="audit-cw"))

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": owned, **CW_OPTIONS, "region": "us-west-2"})

    assert result["failed"] is True
    assert "take_over_from_terraform" in result["msg"]
    assert writes(fake) == []


@OWNED_AREAS
def test_changing_a_syslog_destination_to_cloudwatch_terraform_owns_is_refused(fake_api, run_module, owned):
    fake = fake_api()
    expect_found(fake, syslog(name="audit-cw"))

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": owned, **CW_OPTIONS})

    assert result["failed"] is True
    assert "take_over_from_terraform" in result["msg"]
    assert writes(fake) == []


@OWNED_AREAS
def test_deleting_a_cloudwatch_destination_terraform_owns_is_refused(fake_api, run_module, owned):
    fake = fake_api()
    expect_found(fake, cloudwatch())

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": owned, "name": "audit-cw", "state": "absent"}, check_mode=True)

    assert result["failed"] is True
    assert "take_over_from_terraform" in result["msg"]
    assert writes(fake) == []


@OWNED_AREAS
def test_a_cloudwatch_destination_terraform_owns_that_would_not_change_passes(fake_api, run_module, owned):
    fake = fake_api()
    expect_found(fake, cloudwatch(name="audit-cw"))

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": owned, **CW_OPTIONS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


@OWNED_AREAS
def test_absent_for_a_missing_cloudwatch_destination_terraform_owns_passes(fake_api, run_module, owned):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing())

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": owned, **CW_OPTIONS, "state": "absent"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_a_syslog_destination_is_not_terraform_s(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog(port=514))
    body = syslog(port=601)
    fake.expect("PUT", SIEM, body=body, response={"id": "1", "config": syslog(port=601)})
    expect_found(fake, syslog(port=601))

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": ["audit.cloudwatch"], **SIEM_OPTIONS, "server_port": 601})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize("state", ["present", "absent"])
def test_owning_audit_refuses_a_syslog_write(fake_api, run_module, state):
    fake = fake_api()
    expect_found(fake, syslog(port=514))

    options = {**SIEM_OPTIONS, "server_port": 601} if state == "present" else {"name": "siem", "state": "absent"}
    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": ["audit"], **options}, check_mode=True)

    assert result["failed"] is True
    assert "would write an audit destination, and terraform_owned_areas gives audit to Terraform" in result["msg"]
    assert writes(fake) == []


def test_owning_audit_passes_a_syslog_destination_that_would_not_change(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog())

    result = run_module(audit_destination, {**CONNECTION, "terraform_owned_areas": ["audit"], **SIEM_OPTIONS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_take_over_writes_a_syslog_destination_while_audit_is_owned(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, syslog(port=514))
    fake.expect("PUT", SIEM, body=syslog(port=601), response={"id": "1", "config": syslog(port=601)})
    expect_found(fake, syslog(port=601))

    owned = {"terraform_owned_areas": ["audit"], "take_over_from_terraform": True}
    result = run_module(audit_destination, {**CONNECTION, **owned, **SIEM_OPTIONS, "server_port": 601})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_destination_of_a_type_the_module_does_not_know_fails_naming_it(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**syslog(), "type": "KAFKA"})

    result = run_module(audit_destination, {**CONNECTION, "name": "siem", "enabled": False})

    assert result["failed"] is True
    assert "has type KAFKA, which this module does not know" in result["msg"]
    assert writes(fake) == []


def test_take_over_changes_a_cloudwatch_destination_terraform_owns(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, cloudwatch(name="audit-cw"))
    body = cloudwatch(region="us-west-2")
    fake.expect("PUT", SIEM, body=body, response={"id": "1", "config": cloudwatch(region="us-west-2")})
    expect_found(fake, cloudwatch(region="us-west-2"))

    owned = {"terraform_owned_areas": ["audit.cloudwatch"], "take_over_from_terraform": True}
    result = run_module(audit_destination, {**CONNECTION, **owned, **CW_OPTIONS, "region": "us-west-2"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_more_than_one_destination_with_the_name_fails_naming_them(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing(syslog(), syslog(port=601)))

    result = run_module(audit_destination, {**CONNECTION, **SIEM_OPTIONS}, check_mode=True)

    assert result["failed"] is True
    assert "names more than one destination (1, 2)" in result["msg"]
    assert writes(fake) == []


def test_a_lost_create_is_confirmed_by_reading_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing())
    fake.expect("POST", DESTINATIONS, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    expect_found(fake, syslog())

    result = run_module(audit_destination, {**CONNECTION, **SIEM_OPTIONS})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["notes"] == ["write reconciled after a lost connection"]


def test_the_cluster_s_refusal_fails_with_its_message(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", DESTINATIONS, response=listing())
    refusal = {"error_class": "audit_to_cloudwatch_platform_error", "description": "Sending audit logs to CloudWatch is not supported on this platform."}
    fake.expect("POST", DESTINATIONS, status=403, response=refusal)

    result = run_module(audit_destination, {**CONNECTION, **CW_OPTIONS})

    assert result["failed"] is True
    assert "not supported on this platform" in result["msg"]
