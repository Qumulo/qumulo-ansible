# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Management continuity: changes that can cut a play off from the cluster it is configuring.

A module that would make such a change refuses it unless the task sets allow_management_disruption,
and says what the play must supply to reach the cluster afterwards.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import NamedTuple

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError, Route

WHO_AM_I = Route("/v1/session/who-am-i", ("GET",))


def _who_am_i(client):
    if "who_am_i" not in client.memo:
        client.memo["who_am_i"] = client.get(WHO_AM_I).data
    return client.memo["who_am_i"]


def connecting_user(client):
    """The name of the user this module run connects as: api_username, or, with an access token, who-am-i's answer."""
    if not client.params.get("api_token"):
        return client.params["api_username"]
    return _who_am_i(client)["name"]


def connection_token_id(client):
    """The id of the access token this module run connects with, from who-am-i; None when it connects with a password."""
    if not client.params.get("api_token"):
        return None
    return _who_am_i(client).get("access_token_id")


def require_continuity(params, change, then):
    """Refuse change unless allow_management_disruption is set; then says what the play must do afterwards."""
    if params.get("allow_management_disruption"):
        return
    raise QumuloError(f"{change}, which would cut this play off from the cluster; set allow_management_disruption: true, and {then}")


# Spec 014's continuity rule: which network carries the play's connection, and whether a tenant write or a network PUT
# would leave it in a tenant without REST enabled.

NO_ADDRESS, NO_NETWORK, STATUS_UNAVAILABLE, OUTSIDE = "no address", "no network", "status unavailable", "outside the document"


class Unavailable(NamedTuple):
    """Network status that could not be read: an HTTP 500, as the container answers (G-NET-APPLY), or a status not offered."""

    reason: str


class NotRead(NamedTuple):
    """Network status the caller did not read, which the rule must then never need."""

    caller: str


class Management(NamedTuple):
    """The management network: network ids within the document in force after the write, or why it is undetermined."""

    networks: frozenset
    undetermined: str | None = None  # NO_ADDRESS, NO_NETWORK, STATUS_UNAVAILABLE or OUTSIDE
    outside: tuple = ()  # the declared ids the document lacks


class OffDevice(NamedTuple):
    """A HOST network of the management network that status shows on no device."""

    network: int
    in_current: bool  # whether the current document has it, which picks message 6's text


class Breach(NamedTuple):
    """A network of the management network, or the only tenant, that would leave REST unreachable after the write."""

    tenant: str | None  # None: a network no tenant holds
    network: int | None  # None with one tenant, where the network is never looked up
    off: tuple = ()  # the switches REST enabled needs that are off
    fail_closed: bool = False  # client port 443 with no cluster port declared, where both switches are needed


def _held(document, address):
    from ansible_collections.qumulo.cluster.plugins.module_utils import network  # it imports this module

    return network.holding(document, address)


def _carried(status, document, address):
    from ansible_collections.qumulo.cluster.plugins.module_utils import network  # it imports this module

    return network.carrying(status, document, address)


def _host_served(status):
    from ansible_collections.qumulo.cluster.plugins.module_utils import network  # it imports this module

    return network.carries_host_network(status)


def _ids(document):
    return {network["id"] for network in document["frontend_networks"]}


def _not_read(status, need):
    """The internal error of a rule that needs status a caller did not read; status read or unavailable passes."""
    if isinstance(status, NotRead):
        raise QumuloError(
            f"internal error: {status.caller} did not read the cluster's network status, which the continuity rule needs "
            f"to {need}; report this as a bug in the collection"
        )


def status_needed(declared, document, addresses):
    """Whether a module run alone must read status: no declared set, and an address on no network's ranges or on a HOST one.

    It answers for a judged write that leaves two or more tenants; with one tenant after the write, which needed tells,
    the management network is never looked up and status is never read.
    """
    if declared is not None:
        return False
    for address in addresses:
        held = _held(document, address)
        if held is None or held["type"] == "HOST":
            return True
    return False


