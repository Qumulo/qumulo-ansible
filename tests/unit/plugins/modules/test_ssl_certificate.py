# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.ssl_certificate, driven through FakeQumulo with the TLS probes replaced by a table of served certificates."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import tls
from ansible_collections.qumulo.cluster.plugins.modules import ssl_certificate
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "edge01.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
CERTIFICATE = "/v2/cluster/settings/ssl/certificate"
# Public test certificates; their keys were discarded. CA_SHAPED has basicConstraints CA:TRUE, BARE_LEAF has no extensions.
CA_SHAPED = """-----BEGIN CERTIFICATE-----
MIIBfjCCASSgAwIBAgIUR1HdPZJJZhk/mL41uZKNrabWIE0wCgYIKoZIzj0EAwIw
HTEbMBkGA1UEAwwSZWRnZTAxLmV4YW1wbGUuY29tMB4XDTI2MTAzMTAzMDU1MloX
DTM2MTAyODAzMDU1MlowHTEbMBkGA1UEAwwSZWRnZTAxLmV4YW1wbGUuY29tMFkw
EwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAExZtnrBvNUZIFFhCOqvWVzG+oEJuZ5RuZ
6DL5+ag1u2wdsDTbEPyF5p5xt54Dz641KPqXSg4n3AyJuLXfLcuuL6NCMEAwDwYD
VR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMCAoQwHQYDVR0RBBYwFIISZWRnZTAx
LmV4YW1wbGUuY29tMAoGCCqGSM49BAMCA0gAMEUCIQDOXrWc1FPrlNx5qT236lQC
ndNGSJu8KsBwbqAN8cIj5AIgTy6qQZL2VBB/FrhVWAp9+i+J3DGSDAga+LaYuiAZ
+J0=
-----END CERTIFICATE-----
"""
BARE_LEAF = """-----BEGIN CERTIFICATE-----
MIIBMzCB2wIUAViVz6rtwYGhKyeN96vy8txM8OEwCgYIKoZIzj0EAwIwHTEbMBkG
A1UEAwwSZWRnZTAxLmV4YW1wbGUuY29tMB4XDTI2MTAzMTAzMDU1MloXDTM2MTAy
ODAzMDU1MlowHTEbMBkGA1UEAwwSZWRnZTAxLmV4YW1wbGUuY29tMFkwEwYHKoZI
zj0CAQYIKoZIzj0DAQcDQgAE/oRfh/VmkIIXD4mtcVIyoy5vt42HDORLOchiv5hV
GDEqhA97fbO54bZPjoiQ1d4zk2sMYx9Ue8P3grUq87+04TAKBggqhkjOPQQDAgNH
ADBEAiBvikZkp6cKiV9W7UAFhQoBKzQWBJg/hRfG0soj9mnhtwIgCvvsIHIkbrxN
MZTMsVkO/Ut2nJD86xVHBq4ZZgUtZ9s=
-----END CERTIFICATE-----
"""
KEY = "not a key: the TLS checks are replaced in these tests"


def der(pem):
    body = re.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.DOTALL).group(1)
    return base64.b64decode("".join(body.split()))


def sha(pem):
    return hashlib.sha256(der(pem)).hexdigest()


class Served:
    """Stands in for tls.served_leaf: what each address serves, as a list consumed one probe at a time."""

    def __init__(self, monkeypatch, table):
        self.table = {address: list(answers) for address, answers in table.items()}
        self.calls = []
        monkeypatch.setattr(tls, "served_leaf", self)

    def __call__(self, host, port, timeout):
        address = f"{host}:{port}"
        self.calls.append(address)
        answers = self.table[address]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return der(answer)


@pytest.fixture
def verifies(monkeypatch):
    """Stands in for tls.chain_error: None means the chain verifies; set .error to make it fail."""

    class Check:
        def __init__(self):
            self.error = None
            self.calls = []

        def __call__(self, certificate, private_key, host, ca_path):
            self.calls.append((host, ca_path))
            return self.error

    check = Check()
    monkeypatch.setattr(tls, "chain_error", check)
    return check


def writes(fake):
    return [e for e in fake.log if e.method == "PUT"]


