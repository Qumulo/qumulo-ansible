<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Install and pin the collection

This page installs the `qumulo.cluster` collection on a controller, pins it to a version you choose, and checks that
Ansible finds it.

## Before you start

You need:

- a controller with ansible-core 2.20 or later;
- Qumulo Core 7.10.3 or later on the clusters you will manage;
  [Tested Qumulo Core versions](../reference/tested-versions.md) lists the builds the collection has passed against;
- HTTPS access from the controller to each cluster's REST API, on port 8000 unless `qumulo_api_port` says otherwise;
- HTTPS access from the controller to Ansible Galaxy, `galaxy.ansible.com`, and to the Amazon S3 storage it redirects
  downloads to, to install the collection.

The modules need only the Python standard library and what ansible-core installs with it, so no `pip` package is
needed. They run on the controller under ansible-core's own Python, which [the inventory](set-up-inventory.md) sets
with `ansible_python_interpreter`.
The `qumulo.cluster.terraform` inventory plugin can also run the `terraform` executable. You need it only when a
`qumulo_terraform.yml` source sets `project_path`, so that the plugin runs `terraform output` itself. A source that
sets `output_file` does not run it ([Use a Terraform root's output as the inventory](terraform.md)).

Later steps also need the password of the user the modules log in as (`admin` unless `qumulo_api_username` says
otherwise), or an access token ([Authentication](../explanation/authentication.md)), and, unless a cluster still
serves its factory certificate, the certificate of the CA that signed its TLS certificate.
A factory certificate has no CA that can verify it; see
[Replace the factory certificate](manage-certificates.md#replace-the-factory-certificate).
Neither is needed to install.

## Install the newest release

```sh
ansible-galaxy collection install qumulo.cluster
```

Without a version, the install takes the newest release on Ansible Galaxy.
It installs the collection under the first collections path Ansible uses, which is `~/.ansible/collections` unless you
have changed `collections_path`.
Once any version is installed, running the same command again changes nothing; add `--upgrade` to move to the newest
release.

## Pin a version in a project

Pin the collection in `requirements.yml` so that every run, and every machine, uses the same release.
The collection is pre-release, and its interfaces may change from one release to the next before 1.0.0, so pin the
version you tested.

```yaml
# requirements.yml
collections:
  - name: qumulo.cluster
    version: 0.1.0
```

Replace `0.1.0` with the version you tested.
Install it:

```sh
ansible-galaxy collection install -r requirements.yml
```

To pin on the command line, add a version spec after the name and a colon:

```sh
ansible-galaxy collection install 'qumulo.cluster:==0.1.0'
```

To change the pin, edit `version` and run the install again. An installed version that does not match the pin is
replaced; one that matches is left alone.

## Install into the project

By default the collection goes to your user account's collections directory, and every project on the controller
shares it. To keep it with one project, tell Ansible to look in a directory of the project:

```ini
# ansible.cfg, next to your playbooks
[defaults]
collections_path = ./collections
```

Then install from the project directory:

```sh
ansible-galaxy collection install -r requirements.yml -p collections
```

The install goes to the first `collections_path` entry, so `-p collections` is optional once `ansible.cfg` is in
place. Either way it creates `collections/ansible_collections/qumulo/cluster`. The `collections_path` setting names
the directory that holds `ansible_collections`, not the collection itself.

## Install without internet access

On a machine that reaches Galaxy, download the version your `requirements.yml` pins:

```sh
ansible-galaxy collection download -r requirements.yml -p qumulo-download
```

The directory holds the collection's tarball and a `requirements.yml` that names it. Copy the directory to the
controller, and install from inside it:

```sh
cd qumulo-download
ansible-galaxy collection install -r requirements.yml
```

Add `-p` with the project's collections directory to install there, as
[Install into the project](#install-into-the-project) says.

## Install in a pipeline

Run the install in the pipeline's image or in the first step of the job, from the same `requirements.yml` you use on
a workstation, so that a pipeline run and a local run use the same code.
[Run the playbooks from a pipeline](pipeline.md) covers the rest of a pipeline: secrets, the inventory and the
check-then-apply split.

## Verify the install

Check that Ansible finds the collection:

```sh
ansible-galaxy collection list qumulo.cluster
```

```text
# /home/you/.ansible/collections/ansible_collections
Collection     Version
-------------- -------
qumulo.cluster 0.1.0
```

The version is the release you installed. [CHANGELOG.md](../../CHANGELOG.md) lists what each release changed.

Check that it lists the modules:

```sh
ansible-doc -l qumulo.cluster
```

The output has one line for each module, starting with `qumulo.cluster.access_token`.
If `ansible-galaxy collection list` prints nothing, or `ansible-doc` says `unable to locate collection
qumulo.cluster`, Ansible is not searching the directory you installed into.
Run `ansible-config dump | grep COLLECTIONS_PATHS` to see where it looks, and match `-p` to one of
those directories.

## Next

- [Set up an inventory for your clusters](set-up-inventory.md) points the collection at your clusters.
