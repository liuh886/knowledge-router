"""Explicit state revisions, conflict preservation and logical retirement."""

# ruff: noqa: E402
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_events import commit_event, preview_event
from context_state import project_state
from context_store import register_contexts
from registry import VaultRegistry


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "notes.md").write_text("# Human original\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": "p",
                    "name": "Project",
                    "type": "project",
                    "purpose": "test",
                    "scopes": ["."],
                }
            ],
        },
    )
    return registry, config


def record(setup, key, value, replaces=None, field="status"):
    registry, config = setup
    event = preview_event(
        registry,
        config,
        "Recorded decision",
        target="notes.md",
        primary_context="p",
        context_ids=[],
        idempotency_key=key,
        state_change={
            "context_id": "p",
            "field": field,
            "value": value,
            "replaces": replaces or [],
        },
    )
    commit_event(registry, config, event["event_id"])
    return event["event_id"]


def test_correction_preserves_original_and_supersedes(setup):
    registry, config = setup
    first = record(setup, "one", "in progress")
    before = (config.root_path / "notes.md").read_bytes()
    record(setup, "two", "completed", [first])
    result = project_state(registry, config, "p")
    assert result["fields"][0]["value"] == "completed"
    assert result["declaration_count"] == 2
    assert (config.root_path / "notes.md").read_bytes().startswith(before)


def test_parallel_claims_are_conflicts_until_explicitly_resolved(setup):
    registry, config = setup
    first = record(setup, "one", "ready")
    second = record(setup, "two", "blocked")
    result = project_state(registry, config, "p")
    assert result["status"] == "needs_review"
    assert result["fields"][0]["value"] is None
    record(setup, "three", "reviewed ready", [first, second])
    assert (
        project_state(registry, config, "p")["fields"][0]["value"] == "reviewed ready"
    )


def test_retirement_and_reopening_are_append_only(setup):
    registry, config = setup
    first = record(setup, "retire", "retired", field="lifecycle")
    assert project_state(registry, config, "p")["fields"][0]["value"] == "retired"
    record(setup, "reopen", "active", [first], field="lifecycle")
    assert project_state(registry, config, "p")["fields"][0]["value"] == "active"
    assert (config.root_path / "notes.md").read_text().startswith("# Human original")


def test_modified_source_abstains_instead_of_restoring_old_value(setup):
    registry, config = setup
    first = record(setup, "one", "ready")
    record(setup, "two", "completed", [first])
    note = config.root_path / "notes.md"
    note.write_text(note.read_text().replace("completed", "altered"), encoding="utf-8")
    assert (
        project_state(registry, config, "p")["fields"][0]["status"]
        == "source_unavailable"
    )
    assert project_state(registry, config, "p")["fields"][0]["value"] is None


def test_replacement_must_match_applied_field(setup):
    first = record(setup, "one", "ready")
    with pytest.raises(ValueError, match="same Context and field"):
        record(setup, "two", "retired", [first], field="lifecycle")
