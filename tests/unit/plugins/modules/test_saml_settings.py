# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.saml_settings, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import base64
import textwrap

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import saml_settings
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
SAML = "/v1/saml/settings"
AD_STATUS = "/v1/ad/status"
# The module only decodes the base64 between the PEM armor lines, so any bytes stand in for a certificate's DER.
SIGNING = b"the IdP's signing certificate, DER" * 3
INTERMEDIATE = b"an intermediate certificate, DER" * 3


def pem(der, width=64, newline="\n"):
    """der as one PEM block, wrapped at width; the cluster stores 64 columns and \\n."""
    lines = textwrap.wrap(base64.b64encode(der).decode(), width)
    return newline.join(["-----BEGIN CERTIFICATE-----", *lines, "-----END CERTIFICATE-----"]) + newline


def stored(**changes):
    settings = {
        "enabled": False,
        "idp_sso_url": "",
        "idp_certificate": "",
        "idp_entity_id": "",
        "cluster_dns_name": "",
        "require_sso": False,
    }
    return {**settings, **changes}


CONFIGURED = stored(
    enabled=True,
    idp_sso_url="https://idp.example.com/saml2/sso",
    idp_certificate=pem(SIGNING),
    idp_entity_id="https://idp.example.com/metadata",
    cluster_dns_name="qumulo.corp.example.com",
)
DECLARED = {key: value for key, value in CONFIGURED.items() if key != "require_sso"}


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def test_matching_is_unchanged_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=CONFIGURED, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, **DECLARED})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["saml_settings"] == CONFIGURED
    assert writes(fake) == []


def test_a_certificate_in_other_text_is_the_certificate_the_cluster_stores(fake_api, run_module):
    """The cluster drops the text around a certificate and re-wraps it, so the certificates are compared, not the text."""
    fake = fake_api()
    fake.expect("GET", SAML, response=CONFIGURED, etag='"MaSODg"')
    given = "Subject: CN=idp.example.com\r\n" + pem(SIGNING, width=76, newline="\r\n") + "trailing words\n"

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": given})

    assert result["changed"] is False, result.get("diff") or result.get("msg")
    assert result["saml_settings"]["idp_certificate"] == pem(SIGNING)


def test_a_stored_certificate_in_other_text_is_compared_and_returned_canonical(fake_api, run_module):
    """A file lookup drops the final newline, and a stored certificate need not be canonical: both are read as certificates."""
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "idp_certificate": pem(SIGNING, newline="\r\n")}, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": pem(SIGNING).rstrip("\n")})

    assert result["changed"] is False, result.get("diff") or result.get("msg")
    assert result["saml_settings"]["idp_certificate"] == pem(SIGNING)


def test_change_patches_only_the_changed_keys_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=CONFIGURED, etag='"MaSODg"')
    fake.expect("PATCH", SAML, body={"idp_sso_url": "https://idp2.example.com/sso"}, if_match='"MaSODg"', response=CONFIGURED)
    fake.expect("GET", SAML, response={**CONFIGURED, "idp_sso_url": "https://idp2.example.com/sso"}, etag='"BcIm5A"')

    result = run_module(saml_settings, {**CONNECTION, **DECLARED, "idp_sso_url": "https://idp2.example.com/sso"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["saml_settings"]["idp_sso_url"] == "https://idp2.example.com/sso"


def test_a_new_certificate_is_sent_as_canonical_pem(fake_api, run_module):
    rotated = b"next year's signing certificate, DER" * 3
    fake = fake_api()
    fake.expect("GET", SAML, response=CONFIGURED, etag='"MaSODg"')
    fake.expect("PATCH", SAML, body={"idp_certificate": pem(rotated)}, if_match='"MaSODg"', response=CONFIGURED)
    fake.expect("GET", SAML, response={**CONFIGURED, "idp_certificate": pem(rotated)}, etag='"BcIm5A"')

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": "comment\n" + pem(rotated, width=40)})

    assert result["changed"] is True, result.get("msg")
    assert result["saml_settings"]["idp_certificate"] == pem(rotated)


def test_a_chain_in_other_text_is_the_chain_the_cluster_stores(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "idp_certificate": pem(SIGNING) + pem(INTERMEDIATE)}, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": "leaf\n" + pem(SIGNING, width=72) + "intermediate\n" + pem(INTERMEDIATE)})

    assert result["changed"] is False, result.get("diff") or result.get("msg")


def test_a_chain_in_another_order_is_a_change(fake_api, run_module):
    swapped = pem(INTERMEDIATE) + pem(SIGNING)
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "idp_certificate": pem(SIGNING) + pem(INTERMEDIATE)}, etag='"MaSODg"')
    fake.expect("PATCH", SAML, body={"idp_certificate": swapped}, response=CONFIGURED)
    fake.expect("GET", SAML, response={**CONFIGURED, "idp_certificate": swapped}, etag='"BcIm5A"')

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": swapped})

    assert result["changed"] is True, result.get("msg")


