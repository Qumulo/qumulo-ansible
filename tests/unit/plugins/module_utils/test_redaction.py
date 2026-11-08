# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""redaction.py's URLs without their secrets (specs/017-cluster-upgrade, FR-032)."""

from __future__ import annotations

import pytest
from ansible_collections.qumulo.cluster.plugins.module_utils import redaction

MIRROR = "https://mirror.example.com/qumulo"


# The URL and its secret parts (FR-032)


@pytest.mark.parametrize(
    ("url", "redacted", "secret"),
    [
        (f"{MIRROR}/qumulo_core_7.10.3.qimg?X-Amz-Signature=abc", f"{MIRROR}/qumulo_core_7.10.3.qimg", {"userinfo": None, "query": "X-Amz-Signature=abc"}),
        (
            "https://user:p%40ss@mirror.example.com:8443/q/qumulo_core_7.10.3.qimg",
            "https://mirror.example.com:8443/q/qumulo_core_7.10.3.qimg",
            {"userinfo": "user:p%40ss", "query": None},
        ),
        (f"{MIRROR}/qumulo_core_7.10.3.qimg", f"{MIRROR}/qumulo_core_7.10.3.qimg", {"userinfo": None, "query": None}),
        ("/upgrades/qumulo_core_7.10.3.qimg", "/upgrades/qumulo_core_7.10.3.qimg", {"userinfo": None, "query": None}),
        (
            "ftp://user:pw@mirror.example.com/q/qumulo_core_7.10.3.qimg?t=1",
            "ftp://mirror.example.com/q/qumulo_core_7.10.3.qimg",
            {"userinfo": "user:pw", "query": "t=1"},
        ),
    ],
)
def test_redact_url_and_url_secret_split_a_url_and_join_it_back(url, redacted, secret):
    assert redaction.redact_url(url) == redacted
    assert redaction.url_secret(url) == secret
    assert redaction.join_url(redaction.redact_url(url), redaction.url_secret(url)) == url


@pytest.mark.parametrize(
    ("userinfo", "query"),
    [("reader:pa/ss", None), ("reader:pa?ss", None), ("reader:pa#ss", None), ("reader:pa?ss", "sig=abc"), ("reader:p/s?s", None), ("reader:p@s/s", None)],
)
def test_user_information_with_an_unencoded_slash_question_mark_or_hash_is_split_off_whole(userinfo, query):
    """RFC 3986 requires them percent-encoded, and urlsplit ends the host at the first of them."""
    redacted = f"{MIRROR}/qumulo_core_7.10.3.qimg"
    url = redacted.replace("https://", f"https://{userinfo}@") + (f"?{query}" if query else "")

    assert redaction.redact_url(url) == redacted
    assert redaction.url_secret(url) == {"userinfo": userinfo, "query": query}
    assert redaction.join_url(redacted, redaction.url_secret(url)) == url
    assert redaction.redact_text(f"Qimg Download from: {url} failed") == f"Qimg Download from: {redacted} failed"


def test_an_at_sign_in_the_query_of_a_url_with_a_path_is_the_querys_own():
    """As in a signed URL's credential, which names a service account."""
    url = f"{MIRROR}/qumulo_core_7.10.3.qimg?X-Goog-Credential=reader@project.example.com"

    assert redaction.redact_url(url) == f"{MIRROR}/qumulo_core_7.10.3.qimg"
    assert redaction.url_secret(url) == {"userinfo": None, "query": "X-Goog-Credential=reader@project.example.com"}


def test_user_information_with_an_unencoded_slash_is_cut_off_whole_where_urlsplit_refuses_the_url():
    assert redaction.redact_url("https://u:pa/ss@[::1/qumulo_core_7.11.0.qimg?sig=abc") == "https://[::1/qumulo_core_7.11.0.qimg"


def test_a_url_with_no_host_is_no_url_to_redact():
    assert redaction.redact_url("https:/mirror.example.com/qumulo_core_7.10.3.qimg") == "https:/mirror.example.com/qumulo_core_7.10.3.qimg"


def test_the_full_url_is_replaced_inside_a_server_text():
    url = "https://user:pw@mirror.example.com/qumulo_core_7.10.3.qimg?sig=abc"
    text = f"Qimg Download from: {url} failed; retrying {url}"

    assert redaction.redact_text(text, url) == (
        "Qimg Download from: https://mirror.example.com/qumulo_core_7.10.3.qimg failed; retrying https://mirror.example.com/qumulo_core_7.10.3.qimg"
    )


def test_redact_text_also_strips_a_url_it_was_not_given():
    assert redaction.redact_text("fetch https://a:b@example.com/x.qimg?t=1 failed") == "fetch https://example.com/x.qimg failed"


