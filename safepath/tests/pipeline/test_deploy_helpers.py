"""deploy_helpers tests (task 12.6): pure logic, no AWS and no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SAFEPATH = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SAFEPATH / "scripts"))
import deploy_helpers as dh  # noqa: E402

TAGGED = [{"Key": "Project", "Value": "SafePath"}, {"Key": "Simulated", "Value": "true"}]


def test_prefix_region_email_validation():
    assert dh.validate_prefix("safepath") == "safepath"
    for bad in ("", "Safe", "a", "-x-", "safe_path", "1abc", "a--b"):
        with pytest.raises(ValueError):
            dh.validate_prefix(bad)
    assert dh.validate_region("us-east-1") == "us-east-1"
    with pytest.raises(ValueError):
        dh.validate_region("us east 1; rm")
    with pytest.raises(ValueError):
        dh.validate_email("not an email")


def test_mask_and_root_detection():
    assert dh.mask_account("123456789012") == "********9012"
    assert dh.mask_account("bad") == "????????????"
    assert dh.is_root_arn("arn:aws:iam::123456789012:root")
    assert not dh.is_root_arn("arn:aws:sts::123456789012:assumed-role/Admin/me")
    assert not dh.is_root_arn("arn:aws:iam::123456789012:user/root")


def test_stack_names_and_prefix_check():
    with pytest.raises(ValueError):
        dh.stack_names("sp")
    assert dh.stack_names("safepath") == ["safepath-data", "safepath-api", "safepath-web",
                                          "safepath-observability", "safepath-budget"]
    assert dh.check_stack_name("safepath-api", "safepath")
    for bad in ("other-api", "safepathX-api", "safepath", "prod-safepath-api"):
        with pytest.raises(dh.UnsafeTarget):
            dh.check_stack_name(bad, "safepath")


def test_bucket_requires_prefix_kind_and_tag():
    ok = "safepath-graphs-123456789012-us-east-1"
    assert dh.check_bucket_emptyable(ok, "safepath", TAGGED) == "graphs"
    assert dh.check_bucket_emptyable("safepath-public-website-1-us-east-1", "safepath", TAGGED) == "public-website"
    with pytest.raises(dh.UnsafeTarget):
        dh.check_bucket_emptyable(ok, "safepath", [])  # untagged
    with pytest.raises(dh.UnsafeTarget):
        dh.check_bucket_emptyable(ok, "safepath", [{"Key": "Project", "Value": "Other"}])
    with pytest.raises(dh.UnsafeTarget):
        dh.check_bucket_emptyable("my-company-logs", "safepath", TAGGED)  # tagged but wrong name
    with pytest.raises(dh.UnsafeTarget):
        dh.check_bucket_emptyable("safepath-cdk-assets-1", "safepath", TAGGED)  # unknown kind


def test_select_teardown_stacks_only_tagged_prefixed_in_reverse_order():
    live = [
        {"StackName": "safepath-data", "Tags": TAGGED},
        {"StackName": "safepath-api", "Tags": TAGGED},
        {"StackName": "safepath-web", "Tags": [{"Key": "Project", "Value": "Nope"}]},
        {"StackName": "CDKToolkit", "Tags": TAGGED},
        {"StackName": "safepath-extra", "Tags": TAGGED},
        {"StackName": "other-api", "Tags": TAGGED},
    ]
    chosen, skipped = dh.select_teardown_stacks(live, "safepath")
    assert chosen == ["safepath-api", "safepath-data"]
    assert len(skipped) == 4
    with pytest.raises(dh.UnsafeTarget):
        dh.cdk_destroy_cmd(["CDKToolkit"], "safepath", [])


def test_context_args_from_discovery():
    disc = {"context_flags": {"useCognito": "false", "realtime": "apigw-ws"}}
    args = dh.context_args(disc, region="us-east-1", alert_email="a@b.co")
    pairs = [args[i + 1] for i in range(0, len(args), 2)]
    assert all(args[i] == "-c" for i in range(0, len(args), 2))
    assert "useCognito=false" in pairs and "safepath:prefix=safepath" in pairs
    assert "safepath:requireLambdaPackage=true" in pairs and "safepath:alertEmail=a@b.co" in pairs
    with pytest.raises(ValueError):
        dh.context_args({}, extra={"k": "v; calc"})


def test_command_lines():
    ctx = dh.context_args({"context_flags": {}})
    cmd = dh.cdk_deploy_cmd("safepath", ctx)
    assert cmd[:6] == ["npx", "cdk", "deploy", "--all", "--require-approval", "broadening"]
    assert dh.cdk_bootstrap_cmd("123456789012", "us-east-1")[-1] == "aws://123456789012/us-east-1"
    with pytest.raises(ValueError):
        dh.cdk_bootstrap_cmd("12", "us-east-1")
    assert dh.cdk_destroy_cmd(["safepath-api"], "safepath", [])[:4] == ["npx", "cdk", "destroy", "safepath-api"]


@pytest.mark.parametrize("path,kind,ct,cc", [
    ("index.html", "site", "text/html; charset=utf-8", dh.NO_CACHE),
    ("sw.js", "site", "text/javascript; charset=utf-8", dh.NO_CACHE),
    ("assets/index-Ab12Cd34.js", "site", "text/javascript; charset=utf-8", dh.IMMUTABLE),
    ("assets/logo.svg", "site", "image/svg+xml", "public, max-age=86400"),
    ("manifest.webmanifest", "site", "application/manifest+json", dh.NO_CACHE),
    ("latest.json", "graph", "application/json", dh.NO_CACHE),
    ("edges.jsonl.gz", "graph", "application/gzip", dh.IMMUTABLE),
    ("layers/buildings.geojson", "graph", "application/geo+json", dh.IMMUTABLE),
])
def test_content_type_and_cache_control(path, kind, ct, cc):
    assert dh.content_type_for(path) == ct
    assert dh.cache_control_for(path, kind) == cc


def _make_graph(root: Path, version="v20261024-2e1f05"):
    (root / version / "layers").mkdir(parents=True)
    for rel in ("nodes.jsonl.gz", "edges.jsonl.gz", "manifest.json", "layers/a.geojson"):
        (root / version / rel).write_bytes(b"x")
    (root / "latest.json").write_text(json.dumps({"version": version}))
    return version


def test_graph_plan_orders_latest_last_and_manifest_after_data(tmp_path):
    version = _make_graph(tmp_path)
    plan = dh.graph_upload_plan(tmp_path, "safepath-graphs-1-us-east-1")
    keys = [u.key for u in plan]
    assert keys[-1] == "graphs/latest.json"
    assert keys[-2] == f"graphs/{version}/manifest.json"
    assert keys.count("graphs/latest.json") == 1
    assert plan[-1].cache_control == dh.NO_CACHE
    assert all(k.startswith(f"graphs/{version}/") for k in keys[:-1])
    assert plan[0].command()[:4] == ["aws", "s3", "cp", plan[0].local]


def test_graph_plan_rejects_bad_latest(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps({"version": "../evil"}))
    with pytest.raises(ValueError):
        dh.graph_upload_plan(tmp_path, "b")
    (tmp_path / "latest.json").write_text(json.dumps({"version": "v20261024-abcdef"}))
    with pytest.raises(FileNotFoundError):
        dh.graph_upload_plan(tmp_path, "b")


def test_site_plan_index_last_and_full_plan(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>")
    (dist / "assets" / "app-Zx9Yw8Vu.js").write_text("1")
    (dist / "favicon.ico").write_bytes(b"i")
    tiles = tmp_path / "chicago.pmtiles"
    tiles.write_bytes(b"p")
    graphs = tmp_path / "graphs"
    _make_graph(graphs)
    site = dh.site_upload_plan(dist, "site-b")
    assert site[-1].key == "index.html"
    plan = dh.full_upload_plan(graphs, "graphs-b", "site-b", dist, tiles)
    assert plan[-1].key == "index.html"
    assert any(u.key == "tiles/chicago.pmtiles" and u.content_type == "application/octet-stream" for u in plan)
    graph_idx = [i for i, u in enumerate(plan) if u.bucket == "graphs-b"]
    assert plan[graph_idx[-1]].key == "graphs/latest.json"
    assert not any(u.bucket == "site-b" for u in dh.full_upload_plan(graphs, "graphs-b", None, dist, tiles))


def test_frontend_env_uses_public_ids_only():
    outs = {"safepath-api": {"HttpApiUrl": "https://abc.execute-api.us-east-1.amazonaws.com/", "TokenSecretArn": "arn:x"},
            "other-api": {"HttpApiUrl": "https://evil"}}
    env = dh.frontend_env(dh.parse_outputs(outs, "safepath"))
    assert env["VITE_API_BASE_URL"] == "https://abc.execute-api.us-east-1.amazonaws.com"
    assert not any("SECRET" in k or "ARN" in k for k in env)
    with pytest.raises(ValueError):
        dh.frontend_env({})


def test_frontend_env_cloudfront_base_has_no_api_suffix():
    # The frontend appends /api/... itself; a base ending in /api produced /api/api/... 404s.
    env = dh.frontend_env({"ApiViaCloudFront": "https://d1.cloudfront.net/api", "SiteUrl": "https://d1.cloudfront.net"})
    assert env["VITE_API_BASE_URL"] == "https://d1.cloudfront.net"
    assert env["VITE_PMTILES_URL"] == "https://d1.cloudfront.net/tiles/chicago.pmtiles"
    assert env["VITE_PMTILES_KIND"] == "vector"


def test_cli_roundtrip(capsys):
    assert dh.main(["check-stack", "--prefix", "safepath", "safepath-api"]) == 0
    assert dh.main(["check-stack", "--prefix", "safepath", "evil-api"]) == 2
    assert dh.main(["mask-account", "123456789012"]) == 0
    assert "********9012" in capsys.readouterr().out
