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
