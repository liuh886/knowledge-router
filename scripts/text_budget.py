"""Explicit, dependency-free estimates for bounded evidence output."""

from __future__ import annotations

import json
from typing import Any


def estimate_tokens(text: str) -> int:
    """CJK characters count as one, other characters as 1/4; this is not a tokenizer."""
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff" or "\u3000" <= char <= "\u303f")
    return cjk + (len(text) - cjk + 3) // 4


def budget_brief(brief: dict[str, Any], budget_tokens: int) -> dict[str, Any]:
    """Account for the complete formatted JSON, including citations and Markdown."""
    if budget_tokens < 128:
        raise ValueError("Brief budget must be at least 128 estimated tokens")
    sources = brief["sources"]
    result = {key: value for key, value in brief.items() if key not in {"sources", "markdown"}}
    result.update(
        sources=[],
        markdown="",
        budget_tokens=budget_tokens,
        estimated_tokens=0,
        sources_omitted_by_budget=len(sources),
        budget_basis="complete JSON estimate: CJK=1, other characters=1/4; not a tokenizer",
    )

    def measure() -> int:
        # Include the accounting fields themselves in their own serialization cost.
        for _ in range(4):
            cost = estimate_tokens(json.dumps(result, ensure_ascii=False, indent=2))
            if cost == result["estimated_tokens"]:
                return cost
            result["estimated_tokens"] = cost
        return estimate_tokens(json.dumps(result, ensure_ascii=False, indent=2))

    if measure() > budget_tokens:
        raise ValueError("Budget cannot fit the brief metadata; increase --budget-tokens")
    for source in sources:
        prior_markdown = result["markdown"]
        result["sources"].append(source)
        result["sources_omitted_by_budget"] -= 1
        citation = f"{source.get('project_id', '')}/{source['path']}".lstrip("/")
        result["markdown"] += (
            f"### {source.get('title', citation)}\n{source['excerpt']}\n\n"
            f"Source: {citation}:{source['line_start']}-{source['line_end']} "
            f"sha256 {source.get('source_sha256', source.get('content_hash', ''))}"
            + (" [STALE]" if source.get("stale") else "")
            + "\n\n"
        )
        if measure() > budget_tokens:
            result["sources"].pop()
            result["sources_omitted_by_budget"] += 1
            result["markdown"] = prior_markdown
    if not result["sources"]:
        result["markdown"] = (
            "No source excerpt fits this budget."
            if sources
            else "Evidence gap: no relevant source found."
        )
    if measure() > budget_tokens:
        raise ValueError("Budget cannot fit the brief metadata; increase --budget-tokens")
    return result
