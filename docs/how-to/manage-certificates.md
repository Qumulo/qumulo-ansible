<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Manage the cluster's TLS certificate

This page installs a certificate chain on a cluster, renews it, and sets the TLS cipher suites the cluster accepts, all
from the inventory.
When you finish, the cluster serves the certificate you declared on its REST API and web UI, the controller verifies
the cluster with your CA, and a second baseline run changes nothing.

## Before you start

- Have an inventory that reaches the cluster: [Set up an inventory for your clusters](set-up-inventory.md).
  Give the host its `qumulo_cluster_uuid`.
  The identity guard checks it before any password is sent, also while certificate verification is off.
  If you don't have it, read it without verification and add it to the host entry in `hosts.yml`:

  ```sh
  curl -k https://10.20.0.5:8000/v1/node/state
  ```

  `GET /v1/node/state` reports the UUID as `cluster_id` and needs no login.
- Have the certificate chain in PEM, the cluster's own certificate first and then any intermediates.
  The chain must reach a root certificate authority, or the cluster refuses it.
  The certificate must name the address in `qumulo_api_host`, as a DNS name or IP address in its subject alternative
  names, and verify against your CA.
  The baseline installs a chain that doesn't without checking it, and the next step then fails to verify the cluster.
- Have the private key of the first certificate in PEM.
- Have the certificate of the CA that signed the chain, in PEM.
- Keep the chain, the key and the CA certificate outside `inventory/`, for example in `files/` beside it, because
  Ansible reads every file in an inventory directory as inventory.
- A cluster you adopted has no `qumulo_ssl` in its `host_vars`: the cluster returns neither its certificate nor its
  key ([G-SSL-READBACK](../reference/gaps.md)), so the capture lists both under `capture.supply`.
  Declare `qumulo_ssl` yourself, as the steps below do.

## Replace the factory certificate

A newly formed or rebuilt edge serves a factory certificate that no CA can verify ([G-FACTORY-TLS](../reference/gaps.md)).
Until a certificate your CA signed is installed, a run has to connect with verification off, and the `admin` password
crosses the network unverified in it.
So install the certificate first, on its own, in a run that changes nothing else, and apply the baseline only after
verification is on.

1. Encrypt the private key as one value.
   A PEM key has several lines, so use `--stdin-name`, which reads the whole file, and not `--prompt`:

   ```sh
   ansible-vault encrypt_string --stdin-name vault_edge01_tls_key < files/edge01.key
   ```

   Paste the output into `inventory/host_vars/edge01.yml`.

2. Declare the certificate in the same file.
   `ca_path_after` names the CA that signed the chain:

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_ssl:
     certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-chain.pem') }}"
     private_key: "{{ vault_edge01_tls_key }}"
     ca_path_after: "{{ inventory_dir }}/../files/qumulo-ca.pem"
   ```

3. Turn verification off for the install, in the same host's file:

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_validate_certs: false
   ```

   A `host_vars` file outranks `group_vars/qumulo_clusters.yml`, which keeps `qumulo_validate_certs: true` for every
   other cluster.
   Passing `-e qumulo_validate_certs=false` on the commands in steps 4 and 5 does the same for those runs, and then
   step 6 has no line to remove.

