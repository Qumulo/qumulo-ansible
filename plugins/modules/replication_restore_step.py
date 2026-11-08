#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: replication_restore_step
short_description: Choose the next step of a rebuilt source's restore from its replica, from fresh reads of both clusters
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - The restore of a rebuilt edge from the replica its old UUID left on a target runs in steps. The
    C(qumulo.cluster.replication_pair) role's restore calls this module before every step, with fresh reads, and runs
    only the step it returns.
  - A pure computation. It sends no request, takes no connection options, and is not in the C(qumulo.cluster.qumulo)
    action group, so C(module_defaults) for that group do not reach it.
  - X is the old record on the target that holds the replica, a live record at the target path from one of
    O(previous_cluster_uuids). R is the restore relationship, from the target's replica directory to the edge's declared
    source path; the reverse turns it into the declared relationship's orientation. The restore record, on the
    controller under O(artifact_dir), is written after X's delete and marked before the reconnect.
  - With O(relationship), the selection rule's answer for that relationship, decided in this order, the first that
    applies.
  - A record of an unknown format fails the module, naming the file, with C(stops_run) set, since it stops the edge's
    whole run.
  - A record whose R is ENDED, or missing on a cluster in both orientations, is removed first. An unmarked record whose
    R reconnected is marked first.
  - With a marked record, step 9 once R is ESTABLISHED on both sides, step 8's read-only wait while one side lags, and
    otherwise a refusal with its note.
  - V(done) for the declared relationship ESTABLISHED with no record. The next step while R's pair exists, or a stop
    when that step's precondition no longer holds. Step 1 or step 2 when X exists and R has no pair, or the refusals of
    step 1's preconditions. Otherwise the relationship is not applicable, with its note.
  - When an answer needs inputs only a later read gives, such as step 1's check passes, RV(decision) is V(read) and
    RV(reads) names them. Call the module again with them.
  - With O(relationships) and no O(relationship), the run's choice of the edge's relationships, RV(picked), and what the
    run refuses or reports before any step.
  - With O(waiting), one round's check of step 4's wait. With O(after_delete), step 7's restore record from the read after
    X's delete.
  - In check mode RV(sequence) holds the remaining steps the module predicts, since nothing changes between steps.
