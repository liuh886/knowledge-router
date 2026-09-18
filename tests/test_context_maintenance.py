"""Opt-in review policy, deterministic due dates and source drift."""

# ruff: noqa: E402
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_events import commit_event, preview_event
from context_maintenance import maintenance_queue
from context_store import load_context_version, register_contexts
from registry import VaultRegistry


def setup(tmp_path, policy=None):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("Original", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    definition = {
        "id": "p",
        "type": "project",
        "name": "Project",
        "purpose": "test",
        "scopes": ["."],
    }
    if policy is not None:
        definition["maintenance"] = policy
    register_contexts(registry, config, {"schema_version": 1, "contexts": [definition]})
    anchor = datetime.fromisoformat(
        load_context_version(registry, config)["recorded_at"]
    )
    return registry, config, anchor


def test_opt_in_and_due_boundary(tmp_path):
    registry, config, anchor = setup(tmp_path, {"review_interval_days": 7})
    assert (
        maintenance_queue(registry, config, now=anchor + timedelta(days=6))["status"]
        == "current"
    )
    now = anchor + timedelta(days=7)
    first = maintenance_queue(registry, config, now=now)
    assert first["items"][0]["code"] == "review_due"
    assert maintenance_queue(registry, config, now=now)["items"] == first["items"]
    assert (config.root_path / "note.md").read_text() == "Original"


def test_absent_policy_is_not_universal_expiry(tmp_path):
    registry, config, anchor = setup(tmp_path)
    assert (
        maintenance_queue(registry, config, now=anchor + timedelta(days=3000))["status"]
        == "no_policies"
    )


@pytest.mark.parametrize(
    "policy",
    [
        {"review_interval_days": True},
        {"review_interval_days": 0},
        {"enabled": "yes"},
        {"unknown": 7},
    ],
)
def test_policy_typos_and_bad_types_rejected(tmp_path, policy):
    with pytest.raises(ValueError):
        setup(tmp_path, policy)


def test_edit_is_not_a_review_and_missing_scope_is_reported(tmp_path):
    registry, config, anchor = setup(tmp_path, {"review_interval_days": 1})
    (config.root_path / "note.md").write_text("New edit", encoding="utf-8")
    result = maintenance_queue(registry, config, now=anchor + timedelta(days=2))
    assert {i["code"] for i in result["items"]} == {"source_changed", "review_due"}
    (config.root_path / "note.md").unlink()
    assert "scope_without_evidence" in {
        i["code"] for i in maintenance_queue(registry, config)["items"]
    }


def test_verified_review_and_retirement(tmp_path):
    registry, config, anchor = setup(
        tmp_path, {"review_interval_days": 7, "check_source_changes": False}
    )
    review_at = anchor + timedelta(days=6)
    event = preview_event(
        registry,
        config,
        "Reviewed",
        target="note.md",
        primary_context="p",
        context_ids=[],
        idempotency_key="review",
        state_change={
            "context_id": "p",
            "field": "reviewed_at",
            "value": review_at.isoformat(),
        },
    )
    commit_event(registry, config, event["event_id"])
    assert (
        maintenance_queue(registry, config, now=anchor + timedelta(days=8))["status"]
        == "current"
    )
    event = preview_event(
        registry,
        config,
        "Retired",
        target="note.md",
        primary_context="p",
        context_ids=[],
        idempotency_key="retire",
        state_change={"context_id": "p", "field": "lifecycle", "value": "retired"},
    )
    commit_event(registry, config, event["event_id"])
    assert maintenance_queue(registry, config, now=anchor + timedelta(days=30))[
        "retired_contexts"
    ] == ["p"]
