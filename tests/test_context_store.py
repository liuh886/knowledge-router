"""Persistent definitions, revision conflicts, drift and CLI reuse acceptance."""

# ruff: noqa: E402
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_store import (
    _digest,
    check_contexts,
    compact_context_store,
    load_context_version,
    register_contexts,
)
from registry import VaultRegistry
from vault_service import inspect_contexts


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "person.md").write_text("# Human note\nOriginal", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test")
    definitions = {
        "schema_version": 1,
        "contexts": [
            {
                "id": "contact:one",
                "type": "contact",
                "name": "One",
                "purpose": "Track conversations",
                "scopes": ["person.md"],
                "aliases": ["Alexey"],
            }
        ],
    }
    return registry, config, definitions


def test_persist_reuse_conflict_and_history(setup):
    registry, config, definitions = setup
    before = (config.root_path / "person.md").read_bytes()
    assert register_contexts(registry, config, definitions)["revision"] == 1
    result = inspect_contexts(config, registry=registry, text="Alexey called")
    assert result["definition_revision"] == 1
    assert result["impacts"][0]["context_id"] == "contact:one"
    with pytest.raises(ValueError, match="conflict"):
        register_contexts(registry, config, definitions)
    definitions["contexts"][0]["purpose"] = "Updated purpose"
    assert (
        register_contexts(registry, config, definitions, expected_revision=1)[
            "revision"
        ]
        == 2
    )
    store = json.loads(
        (registry.vault_state_dir(config) / "context_definitions.json").read_text()
    )
    assert (
        store["versions"][0]["definitions"]["contexts"][0]["purpose"]
        == "Track conversations"
    )
    assert (config.root_path / "person.md").read_bytes() == before


def test_drift_does_not_rebase_or_guess_new_path(setup):
    registry, config, definitions = setup
    register_contexts(registry, config, definitions)
    assert check_contexts(registry, config)["status"] == "current"
    note = config.root_path / "person.md"
    note.write_text("# Changed", encoding="utf-8")
    assert check_contexts(registry, config)["findings"][0]["changed_count"] == 1
    note.rename(config.root_path / "renamed.md")
    result = check_contexts(registry, config)
    assert result["findings"][0]["code"] == "scope_without_evidence"
    assert result["baseline_updated"] is False
    assert load_context_version(registry, config)["revision"] == 1


def test_tampered_store_rejected(setup):
    registry, config, definitions = setup
    register_contexts(registry, config, definitions)
    path = registry.vault_state_dir(config) / "context_definitions.json"
    data = json.loads(path.read_text())
    data["vault_id"] = "another-vault"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="mismatch"):
        load_context_version(registry, config)


def test_invalid_registration_does_not_replace_previous_version(setup):
    registry, config, definitions = setup
    register_contexts(registry, config, definitions)
    definitions["contexts"][0]["scopes"] = ["missing"]
    with pytest.raises(ValueError, match="without readable"):
        register_contexts(registry, config, definitions, expected_revision=1)
    assert load_context_version(registry, config)["revision"] == 1


def test_second_registration_compacts_historical_bindings(setup):
    """Only the latest revision keeps its binding snapshot, and drift still works."""
    registry, config, definitions = setup
    register_contexts(registry, config, definitions)
    store_path = registry.vault_state_dir(config) / "context_definitions.json"
    first_only = json.loads(store_path.read_text())
    assert "bindings" in first_only["versions"][0]

    definitions["contexts"][0]["purpose"] = "Second"
    register_contexts(registry, config, definitions, expected_revision=1)
    store = json.loads(store_path.read_text())
    assert len(store["versions"]) == 2
    assert "bindings" not in store["versions"][0]
    assert store["versions"][0]["bindings_compacted"] is True
    assert "bindings" in store["versions"][1]
    # The historical declaration is still recorded even though its snapshot is gone.
    assert store["versions"][0]["definitions"]["contexts"][0]["purpose"] == "Track conversations"
    # Drift detection still reads the surviving snapshot.
    assert check_contexts(registry, config)["status"] == "current"


def test_compact_command_is_idempotent_and_reports_savings(setup):
    registry, config, definitions = setup
    register_contexts(registry, config, definitions)
    definitions["contexts"][0]["purpose"] = "Second"
    register_contexts(registry, config, definitions, expected_revision=1)
    # Re-add a legacy-shaped snapshot to prove the command still cleans it up.
    store_path = registry.vault_state_dir(config) / "context_definitions.json"
    store = json.loads(store_path.read_text())
    store["versions"][0]["bindings"] = {"contact:one": {"person.md": {"person.md": "a" * 64}}}
    store["versions"][0].pop("bindings_compacted", None)
    store.pop("checksum", None)
    # Match the production writer's formatting so the sizes are comparable.
    store_path.write_text(
        json.dumps({**store, "checksum": _digest(store)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = compact_context_store(registry, config)
    assert result["status"] == "compacted"
    assert result["revisions_compacted"] == 1
    assert result["bytes_saved"] > 0
    assert "bindings" not in json.loads(store_path.read_text())["versions"][0]

    again = compact_context_store(registry, config)
    assert again["status"] == "already_compact"
    assert again["revisions_compacted"] == 0
    assert load_context_version(registry, config)["revision"] == 2


def test_cli_registration_survives_process_restart(setup, tmp_path):
    registry, config, definitions = setup
    path = tmp_path / "definitions.json"
    path.write_text(json.dumps(definitions), encoding="utf-8")
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    base = [
        sys.executable,
        str(cli),
        "--state-root",
        str(registry.state_root),
        "contexts",
    ]
    for args in (
        ["register", "--definitions", str(path)],
        ["plan", "Alexey called"],
        ["check"],
    ):
        run = subprocess.run(
            [*base, *args, "--vault", config.id, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert run.returncode == 0, run.stderr
        result = json.loads(run.stdout)
        if args[0] == "plan":
            assert result["definition_origin"] == "registered"
            assert result["impact_count"] == 1
