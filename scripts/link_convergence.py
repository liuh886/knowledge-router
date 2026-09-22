#!/usr/bin/env python3
"""Collapse repeated unresolved link targets into one decision each.

Broken links are reported per occurrence, but a name repeated two hundred times is a
single decision, not two hundred findings. This groups unresolved targets inside a
maintained Context scope and proposes what to do about each, carrying the evidence that
produced the suggestion.

Nothing here writes to the Vault. The only action it can stage hands the payload to the
existing capture broker, which stays a separate, explicit preview/commit step.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from context_manager import extract_tags, note_in_context
from capture_broker import create_capture_plan
from fts_indexer import scan_notes
from link_resolver import (
    STATUS_AMBIGUOUS,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    LinkResolver,
    extract_links,
)
from registry import VaultConfig, VaultRegistry
from vault_doctor import NOISY_HINTS, diagnose_vault, group_targets

RECOMMEND_NAMING_VARIANT = "check_naming_variant"
RECOMMEND_CREATE_OR_RELINK = "create_or_relink"
RECOMMEND_JUDGEMENT = "needs_judgement"

# Normalised names shorter than this are too generic to compare safely: "a", "1" or a
# bare year would match half the Vault. Such targets are reported without a suggestion.
_MIN_NORMALISED = 3

_SAMPLE_SOURCES = 3


def normalise_name(name: str) -> str:
    """Casefold and drop separators so a naming variant is recognisable.

    `[[Digital Carbon]]` and `Digital_Carbon.md` are the same intent, but Obsidian's
    default resolution does not treat a space and an underscore as equal, so the link
    stays broken. Unicode word characters are kept, so CJK names are compared on their
    actual text rather than collapsing to their leading digits.
    """
    return re.sub(r"[\W_]+", "", name.casefold())


@dataclass(frozen=True)
class ConvergenceItem:
    """One unresolved target, its scope, and a suggested next step."""

    target: str
    variants: tuple[str, ...]
    occurrences: int
    sources: tuple[str, ...]
    triage_hint: str | None
    context_ids: tuple[str, ...]
    candidate_host: str | None
    candidate_reason: str | None
    recommended: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "variants": list(self.variants),
            "occurrences": self.occurrences,
            "sources": list(self.sources),
            "triage_hint": self.triage_hint,
            "context_ids": list(self.context_ids),
            "candidate_host": self.candidate_host,
            "candidate_reason": self.candidate_reason,
            "recommended": self.recommended,
            "reason": self.reason,
        }


def _note_index(scan) -> dict[str, list[str]]:
    """Map a normalised name to the note stems that normalise to it."""
    index: dict[str, list[str]] = defaultdict(list)
    for record in scan.records:
        stem = Path(record.path).stem
        key = normalise_name(stem)
        if key and stem not in index[key]:
            index[key].append(stem)
    return index


def _scope_ids(path: str, contexts, tags_by_path) -> list[str]:
    return sorted(
        row["id"] for row in contexts if note_in_context(path, tags_by_path.get(path, ()), row)
    )


@dataclass(frozen=True)
class ConvergenceReport:
    """Convergence items plus what was deliberately left out of the proposal."""

    items: tuple[ConvergenceItem, ...]
    scope: str
    excluded_noisy_targets: int
    excluded_noisy_occurrences: int
    configured: bool = True

    def to_dict(self) -> dict[str, Any]:
        by_recommendation: dict[str, int] = {}
        for item in self.items:
            by_recommendation[item.recommended] = (
                by_recommendation.get(item.recommended, 0) + 1
            )
        return {
            "configured": self.configured,
            "target_count": len(self.items),
            "occurrences_total": sum(item.occurrences for item in self.items),
            "counts_by_recommendation": by_recommendation,
            "excluded_noisy_targets": self.excluded_noisy_targets,
            "excluded_noisy_occurrences": self.excluded_noisy_occurrences,
        }


def converge_targets(
    config: VaultConfig,
    contexts: list[dict[str, Any]],
    scan,
    *,
    context_id: str | None = None,
    min_occurrences: int = 1,
    include_noisy: bool = False,
) -> ConvergenceReport:
    """Group in-scope unresolved targets and suggest one step per distinct name.

    Distinctness is case-insensitive: `CCS` and `ccs` are the same missing note. Only
    targets reachable from a reviewed Context scope are returned; unscoped findings stay
    visible through `quality`.

    Machine-generated noise (Zotero reference dumps, template placeholders) is excluded
    unless `include_noisy` is asked for. Those hints are reported by `doctor` and remain
    available there, so excluding them here narrows what is *proposed for decision* and
    never hides that they exist.
    """
    if not contexts:
        # Nothing is registered, so "no targets" would be a claim about a check that never ran.
        return ConvergenceReport((), context_id or "*", 0, 0, configured=False)
    if context_id and not any(str(row.get("id")) == context_id for row in contexts):
        raise ValueError(f"No registered Context with id {context_id!r}")
    if min_occurrences < 1:
        raise ValueError("min_occurrences must be at least 1")

    doctor = diagnose_vault(config.root_path, source_scan=scan)
    tags_by_path = {record.path: extract_tags(record.content) for record in scan.records}
    index = _note_index(scan)

    def in_scope(row: dict[str, Any]) -> bool:
        path = str(row.get("source", ""))
        return any(
            note_in_context(path, tags_by_path.get(path, ()), definition)
            for definition in contexts
        )

    # Filter the FULL occurrence stream first. Deciding from the truncated sample would drop
    # a target whose only in-scope source is, say, its twenty-first occurrence, and would let
    # an out-of-scope majority decide the hint this scope is judged by.
    occurrences = doctor.get("broken_links", [])
    scoped_groups = group_targets(occurrences, include=in_scope)
    vault_totals = {
        str(row["target"]): int(row["occurrences"])
        for row in doctor.get("broken_targets", [])
    }

    grouped: dict[str, dict[str, Any]] = {}
    excluded: dict[str, int] = {"targets": 0, "occurrences": 0}
    if not include_noisy:
        noisy = group_targets(
            occurrences,
            include=lambda row: in_scope(row) and row.get("triage_hint") in NOISY_HINTS,
        )
        excluded = {
            "targets": len(noisy),
            "occurrences": sum(int(row["occurrences"]) for row in noisy),
        }

    for entry in scoped_groups:
        target = str(entry["target"])
        hint = entry.get("triage_hint")
        if not include_noisy and hint in NOISY_HINTS:
            continue
        sources = [str(item) for item in entry.get("sources", [])]
        ids = sorted(
            {
                cid
                for source in sources
                for cid in _scope_ids(source, contexts, tags_by_path)
                if not context_id or cid == context_id
            }
        )
        if not ids:
            continue
        count = int(entry["occurrences"])
        # `CCS` and `ccs` are one missing note, so spellings accumulate under one key.
        row = grouped.setdefault(
            target.casefold(),
            {
                "occurrences": 0,
                "spellings": Counter(),
                "sources": [],
                "hints": Counter(),
                "context_ids": set(),
                "vault_occurrences": 0,
            },
        )
        row["occurrences"] += count
        row["spellings"][target] += count
        row["hints"][hint] += count
        row["vault_occurrences"] += vault_totals.get(target, count)
        for path in sources:
            if path not in row["sources"] and len(row["sources"]) < _SAMPLE_SOURCES:
                row["sources"].append(path)
        row["context_ids"].update(ids)

    items: list[ConvergenceItem] = []
    for key, row in grouped.items():
        if row["occurrences"] < min_occurrences:
            continue
        target = row["spellings"].most_common(1)[0][0]
        variants = tuple(sorted(row["spellings"]))
        hint = row["hints"].most_common(1)[0][0]
        candidate, reason, recommended, explanation = _suggest(target, index, row["occurrences"])
        items.append(
            ConvergenceItem(
                target=target,
                variants=variants,
                occurrences=row["occurrences"],
                sources=tuple(row["sources"]),
                triage_hint=hint,
                context_ids=tuple(sorted(row["context_ids"])),
                candidate_host=candidate,
                candidate_reason=reason,
                recommended=recommended,
                reason=explanation,
            )
        )
    items.sort(key=lambda item: (-item.occurrences, item.target))
    return ConvergenceReport(
        items=tuple(items),
        scope=context_id or "*",
        excluded_noisy_targets=excluded["targets"],
        excluded_noisy_occurrences=excluded["occurrences"],
    )


def _suggest(
    target: str, index: dict[str, list[str]], occurrences: int
) -> tuple[str | None, str | None, str, str]:
    """Suggest a next step without claiming the suggestion is the answer."""
    key = normalise_name(target)
    if len(key) < _MIN_NORMALISED:
        return (
            None,
            None,
            RECOMMEND_JUDGEMENT,
            f"'{target}' 过于通用，无法安全推断意图；需人工判断是新建笔记还是修正链接",
        )
    matches = index.get(key, [])
    if len(matches) == 1:
        host = matches[0]
        return (
            host,
            "唯一同名变体",
            RECOMMEND_NAMING_VARIANT,
            f"{occurrences} 处链接写作 '{target}'，而库内同名变体是 '{host}'；"
            f"确认后修正链接或为该笔记补别名",
        )
    if len(matches) > 1:
        return (
            None,
            f"{len(matches)} 个同名变体候选",
            RECOMMEND_JUDGEMENT,
            f"'{target}' 在库内有多个同名变体候选（{'、'.join(matches[:3])}），需人工选定",
        )
    return (
        None,
        None,
        RECOMMEND_CREATE_OR_RELINK,
        f"{occurrences} 处链接指向 '{target}'，但库内没有相似名称的笔记；"
        f"需新建该笔记，或修正这些链接",
    )


def format_convergence(report: ConvergenceReport) -> str:
    """Render the convergence list for a human reader."""
    lines = [f"# 链接收敛（scope: {report.scope}）", f"待决目标 {len(report.items)} 个"]
    if not report.configured:
        lines.append("⚠ 没有已登记的 Context：本次没有可收敛的范围，结论不成立")
        return "\n".join(lines)
    if report.excluded_noisy_targets:
        lines.append(
            f"（已排除机器生成噪声 {report.excluded_noisy_targets} 个目标 / "
            f"{report.excluded_noisy_occurrences} 处：Zotero 引用与模板占位；"
            f"如需查看用 --include-noisy）"
        )
    if not report.items:
        lines.append("没有可收敛的范围内断链目标。")
        return "\n".join(lines)
    lines.append(f"建议分布：{dict(Counter(item.recommended for item in report.items))}")
    for item in report.items:
        lines.append(
            f"- [{item.recommended}] '{item.target}' · {item.occurrences} 处 · "
            f"变体 {list(item.variants)}"
        )
        lines.append(f"    {item.reason}")
        if item.candidate_host:
            lines.append(f"    候选笔记：{item.candidate_host}")
        if item.sources:
            lines.append(f"    示例来源：{', '.join(item.sources)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class _StubRouting:
    """Minimal RoutingLike shape for creating the note a link points at."""

    action: str
    relative_target_path: str
    target_filename: str
    suggested_frontmatter: dict[str, Any]
    suggested_wikilinks: list[str]


STUB_BODY = "## 待补充\n\n（此笔记为让链接可解析而建立，内容待补。）"


def stage_missing_note(
    registry: VaultRegistry,
    config: VaultConfig,
    target: str,
    target_path: str,
    *,
    content: str | None = None,
    title: str | None = None,
    idempotency_key: str | None = None,
    scan=None,
) -> dict[str, Any]:
    """Stage a broker plan that creates the note an unresolved link points at.

    Preview only: the returned plan must be committed through the ordinary capture path
    (`capture --plan-id <id> --commit`), which is still gated by the Vault's write policy.

    Before staging, the planned path is re-resolved with the shared resolver to check that it
    would actually resolve the unresolved occurrences of `target`. A create that resolves
    nothing is refused: the write would succeed, the link would stay broken, and the result
    would look like a fix. The same check refuses a path that already exists, because the
    broker would then pick a unique sibling name and the links would still not resolve.
    """
    relative = PurePosixPath(str(target_path).replace("\\", "/")).as_posix()
    if not relative or relative.startswith("/") or relative.startswith("../"):
        raise ValueError("target path must be a relative path inside the Vault")
    if PurePosixPath(relative).suffix.lower() != ".md":
        raise ValueError("target path must end in .md")
    if not str(target).strip():
        raise ValueError("target name cannot be empty")
    destination = config.root_path / PurePosixPath(relative)
    if destination.exists():
        raise FileExistsError(
            f"{relative} already exists; creating a sibling would not resolve '{target}'"
        )

    report = scan or scan_notes(config.root_path)
    resolver = LinkResolver.from_scan(report, vault_root=config.root_path)
    planned = resolver.with_planned_note(relative)
    resolved_samples: list[str] = []
    still_broken = 0
    becomes_ambiguous = 0
    for record in report.records:
        for reference in extract_links(record.path, record.content):
            if resolver.resolve(reference).status != STATUS_UNRESOLVED:
                continue
            decision = planned.resolve(reference)
            if decision.status == STATUS_RESOLVED:
                if len(resolved_samples) < 10:
                    resolved_samples.append(
                        f"{record.path}:{reference.line} [[{reference.target}]]"
                    )
            elif decision.status == STATUS_AMBIGUOUS:
                becomes_ambiguous += 1
            else:
                still_broken += 1
    if not resolved_samples and still_broken:
        raise ValueError(
            f"Creating {relative} would not make any unresolved link resolve "
            f"({still_broken} would stay unresolved). Choose the path the links actually point at."
        )

    routing = _StubRouting(
        action="create_new",
        relative_target_path=relative,
        target_filename=PurePosixPath(relative).name,
        suggested_frontmatter={
            "title": title or target,
            "created_for": f"resolve unresolved link {target}",
        },
        suggested_wikilinks=[],
    )
    plan = create_capture_plan(
        config.root_path,
        registry.vault_state_dir(config),
        content or STUB_BODY,
        routing,
        title=title or target,
        idempotency_key=idempotency_key,
    )
    if plan.relative_path != relative:
        raise RuntimeError(
            f"broker chose {plan.relative_path} instead of {relative}; "
            "refusing a create that would not resolve the link"
        )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "staged",
        "target": target,
        "requested_path": relative,
        "plan_id": plan.plan_id,
        "action": plan.action,
        "relative_path": plan.relative_path,
        "write_policy": config.write_policy,
        "resolved_occurrence_samples": resolved_samples,
        "resolved_occurrence_count": len(resolved_samples),
        "still_unresolved_after_create": still_broken,
        "would_become_ambiguous": becomes_ambiguous,
        "vault_writes": 0,
        "next_step": f"capture --vault {config.name} --plan-id {plan.plan_id} --commit",
        "meaning": (
            "A frozen capture plan, nothing written. The planned path was re-resolved with the "
            "shared resolver, so the occurrences it would fix are listed before anything is "
            "committed. Committing is a separate explicit step and still requires the Vault's "
            "write policy to allow it."
        ),
    }
