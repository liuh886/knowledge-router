"""The documented brief budget covers the complete emitted result."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from text_budget import budget_brief, estimate_tokens


def test_full_json_budget_includes_sources_citations_and_duplicate_markdown():
    sources = [
        {
            "path": f"source-{i}.md",
            "title": "中文主题",
            "line_start": 1,
            "line_end": 2,
            "source_sha256": "a" * 64,
            "stale": False,
            "excerpt": "资料事实" * 100,
        }
        for i in range(8)
    ]
    brief = {"topic": "主题", "evidence_gap": False, "sources": sources, "markdown": "unbounded"}
    result = budget_brief(brief, 1500)
    assert 0 < len(result["sources"]) < len(sources)
    assert result["sources_omitted_by_budget"] == len(sources) - len(result["sources"])
    cost = estimate_tokens(json.dumps(result, ensure_ascii=False, indent=2))
    assert cost == result["estimated_tokens"] <= 1500
    assert all(source in sources for source in result["sources"])
    assert "not a tokenizer" in result["budget_basis"]


def test_brief_rejects_budget_that_cannot_fit_metadata():
    brief = {"topic": "x" * 10000, "sources": [], "markdown": ""}
    with pytest.raises(ValueError, match="metadata"):
        budget_brief(brief, 128)
    with pytest.raises(ValueError, match="128"):
        budget_brief(brief, -1)


def test_readme_budget_command_runs_with_bounded_cited_evidence(tmp_path):
    vault, state = tmp_path / "Vault", tmp_path / "State"
    vault.mkdir()
    (vault / "note.md").write_text("# ESG30\nESG30 evidence for the project.\n", encoding="utf-8")
    command = [sys.executable, str(SCRIPTS / "knowledge_router.py"), "--state-root", str(state)]
    attached = subprocess.run(
        [*command, "attach", str(vault), "--json"], capture_output=True, text=True
    )
    assert attached.returncode == 0, attached.stderr
    completed = subprocess.run(
        [*command, "brief", "ESG30", "--budget-tokens", "1500", "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["sources"]
    assert estimate_tokens(completed.stdout) <= 1500
