"""Acceptance tests for the explicit local follow-up delivery boundary."""

# ruff: noqa: E402
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import context_delivery
from context_delivery import deliver_followups, delivery_status
from context_events import commit_event, preview_event
from context_followups import followup_change
from context_store import register_contexts
from registry import VaultRegistry


def _setup(tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="delivery", write_policy="append-create")
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": "project:router",
                    "type": "project",
                    "name": "Router",
                    "purpose": "delivery test",
                    "scopes": ["."],
                }
            ],
        },
    )
    return registry, config


def _record(registry, config, key: str, *, status: str = "open", replaces=None):
    change = followup_change(
        "project:router",
        followup_id="review",
        title="Review delivery receipt",
        due_at="2026-09-09T09:00:00+08:00",
        status=status,
        replaces=replaces,
    )
    event = preview_event(
        registry,
        config,
        "Delivery test event",
        target="notes.md",
        primary_context="project:router",
        context_ids=[],
        idempotency_key=key,
        state_change=change,
    )
    commit_event(registry, config, event["event_id"])
    return event["event_id"]


def test_delivery_is_durable_idempotent_and_reports_status(tmp_path: Path):
    registry, config = _setup(tmp_path)
    event_id = _record(registry, config, "open")
    sink = tmp_path / "notifications.jsonl"
    now = datetime.fromisoformat("2026-09-09T01:00:00+00:00")

    first = deliver_followups(registry, config, sink, now=now)
    assert first["status"] == "delivered"
    assert first["delivered_count"] == 1
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 1
    status = delivery_status(registry, config)
    assert status["configured"] is True
    assert status["pending"] == 0
    assert status["delivered"] == 1

    second = deliver_followups(registry, config, sink, now=now)
    assert second["delivered_count"] == 0
    assert second["skipped_count"] == 1
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 1
    assert any(
        json.loads(line)["event_id"] == event_id
        for line in sink.read_text(encoding="utf-8").splitlines()
    )


def test_closed_followup_cancels_undelivered_intent(tmp_path: Path, monkeypatch):
    registry, config = _setup(tmp_path)
    event_id = _record(registry, config, "open")
    sink = tmp_path / "notifications.jsonl"
    original_append_sink = context_delivery._append_sink
    monkeypatch.setattr(
        context_delivery,
        "_append_sink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sink unavailable")),
    )
    due = datetime.fromisoformat("2026-09-09T01:00:00+00:00")
    failed = deliver_followups(registry, config, sink, now=due)
    assert failed["failed_count"] == 1
    _record(registry, config, "done", status="done", replaces=[event_id])
    monkeypatch.setattr(context_delivery, "_append_sink", original_append_sink)
    cancelled = deliver_followups(registry, config, sink, now=due)
    assert cancelled["cancelled_count"] == 1


def test_delivery_rejects_vault_internal_sink(tmp_path: Path):
    registry, config = _setup(tmp_path)
    try:
        deliver_followups(registry, config, config.root_path / "notifications.jsonl")
    except ValueError as exc:
        assert "outside" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Vault-internal delivery sink was accepted")


def test_cli_delivery_path_uses_explicit_sink(tmp_path: Path):
    registry, config = _setup(tmp_path)
    _record(registry, config, "cli-open")
    sink = tmp_path / "cli-notifications.jsonl"
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--state-root",
            str(registry.state_root),
            "contexts",
            "deliver",
            "--vault",
            config.id,
            "--sink",
            str(sink),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["delivered_count"] == 1
