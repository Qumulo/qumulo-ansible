# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The Resource, Settings and Action templates, driven through toy modules defined here."""

from __future__ import annotations

from typing import ClassVar
from urllib.error import URLError

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, Route
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable, qumulo_module, run, run_info, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Action, Current, Field, Resource, Settings, digest
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID, NO_HEADER

LOST = URLError(ConnectionResetError(104, "Connection reset by peer"))
CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}

WIDGETS = Route("/v1/widgets/", ("GET", "POST"))
WIDGET = Route("/v1/widgets/{id}", ("GET", "PATCH", "DELETE"))
WIDGET_FIELDS = ("name", "kind", "size", "tags", "password", "api_key")


class Widget(Resource):
    RETURN_KEY = "widget"
    CREATE_ONLY = frozenset({"kind"})
    WRITE_ONLY = frozenset({"password"})
    SECRET_READBACK = frozenset({"api_key"})
    UNORDERED = frozenset({"tags"})

    def fetch(self):
        for item in self.client.get(WIDGETS).data["widgets"]:
            if item["name"] == self.params["name"]:
                return Current(item["id"], item, etag=f'"{item["id"]}"')
        return None

    def normalize(self, current):
        return {**{k: current.data[k] for k in ("id", "name", "kind", "size", "tags")}, "api_key": digest(current.data["api_key"])}

    def desired(self, before):
        return {**before, **{k: self.params[k] for k in WIDGET_FIELDS if self.params[k] is not None}}

    def create(self, after):
        self.client.post(WIDGETS, body=after)

    def update(self, current, after, keys):
        self.client.patch(WIDGET, params={"id": current.id}, body={k: after[k] for k in keys}, if_match=current.etag)

    def delete(self, current):
        self.client.delete(WIDGET, params={"id": current.id})


WIDGET_SPEC = {
    "name": {"type": "str", "required": True},
    "kind": {"type": "str"},
    "size": {"type": "int"},
    "tags": {"type": "list", "elements": "str"},
    "password": {"type": "str", "no_log": True},
    "api_key": {"type": "str", "no_log": True},
    "update_password": {"type": "str", "choices": ["on_create", "always"], "default": "on_create", "no_log": False},
    "recreate": {"type": "bool", "default": False},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
}


def widget_main():
    run_resource(qumulo_module(WIDGET_SPEC), Widget)


def stored(**overrides):
    return {"id": "7", "name": "w", "kind": "a", "size": 1, "tags": ["x", "y"], "api_key": "k1", **overrides}


def listing(*widgets):
    return {"widgets": list(widgets)}


def test_check_mode_sends_no_write(fake_api, run_module):
    fake = fake_api()  # the script holds no write, so any POST, PATCH or DELETE fails the test
    fake.expect("GET", "/v1/widgets/", response=listing())
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))

    created = run_module(widget_main, {**CONNECTION, "name": "w", "kind": "a", "size": 1}, check_mode=True)
    modified = run_module(widget_main, {**CONNECTION, "name": "w", "size": 2}, check_mode=True)
    deleted = run_module(widget_main, {**CONNECTION, "name": "w", "state": "absent"}, check_mode=True)

    assert (created["changed"], created["widget"]) == (True, {"name": "w", "kind": "a", "size": 1})
    assert (modified["changed"], modified["widget"]["size"]) == (True, 2)
    assert (deleted["changed"], deleted["widget"]) == (True, {})


def test_create_then_rerun_is_unchanged(fake_api, run_module):
    args = {**CONNECTION, "name": "w", "kind": "a", "size": 1, "tags": ["y", "x"], "api_key": "k1"}
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing())
    fake.expect("POST", "/v1/widgets/", body={"name": "w", "kind": "a", "size": 1, "tags": ["y", "x"], "api_key": "k1"})
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))

    created = run_module(widget_main, args)
    rerun = run_module(widget_main, args)

    assert (created["changed"], created["widget"]) == (True, {"id": "7", "name": "w", "kind": "a", "size": 1, "tags": ["x", "y"]})
    assert (rerun["changed"], rerun["warnings"]) == (False, [])


