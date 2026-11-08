# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""upgrade_fleet.py's plan, commit schedule, maintenance window and lease (specs/017-cluster-upgrade)."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade_fleet, upgrade_record
from ansible_collections.qumulo.cluster.plugins.module_utils.version import ClusterVersion

MIRROR = "https://mirror.example.com/qumulo"


def v(text, build_id=""):
    return ClusterVersion.parse(text, build_id)


def onprem(release):
    return f"{MIRROR}/qumulo_core_{release}.qimg"


def cloud(release):
    return f"{MIRROR}/cloud_qumulo_core_{release}.qimg"


PATH = "/inv/artifacts/edge01/upgrade/commit.yml"


UUID = "6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11"


OLD, NEW = "385145.1.82", "393623.1.1"


def record(state="pending", **overrides):
    fields = {
        "format": 1,
        "state": state,
        "cluster_uuid": UUID,
        "build": OLD,
        "target_version": "7.11.0",
        "declared_build": None,
        "time": "2026-11-04T05:00:00Z",
        **overrides,
    }
    return "\n".join(f"{k}: {'null' if v is None else (v if isinstance(v, int) else repr(str(v)))}" for k, v in fields.items()) + "\n"


F19_PENDING = (
    f"edge01: a commit record, {PATH}, shows that its commit to 7.11.0 on 2026-11-04T05:00:00Z did not finish, and some "
    "nodes may be armed. Nothing was sent. Contact Qumulo Care. Once they have cleared edge01, remove the record; it "
    "also goes by itself once edge01 runs a new build of 7.11.0 or a later release. To upgrade the rest of the fleet "
    "meanwhile, leave edge01 out of the run with --limit."
)


# The fleet: fleet_hops over every inventory host (FR-010 to FR-016)

NOW = "2026-11-04T17:00:00Z"  # a Wednesday, 09:00 in Los Angeles
EC = "PROTECTION_SYSTEM_TYPE_EC"
OBJECT = "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED"


def fleet_host(name, version="7.10.3", target="7.11.0", entries=None, hub=None, configured=1, **extra):
    return {
        "name": name,
        "cluster_uuid": f"uuid-{name}",
        "version": version,
        "build": OLD,
        "status": {"state": "UPGRADE_STATE_IDLE", "settings": None, "error_info": None},
        "protection": {"type": EC, "configured_node_failures": configured, "remaining_node_failures": configured},
        "license_state": "LICENSE_STATE_ACTIVE",
        "cluster_kind": "edge",
        "target": target,
        "images": [onprem("7.11.0")] if entries is None else entries,
        "portal_hub": hub,
        **extra,
    }


def replicates(*targets, state="present"):
    return [{"target": t, "state": state, "source_path": "/projects", "target_path": "/dr"} for t in targets]


def fleet(*hosts, **kw):
    kw.setdefault("now", NOW)
    return upgrade_fleet.fleet_hops(list(hosts), **kw)


def refused(result):
    return [(r["host"], r["code"]) for r in result["refusals"]]


def warned(result, code):
    return [w["message"] for w in result["warnings"] if w["code"] == code]


def hub_and_spokes(**extra):
    """User Story 2's fleet: hub01 with three edges as spokes, and hub01 replicating to a DR cluster, zdr01."""
    return [
        fleet_host("hub01", relationships=replicates("zdr01"), **extra),
        *(fleet_host(f"edge0{i}", hub="hub01", **extra) for i in (1, 2, 3)),
        fleet_host("zdr01", **extra),
    ]


QUARTERLIES = [onprem("7.9.0.5"), onprem("7.10.0.3"), onprem("7.10.3")]


def test_hosts_on_different_releases_and_targets_hop_in_lock_step():
    result = fleet(
        fleet_host("a", "7.8.4", "7.10.3", QUARTERLIES),
        fleet_host("b", "7.9.0.5", "7.10.3", QUARTERLIES),
        fleet_host("c", "7.8.4", "7.10.0.3", QUARTERLIES),
    )

    assert result["refusals"] == []
    assert [(h["release"], h["moves"]) for h in result["hops"]] == [("7.9.0.5", ["a", "c"]), ("7.10.0.3", ["a", "b", "c"]), ("7.10.3", ["a", "b"])]
    assert "b does not move in the hop to 7.9.0.5; its next hop is 7.10.0.3." in result["hops"][0]["notes"]
    assert result["hops"][0]["hosts"]["a"] == {
        "image_index": 0,
        "image_redacted": onprem("7.9.0.5"),
        "version": "7.9.0.5",
        "build": None,
        "kind": "on-prem",
        "reboot": "rolling",
        "unit": "a",
        "round": 1,
        "wave": None,
        "step": 0,
        "holds": ["a"],
        "reboots_only": False,
    }


def test_a_host_at_its_target_is_skipped_and_one_still_rebooting_joins_the_last_hop_for_its_reboots_alone():
    rebooting = {"state": "UPGRADE_STATE_REBOOTING", "settings": None, "error_info": None}
    result = fleet(fleet_host("a"), fleet_host("b", "7.11.0"), fleet_host("c", "7.11.0", status=rebooting))

    (hop,) = result["hops"]
    assert (hop["moves"], result["skipped"]) == (["a"], ["b"])
    assert (hop["hosts"]["c"]["reboots_only"], hop["hosts"]["c"]["unit"], hop["hosts"]["c"]["image_index"]) == (True, None, 0)


def test_a_host_still_rebooting_whose_list_lacks_the_target_still_joins_the_last_hop_with_the_targets_release():
    """The role's reboot step then passes version and no image, since the module, with commit false, needs none."""
    rebooting = {"state": "UPGRADE_STATE_REBOOTING", "settings": None, "error_info": None}
    result = fleet(fleet_host("a"), fleet_host("c", "7.11.0", entries=[], status=rebooting))

    assert result["refusals"] == []
    entry = result["hops"][0]["hosts"]["c"]
    assert (entry["reboots_only"], entry["image_index"], entry["image_redacted"], entry["version"], entry["build"]) == (True, None, None, "7.11.0", None)


def test_f1_a_limited_release_source_without_the_option():
    result = fleet(fleet_host("a", "7.11.0A", "7.11.1", [onprem("7.11.1")]))

    assert result["refusals"] == [
        {
            "host": "a",
            "code": "F1",
            "message": "a runs Qumulo Core 7.11.0A, a limited release, and Qumulo Core refuses to upgrade from a limited release unless the "
            "rule is waived. Contact Qumulo Care. Set qumulo_upgrade_allow_limited_release: true for a only when Qumulo Care directs it. "
            "To upgrade the rest of the fleet meanwhile, leave a out of the run with --limit.",
        }
    ]
    assert result["hops"] == []


@pytest.mark.parametrize(
    "image", ["relative/qumulo_core_7.11.0.qimg", "ftp://mirror.example.com/qumulo_core_7.11.0.qimg", "https:/mirror.example.com/qumulo_core_7.11.0.qimg"]
)
def test_f2_an_image_neither_a_url_nor_an_absolute_path(image):
    result = fleet(fleet_host("a", entries=[image]))

    assert result["refusals"][0]["code"] == "F2"
    assert result["refusals"][0]["message"] == (
        f"a: the image {image} is neither an http:// or https:// URL nor an absolute path. Give a URL every node can reach, or an "
        "absolute path in the cluster's file system."
    )


def test_an_absolute_path_is_an_image():
    assert fleet(fleet_host("a", entries=["/upgrades/qumulo_core_7.11.0.qimg"]))["refusals"] == []


def test_f3_from_the_hosts_hops():
    assert refused(fleet(fleet_host("a", "7.11.1"))) == [("a", "F3")]


