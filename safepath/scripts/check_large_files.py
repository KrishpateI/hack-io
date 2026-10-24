"""Fail if any non-ignored file under the repo exceeds 50 MB (git cannot ignore by size)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LIMIT = 50 * 1024 * 1024


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    too_big = [f for f in out if (root / f).is_file() and (root / f).stat().st_size > LIMIT]
    for f in too_big:
        print(f"TOO LARGE (>50 MB): {f}")
    return 1 if too_big else 0


if __name__ == "__main__":
    sys.exit(main())
