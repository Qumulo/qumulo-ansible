# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import version
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import client_params


def openapi(*restriction_fields):
    """The part of /openapi.json the require_tls probe reads, with the restriction schema behind a $ref."""
    return {
        "paths": {
            "/v3/nfs/exports/": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "export_path": {"type": "string"},
                                        "restrictions": {"type": "array", "items": {"$ref": "#/components/schemas/restriction"}},
                                    },
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {"schemas": {"restriction": {"type": "object", "properties": {name: {"type": "boolean"} for name in restriction_fields}}}},
    }


@pytest.fixture(autouse=True)
def cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


def connect():
    return QumuloClient(client_params(api_token="t"))


@pytest.mark.parametrize(("revision", "release"), [("7.10.3", (7, 10, 3)), ("7.2.3.2", (7, 2, 3, 2)), ("7.10.3A", (7, 10, 3))])
def test_parses_revision_id(fake_api, revision, release):
    fake = fake_api(version=revision)
    qumulo = connect()

    found = version.cluster_version(qumulo)

    assert (found.release, found.build_id, str(found)) == (release, f"build-{revision}", revision)
    assert version.cluster_version(qumulo) is found
    assert [(e.path, "Authorization" in e.headers) for e in fake.log] == [("/v1/version", False)]


@pytest.mark.parametrize(
    ("revision", "fields", "supported"),
    [("7.10.3", ("read_only", "require_tls"), True), ("7.11.0", ("read_only",), False)],
)
def test_probe_beats_version(fake_api, revision, fields, supported):
    fake_api(version=revision).expect("GET", "/openapi.json", response=openapi(*fields))

    assert version.supports(connect(), "nfs_export.require_tls") is supported


@pytest.mark.parametrize(("revision", "respin"), [("7.10.3", ""), ("7.10.3A", "A")])
def test_keeps_the_respin_letter(fake_api, revision, respin):
    fake_api(version=revision)

    assert version.cluster_version(connect()).respin == respin


def test_respin_orders_after_its_release_and_before_the_next():
    base, respin, nxt = (version.ClusterVersion(release, "b", respin=letter) for release, letter in [((7, 10, 3), ""), ((7, 10, 3), "A"), ((7, 10, 4), "")])

    assert base < respin < nxt


def test_respin_passes_the_gate_of_its_release(fake_api, monkeypatch):
    monkeypatch.setattr(version, "GATES", {"since_7_10_3": version.Gate((7, 10, 3)), "since_7_10_4": version.Gate((7, 10, 4))})
    fake_api(version="7.10.3A")
    qumulo = connect()

    assert version.supports(qumulo, "since_7_10_3") is True
    with pytest.raises(QumuloError, match=r"^option requires Qumulo Core 7\.10\.4 or later; qumulo\.example\.com runs 7\.10\.3A$"):
        version.require(qumulo, "since_7_10_4", "option")


def test_version_decides_gates_without_probe(fake_api):
    fake_api(version="7.10.3")
    assert version.supports(connect(), "portal.suspending") is False
    fake_api(version="7.11.0")
    assert version.supports(connect(), "portal.suspending") is True


def test_capabilities_cached_per_build_id(fake_api, cache_home):
    fake_api(version="7.11.0").expect("GET", "/openapi.json", response=openapi("require_tls"))
    first = connect()
    assert version.supports(first, "nfs_export.require_tls") is True
    assert version.supports(first, "nfs_export.require_tls") is True
    assert version.supports(connect(), "nfs_export.require_tls") is True  # a later module run reads the file

    cached = json.loads((cache_home / "qumulo.cluster" / "capabilities" / "build-7.11.0.json").read_text())
    assert cached == {"nfs_export.require_tls": True}

    fake_api(version="7.11.0", build_id="build-7.11.0-hotfix").expect("GET", "/openapi.json", response=openapi())
    assert version.supports(connect(), "nfs_export.require_tls") is False


