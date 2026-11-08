# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Rules every module in plugins/modules/ follows, checked from its source without a cluster."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils.client import connection_argument_spec
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Resource

ROOT = Path(__file__).resolve().parents[2]
MODULES = sorted(path.stem for path in (ROOT / "plugins" / "modules").glob("*.py") if path.stem != "__init__")
# Pure computations, and a lock on the controller: no connection options, so no action group and no connection fragment.
LOCAL_MODULES = frozenset(
    {
        "clone_host_vars",
        "edge_cache_route",
        "host_vars_text",
        "replication_failover_plan",
        "replication_pair_plan",
        "replication_restore_info",
        "replication_restore_lock",
        "replication_restore_step",
        "tenancy_plan",
        "upgrade_plan",
        "upgrade_report",
    }
)
CLUSTER_MODULES = [name for name in MODULES if name not in LOCAL_MODULES]
# A local module may take a connection option's name as data it never connects with: tenancy_plan takes api_host and
# api_port, the play's, and this version uses neither.
CONNECTION_OPTIONS_AS_DATA = {"tenancy_plan": {"api_host", "api_port"}}
PLUGIN_FILES = sorted(path.relative_to(ROOT) for path in (ROOT / "plugins").rglob("*.py"))
CONNECTION_FRAGMENT = "qumulo.cluster.connection"
ACTION_GROUP_FRAGMENT = "qumulo.cluster.attributes.actiongroup_qumulo"
REQUIRED_ATTRIBUTES = ("check_mode", "diff_mode", "idempotent")
SUPPORT_LEVELS = {"full", "partial", "none", "N/A"}
BANNED_IMPORTS = {"requests", "six", "qumulo"}  # ansible-core is the only runtime dependency
GAP_ID = re.compile(r"\bG-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
QUIRK_ID = re.compile(r"\bQUIRK-\d{3}\b")
REGISTER_IDS = {"gaps": GAP_ID, "quirks": QUIRK_ID}
NOT_IDEMPOTENT = {"partial", "none"}
# A quirk's unit_pin: the test file and test name, or n/a and why for a quirk whose workaround is documentation (docs_only).
UNIT_PIN = re.compile(r"(?P<file>tests/unit/[\w/]+\.py)::(?P<test>test_\w+)|n/a: \S.*")
# A quirk's integration_pin: a target; pending until the module its target lands with exists; or none and why.
INTEGRATION_PIN = re.compile(r"(?P<target>[a-z0-9_]+)|pending: (?P<pending>[a-z0-9_]+) with (?P<module>[a-z0-9_]+)|none: \S.*")
# Files under plugins/ and tests/ that can cite a register id.
CITING_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".j2", ".json", ".txt"}


def module_source(name):
    return ast.parse((ROOT / "plugins" / "modules" / f"{name}.py").read_text(encoding="utf-8"))


def string_constant(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"no {name} string")


