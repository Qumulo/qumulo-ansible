#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: license
short_description: Make sure a Qumulo cluster holds a license for its current UUID
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - A license is bound to a cluster UUID. This module talks only to the cluster and makes no Nexus call. It reads the
    license the cluster holds, applies a license document when given one, and reports what it found.
  - The module never creates a license, reads one from Nexus, or waits for one. O(document) takes the license file Nexus
    issued for the cluster's UUID; once applied, the license is active at once.
  - With O(document), the module applies the document to the cluster, unless the cluster already holds it, and reads the
    license back. Licenses are told apart by their key, which the module never returns.
  - Without a document, the module reports whether the cluster holds an active license for its UUID, and the UUID a
    license must be issued for, and changes nothing. Only applying a document reports a change.
  - A license that raises the usable capacity takes effect at the cluster's next quorum formation, which the module
    reports as RV(needs_quorum_bounce) and does not cause. With O(check_waiting_cap), it also reports a held license's
    cap that still waits for one on an edge formed without a license, as RV(cap_waits_for_formation).
options:
  check_waiting_cap:
    description:
      - For an active license the cluster already held, one this run did not apply, also read the file system's size,
        returned as RV(file_system_size_bytes), and tell whether the license's cap still waits for a quorum formation,
        as RV(cap_waits_for_formation).
      - Meant for an edge whose capacity clamp is declared.
    type: bool
    default: false
    version_added: 0.1.0
  document:
    description:
      - The license document Nexus issued for this cluster, the license file. JSON text is accepted, as from
        C(lookup('ansible.builtin.file', 'license.json')).
      - The key signs every other field, so the document must have exactly the fields below; one Nexus adds later is
        refused rather than dropped.
      - The module validates the document on the cluster before it activates it, and fails without a change when the
        cluster refuses it, for example when it is for another cluster or has been altered.
    type: dict
    aliases: [license]
    suboptions:
      cluster_uuid:
        description: The UUID of the cluster the license is for.
        type: raw
        required: true
      details:
        description: The license's terms, such as C(usable_capacity_cap), as Nexus issued them.
        type: raw
        required: true
      expiration_date:
        description: When the license expires, as Nexus issued it.
        type: raw
        required: true
      earliest_expiration_date:
        description: As Nexus issued it.
        type: raw
        required: true
      license_key:
        description: The signature over the other fields. It is kept out of logs and results.
        type: str
        required: true
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Check mode applies nothing; it predicts whether a document would be applied. It does ask the cluster to validate
        the document, which changes nothing, so a document the cluster would refuse fails in check mode too.
  diff_mode:
    support: full
  idempotent:
    support: full
    details:
      - A document the cluster already holds, by its key, cap and expiry, is not applied again.
notes:
  - "API: GET /v1/license/status and /v1/license/enforcement; POST /v1/debug/license/validate and
    /v1/debug/license/activate to apply a document; GET /v1/file-system once one is applied, and then GET
    /v1/capacity/clamp when the cap is more than 10^11 bytes above the file system. With O(check_waiting_cap), for a
    license the cluster already held, GET /v1/file-system, and then GET /v1/capacity/clamp when the file system is
    inside the window RV(cap_waits_for_formation) describes and the cap more than 10^11 bytes above 10^12 bytes. The
    module makes no Nexus call."
  - The server rounds the file system, the cap and the clamp to whole units of a size the module cannot read, so the
    module compares them through a margin of 10^11 bytes.
  - Applying a document uses the cluster's debug license routes, which need the C(PRIVILEGE_DEBUG) privilege; the
    built-in C(admin) user holds it (G-LICENSE).
  - The license facts are the ones M(qumulo.cluster.cluster_info) reports in its V(license) subset.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Report whether the edge holds a license for its UUID
  # Without a document the module changes nothing.
  qumulo.cluster.license:
  register: license_check

- name: Name the UUID Nexus must issue the license for
  # A rebuilt edge always has a new UUID, and so needs a new license.
  ansible.builtin.debug:
    msg: "No license yet; ask for one for cluster {{ license_check.cluster_uuid }}"
  when: license_check.license_status == 'absent'

- name: Apply the license file Nexus issued for the edge, kept encrypted with ansible-vault
  qumulo.cluster.license:
    document: "{{ lookup('ansible.builtin.file', 'licenses/edge01.json') }}"
  register: licensed

