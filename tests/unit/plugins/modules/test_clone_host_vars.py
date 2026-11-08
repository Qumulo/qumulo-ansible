# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.clone_host_vars, fed a scripted capture_info result: the unique-item rules, each one explicit.

The source is a richly configured edge in a portal and a replication relationship, joined to a domain, registered
with Nexus and licensed, with a share and an export on its spoke roots and a directory it receives replication into.
Everything but what belongs to one cluster is copied verbatim.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path

import pytest
import yaml
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.clone import RULES, Clone
from ansible_collections.qumulo.cluster.plugins.modules import clone_host_vars
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import HOSTED_WITHOUT_TENANT_1_CAPTURE

SOURCE_UUID = "6f1c2c1e-5c3a-4c56-9d2e-3f8c1b0a7e11"
CLONE_UUID = "1b2c33f0-9a8b-4c56-9d2e-3f8c1b0a7e22"
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
DR_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
SECRET_NAMES = ("password", "join_key", "license_document", "private_key", "certificate", "token")

DNS = {"servers": ["10.0.0.2"], "search_domains": ["corp.example.com"]}
ROOT = Path(__file__).resolve().parents[4]
SCRATCH_SHARE = {
    "share_name": "scratch",
    "tenant_id": 1,
    "fs_path": "/local/scratch",
    "permissions": [{"type": "ALLOWED", "trustee": "local:Users", "rights": ["ALL"]}],
}
# On the declared portal's spoke root, whose data the hub holds.
PROJECTS_SHARE = {
    "share_name": "projects",
    "tenant_id": 1,
    "fs_path": "/projects/",
    "permissions": [{"type": "ALLOWED", "trustee": "world:Everyone", "rights": ["READ"]}],
}
SCRATCH_EXPORT = {"export_path": "/scratch", "tenant_id": 1, "fs_path": "/local/scratch", "restrictions": [{"host_restrictions": [], "read_only": True}]}
# On the spoke root of a portal the capture only noted, which only spoke_roots names.
CACHE_EXPORT = {"export_path": "/cache", "tenant_id": 1, "fs_path": "/cache/sub", "restrictions": [{"host_restrictions": [], "read_only": True}]}
LOCAL_DIRECTORIES = [{"path": "/", "mode": "0755"}, {"path": "/local", "mode": "0755"}, {"path": "/local/scratch", "mode": "0775"}]
# The directory the source receives replication into, declared by its path alone.
INCOMING = {"path": "/local/in"}
# Beside it, with a name it begins with, and kept.
INBOX = {"path": "/local/inbox", "mode": "0770"}
INBOX_QUOTA = {"path": "/local/inbox", "limit": 2000000000}
INBOX_ACL = {"path": "/local/inbox", "aces": [{"type": "ALLOWED", "trustee": "local:svc-backup", "rights": ["ALL"], "flags": []}]}
# A quota and a snapshot policy on it, which keep history on a replication target.
INCOMING_QUOTA = {"path": "/local/in", "limit": 1000000000000}
INCOMING_POLICY = {"policy_name": "in-daily", "path": "/local/in", "enabled": True}
SCRATCH_BUCKET = {"name": "scratch", "path": "/local/scratch"}
SCRATCH_BUCKET_POLICY = {"bucket": "scratch", "policy": {"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": "local:svc-backup"}, "Action": "s3:*"}]}}
# In the declared portal's spoke root, with a policy that goes with it.
PROJECTS_BUCKET = {"name": "projects", "path": "/projects/b"}
PROJECTS_BUCKET_POLICY = {
    "bucket": "projects",
    "policy": {"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": "world:Everyone"}, "Action": "s3:GetObject"}]},
}
# On the directory the source receives replication into.
INCOMING_BUCKET = {"name": "incoming", "path": "/local/in"}
INCOMING_BUCKET_POLICY = {
    "bucket": "incoming",
    "policy": {"Statement": [{"Effect": "Allow", "Principal": {"Qumulo": "local:svc-backup"}, "Action": "s3:GetObject"}]},
}
ACCESS_KEYS = [{"user": "local:svc-backup", "access_key_id": "00000000000000000001"}]
ACCESS_KEY_SUPPLY = {"variable": "qumulo_s3_access_keys[00000000000000000001]", "why": "the cluster returned the secret only when it made the key"}
USERS = [
    {"name": "admin", "primary_group": "Users", "uid": 0},
    {"name": "guest", "primary_group": "Guests", "uid": 65534},
    {"name": "svc-backup", "primary_group": "Users", "uid": 5001},
]
COPIED = {
    "qumulo_dns": DNS,
    "qumulo_time": {"source": "API_TIME_SOURCE_NTP", "ntp_servers": ["ntp1.corp.example.com"], "use_ad_for_primary": False},
    "qumulo_ad": {"domain": "corp.example.com", "domain_netbios": "CORP", "ou": "OU=Edges,DC=corp,DC=example,DC=com"},
    "qumulo_monitoring": {"enabled": True, "period": 60, "nexus": {"nexus_enabled": True, "nexus_host": "nexus.example.com"}},
    "qumulo_capacity": {"clamp": 4000000000000, "free_threshold": 0.15, "license": "required"},
    "qumulo_local_groups": [{"name": "edge-admins", "gid": 5000, "members": ["svc-backup"]}],
    "qumulo_local_users": USERS,
    "qumulo_roles": [{"name": "Backup-Operators", "description": "Backups", "privileges": ["PRIVILEGE_SNAPSHOT_READ"]}],
    "qumulo_role_members": [{"role": "Observers", "members": ["local:edge-admins"]}],
    "qumulo_user_defined_mappings": [{"down_level_logon_name": "CORP\\alice", "ldap_name": "alice"}],
    "qumulo_identity_attributes": [{"trustee": "ad:CORP\\alice", "home_directory": "/home/alice"}],
    "qumulo_filesystem_settings": {"permissions_mode": "CROSS_PROTOCOL", "atime_enabled": False},
    "qumulo_web_ui_settings": {"login_banner": "Authorized use only", "inactivity_timeout": 30},
    "qumulo_directories": [*LOCAL_DIRECTORIES, INBOX],
    "qumulo_file_acls": [INBOX_ACL],
    "qumulo_smb_settings": {"signing_required": True},
    "qumulo_smb_shares": [SCRATCH_SHARE],
    "qumulo_nfs_settings": {"v4_enabled": True},
    "qumulo_nfs_exports": [SCRATCH_EXPORT],
    "qumulo_factory_defaults": {"nfs_root_export": "absent", "smb_files_share": "absent"},
    "qumulo_quotas": [INBOX_QUOTA, {"path": "/local/scratch", "limit": 500000000000}],
    "qumulo_snapshot_policies": [{"policy_name": "scratch-hourly", "path": "/local/scratch", "enabled": True}],
    "qumulo_s3_settings": {"enabled": True, "secure": True},
    "qumulo_s3_buckets": [SCRATCH_BUCKET],
    "qumulo_s3_bucket_policies": [SCRATCH_BUCKET_POLICY],
    "qumulo_ftp_settings": {"enabled": True, "anonymous_user": "local:svc-backup", "greeting": "Authorized use only.\n"},
}
PORTAL = {"hub": "hub01", "type": "read_write", "roots": [{"spoke_root": "/projects", "hub_root": "/projects"}]}
RELATIONSHIPS = [
    {"source_path": "/local/scratch/", "target": "dr01", "target_path": "/edge01/scratch/", "replication_enabled": True},
    {"source_path": "/projects/", "target": "dr01", "target_path": "/edge01/projects/", "replication_enabled": False},
]
PASSWORD_WHY = "the cluster never returns a password; give the user's password"
SPOKE_ROOT_NOTE = {"what": "the directory /projects/a", "why": "it lies in the spoke root /projects, whose data the hub holds; the hub's capture declares it"}
CAPTURE = {
    "host_vars": {
        "qumulo_cluster_uuid": SOURCE_UUID,
        "qumulo_cluster_name": "edge01",
        "qumulo_network": {"frontend_networks": [{"name": "Default", "floating_ip_ranges": ["10.0.0.20-21"]}], "managed_interfaces": "auto"},
        "qumulo_portal": PORTAL,
        "qumulo_replication_relationships": RELATIONSHIPS,
        **COPIED,
        "qumulo_directories": [*LOCAL_DIRECTORIES, INCOMING, INBOX],
        "qumulo_file_acls": [{**INBOX_ACL, "path": "/local/in"}, INBOX_ACL, {**INBOX_ACL, "path": "/projects/a"}],
        "qumulo_smb_shares": [PROJECTS_SHARE, SCRATCH_SHARE],
        "qumulo_nfs_exports": [CACHE_EXPORT, SCRATCH_EXPORT],
        "qumulo_quotas": [*COPIED["qumulo_quotas"], INCOMING_QUOTA],
        "qumulo_snapshot_policies": [*COPIED["qumulo_snapshot_policies"], INCOMING_POLICY],
        "qumulo_s3_buckets": [INCOMING_BUCKET, PROJECTS_BUCKET, SCRATCH_BUCKET],
        "qumulo_s3_bucket_policies": [INCOMING_BUCKET_POLICY, PROJECTS_BUCKET_POLICY, SCRATCH_BUCKET_POLICY],
        "qumulo_s3_access_keys": ACCESS_KEYS,
    },
    "supply": [
        {"variable": "qumulo_ad.join_password", "why": "the cluster keeps neither the join user nor its password"},
        {"variable": "qumulo_ad.join_user", "why": "the cluster keeps neither the join user nor its password"},
        {"variable": "qumulo_local_users[admin].password", "why": PASSWORD_WHY},
        {"variable": "qumulo_local_users[svc-backup].password", "why": PASSWORD_WHY},
        ACCESS_KEY_SUPPLY,
        {"variable": "qumulo_ssl.certificate", "why": f"the cluster serves a certificate whose SHA-256 is {'ab' * 32}"},
        {"variable": "qumulo_ssl.private_key", "why": f"the cluster serves a certificate whose SHA-256 is {'ab' * 32}"},
    ],
    "left_out": [
        SPOKE_ROOT_NOTE,
        {"what": f"the replication from {DR_UUID}:/dr/in/ to /local/in", "why": "the source host declares it in qumulo_replication_relationships"},
        {"what": f"the replication of /old/ to {DR_UUID}:/edge01/old/", "why": "it has ENDED"},
        {"what": f"the spoke portal to hub {DR_UUID}", "why": "it is PENDING, not ACCEPTED"},
    ],
    "peer_values": [
        {"uuid": HUB_UUID, "variable": "qumulo_portal_hosts", "value": [{"address": "10.0.1.20", "port": 3713}], "recorded_by": SOURCE_UUID, "unusable": None}
    ],
    "from_peers": ["qumulo_portal_hosts"],
    "spoke_roots": ["/cache", "/projects"],
    "replication_targets": ["/local/in"],
}
PORTAL_WHY = "the hub's record of the portal is the source's"
REPLICATION_WHY = "the peer's record of the relationship is the source's"
SPOKE_ROOT_WHY = "it lies in a spoke root of the source's portal, which the clone does not take"
INVENTORY_WHY = "the target's inventory, or -e, defines it"
FRESH = {"host_vars": {"qumulo_local_users": USERS[:2], "qumulo_smb_shares": [{"share_name": "Files"}], "qumulo_nfs_exports": [{"export_path": "/"}]}}