def test_f16_the_boolean_waiver_without_a_declared_build():
    result = fleet(fleet_host("a", "7.10.3A", "7.11.0", allow_limited_release=True))

    assert result["refusals"][0]["message"] == (
        "a runs 7.10.3A, below 7.11.0, where Qumulo Core's waiver lifts every compatibility rule, so the collection sends it only for "
        "a declared build. Declare the build Qumulo Care names for a's first hop as build in that hop's mapping entry, in a "
        "qumulo_upgrade_images list of a's own. To upgrade the rest of the fleet meanwhile, leave a out of the run with --limit."
    )


def test_the_boolean_waiver_with_a_declared_build_and_the_named_one_are_planned():
    declared = [{"version": "7.11.0", "kind": "on-prem", "image": onprem("7.11.0"), "build": "393623.1.1"}]

    assert fleet(fleet_host("a", "7.10.3A", "7.11.0", declared, allow_limited_release=True))["refusals"] == []
    assert fleet(fleet_host("a", "7.11.0A", "7.11.1", [onprem("7.11.1")], allow_limited_release=True))["refusals"] == []


def status(state, image=None, auto_commit=False, error=None):
    return {"state": f"UPGRADE_STATE_{state}", "settings": {"install_path": image, "auto_commit": auto_commit}, "error_info": error}


def test_f6_preparing_an_upgrade_that_commits_itself():
    result = fleet(fleet_host("a", status=status("PREPARING", onprem("7.11.0"), auto_commit=True)))

    assert result["refusals"][0] == {
        "host": "a",
        "code": "F6",
        "message": "a is preparing an upgrade that commits itself as soon as it is prepared, outside this run's order. Wait for it to "
        "finish, then run the upgrade again, or, to upgrade the rest of the fleet meanwhile, leave a out of the run with --limit.",
    }


@pytest.mark.parametrize("state", ["PREPARING", "COMMITTING", "COMMITTED"])
def test_f6_another_image_started_outside_the_run_compared_redacted(state):
    other = "https://user:pw@other.example.com/qumulo_core_7.11.0.qimg?sig=abc"

    result = fleet(fleet_host("a", status=status(state, other)))

    assert result["refusals"][0]["message"] == (
        f"a is {state} with the image https://other.example.com/qumulo_core_7.11.0.qimg, started outside this run. Wait for it to "
        "finish, then run the upgrade again, or, to upgrade the rest of the fleet meanwhile, leave a out of the run with --limit."
    )


def test_the_hops_image_with_another_query_string_is_not_f6():
    assert fleet(fleet_host("a", status=status("PREPARING", onprem("7.11.0") + "?sig=new")))["refusals"] == []


def test_idle_with_auto_commit_and_an_error_is_only_a_note():
    error = "Qimg Download from: https://mirror.example.com/x.qimg?sig=abc failed"
    result = fleet(fleet_host("a", status=status("IDLE", onprem("7.11.0"), auto_commit=True, error=error)))

    assert result["refusals"] == []
    (note,) = result["notes"]
    assert note["host"] == "a"
    assert "https://mirror.example.com/x.qimg failed" in note["message"]
    assert "sig=abc" not in note["message"]


def test_f13_a_limit_that_leaves_members_out_and_a_member_that_left_the_play():
    hosts = hub_and_spokes()
    hosts[3]["in_play"] = False  # edge03's read failed

    result = fleet(*hosts, limit=["hub01", "edge01", "edge03", "zdr01"])

    assert result["refusals"] == [
        {
            "host": "hub01",
            "code": "F13",
            "message": "hub01's portal component has members outside this run: edge02 (left out by the limit), edge03 (its read failed). "
            "Add them to the limit, or fix what stopped their read; members already at the target are skipped.",
        }
    ]


def test_a_limit_that_leaves_a_whole_component_out_is_planned():
    result = fleet(*hub_and_spokes(), limit=["zdr01"])

    assert (result["refusals"], [h["moves"] for h in result["hops"]]) == ([], [["zdr01"]])


def test_f12_a_hop_that_would_split_a_component_that_started_on_one_release():
    result = fleet(fleet_host("hub01"), fleet_host("edge01", hub="hub01", target="7.10.3"))

    assert result["refusals"] == [
        {
            "host": "hub01",
            "code": "F12",
            "message": "hub01's portal component would end the hop to 7.11.0 on two releases: edge01 on 7.10.3, hub01 on 7.11.0. Give "
            "every member of the component an image of the same release for this hop, and the same qumulo_upgrade_target.",
        }
    ]


def test_w3_for_present_relationships_only():
    far = [onprem("7.11.0")]
    result = fleet(
        fleet_host("a", "7.2.3.2", "7.2.3.2", far, relationships=replicates("b")),
        fleet_host("b", "7.10.3", "7.11.0", far),
        fleet_host("c", "7.2.3.2", "7.2.3.2", far, relationships=replicates("b", state="absent")),
    )

    assert warned(result, "W3") == [
        "a replicates to b, which would be 9 quarterly releases apart after the hop to 7.11.0. Replication refuses clusters about eight quarters apart."
    ]


@pytest.mark.parametrize(("a", "b", "apart"), [("7.10.3", "8.1.0", 2), ("8.1.0", "7.10.3", 2), ("7.9.0.5", "7.11.2", 2), ("7.11.0", "7.11.3", 0)])
def test_quarters_apart_ends_across_a_major_without_named(a, b, apart):
    """The later of the two always counts as named, so the walk reaches it across a major."""
    assert upgrade_fleet.quarters_apart(v(a), v(b)) == apart


def test_w4_and_w7_from_live_portals_the_inventory_does_not_declare():
    result = fleet(fleet_host("hub01", live_hub_portals=2), fleet_host("edge01", hub="hub01", live_spoke_portals=1), fleet_host("x", live_spoke_portals=1))

    assert warned(result, "W4") == [
        (
            "hub01 has 2 live hub portals, and the inventory declares 1 spokes for it. A spoke the inventory does not declare is not "
            "ordered before its hub, and its portal stays down until it matches."
        )
    ]
    assert warned(result, "W7") == [
        "x has 1 live spoke portals, and the inventory declares 0. Its hub is not ordered after it, and the portal stays down until the two match."
    ]


def test_neither_w4_nor_w7_for_a_host_whose_read_lists_portals_unavailable():
    unavailable = {"unavailable_subsets": ["portals"]}
    result = fleet(fleet_host("hub01", live_hub_portals=2, **unavailable), fleet_host("x", live_spoke_portals=1, **unavailable))

    assert warned(result, "W4") == warned(result, "W7") == []


def test_w2_an_unlicensed_edge_by_its_product_or_its_kind():
    result = fleet(
        fleet_host("a", license_state="LICENSE_STATE_EXPIRED", cluster_kind=None, unprotected_edge=True),
        fleet_host("b", license_state="LICENSE_STATE_NONE"),
        fleet_host("c", license_state="LICENSE_STATE_NONE", cluster_kind="baremetal"),
    )

    assert warned(result, "W2") == [
        f"{host} reads license {state}. Qumulo Core refuses to upgrade an unlicensed edge formed more than 30 days ago, and then its "
        f"portal component waits for it. License it with ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> "
        f"-l {host} before the run."
        for host, state in (("a", "LICENSE_STATE_EXPIRED"), ("b", "LICENSE_STATE_NONE"))
    ]


def test_w5_only_once_the_lanes_set_the_oldest_tested_source(monkeypatch):
    hosts = [fleet_host("a", "7.9.0.5", "7.10.3", QUARTERLIES[1:]), fleet_host("b", "7.10.0.3", "7.10.3", QUARTERLIES[1:])]
    assert warned(fleet(*hosts), "W5") == []

    monkeypatch.setattr(upgrade_fleet, "OLDEST_TESTED_SOURCE", ("7.10.0.3", "2026-12-01"))

    assert warned(fleet(*hosts), "W5") == ["a runs 7.9.0.5, older than 7.10.0.3, the oldest release the collection's tests have upgraded from (2026-12-01)."]


