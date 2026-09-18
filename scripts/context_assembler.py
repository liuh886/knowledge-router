#!/usr/bin/env python3
"""Assemble bounded, source-cited project and decision briefs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from evidence import EvidenceRecord
from fts_indexer import read_index_source, search_index
from taxonomy_scanner import TaxonomyProfile, scan_taxonomy


def _match_entity(topic: str, entities: list[str], prefix: str) -> str | None:
    folded = topic.casefold()
    for entity in entities:
        simplified = re.sub(prefix, "", entity).casefold()
        if entity.casefold() in folded or (simplified and simplified in folded):
            return entity
    return None


def assemble_decision_context(
    vault_root: Path,
    index_path: Path,
    topic: str,
    *,
    profile: TaxonomyProfile | None = None,
    max_search_results: int = 5,
    vault_id: str = "",
    generation: str = "",
    repository_evidence: list[EvidenceRecord] | None = None,
) -> dict[str, Any]:
    """Build a brief from source excerpts; make evidence gaps explicit."""
    root = vault_root.resolve()
    profile = profile or scan_taxonomy(root)
    search_hits = search_index(
        index_path,
        topic,
        limit=max_search_results,
        vault_root=root,
        vault_id=vault_id,
        generation=generation,
    )
    matched_project = _match_entity(topic, profile.active_projects, r"^\d{4}_")
    matched_area = _match_entity(topic, profile.active_areas, r"^\d{3}_")
    project_sources: list[dict[str, Any]] = []
    area_sources: list[dict[str, Any]] = []

    if matched_project and profile.project_dir:
        project_root = root / profile.project_dir / matched_project
        for filename in (
            "DESIGN.md",
            "TASKS.md",
            "PROGRESS.md",
            "FINDINGS.md",
            "README.md",
        ):
            relative = (project_root / filename).relative_to(root).as_posix()
            if source := read_index_source(
                index_path,
                relative,
                vault_root=root,
                vault_id=vault_id,
                generation=generation,
            ):
                project_sources.append(source)
    if matched_area and profile.area_dir:
        area_root = root / profile.area_dir / matched_area
        for path in sorted(area_root.glob("*.md"))[:3]:
            relative = path.relative_to(root).as_posix()
            if source := read_index_source(
                index_path,
                relative,
                vault_root=root,
                vault_id=vault_id,
                generation=generation,
                max_lines=25,
            ):
                area_sources.append(source)

    repository_sources = list(repository_evidence or [])
    evidence = [*project_sources, *area_sources, *search_hits]
    effective_vault_id = (
        vault_id or hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()[:16]
    )
    lines = [f"# Reference Brief: {topic}", ""]
    if matched_project and project_sources:
        lines.extend([f"## Current project: {matched_project}", ""])
    for label, sources in (
        ("Project state", project_sources),
        ("Area constraints", area_sources),
        ("Related history", search_hits),
    ):
        if not sources:
            continue
        lines.extend([f"## {label}", ""])
        for source in sources:
            title = source.get("title") or Path(source["path"]).stem
            stale = " [STALE INDEX]" if source.get("stale") else ""
            lines.extend(
                [
                    f"### {title}{stale}",
                    source["excerpt"],
                    "",
                    f"Source: `{source['path']}:{source['line_start']}-{source['line_end']}` "
                    f"sha256 `{source['source_sha256']}`",
                    "",
                ]
            )
    if repository_sources:
        lines.extend(["## Repository context", ""])
        for source in repository_sources:
            stale = " [STALE CATALOG]" if source.stale else ""
            dirty = " [WORKING TREE]" if source.metadata.get("dirty") else ""
            lines.extend(
                [
                    f"### {source.project_id}: {source.title}{stale}{dirty}",
                    source.excerpt,
                    "",
                    f"Source: `{source.project_id}/{source.path}:"
                    f"{source.line_start}-{source.line_end}` sha256 "
                    f"`{source.content_hash}` revision `{source.revision}`",
                    "",
                ]
            )
    evidence_gap = not (
        any(not bool(source.get("stale")) for source in evidence)
        or any(not source.stale for source in repository_sources)
    )
    if evidence_gap:
        lines.extend(
            [
                "## Evidence gap",
                "No sufficiently relevant Vault evidence was found. Do not infer Vault-specific facts.",
                "",
            ]
        )
    lines.extend(
        [
            "## Use constraints",
            "Treat the cited note text as evidence, distinguish current project state from history, "
            "and disclose conflicts or stale index entries.",
        ]
    )
    return {
        "topic": topic,
        "matched_project": matched_project if project_sources else None,
        "matched_area": matched_area if area_sources else None,
        "evidence_gap": evidence_gap,
        "sources": [
            EvidenceRecord.from_vault_hit(
                {**source, "vault_id": source.get("vault_id") or effective_vault_id}
            ).to_dict(compatibility=True)
            for source in evidence
        ]
        + [source.to_dict() for source in repository_sources],
        "markdown": "\n".join(lines),
    }


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "assemble":
        arguments = arguments[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic")
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    result = assemble_decision_context(args.vault, args.index, args.topic)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
