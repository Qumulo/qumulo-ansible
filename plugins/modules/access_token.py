#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: access_token
short_description: Manage the access tokens of a user on a Qumulo cluster
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Makes sure a user holds an access token, rotates the user's tokens, or removes them.
  - Tokens are matched by O(user) alone. The cluster stores no name or description for a token, so the module manages
    the tokens of a user as one set (G-TOKEN).
  - The cluster returns a token's bearer only in its answer to the request that makes the token. The module writes it at
    once to a file of its own in O(secret_destination), readable only by its owner, and returns it in RV(bearer_token)
    of that run only. No other key, diff, note, warning or message holds it.
  - The module refuses to make a token in a task that does not set C(no_log), or that has no O(secret_destination). It
    refuses before any write, in check mode too.
options:
  user:
    description:
      - The user that holds the tokens, as a trustee such as V(local:svc-backup) or V(ad:CORP\\svc-backup). A bare name is
        refused, and the cluster refuses a group.
      - The user must exist. In check mode, a user that does not exist yet because an earlier task creates it is reported
        as a new token. With O(state=absent), a user that does not exist holds no token, and the task changes nothing.
    type: str
    required: true
  state:
    description:
      - V(present) makes a token when the user holds none, and otherwise makes none. O(enabled) and O(expiration_time)
        apply to each token the user holds, and to a token the task makes.
      - V(rotated) makes a new token, writes its bearer to its file, confirms the token in a listing, and then removes
        every token the user held before. It reports a change on every run. O(enabled) and O(expiration_time) apply to
        the new token. A user holds at most two tokens, so a user who holds two is refused before any write.
      - V(rotated) removes the old tokens at the end of the task, so every other holder of the user's token loses access
        until it holds the new bearer (G-TOKEN).
      - V(absent) removes every token of the user. A removed token stops working at once.
    type: str
    choices: [present, absent, rotated]
    default: present
  enabled:
    description:
      - Whether the user's tokens can log in. Unset leaves each token as it is; a token the task makes is enabled.
      - Ignored with O(state=absent).
    type: bool
  expiration_time:
    description:
      - When the user's tokens stop working, as an RFC 3339 time with a time zone offset or V(Z), such as
        V(2027-06-01T00:00:00Z), or V(never) for no expiration. Unset leaves each token as it is; a token the task makes
        then has none.
      - The cluster reads a time back in UTC, so times are compared as instants, not as text.
      - A time at or before now is refused for a token the task would make.
      - An unquoted time in YAML reaches the module in ISO 8601 form, with V(+00:00) for V(Z) and a fraction cut to
        microseconds, which the module accepts.
      - The module sends the instant in UTC with V(Z), whatever form the task gave.
      - Ignored with O(state=absent).
    type: str
  secret_destination:
    description:
      - The directory on the controller where the module writes the bearer of a token it makes, in
        C(access-token-<user>-<id>.yml), readable only by its owner, where <user> is the user as RV(access_tokens) names
        it, with each run of characters other than letters, digits, V(.), V(_) and V(-) replaced by one V(_). The file
        holds C(user), C(access_token_id) and C(bearer_token). It is flushed to disk before its name appears, and never
        replaces another file.
      - The module removes no bearer's file except the one it wrote in the same run for a token that the listing after
        the create did not show, which it deletes with the token. The file of a token that a later task deletes stays,
        and its bearer no longer works; the newest file of a user holds the bearer of the token the user was last given.
      - Before a token is made, the module makes the directory, or makes the one there readable only by its owner, and
        writes a file in it and removes it; if it cannot, the task fails before any write.
      - Required to make a token; a task that would make one without it fails before any write, in check mode too.
      - Check mode does not look at the directory, so one the module cannot use passes check mode and fails the real run
        before any write.
    type: path
  allow_management_disruption:
    description:
      - Allow the task to remove, disable or expire the token it connects with. Later tasks must then connect with a
        password, or with the new token in RV(bearer_token). An expiration at most an hour after the controller's
        clock counts as expiring the token.
      - The module sends the write that removes, disables or expires the token it connects with after its other writes,
        and nothing after it. It reports RV(connection_token_changed) when it removed that token; a token that it
        disabled or expired is not reported. A write that fails after another succeeded fails the task with C(changed)
        true.
    type: bool
    default: false
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - A token that would be made is reported as a change, without a bearer.
  diff_mode:
    support: full
    details:
      - The diff lists the user's tokens before and after, each with its id, O(enabled) and O(expiration_time). In check
        mode, a token the run would make appears with the id V((a new token)).
  idempotent:
    support: partial
    details:
      - A run that makes a token returns its bearer, which no later run can return; a second run with O(state=present)
        finds the token and changes nothing.
      - O(state=rotated) makes a new token on every run.
