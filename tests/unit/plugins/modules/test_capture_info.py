# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.capture_info, driven through FakeQumulo serving a cluster whose state does not change.

Each area's test captures it, checks the variables against the shape the cluster_baseline role takes, then runs each
module that writes the area in check mode, with the captured values as the role would pass them, against the same
state: none of them may predict a change, which is the capture's round trip at the level of one module.
tests/unit/test_capture_coverage.py holds every module capture reads back to an unchanged() call here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from ansible.module_utils.common.arg_spec import ArgumentSpecValidator
from ansible_collections.qumulo.cluster.plugins.module_utils import tls
from ansible_collections.qumulo.cluster.plugins.module_utils.nfs import export_options as nfs_export_options
from ansible_collections.qumulo.cluster.plugins.module_utils.smb import share_options as smb_share_options
from ansible_collections.qumulo.cluster.plugins.module_utils.tenancy_plan import replay
from ansible_collections.qumulo.cluster.plugins.modules import (
    ad_domain,
    audit_destination,
    authoritative_dns,
    capacity_clamp,
    capture_info,
    clone_host_vars,
    cluster_settings,
    directory,
    directory_quota,
    dns_lookup_overrides,
    dns_settings,
    file_acl,
    filesystem_settings,
    ftp_settings,
    kerberos_settings,
    license,
    local_group,
    local_user,
    monitoring_settings,
    network_config,
    network_info,
    nexus_connection,
    nexus_registration,
    nfs_export,
    nfs_info,
    nfs_settings,
    portal_eviction_settings,
    portal_hub,
    portal_spoke,
    replication_relationship,
    replication_target,
    role,
    role_member,
    s3_access_key,
    s3_bucket,
    s3_bucket_policy,
    s3_settings,
    saml_settings,
    smb_info,
    smb_settings,
    smb_share,
    snapshot_policy,
    ssl_ciphers,
    tenancy_plan,
    tenant,
    tenant_info,
    time_settings,
    user_defined_mappings,
    web_ui_settings,
)
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fake_qumulo import CLUSTER_ID
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_local_data import (
    ADMIN,
    AUTHENTICATED_USERS,
    EVERYONE,
    FILE_GROUP_OWNER,
    FILE_OWNER,
    GUEST,
    IDENTITIES,
    UID_1000,
    UNSEEN_SID_DOMAIN,
    USERS,
    acl_path,
    attributes,
    attributes_path,
    file_acl_entry,
    file_acl_response,
    ref,
)
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import (
    API_HOST,
    BOND,
    CORP,
    ENG_NET,
    FINANCE,
    HOSTED_ENG,
    HOSTED_ENG_NET,
    HOSTED_WITHOUT_TENANT_1,
    HOSTED_WITHOUT_TENANT_1_CAPTURE,
    NAMED_DEFAULT,
    NAMED_DEFAULT_CAPTURE,
    NAMED_DEFAULT_DOCUMENT,
    NAMED_ENG,
    NET1,
    NET2,
    NET3,
    SPARE_NET,
    SWITCHES,
    TENANTS_LEFT_OUT,
    TWO,
    TWO_WITH_SPARE,
    TWO_WITH_SPARE_CAPTURE,
    WITH_SPARE,
    WITH_SPARE_CAPTURE,
    WITHOUT_TENANT_1,
    WITHOUT_TENANT_1_CAPTURE,
    listed_tenant,
    wire_document,
    wire_static,
)
from ansible_collections.qumulo.cluster.tests.unit.plugins.module_utils.fixtures_tenancy import ENG as WITHOUT_TENANT_1_ENG

CONNECTION = {"api_host": "qumulo.example.com", "api_password": "secret", "expected_cluster_uuid": CLUSTER_ID}
HUB_UUID = "4b3c802f-8201-42b7-bb41-28f562cd8dfd"
SPOKE_UUID = "625d63a2-9663-4b0e-9d45-ec119a5a9a49"
PEER_UUID = "59052546-452f-483e-a800-0a925d3fa1bd"
ALL_RIGHTS = ["CHANGE_PERMISSIONS", "READ", "WRITE"]
EVERY_DAY = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
METRICS = "/v2/metrics/endpoints/default/data"
NOT_FOUND = {"error_class": "http_not_found_error", "description": "no such route"}
NO_SUCH_ENTRY = {"error_class": "fs_no_such_entry_error", "description": "gone"}


TENANTS_PATH = "/v1/multitenancy/tenants/"
SMB_OVERRIDES = "/v1/multitenancy/smb/settings/"
NFS_OVERRIDES = "/v1/multitenancy/nfs/settings/"
PROTECTION = "/v1/cluster/protection/status"
ERASURE_CODED = {"protection_system_type": "PROTECTION_SYSTEM_TYPE_EC"}
OBJECT_BACKED = {"protection_system_type": "PROTECTION_SYSTEM_TYPE_OBJECT_BACKED"}


# The one tenant a cluster forms with; network_config checks a captured network's tenant against the list.
TENANTS = {"entries": [listed_tenant(1, "Default", networks=[1])]}
# What the network, smb and nfs areas read of a cluster with that one tenant and no override, captured flat.
ONE_TENANT = {TENANTS_PATH: TENANTS}
ONE_TENANT_SMB = {**ONE_TENANT, SMB_OVERRIDES: []}
ONE_TENANT_NFS = {**ONE_TENANT, NFS_OVERRIDES: []}


def cluster(fake_api, state, optional=False, version="7.10.3"):
    """A fake serving state, {path: response}, and the local identities; it fails any request that writes.

    Every served path must be read, unless optional, as for a state several tests share.
    """
    fake = fake_api(version=version)
    fake.identities = list(IDENTITIES)
    for path, response in state.items():
        method, _space, query = path.rpartition(" ")  # "POST <path>" serves a read-only query sent as a POST
        if isinstance(response, Query):
            fake.serve(query, response.answer, optional=optional, method=method, body=response.body)
        else:
            fake.serve(query, response, optional=optional, method=method or "GET")
    return fake


class Query:
    """The answer to a read-only query sent as a POST, served only for this body."""

    def __init__(self, body, answer):
        self.body, self.answer = body, answer

    def __eq__(self, other):
        return isinstance(other, Query) and (self.body, self.answer) == (other.body, other.answer)


# cluster_baseline's argument spec refuses a key it does not list, so every captured variable must pass it, or a clone or
# an adoption of the capture stops at the role's door.
BASELINE_OPTIONS = yaml.safe_load(
    (Path(__file__).resolve().parents[4] / "roles" / "cluster_baseline" / "meta" / "argument_specs.yml").read_text(encoding="utf-8")
)["argument_specs"]["main"]["options"]


def capture(run_module, *areas, **args):
    result = run_module(capture_info, {**CONNECTION, "gather_subset": list(areas) or ["all"], **args})
    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False
    host_vars = result["capture"]["host_vars"]
    errors = ArgumentSpecValidator(BASELINE_OPTIONS).validate({key: value for key, value in host_vars.items() if key in BASELINE_OPTIONS}).error_messages
    assert errors == [], errors
    return result["capture"]


def unchanged(run_module, module, **args):
    """Run module in check mode; it must predict no change."""
    result = run_module(module, {**CONNECTION, **args}, check_mode=True)
    assert not result.get("failed"), result.get("msg")
    assert result["changed"] is False, result.get("diff") or result
    return result


def variables(captured):
    return [entry["variable"] for entry in captured["supply"]]


def reasons(captured):
    return {entry["what"]: entry["why"] for entry in captured["left_out"]}


def test_cluster_name(fake_api, run_module):
    cluster(fake_api, {"/v1/cluster/settings": {"cluster_name": "edge01"}})

    captured = capture(run_module, "cluster")

    assert captured["host_vars"] == {"qumulo_cluster_uuid": CLUSTER_ID, "qumulo_cluster_name": "edge01"}
    unchanged(run_module, cluster_settings, cluster_name="edge01")


FS_SETTINGS = {
    "/v1/file-system/settings/permissions": {"mode": "CROSS_PROTOCOL"},
    "/v1/file-system/settings/atime": {"enabled": True, "granularity": "HOUR"},
    "/v1/file-system/settings/notify": {"recursive_mode": "DISABLED_IGNORE"},
}


def test_filesystem_settings_in_their_modules_shape(fake_api, run_module):
    cluster(fake_api, FS_SETTINGS)

    captured = capture(run_module, "filesystem")

    settings = captured["host_vars"]["qumulo_filesystem_settings"]
    assert settings == {"permissions_mode": "CROSS_PROTOCOL", "atime_enabled": True, "atime_granularity": "HOUR", "notify_recursive_mode": "DISABLED_IGNORE"}
    unchanged(run_module, filesystem_settings, **settings)


def test_a_permissions_mode_the_module_does_not_offer_is_left_out(fake_api, run_module):
    cluster(fake_api, {**FS_SETTINGS, "/v1/file-system/settings/permissions": {"mode": "_DEPRECATED_MERGED_V1"}})

    captured = capture(run_module, "filesystem")

    settings = captured["host_vars"]["qumulo_filesystem_settings"]
    assert "permissions_mode" not in settings
    assert "_DEPRECATED_MERGED_V1" in reasons(captured)["the permissions mode"]
    unchanged(run_module, filesystem_settings, **settings)


def test_web_ui_settings_in_their_modules_shape(fake_api, run_module):
    cluster(fake_api, {"/v1/web-ui/settings": {"inactivity_timeout": {"nanoseconds": "1800000000000"}, "login_banner": "Authorized use only."}})

    captured = capture(run_module, "web_ui")

    settings = captured["host_vars"]["qumulo_web_ui_settings"]
    assert settings == {"inactivity_timeout": 30, "login_banner": "Authorized use only."}
    unchanged(run_module, web_ui_settings, **settings)


def test_a_timeout_that_is_not_whole_minutes_is_left_out(fake_api, run_module):
    """web_ui_settings takes whole minutes, so a timeout set in seconds elsewhere would fail the captured inventory."""
    cluster(fake_api, {"/v1/web-ui/settings": {"inactivity_timeout": {"nanoseconds": "150000000000"}, "login_banner": "Authorized use only."}})

    captured = capture(run_module, "web_ui")

    settings = captured["host_vars"]["qumulo_web_ui_settings"]
    assert settings == {"login_banner": "Authorized use only."}
    assert "2.5 minutes" in reasons(captured)["the Web UI's inactivity timeout"]
    unchanged(run_module, web_ui_settings, **settings)


def network_document(floating):
    static = {
        "type": "STATIC",
        "static_addresses": {
            "default_gateway": "10.20.0.1",
            "ip_ranges": ["10.20.0.5-6"],
            "floating_ip_ranges": floating,
            "netmask": "255.255.255.0",
        },
    }
    bond = {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]}
    interfaces = {"frontend_bond_config": bond, "backend_bond_config": None, "frontend_vlans": [{"vlan_id": 20, "network_id": 2, "mtu": None}]}
    return {"frontend_networks": [{"id": 1, "name": "Default", "tenant_id": 1, "addresses": static}], "managed_interfaces": interfaces}


def test_network_document_in_network_configs_shape(fake_api, run_module):
    cluster(fake_api, {"/v3/network": network_document(["10.20.0.50-10.20.0.51"]), **ONE_TENANT})

    captured = capture(run_module, "network")

    assert captured["host_vars"]["qumulo_network"] == {
        "frontend_networks": [
            {
                "id": 1,
                "name": "Default",
                "type": "STATIC",
                "netmask": "255.255.255.0",
                "default_gateway": "10.20.0.1",
                "ip_ranges": ["10.20.0.5-10.20.0.6"],
                "floating_ip_ranges": ["10.20.0.50-10.20.0.51"],
            }
        ],
        "managed_interfaces": {
            "frontend_bond_config": {"interface_name": "bond0", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [1]},
            "frontend_vlans": [{"vlan_id": 20, "network_id": 2}],
        },
    }
    unchanged(run_module, network_config, **captured["host_vars"]["qumulo_network"])


def test_network_without_the_floating_ips_terraform_owns(fake_api, run_module):
    cluster(fake_api, {"/v3/network": network_document(["10.20.0.50-51"]), **ONE_TENANT})

    captured = capture(run_module, "network", leave_out=["network.floating_ips"])

    network = captured["host_vars"]["qumulo_network"]
    assert "floating_ip_ranges" not in network["frontend_networks"][0]
    assert captured["left_out"] == [{"what": "the floating IP ranges", "why": "leave_out names network.floating_ips, as Terraform owns them"}]
    # On a cluster the provider formed, the role leaves the floating addresses to it.
    unchanged(run_module, network_config, **network, managed_by="terraform_provider")


def test_a_network_without_networks_or_interfaces_declares_none(fake_api, run_module):
    cluster(fake_api, {"/v3/network": {"frontend_networks": [], "managed_interfaces": None}, **ONE_TENANT})

    captured = capture(run_module, "network")

    assert captured["host_vars"]["qumulo_network"] == {"frontend_networks": []}
    unchanged(run_module, network_config, frontend_networks=[])


DNS_SYSTEM = {"dns_servers": ["10.0.0.2"], "dns_search_domains": []}
DNS_DEFAULTS = {"/v1/dns/configs/system": DNS_SYSTEM, "/v1/dns/lookup-override-config": {"lookup_overrides": []}}
AUTHORITATIVE = {"enabled": True, "fqdn": "edge01.corp.example.com.", "host_restrictions": ["10.0.0.0-255", "192.0.2.7"], "record_ttl_seconds": 30}


def test_dns_and_time_in_their_modules_shape(fake_api, run_module):
    cluster(
        fake_api,
        {
            **DNS_DEFAULTS,
            "/v1/dns/configs/system": {"dns_servers": ["2001:DB8:0::0053", "10.0.0.2"], "dns_search_domains": ["corp.example.com"]},
            "/v1/authoritative-dns/settings": AUTHORITATIVE,
            "/v2/time/settings": {"source": "API_TIME_SOURCE_NTP", "use_ad_for_primary": False, "ntp_servers": ["ntp1.corp.example.com"]},
        },
    )

    captured = capture(run_module, "dns", "time")

    hv = captured["host_vars"]
    assert hv["qumulo_dns"]["servers"] == ["2001:db8::53", "10.0.0.2"]
    assert hv["qumulo_dns"]["search_domains"] == ["corp.example.com"]
    assert hv["qumulo_time"] == {"source": "API_TIME_SOURCE_NTP", "ntp_servers": ["ntp1.corp.example.com"], "use_ad_for_primary": False}
    unchanged(run_module, dns_settings, servers=hv["qumulo_dns"]["servers"], search_domains=hv["qumulo_dns"]["search_domains"])
    unchanged(run_module, time_settings, **hv["qumulo_time"])


def test_authoritative_dns_in_its_modules_shape(fake_api, run_module):
    cluster(fake_api, {**DNS_DEFAULTS, "/v1/authoritative-dns/settings": AUTHORITATIVE})

    captured = capture(run_module, "dns")

    hv = captured["host_vars"]
    assert hv["qumulo_authoritative_dns"] == {
        "enabled": True,
        "fqdn": "edge01.corp.example.com.",
        "host_restrictions": ["10.0.0.0-10.0.0.255", "192.0.2.7"],
        "record_ttl_seconds": 30,
    }
    unchanged(run_module, authoritative_dns, **hv["qumulo_authoritative_dns"])


def test_an_authoritative_name_never_set_is_captured_empty(fake_api, run_module):
    """authoritative_dns takes fqdn: "" back where the cluster has never had a name, so capture writes what it read."""
    factory = {"enabled": False, "fqdn": "", "host_restrictions": [], "record_ttl_seconds": 0}
    cluster(fake_api, {**DNS_DEFAULTS, "/v1/authoritative-dns/settings": factory})

    captured = capture(run_module, "dns")

    hv = captured["host_vars"]
    assert hv["qumulo_authoritative_dns"] == factory
    unchanged(run_module, authoritative_dns, **hv["qumulo_authoritative_dns"])


def test_authoritative_dns_the_cluster_does_not_offer_is_left_out_with_the_rest_of_dns_kept(fake_api, run_module):
    fake = cluster(fake_api, DNS_DEFAULTS)
    fake.serve("/v1/authoritative-dns/settings", NOT_FOUND, status=404)

    captured = capture(run_module, "dns")

    assert captured["host_vars"]["qumulo_dns"]["servers"] == ["10.0.0.2"]
    assert "qumulo_authoritative_dns" not in captured["host_vars"]
    assert "does not offer them" in reasons(captured)["the authoritative DNS settings"]


def test_the_authoritative_name_terraform_owns_is_left_out(fake_api, run_module):
    cluster(fake_api, {**DNS_DEFAULTS, "/v1/authoritative-dns/settings": AUTHORITATIVE})

    captured = capture(run_module, "dns", leave_out=["dns.fqdn"])

    hv = captured["host_vars"]
    assert hv["qumulo_authoritative_dns"] == {"host_restrictions": ["10.0.0.0-10.0.0.255", "192.0.2.7"], "record_ttl_seconds": 30}
    assert reasons(captured) == {"the authoritative DNS name and whether it answers": "leave_out names dns.fqdn, as Terraform owns them"}
    # The role passes the inventory's owned areas, so the module refuses the name and its switch.
    unchanged(run_module, authoritative_dns, **hv["qumulo_authoritative_dns"], terraform_owned_areas=["dns.fqdn"])


def test_lookup_overrides_in_their_modules_shape(fake_api, run_module):
    dc1 = {"ip_address": "10.0.0.10", "aliases": ["dc1.corp.example.com", "dc1"]}
    dr01 = {"ip_address": "2001:db8::20", "aliases": ["dr01.corp.example.com"]}
    cluster(
        fake_api,
        {
            "/v1/dns/configs/system": DNS_SYSTEM,
            "/v1/dns/lookup-override-config": {"lookup_overrides": [dr01, dc1]},
            "/v1/authoritative-dns/settings": AUTHORITATIVE,
        },
    )

    captured = capture(run_module, "dns")

    dns = captured["host_vars"]["qumulo_dns"]
    # Sorted by address, each entry's names in the order the cluster holds them.
    assert dns == {"servers": ["10.0.0.2"], "search_domains": [], "lookup_overrides": [dc1, dr01]}
    # The role passes servers and search_domains to dns_settings, and lookup_overrides to this module.
    unchanged(run_module, dns_lookup_overrides, overrides=dns["lookup_overrides"])


def test_a_lookup_table_with_an_address_in_two_entries_is_captured_merged(fake_api, run_module):
    """dns_lookup_overrides refuses an address listed twice, so capture writes the one entry the module reads."""
    cluster(
        fake_api,
        {
            "/v1/dns/configs/system": DNS_SYSTEM,
            "/v1/dns/lookup-override-config": {
                "lookup_overrides": [{"ip_address": "10.0.0.10", "aliases": ["dc1"]}, {"ip_address": "10.0.0.10", "aliases": ["dc1b", "dc1"]}]
            },
            "/v1/authoritative-dns/settings": AUTHORITATIVE,
        },
    )

    captured = capture(run_module, "dns")

    overrides = captured["host_vars"]["qumulo_dns"]["lookup_overrides"]
    assert overrides == [{"ip_address": "10.0.0.10", "aliases": ["dc1", "dc1b"]}]
    assert reasons(captured) == {
        "the lookup overrides of 10.0.0.10": "the cluster lists the address in more than one entry; they are captured as one, names in table order"
    }
    unchanged(run_module, dns_lookup_overrides, overrides=overrides)


