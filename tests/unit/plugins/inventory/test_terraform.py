# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.terraform, the inventory plugin, reading a saved output file or a stub terraform."""

from __future__ import annotations

import ast
import json
import os
import socket
import stat
from pathlib import Path

import pytest
import yaml
from ansible.errors import AnsibleParserError
from ansible.inventory.data import InventoryData
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.loader import inventory_loader

PLUGIN = "qumulo.cluster.terraform"
SOURCE = "inventory.qumulo_terraform.yml"
OUTPUT = "qumulo_ansible_inventory"
EDGE_UUID = "1b2c33f0-5c3a-4c56-9d2e-3f8c1b0a7e11"
CNQ_UUID = "7c1e0d4a-77c1-4b8e-9a51-2f0e6c3d9e02"
LAB_UUID = "0b6d2a41-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
EDGE = {
    "api_host": "10.20.0.5",
    "cluster_uuid": EDGE_UUID,
    "cluster_kind": "edge",
    "managed_by": "none",
    "owned_areas": [],
    "password_ref": {"type": "env", "id": "QUMULO_PASSWORD_EDGE01"},
}
CNQ = {
    "api_host": "10.0.1.20",
    "cluster_uuid": CNQ_UUID,
    "cluster_kind": "cnq",
    "managed_by": "terraform_provider",
    "owned_areas": ["network.floating_ips", "dns"],
    "password_ref": {"type": "aws_ssm_parameter", "id": "/qumulo/cnq01/admin-password"},
}
LAB = {
    "api_host": "lab01.corp.example.com",
    "cluster_uuid": LAB_UUID,
    "cluster_kind": "baremetal",
    "managed_by": "legacy_provisioner",
    "owned_areas": [],
    "password_ref": {"type": "hashicorp_vault", "id": "secret/data/qumulo/lab01"},
}
OUTPUT_VALUE = {"edge01": EDGE, "cnq01": CNQ, "lab01": LAB}
# The addresses the portal and replication roles reach a cluster at: a CNQ cluster's floating IPs, an edge's node IP.
CNQ_ADDRESSED = {**CNQ, "portal_hosts": ["10.0.1.20", "10.0.1.21", "10.0.1.22"], "replication_address": "10.0.1.20"}
EDGE_ADDRESSED = {**EDGE, "portal_hosts": ["10.20.0.5"]}
PASSWORD = "Hunter2-not-for-inventory"


def write_source(directory, config, name=SOURCE):
    path = Path(directory) / name
    path.write_text(yaml.safe_dump({"plugin": PLUGIN, **config}), encoding="utf-8")
    return path


def write_output(directory, value, name="outputs.json"):
    """A saved `terraform output -json qumulo_ansible_inventory`."""
    path = Path(directory) / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def parse(source, cache=True):
    inventory = InventoryData()
    inventory_loader.get(PLUGIN).parse(inventory, DataLoader(), str(source), cache=cache)
    return inventory


def parse_output(tmp_path, value):
    write_output(tmp_path, value)
    return parse(write_source(tmp_path, {"output_file": "outputs.json"}))


def host_vars(inventory, name):
    return {key: value for key, value in inventory.get_host(name).vars.items() if key.startswith("qumulo_")}


def members(inventory, group):
    return sorted(host.name for host in inventory.groups[group].get_hosts())


def refusal(tmp_path, value):
    with pytest.raises(AnsibleParserError) as caught:
        parse_output(tmp_path, value)
    return str(caught.value)


def stub_terraform(directory, stdout="", rc=0, stderr="", sleep=0):
    """An executable that stands in for terraform: it records its arguments, working directory, TF_WORKSPACE and TF_LOG."""
    bin_dir = Path(directory) / "bin"
    bin_dir.mkdir(exist_ok=True)
    record = Path(directory) / "record"
    record.mkdir(exist_ok=True)
    (record / "stdout").write_text(stdout, encoding="utf-8")
    (record / "stderr").write_text(stderr, encoding="utf-8")
    binary = bin_dir / "terraform"
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{record}/args"\n'
        f'pwd > "{record}/cwd"\n'
        f'printf "%s" "${{TF_WORKSPACE-unset}}" > "{record}/workspace"\n'
        f'printf "%s" "${{TF_LOG-unset}}" > "{record}/tf_log"\n' + (f"exec sleep {sleep}\n" if sleep else "") + f'cat "{record}/stdout"\n'
        f'cat "{record}/stderr" >&2\n'
        f"exit {rc}\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary, record


# The inventory source


@pytest.mark.parametrize(
    ("name", "accepted"),
    [
        ("inventory.qumulo_terraform.yml", True),
        ("qumulo_terraform.yaml", True),
        ("prod.qumulo_terraform.yaml", True),
        ("terraform.yml", False),
        ("hosts.yml", False),
        ("inventory.qumulo_terraform.json", False),
    ],
)
def test_verify_file_accepts_only_sources_named_for_the_plugin(tmp_path, name, accepted):
    source = write_source(tmp_path, {"output_file": "outputs.json"}, name=name)

    assert inventory_loader.get(PLUGIN).verify_file(str(source)) is accepted


def test_verify_file_refuses_a_missing_source(tmp_path):
    assert inventory_loader.get(PLUGIN).verify_file(str(tmp_path / SOURCE)) is False


# Hosts, groups and variables


def test_each_entry_becomes_a_host_with_the_collections_variables(tmp_path):
    inventory = parse_output(tmp_path, OUTPUT_VALUE)

    assert sorted(inventory.hosts) == ["cnq01", "edge01", "lab01"]
    assert host_vars(inventory, "edge01") == {
        "qumulo_api_host": "10.20.0.5",
        "qumulo_cluster_uuid": EDGE_UUID,
        "qumulo_cluster_kind": "edge",
        "qumulo_managed_by": "none",
        "qumulo_terraform_owned_areas": [],
        "qumulo_password_ref": {"type": "env", "id": "QUMULO_PASSWORD_EDGE01"},
    }
    assert host_vars(inventory, "cnq01") == {
        "qumulo_api_host": "10.0.1.20",
        "qumulo_cluster_uuid": CNQ_UUID,
        "qumulo_cluster_kind": "cnq",
        "qumulo_managed_by": "terraform_provider",
        "qumulo_terraform_owned_areas": ["network.floating_ips", "dns"],
        "qumulo_password_ref": {"type": "aws_ssm_parameter", "id": "/qumulo/cnq01/admin-password"},
    }


