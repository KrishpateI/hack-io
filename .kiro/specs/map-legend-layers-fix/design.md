# Map Legend Layers Fix Bugfix Design

## Overview

Several legend entries never appear on the map because the frontend never receives their data. `MapLayers.ts` and `styles.ts` already know how to draw SIMULATED flood, debris and fire areas, low-lying areas, restricted edges, the street graph and verified destinations, but `AppShell.tsx` only passes routes, reports, incident boundaries, event-derived closures and the start/destination markers. The hazard geometry exists only in places the normal user path cannot reach: the `demo_scenarios` table (flood polygon, debris zones), notice geometry (fire perimeter) and overlay rows (area restrictions). Closures are built only from live `restriction_*` events, whose payload carries no geometry.

The fix adds one public, read-only, per-incident endpoint, `GET /api/incidents/{incident_id}/map-overlays`, that assembles everything the map needs for the selected incident from data that already exists in every repository (including DynamoDB). The frontend loads it on incident selection, merges live restriction events on top by `overlay_version`, and feeds the existing layer inputs. The legend is changed to list only entries whose data is present and whose toggle is on. Two legend entries without a viable data source are consolidated or removed (low-lying area merged into flood area; street graph removed). Routing is not touched.

### Decisions applied (easy to revisit)

| ID | Decision | Where to change it later |
|---|---|---|
| D1 | New endpoint `GET /api/incidents/{incident_id}/map-overlays` in a new router module `map_overlays.py` (auto-registered by `registry.py`), public, read-only, works with demo mode off. | `backend/app/api/routers/map_overlays.py`, `backend/app/services/map_overlays.py` |
| D2 | Scenario debris zones are shown for SIMULATED incidents even when the `add_debris` step has not run. When no active overlay exists for a zone, it is labeled "SIMULATED scenario area, not applied to routing" (`applied_to_routing: false`). When the zone is an active area restriction, the overlay version is shown instead (`applied_to_routing: true`) and the scenario copy is suppressed. Routing is unchanged. | `ScenarioSource` in the service; label constants |
| D2b | Inactive scenario closure edges (flood and earthquake `resolved.closure`) are NOT drawn. Drawing an unapplied closure in the red "Confirmed closure" style would claim a confirmation that has not happened, which is more misleading than the debris case. They appear as soon as the closure is an active overlay. This deviates from the requested "same approach" and is flagged for the user. | One flag `INCLUDE_PENDING_SCENARIO_CLOSURES = False` |
| D3 | `resolved.flood_highlight` is the elevation-derived set (lowest 20th percentile of edge mean elevation, or lower-level/tunnel street edges, within 1500 m, buffered 12 m). There is no separate flood-area data. The two entries are merged into one: "SIMULATED flood area (modeled low-lying streets, no depth values)" under the `hazards` toggle. The `elevation` toggle, its three layers and its legend entry are removed. | `LEGEND_ENTRIES`, `TOGGLE_META`, `LAYER_DEFS` |
| D4 | No bbox-limited street-graph endpoint exists (`/api/layers` serves only artifact layers). The `graph` toggle, `graph-line` layer, `graph` source and legend entry are removed. | Same three tables in `styles.ts` |
| D5 | Legend lists an entry only when its toggle is on AND its data is present. Station layers that fail are hidden and retried with backoff. | `visibleLegend`, `layerData.ts` |
| D6 | The endpoint reads only rows that already exist in AWS: `incidents`, `notices`, `overlays`, `overlay_meta`, and the `demo_scenarios` row (pk `scenarios`, sk = `incident.scenario_id`). No re-seed required. No infrastructure change: `api/*` is already routed through CloudFront (caching disabled) to the `$default` Lambda route, and the API Lambda already has read access to every table. | n/a |
| D7 | Redeploying the API Lambda and the frontend is the final task and runs only with explicit user approval. | tasks.md final step |

## Glossary

