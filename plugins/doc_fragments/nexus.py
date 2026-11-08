# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations


class ModuleDocFragment:
    # The Nexus options of the modules that call Qumulo's Nexus service, as nexus.nexus_argument_spec() defines them.
    DOCUMENTATION = r"""
options:
  nexus_api_token:
    description:
      - A Nexus user's API token, sent to Nexus as a bearer token. It is never sent to the cluster.
      - What the token may do depends on the user's role in the Nexus organization; each module says which calls it makes.
      - If not set, the value of the E(QUMULO_NEXUS_API_TOKEN) environment variable is used.
    type: str
  nexus_api_url:
    description:
      - Base URL of the Nexus API. Only https URLs are accepted, and the certificate is always verified.
      - If not set, the value of the E(QUMULO_NEXUS_API_URL) environment variable is used.
    type: str
    default: https://api.nexus.qumulo.com
"""
