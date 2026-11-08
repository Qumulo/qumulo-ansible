# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.s3_access_key, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import errno
import stat

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils import client, s3
from ansible_collections.qumulo.cluster.plugins.modules import s3_access_key
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import GUEST, error, fake_with_identities

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
KEYS = "/v1/s3/access-keys/"
GUEST_KEYS = f"{KEYS}?user=auth_id%3A{GUEST['auth_id']}"
OLD = "00000000000001f5a001"
NEW = "00000000000001f5b002"
SECRET = "test-secret-not-real"


def key(key_id, created="2026-11-01T00:00:00Z", owner=None):
    return {"access_key_id": key_id, "owner": dict(owner or GUEST), "creation_time": created}


def keys(*entries):
    return {"entries": list(entries), "paging": {"next": None}}


def minted(key_id=NEW):
    return {**key(key_id, "2026-11-02T00:00:00Z"), "secret_access_key": SECRET}


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "DELETE") and not e.background]


@pytest.fixture
def secrets(tmp_path):
    """The secret destination: a directory the module makes."""
    return tmp_path / "artifacts" / "edge01" / "secrets"


def run(run_module, secrets=None, check_mode=False, diff=False, no_log=True, **args):
    destination = {} if secrets is None else {"secret_destination": str(secrets)}
    return run_module(s3_access_key, {**CONNECTION, "user": "local:guest", "_ansible_no_log": no_log, **destination, **args}, check_mode=check_mode, diff=diff)


def expect_mint(fake, before=(), key_id=NEW):
    fake.expect("POST", KEYS, body={"user": {"auth_id": GUEST["auth_id"]}}, response=minted(key_id))
    fake.expect("GET", GUEST_KEYS, response=keys(*before, key(key_id, "2026-11-02T00:00:00Z")))


def test_a_user_without_a_key_gets_one_its_secret_written_to_a_file_and_returned_once(fake_api, run_module, monkeypatch, secrets):
    registered = []
    monkeypatch.setattr(client, "register_secret", registered.append)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    expect_mint(fake)

    result = run(run_module, secrets)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_access_key"] == {"user": "local:guest", "access_key_id": NEW, "creation_time": "2026-11-02T00:00:00Z"}
    assert result["secret_access_key"] == SECRET
    assert SECRET in registered
    written = secrets / f"s3-local_guest-{NEW}.yml"
    assert result["secret_file"] == str(written)
    assert yaml.safe_load(written.read_text(encoding="utf-8")) == {"user": "local:guest", "access_key_id": NEW, "secret_access_key": SECRET}
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o700


def test_a_user_with_a_key_is_unchanged_and_no_secret_is_returned(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD), key(NEW, "2026-11-02T00:00:00Z")))

    result = run(run_module, no_log=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["s3_access_key"]["access_key_id"] == NEW
    assert "secret_access_key" not in result
    assert writes(fake) == []


# QUIRK-053: the key list refuses pagination with the user filter, so a user's keys are read in one request without a limit.
def test_the_user_s_keys_are_read_without_a_limit(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD)))

    result = run(run_module, access_key_id=OLD)

    assert result["changed"] is False
    listed = [e.path for e in fake.log if e.path.startswith(KEYS)]
    assert listed == [GUEST_KEYS]
    assert "limit" not in listed[0]


def test_a_declared_key_that_exists_is_unchanged(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD), key(NEW)))

    result = run(run_module, access_key_id=OLD, no_log=False)

    assert result["changed"] is False
    assert result["s3_access_key"]["access_key_id"] == OLD
    assert writes(fake) == []


