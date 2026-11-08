# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

DOCUMENTATION = r"""
name: terraform
short_description: Qumulo clusters from a Terraform root's qumulo_ansible_inventory output
version_added: 0.1.0
author: Qumulo, Inc. (@Qumulo)
description:
  - Adds one host for each entry of the C(qumulo_ansible_inventory) output of a Terraform root, with the variables
    the collection's playbooks and roles read.
  - The inventory source is a YAML file whose name ends in C(qumulo_terraform.yml) or C(qumulo_terraform.yaml).
  - The output maps each host name to C(api_host), C(cluster_uuid), C(cluster_kind) (V(edge), V(cnq) or
    V(baremetal)), C(managed_by) (V(none), V(terraform_provider) or V(legacy_provisioner)), C(owned_areas) and
    C(password_ref). C(docs/reference/terraform-inventory.md) lists the fields and the Terraform resources that fill
    them, and C(docs/how-to/terraform.md) shows how to publish the output.
  - Each host gets C(qumulo_api_host), C(qumulo_cluster_uuid), C(qumulo_cluster_kind), C(qumulo_managed_by),
    C(qumulo_terraform_owned_areas) and, unless the entry's C(password_ref) is null, C(qumulo_password_ref).
  - Each host joins a group for its kind, C(qumulo_edges), C(qumulo_cnq) or C(qumulo_baremetal), and one for who
    formed it, C(qumulo_managed_by_none), C(qumulo_managed_by_terraform_provider) or
    C(qumulo_managed_by_legacy_provisioner). All of these are children of C(qumulo_clusters).
  - C(api_host) is a host name or an IP address. The modules build C(https://<api_host>:<api_port>) themselves, so a
    URL, such as the C(endpoint) the provider's C(qumulo_filesystem_edge_baremetal) resource exports, is refused, and
    so is a value with a user name in it, such as C(admin@10.20.0.5).
  - An IPv6 C(api_host) is given bare, as C(fd00::5). A bracketed address is refused, and so is a zone id such as
    C(fe80::1%eth0).
  - C(owned_areas) is a list of area names, such as V(network.floating_ips) or V(dns). For a V(cnq) cluster with
    C(managed_by) set to V(terraform_provider) it must list V(network.floating_ips).
  - C(password_ref) is null, or a map of C(type), one of V(env), V(aws_ssm_parameter), V(aws_secrets_manager),
    V(azure_key_vault), V(gcp_secret_manager) or V(hashicorp_vault), and C(id), a non-empty string. The output never
    carries the password. C(group_vars) resolve the reference into C(qumulo_api_password) with a lookup.
  - An entry may also give C(portal_hosts), the IP addresses the other side of a portal reaches the cluster at, and
    C(replication_address), the IP address a replication source reaches it at, which the replication step of the
    C(qumulo.cluster.cluster_baseline) role gives the source. The host gets C(qumulo_portal_hosts), a list of
    C(address) items on the default portal port, and C(qumulo_replication_address), each address in its canonical
    form. Null, or no field, sets neither unless C(load_balancer_name) fills it, and C(host_vars) outrank both.
  - The plugin publishes the addresses the output gives, or those of a C(load_balancer_name) below, and derives none
    from the cluster. Each is an IP address without a host name, a port, brackets or a zone id; a URL, an unspecified,
    loopback or multicast address, an empty list and an address given twice are refused.
  - An entry may instead give C(load_balancer_name), the fully qualified DNS name of the load balancer in front of a
    multi-AZ cluster in AWS. The plugin resolves it on the controller each time it reads the output, a cached read
    included, and sets C(qumulo_portal_hosts) to its IPv4 and IPv6 addresses, in canonical form, without repeats and
    sorted, IPv4 before IPv6, and C(qumulo_replication_address) to the first, wherever the entry's C(portal_hosts) or
    C(replication_address) is null. Addresses the entry gives win. The name itself is never published as an address.
    A name that resolves to no address, or to a loopback, unspecified, multicast or zoned one, fails the source, and
    the message names it.
  - With O(cache), a name whose addresses are now a strict subset of those the cache recorded fails the source, naming
    the missing ones. Giving the addresses in C(portal_hosts) and C(replication_address), or running once with C(--flush-cache),
    ends the failure.
  - The output's schema is strict. An entry with a missing, unknown or malformed field fails the whole source, and the
    message names every such entry. So do two entries with the same C(cluster_uuid). A field whose name looks like a
    secret, such as C(password) or C(api_token), is refused the same way, and its value is neither read into a
    variable, nor cached, nor shown.
extends_documentation_fragment:
  - ansible.builtin.constructed
  - ansible.builtin.inventory_cache
options:
  plugin:
    description: The name of this plugin.
    type: str
    required: true
    choices: [qumulo.cluster.terraform]
  project_path:
    description:
      - The Terraform root to run C(terraform output -json qumulo_ansible_inventory) in.
      - A relative path is relative to the directory of the inventory source.
      - Set exactly one of O(project_path) and O(output_file).
    type: str
  binary_path:
    description:
      - The terraform executable that O(project_path) runs.
      - A name without a directory is looked up on C(PATH); a relative path is relative to the directory of the
        inventory source.
    type: str
    default: terraform
  workspace:
    description:
      - The Terraform workspace to read, selected through C(TF_WORKSPACE) for the one command.
      - Without it, C(TF_WORKSPACE) from the environment when it is set, and otherwise the root's selected workspace.
      - Only with O(project_path).
    type: str
  timeout:
    description:
      - Seconds to wait for C(terraform output) before failing the source, as when the root's backend does not answer.
      - Only with O(project_path).
    type: int
    default: 120
  output_file:
    description:
      - A file to read the output from instead of running terraform.
      - It holds what C(terraform output -json qumulo_ansible_inventory) prints. A file holding every output, as
        C(terraform output -json) without a name prints them, is refused.
      - A relative path is relative to the directory of the inventory source.
      - The C(ansible.builtin.yaml) inventory plugin reads C(.json) files in an inventory directory as inventories of
        their own, so a file kept in a directory given to C(-i) is parsed as one too.
    type: str
notes:
  - With O(cache), the plugin caches the hosts it read, after checking them, never the raw output. The cache key
    covers the source, O(project_path) or O(output_file), and the workspace, from O(workspace) or C(TF_WORKSPACE); a
    workspace switched with C(terraform workspace select) is read once the cache times out or is refreshed.
  - The C(TF_LOG) variables are removed from the environment terraform runs in.
  - The hosts have no C(ansible_connection). The collection's modules run on the controller, with
    C(ansible_connection=local), which the C(group_vars) example below sets.
seealso:
  - name: Terraform output command
    description: What C(terraform output -json) prints.
    link: https://developer.hashicorp.com/terraform/cli/commands/output
"""

