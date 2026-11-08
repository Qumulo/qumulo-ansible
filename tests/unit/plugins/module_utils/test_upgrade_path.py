# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""upgrade_path.py's release arithmetic, images list and hops (specs/017-cluster-upgrade, research R5)."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import upgrade_path
from ansible_collections.qumulo.cluster.plugins.module_utils.version import ClusterVersion

MIRROR = "https://mirror.example.com/qumulo"


def v(text, build_id=""):
    return ClusterVersion.parse(text, build_id)


def onprem(release):
    return f"{MIRROR}/qumulo_core_{release}.qimg"


def cloud(release):
    return f"{MIRROR}/cloud_qumulo_core_{release}.qimg"


def images(*entries):
    return upgrade_path.parse_images(list(entries))


def hops(path):
    return [str(hop.release) for hop in path.hops]


def path_of(current, entries, target, build="", kind="edge", protection=None, rebooting=False, hub=None):
    return upgrade_path.host_hops(
        "edge01", v(current, build), images(*entries), v(target), cluster_kind=kind, protection_type=protection, rebooting=rebooting, hub=hub
    )


def refusal(current, entries, target, **kw):
    with pytest.raises(upgrade_path.Refusal) as raised:
        path_of(current, entries, target, **kw)
    return raised.value


# next_quarter


@pytest.mark.parametrize(
    ("quarter", "named", "after"),
    [
        ((7, 9), [], (7, 10)),
        ((7, 9), ["7.11.0"], (7, 10)),  # by arithmetic: a list that names 7.11 still needs 7.10
        ((7, 10), ["8.0.0"], (8, 0)),  # no later 7.y is named, so 8.0 takes 7.10 as its previous quarter
        ((7, 11), ["7.11.2", "8.0.0.1"], (8, 0)),  # 7.11 is the highest 7.y named, so 8.0 follows it
        ((7, 10), ["7.11.0", "8.0.0"], (7, 11)),
    ],
)
def test_next_quarter_is_arithmetic_within_a_major_and_named_across_one(quarter, named, after):
    assert upgrade_path.next_quarter(quarter, [v(n) for n in named]) == after


# accepts, research R5's rule


@pytest.mark.parametrize(
    ("source", "target", "named", "accepted"),
    [
        ("7.10.0.1", "7.10.0.3", [], True),  # the same main release, a later dot release
        ("7.10.3", "7.10.3A", [], True),  # the same main release, its letter release
        ("7.10.0.1", "7.10.3", [], True),  # the same quarter, a later release
        ("7.9.3.1", "7.10.0.3", [], True),  # a quarterly from any release of the previous quarter
        ("7.9.0.5", "7.10.0", [], True),
        ("7.8.4", "7.10.0.3", [], False),  # a skip
        ("7.9.3", "7.10.3", [], False),  # a later patch accepts only its own quarter, so it is never a quarter's first hop
        ("7.10.3", "7.10.0.3", [], False),  # a downgrade
        ("7.10.3", "7.10.3", [], False),  # nothing to do is not a hop
        ("7.11.2", "8.0.0.1", ["7.11.2"], True),  # a new major from the last quarterly named of the old one
        ("7.10.3", "8.0.0", ["7.11.0"], False),  # 7.11 lies between
        ("7.10.3A", "7.11.0", [], True),  # a letter source takes the rule from its release
    ],
)
def test_accepts_follows_the_quarterly_rule(source, target, named, accepted):
    assert upgrade_path.accepts(v(source), v(target), [v(n) for n in named]) is accepted


# parse_images


def test_both_file_names_give_the_release_and_the_kind():
    parsed = images(onprem("7.10.3"), cloud("7.10.3"))

    assert [(str(i.release), i.kind, i.image, i.build, i.index) for i in parsed] == [
        ("7.10.3", "on-prem", onprem("7.10.3"), None, 0),
        ("7.10.3", "cloud", cloud("7.10.3"), None, 1),
    ]


def test_a_query_string_does_not_hide_the_file_name():
    url = onprem("7.10.0.3") + "?X-Amz-Signature=abc&X-Amz-Expires=14400"

    (parsed,) = images(url)

    assert (str(parsed.release), parsed.kind, parsed.image) == ("7.10.0.3", "on-prem", url)


def test_a_mapping_gives_the_version_kind_and_build():
    (parsed,) = images({"version": "7.10.0.3", "kind": "on-prem", "image": "/upgrades/core-quarterly.qimg", "build": "366930.1.377"})

    assert (str(parsed.release), parsed.kind, parsed.image, parsed.build) == ("7.10.0.3", "on-prem", "/upgrades/core-quarterly.qimg", "366930.1.377")


