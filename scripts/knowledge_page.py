#!/usr/bin/env python3
"""Versioned knowledge pages with a claim-level evidence manifest.

A *page* is a durable, revisable statement of "what we currently think" about one research topic
or one project, built on top of a Context. It is a derived artifact: it lives in an external
knowledge area beside the Vault, exactly like a compiled card, and never rewrites human prose.
Only reviewed proposals are ever written into the Vault, through the existing capture broker.

The one rule this module enforces is the difference between a *sourced* claim and an *inference*:

- a sourced claim must cite at least one real note, and the citation is recorded with the source
  checksum at publish time;
- an inference is allowed to exist, but it must be labelled as one, so it can never be presented
  as if a document said it;
- a claim citing a note that does not exist is refused outright, because that is indistinguishable
  from a fabricated citation.

Draft syntax (one list item per line, in the section that owns it):

    ## 当前认识
    - C1. 结论文字 [src: 200_Area/211_Research/Concept of CCS.md#L12-L18]
    - C2. 另一个结论 [src: a.md#L3-L4, b.md#L9-L9] [conflict]
    - C3. 尚未有出处的判断 [inference]

    ## 未决问题
    - Q1. 还需要确认什么 [open]

Every item must carry a marker. An unmarked item is rejected rather than silently guessed at.
"""

from __future__ import annotations

from text_budget import estimate_tokens

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from context_manager import extract_tags, note_in_context
from context_compile import (
    definition_fingerprint,
    definitions_fingerprint,
    select_context,
)
from context_store import load_context_version
from fts_indexer import scan_notes
from registry import (
    FileLock,
    VaultConfig,
    VaultRegistry,
    atomic_json_write,
    atomic_text_write,
)

_PAGE_REGISTRY = "knowledge_pages.json"
_PAGE_SCHEMA = 1

CLAIM_SOURCED = "sourced"
CLAIM_INFERENCE = "inference"
CLAIM_CONFLICT = "conflict"

_SECTION_CLAIMS = "当前认识"
_SECTION_QUESTIONS = "未决问题"

_ITEM = re.compile(r"^-\s+(?P<id>[A-Za-z]\w*)\.\s*(?P<body>.*)$")
_SRC = re.compile(r"\[src:\s*(?P<refs>[^\]]+)\]")
_CITATION = re.compile(r"^(?P<path>[^#]+?)(?:#L(?P<start>\d+)(?:-L?(?P<end>\d+))?)?$")
_MARKER_EXCLUSIVE = re.compile(r"\[(inference|open)\]")


def pages_root(config: VaultConfig, out_root: str | None = None) -> Path:
    """Resolve the external knowledge area, which must stay outside the Vault."""
    if out_root:
        root = Path(out_root).expanduser().absolute()
    else:
        parent = config.root_path.parent
        root = parent / f"{config.root_path.name}_knowledge"
    root = root.resolve()
    vault_root = config.root_path.resolve()
    if root == vault_root or vault_root in root.parents:
        raise ValueError("Knowledge area must stay outside the Vault")
    if any((ancestor / ".obsidian").is_dir() for ancestor in (root, *root.parents)):
        raise ValueError("Knowledge area must not be inside an Obsidian Vault")
    if not root.parent.is_dir():
        raise ValueError(f"Knowledge area parent must already exist: {root.parent}")
    return root


@dataclass(frozen=True)
class Citation:
    path: str
    line_start: int
    line_end: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    kind: str
    citations: tuple[Citation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.claim_id,
            "text": self.text,
            "kind": self.kind,
            "citations": [item.to_dict() for item in self.citations],
        }


@dataclass(frozen=True)
class PageDraft:
    claims: tuple[Claim, ...]
    open_questions: tuple[str, ...]
    title: str = ""
    body: str = ""


class DraftError(ValueError):
    """A draft that cannot be published as written."""


def _clean(value: str) -> str:
    return " ".join(value.split())


