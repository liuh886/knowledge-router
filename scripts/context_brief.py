"""Live, scoped, extractive Context briefs with source checks and abstention."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from context_events import context_history
from context_manager import discover_contexts, extract_tags, note_in_context
from context_state import project_state
from context_store import load_context_version
from fts_indexer import extract_query_terms, scan_notes
from link_graph import anchor_linked_paths, load_link_graph
from project_adapter import list_projects, search_projects
from registry import VaultConfig, VaultRegistry
from secure_io import secure_read_bytes


def brief_context(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    *,
    query: str = "",
    limit: int = 8,
    project_id: str | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 30:
        raise ValueError("Brief limit must be between 1 and 30")
    if project_id is not None and (not project_id.strip() or not query.strip()):
        raise ValueError("Project evidence requires an explicit project id and query")
    version = load_context_version(registry, config)
    scan = scan_notes(config.root_path)
    catalog = discover_contexts(
        scan,
        vault_id=config.id,
        definitions=version["definitions"] if version else None,
    )
    contexts = catalog["contexts"]
    exact = [c for c in contexts if c["id"] == selector]
    matches = exact or [
        c
        for c in contexts
        if selector.casefold()
        in {c["name"].casefold(), *(a.casefold() for a in c["aliases"])}
    ]
    if len(matches) != 1:
        raise ValueError(
            "Context is missing or ambiguous; use its exact id from contexts discover"
        )
    context = matches[0]
    history = context_history(registry, config, context["id"], limit=100)
    linked_paths = {event["path"] for event in history["events"]}
    tags_by_path = {n.path: extract_tags(n.content) for n in scan.records}
    notes = [
        n
        for n in scan.records
        if n.path in linked_paths
        or note_in_context(n.path, tags_by_path[n.path], context)
    ]
    affinity = anchor_linked_paths(load_link_graph(registry, config), context["scopes"])
    terms = extract_query_terms(query) if query.strip() else []
    candidates = []
    for note in notes:
        lines = note.content.splitlines()
        # Small source windows preserve exact line references and bounded model input.
        for start in range(0, len(lines), 10):
            excerpt = "\n".join(lines[start : start + 10])
            if not excerpt.strip():
                continue
            folded = excerpt.casefold()
            score = sum(term.casefold() in folded for term in terms)
            if query.strip() and (not terms or not score):
                continue
            # Without a question, prioritize explicit state/task/decision passages.
            category = "reference"
            for name, pattern in (
                ("open_tasks", r"(?m)^\s*[-*] \[ \]"),
                ("decisions", r"(?i)\bdecision\b|决定|决策"),
                ("progress", r"(?i)\b(progress|status)\b|进展|当前状态"),
            ):
                if re.search(pattern, excerpt):
                    category = name
                    break
            candidates.append(
                (
                    score,
                    int(note.path in affinity),
                    category != "reference",
                    note.mtime_ns,
                    note.path,
                    start,
                    excerpt,
                    category,
                    note,
                )
            )
    candidates.sort(
        key=lambda item: (-item[0], -item[1], -item[2], -item[3], item[4], item[5])
    )
    evidence = []
    unavailable = set()
    verified: dict[str, bool] = {}
    for _, _, _, _, _, start, excerpt, category, note in candidates:
        if note.path not in verified:
            try:
                raw = secure_read_bytes(config.root_path, note.path)
                verified[note.path] = hashlib.sha256(raw).hexdigest() == note.checksum
            except (OSError, ValueError):
                verified[note.path] = False
        if not verified[note.path]:
            unavailable.add(note.path)
            continue
        evidence.append(
            {
                "vault_id": config.id,
                "path": note.path,
                "title": note.title,
                "line_start": start + 1,
                "line_end": min(start + 10, len(note.content.splitlines())),
                "source_sha256": note.checksum,
                "stale": False,
                "excerpt": excerpt[:1600],
                "excerpt_truncated": len(excerpt) > 1600,
                "category": category,
                "modified_at": note.modified_at,
            }
        )
        if len(evidence) == limit:
            break
    result = {
        "schema_version": 1,
        "vault_id": config.id,
        "context": context,
        "definition_revision": version["revision"] if version else None,
        "query": query,
        "source_count": len(notes),
        "graph_affinity_used": bool(affinity),
        "graph_affinity_source_count": sum(1 for n in notes if n.path in affinity),
        "evidence": evidence,
        "linked_event_count": history["event_count"],
        "linked_events_truncated": history["truncated"],
        "evidence_gap": not evidence,
        "sources_changed_during_read": sorted(unavailable)[:limit],
        "source_generation": catalog["source_generation"],
        "brief_kind": "extractive_live_reference",
        "recorded_state": project_state(registry, config, context["id"]),
        "limitations": "Passages are source evidence, not reconciled current state. Modification time is not event time; conflicts and completed tasks require interpretation.",
    }
    bound_project_ids = [str(item) for item in context.get("project_ids", [])]
    selected_project_ids = [project_id] if project_id is not None else bound_project_ids
    if selected_project_ids and query.strip():
        known_project_ids = {p["project_id"] for p in list_projects(registry, config)}
        unknown = sorted(set(selected_project_ids) - known_project_ids)
        if unknown:
            raise ValueError(
                "Unknown project id; choose an exact id from projects list: "
                + ", ".join(unknown)
            )
        records = [
            record
            for selected in selected_project_ids
            for record in search_projects(
                registry, config, query, limit=limit, project_id=selected
            )
        ]
        origin = "explicit_request" if project_id is not None else "reviewed_binding"
        selection = {
            "project_ids": selected_project_ids,
            "origin": origin,
            "persisted_relationship": origin == "reviewed_binding",
        }
        if len(selected_project_ids) == 1:
            selection["project_id"] = selected_project_ids[0]
        result.update(
            {
                "project_selection": selection,
                "project_evidence": [record.to_dict() for record in records],
                "project_evidence_gap": not records,
                "combined_evidence_gap": not evidence and not records,
                "evidence_limit_per_source_type": limit,
            }
        )
    return result


def format_context_brief(brief: dict[str, Any]) -> str:
    lines = [f"# {brief['context']['name']}", brief["context"]["purpose"], ""]
    for field in brief.get("recorded_state", {}).get("fields", []):
        if field["status"] == "current":
            source = field["candidates"][0]["source"]
            lines.extend(
                [
                    f"已记录 {field['field']}：{field['value']}",
                    f"来源：{source['path']}:{source['line_start']} "
                    f"提交时 sha256={source['source_sha256_at_commit']}",
                ]
            )
        else:
            lines.append(f"{field['field']}：存在冲突或来源不可用，暂不判断当前值。")
    if brief["evidence_gap"]:
        lines.append("该 Context 的 Vault 原文没有匹配证据，不能据此判断其状态。")
    for item in brief["evidence"]:
        lines.extend(
            [
                item["excerpt"],
                f"来源：{item['path']}:{item['line_start']}-{item['line_end']} "
                f"sha256={item['source_sha256']}",
                "",
            ]
        )
    if "project_selection" in brief:
        lines.append("## 显式选择的 Git 项目资料（不代表 Context 当前状态）")
        if brief["project_evidence_gap"]:
            lines.append("所选项目未找到匹配资料。")
        for item in brief["project_evidence"]:
            lines.extend(
                [
                    item["excerpt"],
                    f"仓库：{item['metadata']['repo_root']} 项目：{item['project_id']}",
                    f"来源：{item['path']}:{item['line_start']}-{item['line_end']} "
                    f"sha256={item['content_hash']} revision={item['revision']}",
                    "来源已变化或不可读取：这是索引中的历史摘录，需重新核实。"
                    if item["stale"]
                    else "该摘录已与当前源文件核对。",
                    "",
                ]
            )
    lines.append(
        "以上为来源资料摘录；历史记录不自动代表当前状态，来源过期标记须单独留意。"
    )
    return "\n".join(lines)