@pytest.mark.parametrize("kind", ["edge", "baremetal"])
def test_an_on_premises_cluster_the_provider_formed_may_give_terraform_no_area(tmp_path, kind):
    """The output is authoritative: off CNQ, an empty owned_areas leaves every area, floating addresses included, to the
    role (maintainer ruling, 2026-11-07)."""
    inventory = parse_output(tmp_path, {"edge01": {**EDGE, "cluster_kind": kind, "managed_by": "terraform_provider", "owned_areas": []}})

    variables = host_vars(inventory, "edge01")
    assert (variables["qumulo_managed_by"], variables["qumulo_terraform_owned_areas"]) == ("terraform_provider", [])


def test_hosts_are_grouped_by_kind_and_by_who_formed_them_under_qumulo_clusters(tmp_path):
    inventory = parse_output(tmp_path, OUTPUT_VALUE)

    assert members(inventory, "qumulo_clusters") == ["cnq01", "edge01", "lab01"]
    assert members(inventory, "qumulo_edges") == ["edge01"]
    assert members(inventory, "qumulo_cnq") == ["cnq01"]
    assert members(inventory, "qumulo_baremetal") == ["lab01"]
    assert members(inventory, "qumulo_managed_by_none") == ["edge01"]
    assert members(inventory, "qumulo_managed_by_terraform_provider") == ["cnq01"]
    assert members(inventory, "qumulo_managed_by_legacy_provisioner") == ["lab01"]
    children = sorted(group.name for group in inventory.groups["qumulo_clusters"].child_groups)
    assert children == [
        "qumulo_baremetal",
        "qumulo_cnq",
        "qumulo_edges",
        "qumulo_managed_by_legacy_provisioner",
        "qumulo_managed_by_none",
        "qumulo_managed_by_terraform_provider",
    ]


def test_an_empty_output_adds_no_hosts(tmp_path):
    inventory = parse_output(tmp_path, {})

    assert inventory.hosts == {}


def test_portal_hosts_and_replication_address_become_the_roles_variables(tmp_path):
    inventory = parse_output(tmp_path, {"cnq01": CNQ_ADDRESSED, "edge01": EDGE_ADDRESSED})

    cnq = host_vars(inventory, "cnq01")
    assert cnq["qumulo_portal_hosts"] == [{"address": "10.0.1.20"}, {"address": "10.0.1.21"}, {"address": "10.0.1.22"}]
    assert cnq["qumulo_replication_address"] == "10.0.1.20"
    edge = host_vars(inventory, "edge01")
    assert edge["qumulo_portal_hosts"] == [{"address": "10.20.0.5"}]
    assert "qumulo_replication_address" not in edge


def test_an_entry_without_addresses_sets_neither_variable(tmp_path):
    inventory = parse_output(tmp_path, {"edge01": EDGE, "cnq01": {**CNQ, "portal_hosts": None, "replication_address": None}})

    for name in ("edge01", "cnq01"):
        assert "qumulo_portal_hosts" not in host_vars(inventory, name)
        assert "qumulo_replication_address" not in host_vars(inventory, name)


def test_addresses_may_be_ipv6(tmp_path):
    inventory = parse_output(tmp_path, {"edge01": {**EDGE, "portal_hosts": ["fd00::5", "10.20.0.6"], "replication_address": "fd00::5"}})

    assert host_vars(inventory, "edge01")["qumulo_portal_hosts"] == [{"address": "fd00::5"}, {"address": "10.20.0.6"}]
    assert host_vars(inventory, "edge01")["qumulo_replication_address"] == "fd00::5"


def test_a_null_password_ref_sets_no_password_ref(tmp_path):
    inventory = parse_output(tmp_path, {"edge01": {**EDGE, "password_ref": None}})

    assert "qumulo_password_ref" not in host_vars(inventory, "edge01")


def test_compose_groups_and_keyed_groups_apply_to_the_hosts(tmp_path):
    write_output(tmp_path, OUTPUT_VALUE)
    source = write_source(
        tmp_path,
        {
            "output_file": "outputs.json",
            "compose": {"qumulo_ca_path": "'/etc/qumulo/' ~ qumulo_cluster_kind ~ '-ca.pem'"},
            "groups": {"qumulo_hubs": "qumulo_cluster_kind != 'edge'"},
            "keyed_groups": [{"key": "qumulo_cluster_kind", "prefix": "kind"}],
        },
    )

    inventory = parse(source)

    assert inventory.get_host("cnq01").vars["qumulo_ca_path"] == "/etc/qumulo/cnq-ca.pem"
    assert inventory.get_host("edge01").vars["qumulo_ca_path"] == "/etc/qumulo/edge-ca.pem"
    assert members(inventory, "qumulo_hubs") == ["cnq01", "lab01"]
    assert members(inventory, "kind_edge") == ["edge01"]


# Sources: a saved file, or terraform itself


def test_an_output_file_holding_every_output_is_refused_without_showing_them(tmp_path):
    document = {
        OUTPUT: {"sensitive": False, "type": ["map", ["object", {}]], "value": {"edge01": EDGE}},
        "admin_password": {"sensitive": True, "type": "string", "value": PASSWORD},
    }

    message = refusal(tmp_path, document)

    assert "outputs.json holds every output of the Terraform root" in message
    assert f"save only `terraform output -json {OUTPUT}`" in message
    assert PASSWORD not in message


def test_output_file_is_read_relative_to_the_inventory_sources_directory(tmp_path, monkeypatch):
    (tmp_path / "inventory").mkdir()
    write_output(tmp_path / "inventory", {"edge01": EDGE}, name="saved.json")
    source = write_source(tmp_path / "inventory", {"output_file": "saved.json"})
    monkeypatch.chdir(tmp_path)

    inventory = parse(source)

    assert sorted(inventory.hosts) == ["edge01"]


def test_an_output_file_that_is_not_json_fails_naming_it(tmp_path):
    (tmp_path / "outputs.json").write_text("not json", encoding="utf-8")

    with pytest.raises(AnsibleParserError, match="outputs.json is not JSON"):
        parse(write_source(tmp_path, {"output_file": "outputs.json"}))


