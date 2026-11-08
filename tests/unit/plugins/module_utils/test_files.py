# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The files area: the emptiness walk over a directory's entries, and the file lookup."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import files
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient, QumuloError
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, client_params
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, entries_path, error

FILE = "FS_FILE_TYPE_FILE"
DIRECTORY_ID, FILE_ID = "1003", "1005"
MISS = {"found": False, "path": None, "id": None, "type": None}


def client():
    return QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID))


def entry(path, kind="FS_FILE_TYPE_FILE"):
    """One entry as GET entries lists it: a directory's path ends with a slash."""
    return {"path": path + ("/" if kind == files.DIRECTORY else ""), "name": path.rsplit("/", 1)[1], "type": kind}


def listing(fake, path, entries, next_page=""):
    fake.expect("GET", entries_path(path) + "?limit=1000", response={"files": entries, "paging": {"next": next_page}})


def test_the_walk_yields_every_object_at_any_depth_and_no_directory(fake_api):
    fake = fake_api()
    listing(fake, "/data", [entry("/data/a", files.DIRECTORY), entry("/data/link", "FS_FILE_TYPE_SYMLINK")])
    listing(fake, "/data/a", [entry("/data/a/b.txt"), entry("/data/a/fifo", "FS_FILE_TYPE_UNIX_PIPE")])

    found = [item["path"] for item in files.objects_under(client(), "/data")]

    assert found == ["/data/a/b.txt", "/data/a/fifo", "/data/link"]


def test_a_directory_holding_only_directories_holds_no_objects(fake_api):
    fake = fake_api()
    listing(fake, "/data", [entry("/data/a", files.DIRECTORY), entry("/data/b", files.DIRECTORY)])
    listing(fake, "/data/a", [entry("/data/a/c", files.DIRECTORY)])
    listing(fake, "/data/a/c", [])
    listing(fake, "/data/b", [])

    assert files.holds_objects(client(), "/data") is False


def test_the_walk_stops_at_the_first_object_without_reading_another_page(fake_api):
    fake = fake_api()
    listing(fake, "/data", [entry("/data/a.txt")], next_page="/v1/files/%2Fdata/entries/?after=1&limit=1000")

    assert files.holds_objects(client(), "/data") is True
    assert [e.path for e in fake.log if not e.background] == [entries_path("/data") + "?limit=1000"]


@pytest.mark.parametrize(
    ("path", "root", "inside"),
    [
        ("/data/scratch", "/data/scratch", True),
        ("/data/scratch/sub", "/data/scratch", True),
        ("/data/scratch2", "/data/scratch", False),
        ("/data", "/data/scratch", False),
        ("/data/scratch", "/", True),
        ("/", "/", True),
    ],
    ids=["the same path", "a child", "a sibling sharing the prefix", "the parent", "under the root", "the root itself"],
)
def test_inside_holds_for_the_path_and_what_lies_beneath_it(path, root, inside):
    assert files.inside(path, root) is inside


def test_find_files_keys_each_entry_by_the_term_as_given_with_the_path_canonical(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("/lookups"), response=attributes("/lookups", file_id=DIRECTORY_ID))
    fake.expect("GET", attributes_path(FILE_ID), response=attributes("/lookups/a.txt", file_id=FILE_ID, kind=FILE))

    found = files.find_files(client(), ["/lookups/", FILE_ID])

    assert found == {
        "/lookups/": {"found": True, "path": "/lookups", "id": DIRECTORY_ID, "type": files.DIRECTORY},
        FILE_ID: {"found": True, "path": "/lookups/a.txt", "id": FILE_ID, "type": FILE},
    }


def test_a_term_given_twice_is_read_once_and_is_one_key(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("/"), response=attributes("/", file_id="2"))

    found = files.find_files(client(), ["/", "/"])

    assert list(found) == ["/"]


