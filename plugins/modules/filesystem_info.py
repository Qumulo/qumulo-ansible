#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: filesystem_info
short_description: Read a Qumulo cluster's file system capacity, root directory and quotas
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Gathers facts about the file system of one cluster, and changes nothing.
  - The root directory comes back in the option shape of M(qumulo.cluster.directory), and each quota in that of
    M(qumulo.cluster.directory_quota) with its usage added.
  - With O(find_files) it also looks up paths and file ids, and returns the file each one names. A lookup runs whatever
    O(gather_subset) lists, and in check mode too, since it changes nothing.
  - A subset the cluster does not offer, or V(capacity) on a node that is not yet part of a cluster, is listed in
    RV(unavailable_subsets) with the reason, and the other subsets are still gathered. On such a node V(root) and
    V(quotas) fail.
options:
  gather_subset:
    description:
      - The subsets of facts to gather. V(all) gathers every subset.
      - V([]) gathers every subset too, unless O(find_files) is given; then only the lookup runs.
      - V(capacity) is the file system's sizes, V(root) the root directory and V(quotas) every directory quota.
    type: list
    elements: str
    choices: [all, capacity, root, quotas]
    default: [all]
  find_files:
    description:
      - Paths and file ids to look up, in one list, each answered in RV(filesystem.found_files) under the term as given.
      - A term is an absolute path with no empty, V(.) or V(..) component, such as V(/data/projects), or a file id of
        decimal digits, such as V(1003). Any other term, such as a relative path, a path with a V(..) component or a hex
        id, fails the task before any request is sent. A trailing slash is accepted.
      - A term that names no file is not an error; its entry has V(false) in C(found). That is a path that does not exist,
        a path that runs through a file, the id of a deleted file or of one never issued, an id of a file system this
        cluster does not have, and an id the server cannot place (QUIRK-101).
      - In check mode a file that an earlier task of the same run would create is not there yet, so its term is a miss.
      - A file id holds only on the cluster that issued it, and only until that cluster is rebuilt. An id from another
        cluster may name nothing here, or an unrelated file. C(docs/explanation/state-management.md), under Keys, not
        ids, says why the collection finds objects by key.
      - A term given twice is one key and is read once.
    type: list
    elements: str
    default: []
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(capacity) GET /v1/file-system, the same facts as the V(filesystem) subset of M(qumulo.cluster.cluster_info);
    V(root) GET /v1/files/%2F/info/attributes; V(quotas) GET /v1/files/quotas/status/, paged;
    O(find_files) GET /v1/files/{ref}/info/attributes, one read for each term, one after another."
  - The read of O(find_files) needs no RBAC privilege; a local user with no role could make it on Qumulo Core 7.10.3
    and 7.11.0. A path the API user may not read, or that lies under a directory it may not read, fails the task with the
    server's 403 C(fs_access_denied_error), which names the access right denied; the message ends with the hint that the
    file system's permissions deny the API user this access.
  - The quota list ends with an empty page whose next link is an empty string (QUIRK-004); every page is read.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the capacity and the quotas
  qumulo.cluster.filesystem_info:
    gather_subset: [capacity, quotas]
  register: fs

- name: List the paths that have a quota
  ansible.builtin.debug:
    msg: "{{ fs.filesystem.quotas | map(attribute='path') | list }}"

- name: Report the free space of the file system in terabytes
  qumulo.cluster.filesystem_info:
    gather_subset: [capacity]
  register: fs_capacity

- name: Show the free space
  ansible.builtin.debug:
    msg: "{{ (fs_capacity.filesystem.capacity.free_size_bytes / 1000 ** 4) | round(2) }} TB free"

- name: Find the quotas that are more than 90 percent used
  qumulo.cluster.filesystem_info:
    gather_subset: [quotas]
  register: fs_quotas

- name: Report the quotas that are more than 90 percent used
  ansible.builtin.debug:
    msg: "{{ item.path }} uses {{ item.capacity_usage }} of {{ item.limit }} bytes"
  loop: "{{ fs_quotas.filesystem.quotas }}"
  loop_control:
    label: "{{ item.path }}"
  when: item.capacity_usage > 0.9 * item.limit

- name: Read the owner and mode of the root directory
  qumulo.cluster.filesystem_info:
    gather_subset: [root]
  register: fs_root

- name: Show the root directory's owner and mode
  ansible.builtin.debug:
    msg: "{{ fs_root.filesystem.root.owner }} {{ fs_root.filesystem.root.mode }}"

# The file ids below are the cluster's own. Use one within the run that read it, and store it nowhere.
- name: Turn the paths of two directories into file ids, reading nothing else
  qumulo.cluster.filesystem_info:
    gather_subset: []
    find_files: [/data/projects, /data/scratch]
  register: ids

- name: Show the ids, or that a directory does not exist
  ansible.builtin.debug:
    msg: "{{ item.key }}: {{ item.value.id if item.value.found else 'not found' }}"
  loop: "{{ ids.filesystem.found_files | dict2items }}"
  loop_control:
    label: "{{ item.key }}"

