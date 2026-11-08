#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_pair_plan
short_description: Join the source's and the target's check passes of a replication relationship into one decision
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Joins the check-mode result of M(qumulo.cluster.replication_relationship) on the source and that of
    M(qumulo.cluster.replication_target) on the target into one decision. The C(qumulo.cluster.replication_pair) role
    runs both checks before it writes on either cluster, passes both results here, and acts on RV(status).
  - A pure computation. It sends no request, takes no connection options, and is not in the C(qumulo.cluster.qumulo)
    action group, so C(module_defaults) for that group do not reach it.
  - RV(status) is V(go) when nothing stops the relationship, V(held) or V(refused) otherwise. Every unmet condition of
    both checks is reported at once in RV(unmet), the source's and the target's in one reporting order.
  - V(held) when, while O(previous_source_cluster_uuids) is set, row O2 is unmet and not lifted with a holder from one
    of those UUIDs, or row O4 is unmet and not lifted. RV(message) then names the copy back into the source directory
    and the release that lets the relationship go ahead.
  - With O(source_license_rule), a held relationship whose holder allows a restore has RV(message) name the restore
    instead of the copy, with the release only as the way to keep the replica as plain data, and RV(restore_form) is
    true. The holder allows it when it is from a previous UUID of the source, ESTABLISHED, read-only and with a recovery
    point, row O2 is not lifted, the source directory holds no file, and O(source_replication_address) is an IP literal.
    The full check is the restore's own step 1.
  - V(refused) for any other unmet condition that nothing lifts, a check that failed for another reason, a release
    entry that lifts nothing in this run, and O(allow_non_empty_target) for a target directory the target host does not
    declare.
  - A source row O3 (an ENDED record of the key with no live one) appears on the target's check as row O5. That O5 entry
    is left out of RV(unmet), and RV(target_release) gives the target's passes each release entry that names O3 with O5
    added.
  - RV(releases) holds one entry for each record id that binds a row no release lifts, with every row both checks
    reported on that record.
  - With O(removed), the result of M(qumulo.cluster.replication_target) with C(state=absent), which has no check pass,
    only the release entries that deleted no record count, and each refuses.
options:
  source:
    description:
      - The result of M(qumulo.cluster.replication_relationship) in check mode, a refusal included, registered with
        C(failed_when) false or C(ignore_errors); a result with C(msg) counts as a failed check.
      - Omit it, and O(target), to have only RV(target_release), RV(target_declared) and RV(ancestors) returned.
    type: dict
  target:
    description: The result of M(qumulo.cluster.replication_target) in check mode, a refusal included.
    type: dict
  removed:
    description:
      - For a removal, the result of M(qumulo.cluster.replication_target) with C(state=absent). O(source) and O(target)
        are then ignored.
    type: dict
  release:
    description:
      - The C(qumulo_replication_release) entries that name this relationship, each
        C(<target host>:<target path>@<record id>:<rows>). An entry for another host or path fails.
    type: list
    elements: str
    default: []
  target_host:
    description: The target's inventory host, as the release entries name it.
    type: str
    required: true
  target_path:
    description: The target directory.
    type: str
    required: true
  previous_source_cluster_uuids:
    description: UUIDs the source cluster had before a rebuild; while any is set, a relationship can be held.
    type: list
    elements: str
    default: []
  allow_non_empty_target:
    description: The relationship entry's consent to replace a target directory that holds entries (row O1).
    type: bool
    default: false
  target_directories:
    description:
      - The target host's C(qumulo_directories), each with C(path) and an optional C(state). The consent counts only for
        a target directory declared there, and the declared ancestors of the target directory are created before it.
    type: list
    elements: dict
    default: []
  source_host:
    description: The source's inventory host, which the restore form names.
    type: str
  source_path:
    description: The source directory, which the restore form names.
    type: str
  source_cluster_uuid:
    description: The source's current cluster UUID, which the restore form names.
    type: str
  source_license_rule:
    description:
      - The license rule's answer for the source host, which chooses the restore form's sentence. V(expected) for a
        C(qumulo_edges) host that does not declare C(qumulo_capacity.license) V(none), whose second pass restores once
        the license applies; V(none) for one that does; V(outside_edges) for any other host, which
        C(playbooks/replication_restore.yml) restores.
      - Omit it for the message without a restore form, which names the copy and the release. With it,
        O(source_host), O(source_path) and O(source_cluster_uuid) are required.
    type: str
    choices: [expected, none, outside_edges]
  source_replication_address:
    description: The source host's C(qumulo_replication_address); the restore form needs an IP literal.
    type: str
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - "API: none."
  - "Registers: G-REPL-PEER, a rebuilt source's old record holds its replica until a release; G-REPL-OVERWRITE, the
    overwrite table the two checks evaluate."