def fragment(full_name):
    """The parsed DOCUMENTATION, or the named variable, of a qumulo.cluster doc fragment."""
    parts = full_name.removeprefix("qumulo.cluster.").split(".")
    file_name, variable = parts[0], parts[1].upper() if len(parts) > 1 else "DOCUMENTATION"
    tree = ast.parse((ROOT / "plugins" / "doc_fragments" / f"{file_name}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ModuleDocFragment":
            return yaml.safe_load(string_constant(node, variable))
    raise AssertionError(f"{file_name}.py has no ModuleDocFragment")


def documentation(name):
    return yaml.safe_load(string_constant(module_source(name), "DOCUMENTATION"))


def merged_attributes(doc):
    """The module's attributes after its fragments are applied, as ansible-doc would show them."""
    merged = {}
    for name in doc.get("extends_documentation_fragment", []):
        for key, value in (fragment(name).get("attributes") or {}).items():
            merged.setdefault(key, {}).update(value)
    for key, value in (doc.get("attributes") or {}).items():
        merged.setdefault(key, {}).update(value)
    return merged


def action_group():
    return yaml.safe_load((ROOT / "meta" / "runtime.yml").read_text(encoding="utf-8"))["action_groups"]["qumulo"] or []


def test_action_group_lists_every_module_that_talks_to_a_cluster_sorted():
    assert action_group() == CLUSTER_MODULES


def test_connection_fragment_documents_the_connection_argument_spec():
    documented = fragment(CONNECTION_FRAGMENT)["options"]
    spec = connection_argument_spec()
    assert sorted(documented) == sorted(spec)
    for name, option in spec.items():
        doc = documented[name]
        description = " ".join(doc["description"]) if isinstance(doc["description"], list) else doc["description"]
        assert doc["type"] == option["type"], name
        assert doc.get("default") == option.get("default"), name
        assert doc.get("required", False) == option.get("required", False), name
        assert f"E({option['fallback'][1][0]})" in description, f"{name} does not name its environment variable"
    assert "no credentials" in " ".join(documented["expected_cluster_uuid"]["description"])


def test_action_group_fragment_names_the_qumulo_group():
    attribute = fragment(ACTION_GROUP_FRAGMENT)["attributes"]["action_group"]
    assert attribute["membership"] == ["qumulo.cluster.qumulo"]


def test_info_module_fragment_is_read_only():
    attributes = fragment("qumulo.cluster.attributes.info_module")["attributes"]
    assert {key: attributes[key]["support"] for key in REQUIRED_ATTRIBUTES} == {"check_mode": "full", "diff_mode": "none", "idempotent": "full"}


@pytest.mark.parametrize("name", CLUSTER_MODULES)
def test_module_extends_the_connection_and_action_group_fragments(name):
    fragments = documentation(name).get("extends_documentation_fragment", [])
    assert CONNECTION_FRAGMENT in fragments
    assert ACTION_GROUP_FRAGMENT in fragments


@pytest.mark.parametrize("name", MODULES)
def test_module_declares_check_diff_and_idempotent_support(name):
    attributes = merged_attributes(documentation(name))
    for key in REQUIRED_ATTRIBUTES:
        assert attributes.get(key, {}).get("support") in SUPPORT_LEVELS, f"{name}: attributes.{key}.support"


@pytest.mark.parametrize("name", MODULES)
def test_module_has_a_unit_test_file_and_an_integration_target(name):
    """A module that documents an action plugin is tested through the plugin, which does its work."""
    kind = "action" if (ROOT / "plugins" / "action" / f"{name}.py").is_file() else "modules"
    assert (ROOT / "tests" / "unit" / "plugins" / kind / f"test_{name}.py").is_file()
    if not (ROOT / "tests" / "integration").is_dir():
        pytest.skip("the built collection leaves out tests/integration")
    assert (ROOT / "tests" / "integration" / "targets" / name / "aliases").is_file()


@pytest.mark.parametrize("path", PLUGIN_FILES, ids=str)
def test_plugin_imports_no_third_party_library(path):
    imported = set()
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & BANNED_IMPORTS


def password_options_without_explicit_no_log(spec, prefix=""):
    found = []
    for option_name, option in spec.items():
        if "password" in option_name and "no_log" not in option:
            found.append(prefix + option_name)
        found += password_options_without_explicit_no_log(option.get("options") or {}, f"{prefix}{option_name}.")
    return found


@pytest.mark.parametrize("name", MODULES)
def test_password_named_options_set_no_log_explicitly(name):
    module = importlib.import_module(f"ansible_collections.qumulo.cluster.plugins.modules.{name}")
    assert password_options_without_explicit_no_log(module.ARGUMENT_SPEC) == []


def runner_class(name):
    """The class a module's main() hands to run_resource, or None for a module that does not call it."""
    for node in ast.walk(module_source(name)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_resource":
            module = importlib.import_module(f"ansible_collections.qumulo.cluster.plugins.modules.{name}")
            return getattr(module, node.args[1].id)
    return None


def can_return_notes(cls):
    """Every Resource returns the notes it gathers; another class can when its own source returns them."""
    return issubclass(cls, Resource) or "notes" in re.findall(r"result\[\"(\w+)\"\]", inspect.getsource(cls))


@pytest.mark.parametrize("name", MODULES)
def test_module_documents_notes_when_its_result_can_carry_them(name):
    cls = runner_class(name)
    if cls is None or not can_return_notes(cls):
        pytest.skip(f"{name} never returns notes")
    returned = yaml.safe_load(string_constant(module_source(name), "RETURN")) or {}
    assert "notes" in returned, f"{name}'s result can carry notes, so its RETURN must document them"
    assert (returned["notes"].get("type"), returned["notes"].get("elements")) == ("list", "str"), f"{name}: notes is a list of str"


def register(name, docs=ROOT / "docs" / "reference"):
    """docs/<name>.yml by id; fails on a malformed or duplicate id, which a dict by id would hide."""
    entries = yaml.safe_load((docs / f"{name}.yml").read_text(encoding="utf-8"))
    pattern = REGISTER_IDS[name]
    ids = [entry.get("id") for entry in entries]
    malformed = [entry_id for entry_id in ids if not isinstance(entry_id, str) or not pattern.fullmatch(entry_id)]
    duplicates = sorted({entry_id for entry_id in ids if ids.count(entry_id) > 1} - set(malformed))
    assert not malformed and not duplicates, f"docs/{name}.yml: malformed ids {malformed}, duplicate ids {duplicates}"
    return {entry["id"]: entry for entry in entries}


def cited_gaps(name):
    notes = documentation(name).get("notes") or []
    return set(GAP_ID.findall(" ".join(notes if isinstance(notes, list) else [notes])))


def citations(pattern):
    """{id: [files citing it]} over plugins/ and tests/, generated test output excluded."""
    found = {}
    for top in ("plugins", "tests"):
        for path in sorted((ROOT / top).rglob("*")):
            relative = path.relative_to(ROOT)
            if path.suffix not in CITING_SUFFIXES or not path.is_file() or relative.parts[:2] == ("tests", "output"):
                continue
            for match in pattern.findall(path.read_text(encoding="utf-8")):
                found.setdefault(match, []).append(str(relative))
    return found


def owners(quirk):
    owner = quirk["workaround_owner"]
    return owner if isinstance(owner, list) else [owner]


def owner_exists(owner):
    """Whether owner names a module, a module_utils file, or an attribute path inside one (client.QumuloClient.paginate)."""
    if owner in MODULES:
        return True
    file_name, *attributes = owner.split(".")
    if not (ROOT / "plugins" / "module_utils" / f"{file_name}.py").is_file():
        return False
    target = importlib.import_module(f"ansible_collections.qumulo.cluster.plugins.module_utils.{file_name}")
    for attribute in attributes:
        target = getattr(target, attribute, None)
        if target is None:
            return False
    return True


def pinned_test_source(path, name):
    """The source of a top-level test function or a Test* class's test method, with the comment lines just above it; None without one."""
    lines = path.read_text(encoding="utf-8").splitlines()
    tree = ast.parse("\n".join(lines))
    candidates = [*tree.body, *(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name.startswith("Test") for node in cls.body)]
    for node in candidates:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)]) - 1
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            return "\n".join(lines[start : node.end_lineno])
    return None


