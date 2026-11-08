#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: saml_settings
short_description: Manage a Qumulo cluster's SAML single sign-on settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets how Active Directory users sign in to the cluster's Web UI through a SAML identity provider (IdP).
  - An option left unset keeps the cluster's current value.
  - Every setting is read back, the IdP certificate included, which is public. The cluster stores the certificate
    re-encoded, without any text around it, so the module compares the certificates themselves, not the text.
  - SAML sign-in works only for users of the Active Directory domain the cluster is joined to. The cluster does not
    check the IdP when the settings are written, so a successful task does not show that sign-in works (G-ID-SAML).
options:
  enabled:
    description:
      - Whether users may sign in through the IdP.
      - Sign-in needs the IdP's certificate. The cluster turns it on without one (QUIRK-032), so the module refuses
        V(true) before any write when neither the task nor the cluster has a certificate.
      - Turning it on without O(idp_certificate) relies on a certificate this task did not give, which can never be
        removed (G-ID-SAML-CERT), and warns.
      - Turning it on warns when the cluster is not joined to Active Directory; in check mode it notes it instead,
        since an earlier task may join the domain.
    type: bool
  idp_sso_url:
    description: The IdP's single sign-on URL, where the cluster sends users to sign in.
    type: str
  idp_certificate:
    description:
      - The certificate the IdP signs its responses with, in PEM, or a chain of certificates. Text around the
        certificates is ignored, as the cluster ignores it.
      - Left unset, the certificate the cluster holds is kept.
      - A value with no PEM certificate fails before any request is sent.
      - The cluster keeps a certificate once one is set and refuses an empty one, so V("") fails when a certificate is
        set (G-ID-SAML-CERT).
    type: str
  idp_entity_id:
    description: The IdP's entity ID, as its metadata names it.
    type: str
  cluster_dns_name:
    description: The name users reach the cluster's Web UI at, which the IdP's application for the cluster names.
    type: str
  require_sso:
    description:
      - Whether Active Directory users must sign in through the IdP while O(enabled) is true. They can then no longer
        sign in to the Web UI, C(qq) or the REST API with a password, so the module warns when a task makes both true.
        Local users, such as C(admin), still can.
    type: bool
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET and PATCH /v1/saml/settings, with If-Match; GET /v1/ad/status when the task turns sign-in on. PUT is
    never sent, since it validates the certificate even when none is given."
  - "Registers: G-ID-SAML, G-ID-SAML-CERT, QUIRK-032."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# Only users of the domain the cluster has joined (qumulo.cluster.ad_domain) can sign in. Give idp_certificate, so that
# the cluster holds the IdP's own certificate. Without it, sign-in relies on whatever certificate the cluster already
# holds, which a task did not give and nobody can remove, and the module warns.
- name: Let AD users sign in to the Web UI through the IdP
  qumulo.cluster.saml_settings:
    enabled: true
    idp_sso_url: https://idp.example.com/saml2/sso
    idp_entity_id: https://idp.example.com/metadata
    idp_certificate: "{{ lookup('ansible.builtin.file', 'files/idp-signing.crt') }}"
    cluster_dns_name: qumulo.corp.example.com

- name: Preview a new signing certificate from the IdP
  qumulo.cluster.saml_settings:
    idp_certificate: "{{ lookup('ansible.builtin.file', 'files/idp-signing-2027.crt') }}"
  check_mode: true
  diff: true
  register: saml_preview

- name: Show the settings the new certificate would leave
  ansible.builtin.debug:
    var: saml_preview.saml_settings

- name: Rotate the IdP's signing certificate
  qumulo.cluster.saml_settings:
    idp_certificate: "{{ lookup('ansible.builtin.file', 'files/idp-signing-2027.crt') }}"

- name: Point the cluster at a different IdP, leaving sign-in as it is
  qumulo.cluster.saml_settings:
    idp_sso_url: https://idp2.example.com/saml2/sso
    idp_entity_id: https://idp2.example.com/metadata
    idp_certificate: "{{ lookup('ansible.builtin.file', 'files/idp2-signing.crt') }}"

# While SAML sign-in is on, AD users sign in only through the IdP. Local users, such as admin, still sign in with a
# password; keep one. require_sso has effect only while enabled is true.
- name: Require AD users to sign in through the IdP
  qumulo.cluster.saml_settings:
    enabled: true
    require_sso: true

# The cluster keeps the IdP certificate once one is set and refuses an empty one, so turn sign-in off with enabled.
- name: Turn SAML sign-in off, keeping the other settings
  qumulo.cluster.saml_settings:
    enabled: false
"""

RETURN = r"""
saml_settings:
  description: The settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    enabled:
      description: Whether users may sign in through the IdP.
      type: bool
    idp_sso_url:
      description: The IdP's single sign-on URL, or V("") when unset.
      type: str
    idp_certificate:
      description: The IdP's certificates as canonical PEM, one block per certificate, or V("") when unset.
      type: str
    idp_entity_id:
      description: The IdP's entity ID, or V("") when unset.
      type: str
    cluster_dns_name:
      description: The name users reach the cluster's Web UI at, or V("") when unset.
      type: str
    require_sso:
      description: Whether Active Directory users must sign in through the IdP.
      type: bool
  sample:
    enabled: true
    idp_sso_url: https://idp.example.com/saml2/sso
    idp_certificate: "-----BEGIN CERTIFICATE-----\nMIIDFTCCAf2gAwIBAgIU...\n-----END CERTIFICATE-----\n"
    idp_entity_id: https://idp.example.com/metadata
    cluster_dns_name: qumulo.corp.example.com
    require_sso: false
notes:
  description:
    - What the module noticed, such as a cluster not joined to Active Directory yet in check mode when sign-in is turned
      on, or a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["SAML sign-in is on, but the cluster is not joined to Active Directory, and only users of the joined domain can sign in through the IdP;
    an earlier task is expected to join it"]
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the settings back did not confirm it.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the settings that did not converge.
  returned: when the settings did not converge
  type: dict
  sample: {require_sso: true}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the settings did not converge
  type: dict
  sample: {require_sso: false}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.saml import SamlSettings

ARGUMENT_SPEC = {
    "enabled": {"type": "bool"},
    "idp_sso_url": {"type": "str"},
    "idp_certificate": {"type": "str"},
    "idp_entity_id": {"type": "str"},
    "cluster_dns_name": {"type": "str"},
    "require_sso": {"type": "bool"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), SamlSettings)


if __name__ == "__main__":
    main()
