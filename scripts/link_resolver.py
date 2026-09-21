#!/usr/bin/env python3
"""Read-only wikilink resolution shared by diagnostics and the relationship graph.

Both the doctor and the link graph must agree on what a ``[[wikilink]]`` resolves to.
Running two independent copies of that logic produced different verdicts for the same
Vault, so resolution lives here once.

Precedence is a *hypothesis* about Obsidian behaviour, not a verified equivalence.
Obsidian distinguishes at least relative links, vault-absolute paths and "shortest
path when the name is unique", and its exact tie-breaking is not reproduced here.
Each decision therefore records the level that produced it, and unresolved/ambiguous
outcomes are reported rather than guessed. ``nearest_candidate`` records which
candidate a proximity rule would prefer, but it never turns an ambiguity into a
resolution on this layer's own authority.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")

LEVEL_RELATIVE = "relative_to_note"
LEVEL_VAULT_PATH = "vault_relative_path"
LEVEL_BASENAME = "unique_basename"
LEVEL_ALIAS = "unique_alias"

STATUS_RESOLVED = "resolved"
STATUS_UNRESOLVED = "unresolved"
STATUS_AMBIGUOUS = "ambiguous"

_LEVEL_ORDER = (LEVEL_RELATIVE, LEVEL_VAULT_PATH, LEVEL_BASENAME, LEVEL_ALIAS)


@dataclass(frozen=True, slots=True)
class LinkReference:
    """One wikilink occurrence, before resolution."""

    source: str
    line: int
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "line": self.line, "target": self.target}


@dataclass(frozen=True, slots=True)
class LinkDecision:
    """The resolution verdict for one occurrence, with the level that decided it."""

    source: str
    line: int
    target: str
    status: str
    level: str
    destination: str | None
    candidates: tuple[str, ...] = ()
    nearest_candidate: str | None = None
    nearest_unique: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "line": self.line,
            "target": self.target,
            "status": self.status,
            "level": self.level,
        }
        if self.destination:
            payload["destination"] = self.destination
        if self.candidates:
            payload["candidates"] = list(self.candidates)
        if self.nearest_candidate:
            payload["nearest_candidate"] = self.nearest_candidate
            payload["nearest_unique"] = self.nearest_unique
        return payload


def aliases(content: str) -> list[str]:
    """Extract frontmatter aliases without mutating the note."""
    frontmatter = re.match(r"\A---\s*\r?\n(.*?)\r?\n---", content, re.DOTALL)
    if not frontmatter:
        return []
    lines = frontmatter.group(1).splitlines()
    values: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^aliases?\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline.startswith("[") and inline.endswith("]"):
            values.extend(inline[1:-1].split(","))
        elif inline:
            values.append(inline)
        else:
            for nested in lines[index + 1 :]:
                if nested and not nested[0].isspace():
                    break
                item = nested.strip()
                if item.startswith("-"):
                    values.append(item.removeprefix("-").strip())
        break
    return [item.strip().strip("'\"") for item in values if item.strip().strip("'\"")]


def normalize_target(value: str) -> str:
    """Strip alias/heading suffixes the way Obsidian treats a link target."""
    target = value.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
    target = target.removesuffix(".md").strip("/")
    return PurePosixPath(target).as_posix() if target else ""


def extract_links(path: str, content: str) -> list[LinkReference]:
    """Return every wikilink occurrence with its 1-based line number."""
    references: list[LinkReference] = []
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", content))
    for match in _WIKILINK.finditer(content):
        target = normalize_target(match.group(1))
        if not target or "{{" in target or "}}" in target:
            continue
        references.append(
            LinkReference(
                source=path,
                line=bisect_line(line_starts, match.start()),
                target=target,
            )
        )
    return references


def bisect_line(line_starts: list[int], position: int) -> int:
    """1-based line number for a character offset (kept local to avoid an import)."""
    low, high = 0, len(line_starts)
    while low < high:
        middle = (low + high) // 2
        if line_starts[middle] <= position:
            low = middle + 1
        else:
            high = middle
    return max(1, low)


def _short_key(value: str) -> str:
    """The final path segment without a trailing ``.md``, case-folded."""
    return PurePosixPath(value).name.removesuffix(".md").casefold()


@dataclass
class LinkResolver:
    """Resolve wikilinks against a fixed snapshot of readable paths.

    ``matching`` maps case-folded keys (note paths without extension, asset paths
    with extension) to real paths. ``by_short`` and ``by_alias`` map case-folded
    short names and aliases to the set of paths that carry them.
    """

    matching: Mapping[str, str] = field(default_factory=dict)
    by_short: Mapping[str, set[str]] = field(default_factory=dict)
    by_alias: Mapping[str, set[str]] = field(default_factory=dict)
    assets: frozenset[str] = frozenset()

    @classmethod
    def from_sources(
        cls,
        *,
        notes: Iterable[tuple[str, str]],
        assets: Iterable[str] = (),
    ) -> LinkResolver:
        """Build a resolver from ``(path, content)`` notes plus asset paths."""
        matching: dict[str, str] = {}
        short: dict[str, set[str]] = defaultdict(set)
        by_alias: dict[str, set[str]] = defaultdict(set)
        asset_paths: set[str] = set()

        for path, content in notes:
            matching[path.removesuffix(".md").casefold()] = path
            short[_short_key(path)].add(path)
            for alias in aliases(content):
                by_alias[alias.casefold()].add(path)
        for asset in assets:
            asset_paths.add(asset)
            matching[asset.casefold()] = asset
            short[_short_key(asset)].add(asset)
            short[PurePosixPath(asset).name.casefold()].add(asset)

        return cls(
            matching=dict(matching),
            by_short={key: set(value) for key, value in short.items()},
            by_alias={key: set(value) for key, value in by_alias.items()},
            assets=frozenset(asset_paths),
        )

    @classmethod
    def from_scan(cls, scan, *, vault_root: Path | None = None) -> LinkResolver:
        """Build a resolver from a scan, optionally including non-Markdown assets."""
        assets: list[str] = []
        if vault_root is not None:
            root = vault_root.resolve()
            for candidate in root.rglob("*"):
                if not candidate.is_file() or candidate.suffix.casefold() == ".md":
                    continue
                try:
                    relative = candidate.relative_to(root)
                    candidate.resolve().relative_to(root)
                except (OSError, ValueError):
                    continue
                if any(part.startswith(".") for part in relative.parts):
                    continue
                assets.append(relative.as_posix())
        return cls.from_sources(
            notes=((record.path, record.content) for record in scan.records),
            assets=assets,
        )

    def _nearest(self, source: str, candidates: set[str]) -> tuple[str | None, bool]:
        """Pick the candidate closest to the source note, without claiming resolution."""
        if not candidates:
            return None, False
        source_parts = PurePosixPath(source).parent.parts
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                _distance(source_parts, PurePosixPath(candidate).parent.parts),
                len(PurePosixPath(candidate).parts),
                candidate,
            ),
        )
        best = ranked[0]
        if len(ranked) > 1:
            first = _distance(source_parts, PurePosixPath(best).parent.parts)
            second = _distance(source_parts, PurePosixPath(ranked[1]).parent.parts)
            return best, first < second
        return best, True

    def resolve(self, reference: LinkReference) -> LinkDecision:
        """Resolve one occurrence using the documented precedence levels."""
        source = reference.source
        target = reference.target
        folded = target.casefold()
        parent = PurePosixPath(source).parent

        levels: list[tuple[str, set[str]]] = [
            (
                LEVEL_RELATIVE,
                {self.matching[PurePosixPath(parent, target).as_posix().casefold()]}
                if PurePosixPath(parent, target).as_posix().casefold() in self.matching
                else set(),
            ),
            (
                LEVEL_VAULT_PATH,
                {self.matching[folded]} if folded in self.matching else set(),
            ),
            (LEVEL_BASENAME, set(self.by_short.get(_short_key(target), set()))),
            (LEVEL_ALIAS, set(self.by_alias.get(folded, set()))),
        ]

        for level, candidates in levels:
            if not candidates:
                continue
            if len(candidates) == 1:
                return LinkDecision(
                    source=source,
                    line=reference.line,
                    target=target,
                    status=STATUS_RESOLVED,
                    level=level,
                    destination=next(iter(candidates)),
                )
            nearest, unique = self._nearest(source, candidates)
            return LinkDecision(
                source=source,
                line=reference.line,
                target=target,
                status=STATUS_AMBIGUOUS,
                level=level,
                destination=None,
                candidates=tuple(sorted(candidates)),
                nearest_candidate=nearest,
                nearest_unique=unique,
            )

        return LinkDecision(
            source=source,
            line=reference.line,
            target=target,
            status=STATUS_UNRESOLVED,
            level="none",
            destination=None,
        )

    def decide_all(self, outgoing: Iterable[LinkReference]) -> list[LinkDecision]:
        return [self.resolve(reference) for reference in outgoing]


def _distance(source_parts: tuple[str, ...], candidate_parts: tuple[str, ...]) -> int:
    """Segment distance between two directories; shallower divergence ranks first."""
    common = 0
    for left, right in zip(source_parts, candidate_parts, strict=False):
        if left.casefold() != right.casefold():
            break
        common += 1
    return (len(source_parts) - common) + (len(candidate_parts) - common)


LEVEL_PRECEDENCE: tuple[str, ...] = _LEVEL_ORDER
