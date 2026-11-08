# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.host_vars_text: a host_vars file Ansible reads back as written, never templating a captured string."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from ansible_collections.qumulo.cluster.plugins.module_utils.host_vars_text import host_vars_text
from ansible_collections.qumulo.cluster.plugins.modules import host_vars_text as module

JINJA = "projects {{ lookup('ansible.builtin.pipe', 'id') }} {% raw %}{# x #}"
VARIABLES = {
    "qumulo_smb_shares": [{"share_name": "projects", "description": JINJA, "tenant_id": 1}],
    "qumulo_cluster_name": "edge01",
    "qumulo_capacity": {"free_threshold": 0.15, "clamp": None},
    "qumulo_nfs_settings": {"v4_enabled": True},
}


def read_back(text, variables=None):
    """The file as a later play reads it: loaded as inventory, whose strings are trusted as templates, then templated."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "host_vars.yml"
        path.write_text(text, encoding="utf-8")
        loader = DataLoader()
        return Templar(loader=loader, variables=variables or {}).template(loader.load_from_file(str(path), trusted_as_template=True))


def test_a_captured_jinja_expression_is_written_literally_and_never_evaluated_when_read_back():
    text = host_vars_text(VARIABLES)

    assert "description: !unsafe" in text
    assert read_back(text)["qumulo_smb_shares"][0]["description"] == JINJA


def test_each_string_is_unsafe_and_keys_collections_and_other_values_are_plain():
    text = host_vars_text(VARIABLES)

    assert "qumulo_cluster_name: !unsafe edge01" in text and "  share_name: !unsafe projects" in text
    assert "qumulo_smb_shares:\n" in text and "qumulo_capacity:\n" in text
    assert "free_threshold: 0.15" in text and "v4_enabled: true" in text and "clamp: null" in text
    assert "!unsafe qumulo" not in text and "!unsafe share_name" not in text
    assert read_back(text) == VARIABLES


def test_a_template_added_by_hand_beside_a_captured_string_is_still_evaluated():
    text = host_vars_text({"qumulo_ad": {"domain": "corp.example.com", "ou": "{{ captured }}"}})
    edited = text.replace("qumulo_ad:\n", 'qumulo_ad:\n  join_password: "{{ vault_join_password }}"\n')

    read = read_back(edited, {"vault_join_password": "s3cret", "captured": "evaluated"})

    assert read["qumulo_ad"] == {"domain": "corp.example.com", "ou": "{{ captured }}", "join_password": "s3cret"}


def test_a_string_that_would_read_as_another_type_unquoted_is_read_back_as_the_string():
    strings = ["", "true", "null", "1.5", "~", "0x1F", "2026-11-02", "yes", "0777"]
    variables = {"qumulo_directories": [{"path": "/a", "mode": "0777"}], "values": strings, **{f"top{i}": value for i, value in enumerate(strings)}}

    text = host_vars_text(variables)

    assert "mode: '0777'" in text and "top8: '0777'" in text
    assert read_back(text) == variables


def test_the_variables_are_sorted_and_an_empty_set_is_an_empty_mapping():
    lines = [line for line in host_vars_text(VARIABLES).splitlines() if line and not line.startswith((" ", "-", "#"))]

    assert lines == sorted(lines)
    assert host_vars_text({}) == "{}\n"


def test_the_header_and_comments_wrap_and_group_as_a_capture_writes_them():
    supply = [
        {"variable": "qumulo_ssl.private_key", "why": "the API returns neither"},
        {"variable": "qumulo_ssl.certificate", "why": "the API returns neither"},
        {"variable": "qumulo_local_users[svc].password", "why": "a password is never returned " * 8},
    ]
    left_out = [{"what": "the network document", "why": "it holds the source's own addresses"}]

    text = host_vars_text({"a": 1}, header="Captured from edge01.\nA second line.", supply=supply, left_out=left_out)

    assert text.startswith("# Captured from edge01.\n# A second line.\na: 1\n\n# Never captured.")
    assert "#\n# qumulo_ssl.certificate, qumulo_ssl.private_key:\n#   the API returns neither\n" in text
    assert all(len(line) <= 116 for line in text.splitlines())
    assert text.endswith("# Read from the cluster but left out:\n#\n# the network document:\n#   it holds the source's own addresses\n")


def test_a_newline_in_a_captured_path_cannot_end_a_comment():
    left_out = [{"what": "the directory /a\nqumulo_cluster_name: injected", "why": "gone\nqumulo_dns: injected"}]

    text = host_vars_text({"qumulo_cluster_name": "edge01"}, left_out=left_out)

    assert read_back(text) == {"qumulo_cluster_name": "edge01"}
    assert all(line.startswith("#") for line in text.split("\n\n", 1)[1].splitlines())


def test_the_module_returns_the_text_and_changes_nothing(run_module):
    result = run_module(module, {"variables": VARIABLES, "header": "Captured."}, check_mode=True)

    assert result["changed"] is False
    assert result["text"] == host_vars_text(VARIABLES, "Captured.")


def test_an_entry_without_its_keys_fails_naming_them(run_module):
    result = run_module(module, {"variables": {"a": 1}, "supply": [{"variable": "qumulo_ssl.private_key"}]})

    assert result["failed"] and "why" in result["msg"]
