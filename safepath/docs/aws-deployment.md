# AWS deployment

## Sandbox discovery

Generated: 2026-10-25T06:09:20+00:00 (read-only probes; nothing was created, modified or deleted).

- Status: `ok`
- Region: `us-east-2`
- Deployable (gate): `true`
- Caller: account `********0117`, principal type `assumed-role`

### Service availability

| Service | Required | State | Detail |
|---|---|---|---|
| dynamodb | yes | available |  |
| s3 | yes | available |  |
| lambda | yes | available |  |
| apigateway | yes | available |  |
| logs | yes | available |  |
| appsync | no | available |  |
| cognito | no | available |  |
| cloudfront | no | available |  |
| bedrock | no | available |  |
| budgets | no | unknown | ParamValidationError |
| servicequotas | no | denied | AccessDeniedException |

### Substitution matrix

| Feature | State | Labeled substitute | CDK flag |
|---|---|---|---|
| Cost guard: AWS Budgets | unknown | No budget stack; monitor cost manually | `-c enableBudget=false` |

### CDK context flags

`-c useCognito=true -c realtime=appsync -c enableBudget=false -c enableBedrock=true -c enableCloudFront=true`

Gate file: `infrastructure/aws/discovery.json` (the deploy script requires `deployable: true`).

## AWS adapters and Lambda entry points

Implemented in `backend/app/adapters/aws/` and `backend/app/lambda_handler.py`. Nothing here was run against real
AWS: this machine has no AWS CLI or credentials, so the code is tested with a fake DynamoDB client, botocore's
`Stubber`, local SigV4 presigning and locally generated JWT keys (`tests/backend/test_aws_adapters.py`).
`SAFEPATH_PROFILE=aws` selects the AWS adapters; every port can still be overridden individually.

| Port | Setting | AWS adapter | Labeled substitute when the service is unavailable |
|---|---|---|---|
| Repository | `SAFEPATH_REPOSITORY=dynamo` | `DynamoRepository` | `sqlite` / `memory` (not durable on Lambda) |
| RealtimePublisher | `SAFEPATH_PUBLISHER=appsync` | `AppSyncEventsPublisher` | `apigw` = `ApiGwWebSocketPublisher` (matches CDK flag `realtime=apigw-ws`) |
| AuthProvider | `SAFEPATH_AUTH=cognito` | `CognitoAuthProvider` | `demo` = backend-signed tokens (`useCognito=false`) |
| BlobStore | `SAFEPATH_BLOBSTORE=s3` | `S3BlobStore` | `local` (filesystem, not usable on Lambda) |
| Graph source | `SAFEPATH_GRAPH_BUCKET` | `adapters/aws/graph_source.py` | bundled `data/processed` or the SIMULATED fixture graph |

If configuration or boto3 is missing, a factory raises `AdapterNotAvailable` with the reason (the app then reports
the dependent endpoints as unavailable instead of crashing).

### Environment variables

| Variable | Used by | Notes |
|---|---|---|
| `AWS_REGION` | all | required; `AWS_ENDPOINT_URL` / `AWS_ENDPOINT_URL_DYNAMODB` point at DynamoDB Local |
| `SAFEPATH_TABLE_PREFIX` | Dynamo | default `safepath-`; table `reports` becomes `safepath-reports` |
| `SAFEPATH_PHOTO_BUCKET` (or `SAFEPATH_BLOB_BUCKET`, `SAFEPATH_DATA_BUCKET`) | S3 | private bucket; optional `SAFEPATH_BLOB_PREFIX` |
| `SAFEPATH_APPSYNC_EVENTS_ENDPOINT` | AppSync | `https://<id>.appsync-api.<region>.amazonaws.com/event`; optional `SAFEPATH_APPSYNC_NAMESPACE` (default `incidents`) |
| `SAFEPATH_APPSYNC_API_KEY` | AppSync | when unset, requests are SigV4 signed with the function role (IAM) |
| `SAFEPATH_WS_ENDPOINT` | WebSocket fallback | `https://<api-id>.execute-api.<region>.amazonaws.com/<stage>` |
| `SAFEPATH_COGNITO_USER_POOL_ID`, `SAFEPATH_COGNITO_CLIENT_ID` | Cognito | optional `SAFEPATH_COGNITO_REGION`, `SAFEPATH_COGNITO_GROUP_MODERATOR`, `SAFEPATH_COGNITO_GROUP_OFFICIAL` |
| `SAFEPATH_TOKEN_SECRET` | guests | signs guest tokens (HS256-style HMAC); required so guests work alongside Cognito |
| `SAFEPATH_ROLES_FILE` | roles | admin `roles.yaml` (subject -> role) |
| `SAFEPATH_GRAPH_BUCKET`, `SAFEPATH_GRAPH_PREFIX` (default `graphs`), `SAFEPATH_GRAPH_CACHE_DIR` (default `/tmp/safepath-graph`) | graph loader | |
| `SAFEPATH_LAMBDA_EAGER_INIT=1` | API function | load the graph during the INIT phase |

