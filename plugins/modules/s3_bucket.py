#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: s3_bucket
short_description: Manage an S3 bucket on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or removes one S3 bucket, matched by its name.
  - A bucket serves a directory of the file system; its objects are the files under it. Options left unset are not
    managed, so an existing bucket keeps the values it has.
  - The bucket's policy is managed by M(qumulo.cluster.s3_bucket_policy).
options:
  name:
    description: The bucket's name, which follows the S3 rules for bucket names, such as lower case only.
    type: str
    required: true
  state:
    description:
      - Whether the bucket exists.
      - Removing a bucket keeps its directory and every object in it, so a bucket created again at the same path
        serves them again. It removes the bucket's policy.
      - The cluster removes no bucket with an unfinished multipart upload. Without O(allow_data_loss) the module fails
        when the bucket has one, in check mode too.
    type: str
    choices: [present, absent]
    default: present
  path:
    description:
      - Absolute path of the directory the bucket serves. A trailing slash is ignored.
      - Required to create a bucket. Fixed once the bucket exists, so a different path fails (G-S3-PATH).
      - The directory must exist unless O(create_fs_path) is set. In check mode a missing directory is reported as a
        change, with a note.
      - A path in a portal's spoke root fails before any write; serve the bucket from the hub.
      - Several buckets may serve one directory, or nested ones.
    type: str
  create_fs_path:
    description:
      - Create O(path) and the directories above it when they do not exist.
      - Used only when the bucket is created.
    type: bool
    default: false
  versioning:
    description:
      - Whether the bucket keeps every version of an object.
      - A bucket never returns to V(Unversioned) once versioning has been V(Enabled) or V(Suspended), and a locked
        bucket keeps it V(Enabled); the module refuses those changes before any write (G-S3-LOCK).
    type: str
    choices: [Unversioned, Enabled, Suspended]
  object_lock:
    description:
      - Whether objects can be locked against deletion and overwrite.
      - A lock needs versioning V(Enabled); a new bucket with a lock gets it. On an existing bucket, set
        O(versioning=Enabled) with it.
      - A lock can never be removed, so V(false) on a locked bucket fails before any write (G-S3-LOCK).
    type: bool
  default_retention:
    description:
      - How long a locked bucket locks each new object, by default. Needs O(object_lock).
      - An empty dict, V({}), removes the default retention.
      - Otherwise give both O(default_retention.value) and O(default_retention.units); one without the other fails before
        any request.
    type: dict
    suboptions:
      value:
        description: The number of O(default_retention.units), greater than zero.
        type: int
      units:
        description: The unit of O(default_retention.value).
        type: str
        choices: [days, years]
  allow_data_loss:
    description:
      - Abort a bucket's unfinished multipart uploads, which loses them, so that the bucket can be removed.
      - The objects in the bucket's directory are never removed.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - A directory that does not exist yet, because an earlier task creates it, is reported as a change.
  diff_mode:
    support: full
    details:
      - The diff lists the bucket's name, path, versioning, object lock and default retention before and after.
  idempotent:
    support: full
notes:
  - "API: GET and POST /v1/s3/buckets/; PATCH and DELETE /v1/s3/buckets/{name}; GET /v1/s3/buckets/{name}/uploads/ and
    DELETE /v1/s3/buckets/{name}/uploads/{id}; GET /v2/portal/spokes/ and /v1/files/{ref}/info/attributes to check
    O(path)."
  - The module lists every bucket to find one. The bucket routes carry no ETag, so a change another client makes between
    the module's read and its write is overwritten.
  - Unless O(create_fs_path) is set, the module checks that O(path) exists before it creates the bucket (QUIRK-051).
  - The module always sends the default retention with the lock (QUIRK-052).
  - A bucket's directory cannot change (G-S3-PATH), and neither versioning nor a lock can be turned off (G-S3-LOCK).
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
# An S3 client writes only where the file system lets its user write, so make the directory with its owner and mode.
- name: Make the directory the photo bucket serves, owned by the photo service
  qumulo.cluster.directory:
    path: /data/photos
    owner: local:svc-photos
    mode: "0750"

