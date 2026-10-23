"""KnowledgeRouter - Symbiotic Memory CLI for AI Agents."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure package directory is on sys.path for flat sibling imports
_PKG_DIR = str(Path(__file__).resolve().parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from .knowledge_router import main

__version__ = "0.1.0"
__all__ = ["main", "__version__"]
