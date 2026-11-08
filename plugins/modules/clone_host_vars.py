#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: clone_host_vars
short_description: Derive a fresh Qumulo cluster's host_vars from another cluster's capture
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Takes the capture of a source cluster, as M(qumulo.cluster.capture_info) returns it, and returns the C(host_vars) of
    a fresh cluster that is to take the source's configuration. The C(qumulo.cluster.clone_edge) playbook writes them
    and runs the baseline from them.
  - Runs on the controller and connects to nothing. It sends no request, takes no connection options, and is not in the
    C(qumulo.cluster.qumulo) action group, so C(module_defaults) for that group do not reach it.
  - Configuration only. No data is copied.
  - Each variable a capture can hold has its own rule, and a variable without one fails the module.
  - "Derived: RV(host_vars) holds O(cluster_uuid) as C(qumulo_cluster_uuid) and O(cluster_name) as C(qumulo_cluster_name).
    The source's UUID is never copied."
  - "Copied verbatim: every other variable a capture holds, with local users' passwords, the join credentials, a join key
    and a license document stripped wherever they appear."
  - "Copied with changes: C(qumulo_ad) without its join credentials; C(qumulo_authoritative_dns) without C(enabled) and
    C(fqdn), with a note in RV(left_out) when the source had a name or answered; and C(qumulo_tenants), each tenant with
    its name, C(previous_name), services and SMB and NFS settings, without its networks, which hold the source's
    addresses, with a note in RV(left_out) for each tenant that had any, and with its C(smb_shares) and C(nfs_exports)
    filtered as the flat lists are. A tenant declared V(absent), as a capture of a cluster without tenant 1 declares
    V(Default), is left out with a note, since the target's V(Default) holds the target's own networks. A tenant with
    C(previous_name) V(Default) keeps it, as a capture of a host-managed cluster without tenant 1 writes it for the
    tenant that holds the network, so the target's V(Default) is renamed to that tenant."
  - "Asked for in RV(supply): the AD join credentials, when C(qumulo_ad) is copied, for a fresh join under the new name, the
    clone's computer account in the domain; a Nexus join key of the clone's own (G-NEXUS-KEY), whenever the source's Nexus
    connection is on; a license document issued for O(cluster_uuid) (G-LICENSE), when the source holds a license or its
    capture asked for one; and the clone's own certificate and key (G-SSL-READBACK), when the capture read the V(ssl) area.
    C(qumulo_ssl) is never copied."
  - "Left out, each with a note in RV(left_out): C(qumulo_portal), each replication relationship and C(qumulo_network);
    the shares, exports, directories, ACLs and S3 buckets in the source's spoke roots, with those buckets' policies; the
    directories the source receives replication into, with the ACLs, quotas, snapshot policies and S3 buckets on them;
    an S3 base path in either (the target keeps its own); C(qumulo_portal_hosts), C(qumulo_replication_address) and
    C(qumulo_replication_port), the addresses the source's peers reach it at; C(qumulo_previous_cluster_uuids), the UUIDs
    the source took over in a rebuild; C(qumulo_managed_by) and C(qumulo_terraform_owned_areas), which describe the source
    alone; the CloudWatch audit destinations; C(qumulo_saml); and each key of C(qumulo_s3_access_keys) (declare the clone's
    own keys, G-S3-KEY). A share or export under a tenant is noted with its tenant's name."
  - When the source requires AD users to sign in through the IdP, the note for C(qumulo_saml) says they sign in to the
    clone with a password until its own C(qumulo_saml) is declared.
  - "Fails when the capture holds no C(qumulo_cluster_uuid), when it holds a variable with no rule, when O(cluster_uuid) is the
    source's UUID, when O(cluster_name) is the source's name or one of O(names_in_use), when O(supplied) names a secret
    that no line of RV(supply) holds, and, unless O(allow_configured_target), when the target holds what formation does not
    create. Names compare without regard to case."
