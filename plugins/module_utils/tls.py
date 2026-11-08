# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""TLS certificates: PEM chains, the certificate an address serves, and whether a chain verifies for a host.

Only the standard library is used, so the few X.509 facts needed are read from the DER encoding directly.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import socket
import ssl
import tempfile
import threading

from ansible.module_utils.urls import make_context

PEM_CERTIFICATE = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.DOTALL)
BASIC_CONSTRAINTS = bytes.fromhex("551d13")  # the DER contents of OID 2.5.29.19
SEQUENCE, BOOLEAN, OID, OCTET_STRING, VERSION, EXTENSIONS = 0x30, 0x01, 0x06, 0x04, 0xA0, 0xA3


def certificates(pem):
    """The DER certificates of a PEM chain, in order; ValueError when there are none."""
    found = [base64.b64decode("".join(body.split())) for body in PEM_CERTIFICATE.findall(pem)]
    if not found:
        raise ValueError("no PEM certificate")
    return found


def sha256(der):
    return hashlib.sha256(der).hexdigest()


def leaf_sha256(pem):
    return sha256(certificates(pem)[0])


def served_leaf(host, port, timeout):
    """The DER certificate host:port serves, read without verifying it."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw, context.wrap_socket(raw, server_hostname=host) as connection:
        return connection.getpeercert(binary_form=True)


def chain_error(certificate, private_key, host, ca_path):
    """Why a client that verifies like the collection's own would refuse this chain for host, or None.

    The chain is served once on the loopback interface by a thread of this process, and read with the same
    verification the REST client uses, against ca_path or the system's certificate authorities.
    """
    directory = tempfile.mkdtemp(prefix="qumulo-tls-")
    try:
        cert_file, key_file = os.path.join(directory, "chain.pem"), os.path.join(directory, "key.pem")
        for path, text in ((cert_file, certificate), (key_file, private_key)):
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
                f.write(text)
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(cert_file, key_file)
    except (OSError, ssl.SSLError) as e:
        return f"the certificate and key cannot be served ({e})"
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    with socket.create_server(("127.0.0.1", 0)) as listener:
        thread = threading.Thread(target=_serve_once, args=(listener, server), daemon=True)
        thread.start()
        try:
            with socket.create_connection(listener.getsockname(), timeout=10) as raw:
                make_context(cafile=ca_path).wrap_socket(raw, server_hostname=host).close()
        except ssl.SSLCertVerificationError as e:
            return f"certificate verify failed: {e.verify_message} for {host}"
        except (OSError, ssl.SSLError) as e:
            return str(e)
        finally:
            thread.join(10)
    return None


def _serve_once(listener, context):
    try:
        connection = listener.accept()[0]
        with context.wrap_socket(connection, server_side=True):
            pass
    except (OSError, ssl.SSLError):
        pass  # the client refused the chain; chain_error reports why


def self_signed_but_not_ca(pem):
    """Whether the first certificate names itself as issuer but lacks basicConstraints CA:TRUE.

    Python 3.13 and later refuse such a certificate as a trust anchor, so it cannot be pinned through ca_path.
    """
    certificate = _children(certificates(pem)[0])[0][1]
    tbs = _children(_children(certificate)[0][1])
    start = 1 if tbs[0][0] == VERSION else 0
    issuer, subject = tbs[start + 2][1], tbs[start + 4][1]
    return issuer == subject and not _is_ca(tbs)


def _is_ca(tbs):
    for tag, value in tbs:
        if tag != EXTENSIONS:
            continue
        for _tag, extension in _children(_children(value)[0][1]):
            fields = _children(extension)
            if fields[0] == (OID, BASIC_CONSTRAINTS):
                constraints = _children(_children(fields[-1][1])[0][1])
                return bool(constraints) and constraints[0][0] == BOOLEAN and constraints[0][1] != b"\x00"
    return False


def _children(data):
    """The (tag, contents) of each DER element in data."""
    out, i = [], 0
    while i < len(data):
        tag, length = data[i], data[i + 1]
        i += 2
        if length & 0x80:
            size = length & 0x7F
            length = int.from_bytes(data[i : i + size], "big")
            i += size
        out.append((tag, data[i : i + length]))
        i += length
    return out