def test_a_missing_output_file_fails_naming_it(tmp_path):
    with pytest.raises(AnsibleParserError, match="cannot read the Terraform output file .*outputs.json"):
        parse(write_source(tmp_path, {"output_file": "outputs.json"}))


def test_terraform_output_json_runs_in_the_project_path(tmp_path, monkeypatch):
    binary, record = stub_terraform(tmp_path, stdout=json.dumps({"edge01": EDGE}))
    monkeypatch.delenv("TF_WORKSPACE", raising=False)
    (tmp_path / "root").mkdir()
    source = write_source(tmp_path, {"project_path": "root", "binary_path": str(binary)})

    inventory = parse(source)

    assert sorted(inventory.hosts) == ["edge01"]
    assert (record / "args").read_text(encoding="utf-8").splitlines() == [f"-chdir={tmp_path / 'root'}", "output", "-no-color", "-json", OUTPUT]
    assert (record / "workspace").read_text(encoding="utf-8") == "unset"


def test_workspace_is_selected_through_tf_workspace(tmp_path, monkeypatch):
    binary, record = stub_terraform(tmp_path, stdout=json.dumps({"edge01": EDGE}))
    monkeypatch.delenv("TF_WORKSPACE", raising=False)
    source = write_source(tmp_path, {"project_path": str(tmp_path), "binary_path": str(binary), "workspace": "staging"})

    parse(source)

    assert (record / "workspace").read_text(encoding="utf-8") == "staging"


def test_terraform_is_found_on_path_by_default(tmp_path, monkeypatch):
    binary, record = stub_terraform(tmp_path, stdout=json.dumps({"edge01": EDGE}))
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ.get('PATH', '')}")

    inventory = parse(write_source(tmp_path, {"project_path": str(tmp_path)}))

    assert sorted(inventory.hosts) == ["edge01"]
    assert (record / "args").exists()


def test_a_relative_binary_path_is_relative_to_the_inventory_sources_directory(tmp_path, monkeypatch):
    (tmp_path / "inventory").mkdir()
    stub_terraform(tmp_path / "inventory", stdout=json.dumps({"edge01": EDGE}))
    source = write_source(tmp_path / "inventory", {"project_path": ".", "binary_path": "bin/terraform"})
    monkeypatch.chdir(tmp_path)

    inventory = parse(source)

    assert sorted(inventory.hosts) == ["edge01"]


def test_a_terraform_that_does_not_finish_in_timeout_fails_the_source(tmp_path):
    binary = stub_terraform(tmp_path, stdout=json.dumps({"edge01": EDGE}), sleep=30)[0]
    source = write_source(tmp_path, {"project_path": str(tmp_path), "binary_path": str(binary), "timeout": 1})

    with pytest.raises(AnsibleParserError, match="did not finish in 1s"):
        parse(source)


def test_terraform_runs_without_the_callers_trace_log(tmp_path, monkeypatch):
    binary, record = stub_terraform(tmp_path, stdout=json.dumps({"edge01": EDGE}))
    monkeypatch.setenv("TF_LOG", "TRACE")

    parse(write_source(tmp_path, {"project_path": str(tmp_path), "binary_path": str(binary)}))

    assert (record / "tf_log").read_text(encoding="utf-8") == "unset"


def test_a_failed_terraform_output_fails_with_its_exit_code_and_stderr(tmp_path):
    binary = stub_terraform(tmp_path, rc=1, stderr='Error: Output "qumulo_ansible_inventory" not found')[0]
    source = write_source(tmp_path, {"project_path": str(tmp_path), "binary_path": str(binary)})

    with pytest.raises(AnsibleParserError) as caught:
        parse(source)

    assert "exit code 1" in str(caught.value)
    assert 'Output "qumulo_ansible_inventory" not found' in str(caught.value)


def test_a_missing_terraform_fails_naming_binary_path(tmp_path):
    source = write_source(tmp_path, {"project_path": str(tmp_path), "binary_path": str(tmp_path / "no-terraform")})

    with pytest.raises(AnsibleParserError, match="cannot run .*no-terraform.*binary_path"):
        parse(source)


def test_a_project_path_that_is_not_a_directory_fails_naming_it(tmp_path):
    binary = stub_terraform(tmp_path)[0]
    source = write_source(tmp_path, {"project_path": "missing-root", "binary_path": str(binary)})

    with pytest.raises(AnsibleParserError, match="project_path .*missing-root is not a directory"):
        parse(source)


@pytest.mark.parametrize(
    ("config", "problem"),
    [
        ({}, "set one of project_path and output_file"),
        ({"project_path": ".", "output_file": "outputs.json"}, "set one of project_path and output_file, not both"),
        ({"output_file": "outputs.json", "workspace": "staging"}, "workspace applies only to project_path"),
    ],
    ids=["neither", "both", "workspace with a file"],
)
def test_the_source_options_must_name_exactly_one_source(tmp_path, config, problem):
    write_output(tmp_path, OUTPUT_VALUE)

    with pytest.raises(AnsibleParserError, match=problem):
        parse(write_source(tmp_path, config))