options:
  edge:
    description: The edge's inventory host name, the cluster being restored.
    type: str
    required: true
  edge_cluster_uuid:
    description: The edge's current UUID. Required with O(relationship).
    type: str
  previous_cluster_uuids:
    description: The edge's C(qumulo_previous_cluster_uuids), the UUIDs an X can come from.
    type: list
    elements: str
    default: []
  inventory_cluster_uuids:
    description:
      - Every inventory host's C(qumulo_cluster_uuid), by host name. A live record at the target path from none of them, nor
        from the edge's UUIDs, is a foreign record; the names also name the other ends of relationships in refusals.
    type: dict
    default: {}
  entry_point:
    description:
      - Which entry point runs the restore, V(second_pass) for the C(qumulo.cluster.edge_license_complete) playbook or
        V(playbook) for the C(qumulo.cluster.replication_restore) playbook. Only the second pass fails on a stranded
        pair that has a restore to do, and only the playbook refuses its whole run before any relationship.
    type: str
    required: true
    choices: [playbook, second_pass]
  inventory:
    description: The inventory as the ways on name it after C(-i).
    type: str
    default: <inventory>
  artifact_dir:
    description:
      - C(edge_rebuild_artifact_dir). The restore's files live under C(<artifact_dir>/<edge>/restore/), and an empty one
        refuses step 1 and stops step 7, since no restore record could be written.
    type: str
    default: ""
  now:
    description: The current time, ISO 8601, for the resume window, the deadlines and the times the module writes. Defaults to the clock.
    type: str
  relationship:
    description:
      - The declared relationship, one C(state=present) entry of the edge's C(qumulo_replication_relationships), with
        C(source_path), C(target) and C(target_path), and the settings step 9 applies.
    type: dict
  named:
    description: Whether C(qumulo_replication_restore) names O(relationship). A named relationship that is not applicable is refused.
    type: bool
    default: false
  target_cluster_uuid:
    description: The target host's current UUID. Required with O(relationship).
    type: str
  edge_relationships:
    description:
      - The edge's C(replication) facts from M(qumulo.cluster.replication_info), with both C(source_relationships) and
        C(target_relationships), read whole or by R's id. A record counts as missing only when it is absent from both.
      - With O(waiting), the round's read; only C(target_relationships) counts.
    type: dict
  target_relationships:
    description: The target's C(replication) facts, as O(edge_relationships) gives the edge's.
    type: dict
  target_error:
    description:
      - The error when the target did not answer. The relationship then fails while it has a restore to do, which only the
        restore record and the report claim can show, and is otherwise not applicable with a warning.
    type: str
  record_text:
    description: The text of the restore record file RV(record_path), or V(null) when there is none.
    type: str
  report_claims:
    description:
      - The edge's rebuild report claims, C(report), the newest report's file name or V(null) when the controller holds none,
        C(claimed), the keys it still claims, and C(stamped), the key hashes of the restore stamps that name that report,
        as C(replication_restore_report_claims) gives them.
    type: dict
    default: {}
  resume_window:
    description: C(replication_restore_resume_window), the seconds after its write time within which an unmarked record resumes step 8.
    type: int
    default: 86400
  replication_address:
    description: The edge's C(qumulo_replication_address), at which the target reaches it. Step 1 requires an IP literal.
    type: str
  replication_port:
    description: The edge's C(qumulo_replication_port).
    type: int
    default: 3712
  target_replication_address:
    description: The target's C(qumulo_replication_address), which the reverse and step 9 send. Step 1 requires an IP literal.
    type: str
  target_replication_port:
    description: The target's C(qumulo_replication_port).
    type: int
    default: 3712
  directories:
    description: The edge's C(qumulo_directories). Step 1 creates the source path and its missing declared ancestors.
    type: list
    elements: dict
    default: []
  source_directory:
    description:
      - What the edge holds at RV(source_directories), for step 1. C(missing), the paths among them that do not exist;
        C(first_object), the first file, link or other object at any depth under the source path, or V(null) when it holds
        only directories or is missing.
    type: dict
  record_directory_writable:
    description: Whether the restore directory under O(artifact_dir) can be created and written, for step 1.
    type: bool
  relationship_check:
    description: For step 1, M(qumulo.cluster.replication_relationship)'s check-mode result on the target for R, with RV(check_options.relationship).
    type: dict
  target_check:
    description: For step 1, M(qumulo.cluster.replication_target)'s check-mode result on the edge for R, with RV(check_options.target).
    type: dict
  in_edges:
    description: Whether the edge is in C(qumulo_edges), for the license rule.
    type: bool
    default: false
  capacity:
    description:
      - The license and the clamp of the edge's C(qumulo_capacity), never its license document, or V(null) when it declares
        none, for the license rule and the clamp half.
    type: dict
    suboptions:
      license:
        description: C(qumulo_capacity.license).
        type: str
      clamp:
        description: C(qumulo_capacity.clamp).
        type: raw
  license:
    description: M(qumulo.cluster.license)'s check-mode result on the edge, where the license rule expects a license.
    type: dict
  predicted_license_pending:
    description:
      - In check mode, the edge's license play's prediction, C(cluster_baseline_license_pending), when it made one. It
        replaces O(license), and passes the clamp half, since the license play would write the clamp first.
    type: bool
  clamp_plan:
    description: M(qumulo.cluster.capacity_clamp)'s check-mode result for the declared clamp, the capacity gate's clamp half.
    type: dict
  target_spokes:
    description: The target's spoke portals, RV(qumulo.cluster.portal_info#module:portals.spokes), for step 1.
    type: list
    elements: dict
  edge_spokes:
    description: The edge's spoke portals, for step 1.
    type: list
    elements: dict
  siblings:
    description:
      - The edge's other C(present) relationships with an equal or nested source path, each with C(source_path),
        C(target_host), C(target_path) and C(past_delete), that relationship's RV(past_delete). Step 1's refusal of a
        directory that holds files names a sibling restored first.
    type: list
    elements: dict
    default: []
  step21:
    description:
      - For a relationship with no X, no pair of R and no record, on an edge with previous UUIDs, step 21's check pass,
        C(replication_pair) in check mode with C(replication_pair_on_held=report), as C(status) and C(message).
    type: dict
  wait_started:
    description: When step 4's wait starts, the end of step 3 in this run or the end of phase 1 for a relationship resumed at step 4.
    type: str
  wait_timeout:
    description: C(replication_restore_wait_timeout), step 4's wait, in seconds.
    type: int
    default: 86400
  reverse_timeout:
    description: C(replication_restore_reverse_timeout), step 5's poll, in seconds.
    type: int
    default: 300
  reconnect_timeout:
    description: C(replication_restore_reconnect_timeout), step 8's two polls together, in seconds.
    type: int
    default: 1800
  subdirectories:
    description:
      - For the summary of step 9, each declared directory under the source path, read after step 4, with C(path) and
        C(exists). One that exists was replaced by R's first job, one that does not was deleted.
    type: list
    elements: dict
    default: []
  relationships:
    description: For the run's choice, the edge's C(qumulo_replication_relationships).
    type: list
    elements: dict
  names:
    description: For the run's choice, C(qumulo_replication_restore), or V(null) to pick every C(present) relationship.
    type: list
    elements: str
  release:
    description: For the run's choice, whether the run carries C(qumulo_replication_release), which the playbook refuses.
    type: bool
    default: false
  targets:
    description:
      - For the run's choice, the target read, C(replication_restore_targets), by target host, each with C(target_relationships)
        or C(error).
    type: dict
  restore_files:
    description:
      - For the run's choice, the names of the files under C(<artifact_dir>/<edge>/restore/). A sibling was restored into
        the edge's directory when its target answered O(targets) with no X for it, and its restore record is among these
        names or O(report_claims) lists its stamp in C(stamped). A picked relationship whose X exists, with a source path
        that sibling's equals or holds, is then not restored, with the release as its way on.
      - A stamp from an earlier rebuild keeps its name, so a stamp's name alone shows nothing.
    type: list
    elements: str
    default: []
  waiting:
    description:
      - For one round of step 4, C(relationship_id), R's id, and C(wait_started).
      - Without O(edge_relationships), the round's read failed; with it, R missing from its C(target_relationships) stops.
    type: dict
  after_delete:
    description:
      - For step 7's record, C(record), the C(write_record) action's record, and C(relationship), R's record on the target
        read after X's delete, or V(null) when that read failed.
    type: dict
