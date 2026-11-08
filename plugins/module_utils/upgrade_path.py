# Copyright (c) 2026 Qumulo, Inc.
# SPDX-License-Identifier: MIT
"""An upgrade's path: the release arithmetic, the images list, and each host's hops to its target.

The release rule is research R5's: a quarterly, x.y.0 or one of its dot releases x.y.0.w, accepts every release of the
previous quarter; any later release accepts only the earlier releases of its own quarter; every build accepts a later
build of its own main release. The server checks each hop again at its verify (G-UPGRADE-PATH).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from ansible_collections.qumulo.cluster.plugins.module_utils.client import QumuloError
from ansible_collections.qumulo.cluster.plugins.module_utils.cluster import OBJECT_BACKED
from ansible_collections.qumulo.cluster.plugins.module_utils.redaction import redact_url
from ansible_collections.qumulo.cluster.plugins.module_utils.version import ClusterVersion

ON_PREM = "on-prem"
CLOUD = "cloud"
KINDS = (ON_PREM, CLOUD)
KIND_WORDS = {ON_PREM: "on-premises", CLOUD: "cloud"}
# qumulo_cluster_kind, as the Terraform inventory plugin sets it, to the image kind its clusters take.
CLUSTER_KINDS = {"cnq": CLOUD, "edge": ON_PREM, "baremetal": ON_PREM}
# The file names Qumulo gives its images; the cloud name must be tried first, since it ends with the other.
IMAGE_NAMES = (
    (CLOUD, re.compile(r"cloud_qumulo_core_(?P<version>[0-9][0-9A-Z.]*)\.qimg")),
    (ON_PREM, re.compile(r"qumulo_core_(?P<version>[0-9][0-9A-Z.]*)\.qimg")),
)


class Refusal(QumuloError):
    """A refusal before any write, with its code from the spec's table (F1 to F34), or "images" for a malformed entry or a
    qumulo_cluster_kind that names no kind, since F15 is only for a kind that cannot be read.
    """

    def __init__(self, code, msg, **details):
        super().__init__(msg, **details)
        self.code = code


# Releases


def is_quarterly(release):
    """x.y.0 or one of its dot releases, x.y.0.w."""
    return release.release[2] == 0


def main_release(release):
    return release.release[:3]


def dotted(quarter):
    return ".".join(str(n) for n in quarter)


def same_release(a, b):
    """Two releases a portal would connect: the whole release string, the letter included (research R8)."""
    return (a.release, a.respin) == (b.release, b.respin)


def next_quarter(quarter, named=()):
    """The quarter after (x, y): (x, y + 1) within a major, by arithmetic, whatever the fleet runs or names.

    Only a major's end comes from named: when no later quarter of major x is named and a later major is, the next is
    (x + 1, 0), and the server checks that hop again at its verify.
    """
    major, minor = quarter
    named = [r.quarter for r in named]
    if any(q[0] == major and q[1] > minor for q in named) or not any(q[0] > major for q in named):
        return (major, minor + 1)
    return (major + 1, 0)


def previous_quarter(quarter, named=()):
    major, minor = quarter
    if minor > 0:
        return (major, minor - 1)
    older = [r.quarter for r in named if r.quarter[0] == major - 1]
    return max(older) if older else None


def accepts(source, target, named=()):
    """Whether target accepts source as the release it upgrades from, by research R5's rule."""
    if target <= source:
        return False  # a downgrade, or nothing to do
    if main_release(target) == main_release(source) or target.quarter == source.quarter:
        return True
    return is_quarterly(target) and previous_quarter(target.quarter, named) == source.quarter


# The images list


@dataclass(frozen=True)
class Image:
    index: int  # its place in the host's own qumulo_upgrade_images
    release: ClusterVersion
    kind: str
    image: str
    build: str | None = None  # declared only in a mapping entry, for its own release (Decision 41)

    @property
    def redacted(self):
        return redact_url(self.image)


def parse_images(entries):
    """qumulo_upgrade_images as Image tuples, in the list's order; a malformed entry is refused, naming the mapping form."""
    return [_parse_entry(index, entry) for index, entry in enumerate(entries or [])]