def test_f14_a_host_without_a_target_while_others_declare_one():
    result = fleet(fleet_host("a"), fleet_host("b", target=None), fleet_host("hub01"), fleet_host("edge01", hub="hub01", target=None))

    assert [r["message"] for r in result["refusals"]] == [
        "b declares no qumulo_upgrade_target, while 2 other hosts of this run do. Set qumulo_upgrade_target for b, or leave b out of the run with --limit.",
        (
            "edge01 declares no qumulo_upgrade_target, while 2 other hosts of this run do, and it belongs to hub01's portal component. Set "
            "qumulo_upgrade_target for edge01, or leave the whole component out of the run with --limit."
        ),
    ]


def test_f34_windows_without_a_timezone():
    result = fleet(fleet_host("a", maintenance_windows=[window("SAT", 22, 6)]))

    assert result["refusals"][0]["message"] == (
        "a declares qumulo_upgrade_maintenance_windows but no qumulo_upgrade_window_timezone. Set qumulo_upgrade_window_timezone for "
        "a to an IANA name, such as America/Los_Angeles."
    )


def test_no_message_holds_a_query_string_or_user_information():
    secret = "https://user:pw@mirror.example.com/qumulo/qumulo_core_7.11.0.qimg?X-Amz-Signature=abc"
    result = fleet(
        fleet_host("a", "7.11.1", entries=[secret]), fleet_host("b", entries=[secret], status=status("COMMITTING", secret.replace("7.11.0", "7.10.9")))
    )

    text = repr(result)
    assert "X-Amz-Signature" not in text
    assert "user:pw" not in text


# split and closes (FR-017), at the hop's start and end


def marks(result, hop=0, name="hub01"):
    found = result["hops"][hop]["components"][name]
    return found["split"], found["closes"]


def test_split_one_spoke_already_at_the_hops_release_which_does_not_move():
    result = fleet(fleet_host("hub01"), fleet_host("edge01", "7.11.0", hub="hub01"), fleet_host("edge02", hub="hub01"))

    assert result["hops"][0]["moves"] == ["edge02", "hub01"]
    assert marks(result) == (True, True)
    assert "hub01's portal component starts the hop to 7.11.0 split: edge01 on 7.11.0, edge02 on 7.10.3, hub01 on 7.10.3." in result["hops"][0]["notes"]


def test_split_a_hub_alone_below_its_spokes():
    result = fleet(fleet_host("hub01"), fleet_host("edge01", "7.11.0", hub="hub01"), fleet_host("edge02", "7.11.0", hub="hub01"))

    assert marks(result) == (True, True)


def test_split_members_below_the_hop_on_two_releases():
    entries = [onprem("7.10.3"), onprem("7.11.0")]
    result = fleet(fleet_host("hub01", "7.10.0.3", entries=entries), fleet_host("edge01", hub="hub01", entries=entries))

    # 7.11.0 accepts both 7.10.0.3 and 7.10.3, so both take it in one hop.
    assert [h["moves"] for h in result["hops"]] == [["edge01", "hub01"]]
    assert marks(result) == (True, True)


def test_every_member_on_one_release_is_not_split():
    assert marks(fleet(*hub_and_spokes())) == (False, False)


def test_a_split_that_the_hops_first_release_does_not_close():
    # The hub on 7.9.3 lands on 7.10.0.3 in hop 1; its spokes, on 7.10.0.1 with no 7.10.0.3 in their own list, wait.
    result = fleet(
        fleet_host("hub01", "7.9.3", "7.10.3", QUARTERLIES[1:]),
        fleet_host("edge01", "7.10.0.1", "7.10.3", [onprem("7.10.3")], hub="hub01"),
    )

    assert [(h["release"], h["moves"]) for h in result["hops"]] == [("7.10.0.3", ["hub01"]), ("7.10.3", ["edge01", "hub01"])]
    assert (marks(result, 0), marks(result, 1)) == ((True, False), (True, True))


# The reboot set size (F20, W10, FR-022, FR-025)


def test_a_group_set_size_over_an_edge_at_tolerance_0_is_ignored_with_w10():
    result = fleet(fleet_host("a", configured=0, reboot_set_size=2))

    assert result["refusals"] == []
    assert warned(result, "W10") == [
        (
            "a's configured node fault tolerance is 0, so a platform upgrade of a reboots every node at once, whatever "
            "qumulo_upgrade_reboot says; its qumulo_upgrade_reboot_set_size of 2 is ignored."
        )
    ]
    assert result["hops"][0]["hosts"]["a"]["reboot"] == "all"


def test_a_cnq_cluster_of_two_nodes_ignores_a_declared_rolling_reboot_with_w10():
    result = fleet(fleet_host("cnq01", configured=0, cluster_kind="cnq", entries=[cloud("7.11.0")], reboot="rolling", reboot_set_size=1))

    assert result["refusals"] == []
    assert warned(result, "W10")[0].endswith("whatever qumulo_upgrade_reboot says; its qumulo_upgrade_reboot_set_size of 1 is ignored.")


def test_tolerance_0_with_nothing_declared_gets_a_report_line_not_w10():
    result = fleet(fleet_host("a", configured=0))

    assert warned(result, "W10") == []
    assert result["notes"] == [{"host": "a", "message": "a reboots every node at once on a platform upgrade, since its configured node fault tolerance is 0."}]


def test_a_reboot_all_host_ignores_its_set_size_with_w10():
    result = fleet(fleet_host("a", configured=2, reboot="all", reboot_set_size=3))

    assert (refused(result), warned(result, "W10")) == ([], ["qumulo_upgrade_reboot is all for a, so its qumulo_upgrade_reboot_set_size of 3 is ignored."])


def test_f20_a_rolling_host_whose_configured_tolerance_is_below_the_count():
    result = fleet(fleet_host("a", configured=1, reboot_set_size=2))

    assert result["refusals"][0] == {
        "host": "a",
        "code": "F20",
        "message": "a: qumulo_upgrade_reboot_set_size is 2, above a's configured node fault tolerance of 1, which Qumulo Core would "
        "refuse at the prepare. Set it to 1 or lower for a, or leave it unset so the server sizes the groups.",
    }


# Commit records at the plan's level (FR-034); each row of the table is decide_record's, above


def with_record(host, text, path=PATH):
    host["commit_record"] = {"path": path, "text": text}
    return host


def test_a_record_of_a_host_the_limit_leaves_out_is_not_decided_and_the_plan_goes_on():
    result = fleet(fleet_host("a"), with_record(fleet_host("edge01"), record("unfinished")), limit=["a"])

    assert (result["refusals"], result["hops"][0]["moves"]) == ([], ["a"])


def test_a_pending_record_whose_hosts_read_failed_is_f30():
    result = fleet(fleet_host("a"), with_record(fleet_host("edge01", in_play=False), record()))

    assert refused(result) == [("edge01", "F30")]
    assert "(it reads no answer)" in result["refusals"][0]["message"]


def test_an_unfinished_record_whose_hosts_read_failed_is_f19_and_not_retired():
    result = fleet(fleet_host("a"), with_record(fleet_host("edge01", in_play=False), record("unfinished")))

    assert refused(result) == [("edge01", "F19")]
    assert result["records"] == {"retire": [], "drop": []}


def test_f19_stops_the_whole_plan_naming_the_limit():
    result = fleet(fleet_host("a"), with_record(fleet_host("edge01", cluster_uuid=UUID), record()))

    assert result["refusals"] == [{"host": "edge01", "code": "F19", "message": F19_PENDING}]
    assert result["hops"] == []