def _parse_citation_token(token: str) -> tuple[str, int | None, int | None]:
    match = _CITATION.match(token.strip().replace("\\", "/"))
    if not match:
        raise DraftError(f"Unreadable citation: {token!r}")
    path = match.group("path").strip().strip("/")
    if not path:
        raise DraftError(f"Citation without a path: {token!r}")
    start = match.group("start")
    end = match.group("end")
    return path, int(start) if start else None, int(end or start) if start else None


def parse_draft(text: str, *, title: str = "") -> PageDraft:
    """Parse a draft, refusing anything that cannot be judged unambiguously."""
    claims: list[Claim] = []
    questions: list[str] = []
    section: str | None = None
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            section = heading if heading in (_SECTION_CLAIMS, _SECTION_QUESTIONS) else None
            continue
        if not line.startswith("- "):
            continue
        if section is None:
            raise DraftError(
                f"Item outside '{_SECTION_CLAIMS}' or '{_SECTION_QUESTIONS}': {line!r}"
            )
        item = _ITEM.match(line)
        if not item:
            raise DraftError(f"Item must start with '- <id>. ': {line!r}")
        claim_id = item.group("id")
        body = item.group("body").strip()
        if claim_id in seen:
            raise DraftError(f"Duplicate item id: {claim_id}")
        seen.add(claim_id)

        exclusive = _MARKER_EXCLUSIVE.search(body)
        sources = _SRC.search(body)
        if exclusive and sources:
            raise DraftError(f"{claim_id} cannot be both an {exclusive.group(1)} and sourced")
        if not exclusive and not sources:
            if "[conflict]" in body:
                raise DraftError(
                    f"{claim_id} is marked [conflict] but cites nothing; a conflict needs at "
                    "least two sources"
                )
            raise DraftError(
                f"{claim_id} carries no marker; add [src: path#Lx-Ly], [inference] or [open]"
            )

        text_only = _clean(_SRC.sub("", _MARKER_EXCLUSIVE.sub("", body))).strip()
        text_only = _clean(re.sub(r"\[conflict\]", "", text_only)).strip()
        if not text_only:
            raise DraftError(f"{claim_id} has no text")

        if section == _SECTION_QUESTIONS:
            if sources:
                raise DraftError(f"{claim_id} is an open question and cannot carry citations")
            questions.append(text_only)
            continue

        if exclusive:
            kind = CLAIM_INFERENCE if exclusive.group(1) == "inference" else CLAIM_CONFLICT
            if kind == CLAIM_CONFLICT:
                raise DraftError(f"{claim_id} is marked [open] inside '{_SECTION_CLAIMS}'")
            claims.append(Claim(claim_id=claim_id, text=text_only, kind=kind))
            continue

        refs = [_parse_citation_token(token) for token in sources.group("refs").split(",")]
        conflict = "[conflict]" in body
        if conflict and len(refs) < 2:
            raise DraftError(f"{claim_id} is a conflict claim and needs at least two sources")
        claims.append(
            Claim(
                claim_id=claim_id,
                text=text_only,
                kind=CLAIM_CONFLICT if conflict else CLAIM_SOURCED,
                # Paths are pre-validated; line ranges and checksums are filled in at publish.
                citations=tuple(
                    Citation(path=path, line_start=start or 0, line_end=end or 0, sha256="")
                    for path, start, end in refs
                ),
            )
        )
    if not claims:
        raise DraftError(f"A page needs at least one claim under '{_SECTION_CLAIMS}'")
    return PageDraft(claims=tuple(claims), open_questions=tuple(questions), title=title, body=text)