def _parse_entry(index, entry):
    if isinstance(entry, str):
        name = urlsplit(entry).path.rsplit("/", 1)[-1]
        for kind, pattern in IMAGE_NAMES:
            match = pattern.fullmatch(name)
            if match:
                try:
                    return Image(index, ClusterVersion.parse(match["version"]), kind, entry)
                except QumuloError:
                    break
        raise Refusal(
            "images",
            f"the qumulo_upgrade_images entry {redact_url(entry)} names no release: its file name is not qumulo_core_<version>.qimg "
            "or cloud_qumulo_core_<version>.qimg. Give it as a mapping, {version, image, kind, build}.",
        )
    if not isinstance(entry, dict):
        raise Refusal("images", f"the qumulo_upgrade_images entry {entry!r} is neither a URL, a path, nor a mapping {{version, image, kind, build}}.")
    image = entry.get("image")
    if not isinstance(image, str) or not image:
        raise Refusal("images", f"the qumulo_upgrade_images entry for version {entry.get('version')} needs image, a URL or a path.")
    shown = redact_url(image)
    version = entry.get("version")
    if isinstance(version, (int, float)) and not isinstance(version, bool):
        raise Refusal(
            "images", f'the qumulo_upgrade_images entry {shown} gives version {version} as a number, which YAML may change; quote it, as "{version}".'
        )
    if entry.get("kind") not in KINDS:
        raise Refusal("images", f"the qumulo_upgrade_images entry {shown} needs kind, on-prem or cloud, since a path's name may not carry it.")
    try:
        release = ClusterVersion.parse(version)
    except QumuloError as e:
        raise Refusal("images", f"the qumulo_upgrade_images entry {shown}: {e}") from None
    build = entry.get("build")
    return Image(index, release, entry["kind"], image, None if build in (None, "") else str(build))


def _order_break(images):
    """F4's break and fix for a list out of order or holding a duplicate, or None."""
    seen = set()
    for image in images:
        if (image.release, image.release.respin, image.kind) in seen:
            return f"two entries are {image.release} {KIND_WORDS[image.kind]} images", "keep one"
        seen.add((image.release, image.release.respin, image.kind))
    releases = [image.release for image in images]
    if releases != sorted(releases):
        order = []
        for release in sorted(releases):
            if not order or not same_release(order[-1], release):
                order.append(release)
        return "the entries are out of order", "list them as " + ", ".join(str(r) for r in order)
    return None


def host_kind(cluster_kind, protection_type):
    """The image kind a host takes: from qumulo_cluster_kind, else from its protection type, else None."""
    if cluster_kind:
        return CLUSTER_KINDS[cluster_kind]
    if protection_type in OBJECT_BACKED:
        return CLOUD
    if protection_type:
        return ON_PREM
    return None


def the_limit(host, hub=None):
    """<the limit> of the refusals: the only --limit F13 allows for a portal member is its whole component."""
    return f"leave {hub}'s whole portal component out of the run with --limit" if hub else f"leave {host} out of the run with --limit"


# A host's hops (Decision 42)


@dataclass(frozen=True)
class HostPath:
    host: str
    current: ClusterVersion
    target: ClusterVersion
    kind: str | None
    kept: tuple[Image, ...]  # the entries of the host's kind above its running release and at or below its target
    hops: tuple[Image, ...]
    reboots_only: bool = False  # on the target build and still REBOOTING: the last hop, for its reboot step alone
    last: Image | None = None  # the target's entry, for a host given the last hop for its reboots alone
    named: tuple[ClusterVersion, ...] = field(default=(), repr=False)


def host_hops(host, current, images, target, *, cluster_kind, protection_type, rebooting=False, hub=None, named=None):
    """The host's hops to its own target, each the furthest kept entry the rule accepts; F3, F4, F10 or F15 otherwise.

    current is the running release with its build_id. named is every release the fleet's reads and lists name, for a
    major's first quarter; by default the host's own.
    """
    named = tuple(named if named is not None else [current, target, *(i.release for i in images)])

    def f4(brk, fix):
        return Refusal("F4", f"{host} on {current} cannot reach {target} with this images list: {brk}. {fix[0].upper()}{fix[1:]}.")

    found = _order_break(images)
    if found:
        raise f4(*found)
    if target < current:
        if hub:
            fix = f"set qumulo_upgrade_target for {host} to {current} or later, or leave {hub}'s whole portal component out of the run with --limit"
        else:
            fix = f"set qumulo_upgrade_target to {current} or later, or leave {host} out of the run with --limit"
        raise Refusal("F3", f"{host} runs Qumulo Core {current}, above the target {target}. The collection never downgrades: {fix}.")
    if cluster_kind and cluster_kind not in CLUSTER_KINDS:
        raise Refusal(
            "images",
            f"{host} declares qumulo_cluster_kind {cluster_kind}, which is none of cnq, edge or baremetal. "
            f"Set qumulo_cluster_kind for {host} to cnq, edge or baremetal.",
        )
    in_range = [i for i in images if current < i.release <= target]
    kind = host_kind(cluster_kind, protection_type)
    if kind is None:
        both = {i.kind for i in in_range}
        if len(both) > 1:
            release = min(i.release for i in in_range if any(o.release == i.release and o.kind != i.kind for o in in_range))
            raise Refusal(
                "F15",
                f"The images for {release} include both kinds, and {host} has no qumulo_cluster_kind. "
                f"Set qumulo_cluster_kind for {host} to cnq, edge or baremetal.",
            )
        kept = in_range
    elif cluster_kind:
        kept = [i for i in in_range if i.kind == kind]
    else:
        # A kind from the protection type picks among both kinds; one kind alone is used as it is, and the server's
        # verify refuses a wrong one before anything is installed (FR-013).
        kept = [i for i in in_range if i.kind == kind] if len({i.kind for i in in_range}) > 1 else in_range
    kept = tuple(kept)
    target_entry = next((i for i in images if same_release(i.release, target) and (kind is None or i.kind == kind)), None)
    if same_release(current, target):
        if target_entry and target_entry.build and current.build_id != target_entry.build:
            raise f4(
                f"{host} already runs {target} on build {current.build_id}, not the build {target_entry.build} its mapping entry declares",
                "correct the entry's build with Qumulo Care; the collection does not move a cluster between builds of one release",
            )
        return HostPath(host, current, target, kind, kept, (), reboots_only=rebooting, last=target_entry if rebooting else None, named=named)
    chain = _walk(current, kept, target, named)
    if chain and isinstance(chain[0], str):
        if cluster_kind:
            _refuse_other_kind(host, kind, current, in_range, target, named, len(_walk_until_gap(current, kept, target, named)))
        raise f4(*chain)
    return HostPath(host, current, target, kind, kept, chain, named=named)