EXAMPLES = r"""
# inventory/prod.qumulo_terraform.yml
# Add the clusters of a Terraform root, in its prod workspace, to the inventory.
plugin: qumulo.cluster.terraform
project_path: ../terraform/prod
workspace: prod

---
# inventory/ci.qumulo_terraform.yml
# Read a saved `terraform output -json qumulo_ansible_inventory` where the controller has no Terraform, pin each
# cluster's certificate authority, and group clusters by region from their host names.
# Keep the saved file outside the directory given to -i, so that no other inventory plugin reads it.
plugin: qumulo.cluster.terraform
output_file: ../terraform-output/qumulo_ansible_inventory.json
compose:
  qumulo_ca_path: "inventory_dir ~ '/../files/' ~ inventory_hostname ~ '-ca.pem'"
keyed_groups:
  - key: inventory_hostname.split('-')[0]
    prefix: region

---
# inventory/cached.qumulo_terraform.yml
# Read the output once an hour instead of on every run.
plugin: qumulo.cluster.terraform
project_path: ../terraform/prod
cache: true
cache_plugin: ansible.builtin.jsonfile
cache_connection: ~/.cache/qumulo-inventory
cache_timeout: 3600

---
# inventory/lab.qumulo_terraform.yml
# Run a Terraform binary that is not on PATH, and wait longer for a slow backend.
plugin: qumulo.cluster.terraform
project_path: ../terraform/lab
binary_path: /opt/terraform/1.11.0/terraform
timeout: 300

---
# inventory/group_vars/qumulo_clusters.yml
# Connect from the controller, and read each cluster's admin password from SSM or an environment variable,
# as its password_ref names. A host whose password_ref is null sets qumulo_api_password in its host_vars.
ansible_connection: local
ansible_python_interpreter: "{{ ansible_playbook_python }}"
qumulo_api_username: admin
qumulo_api_password: >-
  {{ lookup('amazon.aws.ssm_parameter', qumulo_password_ref.id)
     if qumulo_password_ref.type == 'aws_ssm_parameter'
     else lookup('ansible.builtin.env', qumulo_password_ref.id)
     if qumulo_password_ref.type == 'env'
     else undef(hint='no lookup for password_ref type ' ~ qumulo_password_ref.type) }}
qumulo_validate_certs: true
qumulo_ca_path: "{{ inventory_dir }}/../files/qumulo-ca.pem"
"""