extends_documentation_fragment:
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.info_module
notes:
  - "API: none."
  - "Registers: G-REPL-ONESHOT, the restore's verbs are one-shot and its leftovers named; G-REPL-PEER, a rebuilt source's old
    record holds its replica until the restore; G-REPL-SNAPSHOT, step 9 names the snapshots X left in
    RV(snapshots_left); G-REPL-IDMAP, the last step applies the declared C(map_local_ids_to_nfs_ids)."
"""

EXAMPLES = r"""
- name: Choose the next step of the restore of /projects
  # Run on the controller, with fresh reads of both clusters (edge_read and target_read are the registers of
  # qumulo.cluster.replication_info on the edge and on the target).
  qumulo.cluster.replication_restore_step:
    edge: edge01
    edge_cluster_uuid: "{{ qumulo_cluster_uuid }}"
    previous_cluster_uuids: "{{ qumulo_previous_cluster_uuids }}"
    entry_point: playbook
    artifact_dir: "{{ edge_rebuild_artifact_dir }}"
    relationship: "{{ qumulo_replication_relationships[0] }}"
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    edge_relationships: "{{ edge_read.replication }}"
    target_relationships: "{{ target_read.replication }}"
    record_text: "{{ record_file.content | default(none) }}"
    replication_address: "{{ qumulo_replication_address }}"
    target_replication_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
  register: restore_step

