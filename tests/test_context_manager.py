"""Functional Context acceptance across different layouts, ambiguity and read-only safety."""

# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from context_manager import discover_contexts, plan_contexts, summarize_contexts
from fts_indexer import ScanReport, scan_notes
from registry import VaultRegistry


def write(
    root: Path, path: str, text: str = "# Evidence\nOriginal human content\n"
) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_daily_project_contact_share_one_event_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    write(root, "100_Logs/daily/2026-09-07.md")
    write(root, "100_Project/2609_Router/PROGRESS.md")
    write(root, "300_Resources/305_Contact/张一帆.md")
    before = snapshot(root)
    scan = scan_notes(root)
    catalog = discover_contexts(scan, vault_id="test")
    result = plan_contexts("今天和张一帆讨论 Router 项目，下周提醒跟进", catalog, scan)
    assert {i["type"] for i in result["impacts"]} == {"daily", "project", "contact"}
    assert result["event"]["text"].startswith("今天")
    assert result["execution"]["writes_performed"] == 0
    assert result["followup"] == {"detected": True, "scheduled": False, "due_at": None}
    assert snapshot(root) == before
    for impact in result["impacts"]:
        for evidence in impact["evidence"]:
            assert evidence["source_sha256"] == before[evidence["path"]]


def test_custom_type_multiple_scopes_and_declared_alias(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    write(root, "Unusual/one.md")
    write(root, "Elsewhere/two.md")
    scan = scan_notes(root)
    definitions = {
        "schema_version": 1,
        "contexts": [
            {
                "id": "practice:language",
                "type": "language_practice",
                "name": "口语训练",
                "purpose": "持续跟踪口语训练",
                "scopes": ["Unusual", "Elsewhere/two.md"],
                "aliases": ["speaking"],
            }
        ],
    }
    catalog = discover_contexts(scan, vault_id="test", definitions=definitions)
    result = plan_contexts("speaking progress", catalog, scan)
    assert result["impact_count"] == 1
    assert len(result["impacts"][0]["evidence"]) == 2
    assert catalog["unclassified_note_count"] == 0


def test_duplicate_contact_names_stay_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    write(root, "Contacts/Work/Ann.md")
    write(root, "Contacts/Personal/Ann.md")
    scan = scan_notes(root)
    catalog = discover_contexts(scan, vault_id="test")
    result = plan_contexts("Talked with Ann", catalog, scan)
    assert result["impact_count"] == 2
    assert result["ambiguity_count"] == 1
    assert plan_contexts("annual review", catalog, scan)["evidence_gap"]


@pytest.mark.parametrize(
    "scope", ["../escape", "C:/outside", "/outside", "a/../b", ".obsidian"]
)
def test_definitions_reject_unsafe_paths(tmp_path: Path, scope: str) -> None:
    root = tmp_path / "vault"
    write(root, "note.md")
    with pytest.raises(ValueError, match="Unsafe"):
        discover_contexts(
            scan_notes(root),
            vault_id="test",
            definitions={
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "x",
                        "type": "x",
                        "name": "x",
                        "purpose": "x",
                        "scopes": [scope],
                    }
                ],
            },
        )


def test_missing_binding_and_incomplete_scan_are_visible(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    write(root, "note.md")
    catalog = discover_contexts(
        scan_notes(root),
        vault_id="test",
        definitions={
            "schema_version": 1,
            "contexts": [
                {
                    "id": "x",
                    "type": "custom",
                    "name": "Lost",
                    "purpose": "test",
                    "scopes": ["contacts/missing"],
                }
            ],
        },
    )
    assert catalog["signals"][0]["code"] == "scope_without_evidence"
    missing_plan = plan_contexts("Lost", catalog, scan_notes(root))
    assert missing_plan["evidence_gap"]
    assert missing_plan["impacts"][0]["suggested_operation"] == "resolve_scope"
    assert not (root / "contacts").exists()
    with pytest.raises(ValueError, match="complete"):
        discover_contexts(ScanReport((), ("unreadable.md",), ()), vault_id="test")


def test_catalog_identity_bounds_and_declaration_override(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    write(root, "Projects/Alpha/README.md")
    write(root, "Contacts/Ann.md")
    scan = scan_notes(root)
    original = discover_contexts(scan, vault_id="test")
    assert summarize_contexts(original, limit=1)["truncated"]
    write(root, "new.md")
    newer = discover_contexts(scan_notes(root), vault_id="test")
    assert {c["id"] for c in original["contexts"]} == {
        c["id"] for c in newer["contexts"]
    }
    assert original["source_generation"] != newer["source_generation"]
    declared = discover_contexts(
        scan,
        vault_id="test",
        definitions={
            "schema_version": 1,
            "contexts": [
                {
                    "id": "alpha",
                    "type": "project",
                    "name": "Alpha",
                    "purpose": "Research trial",
                    "scopes": ["Projects/Alpha"],
                }
            ],
        },
    )
    assert sum(c["scopes"] == ("Projects/Alpha",) for c in declared["contexts"]) == 1


def test_cli_discovers_and_plans_without_requiring_an_index(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    write(root, "Daily/2026-09-07.md")
    write(root, "Projects/Alpha/README.md")
    write(root, "Contacts/Ann.md")
    state = tmp_path / "state"
    VaultRegistry(state).attach(root, name="test")
    before = snapshot(root)
    base = [
        sys.executable,
        str(SCRIPT_DIR / "knowledge_router.py"),
        "--state-root",
        str(state),
    ]
    for args in (["discover"], ["plan", "Today Ann discussed Alpha"]):
        run = subprocess.run(
            [*base, "contexts", *args, "--vault", "test", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert run.returncode == 0, run.stderr
        output = json.loads(run.stdout)
        assert output["schema_version"] == 1
        if args[0] == "plan":
            assert output["impact_count"] == 3
    assert snapshot(root) == before
