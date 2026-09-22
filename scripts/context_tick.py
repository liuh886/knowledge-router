"""One read-only scheduler poll across registered and event-linked Contexts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from context_events import _read
from context_followups import list_followups
from context_maintenance import maintenance_queue
from context_store import load_context_version
from registry import VaultConfig, VaultRegistry


def poll_contexts(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    limit: int = 20,
    max_contexts: int = 100,
    now: datetime | None = None,
    source_scan=None,
) -> dict[str, Any]:
    """One scheduler poll.

    `source_scan` shares a caller's read snapshot instead of scanning the Vault again, so
    every step of one command sees the same source state.
    """
    if not 1 <= limit <= 100 or not 1 <= max_contexts <= 1000:
        raise ValueError("Tick limits must be 1-100 items and 1-1000 Contexts")
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Tick time must include a timezone")
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    incomplete = False
    ids: set[str] = set()
    try:
        version = load_context_version(registry, config)
        if version:
            ids.update(c["id"] for c in version["definitions"]["contexts"])
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        errors.append({"stage": "definitions", "error": str(exc)})
    maintenance: dict[str, Any] = {}
    try:
        maintenance = maintenance_queue(
            registry, config, limit=100, now=now, source_scan=source_scan
        )
        items.extend({"kind": "maintenance", **i} for i in maintenance["items"])
        incomplete |= maintenance.get("truncated", False)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        errors.append({"stage": "maintenance", "error": str(exc)})
    for path in sorted(
        registry.vault_state_dir(config).glob("context-event.*.applied.json")
    ):
        try:
            record = _read(registry, config, path.name.split(".")[1], "applied")
            if record:
                ids.update(record["context_ids"])
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            errors.append({"stage": "event_receipt", "error": str(exc)})
    ordered = sorted(ids)
    incomplete |= len(ordered) > max_contexts
    for identity in ordered[:max_contexts]:
        try:
            result = list_followups(
                registry, config, identity, due_only=True, limit=100, now=now
            )
            incomplete |= result["truncated"]
            items.extend(
                {"kind": "followup", "context_id": identity, **item}
                for item in result["items"]
            )
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            errors.append(
                {"stage": "followups", "context_id": identity, "error": str(exc)}
            )
    items.sort(key=lambda item: (item["kind"], item["context_id"], item["id"]))
    fingerprint = hashlib.sha256(
        json.dumps(
            {"items": items, "errors": errors, "incomplete": incomplete},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "checked_at": now.isoformat(),
        "status": "incomplete"
        if errors or incomplete
        else "attention"
        if items
        else "clear",
        "fingerprint": fingerprint,
        "context_count": len(ids),
        "contexts_checked": min(len(ids), max_contexts),
        "item_count": len(items),
        "items": items[:limit],
        "errors": errors[:limit],
        "error_count": len(errors),
        "scan_incomplete": incomplete or bool(errors),
        "output_truncated": len(items) > limit or len(errors) > limit,
        # Surfaced separately so an empty poll is not read as "maintenance is covered": with no
        # enabled policy nothing was inspected at all.
        "maintenance_status": maintenance.get("status") if maintenance else None,
        "maintenance_configured_contexts": int(
            (maintenance or {}).get("configured_context_count", 0)
        ),
        "maintenance_retired_contexts": list((maintenance or {}).get("retired_contexts", ())),
        "notifications_sent": 0,
        "writes_performed": 0,
        "meaning": "Poll result, not a delivery receipt. Compare fingerprints only after a complete scan; acknowledge notifications separately.",
    }
