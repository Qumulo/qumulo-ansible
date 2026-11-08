<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Authentication, trust and secrets

This page explains how a module of the collection proves who it is to a cluster, what it checks about the cluster before
it does, and why the collection treats certificates and secrets the way it does.
It does not list the connection variables or give steps. The variables are in [Inventory variables](../reference/variables.md#connection),
and the steps are in the pages linked at the end.

A module sends a credential only to a cluster it has identified, and the identity guard is how it identifies one.
A module sends a credential only over a connection it has verified, unless you turn verification off for a stated reason.
A secret lives outside the inventory's plain text, in the places that can keep it.

## How a module connects

Every module that talks to a cluster takes the same connection options, which a shared documentation fragment
([`plugins/doc_fragments/connection.py`](../../plugins/doc_fragments/connection.py)) defines once.
The modules run on the controller and call the cluster's REST API ([Architecture of the collection](architecture.md) explains why), so the
options describe one HTTPS connection: `api_host`, `api_port`, a login (`api_username` with `api_password`, or `api_token`),
`validate_certs`, `ca_path`, two timeouts, and `expected_cluster_uuid`.

In a playbook of the collection the options come from the inventory.
The header every playbook starts with sets them once for the whole play with `module_defaults` for
`group/qumulo.cluster.qumulo`, reading each from a `qumulo_*` inventory variable, so that no task repeats them.
Without the header, a module reads each option's `QUMULO_*` environment variable when the task leaves the option out.
The two sources do not mix for one option.
The header passes `api_password` and `api_token` even when the variable is unset, as an empty string, and an option that
a task passes, empty or not, is never replaced by its environment variable.
In a play that uses the header, the environment variables therefore do nothing for the options the header sets.
The exception is `ca_path`, which the header omits when `qumulo_ca_path` is unset.
This is why the client counts an empty password or token as unset: the header would otherwise make "exactly one of the two"
impossible to satisfy.

A module given a password logs in once per run, with `POST /v1/session/login`, and sends the session token it gets with
each later request.
A module given an access token sends that token instead and never logs in.
Sessions expire, so when the cluster answers 401 to a request, a module that logged in with a password logs in again
and sends that request once more.
A module that was given an access token cannot do that, because a token is not renewed by logging in, and it fails on
the first 401.

`retry_timeout` bounds how long a module keeps retrying a request that is safe to resend, after a lost connection or an
HTTP 502, 503 or 504, and the identity check and the login are retried under the same limit.
A write that may already have been applied is not resent after a lost connection, which
[Maybe applied](state-management.md#maybe-applied) explains.

## Password or token

A module needs a login, and exactly one of `api_password` and `api_token` supplies it.
The client refuses to start when both are set or neither is, and says so before it sends anything.

An access token is bound to one cluster and does not survive a rebuild (G-TOKEN in the
[gaps register](../reference/gaps.md)).
A rebuilt edge is a new cluster, so a token that worked on the old edge is dead on the new one, and the module's error
for a rejected token says so.
The edge rebuild's pre-flight check treats any access token on the old edge as state the replay cannot create again, and
refuses to go on until you acknowledge it.
A password has no such tie: the replay connects to a newly formed edge as `admin`, with the password the edge forms with
and no token.
That is why the collection recommends a password for an edge.

## The identity guard

Before a module first sends a credential, it asks the cluster at `api_host` which cluster it is.
`GET /v1/node/state` answers without a login, and reports the cluster's UUID.
The module compares that UUID with `expected_cluster_uuid`, which the header fills from the host's `qumulo_cluster_uuid`.
When they differ, the module stops with a message that names both UUIDs and says that no credentials were sent.
A node that is not yet part of a cluster reports an empty UUID, and the message calls it `UNCONFIGURED`.

The UUID is read before the login for this reason.
An address can come to answer for a different cluster than the one you wrote down, for example when a rebuilt edge takes
the old edge's address or a name is repointed.
A module that logged in first would send the `admin` password to a cluster it was never meant to configure, and
could then change that cluster.
Reading the identity first turns every such mistake into a failure that has sent nothing.
Each module run checks once, before it first sends a credential, so no credential reaches a cluster that has not passed.

The guard compares an identity that the connection reports, so it catches mistakes and does not authenticate the cluster.
It reads the UUID over the same connection that [TLS](#tls) protects, and it has no more protection than that connection
does.
It is a safeguard against the wrong cluster, and TLS is the safeguard against an impostor.

An empty `expected_cluster_uuid` switches the guard off.
A module that goes on to log in then warns that the identity guard is disabled, on every run.
The guard has no consent flag; the only way past it is to change or, for a single task, empty the expected UUID.
`cluster_guard` itself refuses an empty expected UUID, and the baseline runs it first, so the baseline does not run
against a host with no `qumulo_cluster_uuid`; only a module run on its own warns and goes on.
[Refusals and guards](refusals-and-guards.md) explains why no consent lifts it.
The UUID is an inventory fact that you, the Terraform plugin or a capture supply.
A rebuilt edge has a new UUID, so the guard stops a play that still names the old one, and the rebuild report gives the
patch to apply.
The rebuild checks the old edge's UUID during its pre-destroy phase and the new edge's after formation.

[`cluster_guard`](../../plugins/modules/cluster_guard.py) is the one module whose job is the guard.
Every module checks the UUID, but the other modules do not wait for the cluster to be ready, and a cluster that has just
started can answer its node state before it can serve a login.
`cluster_guard` in its default mode checks the UUID, and then waits until the node is `ACTIVE`, a login succeeds and the
root directory's attributes can be read, so that the tasks after it do not race the cluster's start.
A UUID that differs fails at once, and the module does not wait for a cluster it should not be talking to.
It reads only, and never reports a change.
With `wait: false` it checks the UUID once and logs in to nothing.

## TLS

The REST API is served over HTTPS, and a module verifies the certificate by default (`validate_certs: true`).
It verifies against the file `ca_path` names, or against the controller's trust store when `ca_path` is unset.
`ca_path` is the CA that signed the cluster's certificate, not the certificate itself.
That distinction matters on Python 3.13 and later, which verify certificates strictly: a self-signed certificate used as
the trust anchor must itself be a certificate authority, with `basicConstraints CA:TRUE`.
A bare self-signed leaf certificate cannot be pinned through `ca_path` there, and only `validate_certs: false`
accepts one.
`ssl_certificate` warns when the certificate it installs is self-signed and not a CA, since it could not then be pinned.
When verification fails, the error says to set `ca_path` to the cluster's CA, or to set `validate_certs: false` for a lab
cluster.

Turning verification off is a decision about what is on the wire.
With it off, the admin password, the token and every secret a task sends can be read by anyone who can sit on the path,
and the identity guard's UUID is no more trustworthy than the connection it came over.
The collection treats it as acceptable only for a lab cluster, for the run that installs a certificate on an edge that
still serves its factory certificate, and for a rebuild's replay, below.

### The factory certificate

A newly formed or rebuilt edge serves a factory certificate, a bare self-signed leaf, and no CA can verify it
([G-FACTORY-TLS](../reference/gaps.md)).
On Python 3.13 and later even a CA file holding that leaf is refused as a trust anchor.
So the first run against such an edge cannot verify, and the collection gives that run a narrow way through instead
of an instruction to disable verification for good.

You declare the certificate the edge should serve in `qumulo_ssl`, with `ca_path_after` naming the CA that signed it.
[Manage certificates](../how-to/manage-certificates.md#replace-the-factory-certificate) installs it in a run of its own
that changes nothing else, and applies the baseline afterwards with verification on.
The reason is the password.
A baseline run that starts with `qumulo_validate_certs: false` sends the `admin` password over unverified TLS for every
step before the certificate step: the guard, network, DNS, time, name, file-system and web UI steps listed below.
Installing the certificate first confines the unverified connection to a run that sends no other setting.

The role can also switch inside one run, and the edge_rebuild replay uses that, because a replay connects to the edge
it has just rebuilt, which serves its factory certificate, within the run that rebuilt it.
The replay starts with `qumulo_validate_certs: false`.
The baseline's certificate step installs the declared certificate, and then sets `qumulo_ca_path` to `ca_path_after` and
`qumulo_validate_certs` to `true`, for the rest of the run.
It sets them as facts under their inventory names, because the header reads those variables for every task that follows.
Every later step, including those that send passwords such as an Active Directory join, therefore goes over verified TLS.
In a check run that would change the certificate, the old certificate is still served, so the step leaves the switch alone.
Verification is off only for the steps before the certificate step, and it comes back by itself.
The facts last for the run only.
Afterwards the inventory has to say what the run switched to: remove the `qumulo_validate_certs: false`, and set
`qumulo_ca_path` to the CA in `ca_path_after`, or the next run starts unverified again, or fails to verify.
A `qumulo_validate_certs: false` left in `host_vars` keeps the guard, network, DNS, time, name, file-system and web UI
steps unverified on every later run, and after a renewal from another CA a `qumulo_ca_path` that still names the old CA
fails the next run at its first step.

When verification is on, [`ssl_certificate`](../../plugins/modules/ssl_certificate.py) checks before it installs, by
serving the new chain to itself on the controller, that the chain verifies against `ca_path` for `api_host`, and refuses
one that does not unless `allow_management_disruption` is set.
The certificate step sets that option whenever `qumulo_ssl.ca_path_after` is declared, because that step is the switch.
A certificate that later tasks cannot verify would cut the play off from the cluster it just configured, and the module
declines to cause that unannounced.
The same applies to a renewal signed by a different CA, where `ca_path_after` names the new CA.

### Why not `-e qumulo_validate_certs=false`

`-e qumulo_validate_certs=false` does not have the same effect.
An extra variable applies to every host of the play, and the baseline reads a portal's hub connection from the hub's
variables, so the hub is then unverified too.
An extra variable also outranks the facts a run sets, so the certificate step cannot turn verification back on.
Every password the run sends after the certificate, the domain join's among them, would go over unverified TLS.
[Inventory variables](../reference/variables.md#precedence) gives the precedence rule that causes this.

The edge rebuild gets the same effect without that risk.
With `edge_rebuild_validate_factory_certs: false` in the new edge's `host_vars`, the replay switches verification off
for that one host.
The hub stays verified throughout, and the certificate step turns verification back on for the edge with
`qumulo_ssl.ca_path_after`.
The option exists beside the global switch because it is scoped to one cluster and ends at a known step.
[Rebuild an unprotected edge](../how-to/edge-rebuild.md) gives the steps.

### Why a certificate is compared by probing it

The cluster has no call that returns the certificate it serves or its private key ([G-SSL-READBACK](../reference/gaps.md)), so
`ssl_certificate` cannot read what is installed and compare it with what you declared.
It connects to each address in `probe` instead, and compares the certificate that address serves with the first
certificate of the chain you declared.
That probe stands in for a read-back.
The module never compares keys, and does not need to: the cluster refuses a key that does not match its certificate,
so a served certificate that matches the declared one means its key is installed.
With no `probe`, the module compares only `api_host` on `api_port`.
An address it does not probe is not compared, so a node or port left out can keep serving the old certificate unnoticed.

After installing, the module waits until every probed address serves the new certificate, because the cluster takes a moment
to reload it.
A task that followed straight away could otherwise run against the old certificate.
[Manage the cluster's TLS certificate](../how-to/manage-certificates.md#renew-the-certificate) shows how to declare, replace
and renew one.

## Where secrets live

The collection never needs a secret in plain text in the project.
The secrets an inventory holds are the cluster passwords and tokens, the certificate key, local user passwords, the
Active Directory join password, the keytab of a standalone Kerberos KDC, the license document and the Nexus
credentials.
[Keep secrets out of the inventory](../how-to/store-secrets.md) shows how to encrypt each with Ansible Vault or look it up
when a run starts.
A capture writes no secret either. It reads one: the `kerberos` area reads the keytab's keys with its entries, to name
the principals, registers them for masking and never writes them. It names each secret the inventory must supply, in
comments in the file it writes ([What capture and clone read and write](../reference/capture-and-clone.md)), and it does
not write the connection variables into the file.

The modules mark each secret option `no_log`, among them `api_password`, `api_token`, a local user's `password`, the
certificate's `private_key`, the license document's `license_key`, the Active Directory `join_password`, the Kerberos
`keytab` (the keytab file in base64), a Nexus `join_key` and the Nexus API token, an unprotected edge's
`admin_password`, a snapshot lock's `private_key` and an upgrade's `image_secret`.
Ansible therefore does not print them in task output or in logs.
`kerberos_keytab` goes further, because the cluster returns the keys of the keytab it holds, as base64: no result, diff
or message carries a key, the module compares keys through one digest, and it registers each key with
`register_secret`, where ansible-core provides it, so that Ansible masks it too.
Where ansible-core provides `register_secret`, the client also registers the session token a login returns, so that
Ansible masks it too.
A URL that an error message quotes has its query string and user information replaced before the message is shown.

The Terraform inventory plugin applies the same rule to its input.
A cluster's admin password is never in the Terraform output the plugin reads.
The output gives a `password_ref`, the type and id of the place that holds the password, and `group_vars` resolve it with
a lookup into `qumulo_api_password`.
The plugin refuses a field whose name looks like a secret, such as `password` or `api_token`, and reads, caches and shows
none of its value, so a secret put into the output under a name such as `password` is refused, and its value never
reaches a variable, the cache or a message.
The output carries no secret, so it need not be marked `sensitive` in Terraform.
[Use a Terraform root's output as the inventory](../how-to/terraform.md) has the lookup for each store.

## Passwords the cluster does not show back

The cluster never returns a local user's password, so the collection cannot tell whether the stored one matches the one
you declare ([G-PASSWORD](../reference/gaps.md)), so the collection sends one only when `update_password` says to.
[Comparing in the form the cluster reads back](state-management.md#comparing-in-the-form-the-cluster-reads-back)
explains how this follows from the way modules compare, and gives the `on_create` and `always` behaviour.

The `admin` password carries one more consequence.
The server applies it to the console and SSH password of every node as well as to the REST API ([G-ADMIN-OS](../reference/gaps.md)).
Changing it is a change to how people log in to the machines, not only to a setting.
`local_user` therefore refuses to change `admin`'s password unless `allow_admin_password_change` is set.
When the change is to the password the task itself connects with, later tasks must connect with the new one, and
`allow_management_disruption` says that you know this.
The module reports `connection_password_changed` so that a play can switch to the new password for the rest of its run.
[`playbooks/admin_password_rotate.yml`](../../playbooks/admin_password_rotate.yml) does this.
Because a password cannot be read back, it first tries to log in with the new password: a login that works means the
password is already rotated, so a second run changes nothing.
[Rotate the admin password](../how-to/rotate-admin-password.md) gives the steps.

## Access tokens the collection makes

[`access_token`](../../plugins/modules/access_token.py) makes, keeps, rotates and revokes the access tokens of a user.
The cluster returns a token's bearer, the string that logs in, only in its answer to the request that makes the token,
so the collection can never read one back ([G-TOKEN](../reference/gaps.md)).
The cluster also stores no name for a token, so the module manages a user's tokens as one set, and a user holds at most
two, expired ones counted.

### Where the bearer appears

The module puts the bearer in two places, both in the run that made the token: in `bearer_token` of the task's result,
also when a later step of that run fails, and in `access-token-<user>-<id>.yml` in `secret_destination`, a file readable only by its owner in a directory readable
only by its owner.
No diff, note, warning or message holds it, and the module quotes none of the cluster's answers to the create.
`identity_info` lists every token with its user, creator, id, times and `enabled`, and never a bearer, because the
cluster's list route returns none.
Where ansible-core provides `register_secret`, the module also registers the bearer so that Ansible masks it.

### Why both `no_log` and a file are required

The two protect against different losses.
Without `no_log`, Ansible prints the task's result, bearer included, to the terminal and to every log and callback.
Without a file, the bearer exists only in that task's result, and a play that fails before it stores the result has lost
the bearer for good: the cluster will not show it again, and a rotation has deleted the old token by then.
The module therefore refuses to make a token in a task without `no_log`, or without `secret_destination`, before it
writes anything, in check mode too.
It also makes the directory, or makes the one there private, and writes and removes a probe file in it before it makes
the token.

`no_log` hides a task's result, its notes and diff included, and keeps its warnings; when the task fails, ansible-core
still prints its `msg`, so a refusal is on screen.
The module reports a token that is expired, and stays so, as a warning, because a note would be hidden.

### Why a rotation makes the new token before it deletes the old

With `state: rotated`, the module makes the new token, writes its bearer to the file, confirms the token in a listing,
and only then deletes the tokens the user held before.
The order protects two things.
It protects the play's own connection, because the token the play connects with is deleted last, and nothing is sent
after it.
It also protects the user's tokens when a step fails: until the new token is stored and confirmed no old token is
touched, and a failure after that keeps the old tokens not yet deleted.
Such a failure can leave the user with two tokens, and the module has no way to delete only the old one.
The failed result still carries `access_token_id`, `secret_file` and `bearer_token`, and the message, which the module
also gives as a warning, names the new token, its file and the old tokens it kept, and says how to delete an old token
by its id outside the collection, with `qq auth_delete_access_token` or `DELETE /v1/auth/access-tokens/{id}`
([G-TOKEN](../reference/gaps.md)).
A user who already holds two tokens has no room for a third, so a rotation is refused before any write.
The message names each token, marks the one the task connects with, and gives the same delete-by-id way out, or
`state: absent` then `state: present` to replace both.
A rotation with `enabled: false` is refused when the token the task connects with is among the old ones, because the
play would be left with no token to connect with.

The order does not protect the token's other consumers.
The old tokens are deleted at the end of the same task, so every other holder of the user's token loses access until it
holds the new bearer, the same gap an S3 key's replacement leaves ([G-S3-ROTATE](../reference/gaps.md)).
That is why each consumer should have a user of its own: one user's tokens are one set, and a rotation for one consumer
cuts off the rest.
A token holds the roles of its user, so the token of a member of Administrators is a full administrator's credential,
and its file deserves the care an admin password gets.

### The token the play connects with

A task that connects with `api_token` asks the cluster which token that is, with `GET /v1/session/who-am-i`, whose
`access_token_id` names it.
A task that would delete that token, disable it, or give it an expiration less than an hour after the controller's
clock is refused, unless it sets `allow_management_disruption`.
The hour covers the rest of the play, and a controller whose clock is behind the cluster's.
With the option set, the module sends the write that deletes, disables or expires that token after its other writes,
and nothing after it, and reports `connection_token_changed` when it deleted the token.
The play must connect with the new `bearer_token` from then on.
A token that the task disabled or expired is not reported there; `access_tokens` in the result shows its `enabled` and
`expiration_time`.
[Rotate the access token a host connects with](../how-to/rotate-access-token.md)
gives the play.
The admin password has the same rule, which [above](#passwords-the-cluster-does-not-show-back) describes.

### Files that stay, and a rebuild

The module removes no file of a token it kept.
The file of a token that a later task deletes stays, with a bearer that no longer works, and the newest file of a user
holds the bearer of the token the user was last given.
A token is also bound to its cluster, so a rebuilt cluster has none of the old tokens, and every file of the old
cluster's tokens is dead with it.

## The Nexus token and join key

A Nexus API token is a different credential from a cluster login.
It belongs to a user of the Nexus service, it is sent to Nexus as a bearer token, and it is never sent to the cluster.
What it may do depends on that user's role in the Nexus organization, so each module that calls Nexus says which calls
it makes.
Calls to Nexus always verify the certificate, and the cluster's `validate_certs` and `ca_path` do not apply to them, because
Nexus presents a publicly trusted certificate.

`nexus_registration` registers a cluster with a join key.
A join key is single use, the module sends it only when the cluster is not yet registered, and never returns it.
Registration is decided by the cluster's status, so a REGISTERED cluster is left alone and a rerun neither spends a key
nor mints one.
A key spent on a failed registration cannot be used again ([G-NEXUS-KEY](../reference/gaps.md)).
With `nexus_api_token` set and no `join_key`, the module mints a key in Nexus and uses that.
[Licensing and capacity](licensing-and-capacity.md) covers what Nexus is for.

Registration writes send no `If-Match`, because the cluster's background rotation of its Nexus secret changes the shared
ETag and no registration write depends on what it read
([How a write is sent](state-management.md#how-a-write-is-sent)).
For the same reason, no other monitoring or Nexus setting should change while a registration runs.

## Learn more

- [Set up an inventory for your clusters](../how-to/set-up-inventory.md) gives every host its connection.
- [Keep secrets out of the inventory](../how-to/store-secrets.md) encrypts or looks up each secret.
- [Manage the cluster's TLS certificate](../how-to/manage-certificates.md) replaces the factory certificate and renews one.
- [Rotate the admin password](../how-to/rotate-admin-password.md) changes the password that logs every module in.
- [Rotate the access token a host connects with](../how-to/rotate-access-token.md)
  replaces the token a play logs in with, and moves the new bearer into the vault.
- [Register a cluster with Nexus](../how-to/nexus.md) registers a cluster with a join key or an API token.
- [Rebuild an unprotected edge](../how-to/edge-rebuild.md) replays the certificate switch for a rebuilt edge.
- [Inventory variables](../reference/variables.md#connection) lists the connection variables and their defaults.
- [Troubleshooting](../reference/troubleshooting.md#connecting) quotes the messages a failed connection gives.
- [Refusals and guards](refusals-and-guards.md) explains why the identity guard has no consent flag.