"""

EXAMPLES = r"""
- name: Check the source side of the relationship without writing
  qumulo.cluster.replication_relationship:
    source_path: /projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    target_path: /replicas/edge01-projects
    target_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
    inventory_cluster_uuids: "{{ groups['qumulo_clusters'] | map('extract', hostvars, 'qumulo_cluster_uuid') | list }}"
    recreate_changed_peer: true
  check_mode: true
  register: source_check
  # A refusal is a result to join, not the end of the play; it keeps its msg and unmet.
  failed_when: false

- name: Check the target side of the relationship without writing
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    source_creates: "{{ source_check.relationship.id | default(none) is none }}"
    relationship_id: "{{ source_check.relationship.id | default(omit) }}"
    directory_pending: true
    source_empty: "{{ source_check.source_empty | default(omit) }}"
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ dr01_connection }}"
  check_mode: true
  register: target_check
  failed_when: false

- name: Join both checks into one decision
  qumulo.cluster.replication_pair_plan:
    source: "{{ source_check }}"
    target: "{{ target_check }}"
    release: "{{ qumulo_replication_release | default([]) | select('match', 'dr01:/replicas/edge01-projects/?@') | list }}"
    target_host: dr01
    target_path: /replicas/edge01-projects
    previous_source_cluster_uuids: "{{ hostvars['edge01'].qumulo_previous_cluster_uuids | default([]) }}"
    target_directories: "{{ hostvars['dr01'].qumulo_directories | default([]) }}"
  register: plan

- name: Stop before any write unless both sides may go ahead
  ansible.builtin.assert:
    that: plan.status == 'go'
    fail_msg: "{{ plan.message }}"
    quiet: true

- name: Print the restore, not the copy, when a rebuilt edge's old record holds its replica
  # source_check and target_check are the registers of the two checks above. The edge is in qumulo_edges, so its
  # license rule is "expected".
  qumulo.cluster.replication_pair_plan:
    source: "{{ source_check }}"
    target: "{{ target_check }}"
    release: "{{ qumulo_replication_release | default([]) | select('match', 'dr01:/replicas/edge01-projects/?@') | list }}"
    target_host: dr01
    target_path: /replicas/edge01-projects
    previous_source_cluster_uuids: "{{ hostvars['edge01'].qumulo_previous_cluster_uuids }}"
    target_directories: "{{ hostvars['dr01'].qumulo_directories | default([]) }}"
    source_host: edge01
    source_path: /projects
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_license_rule: expected
    source_replication_address: "{{ hostvars['edge01'].qumulo_replication_address }}"
  register: restore_plan

- name: Check the target side with the consent to replace a target directory that holds entries
  # The consent goes to both sides: allow_non_empty_directory lifts the target check's row O1, and the plan's
  # allow_non_empty_target adds a refusal when the target host does not declare the directory.
  qumulo.cluster.replication_target:
    source_cluster_uuid: "{{ hostvars['edge01'].qumulo_cluster_uuid }}"
    source_path: /projects
    target_path: /replicas/edge01-projects
    source_creates: "{{ source_check.relationship.id | default(none) is none }}"
    relationship_id: "{{ source_check.relationship.id | default(omit) }}"
    directory_pending: true
    source_empty: "{{ source_check.source_empty | default(omit) }}"
    allow_non_empty_directory: true
  module_defaults:
    group/qumulo.cluster.qumulo: "{{ dr01_connection }}"
  check_mode: true
  register: consent_target_check
  failed_when: false

- name: Allow the replica to replace a target directory that holds entries, only if dr01 declares it
  # allow_non_empty_target counts only for a target directory in the target host's qumulo_directories.
  qumulo.cluster.replication_pair_plan:
    source: "{{ source_check }}"
    target: "{{ consent_target_check }}"
    target_host: dr01
    target_path: /replicas/edge01-projects
    allow_non_empty_target: true
    target_directories: "{{ hostvars['dr01'].qumulo_directories | default([]) }}"
  register: consent_plan

- name: Find the target directory's declared ancestors and the target's release entries, with no checks to join
  qumulo.cluster.replication_pair_plan:
    release: "{{ qumulo_replication_release | default([]) | select('match', 'dr01:/replicas/edge01-projects/?@') | list }}"
    target_host: dr01
    target_path: /replicas/edge01-projects
    target_directories: "{{ hostvars['dr01'].qumulo_directories | default([]) }}"
  register: target_view

- name: Refuse a removal whose release entries deleted nothing
  # target_removed is the result of the target's removal, which has no check pass.
  qumulo.cluster.replication_pair_plan:
    removed: "{{ target_removed }}"
    release: "{{ qumulo_replication_release | default([]) | select('match', 'dr01:/replicas/edge01-projects/?@') | list }}"
    target_host: dr01
    target_path: /replicas/edge01-projects
  register: removal_plan
  failed_when: removal_plan.status != 'go'
"""

RETURN = r"""
status:
  description: V(go), V(held) or V(refused).
  returned: when O(source) or O(removed) is given
  type: str
  sample: held