# edge01_license holds the license file's JSON, as a mapping or as text.
- name: Apply a license document kept in a variable
  qumulo.cluster.license:
    document: "{{ edge01_license }}"

- name: Say that the larger capacity waits for the next quorum formation
  ansible.builtin.debug:
    msg: The larger capacity applies at the next quorum formation; qumulo.cluster.capacity_clamp causes one.
  when: licensed.needs_quorum_bounce

- name: Preview whether the license file would be applied
  qumulo.cluster.license:
    document: "{{ lookup('ansible.builtin.file', 'licenses/edge01.json') }}"
  check_mode: true
  diff: true
  register: license_preview

- name: Show what the preview found
  ansible.builtin.debug:
    msg: "status: {{ license_preview.license_status }}, would apply: {{ license_preview.activated }}"

- name: Tell whether the held license's cap still waits for a quorum formation
  # Meant for an edge whose capacity clamp is declared and that was formed without a license.
  qumulo.cluster.license:
    check_waiting_cap: true
  register: cap_check

- name: Say that the held license's cap applies at the next quorum formation
  ansible.builtin.debug:
    msg: >-
      The file system holds {{ cap_check.file_system_size_bytes }} bytes; the license's cap applies at the next quorum
      formation, which qumulo.cluster.capacity_clamp causes when it changes the clamp.
  when: cap_check.cap_waits_for_formation
"""

RETURN = r"""
cluster_uuid:
  description: The cluster's current UUID, the one a license must be issued for.
  returned: success, and when the cluster accepted the document but does not report it held
  type: str
  sample: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
license:
  description: The cluster's license after the task, as M(qumulo.cluster.cluster_info) reports it.
  returned: success, and when the cluster accepted the document but does not report it held
  type: dict
  sample: {state: LICENSE_STATE_ACTIVE, cluster_uuid: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11, usable_capacity_cap: 4000000000000,
           expiration_date: "2027-10-31T23:59:59Z", enforced: true}
license_status:
  description:
    - V(active) when the cluster holds an active license for its UUID, and, when O(document) is given, that document's.
    - V(absent) when it does not, as when an activation fails.
  returned: success, and when the cluster accepted the document but does not report it held
  type: str
  choices: [active, absent]
  sample: active
license_pending:
  description: True when RV(license_status) is not V(active).
  returned: success, and when the cluster accepted the document but does not report it held
  type: bool
  sample: false
activated:
  description: True when the module applied a license document, or would in check mode.
  returned: success
  type: bool
  sample: false
needs_quorum_bounce:
  description:
    - True when the license this run applied, or in check mode would apply, allows more usable capacity than the file
      system has by more than that margin, 10^11 bytes, and the capacity clamp, when set, allows more by more than
      it too. The cap applies at the next quorum formation, which M(qumulo.cluster.capacity_clamp) causes when it changes
      the clamp.
    - A run that applies nothing reports false. With O(check_waiting_cap), such a run reports RV(cap_waits_for_formation)
      true for a cap that waits on an edge formed without a license, whose file system is inside the floor's margin;
      elsewhere the module cannot tell a cap that waits for a quorum formation from a file system the platform keeps
      smaller.
  returned: success
  type: bool
  sample: true
cap_waits_for_formation:
  description:
    - True when O(check_waiting_cap) is true, the cluster holds an active license that this run did not apply, the file
      system is at most 10^12 bytes and more than 10^12 less the margin of 10^11 bytes, the size an edge formed without a license
      has, and the smaller of the license's cap and the stored capacity clamp, an unset clamp limiting nothing, is more
      than that margin above 10^12 bytes. That cap applies at the next quorum formation, unless the platform's disks
      already limit the file system.
    - False otherwise.
  returned: success
  type: bool
  sample: false
file_system_size_bytes:
  description: The file system's size in bytes, as read for O(check_waiting_cap), whether or not the license has a cap.
  returned: when O(check_waiting_cap) is true and the cluster holds an active license that this run did not apply
  type: int
  sample: 996432412672
maybe_applied:
  description: True when the activation was sent over a connection that was lost and the license read back is not the document.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.licensing import License, license_argument_spec

ARGUMENT_SPEC = {
    "document": license_argument_spec(),
    "check_waiting_cap": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), License)


if __name__ == "__main__":
    main()
