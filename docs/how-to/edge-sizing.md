<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Size and resize an edge's cache

This page sets the size of an unprotected edge's cache with `qumulo_capacity.clamp`, grows it as the cache needs more
room, and shrinks it by rebuilding the edge.
When you finish, the inventory declares the cache size you want and the edge holds it.
Two facts decide which section you need: an edge's disk cannot be resized once the edge is formed, and provisioned
capacity never shrinks.
[Licensing and capacity](../explanation/licensing-and-capacity.md) explains both.

## Before you start

- Have the edge in the `qumulo_edges` group of an inventory with a working connection
  ([Set up an inventory for your clusters](set-up-inventory.md)).
- For a cache above 1 TB, have a license that Qumulo issued for the edge's UUID ([License an edge](licensing.md)).
  An edge with no license is held to 1 TB, whatever the clamp says.
- Limit the baseline to one edge at a time with `-l`.
  Raising the clamp re-forms the edge's quorum, which pauses the edge, and its portal on its hub, for less than 30
  seconds.
  `edge_cache_resize` and `edge_license_complete` already take one edge at a time.
- Keep the license document encrypted ([Keep secrets out of the inventory](store-secrets.md)).
  The commands below use `--ask-vault-pass` where a run reads it.

## Start small on a thin disk

Do this when you install a new edge.

1. Give the edge a large, thin-provisioned disk when you install it.
   Thin provisioning costs nothing for space the edge does not use.
2. Declare the cache size you need now in `qumulo_capacity.clamp`, before the license arrives:

   ```yaml
   # group_vars/qumulo_edges.yml
   qumulo_capacity:
     clamp: 2TB
     free_threshold: 0.2
     license_document: "{{ vault_qumulo_license_document | default(none) }}"
   ```

   `clamp` takes bytes, or a number with a unit such as `2TB` or `2TiB`, where `TB` is 10^12 bytes and `TiB` is 2^40
   bytes. Units ignore case.
   `free_threshold` is the fraction of capacity that eviction keeps free.
   Do not leave the clamp unset: an unset clamp limits nothing, and capacity provisioned under a license's cap never
   shrinks.
   Define `vault_qumulo_license_document` in each edge's `host_vars` once its license arrives; a license is bound to
   one UUID.

3. Bring the edge to its baseline and finish its license, as for any new edge ([License an edge](licensing.md)).
   The license step writes the clamp after it applies the license, and the clamp's write is what applies the license's
   cap.

To check, run the baseline in check mode with a diff.
It reports no change to the clamp once the edge is licensed and the second pass has run:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff
```

### Give one edge a different clamp

A host's `qumulo_capacity` replaces the group's whole, so an edge that needs another clamp declares every key it
needs in its `host_vars`:

```yaml
# host_vars/edge01.yml
qumulo_capacity:
  clamp: 4TB
  free_threshold: 0.2
  license_document: "{{ vault_qumulo_license_document | default(none) }}"
