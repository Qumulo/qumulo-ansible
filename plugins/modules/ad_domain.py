#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: ad_domain
short_description: Join a Qumulo cluster to an Active Directory domain, or leave it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - With O(state=joined), joins the cluster to O(domain) when it is not joined to any domain, and otherwise changes
    the settings that can change in place. With O(state=absent), leaves O(domain).
  - The cluster is in the desired state when it is joined to O(domain), compared without regard to case. It is joined
    under its own name, which is its computer account (C(NAME$)) and cannot change while joined.
  - Joining and leaving are explicit. The module never leaves a domain in order to join another, or to change a
    setting that only a join sets; it fails and names the step to run instead.
  - The join and leave credentials cannot be read back, and the computer account itself lives in Active Directory,
    where the module cannot see it (G-AD-JOIN). A join under a name whose computer account already exists takes that
    account over and resets its password, which breaks any other cluster still using that account.
  - Every operation runs in the background on the cluster. With O(wait=true), the module waits for it to finish and
    fails with the cluster's reason when it fails. With O(wait=false), the result is the membership the operation is
    expected to leave, not one read back.
options:
  domain:
    description: The domain's DNS name, such as V(corp.example.com). Compared without regard to case.
    type: str
    required: true
  state:
    description: V(joined) joins or keeps the cluster joined; V(absent) leaves the domain.
    type: str
    choices: [joined, absent]
    default: joined
  domain_netbios:
    description:
      - The domain's NetBIOS name. Unset lets the cluster derive it from the first label of O(domain).
      - Set only by a join. A different value on a joined cluster fails the task.
    type: str
  ou:
    description:
      - The organizational unit the computer account is created in, as a distinguished name. Unset uses the domain's
        default container.
      - Set only by a join. A different value on a joined cluster fails the task.
    type: str
  join_user:
    description:
      - The domain user that joins the cluster, and that deletes its computer account when it leaves. Needed to join.
      - Without it, leaving still succeeds, with a warning, but leaves the computer account in Active Directory.
    type: str
  join_password:
    description: The password of O(join_user). It cannot be read back, and it is sent only to join or leave.
    type: str
  search_trusted_domains:
    description: Also look identities up in domains this one trusts. Changes in place. A join that leaves it unset turns it on.
    type: bool
  use_ad_posix_attributes:
    description: Read POSIX user and group ids from Active Directory. Changes in place.
    type: bool
  base_dn:
    description:
      - The distinguished names to search for users and groups, separated by C(;). Changes in place.
      - An empty value means the cluster's default, C(CN=Users) under the domain, and compares equal to it.
    type: str
  domain_controllers:
    description:
      - The domain controllers to use, by name or address; empty lets the cluster find them. Changes in place.
      - The cluster reports the configured domain controllers only in the record of its last AD operation, which a later
        operation replaces. When that record is not for O(domain), the list cannot be compared and is sent only with a
        join. On a joined cluster the result then carries a note (G-AD-DCS).
    type: list
    elements: str
  wait:
    description: Wait for the join, leave or change to finish.
    type: bool
    default: true
  wait_timeout:
    description: Seconds to wait for it.
    type: int
    default: 600
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
attributes:
  check_mode:
    support: full
    details:
      - Predicts a join or leave from the cluster's state; whether Active Directory accepts it is known only by doing it.
  diff_mode:
    support: full
  idempotent:
    support: partial
    details:
      - Idempotent as far as the cluster can tell, which is that it is joined to O(domain) under its own name.
notes:
  - "API: GET /v1/ad/status and /v1/ad/monitor; POST /v1/ad/join, /v1/ad/reconfigure and /v1/ad/leave."
  - A join needs the cluster to resolve the domain's names (M(qumulo.cluster.dns_settings)) and its time to be within
    Kerberos's tolerance of the domain's (M(qumulo.cluster.time_settings)).
  - The cluster's name (M(qumulo.cluster.cluster_settings)) is the name of its computer account.
  - "Registers: G-AD-JOIN, G-AD-DCS."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
# The cluster's name becomes its computer account, which cannot be renamed while joined. A join under a name whose
# computer account already exists takes that account over and resets its password, which breaks another cluster
# that still uses it. Name the cluster, and give it the domain's DNS and a time source the domain agrees with, first.
- name: Prepare the edge to join the domain
  block:
    - name: Name the edge
      qumulo.cluster.cluster_settings:
        cluster_name: edge01

    - name: Resolve names through the domain's DNS servers
      qumulo.cluster.dns_settings:
        servers: [10.0.0.2, 10.0.0.3]
        search_domains: [corp.example.com]

    - name: Take time from the domain's time servers
      qumulo.cluster.time_settings:
        source: API_TIME_SOURCE_NTP
        ntp_servers: [ntp1.corp.example.com, ntp2.corp.example.com]

- name: Join the edge to the domain under its own name
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    ou: OU=Edges,DC=corp,DC=example,DC=com
    join_user: svc-qjoin
    join_password: "{{ vault_ad_join_password }}"

