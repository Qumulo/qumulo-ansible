# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.upgrade_plan: the fleet's plan, its refusals and warnings, and the window form (specs/017 T1720a).

upgrade_fleet.py's fleet_hops and window_form are tested branch by branch beside it, where each refusal's and warning's
wording is pinned; these tests run them through the module, one case for each refusal and warning, by its code, with the
inputs the playbook gives: every inventory host, its images through qumulo.cluster.redact_images.
"""

from __future__ import annotations

import json

import pytest
from ansible.module_utils.testing import patch_module_args
from ansible_collections.qumulo.cluster.plugins.filter.upgrade_image import redact_images
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade_fleet, upgrade_record
from ansible_collections.qumulo.cluster.plugins.modules import upgrade_plan

MIRROR = "https://images.example.com/qumulo"
OLD, NEW = "385145.1.82", "393623.1.1"
NOW = "2026-11-04T17:00:00Z"  # a Wednesday, 09:00 in Los Angeles
LA = "America/Los_Angeles"
EC = "PROTECTION_SYSTEM_TYPE_EC"
OBJECT = "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED"
QUARTERLIES = [f"{MIRROR}/qumulo_core_{r}.qimg" for r in ("7.9.0.5", "7.10.0.3", "7.10.3")]


def onprem(release):
    return f"{MIRROR}/qumulo_core_{release}.qimg"


def cloud(release):
    return f"{MIRROR}/cloud_qumulo_core_{release}.qimg"


def status(state="IDLE", image=None, auto_commit=False):
    settings = {"install_path": image, "auto_commit": auto_commit} if image else None
    return {"state": f"UPGRADE_STATE_{state}", "settings": settings, "error_info": None}


def host(name, version="7.10.3", target="7.11.0", images=None, hub=None, configured=1, **extra):
    """An inventory host as the playbook hands it to the plan: its reads, then its variables."""
    return {
        "name": name,
        "cluster_uuid": f"uuid-{name}",
        "version": version,
        "build": OLD,
        "status": status(),
        "protection": {"type": EC, "configured_node_failures": configured, "remaining_node_failures": configured},
        "license_state": "LICENSE_STATE_ACTIVE",
        "cluster_kind": "edge",
        "target": target,
        "images": [onprem("7.11.0")] if images is None else images,
        "portal_hub": hub,
        **extra,
    }


def replicates(*targets, state="present"):
    return [{"target": t, "state": state, "source_path": "/projects", "target_path": "/dr"} for t in targets]


def hub_and_spokes(**extra):
    """User Story 2's fleet: hub01 with three edges as spokes, and hub01 replicating to a DR cluster, zdr01."""
    return [
        host("hub01", relationships=replicates("zdr01"), **extra),
        *(host(f"edge0{i}", hub="hub01", **extra) for i in (1, 2, 3)),
        host("zdr01", **extra),
    ]


def plan(run_module, *hosts, check_mode=False, **options):
    options.setdefault("now", NOW)
    return run_module(upgrade_plan, {"hosts": list(hosts), **options}, check_mode=check_mode)


def planned(run_module, *hosts, **options):
    result = plan(run_module, *hosts, **options)
    assert "failed" not in result, result.get("msg")
    assert result["changed"] is False
    return result["plan"]


def warned(result, code):
    return [w["message"] for w in result["plan"]["warnings"] if w["code"] == code]


# The plan's shape


