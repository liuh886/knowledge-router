"""Source-bound Context discovery and multi-context maintenance previews.

This module has no mutation capability. Inferred identities are candidates, not
permission to maintain a note. Explicit definitions can describe arbitrary types
and multiple existing scopes without changing the Vault's folder conventions.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from fts_indexer import ScanReport

TYPE_RULES = {
    "daily": ("daily", "dailies", "journal", "diary", "日记", "日报"),
    "project": ("project", "projects", "项目"),
    "contact": ("contact", "contacts", "people", "联系人"),
    "research": ("research", "研究"),
    "trip": ("trip", "trips", "travel", "旅行"),
    "job": ("job", "jobs", "jobber", "求职"),
    "area": ("area", "areas", "领域"),
    "resource": ("resource", "resources", "资料"),
    "archive": ("archive", "archives", "归档"),
    "inbox": ("inbox", "收件箱"),
    "template": ("template", "templates", "模板"),
}

PURPOSES = {
    "daily": "记录按日期组织的事件、进展和回顾",
    "project": "维护目标、进展、决策和下一步",
    "contact": "维护人物交流历史、承诺和跟进",
    "research": "维护问题、证据、结论与引用",
    "trip": "维护行程、参与人和准备事项",
    "job": "维护职位、申请阶段和反馈",
    "area": "维护长期责任与标准",
    "resource": "维护可复用材料及适用条件",
    "archive": "保留已结束内容的历史证据",
    "inbox": "接收尚未明确归属的输入",
    "template": "维护可复用写作结构",
}


def _name(value: str) -> str:
    return re.sub(r"^\d+[_ -]+", "", value).strip()


def _contains(text: str, alias: str) -> bool:
    if len(alias.strip()) < 2:
        return False
    # English names must not match inside unrelated words (Ann / annual).
    pattern = re.escape(alias.casefold())
    if re.fullmatch(r"[a-zA-Z0-9 _-]+", alias):
        pattern = rf"(?<![a-z0-9]){pattern}(?![a-z0-9])"
    return re.search(pattern, text.casefold()) is not None


def _under(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(scope.rstrip("/") + "/")


def _scope(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Context scopes must be nonempty Vault-relative paths")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part == ".." or part.startswith(".") for part in path.parts)
    ):
        raise ValueError(f"Unsafe Context scope: {value}")
    return path.as_posix()


def _scope_list(value: Any, field: str) -> tuple[str, ...]:
    if value in (None, []):
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(
            f"Context {field} must be a list of nonempty Vault-relative paths"
        )
    try:
        return tuple(_scope(item) for item in value)
    except ValueError as exc:
        raise ValueError(f"Context {field}: {exc}") from None


def _tag_list(value: Any) -> tuple[str, ...]:
    if value in (None, []):
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError("Context tags must be a list of nonempty strings")
    folded = [item.strip().lstrip("#").casefold() for item in value]
    if any(not item for item in folded):
        raise ValueError("Context tags must not be empty after normalization")
    if len(set(folded)) != len(folded):
        raise ValueError("Context tags must not contain duplicates")
    return tuple(sorted(set(folded)))


def _clean_tag(item: str) -> str:
    return item.strip().strip("'\"").removeprefix("-").strip().lstrip("#").casefold()


def extract_tags(content: str) -> tuple[str, ...]:
    """Read frontmatter ``tags``/``tag`` values, preserving nested Obsidian tags.

    Accepts the loose forms Obsidian tolerates: inline lists, comma scalars and
    block lists, including ``tags: - TagName`` written on the key line.
    """
    frontmatter = re.match(r"\A---\s*\r?\n(.*?)\r?\n---", content, re.DOTALL)
    if not frontmatter:
        return ()
    lines = frontmatter.group(1).splitlines()
    values: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^tags?\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline.startswith("[") and inline.endswith("]"):
            values.extend(inline[1:-1].split(","))
        elif inline:
            values.extend(inline.split(","))
        else:
            for nested in lines[index + 1 :]:
                if nested and not nested[0].isspace():
                    break
                item = nested.strip()
                if item.startswith("-"):
                    values.append(item.removeprefix("-").strip())
        break
    cleaned = {_clean_tag(item) for item in values}
    return tuple(sorted(item for item in cleaned if item))


def _filters(entry: Any) -> tuple[Any, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    if isinstance(entry, ContextDefinition):
        return entry.scopes, entry.include, entry.exclude, entry.tags
    return (
        entry["scopes"],
        tuple(entry.get("include") or ()),
        tuple(entry.get("exclude") or ()),
        tuple(entry.get("tags") or ()),
    )


def passes_filters(path: str, tags: tuple[str, ...], entry: Any) -> bool:
    """Apply include/exclude/tag filters without checking scope membership."""
    _, include, exclude, required = _filters(entry)
    if include and not any(_under(path, scope) for scope in include):
        return False
    if exclude and any(_under(path, scope) for scope in exclude):
        return False
    if not required:
        return True
    folded = {tag.casefold() for tag in tags}
    return any(
        tag == wanted or tag.startswith(wanted + "/")
        for wanted in required
        for tag in folded
    )


def note_in_context(path: str, tags: tuple[str, ...], entry: Any) -> bool:
    """Return whether a note is a filtered member of the given Context."""
    scopes, _, _, _ = _filters(entry)
    return any(_under(path, scope) for scope in scopes) and passes_filters(
        path, tags, entry
    )


@dataclass(frozen=True)
class ContextDefinition:
    id: str
    type: str
    name: str
    purpose: str
    scopes: tuple[str, ...]
    aliases: tuple[str, ...]
    origin: str
    kind: str
    maintenance: dict[str, Any] | None = None
    project_ids: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


def maintenance_policy(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "enabled",
        "review_interval_days",
        "check_source_changes",
    }:
        raise ValueError("Invalid Context maintenance policy")
    result = {
        "enabled": value.get("enabled", True),
        "check_source_changes": value.get("check_source_changes", True),
    }
    if any(type(result[k]) is not bool for k in result):
        raise ValueError("Maintenance switches must be booleans")
    interval = value.get("review_interval_days")
    if interval is not None:
        if type(interval) is not int or not 1 <= interval <= 3650:
            raise ValueError(
                "review_interval_days must be an integer between 1 and 3650"
            )
        result["review_interval_days"] = interval
    return result


def discover_contexts(
    scan: ScanReport, *, vault_id: str, definitions: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a disposable functional map; fail on incomplete source scans."""
    if scan.skipped_decode or scan.skipped_io:
        raise ValueError("Context discovery requires a complete readable source scan")
    records = sorted(scan.records, key=lambda note: note.path)
    folders = sorted(
        {
            parent.as_posix()
            for note in records
            for parent in PurePosixPath(note.path).parents
        }
        - {"."}
    )
    contexts: dict[str, ContextDefinition] = {}

    def add(kind: str, scope: str, name: str, *, instance: bool = False) -> None:
        identity = hashlib.sha256(f"{vault_id}:{kind}:{scope}".encode()).hexdigest()[
            :16
        ]
        item = ContextDefinition(
            f"{kind}:{identity}",
            kind,
            name,
            PURPOSES[kind],
            (scope,),
            tuple(sorted({name, _name(name)})),
            "inferred",
            "instance" if instance else "collection",
        )
        contexts[item.id] = item

    for folder in folders:
        name = _name(PurePosixPath(folder).name).casefold()
        for kind, aliases in TYPE_RULES.items():
            if name not in aliases:
                continue
            add(kind, folder, PurePosixPath(folder).name)
            if kind in {"project", "area", "research", "trip", "job"}:
                for child in folders:
                    if PurePosixPath(child).parent.as_posix() == folder:
                        add(
                            kind, child, _name(PurePosixPath(child).name), instance=True
                        )
            if kind in {"contact", "daily"}:
                for note in records:
                    path = PurePosixPath(note.path)
                    if not _under(note.path, folder):
                        continue
                    if kind == "daily" and not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", path.stem
                    ):
                        continue
                    if kind == "contact" and (
                        path.stem.casefold() in {"readme", "index", "moc", "pipeline"}
                        or path.name.casefold().endswith(".workflow.md")
                    ):
                        continue
                    add(kind, note.path, path.stem, instance=True)

    signals: list[dict[str, Any]] = []
    if definitions is not None:
        if not isinstance(definitions, dict) or definitions.get("schema_version") != 1:
            raise ValueError("Context definitions require schema_version=1")
        entries = definitions.get("contexts")
        if not isinstance(entries, list):
            raise ValueError("Context definitions require a contexts list")
        explicit_ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Each Context definition must be an object")
            for key in ("id", "type", "name", "purpose"):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    raise ValueError(f"Context definition requires {key}")
            if entry["id"] in explicit_ids or entry["id"] in contexts:
                raise ValueError(f"Duplicate Context id: {entry['id']}")
            explicit_ids.add(entry["id"])
            scopes = entry.get("scopes")
            aliases = entry.get("aliases", [])
            if not isinstance(scopes, list) or not scopes:
                raise ValueError("Context definition requires nonempty scopes")
            if not isinstance(aliases, list) or not all(
                isinstance(a, str) and a for a in aliases
            ):
                raise ValueError("Context aliases must be strings")
            kind = entry.get("kind", "instance")
            if kind not in {"instance", "collection"}:
                raise ValueError("Context kind must be instance or collection")
            project_ids = entry.get("project_ids", [])
            if not isinstance(project_ids, list) or not all(
                isinstance(project_id, str) and project_id.strip()
                for project_id in project_ids
            ):
                raise ValueError("Context project_ids must be nonempty strings")
            if len(set(project_ids)) != len(project_ids):
                raise ValueError("Context project_ids must not contain duplicates")
            item = ContextDefinition(
                entry["id"],
                entry["type"],
                entry["name"],
                entry["purpose"],
                tuple(_scope(s) for s in scopes),
                tuple([entry["name"], *aliases]),
                "declared",
                kind,
                maintenance_policy(entry.get("maintenance")),
                tuple(project_ids),
                _scope_list(entry.get("include"), "include"),
                _scope_list(entry.get("exclude"), "exclude"),
                _tag_list(entry.get("tags")),
            )
            # Declared bindings supersede an inferred binding for the same type/scope.
            contexts = {
                key: old
                for key, old in contexts.items()
                if not (
                    old.origin == "inferred"
                    and old.type == item.type
                    and set(old.scopes).issubset(item.scopes)
                )
            }
            contexts[item.id] = item

    rows = []
    for item in sorted(contexts.values(), key=lambda c: (c.type, c.id)):
        sources = [
            note
            for note in records
            if note_in_context(note.path, extract_tags(note.content), item)
        ]
        for scope in item.scopes:
            if not any(_under(note.path, scope) for note in records):
                signals.append(
                    {
                        "code": "scope_without_evidence",
                        "context_id": item.id,
                        "path": scope,
                        "reason": "No readable Markdown source; scope may be missing or empty",
                    }
                )
        rows.append({**asdict(item), "source_count": len(sources)})
    manifest = "\n".join(f"{n.path}:{n.checksum}" for n in records)
    covered = {
        n.path
        for n in records
        if any(note_in_context(n.path, extract_tags(n.content), c) for c in contexts.values())
    }
    return {
        "schema_version": 1,
        "vault_id": vault_id,
        "source_generation": hashlib.sha256(manifest.encode()).hexdigest(),
        "status": "discovered",
        "contexts": rows,
        "signals": signals,
        "notes_scanned": len(records),
        "unclassified_note_count": len(records) - len(covered),
        "counts_by_type": dict(
            sorted(Counter(c.type for c in contexts.values()).items())
        ),
        "inference_notice": "Inferred Contexts are candidates; names do not establish authority or active state",
    }