def derive(capture=None, **params):
    params = {
        "capture": copy.deepcopy(CAPTURE if capture is None else capture),
        "cluster_uuid": CLONE_UUID,
        "cluster_name": "edge02",
        **params,
    }
    full = {"inventory_defines": {}, "names_in_use": [], "target": None, "allow_configured_target": False, "supplied": [], **params}
    return Clone(full).run()


def refusal(**params):
    with pytest.raises(QumuloError) as raised:
        derive(**params)
    return str(raised.value)


def supply_of(result):
    return {entry["variable"]: entry["why"] for entry in result["supply"]}


def notes_of(result):
    return {entry["what"]: entry["why"] for entry in result["left_out"]}


def keys_in(value):
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys_in(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys_in(item)}
    return set()


def test_everything_that_is_not_unique_to_one_cluster_is_copied_verbatim():
    host_vars = derive()["host_vars"]

    assert {name: value for name, value in host_vars.items() if name in COPIED} == COPIED
    assert set(host_vars) == {*COPIED, "qumulo_cluster_uuid", "qumulo_cluster_name"}
    for variable, rule in RULES.items():
        assert rule in ("copy", "filter", "replace", "drop"), variable


def test_audit_destinations_are_copied_without_the_cloudwatch_ones():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_audit_destinations"] = [
        {"name": "cloudwatch", "type": "CLOUDWATCH", "enabled": True, "log_group_name": "edge01-audit", "region": "us-east-1"},
        {"name": "local", "type": "LOCAL", "enabled": True},
        {"name": "siem", "type": "SYSLOG", "enabled": True, "server_address": "siem.example.com", "server_port": 514, "log_format": "csv"},
    ]

    result = derive(capture=capture)

    assert result["host_vars"]["qumulo_audit_destinations"] == capture["host_vars"]["qumulo_audit_destinations"][1:]
    assert "log group and region are in the source's AWS account" in notes_of(result)["the CloudWatch audit destination cloudwatch"]
    assert "edge01-audit" not in json.dumps(result)


