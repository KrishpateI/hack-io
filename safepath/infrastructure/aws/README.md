# SafePath AWS infrastructure (CDK, TypeScript)

Sandbox-only. Everything is tagged `Project=SafePath`, `Environment=<env>`, `Simulated=true`
(one `ProjectTagAspect` applied to the whole app). Task 12.3 delivers `DataStack` only; Lambda/API
stacks and least-privilege IAM come in 12.4. Nothing here has been deployed.

## Commands (no AWS credentials needed for these)

```powershell
$env:NODE_OPTIONS='--use-system-ca'   # only if npm cannot reach the registry
npm install
npm run build        # tsc --noEmit
npm test             # CDK assertion tests (offline)
npx cdk synth -q     # writes cdk.out/, no lookups, no AWS calls
```

Deployment (`cdk bootstrap`, `cdk deploy`) is a deliberate manual step; see `docs/aws-deployment.md`.

## Context flags

Precedence: `-c key=value` > `discovery.json` `context_flags` (written by `scripts/aws_discover.py`) > default.
Defaults are conservative: Cognito, AppSync, Bedrock, CloudFront and Budget are all off unless `true`.

| key | default | notes |
| --- | --- | --- |
| `useCognito`, `enableBudget`, `enableBedrock`, `enableCloudFront` | `false` | read now, used by later tasks |
| `realtime` | `apigw-ws` | `appsync` or `apigw-ws` |
| `safepath:prefix` | `safepath` | table names `<prefix>-<table>`; set `SAFEPATH_TABLE_PREFIX=<prefix>-` in the backend |
| `safepath:environment` | `sandbox` | `Environment` tag |
| `safepath:frontendOrigins` | `http://localhost:5173,http://127.0.0.1:5173` | comma list, photo bucket CORS (never `*`) |
| `safepath:photoRetentionDays` | `30` | photo expiry |
| `safepath:region` / `safepath:account` | unset | else `CDK_DEFAULT_*`; if none the stack stays environment-agnostic |

No secrets live in context.

## DataStack

- 17 DynamoDB tables mirroring `backend/app/adapters/aws/dynamo.py` (`TABLES`, `INDEXES`): `pk`/`sk`
  strings, `PAY_PER_REQUEST`, AWS-managed SSE, GSIs `report-id` (reports) and `user-index` (checkins).
  Keep `TABLE_SPECS` in `lib/data-stack.ts` in sync with the backend.
- TTL on attribute `ttl`: `events`, `ratelimits`, `connections`.
- Point-in-time recovery only on `users` and `audit` (hard to rebuild; the rest is reseedable or
  ephemeral, and PITR adds cost).
- Buckets `<prefix>-graphs-<account>-<region>` (versioned, noncurrent versions expire after 90 days)
  and `<prefix>-photos-<account>-<region>` (photos expire after N days, CORS PUT/GET/HEAD for the
  frontend origins). Both: block all public access, S3-managed encryption, `enforceSSL`.
  Names use CloudFormation pseudo parameters, no hard-coded account ids.
  Backend env: `SAFEPATH_PHOTO_BUCKET` (photos) and the graph source bucket (graphs).
- Outputs: every table name, `GraphsBucketName`, `PhotosBucketName`, `TablePrefix`.

## Removal and cleanup

`RemovalPolicy.DESTROY` is used on tables and buckets because this is a tagged sandbox. S3
`autoDeleteObjects` is off (it would add a custom-resource Lambda with broad S3 rights), so a
non-empty bucket makes `cdk destroy` fail. Empty both buckets first (including old versions of the
graphs bucket), then destroy.

## ApiStack (`<prefix>-api`, task 12.4)

Depends on `DataStack`. Nothing has been deployed; `npm test` and `npx cdk synth -q` run offline.

Resources:

