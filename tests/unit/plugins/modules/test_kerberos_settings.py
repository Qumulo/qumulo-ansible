# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.kerberos_settings, driven through FakeQumulo. Bodies follow the OpenAPI document's kerberos_config."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.modules import kerberos_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SETTINGS = "/v1/auth/kerberos-settings"
OFF = {"use_alt_security_identities_mapping": False}
ON = {"use_alt_security_identities_mapping": True}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_no_option_reads_and_changes_nothing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')

    result = run_module(kerberos_settings, CONNECTION)

    assert result["changed"] is False, result.get("msg")
    assert result["kerberos_settings"] == OFF
    assert writes(fake) == []


def test_matching_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=ON, etag='"WllTVQ"')

    result = run_module(kerberos_settings, {**CONNECTION, **ON})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_a_change_puts_the_whole_document_with_the_read_s_if_match(fake_api, run_module):
    """PATCH answers 405 and a PUT without the field answers 400, so the document goes whole (research R4)."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, body=ON, if_match='"AAAAAA"', response=ON)
    fake.expect("GET", SETTINGS, response=ON, etag='"WllTVQ"')

    result = run_module(kerberos_settings, {**CONNECTION, **ON}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["kerberos_settings"] == ON
    assert result["diff"] == {"before": OFF, "after": ON}


def test_a_412_reads_again_and_writes_with_the_new_etag(fake_api, run_module):
    """The settings' ETag also moves with the keytab, so another writer's keytab change gives a 412."""
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, body=ON, if_match='"AAAAAA"', status=412, response={"error_class": "db_revision_mismatch_error"})
    fake.expect("GET", SETTINGS, response=OFF, etag='"vP2ZNQ"')
    fake.expect("PUT", SETTINGS, body=ON, if_match='"vP2ZNQ"', response=ON)
    fake.expect("GET", SETTINGS, response=ON, etag='"nv5TGA"')

    result = run_module(kerberos_settings, {**CONNECTION, **ON})

    assert result["changed"] is True, result.get("msg")


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=ON, etag='"WllTVQ"')

    result = run_module(kerberos_settings, {**CONNECTION, **OFF}, check_mode=True)

    assert result["changed"] is True
    assert result["kerberos_settings"] == OFF
    assert writes(fake) == []


def test_a_read_back_that_differs_fails_with_expected_and_observed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, body=ON, response=ON)
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')

    result = run_module(kerberos_settings, {**CONNECTION, **ON})

    assert result["failed"] is True
    assert result["changed"] is True
    assert (result["expected"], result["observed"]) == (ON, OFF)