def test_diff_hides_write_only_and_secret_readback(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"api_key": "k2", "password": "pw", "size": 2}, if_match='"7"')
    fake.expect("GET", "/v1/widgets/", response=listing(stored(size=2, api_key="k2")))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "size": 2, "password": "pw", "api_key": "k2", "update_password": "always"}, diff=True)

    public = {"id": "7", "name": "w", "kind": "a", "tags": ["x", "y"]}
    assert result["changed"] is True
    assert result["widget"] == {**public, "size": 2}
    assert result["diff"] == {"before": {**public, "size": 1}, "after": {**public, "size": 2}, "after_header": "changed, not shown: api_key, password"}


def test_update_password_on_create_is_unchanged_on_rerun(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing())
    fake.expect("POST", "/v1/widgets/", body={"name": "w", "kind": "a", "password": "pw"})
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"password": "pw"})
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    args = {**CONNECTION, "name": "w", "kind": "a", "password": "pw"}

    created = run_module(widget_main, args, diff=True)
    assert (created["changed"], created["diff"]["after_header"]) == (True, "changed, not shown: password")
    assert run_module(widget_main, args)["changed"] is False
    assert run_module(widget_main, {**args, "update_password": "always"})["changed"] is True


def test_create_only_change_needs_recreate(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7")
    fake.expect("POST", "/v1/widgets/", body={"name": "w", "kind": "b", "size": 1, "tags": ["x", "y"], "api_key": "k1", "password": "pw"})
    fake.expect("GET", "/v1/widgets/", response=listing(stored(id="8", kind="b")))

    refused = run_module(widget_main, {**CONNECTION, "name": "w", "kind": "b"})
    recreated = run_module(widget_main, {**CONNECTION, "name": "w", "kind": "b", "password": "pw", "api_key": "k1", "recreate": True})

    assert refused["failed"] is True
    assert refused["msg"] == "widget: kind cannot change in place; set recreate=true to delete and recreate it"
    assert (recreated["changed"], recreated["widget"]["id"], recreated["widget"]["kind"]) == (True, "8", "b")


def test_recreate_refuses_without_the_write_only_values(fake_api, run_module):
    fake_api().expect("GET", "/v1/widgets/", response=listing(stored()))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "kind": "b", "recreate": True})

    assert result["msg"] == "widget: recreate=true needs password, which cannot be read back from the existing object"


def test_secret_readback_is_compared_by_digest(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"api_key": "k2"})
    fake.expect("GET", "/v1/widgets/", response=listing(stored(api_key="k2")))

    assert run_module(widget_main, {**CONNECTION, "name": "w", "api_key": "k1"})["changed"] is False
    assert run_module(widget_main, {**CONNECTION, "name": "w", "api_key": "k2"})["changed"] is True


def test_digest_hashes_plaintext_even_when_it_looks_like_a_digest():
    looks_like_one = "sha256:" + "0" * 64

    assert digest(looks_like_one) != looks_like_one
    assert digest(digest("k1")) == digest("k1")


def test_missing_after_the_write_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing())
    fake.expect("POST", "/v1/widgets/")
    fake.expect("GET", "/v1/widgets/", response=listing())

    assert run_module(widget_main, {**CONNECTION, "name": "w"})["msg"] == "widget is missing after the write"


FLAG = Route("/v1/flag", ("GET", "POST"))


class Flag(Resource):
    """A resource with nothing to configure: present or absent."""

    RETURN_KEY = "flag"

    def fetch(self):
        response = self.client.get_or_none(FLAG)
        return response and Current(None, response.data)

    def normalize(self, current):
        return {}

    def desired(self, before):
        return {}

    def create(self, after):
        self.client.post(FLAG, body=after)


def test_create_with_nothing_to_configure_still_creates(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/flag", status=404, response={"error_class": "flag_not_set_error", "description": "unset"})
    fake.expect("POST", "/v1/flag", body={})
    fake.expect("GET", "/v1/flag", response={})

    result = run_module(lambda: run_resource(qumulo_module({}), Flag), CONNECTION)

    assert result["changed"] is True


def test_settle_reports_non_convergence(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"api_key": "k2", "size": 2})
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "size": 2, "api_key": "k2"})

    assert result["failed"] is True
    assert result["msg"] == "widget did not converge on api_key, size"
    assert (result["expected"], result["observed"]) == ({"size": 2}, {"size": 1})