import ipaddress
import json
import os
import re
import socket
import subprocess

from ansible.errors import AnsibleParserError
from ansible.plugins.inventory import BaseInventoryPlugin, Cacheable, Constructable

OUTPUT = "qumulo_ansible_inventory"
SOURCE_SUFFIXES = ("qumulo_terraform.yml", "qumulo_terraform.yaml")
FIELDS = ("api_host", "cluster_uuid", "cluster_kind", "managed_by", "owned_areas", "password_ref")
# Fields an entry may leave out or set to null.
OPTIONAL_FIELDS = ("portal_hosts", "replication_address", "load_balancer_name")
KIND_GROUPS = {"edge": "qumulo_edges", "cnq": "qumulo_cnq", "baremetal": "qumulo_baremetal"}
MANAGED_BY = ("none", "terraform_provider", "legacy_provisioner")
# The stores group_vars can resolve a password from; docs/how-to/terraform.md has a lookup for each.
PASSWORD_REF_TYPES = ("env", "aws_ssm_parameter", "aws_secrets_manager", "azure_key_vault", "gcp_secret_manager", "hashicorp_vault")
# On CNQ the provider re-applies the floating IPs on every operation, so they are always Terraform's there.
PROVIDER_OWNED = "network.floating_ips"
HOST_NAME = re.compile(r"^[^\s/]+$")
# A dotted run of digits that is no IP address, such as 10.20.0.256, is a typo, not a name.
ALL_DIGITS = re.compile(r"^[0-9.]+$")
DNS_NAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9_]([A-Za-z0-9_-]*[A-Za-z0-9_])?(\.[A-Za-z0-9_]([A-Za-z0-9_-]*[A-Za-z0-9_])?)*\.?$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
AREA = re.compile(r"^[a-z_]+(\.[a-z_]+)*$")
SECRET_NAME = re.compile(r"pass|secret|token|credential|private|key|value", re.IGNORECASE)


def field_problems(names, allowed, prefix):
    """Problems with field names outside allowed; a secret-looking name is refused as a secret, and no value is read."""
    problems = []
    for name in sorted(set(names) - set(allowed), key=str):
        label = f"{prefix}{name}"
        if SECRET_NAME.search(str(name)):
            problems.append(f"field '{label}' looks like a secret, and the output must carry none: remove it, and name the password with password_ref")
        else:
            problems.append(f"unknown field '{label}'; the fields are {', '.join(allowed)}")
    return problems


def api_host_problems(value):
    """What is wrong with api_host, which the client puts between https:// and :<api_port>, in brackets when IPv6."""
    if isinstance(value, str) and "://" in value:
        return ["api_host must be a host name or address, not a URL; the modules add https:// and the port themselves"]
    if isinstance(value, str) and "@" in value:
        return ["api_host must be a host name or address without a user name; qumulo_api_username names the user"]
    if isinstance(value, str) and value.startswith("["):
        return ["api_host must be an address without brackets; the modules put an IPv6 address in brackets themselves"]
    if isinstance(value, str) and "%" in value:
        return ["api_host must be an address without a zone id, which a URL cannot carry as written"]
    if isinstance(value, str) and (is_ip_address(value) or (DNS_NAME.match(value) and not ALL_DIGITS.match(value))):
        return []
    return ["api_host must be a host name or address, without a scheme, user, port or path"]