def summarize_contexts(catalog: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("Context limit must be between 1 and 100")
    return {
        **catalog,
        "context_count": len(catalog["contexts"]),
        "contexts": catalog["contexts"][:limit],
        "signal_count": len(catalog["signals"]),
        "signals": catalog["signals"][:limit],
        "truncated": len(catalog["contexts"]) > limit
        or len(catalog["signals"]) > limit,
    }


def plan_contexts(
    text: str, catalog: dict[str, Any], scan: ScanReport, *, limit: int = 20
) -> dict[str, Any]:
    """Explain all named Context impacts without selecting or authorizing write targets."""
    if not text.strip():
        raise ValueError("Context input must not be empty")
    if not 1 <= limit <= 100:
        raise ValueError("Context limit must be between 1 and 100")
    impacts = []
    temporal = any(
        _contains(text, word) for word in ("今天", "今日", "today", "日志", "日记")
    )
    for context in catalog["contexts"]:
        aliases = [alias for alias in context["aliases"] if _contains(text, alias)]
        daily = (
            temporal and context["type"] == "daily" and context["kind"] == "collection"
        )
        if not aliases and not daily:
            continue
        evidence = []
        sources = [
            note
            for note in scan.records
            if note_in_context(note.path, extract_tags(note.content), context)
        ]
        for note in sorted(sources, key=lambda n: n.path)[:3]:
            lines = note.content.splitlines()
            evidence.append(
                {
                    "vault_id": catalog["vault_id"],
                    "path": note.path,
                    "line_start": 1,
                    "line_end": min(len(lines), 8) or 1,
                    "source_sha256": note.checksum,
                    "excerpt": "\n".join(lines[:8])[:600],
                    "evidence_role": "scope_sample",
                    "freshness": "read_during_this_scan",
                }
            )
        impacts.append(
            {
                "context_id": context["id"],
                "type": context["type"],
                "name": context["name"],
                "scopes": context["scopes"],
                "origin": context["origin"],
                "matched_aliases": aliases,
                "reason": "named_context" if aliases else "daily_event_signal",
                "suggested_operation": "link_event" if sources else "resolve_scope",
                "evidence": evidence,
            }
        )
    alias_owners: dict[str, list[str]] = {}
    for impact in impacts:
        for alias in impact["matched_aliases"]:
            alias_owners.setdefault(alias.casefold(), []).append(impact["context_id"])
    ambiguities = [
        {"alias": alias, "candidates": sorted(set(owners))}
        for alias, owners in sorted(alias_owners.items())
        if len(set(owners)) > 1
    ]
    followup = any(
        _contains(text, word) for word in ("跟进", "提醒", "follow up", "remind")
    )
    return {
        "schema_version": 1,
        "vault_id": catalog["vault_id"],
        "status": "preview",
        "source_generation": catalog["source_generation"],
        "event": {"text": text},
        "impact_count": len(impacts),
        "impacts": impacts[:limit],
        "ambiguities": ambiguities[:limit],
        "ambiguity_count": len(ambiguities),
        "truncated": len(impacts) > limit or len(ambiguities) > limit,
        "evidence_gap": not any(impact["evidence"] for impact in impacts),
        "followup": {"detected": followup, "scheduled": False, "due_at": None},
        "workflow": ["received", "contexts_resolved", "impact_plan_prepared"],
        "execution": {
            "supported": False,
            "writes_performed": 0,
            "reason": "Maintenance preview only; canonical event and write plan are not yet bound",
        },
    }
