"""Cross-Vault Context identity stays explicit and source-partitioned."""

# ruff: noqa: E402
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from context_brief import brief_context
from context_store import register_contexts
from registry import VaultRegistry
from vault_service import sync_vault


def _register(registry: VaultRegistry, root: Path, name: str, text: str):
    root.mkdir()
    (root / "project.md").write_text(text, encoding="utf-8")
    config = registry.attach(root, name=name, write_policy="off")
    sync_vault(registry, config)
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": "project:main",
                    "type": "project",
                    "name": "Project",
                    "purpose": "same name, independent source",
                    "scopes": ["."],
                }
            ],
        },
    )
    return config


def test_same_named_contexts_never_join_across_vaults(tmp_path: Path):
    registry = VaultRegistry(tmp_path / "state")
    first = _register(registry, tmp_path / "first", "first", "First vault decision.")
    second = _register(registry, tmp_path / "second", "second", "Second vault decision.")

    first_brief = brief_context(registry, first, "Project", query="First vault")
    second_brief = brief_context(registry, second, "Project", query="Second vault")
    assert first.id != second.id
    assert first_brief["vault_id"] == first.id
    assert second_brief["vault_id"] == second.id
    assert {item["vault_id"] for item in first_brief["evidence"]} == {first.id}
    assert {item["vault_id"] for item in second_brief["evidence"]} == {second.id}
    assert "Second vault" not in "\n".join(item["excerpt"] for item in first_brief["evidence"])
    assert "First vault" not in "\n".join(item["excerpt"] for item in second_brief["evidence"])