CIPHERS = {
    "tls_1_2": ["ECDHE-RSA-AES256-GCM-SHA384", "ECDHE-RSA-AES128-SHA256"],
    "tls_1_3": ["TLS_AES_256_GCM_SHA384", "TLS_AES_128_GCM_SHA256"],
}


def test_the_certificate_is_named_by_its_digest_and_never_written(fake_api, run_module, monkeypatch):
    cluster(fake_api, {"/v2/cluster/settings/ssl/ciphers": CIPHERS})
    probed = []
    monkeypatch.setattr(tls, "served_leaf", lambda host, port, timeout: probed.append((host, port)) or b"leaf")

    captured = capture(run_module, "ssl")

    digest = hashlib.sha256(b"leaf").hexdigest()
    assert probed == [("qumulo.example.com", 8000)]
    assert captured["host_vars"] == {"qumulo_cluster_uuid": CLUSTER_ID, "qumulo_ssl_ciphers": CIPHERS}
    assert variables(captured) == ["qumulo_ssl.certificate", "qumulo_ssl.private_key"]
    assert all(digest in entry["why"] and "G-SSL-READBACK" in entry["why"] for entry in captured["supply"])
    unchanged(run_module, ssl_ciphers, **captured["host_vars"]["qumulo_ssl_ciphers"])


def test_cipher_suites_the_cluster_does_not_offer_are_left_out(fake_api, run_module, monkeypatch):
    fake = cluster(fake_api, {})
    fake.serve("/v2/cluster/settings/ssl/ciphers", NOT_FOUND, status=404)
    monkeypatch.setattr(tls, "served_leaf", lambda host, port, timeout: b"leaf")

    captured = capture(run_module, "ssl")

    assert captured["host_vars"] == {"qumulo_cluster_uuid": CLUSTER_ID}
    assert variables(captured) == ["qumulo_ssl.certificate", "qumulo_ssl.private_key"]
    assert "does not offer them" in reasons(captured)["the TLS cipher suites"]


