# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""The network area: name resolution, time, the cluster's network document, and its authoritative DNS server."""

from __future__ import annotations

import ipaddress
import re
from typing import ClassVar

from ansible.module_utils.parsing.convert_bool import boolean
from ansible_collections.qumulo.cluster.plugins.module_utils import continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.client import READ_ONLY_WHY, QumuloApiError, QumuloError, QumuloMaybeApplied, Route, base_url
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import NOT_OFFERED_STATUS, _read, _require_configured, get_offered
from ansible_collections.qumulo.cluster.plugins.module_utils.common import SubsetUnavailable
from ansible_collections.qumulo.cluster.plugins.module_utils.continuity import Unavailable, require_continuity
from ansible_collections.qumulo.cluster.plugins.module_utils.identity import Pending
from ansible_collections.qumulo.cluster.plugins.module_utils.resource import Action, Field, Settings

DNS = Route("/v1/dns/configs/system", ("GET", "PATCH"))
TIME = Route("/v2/time/settings", ("GET", "PATCH"))
NTP, HYPERVISOR = "API_TIME_SOURCE_NTP", "API_TIME_SOURCE_HYPERVISOR"


def canonical_ip(text):
    """The canonical text of an IP address, such as 2001:db8::1 for 2001:DB8::0001; ValueError when it is not one."""
    return str(ipaddress.ip_address(text.strip()))


def literal_address(host):
    """api_host's IP address, canonical, when it is one, an IPv6 address in a URL's brackets included; else None."""
    text = host.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return canonical_ip(text)
    except ValueError:
        return None


def _unmapped(text):
    """An IPv4-mapped IPv6 address as its IPv4 address, as the cluster stores it; any other text as given."""
    try:
        mapped = getattr(ipaddress.ip_address(text.strip()), "ipv4_mapped", None)
    except ValueError:
        return text
    return str(mapped) if mapped else text


def _canonical_servers(servers):
    """Server addresses the cluster stored as sent, in canonical form; text that does not parse is kept, so it shows as a change."""
    out = []
    for server in servers:
        try:
            out.append(canonical_ip(server))
        except ValueError:
            out.append(server)
    return out


class DnsSettings(Settings):
    RETURN_KEY = "dns_settings"
    ROUTE = DNS
    FIELDS: ClassVar[dict[str, Field]] = {
        "servers": Field("dns_servers", read=_canonical_servers),
        "search_domains": Field("dns_search_domains"),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        # Checked before any request: the cluster refuses a name with ip_address_parse_error only after the read.
        self.servers = None if self.params.get("servers") is None else [self._server(server) for server in self.params["servers"]]

    def desired(self, before):
        after = super().desired(before)
        if self.servers is not None:
            after["servers"] = self.servers
        return after

    @staticmethod
    def _server(server):
        try:
            return canonical_ip(server)
        except ValueError:
            raise QumuloError(f"servers: '{server}' is not an IP address; the cluster accepts DNS servers only as IP addresses") from None


LOOKUP_OVERRIDES = Route("/v1/dns/lookup-override-config", ("GET", "PUT"))
ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9.-]*[A-Za-z0-9]$")  # the cluster's own rule, from its refusal of other names


def _address_key(text):
    """Sorts addresses as the cluster's own lists do: IPv4 before IPv6, each by value; text that does not parse last."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return (7, 0, text)
    return (address.version, int(address), text)


def _override_address(entry):
    return _canonical_servers([_unmapped(entry["ip_address"])])[0]


def canonical_overrides(entries):
    """Lookup overrides sorted by address, each address canonical and its names in order, which is part of the setting.

    The cluster keeps an address sent in several entries; they read as one, the names in table order without repeats.
    """
    merged = {}
    for entry in entries:
        aliases = merged.setdefault(_override_address(entry), [])
        aliases.extend(alias for alias in entry["aliases"] if alias not in aliases)
    return [{"ip_address": address, "aliases": merged[address]} for address in sorted(merged, key=_address_key)]


def duplicated_addresses(entries):
    """The canonical addresses the cluster's table lists in more than one entry, sorted."""
    seen, twice = set(), set()
    for entry in entries:
        address = _override_address(entry)
        (twice if address in seen else seen).add(address)
    return sorted(twice, key=_address_key)


class DnsLookupOverrides(Settings):
    """The whole lookup override table, PUT only when it differs."""

    RETURN_KEY = "dns_lookup_overrides"
    ROUTE = LOOKUP_OVERRIDES
    WRITE = "PUT"
    FIELDS: ClassVar[dict[str, Field]] = {"overrides": Field("lookup_overrides", read=canonical_overrides)}

    def __init__(self, module, client):
        super().__init__(module, client)
        # Checked before any request, so that a refusal names the entry rather than the server's error.
        seen = set()
        for index, entry in enumerate(self.params["overrides"]):
            where = f"overrides[{index}]"
            try:
                address = canonical_ip(_unmapped(entry["ip_address"]))
            except ValueError:
                raise QumuloError(f"{where}.ip_address: '{entry['ip_address']}' is not an IP address") from None
            if address in seen:
                raise QumuloError(f"overrides: {address} is listed twice; give all of its names in one entry")
            seen.add(address)
            for alias in entry["aliases"]:
                if not ALIAS.match(alias):
                    raise QumuloError(
                        f"{where}.aliases: '{alias}' is not a name the cluster takes; a name starts with a letter, ends with a "
                        "letter or digit, holds only letters, digits, - and ., and is at least two characters long"
                    )
        self.overrides = canonical_overrides(self.params["overrides"])

    def desired(self, before):
        return {**before, "overrides": self.overrides}


CLEAR_DNS_CACHE = Route(
    "/v1/dns/clear-dns-cache",
    ("POST",),
    replay=frozenset({"POST"}),
    why="clearing a cache twice does no harm, so a clear lost to a 5xx or a dropped connection is sent again",
)


class DnsCacheClear(Action):
    """dns_cache_clear: nothing about the cache can be read back, so every run clears it (G-ACTIONS)."""

    RETURN_KEY = "dns_cache_clear"

    def __init__(self, module, client):
        super().__init__(module, client)
        self.caches = sorted(set(self.params["caches"]))
        if not self.caches:
            raise QumuloError("caches is empty, so there is nothing to clear; leave it unset to clear both")

    def plan(self):
        return {"caches": self.caches}

    def perform(self, plan):
        self.client.post(CLEAR_DNS_CACHE, body={"skip_forward_cache": "forward" not in plan["caches"], "skip_reverse_cache": "reverse" not in plan["caches"]})