def test_one_hop_of_a_hub_its_spokes_and_its_dr_cluster(run_module):
    result = planned(run_module, *hub_and_spokes(), forks=10, timeouts={"wait": 100, "reboot": 1000, "unblock": 10, "peer": 7})

    (hop,) = result["hops"]
    assert (hop["number"], hop["release"], hop["moves"]) == (1, "7.11.0", ["edge01", "edge02", "edge03", "hub01", "zdr01"])
    assert hop["steps"] == [
        {"round": 1, "wave": None, "hosts": ["zdr01"]},
        {"round": 2, "wave": 0, "hosts": ["edge01", "edge02", "edge03"]},
        {"round": 2, "wave": 1, "hosts": ["hub01"]},
    ]
    assert [(u["name"], u["round"], u["follows"]) for u in hop["units"]] == [("zdr01", 1, []), ("hub01", 2, ["zdr01"])]
    places = {h: (e["unit"], e["round"], e["wave"], e["step"], e["reboot"], e["image_index"], e["image_redacted"]) for h, e in hop["hosts"].items()}
    assert places == {
        "edge01": ("hub01", 2, 0, 1, "rolling", 0, onprem("7.11.0")),
        "edge02": ("hub01", 2, 0, 1, "rolling", 0, onprem("7.11.0")),
        "edge03": ("hub01", 2, 0, 1, "rolling", 0, onprem("7.11.0")),
        "hub01": ("hub01", 2, 1, 2, "rolling", 0, onprem("7.11.0")),
        "zdr01": ("zdr01", 1, None, 0, "rolling", 0, onprem("7.11.0")),
    }
    assert hop["hosts"]["edge02"]["holds"] == ["edge01", "edge02", "edge03", "hub01"]
    assert hop["hosts"]["zdr01"]["holds"] == ["edge01", "edge02", "edge03", "hub01", "zdr01"]
    timeouts = {"request": upgrade_fleet.REQUEST_TIMEOUT, "wait": 100, "reboot": 1000, "unblock": 10, "peer": 7}
    assert hop["lease"] == upgrade_fleet.hop_lease(hop, timeouts, forks=10)
    assert result["lock_hosts"] == {"edge01": ["edge01"], "edge02": ["edge02"], "edge03": ["edge03"], "hub01": ["hub01", "zdr01"], "zdr01": ["zdr01"]}
    assert (result["records"], result["skipped"], result["refusals"]) == ({"retire": [], "drop": []}, [], [])


def test_a_hop_larger_than_forks_carries_w6_for_the_role_and_one_within_it_none(run_module):
    small = planned(run_module, *hub_and_spokes(), forks=2)
    throttled = planned(run_module, *hub_and_spokes(), forks=10, throttle=2)
    within = planned(run_module, *hub_and_spokes(), forks=10)

    assert small["hops"][0]["forks_warning"] == (
        "forks is 2, below this hop's 5 clusters or its largest commit step of 3. Prepares and a step's commits run in batches, "
        "which lengthens the hop and its components' mismatch windows. Raise forks to at least 5."
    )
    assert throttled["hops"][0]["forks_warning"].startswith("cluster_upgrade_prepare_throttle is 2, below this hop's 5 clusters")
    assert within["hops"][0]["forks_warning"] is None
    assert [w for w in small["warnings"] if w["code"] == "W6"] == []


def test_a_timeout_left_unset_counts_at_its_default(run_module):
    (hop,) = planned(run_module, host("edge01"), timeouts={"wait": 100})["hops"]

    assert hop["lease"] == upgrade_fleet.hop_lease(hop, {**upgrade_fleet.DEFAULT_TIMEOUTS, "request": upgrade_fleet.REQUEST_TIMEOUT, "wait": 100})


def test_three_hops_a_host_that_joins_later_and_one_given_the_last_hop_for_its_reboots_alone(run_module):
    rebooting = host("c", "7.10.3", "7.10.3", QUARTERLIES, status=status("REBOOTING"))
    result = planned(run_module, host("a", "7.8.4", "7.10.3", QUARTERLIES), host("b", "7.9.0.5", "7.10.3", QUARTERLIES), rebooting)

    assert [(hop["release"], hop["moves"]) for hop in result["hops"]] == [("7.9.0.5", ["a"]), ("7.10.0.3", ["a", "b"]), ("7.10.3", ["a", "b"])]
    assert [hop["hosts"]["a"]["image_index"] for hop in result["hops"]] == [0, 1, 2]
    assert [hop["hosts"]["a"]["image_redacted"] for hop in result["hops"]] == QUARTERLIES
    assert "b" not in result["hops"][0]["hosts"]
    assert "b does not move in the hop to 7.9.0.5; its next hop is 7.10.0.3." in result["hops"][0]["notes"]
    last = result["hops"][2]["hosts"]["c"]
    assert (last["reboots_only"], last["unit"], last["image_index"], last["reboot"]) == (True, None, 2, "rolling")
    assert "c" not in result["hops"][2]["moves"]
    assert all(isinstance(hop["lease"], int) and hop["lease"] > 0 for hop in result["hops"])
    assert result["lock_hosts"] == {"a": ["a"], "b": ["b"], "c": ["c"]}


