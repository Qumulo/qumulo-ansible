# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.cluster_info, driven through FakeQumulo. Response bodies follow the public qumulo-api SDK."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ansible_collections.qumulo.cluster.plugins.modules import cluster_info
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
TIB = 1 << 40
METRICS = "/v2/metrics/endpoints/default/data"

# As Qumulo Core 7.10.3A answers: the license document itself, as Nexus issued it, with its key.
LICENSE_ACTIVE = {
    "state": "LICENSE_STATE_ACTIVE",
    "license": {
        "cluster_uuid": CLUSTER_ID,
        "details": {"per_node_throughput_cap": None, "usable_capacity_cap": str(2 * TIB), "raw_capacity_cap": None, "object_bridges": "OFF"},
        "expiration_date": "2027-01-01T00:00:00Z",
        "earliest_expiration_date": "2027-01-01T00:00:00Z",
        "license_key": "not-a-real-key",
    },
}
FILE_SYSTEM = {
    "block_size_bytes": 4096,
    "total_size_bytes": "1000",
    "free_size_bytes": "600",
    "snapshot_size_bytes": "0",
    "portal_cache_size_bytes": "100",
}
FILE_SYSTEM_FACTS = {"block_size_bytes": 4096, "total_size_bytes": 1000, "free_size_bytes": 600, "snapshot_size_bytes": 0, "portal_cache_size_bytes": 100}


def metrics(**labels):
    """An OpenMetrics page whose qumulo_info sample carries labels; no labels, no qumulo_info sample."""
    rendered = ",".join(f'{name}="{value}"' for name, value in labels.items())
    info = f"# HELP qumulo_info Information about the cluster.\n# TYPE qumulo_info gauge\nqumulo_info{{{rendered}}} 1\n" if labels else ""
    return (info + "# TYPE qumulo_fs_free_bytes gauge\nqumulo_fs_free_bytes 1234\n# EOF\n").encode()


EDGE_METRICS = metrics(product_intent="edge_accelerator", cluster_type="unprotected_edge")


def error(error_class):
    return {"error_class": error_class, "description": "test"}


def expect_defaults(fake):
    fake.expect("GET", "/v1/cluster/settings", response={"cluster_name": "edge01"}, etag='"1"')
    fake.expect("GET", "/v1/license/status", response=LICENSE_ACTIVE)
    fake.expect("GET", "/v1/license/enforcement", response={"license_is_enforced": True})
    fake.expect("GET", "/v1/capacity/clamp", response={"capacity_clamp": str(TIB)})
    fake.expect("GET", METRICS, response=EDGE_METRICS)
    fake.expect("GET", "/v1/file-system", response=FILE_SYSTEM)


def test_default_subsets(fake_api, run_module):
    fake = fake_api(version="7.10.3")
    expect_defaults(fake)

    result = run_module(cluster_info, CONNECTION)

    assert result == {
        "changed": False,
        "cluster": {
            "cluster_id": CLUSTER_ID,
            "state": "ACTIVE",
            "version": "7.10.3",
            "build_id": "build-7.10.3",
            "cluster_name": "edge01",
            "license": {
                "state": "LICENSE_STATE_ACTIVE",
                "cluster_uuid": CLUSTER_ID,
                "usable_capacity_cap": 2 * TIB,
                "expiration_date": "2027-01-01T00:00:00Z",
                "enforced": True,
            },
            "capacity_clamp": TIB,
            "product": {"product_intent": "edge_accelerator", "cluster_type": "unprotected_edge", "unprotected_edge": True},
            "filesystem": FILE_SYSTEM_FACTS,
        },
        "warnings": [],
    }


def test_unconfigured_node_returns_node_and_version(fake_api, run_module):
    fake = fake_api(cluster_id="", state="UNCONFIGURED")  # the script is empty: any authenticated GET fails the test

    result = run_module(cluster_info, CONNECTION)

    assert "failed" not in result
    assert result["cluster"] == {"cluster_id": None, "state": "UNCONFIGURED", "version": "7.10.3", "build_id": "build-7.10.3"}
    assert sorted(result["unavailable_subsets"]) == ["capacity", "filesystem", "license", "product", "settings"]
    assert len([w for w in result["warnings"] if "UNCONFIGURED" in w]) == 1
    assert fake.logins == 0