# The output's schema


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ({k: v for k, v in EDGE.items() if k != "cluster_uuid"}, "entry 'edge01': cluster_uuid is missing"),
        ({**EDGE, "region": "us-west-2"}, "entry 'edge01': unknown field 'region'"),
        ({**EDGE, "api_host": ""}, "entry 'edge01': api_host must be a host name or address"),
        ({**EDGE, "api_host": "https://10.20.0.5:8000"}, "entry 'edge01': api_host must be a host name or address, not a URL"),
        ({**EDGE, "api_host": "admin@10.20.0.5"}, "entry 'edge01': api_host must be a host name or address without a user name"),
        ({**EDGE, "api_host": "10.20.0.5:8000"}, "entry 'edge01': api_host must be a host name or address, without a scheme, user, port"),
        ({**EDGE, "api_host": "edge01.corp.example.com/api"}, "entry 'edge01': api_host must be a host name or address, without"),
        ({**EDGE, "api_host": "edge01:8000"}, "entry 'edge01': api_host must be a host name or address, without a scheme, user, port"),
        ({**EDGE, "api_host": "10.20.0.256"}, "entry 'edge01': api_host must be a host name or address, without"),
        ({**EDGE, "api_host": "[10.20.0.5]"}, "entry 'edge01': api_host must be an address without brackets"),
        ({**EDGE, "api_host": "[fd00::5]"}, "entry 'edge01': api_host must be an address without brackets"),
        ({**EDGE, "api_host": "fe80::1%eth0"}, "entry 'edge01': api_host must be an address without a zone id"),
        ({**EDGE, "api_host": 5}, "entry 'edge01': api_host must be a host name or address"),
        ({**EDGE, "cluster_uuid": "edge01"}, "entry 'edge01': cluster_uuid must be a UUID"),
        ({**EDGE, "cluster_uuid": None}, "entry 'edge01': cluster_uuid must be a UUID"),
        ({**EDGE, "cluster_kind": "hub"}, "entry 'edge01': cluster_kind must be one of edge, cnq, baremetal"),
        ({**EDGE, "cluster_kind": ["edge"]}, "entry 'edge01': cluster_kind must be one of edge, cnq, baremetal"),
        ({**EDGE, "managed_by": "terraform"}, "entry 'edge01': managed_by must be one of none, terraform_provider, legacy_provisioner"),
        ({**EDGE, "owned_areas": "dns"}, "entry 'edge01': owned_areas must be a list of area names"),
        ({**EDGE, "owned_areas": ["DNS servers"]}, "entry 'edge01': owned_areas must be a list of area names"),
        (
            {**CNQ, "owned_areas": ["dns"]},
            "entry 'edge01': owned_areas must list network.floating_ips for a cnq cluster with managed_by terraform_provider",
        ),
        ({**EDGE, "password_ref": "QUMULO_PASSWORD_EDGE01"}, "entry 'edge01': password_ref must be a map of type and id"),
        ({**EDGE, "password_ref": {"type": "file", "id": "x"}}, "entry 'edge01': password_ref.type must be one of env, aws_ssm_parameter"),
        ({**EDGE, "password_ref": {"type": "env"}}, "entry 'edge01': password_ref.id must be a non-empty string"),
        ({**EDGE, "password_ref": {"type": "env", "id": "  "}}, "entry 'edge01': password_ref.id must be a non-empty string"),
        ({**EDGE, "password_ref": {"type": "env", "id": "X", "path": "y"}}, "entry 'edge01': unknown field 'password_ref.path'"),
        ({**EDGE, "portal_hosts": ["edge01.corp.example.com"]}, "entry 'edge01': portal_hosts must hold IP addresses such as 10.0.1.20 or fd00::5"),
        ({**EDGE, "portal_hosts": ["https://10.20.0.5:3713"]}, "entry 'edge01': portal_hosts must hold IP addresses, not URLs"),
        ({**EDGE, "portal_hosts": ["10.20.0.5:3713"]}, "entry 'edge01': portal_hosts must hold IP addresses such as 10.0.1.20 or fd00::5"),
        ({**EDGE, "portal_hosts": ["[fd00::5]"]}, "entry 'edge01': portal_hosts must hold IP addresses without brackets"),
        ({**EDGE, "portal_hosts": ["fe80::1%eth0"]}, "entry 'edge01': portal_hosts must hold IP addresses without a zone id"),
        ({**EDGE, "portal_hosts": [{"address": "10.20.0.5"}]}, "entry 'edge01': portal_hosts must hold IP addresses"),
        ({**EDGE, "portal_hosts": "10.20.0.5"}, "entry 'edge01': portal_hosts must be a list of IP addresses"),
        ({**EDGE, "portal_hosts": []}, "entry 'edge01': portal_hosts must list at least one address, or be null"),
        ({**EDGE, "portal_hosts": ["10.20.0.5", "10.20.0.5"]}, "entry 'edge01': portal_hosts lists an address twice"),
        ({**EDGE, "portal_hosts": ["fd00::5", "FD00:0::5"]}, "entry 'edge01': portal_hosts lists an address twice"),
        ({**EDGE, "portal_hosts": ["::ffff:10.20.0.5", "10.20.0.5"]}, "entry 'edge01': portal_hosts lists an address twice"),
        ({**EDGE, "portal_hosts": ["0.0.0.0"]}, "entry 'edge01': portal_hosts must hold addresses the other side reaches, not unspecified"),
        ({**EDGE, "portal_hosts": ["127.0.0.1"]}, "entry 'edge01': portal_hosts must hold addresses the other side reaches, not unspecified"),
        ({**EDGE, "portal_hosts": ["224.0.0.1"]}, "entry 'edge01': portal_hosts must hold addresses the other side reaches, not unspecified"),
        ({**EDGE, "replication_address": "::"}, "entry 'edge01': replication_address must be an address the other side reaches, not unspecified"),
        ({**EDGE, "replication_address": "dr01.corp.example.com"}, "entry 'edge01': replication_address must be an IP address such as 10.0.1.20"),
        ({**EDGE, "replication_address": "https://10.20.0.5"}, "entry 'edge01': replication_address must be an IP address, not a URL"),
        ({**EDGE, "replication_address": "10.20.0.5:3712"}, "entry 'edge01': replication_address must be an IP address such as 10.0.1.20"),
        ({**EDGE, "replication_address": "[fd00::5]"}, "entry 'edge01': replication_address must be an IP address without brackets"),
        ({**EDGE, "replication_address": "fe80::1%eth0"}, "entry 'edge01': replication_address must be an IP address without a zone id"),
        ({**EDGE, "replication_address": ["10.20.0.5"]}, "entry 'edge01': replication_address must be an IP address"),
        ("10.20.0.5", "entry 'edge01' must be a map of api_host, cluster_uuid"),
    ],
    ids=[
        "missing field",
        "unknown field",
        "empty api_host",
        "URL api_host",
        "user@ api_host",
        "api_host with a port",
        "name with a port",
        "dotted digits, no address",
        "api_host with a path",
        "bracketed IPv4 api_host",
        "bracketed IPv6 api_host",
        "IPv6 with a zone id",
        "number api_host",
        "malformed UUID",
        "null UUID",
        "unknown kind",
        "kind a list",
        "unknown managed_by",
        "owned_areas not a list",
        "owned area not a name",
        "provider without floating IPs",
        "password_ref a string",
        "password_ref unknown type",
        "password_ref without id",
        "password_ref blank id",
        "password_ref unknown field",
        "portal host a name",
        "portal host a URL",
        "portal host with a port",
        "bracketed portal host",
        "portal host with a zone id",
        "portal host a map",
        "portal_hosts a string",
        "portal_hosts empty",
        "portal host twice",
        "portal host twice, written two ways",
        "portal host twice, once IPv4-mapped",
        "portal host unspecified",
        "portal host loopback",
        "portal host multicast",
        "replication_address unspecified",
        "replication_address a name",
        "replication_address a URL",
        "replication_address with a port",
        "bracketed replication_address",
        "replication_address with a zone id",
        "replication_address a list",
        "entry not a map",
    ],
)
def test_an_invalid_entry_fails_naming_the_entry_and_the_problem(tmp_path, entry, problem):
    message = refusal(tmp_path, {"edge01": entry})

    assert problem in message
    assert message.startswith(f"{OUTPUT}: ")


