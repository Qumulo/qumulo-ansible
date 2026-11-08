# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Where the contract tests get their document (conftest.py), and that a failing test never prints it."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import conftest  # tests/contract/conftest.py
import openapi_checks as checks
import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import client as client_module

HERE = Path(__file__).resolve().parent
CONTRACT_TESTS = HERE / "test_openapi_contract.py"
CONNECTION_VARIABLES = [option["fallback"][1][0] for option in conftest.connection_argument_spec().values()]
# Text that only the document holds; none of it may reach a test run's output.
CANARIES = ("/v9/canary-only-in-the-document", "CanaryOnlyInTheDocument", "canary description only in the document")
REQUIRED_CANARY = "canary_required_only_in_the_document"
TYPE_CANARY = "canary-type-only-in-the-document"
REF_CANARY = "CanaryRefTargetOnlyInTheDocument"
PARAMETER_CANARY = "{canary_parameter_only_in_the_document}"
ALL_CANARIES = (*CANARIES, REQUIRED_CANARY, TYPE_CANARY, REF_CANARY, PARAMETER_CANARY.strip("{}"))
# The release the runs below name with --server-version.
SERVER_VERSION = "7.10.3"
# A route only GET reaches, so that removing it fails one test.
REMOVED = "cluster.NODES"
# A route with a path parameter, which a document can name as it likes.
PARAMETERIZED = "cluster.FILE_ATTRIBUTES"


def _set(schema, field, json_type):
    """Add the dotted wire field to an object schema, or make the schema the bare body's type."""
    if field == checks.BODY:
        schema.clear()
        schema["type"] = json_type
        return
    *parents, leaf = field.split(".")
    for part in parents:
        child = schema["properties"].setdefault(part.removesuffix("[]"), {"type": "object", "properties": {}})
        if part.endswith("[]"):
            child.update(type="array", items=child.get("items") or {"type": "object", "properties": {}})
            child = child["items"]
        schema = child
    schema["properties"][leaf.removesuffix("[]")] = {"type": json_type}


def _mismatching(schema, entry):
    """Make the request schema disagree with the server as the ledger entry says."""
    *parents, leaf = entry["field"].split(".")
    if entry["kind"] == "absent":
        _parent(schema, parents)["properties"].pop(leaf, None)
    elif entry["kind"] == "type":
        _set(schema, entry["field"], "integer" if entry["sent"] == "string" else "string")
    elif entry["kind"] == "enum":
        _set(schema, entry["field"], "array")
        _locate_leaf(schema, parents, leaf)["items"] = {"type": "string", "enum": ["SUNDAY"]}
    else:  # not_required: the built document lists no field as required, so the field need only be there, as sent
        sent = _parent(schema, parents)["properties"].get(leaf.removesuffix("[]"), {}).get("type", "boolean")
        _set(schema, entry["field"], sent)


def _locate_leaf(schema, parents, leaf):
    return _parent(schema, parents)["properties"][leaf]


def _parent(schema, parents):
    for part in parents:
        schema = schema["properties"][part.removesuffix("[]")]
        schema = schema.get("items", schema)
    return schema


def collection_document(server_version=None):
    """A document that every contract test passes against, as a server of that release serves it, holding the canaries.

    Without a release, the ledger entries bounded by one are left out, as a document from a later release would leave them.
    """
    release = tuple(int(part) for part in server_version.split(".")) if server_version else None
    operations = {}

    def operation(method, path):
        return operations.setdefault(path, {}).setdefault(method.lower(), {"responses": {}, "description": CANARIES[2]})

    def request(method, path):
        body = operation(method, path).setdefault("requestBody", {"content": {"application/json": {"schema": {"type": "object", "properties": {}}}}})
        return body["content"]["application/json"]["schema"]

    routes = checks.collect_routes()
    for route in routes.values():
        for method in route.methods:
            operation(method, route.path)
    for entry in checks.load_wire_fields():
        for field, json_type in entry["fields"].items():
            _set(request(entry["method"], routes[entry["route"]].path), field, json_type)
    for entry in checks.load_ledger():
        if not checks.applies(entry, release)[0]:
            continue
        if entry["kind"] == "omitted":
            methods = operations.get(entry["path"], {})
            methods.pop(entry["method"].lower(), None)
            if not methods:
                operations.pop(entry["path"], None)
        elif entry["kind"] == "no_body":
            operation(entry["method"], entry["path"]).pop("requestBody", None)
        else:
            _mismatching(request(entry["method"], entry["path"]), entry)
    canary = {CANARIES[0]: {"get": {"responses": {}, "description": CANARIES[2]}}}
    return {
        "paths": {**canary, **operations},
        "openapi": "3.0.3",
        "components": {"schemas": {CANARIES[1]: {"type": "object", "description": CANARIES[2]}}},
    }


