#!/usr/bin/python
# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

DOCUMENTATION = r"""
module: capture_info
short_description: Read a Qumulo cluster's configuration as the inventory variables that declare it
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Reads every setting the collection's modules write and the cluster reads back, and returns it as the C(qumulo_*)
    variables of the cluster_baseline role, in the shape the role passes to those modules. A check run of the role from
    the returned variables changes nothing.
  - Changes nothing on the cluster. C(playbooks/capture.yml) writes the result to a C(host_vars) file.
  - Values are canonical and lists sorted, so two reads of an unchanged cluster return the same variables.
  - No secret is returned. The one secret read, the keytab's keys in the V(kerberos) area, is registered for masking and
    dropped. Each secret the inventory must supply to manage an area, such as a local user's password, the certificate's
    private key, the AD join password or a Nexus join key, is named in RV(capture.supply) instead.
  - The cluster's UUID is always returned; the license's own UUID and any earlier UUID never are.
  - Fails on a node that belongs to no cluster yet (state C(UNCONFIGURED)), which has nothing to capture.
options:
  gather_subset:
    description:
      - The areas to read, named as the cluster_baseline role names them. V(all) reads every area.
      - V(cluster) the name.
      - V(filesystem) the file system settings.
      - V(web_ui) the Web UI's banner and timeout.
      - V(network) the network document. With the V(tenants) area, a tenant's networks are written under its entry, each
        with C(vlan_id) when a frontend VLAN carries it and C(mtu) when that VLAN sets one, and C(managed_interfaces)
        lists neither the VLANs that carry only tenant networks nor tenant networks on the frontend bond. A network on the
        backend bond, or one no tenant holds, stays in C(qumulo_network). Without the V(tenants) area, or on a cluster
        that does not offer the tenant list, every network is written in C(qumulo_network); on a cluster with two or more
        tenants, each network a tenant holds off the backend bond carries C(tenant) by name.
      - V(dns) the DNS settings, the lookup overrides and the authoritative DNS settings.
      - V(time) the time settings.
      - V(ssl) the cipher suites. The certificate and its key are not returned (G-SSL-READBACK); RV(capture.supply) names them.
      - V(monitoring) cloud monitoring and the Nexus connection.
      - V(audit) the audit log destinations.
      - V(capacity) the clamp, the eviction threshold and the license. The license is V(required) when one is active
        for the cluster's UUID, and V(none) on an unprotected edge that holds none.
      - V(local_groups), V(local_users), V(ad) and V(roles) the identities.
      - V(kerberos) the Kerberos settings. A keytab the cluster holds is never returned; a supply line names its
        principals.
      - V(saml) the SAML settings once SAML is set up, the IdP certificate included, which is public.
      - V(identity_mappings) the user-defined mappings, as C(qumulo_user_defined_mappings). C(qumulo_identity_attributes)
        is never returned, since the cluster cannot list it (G-ID-ATTRIBUTES).
      - V(portal) the spoke portal.
      - V(directories) each directory that a share, an export, a quota, a snapshot policy, an S3 bucket, the S3 base
        path or a replication target names, and each directory above one, the root included. A directory carries its
        ACL in place of its mode when the mode cannot express the ACL.
      - V(smb) and V(nfs) the settings, the shares or exports, and whether the factory share and export are present in
        tenant 1. With the V(tenants) area, each share or export is written under its tenant's entry, and one identical
        in two or more tenants once in C(qumulo_smb_shares) or C(qumulo_nfs_exports), with C(tenants) set to V(all) when
        it is in every tenant, else to its tenants' names, sorted. The factory C(Files) share and C(/) export stay under
        tenant 1 and are never merged. Without the V(tenants) area, or on a cluster that does not offer the tenant list,
        on a cluster with one tenant they are written flat, with no tenant, and the tenant's own settings are noted in
        RV(capture.left_out); on a cluster with two or more, C(qumulo_smb_shares) and C(qumulo_nfs_exports) are written
        empty, and each tenant's shares, exports and settings are noted in RV(capture.left_out).
      - V(tenants) C(qumulo_tenants), every tenant by name, sorted, with its six services, and with what the V(network),
        V(smb) and V(nfs) areas read of it, its networks, V([]) for none, its C(smb_settings) and C(nfs_settings),
        V(inherit) without settings of its own, else the full record, and its C(smb_shares) and C(nfs_exports), each
        left out when it has none. Tenant 1 renamed carries C(previous_name) V(Default). A cluster without tenant 1 whose
        networking the host manages, with no managed interfaces and only HOST networks, gets C(previous_name) V(Default)
        on the one tenant that holds a network, and no entry V(Default), when the V(network), V(smb) and V(nfs) areas are
        read, every network has a tenant, no tenant is named V(Default), and that tenant has no C(/) export that the
        capture leaves out. A rebuild from the capture then renames the V(Default) the new edge forms with to that
        tenant. In that case C(smb_files_share) in C(qumulo_factory_defaults) is V(present) in place of V(absent) when
        the capture writes a share named C(Files) for that tenant, in any letter case, and C(nfs_root_export) when it writes
        that tenant's export at C(/). Any other cluster without tenant 1 gets an entry V(Default) with C(state)
        V(absent), unless another tenant is named V(Default), or, with no network that no tenant
        holds, a network on the backend bond stays in C(qumulo_network.frontend_networks) or O(leave_out) names
        V(network), V(smb) or V(nfs). The C(tenant 1) note in RV(capture.left_out) gives the reason for the form it
        writes. On a cluster whose protection type marks it as cloud, the services are left out.
      - V(s3) the S3 server settings, buckets, bucket policies and access keys, without their secrets.
      - V(ftp) the FTP server settings, as C(qumulo_ftp_settings). An anonymous user that names a deleted local user, or
        that is not a local user, is left out, and RV(capture.left_out) says so.
      - V(quotas) the directory quotas.
      - V(snapshots) the snapshot policies.
      - V(replication) the relationships this cluster is the source of, and the address each source records for a
        target, for the target's host.
    type: list
    elements: str
    choices: [all, cluster, filesystem, web_ui, network, dns, time, ssl, monitoring, audit, capacity, local_groups,
              local_users, ad, kerberos, saml, identity_mappings, roles, portal, directories, smb, nfs, tenants, s3,
              ftp, quotas, snapshots, replication]
    default: [all]
  inventory_clusters:
    description:
      - The inventory's clusters, each UUID mapped to its inventory host.
      - A spoke portal's hub and a replication relationship's target are declared by their inventory host. One whose
        cluster is missing here is left out, and RV(capture.left_out) says so.
    type: dict
    default: {}
  leave_out:
    description:
      - Areas, or parts of an area, that another tool owns and that are not read, such as those
        C(qumulo_terraform_owned_areas) gives to Terraform.
      - "Each that applies is noted in RV(capture.left_out): an area named here and in O(gather_subset), a part once its
        area is read, and V(audit.cloudwatch) once for each CloudWatch destination it leaves out."
      - An area is any choice of O(gather_subset) other than V(all).
      - V(network.floating_ips) reads the network document without its floating IP ranges.
      - V(dns.fqdn) reads the authoritative DNS settings without the name and whether the server answers.
      - V(audit.cloudwatch) reads the audit log destinations without the CloudWatch ones.
      - V(tenants) writes no C(qumulo_tenants), and the V(network), V(smb) and V(nfs) areas write the form O(gather_subset)
        describes without it.
      - Any other name fails before a request is sent.
    type: list
    elements: str
    default: []
extends_documentation_fragment:
  - qumulo.cluster.connection
  - qumulo.cluster.attributes
  - qumulo.cluster.attributes.actiongroup_qumulo
  - qumulo.cluster.attributes.info_module
notes:
  - "API: V(cluster) GET /v1/cluster/settings; V(filesystem) GET /v1/file-system/settings/permissions, /atime and
    /notify; V(web_ui) GET /v1/web-ui/settings; V(network) GET /v3/network; V(dns) GET /v1/dns/configs/system,
    /v1/dns/lookup-override-config and /v1/authoritative-dns/settings; V(time) GET /v2/time/settings; V(ssl) GET
    /v2/cluster/settings/ssl/ciphers and the certificate served at O(api_host); V(monitoring) GET /v1/support/settings,
    /v1/nexus/connection and /v1/nexus/registration; V(audit) GET /v2/audit/destinations/; V(capacity) GET
    /v1/capacity/clamp, /v1/portal/spokes/eviction-settings and /v1/license/status, and without a license the product
    type as M(qumulo.cluster.cluster_info) reads it; V(local_groups), V(local_users) and V(roles) as
    M(qumulo.cluster.identity_info) reads them; V(ad) GET /v1/ad/status and /v1/ad/monitor; V(kerberos) GET
    /v1/auth/kerberos-settings and /v1/auth/kerberos-keytab, whose keys are read and never returned, and which needs
    PRIVILEGE_KERBEROS_KEYTAB_READ; without it the area writes the setting and notes in RV(capture.left_out) that the
    keytab could not be read; V(saml) GET /v1/saml/settings; V(identity_mappings) GET /v1/auth/user-defined-mappings/;
    V(portal) GET /v2/portal/spokes/ and /v2/portal/hubs/; V(directories) GET /v1/files/{ref}/info/attributes and
    /v2/files/{ref}/info/acl and POST /v1/files/{ref}/info/acl/explain-set-mode, a preview that changes nothing, for
    each directory; V(smb) GET /v1/smb/settings, /v3/smb/shares/ and /v1/multitenancy/smb/settings/; V(nfs) GET
    /v2/nfs/settings, /v3/nfs/exports/ and /v1/multitenancy/nfs/settings/; V(network), V(smb), V(nfs) and V(tenants) GET
    /v1/multitenancy/tenants/, which needs PRIVILEGE_TENANT_READ, so that without it the capture of any of the four
    areas fails; V(tenants) GET /v1/cluster/protection/status; V(s3) GET /v1/s3/settings, /v1/s3/buckets/ and each
    bucket's /v1/s3/buckets/{name}/policy, and /v1/s3/access-keys/; V(ftp) GET /v0/ftp/settings; V(quotas) GET
    /v1/files/quotas/status/; V(snapshots) GET /v3/snapshots/policies/status/; V(replication) GET
    /v2/replication/source-relationships/status/ and /v2/replication/target-relationships/status/."
  - Qumulo marks the C(/v3/smb/shares/) and C(/v3/nfs/exports/) routes the V(smb) and V(nfs) areas read as preview in
    Qumulo Core 7.10.3 and 7.11.0; a later Qumulo Core release may change them.
  - The V(network), V(smb), V(nfs) and V(tenants) areas resolve tenants through the multitenancy REST API, which Qumulo
    marks as preview; a later Qumulo Core release may change it.
  - "Noted in RV(capture.left_out): a cloud cluster's tenant services, since no cloud cluster accepts a tenant write
    (G-TENANT-CLOUD); a missing tenant 1, written as the rename of V(Default) to the tenant that holds the network on
    networking the host manages, else as V(Default) absent, and not written when another tenant is named V(Default) or a
    replay could not delete it; a tenant whose identity configuration differs from tenant 1's, written in full but for
    its identity; with the V(tenants) area, or on a cluster with two or more tenants, a VLAN that carries networks of two
    tenants, or of a tenant and of no tenant; and, on a cluster with two or more tenants, a backend network a tenant
    holds (G-TENANT-BACKEND), and a network no tenant holds."
  - The cluster lists no identity that has attributes, so C(qumulo_identity_attributes) is never captured
    (G-ID-ATTRIBUTES).
  - The cluster returns neither its certificate nor the key (G-SSL-READBACK), nor the configured domain controllers
    of a domain joined before the last AD operation (G-AD-DCS), nor a Nexus join key (G-NEXUS-KEY), nor the secret of
    an S3 access key (G-S3-KEY), which RV(capture.supply) names by the key's id. A cluster other
    than an unprotected edge without an active license for its UUID needs a license document (G-LICENSE). An export
    mapped to a user or group that no longer exists is left out (G-MAPTO).
  - Each side of a portal records only the other side's addresses (G-PORTAL-HOSTS), and a replication source the
    address it reaches the target at, so RV(capture.peer_values) gives them for the peer's host, and
    C(playbooks/capture.yml) writes them as that host's C(qumulo_portal_hosts) or C(qumulo_replication_address) when
    every record of them agrees and comes from a live portal or relationship.
  - A directory, quota, snapshot policy or S3 bucket in a spoke root is the hub's data and is left out. A replication
    target directory is declared by its path alone, and what lies inside it is left out.
  - An area this cluster does not offer is listed in RV(unavailable_subsets) and in RV(capture.left_out).
  - "Registers: G-AD-DCS, G-ID-ATTRIBUTES, G-LICENSE, G-MAPTO, G-NEXUS-KEY, G-PORTAL-HOSTS, G-S3-KEY, G-SSL-READBACK,
    G-TENANT-BACKEND, G-TENANT-CLOUD."
"""

