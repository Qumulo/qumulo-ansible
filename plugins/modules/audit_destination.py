#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: audit_destination
short_description: Manage a Qumulo cluster's audit log destination
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or deletes one audit log destination, found by its name. A destination is a syslog server, a
    CloudWatch log group, or the cluster's local audit log.
  - A change sends the destination's whole configuration in one write, with the cluster's values for the options left
    unset. A change of O(type) drops the options of the old type.
  - When O(terraform_owned_areas) lists V(audit.cloudwatch), the module refuses a task that would create, change or
    delete a CloudWatch destination, or change a destination to or from that type. When it lists V(audit), the module
    refuses any task that would write a destination. O(take_over_from_terraform=true) lifts both.
  - These refusals come before any write, in check mode too. A task that would change nothing, and O(state=absent) for a
    destination that does not exist, are not refused.
  - An option of another type than the destination's fails before any write. So does a name that more than one
    destination has, naming their ids, and an existing destination of a type the module does not know, unless O(type)
    replaces it with a type the module knows.
options:
  name:
    description:
      - The destination's name, which the module finds it by.
      - V(syslog), V(cloudwatch) and V(local) are reserved for a destination of the matching type, and a V(LOCAL)
        destination must be named V(local). A name outside these rules fails before any write.
    type: str
    required: true
  type:
    description:
      - Where the destination sends audit logs. Needed to create a destination; unset keeps the destination's.
      - V(CLOUDWATCH) works only on a cluster in AWS; elsewhere the cluster refuses it, and the module fails with its
        reason.
    type: str
    choices: [SYSLOG, CLOUDWATCH, LOCAL]
  enabled:
    description: Whether the cluster sends audit logs to the destination. A new destination is enabled unless this is V(false).
    type: bool
  server_address:
    description: The syslog server's host name or IP address. For V(SYSLOG) only.
    type: str
  server_port:
    description:
      - The syslog server's port. For V(SYSLOG) only.
      - The cluster refuses to enable a syslog destination without one.
    type: int
  log_format:
    description: The format of each syslog message. For V(SYSLOG) only; a new destination uses V(csv).
    type: str
    choices: [csv, json]
  log_group_name:
    description: The CloudWatch log group. For V(CLOUDWATCH) only.
    type: str
  region:
    description: The AWS region of the log group. For V(CLOUDWATCH) only.
    type: str
  state:
    description: Whether the destination exists.
    type: str
    choices: [present, absent]
    default: present
  terraform_owned_areas:
    description:
      - The areas Terraform owns, as the inventory's C(qumulo_terraform_owned_areas) lists them. Only V(audit) and
        V(audit.cloudwatch) concern this module.
    type: list
    elements: str
    default: []
  take_over_from_terraform:
    description: Manage the destination although O(terraform_owned_areas) lists V(audit) or V(audit.cloudwatch).
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
  idempotent:
    support: full
notes:
  - "API: GET /v2/audit/destinations/ to find the destination by name; POST /v2/audit/destinations/ to create it; GET,
    PUT and DELETE /v2/audit/destinations/{id}, with If-Match."
  - A change to an existing destination is written as its whole configuration with PUT, never PATCH (QUIRK-062).
  - Changing O(type) in place is not tested on a running cluster (G-MON-AUDIT-RETYPE).
  - The module does not wait for a destination to connect; each node's connection status is at
    C(GET /v2/audit/destinations/{id}/status).
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Send audit logs to the SIEM as JSON
  qumulo.cluster.audit_destination:
    name: siem
    type: SYSLOG
    server_address: siem.example.com
    server_port: 514
    log_format: json

- name: Move the SIEM destination to another port
  qumulo.cluster.audit_destination:
    name: siem
    server_port: 6514

- name: Keep the cluster's local audit log
  qumulo.cluster.audit_destination:
    name: local
    type: LOCAL

# CLOUDWATCH works only on a cluster in AWS.
- name: Send audit logs to a CloudWatch log group
  qumulo.cluster.audit_destination:
    name: audit-cloudwatch
    type: CLOUDWATCH
    log_group_name: /qumulo/audit
    region: us-west-2

- name: Preview pausing the SIEM destination
  qumulo.cluster.audit_destination:
    name: siem
    enabled: false
  check_mode: true
  diff: true
  register: audit_preview

- name: Show the destination the change would leave
  ansible.builtin.debug:
    var: audit_preview.audit_destination

- name: Pause sending audit logs to the SIEM
  qumulo.cluster.audit_destination:
    name: siem
    enabled: false

- name: Stop sending audit logs to the old SIEM
  qumulo.cluster.audit_destination:
    name: old-siem
    state: absent

# With audit.cloudwatch listed but not audit, the module refuses to write a CloudWatch destination, but still writes a
# syslog one.
- name: Send audit logs to the SIEM while Terraform owns the CloudWatch destination
  qumulo.cluster.audit_destination:
    name: siem
    type: SYSLOG
    server_address: siem.example.com
    server_port: 514
    terraform_owned_areas: [audit.cloudwatch]

- name: Manage a CloudWatch destination that Terraform owns
  qumulo.cluster.audit_destination:
    name: audit-cloudwatch
    type: CLOUDWATCH
    log_group_name: /qumulo/audit
    region: us-west-2
    terraform_owned_areas: "{{ qumulo_terraform_owned_areas }}"
    take_over_from_terraform: true
"""

RETURN = r"""
audit_destination:
  description:
    - The destination after the task, or in check mode the destination the task would leave, with the options of its
      type; empty when it does not exist.
  returned: always
  type: dict
  contains:
    name:
      description: The destination's name.
      type: str
    type:
      description: Where the destination sends audit logs, V(SYSLOG), V(CLOUDWATCH) or V(LOCAL).
      type: str
    enabled:
      description: Whether the cluster sends audit logs to the destination.
      type: bool
    server_address:
      description: The syslog server's host name or IP address; present for V(SYSLOG) when the cluster holds one.
      type: str
    server_port:
      description: The syslog server's port; present for V(SYSLOG) when the cluster holds one.
      type: int
    log_format:
      description: The format of each syslog message; present for V(SYSLOG) when the cluster holds one.
      type: str
    log_group_name:
      description: The CloudWatch log group; present for V(CLOUDWATCH) when the cluster holds one.
      type: str
    region:
      description: The AWS region of the log group; present for V(CLOUDWATCH) when the cluster holds one.
      type: str
  sample:
    name: siem
    type: SYSLOG
    enabled: true
    server_address: siem.example.com
    server_port: 514
    log_format: json
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
  sample: {server_port: 6514}
observed:
  description: The values the cluster holds for the options in RV(expected).
  returned: when the cluster reads back different values from the ones written
  type: dict
  sample: {server_port: 514}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import AuditDestination

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "type": {"type": "str", "choices": ["SYSLOG", "CLOUDWATCH", "LOCAL"]},
    "enabled": {"type": "bool"},
    "server_address": {"type": "str"},
    "server_port": {"type": "int"},
    "log_format": {"type": "str", "choices": ["csv", "json"]},
    "log_group_name": {"type": "str"},
    "region": {"type": "str"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "terraform_owned_areas": {"type": "list", "elements": "str", "default": []},
    "take_over_from_terraform": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), AuditDestination)


if __name__ == "__main__":
    main()