def is_ip_address(value):
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def address_problem(value, field, plural):
    """What is wrong with value as an address the other side reaches, worded for a list of addresses when plural."""
    must = "must hold IP addresses" if plural else "must be an IP address"
    if not isinstance(value, str):
        return f"{field} {must}"
    if "://" in value:
        return f"{field} {must}, not {'URLs' if plural else 'a URL'}"
    if value.startswith("["):
        return f"{field} {must} without brackets"
    if "%" in value:
        return f"{field} {must} without a zone id"
    if not is_ip_address(value):
        return f"{field} {must} such as 10.0.1.20 or fd00::5, without a host name or a port"
    address = canonical_address(value)
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        reaches = "must hold addresses the other side reaches" if plural else "must be an address the other side reaches"
        return f"{field} {reaches}, not unspecified, loopback or multicast ones"
    return None


def canonical_address(value):
    """value, a valid IP address, as one ip_address, with an IPv4-mapped IPv6 address as the IPv4 address it maps."""
    address = ipaddress.ip_address(value)
    return getattr(address, "ipv4_mapped", None) or address


def portal_hosts_problems(hosts):
    if hosts is None:
        return []
    if not isinstance(hosts, list):
        return ["portal_hosts must be a list of IP addresses, or null"]
    if not hosts:
        return ["portal_hosts must list at least one address, or be null"]
    problems = []
    for host in hosts:
        problem = address_problem(host, "portal_hosts", plural=True)
        if problem and problem not in problems:
            problems.append(problem)
    if not problems and len({canonical_address(host) for host in hosts}) < len(hosts):
        problems.append("portal_hosts lists an address twice")
    return problems


def replication_address_problems(address):
    if address is None:
        return []
    problem = address_problem(address, "replication_address", plural=False)
    return [problem] if problem else []


def load_balancer_name_problems(name):
    """What is wrong with load_balancer_name, a DNS name the plugin resolves; the value is not quoted, as it is unchecked."""
    if name is None:
        return []
    if isinstance(name, str) and is_ip_address(name):
        return ["load_balancer_name must be a DNS name, not an IP address; give IP addresses in portal_hosts and replication_address"]
    if not (isinstance(name, str) and DNS_NAME.match(name) and not ALL_DIGITS.match(name)):
        return ["load_balancer_name must be a DNS name, without a scheme, port or path"]
    # A load balancer's name always has several labels; a lone word could be a misplaced secret, which a failed lookup
    # would quote.
    if "." not in name.strip("."):
        return ["load_balancer_name must be a fully qualified DNS name, such as the load balancer's dns_name"]
    return []


def resolved_addresses(name):
    """(addresses, problem): name's addresses on this controller, canonical, without repeats, IPv4 first, or why not.

    getaddrinfo answers in an order the resolver chooses, so the addresses are sorted: replication_address takes the
    first, and a run that resolves the same addresses publishes the same one.
    """
    try:
        answers = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        answers = []
    texts = [sockaddr[0] for _family, _type, _proto, _canonname, sockaddr in answers]
    if not texts:
        return [], f"load_balancer_name {name} resolves to no address on this controller"
    if any(address_problem(text, "load_balancer_name", plural=True) for text in texts):
        return [], f"load_balancer_name {name} resolves to an address the other side cannot reach: loopback, unspecified, multicast or with a zone id"
    addresses = sorted({canonical_address(text) for text in texts}, key=lambda address: (address.version, address))
    return [str(address) for address in addresses], None


