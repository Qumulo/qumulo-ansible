# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The Kerberos area: the keytab of a standalone KDC, which NFS's Kerberos flavours need on a cluster outside Active
Directory, and the Kerberos settings.

The cluster keeps both in one stored record behind two routes, which shows in QUIRK-160 and QUIRK-161. The keytab's
GET returns every key, as base64, so the keytab is compared entry by entry, its keys through one digest that no result
shows (spec 024, Decision 3). A keytab and an AD join exclude each other (G-KRB-AD).
"""

from __future__ import annotations

import base64
import binascii
import struct
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.client import (
    STATUS_HINTS,
    QumuloApiError,
    QumuloError,
    QumuloMaybeApplied,
    Route,
    register_secret,
)
from ansible_collections.qumulo.cluster.plugins.module_utils.dirsvc import joined_domain
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Current, Field, Resource, Settings, digest

KEYTAB = Route(
    "/v1/auth/kerberos-keytab",
    ("GET", "PUT", "DELETE"),
    etag="omit",
    why="the GET's ETag is the same whatever the keytab, and a write with it answers 412 once one is held (QUIRK-160)",
)
SETTINGS = Route("/v1/auth/kerberos-settings", ("GET", "PUT"))
MAPPING = "use_alt_security_identities_mapping"
# A keytab PUT on a joined cluster answers 409 with this class; the module refuses before it (G-KRB-AD), so the cluster
# answers it only to a join that lands between the module's read of the domain and its PUT.
JOINED_ERROR = "auth_joined_to_ad_error"
JOINED_REASON = (
    "a joined cluster takes no keytab: its Kerberos keys are its computer account's (G-KRB-AD); leave with ad_domain state=absent first, or declare no keytab"
)

# The encryption type numbers a keytab file holds, and the names the cluster reports them by. Every type of the
# API's enum, and no other: the cluster refuses a file that holds another (spec 024, research R3).
ENCTYPES = {
    number & 0xFFFF: "KERBEROS_ENCRYPTION_TYPE_" + name
    for number, name in (
        (0, "NULL"),
        (1, "DES_CBC_CRC"),
        (2, "DES_CBC_MD4"),
        (3, "DES_CBC_MD5"),
        (5, "DES3_CBC_MD5"),
        (7, "OLD_DES3_CBC_SHA1"),
        (8, "SIGN_DSA_GENERATE"),
        (9, "ENCRYPT_RSA_PRIV"),
        (10, "ENCRYPT_RSA_PUB"),
        (16, "DES3_CBC_SHA1"),
        (17, "AES128_CTS_HMAC_SHA1_96"),
        (18, "AES256_CTS_HMAC_SHA1_96"),
        (19, "AES128_CTS_HMAC_SHA256_128"),
        (20, "AES256_CTS_HMAC_SHA384_192"),
        (23, "ARCFOUR_HMAC_MD5"),
        (24, "ARCFOUR_HMAC_MD5_56"),
        (48, "ENCTYPE_PK_CROSS"),
        (-128, "ARCFOUR_MD4"),
        (-133, "ARCFOUR_HMAC_OLD"),
        (-135, "ARCFOUR_HMAC_OLD_EXP"),
        (-4096, "DES_CBC_NONE"),
        (-4097, "DES3_CBC_NONE"),
        (-4098, "DES_CFB64_NONE"),
        (-4099, "DES_PCBC_NONE"),
        (-4100, "DIGEST_MD5_NONE"),
        (-4101, "CRAM_MD5_NONE"),
    )
}
FORMAT = b"\x05\x02"
VAULTED = b"$ANSIBLE_VAULT"
VAULT_RECIPE = (
    "a module reads the file as it is and cannot decrypt it; encrypt the keytab's base64 instead and give it as "
    "keytab: \"{{ lookup('ansible.builtin.file', 'FILE') }}\", which decrypts it"
)


def decode_keytab(params):
    """The keytab file's bytes, from keytab (base64, whitespace ignored) or keytab_path. No message quotes the input."""
    path = params.get("keytab_path")
    if path:
        try:
            with open(path, "rb") as stream:
                data = stream.read()
        except OSError as e:
            raise QumuloError(f"keytab_path {path}: {e.strerror}") from None
        if data.startswith(VAULTED):
            raise QumuloError(f"keytab_path {path} is encrypted with Ansible Vault; {VAULT_RECIPE}")
        return data
    try:
        return base64.b64decode("".join(params["keytab"].split()), validate=True)
    except (binascii.Error, ValueError):
        raise QumuloError("keytab is not base64; give the keytab file's bytes as base64, as `base64 -w0 FILE` writes them") from None


