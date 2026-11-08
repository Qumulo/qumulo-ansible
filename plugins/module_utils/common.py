# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""Entry points every qumulo.cluster module's main() calls.

Option names: the connection credentials are api_username, api_password and api_token, so a resource
option may be called password; qumulo_module refuses any option that shadows a connection option.
An option whose name contains "password" but is not a secret, such as update_password, must set
no_log=False, or ansible-core warns on every run that no_log was not set.
"""

from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloClient, QumuloError, connection_argument_spec


class SubsetUnavailable(QumuloError):
    """An info subset this cluster cannot answer, such as a feature its product type lacks, or one the API user may not
    read, where a spec says so."""


def qumulo_module(spec, **kwargs):
    """An AnsibleModule with the connection options added and check mode supported."""
    connection = connection_argument_spec()
    shadowed = sorted(set(spec) & set(connection))
    if shadowed:
        raise ValueError(f"module options {', '.join(shadowed)} shadow qumulo.cluster connection options; rename them")
    return AnsibleModule(argument_spec={**connection, **spec}, supports_check_mode=True, **kwargs)


def run(module, work):
    """Exit with work(client)'s result, or fail with a QumuloError's message and details."""
    client = None
    try:
        client = QumuloClient(module.params)
        result = work(client)
    except QumuloError as e:
        _forward_warnings(module, client)
        module.fail_json(msg=str(e), **e.details)
    _forward_warnings(module, client)
    module.exit_json(**result)


def run_resource(module, cls):
    run(module, lambda client: cls(module, client).run())


def run_info(module, key, subsets):
    """subsets maps a gather_subset name to a function of the client that returns a dict of facts, merged under key.

    "all", the default, gathers every subset. Subsets run in the order subsets lists them. One that raises
    SubsetUnavailable is reported under unavailable_subsets with its reason, and the others are still gathered.
    """

    def gather(client):
        wanted = module.params.get("gather_subset") or ["all"]
        facts, unavailable, producer = {}, {}, {}
        for name, subset in subsets.items():
            if "all" not in wanted and name not in wanted:
                continue
            try:
                gathered = subset(client)
            except SubsetUnavailable as e:
                unavailable[name] = str(e)
                continue
            for fact in gathered:
                if fact in producer:
                    raise ValueError(f"subsets {producer[fact]} and {name} both produce {fact}")
                producer[fact] = name
            facts.update(gathered)
        result = {"changed": False, key: facts}
        if unavailable:
            result["unavailable_subsets"] = unavailable
        return result

    run(module, gather)


def _forward_warnings(module, client):
    for warning in client.warnings if client else ():
        module.warn(warning)
