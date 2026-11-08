<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->
# portal_pair

Pairs a spoke cluster with a hub cluster through a portal, or drains and decommissions the pairing. The role reaches both clusters from one host, with one set of connection options for each, and identifies the pairing by the two cluster UUIDs, never by server ids.

## What it does

| `portal_pair_state` | Steps |
|---|---|
| `present` | checks that both clusters run the same Qumulo Core version; proposes the portal and its roots on the spoke with `qumulo.cluster.portal_spoke`; accepts it and authorizes the roots on the hub with `qumulo.cluster.portal_hub`; waits until the spoke shows it ACCEPTED and ACTIVE with the role's roots authorized |
| `absent` | sets the portal read-only on the hub and waits until both sides show it, so the spoke's outstanding writes reach the hub; deletes the spoke portal; waits until the hub portal is gone too, so no hub slot leaks |

A PENDING portal holds no spoke writes and is deleted without a drain. A hub record that reads DELETING has lost its spoke portal already, to an earlier run whose wait for the record ended; the role skips the drain and waits for the record to go. A portal whose spoke is DISCONNECTED cannot be drained: the role fails before any change when the hub sees the spoke DISCONNECTED, unless `portal_pair_allow_data_loss` is set, which forces the deletes and loses the spoke writes the hub has not received.

To remove only the hub's record of a spoke that no longer exists, such as an edge rebuilt with a new UUID, use `qumulo.cluster.portal_hub` with `state: absent` and `allow_data_loss: true`; this role needs both clusters. Without `allow_data_loss`, `portal_hub` waits for a hub portal that reads DELETING and DISCONNECTED, as one does for a few seconds after its spoke's delete. When the spoke is gone for good, such a record never goes, so the task waits the whole `wait_timeout` and then fails with `changed=false`.

## Variables

`meta/argument_specs.yml` is the reference; the main ones:

| Variable | Default | Meaning |
|---|---|---|
| `portal_pair_spoke`, `portal_pair_hub` | required | each side's connection options, as `module_defaults` for `group/qumulo.cluster.qumulo` takes them, with `expected_cluster_uuid` set |
| `portal_pair_state` | `present` | `present` pairs, `absent` drains and decommissions |
| `portal_pair_hub_hosts`, `portal_pair_spoke_hosts` | none | the IP addresses each side targets on the other; needed to pair. Floating IPs are recommended, or for a multi-AZ cluster in AWS the load balancer's IPs; a bare-metal cluster without floating IPs gives its node IPs. Behind NAT, making them reachable is yours to do |
| `portal_pair_roots` | `[]` | roots to propose and authorize, each with `spoke_root` (must not exist on the spoke yet) and `hub_root` (an existing directory on the hub) |
| `portal_pair_type` | `read_write` | the portal's type |
| `portal_pair_allow_data_loss` | `false` | allow deleting a DISCONNECTED portal, losing the spoke writes the hub has not received |
| `portal_pair_wait_timeout` | `600` | seconds each wait may take |

The role takes the addresses as given. Neither it nor the roles that include it derive, translate or second-guess them; the inventory declares them, as `qumulo_portal_hosts`, or the `qumulo.cluster.terraform` inventory plugin sets them from Terraform's output.

The role sets the fact `portal_pair_portals`, with the portal as each side reports it under `spoke` and `hub`, in the shape `qumulo.cluster.portal_info` uses; after a decommission both are null.

## Example

```yaml
- name: Pair the edge with the hub
  hosts: localhost
  gather_facts: false
  roles:
    - role: qumulo.cluster.portal_pair
      vars:
        portal_pair_spoke:
          api_host: edge01.example.com
          api_password: "{{ edge01_password }}"
          expected_cluster_uuid: "{{ edge01_cluster_uuid }}"
        portal_pair_hub:
          api_host: hub01.example.com
          api_password: "{{ hub01_password }}"
          expected_cluster_uuid: "{{ hub01_cluster_uuid }}"
        portal_pair_hub_hosts: [{address: 10.0.0.10}]
        portal_pair_spoke_hosts: [{address: 10.0.1.20}]
        portal_pair_roots:
          - {spoke_root: /hub-data, hub_root: /data}
```

Set `portal_pair_state: absent` with the same connection options to decommission. Run with `--check` first: the role then predicts each side's change and waits for nothing.

## Requirements

Qumulo Core 7.10.3 or later on both clusters, the same version on each, and ansible-core 2.20 or later. The idempotency gaps it works within are recorded in `docs/reference/gaps.md` (G-PORTAL-ONESHOT, G-PORTAL-ETAG, G-PORTAL-STALE) and `docs/reference/quirks.md` (QUIRK-005).
