# Implementation Plan

## Overview

Fix legend entries that never appear on the map by adding a public, read-only `GET /api/incidents/{incident_id}/map-overlays` endpoint, feeding its hazards and restrictions into `MapLayers`, merging live restriction events on top, making the legend presence-aware, and removing the `elevation` and `graph` entries that have no data source. Exploration tests run first on unfixed code (expected to fail), preservation tests capture current behavior (expected to pass), then the fix, then both are re-run.

Test commands:
- Backend, from `safepath/backend`: `.venv\Scripts\python.exe -m pytest -q` (tests live in `safepath/tests/backend`)
- Frontend, from `safepath/frontend` (unset any `VITE_*` environment variables first): `npx vitest run`, `npm run lint`, `npm run build`

## Tasks

- [x] 1. Write bug condition exploration tests
  - **Property 1: Bug Condition** - Every applicable legend entry is drawn for the selected incident
  - **CRITICAL**: These tests MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: These tests encode the expected behavior - they will validate the fix when they pass after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: The bug is deterministic, so scope the properties to the concrete seeded incidents (`sim-flood-loop`, `sim-earthquake-debris`, `sim-fire-downtown`, tornado incident) and to generated variations of their scenario rows, overlays and notices
  - Bug condition from design: `isBugCondition(input)` holds when (hazards on AND data.flood/debris/fire ≠ ∅ AND the matching source is empty) OR (closures on AND active edge restrictions ≠ ∅ AND `restrictions` source is empty) OR (graph toggle on AND nothing drawn) OR (a legend entry is listed with its toggle on but no data)
  - Backend (`safepath/tests/backend/test_map_overlays_exploration.py`, pytest + hypothesis, seeded fixture graph, demo mode off, run against both `MemoryRepository` and `DynamoRepository(FakeDynamoDB(...))`):
    - `GET /api/incidents/sim-flood-loop/map-overlays` returns 200 with `hazards.flood` containing one feature per `resolved.flood_highlight` feature (fails: route does not exist, `/api/demo/*` is 404 with demo mode off)
    - `GET /api/incidents/sim-earthquake-debris/map-overlays` returns one `hazards.debris` feature per scenario zone id, each with `simulated: true` and label starting with "SIMULATED" (fails)
    - `GET /api/incidents/sim-fire-downtown/map-overlays` returns one `hazards.fire` feature from the SIMULATED `temporary_perimeter` notice (fails)
    - After applying the demo `closure` step and then reading with a fresh client (simulates reload), `restrictions` contains one LineString per active edge with known geometry (fails)
    - After applying `add_debris`, `hazards.debris` features carry `applied_to_routing: true` (fails)
    - Hypothesis: generated scenario rows (random debris zone subsets, Decimal coordinates) and active/removed overlays; assert per-zone and per-notice feature counts and no `/depth/i` keys (fails)
  - Frontend (`safepath/frontend/src/maps/layers/legendLayers.exploration.test.ts`, vitest + fast-check):
    - Build `layerInputs` the way `AppShell.tsx` does today for `sim-fire-downtown` with the notice available; assert the `fire` source has a feature (fails: `hazards` undefined)
    - Feed `restriction_added` events with `kind: "impassable_polygon"`, `edge_ids: []` through `applyRestrictionEvent` + `closureFeatures`; assert a polygon is drawn (fails: zero features)
    - Start a fresh restriction reducer with no live events after a closure exists; assert the `restrictions` source is non-empty (fails)
    - `visibleLegend(defaults)` for the tornado incident must not include `hazard-flood`, `hazard-debris`, `hazard-fire`, `closure` or `verified` (fails: legend filters by toggle only)
    - Turning the `graph` toggle on must either draw features or the toggle must not exist (fails)
    - Mock `/api/layers/simulated_station` as 503; assert the legend omits `sim-station` and a retry is scheduled (fails)
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct - it proves the bug exists)
  - Document counterexamples found (for example "map-overlays route 404 for sim-flood-loop", "fire source empty", "legend lists hazard-fire for tornado") to confirm or refute the root cause analysis
  - Mark task complete when tests are written, run, and failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Routes, non-hazard layers, existing endpoints and public payload safety unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe on UNFIXED code for inputs where `isBugCondition` is false (routes, reports, boundaries, markers, artifact layers that load, no hazards, no restrictions) and record outputs before writing assertions
  - Backend (`safepath/tests/backend/test_map_overlays_preservation.py`, hypothesis, `MemoryRepository` and `FakeDynamoDB`):
    - Route preservation (design Property 5): for each seeded scenario and generated origin/destination pairs, record route responses (route ids, edge sequence, distances, outcome); assert equality with a control app; once the endpoint exists, also assert equality after interleaved endpoint calls and that repository contents are byte-identical before and after
    - Demo console path: applying `closure` and `add_debris` steps writes the same overlays and `restriction_*` events as observed today
    - Existing endpoints: `/api/incidents`, `/api/incidents/{id}`, `/api/notices`, `/api/events`, `/api/layers/*` responses unchanged for seeded data
    - Graph edges endpoint: `/api/graph/edges/near` returns the same geometry (guards the shared `edge_line_geojson` helper move)
  - Frontend (`safepath/frontend/src/maps/layers/preservation.property.test.ts`, fast-check, design Property 6):
    - Random routes, reports (unverified/corroborated/disputed), incident boundaries, start/destination and artifact layers: snapshot the `routes`, `reports`, `incidents`, `markers`, `verified`, `buildings`, `cta-stations`, `simulated-station` FeatureCollections and the report-derived `closures` points from the current builders; assert equal output
    - Reports are never labeled "officially verified"; no verified star without an official source record; SIMULATED labels kept and no depth keys in any built feature
    - Basemap appearance light/dark/satellite keeps every data layer visible
    - `visibleLegend(visible)` without a presence argument keeps the current toggle-only behavior for existing callers
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12_

