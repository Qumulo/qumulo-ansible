#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: monitoring_settings
short_description: Manage a Qumulo cluster's cloud monitoring settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets whether the cluster sends monitoring data to Qumulo's cloud monitoring service, and how it reaches it, through
    its host and port, proxies, the remote-support VPN and the reporting period.
  - Only the options that differ from the cluster are written, in one PATCH. Each option left unset keeps the cluster's
    value.
  - The Nexus fields of the same settings belong to M(qumulo.cluster.nexus_connection) and M(qumulo.cluster.nexus_registration);
    this module never writes or returns them.
options:
  enabled:
    description:
      - Whether cloud monitoring is on.
      - Turning it off also turns off the Nexus connection (C(nexus_enabled)), and the module warns when that happens.
    type: bool
  mq_host:
    description: Host of the cloud monitoring service.
    type: str
  mq_port:
    description: Port of the cloud monitoring service.
    type: int
  mq_proxy_host:
    description: Host of an HTTP proxy for the cloud monitoring service; an empty string means no proxy.
    type: str
  mq_proxy_port:
    description: Port of that proxy.
    type: int
  s3_proxy_host:
    description: Host the cluster uploads monitoring files to.
    type: str
  s3_proxy_port:
    description: Port of that host.
    type: int
  s3_proxy_disable_https:
    description: Upload monitoring files over plain HTTP.
    type: bool
  vpn_enabled:
    description: Whether the remote-support VPN is on.
    type: bool
  vpn_host:
    description: Host of the remote-support VPN.
    type: str
  period:
    description: Seconds between monitoring reports.
    type: int
  wait:
    description:
      - After a write, wait until every node reports monitoring V(CONNECTED), or V(DISABLED) when O(enabled) is false.
      - A node that reports V(ERROR) fails the task at once.
      - Without a write there is nothing to wait for.
    type: bool
    default: false
  wait_timeout:
    description: Seconds to wait when O(wait) is true.
    type: int
    default: 300
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
  - "API: GET and PATCH /v1/support/settings (If-Match), then GET /v1/support/status/."
  - The settings are always sent with PATCH, never PUT (QUIRK-010).
  - The module follows the change at C(/v1/support/status/), not at the C(/v1/support/status) that PATCH names (QUIRK-003).
  - A write that meets HTTP 412 is read and sent again, up to five sends in all.
  - On Qumulo-managed cloud clusters the server refuses to turn monitoring on or off, and the module fails with its message.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# Cloud monitoring does not deliver a license. Apply the license document with qumulo.cluster.license.
# The wait follows a write; on a cluster with monitoring already on, check the nodes with
# qumulo.cluster.monitoring_info's status subset.
- name: Turn on cloud monitoring and wait until every node is connected
  qumulo.cluster.monitoring_settings:
    enabled: true
    wait: true
    wait_timeout: 600

- name: Send monitoring data to Qumulo through a proxy
  qumulo.cluster.monitoring_settings:
    enabled: true
    mq_proxy_host: proxy.example.com
    mq_proxy_port: 3128
    wait: true

- name: Report to a monitoring service at another host
  qumulo.cluster.monitoring_settings:
    mq_host: monitor.example.com
    mq_port: 443

- name: Upload monitoring files through a gateway over plain HTTP
  qumulo.cluster.monitoring_settings:
    s3_proxy_host: gateway.example.com
    s3_proxy_port: 8080
    s3_proxy_disable_https: true

- name: Send monitoring reports every two minutes
  qumulo.cluster.monitoring_settings:
    period: 120

- name: Turn the remote-support VPN on
  qumulo.cluster.monitoring_settings:
    vpn_enabled: true

- name: Turn the remote-support VPN off
  qumulo.cluster.monitoring_settings:
    vpn_enabled: false

- name: Preview dropping the proxy
  qumulo.cluster.monitoring_settings:
    mq_proxy_host: ""
  check_mode: true
  diff: true
  register: monitoring_preview

- name: Show the settings the change would leave
  ansible.builtin.debug:
    var: monitoring_preview.monitoring_settings

- name: Reach the monitoring service without a proxy
  qumulo.cluster.monitoring_settings:
    mq_proxy_host: ""
    wait: true

# Turning monitoring off turns the Nexus connection off too, and the module warns when it does.
- name: Stop sending monitoring data to Qumulo
  qumulo.cluster.monitoring_settings:
    enabled: false
    wait: true
"""

RETURN = r"""
monitoring_settings:
  description: The monitoring settings after the task, or in check mode the settings the task would leave, without the Nexus fields.
  returned: always
  type: dict
  contains:
    enabled:
      description: Whether cloud monitoring is on.
      type: bool
    mq_host:
      description: Host of the cloud monitoring service.
      type: str
    mq_port:
      description: Port of the cloud monitoring service.
      type: int
    mq_proxy_host:
      description: Host of the proxy for the cloud monitoring service; empty when there is none.
      type: str
    mq_proxy_port:
      description: Port of that proxy.
      type: int
    s3_proxy_host:
      description: Host the cluster uploads monitoring files to.
      type: str
    s3_proxy_port:
      description: Port of that host.
      type: int
    s3_proxy_disable_https:
      description: Whether monitoring files are uploaded over plain HTTP.
      type: bool
    vpn_enabled:
      description: Whether the remote-support VPN is on.
      type: bool
    vpn_host:
      description: Host of the remote-support VPN.
      type: str
    period:
      description: Seconds between monitoring reports.
      type: int
  sample:
    enabled: true
    mq_host: monitor.example.com
    mq_port: 443
    mq_proxy_host: ""
    mq_proxy_port: 0
    s3_proxy_host: upload.example.com
    s3_proxy_port: 443
    s3_proxy_disable_https: false
    vpn_enabled: false
    vpn_host: vpn.example.com
    period: 60
monitoring_status:
  description:
    - Each node's status from C(/v1/support/status/), read after the write, and after the wait with O(wait).
    - On a failed wait it is also returned with the failure, as the last status read.
  returned: when the settings were written, and when a wait failed
  type: list
  elements: dict
  sample: [{node_id: 1, monitoring: CONNECTED, file_upload: DISABLED, vpn_connection: DISABLED}]
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
  sample: {period: 60}
observed:
  description: The values the cluster holds for the options in RV(expected).
  returned: when the cluster reads back different values from the ones written
  type: dict
  sample: {period: 300}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import MonitoringSettings

ARGUMENT_SPEC = {
    "enabled": {"type": "bool"},
    "mq_host": {"type": "str"},
    "mq_port": {"type": "int"},
    "mq_proxy_host": {"type": "str"},
    "mq_proxy_port": {"type": "int"},
    "s3_proxy_host": {"type": "str"},
    "s3_proxy_port": {"type": "int"},
    "s3_proxy_disable_https": {"type": "bool"},
    "vpn_enabled": {"type": "bool"},
    "vpn_host": {"type": "str"},
    "period": {"type": "int"},
    "wait": {"type": "bool", "default": False},
    "wait_timeout": {"type": "int", "default": 300},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), MonitoringSettings)


if __name__ == "__main__":
    main()
