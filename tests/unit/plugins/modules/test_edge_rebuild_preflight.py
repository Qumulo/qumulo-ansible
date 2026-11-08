# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.edge_rebuild_preflight, driven through FakeQumulo. Response bodies follow what Qumulo Core 7.10.3 returns."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.filter.tenancy_first_pass import tenancy_first_pass
from ansible_collections.qumulo.cluster.plugins.module_utils import continuity as edge_rebuild_continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.replication import describe_source, describe_target
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import replay
from ansible_collections.qumulo.cluster.plugins.modules import edge_rebuild_preflight
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import SOURCE_STATUS, TARGET_STATUS
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_replication import snapshot as replication_snapshot
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import ENG as ENG_WITHOUT_TENANT_1
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import (
    HOSTED_WITHOUT_TENANT_1_CAPTURE,
    NAMED_DEFAULT,
    NAMED_DEFAULT_CAPTURE,
    NAMED_DEFAULT_DOCUMENT,
    NAMED_ENG,
    TWO_WITH_SPARE,
    TWO_WITH_SPARE_CAPTURE,
    WITH_SPARE_CAPTURE,
    WITHOUT_TENANT_1,
    WITHOUT_TENANT_1_CAPTURE,
)

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
FACTORY_USERS = [{"id": "500", "name": "admin"}, {"id": "501", "name": "guest"}]
FACTORY_GROUPS = [{"id": "513", "name": "Users"}, {"id": "514", "name": "Guests"}]
DHCP = {"managed_interfaces": None, "frontend_networks": [{"id": 1, "name": "Default", "addresses": {"type": "DHCP", "dhcp_addresses": {}}}]}
STATIC = {
    "id": 1,
    "name": "Default",
    "addresses": {
        "type": "STATIC",
        "static_addresses": {"default_gateway": "10.0.0.1", "ip_ranges": ["10.0.0.5"], "floating_ip_ranges": [], "netmask": "255.255.255.0"},
    },
}
LOCAL_KEY_STORE = {"type": "Local", "status": "Encrypted", "ca_cert_expiry": None, "client_cert_expiry": None}


DEFAULT_TENANT = {"id": 1, "name": "Default", "networks": [1], "identity_config_id": 1}  # network 1 as a newly formed edge holds it


def tenant(tenant_id, name, networks=()):
    return {"id": tenant_id, "name": name, "networks": list(networks), "identity_config_id": 1}


def share(name, tenant_id=1):
    return {"id": f"{tenant_id}-{name}", "share_name": name, "fs_path": "/", "tenant_id": tenant_id}


def export(path, tenant_id=1):
    return {"id": f"{tenant_id}-{path}", "export_path": path, "fs_path": "/", "tenant_id": tenant_id}


def held(make, item):
    """A share or export the edge holds: its name, in tenant 1, or a (name, tenant id) pair."""
    return make(*item) if isinstance(item, tuple) else make(item)


def quota(path):
    return {"id": "3", "path": path, "limit": "1000000000", "capacity_usage": "0"}


def policy(name):
    return {"id": 1, "policy_name": name, "source_file_path": "/data/", "enabled": True}


def spoke(*local_roots):
    roots = [{"local_root": file_id, "remote_root": "900", "remote_root_path": "/data/", "authorized": True} for file_id in local_roots]
    return {
        "id": 1,
        "type": "PORTAL_READ_WRITE",
        "state": "ACCEPTED",
        "status": "ACTIVE",
        "hub_hosts": [{"address": "10.0.0.10", "port": 3713}],
        "hub_id": 3,
        "hub_cluster_uuid": HUB_UUID,
        "roots": roots,
    }


def source_status(rid, *, source="/src", target="/dst", target_uuid=HUB_UUID, state="ESTABLISHED", **extra):
    """A source status record as GET /v2/replication/source-relationships/status/ lists it, from this edge to a peer."""
    return {
        **SOURCE_STATUS,
        "id": rid,
        "source_cluster_uuid": CLUSTER_ID.upper(),
        "target_cluster_uuid": target_uuid,
        "source_root_path": source + "/",
        "target_root_path": target + "/",
        "state": state,
        **extra,
    }


def target_status(rid, *, source="/src", target="/dst", source_uuid=HUB_UUID, state="ESTABLISHED", read_only=True, **extra):
    """A target status record as GET /v2/replication/target-relationships/status/ lists it, from a peer into this edge."""
    return {
        **TARGET_STATUS,
        "id": rid,
        "source_cluster_uuid": source_uuid,
        "target_cluster_uuid": CLUSTER_ID,
        "source_root_path": source + "/",
        "target_root_path": target + "/",
        "state": state,
        "target_root_read_only": read_only,
        "recovery_point": "2026-11-01T10:00:00.000000000Z",
        **extra,
    }


def snapshot(sid, *owners):
    """A snapshot as GET /v4/snapshots/status/ lists it, owned by the relationships whose ids owners gives."""
    return replication_snapshot(sid, f"{sid}_replication", owners=owners)


def child(name, *, files=0, links=0, directory=True):
    """One child of a directory as GET aggregates lists it; files and links count everything under it."""
    return {
        "name": name,
        "type": "FS_FILE_TYPE_DIRECTORY" if directory else "FS_FILE_TYPE_FILE",
        "num_files": str(files if directory else 1),
        "num_directories": "0",
        "num_symlinks": str(links),
        "num_other_objects": "0",
    }


def entry(path, *, directory=False):
    """One entry of a directory as GET entries lists it."""
    return {"path": path + ("/" if directory else ""), "name": path.rsplit("/", 1)[1], "type": "FS_FILE_TYPE_DIRECTORY" if directory else "FS_FILE_TYPE_FILE"}


def expect_listing(fake, path, listed):
    """Scripts the entries reads of a directory the aggregates count empty, and of each directory under it."""
    entries = listed.get(path, [])
    fake.expect("GET", f"/v1/files/{path.replace('/', '%2F')}/entries/?limit=1000", response={"files": entries, "paging": {"next": ""}})
    for item in entries:
        if item["type"] == "FS_FILE_TYPE_DIRECTORY":
            expect_listing(fake, item["path"].rstrip("/"), listed)


def expect_edge(
    fake,
    *,
    tenants=(DEFAULT_TENANT,),
    shares=("Files",),
    exports=("/",),
    quotas=(),
    policies=(),
    users=(),
    groups=(),
    spoke_roots=None,
    network=None,
    key_store=None,
    source=(),
    target=(),
    objects=(),
    s3_keys=(),
    tokens=(),
    local=None,
    listed=None,
    snapshots=0,
):
    """Scripts every read, in the module's order.

    source and target hold status records, or ids for a default record of each; snapshots is a count, or the
    snapshots themselves. shares and exports hold names, each in tenant 1, or (name, tenant id) pairs.

    spoke_roots maps each spoke root's file id to its path; local maps each directory whose aggregates the module reads,
    "/" first, to its children; listed maps a directory the aggregates count empty, which the module then lists, to its
    entries.
    """
    fake.expect("GET", "/v3/network", response=network or DHCP, etag='"n1"')
    fake.expect("GET", "/v1/multitenancy/tenants/", response={"entries": list(tenants)})
    fake.expect("GET", "/v3/smb/shares/", response={"entries": [held(share, item) for item in shares]})
    fake.expect("GET", "/v3/nfs/exports/", response={"entries": [held(export, item) for item in exports]})
    fake.expect("GET", "/v1/files/quotas/status/?limit=1000", response={"quotas": [quota(path) for path in quotas], "paging": {"next": ""}})
    fake.expect("GET", "/v3/snapshots/policies/status/", response={"entries": [policy(name) for name in policies]})
    fake.expect("GET", "/v1/users/", response=FACTORY_USERS + [{"id": str(600 + i), "name": name} for i, name in enumerate(users)])
    fake.expect("GET", "/v1/groups/", response=FACTORY_GROUPS + [{"id": str(700 + i), "name": name} for i, name in enumerate(groups)])
    if spoke_roots is None:
        fake.expect("GET", "/v2/portal/spokes/", response={"entries": []})
    else:
        fake.expect("GET", "/v2/portal/spokes/", response={"entries": [spoke(*spoke_roots)]})
        for file_id, path in spoke_roots.items():
            fake.expect("GET", f"/v1/files/{file_id}/info/attributes", response={"id": file_id, "path": path, "type": "FS_FILE_TYPE_DIRECTORY"})
    fake.expect("GET", "/v2/encryption/key-store/status", response=key_store or LOCAL_KEY_STORE)
    fake.expect(
        "GET", "/v2/replication/source-relationships/status/", response=[record if isinstance(record, dict) else source_status(record) for record in source]
    )
    fake.expect(
        "GET", "/v2/replication/target-relationships/status/", response=[record if isinstance(record, dict) else target_status(record) for record in target]
    )
    fake.expect("GET", "/v3/replication/object-relationships/", response=[{"id": rid} for rid in objects])
    fake.expect("GET", "/v1/s3/access-keys/?limit=1000", response={"entries": [{"access_key_id": key} for key in s3_keys], "paging": {"next": None}})
    fake.expect("GET", "/v1/auth/access-tokens/?limit=1000", response={"entries": [{"id": token} for token in tokens], "paging": {"next": None}})
    roots = [path.rstrip("/") for path in (spoke_roots or {}).values()]
    for directory, children in (local or {"/": []}).items():
        ref = directory.replace("/", "%2F")
        fake.expect("GET", f"/v1/files/{ref}/aggregates/", response={"path": directory, "files": children})
        for item in children:
            path = directory.rstrip("/") + "/" + item["name"]
            counted = int(item["num_files"]) + int(item["num_symlinks"]) + int(item["num_other_objects"])
            if item["type"] == "FS_FILE_TYPE_DIRECTORY" and path not in roots and not counted and not any(root.startswith(path + "/") for root in roots):
                expect_listing(fake, path, listed or {})
    entries = [snapshot(str(i)) for i in range(snapshots)] if isinstance(snapshots, int) else list(snapshots)
    fake.expect("GET", "/v4/snapshots/status/?limit=1000", response={"entries": entries, "paging": {"next": None}})


def test_an_edge_that_holds_only_what_the_inventory_declares_passes(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "projects"), exports=("/", "/projects"), quotas=("/data/",), policies=("hourly",))

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "smb_shares": [{"share_name": "projects", "fs_path": "/projects"}],
            "nfs_exports": [{"export_path": "/projects", "fs_path": "/projects"}],
            "quotas": ["/data"],
            "snapshot_policies": ["hourly"],
        },
    )

    assert not result.get("failed"), result["msg"]
    assert result["changed"] is False
    assert (result["drift"], result["unreplayable"], result["acknowledged"]) == ([], [], [])


def test_the_factory_share_and_export_and_the_built_in_identities_are_never_drift(fake_api, run_module):
    expect_edge(fake_api())

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


@pytest.mark.parametrize(
    ("live", "drift", "listed"),
    [
        ({"shares": ("Files", "scratch")}, {"area": "smb_shares", "tenant": "Default", "name": "scratch"}, "smb_shares 'scratch' in tenant Default"),
        ({"exports": ("/", "/scratch")}, {"area": "nfs_exports", "tenant": "Default", "name": "/scratch"}, "nfs_exports '/scratch' in tenant Default"),
        ({"quotas": ("/scratch/",)}, {"area": "quotas", "name": "/scratch"}, "quotas '/scratch'"),
        ({"policies": ("nightly",)}, {"area": "snapshot_policies", "name": "nightly"}, "snapshot_policies 'nightly'"),
        ({"users": ("svc-backup",)}, {"area": "local_users", "name": "svc-backup"}, "local_users 'svc-backup'"),
        ({"groups": ("edge-admins",)}, {"area": "local_groups", "name": "edge-admins"}, "local_groups 'edge-admins'"),
        ({"spoke_roots": {"100": "/hub-data/"}}, {"area": "portal_roots", "name": "/hub-data"}, "portal_roots '/hub-data'"),
        ({"tenants": (DEFAULT_TENANT, tenant(2, "eng"))}, {"area": "tenants", "name": "eng"}, "tenants 'eng'"),
    ],
    ids=["share", "export", "quota", "policy", "user", "group", "portal-root", "tenant"],
)
def test_state_the_inventory_does_not_declare_is_drift_and_fails_naming_it(fake_api, run_module, live, drift, listed):
    expect_edge(fake_api(), **live)

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["failed"]
    assert result["drift"] == [{**drift, "declared_absent": False}]
    assert result["msg"] == (
        f"the edge holds state its inventory does not declare, which a rebuild would lose: {listed}. "
        "Declare it in the inventory, or set ignore_drift to rebuild without it"
    )


def test_drift_lists_every_undeclared_name_sorted_within_its_area(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "b-share", "a-share"), users=("svc-1",))

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["drift"] == [
        {"area": "smb_shares", "tenant": "Default", "name": "a-share", "declared_absent": False},
        {"area": "smb_shares", "tenant": "Default", "name": "b-share", "declared_absent": False},
        {"area": "local_users", "name": "svc-1", "declared_absent": False},
    ]
    assert "smb_shares 'a-share' in tenant Default, 'b-share' in tenant Default; local_users 'svc-1'." in result["msg"]


