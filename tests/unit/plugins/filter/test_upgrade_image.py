# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""qumulo.cluster.redact_images and qumulo.cluster.url_secret: the plan never takes a secret, and the module gets it apart."""

from __future__ import annotations

import pytest
from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.filter.upgrade_image import redact_images, url_secret

PRESIGNED = "https://bucket.s3.amazonaws.com/cloud_qumulo_core_7.11.0.qimg?X-Amz-Signature=abc&X-Amz-Expires=14400"
WITH_USER = "https://user:p%40ss@mirror.example.com/qumulo_core_7.11.0.qimg"
PATH = "/upgrades/qumulo_core_7.11.0.qimg"


def test_a_string_entry_is_redacted_and_its_secret_split_out():
    assert redact_images([PRESIGNED, WITH_USER]) == [
        "https://bucket.s3.amazonaws.com/cloud_qumulo_core_7.11.0.qimg",
        "https://mirror.example.com/qumulo_core_7.11.0.qimg",
    ]
    assert url_secret(PRESIGNED) == {"userinfo": None, "query": "X-Amz-Signature=abc&X-Amz-Expires=14400"}
    assert url_secret(WITH_USER) == {"userinfo": "user:p%40ss", "query": None}


def test_a_mapping_entry_keeps_its_fields_with_its_image_redacted():
    entry = {"version": "7.11.0", "kind": "cloud", "build": "393623.1.1", "image": PRESIGNED}

    assert redact_images([entry]) == [{**entry, "image": "https://bucket.s3.amazonaws.com/cloud_qumulo_core_7.11.0.qimg"}]
    assert url_secret(entry) == {"userinfo": None, "query": "X-Amz-Signature=abc&X-Amz-Expires=14400"}
    assert entry["image"] == PRESIGNED  # the host's variable is never changed


def test_a_path_is_never_secret():
    assert redact_images([PATH]) == [PATH]
    assert url_secret(PATH) == {"userinfo": None, "query": None}


def test_an_entry_with_neither_secret_part_reads_in_full():
    plain = "https://mirror.example.com/qumulo_core_7.11.0.qimg"

    assert redact_images([plain, {"version": "7.11.0", "kind": "on-prem", "image": plain}]) == [plain, {"version": "7.11.0", "kind": "on-prem", "image": plain}]
    assert url_secret(plain) == {"userinfo": None, "query": None}


def test_no_list_is_an_empty_list():
    assert redact_images(None) == []


@pytest.mark.parametrize("entry", [PRESIGNED, {"version": "7.11.0", "kind": "cloud", "image": PRESIGNED}])
def test_one_entry_in_place_of_the_list_is_refused_without_quoting_it(entry):
    with pytest.raises(AnsibleFilterError) as raised:
        redact_images(entry)

    assert str(raised.value) == "redact_images takes a qumulo_upgrade_images list, not one entry; give one entry as [entry]."