def test_records_entries_carry_the_files_digest():
    retired, dropped = record(), record(build=NEW)
    result = fleet(
        with_record(fleet_host("edge01", "7.11.0", cluster_uuid="0d4c1d38-0000-4000-8000-00000000abcd"), retired),
        with_record(fleet_host("edge02", build=NEW, cluster_uuid=UUID, status=status("PREPARED", onprem("7.11.0"))), dropped, "/inv/edge02/commit.yml"),
    )

    entry = result["records"]["retire"][0]
    assert (entry["host"], entry["path"], entry["digest"]) == ("edge01", PATH, upgrade_record.record_digest(retired))
    assert entry["line"].startswith(f"edge01: retired the commit record {PATH}: ")
    assert [(e["host"], e["path"], e["digest"]) for e in result["records"]["drop"]] == [
        ("edge02", "/inv/edge02/commit.yml", upgrade_record.record_digest(dropped))
    ]


# Aggressive reboots' scope (Decision 35), and the hold (Decision 46)


def test_f11_aggressive_in_the_inventory():
    result = fleet(fleet_host("a", reboot="aggressive"))

    assert result["refusals"][0]["message"] == (
        "qumulo_upgrade_reboot is aggressive in the inventory for a. Aggressive reboots are chosen for one run only, with "
        "-e qumulo_upgrade_aggressive=true; set qumulo_upgrade_reboot to rolling or all."
    )


def test_f11_the_extra_variable_while_the_hold_stands():
    assert fleet(fleet_host("a", configured=2), aggressive=True)["refusals"] == [
        {
            "host": None,
            "code": "F11",
            "message": "Aggressive reboots have not yet run live on a cluster they apply to, so this collection refuses "
            "qumulo_upgrade_aggressive. Run without it, and the reboots run as rolling.",
        }
    ]


def test_aggressive_scope_per_host_with_the_hold_lifted(monkeypatch):
    monkeypatch.setattr(upgrade_fleet, "AGGRESSIVE_HELD", False)
    object_backed = {"type": OBJECT, "configured_node_failures": 1, "remaining_node_failures": 1}

    result = fleet(
        fleet_host("ec2", configured=2),
        fleet_host("ec1", configured=1),
        fleet_host("ec0", configured=0),
        fleet_host("obj", protection=object_backed, cluster_kind="cnq", entries=[cloud("7.11.0")]),
        aggressive=True,
    )

    hosts = result["hops"][0]["hosts"]
    assert {h: hosts[h]["reboot"] for h in hosts} == {"ec0": "all", "ec1": "rolling", "ec2": "aggressive", "obj": "rolling"}
    assert warned(result, "W8") == [
        "Aggressive reboots do not apply to ec0's hop to 7.11.0: ec0's configured node fault tolerance is 0, below 2. Its reboots run as all.",
        "Aggressive reboots do not apply to ec1's hop to 7.11.0: ec1's configured node fault tolerance is 1, below 2. Its reboots run as rolling.",
        "Aggressive reboots do not apply to obj's hop to 7.11.0: obj is object-backed. Its reboots run as rolling.",
    ]


# The run's locks (FR-031), for the whole run


def test_lock_hosts_protect_themselves_and_their_targets_for_the_whole_run():
    rebooting = {"state": "UPGRADE_STATE_REBOOTING", "settings": None, "error_info": None}
    result = fleet(
        fleet_host("a", "7.10.0.3", "7.10.3", QUARTERLIES[1:], relationships=replicates("late")),
        fleet_host("late", "7.9.0.5", "7.10.3", QUARTERLIES[1:]),  # moves to 7.10.0.3 in hop 1 and to 7.10.3 only in hop 2
        fleet_host("r", "7.10.3", "7.10.3", QUARTERLIES[1:], status=rebooting),
        fleet_host("src", "7.10.3", "7.10.3", QUARTERLIES[1:], relationships=replicates("r")),
        fleet_host("idle", "7.10.3", "7.10.3", QUARTERLIES[1:], relationships=replicates("x")),
        fleet_host("x", "7.10.3", "7.10.3", QUARTERLIES[1:], relationships=replicates("a")),
    )

    assert result["lock_hosts"] == {"a": ["a", "late"], "late": ["late"], "r": ["r"], "src": ["r", "src"], "x": ["a", "x"]}


def test_w9_for_a_source_outside_the_play_and_w12_for_a_target_the_run_leaves_out():
    result = fleet(fleet_host("a", relationships=replicates("out")), fleet_host("b"), fleet_host("out", relationships=replicates("b")), limit=["a", "b"])

    assert warned(result, "W9") == [
        (
            "out, outside this run, replicates to b, which this run upgrades. The run cannot hold out's restore lock, so a restore of out "
            "started meanwhile is not held off, and the upgrade would restart b between two of its steps."
        )
    ]
    assert warned(result, "W12") == [
        (
            "a replicates to out, which this run leaves out, so a may upgrade ahead of its target and hold new kinds of state back from it "
            "until each file changes again. Add out to the limit, or upgrade it first."
        )
    ]
    assert result["lock_hosts"] == {"a": ["a", "out"], "b": ["b"]}


# W1: the restore files the scan found (FR-016, Decision 48)

RESTORE_DIR = "/inv/artifacts/{}/restore"


def scanned_record(edge, target_host, source_path="/projects"):
    """A record as replication_restore_info lists it."""
    key = {"source_path": source_path, "target_host": target_host, "target_cluster_uuid": "uuid-t", "target_path": "/dr"}
    return {"host": edge, "path": f"{RESTORE_DIR.format(edge)}/{'a' * 64}.yml", "key": key, "hosts": [edge, target_host]}


def scanned_lock(host, liveness="live", holder="restore"):
    return {
        "host": host,
        "path": f"{RESTORE_DIR.format(host)}/lock",
        "holder": holder,
        "controller": "ctl01",
        "pid": 41872,
        "start_time": "2026-11-04T09:00:00.000Z",
        "lease_expiry": "2026-11-05T09:00:00.000Z",
        "liveness": liveness,
    }


def record_w1(host, edge, source_path="/projects"):
    return (
        f"{host}: an open restore record, {RESTORE_DIR.format(edge)}/{'a' * 64}.yml, shows that the restore of {source_path} on {edge} has "
        f"not finished. The upgrade goes on, and restarts {host} between two of the restore's steps. Afterwards, run ansible-playbook "
        f"qumulo.cluster.replication_restore -i <inventory> -l {edge}, which finishes the restore and removes the record. If the "
        "relationship was reconnected by hand, remove the record."
    )


def lock_w1(host, edge):
    return (
        f"{host}: a live restore lock, {RESTORE_DIR.format(edge)}/lock, shows a restore of {edge} under way, by ansible-playbook process "
        f"41872 on ctl01, with a lease until 2026-11-05T09:00:00.000Z. The upgrade goes on, and may restart {host} between two of the "
        f"restore's steps. Afterwards, if that run stopped, run ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l {edge}, "
        "which finishes the restore and removes the record."
    )


def test_w1_for_a_record_of_a_moved_edge_and_of_one_whose_target_moves():
    files = {"records": [scanned_record("a", "dr"), scanned_record("src", "a", "/src")], "locks": []}

    result = fleet(fleet_host("a"), fleet_host("dr", "7.11.0"), fleet_host("src", "7.11.0"), restore_files=files)

    assert warned(result, "W1") == [record_w1("a", "a"), record_w1("a", "src", "/src")]


def test_w1_for_a_record_whose_target_was_renamed_names_the_edge_s_inventory_target():
    files = {"records": [scanned_record("src", "old-name")], "locks": []}

    result = fleet(fleet_host("a"), fleet_host("src", "7.11.0", relationships=replicates("a")), restore_files=files)

    assert warned(result, "W1") == [record_w1("a", "src")]


def test_w1_for_a_record_without_a_target_host_names_its_edge():
    record = {**scanned_record("a", None), "hosts": ["a"]}

    result = fleet(fleet_host("a"), restore_files={"records": [record], "locks": []})

    assert warned(result, "W1") == [record_w1("a", "a")]