EXAMPLES = r"""
# These tasks carry no connection options. Set them once for the play with module_defaults for
# group/qumulo.cluster.qumulo, or in the QUMULO_* environment variables.
- name: Read the cluster's whole configuration
  qumulo.cluster.capture_info:
  register: captured

- name: Read only the DNS and time settings
  qumulo.cluster.capture_info:
    gather_subset: [dns, time]
  register: network_services

# Each tenant's networks, SMB and NFS settings, shares and exports go under its entry in qumulo_tenants.
- name: Read the tenants with their networks, settings, shares and exports
  qumulo.cluster.capture_info:
    gather_subset: [network, smb, nfs, tenants]
  register: tenancy

# On a cluster with two or more tenants, this writes each network a tenant holds flat with its tenant by name, and notes
# each tenant's shares, exports and settings in left_out, since only the tenants area writes them there.
- name: Read a cluster whose tenants Terraform owns
  qumulo.cluster.capture_info:
    leave_out: [tenants]
  register: captured

# Without qumulo_terraform_owned_areas, the cluster_baseline role gives Terraform the floating IP ranges of a cluster whose
# qumulo_managed_by is terraform_provider, unless its qumulo_cluster_kind is edge or baremetal, and this default does the same.
- name: Read a Terraform-managed cluster without the areas Terraform owns
  qumulo.cluster.capture_info:
    leave_out: >-
      {{ qumulo_terraform_owned_areas
         | default(['network.floating_ips'] if qumulo_managed_by | default('none') == 'terraform_provider'
                   and qumulo_cluster_kind | default(none) not in ['edge', 'baremetal'] else []) }}
  register: captured

- name: Read a cluster without its floating IP ranges, the authoritative DNS name and the CloudWatch audit destinations
  qumulo.cluster.capture_info:
    leave_out: [network.floating_ips, dns.fqdn, audit.cloudwatch]
  register: captured

# A portal's hub and a replication target are named by inventory host, so map each cluster UUID to its host. The keys are
# cluster UUIDs and the values are inventory hosts. A host with no UUID yet, one awaiting adoption, has no entry.
- name: Read a cluster that is a spoke or a replication source, naming its peers by inventory host
  qumulo.cluster.capture_info:
    inventory_clusters:
      6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11: hub01
      9533a9e2-e57a-454f-8aa0-9c77b706b7bd: edge01
  register: captured

- name: List what the capture left out, and the secrets the inventory must supply
  ansible.builtin.debug:
    msg:
      left_out: "{{ captured.capture.left_out }}"
      supply: "{{ captured.capture.supply }}"

- name: Render the capture as a host_vars file whose captured strings Ansible never templates
  qumulo.cluster.host_vars_text:
    variables: "{{ captured.capture.host_vars }}"
    header: Captured from {{ inventory_hostname }}.
    supply: "{{ captured.capture.supply }}"
    left_out: "{{ captured.capture.left_out }}"
  register: rendered
  delegate_to: localhost

- name: Write the capture as the host's variables, readable by its owner only
  ansible.builtin.copy:
    dest: "captures/{{ inventory_hostname }}.yml"
    content: "{{ rendered.text }}"
    mode: "0600"
  delegate_to: localhost
"""