def test_an_unknown_field_is_named_with_every_field_the_output_may_give(tmp_path):
    message = refusal(tmp_path, {"edge01": {**EDGE, "region": "us-west-2"}})

    assert (
        "the fields are api_host, cluster_uuid, cluster_kind, managed_by, owned_areas, password_ref, portal_hosts, replication_address, load_balancer_name"
    ) in message


@pytest.mark.parametrize(
    ("fields", "secret"),
    [
        ({"portal_hosts": [f"{PASSWORD}.example.com"]}, PASSWORD),
        ({"portal_hosts": [f"https://{PASSWORD}"]}, PASSWORD),
        ({"portal_hosts": [f"[{PASSWORD}]"]}, PASSWORD),
        ({"portal_hosts": [f"fe80::1%{PASSWORD}"]}, PASSWORD),
        ({"portal_hosts": [{"address": PASSWORD}]}, PASSWORD),
        ({"portal_hosts": PASSWORD}, PASSWORD),
        ({"portal_hosts": ["10.20.0.9", "10.20.0.9"]}, "10.20.0.9"),
        ({"portal_hosts": ["10.20.0.9", "::ffff:10.20.0.9"]}, "10.20.0.9"),
        ({"portal_hosts": ["127.0.0.9"]}, "127.0.0.9"),
        ({"replication_address": f"{PASSWORD}.example.com"}, PASSWORD),
        ({"replication_address": f"https://{PASSWORD}"}, PASSWORD),
        ({"replication_address": f"[{PASSWORD}]"}, PASSWORD),
        ({"replication_address": f"fe80::1%{PASSWORD}"}, PASSWORD),
        ({"replication_address": [PASSWORD]}, PASSWORD),
        ({"replication_address": "127.0.0.9"}, "127.0.0.9"),
    ],
    ids=[
        "portal host a name",
        "portal host a URL",
        "bracketed portal host",
        "portal host with a zone id",
        "portal host a map",
        "portal_hosts a string",
        "portal host twice",
        "portal host twice, once IPv4-mapped",
        "portal host loopback",
        "replication_address a name",
        "replication_address a URL",
        "bracketed replication_address",
        "replication_address with a zone id",
        "replication_address a list",
        "replication_address loopback",
    ],
)
def test_no_address_from_an_invalid_entry_appears_in_the_message(tmp_path, fields, secret):
    message = refusal(tmp_path, {"edge01": {**EDGE, **fields}})

    assert message.startswith(f"{OUTPUT}: entry 'edge01': ")
    assert secret not in message


def test_addresses_are_given_in_their_canonical_form(tmp_path):
    entry = {**EDGE, "portal_hosts": ["FD00:0::5", "::ffff:10.20.0.6"], "replication_address": "::FFFF:10.20.0.5"}

    inventory = parse_output(tmp_path, {"edge01": entry})

    assert host_vars(inventory, "edge01")["qumulo_portal_hosts"] == [{"address": "fd00::5"}, {"address": "10.20.0.6"}]
    assert host_vars(inventory, "edge01")["qumulo_replication_address"] == "10.20.0.5"


@pytest.mark.parametrize("api_host", ["edge01", "edge01.corp.example.com", "10.20.0.5", "fd00::5"])
def test_api_host_takes_a_host_name_or_an_address(tmp_path, api_host):
    inventory = parse_output(tmp_path, {"edge01": {**EDGE, "api_host": api_host}})

    assert host_vars(inventory, "edge01")["qumulo_api_host"] == api_host


def test_every_invalid_entry_is_reported_at_once(tmp_path):
    message = refusal(tmp_path, {"edge01": {**EDGE, "cluster_kind": "hub"}, "edge02": {**EDGE, "managed_by": "x"}, "lab01": LAB})

    assert "entry 'edge01': cluster_kind" in message
    assert "entry 'edge02': managed_by" in message
    assert "lab01" not in message


def test_two_entries_with_one_cluster_uuid_fail_without_quoting_it(tmp_path):
    message = refusal(tmp_path, {"edge01": EDGE, "edge02": {**EDGE, "cluster_uuid": EDGE_UUID.upper()}, "lab01": LAB})

    assert "entries 'edge01' and 'edge02' give the same cluster_uuid" in message
    assert "lab01" not in message
    assert EDGE_UUID not in message.lower()


@pytest.mark.parametrize("name", ["edge 01", "lab/edge01"])
def test_an_entry_whose_name_is_no_host_name_fails(tmp_path, name):
    assert "an entry's name must be a host name without spaces or slashes" in refusal(tmp_path, {name: EDGE})


@pytest.mark.parametrize("value", [[EDGE], "edge01", None], ids=["list", "string", "null"])
def test_an_output_that_is_not_a_map_of_hosts_fails(tmp_path, value):
    assert f"{OUTPUT} must be a map from host name to cluster" in refusal(tmp_path, value)


# A load balancer's name, resolved into addresses

# A multi-AZ CNQ cluster in AWS has no floating IPs; its root publishes its network load balancer's DNS name.
NLB_NAME = "qumulo-nlb-0123456789abcdef.elb.us-east-1.amazonaws.com"
CNQ_MAZ = {**CNQ, "api_host": NLB_NAME, "load_balancer_name": NLB_NAME}


def answers(*addresses):
    """What getaddrinfo returns for addresses, each as one TCP result of its family."""
    results = []
    for address in addresses:
        if ":" in address:
            results.append((socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0, 0, 0)))
        else:
            results.append((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0)))
    return results