def test_share_names_compare_without_case_as_the_server_does(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "Projects"))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "smb_shares": [{"share_name": "projects"}]})

    assert not result.get("failed"), result["msg"]


def test_declared_paths_compare_without_a_trailing_slash(fake_api, run_module):
    expect_edge(fake_api(), quotas=("/data/",), spoke_roots={"100": "/hub-data/"})

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "quotas": ["/data/"], "portal_roots": ["/hub-data/"]})

    assert not result.get("failed"), result["msg"]


def test_export_paths_compare_without_a_trailing_slash(fake_api, run_module):
    expect_edge(fake_api(), exports=("/", "/projects/"))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "nfs_exports": [{"export_path": "/projects"}]})

    assert not result.get("failed"), result["msg"]


def test_what_the_inventory_declares_absent_and_the_edge_holds_is_drift(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "projects", "scratch"))

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "smb_shares": [{"share_name": "projects"}, {"share_name": "Scratch", "state": "absent"}]},
    )

    assert result["failed"]
    assert result["drift"] == [{"area": "smb_shares", "tenant": "Default", "name": "scratch", "declared_absent": True}]
    assert result["msg"] == (
        "the edge still holds state its inventory declares absent: smb_shares 'scratch' in tenant Default. "
        "Apply the inventory to the edge first, or set ignore_drift to rebuild without it"
    )


def test_what_the_inventory_declares_absent_and_the_edge_lacks_passes(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "projects"))

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "smb_shares": [{"share_name": "projects"}, {"share_name": "scratch", "state": "absent"}]},
    )

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_declared_absent_takes_only_the_drift_areas(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "declared_absent": {"portal_roots": ["/data"]}})

    assert result["failed"]
    assert "declared_absent.portal_roots" in result["msg"]


def test_undeclared_and_declared_absent_drift_are_reported_in_one_message(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "scratch"), users=("svc-old",))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "local_users": ["svc-old"], "declared_absent": {"local_users": ["svc-old"]}})

    assert result["msg"] == (
        "the edge holds state its inventory does not declare, which a rebuild would lose: smb_shares 'scratch' in tenant Default. "
        "Declare it in the inventory, or set ignore_drift to rebuild without it. "
        "the edge still holds state its inventory declares absent: local_users 'svc-old'. "
        "Apply the inventory to the edge first, or set ignore_drift to rebuild without it"
    )


def test_ignore_drift_reports_drift_without_failing(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "scratch"))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "ignore_drift": True})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == [{"area": "smb_shares", "tenant": "Default", "name": "scratch", "declared_absent": False}]


def test_ignore_drift_still_fails_on_unreplayable_state(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "scratch"), tokens=("t1",))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "ignore_drift": True})

    assert result["failed"]
    assert result["msg"] == (
        "the edge holds state the replay cannot create again: access_tokens (1 access token). "
        "List access_tokens in acknowledge to rebuild without it and recreate it by hand afterwards"
    )
    assert result["drift"] == [{"area": "smb_shares", "tenant": "Default", "name": "scratch", "declared_absent": False}]


@pytest.mark.parametrize(
    ("live", "area", "detail"),
    [
        ({"network": {"managed_interfaces": None, "frontend_networks": [STATIC]}}, "network", "frontend network 1 'Default' is STATIC"),
        (
            {"network": {"managed_interfaces": {"frontend_vlans": [{"vlan_id": 42, "mtu": 1500}]}, "frontend_networks": []}},
            "network",
            "VLAN 42",
        ),
        ({"key_store": {**LOCAL_KEY_STORE, "type": "KMS"}}, "kmip", "the key store is KMS, not Local"),
        ({"objects": ("o1",)}, "replication", "object relationship o1"),
        ({"s3_keys": ("AK1", "AK2")}, "s3_keys", "2 S3 access keys"),
        ({"tokens": ("t1",)}, "access_tokens", "1 access token"),
    ],
    ids=["static-network", "vlan", "kmip", "object-replication", "s3-keys", "access-tokens"],
)
def test_state_the_replay_cannot_create_fails_naming_its_area(fake_api, run_module, live, area, detail):
    expect_edge(fake_api(), **live)

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["failed"]
    assert result["unreplayable"] == [{"area": area, "detail": detail}]
    assert result["msg"] == (
        f"the edge holds state the replay cannot create again: {area} ({detail}). "
        f"List {area} in acknowledge to rebuild without it and recreate it by hand afterwards" + (", or declare qumulo_network" if area == "network" else "")
    )


LOCAL_DATA_SUFFIX = (
    "; the drain moves only the portal's spoke roots to the hub, so copy local data off the edge first, and know that"
    " acknowledging local_data gives up both its files and its snapshots"
)


@pytest.mark.parametrize(
    ("live", "detail"),
    [
        ({"local": {"/": [child("notes.txt", directory=False)]}}, "files: the file /notes.txt"),
        ({"local": {"/": [child("projects", files=3, links=1)]}}, "files: /projects holds 4 files"),
        (
            {
                "local": {"/": [child("fresh")]},
                "listed": {"/fresh": [entry("/fresh/a"), entry("/fresh/sub", directory=True)], "/fresh/sub": [entry("/fresh/sub/b")]},
            },
            "files: /fresh holds 2 files",
        ),
        ({"snapshots": 2}, "snapshots: 2 snapshots"),
        (
            {
                "spoke_roots": {"100": "/edge/hub-data"},
                "local": {"/": [child("edge", files=9)], "/edge": [child("hub-data", files=9), child("scratch", files=1)]},
            },
            "files: /edge/scratch holds 1 file",
        ),
    ],
    ids=["file", "directory", "not-yet-counted", "snapshots", "beside-a-nested-spoke-root"],
)
def test_data_outside_the_spoke_roots_fails_as_local_data(fake_api, run_module, live, detail):
    expect_edge(fake_api(), **live)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "portal_roots": ["/edge/hub-data"], "ignore_drift": True})

    assert result["failed"]
    assert result["unreplayable"] == [{"area": "local_data", "detail": detail}]
    assert result["msg"] == (
        f"the edge holds state the replay cannot create again: local_data ({detail}). "
        "List local_data in acknowledge to rebuild without it and recreate it by hand afterwards" + LOCAL_DATA_SUFFIX
    )


def test_empty_directories_and_the_spoke_roots_hold_no_local_data(fake_api, run_module):
    expect_edge(fake_api(), spoke_roots={"100": "/hub-data/"}, local={"/": [child("hub-data", files=40), child("empty")]})

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "portal_roots": ["/hub-data"]})

    assert not result.get("failed"), result["msg"]
    assert result["unreplayable"] == []


def test_acknowledged_local_data_passes_and_is_returned_for_the_report(fake_api, run_module):
    expect_edge(fake_api(), local={"/": [child("projects", files=1)]}, snapshots=1)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "acknowledge": ["local_data"]})

    assert not result.get("failed"), result["msg"]
    assert result["acknowledged"] == ["local_data"]
    assert result["unreplayable"] == [
        {"area": "local_data", "detail": "files: /projects holds 1 file"},
        {"area": "local_data", "detail": "snapshots: 1 snapshot"},
    ]


def test_dhcp_and_host_networks_are_replayable(fake_api, run_module):
    host = {"id": 2, "name": "host", "addresses": {"type": "HOST", "host_addresses": {}}}
    expect_edge(fake_api(), network={"managed_interfaces": None, "frontend_networks": [*DHCP["frontend_networks"], host]})

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert not result.get("failed"), result["msg"]
    assert result["unreplayable"] == []


def test_a_static_network_the_inventory_declares_is_replayable(fake_api, run_module):
    expect_edge(fake_api(), network={"managed_interfaces": None, "frontend_networks": [STATIC]})

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "network_declared": True})

    assert not result.get("failed"), result["msg"]
    assert result["unreplayable"] == []


def test_acknowledged_state_passes_and_is_returned_for_the_report(fake_api, run_module):
    expect_edge(fake_api(), tokens=("t1",), s3_keys=("AK1",))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "acknowledge": ["s3_keys", "access_tokens"]})

    assert not result.get("failed"), result["msg"]
    assert result["acknowledged"] == ["access_tokens", "s3_keys"]
    assert [entry["area"] for entry in result["unreplayable"]] == ["s3_keys", "access_tokens"]


def test_acknowledging_an_area_that_holds_nothing_returns_nothing(fake_api, run_module):
    expect_edge(fake_api())

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "acknowledge": ["replication"]})

    assert not result.get("failed"), result["msg"]
    assert result["acknowledged"] == []


def test_only_unacknowledged_areas_fail(fake_api, run_module):
    expect_edge(fake_api(), tokens=("t1",), source=("r1",))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "acknowledge": ["access_tokens"]})

    assert result["failed"]
    assert result["msg"].startswith("the edge holds state the replay cannot create again: replication (source relationship r1 ")
    assert "access_tokens" not in result["msg"]
    assert result["acknowledged"] == ["access_tokens"]


def test_drift_and_unreplayable_state_are_reported_in_one_message(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "scratch"), tokens=("t1",))

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["msg"] == (
        "the edge holds state its inventory does not declare, which a rebuild would lose: smb_shares 'scratch' in tenant Default. "
        "Declare it in the inventory, or set ignore_drift to rebuild without it. "
        "the edge holds state the replay cannot create again: access_tokens (1 access token). "
        "List access_tokens in acknowledge to rebuild without it and recreate it by hand afterwards"
    )


def test_check_mode_reads_the_same_and_fails_the_same(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "scratch"))

    result = run_module(edge_rebuild_preflight, CONNECTION, check_mode=True)

    assert result["failed"]
    assert result["drift"] == [{"area": "smb_shares", "tenant": "Default", "name": "scratch", "declared_absent": False}]


# Tenants, and the shares and exports of each, judged against what the replay builds (spec 014, User Story 5, scenario 6)

ENG = tenant(2, "eng")
# An edge whose one network, 2, no tenant holds: not network 1, which the formed edge keeps in tenant 1.
SPARE_ONLY = {"managed_interfaces": None, "frontend_networks": [{"id": 2, "name": "spare", "addresses": {"type": "DHCP", "dhcp_addresses": {}}}]}


def test_each_tenants_nested_and_shared_entries_pass(fake_api, run_module):
    expect_edge(
        fake_api(),
        tenants=(DEFAULT_TENANT, ENG),
        shares=("Files", "home", ("home", 2), ("builds", 2)),
        exports=("/", ("/eng/", 2)),
    )

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            # tenants: all is every tenant qumulo_tenants declares present, so Default is declared too.
            "tenants": [{"name": "Default"}, {"name": "eng", "smb_shares": [{"share_name": "Builds"}], "nfs_exports": [{"export_path": "/eng"}]}],
            "smb_shares": [{"share_name": "home", "tenants": "all"}],
        },
    )

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_a_share_declared_in_one_tenant_does_not_cover_its_name_in_another(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG), shares=("Files", "projects", ("projects", 2)), exports=("/", "/data", ("/data", 2)))

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "eng", "smb_shares": [{"share_name": "projects"}]}],
            "nfs_exports": [{"export_path": "/data", "tenants": ["Default"]}],
        },
    )

    assert result["failed"]
    assert result["drift"] == [
        {"area": "smb_shares", "tenant": "Default", "name": "projects", "declared_absent": False},
        {"area": "nfs_exports", "tenant": "eng", "name": "/data", "declared_absent": False},
    ]
    assert "smb_shares 'projects' in tenant Default; nfs_exports '/data' in tenant eng." in result["msg"]


def test_only_tenant_1s_files_share_and_root_export_are_the_factory_ones(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG), shares=("Files", ("Files", 2)), exports=("/", ("/", 2)))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}]})

    assert result["drift"] == [
        {"area": "smb_shares", "tenant": "eng", "name": "Files", "declared_absent": False},
        {"area": "nfs_exports", "tenant": "eng", "name": "/", "declared_absent": False},
    ]


def test_an_undeclared_tenant_is_drift_listed_before_its_shares(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG, tenant(3, "finance")), shares=("Files", ("ledger", 3)))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}]})

    assert result["drift"] == [
        {"area": "tenants", "name": "finance", "declared_absent": False},
        {"area": "smb_shares", "tenant": "finance", "name": "ledger", "declared_absent": False},
    ]


def test_tenant_1_under_another_name_is_drift_unless_an_entry_renames_default_to_it(fake_api, run_module):
    # A rebuilt edge forms tenant 1 as Default, and the replay renames it only for previous_name: Default; an entry of
    # the name alone is a second tenant.
    expect_edge(fake_api(), tenants=(tenant(1, "Corp", networks=[1]),))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "Corp"}]})

    assert result["drift"] == [{"area": "tenants", "name": "Corp", "declared_absent": False}]
    assert result["msg"].endswith(
        "Tenant 1 is Corp on the edge, and a newly formed edge names it Default; add previous_name: Default to "
        "qumulo_tenants' entry for Corp, so the replay renames it"
    )


