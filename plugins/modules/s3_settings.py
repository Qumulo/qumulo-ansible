#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: s3_settings
short_description: Manage a Qumulo cluster's S3 server settings
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Turns the cluster's S3 server on or off and sets its other cluster-wide settings. The cluster has one set of S3
    settings, so the module takes no name. Options left unset keep the cluster's values.
  - Only the settings that differ are written, in one PATCH.
options:
  enabled:
    description: Whether the S3 server answers requests.
    type: bool
  secure:
    description: Whether the S3 server accepts only HTTPS connections. With V(false) it accepts plain HTTP too.
    type: bool
  base_path:
    description:
      - Absolute path of the directory under which the cluster puts a bucket created without a path, such as one an S3
        client creates. M(qumulo.cluster.s3_bucket) always gives a path, so this option does not affect its buckets. A
        trailing slash is ignored.
      - The directory must exist. In check mode a missing directory is reported as a change, with a note.
      - A change applies only to buckets created afterwards; existing buckets keep their directories.
    type: str
  multipart_upload_expiry_interval:
    description:
      - How long an unfinished multipart upload is kept before the cluster removes it, as a whole number from 1 to 99
        followed by V(hours), V(days), V(weeks) or V(months), such as V(1days); or V(never). An empty string means
        V(never).
      - The cluster reads the interval back in the largest unit it fits, rounding down, and a month is four weeks, so
        V(14days) reads back as V(2weeks) (QUIRK-050). The module compares in that form, and returns it.
      - A value that would read back as a shorter interval, such as V(36hours), which reads back as V(1days), fails
        before any request is sent.
    type: str
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
      - The diff lists the four settings before and after.
  idempotent:
    support: full
notes:
  - "API: GET and PATCH /v1/s3/settings; GET /v1/files/{ref}/info/attributes to check O(base_path)."
  - The settings carry no ETag, so a change another client makes between the module's read and its write is
    overwritten.
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
- name: Serve S3 over HTTPS only, keeping unfinished uploads for a week
  qumulo.cluster.s3_settings:
    enabled: true
    secure: true
    multipart_upload_expiry_interval: 1weeks

- name: Keep unfinished uploads forever
  qumulo.cluster.s3_settings:
    multipart_upload_expiry_interval: never

# The directory must exist, so make it first.
- name: Make the directory that new buckets go under
  qumulo.cluster.directory:
    path: /s3

- name: Preview giving new buckets their directories under /s3
  qumulo.cluster.s3_settings:
    base_path: /s3
  check_mode: true
  diff: true
  register: s3_preview

- name: Show where new buckets would go
  ansible.builtin.debug:
    msg: "base_path is {{ s3_preview.diff.before.base_path }} and would become /s3"
  when: s3_preview.changed

- name: Give new buckets their directories under /s3
  qumulo.cluster.s3_settings:
    base_path: /s3

- name: Turn the S3 server off
  qumulo.cluster.s3_settings:
    enabled: false
"""

RETURN = r"""
s3_settings:
  description: The settings after the task, with the option names above.
  returned: always
  type: dict
  contains:
    enabled:
      description: Whether the S3 server answers requests.
      type: bool
    secure:
      description: Whether the S3 server accepts only HTTPS connections.
      type: bool
    base_path:
      description: The directory under which the cluster puts a bucket created without a path.
      type: str
    multipart_upload_expiry_interval:
      description: How long an unfinished multipart upload is kept, in the form the cluster reads it back, or V(never).
      type: str
  sample:
    enabled: true
    secure: true
    base_path: /
    multipart_upload_expiry_interval: 1weeks
notes:
  description:
    - What the module noticed, such as a O(base_path) that does not exist yet in check mode, or that a write was
      reconciled after a lost connection.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["base_path /s3 does not exist yet; an earlier task must create it before this one runs"]
maybe_applied:
  description: True when the connection was lost after a write was sent, and reading back did not confirm the write, so the settings may have
    changed. The module fails with C(changed) true.
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
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import S3Settings

ARGUMENT_SPEC = {
    "enabled": {"type": "bool"},
    "secure": {"type": "bool"},
    "base_path": {"type": "str"},
    "multipart_upload_expiry_interval": {"type": "str"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), S3Settings)


if __name__ == "__main__":
    main()