```

### Run an edge without a license

An edge that declares `qumulo_capacity.license: none` runs without a license
([Run an edge without a license](licensing.md#run-an-edge-without-a-license)).
The baseline writes its clamp on the first pass.
A clamp above 1 TB provisions nothing more until a license arrives, and then limits it, from the next quorum formation,
which the collection does not cause while the clamp is unchanged.
To supply a document later, set `license` to `pending` or `required`, keeping the other keys.

### Keep a cache smaller than 1 TB

A new edge provisions the smaller of its platform's limit and 1 TB at formation, before any clamp can be set
(G-EDGE-FLOOR, see [Gaps](../reference/gaps.md)).
Keep a cache smaller than that free with `free_threshold` instead of the clamp.
Leave the clamp at 1 TB, or at the stored clamp.
`edge_cache_resize` needs a declared clamp, so leave it unset only on an edge you never resize with it.
The baseline fails on a declared clamp more than its rounding tolerance below the stored clamp, or, with none stored,
below the file system's size (G-CLAMP-SHRINK).

## Grow the clamp

1. Raise `qumulo_capacity.clamp` in the inventory.
2. Run the playbook in check mode, which only reports the route and its inputs:

   ```sh
   ansible-playbook qumulo.cluster.edge_cache_resize -i inventory -l edge01 --check
   ```

   A check run reports the route and writes nothing
   ([Check and diff](../explanation/state-management.md#check-and-diff)).
   The playbook prints one message per edge.
   It starts with the route and ends with the inputs, in bytes: `T` is the target, `R` the stored clamp, `L` the most
   the license allows, `C` the file system's size, and `margin` how close `C` must be to `T` to count as that size.
   For example:

   ```text
   grow: the target is more than 400000000000 bytes above the file system (1000000000000 bytes), and the clamp
   (1000000000000 bytes) is below it (T=4000000000000, R=1000000000000, L=4000000000000, C=1000000000000,
   margin=400000000000)
   ```

3. Read the route and act on it:

   | Route | What to do |
   |---|---|
   | `grow` | Run step 4. |
   | `within` | Nothing to raise. The file system is already within the margin of the target, and the run applies `free_threshold` if you declared it. |
   | `blocked_license` | Get a license whose cap allows the target, then follow [Raise the clamp after a larger license](#raise-the-clamp-after-a-larger-license). |
   | `platform_limited` | The clamp already allows the target, and the file system is still smaller. Writes no clamp, and applies `free_threshold` if declared. Either the platform's disks cannot provision more, and a larger cache is a rebuild onto a larger disk, or a raised license cap waits for the next quorum formation, which the collection does not cause while the stored clamp already allows the target. |
   | `rebuild` or `below_floor` | The target is below the file system. Go to [Shrink by rebuilding](#shrink-by-rebuilding). |

   The full route table, with the role's variables, is in
   [Playbooks and roles](../reference/playbooks-and-roles.md#edge-cache-resize-routes).

4. Run it:

   ```sh
   ansible-playbook qumulo.cluster.edge_cache_resize -i inventory -l edge01
   ```

   The `grow` route raises the clamp, waits out the quorum re-formation and reads the file system's size again.

5. Verify with the check run again.
   It reports `within`:

   ```sh
   ansible-playbook qumulo.cluster.edge_cache_resize -i inventory -l edge01 --check
   ```

If the file system is still more than the margin below the target after the write, the run reports `platform_limited`
for that edge.
The platform's disks cannot provision more, or a license cap has not applied yet.

The playbook runs one edge at a time.
An edge that fails with a hand-off ends only that edge: the playbook goes on to the next and fails at the end, naming
each edge that failed.
When you run it without `-l`, read the end of the run for the failures.

The baseline also sets the clamp, when it changes and the license allows it.
`edge_cache_resize.yml` adds the routes for a target the clamp alone cannot reach.

### Raise the clamp after a larger license

A `blocked_license` failure names the license the edge's UUID needs and the command that finishes the job
([the message](../reference/troubleshooting.md#the-edges-cache)).
Once Qumulo has issued a license with a usable capacity of at least the target:

1. Put the new document in the edge's `host_vars` as `vault_qumulo_license_document`
   ([Put the document in the inventory](licensing.md#put-the-document-in-the-inventory)).
   A renewal is a new document for the same UUID.
   If the edge declares `qumulo_capacity.license: none`, first set `license` to `pending` or `required`, keeping the
   other keys.

2. Run the second pass.
   It applies the document, then sets the clamp, and the clamp's write applies the new cap:

   ```sh
   ansible-playbook qumulo.cluster.edge_license_complete -i inventory -l edge01 --ask-vault-pass
   ```

   The second pass also restores a rebuilt edge's held replicas.
   To apply the document without the restore, see [Skip the restore](licensing.md#skip-the-restore).

   If the `blocked_license` message says the next quorum formation is one this role does not cause, the stored clamp
   already allows the target, as on an edge that ran with `license: none`.
   The second pass then applies the license and writes no clamp, so the cap applies at the cluster's next quorum
   formation, and until then the check run reports `platform_limited`
   ([A clamp is stored already that the declared one would not raise](../reference/troubleshooting.md#the-license)).

3. Verify with a check run of `edge_cache_resize`, which reports `within` once the cap has applied.

## Shrink by rebuilding

A cache smaller than the file system needs a rebuild, because provisioned capacity never shrinks and a lower clamp does
not give capacity back.
The rebuilt edge forms again under a new UUID.

1. Lower `qumulo_capacity.clamp` to the size you want.
2. Run the playbook in check mode and read the route:

   ```sh
   ansible-playbook qumulo.cluster.edge_cache_resize -i inventory -l edge01 --check
   ```

3. Follow the route.

   **`below_floor`.** The target is below what a rebuilt edge provisions at formation (1 TB, or the platform if it is
   smaller), so a rebuild would not make the cache that small.
   Put `qumulo_capacity.clamp` back to the value it had, declare `free_threshold` at the value the check line suggests
   (`a free_threshold of about 0.50 keeps about the target in use`), and run the playbook without `--check`.
   The route fails until `free_threshold` is declared.
   Once it is, the route is `within`, which applies the threshold
   ([Keep a cache smaller than 1 TB](#keep-a-cache-smaller-than-1-tb)):

   ```yaml
   qumulo_capacity:
     clamp: 1TB   # the value it had
     free_threshold: 0.5
   ```

   `edge_cache_resize` needs a declared clamp, so do not remove it.
   The baseline fails on a clamp more than its rounding tolerance below the stored one (G-CLAMP-SHRINK).
   On a platform that forms smaller than 1 TB, set `edge_cache_resize_floor_bytes` to the capacity it forms with, in
   the edge's `host_vars`, not with `-e` on one run.

   **`rebuild`.** By default the route fails with the plan.
   Either run the rebuild yourself, or have the role run it.

   To run it yourself, limit each playbook to the edge ([Rebuild an unprotected edge](edge-rebuild.md) has the details):

   ```sh
   ansible-playbook qumulo.cluster.edge_predestroy -i inventory -l edge01
   # reinstall the edge
   ansible-playbook qumulo.cluster.edge_rebuild -i inventory -l edge01 -e qumulo_rebuild=true
   ```

   To have the role run all three phases, give it the tasks file that reinstalls the edge, by absolute path:

   ```sh
   ansible-playbook qumulo.cluster.edge_cache_resize -i inventory -l edge01 \
     -e edge_cache_resize_allow_rebuild=true \
     -e edge_rebuild_reinstall_tasks=/srv/ansible/reinstall-edge01.yml \
     -e qumulo_rebuild=true
   ```

   `edge_cache_resize_allow_rebuild` needs `edge_rebuild_reinstall_tasks`, and the rebuild needs
   `-e qumulo_rebuild=true`, the declaration that this host is the one to rebuild
   ([A declaration](respond-to-a-refusal.md#a-declaration)).
   The run needs everything a rebuild needs ([Before you start](edge-rebuild.md#before-you-start),
   [Rebuild in one run](edge-rebuild.md#rebuild-in-one-run)).
   A failed automatic rebuild ends the run
   ([why](../explanation/licensing-and-capacity.md#a-failed-automatic-rebuild-ends-the-run)).

4. License the new edge.
   The rebuilt edge has a new UUID, so it needs a new license before any target above 1 TB.
   Follow the rebuild's report, then finish the second pass ([License an edge](licensing.md)).

5. Verify.
   After a rebuild, a check run of `edge_cache_resize` reports `within`.
   After `below_floor`, the run without `--check` ends without a failure.

If you set `edge_cache_resize_floor_bytes`, set it in the edge's `host_vars`, not with `-e` on one run: `edge_rebuild`
reads it too, and without it refuses a clamp more than its rounding tolerance below 1 TB.

## Read more

- `ansible-doc -t role qumulo.cluster.edge_cache_resize` lists the role's variables, such as the margin and the
  rounding tolerance. The [role's directory](../../roles/edge_cache_resize/) has its README.
- The modules behind the routes are [capacity_clamp](../../plugins/modules/capacity_clamp.py),
  [edge_cache_route](../../plugins/modules/edge_cache_route.py) and
  [portal_eviction_settings](../../plugins/modules/portal_eviction_settings.py).
- The gaps cited above are in [the register](../reference/gaps.md).
- Why the clamp, the license cap and the 1 TB floor interact as they do:
  [Licensing and capacity](../explanation/licensing-and-capacity.md).
- Next: [Rebuild an unprotected edge](edge-rebuild.md).
