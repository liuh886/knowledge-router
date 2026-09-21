#!/usr/bin/env python3
"""Read-only external relationship graph over resolved Obsidian wikilinks.

The graph is a rebuildable external projection stored beside the immutable index.
It records only what the Vault already states through ``[[wikilinks]]``; it never
infers a relationship from co-occurrence and never writes inside the Vault.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from context_store import load_context_version
from fts_indexer import ScanReport, scan_notes
from registry import FileLock, VaultConfig, VaultRegistry, atomic_json_write
from vault_doctor import _aliases, _links

_FILENAME = "link_graph.json"
_MOC_TOKENS = frozenset({"moc", "index", "索引", "总目录", "總目錄"})


def _is_moc(path: str) -> bool:
    stem = PurePosixPath(path).stem.casefold()
    tokens = [token for token in re.split(r"[^0-9a-z\u4e00-\u9fff]+", stem) if token]
    return any(token in _MOC_TOKENS for token in tokens) or stem.endswith("索引")


def build_link_graph(
    scan: ScanReport, *, vault_root: Path | None = None
) -> dict[str, Any]:
    """Resolve wikilinks into a deterministic, external relationship projection.

    When ``vault_root`` is supplied, non-Markdown attachments are included in link
    resolution so that ``![[image.png]]`` style references are not miscounted as
    broken. Edges are only emitted between notes; asset references are reported
    separately through ``asset_links``.
    """
    by_path: dict[str, str] = {}
    by_stem: dict[str, set[str]] = defaultdict(set)
    by_alias: dict[str, set[str]] = defaultdict(set)
    asset_paths: set[str] = set()
    for record in scan.records:
        by_path[record.path.removesuffix(".md").casefold()] = record.path
        by_stem[Path(record.path).stem.casefold()].add(record.path)
        for alias in _aliases(record.content):
            by_alias[alias.casefold()].add(record.path)
    if vault_root is not None:
        root = vault_root.resolve()
        for asset in root.rglob("*"):
            if not asset.is_file() or asset.suffix.casefold() == ".md":
                continue
            try:
                relative = asset.relative_to(root)
                asset.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            if any(part.startswith(".") for part in relative.parts):
                continue
            relative_path = relative.as_posix()
            asset_paths.add(relative_path)
            by_path[relative_path.casefold()] = relative_path
            by_stem[asset.name.casefold()].add(relative_path)
            by_stem[asset.stem.casefold()].add(relative_path)

    edges: set[tuple[str, str]] = set()
    unresolved = 0
    ambiguous = 0
    asset_links = 0
    for record in scan.records:
        parent = PurePosixPath(record.path).parent
        for link in _links(record.path, record.content):
            folded = link.target.casefold()
            local = PurePosixPath(parent, link.target).as_posix().casefold()
            candidates: set[str] = set()
            if local in by_path:
                candidates.add(by_path[local])
            if folded in by_path:
                candidates.add(by_path[folded])
            candidates.update(
                by_stem.get(PurePosixPath(link.target).name.casefold(), set())
            )
            candidates.update(by_alias.get(folded, set()))
            if len(candidates) == 1:
                destination = next(iter(candidates))
                if destination in asset_paths:
                    asset_links += 1
                elif destination != record.path:
                    edges.add((record.path, destination))
            elif not candidates:
                unresolved += 1
            else:
                ambiguous += 1

    degrees: dict[str, list[int]] = {record.path: [0, 0] for record in scan.records}
    for source, destination in edges:
        degrees[source][1] += 1
        degrees[destination][0] += 1
    manifest = "\n".join(f"{r.path}:{r.checksum}" for r in scan.records).encode()
    return {
        "schema_version": 1,
        "source_generation": hashlib.sha256(manifest).hexdigest(),
        "note_count": len(scan.records),
        "edge_count": len(edges),
        "unresolved_links": unresolved,
        "ambiguous_links": ambiguous,
        "asset_links": asset_links,
        "mocs": sorted(path for path in degrees if _is_moc(path)),
        "degrees": degrees,
        "edges": sorted(edges),
    }


def _graph_path(registry: VaultRegistry, config: VaultConfig) -> Path:
    return registry.vault_state_dir(config) / _FILENAME


def persist_link_graph(
    registry: VaultRegistry, config: VaultConfig, graph: dict[str, Any]
) -> None:
    """Write the rebuildable graph projection to external Vault state only."""
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / ".link-graph.lock", pinned_parent=guard):
            atomic_json_write(state / _FILENAME, {"vault_id": config.id, **graph})


def load_link_graph(
    registry: VaultRegistry, config: VaultConfig
) -> dict[str, Any] | None:
    path = _graph_path(registry, config)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("vault_id") != config.id:
        return None
    return data


def _select_context(
    contexts: list[dict[str, Any]], selector: str
) -> dict[str, Any]:
    exact = [row for row in contexts if row["id"] == selector]
    if len(exact) == 1:
        return exact[0]
    folded = selector.casefold()
    matches = [
        row
        for row in contexts
        if row["id"].casefold() == folded
        or row["name"].casefold() == folded
        or folded in {alias.casefold() for alias in row.get("aliases", [])}
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeyError(f"Unknown Context: {selector}")
    raise ValueError(
        "Ambiguous Context selector: "
        + ", ".join(sorted(row["id"] for row in matches))
    )


def _in_scope(path: str, scopes: list[str]) -> bool:
    return any(
        scope == "." or path == scope or path.startswith(scope.rstrip("/") + "/")
        for scope in scopes
    )


def _empty_result(config: VaultConfig) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "unregistered",
        "context": None,
        "graph": None,
        "scope_note_count": 0,
        "internal_edge_count": 0,
        "related_contexts": [],
        "related_context_count": 0,
        "anchors": [],
        "hub_notes": [],
        "bridge_notes": [],
        "truncated": False,
        "vault_writes": 0,
    }


def context_links(
    registry: VaultRegistry,
    config: VaultConfig,
    selector: str,
    *,
    limit: int = 12,
    rebuild: bool = False,
) -> dict[str, Any]:
    """Return one Context's graph neighbourhood without changing Vault sources."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Context links limit must be between 1 and 100")
    version = load_context_version(registry, config)
    if version is None:
        return _empty_result(config)
    contexts = version["definitions"]["contexts"]
    selected = _select_context(contexts, selector)
    graph = None if rebuild else load_link_graph(registry, config)
    if graph is None:
        graph = build_link_graph(
            scan_notes(config.root_path), vault_root=config.root_path
        )
        persist_link_graph(registry, config, graph)

    degrees: dict[str, list[int]] = graph["degrees"]
    members = {
        row["id"]: {path for path in degrees if _in_scope(path, row["scopes"])}
        for row in contexts
    }
    own = members[selected["id"]]
    edges = [(edge[0], edge[1]) for edge in graph["edges"]]
    outgoing: dict[str, list[str]] = defaultdict(list)
    for source, destination in edges:
        outgoing[source].append(destination)

    internal = sum(1 for source, target in edges if source in own and target in own)
    others = set().union(
        *(members[row["id"]] for row in contexts if row["id"] != selected["id"])
    )
    related: list[dict[str, Any]] = []
    for row in contexts:
        if row["id"] == selected["id"]:
            continue
        other = members[row["id"]]
        outbound = sum(
            1 for source, target in edges if source in own and target in other and target not in own
        )
        inbound = sum(
            1 for source, target in edges if source in other and target in own and source not in own
        )
        if outbound or inbound:
            related.append(
                {
                    "context_id": row["id"],
                    "type": row["type"],
                    "name": row["name"],
                    "outbound_links": outbound,
                    "inbound_links": inbound,
                    "cross_links": outbound + inbound,
                }
            )
    related.sort(key=lambda row: (-row["cross_links"], row["context_id"]))

    anchors: list[dict[str, Any]] = []
    for path in graph["mocs"]:
        targets = outgoing.get(path, [])
        in_scope = sum(1 for target in targets if target in own)
        if path in own or in_scope:
            anchors.append(
                {
                    "path": path,
                    "out_links": len(targets),
                    "in_scope_targets": in_scope,
                    "external_targets": len(targets) - in_scope,
                }
            )
    anchors.sort(
        key=lambda row: (-row["in_scope_targets"], -row["out_links"], row["path"])
    )

    hub_notes = sorted(
        (
            {
                "path": path,
                "in_degree": degrees[path][0],
                "out_degree": degrees[path][1],
            }
            for path in own
        ),
        key=lambda row: (-(row["in_degree"] + row["out_degree"]), row["path"]),
    )

    bridge_notes: list[dict[str, Any]] = []
    for path in own:
        crossing = sum(
            1
            for target in outgoing.get(path, [])
            if target in others and target not in own
        )
        if crossing:
            bridge_notes.append({"path": path, "outbound_cross_links": crossing})
    bridge_notes.sort(key=lambda row: (-row["outbound_cross_links"], row["path"]))

    definitions_stale = bool(
        version.get("source_generation")
        and graph.get("source_generation")
        and version["source_generation"] != graph["source_generation"]
    )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "status": "linked",
        "context": {
            key: selected.get(key)
            for key in ("id", "type", "name", "purpose", "scopes", "aliases")
        },
        "graph": {
            "source_generation": graph.get("source_generation"),
            "note_count": graph.get("note_count"),
            "edge_count": graph.get("edge_count"),
            "unresolved_links": graph.get("unresolved_links"),
            "ambiguous_links": graph.get("ambiguous_links"),
            "asset_links": graph.get("asset_links"),
            "moc_count": len(graph.get("mocs", [])),
            "definitions_stale": definitions_stale,
        },
        "scope_note_count": len(own),
        "internal_edge_count": internal,
        "related_contexts": related[:limit],
        "related_context_count": len(related),
        "anchors": anchors[:limit],
        "hub_notes": hub_notes[:limit],
        "bridge_notes": bridge_notes[:limit],
        "truncated": any(
            len(rows) > limit for rows in (related, anchors, hub_notes, bridge_notes)
        ),
        "vault_writes": 0,
    }


