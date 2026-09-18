#!/usr/bin/env python3
"""Read-only Obsidian wikilink and orphan diagnostics."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fts_indexer import ScanReport, scan_notes

_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_DAILY_PATH = re.compile(
    r"(?:^|/)(?:daily|journal|journals|logs?)(?:/|$)", re.IGNORECASE
)
_DAILY_NAME = re.compile(r"^\d{4}[-_.]\d{2}[-_.]\d{2}$")


@dataclass(frozen=True, slots=True)
class LinkReference:
    source: str
    line: int
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "line": self.line, "target": self.target}


def _aliases(content: str) -> list[str]:
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


def _normalize_target(value: str) -> str:
    target = value.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
    target = target.removesuffix(".md").strip("/")
    return PurePosixPath(target).as_posix() if target else ""


def _links(path: str, content: str) -> list[LinkReference]:
    references: list[LinkReference] = []
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", content))
    for match in _WIKILINK.finditer(content):
        target = _normalize_target(match.group(1))
        if not target or "{{" in target or "}}" in target:
            continue
        references.append(
            LinkReference(
                source=path,
                line=bisect.bisect_right(line_starts, match.start()),
                target=target,
            )
        )
    return references


def _daily_or_template(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        _DAILY_PATH.search(path) is not None
        or _DAILY_NAME.match(pure.stem) is not None
        or any(
            part.casefold() in {"template", "templates", "模板"} for part in pure.parts
        )
    )


def diagnose_vault(
    vault_root: Path,
    *,
    include_daily_orphans: bool = False,
    source_scan: ScanReport | None = None,
) -> dict[str, Any]:
    """Return deterministic diagnostics without touching source files."""
    root = vault_root.resolve()
    scan = source_scan or scan_notes(root)
    by_path: dict[str, str] = {}
    by_stem: dict[str, set[str]] = defaultdict(set)
    by_alias: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, list[LinkReference]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)

    for record in scan.records:
        key = record.path.removesuffix(".md").casefold()
        by_path[key] = record.path
        by_stem[Path(record.path).stem.casefold()].add(record.path)
        for alias in _aliases(record.content):
            by_alias[alias.casefold()].add(record.path)
        outgoing[record.path].extend(_links(record.path, record.content))

    asset_count = 0
    for asset in root.rglob("*"):
        if not asset.is_file() or asset.suffix.casefold() == ".md":
            continue
        try:
            relative = asset.relative_to(root)
            asset.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        relative_path = relative.as_posix()
        by_path[relative_path.casefold()] = relative_path
        by_stem[asset.stem.casefold()].add(relative_path)
        asset_count += 1

    broken: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    resolved_count = 0
    for source, links in outgoing.items():
        source_parent = PurePosixPath(source).parent
        for link in links:
            target_folded = link.target.casefold()
            local = PurePosixPath(source_parent, link.target).as_posix().casefold()
            candidates: set[str] = set()
            if local in by_path:
                candidates.add(by_path[local])
            if target_folded in by_path:
                candidates.add(by_path[target_folded])
            candidates.update(
                by_stem.get(PurePosixPath(link.target).name.casefold(), set())
            )
            candidates.update(by_alias.get(link.target.casefold(), set()))
            if len(candidates) == 1:
                destination = next(iter(candidates))
                incoming[destination] += 1
                resolved_count += 1
            elif not candidates:
                broken.append(link.to_dict())
            else:
                ambiguous.append({**link.to_dict(), "candidates": sorted(candidates)})

    orphans = [
        record.path
        for record in scan.records
        if incoming[record.path] == 0
        and not outgoing[record.path]
        and (include_daily_orphans or not _daily_or_template(record.path))
    ]
    broken_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in broken:
        broken_groups[str(item["target"])].append(item)
    broken_targets = [
        {
            "target": target,
            "occurrences": len(items),
            "sources": sorted({str(item["source"]) for item in items})[:20],
        }
        for target, items in sorted(
            broken_groups.items(), key=lambda pair: (-len(pair[1]), pair[0].casefold())
        )
    ]
    ambiguous_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ambiguous:
        ambiguous_groups[str(item["target"])].append(item)
    ambiguous_targets = [
        {
            "target": target,
            "occurrences": len(items),
            "sources": sorted({str(item["source"]) for item in items})[:20],
            "candidates": sorted(
                {str(candidate) for item in items for candidate in item["candidates"]}
            )[:20],
        }
        for target, items in sorted(
            ambiguous_groups.items(),
            key=lambda pair: (-len(pair[1]), pair[0].casefold()),
        )
    ]
    manifest = "\n".join(
        f"{item.path}:{item.checksum}" for item in scan.records
    ).encode()
    return {
        "status": "issues_found" if broken or ambiguous or orphans else "healthy",
        "vault_root": str(root),
        "vault_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "notes_scanned": len(scan.records),
        "assets_scanned": asset_count,
        "links_resolved": resolved_count,
        "broken_links": broken,
        "broken_targets": broken_targets,
        "ambiguous_links": ambiguous,
        "ambiguous_targets": ambiguous_targets,
        "orphans": sorted(orphans),
        "skipped_decode": list(scan.skipped_decode),
        "skipped_io": list(scan.skipped_io),
    }


def diagnostic_counts(report: dict[str, Any]) -> dict[str, int]:
    """Return stable issue counts from either a full or summarized doctor report."""
    existing = report.get("counts")
    if isinstance(existing, dict):
        return {
            "broken_link_occurrences": int(existing["broken_link_occurrences"]),
            "broken_targets": int(existing["broken_targets"]),
            "ambiguous_link_occurrences": int(existing["ambiguous_link_occurrences"]),
            "ambiguous_targets": int(existing["ambiguous_targets"]),
            "orphans": int(existing["orphans"]),
        }
    return {
        "broken_link_occurrences": len(report.get("broken_links", [])),
        "broken_targets": len(report.get("broken_targets", [])),
        "ambiguous_link_occurrences": len(report.get("ambiguous_links", [])),
        "ambiguous_targets": len(report.get("ambiguous_targets", [])),
        "orphans": len(report.get("orphans", [])),
    }


def summarize_diagnostics(
    report: dict[str, Any], *, detail_limit: int = 10
) -> dict[str, Any]:
    """Create a bounded, automation-friendly projection of a full doctor report."""
    if detail_limit < 0 or detail_limit > 100:
        raise ValueError("detail_limit must be between 0 and 100")
    counts = diagnostic_counts(report)
    broken_targets = list(report.get("broken_targets", []))
    ambiguous_targets = list(report.get("ambiguous_targets", []))
    orphans = list(report.get("orphans", []))

    def bounded_target(item: dict[str, Any]) -> dict[str, Any]:
        bounded = dict(item)
        if "sources" in bounded:
            bounded["sources"] = list(bounded["sources"])[:5]
        if "candidates" in bounded:
            bounded["candidates"] = list(bounded["candidates"])[:5]
        return bounded

    return {
        "schema_version": 1,
        "status": report["status"],
        "vault_root": report["vault_root"],
        "vault_manifest_sha256": report["vault_manifest_sha256"],
        "notes_scanned": report["notes_scanned"],
        "assets_scanned": report["assets_scanned"],
        "links_resolved": report["links_resolved"],
        "counts": counts,
        "top_broken_targets": [
            bounded_target(item) for item in broken_targets[:detail_limit]
        ],
        "top_ambiguous_targets": [
            bounded_target(item) for item in ambiguous_targets[:detail_limit]
        ],
        "orphan_samples": orphans[:detail_limit],
        "truncated": {
            "broken_targets": max(0, len(broken_targets) - detail_limit),
            "ambiguous_targets": max(0, len(ambiguous_targets) - detail_limit),
            "orphans": max(0, len(orphans) - detail_limit),
        },
        "skipped_decode": list(report.get("skipped_decode", [])),
        "skipped_io": list(report.get("skipped_io", [])),
    }


def format_diagnostics_summary(report: dict[str, Any]) -> str:
    """Render one concise human-readable line from either report representation."""
    counts = diagnostic_counts(report)
    return (
        f"{report['status']}: {report['notes_scanned']} notes, "
        f"{counts['broken_link_occurrences']} broken link occurrences across "
        f"{counts['broken_targets']} targets, "
        f"{counts['ambiguous_link_occurrences']} ambiguous occurrences, "
        f"{counts['orphans']} orphan candidates"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--include-daily-orphans", action="store_true")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = diagnose_vault(
        args.vault, include_daily_orphans=args.include_daily_orphans
    )
    output = (
        report
        if args.details
        else summarize_diagnostics(report, detail_limit=args.detail_limit)
    )
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(format_diagnostics_summary(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