def test_a_certificate_that_cannot_be_read_still_names_what_to_supply(fake_api, run_module, monkeypatch):
    cluster(fake_api, {"/v2/cluster/settings/ssl/ciphers": CIPHERS})

    def refuse(host, port, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(tls, "served_leaf", refuse)

    captured = capture(run_module, "ssl")

    assert variables(captured) == ["qumulo_ssl.certificate", "qumulo_ssl.private_key"]
    assert all("connection refused" in entry["why"] and "G-SSL-READBACK" in entry["why"] for entry in captured["supply"])


SUPPORT = {
    "enabled": True,
    "mq_host": "api.missionq.qumulo.com",
    "mq_port": 443,
    "mq_proxy_host": "",
    "mq_proxy_port": 0,
    "s3_proxy_host": "monitor.qumulo.com",
    "s3_proxy_port": 443,
    "s3_proxy_disable_https": False,
    "vpn_enabled": False,
    "vpn_host": "ep1.qumulo.com",
    "period": 60,
    "nexus_enabled": True,
    "nexus_host": "api.nexus.qumulo.com",
    "nexus_port": 443,
    "nexus_interval": 30,
    "nexus_registration_key": "PENDING-JOIN-KEY",
    "nexus_secret_created_at": None,
}
MONITORING = {key: value for key, value in SUPPORT.items() if not key.startswith("nexus_")}
NEXUS = {
    "nexus_enabled": True,
    "nexus_host": "api.nexus.qumulo.com",
    "nexus_port": 443,
    "nexus_interval": 30,
    "nexus_capability_remote_support": True,
    "nexus_capability_sso": False,
}


@pytest.mark.parametrize("registration, join_key", [("REGISTERED", False), ("PENDING_REGISTRATION", True)])
def test_monitoring_and_nexus_without_the_join_key(fake_api, run_module, registration, join_key):
    cluster(
        fake_api,
        {
            "/v1/support/settings": SUPPORT,
            "/v1/nexus/connection": NEXUS,
            "/v1/nexus/registration": {"nexus_enabled": True, "registration_status": registration, "join_key": "PENDING-JOIN-KEY"},
        },
    )

    captured = capture(run_module, "monitoring")

    monitoring = captured["host_vars"]["qumulo_monitoring"]
    assert monitoring == {**MONITORING, "nexus": NEXUS}
    assert "PENDING-JOIN-KEY" not in str(captured)
    assert variables(captured) == (["qumulo_monitoring.nexus_join_key"] if join_key else [])
    unchanged(run_module, monitoring_settings, **{key: value for key, value in monitoring.items() if key != "nexus"})
    unchanged(run_module, nexus_connection, **monitoring["nexus"])
    if not join_key:
        # Registered, the role sends no key, and the registration has nothing to do.
        unchanged(run_module, nexus_registration)


def test_monitoring_on_a_cluster_without_nexus(fake_api, run_module):
    fake = cluster(fake_api, {"/v1/support/settings": SUPPORT})
    fake.serve("/v1/nexus/connection", NOT_FOUND, status=404)

    captured = capture(run_module, "monitoring")

    assert captured["host_vars"]["qumulo_monitoring"] == MONITORING
    assert "does not offer it" in reasons(captured)["the Nexus connection"]


AUDIT_NULLS = {"server_address": None, "server_port": None, "log_format": None, "log_group_name": None, "region": None}
AUDIT_ZETA = {**AUDIT_NULLS, "type": "SYSLOG", "name": "zeta", "enabled": False, "server_address": "", "server_port": 0, "log_format": "csv"}
AUDIT_LOCAL = {**AUDIT_NULLS, "type": "LOCAL", "name": "local", "enabled": True}
AUDIT_CW = {**AUDIT_NULLS, "type": "CLOUDWATCH", "name": "cloudwatch", "enabled": True, "log_group_name": "qumulo-audit", "region": "us-east-1"}


def audit_state(*configs):
    """The served list of destinations, and each destination at its own route, which audit_destination reads for its ETag."""
    entries = [{"id": str(number), "config": config} for number, config in enumerate(configs, start=1)]
    return {"/v2/audit/destinations/": {"destinations": entries}, **{f"/v2/audit/destinations/{entry['id']}": entry for entry in entries}}


def test_audit_destinations_in_audit_destination_s_shape(fake_api, run_module):
    cluster(fake_api, audit_state(AUDIT_ZETA, AUDIT_CW, AUDIT_LOCAL))

    captured = capture(run_module, "audit")

    destinations = captured["host_vars"]["qumulo_audit_destinations"]
    assert destinations == [
        {"name": "cloudwatch", "type": "CLOUDWATCH", "enabled": True, "log_group_name": "qumulo-audit", "region": "us-east-1"},
        {"name": "local", "type": "LOCAL", "enabled": True},
        {"name": "zeta", "type": "SYSLOG", "enabled": False, "server_address": "", "server_port": 0, "log_format": "csv"},
    ]
    for destination in destinations:
        unchanged(run_module, audit_destination, **destination)


def test_audit_destinations_the_cluster_does_not_offer_are_left_out(fake_api, run_module):
    fake = cluster(fake_api, {})
    fake.serve("/v2/audit/destinations/", NOT_FOUND, status=404)

    captured = capture(run_module, "audit")

    assert captured["host_vars"] == {"qumulo_cluster_uuid": CLUSTER_ID}
    assert "does not offer them" in reasons(captured)["the audit log destinations"]


def test_a_cloudwatch_destination_terraform_owns_is_left_out(fake_api, run_module):
    cluster(fake_api, audit_state(AUDIT_LOCAL, AUDIT_CW), optional=True)

    captured = capture(run_module, "audit", leave_out=["audit.cloudwatch"])

    assert captured["host_vars"]["qumulo_audit_destinations"] == [{"name": "local", "type": "LOCAL", "enabled": True}]
    assert "audit.cloudwatch" in reasons(captured)["the CloudWatch audit destination cloudwatch"]


LICENSE_ACTIVE = {"state": "LICENSE_STATE_ACTIVE", "license": {"cluster_uuid": CLUSTER_ID.upper(), "details": {"usable_capacity_cap": str(1 << 40)}}}


def edge_metrics(cluster_type):
    return f'qumulo_info{{product_intent="edge_accelerator",cluster_type="{cluster_type}"}} 1\n'.encode()


def test_capacity_with_an_active_license(fake_api, run_module):
    cluster(
        fake_api,
        {
            "/v1/capacity/clamp": {"capacity_clamp": str(1 << 40)},
            "/v1/portal/spokes/eviction-settings": {"free_threshold": 0.05},
            "/v1/license/status": LICENSE_ACTIVE,
            "/v1/license/enforcement": {"license_is_enforced": True},  # the license module reports it
        },
    )

    captured = capture(run_module, "capacity")

    assert captured["host_vars"]["qumulo_capacity"] == {"clamp": 1 << 40, "free_threshold": 0.05, "license": "required"}
    assert captured["supply"] == []
    unchanged(run_module, capacity_clamp, capacity_clamp=1 << 40)
    unchanged(run_module, portal_eviction_settings, free_threshold=0.05)
    # The role keys its hand-off on this status, so license: required captures exactly what it treats as licensed.
    assert unchanged(run_module, license)["license_status"] == "active"


@pytest.mark.parametrize(
    "status, metrics",
    [
        ({"state": "LICENSE_STATE_NONE", "license": None}, edge_metrics("protected_cluster")),
        ({**LICENSE_ACTIVE, "license": {"cluster_uuid": PEER_UUID}}, None),
        ({"state": "LICENSE_STATE_EXPIRED", "license": {"cluster_uuid": CLUSTER_ID}}, None),
    ],
    ids=["none", "another-uuid", "expired"],
)
def test_capacity_without_a_license_for_the_uuid_names_the_document_to_supply(fake_api, run_module, status, metrics):
    state = {"/v1/capacity/clamp": {"capacity_clamp": ""}, "/v1/portal/spokes/eviction-settings": {"free_threshold": 0.1}, "/v1/license/status": status}
    cluster(fake_api, {**state, **({METRICS: metrics} if metrics else {})})

    captured = capture(run_module, "capacity")

    assert captured["host_vars"]["qumulo_capacity"] == {"free_threshold": 0.1}
    assert variables(captured) == ["qumulo_capacity.license_document"]
    assert CLUSTER_ID in captured["supply"][0]["why"] and "G-LICENSE" in captured["supply"][0]["why"]
    assert PEER_UUID not in str(captured["host_vars"])


@pytest.mark.parametrize(("stored", "clamp"), [("", {}), ("2000000000000", {"clamp": 2 * 10**12})], ids=["no_clamp", "clamp"])
def test_an_unprotected_edge_without_a_license_runs_without_one(fake_api, run_module, stored, clamp):
    """license: none, so the baseline neither hands a license off nor waits for one before the clamp."""
    cluster(
        fake_api,
        {
            "/v1/capacity/clamp": {"capacity_clamp": stored},
            "/v1/portal/spokes/eviction-settings": {"free_threshold": 0.1},
            "/v1/license/status": {"state": "LICENSE_STATE_NONE", "license": None},
            METRICS: edge_metrics("unprotected_edge"),
        },
    )

    captured = capture(run_module, "capacity")

    assert captured["host_vars"]["qumulo_capacity"] == {**clamp, "free_threshold": 0.1, "license": "none"}
    assert captured["supply"] == []


def test_capacity_on_a_cluster_without_eviction_settings(fake_api, run_module):
    fake = cluster(fake_api, {"/v1/capacity/clamp": {"capacity_clamp": ""}, "/v1/license/status": LICENSE_ACTIVE})
    fake.serve("/v1/portal/spokes/eviction-settings", NOT_FOUND, status=404)

    captured = capture(run_module, "capacity")

    assert captured["host_vars"]["qumulo_capacity"] == {"license": "required"}
    assert "does not offer it" in reasons(captured)["the eviction threshold"]


SID = "S-1-5-21-1534280521-3995163514-3722741250-"
GROUPS = [
    {"id": "513", "name": "Users", "sid": SID + "513", "gid": ""},
    {"id": "514", "name": "Guests", "sid": SID + "514", "gid": "65534"},
    {"id": "1000", "name": "edge-admins", "sid": SID + "1000", "gid": "5000"},
]
ADMIN_USER = {"id": "500", "name": "admin", "primary_group": "513", "sid": SID + "500", "uid": "0", "home_directory": None}
GUEST_USER = {"id": "501", "name": "guest", "primary_group": "514", "sid": SID + "501", "uid": "65534", "home_directory": None}
BACKUP_USER = {"id": "1001", "name": "svc-backup", "primary_group": "1000", "sid": SID + "1001", "uid": "5001", "home_directory": "/home/svc"}


def test_local_groups_and_users_without_passwords(fake_api, run_module):
    cluster(
        fake_api,
        {
            "/v1/groups/": GROUPS,
            "/v1/groups/513/members/": [ADMIN_USER],
            "/v1/groups/514/members/": [GUEST_USER],
            "/v1/groups/1000/members/": [BACKUP_USER],
            "/v1/users/": [BACKUP_USER, GUEST_USER, ADMIN_USER],
        },
    )

    captured = capture(run_module, "local_groups", "local_users")

    hv = captured["host_vars"]
    assert hv["qumulo_local_groups"] == [
        {"name": "Guests", "gid": 65534, "members": ["guest"]},
        {"name": "Users", "members": ["admin"]},
        {"name": "edge-admins", "gid": 5000, "members": ["svc-backup"]},
    ]
    assert hv["qumulo_local_users"] == [
        {"name": "admin", "primary_group": "Users", "uid": 0},
        {"name": "guest", "primary_group": "Guests", "uid": 65534},
        {"name": "svc-backup", "primary_group": "edge-admins", "uid": 5001, "home_directory": "/home/svc"},
    ]
    # guest has no password until one is set for it, so it gets no line.
    assert variables(captured) == ["qumulo_local_users[admin].password", "qumulo_local_users[svc-backup].password"]
    for group in hv["qumulo_local_groups"]:
        unchanged(run_module, local_group, **group)
    for user in hv["qumulo_local_users"]:
        unchanged(run_module, local_user, **user)


AD_STATUS = {
    "status": "JOINED_TO_DOMAIN",
    "domain": "corp.example.com",
    "ou": "OU=Edges,DC=corp,DC=example,DC=com",
    "search_trusted_domains": False,
    "use_ad_posix_attributes": False,
    "base_dn": "CN=Users,DC=corp,DC=example,DC=com",
    "domain_netbios": "CORP",
    "dcs": [{"name": "dc1", "address": "10.0.0.2"}],
}
EMPTY_ERROR = {"module": "", "error_class": "", "description": "", "stack": "", "user_visible": False}


def ad_monitor(domain, dcs=()):
    return {"status": "JOIN_SUCCEEDED", "last_error": EMPTY_ERROR, "domain": domain, "configured_dcs": list(dcs), "last_action_time": "2026-10-30T00:00:00Z"}


def test_a_joined_domain_without_the_join_credentials(fake_api, run_module):
    cluster(fake_api, {"/v1/ad/status": AD_STATUS, "/v1/ad/monitor": ad_monitor("corp.example.com", ["10.0.0.2"])})

    captured = capture(run_module, "ad")

    ad = captured["host_vars"]["qumulo_ad"]
    assert ad == {
        "domain": "corp.example.com",
        "domain_netbios": "CORP",
        "ou": "OU=Edges,DC=corp,DC=example,DC=com",
        "search_trusted_domains": False,
        "use_ad_posix_attributes": False,
        "base_dn": "CN=Users,DC=corp,DC=example,DC=com",
        "domain_controllers": ["10.0.0.2"],
    }
    assert variables(captured) == ["qumulo_ad.join_password", "qumulo_ad.join_user"]
    unchanged(run_module, ad_domain, **ad)


def test_domain_controllers_the_cluster_does_not_read_back_are_left_out(fake_api, run_module):
    cluster(fake_api, {"/v1/ad/status": AD_STATUS, "/v1/ad/monitor": ad_monitor("other.example.com")})

    captured = capture(run_module, "ad")

    assert "domain_controllers" not in captured["host_vars"]["qumulo_ad"]
    assert "G-AD-DCS" in reasons(captured)["the configured domain controllers"]


def test_a_cluster_in_no_domain_declares_none(fake_api, run_module):
    cluster(fake_api, {"/v1/ad/status": {**AD_STATUS, "status": "NOT_IN_DOMAIN", "domain": ""}, "/v1/ad/monitor": ad_monitor("")})

    captured = capture(run_module, "ad")

    assert "qumulo_ad" not in captured["host_vars"]
    assert captured["supply"] == []


def test_role_members_as_trustees(fake_api, run_module):
    backup = {"domain": "LOCAL", "auth_id": "1001", "uid": None, "gid": None, "sid": SID + "1001", "name": "svc-backup"}

    def page(*identities):
        return {"members": {i["auth_id"]: i for i in identities}, "paging": {"next": None}}

    fake = cluster(
        fake_api,
        {
            "/v1/auth/roles/": {"Observers": {}, "Administrators": {}},
            "/v1/auth/roles/Administrators/members?limit=1000": page(ADMIN),
            "/v1/auth/roles/Observers/members?limit=1000": page(USERS, backup),
        },
    )
    fake.identities.append(backup)

    captured = capture(run_module, "roles")

    roles = captured["host_vars"]["qumulo_role_members"]
    assert roles == [{"role": "Administrators", "members": ["local:admin"]}, {"role": "Observers", "members": ["local:Users", "local:svc-backup"]}]
    for assignment in roles:
        unchanged(run_module, role_member, **assignment)


def test_custom_roles_in_their_modules_shape_without_the_built_in_ones(fake_api, run_module):
    backup = {"description": "Backups", "privileges": ["PRIVILEGE_SNAPSHOT_READ", "PRIVILEGE_QUOTA_READ"]}
    cluster(
        fake_api,
        {
            "/v1/auth/roles/": {"Observers": {"description": "Read-only", "privileges": ["PRIVILEGE_QUOTA_READ"]}, "Backup-Operators": backup},
            "/v1/auth/roles/Backup-Operators/members?limit=1000": {"members": {}, "paging": {"next": None}},
            "/v1/auth/roles/Observers/members?limit=1000": {"members": {}, "paging": {"next": None}},
        },
    )

    captured = capture(run_module, "roles")

    roles = captured["host_vars"]["qumulo_roles"]
    assert roles == [{"name": "Backup-Operators", "description": "Backups", "privileges": ["PRIVILEGE_QUOTA_READ", "PRIVILEGE_SNAPSHOT_READ"]}]
    for declared in roles:
        unchanged(run_module, role, **declared)


KERBEROS_SETTINGS = "/v1/auth/kerberos-settings"
KERBEROS_KEYTAB = "/v1/auth/kerberos-keytab"
KEYTAB_ENTRY = {"principal": "nfs/edge01.corp.example.com@CORP.EXAMPLE.COM", "version": 3, "enctype": "KERBEROS_ENCRYPTION_TYPE_AES256_CTS_HMAC_SHA1_96"}
KEYTAB_KEY = "a2V5IGJ5dGVzIGNhcHR1cmUgbXVzdCBuZXZlciB3cml0ZQ=="


def test_kerberos_setting_and_a_keytab_named_by_its_principals_without_its_keys(fake_api, run_module):
    held = {"entries": [{**KEYTAB_ENTRY, "key": KEYTAB_KEY}, {**KEYTAB_ENTRY, "principal": "host/edge01.corp.example.com@CORP.EXAMPLE.COM", "key": KEYTAB_KEY}]}
    cluster(fake_api, {KERBEROS_SETTINGS: {"use_alt_security_identities_mapping": True}, KERBEROS_KEYTAB: held})

    captured = capture(run_module, "kerberos")

    assert captured["host_vars"]["qumulo_kerberos"] == {"use_alt_security_identities_mapping": True}
    [line] = captured["supply"]
    assert line["variable"] == "qumulo_kerberos.keytab"
    assert "host/edge01.corp.example.com@CORP.EXAMPLE.COM, nfs/edge01.corp.example.com@CORP.EXAMPLE.COM" in line["why"]
    assert KEYTAB_KEY not in json.dumps(captured)
    unchanged(run_module, kerberos_settings, **captured["host_vars"]["qumulo_kerberos"])


def test_kerberos_for_a_user_who_may_not_read_the_keytab_is_the_setting_and_a_note(fake_api, run_module):
    """Spec 024 Decision 11: the keytab read feeds only the supply line, so a 403 on it notes and the capture goes on, as the
    key store's does (spec 022 Decision 6)."""
    fake = cluster(fake_api, {KERBEROS_SETTINGS: {"use_alt_security_identities_mapping": True}})
    fake.serve(KERBEROS_KEYTAB, {"error_class": "access_denied_error", "description": "the user lacks a privilege"}, status=403)

    captured = capture(run_module, "kerberos")

    assert captured["host_vars"]["qumulo_kerberos"] == {"use_alt_security_identities_mapping": True}
    assert captured["supply"] == []
    assert "PRIVILEGE_KERBEROS_KEYTAB_READ" in reasons(captured)["the keytab"]


@pytest.mark.parametrize("status", [409, 500])
def test_kerberos_fails_on_a_keytab_read_error_other_than_403(fake_api, run_module, status):
    fake = cluster(fake_api, {KERBEROS_SETTINGS: {"use_alt_security_identities_mapping": False}})
    fake.serve(KERBEROS_KEYTAB, {"error_class": "some_error", "description": "no"}, status=status)

    result = run_module(capture_info, {**CONNECTION, "gather_subset": ["kerberos"]})

    assert result["failed"] is True
    assert f"HTTP {status}" in result["msg"]


@pytest.mark.parametrize("held", [None, {"entries": []}], ids=["null", "no entries"])
def test_kerberos_as_formed_declares_the_setting_off_and_asks_for_no_keytab(fake_api, run_module, held):
    cluster(fake_api, {KERBEROS_SETTINGS: {"use_alt_security_identities_mapping": False}, KERBEROS_KEYTAB: held})

    captured = capture(run_module, "kerberos")

    assert captured["host_vars"]["qumulo_kerberos"] == {"use_alt_security_identities_mapping": False}
    assert captured["supply"] == []
    unchanged(run_module, kerberos_settings, **captured["host_vars"]["qumulo_kerberos"])


SAML_CERTIFICATE = "-----BEGIN CERTIFICATE-----\nAAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=\n-----END CERTIFICATE-----\n"
SAML_FACTORY = {"enabled": False, "idp_sso_url": "", "idp_certificate": "", "idp_entity_id": "", "cluster_dns_name": "", "require_sso": False}


def test_saml_settings_in_their_modules_shape(fake_api, run_module):
    saml = {
        **SAML_FACTORY,
        "enabled": True,
        "idp_sso_url": "https://idp.example.com/saml2/sso",
        "idp_certificate": SAML_CERTIFICATE,
        "idp_entity_id": "https://idp.example.com/metadata",
        "cluster_dns_name": "qumulo.corp.example.com",
    }
    cluster(fake_api, {"/v1/saml/settings": saml})

    captured = capture(run_module, "saml")

    # The certificate is public, so it is captured whole; the settings hold no secret.
    assert captured["host_vars"]["qumulo_saml"] == saml
    assert captured["supply"] == []
    unchanged(run_module, saml_settings, **captured["host_vars"]["qumulo_saml"])


def test_saml_never_set_up_declares_none(fake_api, run_module):
    cluster(fake_api, {"/v1/saml/settings": SAML_FACTORY})

    captured = capture(run_module, "saml")

    assert "qumulo_saml" not in captured["host_vars"]


def test_user_defined_mappings_sorted_in_their_modules_shape(fake_api, run_module):
    alice = {"down_level_logon_name": "CORP\\alice", "ldap_name": "alice"}
    bob = {"down_level_logon_name": "CORP\\bob", "ldap_name": "robert"}
    cluster(fake_api, {"/v1/auth/user-defined-mappings/": [bob, alice]})

    captured = capture(run_module, "identity_mappings")

    assert captured["host_vars"]["qumulo_user_defined_mappings"] == [alice, bob]
    # The module compares the list without regard to order and reads AD only before a write, so a check run reads no more.
    unchanged(run_module, user_defined_mappings, mappings=captured["host_vars"]["qumulo_user_defined_mappings"])


def test_no_user_defined_mappings_declares_none(fake_api, run_module):
    cluster(fake_api, {"/v1/auth/user-defined-mappings/": []})

    captured = capture(run_module, "identity_mappings")

    assert "qumulo_user_defined_mappings" not in captured["host_vars"]


HUB_HOSTS = [{"address": "10.0.0.20", "port": 3713}, {"address": "10.0.0.10", "port": 3713}]
SPOKE_HOSTS = [{"address": "10.0.1.5", "port": 3713}]


def spoke_record(hub_uuid=HUB_UUID, state="ACCEPTED", status="ACTIVE", local_root="100", hub_path="/data/", spoke_id=1):
    return {
        "id": spoke_id,
        "type": "PORTAL_READ_WRITE",
        "state": state,
        "status": status,
        "hub_hosts": HUB_HOSTS,
        "hub_id": 3,
        "hub_cluster_uuid": hub_uuid.upper(),
        "hub_cluster_name": "hub01",
        "filesystem_uuid": hub_uuid,
        "roots": [{"local_root": local_root, "remote_root": "900", "remote_root_path": hub_path, "authorized": True}],
    }


HUB = {
    "id": 3,
    "type": "PORTAL_READ_ONLY",
    "state": "ACCEPTED",
    "status": "ACTIVE",
    "spoke_hosts": SPOKE_HOSTS,
    "spoke_cluster_uuid": SPOKE_UUID,
    "spoke_cluster_name": "edge02",
    "filesystem_uuid": CLUSTER_ID,
    "pending_roots": [],
    "authorized_roots": ["200"],
}
ROOT_100 = {"/v1/files/100/info/attributes": {"path": "/edge-data/", "id": "100", "type": "FS_FILE_TYPE_DIRECTORY"}}
ROOT_200 = {"/v1/files/200/info/attributes": {"path": "/shared/", "id": "200", "type": "FS_FILE_TYPE_DIRECTORY"}}


def test_the_spoke_portal_names_its_hub_by_inventory_host_and_records_each_peers_addresses(fake_api, run_module):
    cluster(fake_api, {"/v2/portal/spokes/": {"entries": [spoke_record()]}, "/v2/portal/hubs/": {"entries": [HUB]}, **ROOT_100, **ROOT_200})

    captured = capture(run_module, "portal", inventory_clusters={HUB_UUID.upper(): "hub01"})

    portal = captured["host_vars"]["qumulo_portal"]
    assert portal == {"hub": "hub01", "type": "read_write", "roots": [{"spoke_root": "/edge-data", "hub_root": "/data"}]}
    # The spoke records the hub's addresses, and the hub the spoke's: each belongs to the other side's host.
    assert captured["peer_values"] == [
        {
            "uuid": HUB_UUID,
            "variable": "qumulo_portal_hosts",
            "value": sorted(HUB_HOSTS, key=lambda h: h["address"]),
            "recorded_by": CLUSTER_ID,
            "unusable": None,
        },
        {"uuid": SPOKE_UUID, "variable": "qumulo_portal_hosts", "value": SPOKE_HOSTS, "recorded_by": CLUSTER_ID, "unusable": None},
    ]
    assert captured["from_peers"] == ["qumulo_portal_hosts"]
    assert "qumulo_portal_hosts" not in captured["host_vars"]
    assert captured["spoke_roots"] == ["/edge-data"]
    # portal_pair passes the hub host's qumulo_portal_hosts as hub_hosts, and this host's as spoke_hosts on the hub.
    unchanged(run_module, portal_spoke, hub_cluster_uuid=HUB_UUID, hub_hosts=captured["peer_values"][0]["value"], type=portal["type"], roots=portal["roots"])
    unchanged(run_module, portal_hub, spoke_cluster_uuid=SPOKE_UUID, spoke_hosts=SPOKE_HOSTS, type="read_only", authorized_roots=["/shared"])


def test_a_spoke_portal_whose_hub_is_not_in_the_inventory_is_left_out(fake_api, run_module):
    cluster(fake_api, {"/v2/portal/spokes/": {"entries": [spoke_record()]}, "/v2/portal/hubs/": {"entries": []}, **ROOT_100})

    captured = capture(run_module, "portal")

    assert "qumulo_portal" not in captured["host_vars"]
    # A clone of this cluster leaves out what lies in the root, though the portal is not declared.
    assert captured["spoke_roots"] == ["/edge-data"]
    assert captured["left_out"] == [
        {
            "what": f"the spoke portal to hub {HUB_UUID}",
            "why": "no inventory host has that qumulo_cluster_uuid; add the hub to the inventory and capture again",
        }
    ]


@pytest.mark.parametrize(
    "spoke, state, why",
    [
        (spoke_record(hub_path=""), ROOT_100, "the hub has not reported the path of every root"),
        (spoke_record(state="DELETING", status="DISCONNECTED"), ROOT_100, "it is DELETING, not ACCEPTED"),
        (spoke_record(), {"/v1/files/100/info/attributes": NO_SUCH_ENTRY}, "a root's directory on this cluster no longer exists"),
    ],
    ids=["hub-root-unknown", "deleting", "spoke-root-gone"],
)
def test_a_spoke_portal_that_cannot_be_declared_is_left_out(fake_api, run_module, spoke, state, why):
    fake = cluster(fake_api, {"/v2/portal/spokes/": {"entries": [spoke]}, "/v2/portal/hubs/": {"entries": []}})
    for path, response in state.items():
        fake.serve(path, response, status=404 if response is NO_SUCH_ENTRY else 200)

    captured = capture(run_module, "portal", inventory_clusters={HUB_UUID: "hub01"})

    assert "qumulo_portal" not in captured["host_vars"]
    assert why in reasons(captured)[f"the spoke portal to hub {HUB_UUID}"]


def test_a_portal_record_that_is_not_live_is_marked_unusable_and_a_deleting_one_is_not_recorded(fake_api, run_module):
    disconnected = spoke_record(status="DISCONNECTED")
    deleting = {**HUB, "state": "DELETING", "status": "DISCONNECTED"}
    cluster(fake_api, {"/v2/portal/spokes/": {"entries": [disconnected]}, "/v2/portal/hubs/": {"entries": [deleting]}, **ROOT_100, **ROOT_200})

    captured = capture(run_module, "portal", inventory_clusters={HUB_UUID: "hub01"})

    assert [(record["uuid"], record["unusable"]) for record in captured["peer_values"]] == [(HUB_UUID, "ACCEPTED and DISCONNECTED")]
    # The portal itself is still declared: its pairing is ACCEPTED, and only the copy of its addresses waits.
    assert captured["host_vars"]["qumulo_portal"]["hub"] == "hub01"


def test_two_spoke_portals_declare_the_first_hub_and_note_the_other(fake_api, run_module):
    second = spoke_record(hub_uuid=PEER_UUID, local_root="101", hub_path="/more/", spoke_id=2)
    root_101 = {"/v1/files/101/info/attributes": {"path": "/more-data/", "id": "101", "type": "FS_FILE_TYPE_DIRECTORY"}}
    cluster(fake_api, {"/v2/portal/spokes/": {"entries": [spoke_record(), second]}, "/v2/portal/hubs/": {"entries": []}, **ROOT_100, **root_101})

    captured = capture(run_module, "portal", inventory_clusters={HUB_UUID: "hub01", PEER_UUID: "hub02"})

    assert captured["host_vars"]["qumulo_portal"]["hub"] == "hub01"
    assert reasons(captured)[f"the spoke portal to hub {PEER_UUID}"] == "qumulo_portal declares one portal, and another is declared"


def share(name, fs_path, share_id, **overrides):
    return {
        "id": share_id,
        "share_name": name,
        "tenant_id": 1,
        "fs_path": fs_path,
        "description": "",
        "permissions": [{"type": "ALLOWED", "trustee": EVERYONE, "rights": ["READ"]}],
        "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ["READ", "WRITE", "CHANGE_PERMISSIONS"]}],
        "access_based_enumeration_enabled": False,
        "default_file_create_mode": "0644",
        "default_directory_create_mode": "0755",
        "require_encryption": False,
        "allow_fs_path_create": False,
        "expand_fs_path_variables": False,
        "offline_files_caching_mode": "NO_CACHING",
        **overrides,
    }


RESTRICTION = {
    "host_restrictions": ["10.0.0.0/24"],
    "read_only": True,
    "require_privileged_port": False,
    "required_authentication_mode": "AUTHENTICATION_MODE_NONE",
    "user_mapping": "NFS_MAP_NONE",
}


def export(export_path, fs_path, export_id, restrictions=(RESTRICTION,)):
    return {
        "id": export_id,
        "export_path": export_path,
        "tenant_id": 1,
        "fs_path": fs_path,
        "description": "projects",
        "restrictions": list(restrictions),
        "fields_to_present_as_32_bit": [],
    }


def quota(path, limit, quota_id):
    return {"id": quota_id, "path": path + "/", "limit": str(limit), "capacity_usage": "4096"}


DAILY = {"timezone": "UTC", "frequency": "SCHEDULE_DAILY_OR_WEEKLY", "hour": 2, "minute": 30, "on_days": ["MON", "FRI"]}
HOURLY = {
    "timezone": "UTC",
    "frequency": "SCHEDULE_HOURLY_OR_LESS",
    "on_days": EVERY_DAY,
    "window_start_hour": 8,
    "window_start_minute": 0,
    "window_end_hour": 18,
    "window_end_minute": 0,
    "fire_every_interval": "FIRE_IN_HOURS",
    "fire_every": 2,
}


def policy(name, path, policy_id, schedule=None, ttl="7days"):
    return {
        "id": policy_id,
        "policy_name": name,
        "snapshot_name_template": "{ID}_{Policy}_{Directory}",
        "source_file_id": str(100 + policy_id),
        "source_file_path": path + "/",
        "schedule": {"id": policy_id, "creation_schedule": schedule or DAILY, "expiration_time_to_live": ttl},
        "enabled": True,
        "lock_key": None,
        "owners": [],
    }


def replication_record(source_uuid, source_path, target_uuid, target_path, state="ESTABLISHED", record_id="r1", **settings):
    return {
        "id": record_id,
        "state": state,
        "end_reason": "",
        "source_cluster_uuid": source_uuid,
        "source_root_path": source_path + "/",
        "target_cluster_uuid": target_uuid,
        "target_root_path": target_path + "/",
        "target_root_read_only": True,
        "job_state": "REPLICATION_NOT_RUNNING",
        "recovery_point": "",
        "error_from_last_job": "",
        "target_address": settings.pop("target_address", None),
        "target_port": settings.pop("target_port", None),
        **settings,
    }


MODE_RIGHTS = ["READ", "READ_EA", "READ_ATTR", "READ_ACL", "EXECUTE", "SYNCHRONIZE"]
MODE_ACL = file_acl_response([file_acl_entry(ADMIN, MODE_RIGHTS), file_acl_entry(USERS, MODE_RIGHTS), file_acl_entry(EVERYONE, MODE_RIGHTS)])


def acl_of(path, mode, acl=None, from_mode=None):
    """A directory's ACL, and the ACL setting its own mode would leave, as POST explain-set-mode previews it for that
    mode only; MODE_ACL by default."""
    acl, from_mode = acl or MODE_ACL, from_mode or MODE_ACL
    preview = Query({"mode": mode}, {"initial_acl": acl, "result_acl": from_mode})
    return {acl_path(path): acl, f"POST /v1/files/{ref(path)}/info/acl/explain-set-mode": preview}


def s3_bucket_record(name, path, versioning="Unversioned", lock=None):
    return {
        "name": name,
        "creation_time": "2026-11-01T00:00:00Z",
        "path": path,
        "anonymous_access_enabled": False,
        "versioning": versioning,
        "lock_config": lock or {"enabled": False, "default_retention": None},
    }


DATA = {
    "/v3/smb/shares/?populate-trustee-names=true": {"entries": [share("projects", "/data/", "1"), share("edge", "/edge-data/sub/", "2")]},
    "/v3/nfs/exports/": {
        "entries": [
            export("/projects", "/data/projects", "1"),
            export("/data", "/data", "2"),
            export("/gone", "/gone", "3"),
            export("/replica", "/replicas/peer-projects/sub", "4"),
        ]
    },
    "/v1/files/quotas/status/?limit=1000": {
        "quotas": [quota("/data/projects", 10**12, "11"), quota("/edge-data/q", 10**9, "12")],
        "paging": {"next": ""},
    },
    "/v3/snapshots/policies/status/": {"entries": [policy("projects-daily", "/data/projects", 1), policy("edge-daily", "/edge-data", 2)]},
    "/v1/file-system/security/keys/": {"entries": []},
    "/v2/replication/source-relationships/status/": [],
    "/v2/replication/target-relationships/status/": [
        replication_record(PEER_UUID, "/projects", CLUSTER_ID, "/replicas/peer-projects", record_id="r1"),
        replication_record(PEER_UUID, "/old", CLUSTER_ID, "/replicas/old", state="ENDED", record_id="r2"),
    ],
    "/v2/portal/spokes/": {"entries": [spoke_record()]},
    "/v2/portal/hubs/": {"entries": []},
    "/v1/s3/buckets/": {"buckets": [s3_bucket_record("photos", "/data/photos/")]},
    # A base path with no bucket under it, where an S3 client's CreateBucket would put one.
    "/v1/s3/settings": {"enabled": True, "base_path": "/s3-base/", "multipart_upload_expiry_interval": "1days", "secure": True},
    **ROOT_100,
    attributes_path("/"): attributes("/", mode="0777"),
    attributes_path("/data/photos"): attributes("/data/photos", file_id="105", mode="0770"),
    attributes_path("/data"): attributes("/data", file_id="101", mode="0755"),
    attributes_path("/data/projects"): attributes("/data/projects", file_id="102", mode="0750", group=GUEST),
    attributes_path("/replicas"): attributes("/replicas", file_id="103", mode="0700"),
    attributes_path("/replicas/peer-projects"): attributes("/replicas/peer-projects", file_id="104", mode="0555"),
    **acl_of("/", "0777"),
    **acl_of("/data", "0755"),
    **acl_of("/data/photos", "0770"),
    **acl_of("/data/projects", "0750"),
    **acl_of("/replicas", "0700"),
    attributes_path("/s3-base"): attributes("/s3-base", file_id="106", mode="0750"),
    **acl_of("/s3-base", "0750"),
}


def test_directories_are_those_the_data_areas_name_and_every_one_above(fake_api, run_module):
    fake = cluster(fake_api, DATA, optional=True)
    fake.serve(attributes_path("/gone"), NO_SUCH_ENTRY, status=404)

    captured = capture(run_module, "directories")

    directories = captured["host_vars"]["qumulo_directories"]
    assert directories == [
        {"path": "/", "mode": "0777", "owner": "local:admin", "group": "local:Users"},
        # /data is named twice, by a share and an export, and above an export.
        {"path": "/data", "mode": "0755", "owner": "local:admin", "group": "local:Users"},
        # An S3 bucket names its directory too.
        {"path": "/data/photos", "mode": "0770", "owner": "local:admin", "group": "local:Users"},
        {"path": "/data/projects", "mode": "0750", "owner": "local:admin", "group": "local:guest"},
        {"path": "/replicas", "mode": "0700", "owner": "local:admin", "group": "local:Users"},
        # Replication sets a target directory's attributes and its content's, so the export inside it names nothing.
        {"path": "/replicas/peer-projects"},
        # The S3 base path, which s3_settings requires to exist.
        {"path": "/s3-base", "mode": "0750", "owner": "local:admin", "group": "local:Users"},
    ]
    why = reasons(captured)
    assert set(why) == {"the directory /edge-data", "the directory /edge-data/q", "the directory /edge-data/sub", "the directory /gone"}
    assert "spoke root /edge-data" in why["the directory /edge-data/sub"]
    assert "does not exist" in why["the directory /gone"]
    assert captured["replication_targets"] == ["/replicas/peer-projects"]
    for entry in directories:
        unchanged(run_module, directory, **entry)
    # Each ACL is the one its mode leaves, so the mode declares it whole.
    assert "qumulo_file_acls" not in captured["host_vars"]


def test_an_acl_its_mode_cannot_express_is_declared_in_place_of_the_mode(fake_api, run_module):
    acl = file_acl_response(
        [
            file_acl_entry(GUEST, ["DELETE", "DELETE_CHILD"], kind="DENIED"),
            file_acl_entry(ADMIN, MODE_RIGHTS, flags=["OBJECT_INHERIT", "CONTAINER_INHERIT"]),
        ],
        control=["PRESENT", "AUTO_INHERIT"],
    )
    state = {
        "/v3/smb/shares/?populate-trustee-names=true": {"entries": [share("projects", "/data/", "1")]},
        "/v3/nfs/exports/": {"entries": []},
        "/v1/files/quotas/status/?limit=1000": {"quotas": [], "paging": {"next": ""}},
        "/v3/snapshots/policies/status/": {"entries": []},
        "/v2/replication/target-relationships/status/": [],
        "/v2/portal/spokes/": {"entries": []},
        "/v1/s3/settings": S3_EMPTY["/v1/s3/settings"],
        "/v1/s3/buckets/": S3_EMPTY["/v1/s3/buckets/"],
        attributes_path("/"): attributes("/", mode="0777"),
        attributes_path("/data"): attributes("/data", file_id="101", mode="0750"),
        **acl_of("/", "0777"),
        **acl_of("/data", "0750", acl=acl),
    }
    cluster(fake_api, state)

    captured = capture(run_module, "directories")

    hv = captured["host_vars"]
    assert hv["qumulo_directories"] == [
        {"path": "/", "mode": "0777", "owner": "local:admin", "group": "local:Users"},
        {"path": "/data", "owner": "local:admin", "group": "local:Users"},
    ]
    assert hv["qumulo_file_acls"] == [
        {
            "path": "/data",
            "control": ["PRESENT", "AUTO_INHERIT"],
            "posix_special_permissions": [],
            "aces": [
                {"type": "DENIED", "trustee": "local:guest", "rights": ["DELETE", "DELETE_CHILD"], "flags": []},
                {"type": "ALLOWED", "trustee": "local:admin", "rights": MODE_RIGHTS, "flags": ["OBJECT_INHERIT", "CONTAINER_INHERIT"]},
            ],
        }
    ]
    for entry in hv["qumulo_directories"]:
        unchanged(run_module, directory, **entry)
    unchanged(run_module, file_acl, **hv["qumulo_file_acls"][0])


def test_an_acl_naming_authenticated_users_round_trips_on_a_node_that_refuses_its_sid(fake_api, run_module):
    acl = file_acl_response([file_acl_entry(AUTHENTICATED_USERS, MODE_RIGHTS, flags=["OBJECT_INHERIT", "CONTAINER_INHERIT"])])
    state = {
        "/v3/smb/shares/?populate-trustee-names=true": {"entries": [share("projects", "/data/", "1")]},
        "/v3/nfs/exports/": {"entries": []},
        "/v1/files/quotas/status/?limit=1000": {"quotas": [], "paging": {"next": ""}},
        "/v3/snapshots/policies/status/": {"entries": []},
        "/v2/replication/target-relationships/status/": [],
        "/v2/portal/spokes/": {"entries": []},
        "/v1/s3/settings": S3_EMPTY["/v1/s3/settings"],
        "/v1/s3/buckets/": S3_EMPTY["/v1/s3/buckets/"],
        attributes_path("/"): attributes("/", mode="0777"),
        attributes_path("/data"): attributes("/data", file_id="101", mode="0750"),
        **acl_of("/", "0777"),
        **acl_of("/data", "0750", acl=acl),
    }
    cluster(fake_api, state).find_errors.append(UNSEEN_SID_DOMAIN)

    captured = capture(run_module, "directories")

    (declared,) = captured["host_vars"]["qumulo_file_acls"]
    assert [ace["trustee"] for ace in declared["aces"]] == ["ad:NT AUTHORITY\\Authenticated Users"]
    unchanged(run_module, file_acl, **declared)


def test_an_acl_naming_file_owner_and_file_group_owner_round_trips(fake_api, run_module):
    inherit = ["OBJECT_INHERIT", "CONTAINER_INHERIT", "INHERIT_ONLY"]
    acl = file_acl_response([file_acl_entry(FILE_OWNER, MODE_RIGHTS, flags=inherit), file_acl_entry(FILE_GROUP_OWNER, MODE_RIGHTS, flags=inherit)])
    state = {
        "/v3/smb/shares/?populate-trustee-names=true": {"entries": [share("projects", "/data/", "1")]},
        "/v3/nfs/exports/": {"entries": []},
        "/v1/files/quotas/status/?limit=1000": {"quotas": [], "paging": {"next": ""}},
        "/v3/snapshots/policies/status/": {"entries": []},
        "/v2/replication/target-relationships/status/": [],
        "/v2/portal/spokes/": {"entries": []},
        "/v1/s3/settings": S3_EMPTY["/v1/s3/settings"],
        "/v1/s3/buckets/": S3_EMPTY["/v1/s3/buckets/"],
        attributes_path("/"): attributes("/", mode="0777"),
        attributes_path("/data"): attributes("/data", file_id="101", mode="0750"),
        **acl_of("/", "0777"),
        **acl_of("/data", "0750", acl=acl),
    }
    cluster(fake_api, state)

    captured = capture(run_module, "directories")

    (declared,) = captured["host_vars"]["qumulo_file_acls"]
    assert [ace["trustee"] for ace in declared["aces"]] == ["internal:File Owner", "internal:File Group Owner"]
    unchanged(run_module, file_acl, **declared)


SMB_SETTINGS = {
    "session_encryption": "NONE",
    "supported_dialects": ["SMB2_DIALECT_3_0", "SMB2_DIALECT_3_11"],
    "hide_shares_from_unauthorized_users": False,
    "hide_shares_from_unauthorized_hosts": False,
    "snapshot_directory_mode": "VISIBLE",
    "bypass_traverse_checking": False,
    "signing_required": True,
}


def test_smb_settings_shares_in_acl_order_and_the_factory_share_absent(fake_api, run_module):
    acl = [
        {"type": "DENIED", "trustee": GUEST, "rights": ["WRITE"]},
        {"type": "ALLOWED", "trustee": UID_1000, "rights": ["READ", "WRITE"]},
        {"type": "ALLOWED", "trustee": EVERYONE, "rights": ["READ"]},
    ]
    shares = [share("zeta", "/data/", "3"), share("alpha", "/data/", "4"), share("beta", "/data/", "5", permissions=acl)]
    state = {"/v1/smb/settings": SMB_SETTINGS, "/v3/smb/shares/?populate-trustee-names=true": {"entries": shares}, "/v3/smb/shares/": {"entries": shares}}
    fake = cluster(fake_api, {**state, **ONE_TENANT_SMB})
    for entry in shares:
        fake.serve(f"/v3/smb/shares/{entry['id']}", entry)

    captured = capture(run_module, "smb")

    hv = captured["host_vars"]
    assert hv["qumulo_smb_settings"] == SMB_SETTINGS
    # With one tenant and the tenants area not captured, the flat form is exact, and names no tenant.
    assert [entry["share_name"] for entry in hv["qumulo_smb_shares"]] == ["alpha", "beta", "zeta"]
    assert hv["qumulo_smb_shares"][1] == {
        "share_name": "beta",
        "fs_path": "/data",
        "description": "",
        # In the ACL's order, the deny entry first; a trustee with no name by its uid.
        "permissions": [
            {"type": "DENIED", "trustee": "local:guest", "rights": ["WRITE"]},
            {"type": "ALLOWED", "trustee": "uid:1000", "rights": ["READ", "WRITE"]},
            {"type": "ALLOWED", "trustee": "world:Everyone", "rights": ["READ"]},
        ],
        "network_permissions": [{"type": "ALLOWED", "address_ranges": [], "rights": ALL_RIGHTS}],
        "access_based_enumeration_enabled": False,
        "require_encryption": False,
        "default_file_create_mode": "0644",
        "default_directory_create_mode": "0755",
        "expand_fs_path_variables": False,
        "offline_files_caching_mode": "NO_CACHING",
    }
    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "absent"}
    unchanged(run_module, smb_settings, **SMB_SETTINGS)
    for entry in hv["qumulo_smb_shares"]:
        unchanged(run_module, smb_share, **entry)
    unchanged(run_module, smb_share, share_name="Files", state="absent")