def format_context_links(result: dict[str, Any]) -> str:
    """Render a concise human view of one Context's graph neighbourhood."""
    if result["status"] != "linked":
        return "No registered Context definitions; run `contexts register` first."
    context = result["context"]
    graph = result["graph"]
    lines = [
        f"# {context['name']} ({context['id']})",
        f"范围 {len(context['scopes'])} 个 · 库内笔记 {result['scope_note_count']} · "
        f"内部链接 {result['internal_edge_count']}",
        f"关系图 {graph['note_count']} 笔记 / {graph['edge_count']} 链接 / "
        f"{graph['moc_count']} 个 MOC"
        + ("  ⚠ 定义基线已过期，建议重新 register" if graph["definitions_stale"] else ""),
    ]
    if result["anchors"]:
        lines += ["", "## 主题锚点 (MOC)"]
        lines += [
            f"- {row['path']} · 指向本区 {row['in_scope_targets']} · 外链 {row['external_targets']}"
            for row in result["anchors"]
        ]
    if result["related_contexts"]:
        lines += ["", "## 关联 Context"]
        lines += [
            f"- {row['name']} ({row['context_id']}) · 交叉 {row['cross_links']} "
            f"(出 {row['outbound_links']} / 入 {row['inbound_links']})"
            for row in result["related_contexts"]
        ]
    if result["hub_notes"]:
        lines += ["", "## 枢纽笔记"]
        lines += [
            f"- {row['path']} · 入 {row['in_degree']} / 出 {row['out_degree']}"
            for row in result["hub_notes"]
        ]
    if result["bridge_notes"]:
        lines += ["", "## 跨区桥接"]
        lines += [
            f"- {row['path']} · 跨区出链 {row['outbound_cross_links']}"
            for row in result["bridge_notes"]
        ]
    return "\n".join(lines)
