"""Registered memory workflows. Only the broker can mutate Vault documents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from capture_broker import CapturePlan, create_document_plan, load_capture_plan
from mutation_service import commit_and_refresh
from registry import FileLock, VaultConfig, VaultRegistry
from secure_io import secure_read_bytes
from text_document import TextDocument, append_section
from triage_engine import ContactResolver, TagClassifier, format_triage_summary, render_triage
from todo_time import SHANGHAI, normalize_todo_time


# Optional personal profile, not inferred write authority. Missing parent directories
# remain errors at the broker. These are the only stream destinations in this profile.
STREAMS = {
    "todo": ("000_Inbox", "todo", "📋 Todo", "capture"),
    "memo": ("000_Inbox", "memo", "📝 Memo", "capture"),
    "idea": ("000_Inbox", "idea", "💡 Idea", "capture"),
    "biography": ("200_Area/Biography", "biography", "个人经历记录", "biography-chronicle"),
    "investment": ("200_Area/Investment", "investment", "投资待核实记录", "investment-intake"),
}


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def plan_summary(plan: CapturePlan) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.to_dict().items()
        if key not in {"payload_base64", "original_base64"}
    }


def _source(config: VaultConfig, path: str) -> TextDocument | None:
    try:
        return TextDocument(secure_read_bytes(config.root_path, path))
    except FileNotFoundError:
        return None


def _year(year: int | None) -> int:
    chosen = year if year is not None else datetime.now().year
    if not 1900 <= chosen <= 9999:
        raise ValueError("Year must be between 1900 and 9999")
    return chosen


def _text(text: str) -> str:
    if not text.strip():
        raise ValueError("Capture text cannot be empty")
    return text.strip()


@dataclass(frozen=True)
class DocumentProposal:
    path: str
    content: bytes
    expected_sha256: str | None
    metadata: dict[str, Any]


def _execute(
    registry: VaultRegistry,
    config: VaultConfig,
    request: dict[str, Any],
    prepare: Callable[[], DocumentProposal],
    *,
    preview: bool,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Bind a command intent to one frozen plan before any source is touched."""
    current = registry.resolve(config.id)
    if current.root_path != config.root_path:
        raise ValueError("Workflow Vault binding changed")
    if not preview and current.write_policy not in {"append-create", "reviewed-edit"}:
        raise PermissionError("Vault is read-only; enable an explicit write policy first")
    if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        raise ValueError("Invalid idempotency key")
    key = idempotency_key or uuid.uuid4().hex
    filename = f"workflow.{_hash(key)}.json"
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / ".workflow.lock", pinned_parent=guard):
            try:
                record = json.loads(guard.read_bytes(name=filename).decode("utf-8"))
                if not isinstance(record, dict) or record.get("schema_version") != 1:
                    raise ValueError("Invalid workflow intent schema")
            except FileNotFoundError:
                proposal = prepare()
                plan = create_document_plan(
                    config.root_path,
                    state,
                    proposal.path,
                    proposal.content,
                    expected_sha256=proposal.expected_sha256,
                    idempotency_key=f"workflow:{_hash(key)}",
                )
                record = {
                    "schema_version": 1,
                    "vault_id": config.id,
                    "request_sha256": _hash(request),
                    "plan_id": plan.plan_id,
                    "plan_sha256": plan.plan_sha256,
                    "metadata": proposal.metadata,
                }
                encoded = json.dumps(
                    {**record, "checksum": _hash(record)}, ensure_ascii=False
                ).encode()
                fd = guard.create_new(name=filename)
                try:
                    if os.write(fd, encoded) != len(encoded):
                        raise OSError("Short workflow intent write")
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
                checksum = record.pop("checksum", None)
                if checksum != _hash(record) or record.get("vault_id") != config.id:
                    raise ValueError("Workflow intent checksum or Vault binding mismatch")
                if record.get("request_sha256") != _hash(request):
                    raise ValueError(
                        "Idempotency key is already bound to a different workflow request"
                    )
                plan = load_capture_plan(state, record["plan_id"])
                if plan.plan_sha256 != record.get("plan_sha256"):
                    raise ValueError("Workflow intent and frozen plan mismatch")
    metadata = record["metadata"]
    result = {**metadata, "plan_id": plan.plan_id, "plan": plan_summary(plan)}
    if preview:
        return {
            **result,
            "status": "preview",
            "committed": False,
            "message": f"Preview: {plan.action} -> {plan.relative_path}\nPlan: {plan.plan_id}",
        }
    execution = commit_and_refresh(registry, config, plan)
    return {
        **result,
        **execution,
        "committed": True,
        "message": execution.get("message", metadata.get("message", "Committed and indexed")),
    }


