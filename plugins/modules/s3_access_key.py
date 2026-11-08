#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: s3_access_key
short_description: Manage the S3 access keys of a user on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Makes sure a user has an S3 access key, or removes the user's keys.
  - Keys are matched by O(user) and, when O(access_key_id) is set, by key id.
  - The cluster makes both the id and the secret of a key, and returns the secret only in its response to the request
    that makes the key (G-S3-KEY). The module writes the secret at once to a file of its own in O(secret_destination),
    readable only by its owner, and returns it in RV(secret_access_key) of that run only.
  - The module refuses to make a key in a task that does not set C(no_log), or that has no O(secret_destination).
  - Without O(access_key_id), V(present) means the user holds at least one key, and RV(s3_access_key) is the newest.
  - With O(access_key_id), V(present) means the user holds that key. When it is gone, as after a rebuild, the module
    makes a new key and says so in RV(notes), while the user holds fewer keys than the ids in
    O(declared_access_key_ids) and O(access_key_id) together name; so a rebuild restores each of a user's declared keys.
    Otherwise the module fails, naming the keys the user holds.
options:
  user:
    description:
      - The user that owns the keys, as a trustee such as V(local:svc-backup), V(ad:CORP\\svc-backup) or V(uid:1000). A
        bare name is refused, and so is a group.
      - The user must exist; in check mode one that does not exist yet, because an earlier task creates it, is reported
        as a new key.
    type: str
    required: true
  access_key_id:
    description:
      - The id of the key whose secret the user's S3 clients hold, from C(access_key_id) in RV(s3_access_key) when the
        key was made. Quote it, since an id of digits only would otherwise be read as a number.
      - With O(state=absent), only this key is removed.
    type: str
  declared_access_key_ids:
    description:
      - The ids of every key the inventory declares for O(user), as the C(cluster_baseline) role passes them. A declared
        key that is gone is made again only while the user holds fewer keys than this list and O(access_key_id) name
        together.
      - Without it, only this task's O(access_key_id) counts as declared.
    type: list
    elements: str
    default: []
  secret_destination:
    description:
      - The directory on the controller where the module writes the secret of a key it makes, in
        C(s3-<user>-<access_key_id>.yml), readable only by its owner, where <user> is the user as RV(s3_access_key) names
        it, with each run of characters other than letters, digits, V(.), V(_) and V(-) replaced by one V(_). The file holds C(user), C(access_key_id) and
        C(secret_access_key). It is flushed to disk before its name appears, and never replaces another file.
      - Before a key is made, the module makes the directory, or makes the one there readable only by its owner, and
        writes a file in it and removes it; if it cannot, the task fails before any write. Through a symbolic link, the
        directory it points to is made readable only by its owner.
      - Required to make a key; a task that would make one without it fails before any write, in check mode too.
    type: path
  state:
    description:
      - Whether the key exists. V(absent) without O(access_key_id) removes every key of the user.
      - A removed key stops working at once.
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
    details:
      - A key that would be made is reported as a change, without a secret.
  diff_mode:
    support: full
    details:
      - The diff lists the user's key ids before and after. In check mode, a key the run would make appears as V((a new key)).
  idempotent:
    support: partial
    details:
      - A run that makes a key returns its secret, which no later run can return; a second run finds the key and
        changes nothing.
notes:
  - "API: GET /v1/s3/access-keys/ with the user, POST /v1/s3/access-keys/, DELETE /v1/s3/access-keys/{id};
    POST /v1/identity/find."
  - A task that would make a key fails before any write unless it sets C(no_log) and O(secret_destination) (G-S3-KEY).
    On ansible-core 2.22 and later the module also registers the secret with Ansible's masking of secret values.
  - A user holds at most two keys. A key cannot be replaced without its consumers losing access (G-S3-ROTATE).
  - A rebuilt cluster has none of the old keys, so the next run makes a new key for each declared one.
  - The module reads a user's keys in one request, since the cluster refuses a page size there (QUIRK-053).
  - When the connection is lost after a key is made, or the module cannot store its secret, or the listing after the
    make does not show the key, the module removes that key and fails; the message says to run the task again. A key
    the listing cannot be read for is kept, since its secret is in its file, with a note. When the cluster refuses to
    remove such a key, the failure names the key, and the file that holds its secret if there is one.
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
# A task that makes a key needs no_log and secret_destination, since the cluster shows the secret only once.
- name: Give the backup service an S3 key, writing a new key's secret to a file
  qumulo.cluster.s3_access_key:
    user: local:svc-backup
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  register: backup_key
  no_log: true

