"""Bounded review agenda and the durable external deposition ledger."""

# ruff: noqa: E402
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_review import format_review_agenda, review_agenda
from context_store import register_contexts
from registry import VaultRegistry

DEFINITIONS = {
    "schema_version": 1,
    "contexts": [
        {
            "id": "research:area",
            "type": "research",
            "name": "Area",
            "purpose": "Scoped work",
            "scopes": ["Area"],
            "aliases": ["Area"],
            "maintenance": {
                "enabled": True,
                "review_interval_days": 1,
                "check_source_changes": True,
            },
        }
    ],
}


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Area" / "_compiled").mkdir(parents=True)
    (root / "Out").mkdir()
    (root / "Area" / "Topic.md").write_text("# Topic\n[[Missing]]", encoding="utf-8")
    (root / "Area" / "Broken.md").write_text("# Broken\n[[Gone]]", encoding="utf-8")
    (root / "Out" / "Stray.md").write_text("# Stray\n[[StrayGone]]", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test")
    register_contexts(registry, config, DEFINITIONS)
    return registry, config, root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_review_flags_overdue_maintenance_and_records_ledger(vault):
    registry, config, _ = vault
    later = datetime.now(UTC) + timedelta(days=3)
    result = review_agenda(registry, config, now=later)
    codes = {item["code"] for item in result["items"] if item["kind"] == "maintenance"}
    assert "review_due" in codes
    assert result["ledger_written"] is True
    ledger = registry.vault_state_dir(config) / "review_ledger.jsonl"
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["scope"] == "*"
    assert entries[0]["fingerprint"] == result["fingerprint"]


def test_review_quality_findings_stay_inside_scope(vault):
    registry, config, _ = vault
    result = review_agenda(registry, config, record=False)
    quality = [item for item in result["items"] if item["kind"] == "quality"]
    paths = {item.get("path") for item in quality}
    assert "Area/Broken.md" in paths
    assert "Out/Stray.md" not in paths


def test_review_delta_reports_resolved_items(vault):
    registry, config, root = vault
    first = review_agenda(registry, config)
    assert first["delta"]["previous_fingerprint"] is None

    target = root / "Area" / "Broken.md"
    target.write_text("# Broken\nfixed", encoding="utf-8")
    second = review_agenda(registry, config)
    assert second["delta"]["resolved_item_ids"]
    assert second["delta"]["unchanged"] is False

    third = review_agenda(registry, config)
    assert third["delta"]["unchanged"] is True
    assert third["delta"]["new_item_ids"] == []
    assert third["delta"]["resolved_item_ids"] == []


def test_review_is_read_only_against_the_vault(vault):
    registry, config, root = vault
    before = _snapshot(root)
    result = review_agenda(registry, config)
    assert result["vault_writes"] == 0
    assert result["writes_performed"] == 0
    assert _snapshot(root) == before
    assert (registry.vault_state_dir(config) / "review_ledger.jsonl").is_file()


def test_review_no_record_skips_the_ledger(vault):
    registry, config, _ = vault
    result = review_agenda(registry, config, record=False)
    assert result["ledger_written"] is False
    assert not (registry.vault_state_dir(config) / "review_ledger.jsonl").exists()
    assert result["delta"]["previous_fingerprint"] is None


def test_review_flags_a_stale_compiled_card(vault):
    registry, config, root = vault
    card = root / "Area" / "_compiled" / "Area.md"
    card.write_text("# Area\nold", encoding="utf-8")
    old = datetime.now(UTC).timestamp() - 100_000
    os.utime(card, (old, old))
    result = review_agenda(registry, config, record=False)
    stale = [item for item in result["items"] if item["kind"] == "compiled_card_stale"]
    assert stale and stale[0]["path"] == "Area/_compiled/Area.md"


def test_cli_review_json_and_scope(vault):
    registry, config, _ = vault
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    run = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--state-root",
            str(registry.state_root),
            "contexts",
            "review",
            "--context",
            "research:area",
            "--no-record",
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
    assert result["scope"] == "research:area"
    assert result["status"] == "attention"
    assert all(item["context_id"] in (None, "research:area") for item in result["items"])
    assert format_review_agenda(result).startswith("# 复查议程")


def test_smaller_display_limit_does_not_fake_resolutions(vault):
    """A display limit must never change which items count as present.

    Reproduces the reported bug: with the notes completely unchanged, dropping the
    limit from 20 to 1 used to look like every other item had been resolved.
    """
    registry, config, _ = vault
    wide = review_agenda(registry, config, limit=20)
    assert wide["item_count"] > 1
    assert wide["items_truncated"] is False

    narrow = review_agenda(registry, config, limit=1)
    assert narrow["item_count"] == wide["item_count"]
    assert narrow["fingerprint"] == wide["fingerprint"]
    assert len(narrow["items"]) == 1
    assert narrow["items_truncated"] is True
    assert narrow["delta"]["resolved_item_ids"] == []
    assert narrow["delta"]["resolved_suppressed"] == 0
    assert narrow["status"] == "attention"


def test_incomplete_basis_never_reports_clear(tmp_path):
    """No findings over an incomplete basis is not evidence of health."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Out").mkdir()
    (root / "Area" / "Clean.md").write_text("# Clean\nnothing broken here", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="clean")
    register_contexts(registry, config, DEFINITIONS)
    # Registration itself fails closed on an unreadable scan, so the source only
    # becomes unreadable afterwards - the realistic degradation path.
    (root / "Out" / "not-utf8.md").write_bytes(b"\xff\xfe\x00bad bytes")

    result = review_agenda(registry, config, record=False)
    quality = [item for item in result["items"] if item["kind"] == "quality"]
    assert quality == []
    assert result["basis"]["scan_skipped"] == 1
    assert result["basis"]["complete"] is False
    assert "scan_skipped_sources" in result["basis"]["incomplete_reasons"]
    assert result["status"] == "incomplete"
    assert "判定不完整" in format_review_agenda(result)


def test_missing_contexts_never_reports_clear(tmp_path):
    """With no registered Context there is nothing to judge, so never 'clear'."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Note.md").write_text("# Note\nquiet", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="empty")

    result = review_agenda(registry, config, record=False)
    assert result["item_count"] == 0
    assert result["status"] == "incomplete"
    assert "no_registered_contexts" in result["basis"]["incomplete_reasons"]


def test_delta_is_incomparable_against_a_pre_fix_ledger_entry(vault):
    """An older ledger entry without a full basis cannot confirm resolutions."""
    registry, config, _ = vault
    review_agenda(registry, config, limit=20)
    second = review_agenda(registry, config, limit=20)
    assert second["delta"]["comparable"] is True

    ledger = registry.vault_state_dir(config) / "review_ledger.jsonl"
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    legacy = dict(lines[-1])
    legacy.pop("basis", None)
    legacy["item_ids"] = ["legacy-item-that-no-longer-exists"]
    ledger.write_text(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = review_agenda(registry, config, limit=20)
    assert result["delta"]["comparable"] is False
    assert result["delta"]["incomparable_reason"] == "previous_basis_not_full"
    assert result["delta"]["resolved_item_ids"] == []
    assert result["delta"]["resolved_suppressed"] == 1

