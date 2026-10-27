"""Personal profile integration: provenance, retries, accounting boundaries and review."""
import hashlib
import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from capture_broker import commit_capture_plan
from context_review import review_agenda
from context_store import register_contexts
from fts_indexer import scan_notes
from memory_service import append_inbox_stream
from personal_review import personal_findings, personal_source_state, stream_catalog
from registry import VaultRegistry
from vault_service import search_vault


@pytest.fixture
def profile(tmp_path):
    root = tmp_path / "Vault"
    for folder in ("000_Inbox", "200_Area/Biography/Milestones", "200_Area/Investment/Pool"):
        (root / folder).mkdir(parents=True)
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(root, name="test", write_policy="append-create")
    return registry, config


def test_area_captures_are_searchable_and_retry_does_not_duplicate(profile):
    registry, config = profile
    result = append_inbox_stream(registry, config, "biography", "glacierproof new delivery",
                                 tier="L1", event_date="2025-12-31", idempotency_key="one")
    path = config.root_path / result["file"]
    before = path.read_bytes()
    assert result["file"].endswith("biography.2025.md")
    assert b"## 2025-12" in before and "Experience (L1)" in result["entry"]
    assert result["verification"] == "unreviewed"
    assert result["capture_id"] in result["entry"]
    retry = append_inbox_stream(registry, config, "biography", "glacierproof new delivery",
                                tier="L1", event_date="2025-12-31", idempotency_key="one")
    assert retry["capture_id"] == result["capture_id"]
    assert path.read_bytes() == before
    hits = search_vault(registry, registry.resolve(config.id), "glacierproof", limit=5)
    assert hits and not hits[0]["stale"]
    with pytest.raises(ValueError, match="different workflow"):
        append_inbox_stream(registry, config, "biography", "another claim", idempotency_key="one")
    assert not list((config.root_path / "200_Area/Biography/Milestones").iterdir())


def test_investment_intake_never_changes_a_holding(profile):
    registry, config = profile
    stock = config.root_path / "200_Area/Investment/Pool/ABC.md"
    stock.write_text("---\ntype: stock-holding\nticker: ABC\nshares: 10\n---\n", encoding="utf-8")
    before = stock.read_bytes()
    result = append_inbox_stream(registry, config, "investment", "SELL ABC 999999 shares")
    assert not result["ledger_changed"]
    assert stock.read_bytes() == before
    assert result["file"].startswith("200_Area/Investment/investment.")
    assert "Pending investment" in result["entry"]


def test_preview_and_source_race_are_safe(profile):
    registry, config = profile
    first = append_inbox_stream(registry, config, "biography", "original")
    path = config.root_path / first["file"]
    before = path.read_bytes()
    preview = append_inbox_stream(registry, config, "biography", "pending", preview=True)
    assert path.read_bytes() == before
    with path.open("ab") as out:
        out.write(b"human edit\n")
    human = path.read_bytes()
    with pytest.raises(RuntimeError, match="Compare-and-swap"):
        commit_capture_plan(config.root_path, registry.vault_state_dir(config), preview["plan_id"])
    assert path.read_bytes() == human


def test_bom_crlf_month_rollover_preserves_every_old_byte(profile):
    registry, config = profile
    path = config.root_path / "200_Area/Biography/biography.2026.md"
    before = b"\xef\xbb\xbf---\r\ntype: biography-chronicle\r\n---\r\n# Old\r\n## 2026-01\r\nold\r\n"
    path.write_bytes(before)
    append_inbox_stream(registry, config, "biography", "month end", event_date="2026-02-01")
    after = path.read_bytes()
    assert after.startswith(before)
    assert b"## 2026-02\r\n" in after
    assert b"\n" not in after.replace(b"\r\n", b"")


@pytest.mark.parametrize("kind,text,options", [
    ("biography", "x", {"tier": "L4"}), ("memo", "x", {"tier": "L1"}),
    ("biography", "x", {"event_date": "2026-02-30"}),
    ("biography", "x", {"year": 2025}), ("todo", "x", {"event_date": "2026-01-01"}),
    ("idea", "first\nsecond", {}), ("investment", "\x00", {}),
])
def test_invalid_capture_changes_no_source(profile, kind, text, options):
    registry, config = profile
    with pytest.raises(ValueError):
        append_inbox_stream(registry, config, kind, text, **options)
    assert not list(config.root_path.rglob("*.md"))