- **Bug_Condition (C)**: A selected incident and an enabled toggle for which a legend entry's data exists in the backend, but the map draws nothing; or a legend entry listed without any drawable data.
- **Property (P)**: Every legend entry whose data applies to the selected incident and whose toggle is on is drawn with its legend style and SIMULATED labeling; every listed legend entry has data on the map.
- **Preservation**: Routes, reports, incident boundary, buildings, stations, markers, basemap appearance, demo-console behavior and route computation stay exactly as they are.
- **MapOverlays**: The JSON payload returned by the new endpoint (restrictions, hazards, overlay_version, disclaimer).
- **Scenario row**: The `demo_scenarios` item for a SIMULATED incident, holding `resolved.flood_highlight`, `resolved.debris_zones`, `resolved.perimeter`, `resolved.closure`.
- **Area restriction**: An overlay with `kind = "impassable_polygon"` and a polygon `geometry` (for example an applied debris zone).
- **Edge restriction**: An overlay with `edge_ids` (for example a closure).
- **`applied_to_routing`**: Feature property, `true` when the geometry comes from an active overlay or notice that the routing context provider reads; `false` for scenario-only display data.
- **`MapLayers.setInputs`**: Method in `frontend/src/maps/layers/MapLayers.ts` that rebuilds every prop-driven GeoJSON source.
- **`visibleLegend`**: Function in `frontend/src/maps/layers/styles.ts` that picks legend entries.
- **`useArtifactLayers`**: Hook in `frontend/src/maps/layers/layerData.ts` that fetches buildings and station layers.

## Bug Details

### Bug Condition

The bug manifests when a user selects an incident whose hazards, area restrictions or pre-existing closures live in backend storage, because no code path delivers that geometry to `MapLayers`. It also manifests in the legend, which filters by toggle only, so entries are listed with no data behind them.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input = { incidentId, toggles, backendState, artifactStatus }
  OUTPUT: boolean

  data := backendDataFor(input.incidentId, input.backendState)
  // data.flood   = scenario flood_highlight features (SIMULATED incident)
  // data.debris  = scenario debris zones  ∪ active area restrictions
  // data.fire    = SIMULATED temporary_perimeter notice polygons
  // data.edges   = active edge restrictions existing before page load
  missingOnMap :=
       (input.toggles.hazards  AND data.flood  ≠ ∅ AND drawn('flood')  = ∅)
    OR (input.toggles.hazards  AND data.debris ≠ ∅ AND drawn('debris') = ∅)
    OR (input.toggles.hazards  AND data.fire   ≠ ∅ AND drawn('fire')   = ∅)
    OR (input.toggles.elevation AND data.flood ≠ ∅ AND drawn('elevation') = ∅)
    OR (input.toggles.closures AND data.edges  ≠ ∅ AND drawn('restrictions') = ∅)
    OR (input.toggles.graph    AND drawn('graph') = ∅)

  listedWithoutData := EXISTS e IN LEGEND_ENTRIES :
       input.toggles[e.toggle] AND NOT hasData(e, input)

  RETURN missingOnMap OR listedWithoutData