@pytest.fixture
def resolver(monkeypatch):
    """Stands in for the controller's resolver: names maps each name to the addresses it resolves to; asked records the names."""

    class Resolver:
        def __init__(self):
            self.names = {}
            self.asked = []

        def __call__(self, host, port, *args, **kwargs):
            self.asked.append(host)
            if host not in self.names:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            return answers(*self.names[host])

    stub = Resolver()
    monkeypatch.setattr(socket, "getaddrinfo", stub)
    return stub


def test_a_load_balancer_name_resolves_into_portal_hosts_and_replication_address(tmp_path, resolver):
    resolver.names[NLB_NAME] = ["10.0.2.31", "fd00:2::31", "10.0.1.30", "10.0.3.32"]

    variables = host_vars(parse_output(tmp_path, {"cnq01": CNQ_MAZ}), "cnq01")

    assert variables["qumulo_portal_hosts"] == [{"address": "10.0.1.30"}, {"address": "10.0.2.31"}, {"address": "10.0.3.32"}, {"address": "fd00:2::31"}]
    assert variables["qumulo_replication_address"] == "10.0.1.30"
    assert variables["qumulo_api_host"] == NLB_NAME
    assert "load_balancer_name" not in str(variables)


def test_resolved_addresses_are_deduplicated_in_their_canonical_form(tmp_path, resolver):
    resolver.names[NLB_NAME] = ["::ffff:10.0.1.30", "10.0.1.30", "FD00:2:0::31", "fd00:2::31"]

    variables = host_vars(parse_output(tmp_path, {"cnq01": CNQ_MAZ}), "cnq01")

    assert variables["qumulo_portal_hosts"] == [{"address": "10.0.1.30"}, {"address": "fd00:2::31"}]
    assert variables["qumulo_replication_address"] == "10.0.1.30"


def test_explicit_addresses_win_over_a_load_balancer_name(tmp_path, resolver):
    resolver.names[NLB_NAME] = ["10.0.1.30"]
    entry = {**CNQ_MAZ, "portal_hosts": ["10.0.1.20", "10.0.1.21"], "replication_address": "10.0.1.21"}

    variables = host_vars(parse_output(tmp_path, {"cnq01": entry}), "cnq01")

    assert variables["qumulo_portal_hosts"] == [{"address": "10.0.1.20"}, {"address": "10.0.1.21"}]
    assert variables["qumulo_replication_address"] == "10.0.1.21"
    assert resolver.asked == []


@pytest.mark.parametrize(
    ("explicit", "portal_hosts", "replication_address"),
    [
        ({"portal_hosts": ["10.0.1.20"]}, [{"address": "10.0.1.20"}], "10.0.1.30"),
        ({"replication_address": "10.0.1.20"}, [{"address": "10.0.1.30"}, {"address": "10.0.2.31"}], "10.0.1.20"),
    ],
    ids=["portal_hosts given", "replication_address given"],
)
def test_a_load_balancer_name_fills_only_the_address_fields_left_null(tmp_path, resolver, explicit, portal_hosts, replication_address):
    resolver.names[NLB_NAME] = ["10.0.2.31", "10.0.1.30"]

    variables = host_vars(parse_output(tmp_path, {"cnq01": {**CNQ_MAZ, "portal_hosts": None, "replication_address": None, **explicit}}), "cnq01")

    assert variables["qumulo_portal_hosts"] == portal_hosts
    assert variables["qumulo_replication_address"] == replication_address


def test_a_null_load_balancer_name_resolves_nothing(tmp_path, resolver):
    variables = host_vars(parse_output(tmp_path, {"cnq01": {**CNQ, "load_balancer_name": None}}), "cnq01")

    assert "qumulo_portal_hosts" not in variables
    assert "qumulo_replication_address" not in variables
    assert resolver.asked == []


@pytest.mark.parametrize("addresses", [None, []], ids=["unknown name", "no address"])
def test_a_load_balancer_name_that_resolves_to_nothing_fails_naming_it(tmp_path, resolver, addresses):
    if addresses is not None:
        resolver.names[NLB_NAME] = addresses

    message = refusal(tmp_path, {"cnq01": CNQ_MAZ})

    assert message.startswith(f"{OUTPUT}: entry 'cnq01': load_balancer_name {NLB_NAME} resolves to no address")


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0", "224.0.0.1", "fe80::1%eth0"], ids=["loopback", "unspecified", "multicast", "zone id"])
def test_a_load_balancer_name_that_resolves_to_an_unreachable_address_fails_naming_it(tmp_path, resolver, address):
    resolver.names[NLB_NAME] = ["10.0.1.30", address]

    message = refusal(tmp_path, {"cnq01": CNQ_MAZ})

    assert message.startswith(f"{OUTPUT}: entry 'cnq01': load_balancer_name {NLB_NAME} resolves to ")
    assert "the other side cannot reach" in message


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("10.0.1.30", "load_balancer_name must be a DNS name, not an IP address; give IP addresses in portal_hosts"),
        ("fd00::5", "load_balancer_name must be a DNS name, not an IP address; give IP addresses in portal_hosts"),
        (f"https://{NLB_NAME}", "load_balancer_name must be a DNS name"),
        (f"{NLB_NAME}:3713", "load_balancer_name must be a DNS name"),
        ("", "load_balancer_name must be a DNS name"),
        ([NLB_NAME], "load_balancer_name must be a DNS name"),
        ("qumulo-nlb", "load_balancer_name must be a fully qualified DNS name"),
    ],
    ids=["IPv4 address", "IPv6 address", "URL", "with a port", "empty", "a list", "one label"],
)
def test_a_load_balancer_name_that_is_no_dns_name_fails_without_resolving(tmp_path, resolver, name, problem):
    message = refusal(tmp_path, {"cnq01": {**CNQ_MAZ, "load_balancer_name": name}})

    assert message.startswith(f"{OUTPUT}: entry 'cnq01': {problem}")
    assert resolver.asked == []


@pytest.mark.parametrize("name", [f"https://{PASSWORD}", PASSWORD], ids=["URL", "one label"])
def test_a_malformed_load_balancer_name_is_neither_resolved_nor_quoted(tmp_path, resolver, name):
    message = refusal(tmp_path, {"cnq01": {**CNQ_MAZ, "load_balancer_name": name}})

    assert PASSWORD not in message
    assert resolver.asked == []


