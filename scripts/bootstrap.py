#!/usr/bin/env python3
"""Compatibility wrapper for the external, non-mutating attach workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from registry import VaultRegistry
from vault_service import sync_vault


def bootstrap_vault(
    vault_root: Path,
    *,
    force: bool = False,
    state_root: Path | None = None,
    name: str | None = None,
    write_policy: str = "off",
) -> dict[str, Any]:
    """Attach and index while preserving every byte and path inside the Vault."""
    del force  # Historical flag retained so callers do not break; rules are never overwritten.
    registry = VaultRegistry(state_root)
    config = registry.attach(vault_root, name=name, write_policy=write_policy)
    state_dir = registry.vault_state_dir(config)
    index = sync_vault(registry, config)
    return {
        "status": "ready",
        "health": "healthy",
        "vault_id": config.id,
        "vault_name": config.name,
        "vault_root": config.root,
        "archetype": index["archetype"],
        "indexed_notes": index["indexed_notes"],
        "index_db_path": index["index_path"],
        "taxonomy_cache_path": str(state_dir / "taxonomy.json"),
        "agents_md_created": False,
        "active_projects_count": index["active_projects_count"],
        "active_areas_count": index["active_areas_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, nargs="?")
    parser.add_argument("--vault", dest="vault_option", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--name")
    parser.add_argument(
        "--write-policy", choices=("off", "append-create", "reviewed-edit"), default="off"
    )
    parser.add_argument("--force-rules", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    vault = args.vault_option or args.vault
    if vault is None:
        parser.error("a Vault path is required")
    try:
        report = bootstrap_vault(
            vault,
            force=args.force_rules,
            state_root=args.state_root,
            name=args.name,
            write_policy=args.write_policy,
        )
    except Exception as exc:
        print(f"KnowledgeRouter attach failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Attached {report['vault_name']} without changing the Vault.")
        print(f"Indexed {report['indexed_notes']} notes at {report['index_db_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
