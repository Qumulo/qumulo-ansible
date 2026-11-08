#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: ssl_ciphers
short_description: Manage the TLS cipher suites a Qumulo cluster accepts
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the TLS 1.2 and TLS 1.3 cipher suites the cluster accepts, as two lists in the cluster's order of preference.
    The order is part of the setting, so the lists are compared in order.
  - The cluster always accepts V(ECDHE-RSA-AES128-SHA256) for TLS 1.2 and V(TLS_AES_128_GCM_SHA256) for TLS 1.3, and adds
    each to the end of a list that leaves it out (QUIRK-060). The module adds them the same way before it compares, so a
    second run changes nothing.
  - Since TLS 1.3 always keeps its suite, no list cuts the controller off from the cluster.
  - A list left unset keeps the cluster's.
options:
  tls_1_2:
    description:
      - The TLS 1.2 cipher suites, by their OpenSSL names, such as V(ECDHE-RSA-AES256-GCM-SHA384).
      - An empty list, or one that names a suite twice, fails before any request is sent.
    type: list
    elements: str
  tls_1_3:
    description:
      - The TLS 1.3 cipher suites, by their standard names, such as V(TLS_AES_256_GCM_SHA384).
      - An empty list, or one that names a suite twice, fails before any request is sent.
    type: list
    elements: str
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
  - "API: GET and PUT /v2/cluster/settings/ssl/ciphers, with If-Match."
  - The cluster refuses a TLS 1.3 name it does not know, and a TLS 1.2 name, such as V(ALL), that would let in RC4 or
    anonymous suites; the module fails with the cluster's message.
  - The cluster stores a TLS 1.2 name it does not know, so a misspelt name is kept as written and never used.
  - The cluster takes an empty list as the one suite it adds, so the module refuses an empty list itself (QUIRK-061).
  - The certificate the cluster serves is set by M(qumulo.cluster.ssl_certificate).
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Accept AES-GCM suites, AES-256 first
  qumulo.cluster.ssl_ciphers:
    tls_1_2:
      - ECDHE-RSA-AES256-GCM-SHA384
      - ECDHE-RSA-AES128-GCM-SHA256
    tls_1_3:
      - TLS_AES_256_GCM_SHA384
      - TLS_AES_128_GCM_SHA256

- name: Preview a change to the TLS 1.3 suites, leaving TLS 1.2 as it is
  qumulo.cluster.ssl_ciphers:
    tls_1_3: [TLS_AES_256_GCM_SHA384]
  check_mode: true
  diff: true
  register: ciphers_preview

# The TLS 1.3 list ends with TLS_AES_128_GCM_SHA256, which the module adds because the task left it out.
- name: Show the suites the change would leave
  ansible.builtin.debug:
    var: ciphers_preview.ssl_ciphers

- name: Accept only AES-256 GCM for TLS 1.3, and the suite the cluster requires
  qumulo.cluster.ssl_ciphers:
    tls_1_3: [TLS_AES_256_GCM_SHA384]

- name: Accept only AES-256 GCM for TLS 1.2, and the suite the cluster requires
  qumulo.cluster.ssl_ciphers:
    tls_1_2: [ECDHE-RSA-AES256-GCM-SHA384]
"""

RETURN = r"""
ssl_ciphers:
  description:
    - The cipher suites after the task, or in check mode the suites the task would leave, each list with the suite the
      cluster adds.
  returned: always
  type: dict
  contains:
    tls_1_2:
      description: The TLS 1.2 cipher suites, in order of preference.
      type: list
      elements: str
    tls_1_3:
      description: The TLS 1.3 cipher suites, in order of preference.
      type: list
      elements: str
  sample:
    tls_1_2: [ECDHE-RSA-AES256-GCM-SHA384, ECDHE-RSA-AES128-GCM-SHA256, ECDHE-RSA-AES128-SHA256]
    tls_1_3: [TLS_AES_256_GCM_SHA384, TLS_AES_128_GCM_SHA256]
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
maybe_applied:
  description: V(true) when a write was sent and the connection was lost before the cluster answered, and reading the settings back did not confirm
    it. The task fails with C(changed) true.
  returned: on a failure after such a write
  type: bool
  sample: true
expected:
  description: The values the task wrote, for the options that did not converge.
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {tls_1_3: [TLS_AES_256_GCM_SHA384, TLS_AES_128_GCM_SHA256]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {tls_1_3: [TLS_AES_128_GCM_SHA256]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import SslCiphers
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "tls_1_2": {"type": "list", "elements": "str"},
    "tls_1_3": {"type": "list", "elements": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), SslCiphers)


if __name__ == "__main__":
    main()