### DynamoDB layout

Every table has a string `pk` (HASH) and `sk` (RANGE) key and on-demand capacity (`create_table_params()` in
`adapters/aws/dynamo.py` returns the `CreateTable` arguments; `TABLES` lists them). Required GSIs: `reports`
`report-id` (partition `report_id`) and `checkins` `user-index` (partition `user_id`), both projecting ALL. Enable TTL
on attribute `ttl` (epoch seconds) for `events`, `ratelimits` and `connections`. DynamoDB deletes expired items lazily
(up to about 48 hours), so the adapter treats an expired item as absent on reads and lets `put(if_absent)` replace
it. GSIs are eventually consistent; `reports` lookups by `report_id` can briefly miss a just-written report.
Transactions are one `TransactWriteItems` call: at most 100 items and one operation per item (a violation raises
`ValueError` before anything is sent). Throttling and service errors surface as `RepositoryUnavailable` (HTTP 503,
nothing persisted, no event published).

### S3

Photos: `presign_put` returns a SigV4 PUT URL valid for 15 minutes with `Content-Type` and `Content-Length` signed, so
S3 rejects an upload whose type or size differs from what the API authorised (the size is the exact declared
size, already capped at 10 MB). `presign_get` expires in at most 15 minutes. The bucket must block all public access
and use default encryption; CORS must allow `PUT` and `GET` from the frontend origin with the `Content-Type` header.
Graph artifacts live at `s3://<data bucket>/graphs/latest.json` and `graphs/<version>/...`. The cold-start loader
verifies each file's sha256 against `manifest.json`, writes to `/tmp`, tries older versions if the newest is corrupt,
and falls back to the bundled data (reported as a fallback in `/api/graph/status`) if none can be downloaded. The
load time is logged.

### Lambda functions and packaging

`backend\.venv\Scripts\python scripts\package_lambda.py` builds `infrastructure/aws/build/safepath-lambda.zip`
(git-ignored): pinned runtime dependencies as Linux wheels (`--arch arm64` for Graviton), `backend/app`, `data/sources`,
`data/demo`, the SIMULATED fixture graph, and a `lambda_entry.py` shim. Tests, dev, pipeline and pdf extras and
uvicorn are excluded; boto3 comes from the Lambda runtime unless `--include-boto` is passed. If the unzipped package
exceeds 250 MB, move numpy, shapely and cryptography into a layer or use a container image.

| Function | Handler | Trigger |
|---|---|---|
| API | `lambda_entry.handler` | API Gateway HTTP API (payload v2) |
| Sweeper | `lambda_entry.sweeper_handler` | EventBridge, every minute |
| WebSocket fallback | `lambda_entry.websocket_handler` | API Gateway WebSocket routes `$connect`, `$disconnect`, `$default` |

Roles from Cognito come only from the verified token's `cognito:groups` (pool administrators control membership) and
from `roles.yaml`; `custom:role` and any `role` claim are ignored. Guest sessions use the backend-signed demo token
format alongside Cognito JWTs.

### Lambda memory recommendation

`Graph.load` of the real downtown graph (about 61k nodes) used roughly 915 to 975 MB resident in local measurements.
Configure the API function with **3008 MB** (more memory also means more CPU, which shortens the cold-start load), a
timeout of at least 30 seconds, and `SAFEPATH_LAMBDA_EAGER_INIT=1`. The Sweeper and WebSocket functions do not load
the graph in the WebSocket case and can use 512 MB. A lighter loader (a compact array format that avoids building the
full object model, or memory-mapped arrays) is a follow-up and would let the API function run in less memory.

### Not yet verified against real AWS

Real-service behavior (GSI propagation delay, AppSync Events response codes for a given auth mode, API Gateway
management endpoint permissions, S3 CORS) has not been exercised. Run a smoke test after the first deployment.

## Hosting, observability and budget (CDK, task 12.5)

Not deployed. Details and context keys: `infrastructure/aws/README.md`.

