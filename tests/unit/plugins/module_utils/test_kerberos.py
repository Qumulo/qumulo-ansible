# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The keytab reader of the Kerberos area. The keytabs are built by hand from the MIT keytab format."""

from __future__ import annotations

import base64
import struct

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import kerberos
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_kerberos import (
    AES128,
    AES128_NAME,
    AES256_NAME,
    PRINCIPAL,
    keytab,
    record,
)

SECRET_KEY = b"\x8a\x11secret key bytes!\x00\xfe" + b"z" * 12


def test_an_entry_reads_as_the_cluster_reports_it():
    [entry] = kerberos.read_keytab(keytab(record(key=SECRET_KEY)))

    assert entry == {
        "principal": PRINCIPAL,
        "version": 3,
        "enctype": AES256_NAME,
        "key": base64.b64encode(SECRET_KEY).decode(),
        "full_version": 3,
    }


@pytest.mark.parametrize(
    "principal",
    ["alice@CORP.EXAMPLE.COM", "nfs/edge01.corp.example.com@CORP.EXAMPLE.COM", "a/b/c@CORP.EXAMPLE.COM", "ho/st/x@y@CORP.EXAMPLE.COM"],
)
def test_the_principal_joins_its_components_and_realm_without_escaping(principal):
    """The cluster joins the components with / and adds @ and the realm, escaping nothing (research R2)."""
    components, realm = principal.rsplit("@", 1)
    name = components.split("/") if principal != "ho/st/x@y@CORP.EXAMPLE.COM" else ["ho/st", "x@y"]

    def text(value):
        return struct.pack(">H", len(value)) + value.encode()

    body = struct.pack(">H", len(name)) + text(realm) + b"".join(text(c) for c in name) + struct.pack(">IIBHH", 1, 0, 1, 18, 4) + b"keyk"

    assert [e["principal"] for e in kerberos.read_keytab(b"\x05\x02" + struct.pack(">i", len(body)) + body)] == [principal]


def test_entries_keep_the_file_s_order_and_duplicates():
    data = keytab(record(enctype=18), record(enctype=AES128, key=b"q" * 16), record(enctype=18))

    assert [e["enctype"] for e in kerberos.read_keytab(data)] == [AES256_NAME, AES128_NAME, AES256_NAME]


def test_a_version_above_255_reads_as_its_low_8_bits_with_the_full_one_beside_it():
    """QUIRK-162: the cluster keeps the 8-bit field."""
    [entry] = kerberos.read_keytab(keytab(record(version=300, full_version=300)))

    assert (entry["version"], entry["full_version"]) == (44, 300)


def test_a_32_bit_version_of_0_leaves_the_8_bit_one():
    [entry] = kerberos.read_keytab(keytab(record(version=7, full_version=0)))

    assert (entry["version"], entry["full_version"]) == (7, 7)


def test_a_hole_is_skipped():
    real = record()
    data = b"\x05\x02" + struct.pack(">i", -8) + b"\0" * 8 + struct.pack(">i", len(real)) + real

    assert [e["principal"] for e in kerberos.read_keytab(data)] == [PRINCIPAL]


@pytest.mark.parametrize("number", sorted(kerberos.ENCTYPES))
def test_every_type_the_cluster_reports_reads_by_its_number(number):
    [entry] = kerberos.read_keytab(keytab(record(enctype=number)))

    assert entry["enctype"] == kerberos.ENCTYPES[number]