4. Write the play in [Serve a certificate from a play of your own](#serve-a-certificate-from-a-play-of-your-own) as
   `serve-certificate.yml`, and preview the install:

   ```sh
   ansible-playbook serve-certificate.yml -i inventory -l edge01 --check --diff --ask-vault-pass
   ```

   The `Install the certificate on the edge` task reports `changed`, and the diff lists the SHA-256 of the certificate
   each probed address serves now and the one it would serve.
   A check run installs nothing.

5. Install the certificate.
   This is the one run whose password crosses the network unverified:

   ```sh
   ansible-playbook serve-certificate.yml -i inventory -l edge01 --ask-vault-pass
   ```

   The module installs the chain and waits until the address serves it.
   The play does not switch the connection to the new CA.

6. Turn verification on.
   Remove the `qumulo_validate_certs: false` line from `host_vars/edge01.yml`.
   Make sure `qumulo_ca_path` names the same CA, as the example's `group_vars/qumulo_clusters.yml` does:

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_validate_certs: true
   qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
   ```

   Keep `qumulo_ssl.ca_path_after` in the host's file; a rebuild of the edge needs it.
   While it is set, the certificate step installs a chain without checking it against your CA first, so when the CA
   changes, change `ca_path_after` together with the chain.

7. Preview and apply the whole baseline, now over verified TLS:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff --ask-vault-pass
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --ask-vault-pass
   ```

   The baseline's `Install the certificate` task reports `ok`, because the cluster already serves the declared chain.

To verify, run the baseline once more with verification on:

```sh
ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff --ask-vault-pass
```

`Install the certificate` reports `ok`, and the run ends with `changed=0` for the host.
To check from outside Ansible, read the cluster's UUID with your CA and no `-k`:

```sh
curl --cacert files/qumulo-ca.pem https://10.20.0.5:8000/v1/node/state
```

### Serve a certificate from a play of your own

To install the certificate without the rest of the baseline, run `qumulo.cluster.ssl_certificate` in a play of your
own.
The `module_defaults` block is the header of `playbooks/baseline.yml`
([what each line does](../reference/playbooks-and-roles.md)):

```yaml
# serve-certificate.yml
- name: Serve the edge's certificate
  hosts: qumulo_clusters
  gather_facts: false
  module_defaults:
    group/qumulo.cluster.qumulo:
      api_host: "{{ qumulo_api_host }}"
      api_port: "{{ qumulo_api_port | default(8000) }}"
      api_username: "{{ qumulo_api_username | default('admin') }}"
      api_password: "{{ qumulo_api_password | default('') }}"
      api_token: "{{ qumulo_api_token | default('') }}"
      validate_certs: "{{ qumulo_validate_certs | default(true) }}"
      ca_path: "{{ qumulo_ca_path | default(omit) }}"
      expected_cluster_uuid: "{{ qumulo_cluster_uuid | default('') }}"
  tasks:
    - name: Install the certificate on the edge
      qumulo.cluster.ssl_certificate:
        certificate: "{{ qumulo_ssl.certificate }}"
        private_key: "{{ qumulo_ssl.private_key }}"
```

Run it with `ansible-playbook serve-certificate.yml -i inventory -l edge01 --ask-vault-pass`.
The play does not switch the connection afterwards.
Set `qumulo_validate_certs` and `qumulo_ca_path` for the next run yourself, as in step 6 of
[Replace the factory certificate](#replace-the-factory-certificate).

## Renew the certificate

To renew, replace the chain and the key in the host's `host_vars` and run the baseline.
The chain is usually a file beside the inventory directory, and the key is encrypted.

1. Put the new chain at the path `qumulo_ssl.certificate` reads, and encrypt the new key as in step 1 above.
   Replace the vaulted value in `host_vars/edge01.yml`.

2. List every address that must serve the new certificate.
   Without `probe`, the module compares the certificate at `qumulo_api_host` on the API port only.
   For a cluster of several nodes, list each node's address, and port 443 for the web UI:

   ```yaml
   # inventory/host_vars/edge01.yml
   qumulo_ssl:
     certificate: "{{ lookup('ansible.builtin.file', inventory_dir ~ '/../files/edge01-chain.pem') }}"
     private_key: "{{ vault_edge01_tls_key }}"
     ca_path_after: "{{ inventory_dir }}/../files/qumulo-ca.pem"
     probe: ["{{ qumulo_api_host }}:8000", "{{ qumulo_api_host }}:443"]
   ```

3. Preview, then apply:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff --ask-vault-pass
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --ask-vault-pass
   ```

   The module installs the chain when any probed address serves another certificate, and waits up to 120 seconds
   until every probed address serves the new one.
   For a slower reload, use the module's `wait_timeout` in a play of your own.

To verify, run step 3's check command again.
`Install the certificate` reports `ok`, because every probed address serves the declared certificate.

### When another CA signed the new chain

When the new chain is signed by the same CA, nothing else changes.
When another CA signed it, a connection that verifies with the old CA could not verify the cluster after the install.
Put the new chain and key where step 1 above puts them, and set `qumulo_ssl.ca_path_after` to the new CA's certificate:

```yaml
# inventory/host_vars/edge01.yml
qumulo_ssl:
  ca_path_after: "{{ inventory_dir }}/../files/new-ca.pem"
```

The other keys of `qumulo_ssl` stay as they are.
The certificate step installs the chain, and the steps after it verify the cluster with the new CA.
Afterwards, set `qumulo_ca_path` in `host_vars/edge01.yml` to the new CA, so the next run starts with it, or make the
group's CA file hold both CAs while clusters are signed by either.
Setting it in `group_vars/qumulo_clusters.yml` alone would break verification of every cluster still signed by the old
CA.

On a host with no `ca_path_after`, the step refuses a chain that does not verify against `qumulo_ca_path`, and the message names
`allow_management_disruption`.
Prefer `ca_path_after` to that flag: the flag lets the install proceed, but later tasks then fail to verify the cluster.
[Respond to a refusal](respond-to-a-refusal.md#change-the-connection) shows how to give the flag for one run, and
[Troubleshooting](../reference/troubleshooting.md#refusals-that-wait-for-consent) quotes the message.

### When the certificate is self-signed

Python 3.13 and later verify certificates strictly.
A self-signed certificate that you pin through `qumulo_ca_path` or `ca_path_after` must be a certificate authority
itself, with `basicConstraints CA:TRUE`.
The module warns when the first certificate of `certificate` is self-signed and not a CA.
Issue it with that extension, or use a certificate your CA signs and pin that CA.
[Authentication](../explanation/authentication.md#tls) explains why the controller's Python version matters.

## Pin the TLS cipher suites

`qumulo_ssl_ciphers` sets the TLS 1.2 and TLS 1.3 suites the cluster accepts.
Each list is in the order the cluster prefers its suites, and the order is part of the setting.
A list you leave out keeps the cluster's own.

1. Read the names the cluster holds now.
   A capture of an adopted cluster writes them to `qumulo_ssl_ciphers` in its `host_vars`:
   [Adopt a cluster you already run](adopt-a-cluster.md) shows the capture.
   For a cluster you did not adopt, run the baseline once with `--check --diff` and the lists you intend: the diff shows
   the lists the cluster stores now as `before`.
   Copy names from there rather than typing them.
   The cluster stores a TLS 1.2 name it does not know and never uses it ([QUIRK-061](../reference/quirks.md)), so a misspelt name is not
   refused.

2. Declare the lists you want, in order, for every cluster that shares them:

   ```yaml
   # inventory/group_vars/qumulo_clusters.yml
   qumulo_ssl_ciphers:
     tls_1_2: [ECDHE-RSA-AES256-GCM-SHA384, ECDHE-RSA-AES128-GCM-SHA256]
     tls_1_3: [TLS_AES_256_GCM_SHA384, TLS_AES_128_GCM_SHA256]
   ```

   A list may not be empty, and may not name a suite twice.
   The module fails before it sends a request.
   To keep one version's suites, leave that key out.

3. Preview, then apply:

   ```sh
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --check --diff --ask-vault-pass
   ansible-playbook qumulo.cluster.baseline -i inventory -l edge01 --ask-vault-pass
   ```

   Drop `-l edge01` to apply the lists to every cluster.

   The cluster adds one suite of its own to the end of each list that leaves it out:
   `ECDHE-RSA-AES128-SHA256` for TLS 1.2 and `TLS_AES_128_GCM_SHA256` for TLS 1.3 ([QUIRK-060](../reference/quirks.md)).
   The module adds it the same way before it compares, so with the lists above the preview shows
   `ECDHE-RSA-AES128-SHA256` at the end of the TLS 1.2 list.

To verify, run the apply command again.
`Apply the TLS cipher suites` reports `ok`, and the run ends with `changed=0`.

### Change one TLS version only

A play of your own can set one version's list and leave the other.
The `module_defaults` block is the same header as in [the certificate play](#serve-a-certificate-from-a-play-of-your-own):

```yaml
# prefer-aes256.yml
- name: Prefer the AES-256 suite for TLS 1.3, leaving TLS 1.2 as it is
  hosts: qumulo_clusters
  gather_facts: false
  module_defaults:
    group/qumulo.cluster.qumulo:
      api_host: "{{ qumulo_api_host }}"
      api_port: "{{ qumulo_api_port | default(8000) }}"
      api_username: "{{ qumulo_api_username | default('admin') }}"
      api_password: "{{ qumulo_api_password | default('') }}"
      api_token: "{{ qumulo_api_token | default('') }}"
      validate_certs: "{{ qumulo_validate_certs | default(true) }}"
      ca_path: "{{ qumulo_ca_path | default(omit) }}"
      expected_cluster_uuid: "{{ qumulo_cluster_uuid | default('') }}"
  tasks:
    - name: Set the TLS 1.3 suites
      qumulo.cluster.ssl_ciphers:
        tls_1_3: [TLS_AES_256_GCM_SHA384]
```

## Next

- [Store secrets](store-secrets.md) covers the vault options for the key and the other values in the inventory.
- [Troubleshooting](../reference/troubleshooting.md#connecting) lists the connection and verification messages.

## See also

- [Authentication](../explanation/authentication.md#tls) explains why the factory certificate cannot be verified, why
  the module probes the cluster, and what `ca_path_after` changes in the run.
- [Variables](../reference/variables.md) lists `qumulo_ssl` and `qumulo_ssl_ciphers` with their keys.
- [ssl_certificate](../../plugins/modules/ssl_certificate.py) and [ssl_ciphers](../../plugins/modules/ssl_ciphers.py)
  document every option.
- [cluster_baseline](../../roles/cluster_baseline/) runs both in step 6 of its order, the certificate first.