def test_the_uuid_is_the_clones_and_the_sources_appears_nowhere():
    result = derive(cluster_uuid=CLONE_UUID.upper())

    assert result["host_vars"]["qumulo_cluster_uuid"] == CLONE_UUID
    assert result["source_uuid"] == SOURCE_UUID
    assert SOURCE_UUID not in json.dumps([result["host_vars"], result["supply"]])


def test_the_name_is_the_one_given():
    assert derive(cluster_name="edge02")["host_vars"]["qumulo_cluster_name"] == "edge02"


@pytest.mark.parametrize("uuid", [SOURCE_UUID, SOURCE_UUID.upper()])
def test_a_target_that_is_the_source_is_refused(uuid):
    assert "the target is the source itself" in refusal(cluster_uuid=uuid)


@pytest.mark.parametrize("name", ["edge01", "EDGE01"])
def test_the_sources_name_is_refused(name):
    assert "the name " + name + " is the source's, and a name is also the cluster's computer account" in refusal(cluster_name=name)


def test_a_name_another_inventory_cluster_has_is_refused():
    assert "the name Hub01 is already another inventory cluster's" in refusal(cluster_name="Hub01", names_in_use=["edge01", "hub01"])


def test_a_capture_without_a_uuid_is_refused():
    capture = copy.deepcopy(CAPTURE)
    del capture["host_vars"]["qumulo_cluster_uuid"]

    assert "the capture holds no qumulo_cluster_uuid" in refusal(capture=capture)


@pytest.mark.parametrize(
    "variable",
    [
        "qumulo_previous_cluster_uuids",
        "qumulo_portal_hosts",
        "qumulo_replication_address",
        "qumulo_replication_port",
        "qumulo_ssl",
        "qumulo_managed_by",
        "qumulo_terraform_owned_areas",
    ],
)
def test_a_variable_that_describes_the_source_alone_is_never_carried(variable):
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"][variable] = [SOURCE_UUID] if variable == "qumulo_previous_cluster_uuids" else "10.0.0.5"

    result = derive(capture=capture)

    assert variable not in result["host_vars"]
    assert variable in notes_of(result)


SAML = {
    "enabled": True,
    "idp_sso_url": "https://idp.example.com/sso",
    "idp_certificate": "-----BEGIN CERTIFICATE-----\nAAEC\n-----END CERTIFICATE-----\n",
    "idp_entity_id": "https://idp.example.com/metadata",
    "cluster_dns_name": "edge01.corp.example.com",
    "require_sso": False,
}


def test_the_saml_settings_are_never_carried_since_the_idp_knows_the_source_by_its_name():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_saml"] = SAML

    result = derive(capture=capture)

    assert "qumulo_saml" not in result["host_vars"]
    assert "cluster_dns_name is the source's Web UI address" in notes_of(result)["the SAML settings"]
    assert "password" not in notes_of(result)["the SAML settings"]


def test_a_source_that_requires_sso_says_ad_users_sign_in_to_the_clone_with_a_password():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_saml"] = {**SAML, "require_sso": True}

    why = notes_of(derive(capture=capture))["the SAML settings"]

    assert "the source requires AD users to sign in through the IdP" in why
    assert "sign in to it with a password" in why


def test_the_ad_join_is_a_fresh_join_under_the_new_name_and_its_credentials_are_supply_lines():
    result = derive()

    assert result["host_vars"]["qumulo_ad"] == COPIED["qumulo_ad"]
    for variable in ("qumulo_ad.join_user", "qumulo_ad.join_password"):
        assert "joins corp.example.com as a new computer account, EDGE02$" in supply_of(result)[variable]


