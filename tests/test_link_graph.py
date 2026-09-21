"""Read-only relationship graph: resolution, Context neighbourhoods and CLI."""

# ruff: noqa: E402
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_store import register_contexts
from fts_indexer import scan_notes
from link_graph import build_link_graph, context_links, format_context_links
from registry import VaultRegistry

DEFINITIONS = {
    "schema_version": 1,
    "contexts": [
        {
            "id": "research:ccs",
            "type": "research",
            "name": "CCS",
            "purpose": "Carbon capture research",
            "scopes": ["Area"],
            "aliases": ["CCS"],
        },
        {
            "id": "contact:alice",
            "type": "contact",
            "name": "Alice",
            "purpose": "Conversations",
            "scopes": ["Contacts"],
        },
        {
            "id": "topic:main",
            "type": "topic",
            "name": "Topics",
            "purpose": "Topic anchors",
            "scopes": ["Topics"],
        },
    ],
}


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Contacts").mkdir()
    (root / "Topics").mkdir()
    (root / "Area" / "Carbon Capture.md").write_text(
        "# Carbon Capture\n[[Seismic]] [[Alice]] [[Missing Note]]", encoding="utf-8"
    )
    (root / "Area" / "Seismic.md").write_text("# Seismic\nnotes", encoding="utf-8")
    (root / "Area" / "Isolated.md").write_text("# Isolated\nno links", encoding="utf-8")
    (root / "Contacts" / "Alice.md").write_text("# Alice\n[[Seismic]]", encoding="utf-8")
    (root / "Topics" / "CCS_MOC.md").write_text(
        "# CCS MOC\n[[Carbon Capture]] [[Seismic]]", encoding="utf-8"
    )
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test")
    return registry, config, root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_build_graph_resolves_links_and_detects_mocs(vault):
    _, config, _ = vault
    graph = build_link_graph(scan_notes(config.root_path))
    edges = {(source, target) for source, target in graph["edges"]}
    assert ("Area/Carbon Capture.md", "Area/Seismic.md") in edges
    assert ("Contacts/Alice.md", "Area/Seismic.md") in edges
    assert graph["mocs"] == ["Topics/CCS_MOC.md"]
    assert graph["unresolved_links"] == 1
    assert graph["degrees"]["Area/Seismic.md"] == [3, 0]
    assert graph["degrees"]["Area/Isolated.md"] == [0, 0]
    assert "Area/Isolated.md" in graph["degrees"]


def test_context_links_reports_anchors_related_and_bridges(vault):
    registry, config, _ = vault
    register_contexts(registry, config, DEFINITIONS)
    result = context_links(registry, config, "research:ccs")
    assert result["status"] == "linked"
    assert result["scope_note_count"] == 3
    assert result["internal_edge_count"] == 1
    anchors = {row["path"]: row for row in result["anchors"]}
    assert anchors["Topics/CCS_MOC.md"]["in_scope_targets"] == 2
    related = {row["context_id"]: row for row in result["related_contexts"]}
    assert related["contact:alice"]["cross_links"] == 2
    assert related["topic:main"]["cross_links"] == 2
    hubs = {row["path"]: row for row in result["hub_notes"]}
    assert hubs["Area/Seismic.md"]["in_degree"] == 3
    assert hubs["Area/Carbon Capture.md"]["out_degree"] == 2
    bridge = {row["path"]: row for row in result["bridge_notes"]}
    assert bridge["Area/Carbon Capture.md"]["outbound_cross_links"] == 1
    assert result["vault_writes"] == 0


def test_context_links_uses_alias_and_refuses_unknown(vault):
    registry, config, _ = vault
    register_contexts(registry, config, DEFINITIONS)
    assert context_links(registry, config, "CCS")["context"]["id"] == "research:ccs"
    with pytest.raises(KeyError, match="Unknown Context"):
        context_links(registry, config, "nope")


def test_context_links_unregistered_is_safe(vault):
    registry, config, _ = vault
    result = context_links(registry, config, "research:ccs")
    assert result["status"] == "unregistered"
    assert format_context_links(result).startswith("No registered Context")


def test_context_links_rebuilds_external_graph_without_vault_writes(vault):
    registry, config, root = vault
    register_contexts(registry, config, DEFINITIONS)
    before = _snapshot(root)
    result = context_links(registry, config, "research:ccs", rebuild=True)
    assert result["graph"]["moc_count"] == 1
    assert (registry.vault_state_dir(config) / "link_graph.json").is_file()
    assert _snapshot(root) == before


def test_context_links_rejects_out_of_range_limit(vault):
    registry, config, _ = vault
    register_contexts(registry, config, DEFINITIONS)
    with pytest.raises(ValueError, match="limit"):
        context_links(registry, config, "research:ccs", limit=0)


def test_asset_links_resolve_without_becoming_edges(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n![[diagram.png]] [[Other]]", encoding="utf-8")
    (root / "Other.md").write_text("# Other", encoding="utf-8")
    (root / "diagram.png").write_bytes(b"x")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="assets")
    graph = build_link_graph(scan_notes(config.root_path), vault_root=config.root_path)
    assert graph["asset_links"] == 1
    assert graph["unresolved_links"] == 0
    assert {(source, target) for source, target in graph["edges"]} == {
        ("note.md", "Other.md")
    }


def test_cli_links_returns_cited_neighbourhood(vault):
    registry, config, _ = vault
    register_contexts(registry, config, DEFINITIONS)
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    run = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--state-root",
            str(registry.state_root),
            "contexts",
            "links",
            "research:ccs",
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
    assert result["status"] == "linked"
    assert result["context"]["id"] == "research:ccs"
    assert result["related_context_count"] == 2
