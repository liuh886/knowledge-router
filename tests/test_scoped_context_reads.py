"""Scope-aware retrieval must stay live, complete and evidence-equivalent."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_brief
import context_manager
from context_brief import brief_context
from context_events import commit_event, preview_event
from context_store import load_context_version, register_contexts
from fts_indexer import scan_notes
from registry import VaultRegistry
from secure_io import SecureParent


@pytest.fixture
def scoped(tmp_path):
    root = tmp_path / "Vault"
    (root / "Area").mkdir(parents=True)
    (root / "Other").mkdir()
    (root / "Area/one.md").write_text(
        "---\ntags: [capture/idea]\n---\n# One\nCobalt evidence\n", encoding="utf-8"
    )
    (root / "Other/outside.md").write_text("# Outside\nCobalt outside\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(root, name="test", write_policy="append-create")
    register_contexts(registry, config, {"schema_version": 1, "contexts": [{
        "id": "topic:one", "type": "topic", "name": "Selected", "purpose": "test",
        "scopes": ["Area"], "tags": ["capture/idea"],
    }, {"id": "topic:all", "type": "topic", "name": "All", "purpose": "test",
        "scopes": ["."]}]})
    return registry, config


def test_scanner_reads_only_selected_content_and_deduplicates_overlap(scoped, monkeypatch):
    registry, config = scoped
    names = []
    original = SecureParent.read_bytes
    def read(self, **kwargs):
        names.append(kwargs["name"])
        return original(self, **kwargs)
    monkeypatch.setattr(SecureParent, "read_bytes", read)
    scan = scan_notes(config.root_path, scopes=["Area", "Area/one.md"])
    assert [n.path for n in scan.records] == ["Area/one.md"]
    assert names == ["one.md"]
    assert not scan.skipped_io and not scan.skipped_decode


def test_exact_registered_id_skips_discovery_and_matches_name_evidence(scoped, monkeypatch):
    registry, config = scoped
    name_result = brief_context(registry, config, "Selected", query="Cobalt")
    def no_discovery(*args, **kwargs):
        raise AssertionError("Exact registered id should not rediscover the Vault")
    monkeypatch.setattr(context_brief, "discover_contexts", no_discovery)
    exact = brief_context(registry, config, "topic:one", query="Cobalt")
    assert exact["evidence"] == name_result["evidence"]
    assert exact["context"] == name_result["context"]
    assert exact["source_generation_scope"] == "context_scopes_and_linked_events"
    assert name_result["source_generation_scope"] == "vault"


def test_live_add_move_delete_and_content_change_without_cache(scoped):
    registry, config = scoped
    original = brief_context(registry, config, "topic:one", query="Cobalt")
    outside = config.root_path / "Other/outside.md"
    outside.write_text("outside changed", encoding="utf-8")
    assert brief_context(registry, config, "topic:one")["source_generation"] == original["source_generation"]
    added = config.root_path / "Area/two.md"
    added.write_text("---\ntags: [capture/idea]\n---\nCobalt new\n", encoding="utf-8")
    added_result = brief_context(registry, config, "topic:one", query="Cobalt")
    assert {r["path"] for r in added_result["evidence"]} == {"Area/one.md", "Area/two.md"}
    assert added_result["source_generation"] != original["source_generation"]
    added.rename(config.root_path / "Other/moved.md")
    assert {r["path"] for r in brief_context(registry, config, "topic:one")["evidence"]} == {"Area/one.md"}
    (config.root_path / "Area/one.md").unlink()
    empty = brief_context(registry, config, "topic:one", query="Cobalt")
    assert empty["evidence_gap"] and empty["source_count"] == 0


def test_unreadable_in_scope_fails_but_unrelated_bad_encoding_is_not_read(scoped):
    registry, config = scoped
    (config.root_path / "Other/bad.md").write_bytes(b"\xff\xfeinvalid")
    assert brief_context(registry, config, "topic:one", query="Cobalt")["evidence"]
    (config.root_path / "Area/bad.md").write_bytes(b"\xff\xfeinvalid")
    with pytest.raises(ValueError, match="complete readable"):
        brief_context(registry, config, "topic:one")


def test_exact_id_reads_linked_event_outside_scope_and_rechecks_mutation(scoped, monkeypatch):
    registry, config = scoped
    event = preview_event(registry, config, "Amber linked", target="event.md",
                          primary_context="topic:all", context_ids=["topic:one"], idempotency_key="outside")
    commit_event(registry, config, event["event_id"])
    result = brief_context(registry, config, "topic:one", query="Amber")
    assert result["evidence"][0]["path"] == "event.md"
    monkeypatch.setattr(context_brief, "secure_read_bytes", lambda *args: b"changed")
    changed = brief_context(registry, config, "topic:one", query="Amber")
    assert changed["evidence_gap"] and changed["sources_changed_during_read"] == ["event.md"]


@pytest.mark.parametrize("scope", ["../Other", "/absolute", "C:/absolute", "Area\\one.md", ".hidden", ""])
def test_scoped_scan_rejects_unsafe_selectors(scoped, scope):
    registry, config = scoped
    with pytest.raises(ValueError):
        scan_notes(config.root_path, scopes=[scope])


def test_root_and_empty_scope_are_explicit(scoped):
    registry, config = scoped
    assert len(scan_notes(config.root_path, scopes=["."]).records) == 2
    assert scan_notes(config.root_path, scopes=[]).records == ()


def test_tag_parsing_is_once_per_note_in_discovery(scoped, monkeypatch):
    registry, config = scoped
    scan = scan_notes(config.root_path)
    original = context_manager.extract_tags
    calls = []
    def tags(text):
        calls.append(text)
        return original(text)
    monkeypatch.setattr(context_manager, "extract_tags", tags)
    context_manager.discover_contexts(scan, vault_id=config.id)
    assert len(calls) == len(scan.records)


def test_names_still_require_disambiguation_between_registered_contexts(scoped):
    registry, config = scoped
    version = load_context_version(registry, config)
    definitions = version["definitions"]
    next(row for row in definitions["contexts"] if row["id"] == "topic:all")["name"] = "Selected"
    register_contexts(registry, config, definitions, expected_revision=version["revision"])
    with pytest.raises(ValueError, match="ambiguous"):
        brief_context(registry, config, "Selected")
    assert brief_context(registry, config, "topic:one")["evidence"]
