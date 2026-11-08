<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Run the playbooks from a pipeline

This page sets up a CI/CD pipeline that runs the collection's playbooks against your clusters: it pins the collection,
hands the jobs their secrets and inventory, splits each change into a check job and an apply job, and keeps the
records a rebuild writes.
The playbooks run the same from a pipeline as from a terminal. Only the source of the secrets and of the inventory, and
what must survive between jobs, change.

## Before you start

- Have an inventory that works from a terminal: [Set up an inventory for your clusters](set-up-inventory.md).
- Have a secret store the pipeline can read, and know how the inventory looks each secret up:
  [Keep secrets out of the inventory](store-secrets.md).
- Have a runner that can reach every cluster's REST port (8000 unless `qumulo_api_port` says otherwise) and keep the
  CA certificate that signs each cluster's certificate in the checkout or the image.
- Decide where the runner keeps files between jobs. A pipeline that rebuilds edges or makes S3 keys needs this, and
  [Keep the records between jobs](#keep-the-records-between-jobs) says which files.

## Pin the collection

1. Pin the collection to the version you tested in `requirements.yml`.
   [Install and pin the collection](install-the-collection.md#pin-a-version-in-a-project) has the file and the other
   forms of a pin.

2. Install it in the pipeline's image, or in the first step of every job:

   ```sh
   ansible-galaxy collection install -r requirements.yml
   ```

When you change the pin, rebuild the image or add `--force` to the install, or a cached image or a long-lived runner
keeps the installed copy.
The runner needs ansible-core 2.20 or later, and the clusters need Qumulo Core 7.10.3 or later;
[Tested Qumulo Core versions](../reference/tested-versions.md) lists the builds the collection has passed against.

## Give the jobs their secrets

The modules mark a password, a token, a join key and a private key `no_log`, so Ansible does not print them.
Give the pipeline its secrets one of two ways.

### From a vault password

1. Keep the vault password in the pipeline's secret store.
2. At the start of the job, write it to a file outside the repository, readable only by the job's user, and remove the
   file when the job ends:

   ```sh
   vault_pass_file="$(mktemp)"
   trap 'rm -f "$vault_pass_file"' EXIT
   printf '%s\n' "$VAULT_PASSWORD" > "$vault_pass_file"
   ```

3. Pass the file to every `ansible-playbook` command:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory --vault-password-file "$vault_pass_file" --check --diff
   ```

### From the environment

1. Look each password up from an environment variable in `group_vars`, as
   [From the environment](store-secrets.md#from-the-environment) shows.
2. Have the pipeline set those variables from its secret store before the playbook starts.
   A variable that is unset reads as an empty string, and the first task that connects fails with
   `set exactly one of api_password or api_token for <host>`.

### Passwords that a Terraform output names

If the inventory comes from the `qumulo.cluster.terraform` plugin and its password store throttles, as SSM does, a large
run can fail on the lookups.
Export the passwords into the environment first, or read each one once as in step 4 of
[Point the plugin at the output](terraform.md#point-the-plugin-at-the-output).

## Read the inventory from Terraform

A pipeline that applies Terraform can read the root's output in the same job, or a later job can read a saved copy.

- To read it directly, name the root in the inventory source with `project_path`.
- To read a saved copy, write the output to a file and name it with `output_file`.
  Keep the file outside the inventory directory.
  [Read a saved output instead of running Terraform](terraform.md#read-a-saved-output-instead-of-running-terraform) has the
  command and the source file.

Set `ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true` in the job's environment.
Without it, a source that fails to parse takes its hosts out of the run, Ansible only warns, and the run goes on with
fewer clusters.

```sh
export ANSIBLE_INVENTORY_ANY_UNPARSED_IS_FAILED=true
```

## Run the check first, then the apply

Add `-l <host>` to a command to limit a job to one cluster.

1. Add a check job that runs each playbook with `--check --diff` and publishes the output where a reviewer reads it:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory --check --diff
   ```

2. Add an apply job that runs the same command without `--check --diff`.
   It can run in a later job, after someone has read the check job's output; make it wait for that approval.

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory
   ```

   Run the apply from the same commit as the check.
   The apply reads each cluster again and writes what differs then, so run the check again if the cluster may have
   changed since.

3. Give a consent to the check job and the apply job of the one change it is for, and to no other run.
   The consent refusals fire in a check run too, so a consent given only to the apply job makes the check job stop at the
   refusal, and the reviewer never sees the change's diff.
   A refusal names the flag it needs ([Respond to a refusal](respond-to-a-refusal.md)), so the check job is how the
   pipeline first learns which flag a change needs.
   - A run-level flag or variable, such as `qumulo_allow_management_disruption`, `edge_rebuild_allow_data_loss` or a
     `qumulo_replication_release` entry, goes on both jobs' command lines with `-e`. Never put one in the inventory.
   - A flag that is a key of an inventory item, such as `allow_admin_password_change` on a local user, goes in the
     inventory for both jobs and comes out once the apply is done.
   - Pass consents with `-e`, never connection variables such as `qumulo_validate_certs`: an extra variable reaches every
     cluster a play connects to, and outranks the facts the certificate step sets
     ([why](set-up-inventory.md#connect-the-hosts)).

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff -e qumulo_allow_management_disruption=true
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 -e qumulo_allow_management_disruption=true
   ```

A real run against a cluster that already matches its inventory changes nothing, apart from a few exceptions
([Verify](#verify) names them), and a rerun after a failure picks up where the first stopped.
[State management](../explanation/state-management.md#check-and-diff) says why a check in one job and the apply in a
later one see the same change, and [Refusals and guards](../explanation/refusals-and-guards.md) says why a consent
belongs to one run.

## Run many edges

`edge_predestroy.yml`, `edge_rebuild.yml`, `edge_cache_resize.yml`, `edge_license_complete.yml` and
`replication_restore.yml` run one edge at a time.
What a failure does to the other edges depends on the playbook:

- `edge_license_complete.yml` and `edge_cache_resize.yml` end only the edge that failed.
  They go on to the next edge, and their last play fails, naming each edge that failed with its message.
  The exit status is then non-zero, so the job fails and the messages say what each edge needs.
- A failed automatic rebuild in `edge_cache_resize.yml` ends the whole run
  ([why](../explanation/licensing-and-capacity.md#a-failed-automatic-rebuild-ends-the-run)).
- `edge_predestroy.yml`, `edge_rebuild.yml` and `replication_restore.yml` stop at the first edge that fails.
  Run them with `-l` for one edge per job, as [Rebuild an unprotected edge](edge-rebuild.md) does.

The second pass, `playbooks/edge_license_complete.yml`, applies every edge's license and clamp first and then restores
one edge at a time, so a run over many rebuilt edges takes as long as all their restores.
Run one second pass per edge with `-l` to restore them in parallel
([Restore several edges](restore-replica.md#restore-several-edges)).
To run the license play alone, add `--skip-tags restore`.

## Keep the records between jobs

A rebuild writes its records under `artifacts/<edge>/`, in an `artifacts` directory beside the inventory source that
defines the host, never inside it.
With `-i inventory`, that is `artifacts/` next to the `inventory` directory.
Set `edge_rebuild_artifact_dir` to move it.
A host given on the command line, as in `-i edge01,`, has no default directory: set `edge_rebuild_artifact_dir` and
`cluster_baseline_artifact_dir` for it.

1. Put the directory on storage that persists between jobs: a cache, a volume or a path on a long-lived runner.
2. Make every job that runs the replay, the baseline or the second pass of that edge use the same directory.
   Those runs read the predestroy record, the rebuild report, the restore stamps in `artifacts/<edge>/restore/`, the
   restore record and the restore's lock.
   A rebuild in one run (`-e edge_rebuild_phase=all` with `edge_rebuild_reinstall_tasks`, as in
   [Rebuild in one run](edge-rebuild.md#rebuild-in-one-run)) removes only the hand-over of the predestroy record between
   jobs.
   The report, the stamps and the restore files still have to reach the baseline and the second-pass jobs, which may
   run hours later.
3. Keep each job's time limit above the restore's lease: `replication_restore_wait_timeout` plus, for each relationship
   restored, `replication_restore_reverse_timeout` (300 seconds by default), `replication_restore_reconnect_timeout`
   (1800 by default), the authorize timeout and about 600 seconds for reads.
   The wait covers a full sync of the replica, which takes hours for a large one; its default is 86400 seconds, and
   lowering it shortens the lease.
   A restore that times out stops by itself and removes its lock.
   A job killed at its limit leaves the lock instead.
   [Run the restore from a pipeline](restore-replica.md#run-the-restore-from-a-pipeline) names the later limits.
4. Collect the secret of each new S3 key from `<cluster_baseline_artifact_dir>/<host>/secrets/` in the job that makes
   it, because the cluster shows a key's secret once ([Serve a directory as an S3 bucket](s3.md)).
   A hub's keys are written there too.
   `cluster_baseline_artifact_dir` defaults to the same `artifacts` directory, so the secrets stay in the persisted
   store after you collect them.
   Either keep that storage readable only by the pipeline's jobs and never upload it as a build artifact, or set
   `cluster_baseline_artifact_dir` to a job-local path, collect the secrets in that job and let them go with it;
   `edge_rebuild_artifact_dir` alone then keeps the rebuild records.

Losing the directory can strand a restore or turn a warning into a failure
([Why the records persist](../explanation/rebuilds.md#why-the-records-persist)).

### A lock after a killed job

"The same controller" means the same host name, boot id and PID namespace.
A pipeline job in a fresh container or on a fresh runner is therefore always another controller.
After a kill, its next run refuses until the killed run's lease runs out, which is more than 24 hours with the
defaults.
Once you know the killed job is gone, remove `artifacts/<edge>/restore/lock` by hand and the next job goes on.
The lock protects only runs that share the directory, so keep to one restore per edge at a time.
[A lock another run holds](restore-replica.md#a-lock-another-run-holds) has the rest of the lock rules.

### The license after a rebuild

After a rebuild, the new UUID needs a license.
Until the new UUID's license is in `qumulo_capacity.license_document`, or has reached the cluster, the second pass fails
that edge with the license hand-off and restores nothing on it; the other edges in the run still restore.
Commit the vaulted document before the second-pass job runs, or rerun that job once a directly issued license applies.
Nothing is lost meanwhile, because the hub keeps its copy.
[Finish the license](edge-rebuild.md#finish-the-license) describes the two ways a license arrives.

## Verify

1. Run the check job again after the apply job.
   It should report `changed=0` for every cluster and print no diff, apart from a local user with
   `update_password: always` and the actions in
   [Actions are not settings](../explanation/state-management.md#actions-are-not-settings):

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory --check --diff
   ```

2. For a pipeline that rebuilds edges, list `artifacts/edge01/` (or `<edge_rebuild_artifact_dir>/edge01/`) from a job on
   a fresh runner.
   It should hold the `predestroy-*.yml` record and, after the replay, the `rebuild-*.yml` report, with `restore/` once
   the baseline or the second pass has run.

## See also

- Before: [Install and pin the collection](install-the-collection.md), [Keep secrets out of the inventory](store-secrets.md).
- [Use a Terraform root's output as the inventory](terraform.md) for the inventory source and the password lookups.
- [Rebuild an unprotected edge](edge-rebuild.md), [Restore a rebuilt edge's data from its replica](restore-replica.md) and
  [Upgrade a fleet](upgrade.md) are the long-running jobs a pipeline most often carries.
- Why: [Refusals and guards](../explanation/refusals-and-guards.md), [State management](../explanation/state-management.md),
  [Rebuilds](../explanation/rebuilds.md).
