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
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from context_compile import (
    REGISTRY_ABSENT,
    REGISTRY_VALID,
    compiled_card_name,
    compiled_root,
    definition_fingerprint,
    definitions_fingerprint,
    load_card_registry,
    suggest_target,
)
from context_manager import extract_tags, note_in_context
from context_store import load_context_version
from personal_review import PERSONAL_DIAGNOSTICS_VERSION, personal_findings
from fts_indexer import scan_notes
from registry import FileLock, VaultConfig, VaultRegistry
from vault_doctor import DIAGNOSTICS_VERSION, NOISY_HINTS, diagnose_vault

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
    for code, source_key, label, grouped_key in (
        ("broken_reference", "broken_links", "不存在的笔记", "broken_targets"),
        ("ambiguous_reference", "ambiguous_links", "多个同名笔记", "ambiguous_targets"),
    ):
        # Scope is decided over the FULL occurrence stream, never over the truncated display
        # sample: a target whose only in-scope source is its twenty-first occurrence must
        # still be reported, and its in-scope Context ids must come from the real occurrences.
        vault_totals = {
            str(row["target"]): int(row["occurrences"])
            for row in doctor.get(grouped_key, [])
        }
        scoped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in doctor.get(source_key, []):
            path = str(row.get("source", ""))
            ids = [
                cid
                for cid in scoped_ids(path)
                if not context_id or cid == context_id
            ]
            if not ids:
                continue
            scoped[str(row.get("target", ""))].append(
                {"path": path, "ids": ids, "hint": row.get("triage_hint")}
            )
        for target, entries in sorted(
            scoped.items(), key=lambda pair: (-len(pair[1]), pair[0].casefold())
        ):
            ids = sorted({cid for entry in entries for cid in entry["ids"]})
            hints = Counter(
                str(entry["hint"]) for entry in entries if entry["hint"]
            )
            dominant = (
                sorted(hints.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
                if hints
                else None
            )
            scoped_total = len(entries)
            vault_total = vault_totals.get(target, scoped_total)
            if scoped_total == vault_total:
                reason = f"{scoped_total} 处链接指向{label} '{target}'"
            else:
                reason = (
                    f"范围内 {scoped_total} 处链接指向{label} '{target}'"
                    f"（全库同一目标共 {vault_total} 处，其余不在本范围内）"
                )
            items.append(
                {
                    "kind": "quality",
                    "context_id": ids[0],
                    "context_ids": ids,
                    "code": code,
                    "target": target,
                    "occurrences": scoped_total,
                    "vault_target_occurrences": vault_total,
                    "path": entries[0]["path"],
                    "triage_hint": dominant,
                    "reason": reason,
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
    # Orphans are the other half of connectivity: a note nobody links to and that links
    # nowhere is invisible to every forward-looking signal. The reverse is that an orphan
    # has no inbound link to travel along, so this can only be detected by membership, and
    # only scoped notes are reported.
    for path in doctor.get("orphans", []):
        ids = scoped_ids(str(path))
        if not ids or (context_id and context_id not in ids):
            continue
        items.append(
            {
                "kind": "quality",
                "context_id": ids[0],
                "context_ids": ids,
                "code": "orphan_note",
                "occurrences": 0,
                "path": str(path),
                "reason": "该笔记在范围内，但全库既没有链接指向它，它也没有链接指向别处",
                "suggestion": "把它接入相关 MOC，或补上它应有的上级/相关链接",
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
    registry: VaultRegistry,
    config: VaultConfig,
    scan,
    contexts: list[dict[str, Any]],
    *,
    context_id: str | None,
) -> list[dict[str, Any]]:
    """Flag compiled cards that no longer match the sources they were built from.

    Registered cards are checked against their recorded output path and the exact source
    checksums captured at write time, so a card saved with `--out-root` or `--name` is
    covered too, and a deleted card is reported instead of silently ignored.

    Cards written before the registry existed have no record, so the default location is
    still guessed for those contexts as a fallback by modification time.
    """
    items: list[dict[str, Any]] = []
    registry_state = load_card_registry(registry, config)
    cards = registry_state["cards"]
    if registry_state["state"] not in {REGISTRY_ABSENT, REGISTRY_VALID}:
        # Reported as an item, not only as a basis flag: an unreadable registry silently
        # removes every card from the agenda, which would otherwise read as "all resolved".
        items.append(
            {
                "kind": "quality",
                "context_id": context_id or "*",
                "code": "card_registry_unreadable",
                "reason": (
                    f"编译卡登记无法读取（{registry_state['state']}："
                    f"{registry_state.get('reason')}），卡片的缺失不能算作已解决"
                ),
                "path": registry_state["path"],
                "suggestion": "修复或删除该登记文件后重跑；修复前不要按本议程判断卡片状态",
            }
        )
    registered: set[str] = set()
    for record in cards:
        row = next((item for item in contexts if item["id"] == record.get("context_id")), None)
        if row is None:
            continue
        if context_id and row["id"] != context_id:
            continue
        registered.add(row["id"])
        card_path = str(record.get("path") or "")
        recorded = record.get("source_dependencies") or {}
        tags_by_path = {item.path: extract_tags(item.content) for item in scan.records}
        current = {
            item.path: item.checksum
            for item in scan.records
            if note_in_context(item.path, tags_by_path.get(item.path, ()), row)
        }
        added = sorted(set(current) - set(recorded))
        removed = sorted(set(recorded) - set(current))
        changed = sorted(
            path for path in set(recorded) & set(current) if recorded[path] != current[path]
        )
        drift = len(added) + len(removed) + len(changed)
        external_edit = False
        if card_path and Path(card_path).is_file():
            recorded_hash = str(record.get("card_sha256") or "")
            if recorded_hash:
                try:
                    current_hash = hashlib.sha256(Path(card_path).read_bytes()).hexdigest()
                except OSError:
                    current_hash = ""
                external_edit = bool(current_hash) and current_hash != recorded_hash
        if not card_path or not Path(card_path).is_file():
            code = "compiled_card_missing"
            reason = "登记的编译卡已不在记录的输出路径上"
        elif external_edit:
            code = "compiled_card_edited_externally"
            reason = "卡片内容在发布后被外部修改，需人工复核其依据与范围"
        elif record.get("basis") != "verified":
            # The recorded sources may not be the ones the draft was written against, so
            # "sources unchanged" carries no trustworthy meaning for this card.
            code = "compiled_card_unverified_basis"
            reason = (
                "该卡保存时未提供编译包来源快照，登记的是保存时刻的来源，"
                "不能据此认定编写依据未变化；请用 --expected-source-snapshot 重新保存"
            )
        elif record.get("context_fingerprint") != definition_fingerprint(row):
            # Checked before drift: when the scope itself changed, calling the card "stale"
            # would wrongly suggest that source files were edited. Records written before
            # per-Context fingerprints existed fall here too, which is the honest
            # "re-verify this" answer rather than a stale-looking one.
            code = "compiled_card_scope_changed"
            reason = (
                "该 Context 的定义或范围在成卡后变化（或该卡缺少单 Context 指纹，需重新校验），"
                "卡片覆盖的已不是同一批来源"
            )
        elif drift:
            code = "compiled_card_stale"
            reason = (
                f"成卡后范围内来源发生变化：新增 {len(added)}、删除 {len(removed)}、"
                f"修改 {len(changed)}"
            )
        else:
            continue
        items.append(
            {
                "kind": "compiled_card_stale",
                "context_id": row["id"],
                "code": code,
                "reason": reason,
                "path": card_path or str(record.get("name")),
                "added_sources": added[:5],
                "removed_sources": removed[:5],
                "changed_sources": changed[:5],
                "changed_source_count": drift,
                "suggestion": (
                    f"contexts compile {row['id']} --save --content-file <draft>"
                    f" --out-root {record.get('compiled_area')}"
                    f" --name {record.get('name')} --overwrite"
                    "  # then review and re-save"
                ),
            }
        )

    for row in contexts:
        if context_id and row["id"] != context_id:
            continue
        if row["id"] in registered:
            continue
        # Fallback for cards written before output paths were recorded.
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
                        "basis": "unregistered",
                        "suggestion": (
                            f"contexts compile {row['id']} --save --content-file <draft>"
                            f" --expected-source-snapshot <bundle hash>"
                            "  # then review and re-save"
                        ),
                    }
                )
            else:
                # The card exists but has no record, so nothing about its content or its
                # authoring basis can be checked. Its modification time being older than its
                # sources is not evidence that it is current.
                items.append(
                    {
                        "kind": "compiled_card_stale",
                        "context_id": row["id"],
                        "code": "compiled_card_unregistered",
                        "reason": (
                            "该卡在登记机制之前写就，没有输出路径与来源依据记录，"
                            "无法判断它是否仍然有效"
                        ),
                        "path": target,
                        "basis": "unregistered",
                        "suggestion": (
                            f"contexts compile {row['id']} --save --content-file <draft>"
                            f" --expected-source-snapshot <bundle hash> --overwrite"
                            "  # 重新发布后即可被追踪"
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
    "domain_review": 2,
    "quality": 3,
}
# Hints that usually mean machine-generated link noise. They are ranked last for
# display but are still counted, still written to the ledger, and never dropped.


def _rank_item(item: dict[str, Any]) -> tuple[Any, ...]:
    hint = item.get("triage_hint")
    return (
        _KIND_PRIORITY.get(str(item["kind"]), 9),
        0 if item.get("severity") == "high" else 1,
        1 if hint in NOISY_HINTS else 0,
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
    code: str | None = None,
    record: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one bounded, prioritized review agenda and optionally record it.

    `code` narrows the *display* only. The ledger, fingerprint and delta are always
    computed over the full item set, so filtering a view can never be mistaken for
    having resolved anything.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Review limit must be between 1 and 100")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Review time must include a timezone")
    version = load_context_version(registry, config)
    contexts = version["definitions"]["contexts"] if version else []

    from context_tick import poll_contexts

    # One read snapshot for the whole agenda: the maintenance poll and the quality/card checks
    # must not be able to disagree about what the Vault looked like, and the Vault must not be
    # read twice for one command.
    scan = scan_notes(config.root_path)
    poll = poll_contexts(
        registry, config, limit=100, max_contexts=200, now=now, source_scan=scan
    )
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
    card_registry = load_card_registry(registry, config)
    card_registry_state = str(card_registry["state"])
    card_registry_reason = card_registry.get("reason")
    items.extend(_quality_items(config, contexts, scan, context_id=context_id))
    personal_tags = {note.path: extract_tags(note.content) for note in scan.records}
    for finding in personal_findings(scan, today=now.date()):
        for context in contexts:
            if context_id and context["id"] != context_id:
                continue
            if not (context.get("maintenance") or {}).get("enabled", False):
                continue
            if note_in_context(finding["path"], personal_tags.get(finding["path"], ()), context):
                items.append({**finding, "context_id": context["id"]})
    items.extend(_stale_card_items(registry, config, scan, contexts, context_id=context_id))

    for item in items:
        item["id"] = _item_id(item)
    items.sort(key=_rank_item)
    matched = [item for item in items if code is None or item.get("code") == code]
    fingerprint = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    counts: dict[str, int] = {}
    hint_counts: dict[str, int] = {}
    code_counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        item_code = str(item.get("code") or "")
        if item_code:
            code_counts[item_code] = code_counts.get(item_code, 0) + 1
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
        "maintenance_status": poll.get("maintenance_status"),
        "maintenance_configured_contexts": poll.get("maintenance_configured_contexts"),
        "context_revision": version["revision"] if version else 0,
        "definitions_fingerprint": definitions_fingerprint(contexts),
        "diagnostics_version": f"{DIAGNOSTICS_VERSION}:personal-{PERSONAL_DIAGNOSTICS_VERSION}",
        "card_registry_state": card_registry_state,
        "card_registry_reason": card_registry_reason,
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
    if card_registry_state not in {REGISTRY_ABSENT, REGISTRY_VALID}:
        # Cards could not be read, so their absence from the agenda proves nothing.
        basis_reasons.append(f"card_registry_{card_registry_state}")
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
    elif (
        "definitions_fingerprint" not in previous_basis
        or "diagnostics_version" not in previous_basis
    ):
        # An entry from before the semantic basis existed: a full item set, but nothing
        # that identifies what it observed. Do not blame it on a definitions change.
        incomparable_reason = "previous_basis_not_semantic"
    elif not basis["complete"]:
        incomparable_reason = "current_basis_incomplete"
    elif previous_basis.get("definitions_fingerprint") != basis["definitions_fingerprint"]:
        # The Context definitions, scopes or filters changed between runs, so the two
        # agendas observed different source sets. A vanished item may have left the scope
        # rather than been fixed.
        incomparable_reason = "context_definitions_changed"
    elif previous_basis.get("diagnostics_version") != basis["diagnostics_version"]:
        incomparable_reason = "diagnostics_version_changed"
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
        "display_filter": code,
        "display_matched": len(matched),
        "counts_by_kind": counts,
        "counts_by_code": code_counts,
        "counts_by_hint": hint_counts,
        "items": matched[:limit],
        "items_truncated": len(matched) > limit,
        "fingerprint": fingerprint,
        "delta": delta,
        "basis": basis,
        "ledger_written": ledger_written,
        "poll_status": poll["status"],
        "scan_incomplete": (not basis["complete"]),
        "incomplete": (not basis["complete"]),
        "incomplete_reasons": basis["incomplete_reasons"],
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
    if result.get("counts_by_code"):
        lines.append(f"类别分布：{result['counts_by_code']}")
    if result.get("display_filter"):
        lines.append(
            f"仅显示 code={result['display_filter']} 的 {result['display_matched']} 项"
            f"（共 {result['item_count']} 项；筛选只影响显示）"
        )
    if not result["basis"].get("maintenance_configured_contexts"):
        # An empty poll otherwise reads as "maintenance is covered"; show the coverage gap.
        lines.append(
            "维护覆盖：0 个 Context 启用维护策略，本轮没有检查任何范围，"
            "空议程不代表维护正常"
        )
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
    if result.get("incomplete"):
        # Printed even when items exist: a partly-read agenda must not be presented as if it
        # were a complete one just because `status` says attention.
        reasons = "、".join(result["basis"]["incomplete_reasons"]) or "unknown"
        lines.append(f"⚠ 判定不完整（{reasons}）：当前结果不代表全部，也不代表没有问题")
    for item in result["items"]:
        target = item.get("path") or item.get("context_id") or ""
        lines.append(f"- [{item['kind']}] {item.get('code')} · {target} · {item.get('reason')}")
    if result.get("items_truncated"):
        lines.append(f"… 其余 {result['item_count'] - len(result['items'])} 项已省略（仅影响显示）")
    return "\n".join(lines)
