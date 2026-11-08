# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The routes and request fields the collection uses, checked against a cluster's /openapi.json.

The tests that take the openapi fixture need a cluster or --spec-file and are skipped without one.
The rest check the declarations in this directory, and the checks themselves, on small inline
documents, so they always run.
"""

from __future__ import annotations

import openapi_checks as checks  # tests/contract/openapi_checks.py; pytest puts this directory on sys.path
import pytest

ROUTES = checks.collect_routes()
WIRE_FIELDS = checks.load_wire_fields()
LEDGER = checks.load_ledger()
WRITE_METHODS = {"POST", "PUT", "PATCH"}


@pytest.mark.parametrize(("symbol", "method"), [(symbol, method) for symbol, route in ROUTES.items() for method in route.methods])
def test_every_route_exists(openapi, server_version, symbol, method):
    path = ROUTES[symbol].path
    omitted = checks.omitted_route(LEDGER, method, path, server_version)
    if omitted:
        pytest.skip(f"{omitted['id']}: the document leaves {method} {path} out on purpose")
    # A bool, so that a failure's assertion detail cannot quote the document.
    documented = checks.find_operation(openapi, method, path) is not None
    assert documented, f"{method} {path} ({symbol}) is not in /openapi.json{checks.omitted_reason(LEDGER, method, path, server_version)}"


@pytest.mark.parametrize("entry", WIRE_FIELDS, ids=lambda entry: f"{entry['method']} {entry['route']}")
def test_wire_fields_match_the_request_schema(openapi, server_version, entry):
    problems = checks.wire_field_problems(openapi, entry["method"], ROUTES[entry["route"]].path, entry["fields"], LEDGER, server_version)
    assert not problems, f"{entry['method']} {entry['route']}:\n" + "\n".join(problems)


@pytest.mark.parametrize("entry", LEDGER, ids=lambda entry: entry["id"])
def test_known_mismatches_still_mismatch(openapi, server_version, entry):
    applies, why_not = checks.applies(entry, server_version)
    if not applies:
        pytest.skip(why_not)
    holds, detail = checks.mismatch_holds(openapi, entry)
    assert holds, f"{entry['id']} no longer mismatches: {detail}. Review the workaround and remove the entry from tests/contract/known_mismatches.yml."


def test_the_keytab_enctypes_are_the_documents_enum(openapi):
    """kerberos.ENCTYPES names every type the keytab GET reports and no other, so a keytab of any type compares."""
    from ansible_collections.qumulo.cluster.plugins.module_utils.kerberos import ENCTYPES, KEYTAB

    operation = checks.find_operation(openapi, "GET", KEYTAB.path) or {}
    response = ((operation.get("responses") or {}).get("200") or {}).get("content") or {}
    schema = checks.resolve(openapi, (response.get("application/json") or {}).get("schema") or {})
    entries = checks.resolve(openapi, (schema.get("properties") or {}).get("entries") or {})
    enctype = checks.resolve(openapi, (checks.resolve(openapi, entries.get("items") or {}).get("properties") or {}).get("enctype") or {})
    if "enum" not in enctype:
        pytest.skip("the document gives the keytab GET no enctype enum")  # as test_document_source's built document does
    # Counts only, so that a failure cannot quote the document.
    documented, ours = set(enctype["enum"]), set(ENCTYPES.values())
    assert documented == ours, f"{len(documented - ours)} documented types are not in ENCTYPES, {len(ours - documented)} in ENCTYPES are not documented"


def test_every_write_route_declares_its_wire_fields():
    declared = {(entry["route"], entry["method"]) for entry in WIRE_FIELDS}
    undeclared = sorted(
        f"{method} {symbol}" for symbol, route in ROUTES.items() for method in WRITE_METHODS & set(route.methods) if (symbol, method) not in declared
    )
    assert not undeclared, f"declare the request fields of {undeclared} in tests/contract/wire_fields.yml"


def test_wire_fields_name_declared_routes_and_types():
    problems = []
    for entry in WIRE_FIELDS:
        route = ROUTES.get(entry["route"])
        if route is None or entry["method"] not in route.methods:
            problems.append(f"{entry['method']} {entry['route']} is not a declared Route method")
        problems += [f"{entry['route']} {field}: unknown type {kind!r}" for field, kind in entry["fields"].items() if kind not in checks.JSON_TYPES]
    assert not problems, "\n".join(problems)


def test_ledger_entries_are_well_formed():
    problems = [problem for entry in LEDGER for problem in checks.ledger_entry_problems(entry)]
    ids = [entry["id"] for entry in LEDGER]
    problems += [f"duplicate id {entry_id}" for entry_id in sorted({i for i in ids if ids.count(i) > 1})]
    assert not problems, "\n".join(problems)


LEDGER_ENTRY = {"id": "x", "method": "PUT", "path": "/v1/x", "field": "a", "kind": "type", "sent": "integer", "why": "w"}


@pytest.mark.parametrize(
    ("change", "problems"),
    [
        ({"kind": "wrong"}, ["x: kind must be one of absent, type, enum, not_required, omitted, no_body"]),
        ({"sent": None}, ["x: kind type needs sent", "x: sent must be a JSON type"]),
        ({"sent": "int"}, ["x: sent must be a JSON type"]),
        ({"kind": "enum", "sent": "SUN"}, ["x: sent must list the values the collection sends"]),
        ({"before": "7.11"}, []),
        ({"before": "next"}, ["x: before must be a release such as 7.11.0"]),
        ({"method": "get"}, ["x: method 'get' is not one of DELETE, GET, PATCH, POST, PUT"]),
        ({"why": ""}, ["x: missing why"]),
        ({"kind": "omitted", "field": None, "sent": None}, []),
        ({"kind": "no_body", "field": None, "sent": None}, []),
        ({"kind": "type", "field": None}, ["x: missing field"]),
    ],
    ids=[
        "unknown kind",
        "type without sent",
        "type with no JSON type",
        "enum with a scalar sent",
        "two-part before",
        "bad before",
        "bad method",
        "missing why",
        "omitted route without a field",
        "route without a documented body, without a field",
        "field route without a field",
    ],
)
def test_ledger_entry_problems_name_each_malformed_part(change, problems):
    assert checks.ledger_entry_problems({**LEDGER_ENTRY, **change}) == problems


def test_route_collection_finds_module_level_routes_once():
    assert ROUTES["client.LOGIN"].path == "/v1/session/login"
    assert ROUTES["version.OPENAPI"].path == "/openapi.json"
    assert len({id(route) for route in ROUTES.values()}) == len(ROUTES), "a route imported into another file is listed once"


# The checks, on small inline documents.


def document(path, method, schema=None):
    operation = {"responses": {}}
    if schema is not None:
        operation["requestBody"] = {"content": {"application/json": {"schema": schema}}}
    return {"openapi": "3.0.3", "paths": {path: {method.lower(): operation}}, "components": {"schemas": {}}}


def test_find_operation_matches_path_parameters_by_position():
    spec = document("/v3/smb/shares/{share_id}", "PATCH")

    assert checks.find_operation(spec, "PATCH", "/v3/smb/shares/{id}") is not None
    assert checks.find_operation(spec, "PUT", "/v3/smb/shares/{id}") is None
    assert checks.find_operation(spec, "PATCH", "/v3/smb/shares/") is None


@pytest.fixture
def show_document_names(monkeypatch):
    monkeypatch.setenv(checks.SHOW_DOCUMENT_NAMES, "1")


@pytest.fixture(params=[None, "0"], ids=["unset", "0"])
def hide_document_names(monkeypatch, request):
    if request.param is None:
        monkeypatch.delenv(checks.SHOW_DOCUMENT_NAMES, raising=False)
    else:
        monkeypatch.setenv(checks.SHOW_DOCUMENT_NAMES, request.param)


def test_wire_field_problems_report_absent_mistyped_and_unsent_required_fields(show_document_names):
    schema = {
        "type": "object",
        "required": ["name", "limit"],
        "properties": {"name": {"type": "string"}, "limit": {"type": "integer"}, "tags": {"type": "array", "items": {"$ref": "#/components/schemas/tag"}}},
    }
    spec = document("/v1/things/", "POST", schema)
    spec["components"]["schemas"]["tag"] = {"type": "object", "properties": {"key": {"type": "string"}}}

    problems = checks.wire_field_problems(spec, "POST", "/v1/things/", {"name": "integer", "tags[].key": "string", "tags[].colour": "string"}, [], None)

    assert problems == [
        "name: sent as integer, documented as string",
        "tags[].colour: not in the request schema",
        "limit: required by the request schema but never sent",
    ]


def test_wire_field_problems_accept_a_scalar_body_and_an_integer_for_a_number():
    spec = document("/v1/size", "PUT", {"type": "number"})

    assert checks.wire_field_problems(spec, "PUT", "/v1/size", {"$body": "integer"}, [], None) == []
    assert checks.wire_field_problems(spec, "PUT", "/v1/size", {"$body": "string"}, [], None) == ["$body: sent as string, documented as number"]


def test_wire_field_problems_report_a_route_without_a_request_body():
    spec = document("/v1/things/", "POST")

    assert checks.wire_field_problems(spec, "POST", "/v1/things/", {"name": "string"}, [], None) == ["the document declares no JSON request body"]


def test_wire_field_problems_accept_a_write_without_a_body_on_a_route_without_one():
    spec = document("/v1/things/{id}/approve", "POST")

    assert checks.wire_field_problems(spec, "POST", "/v1/things/{id}/approve", {}, [], None) == []


def test_wire_field_problems_skip_what_the_ledger_lists():
    spec = document("/v1/clamp", "PUT", {"type": "object", "properties": {"size": {"type": "string"}}})
    ledger = [
        {"id": "clamp-type", "method": "PUT", "path": "/v1/clamp", "field": "size", "kind": "type", "sent": "integer"},
        {"id": "clamp-extra", "method": "PUT", "path": "/v1/clamp", "field": "extra", "kind": "absent"},
    ]

    assert checks.wire_field_problems(spec, "PUT", "/v1/clamp", {"size": "integer", "extra": "boolean"}, ledger, None) == []


@pytest.mark.parametrize(
    ("other", "problem"),
    [
        ({"method": "PATCH", "path": "/v1/clamp"}, "size: sent as integer, documented as string"),
        ({"method": "PUT", "path": "/v1/other"}, "size: sent as integer, documented as string"),
        ({"field": "tags[].size"}, "size: sent as integer, documented as string"),
    ],
    ids=["another method", "another route", "another path to the same name"],
)
def test_wire_field_problems_skip_only_the_ledger_entries_for_that_field_of_that_operation(other, problem):
    spec = document("/v1/clamp", "PUT", {"type": "object", "properties": {"size": {"type": "string"}}})
    ledger = [{"id": "clamp-type", "method": "PUT", "path": "/v1/clamp", "field": "size", "kind": "type", "sent": "integer", **other}]

    assert checks.wire_field_problems(spec, "PUT", "/v1/clamp", {"size": "integer"}, ledger, None) == [problem]


@pytest.mark.parametrize(
    ("server_version", "problems"),
    [
        ((7, 10, 3), []),
        ((7, 11, 0), ["require_tls: not in the request schema (require-tls applies only before 7.11.0; the server runs 7.11.0)"]),
        (None, ["require_tls: not in the request schema (require-tls applies before 7.11.0 and the server version is unknown; pass --server-version)"]),
    ],
    ids=["before the release", "at the release", "unknown release"],
)
def test_wire_field_problems_skip_a_version_bounded_entry_only_before_its_release(server_version, problems):
    spec = document("/v3/nfs/exports/", "POST", {"type": "object", "properties": {}})
    ledger = [{"id": "require-tls", "method": "POST", "path": "/v3/nfs/exports/", "field": "require_tls", "kind": "absent", "before": "7.11.0"}]

    assert checks.wire_field_problems(spec, "POST", "/v3/nfs/exports/", {"require_tls": "boolean"}, ledger, server_version) == problems


def test_wire_field_problems_report_a_nested_required_field_never_sent(show_document_names):
    tag = {"type": "object", "required": ["key"], "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}
    spec = document("/v1/things/", "POST", {"type": "object", "properties": {"tags": {"type": "array", "items": tag}}})

    assert checks.wire_field_problems(spec, "POST", "/v1/things/", {"tags[].value": "string"}, [], None) == [
        "tags[].key: required by the request schema but never sent"
    ]


def test_wire_field_problems_read_required_fields_through_all_of(show_document_names):
    schema = {"allOf": [{"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}, {"properties": {"size": {"type": "integer"}}}]}
    spec = document("/v1/things/", "POST", schema)

    assert checks.wire_field_problems(spec, "POST", "/v1/things/", {"size": "integer"}, [], None) == ["name: required by the request schema but never sent"]


def test_wire_field_problems_count_unsent_required_fields_without_naming_them(hide_document_names):
    tag = {"type": "object", "required": ["key", "kind"], "properties": {"key": {"type": "string"}, "value": {"type": "string"}}}
    schema = {"type": "object", "required": ["secret_name"], "properties": {"tags": {"type": "array", "items": tag}}}
    spec = document("/v1/things/", "POST", schema)

    assert checks.wire_field_problems(spec, "POST", "/v1/things/", {"tags[].value": "string"}, [], None) == [
        "1 property the request schema requires is never sent; QUMULO_CONTRACT_SHOW_DOCUMENT_NAMES=1 names them",
        "tags[]: 2 properties the request schema requires are never sent; QUMULO_CONTRACT_SHOW_DOCUMENT_NAMES=1 names them",
    ]


def test_type_outside_json_types_is_not_quoted():
    spec = document("/v1/size", "PUT", {"type": "object", "properties": {"size": {"type": "secret-type"}}})
    entry = {"id": "x", "method": "PUT", "path": "/v1/size", "field": "size", "kind": "type", "sent": "integer"}

    assert checks.wire_field_problems(spec, "PUT", "/v1/size", {"size": "integer"}, [], None) == [
        "size: sent as integer, documented as a type outside JSON types"
    ]
    assert checks.mismatch_holds(spec, entry) == (True, "size is documented as a type outside JSON types")


def test_documented_types_read_an_openapi_3_1_type_list():
    spec = document("/v1/things/", "POST", {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "note": {"type": ["null"]}}})

    assert checks.wire_field_problems(spec, "POST", "/v1/things/", {"name": "string", "note": "string"}, [], None) == []
    assert checks.wire_field_problems(spec, "POST", "/v1/things/", {"name": "integer"}, [], None) == ["name: sent as integer, documented as string"]


@pytest.mark.parametrize("keyword", ["oneOf", "anyOf"])
def test_documented_types_read_one_of_and_any_of(keyword):
    spec = document("/v1/size", "PUT", {keyword: [{"type": "integer"}, {"type": "string"}]})

    assert checks.wire_field_problems(spec, "PUT", "/v1/size", {"$body": "string"}, [], None) == []
    assert checks.wire_field_problems(spec, "PUT", "/v1/size", {"$body": "boolean"}, [], None) == ["$body: sent as boolean, documented as integer or string"]


def test_checks_raise_an_error_that_does_not_quote_the_document():
    spec = document("/v1/size", "PUT", {"$ref": "#/components/schemas/SECRET_SCHEMA_NAME"})

    with pytest.raises(checks.DocumentError) as raised:
        checks.wire_field_problems(spec, "PUT", "/v1/size", {"$body": "integer"}, [], None)

    assert "SECRET_SCHEMA_NAME" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__, "the original exception, which names the $ref, is not shown"


def test_resolve_raises_an_error_that_does_not_quote_the_document():
    with pytest.raises(checks.DocumentError) as raised:
        checks.resolve({"paths": {}}, {"$ref": "#/components/schemas/SECRET_SCHEMA_NAME"})

    assert "SECRET_SCHEMA_NAME" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


@pytest.mark.parametrize(
    ("entry", "schema", "fixed"),
    [
        (
            {"kind": "absent", "field": "addresses"},
            {"type": "object", "properties": {"networks": {"type": "array", "items": {"properties": {"api_v3_network_addresses": {"type": "object"}}}}}},
            {"type": "object", "properties": {"networks": {"type": "array", "items": {"properties": {"addresses": {"type": "object"}}}}}},
        ),
        (
            {"kind": "type", "field": "host_restrictions", "sent": "array"},
            {"type": "object", "properties": {"host_restrictions": {"type": "string"}}},
            {"type": "object", "properties": {"host_restrictions": {"type": "array", "items": {"type": "string"}}}},
        ),
        (
            {"kind": "type", "field": "$body", "sent": "integer"},
            {"type": "string"},
            {"type": "integer"},
        ),
        (
            {"kind": "enum", "field": "on_days", "sent": ["SUN", "MON"]},
            {"properties": {"schedule": {"allOf": [{"properties": {"on_days": {"type": "array", "items": {"enum": ["SUNDAY", "MONDAY"]}}}}]}}},
            {"properties": {"schedule": {"allOf": [{"properties": {"on_days": {"type": "array", "items": {"enum": ["SUN", "MON"]}}}}]}}},
        ),
        (
            {"kind": "not_required", "field": "v4_enabled"},
            {"type": "object", "properties": {"v4_enabled": {"type": "boolean"}}},
            {"type": "object", "required": ["v4_enabled"], "properties": {"v4_enabled": {"type": "boolean"}}},
        ),
        (
            {"kind": "absent", "field": "restrictions[].require_tls"},
            {"type": "object", "properties": {"restrictions": {"type": "array", "items": {"properties": {"read_only": {"type": "boolean"}}}}}},
            {"type": "object", "properties": {"restrictions": {"type": "array", "items": {"properties": {"require_tls": {"type": "boolean"}}}}}},
        ),
    ],
    ids=lambda value: value.get("kind", "") + ":" + value.get("field", "") if isinstance(value, dict) and "kind" in value else "",
)
def test_mismatch_holds_until_the_document_is_fixed(entry, schema, fixed):
    entry = {"id": "x", "method": "PUT", "path": "/v1/x", **entry}

    assert checks.mismatch_holds(document("/v1/x", "PUT", schema), entry)[0]
    holds, detail = checks.mismatch_holds(document("/v1/x", "PUT", fixed), entry)
    assert not holds
    assert detail


def test_mismatch_does_not_hold_when_the_route_is_gone():
    holds, detail = checks.mismatch_holds(document("/v1/y", "PUT", {}), {"id": "x", "method": "PUT", "path": "/v1/x", "field": "a", "kind": "absent"})

    assert not holds
    assert "PUT /v1/x is not in the document" in detail


def test_version_bounded_entry_applies_only_before_its_release():
    entry = {"id": "x", "before": "7.11.0"}

    assert checks.applies(entry, (7, 10, 3)) == (True, "")
    assert checks.applies(entry, (7, 11, 0))[0] is False
    assert "pass --server-version" in checks.applies(entry, None)[1]
    assert checks.applies({"id": "y"}, None) == (True, "")


OMITTED = {"id": "internal", "method": "POST", "path": "/v1/debug/thing", "kind": "omitted", "why": "w"}


def test_a_route_the_ledger_says_is_omitted_has_no_wire_field_problems():
    spec = document("/v1/other", "GET")

    assert checks.wire_field_problems(spec, "POST", "/v1/debug/thing", {"$body": "object"}, [OMITTED], None) == []
    assert checks.wire_field_problems(spec, "POST", "/v1/debug/thing", {"$body": "object"}, [], None) == ["POST /v1/debug/thing is not in the document"]


def test_an_omitted_route_holds_until_the_document_has_it():
    assert checks.mismatch_holds(document("/v1/other", "GET"), OMITTED) == (True, "the document now has POST /v1/debug/thing")
    assert checks.mismatch_holds(document("/v1/debug/thing", "POST"), OMITTED)[0] is False


def test_a_route_missing_from_the_document_says_why_an_omitted_entry_for_it_did_not_apply():
    spec = document("/v1/other", "GET")
    bounded = {**OMITTED, "before": "7.11.0"}

    assert checks.wire_field_problems(spec, "POST", "/v1/debug/thing", {"$body": "object"}, [bounded], None) == [
        "POST /v1/debug/thing is not in the document (internal applies before 7.11.0 and the server version is unknown; pass --server-version)"
    ]
    assert checks.omitted_reason([bounded], "POST", "/v1/debug/thing", (7, 10, 3)) == ""
    assert checks.omitted_reason([OMITTED], "GET", "/v1/debug/thing", None) == ""


NO_BODY = {"id": "raw", "method": "PUT", "path": "/v1/thing/{name}/policy", "kind": "no_body", "why": "w"}


def test_a_route_the_ledger_says_has_no_documented_body_has_no_wire_field_problems():
    spec = document("/v1/thing/{id}/policy", "PUT")

    assert checks.wire_field_problems(spec, "PUT", "/v1/thing/{name}/policy", {"Statement": "array"}, [NO_BODY], None) == []
    assert checks.wire_field_problems(spec, "PUT", "/v1/thing/{name}/policy", {"Statement": "array"}, [], None) == [
        "the document declares no JSON request body"
    ]


def test_a_route_without_a_documented_body_holds_until_the_document_declares_one():
    assert checks.mismatch_holds(document("/v1/thing/{id}/policy", "PUT"), NO_BODY) == (True, "PUT /v1/thing/{name}/policy now declares a JSON request body")
    assert checks.mismatch_holds(document("/v1/thing/{id}/policy", "PUT", {"type": "object"}), NO_BODY)[0] is False


def test_omitted_route_matches_method_path_and_release():
    assert checks.omitted_route([OMITTED], "POST", "/v1/debug/thing", None) == OMITTED
    assert checks.omitted_route([OMITTED], "GET", "/v1/debug/thing", None) is None
    assert checks.omitted_route([{**OMITTED, "before": "7.11.0"}], "POST", "/v1/debug/thing", (7, 11, 0)) is None
