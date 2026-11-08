#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: kerberos_keytab
short_description: Install, replace or delete a Qumulo cluster's Kerberos keytab
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - With O(state=present), installs the keytab given in O(keytab) or O(keytab_path) as the cluster's keytab, replacing
    any other. With O(state=absent), deletes the cluster's keytab.
  - The keytab is a standalone KDC's (MIT Kerberos, FreeIPA, or a domain controller used only as a KDC), for a cluster
    outside Active Directory. A cluster joined to a domain takes no keytab, and one that holds a keytab cannot join
    (G-KRB-AD); the module fails before any keytab write, in check mode too, on a joined cluster.
  - The cluster holds one keytab. It is in the desired state when it holds the same entries as the given keytab, each
    compared by principal, key version and encryption type, and the keys of all of them through one SHA-256 digest.
  - The module reads the keytab before it sends it, and fails before any request on a keytab the cluster would refuse
    or one with no entries.
  - No key is returned, shown in a diff, or put in a message; each entry's principal, key version and encryption type
    are.
  - The cluster checks neither the principals nor the keys against the domain (G-KRB-KEYTAB).
options:
  keytab:
    description:
      - The keytab file, in base64. Whitespace in it is ignored.
      - Exactly one of O(keytab) and O(keytab_path) is needed with O(state=present). With O(state=absent) both are
        ignored, and the keytab is not read.
    type: str
  keytab_path:
    description:
      - The absolute path of a plain keytab file, read unchanged. The module reads it on the host it runs on, which is
        the controller with C(ansible_connection) set to C(local), as the collection's inventory sets it; there, a
        relative path resolves against the directory C(ansible-playbook) was started in, not the playbook's or a
        role's.
      - A file encrypted with Ansible Vault fails, because the module reads its bytes and decrypts nothing.
    type: path
  state:
    description:
      - V(present) installs the keytab, or keeps it; V(absent) deletes the cluster's keytab.
      - With V(absent), O(keytab) and O(keytab_path) are ignored and not read, so a task may keep the keytab declared.
    type: str
    choices: [present, absent]
    default: present
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
    details:
      - The diff shows the entries without their keys. When the task installs or replaces the keytab, its
        C(after_header) says that the keys changed, without showing them.
  idempotent:
    support: full
notes:
  - "API: GET, PUT and DELETE /v1/auth/kerberos-keytab, the PUT with the file as application/octet-stream and no
    If-Match (QUIRK-160); GET /v1/ad/status before a PUT; with O(state=absent), GET and PUT /v1/auth/kerberos-settings
    around the DELETE (QUIRK-161)."
  - "Privileges: PRIVILEGE_KERBEROS_KEYTAB_READ and PRIVILEGE_KERBEROS_KEYTAB_WRITE. A write of the keytab, which in
    check mode is only a keytab that would change, also needs PRIVILEGE_AD_READ for GET /v1/ad/status. With
    O(state=absent), the task also needs PRIVILEGE_KERBEROS_SETTINGS_READ and PRIVILEGE_KERBEROS_SETTINGS_WRITE."
  - The cluster keeps the low 8 bits of a key version (QUIRK-162). The module compares by them, and warns about an entry
    whose version is above 255.
  - A failed PUT is reported by its HTTP status and error class only (QUIRK-163).
  - NFS's Kerberos flavours (M(qumulo.cluster.nfs_settings)) work only once the keytab holds the cluster's NFS service
    principal, and the cluster's name and clock agree with the KDC's.
  - "Registers: G-KRB-AD, G-KRB-KEYTAB, QUIRK-160, QUIRK-161, QUIRK-162, QUIRK-163."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# The keytab is a standalone KDC's, for a cluster outside Active Directory; a cluster joined to a domain takes none.
# The file lookup decrypts a file encrypted with ansible-vault. Make one with
#   mkdir -p files
#   base64 -w0 edge01.keytab | ansible-vault encrypt --output files/edge01.keytab.b64
# The lookup finds a relative name under the playbook's or the role's files/ directory.
# A second run with the same keytab changes nothing.
- name: Install the edge's keytab for NFS with Kerberos
  qumulo.cluster.kerberos_keytab:
    keytab: "{{ lookup('ansible.builtin.file', 'edge01.keytab.b64') }}"

# A pipeline that writes the keytab to a file on the controller from its secret store can give the path instead.
# Give an absolute path. The module reads the file as it is, so the file must not be vaulted.
- name: Install the keytab a pipeline step wrote
  qumulo.cluster.kerberos_keytab:
    keytab_path: /run/secrets/edge01.keytab