def _resolve_citations(scan, claims: tuple[Claim, ...]) -> tuple[Claim, ...]:
    """Attach checksums and line bounds, refusing any citation that cannot be verified."""
    by_path = {record.path: record for record in scan.records}
    line_counts = {record.path: len(record.content.splitlines()) or 1 for record in scan.records}
    resolved: list[Claim] = []
    for claim in claims:
        citations: list[Citation] = []
        for citation in claim.citations:
            record = by_path.get(citation.path)
            if record is None:
                raise DraftError(
                    f"{claim.claim_id} cites a source that does not exist: {citation.path}"
                )
            total = line_counts[citation.path]
            start = citation.line_start or 1
            end = citation.line_end or total
            if start < 1 or end < start or start > total or end > total:
                raise DraftError(
                    f"{claim.claim_id} cites {citation.path} lines {start}-{end}, "
                    f"but the note has {total} lines"
                )
            citations.append(
                Citation(
                    path=citation.path,
                    line_start=start,
                    line_end=end,
                    sha256=record.checksum,
                )
            )
        resolved.append(
            Claim(
                claim_id=claim.claim_id,
                text=claim.text,
                kind=claim.kind,
                citations=tuple(citations),
            )
        )
    return tuple(resolved)


def _registry_path(registry: VaultRegistry, config: VaultConfig) -> Path:
    return registry.vault_state_dir(config) / _PAGE_REGISTRY


REGISTRY_ABSENT = "absent"
REGISTRY_VALID = "valid"
REGISTRY_UNREADABLE = "unreadable"
REGISTRY_INVALID = "invalid"
_PAGE_REQUIRED = ("page_id", "context_id", "path", "claims")


def load_page_registry(registry: VaultRegistry, config: VaultConfig) -> dict[str, Any]:
    """Read the page registry, reporting *why* it is empty instead of just being empty."""
    path = _registry_path(registry, config)
    if not path.is_file():
        return {"state": REGISTRY_ABSENT, "pages": [], "path": str(path), "reason": None}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        return {
            "state": REGISTRY_UNREADABLE,
            "pages": [],
            "path": str(path),
            "reason": f"{type(error).__name__}: {error}",
        }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        return {
            "state": REGISTRY_INVALID,
            "pages": [],
            "path": str(path),
            "reason": f"invalid JSON: {error.msg} at line {error.lineno}",
        }
    if not isinstance(data, dict) or data.get("vault_id") != config.id:
        return {
            "state": REGISTRY_INVALID,
            "pages": [],
            "path": str(path),
            "reason": "unexpected shape or a different vault_id",
        }
    pages = data.get("pages")
    if not isinstance(pages, list) or any(
        not isinstance(row, dict) or any(key not in row for key in _PAGE_REQUIRED) for row in pages
    ):
        return {
            "state": REGISTRY_INVALID,
            "pages": [],
            "path": str(path),
            "reason": "a page record is missing a required field",
        }
    return {"state": REGISTRY_VALID, "pages": list(pages), "path": str(path), "reason": None}


def load_pages(registry: VaultRegistry, config: VaultConfig) -> list[dict[str, Any]]:
    """Read the page registry; a damaged registry never invents pages."""
    return load_page_registry(registry, config)["pages"]


def load_page(registry: VaultRegistry, config: VaultConfig, page_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in load_pages(registry, config) if row.get("page_id") == page_id), None
    )


def find_page(registry: VaultRegistry, config: VaultConfig, selector: str) -> dict[str, Any] | None:
    """Find a page by id, by file stem, or by the Context it belongs to."""
    folded = selector.casefold()
    pages = load_pages(registry, config)
    for row in pages:
        if str(row.get("page_id", "")).casefold() == folded:
            return row
    for row in pages:
        if str(row.get("name", "")).removesuffix(".md").casefold() == folded:
            return row
    for row in pages:
        if str(row.get("context_id", "")).casefold() == folded:
            return row
    return None


def _record_page(registry: VaultRegistry, config: VaultConfig, record: dict[str, Any]) -> None:
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / ".knowledge-pages.lock", pinned_parent=guard):
            existing = load_page_registry(registry, config)
            if existing["state"] not in {REGISTRY_ABSENT, REGISTRY_VALID}:
                raise RuntimeError(
                    "Refusing to rewrite an unreadable page registry "
                    f"({existing['state']}: {existing['reason']}); fix it first"
                )
            pages = [row for row in existing["pages"] if row.get("page_id") != record["page_id"]]
            pages.append(record)
            pages.sort(key=lambda row: str(row.get("page_id")))
            atomic_json_write(
                _registry_path(registry, config),
                {"schema_version": _PAGE_SCHEMA, "vault_id": config.id, "pages": pages},
            )


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\-. ]+", "", value, flags=re.UNICODE).strip()
    return re.sub(r"[\s]+", "-", cleaned) or "page"