def management_network(declared, document, status, addresses, planned_ids=frozenset(), *, current):
    """The management network within the canonical document in force after the write.

    The declared ids when declared is set; otherwise, for each address api_host resolves to, the network whose ranges
    hold it, or for an address no range holds, the one status places it on; a HOST network's floating address is that
    network, which hosts_on_device then checks serves. status is the /v3/network/status answer, an Unavailable or a
    NotRead; an address that needs it while it is unavailable leaves the set undetermined. Status shows the cluster as
    it is now, so the network it places an address on is named from current, the current canonical document, and counts
    only if document has it.
    planned_ids are the networks the role's plan puts on the document in force that the document read may lack, which
    a declared id may name: a writer's check-mode run reads the live document, which step 3 has not yet written.
    """
    in_document = _ids(document)
    if declared is not None and not declared:
        raise QumuloError("the management network is declared with no network; declare the id of the network this play reaches the cluster on")
    if declared is not None:
        known = in_document | set(planned_ids)
        outside = tuple(sorted(set(declared) - known))
        return Management(frozenset(set(declared) & known), OUTSIDE if outside else None, outside)
    if not addresses:
        return Management(frozenset(), NO_ADDRESS)
    found, unplaced = set(), False
    for address in addresses:
        held = _held(document, address)
        if held is not None and held["type"] != "HOST":
            found.add(held["id"])
            continue
        _not_read(status, f"place {address}")
        if isinstance(status, Unavailable):
            unplaced = True
        elif held is not None:
            found.add(held["id"])  # a HOST network's floating address; hosts_on_device checks the network serves
        else:
            carrier = _carried(status, current, address)
            if carrier is not None and carrier in in_document:
                found.add(carrier)
    if unplaced:
        return Management(frozenset(found), STATUS_UNAVAILABLE)
    return Management(frozenset(found), None if found else NO_NETWORK)


def needed(tenants_after):
    """Whether the management network is looked up: only with two or more tenants after the write."""
    return len(tenants_after) >= 2


def hosts_on_device(networks, declared, document_in_force, current_document, status):
    """The HOST networks of the management network that status shows on no device of any node, sorted.

    A HOST network serves only on an interface the host associates with its id. Status's network ids are not read
    (qumulo-findings item 37), so a HOST network shows on a device when the current document has it and a frontend
    device of some node lists an entry assigned by HOST; the host's addresses alone show none, since a host-managed node
    lists them with no HOST network. A network the write adds is on no device yet, and with two HOST networks on the
    cluster, both show once one is served. A set with no HOST network needs no status. A declared set is trusted when status is unavailable or not
    read, as the operator's word. An undeclared HOST network with status unavailable is never asked about:
    management_network has marked the set undetermined, and refusal_message refuses it.
    """
    kinds = {network["id"]: network["type"] for network in document_in_force["frontend_networks"]}
    hosts = sorted(n for n in networks if kinds.get(n) == "HOST")
    if not hosts:
        return []
    if not isinstance(status, list):
        if declared is not None:
            return []
        _not_read(status, "check the HOST networks of the management network")
        raise QumuloError("internal error: the HOST check ran with the network status unavailable, for a management network that is then undetermined")
    serving = _host_served(status)
    current = _ids(current_document)
    return [OffDevice(n, n in current) for n in hosts if not (serving and n in current)]


def rest_switches(cluster_port, api_port):
    """The switches REST enabled needs at the cluster-side port; with none declared and client port 443, both."""
    if cluster_port is not None:
        return ("web_ui_enabled",) if cluster_port == 443 else ("rest_api_enabled",)
    return ("rest_api_enabled", "web_ui_enabled") if api_port == 443 else ("rest_api_enabled",)


def rest_reachable(tenants, bindings, networks, cluster_port, api_port):
    """What would leave REST unreachable after the write, as Breaches; none when the play keeps its connection.

    tenants maps each tenant's name after the write to its switches, bindings each network id to its tenant's name or
    None, and networks is the management network. With one tenant only that tenant is asked. Every tenant a network of
    the set is bound to must be a key of tenants, a tenant the run creates included, under its name.
    """
    needs = rest_switches(cluster_port, api_port)
    fail_closed = cluster_port is None and api_port == 443

    def off(name):
        if name not in tenants:
            raise QumuloError(
                f"internal error: network binding names tenant {name}, which the tenants after the write lack; report this as a bug in the collection"
            )
        return tuple(switch for switch in needs if not tenants[name].get(switch))

    if len(tenants) == 1:
        (only,) = tenants
        return [Breach(only, None, off(only), fail_closed)] if off(only) else []
    breaches = []
    for network_id in sorted(networks):
        name = bindings.get(network_id)
        if name is None:
            breaches.append(Breach(None, network_id))
        elif off(name):
            breaches.append(Breach(name, network_id, off(name), fail_closed))
    return breaches


def listed(items):
    """Items as an English list: "a", "a and b", "a, b and c"; "none" for no item."""
    items = list(items)
    if not items:
        return "none"
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _sentence(text):
    return text[:1].upper() + text[1:]


def no_tenant_left(in_role, tenant, entries):
    """The refusal of a write that leaves no tenant: tenant is the one a module deletes, entries those the run deletes."""
    if in_role:
        return f"This run would delete every tenant ({listed(entries)}). Remove state: absent from one of them."
    return f"Deleting tenant {tenant} would leave the cluster with no tenant."


