#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: monitoring_info
short_description: Read a Qumulo cluster's monitoring, Nexus, audit and encryption settings and status
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the cloud monitoring settings and each node's monitoring status, the Nexus connection and registration,
    the audit log destinations, and the encryption key store's status, and changes nothing.
  - The V(settings) and V(nexus_connection) subsets have the option shape of M(qumulo.cluster.monitoring_settings) and
    M(qumulo.cluster.nexus_connection), so they can be fed back to those modules.
  - No subset returns a secret. A pending Nexus join key, which the cluster returns in cleartext, is left out, and the
    support VPN keys are never read.
  - On a node that is not yet part of a cluster (state C(UNCONFIGURED)) no login is possible, so every subset is listed in
    RV(unavailable_subsets), and the module warns.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V(settings) is the cloud monitoring settings, V(status) each node's monitoring status, V(nexus_connection) and
        V(nexus_registration) the Nexus connection and registration, V(audit) the C(syslog) and C(cloudwatch) audit log destinations, and
        V(encryption) the encryption key store's status.
    type: list
    elements: str
    choices: [all, settings, status, nexus_connection, nexus_registration, audit, encryption]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(settings) GET /v1/support/settings; V(status) GET /v1/support/status/; V(nexus_connection) GET
    /v1/nexus/connection; V(nexus_registration) GET /v1/nexus/registration; V(audit) GET /v1/audit/syslog/config,
    /v1/audit/syslog/status, /v1/audit/cloudwatch/config and /v1/audit/cloudwatch/status; V(encryption) GET
    /v2/encryption/key-store/status."
  - A route this cluster does not offer (HTTP 404, 405, 406 or 501) makes its subset unavailable. A cluster that cannot
    send audit logs to CloudWatch reports C(cloudwatch) as V(null). Any other error, such as a missing RBAC privilege,
    fails the module.
  - The license is M(qumulo.cluster.cluster_info)'s V(license) subset.
  - V(audit) reports only the destinations named C(syslog) and C(cloudwatch), which the v1 routes it reads cover, and no
    C(local) destination; M(qumulo.cluster.capture_info)'s C(audit) area reads every destination (G-MON-AUDIT-INFO).
"""

EXAMPLES = r"""
# These tasks carry no connection options, except where a task names another cluster. Set them once for the play
# with module_defaults for group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read every monitoring, Nexus, audit and encryption fact
  qumulo.cluster.monitoring_info:
  register: monitoring

- name: Show the cloud monitoring settings
  ansible.builtin.debug:
    var: monitoring.monitoring.settings

- name: Stop unless the cluster is registered with Nexus
  block:
    - name: Read the Nexus registration
      qumulo.cluster.monitoring_info:
        gather_subset: [nexus_registration]
      register: nexus_state

    - name: Assert that the cluster is registered
      ansible.builtin.assert:
        that: nexus_state.monitoring.nexus_registration.registration_status == 'REGISTERED'
        fail_msg: The cluster is not registered with Nexus.

- name: Fail when a node does not report monitoring as connected
  block:
    - name: Read each node's monitoring status
      qumulo.cluster.monitoring_info:
        gather_subset: [status]
      register: node_status

    - name: Assert that every node is connected
      ansible.builtin.assert:
        that: node_status.monitoring.status | map(attribute='monitoring') | unique | list == ['CONNECTED']

# Destinations with other names, which qumulo.cluster.audit_destination manages, are in qumulo.cluster.capture_info's
# audit area (G-MON-AUDIT-INFO).
- name: Read the syslog and CloudWatch audit destinations and their connection status
  qumulo.cluster.monitoring_info:
    gather_subset: [audit]
  register: audit_state

# A cluster that does not offer a route leaves its subset out of the facts and names it in unavailable_subsets.
- name: Read the encryption key store, if this cluster offers it
  qumulo.cluster.monitoring_info:
    gather_subset: [encryption]
  register: encryption_state

- name: Show when the key was last rotated
  ansible.builtin.debug:
    var: encryption_state.monitoring.encryption.last_key_rotation_time
  when: "'encryption' not in (encryption_state.unavailable_subsets | default({}))"

