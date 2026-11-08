# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.upgrade_report: one hop's report from the upgrade module's recorded results (specs/017 T1725a).

The hop is fleet_hops' for User Story 2's fleet, and each result holds only the upgrade module's return fields, with an
invocation beside them that the report must never read.
"""

from __future__ import annotations

import json

import pytest
from ansible.module_utils.testing import patch_module_args
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade_fleet
from ansible_collections.qumulo.cluster.plugins.modules import upgrade_report

IMAGE = "https://images.example.com/qumulo/qumulo_core_7.11.0.qimg"
SIGNED = IMAGE + "?X-Amz-Credential=AKIAEXAMPLE&X-Amz-Signature=0f1e2d3c4b5a"
OLD, NEW = "385145.1.82", "393623.1.1"
INVOCATION = {"module_args": {"image": IMAGE, "image_secret": {"userinfo": "reader:hunter2", "query": "X-Amz-Signature=0f1e2d3c4b5a"}}}


def fleet_host(name, hub=None, **extra):
    return {
        "name": name,
        "cluster_uuid": f"uuid-{name}",
        "version": "7.10.3",
        "build": OLD,
        "status": {"state": "UPGRADE_STATE_IDLE", "settings": None, "error_info": None},
        "protection": {"type": "PROTECTION_SYSTEM_TYPE_EC", "configured_node_failures": 1, "remaining_node_failures": 1},
        "license_state": "LICENSE_STATE_ACTIVE",
        "cluster_kind": "edge",
        "target": "7.11.0",
        "images": [IMAGE],
        "portal_hub": hub,
        **extra,
    }


def the_hop(*hosts):
    plan = upgrade_fleet.fleet_hops(list(hosts), now="2026-11-04T17:00:00Z")
    assert plan["refusals"] == []
    return plan["hops"][0]


HOP = the_hop(
    fleet_host("hub01", relationships=[{"target": "zdr01", "state": "present"}]),
    *(fleet_host(f"edge0{i}", hub="hub01") for i in (1, 2, 3)),
    fleet_host("zdr01"),
)


def span(started, ended):
    return {"started": f"2026-11-04T{started}+00:00", "ended": None if ended is None else f"2026-11-04T{ended}+00:00"}


def result(phases=None, *, version="7.10.3", build=OLD, to=("7.11.0", None), **fields):
    """A registered upgrade result: its return fields, and an invocation holding the image's secret parts."""
    return {
        "changed": bool(phases),
        "cluster_uuid": "uuid",
        "image_redacted": IMAGE,
        "from": {"version": version, "build": build},
        "to": {"version": to[0], "build": to[1]},
        "upgrade_type": "SOFTWARE_AND_PLATFORM",
        "phases": phases or {},
        "downtime_seconds": None,
        "ready": None,
        "prepared": False,
        "commit_sent": False,
        "reboot": {"mode": "rolling", "set_size": None, "seconds": None},
        "waiver": None,
        "status": {"state": "UPGRADE_STATE_PREPARED", "settings": {"install_path": IMAGE}},
        "invocation": INVOCATION,
        **fields,
    }


def prepared(started="16:00:00", ended="16:20:00"):
    return result({"prepare": span(started, ended)}, prepared=True)


def ready(**fields):
    return result({"ready": span("16:30:00", "16:30:05")}, ready=True, **fields)


def committed(started, ended, downtime=40.5):
    return result({"commit": span(started, ended)}, to=("7.11.0", NEW), commit_sent=True, downtime_seconds=downtime)


def rebooted(seconds=2400):
    return result(
        {"reboots": span("18:00:00", "18:40:00")},
        version="7.11.0",
        build=NEW,
        to=("7.11.0", NEW),
        reboot={"mode": "rolling", "set_size": None, "seconds": seconds},
    )


def failed(msg, phases=None, **fields):
    return {**result(phases), "failed": True, "msg": msg, **fields}


def skipped():
    return {"changed": False, "skipped": True, "skip_reason": "Conditional result was False"}


def report(run_module, results, **options):
    out = run_module(upgrade_report, {"hop": HOP, "results": results, **options})
    assert "failed" not in out, out.get("msg")
    assert out["changed"] is False
    return out["report"]