def refusal_message(
    *,
    write,
    in_role,
    api_host,
    addresses,
    tenants,
    bindings,
    management,
    current,
    in_force,
    removed,
    off_device,
    breaches,
    planned=None,
    tenant=None,
    entries=(),
):
    """The refusal of the spec's Messages for a judged write, the first that applies in the spec's order; None for none.

    write describes the write, as "creating tenant finance". in_role picks the texts that give the role's variables over
    those for a module run alone. management, removed, off_device and breaches are what management_network,
    removed_networks, hosts_on_device and rest_reachable gave; current and in_force are canonical documents, planned the
    run's planned one when the write is judged before step 3, which name the networks. tenant is the tenant the write is
    on, and entries the tenants the run deletes, for a write that leaves no tenant.
    """
    variable = "qumulo_management_network" if in_role else "management_network"
    port_variable = "qumulo_cluster_port" if in_role else "cluster_port"
    documents = [d for d in (in_force, current, planned) if d is not None]

    def named(network_id):
        for document in documents:
            for network in document["frontend_networks"]:
                if network["id"] == network_id:
                    return f"{network_id} ({network['name']})"
        return str(network_id)

    if not tenants:
        return no_tenant_left(in_role, tenant, entries)
    unbound = [breach.network for breach in breaches if breach.tenant is None]
    if needed(tenants) and (management.undetermined or unbound or off_device):
        found = _unreachable(
            variable=variable,
            in_role=in_role,
            api_host=api_host,
            addresses=addresses,
            management=management,
            in_force=in_force,
            removed=removed,
            off_device=off_device,
            unbound=unbound,
            planned=planned,
            tenant=tenant,
            named=named,
        )
        if found:
            return found
    for breach in breaches:
        if breach.tenant is None:
            continue
        switch = " and ".join(breach.off)
        switch_true = " and ".join(f"{s}: true" for s in breach.off)
        if breach.fail_closed and len(breach.off) == 1:
            return (
                f"{_sentence(write)} would leave tenant {breach.tenant}, which holds this play's connection, with {switch} off. "
                "This play connects on port 443 and declares no cluster port, so the collection cannot tell whether it "
                "reaches the REST listener or the web UI listener, and needs both on. If nothing between this play and the "
                f"cluster changes the port, set {port_variable}: 443; if a load balancer maps 443 to another port, set "
                f"{port_variable} to that port."
            )
        if breach.network is None:
            return (
                f"{_sentence(write)} would leave tenant {breach.tenant}, the only tenant, with {switch} off, and this play "
                f"would lose its connection. Set {switch_true} on {breach.tenant}."
            )
        network = named(breach.network)
        return (
            f"{_sentence(write)} would leave network {network} in tenant {breach.tenant} with {switch} off, and this play "
            f"would lose its connection. Set {switch_true} on {breach.tenant}, or list {network} under a tenant that has "
            f"{switch_true}."
        )
    return None


def verdict(
    *,
    write,
    in_role,
    api_host,
    declared,
    tenants,
    bindings,
    cluster_port,
    api_port,
    documents,
    addresses,
    status,
    planned=None,
    planned_ids=frozenset(),
    tenant=None,
):
    """The continuity rule on one judged write: (the management network after it, or None; the refusal, or None).

    The sequence every writer and tenancy_plan run, from needed to refusal_message. declared is the declared management
    network, or None; tenants, bindings and the two ports are rest_reachable's, and write, in_role, api_host, planned
    and tenant refusal_message's, and planned_ids management_network's. The management network is None when the write leaves fewer than two tenants, and the
    three callables are then never called, so such a write reads nothing: documents() gives the current and the
    in-force canonical documents, addresses() api_host's addresses, called only with no declared set, and
    status(in_force, addresses) the network status, an Unavailable or a NotRead.
    """
    management, off_device, removed, shown, current, in_force = None, [], [], [], None, None
    if needed(tenants):
        current, in_force = documents()
        shown = [] if declared is not None else addresses()
        found = status(in_force, shown)
        management = management_network(declared, in_force, found, shown, planned_ids, current=current)
        if management.undetermined is None:
            off_device = hosts_on_device(management.networks, declared, in_force, current, found)
        removed = removed_networks(declared, shown, current, in_force, found)
    networks = management.networks if management is not None else frozenset()
    message = refusal_message(
        write=write,
        in_role=in_role,
        api_host=api_host,
        addresses=shown,
        tenants=tenants,
        bindings=bindings,
        management=management,
        current=current,
        in_force=in_force,
        removed=removed,
        off_device=off_device,
        breaches=rest_reachable(tenants, bindings, networks, cluster_port, api_port),
        planned=planned,
        tenant=tenant,
    )
    return (None if management is None else management.networks), message


