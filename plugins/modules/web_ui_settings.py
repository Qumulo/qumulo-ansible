#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: web_ui_settings
short_description: Manage a Qumulo cluster's Web UI login banner and inactivity timeout
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Sets the banner the Web UI shows before login, and how long an idle Web UI session lasts. Options left unset keep
    the cluster's values.
  - These settings are cluster-wide, so the module has no key.
options:
  login_banner:
    description:
      - The text the Web UI shows before login, at most 32 KiB (32768 bytes in UTF-8). An empty string removes the banner.
    type: str
  inactivity_timeout:
    description:
      - Minutes an idle Web UI session lasts before it logs out, from 2 to 600. V(0) turns the timeout off.
    type: int
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET and PUT (If-Match) /v1/web-ui/settings."
  - The cluster replaces the whole document on every write, so the module sends the setting it does not change as the
    cluster holds it.
  - The cluster refuses an empty banner, and stores none as null, so V("") is sent as null.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Show a banner before login and log idle sessions out after half an hour
  qumulo.cluster.web_ui_settings:
    login_banner: Authorized use only. Activity is logged.
    inactivity_timeout: 30

- name: Log idle Web UI sessions out after ten minutes, keeping the banner
  qumulo.cluster.web_ui_settings:
    inactivity_timeout: 10

- name: Show a banner of several lines
  qumulo.cluster.web_ui_settings:
    login_banner: |
      Authorized use only.
      Activity is logged and monitored.

- name: Preview turning the timeout off
  qumulo.cluster.web_ui_settings:
    inactivity_timeout: 0
  check_mode: true
  diff: true
  register: web_ui_preview

- name: Show the timeout the cluster uses now
  ansible.builtin.debug:
    msg: "inactivity_timeout is {{ web_ui_preview.diff.before.inactivity_timeout }} minutes"
  when: web_ui_preview.changed

- name: Remove the banner
  qumulo.cluster.web_ui_settings:
    login_banner: ""
"""

RETURN = r"""
web_ui_settings:
  description: The settings after the task, with the option names above.
  returned: always
  type: dict
  contains:
    login_banner:
      description: The banner; an empty string when there is none.
      type: str
      sample: Authorized use only. Activity is logged.
    inactivity_timeout:
      description:
        - Minutes an idle session lasts, as an integer; 0 when the timeout is off.
        - A timeout set in seconds elsewhere, not a whole number of minutes, reads back as a fraction of a minute.
      type: raw
      sample: 30
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: True when a write was sent and the read-back did not confirm it, as after a lost connection.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options that read back differently.
  returned: when the cluster did not converge on what was written
  type: dict
observed:
  description: The values the cluster holds for the same options.
  returned: when the cluster did not converge on what was written
  type: dict
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import WebUiSettings
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "login_banner": {"type": "str"},
    "inactivity_timeout": {"type": "int"},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), WebUiSettings)


if __name__ == "__main__":
    main()