def test_a_mapping_without_kind_is_refused():
    with pytest.raises(upgrade_path.Refusal, match=r"/upgrades/core.qimg.* needs kind, on-prem or cloud"):
        images({"version": "7.10.0.3", "image": "/upgrades/core.qimg"})


@pytest.mark.parametrize("number", [7.11, 7])
def test_a_version_given_as_a_number_is_refused_naming_the_quotes(number):
    with pytest.raises(upgrade_path.Refusal, match=rf'version {number} as a number.* quote it, as "{number}"'):
        images({"version": number, "kind": "on-prem", "image": "/upgrades/core.qimg"})


def test_a_name_with_no_release_is_refused_naming_the_mapping_form():
    with pytest.raises(upgrade_path.Refusal, match=r"qinstall.qimg names no release.*\{version, image, kind, build\}"):
        images(f"{MIRROR}/qinstall.qimg")


def test_two_entries_of_one_release_and_kind_are_f4():
    found = refusal("7.9.0.5", [onprem("7.10.3"), onprem("7.10.3")], "7.10.3")

    assert found.code == "F4"
    assert str(found) == ("edge01 on 7.9.0.5 cannot reach 7.10.3 with this images list: two entries are 7.10.3 on-premises images. Keep one.")


def test_entries_out_of_order_are_f4_naming_the_order():
    found = refusal("7.8.4", [onprem("7.10.0.3"), onprem("7.9.0.5"), onprem("7.10.3")], "7.10.3")

    assert found.code == "F4"
    assert str(found) == ("edge01 on 7.8.4 cannot reach 7.10.3 with this images list: the entries are out of order. List them as 7.9.0.5, 7.10.0.3, 7.10.3.")


def test_a_releases_two_kinds_side_by_side_are_accepted():
    path = path_of("7.9.0.5", [onprem("7.10.0.3"), cloud("7.10.0.3"), onprem("7.10.3"), cloud("7.10.3")], "7.10.3", kind="cnq")

    assert hops(path) == ["7.10.0.3", "7.10.3"]
    assert [hop.image for hop in path.hops] == [cloud("7.10.0.3"), cloud("7.10.3")]
    assert path.kind == "cloud"


def test_an_entry_of_the_other_kind_only_is_f10():
    found = refusal("7.9.0.5", [onprem("7.10.0.3")], "7.10.0.3", kind="cnq")

    assert found.code == "F10"
    assert str(found) == (
        f"edge01 is a cloud cluster, but its image for 7.10.0.3, {onprem('7.10.0.3')}, is an on-premises image. "
        "Give a cloud image, or correct qumulo_cluster_kind for edge01."
    )


def test_f10_prints_the_image_redacted():
    found = refusal("7.9.0.5", [onprem("7.10.0.3") + "?sig=secret"], "7.10.0.3", kind="cnq")

    assert "sig=secret" not in str(found)
    assert onprem("7.10.0.3") in str(found)


