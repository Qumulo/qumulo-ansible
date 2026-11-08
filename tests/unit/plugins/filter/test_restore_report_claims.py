# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.restore_report_claims: the role reads the rebuild report's claim through restore_record's reading."""

from __future__ import annotations

import errno
import os

import pytest
import yaml
from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.filter.restore_report_claims import restore_report_claims

UUID = "6f1c3d1e-0b4a-4e7e-9c55-2b8f0a7d4e10"
KEY = {"source_path": "/projects", "target_host": "hub01", "target_cluster_uuid": UUID, "target_path": "/replicas/edge01"}


def test_the_filter_gives_the_newest_reports_claim(tmp_path):
    (tmp_path / "rebuild-20261102T100000Z.yml").write_text(yaml.safe_dump({"held": [{"key": KEY, "holders": ["x-1"]}]}), encoding="utf-8")

    assert restore_report_claims(str(tmp_path)) == {"report": "rebuild-20261102T100000Z.yml", "claimed": [KEY], "stamped": []}


def test_a_directory_that_cannot_be_listed_fails_as_a_filter_error_naming_it(tmp_path, monkeypatch):
    def refuse(path):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)

    monkeypatch.setattr(os, "listdir", refuse)

    with pytest.raises(AnsibleFilterError) as raised:
        restore_report_claims(str(tmp_path))
    assert raised.value.message == f"Could not read {tmp_path}: [Errno 13] Permission denied: '{tmp_path}'"