def test_a_declared_key_that_is_gone_from_a_user_with_no_key_is_replaced(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    expect_mint(fake)

    result = run(run_module, secrets, access_key_id=OLD)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_access_key"]["access_key_id"] == NEW
    assert result["secret_access_key"] == SECRET
    assert any(OLD in note and NEW in note and "access_key_id" in note for note in result["notes"])


@pytest.mark.parametrize("held", [[NEW], [NEW, "00000000000001f5c003"]], ids=["one other key", "two other keys"])
def test_a_declared_key_that_is_gone_from_a_user_with_another_fails_naming_them(fake_api, run_module, secrets, held):
    """A run after a rebuild's run made the replacement must not make another: the entry still names the old id."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(*(key(key_id) for key_id in held)))

    result = run(run_module, secrets, access_key_id=OLD)

    assert result["failed"] is True
    assert OLD in result["msg"]
    assert all(key_id in result["msg"] for key_id in held)
    assert "access_key_id" in result["msg"] and "state: absent" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check mode"])
def test_making_a_key_without_no_log_fails_before_any_write(fake_api, run_module, secrets, check_mode):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())

    result = run(run_module, secrets, check_mode=check_mode, no_log=False)

    assert result["failed"] is True
    assert "no_log: true" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check mode"])
def test_making_a_key_without_a_secret_destination_fails_before_any_write(fake_api, run_module, check_mode):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())

    result = run(run_module, check_mode=check_mode)

    assert result["failed"] is True
    assert "secret_destination" in result["msg"]
    assert writes(fake) == []


def test_check_mode_predicts_a_new_key_and_sends_no_write(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())

    result = run(run_module, secrets, check_mode=True, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert "secret_access_key" not in result
    assert result["diff"]["before"] == {"user": "local:guest", "access_key_ids": []}
    assert result["diff"]["after"]["access_key_ids"] == ["(a new key)"]
    assert writes(fake) == []
    assert not secrets.exists()


@pytest.mark.parametrize("declared", [{}, {"access_key_id": OLD}], ids=["no id", "an id"])
def test_a_user_that_does_not_exist_yet_is_a_new_key_in_check_mode(fake_api, run_module, secrets, declared):
    fake = fake_with_identities(fake_api)

    result = run(run_module, secrets, check_mode=True, user="local:svc-new", **declared)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["notes"] == ["user local:svc-new does not exist yet; an earlier task must create it, and this task then makes its key"]
    assert [e for e in fake.log if not e.background] == []


def test_a_bare_user_name_fails_before_any_request(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)

    result = run(run_module, secrets, user="guest")

    assert result["failed"] is True
    assert "bare name" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_lost_mint_deletes_the_key_whose_secret_was_lost(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_KEYS, response=keys(key(NEW, "2026-11-02T00:00:00Z")))
    fake.expect("DELETE", f"{KEYS}{NEW}")

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert result["changed"] is True
    assert f"deleted {NEW}" in result["msg"]
    assert "run the task again" in result["msg"]


def test_a_lost_mint_that_made_no_key_changes_nothing(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_KEYS, response=keys())

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert not result.get("changed")
    assert "no key was made" in result["msg"]


def test_an_orphan_the_cluster_refuses_to_delete_fails_with_the_cluster_s_error(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, raises=ConnectionResetError("reset"))
    fake.expect("GET", GUEST_KEYS, response=keys(key(NEW, "2026-11-02T00:00:00Z")))
    fake.expect("DELETE", f"{KEYS}{NEW}", status=500, response=error("internal_error", "boom"))

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert "internal_error" in result["msg"]


def test_a_made_key_missing_from_the_listing_is_deleted_and_the_run_fails(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("DELETE", f"{KEYS}{NEW}", status=404, response=error("http_not_found_error", "UnknownAccessKeyId"))

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert SECRET not in str(result)  # ansible-core prints a failed item's msg even under no_log
    assert result["changed"] is True
    assert f"{NEW} is missing after it was made" in result["msg"]
    assert "secret_access_key" not in result
    assert list(secrets.iterdir()) == []


def test_a_made_key_whose_secret_cannot_be_written_is_deleted(fake_api, run_module, secrets, monkeypatch):
    def full(directory, user, made):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(s3, "write_secret", full)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("DELETE", f"{KEYS}{NEW}")

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert SECRET not in str(result)  # ansible-core prints a failed item's msg even under no_log
    assert result["changed"] is True
    assert "could not write" in result["msg"]
    assert f"deleted {NEW}" in result["msg"]


def test_a_made_key_whose_secret_cannot_be_written_and_that_cannot_be_deleted_is_named(fake_api, run_module, secrets, monkeypatch):
    def full(directory, user, made):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(s3, "write_secret", full)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("DELETE", f"{KEYS}{NEW}", status=500, response=error("internal_error", "boom"))

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert SECRET not in str(result)  # ansible-core prints a failed item's msg even under no_log
    assert result["changed"] is True
    assert f"remove {NEW} by hand" in result["msg"]
    assert "internal_error" in result["msg"]


def test_a_made_key_the_listing_cannot_be_read_for_is_kept_with_its_secret(fake_api, run_module, secrets):
    """The secret is safe in its file, so a listing that fails to answer is no reason to throw the key away."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("GET", GUEST_KEYS, status=500, response=error("internal_error", "boom"))

    result = run(run_module, secrets)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["secret_access_key"] == SECRET
    assert (secrets / f"s3-local_guest-{NEW}.yml").exists()
    assert any("could not be confirmed" in note for note in result["notes"])
    assert writes(fake) == [e for e in writes(fake) if e.method == "POST"]


def test_a_made_key_missing_from_the_listing_that_cannot_be_deleted_names_the_kept_file(fake_api, run_module, secrets):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("DELETE", f"{KEYS}{NEW}", status=500, response=error("internal_error", "boom"))

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert SECRET not in str(result)  # ansible-core prints a failed item's msg even under no_log
    assert result["changed"] is True
    assert str(secrets / f"s3-local_guest-{NEW}.yml") in result["msg"]
    assert (secrets / f"s3-local_guest-{NEW}.yml").exists()


def test_a_destination_that_is_no_directory_fails_before_the_post(fake_api, run_module, tmp_path):
    occupied = tmp_path / "a-file"
    occupied.write_text("", encoding="utf-8")
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())

    result = run(run_module, occupied)

    assert result["failed"] is True
    assert "secret_destination" in result["msg"]
    assert writes(fake) == []


