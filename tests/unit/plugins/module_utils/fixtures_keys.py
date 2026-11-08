# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Key pairs made by the openssl command at run time, for the tests of the modules that sign with a private key.

No key is kept in the tree. Each key's private half is noted in SECRETS, which no module result may hold.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import tempfile

import pytest

SECRETS = []  # a line of each private key's PEM body, which no result may hold


class Key:
    def __init__(self, directory, name):
        self.directory = directory
        self.pem_path = directory / f"{name}.pem"
        subprocess.run(["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(self.pem_path)], check=True, capture_output=True)
        self.pem = self.pem_path.read_text()
        # A whole line survives repr and JSON alike; the multi-line PEM never would.
        SECRETS.append(max((line for line in self.pem.splitlines() if not line.startswith("-----")), key=len))
        der = subprocess.run(["openssl", "pkey", "-in", str(self.pem_path), "-pubout", "-outform", "DER"], check=True, capture_output=True).stdout
        self.public_key = base64.b64encode(der).decode()
        self.public_pem = subprocess.run(["openssl", "pkey", "-in", str(self.pem_path), "-pubout"], check=True, capture_output=True).stdout

    def verifies(self, data, signature, directory=None):
        """Whether signature, base64, is this key's ECDSA SHA-256 signature of data."""
        directory = directory or self.directory
        public = directory / "public.pem"
        public.write_bytes(self.public_pem)
        signed = directory / "signature.bin"
        signed.write_bytes(base64.b64decode(signature))
        checked = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(signed)], input=data.encode(), capture_output=True, check=False
        )
        return checked.returncode == 0

    def sign(self, data):
        """This key's signature of data, as base64, made by openssl and not by the module."""
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.pem_path)], input=data.encode(), capture_output=True, check=True).stdout
        return base64.b64encode(signature).decode()


def make_keys(tmp_path_factory, *names):
    """{name: Key}, or a skip (a failure on CI) when the machine has no openssl."""
    if shutil.which("openssl") is None:
        if os.environ.get("CI"):
            pytest.fail("these tests make keys with the openssl command, which this CI runner lacks")
        pytest.skip("these tests make keys with the openssl command")
    directory = tmp_path_factory.mktemp("keys")
    return {name: Key(directory, name) for name in names}


def spy_key_files(monkeypatch):
    """Each file mkstemp makes from now on, with its mode when made; the modules write a private key to one."""
    made = []
    real = tempfile.mkstemp

    def spy(*args, **kwargs):
        fd, path = real(*args, **kwargs)
        made.append((path, stat.S_IMODE(os.stat(path).st_mode)))
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    return made


def assert_no_secret(result):
    shown = json.dumps(result, default=str)
    assert not [secret for secret in SECRETS if secret in shown], "a private key reached the result"
