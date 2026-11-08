# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.snapshot_lock, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK.

The keys are made, and the signatures checked, with the openssl command, which the module signs with too. Every result
is checked for the private keys' text, and every key file the module writes for its mode and its removal.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest
from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils import signing
from ansible_collections.qumulo.cluster.plugins.modules import snapshot_lock
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import ANY, CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_keys import assert_no_secret, make_keys, spy_key_files
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
EXPIRES = "2026-11-16T02:44:35Z"
CHALLENGE = "df5aaab2-1020-47f4-b2ea-560d19e836d122"


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    return make_keys(tmp_path_factory, "legal", "other")


@pytest.fixture
def key_files(monkeypatch):
    """Each file mkstemp makes while the module runs, with its mode when made; the module writes the key to one."""
    return spy_key_files(monkeypatch)


def stored_key(key, key_id="1", name="legal-hold"):
    """GET /v1/file-system/security/keys/{ref}: the public key as base64 DER."""
    return {"id": key_id, "name": name, "comment": "", "disabled": False, "public_key": key.public_key}


def status(lock_key=None, expiration=EXPIRES, in_delete=False):
    return {
        "id": 12,
        "name": "12_before-upgrade",
        "timestamp": "2026-11-02T02:44:35.267716486Z",
        "source_file_id": "100",
        "source_file_path": "/data/projects/",
        "policy_id": None,
        "policy_name": None,
        "expiration": expiration,
        "owners": [],
        "in_delete": in_delete,
        "lock_key": lock_key,
    }


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method == "POST" and not e.background]


def run(run_module, check_mode=False, diff=False, **args):
    result = run_module(snapshot_lock, {**CONNECTION, "snapshot_id": 12, **args}, check_mode=check_mode, diff=diff)
    assert_no_secret(result)
    return result


