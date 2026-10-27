"""Explainable Context quality prioritization never hides unscoped findings."""

# ruff: noqa: E402
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from evidence import QualitySignal
from fts_indexer import ScanReport, scan_notes
from quality_analyzer import context_quality_queue, vault_quality_signals
from vault_doctor import diagnose_vault

CLI = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"


def _signal(kind: str, severity: str, path: str) -> QualitySignal:
    return QualitySignal(
        kind=kind,
        severity=severity,
        source_type="vault",
        source_id="vault",
        path=path,
        reason=f"reason for {path}",
        suggestion="review the cited source",
    )


def test_queue_prioritizes_maintained_contexts_and_keeps_history_hints() -> None:
    contexts = [
        {"id": "contact:alice", "scopes": ["300_Resources/Contacts/Alice.md"]},
        {"id": "project:router", "scopes": ["100_Project/Router"]},
    ]
    signals = [
        _signal("orphan_note", "info", "WYQD/Backups/old.md"),
        _signal("broken_link", "warning", "300_Resources/Contacts/Alice.md"),
        _signal("orphan_note", "info", "100_Project/Router/notes.md"),
    ]

    result = context_quality_queue(contexts, signals, limit=10)

    assert result["status"] == "findings"
    assert result["total"] == 3
    assert result["counts_by_bucket"] == {"maintained": 2, "unscoped": 1}
    assert result["repair_performed"] is False
    assert result["items"][0]["bucket"] == "maintained"
    historical = next(item for item in result["items"] if item["historical_hint"])
    assert historical["category"] == "historical_candidate"
    assert historical["bucket"] == "unscoped"


def test_queue_is_bounded_but_reports_total() -> None:
    signals = [_signal("orphan_note", "info", f"Notes/{index}.md") for index in range(4)]

    result = context_quality_queue([], signals, limit=2)

    assert result["total"] == 4
    assert len(result["items"]) == 2
    assert result["truncated"] == 2


def test_queue_applies_include_exclude_and_nested_tags(tmp_path):
    for path, tag in [("Area/keep/good.md", "Contact/Person"),
                      ("Area/keep/no-tag.md", "other"),
                      ("Area/excluded.md", "Contact"), ("Area/keep/skip.md", "Contact")]:
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\ntags: [{tag}]\n---\n# Note", encoding="utf-8")
    context = {"id": "contact:filtered", "scopes": ["Area"], "include": ["Area/keep"],
               "exclude": ["Area/keep/skip.md"], "tags": ["contact"]}
    signals = [_signal("orphan_note", "info", p.relative_to(tmp_path).as_posix())
               for p in tmp_path.rglob("*.md")]
    result = context_quality_queue([context], signals, source_scan=scan_notes(tmp_path))
    assert result["counts_by_bucket"] == {"maintained": 1, "unscoped": 3}
    assert result["items"][0]["signal"]["path"] == "Area/keep/good.md"
    assert result["membership_complete"]


def test_late_in_scope_occurrence_is_found_after_twenty_source_samples(tmp_path):
    for i in range(25):
        p = tmp_path / f"A/outside-{i:02}.md"
        p.parent.mkdir(exist_ok=True)
        p.write_text("[[Missing]]", encoding="utf-8")
    p = tmp_path / "Z/inside.md"
    p.parent.mkdir()
    p.write_text("---\ntags: [capture/idea]\n---\n[[Missing]]", encoding="utf-8")
    scan = scan_notes(tmp_path)
    doctor = diagnose_vault(tmp_path, source_scan=scan)
    signals = vault_quality_signals("vault", doctor, scan)
    broken = next(s for s in signals if s.kind == "broken_link")
    assert broken.evidence["sources_truncated"]
    assert "Z/inside.md" not in broken.evidence["sources"]
    result = context_quality_queue(
        [{"id": "idea:one", "scopes": ["Z"], "tags": ["capture/idea"]}], [broken],
        source_scan=scan, doctor_report=doctor,
    )
    item = result["items"][0]
    assert item["context_ids"] == ["idea:one"]
    assert item["context_source_counts"] == {"idea:one": 1}
    assert item["membership_basis"] == "full_occurrences"
    assert result["membership_complete"] and result["total"] == 1