notes:
  - "API: GET /v1/auth/access-tokens/ with the user, POST /v1/auth/access-tokens/, PATCH and DELETE
    /v1/auth/access-tokens/{id}; POST /v1/identity/find; GET /v1/session/who-am-i when the task connects with an access
    token."
  - A task that would make a token fails before any write unless it sets C(no_log) and O(secret_destination) (G-TOKEN).
    On ansible-core 2.22 and later the module also registers the bearer with Ansible's masking of secret values.
  - A task with C(no_log) hides its result, RV(notes) and the diff included, and keeps warnings; when the task fails,
    ansible-core still prints its C(msg). The module reports a token that is expired, and stays so, as a warning,
    because the C(no_log) such a task needs hides RV(notes).
  - A user holds at most two tokens, and an expired token counts (G-TOKEN).
  - A token holds the roles of its user, so the token of a member of Administrators is a full administrator's
    credential.
  - A token is bound to the cluster that made it, and a rebuilt cluster has none of the old tokens.
  - When the connection is lost after a token is made, or the module cannot store its bearer, or the listing after the
    make does not show the token, the module removes that token and fails, and a rotation keeps the old tokens; the
    message says to run the task again. When removing that token fails too, the token stays, and the message names it. When the connection to a
    create is lost and the listing after it fails too, the module fails with C(changed) and RV(maybe_applied) true, and
    the message says that a token whose bearer is lost may be left, and gives O(state=rotated) when the user held no
    token, and O(state=absent), then O(state=present), when it held one. A later O(state=rotated) for a user who then
    holds two tokens is refused before any write, and the refusal marks the token the task connects with (G-TOKEN).
  - When a step after the bearer is stored fails, the module fails with C(changed) true, and the message names the new
    token, the file that holds its bearer and the old tokens it kept; the module gives the same words as a warning. The
    user may then hold two tokens, and the module has no way to remove only the old one. The message names the old
    token's id and the way to delete it outside the module, C(qq auth_delete_access_token) or
    C(DELETE /v1/auth/access-tokens/{id}) (G-TOKEN). When a delete of an old token lost its connection, the message
    says that token may have been deleted and says to list the user's tokens before deleting it.
  - A user who holds two tokens is refused a rotation before any write. The message names each token, marks the one the
    task connects with and any that expired, and says to delete the one to drop by its id, or to run O(state=absent)
    and then O(state=present) to replace both (G-TOKEN).
  - A rotation with O(enabled=false) is refused when the token the task connects with is among the old ones, since
    the play would be left no token to connect with.
"""

EXAMPLES = r"""
# These tasks take api_host and a credential from module_defaults for group/qumulo.cluster.qumulo.
# A task that makes a token needs no_log and secret_destination, since the cluster shows the bearer only once.
# Give each consumer a user of its own: a user's tokens are one set, so two consumers of one user cannot tell whose
# token is whose, and a rotation for one cuts off the other.
- name: Give the backup service an access token, writing a new token's bearer to a file
  qumulo.cluster.access_token:
    user: local:svc-backup
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  register: backup_token
  no_log: true

# The bearer is in the file only after the task made a token; a second run finds the token and changes nothing.
- name: Say where a new token's bearer is
  ansible.builtin.debug:
    msg: "made access token {{ backup_token.access_token_id }}; its bearer is in {{ backup_token.secret_file }}"
  when: backup_token.secret_file is defined

# Quote the time. A user who holds no token is given one, so the task carries no_log and secret_destination.
- name: Make the backup service's tokens expire at the end of June
  qumulo.cluster.access_token:
    user: local:svc-backup
    expiration_time: "2027-06-30T23:59:59Z"
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  no_log: true

- name: Stop the backup service's tokens from logging in, keeping them
  qumulo.cluster.access_token:
    user: local:svc-backup
    enabled: false
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  no_log: true

# no_log hides the diff from the output; a check run makes no token, so its diff holds no bearer.
- name: Preview a rotation of the backup service's token
  qumulo.cluster.access_token:
    user: local:svc-backup
    state: rotated
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  check_mode: true
  diff: true
  register: preview
  no_log: true

- name: Show the rotation's preview
  ansible.builtin.debug:
    var: preview.diff