def test_missing_area_does_not_provision_directories(profile):
    registry, config = profile
    (config.root_path / "200_Area/Investment/Pool").rmdir()
    (config.root_path / "200_Area/Investment").rmdir()
    with pytest.raises((FileNotFoundError, ValueError)):
        append_inbox_stream(registry, config, "investment", "raw input")
    assert not (config.root_path / "200_Area/Investment").exists()
    assert stream_catalog(config.root_path)["streams"][-1]["status"] == "unavailable"


def test_todo_creation_time_is_not_scheduled_time_and_parser_is_preserved(profile):
    registry, config = profile
    plain = append_inbox_stream(registry, config, "todo", "buy milk")
    assert "⏳" not in plain["entry"] and "[created::" in plain["entry"]
    timed = append_inbox_stream(registry, config, "todo", "2026-10-14 15:00 客户会议")
    assert "[start:: 2026-10-14T15:00:00+08:00]" in timed["entry"]
    unknown = append_inbox_stream(registry, config, "todo", "明天25:00会议")
    assert "[calendar:: unresolved]" in unknown["entry"] and "[start::" not in unknown["entry"]


def test_pending_index_retries_frozen_area_input(profile):
    registry, config = profile
    with patch("vault_service.sync_vault", side_effect=OSError("offline index")):
        first = append_inbox_stream(registry, config, "investment", "raw note", idempotency_key="retry")
    assert first["status"] == "applied_pending_index"
    path = config.root_path / first["file"]
    before = path.read_bytes()
    retry = append_inbox_stream(registry, config, "investment", "raw note", idempotency_key="retry")
    assert retry["status"] == "success" and path.read_bytes() == before


def test_domain_findings_join_review_queue_without_source_mutations(profile):
    registry, config = profile
    path = config.root_path / "200_Area/Biography/Milestones/example.md"
    path.write_text("---\ntype: milestone\nstatus: completed\nverification_status: verified\n"
                    "tier: L1-milestone\ndate: 2026-01-01\nevidence: null\n---\n# Claim\n", encoding="utf-8")
    register_contexts(registry, config, {"schema_version": 1, "contexts": [{
        "id": "bio:main", "type": "biography", "name": "Biography", "purpose": "facts",
        "scopes": ["200_Area/Biography"], "maintenance": {"enabled": True},
    }]})
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = review_agenda(registry, config, context_id="bio:main", record=False)
    assert "biography_review_receipt_missing" in result["counts_by_code"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    findings = personal_findings(scan_notes(config.root_path), today=date(2026, 10, 25))
    assert {f["code"] for f in findings} >= {"biography_evidence_missing", "biography_review_receipt_missing"}


def test_cli_stream_preview_and_checks(profile):
    registry, config = profile
    base = [sys.executable, str(SCRIPTS / "knowledge_router.py"), "--state-root", str(registry.state_root)]
    result = subprocess.run(base + ["streams", "append", "biography", "preview only", "--preview",
                            "--vault", config.id, "--json"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0 and '"status": "preview"' in result.stdout
    assert not list(config.root_path.rglob("*.md"))


def test_excerpts_disclose_declared_verification_and_invalid_receipt_dates(profile):
    registry, config = profile
    path = "200_Area/Biography/Milestones/claim.md"
    text = ("---\ntype: milestone\nverification_status: verified\nverified_at: 2999-01-01\n"
            "verified_by: source reviewer\nsource_locator: page 1\n---\n# Claim\n")
    (config.root_path / path).write_text(text, encoding="utf-8")
    state = personal_source_state(path, text)
    assert state["verification_status"] == "verified"
    assert "not Router certification" in state["meaning"]
    findings = personal_findings(scan_notes(config.root_path), today=date(2026, 10, 25))
    assert "biography_review_date_invalid" in {item["code"] for item in findings}