def test_a_share_naming_authenticated_users_round_trips_on_a_node_that_refuses_its_sid(fake_api, run_module):
    shares = [share("projects", "/data/", "3", permissions=[{"type": "ALLOWED", "trustee": AUTHENTICATED_USERS, "rights": ["READ"]}])]
    state = {"/v1/smb/settings": SMB_SETTINGS, "/v3/smb/shares/?populate-trustee-names=true": {"entries": shares}, "/v3/smb/shares/": {"entries": shares}}
    fake = cluster(fake_api, {**state, **ONE_TENANT_SMB})
    fake.serve("/v3/smb/shares/3", shares[0])
    fake.find_errors.append(UNSEEN_SID_DOMAIN)

    captured = capture(run_module, "smb")

    (declared,) = captured["host_vars"]["qumulo_smb_shares"]
    assert declared["permissions"] == [{"type": "ALLOWED", "trustee": "ad:NT AUTHORITY\\Authenticated Users", "rights": ["READ"]}]
    unchanged(run_module, smb_share, **declared)


def test_the_factory_share_present_is_declared_present(fake_api, run_module):
    shares = [share("Files", "/", "2")]
    cluster(
        fake_api, {"/v1/smb/settings": {"session_encryption": "NONE"}, "/v3/smb/shares/?populate-trustee-names=true": {"entries": shares}, **ONE_TENANT_SMB}
    )

    captured = capture(run_module, "smb")

    assert captured["host_vars"]["qumulo_factory_defaults"] == {"smb_files_share": "present"}
    assert [entry["share_name"] for entry in captured["host_vars"]["qumulo_smb_shares"]] == ["Files"]


NFS_SETTINGS = {"v4_enabled": True, "krb5_enabled": True, "krb5p_enabled": True, "krb5i_enabled": True, "auth_sys_enabled": True, "idmap_domain": None}


def test_nfs_settings_exports_and_the_factory_export_absent(fake_api, run_module):
    mapped = {
        **RESTRICTION,
        "host_restrictions": ["10.0.1.0/24"],
        "read_only": False,
        "user_mapping": "NFS_MAP_ALL",
        "map_to_user": {"id_type": "LOCAL_USER", "id_value": "guest"},
    }
    exports = [export("/projects", "/data/projects", "1", restrictions=[RESTRICTION, mapped]), export("/archive", "/data/archive", "2")]
    fake = cluster(fake_api, {"/v2/nfs/settings": NFS_SETTINGS, "/v3/nfs/exports/": {"entries": exports}, **ONE_TENANT_NFS})
    for entry in exports:
        fake.serve(f"/v3/nfs/exports/{quote(entry['id'], safe='')}", entry)

    captured = capture(run_module, "nfs")

    hv = captured["host_vars"]
    assert hv["qumulo_nfs_settings"] == {**NFS_SETTINGS, "idmap_domain": ""}
    assert [entry["export_path"] for entry in hv["qumulo_nfs_exports"]] == ["/archive", "/projects"]
    assert hv["qumulo_nfs_exports"][1] == {
        "export_path": "/projects",
        "fs_path": "/data/projects",
        "description": "projects",
        "restrictions": [
            RESTRICTION,
            {**RESTRICTION, "host_restrictions": ["10.0.1.0/24"], "read_only": False, "user_mapping": "NFS_MAP_ALL", "map_to_user": "local:guest"},
        ],
        "fields_to_present_as_32_bit": [],
    }
    assert hv["qumulo_factory_defaults"] == {"nfs_root_export": "absent"}
    unchanged(run_module, nfs_settings, **hv["qumulo_nfs_settings"])
    for entry in hv["qumulo_nfs_exports"]:
        unchanged(run_module, nfs_export, **entry)
    unchanged(run_module, nfs_export, export_path="/", state="absent")


def test_an_export_mapped_to_a_deleted_user_is_left_out(fake_api, run_module):
    deleted = {**RESTRICTION, "user_mapping": "NFS_MAP_ALL", "map_to_user": {"id_type": "LOCAL_USER", "id_value": ""}}
    exports = [export("/", "/", "1"), export("/orphan", "/data", "2", restrictions=[deleted])]
    cluster(fake_api, {"/v2/nfs/settings": NFS_SETTINGS, "/v3/nfs/exports/": {"entries": exports}, **ONE_TENANT_NFS})

    captured = capture(run_module, "nfs")

    assert [entry["export_path"] for entry in captured["host_vars"]["qumulo_nfs_exports"]] == ["/"]
    assert captured["host_vars"]["qumulo_factory_defaults"] == {"nfs_root_export": "present"}
    assert "G-MAPTO" in reasons(captured)["the NFS export /orphan"]


def test_require_tls_is_captured_when_set_and_left_out_when_false(fake_api, run_module, monkeypatch, tmp_path):
    # From 7.11.0 every restriction holds require_tls, false unless set; 7.10.3 has no such field.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))  # where the require_tls gate caches what it read of /openapi.json
    tls = {**RESTRICTION, "host_restrictions": ["10.0.1.0/24"], "require_tls": True}
    exports = [export("/projects", "/data/projects", "1", restrictions=[{**RESTRICTION, "require_tls": False}, tls])]
    restriction_schema = {"type": "object", "properties": {"read_only": {"type": "boolean"}, "require_tls": {"type": "boolean"}}}
    request = {"type": "object", "properties": {"restrictions": {"type": "array", "items": restriction_schema}}}
    openapi = {"paths": {"/v3/nfs/exports/": {"post": {"requestBody": {"content": {"application/json": {"schema": request}}}}}}}
    state = {"/v2/nfs/settings": NFS_SETTINGS, "/v3/nfs/exports/": {"entries": exports}, "/v3/nfs/exports/1": exports[0], "/openapi.json": openapi}
    cluster(fake_api, {**state, **ONE_TENANT_NFS}, version="7.11.0")

    captured = capture(run_module, "nfs")

    (entry,) = captured["host_vars"]["qumulo_nfs_exports"]
    assert entry["restrictions"] == [RESTRICTION, tls]
    unchanged(run_module, nfs_export, **entry)


S3_SETTINGS = {"enabled": True, "base_path": "/s3/", "multipart_upload_expiry_interval": "2weeks", "secure": True}
# Every route the s3 area reads, empty, for the S3 tests below: each serves what it states, and the rest of these.
S3_EMPTY = {
    "/v1/s3/settings": {"enabled": False, "base_path": "/", "multipart_upload_expiry_interval": "1days", "secure": True},
    "/v1/s3/buckets/": {"buckets": []},
    "/v1/s3/access-keys/?limit=1000": {"entries": [], "paging": {"next": None}},
}


def s3_cluster(fake_api, state, buckets=()):
    """cluster() serving state, and each route of S3_EMPTY that state does not give, which the area may read or not.

    Each of buckets has no policy, which the area may read or not.
    """
    fake = cluster(fake_api, state)
    quiet = {**S3_EMPTY, **{f"/v1/s3/buckets/{name}/policy": {} for name in buckets}}
    for path, response in quiet.items():
        if path not in state:
            fake.serve(path, response, optional=True)
    return fake


def test_s3_settings_in_the_form_the_cluster_reads_back(fake_api, run_module):
    s3_cluster(fake_api, {"/v1/s3/settings": S3_SETTINGS})

    captured = capture(run_module, "s3")

    hv = captured["host_vars"]
    assert hv["qumulo_s3_settings"] == {"enabled": True, "base_path": "/s3", "multipart_upload_expiry_interval": "2weeks", "secure": True}
    unchanged(run_module, s3_settings, **hv["qumulo_s3_settings"])


FTP_SETTINGS = {
    "enabled": True,
    "check_remote_host": True,
    "log_operations": False,
    "chroot_users": True,
    "allow_unencrypted_connections": False,
    "expand_wildcards": False,
    "anonymous_user": {"id_type": "LOCAL_USER", "id_value": "ftp-guest"},
    "greeting": "Authorized use only.\n",
}


def test_ftp_settings_in_their_modules_shape(fake_api, run_module):
    cluster(fake_api, {"/v0/ftp/settings": FTP_SETTINGS})

    captured = capture(run_module, "ftp")

    settings = captured["host_vars"]["qumulo_ftp_settings"]
    assert settings == {**FTP_SETTINGS, "anonymous_user": "local:ftp-guest"}
    unchanged(run_module, ftp_settings, **settings)