def test_tenant_1_under_no_name_the_inventory_declares_is_drift_with_the_name_the_replay_gives_it(fake_api, run_module):
    expect_edge(fake_api(), tenants=(tenant(1, "Corp", networks=[1]),))

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["drift"] == [{"area": "tenants", "name": "Corp", "declared_absent": False}]
    assert result["msg"].endswith(
        "Tenant 1 is Corp on the edge, but the rebuilt edge's tenant 1 will be Default: a newly formed edge names it "
        "Default, and the replay renames it only for a qumulo_tenants entry with previous_name: Default"
    )


def test_tenant_1_is_drift_when_the_replay_renames_default_to_another_tenants_name(fake_api, run_module):
    # The edge holds Corp besides tenant 1, so the inventory's rename of Default to Corp is one already done there.
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, tenant(2, "Corp")))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "Corp", "previous_name": "Default"}]})

    assert result["drift"] == [{"area": "tenants", "name": "Default", "declared_absent": False}]
    assert result["msg"].endswith("Tenant 1 is Default on the edge, but the replay renames the newly formed edge's tenant 1, Default, to Corp")


def test_a_tenant_named_default_that_is_not_tenant_1_is_replayed_by_name(fake_api, run_module):
    # Tenant 1 was deleted and a tenant named Default made since; the replay keeps the new edge's tenant 1 as Default.
    expect_edge(fake_api(), tenants=(tenant(7, "Default", networks=[1]), ENG))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "Default"}, {"name": "eng"}]})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_tenant_1_renamed_from_default_by_its_entry_passes(fake_api, run_module):
    expect_edge(fake_api(), tenants=(tenant(1, "Corp", networks=[1]),), shares=("Files", "projects"))

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "tenants": [{"name": "Corp", "previous_name": "Default"}], "smb_shares": [{"share_name": "projects"}]},
    )

    assert not result.get("failed"), result["msg"]


def test_a_tenant_and_its_entries_declared_absent_and_still_held_are_drift(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG), shares=("Files", ("scratch", 2)), exports=("/", ("/scratch", 2)))

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "eng", "state": "absent", "smb_shares": [{"share_name": "scratch"}], "nfs_exports": [{"export_path": "/scratch"}]}],
        },
    )

    assert result["drift"] == [
        {"area": "tenants", "name": "eng", "declared_absent": True},
        {"area": "smb_shares", "tenant": "eng", "name": "scratch", "declared_absent": True},
        {"area": "nfs_exports", "tenant": "eng", "name": "/scratch", "declared_absent": True},
    ]
    assert "declares absent: tenants 'eng'; smb_shares 'scratch' in tenant eng; nfs_exports '/scratch' in tenant eng." in result["msg"]


def test_tenants_all_claims_the_name_for_removal_in_a_tenant_declared_absent(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG), shares=("Files", "home", ("home", 2)))

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "tenants": [{"name": "Default"}, {"name": "eng", "state": "absent"}], "smb_shares": [{"share_name": "home", "tenants": "all"}]},
    )

    assert result["drift"] == [
        {"area": "tenants", "name": "eng", "declared_absent": True},
        {"area": "smb_shares", "tenant": "eng", "name": "home", "declared_absent": True},
    ]


def test_a_flat_entry_without_tenants_is_the_only_tenants(fake_api, run_module):
    expect_edge(fake_api(), shares=("Files", "projects"), exports=("/", "/projects"))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "smb_shares": [{"share_name": "projects"}], "nfs_exports": [{"export_path": "/projects"}]})

    assert not result.get("failed"), result["msg"]


def test_a_flat_entry_without_tenants_is_refused_when_the_inventory_adds_a_second_tenant(fake_api, run_module):
    # The edge holds one tenant, but the replay creates eng too, and then refuses the entry after the destroy.
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}], "smb_shares": [{"share_name": "projects"}]})

    assert result["failed"]
    assert result["msg"] == (
        "the replay onto the rebuilt edge would refuse the inventory: qumulo_smb_shares[0] (projects) names no tenant, but "
        "this run has more than one tenant (Default and eng). Name them with tenants, such as tenants: all."
    )


def test_a_flat_entry_naming_a_tenant_only_the_old_edge_holds_is_refused(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "smb_shares": [{"share_name": "projects", "tenants": ["eng"]}]})

    assert result["failed"]
    assert result["msg"].startswith("the replay onto the rebuilt edge would refuse the inventory: qumulo_smb_shares[0] (projects) names tenant eng")


def test_tenant_id_1_is_tenant_1_and_another_id_is_skipped_as_the_replay_skips_it(fake_api, run_module):
    # The rebuilt edge numbers its tenants again, so the replay knows only tenant 1's id.
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG), shares=("Files", "projects", ("builds", 2)))

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default"}, {"name": "eng"}],
            "smb_shares": [{"share_name": "projects", "tenant_id": 1}, {"share_name": "builds", "tenant_id": 2}],
        },
    )

    assert result["drift"] == [{"area": "smb_shares", "tenant": "eng", "name": "builds", "declared_absent": False}]
    assert any("tenant_id 2 is no tenant on the cluster" in warning for warning in result["warnings"])


def test_qumulo_factory_defaults_claims_tenant_1s_files_share_as_the_baseline_does(fake_api, run_module):
    # The factory share's removal is a claim, so an entry declaring Files in tenant 1 claims the pair a second time.
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "smb_shares": [{"share_name": "Files", "tenants": ["Default"]}], "factory_defaults": {"smb_files_share": "absent"}},
    )

    assert result["failed"]
    assert result["msg"].startswith("the replay onto the rebuilt edge would refuse the inventory: Default's SMB share Files comes from both ")


def test_a_tenant_entry_without_a_name_is_refused_before_any_read(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"previous_name": "eng"}]})

    assert result["failed"]
    assert result["msg"] == "qumulo_tenants[0] gives no name"


def test_a_tenant_entry_with_a_state_other_than_present_or_absent_is_refused(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng", "state": "Absent"}]})

    assert result["failed"]
    assert result["msg"] == "qumulo_tenants[0] (eng) sets state to 'Absent'; give present or absent"


def test_two_tenant_entries_of_one_name_are_refused_before_any_read(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}, {"name": "eng"}]})

    assert result["failed"]
    assert result["msg"] == (
        "the replay onto the rebuilt edge would refuse the inventory: qumulo_tenants[0] and qumulo_tenants[1] both declare "
        "tenant eng; declare each tenant once."
    )


def test_one_mistake_gives_one_refusal_as_the_baseline_stops_after_the_stage_that_refuses(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}], "smb_shares": [{"share_name": "projects", "tenant": "eng"}]})

    assert result["failed"]
    assert result["msg"] == (
        "the replay onto the rebuilt edge would refuse the inventory: qumulo_smb_shares[0] (projects) sets tenant, which a "
        "qumulo_smb_shares entry does not take; write tenants: [eng]."
    )


def test_a_rename_still_to_be_done_reads_the_tenant_by_its_new_name(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, tenant(2, "engineering")), shares=("Files", ("projects", 2)))

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "tenants": [{"name": "eng", "previous_name": "engineering", "smb_shares": [{"share_name": "projects"}]}]},
    )

    assert not result.get("failed"), result["msg"]


def test_a_rename_already_done_leaves_the_tenant_its_name(fake_api, run_module):
    # Capture keeps writing previous_name after the rename, and a tenant of the old name made since is another tenant.
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG, tenant(3, "engineering")))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng", "previous_name": "engineering"}]})

    assert result["drift"] == [{"area": "tenants", "name": "engineering", "declared_absent": False}]


# Each tenant's networks, which the replay binds as the inventory declares them (spec 014, User Story 5, scenario 6)


def dhcp(network_id, name):
    return {"id": network_id, "name": name, "addresses": {"type": "DHCP", "dhcp_addresses": {}}}


TWO_NETWORKS = {"managed_interfaces": None, "frontend_networks": [dhcp(1, "Default"), dhcp(2, "eng-net")]}
BOUND = (tenant(1, "Default", networks=[1]), tenant(2, "eng", networks=[2]))


def test_a_network_another_tenant_holds_is_drift_when_the_inventory_declares_no_networks(fake_api, run_module):
    # The replay keeps the newly formed edge's networks, which are all tenant 1's.
    expect_edge(fake_api(), tenants=BOUND, network=TWO_NETWORKS)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "Default"}, {"name": "eng"}], "network_declared": True})

    assert result["drift"] == [{"area": "tenant_networks", "tenant": "eng", "name": "2 (eng-net)", "declared_absent": False}]
    assert "tenant_networks '2 (eng-net)' in tenant eng." in result["msg"]


def test_each_tenants_networks_declared_under_it_pass(fake_api, run_module):
    expect_edge(fake_api(), tenants=BOUND, network=TWO_NETWORKS)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [
                {"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP"}]},
                {"name": "eng", "networks": [{"id": 2, "name": "eng-net", "type": "DHCP"}]},
            ],
            "network_declared": True,
        },
    )

    assert not result.get("failed"), result["msg"]


def test_a_network_qumulo_network_gives_another_tenant_is_drift_for_the_tenant_that_holds_it(fake_api, run_module):
    expect_edge(fake_api(), tenants=BOUND, network=TWO_NETWORKS)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default"}, {"name": "eng"}],
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP", "tenant": "eng"}]},
            "network_declared": True,
        },
    )

    assert result["drift"] == [
        {"area": "tenant_networks", "tenant": "Default", "name": "1 (Default)", "declared_absent": False},
        {"area": "tenant_networks", "tenant": "eng", "name": "2 (eng-net)", "declared_absent": False},
    ]


def test_a_network_without_a_tenant_is_the_only_tenants(fake_api, run_module):
    expect_edge(fake_api(), tenants=(tenant(1, "Default", networks=[1]),))

    result = run_module(
        edge_rebuild_preflight, {**CONNECTION, "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP"}]}, "network_declared": True}
    )

    assert not result.get("failed"), result["msg"]


def test_a_tenant_that_is_drift_is_reported_without_its_networks(fake_api, run_module):
    expect_edge(fake_api(), tenants=BOUND, network=TWO_NETWORKS)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "network_declared": True})

    assert result["drift"] == [{"area": "tenants", "name": "eng", "declared_absent": False}]


def test_a_network_naming_a_tenant_the_replay_lacks_is_refused_before_any_read(fake_api, run_module):
    fake_api()

    result = run_module(
        edge_rebuild_preflight, {**CONNECTION, "network": {"frontend_networks": [{"id": 2, "name": "eng-net", "type": "DHCP", "tenant": "eng"}]}}
    )

    assert result["failed"]
    assert result["msg"].startswith(
        "the replay onto the rebuilt edge would refuse the inventory: qumulo_network.frontend_networks[0] (network 2) names tenant eng"
    )


def test_a_network_without_a_tenant_stays_tenant_1s_on_a_replay_of_two_tenants(fake_api, run_module):
    # Step 3 sends network 1, which the newly formed edge holds, with no tenant, so it keeps the tenant the formed edge
    # gives it, tenant 1.
    expect_edge(fake_api(), tenants=(tenant(1, "Default", networks=[1]), tenant(2, "eng", networks=[2])), network=TWO_NETWORKS)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default"}, {"name": "eng"}],
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP"}, {"id": 2, "name": "eng-net", "type": "DHCP", "tenant": "eng"}]},
            "network_declared": True,
        },
    )

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


NEW_WITHOUT_A_TENANT = (
    "the replay onto the rebuilt edge would refuse the inventory: qumulo_network.frontend_networks[0] (network 2) is a new "
    "network with no tenant, but this run has more than one tenant (Default and eng), so the cluster would hold it in no "
    "tenant and it would serve no client. Name its tenant with tenant, or list it under its tenant in qumulo_tenants."
)


@pytest.mark.parametrize("ignore_drift", [False, True], ids=["", "ignoring drift"])
def test_a_captured_network_no_tenant_holds_on_a_replay_of_two_tenants_is_refused_as_step_2_refuses_it(fake_api, run_module, ignore_drift):
    """Issue #305: the newly formed edge lacks network 2, so the replay's step 2 would refuse it after the destroy."""
    fake_api().expect("GET", "/v3/network", response=TWO_WITH_SPARE, etag='"n1"')

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **captured_inventory(TWO_WITH_SPARE_CAPTURE), "ignore_drift": ignore_drift})

    assert result["failed"]
    assert result["msg"] == NEW_WITHOUT_A_TENANT
    assert "drift" not in result


def test_a_network_without_a_tenant_on_the_inventory_s_backend_bond_passes_on_a_replay_of_two_tenants(fake_api, run_module):
    """Step 2 refuses only a frontend network: the backend bond takes no tenant."""
    backend = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [2]}
    edge = {**TWO_WITH_SPARE, "managed_interfaces": {**TWO_WITH_SPARE["managed_interfaces"], "backend_bond_config": backend}}
    expect_edge(fake_api(), tenants=(tenant(1, "Default", networks=[1]), ENG), shares=(), exports=(), network=edge)
    inventory = captured_inventory(TWO_WITH_SPARE_CAPTURE)
    interfaces = {**inventory["network"]["managed_interfaces"], "backend_bond_config": backend}
    inventory["network"] = {**inventory["network"], "managed_interfaces": interfaces}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **inventory})

    assert not result.get("failed"), result["msg"]


