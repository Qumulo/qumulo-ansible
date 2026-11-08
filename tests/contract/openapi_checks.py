# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The collection's routes and request fields, and the checks that compare them with an OpenAPI document.

A field path is dotted from the request body, with [] for the items of an array, as in
restrictions[].require_tls; $body is a body that is a bare value. A ledger entry's field may also be
a bare property name, which matches that property at any depth of the request schema.

The document a cluster serves is confidential, so nothing the checks return or raise quotes it. A problem names
only what the collection sends, JSON types, and counts; QUMULO_CONTRACT_SHOW_DOCUMENT_NAMES=1 adds the names of
required properties for a run by hand. The checks a test calls raise DocumentError, whose message names no part of
the document, in place of whatever a malformed document makes them raise.
"""

from __future__ import annotations

import ast
import functools
import importlib
import os
import re
import sys
from pathlib import Path

import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils.client import Route
from ansible_collections.qumulo.cluster.plugins.module_utils.version import _properties, _resolve

HERE = Path(__file__).resolve().parent
MODULE_UTILS = HERE.parents[1] / "plugins" / "module_utils"
PACKAGE = "ansible_collections.qumulo.cluster.plugins.module_utils"
BODY = "$body"
JSON_TYPES = frozenset({"string", "integer", "number", "boolean", "array", "object"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
LEDGER_FIELDS = ("id", "method", "path", "field", "kind", "why")
# Each kind of ledger entry, and the keys it needs beyond LEDGER_FIELDS.
LEDGER_KINDS = {
    "absent": (),  # the request schema has no such property
    "type": ("sent",),  # the property's documented type excludes the JSON type the collection sends
    "enum": ("sent",),  # the property's documented enum lacks values the collection sends
    "not_required": (),  # the server requires the property, but the document does not list it as required
    "omitted": (),  # the document leaves the whole route out on purpose, as it does an internal route; no field
    "no_body": (),  # the document declares no JSON request body, though the server takes one; no field
}
WHOLE_ROUTE_KINDS = frozenset({"omitted", "no_body"})  # the kinds whose entries name no field
PATH_PARAMETER = re.compile(r"\{[^}]*\}")
SHOW_DOCUMENT_NAMES = "QUMULO_CONTRACT_SHOW_DOCUMENT_NAMES"
OTHER_TYPE = "a type outside JSON types"


class DocumentError(Exception):
    pass


def _hides_the_document(check):
    """check for a test to call, raising DocumentError without the original exception, whose message can quote the document (a $ref's target).

    The checks call one another unwrapped, so this is the only place a traceback is cut.
    """

    @functools.wraps(check)
    def wrapper(*args, **kwargs):
        try:
            return check(*args, **kwargs)
        except Exception:  # noqa: BLE001 - any failure reading the document is reported the same way
            # Unbound and suppressed: a traceback shows neither the original exception nor its message.
            raise DocumentError(f"{check.__name__.lstrip('_')} could not read the document ({sys.exc_info()[0].__name__})") from None

    return wrapper


# Files whose Routes are another server's, which a cluster's /openapi.json does not describe.
OTHER_SERVERS = frozenset({"nexus"})


def collect_routes():
    """{"file.NAME": Route} for every Route assigned at the top level of a file in plugins/module_utils/, less OTHER_SERVERS.

    A Route imported into another file is listed once, under the file that assigns it.
    """
    routes = {}
    for path in sorted(MODULE_UTILS.glob("*.py")):
        if path.stem in OTHER_SERVERS:
            continue
        names = [
            target.id
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, ast.Assign) and _calls_route(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        if names:
            module = importlib.import_module(f"{PACKAGE}.{path.stem}")
            routes.update({f"{path.stem}.{name}": getattr(module, name) for name in names if isinstance(getattr(module, name), Route)})
    return routes


def _calls_route(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "Route") or (isinstance(func, ast.Attribute) and func.attr == "Route")


def load_wire_fields():
    return yaml.safe_load((HERE / "wire_fields.yml").read_text(encoding="utf-8")) or []


def load_ledger():
    return yaml.safe_load((HERE / "known_mismatches.yml").read_text(encoding="utf-8")) or []


def _find_operation(spec, method, path):
    """The document's operation for method and a Route path; path parameters match by position, not by name."""
    wanted = PATH_PARAMETER.sub("{}", path)
    for documented, operations in (spec.get("paths") or {}).items():
        if PATH_PARAMETER.sub("{}", documented) == wanted and method.lower() in operations:
            return operations[method.lower()]
    return None


def request_schema(spec, operation):
    body = _resolve(spec, operation.get("requestBody") or {})
    return ((body.get("content") or {}).get("application/json") or {}).get("schema")


def _wire_field_problems(spec, method, path, fields, ledger, server_version):
    """What disagrees between the fields a write sends ({path: JSON type}) and its request schema, less what the ledger lists.

    Only the ledger entries for this operation that apply to server_version (see applies) are skipped.
    """
    operation = _find_operation(spec, method, path)
    if operation is None:
        if omitted_route(ledger, method, path, server_version):
            return []
        return [f"{method} {path} is not in the document{omitted_reason(ledger, method, path, server_version)}"]
    root = request_schema(spec, operation)
    operation_entries = [entry for entry in ledger if entry["method"] == method and _same_path(entry["path"], path)]
    entries = [entry for entry in operation_entries if applies(entry, server_version)[0]]
    if root is None:
        # A write that sends no body, such as a POST that only names its object in the path, needs none.
        undocumented = fields and not any(entry["kind"] == "no_body" for entry in entries)
        return ["the document declares no JSON request body"] if undocumented else []

    def known(kind, field):
        return any(entry["kind"] == kind and _names(entry["field"], field) for entry in entries)

    def hint(kind, field):
        """Why an entry that names the field does not apply, such as an unknown server version, or ""."""
        reasons = [applies(entry, server_version)[1] for entry in operation_entries if entry["kind"] == kind and _names(entry["field"], field)]
        reasons = [reason for reason in reasons if reason]
        return f" ({reasons[0]})" if reasons else ""

    problems, objects = [], {"": root}
    for field, sent in fields.items():
        found = _walk(spec, root, field, objects)
        if found is None:
            if not known("absent", field):
                problems.append(f"{field}: not in the request schema{hint('absent', field)}")
            continue
        documented = documented_types(spec, found[1])
        if documented and not _accepts(documented, sent) and not known("type", field):
            problems.append(f"{field}: sent as {sent}, documented as {_type_names(documented)}{hint('type', field)}")
    for prefix, schema in objects.items():
        order = list(_properties(spec, schema))
        unsent = [
            prefix + name
            for name in sorted(_required(spec, schema), key=lambda name, order=order: (order.index(name) if name in order else len(order), name))
            if not any(field == prefix + name or field.startswith((prefix + name + ".", prefix + name + "[]")) for field in fields)
        ]
        if unsent and os.environ.get(SHOW_DOCUMENT_NAMES) == "1":
            problems += [f"{full}: required by the request schema but never sent" for full in unsent]
        elif unsent:
            # The names come from the document, so only their number is reported.
            where = f"{prefix.removesuffix('.')}: " if prefix else ""
            count = "1 property the request schema requires is" if len(unsent) == 1 else f"{len(unsent)} properties the request schema requires are"
            problems.append(f"{where}{count} never sent; {SHOW_DOCUMENT_NAMES}=1 names them")
    return problems


def _mismatch_holds(spec, entry):
    """(True, "") while the document still disagrees with the server as the ledger entry says, else (False, what changed)."""
    method, path, field, kind = entry["method"], entry["path"], entry.get("field"), entry["kind"]
    operation = _find_operation(spec, method, path)
    if kind == "omitted":
        return operation is None, f"the document now has {method} {path}"
    if operation is None:
        return False, f"{method} {path} is not in the document"
    root = request_schema(spec, operation)
    if kind == "no_body":
        return root is None, f"{method} {path} now declares a JSON request body"
    if root is None:
        return False, f"{method} {path} declares no JSON request body"
    matches = _locate(spec, root, field)
    if kind == "absent":
        return (not matches), f"the request schema now has {field}"
    if not matches:
        return False, f"{field} is no longer in the request schema"
    if kind == "type":
        documented = set().union(*(documented_types(spec, schema) for unused, schema in matches))
        holds = bool(documented) and not _accepts(documented, entry["sent"])
        return holds, f"{field} is documented as {_type_names(documented) or 'untyped'}"
    if kind == "enum":
        enums = [_enum(spec, schema) for unused, schema in matches]
        holds = any(enum and not set(entry["sent"]) <= set(enum) for enum in enums)
        return holds, f"the {field} enum now accepts {', '.join(entry['sent'])}, or has no enum"
    leaf = _leaf(field)
    holds = all(parent is not None and leaf not in _required(spec, parent) for parent, unused in matches)
    return holds, f"{field} is now listed as required"


find_operation = _hides_the_document(_find_operation)
# A $ref resolved within the document, for a test that walks a response schema.
resolve = _hides_the_document(_resolve)
wire_field_problems = _hides_the_document(_wire_field_problems)
mismatch_holds = _hides_the_document(_mismatch_holds)


def applies(entry, version):
    """(whether the ledger entry applies to a server of this release, why not); version is a tuple or None."""
    before = entry.get("before")
    if not before:
        return True, ""
    limit = tuple(int(part) for part in before.split("."))
    if version is None:
        return False, f"{entry['id']} applies before {before} and the server version is unknown; pass --server-version"
    if version < limit:
        return True, ""
    return False, f"{entry['id']} applies only before {before}; the server runs {'.'.join(map(str, version))}"


def omitted_route(ledger, method, path, version):
    """The ledger entry that says the document leaves method and path out on purpose, for a server of this release, or None."""
    for entry in ledger:
        if entry["kind"] == "omitted" and entry["method"] == method and _same_path(entry["path"], path) and applies(entry, version)[0]:
            return entry
    return None


def omitted_reason(ledger, method, path, version):
    """Why an omitted entry for method and path does not apply, such as an unknown server version, as " (...)", or ""."""
    for entry in ledger:
        if entry["kind"] == "omitted" and entry["method"] == method and _same_path(entry["path"], path):
            reason = applies(entry, version)[1]
            if reason:
                return f" ({reason})"
    return ""


def ledger_entry_problems(entry):
    entry_id = entry.get("id", "an entry")
    kind = entry.get("kind")
    needed = [key for key in LEDGER_FIELDS if not (key == "field" and kind in WHOLE_ROUTE_KINDS)]
    problems = [f"{entry_id}: missing {key}" for key in needed if not entry.get(key)]
    if kind not in LEDGER_KINDS:
        return [*problems, f"{entry_id}: kind must be one of {', '.join(LEDGER_KINDS)}"]
    problems += [f"{entry_id}: kind {kind} needs {key}" for key in LEDGER_KINDS[kind] if not entry.get(key)]
    if entry.get("method") not in METHODS:
        problems.append(f"{entry_id}: method {entry.get('method')!r} is not one of {', '.join(sorted(METHODS))}")
    if kind == "type" and entry.get("sent") not in JSON_TYPES:
        problems.append(f"{entry_id}: sent must be a JSON type")
    if kind == "enum" and not isinstance(entry.get("sent"), list):
        problems.append(f"{entry_id}: sent must list the values the collection sends")
    if entry.get("before") and not re.fullmatch(r"\d+(\.\d+)+", str(entry["before"])):
        problems.append(f"{entry_id}: before must be a release such as 7.11.0")
    return problems


def documented_types(spec, schema):
    """The JSON types a schema allows, through $ref and allOf, oneOf or anyOf; empty when it does not say.

    OpenAPI 3.1 may list several types. null is left out: the collection never sends one, and a nullable field
    accepts what its other types do.
    """
    schema = _resolve(spec, schema)
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        return {kind for kind in kinds if kind != "null"}
    types = set()
    for keyword in ("allOf", "oneOf", "anyOf"):
        for part in schema.get(keyword, []):
            types |= documented_types(spec, part)
    if not types and "properties" in schema:
        types.add("object")
    return types


def _type_names(documented):
    """The documented types for a message: a type the document names outside JSON types is not quoted."""
    return " or ".join(sorted({kind if kind in JSON_TYPES else OTHER_TYPE for kind in documented}))


def _accepts(documented, sent):
    return sent in documented or (sent == "integer" and "number" in documented)


def _walk(spec, root, field, objects):
    """(parent schema, property schema) for a dotted field path, or None; records each object schema passed under its prefix."""
    if field == BODY:
        return None, _resolve(spec, root)
    schema, prefix, parent = root, "", None
    for part in field.split("."):
        name = part.removesuffix("[]")
        prop = _properties(spec, schema).get(name)
        if prop is None:
            return None
        parent, schema = schema, _resolve(spec, prop)
        if part.endswith("[]"):
            schema = _resolve(spec, schema.get("items") or {})
        prefix += part + "."
        if _properties(spec, schema):
            objects.setdefault(prefix, schema)
    return parent, schema


def _locate(spec, root, field):
    """Every (parent, property schema) the ledger's field names: a dotted path, $body, or a bare name at any depth."""
    if field == BODY or "." in field or field.endswith("[]"):
        found = _walk(spec, root, field, {})
        return [found] if found else []
    found, seen = [], set()

    def visit(schema):
        schema = _resolve(spec, schema or {})
        if id(schema) in seen:
            return
        seen.add(id(schema))
        for name, prop in _properties(spec, schema).items():
            if name == field:
                found.append((schema, _resolve(spec, prop)))
            visit(prop)
        if "items" in schema:
            visit(schema["items"])
        for keyword in ("allOf", "oneOf", "anyOf"):
            for part in schema.get(keyword, []):
                visit(part)

    visit(root)
    return found


def _required(spec, schema):
    schema = _resolve(spec, schema)
    required = set(schema.get("required") or [])
    for part in schema.get("allOf", []):
        required |= _required(spec, part)
    return required


def _enum(spec, schema):
    schema = _resolve(spec, schema)
    if "enum" in schema:
        return schema["enum"]
    return _resolve(spec, schema.get("items") or {}).get("enum")


def _leaf(field):
    return field.rsplit(".", 1)[-1].removesuffix("[]")


def _names(ledger_field, field):
    """Whether a ledger entry's field names a wire field: a path names itself, a bare property name any field ending in it."""
    if ledger_field == BODY or "." in ledger_field or ledger_field.endswith("[]"):
        return ledger_field == field
    return _leaf(field) == ledger_field


def _same_path(a, b):
    return PATH_PARAMETER.sub("{}", a) == PATH_PARAMETER.sub("{}", b)
