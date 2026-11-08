#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: nexus_registration
short_description: Register a Qumulo cluster with Qumulo's Nexus service
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Registers the cluster with Nexus using a single-use join key, or removes the cluster's registration.
  - The key is O(join_key), or, without one, a key the module mints in Nexus with O(nexus_api_token).
  - A cluster whose registration status is C(REGISTERED) is left alone, so a re-run neither spends nor mints a key.
  - The module registers synchronously and fails with Nexus's reason when registration fails.
options:
  state:
    description:
      - V(present) registers a cluster whose status is C(NONE) or C(PENDING_REGISTRATION).
      - With C(PENDING_REGISTRATION) and no O(join_key), the key the cluster already stores is tried first, then, if that
        fails and O(nexus_api_token) is set, a newly minted one.
      - V(absent) removes the cluster's registration secret and any stored key. It does not retire the cluster in Nexus;
        M(qumulo.cluster.nexus_cluster) does.
    type: str
    choices: [present, absent]
    default: present
  join_key:
    description:
      - A single-use Nexus join key for this cluster, as the Nexus web interface creates it.
      - It is sent only when the cluster is not registered, and never returned.
      - A key spent on a failed registration cannot be used again (G-NEXUS-KEY).
    type: str
  nexus_account_id:
    description:
      - The Nexus organization a minted key registers the cluster to.
      - Needed only when O(nexus_api_token) belongs to a user in more than one organization.
    type: int
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.nexus
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Check mode neither mints a key nor writes, and predicts the registration.
      - With the Nexus connection off, check mode still predicts the registration and warns, because an earlier
        M(qumulo.cluster.nexus_connection) task in the same check-mode run turned nothing on.
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - Registration is decided by the cluster's status, so a registered cluster is never sent another key.
      - A key spent on a failed attempt cannot be used again (G-NEXUS-KEY).
notes:
  - "API: GET, PUT and DELETE /v1/nexus/registration, POST /v1/nexus/registration/rotate; PATCH /v1/support/settings
    to clear a stored key; with O(nexus_api_token), Nexus C(POST /api/v1/api_tokens) with token type C(CLUSTER_OTP)."
  - Registration needs the Nexus connection on (M(qumulo.cluster.nexus_connection)). No other monitoring or Nexus
    setting should change while it runs.
  - Minting a key needs a Nexus role that may create API tokens in the organization.
  - A join key is single-use, and a key spent on a failed registration cannot be used again (G-NEXUS-KEY).
  - After storing the key the module POSTs rotate, which registers at once and answers with Nexus's reason when it fails
    (QUIRK-011). A rotate whose answer is lost is never sent again; the registration status says whether it registered.
  - A rotate that answers HTTP 412 is sent again, up to three times in all, until the registration status reads
    C(REGISTERED) (QUIRK-018).
  - A rotate that answers HTTP 400 C(decode_error) carrying the 5xx error page of the gateway in front of Nexus is sent
    again while the key is still pending, backing off from two seconds to fifteen, until O(retry_timeout) runs out
    (QUIRK-022).
  - A mint that the gateway in front of Nexus answers with HTTP 502, 503 or 504, or whose connection is lost, is sent
    again until O(retry_timeout) runs out. A mint whose answer was lost may leave a spare join key in the Nexus
    organization, which nothing uses.
  - V(absent) clears a stored key by PATCHing it empty, because C(DELETE /v1/nexus/registration) clears only the secret
    (QUIRK-016). The settings are never PUT (QUIRK-010).
  - Writes to C(/v1/nexus/registration) send no If-Match.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# Registration needs the Nexus connection on, which in turn needs cloud monitoring on.
- name: Turn on the Nexus connection and register with a key minted in Nexus
  block:
    - name: Turn on cloud monitoring
      qumulo.cluster.monitoring_settings:
        enabled: true
        wait: true

    - name: Connect the cluster to Nexus
      qumulo.cluster.nexus_connection:
        nexus_enabled: true

    - name: Register with a key minted in Nexus
      qumulo.cluster.nexus_registration:
        nexus_api_token: "{{ vault_nexus_api_token }}"

# Needed only when the token's user belongs to more than one Nexus organization.
- name: Register with a key minted in a chosen Nexus organization
  qumulo.cluster.nexus_registration:
    nexus_api_token: "{{ vault_nexus_api_token }}"
    nexus_account_id: 1234

# A join key is single-use, and one spent on a failed registration cannot be used again (G-NEXUS-KEY).
- name: Register with a key made in the Nexus web interface
  qumulo.cluster.nexus_registration:
    join_key: "{{ vault_nexus_join_key }}"

# The cluster's Nexus connection (nexus_connection's nexus_host) must name the same Nexus service.
- name: Mint the join key in a Nexus service at another URL
  qumulo.cluster.nexus_registration:
    nexus_api_token: "{{ vault_nexus_api_token }}"
    nexus_api_url: https://nexus.example.com

# Check mode neither mints a key nor writes.
- name: Preview the registration
  qumulo.cluster.nexus_registration:
    nexus_api_token: "{{ vault_nexus_api_token }}"
  check_mode: true
  diff: true
  register: registration_preview

- name: Show where the key would come from
  ansible.builtin.debug:
    msg: "{{ registration_preview.join_key_source | default('already registered') }}"

# Removing the registration does not retire the cluster's UUID in Nexus; qumulo.cluster.nexus_cluster does.
- name: Remove the cluster's registration before it is destroyed
  qumulo.cluster.nexus_registration:
    state: absent
"""

RETURN = r"""
registration:
  description: The cluster's registration status after the task, or in check mode the status the task would leave.
  returned: always
  type: dict
  contains:
    nexus_enabled:
      description: Whether the cluster's Nexus connection is on.
      type: bool
    registration_status:
      description: The registration status, such as V(NONE), V(PENDING_REGISTRATION) or V(REGISTERED).
      type: str
    secret_created_at:
      description: When the cluster's Nexus secret was created; V(null) after the registration was removed.
      type: str
  sample: {nexus_enabled: true, registration_status: REGISTERED, secret_created_at: "2026-10-31T02:00:00Z"}
join_key_source:
  description:
    - Where the key that registered the cluster came from, or would come from in check mode.
    - V(given) is O(join_key), V(minted) a key minted in Nexus, and V(stored) the key the cluster already stored.
  returned: when the module registers the cluster, or would in check mode
  type: str
  sample: minted
notes:
  description: What the module noticed, such as a join key the cluster stored that failed to register it, so that a newly minted key registered it instead.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["the join key the cluster stored did not register it (...); registered with a newly minted key"]
maybe_applied:
  description:
    - V(true) when a write to the cluster was sent and its answer lost, so the module cannot tell whether it took effect.
    - After a lost rotate, the module reads the registration status first, and fails this way only when it is not C(REGISTERED).
    - The task fails with C(changed) set.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.monitoring import NexusRegistration
from ansible_collections.qumulo.cluster.plugins.module_utils.nexus import nexus_argument_spec

ARGUMENT_SPEC = {
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "join_key": {"type": "str", "no_log": True},
    "nexus_account_id": {"type": "int"},
    **nexus_argument_spec(),
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), NexusRegistration)


if __name__ == "__main__":
    main()
