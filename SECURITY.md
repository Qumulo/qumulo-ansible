<!--
Copyright (c) 2026 Qumulo, Inc.
SPDX-License-Identifier: MIT
-->

# Security policy

## Reporting a vulnerability

Do not report a vulnerability in an issue on the public repository,
[Qumulo/qumulo-ansible](https://github.com/Qumulo/qumulo-ansible): every issue and comment there is public. Report it
privately instead, with "Report a vulnerability" on that repository's Security tab
(<https://github.com/Qumulo/qumulo-ansible/security/advisories/new>). Give the collection version, the Qumulo Core
version, the ansible-core version, the steps to reproduce and the impact you see. Leave out passwords, tokens and
keys, and your clusters' addresses and host names.

We aim to acknowledge a report within five working days and to agree a disclosure date with you once the problem is
understood.

A vulnerability in Qumulo Core itself, rather than in this collection, goes to Qumulo; see <https://qumulo.com/> for
contact details.

## Supported versions

The collection is pre-release. Only the newest release on Ansible Galaxy receives fixes, and a fix reaches you in the
next release.

## Handling of secrets

Modules never return passwords, tokens or keys unless a task asks for them explicitly, and mark them `no_log`. If you
find a path where a secret reaches task output, logs or diff output, report it as a vulnerability.
