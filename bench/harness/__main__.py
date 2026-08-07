"""Module entry point: ``uv run python -m bench.harness``.

The implementation lives in :mod:`bench.harness.runner` so it stays importable from
tests without executing anything.
"""

from __future__ import annotations

import sys

from bench.harness.runner import main

if __name__ == "__main__":
    sys.exit(main())