def test_an_existing_destination_others_could_read_is_made_private_before_the_post(fake_api, run_module, secrets):
    secrets.mkdir(parents=True)
    secrets.chmod(0o755)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    expect_mint(fake)

    result = run(run_module, secrets)

    assert not result.get("failed"), result.get("msg")
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o700


def test_a_destination_without_hard_links_still_gets_the_whole_file(fake_api, run_module, secrets, monkeypatch):
    def no_links(source, target):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(s3.os, "link", no_links)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    expect_mint(fake)

    result = run(run_module, secrets)

    assert not result.get("failed"), result.get("msg")
    written = secrets / f"s3-local_guest-{NEW}.yml"
    assert yaml.safe_load(written.read_text(encoding="utf-8"))["secret_access_key"] == SECRET
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert sorted(path.name for path in secrets.iterdir()) == [written.name]


SECOND = "00000000000001f5a002"


def test_a_rebuild_restores_each_of_a_user_s_declared_keys(fake_api, run_module, secrets):
    """With two entries for one user, each gone key is made again while the user holds fewer new keys than gone ones."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    expect_mint(fake)
    first = run(run_module, secrets, access_key_id=OLD, declared_access_key_ids=[OLD, SECOND])

    fake.expect("GET", GUEST_KEYS, response=keys(key(NEW, "2026-11-02T00:00:00Z")))
    expect_mint(fake, before=[key(NEW, "2026-11-02T00:00:00Z")], key_id="00000000000001f5b003")
    second = run(run_module, secrets, access_key_id=SECOND, declared_access_key_ids=[OLD, SECOND])

    assert not first.get("failed"), first.get("msg")
    assert not second.get("failed"), second.get("msg")
    assert first["changed"] and second["changed"]
    assert second["s3_access_key"]["access_key_id"] == "00000000000001f5b003"


def test_a_gone_key_is_not_made_again_while_the_user_holds_as_many_undeclared_keys(fake_api, run_module, secrets):
    """The run after the rebuild's, before the inventory names the new keys, makes no third key."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(NEW), key("00000000000001f5b003")))

    result = run(run_module, secrets, access_key_id=SECOND, declared_access_key_ids=[OLD, SECOND])

    assert result["failed"] is True
    assert NEW in result["msg"] and "00000000000001f5b003" in result["msg"]
    assert writes(fake) == []