def render_page(record: dict[str, Any]) -> str:
    """Render the human-readable page, including its evidence manifest."""
    lines = [
        "---",
        f"page_id: {json.dumps(record['page_id'], ensure_ascii=False)}",
        f"page_type: {record['page_type']}",
        f"context_id: {json.dumps(record['context_id'], ensure_ascii=False)}",
        f"version: {record['version']}",
        f"context_revision: {record['context_revision']}",
        f"published_at: {record['published_at']}",
        f"source_count: {record['source_count']}",
        f"claim_count: {len(record['claims'])}",
        "---",
        "",
        f"# {record['title']}",
        "",
        f"> 由 Knowledge Router 发布 · 版本 {record['version']} · "
        f"Context `{record['context_id']}` 修订 {record['context_revision']} · "
        f"范围内来源 {record['source_count']} 条",
        "",
        f"## {_SECTION_CLAIMS}",
        "",
    ]
    for claim in record["claims"]:
        marks = {
            CLAIM_SOURCED: "引用",
            CLAIM_INFERENCE: "推断",
            CLAIM_CONFLICT: "冲突",
        }[claim["kind"]]
        lines.append(f"- **{claim['id']}**（{marks}）{claim['text']}")
        for citation in claim["citations"]:
            lines.append(
                f"  - 出处：`{citation['path']}:{citation['line_start']}-{citation['line_end']}`"
            )
    lines.extend(["", f"## {_SECTION_QUESTIONS}", ""])
    for question in record["open_questions"]:
        lines.append(f"- {question}")
    if not record["open_questions"]:
        lines.append("- （暂无）")

    lines.extend(["", "## 依据 manifest", ""])
    lines.append(f"来源快照 {record['source_count']} 条，摘要 sha256：`{record['sources_sha256']}`")
    lines.append("")
    lines.append("| 主张 | 类型 | 出处 |")
    lines.append("|---|---|---|")
    for claim in record["claims"]:
        refs = (
            ", ".join(
                f"`{item['path']}#L{item['line_start']}-L{item['line_end']}`"
                for item in claim["citations"]
            )
            or "—"
        )
        lines.append(f"| {claim['id']} | {claim['kind']} | {refs} |")
    lines.append("")
    return "\n".join(lines)