def whole_hop():
    """Every host of the hop through steps 2, 3, 5 and 7, as the role registers them."""
    return {
        "zdr01": [prepared(), ready(), committed("17:00:00", "17:05:00"), rebooted()],
        "edge01": [prepared(), ready(), committed("17:10:00", "17:14:00"), rebooted()],
        "edge02": [prepared(), ready(), committed("17:10:02", "17:16:00"), rebooted()],
        "edge03": [prepared(), ready(), committed("17:10:01", "17:15:00"), skipped(), rebooted()],
        "hub01": [prepared(), ready(), committed("17:16:30", "17:25:00"), rebooted()],
    }


def test_each_hosts_outcome_times_and_reboot(run_module):
    found = report(run_module, whole_hop())

    assert found["hop"] == {"number": 1, "release": "7.11.0", "lease": HOP["lease"], "moves": HOP["moves"]}
    assert found["check_mode_run"] is False
    assert sorted(found["hosts"]) == ["edge01", "edge02", "edge03", "hub01", "zdr01"]
    assert found["hosts"]["edge01"] == {
        "from": {"version": "7.10.3", "build": OLD},
        "to": {"version": "7.11.0", "build": NEW},
        "upgrade_type": "SOFTWARE_AND_PLATFORM",
        "phases": {
            "prepare": span("16:00:00", "16:20:00"),
            "ready": span("16:30:00", "16:30:05"),
            "commit": span("17:10:00", "17:14:00"),
            "reboots": span("18:00:00", "18:40:00"),
        },
        "prepare_seconds": 1200,
        "commit_started": "2026-11-04T17:10:00+00:00",
        "downtime_seconds": 40.5,
        "reboot": {"mode": "rolling", "set_size": None, "seconds": 2400},
        "commit_sent": True,
        "committed": True,
        "outcome": "rebooted",
        "lines": [],
    }
    assert {h: e["outcome"] for h, e in found["hosts"].items()} == dict.fromkeys(found["hosts"], "rebooted")
    assert (found["warnings"], found["lines"]) == ([], [])


def test_a_software_only_upgrade_is_committed_with_no_reboot(run_module):
    software = {"upgrade_type": "SOFTWARE_ONLY", "reboot": {"mode": "none", "set_size": None, "seconds": None}}
    results = {"zdr01": [{**prepared(), **software}, {**committed("17:00:00", "17:05:00", downtime=12.0), **software}]}

    entry = report(run_module, results)["hosts"]["zdr01"]

    assert (entry["outcome"], entry["committed"], entry["upgrade_type"], entry["reboot"]) == (
        "committed",
        True,
        "SOFTWARE_ONLY",
        {"mode": "none", "set_size": None, "seconds": None},
    )


def test_only_return_fields_are_read_never_the_invocation(run_module):
    text = json.dumps(report(run_module, whole_hop()))

    assert "hunter2" not in text and "X-Amz" not in text and "module_args" not in text


def test_a_components_mismatch_window_from_its_first_commits_start_to_its_last_members_commit_done(run_module):
    found = report(run_module, whole_hop())

    assert found["components"] == {
        "hub01": {
            "members": ["edge01", "edge02", "edge03", "hub01"],
            "moving": ["edge01", "edge02", "edge03", "hub01"],
            "split": False,
            "window": {"started": "2026-11-04T17:10:00Z", "ended": "2026-11-04T17:25:00Z", "seconds": 900, "open": False, "uncommitted": []},
            "two_releases": False,
        }
    }


def test_a_component_left_on_two_releases_and_the_hosts_its_failure_held(run_module):
    results = whole_hop()
    results["edge02"] = [
        prepared(),
        ready(),
        failed(f"edge02's commit to 7.11.0 was sent, and the image {SIGNED} ...", {"commit": span("17:10:02", None)}, commit_sent=True, maybe_applied=True),
    ]
    results["hub01"] = [prepared(), ready()]

    found = report(run_module, results, held=["edge01", "edge02", "edge03", "hub01"])

    assert found["components"]["hub01"]["window"] == {
        "started": "2026-11-04T17:10:00Z",
        "ended": None,
        "seconds": None,
        "open": True,
        "uncommitted": ["edge02", "hub01"],
    }
    assert found["components"]["hub01"]["two_releases"] is True
    assert found["lines"] == ["hub01's portal component was left on two releases: edge02, hub01 did not commit to 7.11.0."]
    edge02, hub01 = found["hosts"]["edge02"], found["hosts"]["hub01"]
    assert (edge02["outcome"], edge02["committed"], edge02["commit_sent"], edge02["maybe_applied"], edge02["unfinished_commit"]) == (
        "failed",
        False,
        True,
        True,
        False,
    )
    assert edge02["message"] == f"edge02's commit to 7.11.0 was sent, and the image {IMAGE} ..."
    assert (hub01["outcome"], hub01["committed"], hub01["held_by"], hub01["lock_held"]) == ("held", False, "edge02", False)
    assert hub01["lines"] == ["hub01 was not committed: the failure of edge02 holds it."]
    assert found["hosts"]["edge01"]["outcome"] == "rebooted"


