#!/usr/bin/env python3
"""Bounded review agenda plus a durable external deposition ledger.

The agenda merges maintenance, due follow-ups, in-scope quality findings and
stale compiled cards into one prioritized list. Each run is appended to an
external ledger so successive reviews can be compared over time. The Vault is
only read; nothing is repaired, deleted or rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from context_compile import compiled_card_name, compiled_root, suggest_target
from context_manager import extract_tags, note_in_context
from context_store import load_context_version
from fts_indexer import scan_notes
from registry import FileLock, VaultConfig, VaultRegistry
from vault_doctor import diagnose_vault

_LEDGER = "review_ledger.jsonl"


def _item_id(item: dict[str, Any]) -> str:
    if item.get("id"):
        return str(item["id"])
    key = ":".join(
        str(item.get(key, ""))
        for key in ("kind", "context_id", "code", "target", "path")
    )
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _quality_items(
    config: VaultConfig,
    contexts: list[dict[str, Any]],
    scan,
    *,
    context_id: str | None,
) -> list[dict[str, Any]]:
    """Aggregate doctor findings into a de-noised, maintained-scope-only agenda.

    The raw signal stream is far too noisy for review (thousands of daily-log broken
    links). This groups broken and ambiguous targets with occurrence counts and keeps
    only targets reachable from a reviewed Context scope; unscoped findings stay
    available through `quality`.

    The full candidate list is returned. Callers must truncate for display only:
    a display limit must never change which items count as present, or a smaller
    limit would look like problems had been resolved.
    """
    if not contexts:
        return []
    doctor = diagnose_vault(config.root_path, source_scan=scan)
    tags_by_path = {record.path: extract_tags(record.content) for record in scan.records}

    def scoped_ids(path: str) -> list[str]:
        return sorted(
            row["id"]
            for row in contexts
            if note_in_context(path, tags_by_path.get(path, ()), row)
        )

    items: list[dict[str, Any]] = []
    for code, source_key, label in (
        ("broken_reference", "broken_targets", "不存在的笔记"),
        ("ambiguous_reference", "ambiguous_targets", "多个同名笔记"),
    ):
        for entry in doctor.get(source_key, []):
            sources = [str(item) for item in entry.get("sources", [])]
            ids = sorted({cid for path in sources for cid in scoped_ids(path)})
            if not ids or (context_id and context_id not in ids):
                continue
            items.append(
                {
                    "kind": "quality",
                    "context_id": ids[0],
                    "context_ids": ids,
                    "code": code,
                    "target": entry["target"],
                    "occurrences": int(entry["occurrences"]),
                    "path": sources[0] if sources else None,
                    "triage_hint": entry.get("triage_hint"),
                    "reason": (
                        f"{entry['occurrences']} 处链接指向{label} '{entry['target']}'"
                    ),
                }
            )
    for path in [*scan.skipped_decode, *scan.skipped_io]:
        ids = scoped_ids(path)
        if not ids or (context_id and context_id not in ids):
            continue
        items.append(
            {
                "kind": "quality",
                "context_id": ids[0],
                "context_ids": ids,
                "code": "evidence_gap",
                "occurrences": 1,
                "path": path,
                "reason": "来源无法按 UTF-8 读取或访问，需人工检查",
            }
        )
    items.sort(
        key=lambda row: (
            -row["occurrences"],
            row["code"],
            str(row.get("target") or row.get("path")),
        )
    )
    return items


def _stale_card_items(
    config: VaultConfig,
    scan,
    contexts: list[dict[str, Any]],
    *,
    context_id: str | None,
) -> list[dict[str, Any]]:
    """Flag compiled cards whose scoped sources changed after the card was written.

    Cards are deposited either inside the Vault (`_compiled/`) or - by default now -
    in the external compiled area beside the Vault. Both locations are checked, so an
    externally deposited card can still be detected as stale.
    """
    items: list[dict[str, Any]] = []
    for row in contexts:
        if context_id and row["id"] != context_id:
            continue
        in_vault = suggest_target(row)
        candidates: list[tuple[str, Path]] = [(in_vault, config.root_path / PurePosixPath(in_vault))]
        external_name = compiled_card_name(row)
        try:
            external = compiled_root(config) / external_name
        except ValueError:
            external = None
        if external is not None:
            candidates.append((f"{external.parent.name}/{external_name}", external))

        newest = 0
        for record in scan.records:
            if note_in_context(record.path, extract_tags(record.content), row):
                newest = max(newest, record.mtime_ns)

        for target, card in candidates:
            try:
                card_mtime = card.stat().st_mtime_ns
            except OSError:
                continue
            if newest > card_mtime:
                items.append(
                    {
                        "kind": "compiled_card_stale",
                        "context_id": row["id"],
                        "code": "compiled_card_stale",
                        "reason": "A scoped source changed after the compiled card was written",
                        "path": target,
                        "suggestion": (
                            f"contexts compile {row['id']} --save --content-file <draft>"
                            "  # then review and re-save"
                        ),
                    }
                )
    return items


def _ledger_path(registry: VaultRegistry, config: VaultConfig) -> Path:
    return registry.vault_state_dir(config) / _LEDGER


def _previous_entry(
    registry: VaultRegistry, config: VaultConfig, scope: str
) -> dict[str, Any] | None:
    path = _ledger_path(registry, config)
    if not path.is_file():
        return None
    previous: dict[str, Any] | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("scope") == scope:
                previous = entry
    except OSError:
        return None
    return previous


def _append_entry(
    registry: VaultRegistry, config: VaultConfig, entry: dict[str, Any]
) -> None:
    state = registry.vault_state_dir(config)
    payload = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / ".review.lock", pinned_parent=guard):
            try:
                descriptor = guard.open_existing(
                    writable=True, exclusive_writes=False, name=_LEDGER
                )
            except FileNotFoundError:
                descriptor = guard.create_new(name=_LEDGER)
            try:
                os.lseek(descriptor, 0, os.SEEK_END)
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise OSError("Short review ledger write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


_KIND_PRIORITY = {
    "maintenance": 0,
    "followup": 1,
    "compiled_card_stale": 2,
    "quality": 3,
}
# Hints that usually mean machine-generated link noise. They are ranked last for
# display but are still counted, still written to the ledger, and never dropped.
_NOISY_HINTS = frozenset({"zotero_generated", "template_placeholder"})


def _rank_item(item: dict[str, Any]) -> tuple[Any, ...]:
    hint = item.get("triage_hint")
    return (
        _KIND_PRIORITY.get(str(item["kind"]), 9),
        1 if hint in _NOISY_HINTS else 0,
        -int(item.get("occurrences") or 0),
        str(item.get("code") or ""),
        str(item.get("target") or item.get("path") or ""),
        item["id"],
    )


def review_agenda(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    context_id: str | None = None,
    limit: int = 20,
    record: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one bounded, prioritized review agenda and optionally record it."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Review limit must be between 1 and 100")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Review time must include a timezone")
    version = load_context_version(registry, config)
    contexts = version["definitions"]["contexts"] if version else []

    from context_tick import poll_contexts

    poll = poll_contexts(registry, config, limit=100, max_contexts=200, now=now)
    items: list[dict[str, Any]] = []
    for entry in poll["items"]:
        if context_id and entry.get("context_id") != context_id:
            continue
        items.append(
            {
                "kind": entry["kind"],
                "context_id": entry.get("context_id"),
                "code": entry.get("code") or entry.get("status"),
                "reason": entry.get("reason") or entry.get("title"),
                "due_at": entry.get("due_at"),
                "path": entry.get("source", {}).get("path")
                if isinstance(entry.get("source"), dict)
                else None,
                "id": entry.get("id"),
            }
        )
    scan = scan_notes(config.root_path)
    items.extend(_quality_items(config, contexts, scan, context_id=context_id))
    items.extend(_stale_card_items(config, scan, contexts, context_id=context_id))

    for item in items:
        item["id"] = _item_id(item)
    items.sort(key=_rank_item)
    fingerprint = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    counts: dict[str, int] = {}
    hint_counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        hint = item.get("triage_hint")
        if hint:
            hint_counts[str(hint)] = hint_counts.get(str(hint), 0) + 1

    # The agenda's basis: an empty agenda only means "clear" when every input was
    # actually read to completion. Display truncation is never part of the basis.
    basis = {
        "item_source": "full",
        "contexts_registered": len(contexts),
        "scan_skipped": len(scan.skipped_decode) + len(scan.skipped_io),
        "poll_status": poll.get("status"),
        "poll_incomplete": bool(poll.get("scan_incomplete")),
        "poll_truncated": bool(poll.get("output_truncated"))
        or int(poll.get("context_count", 0)) > int(poll.get("contexts_checked", 0)),
        "poll_items_shown": len(poll.get("items", ())),
    }
    basis_reasons: list[str] = []
    if not contexts:
        basis_reasons.append("no_registered_contexts")
    if basis["scan_skipped"]:
        basis_reasons.append("scan_skipped_sources")
    if basis["poll_incomplete"]:
        basis_reasons.append("poll_incomplete")
    if basis["poll_truncated"]:
        basis_reasons.append("poll_truncated")
    basis["complete"] = not basis_reasons
    basis["incomplete_reasons"] = basis_reasons
    if items:
        status = "attention"
    else:
        status = "clear" if basis["complete"] else "incomplete"

    scope = context_id or "*"
    previous = _previous_entry(registry, config, scope) if record else None
    current_ids = {item["id"] for item in items}
    previous_ids = set(previous.get("item_ids", [])) if previous else set()
    previous_basis = previous.get("basis") if previous else None
    # A disappearance is only proof of resolution when both sides were recorded over a
    # complete basis. Older entries (or truncated/incomplete runs) cannot confirm one.
    incomparable_reason: str | None = None
    if previous is None:
        incomparable_reason = "no_previous_entry"
    elif not isinstance(previous_basis, dict) or previous_basis.get("item_source") != "full":
        incomparable_reason = "previous_basis_not_full"
    elif previous_basis.get("complete") is not True:
        incomparable_reason = "previous_basis_incomplete"
    elif not basis["complete"]:
        incomparable_reason = "current_basis_incomplete"
    comparable = incomparable_reason is None
    vanished = sorted(previous_ids - current_ids)
    delta = {
        "previous_fingerprint": previous.get("fingerprint") if previous else None,
        "previous_recorded_at": previous.get("recorded_at") if previous else None,
        "new_item_ids": sorted(current_ids - previous_ids),
        "resolved_item_ids": vanished if comparable else [],
        "resolved_suppressed": 0 if comparable else len(vanished),
        "comparable": comparable,
        "incomparable_reason": incomparable_reason,
        "unchanged": bool(previous and previous.get("fingerprint") == fingerprint),
    }
    ledger_written = False
    if record:
        _append_entry(
            registry,
            config,
            {
                "schema_version": 1,
                "vault_id": config.id,
                "scope": scope,
                "recorded_at": now.isoformat(),
                "fingerprint": fingerprint,
                "item_ids": sorted(current_ids),
                "counts_by_kind": counts,
                "basis": basis,
            },
        )
        ledger_written = True
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "checked_at": now.isoformat(),
        "scope": scope,
        "status": status,
        "item_count": len(items),
        "counts_by_kind": counts,
        "counts_by_hint": hint_counts,
        "items": items[:limit],
        "items_truncated": len(items) > limit,
        "fingerprint": fingerprint,
        "delta": delta,
        "basis": basis,
        "ledger_written": ledger_written,
        "poll_status": poll["status"],
        "scan_incomplete": (not basis["complete"]),
        "writes_performed": 0,
        "vault_writes": 0,
        "meaning": (
            "Review candidates and a durable external ledger entry, not an automatic repair or a "
            "scheduled reminder. The Vault is never modified."
        ),
    }


def format_review_agenda(result: dict[str, Any]) -> str:
    """Render a concise human view of one review agenda."""
    lines = [
        f"# 复查议程（scope: {result['scope']}）",
        f"状态 {result['status']} · 事项 {result['item_count']} · "
        f"分布 {result['counts_by_kind']} · poll {result['poll_status']}",
    ]
    if result.get("counts_by_hint"):
        lines.append(f"线索分布（仅提示，不构成忽略依据）：{result['counts_by_hint']}")
    delta = result["delta"]
    if delta["previous_recorded_at"]:
        resolved = len(delta["resolved_item_ids"])
        extra = (
            f"（另有 {delta['resolved_suppressed']} 项消失但不可比，未确认消解）"
            if delta.get("resolved_suppressed")
            else ""
        )
        lines.append(
            f"较上次（{delta['previous_recorded_at']}）：新增 {len(delta['new_item_ids'])} · "
            f"已消解 {resolved}{extra}"
            + (" · 无变化" if delta["unchanged"] else "")
        )
    else:
        lines.append("首次记录（已写入外部台账）" if result["ledger_written"] else "未记录")
    if result["status"] == "incomplete":
        reasons = "、".join(result["basis"]["incomplete_reasons"]) or "unknown"
        lines.append(f"⚠ 判定不完整（{reasons}）：当前没有发现不等于没有问题")
    for item in result["items"]:
        target = item.get("path") or item.get("context_id") or ""
        lines.append(f"- [{item['kind']}] {item.get('code')} · {target} · {item.get('reason')}")
    if result.get("items_truncated"):
        lines.append(f"… 其余 {result['item_count'] - len(result['items'])} 项已省略（仅影响显示）")
    return "\n".join(lines)