def test_state_absent_deletes_and_absent_missing_is_unchanged(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7")
    fake.expect("GET", "/v1/widgets/", response=listing())

    assert run_module(widget_main, {**CONNECTION, "name": "w", "state": "absent"})["changed"] is True
    assert run_module(widget_main, {**CONNECTION, "name": "w", "state": "absent"})["changed"] is False


KNOBS = Route("/v1/knobs", ("GET", "PATCH", "PUT"))


class Knobs(Settings):
    RETURN_KEY = "knobs"
    ROUTE = KNOBS
    FIELDS: ClassVar[dict[str, Field]] = {
        "threshold": Field("free_threshold", compare=lambda a, b: abs(a - b) < 1e-6),
        "mode": Field("mode_wire", read=str.lower, write=str.upper),
        "hosts": Field("hosts", unordered=True),
    }


class PutKnobs(Knobs):
    WRITE = "PUT"


KNOBS_SPEC = {"threshold": {"type": "float"}, "mode": {"type": "str"}, "hosts": {"type": "list", "elements": "str"}}
KNOBS_DATA = {"free_threshold": 0.05, "mode_wire": "SLOW", "hosts": ["a", "b"], "other": 1}


def knobs_main():
    run_resource(qumulo_module(KNOBS_SPEC), Knobs)


def put_knobs_main():
    run_resource(qumulo_module(KNOBS_SPEC), PutKnobs)


def test_settings_patch_sends_only_changed_wire_keys_with_if_match(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/knobs", response=KNOBS_DATA, etag='"1"')
    fake.expect("PATCH", "/v1/knobs", body={"mode_wire": "FAST"}, if_match='"1"')
    fake.expect("GET", "/v1/knobs", response={**KNOBS_DATA, "mode_wire": "FAST"}, etag='"2"')

    result = run_module(knobs_main, {**CONNECTION, "threshold": 0.0500000001, "mode": "fast", "hosts": ["b", "a"]}, diff=True)

    assert result["changed"] is True
    assert result["knobs"] == {"threshold": 0.05, "mode": "fast", "hosts": ["a", "b"]}
    assert result["diff"]["before"]["mode"] == "slow"


def test_settings_reread_after_412_that_finds_it_converged_writes_nothing_and_reports_no_change(fake_api, run_module):
    """A 412 applied nothing, and the re-read shows another writer already set the value, so this task changed nothing."""
    fake = fake_api()
    fake.expect("GET", "/v1/knobs", response=KNOBS_DATA, etag='"1"')
    fake.expect("PATCH", "/v1/knobs", body={"mode_wire": "FAST"}, if_match='"1"', status=412, response={"error_class": "http_precondition_failed_error"})
    fake.expect("GET", "/v1/knobs", response={**KNOBS_DATA, "mode_wire": "FAST"}, etag='"2"')
    fake.expect("GET", "/v1/knobs", response={**KNOBS_DATA, "mode_wire": "FAST"}, etag='"2"')

    result = run_module(knobs_main, {**CONNECTION, "mode": "fast"}, diff=True)

    assert result["changed"] is False
    assert result["diff"]["before"] == result["diff"]["after"]
    assert [e.method for e in fake.log if not e.background] == ["GET", "PATCH", "GET", "GET"]


def test_settings_unordered_unions_explicit_names_with_unordered_fields():
    class Mixed(Settings):
        ROUTE = KNOBS
        UNORDERED = frozenset({"tags"})
        FIELDS: ClassVar[dict[str, Field]] = {"hosts": Field("hosts", unordered=True), "tags": Field("tags")}

    assert Mixed.UNORDERED == frozenset({"hosts", "tags"})


def test_settings_put_sends_the_whole_document(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/knobs", response=KNOBS_DATA, etag='"1"')
    fake.expect("PUT", "/v1/knobs", body={**KNOBS_DATA, "free_threshold": 0.15}, if_match='"1"')
    fake.expect("GET", "/v1/knobs", response={**KNOBS_DATA, "free_threshold": 0.15})
    fake.expect("GET", "/v1/knobs", response={**KNOBS_DATA, "free_threshold": 0.15})

    assert run_module(put_knobs_main, {**CONNECTION, "threshold": 0.15})["changed"] is True
    assert run_module(put_knobs_main, {**CONNECTION, "threshold": 0.15})["changed"] is False


RESTART = Route("/v1/restart", ("POST",))


class Restart(Action):
    def plan(self):
        return {"restart": self.params["service"]} if self.params["service"] else None

    def perform(self, plan):
        return {"job": self.client.post(RESTART, body=plan).data["job"]}


def restart_main():
    run_resource(qumulo_module({"service": {"type": "str"}}), Restart)


def test_action_plans_in_check_mode_and_performs_otherwise(fake_api, run_module):
    fake = fake_api()
    fake.expect("POST", "/v1/restart", body={"restart": "smb"}, if_match=NO_HEADER, response={"job": "9"})

    assert run_module(restart_main, {**CONNECTION, "service": None}) == {"changed": False, "warnings": []}
    assert run_module(restart_main, {**CONNECTION, "service": "smb"}, check_mode=True, diff=True) == {
        "changed": True,
        "plan": {"restart": "smb"},
        "diff": {"before": {}, "after": {"restart": "smb"}},
        "warnings": [],
    }
    assert run_module(restart_main, {**CONNECTION, "service": "smb"}) == {"changed": True, "plan": {"restart": "smb"}, "job": "9", "warnings": []}


def test_action_that_loses_its_connection_reports_it_may_have_changed(fake_api, run_module):
    fake_api().expect("POST", "/v1/restart", body={"restart": "smb"}, raises=LOST)

    result = run_module(restart_main, {**CONNECTION, "service": "smb"})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)


def guarded_main():
    run(qumulo_module({}), lambda client: client.get(WIDGETS).data)


def unguarded_main():
    run(qumulo_module({}), lambda client: {"changed": False, "count": len(client.get(WIDGETS).data["widgets"])})


def test_run_reports_a_qumulo_error_with_its_details(fake_api, run_module):
    fake_api(cluster_id="")

    failed = run_module(guarded_main, CONNECTION)

    assert failed["msg"].startswith("identity guard: https://qumulo.example.com:8000 is cluster 'UNCONFIGURED'")
    assert (failed["failed"], failed["expected_cluster_uuid"]) == (True, CLUSTER_ID)


def test_run_forwards_client_warnings_on_success(fake_api, run_module):
    fake_api().expect("GET", "/v1/widgets/", response=listing())

    unguarded = run_module(unguarded_main, {**CONNECTION, "expected_cluster_uuid": ""})

    assert unguarded == {"changed": False, "count": 0, "warnings": ["identity guard disabled: expected_cluster_uuid is empty"]}


def test_run_forwards_client_warnings_on_failure(fake_api, run_module):
    fake_api().expect("GET", "/v1/widgets/", status=403, response={"error_class": "api_access_denied_error", "description": "denied"})

    failed = run_module(guarded_main, {**CONNECTION, "expected_cluster_uuid": ""})

    assert failed["failed"] is True
    assert failed["warnings"] == ["identity guard disabled: expected_cluster_uuid is empty"]


def test_run_lets_other_exceptions_propagate(fake_api, run_module):
    fake_api()

    with pytest.raises(ZeroDivisionError):
        run_module(lambda: run(qumulo_module({}), lambda client: 1 / 0), CONNECTION)


def test_run_reports_a_credential_error(fake_api, run_module):
    fake_api()

    failed = run_module(unguarded_main, {**CONNECTION, "api_token": "t"})

    assert failed["msg"].startswith("set exactly one of api_password or api_token")


def info_main(subsets):
    def main():
        spec = {"gather_subset": {"type": "list", "elements": "str", "choices": ["all", *subsets], "default": ["all"]}}
        run_info(qumulo_module(spec), "cluster", subsets)

    return main


def test_run_info_merges_the_facts_of_selected_subsets(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("GET", "/v1/widgets/", response=listing())
    main = info_main(
        {
            "widgets": lambda client: {"widgets": client.get(WIDGETS).data["widgets"]},
            "version": lambda client: {"version": "7.10.3", "build_id": "b1"},
        }
    )

    assert run_module(main, {**CONNECTION, "gather_subset": ["version"]}) == {
        "changed": False,
        "cluster": {"version": "7.10.3", "build_id": "b1"},
        "warnings": [],
    }
    assert run_module(main, CONNECTION)["cluster"] == {"widgets": [stored()], "version": "7.10.3", "build_id": "b1"}
    assert run_module(main, {**CONNECTION, "gather_subset": ["widgets", "all"]})["cluster"]["widgets"] == []


def test_run_info_records_an_unavailable_subset_and_gathers_the_rest(fake_api, run_module):
    fake_api()

    def unavailable(client):
        raise SubsetUnavailable("this cluster has no widgets")

    main = info_main({"widgets": unavailable, "version": lambda client: {"version": "7.10.3"}})
    result = run_module(main, CONNECTION)

    assert "failed" not in result
    assert result["cluster"] == {"version": "7.10.3"}
    assert result["unavailable_subsets"] == {"widgets": "this cluster has no widgets"}


def test_run_info_refuses_two_subsets_that_produce_the_same_fact(fake_api, run_module):
    fake_api()
    main = info_main({"first": lambda client: {"version": "7.10.3"}, "second": lambda client: {"version": "7.11.0"}})

    with pytest.raises(ValueError, match=r"subsets first and second both produce version"):
        run_module(main, CONNECTION)


def test_module_option_shadowing_a_connection_option_is_refused(run_module):
    def shadowing_main():
        qumulo_module({"name": {"type": "str"}, "api_password": {"type": "str", "no_log": True}})

    with pytest.raises(ValueError, match=r"module options api_password shadow qumulo\.cluster connection options"):
        run_module(shadowing_main, CONNECTION)


def test_lost_connection_on_update_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"size": 2}, raises=LOST)
    fake.expect("GET", "/v1/widgets/", response=listing(stored(size=2)))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "size": 2})

    assert (result["changed"], result["notes"], result["widget"]["size"]) == (True, ["write reconciled after a lost connection"], 2)


