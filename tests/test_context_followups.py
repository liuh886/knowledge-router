"""Follow-up dates, completion, conflicts and canonical source preservation."""

# ruff: noqa: E402
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_events import commit_event, preview_event
from context_followups import followup_change, list_followups
from context_state import normalize_change
from context_store import register_contexts
from registry import VaultRegistry


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "notes.md").write_text("# Original\n", encoding="utf-8")
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
                    "type": "project",
                    "name": "Project",
                    "purpose": "test",
                    "scopes": ["."],
                }
            ],
        },
    )
    return registry, config


def record(setup, key, status="open", replaces=None):
    registry, config = setup
    change = followup_change(
        "p",
        followup_id="venue",
        title="Confirm venue",
        due_at="2026-09-09T09:00:00+08:00",
        status=status,
        replaces=replaces,
    )
    event = preview_event(
        registry,
        config,
        "Venue follow-up",
        target="notes.md",
        primary_context="p",
        context_ids=[],
        idempotency_key=key,
        state_change=change,
    )
    commit_event(registry, config, event["event_id"])
    return event["event_id"]


def test_timezone_boundary_and_completion(setup):
    registry, config = setup
    event = record(setup, "one")
    before = (config.root_path / "notes.md").read_bytes()
    assert (
        list_followups(
            registry,
            config,
            "p",
            due_only=True,
            now=datetime.fromisoformat("2026-09-09T00:59:59+00:00"),
        )["item_count"]
        == 0
    )
    due = list_followups(
        registry,
        config,
        "p",
        due_only=True,
        now=datetime.fromisoformat("2026-09-09T01:00:00+00:00"),
    )
    assert due["items"][0]["due"]
    assert due["notifications_sent"] == 0
    record(setup, "done", "done", [event])
    assert list_followups(registry, config, "p", due_only=True)["item_count"] == 0
    assert list_followups(registry, config, "p")["items"][0]["status"] == "done"
    assert (config.root_path / "notes.md").read_bytes().startswith(before)


def test_cancellation_and_conflicting_updates(setup):
    registry, config = setup
    first = record(setup, "one")
    record(setup, "two", "cancelled", [first])
    assert list_followups(registry, config, "p")["items"][0]["status"] == "cancelled"
    record(setup, "conflict")
    assert (
        list_followups(registry, config, "p", due_only=True)["items"][0]["status"]
        == "needs_review"
    )


@pytest.mark.parametrize(
    "date", ["2026-09-09", "2026-09-09T09:00:00", "next Tuesday", "invalid"]
)
def test_ambiguous_dates_rejected(date):
    with pytest.raises(ValueError):
        followup_change("p", followup_id="venue", title="Check", due_at=date)


def test_generic_state_path_cannot_bypass_followup_validation():
    with pytest.raises(ValueError):
        normalize_change(
            {"context_id": "p", "field": "followup_venue", "value": "anything"}, ["p"]
        )


def test_missing_original_is_not_treated_as_completed(setup):
    registry, config = setup
    record(setup, "one")
    (config.root_path / "notes.md").unlink()
    item = list_followups(registry, config, "p")["items"][0]
    assert item["status"] == "needs_review"
    assert item["reason"] == "source_unavailable"


@pytest.mark.parametrize(
    "due,zone", [("2026-09-09T09:00:00+08:00", None), ("明天上午9点", "Asia/Shanghai")]
)
def test_cli_followup_capture_and_list(setup, due, zone):
    registry, config = setup
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    base = [
        sys.executable,
        str(cli),
        "--state-root",
        str(registry.state_root),
        "contexts",
    ]

    def run(args):
        result = subprocess.run(
            [*base, *args, "--vault", config.id, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    event = run(
        [
            "capture",
            "Confirm venue",
            "--target",
            "notes.md",
            "--primary-context",
            "p",
            "--idempotency-key",
            "cli-followup",
            "--followup-id",
            "venue",
            "--followup-title",
            "Confirm venue",
            "--due-at",
            due,
            *(["--timezone", zone] if zone else []),
        ]
    )
    run(["commit", event["event_id"]])
    listed = run(["followups", "p"])
    assert listed["items"][0]["due_at"] == event["due_resolution"]["due_at"]
    assert event["due_resolution"]["local_time"].endswith("T09:00:00+08:00")
    assert listed["items"][0]["status"] == "open"
