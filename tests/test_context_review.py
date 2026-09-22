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
from context_compile import (
    compiled_card_name,
    compiled_root,
    compile_context,
    scoped_source_dependencies,
    source_snapshot_sha256,
    write_compiled_card,
)
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


def _compiled_vault(tmp_path):
    """A quiet in-scope Vault (no broken links, no orphans) so cards are the only items."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Clean.md").write_text("# Clean\n[[Clean2]]", encoding="utf-8")
    (root / "Area" / "Clean2.md").write_text("# Clean2\n[[Clean]]", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="compiled")
    register_contexts(registry, config, DEFINITIONS)
    return registry, config, root


def _stale_paths(result) -> set[str]:
    return {
        row["path"]
        for row in result["items"]
        if row.get("kind") == "compiled_card_stale"
    }


def _basis_for(config, context=None):
    """The bundle snapshot a draft must carry back to get a verified basis."""
    context = context if context is not None else DEFINITIONS["contexts"][0]
    return source_snapshot_sha256(scoped_source_dependencies(config, context))


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


def test_scope_change_is_incomparable_and_never_confirms_resolution(vault):
    """Changing a Context's scope observes a different source set; that is not a fix.

    Vanished items may simply have left the scope, so the comparison must be refused
    rather than reported as resolved.
    """
    registry, config, root = vault
    first = review_agenda(registry, config)
    assert first["delta"]["comparable"] is False  # no previous entry yet
    assert first["basis"]["context_revision"] == 1
    assert first["basis"]["definitions_fingerprint"]

    # Same notes, same contents: only the declared scope changes.
    moved = json.loads(json.dumps(DEFINITIONS))
    moved["contexts"][0]["scopes"] = ["Out"]
    register_contexts(registry, config, moved, expected_revision=1)

    second = review_agenda(registry, config)
    assert second["basis"]["complete"] is True
    assert second["delta"]["comparable"] is False
    assert second["delta"]["incomparable_reason"] == "context_definitions_changed"
    assert second["delta"]["resolved_item_ids"] == []
    assert second["delta"]["resolved_suppressed"] > 0
    # The notes were never touched.
    assert (root / "Area" / "Broken.md").read_text(encoding="utf-8").endswith("[[Gone]]")


def test_diagnostics_version_change_is_incomparable(vault):
    """Findings from different diagnostic rules are not comparable either."""
    registry, config, _ = vault
    review_agenda(registry, config)
    ledger = registry.vault_state_dir(config) / "review_ledger.jsonl"
    entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    entry["fingerprint"] = "unchanged-marker"
    entry["basis"]["diagnostics_version"] = "0"
    ledger.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    again = review_agenda(registry, config)
    assert again["delta"]["comparable"] is False
    assert again["delta"]["incomparable_reason"] == "diagnostics_version_changed"


def test_incomplete_review_exits_nonzero_like_tick_and_coverage(tmp_path):
    """A scheduler reading only the exit code must not see a truncated check as success."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Out").mkdir()
    (root / "Area" / "Clean.md").write_text("# Clean\n[[Clean2]]", encoding="utf-8")
    (root / "Area" / "Clean2.md").write_text("# Clean2\n[[Clean]]", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="exitcode")
    register_contexts(registry, config, DEFINITIONS)
    # Registered first, then made unreadable - the realistic degradation path. It lives
    # outside the scope so the agenda is genuinely empty, not merely attention-worthy.
    (root / "Out" / "not-utf8.md").write_bytes(b"\xff\xfe\x00bad bytes")

    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    run = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--state-root",
            str(registry.state_root),
            "contexts",
            "review",
            "--no-record",
            "--vault",
            config.id,
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert run.returncode == 2, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "incomplete"
    assert result["scan_incomplete"] is True


def test_custom_out_root_card_is_detected_as_stale(tmp_path):
    """A card saved with --out-root/--name is tracked by its recorded output path."""
    registry, config, root = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    written = write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Different.md",
        out_root=str(out_root), expected_source_snapshot=_basis_for(config),
    )
    assert written["registered"] is True
    card = out_root / "Different.md"
    assert card.is_file()

    assert _stale_paths(review_agenda(registry, config, record=False)) == set()

    # A scoped source changes: the registered card must now be reported by its real path.
    (root / "Area" / "Topic.md").write_text("# Topic\n[[Missing]] changed", encoding="utf-8")
    stale = review_agenda(registry, config, record=False)
    item = next(row for row in stale["items"] if row["kind"] == "compiled_card_stale")
    assert item["code"] == "compiled_card_stale"
    assert item["path"] == str(card.resolve())
    assert item["changed_source_count"] >= 1
    assert str(card.resolve()) in _stale_paths(stale)