def test_no_w1_for_a_record_naming_no_host_of_a_hop():
    files = {"records": [scanned_record("idle", "dr"), scanned_record("gone", "elsewhere")], "locks": []}

    result = fleet(fleet_host("a"), fleet_host("idle", "7.11.0"), fleet_host("dr", "7.11.0"), restore_files=files)

    assert warned(result, "W1") == []


def test_w1_for_a_live_lock_outside_the_run_s_locks_whose_source_replicates_to_a_moved_host():
    files = {"records": [], "locks": [scanned_lock("out")]}

    result = fleet(fleet_host("a"), fleet_host("out", "7.11.0", relationships=replicates("a")), limit=["a"], restore_files=files)

    assert "out" not in result["lock_hosts"]
    assert warned(result, "W1") == [lock_w1("a", "out")]


def test_w1_for_another_upgrade_s_live_lock_gives_no_restore_advice():
    files = {"records": [], "locks": [scanned_lock("out", holder="upgrade")]}

    result = fleet(fleet_host("a"), fleet_host("out", "7.11.0", relationships=replicates("a")), limit=["a"], restore_files=files)

    assert warned(result, "W1") == [
        (
            "a: a live restore lock, /inv/artifacts/out/restore/lock, shows another upgrade of out under way, by ansible-playbook process 41872 "
            "on ctl01, with a lease until 2026-11-05T09:00:00.000Z. This run upgrades a meanwhile."
        )
    ]


def test_w1_names_a_lock_s_free_form_holder_with_its_article():
    files = {"records": [], "locks": [scanned_lock("out", holder="nightly-restore")]}

    result = fleet(fleet_host("a"), fleet_host("out", "7.11.0", relationships=replicates("a")), limit=["a"], restore_files=files)

    assert warned(result, "W1")[0].startswith("a: a live restore lock, /inv/artifacts/out/restore/lock, shows a nightly-restore of out under way,")


def test_no_w1_for_a_lock_on_a_lock_host_or_a_lock_whose_run_is_gone():
    files = {"records": [], "locks": [scanned_lock("a"), scanned_lock("out", "gone"), scanned_lock("far", "expired")]}

    result = fleet(
        fleet_host("a"),
        fleet_host("out", "7.11.0", relationships=replicates("a")),
        fleet_host("far", "7.11.0", relationships=replicates("a")),
        limit=["a"],
        restore_files=files,
    )

    assert "a" in result["lock_hosts"]
    assert warned(result, "W1") == []


# The commit schedule (FR-015)


def hop_of(*hosts, **kw):
    result = fleet(*hosts, **kw)
    assert result["refusals"] == []
    return result["hops"][0]


def test_one_hub_its_spokes_together_then_the_hub_and_the_dr_cluster_first():
    hop = hop_of(*hub_and_spokes())

    assert hop["steps"] == [
        {"round": 1, "wave": None, "hosts": ["zdr01"]},
        {"round": 2, "wave": 0, "hosts": ["edge01", "edge02", "edge03"]},
        {"round": 2, "wave": 1, "hosts": ["hub01"]},
    ]
    assert hop["units"] == [
        {
            "name": "zdr01",
            "round": 1,
            "waves": [["zdr01"]],
            "follows": [],
            "reason": "a cluster in no portal component the hop moves; a replication target before its source, hub01",
        },
        {
            "name": "hub01",
            "round": 2,
            "waves": [["edge01", "edge02", "edge03"], ["hub01"]],
            "follows": ["zdr01"],
            "reason": "spokes together before their hub; after zdr01, which it replicates to",
        },
    ]
    assert (hop["hosts"]["edge02"]["unit"], hop["hosts"]["edge02"]["wave"], hop["hosts"]["edge02"]["step"]) == ("hub01", 0, 1)


def test_two_hubs_in_one_round_wave_by_wave_and_a_standalone_unit_after_both_last_waves():
    hop = hop_of(
        fleet_host("hubA"), fleet_host("a1", hub="hubA"), fleet_host("a2", hub="hubA"), fleet_host("hubB"), fleet_host("b1", hub="hubB"), fleet_host("s")
    )

    assert hop["steps"] == [
        {"round": 1, "wave": 0, "hosts": ["a1", "a2", "b1"]},
        {"round": 1, "wave": 1, "hosts": ["hubA", "hubB"]},
        {"round": 1, "wave": None, "hosts": ["s"]},
    ]


def test_a_round_of_standalone_units_only_is_one_step():
    assert hop_of(fleet_host("a"), fleet_host("b"), fleet_host("c"))["steps"] == [{"round": 1, "wave": None, "hosts": ["a", "b", "c"]}]


def test_a_nested_hubs_waves_go_by_height():
    hop = hop_of(fleet_host("h"), fleet_host("x", hub="h"), fleet_host("e1", hub="h"), fleet_host("x1", hub="x"), fleet_host("x2", hub="x"))

    assert hop["units"][0]["waves"] == [["e1", "x1", "x2"], ["x"], ["h"]]


def test_after_overrides_the_replication_preference_with_a_note():
    hosts = hub_and_spokes()
    hosts[-1]["after"] = ["hub01"]

    hop = hop_of(*hosts)

    assert [(u["name"], u["round"]) for u in hop["units"]] == [("hub01", 1), ("zdr01", 2)]
    assert (
        "hub01 replicates to zdr01, but qumulo_upgrade_after on zdr01 orders zdr01 after hub01: the declaration drops the "
        "replication preference for its target first."
    ) in hop["notes"]


def test_mutual_replication_keeps_one_edge_and_notes_the_dropped_one():
    hop = hop_of(fleet_host("a", relationships=replicates("b")), fleet_host("b", relationships=replicates("a")))

    assert [(u["name"], u["round"], u["follows"]) for u in hop["units"]] == [("b", 1, []), ("a", 2, ["b"])]
    assert hop["notes"] == ["b replicates to a, but the order already kept puts a after b, so this replication edge is dropped."]
    assert (hop["hosts"]["a"]["holds"], hop["hosts"]["b"]["holds"]) == (["a"], ["a", "b"])


def test_two_replication_edges_that_together_contradict_a_portal_edge():
    # edge01 replicates to s, and s to hub01: s would commit after edge01 and before hub01, inside one unit.
    hop = hop_of(fleet_host("hub01"), fleet_host("edge01", hub="hub01", relationships=replicates("s")), fleet_host("s", relationships=replicates("hub01")))

    assert [(u["name"], u["round"]) for u in hop["units"]] == [("s", 1), ("hub01", 2)]
    assert hop["notes"] == ["s replicates to hub01, but the order already kept puts hub01 after s, so this replication edge is dropped."]


def test_an_absent_relationship_orders_nothing():
    hosts = hub_and_spokes()
    hosts[0]["relationships"] = replicates("zdr01", state="absent")

    assert [(u["name"], u["round"]) for u in hop_of(*hosts)["units"]] == [("hub01", 1), ("zdr01", 1)]


def test_replication_inside_one_unit_orders_nothing():
    hop = hop_of(fleet_host("hub01", relationships=replicates("edge01")), fleet_host("edge01", hub="hub01"))

    assert (hop["notes"], len(hop["units"])) == ([], 1)


# follows and holds (FR-015, FR-019)


def test_a_failure_in_an_after_chain_holds_every_unit_after_it():
    hop = hop_of(fleet_host("a"), fleet_host("b", after=["a"]), fleet_host("c", after="b"))

    assert [(u["name"], u["round"], u["follows"]) for u in hop["units"]] == [("a", 1, []), ("b", 2, ["a"]), ("c", 3, ["a", "b"])]
    assert [hop["hosts"][h]["holds"] for h in "abc"] == [["a", "b", "c"], ["b", "c"], ["c"]]
    assert hop["units"][1]["reason"] == "a cluster in no portal component the hop moves; after a, by qumulo_upgrade_after on b"