def test_a_network_without_a_tenant_on_the_backend_bond_is_refused_when_the_run_does_not_own_interfaces(fake_api, run_module):
    """Without interfaces in owned_parts, step 2 keeps the formed edge's interfaces, so the inventory's backend bond does not count."""
    fake_api().expect("GET", "/v3/network", response=TWO_WITH_SPARE, etag='"n1"')
    inventory = captured_inventory(TWO_WITH_SPARE_CAPTURE)
    backend = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [2]}
    inventory["network"] = {**inventory["network"], "managed_interfaces": {"backend_bond_config": backend}}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **inventory, "owned_parts": ["networks", "floating_ips"]})

    assert result["failed"]
    assert result["msg"] == NEW_WITHOUT_A_TENANT


def test_a_flat_network_whose_tenant_id_the_formed_edge_lacks_is_refused_on_a_replay_of_two_tenants(fake_api, run_module):
    """Step 3 sends it with no tenant, as it sends a network with neither tenant nor tenant_id."""
    fake_api().expect("GET", "/v3/network", response=TWO_WITH_SPARE, etag='"n1"')
    inventory = captured_inventory(TWO_WITH_SPARE_CAPTURE)
    inventory["network"] = {**inventory["network"], "frontend_networks": [{**inventory["network"]["frontend_networks"][0], "tenant_id": 7}]}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **inventory})

    assert result["failed"]
    assert result["msg"] == NEW_WITHOUT_A_TENANT


def test_network_1_listed_with_no_tenant_is_drift_for_the_tenant_that_holds_it_on_a_replay_of_two_tenants(fake_api, run_module):
    """The formed edge holds network 1, so the replay leaves it in tenant 1 and does not refuse it; eng loses it."""
    expect_edge(fake_api(), tenants=(tenant(1, "Default"), tenant(2, "eng", networks=[1])))

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default"}, {"name": "eng"}],
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP"}]},
            "network_declared": True,
        },
    )

    assert result["failed"]
    assert result["drift"] == [{"area": "tenant_networks", "tenant": "eng", "name": "1 (Default)", "declared_absent": False}]


UNBOUND_GAINED = (
    "the rebuilt edge would put network 1 (Default), which this edge holds in no tenant, in tenant Default: the inventory "
    "lists it with no tenant, and the replay leaves such a network in tenant 1, Default. Name its tenant with tenant, or "
    "list it under its tenant in qumulo_tenants; or set ignore_drift to rebuild with it there"
)


@pytest.mark.parametrize("ignore_drift", [False, True], ids=["", "ignoring drift"])
def test_network_1_that_no_tenant_holds_listed_with_no_tenant_is_drift_for_the_tenant_1_it_lands_in(fake_api, run_module, ignore_drift):
    """Issue #327: the newly formed edge holds network 1, so step 3 leaves it in tenant 1, where the old edge held it in none."""
    expect_edge(fake_api(), tenants=(tenant(1, "Default"), ENG))

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default"}, {"name": "eng"}],
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP"}]},
            "network_declared": True,
            "ignore_drift": ignore_drift,
        },
    )

    assert result["drift"] == [{"area": "tenant_networks", "tenant": "Default", "name": "1 (Default)", "declared_absent": False, "added": True}]
    assert result.get("failed", False) is not ignore_drift
    if not ignore_drift:
        assert result["msg"] == UNBOUND_GAINED


def test_a_flat_network_with_tenant_id_1_is_tenant_1s(fake_api, run_module):
    expect_edge(fake_api(), tenants=BOUND, network=TWO_NETWORKS)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "networks": []}, {"name": "eng", "networks": [{"id": 2, "name": "eng-net", "type": "DHCP"}]}],
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP", "tenant_id": 1}]},
            "network_declared": True,
        },
    )

    assert not result.get("failed"), result["msg"]


def test_a_renamed_tenants_networks_are_read_by_its_new_name(fake_api, run_module):
    expect_edge(fake_api(), tenants=BOUND, network=TWO_NETWORKS)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [
                {"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP"}]},
                {"name": "engineering", "previous_name": "eng", "networks": [{"id": 2, "name": "eng-net", "type": "DHCP"}]},
            ],
            "network_declared": True,
        },
    )

    assert not result.get("failed"), result["msg"]


def test_one_tenant_declaring_networks_while_another_leaves_them_out_is_refused_before_any_read(fake_api, run_module):
    fake_api()

    result = run_module(
        edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "Default"}, {"name": "eng", "networks": [{"id": 2, "name": "eng-net", "type": "DHCP"}]}]}
    )

    assert result["failed"]
    assert result["msg"].startswith(
        "the replay onto the rebuilt edge would refuse the inventory: qumulo_tenants[0] (Default) leaves out networks, but "
        "qumulo_tenants[1] (eng) declares them."
    )


def test_a_network_listed_twice_is_refused_before_any_read(fake_api, run_module):
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP"}]}],
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP"}]},
        },
    )

    assert result["failed"]
    assert "network 1 is listed in qumulo_network.frontend_networks[0] and under tenant Default; list each network once." in result["msg"]


def test_a_flat_network_without_a_tenant_while_default_is_declared_absent_is_refused(fake_api, run_module):
    # It stays tenant 1's, the tenant the run deletes.
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "state": "absent"}, {"name": "eng", "networks": []}],
            "factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
            "network": {"frontend_networks": [{"id": 1, "name": "Default", "type": "DHCP"}]},
        },
    )

    assert result["failed"]
    assert result["msg"] == (
        "the replay onto the rebuilt edge would refuse the inventory: tenant Default is state: absent but holds network 1, "
        "kept in qumulo_network.frontend_networks[0]. Give the network a tenant that stays, or leave it out so the run "
        "removes it."
    )


def test_a_network_entry_without_an_id_is_refused_before_any_read(fake_api, run_module):
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "network": {"frontend_networks": [{"name": "Default", "type": "DHCP"}]}})

    assert result["failed"]
    assert result["msg"] == "qumulo_network.frontend_networks[0] gives no id"


def test_a_tenant_network_is_refused_before_any_read_when_the_owned_parts_leave_out_networks(fake_api, run_module):
    # The baseline passes tenancy_plan the parts of the network document it writes, which Terraform can own.
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP"}]}],
            "owned_parts": ["interfaces", "floating_ips"],
        },
    )

    assert result["failed"]
    assert result["msg"] == (
        "the replay onto the rebuilt edge would refuse the inventory: qumulo_tenants[0] (Default) declares networks, but "
        "owned_parts leaves out networks, so the run cannot give a network to a tenant. Add networks to owned_parts, or "
        "leave networks out of the entry."
    )


def test_floating_ranges_are_refused_before_any_read_when_the_owned_parts_leave_out_floating_addresses(fake_api, run_module):
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "HOST", "floating_ip_ranges": ["192.0.2.50"]}]}],
            "owned_parts": ["interfaces", "networks"],
        },
    )

    assert result["failed"]
    assert result["msg"].startswith("the replay onto the rebuilt edge would refuse the inventory: qumulo_tenants[0].networks[0]")
    assert "floating_ips" in result["msg"]


def test_the_owned_parts_leaving_out_floating_addresses_pass_an_inventory_that_declares_none(fake_api, run_module):
    expect_edge(fake_api(), tenants=(tenant(1, "Default", networks=[1]),))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "owned_parts": ["interfaces", "networks"]})

    assert not result.get("failed"), result["msg"]


# An old edge without tenant 1, whose rebuilt edge would gain it (spec 014, FR-028, decision D6)


def test_an_edge_without_tenant_1_is_drift_for_the_tenant_1_the_replay_keeps(fake_api, run_module):
    expect_edge(fake_api(), tenants=(ENG,), shares=(), exports=(), network=SPARE_ONLY)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}]})

    assert result["failed"]
    assert result["drift"] == [{"area": "tenants", "name": "Default", "declared_absent": False, "added": True}]
    assert result["msg"] == (
        "the rebuilt edge would gain tenant Default, which this edge lacks: a newly formed edge has tenant 1, Default, with "
        "the factory Files share and / export, and the replay keeps it. Declare Default with state absent in "
        "qumulo_tenants, claim the factory share and export for removal, with qumulo_factory_defaults smb_files_share "
        "and nfs_root_export set to absent or by declaring them under that entry, and list each network under a tenant "
        "that stays; or set ignore_drift to rebuild with it"
    )


def test_a_gained_tenant_1_that_an_entry_renames_is_named_by_its_new_name(fake_api, run_module):
    expect_edge(fake_api(), tenants=(ENG,), shares=(), exports=(), network=SPARE_ONLY)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "Corp", "previous_name": "Default"}, {"name": "eng"}]})

    assert result["drift"] == [{"area": "tenants", "name": "Corp", "declared_absent": False, "added": True}]
    assert result["msg"].startswith(
        "the rebuilt edge would gain tenant Corp, which this edge lacks: a newly formed edge has tenant 1, Default, with the "
        "factory Files share and / export, and the replay renames it to Corp and keeps it. Drop qumulo_tenants' entry for "
        "Corp, or its previous_name, and declare Default with state absent"
    )


def test_an_edge_without_tenant_1_passes_once_the_inventory_declares_default_absent(fake_api, run_module):
    expect_edge(fake_api(), tenants=(ENG,), shares=(), exports=(), network=SPARE_ONLY)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "state": "absent"}, {"name": "eng", "networks": []}],
            "factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
        },
    )

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_default_declared_absent_with_no_network_listed_is_refused_since_it_keeps_the_formed_networks(fake_api, run_module):
    # Tenant 1 holds every network of a newly formed edge, and with no network listed step 3 keeps them.
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "state": "absent"}, {"name": "eng"}],
            "factory_defaults": {"smb_files_share": "absent", "nfs_root_export": "absent"},
        },
    )

    assert result["failed"]
    assert result["msg"] == (
        "the replay onto the rebuilt edge would refuse the inventory: tenant Default is state: absent but holds the newly "
        "formed edge's networks, which this run keeps: no present tenant declares networks and "
        "qumulo_network.frontend_networks is unset, so the run sends no network document. Declare networks under every "
        "present tenant, or set qumulo_network.frontend_networks, listing each network under a tenant that stays."
    )


def test_a_tenant_lost_and_a_tenant_gained_are_both_in_one_message(fake_api, run_module):
    expect_edge(fake_api(), tenants=(ENG, tenant(3, "finance")), shares=(), exports=(), network=SPARE_ONLY)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}]})

    assert result["drift"] == [
        {"area": "tenants", "name": "finance", "declared_absent": False},
        {"area": "tenants", "name": "Default", "declared_absent": False, "added": True},
    ]
    assert result["msg"].startswith("the edge holds state its inventory does not declare, which a rebuild would lose: tenants 'finance'.")
    assert ". the rebuilt edge would gain tenant Default, which this edge lacks:" in result["msg"]


def test_ignore_drift_rebuilds_an_edge_without_tenant_1_with_default(fake_api, run_module):
    expect_edge(fake_api(), tenants=(ENG,), shares=(), exports=(), network=SPARE_ONLY)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": [{"name": "eng"}], "ignore_drift": True})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == [{"area": "tenants", "name": "Default", "declared_absent": False, "added": True}]


# Replication relationships the inventory declares (spec 011, User Story 3, scenario 8)

REPLICA = {"side": "target", "peer_host": "hub01", "peer_cluster_uuid": HUB_UUID, "source_path": "/src", "target_path": "/replica"}
SOURCE = {"side": "source", "peer_host": "dr01", "peer_cluster_uuid": HUB_UUID, "source_path": "/projects", "target_path": "/dr/projects"}
# The UUID a peer had before it was rebuilt, which its host's qumulo_previous_cluster_uuids holds.
OLD_PEER_UUID = "00000000-0000-4000-8000-0000000000aa"


def replica(state="ESTABLISHED", **extra):
    return target_status("7", target="/replica", state=state, **extra)


def hub_source(state="ESTABLISHED", rid="7"):
    """The hub's own record of the replica's relationship, as replication_info returns it."""
    record = source_status(rid, target="/replica", target_uuid=CLUSTER_ID, state=state, recovery_point="2026-11-01T10:00:00Z")
    return describe_source({**record, "source_cluster_uuid": HUB_UUID})


def dr_target(state="ESTABLISHED", rid="12", recovery_point="2026-11-01T09:00:00Z"):
    """dr01's own record of the edge's /projects relationship, as replication_info returns it."""
    record = target_status(rid, source="/projects", target="/dr/projects", state=state, recovery_point=recovery_point or "")
    return describe_target({**record, "source_cluster_uuid": CLUSTER_ID, "target_cluster_uuid": HUB_UUID})


def details(result, area):
    return [entry["detail"] for entry in result["unreplayable"] if entry["area"] == area]


def test_the_preflight_reads_the_status_routes_and_lists_each_relationship_for_the_record(fake_api, run_module):
    expect_edge(fake_api(), target=(replica(),), local={"/": [child("replica", files=5)]}, snapshots=[snapshot("2", "7")])

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**REPLICA, "peer_relationships": [hub_source()]}]})

    assert not result.get("failed"), result["msg"]
    assert result["unreplayable"] == []
    [relationship] = result["replication"]
    assert {key: value for key, value in relationship.items() if key != "detail"} == {
        "id": "7",
        "side": "target",
        "peer_cluster_uuid": HUB_UUID,
        "source_path": "/src",
        "target_path": "/replica",
        "state": "ESTABLISHED",
        "outcome": "replayable",
        "peer": {"state": "ESTABLISHED", "recovery_point": "2026-11-01T10:00:00Z", "error_from_last_job": None},
    }
    assert "hub01" in relationship["detail"]


