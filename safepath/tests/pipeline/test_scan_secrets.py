"""Unit tests for scripts/scan_secrets.py. Fake secrets are assembled from fragments."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan_secrets.py"
spec = importlib.util.spec_from_file_location("scan_secrets", SCRIPT)
ss = importlib.util.module_from_spec(spec)
import sys
sys.modules["scan_secrets"] = ss
spec.loader.exec_module(ss)

AKID = "AK" + "IA" + "ABCDEFGH" + "12345678"
AWS_SECRET = "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCY" + "EXAMPLEKEY"
JWT = "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r"
GHP = "gh" + "p_" + "aB3dE5gH7jK9mN1pQ3sT5vX7zA9cF1hJ3kL5"
SLACK = "xo" + "xb-" + "1234567890-abcdefghij"
APPSYNC = "da" + "2-" + "abcdefghij1234567890klmnop"
PRIV = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"


def rules(path: str, text: str) -> set[str]:
    return {f.rule for f in ss.scan_text(path, text)}


@pytest.mark.parametrize("text,rule", [
    (f'key = "{AKID}"', "aws-access-key-id"),
    (f'aws_secret_access_key = "{AWS_SECRET}"', "aws-secret-access-key"),
    (PRIV, "private-key-block"),
    (f"token {JWT}", "jwt"),
    (f"Authorization: Bearer {GHP[4:]}Zq", "bearer-token"),
    (f'k = "{APPSYNC}"', "appsync-api-key"),
    (f"t = {SLACK}", "slack-token"),
    (f"t = {GHP}", "github-token"),
    ('api_key = "q8Zr2LmX9vTb4KdW"', "generic-secret-assignment"),
])
def test_positive_samples(text, rule):
    assert rule in rules("backend/app/x.py", text)


@pytest.mark.parametrize("text", [
    'api_key = os.environ["API_KEY"]',
    'password = "${DB_PASSWORD}"',
    'secret = "aaaaaaaaaaaa"',
    "AKIA-not-a-key",
    "def get_token(): return None",
])
def test_negative_samples(text):
    assert not rules("backend/app/x.py", text)


def test_frontend_is_stricter_and_vite_names_checked():
    assert "frontend-hardcoded-secret" in rules("frontend/src/a.ts", 'const token = "abcd1234efgh"')
    assert "vite-secret-name" in rules("frontend/src/a.ts", "const k = import.meta.env.VITE_MAP_API_KEY;")
    assert not rules("frontend/src/a.ts", "const u = import.meta.env.VITE_API_BASE_URL;")


def test_env_files():
    assert ss.env_file_finding(".env")
    assert ss.env_file_finding("frontend/.env.local")
    assert ss.env_file_finding(".env.example") is None


def test_masking_never_prints_full_secret():
    f = ss.scan_text("a.py", f'k = "{AKID}"')[0]
    assert AKID not in f.render()
    assert AKID[:4] in f.render()


def test_allowlist_requires_justification_and_scopes(tmp_path):
    p = tmp_path / "al.json"
    p.write_text(json.dumps({"entries": [{"path": "x/*"}]}))
    with pytest.raises(SystemExit):
        ss.load_allowlist(p)
    entries = [{"path": "tests/*", "rule": "aws-access-key-id", "justification": "fixture"}]
    f_in = ss.Finding("tests/a.py", 1, "aws-access-key-id", AKID, "")
    f_out = ss.Finding("src/a.py", 1, "aws-access-key-id", AKID, "")
    assert ss.is_allowed(f_in, entries) and not ss.is_allowed(f_out, entries)


def test_scan_directory_and_dist(tmp_path, capsys):
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "bad.js").write_text(f'k="{AKID}"')
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "a.js").write_text(f'var k="{AKID}";')
    (tmp_path / "blob.bin").write_bytes(b"\0" + AKID.encode())
    findings, _ = ss.scan(tmp_path, [], include_dist=True)
    assert [f.path for f in findings] == ["frontend/dist/a.js"]
    assert ss.main(["--root", str(tmp_path), "--no-dist"]) == 0
    assert ss.main(["--root", str(tmp_path)]) == 1