def cites(text, quirk_id):
    return quirk_id in QUIRK_ID.findall(text or "")


def target_cites(target, quirk_id):
    """Whether any file of the target, its aliases included, cites the quirk; a target can hold binary files."""
    return any(path.is_file() and cites(path.read_text(encoding="utf-8", errors="ignore"), quirk_id) for path in target.rglob("*"))


def quirk_pin_problems(quirk, root=ROOT):
    """What is wrong with a quirk's pins: their form and expiry always, and once it is implemented, that they exist and cite it."""
    problems = []
    unit = UNIT_PIN.fullmatch(str(quirk["unit_pin"]))
    integration = INTEGRATION_PIN.fullmatch(str(quirk["integration_pin"]))
    if not unit:
        problems.append(f"unit_pin {quirk['unit_pin']!r} is not tests/unit/<file>.py::test_<name>, or n/a: <reason>")
    elif not unit["file"] and not quirk.get("docs_only"):
        problems.append("unit_pin is n/a, but the workaround is code (docs_only is not set)")
    if not integration:
        problems.append(f"integration_pin {quirk['integration_pin']!r} is not a target, pending: <target> with <module>, or none: <reason>")
    elif integration["module"] and (root / "plugins" / "modules" / f"{integration['module']}.py").is_file():
        problems.append(f"integration_pin is pending on {integration['module']}, which now exists; name {integration['pending']}")
    if quirk["status"] != "implemented":
        return problems
    if unit and unit["file"]:
        source = pinned_test_source(root / unit["file"], unit["test"]) if (root / unit["file"]).is_file() else None
        if source is None:
            problems.append(f"unit_pin {quirk['unit_pin']} names no test")
        elif not cites(source, quirk["id"]):
            problems.append(f"unit_pin {quirk['unit_pin']} does not cite {quirk['id']} in its body or the comment above it")
    # The built collection leaves out tests/integration, so its pins are checked only in the source tree.
    if integration and integration["target"] and (root / "tests" / "integration").is_dir():
        target = root / "tests" / "integration" / "targets" / integration["target"]
        if not target.is_dir():
            problems.append(f"integration_pin {quirk['integration_pin']} is not a directory under tests/integration/targets/")
        elif not target_cites(target, quirk["id"]):
            problems.append(f"integration_pin {quirk['integration_pin']} does not cite {quirk['id']} in its tasks or aliases")
    return problems


