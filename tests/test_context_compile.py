"""Compiled Context cards: read-only bundles and externally staged drafts."""

# ruff: noqa: E402
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from capture_broker import commit_capture_plan
from context_compile import compile_context, format_compile_bundle, stage_compiled_card
from context_store import register_contexts
from fts_indexer import scan_notes
from link_graph import build_link_graph, persist_link_graph
from registry import VaultRegistry

DEFINITIONS = {
    "schema_version": 1,
    "contexts": [
        {
            "id": "research:area",
            "type": "research",
            "name": "Area",
            "purpose": "Carbon capture work",
            "scopes": ["Area"],
            "aliases": ["Area"],
        }
    ],
}

CARD = "# Area\n\n## 当前状态\n- 进展中\n"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Area" / "_compiled").mkdir(parents=True)
    (root / "Area" / "MOC.md").write_text("# MOC\n[[Topic]]", encoding="utf-8")
    (root / "Area" / "Topic.md").write_text(
        "# Topic\n\n## 进展\nprogress alpha", encoding="utf-8"
    )
    (root / "Area" / "Other.md").write_text("# Other\nmisc", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    register_contexts(registry, config, DEFINITIONS)
    persist_link_graph(
        registry,
        config,
        build_link_graph(scan_notes(config.root_path), vault_root=config.root_path),
    )
    return registry, config, root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_compile_bundle_is_read_only_and_cited(vault):
    registry, config, root = vault
    before = _snapshot(root)
    bundle = compile_context(registry, config, "research:area", limit=5)
    assert bundle["status"] == "compiled_bundle"
    assert bundle["writes_performed"] == 0
    assert bundle["suggested_target"] == "Area/_compiled/Area.md"
    assert bundle["suggested_target_directory_exists"] is True
    assert bundle["existing_card"] is False
    assert "## 来源（ground truth）" in bundle["template"]
    assert "Area/Topic.md:" in bundle["template"]
    assert _snapshot(root) == before


def test_compile_bundle_reports_topic_anchors(vault):
    registry, config, _ = vault
    result = compile_context(registry, config, "research:area", limit=5)
    paths = {item["path"] for item in result["anchors"]}
    assert "Area/MOC.md" in paths
    assert "[[MOC]]" in result["template"]


def test_stage_writes_external_plan_only(vault):
    registry, config, root = vault
    before = _snapshot(root)
    staged = stage_compiled_card(
        registry, config, "research:area", CARD, target="Area/_compiled/Area.md"
    )
    assert staged["status"] == "staged"
    assert staged["vault_writes"] == 0
    assert staged["action"] == "create_new"
    plan_file = registry.vault_state_dir(config) / f"plan.{staged['plan_id']}.json"
    assert plan_file.is_file()
    assert _snapshot(root) == before


def test_commit_creates_only_the_compiled_card(vault):
    registry, config, root = vault
    before = _snapshot(root)
    staged = stage_compiled_card(
        registry, config, "research:area", CARD, target="Area/_compiled/Area.md"
    )
    commit_capture_plan(
        config.root_path, registry.vault_state_dir(config), staged["plan_id"]
    )
    after = _snapshot(root)
    added = set(after) - set(before)
    assert added == {"Area/_compiled/Area.md"}
    assert CARD.strip().encode("utf-8") in after["Area/_compiled/Area.md"]
    assert compile_context(registry, config, "research:area")["existing_card"] is True


def test_stage_requires_an_existing_directory(vault):
    registry, config, root = vault
    with pytest.raises(FileNotFoundError):
        stage_compiled_card(
            registry, config, "research:area", CARD, target="Area/Missing/Card.md"
        )
    assert not list(registry.vault_state_dir(config).glob("plan.*.json"))


def test_stage_rejects_empty_content(vault):
    registry, config, _ = vault
    with pytest.raises(ValueError, match="empty"):
        stage_compiled_card(registry, config, "research:area", "   ")


def test_cli_compile_bundle(vault):
    registry, config, _ = vault
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    run = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--state-root",
            str(registry.state_root),
            "contexts",
            "compile",
            "research:area",
            "--vault",
            config.id,
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "compiled_bundle"
    assert result["context"]["id"] == "research:area"
    assert "template" in result
    assert format_compile_bundle(result).startswith("# 编译包")