- name: Join the edge with the domain's NetBIOS name, POSIX attributes from Active Directory and a search base
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    domain_netbios: CORP
    use_ad_posix_attributes: true
    base_dn: "CN=Users,DC=corp,DC=example,DC=com;OU=Staff,DC=corp,DC=example,DC=com"
    join_user: svc-qjoin
    join_password: "{{ vault_ad_join_password }}"

- name: Allow a slow domain to take half an hour to join
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    join_user: svc-qjoin
    join_password: "{{ vault_ad_join_password }}"
    wait_timeout: 1800

# With wait: false the result is the membership the join is expected to leave. Read the cluster later with
# qumulo.cluster.identity_info to see whether the cluster has joined.
- name: Start the join and carry on without waiting for it
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    join_user: svc-qjoin
    join_password: "{{ vault_ad_join_password }}"
    wait: false

- name: Preview joining the domain
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    join_user: svc-qjoin
    join_password: "{{ vault_ad_join_password }}"
  check_mode: true
  diff: true
  register: join_preview

- name: Preview a change to the domain controllers
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    domain_controllers: [dc1.corp.example.com, dc2.corp.example.com]
  check_mode: true
  diff: true
  register: ad_preview

- name: Show the membership the change would leave
  ansible.builtin.debug:
    var: ad_preview.ad_domain

# On a joined cluster these settings change in place, with no join credentials. ou and domain_netbios are set only
# by a join, so a different value fails the task. When the cluster's last AD operation was for another domain, the
# domain controllers cannot be compared and are not sent (G-AD-DCS).
- name: Use two domain controllers and stop searching trusted domains
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    domain_controllers: [dc1.corp.example.com, dc2.corp.example.com]
    search_trusted_domains: false

# A cluster joined to one domain is never moved to another: leave it first, then join the other.
- name: Move the edge from the old domain to the new one
  block:
    - name: Leave the old domain, deleting the computer account
      qumulo.cluster.ad_domain:
        domain: old.example.com
        state: absent
        join_user: svc-qjoin-old
        join_password: "{{ vault_old_ad_join_password }}"

    - name: Join the new domain
      qumulo.cluster.ad_domain:
        domain: corp.example.com
        join_user: svc-qjoin
        join_password: "{{ vault_ad_join_password }}"

- name: Leave the domain before the edge is destroyed, deleting its computer account
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    state: absent
    join_user: svc-qjoin
    join_password: "{{ vault_ad_join_password }}"

# Without credentials the cluster leaves, warns, and the computer account stays in Active Directory.
- name: Leave the domain without credentials
  qumulo.cluster.ad_domain:
    domain: corp.example.com
    state: absent
"""

RETURN = r"""
ad_domain:
  description: The cluster's AD membership after the task. In check mode, or with O(wait=false), it is the membership the task is expected to leave,
    not one read back.
  returned: always
  type: dict
  contains:
    status:
      description: The join status, such as V(JOINED_TO_DOMAIN) or V(NOT_IN_DOMAIN).
      type: str
    domain:
      description: The joined domain, or V(null).
      type: str
    domain_netbios:
      description: The domain's NetBIOS name, or V(null).
      type: str
    ou:
      description: The organizational unit given at the join, or V(null).
      type: str
    search_trusted_domains:
      description: Whether trusted domains are searched.
      type: bool
    use_ad_posix_attributes:
      description: Whether POSIX ids come from Active Directory.
      type: bool
    base_dn:
      description: The search base distinguished names, or V(null).
      type: str
    domain_controllers:
      description: The configured domain controllers, or V(null) when they cannot be read for this domain.
      type: list
      elements: str
  sample: {status: JOINED_TO_DOMAIN, domain: corp.example.com, domain_netbios: CORP, ou: null, search_trusted_domains: false,
           use_ad_posix_attributes: false, base_dn: "CN=Users,DC=corp,DC=example,DC=com", domain_controllers: []}
notes:
  description:
    - What the module noticed but did not compare or change, such as domain controllers that cannot be read back, or a
      cluster joined to another domain that O(state=absent) leaves as it is.
  returned: when there is something to note
  type: list
  elements: str
  sample: ["the cluster is joined to other.example.com, not corp.example.com; left as it is"]
maybe_applied:
  description: V(true) when the join, change or leave request was sent and the connection was lost before the cluster answered.
  returned: on a failure after such a request, only with O(wait=false)
  type: bool
  sample: true
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run_resource
from ansible_collections.qumulo.cluster.plugins.module_utils.dirsvc import AdDomain

ARGUMENT_SPEC = {
    "domain": {"type": "str", "required": True},
    "state": {"type": "str", "choices": ["joined", "absent"], "default": "joined"},
    "domain_netbios": {"type": "str"},
    "ou": {"type": "str"},
    "join_user": {"type": "str"},
    "join_password": {"type": "str", "no_log": True},
    "search_trusted_domains": {"type": "bool"},
    "use_ad_posix_attributes": {"type": "bool"},
    "base_dn": {"type": "str"},
    "domain_controllers": {"type": "list", "elements": "str"},
    "wait": {"type": "bool", "default": True},
    "wait_timeout": {"type": "int", "default": 600},
}


def main():
    run_resource(qumulo_module(ARGUMENT_SPEC), AdDomain)


if __name__ == "__main__":
    main()
