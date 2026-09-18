#!/usr/bin/env python3
# ruff: noqa: INP001
"""Ductor workspace bridge to the installed local Knowledge Router skill."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_DEFAULT_ENTRY = Path(".codex") / "skills" / "knowledge-router" / "scripts" / "knowledge_router.py"


def resolve_entry() -> Path | None:
    """Locate the installed Router CLI, honoring an explicit absolute override."""
    override = os.environ.get("KNOWLEDGE_ROUTER_ENTRYPOINT", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_absolute() else None
    return Path.home() / _DEFAULT_ENTRY


def main(argv: list[str] | None = None) -> int:
    """Forward explicit Router CLI arguments without importing Ductor internals."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    entry = resolve_entry()
    if entry is None:
        print(
            "KNOWLEDGE_ROUTER_ENTRYPOINT must be an absolute path to the Router CLI.",
            file=sys.stderr,
        )
        return 2
    if arguments == ["--bridge-check"]:
        print(json.dumps({"bridge": "knowledge-router", "entry": str(entry),
                          "installed": entry.is_file(), "python": sys.executable}))
        return 0 if entry.is_file() else 2
    if not entry.is_file():
        print(
            "Knowledge Router is not installed at the expected entrypoint. "
            "Set KNOWLEDGE_ROUTER_ENTRYPOINT to an absolute CLI path.",
            file=sys.stderr,
        )
        return 2
    try:
        return subprocess.run(  # noqa: S603
            [sys.executable, str(entry), *arguments],
            cwd=entry.parent, check=False, timeout=120,
        ).returncode
    except subprocess.TimeoutExpired:
        print("Knowledge Router timed out. Inspect an event before retrying its commit.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