def test_deleted_registered_card_is_reported_not_ignored(tmp_path):
    """A card that disappeared must be surfaced, not silently dropped."""
    registry, config, _ = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Gone.md",
        out_root=str(out_root), expected_source_snapshot=_basis_for(config),
    )
    (out_root / "Gone.md").unlink()

    result = review_agenda(registry, config, record=False)
    codes = {row.get("code") for row in result["items"]}
    assert "compiled_card_missing" in codes


def test_rescoping_a_context_marks_its_card_scope_changed(tmp_path):
    """A card built for the old scope must not be treated as current."""
    registry, config, _ = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Scoped.md",
        out_root=str(out_root), expected_source_snapshot=_basis_for(config),
    )
    moved = json.loads(json.dumps(DEFINITIONS))
    moved["contexts"][0]["scopes"] = ["Out"]
    (tmp_path / "vault" / "Out").mkdir()
    (tmp_path / "vault" / "Out" / "Other.md").write_text("# Other\nbody", encoding="utf-8")
    register_contexts(registry, config, moved, expected_revision=1)

    result = review_agenda(registry, config, record=False)
    codes = {row.get("code") for row in result["items"]}
    assert "compiled_card_scope_changed" in codes


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
    (root / "Area" / "Clean.md").write_text("# Clean\n[[Clean2]]", encoding="utf-8")
    (root / "Area" / "Clean2.md").write_text("# Clean2\n[[Clean]]", encoding="utf-8")
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


def test_agenda_surfaces_scoped_orphans_and_ignores_unscoped_ones(tmp_path):
    """A note nobody links to and that links nowhere is reported, but only in scope."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Out").mkdir()
    (root / "Area" / "Isolated.md").write_text("# Isolated\nnothing links here", encoding="utf-8")
    (root / "Area" / "Linked.md").write_text("# Linked\n[[Missing]]", encoding="utf-8")
    (root / "Out" / "Stray.md").write_text("# Stray\nno links either", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="orphans")
    register_contexts(registry, config, DEFINITIONS)

    result = review_agenda(registry, config, record=False)
    orphan_paths = {
        item["path"] for item in result["items"] if item.get("code") == "orphan_note"
    }
    assert result["counts_by_code"]["orphan_note"] == 1
    assert orphan_paths == {"Area/Isolated.md"}
    # A note with an outgoing link is not an orphan, and an out-of-scope note is not
    # reported even when it is genuinely isolated.
    assert "Area/Linked.md" not in orphan_paths
    assert "Out/Stray.md" not in orphan_paths


def test_code_filter_changes_display_only_not_the_ledger(tmp_path):
    """Filtering a view must never look like resolving items."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Broken.md").write_text("# B\n[[Gone]]", encoding="utf-8")
    (root / "Area" / "Isolated.md").write_text("# I\nno links", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="filter")
    register_contexts(registry, config, DEFINITIONS)

    everything = review_agenda(registry, config, record=False, limit=50)
    filtered = review_agenda(registry, config, record=False, limit=50, code="orphan_note")
    assert filtered["item_count"] == everything["item_count"]
    assert filtered["fingerprint"] == everything["fingerprint"]
    assert filtered["counts_by_code"] == everything["counts_by_code"]
    assert filtered["display_filter"] == "orphan_note"
    assert filtered["display_matched"] == everything["counts_by_code"]["orphan_note"]
    assert all(item["code"] == "orphan_note" for item in filtered["items"])
    assert "筛选只影响显示" in format_review_agenda(filtered)


def test_scope_is_decided_over_all_occurrences_not_the_truncated_sample(tmp_path):
    """A target whose only in-scope source is its 21st occurrence must still be reported.

    Doctor keeps a 20-source display sample per target. Deciding scope membership from that
    sample silently dropped the target, and an empty agenda over a complete basis then
    reported `clear`.
    """
    root = tmp_path / "vault"
    (root / "Out").mkdir(parents=True)
    (root / "ZZ").mkdir()
    for index in range(1, 21):
        (root / "Out" / f"a{index:02d}.md").write_text("# Out\n[[Missing]]", encoding="utf-8")
    (root / "ZZ" / "note.md").write_text("# Note\n[[Missing]]", encoding="utf-8")
    definitions = {
        "schema_version": 1,
        "contexts": [
            {
                "id": "research:zz",
                "type": "research",
                "name": "ZZ",
                "purpose": "Scoped",
                "scopes": ["ZZ"],
                "aliases": ["ZZ"],
            }
        ],
    }
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="sample")
    register_contexts(registry, config, definitions)

    result = review_agenda(registry, config, record=False)
    quality = [item for item in result["items"] if item.get("target") == "Missing"]
    assert quality, "the in-scope occurrence must not be lost to the display sample"
    assert quality[0]["occurrences"] == 1
    assert quality[0]["vault_target_occurrences"] == 21
    assert "全库同一目标共 21 处" in quality[0]["reason"]
    assert result["status"] == "attention"