def test_an_anonymous_user_that_names_a_deleted_user_is_left_out(fake_api, run_module):
    """ftp_settings cannot declare local: with no name, so a check run from the capture would fail on it."""
    cluster(fake_api, {"/v0/ftp/settings": {**FTP_SETTINGS, "anonymous_user": {"id_type": "LOCAL_USER", "id_value": ""}}})

    captured = capture(run_module, "ftp")

    settings = captured["host_vars"]["qumulo_ftp_settings"]
    assert "anonymous_user" not in settings
    assert "deleted" in reasons(captured)["the FTP anonymous user"]
    unchanged(run_module, ftp_settings, **settings)


def test_an_anonymous_user_that_is_not_a_local_user_is_left_out_naming_it(fake_api, run_module):
    cluster(fake_api, {"/v0/ftp/settings": {**FTP_SETTINGS, "anonymous_user": {"id_type": "NFS_UID", "id_value": "1000"}}})

    captured = capture(run_module, "ftp")

    settings = captured["host_vars"]["qumulo_ftp_settings"]
    assert "anonymous_user" not in settings
    assert "nfs_uid:1000" in reasons(captured)["the FTP anonymous user"]
    unchanged(run_module, ftp_settings, **settings)


def test_a_cluster_that_does_not_offer_ftp_leaves_the_area_out(fake_api, run_module):
    fake = cluster(fake_api, {})
    fake.serve("/v0/ftp/settings", NOT_FOUND, status=404)

    result = run_module(capture_info, {**CONNECTION, "gather_subset": ["ftp"]})

    assert "qumulo_ftp_settings" not in result["capture"]["host_vars"]
    assert "ftp" in result["unavailable_subsets"]


def test_s3_buckets_outside_the_spoke_roots(fake_api, run_module):
    locked = {"enabled": True, "default_retention": {"units": "days", "value": 30}}
    buckets = [s3_bucket_record("zeta", "/s3/zeta/", "Enabled", locked), s3_bucket_record("edge", "/edge-data/photos"), s3_bucket_record("alpha", "/s3/alpha")]
    s3_cluster(fake_api, {"/v1/s3/buckets/": {"buckets": buckets}, "/v2/portal/spokes/": {"entries": [spoke_record()]}, **ROOT_100}, buckets=["alpha", "zeta"])

    captured = capture(run_module, "s3")

    hv = captured["host_vars"]
    assert hv["qumulo_s3_buckets"] == [
        {"name": "alpha", "path": "/s3/alpha", "versioning": "Unversioned", "object_lock": False},
        {"name": "zeta", "path": "/s3/zeta", "versioning": "Enabled", "object_lock": True, "default_retention": {"value": 30, "units": "days"}},
    ]
    assert "the S3 bucket edge" in reasons(captured)
    for entry in hv["qumulo_s3_buckets"]:
        unchanged(run_module, s3_bucket, **entry)


ALICE = {"domain": "ACTIVE_DIRECTORY", "auth_id": "17179869185", "uid": None, "gid": None, "sid": "S-1-5-21-7-8-9-1105", "name": "CORP\\alice"}


def test_s3_bucket_policies_with_each_principal_as_a_trustee(fake_api, run_module):
    policy = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["local:guest", "sid:S-1-5-88-1-1000", "world:Everyone"]}}],
    }
    # The cluster names an AD principal without a prefix; capture writes it as the trustee it resolves to.
    by_name = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["CORP\\alice"]}}]}
    unknown = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["CORP\\gone"]}}]}
    buckets = [s3_bucket_record(name, f"/s3/{name}") for name in ("zeta", "gamma", "beta", "alpha")]
    policies = {"/v1/s3/buckets/alpha/policy": {}, "/v1/s3/buckets/beta/policy": by_name, "/v1/s3/buckets/gamma/policy": unknown}
    fake = s3_cluster(
        fake_api, {"/v1/s3/buckets/": {"buckets": buckets}, "/v2/portal/spokes/": {"entries": []}, **policies, "/v1/s3/buckets/zeta/policy": policy}
    )
    fake.identities.append(ALICE)

    captured = capture(run_module, "s3")

    hv = captured["host_vars"]
    assert hv["qumulo_s3_bucket_policies"] == [
        {"bucket": "beta", "policy": {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["ad:CORP\\alice"]}}]}},
        {"bucket": "zeta", "policy": policy},
    ]
    # A principal no identity answers to leaves its policy out; the bucket is still declared.
    assert "CORP\\gone" in reasons(captured)["the policy of the S3 bucket gamma"]
    assert [bucket["name"] for bucket in hv["qumulo_s3_buckets"]] == ["alpha", "beta", "gamma", "zeta"]
    for entry in hv["qumulo_s3_bucket_policies"]:
        unchanged(run_module, s3_bucket_policy, **entry)


def test_an_s3_policy_naming_authenticated_users_round_trips_on_a_node_that_refuses_its_sid(fake_api, run_module):
    # 7.11.0 reads the group back as ad:Authenticated Users, a spelling the grammar refuses.
    policy = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["ad:Authenticated Users"]}}]}
    fake = s3_cluster(
        fake_api,
        {
            "/v1/s3/buckets/": {"buckets": [s3_bucket_record("alpha", "/s3/alpha")]},
            "/v2/portal/spokes/": {"entries": []},
            "/v1/s3/buckets/alpha/policy": policy,
        },
    )
    fake.find_errors.append(UNSEEN_SID_DOMAIN)

    captured = capture(run_module, "s3")

    (declared,) = captured["host_vars"]["qumulo_s3_bucket_policies"]
    assert declared["policy"]["Statement"][0]["Principal"] == {"Qumulo": ["ad:NT AUTHORITY\\Authenticated Users"]}
    unchanged(run_module, s3_bucket_policy, **declared)


def test_an_s3_policy_naming_file_owner_round_trips(fake_api, run_module):
    # 7.11.0 reads a principal of File Owner back as the bare name, which the identity find answers.
    policy = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Principal": {"Qumulo": ["File Owner"]}}]}
    s3_cluster(
        fake_api,
        {
            "/v1/s3/buckets/": {"buckets": [s3_bucket_record("alpha", "/s3/alpha")]},
            "/v2/portal/spokes/": {"entries": []},
            "/v1/s3/buckets/alpha/policy": policy,
        },
    )

    captured = capture(run_module, "s3")

    (declared,) = captured["host_vars"]["qumulo_s3_bucket_policies"]
    assert declared["policy"]["Statement"][0]["Principal"] == {"Qumulo": ["internal:File Owner"]}
    unchanged(run_module, s3_bucket_policy, **declared)


def test_s3_access_keys_by_their_ids_with_each_secret_to_supply(fake_api, run_module):
    def key(key_id, owner):
        return {"access_key_id": key_id, "owner": dict(owner), "creation_time": "2026-11-01T00:00:00Z"}

    entries = [key("00000000000001f5b002", GUEST), key("00000000000001f4a001", ADMIN), key("00000000000001f5a001", GUEST)]
    s3_cluster(
        fake_api,
        {
            "/v1/s3/access-keys/?limit=1000": {"entries": entries, "paging": {"next": None}},
            "/v1/s3/access-keys/?user=auth_id%3A500": {"entries": [entries[1]], "paging": {"next": None}},
            "/v1/s3/access-keys/?user=auth_id%3A501": {"entries": [entries[2], entries[0]], "paging": {"next": None}},
        },
    )

    captured = capture(run_module, "s3")

    hv = captured["host_vars"]
    assert hv["qumulo_s3_access_keys"] == [
        {"user": "local:admin", "access_key_id": "00000000000001f4a001"},
        {"user": "local:guest", "access_key_id": "00000000000001f5a001"},
        {"user": "local:guest", "access_key_id": "00000000000001f5b002"},
    ]
    # The cluster never returns a secret, so each key's is named for the inventory to supply to its consumers.
    supplied = {entry["variable"]: entry["why"] for entry in captured["supply"]}
    assert set(supplied) == {f"qumulo_s3_access_keys[{entry['access_key_id']}]" for entry in entries}
    assert all("G-S3-KEY" in why and "artifact directory" in why for why in supplied.values())
    assert "secret_access_key" not in str(hv)
    for entry in hv["qumulo_s3_access_keys"]:
        unchanged(run_module, s3_access_key, **entry)


def test_each_security_key_is_left_out_by_name_and_id_with_no_key_material(fake_api, run_module):
    """A key is registered only with its private key or a signature of its name (spec 022, Decision 8)."""
    public_key = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEexample"  # not a real key: capture must not copy it
    cluster(
        fake_api,
        {
            "/v3/snapshots/policies/status/": {"entries": []},
            "/v1/file-system/security/keys/": {
                "entries": [
                    {"id": "2", "name": "legal-hold", "comment": "holds", "disabled": False, "public_key": public_key},
                    {"id": "3", "name": "archive", "comment": "", "disabled": True, "public_key": public_key},
                ]
            },
        },
    )

    captured = capture(run_module, "snapshots")

    why = reasons(captured)
    assert set(why) == {"the security key legal-hold (id 2)", "the security key archive (id 3)"}
    assert all("qumulo.cluster.security_key" in reason for reason in why.values())
    assert public_key not in json.dumps(captured)


def test_a_policy_s_lock_key_is_captured_by_the_key_s_name(fake_api, run_module):
    """By name: a rebuilt or cloned cluster gives a key registered again a new id (spec 026, Decision 3)."""
    key = {"id": "7", "name": "legal-hold", "comment": "", "disabled": False, "public_key": "MFkwEwexample"}
    fake = cluster(
        fake_api,
        {
            "/v3/snapshots/policies/status/": {"entries": [{**policy("projects-daily", "/data/projects", 1), "lock_key": "7"}]},
            "/v2/portal/spokes/": {"entries": []},
            "/v1/file-system/security/keys/": {"entries": [key]},
        },
    )

    captured = capture(run_module, "snapshots")

    (entry,) = captured["host_vars"]["qumulo_snapshot_policies"]
    assert entry["lock_key"] == "legal-hold"
    assert "the lock key of the snapshot policy projects-daily" not in reasons(captured)
    # snapshot_policy, run back from the capture, reads the policy and its key by id, and the key by name.
    fake.serve(
        "/v3/snapshots/policies/1", {k: v for k, v in policy("projects-daily", "/data/projects", 1).items() if k != "source_file_path"} | {"lock_key_ref": "7"}
    )
    fake.serve("/v1/file-system/security/keys/7", key)
    fake.serve("/v1/file-system/security/keys/legal-hold", key)
    unchanged(run_module, snapshot_policy, **entry)


def test_a_policy_s_key_with_an_empty_name_is_left_out_with_a_note(fake_api, run_module):
    """QUIRK-120: a lock_key of "" would take the key off on a replay."""
    cluster(
        fake_api,
        {
            "/v3/snapshots/policies/status/": {"entries": [{**policy("projects-daily", "/data/projects", 1), "lock_key": "6"}]},
            "/v2/portal/spokes/": {"entries": []},
            "/v1/file-system/security/keys/": {"entries": [{"id": "6", "name": "", "comment": "", "disabled": False, "public_key": "MFkwEwexample"}]},
        },
    )

    captured = capture(run_module, "snapshots")

    (entry,) = captured["host_vars"]["qumulo_snapshot_policies"]
    assert "lock_key" not in entry
    assert "id 6" in reasons(captured)["the lock key of the snapshot policy projects-daily"]


def test_a_policy_s_lock_key_is_left_out_when_the_key_store_may_not_be_read(fake_api, run_module):
    fake = cluster(
        fake_api,
        {
            "/v3/snapshots/policies/status/": {"entries": [{**policy("projects-daily", "/data/projects", 1), "lock_key": "7"}]},
            "/v2/portal/spokes/": {"entries": []},
        },
    )
    fake.serve("/v1/file-system/security/keys/", {"error_class": "access_denied_error", "description": "no privilege"}, status=403)

    captured = capture(run_module, "snapshots")

    (entry,) = captured["host_vars"]["qumulo_snapshot_policies"]
    assert "lock_key" not in entry
    assert "id 7" in reasons(captured)["the lock key of the snapshot policy projects-daily"]


def test_a_key_store_the_user_may_not_read_is_left_out_and_the_policies_are_captured(fake_api, run_module):
    fake = cluster(fake_api, {"/v3/snapshots/policies/status/": {"entries": []}})
    fake.serve("/v1/file-system/security/keys/", {"error_class": "http_forbidden_error", "description": "no privilege"}, status=403)

    captured = capture(run_module, "snapshots")

    assert captured["host_vars"]["qumulo_snapshot_policies"] == []
    assert "the security keys" in reasons(captured)


def test_quotas_and_snapshot_policies_outside_the_spoke_roots(fake_api, run_module):
    policies = [
        policy("projects-daily", "/data/projects", 1),
        policy("edge-daily", "/edge-data", 2),
        policy("projects-hourly", "/data/projects", 3, schedule=HOURLY, ttl=""),
    ]
    fake = cluster(
        fake_api,
        {
            "/v1/files/quotas/status/?limit=1000": DATA["/v1/files/quotas/status/?limit=1000"],
            "/v3/snapshots/policies/status/": {"entries": policies},
            "/v1/file-system/security/keys/": {"entries": []},
            "/v2/portal/spokes/": {"entries": [spoke_record()]},
            **ROOT_100,
            "/v1/files/quotas/11": {"id": "11", "limit": str(10**12)},
            attributes_path("/data/projects"): attributes("/data/projects", file_id="11"),
        },
    )
    for entry in policies:
        if entry["policy_name"] != "edge-daily":  # captured, so its module reads it
            fake.serve(f"/v3/snapshots/policies/{entry['id']}", {key: value for key, value in entry.items() if key != "source_file_path"})

    captured = capture(run_module, "quotas", "snapshots")

    hv = captured["host_vars"]
    assert hv["qumulo_quotas"] == [{"path": "/data/projects", "limit": 10**12}]
    assert hv["qumulo_snapshot_policies"] == [
        {
            "policy_name": "projects-daily",
            "path": "/data/projects",
            "enabled": True,
            "expiration_time_to_live": "7days",
            "snapshot_name_template": "{ID}_{Policy}_{Directory}",
            "schedule": DAILY,
        },
        {
            "policy_name": "projects-hourly",
            "path": "/data/projects",
            "enabled": True,
            # A policy whose snapshots never expire reads back an empty time to live.
            "expiration_time_to_live": "",
            "snapshot_name_template": "{ID}_{Policy}_{Directory}",
            "schedule": HOURLY,
        },
    ]
    why = reasons(captured)
    assert set(why) == {"the quota on /edge-data/q", "the snapshot policy edge-daily"}
    assert all("spoke root /edge-data" in reason for reason in why.values())
    unchanged(run_module, directory_quota, **hv["qumulo_quotas"][0])
    for entry in hv["qumulo_snapshot_policies"]:
        unchanged(run_module, snapshot_policy, **entry)


SOURCE_SETTINGS = {"replication_enabled": True, "map_local_ids_to_nfs_ids": False, "source_root_read_only": False, "target_port": 3712}


def test_replication_relationships_this_cluster_is_the_source_of(fake_api, run_module):
    windows = [
        {"start_hour": 22, "start_minute": 0, "end_hour": 23, "end_minute": 30, "on_days": ["EVERY_DAY"]},
        {"start_hour": 8, "start_minute": 15, "end_hour": 18, "end_minute": 45, "on_days": ["SAT", "MON"]},
    ]
    address = {"target_address": "203.0.113.40"}
    sources = [
        replication_record(
            CLUSTER_ID,
            "/projects",
            PEER_UUID.upper(),
            "/replicas/edge-projects",
            record_id="r1",
            blackout_windows=windows,
            blackout_window_timezone="UTC",
            **address,
            **SOURCE_SETTINGS,
        ),
        replication_record(
            CLUSTER_ID,
            "/archive",
            PEER_UUID,
            "/replicas/edge-archive",
            record_id="r2",
            blackout_windows=[],
            blackout_window_timezone="",
            **address,
            **SOURCE_SETTINGS,
        ),
        replication_record(
            CLUSTER_ID,
            "/old",
            PEER_UUID,
            "/replicas/edge-old",
            state="ENDED",
            record_id="r3",
            blackout_windows=[],
            blackout_window_timezone="",
            **address,
            **SOURCE_SETTINGS,
        ),
        replication_record(
            CLUSTER_ID, "/elsewhere", HUB_UUID, "/replicas/x", record_id="r4", blackout_windows=[], blackout_window_timezone="", **address, **SOURCE_SETTINGS
        ),
        replication_record(
            CLUSTER_ID,
            "/untimed",
            PEER_UUID,
            "/replicas/untimed",
            record_id="r5",
            blackout_windows=windows[:1],
            blackout_window_timezone="",
            **address,
            **SOURCE_SETTINGS,
        ),
    ]
    cluster(fake_api, {"/v2/replication/source-relationships/status/": sources, "/v2/replication/target-relationships/status/": []})

    captured = capture(run_module, "replication", inventory_clusters={PEER_UUID: "dr01"})

    common = {"target": "dr01", "replication_enabled": True, "map_local_ids_to_nfs_ids": False, "source_root_read_only": False}
    assert captured["host_vars"]["qumulo_replication_relationships"] == [
        {"source_path": "/archive", "target_path": "/replicas/edge-archive", **common, "blackout_windows": []},
        {
            "source_path": "/projects",
            "target_path": "/replicas/edge-projects",
            **common,
            # Each window's days in the server's order, EVERY_DAY written out.
            "blackout_windows": [{**windows[0], "on_days": EVERY_DAY}, {**windows[1], "on_days": ["MON", "SAT"]}],
            "blackout_window_timezone": "UTC",
        },
    ]
    why = reasons(captured)
    assert why[f"the replication of /old to {PEER_UUID}:/replicas/edge-old"] == "it has ENDED"
    assert "no inventory host" in why[f"the replication of /elsewhere to {HUB_UUID}:/replicas/x"]
    # Windows without a timezone read back "" (QUIRK-019), and the replication roles refuse them.
    assert "no timezone" in why[f"the replication of /untimed to {PEER_UUID}:/replicas/untimed"]
    # Each live record gives the target's address once, for the target's host.
    targets = {(record["uuid"], str(record["value"])) for record in captured["peer_values"]}
    assert targets == {(PEER_UUID, str({"address": "203.0.113.40", "port": 3712})), (HUB_UUID, str({"address": "203.0.113.40", "port": 3712}))}
    for relationship in captured["host_vars"]["qumulo_replication_relationships"]:
        options = {key: value for key, value in relationship.items() if key != "target"}
        unchanged(run_module, replication_relationship, **options, target_cluster_uuid=PEER_UUID, target_address="203.0.113.40")


def test_a_target_records_which_relationships_its_sources_declare(fake_api, run_module):
    targets = [replication_record(PEER_UUID.upper(), "/projects", CLUSTER_ID, "/replicas/peer-projects", record_id="r1")]
    cluster(
        fake_api,
        {
            "/v2/replication/source-relationships/status/": [],
            "/v2/replication/target-relationships/status/": targets,
            "/v2/replication/target-relationships/r1/status": targets[0],
        },
    )

    captured = capture(run_module, "replication", inventory_clusters={PEER_UUID: "src01"})

    # The source is in the inventory, under a UUID the server gives in upper case, so nothing is left out.
    assert captured["left_out"] == []
    assert captured["from_peers"] == ["qumulo_replication_address"]
    assert "qumulo_replication_relationships" not in captured["host_vars"]
    # The source host's entry and the record's id are what the replication roles pass on the target.
    unchanged(
        run_module, replication_target, source_cluster_uuid=PEER_UUID, source_path="/projects", target_path="/replicas/peer-projects", relationship_id="r1"
    )


