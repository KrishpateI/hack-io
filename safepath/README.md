# SafePath

> **SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies.**

SafePath is a hackathon prototype for evacuation navigation in downtown Chicago. It computes walking, wheelchair and
driving routes on a real street graph built from public data (OpenStreetMap, the City of Chicago Data Portal, CTA,
USGS 3DEP). Official restrictions are applied as hard exclusions. Community evidence that nobody has confirmed only adds
cost, never a hard block. Every item on screen is labeled Official, Community, Simulated or Unknown source.

What it is not:

- Not an official system. No city, county, state or agency reviewed, endorsed or approved it.
- Not connected to 911, dispatch or any responder. An assistance message is visible to other app users only.
- Every incident in the demo (flood, earthquake debris, blocked stairwell, tornado, fire) is **simulated**. The building
  in the indoor scenario is fictional.
- Routes are algorithm-generated estimates. SafePath never tells you that a route, street, building or exit is safe.
- **Not deployed to AWS.** The machine that produced this repository has no AWS CLI and no credentials. All AWS code is
  tested with stand-ins only. There is no deployed URL.

## Feature status

| Feature | Status |
|---|---|
| Real Chicago graph (61k nodes, 158k directed edges) from CDP + OSM + 3DEP elevation + CTA stations | Done locally. Artifact present in `data/processed/graphs/` |
| Map with basemap, buildings, CTA stations, extraction boundary, route lines, layer toggles | Done locally. Basemap is sparse (roads and water only, no labels). See [Known limitations](#known-limitations) |
| Routing: hard restrictions, then soft penalties, A*, alternatives, plain-language explanation | Done locally (real graph, about 0.1 to 0.5 s per request) |
| Disaster modules: flood, earthquake, tornado (shelter in place), fire, building | Implemented and unit tested. Flood and building scenarios clicked through by hand; the others run through the demo console and automated tests |
| Community reports with Unverified / Corroborated / Disputed / Officially verified states | Done locally. Demo moderator stands in for an official |
| Realtime updates (SSE locally) | Done locally. Report appeared in a second window in about 50 ms (SSE). WebSocket mode was not browser-tested |
| Messages, check-ins, assistance requests | Done locally. No dispatch, no guarantee anyone sees a request |
| Indoor evacuation (fictional 3-floor building) | Done locally |
| Demo admin console and two-window helpers | Done locally (demo mode only) |
| Offline map and queue (service worker, IndexedDB) | Implemented; not tested on a real offline device |
| Optional AI summaries (Bedrock) | Implemented, off by default, never called against Bedrock |
| AWS deployment (CDK, Lambda, DynamoDB, S3, AppSync/WebSocket, CloudFront) | Code and `cdk synth` only. **Not deployed, not verified on AWS** |

## Architecture

A React + MapLibre frontend talks to a FastAPI backend through one REST contract, plus an SSE/WebSocket/AppSync
realtime channel. The backend reaches storage, realtime, auth and blobs through four adapter ports, each with a local
implementation (memory, in-process, demo tokens, filesystem) and an AWS implementation (DynamoDB, AppSync Events or API
Gateway WebSocket, Cognito, S3). The routing engine loads one versioned graph artifact, applies the active incident's
restrictions, then scores what is left.

Full description and diagrams, including the data-flow diagram that covers datasets, official plans, the routing
engine, AWS services, user devices and community reporting: [docs/architecture.md](docs/architecture.md#5-data-flow).
Routing details: [docs/routing-design.md](docs/routing-design.md).

## Quick start (Windows PowerShell)

Prerequisites:

- Python 3.12 or newer (3.14.7 was used here)
- Node.js 24 (v24.22.1, npm 11.19.1 used here)
- Optional, only for deployment later: AWS CLI v2 that you install and configure yourself

### 1. Backend

```powershell
cd safepath\backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Run the API with the demo profile (the secrets below are throw-away dev values; set your own, and never commit them):

```powershell
cd safepath\backend
$env:SAFEPATH_PROFILE = "local"
$env:SAFEPATH_DEMO_MODE = "true"
$env:SAFEPATH_ALIAS_SECRET = "dev-alias-secret-not-for-prod"
$env:SAFEPATH_TOKEN_SECRET = "dev-token-secret-not-for-prod"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# http://localhost:8000/api/health  ->  {"status":"ok","graph_version":"v20261024-2e1f05", ...}
```

Environment variables (read from the environment only; `.env*` files are git-ignored):

| Variable | Meaning |
|---|---|
| `SAFEPATH_PROFILE` | `local` (default) or `aws`. Picks the adapters |
| `SAFEPATH_DEMO_MODE` | `true` enables `/api/demo/*`, moderator demo sessions and the Demo console. Never enable it in a shared deployment: anyone who can reach the API can mint moderator-level demo sessions |
| `SAFEPATH_ALIAS_SECRET` | Secret for the public report/message aliases |
| `SAFEPATH_TOKEN_SECRET` | Signs demo and guest tokens. Without it the local profile creates an ephemeral secret |
| `SAFEPATH_DEMO_AUTOSEED` | Default `true`: seed the simulated scenarios at start |
| `SAFEPATH_REPOSITORY`, `SAFEPATH_PUBLISHER`, `SAFEPATH_AUTH`, `SAFEPATH_BLOBSTORE` | Override one adapter. Local default is in-memory, so **a backend restart wipes all reports and scenario state** |
| `SAFEPATH_CORS_ORIGINS` | Only needed if the frontend calls the API cross-origin |

AWS-only variables are listed in [docs/aws-deployment.md](docs/aws-deployment.md#environment-variables).

### 2. Frontend

```powershell
cd safepath\frontend
npm install
npm run dev          # http://localhost:5173 ; the Vite proxy forwards /api, /sse and /ws to localhost:8000
```

Leave `VITE_API_BASE_URL` empty in development so requests go through the proxy. `VITE_REALTIME_MODE` defaults to `sse`
(`websocket` and `appsync` also exist). Only public identifiers may go into `VITE_*` variables, because they ship in the
browser bundle. See `frontend/.env.example`.

Without a basemap file the dev server falls back to light-use OpenStreetMap raster tiles (development only; production
builds never request them). To use the self-hosted Protomaps extract that is already in the repository:

```powershell
cd safepath
New-Item -ItemType Directory -Force frontend\public\tiles | Out-Null
Copy-Item data\processed\tiles\chicago.pmtiles frontend\public\tiles\
Set-Content frontend\.env.local "VITE_PMTILES_URL=/tiles/chicago.pmtiles`nVITE_PMTILES_KIND=vector"
```

Restart `npm run dev` after changing env files. `frontend\.env.local` and `frontend\public\tiles\*.pmtiles` are git-ignored.

**What you should see** (checked on 2026-10-24 with Playwright and software WebGL in Chromium, screenshots in
`frontend/e2e/screenshots/map-basemap-1440.png` and `map-route-1440.png`): a dark map of the Loop with road and water
lines, building footprints, CTA stations with names, the dashed extraction boundary and attribution. Press **Use sample
points (flood scenario)** and a cyan route line appears within a second.

### 3. Real Chicago graph artifact

The routing graph is a versioned folder under `data/processed/graphs/`, selected by `latest.json`.

| Path | In git? |
|---|---|
| `data/processed/graphs/v20261024-2e1f05/` (nodes, edges, manifest, 4 GeoJSON layers; 61,177 nodes and 157,687 edges in the manifest, 61,212 and 157,779 at runtime after the simulated Clark/Lake station is added) | Present on disk, not git-ignored (about 22 MB). Not yet committed at the time of writing |
| `data/processed/tiles/chicago.pmtiles` (4.3 MB) | Present, not git-ignored |
| `data/processed/elevation/`, `cta_gtfs/`, `summary_report.json`, `fallbacks.json`, `source_records.json` | Tracked |
| `data/processed/cdp_centerlines/`, `osm_streets/`, `osm_pedestrian/`, `osm_buildings/`, `streets/` (intermediate, 2 to 22 MB each) | Git-ignored. Regenerate |
| `data/raw/*` | Git-ignored. Never committed |
| Files over 50 MB | Rejected by `python scripts\check_large_files.py` |

If no graph loads, the app falls back to a SIMULATED synthetic fixture grid (`tests/fixtures/graph`) and says so in
`/api/health`. Do not use the fixture for guidance.

To regenerate everything (needs network; run from `safepath\`, using the venv Python):

```powershell
$py = "backend\.venv\Scripts\python.exe"
& $py scripts\download_chicago_data.py          # CDP centerlines, OSM streets/paths/buildings, CTA GTFS (retries, then falls back to raw copies)
& $py scripts\process_streets.py                # merge into data\processed\streets\segments.jsonl.gz
& $py scripts\import_elevation.py               # USGS 3DEP samples; about 10 minutes cold, quick when cached
& $py scripts\build_graph.py                    # writes a NEW version folder, updates latest.json last; about 41 s warm
& $py scripts\extract_pmtiles.py                # optional; Protomaps range-request extract (use --dry-run first)
& $py scripts\import_emergency_plan.py          # optional; 2007 CBD plan excerpts (needs the pdf extra)
& $py scripts\review_official_sources.py        # optional; official source records
```

The graph build uses the optional pipeline extras only for later steps: `pip install -e ".[pipeline]"`.

### 4. Demo scenarios

Demo mode seeds five SIMULATED scenarios from `data/demo/scenarios.json`. Open **Demo console** in the app (or
`http://localhost:5173/#/demo`). The 3-minute rehearsal is in [docs/demo-script.md](docs/demo-script.md).

## Tests

```powershell
.\scripts\run_all_tests.ps1                 # backend + pipeline pytest, frontend lint/build/vitest, CDK jest + synth; writes docs\test-results.md/.json
.\scripts\run_all_tests.ps1 -Backend        # or -Frontend, -Infra
backend\.venv\Scripts\python.exe -m pytest -q                              # Hypothesis profile ci: 100 examples
backend\.venv\Scripts\python.exe -m pytest -q --hypothesis-profile=demo-check   # 25 examples
cd frontend; npm test                       # vitest + fast-check (numRuns 25 to 100 per property)
cd infrastructure\aws; npm test; npx cdk synth -q    # offline, no AWS calls
```

Latest recorded run: [docs/test-results.md](docs/test-results.md) (1244 passed, 1 failed: an unused-variable lint error
in `offline.test.ts`; the table predates later changes). On 2026-10-24 the frontend suite (25 files, 520 tests) passed
again after the map fix below. The recorded run is a snapshot; run the script again for current numbers.

## Data sources and attribution

- OpenStreetMap contributors, ODbL 1.0 (streets, paths, buildings, and the Protomaps basemap derived from OSM). The
  attribution is always shown on the map.
- City of Chicago Data Portal, street center lines (terms of the portal).
- CTA GTFS feed (stations and routes). Live alerts are optional and not required.
- USGS 3DEP elevation via the National Map (bare-earth terrain, not bridges or tunnels).
- Protomaps daily build (PMTiles extract).
- Official pages (OEMC, UIC Ready, Ready Illinois, Cook County HMP) are linked and briefly paraphrased with their source
  records. The 2007 Chicago Central Business District evacuation plan is shown as a historical reference only.

Counts, URLs, license positions, retrieval dates and limitations: [docs/data-sources.md](docs/data-sources.md).

## API

Endpoint table, auth, rate limits and an example request: [docs/api.md](docs/api.md). Machine-readable contract:
[docs/openapi.json](docs/openapi.json). Regenerate it and the typed client after endpoint changes:

```powershell
backend\.venv\Scripts\python.exe scripts\export_openapi.py            # --check exits 1 if stale
cd frontend; npm run gen:api
```

## AWS deployment

Nothing has been deployed. [docs/aws-deployment.md](docs/aws-deployment.md) holds the substitution matrix, Lambda
packaging, CDK stacks, costs and teardown. In short:

1. Install the AWS CLI v2 and sign in yourself (`aws configure sso` preferred). Do not paste access keys anywhere in
   the repository, a chat or a script. Use a sandbox account and a non-root identity.
2. `.\scripts\deploy.ps1 -Region <region>` is a **dry run by default**: it prints the plan and changes nothing.
3. `-Execute` is required to change anything, and you have to type the stack prefix to confirm.
4. `.\scripts\teardown.ps1` removes only stacks tagged `Project=SafePath`.

The deployed URL is not available because there is no deployment.

## Security checks

```powershell
backend\.venv\Scripts\python.exe scripts\scan_secrets.py     # exit 1 on findings; run before every commit
```

Details and the credentials policy: [docs/security-checks.md](docs/security-checks.md). No penetration test or independent
review has been done. Demo mode must stay off outside a local or sandbox demo.

## Known limitations

Full list: [docs/safety-limitations.md](docs/safety-limitations.md). Top items:

- Prototype with simulated incidents. No official verification workflow exists. "Moderator" means the demo console role.
- No live feeds for closures, flooding, construction or transit status. Street data is a 2026-10-24 snapshot, and the
  City centerline view is dated 2016.
- Most infrastructure attributes are Unknown in the graph (for example 147,323 of 157,687 edges have Unknown
  infrastructure level). Unknown adds a penalty; it is never treated as clear.
- Elevation is bare-earth terrain. It does not represent bridge, tunnel or underground levels. The flood model is a
  rough elevation heuristic with no water depth, and the shoreline term is skipped because the graph has no water layer.
- Routing weights are hand-chosen, not calibrated, and not validated against real evacuations.
- No building entrances, no curb-ramp or elevator data. A step-free route cannot be confirmed as step-free.
- Basemap is sparse: roads and water only, no street labels. Buildings and stations come from API overlays.
- Tornado: inside an active warning area the answer is shelter in place and no outdoor route. No verified shelter is
  named unless a sourced one exists.
- Corroboration needs two distinct people other than the reporter. One corroboration leaves a report Unverified.
- Not deployed to AWS. Realtime latency, DynamoDB index delay, Lambda cold start and Cognito are unmeasured on AWS.
- The WebSocket fallback path expects a query parameter, while the frontend's websocket mode uses a path. Use SSE
  locally; on AWS use polling or AppSync until this is aligned.
- No accessibility test with assistive technology, no privacy review, no terms of service. Not suitable for real
  personal data.

### Fixed while writing this document

- The map container collapsed to zero height in the real browser (`maplibre-gl.css` overrode the Tailwind `absolute`
  class), so the canvas was blank. `frontend/src/maps/MapView.tsx` now sizes it inline.
- `Reset all` / `Reset scenario` left report votes behind. Demo report ids are deterministic, so every corroboration
  after a reset failed with `409 conflict`. `reset_scenario` in `backend/app/services/demo_data.py` now deletes them.
- The demo console's indoor route comparison sent routes without a destination (`400 validation_error`). `DemoAdmin.tsx`
  now adds the building exit as destination.

## Layout

```
safepath/
  README.md  .gitignore
  backend/            FastAPI app (pyproject.toml, .venv git-ignored)
    app/{api,models,services,routing,disasters,communication,auth,adapters}
  frontend/           Vite + React + TypeScript + Tailwind + MapLibre
    src/{components,pages,hooks,services,maps,styles}  e2e/screenshots/
  data/
    raw/ (ignored)  processed/ (graphs, tiles, elevation, summaries)  demo/ (scenarios, indoor building)  sources/ (catalog, plans)
  scripts/            pipeline, deploy.ps1, teardown.ps1, run_all_tests.ps1, scan_secrets.py, export_openapi.py ...
  infrastructure/aws/ CDK app (TypeScript), discovery.json gate file
  tests/              backend/ pipeline/ frontend/ integration/ fixtures/
  docs/               architecture, data-sources, routing-design, safety-limitations, aws-deployment, api, demo-script, pitch ...
  tools/bin/          pmtiles CLI (ignored)
```

Deviations from Requirement 45.1:

- `backend/app/adapters/` is added for the four ports and their local and AWS implementations.
- `backend/app/api/routers/` holds one module per router, auto-registered, so `main.py` stays unchanged.
- `tests/` is split into `backend/`, `pipeline/`, `frontend/`, `integration/` and `fixtures/`.
- `data/raw` is git-ignored; files over 50 MB must not be committed.

## License and attribution

No license file has been chosen for this repository yet; add one before publishing. Data keeps its own terms:
OpenStreetMap data is ODbL 1.0 (attribution "© OpenStreetMap contributors"), Chicago Data Portal and CTA data follow their
publishers' terms, USGS data asks for USGS credit, and Protomaps builds are derived from OSM. Official-site text is
paraphrased briefly with source links. Third-party packages keep their own licenses (see the lock files).

## Definition of done (Requirement 46)

| # | Criterion | Status | Evidence and caveats |
|---|---|---|---|
| 46.1 | Map shows real Chicago street, building and transit data | **Done locally** | Screenshot `map-basemap-1440.png`. Basemap lines come from PMTiles; buildings and CTA stations from API overlays. Not verified on AWS |
| 46.2 | Origin and destination give a route computed on the real graph | **Done locally** | `POST /api/routes/calculate` on the 61k-node graph, two alternatives in 0.1 to 0.5 s; `map-route-1440.png` |
| 46.3 | Switching flood, earthquake, tornado, fire, building applies the matching module and guidance | **Done locally** | Flood and building clicked through; earthquake, tornado, fire through demo scenarios and automated tests |
| 46.4 | A report in one browser shows in a second browser within 5 s, persisted in the cloud backend | **Done locally** (about 50 ms over SSE, in-memory store). **Not verified on AWS** | No cloud backend exists |
| 46.5 | Moderator verify or resolve updates the routable network for requests after 5 s | **Done locally** | Route calls after a confirm include the new restrictions (console shows 0 to 4 restrictions considered). Not verified on AWS |
| 46.6 | Frontend shows the CBD plan as a historical source dated 2007 | **Done locally** (label "Historical reference (2007), not a current evacuation order"; automated tests) | Not re-clicked in this pass |
| 46.7 | Authoritative, community and simulated information distinguished on every item | **Done locally** | Source and status badges; property test for classification |
| 45.5 | Pitch script of 3 minutes or less | Done | [docs/pitch.md](docs/pitch.md) |
| 45.2 | docs/demo-script.md | Done | [docs/demo-script.md](docs/demo-script.md) |
| 45.3 | README with setup, local development, deployment, data import, limitations, deployed URL where permitted | Done; deployed URL: none | This file |