| Stack | Created when | Contents |
|---|---|---|
| `<prefix>-web` | always (site bucket); distribution only with `-c enableCloudFront=true` | private S3 site bucket; CloudFront with OAC, security headers + CSP, `/api/*` proxy, `tiles/*` Range-friendly behavior, SPA fallback function |
| `<prefix>-observability` | always (skip with `-c safepath:skip=observability`) | dashboard `<prefix>-overview`, alarms, optional SNS email topic (`-c safepath:alertEmail=`) |
| `<prefix>-budget` | `enableBudget=true` and `safepath:alertEmail` set | monthly 25 USD budget, ACTUAL 80% and FORECASTED 100% email alerts |

Frontend upload (no CDK-managed deployment Lambda): after `npm run build` in `frontend/`,
`aws s3 sync frontend/dist s3://<SiteBucketName> --delete`, PMTiles to `tiles/`, GeoJSON to `layers/`, then invalidate `/index.html` and `/sw.js`
in CloudFront if they changed. Without CloudFront nothing is hosted publicly; run the frontend locally. The only alternative is the explicit
`-c safepath:webHosting=s3-website` opt-in: a public, HTTP-only bucket (no TLS, so no service worker or geolocation in browsers); sandbox demos only.

Notes for the first deployment:

- The default `*.cloudfront.net` certificate cannot enforce TLS 1.2; use `safepath:domainName` + `safepath:certificateArn` (us-east-1) for that.
- WebSockets bypass CloudFront: use the api stack's `WebSocketUrl` (or the AppSync Events endpoint); the CSP `connect-src` already allows them.
- With CloudFront, call the API at `https://<distribution>/api/...` (same origin, no CORS); `frontendOrigins` must still contain the origin for direct API calls.
- Verify against the real distribution: Range requests on a `.pmtiles` object return 206, and a deep link such as `/map` returns the SPA.
- Budgets need billing permissions that a sandbox may lack; keep `enableBudget=false` there and watch cost manually.
- `cdk destroy` fails on non-empty buckets: empty the site bucket first.

## Deploying (task 12.6)

None of this has been run: the machine that wrote the tooling has no AWS CLI and no credentials. The scripts were
parse-checked and exercised in dry-run mode only; the pure logic is unit tested (`tests/pipeline/test_deploy_helpers.py`).
Run the first real deployment yourself and read the dry-run output first.

### Prerequisites

1. Install the AWS CLI v2 (https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), Node.js and the
   project venv (`backend\.venv`, `pip install -e "backend[dev,aws]"`).
2. Sign in with a NON-root identity: `aws configure sso` (preferred) or `aws configure`. Never paste access keys into the
   repository, a chat or a script; `deploy.ps1` refuses the root user and only prints a masked account id.
3. Use a sandbox account. Everything is tagged `Project=SafePath`, `Simulated=true`.

### Commands (from `safepath/`)

```powershell
# 1. Dry run (default): prints the plan, changes nothing, exits 0 even without the aws CLI.
.\scripts\deploy.ps1 -Region us-east-1

# 2. Real run. -Bootstrap only the first time in this account/region. You must type the stack prefix to confirm.
.\scripts\deploy.ps1 -Region us-east-1 -Execute -Bootstrap -AlertEmail you@example.com

# Single stages: -Stage preflight | build | bootstrap | deploy | frontend | upload | verify
.\scripts\deploy.ps1 -Region us-east-1 -Execute -Stage upload

# Smoke tests only (HTTP, no AWS API): reads cdk-outputs.json or takes -ApiBaseUrl
.\scripts\verify_deployment.ps1 -ApiBaseUrl https://<id>.execute-api.us-east-1.amazonaws.com
```

Flags: `-Execute` (required to change anything; `-DryRun`/`-WhatIf` are the default), `-Region` (required, never guessed),
`-Prefix` (default `safepath`; stacks and buckets must start with it), `-AlertEmail`, `-Profile`, `-Bootstrap`,
`-Yes -ConfirmPrefix <prefix>` (non-interactive confirmation; it still has to name the prefix), `-SkipFrontend`,
`-RealtimeMode` (value of `VITE_REALTIME_MODE`, default `sse`).

What `-Execute` does, in order:

1. Preflight: `aws sts get-caller-identity` (read-only), masked account, not root, re-runs `scripts/aws_discover.py` and
   stops when `discovery.json` says `deployable=false`; prints the cost/risk summary; you type the prefix.