def test_nexus_needs_a_join_key_of_the_clones_own_even_when_the_source_is_registered():
    result = derive()

    assert "the source's registration is the source's" in supply_of(result)["qumulo_monitoring.nexus_join_key"]
    assert result["host_vars"]["qumulo_monitoring"] == COPIED["qumulo_monitoring"]


def test_no_join_key_while_the_nexus_connection_is_off():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_monitoring"]["nexus"]["nexus_enabled"] = False
    capture["supply"].append({"variable": "qumulo_monitoring.nexus_join_key", "why": "unregistered"})

    assert "qumulo_monitoring.nexus_join_key" not in supply_of(derive(capture=capture))


def test_a_licensed_source_gives_a_license_line_for_the_clones_uuid():
    why = supply_of(derive())["qumulo_capacity.license_document"]

    assert CLONE_UUID in why and "(G-LICENSE)" in why
    # The playbook as an installed collection names it; playbooks/ exists only in a checkout.
    assert "ansible-playbook qumulo.cluster.edge_license_complete -i <inventory> -l <host>" in why
    assert "playbooks/" not in why


def test_a_source_without_its_license_gives_the_line_for_the_clones_uuid_instead():
    capture = copy.deepcopy(CAPTURE)
    del capture["host_vars"]["qumulo_capacity"]["license"]
    capture["supply"].append({"variable": "qumulo_capacity.license_document", "why": f"no active license for {SOURCE_UUID}"})

    why = supply_of(derive(capture=capture))["qumulo_capacity.license_document"]

    assert CLONE_UUID in why and SOURCE_UUID not in why


def test_an_unprotected_edge_that_runs_without_a_license_gives_no_license_line():
    capture = copy.deepcopy(CAPTURE)
    del capture["host_vars"]["qumulo_capacity"]["license"]

    assert "qumulo_capacity.license_document" not in supply_of(derive(capture=capture))


def test_the_portal_is_dropped_with_a_note_and_the_captures_portal_notes_say_the_same():
    result = derive()

    assert "qumulo_portal" not in result["host_vars"]
    assert PORTAL_WHY in notes_of(result)["the spoke portal to hub hub01"]
    assert PORTAL_WHY in notes_of(result)[f"the spoke portal to hub {DR_UUID}"]


def test_each_replication_relationship_is_dropped_with_a_note_and_the_captures_replication_notes_say_the_same():
    result = derive()

    assert "qumulo_replication_relationships" not in result["host_vars"]
    notes = notes_of(result)
    for what in (
        "the replication of /local/scratch/ to dr01:/edge01/scratch/",
        "the replication of /projects/ to dr01:/edge01/projects/",
        f"the replication of /old/ to {DR_UUID}:/edge01/old/",
        f"the replication from {DR_UUID}:/dr/in/ to /local/in",
    ):
        assert REPLICATION_WHY in notes[what]


def test_the_authoritative_dns_name_is_left_to_the_clone_and_its_clients_copied():
    authoritative = {"enabled": True, "fqdn": "edge01.corp.example.com.", "host_restrictions": ["10.0.0.0-10.0.0.255"], "record_ttl_seconds": 0}
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_authoritative_dns"] = authoritative

    result = derive(capture)

    assert result["host_vars"]["qumulo_authoritative_dns"] == {"host_restrictions": ["10.0.0.0-10.0.0.255"], "record_ttl_seconds": 0}
    assert "must not answer for" in notes_of(result)["the authoritative DNS name and whether it answers"]


def test_a_source_that_never_had_an_authoritative_name_gives_no_note():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_authoritative_dns"] = {"enabled": False, "fqdn": "", "host_restrictions": [], "record_ttl_seconds": 0}

    result = derive(capture)

    assert result["host_vars"]["qumulo_authoritative_dns"] == {"host_restrictions": [], "record_ttl_seconds": 0}
    assert "the authoritative DNS name and whether it answers" not in notes_of(result)


def test_the_network_document_is_left_to_terraform_or_the_caller():
    result = derive()

    assert "qumulo_network" not in result["host_vars"]
    assert "Terraform or you declare the clone's network" in notes_of(result)["the network document"]


def test_the_certificate_lines_ask_for_the_clones_own_and_name_no_digest():
    supply = supply_of(derive())

    for variable in ("qumulo_ssl.certificate", "qumulo_ssl.private_key"):
        assert "give the clone's own" in supply[variable] and "ab" * 32 not in supply[variable]


def test_the_kerberos_setting_is_copied_and_the_keytab_line_asks_for_the_clones_own():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_kerberos"] = {"use_alt_security_identities_mapping": True}
    capture["supply"].append({"variable": "qumulo_kerberos.keytab", "why": "the cluster holds a keytab for nfs/edge01.corp.example.com@CORP.EXAMPLE.COM"})

    result = derive(capture=capture)

    assert result["host_vars"]["qumulo_kerberos"] == {"use_alt_security_identities_mapping": True}
    why = supply_of(result)["qumulo_kerberos.keytab"]
    assert "export the clone's own" in why and "edge01" not in why


def test_a_note_that_the_source_s_keytab_was_unreadable_is_restated_for_the_clone():
    """The capture's note tells the operator to install the source's keytab again; the clone needs one of its own names."""
    capture = copy.deepcopy(CAPTURE)
    capture["left_out"].append({"what": "the keytab", "why": "the API user may not read it; give qumulo_kerberos.keytab to install it again"})

    why = notes_of(derive(capture=capture))["the keytab"]

    assert "install it again" not in why
    assert "export the clone's own" in why


def test_no_keytab_line_when_the_source_held_none():
    assert "qumulo_kerberos.keytab" not in supply_of(derive())


