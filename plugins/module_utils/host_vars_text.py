# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""host_vars_text: a host_vars file's text from variables read from a cluster, which Ansible must never template.

Ansible templates every string it reads from an inventory file, so a captured string holding a Jinja expression, such
as a share description, would run on the controller at the next play that reads the file. Each captured string is
written with the !unsafe tag, so Ansible reads it as the string itself, and only that string: a template a person adds
to the file later, such as a reference to a vaulted secret, is still evaluated. The comments that end the file wrap
every line they write, newlines in a captured path among them, so no captured text leaves a comment.
"""

from __future__ import annotations

import textwrap
from itertools import groupby

from ansible.module_utils.common.yaml import HAS_YAML, SafeDumper, yaml_dump

UNSAFE = "!unsafe"
WIDTH = 112  # the width of a comment's text, after its "#   " indent
SUPPLY_HEADING = "Never captured. Supply each of these to manage what it guards, a secret encrypted with ansible-vault:"
LEFT_OUT_HEADING = "Read from the cluster but left out:"

if HAS_YAML:
    from yaml.nodes import ScalarNode

    STR = "tag:yaml.org,2002:str"

    class UnsafeDumper(SafeDumper):  # pylint: disable=too-many-ancestors
        """Writes each string value !unsafe and nothing else: mapping keys, which name variables and options, and
        mappings and sequences stay untagged. Ansible resolves an !unsafe scalar's type again as if it had no tag, so a
        string that would read as another type, such as 0777 or the empty string, is written quoted without the tag
        instead: no such string can hold a template."""

        def ignore_aliases(self, data):
            return True

        def represent_str(self, data):
            if data and self.resolve(ScalarNode, data, (True, False)) == STR:
                return self.represent_scalar(UNSAFE, data)
            return super().represent_str(data)

        def represent_mapping(self, tag, mapping, flow_style=None):
            node = super().represent_mapping(tag, mapping, flow_style)
            for key, _value in node.value:
                if key.tag == UNSAFE:
                    key.tag = STR
            return node

    UnsafeDumper.add_representer(str, UnsafeDumper.represent_str)


def comment(text, indent=""):
    """text as comment lines, each "# " and indent before a line of at most WIDTH characters, whatever newlines it holds."""
    lines = []
    for paragraph in str(text).splitlines() or [""]:
        lines += textwrap.wrap(paragraph, WIDTH, break_long_words=False, break_on_hyphens=False) or [""]
    return "".join(f"# {indent}{line}".rstrip() + "\n" for line in lines)


def host_vars_text(variables, header="", supply=(), left_out=()):
    """The file: the header as comments, the variables sorted with each string !unsafe, then what to supply and what
    was left out. supply entries have variable and why, grouped by why; left_out entries have what and why."""
    text = comment(header) if header else ""
    text += yaml_dump(variables, Dumper=UnsafeDumper, indent=2, sort_keys=True, default_flow_style=False, allow_unicode=True) if variables else "{}\n"
    if supply:
        text += "\n" + comment(SUPPLY_HEADING)
        for why, entries in groupby(sorted(supply, key=lambda entry: (entry["why"], entry["variable"])), key=lambda entry: entry["why"]):
            text += "#\n" + comment(", ".join(entry["variable"] for entry in entries) + ":") + comment(why, "  ")
    if left_out:
        text += "\n" + comment(LEFT_OUT_HEADING)
        for entry in left_out:
            text += "#\n" + comment(entry["what"] + ":") + comment(entry["why"], "  ")
    return text
