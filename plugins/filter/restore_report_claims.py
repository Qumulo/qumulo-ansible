# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.restore_report_claims, which plugins/filter/restore_report_claims.yml documents."""

from __future__ import annotations

from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.module_utils.restore_record import report_claims


def restore_report_claims(host_dir):
    """The newest rebuild report's claim in host_dir, as the restore's modules read it."""
    try:
        return report_claims(host_dir)
    except OSError as e:
        raise AnsibleFilterError(f"Could not read {host_dir}") from e


class FilterModule:
    def filters(self):
        return {"restore_report_claims": restore_report_claims}
