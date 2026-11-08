# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.ad_domain, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import json
from urllib.error import URLError

from ansible_collections.qumulo.cluster.plugins.modules import ad_domain
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
STATUS, MONITOR = "/v1/ad/status", "/v1/ad/monitor"
JOIN, RECONFIGURE, LEAVE = "/v1/ad/join", "/v1/ad/reconfigure", "/v1/ad/leave"
CREDENTIALS = {"join_user": "svc-qjoin", "join_password": "join-pw"}
OU = "OU=Edges,DC=corp,DC=example,DC=com"
NO_ERROR = {"module": "", "error_class": "", "description": "", "stack": "", "user_visible": False}
BEFORE, AFTER = "2026-10-30T00:00:00Z", "2026-10-31T00:00:00Z"


def status(joined=True, domain="corp.example.com", **changes):
    base = {
        "status": "JOINED_TO_DOMAIN" if joined else "NOT_IN_DOMAIN",
        "domain": domain if joined else "",
        "ou": OU if joined else "",
        "search_trusted_domains": False,
        "use_ad_posix_attributes": False,
        "base_dn": "CN=Users,DC=corp,DC=example,DC=com" if joined else "",
        "domain_netbios": "CORP" if joined else "",
        "dcs": [],
    }
    return {**base, **changes}


def monitor(state="JOIN_SUCCEEDED", domain="corp.example.com", dcs=(), error="", when=BEFORE):
    return {
        "status": state,
        "last_error": {**NO_ERROR, "error_class": error, "description": f"{error} happened" if error else ""},
        "last_action_time": when,
        "domain": domain,
        "configured_dcs": list(dcs),
    }


def expect_read(fake, ad_status, ad_monitor):
    fake.expect("GET", STATUS, response=ad_status)
    fake.expect("GET", MONITOR, response=ad_monitor)


def writes(fake):
    return [f"{e.method} {e.path}" for e in fake.log if e.method == "POST" and not e.background]


