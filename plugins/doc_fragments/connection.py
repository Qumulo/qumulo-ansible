# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations


class ModuleDocFragment:
    # The connection options of every qumulo.cluster module, as client.connection_argument_spec() defines them.
    DOCUMENTATION = r"""
options:
  api_host:
    description:
      - Address or name of the Qumulo cluster's REST API.
      - An IPv6 address is given bare, as V(fd00::5); the URL puts it in brackets.
      - If not set, the value of the E(QUMULO_API_HOST) environment variable is used.
    type: str
    required: true
  api_port:
    description:
      - TCP port of the REST API.
      - If not set, the value of the E(QUMULO_API_PORT) environment variable is used.
    type: int
    default: 8000
  api_username:
    description:
      - User that logs in with O(api_password).
      - If not set, the value of the E(QUMULO_API_USERNAME) environment variable is used.
    type: str
    default: admin
  api_password:
    description:
      - Password of O(api_username).
      - Set exactly one of O(api_password) and O(api_token). An empty string counts as unset.
      - If not set, the value of the E(QUMULO_API_PASSWORD) environment variable is used.
    type: str
  api_token:
    description:
      - An access token, sent in place of logging in.
      - An access token is bound to one cluster and does not survive a rebuild.
      - Set exactly one of O(api_password) and O(api_token). An empty string counts as unset.
      - If not set, the value of the E(QUMULO_API_TOKEN) environment variable is used.
    type: str
  validate_certs:
    description:
      - Verify the cluster's TLS certificate.
      - If not set, the value of the E(QUMULO_VALIDATE_CERTS) environment variable is used.
    type: bool
    default: true
  ca_path:
    description:
      - PEM file of the certificate authority that signed the cluster's certificate.
      - On Python 3.13 and later this must be a CA certificate, with basic constraints.
      - A bare self-signed leaf certificate cannot be pinned here on Python 3.13 and later; such a cluster is reached
        only with O(validate_certs=false).
      - If not set, the value of the E(QUMULO_CA_PATH) environment variable is used.
    type: path
  request_timeout:
    description:
      - Seconds to wait for one HTTP response.
      - If not set, the value of the E(QUMULO_REQUEST_TIMEOUT) environment variable is used.
    type: int
    default: 60
  retry_timeout:
    description:
      - Seconds to keep retrying a request that is safe to resend, after a lost connection or an HTTP 502, 503 or 504.
      - If not set, the value of the E(QUMULO_RETRY_TIMEOUT) environment variable is used.
    type: int
    default: 300
  expected_cluster_uuid:
    description:
      - The UUID the cluster at O(api_host) must have.
      - Before the first credentials are sent, the module reads the cluster's UUID from C(GET /v1/node/state), which needs no login,
        and fails when it differs, so that no credentials are sent to the wrong cluster. On a mismatch the task fails with
        C(live_cluster_uuid) and C(expected_cluster_uuid) in its result.
      - An empty value disables this identity guard, and a module that logs in warns.
      - If not set, the value of the E(QUMULO_EXPECTED_CLUSTER_UUID) environment variable is used.
    type: str
"""