@pytest.mark.parametrize("scheme", ["ftp", "sftp", "s3", "SMB"])
def test_a_url_of_any_scheme_loses_its_user_information_and_query(scheme):
    """F2 refuses an image that is no http(s) URL, but prints it, and a password is likeliest in such a URL."""
    url = f"{scheme}://a:b@example.com/x.qimg?t=1"
    redacted = f"{scheme.lower()}://example.com/x.qimg"

    assert redaction.redact_url(url) == redacted
    assert redaction.redact_text(f"fetch {url} failed") == f"fetch {redacted} failed"


def test_a_url_of_another_scheme_that_urlsplit_refuses_is_still_cut_before_its_secrets():
    assert redaction.redact_url("ftp://a:b@[::1/x.qimg?t=1") == "ftp://[::1/x.qimg"


@pytest.mark.parametrize(
    ("url", "redacted"),
    [
        (f"{MIRROR}/qumulo_core_7.10.3.qimg?sig=ab,cd", f"{MIRROR}/qumulo_core_7.10.3.qimg"),
        ("https://user:p(w)@mirror.example.com/qumulo_core_7.10.3.qimg", "https://mirror.example.com/qumulo_core_7.10.3.qimg"),
        ("https://[::1]:8443/qumulo_core_7.10.3.qimg?q=1", "https://[::1]:8443/qumulo_core_7.10.3.qimg"),
    ],
)
def test_redact_text_leaves_no_fragment_of_a_secret_that_punctuation_or_brackets_could_cut(url, redacted):
    """A query string and user information may hold , ( ) [ ] unencoded (RFC 3986), and an IPv6 host sits in brackets."""
    assert redaction.redact_text(f"exec([ verify.py, --path, {url} ])") == f"exec([ verify.py, --path, {redacted} ])"
    assert redaction.redact_text(f"'{url}'") == f"'{redacted}'"


@pytest.mark.parametrize("after", [".", ",", ";", ":", "!", "?", ")", "]", "}", ">"])
def test_redact_text_ends_a_url_before_trailing_punctuation(after):
    url = "https://mirror.example.com/qumulo_core_7.11.0.qimg?sig=abc"

    assert redaction.redact_text(f"(see {url}{after} then)") == f"(see https://mirror.example.com/qumulo_core_7.11.0.qimg{after} then)"


@pytest.mark.parametrize(("opening", "closing"), [("[", "]"), ("(", ")"), ("{", "}"), ("<", ">")])
def test_redact_text_keeps_the_brackets_around_a_url(opening, closing):
    url = "https://mirror.example.com/qumulo_core_7.11.0.qimg?sig=abc"

    assert redaction.redact_text(f"fetch {opening}{url}{closing}.") == f"fetch {opening}https://mirror.example.com/qumulo_core_7.11.0.qimg{closing}."


@pytest.mark.parametrize(
    ("url", "redacted"),
    [
        ("https://user:pw@[::1/qumulo_core_7.11.0.qimg?sig=abc", "https://[::1/qumulo_core_7.11.0.qimg"),
        ("https://mirror]example.com/qumulo_core_7.11.0.qimg?sig=abc#part", "https://mirror]example.com/qumulo_core_7.11.0.qimg"),
        ("https://u@[mirror.example.com]?sig=abc", "https://[mirror.example.com]"),
    ],
)
def test_a_url_with_a_bracket_urlsplit_refuses_is_still_cut_before_its_secrets(url, redacted):
    """urlsplit raises on an unbalanced bracket in the host, or a bracketed host that is no IPv6 address."""
    assert redaction.redact_url(url) == redacted
    assert redaction.redact_text(f"Qimg Download from: {url} failed") == f"Qimg Download from: {redacted} failed"


@pytest.mark.parametrize(
    ("text", "redacted"),
    [
        (f"Download of {MIRROR}/x.qimg?sig=abc. Retrying.", f"Download of {MIRROR}/x.qimg. Retrying."),
        (f"Download failed ({MIRROR}/x.qimg?sig=abc).", f"Download failed ({MIRROR}/x.qimg)."),
        (f"[{MIRROR}]", f"[{MIRROR}]"),
        ("fetch HTTPS://a:b@mirror.example.com/x.qimg?t=1 failed", "fetch https://mirror.example.com/x.qimg failed"),
        ("fetch https://u:p'w@mirror.example.com/x.qimg failed", "fetch https://mirror.example.com/x.qimg failed"),
        (f"fetch {MIRROR}/x.qimg?sig=a'b failed", f"fetch {MIRROR}/x.qimg failed"),
        ("fetch https://u:p@[fe80::1]:8443/x.qimg?q=1 failed", "fetch https://[fe80::1]:8443/x.qimg failed"),
    ],
)
def test_redact_text_keeps_the_prose_around_a_url_and_leaves_none_of_its_secret(text, redacted):
    """A scheme is any case, and user information and a query string may hold a ' unencoded (RFC 3986)."""
    assert redaction.redact_text(text) == redacted