def with_load_balancer_addresses(hosts, previous):
    """(hosts, resolved): hosts, each entry with a load_balancer_name given the name's addresses for portal_hosts and
    replication_address wherever the entry leaves them out, and {name: addresses} for each name resolved.

    Addresses the entry gives win. previous holds the addresses each name resolved to on the run the inventory cache
    last recorded, and a name that now resolves to fewer of them fails: with cross-zone load balancing off, a load
    balancer's name leaves out a zone that has no healthy target, and publishing the shorter list would take that
    zone out of the portal. AnsibleParserError names each entry that fails.
    """
    hosts_out, resolved, problems = {}, {}, []
    for host, entry in hosts.items():
        missing = [field for field in ("portal_hosts", "replication_address") if field not in entry]
        if "load_balancer_name" not in entry or not missing:
            hosts_out[host] = entry
            continue
        name = entry["load_balancer_name"]
        addresses, problem = resolved_addresses(name)
        lost = [address for address in previous.get(name, []) if address not in addresses]
        if not problem and lost and set(addresses) < set(previous[name]):
            problem = (
                f"load_balancer_name {name} resolves to fewer addresses than the inventory cache recorded, without "
                f"{', '.join(lost)}; with cross-zone load balancing off, a zone with no healthy target drops out of the "
                "name. Give the addresses in portal_hosts and replication_address, or run once with --flush-cache to "
                "take the shorter list"
            )
        if problem:
            problems.append(f"entry {host!r}: {problem}")
            continue
        resolved[name] = addresses
        found = {"portal_hosts": addresses, "replication_address": addresses[0]}
        hosts_out[host] = entry | {field: found[field] for field in missing}
    if problems:
        raise AnsibleParserError(f"{OUTPUT}: " + "; ".join(problems))
    return hosts_out, resolved


def password_ref_problems(ref):
    if ref is None:
        return []
    if not isinstance(ref, dict):
        return ["password_ref must be a map of type and id, or null"]
    problems = field_problems(ref, ("type", "id"), "password_ref.")
    if ref.get("type") not in PASSWORD_REF_TYPES:
        problems.append(f"password_ref.type must be one of {', '.join(PASSWORD_REF_TYPES)}")
    if not isinstance(ref.get("id"), str) or not ref["id"].strip():
        problems.append("password_ref.id must be a non-empty string")
    return problems


def entry_problems(entry):
    """What is wrong with one host's entry. Values are never quoted, since a misplaced secret could be one."""
    if not isinstance(entry, dict):
        return [f"must be a map of {', '.join(FIELDS)}"]
    problems = field_problems(entry, FIELDS + OPTIONAL_FIELDS, "")
    problems += [f"{name} is missing" for name in FIELDS if name not in entry]
    api_host, uuid, owned = entry.get("api_host"), entry.get("cluster_uuid"), entry.get("owned_areas")
    if "api_host" in entry:
        problems += api_host_problems(api_host)
    if "cluster_uuid" in entry and not (isinstance(uuid, str) and UUID.match(uuid)):
        problems.append("cluster_uuid must be a UUID")
    if "cluster_kind" in entry and entry["cluster_kind"] not in tuple(KIND_GROUPS):
        problems.append(f"cluster_kind must be one of {', '.join(KIND_GROUPS)}")
    if "managed_by" in entry and entry["managed_by"] not in MANAGED_BY:
        problems.append(f"managed_by must be one of {', '.join(MANAGED_BY)}")
    if "owned_areas" in entry:
        if not (isinstance(owned, list) and all(isinstance(area, str) and AREA.match(area) for area in owned)):
            problems.append("owned_areas must be a list of area names, such as network.floating_ips or dns")
        elif entry.get("cluster_kind") == "cnq" and entry.get("managed_by") == "terraform_provider" and PROVIDER_OWNED not in owned:
            problems.append(f"owned_areas must list {PROVIDER_OWNED} for a cnq cluster with managed_by terraform_provider, which re-applies them")
    if "password_ref" in entry:
        problems += password_ref_problems(entry["password_ref"])
    problems += portal_hosts_problems(entry.get("portal_hosts"))
    problems += replication_address_problems(entry.get("replication_address"))
    problems += load_balancer_name_problems(entry.get("load_balancer_name"))
    return problems


