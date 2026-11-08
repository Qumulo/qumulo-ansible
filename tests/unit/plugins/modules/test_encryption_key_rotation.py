# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.encryption_key_rotation, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import monitoring
from ansible_collections.qumulo.cluster.plugins.modules import encryption_key_rotation
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
KEY_STORE = "/v2/encryption/key-store"
STATUS = "/v2/encryption/key-store/status"
ROTATE = "/v2/encryption/rotate-keys"
NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC)
# The cluster reports nanoseconds, which Python's datetime does not parse on its own.
TEN_DAYS_AGO = "2026-10-23T12:00:00.767044175Z"
JUST_NOW = "2026-11-02T12:00:00.123456789Z"
SEVEN_DAYS_AGO = "2026-10-26T12:00:00Z"
NINE_AND_A_HALF_DAYS_AGO = "2026-10-24T00:00:00Z"
LOCAL = {"type": "Local", "local_store": {"status": "Encrypted"}}
KMS = {"type": "KMS", "kms_store": {"hostname": "kms.example.com", "port": 5696, "key_id": "key-1", "config_creation_time": TEN_DAYS_AGO}}


def status(rotated, kind="Local", state="Encrypted"):
    return {
        "type": kind,
        "status": state,
        "ca_cert_expiry": None,
        "client_cert_expiry": None,
        "last_key_rotation_time": rotated,
        "last_status_update_time": None,
    }


@pytest.fixture(autouse=True)
def now(monkeypatch):
    monkeypatch.setattr(monitoring, "utc_now", lambda: NOW, raising=False)  # the red commit's stub has none


def expect_reads(fake, store, rotated, kind="Local"):
    fake.expect("GET", KEY_STORE, response=store)
    fake.expect("GET", STATUS, response=status(rotated, kind))


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_a_key_younger_than_the_age_is_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 30})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["key_store"] == {"type": "Local", "last_key_rotation_time": TEN_DAYS_AGO}
    assert writes(fake) == []


def test_a_key_older_than_the_age_is_rotated_and_the_new_time_read_back(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)
    fake.expect("POST", ROTATE, body={})
    expect_reads(fake, LOCAL, JUST_NOW)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["rotation"] == {"key_store": "Local", "last_key_rotation_time": TEN_DAYS_AGO, "age_days": 10.0}
    assert result["key_store"] == {"type": "Local", "last_key_rotation_time": JUST_NOW}


def test_check_mode_predicts_the_rotation_without_sending_it(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["rotation"]["age_days"] == 10.0
    assert result["diff"]["after"] == result["rotation"]
    assert writes(fake) == []


def test_zero_days_rotates_a_key_rotated_moments_ago(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, "2026-11-02T11:59:59Z")
    fake.expect("POST", ROTATE, body={})
    expect_reads(fake, LOCAL, JUST_NOW)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 0})

    assert result["changed"] is True
    assert result["rotation"] == {"key_store": "Local", "last_key_rotation_time": "2026-11-02T11:59:59Z", "age_days": 0.0}


def test_a_key_exactly_as_old_as_the_age_is_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, SEVEN_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert result["changed"] is False


def test_the_age_is_reported_to_two_decimals(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, NINE_AND_A_HALF_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 9}, check_mode=True)

    assert result["rotation"]["age_days"] == 9.5


def test_a_key_never_rotated_is_due(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, None)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 365}, check_mode=True)

    assert result["changed"] is True
    assert result["rotation"] == {"key_store": "Local", "last_key_rotation_time": None, "age_days": None}


def test_a_rotation_the_cluster_does_not_show_fails_as_changed(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)
    fake.expect("POST", ROTATE, body={})
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "still reports the key rotated at" in result["msg"]


def test_a_lost_rotation_is_confirmed_by_reading_back(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)
    fake.expect("POST", ROTATE, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    expect_reads(fake, LOCAL, JUST_NOW)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["notes"] == ["rotation confirmed by reading back after a lost connection"]


def test_a_lost_rotation_that_did_not_happen_is_reported_and_never_resent(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)
    fake.expect("POST", ROTATE, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert [e.method for e in writes(fake)] == ["POST"]


def test_an_unencrypted_cluster_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", KEY_STORE, response={"type": "Local", "local_store": {"status": "Not Encrypted"}})

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert result["failed"] is True
    assert "not encrypted at rest" in result["msg"]
    assert writes(fake) == []


def test_a_key_id_with_the_cluster_s_own_key_store_fails_before_any_write(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, LOCAL, TEN_DAYS_AGO)

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7, "key_id": "key-2"})

    assert result["failed"] is True
    assert "key_id needs a KMS key store" in result["msg"]
    assert writes(fake) == []