def status_on_demand(declared, read, caller):
    """verdict's status for a module run alone: read() only when status_needed says the rule needs it, else a NotRead."""
    return lambda document, addresses: read() if status_needed(declared, document, addresses) else NotRead(caller)


def removed_networks(declared, addresses, current, in_force, status):
    """The networks of the management network on the current document that the document in force lacks, sorted: message 2's.

    The set on the current document is the declared one, or each address's network by the current ranges, then by
    status when status was read. A network status places an address on that neither document has is not one the run
    removes.
    """
    if declared is not None:
        on_current = set(declared)
    else:
        on_current = set()
        for address in addresses:
            held = _held(current, address)
            if held is not None:
                on_current.add(held["id"])
            elif isinstance(status, list):
                on_current.add(_carried(status, current, address))
    return sorted((on_current & _ids(current)) - _ids(in_force))


def _unreachable(*, variable, in_role, api_host, addresses, management, in_force, removed, off_device, unbound, planned, tenant, named):
    """Messages 1 to 6: the management network undetermined, held by no tenant, or on no device."""
    if not in_force["managed_interfaces"] and not in_force["frontend_networks"]:
        first = "The cluster's networking is host-managed and it has no frontend network yet, so with a second tenant REST would listen on no network. "
        if in_role:
            return first + (
                "List its HOST network under one tenant in qumulo_tenants and run once before adding the second tenant, "
                "and, if this cluster's network status is unavailable, set qumulo_management_network to that network's id."
            )
        return first + (
            "Add the HOST network with network_config before creating the second tenant, and, if this cluster's network "
            "status is unavailable, pass its id as management_network."
        )
    if removed:
        first = f"This run removes network {named(removed[0])}, which carries this play's connection. "
        if in_role:
            return first + (
                "List it under the networks of a tenant in qumulo_tenants that is not state: absent, or set "
                "qumulo_allow_management_disruption: true and qumulo_api_host_after to the address the play uses after the run."
            )
        return first + "Keep it in the networks you pass, or set allow_management_disruption: true."
    if management.outside:
        network_id = management.outside[0]
        if planned is not None and network_id in _ids(planned):
            network = named(network_id)
            return (
                f"{variable} is {network}, which is not on the cluster. Add network {network} first, in a run whose inventory "
                f"does not yet list tenant {tenant}."
            )
        if in_role:
            return f"{variable} is {named(network_id)}, which is neither on the cluster nor in this inventory."
        return f"{variable} is {named(network_id)}, which is not on the cluster."
    if unbound:
        first = f"Network {named(unbound[0])} carries this play's connection but belongs to no tenant. "
        if in_role:
            return first + "List it under the networks of its tenant in qumulo_tenants."
        return first + f"Pass it with its tenant to network_config, or run qq network_mod_network --network-id {unbound[0]} --tenant-id <tenant id>."
    shown = ", ".join(addresses)
    if management.undetermined == NO_ADDRESS:
        return f"{api_host} resolves to no address. Set {variable} to the id of the network this play reaches the cluster on."
    if management.undetermined == NO_NETWORK:
        return (
            f"No network on the cluster carries {api_host} ({shown}). If this play reaches the cluster through NAT or a "
            f"load balancer, set {variable} to the id of the network {api_host} is forwarded to."
        )
    if management.undetermined == STATUS_UNAVAILABLE:
        return (
            f"The cluster's network status is unavailable (G-NET-APPLY), so the collection cannot tell which network "
            f"carries {api_host} ({shown}), or whether a HOST network serves it. Set {variable} to the id of the network "
            "this play reaches the cluster on."
        )
    for off in off_device:
        network = named(off.network)
        if not off.in_current:
            return (
                f"HOST network {network} is not on the cluster yet, so it cannot carry this play's connection. Add it in "
                f"a run that leaves {variable} on the network this play uses now, then set {variable} to {network} once "
                "status shows it on a device."
            )
        return (
            f"Status shows HOST network {network} on no device. A HOST network serves only on an interface the host "
            f"associates with its id, and a node reads that association when it starts. If {network} was just created, "
            "run again once status shows it applied; otherwise set its id to the one the host associates with an interface."
        )
    return None


def api_host_addresses(api_host):
    """The addresses api_host resolves to on the controller, where modules run: canonical, once each, IPv4 first.

    A literal address is its own address, an IPv6 address in a URL's brackets included, as the client takes it; a
    name that does not resolve gives none.
    """
    from ansible_collections.qumulo.cluster.plugins.module_utils import network  # it imports this module

    literal = network.literal_address(api_host)
    if literal is not None:
        return [literal]
    try:
        answers = socket.getaddrinfo(api_host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    found = set()
    for _family, _type, _proto, _canonname, sockaddr in answers:
        try:
            found.add(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    return [str(address) for address in sorted(found, key=lambda address: (address.version, address))]
