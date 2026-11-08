# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.kerberos_keytab, driven through FakeQumulo. Bodies follow the OpenAPI document's kerberos_keytab;
every keytab is built in the test with made-up keys."""

from __future__ import annotations

import json

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import kerberos_keytab
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_kerberos import AES128, AES128_NAME, b64, entry, keytab, record, shown

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
KEYTAB = "/v1/auth/kerberos-keytab"
SETTINGS = "/v1/auth/kerberos-settings"
KEY = b"\x01the aes256 key of version 3!!\xff"
ROTATED_KEY = b"\x02the aes256 key of version 4!!\xff"
FILE = keytab(record(key=KEY))
HELD = {"entries": [entry(key=KEY)]}
ON = {"use_alt_security_identities_mapping": True}
OFF = {"use_alt_security_identities_mapping": False}


AD_STATUS = "/v1/ad/status"


def outside(fake):
    """fake, answering that the cluster is in no domain, for any keytab write's check (G-KRB-AD)."""
    return fake.serve(AD_STATUS, {"status": "NOT_IN_DOMAIN", "domain": ""}, optional=True)


def writes(fake):
    return [e for e in fake.log if e.method != "GET" and not e.background]


def assert_no_secret(result, *secrets):
    """Neither a key, as bytes' base64, nor the base64 content given, appears anywhere in the result."""
    text = json.dumps(result)
    for secret in (KEY, ROTATED_KEY, FILE, *secrets):
        assert b64(secret) not in text
    assert "key" not in {k for e in result.get("kerberos_keytab", {}).get("entries", []) for k in e}


def test_install_puts_the_file_without_if_match_and_returns_entries_without_keys(fake_api, run_module):
    """QUIRK-160: the keytab GET's ETag is constant, and a write with it answers 412."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None, etag='"AAAAAA"')
    fake.expect("PUT", KEYTAB, body=FILE, if_match=NO_HEADER, response=HELD)
    fake.expect("GET", KEYTAB, response=HELD, etag='"AAAAAA"')

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["kerberos_keytab"] == {"entries": [shown()]}
    assert result["diff"]["before"] == {"entries": []}
    assert result["diff"]["after"] == {"entries": [shown()]}
    assert writes(fake)[0].headers["Content-Type"] == "application/octet-stream"
    assert_no_secret(result)


def test_every_key_is_registered_for_masking(fake_api, run_module, monkeypatch):
    """The keys of the given keytab and of the cluster's, as the client registers a session token."""
    from ansible_collections.qumulo.cluster.plugins.module_utils import kerberos

    registered = []
    monkeypatch.setattr(kerberos, "register_secret", registered.append)
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response={"entries": [entry(key=ROTATED_KEY)]})
    fake.expect("PUT", KEYTAB, response=HELD)
    fake.expect("GET", KEYTAB, response=HELD)

    run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert {b64(KEY), b64(ROTATED_KEY)} <= set(registered)


def test_the_same_keytab_sends_no_write(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD, etag='"AAAAAA"')

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["changed"] is False, result.get("msg")
    assert result["kerberos_keytab"] == {"entries": [shown()]}
    assert writes(fake) == []


def test_entries_in_another_order_are_the_same_keytab(fake_api, run_module):
    second = record(enctype=AES128, key=b"q" * 16)
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response={"entries": [entry(enctype=AES128_NAME, key=b"q" * 16), entry(key=KEY)]})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(keytab(record(key=KEY), second))})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_a_path_on_the_controller_is_the_same_keytab(fake_api, run_module, tmp_path):
    (tmp_path / "edge01.keytab").write_bytes(FILE)
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab_path": str(tmp_path / "edge01.keytab")})

    assert result["changed"] is False, result.get("msg")


def test_check_mode_reports_the_install_and_sends_no_write(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)}, check_mode=True)

    assert result["changed"] is True
    assert result["kerberos_keytab"] == {"entries": [shown()]}
    assert writes(fake) == []


def test_a_rotation_puts_the_new_keytab_and_diffs_the_versions(fake_api, run_module):
    rotated = keytab(record(version=4, key=ROTATED_KEY))
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("PUT", KEYTAB, body=rotated, response={"entries": [entry(version=4, key=ROTATED_KEY)]})
    fake.expect("GET", KEYTAB, response={"entries": [entry(version=4, key=ROTATED_KEY)]})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(rotated)}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"] == {"entries": [shown()]}
    assert result["diff"]["after"] == {"entries": [shown(version=4)]}
    assert result["diff"]["after_header"] == "changed, not shown: keys"
    assert_no_secret(result, rotated)


def test_other_keys_alone_are_a_change_the_diff_names_without_showing(fake_api, run_module):
    """Principals, versions and enctypes alike, keys different: compared through the digest (spec Decision 3)."""
    other = keytab(record(key=ROTATED_KEY))
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("PUT", KEYTAB, body=other, response={"entries": [entry(key=ROTATED_KEY)]})
    fake.expect("GET", KEYTAB, response={"entries": [entry(key=ROTATED_KEY)]})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(other)}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"] == result["diff"]["after"] == {"entries": [shown()]}
    assert result["diff"]["after_header"] == "changed, not shown: keys"
    assert_no_secret(result, other)


