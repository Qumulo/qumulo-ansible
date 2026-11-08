# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Version and capability gates.

A gate passes from the cluster's release number, unless it names a probe: then the probe decides,
because development builds can report a release number that does not match their API. Probes read
/openapi.json (about 4 MB) once per build_id and cache the answers on the host running the module.
On a build that serves no /openapi.json, a probed gate falls back to the release, with a warning.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloApiError, QumuloError, Route

VERSION = Route("/v1/version", ("GET",))  # needs no authentication
OPENAPI = Route("/openapi.json", ("GET",))  # needs no authentication

# How a build without the document answers GET /openapi.json: 404, or, on the 7.10.3A container image,
# 500 with this description. Any other 500 is a fault, which fails the gate rather than skip its probe.
NO_DOCUMENT_DESCRIPTION = "Failed to retrieve OpenAPI spec"
# The cached answers of a build that serves no document.
NO_DOCUMENT = "no_openapi_document"

# The oldest Qumulo Core release the integration tests run against; cluster_guard warns below it.
TESTED_FLOOR = (7, 10, 3)


@dataclass(frozen=True)
class Gate:
    since: tuple[int, ...]
    probe: tuple[str, str, str] | None = None  # (method, path, request property such as "restrictions[].require_tls")
    why: str = ""


GATES = {
    "nfs_export.require_tls": Gate(
        (7, 11, 0),
        probe=("post", "/v3/nfs/exports/", "restrictions[].require_tls"),
        why="absent from 7.10.3, which drops unknown restriction fields without an error",
    ),
    "portal.suspending": Gate((7, 11, 0), why="7.10.3 has no SUSPENDING portal state"),
    # By release, with no probe: the waiver is a query parameter, which the probe's request bodies cannot show.
    "upgrade.compatibility_overrides": Gate(
        (7, 11, 0),
        why=(
            "7.11.0 takes compatibility_overrides, a list of named rules; 7.10.3 takes override_compatibility_check, a boolean "
            "that lifts every rule, and drops the named rules without an error"
        ),
    ),
}


@dataclass(frozen=True, order=True)
class ClusterVersion:
    """Orders by release, then respin: 7.10.3 < 7.10.3A < 7.10.4. Gates compare the release alone."""

    release: tuple[int, ...]
    build_id: str = field(compare=False)
    respin: str = ""  # a respin of the release, "A" in 7.10.3A; "" for the release itself

    def __str__(self):
        return _dotted(self.release) + self.respin

    @classmethod
    def parse(cls, text, build_id=""):
        """A version string as the inventory and image names give it: 7.10.3, 7.10.0.3 or 7.10.3A."""
        match = VERSION_STRING.fullmatch(str(text).strip())
        if not match:
            raise QumuloError(f"{text!r} is not a Qumulo Core version, such as 7.10.3, 7.10.0.3 or 7.10.3A")
        return cls(tuple(int(n) for n in match.group(1).split(".")), build_id, respin=match.group(2))

    @classmethod
    def from_revision(cls, revision_id, build_id=""):
        """The release at the end of a revision_id ("Qumulo Core 7.10.3", or "Qumulo Core 7.10.3A"), of any number of parts, or None."""
        match = re.search(r"(\d+(?:\.\d+)+)([A-Z]?)\s*$", revision_id or "")
        if not match:
            return None
        return cls(tuple(int(n) for n in match.group(1).split(".")), build_id, respin=match.group(2))

    @property
    def quarter(self):
        """(major, minor): 7.10.0.3 and 7.10.3 are both in quarter (7, 10)."""
        return self.release[:2]


VERSION_STRING = re.compile(r"(\d+\.\d+\.\d+(?:\.\d+)?)([A-Z]?)")


def cluster_version(client):
    """The release parsed from revision_id, read once per client."""
    if "version" not in client.memo:
        data = client.get(VERSION, auth=False).data
        found = ClusterVersion.from_revision(data["revision_id"], data["build_id"])
        if found is None:
            raise QumuloError(f"cannot parse the Qumulo Core version from revision_id {data['revision_id']!r}")
        client.memo["version"] = found
    return client.memo["version"]


def supports(client, feature):
    gate = GATES[feature]
    answers = _probes(client) if gate.probe else {}
    if feature in answers:
        return answers[feature]
    if answers.get(NO_DOCUMENT):
        warning = f"this cluster serves no /openapi.json, so {feature} is decided by its release, {cluster_version(client)}"
        if warning not in client.warnings:
            client.warnings.append(warning)
    return cluster_version(client).release >= gate.since


def require(client, feature, option):
    if supports(client, feature):
        return
    gate, version, host = GATES[feature], cluster_version(client), client.params["api_host"]
    if version.release >= gate.since:  # new enough, yet the probe did not find it
        raise QumuloError(f"this cluster's API lacks {option}; {host} runs {version}")
    raise QumuloError(f"{option} requires Qumulo Core {_dotted(gate.since)} or later; {host} runs {version}")


def _dotted(release):
    return ".".join(str(n) for n in release)


def _probes(client):
    """Every probe's answer for this build, from the cache file or from one read of /openapi.json.

    A build that serves no /openapi.json answers no probe, and its gates fall back to its release.
    """
    if "probes" in client.memo:
        return client.memo["probes"]
    build_id = cluster_version(client).build_id
    path = os.path.join(_cache_dir(), re.sub(r"[^A-Za-z0-9._-]", "_", build_id) + ".json")
    answers = _read_json(path)
    if answers is None or not (answers.get(NO_DOCUMENT) or all(name in answers for name, gate in GATES.items() if gate.probe)):
        answers = _read_document(client)
        _write_json(path, answers)
    client.memo["probes"] = answers
    return answers


def _read_document(client):
    try:
        spec = client.get(OPENAPI, auth=False).data
    except QumuloApiError as e:
        if e.status == 404 or (e.status == 500 and e.description == NO_DOCUMENT_DESCRIPTION):
            return {NO_DOCUMENT: True}
        raise
    return {name: _has_request_property(spec, *gate.probe) for name, gate in GATES.items() if gate.probe}


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "qumulo.cluster", "capabilities")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    """Best effort: an unwritable cache costs one more /openapi.json read next time."""
    tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


def _has_request_property(spec, method, path, dotted):
    operation = spec.get("paths", {}).get(path, {}).get(method)
    if not operation:
        return False
    schema = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    for part in dotted.split("."):
        prop = _properties(spec, schema).get(part.removesuffix("[]"))
        if prop is None:
            return False
        schema = _resolve(spec, prop)
        if part.endswith("[]"):
            schema = schema.get("items")
    return True


def _properties(spec, schema):
    """The properties a schema declares, through $ref and allOf, oneOf or anyOf."""
    schema = _resolve(spec, schema or {})
    found = dict(schema.get("properties", {}))
    for keyword in ("allOf", "oneOf", "anyOf"):
        for part in schema.get(keyword, []):
            found.update(_properties(spec, part))
    return found


def _resolve(spec, schema):
    while "$ref" in schema:
        node = spec
        for key in schema["$ref"].removeprefix("#/").split("/"):
            node = node[key]
        schema = node
    return schema
