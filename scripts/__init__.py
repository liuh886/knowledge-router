"""KnowledgeRouter: verified local evidence and opt-in memory workflows."""

from __future__ import annotations

__version__ = "0.2.0"
__all__ = ["main", "__version__"]


def main() -> int:
    """Load the CLI only when invoked; package import has no path or I/O effects."""
    from .knowledge_router import main as run

    return run()
