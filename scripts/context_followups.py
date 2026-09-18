"""Explicit follow-ups backed by canonical Context state events, never a parallel task DB."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from registry import VaultConfig, VaultRegistry


def normalize_followup(value: str) -> str:
    try:
        data = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("Follow-up value must be a JSON object") from exc
    if not isinstance(data, dict) or set(data) != {"title", "due_at", "status"}:
        raise ValueError("Follow-up requires exactly title, due_at and status")
    if (
        not isinstance(data["title"], str)
        or not data["title"].strip()
        or len(data["title"]) > 500
    ):
        raise ValueError("Follow-up title must have 1-500 characters")
    if data["status"] not in ("open", "done", "cancelled"):
        raise ValueError("Follow-up status must be open, done or cancelled")
    if not isinstance(data["due_at"], str):
        raise ValueError("Follow-up due_at must be an ISO timestamp")
    try:
        due = datetime.fromisoformat(data["due_at"])
    except ValueError as exc:
        raise ValueError(
            "Follow-up due_at must be an ISO timestamp with timezone"
        ) from exc
    if due.tzinfo is None or due.utcoffset() is None:
        raise ValueError("Follow-up due_at must include a timezone")
    data["due_at"] = due.astimezone(UTC).isoformat()
    data["title"] = data["title"].strip()
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def followup_change(
    identity: str,
    *,
    followup_id: str,
    title: str,
    due_at: str,
    status: str = "open",
    replaces: list[str] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9_]{1,48}", followup_id):
        raise ValueError(
            "Follow-up id must use 1-48 lowercase letters, digits or underscores"
        )
    value = normalize_followup(
        json.dumps({"title": title, "due_at": due_at, "status": status})
    )
    return {
        "context_id": identity,
        "field": f"followup_{followup_id}",
        "value": value,
        "replaces": replaces or [],
    }


def list_followups(
    registry: VaultRegistry,
    config: VaultConfig,
    context_id: str,
    *,
    due_only: bool = False,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    from context_state import project_state

    if not 1 <= limit <= 100:
        raise ValueError("Follow-up limit must be between 1 and 100")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Follow-up evaluation requires a timezone")
    state = project_state(registry, config, context_id, limit=100)
    items = []
    for field in state["fields"]:
        if not field["field"].startswith("followup_"):
            continue
        identity = field["field"].removeprefix("followup_")
        if field["status"] != "current":
            items.append(
                {
                    "id": identity,
                    "status": "needs_review",
                    "due": None,
                    "reason": field["status"],
                    "candidates": field["candidates"][:limit],
                }
            )
            continue
        try:
            data = json.loads(normalize_followup(field["value"]))
        except ValueError:
            items.append(
                {
                    "id": identity,
                    "status": "needs_review",
                    "due": None,
                    "reason": "invalid_legacy_followup",
                }
            )
            continue
        due = data["status"] == "open" and datetime.fromisoformat(data["due_at"]) <= now
        if due_only and not due:
            continue
        source = field["candidates"][0]
        items.append(
            {
                "id": identity,
                **data,
                "due": due,
                "event_id": source["event_id"],
                "source": source["source"],
                "notification_scheduled": False,
            }
        )
    items.sort(key=lambda item: (item.get("due_at", ""), item["id"]))
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "context_id": context_id,
        "items": items[:limit],
        "item_count": len(items),
        "truncated": len(items) > limit or state["truncated"],
        "checked_at": now.isoformat(),
        "notifications_sent": 0,
        "meaning": "Recorded follow-up state. Listing due items does not schedule or send reminders.",
    }
