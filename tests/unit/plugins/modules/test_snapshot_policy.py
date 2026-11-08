# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.snapshot_policy, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import snapshot_policy
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import ANY, CLUSTER_ID, NO_HEADER
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import attributes, attributes_path, error

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
STATUSES = "/v3/snapshots/policies/status/"
POLICIES = "/v3/snapshots/policies/"
WEEK = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
HOURLY = {
    "timezone": "UTC",
    "frequency": "SCHEDULE_HOURLY_OR_LESS",
    "on_days": WEEK,
    "window_start_hour": 0,
    "window_start_minute": 0,
    "window_end_hour": 23,
    "window_end_minute": 59,
    "fire_every_interval": "FIRE_IN_HOURS",
    "fire_every": 1,
}
DAILY = {"timezone": "UTC", "frequency": "SCHEDULE_DAILY_OR_WEEKLY", "hour": 2, "minute": 30, "on_days": ["MON", "FRI"]}
DIR_ID = "126542065494555443589290131459"
KEYS = "/v1/file-system/security/keys/"


def policy(policy_id=1, name="projects-hourly", schedule=None, ttl="7days", enabled=True, path="/data/projects/", file_id=DIR_ID):
    """GET /v3/snapshots/policies/{id} and, with source_file_path, an entry of the status list; the schedule defaults to HOURLY."""
    schedule = schedule or HOURLY
    return {
        "id": policy_id,
        "policy_name": name,
        "snapshot_name_template": "{ID}_{Policy}_{Directory}",
        "source_file_id": file_id,
        "source_file_path": path,
        "schedule": {"id": 1, "creation_schedule": dict(schedule), "expiration_time_to_live": ttl},
        "enabled": enabled,
        "lock_key_ref": None,
    }


def key(key_id, name, disabled=False):
    """A key as GET /v1/file-system/security/keys/{ref} answers it; the public key is not read here."""
    return {"id": key_id, "name": name, "comment": "", "disabled": disabled, "public_key": "MFkwEwexample"}


def sent(fake, method, path):
    return next(e.body for e in fake.log if e.method == method and e.path == path)


def by_id(stored):
    return {k: v for k, v in stored.items() if k != "source_file_path"}


def expect_found(fake, stored, etag='"p1"'):
    fake.expect("GET", STATUSES, response={"entries": [stored]})
    fake.expect("GET", f"{POLICIES}{stored['id']}", response=by_id(stored), etag=etag)


OPTIONS = {
    "policy_name": "projects-hourly",
    "path": "/data/projects",
    "enabled": True,
    "expiration_time_to_live": "7days",
    "snapshot_name_template": "{ID}_{Policy}_{Directory}",
    "schedule": HOURLY,
    "lock_key": None,
}
WANTED_HOURLY = {**HOURLY, "on_days": ["EVERY_DAY"]}
DESIRED = {"policy_name": "projects-hourly", "path": "/data/projects", "expiration_time_to_live": "7days", "schedule": WANTED_HOURLY}


def writes(fake):
    return [e for e in fake.log if e.method in ("POST", "PATCH", "DELETE") and not e.background]


def run(run_module, check_mode=False, diff=False, **args):
    return run_module(snapshot_policy, {**CONNECTION, **args}, check_mode=check_mode, diff=diff)


