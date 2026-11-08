# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""module_utils/tls.py against real TLS on the loopback interface, with certificates made by openssl for each test."""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import tls

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command to make test certificates")

CA_EXTENSIONS = "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,digitalSignature\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"


def make_certificate(directory, name, extensions=CA_EXTENSIONS):
    """A self-signed certificate and its key, as (certificate PEM, key PEM); no extensions makes a bare leaf."""
    config = directory / f"{name}.cnf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nprompt=no\n"
        + ("x509_extensions=v3\n" if extensions else "")
        + "[dn]\nCN=localhost\n"
        + ("[v3]\n" + extensions if extensions else "")
    )
    key, cert = directory / f"{name}.key", directory / f"{name}.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2"]
        + ["-keyout", str(key), "-out", str(cert), "-config", str(config)],
        check=True,
        capture_output=True,
    )
    return cert.read_text(), key.read_text()


@pytest.fixture
def server(tmp_path):
    """Serves a certificate on the loopback interface; returns its port."""
    stop = []

    def serve(certificate, key):
        (tmp_path / "served.crt").write_text(certificate)
        (tmp_path / "served.key").write_text(key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tmp_path / "served.crt", tmp_path / "served.key")
        listener = socket.create_server(("127.0.0.1", 0))
        stop.append(listener)

        def accept():
            try:
                connection = listener.accept()[0]
                with context.wrap_socket(connection, server_side=True):
                    pass
            except OSError:
                pass

        threading.Thread(target=accept, daemon=True).start()
        return listener.getsockname()[1]

    yield serve
    for listener in stop:
        listener.close()


def test_leaf_digest_is_the_first_certificate_of_the_chain(tmp_path):
    first = make_certificate(tmp_path, "first")[0]
    second = make_certificate(tmp_path, "second")[0]

    assert tls.leaf_sha256(first + second) == tls.leaf_sha256(first)
    assert tls.leaf_sha256(first) != tls.leaf_sha256(second)


def test_text_without_a_certificate_has_no_leaf():
    with pytest.raises(ValueError, match="no PEM certificate"):
        tls.leaf_sha256("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")


def test_served_leaf_reads_the_certificate_without_verifying_it(tmp_path, server):
    certificate, key = make_certificate(tmp_path, "served", extensions="")
    port = server(certificate, key)

    assert tls.sha256(tls.served_leaf("127.0.0.1", port, 5)) == tls.leaf_sha256(certificate)


def test_ca_shaped_certificate_is_not_flagged(tmp_path):
    certificate = make_certificate(tmp_path, "ca")[0]

    assert tls.self_signed_but_not_ca(certificate) is False


def test_bare_self_signed_leaf_is_flagged(tmp_path):
    certificate = make_certificate(tmp_path, "leaf", extensions="")[0]

    assert tls.self_signed_but_not_ca(certificate) is True


def test_chain_that_verifies_against_ca_path_for_the_host_has_no_error(tmp_path):
    certificate, key = make_certificate(tmp_path, "ca")
    (tmp_path / "pin.crt").write_text(certificate)

    assert tls.chain_error(certificate, key, "localhost", str(tmp_path / "pin.crt")) is None


def test_chain_for_another_host_has_an_error(tmp_path):
    certificate, key = make_certificate(tmp_path, "ca")
    (tmp_path / "pin.crt").write_text(certificate)

    assert "edge01.example.com" in tls.chain_error(certificate, key, "edge01.example.com", str(tmp_path / "pin.crt"))


def test_chain_signed_by_another_ca_has_an_error(tmp_path):
    certificate, key = make_certificate(tmp_path, "new")
    other = make_certificate(tmp_path, "old")[0]
    (tmp_path / "pin.crt").write_text(other)

    assert "certificate verify failed" in tls.chain_error(certificate, key, "localhost", str(tmp_path / "pin.crt"))
