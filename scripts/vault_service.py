#!/usr/bin/env python3
"""Application services shared by the primary CLI and compatibility wrappers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_manager import discover_contexts, plan_contexts, summarize_contexts
from context_store import load_context_version
from fts_indexer import (
    ScanReport,
    audit_index,
    build_generation_with_scan,
    prune_generations,
    scan_notes,
    search_index,
)
from registry import FileLock, VaultConfig, VaultRegistry, atomic_json_write
from secure_io import secure_read_bytes
from taxonomy_scanner import scan_taxonomy


def inspect_contexts(
    config: VaultConfig,
    *,
    registry: VaultRegistry | None = None,
    text: str | None = None,
    definitions_path: Path | None = None,
    details: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Discover current Contexts and optionally preview impacts from one source scan.

    Definitions are explicit data, never workflow instructions to execute. Their
    paths bind only readable Markdown within the selected Vault.
    """
    saved = load_context_version(registry, config) if registry is not None else None
    definitions = saved["definitions"] if saved else None
    if definitions_path is not None:
        path = definitions_path.expanduser().absolute()
        definitions = json.loads(
            secure_read_bytes(path.parent, path.name).decode("utf-8-sig")
        )
    scan = scan_notes(config.root_path)
    catalog = discover_contexts(scan, vault_id=config.id, definitions=definitions)
    metadata = {
        "definition_origin": "file_override"
        if definitions_path
        else "registered"
        if saved
        else "inferred_only",
        "definition_revision": saved["revision"]
        if saved and not definitions_path
        else None,
    }
    if text is not None:
        return {**plan_contexts(text, catalog, scan, limit=limit), **metadata}
    return {
        **(catalog if details else summarize_contexts(catalog, limit=limit)),
        **metadata,
    }


def _sync_vault(
    registry: VaultRegistry, config: VaultConfig, *, force_rebuild: bool = False,
) -> tuple[dict[str, Any], ScanReport]:
    state_dir = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as state_guard:
        with FileLock(
            state_dir / ".index.lock",
            timeout=120,
            pinned_parent=state_guard,
        ):
            current = registry.resolve(config.id)
            previous_index = state_dir / current.active_index if current.active_index else None
            source_scan = scan_notes(config.root_path)
            profile = scan_taxonomy(
                config.root_path, source_records=source_scan.records
            )
            atomic_json_write(state_dir / "taxonomy.json", profile.to_dict())
            result, verified_scan = build_generation_with_scan(
                config.root_path, state_dir, source_scan=source_scan, previous_index=previous_index,
                force_rebuild=force_rebuild,
            )
            # Publish the pointer and prune under the same lock as the build, so
            # an older concurrent sync cannot activate after a newer generation.
            registry.update_index(
                config.id, filename=str(result["filename"]), generation=str(result["generation"]),
            )
            result["pruned_indexes"] = prune_generations(
                state_dir, active_filename=str(result["filename"]), keep=2
            )
    return (
        {
            "vault": config.name,
            "vault_id": config.id,
            "archetype": profile.archetype,
            "active_projects_count": len(profile.active_projects),
            "active_areas_count": len(profile.active_areas),
            **result,
        },
        verified_scan,
    )


def sync_vault(
    registry: VaultRegistry, config: VaultConfig, *, force_rebuild: bool = False,
) -> dict[str, Any]:
    """Build and activate one complete external projection without changing the Vault."""
    result, _verified_scan = _sync_vault(registry, config, force_rebuild=force_rebuild)
    return result


def sync_vault_with_scan(
    registry: VaultRegistry, config: VaultConfig
) -> tuple[dict[str, Any], ScanReport]:
    """Sync and expose the verified snapshot for immediate downstream read operations."""
    return _sync_vault(registry, config)


def active_index_path(registry: VaultRegistry, config: VaultConfig) -> Path:
    """Resolve the configured immutable index or fail with an actionable message."""
    if not config.active_index:
        raise FileNotFoundError(f"Vault {config.name!r} has no index; run sync")
    path = registry.vault_state_dir(config) / config.active_index
    if not path.is_file():
        raise FileNotFoundError(
            f"Active index is missing for Vault {config.name!r}: {path}"
        )
    return path


def vault_status(
    registry: VaultRegistry, config: VaultConfig, *, audit: bool
) -> dict[str, Any]:
    """Return registry and optional source/index consistency state."""
    path = (
        registry.vault_state_dir(config) / config.active_index
        if config.active_index
        else None
    )
    result: dict[str, Any] = {
        **config.to_dict(),
        "root_exists": config.root_path.is_dir(),
        "index_path": str(path) if path else "",
        "index_exists": bool(path and path.is_file()),
    }
    if audit and path:
        result["audit"] = audit_index(config.root_path, path)
    return result


def search_vault(
    registry: VaultRegistry,
    config: VaultConfig,
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Search one bound index and return fully sourced evidence rows."""
    return search_index(
        active_index_path(registry, config),
        query,
        limit=limit,
        vault_root=config.root_path,
        vault_id=config.id,
        generation=config.index_generation,
    )