def test_a_kept_replication_edges_target_holds_its_source():
    hop = hop_of(*hub_and_spokes())

    assert hop["hosts"]["zdr01"]["holds"] == ["edge01", "edge02", "edge03", "hub01", "zdr01"]
    assert hop["hosts"]["edge01"]["holds"] == ["edge01", "edge02", "edge03", "hub01"]


# F18: an order the plan refuses (FR-015)


def f18(*hosts):
    result = fleet(*hosts)
    assert {r["code"] for r in result["refusals"]} == {"F18"}
    return [r["message"] for r in result["refusals"]]


def test_f18_an_after_that_puts_a_hub_before_its_spoke():
    assert f18(fleet_host("hub01"), fleet_host("edge01", hub="hub01", after=["hub01"])) == [
        "qumulo_upgrade_after on edge01 puts hub01 before its spoke edge01. Change qumulo_upgrade_after on edge01."
    ]


@pytest.mark.parametrize(("declares", "names"), [("edge01", "edge02"), ("hub01", "edge01")], ids=["one wave", "two waves, as the waves agree"])
def test_f18_an_after_between_two_members_of_one_component(declares, names):
    hosts = {h: fleet_host(h, hub=None if h == "hub01" else "hub01") for h in ("hub01", "edge01", "edge02")}
    hosts[declares]["after"] = [names]

    assert f18(*hosts.values()) == [
        (
            f"qumulo_upgrade_after on {declares} orders {declares} and {names}, members of one portal component, whose order the portal "
            f"graph sets. Change qumulo_upgrade_after on {declares}."
        )
    ]


def test_f18_a_cycle_among_the_hard_edges():
    assert f18(fleet_host("a", after=["c"]), fleet_host("b", after=["a"]), fleet_host("c", after=["b"])) == [
        "qumulo_upgrade_after on c closes a cycle, a, b, c. Change qumulo_upgrade_after on a, b, c."
    ]


def test_f18_a_cycle_through_a_portal_component():
    # s after edge01 puts s after the hub's whole unit, which hub01's own after puts after s.
    assert f18(fleet_host("hub01", after=["s"]), fleet_host("edge01", hub="hub01"), fleet_host("s", after=["edge01"])) == [
        "qumulo_upgrade_after on s closes a cycle, hub01, s. Change qumulo_upgrade_after on hub01, s."
    ]


def test_f18_an_after_naming_no_inventory_host():
    assert f18(fleet_host("a", after=["nosuch"])) == ["qumulo_upgrade_after on a names nosuch, which is no inventory host. Change qumulo_upgrade_after on a."]


def test_f18_an_after_naming_no_inventory_host_on_a_host_that_moves_in_no_hop():
    assert f18(fleet_host("a"), fleet_host("b", "7.11.0", after=["nosuch"])) == [
        "qumulo_upgrade_after on b names nosuch, which is no inventory host. Change qumulo_upgrade_after on b."
    ]


def test_an_after_naming_a_host_the_hop_does_not_move_orders_nothing_with_a_note():
    hop = hop_of(fleet_host("a", after=["b"]), fleet_host("b", "7.11.0"))

    assert hop["units"][0]["round"] == 1
    assert hop["notes"] == ["qumulo_upgrade_after on a names b, which the hop to 7.11.0 does not move, so it orders nothing in that hop."]


def test_commit_schedule_on_its_own():
    schedule = upgrade_fleet.commit_schedule(["a", "b"], {"a": None, "b": None}, {"b": ["a"]}, [])

    assert (schedule.steps, schedule.refusals) == ([{"round": 1, "wave": None, "hosts": ["a"]}, {"round": 2, "wave": None, "hosts": ["b"]}], [])


# The maintenance window (FR-017)

LA = "America/Los_Angeles"


def window(days, start, end, start_minute=0, end_minute=0):
    return {"start_hour": start, "start_minute": start_minute, "end_hour": end, "end_minute": end_minute, "on_days": [days] if isinstance(days, str) else days}


@pytest.mark.parametrize(
    ("now", "inside"),
    [
        ("2026-11-06T21:00:00Z", False),  # Friday, before the range starts
        ("2026-11-06T23:00:00Z", True),  # Friday night
        ("2026-11-07T01:59:00Z", True),  # past midnight, into Saturday, which the range does not name
        ("2026-11-07T02:00:00Z", False),  # its end
        ("2026-11-07T23:00:00Z", False),  # Saturday night: the range starts only on Friday
    ],
)
def test_a_range_across_midnight_runs_into_the_next_day(now, inside):
    assert upgrade_fleet.window_covers([window("FRI", 22, 2)], "UTC", now) is inside


def test_every_day():
    assert all(upgrade_fleet.window_covers([window("EVERY_DAY", 1, 5)], "UTC", f"2026-11-{day:02d}T03:00:00Z") for day in range(1, 8))
    assert not upgrade_fleet.window_covers([window("EVERY_DAY", 1, 5)], "UTC", "2026-11-04T06:00:00Z")


def test_a_timezone():
    # 06:30 UTC on Thursday is 22:30 on Wednesday in Los Angeles.
    assert upgrade_fleet.window_covers([window("WED", 22, 23)], LA, "2026-11-05T06:30:00Z")
    assert not upgrade_fleet.window_covers([window("WED", 22, 23)], "UTC", "2026-11-05T06:30:00Z")


def test_no_ranges_declared_is_inside_at_every_time():
    assert upgrade_fleet.window_covers([], LA, NOW)
    assert upgrade_fleet.window_covers(None, None, NOW)


def windowed(name, ranges, **extra):
    return fleet_host(name, maintenance_windows=ranges, window_timezone=LA, **extra)


def test_w11_ranges_with_no_time_in_common():
    result = fleet(windowed("a", [window("SAT", 22, 6)]), windowed("b", [window("SUN", 8, 12)]), fleet_host("c"))

    assert warned(result, "W11") == [
        (
            "The maintenance windows of a, b share no time, so a hop that moves them all can never begin its commits. Give them a common "
            "range, or upgrade them in separate runs with --limit."
        )
    ]
    assert upgrade_fleet.windows_overlap({"a": {"ranges": [window("SAT", 22, 6)], "timezone": LA}, "b": {"ranges": [window("SUN", 4, 8)], "timezone": LA}}, NOW)


def test_the_dr_host_declaring_no_window_is_inside_whenever_production_is():
    hosts = hub_and_spokes()
    for h in hosts[:4]:
        h.update(maintenance_windows=[window("WED", 8, 12)], window_timezone=LA)
    hop = hop_of(*hosts)

    assert upgrade_fleet.window_form(hop, [], NOW)["inside"] is True
    assert upgrade_fleet.window_form(hop, [], "2026-11-04T22:00:00Z")["inside"] is False


def test_no_host_declaring_a_window_reads_as_none_declared():
    answer = upgrade_fleet.window_form(hop_of(*hub_and_spokes()), [], NOW)

    assert (answer["declared"], answer["inside"], answer["held"]) == (False, True, [])


def test_a_host_the_role_has_held_does_not_decide_the_clock():
    hop = hop_of(windowed("a", [window("SAT", 22, 6)]), fleet_host("b"))

    assert upgrade_fleet.window_form(hop, ["a"], NOW)["inside"] is True


SATURDAY_NIGHT = [window("SAT", 22, 6)]


def test_outside_a_standalone_unit_is_held_by_f9():
    answer = upgrade_fleet.window_form(hop_of(windowed("a", SATURDAY_NIGHT)), [], NOW)

    assert answer == {
        "declared": True,
        "inside": False,
        "next_open": "2026-11-08T06:00:00Z",
        "outside_hosts": ["a"],
        "held": ["a"],
        "lines": [],
        "message": "a were not committed: the hop to 7.11.0 is outside their maintenance window, which next opens 2026-11-07 22:00 "
        "America/Los_Angeles. They stay prepared. Run the upgrade again inside the window.",
    }


