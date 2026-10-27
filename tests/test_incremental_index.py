"""Verified immutable deltas must match a fresh projection and preserve activation."""
import hashlib
import json
import os
import sqlite3
import sys
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fts_indexer as indexer
import vault_service
from registry import VaultRegistry
from vault_service import search_vault, sync_vault


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "Vault"
    for relative, data in {
        "a.md": b"# Cobalt\nOld evidence\n",
        "Sub/b.md": "# 中文研究\n积雪观测 数据比较\n".encode(),
        "Sub/move.md": b"\xef\xbb\xbf# Move\r\nUnique movedmark\r\n",
        "remove.md": b"# Remove\nOld removemark\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(root, name="test")
    first = sync_vault(registry, config)
    return registry, registry.resolve(config.id), first


def logical_tables(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: sorted(db.execute(f"SELECT * FROM {table}").fetchall())
                for table in ("notes", "notes_fts", "note_bigrams", "bigram_stats")}


def assert_matches_fresh(root, result, target):
    scan = indexer.scan_notes(root)
    indexer.build_database(target, scan, generation=indexer._content_generation(scan),
                           vault_root=root, vault_id=indexer._vault_identity(root))
    assert logical_tables(result["index_path"]) == logical_tables(target)


def test_external_changes_with_unchanged_size_and_mtime_match_fresh_projection(indexed, tmp_path):
    registry, config, first = indexed
    base = Path(first["index_path"])
    original = hashlib.sha256(base.read_bytes()).hexdigest()
    note = config.root_path / "a.md"
    stamp = note.stat()
    note.write_bytes(note.read_bytes().replace(b"Cobalt", b"Copper"))
    os.utime(note, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    (config.root_path / "Sub/move.md").rename(config.root_path / "renamed.md")
    (config.root_path / "remove.md").unlink()
    (config.root_path / "new.md").write_text("# 新发现\nAdded ambermark\n", encoding="utf-8")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "incremental" and result["fallback_reason"] is None
    assert result["delta"] == {"added": 2, "updated": 1, "removed": 2,
                               "metadata_updated": 0, "unchanged": 1}
    assert hashlib.sha256(base.read_bytes()).hexdigest() == original
    assert_matches_fresh(config.root_path, result, tmp_path / "fresh.db")
    current = registry.resolve(config.id)
    assert search_vault(registry, current, "Copper", limit=5)
    assert not search_vault(registry, current, "Cobalt", limit=5)
    assert not search_vault(registry, current, "removemark", limit=5)
    assert search_vault(registry, current, "积雪观测", limit=5)


@pytest.mark.parametrize("damage", [
    "DELETE FROM notes_fts WHERE rowid IN (SELECT rowid FROM notes_fts LIMIT 1)",
    "UPDATE notes_fts SET content='wrong inherited text' WHERE rowid IN (SELECT rowid FROM notes_fts LIMIT 1)",
    "UPDATE notes SET content='wrong cached source' WHERE path='a.md'",
    "DELETE FROM note_bigrams WHERE gram IN (SELECT gram FROM note_bigrams LIMIT 1)",
    "UPDATE bigram_stats SET document_count=document_count+1",
    "UPDATE metadata SET value='foreign' WHERE key='vault_id'",
])
def test_damaged_predecessor_rebuilds_instead_of_inheriting_corruption(indexed, tmp_path, damage):
    registry, config, first = indexed
    with closing(sqlite3.connect(first["index_path"])) as db:
        db.execute(damage)
        db.commit()
    (config.root_path / "new.md").write_text("# New\nNew source\n", encoding="utf-8")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "full" and result["fallback_reason"]
    assert result["delta"] is None
    assert_matches_fresh(config.root_path, result, tmp_path / "rebuilt.db")


def test_damaged_matching_generation_is_repaired_without_source_changes(indexed, tmp_path):
    registry, config, first = indexed
    with closing(sqlite3.connect(first["index_path"])) as db:
        db.execute("DELETE FROM note_bigrams")
        db.commit()
    result = sync_vault(registry, config)
    assert result["generation"] == first["generation"]
    assert result["build_mode"] == "full" and result["fallback_reason"]
    assert_matches_fresh(config.root_path, result, tmp_path / "repaired.db")


def test_missing_predecessor_and_interrupted_delta_fall_back_to_complete_build(indexed, monkeypatch, tmp_path):
    registry, config, first = indexed
    Path(first["index_path"]).unlink()
    (config.root_path / "new.md").write_text("# New\n", encoding="utf-8")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "full" and result["fallback_reason"]
    current = registry.resolve(config.id)
    def fail_after_copy(destination, *args, **kwargs):
        destination.write_bytes(b"interrupted clone")
        raise OSError("simulated delta interruption")
    monkeypatch.setattr(indexer, "build_incremental_database", fail_after_copy)
    (config.root_path / "second.md").write_text("# Second\n", encoding="utf-8")
    retry = sync_vault(registry, current)
    assert retry["build_mode"] == "full" and "interruption" in retry["fallback_reason"]
    assert_matches_fresh(config.root_path, retry, tmp_path / "recovered.db")


def test_second_scan_change_or_decode_failure_never_publishes_candidate(indexed, monkeypatch):
    registry, config, first = indexed
    old_bytes = Path(first["index_path"]).read_bytes()
    (config.root_path / "new.md").write_text("# New\n", encoding="utf-8")
    original = indexer.scan_notes
    def changed_second(root, **kwargs):
        (root / "new.md").write_text("# Concurrent external edit\n", encoding="utf-8")
        return original(root, **kwargs)
    monkeypatch.setattr(indexer, "scan_notes", changed_second)
    with pytest.raises(RuntimeError, match="changed during indexing"):
        sync_vault(registry, config)
    assert registry.resolve(config.id).active_index == config.active_index
    assert Path(first["index_path"]).read_bytes() == old_bytes
    assert not list(registry.vault_state_dir(config).glob(".*.tmp"))
    def unreadable_second(root, **kwargs):
        (root / "bad.md").write_bytes(b"\xff\xfeinvalid")
        return original(root, **kwargs)
    monkeypatch.setattr(indexer, "scan_notes", unreadable_second)
    with pytest.raises(RuntimeError, match="unreadable"):
        sync_vault(registry, config)
    assert registry.resolve(config.id).active_index == config.active_index
    assert not list(registry.vault_state_dir(config).glob(".*.tmp"))


def test_activation_failure_preserves_prior_pointer_and_retry_reuses_valid_candidate(indexed, monkeypatch):
    registry, config, first = indexed
    (config.root_path / "new.md").write_text("# New\n", encoding="utf-8")
    original = registry.update_index
    def interrupted(*args, **kwargs):
        raise OSError("simulated pointer publication failure")
    monkeypatch.setattr(registry, "update_index", interrupted)
    with pytest.raises(OSError, match="publication"):
        sync_vault(registry, config)
    assert registry.resolve(config.id).active_index == config.active_index
    monkeypatch.setattr(registry, "update_index", original)
    result = sync_vault(registry, config)
    assert result["build_mode"] == "reused"
    assert Path(first["index_path"]).exists()


def test_parallel_scan_has_exact_serial_coverage_and_bounds_directory_tasks(indexed, monkeypatch):
    registry, config, first = indexed
    for i in range(12):
        p = config.root_path / f"Dir{i}/x.md"
        p.parent.mkdir()
        p.write_text(f"# {i}\n", encoding="utf-8")
    monkeypatch.setattr(indexer, "_SCAN_WORKERS", 1)
    serial = indexer.scan_notes(config.root_path)
    original, lock, active, maximum = indexer._scan_group, threading.Lock(), 0, 0
    def counted(*args):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            return original(*args)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(indexer, "_scan_group", counted)
    monkeypatch.setattr(indexer, "_SCAN_WORKERS", 4)
    parallel = indexer.scan_notes(config.root_path)
    assert parallel == serial and maximum <= 4


def test_concurrent_syncs_hold_build_activation_and_pruning_under_one_lock(indexed, monkeypatch):
    registry, config, first = indexed
    original = vault_service.build_generation_with_scan
    entered, release = threading.Event(), threading.Event()
    calls = 0
    def gated(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(vault_service, "build_generation_with_scan", gated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(sync_vault, registry, config)
        assert entered.wait(10)
        two = pool.submit(sync_vault, registry, config)
        release.set()
        a, b = one.result(20), two.result(20)
    assert a["generation"] == b["generation"] == registry.resolve(config.id).index_generation


def test_legacy_index_without_receipt_is_fully_validated_before_delta(indexed, tmp_path):
    registry, config, first = indexed
    Path(first["index_path"]).with_suffix(".verified.json").unlink()
    (config.root_path / "new.md").write_text("# Legacy upgrade\n", encoding="utf-8")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "incremental"
    assert Path(result["index_path"]).with_suffix(".verified.json").is_file()
    assert_matches_fresh(config.root_path, result, tmp_path / "legacy-full.db")


@pytest.mark.parametrize("damage", ["malformed", "wrong_hash", "foreign_vault"])
def test_invalid_projection_receipt_cannot_certify_inherited_data(indexed, tmp_path, damage):
    registry, config, first = indexed
    path = Path(first["index_path"]).with_suffix(".verified.json")
    if damage == "malformed":
        path.write_text("{broken", encoding="utf-8")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["index_sha256" if damage == "wrong_hash" else "vault_id"] = "0" * 64
        path.write_text(json.dumps(data), encoding="utf-8")
    (config.root_path / "new.md").write_text("# New\n", encoding="utf-8")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "full" and result["fallback_reason"]
    assert_matches_fresh(config.root_path, result, tmp_path / "receipt-fallback.db")


def test_donor_edit_between_receipt_lookup_and_copy_is_not_inherited(indexed, monkeypatch, tmp_path):
    registry, config, first = indexed
    original = indexer._copy_verified_projection
    def changed_donor(destination, source, expected):
        before = source.stat()
        with closing(sqlite3.connect(source)) as db:
            db.execute("UPDATE notes_fts SET content='tampered donor'")
            db.commit()
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        return original(destination, source, expected)
    monkeypatch.setattr(indexer, "_copy_verified_projection", changed_donor)
    (config.root_path / "new.md").write_text("# New\n", encoding="utf-8")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "full" and "bytes differ" in result["fallback_reason"]
    assert_matches_fresh(config.root_path, result, tmp_path / "raced-donor.db")


def test_receipt_publication_failure_preserves_active_pointer_and_is_recoverable(indexed, monkeypatch):
    registry, config, first = indexed
    original = indexer._write_projection_receipt
    def unavailable(*args):
        raise OSError("receipt publication unavailable")
    monkeypatch.setattr(indexer, "_write_projection_receipt", unavailable)
    (config.root_path / "new.md").write_text("# New\n", encoding="utf-8")
    with pytest.raises(OSError, match="receipt publication"):
        sync_vault(registry, config)
    assert registry.resolve(config.id).active_index == config.active_index
    monkeypatch.setattr(indexer, "_write_projection_receipt", original)
    result = sync_vault(registry, config)
    assert result["build_mode"] == "reused"


def test_equal_score_search_candidates_are_identical_after_delta_and_full_build(tmp_path):
    root = tmp_path / "Vault"
    root.mkdir()
    for i in range(90):
        (root / f"doc{i:03}.md").write_text("# Common\nCobalt source\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(root, name="ties")
    sync_vault(registry, config)
    with (root / "doc000.md").open("ab") as out:
        out.write(b"\n")  # Changes source bytes without changing token frequencies.
    result = sync_vault(registry, registry.resolve(config.id))
    assert result["build_mode"] == "incremental"
    scan = indexer.scan_notes(root)
    fresh = tmp_path / "fresh.db"
    generation = indexer._content_generation(scan)
    indexer.build_database(fresh, scan, generation=generation,
                           vault_root=root, vault_id=config.id)
    delta_hits = indexer.search_index(Path(result["index_path"]), "Cobalt", limit=5, vault_root=root)
    full_hits = indexer.search_index(fresh, "Cobalt", limit=5, vault_root=root)
    assert delta_hits == full_hits
    assert delta_hits[0]["path"] == "doc000.md"


def test_no_unchanged_sources_uses_full_builder(indexed):
    registry, config, first = indexed
    for path in config.root_path.rglob("*.md"):
        with path.open("ab") as out:
            out.write(b"\nall sources changed\n")
    result = sync_vault(registry, config)
    assert result["build_mode"] == "full"
    assert "No unchanged notes" in result["fallback_reason"]


def test_explicit_full_rebuild_keeps_generation_and_uses_complete_verification(indexed):
    registry, config, first = indexed
    result = subprocess.run([
        sys.executable, str(Path(indexer.__file__).with_name("knowledge_router.py")),
        "--state-root", str(registry.state_root), "sync", "--full", "--vault", config.id, "--json",
    ], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["build_mode"] == "full" and not data["reused"]
    assert data["generation"] == first["generation"]
