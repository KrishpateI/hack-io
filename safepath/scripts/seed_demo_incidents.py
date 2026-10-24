"""Seed the SIMULATED demo incidents (tasks 11.3; Req 39, 40.4, 7.1).

Creates five SIMULATED scenarios (Loop flooding, earthquake / debris, blocked stairwell, plus the
optional tornado shelter-in-place and downtown fire samples) with ``simulation_flag`` true, a
SIMULATED destination notice where relevant, SIMULATED users and the resolved scenario step data
(``data/demo/scenarios.json`` is the definition; real edge ids are chosen from the loaded graph).

Run (from the repository's ``safepath`` folder)::

    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo_incidents.py --profile local
    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo_incidents.py --reset flood_loop
    backend\\.venv\\Scripts\\python.exe scripts\\seed_demo_incidents.py --reset-all

IMPORTANT: the default local repository is IN-MEMORY and private to the process. Seeding it from a
separate process therefore proves the seed works and prints a summary, but the running API cannot
see it. In demo mode the API seeds its own repository at start-up through the DemoService
(task 11.4), which calls ``app.services.demo_data.seed``. To persist across processes use the
SQLite repository: ``SAFEPATH_REPOSITORY=sqlite SAFEPATH_SQLITE_PATH=data/processed/demo.sqlite``
(the API must use the same settings).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SAFEPATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAFEPATH / "backend"))

from app.adapters.factory import make_repository  # noqa: E402
from app.services import demo_data  # noqa: E402
from app.services.demo_data import DemoDataError, reset_all, reset_scenario, seed  # noqa: E402

__all__ = ["seed", "reset_scenario", "reset_all", "main"]


def main(argv: list[str] | None = None, repository=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--profile", choices=("local", "aws"), default="local",
                   help="adapter profile; only 'local' is available (memory or sqlite repository)")
    p.add_argument("--scenario", action="append", dest="scenarios", metavar="ID",
                   help="seed only this scenario (repeatable); default: all")
    p.add_argument("--reset", metavar="ID", help="delete one scenario's records, then re-seed it")
    p.add_argument("--reset-all", action="store_true", help="delete all demo records, then re-seed")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = p.parse_args(argv)

    if args.profile != "local":
        print("only the local profile is supported by this script", file=sys.stderr)
        return 2
    env = dict(os.environ, SAFEPATH_PROFILE="local")
    repo = repository if repository is not None else make_repository(env)
    try:
        if args.reset_all:
            reset_all(repo)
        elif args.reset:
            reset_scenario(repo, args.reset)
            args.scenarios = [args.reset]
        result = seed(repo, scenarios=args.scenarios)
    except (DemoDataError, KeyError) as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"created": result.created, "skipped": result.skipped,
                          "graph": result.graph, "details": result.details}, indent=2))
    else:
        print("SIMULATED demo scenarios (not real events)")
        print("\n".join(result.summary_lines()))
        if env.get("SAFEPATH_REPOSITORY", "memory") == "memory":
            print("note: in-memory repository; records vanish when this process exits. "
                  "The API seeds its own repository in demo mode (DemoService).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