def _stream_proposal(
    config: VaultConfig,
    kind: str,
    year: int,
    month: int,
    entry: str,
    triage: dict[str, Any] | None = None,
) -> DocumentProposal:
    folder, stem, title, note_type = STREAMS[kind]
    path = f"{folder}/{stem}.{year}.md"
    document = _source(config, path)
    basis = document.checksum if document else None
    if document is None:
        document = TextDocument(
            (
                f'---\ntitle: "{title} {year}"\nyear: {year}\n'
                f"type: {note_type}\ntags: [capture/{kind}]\n---\n\n"
                f"# {title} {year}\n\n"
                + ("> 原始输入，尚未核实或记账；不改变持仓、成本或现金。\n\n"
                   if kind == "investment" else "")
            ).encode()
        )
    return DocumentProposal(
        path,
        append_section(document, f"{year}-{month:02d}", entry),
        basis,
        {
            "type": kind,
            "year": year,
            "file": path,
            "entry": entry,
            "triage": triage,
            "message": f"Recorded and indexed {path}:\n{entry}",
        },
    )


def append_inbox_stream(
    registry: VaultRegistry,
    config: VaultConfig,
    capture_type: str,
    text: str,
    *,
    year: int | None = None,
    preview: bool = False,
    idempotency_key: str | None = None,
    completed: bool = False,
    tier: str | None = None,
    event_date: str | None = None,
) -> dict[str, Any]:
    text = _text(text)
    if any(c in text for c in ("\n", "\r", "\x00")):
        raise ValueError("Capture one entry per command; newlines and NUL are not allowed")
    if capture_type not in STREAMS:
        raise ValueError("Unknown personal capture stream")
    if tier is not None and (capture_type != "biography" or tier not in {"L1", "L2", "L3"}):
        raise ValueError("Tier is only supported for biography: L1, L2 or L3")
    if completed and capture_type != "todo":
        raise ValueError("Completed is only supported for todo")
    occurrence = date.fromisoformat(event_date) if event_date else None
    if occurrence and (capture_type not in {"biography", "investment"} or year is not None):
        raise ValueError("Use --date only for area streams, without --year")
    if year is not None and capture_type in {"biography", "investment"}:
        raise ValueError("Area captures use --date YYYY-MM-DD, not --year")
    if year is not None:
        _year(year)

    def prepare() -> DocumentProposal:
        now = datetime.now(SHANGHAI)
        chosen = occurrence.year if occurrence else _year(year)
        month = occurrence.month if occurrence else now.month
        stamp = now.strftime("%Y-%m-%d %H:%M")
        temporal = None
        if capture_type == "todo":
            temporal = normalize_todo_time(text, now)
            fields = f" [created:: {now.isoformat(timespec='minutes')}]"
            if temporal["start"] is not None:
                fields += f" [start:: {temporal['start']}]"
            if temporal["end"] is not None:
                fields += f" [end:: {temporal['end']}]"
            if temporal["status"] == "unresolved":
                fields += " [calendar:: unresolved]"
            entry = f"- [{'x' if completed else ' '}] {text}{fields} #todo/inbox"
            resolver = ContactResolver(config.root_path)
            entry, contacts = resolver.link_entities(entry)
            entry, tags = TagClassifier().classify_and_tag(entry)
            triage = {"contacts": contacts, "tags": tags}
        elif capture_type in {"memo", "idea"}:
            icon = "💡" if capture_type == "idea" else "📌"
            entry = f"- {icon} [{stamp}] {text}" + (" #idea/seed" if capture_type == "idea" else "")
            triage = None
        else:
            happened = occurrence or now.date()
            marker = f"Experience ({tier or 'L2'})" if capture_type == "biography" else "Pending investment"
            entry = f"- 📌 [{happened}] **{marker}**: {text} [created:: {now.isoformat(timespec='minutes')}]"
            triage = None
        capture_id = f"kr-{uuid.uuid4().hex[:16]}"
        entry += f" ^{capture_id}"
        proposal = _stream_proposal(config, capture_type, chosen, month, entry, triage)
        proposal.metadata["capture_id"] = capture_id
        proposal.metadata["temporal_resolution"] = temporal
        proposal.metadata["verification"] = "unreviewed"
        if capture_type == "investment":
            proposal.metadata["ledger_changed"] = False
        return proposal

    return _execute(
        registry,
        config,
        {
            "operation": "inbox",
            "type": capture_type,
            "text": text,
            "year": year,
            "completed": completed,
            "tier": tier,
            "event_date": event_date,
        },
        prepare,
        preview=preview,
        idempotency_key=idempotency_key,
    )


