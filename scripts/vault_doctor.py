#!/usr/bin/env python3
"""Read-only Obsidian wikilink and orphan diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from fts_indexer import ScanReport, scan_notes
from link_resolver import (
    STATUS_AMBIGUOUS,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    LinkReference,
    LinkResolver,
    aliases,
    extract_links,
)

_DAILY_PATH = re.compile(
    r"(?:^|/)(?:daily|journal|journals|logs?)(?:/|$)", re.IGNORECASE
)
_DAILY_NAME = re.compile(r"^\d{4}[-_.]\d{2}[-_.]\d{2}$")

# Triage hints only. None of these mean "safe to ignore": every hint is a
# heuristic about where a human should look first, and everything else stays
# explicit `needs_review`.
TRIAGE_ZOTERO = "zotero_generated"
TRIAGE_TEMPLATE = "template_placeholder"
TRIAGE_ASSET = "missing_asset_reference"
TRIAGE_REVIEW = "needs_review"

_ZOTERO_DIR = re.compile(r"zotero", re.IGNORECASE)
_TEMPLATE_DIRS = frozenset({"template", "templates", "模板"})
_PLACEHOLDER_TARGETS = frozenset(
    {
        "link",
        "links",
        "链接",
        "双向链接",
        "任务",
        "日程",
        "决策建议",
        "项目",
        "area",
        "resource",
        "resources",
        "资源",
    }
)
_ASSET_SUFFIXES = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".xlsx",
        ".xls",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".csv",
        ".zip",
        ".mp4",
        ".mov",
        ".ipynb",
    }
)

# Backwards-compatible aliases; resolution itself now lives in link_resolver.
_aliases = aliases
_links = extract_links


def triage_hint(source: str, target: str) -> tuple[str, str, str]:
    """Classify one *occurrence* with a reason, never as a final verdict.

    Returns ``(hint, reason, confidence)``. Confidence is always ``"hint"``: a
    Zotero-generated note or a template can still contain a reference that matters,
    and a target whose name looks like a concept must not be promoted to
    "actionable" by name alone.
    """
    parts = PurePosixPath(source).parts
    if any(_ZOTERO_DIR.search(part) for part in parts):
        return (
            TRIAGE_ZOTERO,
            "source sits in an auto-generated Zotero note directory; confirm before dismissing",
            "hint",
        )
    if any(part.casefold() in _TEMPLATE_DIRS for part in parts):
        return (
            TRIAGE_TEMPLATE,
            "source sits in a template directory",
            "hint",
        )
    folded = target.casefold()
    if folded in _PLACEHOLDER_TARGETS:
        return (
            TRIAGE_TEMPLATE,
            "target is a known boilerplate placeholder name; confirm before dismissing",
            "hint",
        )
    if PurePosixPath(target).suffix.casefold() in _ASSET_SUFFIXES:
        return (
            TRIAGE_ASSET,
            "target names a file attachment rather than a note",
            "hint",
        )
    return (
        TRIAGE_REVIEW,
        "no heuristic applies; needs a human decision",
        "hint",
    )


def _daily_or_template(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        _DAILY_PATH.search(path) is not None
        or _DAILY_NAME.match(pure.stem) is not None
        or any(part.casefold() in _TEMPLATE_DIRS for part in pure.parts)
    )


def _group_targets(
    items: list[dict[str, Any]], *, key: str = "target"
) -> list[dict[str, Any]]:
    """Group occurrence rows by target, keeping original totals and hint mix."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item[key])].append(item)
    rows: list[dict[str, Any]] = []
    for target, entries in sorted(
        groups.items(), key=lambda pair: (-len(pair[1]), pair[0].casefold())
    ):
        hints: dict[str, int] = defaultdict(int)
        for entry in entries:
            hints[str(entry.get("triage_hint", TRIAGE_REVIEW))] += 1
        dominant = sorted(hints.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
        row: dict[str, Any] = {
            "target": target,
            "occurrences": len(entries),
            "sources": sorted({str(entry["source"]) for entry in entries})[:20],
            "triage_hint": dominant,
            "triage_hint_counts": dict(sorted(hints.items())),
        }
        candidates = sorted(
            {str(candidate) for entry in entries for candidate in entry.get("candidates", ())}
        )
        if candidates:
            row["candidates"] = candidates[:20]
        proximity = sum(1 for entry in entries if entry.get("nearest_unique"))
        if proximity:
            row["proximity_unique"] = proximity
        rows.append(row)
    return rows


def diagnose_vault(
    vault_root: Path,
    *,
    include_daily_orphans: bool = False,
    source_scan: ScanReport | None = None,
) -> dict[str, Any]:
    """Return deterministic diagnostics without touching source files."""
    root = vault_root.resolve()
    scan = source_scan or scan_notes(root)
    resolver = LinkResolver.from_scan(scan, vault_root=root)

    outgoing: dict[str, list[LinkReference]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    for record in scan.records:
        outgoing[record.path].extend(extract_links(record.path, record.content))

    broken: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    resolved_count = 0
    level_counts: dict[str, int] = defaultdict(int)
    for source, links in outgoing.items():
        for link in links:
            decision = resolver.resolve(link)
            level_counts[decision.level] += 1
            if decision.status == STATUS_RESOLVED:
                incoming[decision.destination] += 1
                resolved_count += 1
            elif decision.status == STATUS_UNRESOLVED:
                hint, reason, confidence = triage_hint(link.source, link.target)
                if decision.basename_fallback:
                    reason = (
                        f"{reason}; a uniquely named file exists elsewhere, but the "
                        "documented behaviour for this fallback is unspecified"
                    )
                entry = {
                    **link.to_dict(),
                    "triage_hint": hint,
                    "triage_reason": reason,
                    "triage_confidence": confidence,
                }
                if decision.basename_fallback:
                    entry["basename_fallback"] = decision.basename_fallback
                broken.append(entry)
            elif decision.status == STATUS_AMBIGUOUS:
                ambiguous.append(decision.to_dict())

    orphans = [
        record.path
        for record in scan.records
        if incoming[record.path] == 0
        and not outgoing[record.path]
        and (include_daily_orphans or not _daily_or_template(record.path))
    ]
    broken_targets = _group_targets(broken)
    ambiguous_targets = _group_targets(ambiguous)
    broken_by_hint: dict[str, int] = defaultdict(int)
    for item in broken:
        broken_by_hint[str(item["triage_hint"])] += 1
    # Informational only: how many ambiguities a proximity rule would collapse.
    # This never changes the reported ambiguity, because that rule is unverified.
    ambiguous_proximity = sum(1 for item in ambiguous if item.get("nearest_unique"))
    unresolved_with_fallback = sum(1 for item in broken if item.get("basename_fallback"))
    manifest = "\n".join(
        f"{item.path}:{item.checksum}" for item in scan.records
    ).encode()
    return {
        "status": "issues_found" if broken or ambiguous or orphans else "healthy",
        "vault_root": str(root),
        "vault_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "notes_scanned": len(scan.records),
        "assets_scanned": len(resolver.assets),
        "links_resolved": resolved_count,
        "resolution_levels": dict(sorted(level_counts.items())),
        "broken_links": broken,
        "broken_targets": broken_targets,
        "broken_by_hint": dict(sorted(broken_by_hint.items())),
        "broken_link_occurrences": len(broken),
        "ambiguous_links": ambiguous,
        "ambiguous_targets": ambiguous_targets,
        "ambiguous_link_occurrences": len(ambiguous),
        "ambiguous_proximity_unique": ambiguous_proximity,
        "unresolved_with_unique_basename_fallback": unresolved_with_fallback,
        "orphans": sorted(orphans),
        "skipped_decode": list(scan.skipped_decode),
        "skipped_io": list(scan.skipped_io),
        "scan_complete": not (scan.skipped_decode or scan.skipped_io),
        "triage_note": (
            "Triage hints are heuristics, not verdicts: no hint means a reference is safe "
            "to ignore, and no target is called 'actionable' from its name alone. "
            "unresolved_with_unique_basename_fallback counts unresolved path targets whose "
            "basename is unique elsewhere; Obsidian's documented behaviour does not cover "
            "that fallback, so they stay unresolved and are reported instead."
        ),
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
        "broken_link_occurrences": int(
            report.get("broken_link_occurrences", len(report.get("broken_links", [])))
        ),
        "broken_targets": len(report.get("broken_targets", [])),
        "ambiguous_link_occurrences": int(
            report.get(
                "ambiguous_link_occurrences", len(report.get("ambiguous_links", []))
            )
        ),
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
        "resolution_levels": dict(report.get("resolution_levels", {})),
        "counts": counts,
        "broken_by_hint": dict(report.get("broken_by_hint", {})),
        "ambiguous_proximity_unique": int(report.get("ambiguous_proximity_unique", 0)),
        "unresolved_with_unique_basename_fallback": int(
            report.get("unresolved_with_unique_basename_fallback", 0)
        ),
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
        "scan_complete": bool(
            report.get("scan_complete", not report.get("skipped_decode") and not report.get("skipped_io"))
        ),
        "triage_note": report.get("triage_note"),
    }


def format_diagnostics_summary(report: dict[str, Any]) -> str:
    """Render one concise human-readable line from either report representation."""
    counts = diagnostic_counts(report)
    hints = report.get("broken_by_hint") or {}
    hint_text = ", ".join(f"{name} {value}" for name, value in sorted(hints.items()))
    incomplete = "" if report.get("scan_complete", True) else " (incomplete scan)"
    return (
        f"{report['status']}: {report['notes_scanned']} notes, "
        f"{counts['broken_link_occurrences']} broken link occurrences across "
        f"{counts['broken_targets']} targets, "
        f"{counts['ambiguous_link_occurrences']} ambiguous occurrences, "
        f"{counts['orphans']} orphan candidates"
        + (f"; triage hints: {hint_text}" if hint_text else "")
        + incomplete
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