def run_contract_tests(tmp_path, text, *args):
    spec_file = tmp_path / "openapi.json"
    if text is not None:
        spec_file.write_text(text, encoding="utf-8")
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in CONNECTION_VARIABLES and name not in {conftest.REQUIRE_CLUSTER, checks.SHOW_DOCUMENT_NAMES}
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(CONTRACT_TESTS), "-p", "no:cacheprovider", "-rA", "--tb=long", "-l", "-vv", "--spec-file", str(spec_file), *args],
        cwd=conftest.COLLECTION,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _request_schema(document, symbol, method):
    return document["paths"][checks.collect_routes()[symbol].path][method]["requestBody"]["content"]["application/json"]["schema"]


def without_a_route(document):
    del document["paths"][checks.collect_routes()[REMOVED].path]
    return json.dumps(document)


def with_a_required_canary(document):
    _request_schema(document, "client.LOGIN", "post")["required"] = [REQUIRED_CANARY]
    return json.dumps(document)


def with_a_type_canary(document):
    _request_schema(document, "client.LOGIN", "post")["properties"]["username"]["type"] = TYPE_CANARY
    return json.dumps(document)


def with_broken_references(document):
    """Every request body names a schema that does not exist, and the parameterized route's path item is not an object."""
    for operations in document["paths"].values():
        for operation in operations.values():
            if "requestBody" in operation:
                operation["requestBody"]["content"]["application/json"]["schema"] = {"$ref": f"#/components/schemas/{REF_CANARY}"}
    path = checks.collect_routes()[PARAMETERIZED].path
    del document["paths"][path]
    document["paths"][re.sub(r"\{[^}]*\}", PARAMETER_CANARY, path)] = 0
    return json.dumps(document)


def truncated(document):
    return json.dumps(document)[:-5]


def missing(document):
    return None


ROUTE_FAILED = [f"test_openapi_contract.py::test_every_route_exists[{REMOVED}-GET]"]
LOGIN_FIELDS_FAILED = ["test_openapi_contract.py::test_wire_fields_match_the_request_schema[POST client.LOGIN]"]
DOCUMENT_ERRORS = "every failure is a DocumentError"
USAGE_ERROR = "the run stops with one usage error"


@pytest.mark.parametrize(
    ("change", "returncode", "failed"),
    [
        (without_a_route, 1, ROUTE_FAILED),
        (with_a_required_canary, 1, LOGIN_FIELDS_FAILED),
        (with_a_type_canary, 1, LOGIN_FIELDS_FAILED),
        (with_broken_references, 1, DOCUMENT_ERRORS),
        (truncated, 4, USAGE_ERROR),
        (missing, 4, USAGE_ERROR),
    ],
    ids=["absent route", "required property", "type", "broken $ref", "truncated file", "missing file"],
)
def test_a_failing_run_never_prints_the_document(tmp_path, change, returncode, failed):
    result = run_contract_tests(tmp_path, change(collection_document(SERVER_VERSION)), "--server-version", SERVER_VERSION)
    output = result.stdout + result.stderr
    failures = re.findall(r"^FAILED \S*?(test_openapi_contract\.py::.+?\])(?: - (.*))?$", output, re.MULTILINE)

    assert result.returncode == returncode, output
    if failed == DOCUMENT_ERRORS:
        assert failures, output
        assert [line for line in failures if "DocumentError" not in line[1]] == [], output
    elif failed == USAGE_ERROR:
        assert failures == [], output
        assert result.stderr.strip().splitlines() == [f"ERROR: --spec-file {tmp_path / 'openapi.json'} is not a readable JSON file"], output
    else:
        assert [test for test, unused in failures] == failed, output
    if failures:
        assert "openapi = <openapi document:" in output, "the long traceback shows the fixture, by its repr"
    assert [canary for canary in ALL_CANARIES if canary in output] == []


def test_a_run_with_a_server_version_runs_the_version_bounded_entries(tmp_path):
    result = run_contract_tests(tmp_path, json.dumps(collection_document(SERVER_VERSION)), "--server-version", SERVER_VERSION)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert re.search(r"^PASSED \S+::test_known_mismatches_still_mismatch\[nfs-export-require-tls\]", output, re.MULTILINE), output