@pytest.mark.parametrize("given", ["not a certificate", "-----BEGIN CERTIFICATE-----\n!!!!\n-----END CERTIFICATE-----\n"], ids=["no block", "bad base64"])
def test_a_value_with_no_pem_certificate_fails_before_any_request(fake_api, run_module, given):
    fake = fake_api()

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": given})

    assert result["failed"] is True
    assert "idp_certificate" in result["msg"] and "PEM" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_clearing_a_certificate_the_cluster_holds_fails_before_any_write(fake_api, run_module):
    """G-ID-SAML-CERT: once set, the cluster refuses an empty certificate with ssl_certificate_invalid_error."""
    fake = fake_api()
    fake.expect("GET", SAML, response=CONFIGURED, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": ""})

    assert result["failed"] is True
    assert "G-ID-SAML-CERT" in result["msg"] and "enabled: false" in result["msg"]
    assert writes(fake) == []


def test_an_empty_certificate_on_a_cluster_with_none_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=stored(), etag='"AAAAAA"')

    result = run_module(saml_settings, {**CONNECTION, "idp_certificate": "", "enabled": False})

    assert result["changed"] is False, result.get("msg")
    assert result["saml_settings"] == stored()


def test_check_mode_reports_the_change_and_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=stored(), etag='"AAAAAA"')

    result = run_module(saml_settings, {**CONNECTION, "idp_sso_url": "https://idp.example.com/sso", "cluster_dns_name": "qumulo.example.com"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["saml_settings"] == stored(idp_sso_url="https://idp.example.com/sso", cluster_dns_name="qumulo.example.com")
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=stored(), etag='"AAAAAA"')

    result = run_module(saml_settings, {**CONNECTION, "idp_entity_id": "https://idp.example.com/metadata"}, check_mode=True, diff=True)

    assert result["diff"]["before"] == stored()
    assert result["diff"]["after"] == stored(idp_entity_id="https://idp.example.com/metadata")
    assert writes(fake) == []


def test_turning_on_require_sso_while_sign_in_is_on_warns_that_ad_users_lose_password_sign_in(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=CONFIGURED, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, "require_sso": True}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert any("require_sso" in warning and "password" in warning for warning in result["warnings"]), result["warnings"]


def test_require_sso_while_sign_in_is_off_does_not_warn(fake_api, run_module):
    """The cluster requires SSO only while sign-in is on, so require_sso alone takes no password away."""
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "enabled": False}, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, "require_sso": True}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == []


def test_require_sso_already_on_does_not_warn(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "require_sso": True}, etag='"MaSODg"')

    result = run_module(saml_settings, {**CONNECTION, "require_sso": True})

    assert result["changed"] is False, result.get("msg")
    assert result["warnings"] == []


def test_turning_sign_in_on_without_any_certificate_fails_before_any_write(fake_api, run_module):
    """QUIRK-032: the cluster turns sign-in on without a certificate, so the module refuses."""
    fake = fake_api()
    fake.expect("GET", SAML, response=stored(), etag='"AAAAAA"')

    result = run_module(saml_settings, {**CONNECTION, "enabled": True, "idp_sso_url": "https://idp.example.com/sso"})

    assert result["failed"] is True
    assert "idp_certificate" in result["msg"]
    assert writes(fake) == []


def test_turning_sign_in_on_with_the_certificate_set_earlier_warns(fake_api, run_module):
    """G-ID-SAML-CERT: a certificate once set stays, so sign-in that relies on it without checking it is warned about."""
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "enabled": False}, etag='"MaSODg"')
    fake.expect("GET", AD_STATUS, response={"status": "JOINED_TO_DOMAIN", "domain": "corp.example.com"})

    result = run_module(saml_settings, {**CONNECTION, "enabled": True}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert any("G-ID-SAML-CERT" in warning for warning in result["warnings"]), result["warnings"]


def test_turning_sign_in_on_outside_a_domain_warns(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "enabled": False}, etag='"MaSODg"')
    fake.expect("GET", AD_STATUS, response={"status": "NOT_IN_DOMAIN", "domain": ""})
    fake.expect("PATCH", SAML, body={"enabled": True}, if_match='"MaSODg"', response=CONFIGURED)
    fake.expect("GET", SAML, response=CONFIGURED, etag='"BcIm5A"')

    result = run_module(saml_settings, {**CONNECTION, "enabled": True, "idp_certificate": pem(SIGNING)})

    assert result["changed"] is True, result.get("msg")
    assert [warning for warning in result["warnings"] if "not joined" in warning], result["warnings"]


def test_turning_sign_in_on_outside_a_domain_in_check_mode_notes_the_join_it_expects(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "enabled": False}, etag='"MaSODg"')
    fake.expect("GET", AD_STATUS, response={"status": "NOT_IN_DOMAIN", "domain": ""})

    result = run_module(saml_settings, {**CONNECTION, "enabled": True, "idp_certificate": pem(SIGNING)}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == []
    assert any("not joined" in note and "earlier task" in note for note in result["notes"]), result.get("notes")


def test_turning_sign_in_on_in_a_domain_does_not_warn(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response={**CONFIGURED, "enabled": False}, etag='"MaSODg"')
    fake.expect("GET", AD_STATUS, response={"status": "JOINED_TO_DOMAIN", "domain": "corp.example.com"})

    result = run_module(saml_settings, {**CONNECTION, "enabled": True, "idp_certificate": pem(SIGNING)}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["warnings"] == []


def test_a_lost_patch_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", SAML, response=stored(), etag='"AAAAAA"')
    fake.expect("PATCH", SAML, raises=TimeoutError("timed out"))
    fake.expect("GET", SAML, response=stored(cluster_dns_name="qumulo.example.com"), etag='"BcIm5A"')

    result = run_module(saml_settings, {**CONNECTION, "cluster_dns_name": "qumulo.example.com"})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]
    assert [e.method for e in writes(fake)] == ["PATCH"]
