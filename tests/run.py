#!/usr/bin/env python3
"""Zero-dependency test runner (pytest-compatible tests also work with
`python -m pytest tests/` when pytest is available).

Usage: python tests/run.py
"""

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_scheduler  # noqa: E402


def main() -> int:
    tests = [getattr(test_scheduler, n) for n in dir(test_scheduler)
             if n.startswith("test_")]
    failed = 0
    for fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {fn.__name__} ({time.time() - t0:.1f}s)")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