def test_a_document_without_a_server_version_skips_the_version_bounded_entries(tmp_path):
    result = run_contract_tests(tmp_path, json.dumps(collection_document()))
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert re.search(r"^SKIPPED .*nfs-export-require-tls applies before 7\.11\.0 .* pass --server-version$", output, re.MULTILINE), output


@pytest.fixture
def no_connection_variables(monkeypatch, tmp_path):
    for name in [*CONNECTION_VARIABLES, conftest.REQUIRE_CLUSTER]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(conftest, "INTEGRATION_CONFIG", tmp_path / "integration_config.yml")
    return conftest.INTEGRATION_CONFIG


def test_connection_params_read_the_integration_config_with_its_types(no_connection_variables):
    template = conftest.COLLECTION / "tests" / "integration" / "integration_config.yml.template"
    if not template.is_file():
        pytest.skip("the built collection leaves out tests/integration")
    no_connection_variables.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    params = conftest.connection_params()

    assert params["api_host"] == "localhost"
    assert params["api_port"] == 8000
    assert params["validate_certs"] is True
    assert params["api_password"] == "CHANGE-ME"
    assert params["expected_cluster_uuid"] == "00000000-0000-0000-0000-000000000000", "read from qumulo_cluster_uuid"
    assert params["request_timeout"] == 60, "an option the config leaves out takes its default"


def test_connection_params_take_the_environment_over_the_config(no_connection_variables, monkeypatch):
    no_connection_variables.write_text("qumulo_api_host: config-host\nqumulo_api_port: 8000\nqumulo_api_password: from-config\n", encoding="utf-8")
    monkeypatch.setenv("QUMULO_API_HOST", "env-host")
    monkeypatch.setenv("QUMULO_API_PORT", "18000")
    monkeypatch.setenv("QUMULO_VALIDATE_CERTS", "no")
    monkeypatch.setenv("QUMULO_EXPECTED_CLUSTER_UUID", "env-uuid")

    params = conftest.connection_params()

    assert (params["api_host"], params["api_port"], params["validate_certs"], params["api_password"]) == ("env-host", 18000, False, "from-config")
    assert params["expected_cluster_uuid"] == "env-uuid"


@pytest.mark.parametrize(
    "config",
    ["qumulo_api_host: config-host\n", "qumulo_api_password: secret\n", ""],
    ids=["no credentials", "no host", "nothing"],
)
def test_connection_params_are_none_without_a_host_and_credentials(no_connection_variables, config):
    no_connection_variables.write_text(config, encoding="utf-8")

    assert conftest.connection_params() is None


def test_connection_params_accept_a_token_for_credentials(no_connection_variables, monkeypatch):
    monkeypatch.setenv("QUMULO_API_HOST", "env-host")
    monkeypatch.setenv("QUMULO_API_TOKEN", "a-token")

    assert conftest.connection_params()["api_token"] == "a-token"


class NodeState:
    base = "https://cluster:8000"

    def __init__(self, cluster_id):
        self.cluster_id = cluster_id

    def node_state(self):
        return self.cluster_id, "ACTIVE"


def test_check_cluster_accepts_the_expected_cluster_in_any_case():
    conftest.check_cluster(NodeState("ABC-123"), "abc-123")
    conftest.check_cluster(NodeState("any"), None)


def test_check_cluster_fails_on_another_cluster():
    with pytest.raises(pytest.fail.Exception, match="is cluster 'other' \\(state ACTIVE\\), expected 'abc-123'"):
        conftest.check_cluster(NodeState("other"), "abc-123")


def session(spec_file=None):
    return SimpleNamespace(config=SimpleNamespace(getoption=lambda name: spec_file if name == "--spec-file" else None, stash={}))


def test_a_required_cluster_that_is_missing_is_an_error(no_connection_variables, monkeypatch, tmp_path):
    monkeypatch.setenv(conftest.REQUIRE_CLUSTER, "1")
    spec_file = tmp_path / "openapi.json"
    spec_file.write_text("{}", encoding="utf-8")

    with pytest.raises(pytest.UsageError, match="QUMULO_CONTRACT_REQUIRE_CLUSTER=1, but there is no cluster to check against"):
        conftest.pytest_sessionstart(session())
    conftest.pytest_sessionstart(session(spec_file))


def test_a_missing_cluster_is_not_an_error_unless_required(no_connection_variables):
    conftest.pytest_sessionstart(session())


class FakeClient(NodeState):
    def __init__(self, params):
        super().__init__("live-uuid")
        self.params = params


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(conftest, "QumuloClient", FakeClient)


PARAMS = {"api_host": "env-host", "expected_cluster_uuid": "live-uuid"}


