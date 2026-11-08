# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.warn, which plugins/filter/warn.yml documents."""

from __future__ import annotations

from ansible.utils.display import Display


def warn(text):
    """text, once Ansible has shown it as a warning of the task being rendered."""
    Display().warning(text)
    return text


class FilterModule:
    def filters(self):
        return {"warn": warn}
