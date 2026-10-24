# API reference

Machine-readable contract: [openapi.json](openapi.json) (exported from the FastAPI app with
`backend\.venv\Scripts\python.exe scripts\export_openapi.py`; `--check` exits 1 when it is stale; a backend test also
fails on a stale file). The frontend client types in `frontend/src/services/api.generated.ts` are generated from it.
This page adds what the OpenAPI file does not carry: authentication, rate limits, behavior notes and examples.

SafePath is an experimental prototype. It is not an official emergency service, it does not contact 911, and routes are
algorithm-generated estimates. The OpenAPI description says the same.

## Conventions

- Base path `/api`. Local default: `http://localhost:8000`. Real-time endpoints outside `/api` are listed at the end.
- Authentication: `Authorization: Bearer <token>`. Tokens come from `POST /api/auth/guest` (24 h guest session),
  `POST /api/auth/register` and `POST /api/auth/login` (local profile), or Cognito when configured. The role is taken from
  the verified token plus administrator configuration (`roles.yaml`, or Cognito groups). A role sent in a request body is
  rejected. A missing, expired or invalid token gives 401; a valid token with the wrong role gives 403.
  The OpenAPI file declares no `securitySchemes`; auth is enforced by dependencies in the code, and is summarized below.
- Roles: `guest`, `user`, `moderator`, `authorized_official`. "Any role" below means any of these four.
- Rate limits are per user (token subject, else client address) over a sliding window. A rejected call is not counted.
  Exceeding gives 429 with a `Retry-After` header and `retry_after_seconds` in the envelope.
- Request bodies are limited to 1 MB (photo uploads have a separate cap of 10 MB). Larger gives 413.
- Unknown JSON fields are rejected on most write bodies (HTTP 400).
- Once deployed, API Gateway throttles each stage at 50 requests per second, burst 100 (not yet deployed).

### Error envelope