# The settings subset has the option shape of monitoring_settings, so it can be applied to another cluster.
# Both clusters must accept the credentials the play sets, since an api_token is bound to one cluster. api_host is a
# task argument here: a task-level module_defaults entry for the group would replace the play's credentials.
- name: Copy one cluster's monitoring settings to another
  block:
    - name: Read the source cluster's monitoring settings
      qumulo.cluster.monitoring_info:
        api_host: edge01.example.com
        gather_subset: [settings]
      register: source_monitoring

    # Name each field to copy; the proxy and VPN fields copy the same way.
    - name: Apply them to the other cluster
      qumulo.cluster.monitoring_settings:
        api_host: edge02.example.com
        enabled: "{{ source_monitoring.monitoring.settings.enabled }}"
        mq_host: "{{ source_monitoring.monitoring.settings.mq_host }}"
        mq_port: "{{ source_monitoring.monitoring.settings.mq_port }}"
        period: "{{ source_monitoring.monitoring.settings.period }}"
"""

RETURN = r"""
monitoring:
  description: The gathered facts. Each key is present only when its subset was gathered and available.
  returned: always
  type: dict
  contains:
    settings:
      description: The cloud monitoring settings, as M(qumulo.cluster.monitoring_settings) takes them.
      type: dict
      sample: {enabled: true, mq_host: monitor.example.com, mq_port: 443, period: 60, vpn_enabled: false}
    status:
      description: Each node's monitoring, file upload and VPN status.
      type: list
      elements: dict
      contains:
        node_id:
          description: The node's ID.
          type: int
          sample: 1
        monitoring:
          description: The node's monitoring connection status, such as V(CONNECTED), V(DISABLED) or V(ERROR).
          type: str
          sample: CONNECTED
        file_upload:
          description: The node's file upload status.
          type: str
          sample: DISABLED
        vpn_connection:
          description: The node's remote-support VPN status.
          type: str
          sample: DISABLED
      sample: [{node_id: 1, monitoring: CONNECTED, file_upload: DISABLED, vpn_connection: DISABLED}]
    nexus_connection:
      description: The Nexus connection settings, as M(qumulo.cluster.nexus_connection) takes them.
      type: dict
      sample: {nexus_enabled: true, nexus_host: api.nexus.qumulo.com, nexus_port: 443, nexus_interval: 30}
    nexus_registration:
      description: The Nexus registration status and when the cluster's Nexus secret was created; no other field is passed on.
      type: dict
      contains:
        registration_status:
          description: The registration status, such as V(NONE), V(PENDING_REGISTRATION) or V(REGISTERED).
          type: str
          sample: REGISTERED
        secret_created_at:
          description: When the cluster's Nexus secret was created.
          type: str
          sample: "2026-10-31T02:00:00Z"
    audit:
      description:
        - The C(syslog) and C(cloudwatch) audit log destinations, as the v1 routes return them, each with its connection C(status).
        - C(cloudwatch) is V(null) on a cluster that cannot send audit logs to CloudWatch.
      type: dict
      contains:
        syslog:
          description: The syslog destination's configuration, with its connection C(status).
          type: dict
        cloudwatch:
          description: The CloudWatch destination's configuration, with its connection C(status); V(null) when the cluster cannot send audit logs to CloudWatch.
          type: dict
      sample: {syslog: {enabled: false, server_address: "", server_port: 0, format: csv, status: {connection_status: AUDIT_LOG_DISABLED}},
               cloudwatch: null}
    encryption:
      description: The encryption key store's type and status, as the cluster reports them.
      type: dict
      sample: {type: Local, status: Encrypted, last_key_rotation_time: "2026-10-31T02:40:19Z"}
unavailable_subsets:
  description: Each requested subset this cluster could not answer, with the reason.
  returned: when a subset was unavailable
  type: dict
  sample: {encryption: "GET /v2/encryption/key-store/status failed: HTTP 404 ..."}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import MONITORING_INFO_SUBSETS

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *MONITORING_INFO_SUBSETS], "default": ["all"]},
}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "monitoring", MONITORING_INFO_SUBSETS)


if __name__ == "__main__":
    main()