def test_product_from_metric_labels(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", METRICS, response=metrics(product_intent="default", cluster_type="protected_cluster"))
    fake.expect("GET", METRICS, response=metrics())

    labelled = run_module(cluster_info, {**CONNECTION, "gather_subset": ["product"]})
    unlabelled = run_module(cluster_info, {**CONNECTION, "gather_subset": ["product"]})

    assert labelled["cluster"] == {"product": {"product_intent": "default", "cluster_type": "protected_cluster", "unprotected_edge": False}}
    assert unlabelled["cluster"] == {"product": {"product_intent": "unknown", "cluster_type": "unknown", "unprotected_edge": None}}


def test_metrics_are_requested_as_text(fake_api, run_module):
    """The metrics endpoint offers no JSON: the server answers 406 to Accept: application/json."""
    fake = fake_api()
    fake.expect("GET", METRICS, response=EDGE_METRICS)

    run_module(cluster_info, {**CONNECTION, "gather_subset": ["product"]})

    assert [e.headers["Accept"] for e in fake.log if e.path == METRICS] == ["text/plain"]


@pytest.mark.parametrize("status", [404, 406])
def test_metrics_not_offered_make_product_unavailable(fake_api, run_module, status):
    fake = fake_api()
    fake.expect("GET", METRICS, status=status, response=error("http_not_found_error" if status == 404 else "http_not_acceptable"))

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["product"]})

    assert result["cluster"] == {}
    assert f"HTTP {status}" in result["unavailable_subsets"]["product"]


def test_metrics_permission_denied_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", METRICS, status=403, response=error("http_forbidden"))

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["product"]})

    assert result["failed"] is True
    assert "HTTP 403" in result["msg"]
    assert "RBAC privilege" in result["msg"]


def test_license_cap_reported(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/license/status", response=LICENSE_ACTIVE)
    fake.expect("GET", "/v1/license/enforcement", response={"license_is_enforced": False})
    fake.expect("GET", "/v1/license/status", response={"state": "LICENSE_STATE_NONE"})
    fake.expect("GET", "/v1/license/enforcement", response={"license_is_enforced": False})

    active = run_module(cluster_info, {**CONNECTION, "gather_subset": ["license"]})
    none = run_module(cluster_info, {**CONNECTION, "gather_subset": ["license"]})

    assert active["cluster"]["license"]["usable_capacity_cap"] == 2 * TIB
    assert active["cluster"]["license"]["cluster_uuid"] == CLUSTER_ID
    assert active["cluster"]["license"]["expiration_date"] == "2027-01-01T00:00:00Z"
    assert active["cluster"]["license"]["enforced"] is False
    assert "not-a-real-key" not in str(active)
    assert none["cluster"]["license"] == {
        "state": "LICENSE_STATE_NONE",
        "cluster_uuid": None,
        "usable_capacity_cap": None,
        "expiration_date": None,
        "enforced": False,
    }


def test_license_enforcement_route_missing_is_null(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/license/status", response=LICENSE_ACTIVE)
    fake.expect("GET", "/v1/license/enforcement", status=404, response=error("http_not_found_error"))

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["license"]})

    assert result["cluster"]["license"]["enforced"] is None
    assert "unavailable_subsets" not in result


def test_capacity_clamp_unset_is_null(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/capacity/clamp", response={"capacity_clamp": None})

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["capacity"]})

    assert result["cluster"] == {"capacity_clamp": None}


def test_subset_unavailable_is_recorded_not_fatal(fake_api, run_module):
    fake = fake_api()
    expect_defaults(fake)
    fake.expect("GET", "/v1/cluster/nodes/", response=[{"id": 1, "node_name": "edge01-1", "uuid": "n1", "model_number": "Q0", "node_status": "online"}])
    fake.expect("GET", "/v2/portal/spokes/", status=404, response=error("http_not_found_error"))
    fake.expect("GET", "/v1/cluster/protection/status", response={"protection_system_type": "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "blocks_left": 3})
    fake.expect("GET", "/v3/upgrade/status", status=404, response=error("http_not_found_error"))
    fake.expect("GET", "/v2/upgrade/blocked", status=404, response=error("http_not_found_error"))

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["all"]})

    assert "failed" not in result
    assert list(result["unavailable_subsets"]) == ["portals", "upgrade", "upgrade_blocked"]
    assert "HTTP 404" in result["unavailable_subsets"]["portals"]
    facts = result["cluster"]
    assert "portals" not in facts
    assert facts["node_count"] == 1
    assert facts["nodes"][0]["node_name"] == "edge01-1"
    assert facts["filesystem"] == FILE_SYSTEM_FACTS
    assert facts["protection"] == {"protection_system_type": "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED"}


