"""One event, multiple Context links, source protection and interrupted-run recovery."""

# ruff: noqa: E402
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import context_events
import vault_service
from context_events import commit_event, context_history, inspect_event, preview_event
from context_store import register_contexts
from registry import VaultRegistry
from vault_service import search_vault


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "vault"
    for directory in ("Daily", "Projects", "Contacts"):
        (root / directory).mkdir(parents=True)
        (root / directory / "source.md").write_text("# Human original\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": kind,
                    "type": kind,
                    "name": directory,
                    "purpose": directory,
                    "scopes": [directory],
                    "kind": "collection",
                }
                for kind, directory in (
                    ("daily", "Daily"),
                    ("project", "Projects"),
                    ("contact", "Contacts"),
                )
            ],
        },
    )
    return registry, config


def preview(setup, **changes):
    registry, config = setup
    args = {
        "target": "Daily/event.md",
        "primary_context": "daily",
        "context_ids": ["contact", "project"],
        "idempotency_key": "test-event",
    }
    args.update(changes)
    return preview_event(registry, config, "Cobalt telescope milestone confirmed", **args)


def test_single_capture_shared_links_and_replay(setup):
    registry, config = setup
    before = {p: p.read_bytes() for p in config.root_path.rglob("*.md")}
    event = preview(setup)
    assert not (config.root_path / "Daily/event.md").exists()
    assert preview(setup)["plan_id"] == event["plan_id"]
    done = commit_event(registry, config, event["event_id"])
    assert done["status"] == "completed"
    assert len(done["context_links"]) == 3
    assert context_history(registry, config, "contact")["event_count"] == 1
    assert len(list(config.root_path.rglob("*.md"))) == 4
    payload = (config.root_path / "Daily/event.md").read_bytes()
    commit_event(registry, config, event["event_id"])
    assert (config.root_path / "Daily/event.md").read_bytes() == payload
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert search_vault(registry, registry.resolve(config.id), "Cobalt telescope", limit=5)


def test_index_failure_is_resumable_without_double_write(setup, monkeypatch):
    registry, config = setup
    event = preview(setup)
    original = vault_service.sync_vault

    def fail(*args, **kwargs):
        raise RuntimeError("simulated projection failure")

    monkeypatch.setattr(vault_service, "sync_vault", fail)
    pending = commit_event(registry, config, event["event_id"])
    assert pending["status"] == "applied_pending_index"
    assert "projection failure" in pending["index_error"]
    assert inspect_event(registry, config, event["event_id"])["status"] == "applied_pending_index"
    payload = (config.root_path / "Daily/event.md").read_bytes()
    monkeypatch.setattr(vault_service, "sync_vault", original)
    assert commit_event(registry, config, event["event_id"])["status"] == "completed"
    assert (config.root_path / "Daily/event.md").read_bytes() == payload


def test_changed_append_target_preserved(setup):
    registry, config = setup
    event = preview(setup, target="Daily/source.md")
    note = config.root_path / "Daily/source.md"
    note.write_text("Human edited while preview open", encoding="utf-8")
    with pytest.raises((RuntimeError, PermissionError)):
        commit_event(registry, config, event["event_id"])
    assert note.read_text() == "Human edited while preview open"


def test_readonly_policy_and_scope_rejection(setup):
    registry, config = setup
    with pytest.raises(ValueError, match="primary Context"):
        preview(setup, target="Contacts/new.md")
    event = preview(setup)
    registry.attach(config.root_path, name="test", write_policy="off")
    with pytest.raises(PermissionError, match="read-only"):
        commit_event(registry, config, event["event_id"])
    assert not (config.root_path / "Daily/event.md").exists()


def test_same_key_different_contexts_rejected(setup):
    preview(setup)
    with pytest.raises(ValueError, match="different request"):
        preview(setup, context_ids=[])


def test_crash_after_broker_before_receipt_recovers(setup, monkeypatch):
    registry, config = setup
    event = preview(setup)
    publish = context_events._publish

    def fail_receipt(registry, config, event_id, stage, value):
        if stage == "applied":
            raise RuntimeError("receipt unavailable")
        return publish(registry, config, event_id, stage, value)

    monkeypatch.setattr(context_events, "_publish", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        commit_event(registry, config, event["event_id"])
    payload = (config.root_path / "Daily/event.md").read_bytes()
    assert context_history(registry, config, "contact")["event_count"] == 0
    monkeypatch.setattr(context_events, "_publish", publish)
    assert commit_event(registry, config, event["event_id"])["status"] == "completed"
    assert (config.root_path / "Daily/event.md").read_bytes() == payload
    assert context_history(registry, config, "contact")["event_count"] == 1


def test_cli_append_and_history_across_processes(setup):
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

    before = (config.root_path / "Daily/source.md").read_bytes()
    event = run(
        [
            "capture",
            "One durable event",
            "--target",
            "Daily/source.md",
            "--primary-context",
            "daily",
            "--context-id",
            "project",
            "--idempotency-key",
            "cli-test",
        ]
    )
    assert run(["commit", event["event_id"]])["status"] == "completed"
    assert run(["event", event["event_id"]])["status"] == "completed"
    assert run(["history", "project"])["event_count"] == 1
    after = (config.root_path / "Daily/source.md").read_bytes()
    assert after.startswith(before)
    run(["commit", event["event_id"]])
    assert (config.root_path / "Daily/source.md").read_bytes() == after