def test_a_held_unit_is_reported_as_not_committed_with_the_host_in_the_unit_it_follows_that_held_it(run_module):
    results = {
        "zdr01": [prepared(), failed("zdr01: Rolling reboot must reboot 1 or fewer nodes at once ...", final=True)],
        **{h: [prepared(), ready()] for h in ("edge01", "edge02", "edge03", "hub01")},
    }

    found = report(run_module, results, held=["edge01", "edge02", "edge03", "hub01", "zdr01"])

    assert {h: (e["outcome"], e["committed"], e.get("held_by")) for h, e in found["hosts"].items()} == {
        "zdr01": ("failed", False, None),
        "edge01": ("held", False, "zdr01"),
        "edge02": ("held", False, "zdr01"),
        "edge03": ("held", False, "zdr01"),
        "hub01": ("held", False, "zdr01"),
    }
    assert found["components"]["hub01"] == {**found["components"]["hub01"], "window": None, "two_releases": False}


def test_a_host_a_refused_lock_holds_and_one_the_run_held_alone(run_module):
    found = report(run_module, {"zdr01": [prepared(), ready()], "edge01": [prepared()]}, held=["zdr01", "edge01"], lock_held=["zdr01"])

    zdr01, edge01 = found["hosts"]["zdr01"], found["hosts"]["edge01"]
    assert (zdr01["outcome"], zdr01["held_by"], zdr01["lock_held"]) == ("held", None, True)
    assert zdr01["lines"] == ["zdr01 was not committed: a restore lock the run could not take protects it."]
    assert (edge01["outcome"], edge01["held_by"], edge01["lock_held"]) == ("held", None, False)
    assert edge01["lines"] == ["edge01 was not committed: the run held it."]


def test_a_failed_prepare_prepared_again_and_committed_reports_its_last_result(run_module):
    results = {"zdr01": [failed("zdr01: the prepare's answer was lost ..."), prepared("16:40:00", "16:55:00"), ready(), committed("17:00:00", "17:05:00")]}

    entry = report(run_module, results)["hosts"]["zdr01"]

    assert (entry["outcome"], entry["prepare_seconds"], entry["commit_started"]) == ("committed", 900, "2026-11-04T17:00:00+00:00")


def test_a_first_prepare_kept_for_a_second_that_a_siblings_failure_held_is_held_by_it(run_module):
    """edge01's first prepare failed, and the run kept it for a second prepare; edge02's refusal, which no second
    prepare lifts, then held their component, so edge01's only result is the failed first prepare."""
    results = {"edge01": [failed("edge01: the prepare's answer was lost ...")], "edge02": [failed("edge02 holds another image.", final=True)]}

    found = report(run_module, results, held=["edge01", "edge02", "edge03", "hub01"], prepare_failed=["edge01"])

    edge01, edge02 = found["hosts"]["edge01"], found["hosts"]["edge02"]
    assert (edge01["outcome"], edge01["held_by"], edge01["lock_held"]) == ("held", "edge02", False)
    assert "message" not in edge01
    assert edge01["lines"] == [
        "edge01's first prepare failed and was not tried again, since its unit was held: edge01: the prepare's answer was lost ...",
        "edge01 was not committed: the failure of edge02 holds it.",
    ]
    assert (edge02["outcome"], edge02["message"]) == ("failed", "edge02 holds another image.")
    assert {found["hosts"][h]["held_by"] for h in ("edge03", "hub01")} == {"edge02"}


def test_a_first_prepare_kept_for_a_second_that_failed_again_is_failed_with_the_second(run_module):
    results = {"zdr01": [failed("zdr01: the prepare's answer was lost ..."), failed("zdr01: the prepare timed out ...")]}

    entry = report(run_module, results, held=["zdr01"], prepare_failed=["zdr01"])["hosts"]["zdr01"]

    assert (entry["outcome"], entry["message"]) == ("failed", "zdr01: the prepare timed out ...")