def checked_hosts(value):
    """The output's entries as {host: entry}, or AnsibleParserError naming every bad entry and what is wrong with it."""
    if not isinstance(value, dict):
        raise AnsibleParserError(f"{OUTPUT}: {OUTPUT} must be a map from host name to cluster")
    problems = []
    for name, entry in value.items():
        if not (isinstance(name, str) and HOST_NAME.match(name)):
            problems.append("an entry's name must be a host name without spaces or slashes")
            continue
        found = entry_problems(entry)
        if len(found) == 1 and not isinstance(entry, dict):
            problems.append(f"entry {name!r} {found[0]}")
        else:
            problems += [f"entry {name!r}: {problem}" for problem in found]
    problems += shared_uuid_problems(value)
    if problems:
        raise AnsibleParserError(f"{OUTPUT}: " + "; ".join(problems))
    return {
        name: {field: entry[field] for field in FIELDS} | {field: entry[field] for field in OPTIONAL_FIELDS if entry.get(field) is not None}
        for name, entry in value.items()
    }


def shared_uuid_problems(value):
    """One problem for each entry whose cluster_uuid, in any case, an earlier entry gives too; the UUID is not quoted."""
    first_with = {}
    problems = []
    for name, entry in value.items():
        uuid = entry.get("cluster_uuid") if isinstance(entry, dict) else None
        if not (isinstance(uuid, str) and UUID.match(uuid)):
            continue
        if uuid.lower() in first_with:
            problems.append(f"entries {first_with[uuid.lower()]!r} and {name!r} give the same cluster_uuid; each cluster is one host")
        else:
            first_with[uuid.lower()] = name
    return problems


def is_output_listing(document):
    """document is what `terraform output -json` prints without an output name: every output, each with its value and type."""
    return isinstance(document, dict) and bool(document) and all(isinstance(v, dict) and {"value", "type"} <= set(v) for v in document.values())


def decoded(text, origin):
    try:
        return json.loads(text)
    except ValueError:
        raise AnsibleParserError(f"{OUTPUT}: {origin} is not JSON") from None


