"""Allow running as `python -m knowledge_router`."""

from __future__ import annotations

from .knowledge_router import main

if __name__ == "__main__":
    raise SystemExit(main())
