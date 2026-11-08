<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# License an edge

This page takes a new edge, or an edge rebuilt with a new UUID, from no license to a license the cluster holds, with
its capacity clamp set.
A license is bound to one cluster UUID, so a new UUID takes two passes of the same step with a hand-off between them:
the first pass registers the cluster and names the UUID, and the second applies the document Qumulo issued for it.
When you finish, [`license`](../../plugins/modules/license.py) reports the license as `active` on the edge, and a repeat run changes
nothing.

For why a license needs two passes, and how the clamp, the license's cap and the 1 TB floor interact, see
[Licensing and capacity](../explanation/licensing-and-capacity.md).

## Before you start

- Have an inventory that connects to the edge and declares `qumulo_capacity`, with a `clamp` if the edge's cache
  should be limited: [Set up an inventory for your clusters](set-up-inventory.md).
  The license step runs whenever `qumulo_capacity` is declared.
  [`examples/inventory/group_vars/qumulo_edges.yml`](../../examples/inventory/group_vars/qumulo_edges.yml) is a
  working block.
- Declare `qumulo_monitoring` with a registration, so that the first pass registers the edge with Nexus and Qumulo can
  see its UUID: [Register a cluster with Nexus](nexus.md).
- Know how you keep secrets: [Keep secrets out of the inventory](store-secrets.md).
  The license document goes in the inventory encrypted.
- Connect as the built-in `admin` user, which holds the `PRIVILEGE_DEBUG` privilege.
  Applying a document uses the cluster's debug license routes (G-LICENSE, see [Gaps](../reference/gaps.md)).

## Run the first pass

Run the baseline on the edge, adding `--ask-vault-pass` or `--vault-password-file` when the inventory holds vault
values:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --ask-vault-pass
```

The license step finds that the cluster holds no license for its UUID and that the inventory holds no document for it.
It succeeds and nothing waits.
It leaves the clamp unset, because the clamp's write is what applies a license's cap.
It prints a hand-off that starts `edge01: The cluster holds no license for its UUID, <uuid>.`

Copy the UUID from the hand-off.
[Troubleshooting](../reference/troubleshooting.md#the-license) has the whole message.

If the edge already has a clamp stored that the declared one would not raise, as on an edge moved from `license: none`
to `pending`, the hand-off ends `Its clamp is already set, so the license's cap applies at the next quorum formation
after the license arrives.`
The second pass then writes no clamp.

## Get the license document

Ask Qumulo for the license for the UUID the hand-off names.
The document is a JSON file for that one UUID.
How Qumulo delivers the document to a customer is not yet defined (G-LICENSE).

Qumulo may instead issue the license to the cluster directly, and it reaches the cluster on its own, which can take
hours.
Then skip [Put the document in the inventory](#put-the-document-in-the-inventory), keep `license: pending`, and run the
second pass once the cluster holds the license.
Until then the second pass fails with the hand-off.

The collection never creates a license, never reads one from Nexus and never waits for one.

## Put the document in the inventory

1. Encrypt the whole JSON file as one value:

   ```sh
   ansible-vault encrypt_string --stdin-name vault_qumulo_license_document < edge01-license.json
   ```

   `--prompt` would read only the first line of the file.

2. Paste the output into the edge's `host_vars/edge01.yml`.

3. Refer to it once from the group that declares `qumulo_capacity`:

   ```yaml
   # inventory/group_vars/qumulo_edges.yml
   qumulo_capacity:
     clamp: 4TB
     license_document: "{{ vault_qumulo_license_document | default(none) }}"
     license: pending
   ```

   A host's `qumulo_capacity` replaces the group's whole, so the document has a variable of its own.
   The `default(none)` lets an edge without a vaulted document pass.
   [Inventory variables](../reference/variables.md#capacity) lists every key of `qumulo_capacity`.
   An edge that declares its own `qumulo_capacity` in its host_vars needs the `license_document` line in that block too.

## Run the second pass

On a host in `qumulo_edges`, run [`edge_license_complete`](../../playbooks/edge_license_complete.yml).
On any other cluster, run the baseline again, which runs the same step.
The hand-off names the right one.

```sh
ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l edge01 --ask-vault-pass
```

For this edge, the run:

1. Applies the document with `license`, unless the cluster holds it already.
   The license is active as soon as it is applied.
2. Checks that the license's cap allows the clamp, when the clamp would change.
   A license too small fails here, before anything re-forms quorum.
3. Sets the clamp with [`capacity_clamp`](../../plugins/modules/capacity_clamp.py).
   The write re-forms quorum, which pauses the edge, and its portal on its hub, for less than 30 seconds.
   That is when the license's cap applies (G-CLAMP-BOUNCE in [Gaps](../reference/gaps.md)).
4. Declares the replication relationships into the edge that other inventory hosts declare, which the capacity gate
   held back until the license and the clamp applied.

Once every edge in the run has its license and clamp, a second play tagged `restore` restores each rebuilt edge's
relationships from their replicas
([Restore a rebuilt edge's data from its replica](restore-replica.md#restore-in-the-second-pass)).

The playbook runs one edge at a time.
An edge with neither an active license nor a document for its UUID fails at once, naming its UUID.
With several edges in the run, the playbook goes on to the next edge and fails at the end, naming each edge that
failed.

Once the cluster holds the license, the second pass needs no document.
A run on a cluster that holds the license and whose clamp is set changes nothing.

## Check the license

Add these two tasks under `tasks:` in `first-run.yml` from [Check the connection](set-up-inventory.md#check-the-connection):

```yaml
    - name: Read the license for the edge's UUID
      qumulo.cluster.license:
      register: license_check

    - name: Show the license
      ansible.builtin.debug:
        msg: "{{ license_check.license_status }}, cap {{ license_check.license.usable_capacity_cap | default('none', true) }}"