def test_other_supply_lines_and_notes_are_kept_as_the_capture_wrote_them():
    capture = copy.deepcopy(CAPTURE)
    capture["left_out"].append({"what": "the directory /gone", "why": "a share names it, but it does not exist"})

    result = derive(capture=capture)

    assert supply_of(result)["qumulo_local_users[svc-backup].password"] == PASSWORD_WHY
    assert notes_of(result)["the directory /gone"] == "a share names it, but it does not exist"


def test_what_lies_in_the_sources_spoke_roots_is_dropped_with_a_note_whether_or_not_the_portal_was_declared():
    result = derive()

    assert result["host_vars"]["qumulo_smb_shares"] == [SCRATCH_SHARE]
    assert result["host_vars"]["qumulo_nfs_exports"] == [SCRATCH_EXPORT]
    notes = notes_of(result)
    assert SPOKE_ROOT_WHY in notes["the SMB share projects"]
    assert SPOKE_ROOT_WHY in notes["the NFS export /cache"]
    assert SPOKE_ROOT_WHY in notes["the ACL of /projects/a"]
    # The capture's own notes on spoke-root data say the same.
    assert SPOKE_ROOT_WHY in notes[SPOKE_ROOT_NOTE["what"]]


def test_the_directory_the_source_receives_replication_into_is_not_created_on_the_clone_nor_what_lies_on_it():
    result = derive()

    assert result["host_vars"]["qumulo_directories"] == [*LOCAL_DIRECTORIES, INBOX]
    assert result["host_vars"]["qumulo_file_acls"] == [INBOX_ACL]
    assert result["host_vars"]["qumulo_quotas"] == COPIED["qumulo_quotas"]
    assert result["host_vars"]["qumulo_snapshot_policies"] == COPIED["qumulo_snapshot_policies"]
    for what in (
        "the directory /local/in",
        "the ACL of /local/in",
        "the quota on /local/in",
        "the snapshot policy in-daily",
        "the S3 bucket incoming",
        "the policy of the S3 bucket incoming",
    ):
        assert "receives the source's incoming replication" in notes_of(result)[what]


def test_an_s3_bucket_in_the_sources_spoke_roots_is_dropped_with_its_policy():
    result = derive()

    assert result["host_vars"]["qumulo_s3_buckets"] == [SCRATCH_BUCKET]
    assert result["host_vars"]["qumulo_s3_bucket_policies"] == [SCRATCH_BUCKET_POLICY]
    for what in ("the S3 bucket projects", "the policy of the S3 bucket projects"):
        assert SPOKE_ROOT_WHY in notes_of(result)[what]


@pytest.mark.parametrize(
    "base_path, why",
    [("/projects/s3", SPOKE_ROOT_WHY), ("/local/in/s3", "receives the source's incoming replication")],
    ids=["in a spoke root", "on a replication target"],
)
def test_an_s3_base_path_the_clone_has_no_directory_for_is_left_out_with_a_note(base_path, why):
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_s3_settings"] = {**COPIED["qumulo_s3_settings"], "base_path": base_path}

    result = derive(capture=capture)

    assert result["host_vars"]["qumulo_s3_settings"] == COPIED["qumulo_s3_settings"]
    assert why in notes_of(result)[f"the S3 base path {base_path}"]


def test_the_sources_s3_access_keys_are_dropped_with_a_note_and_without_their_supply_lines():
    result = derive()

    assert "qumulo_s3_access_keys" not in result["host_vars"]
    assert "declare the clone's own keys" in notes_of(result)["the S3 access key 00000000000000000001 of local:svc-backup"]
    assert ACCESS_KEY_SUPPLY["variable"] not in supply_of(result)
    assert "no supply line of the clone's file names" in refusal(supplied=[ACCESS_KEY_SUPPLY["variable"]])


def test_every_variable_capture_writes_has_a_rule():
    capture_py = (ROOT / "plugins" / "module_utils" / "capture.py").read_text(encoding="utf-8")
    capture_yml = (ROOT / "playbooks" / "capture.yml").read_text(encoding="utf-8")
    areas = yaml.safe_load((ROOT / "roles" / "cluster_baseline" / "vars" / "main.yml").read_text(encoding="utf-8"))["cluster_baseline_area_variables"]
    written = set(re.findall(r'host_vars(?:\[|\.setdefault\()"(qumulo_\w+)"', capture_py))
    written |= set(re.findall(r"'(qumulo_\w+)'", capture_yml)) | set(re.findall(r'= "(qumulo_\w+)"', capture_py))
    written |= {name.split(".")[0] for names in areas.values() for name in ([names] if isinstance(names, str) else names)}

    assert written, "found no variable capture writes; the patterns no longer match capture.py"
    assert sorted(written - set(RULES)) == []


def test_a_variable_without_a_rule_fails_rather_than_being_copied_unseen():
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_nexus_api_token"] = "secret"

    assert "the capture holds qumulo_nexus_api_token, which the clone has no rule for" in refusal(capture=capture)


@pytest.mark.parametrize(
    "held, what",
    [
        ({"qumulo_local_users": USERS}, "the local user svc-backup"),
        ({"qumulo_ad": {"domain": "corp.example.com"}}, "a join to corp.example.com"),
        ({"qumulo_smb_shares": [{"share_name": "Files"}, {"share_name": "projects"}]}, "the SMB share projects"),
        ({"qumulo_nfs_exports": [{"export_path": "/"}, {"export_path": "/projects"}]}, "the NFS export /projects"),
        ({"qumulo_s3_buckets": [{"name": "photos", "path": "/photos"}]}, "the S3 bucket photos"),
        ({"qumulo_s3_access_keys": [{"user": "local:svc", "access_key_id": "00000000000000000009"}]}, "the S3 access key 00000000000000000009 of local:svc"),
    ],
)
def test_a_target_that_holds_more_than_formation_creates_is_refused_unless_allowed(held, what):
    target = {"host_vars": {**FRESH["host_vars"], **held}}

    assert f"the target holds {what}, so it is not a fresh cluster" in refusal(target=target)
    assert derive(target=target, allow_configured_target=True)["host_vars"]["qumulo_cluster_uuid"] == CLONE_UUID