def test_a_replayable_replica_and_its_own_snapshot_are_not_local_data_but_other_snapshots_are(fake_api, run_module):
    snapshots = [snapshot("2", "7"), snapshot("3"), snapshot("4", "7", "9")]
    expect_edge(fake_api(), target=(replica(),), local={"/": [child("replica", files=5)]}, snapshots=snapshots)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**REPLICA, "peer_relationships": [hub_source()]}]})

    assert result["unreplayable"] == [{"area": "local_data", "detail": "snapshots: 2 snapshots"}]


def test_a_replayable_replica_below_the_root_is_not_local_data_but_its_sibling_is(fake_api, run_module):
    expect_edge(
        fake_api(),
        target=(target_status("7", target="/data/replica"),),
        local={"/": [child("data", files=7)], "/data": [child("replica", files=5), child("other", files=2)]},
    )
    declared = {**REPLICA, "target_path": "/data/replica", "peer_relationships": [hub_source()]}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    assert result["unreplayable"] == [{"area": "local_data", "detail": "files: /data/other holds 2 files"}]


def test_a_failed_over_replica_below_the_root_is_local_data_with_its_sibling(fake_api, run_module):
    expect_edge(
        fake_api(),
        target=(target_status("7", target="/data/replica", state="DISCONNECTED", read_only=False),),
        local={"/": [child("data", files=7)], "/data": [child("replica", files=5), child("other", files=2)]},
    )
    declared = {**REPLICA, "target_path": "/data/replica", "peer_relationships": [hub_source()]}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    replica_detail, other_detail = details(result, "local_data")
    assert replica_detail.startswith("files: /data/replica holds 5 files, ")
    assert "DISCONNECTED" in replica_detail
    assert other_detail == "files: /data/other holds 2 files"


@pytest.mark.parametrize(
    ("record", "peers", "names"),
    [
        (replica("DISCONNECTED", read_only=False), [hub_source()], ["DISCONNECTED", "writable", "hub01's record is ESTABLISHED"]),
        (replica(read_only=False), [hub_source()], ["ESTABLISHED", "writable", "hub01's record is ESTABLISHED"]),
        (replica("AWAITING_AUTHORIZATION"), [hub_source("AWAITING_AUTHORIZATION")], ["AWAITING_AUTHORIZATION", "hub01's record is AWAITING_AUTHORIZATION"]),
        (replica("ENDED", read_only=False), [], ["ENDED", "writable", "hub01 holds no record"]),
        (replica(), [hub_source("ENDED")], ["hub01's record is ENDED"]),
        (replica(), [hub_source("DISCONNECTED")], ["hub01's record is DISCONNECTED"]),
        (replica(), [hub_source(rid="8")], ["hub01 holds no record"]),
        (replica(), None, ["hub01 did not answer"]),
    ],
    ids=["failed-over", "made-writable", "pending", "ended", "source-ended", "source-failed-over", "source-another-id", "source-unread"],
)
def test_a_declared_replica_in_any_other_state_is_local_data_naming_both_states(fake_api, run_module, record, peers, names):
    expect_edge(fake_api(), target=(record,), local={"/": [child("replica", files=2)]}, snapshots=[snapshot("2", "7")])

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**REPLICA, "peer_relationships": peers}]})

    assert result["failed"]
    [relationship] = result["replication"]
    assert (relationship["outcome"], relationship["state"]) == ("local_data", record["state"])
    files, snapshots = details(result, "local_data")
    assert files.startswith("files: /replica holds 2 files, ")
    assert [name for name in names if name not in files] == []
    assert snapshots == "snapshots: 1 snapshot"


def test_a_declared_replica_the_replay_cannot_rebuild_is_listed_even_when_it_holds_nothing(fake_api, run_module):
    expect_edge(fake_api(), target=(replica("ENDED"),), local={"/": [child("replica")]})

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**REPLICA, "peer_relationships": []}]})

    [files] = details(result, "local_data")
    assert files.startswith("files: /replica holds 0 files, ")
    assert "ENDED" in files


def test_local_data_acknowledged_lets_a_replica_the_replay_cannot_rebuild_go(fake_api, run_module):
    expect_edge(fake_api(), target=(replica("DISCONNECTED", read_only=False),), local={"/": [child("replica", files=2)]})

    result = run_module(
        edge_rebuild_preflight, {**CONNECTION, "acknowledge": ["local_data"], "replication_declared": [{**REPLICA, "peer_relationships": [hub_source()]}]}
    )

    assert not result.get("failed"), result["msg"]
    assert result["acknowledged"] == ["local_data"]


def test_a_declared_source_is_held_with_its_replica_and_its_source_directory_stays_local_data(fake_api, run_module):
    record = source_status("12", source="/projects", target="/dr/projects")
    expect_edge(fake_api(), source=(record,), local={"/": [child("projects", files=3)]}, snapshots=[snapshot("5", "12")])

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**SOURCE, "peer_relationships": [dr_target()]}]})

    assert result["unreplayable"] == [{"area": "local_data", "detail": "files: /projects holds 3 files"}]
    [relationship] = result["replication"]
    assert relationship["outcome"] == "held"
    assert relationship["peer"] == {"state": "ESTABLISHED", "recovery_point": "2026-11-01T09:00:00Z", "error_from_last_job": None}
    assert "2026-11-01T09:00:00Z" in relationship["detail"]


@pytest.mark.parametrize(
    ("peers", "why"),
    [([dr_target("ENDED", recovery_point=None)], "ENDED"), ([], "holds no record"), (None, "did not answer")],
    ids=["ended", "missing", "unread"],
)
def test_a_declared_source_whose_target_holds_no_live_replica_says_there_may_be_none_and_its_snapshots_count(fake_api, run_module, peers, why):
    expect_edge(fake_api(), source=(source_status("12", source="/projects", target="/dr/projects"),), snapshots=[snapshot("5", "12")])

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**SOURCE, "peer_relationships": peers}]})

    [relationship] = result["replication"]
    assert relationship["outcome"] == "held"
    assert "there may be none to restore from" in relationship["detail"]
    assert why in relationship["detail"]
    # The snapshot may hold the only copy left of the source's data.
    assert result["unreplayable"] == [{"area": "local_data", "detail": "snapshots: 1 snapshot"}]


@pytest.mark.parametrize("state", ["ENDED", "AWAITING_AUTHORIZATION"])
def test_a_declared_source_whose_record_is_ended_or_pending_needs_replication_acknowledged(fake_api, run_module, state):
    expect_edge(fake_api(), source=(source_status("12", source="/projects", target="/dr/projects", state=state),))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**SOURCE, "peer_relationships": []}]})

    assert result["failed"]
    [relationship] = result["replication"]
    assert (relationship["outcome"], relationship["state"]) == ("unreplayable", state)
    [detail] = details(result, "replication")
    assert "source relationship 12 " in detail
    assert state in detail


def test_one_relationship_of_each_kind_is_held_and_replayable_and_only_their_own_snapshots_are_exempt(fake_api, run_module):
    expect_edge(
        fake_api(),
        source=(source_status("12", source="/projects", target="/dr/projects"),),
        target=(replica(),),
        local={"/": [child("projects", files=3), child("replica", files=5)]},
        snapshots=[snapshot("2", "7"), snapshot("5", "12"), snapshot("6", "7", "12"), snapshot("8", "7", "99")],
    )
    declared = [{**REPLICA, "peer_relationships": [hub_source()]}, {**SOURCE, "peer_relationships": [dr_target()]}]

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": declared})

    assert result["unreplayable"] == [
        {"area": "local_data", "detail": "files: /projects holds 3 files"},
        {"area": "local_data", "detail": "snapshots: 1 snapshot"},
    ]
    assert [item["outcome"] for item in result["replication"]] == ["held", "replayable"]


def test_a_declared_source_whose_target_was_rebuilt_is_matched_by_its_previous_uuid_and_may_have_no_replica(fake_api, run_module):
    record = source_status("12", source="/projects", target="/dr/projects", target_uuid=OLD_PEER_UUID)
    expect_edge(fake_api(), source=(record,), local={"/": [child("projects", files=3)]}, snapshots=[snapshot("5", "12")])
    # dr01's new cluster happens to hold a record with the same id; it is not this relationship's.
    declared = {**SOURCE, "peer_previous_cluster_uuids": [OLD_PEER_UUID.upper()], "peer_relationships": [dr_target()]}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    [relationship] = result["replication"]
    assert relationship["outcome"] == "held"
    assert "peer" not in relationship
    assert OLD_PEER_UUID in relationship["detail"]
    assert "there may be none to restore from" in relationship["detail"]
    assert result["unreplayable"] == [
        {"area": "local_data", "detail": "files: /projects holds 3 files"},
        {"area": "local_data", "detail": "snapshots: 1 snapshot"},
    ]


def test_a_replica_whose_source_was_rebuilt_is_the_only_copy_and_needs_local_data_and_replication(fake_api, run_module):
    expect_edge(fake_api(), target=(replica(source_uuid=OLD_PEER_UUID),), local={"/": [child("replica", files=5)]}, snapshots=[snapshot("2", "7")])
    # The hub's new cluster happens to hold an ESTABLISHED record with the same id, which would make it replayable.
    declared = {**REPLICA, "peer_previous_cluster_uuids": [OLD_PEER_UUID], "peer_relationships": [hub_source()]}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    assert result["failed"]
    [relationship] = result["replication"]
    assert relationship["outcome"] == "only_copy"
    assert "peer" not in relationship
    files, snapshots = details(result, "local_data")
    assert files.startswith("files: /replica holds 5 files, ")
    assert OLD_PEER_UUID in files
    assert "only copy" in files
    assert snapshots == "snapshots: 1 snapshot"
    # Rebuilding the edge ends the hold on the source's side, so replication must be acknowledged too.
    [hold] = details(result, "replication")
    assert "hub01" in hold
    assert "hold" in hold


# Spec User Story 8, scenario 13: a replica the source's restore can come back from names that restore first.
ONLY_COPY_NAME = f"target relationship 7 from {OLD_PEER_UUID}:/src into /replica"
RESTORE_TAIL = (
    ", which copies this replica back into hub01:/src and reverses the relationship, after which this rebuild finds it replayable. "
    "To give the replica up instead, acknowledge both local_data and replication."
)


def only_copy_detail(result):
    [relationship] = result["replication"]
    assert relationship["outcome"] == "only_copy"
    return relationship["detail"]


@pytest.mark.parametrize(
    ("in_edges", "restore"),
    [
        (
            True,
            (
                "ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l hub01, once hub01's license applies, or at once if "
                "hub01 declares qumulo_capacity.license: none"
            ),
        ),
        (False, "ansible-playbook qumulo.cluster.replication_restore -i <inventory> -l hub01"),
    ],
    ids=["source_in_qumulo_edges", "any_other_source"],
)
def test_an_only_copy_replica_the_sources_restore_can_come_back_from_names_that_restore(fake_api, run_module, in_edges, restore):
    expect_edge(fake_api(), target=(replica(source_uuid=OLD_PEER_UUID),), local={"/": [child("replica", files=5)]})
    declared = {
        **REPLICA,
        "peer_previous_cluster_uuids": [OLD_PEER_UUID],
        "peer_relationships": [],
        "peer_replication_address": "203.0.113.30",
        "peer_in_edges": in_edges,
    }

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    assert result["failed"]
    detail = (
        f"the target directory of {ONLY_COPY_NAME}, which may hold the only copy of the data hub01 had before it was rebuilt. Restore hub01 "
        f"from it first: {restore}{RESTORE_TAIL}"
    )
    assert only_copy_detail(result) == detail
    assert details(result, "replication") == [detail]
    assert details(result, "local_data") == [f"files: /replica holds 5 files, {detail}"]


@pytest.mark.parametrize(
    ("record", "address"),
    [
        ({"state": "DISCONNECTED", "read_only": False}, "203.0.113.30"),
        ({"read_only": False}, "203.0.113.30"),
        ({"recovery_point": None}, "203.0.113.30"),
        ({}, "hub01.example.com"),
        ({}, None),
    ],
    ids=["failed_over", "writable", "no_recovery_point", "address_a_name", "no_address"],
)
def test_an_only_copy_replica_that_does_not_allow_a_restore_keeps_rs_text(fake_api, run_module, record, address):
    expect_edge(fake_api(), target=(replica(source_uuid=OLD_PEER_UUID, **record),), local={"/": [child("replica", files=5)]})
    declared = {**REPLICA, "peer_previous_cluster_uuids": [OLD_PEER_UUID], "peer_relationships": [], "peer_in_edges": True}
    if address is not None:
        declared["peer_replication_address"] = address

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    assert only_copy_detail(result) == (
        f"the target directory of {ONLY_COPY_NAME}, which may hold the only copy of the data hub01 had before: hub01 was rebuilt since, "
        f"and the record names its previous UUID {OLD_PEER_UUID}. The edge's record is the hold on hub01's side; rebuilding the edge ends "
        "it, and the second pass then creates the relationship from hub01's source directory as it is"
    )


