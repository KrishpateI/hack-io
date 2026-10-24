"""Read-only AWS sandbox discovery (Requirement 29.1, 29.5).

Probes credentials, region and service availability with harmless
list/describe calls only. Never creates, modifies or deletes anything and
never prints credentials. Writes:

* docs/aws-deployment.md  ("## Sandbox discovery" section only)
* infrastructure/aws/discovery.json  (gate file required by the deploy script)

Run (from safepath/):  backend\\.venv\\Scripts\\python scripts\\aws_discover.py
Optional boto3:         pip install -e "backend[aws]"
Without boto3/credentials the result is ``status: unavailable`` plus the
exact steps to fix it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SECTION_HEADING = "## Sandbox discovery"

AVAILABLE, DENIED, UNKNOWN = "available", "denied", "unknown"

# service key -> (boto3 client, method, kwargs, required for core deploy)
# All calls are read-only list/describe operations.
PROBES: dict[str, tuple[str, str, dict[str, Any], bool]] = {
    "dynamodb": ("dynamodb", "list_tables", {"Limit": 1}, True),
    "s3": ("s3", "list_buckets", {}, True),
    "lambda": ("lambda", "list_functions", {"MaxItems": 1}, True),
    "apigateway": ("apigateway", "get_rest_apis", {"limit": 1}, True),
    "logs": ("logs", "describe_log_groups", {"limit": 1}, True),
    "appsync": ("appsync", "list_apis", {"maxResults": 1}, False),
    "cognito": ("cognito-idp", "list_user_pools", {"MaxResults": 1}, False),
    "cloudfront": ("cloudfront", "list_distributions", {"MaxItems": "1"}, False),
    "bedrock": ("bedrock", "list_foundation_models", {}, False),
    "budgets": ("budgets", "describe_budgets", {"MaxResults": 1}, False),
    "servicequotas": ("service-quotas", "list_services", {"MaxResults": 1}, False),
}

DENIED_CODES = {
    "AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
    "UnauthorizedException", "AuthorizationError", "Forbidden",
    "NotAuthorized", "OptInRequired", "SubscriptionRequiredException",
    "UnrecognizedClientException", "ExplicitDeny",
}

# Substitution matrix (design.md "AWS discovery"): flag -> value when blocked.
SUBSTITUTIONS = [
    ("appsync", "Real-time delivery: AppSync Events",
     "API Gateway WebSocket", "realtime", "apigw-ws"),
    ("cognito", "Authentication: Amazon Cognito",
     "Backend-signed demo tokens (DemoTokenAuthProvider)", "useCognito", "false"),
    ("cloudfront", "Frontend CDN: CloudFront",
     "S3 static website hosting (documented alternative)", "enableCloudFront", "false"),
    ("bedrock", "AI summaries: Amazon Bedrock",
     "AI feature disabled (UI shows 'AI summaries unavailable')", "enableBedrock", "false"),
    ("budgets", "Cost guard: AWS Budgets",
     "No budget stack; monitor cost manually", "enableBudget", "false"),
]

SETUP_STEPS = [
    "Install the AWS CLI v2 (https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).",
    "Install boto3: backend\\.venv\\Scripts\\python -m pip install -e \"backend[aws]\" (from safepath/).",
    "Configure sandbox credentials: `aws configure` or set AWS_ACCESS_KEY_ID, "
    "AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN) plus AWS_DEFAULT_REGION.",
    "Re-run: backend\\.venv\\Scripts\\python scripts\\aws_discover.py",
]


def classify_error(exc: BaseException) -> str:
    """Map an exception from a probe to denied / unknown."""
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
    return DENIED if code in DENIED_CODES else UNKNOWN


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", "")) or type(exc).__name__
    return type(exc).__name__


def _mask_account(account: str) -> str:
    return "*" * max(len(account) - 4, 0) + account[-4:]


def discover(client_factory: Callable[[str], Any] | None, region: str | None) -> dict[str, Any]:
    """Run all probes. ``client_factory(service)`` returns a boto3-like client.

    ``None`` means boto3 or credentials are missing -> status unavailable.
    """
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": region,
        "read_only": True,
        "caller": None,
        "services": {},
        "reason": None,
        "next_steps": [],
    }
    if client_factory is None or not region:
        result["status"] = "unavailable"
        result["reason"] = (
            "boto3 not installed, no credentials configured, or no region set"
            if client_factory is None else "no AWS region configured")
        result["next_steps"] = list(SETUP_STEPS)
        return finalize(result)

    try:
        identity = client_factory("sts").get_caller_identity()
        account = str(identity.get("Account", ""))
        result["caller"] = {"account": _mask_account(account), "principal_type": _principal_type(identity.get("Arn", ""))}
    except Exception as exc:  # noqa: BLE001 - report, never raise
        result["status"] = "unavailable"
        result["reason"] = f"sts:GetCallerIdentity failed ({_error_code(exc)})"
        result["next_steps"] = list(SETUP_STEPS)
        return finalize(result)

    for name, (svc, method, kwargs, required) in PROBES.items():
        entry: dict[str, Any] = {"required": required}
        try:
            getattr(client_factory(svc), method)(**kwargs)
            entry["state"] = AVAILABLE
        except Exception as exc:  # noqa: BLE001
            entry["state"] = classify_error(exc)
            entry["detail"] = _error_code(exc)
        result["services"][name] = entry
    result["status"] = "ok"
    return finalize(result)


def _principal_type(arn: str) -> str:
    m = re.match(r"arn:[^:]*:(iam|sts)::\d*:([a-z\-]+)/", arn or "")
    return m.group(2) if m else "unknown"


def finalize(result: dict[str, Any]) -> dict[str, Any]:
    """Derive CDK context flags, substitutions and the deploy gate."""
    services = result["services"]

    def state(name: str) -> str:
        return services.get(name, {}).get("state", UNKNOWN)

    flags: dict[str, str] = {
        "useCognito": "true", "realtime": "appsync", "enableBudget": "true",
        "enableBedrock": "true", "enableCloudFront": "true",
    }
    subs = []
    for svc, feature, substitute, flag, value in SUBSTITUTIONS:
        if state(svc) != AVAILABLE:  # denied or unknown -> be conservative
            flags[flag] = value
            subs.append({"service": svc, "feature": feature, "state": state(svc),
                         "substitute": substitute, "flag": flag, "value": value})
    blocked_core = [n for n, e in services.items() if e["required"] and e["state"] != AVAILABLE]
    deployable = result["status"] == "ok" and not blocked_core
    result["context_flags"] = flags
    result["substitutions"] = subs
    result["blocked_core_services"] = blocked_core
    result["deployable"] = deployable
    result["cdk_context_args"] = " ".join(f"-c {k}={v}" for k, v in flags.items())
    return result


def render_section(result: dict[str, Any]) -> str:
    """Render the markdown 'Sandbox discovery' section (no secrets)."""
    lines = [SECTION_HEADING, "",
             f"Generated: {result['generated_at']} (read-only probes; nothing was created, modified or deleted).", "",
             f"- Status: `{result['status']}`",
             f"- Region: `{result['region'] or 'not set'}`",
             f"- Deployable (gate): `{str(result['deployable']).lower()}`"]
    if result["caller"]:
        c = result["caller"]
        lines.append(f"- Caller: account `{c['account']}`, principal type `{c['principal_type']}`")
    if result["reason"]:
        lines.append(f"- Reason: {result['reason']}")
    if result["next_steps"]:
        lines += ["", "### Steps to enable discovery", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(result["next_steps"], 1)]
    if result["services"]:
        lines += ["", "### Service availability", "", "| Service | Required | State | Detail |", "|---|---|---|---|"]
        for n, e in result["services"].items():
            lines.append(f"| {n} | {'yes' if e['required'] else 'no'} | {e['state']} | {e.get('detail', '')} |")
    lines += ["", "### Substitution matrix", ""]
    if result["substitutions"]:
        lines += ["| Feature | State | Labeled substitute | CDK flag |", "|---|---|---|---|"]
        for s in result["substitutions"]:
            lines.append(f"| {s['feature']} | {s['state']} | {s['substitute']} | `-c {s['flag']}={s['value']}` |")
    elif result["status"] == "ok":
        lines.append("No substitutions needed; all optional services are available.")
    else:
        lines.append("Not evaluated (discovery unavailable). Optional services default to their substitutes in the context flags.")
    lines += ["", "### CDK context flags", "", f"`{result['cdk_context_args']}`", "",
              "Gate file: `infrastructure/aws/discovery.json` (the deploy script requires `deployable: true`).", ""]
    return "\n".join(lines)


def replace_section(doc: str, section: str) -> str:
    """Replace only the Sandbox discovery section, or append it."""
    pattern = re.compile(rf"^{re.escape(SECTION_HEADING)}\s*$.*?(?=^## |\Z)", re.S | re.M)
    block = section.rstrip("\n") + "\n\n"
    if pattern.search(doc):
        return pattern.sub(lambda _m: block, doc, count=1).rstrip("\n") + "\n"
    base = doc.rstrip("\n")
    return (base + "\n\n" if base else "# AWS deployment\n\n") + block.rstrip("\n") + "\n"


def write_outputs(result: dict[str, Any], root: Path = ROOT) -> None:
    doc_path = root / "docs" / "aws-deployment.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    existing = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    doc_path.write_text(replace_section(existing, render_section(result)), encoding="utf-8")
    gate = root / "infrastructure" / "aws" / "discovery.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def make_boto3_factory(region_arg: str | None) -> tuple[Callable[[str], Any] | None, str | None]:
    """Return (factory, region) or (None, region) if boto3/credentials missing."""
    try:
        import boto3  # type: ignore
    except ImportError:
        return None, region_arg or os.environ.get("AWS_DEFAULT_REGION")
    session = boto3.session.Session(region_name=region_arg)
    if session.get_credentials() is None:
        return None, session.region_name
    region = session.region_name

    def factory(service: str) -> Any:
        # Budgets and CloudFront are global; use us-east-1 where required.
        reg = "us-east-1" if service in ("budgets", "cloudfront") else region
        return session.client(service, region_name=reg)

    return factory, region


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default=None)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    factory, region = make_boto3_factory(args.region)
    result = discover(factory, region)
    write_outputs(result, args.root)
    print(f"AWS discovery status: {result['status']}; deployable: {result['deployable']}")
    if result["reason"]:
        print(f"Reason: {result['reason']}")
        for i, s in enumerate(result["next_steps"], 1):
            print(f"  {i}. {s}")
    print("Wrote docs/aws-deployment.md and infrastructure/aws/discovery.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