FACTORY_STATE = {
    "/v1/cluster/settings": {"cluster_name": "edge01"},
    **FS_SETTINGS,
    "/v1/web-ui/settings": {"inactivity_timeout": None, "login_banner": None},
    "/v3/network": {"frontend_networks": [], "managed_interfaces": None},
    "/v1/dns/configs/system": {"dns_servers": ["10.0.0.3", "10.0.0.2"], "dns_search_domains": ["b.example.com", "a.example.com"]},
    "/v1/dns/lookup-override-config": {"lookup_overrides": []},
    "/v1/authoritative-dns/settings": {"enabled": False, "fqdn": "", "host_restrictions": [], "record_ttl_seconds": 0},
    "/v2/time/settings": {"source": "API_TIME_SOURCE_NTP", "use_ad_for_primary": False, "ntp_servers": ["0.qumulo.pool.ntp.org"]},
    "/v2/cluster/settings/ssl/ciphers": CIPHERS,
    "/v2/audit/destinations/": audit_state(AUDIT_ZETA, {**AUDIT_ZETA, "name": "alpha"})["/v2/audit/destinations/"],
    "/v1/support/settings": {**SUPPORT, "enabled": False},
    "/v1/nexus/connection": {**NEXUS, "nexus_enabled": False},
    "/v1/nexus/registration": {"nexus_enabled": False, "registration_status": "NOT_REGISTERED"},
    "/v1/capacity/clamp": {"capacity_clamp": ""},
    "/v1/portal/spokes/eviction-settings": {"free_threshold": 0.05},
    "/v1/license/status": {"state": "LICENSE_STATE_NONE", "license": None},
    METRICS: edge_metrics("unprotected_edge"),
    "/v1/groups/": GROUPS,
    "/v1/groups/513/members/": [ADMIN_USER],
    "/v1/groups/514/members/": [GUEST_USER],
    "/v1/groups/1000/members/": [BACKUP_USER],
    "/v1/users/": [BACKUP_USER, GUEST_USER, ADMIN_USER],
    "/v1/ad/status": {**AD_STATUS, "status": "NOT_IN_DOMAIN", "domain": ""},
    "/v1/ad/monitor": ad_monitor(""),
    "/v1/auth/roles/": {"Observers": {}, "Administrators": {}},
    "/v1/auth/roles/Administrators/members?limit=1000": {"members": {"500": ADMIN}, "paging": {"next": None}},
    "/v1/auth/roles/Observers/members?limit=1000": {"members": {"513": USERS, "501": GUEST}, "paging": {"next": None}},
    "/v1/saml/settings": SAML_FACTORY,
    KERBEROS_SETTINGS: {"use_alt_security_identities_mapping": False},
    KERBEROS_KEYTAB: None,
    "/v1/auth/user-defined-mappings/": [],
    "/v2/portal/spokes/": {"entries": []},
    "/v2/portal/hubs/": {"entries": []},
    "/v2/replication/source-relationships/status/": [],
    "/v2/replication/target-relationships/status/": [],
    "/v3/smb/shares/?populate-trustee-names=true": {"entries": [share("zeta", "/b/", "4"), share("Files", "/", "2"), share("alpha", "/a/", "3")]},
    "/v3/nfs/exports/": {"entries": [export("/z", "/b", "3"), export("/", "/", "1"), export("/a", "/a", "2")]},
    "/v1/files/quotas/status/?limit=1000": {"quotas": [quota("/b", 10**9, "12"), quota("/a", 10**9, "11")], "paging": {"next": ""}},
    "/v3/snapshots/policies/status/": {"entries": [policy("zeta", "/b", 2), policy("alpha", "/a", 1)]},
    "/v1/file-system/security/keys/": {"entries": []},
    "/v1/smb/settings": {"session_encryption": "NONE"},
    "/v2/nfs/settings": {"v4_enabled": False},
    TENANTS_PATH: {"entries": [listed_tenant(1, "Default")]},
    SMB_OVERRIDES: [],
    NFS_OVERRIDES: [],
    PROTECTION: ERASURE_CODED,
    "/v1/s3/settings": {"enabled": False, "base_path": "/", "multipart_upload_expiry_interval": "1days", "secure": True},
    "/v1/s3/buckets/": {"buckets": [s3_bucket_record("zeta", "/b"), s3_bucket_record("alpha", "/a")]},
    "/v1/s3/buckets/zeta/policy": {},
    "/v1/s3/buckets/alpha/policy": {},
    "/v1/s3/access-keys/?limit=1000": {"entries": [], "paging": {"next": None}},
    "/v0/ftp/settings": {
        "enabled": False,
        "check_remote_host": True,
        "log_operations": True,
        "chroot_users": False,
        "allow_unencrypted_connections": True,
        "expand_wildcards": False,
        "anonymous_user": None,
        "greeting": "Welcome.\n",
    },
    attributes_path("/"): attributes("/", mode="0777"),
    attributes_path("/a"): attributes("/a", file_id="11"),
    attributes_path("/b"): attributes("/b", file_id="12"),
    **acl_of("/", "0777"),
    **acl_of("/a", "0755"),
    **acl_of("/b", "0755"),
}
# The lists capture sorts, each with the keys it sorts by.
SORTED_BY = {
    "qumulo_audit_destinations": ("name",),
    "qumulo_local_groups": ("name",),
    "qumulo_local_users": ("name",),
    "qumulo_role_members": ("role",),
    "qumulo_directories": ("path",),
    "qumulo_quotas": ("path",),
    "qumulo_snapshot_policies": ("policy_name",),
    "qumulo_s3_buckets": ("name",),
}


def reversed_lists(value):
    """value with every list of records, at any depth, in reverse order, as a server may list them another time."""
    if isinstance(value, Query):
        return Query(value.body, reversed_lists(value.answer))
    if isinstance(value, dict):
        return {key: reversed_lists(item) for key, item in value.items()}
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return [reversed_lists(item) for item in reversed(value)]
    return value


def test_every_area_of_a_new_cluster_is_captured_sorted_whatever_order_the_server_lists(fake_api, run_module, monkeypatch):
    monkeypatch.setattr(tls, "served_leaf", lambda host, port, timeout: b"leaf")
    cluster(fake_api, FACTORY_STATE)
    first = capture(run_module)
    cluster(fake_api, reversed_lists(FACTORY_STATE))
    second = run_module(capture_info, CONNECTION, check_mode=True)["capture"]

    assert first == second
    hv = first["host_vars"]
    assert list(hv) == sorted(hv)
    assert list(hv) == [
        "qumulo_audit_destinations",
        "qumulo_authoritative_dns",
        "qumulo_capacity",
        "qumulo_cluster_name",
        "qumulo_cluster_uuid",
        "qumulo_directories",
        "qumulo_dns",
        "qumulo_factory_defaults",
        "qumulo_filesystem_settings",
        "qumulo_ftp_settings",
        "qumulo_kerberos",
        "qumulo_local_groups",
        "qumulo_local_users",
        "qumulo_monitoring",
        "qumulo_network",
        "qumulo_nfs_exports",
        "qumulo_nfs_settings",
        "qumulo_quotas",
        "qumulo_role_members",
        "qumulo_s3_access_keys",
        "qumulo_s3_bucket_policies",
        "qumulo_s3_buckets",
        "qumulo_s3_settings",
        "qumulo_smb_settings",
        "qumulo_smb_shares",
        "qumulo_snapshot_policies",
        "qumulo_ssl_ciphers",
        "qumulo_tenants",
        "qumulo_time",
        "qumulo_web_ui_settings",
    ]
    # On one tenant every share and export sits under it, the factory ones included, and none is shared.
    (default,) = hv["qumulo_tenants"]
    assert [entry["share_name"] for entry in default["smb_shares"]] == ["Files", "alpha", "zeta"]
    assert [entry["export_path"] for entry in default["nfs_exports"]] == ["/", "/a", "/z"]
    assert hv["qumulo_smb_shares"] == hv["qumulo_nfs_exports"] == []
    for name, keys in SORTED_BY.items():
        assert len(hv[name]) > 1, name
        assert hv[name] == sorted(hv[name], key=lambda entry, keys=keys: tuple(entry[key] for key in keys)), name
    # The order of servers and search domains is the setting itself, kept as the cluster holds it.
    assert hv["qumulo_dns"] == {"servers": ["10.0.0.3", "10.0.0.2"], "search_domains": ["b.example.com", "a.example.com"], "lookup_overrides": []}
    assert hv["qumulo_factory_defaults"] == {"nfs_root_export": "present", "smb_files_share": "present"}
    assert variables(first) == [
        "qumulo_local_users[admin].password",
        "qumulo_local_users[svc-backup].password",
        "qumulo_ssl.certificate",
        "qumulo_ssl.private_key",
    ]


def test_an_area_terraform_owns_is_not_read(fake_api, run_module):
    cluster(fake_api, {"/v1/cluster/settings": {"cluster_name": "edge01"}})

    captured = capture(run_module, "cluster", "dns", leave_out=["dns"])

    assert captured["host_vars"] == {"qumulo_cluster_uuid": CLUSTER_ID, "qumulo_cluster_name": "edge01"}
    assert captured["left_out"] == [{"what": "the dns area", "why": "leave_out names it, as an area Terraform owns"}]


def test_leave_out_naming_no_area_fails_before_any_request(fake_api, run_module):
    fake_api()

    result = run_module(capture_info, {**CONNECTION, "leave_out": ["dns", "floating_ips"]})

    assert result["failed"] is True
    assert "floating_ips" in result["msg"] and "network.floating_ips" in result["msg"]


def test_an_area_the_cluster_does_not_offer_is_left_out_with_the_reason(fake_api, run_module):
    fake = cluster(fake_api, {"/v1/cluster/settings": {"cluster_name": "edge01"}})
    fake.serve("/v3/network", NOT_FOUND, status=404)

    result = run_module(capture_info, {**CONNECTION, "gather_subset": ["network", "cluster"]})

    assert result["capture"]["host_vars"] == {"qumulo_cluster_uuid": CLUSTER_ID, "qumulo_cluster_name": "edge01"}
    assert list(result["unavailable_subsets"]) == ["network"]
    assert "does not offer it" in reasons(result["capture"])["the network area"]


def test_a_node_in_no_cluster_is_refused(fake_api, run_module):
    fake_api(cluster_id="", state="UNCONFIGURED")

    result = run_module(capture_info, {**CONNECTION, "expected_cluster_uuid": ""})

    assert result["failed"] is True
    assert "belongs to no cluster yet" in result["msg"]


# The tenants area (spec 014, User Story 4, FR-022). A share, an export or a network sits under the tenant that holds it
# when the area is captured; the step-2 round trip runs the role's own reads against the same cluster and then
# tenancy_plan from the capture, which must plan no change.


def wire_host(network_id, name, tenant_id):
    """A HOST network, as the tier 1 container's network 0, in the wire shape of /v3/network."""
    return {
        "id": network_id,
        "name": name,
        "tenant_id": tenant_id,
        "addresses": {"type": "HOST", "host_addresses": {"floating_ip_ranges": [], "netmask": None}},
    }


def override(tenant_id, settings):
    """An entry of /v1/multitenancy/smb/settings/ or /nfs/settings/."""
    return {"key": tenant_id, "value": settings}


def tenancy_cluster(fake_api, tenants, document=None, shares=(), exports=(), smb_overrides=(), nfs_overrides=(), protection=None):
    """A cluster with tenants, serving what the network, smb, nfs and tenants areas read and what the writers' run-backs
    and the role's step-2 reads read; optional, since each test reads its own part."""
    state = {
        TENANTS_PATH: {"entries": list(tenants)},
        PROTECTION: protection or ERASURE_CODED,
        "/v3/network": document or wire_document(bond=()),
        "/v1/smb/settings": SMB_SETTINGS,
        "/v3/smb/shares/?populate-trustee-names=true": {"entries": list(shares)},
        "/v3/smb/shares/": {"entries": list(shares)},
        SMB_OVERRIDES: list(smb_overrides),
        "/v2/nfs/settings": NFS_SETTINGS,
        "/v3/nfs/exports/": {"entries": list(exports)},
        NFS_OVERRIDES: list(nfs_overrides),
    }
    fake = cluster(fake_api, state, optional=True)
    # Each tenant's override as smb_settings and nfs_settings read it, by its own route.
    for protocol, overrides in (("smb", smb_overrides), ("nfs", nfs_overrides)):
        held = {entry["key"]: entry["value"] for entry in overrides}
        for listed in tenants:
            route = f"/v1/multitenancy/{protocol}/settings/{listed['id']}"
            if listed["id"] in held:
                fake.serve(route, held[listed["id"]], optional=True)
            else:
                missing = {"error_class": f"api_{protocol}_tenant_settings_do_not_exist_error", "description": "no override"}
                fake.serve(route, missing, status=404, optional=True)
    for entry in shares:
        fake.serve(f"/v3/smb/shares/{entry['id']}", entry, optional=True)
    for entry in exports:
        fake.serve(f"/v3/nfs/exports/{quote(entry['id'], safe='')}", entry, optional=True)
    return fake


def capture_tenancy(run_module, *areas, **args):
    return capture(run_module, *(areas or ("network", "smb", "nfs", "tenants")), **args)


def step_2(run_module, host_vars, **options):
    """tenancy_plan from captured host_vars, with the cluster option the role's step 2 reads, as tasks/tenants.yml builds it."""
    tenants = run_module(tenant_info, CONNECTION)
    network = run_module(network_info, {**CONNECTION, "gather_subset": ["document"]})
    smb = run_module(smb_info, {**CONNECTION, "gather_subset": ["shares"]})
    nfs = run_module(nfs_info, {**CONNECTION, "gather_subset": ["exports", "settings", "tenant_settings"]})
    params = {
        "tenants": host_vars.get("qumulo_tenants", []),
        "smb_shares": host_vars.get("qumulo_smb_shares", []),
        "nfs_exports": host_vars.get("qumulo_nfs_exports", []),
        "factory_defaults": host_vars.get("qumulo_factory_defaults", {}),
        "api_host": API_HOST,
        "cluster": {
            "tenants": tenants["tenants"],
            "network": network["network"]["document"],
            "protection_type": None,
            "smb_shares": smb["smb"]["shares"],
            "nfs_exports": nfs["nfs"]["exports"],
            "nfs_overrides": nfs["nfs"]["tenant_settings"],
            "nfs_settings": nfs["nfs"]["settings"],
        },
        **options,
    }
    for variable, option in (("qumulo_network", "network"), ("qumulo_nfs_settings", "nfs_settings")):
        if variable in host_vars:
            params[option] = host_vars[variable]
    return run_module(tenancy_plan, params)


def refusal_messages(result):
    return [refusal["message"] for refusal in result.get("refusals", [])]


def changes_nothing(run_module, host_vars, **options):
    """Step 2 from the capture refuses nothing and plans no tenant or network write, and each share, export and override
    step 18 or 19 would pass its module, run in check mode as the role runs it, changes nothing."""
    result = step_2(run_module, host_vars, **options)
    assert not result.get("failed"), result.get("msg")
    assert result.get("network_changes") is False, result["network"]
    for step in ("step_3a", "step_3b", "step_20a"):
        assert result[step] == [], (step, result[step])
    if result["network"]:
        unchanged(run_module, network_config, **result["network"])
    for module, settings, area, items in ((smb_share, smb_settings, "smb", "shares"), (nfs_export, nfs_settings, "nfs", "exports")):
        for item in result[area]["removals"] + result[area][items]:
            unchanged(run_module, module, **item)
        for item in result[area]["overrides"]:
            unchanged(run_module, settings, **item)
    for entry in host_vars.get("qumulo_tenants", []):
        unchanged(run_module, tenant, **{key: entry[key] for key in ("name", "previous_name", "state", *SWITCHES) if key in entry})
    return result


FINANCE_SMB = {**SMB_SETTINGS, "signing_required": False, "session_encryption": "REQUIRED"}
TWO_SHARES = [
    share("Files", "/", "1"),
    share("home", "/home/", "2"),
    share("home", "/home/", "3", tenant_id=2),
    share("ledger", "/finance/", "4", tenant_id=2),
]
TWO_EXPORTS = [export("/", "/", "1"), export("/reports", "/finance/reports", "2") | {"tenant_id": 2}]


def two_tenants(fake_api):
    return tenancy_cluster(fake_api, [CORP, FINANCE], TWO, TWO_SHARES, TWO_EXPORTS, smb_overrides=[override(2, FINANCE_SMB)])


def test_two_tenants_are_captured_under_their_names_and_a_check_run_from_the_capture_changes_nothing(fake_api, run_module):
    """US4-1, US4-9: tenant 1 renamed, an override on one, shares in each and a network bound to each, two on VLANs."""
    two_tenants(fake_api)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    every_on = dict.fromkeys(SWITCHES, True)
    assert hv["qumulo_tenants"] == [
        {
            "name": "Corp",
            "previous_name": "Default",
            **every_on,
            "networks": [
                {
                    "id": 1,
                    "name": "Corp",
                    "type": "STATIC",
                    "netmask": "255.255.255.0",
                    "default_gateway": "192.0.2.1",
                    "ip_ranges": ["192.0.2.11-192.0.2.14"],
                    "floating_ip_ranges": ["192.0.2.21-192.0.2.24"],
                }
            ],
            "smb_settings": "inherit",
            "nfs_settings": "inherit",
            "smb_shares": [TWO_SHARES_OPTIONS[0]],
            "nfs_exports": [TWO_EXPORTS_OPTIONS[0]],
        },
        {
            "name": "finance",
            **every_on,
            "ssh_enabled": False,
            "networks": [
                {
                    "id": 2,
                    "name": "finance",
                    "type": "STATIC",
                    "netmask": "255.255.255.0",
                    "ip_ranges": ["198.51.100.11-198.51.100.14"],
                    "vlan_id": 20,
                    "mtu": 1500,
                },
                {"id": 3, "name": "finance-b", "type": "STATIC", "netmask": "255.255.255.0", "ip_ranges": ["203.0.113.11-203.0.113.14"], "vlan_id": 30},
            ],
            "smb_settings": FINANCE_SMB,
            "nfs_settings": "inherit",
            "smb_shares": [TWO_SHARES_OPTIONS[3]],
            "nfs_exports": [TWO_EXPORTS_OPTIONS[1]],
        },
    ]
    # The VLANs and the bond's networks are the tenants' to carry, so the flat document keeps neither.
    assert hv["qumulo_network"] == {"frontend_networks": [], "managed_interfaces": {"frontend_bond_config": {**BOND, "networks": []}, "frontend_vlans": []}}
    assert hv["qumulo_smb_shares"] == [{"share_name": "home", "tenants": "all", **without(TWO_SHARES_OPTIONS[1], "share_name")}]
    assert hv["qumulo_nfs_exports"] == []
    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "present", "nfs_root_export": "present"}
    assert "tenant_id" not in json.dumps(hv)
    assert captured["left_out"] == []
    changes_nothing(run_module, hv)