- [ ] 3. Fix for legend entries that never appear on the map

  - [x] 3.1 Add shared edge geometry helper
    - Move `_geometry` from `safepath/backend/app/api/routers/graph_edges.py` into a shared helper `edge_line_geojson(graph, edge_id)` (for example in `app/routing/graph.py`) and import it from `graph_edges.py`
    - No behavior change for `/api/graph/edges/near`
    - _Bug_Condition: isBugCondition(input) where active edge restrictions exist but no geometry reaches the map_
    - _Preservation: graph edges endpoint output unchanged_
    - _Requirements: 2.6, 2.7, 3.7_

  - [x] 3.2 Implement `build_map_overlays` service
    - New `safepath/backend/app/services/map_overlays.py` with `build_map_overlays(repo, graph, incident, now) -> dict`
    - Read-only: reads `overlays` (active only), `overlay_meta` via `read_overlay_meta`, `notices` for the incident, and the `demo_scenarios` row (pk `scenarios`, sk `incident.scenario_id`) via `demo_data.get_scenario` only when `incident.simulation_flag is True and incident.scenario_id`; never calls a write method; not read by `RoutingService`
    - Edge restrictions: one LineString per known edge with `restriction_id, edge_id, kind, simulated, status, applied_to_routing: true, label`; SIMULATED label "SIMULATED closed road segment (not a real closure)"; unknown edges counted in `limitations.missing_edge_geometry`; cap 2,000 features; omit lines and set `graph_loaded: false` when the graph is not loaded
    - Area restrictions: Polygon/MultiPolygon overlays become hazard features by incident type (earthquake → debris, fire → fire, flood → flood, other → debris), `applied_to_routing: true`
    - Scenario data: `resolved.flood_highlight` → `hazards.flood` with label "SIMULATED flood area (modeled low-lying streets, no depth values)", `applied_to_routing: false`; `resolved.debris_zones` → `hazards.debris` only for zone ids without an active overlay, label "SIMULATED scenario area, not applied to routing", `applied_to_routing: false` (D2); `resolved.perimeter` → `hazards.fire` only as fallback when no SIMULATED perimeter notice with geometry exists; `resolved.closure` ignored while inactive (`INCLUDE_PENDING_SCENARIO_CLOSURES = False`, D2b)
    - Notices: SIMULATED `temporary_perimeter` notices with polygon geometry on fire incidents → `hazards.fire`, label "SIMULATED fire perimeter (not an official instruction)", `notice_id`, `applied_to_routing: true`
    - Sanitize: per-feature property allow-list, drop `/depth/i` keys, `Decimal` → `float`, round coordinates to 6 decimals, validate with existing `_check_geometry` rules (≤ 20,000 points), skip invalid geometry; never emit `users`, `user_ids`, `steps`, `route_*_edge_ids`, `building_properties`, `moderator_user_id`, `source_report_id`; simulated features get `simulated: true`, `classification: "Simulated"`, never `is_official: true`
    - Payload: `incident_id, simulation_flag, overlay_version, generated_at, disclaimer, restrictions, hazards {flood, debris, fire}, limitations {missing_edge_geometry, graph_loaded}`
    - _Bug_Condition: isBugCondition(input) where data.flood, data.debris, data.fire or data.edges ≠ ∅ but drawn(...) = ∅_
    - _Expected_Behavior: expectedBehavior(sources, state) from design (Properties 1 and 7)_
    - _Preservation: routing untouched, repository unchanged by reads (Property 5)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.3, 3.8, 3.9, 3.10_

  - [x] 3.3 Add `GET /api/incidents/{incident_id}/map-overlays` router
    - New `safepath/backend/app/api/routers/map_overlays.py`, auto-registered by `registry.py`
    - Public (no `Depends(require(...))`), works with demo mode off, path param `min_length=1, max_length=64`, 404 for unknown incident, `Cache-Control: no-store`
    - Graph from `app.routing.runtime.get_graph`; graph not loaded still returns 200 with hazards and area restrictions
    - `RepositoryUnavailable` → 503 through the existing error envelope
    - Add backend unit tests in `safepath/tests/backend/test_map_overlays.py` (both `MemoryRepository` and `FakeDynamoDB`): flood, earthquake before/after `add_debris` and after removing one overlay, fire notice and fallback, closure edge lines, graph not loaded, tornado empty, non-simulated incident with `scenario_id` ignores scenario row, 404, 503, allow-list
    - Add hypothesis tests for design Properties 1 and 7 (generated scenario rows with private and depth-like keys, Decimal coordinates, random overlays and notices)
    - _Bug_Condition: no public endpoint delivers hazard geometry with demo mode off_
    - _Expected_Behavior: Properties 1 and 7 from design_
    - _Preservation: existing endpoints unchanged_
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 2.6, 2.7, 3.9, 3.10, 3.11_

  - [x] 3.4 Regenerate OpenAPI and frontend API types
    - Run `scripts/export_openapi.py` to regenerate `safepath/docs/openapi.json`
    - From `safepath/frontend`, run `npm run gen:api` to regenerate `src/services/api.generated.ts`
    - Confirm `safepath/tests/backend/test_openapi.py` passes
    - _Requirements: 2.6_

  - [x] 3.5 Add frontend overlay data path
    - `safepath/frontend/src/services/api.ts`: add `getMapOverlays(incidentId, signal)` using `getJson` and a `MapOverlays` type from the generated schema
    - `safepath/frontend/src/services/mapData.ts`: pure `mergeRestrictions(snapshot, events)` (apply only events with `overlay_version > snapshot.overlay_version`; `restriction_removed` drops every feature with that `restriction_id`; `restriction_added` for unknown ids keeps `edge_ids` pending with `useEdgeCoordinates` as interim fallback) and pure `overlayLayerInputs(merged)` returning `hazards` and `restrictions`
    - New `safepath/frontend/src/hooks/useMapOverlays.ts`: fetch on selection with `AbortController`, discard responses whose `incident_id` differs from the current selection, clear on `null` or incident change, debounced (300 ms) refetch on `restriction_*` / `notice_*` events for the incident, retry with `backoff.ts`, keep last good snapshot
    - Tests: fast-check for design Property 2 (overlay timeline, random snapshot point, shuffled and duplicated events → final active set) and Property 4 (two-incident interleavings → only selected incident's features); unit tests for stale-response discard, refetch on events, clear on deselect
    - _Bug_Condition: closures only from live events without geometry; area restrictions dropped_
    - _Expected_Behavior: Properties 2 and 4 from design_
    - _Preservation: report-derived closure points unchanged_
    - _Requirements: 2.4, 2.6, 2.11, 3.8, 3.11_

  - [x] 3.6 Update layer styles, legend and inspector
    - `safepath/frontend/src/maps/layers/styles.ts`, `types.ts`, `data.ts`, `MapLayers.ts`: remove the `elevation` and `graph` toggles, their layers, sources, legend entries and the `floodHighlight` / `graphEdges` inputs (D3, D4); relabel `hazard-flood` to "SIMULATED flood area (modeled low-lying streets, no depth values)"; feed edge restrictions to the `restrictions` source so the hatched style draws; `closures` carries only report-derived closure points
    - `visibleLegend(visible, presence?)`: with `presence`, require `visible[e.toggle] !== false AND presence[e.id] === true`, preserving `LEGEND_ENTRIES` order; without it, keep old behavior
    - Pure `legendPresence(inputs, artifactStatus)` covering routes, closure, each report status, verified, flood/debris/fire, incident boundary, transit, sim-station, height classes, start, destination
    - `buildHazard` keeps server labels starting with "SIMULATED", otherwise the default label, and strips depth keys
    - `safepath/frontend/src/components/inspect/inspectModel.ts`: remove `elevation-*` and `graph-line` matchers; road popup from `/api/graph/edges/near` keeps logical `layer: 'graph'`
    - Tests: fast-check for design Property 3; one test per legend entry (A routes primary, B alternative, C closure/restricted edge, D/E/F reports, G verified absent without official source, H flood merged with K, I debris dotted, J fire dash-dot, L incident boundary, M CTA station, N SIMULATED station, O-R building classes, S start, T simulation destination) asserting listed exactly when data present and toggle on, and drawn in the expected source; assert no `elevation` or `graph` toggles or entries remain (K, U)
    - _Bug_Condition: legend lists entries without data; flood/low-lying and graph entries have no source_
    - _Expected_Behavior: Property 3 from design_
    - _Preservation: Property 6 non-hazard sources unchanged; SIMULATED labels and disclaimer kept_
    - _Requirements: 2.1, 2.2, 2.7, 2.8, 2.9, 2.12, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.9, 3.12_

  - [x] 3.7 Add artifact status store with retry
    - `safepath/frontend/src/maps/layers/layerData.ts`: status store (`idle | loading | loaded | failed`, feature count) exposed via `useArtifactStatus()` (`useSyncExternalStore`)
    - On failure set `failed` and schedule retry with `backoff.ts` (5 s to 5 min cap); also retry on browser `online` event and when the toggle is turned on again; a failure never blocks other layers
    - Tests: failed layer sets `failed`, legend omits its entry, retry succeeds and the entry reappears
    - _Bug_Condition: station layer 503 leaves legend entry listed with no retry_
    - _Expected_Behavior: Property 3 (failed station absent until retry succeeds)_
    - _Preservation: successful buildings and station layers render as before_
    - _Requirements: 2.10, 3.5_

  - [x] 3.8 Wire AppShell
    - `safepath/frontend/src/AppShell.tsx`: replace `closures: closureFeatures(...)` with `hazards` and `restrictions` from `useMapOverlays` + `mergeRestrictions` + `overlayLayerInputs`; keep `closures` as `buildClosures(null, rep.closurePoints)`
    - Feed the realtime sink into the overlay hook as well as the existing restriction reducer
    - Compute `legendPresence(layerInputs, artifactStatus)` and pass it through `useLayerToggles` → `MapLayerControls` → `Legend`
    - Integration test with a fake map: select flood, earthquake, fire, tornado and stairwell incidents with demo mode off → expected sources and legend entries; switch flood → fire → none clears and redraws
    - _Bug_Condition: AppShell never passes hazards, restrictions or verified data to MapLayers_
    - _Expected_Behavior: expectedBehavior(sources, state) from design_
    - _Preservation: routes, reports, markers, boundaries unchanged_
    - _Requirements: 2.1, 2.3, 2.5, 2.6, 2.9, 2.11, 2.12, 3.1, 3.6, 3.8, 3.11_

  - [ ] 3.9 Verify bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - Every applicable legend entry is drawn for the selected incident
    - **IMPORTANT**: Re-run the SAME tests from task 1 - do NOT write new tests
    - Update only call sites that referenced removed toggles (`elevation`, `graph`) if the test asserted their absence via the new API; keep assertions unchanged
    - Run backend and frontend exploration tests from task 1
    - **EXPECTED OUTCOME**: Tests PASS (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12_

  - [ ] 3.10 Verify preservation tests still pass
    - **Property 2: Preservation** - Routes, non-hazard layers, existing endpoints and public payload safety unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run backend and frontend preservation property tests from task 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Backend full suite from `safepath/backend`: `.venv\Scripts\python.exe -m pytest -q` (includes `test_openapi.py`)
  - Frontend from `safepath/frontend` with `VITE_*` variables unset: `npx vitest run`, `npm run lint`, `npm run build`
  - Playwright e2e suite from `safepath/frontend`: `npm run e2e`
  - Clean up any temporary output files created during verification
  - Ensure all tests pass, ask the user if questions arise

- [ ] 5. Redeploy API Lambda and frontend (ONLY after explicit user approval)
  - Ask the user for explicit approval before running anything; explain this updates the live us-east-2 stack and is reversible by redeploying the previous build
  - API Lambda: `scripts/deploy.ps1 -Region us-east-2 -Profile safepath -Execute` with stages `build` then `deploy` (`cdk deploy safepath-api`); no infrastructure changes expected
  - Frontend: build with `deploy_helpers.py frontend-env` values, then site upload to the existing site bucket
  - Do NOT re-run the seed (`seed_demo_incidents.py` / `demo_data.seed`); the endpoint reads existing `incidents`, `notices`, `overlays`, `overlay_meta` and `demo_scenarios` rows
  - Verify on https://dcmg8cq16ejc1.cloudfront.net: `GET /api/incidents/sim-flood-loop/map-overlays` returns a flood feature, `sim-earthquake-debris` returns two debris features, `sim-fire-downtown` returns a fire feature; selecting each incident in the UI shows the flood area, dotted debris zones and dash-dot fire perimeter with SIMULATED labels and the disclaimer visible
  - Do not commit; the user commits
  - _Requirements: 2.1, 2.3, 2.5, 3.9_

## Notes

- All tasks are required; none are optional.
- Decision D2b (inactive scenario closures are not drawn) deviates from drawing every scenario hazard and is flagged for the user; it is controlled by `INCLUDE_PENDING_SCENARIO_CLOSURES`.
- Task 5 changes a live environment and runs only after explicit user approval. Do not re-run the seed.
- Do not commit. The user commits.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3.1", "3.6", "3.7"] },
    { "id": 2, "tasks": ["3.2"] },
    { "id": 3, "tasks": ["3.3"] },
    { "id": 4, "tasks": ["3.4"] },
    { "id": 5, "tasks": ["3.5"] },
    { "id": 6, "tasks": ["3.8"] },
    { "id": 7, "tasks": ["3.9", "3.10"] },
    { "id": 8, "tasks": ["4"] },
    { "id": 9, "tasks": ["5"] }
  ]
}
```
