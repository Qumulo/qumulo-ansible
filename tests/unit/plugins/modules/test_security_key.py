# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.security_key, driven through FakeQumulo. Bodies follow the public qumulo-api SDK and spec 022's research.

The keys are made, and every signature the module sends is checked, with the openssl command. Every result is checked for
the private keys' text.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.modules import security_key
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import ANY, CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_keys import assert_no_secret, make_keys, spy_key_files
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
KEY_PATH = "/v1/file-system/security/keys/legal-hold"
ETAG = '"AQAAAA"'
CHALLENGE = "legal-hold1"
MISSING = error("fs_change_lock_key_store_no_such_key_error", "A public key with name legal-hold could not be found.")


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    return make_keys(tmp_path_factory, "legal", "new", "other")


def stored(key, comment="", disabled=False, key_id="2"):
    """A key as GET /v1/file-system/security/keys/{ref} answers it: the public key as base64 DER."""
    return {"id": key_id, "name": "legal-hold", "comment": comment, "disabled": disabled, "public_key": key.public_key}


def usages(*entries):
    return {"usages": [{"type": kind, "id": ident} for kind, ident in entries], "paging": {"next": None}}


def run(run_module, check_mode=False, diff=False, **args):
    result = run_module(security_key, {**CONNECTION, "name": "legal-hold", **args}, check_mode=check_mode, diff=diff)
    assert_no_secret(result)
    return result


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def key_requests(fake):
    return [f"{e.method} {e.path}" for e in fake.log if "/security/keys" in e.path]


def sent(fake, method, path):
    return next(e.body for e in fake.log if e.method == method and e.path == path)


# Creating


def test_a_missing_key_is_created_with_its_name_signed_by_the_private_key(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("POST", "/v1/file-system/security/keys/", body=ANY, response=stored(keys["legal"]))
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, private_key=keys["legal"].pem, diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["security_key"] == {"id": "2", "name": "legal-hold", "comment": "", "enabled": True, "public_key": keys["legal"].public_key}
    body = sent(fake, "POST", "/v1/file-system/security/keys/")
    assert set(body) == {"name", "public_key", "comment", "verification_signature"}
    assert (body["name"], body["public_key"], body["comment"]) == ("legal-hold", keys["legal"].public_key, "")
    assert keys["legal"].verifies("legal-hold", body["verification_signature"])
    assert result["diff"]["before"] == {}
    assert result["diff"]["after"]["public_key"] == keys["legal"].public_key


def test_a_key_is_created_from_a_pem_public_key_and_a_signature_without_openssl(fake_api, run_module, keys, monkeypatch):
    signature = keys["legal"].sign("legal-hold")
    monkeypatch.setattr(AnsibleModule, "get_bin_path", lambda self, name, *args, **kwargs: None)
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect(
        "POST",
        "/v1/file-system/security/keys/",
        body={"name": "legal-hold", "public_key": keys["legal"].public_key, "comment": "holds", "verification_signature": signature},
        response=stored(keys["legal"], comment="holds"),
    )
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], comment="holds"), etag=ETAG)

    result = run(run_module, public_key=keys["legal"].public_pem.decode(), verification_signature=signature, comment="holds")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_a_new_key_declared_disabled_is_created_then_disabled(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("POST", "/v1/file-system/security/keys/", body=ANY, response=stored(keys["legal"]))
    fake.expect("GET", "/v1/file-system/security/keys/2", response=stored(keys["legal"]), etag=ETAG)
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"disabled": True}, if_match=ETAG, response=stored(keys["legal"], disabled=True))
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], disabled=True), etag='"AgAAAA"')

    result = run(run_module, private_key=keys["legal"].pem, enabled=False)

    assert not result.get("failed"), result.get("msg")
    assert result["security_key"]["enabled"] is False


