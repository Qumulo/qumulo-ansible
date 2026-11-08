#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: host_vars_text
short_description: Write variables read from a Qumulo cluster as a host_vars file Ansible never templates
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Returns the text of a C(host_vars) file holding O(variables), sorted, each string value tagged C(!unsafe).
    Mapping keys, mappings and sequences are not tagged.
  - Only the strings are tagged, so a template added to the file by hand, such as a reference to a vaulted secret, is
    still evaluated.
  - A string that would read as another type without its tag, such as C(0777) or C(true),
    is written quoted without the tag. The empty string is written without the tag too.
  - The file starts with O(header) as comments and ends with comments naming each secret to supply, from O(supply),
    and what was left out, from O(left_out), in the shape M(qumulo.cluster.capture_info) and
    M(qumulo.cluster.clone_host_vars) return them. Every comment line is wrapped at 112 characters of text and starts
    with C(#).
  - C(playbooks/capture.yml) writes its files with it, and the C(qumulo.cluster.clone_edge) playbook its file.
  - Runs on the controller and connects to nothing. It sends no request, takes no connection options, and is not in the
    C(qumulo.cluster.qumulo) action group, so C(module_defaults) for that group do not reach it.
options:
  variables:
    description:
      - The variables to write. Mapping keys are written as they are.
      - String values are tagged C(!unsafe), except a string that would read as another type and the empty string, which
        are written without the tag.
    type: dict
    required: true
  header:
    description: Comment text to start the file with, wrapped; a newline starts a new line.
    type: str
    default: ""
  supply:
    description:
      - Each secret to supply, written after the variables as comments, sorted by C(why) and then by C(variable).
      - Lines with the same C(why) are grouped under it.
    type: list
    elements: dict
    default: []
    suboptions:
      variable:
        description: The variable that holds the secret, as M(qumulo.cluster.capture_info) names it.
        type: str
        required: true
      why:
        description: Why it must be supplied.
        type: str
        required: true
  left_out:
    description: What was left out, written as comments after the variables in the order given.
    type: list
    elements: dict
    default: []
    suboptions:
      what:
        description: What was left out.
        type: str
        required: true
      why:
        description: Why.
        type: str
        required: true
requirements:
  - PyYAML, which ansible-core needs on the controller.
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - "API: none."
"""

EXAMPLES = r"""
# The module runs on the controller and takes no connection options.
- name: Write a cluster's captured configuration as a host_vars file
  block:
    - name: Read the cluster's configuration
      qumulo.cluster.capture_info:
      register: captured

    - name: Render it as a host_vars file whose captured strings Ansible never templates
      qumulo.cluster.host_vars_text:
        variables: "{{ captured.capture.host_vars }}"
        header: Captured from {{ inventory_hostname }}.
        supply: "{{ captured.capture.supply }}"
        left_out: "{{ captured.capture.left_out }}"
      register: rendered
      delegate_to: localhost

    - name: Write the host_vars file, readable by its owner only
      ansible.builtin.copy:
        dest: "captures/{{ inventory_hostname }}.yml"
        content: "{{ rendered.text }}"
        mode: "0600"
      delegate_to: localhost

# source is the capture_info result of the cluster to copy, read through that cluster's connection options as in
# qumulo.cluster.clone_host_vars's examples.
- name: Render a clone's derived variables as a host_vars file
  block:
    - name: Derive the new cluster's host_vars
      qumulo.cluster.clone_host_vars:
        capture: "{{ source.capture }}"
        cluster_uuid: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
        cluster_name: edge02
      register: clone

    - name: Render them as a host_vars file
      qumulo.cluster.host_vars_text:
        variables: "{{ clone.host_vars }}"
        header: Cloned from edge01.
        supply: "{{ clone.supply }}"
        left_out: "{{ clone.left_out }}"
      register: rendered
      delegate_to: localhost

- name: Render variables with a comment header and nothing to supply
  qumulo.cluster.host_vars_text:
    variables:
      qumulo_cluster_name: edge01
      qumulo_dns: {servers: [10.0.0.2], search_domains: [corp.example.com]}
    header: |-
      Hand-written settings for edge01.
      Review before use.
  register: rendered
  delegate_to: localhost

- name: Render variables with no header or trailing comments
  qumulo.cluster.host_vars_text:
    variables:
      qumulo_cluster_name: edge01
      qumulo_time: {ntp_servers: [ntp.corp.example.com]}
  register: rendered
  delegate_to: localhost

- name: Show the rendered file
  ansible.builtin.debug:
    msg: "{{ rendered.text }}"
"""

RETURN = r"""
text:
  description: The file's text.
  returned: success
  type: str
  sample: "# Captured from edge01.\nqumulo_cluster_name: !unsafe edge01\n"
"""

from ansible.module_utils.basic import AnsibleModule, missing_required_lib
from ansible_collections.qumulo.cluster.plugins.module_utils.host_vars_text import HAS_YAML, host_vars_text

ARGUMENT_SPEC = {
    "variables": {"type": "dict", "required": True},
    "header": {"type": "str", "default": ""},
    "supply": {
        "type": "list",
        "elements": "dict",
        "default": [],
        "options": {"variable": {"type": "str", "required": True}, "why": {"type": "str", "required": True}},
    },
    "left_out": {
        "type": "list",
        "elements": "dict",
        "default": [],
        "options": {"what": {"type": "str", "required": True}, "why": {"type": "str", "required": True}},
    },
}


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    if not HAS_YAML:
        module.fail_json(msg=missing_required_lib("PyYAML"))
    params = module.params
    module.exit_json(changed=False, text=host_vars_text(params["variables"], params["header"], params["supply"], params["left_out"]))


if __name__ == "__main__":
    main()
