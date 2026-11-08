#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: ssl_certificate
short_description: Install the TLS certificate a Qumulo cluster serves
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Installs a certificate chain and its private key as the certificate the cluster serves for its REST API, web UI
    and NFS over TLS.
  - The cluster offers no way to read the installed certificate or key back (G-SSL-READBACK). The module connects to
    each address in O(probe) and compares the certificate it serves with the first certificate of O(certificate). It
    installs when any probed address serves another certificate, and never compares keys.
  - After installing, the module waits up to O(wait_timeout) seconds until every probed address serves the new
    certificate.
  - When O(validate_certs=true), the module checks before installing, by serving the new chain to itself, that the chain
    verifies against O(ca_path) for O(api_host). It refuses a chain that does not verify unless
    O(allow_management_disruption=true).
  - On Python 3.13 and later, a self-signed certificate pinned through O(ca_path) must be a certificate authority
    itself, with C(basicConstraints CA:TRUE); the module warns about a self-signed certificate that is not.
options:
  certificate:
    description:
      - The certificate chain in PEM, the cluster's own certificate first, then any intermediates. The chain must
        reach a root certificate authority; the cluster refuses one that does not.
    type: str
    required: true
  private_key:
    description: The private key of the first certificate, in PEM. A key that does not match fails before the install when O(validate_certs=true),
      unless O(allow_management_disruption=true); the cluster refuses it otherwise.
    type: str
    required: true
  probe:
    description:
      - The addresses to compare, as V(host) or V(host:port); a V(host) alone uses O(api_port). An IPv6 address with a
        port is written V([fd00::5]:8000).
      - Empty probes O(api_host) on O(api_port).
    type: list
    elements: str
    default: []
  wait_timeout:
    description: Seconds to wait, after installing, until every probed address serves the new certificate.
    type: int
    default: 120
  allow_management_disruption:
    description: Install a chain that does not verify against O(ca_path) for O(api_host) although O(validate_certs=true).
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
    details:
      - The diff maps each probed C(host:port) to the SHA-256 digest it serves before the task, and to the digest of
        O(certificate) after it.
  idempotent:
    support: partial
    details:
      - Compared by the certificate each probed address serves; an address that is not probed is not compared.
notes:
  - "API: PUT /v2/cluster/settings/ssl/certificate; TLS connections to each probed address."
  - "Registers: G-SSL-READBACK."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Serve the edge's certificate on the REST API port
  qumulo.cluster.ssl_certificate:
    certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01.pem') }}"
    private_key: "{{ vault_edge01_tls_key }}"

# Without probe, only api_host on api_port is compared. To cover a cluster of several nodes, list every node's
# address, and port 443 for the web UI.
- name: Serve the edge's certificate, and check both of its ports
  qumulo.cluster.ssl_certificate:
    certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01.pem') }}"
    private_key: "{{ vault_edge01_tls_key }}"
    probe: ["{{ qumulo_api_host }}:8000", "{{ qumulo_api_host }}:443"]

- name: Preview serving the renewed certificate
  qumulo.cluster.ssl_certificate:
    certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-renewed.pem') }}"
    private_key: "{{ vault_edge01_tls_key_renewed }}"
    probe: ["{{ qumulo_api_host }}:8000", "{{ qumulo_api_host }}:443"]
  check_mode: true
  diff: true
  register: tls_preview

- name: Say whether any probed address would change, and to which certificate
  ansible.builtin.debug:
    msg: "{{ 'would serve ' ~ tls_preview.ssl_certificate.sha256 if tls_preview.changed else 'already served' }}"

- name: Serve the renewed certificate
  qumulo.cluster.ssl_certificate:
    certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-renewed.pem') }}"
    private_key: "{{ vault_edge01_tls_key_renewed }}"
    probe: ["{{ qumulo_api_host }}:8000", "{{ qumulo_api_host }}:443"]

- name: Give a slow node more time to reload the renewed certificate
  qumulo.cluster.ssl_certificate:
    certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-renewed.pem') }}"
    private_key: "{{ vault_edge01_tls_key_renewed }}"
    probe: ["{{ qumulo_api_host }}:8000", "{{ qumulo_api_host }}:443"]
    wait_timeout: 300

# A chain from another certificate authority does not verify against the current ca_path, so the module refuses it
# without allow_management_disruption. Later tasks must then connect with the new authority's certificate in ca_path.
- name: Serve a certificate from the site's new certificate authority
  qumulo.cluster.ssl_certificate:
    certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-new-ca.pem') }}"
    private_key: "{{ vault_edge01_tls_key_new_ca }}"
    probe: ["{{ qumulo_api_host }}:8000", "{{ qumulo_api_host }}:443"]
    allow_management_disruption: true
"""

RETURN = r"""
ssl_certificate:
  description: The certificate the task leaves served, or in check mode would leave served.
  returned: always
  type: dict
  contains:
    sha256:
      description: The SHA-256 digest of the first certificate of O(certificate), in DER, as hex.
      type: str
    served:
      description:
        - The SHA-256 digest of the certificate each probed address serves, by V(host:port), an IPv6 host without brackets.
        - In check mode, when the task would change something, the digest each address would serve.
      type: dict
  sample: {sha256: 9f2a0cad..., served: {"edge01.example.com:8000": 9f2a0cad...}}
served:
  description: The digest each probed address serves, or V(unreachable (reason)), by V(host:port), an IPv6 host without brackets.
  returned: when the install was sent but a probed address still served another certificate after O(wait_timeout)
  type: dict
  sample: {"edge01.example.com:8000": 9f2a0cad..., "edge01.example.com:443": "unreachable (timed out)"}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import SslCertificate
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "certificate": {"type": "str", "required": True},
    "private_key": {"type": "str", "required": True, "no_log": True},
    "probe": {"type": "list", "elements": "str", "default": []},
    "wait_timeout": {"type": "int", "default": 120},
    "allow_management_disruption": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), SslCertificate)


if __name__ == "__main__":
    main()