def test_the_types_are_the_api_s_enum():
    """Every name of the enum GET answers with, and the numbers research R3 saw each read back as."""
    assert len(kerberos.ENCTYPES) == 26
    assert kerberos.ENCTYPES[23] == "KERBEROS_ENCRYPTION_TYPE_ARCFOUR_HMAC_MD5"
    assert kerberos.ENCTYPES[0xFF80] == "KERBEROS_ENCRYPTION_TYPE_ARCFOUR_MD4"
    assert kerberos.ENCTYPES[0xEFFB] == "KERBEROS_ENCRYPTION_TYPE_CRAM_MD5_NONE"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "not a keytab file of format 0x0502"),
        (b"\x05\x01" + keytab()[2:], "not a keytab file of format 0x0502"),
        (b"\x05\x02", "holds no entry; to remove the cluster's keytab, use state: absent"),
        (keytab()[:-5], "keytab entry 1 is malformed"),
        (keytab() + b"\x00\x00", "keytab entry 2 is malformed"),
        (b"\x05\x02" + struct.pack(">i", 0), "keytab entry 1 is malformed"),
        (keytab(record(), record(enctype=26)), "keytab entry 2 (" + PRINCIPAL + ") has encryption type 26, which the cluster does not take"),
        (keytab(record(enctype=-1)), "has encryption type -1, which the cluster does not take"),
    ],
)
def test_a_keytab_the_cluster_would_refuse_fails_naming_the_problem(data, message):
    with pytest.raises(QumuloError, match=message.replace("(", r"\(").replace(")", r"\)")):
        kerberos.read_keytab(data)


def test_a_malformed_entry_s_message_holds_none_of_its_bytes():
    body = record(key=SECRET_KEY)
    data = b"\x05\x02" + struct.pack(">i", len(body) + 9) + body

    with pytest.raises(QumuloError) as raised:
        kerberos.read_keytab(data)

    assert "secret" not in str(raised.value)
    assert raised.value.details == {}


def test_base64_content_decodes_with_whitespace_ignored():
    data = keytab(record(key=SECRET_KEY))
    wrapped = "\n".join(base64.b64encode(data).decode()[i : i + 20] for i in range(0, 200, 20)) + "\n"

    assert kerberos.decode_keytab({"keytab": wrapped, "keytab_path": None}) == data


@pytest.mark.parametrize("content", ["not base64 at all!", "QUJD=X", "é"])
def test_content_that_is_not_base64_fails_without_quoting_it(content):
    with pytest.raises(QumuloError, match="keytab is not base64") as raised:
        kerberos.decode_keytab({"keytab": content, "keytab_path": None})

    assert content not in str(raised.value)


def test_a_path_is_read_as_bytes(tmp_path):
    data = keytab(record(key=SECRET_KEY))
    (tmp_path / "edge01.keytab").write_bytes(data)

    assert kerberos.decode_keytab({"keytab": None, "keytab_path": str(tmp_path / "edge01.keytab")}) == data


def test_a_vaulted_path_fails_naming_the_lookup(tmp_path):
    (tmp_path / "edge01.keytab").write_bytes(b"$ANSIBLE_VAULT;1.1;AES256\n6162\n")

    with pytest.raises(QumuloError, match=r"encrypted with Ansible Vault; .*lookup\('ansible.builtin.file'"):
        kerberos.decode_keytab({"keytab": None, "keytab_path": str(tmp_path / "edge01.keytab")})


def test_a_missing_path_fails_naming_it(tmp_path):
    with pytest.raises(QumuloError, match=r"keytab_path .*missing.keytab: No such file or directory"):
        kerberos.decode_keytab({"keytab": None, "keytab_path": str(tmp_path / "missing.keytab")})


def test_keytab_state_digests_the_keys_and_shows_the_rest_sorted():
    first = {"principal": "b@R", "version": 2, "enctype": AES256_NAME, "key": "a2V5"}
    second = {"principal": "a@R", "version": 1, "enctype": AES128_NAME, "key": "a2V5Mg=="}

    state = kerberos.keytab_state([first, second])

    assert state["entries"] == [
        {"principal": "a@R", "version": 1, "enctype": AES128_NAME},
        {"principal": "b@R", "version": 2, "enctype": AES256_NAME},
    ]
    assert state["keys"].startswith("sha256:")
    assert state == kerberos.keytab_state([second, first])
    assert state["keys"] != kerberos.keytab_state([first, {**second, "key": "b3RoZXI="}])["keys"]
