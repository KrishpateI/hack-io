# Security checks

## Secret scan

```powershell
python scripts/scan_secrets.py            # from safepath/; exit 1 on findings
python scripts/scan_secrets.py --no-dist  # skip the built frontend bundle
```

Run it before every commit (or wire it as a git pre-commit hook:
`python safepath/scripts/scan_secrets.py || exit 1`). It is pure Python, offline.

### What it covers
- Every file git would commit (`git ls-files --cached --others --exclude-standard`), so `.gitignore` is respected. Binaries, `node_modules`, `.venv`, `cdk.out`, `data/raw`, build outputs and lockfiles are skipped.
- AWS access key ids (`AKIA`/`ASIA`), AWS secret keys near `aws_secret`, `BEGIN ... PRIVATE KEY` blocks, JWTs, high-entropy bearer tokens, AppSync keys (`da2-`), Slack and GitHub tokens, and high-entropy values assigned to names like `secret`, `token`, `password`, `api_key`.
- Any `.env` file other than `.env.example`.
- Frontend (`frontend/src`, `frontend/public`, and `frontend/dist` when built) uses a stricter threshold and also flags long high-entropy string literals.
- `VITE_*` names used via `import.meta.env` (or listed in env files) must not contain `SECRET`, `KEY`, `TOKEN`, `PASSWORD` or `CREDENTIAL`: everything in the bundle is public.

Output is `file:line [rule] masked-excerpt`; full secrets are never printed.

### Allowlist
`scripts/secret_scan_allowlist.json` holds `{path, rule?, match?, justification}` entries. `justification` is mandatory. Prefer path-scoped entries with a `rule` and `match` over broad ones. Only use it for test fixtures, dev defaults and env-var/storage-key names.

## Credentials policy
- `.env`, `.env.*` (except `.env.example`), `*.pem`, `*.key`, `credentials*`, `secrets.*`, `.aws/` are git-ignored. Never force-add them.
- AWS credentials live only in the AWS CLI profile (`aws configure` / SSO), never in the repo, frontend, or env files committed to git.
- Runtime secrets come from environment variables or AWS Secrets Manager.
- If a real secret is ever committed, rotate it first, then remove it from history.