- name: Preview the steps the restore of /projects would run from here
  qumulo.cluster.replication_restore_step:
    edge: edge01
    edge_cluster_uuid: "{{ qumulo_cluster_uuid }}"
    previous_cluster_uuids: "{{ qumulo_previous_cluster_uuids }}"
    entry_point: playbook
    artifact_dir: "{{ edge_rebuild_artifact_dir }}"
    relationship: "{{ qumulo_replication_relationships[0] }}"
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    edge_relationships: "{{ edge_read.replication }}"
    target_relationships: "{{ target_read.replication }}"
    replication_address: "{{ qumulo_replication_address }}"
    target_replication_address: "{{ hostvars['dr01'].qumulo_replication_address }}"
  check_mode: true
  register: restore_preview

- name: Show the steps the preview predicts
  ansible.builtin.debug:
    msg: "{{ restore_preview.sequence }}"

- name: Choose the relationships the restore run picks, and what it refuses before any step
  qumulo.cluster.replication_restore_step:
    edge: edge01
    previous_cluster_uuids: "{{ qumulo_previous_cluster_uuids }}"
    inventory_cluster_uuids: "{{ dict(groups['qumulo_clusters'] | zip(groups['qumulo_clusters'] | map('extract', hostvars, 'qumulo_cluster_uuid'))) }}"
    entry_point: playbook
    relationships: "{{ qumulo_replication_relationships }}"
    names: "{{ qumulo_replication_restore | default(none) }}"
    targets: "{{ target_reads }}"
    restore_files: "{{ restore_directory_files }}"
  register: restore_run

- name: Stop the run when the playbook refused it as a whole
  ansible.builtin.fail:
    msg: "{{ restore_run.refusal }}"
  when: restore_run.refusal != ''

- name: Check one round of the wait for the full sync of the restore relationship
  qumulo.cluster.replication_restore_step:
    edge: edge01
    edge_cluster_uuid: "{{ qumulo_cluster_uuid }}"
    entry_point: playbook
    relationship:
      source_path: /projects
      target: dr01
      target_path: /replicas/edge01-projects
    target_cluster_uuid: "{{ hostvars['dr01'].qumulo_cluster_uuid }}"
    edge_relationships: "{{ round_read.replication }}"
    waiting:
      relationship_id: "{{ restore_id }}"
      wait_started: "{{ wait_started }}"
    wait_timeout: 86400
  register: round

- name: Compose the restore record from the read of R after X's delete
  qumulo.cluster.replication_restore_step:
    edge: edge01
    entry_point: playbook
    after_delete:
      record: "{{ restore_step.actions | selectattr('action', 'equalto', 'write_record') | map(attribute='record') | first }}"
      relationship: "{{ after_delete_read.replication.relationship | default(none) }}"
  register: composed
"""

RETURN = r"""
decision:
  description:
    - V(step) to run RV(step), V(read) to call again with RV(reads), or the outcome, V(done), V(refused), V(stopped),
      V(not_restored), V(refused_by_step_21), V(not_applicable) or V(failed).
    - With O(waiting), V(landed), V(wait) or V(stopped).
  returned: with O(relationship)
  type: str
  sample: step
step:
  description: The step to run, 1 to 9, for V(step).
  returned: with O(relationship), without O(waiting)
  type: int
  sample: 2
outcome:
  description: The report's outcome, such as V(not restored), for an answer that ends the relationship's run.
  returned: with O(relationship) or O(waiting)
  type: str
fails:
  description: Whether the outcome fails the run.
  returned: with O(relationship) or O(waiting)
  type: bool
message:
  description: What the report prints after the outcome, the reason and its way on.
  returned: with O(relationship) or O(waiting)
  type: str
note:
  description: The selection rule's note for a relationship that is not applicable, or refused with its note.
  returned: with O(relationship), without O(waiting)
  type: str
summary:
  description: The summary line, for step 9 and for a relationship that is not applicable.
  returned: with O(relationship), without O(waiting)
  type: str