def without(item, *keys):
    return {key: value for key, value in item.items() if key not in keys}


def options_of(entry):
    """A share or export as capture writes it under its tenant: the module's option shape, without tenant_id."""
    if "share_name" in entry:
        return without(smb_share_options(entry), "tenant_id")
    return without(nfs_export_options(entry), "tenant_id")


TWO_SHARES_OPTIONS = [options_of(entry) for entry in TWO_SHARES]
TWO_EXPORTS_OPTIONS = [options_of(entry) for entry in TWO_EXPORTS]


def test_a_freshly_formed_cluster_s_one_tenant_holds_its_networks_shares_and_exports(fake_api, run_module):
    """US4-2: the factory share and export under Default, qumulo_factory_defaults present, no tenant id anywhere."""
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1])], wire_document(NET1 | {"tenant_id": 1}), TWO_SHARES[:2], TWO_EXPORTS[:1])

    hv = capture_tenancy(run_module)["host_vars"]

    (default,) = hv["qumulo_tenants"]
    assert default["name"] == "Default" and "previous_name" not in default
    assert [network["id"] for network in default["networks"]] == [1]
    assert default["smb_shares"] == TWO_SHARES_OPTIONS[:2]
    assert default["nfs_exports"] == TWO_EXPORTS_OPTIONS[:1]
    assert hv["qumulo_smb_shares"] == hv["qumulo_nfs_exports"] == []
    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "present", "nfs_root_export": "present"}
    assert "tenant_id" not in json.dumps(hv)
    changes_nothing(run_module, hv)


def test_a_fresh_container_s_one_tenant_has_networks_empty(fake_api, run_module):
    """US4-2's tier 1 clause: a container with no frontend network."""
    tenancy_cluster(fake_api, [listed_tenant(1, "Default")], {"frontend_networks": [], "managed_interfaces": None}, TWO_SHARES[:1], TWO_EXPORTS[:1])

    hv = capture_tenancy(run_module)["host_vars"]

    assert hv["qumulo_tenants"][0]["networks"] == []
    assert hv["qumulo_network"] == {"frontend_networks": []}
    changes_nothing(run_module, hv)


def test_a_host_network_sits_under_its_tenant_on_networking_the_host_manages(fake_api, run_module):
    """The tier 1 lane after its tenancy setup: HOST network 0 in tenant 1, no managed interfaces."""
    document = {"frontend_networks": [wire_host(0, "Default", 1)], "managed_interfaces": None}
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[0])], document, TWO_SHARES[:1], TWO_EXPORTS[:1])

    hv = capture_tenancy(run_module)["host_vars"]

    assert hv["qumulo_tenants"][0]["networks"] == [{"id": 0, "name": "Default", "type": "HOST"}]
    assert hv["qumulo_network"] == {"frontend_networks": []}
    changes_nothing(run_module, hv, management_network=[0])


def test_a_second_tenant_added_to_a_single_tenant_capture_keeps_the_shares_where_they_are(fake_api, run_module):
    """US4-3: the captured shares already sit under Default, so a new entry is all a second tenant takes."""
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1])], wire_document(NET1 | {"tenant_id": 1}), TWO_SHARES[:2], TWO_EXPORTS[:1])
    hv = capture_tenancy(run_module)["host_vars"]

    result = step_2(run_module, {**hv, "qumulo_tenants": [*hv["qumulo_tenants"], {"name": "finance", "networks": []}]})

    assert result.get("refusals", []) == []
    assert [write["name"] for write in result["step_3a"]] == ["finance"]


def test_each_override_is_inherit_or_its_full_record_even_when_it_equals_the_global_settings(fake_api, run_module):
    """US4-5."""
    tenants = [listed_tenant(1, "Default"), listed_tenant(2, "finance"), listed_tenant(3, "hr")]
    nfs = {**NFS_SETTINGS, "v4_enabled": False}
    tenancy_cluster(fake_api, tenants, smb_overrides=[override(2, FINANCE_SMB), override(3, SMB_SETTINGS)], nfs_overrides=[override(3, nfs)])

    hv = capture_tenancy(run_module, "smb", "nfs", "tenants")["host_vars"]

    assert [(entry["name"], entry["smb_settings"], entry["nfs_settings"]) for entry in hv["qumulo_tenants"]] == [
        ("Default", "inherit", "inherit"),
        ("finance", FINANCE_SMB, "inherit"),
        ("hr", SMB_SETTINGS, {**nfs, "idmap_domain": ""}),
    ]
    changes_nothing(run_module, hv)


def test_a_network_no_tenant_holds_on_several_tenants_is_flat_and_noted(fake_api, run_module):
    """US4-6."""
    unbound = wire_static(4, "spare", None, ["192.0.2.31-34"])
    tenancy_cluster(fake_api, [CORP, FINANCE], wire_document(NET1, NET2, NET3, unbound, bond=(1, 4), vlans=TWO["managed_interfaces"]["frontend_vlans"]))

    captured = capture_tenancy(run_module, "network", "tenants")

    network = captured["host_vars"]["qumulo_network"]
    assert [item["id"] for item in network["frontend_networks"]] == [4]
    assert "tenant" not in network["frontend_networks"][0]
    assert network["managed_interfaces"]["frontend_bond_config"]["networks"] == [4]
    assert "a replay onto a rebuilt cluster with several tenants refuses it" in reasons(captured)["the tenant of network 4 (spare)"]
    changes_nothing(run_module, captured["host_vars"])


def test_a_backend_network_of_the_one_tenant_is_flat_and_not_noted(fake_api, run_module):
    """US4-7."""
    backend = wire_static(5, "backend", 1, ["10.99.0.11-14"])
    document = wire_document(NET1, backend, backend={"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [5]})
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1, 5])], document)

    captured = capture_tenancy(run_module, "network", "tenants")

    hv = captured["host_vars"]
    assert [item["id"] for item in hv["qumulo_network"]["frontend_networks"]] == [5]
    assert [item["id"] for item in hv["qumulo_tenants"][0]["networks"]] == [1]
    assert captured["left_out"] == []
    changes_nothing(run_module, hv)


def test_a_backend_network_a_tenant_holds_on_several_tenants_is_flat_and_noted(fake_api, run_module):
    backend = wire_static(5, "backend", 1, ["10.99.0.11-14"])
    bond1 = {"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [5]}
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1, 5]), listed_tenant(2, "finance")], wire_document(NET1, backend, backend=bond1))

    captured = capture_tenancy(run_module, "network", "tenants")

    assert [item["id"] for item in captured["host_vars"]["qumulo_network"]["frontend_networks"]] == [5]
    assert "G-TENANT-BACKEND" in reasons(captured)["the tenant of network 5 (backend)"]
    changes_nothing(run_module, captured["host_vars"])


def share_in(tenant_id, name, fs_path, share_id, **overrides):
    return share(name, fs_path, share_id, tenant_id=tenant_id, **overrides)


def test_identical_shares_merge_into_shared_entries_and_the_rest_stay_under_their_tenant(fake_api, run_module):
    """US4-8: home in every tenant, forms in finance and hr, a different forms in legal; names match without case."""
    tenants = [listed_tenant(1, "Default"), listed_tenant(2, "finance"), listed_tenant(3, "hr"), listed_tenant(4, "legal")]
    shares = [
        share_in(1, "Files", "/", "1"),
        share_in(1, "home", "/home/", "2"),
        share_in(2, "home", "/home/", "3"),
        share_in(3, "HOME", "/home/", "4"),
        share_in(4, "home", "/home/", "5"),
        share_in(3, "forms", "/forms/", "6"),
        share_in(2, "Forms", "/forms/", "7"),
        share_in(4, "forms", "/legal/forms/", "8"),
        # The factory share of tenant 1 never merges, even with an identical copy elsewhere.
        share_in(2, "Files", "/", "9"),
    ]
    tenancy_cluster(fake_api, tenants, shares=shares)

    hv = capture_tenancy(run_module, "smb", "tenants")["host_vars"]

    body = without(options_of(shares[1]), "share_name")
    assert hv["qumulo_smb_shares"] == [
        {"share_name": "Forms", "tenants": ["finance", "hr"], **without(options_of(shares[6]), "share_name")},
        {"share_name": "home", "tenants": "all", **body},
    ]
    nested = {entry["name"]: [item["share_name"] for item in entry.get("smb_shares", [])] for entry in hv["qumulo_tenants"]}
    assert nested == {"Default": ["Files"], "finance": ["Files"], "hr": [], "legal": ["forms"]}
    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "present"}
    changes_nothing(run_module, hv)


def test_identical_exports_merge_and_the_factory_export_stays_under_tenant_1(fake_api, run_module):
    tenants = [listed_tenant(1, "Default"), listed_tenant(2, "finance"), listed_tenant(3, "hr")]
    exports = [export("/", "/", "1"), export("/", "/", "2") | {"tenant_id": 2}, export("/data", "/data", "3"), export("/data", "/data", "4") | {"tenant_id": 3}]
    tenancy_cluster(fake_api, tenants, exports=exports)

    hv = capture_tenancy(run_module, "nfs", "tenants")["host_vars"]

    assert hv["qumulo_nfs_exports"] == [{"export_path": "/data", "tenants": ["Default", "hr"], **without(options_of(exports[2]), "export_path")}]
    nested = {entry["name"]: [item["export_path"] for item in entry.get("nfs_exports", [])] for entry in hv["qumulo_tenants"]}
    assert nested == {"Default": ["/"], "finance": ["/"], "hr": []}
    changes_nothing(run_module, hv)


def test_the_factory_share_in_another_tenant_alone_is_not_the_factory_share(fake_api, run_module):
    tenancy_cluster(fake_api, [listed_tenant(1, "Default"), listed_tenant(2, "finance")], shares=[share_in(2, "Files", "/", "1")])

    hv = capture_tenancy(run_module, "smb", "tenants")["host_vars"]

    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "absent"}


FACTORY_WARNINGS = [
    "qumulo_factory_defaults.smb_files_share: absent does nothing, since the cluster has no tenant 1",
    "qumulo_factory_defaults.nfs_root_export: absent does nothing, since the cluster has no tenant 1",
]


def test_a_missing_tenant_1_is_written_as_default_absent_with_the_factory_share_and_export_absent(fake_api, run_module):
    """US4-9's second clause, as issue #287 rules it: a rebuild from the capture drops the Default a newly formed edge has."""
    tenancy_cluster(fake_api, [WITHOUT_TENANT_1_ENG], WITHOUT_TENANT_1)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert {variable: hv[variable] for variable in WITHOUT_TENANT_1_CAPTURE} == WITHOUT_TENANT_1_CAPTURE
    note = reasons(captured)["tenant 1"]
    assert "written as Default with state: absent and the factory Files share and / export absent, so that" in note
    assert note.endswith("drops all three")
    # On the captured cluster the absent entry and the factory claims do nothing: the cluster lacks Default.
    result = changes_nothing(run_module, hv)
    for warning in FACTORY_WARNINGS:
        assert any(warning in found for found in result["warnings"]), (warning, result["warnings"])


@pytest.mark.parametrize(
    ("areas", "factory", "keys"),
    [
        pytest.param(("network", "nfs", "tenants"), {"nfs_root_export": "absent"}, "smb_files_share", id="smb-not-gathered"),
        pytest.param(("network", "tenants"), {}, "smb_files_share and nfs_root_export", id="smb-and-nfs-not-gathered"),
    ],
)
def test_a_missing_tenant_1_without_smb_gathered_says_the_replay_refuses_until_the_share_is_claimed(fake_api, run_module, areas, factory, keys):
    tenancy_cluster(fake_api, [WITHOUT_TENANT_1_ENG], WITHOUT_TENANT_1)

    captured = capture_tenancy(run_module, *areas)

    hv = captured["host_vars"]
    assert hv["qumulo_tenants"][0] == {"name": "Default", "state": "absent"}
    assert hv.get("qumulo_factory_defaults", {}) == factory
    note = reasons(captured)["tenant 1"]
    assert f"the replay refuses it until qumulo_factory_defaults declares {keys} absent" in note
    changes_nothing(run_module, hv)


def test_a_missing_tenant_1_beside_a_network_no_tenant_holds_is_written_absent_so_the_preflight_refuses(fake_api, run_module):
    """A rebuilt cluster would hold Default beside eng, and step 2 there refuses the new network without a tenant, after the
    old edge is gone; with Default absent the preflight refuses before, naming the remedy."""
    tenancy_cluster(fake_api, [WITHOUT_TENANT_1_ENG], WITH_SPARE)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert {variable: hv[variable] for variable in WITH_SPARE_CAPTURE} == WITH_SPARE_CAPTURE
    assert "a replay onto a rebuilt cluster with several tenants refuses it" in reasons(captured)["the tenant of network 2 (spare)"]
    note = reasons(captured)["tenant 1"]
    assert "the rebuild preflight refuses the inventory until network 2 (spare) is listed under a tenant that stays" in note
    changes_nothing(run_module, hv)


def test_a_network_no_tenant_holds_beside_two_tenants_is_written_flat_so_the_preflight_refuses(fake_api, run_module):
    """Issue #305: the rebuild preflight refuses the capture before the destroy, as the rebuilt cluster's step 2 would after."""
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1]), listed_tenant(2, "eng")], TWO_WITH_SPARE)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert {variable: hv[variable] for variable in TWO_WITH_SPARE_CAPTURE} == TWO_WITH_SPARE_CAPTURE
    assert "a replay onto a rebuilt cluster with several tenants refuses it" in reasons(captured)["the tenant of network 2 (spare)"]
    changes_nothing(run_module, hv)


def test_a_missing_tenant_1_without_the_network_area_gathered_says_the_replay_refuses_until_the_networks_are_listed(fake_api, run_module):
    """The remedy is one the inventory can give, as for smb or nfs not gathered, so the entry is written."""
    tenancy_cluster(fake_api, [WITHOUT_TENANT_1_ENG], WITHOUT_TENANT_1)

    captured = capture_tenancy(run_module, "smb", "nfs", "tenants")

    hv = captured["host_vars"]
    assert hv["qumulo_tenants"][0] == {"name": "Default", "state": "absent"}
    note = reasons(captured)["tenant 1"]
    assert "the replay refuses it until the network area is captured or each network is listed under a tenant that stays" in note
    changes_nothing(run_module, hv)


BACKEND_AND_SPARE = wire_document(
    ENG_NET,
    SPARE_NET,
    wire_static(5, "backend", 2, ["10.99.0.11-14"]),
    bond=(1, 2),
    backend={"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [5]},
)


@pytest.mark.parametrize(
    ("document", "leave_out", "why"),
    [
        pytest.param(BACKEND_AND_SPARE, [], "network 5 (backend), on the backend bond, stays in qumulo_network.frontend_networks", id="backend"),
        pytest.param(WITH_SPARE, ["smb"], "leave_out names smb, as Terraform owns it", id="smb-left-out"),
        pytest.param(WITH_SPARE, ["nfs"], "leave_out names nfs, as Terraform owns it", id="nfs-left-out"),
    ],
)
def test_a_missing_tenant_1_beside_a_network_no_tenant_holds_is_written_absent_whatever_else_keeps_default(fake_api, run_module, document, leave_out, why):
    """Without the entry, the rebuilt cluster's step 2 would refuse the unbound network after the old edge is gone; with it,
    the preflight refuses first, so the entry is written beside a cause that would otherwise skip it."""
    tenancy_cluster(fake_api, [WITHOUT_TENANT_1_ENG], document)

    captured = capture_tenancy(run_module, leave_out=leave_out)

    hv = captured["host_vars"]
    assert hv["qumulo_tenants"][0] == {"name": "Default", "state": "absent"}
    note = reasons(captured)["tenant 1"]
    assert "the rebuild preflight refuses the inventory until network 2 (spare) is listed under a tenant that stays" in note
    assert f"it is written although {why}" in note
    changes_nothing(run_module, hv)


@pytest.mark.parametrize(
    ("document", "areas", "leave_out", "why"),
    [
        pytest.param(
            wire_document(
                ENG_NET,
                wire_static(5, "backend", 2, ["10.99.0.11-14"]),
                backend={"interface_name": "bond1", "bonding_mode": "IEEE_8023AD", "mtu": 9000, "networks": [5]},
            ),
            (),
            [],
            "network 5 (backend), on the backend bond, stays in qumulo_network.frontend_networks",
            id="backend-network",
        ),
        pytest.param(WITHOUT_TENANT_1, (), ["network"], "leave_out names network, as Terraform owns it", id="network-left-out"),
        pytest.param(WITHOUT_TENANT_1, (), ["smb"], "leave_out names smb, as Terraform owns it", id="smb-left-out"),
        pytest.param(WITHOUT_TENANT_1, (), ["nfs"], "leave_out names nfs, as Terraform owns it", id="nfs-left-out"),
    ],
)
def test_a_missing_tenant_1_is_only_noted_where_a_replay_could_not_delete_default(fake_api, run_module, document, areas, leave_out, why):
    """Issue #287, built as on 2026-11-08: Default absent would make the replay refuse, where its drift can be waived."""
    tenancy_cluster(fake_api, [WITHOUT_TENANT_1_ENG], document)

    captured = capture_tenancy(run_module, *areas, leave_out=leave_out)

    hv = captured["host_vars"]
    assert all(entry["name"] != "Default" for entry in hv["qumulo_tenants"])
    note = reasons(captured)["tenant 1"]
    assert why in note
    assert "which the rebuild preflight reports as drift" in note
    changes_nothing(run_module, hv)


def test_a_missing_tenant_1_is_only_noted_when_another_tenant_has_the_name_default(fake_api, run_module):
    """Default absent would delete that tenant, so capture writes no entry for tenant 1."""
    tenancy_cluster(fake_api, [NAMED_DEFAULT, NAMED_ENG], NAMED_DEFAULT_DOCUMENT)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert {variable: hv[variable] for variable in NAMED_DEFAULT_CAPTURE} == NAMED_DEFAULT_CAPTURE
    note = reasons(captured)["tenant 1"]
    assert "another tenant is named Default" in note
    assert "drops the factory Files share and / export, which qumulo_factory_defaults declares absent" in note
    changes_nothing(run_module, hv)


def test_a_missing_tenant_1_beside_a_default_says_which_factory_items_the_rebuild_keeps(fake_api, run_module):
    tenancy_cluster(fake_api, [NAMED_DEFAULT, NAMED_ENG], NAMED_DEFAULT_DOCUMENT)

    note = reasons(capture_tenancy(run_module, "network", "nfs", "tenants"))["tenant 1"]

    assert "drops the factory / export, which qumulo_factory_defaults declares absent" in note
    assert "keeps the factory Files share, which the capture does not read" in note


# On networking the host manages, capture renames Default to the one tenant that holds a network, where it can, so a
# rebuild keeps the HOST network, which the play reaches REST on, in tenant 1 throughout; with Default absent the replay
# would move it from Default to that tenant at the baseline's step 3.
HOSTED_OPS_NET = {**HOSTED_ENG_NET, "id": 1, "name": "ops", "tenant_id": 3}
HOSTED_SPARE_NET = {**HOSTED_ENG_NET, "id": 1, "name": "spare", "tenant_id": None}


def hosted(*networks):
    return {"frontend_networks": list(networks), "managed_interfaces": None}


def replays(host_vars):
    """The rebuild preflight's model of a replay of host_vars onto a newly formed edge refuses nothing."""
    built = replay(
        host_vars["qumulo_tenants"],
        host_vars["qumulo_smb_shares"],
        host_vars["qumulo_nfs_exports"],
        host_vars["qumulo_factory_defaults"],
        host_vars["qumulo_network"],
    )
    return [refusal.message for refusal in built.findings.refusals]


def test_a_missing_tenant_1_on_networking_the_host_manages_is_written_as_the_rename_of_default(fake_api, run_module):
    tenancy_cluster(fake_api, [HOSTED_ENG], HOSTED_WITHOUT_TENANT_1)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert {variable: hv[variable] for variable in HOSTED_WITHOUT_TENANT_1_CAPTURE} == HOSTED_WITHOUT_TENANT_1_CAPTURE
    note = reasons(captured)["tenant 1"]
    assert "so eng, the one tenant that holds a network, is written with previous_name: Default" in note
    assert note.endswith(
        "renames the Default it forms with to eng and drops the factory Files share and / export, which qumulo_factory_defaults declares absent"
    )
    assert replays(hv) == []
    # On the captured cluster the rename is one already done, since it holds eng and no Default.
    result = changes_nothing(run_module, hv, management_network=[0])
    for warning in FACTORY_WARNINGS:
        assert any(warning in found for found in result["warnings"]), (warning, result["warnings"])


@pytest.mark.parametrize(
    ("tenants", "document", "shares", "where"),
    [
        pytest.param([HOSTED_ENG], HOSTED_WITHOUT_TENANT_1, [share_in(2, "files", "/", "1")], "nested", id="nested"),
        pytest.param(
            [HOSTED_ENG, listed_tenant(3, "ops")],
            HOSTED_WITHOUT_TENANT_1,
            [share_in(2, "Files", "/", "1"), share_in(3, "Files", "/", "2")],
            "shared",
            id="shared",
        ),
    ],
)
def test_the_renamed_tenant_s_own_files_share_and_root_export_keep_the_factory_keys_present(fake_api, run_module, tenants, document, shares, where):
    """The Default the rebuilt edge forms with becomes eng, factory share and export included, which eng's own entries
    then declare; absent would claim each of them twice."""
    exports = [export("/", "/", "3") | {"tenant_id": 2}]
    tenancy_cluster(fake_api, tenants, document, shares, exports)

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "present", "nfs_root_export": "present"}
    eng = next(entry for entry in hv["qumulo_tenants"] if entry["name"] == "eng")
    assert eng["previous_name"] == "Default"
    assert [item["export_path"] for item in eng["nfs_exports"]] == ["/"]
    held = [item["share_name"] for item in eng.get("smb_shares", [])] if where == "nested" else [item["share_name"] for item in hv["qumulo_smb_shares"]]
    assert [name.casefold() for name in held] == ["files"]
    assert "and keeps the factory Files share and / export as eng's own, which qumulo_factory_defaults declares present" in reasons(captured)["tenant 1"]
    assert replays(hv) == []
    changes_nothing(run_module, hv, management_network=[0])