def test_a_declared_key_kept_by_another_entry_does_not_count_as_new(fake_api, run_module, secrets):
    """The user holds the first entry's key; the second entry's gone key is made again."""
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD)))
    expect_mint(fake, before=[key(OLD)])

    result = run(run_module, secrets, access_key_id=SECOND, declared_access_key_ids=[OLD, SECOND])

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_state_absent_deletes_the_declared_key_only(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD), key(NEW)))
    fake.expect("DELETE", f"{KEYS}{OLD}")

    result = run(run_module, state="absent", access_key_id=OLD, no_log=False, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["s3_access_key"] == {}
    assert result["diff"] == {"before": {"user": "local:guest", "access_key_ids": [OLD, NEW]}, "after": {"user": "local:guest", "access_key_ids": [NEW]}}


def test_state_absent_without_a_key_id_deletes_every_key_of_the_user(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD), key(NEW)))
    fake.expect("DELETE", f"{KEYS}{OLD}")
    fake.expect("DELETE", f"{KEYS}{NEW}")

    result = run(run_module, state="absent", no_log=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_state_absent_in_check_mode_sends_no_delete(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD)))

    result = run(run_module, check_mode=True, state="absent", access_key_id=OLD, no_log=False)

    assert result["changed"] is True
    assert writes(fake) == []


@pytest.mark.parametrize("stored", [keys(), keys(key(NEW))])
def test_state_absent_on_a_missing_key_is_unchanged(fake_api, run_module, stored):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=stored)

    result = run(run_module, state="absent", access_key_id=OLD, no_log=False)

    assert result["changed"] is False
    assert writes(fake) == []


def test_a_key_another_client_deleted_first_counts_as_deleted(fake_api, run_module):
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys(key(OLD)))
    fake.expect("DELETE", f"{KEYS}{OLD}", status=404, response=error("http_not_found_error", "UnknownAccessKeyId"))

    result = run(run_module, state="absent", access_key_id=OLD, no_log=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_secret_file_whose_directory_cannot_be_synced_is_removed_with_the_key(fake_api, run_module, secrets, monkeypatch):
    """The file is in place before the directory is synced; the key is deleted, so no file may keep its secret."""
    sync = s3.os.fsync

    def failing_on_the_directory(descriptor):
        if stat.S_ISDIR(s3.os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "Input/output error")
        sync(descriptor)

    monkeypatch.setattr(s3.os, "fsync", failing_on_the_directory)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("DELETE", f"{KEYS}{NEW}")

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert f"deleted {NEW}" in result["msg"]
    assert list(secrets.iterdir()) == []


@pytest.mark.skipif(s3.os.geteuid() == 0, reason="root writes a directory whatever its mode")
def test_a_destination_that_cannot_be_written_fails_before_the_post(fake_api, run_module, secrets):
    secrets.mkdir(parents=True)
    secrets.chmod(0o500)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    try:
        result = run(run_module, secrets)
    finally:
        secrets.chmod(0o700)

    assert result["failed"] is True
    assert "cannot hold a secret" in result["msg"]
    assert writes(fake) == []


def test_a_destination_whose_mode_does_not_change_fails_before_the_post(fake_api, run_module, secrets, monkeypatch):
    """Some mounts accept a chmod and keep the mode, as vfat and CIFS do."""
    secrets.mkdir(parents=True)
    secrets.chmod(0o755)
    monkeypatch.setattr(s3.os, "chmod", lambda path, mode: None)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert "readable only by its owner" in result["msg"]
    assert writes(fake) == []


def test_a_secret_file_name_taken_without_hard_links_is_reported_as_taken(fake_api, run_module, secrets, monkeypatch):
    def no_links(source, target):
        raise OSError(errno.EPERM, "Operation not permitted")

    secrets.mkdir(parents=True, mode=0o700)
    taken = secrets / f"s3-local_guest-{NEW}.yml"
    taken.write_text("kept\n", encoding="utf-8")
    monkeypatch.setattr(s3.os, "link", no_links)
    fake = fake_with_identities(fake_api)
    fake.expect("GET", GUEST_KEYS, response=keys())
    fake.expect("POST", KEYS, response=minted())
    fake.expect("DELETE", f"{KEYS}{NEW}")

    result = run(run_module, secrets)

    assert result["failed"] is True
    assert "File exists" in result["msg"]
    assert taken.read_text(encoding="utf-8") == "kept\n"
