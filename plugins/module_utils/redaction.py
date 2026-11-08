# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""URLs without their secrets (FR-032): a URL's query string and user information split off, joined back, and redacted
from any text that quotes the URL."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# A URL ends only at whitespace or a double quote: its query string and user information may hold , ( ) [ ] ' unencoded
# (RFC 3986), and an IPv6 host sits in brackets, so a match cut at any of them would leave part of a secret in the text.
# Punctuation, a closing bracket or a single quote at its very end is the sentence's, not the URL's. A scheme is any
# scheme in any case, as urlsplit takes it: F2 refuses an image that is no http(s) URL, but prints it.
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*", re.IGNORECASE)
URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"]*[^\s\"'.,;:!?)\]}>]", re.IGNORECASE)
# An authority with no user information: a bracketed IPv6 address or a name, and only digits after a colon.
HOST_PORT = re.compile(r"(?:\[[^\]]*\]|[^:\[\]]*)(?::[0-9]*)?")


# URLs (FR-032)


def _hidden_userinfo(url):
    """(scheme, user information, the rest) for a URL whose user information holds an unencoded /, ? or #; else None.

    RFC 3986 requires those percent-encoded, and urlsplit ends the host at the first of them, so it takes part of the user
    information for the host. Such a URL has an @ after that host: in the path, in a query or fragment straight after the
    host, or after a host that is no host[:port]. Its user information then runs to the URL's last @. An @ in the query
    or fragment of a URL with a path and a well-formed host is theirs, as in a signed URL's credential.
    """
    scheme, sep, rest = url.partition("://")
    if not sep or not SCHEME.fullmatch(scheme):
        return None
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    after_host = rest[len(authority) :]
    if "@" not in after_host:
        return None
    path = re.split(r"[?#]", after_host, maxsplit=1)[0]
    if "@" in path or not path or not HOST_PORT.fullmatch(authority.rpartition("@")[2]):
        userinfo, rest = rest.rsplit("@", 1)
        return scheme, userinfo, rest
    return None


def _parts(url):
    if not isinstance(url, str):
        return None
    hidden = _hidden_userinfo(url)
    if hidden:
        scheme, userinfo, rest = hidden
        parts = urlsplit(f"{scheme}://{rest}")
        return parts, userinfo, parts.netloc
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    userinfo, at, hostport = parts.netloc.rpartition("@")
    return parts, (userinfo if at else None) or None, hostport


def _cut_by_hand(url):
    """A URL urlsplit refuses, without its user information, query string and fragment; anything else as it is."""
    scheme, sep, rest = url.partition("://")
    if not sep or not SCHEME.fullmatch(scheme):
        return url
    hidden = _hidden_userinfo(url)
    rest = hidden[2] if hidden else rest
    authority, slash, path = re.split(r"[?#]", rest, maxsplit=1)[0].partition("/")
    return f"{scheme}://{authority.rpartition('@')[2]}{slash}{path}"


def redact_url(url):
    """The URL, of any scheme, without its query string and user information; a path, or anything with no host, as it is."""
    try:
        split = _parts(url)
    except ValueError:  # an unbalanced bracket in the host, or a bracketed host that is no IPv6 address
        return _cut_by_hand(url)
    if split is None:
        return url
    parts, hostport = split[0], split[2]
    return urlunsplit((parts.scheme, hostport, parts.path, "", parts.fragment))


def url_secret(url):
    """The parts redact_url drops, each on its own so each is masked on its own: {userinfo, query}."""
    split = _parts(url) if isinstance(url, str) else None
    if split is None:
        return {"userinfo": None, "query": None}
    parts, userinfo = split[0], split[1]
    return {"userinfo": userinfo, "query": parts.query or None}


def join_url(image, secret):
    """The full URL from redact_url's and url_secret's halves."""
    secret = secret or {}
    split = _parts(image)
    if split is None:
        return image
    parts, hostport = split[0], split[2]
    netloc = f"{secret['userinfo']}@{hostport}" if secret.get("userinfo") else hostport
    return urlunsplit((parts.scheme, netloc, parts.path, secret.get("query") or "", parts.fragment))


def redact_text(text, *urls):
    """text with each of urls, and any other URL in it, redacted: for server texts the module passes on."""
    if not isinstance(text, str):
        return text
    for url in urls:
        if url:
            text = text.replace(url, redact_url(url))
    return URL.sub(lambda m: redact_url(m.group(0)), text)