Every error uses the same shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "invalid fields: travel_mode",
    "fields": [
      { "field": "travel_mode", "problem": "unsupported value; supported values: walking, wheelchair, driving" }
    ],
    "retry_after_seconds": null
  }
}
```

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `validation_error`, `invalid_coordinate`, `outside_region` | Bad fields; coordinates not numeric or outside the supported Chicago box |
| 401 | `unauthenticated` | Missing, expired or invalid token (`WWW-Authenticate: Bearer`) |
| 403 | `forbidden` | Role not allowed, or a self-confirmation of your own report |
| 404 | `not_found` | Unknown incident, report, edge, layer; demo endpoints when demo mode is off |
| 409 | `conflict` | Concurrent update that did not settle, or an upload key already used |
| 413 | `payload_too_large` | Body over the cap |
| 415 | `unsupported_media_type` | Photo type or magic bytes do not match |
| 429 | `rate_limited` | Per-user limit; see `retry_after_seconds` |
| 500 | `internal_error` | Unhandled error; details are logged, not returned |
| 503 | `service_unavailable` | Repository, overlays or weights unavailable. Routing fails closed: it never answers without the overlays |

OpenAPI marks the 400 cases as 422 in its response lists (FastAPI's default); the running app returns 400 with the
envelope above.

## Endpoints

Generated from `docs/openapi.json` (52 operations), with auth and rate-limit columns added from the router code.
"Public" means no token is needed.

### Health, regions, graph, sources

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| GET `/api/health` | Public | none | Liveness, graph version and status, fallback marker, profile, routing-weights status |
| GET `/api/regions` | Public | none | Supported region and bounds ("data extraction boundary, not an official evacuation boundary") |
| GET `/api/regions/chicago/metadata` | Public | none | Bounds, CRS, approximate neighborhood reference points, graph version |
| GET `/api/graph/status` | Public | none | Loaded graph version, status, warnings, fallback info |
| GET `/api/graph/edges/near` | Public | none | Edges within `radius` (max 500 m) of `lat`,`lon`; optional `incident_id` adds restriction state |
| GET `/api/graph/edges/{edge_id}` | Public | none | One edge: attributes (Unknown preserved), geometry, restriction state |
| GET `/api/layers/{name}` | Public | none | Static GeoJSON layer of the loaded graph: `buildings`, `cta_stations`, `simulated_station`, `extraction_boundary`, `indoor_building`. `bbox` and `simplify` options; gzip and ETag |
| GET `/api/sources` | Public | none | Source records (organization, URL, classification, retrieval time, usage restrictions) |
| GET `/api/indoor/building` | Public | none | The fictional SIMULATED indoor building |
| GET `/api/transit/alerts` | Public | none | Optional CTA alerts with stale fallback; `available: false` when not configured |

### Routing and incidents

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| POST `/api/routes/calculate` | Public (read-only) | none in the app | Compute up to 3 route options; see below |
| GET `/api/incidents` | Public | none | List incidents |
| GET `/api/incidents/{incident_id}` | Public | none | One incident |
| POST `/api/incidents` | Moderator or Authorized_Official | none | Create an incident |
| GET `/api/notices?incident_id=` | Public | none | Official notices of an incident |
| POST `/api/notices` | Authorized_Official only (Moderator gets 403); audited | none | Create an Official_Notice (requires a Source_Record) |

### Reports and photos

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| GET `/api/reports?incident_id=` | Public | none | Public views: alias, category, location, status, counts. Never the reporter id |
| GET `/api/reports/{report_id}` | Public | none | One report (public view; a signed photo URL when a photo exists) |
| POST `/api/reports` | Any role | 5 per 10 min | Create a report (starts `Unverified`); `client_report_id` makes retries idempotent |
| POST `/api/reports/{report_id}/corroborate` | Any role | 30 votes per 10 min | Confirm; not allowed on your own report (403) |
| POST `/api/reports/{report_id}/dispute` | Any role | 30 votes per 10 min | Dispute |
| POST `/api/reports/{report_id}/moderate` | Moderator or Authorized_Official | none | `action` is `verify` or `resolve`; `verify` is the only route to `Officially_Verified` |
| POST `/api/photos/presign` | Any role | 10 per 10 min | Signed upload URL (15 min), JPEG or PNG, up to 10 MB. Can be disabled with `SAFEPATH_PHOTOS_ENABLED=false` |
| PUT `/api/photos/upload/{key}` | Signed URL (local blob store only) | none | Upload bytes; type, size and magic bytes are checked |
| GET `/api/photos/upload/{key}` | Signed URL (local blob store only) | none | Download via short-lived URL |

On AWS the presigned URL points at S3 directly and these two upload routes are not used.

### Messages, check-ins, events

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| GET `/api/messages?incident_id=` | Public | none | Visible community messages (`since` cursor); labeled "Community" |
| POST `/api/messages` | Any role | 20 per min | Post a message; optional location is coarsened to a 200 m grid |
| POST `/api/messages/{message_id}/hide` | Moderator or Authorized_Official | none | Hide a message |
| GET `/api/checkins?incident_id=` | Optional token (must be valid if sent) | none | Public check-in views; the owner sees their own private view |
| POST `/api/checkins` | Any role | 10 per 10 min | Safety check-in; optional location, coarsened, kept 72 h |
| DELETE `/api/checkins/location` | Any role | none | Remove your stored check-in locations |
| GET `/api/events?incident_id=&since=` | Public | none | Catch-up of stored public events (oldest first, 24 h retention, `limit` up to 500) |

Messages and check-ins have no server-side idempotency (see [safety-limitations.md](safety-limitations.md)).

### Authentication

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| POST `/api/auth/guest` | Public | none | 24 h guest token with an anonymous alias |
| POST `/api/auth/register` | Public (local profile) | none | Create an account (password 8 to 128 characters) |
| POST `/api/auth/login` | Public (local profile) | none | Log in |
| GET `/api/auth/me` | Any valid token | none | Your subject and role |
| PUT `/api/auth/me/privacy` | Any valid token | none | Location sharing preference |
| POST `/api/auth/role-request` | Any valid token | none | Always 403; attempts at privileged roles are audited |

### AI assistant (optional)

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| POST `/api/ai/summarize` | Any role | 10 per min | Summaries of supplied verified facts. Read-only; returns a deterministic template with `ai_used: false` when the model is off or its output fails the guard. Never used by routing |

### Admin and demo

| Method and path | Auth | Rate limit | Purpose |
|---|---|---|---|
| POST `/api/admin/sweep` | Moderator or Authorized_Official | none | Run the sweeper once (expiry and retention) |
| GET `/api/demo/status` | Public | none | Whether demo mode is on |
| POST `/api/demo/sessions` | Public, only when demo mode is on (else 404) | none | Pre-roled SIMULATED tokens; moderator tokens only with demo mode and the local profile |
| GET `/api/demo/scenarios` | Moderator or Authorized_Official, demo mode on | none | Scenario list |
| POST `/api/demo/scenarios/{scenario_id}/start`, `/advance`, `/reset`, `/steps/{step_id}/apply`, `/act` | Moderator or Authorized_Official, demo mode on | none | Run and reset SIMULATED scenarios (`flood_loop`, `earthquake_debris`, `blocked_stairwell`, `tornado_shelter`, `fire_downtown`) |
| POST `/api/demo/reset-all` | Moderator or Authorized_Official, demo mode on | none | Reset all scenarios |
| POST `/api/demo/closures`, DELETE `/api/demo/closures/{restriction_id}` | Moderator or Authorized_Official, demo mode on | none | Add or remove a demo closure or impassable polygon |

### Real-time endpoints (not in the OpenAPI file's `/api` set)

| Path | Auth | Notes |
|---|---|---|
| GET `/sse/incidents/{incident_id}` (in OpenAPI) | Token optional; a supplied token must be valid | Server-sent events; local in-process publisher only |
| WebSocket `/ws/incidents/{incident_id}` | Token optional (`?token=` or header); invalid gives close code 1008 | Local in-process publisher only. On AWS, delivery is AppSync Events or the API Gateway WebSocket (`$connect?incident_id=`), neither tested against real AWS |

Events carry public views only. Use `GET /api/events` after a reconnect.

## Routing request and response

`POST /api/routes/calculate`

| Field | Type | Rules |
|---|---|---|
| `incident_id` | string | Required; unknown incident gives 404 |
| `origin`, `destination` | `{latitude, longitude}` | Required; must be numbers inside the supported Chicago box (W -87.68, S 41.85, E -87.60, N 41.91) |
| `travel_mode` | string | `walking`, `wheelchair` or `driving` |
| `mobility_profile` | string | `standard` or `step_free` |
| `alternatives` | integer | 1 to 3, default 1; fewer may come back |
| `origin_indoor_node_id` | string or null | Start from a node of the fictional indoor building |

Validation order: schema, then coordinates (`invalid_coordinate` or `outside_region`), then incident existence.

### Example

The example below was captured on 2026-10-24 from a local run against the SIMULATED fixture graph with the SIMULATED
`sim-flood-loop` scenario. It is abridged (geometry and edge penalties are cut to a few items, and long limitation texts
are shortened). Costs and ids are from the synthetic grid, not real streets. It illustrates the shape, not a real
recommendation.

Request:

```json
{
  "incident_id": "sim-flood-loop",
  "origin": { "latitude": 41.8827, "longitude": -87.6233 },
  "destination": { "latitude": 41.8781, "longitude": -87.6298 },
  "travel_mode": "walking",
  "mobility_profile": "standard",
  "alternatives": 2
}
```

Response (abridged):

```json
{
  "policy_outcome": "ROUTE_OPTIONS_AVAILABLE",
  "explanation": "2 algorithm-generated route options available. Review the restrictions considered and uncertainty before choosing.",
  "status": "ok",
  "calculated_at": "2026-10-24T21:39:56.805110Z",
  "graph_version": "v20260101-6b3896",
  "data_timestamp": "2026-10-24T21:39:56.766286Z",
  "route_count": 2,
  "label": "algorithm-generated",
  "routes": [
    {
      "route_id": "rt-6c2846d9f125",
      "route_type": "disaster-adjusted",
      "geometry": { "type": "LineString", "coordinates": [[-87.6226, 41.8826], [-87.6258, 41.8826], "..."] },
      "distance_meters": 1151.32,
      "estimated_duration_seconds": 822.39,
      "hazard_flags": ["flood_elevation"],
      "excluded_edges_count": 0,
      "uncertainty_level": "High",
      "policy_outcome": "ROUTE_OPTIONS_AVAILABLE",
      "restrictions_considered": [],
      "edge_penalties": [
        {
          "edge_id": "fx-sim-e059",
          "travel": 189.23,
          "hazard": { "flood_elevation": 80.14 },
          "uncertainty": {
            "unknown_infrastructure_level": 21.19,
            "unknown_pedestrian_access": 21.19,
            "unknown_bridge_flag": 21.19,
            "unknown_tunnel_flag": 21.19
          },
          "report_ids": []
        }
      ],
      "reason_summary": "SIMULATED hazards included. Algorithm-generated route with modeled cost 1436 (travel 822, hazard 285, uncertainty 329). Chosen over rt-53c0277417a9: modeled cost 33 lower, mainly lower data uncertainty ...",
      "sources": ["SIMULATED-DEMO-SOURCE", "READY-ILLINOIS"],
      "confidence_limitations": [
        "This route is algorithm-generated from available map and incident data and may be incomplete or out of date.",
        "Attributes recorded as Unknown add a penalty; they are never treated as favorable.",
        "Flood routing reports lower modeled exposure only. It does not show where water is present and does not use depth measurements.",
        "Water geometry (Chicago River / Lake Michigan shoreline) is unavailable, so the shoreline-proximity penalty was skipped and exposure near water is uncertain."
      ],
      "calculated_at": "2026-10-24T21:39:56.805110Z",
      "status": "ok",
      "label": "algorithm-generated"
    },
    { "route_id": "rt-53c0277417a9", "route_type": "alternate", "...": "..." }
  ],
  "notices": [],
  "missing_data": [],
  "sources": ["SIMULATED-DEMO-SOURCE", "READY-ILLINOIS"]
}
```

Reading the response:

- `policy_outcome` is one of `ROUTE_OPTIONS_AVAILABLE`, `SHELTER_IN_PLACE`, `FOLLOW_OFFICIAL_INSTRUCTIONS`,
  `NO_VERIFIED_ROUTE_AVAILABLE`, `INSUFFICIENT_DATA`. For anything but the first, `route_count` is 0, the single placeholder
  route has empty geometry, and `explanation` and `notices` say why. Do not treat an empty route as a failure to display.
- `excluded_edges_count` counts edges removed by hard restrictions; `restrictions_considered` names them (kind, id,
  source record, simulated flag).
- `edge_penalties` are the exact cost components the search used: `travel`, `hazard` by key, `uncertainty` by key.
- `uncertainty_level` is `Low`, `Medium`, `High` or `Unknown`. On real data `High` is common because many attributes are Unknown.
- `missing_data` lists what blocked a result when the outcome is `INSUFFICIENT_DATA` (for example `graph`,
  `origin_snap_candidate`).
- Every route carries `label: algorithm-generated`. There is no field claiming a route is safe, and clients must not add one.

### Error examples

Unsupported mode:

```json
{ "error": { "code": "validation_error", "message": "invalid fields: travel_mode",
  "fields": [{ "field": "travel_mode", "problem": "unsupported value; supported values: walking, wheelchair, driving" }],
  "retry_after_seconds": null } }
```

Origin outside the box (HTTP 400):

```json
{ "error": { "code": "outside_region", "message": "origin outside the supported Chicago region",
  "fields": [{ "field": "origin", "problem": "outside the supported Chicago region" }], "retry_after_seconds": null } }
```

Weights file invalid (HTTP 503, from the routing service): the detail names the file and field, and `/api/health` shows
`"routing": "invalid_configuration"`. Overlays unreadable (HTTP 503): the service fails closed.

## Caching and static data

`GET /api/layers/{name}` sends `Cache-Control: public, max-age=3600`, an ETag derived from the graph version, and gzip when
requested. The service worker answers `layers`, `sources` and `regions` stale-while-revalidate, and never caches routes,
reports, messages, check-ins, events, incidents, notices, auth, demo or photos.