options:
  capture:
    description: The source's capture, the RV(qumulo.cluster.capture_info#module:capture) that module returns.
    type: dict
    required: true
  cluster_uuid:
    description: The UUID of the fresh cluster, the clone, as it reports it.
    type: str
    required: true
  cluster_name:
    description: The clone's name. It must differ from the source's and from each of O(names_in_use).
    type: str
    required: true
  inventory_defines:
    description:
      - The variables the clone's inventory already defines for it outside the file RV(host_vars) is written to, each
        mapped to the list of its keys whose values are not empty when it is a mapping, else V(null).
      - Each is left out of RV(host_vars) with a note and its supply lines, because a C(host_vars) file outranks the
        inventory.
      - One whose inventory mapping holds only the variable's secrets, such as a C(qumulo_capacity) holding only
        C(license_document), is kept, and a note says the file's mapping outranks the inventory's from now on.
        RV(first_pass_needs) counts the secrets that inventory mapping gives as supplied.
    type: dict
    default: {}
  names_in_use:
    description: The names of the inventory's other clusters, none of which the clone may take.
    type: list
    elements: str
    default: []
  target:
    description:
      - The clone's own capture of the V(local_users), V(ad), V(smb), V(nfs), V(tenants) and V(s3) areas, as
        M(qumulo.cluster.capture_info) returns it.
      - It tells whether the target is fresh, and which secrets the first baseline needs.
      - Without it, the clone is taken to be fresh, with only the local users every cluster has, C(admin) and C(guest), and
        no domain.
    type: dict
  allow_configured_target:
    description:
      - Clone onto a target that holds what formation does not create, as on a second run of the same clone.
      - Formation creates the local users C(admin) and C(guest), one tenant, and the factory C(Files) share and the
        factory C(/) export in it. A target with a local user other than these, a domain join, a second tenant, another
        SMB share or NFS export, or an S3 bucket or access key fails unless this is set.
      - The tenant formation creates is the target's only one, or the one named V(Default) or renamed from it. A target
        whose capture notes a missing tenant 1 in RV(qumulo.cluster.capture_info#module:capture.left_out) has none, so
        each tenant it holds counts.
    type: bool
    default: false
  supplied:
    description:
      - The names of the secrets the caller gives for the first baseline, each as a line of RV(supply) names it, such as
        V(qumulo_local_users[svc-backup].password). Only the names; the values never reach this module.
      - A name no line of RV(supply) holds fails.
    type: list
    elements: str
    default: []
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - "API: none."
  - The V(qumulo_tenants) rule copies tenants that Qumulo's multitenancy REST API, marked as preview, defines; a later
    Qumulo Core release may change them.
  - "Registers: G-LICENSE, G-NEXUS-KEY, G-S3-KEY, G-SSL-READBACK."
"""

EXAMPLES = r"""
# The module runs on the controller and takes no connection options. Only the tasks that read a cluster do. Run these
# against the new cluster's inventory host, with module_defaults for group/qumulo.cluster.qumulo set from its variables.
# clone_source is the inventory host of the cluster to copy.
- name: Derive the host_vars of a new cluster from an existing cluster
  vars:
    clone_source: edge01
  block:
    - name: Read the new cluster's UUID
      qumulo.cluster.cluster_info:
        gather_subset: [node]
      register: target_info

    # The source is another cluster, so its read takes every connection option from the source's variables.
    - name: Read the source cluster, changing nothing on it
      module_defaults:
        group/qumulo.cluster.qumulo:
          api_host: "{{ hostvars[clone_source].qumulo_api_host }}"
          api_port: "{{ hostvars[clone_source].qumulo_api_port | default(8000) }}"
          api_username: "{{ hostvars[clone_source].qumulo_api_username | default('admin') }}"
          api_password: "{{ hostvars[clone_source].qumulo_api_password | default('') }}"
          api_token: "{{ hostvars[clone_source].qumulo_api_token | default('') }}"
          validate_certs: "{{ hostvars[clone_source].qumulo_validate_certs | default(true) }}"
          ca_path: "{{ hostvars[clone_source].qumulo_ca_path | default(omit) }}"
          expected_cluster_uuid: "{{ hostvars[clone_source].qumulo_cluster_uuid }}"
      block:
        - name: Read the source's configuration
          qumulo.cluster.capture_info:
          register: source

    - name: Derive the new cluster's host_vars
      qumulo.cluster.clone_host_vars:
        capture: "{{ source.capture }}"
        cluster_uuid: "{{ target_info.cluster.cluster_id }}"
        cluster_name: edge02
      register: clone

    - name: Show the secrets the new cluster's first baseline cannot do without
      ansible.builtin.debug:
        var: clone.first_pass_needs

    # No data is copied. To copy data, declare a replication relationship from the source to the clone.
    - name: Stop unless the first baseline needs no secret that is not yet supplied
      ansible.builtin.assert:
        that: clone.first_pass_needs | length == 0
        fail_msg: "Supply {{ clone.first_pass_needs | join(', ') }} before the first baseline."

    - name: Render the derived variables as a host_vars file whose captured strings Ansible never templates
      qumulo.cluster.host_vars_text:
        variables: "{{ clone.host_vars }}"
        header: Cloned from edge01.
        supply: "{{ clone.supply }}"
        left_out: "{{ clone.left_out }}"
      register: rendered
      delegate_to: localhost

    - name: Write the derived variables as the new cluster's host_vars
      ansible.builtin.copy:
        dest: "host_vars/edge02.yml"
        content: "{{ rendered.text }}"
        mode: "0644"
      delegate_to: localhost

# source is the capture_info result of the source cluster, read as in the recipe above.
# names_in_use leaves out the host the play runs on, whose own inventory may already give the name the clone is to take.
- name: Derive host_vars that refuse a name another inventory cluster has
  qumulo.cluster.clone_host_vars:
    capture: "{{ source.capture }}"
    cluster_uuid: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
    cluster_name: edge02
    names_in_use: >-
      {{ groups['qumulo_clusters'] | reject('equalto', inventory_hostname) | map('extract', hostvars)
         | selectattr('qumulo_cluster_name', 'defined') | map(attribute='qumulo_cluster_name') | list }}
  register: clone

# source and target_info are the first recipe's reads of the source cluster and of the new cluster.
- name: Derive host_vars after checking that the new cluster is fresh
  block:
    - name: Read what the new cluster holds
      qumulo.cluster.capture_info:
        gather_subset: [local_users, ad, smb, nfs, tenants, s3]
        expected_cluster_uuid: "{{ target_info.cluster.cluster_id }}"
      register: target_read

    - name: Derive the new cluster's host_vars
      qumulo.cluster.clone_host_vars:
        capture: "{{ source.capture }}"
        cluster_uuid: "{{ target_info.cluster.cluster_id }}"
        cluster_name: edge02
        target: "{{ target_read.capture }}"
      register: clone

# source is the capture_info result of the source cluster.
- name: Derive host_vars, naming the secrets the first baseline will be given
  qumulo.cluster.clone_host_vars:
    capture: "{{ source.capture }}"
    cluster_uuid: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
    cluster_name: edge02
    supplied:
      - qumulo_local_users[svc-backup].password
      - qumulo_ad.join_user
      - qumulo_ad.join_password
  register: clone

# source is the capture_info result of the source cluster.
- name: Derive host_vars, leaving out the variables the inventory already defines for the new cluster
  qumulo.cluster.clone_host_vars:
    capture: "{{ source.capture }}"
    cluster_uuid: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
    cluster_name: edge02
    inventory_defines:
      qumulo_cluster_name: null
      qumulo_capacity: [license_document]
  register: clone

# source is the capture_info result of the source cluster.
- name: Derive host_vars again for a cluster that an earlier clone configured
  qumulo.cluster.clone_host_vars:
    capture: "{{ source.capture }}"
    cluster_uuid: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
    cluster_name: edge02
    allow_configured_target: true
  register: clone
"""

RETURN = r"""
host_vars:
  description:
    - The clone's C(qumulo_*) variables, sorted by name, in the shape the cluster_baseline role takes them.
    - Holds C(qumulo_cluster_uuid) and C(qumulo_cluster_name) unless O(inventory_defines) names them.
  returned: success
  type: dict
  sample:
    qumulo_cluster_name: edge02
    qumulo_cluster_uuid: 1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11
    qumulo_dns: {search_domains: [corp.example.com], servers: [10.0.0.2]}
supply:
  description: Each secret the inventory must supply to manage an area of the clone, with C(variable) and C(why), sorted by C(variable).
  returned: success
  type: list
  elements: dict
  sample: [{variable: qumulo_monitoring.nexus_join_key, why: "a join key registers one cluster only, ..."}]
left_out:
  description: What the source holds and the clone does not take, each with C(what) and C(why), sorted by C(what).
  returned: success
  type: list
  elements: dict
  sample: [{what: the network document, why: "it holds the source's own addresses, ..."}]
first_pass_needs:
  description:
    - The lines of RV(supply) the first baseline cannot do without and O(supplied) does not name, sorted.
    - A local user the clone lacks needs its password, and joining the domain needs the join credentials.
    - The other secrets guard what the first baseline skips or hands off.
  returned: success
  type: list
  elements: str
  sample: ["qumulo_ad.join_password", "qumulo_ad.join_user"]
source_uuid:
  description: The source's UUID, lower case, from O(capture).
  returned: success
  type: str
  sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.clone import Clone

ARGUMENT_SPEC = {
    "capture": {"type": "dict", "required": True},
    "cluster_uuid": {"type": "str", "required": True},
    "cluster_name": {"type": "str", "required": True},
    "inventory_defines": {"type": "dict", "default": {}},
    "names_in_use": {"type": "list", "elements": "str", "default": []},
    "target": {"type": "dict"},
    "allow_configured_target": {"type": "bool", "default": False},
    "supplied": {"type": "list", "elements": "str", "default": []},
}


def main():
    module = AnsibleModule(argument_spec=ARGUMENT_SPEC, supports_check_mode=True)
    try:
        result = Clone(module.params).run()
    except QumuloError as e:
        module.fail_json(msg=str(e))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