def test_lost_update_of_a_write_only_field_is_not_reported_as_reconciled(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"password": "pw"}, raises=LOST)

    result = run_module(widget_main, {**CONNECTION, "name": "w", "password": "pw", "update_password": "always"})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)
    assert result["msg"].endswith("; password cannot be read back, so whether the change took effect is unknown")


def test_lost_connection_without_convergence_fails_without_writing_again(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"size": 2}, raises=LOST)
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "size": 2})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)
    assert result["msg"].startswith("PATCH /v1/widgets/7: the connection was lost after the request was sent")
    assert result["msg"].endswith("reading back did not confirm it: widget did not converge on size")
    assert [e.method for e in fake.log if not e.background] == ["GET", "PATCH", "GET"]


def test_lost_connection_on_delete_is_reconciled_by_reading_back(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7", raises=LOST)
    fake.expect("GET", "/v1/widgets/", response=listing())

    result = run_module(widget_main, {**CONNECTION, "name": "w", "state": "absent"})

    assert (result["changed"], result["notes"]) == (True, ["write reconciled after a lost connection"])


def test_lost_connection_on_delete_of_a_survivor_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7", raises=LOST)
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "state": "absent"})

    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)
    assert result["msg"].endswith("reading back shows widget still exists")


def test_failure_after_a_write_reports_changed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("PATCH", "/v1/widgets/7", body={"size": 2})
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))

    result = run_module(widget_main, {**CONNECTION, "name": "w", "size": 2})

    assert (result["failed"], result["changed"], result["msg"]) == (True, True, "widget did not converge on size")


