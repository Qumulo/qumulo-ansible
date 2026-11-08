# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.restore_key_hash, which plugins/filter/restore_key_hash.yml documents."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import key_hash


def restore_key_hash(key):
    """The digest that names the relationship's restore record and stamp, as the restore's modules compute it."""
    try:
        return key_hash(key)
    except QumuloError as e:
        raise AnsibleFilterError(str(e)) from e


class FilterModule:
    def filters(self):
        return {"restore_key_hash": restore_key_hash}