def test_served_certificate_matching_the_leaf_is_unchanged_and_sends_no_write(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    Served(monkeypatch, {"edge01.example.com:8000": [CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["ssl_certificate"] == {"sha256": sha(CA_SHAPED), "served": {"edge01.example.com:8000": sha(CA_SHAPED)}}
    assert writes(fake) == []
    assert verifies.calls == []


def test_different_certificate_is_installed_then_probed_until_served(fake_api, run_module, monkeypatch, verifies, fake_clock):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE, body={"certificate": CA_SHAPED, "private_key": KEY})
    served = Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF, BARE_LEAF, CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["ssl_certificate"]["served"] == {"edge01.example.com:8000": sha(CA_SHAPED)}
    assert len(served.calls) == 3
    assert fake_clock.sleeps == [2.0]
    assert verifies.calls == [("edge01.example.com", None)]
    assert KEY not in json.dumps(result)


def test_lost_connection_after_the_put_is_settled_by_probing(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF, CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert len(writes(fake)) == 1


def test_every_probed_address_must_serve_the_leaf(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE)
    Served(monkeypatch, {"edge01.example.com:8000": [CA_SHAPED], "10.0.0.5:443": [BARE_LEAF, CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY, "probe": ["edge01.example.com", "10.0.0.5:443"]})

    assert result["changed"] is True, result.get("msg")


def test_check_mode_and_diff_report_the_replacement_without_writing(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["diff"] == {"before": {"edge01.example.com:8000": sha(BARE_LEAF)}, "after": {"edge01.example.com:8000": sha(CA_SHAPED)}}
    assert writes(fake) == []


def test_old_certificate_still_served_at_the_deadline_fails_reporting_the_change(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE)
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY, "wait_timeout": 10})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "edge01.example.com:8000 still serves" in result["msg"]
    assert "after 10s" in result["msg"]


def test_chain_that_does_not_verify_for_api_host_needs_allow_management_disruption(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF]})
    verifies.error = "certificate verify failed: self-signed certificate"

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY, "ca_path": "/etc/qumulo-ca.pem"})

    assert result["failed"] is True
    assert "would not verify against ca_path for edge01.example.com" in result["msg"]
    assert "self-signed certificate" in result["msg"]
    assert "allow_management_disruption" in result["msg"]
    assert verifies.calls == [("edge01.example.com", "/etc/qumulo-ca.pem")]
    assert writes(fake) == []


def test_allowed_disruption_installs_a_chain_that_does_not_verify(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE)
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF, CA_SHAPED]})
    verifies.error = "certificate verify failed"

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY, "allow_management_disruption": True})

    assert result["changed"] is True, result.get("msg")


def test_validate_certs_false_skips_the_verification(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE)
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF, CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY, "validate_certs": False})

    assert result["changed"] is True, result.get("msg")
    assert verifies.calls == []


def test_self_signed_certificate_that_is_not_a_ca_warns(fake_api, run_module, monkeypatch, verifies):
    fake_api()
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": BARE_LEAF, "private_key": KEY})

    assert result["changed"] is False, result.get("msg")
    assert any("Python 3.13 and later refuse it" in warning for warning in result["warnings"])


def test_ca_shaped_certificate_does_not_warn(fake_api, run_module, monkeypatch, verifies):
    fake_api()
    Served(monkeypatch, {"edge01.example.com:8000": [CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY})

    assert not any("Python 3.13" in warning for warning in result["warnings"])


def test_text_without_a_certificate_fails_before_any_request(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    served = Served(monkeypatch, {})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": "not a certificate", "private_key": KEY})

    assert result["failed"] is True
    assert "certificate holds no PEM certificate" in result["msg"]
    assert fake.log == [] and served.calls == []


def test_unreachable_probe_fails_naming_the_address(fake_api, run_module, monkeypatch, verifies):
    fake_api()
    Served(monkeypatch, {"edge01.example.com:8000": [ConnectionRefusedError(111, "Connection refused")]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY})

    assert result["failed"] is True
    assert "cannot read the certificate edge01.example.com:8000 serves" in result["msg"]


def test_refused_certificate_fails_with_the_server_reason(fake_api, run_module, monkeypatch, verifies):
    fake = fake_api()
    fake.expect("PUT", CERTIFICATE, status=400, response={"error_class": "x509_certificate_key_mismatch_error", "description": "mismatch"})
    Served(monkeypatch, {"edge01.example.com:8000": [BARE_LEAF]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY})

    assert result["failed"] is True
    assert "x509_certificate_key_mismatch_error" in result["msg"]


@pytest.mark.parametrize(
    ("probe", "address"),
    [("[2001:db8::5]:443", "2001:db8::5:443"), ("2001:db8::5", "2001:db8::5:8000"), ("10.0.0.5", "10.0.0.5:8000")],
    ids=["bracketed_with_port", "bare_ipv6", "ipv4_without_port"],
)
def test_probe_addresses_take_api_port_unless_they_name_a_port(fake_api, run_module, monkeypatch, verifies, probe, address):
    """An IPv6 address names a port only inside brackets; a bare one ends in a group of hex digits, not a port."""
    fake_api()
    served = Served(monkeypatch, {address: [CA_SHAPED]})

    result = run_module(ssl_certificate, {**CONNECTION, "certificate": CA_SHAPED, "private_key": KEY, "probe": [probe]})

    assert result["changed"] is False, result.get("msg")
    assert served.calls == [address]
