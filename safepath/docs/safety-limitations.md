# Safety and privacy limitations

SafePath is an experimental prototype for a hackathon. It is not an official emergency service and has no
endorsement, review or approval by any city, county, state or agency. It does not contact or dispatch 911 or any
emergency service, and it cannot tell you that any street, building, exit, stairwell or shelter is safe. Routes are
algorithm-generated estimates built from public data with known gaps. In an emergency, call 911 and follow instructions
from official responders.

This file lists every fallback, unavailable source, substitution and known limitation that the repository records. Each
"known gap" below was checked against the code on the date of writing; where something could not be verified, it says so.
Companion documents: [data-sources.md](data-sources.md), [routing-design.md](routing-design.md),
[architecture.md](architecture.md), [aws-deployment.md](aws-deployment.md).

## 1. What is simulated

- All five demo scenarios are SIMULATED (`data/demo/scenarios.json`): Chicago Loop flooding, earthquake and high-rise
  debris, blocked stairwell, tornado shelter-in-place, and downtown fire perimeter. Their incidents, notices, polygons and
  reports are invented and labeled Simulated in the UI and in every record (`simulation_flag`).
- The indoor building (3 floors, 3 stairwells, 2 exits) is fictional. It is not a real building and not a real UIC floor
  plan, because no real floor plan was confirmed publicly reusable.
- The Clark/Lake station model (street level plus platform, joined by stair and ramp) is SIMULATED. Only its coordinate is
  real.
- The fixture graph (`tests/fixtures/graph`, synthetic Chicago-like grid) is used only when no real graph artifact
  loads. The app then reports `fallback` in `/api/health` and `/api/graph/status`. Do not use it for real guidance.
- Flood polygons in demo data have no water-depth values. No hydrology or flood model is run.
- Demo "official" notices are marked Simulated. No agency issued them.

## 2. Community reports versus official information

- A community report starts as `Unverified`. Corroboration by two distinct other users makes it `Corroborated`; two
  disputes make it `Disputed`. None of those states changes the hard-restriction mask. They add soft cost only
  (Unverified 20, Corroborated 40 per 100 m-scaled unit; Disputed adds uncertainty).
- Only an explicit action by a Moderator (demo) or an Authorized_Official sets `Officially_Verified`, and only then does
  a report close edges. Corroboration can never produce `Officially_Verified`.
- Moderator in this prototype means the demo console role. It is not a verification of the facts on the ground. Real
  verification workflows, staffing and accountability do not exist.
- A report that is not verified is shown as a report, with its status icon and text label, never as an official fact.
- Reports expire after 4 hours by default. Resolved and Expired reports stop affecting routes. The sweeper does this
  every minute when deployed; reads render an overdue report as Expired before the sweeper has written it.
- Authorized_Official is a role that an administrator assigns in `roles.yaml` or, with Cognito, through the group
  `authorized_official`. Client-supplied role claims are ignored and `POST /api/auth/role-request` always returns 403
  (an attempt at a privileged role is written to the audit log). No real official has been onboarded and the role has not been exercised outside tests.
- `POST /api/notices` (the only way an Official_Notice is created) is restricted to Authorized_Official and is audited.
  Moderators get 403.
- Official_Notices without a Source_Record are excluded and shown as "Unknown source". Notices derived from the 2007 plan
  or from the Cook County Hazard Mitigation Plan area are excluded from live decisions.

## 3. 911 and dispatch non-integration

- SafePath does not call, notify or dispatch 911, police, fire, EMS or any agency. No code path does, and there is no
  integration with CAD or any dispatch system.
- A "person needing assistance" report or an assistance message is visible to other app users only. There is no
  assurance anyone will see it. The UI says so and shows a 911 panel (`Emergency911Panel`, `NoDispatchNotice`).
- Check-ins are statements by the user. They do not notify family, emergency contacts or authorities.

## 4. Policy behavior that users may not expect

- Tornado: while a tornado-warning incident is active and the origin is inside a live incident boundary, SafePath returns
  `SHELTER_IN_PLACE` and computes no outdoor route. It names an official shelter only if a verified one (with a Source_Record)
  exists inside the boundary; otherwise it says "No verified shelter available" and shows Ready Illinois guidance. Every
  outdoor edge is filtered out while the warning is active. This is not a statement that a building is safe.
- If official notices conflict, or give an instruction SafePath cannot model, the outcome is
  `FOLLOW_OFFICIAL_INSTRUCTIONS` and no route is computed. Fire instructions always rank ahead of computed routes.
- `NO_VERIFIED_ROUTE_AVAILABLE` means that, with the restrictions SafePath knows about, no route was found. It does not
  mean that no way out exists.