- API Lambda `<prefix>-api` (Python 3.12, `lambda_entry.handler`, 3008 MB, 29 s, `SAFEPATH_PROFILE=aws`, graph loaded from the graphs bucket at cold start), sweeper Lambda `<prefix>-sweeper` (`lambda_entry.sweeper_handler`, EventBridge `rate(1 minute)`), and, when `realtime=apigw-ws` (default), a websocket Lambda (`lambda_entry.websocket_handler`).
- HTTP API (payload format 2.0) with a catch-all `$default` route to the API Lambda, stage throttling 50 rps / burst 100, CORS limited to `safepath:frontendOrigins` (never `*`).
- WebSocket API (`$connect`, `$disconnect`, `$default`, stage `prod`, same throttling) for `realtime=apigw-ws`; `realtime=appsync` creates an AppSync Events API (IAM auth, `incidents` namespace) instead. `aws-cdk-lib` 2.274 ships the `EventApi` L2, so no fallback is needed.
- `useCognito=true`: user pool, secretless app client (SRP), groups `moderator` and `authorized_official`, ids passed in env. Off by default; the backend then uses signed demo tokens (`SAFEPATH_AUTH=demo`).
- Two generated Secrets Manager secrets (token signing key, alias HMAC key). Lambda env holds only `SAFEPATH_TOKEN_SECRET_ARN` / `SAFEPATH_ALIAS_SECRET_ARN`; read access goes to the API Lambda only. The backend resolves these ARNs at start-up (`backend/app/config_secrets.py`, aws profile only) into `SAFEPATH_TOKEN_SECRET` / `SAFEPATH_ALIAS_SECRET`.
- Log groups with 7-day retention, CloudWatch alarms on API/sweeper errors and API throttles (no actions attached), `enableBedrock=true` adds `bedrock:InvokeModel` on one model (`-c safepath:bedrockModelId=...`).
- Outputs: `HttpApiUrl`, `WebSocketUrl`, function names, `CodeSource`, Cognito ids.

IAM: one default role per function (managed `AWSLambdaBasicExecutionRole` for logs). Explicit grants only: `grantReadWriteData` per named table (API: all; sweeper: the tables `run_sweep` touches; websocket: `connections`), `s3:GetObject` on `graphs/*` plus `s3:ListBucket` limited to the `graphs` prefix, `s3:PutObject`/`s3:GetObject` on `photos/*`, `execute-api:ManageConnections` on the specific websocket API. No wildcard actions or resources (asserted in `test/api-stack.test.ts`).

Lambda package: build with `python scripts/package_lambda.py` (writes `build/safepath-lambda.zip`; override with `-c safepath:lambdaZip=<path>`). If the zip is missing, synth uses `lambda-stub/` and warns, so offline synth and tests work. For real deployments pass `-c safepath:requireLambdaPackage=true` to make synth fail without the package.

Optional context: `safepath:reservedConcurrency` (omitted by default because new accounts often have too little unreserved concurrency for a reservation), `safepath:bedrockModelId`.

The budget lives in `BudgetStack` (see below).

## WebStack, ObservabilityStack, BudgetStack (task 12.5)

Nothing has been deployed. All three are independent of each other; skip any with `-c safepath:skip=web,observability,budget`.
Extra context keys: `safepath:alertEmail`, `safepath:budgetUsd` (default 25), `safepath:webHosting` (`none` | `s3-website`),
`safepath:tileSources` (comma list of extra tile/image origins for the CSP), `safepath:domainName` + `safepath:certificateArn`
(optional custom domain; the ACM certificate must live in us-east-1).

### WebStack (`<prefix>-web`)

- Always: a private site bucket `<prefix>-site-<account>-<region>` (block all public access, S3-managed SSE, `enforceSSL`, tagged,
  `RemovalPolicy.DESTROY`, not auto-emptied). Layout: the built frontend (`frontend/dist`) at the root, PMTiles under `tiles/`,
  static GeoJSON under `layers/`.