# The classes T2000p saw on 7.10.3 and 7.11.0 for a term that names nothing.
@pytest.mark.parametrize(
    ("term", "error_class"),
    [
        ("/missing", "fs_no_such_entry_error"),
        ("/lookups/a.txt/b", "fs_no_such_path_error"),
        ("1007", "fs_no_such_inode_error"),
        ("97193315632660634721968979972", "fs_no_such_filesystem_error"),
    ],
    ids=["a missing path", "a path through a file", "a deleted or never-issued id", "an id of a file system this cluster lacks"],
)
def test_a_term_that_names_nothing_is_a_miss(fake_api, term, error_class):
    fake = fake_api()
    fake.expect("GET", attributes_path(term), status=404, response=error(error_class))

    found = files.find_files(client(), [term])

    assert found == {term: MISS}


# QUIRK-101: an id the server cannot place, such as 0, answers the 404 of a route that does not exist.
def test_an_id_answered_with_the_missing_routes_404_is_a_miss(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("0"), status=404, response=error("http_not_found_error", "System error"))

    found = files.find_files(client(), ["0"])

    assert found == {"0": MISS}


def test_a_path_answered_with_the_missing_routes_404_fails_with_the_clients_hint(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("/lookups"), status=404, response=error("http_not_found_error", "System error"))

    with pytest.raises(QumuloError, match=r"find_files term '/lookups'.*this route does not exist on this Qumulo Core version"):
        files.find_files(client(), ["/lookups"])


@pytest.mark.parametrize(
    ("status", "error_class"),
    [(404, "fs_no_such_snapshot_error"), (403, "fs_access_denied_error"), (400, "decode_error")],
    ids=["a 404 of another class", "a path the user may not read", "a 400"],
)
def test_any_other_error_fails_naming_the_term(fake_api, status, error_class):
    fake = fake_api()
    fake.expect("GET", attributes_path("/lookups"), status=status, response=error(error_class))

    with pytest.raises(QumuloError, match=rf"find_files term '/lookups/': .*HTTP {status} {error_class}") as raised:
        files.find_files(client(), ["/lookups/"])
    assert raised.value.details["term"] == "/lookups/"


def test_a_read_that_cannot_reach_the_cluster_fails_naming_the_term(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("/lookups"), raises=ConnectionResetError("reset by peer"))
    unreachable = QumuloClient(client_params(api_password="secret", expected_cluster_uuid=CLUSTER_ID, retry_timeout=0))

    with pytest.raises(QumuloError, match=r"^find_files term '/lookups': .*unreachable") as raised:
        files.find_files(unreachable, ["/lookups"])
    assert raised.value.details["term"] == "/lookups"


def test_a_path_the_cluster_answers_that_is_not_absolute_fails_naming_the_term(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("1007"), response={**attributes("/lookups", file_id="1007"), "path": ""})

    with pytest.raises(QumuloError, match=r"^find_files term '1007': the cluster's path '' must be an absolute path") as raised:
        files.find_files(client(), ["1007"])
    assert raised.value.details["term"] == "1007"


@pytest.mark.parametrize("term", ["relative/path", "/a/../b", "/a//b", "abc", "-1", "0x2", "12a", ""])
def test_a_term_that_is_neither_an_absolute_path_nor_a_decimal_id_is_refused_before_any_request(fake_api, term):
    fake = fake_api()

    with pytest.raises(QumuloError, match=r"find_files term"):
        files.find_files(client(), ["/lookups", term])
    assert fake.log == []


def test_a_term_refused_names_the_accepted_forms():
    with pytest.raises(QumuloError, match=r"find_files term 'abc'.*an absolute path.*a file id"):
        files.check_file_terms(["abc"])


def test_find_files_leaves_a_503_to_the_clients_retry(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("/lookups"), status=503, response=error("service_unavailable_error"))
    fake.expect("GET", attributes_path("/lookups"), response=attributes("/lookups", file_id=DIRECTORY_ID))

    found = files.find_files(client(), ["/lookups"])

    assert found["/lookups"]["found"] is True


def test_attributes_still_takes_only_a_missing_entry_as_nothing_there(fake_api):
    fake = fake_api()
    fake.expect("GET", attributes_path("/missing"), status=404, response=error("fs_no_such_entry_error"))
    fake.expect("GET", attributes_path("/a.txt/b"), status=404, response=error("fs_no_such_path_error"))

    assert files.attributes(client(), "/missing") is None
    with pytest.raises(QumuloError, match="fs_no_such_path_error"):
        files.attributes(client(), "/a.txt/b")