RETURN = r"""
capture:
  description: What was read.
  returned: always
  type: dict
  contains:
    host_vars:
      description:
        - The C(qumulo_*) variables, sorted by name, each in the shape the cluster_baseline role takes it, with keys
          whose value is unset left out. C(qumulo_cluster_uuid) is always there.
        - No tenant id is returned anywhere; tenants are named.
        - C(qumulo_replication_relationships) lists the relationships this cluster is the source of, each entry's
          settings as M(qumulo.cluster.replication_relationship) takes them and its target named by inventory host.
      type: dict
      sample:
        qumulo_cluster_uuid: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11
        qumulo_cluster_name: edge01
        qumulo_dns: {servers: [10.0.0.2], search_domains: [corp.example.com]}
        qumulo_factory_defaults: {nfs_root_export: absent, smb_files_share: present}
        qumulo_tenants:
          - {name: Default, web_ui_enabled: true, rest_api_enabled: true, ssh_enabled: true, replication_enabled: true,
             nfs_enabled: true, smb_enabled: true, networks: [], smb_settings: inherit, nfs_settings: inherit}
    supply:
      description: Each secret the inventory must supply to manage an area, with C(variable) and C(why), sorted by variable.
      type: list
      elements: dict
      sample: [{variable: qumulo_ssl.private_key, why: "the cluster serves a certificate whose SHA-256 is ..."}]
    left_out:
      description:
        - What was read but is not in RV(capture.host_vars), or is written there in a form its note explains, such as a
          missing tenant 1 written as V(Default) absent or as the rename of V(Default), each with C(what) and C(why), sorted by
          C(what).
      type: list
      elements: dict
      sample: [{what: the floating IP ranges, why: "leave_out names network.floating_ips, as Terraform owns them"}]
    peer_values:
      description:
        - What this cluster records of another cluster's own variables, sorted. Each has C(uuid), the other cluster's;
          C(variable), V(qumulo_portal_hosts) or V(qumulo_replication_address); C(value), a list of C(address) and
          C(port) or one C(address) and C(port); C(recorded_by), this cluster's UUID; and C(unusable), why the record
          must not be copied, such as a portal that is not ACCEPTED and ACTIVE, or V(null).
      type: list
      elements: dict
      sample:
        - {uuid: 9533a9e2-e57a-454f-8aa0-9c77b706b7bd, variable: qumulo_portal_hosts, value: [{address: 10.0.1.20, port: 3713}],
           recorded_by: 6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11, unusable: null}
    from_peers:
      description:
        - The variables of this cluster's own that only its peers record, V(qumulo_portal_hosts) for a cluster in a portal
          and V(qumulo_replication_address) for a replication target.
      type: list
      elements: str
      sample: [qumulo_portal_hosts]
    spoke_roots:
      description:
        - The directory of each root of this cluster's spoke portals, whatever their state, sorted, as the V(portal) area
          reads them. A clone of this cluster leaves out what lies in them, whose data the hub holds.
      type: list
      elements: str
      sample: [/hub-projects]
    replication_targets:
      description:
        - The directory of each replication relationship this cluster is the target of and that has not ENDED, sorted,
          as the V(directories) area reads them. A clone of this cluster leaves them out.
      type: list
      elements: str
      sample: [/replicas/edge01-projects]
unavailable_subsets:
  description: Each requested area this cluster could not answer, mapped to the reason.
  returned: when an area was unavailable
  type: dict
  sample: {portal: "GET /v2/portal/spokes/ failed: HTTP 404 ..."}
"""

from ansible_collections.qumulo.cluster.plugins.module_utils.capture import AREAS, PARTS, Capture
from ansible_collections.qumulo.cluster.plugins.module_utils.common import qumulo_module, run

ARGUMENT_SPEC = {
    "gather_subset": {"type": "list", "elements": "str", "choices": ["all", *AREAS], "default": ["all"]},
    "inventory_clusters": {"type": "dict", "default": {}},
    "leave_out": {"type": "list", "elements": "str", "default": []},
}


def main():
    module = qumulo_module(ARGUMENT_SPEC)
    unknown = sorted(set(module.params["leave_out"]) - {*AREAS, *PARTS})
    if unknown:
        module.fail_json(msg=f"leave_out names {', '.join(unknown)}, which are not areas; give some of {', '.join([*AREAS, *PARTS])}")
    run(module, lambda client: Capture(client, module.params).run())


if __name__ == "__main__":
    main()