def test_an_unlocked_snapshot_that_expires_is_locked_with_the_keys_id(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))
    fake.expect("POST", "/v3/snapshots/12/lock", body={"lock_key_ref": "1"}, response={"lock_key_ref": "1"})

    result = run(run_module, lock_key="legal-hold", diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["snapshot_lock"] == {
        "snapshot_id": 12,
        "name": "12_before-upgrade",
        "path": "/data/projects",
        "expiration": EXPIRES,
        "lock_key": "legal-hold",
    }
    assert result["diff"]["before"]["lock_key"] is None
    assert result["diff"]["after"]["lock_key"] == "legal-hold"


def test_a_snapshot_locked_with_the_key_is_unchanged_and_sends_no_lock(fake_api, run_module, keys):
    """The cluster refuses to lock a locked snapshot, even with its own key (G-SNAP-LOCK)."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, lock_key="legal-hold")

    assert result["changed"] is False
    assert writes(fake) == []


def test_a_key_given_by_id_is_the_same_key(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, lock_key="1")

    assert result["changed"] is False
    assert result["snapshot_lock"]["lock_key"] == "legal-hold"


def test_a_snapshot_locked_with_another_key_is_refused(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="2"))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))
    fake.expect("GET", "/v1/file-system/security/keys/2", response=stored_key(keys["other"], key_id="2", name="archive"))

    result = run(run_module, lock_key="legal-hold")

    assert result["failed"] is True
    assert "locked with the key archive" in result["msg"] and "unlock it first" in result["msg"]
    assert writes(fake) == []


def test_a_snapshot_that_never_expires_is_not_locked_without_the_private_key(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(expiration=""))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, check_mode=True, lock_key="legal-hold")

    assert result["failed"] is True
    assert "never expires" in result["msg"] and "private_key" in result["msg"] and "expiration" in result["msg"]
    assert writes(fake) == []


def test_a_snapshot_that_never_expires_is_locked_with_a_private_key_that_matches(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(expiration=""))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))
    fake.expect("POST", "/v3/snapshots/12/lock", body={"lock_key_ref": "1"}, response={"lock_key_ref": "1"})

    result = run(run_module, lock_key="legal-hold", private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_private_key_that_does_not_match_the_lock_key_is_refused(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(expiration=""))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, lock_key="legal-hold", private_key=keys["other"].pem)

    assert result["failed"] is True
    assert "does not match" in result["msg"] and "legal-hold" in result["msg"]
    assert writes(fake) == []


def test_a_locked_snapshot_is_unlocked_with_a_signed_challenge(fake_api, run_module, keys, tmp_path):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))
    fake.expect("GET", "/v3/snapshots/12/unlock-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v3/snapshots/12/unlock", body=ANY, response=None)

    result = run(run_module, state="unlocked", private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["snapshot_lock"]["lock_key"] is None
    signature = next(e.body["signature"] for e in fake.log if e.path == "/v3/snapshots/12/unlock")
    assert keys["legal"].verifies(CHALLENGE, signature, tmp_path)


def test_an_unlocked_snapshot_is_unchanged_without_a_private_key(fake_api, run_module):
    """The cluster refuses to unlock an unlocked snapshot."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())

    result = run(run_module, state="unlocked")

    assert result["changed"] is False
    assert writes(fake) == []


def test_unlocking_without_the_private_key_fails_naming_the_key(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, state="unlocked")

    assert result["failed"] is True
    assert "legal-hold" in result["msg"] and "private_key" in result["msg"]


def test_check_mode_unlock_checks_the_key_and_asks_for_no_challenge(fake_api, run_module, keys):
    """A challenge is single-use and the cluster audits it, so check mode asks for none."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, check_mode=True, diff=True, state="unlocked", private_key=keys["legal"].pem)

    assert result["changed"] is True
    assert result["diff"]["before"]["lock_key"] == "legal-hold"
    assert result["diff"]["after"]["lock_key"] is None


def test_check_mode_lock_sends_no_lock(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, check_mode=True, lock_key="legal-hold")

    assert result["changed"] is True
    assert writes(fake) == []


def test_without_openssl_a_private_key_fails_before_any_request(fake_api, run_module, monkeypatch):
    """Runs without openssl too: the module never gets as far as the key."""
    monkeypatch.setattr(AnsibleModule, "get_bin_path", lambda self, name, *args, **kwargs: None)
    fake = fake_api()

    result = run(run_module, state="unlocked", private_key="-----BEGIN PRIVATE KEY-----\nnot read\n-----END PRIVATE KEY-----\n")

    assert result["failed"] is True
    assert "private_key needs the openssl command on the controller" in result["msg"] and "PATH" in result["msg"]
    assert fake.log == []


def test_a_snapshot_found_by_name(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v4/snapshots/status/?limit=1000", response={"entries": [{**status(), "id": 11, "name": "11_other"}, status(lock_key="1")]})
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run_module(snapshot_lock, {**CONNECTION, "name": "12_before-upgrade", "lock_key": "legal-hold"})

    assert result["changed"] is False
    assert result["snapshot_lock"]["snapshot_id"] == 12


def test_a_missing_snapshot_fails_naming_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", status=404, response=error("snapshot_not_found_error"))

    result = run(run_module, lock_key="legal-hold")

    assert result["failed"] is True
    assert "no snapshot 12" in result["msg"]


def test_a_missing_key_fails_naming_the_key_store(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", status=404, response=error("fs_key_not_found_error"))

    result = run(run_module, lock_key="legal-hold")

    assert result["failed"] is True
    assert "legal-hold" in result["msg"] and "key store" in result["msg"] and "qumulo.cluster.security_key" in result["msg"]


def test_a_lost_lock_is_reconciled_by_reading_the_lock_back(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))
    fake.expect("POST", "/v3/snapshots/12/lock", raises=ConnectionResetError())
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))

    result = run(run_module, lock_key="legal-hold")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert any("reconciled" in note for note in result["notes"])


def test_a_lost_unlock_that_did_not_take_fails_as_maybe_applied(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))
    fake.expect("GET", "/v3/snapshots/12/unlock-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v3/snapshots/12/unlock", raises=ConnectionResetError())
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))

    result = run(run_module, state="unlocked", private_key=keys["legal"].pem)

    assert result["failed"] is True
    assert result["maybe_applied"] is True


def test_the_key_file_is_readable_by_its_user_only_and_removed_after_a_signature(fake_api, run_module, keys, key_files):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))
    fake.expect("GET", "/v3/snapshots/12/unlock-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v3/snapshots/12/unlock", body=ANY, response=None)

    result = run(run_module, state="unlocked", private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert key_files, "the module wrote no key file for openssl"
    assert [mode for _path, mode in key_files] == [0o600] * len(key_files)
    assert [path for path, _mode in key_files if os.path.exists(path)] == []


def test_a_private_key_openssl_cannot_read_fails_and_its_file_is_removed(fake_api, run_module, keys, key_files):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, state="unlocked", private_key="not a key")

    assert result["failed"] is True
    assert "openssl could not read the private key" in result["msg"]
    assert key_files
    assert [path for path, _mode in key_files if os.path.exists(path)] == []
    assert writes(fake) == []


def test_an_encrypted_private_key_fails_at_once_saying_to_give_it_decrypted(fake_api, run_module, keys, tmp_path, key_files):
    """An encrypted PEM is refused before its key file is written."""
    encrypted = tmp_path / "encrypted.pem"
    subprocess.run(
        ["openssl", "pkey", "-in", str(keys["legal"].pem_path), "-aes256", "-passout", "pass:secret-phrase", "-out", str(encrypted)],
        check=True,
        capture_output=True,
    )
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, state="unlocked", private_key=encrypted.read_text())

    assert result["failed"] is True
    assert "private_key is encrypted" in result["msg"] and "decrypted" in result["msg"]
    assert key_files == []
    assert writes(fake) == []


def test_an_encrypted_key_the_pem_header_does_not_show_fails_on_openssl_s_answer(fake_api, run_module, keys, tmp_path, monkeypatch):
    """openssl gets a passphrase no key uses, so it fails rather than asking for the passphrase."""
    monkeypatch.setattr(signing, "ENCRYPTED_PEM", re.compile(r"(?!)"))  # as for an encrypted form the header check misses
    encrypted = tmp_path / "encrypted.pem"
    subprocess.run(
        ["openssl", "pkey", "-in", str(keys["legal"].pem_path), "-aes256", "-passout", "pass:secret-phrase", "-out", str(encrypted)],
        check=True,
        capture_output=True,
    )
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, state="unlocked", private_key=encrypted.read_text())

    assert result["failed"] is True
    assert "private_key is encrypted" in result["msg"]
    assert writes(fake) == []


def test_an_encrypted_key_openssl_cannot_decode_fails_as_encrypted(fake_api, run_module, keys, tmp_path, monkeypatch):
    """With the wrong passphrase, about one encryption in 200 decrypts to bytes that pass the padding check, and openssl
    then says it cannot decode the key rather than bad decrypt. The stub openssl gives that answer every time."""
    monkeypatch.setattr(signing, "ENCRYPTED_PEM", re.compile(r"(?!)"))  # as for an encrypted form the header check misses
    encrypted = tmp_path / "encrypted.pem"
    subprocess.run(
        ["openssl", "pkey", "-in", str(keys["legal"].pem_path), "-aes256", "-passout", "pass:secret-phrase", "-out", str(encrypted)],
        check=True,
        capture_output=True,
    )
    openssl = tmp_path / "openssl"
    openssl.write_text(
        f"""#!/bin/sh
case "$1" in pkey|dgst) ;; *) exec {shutil.which("openssl")} "$@" ;; esac
echo 'Could not read key from /tmp/qumulo-key-x' >&2
echo '807BE95C317F0000:error:1E08010C:DECODER routines:OSSL_DECODER_from_bio:unsupported:No supported data to decode.' >&2
exit 1
"""
    )
    openssl.chmod(0o755)
    monkeypatch.setattr(AnsibleModule, "get_bin_path", lambda self, name, *args, **kwargs: str(openssl))
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, state="unlocked", private_key=encrypted.read_text())

    assert result["failed"] is True
    assert "private_key is encrypted" in result["msg"]
    assert writes(fake) == []


def test_unlocking_an_unlocked_snapshot_reads_no_key(fake_api, run_module):
    """The key may already be gone from the key store, as in a teardown that unlocks and then removes it."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())

    result = run(run_module, state="unlocked", lock_key="legal-hold")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_a_private_key_that_does_not_match_the_holder_asks_for_no_challenge(fake_api, run_module, keys):
    """A challenge is single-use and audited, so a key that cannot sign it is refused before one is asked for."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response=stored_key(keys["legal"]))

    result = run(run_module, state="unlocked", private_key=keys["other"].pem)

    assert result["failed"] is True
    assert "does not match" in result["msg"]
    assert not [e for e in fake.log if e.path.endswith("/unlock-challenge")]


def test_a_lock_without_a_private_key_needs_no_openssl(fake_api, run_module, keys, monkeypatch):
    monkeypatch.setattr(AnsibleModule, "get_bin_path", lambda self, name, *args, **kwargs: None)
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))
    fake.expect("POST", "/v3/snapshots/12/lock", body={"lock_key_ref": "1"}, response={"lock_key_ref": "1"})

    result = run(run_module, lock_key="legal-hold")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


@pytest.mark.parametrize("never", ["", None], ids=["empty", "null"])
def test_a_snapshot_whose_expiration_reads_as_never_in_any_form_is_not_locked_without_the_private_key(fake_api, run_module, keys, never):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(expiration=never))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, lock_key="legal-hold")

    assert result["failed"] is True
    assert "never expires" in result["msg"]
    assert writes(fake) == []


def test_a_snapshot_being_deleted_is_not_locked(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(in_delete=True))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, lock_key="legal-hold")

    assert result["failed"] is True
    assert "being deleted" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_disabled_key_is_refused_before_the_lock_naming_security_key(fake_api, run_module, keys, check_mode):
    """The cluster refuses to lock with a disabled key (spec 022, Decision 7)."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status())
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response={**stored_key(keys["legal"]), "disabled": True})

    result = run(run_module, check_mode=check_mode, lock_key="legal-hold")

    assert result["failed"] is True
    assert "legal-hold is disabled" in result["msg"] and "qumulo.cluster.security_key" in result["msg"]
    assert writes(fake) == []


