"""Allow `python -m arceus_core` to behave like the `arceus` console script."""

from __future__ import annotations

import sys

from arceus_core.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised via subprocess smoke test
    sys.exit(main())