class _Record:
    """A cursor over one entry of a keytab file; every read past its end raises struct.error."""

    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, layout):
        values = struct.unpack_from(layout, self.data, self.at)
        self.at += struct.calcsize(layout)
        return values if len(values) > 1 else values[0]

    def text(self):
        size = self.take(">H")
        if self.at + size > len(self.data):
            raise struct.error("a string runs past the entry")
        value = self.data[self.at : self.at + size].decode("utf-8")
        self.at += size
        return value

    def left(self):
        return len(self.data) - self.at


def _entry(data, number):
    record = _Record(data)
    try:
        components = record.take(">H")
        realm = record.text()
        principal = "/".join(record.text() for _component in range(components)) + "@" + realm
        _name_type, _timestamp, version, enctype, key_size = record.take(">IIBHH")
        if record.left() < key_size:
            raise struct.error("the key runs past the entry")
        key = data[record.at : record.at + key_size]
        record.at += key_size
        # A file may add the full version as 32 bits after the key; 0 there means the 8-bit field holds it.
        full_version = (record.take(">I") if record.left() >= 4 else 0) or version
    except (struct.error, UnicodeDecodeError):
        raise QumuloError(f"keytab entry {number} is malformed") from None
    if enctype not in ENCTYPES:
        signed = enctype - 0x10000 if enctype & 0x8000 else enctype
        raise QumuloError(f"keytab entry {number} ({principal}) has encryption type {signed}, which the cluster does not take")
    return {"principal": principal, "version": version, "enctype": ENCTYPES[enctype], "key": base64.b64encode(key).decode(), "full_version": full_version}


def read_keytab(data):
    """The entries of a keytab file of format 0x0502, as GET /v1/auth/kerberos-keytab answers them, in the file's order.

    Each holds principal, version (the low 8 bits, which the cluster keeps: QUIRK-162), enctype and key (base64), and
    full_version, the version the file holds. Holes, which a negative length marks, are skipped, as the cluster skips them.
    """
    if data[:2] != FORMAT:
        raise QumuloError("keytab is not a keytab file of format 0x0502, the only one the cluster reads")
    entries, at = [], len(FORMAT)
    while at < len(data):
        if len(data) - at < 4:
            raise QumuloError(f"keytab entry {len(entries) + 1} is malformed")
        (size,) = struct.unpack_from(">i", data, at)
        at += 4
        if size < 0:
            at -= size
            continue
        if size == 0 or at + size > len(data):
            raise QumuloError(f"keytab entry {len(entries) + 1} is malformed")
        entries.append(_entry(data[at : at + size], len(entries) + 1))
        at += size
    if not entries:
        raise QumuloError("keytab holds no entry; to remove the cluster's keytab, use state: absent")
    return entries


def shown(entries):
    """entries without their keys, sorted: what a result, a diff or a capture may carry."""
    return sorted(({"principal": e["principal"], "version": e["version"], "enctype": e["enctype"]} for e in entries), key=lambda e: tuple(e.values()))


def keytab_state(entries):
    """A keytab in the module's state: its entries without keys, and one digest over the entries with them."""
    return {"entries": shown(entries), "keys": digest(sorted([e["principal"], e["version"], e["enctype"], e.get("key")] for e in entries))}


def _register_keys(entries):
    """Have Ansible mask each key's base64 wherever it would show, as no_log masks the keytab option."""
    for entry in entries:
        if entry.get("key"):
            register_secret(entry["key"])


def held_entries(client):
    """The entries of the cluster's keytab, keys included; [] when it holds none, which it answers as null or no entries."""
    entries = (client.get(KEYTAB).data or {}).get("entries") or []
    _register_keys(entries)
    return entries


def keytab_entries(client):
    """The cluster's keytab entries without their keys, sorted; [] when it holds none."""
    return shown(held_entries(client))