def test_recreate_that_fails_after_the_delete_says_so(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7")
    fake.expect("POST", "/v1/widgets/", status=400, response={"error_class": "bad_widget", "description": "no"})

    result = run_module(widget_main, {**CONNECTION, "name": "w", "kind": "b", "password": "pw", "recreate": True})

    assert result["msg"] == "deleted widget for recreate but did not recreate it: POST /v1/widgets/ failed: HTTP 400 bad_widget: no"
    assert (result["failed"], result["changed"]) == (True, True)


def test_recreate_whose_delete_is_lost_does_not_attempt_the_create(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7", raises=LOST)

    result = run_module(widget_main, {**CONNECTION, "name": "w", "kind": "b", "password": "pw", "recreate": True})

    assert result["msg"].endswith("; this delete was for recreate, so the create was not attempted")
    assert (result["failed"], result["changed"], result["maybe_applied"]) == (True, True, True)


class SlowWidget(Widget):
    def settle_absent(self):
        raise QumuloError("widget is still being deleted")


def test_failure_after_a_delete_reports_changed(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/widgets/", response=listing(stored()))
    fake.expect("DELETE", "/v1/widgets/7")

    result = run_module(lambda: run_resource(qumulo_module(WIDGET_SPEC), SlowWidget), {**CONNECTION, "name": "w", "state": "absent"})

    assert (result["failed"], result["changed"], result["msg"]) == (True, True, "widget is still being deleted")
