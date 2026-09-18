"""Explainable Context quality prioritization never hides unscoped findings."""

# ruff: noqa: E402
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from evidence import QualitySignal
from quality_analyzer import context_quality_queue

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


def test_quality_cli_exposes_maintained_context_queue(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    note = root / "300_Resources" / "Contacts" / "Alice.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Alice\nSee [[Missing contact]].\n", encoding="utf-8")
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
                        "scopes": ["300_Resources/Contacts/Alice.md"],
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