@pytest.mark.parametrize("name", MODULES)
def test_partial_idempotency_cites_existing_gap(name):
    support = merged_attributes(documentation(name))["idempotent"]["support"]
    cited = cited_gaps(name)
    if support in NOT_IDEMPOTENT:
        assert cited, f"{name}: idempotent support is {support}, so its notes must cite the G- id in docs/reference/gaps.yml that explains why"
    assert cited <= register("gaps").keys(), f"{name} cites {sorted(cited - register('gaps').keys())}, which docs/reference/gaps.yml lacks"


def test_gap_ids_cited_anywhere_exist():
    known = register("gaps")
    unknown = {gap: files for gap, files in citations(GAP_ID).items() if gap not in known}
    assert not unknown, f"cited but missing from docs/reference/gaps.yml: {unknown}"


def test_quirk_ids_referenced_by_tests_exist():
    known = register("quirks")
    unknown = {quirk: files for quirk, files in citations(QUIRK_ID).items() if quirk not in known}
    assert not unknown, f"cited but missing from docs/reference/quirks.yml: {unknown}"


def test_each_gap_lists_the_modules_that_cite_it():
    wrong = {}
    for gap_id, gap in register("gaps").items():
        citing = sorted(name for name in MODULES if gap_id in cited_gaps(name))
        if sorted(gap["modules"]) != citing:
            wrong[gap_id] = citing
    assert not wrong, f"set modules in docs/reference/gaps.yml to the modules whose notes cite each gap: {wrong}"


def test_quirk_workaround_owner_exists_once_implemented():
    problems = []
    for quirk_id, quirk in register("quirks").items():
        existing = [owner for owner in owners(quirk) if owner_exists(owner)]
        if quirk["status"] == "implemented" and len(existing) < len(owners(quirk)):
            problems.append(f"{quirk_id}: workaround_owner {sorted(set(owners(quirk)) - set(existing))} is not a module or module_utils symbol")
        if quirk["status"] == "planned" and len(existing) == len(owners(quirk)):
            problems.append(f"{quirk_id}: {', '.join(existing)} now exists; set status: implemented and check its pins")
    assert not problems, "docs/reference/quirks.yml:\n" + "\n".join(problems)


def test_quirk_pins_are_well_formed_and_exist_once_implemented():
    problems = {quirk_id: found for quirk_id, quirk in register("quirks").items() if (found := quirk_pin_problems(quirk))}
    assert not problems, f"docs/reference/quirks.yml: {problems}"


QUIRK = {"id": "QUIRK-001", "status": "implemented", "unit_pin": "tests/unit/test_pin.py::test_pinned", "integration_pin": "quirk_target"}
PIN_TESTS = """\
# Pins QUIRK-001.
@pytest.mark.parametrize("x", [1])
def test_pinned(x):
    pass


# QUIRK-002 is another quirk.
def test_uncited():
    pass


# Pins QUIRK-001, but a blank line separates this comment from the test.

def test_after_a_blank_line():
    pass


def helper():
    def test_nested():
        pass  # QUIRK-001


class TestPins:
    def test_in_a_class(self):
        assert "QUIRK-001"


class Helper:
    def test_in_a_helper(self):
        assert "QUIRK-001"
"""


@pytest.fixture
def pinned_tree(tmp_path):
    """A collection holding a cited unit test and target, a target that cites nothing, and a module."""
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_pin.py").write_text(PIN_TESTS, encoding="utf-8")
    targets = tmp_path / "tests" / "integration" / "targets"
    (targets / "quirk_target" / "tasks").mkdir(parents=True)
    (targets / "quirk_target" / "tasks" / "main.yml").write_text("# Pins QUIRK-001\n", encoding="utf-8")
    (targets / "uncited_target" / "files").mkdir(parents=True)
    (targets / "uncited_target" / "aliases").write_text("# Pins QUIRK-002\nqumulo/edge\n", encoding="utf-8")
    (targets / "uncited_target" / "files" / "blob.bin").write_bytes(b"\xff\xfe QUIRK-002 \x80")
    (targets / "aliases_target").mkdir()
    (targets / "aliases_target" / "aliases").write_text("# Pins QUIRK-001\nqumulo/edge\n", encoding="utf-8")
    (tmp_path / "plugins" / "modules").mkdir(parents=True)
    (tmp_path / "plugins" / "modules" / "landed.py").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "pins",
    [
        {},
        {"unit_pin": "tests/unit/test_pin.py::test_in_a_class"},
        {"unit_pin": "n/a: documentation only", "docs_only": True},
        {"integration_pin": "pending: quirk_later with not_landed"},
        {"integration_pin": "none: lab only"},
        {"integration_pin": "aliases_target"},
    ],
    ids=["cited test and target", "Test class method", "docs only", "pending on a module", "no target can pin it", "cited in aliases"],
)
def test_implemented_quirk_pins_that_resolve_pass(pinned_tree, pins):
    assert quirk_pin_problems({**QUIRK, **pins}, pinned_tree) == []