def test_protection_of_an_erasure_coded_cluster_is_returned_whole(fake_api, run_module):
    status = {"protection_system_type": "PROTECTION_SYSTEM_TYPE_EC", "blocks_left_before_degraded": 2, "max_drive_failures": 2}
    fake = fake_api()
    fake.expect("GET", "/v1/cluster/protection/status", response=status)

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["protection"]})

    assert result["cluster"] == {"protection": status}


def test_portal_counts(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v2/portal/spokes/", response={"entries": [{"id": 1, "state": "ACTIVE"}]})
    fake.expect("GET", "/v2/portal/hubs/", response={"entries": []})

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["portals"]})

    assert result["cluster"] == {"portals": {"spokes": 1, "hubs": 0}}


def test_permission_denied_fails(fake_api, run_module):
    fake = fake_api()
    fake.expect("GET", "/v1/cluster/settings", status=403, response=error("http_forbidden"))

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["settings"]})

    assert result["failed"] is True
    assert "RBAC privilege" in result["msg"]


# The upgrade subset (specs/017-cluster-upgrade, T1706a), from T1700p's recorded answers of a 7.10.3A edge.

EDGE_RECORDING = Path(__file__).resolve().parents[2] / "fixtures" / "upgrade" / "edge-7.10.3A.json"


def recorded(path):
    """The edge's first recorded answer to GET path."""
    exchanges = json.loads(EDGE_RECORDING.read_text())["exchanges"]
    return next(e["response"] for e in exchanges if (e["method"], e["path"]) == ("GET", path) and e["status"] == 200)


def expect_upgrade_reads(fake, status=None, protection=None, nodes=None):
    fake.expect("GET", "/v3/upgrade/status", response=status or recorded("/v3/upgrade/status"))
    fake.expect("GET", "/v2/upgrade/blocked", response=recorded("/v2/upgrade/blocked"))
    fake.expect("GET", "/v1/shutdown/reboot/status", response=recorded("/v1/shutdown/reboot/status"))
    fake.expect("GET", "/v1/cluster/protection/status", response=protection or recorded("/v1/cluster/protection/status"))
    if nodes is not None:
        fake.expect("GET", "/v1/cluster/nodes/", response=nodes)


def test_the_upgrade_subset_of_an_edge(fake_api, run_module):
    expect_upgrade_reads(fake_api())

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade"]})

    assert result["cluster"] == {
        "upgrade": {
            "status": {"state": "UPGRADE_STATE_IDLE", "progress": None, "settings": None, "error_info": None},
            "blocked": {"is_blocked": False, "cluster_degraded": False, "blocked_reason": ""},
            "reboot": {
                "state": "REBOOT_IDLE",
                "rolling": False,
                "upgrade_initiated": False,
                "total_node_count": 0,
                "rebooted_node_count": 0,
                "error_message": None,
            },
            "protection": {"type": "PROTECTION_SYSTEM_TYPE_EC", "configured_node_failures": 0, "remaining_node_failures": 0},
        }
    }


def test_the_upgrade_subset_redacts_the_statuss_urls(fake_api, run_module):
    url = "https://u:p@mirror.example.com/qumulo_core_7.11.0.qimg?X-Amz-Signature=abc"
    status = {
        "state": "UPGRADE_STATE_IDLE",
        "progress": None,
        "settings": {"install_path": url, "target_version": "7.11.0", "upgrade_type": "SOFTWARE_ONLY", "auto_commit": True},
        "error_info": f"Qimg Download from: {url} failed",
    }
    expect_upgrade_reads(fake_api(), status=status)

    found = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade"]})["cluster"]["upgrade"]["status"]

    assert found["settings"]["install_path"] == "https://mirror.example.com/qumulo_core_7.11.0.qimg"
    assert found["error_info"] == "Qimg Download from: https://mirror.example.com/qumulo_core_7.11.0.qimg failed"


