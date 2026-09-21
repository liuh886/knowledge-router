#!/usr/bin/env python3
"""Explainable, read-only quality signals for Vaults and project catalogs."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

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
) -> dict[str, Any]:
    """Prioritize explainable findings against reviewed Context scopes.

    Every input signal remains visible in either the maintained or unscoped bucket.  Archive,
    backup and snapshot paths receive only a historical hint; they are never silently excluded.
    """
    if limit < 0 or limit > 100:
        raise ValueError("limit must be between 0 and 100")
    definitions = [item for item in contexts if isinstance(item, dict)]

    def under(path: str, scope: str) -> bool:
        normalized_path = path.replace("\\", "/").strip("/")
        normalized_scope = scope.replace("\\", "/").strip("/")
        return normalized_scope in {"", "."} or normalized_path == normalized_scope or (
            normalized_path.startswith(normalized_scope + "/")
        )

    def matched_contexts(path: str | None) -> list[str]:
        if not path:
            return []
        matches = []
        for context in definitions:
            scopes = context.get("scopes", [])
            if isinstance(scopes, list) and any(under(path, str(scope)) for scope in scopes):
                matches.append(str(context.get("id", "")))
        return sorted(item for item in matches if item)

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
        context_ids = matched_contexts(signal.path)
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
    return {
        "schema_version": 1,
        "status": "findings" if entries else "clear",
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