END FUNCTION
```

### Examples

- Select `sim-flood-loop`, hazards on: expected a light-blue SIMULATED flood area around the Loop origin; actual nothing. `resolved.flood_highlight.features[0]` exists in the scenario row.
- Select `sim-earthquake-debris`, hazards on, demo mode off: expected two dotted SIMULATED debris rings beside building footprints; actual nothing. `resolved.debris_zones` has 2 zones, overlays table is empty (seed reported `overlays=0`).
- Demo console applies `add_debris`: overlays now hold two `impassable_polygon` rows; the `restriction_added` event has `edge_ids: []`, so `closureFeatures` emits nothing. Expected the rings in the debris style.
- Select `sim-fire-downtown`, hazards on: expected a 150 m dash-dot SIMULATED fire perimeter at 41.8850, -87.6300; actual nothing although `GET /api/notices` returns the `temporary_perimeter` notice with its Polygon.
- Apply a closure, then reload the page: expected the red hatched edge; actual nothing until another event arrives (catch-up replays at most 100 events from the start of history and restriction payloads carry no geometry).
- Toggle "Street graph (faint)" on: expected faint streets; actual nothing (no source).
- Select the tornado incident: legend still lists flood, debris, fire, closures and verified destination, none of which can appear.
- Edge case: `/api/layers/simulated_station` returns 503: legend still shows "SIMULATED station (hexagon)" and nothing retries until the toggle changes.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Recommended and alternative route lines, labels and selection (3.1).
- Unverified, corroborated and disputed report symbols and wording, never "officially verified" (3.2).
- No verified-destination star without an official source record; no SIMULATED destination shown as a shelter (3.3).
- Tornado incident boundary as a dotted yellow line labeled not an official evacuation boundary (3.4).
- Buildings by height class including the dashed Height Unknown outline; CTA circles; SIMULATED station hexagon when the layers load (3.5).
- Start and Simulation destination markers (3.6).
- Extraction boundary and basemap (3.7).
- Nothing incident-dependent drawn when no incident is selected (3.8).
- SIMULATED labels, no depth values, disclaimer visible (3.9).
- Route responses identical for the same incident state (3.10). The new endpoint never writes and is not read by `RoutingService`.
- Demo console step application unchanged; its closures and debris zones now appear through the same display path (3.11).
- Basemap appearance changes restyle labels only (3.12).

**Scope:**
All inputs that do NOT involve the bug condition must be unaffected. This includes:
- Route calculation requests and their responses.
- Report, message and check-in flows and their realtime events.
- Artifact layer requests that succeed (buildings, stations).
- Demo admin endpoints and seed/reset behavior.
- Existing `GET /api/incidents`, `GET /api/incidents/{id}`, `GET /api/notices`, `GET /api/events` responses.

## Hypothesized Root Cause

1. **No data supplier for existing layer inputs**: `AppShell.tsx` builds `layerInputs` without `hazards`, `floodHighlight`, `restrictions`, `graphEdges` or `verifiedDestinations`. The renderer is correct; the inputs are empty.

2. **Hazard geometry not reachable for normal users**:
   - Flood polygon and debris zones are stored only in the `demo_scenarios` row; the only readers are `/api/demo/*` routes, which return 404 when demo mode is off (the deployed setting).
   - Debris zones become overlays only when the demo `add_debris` step runs.
   - The fire perimeter is a notice with geometry; `listNotices` exists in `api.ts` but no code converts notice geometry into map features.

3. **Closures depend only on live events without geometry**: `useActiveRestrictions` reduces `restriction_added/removed` events; there is no REST snapshot on selection. The event payload has `kind`, `edge_ids`, `simulated`, `overlay_version` and no `geometry`, so `applyRestrictionEvent` keeps only `edge_ids` and `closureFeatures` draws only lines. Area restrictions are dropped. Edge lines go to the `closures` source, so the `restrictions-hatch` layer is never fed.

4. **Legend filters by toggle only**: `visibleLegend(visible)` ignores data presence; `useArtifactLayers` records failures only by deleting the name from its `loaded` set, so there is no status for the legend and no timed retry.

5. **Two legend entries have no viable source**: `flood_highlight` is the low-lying set (no separate flood area exists), and no street-graph source exists in `/api/layers`.

## Correctness Properties

Property 1: Bug Condition - Every applicable legend entry is drawn for the selected incident

_For any_ SIMULATED incident state (scenario row, notices, active and removed overlays, toggle map) where the bug condition holds, the fixed system SHALL return a MapOverlays payload whose `hazards.flood`, `hazards.debris`, `hazards.fire` and `restrictions` collections contain a feature for each scenario flood feature, each scenario debris zone or active area restriction (one per zone id), each SIMULATED temporary-perimeter notice polygon and each active edge restriction edge with known geometry; and the fixed `MapLayers` SHALL put those features into the `flood`, `debris`, `fire` and `restrictions` sources when the matching toggle is on, each with `simulated: true` where applicable, a label starting with "SIMULATED" for simulated items, and no property key matching `/depth/i`.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.7**

Property 2: Bug Condition - Snapshot plus live events equals current server state

_For any_ initial overlay set with version v0 and any sequence of `restriction_added` / `restriction_removed` events (with increasing `overlay_version`, delivered before or after the snapshot, possibly duplicated), the merged restriction set produced by `mergeRestrictions(snapshot, events)` SHALL equal the set of overlays active on the server after the last event, so closures are shown on selection and after reload and stay in sync with later events.

**Validates: Requirements 2.6, 2.4**

Property 3: Bug Condition - Legend lists exactly the entries with data and toggle on

_For any_ toggle map and any presence map (derived from layer inputs and artifact-layer status), `visibleLegend(visible, presence)` SHALL return exactly the entries `e` with `visible[e.toggle] !== false AND presence[e.id] === true`, preserving `LEGEND_ENTRIES` order; the legend SHALL contain no entry for the removed `elevation` and `graph` toggles; and a failed station layer SHALL be absent from the legend until a retry succeeds.

**Validates: Requirements 2.8, 2.9, 2.10, 2.12**

Property 4: Bug Condition - Incident switch isolation

_For any_ two incidents A and B and any interleaving of their snapshot responses and events, after the user selects B the hazard, low-lying, restriction and boundary sources SHALL contain only features whose `incident_id` is B (or be empty), and late responses or events for A SHALL be ignored.

**Validates: Requirements 2.11, 3.8**

Property 5: Preservation - Routing results unchanged

_For any_ seeded incident state and route request, the route response (route ids, edge sequence, distances, outcome) computed after one or more calls to the map-overlays endpoint SHALL equal the response computed without any call, and the repository contents SHALL be byte-identical before and after the calls.

**Validates: Requirements 3.10, 3.11**

Property 6: Preservation - Non-hazard layers render as before

_For any_ layer inputs that contain only routes, reports, incident boundaries, start/destination and artifact layers (no hazards, no restrictions), the fixed `MapLayers.setInputs` SHALL produce the same FeatureCollections for the `routes`, `reports`, `incidents`, `markers`, `verified`, `buildings`, `cta-stations` and `simulated-station` sources as the original code, and the `closures` source SHALL equal the original report-derived closure points.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.12**

Property 7: Preservation - Public payload exposes nothing private and never claims official status

_For any_ scenario row (including `users`, `user_ids`, `steps`, `route_*_edge_ids`, `building_properties`, `moderator_user_id`) and any overlays (including `source_report_id`), the MapOverlays payload SHALL contain only allow-listed keys, no user identifiers, no `source_report_id`, no depth keys, and every feature derived from scenario data or a SIMULATED notice SHALL have `simulated: true`, `classification: "Simulated"` and no `is_official: true`.

**Validates: Requirements 3.3, 3.9**

## Fix Implementation

### Changes Required

Assuming the root cause analysis is correct:

**File**: `safepath/backend/app/services/map_overlays.py` (new)

**Function**: `build_map_overlays(repo, graph, incident, now) -> dict`

**Specific Changes**:
1. **Inputs**: Reads `overlays` (all, then filters `status == "active" and active`), `overlay_meta` (via `read_overlay_meta` for `overlay_version`), `notices` for the incident, and, only when `incident.simulation_flag is True and incident.scenario_id`, the scenario row via `demo_data.get_scenario(repo, incident.scenario_id)`. Read-only; never calls a write method. Uses the graph only to decode edge geometry (reuses `_geometry` from `graph_edges.py`, moved to a shared helper `edge_line_geojson(graph, edge_id)`).
2. **Edge restrictions**: For each active overlay with `edge_ids`, one LineString feature per edge with known geometry: `{restriction_id, edge_id, kind, simulated, status: "active", applied_to_routing: true, label}`. Label: `"SIMULATED closed road segment (not a real closure)"` when simulated, else `"Closed road segment (" + kind + ")"`. Edges unknown to the loaded graph are counted in `limitations.missing_edge_geometry` and skipped. Cap 2,000 edge features.
3. **Area restrictions**: Each active overlay with Polygon/MultiPolygon `geometry` becomes a hazard feature with `applied_to_routing: true`. Hazard kind by incident type: `earthquake → debris`, `fire → fire`, `flood → flood`, other types → `debris`. Label `"SIMULATED debris zone"` (or fire/flood) for simulated overlays, `"Restricted area"` otherwise.
4. **Scenario data (SIMULATED incidents only)**:
   - `resolved.flood_highlight.features` → `hazards.flood`, label `"SIMULATED flood area (modeled low-lying streets, no depth values)"`, keep only `label`, `basis`, `edge_count`, `elevation_percentile`; `applied_to_routing: false` (display data; routing uses its own flood module).
   - `resolved.debris_zones` → `hazards.debris` for zone ids without an active overlay; keep only `zone_id`, `label`, `disclaimer`, `geometry`; label `"SIMULATED scenario area, not applied to routing"`, `applied_to_routing: false`.
   - `resolved.perimeter` → `hazards.fire` only if no SIMULATED `temporary_perimeter` notice with geometry exists (fallback).
   - `resolved.closure` is ignored while inactive (D2b).
5. **Notices**: SIMULATED `temporary_perimeter` notices with Polygon/MultiPolygon geometry on fire incidents → `hazards.fire`, label from notice text prefix `"SIMULATED fire perimeter (not an official instruction)"`, `notice_id`, `applied_to_routing: true`. Non-simulated perimeter notices are not drawn as SIMULATED hazards (out of scope; listed as a limitation).
6. **Sanitizing**: Allow-list properties per feature type; drop any key matching `/depth/i`; convert `Decimal` to `float` (DynamoDB decode); round coordinates to 6 decimals; validate geometry with the existing `_check_geometry` rules (types, ranges, ≤ 20,000 points per geometry); skip invalid geometry.
7. **Payload**:
   ```
   {
     "incident_id", "simulation_flag", "overlay_version", "generated_at",
     "disclaimer": "SIMULATED data for demonstration. Not official. Follow official instructions and call 911 in an emergency.",
     "restrictions": FeatureCollection,          // edge lines
     "hazards": { "flood": FC, "debris": FC, "fire": FC },
     "limitations": { "missing_edge_geometry": int, "graph_loaded": bool }
   }
   ```

**File**: `safepath/backend/app/api/routers/map_overlays.py` (new)

**Function**: `GET /api/incidents/{incident_id}/map-overlays`

**Specific Changes**:
1. Public (no `Depends(require(...))`), path param validated `min_length=1, max_length=64`, 404 when the incident does not exist, `Cache-Control: no-store` (live state).
2. Graph via `app.routing.runtime.get_graph`; if not loaded, still return hazards and area restrictions and set `limitations.graph_loaded = false` (edge lines omitted, not a 503).
3. `RepositoryUnavailable` → 503 through the existing error envelope.
4. Regenerate `docs/openapi.json` and `frontend/src/services/api.generated.ts` with the project's existing generation script.

**File**: `safepath/backend/app/api/routers/graph_edges.py`

**Specific Changes**:
1. Move `_geometry` into a shared helper (for example `app/routing/graph.py: edge_line_geojson`) and import it here. No behavior change.

**File**: `safepath/frontend/src/services/api.ts`

**Specific Changes**:
1. Add `getMapOverlays(incidentId, signal)` using the existing `getJson` helper and a `MapOverlays` type.

**File**: `safepath/frontend/src/hooks/useMapOverlays.ts` (new) and `safepath/frontend/src/services/mapData.ts`

**Specific Changes**:
1. `useMapOverlays(incidentId)`: fetch on selection with an `AbortController`; discard responses whose `incident_id` differs from the current selection; clear to empty on `null` or incident change; refetch (debounced 300 ms) on any `restriction_*` or `notice_*` event for the incident; retry with the existing `backoff.ts` on failure; keep the last good snapshot.
2. Pure `mergeRestrictions(snapshot, events)`: start from snapshot features; apply only events with `payload.overlay_version > snapshot.overlay_version`; `restriction_removed` drops every feature with that `restriction_id` (edge lines and areas); `restriction_added` for an id not in the snapshot keeps its `edge_ids` pending until the debounced refetch delivers geometry (existing `useEdgeCoordinates` stays as the interim fallback for edge lines).
3. Pure `overlayLayerInputs(merged) -> Pick<MapLayerInputs, 'hazards' | 'restrictions'>`.
4. `closureFeatures` is no longer used for edge restrictions; `closures` carries only report-derived closure points (unchanged `buildClosures(null, rep.closurePoints)`).

**File**: `safepath/frontend/src/AppShell.tsx`

**Specific Changes**:
1. Replace the `closures: closureFeatures(...)` input with `hazards` and `restrictions` from `useMapOverlays` + `mergeRestrictions`.
2. Feed the realtime sink into the overlay hook as well as the existing restriction reducer.
3. Compute `legendPresence(layerInputs, artifactStatus)` and pass it through `useLayerToggles` → `MapLayerControls` → `Legend`.

**File**: `safepath/frontend/src/maps/layers/styles.ts`, `types.ts`, `data.ts`, `MapLayers.ts`, `components/inspect/inspectModel.ts`

**Specific Changes**:
1. Remove the `elevation` and `graph` toggles, layers, sources, legend entries and the `floodHighlight` / `graphEdges` inputs (D3, D4). Remove the matching inspector matchers (`elevation-*`, `graph-line`); the road popup from `/api/graph/edges/near` keeps its logical `layer: 'graph'` value.
2. Relabel `hazard-flood` to `"SIMULATED flood area (modeled low-lying streets, no depth values)"`.
3. Change `visibleLegend(visible, presence?)`: with `presence` given, also require `presence[e.id] === true`; without it, keep the old behavior so unrelated callers and tests are unaffected.
4. Add pure `legendPresence(inputs, artifactStatus): Record<string, boolean>`: routes primary/alternative from `inputs.routes`; `closure` from restriction features or closure points; each report entry from the report status present; `verified` from `buildVerified(...)`; each hazard from its collection; `incident` from boundary features; `transit` / `sim-station` / height classes from artifact status `loaded` and feature counts; `start` / `destination` from markers.
5. `buildHazard` keeps the server label when it already starts with `SIMULATED`, otherwise uses the existing default label, and strips depth keys (same as `buildElevation` did).

**File**: `safepath/frontend/src/maps/layers/layerData.ts`

**Specific Changes**:
1. Add a small artifact status store (`idle | loading | loaded | failed`, feature count) with `useArtifactStatus()` (`useSyncExternalStore`).
2. On failure, set `failed` and schedule a retry with `backoff.ts` (capped, for example 5 s to 5 min), also retry on the browser `online` event and when the toggle is turned on again. A failure never blocks other layers.

**Final step (approval required)**: Redeploy the API Lambda and the frontend bundle with the project's existing CDK/deploy commands, only after the user explicitly approves. Re-running `demo_data.seed` is not required because the endpoint reads existing `incidents`, `notices`, `overlays`, `overlay_meta` and `demo_scenarios` rows.

## Testing Strategy

### Validation Approach

First surface counterexamples on the unfixed code (exploration tests that fail), then verify the fix for every legend entry and check preservation with property-based tests. Backend tests live in `safepath/tests/backend` (pytest + hypothesis, seeded with the fixture graph, run against `MemoryRepository` and `DynamoRepository(FakeDynamoDB(...))`). Frontend tests are colocated (`*.test.ts(x)`, `*.property.test.ts` with fast-check) and run with `npx vitest --run`.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Seed all scenarios with demo mode off, then exercise the public API and the frontend data path that `AppShell` uses. Run on the UNFIXED code.

**Test Cases**:
1. **Flood area reachable**: With demo mode off, no public endpoint returns `flood_highlight` geometry for `sim-flood-loop` (will fail on unfixed code: `/api/demo/scenarios` is 404 and no other route exists).
2. **Debris zones reachable**: No public endpoint returns geometry for `sim-earthquake-debris` debris zones (will fail).
3. **Fire perimeter drawn**: Build `layerInputs` the way `AppShell` does for `sim-fire-downtown` and assert the `fire` source has a feature (will fail: `hazards` is undefined).
4. **Area restriction drawn**: Apply `add_debris`, feed the resulting `restriction_added` events through `applyRestrictionEvent` + `closureFeatures` and assert a polygon is drawn (will fail: zero features).
5. **Closures after reload**: Apply a closure, then start a fresh reducer with no live events and assert the `restrictions` source is non-empty (will fail).
6. **Legend without data**: `visibleLegend(defaults)` for the tornado incident includes `hazard-fire` (will fail the "no entry without data" assertion).
7. **Edge case, station 503**: Mock `/api/layers/simulated_station` as 503; assert the legend omits `sim-station` and a retry is scheduled (will fail).

**Expected Counterexamples**:
- Empty `flood`, `debris`, `fire`, `restrictions` sources for seeded SIMULATED incidents.
- Legend entries listed with no data.
- Possible causes: missing layer inputs, no public geometry source, event payloads without geometry, toggle-only legend filter.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL state WHERE isBugCondition(state) DO
  payload := GET /api/incidents/{state.incidentId}/map-overlays
  inputs  := overlayLayerInputs(mergeRestrictions(payload, state.events))
  sources := MapLayers_fixed.setInputs(inputs)
  ASSERT expectedBehavior(sources, state)
END FOR

FUNCTION expectedBehavior(sources, state)
  RETURN (state.toggles.hazards IMPLIES
            |sources.flood|  = |scenarioFlood(state)|
        AND zoneIds(sources.debris) = scenarioZoneIds(state) ∪ activeAreaIds(state)
        AND |sources.fire| = |simPerimeterNotices(state)| OR fallbackPerimeter(state))
     AND (state.toggles.closures IMPLIES
            edgeKeys(sources.restrictions) = activeEdgeKeysWithGeometry(state))
     AND FOR ALL f IN simulatedFeatures(sources):
            f.simulated = true AND startsWith(f.label, "SIMULATED") AND NOT hasDepthKey(f)
     AND FOR ALL f IN sources.debris WHERE NOT f.applied_to_routing:
            f.label = "SIMULATED scenario area, not applied to routing"
END FUNCTION
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT MapLayers_original.setInputs(input).routes   = MapLayers_fixed.setInputs(input).routes
  ASSERT ... same for reports, incidents, markers, verified, closure points
  ASSERT routeResponse_original(input) = routeResponse_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Capture current outputs on the UNFIXED code (snapshot builder outputs for generated inputs and route responses for seeded scenarios), then assert equality after the fix.

**Test Cases**:
1. **Route preservation**: For each seeded scenario and generated origin/destination pairs, the route response before and after calling the endpoint (and before/after the code change) is identical.
2. **Non-hazard source preservation**: Builders for routes, reports, boundaries, markers and verified destinations produce identical collections.
3. **Demo console path**: Applying `closure` and `add_debris` steps still writes the same overlays and events; their geometry now arrives through the endpoint.
4. **Existing endpoints**: `/api/incidents`, `/api/notices`, `/api/events`, `/api/layers/*` responses unchanged for seeded data.

### Unit Tests

Backend (`tests/backend/test_map_overlays.py`):
- Flood incident: one flood feature, merged label, no depth keys, `applied_to_routing: false`.
- Earthquake before `add_debris`: two debris features labeled "not applied to routing"; after `add_debris`: two features with `applied_to_routing: true`, no duplicates; after removing one overlay: that zone falls back to the scenario copy.
- Fire: one fire feature from the notice; fallback to `resolved.perimeter` when the notice is missing.
- Closure applied: hatched edge lines with geometry; graph not loaded: lines omitted, `graph_loaded: false`, 200.
- Tornado: empty hazards and restrictions.
- Non-simulated incident with a `scenario_id`: scenario row ignored.
- Unknown incident: 404; repository failure: 503 envelope.
- Same tests against `DynamoRepository(FakeDynamoDB())` (Decimal coordinates serialize as numbers).
- Payload allow-list: no `users`, `user_ids`, `source_report_id`, `steps`, `moderator_user_id`.

Frontend:
- One test per legend entry (A to T after removals) asserting it is listed exactly when its data is present and its toggle is on, and drawn in the expected source: routes (A, B), closure/restricted edge (C), report symbols (D, E, F), verified destination star (G, absent without official source), flood (H, merged with K), debris (I, dotted), fire (J, dash-dot), incident boundary (L), CTA station (M), SIMULATED station (N), building classes (O to R), start (S), simulation destination (T); `elevation` and `graph` entries and toggles no longer exist (U, K).
- `useMapOverlays`: stale response for a previous incident discarded; refetch on restriction events; clear on deselect.
- `layerData`: failed layer sets `failed`, retry succeeds and the legend entry reappears.

### Property-Based Tests

- **Backend, hypothesis (Property 1, 7)**: Generate scenario rows (random debris zone subsets, extra private keys, depth-like keys, Decimal coordinates), random active/removed overlays and perimeter notices; assert feature counts per zone id and notice, labels, `simulated` flags and the key allow-list.
- **Backend, hypothesis (Property 5)**: For generated route requests on seeded scenarios and random sequences of endpoint calls interleaved with demo steps, route responses equal those of a control app that never calls the endpoint, and repository contents are unchanged by the calls.
- **Frontend, fast-check (Property 2)**: Generate an overlay timeline, pick a snapshot point, deliver events in random order with duplicates; `mergeRestrictions` equals the final active set.
- **Frontend, fast-check (Property 3)**: Random toggle and presence maps; `visibleLegend` equals the filtered, order-preserving list.
- **Frontend, fast-check (Property 4)**: Random interleavings of responses and events for two incidents; sources contain only the selected incident's features.
- **Frontend, fast-check (Property 6)**: Random routes, reports, boundaries and markers; non-hazard sources equal the original builder output.

### Integration Tests

- Full flow per seeded incident with demo mode off (FastAPI TestClient + the frontend data path with a fake map): select incident → endpoint → `MapLayers` sources → legend entries, for flood, earthquake, fire, tornado and stairwell incidents.
- Demo mode on: apply closure and debris steps while the incident is selected; features appear within one refetch and disappear on reset/remove.
- Incident switching flood → fire → none clears and redraws correctly.
- Basemap appearance light/dark/satellite keeps hazard and restriction layers visible.
- Post-deploy smoke check (after user-approved redeploy): `GET https://<cloudfront>/api/incidents/sim-fire-downtown/map-overlays` returns a fire feature; earthquake returns two debris features; no re-seed run.