def test_require_message_names_option_and_minimum(fake_api):
    fake_api(version="7.10.3").expect("GET", "/openapi.json", response=openapi("read_only"))
    qumulo = connect()

    with pytest.raises(QumuloError, match=r"^restrictions\[\]\.require_tls requires Qumulo Core 7\.11\.0 or later; qumulo\.example\.com runs 7\.10\.3$"):
        version.require(qumulo, "nfs_export.require_tls", "restrictions[].require_tls")


def test_require_says_the_api_lacks_the_option_when_a_new_enough_cluster_fails_the_probe(fake_api):
    fake_api(version="7.11.0").expect("GET", "/openapi.json", response=openapi("read_only"))

    with pytest.raises(QumuloError, match=r"^this cluster's API lacks restrictions\[\]\.require_tls; qumulo\.example\.com runs 7\.11\.0$"):
        version.require(connect(), "nfs_export.require_tls", "restrictions[].require_tls")


@pytest.mark.parametrize("keyword", ["allOf", "oneOf", "anyOf"])
def test_probe_follows_schema_composition(fake_api, keyword):
    spec = openapi("read_only")
    restriction = spec["components"]["schemas"]["restriction"]
    spec["components"]["schemas"]["restriction"] = {keyword: [restriction, {"type": "object", "properties": {"require_tls": {"type": "boolean"}}}]}
    fake_api(version="7.10.3").expect("GET", "/openapi.json", response=spec)

    assert version.supports(connect(), "nfs_export.require_tls") is True


def test_probe_route_absent_from_the_spec_is_unsupported(fake_api):
    fake_api(version="7.11.0").expect("GET", "/openapi.json", response={"paths": {}})

    assert version.supports(connect(), "nfs_export.require_tls") is False


@pytest.mark.parametrize("revision_id", ["Qumulo Core development", "Qumulo Core 7.10.3AB"])
def test_unparseable_revision_id_fails(fake_api, revision_id):
    fake_api().version["revision_id"] = revision_id

    with pytest.raises(QumuloError, match=f"cannot parse the Qumulo Core version from revision_id '{revision_id}'"):
        version.cluster_version(connect())


def test_cache_file_without_the_gate_is_probed_again_once(fake_api, cache_home):
    cache = cache_home / "qumulo.cluster" / "capabilities"
    cache.mkdir(parents=True)
    (cache / "build-7.11.0.json").write_text("{}")
    fake_api(version="7.11.0").expect("GET", "/openapi.json", response=openapi("require_tls"))

    assert version.supports(connect(), "nfs_export.require_tls") is True
    assert version.supports(connect(), "nfs_export.require_tls") is True  # read from the rewritten file
    assert json.loads((cache / "build-7.11.0.json").read_text()) == {"nfs_export.require_tls": True}