# The cluster stores the keytab without asking the KDC, so a keytab for the wrong host installs and NFS with Kerberos
# then fails (G-KRB-KEYTAB); the entries below show the principals the cluster would hold.
- name: Preview replacing the keytab after the KDC rotated the service principal's keys
  qumulo.cluster.kerberos_keytab:
    keytab: "{{ lookup('ansible.builtin.file', 'edge01-rotated.keytab.b64') }}"
  check_mode: true
  diff: true
  register: keytab_preview

- name: Show the principals and key versions the keytab would hold
  ansible.builtin.debug:
    var: keytab_preview.kerberos_keytab.entries

# With state: absent the keytab options are ignored, so a play may keep the keytab declared. Deleting the keytab turns
# use_alt_security_identities_mapping off on the cluster (QUIRK-161); the module turns it back on when it was on. If
# turning it back on fails, a rerun finds no keytab and does not try again, so declare kerberos_settings after a
# keytab's absent, as the second task does.
- name: Delete the cluster's keytab and keep the altSecurityIdentities mapping on
  qumulo.cluster.kerberos_keytab:
    state: absent

- name: Declare the altSecurityIdentities mapping after the keytab is gone
  qumulo.cluster.kerberos_settings:
    use_alt_security_identities_mapping: true
"""

RETURN = r"""
kerberos_keytab:
  description: The keytab after the task, without its keys. In check mode, the keytab the task would leave.
  returned: always
  type: dict
  contains:
    entries:
      description:
        - The keytab's entries, sorted, each with C(principal), C(version), the key version the cluster keeps, and
          C(enctype). Empty when the cluster holds no keytab, and after O(state=absent).
      type: list
      elements: dict
  sample:
    entries:
      - {principal: nfs/edge01.corp.example.com@CORP.EXAMPLE.COM, version: 3, enctype: KERBEROS_ENCRYPTION_TYPE_AES256_CTS_HMAC_SHA1_96}
notes:
  description:
    - What the module noticed, such as the setting it turned back on after the DELETE, or a write it confirmed by
      reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["the keytab's DELETE turned use_alt_security_identities_mapping off (QUIRK-161); turned it back on"]
diff:
  description: The entries before and after the task, without their keys.
  returned: when the task runs with diff mode on
  type: dict
  contains:
    before:
      description: The keytab before the task.
      type: dict
    after:
      description: The keytab after the task.
      type: dict
    after_header:
      description: "C(changed, not shown: keys) when the task installs or replaces the keytab."
      returned: when the task installs or replaces the keytab
      type: str
      sample: "changed, not shown: keys"
status:
  description: The HTTP status of a request the cluster answered with an error, such as a refused keytab PUT.
  returned: on a failure the cluster answered with an HTTP error, except when turning
    use_alt_security_identities_mapping back on failed
  type: int
  sample: 400
error_class:
  description: The error class of a request the cluster answered with an error, such as a refused keytab PUT.
  returned: on a failure the cluster answered with an HTTP error, except when turning
    use_alt_security_identities_mapping back on failed
  type: str
  sample: keytab_parse_error
maybe_applied:
  description:
    - True when a write was sent and the connection was lost, and the read-back did not confirm it. The writes are the
      keytab's PUT or DELETE, and the settings PUT that turns use_alt_security_identities_mapping back on; nothing is
      read back after that PUT. The module fails with C(changed) true.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description:
    - The entries the module wrote, when the read-back after the write held others. Both this and RV(observed) are
      empty when only the keys differ.
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {entries: [{principal: nfs/edge01.corp.example.com@CORP.EXAMPLE.COM, version: 3, enctype: KERBEROS_ENCRYPTION_TYPE_AES256_CTS_HMAC_SHA1_96}]}
observed:
  description: The entries the cluster returned on that read-back.
  returned: on a failure when the read-back after a write differs from what was written, except after a lost
    connection, which returns maybe_applied instead
  type: dict
  sample: {entries: [{principal: nfs/edge01.corp.example.com@CORP.EXAMPLE.COM, version: 2, enctype: KERBEROS_ENCRYPTION_TYPE_AES256_CTS_HMAC_SHA1_96}]}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.kerberos import KerberosKeytab

ARGUMENT_SPEC = {
    "keytab": {"type": "str", "no_log": True},
    "keytab_path": {"type": "path"},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    module = qumulo_module(
        ARGUMENT_SPEC,
        mutually_exclusive=[["keytab", "keytab_path"]],
        required_if=[["state", "present", ["keytab", "keytab_path"], True]],
    )
    run_resource(module, KerberosKeytab)


if __name__ == "__main__":
    main()