def test_one_load_balancer_name_that_fails_fails_the_whole_source_naming_only_its_entry(tmp_path, resolver):
    resolver.names[NLB_NAME] = ["10.0.1.30"]
    other = {**CNQ_MAZ, "cluster_uuid": LAB_UUID, "load_balancer_name": "qumulo-nlb-fedcba9876543210.elb.us-east-1.amazonaws.com"}

    message = refusal(tmp_path, {"cnq01": CNQ_MAZ, "cnq02": other})

    assert message == f"{OUTPUT}: entry 'cnq02': load_balancer_name {other['load_balancer_name']} resolves to no address on this controller"


def test_a_cache_hit_resolves_the_load_balancer_name_again(tmp_path, resolver):
    resolver.names[NLB_NAME] = ["10.0.1.30"]
    output = write_output(tmp_path, {"cnq01": CNQ_MAZ})
    source = cached_source(tmp_path)
    parse_and_flush(source)
    output.unlink()
    resolver.names[NLB_NAME] = ["10.0.1.40"]

    variables = host_vars(parse_and_flush(source), "cnq01")

    assert variables["qumulo_portal_hosts"] == [{"address": "10.0.1.40"}]
    assert variables["qumulo_replication_address"] == "10.0.1.40"


def test_a_cached_read_whose_lookup_fails_fails_the_source_naming_the_name(tmp_path, resolver):
    resolver.names[NLB_NAME] = ["10.0.1.30"]
    write_output(tmp_path, {"cnq01": CNQ_MAZ})
    source = cached_source(tmp_path)
    parse_and_flush(source)
    del resolver.names[NLB_NAME]

    with pytest.raises(AnsibleParserError) as caught:
        parse_and_flush(source)

    assert str(caught.value) == f"{OUTPUT}: entry 'cnq01': load_balancer_name {NLB_NAME} resolves to no address on this controller"


THREE_ZONES = ["10.0.1.30", "10.0.2.31", "10.0.3.32"]


def published_after(tmp_path, resolver, first, then, cached=True):
    """The qumulo_portal_hosts addresses of a second read whose name resolves to then, after a first that resolved to first."""
    write_output(tmp_path, {"cnq01": CNQ_MAZ})
    source = cached_source(tmp_path) if cached else write_source(tmp_path, {"output_file": "outputs.json"})
    resolver.names[NLB_NAME] = first
    parse_and_flush(source)
    resolver.names[NLB_NAME] = then
    return [host["address"] for host in host_vars(parse_and_flush(source), "cnq01")["qumulo_portal_hosts"]]


def test_a_cached_name_that_resolves_to_fewer_addresses_than_last_time_fails_naming_the_missing_ones(tmp_path, resolver):
    with pytest.raises(AnsibleParserError) as caught:
        published_after(tmp_path, resolver, THREE_ZONES, ["10.0.1.30", "10.0.2.31"])

    message = str(caught.value)
    assert message.startswith(f"{OUTPUT}: entry 'cnq01': load_balancer_name {NLB_NAME} resolves to fewer addresses than the inventory cache")
    assert "10.0.3.32" in message
    assert "10.0.1.30" not in message


@pytest.mark.parametrize(
    ("first", "then"),
    [(THREE_ZONES[:2], THREE_ZONES), (THREE_ZONES, THREE_ZONES), (THREE_ZONES, ["10.0.1.30", "10.0.2.31", "10.0.4.33"])],
    ids=["more", "the same", "one moved"],
)
def test_a_cached_name_that_resolves_to_no_fewer_addresses_publishes_them(tmp_path, resolver, first, then):
    assert published_after(tmp_path, resolver, first, then) == then


def test_without_the_cache_a_name_that_resolves_to_fewer_addresses_publishes_them(tmp_path, resolver):
    assert published_after(tmp_path, resolver, THREE_ZONES, THREE_ZONES[:2], cached=False) == THREE_ZONES[:2]


def test_a_refresh_takes_the_smaller_set_and_the_cache_remembers_it(tmp_path, resolver):
    write_output(tmp_path, {"cnq01": CNQ_MAZ})
    source = cached_source(tmp_path)
    resolver.names[NLB_NAME] = THREE_ZONES
    parse_and_flush(source)
    resolver.names[NLB_NAME] = THREE_ZONES[:2]

    refreshed = host_vars(parse_and_flush(source, cache=False), "cnq01")
    cached = host_vars(parse_and_flush(source), "cnq01")

    assert refreshed["qumulo_portal_hosts"] == [{"address": "10.0.1.30"}, {"address": "10.0.2.31"}]
    assert cached["qumulo_portal_hosts"] == refreshed["qumulo_portal_hosts"]


# No secret reaches a variable


@pytest.mark.parametrize(
    ("entry", "field"),
    [
        ({**EDGE, "password": PASSWORD}, "password"),
        ({**EDGE, "admin_password": PASSWORD}, "admin_password"),
        ({**EDGE, "api_token": PASSWORD}, "api_token"),
        ({**EDGE, "nexus_join_key": PASSWORD}, "nexus_join_key"),
        ({**EDGE, "Secret": PASSWORD}, "Secret"),
        ({**EDGE, "password_ref": {"type": "env", "id": "QUMULO_PASSWORD_EDGE01", "value": PASSWORD}}, "password_ref.value"),
        ({**EDGE, "password_ref": {"type": "env", "id": "QUMULO_PASSWORD_EDGE01", "secret_string": PASSWORD}}, "password_ref.secret_string"),
    ],
)
def test_a_secret_looking_field_is_refused_without_reading_its_value(tmp_path, entry, field):
    message = refusal(tmp_path, {"edge01": entry})

    assert f"entry 'edge01': field '{field}' looks like a secret" in message
    assert "password_ref" in message
    assert PASSWORD not in message


def test_no_value_from_an_invalid_entry_appears_in_the_message(tmp_path):
    entry = {**EDGE, "api_host": f"https://admin:{PASSWORD}@10.20.0.5", "cluster_uuid": PASSWORD}

    message = refusal(tmp_path, {"edge01": entry})

    assert "entry 'edge01': api_host must be a host name or address, not a URL" in message
    assert "entry 'edge01': cluster_uuid must be a UUID" in message
    assert PASSWORD not in message


