# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.policy_lock_keys, which plugins/filter/policy_lock_keys.yml documents."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError
from ansible.module_utils.parsing.convert_bool import boolean
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import policy_lock_key_plan


def policy_lock_keys(policies, keys, allow_deferred=False, unreadable=""):
    """What cluster_baseline's step 20 applies of qumulo_snapshot_policies: snapshots.policy_lock_key_plan."""
    try:
        # As Ansible reads a boolean: the string "false" is false, where Python's bool() takes it for true.
        deferred = boolean(allow_deferred, strict=True)
    except TypeError as e:
        raise AnsibleFilterError(f"qumulo_allow_deferred_policy_lock_keys must be a boolean ({e})") from e
    try:
        return policy_lock_key_plan(policies or [], keys, deferred, unreadable or "")
    except (KeyError, TypeError, AttributeError) as e:
        raise AnsibleFilterError(f"qumulo_snapshot_policies: each entry needs a policy_name, and each key a name and an id ({e!r})") from e


class FilterModule:
    def filters(self):
        return {"policy_lock_keys": policy_lock_keys}