def test_check_mode_reports_the_create_and_sends_nothing(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, check_mode=True, diff=True, private_key=keys["legal"].pem, comment="holds")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["diff"]["after"] == {"name": "legal-hold", "comment": "holds", "enabled": True, "public_key": keys["legal"].public_key}
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_missing_key_without_a_private_key_or_a_signature_is_refused(fake_api, run_module, keys, check_mode):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, check_mode=check_mode, public_key=keys["legal"].public_key)

    assert result["failed"] is True
    assert "private_key" in result["msg"] and "verification_signature" in result["msg"]
    assert writes(fake) == []


def test_a_public_key_and_a_private_key_that_do_not_match_fail_before_any_request(fake_api, run_module, keys):
    fake = fake_api()

    result = run(run_module, public_key=keys["other"].public_key, private_key=keys["legal"].pem)

    assert result["failed"] is True
    assert "do not match" in result["msg"]
    assert key_requests(fake) == []


def test_a_name_of_digits_only_is_refused_before_any_request(fake_api, run_module, keys):
    fake = fake_api()

    result = run_module(security_key, {**CONNECTION, "name": "123", "public_key": keys["legal"].public_key})

    assert result["failed"] is True
    assert "name '123'" in result["msg"]
    assert key_requests(fake) == []


def test_an_empty_name_is_refused_before_any_request(fake_api, run_module, keys):
    """QUIRK-120: the cluster stores a key with an empty name, which no key_ref names afterwards."""
    fake = fake_api()

    result = run_module(security_key, {**CONNECTION, "name": "", "public_key": keys["legal"].public_key})

    assert result["failed"] is True
    assert "name ''" in result["msg"]
    assert key_requests(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_missing_key_without_a_public_key_or_a_private_key_is_refused(fake_api, run_module, check_mode):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, check_mode=check_mode, comment="holds")

    assert result["failed"] is True
    assert "public_key or private_key" in result["msg"]
    assert writes(fake) == []


def test_a_signature_without_a_public_key_is_refused_by_the_argument_spec(fake_api, run_module, keys):
    fake = fake_api()

    result = run(run_module, verification_signature=keys["legal"].sign("legal-hold"))

    assert result["failed"] is True
    assert "public_key" in result["msg"]
    assert key_requests(fake) == []


def test_a_rotation_task_that_finds_no_key_registers_it_and_notes_old_private_key_went_unused(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("POST", "/v1/file-system/security/keys/", body=ANY, response=stored(keys["new"]))
    fake.expect("GET", KEY_PATH, response=stored(keys["new"]), etag=ETAG)

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert any("old_private_key was not used" in note for note in result["notes"])


def test_a_lost_create_of_a_disabled_key_that_took_is_noted_and_the_key_disabled(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("POST", "/v1/file-system/security/keys/", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"disabled": True}, if_match=ETAG, response=stored(keys["legal"], disabled=True))
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], disabled=True), etag='"AgAAAA"')

    result = run(run_module, private_key=keys["legal"].pem, enabled=False)

    assert not result.get("failed"), result.get("msg")
    assert result["security_key"]["enabled"] is False
    assert "the create was confirmed by reading back after a lost connection" in result["notes"]


def test_a_lost_create_of_a_disabled_key_that_did_not_take_fails_as_maybe_applied(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("POST", "/v1/file-system/security/keys/", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, private_key=keys["legal"].pem, enabled=False)

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "PATCH /v1/file-system/security/keys/2" not in writes(fake)


# Unchanged, and changed in place


def test_a_matching_key_given_as_pem_is_unchanged_and_sends_no_write(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], comment="holds"), etag=ETAG)

    result = run(run_module, public_key=keys["legal"].public_pem.decode())

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["security_key"]["comment"] == "holds"
    assert writes(fake) == []


def test_a_matching_key_given_by_its_private_key_is_unchanged(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_a_comment_is_patched_with_the_reads_etag(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"comment": "holds"}, if_match=ETAG, response=stored(keys["legal"], comment="holds"))
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], comment="holds"), etag='"AgAAAA"')

    result = run(run_module, diff=True, public_key=keys["legal"].public_key, comment="holds")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert (result["diff"]["before"]["comment"], result["diff"]["after"]["comment"]) == ("", "holds")


