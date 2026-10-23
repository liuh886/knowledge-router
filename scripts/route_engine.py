#!/usr/bin/env python3
"""Vault-aware routing plans and an append/create-only mutation broker."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from capture_broker import (
    MutationResult,
    create_capture_plan,
)
from taxonomy_scanner import TaxonomyProfile, scan_taxonomy


@dataclass(slots=True)
class RoutingResult:
    intent: str
    action: str
    target_directory: str
    target_filename: str
    relative_target_path: str
    confidence: float
    reasoning: str
    suggested_tags: list[str] = field(default_factory=list)
    suggested_wikilinks: list[str] = field(default_factory=list)
    suggested_frontmatter: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now().astimezone()


def _slugify(text: str, max_len: int = 42) -> str:
    cleaned = re.sub(r"[^\w\u3400-\u9fff\- ]+", "", text).strip()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    return (cleaned or "note")[:max_len].strip("-") or "note"


def _stable_capture_id(text: str) -> str:
    stamp = _now().strftime("%Y%m%d%H%M%S%f")
    digest = hashlib.sha256(f"{stamp}:{text}".encode()).hexdigest()[:12]
    return f"kr-{stamp}-{digest}"


def _existing_directory(vault_root: Path, relative: str | None) -> str | None:
    if not relative:
        return None
    candidate = (vault_root / Path(relative)).resolve()
    try:
        candidate.relative_to(vault_root.resolve())
    except ValueError:
        return None
    return PurePosixPath(relative).as_posix() if candidate.is_dir() else None


def _capture_directory(vault_root: Path, profile: TaxonomyProfile) -> str:
    return _existing_directory(vault_root, profile.inbox_dir) or "."


def _log_directory(vault_root: Path, profile: TaxonomyProfile, period: str) -> str:
    base = _existing_directory(vault_root, profile.log_dir)
    if not base:
        return _capture_directory(vault_root, profile)
    child = _existing_directory(vault_root, f"{base}/{period}")
    return child or base


def _action_for(vault_root: Path, relative_path: str, preferred: str) -> str:
    return preferred if (vault_root / Path(relative_path)).is_file() else "create_new"


def resolve_route(
    text: str, profile: TaxonomyProfile, *, vault_root: Path | None = None
) -> RoutingResult:
    """Return a plan that reuses only directories already present in the Vault."""
    root = (vault_root or Path(profile.vault_root)).resolve()
    folded = text.casefold()
    now = _now()
    today = now.strftime("%Y-%m-%d")

    matched_project: str | None = None
    for project in profile.active_projects:
        simplified = re.sub(r"^\d{4}_", "", project).casefold()
        if project.casefold() in folded or (simplified and simplified in folded):
            matched_project = project
            break
    matched_area: str | None = None
    if not matched_project:
        for area in profile.active_areas:
            simplified = re.sub(r"^\d{3}_", "", area).casefold()
            if area.casefold() in folded or (simplified and simplified in folded):
                matched_area = area
                break

    log_words = (
        "今日",
        "今天",
        "本周",
        "本月",
        "日志",
        "日记",
        "周报",
        "月报",
        "汇报",
        "会议纪要",
        "daily",
        "journal",
        "meeting",
        "weekly",
        "report",
        "review",
    )
    progress_words = (
        "进展",
        "完成了",
        "进行中",
        "里程碑",
        "blocker",
        "progress",
        "milestone",
    )
    task_words = ("todo", "task", "待办", "任务", "bug", "issue")
    reference_words = (
        "文献",
        "论文",
        "参考",
        "模板",
        "摘录",
        "paper",
        "reference",
        "template",
        "citation",
        "cheatsheet",
    )
    idea_words = (
        "点子",
        "灵感",
        "闪念",
        "想法",
        "思路",
        "脑暴",
        "idea",
        "thought",
        "insight",
        "brainstorm",
    )
    is_log = any(word in folded for word in log_words)
    is_progress = any(word in folded for word in progress_words)
    is_task = any(word in folded for word in task_words)
    is_reference = any(word in folded for word in reference_words)
    is_idea = any(word in folded for word in idea_words)

    if matched_project:
        project_base = _existing_directory(root, profile.project_dir)
        project_dir = (
            _existing_directory(root, f"{project_base}/{matched_project}") if project_base else None
        )
        target_dir = project_dir or _capture_directory(root, profile)
        if is_progress:
            filename = "PROGRESS.md"
            intent = "project_progress"
            preferred = "append_existing"
        elif is_task:
            filename = "TASKS.md"
            intent = "project_task_update"
            preferred = "append_existing"
        else:
            filename = f"{today}-{_slugify(text)}.md"
            intent = "project_knowledge"
            preferred = "create_new"
        relative = PurePosixPath(target_dir, filename).as_posix()
        action = _action_for(root, relative, preferred)
        reasoning = f"匹配现有项目目录 {matched_project}"
        confidence = 0.95 if project_dir else 0.78
        tags = ["project", re.sub(r"^\d{4}_", "", matched_project).casefold()]
        links = [f"[[{matched_project}]]"]
    elif is_log and not is_idea:
        weekly = any(word in folded for word in ("周报", "本周", "weekly", "周总结"))
        target_dir = _log_directory(root, profile, "weekly" if weekly else "daily")
        filename = f"{now.strftime('%G-W%V') if weekly else today}.md"
        relative = PurePosixPath(target_dir, filename).as_posix()
        action = _action_for(root, relative, "append_existing")
        intent = "weekly_report" if weekly else "daily_log"
        reasoning = "匹配现有日志目录；不存在的 daily/weekly 子目录不会自动创建"
        confidence = 0.90
        tags = ["log", "weekly" if weekly else "daily"]
        links = []
    elif matched_area:
        area_base = _existing_directory(root, profile.area_dir)
        area_path = _existing_directory(root, f"{area_base}/{matched_area}") if area_base else None
        target_dir = area_path or _capture_directory(root, profile)
        filename = f"{_slugify(text)}.md"
        relative = PurePosixPath(target_dir, filename).as_posix()
        action = "create_new"
        intent = "area_knowledge"
        reasoning = f"匹配现有长期领域目录 {matched_area}"
        confidence = 0.88 if area_path else 0.76
        tags = ["area", re.sub(r"^\d{3}_", "", matched_area).casefold()]
        links = [f"[[{matched_area}]]"]
    elif is_reference:
        target_dir = _existing_directory(root, profile.resource_dir) or _capture_directory(
            root, profile
        )
        filename = f"{_slugify(text)}.md"
        relative = PurePosixPath(target_dir, filename).as_posix()
        action = "create_new"
        intent = "reference_asset"
        reasoning = "匹配现有资料目录；未检测到时回退到现有收件箱或仓库根目录"
        confidence = 0.85
        tags = ["resource", "reference"]
        links = []
    else:
        target_dir = _capture_directory(root, profile)
        filename = f"{today}-{_slugify(text)}.md"
        relative = PurePosixPath(target_dir, filename).as_posix()
        action = "create_new"
        intent = "fleeting_idea" if is_idea else "unclassified_capture"
        reasoning = "未可靠匹配项目或领域，回退到现有收件箱或仓库根目录"
        confidence = 0.80 if profile.inbox_dir else 0.65
        tags = ["inbox", "idea"] if is_idea else ["inbox"]
        links = []

    frontmatter = {
        "id": _stable_capture_id(text),
        "date": today,
        "tags": tags,
        "intent": intent,
    }
    return RoutingResult(
        intent=intent,
        action=action,
        target_directory=target_dir,
        target_filename=filename,
        relative_target_path=relative,
        confidence=confidence,
        reasoning=reasoning,
        suggested_tags=tags,
        suggested_wikilinks=links,
        suggested_frontmatter=frontmatter,
    )


def execute_capture(
    vault_root: Path,
    state_dir: Path,
    content: str,
    routing: RoutingResult,
    *,
    title: str | None = None,
    expected_sha256: str | None = None,
    idempotency_key: str | None = None,
) -> MutationResult:
    """Compatibility API using the same commit/index boundary as the CLI."""
    from mutation_service import commit_and_refresh
    from registry import VaultRegistry

    plan = create_capture_plan(
        vault_root,
        state_dir,
        content,
        routing,
        title=title,
        expected_sha256=expected_sha256,
        idempotency_key=idempotency_key,
    )
    registry = VaultRegistry(state_dir.expanduser().resolve().parent)
    config = registry.resolve(state_dir.name)
    result = commit_and_refresh(registry, config, plan)
    if result["status"] == "applied_pending_index":
        raise RuntimeError(f"{result['message']}: {result['index_error']}")
    return MutationResult(**result["mutation"])


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "resolve":
        arguments = arguments[1:]
    elif arguments and arguments[0] == "capture":
        print(
            "Direct capture is disabled; use knowledge_router.py capture so saved policy applies.",
            file=sys.stderr,
        )
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text")
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(arguments)
    profile = scan_taxonomy(args.vault)
    routing = resolve_route(args.text, profile, vault_root=args.vault)
    if args.json:
        print(json.dumps(routing.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Target: {routing.relative_target_path}")
        print(f"Action: {routing.action}; confidence: {routing.confidence:.2f}")
        print(routing.reasoning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
