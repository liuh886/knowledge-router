"""Explicit, idempotent follow-up delivery to a user-selected local sink.

The Context scheduler remains a poller.  This module is the deliberately
separate delivery boundary: it records an intent before writing a notification
to a local JSONL sink, then records the outcome.  All receipts live in the
external Vault state directory and never in the Obsidian Vault.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from context_events import _read
from context_followups import list_followups
from context_store import load_context_version
from registry import FileLock, VaultConfig, VaultRegistry
from secure_io import SecureParent, is_reparse

_SCHEMA_VERSION = 1
_DELIVERY_JOURNAL = "context_delivery.jsonl"
_DELIVERY_LOCK = ".context-delivery.lock"


def _now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Delivery time must include a timezone")
    return value


def _append_state_record(
    registry: VaultRegistry, config: VaultConfig, payload: dict[str, Any]
) -> None:
    """Append one receipt using the same guarded state primitive as maintenance."""
    state = registry.vault_state_dir(config)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / _DELIVERY_LOCK, pinned_parent=guard):
            try:
                descriptor = guard.open_existing(
                    writable=True, exclusive_writes=False, name=_DELIVERY_JOURNAL
                )
            except FileNotFoundError:
                descriptor = guard.create_new(name=_DELIVERY_JOURNAL)
            try:
                os.lseek(descriptor, 0, os.SEEK_END)
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("Short context delivery receipt write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def _read_state_records(
    registry: VaultRegistry, config: VaultConfig
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    with registry.guard_vault_state(config) as guard:
        try:
            raw = guard.read_bytes(name=_DELIVERY_JOURNAL)
        except FileNotFoundError:
            return records, errors
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{_DELIVERY_JOURNAL}:{line_number}: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{_DELIVERY_JOURNAL}:{line_number}: receipt is not an object")
            continue
        records.append(value)
    return records, errors


def _sink_path(config: VaultConfig, sink: Path, state: Path) -> Path:
    candidate = sink.expanduser()
    if not candidate.is_absolute():
        raise ValueError("Delivery sink must be an absolute path")
    candidate = candidate.absolute()
    try:
        information = candidate.lstat()
    except FileNotFoundError:
        information = None
    if information is not None and (
        stat.S_ISLNK(information.st_mode) or is_reparse(information)
    ):
        raise ValueError("Delivery sink cannot be a symlink or reparse point")
    resolved = candidate.resolve(strict=False)
    for protected in (config.root_path.resolve(), state.resolve()):
        try:
            resolved.relative_to(protected)
        except ValueError:
            continue
        raise ValueError("Delivery sink must stay outside the Vault and its external state")
    if not candidate.parent.is_dir():
        raise FileNotFoundError(f"Delivery sink parent does not exist: {candidate.parent}")
    if candidate.name in {"", ".", ".."} or "/" in candidate.name or "\\" in candidate.name:
        raise ValueError("Delivery sink filename is unsafe")
    return candidate


def _read_sink_ids(sink: Path) -> tuple[set[str], list[str]]:
    if not sink.exists():
        return set(), []
    try:
        with SecureParent(sink.parent, sink.name, allow_hidden=True) as parent:
            raw = parent.read_bytes(name=sink.name)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Delivery sink is not safely readable: {sink}: {exc}") from exc
    ids: set[str] = set()
    errors: list[str] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"sink:{line_number}: {exc}")
            continue
        if isinstance(value, dict) and isinstance(value.get("notification_id"), str):
            ids.add(value["notification_id"])
    return ids, errors


def _append_sink(sink: Path, payload: dict[str, Any]) -> bool:
    """Append atomically under a sibling lock; return False if already present."""
    lock = sink.parent / f".{sink.name}.lock"
    with FileLock(lock):
        existing, _ = _read_sink_ids(sink)
        notification_id = str(payload["notification_id"])
        if notification_id in existing:
            return False
        with SecureParent(sink.parent, sink.name, allow_hidden=True) as parent:
            try:
                descriptor = parent.open_existing(
                    writable=True, exclusive_writes=False, name=sink.name
                )
            except FileNotFoundError:
                descriptor = parent.create_new(name=sink.name)
            try:
                encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
                    "utf-8"
                )
                os.lseek(descriptor, 0, os.SEEK_END)
                written = os.write(descriptor, encoded)
                if written != len(encoded):
                    raise OSError("Short delivery sink write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return True


def _context_ids(registry: VaultRegistry, config: VaultConfig) -> list[str]:
    ids: set[str] = set()
    version = load_context_version(registry, config)
    if version:
        ids.update(str(item["id"]) for item in version["definitions"]["contexts"])
    for path in sorted(registry.vault_state_dir(config).glob("context-event.*.applied.json")):
        event_id = path.name.split(".")[1]
        event = _read(registry, config, event_id, "applied")
        if event:
            ids.update(str(value) for value in event.get("context_ids", []))
            primary = event.get("primary_context")
            if primary:
                ids.add(str(primary))
    return sorted(ids)


def _notification_id(
    vault_id: str, context_id: str, item: dict[str, Any]
) -> str:
    identity = "\x1f".join(
        (vault_id, context_id, str(item["id"]), str(item["event_id"]), str(item["due_at"]))
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def delivery_status(
    registry: VaultRegistry, config: VaultConfig, *, limit: int = 20
) -> dict[str, Any]:
    """Summarize the durable delivery ledger without creating a sink."""
    if not 1 <= limit <= 100:
        raise ValueError("Delivery status limit must be between 1 and 100")
    records, errors = _read_state_records(registry, config)
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        notification_id = record.get("notification_id")
        if isinstance(notification_id, str):
            latest[notification_id] = record
    values = list(latest.values())
    delivered = [item for item in values if item.get("status") == "delivered"]
    pending = [item for item in values if item.get("status") == "pending"]
    failed = [item for item in values if item.get("status") == "failed"]
    configured = bool(values)
    result: dict[str, Any] = {
        "configured": configured,
        "mode": "local_file" if configured else None,
        "pending": len(pending),
        "delivered": len(delivered),
        "failed": len(failed),
        "cancelled": sum(item.get("status") == "cancelled" for item in values),
        "last_acknowledged": max(
            (item.get("recorded_at") for item in delivered if item.get("recorded_at")),
            default=None,
        ),
        "sink": next(
            (item.get("sink") for item in reversed(records) if item.get("sink")), None
        ),
        "error_count": len(errors),
        "errors": errors[:limit],
    }
    if not configured and not errors:
        return {
            "configured": False,
            "pending": 0,
            "last_acknowledged": None,
            "meaning": "Router emits poll results; notification delivery is not configured here",
        }
    result["meaning"] = (
        "Explicit local-file delivery ledger; remote transport delivery remains a separate adapter"
    )
    return result


def deliver_followups(
    registry: VaultRegistry,
    config: VaultConfig,
    sink: Path,
    *,
    limit: int = 20,
    retry_failed: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Deliver due open follow-ups once to a local JSONL sink.

    The pending receipt is written before the sink, and the sink itself is
    deduplicated by ``notification_id``.  This makes a retry safe after a
    process crash between the two receipts.  Closed or replaced follow-ups
    cancel only undelivered intents; a delivered reminder remains historical.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Delivery limit must be between 1 and 100")
    moment = _now(now)
    state = registry.vault_state_dir(config)
    sink_path = _sink_path(config, sink, state)
    sink_ids, sink_errors = _read_sink_ids(sink_path)
    records, read_errors = _read_state_records(registry, config)
    errors = [*sink_errors, *read_errors]
    latest: dict[str, dict[str, Any]] = {}
    attempts: dict[str, int] = {}
    for record in records:
        notification_id = record.get("notification_id")
        if not isinstance(notification_id, str):
            continue
        latest[notification_id] = record
        try:
            attempt = int(record.get("attempt", 0))
        except (TypeError, ValueError):
            errors.append(f"invalid attempt for notification {notification_id}")
            attempt = 0
        attempts[notification_id] = max(attempts.get(notification_id, 0), attempt)

    due: list[tuple[str, dict[str, Any]]] = []
    current_keys: set[tuple[str, str, str]] = set()
    contexts_checked = 0
    for context_id in _context_ids(registry, config):
        if contexts_checked >= 100:
            break
        contexts_checked += 1
        try:
            result = list_followups(
                registry, config, context_id, due_only=False, limit=100, now=moment
            )
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            errors.append(f"{context_id}: {exc}")
            continue
        for item in result["items"]:
            event_id = str(item.get("event_id", ""))
            key = (context_id, str(item["id"]), event_id)
            if item.get("status") == "open":
                current_keys.add(key)
            if item.get("status") == "open" and item.get("due"):
                due.append((context_id, item))

    delivered = failed = skipped = cancelled = 0
    receipt_count = 0
    for context_id, item in due[:limit]:
        notification_id = _notification_id(config.id, context_id, item)
        previous = latest.get(notification_id, {})
        status = previous.get("status")
        if status == "delivered" or (status == "failed" and not retry_failed):
            skipped += 1
            continue
        attempt = attempts.get(notification_id, 0) + 1
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "notification_id": notification_id,
            "vault_id": config.id,
            "context_id": context_id,
            "followup_id": item["id"],
            "title": item["title"],
            "due_at": item["due_at"],
            "event_id": item["event_id"],
            "source": item.get("source"),
            "sink": str(sink_path),
        }
        _append_state_record(
            registry,
            config,
            {
                **payload,
                "status": "pending",
                "attempt": attempt,
                "recorded_at": moment.isoformat(),
            },
        )
        receipt_count += 1
        try:
            appended = _append_sink(sink_path, {**payload, "delivered_at": moment.isoformat()})
            _append_state_record(
                registry,
                config,
                {
                    **payload,
                    "status": "delivered",
                    "attempt": attempt,
                    "deduplicated": not appended,
                    "recorded_at": moment.isoformat(),
                },
            )
            receipt_count += 1
            delivered += 1
            sink_ids.add(notification_id)
        except (OSError, ValueError, RuntimeError) as exc:
            _append_state_record(
                registry,
                config,
                {
                    **payload,
                    "status": "failed",
                    "attempt": attempt,
                    "error": str(exc)[:500],
                    "recorded_at": moment.isoformat(),
                },
            )
            receipt_count += 1
            failed += 1

    # A pending/failed intent whose source event is no longer the open head is
    # cancelled.  Delivered reminders remain immutable history.
    for notification_id, previous in list(latest.items()):
        if previous.get("status") not in {"pending", "failed"}:
            continue
        identity = (
            str(previous.get("context_id", "")),
            str(previous.get("followup_id", "")),
            str(previous.get("event_id", "")),
        )
        if identity in current_keys:
            continue
        _append_state_record(
            registry,
            config,
            {
                **{key: previous[key] for key in ("notification_id", "vault_id", "context_id", "followup_id", "event_id", "sink") if key in previous},
                "status": "cancelled",
                "attempt": attempts.get(notification_id, 0),
                "reason": "followup_closed_or_replaced",
                "recorded_at": moment.isoformat(),
            },
        )
        receipt_count += 1
        cancelled += 1

    return {
        "schema_version": _SCHEMA_VERSION,
        "vault_id": config.id,
        "sink": str(sink_path),
        "checked_at": moment.isoformat(),
        "contexts_checked": contexts_checked,
        "due_count": len(due),
        "delivered_count": delivered,
        "failed_count": failed,
        "skipped_count": skipped,
        "cancelled_count": cancelled,
        "receipt_count": receipt_count,
        "retry_failed": retry_failed,
        "errors": errors[:limit],
        "error_count": len(errors),
        "status": "failed" if failed or errors else "delivered" if delivered else "clear",
        "meaning": "Local sink delivery with durable pending/delivered/failed/cancelled receipts; remote transport is not implied.",
    }
