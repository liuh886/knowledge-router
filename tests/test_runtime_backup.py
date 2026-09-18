"""OC-05: archive integrity, restore rehearsal, traversal rejection, baseline preservation.

All fixtures live under pytest tmp_path (OS temp). No production Vault,
Ductor, or distribution paths are touched.
"""

# ruff: noqa: E402
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from maintenance_cycle import run_maintenance_cycle
from context_store import register_contexts
from registry import VaultRegistry
from runtime_backup import (
    create_runtime_backup,
    create_source_checkpoint,
    restore_backup_archive,
    verify_backup_archive,
)


def make_vault(tmp_path, name="test"):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("Original", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name=name, write_policy="append-create")
    return registry, config


def register_policy(registry, config):
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": "project:alpha",
                    "type": "project",
                    "name": "Alpha",
                    "purpose": "test",
                    "scopes": ["."],
                    "maintenance": {"review_interval_days": 30},
                }
            ],
        },
    )


def test_archive_integrity_manifest_and_sidecar(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(registry, config)
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    backup_root = tmp_path / "backups"
    result = create_runtime_backup(registry, config, backup_root, label="oc05a")
    archive = Path(result["archive"])
    assert archive.is_file()
    assert archive.parent == backup_root.resolve()
    sidecar = archive.with_name(archive.name + ".sha256")
    assert sidecar.is_file()
    with open(archive, "rb") as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == result["archive_sha256"]
    with zipfile.ZipFile(archive, mode="r") as zf:
        manifest = json.loads(zf.read("MANIFEST.json").decode("utf-8"))
        for entry in manifest["entries"]:
            data = zf.read(entry["path"])
            assert len(data) == entry["size"]
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        # Durable baseline must be archived; rebuildable caches must not be.
        names = set(zf.namelist())
        assert f"{config.id}/maintenance_baseline.json" in names
        assert f"{config.id}/context_definitions.json" in names
        assert not any(n.startswith(f"{config.id}/index.") for n in names)
        assert "taxonomy.json" not in "".join(names)
    report = verify_backup_archive(archive)
    assert report["status"] == "verified"
    assert report["entry_count"] == result["entry_count"]
    # Source checkpoint stays allowlisted and verifiable.
    checkpoint = create_source_checkpoint(
        Path(__file__).resolve().parent.parent, tmp_path / "src-backups", label="oc05src"
    )
    assert Path(checkpoint["archive"]).is_file()
    assert verify_backup_archive(Path(checkpoint["archive"]))["status"] == "verified"


def test_restore_rehearsal_isolated_target(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(registry, config)
    run_maintenance_cycle(registry, config)
    backup_root = tmp_path / "backups"
    result = create_runtime_backup(registry, config, backup_root, label="oc05b")
    archive = Path(result["archive"])
    target = tmp_path / "isolated-restore"
    assert not target.exists()
    restored = restore_backup_archive(archive, target)
    assert restored["status"] == "restored"
    assert restored["entry_count"] == result["entry_count"]
    # Every archived durable file must match byte-for-byte.
    with zipfile.ZipFile(archive, mode="r") as zf:
        for entry in json.loads(zf.read("MANIFEST.json").decode("utf-8"))["entries"]:
            expected = zf.read(entry["path"])
            actual = (target / entry["path"]).read_bytes()
            assert actual == expected
    # Production state was never overwritten: baseline still present in state dir.
    assert (registry.vault_state_dir(config) / "maintenance_baseline.json").is_file()


def test_traversal_rejected_and_nothing_escapes(tmp_path):
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    evil = backup_root / "evil.zip"
    with zipfile.ZipFile(evil, mode="w") as zf:
        zf.writestr(
            "MANIFEST.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "runtime-backup",
                    "entries": [{"path": "../escape.txt", "sha256": "x", "size": 1}],
                }
            ),
        )
        zf.writestr("../escape.txt", "x")
    with pytest.raises((ValueError, RuntimeError)):
        verify_backup_archive(evil)
    target = tmp_path / "isolated-evil"
    with pytest.raises((ValueError, RuntimeError)):
        restore_backup_archive(evil, target)
    assert not (tmp_path / "escape.txt").exists()
    assert not target.exists() or list(target.iterdir()) == []

    absolute = backup_root / "absolute.zip"
    with zipfile.ZipFile(absolute, mode="w") as zf:
        zf.writestr(
            "MANIFEST.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "runtime-backup",
                    "entries": [{"path": "/tmp/x.txt", "sha256": "x", "size": 1}],
                }
            ),
        )
        zf.writestr("/tmp/x.txt", "x")
    with pytest.raises((ValueError, RuntimeError)):
        verify_backup_archive(absolute)


def test_baseline_preserved_on_failure(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(registry, config)
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    baseline_path = registry.vault_state_dir(config) / "maintenance_baseline.json"
    before = baseline_path.read_bytes()
    backup_root = tmp_path / "backups"
    result = create_runtime_backup(registry, config, backup_root, label="oc05c")
    archive = Path(result["archive"])
    # Corrupt one archived byte without updating the manifest.
    corrupted = backup_root / "corrupted.zip"
    corrupted.write_bytes(archive.read_bytes())
    with zipfile.ZipFile(corrupted, mode="a") as zf:
        zf.writestr(f"{config.id}/context_definitions.json", b"tampered")
    with pytest.raises((ValueError, RuntimeError)):
        verify_backup_archive(corrupted)
    with pytest.raises((ValueError, RuntimeError)):
        restore_backup_archive(corrupted, tmp_path / "isolated-corrupt")
    # Failed verification/restore must leave the live success baseline untouched.
    assert baseline_path.read_bytes() == before
    # Restore must refuse a non-empty target without an explicit empty-target flag.
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        restore_backup_archive(archive, occupied)
    assert (occupied / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert baseline_path.read_bytes() == before
    # Explicit empty target is the only allowed existing-directory restore.
    empty_dir = tmp_path / "empty-ok"
    empty_dir.mkdir()
    ok = restore_backup_archive(archive, empty_dir, allow_empty_existing=True)
    assert ok["status"] == "restored"
    assert baseline_path.read_bytes() == before

