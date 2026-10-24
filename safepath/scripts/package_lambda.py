"""Build the minimal Lambda deployment zip into infrastructure/aws/build/ (git-ignored).

    backend\\.venv\\Scripts\\python scripts\\package_lambda.py [--arch x86_64|arm64] [--include-boto]
                                                            [--bundle-graph PATH] [--skip-deps]

Contents (no tests, no dev/pipeline/pdf extras):
  * pinned runtime dependencies from backend/pyproject.toml (``dependencies`` + the ``aws`` extra),
    installed as Linux wheels for the Lambda runtime (``pip --platform``); uvicorn is skipped and
    boto3/botocore are skipped because the Lambda runtime provides them (``--include-boto`` keeps
    the pinned copies);
  * ``backend/app`` (the repo layout is preserved because modules resolve ``data/`` from their
    own path: ``backend/app/x/y.py`` -> ``<root>/data``);
  * ``data/sources`` and ``data/demo`` (+ ``--bundle-graph`` artifact root as ``data/processed``),
    and the SIMULATED fixture graph at ``tests/fixtures/graph`` (bundled fallback);
  * ``lambda_entry.py`` exposing ``handler``, ``sweeper_handler`` and ``websocket_handler``.

Handlers: ``lambda_entry.handler`` (API), ``lambda_entry.sweeper_handler``,
``lambda_entry.websocket_handler``. Nothing is uploaded or deployed by this script.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BUILD_DIR = ROOT / "infrastructure" / "aws" / "build"
ZIP_NAME = "safepath-lambda.zip"
RUNTIME_EXTRAS = ("aws",)
SKIP_ALWAYS = {"uvicorn", "websockets"}  # local dev server; Mangum handles the ASGI app
LAMBDA_PROVIDED = {"boto3", "botocore", "s3transfer"}
PLATFORMS = {
    "x86_64": ("manylinux2014_x86_64", "manylinux_2_28_x86_64"),
    "arm64": ("manylinux2014_aarch64", "manylinux_2_28_aarch64"),
}
APP_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test_*.py", ".pytest_cache")

ENTRY_SHIM = '''"""Lambda entry shim: the app lives in backend/app (repo layout is preserved)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from app.lambda_handler import handler, sweeper_handler, websocket_handler  # noqa: E402,F401
'''


def _name(req: str) -> str:
    return re.split(r"[<>=!~\[; ]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")


def collect_requirements(pyproject: Path = BACKEND / "pyproject.toml", *,
                         include_boto: bool = False) -> list[str]:
    """Pinned runtime requirements: ``dependencies`` plus the runtime extras."""
    meta = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    reqs = list(meta.get("dependencies", []))
    for extra in RUNTIME_EXTRAS:
        reqs += meta.get("optional-dependencies", {}).get(extra, [])
    out = []
    for r in reqs:
        n = _name(r)
        if n in SKIP_ALWAYS or (n in LAMBDA_PROVIDED and not include_boto):
            continue
        if "==" not in r:
            raise SystemExit(f"requirement {r!r} is not pinned with ==")
        out.append(r)
    return out


def install_dependencies(requirements: list[str], target: Path, arch: str, python: str) -> None:
    if not requirements:
        return
    cmd = [python, "-m", "pip", "install", "--no-compile", "--only-binary=:all:",
           "--implementation", "cp", "--python-version", "3.12", "--target", str(target)]
    for plat in PLATFORMS[arch]:
        cmd += ["--platform", plat]
    subprocess.run([*cmd, *requirements], check=True)


def stage_sources(stage: Path, bundle_graph: Path | None = None) -> None:
    shutil.copytree(BACKEND / "app", stage / "backend" / "app", ignore=APP_IGNORE)
    for sub in ("sources", "demo"):
        src = ROOT / "data" / sub
        if src.is_dir():
            shutil.copytree(src, stage / "data" / sub, ignore=APP_IGNORE)
    fixture = ROOT / "tests" / "fixtures" / "graph"
    if fixture.is_dir():
        shutil.copytree(fixture, stage / "tests" / "fixtures" / "graph", ignore=APP_IGNORE)
    if bundle_graph is not None:
        if not (bundle_graph / "graphs").is_dir():
            raise SystemExit(f"--bundle-graph {bundle_graph} has no graphs/ directory")
        shutil.copytree(bundle_graph / "graphs", stage / "data" / "processed" / "graphs")
    (stage / "lambda_entry.py").write_text(ENTRY_SHIM, encoding="utf-8")


def make_zip(stage: Path, zip_path: Path) -> int:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(stage.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, p.relative_to(stage).as_posix())
    return zip_path.stat().st_size


def build(*, arch: str = "x86_64", include_boto: bool = False, bundle_graph: Path | None = None,
          skip_deps: bool = False, build_dir: Path = BUILD_DIR, python: str = sys.executable) -> Path:
    stage = build_dir / "lambda-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    if not skip_deps:
        install_dependencies(collect_requirements(include_boto=include_boto), stage, arch, python)
    stage_sources(stage, bundle_graph)
    zip_path = build_dir / ZIP_NAME
    size = make_zip(stage, zip_path)
    shutil.rmtree(stage, ignore_errors=True)
    print(f"wrote {zip_path} ({size / 1_048_576:.1f} MiB)")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arch", choices=sorted(PLATFORMS), default="x86_64")
    ap.add_argument("--include-boto", action="store_true", help="bundle the pinned boto3/botocore")
    ap.add_argument("--bundle-graph", type=Path, help="artifact root containing graphs/ to bundle")
    ap.add_argument("--skip-deps", action="store_true", help="package application files only")
    ap.add_argument("--print-requirements", action="store_true")
    args = ap.parse_args(argv)
    if args.print_requirements:
        print("\n".join(collect_requirements(include_boto=args.include_boto)))
        return 0
    build(arch=args.arch, include_boto=args.include_boto, bundle_graph=args.bundle_graph,
          skip_deps=args.skip_deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
