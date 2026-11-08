# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations


class ModuleDocFragment:
    # The attribute descriptions; each module sets the support levels.
    DOCUMENTATION = r"""
options: {}
attributes:
  check_mode:
    description: Can run in check mode and return a changed status prediction without modifying the cluster.
  diff_mode:
    description: Will return details on what has changed, or would change in check mode, when in diff mode.
  idempotent:
    description:
      - When run twice in a row outside check mode with the same arguments, the second run reports no change.
      - This assumes that nothing else changed the cluster in between.
"""

    ACTIONGROUP_QUMULO = r"""
options: {}
attributes:
  action_group:
    description: Use C(group/qumulo.cluster.qumulo) in C(module_defaults) to set the connection options for this module.
    support: full
    membership:
      - qumulo.cluster.qumulo
"""

    INFO_MODULE = r"""
options: {}
attributes:
  check_mode:
    support: full
    details:
      - This action does not modify the cluster.
  diff_mode:
    support: none
    details:
      - This action does not modify the cluster.
  idempotent:
    support: full
    details:
      - This action does not modify the cluster.
"""
