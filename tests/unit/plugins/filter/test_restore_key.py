# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.restore_key_hash: the role's task files name a restore record by the digest the modules use."""

from __future__ import annotations

import pytest
from ansible.errors import AnsibleFilterError
from ansible.template import Templar, trust_as_template
from ansible_collections.qumulo.cluster.plugins.filter.restore_key import restore_key_hash

UUID = "6f1c3d1e-0b4a-4e7e-9c55-2b8f0a7d4e10"
KEY = {"source_path": "/projects/", "target_host": "hub01", "target_cluster_uuid": UUID.upper(), "target_path": "/"}


def test_the_filter_gives_jinjas_sha256_of_the_three_joined_lines():
    """Joined by a newline character, as a YAML double-quoted "\\n" gives it. Ansible's templating keeps the two
    characters \\n of a Jinja string literal as they are, so joining with those would hash another text."""
    template = "{{ ['/projects', key.target_cluster_uuid | lower, '/'] | join('\n') | hash('sha256') }}"

    assert restore_key_hash(KEY) == Templar(loader=None, variables={"key": KEY}).template(trust_as_template(template))


def test_a_relative_path_fails_as_a_filter_error():
    with pytest.raises(AnsibleFilterError, match="target_path 'replicas' must be an absolute path"):
        restore_key_hash({**KEY, "target_path": "replicas"})