```

Run it on the edge:

```sh
ansible-playbook -i inventory first-run.yml -l edge01 --ask-vault-pass
```

The debug task prints `active`, followed by the license's cap in bytes.
A baseline run on the edge then prints no hand-off, and its license step changes nothing.

## Variants

### Run an edge without a license

For an edge that stays unlicensed, declare `license: none` with a clamp, and no document:

```yaml
# inventory/host_vars/edge01.yml
qumulo_capacity:
  clamp: 1TB
  license: none
```

The baseline hands nothing off and says `edge01: runs without a license.`
It writes the clamp in the first pass.
Its capacity is what formation gave it, the smaller of its platform and 1 TB (G-EDGE-FLOOR in [Gaps](../reference/gaps.md)).
A clamp above 1 TB is stored and provisions nothing more until a license arrives.
A clamp more than its rounding tolerance below the file system fails as a decrease (G-CLAMP-SHRINK in [Gaps](../reference/gaps.md)), so declare a
clamp at or above what formation provisioned.
[`capture_info`](../../plugins/modules/capture_info.py) writes `none` for an unprotected edge that holds no license; set `pending` instead if a license is
coming.

`none` goes with a clamp and never with a document.
The license step refuses the two together before it applies anything: remove the document, or set
`qumulo_capacity.license` to `pending` or `required`.

An edge that declares `none` and holds an active license after all runs as `pending` does, and warns that `none` is
stale.
Set `qumulo_capacity.license` to `pending` or `required`.
[Troubleshooting](../reference/troubleshooting.md#the-license) lists the four forms of the warning.

### Flag a missing license

Set `qumulo_capacity.license: required` instead of `pending`, the default.
The first pass still succeeds, and the hand-off starts `edge01: qumulo_capacity.license is required, and the cluster
has none.`
While the cluster has no active license, the role also sets `cluster_baseline_license_missing`, which a later task of
your play can test.

### Replace a document for another UUID

After a rebuild, the document in the inventory is the old edge's.
The license step does not apply a document for another UUID, and its hand-off says
`The license document in qumulo_capacity.license_document is for <old UUID>, not this cluster.`

1. Get the document issued for the new UUID.
2. Replace the encrypted value of `vault_qumulo_license_document` with it.
3. Run the second pass.

[Rebuild an unprotected edge](edge-rebuild.md#finish-the-license) shows the license in a rebuild.

### Skip the restore

The second pass restores a rebuilt edge's held relationships after the license and the clamp apply.
To apply the license alone, skip the tagged play:

```sh
ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l edge01 --ask-vault-pass --skip-tags restore
```

### Renew a license

A renewed license is a new document for the same UUID.

1. Encrypt the new document as above and replace the value of `vault_qumulo_license_document`.
2. Run the second pass.

The module applies a document the cluster does not already hold, told apart by its key, cap and expiry, and leaves
one it holds alone.
On a cluster that is not an edge, run the baseline instead.

On an edge with `qumulo_previous_cluster_uuids`, or one whose declared target path holds a live record from a UUID the
inventory does not name, the restore play can fail the run.
[Restore a rebuilt edge's data from its replica](restore-replica.md#when-the-restore-refuses-or-stops) lists what stops it.
For a renewal that must not restore, skip the restore.

### Grow the clamp after a larger cap

A renewed license with a larger cap lets the clamp grow.
Raise `qumulo_capacity.clamp`, then follow [Grow the clamp](edge-sizing.md#grow-the-clamp).

### License an edge before an upgrade

Qumulo Core refuses to upgrade an edge whose license has expired, or that has none and was formed more than 30 days ago
(G-UPGRADE-LICENSE in [Gaps](../reference/gaps.md)).
Get its license and run the second pass before the upgrade.
The upgrade's plan warns about each edge whose license reads none or expired, and goes on.
[Why](../explanation/licensing-and-capacity.md#upgrading-an-edge-without-a-license);
[Upgrade a fleet](upgrade.md) describes the upgrade.

## If a run stops

[Troubleshooting](../reference/troubleshooting.md#the-license) quotes each message and says what to do.
In short:

- The edge has neither an active license nor a document for its UUID: do
  [Get the license document](#get-the-license-document) and the steps after it.
- The license's cap is below the clamp: nothing re-formed quorum.
  Get a license whose cap allows the clamp, or lower `qumulo_capacity.clamp`.
- `declares qumulo_capacity.license: none and a license document`: see
  [Run an edge without a license](#run-an-edge-without-a-license).
- The cluster refused the document: a document must be exactly the file Qumulo issued, for this cluster's UUID.
- The message names `PRIVILEGE_DEBUG`: apply the document as `admin`.
- A warning that the clamp is already set, so the license's cap applies at the next quorum formation: nothing failed.
  To apply the cap now, raise `qumulo_capacity.clamp` and follow [Grow the clamp](edge-sizing.md#grow-the-clamp),
  since the clamp's write re-forms quorum.
- The hand-off says the document is for another UUID: see
  [Replace a document for another UUID](#replace-a-document-for-another-uuid).

## Next

- [Size and resize an edge's cache](edge-sizing.md) grows the clamp within the license's cap.
- [Rebuild an unprotected edge](edge-rebuild.md#finish-the-license) gives the edge a new UUID, and so needs a new license.
- [Inventory variables](../reference/variables.md#capacity) lists the keys of `qumulo_capacity`.
- [Licensing and capacity](../explanation/licensing-and-capacity.md) explains the two passes and the clamp.
- [`edge_license_complete`](../../playbooks/edge_license_complete.yml) and the
  [cluster_baseline](../../roles/cluster_baseline/) role hold the license step.
