#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: kerberos_settings
short_description: Manage a Qumulo cluster's Kerberos settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the cluster's Kerberos settings. Options left unset keep the cluster's values.
  - The settings are written only when one differs, with one PUT of the whole settings document.
  - The keytab is managed by M(qumulo.cluster.kerberos_keytab).
options:
  use_alt_security_identities_mapping:
    description:
      - Map a Kerberos principal to an Active Directory user through the user's C(altSecurityIdentities) attribute.
      - Deleting the keytab with M(qumulo.cluster.kerberos_keytab) turns it off on the cluster, which that module turns
        back on (QUIRK-161).
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
  - "API: GET and PUT /v1/auth/kerberos-settings. The PUT carries the If-Match of the GET; that ETag also changes with the
    keytab, so a keytab change between the read and the write makes the module read again."
  - "Privileges: PRIVILEGE_KERBEROS_SETTINGS_READ, and PRIVILEGE_KERBEROS_SETTINGS_WRITE when the setting changes."
  - The cluster takes the setting whether or not it is joined to a domain.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# A second run changes nothing. Deleting the cluster's keytab with qumulo.cluster.kerberos_keytab turns this setting
# off (QUIRK-161), and that module turns it back on. A rerun after a failed put-back finds no keytab and does not turn
# it on, so a play that deletes a keytab declares the setting after it.
- name: Map Kerberos principals to AD users through altSecurityIdentities
  qumulo.cluster.kerberos_settings:
    use_alt_security_identities_mapping: true

- name: Preview turning the altSecurityIdentities mapping off
  qumulo.cluster.kerberos_settings:
    use_alt_security_identities_mapping: false
  check_mode: true
  diff: true
  register: kerberos_preview

- name: Show the settings that would change
  ansible.builtin.debug:
    msg: "{{ kerberos_preview.diff.before }} -> {{ kerberos_preview.diff.after }}"
  when: kerberos_preview.changed

# With no option the task changes nothing and returns the settings.
- name: Read the Kerberos settings without changing them
  qumulo.cluster.kerberos_settings:
  register: kerberos_now
"""

RETURN = r"""
kerberos_settings:
  description: The settings after the task, with the option names above. In check mode, the settings the task would leave.
  returned: always
  type: dict
  sample: {use_alt_security_identities_mapping: false}
diff:
  description: The settings before and after the task, with the option names above.
  returned: when the task runs with diff mode on
  type: dict
  contains:
    before:
      description: The settings before the task.
      type: dict
    after:
      description: The settings after the task.
      type: dict
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["write reconciled after a lost connection"]
maybe_applied:
  description:
    - True when a write was sent and the read-back did not confirm it. The module fails with C(changed) true.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options the read-back after the write did not return as written.
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {use_alt_security_identities_mapping: true}
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {use_alt_security_identities_mapping: false}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.kerberos import KerberosSettings

ARGUMENT_SPEC = {
    "use_alt_security_identities_mapping": {"type": "bool"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), KerberosSettings)


if __name__ == "__main__":
    main()