def test_unwritable_cache_directory_still_answers(fake_api, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    fake_api(version="7.11.0").expect("GET", "/openapi.json", response=openapi("require_tls"))

    assert version.supports(connect(), "nfs_export.require_tls") is True


def test_failed_cache_write_leaves_no_temporary_file(fake_api, cache_home):
    cache = cache_home / "qumulo.cluster" / "capabilities"
    (cache / "build-7.11.0.json").mkdir(parents=True)  # a directory where the file should go: the final rename fails
    fake_api(version="7.11.0").expect("GET", "/openapi.json", response=openapi("require_tls"))

    assert version.supports(connect(), "nfs_export.require_tls") is True
    assert sorted(path.name for path in cache.iterdir()) == ["build-7.11.0.json"]


NO_DOCUMENT_ANSWERS = {
    "not_found": (404, {"error_class": "http_not_found_error", "description": "not found"}),
    "server_error": (500, {"error_class": "http_internal_server_error_error", "description": "Failed to retrieve OpenAPI spec"}),
}


@pytest.mark.parametrize(("revision", "supported"), [("7.10.3", False), ("7.11.0", True)])
@pytest.mark.parametrize("answer", NO_DOCUMENT_ANSWERS, ids=list(NO_DOCUMENT_ANSWERS))
def test_cluster_without_an_openapi_document_falls_back_to_the_version(fake_api, cache_home, revision, supported, answer):
    """Some builds, such as the tier 1 container image of 7.10.3A, serve no /openapi.json and answer 404 or 500; the gate's release decides."""
    status, body = NO_DOCUMENT_ANSWERS[answer]
    fake_api(version=revision).expect("GET", "/openapi.json", status=status, response=body)
    qumulo = connect()

    assert version.supports(qumulo, "nfs_export.require_tls") is supported
    assert qumulo.warnings == [f"this cluster serves no /openapi.json, so nfs_export.require_tls is decided by its release, {revision}"]


def test_build_without_an_openapi_document_is_asked_once(fake_api, cache_home):
    status, body = NO_DOCUMENT_ANSWERS["server_error"]
    fake_api(version="7.10.3").expect("GET", "/openapi.json", status=status, response=body)

    assert version.supports(connect(), "nfs_export.require_tls") is False
    assert version.supports(connect(), "nfs_export.require_tls") is False  # a later module run reads the file
    assert json.loads((cache_home / "qumulo.cluster" / "capabilities" / "build-7.10.3.json").read_text()) == {"no_openapi_document": True}


def test_require_without_an_openapi_document_names_the_minimum_version(fake_api):
    fake_api(version="7.10.3").expect("GET", "/openapi.json", status=404, response={"error_class": "http_not_found_error", "description": "not found"})

    with pytest.raises(QumuloError, match=r"^restrictions\[\]\.require_tls requires Qumulo Core 7\.11\.0 or later; qumulo\.example\.com runs 7\.10\.3$"):
        version.require(connect(), "nfs_export.require_tls", "restrictions[].require_tls")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (403, {"error_class": "http_forbidden_error", "description": "no"}),
        (500, {"error_class": "http_internal_server_error_error", "description": "out of memory"}),
    ],
    ids=["forbidden", "server_fault"],
)
def test_other_failure_to_read_the_openapi_document_fails(fake_api, cache_home, status, body):
    fake_api(version="7.11.0").expect("GET", "/openapi.json", status=status, response=body)

    with pytest.raises(QumuloError, match=rf"^GET /openapi\.json failed: HTTP {status}"):
        version.supports(connect(), "nfs_export.require_tls")
    assert list(cache_home.rglob("*.json")) == []


@pytest.mark.parametrize(
    ("text", "release", "respin"),
    [
        ("7.10.3", (7, 10, 3), ""),
        ("7.10.0.3", (7, 10, 0, 3), ""),
        ("7.10.3A", (7, 10, 3), "A"),
        ("7.9.0.5A", (7, 9, 0, 5), "A"),
        (" 7.11.0 ", (7, 11, 0), ""),
    ],
)
def test_parses_a_version_string_with_four_parts_and_a_letter(text, release, respin):
    parsed = version.ClusterVersion.parse(text)

    assert (parsed.release, parsed.respin, parsed.build_id) == (release, respin, "")


@pytest.mark.parametrize("text", ["7.11", "7", "7.10.x", "v7.10.3", "7.10.3ab", "", "7.10.3-rc1"])
def test_a_version_string_without_three_numbers_is_refused(text):
    with pytest.raises(QumuloError, match="not a Qumulo Core version"):
        version.ClusterVersion.parse(text)


@pytest.mark.parametrize(("text", "quarter"), [("7.10.3", (7, 10)), ("7.10.0.3", (7, 10)), ("7.9.0.5A", (7, 9)), ("8.0.0", (8, 0))])
def test_quarter_is_the_major_and_minor(text, quarter):
    assert version.ClusterVersion.parse(text).quarter == quarter


def test_a_dot_release_orders_after_a_respin_of_its_release():
    shuffled = [version.ClusterVersion.parse(text) for text in ("7.10.4", "7.10.3.1", "7.10.3", "7.10.3A")]

    assert [str(v) for v in sorted(shuffled)] == ["7.10.3", "7.10.3A", "7.10.3.1", "7.10.4"]