@pytest.mark.parametrize(
    ("pins", "problem"),
    [
        ({"unit_pin": ""}, "unit_pin '' is not"),
        ({"unit_pin": "test_pinned"}, "is not tests/unit/<file>.py::test_<name>"),
        ({"unit_pin": "n/a"}, "is not tests/unit/<file>.py::test_<name>, or n/a: <reason>"),
        ({"unit_pin": "n/a: documentation only"}, "the workaround is code"),
        ({"unit_pin": "tests/unit/test_nothing.py::test_x"}, "names no test"),
        ({"unit_pin": "tests/unit/test_pin.py::test_x"}, "names no test"),
        ({"unit_pin": "tests/unit/test_pin.py::test_nested"}, "names no test"),
        ({"unit_pin": "tests/unit/test_pin.py::test_in_a_helper"}, "names no test"),
        ({"unit_pin": "tests/unit/test_pin.py::test_uncited"}, "does not cite QUIRK-001"),
        ({"unit_pin": "tests/unit/test_pin.py::test_after_a_blank_line"}, "does not cite QUIRK-001"),
        ({"integration_pin": ""}, "integration_pin '' is not"),
        ({"integration_pin": "(lab only)"}, "is not a target"),
        ({"integration_pin": "pending: the target lands later"}, "is not a target, pending: <target> with <module>"),
        ({"integration_pin": "pending: quirk_later with landed"}, "pending on landed, which now exists; name quirk_later"),
        ({"integration_pin": "quirk_no_such_target"}, "is not a directory under tests/integration/targets/"),
        ({"integration_pin": "uncited_target"}, "does not cite QUIRK-001"),
    ],
    ids=[
        "empty unit pin",
        "unit pin without its file",
        "n/a without a reason",
        "n/a on a code workaround",
        "unit pin in a missing file",
        "unit pin naming a missing test",
        "unit pin naming a nested function",
        "unit pin naming a method outside a Test class",
        "unit pin citing another quirk",
        "unit pin cited only above a blank line",
        "empty integration pin",
        "parenthesized note",
        "pending without its module",
        "pending on a module that exists",
        "missing target",
        "target citing another quirk, and a binary file",
    ],
)
def test_implemented_quirk_pins_that_do_not_resolve_fail(pinned_tree, pins, problem):
    problems = quirk_pin_problems({**QUIRK, **pins}, pinned_tree)

    assert len(problems) == 1 and problem in problems[0], problems


def test_planned_quirk_pins_need_only_their_form(pinned_tree):
    quirk = {"id": "QUIRK-001", "status": "planned", "unit_pin": "tests/unit/plugins/modules/test_future.py::test_future", "integration_pin": "quirk_future"}

    assert quirk_pin_problems(quirk, pinned_tree) == []
    assert quirk_pin_problems({**quirk, "unit_pin": ""}, pinned_tree) != []
    assert quirk_pin_problems({**quirk, "integration_pin": "pending: quirk_future with landed"}, pinned_tree) != [], "pending expires for planned quirks too"


@pytest.mark.parametrize(
    ("ids", "problem"),
    [(["QUIRK-001", "QUIRK-001"], "duplicate ids ['QUIRK-001']"), (["QUIRK-1"], "malformed ids ['QUIRK-1']")],
    ids=["duplicate", "malformed"],
)
def test_register_refuses_duplicate_and_malformed_ids(tmp_path, ids, problem):
    (tmp_path / "quirks.yml").write_text(yaml.safe_dump([{"id": quirk_id} for quirk_id in ids]), encoding="utf-8")

    with pytest.raises(AssertionError, match=re.escape(problem)):
        register("quirks", tmp_path)


@pytest.mark.parametrize("name", sorted(LOCAL_MODULES))
def test_local_module_takes_no_connection_options(name):
    module = importlib.import_module(f"ansible_collections.qumulo.cluster.plugins.modules.{name}")
    assert not set(module.ARGUMENT_SPEC) & set(connection_argument_spec()) - CONNECTION_OPTIONS_AS_DATA.get(name, set())
    assert CONNECTION_FRAGMENT not in documentation(name).get("extends_documentation_fragment", [])