@pytest.mark.parametrize(
    ("url", "redacted"),
    [
        ("https://mirror.example.com/qumulo_core_7.11.0.qimg?sig=ab,cd", "https://mirror.example.com/qumulo_core_7.11.0.qimg"),
        ("https://user:p(w)@mirror.example.com/qumulo_core_7.11.0.qimg", "https://mirror.example.com/qumulo_core_7.11.0.qimg"),
        ("https://[::1]:8443/qumulo_core_7.11.0.qimg?q=1", "https://[::1]:8443/qumulo_core_7.11.0.qimg"),
    ],
)
def test_the_upgrade_subset_redacts_a_url_whatever_its_secret_or_host_holds(fake_api, run_module, url, redacted):
    status = {"state": "UPGRADE_STATE_IDLE", "progress": None, "settings": {"install_path": url}, "error_info": f"exec([ verify.py, --path, {url} ])"}
    expect_upgrade_reads(fake_api(), status=status)

    found = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade"]})["cluster"]["upgrade"]["status"]

    assert found["settings"]["install_path"] == redacted
    assert found["error_info"] == f"exec([ verify.py, --path, {redacted} ])"


@pytest.mark.parametrize(("max_node_failures", "remaining"), [(2, 1), (1, 0)])
def test_an_erasure_coded_clusters_configured_tolerance_is_max_node_failures(fake_api, run_module, max_node_failures, remaining):
    protection = {"protection_system_type": "PROTECTION_SYSTEM_TYPE_EC", "max_node_failures": max_node_failures, "remaining_node_failures": remaining}
    expect_upgrade_reads(fake_api(), protection=protection)

    found = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade"]})["cluster"]["upgrade"]["protection"]

    assert found == {"type": "PROTECTION_SYSTEM_TYPE_EC", "configured_node_failures": max_node_failures, "remaining_node_failures": remaining}


@pytest.mark.parametrize(("nodes", "configured"), [(2, 0), (3, 1), (6, 1)])
def test_an_object_backed_clusters_configured_tolerance_is_the_node_count_rule(fake_api, run_module, nodes, configured):
    # max_node_failures describes a stripe an object-backed cluster does not use, so it is never the answer.
    protection = {"protection_system_type": "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "max_node_failures": 2, "remaining_node_failures": configured}
    expect_upgrade_reads(fake_api(), protection=protection, nodes=[{"id": n + 1, "node_status": "online"} for n in range(nodes)])

    found = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade"]})["cluster"]["upgrade"]["protection"]

    assert found == {"type": "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED", "configured_node_failures": configured, "remaining_node_failures": configured}


def test_the_upgrade_subset_is_unavailable_where_the_status_route_is_missing(fake_api, run_module):
    fake_api().expect("GET", "/v3/upgrade/status", status=404, response={"error_class": "http_not_found_error", "description": "GET /v3/upgrade/status"})

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade"]})

    assert result["cluster"] == {}
    assert "upgrade" in result["unavailable_subsets"]


# The upgrade_blocked subset: the preflight alone, which the cluster_upgrade role polls outside a reboot (FR-025a).


def test_the_upgrade_blocked_subset_reads_only_the_preflight(fake_api, run_module):
    fake_api().expect("GET", "/v2/upgrade/blocked", response=recorded("/v2/upgrade/blocked"))

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade_blocked"]})

    assert result["cluster"] == {"upgrade_blocked": {"is_blocked": False, "cluster_degraded": False, "blocked_reason": ""}}


def test_the_upgrade_blocked_subset_passes_a_blocked_preflight_on(fake_api, run_module):
    blocked = {"is_blocked": True, "cluster_degraded": True, "blocked_reason": "Node 2 is offline."}
    fake_api().expect("GET", "/v2/upgrade/blocked", response=blocked)

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade_blocked"]})

    assert result["cluster"] == {"upgrade_blocked": blocked}


def test_the_upgrade_blocked_subset_is_unavailable_where_the_preflight_route_is_missing(fake_api, run_module):
    fake_api().expect("GET", "/v2/upgrade/blocked", status=404, response={"error_class": "http_not_found_error", "description": "GET /v2/upgrade/blocked"})

    result = run_module(cluster_info, {**CONNECTION, "gather_subset": ["upgrade_blocked"]})

    assert result["cluster"] == {}
    assert "HTTP 404" in result["unavailable_subsets"]["upgrade_blocked"]