# Give the secret in that file to the key's consumers, then declare the key by its id (next tasks) so that
# later runs keep this key. Any task that reads backup_key.secret_access_key needs no_log too.
- name: Say where a new key's secret is
  ansible.builtin.debug:
    msg: "made S3 key {{ backup_key.s3_access_key.access_key_id }}; its secret is in {{ backup_key.secret_file }}"
  when: backup_key.secret_file is defined

- name: Keep the key the backup service holds, by its id
  qumulo.cluster.s3_access_key:
    user: local:svc-backup
    access_key_id: "{{ backup_s3_access_key_id }}"
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  no_log: true

# After a rebuild the cluster holds none of these keys, so the run makes a new key for each one; every S3 client then
# needs the new secret from its file, and the ids in backup_s3_access_key_ids must be replaced by the new ids.
- name: Keep both keys the backup service holds, making either again if it is gone
  qumulo.cluster.s3_access_key:
    user: local:svc-backup
    access_key_id: "{{ item }}"
    declared_access_key_ids: "{{ backup_s3_access_key_ids }}"
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  loop: "{{ backup_s3_access_key_ids }}"
  no_log: true

# Replace a key. Its consumers lose access until they hold the new secret. If the user holds another key, list its id
# with this one in declared_access_key_ids on the second task, or the run fails.
- name: Retire the key the backup service holds
  qumulo.cluster.s3_access_key:
    user: local:svc-backup
    access_key_id: "{{ backup_s3_access_key_id }}"
    state: absent

- name: Make the backup service's replacement key
  qumulo.cluster.s3_access_key:
    user: local:svc-backup
    access_key_id: "{{ backup_s3_access_key_id }}"
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  register: replacement_key
  no_log: true

- name: Show the id to declare from now on
  ansible.builtin.debug:
    msg: "set access_key_id to {{ replacement_key.s3_access_key.access_key_id }}"
  when: replacement_key.changed

- name: Preview removing every key of a retired service
  qumulo.cluster.s3_access_key:
    user: local:svc-old
    state: absent
  check_mode: true
  diff: true
  register: retired_keys

- name: Show the keys that would go
  ansible.builtin.debug:
    msg: "{{ retired_keys.diff.before.access_key_ids }}"
  when: retired_keys.changed

- name: Remove every key of the retired service
  qumulo.cluster.s3_access_key:
    user: local:svc-old
    state: absent
"""

RETURN = r"""
s3_access_key:
  description:
    - The key that meets the declaration after the task, without its secret; an empty dict after O(state=absent).
    - In check mode a key the run would make has no id or creation time yet, and both are null.
  returned: always
  type: dict
  contains:
    user:
      description: The key's owner, as a trustee.
      type: str
    access_key_id:
      description: The key's id.
      type: str
    creation_time:
      description: When the cluster made the key.
      type: str
  sample: {user: local:svc-backup, access_key_id: 00000000000003e8b5a2, creation_time: "2026-11-02T01:58:32Z"}
secret_access_key:
  description: The new key's secret, the only time the cluster returns it.
  returned: when the run made a key, outside check mode
  type: str
  sample: 0123456789abcdef-example-secret
secret_file:
  description: The file in O(secret_destination) that holds the new key's secret.
  returned: when the run made a key, outside check mode
  type: str
  sample: /srv/artifacts/hub01/secrets/s3-local_svc-backup-00000000000003e8b5a2.yml
notes:
  description:
    - What the module noticed, such as a declared key that was gone and the new one made in its place, a user that does
      not exist yet in check mode, or a new key whose listing could not be read.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["access key 00000000000003e8b5a2 of local:svc-backup does not exist, as after a rebuild; a new key would be made in its place"]
maybe_applied:
  description: True when the connection was lost after a key's removal was sent, so the key may be gone. The module fails with C(changed) true.
  returned: on such a failure
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import S3AccessKey

ARGUMENT_SPEC = {
    "user": {"type": "str", "required": True},
    "access_key_id": {"type": "str"},
    "secret_destination": {"type": "path"},
    # Key ids, which the inventory holds in the clear; a secret never passes through this option.
    "declared_access_key_ids": {"type": "list", "elements": "str", "default": [], "no_log": False},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), S3AccessKey)


if __name__ == "__main__":
    main()