def test_a_target_as_formation_leaves_it_is_fresh():
    assert derive(target=FRESH)["host_vars"]["qumulo_cluster_uuid"] == CLONE_UUID


def test_the_result_is_sorted_and_the_same_from_records_in_any_order():
    shuffled = copy.deepcopy(CAPTURE)
    shuffled["host_vars"] = dict(random.Random(7).sample(list(shuffled["host_vars"].items()), len(shuffled["host_vars"])))
    random.Random(7).shuffle(shuffled["supply"])
    random.Random(7).shuffle(shuffled["left_out"])

    result, again = derive(), derive(capture=shuffled)

    assert result == again
    assert list(result["host_vars"]) == sorted(result["host_vars"])
    assert [entry["variable"] for entry in result["supply"]] == sorted(entry["variable"] for entry in result["supply"])
    assert [entry["what"] for entry in result["left_out"]] == sorted(entry["what"] for entry in result["left_out"])


def test_no_secret_is_ever_returned_even_from_a_capture_that_holds_some():
    capture = copy.deepcopy(CAPTURE)
    host_vars = capture["host_vars"]
    host_vars["qumulo_local_users"][2]["password"] = "secret-1"
    host_vars["qumulo_ad"].update(join_user="svc-qjoin", join_password="secret-2")
    host_vars["qumulo_monitoring"]["nexus_join_key"] = "secret-3"
    host_vars["qumulo_capacity"]["license_document"] = "secret-4"
    host_vars["qumulo_ssl"] = {"certificate": "secret-5", "private_key": "secret-6"}

    result = derive(capture=capture, supplied=["qumulo_local_users[svc-backup].password"])

    assert not [key for key in keys_in(result["host_vars"]) if any(secret in key for secret in SECRET_NAMES + ("join_user",))]
    assert "secret-" not in json.dumps(result)
    assert result["host_vars"]["qumulo_ad"] == COPIED["qumulo_ad"]


def test_what_the_inventory_defines_is_left_out_with_a_note_and_without_its_supply_lines():
    defines = {"qumulo_cluster_uuid": None, "qumulo_ad": ["domain", "join_password", "join_user"], "qumulo_dns": ["servers"], "ansible_connection": None}

    result = derive(inventory_defines=defines)

    assert not {"qumulo_cluster_uuid", "qumulo_ad", "qumulo_dns"} & set(result["host_vars"])
    assert result["host_vars"]["qumulo_cluster_name"] == "edge02"
    assert not [entry for entry in result["supply"] if entry["variable"].startswith("qumulo_ad.")]
    assert result["first_pass_needs"] == ["qumulo_local_users[svc-backup].password"]
    for variable in ("qumulo_cluster_uuid", "qumulo_ad", "qumulo_dns"):
        assert INVENTORY_WHY in notes_of(result)[variable]
    assert "ansible_connection" not in notes_of(result)


def test_an_inventory_mapping_of_secrets_alone_keeps_the_sources_other_settings_with_a_note():
    result = derive(inventory_defines={"qumulo_capacity": ["license_document"], "qumulo_monitoring": ["nexus_join_key"]})

    assert result["host_vars"]["qumulo_capacity"] == COPIED["qumulo_capacity"]
    assert result["host_vars"]["qumulo_monitoring"] == COPIED["qumulo_monitoring"]
    assert "gives only license_document of it" in notes_of(result)["qumulo_capacity"]
    assert {"qumulo_capacity.license_document", "qumulo_monitoring.nexus_join_key"} <= set(supply_of(result))


def test_an_inventory_mapping_of_empty_placeholders_keeps_the_sources_and_supplies_nothing():
    result = derive(inventory_defines={"qumulo_ad": []})

    assert result["host_vars"]["qumulo_ad"] == COPIED["qumulo_ad"]
    assert result["first_pass_needs"] == ["qumulo_ad.join_password", "qumulo_ad.join_user", "qumulo_local_users[svc-backup].password"]


def test_the_secrets_an_inventory_mapping_gives_count_as_supplied_for_the_first_pass():
    defines = {"qumulo_ad": ["join_password", "join_user"]}

    result = derive(inventory_defines=defines)

    assert result["host_vars"]["qumulo_ad"] == COPIED["qumulo_ad"]
    assert result["first_pass_needs"] == ["qumulo_local_users[svc-backup].password"]
    assert "The first pass takes join_password, join_user from the inventory" in notes_of(result)["qumulo_ad"]


def test_the_first_pass_needs_a_password_for_each_user_the_clone_lacks_and_the_join_credentials():
    assert derive()["first_pass_needs"] == ["qumulo_ad.join_password", "qumulo_ad.join_user", "qumulo_local_users[svc-backup].password"]


def test_what_is_supplied_or_already_on_the_clone_is_not_needed():
    target = {"host_vars": {"qumulo_local_users": USERS, "qumulo_ad": {"domain": "CORP.example.com"}}}

    assert derive(target=target, allow_configured_target=True)["first_pass_needs"] == []
    supplied = ["qumulo_ad.join_password", "qumulo_ad.join_user", "qumulo_local_users[svc-backup].password"]
    assert derive(supplied=supplied)["first_pass_needs"] == []


def test_a_clone_joined_to_another_domain_needs_the_join_credentials():
    target = {"host_vars": {"qumulo_local_users": USERS, "qumulo_ad": {"domain": "other.example.com"}}}

    assert derive(target=target, allow_configured_target=True)["first_pass_needs"] == ["qumulo_ad.join_password", "qumulo_ad.join_user"]


