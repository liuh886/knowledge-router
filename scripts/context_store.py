"""Versioned external Context definitions and source-binding drift checks."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from context_manager import discover_contexts, extract_tags, passes_filters
from fts_indexer import ScanReport, scan_notes
from registry import FileLock, VaultConfig, VaultRegistry, atomic_json_write

_FILENAME = "context_definitions.json"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _read(config: VaultConfig, guard: Any) -> dict[str, Any]:
    try:
        descriptor = guard.open_existing(writable=False, name=_FILENAME)
    except FileNotFoundError:
        return {"schema_version": 1, "vault_id": config.id, "versions": []}
    try:
        chunks = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid Context store")
    checksum = data.pop("checksum", None)
    if (
        checksum != _digest(data)
        or data.get("vault_id") != config.id
        or data.get("schema_version") != 1
    ):
        raise ValueError("Context store checksum or Vault binding mismatch")
    versions = data.get("versions")
    if not isinstance(versions, list) or any(
        not isinstance(v, dict) or v.get("revision") != i
        for i, v in enumerate(versions, 1)
    ):
        raise ValueError("Invalid Context revision history")
    return data


def load_context_version(
    registry: VaultRegistry, config: VaultConfig
) -> dict[str, Any] | None:
    with registry.guard_vault_state(config) as guard:
        versions = _read(config, guard)["versions"]
    return versions[-1] if versions else None


def _bindings(definitions: dict[str, Any], scan: ScanReport) -> dict[str, Any]:
    return {
        entry["id"]: {
            scope: {
                n.path: n.checksum
                for n in scan.records
                if (
                    scope == "."
                    or n.path == scope
                    or n.path.startswith(scope.rstrip("/") + "/")
                )
                and passes_filters(n.path, extract_tags(n.content), entry)
            }
            for scope in entry["scopes"]
        }
        for entry in definitions["contexts"]
    }


def register_contexts(
    registry: VaultRegistry,
    config: VaultConfig,
    definitions: dict[str, Any],
    *,
    expected_revision: int = 0,
) -> dict[str, Any]:
    """Atomically append a reviewed declaration version, preserving prior versions."""
    if not isinstance(definitions, dict):
        raise ValueError("Context definitions must be a JSON object")
    scan = scan_notes(config.root_path)
    catalog = discover_contexts(scan, vault_id=config.id, definitions=definitions)
    # Persist normalized declarations; inferred guesses are never automatically saved.
    normalized = {
        "schema_version": 1,
        "contexts": [
            {
                key: row[key]
                for key in (
                    "id",
                    "type",
                    "name",
                    "purpose",
                    "scopes",
                    "aliases",
                    "kind",
                    "maintenance",
                    "project_ids",
                    "include",
                    "exclude",
                    "tags",
                )
            }
            for row in catalog["contexts"]
            if row["origin"] == "declared"
        ],
    }
    if catalog["signals"]:
        raise ValueError(
            "Cannot register Context scopes without readable Markdown evidence"
        )
    requested_projects = {
        project_id
        for row in catalog["contexts"]
        for project_id in row.get("project_ids", [])
    }
    if requested_projects:
        from project_adapter import list_projects

        try:
            known_projects = {
                str(item["project_id"]) for item in list_projects(registry, config)
            }
        except FileNotFoundError as exc:
            raise ValueError(
                "Reviewed Context project_ids require an attached project catalog"
            ) from exc
        unknown = sorted(requested_projects - known_projects)
        if unknown:
            raise ValueError(
                "Unknown reviewed project id(s): " + ", ".join(unknown)
            )
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / ".contexts.lock", pinned_parent=guard):
            store = _read(config, guard)
            current_revision = len(store["versions"])
            if expected_revision != current_revision:
                raise ValueError(
                    f"Context revision conflict: expected {expected_revision}, current {current_revision}"
                )
            revision = {
                "revision": current_revision + 1,
                "recorded_at": datetime.now(UTC).isoformat(),
                "definitions": normalized,
                "bindings": _bindings(normalized, scan),
                "source_generation": catalog["source_generation"],
            }
            store["versions"].append(revision)
            atomic_json_write(state / _FILENAME, {**store, "checksum": _digest(store)})
    return {
        "schema_version": 1,
        "status": "registered",
        "vault_id": config.id,
        "revision": revision["revision"],
        "context_count": len(normalized["contexts"]),
        "previous_versions_retained": current_revision,
        "vault_writes": 0,
    }


def check_contexts(
    registry: VaultRegistry, config: VaultConfig, *, limit: int = 20
) -> dict[str, Any]:
    """Compare current evidence with the saved baseline without silently rebasing it."""
    if not 1 <= limit <= 100:
        raise ValueError("Context limit must be between 1 and 100")
    version = load_context_version(registry, config)
    if version is None:
        return {
            "schema_version": 1,
            "status": "unregistered",
            "revision": 0,
            "findings": [],
        }
    scan = scan_notes(config.root_path)
    discover_contexts(scan, vault_id=config.id, definitions=version["definitions"])
    current = _bindings(version["definitions"], scan)
    findings = []
    for context_id, scopes in version["bindings"].items():
        for scope, previous in scopes.items():
            now = current[context_id][scope]
            added = sorted(set(now) - set(previous))
            removed = sorted(set(previous) - set(now))
            changed = sorted(
                p for p in set(previous) & set(now) if previous[p] != now[p]
            )
            if not (added or removed or changed):
                continue
            findings.append(
                {
                    "context_id": context_id,
                    "scope": scope,
                    "code": "scope_without_evidence"
                    if not now
                    else "source_set_changed",
                    "added_count": len(added),
                    "removed_count": len(removed),
                    "changed_count": len(changed),
                    "added": added[:limit],
                    "removed": removed[:limit],
                    "changed": changed[:limit],
                    "samples_truncated": any(
                        len(paths) > limit for paths in (added, removed, changed)
                    ),
                    "suggestion": "Review current sources; this may be an ordinary edit, not a quality defect",
                }
            )
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "revision": version["revision"],
        "status": "changed" if findings else "current",
        "finding_count": len(findings),
        "findings": findings[:limit],
        "truncated": len(findings) > limit,
        "baseline_updated": False,
        "vault_writes": 0,
    }