- name: Serve the photo archive over S3
  qumulo.cluster.s3_bucket:
    name: photos
    path: /data/photos

- name: Serve a new project directory over S3, creating the directory with the cluster's defaults
  qumulo.cluster.s3_bucket:
    name: projects
    path: /data/projects
    create_fs_path: true

- name: Keep every version of the archive, and lock new objects for a year
  qumulo.cluster.s3_bucket:
    name: photos
    versioning: Enabled
    object_lock: true
    default_retention:
      value: 1
      units: years

- name: Stop locking new objects of the archive by default
  qumulo.cluster.s3_bucket:
    name: photos
    default_retention: {}

- name: Preview suspending versioning
  qumulo.cluster.s3_bucket:
    name: scratch
    versioning: Suspended
  check_mode: true
  diff: true
  register: scratch_preview

- name: Show what versioning would change from
  ansible.builtin.debug:
    msg: "versioning is {{ scratch_preview.diff.before.versioning }} and would become Suspended"
  when: scratch_preview.changed

- name: Remove the scratch bucket, keeping its directory and objects
  qumulo.cluster.s3_bucket:
    name: scratch
    state: absent

# Aborting the uploads loses what they held. The bucket's objects stay.
- name: Remove the scratch bucket although it has unfinished multipart uploads
  qumulo.cluster.s3_bucket:
    name: scratch
    state: absent
    allow_data_loss: true

# A bucket's directory cannot change. To move a bucket, move its files to the new directory, then remove the bucket
# and create it again there with the same settings. Its policy goes with it, so set the policy again with
# qumulo.cluster.s3_bucket_policy. Run these two tasks once, then keep only the second in the playbook.
- name: Remove the projects bucket so that it can be created at a new directory
  qumulo.cluster.s3_bucket:
    name: projects
    state: absent

- name: Serve the projects bucket from its new directory
  qumulo.cluster.s3_bucket:
    name: projects
    path: /data/archive/projects
"""

RETURN = r"""
s3_bucket:
  description: The bucket after the task, with the option names above; an empty dict after O(state=absent).
  returned: always
  type: dict
  contains:
    name:
      description: The bucket's name.
      type: str
    path:
      description: The directory the bucket serves.
      type: str
    versioning:
      description: V(Unversioned), V(Enabled) or V(Suspended).
      type: str
      choices: [Unversioned, Enabled, Suspended]
    object_lock:
      description: Whether objects can be locked.
      type: bool
    default_retention:
      description: How long a locked bucket locks each new object, by default; null when there is none.
      type: dict
      contains:
        value:
          description: The number of C(units).
          type: int
        units:
          description: The unit of C(value).
          type: str
  sample:
    name: photos
    path: /data/photos
    versioning: Enabled
    object_lock: true
    default_retention: {value: 1, units: years}
notes:
  description:
    - What the module noticed, such as a directory that does not exist yet in check mode, or that a write was
      reconciled after a lost connection.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["path /data/photos does not exist yet; an earlier task must create it before this one runs"]
maybe_applied:
  description: True when the connection was lost after a write was sent, and reading back did not confirm the write, so the bucket may have changed.
    The module fails with C(changed) true.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options the read-back after the write did not return as written.
  returned: on a failure when the read-back differs from what was written
  type: dict
observed:
  description: The values the cluster returned on that read-back, for the options in RV(expected).
  returned: on a failure when the read-back differs from what was written
  type: dict
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import S3Bucket

ARGUMENT_SPEC = {
    "name": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "path": {"type": "str"},
    "create_fs_path": {"type": "bool", "default": False},
    "versioning": {"type": "str", "choices": ["Unversioned", "Enabled", "Suspended"]},
    "object_lock": {"type": "bool"},
    "default_retention": {
        "type": "dict",
        "options": {
            "value": {"type": "int"},
            "units": {"type": "str", "choices": ["days", "years"]},
        },
    },
    "allow_data_loss": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), S3Bucket)


if __name__ == "__main__":
    main()
