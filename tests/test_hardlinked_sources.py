"""A hardlinked source must not turn a committed capture into an indexing failure."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from capture_broker import create_capture_plan, load_capture_plan
from fts_indexer import scan_notes
from registry import VaultRegistry
from route_engine import RoutingResult
from secure_io import SecureParent, secure_read_bytes
from vault_service import search_vault, sync_vault


@pytest.fixture
def linked_vault(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "Inbox").mkdir(parents=True)
    source = vault / "AGENTS.md"
    source.write_bytes(b"# Linked source\nhardlinkedreference evidence\n")
    snapshot = tmp_path / "snapshot.md"
    os.link(source, snapshot)
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(vault, name="test", write_policy="append-create")
    return registry, config, source, snapshot


def test_committed_capture_finishes_indexing_and_replays_once(linked_vault):
    registry, config, source, snapshot = linked_vault
    original = source.read_bytes()
    state = registry.vault_state_dir(config)
    route = RoutingResult("memo", "create_new", "Inbox", "saved.md", "Inbox/saved.md", 1.0, "test")
    plan = create_capture_plan(config.root_path, state, "capturedrecordmarker", route)
    command = [
        sys.executable,
        str(SCRIPTS / "knowledge_router.py"),
        "--state-root",
        str(registry.state_root),
        "capture",
        "--commit",
        "--plan-id",
        plan.plan_id,
        "--vault",
        "test",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["mutation"]["status"] == "committed"
    saved = (config.root_path / "Inbox/saved.md").read_bytes()
    replay = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["mutation"]["status"] == "idempotent_replay"
    assert (config.root_path / "Inbox/saved.md").read_bytes() == saved
    hits = search_vault(registry, registry.resolve(config.id), "hardlinkedreference", limit=5)
    assert hits and hits[0]["path"] == "AGENTS.md" and not hits[0]["stale"]
    assert source.read_bytes() == snapshot.read_bytes() == original


def test_source_read_permission_does_not_authorize_mutation_or_control_aliases(linked_vault):
    registry, config, source, snapshot = linked_vault
    original = source.read_bytes()
    with pytest.raises(ValueError, match="Hard-linked"):
        secure_read_bytes(config.root_path, "AGENTS.md")
    route = RoutingResult("memo", "append_existing", ".", "AGENTS.md", "AGENTS.md", 1.0, "test")
    with pytest.raises(ValueError, match="Hard-linked"):
        create_capture_plan(
            config.root_path, registry.vault_state_dir(config), "must refuse", route
        )
    with SecureParent(config.root_path, "AGENTS.md") as guard:
        with pytest.raises(ValueError):
            guard.open_existing(writable=True, allow_hardlinks=True)
    route = RoutingResult("memo", "create_new", "Inbox", "safe.md", "Inbox/safe.md", 1.0, "test")
    plan = create_capture_plan(
        config.root_path, registry.vault_state_dir(config), "preview only", route
    )
    plan_file = registry.vault_state_dir(config) / f"plan.{plan.plan_id}.json"
    os.link(plan_file, snapshot.with_name("linked-plan.json"))
    with pytest.raises(ValueError, match="Hard-linked"):
        load_capture_plan(registry.vault_state_dir(config), plan.plan_id)
    assert source.read_bytes() == snapshot.read_bytes() == original


def test_hardlinked_notes_are_included_and_freshness_is_still_checked(linked_vault):
    registry, config, source, snapshot = linked_vault
    scan = scan_notes(config.root_path)
    assert [note.path for note in scan.records] == ["AGENTS.md"]
    assert scan.records[0].checksum == hashlib.sha256(source.read_bytes()).hexdigest()
    assert not scan.skipped_decode and not scan.skipped_io
    sync_vault(registry, config)
    source.write_bytes(source.read_bytes() + b"external edit\n")
    hits = search_vault(registry, registry.resolve(config.id), "hardlinkedreference", limit=5)
    assert hits and hits[0]["stale"]
