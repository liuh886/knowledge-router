#!/usr/bin/env python3
"""Evaluate source-backed retrieval quality and warm-query latency."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fts_indexer import search_index


def _ratio(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def run_benchmark(
    fixture_path: Path,
    index_path: Path,
    *,
    vault_root: Path | None = None,
    repetitions: int = 1,
    latency_target_ms: float = 50.0,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = list(fixture["cases"])
    if not cases or not any(case.get("abstain") for case in cases):
        raise ValueError("fixture must contain positive and abstention cases")
    limit = int(fixture.get("limit", 5))
    excerpt_chars = int(fixture.get("excerpt_chars", 800))
    positive_count = positive_hits = abstention_count = abstention_hits = 0
    reciprocal_rank = 0.0
    evidence: list[dict[str, Any]] = []
    difficulty: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    source_fields = (
        "path",
        "line_start",
        "line_end",
        "source_sha256",
        "index_generation",
        "stale",
    )
    source_checks: list[bool] = []

    for case in cases:
        results = search_index(
            index_path,
            str(case["query"]),
            limit=limit,
            excerpt_chars=excerpt_chars,
            vault_root=vault_root,
        )
        paths = [str(item["path"]) for item in results]
        source_checks.extend(
            all(field in item for field in source_fields)
            and bool(item["vault_id"] or vault_root is not None)
            and len(str(item["source_sha256"])) == 64
            and bool(item["index_generation"])
            and int(item["line_start"]) >= 1
            and int(item["line_end"]) >= int(item["line_start"])
            for item in results
        )
        rank = 0
        if case.get("abstain"):
            abstention_count += 1
            abstention_hits += not paths
        else:
            positive_count += 1
            expected = set(case["expected_paths"])
            rank = next(
                (number for number, path in enumerate(paths, 1) if path in expected), 0
            )
            hit = rank > 0
            positive_hits += hit
            reciprocal_rank += 1 / rank if rank else 0.0
            difficulty[str(case.get("difficulty", "unknown"))].append(
                (hit, 1 / rank if rank else 0.0)
            )
        evidence.append({"id": case["id"], "rank": rank, "paths": paths})

    for case in cases:
        search_index(
            index_path, str(case["query"]), limit=limit, excerpt_chars=excerpt_chars
        )
    latencies: list[float] = []
    per_case_latencies: dict[str, list[float]] = defaultdict(list)
    for _ in range(max(1, repetitions)):
        for case in cases:
            started = time.perf_counter()
            search_index(
                index_path, str(case["query"]), limit=limit, excerpt_chars=excerpt_chars
            )
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)
            per_case_latencies[str(case["id"])].append(elapsed)
    ordered = sorted(latencies)
    p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
    metrics: dict[str, Any] = {
        "recall_at_k": _ratio(positive_hits, positive_count),
        "mrr": _ratio(reciprocal_rank, positive_count),
        "abstention_precision": _ratio(abstention_hits, abstention_count),
        "source_coverage": _ratio(sum(source_checks), len(source_checks)),
        "latency_samples": len(latencies),
        "latency_p50_ms": statistics.median(ordered),
        "latency_p95_ms": p95,
        "by_difficulty": {
            key: {
                "recall_at_k": _ratio(sum(hit for hit, _ in values), len(values)),
                "mrr": _ratio(sum(rank for _, rank in values), len(values)),
                "cases": len(values),
            }
            for key, values in sorted(difficulty.items())
        },
    }
    thresholds = dict(fixture.get("thresholds", {}))
    checks = {
        "recall_at_k": metrics["recall_at_k"]
        >= float(thresholds.get("recall_at_k", 0.95)),
        "mrr": metrics["mrr"] >= float(thresholds.get("mrr", 0.0)),
        "abstention_precision": metrics["abstention_precision"]
        >= float(thresholds.get("abstention_precision", 1.0)),
        "source_coverage": metrics["source_coverage"] >= 1.0,
        "latency_p95_ms": p95 < latency_target_ms,
    }
    for key, threshold in dict(thresholds.get("difficulty_recall_at_k", {})).items():
        checks[f"difficulty_recall_at_k.{key}"] = float(
            metrics["by_difficulty"].get(key, {}).get("recall_at_k", 0.0)
        ) >= float(threshold)
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "evidence": [
            {
                **item,
                "latency_p50_ms": statistics.median(
                    per_case_latencies[str(item["id"])]
                ),
                "latency_max_ms": max(per_case_latencies[str(item["id"])]),
            }
            for item in evidence
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--latency-target-ms", type=float, default=50.0)
    args = parser.parse_args()
    try:
        result = run_benchmark(
            args.fixture,
            args.index,
            vault_root=args.vault,
            repetitions=args.repetitions,
            latency_target_ms=args.latency_target_ms,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Benchmark error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