def test_a_version_above_255_warns_and_compares_by_its_low_8_bits(fake_api, run_module):
    """QUIRK-162: the cluster keeps the 8-bit field, so the held 44 is the given 300."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response={"entries": [entry(version=44, key=KEY)]})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(keytab(record(version=300, full_version=300, key=KEY)))})

    assert result["changed"] is False, result.get("msg")
    assert result["kerberos_keytab"] == {"entries": [shown(version=44)]}
    assert any("key version 300" in w and "low 8 bits, 44" in w and "QUIRK-162" in w for w in result["warnings"])


def test_absent_deletes_and_turns_the_mapping_back_on(fake_api, run_module):
    """QUIRK-161: the DELETE turns use_alt_security_identities_mapping off."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON, etag='"vP2ZNQ"')
    fake.expect("DELETE", KEYTAB, if_match=NO_HEADER)
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, body=ON, if_match='"AAAAAA"', response=ON)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["kerberos_keytab"] == {"entries": []}
    assert result["diff"] == {"before": {"entries": [shown()]}, "after": {"entries": []}}
    assert result["notes"] == ["the keytab's DELETE turned use_alt_security_identities_mapping off (QUIRK-161); turned it back on"]
    assert_no_secret(result)


def test_absent_with_the_mapping_off_only_deletes(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=OFF)
    fake.expect("DELETE", KEYTAB)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert "notes" not in result


def test_absent_fails_with_changed_when_the_mapping_cannot_be_turned_back_on(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON)
    fake.expect("DELETE", KEYTAB)
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, status=403, response={"error_class": "permission_denied_error", "description": "no"})

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "turned use_alt_security_identities_mapping off (QUIRK-161), and turning it back on failed" in result["msg"]


def test_a_lost_delete_is_reconciled_and_the_mapping_turned_back_on(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON)
    fake.expect("DELETE", KEYTAB, raises=ConnectionResetError("reset"))
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, body=ON, response=ON)
    fake.expect("GET", KEYTAB, response=None)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"][0].endswith("turned it back on")
    assert "write reconciled after a lost connection" in result["notes"]


def test_a_lost_delete_that_left_the_keytab_fails_maybe_applied_and_touches_no_setting(fake_api, run_module):
    """The setting was not turned off, so nothing is put back; a rerun deletes, and then puts it back."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON)
    fake.expect("DELETE", KEYTAB, status=502)
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", KEYTAB, response=HELD)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert writes(fake) == [e for e in writes(fake) if e.method == "DELETE"]


def test_a_failed_read_back_after_the_delete_names_the_setting(fake_api, run_module):
    """A rerun finds no keytab and puts nothing back, so the failure says the setting may be off."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON)
    fake.expect("DELETE", KEYTAB)
    fake.expect("GET", KEYTAB, status=403, response={"error_class": "permission_denied_error", "description": "no"})

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["failed"] is True
    assert result["changed"] is True
    assert "may have turned use_alt_security_identities_mapping off (QUIRK-161), and reading the keytab back failed" in result["msg"]


def test_a_412_on_the_put_back_reads_the_setting_again(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON)
    fake.expect("DELETE", KEYTAB)
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, if_match='"AAAAAA"', status=412, response={"error_class": "db_revision_mismatch_error"})
    fake.expect("GET", SETTINGS, response=ON, etag='"WllTVQ"')

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert "notes" not in result


@pytest.mark.parametrize("held", [None, {"entries": []}], ids=["null", "no entries"])
def test_absent_with_no_keytab_sends_nothing(fake_api, run_module, held):
    """A formed cluster answers null; one whose keytab a file with no entries emptied answers no entries (research R2)."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=held)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["changed"] is False, result.get("msg")
    assert result["kerberos_keytab"] == {"entries": []}
    assert writes(fake) == []


def test_absent_ignores_a_keytab_it_is_given_and_does_not_read_it(fake_api, run_module):
    """As local_user ignores a password with state: absent, so that an inventory can keep the vaulted keytab."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=OFF)
    fake.expect("DELETE", KEYTAB)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent", "keytab": "not even base64!"})

    assert result["changed"] is True, result.get("msg")


def test_a_lost_put_back_of_the_mapping_fails_maybe_applied_naming_the_setting(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)
    fake.expect("GET", SETTINGS, response=ON)
    fake.expect("DELETE", KEYTAB)
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("GET", SETTINGS, response=OFF, etag='"AAAAAA"')
    fake.expect("PUT", SETTINGS, raises=ConnectionResetError("reset"))

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"})

    assert result["failed"] is True
    assert (result["changed"], result["maybe_applied"]) == (True, True)
    assert "turning it back on failed" in result["msg"]


