"""OpenAPI document covers the Req 42.1 endpoints and docs/openapi.json is current (Req 42.2)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_FILE = ROOT / "docs" / "openapi.json"

# (method, path) required by Req 42.1 that the backend exposes today.
REQUIRED = [
    ("get", "/api/health"),
    ("get", "/api/regions"),
    ("get", "/api/regions/chicago/metadata"),
    ("get", "/api/graph/status"),
    ("get", "/api/incidents"),
    ("post", "/api/incidents"),
    ("get", "/api/incidents/{incident_id}"),
    ("post", "/api/routes/calculate"),
    ("post", "/api/reports"),
    ("get", "/api/reports"),
    ("get", "/api/messages"),
    ("post", "/api/messages"),
    ("post", "/api/checkins"),
    ("get", "/api/checkins"),
    ("get", "/api/sources"),
]
# Added by task 9.3; must be POST when present, but may not exist yet.
PENDING = [
    "/api/reports/{report_id}/corroborate",
    "/api/reports/{report_id}/dispute",
    "/api/reports/{report_id}/moderate",
]


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_text() -> str:
    return _load_exporter().build_openapi_text()


def test_openapi_contains_required_endpoints():
    paths = json.loads(_current_text())["paths"]
    for method, path in REQUIRED:
        assert method in paths.get(path, {}), f"missing {method.upper()} {path}"
    for path in PENDING:
        if path in paths:
            assert "post" in paths[path]


def test_openapi_export_is_deterministic():
    assert _current_text() == _current_text()


def test_committed_openapi_is_up_to_date():
    assert SPEC_FILE.exists(), "run: backend\\.venv\\Scripts\\python.exe scripts\\export_openapi.py"
    assert SPEC_FILE.read_text(encoding="utf-8") == _current_text(), (
        "docs/openapi.json is stale; run scripts/export_openapi.py and `npm run gen:api` in frontend"
    )
