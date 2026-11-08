<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# qumulo.cluster

An Ansible collection for day-2 configuration of Qumulo Core clusters: shares, exports, identity, portals, quotas,
snapshot policies, licensing and monitoring, declared in an inventory and applied through the Qumulo REST API.

## What this repository is

This repository holds the files each release of the collection ships. Each release is one commit, tagged `vX.Y.Z`, and
the commit at the head of `main` is version 0.1.0. [CHANGELOG.md](CHANGELOG.md) lists what each release changed.

The collection is developed elsewhere, so a pull request opened here is not merged. To report a problem or ask for a
change, [open an issue](https://github.com/Qumulo/qumulo-ansible/issues/new/choose).

## Install from Galaxy

```sh
ansible-galaxy collection install qumulo.cluster
```

Install from Galaxy, not from this repository. `ansible-galaxy collection install git+...` does not work here, because
the repository holds the released files without a `galaxy.yml` or the `MANIFEST.json` that Galaxy's artifact adds.

The collection needs Qumulo Core 7.10.3 or later on the clusters it manages, and ansible-core 2.20 or later on the
controller. It is pre-release: interfaces may change before 1.0.0, and nothing here is supported for production use yet.

To use the same version on every run and every machine, pin it in `requirements.yml`:

```yaml
collections:
  - name: qumulo.cluster
    version: 0.1.0
```

## Documentation

The documentation is at https://github.com/Qumulo/qumulo-ansible/blob/main/docs/README.md. Its pages are also in this repository's [docs/](docs/README.md) directory.

## Tested Qumulo Core versions

[What each column means](https://github.com/Qumulo/qumulo-ansible/blob/main/docs/reference/tested-versions.md).

| Qumulo Core | Build | Docker edge | CNQ | Hardware | VM edge | Edge | Formation | Pair | AD | Rebuild | Tested |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7.11.0 | 393623.1.21 | pass | pass | pass | pass | pass | pass | pass | pass | pass | 2026-11-07 |
| 7.10.3.1 | 385145.1.101 | pass | pass | pass | not-tested | pass | pass | pass | pass | pass | 2026-11-06 |
| 7.10.3 | 385145.1.82 | pass | pass | pass | pass | pass | pass | pass | pass | pass | 2026-11-08 |
| 7.9.1 | 355587.1.19 | not-tested | not-tested | not-tested | fail |  |  |  |  |  | 2026-11-07 |
|  |  |  |  |  | ubuntu-26.04/host-managed: contract, ssl_ciphers, upgrade |  |  |  |  |  |  |
| 7.8.4.3 | 349102.1.48 | not-tested | not-tested | not-tested | fail |  |  |  |  |  | 2026-11-07 |
|  |  |  |  |  | ubuntu-26.04/host-managed: contract, ssl_ciphers, upgrade |  |  |  |  |  |  |
| 7.7.0.2 | 334623.1.61 | not-tested | not-tested | not-tested | fail |  |  |  |  |  | 2026-11-07 |
|  |  |  |  |  | ubuntu-26.04/host-managed: contract, smb_info, smb_share, ssl_ciphers, time_settings, upgrade |  |  |  |  |  |  |
| 7.6.0 | 329337.1.4 | not-tested | not-tested | not-tested | fail |  |  |  |  |  | 2026-11-07 |
|  |  |  |  |  | ubuntu-26.04/host-managed: contract, s3_bucket, s3_round_trip, smb_info, smb_share, snapshot, ssl_ciphers, time_settings, upgrade |  |  |  |  |  |  |

## Filing an issue

[Open an issue](https://github.com/Qumulo/qumulo-ansible/issues/new/choose) and choose the form that fits: a bug report or a request. Give:

- the collection version (`ansible-galaxy collection list qumulo.cluster`), the Qumulo Core version of the cluster, and
  the ansible-core version (`ansible --version`);
- the platform: a hardware cluster, Cloud Native Qumulo (CNQ), or a virtual or hardware edge;
- the task that failed and the module's output.

Leave out passwords, tokens and keys, your clusters' addresses and host names, and anything else you would not publish.
This repository is public, and so is every issue and comment in it.

The maintainers read the issues. A fix reaches you in a release, which [CHANGELOG.md](CHANGELOG.md) announces.

## Security

Do not open an issue for a vulnerability. Report it as [SECURITY.md](SECURITY.md) says.

## License

MIT, except the plugins that import ansible-core, which are GPL-3.0-or-later. See [LICENSE](LICENSE) and
[LICENSES/GPL-3.0-or-later.txt](LICENSES/GPL-3.0-or-later.txt).
