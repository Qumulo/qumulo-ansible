# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Where the contract tests get the OpenAPI document: a live cluster, or a file named with --spec-file.

The cluster is the one the connection environment variables name (QUMULO_API_HOST and the rest, as
for the modules), or else the one tests/integration/integration_config.yml describes, which
`task edge:up` writes. /openapi.json needs no login, but a cluster is used only when credentials are
set, as for an integration run. The document is held in memory and never written anywhere, and its
repr names no part of it, so that a failing test's traceback cannot print it.

Without a cluster or --spec-file the tests that need the document are skipped, unless
QUMULO_CONTRACT_REQUIRE_CLUSTER=1, as in the integration workflow, makes that an error.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

COLLECTION = Path(__file__).resolve().parents[2]
# The directory holding ansible_collections/, so that the collection imports by its full name.
sys.path.insert(0, str(COLLECTION.parents[2]))

from ansible.module_utils.parsing.convert_bool import boolean
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloClient, Route, connection_argument_spec
from ansible_collections.qumulo.cluster.plugins.module_utils.version import OPENAPI, cluster_version

INTEGRATION_CONFIG = COLLECTION / "tests" / "integration" / "integration_config.yml"
NO_CLUSTER = (
    "no cluster to check against: set QUMULO_API_HOST, QUMULO_API_PORT and QUMULO_API_PASSWORD or QUMULO_API_TOKEN, run task edge:up, or pass --spec-file"
)
REQUIRE_CLUSTER = "QUMULO_CONTRACT_REQUIRE_CLUSTER"
NO_DOCUMENT = "the cluster serves no OpenAPI document"
REQUIRE_DOCUMENT = "QUMULO_CONTRACT_REQUIRE_DOCUMENT"
# Where a cluster may serve the document. The Qumulo Core 7.10.3 docker image answers 500 and 404 on these.
DOCUMENT_ROUTES = (OPENAPI, Route("/v1/openapi.json", ("GET",)))
NO_DOCUMENT_STATUSES = frozenset({404, 500})
# integration_config.yml keys that are not qumulo_<option>; ci/qumulo-edge.sh writes these.
CONFIG_KEYS = {"expected_cluster_uuid": "qumulo_cluster_uuid"}


class OpenAPIDocument(dict):
    """The document, as a dict whose repr and str show only its size."""

    def __repr__(self):
        return f"<openapi document: {len(self.get('paths') or {})} paths>"

    __str__ = __repr__


def pytest_addoption(parser):
    group = parser.getgroup("qumulo contract")
    group.addoption("--spec-file", type=Path, help="check against this OpenAPI document instead of a live cluster")
    group.addoption("--server-version", help="with --spec-file, the Qumulo Core release the document is from, such as 7.10.3")


def connection_params():
    """The connection options from the environment, else from integration_config.yml; None without a host and credentials."""
    spec = connection_argument_spec()
    config = {}
    if INTEGRATION_CONFIG.is_file():
        config = yaml.safe_load(INTEGRATION_CONFIG.read_text(encoding="utf-8")) or {}
    params = {}
    for name, option in spec.items():
        value = os.environ.get(option["fallback"][1][0])
        if value is None:
            value = config.get(CONFIG_KEYS.get(name, f"qumulo_{name}"), option.get("default"))
        if value is not None and option["type"] == "int":
            value = int(value)
        elif value is not None and option["type"] == "bool":
            value = boolean(value)
        params[name] = value
    if not params["api_host"] or not (params["api_password"] or params["api_token"]):
        return None
    return params


SPEC_DOCUMENT = pytest.StashKey[OpenAPIDocument]()
SERVED_NO_DOCUMENT = pytest.StashKey[bool]()


def pytest_sessionstart(session):
    spec_file = session.config.getoption("--spec-file")
    if spec_file:
        session.config.stash[SPEC_DOCUMENT] = load_spec_file(spec_file)
    elif os.environ.get(REQUIRE_CLUSTER) == "1" and connection_params() is None:
        raise pytest.UsageError(f"{REQUIRE_CLUSTER}=1, but there is {NO_CLUSTER}")


