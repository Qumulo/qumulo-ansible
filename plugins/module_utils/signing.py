# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Signatures with a private key the task gives, made by the openssl command on the controller.

The collection's modules import nothing beyond ansible-core, so the key goes to openssl in a file only the controller's
user can read, removed once openssl exits. openssl is given a passphrase no key uses, so an encrypted key fails at once
rather than asking for one. Nothing here returns or logs the key.
"""

from __future__ import annotations

import base64
import os
import re
import tempfile
from contextlib import contextmanager

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError

PEM_ARMOR = re.compile(r"-----(BEGIN|END) [A-Z ]+-----")
# An encrypted key in PEM, PKCS#8 or the older OpenSSL form.
ENCRYPTED_PEM = re.compile(r"-----BEGIN ENCRYPTED PRIVATE KEY-----|Proc-Type: 4,ENCRYPTED")
# What openssl says when it cannot decrypt a key with the passphrase it was given. About one time in 200, the wrong
# passphrase decrypts to bytes that pass the padding check, and openssl says instead that it cannot decode the key.
BAD_DECRYPT = re.compile(r"bad decrypt|maybe wrong password", re.IGNORECASE)
# The algorithm of an encrypted PKCS#8 key, as openssl asn1parse names it, which shows with no passphrase.
ENCRYPTED_PKCS8 = re.compile(r":(PBES2|pbeWith)")
# Given to openssl so that it never asks for one: an empty passphrase makes some releases ask anyway.
NO_PASSPHRASE = "pass:qumulo.cluster-no-passphrase"
ENCRYPTED_KEY = "private_key is encrypted with a passphrase; give it decrypted, and keep it encrypted with ansible-vault instead"


def bare_public_key(text):
    """A public key as base64 DER without PEM armor or whitespace, the form the key store returns."""
    return re.sub(r"\s", "", PEM_ARMOR.sub("", text))


class Signer:
    def __init__(self, module, purpose):
        """purpose says what the key is for, in the failure when the controller has no openssl."""
        self.module = module
        self.openssl = module.get_bin_path("openssl")
        if not self.openssl:
            raise QumuloError(f"{purpose} needs the openssl command on the controller; install it, or put it on PATH")

    def public_key(self, private_key):
        """The public half of private_key, as base64 DER."""
        with self._key_file(private_key) as path:
            der = self._run(["pkey", "-in", path, "-passin", NO_PASSPHRASE, "-pubout", "-outform", "DER"], path, "read the private key")
        return base64.b64encode(der).decode()

    def sign(self, private_key, text):
        """A base64 SHA-256 signature of text, as the cluster's unlock challenge takes it."""
        with self._key_file(private_key) as path:
            signature = self._run(["dgst", "-sha256", "-sign", path, "-passin", NO_PASSPHRASE], path, "sign", data=text.encode())
        return base64.b64encode(signature).decode()

    def _run(self, args, key_path, what, data=None):
        rc, out, err = self.module.run_command([self.openssl, *args], data=data, binary_data=True, encoding=None)
        if rc != 0:
            text = err.decode(errors="replace").strip()
            if BAD_DECRYPT.search(text) or self._is_encrypted(key_path):
                raise QumuloError(ENCRYPTED_KEY)
            raise QumuloError(f"openssl could not {what}: {text or f'exit {rc}'}")
        return out

    def _is_encrypted(self, key_path):
        rc, out, _err = self.module.run_command([self.openssl, "asn1parse", "-in", key_path], binary_data=True, encoding=None)
        return rc == 0 and bool(ENCRYPTED_PKCS8.search(out.decode(errors="replace")))

    @contextmanager
    def _key_file(self, private_key):
        if ENCRYPTED_PEM.search(private_key):
            raise QumuloError(ENCRYPTED_KEY)
        fd, path = tempfile.mkstemp(prefix="qumulo-key-", dir=self.module.tmpdir)
        try:
            with os.fdopen(fd, "w") as handle:  # mkstemp makes the file readable by this user only
                handle.write(private_key)
            yield path
        finally:
            os.unlink(path)