def record(state="pending", uuid="uuid-edge01", build=OLD):
    return (
        f"format: 1\nstate: {state}\ncluster_uuid: '{uuid}'\nbuild: '{build}'\ntarget_version: '7.11.0'\ndeclared_build: null\ntime: '2026-11-04T05:00:00Z'\n"
    )


def with_record(name, text, path, **extra):
    return host(name, commit_record={"path": str(path), "text": text}, **extra)


@pytest.mark.parametrize("check_mode", [False, True])
def test_the_records_to_retire_and_drop_are_reported_and_none_is_removed(run_module, tmp_path, check_mode):
    """edge01's record is another cluster's, by the cluster_uuid of edge01's node subset; edge02 reads PREPARED on the recorded build."""
    retired, dropped = tmp_path / "edge01" / "commit.yml", tmp_path / "edge02" / "commit.yml"
    for path, text in ((retired, record(uuid="uuid-before-the-rebuild")), (dropped, record(uuid="uuid-edge02"))):
        path.parent.mkdir()
        path.write_text(text, encoding="utf-8")

    result = planned(
        run_module,
        with_record("edge01", retired.read_text(encoding="utf-8"), retired),
        with_record("edge02", dropped.read_text(encoding="utf-8"), dropped, status=status("PREPARED", onprem("7.11.0"))),
        check_mode=check_mode,
    )

    assert result["records"] == {
        "retire": [
            {
                "host": "edge01",
                "path": str(retired),
                "digest": upgrade_record.record_digest(record(uuid="uuid-before-the-rebuild")),
                "line": f"edge01: retired the commit record {retired}: edge01 is now cluster uuid-edge01, not uuid-before-the-rebuild, as after a rebuild",
            }
        ],
        "drop": [
            {
                "host": "edge02",
                "path": str(dropped),
                "digest": upgrade_record.record_digest(record(uuid="uuid-edge02")),
                "line": f"edge02: dropped the commit record {dropped}: edge02 reads PREPARED on build {OLD}, which a commit never leaves",
            }
        ],
    }
    assert retired.is_file() and dropped.is_file()


def test_a_record_of_the_hosts_own_cluster_is_decided_by_its_state(run_module, tmp_path):
    path = tmp_path / "commit.yml"
    result = planned(run_module, with_record("edge01", record(), path, version="7.11.0", build=NEW, target="7.11.0"))

    assert [r["host"] for r in result["records"]["retire"]] == ["edge01"]
    assert (
        result["records"]["retire"][0]["line"] == f"edge01: retired the commit record {path}: edge01 runs 7.11.0 build {NEW}, so its commit to 7.11.0 is done"
    )


# No secret (FR-010a)

PRESIGNED = f"{MIRROR}/cloud_qumulo_core_7.11.0.qimg?X-Amz-Credential=AKIAEXAMPLE&X-Amz-Signature=0f1e2d3c4b5a"
WITH_USER = "https://reader:hunter2@images.example.com/qumulo/qumulo_core_7.11.0.qimg"
SECRETS = ("AKIAEXAMPLE", "0f1e2d3c4b5a", "hunter2", "reader", "X-Amz")


def secret_fleet():
    """Two hosts whose images came through qumulo.cluster.redact_images, as the playbook passes them."""
    return [
        host("cnq01", cluster_kind="cnq", images=redact_images([PRESIGNED])),
        host("edge01", images=redact_images([{"version": "7.11.0", "kind": "on-prem", "image": WITH_USER}]), relationships=replicates("cnq01")),
    ]


