# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.ssl_ciphers, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import ssl_ciphers
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
CIPHERS = "/v2/cluster/settings/ssl/ciphers"
FACTORY = {
    "tls_1_2": ["ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-GCM-SHA256", "ECDHE-RSA-AES256-SHA384", "ECDHE-RSA-AES128-SHA256"],
    "tls_1_3": ["TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256"],
}
GCM_ONLY = ["ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-GCM-SHA256"]


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_the_cluster_s_own_lists_are_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=FACTORY, etag='"4Hu11w"')

    result = run_module(ssl_ciphers, {**CONNECTION, **FACTORY})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["ssl_ciphers"] == FACTORY
    assert writes(fake) == []


def test_a_new_list_is_put_whole_with_if_match_and_read_back(fake_api, run_module):
    stored = {**FACTORY, "tls_1_2": [*GCM_ONLY, "ECDHE-RSA-AES128-SHA256"]}
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=FACTORY, etag='"4Hu11w"')
    fake.expect("PUT", CIPHERS, body=stored, if_match='"4Hu11w"', response=stored)
    fake.expect("GET", CIPHERS, response=stored, etag='"l0aE2A"')

    result = run_module(ssl_ciphers, {**CONNECTION, "tls_1_2": GCM_ONLY})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["ssl_ciphers"] == stored


# QUIRK-060: the cluster adds its required suite to the end of a list that leaves it out.
def test_a_list_without_the_required_suite_compares_with_it_added_at_the_end(fake_api, run_module):
    stored = {"tls_1_2": [*GCM_ONLY, "ECDHE-RSA-AES128-SHA256"], "tls_1_3": ["TLS_AES_256_GCM_SHA384", "TLS_AES_128_GCM_SHA256"]}
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=stored, etag='"l0aE2A"')

    result = run_module(ssl_ciphers, {**CONNECTION, "tls_1_2": GCM_ONLY, "tls_1_3": ["TLS_AES_256_GCM_SHA384"]})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["ssl_ciphers"] == stored


def test_the_order_is_the_setting(fake_api, run_module):
    reordered = {**FACTORY, "tls_1_3": list(reversed(FACTORY["tls_1_3"]))}
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=FACTORY, etag='"4Hu11w"')
    fake.expect("PUT", CIPHERS, body=reordered, if_match='"4Hu11w"', response=reordered)
    fake.expect("GET", CIPHERS, response=reordered, etag='"5"')

    result = run_module(ssl_ciphers, {**CONNECTION, "tls_1_3": reordered["tls_1_3"]})

    assert result["changed"] is True
    assert result["ssl_ciphers"] == reordered


def test_the_required_suite_placed_by_the_task_stays_where_it_is(fake_api, run_module):
    wanted = ["TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384"]
    stored = {**FACTORY, "tls_1_3": wanted}
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=FACTORY, etag='"4Hu11w"')
    fake.expect("PUT", CIPHERS, body=stored, response=stored)
    fake.expect("GET", CIPHERS, response=stored, etag='"5"')

    result = run_module(ssl_ciphers, {**CONNECTION, "tls_1_3": wanted})

    assert result["ssl_ciphers"]["tls_1_3"] == wanted


def test_check_mode_and_diff_show_the_lists_without_writing(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=FACTORY, etag='"4Hu11w"')

    result = run_module(ssl_ciphers, {**CONNECTION, "tls_1_2": GCM_ONLY}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": FACTORY, "after": {**FACTORY, "tls_1_2": [*GCM_ONLY, "ECDHE-RSA-AES128-SHA256"]}}
    assert writes(fake) == []


# QUIRK-061: the cluster takes an empty list as the one suite it adds, so the module refuses it.
@pytest.mark.parametrize("version", ["tls_1_2", "tls_1_3"])
def test_an_empty_list_fails_before_any_request(fake_api, run_module, version):
    fake = fake_api()

    result = run_module(ssl_ciphers, {**CONNECTION, version: []}, check_mode=True)

    assert result["failed"] is True
    assert f"{version} is empty" in result["msg"]
    assert fake.log == []


@pytest.mark.parametrize("version, suites", [("tls_1_2", GCM_ONLY + GCM_ONLY[:1]), ("tls_1_3", ["TLS_AES_256_GCM_SHA384"] * 2)])
def test_a_suite_named_twice_fails_before_any_request(fake_api, run_module, version, suites):
    fake = fake_api()

    result = run_module(ssl_ciphers, {**CONNECTION, version: suites})

    assert result["failed"] is True
    assert f"{version} names {suites[0]} twice" in result["msg"]
    assert fake.log == []


def test_a_list_the_cluster_refuses_fails_with_its_message(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", CIPHERS, response=FACTORY, etag='"4Hu11w"')
    refusal = {
        "error_class": "http_bad_request_error",
        "description": "TLS 1.2 cipher ALL offers ECDHE-ECDSA-RC4-SHA, which is not allowed: RC4 and anonymous suites are refused",
    }
    fake.expect("PUT", CIPHERS, status=400, response=refusal)

    result = run_module(ssl_ciphers, {**CONNECTION, "tls_1_2": ["ALL"]})

    assert result["failed"] is True
    assert "RC4 and anonymous suites are refused" in result["msg"]
