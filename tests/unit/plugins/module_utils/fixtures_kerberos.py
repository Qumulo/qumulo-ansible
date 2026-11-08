# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Keytab files for the Kerberos units, built as the MIT keytab format 0x0502 lays them out, with made-up keys."""

from __future__ import annotations

import base64
import struct

PRINCIPAL = "nfs/edge01.corp.example.com@CORP.EXAMPLE.COM"
AES256 = 18
AES128 = 17
AES256_NAME = "KERBEROS_ENCRYPTION_TYPE_AES256_CTS_HMAC_SHA1_96"
AES128_NAME = "KERBEROS_ENCRYPTION_TYPE_AES128_CTS_HMAC_SHA1_96"


def record(principal=PRINCIPAL, version=3, enctype=AES256, key=b"k" * 32, full_version=None, name_type=1, timestamp=0):
    """One entry's bytes, without its length; full_version, when given, is the 32-bit version after the key."""
    name, realm = principal.rsplit("@", 1)
    components = name.split("/")

    def text(value):
        return struct.pack(">H", len(value.encode())) + value.encode()

    body = struct.pack(">H", len(components)) + text(realm) + b"".join(text(c) for c in components)
    body += struct.pack(">IIBHH", name_type, timestamp, version & 0xFF, enctype & 0xFFFF, len(key)) + key
    if full_version is not None:
        body += struct.pack(">I", full_version)
    return body


def keytab(*records):
    """A keytab file of the records; with none, one entry of record()'s defaults."""
    records = records or (record(),)
    return b"\x05\x02" + b"".join(struct.pack(">i", len(r)) + r for r in records)


def entry(principal=PRINCIPAL, version=3, enctype=AES256_NAME, key=b"k" * 32):
    """One entry as GET /v1/auth/kerberos-keytab answers it, its key as base64."""
    return {"principal": principal, "version": version, "enctype": enctype, "key": base64.b64encode(key).decode()}


def shown(principal=PRINCIPAL, version=3, enctype=AES256_NAME):
    """One entry as the module returns it, without its key."""
    return {"principal": principal, "version": version, "enctype": enctype}


def b64(data):
    return base64.b64encode(data).decode()