def test_no_field_holds_a_query_string_or_user_information(run_module):
    result = plan(run_module, *secret_fleet())

    text = json.dumps(result)
    assert not [secret for secret in SECRETS if secret in text]
    assert "?" not in text and "@" not in text
    hosts = result["plan"]["hops"][0]["hosts"]
    assert (hosts["cnq01"]["image_redacted"], hosts["edge01"]["image_redacted"]) == (cloud("7.11.0"), onprem("7.11.0"))


def test_the_plan_has_no_no_log_option_and_registers_no_secret():
    def no_log_options(spec):
        return [name for name, option in spec.items() if "no_log" in option or no_log_options(option.get("options") or {})]

    assert no_log_options(upgrade_plan.ARGUMENT_SPEC) == []
    with open(upgrade_plan.__file__, encoding="utf-8") as source:
        assert "register_secret" not in source.read()


def run_main(capsys, module_args):
    """main() through AnsibleModule's own exit_json, which below ansible-core 2.22 masks every no_log value it holds."""
    with patch_module_args({**module_args, "_ansible_check_mode": False, "_ansible_diff": False}), pytest.raises(SystemExit):
        upgrade_plan.main()
    return json.loads(capsys.readouterr().out)


def test_through_exit_json_host_names_releases_and_integers_read_unmasked(capsys):
    out = run_main(capsys, {"hosts": secret_fleet(), "now": NOW, "forks": 10})

    text = json.dumps(out)
    assert "failed" not in out
    assert not [secret for secret in SECRETS if secret in text] and "********" not in text
    (hop,) = out["plan"]["hops"]
    assert (hop["release"], hop["moves"], hop["number"]) == ("7.11.0", ["cnq01", "edge01"], 1)
    assert isinstance(hop["lease"], int) and hop["lease"] > 0
    assert (hop["hosts"]["cnq01"]["image_index"], hop["hosts"]["cnq01"]["version"], hop["hosts"]["cnq01"]["kind"]) == (0, "7.11.0", "cloud")
    assert hop["hosts"]["edge01"]["image_redacted"] == onprem("7.11.0")
    assert out["plan"]["lock_hosts"] == {"cnq01": ["cnq01"], "edge01": ["cnq01", "edge01"]}


# The refusals, each stopping the whole plan. Their wording is pinned once, beside upgrade_fleet, upgrade_path and
# upgrade_record; here each runs through the module.

WAVE_HUB = [host("hub01"), host("edge01", hub="hub01")]

REFUSALS = {
    "F1": ([host("edge01", "7.10.3A")], {}),
    "F2": ([host("edge01", images=["ftp://images.example.com/qumulo/qumulo_core_7.11.0.qimg"])], {}),
    "F3": ([host("edge01", "7.11.0", "7.10.3")], {}),
    "F4": ([host("edge01", "7.11.0", "7.11.1")], {}),
    "F6": ([host("edge01", status=status("PREPARING", onprem("7.10.3.1")))], {}),
    "F10": ([host("cnq01", cluster_kind="cnq")], {}),
    "F11": ([host("edge01", reboot="aggressive")], {}),
    "F12": ([host("hub01"), host("edge01", target="7.10.3.1", images=[onprem("7.10.3.1")], hub="hub01")], {}),
    "F13": (WAVE_HUB, {"limit": ["hub01"]}),
    "F14": ([host("edge03", target=None)], {}),  # beside solo01 and solo02
    "F15": ([host("edge01", cluster_kind=None, protection=None, images=[onprem("7.11.0"), cloud("7.11.0")])], {}),
    "F16": ([host("edge01", "7.10.3A", allow_limited_release=True)], {}),
    "F18": ([host("edge01", after=["nowhere01"])], {}),
    "F19": ([with_record("edge01", record(), "/inv/artifacts/edge01/upgrade/commit.yml")], {}),
    "F20": ([host("edge01", reboot_set_size=3)], {}),
    "F30": ([with_record("edge01", record(), "/inv/artifacts/edge01/upgrade/commit.yml", status=status("COMMITTING", onprem("7.11.0")))], {}),
    "F31": ([with_record("edge01", "format: 2\n", "/inv/artifacts/edge01/upgrade/commit.yml")], {}),
    "F34": ([host("edge01", maintenance_windows=[{"start_hour": 22, "start_minute": 0, "end_hour": 4, "end_minute": 0, "on_days": ["SAT"]}])], {}),
}


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_each_refusal_stops_the_whole_plan_with_its_message(run_module, code):
    hosts, options = REFUSALS[code]
    others = [host("solo01"), host("solo02")]  # nothing stops them, and the plan still moves neither

    result = plan(run_module, *hosts, *others, **({**options, "limit": [*options["limit"], "solo01", "solo02"]} if "limit" in options else {}))

    assert result["failed"] is True
    assert [r["code"] for r in result["plan"]["refusals"]] == [code]
    assert result["msg"] == result["plan"]["refusals"][0]["message"]
    assert (result["plan"]["hops"], result["plan"]["lock_hosts"]) == ([], {})