message:
  description: Why the relationship is held or refused, with what would lift it; empty for V(go).
  returned: when O(source) or O(removed) is given
  type: str
unmet:
  description:
    - Every unmet condition of both checks, in the shape the two modules return it, with C(side), V(source) or
      V(target). Other conditions come first, then the overwrite table's rows in the reporting order.
  returned: when O(source) is given and O(removed) is not
  type: list
  elements: dict
blocking:
  description: The entries of RV(unmet) that stop the relationship, neither lifted nor pending.
  returned: when O(source) is given and O(removed) is not
  type: list
  elements: dict
releases:
  description:
    - The release entries to print, C(<target host>:<target path>@<record id>:<rows>), one per record id that binds a
      row no release lifts, with every row both checks reported on that record.
  returned: when O(source) is given and O(removed) is not
  type: list
  elements: str
  sample: ["dr01:/replicas/edge01-projects@9465828e-7dcf-466e-a550-78ed0bd6b79b:O2,O4"]
lifting_nothing:
  description: The O(release) entries that lift nothing in this run; each refuses the relationship.
  returned: when O(source) or O(removed) is given
  type: list
  elements: str
failures:
  description: The failures of a check that no unmet entry explains, such as a cluster that did not answer.
  returned: when O(source) is given and O(removed) is not
  type: list
  elements: str
restore_form:
  description:
    - Whether RV(message) names the restore in place of the copy, printing the release only as the way to keep the
      replica as plain data instead. The rebuild report places such a message without the baseline run that would
      release it.
  returned: when O(source) is given and O(removed) is not
  type: bool
holders:
  description: The target's holders, as M(qumulo.cluster.replication_target) returns them in its RV(holders).
  returned: when O(source) is given and O(removed) is not
  type: list
  elements: dict
pairing:
  description:
    - Whether the source creates the relationship or the target authorizes it, so that the capacity gate applies. The
      target authorizes when its check evaluated the overwrite table, which it does only before an authorize, and so
      returned RV(qumulo.cluster.replication_target#module:replaces).
  returned: when O(source) is given and O(removed) is not
  type: bool
target_release:
  description: O(release) as the target's passes take it, each entry that names O3 with O5 added.
  returned: always
  type: list
  elements: str
target_declared:
  description: Whether O(target_directories) declares the target directory.
  returned: always
  type: bool
ancestors:
  description:
    - The entries of O(target_directories) above the target directory, outermost first, each with its path without a
      trailing slash.
  returned: always
  type: list
  elements: dict
"""

from dataclasses import asdict

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.files import canonical_path
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import RestoreForm, declared_directories, is_ip_literal, pair_plan, target_releases

ARGUMENT_SPEC = {
    "source": {"type": "dict"},
    "target": {"type": "dict"},
    "removed": {"type": "dict"},
    "release": {"type": "list", "elements": "str", "default": []},
    "target_host": {"type": "str", "required": True},
    "target_path": {"type": "str", "required": True},
    "previous_source_cluster_uuids": {"type": "list", "elements": "str", "default": []},
    "allow_non_empty_target": {"type": "bool", "default": False},
    "target_directories": {"type": "list", "elements": "dict", "default": []},
    "source_host": {"type": "str"},
    "source_path": {"type": "str"},
    "source_cluster_uuid": {"type": "str"},
    "source_license_rule": {"type": "str", "choices": ["expected", "none", "outside_edges"]},
    "source_replication_address": {"type": "str"},
}


def main():
    module = AnsibleModule(
        argument_spec=ARGUMENT_SPEC,
        required_by={"source_license_rule": ("source_host", "source_path", "source_cluster_uuid")},
        supports_check_mode=True,
    )
    params = module.params
    try:
        target_path = canonical_path(params["target_path"], "target_path")
        declared, ancestors = declared_directories(target_path, params["target_directories"])
        result = {"target_release": target_releases(params["release"]), "target_declared": declared, "ancestors": ancestors}
        restore = None
        if params["source_license_rule"] is not None:
            restore = RestoreForm(
                host=params["source_host"],
                source_path=canonical_path(params["source_path"], "source_path"),
                cluster_uuid=params["source_cluster_uuid"],
                license_rule=params["source_license_rule"],
                ip_literal=is_ip_literal(params["source_replication_address"]),
            )
        if params["source"] is not None or params["removed"] is not None:
            plan = pair_plan(
                source=params["source"],
                target=params["target"],
                release=params["release"],
                target_host=params["target_host"],
                target_path=target_path,
                previous_source_cluster_uuids=params["previous_source_cluster_uuids"],
                allow_non_empty_target=params["allow_non_empty_target"],
                target_directories=params["target_directories"],
                removed=params["removed"],
                restore=restore,
            )
            found = asdict(plan)
            if params["removed"] is not None:
                found = {key: found[key] for key in ("status", "message", "lifting_nothing")}
            result.update(found)
    except QumuloError as e:
        module.fail_json(msg=str(e))
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