def test_the_reboots_mode_and_set_size_come_from_the_commit_and_only_the_seconds_from_its_wait(run_module):
    """Step 7's wait reads the mode back after the fact, and a cluster that rebooted every node reads it as rolling."""
    commit = {**committed("17:00:00", "17:05:00"), "reboot": {"mode": "all", "set_size": None, "seconds": None}}
    wait = {**rebooted(600), "reboot": {"mode": "rolling", "set_size": None, "seconds": 600}}

    entry = report(run_module, {"zdr01": [prepared(), ready(), commit, wait]})["hosts"]["zdr01"]

    assert entry["reboot"] == {"mode": "all", "set_size": None, "seconds": 600}


def test_a_component_that_started_split_and_that_the_hop_does_not_close_stays_split(run_module):
    """hub01, on 7.9.3, lands on 7.10.0.3 in the first hop; its spoke, with no 7.10.0.3 in its own list, waits."""
    quarterlies = [f"https://images.example.com/qumulo/qumulo_core_{r}.qimg" for r in ("7.10.0.3", "7.10.3")]
    hop = the_hop(
        fleet_host("hub01", version="7.9.3", target="7.10.3", images=quarterlies),
        fleet_host("edge01", hub="hub01", version="7.10.0.1", target="7.10.3", images=quarterlies[1:]),
    )
    assert (hop["release"], hop["moves"], hop["components"]["hub01"]["split"], hop["components"]["hub01"]["closes"]) == (
        "7.10.0.3",
        ["hub01"],
        True,
        False,
    )

    out = run_module(upgrade_report, {"hop": hop, "results": {"hub01": [prepared(), ready(), committed("17:00:00", "17:05:00"), rebooted()]}})

    assert out["report"]["components"]["hub01"]["two_releases"] is True
    assert out["report"]["lines"] == ["hub01's portal component stays split after the hop to 7.10.0.3."]


def test_a_commit_done_whose_reboots_failed_is_failed_and_committed(run_module):
    results = {"zdr01": [prepared(), ready(), committed("17:00:00", "17:05:00"), failed("zdr01's reboot stopped on an error: ...")]}

    entry = report(run_module, results)["hosts"]["zdr01"]

    assert (entry["outcome"], entry["committed"], entry["message"]) == ("failed", True, "zdr01's reboot stopped on an error: ...")


def test_a_host_already_at_the_hops_release(run_module):
    entry = report(run_module, {"zdr01": [result(version="7.11.0", build=NEW, to=("7.11.0", NEW))]})["hosts"]["zdr01"]

    assert (entry["outcome"], entry["committed"], entry["phases"]) == ("at target", False, {})


def test_a_host_with_no_result_is_not_committed(run_module):
    entry = report(run_module, {})["hosts"]["hub01"]

    assert entry == {**entry, "outcome": "not committed", "committed": False, "from": None, "to": None, "phases": {}, "lines": []}


def test_a_check_run_reports_each_hosts_prediction(run_module):
    prediction = {"from": {"version": "7.10.3", "build": OLD, "state": "IDLE"}, "phases": ["prepare", "commit", "reboots"], "waits": []}
    results = {h: [result(changed=True, plan=prediction)] for h in HOP["moves"]}

    found = report(run_module, results, check_mode_run=True)

    assert found["check_mode_run"] is True
    assert {h: e["outcome"] for h, e in found["hosts"].items()} == dict.fromkeys(HOP["moves"], "predicted")
    assert found["hosts"]["hub01"]["prediction"] == prediction


def test_a_readiness_task_that_prepared_again_gets_its_line(run_module):
    results = whole_hop()
    results["hub01"] = [prepared(), ready(prepared=True), committed("17:16:30", "17:25:00")]

    found = report(run_module, results)

    assert found["hosts"]["hub01"]["lines"] == ["hub01 was prepared again at its commit step's readiness check, inside its component's mismatch window."]


def test_the_results_notes_and_warnings_redacted(run_module):
    results = whole_hop()
    results["zdr01"][0] = {
        **prepared(),
        "notes": [f"zdr01 reads IDLE with an earlier attempt's image {SIGNED} and its error."],
        "warnings": [f"zdr01: the earlier image {SIGNED} was replaced."],
    }
    results["edge01"][1] = {**ready(), "warnings": ["edge01's configured node fault tolerance is 0, so ..."]}

    found = report(run_module, results)

    assert found["hosts"]["zdr01"]["lines"] == [f"zdr01 reads IDLE with an earlier attempt's image {IMAGE} and its error."]
    assert found["warnings"] == [
        {"host": "edge01", "message": "edge01's configured node fault tolerance is 0, so ..."},
        {"host": "zdr01", "message": f"zdr01: the earlier image {IMAGE} was replaced."},
    ]