class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    NAME = "qumulo.cluster.terraform"

    def verify_file(self, path):
        return super().verify_file(path) and path.endswith(SOURCE_SUFFIXES)

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path, cache)
        self._read_config_data(path)
        base = os.path.dirname(path)
        project, output_file = self._source(base)
        workspace = (self.get_option("workspace") or os.environ.get("TF_WORKSPACE")) if project else None
        key = self.get_cache_key(json.dumps([path, project, output_file, workspace]))
        # The addresses each load_balancer_name resolved to, kept apart from the hosts, which keep only the name.
        addresses_key = self.get_cache_key(json.dumps([path, project, output_file, workspace, "load_balancer_addresses"]))
        read_cache = self.get_option("cache") and cache
        write_cache = self.get_option("cache") and not cache
        hosts = None
        if read_cache:
            try:
                hosts = checked_hosts(self._cache[key])
            except KeyError:
                write_cache = True
        if hosts is None:
            hosts = checked_hosts(self._run_terraform(project, base) if project else self._read_file(output_file))
        if write_cache:
            self._cache[key] = hosts
        # After the cache, which keeps the name, so that each run resolves it again.
        previous = self._cached(addresses_key) if read_cache else {}
        hosts, resolved = with_load_balancer_addresses(hosts, previous)
        if self.get_option("cache") and resolved and previous | resolved != previous:
            self._cache[addresses_key] = previous | resolved
        for name, entry in hosts.items():
            self._add(name, entry)

    def _cached(self, key):
        """The map the cache holds at key, or {} when it holds none."""
        try:
            value = self._cache[key]
        except KeyError:
            return {}
        return value if isinstance(value, dict) else {}

    def _source(self, base):
        """(project_path, None) or (None, output_file), resolved against base, the inventory source's directory."""
        project, output_file = self.get_option("project_path"), self.get_option("output_file")
        if project and output_file:
            raise AnsibleParserError(f"{OUTPUT}: set one of project_path and output_file, not both")
        if output_file and self.get_option("workspace"):
            raise AnsibleParserError(f"{OUTPUT}: workspace applies only to project_path, not to output_file")
        if not (project or output_file):
            raise AnsibleParserError(f"{OUTPUT}: set one of project_path and output_file")
        return (resolved(project, base), None) if project else (None, resolved(output_file, base))

    def _read_file(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise AnsibleParserError(f"{OUTPUT}: cannot read the Terraform output file {path}: {e.strerror}") from None
        document = decoded(text, path)
        if is_output_listing(document):
            raise AnsibleParserError(
                f"{OUTPUT}: {path} holds every output of the Terraform root, sensitive ones in plain text; "
                f"save only `terraform output -json {OUTPUT}` and delete this file"
            )
        return document

    def _run_terraform(self, project, base):
        if not os.path.isdir(project):
            raise AnsibleParserError(f"{OUTPUT}: project_path {project} is not a directory")
        binary = self.get_option("binary_path")
        if os.sep in binary:
            binary = resolved(binary, base)
        # The failure message quotes stderr, where a trace log would put provider requests and responses.
        env = {name: value for name, value in os.environ.items() if not name.startswith("TF_LOG")}
        if self.get_option("workspace"):
            env["TF_WORKSPACE"] = self.get_option("workspace")
        command = [binary, f"-chdir={project}", "output", "-no-color", "-json", OUTPUT]
        timeout = self.get_option("timeout")
        try:
            done = subprocess.run(command, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False, timeout=timeout)
        except OSError as e:
            raise AnsibleParserError(f"{OUTPUT}: cannot run {binary} ({e.strerror}); install terraform or set binary_path") from None
        except subprocess.TimeoutExpired:
            raise AnsibleParserError(f"{OUTPUT}: terraform output in {project} did not finish in {timeout}s; check its backend, or raise timeout") from None
        if done.returncode != 0:
            raise AnsibleParserError(f"{OUTPUT}: terraform output in {project} failed with exit code {done.returncode}: {done.stderr.strip()}")
        return decoded(done.stdout, f"the output of terraform in {project}")

    def _add(self, name, entry):
        self.inventory.add_group("qumulo_clusters")
        self.inventory.add_host(name)
        for group in (KIND_GROUPS[entry["cluster_kind"]], f"qumulo_managed_by_{entry['managed_by']}"):
            self.inventory.add_group(group)
            self.inventory.add_child("qumulo_clusters", group)
            self.inventory.add_host(name, group=group)
        variables = {
            "qumulo_api_host": entry["api_host"],
            "qumulo_cluster_uuid": entry["cluster_uuid"],
            "qumulo_cluster_kind": entry["cluster_kind"],
            "qumulo_managed_by": entry["managed_by"],
            "qumulo_terraform_owned_areas": entry["owned_areas"],
        }
        if entry["password_ref"] is not None:
            variables["qumulo_password_ref"] = entry["password_ref"]
        if "portal_hosts" in entry:
            variables["qumulo_portal_hosts"] = [{"address": str(canonical_address(address))} for address in entry["portal_hosts"]]
        if "replication_address" in entry:
            variables["qumulo_replication_address"] = str(canonical_address(entry["replication_address"]))
        for variable, value in variables.items():
            self.inventory.set_variable(name, variable, value)
        strict = self.get_option("strict")
        self._set_composite_vars(self.get_option("compose"), self.inventory.get_host(name).get_vars(), name, strict=strict)
        host_vars = self.inventory.get_host(name).get_vars()
        self._add_host_to_composed_groups(self.get_option("groups"), host_vars, name, strict=strict)
        self._add_host_to_keyed_groups(self.get_option("keyed_groups"), host_vars, name, strict=strict)


def resolved(path, base):
    """path with ~ and variables expanded, relative to base (the inventory source's directory) unless absolute."""
    path = os.path.expanduser(os.path.expandvars(path))
    return path if os.path.isabs(path) else os.path.join(base, path)