def test_absent_policy_is_created_with_the_directory_id(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": []})
    fake.expect("GET", attributes_path("/data/projects"), response=attributes("/data/projects", file_id=DIR_ID))
    fake.expect(
        "POST",
        POLICIES,
        body={
            "policy_name": "projects-hourly",
            "enabled": True,
            "source_file_id": DIR_ID,
            "schedule": {"creation_schedule": HOURLY, "expiration_time_to_live": "7days"},
        },
        response=by_id(policy()),
    )
    expect_found(fake, policy())

    result = run(run_module, **DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["snapshot_policy"] == OPTIONS


def test_matching_policy_is_unchanged_and_sends_no_write(fake_api, run_module):
    """EVERY_DAY is the seven days the server lists, and never is the empty TTL it stores."""
    fake = fake_api()
    expect_found(fake, policy(ttl=""))

    result = run(run_module, **{**DESIRED, "expiration_time_to_live": "never", "path": "/data/projects/"}, enabled=True)

    assert result["changed"] is False
    assert result["snapshot_policy"] == {**OPTIONS, "expiration_time_to_live": ""}
    assert writes(fake) == []


# QUIRK-006: any creation_schedule the server receives resets the schedule's anchor, so an unchanged one is never sent.
def test_unchanged_schedule_not_sent(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy(), etag='"p1"')
    fake.expect("PATCH", f"{POLICIES}1", body={"enabled": False}, if_match='"p1"', response=by_id(policy(enabled=False)))
    expect_found(fake, policy(enabled=False))

    result = run(run_module, **DESIRED, enabled=False)

    assert result["changed"] is True
    assert result["snapshot_policy"]["enabled"] is False


def test_changed_expiration_is_sent_without_the_schedule(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy(), etag='"p1"')
    fake.expect("PATCH", f"{POLICIES}1", body={"schedule": {"expiration_time_to_live": "14days"}}, response=by_id(policy(ttl="14days")))
    expect_found(fake, policy(ttl="14days"))

    result = run(run_module, **{**DESIRED, "expiration_time_to_live": "14days"})

    assert result["changed"] is True


def test_changed_schedule_is_sent_whole(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy(), etag='"p1"')
    fake.expect("PATCH", f"{POLICIES}1", body={"schedule": {"creation_schedule": DAILY}}, if_match='"p1"', response=by_id(policy(schedule=DAILY)))
    expect_found(fake, policy(schedule=DAILY))

    result = run(run_module, **{**DESIRED, "schedule": {**DAILY, "on_days": ["FRI", "MON", "MON"]}})

    assert result["changed"] is True
    assert result["snapshot_policy"]["schedule"] == DAILY


def test_schedule_fields_the_server_fills_in_are_not_drift(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy())

    result = run(run_module, policy_name="projects-hourly", schedule={"frequency": "SCHEDULE_HOURLY_OR_LESS", "fire_every": 1})

    assert result["changed"] is False
    assert result["snapshot_policy"]["schedule"] == HOURLY


def test_two_policies_with_the_name_fail_naming_both(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": [policy(), policy(policy_id=2, path="/data/other/", file_id="200")]})

    result = run(run_module, **DESIRED, state="absent")

    assert result["failed"] is True
    assert result["msg"] == (
        "snapshot policy 'projects-hourly' names more than one policy (1 on /data/projects/, 2 on /data/other/); rename all but one of them"
    )
    assert writes(fake) == []


def test_path_change_needs_recreate(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy())

    result = run(run_module, **{**DESIRED, "path": "/data/other"})

    assert result["failed"] is True
    assert result["msg"] == "snapshot policy 'projects-hourly': path cannot change in place; set recreate=true to delete and recreate it"
    assert writes(fake) == []


def test_path_change_with_recreate_deletes_and_creates(fake_api, run_module):
    moved = policy(policy_id=2, path="/data/other/", file_id="200")
    fake = fake_api()
    expect_found(fake, policy())
    fake.expect("DELETE", f"{POLICIES}1", if_match=NO_HEADER)
    fake.expect("GET", attributes_path("/data/other"), response=attributes("/data/other", file_id="200"))
    fake.expect(
        "POST",
        POLICIES,
        body={
            "policy_name": "projects-hourly",
            "enabled": True,
            "source_file_id": "200",
            "snapshot_name_template": "{ID}_{Policy}_{Directory}",
            "schedule": {"creation_schedule": HOURLY, "expiration_time_to_live": "7days"},
        },
        response=by_id(moved),
    )
    expect_found(fake, moved)

    result = run(run_module, **{**DESIRED, "path": "/data/other"}, recreate=True)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is True
    assert result["snapshot_policy"]["path"] == "/data/other"


def test_a_recreate_carries_the_lock_key_to_the_new_policy(fake_api, run_module):
    """The recreated policy is created with the key the old one held (spec 026, Decision 2)."""
    moved = {**policy(policy_id=2, path="/data/other/", file_id="200"), "lock_key_ref": "7"}
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))
    fake.expect("DELETE", f"{POLICIES}1", if_match=NO_HEADER)
    fake.expect("GET", attributes_path("/data/other"), response=attributes("/data/other", file_id="200"))
    fake.expect("POST", POLICIES, body=ANY, response=by_id(moved))
    expect_found(fake, moved)
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))

    result = run(run_module, **{**DESIRED, "path": "/data/other"}, recreate=True)

    assert not result.get("failed"), result.get("msg")
    assert sent(fake, "POST", POLICIES)["lock_key_ref"] == "7"
    assert result["snapshot_policy"]["lock_key"] == "legal-hold"


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_recreate_that_would_carry_a_disabled_key_is_refused_before_the_delete(fake_api, run_module, check_mode):
    """The cluster refuses a disabled key on the create, which would come after the delete."""
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold", disabled=True))

    result = run(run_module, check_mode=check_mode, **{**DESIRED, "path": "/data/other"}, recreate=True)

    assert result["failed"] is True
    assert "legal-hold" in result["msg"] and "disabled" in result["msg"]
    assert writes(fake) == []