def test_a_supplied_name_no_supply_line_holds_is_refused():
    assert "no supply line of the clone's file names qumulo_capacity.licence_document" in refusal(supplied=["qumulo_capacity.licence_document"])


def test_the_module_sends_no_request_and_changes_nothing(run_module):
    result = run_module(clone_host_vars, {"capture": CAPTURE, "cluster_uuid": CLONE_UUID, "cluster_name": "edge02"}, check_mode=True)

    assert result["changed"] is False
    assert result["host_vars"]["qumulo_cluster_uuid"] == CLONE_UUID


def test_the_module_fails_with_the_reason(run_module):
    result = run_module(clone_host_vars, {"capture": CAPTURE, "cluster_uuid": SOURCE_UUID, "cluster_name": "edge02"})

    assert result["failed"] and "the target is the source itself" in result["msg"]


# The tenants (spec 014, FR-038): a clone keeps each tenant and drops its networks, which hold the source's addresses,
# and filters the shares and exports nested under it as it filters the flat lists.
FINANCE_LEDGER = {"share_name": "ledger", "fs_path": "/local/finance", "permissions": [{"type": "ALLOWED", "trustee": "local:Users", "rights": ["ALL"]}]}
FINANCE_REPORTS = {"export_path": "/reports", "fs_path": "/local/finance/reports", "restrictions": [{"host_restrictions": [], "read_only": True}]}
SWITCHES = {"web_ui_enabled": True, "rest_api_enabled": True, "ssh_enabled": False, "replication_enabled": True, "nfs_enabled": True, "smb_enabled": True}
SOURCE_TENANTS = [
    {
        "name": "Corp",
        "previous_name": "Default",
        **SWITCHES,
        "networks": [{"id": 1, "name": "Default", "type": "STATIC", "ip_ranges": ["10.0.0.5-6"], "floating_ip_ranges": ["10.0.0.20-21"]}],
        "smb_settings": "inherit",
        "nfs_settings": "inherit",
        "smb_shares": [{"share_name": "scratch", "fs_path": "/local/scratch", "permissions": []}],
    },
    {
        "name": "finance",
        **SWITCHES,
        "smb_enabled": False,
        "networks": [{"id": 2, "name": "finance", "type": "STATIC", "ip_ranges": ["10.0.2.5-6"], "vlan_id": 20}],
        "smb_settings": {"signing_required": True, "session_encryption": "REQUIRED"},
        "nfs_settings": "inherit",
        # One on the declared portal's spoke root, and one on the directory the source receives replication into.
        "smb_shares": [FINANCE_LEDGER, {"share_name": "projects", "fs_path": "/projects/finance", "permissions": []}],
        "nfs_exports": [FINANCE_REPORTS, {"export_path": "/in", "fs_path": "/local/in/finance", "restrictions": []}],
    },
    {"name": "hr", **SWITCHES, "networks": [], "smb_settings": "inherit", "nfs_settings": "inherit"},
]
SHARED_HOME = {"share_name": "home", "tenants": "all", "fs_path": "/local/home", "permissions": []}
SHARED_CACHE = {"share_name": "cache", "tenants": ["finance", "hr"], "fs_path": "/cache/home", "permissions": []}


def tenant_capture(tenants=None, shares=None):
    capture = copy.deepcopy(CAPTURE)
    capture["host_vars"]["qumulo_tenants"] = copy.deepcopy(SOURCE_TENANTS if tenants is None else tenants)
    if shares is not None:
        capture["host_vars"]["qumulo_smb_shares"] = shares
    return capture


def test_each_tenant_keeps_its_name_previous_name_switches_and_settings():
    tenants = derive(capture=tenant_capture())["host_vars"]["qumulo_tenants"]

    assert [tenant["name"] for tenant in tenants] == ["Corp", "finance", "hr"]
    assert tenants[0]["previous_name"] == "Default"
    keys = (*SWITCHES, "smb_settings", "nfs_settings")
    for kept, source in zip(tenants, SOURCE_TENANTS):
        assert {key: kept[key] for key in keys} == {key: source[key] for key in keys}


def test_the_tenants_networks_are_dropped_with_one_note_per_tenant_that_had_any():
    result = derive(capture=tenant_capture())

    assert all("networks" not in tenant for tenant in result["host_vars"]["qumulo_tenants"])
    notes = notes_of(result)
    assert "the source's own addresses" in notes["the networks of tenant Corp"]
    assert "the source's own addresses" in notes["the networks of tenant finance"]
    assert "the networks of tenant hr" not in notes


def test_a_nested_share_in_a_spoke_root_and_a_nested_export_on_a_replication_target_are_left_out_naming_their_tenant():
    result = derive(capture=tenant_capture())

    finance = result["host_vars"]["qumulo_tenants"][1]
    assert finance["smb_shares"] == [FINANCE_LEDGER]
    assert finance["nfs_exports"] == [FINANCE_REPORTS]
    notes = notes_of(result)
    assert SPOKE_ROOT_WHY in notes["the SMB share projects of tenant finance"]
    assert "it receives the source's incoming replication" in notes["the NFS export /in of tenant finance"]


def test_a_shared_entry_is_filtered_as_the_flat_list_is():
    result = derive(capture=tenant_capture(shares=[SHARED_CACHE, SHARED_HOME]))

    assert result["host_vars"]["qumulo_smb_shares"] == [SHARED_HOME]
    assert SPOKE_ROOT_WHY in notes_of(result)["the SMB share cache"]


def test_a_cloud_tenant_without_its_switches_is_kept_as_captured():
    cloud = [{"name": "Default", "networks": [], "smb_settings": "inherit", "nfs_settings": "inherit"}]

    assert derive(capture=tenant_capture(tenants=cloud))["host_vars"]["qumulo_tenants"] == [
        {"name": "Default", "smb_settings": "inherit", "nfs_settings": "inherit"}
    ]