def test_outside_a_component_not_split_is_held():
    hosts = [windowed(h["name"], SATURDAY_NIGHT, **{k: v for k, v in h.items() if k != "name"}) for h in hub_and_spokes()]
    answer = upgrade_fleet.window_form(hop_of(*hosts), [], NOW)

    assert (answer["held"], answer["lines"]) == (["edge01", "edge02", "edge03", "hub01", "zdr01"], [])


def test_outside_a_split_component_the_hop_does_not_close_is_held_with_its_line():
    hop = hop_of(
        windowed("hub01", SATURDAY_NIGHT, version="7.9.3", target="7.10.3", entries=QUARTERLIES[1:]),
        windowed("edge01", SATURDAY_NIGHT, version="7.10.0.1", target="7.10.3", entries=[onprem("7.10.3")], hub="hub01"),
    )
    answer = upgrade_fleet.window_form(hop, [], NOW)

    assert answer["held"] == ["hub01"]
    assert answer["lines"] == ["hub01's portal component is held by the maintenance window, and stays split: the hop to 7.10.0.3 would not close its split."]


def split_component(**hub):
    return [
        windowed("hub01", SATURDAY_NIGHT, **hub),
        windowed("edge01", SATURDAY_NIGHT, version="7.11.0", hub="hub01"),
        windowed("edge02", SATURDAY_NIGHT, hub="hub01"),
        windowed("zdr01", SATURDAY_NIGHT),
    ]


def test_outside_a_split_component_that_closes_but_follows_a_held_unit_is_held_naming_it():
    answer = upgrade_fleet.window_form(hop_of(*split_component(relationships=replicates("zdr01"))), [], NOW)

    assert answer["held"] == ["edge02", "hub01", "zdr01"]
    assert answer["lines"] == ["hub01's portal component is held, though the hop would close its split: it follows zdr01, which is held."]


def test_outside_a_split_component_that_closes_and_follows_none_commits_its_moving_members():
    answer = upgrade_fleet.window_form(hop_of(*split_component()), [], NOW)

    assert answer["held"] == ["zdr01"]
    assert answer["lines"] == [
        "edge02 commits outside the maintenance window, to close the split of hub01's portal component.",
        "hub01 commits outside the maintenance window, to close the split of hub01's portal component.",
    ]
    assert answer["message"].startswith("zdr01 were not committed: the hop to 7.11.0 is outside their maintenance window")


def test_inside_nothing_is_held():
    answer = upgrade_fleet.window_form(hop_of(*split_component()), [], "2026-11-08T07:00:00Z")

    assert (answer["inside"], answer["held"], answer["message"]) == (True, [], None)


# The mismatch window (FR-035), from the module results' phases


def committed(started, ended=None):
    return {"phases": {"prepare": {"started": "2026-11-04T16:00:00Z", "ended": "2026-11-04T16:20:00Z"}, "commit": {"started": started, "ended": ended}}}


def test_mismatch_windows_from_the_first_commits_start_to_the_last_members_commit_done():
    hop = hop_of(*hub_and_spokes())
    results = {
        "zdr01": committed("2026-11-04T17:00:00Z", "2026-11-04T17:05:00Z"),
        "edge01": committed("2026-11-04T17:10:00Z", "2026-11-04T17:14:00Z"),
        "edge02": committed("2026-11-04T17:10:02Z", "2026-11-04T17:16:00Z"),
        "edge03": committed("2026-11-04T17:10:01Z", "2026-11-04T17:15:00Z"),
        "hub01": committed("2026-11-04T17:16:30Z", "2026-11-04T17:25:00Z"),
    }

    assert upgrade_fleet.mismatch_windows(hop, results) == {
        "hub01": {"started": "2026-11-04T17:10:00Z", "ended": "2026-11-04T17:25:00Z", "seconds": 900, "open": False, "uncommitted": []}
    }


def test_a_component_left_on_two_releases_has_an_open_window():
    hop = hop_of(*hub_and_spokes())
    results = {
        "edge01": committed("2026-11-04T17:10:00Z", "2026-11-04T17:14:00Z"),
        "edge02": committed("2026-11-04T17:10:00Z"),  # its commit failed
        "edge03": committed("2026-11-04T17:10:00Z", "2026-11-04T17:15:00Z"),
        "hub01": {"phases": {}},
    }

    assert upgrade_fleet.mismatch_windows(hop, results) == {
        "hub01": {"started": "2026-11-04T17:10:00Z", "ended": None, "seconds": None, "open": True, "uncommitted": ["edge02", "hub01"]}
    }


def test_a_component_none_of_whose_members_committed_has_no_window():
    assert upgrade_fleet.mismatch_windows(hop_of(*hub_and_spokes()), {}) == {}


# The hop's lease (FR-031)

TIMEOUTS = {"wait": 100, "reboot": 1000, "unblock": 10, "peer": 7, "request": 50}


def test_hop_lease_over_its_parts_with_forks_below_the_hops_size():
    hop = hop_of(*hub_and_spokes())  # 5 moves; steps of 1, 3 and 1 hosts, the hub's a later wave
    prepare = 50 + 100 + 50 + 100 + 1000

    expected = (
        (1000 + 10) * 3  # step 1, in 3 batches of forks 2
        + 2 * prepare * 3  # steps 2 and 3's prepares
        + 100 * 3  # step 3's readiness
        + (100 + 100) * 1  # zdr01's standalone step: readiness and commit
        + (100 + 100) * 2  # the spokes' first wave, in 2 batches
        + (100 + 50 + 100 + 100) * 1  # the hub's later wave, whose readiness task may prepare again
        + 2 * 7  # one peer wait for each of the two peer steps, whose hubs wait at once
        + 1000  # the reboots
    )
    assert upgrade_fleet.hop_lease(hop, TIMEOUTS, forks=2) == expected


def test_hop_lease_counts_the_prepares_batches_from_the_throttle():
    hop = hop_of(fleet_host("a"), fleet_host("b"), fleet_host("c"), fleet_host("d"))
    prepare = 50 + 100 + 50 + 100 + 1000

    assert upgrade_fleet.hop_lease(hop, TIMEOUTS, forks=10, throttle=2) == (1000 + 10) + 2 * prepare * 2 + 100 + (100 + 100) + 2 * 7 + 1000


def test_one_peer_wait_per_peer_step_however_many_hubs_and_sources():
    one = hop_of(fleet_host("hubA"), fleet_host("a1", hub="hubA"))
    many = hop_of(
        fleet_host("hubA", relationships=replicates("s")), fleet_host("a1", hub="hubA"), fleet_host("hubB"), fleet_host("b1", hub="hubB"), fleet_host("s")
    )
    without_peers = {**TIMEOUTS, "peer": 0}

    assert upgrade_fleet.hop_lease(one, TIMEOUTS) - upgrade_fleet.hop_lease(one, without_peers) == 14
    assert upgrade_fleet.hop_lease(many, TIMEOUTS) - upgrade_fleet.hop_lease(many, without_peers) == 14


def test_the_plans_lease_uses_the_longer_request_timeout_below_7_10_3():
    newer = hop_of(fleet_host("a"))
    older = hop_of(fleet_host("a", "7.10.0.3", "7.10.3", [onprem("7.10.3")]))

    assert newer["lease"] == upgrade_fleet.hop_lease(newer, {**upgrade_fleet.DEFAULT_TIMEOUTS, "request": 2700})
    assert older["lease"] == upgrade_fleet.hop_lease(older, {**upgrade_fleet.DEFAULT_TIMEOUTS, "request": 4500})


