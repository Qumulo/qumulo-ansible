#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: snapshot_policy
short_description: Manage a snapshot policy on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Creates, changes or removes one snapshot policy, found by its name. The name is the key; the module looks the id up on
    every run and never keeps it.
  - Options left unset are not managed, so an existing policy keeps the values it has.
options:
  policy_name:
    description: The policy's name, at most 50 characters and without C(/). It is the key.
    type: str
    required: true
  state:
    description: Whether the policy exists. Removing a policy keeps the snapshots it took.
    type: str
    choices: [present, absent]
    default: present
  path:
    description:
      - Absolute path of the directory the policy snapshots. The directory must exist.
      - A policy cannot move to another directory, so a different path needs O(recreate=true). Required to create a policy.
      - The cluster refuses a policy on a directory inside a portal spoke's root, and the module fails with that
        reason (G-SPOKE-PATHS).
    type: str
  recreate:
    description:
      - Delete and recreate the policy when O(path) differs. The snapshots the policy took are kept.
      - The new policy keeps the lock key of the old one, unless O(lock_key) names another or V(""). A recreate that
        would carry a disabled key, or one the API user may not read, is refused before the delete.
    type: bool
    default: false
  enabled:
    description: Whether the policy takes snapshots. A policy created without it is enabled.
    type: bool
  lock_key:
    description:
      - The key that locks each snapshot the policy takes, by its name or id in the file system key store, as
        M(qumulo.cluster.snapshot_lock) takes it. V("") takes the key off. Left unset, the policy keeps its key.
      - Compared and returned by the key's name. A key that is not in the key store, or that is disabled, is refused
        before any write, in a check run too; a policy that already holds a key disabled since is no change.
      - Reading a key, to set it or to name the key a policy holds, needs C(PRIVILEGE_FS_KEY_MANAGEMENT_READ). Without it
        a held key is reported by its id, with a warning.
    type: str
  expiration_time_to_live:
    description:
      - How long the policy's snapshots are kept, such as V(7days), V(12hours), V(90minutes), V(2weeks) or V(6months);
        V(never) or an empty string keeps them until they are deleted. A policy created without it keeps them.
      - Compared as written, after V(never) becomes an empty string, as the cluster stores it.
      - A change applies only to snapshots taken after it; the earlier ones keep the expiration they were given (G-SNAP-TTL).
    type: str
  snapshot_name_template:
    description:
      - How the policy names its snapshots, such as V({ID}_{Policy}_{Directory}).
      - Left unset on a new policy, the cluster uses V({ID}_{Policy}) for C(/) and V({ID}_{Policy}_{Directory}) otherwise.
    type: str
  schedule:
    description:
      - When the policy takes snapshots, in the fields the cluster uses for that frequency; it refuses a field another
        frequency uses. Required to create a policy.
      - V(SCHEDULE_HOURLY_OR_LESS) takes O(schedule.fire_every), O(schedule.fire_every_interval), the window fields,
        O(schedule.on_days) and O(schedule.timezone). V(SCHEDULE_DAILY_OR_WEEKLY) takes O(schedule.hour),
        O(schedule.minute), O(schedule.on_days) and O(schedule.timezone). V(SCHEDULE_MONTHLY) takes
        O(schedule.day_of_month), O(schedule.hour), O(schedule.minute) and O(schedule.timezone).
      - Only the fields the task gives are compared, so a field the cluster filled in is not a difference.
      - A schedule the cluster receives restarts the policy's timing, so the module sends the schedule only when it
        differs (QUIRK-006).
    type: dict
    suboptions:
      frequency:
        description: How often snapshots are taken.
        type: str
        choices: [SCHEDULE_HOURLY_OR_LESS, SCHEDULE_DAILY_OR_WEEKLY, SCHEDULE_MONTHLY]
        required: true
      timezone:
        description: The time zone the times are in, such as V(UTC) or V(America/Los_Angeles).
        type: str
      on_days:
        description: The days snapshots are taken on, compared as a set. V(EVERY_DAY) is all seven.
        type: list
        elements: str
        choices: [SUN, MON, TUE, WED, THU, FRI, SAT, EVERY_DAY]
      hour:
        description: The hour, 0 to 23.
        type: int
      minute:
        description: The minute, 0 to 59.
        type: int
      day_of_month:
        description: The day of the month, 1 to 31; 128 is the last day of the month.
        type: int
      window_start_hour:
        description: The hour the window of hourly snapshots opens.
        type: int
      window_start_minute:
        description: The minute the window of hourly snapshots opens.
        type: int
      window_end_hour:
        description: The hour the window of hourly snapshots closes.
        type: int
      window_end_minute:
        description: The minute the window of hourly snapshots closes.
        type: int
      fire_every:
        description: How many O(schedule.fire_every_interval) units apart hourly snapshots are.
        type: int
      fire_every_interval:
        description: The unit of O(schedule.fire_every).
        type: str
        choices: [FIRE_IN_MINUTES, FIRE_IN_HOURS]
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
  diff_mode:
    support: full
  idempotent:
    support: full
