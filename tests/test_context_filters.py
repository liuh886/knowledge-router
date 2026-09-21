"""Context include/exclude/tag filters and graph-affinity brief ranking."""

# ruff: noqa: E402
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_brief import brief_context
from context_manager import discover_contexts, extract_tags
from context_store import load_context_version, register_contexts
from fts_indexer import scan_notes
from link_graph import build_link_graph, context_links, persist_link_graph
from registry import VaultRegistry


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Area" / "Sub").mkdir(parents=True)
    (root / "Notes").mkdir()
    (root / "Area" / "Alpha.md").write_text(
        "---\ntags: [ccs, research]\n---\n# Alpha\nalpha finding", encoding="utf-8"
    )
    (root / "Area" / "Beta.md").write_text(
        "---\ntags: [other]\n---\n# Beta\nalpha finding", encoding="utf-8"
    )
    (root / "Area" / "Sub" / "Gamma.md").write_text(
        "---\ntags: [ccs]\n---\n# Gamma\nalpha finding", encoding="utf-8"
    )
    (root / "Notes" / "Delta.md").write_text("# Delta\nnothing", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test")
    return registry, config, root


def _definitions(**overrides):
    entry = {
        "id": "research:area",
        "type": "research",
        "name": "Area",
        "purpose": "test context",
        "scopes": ["Area"],
    }
    entry.update(overrides)
    return {"schema_version": 1, "contexts": [entry]}


def _source_count(registry, config, definitions):
    scan = scan_notes(config.root_path)
    catalog = discover_contexts(scan, vault_id=config.id, definitions=definitions)
    row = next(c for c in catalog["contexts"] if c["id"] == "research:area")
    return row["source_count"]


def test_extract_tags_reads_inline_and_block_forms():
    assert extract_tags("---\ntags: [ccs, Research]\n---\nbody") == ("ccs", "research")
    assert extract_tags("---\ntags:\n  - ccs\n  - Research\n---\nbody") == (
        "ccs",
        "research",
    )
    assert extract_tags("---\ntag: [CCS/Person]\n---\nbody") == ("ccs/person",)
    assert extract_tags("---\ntags: - MonthlyLog\n---\nbody") == ("monthlylog",)
    assert extract_tags("---\ntags: ccs, Research\n---\nbody") == ("ccs", "research")
    assert extract_tags("no frontmatter") == ()


def test_exclude_filter_narrows_membership(vault):
    registry, config, _ = vault
    definitions = _definitions(exclude=["Area/Sub"])
    assert _source_count(registry, config, definitions) == 2
    register_contexts(registry, config, definitions)
    brief = brief_context(registry, config, "research:area", query="alpha")
    assert brief["source_count"] == 2
    assert all("Sub/" not in item["path"] for item in brief["evidence"])


def test_include_filter_narrows_membership(vault):
    registry, config, _ = vault
    assert _source_count(registry, config, _definitions(include=["Area/Sub"])) == 1


def test_tag_filter_requires_frontmatter_tag(vault):
    registry, config, _ = vault
    definitions = _definitions(tags=["ccs"])
    assert _source_count(registry, config, definitions) == 2
    register_contexts(registry, config, definitions)
    brief = brief_context(registry, config, "research:area", query="alpha")
    assert {item["path"] for item in brief["evidence"]} == {
        "Area/Alpha.md",
        "Area/Sub/Gamma.md",
    }


def test_registration_persists_filters_and_drift_uses_them(vault):
    registry, config, _ = vault
    definitions = _definitions(exclude=["Area/Sub"], tags=["ccs"])
    register_contexts(registry, config, definitions)
    saved = load_context_version(registry, config)["definitions"]["contexts"][0]
    assert saved["exclude"] == ["Area/Sub"]
    assert saved["tags"] == ["ccs"]
    assert saved["include"] == []


def test_filters_narrow_the_relationship_graph(vault):
    registry, config, _ = vault
    register_contexts(registry, config, _definitions(exclude=["Area/Sub"]))
    result = context_links(registry, config, "research:area", rebuild=True)
    assert result["scope_note_count"] == 2


def test_invalid_filters_are_rejected(vault):
    registry, config, _ = vault
    with pytest.raises(ValueError, match="include"):
        register_contexts(registry, config, _definitions(include=["../escape"]))
    with pytest.raises(ValueError, match="duplicates"):
        register_contexts(registry, config, _definitions(tags=["ccs", "CCS"]))


def test_graph_affinity_ranks_anchor_linked_evidence_first(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "MOC.md").write_text("# MOC\n[[Topic]]", encoding="utf-8")
    (root / "Topic.md").write_text("# Topic\nalpha", encoding="utf-8")
    (root / "Noise.md").write_text("# Noise\nalpha", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="affinity")
    definitions = {
        "schema_version": 1,
        "contexts": [
            {
                "id": "research:root",
                "type": "research",
                "name": "Root",
                "purpose": "test",
                "scopes": ["."],
            }
        ],
    }
    register_contexts(registry, config, definitions)
    persist_link_graph(
        registry,
        config,
        build_link_graph(scan_notes(config.root_path), vault_root=config.root_path),
    )
    brief = brief_context(registry, config, "research:root", query="alpha")
    assert brief["graph_affinity_used"] is True
    assert brief["evidence"][0]["path"] == "Topic.md"
