"""Representative effect and latency acceptance for the local Context manager."""

# ruff: noqa: E402

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from context_brief import brief_context
from context_store import register_contexts
from evaluate import run_benchmark
from registry import VaultRegistry
from vault_service import active_index_path, sync_vault

CLI = SCRIPT_DIR / "knowledge_router.py"


def _quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


@pytest.mark.parametrize("warm_repetitions", [3])
def test_representative_effects_and_process_latency(tmp_path: Path, warm_repetitions: int) -> None:
    """Check source-backed quality across Context kinds without imposing a machine SLA."""
    root = tmp_path / "vault"
    _write(
        root / "100_Logs" / "daily" / "2026-09-11.md",
        "# Daily\nDaily router milestone: triage completed and next review is Friday.\n",
    )
    _write(
        root / "300_Resources" / "Contacts" / "Alice.md",
        "# Alice\nAlice renewal call: confirm the contact window and send the summary.\n",
    )
    _write(
        root / "100_Project" / "Router" / "PROGRESS.md",
        "# Router\nRouter project milestone: Context brief now cites current state.\n",
    )
    _write(
        root / "400_Archives" / "Legacy decision.md",
        "# Legacy decision\nOld-state archive decision from 2023; reference only.\n",
    )

    state = tmp_path / "state"
    registry = VaultRegistry(state)
    config = registry.attach(root, name="effects", write_policy="off")
    sync_vault(registry, config)
    config = registry.resolve("effects")
    register_contexts(
        registry,
        config,
        {
            "schema_version": 1,
            "contexts": [
                {
                    "id": "daily:main",
                    "type": "daily",
                    "name": "Daily",
                    "purpose": "daily progress",
                    "scopes": ["100_Logs/daily"],
                },
                {
                    "id": "contact:alice",
                    "type": "contact",
                    "name": "Alice",
                    "purpose": "communication history",
                    "scopes": ["300_Resources/Contacts/Alice.md"],
                },
                {
                    "id": "project:router",
                    "type": "project",
                    "name": "Router",
                    "purpose": "project progress",
                    "scopes": ["100_Project/Router"],
                },
                {
                    "id": "archive:legacy",
                    "type": "archive",
                    "name": "Legacy decision",
                    "purpose": "historical reference",
                    "scopes": ["400_Archives/Legacy decision.md"],
                },
            ],
        },
    )

    fixture = tmp_path / "effects.json"
    fixture.write_text(
        json.dumps(
            {
                "version": 1,
                "limit": 3,
                "thresholds": {
                    "recall_at_k": 1.0,
                    "mrr": 1.0,
                    "abstention_precision": 1.0,
                },
                "cases": [
                    {
                        "id": "daily",
                        "query": "daily router milestone",
                        "difficulty": "context",
                        "expected_paths": ["100_Logs/daily/2026-09-11.md"],
                    },
                    {
                        "id": "contact",
                        "query": "Alice renewal call",
                        "difficulty": "entity",
                        "expected_paths": ["300_Resources/Contacts/Alice.md"],
                    },
                    {
                        "id": "project",
                        "query": "Router project milestone",
                        "difficulty": "project",
                        "expected_paths": ["100_Project/Router/PROGRESS.md"],
                    },
                    {
                        "id": "old-state",
                        "query": "Old-state archive decision 2023",
                        "difficulty": "old_state",
                        "expected_paths": ["400_Archives/Legacy decision.md"],
                    },
                    {
                        "id": "abstain",
                        "query": "不存在的紫色量子香蕉协议",
                        "difficulty": "abstention",
                        "abstain": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    benchmark = run_benchmark(
        fixture,
        active_index_path(registry, config),
        vault_root=root,
        repetitions=warm_repetitions,
    )
    assert benchmark["passed"]
    assert benchmark["metrics"]["recall_at_k"] == 1.0
    assert benchmark["metrics"]["mrr"] == 1.0
    assert benchmark["metrics"]["abstention_precision"] == 1.0
    assert benchmark["metrics"]["source_coverage"] == 1.0
    assert benchmark["metrics"]["latency_samples"] == 5 * warm_repetitions
    assert set(benchmark["metrics"]["by_difficulty"]) >= {
        "context",
        "entity",
        "project",
        "old_state",
    }

    for selector, query in (
        ("daily:main", "router milestone"),
        ("contact:alice", "renewal call"),
        ("project:router", "project milestone"),
        ("archive:legacy", "old-state archive"),
    ):
        brief = brief_context(registry, config, selector, query=query, limit=2)
        assert not brief["evidence_gap"]
        assert brief["evidence"]
        assert all(item["line_start"] >= 1 and not item["stale"] for item in brief["evidence"])

    def process_call(*args: str) -> tuple[float, dict]:
        started = time.perf_counter()
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--state-root",
                str(state),
                *args,
                "--vault",
                config.id,
                "--json",
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        elapsed = (time.perf_counter() - started) * 1000
        assert completed.returncode == 0, completed.stderr
        return elapsed, json.loads(completed.stdout)

    process_search_ms: list[float] = []
    process_brief_ms: list[float] = []
    for _ in range(2):
        elapsed, search = process_call("search", "Alice renewal call")
        process_search_ms.append(elapsed)
        assert search and search[0]["path"] == "300_Resources/Contacts/Alice.md"
        elapsed, context_brief = process_call(
            "contexts", "brief", "project:router", "--query", "project milestone"
        )
        process_brief_ms.append(elapsed)
        assert context_brief["evidence"] and not context_brief["evidence_gap"]

    # Keep these measurements visible to pytest diagnostics while avoiding a brittle SLA.
    assert statistics.median(process_search_ms) > 0
    assert _quantile(process_search_ms, 0.95) > 0
    assert statistics.median(process_brief_ms) > 0
    assert _quantile(process_brief_ms, 0.95) > 0