def publish_page(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    draft_text: str,
    *,
    name: str | None = None,
    out_root: str | None = None,
    overwrite: bool = False,
    title: str | None = None,
    page_type: str | None = None,
) -> dict[str, Any]:
    """Validate a draft against the Vault and publish it into the external knowledge area."""
    version = load_context_version(registry, config)
    if version is None:
        raise ValueError("No registered Context definitions; run contexts register first")
    # Checked before anything is written: a page file that cannot be registered would be an
    # untracked artifact, and the next publish would not know its version.
    registry_state = load_page_registry(registry, config)
    if registry_state["state"] not in {REGISTRY_ABSENT, REGISTRY_VALID}:
        raise RuntimeError(
            "Refusing to publish while the page registry is unreadable "
            f"({registry_state['state']}: {registry_state['reason']}); fix it first"
        )
    contexts = version["definitions"]["contexts"]
    context = select_context(contexts, selector)
    draft = parse_draft(draft_text, title=title or context["name"])
    scan = scan_notes(config.root_path)
    claims = _resolve_citations(scan, draft.claims)

    root = pages_root(config, out_root)
    filename = name or f"{_slug(context['name'])}.md"
    if not filename.endswith(".md"):
        filename = f"{filename}.md"
    if filename != PurePosixPath(filename).name or filename.startswith("."):
        raise ValueError("Page name must be a single Markdown file name")
    target = (root / filename).resolve()
    if root not in target.parents:
        raise ValueError("Page name must stay inside the knowledge area")
    existed = target.exists()
    if existed and not overwrite:
        raise FileExistsError(f"Page already exists: {target} (use --overwrite to replace)")

    previous = load_page(registry, config, context["id"])
    sources = {
        record.path: record.checksum
        for record in scan.records
        if note_in_context(record.path, extract_tags(record.content), context)
    }
    record = {
        "page_id": context["id"],
        "page_type": page_type or (context.get("type") or "research_topic"),
        "title": title or context["name"],
        "context_id": context["id"],
        "context_revision": version["revision"],
        "context_fingerprint": definition_fingerprint(context),
        "definitions_fingerprint": definitions_fingerprint(contexts),
        "version": int(previous["version"]) + 1 if previous else 1,
        "published_at": datetime.now(UTC).isoformat(),
        "name": filename,
        "path": str(target),
        "knowledge_area": str(root),
        "sources_sha256": hashlib.sha256(
            json.dumps(sources, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "source_count": len(sources),
        "sources": sources,
        "claims": [claim.to_dict() for claim in claims],
        "open_questions": list(draft.open_questions),
    }
    atomic_text_write(target, render_page(record))
    _record_page(registry, config, record)
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "published",
        "page_id": record["page_id"],
        "version": record["version"],
        "path": str(target),
        "replaced": existed,
        "claim_count": len(record["claims"]),
        "claims_by_kind": _count_kinds(record["claims"]),
        "citation_count": sum(len(item["citations"]) for item in record["claims"]),
        "source_count": record["source_count"],
        "open_question_count": len(record["open_questions"]),
        "vault_writes": 0,
        "meaning": (
            "Published as a derived artifact beside the Vault. Every sourced claim cites a real "
            "note and its checksum; inferences are labelled. Nothing in the Vault was modified."
        ),
    }


def _count_kinds(claims: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        counts[str(claim["kind"])] = counts.get(str(claim["kind"]), 0) + 1
    return counts


def page_status(
    registry: VaultRegistry,
    config: VaultConfig,
    page: dict[str, Any],
    *,
    scan=None,
    contexts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare a published page with the Vault it was built from."""
    report = scan or scan_notes(config.root_path)
    all_checksums = {record.path: record.checksum for record in report.records}
    if contexts is None:
        version = load_context_version(registry, config)
        contexts = version["definitions"]["contexts"] if version else []
    context = next((row for row in contexts if row.get("id") == page.get("context_id")), None)
    recorded = page.get("sources") or {}
    if context is None:
        # The Context was retired: nothing observes it any more.
        current: dict[str, str] = {}
        scope_changed = True
    else:
        tags_by_path = {record.path: extract_tags(record.content) for record in report.records}
        current = {
            record.path: record.checksum
            for record in report.records
            if note_in_context(record.path, tags_by_path.get(record.path, ()), context)
        }
        # Only this page's own Context matters: adding an unrelated Context must not make it
        # look re-scoped.
        scope_changed = page.get("context_fingerprint") != definition_fingerprint(context)
    # Compare against the *scoped* set, never the whole Vault: an unrelated note elsewhere is not
    # a change to this page's sources.
    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    changed = sorted(
        path for path in set(recorded) & set(current) if recorded[path] != current[path]
    )
    cited = {
        citation["path"]
        for claim in page.get("claims", [])
        for citation in claim.get("citations", [])
    }
    citation_drift = sorted(
        path
        for path in cited
        if path not in all_checksums or all_checksums.get(path) != _cited_checksum(page, path)
    )
    stored = Path(str(page.get("path") or ""))
    exists = stored.is_file()
    return {
        "page_id": page.get("page_id"),
        "version": page.get("version"),
        "path": str(stored),
        "file_exists": exists,
        "source_count": page.get("source_count"),
        "added_sources": added[:10],
        "removed_sources": removed[:10],
        "changed_sources": changed[:10],
        "drift_count": len(added) + len(removed) + len(changed),
        "citation_drift": citation_drift[:10],
        "citation_drift_count": len(citation_drift),
        # A re-scoped Context means the page covers a different source set, so it is stale even
        # if every recorded file is byte-identical.
        "stale": bool(not exists or added or removed or changed or citation_drift or scope_changed),
        "scope_changed": scope_changed,
    }


def _cited_checksum(page: dict[str, Any], path: str) -> str:
    for claim in page.get("claims", []):
        for citation in claim.get("citations", []):
            if citation.get("path") == path:
                return str(citation.get("sha256") or "")
    return ""


def context_pack(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    *,
    budget_tokens: int = 1200,
    scan=None,
) -> dict[str, Any]:
    """Assemble a budgeted, source-grounded pack an agent can continue working from.

    The pack states what is currently believed, which claims are sourced and which are inference,
    what is unresolved, and how much it costs — so the caller is never told "the context" while
    being handed an unbounded dump.
    """
    if budget_tokens < 64:
        raise ValueError("budget_tokens must be at least 64")
    version = load_context_version(registry, config)
    if version is None:
        raise ValueError("No registered Context definitions; run contexts register first")
    context = select_context(version["definitions"]["contexts"], selector)
    registry_state = load_page_registry(registry, config)
    if registry_state["state"] not in {REGISTRY_ABSENT, REGISTRY_VALID}:
        # "No page published" would be a false claim when the registry cannot be read.
        return {
            "schema_version": 1,
            "vault_id": config.id,
            "context_id": context["id"],
            "status": "registry_unreadable",
            "registry_state": registry_state["state"],
            "registry_reason": registry_state.get("reason"),
            "budget_tokens": budget_tokens,
            "used_tokens": 0,
            "items": [],
            "meaning": (
                "The page registry could not be read, so nothing can be said about whether a "
                "page exists. This is not a claim that nothing is known."
            ),
        }
    page = next(
        (row for row in registry_state["pages"] if row.get("page_id") == context["id"]), None
    )
    if page is None:
        return {
            "schema_version": 1,
            "vault_id": config.id,
            "context_id": context["id"],
            "status": "unpublished",
            "budget_tokens": budget_tokens,
            "used_tokens": 0,
            "items": [],
            "meaning": (
                "No knowledge page is published for this Context yet. This is not a claim that "
                "nothing is known; nothing has been consolidated."
            ),
        }

    status = page_status(registry, config, page, scan=scan)
    items: list[dict[str, Any]] = []
    used = 0
    omitted = 0
    for claim in page.get("claims", []):
        rendered = f"[{claim['id']}] {claim['text']}"
        cost = estimate_tokens(rendered)
        if used + cost > budget_tokens:
            omitted += 1
            continue
        items.append(
            {
                "id": claim["id"],
                "kind": claim["kind"],
                "text": claim["text"],
                "expandable": bool(claim.get("citations")),
                "citations": [
                    {
                        "path": item["path"],
                        "lines": f"L{item['line_start']}-L{item['line_end']}",
                    }
                    for item in claim.get("citations", [])
                ],
            }
        )
        used += cost
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "context_id": context["id"],
        "status": "stale" if status["stale"] else "current",
        "page_id": page["page_id"],
        "page_version": page["version"],
        "page_path": page["path"],
        "purpose": context.get("purpose"),
        "scope": context.get("scopes"),
        "budget_tokens": budget_tokens,
        "used_tokens": used,
        "budget_basis": "estimated (CJK chars as 1, others as 1/4); not a tokenizer",
        "claims_total": len(page.get("claims", [])),
        "claims_omitted_by_budget": omitted,
        "claims_by_kind": _count_kinds(page.get("claims", [])),
        "open_questions": page.get("open_questions", []),
        "coverage": {
            "source_count": page.get("source_count"),
            "drift_count": status["drift_count"],
            "citation_drift_count": status["citation_drift_count"],
            "context_revision": page.get("context_revision"),
        },
        "items": items,
        "meaning": (
            "A budgeted reading of one published page, with sourced claims distinguished from "
            "inference. Expand any item with citations to reach the underlying notes."
        ),
    }


def expand_claim(
    registry: VaultRegistry,
    config: VaultConfig,
    page_id: str,
    claim_id: str,
    *,
    depth: str = "brief",
    excerpt_lines: int = 12,
    scan=None,
) -> dict[str, Any]:
    """Expand one claim to its citations, re-reading the sources as they are now."""
    if depth not in {"brief", "full"}:
        raise ValueError("depth must be 'brief' or 'full'")
    page = find_page(registry, config, page_id)
    if page is None:
        raise FileNotFoundError(f"No published page matches: {page_id}")
    claim = next((row for row in page.get("claims", []) if str(row.get("id")) == claim_id), None)
    if claim is None:
        raise FileNotFoundError(f"Page {page['page_id']!r} has no claim {claim_id!r}")

    report = scan or scan_notes(config.root_path)
    by_path = {record.path: record for record in report.records}
    citations: list[dict[str, Any]] = []
    for item in claim.get("citations", []):
        record = by_path.get(item["path"])
        if record is None:
            citations.append(
                {
                    "path": item["path"],
                    "status": "missing",
                    "lines": f"L{item['line_start']}-L{item['line_end']}",
                    "excerpt": None,
                    "sha256": item["sha256"],
                }
            )
            continue
        lines = record.content.splitlines()
        start = max(1, int(item["line_start"]))
        end = min(len(lines) or 1, int(item["line_end"]))
        if depth == "brief":
            end = min(end, start + excerpt_lines - 1)
        citations.append(
            {
                "path": item["path"],
                "status": "changed" if record.checksum != item["sha256"] else "current",
                "lines": f"L{start}-L{end}",
                "excerpt": "\n".join(lines[start - 1 : end]),
                "sha256": record.checksum,
                "cited_sha256": item["sha256"],
            }
        )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "page_id": page["page_id"],
        "page_version": page["version"],
        "claim": {
            "id": claim["id"],
            "kind": claim["kind"],
            "text": claim["text"],
        },
        "depth": depth,
        "citations": citations,
        "meaning": (
            "The claim as published, next to its sources as they are now. A 'changed' or 'missing' "
            "citation means the page is stale, not that the claim is wrong."
        ),
    }


def format_page_pack(pack: dict[str, Any]) -> str:
    """Render a knowledge pack for a human reader."""
    if pack.get("status") == "unpublished":
        return f"# 上下文包：{pack['context_id']}\n尚未发布知识页；未整理不等于没有知识。"
    lines = [
        f"# 上下文包：{pack['context_id']}（版本 {pack['page_version']}）",
        f"状态 {pack['status']} · 预算 {pack['used_tokens']}/{pack['budget_tokens']} tokens"
        f"（{pack['budget_basis']}）",
        f"主张 {pack['claims_total']} 条（{pack['claims_by_kind']}）"
        f" · 范围内来源 {pack['coverage']['source_count']} 条"
        f" · 漂移 {pack['coverage']['drift_count']}",
    ]
    for item in pack["items"]:
        lines.append(f"- [{item['id']}/{item['kind']}] {item['text']}")
        for citation in item["citations"]:
            lines.append(f"    {citation['path']} {citation['lines']}")
    if pack["claims_omitted_by_budget"]:
        lines.append(f"… 因预算省略 {pack['claims_omitted_by_budget']} 条主张")
    if pack["open_questions"]:
        lines.append("未决问题：")
        for question in pack["open_questions"]:
            lines.append(f"- {question}")
    return "\n".join(lines)


def format_expand(result: dict[str, Any]) -> str:
    """Render one expanded claim."""
    claim = result["claim"]
    lines = [
        f"# {result['page_id']} · {claim['id']}（{claim['kind']}）",
        claim["text"],
    ]
    for citation in result["citations"]:
        lines.append("")
        lines.append(f"## {citation['path']} {citation['lines']} · {citation['status']}")
        if citation["excerpt"]:
            lines.append(citation["excerpt"])
    return "\n".join(lines)