actions:
  description:
    - The step's actions, in the order the role runs them, each with C(action) and its parameters. A removal or a mark of
      the record that comes first is listed before them.
    - C(create_directory), C(ensure_record_directory), C(create_relationship), C(authorize), C(wait_for_recovery_point),
      C(make_writable), C(poll), C(reverse), C(delete_x), C(read_after_delete), C(write_record), C(mark_record),
      C(reconnect), C(apply_settings), C(write_stamp) and C(remove_record).
    - An action with C(stop_message) stops the relationship with that message when it fails, and one that also has
      C(stop_on) only when its failure's C(error_class) is that class, as C(create_relationship) and C(reverse) have
      C(replication_connection_error).
  returned: with O(relationship), without O(waiting)
  type: list
  elements: dict
reads:
  description: For V(read), the options to read and pass on the next call.
  returned: with O(relationship), without O(waiting)
  type: list
  elements: str
check_options:
  description: For V(read) at step 1, the options of the two check passes.
  returned: when step 1's checks are to be read
  type: dict
  contains:
    relationship:
      description: The options of M(qumulo.cluster.replication_relationship) on the target for R, which step 2 sends too.
      type: dict
    target:
      description: The options of M(qumulo.cluster.replication_target) on the edge for R, in check mode.
      type: dict
sequence:
  description: In check mode, the steps the module predicts from RV(step) on.
  returned: with O(relationship), without O(waiting)
  type: list
  elements: int
restore_to_do:
  description:
    - Whether the relationship has a restore to do. It has one while X, its restore record or R's pair exists, or while
      the rebuild report claims it (O(report_claims)).
  returned: with O(relationship), without O(waiting)
  type: bool
relationship_id:
  description: R's id, when known.
  returned: with O(relationship) or O(waiting)
  type: str
x:
  description: X's record, or V(null).
  returned: with O(relationship), without O(waiting)
  type: dict
past_delete:
  description: Whether the relationship is done or past X's delete, for its siblings' O(siblings).
  returned: with O(relationship), without O(waiting)
  type: bool
snapshots_left:
  description:
    - For step 9, the replication snapshots the restore leaves owned by nothing, which never expire (G-REPL-SNAPSHOT).
      These are X's recovery-point snapshots on the target, whose ids only the restore record held. The make-writable
      and the reverse leave others on both clusters, which M(qumulo.cluster.snapshot_info) lists.
    - Empty for every other answer.
  returned: with O(relationship), without O(waiting)
  type: list
  elements: dict
  contains:
    host:
      description: The inventory host that holds the snapshot.
      type: str
    id:
      description: The snapshot's id.
      type: int
deadline:
  description: Step 4's deadline, O(wait_started) plus O(wait_timeout).
  returned: for step 4, and with O(waiting)
  type: str
name:
  description: The relationship as the report names it, C(<edge>:<source path> to <target host>:<target path>).
  returned: with O(relationship), without O(waiting)
  type: str
record_path:
  description: The restore record's file, named by the hash of the relationship's key.
  returned: with O(relationship), without O(waiting)
  type: str
stamp_path:
  description: The restore stamp's file.
  returned: with O(relationship), without O(waiting)
  type: str
source_directories:
  description: The source path and its declared ancestors, outermost first, whose existence O(source_directory) reports.
  returned: with O(relationship), without O(waiting)
  type: list
  elements: str
picked:
  description:
    - The relationships the run picks, each with C(name), C(source_path), C(target_host), C(target_path), C(named),
      C(target_answered) and C(x), X's record or V(null). Those RV(outcomes) decides are left out.
  returned: with O(relationships)
  type: list
  elements: dict
outcomes:
  description:
    - The outcomes the run decides before any step, the nested group's refusals, the fan-out's losers and those a
      sibling was restored into first (O(restore_files)), each with C(name), C(outcome), C(fails) and C(message).
  returned: with O(relationships)
  type: list
  elements: dict
refusal:
  description: The playbook's refusal of its whole run, before any relationship; empty when it goes on.
  returned: with O(relationships)
  type: str
