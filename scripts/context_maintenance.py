"""Deterministic read-only review queue from explicit per-Context policies."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from context_manager import discover_contexts
from context_state import project_state
from context_store import _bindings, load_context_version
from fts_indexer import scan_notes
from registry import VaultConfig, VaultRegistry


def maintenance_queue(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    limit: int = 20,
    now: datetime | None = None,
    source_scan=None,
) -> dict[str, Any]:
    """Review queue from registered policies.

    `source_scan` lets a caller share one read snapshot across steps, so two parts of the
    same command cannot disagree about what the Vault looked like.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Queue limit must be between 1 and 100")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Queue time must include a timezone")
    version = load_context_version(registry, config)
    if version is None:
        return {
            "schema_version": 1,
            "status": "unregistered",
            "items": [],
            "item_count": 0,
        }
    scan = source_scan or scan_notes(config.root_path)
    # Only Contexts with an enabled policy are ever inspected, so only they need a source
    # baseline. Computing bindings for every Context made a Vault with no maintenance policies
    # pay the full cost for an empty queue.
    enabled = [
        context
        for context in version["definitions"]["contexts"]
        if (context.get("maintenance") or {}).get("enabled", True)
        and context.get("maintenance")
    ]
    if not enabled:
        return {
            "schema_version": 1,
            "vault_id": config.id,
            "definition_revision": version["revision"],
            "status": "no_policies",
            "configured_context_count": 0,
            "retired_contexts": [],
            "item_count": 0,
            "items": [],
            "truncated": False,
            "checked_at": now.isoformat(),
            "writes_performed": 0,
            "notifications_sent": 0,
            "meaning": (
                "Review candidates, not automatic repairs or scheduled reminders. No Context has "
                "an enabled maintenance policy, so nothing was inspected and no source baseline "
                "was computed."
            ),
        }
    discover_contexts(scan, vault_id=config.id, definitions=version["definitions"])
    bindings = _bindings({"contexts": enabled}, scan)
    items = []
    configured = 0
    retired = []

    def add(identity: str, code: str, reason: str, **details: Any) -> None:
        key = f"{config.id}:{identity}:{code}:{version['revision']}:{details.get('due_at', '')}:{details.get('scope', '')}"
        items.append(
            {
                "id": hashlib.sha256(key.encode()).hexdigest()[:24],
                "context_id": identity,
                "code": code,
                "reason": reason,
                **details,
            }
        )

    for context in version["definitions"]["contexts"]:
        policy = context.get("maintenance")
        if not policy or not policy.get("enabled", True):
            continue
        configured += 1
        identity = context["id"]
        state = project_state(registry, config, identity)
        fields = {item["field"]: item for item in state["fields"]}
        lifecycle = fields.get("lifecycle", {})
        if lifecycle.get("status") == "current" and lifecycle.get("value") == "retired":
            retired.append(identity)
            continue
        if state["status"] == "needs_review":
            add(
                identity,
                "state_needs_review",
                "Recorded state has conflicts or unavailable evidence",
            )
        if state["truncated"]:
            add(
                identity,
                "state_truncated",
                "State exceeds the inspection bound; inspect full relevant fields",
            )
        for scope, previous in version["bindings"][identity].items():
            current = bindings[identity][scope]
            if not current:
                add(
                    identity,
                    "scope_without_evidence",
                    "Registered scope has no readable Markdown",
                    scope=scope,
                )
            elif policy.get("check_source_changes", True) and previous != current:
                add(
                    identity,
                    "source_changed",
                    "Sources changed since registration; this may be an ordinary edit",
                    scope=scope,
                )
        interval = policy.get("review_interval_days")
        if interval is None:
            continue
        anchor = datetime.fromisoformat(version["recorded_at"])
        reviewed = fields.get("reviewed_at")
        if reviewed:
            try:
                if reviewed["status"] != "current":
                    raise ValueError("unresolved review timestamp")
                anchor = datetime.fromisoformat(reviewed["value"])
                if anchor.tzinfo is None or anchor.utcoffset() is None or anchor > now:
                    raise ValueError(
                        "review timestamp must have timezone and not be in the future"
                    )
            except (ValueError, TypeError):
                add(
                    identity,
                    "invalid_review_timestamp",
                    "reviewed_at must be a verified non-future ISO timestamp with timezone",
                )
                continue
        due = anchor + timedelta(days=interval)
        if due <= now:
            add(
                identity,
                "review_due",
                "Configured review interval elapsed; modification time does not count as review",
                due_at=due.isoformat(),
                interval_days=interval,
                anchor=anchor.isoformat(),
            )
    items.sort(
        key=lambda item: (item["context_id"], item["code"], str(item.get("scope", "")))
    )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "definition_revision": version["revision"],
        "status": "needs_review"
        if items
        else "no_policies"
        if not configured
        else "current",
        "configured_context_count": configured,
        "retired_contexts": retired[:limit],
        "item_count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
        "checked_at": now.isoformat(),
        "writes_performed": 0,
        "notifications_sent": 0,
        "meaning": "Review candidates, not automatic repairs or scheduled reminders",
    }