# The new token works before the old one is removed, but the old one is removed at the end of the task: every consumer
# of the user's token loses access until it holds the new bearer. A user who holds two tokens is refused before any write;
# delete the one to drop by its id outside the collection, as the message says, or replace both with state: absent, then
# state: present, from a play that connects with a password.
- name: Rotate the backup service's token
  qumulo.cluster.access_token:
    user: local:svc-backup
    state: rotated
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  register: rotated
  no_log: true

# When the play connects with the token it rotates, later tasks must connect with the new one. The old token is removed
# last, so this task's own connection lasts until it ends. The token of a member of Administrators is a full
# administrator's credential: keep the file private.
- name: Rotate the token this play connects with
  qumulo.cluster.access_token:
    user: local:svc-ansible
    state: rotated
    allow_management_disruption: true
    secret_destination: "{{ inventory_dir }}/../artifacts/{{ inventory_hostname }}/secrets"
  register: own_token
  no_log: true

- name: Connect with the new token from here on
  ansible.builtin.set_fact:
    qumulo_api_token: "{{ own_token.bearer_token }}"
  when: own_token.connection_token_changed | default(false)
  no_log: true

- name: Revoke every token of a retired service
  qumulo.cluster.access_token:
    user: local:svc-old
    state: absent
"""

RETURN = r"""
access_tokens:
  description:
    - The user's tokens after the task, oldest first, without their bearers; empty after O(state=absent).
    - In check mode a token the run would make has no id, creator or creation time yet, and each is null.
  returned: when the task succeeds
  type: list
  elements: dict
  contains:
    user:
      description: The token's user, as a trustee.
      type: str
    id:
      description: The token's id.
      type: str
    creator:
      description: The user who made the token, as a trustee.
      type: str
    creation_time:
      description: When the cluster made the token.
      type: str
    expiration_time:
      description:
        - When the token stops working, as the cluster reads it back, in UTC; null for never.
        - In check mode, a time the task would set is shown in UTC with V(Z), as the module would send it.
      type: str
    enabled:
      description: Whether the token can log in.
      type: bool
  sample: [{user: local:svc-backup, id: "1000000000000000000001", creator: local:admin, creation_time: "2026-06-01T09:30:00.000000000Z",
            expiration_time: null, enabled: true}]
bearer_token:
  description:
    - The new token's bearer, the only time the cluster returns it. Hide it with C(no_log).
    - After a failure it is the bearer of the token the module made, which is also in the file RV(secret_file) names.
  returned: >-
    when the run made a token, outside check mode; and on a failure after the bearer was stored in its file, when the
    confirming listing, the disabling of the new token or the removal of an old token failed
  type: str
  sample: access-v1:example-not-a-real-bearer
access_token_id:
  description: The id of the token the run made.
  returned: when the run made a token, outside check mode; and on the same failures as RV(bearer_token)
  type: str
  sample: "1000000000000000000001"
secret_file:
  description: The file in O(secret_destination) that holds the new token's bearer.
  returned: when the run made a token, outside check mode; and on the same failures as RV(bearer_token)
  type: str
  sample: /srv/artifacts/edge01/secrets/access-token-local_svc-backup-1000000000000000000001.yml
connection_token_changed:
  description: V(true) when the task removed the token it connected with; later tasks must connect with another credential.
  returned: when the task removed the token it connected with
  type: bool
  sample: true
notes:
  description: What the module noticed, which is a user that does not exist yet in check mode.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["user local:svc-backup does not exist yet; an earlier task must create it, and this task then makes its token"]
maybe_applied:
  description: >-
    V(true) when a create, change or removal of a token was sent and its answer was lost, by a lost connection or a
    gateway's HTTP 502 or 504, so it may have been applied. After a lost create it is set only when the listing that
    looks for the new token failed too; the message then says a token whose bearer is lost may be left, and what to
    run. The module fails with C(changed) true.
  returned: >-
    on such a failure; with RV(bearer_token), RV(access_token_id) and RV(secret_file) when the lost write came after the
    new bearer was stored
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.access_tokens import AccessToken
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource

ARGUMENT_SPEC = {
    "user": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["present", "absent", "rotated"], "default": "present"},
    "enabled": {"type": "bool"},
    "expiration_time": {"type": "str"},
    "secret_destination": {"type": "path"},
    "allow_management_disruption": {"type": "bool", "default": False},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), AccessToken)


if __name__ == "__main__":
    main()