@pytest.mark.parametrize(("protection", "kind"), [("PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "cloud"), ("PROTECTION_SYSTEM_TYPE_EC", "on-prem")])
def test_a_host_without_a_kind_takes_its_protection_types_images(protection, kind):
    path = path_of("7.9.0.5", [onprem("7.10.0.3"), cloud("7.10.0.3")], "7.10.0.3", kind=None, protection=protection)

    assert path.kind == kind
    assert [hop.kind for hop in path.hops] == [kind]


def test_f15_only_for_a_host_whose_protection_type_could_not_be_read():
    found = refusal("7.9.0.5", [onprem("7.10.0.3"), cloud("7.10.0.3")], "7.10.0.3", kind=None, protection=None)

    assert found.code == "F15"
    assert str(found) == (
        "The images for 7.10.0.3 include both kinds, and edge01 has no qumulo_cluster_kind. Set qumulo_cluster_kind for edge01 to cnq, edge or baremetal."
    )


@pytest.mark.parametrize("kind", ["cnq", "edge", "baremetal"])
def test_each_known_cluster_kind_is_accepted(kind):
    assert path_of("7.9.0.5", [onprem("7.10.0.3"), cloud("7.10.0.3")], "7.10.0.3", kind=kind).kind == upgrade_path.CLUSTER_KINDS[kind]


def test_an_unknown_cluster_kind_is_refused_naming_it():
    found = refusal("7.9.0.5", [onprem("7.10.0.3")], "7.10.0.3", kind="azure")

    assert found.code == "images"
    assert (
        str(found)
        == "edge01 declares qumulo_cluster_kind azure, which is none of cnq, edge or baremetal. Set qumulo_cluster_kind for edge01 to cnq, edge or baremetal."
    )


def test_one_kind_only_and_no_kind_known_is_used_as_it_is():
    path = path_of("7.9.0.5", [cloud("7.10.0.3")], "7.10.0.3", kind=None, protection=None)

    assert hops(path) == ["7.10.0.3"]


# host_hops, the furthest accepted entry (Decision 42)


QUARTERS = [onprem("7.9.0.5"), onprem("7.10.0.3"), onprem("7.10.3")]


def test_a_host_crosses_each_quarter_to_its_quarterly():
    assert hops(path_of("7.8.4", QUARTERS, "7.10.3")) == ["7.9.0.5", "7.10.0.3", "7.10.3"]


def test_a_later_patch_of_an_intermediate_quarter_is_passed_over():
    entries = [onprem("7.9.0.5"), onprem("7.9.3.1"), onprem("7.10.0.3"), onprem("7.10.3")]

    assert hops(path_of("7.8.4", entries, "7.10.3")) == ["7.9.0.5", "7.10.0.3", "7.10.3"]


def test_a_host_part_way_through_a_quarter_takes_one_hop():
    assert hops(path_of("7.10.0.1", [onprem("7.10.0.3"), onprem("7.10.3")], "7.10.3")) == ["7.10.3"]


def test_a_host_stops_at_its_own_target_below_the_lists_last_entry():
    assert hops(path_of("7.8.4", QUARTERS, "7.10.0.3")) == ["7.9.0.5", "7.10.0.3"]


def test_a_list_without_a_quarter_is_refused_although_nothing_names_it():
    found = refusal("7.8.4", [onprem("7.10.0.3"), onprem("7.10.3")], "7.10.3")

    assert found.code == "F4"
    assert str(found) == (
        "edge01 on 7.8.4 cannot reach 7.10.3 with this images list: the quarter 7.9 has no image the rule accepts. "
        "Add an image of 7.9.0 or one of its dot releases."
    )


def test_a_later_patch_as_a_quarters_only_entry_is_refused():
    found = refusal("7.8.4", [onprem("7.9.3.1"), onprem("7.10.0.3"), onprem("7.10.3")], "7.10.3")

    assert str(found) == (
        "edge01 on 7.8.4 cannot reach 7.10.3 with this images list: the only entry for 7.9 is a later patch. Add 7.9.0 or one of its dot releases."
    )


def test_a_letter_release_as_the_only_intermediate_entry_is_refused():
    found = refusal("7.8.4", [onprem("7.9.0.5A"), onprem("7.10.0.3"), onprem("7.10.3")], "7.10.3")

    assert str(found) == (
        "edge01 on 7.8.4 cannot reach 7.10.3 with this images list: the only acceptable entry is a letter release before "
        "the last hop. Use the release without the letter."
    )


def test_a_letter_target_is_a_hop():
    assert hops(path_of("7.10.0.3", [onprem("7.10.3A")], "7.10.3A")) == ["7.10.3A"]


def test_no_entry_of_the_target_is_refused():
    found = refusal("7.8.4", [onprem("7.9.0.5"), onprem("7.10.0.3")], "7.10.3")

    assert str(found) == "edge01 on 7.8.4 cannot reach 7.10.3 with this images list: no entry is the target. Add an image of 7.10.3."


@pytest.mark.parametrize("build", [None, "385145.1.82"])
def test_a_host_at_its_target_has_no_hops(build):
    entry = {"version": "7.10.3", "kind": "on-prem", "image": onprem("7.10.3"), **({"build": build} if build else {})}

    path = path_of("7.10.3", [entry], "7.10.3", build="385145.1.82")

    assert (list(path.hops), path.reboots_only) == ([], False)


def test_a_host_on_its_target_release_on_another_build_than_its_entry_declares_is_f4():
    entry = {"version": "7.10.3", "kind": "on-prem", "image": onprem("7.10.3"), "build": "385145.1.99"}

    found = refusal("7.10.3", [entry], "7.10.3", build="385145.1.82")

    assert found.code == "F4"
    assert str(found) == (
        "edge01 on 7.10.3 cannot reach 7.10.3 with this images list: edge01 already runs 7.10.3 on build 385145.1.82, not "
        "the build 385145.1.99 its mapping entry declares. Correct the entry's build with Qumulo Care; the collection does "
        "not move a cluster between builds of one release."
    )


def test_a_build_declared_above_the_hosts_target_is_never_met():
    entries = [onprem("7.10.0.3"), {"version": "7.10.3", "kind": "on-prem", "image": onprem("7.10.3"), "build": "385145.1.99"}]

    path = path_of("7.9.0.5", entries, "7.10.0.3")

    assert hops(path) == ["7.10.0.3"]
    assert path.hops[0].build is None


def test_the_last_hop_carries_its_entrys_build():
    entries = [{"version": "7.10.0.3", "kind": "on-prem", "image": onprem("7.10.0.3"), "build": "366930.1.377"}]

    assert path_of("7.9.0.5A", entries, "7.10.0.3").hops[0].build == "366930.1.377"


def test_a_host_at_its_target_build_still_rebooting_is_given_the_last_hop_for_its_reboots_alone():
    path = path_of("7.10.3", QUARTERS, "7.10.3", rebooting=True)

    assert (list(path.hops), path.reboots_only) == ([], True)
    assert path.last.image == onprem("7.10.3")


def test_a_host_above_its_target_is_f3():
    found = refusal("7.11.0", QUARTERS, "7.10.3")

    assert found.code == "F3"
    assert str(found) == (
        "edge01 runs Qumulo Core 7.11.0, above the target 7.10.3. The collection never downgrades: set "
        "qumulo_upgrade_target to 7.11.0 or later, or leave edge01 out of the run with --limit."
    )


def test_f3_for_a_portal_member_names_its_component():
    found = refusal("7.11.0", QUARTERS, "7.10.3", hub="hub01")

    assert str(found) == (
        "edge01 runs Qumulo Core 7.11.0, above the target 7.10.3. The collection never downgrades: set "
        "qumulo_upgrade_target for edge01 to 7.11.0 or later, or leave hub01's whole portal component out of the run "
        "with --limit."
    )


GA = ["7.3.0", "7.4.0", "7.5.0.3", "7.6.0.2", "7.7.0.3", "7.8.0.4", "7.9.0.5", "7.10.0.3", "7.11.0"]


def test_every_quarterly_from_7_2_to_7_11_is_one_hop_each():
    """SC-006: the arithmetic over research R5's quarterlies; FR-026's runs decide what is supported."""
    assert hops(path_of("7.2.3.2", [onprem(r) for r in GA], "7.11.0")) == GA


# align_split (Decision 42), at every hop


def member(host, current, entries, target, kind="edge"):
    return upgrade_path.host_hops(host, v(current), images(*entries), v(target), cluster_kind=kind, protection_type=None)


def aligned(paths, components):
    return {host: hops(path) for host, path in upgrade_path.align_split({p.host: p for p in paths}, components).items()}


def test_spokes_above_their_hub_take_its_first_release_so_the_split_closes_in_hop_1():
    entries = [onprem("7.10.0.3"), onprem("7.10.3")]
    paths = [member("hub01", "7.9.3", entries, "7.10.3"), member("edge01", "7.10.0.1", entries, "7.10.3"), member("edge02", "7.10.0.1", entries, "7.10.3")]

    assert aligned(paths, [["hub01", "edge01", "edge02"]]) == {
        "hub01": ["7.10.0.3", "7.10.3"],
        "edge01": ["7.10.0.3", "7.10.3"],
        "edge02": ["7.10.0.3", "7.10.3"],
    }


def test_a_hub_two_quarters_behind_moves_alone_and_its_spokes_align_in_hop_2():
    paths = [member("hub01", "7.8.4", QUARTERS, "7.10.3"), member("edge01", "7.10.0.1", QUARTERS, "7.10.3")]

    assert aligned(paths, [["hub01", "edge01"]]) == {"hub01": ["7.9.0.5", "7.10.0.3", "7.10.3"], "edge01": ["7.10.0.3", "7.10.3"]}


def test_a_cloud_spoke_without_an_entry_of_the_earliest_release_is_not_aligned():
    entries = [onprem("7.10.0.3"), onprem("7.10.3"), cloud("7.10.3")]
    paths = [member("hub01", "7.9.3", entries, "7.10.3"), member("cnq01", "7.10.0.1", entries, "7.10.3", kind="cnq")]

    assert aligned(paths, [["hub01", "cnq01"]]) == {"hub01": ["7.10.0.3", "7.10.3"], "cnq01": ["7.10.3"]}


def test_a_member_whose_earliest_release_would_be_a_downgrade_is_not_aligned():
    entries = [onprem("7.9.0.5"), onprem("7.10.0.3"), onprem("7.10.3")]
    # The hub's first hop is 7.9.0.5, which the spoke on 7.10.0.1 has passed: aligning would be a downgrade.
    paths = [member("hub01", "7.8.4", entries, "7.10.3"), member("edge01", "7.10.0.1", entries, "7.10.3")]

    assert aligned(paths, [["hub01", "edge01"]])["edge01"] == ["7.10.0.3", "7.10.3"]


def test_a_letter_release_is_compared_with_its_letter():
    entries = [onprem("7.10.3"), onprem("7.10.3A")]
    # One member on 7.10.3 and one on 7.10.3A are split, though their main release is one.
    paths = [member("hub01", "7.10.3", entries, "7.10.3A"), member("edge01", "7.10.3A", entries, "7.10.3A")]

    assert upgrade_path.split_at_start({p.host: p.current for p in paths}, ["hub01", "edge01"]) is True
    assert aligned(paths, [["hub01", "edge01"]]) == {"hub01": ["7.10.3A"], "edge01": []}
