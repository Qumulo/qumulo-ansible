#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: nexus_connection
short_description: Manage a Qumulo cluster's connection to Qumulo's Nexus service
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets whether the cluster connects to Nexus, where, how often, and which Nexus capabilities it allows.
  - Only the options that differ from the cluster are written, in one PATCH. Each option left unset keeps the cluster's
    value.
  - Registering the cluster with Nexus is M(qumulo.cluster.nexus_registration)'s job.
options:
  nexus_enabled:
    description:
      - Whether the cluster connects to Nexus.
      - Turning it on needs cloud monitoring on (M(qumulo.cluster.monitoring_settings) O(qumulo.cluster.monitoring_settings#module:enabled)),
        and the module fails before any write when it is off (QUIRK-015).
      - Turning cloud monitoring off turns this off too.
    type: bool
  nexus_host:
    description: Host of the Nexus service the cluster connects to.
    type: str
  nexus_port:
    description: Port of that host.
    type: int
  nexus_interval:
    description: Seconds between the cluster's reports to Nexus.
    type: int
  nexus_capability_remote_support:
    description: Whether Qumulo Care may reach the cluster through Nexus for remote support.
    type: bool
  nexus_capability_sso:
    description: Whether users may sign in to the cluster through Nexus single sign-on.
    type: bool
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - With cloud monitoring off, check mode predicts turning the connection on and warns, because an earlier
        M(qumulo.cluster.monitoring_settings) task in the same check-mode run turned nothing on.
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET and PATCH /v1/nexus/connection (If-Match); GET /v1/support/settings when the task turns the connection on."
  - A write that meets HTTP 412 is read and sent again, up to five sends in all.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# The Nexus connection needs cloud monitoring on, and the module fails before any write when it is off.
- name: Turn on cloud monitoring and connect the cluster to Nexus
  block:
    - name: Turn on cloud monitoring
      qumulo.cluster.monitoring_settings:
        enabled: true
        wait: true

    - name: Connect the cluster to Nexus
      qumulo.cluster.nexus_connection:
        nexus_enabled: true

- name: Report to Nexus every minute and allow remote support
  qumulo.cluster.nexus_connection:
    nexus_interval: 60
    nexus_capability_remote_support: true

- name: Connect to a Nexus service at another host
  qumulo.cluster.nexus_connection:
    nexus_host: nexus.example.com
    nexus_port: 443

- name: Preview allowing Nexus single sign-on
  qumulo.cluster.nexus_connection:
    nexus_capability_sso: true
  check_mode: true
  diff: true
  register: nexus_preview

- name: Show the connection settings the change would leave
  ansible.builtin.debug:
    var: nexus_preview.nexus_connection

- name: Let users sign in through Nexus single sign-on
  qumulo.cluster.nexus_connection:
    nexus_capability_sso: true

- name: Stop Qumulo Care reaching the cluster through Nexus
  qumulo.cluster.nexus_connection:
    nexus_capability_remote_support: false

- name: Disconnect the cluster from Nexus
  qumulo.cluster.nexus_connection:
    nexus_enabled: false
"""

RETURN = r"""
nexus_connection:
  description: The Nexus connection settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    nexus_enabled:
      description: Whether the cluster connects to Nexus.
      type: bool
    nexus_host:
      description: Host of the Nexus service.
      type: str
    nexus_port:
      description: Port of that host.
      type: int
    nexus_interval:
      description: Seconds between the cluster's reports to Nexus.
      type: int
    nexus_capability_remote_support:
      description: Whether Qumulo Care may reach the cluster through Nexus for remote support.
      type: bool
    nexus_capability_sso:
      description: Whether users may sign in to the cluster through Nexus single sign-on.
      type: bool
  sample:
    nexus_enabled: true
    nexus_host: api.nexus.qumulo.com
    nexus_port: 443
    nexus_interval: 30
    nexus_capability_remote_support: true
    nexus_capability_sso: false
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["write reconciled after a lost connection"]
maybe_applied:
  description: V(true) when a write was sent, its answer was lost and reading back did not confirm it; the task fails with C(changed) set.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote for the options that did not converge.
  returned: when the cluster reads back different values from the ones written
  type: dict
  sample: {nexus_interval: 60}
observed:
  description: The values the cluster holds for the options in RV(expected).
  returned: when the cluster reads back different values from the ones written
  type: dict
  sample: {nexus_interval: 300}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import NexusConnection

ARGUMENT_SPEC = {
    "nexus_enabled": {"type": "bool"},
    "nexus_host": {"type": "str"},
    "nexus_port": {"type": "int"},
    "nexus_interval": {"type": "int"},
    "nexus_capability_remote_support": {"type": "bool"},
    "nexus_capability_sso": {"type": "bool"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), NexusConnection)


if __name__ == "__main__":
    main()