def test_unjoined_cluster_joins_then_waits_for_the_join(fake_api, run_module, fake_clock):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))
    body = {
        "domain": "corp.example.com",
        "domain_netbios": "",
        "user": "svc-qjoin",
        "password": "join-pw",
        "ou": OU,
        "use_ad_posix_attributes": False,
        "base_dn": "",
    }
    fake.expect("POST", JOIN, body=body, status=202, response={"monitor_uri": "/v1/ad/monitor"})
    fake.expect("GET", MONITOR, response=monitor("JOIN_IN_PROGRESS", when=AFTER))
    fake.expect("GET", MONITOR, response=monitor(when=AFTER))
    expect_read(fake, status(), monitor(when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com", "ou": OU})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["ad_domain"]["status"] == "JOINED_TO_DOMAIN"
    assert fake_clock.sleeps == [2.0]
    assert "join-pw" not in json.dumps(result)


def test_joined_to_the_domain_is_unchanged_whatever_its_case(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor(domain="CORP.EXAMPLE.COM"))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "Corp.Example.Com", "ou": "ou=edges, dc=corp, dc=example, dc=com"})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_joining_needs_credentials(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com"})

    assert result["failed"] is True
    assert "joining corp.example.com needs join_user and join_password" in result["msg"]
    assert writes(fake) == []


def test_failed_join_reports_the_cluster_s_reason(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))
    fake.expect("POST", JOIN, status=202)
    fake.expect("GET", MONITOR, response=monitor("JOIN_FAILED", error="AD_DOMAIN_COMPUTER_ACCOUNT_ALREADY_EXISTS", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "joining corp.example.com failed: AD_DOMAIN_COMPUTER_ACCOUNT_ALREADY_EXISTS" in result["msg"]
    assert "G-AD-JOIN" in result["msg"]


def test_join_failed_for_another_reason_does_not_blame_a_computer_account(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))
    fake.expect("POST", JOIN, status=202)
    fake.expect("GET", MONITOR, response=monitor("JOIN_FAILED", error="AD_LOGON_FAILURE", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "joining corp.example.com failed: AD_LOGON_FAILURE" in result["msg"]
    assert "computer account" not in result["msg"]


def test_lost_connection_after_the_join_waits_for_the_monitor(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))
    fake.expect("POST", JOIN, raises=URLError(ConnectionResetError(104, "Connection reset by peer")))
    fake.expect("GET", MONITOR, response=monitor(when=AFTER))
    expect_read(fake, status(), monitor(when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com"})

    assert result["changed"] is True, result.get("msg")
    assert writes(fake) == [f"POST {JOIN}"]


def test_join_that_does_not_finish_in_time_fails_reporting_the_change(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))
    fake.expect("POST", JOIN, status=202)
    for _poll in range(6):
        fake.expect("GET", MONITOR, response=monitor("JOIN_IN_PROGRESS", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com", "wait_timeout": 10})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "still JOIN_IN_PROGRESS after 10s" in result["msg"]


def test_joined_to_another_domain_fails_naming_the_leave(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(domain="other.example.com"), monitor(domain="other.example.com"))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com"})

    assert result["failed"] is True
    assert "joined to other.example.com, not corp.example.com" in result["msg"]
    assert "state=absent" in result["msg"]


def test_different_ou_on_a_joined_cluster_fails_since_only_a_join_sets_it(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com", "ou": "OU=Other,DC=corp,DC=example,DC=com"})

    assert result["failed"] is True
    assert "ou is set only by a join" in result["msg"]
    assert writes(fake) == []


def test_in_place_settings_are_changed_by_reconfigure_without_credentials(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor(dcs=["10.0.0.2"]))
    fake.expect("POST", RECONFIGURE, body={"search_trusted_domains": True, "domain_controllers": "10.0.0.2,10.0.0.3"}, status=202)
    fake.expect("GET", MONITOR, response=monitor(dcs=["10.0.0.2", "10.0.0.3"], when=AFTER))
    expect_read(fake, status(search_trusted_domains=True), monitor(dcs=["10.0.0.2", "10.0.0.3"], when=AFTER))

    args = {"domain": "corp.example.com", "search_trusted_domains": True, "domain_controllers": ["10.0.0.2", "10.0.0.3"]}
    result = run_module(ad_domain, {**CONNECTION, **args}, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["before"]["domain_controllers"] == ["10.0.0.2"]
    assert result["diff"]["after"]["domain_controllers"] == ["10.0.0.2", "10.0.0.3"]


def test_reconfigure_that_the_cluster_reverts_fails_with_its_reason(fake_api, run_module):
    """A failed reconfigure goes back to JOIN_SUCCEEDED; only a new last_error tells."""
    fake = fake_api()
    expect_read(fake, status(), monitor())
    fake.expect("POST", RECONFIGURE, status=202)
    fake.expect("GET", MONITOR, response=monitor(error="ad_base_dn_validation_error", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "base_dn": "CN=Nope,DC=corp,DC=example,DC=com"})

    assert (result["failed"], result["changed"]) == (True, True)
    assert "changing the settings of corp.example.com failed: ad_base_dn_validation_error" in result["msg"]


def test_reconfigure_ignores_the_last_error_of_an_earlier_operation(fake_api, run_module):
    """The monitor keeps the last error until another failure; only a new last_action_time makes it this operation's."""
    fake = fake_api()
    expect_read(fake, status(), monitor(error="ad_base_dn_validation_error"))
    fake.expect("POST", RECONFIGURE, body={"search_trusted_domains": True}, status=202)
    fake.expect("GET", MONITOR, response=monitor(error="ad_base_dn_validation_error"))
    expect_read(fake, status(search_trusted_domains=True), monitor(error="ad_base_dn_validation_error"))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "search_trusted_domains": True})

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True


def test_check_mode_predicts_a_reconfigure_and_sends_nothing(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "use_ad_posix_attributes": True}, check_mode=True, diff=True)

    assert result["changed"] is True, result.get("msg")
    assert result["diff"]["after"]["use_ad_posix_attributes"] is True
    assert writes(fake) == []


def test_empty_base_dn_matches_the_default_the_cluster_stores(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "base_dn": ""})

    assert result["changed"] is False, result.get("msg")
    assert writes(fake) == []


def test_domain_controllers_of_another_operation_are_not_compared(fake_api, run_module):
    """The monitor describes another domain, so the configured DCs cannot be read (G-AD-DCS)."""
    fake = fake_api()
    expect_read(fake, status(), monitor(domain="other.example.com", dcs=["10.9.9.9"]))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "domain_controllers": ["10.0.0.2"]})

    assert result["changed"] is False, result.get("msg")
    assert any("G-AD-DCS" in note for note in result["notes"])


def test_base_dn_compares_without_case_or_spaces(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(base_dn="CN=Users,DC=corp,DC=example,DC=com;OU=Svc,DC=corp,DC=example,DC=com"), monitor())

    result = run_module(
        ad_domain, {**CONNECTION, "domain": "corp.example.com", "base_dn": "cn=users,dc=corp,dc=example,dc=com; ou=svc,dc=corp,dc=example,dc=com"}
    )

    assert result["changed"] is False, result.get("msg")


def test_check_mode_predicts_a_join_and_sends_nothing(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com"}, check_mode=True, diff=True)

    assert result["changed"] is True
    assert result["diff"]["before"]["status"] == "NOT_IN_DOMAIN"
    assert result["diff"]["after"]["status"] == "JOINED_TO_DOMAIN"
    assert result["diff"]["after"]["domain"] == "corp.example.com"
    assert result["diff"]["after"]["search_trusted_domains"] is True  # the server's default when the join leaves it out
    assert result["diff"]["after"]["base_dn"] == "CN=Users,DC=corp,DC=example,DC=com"
    assert writes(fake) == []


def test_operation_in_progress_is_waited_out_first(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("JOIN_IN_PROGRESS"))
    fake.expect("GET", MONITOR, response=monitor())
    expect_read(fake, status(), monitor())

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com"})

    assert result["changed"] is False, result.get("msg")


def test_state_absent_leaves_with_credentials(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())
    fake.expect("POST", LEAVE, body={"domain": "corp.example.com", "user": "svc-qjoin", "password": "join-pw"}, status=202)
    fake.expect("GET", MONITOR, response=monitor("LEAVE_SUCCEEDED", domain="", when=AFTER))
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain="", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com", "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert result["ad_domain"]["status"] == "NOT_IN_DOMAIN"
    assert result["warnings"] == []


def test_state_absent_without_credentials_warns_about_the_computer_account(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())
    fake.expect("POST", LEAVE, body={"domain": "corp.example.com", "user": "", "password": ""}, status=202)
    fake.expect("GET", MONITOR, response=monitor("LEAVE_SUCCEEDED", domain="", when=AFTER))
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain="", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert any("computer account stays in Active Directory" in warning for warning in result["warnings"])


def test_leave_that_could_not_delete_the_account_warns_with_the_reason(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())
    fake.expect("POST", LEAVE, status=202)
    fake.expect("GET", MONITOR, response=monitor("LEAVE_SUCCEEDED", domain="", error="ad_insufficient_rights_error", when=AFTER))
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain="", when=AFTER))

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com", "state": "absent"})

    assert result["changed"] is True, result.get("msg")
    assert any("ad_insufficient_rights_error" in warning for warning in result["warnings"])


def test_check_mode_predicts_a_leave_and_sends_nothing(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(), monitor())

    result = run_module(ad_domain, {**CONNECTION, **CREDENTIALS, "domain": "corp.example.com", "state": "absent"}, check_mode=True)

    assert result["changed"] is True, result.get("msg")
    assert result["ad_domain"]["status"] == "NOT_IN_DOMAIN"
    assert writes(fake) == []


def test_state_absent_when_not_joined_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(joined=False), monitor("LEAVE_SUCCEEDED", domain=""))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "state": "absent"})

    assert result["changed"] is False
    assert writes(fake) == []


def test_state_absent_when_joined_elsewhere_is_unchanged_with_a_note(fake_api, run_module):
    fake = fake_api()
    expect_read(fake, status(domain="other.example.com"), monitor(domain="other.example.com"))

    result = run_module(ad_domain, {**CONNECTION, "domain": "corp.example.com", "state": "absent"})

    assert result["changed"] is False
    assert "joined to other.example.com, not corp.example.com" in result["notes"][0]