def test_every_refusals_message_is_in_msg_one_to_a_line(run_module):
    result = plan(run_module, host("edge01", "7.10.3A"), host("edge02", reboot="aggressive"))

    assert result["failed"] is True
    assert result["msg"].split("\n") == [r["message"] for r in result["plan"]["refusals"]]
    assert sorted(r["code"] for r in result["plan"]["refusals"]) == ["F1", "F11"]


def test_f11_the_extra_variable_while_aggressive_reboots_are_held(run_module):
    result = plan(run_module, host("edge01", configured=2), aggressive=True)

    assert result["failed"] is True
    assert result["plan"]["refusals"] == [{"host": None, "code": "F11", "message": upgrade_fleet.F11_HELD}]
    assert result["msg"] == upgrade_fleet.F11_HELD


# The warnings, each also printed. As for the refusals, their wording is pinned beside upgrade_fleet.


def printed(result, code):
    """The hosts the plan's warnings of one code name, once each warning is shown printed by module.warn."""
    found = [w for w in result["plan"]["warnings"] if w["code"] == code]
    assert found and all(w["message"] in result["warnings"] for w in found), code
    return [w["host"] for w in found]


def test_w1_for_each_restore_file_the_scan_found_except_a_lock_on_a_host_the_run_locks(run_module):
    key = {"source_path": "/projects", "target_host": "edge09", "target_cluster_uuid": "uuid-edge09", "target_path": "/dr"}
    record = {"host": "edge01", "path": "/inv/artifacts/edge01/restore/r.yml", "key": key, "hosts": ["edge01", "edge09"]}
    lock = {
        "host": "edge01",
        "path": "/inv/artifacts/edge01/restore/lock",
        "holder": "restore",
        "controller": "ctl01",
        "pid": 7,
        "start_time": "2026-11-04T09:00:00.000Z",
        "lease_expiry": "2026-11-05T09:00:00.000Z",
        "liveness": "live",
    }

    result = plan(run_module, host("edge01"), host("edge09", "7.11.0"), restore_files={"records": [record], "locks": [lock]})

    assert [message.split(",")[0] for message in warned(result, "W1")] == ["edge01: an open restore record"]
    assert result["warnings"] == warned(result, "W1")


def test_the_plan_takes_no_restore_files_by_default(run_module):
    assert warned(plan(run_module, host("edge01")), "W1") == []


def test_restore_files_may_leave_out_either_list(run_module):
    assert warned(plan(run_module, host("edge01"), restore_files={"locks": []}), "W1") == []


def test_restore_files_refuses_a_key_it_does_not_know(run_module):
    result = plan(run_module, host("edge01"), restore_files={"records": [], "findings": []})

    assert result["failed"] is True
    assert "findings" in result["msg"]


@pytest.mark.parametrize("product", [{"cluster_kind": "edge"}, {"cluster_kind": None, "unprotected_edge": True}], ids=["kind", "product"])
def test_w2_an_unlicensed_edge(run_module, product):
    result = plan(run_module, host("edge01", license_state="LICENSE_STATE_EXPIRED", **product))

    assert printed(result, "W2") == ["edge01"]