@pytest.mark.parametrize(
    ("acknowledge", "refused"),
    [(["local_data"], ["replication"]), (["replication"], ["local_data"]), (["local_data", "replication"], [])],
    ids=["local-data-alone", "replication-alone", "both"],
)
def test_a_replica_whose_source_was_rebuilt_goes_only_with_both_acknowledged(fake_api, run_module, acknowledge, refused):
    expect_edge(fake_api(), target=(replica(source_uuid=OLD_PEER_UUID),), local={"/": [child("replica", files=5)]})
    declared = {**REPLICA, "peer_previous_cluster_uuids": [OLD_PEER_UUID], "peer_relationships": []}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "acknowledge": acknowledge, "replication_declared": [declared]})

    assert bool(result.get("failed")) == bool(refused)
    assert result["acknowledged"] == sorted(acknowledge)
    if refused:
        [area] = refused
        assert result["msg"].startswith(f"the edge holds state the replay cannot create again: {area} (")


def test_the_current_uuid_wins_over_a_previous_one_when_two_entries_share_a_key(fake_api, run_module):
    expect_edge(fake_api(), source=(source_status("12", source="/projects", target="/dr/projects"),))
    # dr02's host_vars were copied from dr01's, previous UUIDs and all.
    copied = {**SOURCE, "peer_host": "dr02", "peer_cluster_uuid": OLD_PEER_UUID, "peer_previous_cluster_uuids": [HUB_UUID], "peer_relationships": []}
    declared = [copied, {**SOURCE, "peer_relationships": [dr_target()]}]

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": declared})

    [relationship] = result["replication"]
    assert relationship["outcome"] == "held"
    assert relationship["peer"]["state"] == "ESTABLISHED"
    assert "dr01" in relationship["detail"]
    assert "dr02" not in relationship["detail"]


@pytest.mark.parametrize(
    ("live", "declared", "host"),
    [
        ({"source": (source_status("12", source="/projects", target="/dr/projects", target_uuid=OLD_PEER_UUID),)}, SOURCE, "dr01"),
        ({"target": (replica(source_uuid=OLD_PEER_UUID),)}, REPLICA, "hub01"),
    ],
    ids=["source", "target"],
)
def test_a_declared_relationship_whose_peer_inventory_lacks_the_uuid_says_to_apply_the_peers_patch(fake_api, run_module, live, declared, host):
    expect_edge(fake_api(), **live)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**declared, "peer_relationships": []}]})

    [relationship] = result["replication"]
    assert relationship["outcome"] == "unreplayable"
    [detail] = details(result, "replication")
    assert [token for token in (host, OLD_PEER_UUID, "host_vars_patch") if token not in detail] == []


@pytest.mark.parametrize(("live", "rid"), [({"source": ("r1",)}, "r1"), ({"target": ("r2",)}, "r2")], ids=["source", "target"])
def test_an_undeclared_relationship_needs_replication_listed_by_its_id_peer_and_paths(fake_api, run_module, live, rid):
    expect_edge(fake_api(), **live)

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["failed"]
    [detail] = details(result, "replication")
    assert [token for token in (f" {rid} ", HUB_UUID, "/src", "/dst") if token not in detail] == []
    assert [item["outcome"] for item in result["replication"]] == ["unreplayable"]


@pytest.mark.parametrize(
    "declared",
    [
        {**REPLICA, "target_path": "/elsewhere"},
        {**REPLICA, "source_path": "/other"},
        {**REPLICA, "peer_cluster_uuid": "00000000-0000-4000-8000-000000000009"},
        {**REPLICA, "side": "source"},
    ],
    ids=["another-target-path", "another-source-path", "another-peer", "the-other-side"],
)
def test_a_relationship_no_entry_declares_needs_replication_acknowledged(fake_api, run_module, declared):
    expect_edge(fake_api(), target=(replica(),), local={"/": [child("replica", files=1)]}, snapshots=[snapshot("2", "7")])

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [{**declared, "peer_relationships": [hub_source()]}]})

    [detail] = details(result, "replication")
    assert "target relationship 7 " in detail
    assert details(result, "local_data") == ["files: /replica holds 1 file", "snapshots: 1 snapshot"]
    assert [item["outcome"] for item in result["replication"]] == ["unreplayable"]


def test_declared_paths_and_uuids_compare_as_the_server_reports_them(fake_api, run_module):
    expect_edge(fake_api(), target=(replica(source_uuid=HUB_UUID.upper()),), local={"/": [child("replica", files=1)]})
    declared = {**REPLICA, "peer_cluster_uuid": HUB_UUID.upper(), "source_path": "/src/", "target_path": "/replica/", "peer_relationships": [hub_source()]}

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "replication_declared": [declared]})

    assert not result.get("failed"), result["msg"]
    assert [item["outcome"] for item in result["replication"]] == ["replayable"]


# A host-managed edge forms again with no frontend network, and the replay first writes every network the inventory
# lists, all in tenant 1, so that step 2 does not refuse the second tenant's create with message 1 after the destroy
# (issues #294, #306). An inventory that lists none leaves that pass nothing to write, which the preflight refuses
# whatever ignore_drift says.
HOSTED = {"managed_interfaces": None, "frontend_networks": [{"id": 0, "name": "Default", "addresses": {"type": "HOST", "host_addresses": {}}}]}
HOSTED_TENANTS = (tenant(1, "it-one", networks=[0]), tenant(2, "it-second"))
HOST0 = {"id": 0, "name": "Default", "type": "HOST"}
NO_NETWORK_OPENING = (
    "the replay would create tenant it-second on the newly formed edge, which like this edge is host-managed and forms "
    "with no frontend network, but the inventory lists no network for a tenant that stays, so the baseline's step 2 "
    "would refuse the create after the destroy. "
)
FIRST_NETWORK = NO_NETWORK_OPENING + "List the edge's HOST network under a tenant in qumulo_tenants, or in qumulo_network.frontend_networks"
FORMED_EDGE = ([{"id": 1, "name": "Default"}], {"managed_interfaces": None, "frontend_networks": []})


@pytest.mark.parametrize(
    ("tenants", "network"),
    [
        pytest.param([{"name": "it-one", "previous_name": "Default"}, {"name": "it-second"}], None, id="no networks listed"),
        pytest.param([{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": []}], None, id="none for any tenant"),
    ],
)
@pytest.mark.parametrize("ignore_drift", [False, True], ids=["", "ignoring drift"])
def test_a_host_managed_edge_whose_replay_creates_a_tenant_needs_a_network_listed(fake_api, run_module, tenants, network, ignore_drift):
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "network": network, "network_declared": True, "ignore_drift": ignore_drift})

    assert result["failed"]
    # Last, after any drift, which ignore_drift waives and this does not.
    assert result["msg"].endswith(FIRST_NETWORK)
    if ignore_drift:
        assert result["msg"] == FIRST_NETWORK


@pytest.mark.parametrize(
    ("tenants", "network"),
    [
        pytest.param([{"name": "it-one", "previous_name": "Default", "networks": [HOST0]}, {"name": "it-second", "networks": []}], None, id="under tenant 1"),
        pytest.param(
            [{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": [HOST0]}], None, id="under the second tenant"
        ),
        pytest.param(
            [{"name": "it-one", "previous_name": "Default"}, {"name": "it-second"}],
            {"frontend_networks": [{**HOST0, "tenant": "it-second"}]},
            id="flat for the second tenant",
        ),
    ],
)
def test_a_host_managed_edge_whose_inventory_lists_its_network_anywhere_passes(fake_api, run_module, tenants, network):
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "network": network, "network_declared": True, "ignore_drift": True})

    assert "forms with no frontend network" not in result.get("msg", ""), result["msg"]


@pytest.mark.parametrize(
    ("network", "tenants", "edge"),
    [
        pytest.param(DHCP, [{"name": "Default"}, {"name": "eng"}], (DEFAULT_TENANT, ENG), id="managed networking"),
        pytest.param(HOSTED, [{"name": "it-one", "previous_name": "Default"}], (tenant(1, "it-one", networks=[0]),), id="no tenant to create"),
    ],
)
def test_no_network_for_tenant_1_is_needed_on_managed_networking_or_without_a_create(fake_api, run_module, network, tenants, edge):
    expect_edge(fake_api(), tenants=edge, network=network)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "network_declared": True})

    assert not result.get("failed"), result["msg"]


FLAT0 = {"frontend_networks": [HOST0]}
ABSENT_FACTORY = {"smb_files_share": "absent", "nfs_root_export": "absent"}


@pytest.mark.parametrize(
    ("tenants", "network"),
    [
        pytest.param([{"name": "it-one", "previous_name": "Default", "networks": [HOST0]}, {"name": "it-second", "networks": []}], None, id="under tenant 1"),
        pytest.param([{"name": "it-one", "previous_name": "Default"}, {"name": "it-second"}], None, id="no networks listed"),
        pytest.param([{"name": "it-second", "networks": []}], FLAT0, id="tenant 1 undeclared, the network flat with no tenant"),
        pytest.param(
            [{"name": "it-second", "networks": []}], {"frontend_networks": [{**HOST0, "tenant": "Default"}]}, id="tenant 1 undeclared, tenant Default"
        ),
        pytest.param(
            [{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": []}],
            {"frontend_networks": [{**HOST0, "tenant_id": 7}]},
            id="a tenant_id the formed edge lacks",
        ),
        pytest.param(
            [{"name": "it-one", "previous_name": "Default"}, {"name": "it-second"}],
            {"frontend_networks": [{**HOST0, "tenant": "it-second"}]},
            id="the network flat for the second tenant",
        ),
        pytest.param([{"name": "Default", "state": "absent"}, {"name": "it-second", "networks": [HOST0]}], None, id="tenant 1 absent"),
        pytest.param(
            [{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": []}], None, id="no network for any tenant"
        ),
    ],
)
def test_the_preflight_refuses_exactly_where_the_replay_would_run_no_tenant_1_pass(fake_api, run_module, tenants, network):
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "tenants": tenants, "network": network, "network_declared": True, "ignore_drift": True, "factory_defaults": ABSENT_FACTORY},
    )

    refused = "forms with no frontend network, but " in result.get("msg", "")
    assert refused == (tenancy_first_pass(tenants, *FORMED_EDGE, network) == {}), result.get("msg")


def test_a_host_managed_edge_s_network_without_a_tenant_passes_on_a_replay_of_two_tenants(fake_api, run_module):
    """The tenant 1 pass writes the HOST network, which the edge forms without, before the replay's step 2 runs.

    Today the pass writes every listed network with no tenant, so on host-managed networking the refusal of a new network
    with no tenant cannot fire; this pins that the networks the pass writes count as the formed edge's."""
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)
    tenants = [{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": []}]

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "network": FLAT0, "network_declared": True})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_a_network_no_tenant_holds_that_the_formed_edge_lacks_is_drift_on_a_replay_of_one_tenant(fake_api, run_module):
    """With one tenant at step 3 nothing refuses the new network, and step 3 puts it in that tenant."""
    expect_edge(fake_api(), tenants=(tenant(1, "Default", networks=[1]),), network=TWO_NETWORKS)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP"}]}],
            "network": {"frontend_networks": [{"id": 2, "name": "eng-net", "type": "DHCP"}]},
            "network_declared": True,
        },
    )

    assert result["drift"] == [{"area": "tenant_networks", "tenant": "Default", "name": "2 (eng-net)", "declared_absent": False, "added": True}]
    assert result["msg"] == (
        "the rebuilt edge would put network 2 (eng-net), which this edge holds in no tenant, in tenant Default: the inventory "
        "lists it with no tenant, and the replay leaves such a network in tenant 1, Default. Name its tenant with tenant, or "
        "list it under its tenant in qumulo_tenants; or set ignore_drift to rebuild with it there"
    )


def test_two_networks_no_tenant_holds_are_drift_in_one_reason_on_a_replay_of_one_tenant(fake_api, run_module):
    network = {"managed_interfaces": None, "frontend_networks": [dhcp(1, "Default"), dhcp(2, "a"), dhcp(3, "b")]}
    expect_edge(fake_api(), tenants=(tenant(1, "Default", networks=[1]),), network=network)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP"}]}],
            "network": {"frontend_networks": [{"id": 2, "name": "a", "type": "DHCP"}, {"id": 3, "name": "b", "type": "DHCP", "tenant_id": 7}]},
            "network_declared": True,
        },
    )

    assert [entry["name"] for entry in result["drift"]] == ["2 (a)", "3 (b)"]
    assert result["msg"].startswith(
        "the rebuilt edge would put network 2 (a) and network 3 (b), which this edge holds in no tenant, in tenant Default: "
        "the inventory lists them with no tenant, and the replay leaves such a network in tenant 1, Default. Name each one's "
        "tenant with tenant, or list them under each one's tenant in qumulo_tenants"
    )


