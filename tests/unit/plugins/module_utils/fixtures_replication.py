# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Status records of one relationship on each side, as Qumulo Core 7.10.3A returns them, for the replication tests.

SOURCE_STATUS is the source's ESTABLISHED record; TARGET_STATUS is the target's record awaiting authorization, whose
target_root_path reads back as sent until the source first polls. snapshot() builds a snapshot their relationships own.
"""

from __future__ import annotations

SOURCE_UUID = "9533a9e2-e57a-454f-8aa0-9c77b706b7bd"
TARGET_UUID = "59052546-452f-483e-a800-0a925d3fa1bd"
ADDRESS = "203.0.113.40"
REL_ID = "9465828e-7dcf-466e-a550-78ed0bd6b79b"
WINDOW = {"start_hour": 8, "start_minute": 0, "end_hour": 18, "end_minute": 0, "on_days": ["FRI", "MON"]}

SOURCE_STATUS = {
    "id": REL_ID,
    "target_address": ADDRESS,
    "target_port": 3712,
    "source_root_read_only": False,
    "map_local_ids_to_nfs_ids": False,
    "replication_enabled": True,
    "replication_mode": "REPLICATION_CONTINUOUS",
    "blackout_window_timezone": "America/Los_Angeles",
    "blackout_windows": [WINDOW],
    "snapshot_policies": [],
    "state": "ESTABLISHED",
    "end_reason": "",
    "source_cluster_name": "edge01",
    "source_cluster_uuid": SOURCE_UUID.upper(),
    "source_root_path": "/src/",
    "target_cluster_name": "dr01",
    "target_cluster_uuid": TARGET_UUID,
    "target_root_path": "/dst/",
    "target_root_read_only": True,
    "job_state": "REPLICATION_NOT_RUNNING",
    "job_start_time": "",
    "recovery_point": "2026-11-01T08:21:54.728656093Z",
    "error_from_last_job": "",
    "duration_of_last_job": {"nanoseconds": "202844024"},
    "source_root_id": "106505742056649366299408859139",
    "next_active_time": "",
    "replication_job_status": None,
    "queued_snapshot_count": 0,
    "recovery_point_snapshot": None,
    "replicating_snapshot": None,
}
TARGET_STATUS = {
    "id": REL_ID,
    "state": "AWAITING_AUTHORIZATION",
    "end_reason": "",
    "source_cluster_name": "edge01",
    "source_cluster_uuid": SOURCE_UUID,
    "source_root_path": "/src/",
    "source_root_read_only": False,
    "source_address": None,
    "source_port": None,
    "target_cluster_name": "dr01",
    "target_cluster_uuid": TARGET_UUID,
    "target_root_path": "/dst",
    "target_root_read_only": False,
    "job_state": "REPLICATION_NOT_RUNNING",
    "job_start_time": "",
    "recovery_point": "",
    "error_from_last_job": "",
    "duration_of_last_job": None,
    "target_root_id": None,
    "replication_enabled": False,
    "replication_job_status": None,
    "recovery_point_snapshot": None,
    "lock_key": None,
}


def snapshot(snapshot_id, name, *, owners=(), path="/dst/", in_delete=False):
    """A snapshot as GET /v4/snapshots/status/ lists it; owners are relationship ids."""
    return {
        "id": snapshot_id,
        "name": name,
        "source_file_path": path,
        "owners": [{"id": owner} for owner in owners],
        "policy_id": None,
        "in_delete": in_delete,
    }