def test_absent_in_check_mode_sends_no_write(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=HELD)

    result = run_module(kerberos_keytab, {**CONNECTION, "state": "absent"}, check_mode=True)

    assert result["changed"] is True
    assert writes(fake) == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({}, "state is present but any of the following are missing: keytab, keytab_path"),
        ({"keytab": "AAAA", "keytab_path": "/x.keytab"}, "parameters are mutually exclusive: keytab|keytab_path"),
        ({"keytab": "not base64!"}, "keytab is not base64"),
        ({"keytab": b64(b"\x05\x02")}, "keytab holds no entry; to remove the cluster's keytab, use state: absent"),
        ({"keytab": b64(keytab(record(enctype=26)))}, "has encryption type 26, which the cluster does not take"),
    ],
)
def test_an_input_the_module_refuses_fails_before_any_request(fake_api, run_module, args, message):
    fake = outside(fake_api())

    result = run_module(kerberos_keytab, {**CONNECTION, **args})

    assert result["failed"] is True
    assert message in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_vaulted_path_fails_before_any_request(fake_api, run_module, tmp_path):
    (tmp_path / "edge01.keytab").write_bytes(b"$ANSIBLE_VAULT;1.1;AES256\n3132\n")
    fake = outside(fake_api())

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab_path": str(tmp_path / "edge01.keytab")})

    assert result["failed"] is True
    assert "encrypted with Ansible Vault" in result["msg"]
    assert [e for e in fake.log if not e.background] == []


def test_a_refused_put_reports_status_and_class_without_the_description(fake_api, run_module):
    """QUIRK-163: an error answer to the keytab PUT can quote the keys sent."""
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, status=400, response={"error_class": "decode_error", "description": f'value \'[{{"key": "{b64(KEY)}"}}]\''})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["failed"] is True
    assert result["msg"] == "PUT /v1/auth/kerberos-keytab failed: HTTP 400 decode_error: the cluster refused the keytab"
    assert (result["status"], result["error_class"]) == (400, "decode_error")
    assert_no_secret(result)


@pytest.mark.parametrize(
    ("status", "error_class", "reason"),
    [
        (409, "auth_joined_to_ad_error", "the cluster is joined to a domain, and a joined cluster takes no keytab"),
        (403, "access_denied_error", "the API user lacks PRIVILEGE_KERBEROS_KEYTAB_WRITE"),
    ],
    ids=["a join between the read and the PUT", "no keytab write privilege"],
)
def test_a_refused_put_names_what_the_class_or_status_says_without_the_description(fake_api, run_module, status, error_class, reason):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, status=status, response={"error_class": error_class, "description": f"quoting {b64(KEY)}"})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["failed"] is True
    assert reason in result["msg"]
    assert_no_secret(result)


def test_a_parse_error_says_the_cluster_could_not_read_the_keytab(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, status=400, response={"error_class": "keytab_parse_error", "description": "System error"})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["msg"].endswith("keytab_parse_error: the cluster could not read the keytab")


def test_a_lost_put_that_the_read_back_confirms_is_a_change_with_a_note(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, raises=ConnectionResetError("reset"))
    fake.expect("GET", KEYTAB, response=HELD)

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["changed"] is True, result.get("msg")
    assert result["notes"] == ["write reconciled after a lost connection"]


def test_a_lost_put_that_the_read_back_does_not_confirm_fails_maybe_applied(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, raises=ConnectionResetError("reset"))
    fake.expect("GET", KEYTAB, response=None)

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["failed"] is True
    assert result["maybe_applied"] is True
    assert_no_secret(result)


def test_a_read_back_with_other_keys_fails_without_showing_them(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, response=HELD)
    fake.expect("GET", KEYTAB, response={"entries": [entry(key=ROTATED_KEY)]})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["failed"] is True
    assert result["msg"] == "the Kerberos keytab did not converge on keys"
    assert (result["expected"], result["observed"]) == ({}, {})
    assert_no_secret(result)


def test_a_read_back_with_other_entries_fails_naming_them_without_keys(fake_api, run_module):
    fake = outside(fake_api())
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("PUT", KEYTAB, response=HELD)
    fake.expect("GET", KEYTAB, response={"entries": [entry(version=9, key=KEY)]})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["failed"] is True
    assert result["expected"] == {"entries": [shown()]}
    assert result["observed"] == {"entries": [shown(version=9)]}
    assert_no_secret(result)


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check mode"])
def test_a_keytab_write_on_a_joined_cluster_fails_before_it_is_sent(fake_api, run_module, check_mode):
    """G-KRB-AD: a joined cluster answers a keytab PUT with 409 auth_joined_to_ad_error."""
    fake = fake_api()
    fake.expect("GET", KEYTAB, response=None)
    fake.expect("GET", AD_STATUS, response={"status": "JOINED_TO_DOMAIN", "domain": "corp.example.com"})

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)}, check_mode=check_mode)

    assert result["failed"] is True
    assert "joined to corp.example.com" in result["msg"] and "G-KRB-AD" in result["msg"]
    assert writes(fake) == []


def test_a_keytab_that_needs_no_write_reads_no_domain_status(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", KEYTAB, response=HELD)

    result = run_module(kerberos_keytab, {**CONNECTION, "keytab": b64(FILE)})

    assert result["changed"] is False, result.get("msg")