FRESH_TENANT = {"name": "Default", **SWITCHES, "smb_shares": [{"share_name": "Files", "fs_path": "/"}], "nfs_exports": [{"export_path": "/", "fs_path": "/"}]}


@pytest.mark.parametrize(
    "tenants, shares, what",
    [
        pytest.param([FRESH_TENANT, {"name": "finance"}], None, "the tenant finance", id="second-tenant"),
        pytest.param(
            [{**FRESH_TENANT, "smb_shares": [*FRESH_TENANT["smb_shares"], {"share_name": "projects"}]}],
            None,
            "the SMB share projects of tenant Default",
            id="nested-share",
        ),
        pytest.param(
            [{**FRESH_TENANT, "nfs_exports": [*FRESH_TENANT["nfs_exports"], {"export_path": "/projects"}]}],
            None,
            "the NFS export /projects of tenant Default",
            id="nested-export",
        ),
        pytest.param([FRESH_TENANT], [{"share_name": "home", "tenants": "all"}], "the SMB share home", id="shared-entry"),
        # The factory pair is the formation tenant's alone; a Files share in another tenant is held.
        pytest.param(
            [FRESH_TENANT, {"name": "finance", "smb_shares": [{"share_name": "Files"}]}],
            None,
            "the tenant finance, the SMB share Files of tenant finance",
            id="files-elsewhere",
        ),
    ],
)
def test_a_target_whose_tenants_hold_more_than_formation_creates_is_refused_unless_allowed(tenants, shares, what):
    target = {"host_vars": {"qumulo_local_users": USERS[:2], "qumulo_tenants": tenants, **({} if shares is None else {"qumulo_smb_shares": shares})}}

    assert f"the target holds {what}, so it is not a fresh cluster" in refusal(capture=tenant_capture(), target=target)
    assert derive(capture=tenant_capture(), target=target, allow_configured_target=True)["host_vars"]["qumulo_cluster_uuid"] == CLONE_UUID


@pytest.mark.parametrize("name", ["Default", "Corp"])
def test_a_target_whose_one_tenant_holds_only_the_factory_share_and_export_is_fresh(name):
    target = {"host_vars": {"qumulo_local_users": USERS[:2], "qumulo_tenants": [{**FRESH_TENANT, "name": name}], "qumulo_smb_shares": []}}

    assert derive(capture=tenant_capture(), target=target)["host_vars"]["qumulo_cluster_uuid"] == CLONE_UUID


def test_the_formation_tenant_of_two_is_the_one_named_default_or_renamed_from_it():
    renamed = {**FRESH_TENANT, "name": "Corp", "previous_name": "Default"}
    target = {"host_vars": {"qumulo_local_users": USERS[:2], "qumulo_tenants": [{"name": "alpha"}, renamed]}}

    assert "the target holds the tenant alpha, so it is not a fresh cluster" in refusal(capture=tenant_capture(), target=target)


def test_a_tenant_declared_absent_is_left_to_the_target_with_a_note():
    """Issue #287: a source without tenant 1 captures Default absent; the target's Default holds the target's networks."""
    source = [{"name": "Default", "state": "absent"}, {**SOURCE_TENANTS[1]}]

    result = derive(capture=tenant_capture(tenants=source))

    assert [tenant["name"] for tenant in result["host_vars"]["qumulo_tenants"]] == ["finance"]
    assert "the target's Default holds the target's own networks" in notes_of(result)["tenant Default, declared absent"]
    assert result["host_vars"]["qumulo_factory_defaults"] == COPIED["qumulo_factory_defaults"]


def test_the_rename_a_capture_writes_of_a_host_managed_source_without_tenant_1_renames_the_target_s_default():
    """The clone keeps previous_name, so a fresh target's Default, which holds the target's own networks, becomes eng, as
    it does for a source whose tenant 1 is renamed."""
    (eng,) = HOSTED_WITHOUT_TENANT_1_CAPTURE["qumulo_tenants"]

    result = derive(capture=tenant_capture(tenants=[eng]))

    assert result["host_vars"]["qumulo_tenants"] == [{key: value for key, value in eng.items() if key != "networks"}]
    assert list(notes_of(result)).count("the networks of tenant eng") == 1
    assert not any(what.startswith("tenant Default") for what in notes_of(result))


@pytest.mark.parametrize(
    "target",
    [
        # As clone_edge reads the target, without the network area: no Default entry, but the note.
        pytest.param(
            {"host_vars": {"qumulo_local_users": USERS[:2], "qumulo_tenants": [{"name": "eng"}]}, "left_out": [{"what": "tenant 1", "why": "..."}]},
            id="noted",
        ),
        pytest.param(
            {"host_vars": {"qumulo_local_users": USERS[:2], "qumulo_tenants": [{"name": "Default", "state": "absent"}, {"name": "eng"}]}}, id="absent"
        ),
    ],
)
def test_a_target_whose_capture_lacks_tenant_1_has_no_formation_tenant_so_is_not_fresh(target):
    """Issue #287, built as on 2026-11-08: a target without tenant 1 had it deleted, so every tenant it holds is held."""
    assert "the target holds the tenant eng, so it is not a fresh cluster" in refusal(capture=tenant_capture(), target=target)


def test_a_tenant_whose_every_nested_share_is_left_out_keeps_no_empty_list():
    """Capture leaves an empty list out of an entry, and so does the clone."""
    tenants = [{**SOURCE_TENANTS[2], "smb_shares": [{"share_name": "projects", "fs_path": "/projects/hr", "permissions": []}]}]

    (hr,) = derive(capture=tenant_capture(tenants=tenants))["host_vars"]["qumulo_tenants"]

    assert "smb_shares" not in hr
