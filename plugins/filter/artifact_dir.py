# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.artifact_dir, which plugins/filter/artifact_dir.yml documents."""

from __future__ import annotations

import os


def artifact_dir(sources, inventory_file):
    """The artifacts directory beside the outermost inventory source on disk that holds inventory_file, never inside it.

    Ansible reads every .yml file in an inventory directory as an inventory, so the records and secrets go beside it.
    A host from no source on disk, as from a host list given with -i, has none, and gets "".
    """
    if not inventory_file:
        return ""
    holding = [
        source
        for source in sources
        if (os.path.isfile(source) and source == inventory_file) or (os.path.isdir(source) and inventory_file.startswith(source.rstrip("/") + "/"))
    ]
    return os.path.join(os.path.dirname(min(holding)), "artifacts") if holding else ""


class FilterModule:
    def filters(self):
        return {"artifact_dir": artifact_dir}