def test_unreadable_card_registry_cannot_confirm_resolutions(tmp_path):
    """A damaged registry must not make a stale card look resolved.

    `load_compiled_cards` used to swallow a JSON error into an empty list, so the card simply
    vanished from the agenda while the basis still claimed to be complete.
    """
    registry, config, root = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Tracked.md",
        out_root=str(out_root), expected_source_snapshot=_basis_for(config),
    )
    (root / "Area" / "Clean.md").write_text("# Clean\n[[Clean2]] changed", encoding="utf-8")

    first = review_agenda(registry, config)  # records a ledger entry with the stale card
    assert "compiled_card_stale" in {row.get("code") for row in first["items"]}

    registry_file = registry.vault_state_dir(config) / "compiled_cards.json"
    registry_file.write_text("{ this is not json", encoding="utf-8")
    second = review_agenda(registry, config)

    assert second["basis"]["card_registry_state"] == "invalid"
    assert second["basis"]["complete"] is False
    assert "card_registry_invalid" in second["basis"]["incomplete_reasons"]
    # Items exist, so `status` stays attention - but incompleteness must not be hidden by it.
    assert second["incomplete"] is True
    assert "⚠ 判定不完整" in format_review_agenda(second)
    assert second["delta"]["comparable"] is False
    assert second["delta"]["resolved_item_ids"] == []
    assert second["delta"]["resolved_suppressed"] >= 1
    assert "card_registry_unreadable" in {row.get("code") for row in second["items"]}


def test_unrelated_context_does_not_mark_a_card_scope_changed(tmp_path):
    """A card depends on its own Context, not on the whole definition set."""
    registry, config, root = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Mine.md",
        out_root=str(out_root), expected_source_snapshot=_basis_for(config),
    )
    assert _stale_paths(review_agenda(registry, config, record=False)) == set()

    (root / "Other").mkdir()
    (root / "Other" / "Note.md").write_text("# Other\nbody", encoding="utf-8")
    widened = json.loads(json.dumps(DEFINITIONS))
    widened["contexts"].append(
        {
            "id": "research:other",
            "type": "research",
            "name": "Other",
            "purpose": "Unrelated",
            "scopes": ["Other"],
            "aliases": ["Other"],
        }
    )
    register_contexts(registry, config, widened, expected_revision=1)

    result = review_agenda(registry, config, record=False)
    codes = {row.get("code") for row in result["items"]}
    assert "compiled_card_scope_changed" not in codes
    assert "compiled_card_stale" not in codes


def test_unverified_basis_is_flagged_not_treated_as_clean(tmp_path):
    """Saving without the bundle's snapshot must not read as "sources unchanged"."""
    registry, config, _ = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    written = write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Loose.md",
        out_root=str(out_root),
    )
    assert written["basis"] == "unverified"

    result = review_agenda(registry, config, record=False)
    codes = {row.get("code") for row in result["items"]}
    assert "compiled_card_unverified_basis" in codes


def test_save_refuses_when_sources_changed_since_the_bundle(tmp_path):
    """A draft cannot be vouched for by a source state newer than the one it was written from.

    Reproduces the reported case: the source said OLD, the bundle was assembled, the source
    changed to NEW, and saving the OLD draft used to register NEW as its trusted basis.
    """
    registry, config, root = _compiled_vault(tmp_path)
    note = root / "Area" / "Clean2.md"
    note.write_text("# Clean2\nOLD decision\n", encoding="utf-8")
    bundle_basis = _basis_for(config)

    note.write_text("# Clean2\nNEW decision\n", encoding="utf-8")
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    try:
        write_compiled_card(
            registry, config, "research:area", "# stale draft\nbased on OLD",
            name="Stale.md", out_root=str(out_root),
            expected_source_snapshot=bundle_basis,
        )
    except RuntimeError as error:
        assert "changed since the bundle was assembled" in str(error)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("a stale draft must not be registered as verified")
    assert not (out_root / "Stale.md").exists()


