"""Export the FastAPI OpenAPI document to ``docs/openapi.json`` (deterministic, no network).

Usage (PowerShell, from safepath/):
    backend\\.venv\\Scripts\\python.exe scripts\\export_openapi.py [--check] [--out PATH]

``--check`` exits 1 when the committed file differs from the current app schema.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_OUT = ROOT / "docs" / "openapi.json"


def build_openapi_text() -> str:
    """Return the canonical JSON text of the app's OpenAPI document."""
    from app.config import Settings
    from app.main import create_app

    # Fixed local profile keeps the output independent of the caller's environment.
    app = create_app(Settings(profile="local"))
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args(argv)
    text = build_openapi_text()
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is out of date; run scripts/export_openapi.py", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