def load_spec_file(path):
    """The document in the file; a file that is not JSON is a usage error that, unlike json's own, does not quote it."""
    try:
        return OpenAPIDocument(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # Suppressed: json.decoder's frames hold the file's text.
        raise pytest.UsageError(f"--spec-file {path} is not a readable JSON file") from None


def pytest_terminal_summary(terminalreporter, config):
    if not config.getoption("--spec-file") and connection_params() is None:
        terminalreporter.write_line(f"skipped: {NO_CLUSTER}")
    if config.stash.get(SERVED_NO_DOCUMENT, False):
        terminalreporter.write_line(f"skipped: {NO_DOCUMENT}")


def check_cluster(client, expected_uuid):
    """Fail unless the cluster the client reaches is the expected one; the contract tests never log in, so the client's own guard never runs."""
    if not expected_uuid:
        return
    live_uuid, state = client.node_state()
    if live_uuid.lower() != expected_uuid.lower():
        pytest.fail(f"{client.base} is cluster '{live_uuid or 'UNCONFIGURED'}' (state {state}), expected '{expected_uuid}'")


class DocumentFetchError(Exception):
    pass


def fetch_document(client):
    """The cluster's document, or None when every route that could serve it answers 404 or 500.

    Any other failure raises an error that, unlike the HTTP client's exception and frames, holds none of the body.
    """
    for route in DOCUMENT_ROUTES:
        document, failure = _read_document(client, route)
        if document is not None:
            return document
        if failure not in NO_DOCUMENT_STATUSES:
            raise DocumentFetchError(f"could not read {route.path} from {client.base} ({failure})") from None
    return None


def _read_document(client, route):
    """(the document, None), else (None, the HTTP status, or the exception's class name), so that no exception outlives this call."""
    try:
        return OpenAPIDocument(client.get(route, auth=False).data), None
    except QumuloApiError:
        status = sys.exc_info()[1].status
        return None, status if status in NO_DOCUMENT_STATUSES else f"HTTP {status}"
    except Exception:  # noqa: BLE001 - any other failure to read the document is reported the same way
        return None, sys.exc_info()[0].__name__


def document_from(client, config):
    """The cluster's document; skips, or with QUMULO_CONTRACT_REQUIRE_DOCUMENT=1 fails, when the cluster serves none."""
    document = fetch_document(client)
    if document is not None:
        return document
    if os.environ.get(REQUIRE_DOCUMENT) == "1":
        pytest.fail(f"{REQUIRE_DOCUMENT}=1, but {NO_DOCUMENT}", pytrace=False)
    config.stash[SERVED_NO_DOCUMENT] = True
    pytest.skip(NO_DOCUMENT)


def client_for(params, spec_file):
    """The client for the cluster params name, checked to be the expected one; None with a spec file or without params."""
    if spec_file or not params:
        return None
    client = QumuloClient(params)
    check_cluster(client, params["expected_cluster_uuid"])
    return client


@pytest.fixture(scope="session")
def live_client(request):
    return client_for(connection_params(), request.config.getoption("--spec-file"))


@pytest.fixture(scope="session")
def openapi(request, live_client):
    if request.config.getoption("--spec-file"):
        return request.config.stash[SPEC_DOCUMENT]
    if live_client is None:
        pytest.skip(NO_CLUSTER)
    return document_from(live_client, request.config)


@pytest.fixture(scope="session")
def server_version(request, live_client):
    """The release as a tuple such as (7, 10, 3), or None when it is unknown."""
    if request.config.getoption("--spec-file"):
        given = request.config.getoption("--server-version")
        return tuple(int(part) for part in given.split(".")) if given else None
    if live_client is None:
        pytest.skip(NO_CLUSTER)
    return cluster_version(live_client).release