def test_a_new_kms_key_id_is_rotated_to_whatever_the_age(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, JUST_NOW, kind="KMS")
    fake.expect("POST", ROTATE, body={"key_id": "key-2"})
    expect_reads(fake, {"type": "KMS", "kms_store": {**KMS["kms_store"], "key_id": "key-2"}}, JUST_NOW, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 365, "key_id": "key-2"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["rotation"]["key_id"] == "key-2"
    assert result["key_store"] == {"type": "KMS", "key_id": "key-2", "last_key_rotation_time": JUST_NOW}


def test_the_kms_key_in_use_is_left_alone_whatever_the_age(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, TEN_DAYS_AGO, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7, "key_id": "key-1"})

    assert result["changed"] is False
    assert writes(fake) == []


def test_a_kms_rotation_the_cluster_does_not_show_fails_as_changed(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, JUST_NOW, kind="KMS")
    fake.expect("POST", ROTATE, body={"key_id": "key-2"})
    expect_reads(fake, KMS, JUST_NOW, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 365, "key_id": "key-2"})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "with key key-1" in result["msg"]


def test_a_lost_kms_rotation_that_did_not_happen_is_reported_and_never_resent(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, JUST_NOW, kind="KMS")
    fake.expect("POST", ROTATE, body={"key_id": "key-2"}, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    expect_reads(fake, KMS, JUST_NOW, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 365, "key_id": "key-2"})

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert [e.method for e in writes(fake)] == ["POST"]


def test_a_kms_key_never_rotated_is_aged_from_its_configuration(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, None, kind="KMS")  # KMS's config_creation_time is ten days ago

    young = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 30})

    assert not young.get("failed"), young.get("msg")
    assert young["changed"] is False


def test_a_kms_key_is_aged_from_its_configuration_when_that_is_later_than_its_rotation(fake_api, run_module):
    fake = fake_api()
    store = {"type": "KMS", "kms_store": {**KMS["kms_store"], "config_creation_time": SEVEN_DAYS_AGO}}
    expect_reads(fake, store, TEN_DAYS_AGO, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 8})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_the_plan_says_a_kms_key_never_rotated_is_aged_from_its_configuration(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, None, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7, "key_id": "key-2"}, check_mode=True)

    assert result["rotation"] == {"key_store": "KMS", "last_key_rotation_time": None, "age_days": 10.0, "aged_from": "configuration", "key_id": "key-2"}


def test_a_configuration_time_the_module_cannot_read_fails_naming_it(fake_api, run_module):
    fake = fake_api()
    store = {"type": "KMS", "kms_store": {**KMS["kms_store"], "config_creation_time": "yesterday"}}
    expect_reads(fake, store, None, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7})

    assert result["failed"] is True
    assert "the KMS configuration's creation time as 'yesterday'" in result["msg"]


def test_an_old_kms_key_never_rotated_fails_naming_its_age(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, None, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7}, check_mode=True)

    assert result["failed"] is True
    assert "10.0 days" in result["msg"] and "None" not in result["msg"]


def test_an_old_kms_key_without_a_new_key_id_fails(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, TEN_DAYS_AGO, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 7}, check_mode=True)

    assert result["failed"] is True
    assert "G-MON-KMS-ROTATE" in result["msg"] and "key_id" in result["msg"]
    assert writes(fake) == []


def test_a_young_kms_key_without_a_key_id_is_left_alone(fake_api, run_module):
    fake = fake_api()
    expect_reads(fake, KMS, TEN_DAYS_AGO, kind="KMS")

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": 30})

    assert result["changed"] is False


def test_a_negative_age_fails_before_any_request(fake_api, run_module):
    fake = fake_api()

    result = run_module(encryption_key_rotation, {**CONNECTION, "max_age_days": -1})

    assert result["failed"] is True
    assert "max_age_days" in result["msg"]
    assert fake.log == []
