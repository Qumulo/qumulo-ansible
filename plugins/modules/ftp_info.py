#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: ftp_info
short_description: Read a Qumulo cluster's FTP settings and each node's FTP server status
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the FTP configuration of one cluster and whether each node's FTP server runs, and changes nothing.
  - The settings come back in the option shape of M(qumulo.cluster.ftp_settings). An anonymous user whose local user was
    deleted reads V(local:), as that module returns it.
  - The cluster has one FTP server, which answers on every network, so the module has no tenant option.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
    type: list
    elements: str
    choices: [all, settings, status]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(settings) GET /v0/ftp/settings; V(status) GET /v0/ftp/status."
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
- name: Read the cluster's FTP settings and status
  qumulo.cluster.ftp_info:
  register: ftp

- name: Check that the FTP server runs on every node
  # Run it after turning the server on with qumulo.cluster.ftp_settings.
  qumulo.cluster.ftp_info:
    gather_subset: [status]
  register: ftp_status
  failed_when: ftp_status.ftp.status | rejectattr('status', 'equalto', 'running') | list | length > 0

- name: Read who anonymous logins act as
  qumulo.cluster.ftp_info:
    gather_subset: [settings]
  register: ftp_settings_read

- name: Print the anonymous user
  ansible.builtin.debug:
    msg: "{{ ftp_settings_read.ftp.settings.anonymous_user or 'none' }}"
"""

RETURN = r"""
ftp:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    settings:
      description: The FTP settings, in the option shape of M(qumulo.cluster.ftp_settings).
      type: dict
      contains:
        enabled:
          description: Whether the cluster allows FTP connections.
          type: bool
        check_remote_host:
          description: Whether all the connections of a session must come from the same host.
          type: bool
        log_operations:
          description: Whether the cluster logs FTP operations.
          type: bool
        chroot_users:
          description: Whether the cluster confines FTP users with C(chroot).
          type: bool
        allow_unencrypted_connections:
          description: Whether the cluster accepts connections that are not encrypted.
          type: bool
        expand_wildcards:
          description: Whether C(LIST) and C(NLST) expand wildcards.
          type: bool
        anonymous_user:
          description:
            - The local user anonymous logins act as, as V(local:<name>) in the cluster's spelling, V("") for none, and
              V(local:) for a user since deleted. Any other identity type, which the cluster refuses to store, reads as
              C(<id_type in lower case>:<id_value>), such as V(local_group:Users).
          type: str
        greeting:
          description: The text sent with the C(220) reply to a new connection.
          type: str
    status:
      description: Each node's FTP server, in the order the cluster lists the nodes.
      type: list
      elements: dict
      contains:
        node_id:
          description: The node's id.
          type: int
        status:
          description: The node's FTP server state as the cluster names it, such as V(running) or V(stopped).
          type: str
  sample:
    settings:
      enabled: true
      check_remote_host: true
      log_operations: true
      chroot_users: false
      allow_unencrypted_connections: true
      expand_wildcards: false
      anonymous_user: ""
      greeting: "Authorized use only.\n"
    status:
      - {node_id: 1, status: running}
unavailable_subsets:
  description: Each requested subset this cluster could not answer, with the reason.
  returned: when a subset was unavailable
  type: dict
  sample: {status: "GET /v0/ftp/status failed: HTTP 404 ..."}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.ftp import FTP_INFO_SUBSETS

ARGUMENT_SPEC = {"gather_subset": {"type": "list", "elements": "str", "choices": ["all", *FTP_INFO_SUBSETS], "default": ["all"]}}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "ftp", FTP_INFO_SUBSETS)


if __name__ == "__main__":
    main()
