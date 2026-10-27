#!/usr/bin/env python3
"""Measure process-inclusive live Context brief latency.

This is a repeatable measurement tool, not a hard-coded product SLA.  A caller
supplies a local budget appropriate for the machine and Vault size; the report
keeps sample count, p50/p95 and every failed invocation visible.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def measure(
    *,
    state_root: Path,
    vault: str,
    selector: str,
    queries: list[str],
    repetitions: int = 3,
    budget_ms: float | None = None,
    cli: Path | None = None,
) -> dict[str, Any]:
    if repetitions < 1 or repetitions > 100:
        raise ValueError("repetitions must be between 1 and 100")
    if not queries:
        raise ValueError("at least one query is required")
    if budget_ms is not None and budget_ms <= 0:
        raise ValueError("budget_ms must be positive")
    entrypoint = (cli or Path(__file__).with_name("knowledge_router.py")).resolve()
    samples: list[float] = []
    failures: list[dict[str, Any]] = []
    for _ in range(repetitions):
        for query in queries:
            started = time.perf_counter()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(entrypoint),
                    "contexts",
                    "brief",
                    selector,
                    "--query",
                    query,
                    "--vault",
                    vault,
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env={**os.environ, "KNOWLEDGE_ROUTER_STATE_ROOT": str(state_root.resolve())},
            )
            elapsed = (time.perf_counter() - started) * 1000
            if completed.returncode:
                failures.append(
                    {
                        "query": query,
                        "returncode": completed.returncode,
                        "stderr": completed.stderr[-500:],
                    }
                )
                continue
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                failures.append({"query": query, "error": f"invalid JSON: {exc}"})
                continue
            if not isinstance(payload, dict):
                failures.append({"query": query, "error": "brief result is not an object"})
                continue
            samples.append(round(elapsed, 3))
    p50 = _quantile(samples, 0.5) if samples else None
    p95 = _quantile(samples, 0.95) if samples else None
    passed = None if budget_ms is None else bool(p95 is not None and p95 <= budget_ms)
    return {
        "schema_version": 1,
        "vault": vault,
        "selector": selector,
        "queries": queries,
        "repetitions": repetitions,
        "samples": len(samples),
        "latencies_ms": samples,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "budget_ms": budget_ms,
        "passed": passed,
        "failures": failures,
        "meaning": "Process-inclusive live Context brief measurement; budget is caller-supplied and machine/Vault specific, not a universal SLA.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--budget-ms", type=float)
    args = parser.parse_args()
    try:
        result = measure(
            state_root=args.state_root,
            vault=args.vault,
            selector=args.selector,
            queries=args.query,
            repetitions=args.repetitions,
            budget_ms=args.budget_ms,
        )
    except (OSError, ValueError) as exc:
        print(f"Context latency error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] is not False and not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