- `enableCloudFront=true`: a CloudFront distribution with Origin Access Control (bucket policy allows only this distribution), default root
  object `index.html`, HTTPS redirect for the site and HTTPS-only for the API, HTTP/2 + HTTP/3, price class 100.
  - Security headers policy: HSTS (1 year, subdomains), `nosniff`, `X-Frame-Options: DENY`, `strict-origin-when-cross-origin`,
    `Permissions-Policy`, and a conservative CSP (`default-src 'self'`, `script-src 'self'`, `worker-src 'self' blob:`, `object-src 'none'`,
    `frame-ancestors 'none'`). `connect-src` lists `'self'` plus the HTTP API, WebSocket API and (if used) AppSync Events endpoints and any
    `safepath:tileSources`. `style-src` allows `'unsafe-inline'` because MapLibre injects inline styles.
  - Caching: `assets/*` (hashed files) one year; `index.html`, `sw.js`, `service-worker.js`, `registerSW.js`, `manifest.webmanifest` and the
    default behavior are not cached; `layers/*` and `tiles/*` use the managed CachingOptimized policy.
  - PMTiles and Range: CloudFront serves HTTP Range requests natively (206 from cache or origin; Range is not in the cache key), so
    `tiles/*` is a dedicated behavior with GET/HEAD/OPTIONS and compression off so byte offsets stay valid. Store `.pmtiles` objects as-is
    (no `Content-Encoding`). Not exercised against a real distribution yet.
  - `/api/*` proxies to the HTTP API origin (all methods, caching disabled, all viewer headers except Host, so `Authorization` and
    `Idempotency-Key` pass). The frontend can call `/api/...` same-origin and avoid CORS. The backend routes already start with `/api`, so no
    path rewrite is needed. WebSockets are NOT proxied: connect straight to the `WebSocketUrl` output of the api stack (or the AppSync Events endpoint).
  - SPA fallback: a viewer-request CloudFront Function rewrites extensionless paths (not `/api/`) to `/index.html`. This deliberately replaces
    distribution-wide 403/404 to `/index.html` custom error responses, which would also turn the API's own 403/404 JSON errors into HTML.
    Missing files with an extension (for example a missing tile) still return 404.
  - TLS: `TLSv1.2_2021` is requested. CloudFront only enforces a minimum protocol on a custom domain with your own certificate; the default
    `*.cloudfront.net` certificate always allows TLS 1.0+. For a hard TLS 1.2 floor pass `safepath:domainName` and `safepath:certificateArn`.
  - Cross-stack: the CSP and `/api/*` origin reference the api stack outputs, so deploy order is data, api, web (CDK handles it).
- `enableCloudFront=false` (default): no distribution and no public resource. The stack only prints `HostingInstructions`
  (run `npm run dev`, or `npm run build && npm run preview` in `frontend/`).
- Explicit opt-in `-c safepath:webHosting=s3-website` (ignored when CloudFront is on): creates a SEPARATE, tagged, public-read S3 website
  bucket `<prefix>-public-website-<account>-<region>`. **Security trade-off: world-readable and HTTP only (S3 website endpoints have no HTTPS),
  so no TLS, no security headers, and browsers will block geolocation and service workers on a non-secure origin.** Demo use only; synth emits a
  warning and the output `PublicWebsiteUrl` carries a warning. The private site bucket still exists.
- No `BucketDeployment` (it adds a custom-resource Lambda with broad S3 rights). Upload yourself, or with the deploy script from task 12.6:

```powershell
aws s3 sync frontend/dist s3://<SiteBucketName> --delete
aws s3 cp tiles/chicago.pmtiles s3://<SiteBucketName>/tiles/chicago.pmtiles
aws cloudfront create-invalidation --distribution-id <id> --paths "/index.html" "/sw.js"   # only if index/sw changed
```

### ObservabilityStack (`<prefix>-observability`)

- Dashboard `<prefix>-overview`: API requests, 4xx/5xx, p95/p99 latency, Lambda throttles, Lambda duration (p95/max) and errors,
  DynamoDB throttle events and consumed capacity (summed over all tables), sweeper invocations and errors. HTTP APIs publish no
  throttle metric (throttled calls show as 429 in 4xx); Lambda used-memory needs Lambda Insights and is not enabled.
- Alarms: API 5xx, API p99 latency (> 10 s twice), API Lambda errors, sweeper stalled (fewer than 1 invocation per 5 min, missing data counts
  as breaching), DynamoDB throttles on the five busiest tables (CloudWatch allows 10 metrics per alarm expression). These complement the
  error/throttle alarms already in ApiStack. Log groups keep 7-day retention (ApiStack).
- `-c safepath:alertEmail=you@example.com`: creates SNS topic `<prefix>-alerts` (TLS only) with an email subscription (confirm the email) and
  attaches it to the alarms. Without it there is no topic and the alarms have no action.

### BudgetStack (`<prefix>-budget`)

- Instantiated only when `enableBudget=true` AND `safepath:alertEmail` is set (otherwise `cdk synth` prints one line saying why).
- `AWS::Budgets::Budget`: monthly COST budget, default 25 USD (`-c safepath:budgetUsd=40`), notifications ACTUAL at 80% and FORECASTED at 100%
  by email. Tagged through `resourceTags` (Budgets resources are not covered by the tag aspect).
- Budgets are account-scoped and need billing permissions (`budgets:*`; IAM users may also need billing access enabled). `scripts/aws_discover.py`
  reports availability and a sandbox may not allow it, in which case leave `enableBudget=false`.

Tests: `test/web-stack.test.ts` (offline assertions for all three stacks).