def test_a_key_only_snapshots_use_is_disabled_by_its_name_alone(fake_api, run_module, keys):
    """A disabled key still unlocks the snapshots it locked; only a policy or a replication target blocks a disable."""
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/usages?limit=1000", response=usages(("snapshot", "12")))
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"disabled": True}, if_match=ETAG, response=stored(keys["legal"], disabled=True))
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], disabled=True), etag='"AgAAAA"')

    result = run(run_module, enabled=False)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["security_key"]["enabled"] is False


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_key_a_policy_or_a_replication_target_uses_is_not_disabled(fake_api, run_module, keys, check_mode):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect(
        "GET",
        "/v1/file-system/security/keys/2/usages?limit=1000",
        response=usages(("snapshot", "12"), ("snapshot_policy", "1"), ("replication_target", "5")),
    )

    result = run(run_module, check_mode=check_mode, public_key=keys["legal"].public_key, enabled=False)

    assert result["failed"] is True
    assert "replication_target 5" in result["msg"] and "snapshot_policy 1" in result["msg"]
    assert "snapshot 12" not in result["msg"]
    assert writes(fake) == []


def test_enabling_a_disabled_key_reads_no_usages(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"], disabled=True), etag=ETAG)
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"disabled": False}, if_match=ETAG, response=stored(keys["legal"]))
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag='"AgAAAA"')

    result = run(run_module, public_key=keys["legal"].public_key, enabled=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


# Replacing


def test_a_replace_signs_the_challenge_with_both_keys_and_sends_the_reads_etag(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", body=ANY, if_match=ETAG, response=stored(keys["new"]))
    fake.expect("GET", KEY_PATH, response=stored(keys["new"]), etag='"AgAAAA"')

    result = run(run_module, diff=True, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["security_key"]["id"] == "2"
    assert result["security_key"]["public_key"] == keys["new"].public_key
    body = sent(fake, "POST", "/v1/file-system/security/keys/2/replace")
    assert set(body) == {"replacement_key", "old_key_verification_signature", "replacement_key_verification_signature"}
    assert body["replacement_key"] == keys["new"].public_key
    assert keys["legal"].verifies(CHALLENGE, body["old_key_verification_signature"])
    assert keys["new"].verifies(CHALLENGE, body["replacement_key_verification_signature"])
    assert (result["diff"]["before"]["public_key"], result["diff"]["after"]["public_key"]) == (keys["legal"].public_key, keys["new"].public_key)


def test_a_replace_with_a_new_comment_patches_it_after_reading_the_etag_again(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", body=ANY, if_match=ETAG, response=stored(keys["new"]))
    fake.expect("GET", "/v1/file-system/security/keys/2", response=stored(keys["new"]), etag='"AgAAAA"')
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"comment": "2027"}, if_match='"AgAAAA"', response=stored(keys["new"], comment="2027"))
    fake.expect("GET", KEY_PATH, response=stored(keys["new"], comment="2027"), etag='"AwAAAA"')

    result = run(run_module, public_key=keys["new"].public_key, private_key=keys["new"].pem, old_private_key=keys["legal"].pem, comment="2027")

    assert not result.get("failed"), result.get("msg")
    assert result["security_key"]["comment"] == "2027"


def test_a_lost_replace_with_a_new_comment_that_took_is_noted_and_the_comment_patched(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, response=stored(keys["new"]), etag='"AgAAAA"')
    fake.expect("PATCH", "/v1/file-system/security/keys/2", body={"comment": "2027"}, if_match='"AgAAAA"', response=stored(keys["new"], comment="2027"))
    fake.expect("GET", KEY_PATH, response=stored(keys["new"], comment="2027"), etag='"AwAAAA"')

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem, comment="2027")

    assert not result.get("failed"), result.get("msg")
    assert result["security_key"]["comment"] == "2027"
    assert "the replace was confirmed by reading back after a lost connection" in result["notes"]


def test_a_lost_replace_with_a_new_comment_that_did_not_take_sends_no_patch(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem, comment="2027")

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert "PATCH /v1/file-system/security/keys/2" not in writes(fake)


def test_a_patch_refused_after_a_replace_fails_as_changed_naming_the_replace(fake_api, run_module, keys):
    """The replace took, so the task changed the key even though the comment was not written."""
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", body=ANY, if_match=ETAG, response=stored(keys["new"]))
    fake.expect("GET", "/v1/file-system/security/keys/2", response=stored(keys["new"]), etag='"AgAAAA"')
    fake.expect("PATCH", "/v1/file-system/security/keys/2", status=412, response=error("db_revision_mismatch_error"))

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem, comment="2027")

    assert result["failed"] is True
    assert result["changed"] is True
    assert "already replaced the key's public key" in result["msg"]


