"""Pure, offline helpers for scripts/deploy.ps1, teardown.ps1 and verify_deployment.ps1 (task 12.6).

Nothing here talks to AWS or the network. The PowerShell scripts call the CLI at the bottom
(``python scripts/deploy_helpers.py <command> ...``) and receive JSON, so every decision that matters
(name/prefix safety, upload ordering, Content-Type and Cache-Control, command lines) is unit tested.

Safety rules encoded here:
* a stack or bucket may only be touched when its name is the SafePath prefix or starts with ``<prefix>-``;
* teardown additionally requires the live resource to carry the tag ``Project=SafePath``;
* ``latest.json`` of the graph artifact is always uploaded last.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_PREFIX = "safepath"
TAG_KEY, TAG_VALUE = "Project", "SafePath"
STACK_SUFFIXES = ("data", "api", "web", "observability", "budget")
# Bucket kinds created by the CDK app: <prefix>-<kind>-<account>-<region>
BUCKET_KINDS = ("graphs", "photos", "site", "public-website")
EMPTYABLE_KINDS = ("graphs", "photos", "site", "public-website")

_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ACCOUNT_RE = re.compile(r"^\d{12}$")

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache, no-store, must-revalidate"
SHORT = "public, max-age=3600"

_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json", ".geojson": "application/geo+json",
    ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".map": "application/json",
    ".woff2": "font/woff2", ".woff": "font/woff", ".pmtiles": "application/octet-stream",
    ".gz": "application/gzip", ".wasm": "application/wasm", ".xml": "application/xml",
}
_NO_CACHE_NAMES = {"index.html", "sw.js", "service-worker.js", "registerSW.js", "manifest.webmanifest",
                   "workbox-config.js", "latest.json"}
_HASHED_RE = re.compile(r"[-.][A-Za-z0-9_]{8,}\.[A-Za-z0-9]+$")


class UnsafeTarget(ValueError):
    """Raised when a stack or bucket is not clearly a SafePath resource."""


# --------------------------------------------------------------------------- validation
def validate_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix) or not 3 <= len(prefix) <= 24:
        raise ValueError("prefix must be 3-24 chars of lowercase letters, digits and single hyphens, starting with a letter")
    return prefix


def validate_region(region: str) -> str:
    if not isinstance(region, str) or not _REGION_RE.match(region):
        raise ValueError(f"not a valid AWS region name: {region!r}")
    return region


def validate_email(email: str) -> str:
    if not isinstance(email, str) or not _EMAIL_RE.match(email) or len(email) > 254:
        raise ValueError("alert email address looks invalid")
    return email


def mask_account(account: str) -> str:
    """``123456789012`` -> ``********9012``."""
    if not _ACCOUNT_RE.match(account or ""):
        return "????????????"
    return "*" * 8 + account[-4:]


def is_root_arn(arn: str) -> bool:
    """True for the account root user ARN (``arn:aws:iam::123456789012:root``)."""
    return bool(re.match(r"^arn:aws[a-z-]*:iam::\d{12}:root$", arn or ""))


def stack_names(prefix: str = DEFAULT_PREFIX, skip: Iterable[str] = ()) -> list[str]:
    validate_prefix(prefix)
    skipped = set(skip)
    return [f"{prefix}-{s}" for s in STACK_SUFFIXES if s not in skipped]


def name_has_prefix(name: str, prefix: str) -> bool:
    return isinstance(name, str) and (name == prefix or name.startswith(prefix + "-")) and len(name) > len(prefix)


def check_stack_name(name: str, prefix: str) -> str:
    validate_prefix(prefix)
    if not name_has_prefix(name, prefix):
        raise UnsafeTarget(f"stack {name!r} does not start with the SafePath prefix {prefix + '-'!r}")
    return name


def bucket_kind(name: str, prefix: str) -> str | None:
    """Kind (graphs, photos, site, public-website) of ``<prefix>-<kind>-...`` or None."""
    if not name_has_prefix(name, prefix):
        return None
    rest = name[len(prefix) + 1:]
    for kind in sorted(BUCKET_KINDS, key=len, reverse=True):
        if rest == kind or rest.startswith(kind + "-"):
            return kind
    return None


def has_safepath_tag(tags: Any) -> bool:
    """Accepts ``[{"Key":..,"Value":..}]`` (S3/CFN style) or a plain mapping."""
    if isinstance(tags, Mapping):
        return tags.get(TAG_KEY) == TAG_VALUE
    if isinstance(tags, (list, tuple)):
        return any(isinstance(t, Mapping) and t.get("Key") == TAG_KEY and t.get("Value") == TAG_VALUE for t in tags)
    return False


def check_bucket_emptyable(name: str, prefix: str, tags: Any) -> str:
    """Return the bucket kind or raise: the name must match AND the live bucket must be tagged."""
    validate_prefix(prefix)
    kind = bucket_kind(name, prefix)
    if kind is None or kind not in EMPTYABLE_KINDS:
        raise UnsafeTarget(f"bucket {name!r} is not a SafePath bucket for prefix {prefix!r}; refusing")
    if not has_safepath_tag(tags):
        raise UnsafeTarget(f"bucket {name!r} is not tagged {TAG_KEY}={TAG_VALUE}; refusing to empty it")
    return kind


def select_teardown_stacks(existing: Iterable[Mapping[str, Any]], prefix: str) -> tuple[list[str], list[str]]:
    """Split live stacks (``{"StackName","Tags"}``) into (to delete, skipped with reason).

    Deletion order is reverse dependency order (budget, observability, web, api, data).
    """
    validate_prefix(prefix)
    order = {f"{prefix}-{s}": i for i, s in enumerate(STACK_SUFFIXES)}
    chosen: list[str] = []
    skipped: list[str] = []
    for st in existing:
        name = st.get("StackName", "")
        if not name_has_prefix(name, prefix):
            skipped.append(f"{name}: name does not start with {prefix}-")
        elif name not in order:
            skipped.append(f"{name}: not one of the known SafePath stacks")
        elif not has_safepath_tag(st.get("Tags")):
            skipped.append(f"{name}: missing tag {TAG_KEY}={TAG_VALUE}")
        else:
            chosen.append(name)
    chosen.sort(key=lambda n: -order[n])
    return chosen, skipped


# --------------------------------------------------------------------------- command lines
def context_args(discovery: Mapping[str, Any], *, prefix: str = DEFAULT_PREFIX, region: str | None = None,
                 alert_email: str | None = None, extra: Mapping[str, str] | None = None,
                 require_package: bool = True) -> list[str]:
    """``-c key=value`` pairs for cdk from discovery.json context_flags plus explicit settings."""
    validate_prefix(prefix)
    pairs: dict[str, str] = {str(k): str(v) for k, v in (discovery.get("context_flags") or {}).items()}
    pairs["safepath:prefix"] = prefix
    if region:
        pairs["safepath:region"] = validate_region(region)
    if alert_email:
        pairs["safepath:alertEmail"] = validate_email(alert_email)
    if require_package:
        pairs["safepath:requireLambdaPackage"] = "true"
    pairs.update({str(k): str(v) for k, v in (extra or {}).items()})
    out: list[str] = []
    for k, v in pairs.items():
        if not re.match(r"^[A-Za-z0-9:_.\-]+$", k) or not re.match(r"^[A-Za-z0-9@:_.,/+\-]*$", v):
            raise ValueError(f"unsafe context entry {k!r}={v!r}")
        out += ["-c", f"{k}={v}"]
    return out


def cdk_bootstrap_cmd(account: str, region: str) -> list[str]:
    if not _ACCOUNT_RE.match(account):
        raise ValueError("account id must be 12 digits")
    return ["npx", "cdk", "bootstrap", f"aws://{account}/{validate_region(region)}"]


def cdk_deploy_cmd(prefix: str, ctx: list[str], *, outputs_file: str = "cdk-outputs.json",
                   approval: str = "broadening") -> list[str]:
    validate_prefix(prefix)
    if approval not in ("broadening", "any-change", "never"):
        raise ValueError("bad approval level")
    # --all only covers the stacks of this CDK app, which are all named <prefix>-*.
    # CloudFormation rolls a failed stack back (removing only what that deployment created).
    return ["npx", "cdk", "deploy", "--all", "--require-approval", approval,
            "--outputs-file", outputs_file] + ctx


def cdk_destroy_cmd(stacks: list[str], prefix: str, ctx: list[str]) -> list[str]:
    for s in stacks:
        check_stack_name(s, prefix)
    if not stacks:
        raise ValueError("no stacks to destroy")
    return ["npx", "cdk", "destroy"] + stacks + ["--force"] + ctx


# --------------------------------------------------------------------------- uploads
def content_type_for(rel_path: str) -> str:
    ext = Path(rel_path).suffix.lower()
    if ext in _TYPES:
        return _TYPES[ext]
    guess, _ = mimetypes.guess_type(rel_path)
    return guess or "application/octet-stream"


def cache_control_for(rel_path: str, kind: str = "site") -> str:
    """Hashed build assets are immutable; entry points, service worker and latest.json are never cached.

    ``kind``: ``site`` (frontend dist), ``tiles``, ``graph`` (versioned artifact files).
    """
    name = Path(rel_path).name
    if name in _NO_CACHE_NAMES:
        return NO_CACHE
    if kind == "graph":
        return IMMUTABLE  # versioned directories are never overwritten
    if kind == "tiles":
        return SHORT
    parts = Path(rel_path).as_posix().split("/")
    if parts[0] == "assets" and _HASHED_RE.search(name):
        return IMMUTABLE
    if parts[0] == "assets":
        return "public, max-age=86400"
    return SHORT


@dataclass(frozen=True)
class Upload:
    local: str
    bucket: str
    key: str
    content_type: str
    cache_control: str

    def command(self) -> list[str]:
        return ["aws", "s3", "cp", self.local, f"s3://{self.bucket}/{self.key}",
                "--content-type", self.content_type, "--cache-control", self.cache_control,
                "--only-show-errors"]


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def read_latest_version(graphs_root: Path) -> str:
    data = json.loads((graphs_root / "latest.json").read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not re.match(r"^v[0-9]{8}-[0-9a-f]{6,}$", version):
        raise ValueError(f"latest.json has no valid version: {version!r}")
    if not (graphs_root / version).is_dir():
        raise FileNotFoundError(f"graph version directory missing: {graphs_root / version}")
    return version


def graph_upload_plan(graphs_root: Path, bucket: str, prefix: str = "graphs") -> list[Upload]:
    """Version files first (sorted, manifest.json last among them), ``latest.json`` strictly last."""
    version = read_latest_version(graphs_root)
    vdir = graphs_root / version
    files = _files(vdir)
    files.sort(key=lambda p: (p.name == "manifest.json", p.relative_to(vdir).as_posix()))
    plan = [Upload(str(p), bucket, f"{prefix}/{version}/{p.relative_to(vdir).as_posix()}",
                   content_type_for(p.name), cache_control_for(p.name, "graph")) for p in files]
    latest = graphs_root / "latest.json"
    plan.append(Upload(str(latest), bucket, f"{prefix}/latest.json", "application/json", NO_CACHE))
    return plan


def site_upload_plan(dist: Path, bucket: str) -> list[Upload]:
    """Frontend bundle: everything except ``index.html`` first, ``index.html`` last (no stale entry point)."""
    files = _files(dist)
    files.sort(key=lambda p: (p.relative_to(dist).as_posix() == "index.html", p.relative_to(dist).as_posix()))
    return [Upload(str(p), bucket, p.relative_to(dist).as_posix(), content_type_for(p.name),
                   cache_control_for(p.relative_to(dist).as_posix(), "site")) for p in files]


def tiles_upload_plan(pmtiles: Path, bucket: str) -> list[Upload]:
    # PMTiles are served with Range requests: store as-is, never with Content-Encoding.
    return [Upload(str(pmtiles), bucket, f"tiles/{pmtiles.name}", "application/octet-stream", SHORT)]


def full_upload_plan(graphs_root: Path, graphs_bucket: str, site_bucket: str | None,
                     dist: Path | None, pmtiles: Path | None) -> list[Upload]:
    plan = graph_upload_plan(graphs_root, graphs_bucket)
    if site_bucket:
        if pmtiles is not None and pmtiles.is_file():
            plan += tiles_upload_plan(pmtiles, site_bucket)
        if dist is not None and dist.is_dir():
            plan += site_upload_plan(dist, site_bucket)
    return plan


# --------------------------------------------------------------------------- outputs
def parse_outputs(outputs: Mapping[str, Mapping[str, str]], prefix: str) -> dict[str, str]:
    """Flatten ``cdk --outputs-file`` JSON for the stacks of this prefix (others are ignored)."""
    flat: dict[str, str] = {}
    for stack, vals in outputs.items():
        if name_has_prefix(stack, prefix):
            for k, v in vals.items():
                flat.setdefault(k, str(v))
    return flat


def frontend_env(outputs: Mapping[str, str], realtime: str = "sse") -> dict[str, str]:
    """Public identifiers only (Req 45.3): API URL, realtime mode, tile URL. Never secrets."""
    api = outputs.get("ApiViaCloudFront") or outputs.get("HttpApiUrl") or ""
    if not api:
        raise ValueError("stack outputs contain no API URL (HttpApiUrl)")
    # The frontend appends "/api/..." itself, so the base must be the origin. ApiViaCloudFront ends in
    # "/api"; passing it through unchanged produced "/api/api/..." requests that all returned 404.
    base = api.rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    env = {"VITE_API_BASE_URL": base, "VITE_REALTIME_MODE": realtime}
    site = outputs.get("SiteUrl")
    if site:
        env["VITE_PMTILES_URL"] = site.rstrip("/") + "/tiles/chicago.pmtiles"
        # chicago.pmtiles is a vector (MVT) extract; the frontend defaults to raster and renders nothing.
        env["VITE_PMTILES_KIND"] = "vector"
    return env


def smoke_urls(api_base: str) -> list[dict[str, str]]:
    base = api_base.rstrip("/")
    return [
        {"method": "GET", "url": base + "/api/health"},
        {"method": "GET", "url": base + "/api/graph/status"},
        {"method": "POST", "url": base + "/api/routes/calculate"},
    ]


# --------------------------------------------------------------------------- CLI for PowerShell

def _load_json(path: str) -> Any:
    # utf-8-sig: Windows PowerShell writes a BOM with -Encoding UTF8
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _emit(obj: Any) -> int:
    print(json.dumps(obj, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("context-args")
    p.add_argument("--discovery", type=Path, required=True)
    p.add_argument("--prefix", default=DEFAULT_PREFIX)
    p.add_argument("--region")
    p.add_argument("--alert-email")
    p.add_argument("--no-require-package", action="store_true")

    p = sub.add_parser("upload-plan")
    p.add_argument("--graphs", type=Path, required=True)
    p.add_argument("--graphs-bucket", required=True)
    p.add_argument("--site-bucket")
    p.add_argument("--dist", type=Path)
    p.add_argument("--pmtiles", type=Path)

    p = sub.add_parser("deploy-cmd")
    p.add_argument("--prefix", required=True)
    p.add_argument("--ctx-file", required=True)

    p = sub.add_parser("bootstrap-cmd")
    p.add_argument("--account", required=True)
    p.add_argument("--region", required=True)

    p = sub.add_parser("destroy-cmd")
    p.add_argument("--prefix", required=True)
    p.add_argument("--ctx-file", required=True)
    p.add_argument("stacks", nargs="+")

    p = sub.add_parser("check-stack")
    p.add_argument("--prefix", required=True)
    p.add_argument("names", nargs="+")

    p = sub.add_parser("check-bucket")
    p.add_argument("--prefix", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--tags-file", required=True)

    p = sub.add_parser("select-stacks")
    p.add_argument("--prefix", required=True)
    p.add_argument("--stacks-file", required=True, help="JSON file: list of {StackName, Tags}")

    p = sub.add_parser("frontend-env")
    p.add_argument("--outputs-file", required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--realtime", default="sse")

    p = sub.add_parser("mask-account")
    p.add_argument("account")

    p = sub.add_parser("is-root")
    p.add_argument("arn")

    p = sub.add_parser("validate")
    p.add_argument("--prefix")
    p.add_argument("--region")
    p.add_argument("--email")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "context-args":
            disc = _load_json(str(a.discovery))
            return _emit(context_args(disc, prefix=a.prefix, region=a.region, alert_email=a.alert_email,
                                      require_package=not a.no_require_package))
        if a.cmd == "upload-plan":
            plan = full_upload_plan(a.graphs, a.graphs_bucket, a.site_bucket, a.dist, a.pmtiles)
            return _emit([{**asdict(u), "command": u.command()} for u in plan])
        if a.cmd == "deploy-cmd":
            return _emit(cdk_deploy_cmd(a.prefix, _load_json(a.ctx_file)))
        if a.cmd == "bootstrap-cmd":
            return _emit(cdk_bootstrap_cmd(a.account, a.region))
        if a.cmd == "destroy-cmd":
            return _emit(cdk_destroy_cmd(a.stacks, a.prefix, _load_json(a.ctx_file)))
        if a.cmd == "check-stack":
            return _emit([check_stack_name(n, a.prefix) for n in a.names])
        if a.cmd == "check-bucket":
            return _emit({"name": a.name, "kind": check_bucket_emptyable(a.name, a.prefix, _load_json(a.tags_file))})
        if a.cmd == "select-stacks":
            chosen, skipped = select_teardown_stacks(_load_json(a.stacks_file), a.prefix)
            return _emit({"delete": chosen, "skipped": skipped})
        if a.cmd == "frontend-env":
            flat = parse_outputs(_load_json(a.outputs_file), a.prefix)
            return _emit(frontend_env(flat, a.realtime))
        if a.cmd == "mask-account":
            print(mask_account(a.account))
            return 0
        if a.cmd == "is-root":
            print("true" if is_root_arn(a.arn) else "false")
            return 0
        if a.cmd == "validate":
            if a.prefix:
                validate_prefix(a.prefix)
            if a.region:
                validate_region(a.region)
            if a.email:
                validate_email(a.email)
            return _emit({"ok": True})
    except (ValueError, UnsafeTarget, FileNotFoundError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