def test_w3_relationship_ends_more_than_eight_quarters_apart_before_the_run(run_module):
    """The one shape of W3 that upgrade_fleet's tests do not give in full: the ends apart before the run, not after a hop."""
    result = plan(run_module, host("src01", "7.11.0", relationships=replicates("dst01")), host("dst01", "7.2.0.1", "7.2.0.1"))

    assert warned(result, "W3") == [
        "src01 replicates to dst01, which would be 9 quarterly releases apart before the run. Replication refuses clusters about eight quarters apart."
    ]
    assert printed(result, "W3") == ["src01"]


def test_w4_and_w7_live_portals_the_inventory_does_not_declare(run_module):
    result = plan(run_module, host("hub01", live_hub_portals=2), host("edge01", live_spoke_portals=1))

    assert (printed(result, "W4"), printed(result, "W7")) == (["hub01"], ["edge01"])


def test_w5_a_source_older_than_the_collections_tests_have_upgraded_from(run_module, monkeypatch):
    monkeypatch.setattr(upgrade_fleet, "OLDEST_TESTED_SOURCE", ("7.10.3", "2026-10-31"))

    assert printed(plan(run_module, host("edge01", "7.10.0.3")), "W5") == ["edge01"]


def test_w8_for_each_reason_the_plan_can_know_once_the_hold_is_lifted(run_module, monkeypatch):
    monkeypatch.setattr(upgrade_fleet, "AGGRESSIVE_HELD", False)
    cnq = host("cnq01", cluster_kind="cnq", images=[cloud("7.11.0")], configured=2, protection={"type": OBJECT, "configured_node_failures": 2})

    result = plan(run_module, cnq, host("edge01", configured=1), host("edge02", configured=2), aggressive=True)

    assert printed(result, "W8") == ["cnq01", "edge01"]
    hosts = result["plan"]["hops"][0]["hosts"]
    assert (hosts["cnq01"]["reboot"], hosts["edge01"]["reboot"], hosts["edge02"]["reboot"]) == ("rolling", "rolling", "aggressive")


def test_w9_a_source_outside_the_run_and_w12_a_target_the_run_leaves_out(run_module):
    result = plan(
        run_module,
        host("src01", relationships=replicates("edge01")),
        host("edge01", relationships=replicates("dr01")),
        host("dr01"),
        limit=["edge01"],
    )

    assert (printed(result, "W9"), printed(result, "W12")) == (["edge01"], ["edge01"])


def test_w10_a_set_size_the_reboot_ignores(run_module):
    result = plan(run_module, host("edge01", configured=0, reboot_set_size=2), host("edge02", reboot="all", reboot_set_size=1))

    assert printed(result, "W10") == ["edge01", "edge02"]
    hosts = result["plan"]["hops"][0]["hosts"]
    assert (hosts["edge01"]["reboot"], hosts["edge02"]["reboot"]) == ("all", "all")


def window(days, start, end):
    return {"start_hour": start, "start_minute": 0, "end_hour": end, "end_minute": 0, "on_days": [days]}


SATURDAY_NIGHT = [window("SAT", 22, 4)]


def windowed(name, ranges=None, **extra):
    return host(name, maintenance_windows=ranges or SATURDAY_NIGHT, window_timezone=LA, **extra)


def test_w11_ranges_with_no_time_in_common(run_module):
    result = plan(run_module, windowed("edge01"), windowed("edge02", [window("SUN", 8, 12)]), host("edge03"))

    assert printed(result, "W11") == [None]


def test_w11_is_not_given_for_ranges_in_common(run_module):
    assert warned(plan(run_module, windowed("edge01"), windowed("edge02")), "W11") == []


# The window form


def split_component():
    """hub01's component started split: edge01 is already on 7.11.0, and the hop closes the split; zdr01 stands alone."""
    return [windowed("hub01"), windowed("edge01", version="7.11.0", hub="hub01"), windowed("edge02", hub="hub01"), windowed("zdr01")]


def window_form(run_module, hop, held=(), now=NOW):
    result = run_module(upgrade_plan, {"window": {"hop": hop, "held": list(held)}, "now": now})
    assert set(result) == {"changed", "window", "warnings"}
    assert result["changed"] is False
    return result["window"]