def test_compile_bundle_basis_round_trips_to_a_verified_card(tmp_path):
    """The bundle exposes the basis that makes a save verifiable."""
    registry, config, _ = _compiled_vault(tmp_path)
    bundle = compile_context(registry, config, "research:area")
    assert bundle["source_snapshot_sha256"]
    assert bundle["context_fingerprint"]

    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    written = write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Verified.md",
        out_root=str(out_root),
        expected_source_snapshot=bundle["source_snapshot_sha256"],
    )
    assert written["basis"] == "verified"
    assert _stale_paths(review_agenda(registry, config, record=False)) == set()


def test_externally_edited_card_is_flagged(tmp_path):
    """A card changed outside the tool must be re-reviewed, not assumed current."""
    registry, config, _ = _compiled_vault(tmp_path)
    out_root = tmp_path / "custom_cards"
    out_root.mkdir()
    write_compiled_card(
        registry, config, "research:area", "# Area\nbody", name="Edited.md",
        out_root=str(out_root), expected_source_snapshot=_basis_for(config),
    )
    assert _stale_paths(review_agenda(registry, config, record=False)) == set()

    card = out_root / "Edited.md"
    card.write_text(card.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    result = review_agenda(registry, config, record=False)
    codes = {row.get("code") for row in result["items"]}
    assert "compiled_card_edited_externally" in codes


def test_agenda_shows_a_maintenance_coverage_gap(tmp_path):
    """An empty poll must not read as "maintenance is covered" when no policy is enabled."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Clean.md").write_text("# Clean\n[[Clean2]]", encoding="utf-8")
    (root / "Area" / "Clean2.md").write_text("# Clean2\n[[Clean]]", encoding="utf-8")
    definitions = json.loads(json.dumps(DEFINITIONS))
    definitions["contexts"][0]["maintenance"] = {"enabled": False}
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="coverage")
    register_contexts(registry, config, definitions)

    result = review_agenda(registry, config, record=False)
    assert result["basis"]["maintenance_status"] == "no_policies"
    assert result["basis"]["maintenance_configured_contexts"] == 0
    assert "维护覆盖" in format_review_agenda(result)


def test_no_enabled_policy_skips_the_source_baseline(tmp_path, monkeypatch):
    """Nothing is inspected when no policy is enabled, so no baseline is computed."""
    import context_maintenance

    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Clean.md").write_text("# Clean\nbody", encoding="utf-8")
    definitions = json.loads(json.dumps(DEFINITIONS))
    definitions["contexts"][0]["maintenance"] = {"enabled": False}
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="skip")
    register_contexts(registry, config, definitions)

    calls = {"n": 0}
    real = context_maintenance._bindings

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(context_maintenance, "_bindings", counting)
    queue = context_maintenance.maintenance_queue(registry, config)
    assert queue["status"] == "no_policies"
    assert queue["items"] == []
    assert calls["n"] == 0, "no baseline should be computed for an empty policy set"


def test_unregistered_card_is_flagged_rather_than_silently_trusted(tmp_path):
    """A card with no record cannot be validated, so it must not pass as current.

    A newer modification time than its sources is not evidence: its content and authoring
    basis are both unknown to the tool.
    """
    registry, config, _ = _compiled_vault(tmp_path)
    card_dir = compiled_root(config)
    card_dir.mkdir(parents=True, exist_ok=True)
    card = card_dir / compiled_card_name(DEFINITIONS["contexts"][0])
    card.write_text("# hand-written card, never registered\n", encoding="utf-8")

    result = review_agenda(registry, config, record=False)
    item = next(
        row for row in result["items"] if row.get("code") == "compiled_card_unregistered"
    )
    assert item["path"] == f"{card_dir.name}/{card.name}"
    assert item["basis"] == "unregistered"
    assert "无法判断" in item["reason"]


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


def test_basis_without_semantic_keys_is_incomparable(vault):
    """A full basis from before the fingerprint existed cannot confirm resolutions.

    Those entries recorded the item set but nothing identifying *what they observed*, so
    they must not be blamed on a definitions change either.
    """
    registry, config, _ = vault
    review_agenda(registry, config, limit=20)
    ledger = registry.vault_state_dir(config) / "review_ledger.jsonl"
    entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    entry["item_ids"] = ["stale-id"]
    entry["basis"].pop("definitions_fingerprint", None)
    entry["basis"].pop("diagnostics_version", None)
    ledger.write_text(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = review_agenda(registry, config, limit=20)
    assert result["delta"]["comparable"] is False
    assert result["delta"]["incomparable_reason"] == "previous_basis_not_semantic"
    assert result["delta"]["resolved_item_ids"] == []
    assert result["delta"]["resolved_suppressed"] == 1

