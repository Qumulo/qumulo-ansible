# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.warn: a role's task shows a text as an Ansible warning."""

from __future__ import annotations

from ansible.utils.display import Display
from ansible_collections.qumulo.cluster.plugins.filter.warn import warn


def test_the_filter_warns_with_the_text_and_gives_it_back(monkeypatch):
    shown = []
    monkeypatch.setattr(Display, "warning", lambda self, text, *args, **kwargs: shown.append(text))

    assert warn("Could not write the restore stamp /a/done.yml.") == "Could not write the restore stamp /a/done.yml."
    assert shown == ["Could not write the restore stamp /a/done.yml."]