def test_a_patch_refused_after_a_create_fails_as_changed_naming_the_create(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)
    fake.expect("POST", "/v1/file-system/security/keys/", body=ANY, response=stored(keys["legal"]))
    fake.expect("GET", "/v1/file-system/security/keys/2", response=stored(keys["legal"]), etag=ETAG)
    fake.expect("PATCH", "/v1/file-system/security/keys/2", status=400, response=error("fs_api_key_store_key_in_use_error"))

    result = run(run_module, private_key=keys["legal"].pem, enabled=False)

    assert result["failed"] is True
    assert result["changed"] is True
    assert "already registered the key" in result["msg"]


def test_a_lost_replace_whose_read_back_fails_fails_as_changed_and_maybe_applied(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, status=500, response=error("internal_error"))  # retry_timeout 0: asked once

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem, comment="2027", retry_timeout=0)

    assert result["failed"] is True
    assert result["changed"] is True
    assert result["maybe_applied"] is True
    assert "reading back failed" in result["msg"]
    assert "PATCH /v1/file-system/security/keys/2" not in writes(fake)


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_different_public_key_without_old_private_key_is_refused(fake_api, run_module, keys, check_mode):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, check_mode=check_mode, private_key=keys["new"].pem)

    assert result["failed"] is True
    assert "old_private_key" in result["msg"]
    assert key_requests(fake) == [f"GET {KEY_PATH}"]


def test_a_different_public_key_with_only_a_public_key_names_both_private_keys(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, public_key=keys["new"].public_key)

    assert result["failed"] is True
    assert "old_private_key" in result["msg"] and "and private_key" in result["msg"]


def test_an_old_private_key_that_does_not_match_asks_for_no_challenge(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["other"].pem)

    assert result["failed"] is True
    assert "old_private_key does not match" in result["msg"]
    assert key_requests(fake) == [f"GET {KEY_PATH}"]


def test_a_replace_in_check_mode_checks_both_keys_and_asks_for_no_challenge(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, check_mode=True, diff=True, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["diff"]["after"]["public_key"] == keys["new"].public_key
    assert key_requests(fake) == [f"GET {KEY_PATH}"]


def test_old_private_key_is_not_used_once_the_key_holds_the_declared_public_key(fake_api, run_module, keys):
    """A rerun of the replace finds the new key, though old_private_key no longer matches the store."""
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["new"]), etag=ETAG)

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_a_lost_replace_that_reading_back_shows_is_reconciled(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, response=stored(keys["new"]), etag='"AgAAAA"')

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert "write reconciled after a lost connection" in result["notes"]


