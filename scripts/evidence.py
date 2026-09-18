#!/usr/bin/env python3
"""Canonical evidence and quality records shared by Vault and repository adapters."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One immutable, resolvable excerpt from a human-owned source."""

    source_type: str
    source_id: str
    title: str
    path: str
    line_start: int
    line_end: int
    content_hash: str
    revision: str
    stale: bool
    excerpt: str
    score: float = 0.0
    project_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_type not in {"vault", "repository"}:
            raise ValueError("source_type must be 'vault' or 'repository'")
        if not self.source_id or not self.path or not self.content_hash:
            raise ValueError("Evidence requires source_id, path, and content_hash")
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("Evidence line range is invalid")

    @classmethod
    def from_vault_hit(cls, hit: dict[str, Any]) -> EvidenceRecord:
        return cls(
            source_type="vault",
            source_id=str(hit.get("vault_id", "")),
            title=str(hit.get("title", "")),
            path=str(hit["path"]),
            line_start=int(hit["line_start"]),
            line_end=int(hit["line_end"]),
            content_hash=str(hit["source_sha256"]),
            revision=str(hit.get("index_generation", "")),
            stale=bool(hit.get("stale", False)),
            excerpt=str(hit.get("excerpt", "")),
            score=float(hit.get("score", 0.0)),
        )

    def to_dict(self, *, compatibility: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not self.metadata:
            result.pop("metadata")
        if self.project_id is None:
            result.pop("project_id")
        if compatibility and self.source_type == "vault":
            result.update(
                {
                    "vault_id": self.source_id,
                    "source_sha256": self.content_hash,
                    "index_generation": self.revision,
                }
            )
        return result


@dataclass(frozen=True, slots=True)
class QualitySignal:
    """One explainable observation; never a hidden aggregate quality score."""

    kind: str
    severity: str
    source_type: str
    source_id: str
    reason: str
    suggestion: str
    path: str | None = None
    project_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "error"}:
            raise ValueError("severity must be info, warning, or error")
        if self.source_type not in {"vault", "repository"}:
            raise ValueError("source_type must be 'vault' or 'repository'")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.path is None:
            result.pop("path")
        if self.project_id is None:
            result.pop("project_id")
        if not self.evidence:
            result.pop("evidence")
        return result


def merge_evidence(
    groups: Iterable[Iterable[EvidenceRecord]], *, limit: int
) -> list[EvidenceRecord]:
    """Merge adapters at the evidence seam with deterministic global ranking."""
    if limit < 1:
        raise ValueError("limit must be positive")
    records = [record for group in groups for record in group]
    records.sort(
        key=lambda item: (
            item.stale,
            -item.score,
            item.source_type,
            item.source_id,
            item.path.casefold(),
            item.line_start,
        )
    )
    return records[:limit]