def test_resolved_relative_occurrence_does_not_count_as_an_in_scope_problem(tmp_path):
    for path, text in [("Area/Target.md", "# Target"),
                       ("Area/Source.md", "[[./Target]]"),
                       ("Other/Source.md", "[[./Target]]")]:
        p = tmp_path / path
        p.parent.mkdir(exist_ok=True)
        p.write_text(text, encoding="utf-8")
    scan = scan_notes(tmp_path)
    doctor = diagnose_vault(tmp_path, source_scan=scan)
    signals = [s for s in vault_quality_signals("vault", doctor, scan) if s.kind == "broken_link"]
    result = context_quality_queue([{"id": "area:one", "scopes": ["Area"]}], signals,
                                   source_scan=scan, doctor_report=doctor)
    assert result["counts_by_bucket"] == {"maintained": 0, "unscoped": 1}
    assert result["membership_complete"]


def test_missing_tag_evidence_is_unknown_even_when_display_is_empty():
    signal = _signal("unreadable_note", "error", "Area/unreadable.md")
    result = context_quality_queue(
        [{"id": "idea:one", "scopes": ["Area"], "tags": ["capture/idea"]}], [signal],
        limit=0, source_scan=ScanReport((), (), ("Area/unreadable.md",)),
    )
    assert result["status"] == "incomplete" and not result["membership_complete"]
    assert result["total"] == result["truncated"] == 1 and not result["items"]
    visible = context_quality_queue(
        [{"id": "idea:one", "scopes": ["Area"], "tags": ["capture/idea"]}], [signal],
    )["items"][0]
    assert visible["unverified_context_ids"] == ["idea:one"]


def test_excluded_path_needs_no_tag_evidence():
    result = context_quality_queue(
        [{"id": "idea:one", "scopes": ["Area"], "exclude": ["Area/skip.md"],
          "tags": ["capture/idea"]}], [_signal("orphan_note", "info", "Area/skip.md")],
    )
    assert result["membership_complete"]
    assert result["items"][0]["context_ids"] == []


def test_repository_path_cannot_be_mistaken_for_a_vault_note():
    signal = QualitySignal(kind="missing_document", severity="warning", source_type="repository",
                           source_id="repo", path="Area/one.md", reason="missing", suggestion="review")
    result = context_quality_queue([{"id": "area:one", "scopes": ["Area"]}], [signal])
    assert result["items"][0]["context_ids"] == []
    assert result["membership_complete"]


def test_missing_full_occurrences_and_sample_truncation_are_not_a_false_clear():
    signal = QualitySignal(kind="broken_link", severity="warning", source_type="vault",
                           source_id="vault", path="Other/n.md", reason="missing", suggestion="review",
                           evidence={"target": "Missing", "sources": ["Other/n.md"],
                                     "sources_truncated": True})
    for doctor in (None, {}):
        result = context_quality_queue([{"id": "area:one", "scopes": ["Area"]}], [signal],
                                       doctor_report=doctor)
        assert not result["membership_complete"]
        assert result["status"] == "incomplete" and result["total"] == 1


def test_quality_cli_exposes_maintained_context_queue(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    note = root / "300_Resources" / "Contacts" / "Alice.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntags: [Contact/Person]\n---\n# Alice\nSee [[Missing contact]].\n", encoding="utf-8")
    (note.parent / "Ignore.md").write_text(
        "---\ntags: [Contact/Person]\n---\n[[Excluded missing]]", encoding="utf-8"
    )
    state = tmp_path / "state"

    def call(*args: str, expected: int = 0) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--state-root",
                str(state),
                *args,
                "--vault",
                "quality",
                "--json",
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert completed.returncode == expected, completed.stderr
        return json.loads(completed.stdout)

    subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--state-root",
            str(state),
            "attach",
            str(root),
            "--name",
            "quality",
            "--write-policy",
            "off",
            "--json",
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    definitions = tmp_path / "definitions.json"
    definitions.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "contact:alice",
                        "type": "contact",
                        "name": "Alice",
                        "purpose": "communication history",
                        "scopes": ["300_Resources/Contacts"],
                        "exclude": ["300_Resources/Contacts/Ignore.md"],
                        "tags": ["Contact"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--state-root",
            str(state),
            "contexts",
            "register",
            "--definitions",
            str(definitions),
            "--vault",
            "quality",
            "--json",
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )

    result = call("quality", expected=1)
    queue = result["context_queue"]
    assert queue["status"] == "findings"
    assert queue["counts_by_bucket"]["maintained"] >= 1
    assert queue["items"][0]["context_ids"] == ["contact:alice"]
    assert queue["repair_performed"] is False
    excluded = next(row for row in queue["items"]
                    if row["signal"]["path"].endswith("Ignore.md"))
    assert excluded["context_ids"] == []
    assert queue["membership_complete"] and queue["schema_version"] == 2