# W6, for the role (FR-018)


def test_forks_warning_below_the_hops_clusters_or_its_largest_step():
    hop = hop_of(*hub_and_spokes())

    assert upgrade_fleet.forks_warning(hop, 5) is None
    assert upgrade_fleet.forks_warning(hop, 2) == (
        "forks is 2, below this hop's 5 clusters or its largest commit step of 3. Prepares and a step's commits run in batches, which "
        "lengthens the hop and its components' mismatch windows. Raise forks to at least 5."
    )
    assert upgrade_fleet.forks_warning(hop, 5, throttle=2).startswith("cluster_upgrade_prepare_throttle is 2, below this hop's 5 clusters")


# Corners of the fleet's inputs


def test_f13_names_a_hub_that_is_no_inventory_host():
    result = fleet(fleet_host("edge01", hub="hub99"))

    assert result["refusals"][0]["message"].startswith("hub99's portal component has members outside this run: hub99 (no inventory host).")


def test_an_images_lists_own_refusal_and_a_target_that_is_no_version_are_listed_with_the_rest():
    result = fleet(fleet_host("a", entries=["https://mirror.example.com/core.qimg"]), fleet_host("b", target="seven"))

    assert refused(result) == [("a", "images"), ("b", "target")]
    assert result["refusals"][1]["message"].startswith("b: 'seven' is not a Qumulo Core version")


def test_a_refusal_found_in_every_hop_is_given_once():
    # b moves in both hops, 7.10.0.3 and 7.11.0, and its after is refused in each.
    entries = [onprem("7.10.0.3"), onprem("7.10.3"), onprem("7.11.0")]
    result = fleet(fleet_host("a", "7.10.0.3", entries=entries), fleet_host("b", "7.9.0.5", entries=entries, after=["nosuch"]))

    assert refused(result) == [("b", "F18")]


def test_w11_is_given_once_however_many_hops_it_holds_in():
    entries = [onprem("7.10.0.3"), onprem("7.10.3"), onprem("7.11.0")]
    result = fleet(
        fleet_host("a", "7.9.0.5", entries=entries, maintenance_windows=[window("SAT", 8, 12)], window_timezone=LA),
        fleet_host("b", "7.9.0.5", entries=entries, maintenance_windows=[window("SUN", 8, 12)], window_timezone=LA),
    )

    assert [h["moves"] for h in result["hops"]] == [["a", "b"], ["a", "b"]]
    assert len(warned(result, "W11")) == 1


def test_two_replication_edges_between_one_pair_of_units_order_them_once():
    hop = hop_of(
        fleet_host("hub01", relationships=replicates("zdr01")), fleet_host("edge01", hub="hub01", relationships=replicates("zdr01")), fleet_host("zdr01")
    )

    assert ([(u["name"], u["round"]) for u in hop["units"]], hop["notes"]) == ([("zdr01", 1), ("hub01", 2)], [])


def test_f18_an_after_naming_its_own_host():
    assert f18(fleet_host("a", after=["a"])) == ["qumulo_upgrade_after on a closes a cycle, a. Change qumulo_upgrade_after on a."]


def test_a_diamond_of_after_edges_rounds_by_the_longest_chain():
    hop = hop_of(fleet_host("a"), fleet_host("b", after=["a"]), fleet_host("c", after=["a"]), fleet_host("d", after=["b", "c", "a"]))

    assert [(u["name"], u["round"], u["follows"]) for u in hop["units"]] == [("a", 1, []), ("b", 2, ["a"]), ("c", 2, ["a"]), ("d", 3, ["a", "b", "c"])]


def test_overlapping_ranges_of_one_host_merge():
    ranges = [window("WED", 8, 12), window("WED", 11, 14)]

    assert upgrade_fleet.window_covers(ranges, LA, "2026-11-04T21:30:00Z")  # 13:30 in Los Angeles
    assert upgrade_fleet.windows_overlap({"a": {"ranges": ranges, "timezone": LA}, "b": {"ranges": [], "timezone": None}}, NOW)


def test_a_unit_the_role_has_held_is_left_to_the_role():
    hop = hop_of(windowed("a", SATURDAY_NIGHT), windowed("b", SATURDAY_NIGHT))
    answer = upgrade_fleet.window_form(hop, ["b"], NOW)

    assert (answer["held"], answer["outside_hosts"]) == (["a"], ["a"])


def test_ranges_with_no_time_in_common_next_open_at_the_first_outside_hosts_window():
    hop = hop_of(windowed("a", [window("SAT", 22, 6)]), windowed("b", [window("FRI", 8, 12)]))

    assert upgrade_fleet.window_form(hop, [], NOW)["next_open"] == "2026-11-06T16:00:00Z"


def test_reboot_all_at_tolerance_0_needs_no_line():
    result = fleet(fleet_host("a", configured=0, reboot="all"))

    assert (result["notes"], result["warnings"], result["hops"][0]["hosts"]["a"]["reboot"]) == ([], [], "all")


# zoneinfo raises ZoneInfoNotFoundError for the first three and ValueError for the path, which leaves the zone directory.
@pytest.mark.parametrize("zone", ["America/Nowhere", "America/Las_Angeles", "UTC ", "../../etc/passwd"])
def test_f34_a_timezone_that_is_no_iana_name_is_refused_by_name(zone):
    result = fleet(fleet_host("a", maintenance_windows=[window("SAT", 22, 6)], window_timezone=zone))

    assert [(r["code"], r["message"]) for r in result["refusals"]] == [
        (
            "F34",
            (
                f"a declares qumulo_upgrade_window_timezone {zone}, which is no IANA timezone name. Set "
                "qumulo_upgrade_window_timezone for a to an IANA name, such as America/Los_Angeles."
            ),
        )
    ]
    assert result["hops"] == []


@pytest.mark.parametrize(
    ("windows", "message"),
    [
        (
            [window("SAT", 25, 6)],
            (
                "a declares qumulo_upgrade_maintenance_windows with start_hour 25, which is no whole number from 0 to 23. Set each "
                "range's start_hour for a to a whole number from 0 to 23."
            ),
        ),
        (
            [window("SAT", 22, 6, end_minute=60)],
            (
                "a declares qumulo_upgrade_maintenance_windows with end_minute 60, which is no whole number from 0 to 59. Set each "
                "range's end_minute for a to a whole number from 0 to 59."
            ),
        ),
        (
            [window("SATURDAY", 22, 6)],
            (
                "a declares qumulo_upgrade_maintenance_windows with on_days SATURDAY, which is no day name. Name each range's days "
                "for a from SUN, MON, TUE, WED, THU, FRI, SAT and EVERY_DAY."
            ),
        ),
    ],
)
def test_f34_a_range_outside_the_clock_or_the_week_is_refused_by_its_field(windows, message):
    result = fleet(fleet_host("a", maintenance_windows=windows, window_timezone="America/Los_Angeles"))

    assert [(r["code"], r["message"]) for r in result["refusals"]] == [("F34", message)]
    assert result["hops"] == []


def test_f34_names_each_bad_field_once_however_many_ranges_carry_it():
    windows = [window("SAT", -1, 6, start_minute=75), window("SUN", -1, 6)]

    result = fleet(fleet_host("a", maintenance_windows=windows, window_timezone="America/Los_Angeles"))

    assert [(r["code"], r["message"].split(",")[0]) for r in result["refusals"]] == [
        ("F34", "a declares qumulo_upgrade_maintenance_windows with start_hour -1"),
        ("F34", "a declares qumulo_upgrade_maintenance_windows with start_minute 75"),
    ]


def test_a_day_name_in_lower_case_is_a_day_name():
    result = fleet(fleet_host("a", maintenance_windows=[window(["sat", "every_day"], 22, 6)], window_timezone="America/Los_Angeles"))

    assert result["refusals"] == []
