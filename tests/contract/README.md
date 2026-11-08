<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# API contract tests

These tests compare what the collection sends with the OpenAPI document a Qumulo cluster serves at `/openapi.json`. They catch a route that a release drops or renames, a request field whose name or type changes, and a documentation mismatch that a release fixes, which makes its workaround reviewable.

## What they check

- `test_every_route_exists`: every `Route` assigned at the top level of a file in `plugins/module_utils/` is in the document, for each method it declares. Path parameters match by position, so `{id}` matches `{share_id}`.
- `test_wire_fields_match_the_request_schema`: the fields each write sends, declared in `wire_fields.yml`, are in the request schema with a compatible type, and every field the schema requires is sent. Entries in `known_mismatches.yml` that apply to the server's release are skipped.
- `test_known_mismatches_still_mismatch`: every entry in `known_mismatches.yml` still describes the document. When one fails, the document has been fixed: review the workaround, then remove the entry.

The other tests need no cluster. They check that every POST, PUT and PATCH route has an entry in `wire_fields.yml`, that both YAML files are well formed, and that the checks in `openapi_checks.py` behave as intended on small inline documents. `test_document_source.py` checks where the document comes from, and runs the tests above against a document it builds from the routes, `wire_fields.yml` and the ledger. CI's `tools` job runs these on every pull request.

## Running them

```sh
task contract
```

The document comes from the first of:

1. `--spec-file FILE`, a document saved earlier, as in `task contract -- --spec-file ~/openapi-7.10.3.json --server-version 7.10.3`. `--server-version` names the release the document is from, for ledger entries that apply only before a release.
2. The cluster named by the connection environment variables the modules use: `QUMULO_API_HOST`, `QUMULO_API_PORT`, `QUMULO_VALIDATE_CERTS`, `QUMULO_CA_PATH`, and `QUMULO_API_PASSWORD` or `QUMULO_API_TOKEN`.
3. The cluster in `tests/integration/integration_config.yml`, which `task edge:up` writes. The integration workflow's `edge` job runs the tests this way.

Without any of them, the tests that need the document are skipped, and the run ends with a line that starts `skipped: no cluster`. With `QUMULO_CONTRACT_REQUIRE_CLUSTER=1`, which the integration workflow sets, a missing cluster is an error instead. A cluster that answers but serves no document, as the Qumulo Core 7.10.3 docker image does (404 or 500 on both `/openapi.json` and `/v1/openapi.json`, gap G-CONTAINER-OPENAPI in `docs/reference/gaps.yml`), skips those tests too, and the run ends with `skipped: the cluster serves no OpenAPI document`, even under `QUMULO_CONTRACT_REQUIRE_CLUSTER=1`. On the container, then, only the tests that need no document run; the others need a full cluster or a `--spec-file`. With `QUMULO_CONTRACT_REQUIRE_DOCUMENT=1`, for a lane that runs a full cluster, a cluster that serves no document is an error. `/openapi.json` needs no login, but a cluster is used only when credentials are set, as for an integration run.

The document is held in memory, and a failing test's output never shows it: the fixture's repr gives only its number of paths, a problem names only what the collection sends, JSON types and counts, the checks raise `DocumentError` in place of an error that could quote it, a `--spec-file` that is missing or not JSON is a usage error that does not quote the file, and a failed read from a cluster raises an error that holds none of the body. The canary tests in `test_document_source.py` prove this for a document file; a run against a live cluster with `-l` (`--showlocals`) is the one path they do not cover, so do not pass `-l` to a run by hand against a cluster on an unreleased build. To see the names of required properties the collection never sends, set `QUMULO_CONTRACT_SHOW_DOCUMENT_NAMES=1` on a run by hand; no workflow sets it. Do not save a document from an unreleased build in the repository, in an issue, or in a workflow artifact. The OpenAPI drift queue is the one exception the maintainer allows: it keeps a fingerprint of each rc and ga build's document, and the changes between them, in the repository, its issues and its artifacts, since the repository stays private and customers get the collection from Galaxy, whose artifact leaves the queue out.

`conftest.py` puts the collection on `sys.path` for these tests.

## When you add a write

Add the route's fields to `wire_fields.yml`. If the document disagrees with what the server accepts, add an entry to `known_mismatches.yml` that says what a client sees, and cite it from the code that works around it.