2. Build: `scripts/package_lambda.py` (-> `infrastructure/aws/build/safepath-lambda.zip`), `npm ci` for the CDK app.
3. Bootstrap (only with `-Bootstrap`): `npx cdk bootstrap aws://<account>/<region>`.
4. `npx cdk deploy --all --require-approval broadening --outputs-file cdk-outputs.json` with the context flags from
   `discovery.json` plus `safepath:prefix`, `safepath:region`, `safepath:alertEmail` and `safepath:requireLambdaPackage=true`.
   CloudFormation rolls a failed stack back, removing only what that deployment created.
5. Frontend, phase two: `npm ci` and `npm run build` with `VITE_API_BASE_URL` (and realtime/tile settings) taken from the
   stack outputs. Only public identifiers are baked into the bundle.
6. Upload with explicit `Content-Type` and `Cache-Control`: graph files to `s3://<graphs bucket>/graphs/<version>/`
   (immutable), `graphs/latest.json` strictly LAST (no-cache); PMTiles to `tiles/` and the frontend bundle to the site
   bucket (hashed `assets/*` immutable, `index.html`, `sw.js`, manifest no-cache, `index.html` last). The site bucket is
   used only when hosting exists (CloudFront, or the explicit `safepath:webHosting=s3-website` opt-in); otherwise only the
   graph is uploaded and you run the frontend locally. The plan uses one `aws s3 cp` per file (no `--delete`), so a stale
   file from an older build is left in place; remove it by hand if needed.
7. Smoke tests: `GET /api/health`, `GET /api/graph/status`, `POST /api/routes/calculate` (a fresh deployment has no incident,
   so a clean 404 counts as reachable), then the URLs are printed.

Note: the websocket realtime transport of the API Gateway fallback expects `incident_id` as a query parameter, while the
frontend's `websocket` mode builds `/ws/incidents/<id>` paths; keep `-RealtimeMode sse` (or polling) until that is aligned.

### Bootstrap

`cdk bootstrap` creates the `CDKToolkit` CloudFormation stack in the region: a staging S3 bucket, an ECR repository and IAM
roles (`cdk-*`). CDK needs it to upload the Lambda zip asset, so there is no practical bootstrap-less route for this app. If
your sandbox forbids creating IAM roles or the toolkit stack, ask the account owner to bootstrap once (they can use a
different qualifier), or deploy somewhere that already has `CDKToolkit`. The bootstrap resources are not tagged
`Project=SafePath` and `teardown.ps1` never removes them, since they may be shared.

### Secrets and demo mode

The API stack passes `SAFEPATH_TOKEN_SECRET_ARN` and `SAFEPATH_ALIAS_SECRET_ARN`; at start-up
(`backend/app/config_secrets.py`, only with `SAFEPATH_PROFILE=aws`, only when the plain variable is absent) the backend reads
the values from Secrets Manager once, caches them in memory and never logs them. The function role already has read access.

`SAFEPATH_DEMO_MODE` is OFF in AWS. To try the demo scenarios, set it to `true` on the API function (console: Lambda ->
`<prefix>-api` -> Configuration -> Environment variables) and call `POST /api/demo/sessions`. Risk: while it is on, anyone who
can reach the API can mint moderator-level demo sessions. Remove the variable right after the demo (a redeploy also resets it).
Demo data is SIMULATED; never mix it with real data.

### Cost notes

Designed to stay small: on-demand DynamoDB, Lambda (3008 MB API function, one invocation per minute for the sweeper), 7-day
log retention, no NAT gateways or provisioned capacity. Not free: two Secrets Manager secrets (about 0.80 USD/month), alarms and
the dashboard beyond the free tier, and CloudFront transfer if enabled. Expect a few USD/month idle; this is an estimate, not a
quote. The optional budget stack (25 USD/month, alerts at 80% actual and 100% forecast) is created only with
`enableBudget=true` and `-AlertEmail`. The graphs, photos and site buckets are `RemovalPolicy.DESTROY`.

### Teardown

```powershell
.\scripts\teardown.ps1 -Region us-east-1             # dry run: lists what would be deleted
.\scripts\teardown.ps1 -Region us-east-1 -Execute    # type the prefix to confirm
```

It only considers stacks named `<prefix>-data|api|web|observability|budget` that carry the tag `Project=SafePath`; it empties
(all object versions and delete markers) only buckets that belong to those stacks, are named `<prefix>-(graphs|photos|site|
public-website)-*` and are tagged `Project=SafePath`, then runs `npx cdk destroy <stacks> --force`. Anything else, including
`CDKToolkit`, is left alone and reported as skipped. DynamoDB data is deleted with the tables.
