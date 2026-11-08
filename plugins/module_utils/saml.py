# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The SAML area: single sign-on to the Web UI through an identity provider.

The cluster validates the IdP certificate and stores it as canonical PEM, dropping any text around it and re-wrapping
the base64, so certificates are compared by their DER, in order, never as text.
"""

from __future__ import annotations

import base64
import binascii
import re
import textwrap
from typing import ClassVar

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.dirsvc import AD_STATUS, JOINED
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Field, Settings

# PATCH only: PUT validates idp_certificate even when the body leaves it out, so a PUT without a certificate always fails.
SAML_SETTINGS = Route("/v1/saml/settings", ("GET", "PATCH"))
PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.DOTALL)
FACTORY = {"enabled": False, "idp_sso_url": "", "idp_certificate": "", "idp_entity_id": "", "cluster_dns_name": "", "require_sso": False}


def certificates(text):
    """The DER of each PEM certificate in text, in order; ValueError when a block's base64 does not decode."""
    found = []
    for body in PEM_BLOCK.findall(text or ""):
        try:
            found.append(base64.b64decode("".join(body.split()), validate=True))
        except binascii.Error as e:
            raise ValueError(f"a PEM certificate's base64 does not decode: {e}") from None
    return found


def canonical_pem(text):
    """text's certificates as the cluster stores them: one PEM block each, 64 columns, \\n; "" for none."""
    return "".join(
        "-----BEGIN CERTIFICATE-----\n" + "\n".join(textwrap.wrap(base64.b64encode(der).decode(), 64)) + "\n-----END CERTIFICATE-----\n"
        for der in certificates(text)
    )


class SamlSettings(Settings):
    RETURN_KEY = "saml_settings"
    ROUTE = SAML_SETTINGS
    FIELDS: ClassVar[dict[str, Field]] = {
        "enabled": Field("enabled"),
        "idp_sso_url": Field("idp_sso_url"),
        "idp_certificate": Field("idp_certificate", read=canonical_pem),
        "idp_entity_id": Field("idp_entity_id"),
        "cluster_dns_name": Field("cluster_dns_name"),
        "require_sso": Field("require_sso"),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        given = self.params.get("idp_certificate")
        self.certificate = None
        if given:
            try:
                self.certificate = canonical_pem(given)
            except ValueError as e:
                raise QumuloError(f"idp_certificate: {e}") from None
            if not self.certificate:
                raise QumuloError("idp_certificate holds no PEM certificate; give the IdP's signing certificate as -----BEGIN CERTIFICATE----- text")

    def desired(self, before):
        after = super().desired(before)
        if self.params.get("idp_certificate") == "":
            if before.get("idp_certificate"):
                raise QumuloError(
                    "the cluster keeps the IdP certificate once one is set and refuses an empty one (G-ID-SAML-CERT); "
                    "turn SAML sign-in off with enabled: false instead"
                )
            after["idp_certificate"] = before.get("idp_certificate", "")
        elif self.certificate is not None:
            after["idp_certificate"] = self.certificate
        if after.get("enabled") and not after.get("idp_certificate"):
            # The cluster turns sign-in on without a certificate, and then no IdP response can be checked (QUIRK-032).
            raise QumuloError("SAML sign-in needs the IdP's certificate, and the cluster holds none; give idp_certificate")
        self._warn(before, after)
        return after

    def _warn(self, before, after):
        if after.get("enabled") and after.get("require_sso") and not (before.get("enabled") and before.get("require_sso")):
            self.client.warnings.append(
                "require_sso: while SAML sign-in is on, Active Directory users can sign in to the Web UI, qq and the REST API only "
                "through the IdP, not with a password; keep a local user, such as admin, that signs in with one"
            )
        if not after.get("enabled") or before.get("enabled"):
            return
        if self.params.get("idp_certificate") is None:
            self.client.warnings.append(
                "SAML sign-in is on with the IdP certificate the cluster already held, which this task did not give and which can "
                "never be removed (G-ID-SAML-CERT); give idp_certificate to check that it is the IdP's"
            )
        if self.client.get(AD_STATUS).data["status"] != JOINED:
            why = "SAML sign-in is on, but the cluster is not joined to Active Directory, and only users of the joined domain can sign in through the IdP"
            if self.module.check_mode:
                self.notes.append(f"{why}; an earlier task is expected to join it")
            else:
                self.client.warnings.append(why)


def read_saml(client):
    """The SAML settings as saml_settings takes them."""
    data = client.get(SAML_SETTINGS).data
    return {name: field.read(data[field.wire]) if field.read else data[field.wire] for name, field in SamlSettings.FIELDS.items() if field.wire in data}