def test_the_peer_steps_readings_and_their_lines(run_module):
    peers = {
        "commits": [
            {
                "component": "hub01",
                "host": "hub01",
                "active": None,
                "seconds": None,
                "line": "hub01's portals were not read: edge01 took a rolling platform reboot.",
            },
            {
                "source": "hub01",
                "target": "zdr01",
                "error": f"could not reach {SIGNED}",
                "line": f"hub01 to zdr01: the last job failed: could not reach {SIGNED}",
            },
        ],
        "reboots": [{"component": "hub01", "host": "hub01", "active": True, "seconds": 42, "line": "hub01's portals read ACTIVE after 42 seconds."}],
    }

    found = report(run_module, whole_hop(), peers=peers)

    assert found["peers"]["commits"][1]["error"] == f"could not reach {IMAGE}"
    assert found["peers"]["reboots"] == peers["reboots"]
    assert found["lines"] == [
        "hub01's portals were not read: edge01 took a rolling platform reboot.",
        f"hub01 to zdr01: the last job failed: could not reach {IMAGE}",
        "hub01's portals read ACTIVE after 42 seconds.",
    ]


def test_the_window_forms_answer_and_its_lines(run_module):
    window = {
        "declared": True,
        "inside": False,
        "next_open": "2026-11-08T06:00:00Z",
        "outside_hosts": ["zdr01"],
        "held": ["zdr01"],
        "lines": ["hub01 commits outside the maintenance window, to close the split of hub01's portal component."],
        "message": "zdr01 were not committed: the hop to 7.11.0 is outside their maintenance window, which next opens ...",
    }

    found = report(run_module, whole_hop(), window=window)

    assert found["window"] == window
    assert found["lines"] == [window["message"], *window["lines"]]


def test_the_roles_own_lines_come_first_redacted(run_module):
    """The role's line for a run it stopped before the hop, as a failed lock renewal stops it."""
    stopped = f"The run stopped before the hop to 7.11.0: zdr01's restore lock could not be renewed: {SIGNED} is held."
    window = {"declared": True, "inside": True, "lines": ["hub01 commits inside the maintenance window."], "message": None}

    found = report(run_module, {}, lines=[stopped], window=window)

    assert found["lines"] == [stopped.replace(SIGNED, IMAGE), "hub01 commits inside the maintenance window."]


@pytest.mark.parametrize("options", [{}, {"window": None}], ids=["no window", "null window"])
def test_no_window_form_is_none(run_module, options):
    assert report(run_module, whole_hop(), **options)["window"] is None


def test_the_report_has_no_no_log_option_and_registers_no_secret():
    def no_log_options(spec):
        return [name for name, option in spec.items() if "no_log" in option or no_log_options(option.get("options") or {})]

    assert no_log_options(upgrade_report.ARGUMENT_SPEC) == []
    with open(upgrade_report.__file__, encoding="utf-8") as source:
        assert "register_secret" not in source.read()


def run_main(capsys, module_args):
    """main() through AnsibleModule's own exit_json, which below ansible-core 2.22 masks every no_log value it holds."""
    with patch_module_args({**module_args, "_ansible_check_mode": False, "_ansible_diff": False}), pytest.raises(SystemExit):
        upgrade_report.main()
    return json.loads(capsys.readouterr().out)


def test_through_exit_json_host_names_releases_and_numbers_read_unmasked(capsys):
    """Ansible registers a no_log option's value as VALUE_SPECIFIED_IN_NO_LOG_PARAMETER; the report must still read in full."""
    masked = {"module_args": {**INVOCATION["module_args"], "image_secret": "VALUE_SPECIFIED_IN_NO_LOG_PARAMETER"}}
    results = {host: [{**r, "invocation": masked} if "invocation" in r else r for r in found] for host, found in whole_hop().items()}

    out = run_main(capsys, {"hop": HOP, "results": results})

    assert "failed" not in out
    assert "********" not in json.dumps(out)
    hosts = out["report"]["hosts"]
    assert {h: e["outcome"] for h, e in hosts.items()} == dict.fromkeys(HOP["moves"], "rebooted")
    assert (hosts["edge01"]["from"]["version"], hosts["edge01"]["to"]["version"], hosts["edge01"]["downtime_seconds"]) == ("7.10.3", "7.11.0", 40.5)
    assert out["report"]["hop"] == {"number": 1, "release": "7.11.0", "lease": HOP["lease"], "moves": HOP["moves"]}
