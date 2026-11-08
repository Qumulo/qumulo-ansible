#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: time_settings
short_description: Manage where a Qumulo cluster gets its time
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster's time source, its NTP servers, and whether it prefers the Active Directory domain controller.
  - An option left unset keeps the cluster's current value.
  - Rules the server enforces are checked before any write, on the settings the task would leave.
  - A cluster managed by Qumulo sets its own time and refuses this write; the module then fails and says so.
options:
  source:
    description:
      - V(API_TIME_SOURCE_NTP) takes time from O(ntp_servers), which must then not be empty.
      - V(API_TIME_SOURCE_HYPERVISOR) takes time from the hypervisor. The server accepts it only on Azure, and only
        with no NTP servers and O(use_ad_for_primary=false).
    type: str
    choices: [API_TIME_SOURCE_NTP, API_TIME_SOURCE_HYPERVISOR]
  ntp_servers:
    description:
      - NTP server names or addresses, in order. Compared in order.
      - An empty name, or one with leading or trailing spaces, fails before any request is sent.
    type: list
    elements: str
  use_ad_for_primary:
    description:
      - Take time from the Active Directory domain controller first, and from O(ntp_servers) when it cannot be reached.
      - It takes effect only once the cluster has joined a domain; see M(qumulo.cluster.ad_domain).
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
  - "API: GET and PATCH /v2/time/settings, with If-Match."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Take time from the site's NTP servers
  qumulo.cluster.time_settings:
    source: API_TIME_SOURCE_NTP
    ntp_servers: [ntp1.corp.example.com, ntp2.corp.example.com]
    use_ad_for_primary: false

- name: Preview taking time from the domain controller first
  qumulo.cluster.time_settings:
    use_ad_for_primary: true
  check_mode: true
  diff: true
  register: time_preview

- name: Show the settings the change would leave
  ansible.builtin.debug:
    var: time_preview.time_settings

- name: Replace the NTP servers of a cluster that takes time from NTP
  qumulo.cluster.time_settings:
    ntp_servers: [ntp3.corp.example.com, ntp4.corp.example.com]

# It takes effect only once the cluster has joined a domain, so run it after qumulo.cluster.ad_domain.
# The cluster_baseline role does the same: it sets the NTP servers first and use_ad_for_primary after the join.
- name: Take time from the domain controller first, and from the NTP servers when it cannot be reached
  qumulo.cluster.time_settings:
    use_ad_for_primary: true

- name: Go back to the NTP servers alone
  qumulo.cluster.time_settings:
    use_ad_for_primary: false

# The cluster accepts the hypervisor only on Azure, and only with no NTP servers and use_ad_for_primary false.
- name: Take time from the hypervisor of an Azure cluster
  qumulo.cluster.time_settings:
    source: API_TIME_SOURCE_HYPERVISOR
    ntp_servers: []
    use_ad_for_primary: false
"""

RETURN = r"""
time_settings:
  description: The settings after the task, or in check mode the settings the task would leave.
  returned: always
  type: dict
  contains:
    source:
      description: The time source.
      type: str
    ntp_servers:
      description: The NTP servers, in order.
      type: list
      elements: str
    use_ad_for_primary:
      description: Whether the domain controller is the first source.
      type: bool
  sample: {source: API_TIME_SOURCE_NTP, ntp_servers: [ntp1.corp.example.com], use_ad_for_primary: false}
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
  sample: {ntp_servers: [ntp3.corp.example.com]}
observed:
  description: What the cluster read back for the keys of RV(expected).
  returned: when the read-back after a write differs from what was written; after a lost connection the task returns RV(maybe_applied) instead
  type: dict
  sample: {ntp_servers: [ntp1.corp.example.com]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.network import TimeSettings

ARGUMENT_SPEC = {
    "source": {"type": "str", "choices": ["API_TIME_SOURCE_NTP", "API_TIME_SOURCE_HYPERVISOR"]},
    "ntp_servers": {"type": "list", "elements": "str"},
    "use_ad_for_primary": {"type": "bool"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), TimeSettings)


if __name__ == "__main__":
    main()