def test_a_lost_replace_that_did_not_take_fails_as_maybe_applied_and_is_not_resent(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert writes(fake).count("POST /v1/file-system/security/keys/2/replace") == 1


def test_the_key_files_are_readable_by_their_user_only_and_removed(fake_api, run_module, keys, monkeypatch):
    key_files = spy_key_files(monkeypatch)
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/key-replacement-challenge", response={"challenge": CHALLENGE})
    fake.expect("POST", "/v1/file-system/security/keys/2/replace", body=ANY, if_match=ETAG, response=stored(keys["new"]))
    fake.expect("GET", KEY_PATH, response=stored(keys["new"]), etag='"AgAAAA"')

    result = run(run_module, private_key=keys["new"].pem, old_private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert key_files, "the module wrote no key file for openssl"
    assert [mode for _path, mode in key_files] == [0o600] * len(key_files)
    assert [path for path, _mode in key_files if os.path.exists(path)] == []


def test_an_encrypted_private_key_is_refused_before_any_request(fake_api, run_module, keys, tmp_path):
    encrypted = tmp_path / "encrypted.pem"
    subprocess.run(
        ["openssl", "pkey", "-in", str(keys["legal"].pem_path), "-aes256", "-passout", "pass:secret-phrase", "-out", str(encrypted)],
        check=True,
        capture_output=True,
    )
    fake = fake_api()

    result = run(run_module, private_key=encrypted.read_text())

    assert result["failed"] is True
    assert "private_key is encrypted" in result["msg"]
    assert key_requests(fake) == []


# Removing


def test_a_key_nothing_uses_is_deleted_by_its_id(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/usages?limit=1000", response=usages())
    fake.expect("DELETE", "/v1/file-system/security/keys/2", response=None)

    result = run(run_module, state="absent", diff=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["security_key"] == {}
    assert result["diff"]["before"]["public_key"] == keys["legal"].public_key


def test_a_missing_key_is_already_absent(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_key_in_use_is_not_deleted_and_each_use_is_named(fake_api, run_module, keys, check_mode):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect(
        "GET",
        "/v1/file-system/security/keys/2/usages?limit=1000",
        response={"usages": [{"type": "snapshot_policy", "id": "1"}], "paging": {"next": "/v1/file-system/security/keys/2/usages?after=9&limit=1000"}},
    )
    fake.expect("GET", "/v1/file-system/security/keys/2/usages?after=9&limit=1000", response=usages(("snapshot", "12")))

    result = run(run_module, check_mode=check_mode, state="absent")

    assert result["failed"] is True
    assert "snapshot 12, snapshot_policy 1" in result["msg"]
    assert 'lock_key: "" on qumulo.cluster.snapshot_policy' in result["msg"]
    assert writes(fake) == []


def test_a_lost_delete_that_reading_back_shows_is_reconciled(fake_api, run_module, keys):
    fake = fake_api()
    fake.expect("GET", KEY_PATH, response=stored(keys["legal"]), etag=ETAG)
    fake.expect("GET", "/v1/file-system/security/keys/2/usages?limit=1000", response=usages())
    fake.expect("DELETE", "/v1/file-system/security/keys/2", raises=ConnectionResetError())
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert "write reconciled after a lost connection" in result["notes"]


def test_a_name_the_cluster_reads_as_another_key_is_refused_and_nothing_is_removed(fake_api, run_module, keys):
    """A key_ref the cluster might read as an id must never change or remove the key it names."""
    fake = fake_api()
    fake.expect("GET", "/v1/file-system/security/keys/%202", response={**stored(keys["legal"]), "name": "legal-hold"}, etag=ETAG)

    result = run_module(security_key, {**CONNECTION, "name": " 2", "state": "absent"})

    assert result["failed"] is True
    assert "named key 2" in result["msg"]
    assert writes(fake) == []


def test_removal_with_a_private_key_in_the_task_needs_no_openssl(fake_api, run_module, keys, monkeypatch):
    monkeypatch.setattr(AnsibleModule, "get_bin_path", lambda self, name, *args, **kwargs: None)
    fake = fake_api()
    fake.expect("GET", KEY_PATH, status=404, response=MISSING)

    result = run(run_module, state="absent", private_key=keys["legal"].pem)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
