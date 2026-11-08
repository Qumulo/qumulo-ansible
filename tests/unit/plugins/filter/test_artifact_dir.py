# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.artifact_dir: where the roles keep a host's records and secrets, beside its inventory source."""

from __future__ import annotations

from ansible_collections.qumulo.cluster.plugins.filter.artifact_dir import artifact_dir


def test_a_file_source_puts_the_directory_beside_it(tmp_path):
    source = tmp_path / "inventory" / "hosts.yml"
    source.parent.mkdir()
    source.write_text("", encoding="utf-8")

    assert artifact_dir([str(source)], str(source)) == str(tmp_path / "inventory" / "artifacts")


def test_a_directory_source_puts_the_directory_beside_it_never_inside(tmp_path):
    source = tmp_path / "inventory"
    (source / "host_vars").mkdir(parents=True)

    assert artifact_dir([str(source)], str(source / "hosts.yml")) == str(tmp_path / "artifacts")


def test_the_outermost_source_that_holds_the_host_wins(tmp_path):
    outer, inner = tmp_path / "inventory", tmp_path / "inventory" / "lab"
    inner.mkdir(parents=True)

    assert artifact_dir([str(inner), str(outer)], str(inner / "hosts.yml")) == str(tmp_path / "artifacts")


def test_a_host_list_is_no_source_on_disk(tmp_path):
    """-i edge01, names a host, not a file: there is no inventory to keep the records beside."""
    assert artifact_dir(["edge01,"], "edge01,") == ""


def test_a_source_that_does_not_hold_the_host_or_is_gone_gives_none(tmp_path):
    other = tmp_path / "other"
    other.mkdir()

    assert artifact_dir([str(other)], str(tmp_path / "inventory" / "hosts.yml")) == ""
    assert artifact_dir([str(tmp_path / "gone.yml")], str(tmp_path / "gone.yml")) == ""
    assert artifact_dir([str(other)], "") == ""
