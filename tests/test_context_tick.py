"""Scheduler poll completeness, stable snapshots and due event integration."""

# ruff: noqa: E402
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_events import commit_event, preview_event
from context_followups import followup_change
from context_store import register_contexts
from context_tick import poll_contexts
from registry import VaultRegistry


def setup(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("Original", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    return registry, config


def register(registry, config):
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": name,
                    "type": "project",
                    "name": name,
                    "purpose": "test",
                    "scopes": ["."],
                }
                for name in ("alpha", "beta")
            ],
        },
    )


def test_empty_and_bounded_context_scan(tmp_path):
    registry, config = setup(tmp_path)
    assert poll_contexts(registry, config)["status"] == "clear"
    register(registry, config)
    result = poll_contexts(registry, config, max_contexts=1)
    assert result["status"] == "incomplete"
    assert result["context_count"] == 2
    assert result["contexts_checked"] == 1


def test_due_followup_poll_and_stable_fingerprint(tmp_path):
    registry, config = setup(tmp_path)
    register(registry, config)
    change = followup_change(
        "alpha", followup_id="test", title="Review", due_at="2026-09-08T09:00:00+08:00"
    )
    event = preview_event(
        registry,
        config,
        "Review",
        target="note.md",
        primary_context="alpha",
        context_ids=[],
        idempotency_key="poll",
        state_change=change,
    )
    commit_event(registry, config, event["event_id"])
    before = (config.root_path / "note.md").read_bytes()
    first = poll_contexts(
        registry, config, now=datetime.fromisoformat("2026-09-09T00:00:00+00:00")
    )
    second = poll_contexts(
        registry, config, now=datetime.fromisoformat("2026-09-10T00:00:00+00:00")
    )
    assert first["status"] == "attention"
    assert first["items"][0]["kind"] == "followup"
    assert first["fingerprint"] == second["fingerprint"]
    assert (config.root_path / "note.md").read_bytes() == before


def test_failure_is_not_clear_and_other_contexts_continue(tmp_path):
    registry, config = setup(tmp_path)
    register(registry, config)
    with patch(
        "context_tick.list_followups",
        side_effect=[ValueError("broken source"), {"truncated": False, "items": []}],
    ) as followups:
        result = poll_contexts(registry, config)
    assert followups.call_count == 2
    assert result["status"] == "incomplete"
    assert result["error_count"] == 1