class TimeSettings(Settings):
    RETURN_KEY = "time_settings"
    ROUTE = TIME
    FIELDS: ClassVar[dict[str, Field]] = {
        "source": Field("source"),
        "ntp_servers": Field("ntp_servers"),
        "use_ad_for_primary": Field("use_ad_for_primary"),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        for server in self.params.get("ntp_servers") or []:
            if not server or server != server.strip():
                raise QumuloError(f"ntp_servers: {server!r} is empty or has leading or trailing spaces, which the cluster refuses")

    def desired(self, before):
        after = super().desired(before)
        if after.get("source") == NTP and not after.get("ntp_servers"):
            raise QumuloError(f"{NTP} needs at least one entry in ntp_servers")
        if after.get("source") == HYPERVISOR and (after.get("ntp_servers") or after.get("use_ad_for_primary")):
            raise QumuloError(f"{HYPERVISOR} needs ntp_servers: [] and use_ad_for_primary: false; the cluster refuses it with either set")
        return after

    def update(self, current, after, keys):
        try:
            super().update(current, after, keys)
        except QumuloApiError as e:
            # Observed class name unverified on a live Qumulo-managed cluster; matched without its suffix.
            if "time_configuration_disabled" in e.error_class:
                raise QumuloError(
                    f"this cluster's time is managed by Qumulo, so it refuses time settings ({e.error_class}); "
                    "remove the time settings from this cluster's baseline"
                ) from None
            raise


NETWORK = Route("/v3/network", ("GET", "PUT"), etag="drift", why="the ETag hashes the nodes' addresses, which change as they are allocated")
NETWORK_VALIDATE = Route(
    "/v3/network/validate",
    ("PUT",),
    etag="omit",
    replay=frozenset({"PUT"}),
    why="validation writes nothing, so the drifting ETag of /v3/network would only fail it with spurious 412s",
)
NETWORK_STATUS = Route("/v3/network/status", ("GET",))
PARTS = ("interfaces", "networks", "floating_ips")
ADDRESS_KEYS = {"STATIC": "static_addresses", "DHCP": "dhcp_addresses", "HOST": "host_addresses"}
NETWORK_FIELDS = ("name", "type", "tenant", "tenant_id", "netmask", "default_gateway", "ip_ranges")
# network_config's options for one frontend network, which tenancy_plan takes for the networks of an inventory too.
NETWORK_OPTIONS = {
    "id": {"type": "int", "required": True},
    "name": {"type": "str"},
    "type": {"type": "str", "choices": ["DHCP", "STATIC", "HOST"]},
    "tenant": {"type": "str"},
    "tenant_id": {"type": "int", "removed_in_version": "0.3.0", "removed_from_collection": "qumulo.cluster"},
    "netmask": {"type": "str"},
    "default_gateway": {"type": "str"},
    "ip_ranges": {"type": "list", "elements": "str"},
    "floating_ip_ranges": {"type": "list", "elements": "str"},
}


def _interval(text, strip_ends):
    """(version, first, last) of a single address, a first-last range (last may give only its trailing parts), or a CIDR block."""
    text = text.strip()
    if "/" in text:
        block = ipaddress.ip_network(text, strict=False)
        first, last = int(block.network_address), int(block.broadcast_address)
        if strip_ends and block.version == 4 and block.num_addresses > 2:
            first, last = first + 1, last - 1  # a static network never uses a block's network and broadcast addresses
        return block.version, first, last
    start, dash, end = text.partition("-")
    first = ipaddress.ip_address(start)
    if not dash:
        return first.version, int(first), int(first)
    try:
        # A whole last address, which an IPv6 one compressed with :: writes in fewer than eight groups.
        last = ipaddress.ip_address(end)
    except ValueError:
        sep = "." if first.version == 4 else ":"
        full = first.exploded.split(sep)
        tail = end.split(sep)
        if len(tail) >= len(full):
            raise
        last = ipaddress.ip_address(sep.join(full[: len(full) - len(tail)] + tail))
    if last.version != first.version or int(last) < int(first):
        raise ValueError(text)
    return first.version, int(first), int(last)


def _merge(intervals):
    merged = []
    for version, first, last in sorted(intervals):
        if merged and merged[-1][0] == version and first <= merged[-1][2] + 1:
            merged[-1][2] = max(merged[-1][2], last)
        else:
            merged.append([version, first, last])
    return merged


def _render(version, first, last, concise):
    a, b = ipaddress.ip_address(first), ipaddress.ip_address(last)
    if first == last:
        return str(a)
    if concise and version == 4:
        start, end = str(a).split("."), str(b).split(".")
        same = next(i for i in range(4) if start[i] != end[i])
        return f"{a}-{'.'.join(end[same:])}"  # the server's own form: 10.1.1.20-21
    return f"{a}-{b}"


def canonical_ranges(ranges, strip_ends=False, concise=False):
    """Address ranges merged and sorted, written first-last, or as the server writes them with concise."""
    return [_render(*interval, concise) for interval in _merge(_interval(text, strip_ends) for text in ranges or [])]


def canonical_netmask(text):
    if text in (None, ""):
        return None
    text = str(text).strip().lstrip("/")
    if text.isdigit() and int(text) <= 32:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{text}").netmask)
    if "/" in text:
        block = ipaddress.ip_network(text, strict=False)
        return str(block.netmask) if block.version == 4 else str(block)
    return str(ipaddress.IPv4Network(f"0.0.0.0/{text}").netmask)


def _flatten(network):
    """A wire network in the option shape, canonical, so that two documents the server treats alike compare equal."""
    kind = network["addresses"]["type"]
    block = network["addresses"].get(ADDRESS_KEYS[kind]) or {}
    return {
        "id": network["id"],
        "name": network["name"],
        "type": kind,
        "tenant_id": network.get("tenant_id"),
        "netmask": canonical_netmask(block.get("netmask")),
        "default_gateway": block.get("default_gateway") or None,
        "ip_ranges": canonical_ranges(block.get("ip_ranges")),
        "floating_ip_ranges": canonical_ranges(block.get("floating_ip_ranges")),
    }


def _wire(network):
    """A flattened network as the server takes it."""
    block = {"floating_ip_ranges": canonical_ranges(network["floating_ip_ranges"], concise=True), "netmask": network["netmask"]}
    if network["type"] == "STATIC":
        # The decoder refuses a null gateway (qumulo-findings item 36); "" is a network without one, as the read takes it.
        block = {"default_gateway": network["default_gateway"] or "", "ip_ranges": canonical_ranges(network["ip_ranges"], concise=True), **block}
    wire = {"id": network["id"], "name": network["name"], "addresses": {"type": network["type"], ADDRESS_KEYS[network["type"]]: block}}
    if network["tenant_id"] is not None:
        wire["tenant_id"] = network["tenant_id"]
    return wire


def sendable(document):
    """A wire document as it is sent: a network whose tenant is Pending goes without tenant_id (spec 014, FR-014).

    A Pending tenant is one a check run predicts an earlier task creates, so it has no id yet. It stays in the
    document the task compares, where it differs from every tenant the cluster has.
    """
    networks = [{k: v for k, v in network.items() if not (k == "tenant_id" and isinstance(v, Pending))} for network in document["frontend_networks"]]
    return {**document, "frontend_networks": networks}


BONDS = ("frontend_bond_config", "backend_bond_config")


def _set_keys(entry):
    """A key whose value is null compares equal to one that is left out."""
    return {key: value for key, value in entry.items() if value is not None}


def _canonical_interfaces(interfaces):
    if interfaces is None:
        return None
    out = _set_keys(interfaces)
    for key in BONDS:
        if out.get(key):
            out[key] = _set_keys({**out[key], "networks": sorted(out[key].get("networks") or [])})
    out["frontend_vlans"] = sorted((_set_keys(vlan) for vlan in out.get("frontend_vlans") or []), key=lambda vlan: vlan["vlan_id"])
    return out


def overlay_interfaces(given, live):
    """The task's interfaces over the cluster's: a key the task leaves out keeps the cluster's value.

    Bonds merge key by key. VLANs match by vlan_id, each merging over the cluster's VLAN of that id, and the task's
    list is the whole set of VLANs.
    """
    live = live or {}
    out = {**live, **given}
    for key in BONDS:
        if isinstance(given.get(key), dict) and isinstance(live.get(key), dict):
            out[key] = {**live[key], **given[key]}
    if given.get("frontend_vlans") is not None:
        current = {vlan["vlan_id"]: vlan for vlan in live.get("frontend_vlans") or []}
        out["frontend_vlans"] = [{**current.get(vlan["vlan_id"], {}), **vlan} for vlan in given["frontend_vlans"]]
    return out


def canonical_document(document):
    return {
        "frontend_networks": sorted((_flatten(network) for network in document.get("frontend_networks") or []), key=lambda network: network["id"]),
        "managed_interfaces": _canonical_interfaces(document.get("managed_interfaces")),
    }


def wire_document(document):
    """A canonical document, such as network_info's document subset, back in the wire shape desired_document reads.

    A network's tenant name, which network_info gives beside tenant_id, is dropped; tenant_id is kept.
    """
    return {"managed_interfaces": document["managed_interfaces"], "frontend_networks": [_wire(network) for network in document["frontend_networks"]]}


def same_document(want, have):
    """Equal, except that a tenant_id left to the server matches whatever tenant it chose."""
    if want["managed_interfaces"] != have["managed_interfaces"] or len(want["frontend_networks"]) != len(have["frontend_networks"]):
        return False
    for a, b in zip(want["frontend_networks"], have["frontend_networks"]):
        if {**a, "tenant_id": a["tenant_id"] if a["tenant_id"] is not None else b["tenant_id"]} != b:
            return False
    return True


def wanted_networks(networks, owned_parts, place=lambda index: f"frontend_networks[{index}]"):
    """The task's frontend networks with their ranges and netmasks canonical, or None when it gives none.

    Every field of a part outside owned_parts fails here, before any request. place words the network at an index for
    the messages.
    """
    if networks is None:
        return None
    if not owned_parts & {"networks", "floating_ips"}:
        raise QumuloError("frontend_networks needs networks or floating_ips in owned_parts")
    wanted = []
    for index, network in enumerate(networks):
        where = place(index)
        for field, part in [*((field, "networks") for field in NETWORK_FIELDS), ("floating_ip_ranges", "floating_ips")]:
            if network.get(field) is not None and part not in owned_parts:
                raise QumuloError(f"{where}.{field} is in {part}, which this task does not own; add it to owned_parts")
        if "networks" in owned_parts and not (network.get("name") and network.get("type")):
            raise QumuloError(f"{where} needs name and type, since networks is owned and the list is the whole set of networks")
        entry = dict(network)
        for field in ("ip_ranges", "floating_ip_ranges"):
            if entry.get(field) is not None:
                entry[field] = _checked_ranges(where, field, entry[field], strip_ends=network.get("type") == "STATIC")
        if entry.get("netmask") is not None:
            try:
                entry["netmask"] = canonical_netmask(entry["netmask"])
            except ValueError:
                raise QumuloError(f"{where}.netmask: '{network['netmask']}' is not a netmask") from None
        wanted.append(entry)
    return wanted


def _checked_ranges(where, field, ranges, strip_ends):
    for text in ranges:
        try:
            _interval(text, strip_ends)
        except ValueError:
            raise QumuloError(f"{where}.{field}: '{text}' is not an address range") from None
    return canonical_ranges(ranges, strip_ends)


def desired_document(live, wanted, interfaces, owned_parts, place=lambda index: f"frontend_networks[{index}]"):
    """The wire document a task asks for, built over the live one so that the parts it does not own are kept.

    wanted is wanted_networks' answer, or None when the task gives no frontend networks, and interfaces the task's
    managed_interfaces, or None when it gives none. place words the network at an index of wanted for the messages.
    """
    document = {"managed_interfaces": live.get("managed_interfaces"), "frontend_networks": list(live.get("frontend_networks") or [])}
    if "interfaces" in owned_parts and interfaces is not None:
        document["managed_interfaces"] = overlay_interfaces(interfaces, live.get("managed_interfaces"))
    if wanted is None:
        return document
    current = {network["id"]: _flatten(network) for network in document["frontend_networks"]}
    if "networks" in owned_parts:
        document["frontend_networks"] = [_wire(merge_network(entry, current.get(entry["id"]), owned_parts)) for entry in wanted]
        return document
    for index, entry in enumerate(wanted):
        if entry["id"] not in current:
            raise QumuloError(f"{place(index)}: network id {entry['id']} does not exist")
        if entry.get("floating_ip_ranges") is not None:
            current[entry["id"]]["floating_ip_ranges"] = entry["floating_ip_ranges"]
    document["frontend_networks"] = [_wire(current[network["id"]]) for network in document["frontend_networks"]]
    return document


def planned_document(live, wanted, interfaces, owned_parts, place=lambda index: f"frontend_networks[{index}]"):
    """desired_document, less the tenant of every network the backend bond lists.

    A backend-bond network never carries a tenant (spec 014, FR-013): with one tenant the cluster puts it in that
    tenant, and once there are two, a tenant network with no frontend interface aborts every node.
    """
    document = desired_document(live, wanted, interfaces, owned_parts, place)
    backend = backend_networks(document)
    for network in document["frontend_networks"]:
        if network["id"] in backend:
            network.pop("tenant_id", None)
    return document


def merge_network(entry, live_network, owned_parts):
    """A wanted network over the cluster's flattened one, or over nothing for a new network: a field left unset keeps the cluster's."""
    base = live_network or {
        "id": entry["id"],
        "name": None,
        "type": None,
        "tenant_id": None,
        "netmask": None,
        "default_gateway": None,
        "ip_ranges": [],
        "floating_ip_ranges": [],
    }
    merged = {key: base[key] if entry.get(key) is None else entry[key] for key in base}
    if "floating_ips" not in owned_parts:
        merged["floating_ip_ranges"] = base["floating_ip_ranges"]
    return merged


def holding(document, address):
    """The network of a canonical document whose node or floating ranges hold address, an IP address's text; else None.

    Only a STATIC network has node ranges, so a DHCP or HOST network holds only its floating addresses.
    """
    wanted = ipaddress.ip_address(address)
    for network in document["frontend_networks"]:
        for text in network["ip_ranges"] + network["floating_ip_ranges"]:
            version, first, last = _interval(text, False)
            if version == wanted.version and first <= int(wanted) <= last:
                return network
    return None


def read_status(client):
    """The /v3/network/status answer, read once per module run, or continuity.Unavailable with the reason.

    An HTTP 500, which the Qumulo Core container answers (G-NET-APPLY), and a status the cluster does not offer are
    unavailable; any other failure fails the module.
    """
    if "network_status" not in client.memo:
        try:
            client.memo["network_status"] = client.get(NETWORK_STATUS).data
        except QumuloApiError as e:
            if e.status != 500 and e.status not in NOT_OFFERED_STATUS:
                raise
            client.memo["network_status"] = Unavailable(str(e))
    return client.memo["network_status"]


def _status_address(text):
    """An address as status writes it, which may carry a prefix length; None for an empty or unparsable one."""
    try:
        return ipaddress.ip_interface(text.strip()).ip
    except (AttributeError, ValueError):
        return None


def _lists(texts, wanted):
    return any(_status_address(text) == wanted for text in texts)


FRONTEND_USES = frozenset({"FRONTEND", "FRONTEND_AND_BACKEND"})


def _frontend_details(status):
    """The details of each frontend device of each node in a /v3/network/status answer.

    7.11.0 answers a device's details under network_details, with use_for; its OpenAPI document names them
    api_network_specific_fields, with type, which is read for a build that answers so (qumulo-findings item 37). The
    first of the two that says the device's use is read, since /v1/network/status gives link statistics under
    network_details.
    """
    for node in status:
        for device in node.get("devices") or []:
            for key in ("network_details", "api_network_specific_fields"):
                details = device.get(key) or {}
                use = details.get("use_for") or details.get("type")
                if use:
                    if use in FRONTEND_USES:
                        yield details
                    break


def _shown_as(status, wanted):
    """How status shows wanted on a frontend device: the assigned_by of the qumulo_ip_addresses entry that lists it as
    its node or floating address, or HOST for one of the device's host_routable_ip_addresses; None when it shows nowhere.
    """
    for details in _frontend_details(status):
        for entry in details.get("qumulo_ip_addresses") or []:
            if _lists([entry.get("address") or "", *(entry.get("floating_addresses") or [])], wanted):
                return entry.get("assigned_by") or ""
        if _lists(details.get("host_routable_ip_addresses") or [], wanted):
            return "HOST"
    return None


def _of_kind(document, kind):
    """The ids of a canonical document's frontend networks of type kind, off the backend bond."""
    backend = backend_networks(document)
    return [network["id"] for network in document["frontend_networks"] if network["type"] == kind and network["id"] not in backend]


def _only(document, kind):
    """The id of a canonical document's one frontend network of type kind, off the backend bond; None for none or two."""
    found = _of_kind(document, kind)
    return found[0] if len(found) == 1 else None


def carrying(status, document, address):
    """The id of the network of a canonical document that carries address, by a /v3/network/status answer; or None.

    Status must show address on a frontend device. The entry's network_id is never read, since it is not the
    network's id in the document on hardware (qumulo-findings item 37), so the document names the network: the one
    whose node or floating ranges hold address; else, for an address a DHCP entry lists, the document's one DHCP network
    off the backend bond; else, for an address a HOST entry lists or one of the device's host_routable_ip_addresses, its
    one HOST network off the backend bond. With none or two networks of that type, status cannot tell which carries
    address. Whether that HOST network serves is carries_host_network's question.
    """
    shown = _shown_as(status, ipaddress.ip_address(address))
    if shown is None:
        return None
    held = holding(document, address)
    if held is not None:
        return held["id"]
    return _only(document, shown) if shown in ("DHCP", "HOST") else None


def carries_host_network(status):
    """Whether a frontend device of any node carries a HOST network in a /v3/network/status answer.

    It does when it lists a qumulo_ip_addresses entry assigned by HOST, as the VM edge's interface did once its HOST
    network applied. The host's addresses under host_routable_ip_addresses show no HOST network: a host-managed node
    lists them with no HOST network at all. The entry's network_id is not read (qumulo-findings item 37), so status
    cannot show which HOST network serves, nor one off its interface while another serves.
    """
    return any(entry.get("assigned_by") == "HOST" for details in _frontend_details(status) for entry in details.get("qumulo_ip_addresses") or [])


def _untold(status, document, address):
    """For an address status shows on a frontend device that no range holds: (its assignment, the document's two or
    more networks of that type), when status cannot tell which carries it; else None.
    """
    shown = _shown_as(status, ipaddress.ip_address(address))
    if shown not in ("DHCP", "HOST") or holding(document, address) is not None:
        return None
    found = _of_kind(document, shown)
    return (shown, found) if len(found) > 1 else None


def backend_networks(document):
    """The ids of the networks a wire or canonical document's backend bond lists."""
    interfaces = document.get("managed_interfaces") or {}
    return set((interfaces.get("backend_bond_config") or {}).get("networks") or [])


def _tenant_in(document, network_id):
    """The tenant id a canonical document gives a network, or None when it gives none or lacks the network."""
    return next((network["tenant_id"] for network in document["frontend_networks"] if network["id"] == network_id), None)


def named_document(document, name_of, id_of=lambda tenant_id: tenant_id):
    """A canonical document whose networks carry tenant, the name, before tenant_id.

    A backend-bond network's tenant is null: the cluster puts one in the only tenant, but the network takes no tenant,
    so a document read back can be given to network_config again (spec 014, FR-013, FR-031).
    """
    backend = backend_networks(document)
    networks = []
    for network in document["frontend_networks"]:
        named = {}
        for key, value in network.items():
            if key == "tenant_id":
                named["tenant"] = None if network["id"] in backend else name_of(value)
                named["tenant_id"] = id_of(value)
            else:
                named[key] = value
        networks.append(named)
    return {**document, "frontend_networks": networks}


def bindings(document, tenants, name_of):
    """Network id to tenant name in a canonical document; a network left to the server goes to the only one of tenants."""
    only = next(iter(tenants)) if len(tenants) == 1 else None
    return {network["id"]: only if network["tenant_id"] is None else name_of(network["tenant_id"]) for network in document["frontend_networks"]}


def named_network(network_id, names):
    """A network id with its name from names, an id to name map, when it has one: 2 (finance)."""
    return f"{network_id} ({names[network_id]})" if network_id in names else str(network_id)


def interface_tenants(document, name_of):
    """Each frontend interface of a canonical document, as a name, to its tenants' names and their networks on it."""
    tenant_of = {network["id"]: network["tenant_id"] for network in document["frontend_networks"]}
    interfaces = document["managed_interfaces"] or {}
    bond = interfaces.get("frontend_bond_config") or {}
    checks = [(f"the frontend bond {bond.get('interface_name', '')}".rstrip(), bond.get("networks") or [])]
    checks += [(f"VLAN {vlan['vlan_id']}", [vlan.get("network_id"), vlan.get("secondary_network_id")]) for vlan in interfaces.get("frontend_vlans") or []]
    out = {}
    for interface, network_ids in checks:
        held = {}
        for network_id in network_ids:
            tenant = name_of(tenant_of.get(network_id))
            if network_id is not None and tenant is not None:
                held.setdefault(tenant, []).append(network_id)
        out[interface] = held
    return out


def check_interfaces(before, after, name_of, warnings):
    """Spec 014, FR-013: refuse a canonical document, after, that gives two tenants networks on one interface or VLAN.

    A conflict that before already holds, with the same tenants and networks, adds a warning to warnings instead.
    """
    names = {network["id"]: network["name"] for network in after["frontend_networks"]}
    held_after, held_before = interface_tenants(after, name_of), interface_tenants(before, name_of)
    for interface, held in held_after.items():
        if len(held) <= 1:
            continue
        each = continuity.listed(f"{named_network(ids[0], names)} in tenant '{tenant}'" for tenant, ids in sorted(held.items()))
        if held == held_before.get(interface):
            # Another writer put them there; a task that leaves that interface alone may still write.
            warnings.append(f"{interface} already carries {each}, but the networks of one interface or VLAN belong to one tenant")
            continue
        raise QumuloError(
            f"{interface} carries {each}, but the networks of one interface or VLAN belong to one tenant. Put each tenant's "
            "networks on an interface or VLAN of their own"
        )


def _gateway_networks(document):
    """IP version to the ids of the networks of a canonical document that carry a default gateway, in id order."""
    out = {}
    for item in document["frontend_networks"]:
        if item["type"] == "STATIC" and item["default_gateway"]:  # only a STATIC network has a gateway
            out.setdefault(6 if ":" in item["default_gateway"] else 4, []).append(item["id"])
    return out


def gateway_refusals(before, after, warnings):
    """(message, network ids) for each IP version of which two or more STATIC networks of a canonical document, after, carry a default gateway.

    The cluster takes one IPv4 and one IPv6 default gateway, and refuses a second on chassis nodes (qumulo-findings
    item 35). A client cannot tell a chassis cluster from a VM or cloud one, so the rule holds for every cluster;
    networking the host manages has no STATIC network. When before already gives each of those networks a gateway of
    that version, the cluster took them, so a warning goes to warnings instead: a task that does not own networks could
    not remove one.
    """
    names = {network["id"]: network["name"] for network in after["frontend_networks"]}
    held = _gateway_networks(before)
    refusals = []
    for version, ids in sorted(_gateway_networks(after).items()):
        if len(ids) < 2:
            continue
        each = continuity.listed(named_network(network_id, names) for network_id in ids)
        if set(ids) <= set(held.get(version, ())):
            warnings.append(
                f"Networks {each} each carry an IPv{version} default gateway, which a chassis cluster refuses; the cluster already "
                'holds them, so they are kept. To leave the gateway on one network, set default_gateway: "" on the others.'
            )
            continue
        message = (
            f"Networks {each} each carry an IPv{version} default gateway, but a cluster on chassis nodes takes one IPv{version} "
            "default gateway, and the module refuses a second on every cluster. Keep the gateway on one network and set "
            'default_gateway: "" on the others; a new network left without default_gateway has none.'
        )
        refusals.append((message, ids))
    return refusals


def check_api_host_kept(params, before, after, in_role=False):
    """Refuse a canonical document, after, that drops the literal address in params' api_host from before's ranges.

    params holds api_host, allow_management_disruption and api_host_after, as network_config's options name them; the
    flag with api_host_after lets the document through. in_role words the refusal for the cluster_baseline role's step
    2, which judges step 3's document, with the inventory's variables.
    """
    host = params["api_host"]
    address = literal_address(host)
    if address is None:
        return  # a name: whether it still resolves to the cluster cannot be told from the document
    if holding(before, address) is None or holding(after, address) is not None:
        return
    if in_role:
        change = f"Step 3's network document would drop {host}, the address this play connects to"
        later = "the address the play connects to after step 3"
        if not params.get("allow_management_disruption"):
            raise QumuloError(
                f"{change}, which would cut this play off from the cluster. Set qumulo_allow_management_disruption: true, and set "
                f"qumulo_api_host_after to {later}."
            )
        if not params.get("api_host_after"):
            raise QumuloError(f"{change}. Set qumulo_api_host_after to {later}.")
        return
    change = f"the new network document would drop {host}, the address this task connects to"
    require_continuity(params, change, "set api_host_after to the address later tasks connect to")
    if not params.get("api_host_after"):
        raise QumuloError(f"{change}; set api_host_after to the address later tasks connect to")


class NetworkConfig:
    """network_config: one read of the whole document, and one PUT only when it differs (QUIRK-002)."""

    RETURN_KEY = "network_config"

    def __init__(self, module, client):
        self.module, self.client, self.params = module, client, module.params
        self.owned = self._owned_parts()
        if self.params.get("managed_interfaces") is not None and "interfaces" not in self.owned:
            raise QumuloError("managed_interfaces is in interfaces, which this task does not own; add it to owned_parts")
        self.wanted = wanted_networks(self.params.get("frontend_networks"), self.owned)
        self.notes = []
        self.tenants = None  # the tenant list, read only when the task will write or names a tenant

    def _owned_parts(self):
        terraform = self.params["managed_by"] == "terraform_provider"
        given = self.params.get("owned_parts")
        owned = set(given) if given is not None else set(PARTS) - ({"floating_ips"} if terraform else set())
        if terraform and "floating_ips" in owned and not self.params["take_over_from_terraform"]:
            raise QumuloError(
                "floating_ips belong to the Terraform provider on this cluster, which applies its own on every run; "
                "set take_over_from_terraform: true to manage them here anyway"
            )
        return owned

    def desired(self, live):
        """The wire document the task asks for, built over the live one so that parts it does not own are kept."""
        return planned_document(live, self.wanted, self.params.get("managed_interfaces"), self.owned)

    def run(self):
        response = self.client.get(NETWORK)
        if self.wanted is not None and any(entry.get("tenant") is not None or entry.get("tenant_id") is not None for entry in self.wanted):
            self._resolve_tenants(response.data)
        before = canonical_document(response.data)
        document = self.desired(response.data)
        after = canonical_document(document)
        changed = not same_document(after, before)
        result = {}
        if not changed:
            after = before
        else:
            refusals = gateway_refusals(before, after, self.client.warnings)
            if refusals:
                raise QumuloError(" ".join(message for message, _ids in refusals))
            self._read_tenants()
            tenants = self._tenants_after()
            rebound = self._check_tenancy(before, after, document, tenants)
            self._check_continuity(before, after, tenants)
            routes = f"this network change binds {rebound}, which removes the static routes of every node (G-TENANT-ROUTES)"
            if self.module.check_mode:
                self._validate(document)
            else:
                try:
                    changed, after = self._write(response, result)
                except QumuloError as e:
                    if rebound and e.details.get("changed"):  # the PUT went out, though its read-back did not confirm it
                        self.client.warnings.append(routes)
                    raise
            # After the write, so a refused one warns of nothing, and one a re-read found already made sends nothing.
            if rebound and changed:
                self.client.warnings.append(routes)
        result["changed"] = changed
        result[self.RETURN_KEY] = self._named(after)
        if self.notes:
            result["notes"] = self.notes
        if self.module._diff:
            result["diff"] = {"before": self._named(before), "after": result[self.RETURN_KEY]}
        return result

    def _read_tenants(self):
        if self.tenants is None:
            from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy  # it imports this module

            self.tenants = tenancy.tenant_ids(self.client)
        return self.tenants

    def _resolve_tenants(self, live):
        """Each entry's tenant, by name, as the id the document carries; a deprecated tenant_id is checked against the list."""
        from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy  # it imports this module

        ids = self._read_tenants()
        backend = backend_networks(desired_document(live, self.wanted, self.params.get("managed_interfaces"), self.owned))
        for index, entry in enumerate(self.wanted):
            where = f"frontend_networks[{index}]"
            name, given_id = entry.get("tenant"), entry.get("tenant_id")
            if entry["id"] in backend:
                if name is not None:
                    raise QumuloError(
                        f"{where}: network {entry['id']} is on the backend bond, and a backend network takes no tenant: "
                        "with two tenants a tenant network without a frontend interface aborts every node "
                        "(G-TENANT-BACKEND). Leave tenant out"
                    )
                if given_id is not None:
                    self.client.warnings.append(
                        f"{where}: network {entry['id']} is on the backend bond, which takes no tenant, so tenant_id {given_id} is dropped"
                    )
                entry["tenant_id"] = None
                continue
            if name is not None:
                resolved = tenancy.resolve(self.client, name, self.module.check_mode)
                note = tenancy.pending_note(name)
                if isinstance(resolved, Pending) and note not in self.notes:
                    self.notes.append(note)
                if given_id is not None and resolved != given_id:
                    raise QumuloError(
                        f"{where} names tenant '{name}' and tenant_id {given_id}, which is "
                        f"{repr(ids.by_id[given_id]).join(('tenant ', '')) if given_id in ids.by_id else 'no tenant on the cluster'}; give tenant alone"
                    )
                entry["tenant_id"] = resolved
            elif given_id is not None and given_id not in ids.by_id:
                self.client.warnings.append(
                    f"{where}: tenant_id {given_id} is no tenant on the cluster, since tenant ids are never reused, so it is "
                    "dropped and the network keeps the tenant the cluster reports; name its tenant with tenant"
                )
                entry["tenant_id"] = None

    def _tenant_name(self, tenant_id):
        if isinstance(tenant_id, Pending):
            return str(tenant_id)
        return None if tenant_id is None or self.tenants is None else self.tenants.by_id.get(tenant_id)

    def _named(self, document):
        """A canonical document with each network's tenant by name beside its tenant_id: null where the run read no tenant list."""
        return named_document(document, self._tenant_name, lambda tenant_id: None if isinstance(tenant_id, Pending) else tenant_id)

    def _tenants_after(self):
        """The tenants' switches by name as they will be immediately after the write: the role's plan in check mode."""
        from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy  # it imports this module

        planned = self.params.get("planned_tenants")
        if self.module.check_mode and planned is not None:
            needs = continuity.rest_switches(self.params.get("cluster_port"), self.params["api_port"])
            return {name: dict.fromkeys(needs, boolean(enabled)) for name, enabled in planned.items()}
        return {entry["name"]: entry for entry in tenancy.tenant_entries(self.client)}

    def _check_tenancy(self, before, after, document, tenants):
        """Spec 014's refusals on the document the task would write: FR-012's untenanted network and FR-013's interfaces.

        tenants are the tenants after the write, so with the role's plan a run creating the second tenant counts two.
        It returns the bindings the write changes, as text for the static routes warning, or "" for none.
        """
        backend = backend_networks(document)
        names = {network["id"]: network["name"] for network in after["frontend_networks"]}
        existing = {network["id"] for network in before["frontend_networks"]}
        if len(tenants) > 1:
            for network in after["frontend_networks"]:
                if network["id"] not in existing and network["tenant_id"] is None and network["id"] not in backend:
                    raise QumuloError(
                        f"network {network['id']} ({network['name']}) is new and names no tenant, and with more than one "
                        "tenant the cluster would hold it in no tenant, so it would serve no client. Give it a tenant"
                    )
        check_interfaces(before, after, self._tenant_name, self.client.warnings)
        rebound = [
            f"network {named_network(n['id'], names)} to tenant '{self._tenant_name(n['tenant_id'])}'"
            for n in after["frontend_networks"]
            if n["tenant_id"] is not None and n["tenant_id"] != _tenant_in(before, n["id"])
        ]
        return continuity.listed(rebound) if rebound else ""

    def _check_continuity(self, before, after, tenants):
        if not self.params["allow_management_disruption"]:
            self._judge(before, after, dict(tenants))
        check_api_host_kept(self.params, before, after)

    def _judge(self, before, after, tenants):
        """Spec 014's continuity rule on this task's own document: its PUT is a judged write."""
        from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy  # it imports this module

        cluster_port, api_port = self.params.get("cluster_port"), self.params["api_port"]
        bound = bindings(after, tenants, self._tenant_name)
        live = {entry["name"]: entry for entry in tenancy.tenant_entries(self.client)}
        for name in sorted({name for name in bound.values() if name is not None and name not in tenants}):
            if name in live:
                # A cluster tenant the role's plan leaves out, as one renamed earlier in the run: judged as it is now.
                tenants[name] = live[name]
                continue
            # A tenant an earlier task creates, whose services only the role's planned_tenants can tell; run alone,
            # the check takes them as it predicts the write, and the run judges them.
            tenants[name] = dict.fromkeys(continuity.rest_switches(cluster_port, api_port), True)
            created = tenancy.pending_note(name)
            judged = f"{created}, and the check does not judge its REST switches; the run does"
            self.notes = [judged if note == created else note for note in self.notes]
            if judged not in self.notes:
                self.notes.append(judged)
        declared = self.params.get("management_network")
        declared = None if declared is None else set(declared)
        _management, message = continuity.verdict(
            write="this network change",
            in_role=False,
            api_host=self.params["api_host"],
            declared=declared,
            tenants=tenants,
            bindings=bound,
            cluster_port=cluster_port,
            api_port=api_port,
            documents=lambda: (before, after),
            addresses=lambda: continuity.api_host_addresses(self.params["api_host"]),
            status=continuity.status_on_demand(declared, lambda: read_status(self.client), "network_config"),
        )
        if message:
            raise QumuloError(message)

    def _validate(self, document):
        if not self.params["validate"]:
            return
        try:
            self.client.put(NETWORK_VALIDATE, body=sendable(document))
        except QumuloApiError as e:
            raise QumuloError(f"the cluster refuses this network document: {e}") from None

    def _write(self, response, result):
        """(changed, the document after); a 412 re-read that already holds the wanted document writes nothing."""
        last = {}

        def build(data):
            document = self.desired(data)
            last["want"], last["have"] = canonical_document(document), canonical_document(data)
            return None if same_document(last["want"], last["have"]) else sendable(document)

        try:
            written = self.client.read_modify_write(NETWORK, build, method="PUT", first=response)
        except QumuloMaybeApplied as lost:
            self.notes.append("write reconciled after a lost connection")
            self._move_connection(result)
            return True, self._settle(last["want"], lost)
        self._move_connection(result)
        if written is None:
            return False, last["have"]
        return True, self._settle(last["want"])

    def _move_connection(self, result):
        after = self.params.get("api_host_after")
        if after:
            self.client.base = base_url(after, self.params["api_port"])
            result["api_host"] = after

    def _settle(self, want, lost=None):
        observed = canonical_document(self.client.get(NETWORK).data)
        if not same_document(want, observed):
            cause = f"{lost}; reading back did not confirm it" if lost else "the network document did not converge"
            raise QumuloError(cause, changed=True, expected=want, observed=observed)
        return observed


AUTHORITATIVE_DNS = Route(
    "/v1/authoritative-dns/settings",
    ("GET", "PATCH"),
    etag="omit",
    why="once the settings were written, the GET ETag never matches what PATCH checks, so an If-Match from it always fails 412 (QUIRK-024)",
)
FQDN_AREA = "dns.fqdn"  # the part of the dns area the Terraform provider can own: the name, and whether the server answers
FQDN_OPTIONS = ("enabled", "fqdn")
MAX_RECORD_TTL = 3600
DNS_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
MAX_NAME = 253


def _mapped_range(text):
    """Whether text is a range or block whose first address is IPv4-mapped, a form whose stored shape is not recorded."""
    first = re.split(r"[-/]", text.strip(), maxsplit=1)
    if len(first) < 2:
        return False
    try:
        return getattr(ipaddress.ip_address(first[0]), "ipv4_mapped", None) is not None
    except ValueError:
        return False


def canonical_host_restrictions(ranges):
    """Client ranges merged and sorted, written first-last, so that two lists the cluster stores alike compare equal."""
    return canonical_ranges([_unmapped(text) for text in ranges])


def fully_qualified(name):
    """name with the final dot the cluster stores; the empty name of a cluster that has never had one stays empty."""
    return name if not name or name.endswith(".") else name + "."


class AuthoritativeDns(Settings):
    RETURN_KEY = "authoritative_dns"
    ROUTE = AUTHORITATIVE_DNS
    FIELDS: ClassVar[dict[str, Field]] = {
        "enabled": Field("enabled"),
        "fqdn": Field("fqdn"),
        "host_restrictions": Field("host_restrictions", read=canonical_host_restrictions),
        "record_ttl_seconds": Field("record_ttl_seconds"),
    }

    def __init__(self, module, client):
        super().__init__(module, client)
        # Every check runs before any request, so that a refusal names the option rather than the server's error.
        self._check_ownership()
        self.fqdn = self._fqdn(self.params.get("fqdn"))
        self.host_restrictions = self._host_restrictions(self.params.get("host_restrictions"))
        ttl = self.params.get("record_ttl_seconds")
        if ttl is not None and not 0 <= ttl <= MAX_RECORD_TTL:
            raise QumuloError(f"record_ttl_seconds: {ttl} is not between 0 and {MAX_RECORD_TTL}")

    def _check_ownership(self):
        if FQDN_AREA not in self.params["terraform_owned_areas"] or self.params["take_over_from_terraform"]:
            return
        for option in FQDN_OPTIONS:
            if self.params.get(option) is not None:
                raise QumuloError(
                    f"{option} is in {FQDN_AREA}, which Terraform owns on this cluster and applies again on every run; "
                    "set take_over_from_terraform: true to manage it here anyway"
                )

    @staticmethod
    def _fqdn(name):
        """The name with its final dot; "" stays "", which desired() takes only where the cluster has no name."""
        if not name:
            return name
        bare = name.removesuffix(".")
        if len(bare) > MAX_NAME or not all(DNS_LABEL.match(label) for label in bare.split(".")):
            raise QumuloError(
                f"fqdn: '{name}' is not a DNS name; each label between dots takes 1 to 63 of the characters a-z, A-Z, 0-9 "
                f"and -, neither starting nor ending with -, and the name at most {MAX_NAME}"
            )
        return fully_qualified(name)

    @staticmethod
    def _host_restrictions(ranges):
        if ranges is None:
            return None
        for text in ranges:
            if _mapped_range(text):
                raise QumuloError(f"host_restrictions: '{text}' is an IPv4-mapped range; write it as IPv4")
            try:
                _interval(_unmapped(text), False)
            except ValueError:
                raise QumuloError(f"host_restrictions: '{text}' is not an address range") from None
        return canonical_host_restrictions(ranges)

    def desired(self, before):
        after = super().desired(before)
        if self.fqdn == "" and before.get("fqdn"):
            raise QumuloError(
                f'fqdn: "" asks to clear the name {before["fqdn"]}, which the cluster cannot do once a name is set '
                "(G-DNS-FQDN-CLEAR); leave fqdn out, or declare that name, and if Terraform set it, list dns.fqdn in "
                "qumulo_terraform_owned_areas"
            )
        if self.fqdn is not None:
            after["fqdn"] = self.fqdn
        if self.host_restrictions is not None:
            after["host_restrictions"] = self.host_restrictions
        if self.params.get("enabled") and not after.get("fqdn"):
            raise QumuloError("enabled: true needs fqdn, since the cluster has no name to answer for")
        return after


INTERFACES = Route("/v2/network/interfaces/", ("GET",))
INTERFACE_NETWORKS = Route("/v2/network/interfaces/{interface_id}/networks/", ("GET",))
LEGACY_SETTINGS = Route("/v1/network/settings", ("GET",))
RESOLVE_NAMES = Route("/v1/dns/resolve-names-to-ips", ("POST",), replay=frozenset({"POST"}), why=READ_ONLY_WHY)
RESOLVE_ADDRESSES = Route("/v1/dns/resolve-ips-to-names", ("POST",), replay=frozenset({"POST"}), why=READ_ONLY_WHY)


def _network_document(client):
    """The canonical /v3/network document, read once per module run for the document and api_host_network subsets."""
    if "network_document" not in client.memo:
        client.memo["network_document"] = canonical_document(_read(client, NETWORK))
    return client.memo["network_document"]


def _document_subset(client):
    """The canonical document, with each network's tenant by name beside its tenant_id when the tenant list answers."""
    from ansible_collections.qumulo.cluster.plugins.module_utils import tenancy  # it imports this module

    document = _network_document(client)
    try:
        names = {entry["id"]: entry["name"] for entry in tenancy.tenant_entries(client)}
    except QumuloApiError as e:
        if e.status == 403:
            client.warnings.append(f"the document's tenant names are null: reading the tenant list needs PRIVILEGE_TENANT_READ ({e.error_class})")
        elif e.status not in NOT_OFFERED_STATUS:
            raise
        names = {}
    return {"document": named_document(document, names.get)}


def _status(client):
    _require_configured(client)
    status = read_status(client)
    if isinstance(status, Unavailable):
        raise SubsetUnavailable(status.reason)
    return status


def _status_subset(client):
    return {"status": _status(client)}


def _api_host_network_subset(client):
    """The document's id of the network that carries api_host by the status, for an operator; never a guess (G-NET-APPLY)."""
    host = client.params["api_host"]
    address = literal_address(host)
    if address is None:
        raise SubsetUnavailable(f"api_host {host} is a name, which the status cannot place; give its address, or read status")
    status = _status(client)
    document = _network_document(client)
    carrier = carrying(status, document, address)
    if carrier is None and not document["frontend_networks"]:
        raise SubsetUnavailable(f"no network of the cluster carries {address}: the cluster has no frontend network yet")
    untold = _untold(status, document, address) if carrier is None else None
    if untold is not None:
        kind, found = untold
        raise SubsetUnavailable(
            f"no network of the cluster carries {address} by the status: it shows the address as {kind}, and the network "
            f"document has {len(found)} {kind} networks, which the status does not tell apart"
        )
    if carrier is None:
        raise SubsetUnavailable(f"no network of the cluster carries {address}, as when the play reaches it through NAT or a load balancer")
    return {"api_host_network": carrier}


def _interfaces_subset(client):
    interfaces = _read(client, INTERFACES)
    return {"interfaces": [{**i, "networks": get_offered(client, INTERFACE_NETWORKS, params={"interface_id": i["id"]})} for i in interfaces]}


# gather_subset name -> function of the client returning a dict of that subset's facts, in gathering order. Each reads
# through cluster._read, so that on an UNCONFIGURED node every subset is unavailable rather than the module failing.
NETWORK_INFO_SUBSETS = {
    "document": _document_subset,
    "interfaces": _interfaces_subset,
    "legacy_settings": lambda client: {"legacy_settings": _read(client, LEGACY_SETTINGS)},
    "dns": lambda client: {"dns": DnsSettings.options(_read(client, DNS))},
    "dns_lookup_overrides": lambda client: {"dns_lookup_overrides": DnsLookupOverrides.options(_read(client, LOOKUP_OVERRIDES))},
    "authoritative_dns": lambda client: {"authoritative_dns": AuthoritativeDns.options(_read(client, AUTHORITATIVE_DNS))},
    "status": _status_subset,
    "api_host_network": _api_host_network_subset,
}


def _lookup(client, route, body):
    _require_configured(client)
    return client.post(route, body=body).data


def network_lookups(names, addresses):
    """The lookups network_info was asked for, as more subsets: name -> function of the client returning its facts."""
    lookups = {}
    if names:
        lookups["resolved_names"] = lambda client: {"resolved_names": _lookup(client, RESOLVE_NAMES, names)}
    if addresses:
        lookups["resolved_addresses"] = lambda client: {"resolved_addresses": _lookup(client, RESOLVE_ADDRESSES, addresses)}
    return lookups
