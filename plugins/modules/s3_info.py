#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: s3_info
short_description: Read a Qumulo cluster's S3 settings, buckets, bucket policies and access keys
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers the S3 configuration of one cluster, and changes nothing.
  - The settings, each bucket and each policy come back in the option shape of M(qumulo.cluster.s3_settings),
    M(qumulo.cluster.s3_bucket) and M(qumulo.cluster.s3_bucket_policy). Each key comes back with its user, its id and
    when it was made.
  - No key's secret is returned; the cluster returns a secret only when it makes the key (G-S3-KEY).
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
    type: list
    elements: str
    choices: [all, settings, buckets, policies, access_keys]
    default: [all]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(settings) GET /v1/s3/settings; V(buckets) GET /v1/s3/buckets/; V(policies) GET /v1/s3/buckets/ and each
    bucket's GET /v1/s3/buckets/{name}/policy; V(access_keys) GET /v1/s3/access-keys/, paged."
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
- name: Read all of the cluster's S3 configuration
  qumulo.cluster.s3_info:
  register: s3

- name: Read the S3 server settings only
  qumulo.cluster.s3_info:
    gather_subset: [settings]
  register: s3_server

- name: Say whether the S3 server is on
  ansible.builtin.debug:
    msg: "S3 is {{ 'on' if s3_server.s3.settings.enabled else 'off' }}, HTTPS only: {{ s3_server.s3.settings.secure }}"

- name: Read the buckets and the keys
  qumulo.cluster.s3_info:
    gather_subset: [buckets, access_keys]
  register: s3_inventory

- name: Check that no bucket lies outside /data
  ansible.builtin.assert:
    that: s3_inventory.s3.buckets | rejectattr('path', 'match', '^/data(/|$)') | list == []

- name: Find the ids of the keys the backup service holds
  ansible.builtin.set_fact:
    backup_key_ids: "{{ s3_inventory.s3.access_keys | selectattr('user', 'equalto', 'local:svc-backup') | map(attribute='access_key_id') | list }}"

- name: Find the buckets that have no policy
  qumulo.cluster.s3_info:
    gather_subset: [buckets, policies]
  register: s3_policies

- name: List the buckets without a policy
  ansible.builtin.debug:
    msg: "{{ s3_policies.s3.buckets | map(attribute='name') | difference(s3_policies.s3.policies | map(attribute='bucket')) | list }}"
"""

RETURN = r"""
s3:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    settings:
      description: The S3 server settings, in the option shape of M(qumulo.cluster.s3_settings).
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
    buckets:
      description: Every bucket, in the option shape of M(qumulo.cluster.s3_bucket).
      type: list
      elements: dict
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
    policies:
      description:
        - The policy of each bucket that has one, in the option shape of M(qumulo.cluster.s3_bucket_policy).
        - A bare AD name must be given a trustee prefix before the entry is passed to M(qumulo.cluster.s3_bucket_policy).
      type: list
      elements: dict
      contains:
        bucket:
          description: The bucket's name.
          type: str
        policy:
          description:
            - The policy document as the cluster reads it back.
            - Principals are as the cluster names them, such as V(sid:S-1-5-88-1-1000) for V(uid:1000),
              V(ad:Authenticated Users) for Authenticated Users, the bare V(File Owner) for File Owner, or a bare AD name.
          type: dict
    access_keys:
      description: Every key, with its user and id and when it was made, never its secret.
      type: list
      elements: dict
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
  sample:
    settings: {enabled: true, secure: true, base_path: /, multipart_upload_expiry_interval: 1weeks}
    buckets:
      - {name: photos, path: /data/photos, versioning: Enabled, object_lock: false, default_retention: null}
    policies:
      - bucket: photos
        policy:
          Statement:
            - {Effect: Allow, Action: [s3:GetObject], Principal: {Qumulo: [world:Everyone]}}
    access_keys:
      - {user: "local:svc-backup", access_key_id: 00000000000003e8b5a2, creation_time: "2026-11-02T01:58:32Z"}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.s3 import S3_INFO_SUBSETS

ARGUMENT_SPEC = {"gather_subset": {"type": "list", "elements": "str", "choices": ["all", *S3_INFO_SUBSETS], "default": ["all"]}}


def main():
    run_info(qumulo_module(ARGUMENT_SPEC), "s3", S3_INFO_SUBSETS)


if __name__ == "__main__":
    main()
