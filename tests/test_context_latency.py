"""Repeatable process-inclusive Context latency measurement."""

# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from context_latency import measure
from context_store import register_contexts
from registry import VaultRegistry
from vault_service import sync_vault


def test_process_latency_report_has_explicit_budget_and_samples(tmp_path: Path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "daily.md").write_text(
        "# Daily\nRouter delivery budget measurement.\n", encoding="utf-8"
    )
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="latency", write_policy="off")
    sync_vault(registry, config)
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
                    "purpose": "latency test",
                    "scopes": ["."],
                }
            ],
        },
    )
    result = measure(
        state_root=registry.state_root,
        vault=config.id,
        selector="daily:main",
        queries=["Router delivery"],
        repetitions=2,
        budget_ms=10000,
    )
    assert result["samples"] == 2
    assert result["latency_p50_ms"] > 0
    assert result["latency_p95_ms"] > 0
    assert result["budget_ms"] == 10000
    assert result["passed"] is True
    assert result["failures"] == []