def test_network_1_that_no_tenant_holds_is_drift_when_the_inventory_lists_no_network(fake_api, run_module):
    """With no network listed the replay keeps the formed edge's network 1, in tenant 1."""
    expect_edge(fake_api(), tenants=(tenant(1, "Default"),))

    result = run_module(edge_rebuild_preflight, CONNECTION)

    assert result["drift"] == [{"area": "tenant_networks", "tenant": "Default", "name": "1 (Default)", "declared_absent": False, "added": True}]
    assert result["msg"] == (
        "the rebuilt edge would put network 1 (Default), which this edge holds in no tenant, in tenant Default: the inventory "
        "lists no network, so the replay keeps the newly formed edge's network 1 in tenant 1, Default. List it under its "
        "tenant in qumulo_tenants, or in qumulo_network.frontend_networks with tenant, with every other network the edge "
        "holds; or set ignore_drift to rebuild with it there"
    )


def test_a_host_managed_edge_s_network_that_no_tenant_holds_is_drift_for_the_tenant_1_the_pass_puts_it_in(fake_api, run_module):
    """Issue #327: the tenant 1 pass writes the flat network into tenant 1, where the old edge held it in none."""
    expect_edge(fake_api(), tenants=(tenant(1, "it-one"), tenant(2, "it-second")), network=HOSTED)
    tenants = [{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": []}]

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "network": FLAT0, "network_declared": True})

    assert result["failed"]
    assert result["drift"] == [{"area": "tenant_networks", "tenant": "it-one", "name": "0 (Default)", "declared_absent": False, "added": True}]
    assert result["msg"].startswith("the rebuilt edge would put network 0 (Default), which this edge holds in no tenant, in tenant it-one")


def test_tenant_1_declared_absent_rebuilds_with_the_formed_default_holding_the_networks_until_the_baseline_binds_them(fake_api, run_module):
    tenants = [{"name": "Default", "state": "absent"}, {"name": "it-one", "networks": [HOST0]}, {"name": "it-second", "networks": []}]
    expect_edge(fake_api(), tenants=(tenant(2, "it-one", networks=[0]), tenant(3, "it-second")), network=HOSTED)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "factory_defaults": ABSENT_FACTORY, "network_declared": True})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []
    assert tenancy_first_pass(tenants, *FORMED_EDGE) == {"tenants": [{"name": "Default"}], "network": {"frontend_networks": [HOST0]}}


def test_the_rename_an_edge_without_tenant_1_is_told_to_make_passes_and_runs_the_pass(fake_api, run_module):
    tenants = [{"name": "it-one", "previous_name": "Default", "networks": [HOST0]}, {"name": "it-second", "networks": []}]
    expect_edge(fake_api(), tenants=(tenant(2, "it-one", networks=[0]), tenant(3, "it-second")), network=HOSTED)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "factory_defaults": ABSENT_FACTORY, "network_declared": True})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []
    assert tenancy_first_pass(tenants, *FORMED_EDGE) == {"tenants": [{"name": "it-one", "previous_name": "Default"}], "network": {"frontend_networks": [HOST0]}}


def test_an_edge_without_tenant_1_passes_with_the_inventory_capture_writes_of_it(fake_api, run_module):
    """Issue #287: capture_info writes Default absent and the factory share and export absent, which the replay applies."""
    expect_edge(fake_api(), tenants=(ENG_WITHOUT_TENANT_1,), shares=(), exports=(), network=WITHOUT_TENANT_1)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **captured_inventory(WITHOUT_TENANT_1_CAPTURE)})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def captured_inventory(captured):
    """The preflight's options from a capture's host_vars, as predestroy.yml passes the inventory's."""
    return {
        "tenants": captured["qumulo_tenants"],
        "smb_shares": captured["qumulo_smb_shares"],
        "nfs_exports": captured["qumulo_nfs_exports"],
        "factory_defaults": captured["qumulo_factory_defaults"],
        "network": captured["qumulo_network"],
        "network_declared": True,
    }


def test_an_edge_whose_other_tenant_is_named_default_passes_with_the_inventory_capture_writes_of_it(fake_api, run_module):
    """The newly formed edge's Default becomes that tenant, without the factory share and export."""
    expect_edge(fake_api(), tenants=(NAMED_DEFAULT, NAMED_ENG), shares=(), exports=(), network=NAMED_DEFAULT_DOCUMENT)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **captured_inventory(NAMED_DEFAULT_CAPTURE)})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_an_edge_without_tenant_1_beside_a_network_no_tenant_holds_is_refused_before_the_destroy(fake_api, run_module):
    """The rebuilt edge would hold Default beside eng, and step 2 refuses the new network without a tenant; with Default
    absent the replay refuses it here instead, with the remedy, and ignore_drift does not waive it."""
    fake_api()

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **captured_inventory(WITH_SPARE_CAPTURE), "ignore_drift": True})

    assert result["failed"]
    assert "tenant Default is state: absent but holds network 2" in result["msg"]
    assert "Give the network a tenant that stays" in result["msg"]


def test_claims_under_the_absent_default_entry_do_not_reach_the_renamed_tenant_1():
    # Why an inventory that renames tenant 1 claims the factory share and export in qumulo_factory_defaults: once tenant 1
    # is renamed, a claim under the absent Default entry names a tenant the replay no longer holds, and removes nothing.
    tenants = [
        {"name": "Default", "state": "absent", "smb_shares": [{"share_name": "Files"}], "nfs_exports": [{"export_path": "/"}]},
        {"name": "it-one", "previous_name": "Default", "networks": [HOST0]},
        {"name": "it-second", "networks": []},
    ]

    built = replay(tenants, [], [], {})

    assert built.findings.refusals == []
    assert (built.smb.removals, built.nfs.removals) == ([], [])


# A host-managed edge without tenant 1: Default declared absent, as an inventory written by hand may declare it, and the
# rename, which capture_info writes of such an edge, both pass.
HOSTED_ENG = tenant(2, "eng", networks=[0])
HOSTED_WITHOUT_TENANT_1 = {
    "managed_interfaces": None,
    "frontend_networks": [{"id": 0, "name": "eng", "tenant_id": 2, "addresses": {"type": "HOST", "host_addresses": {}}}],
}
HOST_ENG = {"id": 0, "name": "eng", "type": "HOST"}
HOSTED_ABSENT = {
    **WITHOUT_TENANT_1_CAPTURE,
    "qumulo_tenants": [{"name": "Default", "state": "absent"}, {"name": "eng", "networks": [HOST_ENG], "smb_settings": "inherit", "nfs_settings": "inherit"}],
    "qumulo_network": {"frontend_networks": []},
}


def test_default_absent_on_a_host_managed_edge_without_tenant_1_passes(fake_api, run_module):
    expect_edge(fake_api(), tenants=(HOSTED_ENG,), shares=(), exports=(), network=HOSTED_WITHOUT_TENANT_1)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **captured_inventory(HOSTED_ABSENT)})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


def test_a_host_managed_edge_without_tenant_1_passes_with_the_inventory_capture_writes_of_it(fake_api, run_module):
    expect_edge(fake_api(), tenants=(HOSTED_ENG,), shares=(), exports=(), network=HOSTED_WITHOUT_TENANT_1)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, **captured_inventory(HOSTED_WITHOUT_TENANT_1_CAPTURE)})

    assert not result.get("failed"), result["msg"]
    assert result["drift"] == []


# Step 2's host-managed check of each tenant network, which the newly formed edge, host-managed as this one is, would
# otherwise make only after the destroy.
@pytest.mark.parametrize(
    ("listed", "refused"),
    [
        pytest.param(
            {**HOST0, "vlan_id": 10},
            "network 0 of tenant it-one has vlan_id 10, but the host manages this cluster's networking, so a tenant network takes no VLAN. Remove vlan_id.",
            id="vlan",
        ),
        pytest.param(
            {"id": 0, "name": "Default", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["192.0.2.11-14"]},
            "network 0 of tenant it-one is STATIC, but the host manages this cluster's networking, so a tenant network is HOST. Give it type: HOST.",
            id="static",
        ),
    ],
)
@pytest.mark.parametrize("ignore_drift", [False, True], ids=["", "ignoring drift"])
def test_a_tenant_network_a_host_managed_edge_cannot_take_is_refused_before_the_destroy(fake_api, run_module, listed, refused, ignore_drift):
    fake_api().expect("GET", "/v3/network", response=HOSTED, etag='"n1"')

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "it-one", "previous_name": "Default", "networks": [listed]}, {"name": "it-second", "networks": []}],
            "network_declared": True,
            "ignore_drift": ignore_drift,
        },
    )

    assert result["failed"]
    assert result["msg"] == "the replay onto the rebuilt edge would refuse the inventory: " + refused


def test_the_host_managed_check_waits_for_the_replay_when_the_inventory_declares_managed_interfaces(fake_api, run_module):
    """The tenant 1 pass writes the declared interfaces, after which the baseline's step 2 makes no host-managed check."""
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)
    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "it-one", "previous_name": "Default", "networks": [{**HOST0, "vlan_id": 10}]}, {"name": "it-second", "networks": []}],
            "network": {"managed_interfaces": INTERFACES},
            "network_declared": True,
            "ignore_drift": True,
        },
    )

    assert "the host manages" not in result.get("msg", ""), result["msg"]


INTERFACES = {"frontend_bond_config": {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [0]}}


def test_declared_interfaces_no_pass_writes_leave_the_host_managed_check_before_the_destroy(fake_api, run_module):
    """With no tenant to create there is no pass, so the baseline's step 2 judges the formed, host-managed edge."""
    fake_api().expect("GET", "/v3/network", response=HOSTED, etag='"n1"')

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "it-one", "previous_name": "Default", "networks": [{**HOST0, "vlan_id": 10}]}],
            "network": {"managed_interfaces": INTERFACES},
            "network_declared": True,
        },
    )

    assert result["failed"]
    assert "network 0 of tenant it-one has vlan_id 10, but the host manages this cluster's networking" in result["msg"]


def test_declared_interfaces_do_not_lift_the_refusal_of_an_inventory_listing_no_network(fake_api, run_module):
    """No pass runs, so nothing writes the interfaces before the create."""
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "it-one", "previous_name": "Default", "networks": []}, {"name": "it-second", "networks": []}],
            "network": {"managed_interfaces": INTERFACES},
            "network_declared": True,
            "ignore_drift": True,
        },
    )

    assert result["failed"]
    assert result["msg"] == FIRST_NETWORK


# With two or more networks in tenant 1 after the pass, the baseline's step 2 cannot tell from the HOST networks which
# one the play reaches the edge on, so it needs qumulo_management_network. The pass writes two when tenant 1 holds two,
# or when tenant 1 holds none and the inventory lists two.
HOST1 = {"id": 1, "name": "second", "type": "HOST"}
TWO_HOSTED = [{"name": "it-one", "previous_name": "Default", "networks": [HOST0, HOST1]}, {"name": "it-second", "networks": []}]
# A second tenant with its own HOST network beside tenant 1's: the pass writes tenant 1's alone, and step 3 adds the other.
OWN_HOSTS = [{"name": "it-one", "previous_name": "Default", "networks": [HOST0]}, {"name": "it-second", "networks": [HOST1]}]


@pytest.mark.parametrize("management", [None, [0]], ids=["unset", "tenant 1's"])
def test_a_second_tenant_with_its_own_host_network_needs_no_management_network(fake_api, run_module, management):
    """The pass writes tenant 1's network alone, which step 2 places the play's address on, for a play on tenant 1's
    network."""
    expect_edge(fake_api(), tenants=TWO_HOST_TENANTS, network=TWO_HOSTS)

    result = run_module(
        edge_rebuild_preflight, {**AT_ADDRESS, "tenants": OWN_HOSTS, "network_declared": True, "ignore_drift": True, "management_network": management}
    )

    assert not result.get("failed"), result["msg"]
    assert result["management_network"] == management


def test_a_management_network_the_pass_leaves_to_step_3_is_refused(fake_api, run_module):
    """The baseline's step 2 judges the first create against the document the pass leaves, which lacks network 1 until
    step 3 adds it after the create."""
    expect_edge(fake_api(), tenants=TWO_HOST_TENANTS, network=TWO_HOSTS)

    result = run_module(edge_rebuild_preflight, {**AT_ADDRESS, "tenants": OWN_HOSTS, "network_declared": True, "ignore_drift": True, "management_network": [1]})

    assert result["failed"]
    assert result["msg"] == (
        "qumulo_management_network names network 1, but the replay's tenant 1 pass writes only network 0 before it creates "
        "tenant it-second, and the baseline's step 3 adds network 1 only after that create, so the baseline's step 2 would "
        "refuse the create after the destroy. Name the network of tenant 1 that qumulo_api_host is on, or, if the play "
        "reaches the edge on network 1, set qumulo_api_host to an address on network 0 for the rebuild and "
        "qumulo_management_network to that network's id"
    )


PLAY_ADDRESS = "192.0.2.11"
AT_ADDRESS = {**CONNECTION, "api_host": PLAY_ADDRESS}
MANAGEMENT_OPENING = (
    "the replay's tenant 1 pass would write networks 0 and 1 into tenant 1 on the newly formed edge before it creates "
    "tenant it-second, and with more than one HOST network the baseline's step 2 cannot tell which one this play reaches "
    "the edge on, nor can the preflight tell from this edge, since "
)
MANAGEMENT_CLOSING = ", so step 2 would refuse the create after the destroy. Set qumulo_management_network to the id of the network qumulo_api_host is on"