class KerberosKeytab(Resource):
    RETURN_KEY = "kerberos_keytab"
    SECRET_READBACK: ClassVar[frozenset[str]] = frozenset({"keys"})
    UNORDERED: ClassVar[frozenset[str]] = frozenset({"entries"})
    READ_ONLY: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, module, client):
        super().__init__(module, client)
        self.data = self.wanted = None
        self.mapping_before = False
        if self.params["state"] == "present":
            self.data = decode_keytab(self.params)
            entries = read_keytab(self.data)
            _register_keys(entries)
            for entry in entries:
                if entry["full_version"] != entry["version"]:
                    module.warn(
                        f"keytab entry for {entry['principal']} has key version {entry['full_version']}, of which the cluster keeps only "
                        f"the low 8 bits, {entry['version']} (QUIRK-162)"
                    )
            self.wanted = keytab_state(entries)

    def describe(self):
        return "the Kerberos keytab"

    def changes(self, before, after, creating):
        keys = super().changes(before, after, creating)
        if keys or creating:
            self.refuse_when_joined()
        return keys

    def refuse_when_joined(self):
        """Fail before a keytab PUT, in check mode too, on a cluster in a domain or joining one, which refuses it (G-KRB-AD)."""
        domain = joined_domain(self.client)
        if domain:
            raise QumuloError(f"the cluster is joined to {domain}, and {JOINED_REASON}")

    def fetch(self):
        entries = held_entries(self.client)
        return Current(None, {"entries": entries}) if entries else None

    def normalize(self, current):
        return keytab_state(current.data["entries"])

    def desired(self, before):
        return self.wanted

    def create(self, after):
        self._put()

    def update(self, current, after, keys):
        self._put()

    def _put(self):
        try:
            self.client.put(KEYTAB, raw=self.data)
        except QumuloApiError as e:
            # The description can quote the keytab (QUIRK-163), so only the status and class are reported, with a reason
            # of the module's own for the classes it knows.
            if e.is_("keytab_parse_error"):
                reason = "the cluster could not read the keytab"
            elif e.is_(JOINED_ERROR):
                reason = f"the cluster is joined to a domain, and {JOINED_REASON}"
            elif e.status == 403:
                reason = "the API user lacks PRIVILEGE_KERBEROS_KEYTAB_WRITE"
            else:
                reason = STATUS_HINTS.get(e.status, "the cluster refused the keytab")
            raise QumuloError(f"PUT {KEYTAB.path} failed: HTTP {e.status} {e.error_class}: {reason}", status=e.status, error_class=e.error_class) from None

    def delete(self, current):
        self.mapping_before = bool(self.client.get(SETTINGS).data.get(MAPPING))
        self.client.delete(KEYTAB)

    def settle_absent(self):
        """Turn the mapping back on when the DELETE turned it off (QUIRK-161); a lost DELETE's reconcile runs this too.

        A keytab still held means a lost DELETE that did not happen, which turned nothing off: the reconcile then fails,
        and a rerun deletes and puts the setting back.
        """
        if not self.mapping_before:
            return
        try:
            still_held = bool(held_entries(self.client))
        except QumuloError as e:
            raise QumuloError(f"the keytab's DELETE may have turned {MAPPING} off (QUIRK-161), and reading the keytab back failed: {e}", changed=True) from None
        if still_held:
            return

        def turn_back_on(settings):
            return None if settings.get(MAPPING) else {**settings, MAPPING: True}

        try:
            wrote = self.client.read_modify_write(SETTINGS, turn_back_on, method="PUT") is not None
        except QumuloError as e:
            lost = {"maybe_applied": True} if isinstance(e, QumuloMaybeApplied) else {}
            raise QumuloError(f"the keytab's DELETE turned {MAPPING} off (QUIRK-161), and turning it back on failed: {e}", changed=True, **lost) from None
        if wrote:
            self.notes.append(f"the keytab's DELETE turned {MAPPING} off (QUIRK-161); turned it back on")

    def result(self, changed, before, after, keys):
        result = super().result(changed, before, after or {"entries": []}, keys)
        if "diff" in result and not before:
            result["diff"]["before"] = {"entries": []}
        return result


class KerberosSettings(Settings):
    RETURN_KEY = "kerberos_settings"
    ROUTE = SETTINGS
    # PATCH answers 405 and a PUT needs the field, so the whole document is PUT with the If-Match of its read; that
    # ETag covers the keytab too, so a keytab change in between answers 412, and the read-modify-write reads again.
    WRITE = "PUT"
    FIELDS: ClassVar[dict[str, Field]] = {MAPPING: Field(MAPPING)}

    def describe(self):
        return "the Kerberos settings"