# Caching


def cached_source(tmp_path, **extra):
    return write_source(
        tmp_path,
        {"output_file": "outputs.json", "cache": True, "cache_plugin": "ansible.builtin.jsonfile", "cache_connection": str(tmp_path / "cache"), **extra},
    )


def parse_and_flush(source, cache=True):
    """Parse as the inventory manager does, which writes the cache plugin's changes after each source."""
    plugin = inventory_loader.get(PLUGIN)
    inventory = InventoryData()
    plugin.parse(inventory, DataLoader(), str(source), cache=cache)
    try:
        plugin.update_cache_if_changed()
    except AttributeError:
        pass  # No cache plugin is loaded without the cache option; the inventory manager ignores this too.
    return inventory


def test_a_cache_hit_reads_neither_the_file_nor_terraform(tmp_path):
    output = write_output(tmp_path, OUTPUT_VALUE)
    source = cached_source(tmp_path)
    parse_and_flush(source)
    output.unlink()

    inventory = parse_and_flush(source)

    assert sorted(inventory.hosts) == ["cnq01", "edge01", "lab01"]
    assert host_vars(inventory, "edge01")["qumulo_cluster_uuid"] == EDGE_UUID


def test_a_cache_hit_keeps_the_addresses(tmp_path):
    output = write_output(tmp_path, {"cnq01": CNQ_ADDRESSED})
    source = cached_source(tmp_path)
    parse_and_flush(source)
    output.unlink()

    inventory = parse_and_flush(source)

    assert host_vars(inventory, "cnq01")["qumulo_portal_hosts"] == [{"address": "10.0.1.20"}, {"address": "10.0.1.21"}, {"address": "10.0.1.22"}]
    assert host_vars(inventory, "cnq01")["qumulo_replication_address"] == "10.0.1.20"


def test_a_refresh_reads_the_source_again_and_updates_the_cache(tmp_path):
    write_output(tmp_path, OUTPUT_VALUE)
    source = cached_source(tmp_path)
    parse_and_flush(source)
    write_output(tmp_path, {"edge01": EDGE})

    refreshed = parse_and_flush(source, cache=False)
    cached = parse_and_flush(source)

    assert sorted(refreshed.hosts) == ["edge01"]
    assert sorted(cached.hosts) == ["edge01"]


def test_a_refused_output_is_never_cached(tmp_path):
    write_output(tmp_path, {"edge01": {**EDGE, "password": PASSWORD}})
    source = cached_source(tmp_path)
    plugin = inventory_loader.get(PLUGIN)

    # Flushed even after a refusal, as a caller that writes the cache whatever happens would.
    with pytest.raises(AnsibleParserError):
        try:
            plugin.parse(InventoryData(), DataLoader(), str(source), cache=True)
        finally:
            plugin.update_cache_if_changed()

    cache_dir = tmp_path / "cache"
    stored = [path.read_text(encoding="utf-8") for path in cache_dir.rglob("*") if path.is_file()] if cache_dir.exists() else []
    assert not any(PASSWORD in text for text in stored)


def test_a_cached_entry_is_checked_again_when_read(tmp_path):
    write_output(tmp_path, OUTPUT_VALUE)
    source = cached_source(tmp_path)
    plugin = inventory_loader.get(PLUGIN)
    plugin.parse(InventoryData(), DataLoader(), str(source), cache=False)
    key = next(iter(plugin._cache))
    plugin._cache[key] = {"edge01": {**EDGE, "password": PASSWORD}}  # as an older plugin, or a hand edit, might leave it
    plugin.update_cache_if_changed()

    with pytest.raises(AnsibleParserError, match="field 'password' looks like a secret"):
        parse_and_flush(source)


def test_a_workspace_reads_its_own_cache_entry(tmp_path, monkeypatch):
    binary, record = stub_terraform(tmp_path, stdout=json.dumps({"edge01": EDGE}))
    monkeypatch.delenv("TF_WORKSPACE", raising=False)
    config = {"project_path": str(tmp_path), "binary_path": str(binary)}
    config |= {"cache": True, "cache_plugin": "ansible.builtin.jsonfile", "cache_connection": str(tmp_path / "cache")}
    parse_and_flush(write_source(tmp_path, {**config, "workspace": "a"}))
    (record / "stdout").write_text(json.dumps({"lab01": LAB}), encoding="utf-8")

    inventory = parse_and_flush(write_source(tmp_path, {**config, "workspace": "b"}))

    assert sorted(inventory.hosts) == ["lab01"]
    assert (record / "workspace").read_text(encoding="utf-8") == "b"


def test_without_the_cache_option_nothing_is_cached(tmp_path):
    output = write_output(tmp_path, OUTPUT_VALUE)
    source = write_source(tmp_path, {"output_file": "outputs.json", "cache_plugin": "ansible.builtin.jsonfile", "cache_connection": str(tmp_path / "cache")})
    parse_and_flush(source)
    output.unlink()

    with pytest.raises(AnsibleParserError, match="cannot read the Terraform output file"):
        parse_and_flush(source)


# Documentation


def plugin_source():
    return ast.parse(Path(inventory_loader.find_plugin(PLUGIN)).read_text(encoding="utf-8"))


def documented(text):
    return set((yaml.safe_load(text).get("options") or {}).keys())


def fragment_options(name):
    module = __import__(f"ansible.plugins.doc_fragments.{name}", fromlist=["ModuleDocFragment"])
    return documented(module.ModuleDocFragment.DOCUMENTATION)


def test_documented_options_are_the_options_the_plugin_reads():
    tree = plugin_source()
    documentation = next(
        node.value.value for node in tree.body if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "DOCUMENTATION" for t in node.targets)
    )
    doc = yaml.safe_load(documentation)
    own = documented(documentation) - {"plugin"}
    inherited = set().union(*(fragment_options(name.removeprefix("ansible.builtin.")) for name in doc["extends_documentation_fragment"]))
    read = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get_option" and isinstance(node.args[0], ast.Constant)
    }

    assert own == read - inherited, "each option the plugin documents is one it reads, and the reverse"
    assert read <= own | inherited, f"read but documented nowhere: {sorted(read - own - inherited)}"
    assert doc["options"]["plugin"]["choices"] == [PLUGIN]