- name: Turn the projects directory's id back into its path
  qumulo.cluster.filesystem_info:
    gather_subset: []
    find_files: ["{{ ids.filesystem.found_files['/data/projects'].id }}"]
  register: by_id
  when: ids.filesystem.found_files['/data/projects'].found

- name: Show the path that the id names
  ansible.builtin.debug:
    msg: "{{ by_id.filesystem.found_files[ids.filesystem.found_files['/data/projects'].id].path }}"
  when: by_id is not skipped

- name: Look up the projects directory
  qumulo.cluster.filesystem_info:
    gather_subset: []
    find_files: [/data/projects]
  register: projects

- name: Stop unless the projects directory exists
  # A path that does not exist is a miss, not an error, so the find passes and this task stops the play.
  ansible.builtin.assert:
    that: projects.filesystem.found_files['/data/projects'].found
    fail_msg: /data/projects does not exist on this cluster

- name: Look up the projects directory in a check run
  # A lookup only reads, so a check run answers as a real run does.
  qumulo.cluster.filesystem_info:
    gather_subset: []
    find_files: [/data/projects]
  check_mode: true
  register: projects_preview

- name: Show whether the check run found the projects directory
  ansible.builtin.debug:
    msg: "{{ projects_preview.filesystem.found_files['/data/projects'].found }}"
"""

RETURN = r"""
filesystem:
  description: The gathered facts. Each key is present only when its subset was gathered.
  returned: always
  type: dict
  contains:
    capacity:
      description: The file system's sizes in bytes, one key for each field of C(/v1/file-system) that ends in C(_bytes), such as
        C(total_size_bytes), C(free_size_bytes) and C(portal_cache_size_bytes).
      type: dict
      contains:
        total_size_bytes:
          description: The file system's usable size, or null when the cluster reports none.
          type: int
          sample: 100000000000000
        free_size_bytes:
          description: The space not yet used, or null when the cluster reports none.
          type: int
          sample: 60000000000000
    root:
      description: The root directory, in the option shape of M(qumulo.cluster.directory).
      type: dict
      contains:
        path:
          description: The directory's path, always C(/).
          type: str
          sample: /
        mode:
          description: The POSIX mode, four octal digits.
          type: str
          sample: "0777"
        owner:
          description: The owner as a trustee.
          type: str
          sample: local:admin
        group:
          description: The group as a trustee.
          type: str
          sample: local:Users
      sample: {path: /, mode: "0777", owner: local:admin, group: local:Users}
    quotas:
      description: Every directory quota, in the option shape of M(qumulo.cluster.directory_quota), with C(capacity_usage) in bytes.
      type: list
      elements: dict
      contains:
        path:
          description: The directory's path.
          type: str
          sample: /data/projects
        limit:
          description: The quota in bytes.
          type: int
          sample: 2199023255552
        capacity_usage:
          description: The bytes the directory uses against the quota.
          type: int
          sample: 4096
    found_files:
      description:
        - Each term of O(find_files), under the term as given, with the file it names. A path given with a trailing slash
          is a key with its slash, and the C(path) under it has none.
        - Each value holds the keys below.
      returned: when O(find_files) is given
      type: dict
      contains:
        found:
          description: Whether the term names a file on this cluster.
          returned: always
          type: bool
          sample: true
        path:
          description: The file's canonical path, without a trailing slash except for the root, C(/), or null when the term names no file.
          returned: always
          type: str
          sample: /data/projects
        id:
          description:
            - The file's id, a string of decimal digits, or null when the term names no file.
            - The id holds only on this cluster and until it is rebuilt; see O(find_files).
          returned: always
          type: str
          sample: "1003"
        type:
          description: The file's type as the cluster names it, such as V(FS_FILE_TYPE_FILE) or V(FS_FILE_TYPE_DIRECTORY), or null when the term names no file.
          returned: always
          type: str
          sample: FS_FILE_TYPE_DIRECTORY
      sample:
        /data/projects: {found: true, path: /data/projects, id: "1003", type: FS_FILE_TYPE_DIRECTORY}
        /data/removed: {found: false, path: null, id: null, type: null}
unavailable_subsets:
  description: Each requested subset this cluster could not answer, with the reason.
  returned: when a subset was unavailable
  type: dict
  sample: {capacity: "the node is UNCONFIGURED"}
term:
  description: The O(find_files) term whose read failed.
  returned: when the read of a term fails
  type: str
  sample: /data/projects
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_info
from ansible_collections.qumulo.cluster.plugins.module_utils.filesystem import FILESYSTEM_INFO_SUBSETS, filesystem_lookups

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *FILESYSTEM_INFO_SUBSETS], "default": ["all"]},
    "find_files": {"type": "list", "elements": "str", "default": []},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC)
    try:
        lookups = filesystem_lookups(module.params)
    except QumuloError as e:  # a bad term, refused before run_info sends anything
        module.fail_json(msg=str(e), **e.details)
    # A lookup asked for runs whatever gather_subset lists, so its name joins the subsets to gather.
    module.params["gather_subset"] = [*module.params["gather_subset"], *lookups]
    run_info(module, "filesystem", {**FILESYSTEM_INFO_SUBSETS, **lookups})


if __name__ == "__main__":
    main()
