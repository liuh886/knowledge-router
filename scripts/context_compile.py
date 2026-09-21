#!/usr/bin/env python3
"""Compiled Context cards: read-only bundles and externally staged drafts.

Compilation folds scattered evidence into one dense "current state" card per
Context. A bundle is assembled read-only and never synthesizes a judgement; the
template scaffolds prose that must be written only from cited sources. Staging
writes only an external broker plan, so the Vault is written solely by the
existing capture commit under an explicit append-create policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from capture_broker import create_capture_plan
from context_brief import brief_context
from context_followups import list_followups
from context_store import load_context_version
from link_graph import load_link_graph, scope_anchors, select_context
from registry import VaultConfig, VaultRegistry, atomic_text_write

_COMPILE_DIR = "_compiled"
_PLACEHOLDER = "（结合下方来源，用要点写 3–7 条；不要写来源中不存在的结论）"


@dataclass(frozen=True, slots=True)
class _CompiledRouting:
    action: str
    relative_target_path: str
    target_filename: str
    suggested_frontmatter: dict[str, Any]
    suggested_wikilinks: list[str]


def _slug(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "context"


def suggest_target(context: dict[str, Any]) -> str:
    bases = [scope for scope in context["scopes"] if not scope.endswith(".md")]
    base = bases[0] if bases else "."
    folder = f"{base.rstrip('/')}/{_COMPILE_DIR}" if base != "." else _COMPILE_DIR
    return f"{folder}/{_slug(context['name'])}.md"


def _open_items(registry, config, context_id: str, limit: int) -> list[dict[str, Any]]:
    followups = list_followups(registry, config, context_id, limit=limit)
    return [
        {
            "id": item["id"],
            "title": item.get("title", item["id"]),
            "status": item.get("status"),
            "due_at": item.get("due_at"),
            "due": item.get("due"),
        }
        for item in followups["items"]
        if item.get("status") in {"open", "needs_review"}
    ]


def _current_fields(recorded_state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        field
        for field in recorded_state.get("fields", [])
        if field.get("status") == "current" and not field["field"].startswith("followup_")
    ]


def _template(
    context: dict[str, Any],
    *,
    recorded_state: dict[str, Any],
    open_followups: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    key_evidence: list[dict[str, Any]],
    source_count: int,
) -> str:
    lines = [
        f"# {context['name']}",
        "",
        f"> 状态：{_PLACEHOLDER}",
        f"> 更新：{datetime.now(UTC).date().isoformat()} | 来源：{source_count} 条",
        "",
        "## 当前状态",
    ]
    state_fields = _current_fields(recorded_state)
    if state_fields:
        lines.extend(f"- {field['field']}: {field['value']}" for field in state_fields)
    else:
        lines.append(_PLACEHOLDER)
    lines += ["", "## 关键进展"]
    highlights = [
        item
        for item in key_evidence
        if item["category"] in {"progress", "decisions", "open_tasks"}
    ]
    if highlights:
        for item in highlights[:5]:
            first = item["excerpt"].splitlines()[0].strip() if item["excerpt"] else ""
            lines.append(
                f"- `{item['path']}:{item['line_start']}-{item['line_end']}` — {first[:120]}"
            )
    else:
        lines.append(_PLACEHOLDER)
    lines += ["", "## 待决 / 风险"]
    if open_followups:
        lines.extend(
            f"- [ ] {item['title']} — 到期 {item.get('due_at') or '未定'}"
            for item in open_followups
        )
    else:
        lines.append(_PLACEHOLDER)
    if anchors:
        lines += ["", "## 主题锚点"]
        lines.extend(f"- [[{PurePosixPath(item['path']).stem}]]" for item in anchors[:5])
    lines += ["", "## 来源（ground truth）"]
    seen: list[str] = []
    for item in key_evidence:
        if item["path"] in seen:
            continue
        seen.append(item["path"])
        lines.append(
            f"- [[{PurePosixPath(item['path']).stem}]] "
            f"`{item['path']}:{item['line_start']}-{item['line_end']}` "
            f"sha256={item['source_sha256'][:12]}…"
        )
    if not seen:
        lines.append("（无匹配来源）")
    return "\n".join(lines)


def _default_query(context: dict[str, Any]) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for value in (context["name"], *(context.get("aliases") or [])):
        key = str(value).casefold()
        if key and key not in seen:
            seen.add(key)
            parts.append(str(value))
    return " ".join(parts)


def compile_context(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    *,
    limit: int = 8,
    target: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Assemble a read-only compiled-card bundle with a cited scaffold."""
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Compile limit must be between 1 and 30")
    version = load_context_version(registry, config)
    if version is None:
        raise ValueError("No registered Context definitions; run contexts register first")
    context = select_context(version["definitions"]["contexts"], selector)
    effective_query = query if query is not None else _default_query(context)
    brief = brief_context(
        registry, config, context["id"], query=effective_query, limit=limit
    )
    followups = _open_items(registry, config, context["id"], limit)
    graph = load_link_graph(registry, config)
    anchors = scope_anchors(graph, context["scopes"], limit=limit)
    suggested = target or suggest_target(context)
    try:
        area = compiled_root(config)
        output_path = area / f"{_slug(context['name'])}.md"
        compiled_area, suggested_output = str(area), str(output_path)
        existing_output = output_path.is_file()
    except (OSError, ValueError):
        compiled_area, suggested_output, existing_output = "", "", False
    template = _template(
        context,
        recorded_state=brief["recorded_state"],
        open_followups=followups,
        anchors=anchors,
        key_evidence=brief["evidence"],
        source_count=brief["source_count"],
    )
    target_path = config.root_path / PurePosixPath(suggested)
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "compiled_bundle",
        "context": context,
        "definition_revision": brief["definition_revision"],
        "source_generation": brief["source_generation"],
        "source_count": brief["source_count"],
        "query": effective_query,
        "recorded_state": brief["recorded_state"],
        "open_followups": followups,
        "anchors": anchors,
        "key_evidence": brief["evidence"],
        "graph_affinity_used": brief["graph_affinity_used"],
        "compiled_area": compiled_area,
        "suggested_output": suggested_output,
        "existing_output": existing_output,
        "suggested_target": suggested,
        "suggested_target_directory_exists": target_path.parent.is_dir(),
        "existing_card": target_path.is_file(),
        "template": template,
        "writes_performed": 0,
        "limitations": (
            "This is a cited scaffold, not a synthesized judgement. Fill the prose only from "
            "the listed sources; unresolved conflicts stay visible and are never auto-resolved."
        ),
    }


