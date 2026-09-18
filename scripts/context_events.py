"""One broker-backed event with durable Context links and resumable indexing.

Immutable external event/applied/completed records make interrupted stages visible.
Only the capture broker may write a Vault source. A retry always uses its frozen plan.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import PurePosixPath
from typing import Any

from capture_broker import commit_capture_plan, create_capture_plan, load_capture_plan
from context_manager import _scope, _under, discover_contexts
from context_store import load_context_version
from fts_indexer import scan_notes
from registry import VaultConfig, VaultRegistry
from route_engine import RoutingResult
from secure_io import secure_read_bytes


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _filename(event_id: str, stage: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", event_id):
        raise ValueError("Invalid Context event id")
    return f"context-event.{event_id}.{stage}.json"


def _read(
    registry: VaultRegistry, config: VaultConfig, event_id: str, stage: str
) -> dict[str, Any] | None:
    with registry.guard_vault_state(config) as guard:
        try:
            fd = guard.open_existing(writable=False, name=_filename(event_id, stage))
        except FileNotFoundError:
            return None
        try:
            chunks = []
            while chunk := os.read(fd, 1024 * 1024):
                chunks.append(chunk)
        finally:
            os.close(fd)
    data = json.loads(b"".join(chunks).decode("utf-8"))
    checksum = data.pop("checksum", None)
    if (
        checksum != _hash(data)
        or data.get("vault_id") != config.id
        or data.get("event_id") != event_id
    ):
        raise ValueError("Context event checksum or binding mismatch")
    return data


def _publish(
    registry: VaultRegistry,
    config: VaultConfig,
    event_id: str,
    stage: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    # Publish a fully fsynced temporary file through an exclusive hard link. Readers
    # never see a partially written final record; competing publishers keep the winner.
    import uuid

    data = {"schema_version": 1, "vault_id": config.id, "event_id": event_id, **value}
    payload = json.dumps({**data, "checksum": _hash(data)}, ensure_ascii=False).encode(
        "utf-8"
    )
    state = registry.vault_state_dir(config)
    temporary = f"context-event-tmp.{uuid.uuid4().hex}.json"
    with registry.guard_vault_state(config) as guard:
        fd = guard.create_new(name=temporary)
        try:
            if os.write(fd, payload) != len(payload):
                raise OSError("Short Context event record write")
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(state / temporary, state / _filename(event_id, stage))
        except FileExistsError:
            pass
        finally:
            (state / temporary).unlink()
    stored = _read(registry, config, event_id, stage)
    if stored is None:
        raise RuntimeError("Context event publication failed")
    return stored


def preview_event(
    registry: VaultRegistry,
    config: VaultConfig,
    text: str,
    *,
    target: str,
    primary_context: str,
    context_ids: list[str],
    idempotency_key: str,
    state_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from context_state import declaration_text, normalize_change, validate_replacements

    if not text.strip() or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        raise ValueError(
            "Nonempty event text and a stable 1-128 character idempotency key are required"
        )
    target = _scope(target)
    selected = sorted(set([primary_context, *context_ids]))
    request = {
        "text": text,
        "target": target,
        "primary_context": primary_context,
        "contexts": selected,
    }
    if state_change is not None:
        state_change = normalize_change(state_change, selected)
        request["state_change"] = state_change
    event_id = _hash([config.id, idempotency_key])[:32]
    previous = _read(registry, config, event_id, "plan")
    if previous is not None:
        if previous["request_hash"] != _hash(request):
            raise ValueError(
                "Context event key is already bound to a different request"
            )
        return inspect_event(registry, config, event_id)
    version = load_context_version(registry, config)
    if state_change is not None:
        validate_replacements(registry, config, state_change)
    scan = scan_notes(config.root_path)
    catalog = discover_contexts(
        scan,
        vault_id=config.id,
        definitions=version["definitions"] if version else None,
    )
    contexts = {c["id"]: c for c in catalog["contexts"]}
    for identity in selected:
        if identity not in contexts or not contexts[identity]["source_count"]:
            raise ValueError(f"Context has no resolved source: {identity}")
    if not any(_under(target, scope) for scope in contexts[primary_context]["scopes"]):
        raise ValueError("Event target must belong to the selected primary Context")
    existing = next((n for n in scan.records if n.path == target), None)
    path = PurePosixPath(target)
    routing = RoutingResult(
        "context_event",
        "append_existing" if existing else "create_new",
        path.parent.as_posix(),
        path.name,
        target,
        1.0,
        "Explicit primary Context and target; one event, external links",
    )
    plan = create_capture_plan(
        config.root_path,
        registry.vault_state_dir(config),
        text + (declaration_text(state_change) if state_change else ""),
        routing,
        expected_sha256=existing.checksum if existing else None,
        idempotency_key=f"context:{event_id}",
    )
    # A collision suffix must still fit a single-note primary binding.
    if not any(
        _under(plan.relative_path, scope)
        for scope in contexts[primary_context]["scopes"]
    ):
        raise ValueError("Frozen capture destination left the primary Context scope")
    stored = _publish(
        registry,
        config,
        event_id,
        "plan",
        {
            "request_hash": _hash(request),
            "plan_id": plan.plan_id,
            "plan_sha256": plan.plan_sha256,
            "definition_revision": version["revision"] if version else None,
            "primary_context": primary_context,
            "context_links": [contexts[i] for i in selected],
            "target": plan.relative_path,
            "source_generation": catalog["source_generation"],
            "state_change": state_change,
        },
    )
    if stored["request_hash"] != _hash(request):
        raise ValueError("Context event key was concurrently bound to another request")
    return inspect_event(registry, config, event_id)


def inspect_event(
    registry: VaultRegistry, config: VaultConfig, event_id: str
) -> dict[str, Any]:
    event = _read(registry, config, event_id, "plan")
    if event is None:
        raise FileNotFoundError(f"Context event not found: {event_id}")
    plan = load_capture_plan(registry.vault_state_dir(config), event["plan_id"])
    if plan.plan_sha256 != event["plan_sha256"] or plan.vault_id != config.id:
        raise ValueError("Context event and frozen capture plan mismatch")
    applied = _read(registry, config, event_id, "applied")
    completed = _read(registry, config, event_id, "completed")
    state = None
    if event.get("state_change"):
        from context_state import project_state

        state = project_state(registry, config, event["state_change"]["context_id"])
    return {
        **event,
        "status": "completed"
        if completed
        else "applied_pending_index"
        if applied
        else "preview_or_interrupted",
        "capture": {
            "action": plan.action,
            "target": plan.relative_path,
            "payload": plan.payload.decode("utf-8-sig"),
            "expires_at": plan.expires_at,
        },
        "applied": applied,
        "completed": completed,
        "recorded_state": state,
        "note": "If interrupted before applied receipt, retry commit to reconcile the broker journal",
    }


def commit_event(
    registry: VaultRegistry, config: VaultConfig, event_id: str
) -> dict[str, Any]:
    from vault_service import sync_vault

    event = inspect_event(registry, config, event_id)
    if event["completed"]:
        return event
    # Broker revalidates policy, exact plan, source hash and idempotent recovery.
    mutation = commit_capture_plan(
        config.root_path, registry.vault_state_dir(config), event["plan_id"]
    )
    _publish(
        registry,
        config,
        event_id,
        "applied",
        {
            "plan_id": event["plan_id"],
            "mutation": mutation.to_dict(),
            "context_ids": [c["id"] for c in event["context_links"]],
        },
    )
    sync = sync_vault(registry, registry.resolve(config.id))
    _publish(
        registry,
        config,
        event_id,
        "completed",
        {
            "plan_id": event["plan_id"],
            "index_generation": sync["generation"],
            "meaning": "Canonical event applied; links recorded; retrieval index refreshed",
        },
    )
    return inspect_event(registry, config, event_id)


def context_history(
    registry: VaultRegistry, config: VaultConfig, context_id: str, *, limit: int = 20
) -> dict[str, Any]:
    """Return committed event references, not a synthesized current-state brief."""
    if not 1 <= limit <= 100:
        raise ValueError("History limit must be between 1 and 100")
    records = []
    for path in registry.vault_state_dir(config).glob("context-event.*.applied.json"):
        event_id = path.name.split(".")[1]
        applied = _read(registry, config, event_id, "applied")
        if applied is None or context_id not in applied["context_ids"]:
            continue
        event = _read(registry, config, event_id, "plan")
        if event is None or event["plan_id"] != applied["plan_id"]:
            raise ValueError("Context history event/receipt mismatch")
        records.append(
            {
                "event_id": event_id,
                "path": applied["mutation"]["relative_path"],
                "source_sha256_at_commit": applied["mutation"]["after_sha256"],
                "current_source_verified": False,
                "index_completed": _read(registry, config, event_id, "completed")
                is not None,
            }
        )
        record = records[-1]
        try:
            raw = secure_read_bytes(config.root_path, record["path"])
            record["current_source_sha256"] = hashlib.sha256(raw).hexdigest()
            record["current_source_verified"] = True
            record["stale"] = (
                record["current_source_sha256"] != record["source_sha256_at_commit"]
            )
            record["source_status"] = "changed" if record["stale"] else "unchanged"
        except (OSError, ValueError):
            record["current_source_sha256"] = None
            record["stale"] = True
            record["source_status"] = "unavailable"
    records.sort(key=lambda row: row["event_id"])
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "context_id": context_id,
        "event_count": len(records),
        "events": records[:limit],
        "truncated": len(records) > limit,
        "ordering": "stable_event_id",
        "meaning": "Historical receipts; use source search before drawing current conclusions",
    }