def test_the_window_form_inside_holds_nothing(run_module):
    (hop,) = planned(run_module, *split_component())["hops"]

    answer = window_form(run_module, hop, now="2026-11-08T07:00:00Z")  # Saturday, 23:00 in Los Angeles

    assert answer == {"declared": True, "inside": True, "next_open": None, "outside_hosts": [], "held": [], "lines": [], "message": None}


def test_the_window_form_outside_holds_all_but_the_moving_members_of_a_split_it_closes(run_module):
    (hop,) = planned(run_module, *split_component())["hops"]

    answer = window_form(run_module, hop)

    assert answer["inside"] is False
    assert answer["next_open"] == "2026-11-08T06:00:00Z"  # Saturday 22:00 in Los Angeles, on standard time
    assert answer["outside_hosts"] == ["edge02", "hub01", "zdr01"]
    assert answer["held"] == ["zdr01"]
    assert answer["lines"] == [
        "edge02 commits outside the maintenance window, to close the split of hub01's portal component.",
        "hub01 commits outside the maintenance window, to close the split of hub01's portal component.",
    ]
    assert answer["message"] == (
        "zdr01 were not committed: the hop to 7.11.0 is outside their maintenance window, which next opens 2026-11-07 22:00 "
        "America/Los_Angeles. They stay prepared. Run the upgrade again inside the window."
    )


def test_the_window_form_leaves_the_clock_to_hosts_the_run_has_not_held(run_module):
    (hop,) = planned(run_module, windowed("edge01"), host("edge02"))["hops"]

    answer = window_form(run_module, hop, held=["edge01"])

    assert (answer["declared"], answer["inside"], answer["held"]) == (False, True, [])


def test_the_window_form_defaults_now_to_the_controllers_clock(run_module, monkeypatch):
    (hop,) = planned(run_module, windowed("edge01"))["hops"]
    monkeypatch.setattr(upgrade_plan, "utc_now", lambda: upgrade_fleet._instant("2026-11-08T07:00:00Z"))

    result = run_module(upgrade_plan, {"window": {"hop": hop}})

    assert result["window"]["inside"] is True


def test_a_now_that_is_no_iso_time_fails_naming_the_form(run_module):
    result = run_module(upgrade_plan, {"hosts": [host("edge01")], "now": "next tuesday"})

    assert result["failed"] is True
    assert result["msg"] == "now must be an ISO 8601 time, such as 2026-11-04T17:00:00Z, not next tuesday"


# Every inventory host, the limit and the hosts that left the play


def test_every_inventory_host_is_read_and_only_the_play_is_planned(run_module):
    """solo02 is left out by the limit and solo03's read failed; neither moves, and neither stops the plan."""
    result = plan(
        run_module,
        host("solo01", relationships=replicates("solo03")),
        host("solo02", relationships=replicates("solo01")),
        host("solo03", in_play=False),
        limit=["solo01", "solo03"],
    )

    assert "failed" not in result
    assert [hop["moves"] for hop in result["plan"]["hops"]] == [["solo01"]]
    assert printed(result, "W9") == ["solo01"]
    assert result["plan"]["lock_hosts"] == {"solo01": ["solo01", "solo03"]}


def test_a_member_whose_read_failed_stops_its_component(run_module):
    result = plan(run_module, host("hub01"), host("edge01", hub="hub01", in_play=False), host("solo01"))

    assert result["failed"] is True
    assert [(r["host"], r["code"]) for r in result["plan"]["refusals"]] == [("hub01", "F13")]
    assert "edge01 (its read failed)" in result["msg"]


def test_a_limit_that_leaves_a_whole_component_out_is_planned(run_module):
    result = planned(run_module, *WAVE_HUB, host("solo01"), limit=["solo01"])

    assert [hop["moves"] for hop in result["hops"]] == [["solo01"]]


def test_neither_hosts_nor_window_fails(run_module):
    result = run_module(upgrade_plan, {})

    assert result["failed"] is True
    assert result["msg"] == "one of the following is required: hosts, window"