matched_names:
  description: The names in O(names) that match a C(present) relationship out of the edge.
  returned: with O(relationships)
  type: list
  elements: str
unmatched_names:
  description: The names in O(names) that match none.
  returned: with O(relationships)
  type: list
  elements: str
ends:
  description: In the second pass, whether an edge with no previous UUIDs ends at the target read, no target holding a foreign record.
  returned: with O(relationships)
  type: bool
record:
  description: With O(after_delete), the restore record to write.
  returned: with O(after_delete)
  type: dict
stops_run:
  description: True when the failure stops the edge's whole run, as for a restore record of an unknown format.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_step import choose, compose_record, pick, round_check

ARGUMENT_SPEC = {
    "edge": {"type": "str", "required": True},
    "edge_cluster_uuid": {"type": "str"},
    "previous_cluster_uuids": {"type": "list", "elements": "str", "default": []},
    "inventory_cluster_uuids": {"type": "dict", "default": {}},
    "entry_point": {"type": "str", "required": True, "choices": ["playbook", "second_pass"]},
    "inventory": {"type": "str", "default": "<inventory>"},
    "artifact_dir": {"type": "str", "default": ""},
    "now": {"type": "str"},
    "relationship": {"type": "dict"},
    "named": {"type": "bool", "default": False},
    "target_cluster_uuid": {"type": "str"},
    "edge_relationships": {"type": "dict"},
    "target_relationships": {"type": "dict"},
    "target_error": {"type": "str"},
    "record_text": {"type": "str"},
    "report_claims": {"type": "dict", "default": {}},
    "resume_window": {"type": "int", "default": 86400},
    "replication_address": {"type": "str"},
    "replication_port": {"type": "int", "default": 3712},
    "target_replication_address": {"type": "str"},
    "target_replication_port": {"type": "int", "default": 3712},
    "directories": {"type": "list", "elements": "dict", "default": []},
    "source_directory": {"type": "dict"},
    "record_directory_writable": {"type": "bool"},
    "relationship_check": {"type": "dict"},
    "target_check": {"type": "dict"},
    "in_edges": {"type": "bool", "default": False},
    "capacity": {"type": "dict", "options": {"license": {"type": "str"}, "clamp": {"type": "raw"}}},
    "license": {"type": "dict"},
    "predicted_license_pending": {"type": "bool"},
    "clamp_plan": {"type": "dict"},
    "target_spokes": {"type": "list", "elements": "dict"},
    "edge_spokes": {"type": "list", "elements": "dict"},
    "siblings": {"type": "list", "elements": "dict", "default": []},
    "step21": {"type": "dict"},
    "wait_started": {"type": "str"},
    "wait_timeout": {"type": "int", "default": 86400},
    "reverse_timeout": {"type": "int", "default": 300},
    "reconnect_timeout": {"type": "int", "default": 1800},
    "subdirectories": {"type": "list", "elements": "dict", "default": []},
    "relationships": {"type": "list", "elements": "dict"},
    "names": {"type": "list", "elements": "str"},
    "release": {"type": "bool", "default": False},
    "targets": {"type": "dict"},
    "restore_files": {"type": "list", "elements": "str", "default": []},
    "waiting": {"type": "dict"},
    "after_delete": {"type": "dict"},
}


def main():
    module = AnsibleModule(
        argument_spec=ARGUMENT_SPEC,
        required_one_of=[("relationship", "relationships", "after_delete")],
        mutually_exclusive=[("relationship", "relationships", "after_delete")],
        required_by={"relationship": ("edge_cluster_uuid", "target_cluster_uuid"), "waiting": ("relationship",)},
        supports_check_mode=True,
    )
    params = module.params
    try:
        if params["after_delete"] is not None:
            result = compose_record(params)
        elif params["relationships"] is not None:
            result = pick(params)
        elif params["waiting"] is not None:
            result = round_check(params)
        else:
            result = choose(params, module.check_mode)
    except QumuloError as e:
        module.fail_json(msg=str(e), **e.details)
    for warning in result.pop("warnings", []):
        module.warn(warning)
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
