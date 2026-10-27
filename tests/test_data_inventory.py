"""Foundation inventory preserves uncertainty and never needs a Context or writes sources."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import data_inventory
from data_inventory import audit_inventory, build_inventory, inspect_note, simple_scalar
from fts_indexer import NoteRecord, ScanReport


def scan(notes, *, skipped_io=()):
    return ScanReport(tuple(NoteRecord(
        ulid="index-id", title=path, content=text, path=path,
        modified_at="2026-10-25T00:00:00+00:00", mtime_ns=0,
        size=len(text.encode()), checksum=hashlib.sha256(text.encode()).hexdigest(),
    ) for path, text in notes.items()), (), skipped_io)


def test_domain_id_namespaces_and_backup_versions_remain_candidates():
    report = build_inventory(scan({
        "Objects/a.md": '---\nschema_version: "0.1"\ntype: object\nid: same\n---\n',
        "Backup/a.md": '---\nschema_version: "0.1"\ntype: object\nid: same\n---\n',
        "Trips/a.md": '---\nschema_version: "0.1"\ntype: trip\nid: same\n---\n',
    }))
    group = report["identifier_candidates"]["items"][0]
    assert report["identifier_candidates"]["total"] == 1
    assert group["namespace"] == ["id", "0.1", "object"]
    assert group["occurrences"] == 2
    assert group["sources"][1]["historical_path_hint"]
    assert report["counts_by_role_hint"] == {"structured_record_candidate": 3}
    assert "empty_note" not in json.dumps(report)


def test_duplicate_fields_do_not_silently_select_identity():
    report = build_inventory(scan({
        "one.md": "---\nulid: A\nulid: B\n---\ntext",
        "two.md": "---\nulid: A\n---\ntext",
    }))
    assert report["identifier_candidates"]["total"] == 0
    assert report["duplicate_top_level_fields"]["items"][0]["lines"] == [2, 3]


def test_unsupported_yaml_is_not_certified_or_treated_as_identity():
    assert simple_scalar("[a, b]") == ("unparsed", None)
    assert simple_scalar("'A''B'") == ("scalar", "A'B")
    assert simple_scalar('"A#B"') == ("scalar", "A#B")
    assert simple_scalar("A # annotation") == ("scalar", "A")
    report = build_inventory(scan({"one.md": "---\nid: [a, b]\n---\n"}))
    assert report["unparsed_identity_fields"]["total"] == 1
    assert report["identifier_candidates"]["total"] == 0


def test_frontmatter_only_and_body_text_do_not_create_fake_fields():
    note = inspect_note("one.md", "# Notes\nid: A\ncreated: yesterday\n", "hash")
    assert note["fields"] == {}
    note = inspect_note("one.md", "---\nid: A\n---\nid: B\n", "hash")
    assert note["fields"]["id"] == [{"line": 2, "state": "scalar", "value": "A"}]


def test_zero_limit_keeps_totals_and_nested_source_lists_are_bounded():
    source = scan({f"{i}.md": "---\nulid: A\n---\n" for i in range(5)})
    hidden = build_inventory(source, limit=0)
    assert hidden["identifier_candidates"] == {"total": 1, "items": [], "omitted": 1}
    visible = build_inventory(source, limit=1)["identifier_candidates"]["items"][0]
    assert visible["occurrences"] == 5
    assert len(visible["sources"]) == 1 and visible["sources_omitted"] == 4


def test_payload_count_is_observation_not_deletion_verdict():
    report = build_inventory(scan({"one.md": "![](data:image/png;base64,AAAA==)"}))
    assert report["embedded_image_payloads"]["total"] == 1
    assert report["embedded_image_payloads"]["items"][0]["count"] == 1
    assert report["writes_performed"] == 0


def test_changed_or_incomplete_verification_never_reports_complete(monkeypatch):
    first = scan({"one.md": "first"})
    for second in (scan({"one.md": "other"}), scan({"one.md": "first"}, skipped_io=("bad.md",))):
        scans = iter((first, second))
        monkeypatch.setattr(data_inventory, "scan_notes", lambda root: next(scans))
        report = audit_inventory(Path("unused"))
        assert report["status"] == "incomplete" and not report["complete"]


def test_cli_reads_without_creating_context_state_or_source_files(tmp_path):
    root = tmp_path / "Vault"
    root.mkdir()
    source = root / "memo.2026.md"
    source.write_bytes(b"---\ntype: capture\n---\nOriginal text\n")
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    script = Path(data_inventory.__file__)
    result = subprocess.run(
        [sys.executable, str(script), "--vault-root", str(root), "--limit", "1"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    report = json.loads(result.stdout)
    assert report["complete"] and report["source_set_stable"]
    assert report["counts_by_role_hint"] == {"raw_capture_candidate": 1}
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes()
                      for p in tmp_path.rglob("*") if p.is_file()}


def test_cli_nonexistent_root_returns_error_without_creating_it(tmp_path):
    missing = tmp_path / "missing"
    result = subprocess.run(
        [sys.executable, data_inventory.__file__, "--vault-root", str(missing)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 2 and not missing.exists()
    assert json.loads(result.stdout)["writes_performed"] == 0


def test_graph_cache_built_with_old_resolution_rules_is_not_reused(tmp_path):
    from link_graph import build_link_graph, load_link_graph, persist_link_graph
    from registry import VaultRegistry

    root = tmp_path / "Vault"
    root.mkdir()
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(root, name="test")
    graph = build_link_graph(scan({"One.md": "one"}), vault_root=root)
    persist_link_graph(registry, config, graph)
    assert load_link_graph(registry, config)["resolution_version"] == graph["resolution_version"]
    legacy = {k: v for k, v in graph.items() if k != "resolution_version"}
    persist_link_graph(registry, config, legacy)
    assert load_link_graph(registry, config) is None
    persist_link_graph(registry, config, {**graph, "resolution_version": -1})
    assert load_link_graph(registry, config) is None