def append_contact_log(
    registry: VaultRegistry,
    config: VaultConfig,
    target: str,
    text: str,
    *,
    preview: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    text = _text(text)

    def prepare() -> DocumentProposal:
        resolver = ContactResolver(config.root_path)
        entity, candidates = resolver.match_contact(target)
        if not entity:
            raise ValueError(
                f"Contact is {'ambiguous' if candidates else 'not found'}: {target}; "
                f"candidates: {', '.join(candidates)}. Use an exact Vault-relative path."
            )
        document = _source(config, entity.relative_path)
        if document is None:
            raise FileNotFoundError(entity.relative_path)
        # Re-read must still be the source whose aliases/identity were resolved.
        if document.checksum != resolver.notes[entity.relative_path].checksum:
            raise RuntimeError("Contact source changed during identity resolution")
        entry = f"- [{datetime.now():%Y-%m-%d %H:%M}] {text}"
        return DocumentProposal(
            entity.relative_path,
            append_section(document, "Log", entry),
            document.checksum,
            {
                "canonical": entity.canonical,
                "relative_path": entity.relative_path,
                "entry": entry,
                "message": f"Recorded and indexed [[{entity.canonical}]]:\n{entry}",
            },
        )

    return _execute(
        registry,
        config,
        {"operation": "contact", "target": target, "text": text},
        prepare,
        preview=preview,
        idempotency_key=idempotency_key,
    )


def triage_todos(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    year: int | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    chosen = _year(year)
    resolver = ContactResolver(config.root_path)
    path = f"000_Inbox/todo.{chosen}.md"
    document = _source(config, path)
    report, content = render_triage(
        document.text if document else "", resolver, vault_name=config.name, year=chosen
    )
    if not report.diffs:
        return {**report.to_dict(), "status": "unchanged", "message": format_triage_summary(report)}

    def prepare() -> DocumentProposal:
        return DocumentProposal(path, document.encode(content), document.checksum, report.to_dict())

    result = _execute(
        registry,
        config,
        {"operation": "triage", "year": chosen, "source_sha256": document.checksum},
        prepare,
        preview=not commit,
        idempotency_key=None,
    )
    report.committed = result["committed"]
    if result["status"] != "applied_pending_index":
        result["message"] = format_triage_summary(report)
    return result


def drain_quick_capture_buffer(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    year: int | None = None,
) -> dict[str, Any]:
    """Import stable entry occurrences once; keep the source buffer as evidence."""
    path = "000_Inbox/Quick_Capture.md"
    document = _source(config, path)
    if document is None:
        return {"status": "unchanged", "routed_count": 0, "message": "No legacy buffer"}
    occurrences: Counter[str] = Counter()
    routed = []
    for line in document.text.splitlines():
        entry = line.strip()
        task = re.match(r"^-\s*\[([ xX])\]\s*(.+)$", entry)
        if task:
            kind = "todo"
        elif entry.startswith(("- 💡", "- 📌")):
            kind = "idea" if entry.startswith("- 💡") else "memo"
        else:
            continue
        occurrences[entry] += 1
        key = "buffer:" + _hash([path, entry, occurrences[entry]])

        def prepare() -> DocumentProposal:
            # Imports preserve the source entry verbatim instead of re-stamping/re-tagging it.
            date = re.search(r"\b(\d{4})-(\d{2})-\d{2}\b", entry)
            source_year = int(date.group(1)) if date else None
            month = int(date.group(2)) if date else datetime.now().month
            if not 1 <= month <= 12:
                raise ValueError("Legacy entry has an invalid month")
            chosen = _year(year if year is not None else source_year)
            return _stream_proposal(config, kind, chosen, month, entry)

        result = _execute(
            registry,
            config,
            {
                "operation": "buffer_import",
                "source": path,
                "entry": entry,
                "occurrence": occurrences[entry],
                "year": year,
            },
            prepare,
            preview=False,
            idempotency_key=key,
        )
        routed.append(
            {
                "type": kind,
                "plan_id": result["plan_id"],
                "replayed": result["mutation"]["status"] == "idempotent_replay",
            }
        )
        if result["status"] == "applied_pending_index":
            return {
                **result,
                "routed_count": len(routed),
                "routed": routed,
                "buffer_preserved": True,
            }
    return {
        "status": "success",
        "routed_count": len(routed),
        "routed": routed,
        "buffer_preserved": True,
        "message": f"Imported {len(routed)} entries; buffer preserved",
    }