- Flood wording is "lower modeled exposure". Higher terrain is not evidence of lower flood risk (see elevation below).
- The AI assistant, when enabled, cannot change routes or statuses (section 8).

## 5. Data limitations

- Source freshness: street data is a snapshot retrieved on 2026-10-24. The Chicago Data Portal centerline view is dated
  August 2016. There is no live feed for road closures, construction, flooding, transit disruptions, or building status.
- Unknown values: most infrastructure attributes are Unknown in the real graph (for example 147,323 of 157,687 edges have
  Unknown infrastructure level; 154,609 have Unknown mobility accessibility). Unknown adds cost and raises the route
  uncertainty level; it is not treated as clear.
- Bridge, tunnel and underground levels: USGS 3DEP is bare-earth terrain and does not represent them. Edges on such
  infrastructure get Low elevation confidence. Terrain elevation can be far from the surface people travel on.
- One-way streets: 29,120 driving edges have an Unknown one-way flag and are created in both directions. The cost model
  does not penalize that flag. Driving routes can run against traffic. 730 CDP-versus-OSM one-way conflicts were resolved
  in favor of OSM.
- Name conflicts between sources: 534, resolved in favor of the City data (street names may differ from signage).
- Building entrances are not in the graph (the OSM extract has none). Routes end at street nodes, not doors.
- Building height class is Unknown for 14,569 of 28,694 footprints and is display-only in the earthquake module.
- Connectivity: 262 disconnected components; the largest holds 98.7% of nodes (walking 97.5%, wheelchair 95.2%, driving
  93.5% of each mode's nodes). A point on a small island may get no route even though real streets connect.
- Wheelchair and step-free: no curb-ramp, slope, surface or elevator-outage data. A step-free profile excludes known steps
  and penalizes Unknown accessibility; it cannot confirm a route is step-free.
- Travel time uses fixed baselines (1.4 m/s walking; road-class driving defaults). Crowds, signals and conditions are not
  modeled.
- Flood shoreline penalty: the built graph has no `water` layer, so the shoreline term is not applied and the route
  advisory says water geometry is unavailable.
- Flood weights use fixed elevation bounds (176.0 and 184.0 m) that were not updated from the built graph's 5th and 95th
  percentiles (178.609 and 185.277 m).
- Routing weights are hand-chosen and not calibrated. There is no validation against real evacuations.
- The 2007 CBD plan is Historical. Its appendices and any newer version were not located. It names no shelter-in-place
  criteria and no specific shelter or assembly locations. See [data-sources.md](data-sources.md).
- No current OEMC public evacuation procedure was found on the pages read; SafePath states none.

## 6. Substitutions and unavailable services

Nothing has been deployed to AWS from the machine that produced this repository. It has no AWS CLI and no credentials,
so `infrastructure/aws/discovery.json` records `status: unavailable` and `deployable: false`. All AWS code is tested only
with stand-ins (a fake DynamoDB client, botocore `Stubber`, local SigV4 presigning, generated JWT keys, CDK offline
assertions, `cdk synth`). Deploy scripts were run in dry-run mode only.

| Feature | Intended | Substitute when unavailable (and CDK default) | State |
|---|---|---|---|
| Real-time delivery | AppSync Events | API Gateway WebSocket (`realtime=apigw-ws`) | Neither exercised against real AWS |
| Authentication | Amazon Cognito | Backend-signed demo tokens (`useCognito=false`) | Cognito path tested with local keys only |
| Frontend CDN | CloudFront | None: without CloudFront nothing is hosted publicly; run the frontend locally. Explicit opt-in S3 website is HTTP-only | Not deployed |
| AI summaries | Amazon Bedrock | AI disabled; UI says summaries are unavailable | Never called against Bedrock |
| Cost guard | AWS Budgets | None; monitor cost manually | May need billing permissions |
| Repository (local) | DynamoDB | In-memory (default) or SQLite; not durable on Lambda | |
| Graph source | S3 | Bundled `data/processed` or SIMULATED fixture | |
| Photo storage (local) | S3 | Local filesystem, signed local URLs | |
| Basemap | Protomaps PMTiles extract | Light-use OSM raster tiles in development only, or a blank basemap in production | Extract exists locally (4.3 MB) |
| CTA live alerts | CTA Customer Alerts API | Cached alerts with original timestamp, or "live transit alerts unavailable" | Optional |
| Transit station status | Official notice | Entrance safety stays Unknown | |

## 7. Known gaps (verified by reading the code)

These are open issues, not planned features. Each was confirmed on the date of writing.

1. Basemap style. The frontend vector style draws only `water` and `roads` layers (`frontend/src/maps/pmtiles.ts`). There
   are no labels, glyph or sprite assets, buildings or land use. The map is functional but sparse. Buildings, CTA
   stations and the extraction boundary come from `GET /api/layers/*` overlays, not from the PMTiles file. A real
   Chromium run on 2026-10-24 (Playwright with software WebGL) showed the basemap, those overlays and a computed route
   line rendering. An earlier headless check had not confirmed the canvas because the map container collapsed to zero
   height (maplibre-gl.css overrode the Tailwind `absolute` class); `MapView.tsx` now sizes it with an inline style.
2. WebSocket fallback path mismatch. The API Gateway WebSocket handler (`lambda_handler.py`) reads `incident_id` from the
   `$connect` query string, while the frontend `websocket` mode connects to `/ws/incidents/<id>` (`realtimeClients.ts`).
   The default deploy mode is `-RealtimeMode sse`, and the SSE and WebSocket endpoints in `events.py` only exist for the
   local in-process publisher. On AWS without AppSync, the practical client modes are polling or an aligned WebSocket
   path, which is not yet done.
3. No server-side idempotency for messages and check-ins. The offline queue sends a `client_request_id` for them, but
   `messaging.py` and `checkins.py` do not use it. A retry after a timeout can create a duplicate. Reports are idempotent
   through `client_report_id` (a deterministic report id).
4. Hazard overlays are not served over REST. `GET /api/layers/{name}` serves static layers only. Closures, polygons and
   notices reach the map through realtime events (stored for 24 hours) and `GET /api/graph/edges/{id}`. A device that
   connects more than 24 hours after a restriction was created may not draw it, although routing still honors it.
5. DynamoDB global secondary indexes are eventually consistent. A lookup of a just-written report by `report_id`
   (`reports` GSI `report-id`) or of a user's check-ins (`user-index`) can briefly miss it. Not exercised on real
   DynamoDB.
6. CloudFront TLS minimum. CloudFront enforces a TLS 1.2 minimum only with a custom domain and your own certificate; the
   default `*.cloudfront.net` certificate allows older TLS. No custom domain is configured.
7. Realtime latency is unmeasured. The five-second event-to-route goal assumes the 2 s in-process overlay cache plus
   channel latency. It was only tested in-process.
8. Cold start and memory. The real graph needs about 915 to 975 MB resident in local measurements. The Lambda is planned at
   3008 MB with eager init; cold-start load time was not measured on Lambda.
9. Rate limits. Per-user limits are in the app (section 9). The per-IP path for unauthenticated reads relies on API
   Gateway throttling (50 rps, burst 100 per stage) once deployed; there is no per-client limit for `POST /api/routes/calculate`,
   which is public and CPU-heavy.
10. Role-based access depends on token secrets. Demo tokens are HMAC-signed with `SAFEPATH_TOKEN_SECRET`; without it, the
    local profile creates an ephemeral secret and the AWS profile refuses to start the provider.
11. Frontend lint reports one error in `offline.test.ts` in the latest recorded run (`docs/test-results.md`), and the
    test totals there predate later changes.
12. Accessibility. Palette contrast is tested and status chips carry icon plus text, but no manual test with screen readers
    or other assistive technology was done. Full WCAG conformance requires manual testing and expert review.
13. Security review. A local secret scanner exists (`scripts/scan_secrets.py`, `docs/security-checks.md`). No penetration
    test or independent review was done.
14. Retention is enforced by the app and by DynamoDB TTL, which deletes lazily (up to about 48 hours); the adapter treats
    expired items as absent on read.
15. Messages are plain text, sanitized, and moderated only after the fact (`hide`). There is no automated abuse detection.

## 8. AI assistant guardrails

- The assistant is optional, read-only and off by default (`SAFEPATH_AI_ENABLED` plus a Bedrock model id must both be set).
  When off, unavailable, slow (over 15 s) or erroring, a deterministic template is returned and the response says
  `ai_used: false`.
- Routing, restrictions, policy outcomes and report statuses never call it. Routes are computed the same way with the
  assistant absent.
- Input is limited to structured, already-verified facts (notices with a Source_Record, computed route flags and
  restrictions, fixed glossary text). Report categorization only suggests one of nine fixed categories.
- An output guard discards any text that calls something safe, asserts a shelter, closure, order, official
  route, exit or hazard not present in the supplied facts, or cites no supplied Source_Record, and replaces it with the
  template. Every output carries the label "AI-generated summary - not official guidance" (or "Automated summary") and the
  reminder to follow local authorities and call 911.
- Limit: 10 requests per minute per user. A guard is a filter, not proof; model output can still be wrong or incomplete.

## 9. Privacy

- Guests get an alias, not a name. Public views of reports, messages and check-ins show an alias and never the reporter id,
  user id, display name, email, photo key or client idempotency key. Event payloads are scrubbed of those keys.
- Check-in and message location is opt-in per request. When shared, it is rounded to a grid of at least 200 m before
  storage and in every public view. A check-in's location expires 72 hours after creation; the sweeper removes the location
  and keeps the check-in record. Turning off sharing removes the location from public views.
- Report coordinates are not coarsened. A report's latitude and longitude are the hazard position the reporter provided
  and appear in the public report view. Users should not report from, or tag, a place they do not want shown.
- Events are stored for 24 hours. Rate-limit rows expire with their window. Reports expire in 4 hours by default.
  Photos expire in 30 days in the CDK default.
- Audit records store actor ids and are not public; the public audit view omits the actor.
- Logs and secrets were not assessed in a data-protection review. The secret scanner covers files committed to git, not
  runtime logs.
- No analytics library was added by this project, but third-party tracking in dependencies was not audited. The map loads
  tiles from the configured PMTiles source; in a development build without it, it can request OSM raster tiles directly,
  which exposes the viewer's IP and viewport to that server.
- No terms of service, privacy policy or data protection assessment has been written. This is not suitable for real
  personal data.

## 10. Rate limits and abuse controls

Per user (guest, user, moderator): reports 5 per 10 min, messages 20 per min, report votes 30 per 10 min, check-ins 10 per
10 min, photo presigns 10 per 10 min, AI 10 per min. A rejected call returns 429 with `Retry-After` and is not counted.
Request bodies are capped at 1 MB (photo upload has its own cap of 10 MB). Duplicate reports within 50 m and 60 minutes are
linked. Guest sessions last 24 hours and are free to create, so these limits are weak against someone who makes many
guests.

## 11. Offline behavior and staleness rules

- The service worker never caches or serves stale routes, reports, messages, check-ins, events, incidents, notices, demo,
  auth or photo calls, nor realtime streams. Offline, these fail visibly. Static layers, sources and region metadata are
  served stale-while-revalidate; the PMTiles basemap is cached only after the user opts in to a download.
- A device that has been disconnected for more than about 5 seconds shows a stale warning, and road conditions are not
  presented as current. After reconnect, the client replays events since its last event id and deduplicates by id.
- Reports, messages and check-ins created offline are held in a device queue (maximum 50) labeled "Queued on this device -
  not sent". They are not visible to anyone else and no emergency service has seen them. If the queue is full, the
  user is told.
- A route shown before the device went offline is not recalculated offline and may rely on restrictions that have since
  changed. SafePath cannot calculate a new route without the server.
- Cached layers show their cache time. Cached data may be out of date.

## 12. Demo-mode risk

`SAFEPATH_DEMO_MODE` is off by default and is off in the AWS CDK configuration. When it is on, `POST /api/demo/sessions`
is unauthenticated and hands out pre-roled tokens, and the demo console can add and remove closures and reset scenarios.
In the current code, moderator-role demo tokens are issued only when demo mode is on and the profile is `local`; on the
`aws` profile the endpoint refuses moderator sessions (403) but still issues guest and user demo sessions. The deployment
guide (`docs/aws-deployment.md`) warns more broadly that, while demo mode is on, anyone who can reach the API can obtain
demo sessions; treat demo mode as a local rehearsal feature, and turn it off immediately after any shared demo. Demo data
is SIMULATED and must not be mixed with real data. Demo state is reset by the console, and the in-memory repository loses
it when the process restarts.

## 13. Photo uploads

- Photos are stored privately and are only reachable through short-lived (15 minute) signed URLs.
  There is no public listing endpoint and the public report view never exposes the storage key.
- Only JPEG and PNG are accepted, up to 10 MB. The server checks the declared type, the signed
  size cap and the file magic bytes. Filenames from clients are never used; keys are generated.
- **Limitation: EXIF and other metadata are not stripped.** Uploaded images may contain GPS
  coordinates, device details and timestamps. SafePath stores the bytes as received. Before
  production use, strip metadata on upload (for example with an image library in the upload
  handler or an S3-triggered job) and consider re-encoding images.
- Magic-byte checks confirm only the file header, not that the image is fully valid or safe.
- Photo uploads can be disabled with `SAFEPATH_PHOTOS_ENABLED=false`.
- Photos are not scanned for malware or inappropriate content.

## 14. What would be needed before any real use

Official data feeds and agency agreement; a reviewed verification workflow; a security and privacy assessment; EXIF
removal; load and failure testing on real infrastructure; accessibility testing with assistive technology; legal review of
data licenses (ODbL share-alike for derived OSM data, portal and CTA terms); and clear ownership of the service. None of
that exists.