def _refuse_other_kind(host, kind, current, in_range, target, named, taken):
    """F10 when the hop at the gap in the host's own kind's chain is an entry of the other kind."""
    any_kind = _walk_until_gap(current, tuple(in_range), target, named)
    if len(any_kind) > taken and any_kind[taken].kind != kind:
        other, words = any_kind[taken], KIND_WORDS[kind]
        raise Refusal(
            "F10",
            f"{host} is {_article(words)} {words} cluster, but its image for {other.release}, {other.redacted}, is "
            f"{_article(KIND_WORDS[other.kind])} {KIND_WORDS[other.kind]} image. Give {_article(words)} {words} image, "
            f"or correct qumulo_cluster_kind for {host}.",
        )


def _article(word):
    return "an" if word[0] in "aeiou" else "a"


def _walk(position, kept, target, named):
    """The hops from position to target over kept, or F4's (break, fix) for the first gap."""
    hops = _walk_until_gap(position, kept, target, named)
    end = hops[-1].release if hops else position
    if same_release(end, target):
        return hops
    accepted = [i for i in kept if end < i.release and accepts(end, i.release, named)]
    return _gap(end, kept, target, named, letters_only=bool(accepted))


def _walk_until_gap(position, kept, target, named):
    """The hops from position over kept, each the furthest accepted entry that is no letter release but the target's."""
    hops = []
    while not same_release(position, target):
        usable = [i for i in kept if position < i.release and accepts(position, i.release, named) and (not i.release.respin or same_release(i.release, target))]
        if not usable:
            break
        hop = max(usable, key=lambda i: i.release)
        hops.append(hop)
        position = hop.release
    return tuple(hops)


def _gap(position, kept, target, named, letters_only):
    if letters_only:
        return "the only acceptable entry is a letter release before the last hop", "use the release without the letter"
    if position.quarter == target.quarter:
        return "no entry is the target", f"add an image of {target}"
    quarter = next_quarter(position.quarter, named)
    first = dotted((*quarter, 0))
    if any(i.release.quarter == quarter for i in kept):
        return f"the only entry for {dotted(quarter)} is a later patch", f"add {first} or one of its dot releases"
    return f"the quarter {dotted(quarter)} has no image the rule accepts", f"add an image of {first} or one of its dot releases"


# Portal components that start a hop split (Decision 42, FR-011)


def split_at_start(positions, members):
    """Whether the members do not all run one release, the letter included, at positions, {host: release} at a hop's start."""
    releases = {(positions[m].release, positions[m].respin) for m in members if m in positions}
    return len(releases) > 1


def align_split(paths, components, named=None):
    """paths with each split component aligned at every hop.

    At each fleet hop, in a component whose members do not all run one release, a member whose next hop is above the
    component's earliest next hop takes that release first, when its own kept entries hold it, it is above the member's
    running release, and the rule accepts it; the rest of its chain is walked again from there.
    """
    paths = dict(paths)
    position = {host: path.current for host, path in paths.items()}
    done = {host: 0 for host in paths}  # hops of each host already taken in the simulation
    while True:
        pending = {host: path.hops[done[host]] for host, path in paths.items() if done[host] < len(path.hops)}
        if not pending:
            return paths
        for members in components:
            members = [m for m in members if m in paths]
            if not split_at_start(position, members):
                continue
            nexts = [pending[m].release for m in members if m in pending]
            if not nexts:
                continue
            earliest = min(nexts)
            for m in members:
                if m not in pending or pending[m].release <= earliest:
                    continue
                path = paths[m]
                entry = next((i for i in path.kept if same_release(i.release, earliest)), None)
                if entry is None or not position[m] < earliest or not accepts(position[m], earliest, path.named):
                    continue
                rest = _walk(earliest, path.kept, path.target, path.named)
                if rest and isinstance(rest[0], str):
                    continue
                paths[m] = replace(path, hops=(*path.hops[: done[m]], entry, *rest))
                pending[m] = entry
        release = min(hop.release for hop in pending.values())
        for host, hop in pending.items():
            if same_release(hop.release, release):
                position[host] = hop.release
                done[host] += 1
