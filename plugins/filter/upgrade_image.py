# Copyright (c) 2026 Qumulo, Inc.
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""qumulo.cluster.redact_images and qumulo.cluster.url_secret, which plugins/filter/redact_images.yml and url_secret.yml document."""

from __future__ import annotations

from collections.abc import Mapping

from ansible.errors import AnsibleFilterError
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import redact_url
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import url_secret as _url_secret


def redact_images(entries):
    """qumulo_upgrade_images with every image's query string and user information dropped, for the plan.

    One entry in place of the list is refused, not quoted, since its URL may hold the secret.
    """
    if isinstance(entries, (str, Mapping)):
        raise AnsibleFilterError("redact_images takes a qumulo_upgrade_images list, not one entry; give one entry as [entry].")
    return [{**entry, "image": redact_url(entry.get("image"))} if isinstance(entry, dict) else redact_url(entry) for entry in entries or []]


def url_secret(entry):
    """The parts redact_images drops from one entry's image, {userinfo, query}, for the upgrade module's image_secret."""
    return _url_secret(entry.get("image") if isinstance(entry, dict) else entry)


class FilterModule:
    def filters(self):
        return {"redact_images": redact_images, "url_secret": url_secret}
