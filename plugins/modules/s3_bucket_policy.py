#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: s3_bucket_policy
short_description: Manage the policy of an S3 bucket on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets or removes the policy of one S3 bucket, found by the bucket's name. The policy says who may do what with the
    bucket and its objects.
  - The policy is the whole document, so the cluster's policy becomes O(policy), and a statement it leaves out is
    removed.
  - The documents are compared as the cluster reads a policy back, statement by statement in order. Within a statement
    the actions compare as a set, and the principals by the identities they resolve to, so V(Everyone) and the cluster's
    V(world:Everyone) are the same principal.
options:
  bucket:
    description:
      - The bucket's name.
      - With O(state=present) the bucket must exist; M(qumulo.cluster.s3_bucket) makes one. In check mode a missing
        bucket is noted instead, since an earlier task may create it.
      - With O(state=absent), a bucket that does not exist has no policy, and the task reports no change.
    type: str
    required: true
  state:
    description: Whether the bucket has a policy. V(absent) removes it.
    type: str
    choices: [present, absent]
    default: present
  policy:
    description:
      - The policy document, as in S3, with C(Statement), a list, and optionally C(Version) and C(Id). Required with
        O(state=present).
      - Each statement has C(Effect), V(Allow) or V(Deny); C(Action), an action such as V(s3:GetObject), V(s3:*), or a
        list of them; and C(Principal), whose C(Qumulo) key holds a trustee or a list of them, such as V(Everyone),
        V(local:svc-backup), V(ad:CORP\\alice), V(uid:1000) or V(sid:S-1-5-21-1-2-3-1001). A bare name is refused. A
        statement may also have C(Sid).
      - A statement applies to its own bucket, so C(Resource) is refused, as the cluster refuses it.
      - Each principal must exist when the policy is set. In check mode one that does not exist yet, because an earlier
        task creates it, is reported as a change.
    type: dict
  allow_remove_self:
    description:
      - Allow a policy that takes away the connecting user's own right to change the policy. The cluster refuses such a
        policy otherwise.
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
      - A bucket or a principal that does not exist yet, because an earlier task creates it, is reported as a change.
  diff_mode:
    support: full
    details:
      - The diff shows the bucket and its policy before and after, as the cluster reads it back. In check mode a policy
        the run would change shows after as O(policy) gives it.
  idempotent:
    support: full
notes:
  - "API: GET /v1/s3/buckets/; GET and PUT /v1/s3/buckets/{name}/policy; POST /v1/identity/find."
  - A principal the cluster reads back outside the trustee grammar, such as a bare AD name, is found through the
    identity routes. The module fails, naming the principal and the cluster's answer, when they cannot resolve it, as
    for a name no identity holds or more than one identity source holds. Identities are resolved again on every run.
  - The policy route carries no ETag, so a change another client makes between the module's read and its write is
    overwritten.
  - The module lists the buckets before it reads or writes a policy (QUIRK-051).
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
# Each principal must exist when the policy is set, so make local users and groups, and join the domain, in earlier tasks.
- name: Let everyone read the photo archive, and the backup service write it
  qumulo.cluster.s3_bucket_policy:
    bucket: photos
    policy:
      Version: "2012-10-17"
      Statement:
        - Sid: read
          Effect: Allow
          Principal: {Qumulo: [Everyone]}
          Action: [s3:GetObject, s3:ListBucket]
        - Sid: backup
          Effect: Allow
          Principal: {Qumulo: ["local:svc-backup"]}
          Action: [s3:PutObject, s3:DeleteObject]

# The policy is the whole document, so it repeats every statement the bucket keeps.
- name: Let the photographers upload and delete in the photo archive, except the contractors among them
  qumulo.cluster.s3_bucket_policy:
    bucket: photos
    policy:
      Statement:
        - Sid: read
          Effect: Allow
          Principal: {Qumulo: [Everyone]}
          Action: [s3:GetObject, s3:ListBucket]
        - Sid: backup
          Effect: Allow
          Principal: {Qumulo: ["local:svc-backup"]}
          Action: [s3:PutObject, s3:DeleteObject]
        - Sid: photographers
          Effect: Allow
          Principal: {Qumulo: ["ad:CORP\\photographers"]}
          Action: [s3:PutObject, s3:DeleteObject]
        - Sid: no-contractor-deletes
          Effect: Deny
          Principal: {Qumulo: ["ad:CORP\\contractors"]}
          Action: [s3:DeleteObject]

# The policy is the whole document: this task removes every statement it leaves out.
- name: Preview taking write access away from the backup service
  qumulo.cluster.s3_bucket_policy:
    bucket: photos
    policy:
      Statement:
        - Effect: Allow
          Principal: {Qumulo: [Everyone]}
          Action: [s3:GetObject, s3:ListBucket]
  check_mode: true
  diff: true
  register: photos_policy

- name: Show the statements the policy has now
  ansible.builtin.debug:
    msg: "{{ photos_policy.diff.before.policy.Statement | length }} statements now, 1 after"
  when: photos_policy.changed

# After this, the connecting user can no longer change the vault bucket's policy (allow_remove_self).
- name: Let only the backup service use the vault bucket, although that removes the connecting user's right to change its policy
  qumulo.cluster.s3_bucket_policy:
    bucket: vault
    allow_remove_self: true
    policy:
      Statement:
        - Effect: Allow
          Principal: {Qumulo: ["local:svc-backup"]}
          Action: ["s3:*"]

- name: Remove the scratch bucket's policy
  qumulo.cluster.s3_bucket_policy:
    bucket: scratch
    state: absent
"""

RETURN = r"""
s3_bucket_policy:
  description:
    - The bucket and its policy after the task, the policy as the cluster reads it back; an empty dict after
      O(state=absent).
    - In check mode, a policy the run would change is returned as O(policy) gives it.
  returned: always
  type: dict
  contains:
    bucket:
      description: The bucket's name.
      type: str
    policy:
      description:
        - The policy document, with C(Statement) and, where the policy has them, C(Version) and C(Id).
        - Principals are as the cluster names them, such as V(sid:S-1-5-88-1-1000) for V(uid:1000), or a bare AD name.
      type: dict
  sample:
    bucket: photos
    policy:
      Version: "2012-10-17"
      Statement:
        - {Sid: read, Effect: Allow, Action: [s3:GetObject, s3:ListBucket], Principal: {Qumulo: [world:Everyone]}}
notes:
  description:
    - What the module noticed, such as a bucket that does not exist yet in check mode, or that a write was reconciled
      after a lost connection.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["S3 bucket 'photos' does not exist yet; an earlier task must create it before this one runs"]
maybe_applied:
  description: True when the connection was lost after a write was sent, and reading back did not confirm the write, so the policy may have changed.
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
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import S3BucketPolicy

ARGUMENT_SPEC = {
    "bucket": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "policy": {"type": "dict"},
    "allow_remove_self": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), S3BucketPolicy)


if __name__ == "__main__":
    main()