notes:
  - "API: GET /v3/snapshots/policies/status/, GET /v1/files/{path}/info/attributes, GET /v1/file-system/security/keys/{key},
    POST /v3/snapshots/policies/, GET, PATCH (If-Match) and DELETE /v3/snapshots/policies/{id}."
  - The cluster refuses to delete a policy a replication relationship uses (G-SNAP-TTL); the module fails with the
    cluster's reason.
  - The DELETE carries no If-Match (QUIRK-008).
  - "Registers: G-SNAP-TTL, G-SPOKE-PATHS."
seealso:
  - module: qumulo.cluster.snapshot_info
    description: Reads the policies in the option shape of this module.
  - module: qumulo.cluster.snapshot
    description: Takes a snapshot on demand.
  - module: qumulo.cluster.security_key
    description: Registers the key O(lock_key) names.
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Snapshot the project tree every hour on weekdays, kept for a week
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    path: /data/projects
    expiration_time_to_live: 7days
    schedule:
      frequency: SCHEDULE_HOURLY_OR_LESS
      fire_every: 1
      fire_every_interval: FIRE_IN_HOURS
      window_start_hour: 0
      window_start_minute: 0
      window_end_hour: 23
      window_end_minute: 59
      on_days: [MON, TUE, WED, THU, FRI]
      timezone: UTC

- name: Take the daily snapshots at 02:00 on weekdays
  qumulo.cluster.snapshot_policy:
    policy_name: projects-daily
    path: /data/projects
    expiration_time_to_live: 30days
    schedule:
      frequency: SCHEDULE_DAILY_OR_WEEKLY
      hour: 2
      minute: 0
      on_days: [MON, TUE, WED, THU, FRI]
      timezone: America/Los_Angeles

- name: Take a snapshot of the archive tree on the last day of each month
  qumulo.cluster.snapshot_policy:
    policy_name: archive-monthly
    path: /data/archive
    expiration_time_to_live: 6months
    schedule:
      frequency: SCHEDULE_MONTHLY
      day_of_month: 128
      hour: 1
      minute: 0
      timezone: UTC

- name: Take the hourly snapshots every day, not only on weekdays
  # The policy exists already, so path is not needed. A changed schedule restarts the policy's timing (QUIRK-006).
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    schedule:
      frequency: SCHEDULE_HOURLY_OR_LESS
      fire_every: 1
      fire_every_interval: FIRE_IN_HOURS
      window_start_hour: 0
      window_start_minute: 0
      window_end_hour: 23
      window_end_minute: 59
      on_days: [EVERY_DAY]
      timezone: UTC

- name: Keep the hourly snapshots for two weeks
  # Snapshots already taken keep the expiration they were given (G-SNAP-TTL).
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    expiration_time_to_live: 2weeks

- name: Preview a new schedule for the policy
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    path: /data/projects
    schedule: "{{ projects_snapshot_schedule }}"
  check_mode: true
  diff: true
  register: policy_preview

- name: Show the schedule change, which restarts its timing
  ansible.builtin.debug:
    msg: >-
      {{ policy_preview.diff.before.schedule | default('no policy') }}
      -> {{ policy_preview.snapshot_policy.schedule }}
  when: policy_preview.diff.before.schedule | default(none) != policy_preview.snapshot_policy.schedule

- name: Pause the policy
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    enabled: false

- name: Resume a paused policy
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    enabled: true

- name: Lock each snapshot the daily policy takes with the legal-hold key
  # Register the key first with qumulo.cluster.security_key; the task refuses a key that is not in the key store, or that
  # is disabled, before it writes. Snapshots the policy took earlier are not changed. A second run reports no change.
  qumulo.cluster.snapshot_policy:
    policy_name: projects-daily
    lock_key: legal-hold

- name: Preview taking the lock key off the daily policy
  # An empty string takes the key off. Left unset, the policy keeps the key it has.
  qumulo.cluster.snapshot_policy:
    policy_name: projects-daily
    lock_key: ""
  check_mode: true
  diff: true

- name: Take the lock key off the daily policy
  # Take the key off before you disable or remove it with qumulo.cluster.security_key; the cluster refuses either while
  # a policy uses the key. A second run reports no change.
  qumulo.cluster.snapshot_policy:
    policy_name: projects-daily
    lock_key: ""

- name: Point the policy at the archive tree, deleting and recreating it
  # A policy cannot move in place. The snapshots it took are kept, and the new policy keeps the lock key. The cluster
  # refuses a policy on a directory inside a portal spoke's root (G-SPOKE-PATHS).
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    path: /data/archive
    recreate: true