def test_client_for_checks_the_cluster(fake_client):
    assert conftest.client_for(PARAMS, None).params == PARAMS
    with pytest.raises(pytest.fail.Exception, match="expected 'other-uuid'"):
        conftest.client_for({**PARAMS, "expected_cluster_uuid": "other-uuid"}, None)


def test_client_for_gives_no_client_with_a_spec_file_or_without_a_cluster(fake_client):
    assert conftest.client_for(PARAMS, Path("openapi.json")) is None
    assert conftest.client_for(None, None) is None


class FailingFetch:
    base = "https://cluster:8000"

    def get(self, route, auth):
        body = CANARIES[2]  # the HTTP client's frames hold the body read so far
        raise ConnectionError(f"incomplete read after {body}")


def test_a_failed_fetch_does_not_carry_the_body():
    with pytest.raises(conftest.DocumentFetchError) as raised:
        conftest.fetch_document(FailingFetch())

    assert str(raised.value) == "could not read /openapi.json from https://cluster:8000 (ConnectionError)"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


class FakeTransport:
    """Answers each path with a status, and an error body that only the server holds."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, method, url, data, headers):
        path = url.removeprefix("https://cluster:8000")
        self.requests.append(f"{method} {path}")
        status = self.answers[path]
        body = {"error_class": "canary_error_class_only_on_the_server", "description": CANARIES[2]} if status >= 300 else {"paths": {}}
        return status, {}, json.dumps(body).encode()


@pytest.fixture
def cluster_answering(monkeypatch, no_connection_variables):
    """A client of a cluster whose transport answers /openapi.json and /v1/openapi.json with the given statuses."""
    transports = []

    def client(root_status, v1_status):
        transports.append(FakeTransport({"/openapi.json": root_status, "/v1/openapi.json": v1_status}))
        monkeypatch.setattr(client_module, "UrlsTransport", lambda params: transports[-1])
        params = {name: option.get("default") for name, option in conftest.connection_argument_spec().items()}
        return conftest.client_for({**params, "api_host": "cluster", "api_password": "secret"}, None)

    client.transports = transports
    return client


@pytest.mark.parametrize(("root_status", "v1_status"), [(500, 500), (404, 404), (500, 404)], ids=["500", "404", "500 then 404"])
def test_a_cluster_serving_no_document_skips_the_live_tests(cluster_answering, root_status, v1_status, monkeypatch):
    config = session().config
    monkeypatch.setenv("QUMULO_API_HOST", "cluster")
    monkeypatch.setenv("QUMULO_API_PASSWORD", "secret")

    with pytest.raises(pytest.skip.Exception, match=f"^{conftest.NO_DOCUMENT}$"):
        conftest.document_from(cluster_answering(root_status, v1_status), config)

    assert cluster_answering.transports[-1].requests == ["GET /openapi.json", "GET /v1/openapi.json"]
    assert summary_lines(None, config) == [f"skipped: {conftest.NO_DOCUMENT}"]


def test_a_document_on_either_path_is_used(cluster_answering):
    assert conftest.document_from(cluster_answering(404, 200), session().config) == {"paths": {}}
    assert conftest.document_from(cluster_answering(200, 404), session().config) == {"paths": {}}


def test_a_required_document_that_the_cluster_does_not_serve_fails(cluster_answering, monkeypatch):
    monkeypatch.setenv(conftest.REQUIRE_DOCUMENT, "1")

    with pytest.raises(pytest.fail.Exception) as raised:
        conftest.document_from(cluster_answering(500, 404), session().config)

    assert str(raised.value) == f"{conftest.REQUIRE_DOCUMENT}=1, but {conftest.NO_DOCUMENT}"


def test_another_error_status_is_a_fetch_error_without_the_body(cluster_answering):
    with pytest.raises(conftest.DocumentFetchError) as raised:
        conftest.document_from(cluster_answering(403, 404), session().config)

    assert str(raised.value) == "could not read /openapi.json from https://cluster:8000 (HTTP 403)"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def summary_lines(spec_file, config=None):
    lines = []
    conftest.pytest_terminal_summary(SimpleNamespace(write_line=lines.append), config or session(spec_file).config)
    return lines


def test_a_run_without_a_cluster_ends_by_saying_so(no_connection_variables):
    assert summary_lines(None) == [f"skipped: {conftest.NO_CLUSTER}"]
    assert summary_lines(Path("openapi.json")) == []


def test_the_document_shows_only_its_size():
    document = conftest.OpenAPIDocument(collection_document())

    assert repr(document) == str(document) == f"<openapi document: {len(document['paths'])} paths>"
    assert repr([document]) == f"[{document!r}]"
