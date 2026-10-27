#!/usr/bin/env python3
"""Explainable, read-only quality signals for Vaults and project catalogs."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from context_manager import extract_tags, note_in_context
from evidence import QualitySignal

if TYPE_CHECKING:
    from fts_indexer import ScanReport


def vault_quality_signals(
    vault_id: str, report: dict[str, Any], source_scan: ScanReport
) -> list[QualitySignal]:
    """Convert existing diagnostics and exact duplicate hashes into stable signals."""
    signals: list[QualitySignal] = []
    for item in report.get("broken_targets", []):
        sources = list(item.get("sources", []))
        hint = item.get("triage_hint")
        hint_text = f" Triage hint: {hint}." if hint else ""
        signals.append(
            QualitySignal(
                kind="broken_link",
                severity="warning",
                source_type="vault",
                source_id=vault_id,
                path=sources[0] if sources else None,
                reason=(
                    f"{item['occurrences']} link occurrence(s) cannot resolve target "
                    f"{item['target']!r}.{hint_text}"
                ),
                suggestion="Review the target spelling or create the intended note manually.",
                evidence={
                    "target": item["target"],
                    "occurrences": item["occurrences"],
                    "sources": sources,
                    "source_count": item.get("source_count", len(sources)),
                    "sources_truncated": item.get("sources_truncated", False),
                    "triage_hint": hint,
                    "triage_hint_counts": dict(item.get("triage_hint_counts", {})),
                },
            )
        )
    for item in report.get("ambiguous_targets", []):
        sources = list(item.get("sources", []))
        signals.append(
            QualitySignal(
                kind="ambiguous_link",
                severity="warning",
                source_type="vault",
                source_id=vault_id,
                path=sources[0] if sources else None,
                reason=(
                    f"{item['occurrences']} link occurrence(s) match multiple notes for "
                    f"{item['target']!r}."
                ),
                suggestion="Use a path-qualified wikilink when the intended note is clear.",
                evidence={
                    "target": item["target"],
                    "occurrences": item["occurrences"],
                    "sources": sources,
                    "source_count": item.get("source_count", len(sources)),
                    "sources_truncated": item.get("sources_truncated", False),
                    "candidates": list(item.get("candidates", [])),
                    "proximity_unique": int(item.get("proximity_unique", 0)),
                },
            )
        )
    for path in report.get("orphans", []):
        signals.append(
            QualitySignal(
                kind="orphan_note",
                severity="info",
                source_type="vault",
                source_id=vault_id,
                path=str(path),
                reason="The note has no incoming or outgoing wikilinks.",
                suggestion="Review whether a meaningful link would improve discoverability.",
            )
        )
    duplicates: dict[str, list[str]] = defaultdict(list)
    for record in source_scan.records:
        duplicates[record.checksum].append(record.path)
    for checksum, paths in sorted(duplicates.items()):
        if len(paths) < 2:
            continue
        signals.append(
            QualitySignal(
                kind="duplicate_content",
                severity="info",
                source_type="vault",
                source_id=vault_id,
                path=sorted(paths)[0],
                reason=f"{len(paths)} notes have byte-identical content.",
                suggestion="Review the copies; no automatic merge or deletion will occur.",
                evidence={"content_hash": checksum, "paths": sorted(paths)},
            )
        )
    for path in report.get("skipped_decode", []):
        signals.append(
            QualitySignal(
                kind="unreadable_note",
                severity="error",
                source_type="vault",
                source_id=vault_id,
                path=str(path),
                reason="The note could not be decoded for diagnostics.",
                suggestion="Review its encoding before relying on complete retrieval.",
            )
        )
    for path in report.get("skipped_io", []):
        signals.append(
            QualitySignal(
                kind="unreadable_note",
                severity="error",
                source_type="vault",
                source_id=vault_id,
                path=str(path),
                reason="The note could not be read for diagnostics.",
                suggestion="Review permissions or filesystem health.",
            )
        )
    return signals


def project_quality_signals(projects: Iterable[dict[str, Any]]) -> list[QualitySignal]:
    """Report catalog metadata gaps without assuming every repo needs every document."""
    records = list(projects)
    signals: list[QualitySignal] = []
    remotes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for project in records:
        remote = str(project.get("remote", "")).strip().casefold()
        if remote:
            remotes[remote].append(project)
        documents = {
            str(path).rsplit("/", 1)[-1].casefold() for path in project.get("core_documents", [])
        }
        project_id = str(project["project_id"])
        source_id = str(project["repo_id"])
        common = {
            "source_type": "repository",
            "source_id": source_id,
            "project_id": project_id,
        }
        if not any(name.startswith("readme.") for name in documents):
            signals.append(
                QualitySignal(
                    kind="project_metadata_gap",
                    severity="warning",
                    reason="The repository has no root README document.",
                    suggestion="Consider documenting its purpose and entry points.",
                    evidence={"missing": "README"},
                    **common,
                )
            )
        for filename, purpose in (
            ("design.md", "design decisions"),
            ("tasks.md", "current work"),
            ("agents.md", "agent operating rules"),
        ):
            if filename not in documents:
                signals.append(
                    QualitySignal(
                        kind="project_metadata_gap",
                        severity="info",
                        reason=f"The repository has no root {filename.upper()} document.",
                        suggestion=(
                            f"Add it only if this project benefits from explicit {purpose}."
                        ),
                        evidence={"missing": filename},
                        **common,
                    )
                )
        if bool(project.get("dirty")):
            signals.append(
                QualitySignal(
                    kind="working_tree_state",
                    severity="info",
                    reason="The repository working tree differs from HEAD.",
                    suggestion="Treat cited content hashes as authoritative for this catalog.",
                    **common,
                )
            )
        if not str(project.get("branch", "")) and str(project.get("head", "")):
            signals.append(
                QualitySignal(
                    kind="detached_head",
                    severity="warning",
                    reason="The repository is not on a named branch.",
                    suggestion="Review whether detached HEAD is intentional.",
                    **common,
                )
            )
        if not str(project.get("remote", "")):
            signals.append(
                QualitySignal(
                    kind="missing_remote",
                    severity="info",
                    reason="The repository has no origin remote.",
                    suggestion="Add one only if the repository should be shared or backed up.",
                    **common,
                )
            )
        skipped = list(project.get("skipped_documents", []))
        if skipped:
            signals.append(
                QualitySignal(
                    kind="unreadable_project_document",
                    severity="warning",
                    reason=f"{len(skipped)} project document(s) were not cataloged.",
                    suggestion="Review the reported size, encoding, or filesystem issue.",
                    evidence={"documents": skipped},
                    **common,
                )
            )
    for remote, matches in sorted(remotes.items()):
        if len(matches) < 2:
            continue
        first = matches[0]
        signals.append(
            QualitySignal(
                kind="duplicate_remote",
                severity="warning",
                source_type="repository",
                source_id=str(first["repo_id"]),
                project_id=str(first["project_id"]),
                reason=f"{len(matches)} local repositories share one origin remote.",
                suggestion="Confirm which checkout is authoritative before briefing or release work.",
                evidence={
                    "remote": remote,
                    "projects": [str(item["project_id"]) for item in matches],
                },
            )
        )
    return signals


def quality_report(
    signals: Iterable[QualitySignal], *, detail_limit: int = 20, details: bool = False
) -> dict[str, Any]:
    """Return a bounded summary by default and an explicit forensic view on request."""
    if detail_limit < 0 or detail_limit > 100:
        raise ValueError("detail_limit must be between 0 and 100")
    ordered = sorted(
        signals,
        key=lambda item: (
            {"error": 0, "warning": 1, "info": 2}[item.severity],
            item.kind,
            item.source_type,
            item.project_id or "",
            item.path or "",
        ),
    )
    by_severity = Counter(item.severity for item in ordered)
    by_kind = Counter(item.kind for item in ordered)
    visible = ordered if details else ordered[:detail_limit]
    return {
        "schema_version": 1,
        "status": "findings" if ordered else "healthy",
        "total_signals": len(ordered),
        "counts_by_severity": dict(sorted(by_severity.items())),
        "counts_by_kind": dict(sorted(by_kind.items())),
        "signals": [item.to_dict() for item in visible],
        "truncated": 0 if details else max(0, len(ordered) - detail_limit),
        "aggregate_score": None,
    }


def context_quality_queue(
    contexts: Iterable[dict[str, Any]],
    signals: Iterable[QualitySignal],
    *,
    limit: int = 20,
    source_scan: ScanReport | None = None,
    doctor_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prioritize explainable findings against reviewed Context scopes.

    Every input signal remains visible in either the maintained or unscoped bucket.  Archive,
    backup and snapshot paths receive only a historical hint; they are never silently excluded.
    """
    if limit < 0 or limit > 100:
        raise ValueError("limit must be between 0 and 100")
    definitions = [item for item in contexts if isinstance(item, dict)]

    tags_by_path = {
        note.path: extract_tags(note.content) for note in source_scan.records
    } if source_scan is not None else {}
    # Use actual unresolved/ambiguous occurrences, not all links sharing a target:
    # the same relative target may resolve in one directory and fail in another.
    occurrence_paths: dict[tuple[str, str], set[str]] = defaultdict(set)
    if doctor_report is not None:
        for kind, key in (("broken_link", "broken_links"), ("ambiguous_link", "ambiguous_links")):
            for item in doctor_report.get(key, []):
                occurrence_paths[(kind, str(item["target"]))].add(str(item["source"]))

    def source_paths(signal: QualitySignal) -> tuple[set[str], bool, str]:
        target = signal.evidence.get("target")
        if doctor_report is not None and signal.kind in {"broken_link", "ambiguous_link"}:
            key = (signal.kind, str(target))
            if key in occurrence_paths:
                return occurrence_paths[key], True, "full_occurrences"
            # A supplied but inconsistent report is not evidence for a false clear.
            return set(), False, "missing_occurrences"
        paths = {
            str(p) for key in ("sources", "paths")
            for p in signal.evidence.get(key, []) if isinstance(p, str) and p
        }
        if signal.path:
            paths.add(signal.path)
        complete = not signal.evidence.get("sources_truncated", False)
        return paths, complete, "signal_sources"

    def membership(paths: set[str]) -> tuple[dict[str, int], list[str]]:
        counts: Counter[str] = Counter()
        unknown: set[str] = set()
        for path in sorted(paths):
            for context in definitions:
                cid = str(context.get("id", ""))
                if not cid:
                    continue
                # Missing tag evidence is unknown, rather than an empty tag list.
                if context.get("tags") and path not in tags_by_path:
                    if note_in_context(path, (), {**context, "tags": []}):
                        unknown.add(cid)
                    continue
                if note_in_context(path, tags_by_path.get(path, ()), context):
                    counts[cid] += 1
        return dict(sorted(counts.items())), sorted(unknown)

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    category_rank = {
        "evidence_gap": 0,
        "path_drift": 1,
        "broken_reference": 2,
        "maintained_orphan": 3,
        "unscoped_addition": 4,
        "historical_candidate": 5,
        "other": 6,
    }
    historical_parts = {"archive", "archives", "backup", "backups", "snapshot", "snapshots"}
    entries: list[dict[str, Any]] = []
    for signal in signals:
        if signal.source_type == "vault":
            paths, complete, basis = source_paths(signal)
            matched_counts, unknown_ids = membership(paths)
            context_ids = sorted(matched_counts)
        else:
            # Repository-relative paths cannot establish Vault scope membership.
            matched_counts, unknown_ids, context_ids = {}, [], []
            complete, basis = True, "non_vault_source"
        kind = signal.kind
        if kind in {"unreadable_note", "unreadable_project_document"}:
            category = "evidence_gap"
        elif kind in {"context_path_drift", "scope_without_evidence"}:
            category = "path_drift"
        elif kind in {"broken_link", "ambiguous_link"}:
            category = "broken_reference"
        elif kind == "orphan_note":
            parts = {part.casefold() for part in str(signal.path or "").replace("\\", "/").split("/")}
            category = "maintained_orphan" if context_ids else (
                "historical_candidate" if parts & historical_parts else "unscoped_addition"
            )
        else:
            category = "other"
        bucket = "maintained" if context_ids else "unscoped"
        entries.append(
            {
                "priority": {
                    "maintained": 0,
                    "unscoped": 1,
                }[bucket] * 10 + severity_rank[signal.severity],
                "bucket": bucket,
                "category": category,
                "context_ids": context_ids,
                "context_source_counts": matched_counts,
                "unverified_context_ids": unknown_ids,
                "membership_complete": complete and not unknown_ids,
                "membership_basis": basis,
                "historical_hint": category == "historical_candidate",
                "signal": signal.to_dict(),
            }
        )
    entries.sort(
        key=lambda item: (
            item["priority"],
            category_rank[item["category"]],
            item["signal"].get("source_type", ""),
            item["signal"].get("path", ""),
            item["signal"].get("kind", ""),
        )
    )
    visible = entries[:limit]
    scan_complete = source_scan is None or not (
        source_scan.skipped_io or source_scan.skipped_decode
    )
    membership_complete = scan_complete and all(row["membership_complete"] for row in entries)
    return {
        "schema_version": 2,
        "membership_rules_version": 2,
        "membership_complete": membership_complete,
        "status": ("findings" if entries else "clear") if membership_complete else "incomplete",
        "total": len(entries),
        "counts_by_bucket": {
            "maintained": sum(item["bucket"] == "maintained" for item in entries),
            "unscoped": sum(item["bucket"] == "unscoped" for item in entries),
        },
        "counts_by_category": dict(
            sorted(Counter(item["category"] for item in entries).items())
        ),
        "items": visible,
        "truncated": max(0, len(entries) - limit),
        "repair_performed": False,
    }
