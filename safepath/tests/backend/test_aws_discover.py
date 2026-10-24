"""Unit tests for scripts/aws_discover.py using stubbed clients (no network)."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "aws_discover.py"
spec = importlib.util.spec_from_file_location("aws_discover", SCRIPT)
ad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ad)


class StubError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class StubClient:
    def __init__(self, service, behaviors):
        self.service, self.behaviors = service, behaviors

    def __getattr__(self, method):
        def call(**kwargs):
            b = self.behaviors.get(self.service)
            if isinstance(b, str):
                raise StubError(b)
            if method == "get_caller_identity":
                return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/Sandbox/me"}
            return {}
        return call


def factory_for(behaviors):
    return lambda svc: StubClient(svc, behaviors)


def test_unavailable_without_credentials():
    r = ad.discover(None, None)
    assert r["status"] == "unavailable"
    assert r["deployable"] is False
    assert r["next_steps"]
    assert r["context_flags"]["realtime"] == "apigw-ws"


def test_all_available_is_deployable_with_default_flags():
    r = ad.discover(factory_for({}), "us-east-2")
    assert r["deployable"] is True
    assert r["substitutions"] == []
    assert r["context_flags"]["realtime"] == "appsync"
    assert r["caller"]["account"] == "********9012"


def test_denied_optional_services_map_to_substitutes():
    r = ad.discover(factory_for({"appsync": "AccessDeniedException", "cognito-idp": "AccessDenied",
                                 "bedrock": "WeirdError"}), "us-east-2")
    assert r["services"]["appsync"]["state"] == "denied"
    assert r["services"]["bedrock"]["state"] == "unknown"
    assert r["context_flags"]["realtime"] == "apigw-ws"
    assert r["context_flags"]["useCognito"] == "false"
    assert r["context_flags"]["enableBedrock"] == "false"
    assert r["deployable"] is True


def test_denied_core_service_blocks_gate():
    r = ad.discover(factory_for({"dynamodb": "AccessDenied"}), "us-east-2")
    assert r["blocked_core_services"] == ["dynamodb"]
    assert r["deployable"] is False


def test_sts_failure_is_unavailable():
    r = ad.discover(factory_for({"sts": "InvalidClientTokenId"}), "us-east-2")
    assert r["status"] == "unavailable"


def test_replace_only_section_and_write_outputs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "aws-deployment.md").write_text(
        "# AWS\n\n## Intro\nkeep me\n\n## Sandbox discovery\nOLD\n\n## Other\nkeep too\n", encoding="utf-8")
    ad.write_outputs(ad.discover(None, None), tmp_path)
    ad.write_outputs(ad.discover(factory_for({}), "us-east-2"), tmp_path)
    text = (docs / "aws-deployment.md").read_text(encoding="utf-8")
    assert "OLD" not in text and "keep me" in text and "keep too" in text
    assert text.count("## Sandbox discovery") == 1
    gate = json.loads((tmp_path / "infrastructure" / "aws" / "discovery.json").read_text())
    assert gate["status"] == "ok" and gate["deployable"] is True
    assert "123456789012" not in text