def _strip_leading_heading(content: str) -> str:
    """Drop one leading H1 so the broker's own title heading is not duplicated."""
    lines = content.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].lstrip().startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        return "\n".join(lines[index:])
    return content


def stage_compiled_card(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    content: str,
    *,
    target: str | None = None,
    title: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Freeze one reviewed compiled card as an external broker plan (no Vault write)."""
    if not content.strip():
        raise ValueError("Compiled card content cannot be empty")
    version = load_context_version(registry, config)
    if version is None:
        raise ValueError("No registered Context definitions; run contexts register first")
    context = select_context(version["definitions"]["contexts"], selector)
    relative = target or suggest_target(context)
    anchors = scope_anchors(load_link_graph(registry, config), context["scopes"], limit=3)
    routing = _CompiledRouting(
        action="create_new",
        relative_target_path=relative,
        target_filename=PurePosixPath(relative).name,
        suggested_frontmatter={
            "title": title or context["name"],
            "context_id": context["id"],
            "compiled": "true",
        },
        suggested_wikilinks=[
            f"[[{PurePosixPath(item['path']).stem}]]" for item in anchors
        ],
    )
    plan = create_capture_plan(
        config.root_path,
        registry.vault_state_dir(config),
        _strip_leading_heading(content),
        routing,
        title=title or context["name"],
        idempotency_key=idempotency_key,
    )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "staged",
        "context": {"id": context["id"], "name": context["name"]},
        "write_policy": config.write_policy,
        "plan_id": plan.plan_id,
        "action": plan.action,
        "relative_path": plan.relative_path,
        "payload_sha256": plan.payload_sha256,
        "expires_at": plan.expires_at,
        "vault_writes": 0,
        "commit_command": (
            f"capture --vault {config.name} --commit --plan-id {plan.plan_id}"
        ),
    }


def compiled_root(config: VaultConfig, out_root: str | None = None) -> Path:
    """Resolve the external compiled area, which must stay outside the Vault.

    Defaults to a sibling directory ``<vault>_compiled`` next to the Vault so the
    cards are visible next to the knowledge base without writing into it.
    """
    if out_root:
        root = Path(out_root).expanduser().absolute()
    else:
        parent = config.root_path.parent
        root = parent / f"{config.root_path.name}_compiled"
    root = root.resolve()
    vault_root = config.root_path.resolve()
    if root == vault_root or vault_root in root.parents:
        raise ValueError("Compiled area must stay outside the Vault")
    if any((ancestor / ".obsidian").is_dir() for ancestor in (root, *root.parents)):
        raise ValueError("Compiled area must not be inside an Obsidian Vault")
    if not root.parent.is_dir():
        raise ValueError(f"Compiled area parent must already exist: {root.parent}")
    return root


def _render_card(context: dict[str, Any], content: str, title: str) -> str:
    header = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"context_id: {json.dumps(context['id'], ensure_ascii=False)}",
        'compiled: "true"',
        "---",
        "",
        f"# {title}",
        "",
    ]
    return "\n".join(header) + "\n" + _strip_leading_heading(content).strip() + "\n"


def write_compiled_card(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    content: str,
    *,
    name: str | None = None,
    out_root: str | None = None,
    overwrite: bool = False,
    title: str | None = None,
) -> dict[str, Any]:
    """Write one compiled card into the external compiled area (never the Vault)."""
    if not content.strip():
        raise ValueError("Compiled card content cannot be empty")
    version = load_context_version(registry, config)
    if version is None:
        raise ValueError("No registered Context definitions; run contexts register first")
    context = select_context(version["definitions"]["contexts"], selector)
    root = compiled_root(config, out_root)
    filename = name or f"{_slug(context['name'])}.md"
    if not filename.endswith(".md"):
        filename = f"{filename}.md"
    if filename != PurePosixPath(filename).name or filename.startswith("."):
        raise ValueError("Compiled card name must be a single Markdown file name")
    target = (root / filename).resolve()
    if root not in target.parents:
        raise ValueError("Compiled card name must stay inside the compiled area")
    existed = target.exists()
    if existed and not overwrite:
        raise FileExistsError(
            f"Compiled card already exists: {target} (use --overwrite to replace)"
        )
    card_title = title or context["name"]
    body = _render_card(context, content, card_title)
    atomic_text_write(target, body)
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "written",
        "context": {"id": context["id"], "name": context["name"]},
        "compiled_area": str(root),
        "path": str(target),
        "replaced": existed,
        "card_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "vault_writes": 0,
        "meaning": (
            "Written outside the Vault; no Vault authorization is involved. Re-run compile "
            "to refresh the scaffold after scoped sources change."
        ),
    }


def format_compile_bundle(bundle: dict[str, Any]) -> str:
    """Render a human-readable compiled-card bundle with its scaffold."""
    context = bundle["context"]
    lines = [
        f"# 编译包：{context['name']} ({context['id']})",
        f"来源 {bundle['source_count']} 条 · 证据 {len(bundle['key_evidence'])} 条 · "
        f"锚点 {len(bundle['anchors'])} 个 · 待决 {len(bundle['open_followups'])} 项",
    ]
    if bundle.get("suggested_output"):
        lines.append(
            f"外部编译区：{bundle['compiled_area']}（不碰 Vault）"
            f" · 目标：{bundle['suggested_output']}"
            f" · 已存在：{'是' if bundle['existing_output'] else '否'}"
        )
    lines += [
        "",
        "## 模板（用来源填写，勿编造）",
        "",
        bundle["template"],
        "",
        "填写后写入外部编译区：",
        "contexts compile <id> --save --content-file <file> [--name <file.md>] [--overwrite]",
        "如需写入 Vault 内（需 append-create 授权）：--stage --content-file <file> --target <相对路径>",
    ]
    return "\n".join(lines)