- name: Remove the policy, keeping the snapshots it took
  # The cluster refuses while a replication relationship uses the policy.
  qumulo.cluster.snapshot_policy:
    policy_name: projects-hourly
    state: absent
"""

RETURN = r"""
snapshot_policy:
  description: The policy after the task, with the option names above; an empty dict after O(state=absent).
  returned: always
  type: dict
  contains:
    policy_name:
      description: The policy's name.
      type: str
      sample: projects-hourly
    path:
      description: The directory the policy snapshots, without a trailing slash.
      type: str
      sample: /data/projects
    enabled:
      description: Whether the policy takes snapshots.
      type: bool
      sample: true
    expiration_time_to_live:
      description: How long the policy's snapshots are kept, as the cluster stores it; an empty string for never.
      type: str
      sample: 7days
    snapshot_name_template:
      description:
        - How the policy names its snapshots.
        - Absent in check mode when the task would create the policy without O(snapshot_name_template).
      type: str
      sample: "{ID}_{Policy}_{Directory}"
    lock_key:
      description:
        - The name of the key that locks each snapshot the policy takes, or null for none.
        - The key's id when the API user may not read the key store, with a warning, or when the key has no name
          (QUIRK-120).
      type: str
      sample: legal-hold
    schedule:
      description:
        - When the policy takes snapshots. It holds the fields of O(schedule) that the policy's frequency uses, and in
          check mode only the fields the task gave.
        - C(on_days) is always the days in the order SUN to SAT, so V(EVERY_DAY) reads back as all seven.
      type: dict
      contains:
        frequency:
          description: How often snapshots are taken.
          type: str
          sample: SCHEDULE_HOURLY_OR_LESS
        timezone:
          description: The time zone the times are in.
          type: str
          sample: UTC
        on_days:
          description: The days snapshots are taken on.
          type: list
          elements: str
          sample: [MON, TUE, WED, THU, FRI]
        hour:
          description: The hour, for the daily or weekly and monthly frequencies.
          type: int
          sample: 2
        minute:
          description: The minute, for the daily or weekly and monthly frequencies.
          type: int
          sample: 0
        day_of_month:
          description: The day of the month, for the monthly frequency; 128 is the last day.
          type: int
          sample: 128
        window_start_hour:
          description: The hour the window of hourly snapshots opens.
          type: int
          sample: 0
        window_start_minute:
          description: The minute the window of hourly snapshots opens.
          type: int
          sample: 0
        window_end_hour:
          description: The hour the window of hourly snapshots closes.
          type: int
          sample: 23
        window_end_minute:
          description: The minute the window of hourly snapshots closes.
          type: int
          sample: 59
        fire_every:
          description: How many C(fire_every_interval) units apart hourly snapshots are.
          type: int
          sample: 1
        fire_every_interval:
          description: The unit of C(fire_every).
          type: str
          sample: FIRE_IN_HOURS
notes:
  description: What the module noticed, such as a write it confirmed by reading back after the connection was lost.
  returned: when there is something to note
  type: list
  elements: str
  sample: [write reconciled after a lost connection]
maybe_applied:
  description: True when a write was sent and the read-back did not confirm it, as after a lost connection.
  returned: on such a failure
  type: bool
  sample: true
expected:
  description: The values the module wrote, for the options that read back differently.
  returned: when the cluster did not converge on what was written
  type: dict
observed:
  description: The values the cluster holds for the same options.
  returned: when the cluster did not converge on what was written
  type: dict
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.snapshots import SnapshotPolicy

DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
ARGUMENT_SPEC = {
    "policy_name": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
    "path": {"type": "str"},
    "recreate": {"type": "bool", "default": False},
    "enabled": {"type": "bool"},
    "expiration_time_to_live": {"type": "str"},
    "snapshot_name_template": {"type": "str"},
    "lock_key": {"type": "str", "no_log": False},
    "schedule": {
        "type": "dict",
        "options": {
            "frequency": {"type": "str", "choices": ["SCHEDULE_HOURLY_OR_LESS", "SCHEDULE_DAILY_OR_WEEKLY", "SCHEDULE_MONTHLY"], "required": True},
            "timezone": {"type": "str"},
            "on_days": {"type": "list", "elements": "str", "choices": [*DAYS, "EVERY_DAY"]},
            "hour": {"type": "int"},
            "minute": {"type": "int"},
            "day_of_month": {"type": "int"},
            "window_start_hour": {"type": "int"},
            "window_start_minute": {"type": "int"},
            "window_end_hour": {"type": "int"},
            "window_end_minute": {"type": "int"},
            "fire_every": {"type": "int"},
            "fire_every_interval": {"type": "str", "choices": ["FIRE_IN_MINUTES", "FIRE_IN_HOURS"]},
        },
    },
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), SnapshotPolicy)


if __name__ == "__main__":
    main()