def test_a_snapshot_locked_with_a_key_disabled_since_is_unchanged_on_a_rerun(fake_api, run_module, keys):
    """The disabled-key refusal comes after the held-lock checks, so a rerun of the lock task reports no change."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response={**stored_key(keys["legal"]), "disabled": True})

    result = run(run_module, lock_key="legal-hold")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_an_empty_lock_key_is_refused_before_any_request(fake_api, run_module):
    """An empty key_ref is the key list's route (#333)."""
    fake = fake_api()

    result = run(run_module, lock_key="")

    assert result["failed"] is True
    assert "lock_key is empty" in result["msg"]
    assert [e.path for e in fake.log if "/snapshots/" in e.path or "/security/" in e.path] == []


def test_a_snapshot_whose_key_is_disabled_is_still_unlocked(fake_api, run_module, keys, tmp_path):
    """A disabled key still unlocks what it locked."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/1", response={**stored_key(keys["legal"]), "disabled": True})
    fake.expect("GET", "/v3/snapshots/12/unlock-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v3/snapshots/12/unlock", body=ANY, response=None)

    result = run(run_module, state="unlocked", private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_unlocking_with_a_lock_key_that_does_not_hold_the_lock_is_refused(fake_api, run_module, keys):
    """A task that names a key must unlock only what that key locked."""
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="2"))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))
    fake.expect("GET", "/v1/file-system/security/keys/2", response=stored_key(keys["other"], key_id="2", name="archive"))

    result = run(run_module, state="unlocked", lock_key="legal-hold", private_key=keys["other"].pem)

    assert result["failed"] is True
    assert "locked with the key archive, not legal-hold" in result["msg"]
    assert writes(fake) == []


def test_unlocking_names_the_key_that_holds_the_lock(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", "/v3/snapshots/status/12", response=status(lock_key="1"))
    fake.expect("GET", "/v1/file-system/security/keys/legal-hold", response=stored_key(keys["legal"]))

    result = run(run_module, check_mode=True, state="unlocked", lock_key="legal-hold", private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_name_no_snapshot_has_fails_naming_it(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v4/snapshots/status/?limit=1000", response={"entries": [{**status(), "id": 11, "name": "11_other"}]})

    result = run_module(snapshot_lock, {**CONNECTION, "name": "12_before-upgrade", "lock_key": "legal-hold"})

    assert result["failed"] is True
    assert "no snapshot named 12_before-upgrade" in result["msg"]