def network_status(*host_networks, host_addresses=(PLAY_ADDRESS,)):
    """/v3/network/status for one node whose one frontend device carries the HOST networks given and the host's addresses."""
    entries = [{"network_id": network_id, "assigned_by": "HOST", "netmask": "", "address": "", "floating_addresses": []} for network_id in host_networks]
    details = {"host_routable_ip_addresses": list(host_addresses), "qumulo_ip_addresses": entries, "use_for": "FRONTEND_AND_BACKEND"}
    return [{"node_id": 1, "node_name": "node1", "network_statuses": {}, "devices": [{"name": "eth0", "network_details": details}]}]


def host_network(network_id, name, tenant_id, floating=()):
    return {"id": network_id, "name": name, "tenant_id": tenant_id, "addresses": {"type": "HOST", "host_addresses": {"floating_ip_ranges": list(floating)}}}


TWO_HOSTS = {"managed_interfaces": None, "frontend_networks": [host_network(0, "Default", 1), host_network(1, "second", 2)]}
TWO_HOST_TENANTS = (tenant(1, "it-one", networks=[0]), tenant(2, "it-second", networks=[1]))


def test_the_management_network_is_inferred_from_the_network_the_play_reaches_this_edge_on(fake_api, run_module):
    """One HOST network on the old edge places the play's address; the inventory adds a second network to tenant 1, and
    lists network 0 under another name, which the rebuilt edge's host gives the same id."""
    fake = fake_api()
    expect_edge(fake, tenants=HOSTED_TENANTS, network=HOSTED)
    fake.expect("GET", "/v3/network/status", response=network_status(0))
    tenants = [{**TWO_HOSTED[0], "networks": [{**HOST0, "name": "eng"}, HOST1]}, TWO_HOSTED[1]]

    result = run_module(edge_rebuild_preflight, {**AT_ADDRESS, "tenants": tenants, "network_declared": True, "ignore_drift": True})

    assert not result.get("failed"), result["msg"]
    assert result["management_network"] == [0]


def test_a_floating_address_of_a_host_network_places_the_play_among_two(fake_api, run_module):
    fake = fake_api()
    floating = {**TWO_HOSTS, "frontend_networks": [host_network(0, "Default", 1), host_network(1, "second", 2, floating=["192.0.2.21"])]}
    expect_edge(fake, tenants=TWO_HOST_TENANTS, network=floating)
    fake.expect("GET", "/v3/network/status", response=network_status(0, 1, host_addresses=()))

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "api_host": "192.0.2.21", "tenants": TWO_HOSTED, "network_declared": True, "ignore_drift": True})

    assert not result.get("failed"), result["msg"]
    assert result["management_network"] == [1]


@pytest.mark.parametrize(
    ("document", "edge", "status", "why"),
    [
        pytest.param(
            TWO_HOSTS,
            TWO_HOST_TENANTS,
            {"response": network_status(0, 1)},
            "this edge holds HOST networks 0 and 1 and the play reaches it on the host's own address, 192.0.2.11, which the "
            "network status does not place on any one of them",
            id="two HOST networks",
        ),
        pytest.param(
            HOSTED,
            HOSTED_TENANTS,
            {"status": 500, "response": {"error_class": "net_read_error", "description": "net_read_error"}},
            "this edge does not answer its network status",
            id="status unavailable",
        ),
    ],
)
@pytest.mark.parametrize("ignore_drift", [False, True], ids=["", "ignoring drift"])
def test_a_pass_of_two_host_networks_needs_the_management_network_when_this_edge_cannot_tell(fake_api, run_module, document, edge, status, why, ignore_drift):
    fake = fake_api()
    expect_edge(fake, tenants=edge, network=document)
    fake.expect("GET", "/v3/network/status", **status)

    result = run_module(edge_rebuild_preflight, {**AT_ADDRESS, "tenants": TWO_HOSTED, "network_declared": True, "ignore_drift": ignore_drift})

    assert result["failed"]
    assert result["msg"].endswith(MANAGEMENT_OPENING + why + MANAGEMENT_CLOSING), result["msg"]
    assert result["management_network"] is None


@pytest.mark.parametrize(
    ("api_host", "status", "tenants", "why"),
    [
        pytest.param(
            PLAY_ADDRESS,
            {"response": network_status(host_addresses=(PLAY_ADDRESS,))},
            TWO_HOSTED,
            "this edge's network status shows network 0 served on no device",
            id="not served",
        ),
        pytest.param("unresolvable.invalid", {"response": network_status(0)}, TWO_HOSTED, "qumulo_api_host resolves to no address", id="no address"),
    ],
)
def test_the_inference_gives_up_where_step_2_could_not_trust_it(fake_api, run_module, monkeypatch, api_host, status, tenants, why):
    monkeypatch.setattr(edge_rebuild_continuity.socket, "getaddrinfo", lambda *args, **kwargs: [])
    fake = fake_api()
    expect_edge(fake, tenants=HOSTED_TENANTS, network=HOSTED)
    fake.expect("GET", "/v3/network/status", **status)

    result = run_module(edge_rebuild_preflight, {**CONNECTION, "api_host": api_host, "tenants": tenants, "network_declared": True, "ignore_drift": True})

    assert result["failed"]
    assert result["msg"] == MANAGEMENT_OPENING + why + MANAGEMENT_CLOSING, result["msg"]


def test_a_play_on_a_network_the_pass_does_not_write_is_told_to_reach_the_edge_on_a_written_network_or_give_its_id(fake_api, run_module):
    """The old edge places the play on network 0, which the pass does not write: the inventory lists it under no tenant
    that stays, or renumbered."""
    fake = fake_api()
    expect_edge(fake, tenants=HOSTED_TENANTS, network=HOSTED)
    fake.expect("GET", "/v3/network/status", response=network_status(0))
    tenants = [
        {"name": "it-one", "previous_name": "Default", "networks": [{"id": 5, "name": "five", "type": "HOST"}, HOST1]},
        {"name": "it-second", "networks": []},
    ]

    result = run_module(edge_rebuild_preflight, {**AT_ADDRESS, "tenants": tenants, "network_declared": True, "ignore_drift": True})

    assert result["failed"]
    assert result["msg"] == (
        "the play reaches this edge on network 0, which the replay's tenant 1 pass does not write before it creates tenant "
        "it-second, so the baseline's step 2 would refuse the create after the destroy. Set qumulo_api_host to an address "
        "on one of networks 1 and 5 for the rebuild and qumulo_management_network to that network's id, or, if the "
        "inventory lists network 0 under another id, set qumulo_management_network to that id"
    )


@pytest.mark.parametrize(
    ("status", "declared", "expected"),
    [
        pytest.param(
            {"status": 403, "response": {"error_class": "api_access_denied_error", "description": "denied"}},
            None,
            "this edge's network status read failed",
            id="status read error",
        ),
        pytest.param(None, [], "qumulo_management_network is declared with no network", id="declared empty"),
    ],
)
def test_a_status_read_error_and_an_empty_management_network_are_refused_before_the_destroy(fake_api, run_module, status, declared, expected):
    fake = fake_api()
    expect_edge(fake, tenants=HOSTED_TENANTS, network=HOSTED)
    if status:
        fake.expect("GET", "/v3/network/status", **status)

    result = run_module(
        edge_rebuild_preflight,
        {**AT_ADDRESS, "tenants": TWO_HOSTED, "network_declared": True, "ignore_drift": True, "management_network": declared},
    )

    assert result["failed"]
    assert expected in result["msg"], result["msg"]
    assert "Set qumulo_management_network to the id of the network qumulo_api_host is on" in result["msg"]


@pytest.mark.parametrize(
    "status",
    [
        pytest.param({"response": network_status(0)}, id="agrees"),
        pytest.param({"status": 500, "response": {"error_class": "net_read_error", "description": "net_read_error"}}, id="status unavailable"),
    ],
)
def test_a_declared_management_network_warns_only_when_the_inference_disagrees(fake_api, run_module, status):
    fake = fake_api()
    expect_edge(fake, tenants=HOSTED_TENANTS, network=HOSTED)
    fake.expect("GET", "/v3/network/status", **status)

    result = run_module(
        edge_rebuild_preflight, {**AT_ADDRESS, "tenants": TWO_HOSTED, "network_declared": True, "ignore_drift": True, "management_network": [0]}
    )

    assert not result.get("failed"), result["msg"]
    assert result["management_network"] == [0]
    assert not any("qumulo_management_network names" in warning for warning in result.get("warnings", []))


def test_a_declared_management_network_wins_over_the_inferred_one_with_a_warning(fake_api, run_module):
    fake = fake_api()
    expect_edge(fake, tenants=HOSTED_TENANTS, network=HOSTED)
    fake.expect("GET", "/v3/network/status", response=network_status(0))

    result = run_module(
        edge_rebuild_preflight, {**AT_ADDRESS, "tenants": TWO_HOSTED, "network_declared": True, "ignore_drift": True, "management_network": [1]}
    )

    assert not result.get("failed"), result["msg"]
    assert result["management_network"] == [1]
    assert any(
        "qumulo_management_network names network 1, but the play reaches the old edge on network 0; the replay uses qumulo_management_network" in w
        for w in result["warnings"]
    ), result["warnings"]


def test_on_managed_networking_no_management_network_is_inferred(fake_api, run_module):
    """No pass runs there, and step 2 places the play's address by the networks' ranges."""
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT, ENG), network=DHCP)

    result = run_module(edge_rebuild_preflight, {**AT_ADDRESS, "tenants": [{"name": "Default"}, {"name": "eng"}], "network_declared": True})

    assert not result.get("failed"), result["msg"]
    assert result["management_network"] is None


@pytest.mark.parametrize(
    ("tenants", "management", "writes"),
    [
        pytest.param(TWO_HOSTED, [2], "networks 0 and 1", id="two written"),
        pytest.param([{"name": "it-one", "previous_name": "Default", "networks": [HOST0]}, {"name": "it-second", "networks": []}], [1], "network 0", id="one"),
    ],
)
def test_a_management_network_no_tenant_lists_is_refused(fake_api, run_module, tenants, management, writes):
    """Step 2 would find the play's address outside every network it judges, which it refuses after the destroy."""
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)

    result = run_module(
        edge_rebuild_preflight, {**CONNECTION, "tenants": tenants, "network_declared": True, "ignore_drift": True, "management_network": management}
    )

    assert result["failed"]
    assert result["msg"] == (
        f"qumulo_management_network names network {management[0]}, which the inventory lists for no tenant that stays, "
        f"and the replay's tenant 1 pass writes only {writes} before it creates tenant it-second, so the baseline's step 2 "
        "could not find the network this play reaches the edge on and would refuse the create after the destroy. Set "
        "qumulo_management_network to the id the inventory gives the network qumulo_api_host is on"
    )


def test_a_tenant_network_on_a_vlan_passes_the_preflight_on_managed_networking(fake_api, run_module):
    expect_edge(fake_api(), tenants=(DEFAULT_TENANT,), network=DHCP)

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "tenants": [{"name": "Default", "networks": [{"id": 1, "name": "Default", "type": "DHCP", "vlan_id": 10}]}], "network_declared": True},
    )

    assert not result.get("failed"), result["msg"]


# With Terraform owning network, the baseline's ownership check, which predestroy runs before the preflight, refuses a
# network listed in qumulo_tenants or qumulo_network, so the refusal's remedy names the ownership too.
TERRAFORM_NETWORK = NO_NETWORK_OPENING + (
    "Terraform owns network by qumulo_terraform_owned_areas, whose ownership check refuses qumulo_network and a networks "
    "key under a tenant, so take network out of qumulo_terraform_owned_areas for the rebuild and list the edge's HOST "
    "network under a tenant in qumulo_tenants, or in qumulo_network.frontend_networks; when network goes back into "
    "qumulo_terraform_owned_areas, take that listing out in the same change"
)


def test_with_terraform_owning_network_the_refusal_says_to_take_network_from_terraform(fake_api, run_module):
    expect_edge(fake_api(), tenants=HOSTED_TENANTS, network=HOSTED)

    result = run_module(
        edge_rebuild_preflight,
        {**CONNECTION, "tenants": [{"name": "it-one", "previous_name": "Default"}, {"name": "it-second"}], "terraform_owned_areas": ["network"]},
    )

    assert result["failed"]
    assert result["msg"] == TERRAFORM_NETWORK


def test_with_terraform_owning_network_tenant_1_absent_is_refused_by_the_inventory_rules_first(fake_api, run_module):
    """The inventory lists no network, so the inventory rules refuse Default absent first, before any read."""
    fake_api()

    result = run_module(
        edge_rebuild_preflight,
        {
            **CONNECTION,
            "tenants": [{"name": "Default", "state": "absent"}, {"name": "it-one"}, {"name": "it-second"}],
            "factory_defaults": ABSENT_FACTORY,
            "terraform_owned_areas": ["network"],
        },
    )

    assert result["failed"]
    assert "tenant Default is state: absent but holds the newly formed edge's networks" in result["msg"]
