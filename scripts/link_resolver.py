#!/usr/bin/env python3
"""Read-only wikilink resolution shared by diagnostics and the relationship graph.

Both the doctor and the link graph must agree on what a ``[[wikilink]]`` resolves to.
Running two independent copies of that logic produced different verdicts for the same
Vault, so resolution lives here once.

The model follows Obsidian's documented behaviour rather than an invented ladder.
Obsidian's help states that it "resolves links by finding the best match among files
in your vault", that with the default link format it "uses only the filename if it is
unique in the vault", that it "adds enough path components to disambiguate" when two
notes share a name, and that the supported link forms are a path relative to the
current note or an absolute path from the vault root. Non-Markdown targets must carry
their file extension (``[[Figure 1.png]]``).

That yields one rule with two branches:

* a target containing a path separator is resolved **as a path** - relative to the
  linking note first, then from the vault root - because an explicit path must win
  over any bare-name match;
* a bare target is resolved by **unique filename**, then by unique alias.

Ambiguity is never silently collapsed. Obsidian's documentation does not specify how
an already-existing bare link is resolved when the filename is not unique, so such a
target stays ``ambiguous`` and only carries a proximity candidate as an explicitly
unverified hint.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")

# A target with no separator: the documented "filename if unique" branch.
LEVEL_BASENAME = "unique_basename"
LEVEL_ALIAS = "unique_alias"
# A target that already carries path components: the documented path forms.
LEVEL_RELATIVE = "relative_to_note"
LEVEL_VAULT_PATH = "vault_relative_path"

STATUS_RESOLVED = "resolved"
STATUS_UNRESOLVED = "unresolved"
STATUS_AMBIGUOUS = "ambiguous"

LEVEL_PRECEDENCE: tuple[str, ...] = (
    LEVEL_RELATIVE,
    LEVEL_VAULT_PATH,
    LEVEL_BASENAME,
    LEVEL_ALIAS,
)


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
    basename_fallback: str | None = None

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
        if self.basename_fallback:
            payload["basename_fallback"] = self.basename_fallback
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
    if not target:
        return ""
    explicit_relative = target.startswith("./") or target.startswith("../")
    normalized = PurePosixPath(target).as_posix()
    # `PurePosixPath` drops a leading "./", which would silently demote an explicit
    # same-directory link to a bare filename. Keep the marker so it is resolved as a path.
    if explicit_relative and not normalized.startswith("."):
        return f"./{normalized}"
    return normalized


def join_relative(parent: str, target: str) -> str | None:
    """Resolve a note-relative target lexically, bounded to the Vault root.

    `PurePosixPath` keeps `..` segments, so `Area/Sub` + `../Target` stayed
    `Area/Sub/../Target` and never matched a real path. Normalise `.`/`..` instead.
    Returns None when the target escapes the Vault; no filesystem access is involved,
    matching how Obsidian resolves a path relative to the linking note.
    """
    segments: list[str] = []
    for part in PurePosixPath(parent, target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not segments:
                return None
            segments.pop()
            continue
        segments.append(part)
    return "/".join(segments) if segments else None


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

    def with_planned_note(self, path: str) -> LinkResolver:
        """A copy that also knows about a note which does not exist yet.

        Answers "would creating this path actually resolve anything?" *before* a create is
        staged. A successful write is not evidence that a link was fixed: the broker may place
        the file at a path no link points at.
        """
        planned = LinkResolver(
            matching=dict(self.matching),
            by_short={key: set(value) for key, value in self.by_short.items()},
            by_alias={key: set(value) for key, value in self.by_alias.items()},
            assets=self.assets,
        )
        planned.matching[path.removesuffix(".md").casefold()] = path
        planned.by_short.setdefault(_short_key(path), set()).add(path)
        return planned

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
        """Resolve one occurrence using Obsidian's documented rules."""
        source = reference.source
        target = reference.target
        folded = target.casefold()
        parent = PurePosixPath(source).parent
        has_path = "/" in target

        if has_path:
            # An explicit path must not be overridden by an unrelated bare-name match.
            relative = join_relative(str(parent), target)
            levels: list[tuple[str, set[str]]] = [
                (
                    LEVEL_RELATIVE,
                    {self.matching[relative.casefold()]}
                    if relative and relative.casefold() in self.matching
                    else set(),
                ),
                (
                    LEVEL_VAULT_PATH,
                    {self.matching[folded]} if folded in self.matching else set(),
                ),
            ]
        else:
            levels = [
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
            basename_fallback=self._basename_fallback(target) if has_path else None,
        )

    def _basename_fallback(self, target: str) -> str | None:
        """A unique same-named file elsewhere, when a path target does not exist.

        Obsidian's help does not say whether a path-qualified link falls back to a
        unique filename after the path fails. Reported as information only; the link
        stays unresolved so the unknown rule is never silently decided here.
        """
        candidates = self.by_short.get(_short_key(target), set())
        return next(iter(candidates)) if len(candidates) == 1 else None

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