def test_a_heir_whose_root_export_capture_drops_for_g_mapto_stays_default_absent(fake_api, run_module):
    """Renamed, the rebuilt tenant 1 would keep the factory / export in place of the dropped one, which the preflight never
    reports for tenant 1; Default absent drops the factory export with Default, and the old edge's export is drift."""
    deleted = {**RESTRICTION, "user_mapping": "NFS_MAP_ALL", "map_to_user": {"id_type": "LOCAL_USER", "id_value": ""}}
    tenancy_cluster(fake_api, [HOSTED_ENG], HOSTED_WITHOUT_TENANT_1, exports=[export("/", "/", "3", restrictions=[deleted]) | {"tenant_id": 2}])

    captured = capture_tenancy(run_module)

    hv = captured["host_vars"]
    assert hv["qumulo_tenants"][0] == {"name": "Default", "state": "absent"}
    assert all("previous_name" not in entry for entry in hv["qumulo_tenants"])
    assert hv["qumulo_factory_defaults"] == {"smb_files_share": "absent", "nfs_root_export": "absent"}
    assert "G-MAPTO" in reasons(captured)["the NFS export / of tenant eng"]
    assert "eng's / export, which the capture leaves out, is not written as the rename of Default to eng" in reasons(captured)["tenant 1"]
    changes_nothing(run_module, hv, management_network=[0])


def test_a_capture_where_two_tenants_hold_networks_says_a_rebuild_needs_the_management_network(fake_api, run_module):
    tenancy_cluster(fake_api, [HOSTED_ENG, listed_tenant(3, "ops", networks=[1])], hosted(HOSTED_ENG_NET, HOSTED_OPS_NET))

    note = reasons(capture_tenancy(run_module))["tenant 1"]

    assert "2 tenants hold networks, so the replay's tenant 1 pass writes them all into tenant 1" in note
    assert "needs qumulo_management_network" in note


@pytest.mark.parametrize(
    ("tenants", "document", "areas"),
    [
        pytest.param([HOSTED_ENG, listed_tenant(3, "ops", networks=[1])], hosted(HOSTED_ENG_NET, HOSTED_OPS_NET), (), id="two-tenants-hold-networks"),
        pytest.param([listed_tenant(2, "eng")], hosted(), (), id="no-tenant-holds-a-network"),
        pytest.param([HOSTED_ENG], hosted(HOSTED_ENG_NET, HOSTED_SPARE_NET), (), id="a-network-no-tenant-holds"),
        pytest.param([HOSTED_ENG], HOSTED_WITHOUT_TENANT_1, ("network", "nfs", "tenants"), id="smb-not-gathered"),
        pytest.param([HOSTED_ENG], HOSTED_WITHOUT_TENANT_1, ("smb", "nfs", "tenants"), id="network-not-gathered"),
    ],
)
def test_a_missing_tenant_1_on_networking_the_host_manages_stays_default_absent_without_one_tenant_to_rename(fake_api, run_module, tenants, document, areas):
    """Without exactly one tenant to rename, or with a network or a factory key the capture leaves to the operator, the
    entry stays Default absent."""
    tenancy_cluster(fake_api, tenants, document)

    captured = capture_tenancy(run_module, *areas)

    hv = captured["host_vars"]
    assert hv["qumulo_tenants"][0] == {"name": "Default", "state": "absent"}
    assert all("previous_name" not in entry for entry in hv["qumulo_tenants"])
    changes_nothing(run_module, hv, management_network=[0])


def clone_target_areas():
    """The clone's read of its target, playbooks/clone_edge.yml's gather_subset, which leaves out the network area."""
    plays = yaml.safe_load((Path(__file__).resolve().parents[4] / "playbooks" / "clone_edge.yml").read_text(encoding="utf-8"))
    tasks = [task for play in plays for task in play.get("tasks", [])]
    (read,) = [task for task in tasks if task.get("name", "").startswith("Read what the target holds")]
    return read["qumulo.cluster.capture_info"]["gather_subset"]


def test_a_clone_target_without_tenant_1_read_as_clone_edge_reads_it_is_not_fresh(fake_api, run_module):
    """The target's capture notes the missing tenant 1, whatever it writes for it, and the clone reads the note."""
    state = {**FACTORY_STATE, TENANTS_PATH: {"entries": [listed_tenant(2, "eng")]}}
    cluster(fake_api, state, optional=True)
    target = capture(run_module, *clone_target_areas())
    assert "tenant 1" in reasons(target)

    source = {"host_vars": {"qumulo_cluster_uuid": PEER_UUID, "qumulo_cluster_name": "edge01"}}
    result = run_module(clone_host_vars, {"capture": source, "cluster_uuid": CLUSTER_ID, "cluster_name": "edge02", "target": target})

    assert result["failed"] is True
    assert "the tenant eng" in result["msg"], result["msg"]


def test_a_tenant_on_its_own_identity_configuration_is_written_in_full_with_only_its_identity_noted(fake_api, run_module):
    """US4-10."""
    tenancy_cluster(fake_api, [listed_tenant(1, "Default"), listed_tenant(2, "finance", identity_config_id=2)])

    captured = capture_tenancy(run_module, "tenants")

    assert captured["host_vars"]["qumulo_tenants"][1] == {"name": "finance", **dict.fromkeys(SWITCHES, True)}
    assert list(reasons(captured)) == ["the identity configuration of tenant finance"]
    assert "per-tenant identity, which the collection does not support yet" in reasons(captured)["the identity configuration of tenant finance"]
    changes_nothing(run_module, captured["host_vars"])


def vlan_of_two(fake_api, unbound):
    """VLAN 20 carrying network 1, Default's, and network 2, finance's or no tenant's."""
    first = wire_static(1, "Default", 1, ["192.0.2.11-14"])
    second = wire_static(2, "second", None if unbound else 2, ["2001:db8::11-2001:db8::14"])
    document = wire_document(first, second, bond=(), vlans=[{"vlan_id": 20, "network_id": 1, "secondary_network_id": 2}])
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1]), listed_tenant(2, "finance", networks=[] if unbound else [2])], document)


def test_a_vlan_that_carries_two_tenants_networks_is_noted_and_warned_of_on_its_own_cluster(fake_api, run_module):
    """US4-11: the baseline warns of a layout the cluster holds, and refuses it only where the cluster lacks it."""
    vlan_of_two(fake_api, unbound=False)

    captured = capture_tenancy(run_module, "network", "tenants")

    why = reasons(captured)["VLAN 20"]
    assert "networks of the tenants Default, finance" in why
    assert "warns of it on this cluster and refuses it on a cluster that lacks it" in why
    result = changes_nothing(run_module, captured["host_vars"])
    assert any("VLAN 20" in warning for warning in result["warnings"]), result["warnings"]


def test_a_vlan_that_carries_a_tenant_s_network_and_an_unbound_one_is_kept_noted_and_refused(fake_api, run_module):
    """US4-11: the VLAN stays in managed_interfaces, so step 2 refuses it rather than take network 2 off it."""
    vlan_of_two(fake_api, unbound=True)

    captured = capture_tenancy(run_module, "network", "tenants")

    hv = captured["host_vars"]
    assert hv["qumulo_network"]["managed_interfaces"]["frontend_vlans"] == [{"vlan_id": 20, "network_id": 1, "secondary_network_id": 2}]
    assert hv["qumulo_tenants"][0]["networks"][0]["vlan_id"] == 20
    why = reasons(captured)["VLAN 20"]
    assert "a network of tenant Default and network 2, which no tenant holds" in why
    assert "a check run from this capture refuses it" in why
    result = step_2(run_module, hv)
    assert result["failed"] is True
    # Removing the VLAN from frontend_vlans would let step 3 take network 2 off it, so the remedy is a tenant or a VLAN.
    assert refusal_messages(result) == [
        (
            "VLAN 20 is in qumulo_network.managed_interfaces.frontend_vlans and is the vlan_id of network 1 of tenant Default; a "
            "VLAN is declared in one place. List network 2 (second) under Default with vlan_id 20 and remove the VLAN from "
            "frontend_vlans, or give each network a VLAN of its own."
        )
    ]


def test_a_vlan_that_carries_a_tenant_s_network_and_an_unbound_one_is_refused_with_its_vlan_taken_out_of_frontend_vlans(fake_api, run_module):
    """Issue #301: without the VLAN in frontend_vlans, step 2 still refuses, rather than take network 2 off VLAN 20."""
    vlan_of_two(fake_api, unbound=True)
    hv = capture_tenancy(run_module, "network", "tenants")["host_vars"]
    hv["qumulo_network"]["managed_interfaces"]["frontend_vlans"] = []

    result = step_2(run_module, hv)

    assert result["failed"] is True
    assert refusal_messages(result) == [
        (
            "VLAN 20 carries network 2 (second), which no qumulo_tenants entry lists, beside network 1 of tenant Default, and the run "
            "derives a tenant network's VLAN from its vlan_id and puts network 2 on no interface, so it would take network 2 "
            "off VLAN 20. List network 2 (second) under Default with vlan_id 20, or give each network a VLAN of its own."
        )
    ]


def test_a_vlan_that_carries_the_one_tenant_s_network_and_an_unbound_one_is_kept_noted_and_refused(fake_api, run_module):
    """With the tenants area, the one-tenant cluster nests its tenant's network, so its VLAN is shared too."""
    first = wire_static(1, "Default", 1, ["192.0.2.11-14"])
    second = wire_static(2, "second", None, ["2001:db8::11-14"])
    document = wire_document(first, second, bond=(), vlans=[{"vlan_id": 20, "network_id": 1, "secondary_network_id": 2}])
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1])], document)

    captured = capture_tenancy(run_module, "network", "tenants")

    assert "a check run from this capture refuses it" in reasons(captured)["VLAN 20"]
    assert step_2(run_module, captured["host_vars"])["failed"] is True


def test_a_vlan_that_carries_a_tenant_s_network_and_an_unbound_one_with_tenants_left_out_is_kept_as_it_is(fake_api, run_module):
    vlan_of_two(fake_api, unbound=True)

    captured = capture_tenancy(run_module, "network", "tenants", leave_out=["tenants"])

    assert "a replay onto this cluster keeps it" in reasons(captured)["VLAN 20"]
    changes_nothing(run_module, captured["host_vars"])


def test_a_cloud_cluster_s_tenant_is_written_without_its_switches_noted_once(fake_api, run_module):
    """US4-12 (decision D8): floating ranges out of the tenant's networks when network.floating_ips is left out."""
    tenancy_cluster(fake_api, [listed_tenant(1, "Default", networks=[1])], wire_document(NET1 | {"tenant_id": 1}), TWO_SHARES[:1], protection=OBJECT_BACKED)

    captured = capture_tenancy(run_module, leave_out=["network.floating_ips"])

    (default,) = captured["host_vars"]["qumulo_tenants"]
    assert not set(SWITCHES) & set(default)
    assert "floating_ip_ranges" not in default["networks"][0]
    assert "G-TENANT-CLOUD" in reasons(captured)["the services of every tenant"]
    changes_nothing(run_module, captured["host_vars"], owned_parts=["interfaces", "networks"], cluster_kind="cnq")


def test_a_left_out_smb_or_nfs_drops_every_share_export_and_override_nested_or_shared(fake_api, run_module):
    """US4-13's first case: smb left out, then nfs."""
    two_tenants(fake_api)

    without_smb = capture_tenancy(run_module, leave_out=["smb"])["host_vars"]
    without_nfs = capture_tenancy(run_module, leave_out=["nfs"])["host_vars"]

    assert "qumulo_smb_shares" not in without_smb
    assert all("smb_settings" not in entry and "smb_shares" not in entry for entry in without_smb["qumulo_tenants"])
    assert all("nfs_settings" in entry for entry in without_smb["qumulo_tenants"])
    assert "qumulo_nfs_exports" not in without_nfs
    assert all("nfs_settings" not in entry and "nfs_exports" not in entry for entry in without_nfs["qumulo_tenants"])
    changes_nothing(run_module, without_smb)


def test_a_left_out_network_drops_every_tenant_s_networks(fake_api, run_module):
    two_tenants(fake_api)

    hv = capture_tenancy(run_module, leave_out=["network"])["host_vars"]

    assert "qumulo_network" not in hv
    assert all("networks" not in entry for entry in hv["qumulo_tenants"])
    changes_nothing(run_module, hv)


def test_tenants_left_out_on_one_tenant_write_everything_flat_with_no_tenant(fake_api, run_module):
    """US4-13's second case: VLANs and bond membership as the cluster has them, and the tenant's override noted."""
    vlan_net = wire_static(2, "vlan", 1, ["198.51.100.11-14"])
    document = wire_document(NET1 | {"tenant_id": 1}, vlan_net, vlans=[{"vlan_id": 20, "network_id": 2, "mtu": 1500}])
    tenancy_cluster(
        fake_api, [listed_tenant(1, "Default", networks=[1, 2])], document, TWO_SHARES[:2], TWO_EXPORTS[:1], smb_overrides=[override(1, FINANCE_SMB)]
    )

    captured = capture_tenancy(run_module, leave_out=["tenants"])

    hv = captured["host_vars"]
    assert "qumulo_tenants" not in hv
    assert [item["id"] for item in hv["qumulo_network"]["frontend_networks"]] == [1, 2]
    assert "tenant" not in json.dumps(hv["qumulo_network"])
    assert hv["qumulo_network"]["managed_interfaces"]["frontend_vlans"] == [{"vlan_id": 20, "network_id": 2, "mtu": 1500}]
    assert hv["qumulo_network"]["managed_interfaces"]["frontend_bond_config"]["networks"] == [1]
    assert hv["qumulo_smb_shares"] == TWO_SHARES_OPTIONS[:2]
    assert hv["qumulo_nfs_exports"] == TWO_EXPORTS_OPTIONS[:1]
    notes = reasons(captured)
    assert notes["the tenants area"] == "leave_out names it, as an area Terraform owns"
    assert "leave_out names tenants" in notes["the SMB settings of tenant Default"]
    for entry in hv["qumulo_smb_shares"]:
        unchanged(run_module, smb_share, **entry)
    changes_nothing(run_module, hv)


def test_tenants_left_out_on_several_tenants_name_each_network_s_tenant_and_drop_what_tenants_hold(fake_api, run_module):
    """US4-13's third case (decision D23)."""
    two_tenants(fake_api)

    captured = capture_tenancy(run_module, leave_out=["tenants"])

    hv = captured["host_vars"]
    assert hv["qumulo_network"] == TENANTS_LEFT_OUT
    assert hv["qumulo_smb_shares"] == hv["qumulo_nfs_exports"] == []
    notes = reasons(captured)
    for what in (
        "the SMB shares of tenant Corp",
        "the SMB shares of tenant finance",
        "the NFS exports of tenant finance",
        "the SMB settings of tenant finance",
    ):
        assert "only the tenants area writes on a cluster with several tenants" in notes[what], what
    changes_nothing(run_module, hv)


def test_a_gather_subset_without_tenants_writes_the_flat_forms_and_says_so(fake_api, run_module):
    two_tenants(fake_api)

    captured = capture(run_module, "smb")

    assert captured["host_vars"]["qumulo_smb_shares"] == []
    assert "gather_subset leaves out the tenants area" in reasons(captured)["the SMB shares of tenant finance"]


def test_a_cluster_without_the_tenant_list_captures_the_tenants_area_as_unavailable_and_the_rest_flat(fake_api, run_module):
    fake = cluster(fake_api, {"/v3/network": network_document(["10.20.0.50-51"])})
    fake.serve(TENANTS_PATH, NOT_FOUND, status=404)

    result = run_module(capture_info, {**CONNECTION, "gather_subset": ["network", "tenants"]})

    assert list(result["unavailable_subsets"]) == ["tenants"]
    assert "qumulo_tenants" not in result["capture"]["host_vars"]
    assert "tenant" not in json.dumps(result["capture"]["host_vars"]["qumulo_network"])
