"""Rebuild explicit Context state from source-verified, append-only event declarations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from capture_broker import load_capture_plan
from registry import VaultConfig, VaultRegistry
from secure_io import secure_read_source_bytes as secure_read_bytes


def normalize_change(change: dict[str, Any], contexts: list[str]) -> dict[str, Any]:
    if not isinstance(change, dict):
        raise ValueError("State change must be an object")
    identity, field, value = (change.get(k) for k in ("context_id", "field", "value"))
    if (
        identity not in contexts
        or not isinstance(field, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field)
    ):
        raise ValueError("State change must name a linked Context and a valid field")
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("State value must be a nonempty string of at most 2000 characters")
    if field == "lifecycle" and value not in {"active", "retired"}:
        raise ValueError("Lifecycle must be active or retired")
    if field.startswith("followup_"):
        from context_followups import normalize_followup

        if not re.fullmatch(r"followup_[a-z0-9_]{1,48}", field):
            raise ValueError("Invalid follow-up field id")
        value = normalize_followup(value)
    replaces = change.get("replaces", [])
    if not isinstance(replaces, list) or not all(
        isinstance(e, str) and re.fullmatch(r"[a-f0-9]{32}", e) for e in replaces
    ):
        raise ValueError("replaces must contain event ids")
    return {
        "context_id": identity,
        "field": field,
        "value": value,
        "replaces": sorted(set(replaces)),
    }


def declaration_text(change: dict[str, Any]) -> str:
    return (
        "\n\n## Context state declaration\n\n```json\n"
        + json.dumps(change, ensure_ascii=False, sort_keys=True)
        + "\n```"
    )


def validate_replacements(
    registry: VaultRegistry, config: VaultConfig, change: dict[str, Any]
) -> None:
    from context_events import _read

    for event_id in change["replaces"]:
        event = _read(registry, config, event_id, "plan")
        receipt = _read(registry, config, event_id, "applied")
        previous = event.get("state_change") if event else None
        if (
            not receipt
            or not previous
            or previous["context_id"] != change["context_id"]
            or previous["field"] != change["field"]
        ):
            raise ValueError(
                "Replacement must reference an applied declaration for the same Context and field"
            )


def project_state(
    registry: VaultRegistry, config: VaultConfig, context_id: str, *, limit: int = 20
) -> dict[str, Any]:
    from context_events import _read

    if not 1 <= limit <= 100:
        raise ValueError("State limit must be between 1 and 100")
    declarations = {}
    for path in registry.vault_state_dir(config).glob("context-event.*.applied.json"):
        event_id = path.name.split(".")[1]
        event = _read(registry, config, event_id, "plan")
        receipt = _read(registry, config, event_id, "applied")
        change = event.get("state_change") if event else None
        if not change or change["context_id"] != context_id:
            continue
        if not receipt or receipt["plan_id"] != event["plan_id"]:
            raise ValueError("State event receipt mismatch")
        plan = load_capture_plan(registry.vault_state_dir(config), event["plan_id"])
        if plan.plan_sha256 != event["plan_sha256"]:
            raise ValueError("State event plan mismatch")
        # The declaration must also live in the canonical note payload, not only a DB.
        if declaration_text(change).replace("\n", "").encode() not in plan.payload.replace(
            b"\r", b""
        ).replace(b"\n", b""):
            raise ValueError("State declaration is not backed by its canonical payload")
        available = False
        try:
            raw = secure_read_bytes(config.root_path, plan.relative_path)
            end = plan.before_size + len(plan.payload)
            available = (
                raw[plan.before_size : end] == plan.payload
                and hashlib.sha256(raw[:end]).hexdigest() == receipt["mutation"]["after_sha256"]
            )
            line_start = raw[: plan.before_size].count(b"\n") + 1
        except (OSError, ValueError):
            line_start = None
        declarations[event_id] = {
            **change,
            "event_id": event_id,
            "source_verified": available,
            "source": {
                "path": plan.relative_path,
                "line_start": line_start,
                "source_sha256_at_commit": receipt["mutation"]["after_sha256"],
            },
        }
    fields = []
    for field in sorted({d["field"] for d in declarations.values()}):
        changes = [d for d in declarations.values() if d["field"] == field]
        superseded = {event_id for d in changes for event_id in d["replaces"]}
        heads = [d for d in changes if d["event_id"] not in superseded]
        heads.sort(key=lambda d: d["event_id"])
        status = (
            "conflict"
            if len(heads) > 1
            else "current"
            if heads and heads[0]["source_verified"]
            else "source_unavailable"
        )
        fields.append(
            {
                "field": field,
                "status": status,
                "value": heads[0]["value"] if status == "current" else None,
                "candidates": heads[:limit],
                "candidate_count": len(heads),
            }
        )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "context_id": context_id,
        "status": "evidence_gap"
        if not fields
        else "needs_review"
        if any(f["status"] != "current" for f in fields)
        else "current",
        "fields": fields[:limit],
        "field_count": len(fields),
        "truncated": len(fields) > limit,
        "declaration_count": len(declarations),
        "original_notes_modified": False,
        "meaning": "Explicit recorded state; no inferred completion or physical deletion. Multiple unsuperseded declarations remain conflicts.",
    }