def test_a_held_key_the_user_may_not_read_is_named_by_its_id_with_a_warning(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", status=403, response=error("access_denied_error"))

    result = run(run_module, **DESIRED)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert result["snapshot_policy"]["lock_key"] == "7"
    assert any("by id 7" in warning for warning in result["warnings"])


def test_a_recreate_carries_a_held_key_with_an_empty_name_by_its_id(fake_api, run_module):
    """QUIRK-120: a key stored with an empty name is named by its id, so the recreate still carries it."""
    moved = {**policy(policy_id=2, path="/data/other/", file_id="200"), "lock_key_ref": "6"}
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "6"})
    fake.expect("GET", f"{KEYS}6", response=key("6", ""))
    fake.expect("DELETE", f"{POLICIES}1", if_match=NO_HEADER)
    fake.expect("GET", attributes_path("/data/other"), response=attributes("/data/other", file_id="200"))
    fake.expect("POST", POLICIES, body=ANY, response=by_id(moved))
    expect_found(fake, moved)
    fake.expect("GET", f"{KEYS}6", response=key("6", ""))

    result = run(run_module, **{**DESIRED, "path": "/data/other"}, recreate=True)

    assert not result.get("failed"), result.get("msg")
    assert sent(fake, "POST", POLICIES)["lock_key_ref"] == "6"
    assert result["snapshot_policy"]["lock_key"] == "6"


def test_a_key_with_an_empty_name_named_by_its_id_is_set_not_taken_off(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))
    fake.expect("GET", f"{KEYS}6", response=key("6", ""))
    fake.expect("PATCH", f"{POLICIES}1", body={"lock_key_ref": "6"}, if_match='"p1"', response=by_id({**policy(), "lock_key_ref": "6"}))
    expect_found(fake, {**policy(), "lock_key_ref": "6"})
    fake.expect("GET", f"{KEYS}6", response=key("6", ""))

    result = run(run_module, **DESIRED, lock_key="6")

    assert not result.get("failed"), result.get("msg")
    assert result["snapshot_policy"]["lock_key"] == "6"


def test_a_rerun_naming_a_held_key_with_an_empty_name_by_its_id_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "6"})
    fake.expect("GET", f"{KEYS}6", response=key("6", ""))
    fake.expect("GET", f"{KEYS}6", response=key("6", ""))

    result = run(run_module, **DESIRED, lock_key="6")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_recreate_that_would_carry_a_key_the_user_may_not_read_is_refused_before_the_delete(fake_api, run_module, check_mode):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", status=403, response=error("access_denied_error"))

    result = run(run_module, check_mode=check_mode, **{**DESIRED, "path": "/data/other"}, recreate=True)

    assert result["failed"] is True
    assert "PRIVILEGE_FS_KEY_MANAGEMENT_READ" in result["msg"]
    assert writes(fake) == []


def test_a_new_policy_is_created_with_the_key_named(fake_api, run_module):
    created = {**policy(), "lock_key_ref": "7"}
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": []})
    fake.expect("GET", f"{KEYS}legal-hold", response=key("7", "legal-hold"))
    fake.expect("GET", attributes_path("/data/projects"), response=attributes("/data/projects", file_id=DIR_ID))
    fake.expect("POST", POLICIES, body=ANY, response=by_id(created))
    expect_found(fake, created)
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))

    result = run(run_module, **DESIRED, lock_key="legal-hold")

    assert not result.get("failed"), result.get("msg")
    assert sent(fake, "POST", POLICIES)["lock_key_ref"] == "7"
    assert result["snapshot_policy"]["lock_key"] == "legal-hold"


@pytest.mark.parametrize("ref", ["legal-hold", "7"], ids=["name", "id"])
def test_a_policy_holding_the_key_named_by_name_or_id_is_unchanged(fake_api, run_module, ref):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))
    fake.expect("GET", f"{KEYS}{ref}", response=key("7", "legal-hold"))

    result = run(run_module, **DESIRED, lock_key=ref)

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    assert writes(fake) == []


def test_another_key_is_patched_by_its_id_and_the_diff_shows_the_names(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))
    fake.expect("GET", f"{KEYS}archive", response=key("8", "archive"))
    fake.expect("PATCH", f"{POLICIES}1", body={"lock_key_ref": "8"}, if_match='"p1"', response=by_id({**policy(), "lock_key_ref": "8"}))
    expect_found(fake, {**policy(), "lock_key_ref": "8"})
    fake.expect("GET", f"{KEYS}8", response=key("8", "archive"))

    result = run(run_module, diff=True, **DESIRED, lock_key="archive")

    assert not result.get("failed"), result.get("msg")
    assert (result["diff"]["before"]["lock_key"], result["diff"]["after"]["lock_key"]) == ("legal-hold", "archive")


