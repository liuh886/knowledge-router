"""Scoped retrieval, explicit gaps and live event evidence acceptance."""

# ruff: noqa: E402
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import context_brief
from context_brief import brief_context
from context_events import commit_event, context_history, preview_event
from context_store import register_contexts
from registry import VaultRegistry


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "one.md").write_text(
        "# Progress\nCobalt telescope requires calibration\n- [ ] Check sensor\n",
        encoding="utf-8",
    )
    (root / "other.md").write_text(
        "# Other\nCobalt telescope unrelated secret\n", encoding="utf-8"
    )
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": "project:one",
                    "name": "Alpha",
                    "type": "project",
                    "purpose": "test",
                    "scopes": ["one.md"],
                },
                {
                    "id": "daily:all",
                    "name": "Daily",
                    "type": "daily",
                    "purpose": "test",
                    "scopes": ["."],
                },
            ],
        },
    )
    return registry, config


def test_scoped_live_evidence_and_no_index_required(setup):
    registry, config = setup
    result = brief_context(registry, config, "Alpha", query="Cobalt telescope")
    assert not result["evidence_gap"]
    assert {e["path"] for e in result["evidence"]} == {"one.md"}
    assert result["evidence"][0]["line_start"] == 1
    assert result["evidence"][0]["stale"] is False
    assert brief_context(registry, config, "Alpha", query="unobtainium")["evidence_gap"]
    with pytest.raises(ValueError, match="missing or ambiguous"):
        brief_context(registry, config, "Not registered")


def test_changed_source_during_read_is_excluded(setup, monkeypatch):
    registry, config = setup
    monkeypatch.setattr(
        context_brief, "secure_read_bytes", lambda *args: b"different content"
    )
    result = brief_context(registry, config, "Alpha")
    assert result["evidence_gap"]
    assert result["sources_changed_during_read"] == ["one.md"]


def test_linked_event_outside_scope_is_available_and_freshness_is_checked(setup):
    registry, config = setup
    event = preview_event(
        registry,
        config,
        "Amber detector accepted",
        target="event.md",
        primary_context="daily:all",
        context_ids=["project:one"],
        idempotency_key="event",
    )
    commit_event(registry, config, event["event_id"])
    history = context_history(registry, config, "project:one")
    assert history["events"][0]["source_status"] == "unchanged"
    result = brief_context(registry, config, "Alpha", query="Amber detector")
    assert result["evidence"][0]["path"] == "event.md"
    (config.root_path / "event.md").write_text("Updated source", encoding="utf-8")
    assert context_history(registry, config, "project:one")["events"][0]["stale"]
    assert brief_context(registry, config, "Alpha", query="Amber detector")[
        "evidence_gap"
    ]
    (config.root_path / "event.md").unlink()
    assert (
        context_history(registry, config, "project:one")["events"][0]["source_status"]
        == "unavailable"
    )