def test_an_empty_lock_key_takes_the_key_off(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))
    fake.expect("PATCH", f"{POLICIES}1", body={"lock_key_ref": None}, if_match='"p1"', response=by_id(policy()))
    expect_found(fake, policy())

    result = run(run_module, **DESIRED, lock_key="")

    assert not result.get("failed"), result.get("msg")
    assert result["snapshot_policy"]["lock_key"] is None


def test_an_unset_lock_key_keeps_the_policy_s_key(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold"))

    result = run(run_module, **DESIRED)

    assert result["changed"] is False
    assert result["snapshot_policy"]["lock_key"] == "legal-hold"


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_key_that_is_not_in_the_key_store_is_refused(fake_api, run_module, check_mode):
    fake = fake_api()
    expect_found(fake, policy())
    fake.expect("GET", f"{KEYS}legal-hold", status=404, response=error("fs_change_lock_key_store_no_such_key_error"))

    result = run(run_module, check_mode=check_mode, **DESIRED, lock_key="legal-hold")

    assert result["failed"] is True
    assert "holds no key legal-hold" in result["msg"] and "qumulo.cluster.security_key" in result["msg"]
    assert writes(fake) == []


@pytest.mark.parametrize("check_mode", [False, True], ids=["run", "check"])
def test_a_disabled_key_is_refused(fake_api, run_module, check_mode):
    fake = fake_api()
    expect_found(fake, policy())
    fake.expect("GET", f"{KEYS}legal-hold", response=key("7", "legal-hold", disabled=True))

    result = run(run_module, check_mode=check_mode, **DESIRED, lock_key="legal-hold")

    assert result["failed"] is True
    assert "legal-hold is disabled" in result["msg"]
    assert writes(fake) == []


def test_a_policy_already_holding_a_key_disabled_since_is_unchanged(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, {**policy(), "lock_key_ref": "7"})
    fake.expect("GET", f"{KEYS}7", response=key("7", "legal-hold", disabled=True))
    fake.expect("GET", f"{KEYS}legal-hold", response=key("7", "legal-hold", disabled=True))

    result = run(run_module, **DESIRED, lock_key="legal-hold")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": []})

    result = run(run_module, check_mode=True, **DESIRED)

    assert result["changed"] is True
    assert writes(fake) == []


def test_diff_returns_before_and_after(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy())

    result = run(run_module, check_mode=True, diff=True, policy_name="projects-hourly", expiration_time_to_live="never")

    assert result["diff"] == {"before": OPTIONS, "after": {**OPTIONS, "expiration_time_to_live": ""}}


@pytest.mark.parametrize("missing", ["path", "schedule"])
def test_create_without_path_or_schedule_fails(fake_api, run_module, missing):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": []})

    result = run(run_module, **{k: v for k, v in DESIRED.items() if k != missing})

    assert result["failed"] is True
    assert result["msg"] == "snapshot policy 'projects-hourly' does not exist; set path and schedule to create it"


def test_missing_directory_fails_on_create(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": []})
    fake.expect("GET", attributes_path("/data/projects"), status=404, response=error("fs_no_such_entry_error"))

    result = run(run_module, **DESIRED)

    assert result["failed"] is True
    assert result["msg"] == "/data/projects does not exist; create it first, for example with qumulo.cluster.directory"
    assert writes(fake) == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"policy_name": "a/b"}, "policy_name 'a/b' must be 1 to 50 characters without '/'"),
        ({"policy_name": "x" * 51}, f"policy_name '{'x' * 51}' must be 1 to 50 characters without '/'"),
        ({"policy_name": "p", "path": "data"}, "path 'data' must be an absolute path"),
    ],
    ids=["slash", "too_long", "relative_path"],
)
def test_bad_options_fail_before_any_request(fake_api, run_module, args, message):
    fake = fake_api()

    result = run(run_module, **args)

    assert result["failed"] is True
    assert result["msg"].startswith(message), result["msg"]
    assert fake.log == []


def test_policy_name_of_50_characters_is_looked_up(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": []})

    result = run(run_module, policy_name="x" * 50, state="absent")

    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False


# QUIRK-008: the server accepts If-Match on a policy DELETE and ignores it, so none is sent.
def test_policy_delete_never_sends_if_match(fake_api, run_module):
    fake = fake_api()
    expect_found(fake, policy(), etag='"p1"')
    fake.expect("DELETE", f"{POLICIES}1", if_match=NO_HEADER)

    result = run(run_module, policy_name="projects-hourly", state="absent")

    assert result["changed"] is True
    assert result["snapshot_policy"] == {}


def test_state_absent_on_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", STATUSES, response={"entries": [policy(name="other")]})

    result = run(run_module, policy_name="projects-hourly", state="absent")

    assert result["changed"] is False
    assert writes(fake) == []
