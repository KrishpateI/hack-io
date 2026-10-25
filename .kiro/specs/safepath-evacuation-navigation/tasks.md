# Implementation Plan: SafePath Evacuation Navigation

## Overview

This plan implements SafePath in the priority order the project requires: (1) foundation and map, (2) real Chicago street graph pipeline, (3) emergency plan ingestion and source catalog, (4) disaster-aware routing core, (5) local-profile backend behind adapter ports, (6) community reports, realtime, messages and check-ins, (7) indoor scenario and demo scenarios, (8) AWS discovery, CDK and deployment tooling, (9) frontend screens and polish, (10) offline, optional AI, documentation and demo script.

Implementation languages follow the design: Python 3.12 (FastAPI, Pydantic v2, Hypothesis, pytest) for the backend and Data_Pipeline, TypeScript (React, Vite, Tailwind, MapLibre GL JS, vitest, fast-check, Playwright) for the frontend, and TypeScript for AWS CDK.

Every task builds on earlier tasks and ends wired into the running app. Property-based tests are optional sub-tasks (marked `*`), one per design Correctness Property, each tagged `# Feature: safepath-evacuation-navigation, Property {n}: {title}`.

## Tasks

- [x] 1. Project foundation and map
  - [x] 1.1 Scaffold the repository and backend skeleton
    - Create the `safepath/` layout from Requirement 45.1 (frontend, backend/app/{api,models,services,routing,disasters,communication,auth}, backend/app/adapters, data/{raw,processed,demo,sources}, scripts, infrastructure/aws, tests, docs) and a `.gitignore` excluding `data/raw`, any file over 50 MB, `node_modules`, virtualenvs and secrets
    - Create `backend/pyproject.toml` (pinned FastAPI, Pydantic v2, Mangum, uvicorn, pytest, Hypothesis, pyyaml) and Hypothesis profiles `ci` (max_examples 100, deadline None) and `demo-check` (25) in `tests/backend/conftest.py`
    - Implement `create_app(settings)` with a `Settings` class driven by `SAFEPATH_PROFILE=local|aws`, a router registry that auto-includes every module in `backend/app/api/routers/` (so later tasks never edit `main.py`), and `GET /api/health` returning status, graph version placeholder and profile
    - Add `README.md` skeleton documenting layout deviations (adapters folder, tests subfolders) and local run commands for PowerShell
    - _Requirements: 29.7, 42.1, 42.6, 45.1, 45.3, 7.9_
  - [x] 1.2 Scaffold the frontend (Vite + React + TypeScript + Tailwind)
    - Create `frontend/` with Vite, React, Tailwind, vitest, fast-check, React Testing Library, Playwright config and ESLint
    - Add `styles/tokens.ts` and Tailwind theme: dark navy/charcoal base, cyan/blue routes, red closures, orange unverified, green verified resources
    - Add the root layout containing a non-dismissible `DisclaimerBanner` with the exact Disclaimer_Banner text, rendered on every screen at 320 to 2560 px, and a `Header` placeholder with SIMULATED indicator slot
    - Add `.env.example` with public identifiers only (API base URL, realtime mode) and no secrets
    - _Requirements: 1.1, 1.5, 34.1, 36.1, 31.2_
  - [x] 1.3 Implement shared labeling and status components
    - Implement `services/labels.ts` `classify(item) -> Official | Simulated | Community | Unknown source` and `labelAttribute()` that renders `Unknown` for missing values
    - Implement `SourceLabel`, `SimulatedBadge`, `StatusBadge` (icon + text + color), and `Emergency911Panel` (911 guidance, visually separate, reachable in one interaction from every screen, with no claim of dispatching or notifying 911)
    - Show the "SIMULATED" header indicator whenever an active Incident has `simulation_flag` true
    - _Requirements: 1.2, 1.3, 1.4, 1.6, 10.4, 10.5, 36.2, 36.5_
  - [x] 1.4 Implement the Map_View with basemap, attribution and extraction boundary
    - Implement `maps/MapView.tsx` with MapLibre GL JS centered on downtown Chicago, PMTiles protocol support in `maps/pmtiles.ts`, and a configurable style that falls back to light-use OSM raster tiles in development only when no PMTiles file is present (document the OSM tile policy)
    - Always render OpenStreetMap attribution on the Map_View
    - Render the Chicago_Extraction_Area (N 41.91, S 41.85, W -87.68, E -87.60) with a dashed distinct line style and the label "Data extraction boundary, not an official evacuation boundary"
    - Add `LayerToggles` that change layer `visibility` immediately
    - _Requirements: 2.3, 3.8, 34.3, 46.1_
  - [x] 1.5 Write property test and component tests for content classification and the disclaimer
    - **Property 33: Content classification and labeling** (fast-check, numRuns 100)
    - Component tests: disclaimer present and verbatim at widths 320 and 2560 with no dismiss control, SIMULATED header indicator, 911 panel reachable in one interaction
    - **Validates: Requirements 1.1, 1.3, 10.4, 10.5, 10.8, 35.4, 36.5**
  - [x] 1.6 Write palette contrast test and wording lint
    - Test every text/background token pair in `tokens.ts` for contrast ratio of at least 4.5:1
    - Add a lint test scanning UI strings and API explanation templates for forbidden claims ("safe route", "guaranteed", "certified", "approved", "flood-safe", dispatch/911 integration claims) outside the disclaimer
    - _Requirements: 1.5, 1.6, 1.7, 36.3, 17.7_

- [ ] 2. Checkpoint - Foundation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Real Chicago street graph pipeline
  - [x] 3.1 Implement geographic utilities
    - In `backend/app/geo.py` implement bounds constants, boundary-inclusive `classify_coordinate` (invalid, outside-region, inside) identifying origin, destination or both, bbox clipping, haversine, and `building_height_class(height, floors)` with zero/negative/non-numeric treated as absent
    - _Requirements: 2.1, 2.4, 2.5, 6.2, 6.3_
  - [x] 3.2 Write property test for clipping and region bounds
    - **Property 20: Geometry clipping and region bounds are exact and boundary-inclusive**
    - **Validates: Requirements 2.1, 2.4, 2.5, 23.8**
  - [x] 3.3 Write property test for building height class
    - **Property 21: Building height class is a pure, total function of source data**
    - **Validates: Requirements 6.2, 6.3**
  - [x] 3.4 Define Graph and provenance models and the versioned artifact format
    - Implement Pydantic models `Node`, `Edge` (all attributes in Requirement 12.2, `Unknown` literal never null/false/0), `SourceRecord`, `ReuseDetermination`, and the Data_Classification enum in `backend/app/models/graph.py` and `provenance.py`
    - Validators: `distance_meters > 0`, `estimated_travel_time_seconds > 0`, endpoints exist, Level_Transition required when node levels differ
    - Implement artifact writer/reader (`nodes.jsonl.gz`, `edges.jsonl.gz`, `manifest.json` with sha256 and counts, `layers/*.geojson`), version id `v{UTC date}-{short hash}`, no overwrite of prior versions, `latest.json` updated last and atomically
    - _Requirements: 12.1, 12.2, 12.3, 12.5, 12.6, 12.7, 7.8, 10.1_
  - [x] 3.5 Write property test for graph structural invariants
    - **Property 18: Graph structural invariants** (positive distance/time, endpoints present, edges to absent nodes excluded with edge_id warning, level changes only via Level_Transition)
    - **Validates: Requirements 12.5, 12.7, 12.8, 21.2, 44.9, 5.6**
  - [x] 3.6 Build the Data_Pipeline framework
    - In `scripts/pipeline/` implement the fixed stage runner (check_availability, save_raw, reproject EPSG:26916, clip, clean_geometry, normalize, build_nodes_edges, save_outputs), fetch with 3 attempts and 60 s timeout, previous-copy fallback with timestamp warning, missing-source recording, per-stage failure isolation, fatal exit only when no street source exists in any form (leaving the saved Graph unchanged), and `summary_report.json` (feature count, URL, timestamp, acquisition status, warnings)
    - Auto-create a Source_Record for each retrieved dataset (OSM record with ODbL license and attribution restriction)
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 3.7_
  - [x] 3.7 Write property test for pipeline source resolution
    - **Property 42: Pipeline source resolution is deterministic**
    - **Validates: Requirements 3.5, 7.4, 7.5, 7.6, 7.7**
  - [x] 3.8 Implement `download_chicago_data.py`
    - Download Chicago Data Portal centerlines (6imu-meau), OSMnx streets/walkways/pedestrian paths, OSM building footprints for the Chicago_Extraction_Area, and the CTA GTFS static feed; save to git-ignored `data/raw`
    - Handle unavailable sources using the framework fallbacks (previous copy, else missing) and write each fallback and limitation to `docs/safety-limitations.md` input data (`data/processed/fallbacks.json`); document a reproduce command in the README
    - Extract CTA stations (location, id, name, routes) inside the extraction area
    - _Requirements: 3.1, 3.2, 5.1, 6.1, 7.1, 7.4, 7.5, 7.9_
  - [x] 3.9 Implement `process_streets.py`
    - Merge Chicago Data Portal and OSM segments, extracting geometry, source id, road class, one-way, pedestrian access, bridge, tunnel, layer; assign `infrastructure_level` only where source identifies it and flag river crossings by source data
    - Store absent attributes as `Unknown` (never defaults); apply documented source precedence (CDP geometry and name, OSM access/bridge/tunnel/layer), record retained source and a conflict warning
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 7.1_
  - [x] 3.10 Write property test for attribute normalization
    - **Property 19: Missing attributes normalize to Unknown, never defaults**
    - **Validates: Requirements 3.4, 4.4, 4.5, 12.3**
  - [x] 3.11 Implement `import_elevation.py`
    - Sample USGS 3DEP (py3dep or TNM ImageServer) at every Node, store meters with NAVD 88 datum, set `elevation_start`/`elevation_end` per Edge and `elevation_confidence`, force Low/Unknown for bridge/tunnel/elevated/lower-level edges, record `Unknown` (never zero) when unavailable and report the Unknown count; compute elevation percentiles for the manifest
    - If 3DEP is unreachable, continue with Unknown elevations and a documented fallback warning
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 7.1_
  - [x] 3.12 Implement `build_graph.py`
    - Build Nodes and Edges (directed one-way edges for vehicles), drop edges with missing endpoints with an `edge_id` warning, add building entrances, the simplified two-level station model (street and platform joined only by Level_Transition edges, classified Simulated), building footprints layer with `Building_Height_Class` and only source-present attributes, stations and boundary layers
    - Write the versioned artifact, manifest and `summary_report.json`
    - _Requirements: 2.2, 5.6, 6.4, 7.1, 7.8, 12.1, 12.5, 12.6, 12.8, 3.6_
  - [x] 3.13 Implement the Graph runtime loader and region/graph endpoints
    - `routing/graph.py`: `Graph.load(source)` with checksum validation, array-based adjacency, lazy STRtree, `snap(lat, lon, mask)`; fall back to previous version on checksum failure and report `INSUFFICIENT_DATA` when none loads
    - Routers: `GET /api/regions`, `GET /api/regions/chicago/metadata` (bounds, neighborhoods, CRS, graph version), `GET /api/graph/status`; update `/api/health` with loaded graph version
    - _Requirements: 2.6, 42.1, 42.5, 42.6, 43.8, 13.7_
  - [x] 3.14 Create the fixture graph and documented fallback dataset
    - Generate a small synthetic Chicago-like graph artifact in `tests/fixtures/graph/` and a `scripts/make_fixture_graph.py` so development, CI and demos work when real downloads are unavailable; label it Simulated and document the limitation in `data/processed/fallbacks.json`
    - _Requirements: 7.4, 7.5, 45.6, 45.7_
  - [x] 3.15 Write artifact smoke test and pipeline unit tests
    - Fixture-based tests for Unknown handling, conflict warnings, manifest checksums, per-neighborhood routable Node presence (Loop, West Loop, Riverwalk, Clark/Lake, Millennium Park, DePaul Loop, UIC, UIC-downtown corridor)
    - _Requirements: 2.2, 3.4, 3.5, 7.3, 7.8_

- [x] 4. Emergency plan ingestion and source catalog
  - [x] 4.1 Implement the Source_Catalog service and `GET /api/sources`
    - `services/sources.py` with case-insensitive substring search on organization/title, exact filters on Data_Classification and verification status, 400 listing permitted values for invalid filter values, response within 2 s
    - Store catalog in `data/sources/catalog.json` and enforce a Source_Record on every rule, zone, closure, shelter, destination, notice and dataset
    - _Requirements: 10.1, 10.2, 10.7_
  - [x] 4.2 Write property test for catalog search and filter
    - **Property 34: Source catalog search and filter**
    - **Validates: Requirements 10.2, 10.7**
  - [x] 4.3 Implement `import_emergency_plan.py` for the 2007 CBD Evacuation Plan
    - Download the CBD_Plan PDF and store a local copy only if terms permit (record the determination), classify as Historical with the 2007 date
    - Extract Evacuation Levels I-III, activation conditions, shelter-in-place vs evacuation criteria, activation authority, CBD boundaries, hot/cold zones, transportation coordination, closure procedures, CTA/Metra coordination, Assembly/Transportation Centers, shelters, communication procedures, agency roles, each with page number and section heading; record not-found topics
    - Record the approximate CBD boundary (Division to Roosevelt, Halsted to Lake Michigan) labeled approximate; set `cbd_derived=true` on all records
    - Add search records for appendices and newer plan versions (date and result); leave content unpopulated when not located
    - If the PDF is unreachable, reuse a previous copy or record the plan as unavailable, continue, and document the fallback
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 7.1_
  - [x] 4.4 Implement official source review (OEMC, UIC, UIC Ready, Ready Illinois, Cook County HMP)
    - Add `scripts/review_official_sources.py` that creates Source_Records for the five required URLs, checks reachability (60 s timeout, record unreachable with check date and keep the prior record), writes UIC reuse determinations, excludes UIC access codes, security procedures, restricted areas and unreviewed floor plans, and classifies Cook HMP areas as long-term mitigation
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7_
  - [x] 4.5 Build the Data Sources and Limitations screen and official info links
    - `pages/SourcesLimitations.tsx` listing all Source_Records (filter/search via `/api/sources`) and documented limitations; selected-feature popup showing source organization, URL, title, dates, classification, verification status and page/section within 1 s
    - Official information links (OEMC, Ready Illinois, UIC Ready) in the left sidebar component and a `HistoricalLabel` component with the text "Historical reference (2007), not a current evacuation order" used next to CBD-derived content
    - _Requirements: 8.10, 9.6, 10.3, 10.6, 46.6_
  - [x] 4.6 Write unit tests for CBD extraction and source review
    - Recorded-fixture tests for page/section capture, not-found topics, unavailable appendices, unreachable URL handling, UIC exclusions
    - _Requirements: 8.4, 8.5, 8.7, 8.8, 9.3, 9.7_

- [ ] 5. Checkpoint - Data and sources
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Disaster-aware routing core
  - [x] 6.1 Define domain and routing models plus request validation
    - `models/domain.py`: Incident, OfficialNotice, HazardReport (routing-relevant fields), Rule, Restriction/RestrictionCause, enums (PolicyOutcome, IncidentType, Verification_Status, TravelMode, MobilityProfile, UncertaintyLevel), PolicyDecision
    - `models/routing.py` and `routing/validation.py`: route request/response models; validate coordinates and region, `alternatives` integer 1..3 (default 1), supported `travel_mode`/`mobility_profile` listing supported values, unknown incident 404
    - _Requirements: 13.3, 13.11, 15.1, 43.1, 43.2, 43.3, 43.5, 43.6, 43.7, 11.1_
  - [x] 6.2 Implement the weights loader and validator
    - `routing/cost.py` weights loading from `routing_weights.yaml` (design defaults), validation at startup and reload (missing file, negative or non-numeric weights, `w_unknown_attr > 0`), and a degraded state where `/health` reports `routing: invalid_configuration` and route requests return 503 naming the file and field
    - _Requirements: 13.2, 13.12_
  - [x] 6.3 Define the DisasterModule protocol, IncidentContext, RestrictionSet and EdgeMask
    - `disasters/base.py` with `DisasterModule` (policy_override, hard_restrictions, edge_penalty, route_filter, uncertainty_floor, advisories), module registry, `IncidentContext`, `RestrictionSet` (blocked_edges with causes, overlay_version) and numpy `EdgeMask`
    - _Requirements: 14.2, 11.3_
  - [x] 6.4 Implement the RestrictionResolver (hard step)
    - `routing/restrictions.py`: resolve confirmed closures (Officially_Verified or demo Moderator-confirmed), official prohibited/impassable polygons using exact LineString `intersects`, notice road-direction restrictions as directed constraints, module hard restrictions; exclude Unverified/Corroborated/Disputed reports; cache by (overlay_version, incident_id, graph_version); report `excluded_edges_count`; detect isolated origin/destination
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 11.7_
  - [x] 6.5 Implement the CostModel (soft step)
    - `routing/cost.py`: `Route_Cost = travel_cost + disaster_hazard_penalty + uncertainty_penalty`, length-scaled, non-negative, strictly positive penalty per Unknown attribute, Low/Unknown elevation confidence, Unknown `mobility_accessibility` under step-free profile, disputed report hits; record `PenaltyComponents` per edge; step-free profile excludes known-inaccessible edges
    - _Requirements: 13.2, 13.5, 13.8, 12.4, 17.4_
  - [x] 6.6 Implement A* and the Dijkstra reference search
    - `routing/search.py`: A* with admissible haversine/`v_max` heuristic over the masked graph honoring directed edges and `route_filter`, plus a reference Dijkstra used by tests
    - _Requirements: 13.1, 12.6, 11.7_
  - [x] 6.7 Implement alternatives
    - `routing/alternatives.py`: penalty-based k-alternatives (inflate factor, max attempts), distinctness by at least one edge, Yen-style spur fallback, report true uninflated cost and the count returned; hard restrictions in the mask for all passes
    - _Requirements: 13.4_
  - [x] 6.8 Implement explanation and uncertainty level
    - `routing/explain.py`: restrictions considered, excluded edge count, per-edge penalty components, reason summary relative to other routes, data timestamp, sources, `algorithm-generated` label, uncertainty level (Low <5%, Medium <20%, High otherwise, Unknown with no coverage) raised only by module floors; flood wording limited to "lower modeled exposure"
    - _Requirements: 15.1, 15.2, 15.3, 15.6, 1.7, 17.7_
  - [x] 6.9 Implement the Rules_Engine
    - `disasters/rules_engine.py`: pure `evaluate(ctx) -> PolicyDecision` with eligibility filter (no Source_Record, CBD-derived, Cook HMP area as live boundary, expiry + 60 s, not yet effective) and precedence (conflicting notices, shelter-in-place, unmodelable instruction including fire, module override, insufficient data, no route, route options); incident-specific destination ranked ahead of non-official ones
    - _Requirements: 11.1, 11.2, 11.4, 11.5, 11.6, 11.8, 11.9, 8.9, 9.5, 10.8, 20.4_
  - [x] 6.10 Implement the Flood module
    - `disasters/flood.py`: hard restrictions for confirmed closures, modeled impassable polygons and officially reported flooded segments; tunnel/underpass, lower-level and shoreline-proximity penalties (50 m); elevation penalty non-increasing in mean elevation; uncertainty penalty for Low/Unknown elevation confidence; no-evidence case sets `uncertainty_floor` High; Simulated flood polygons with no depth values
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8_
  - [x] 6.11 Implement Earthquake, Tornado and Fire modules
    - `disasters/earthquake.py`: hard restrictions for bridge closures, collapse and debris zones, notice-identified segments; high-rise proximity penalty only within 50 m of buildings tied to active debris zone/notice/report, never from `Building_Height_Class` alone; confirmed structural info replaces heuristics
    - `disasters/tornado.py`: SHELTER_IN_PLACE override when origin is inside an active warning boundary, official shelter plus Source_Record or "no verified shelter available", outdoor edge exclusion, resume on status change
    - `disasters/fire.py`: hard restrictions for verified perimeters/prohibited areas and confirmed blocked streets; unverified perimeters as penalty plus warning; 100 m smoke/fire report penalty
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 20.1, 20.2, 20.3, 20.6_
  - [x] 6.12 Implement RoutingService and `POST /api/routes/calculate`
    - `routing/service.py` orchestrating validate, load context (via a `ContextProvider` protocol with in-memory implementation), Rules_Engine, data-sufficiency check, restriction resolution, endpoint snapping, cost, A*, alternatives, explanation; non-route outcomes return HTTP 200 with empty geometry and explanation; overlay read failure fails closed with 503; works with AI disabled; computes routes at request time with no precomputed route
    - _Requirements: 13.1, 13.3, 13.6, 13.7, 13.9, 13.10, 15.5, 43.2, 43.4, 43.8, 11.3, 46.2, 46.3_
  - [x] 6.13 Create shared Hypothesis strategies
    - `tests/backend/strategies.py`: `graphs()`, `restriction_sets(graph)`, `notices()`, `incidents()`, `coordinates()`, `raw_segments()` per the design
    - _Requirements: 44.5, 44.8_
  - [x] 6.14 Write property test for hard restrictions
    - **Property 1: Hard restrictions are never traversed** (at least 100 examples)
    - **Validates: Requirements 14.1, 14.3, 14.4, 14.6, 17.1, 17.5, 18.1, 18.2, 20.1, 20.2, 44.5**
  - [x] 6.15 Write property test for search optimality
    - **Property 2: Search returns an optimal path** (A* vs reference Dijkstra and brute force)
    - **Validates: Requirements 13.1, 44.1, 44.2**
  - [x] 6.16 Write property test for restriction add/remove
    - **Property 3: Restriction add then remove restores routing**
    - **Validates: Requirements 14.7, 16.3, 21.4, 24.6, 44.2**
  - [x] 6.17 Write property test for directed edges
    - **Property 4: Directed edges are traversed only in their permitted direction**
    - **Validates: Requirements 11.7, 12.6, 44.3**
  - [x] 6.18 Write property test for alternatives
    - **Property 5: Alternatives are valid, distinct and counted** (at least 100 graphs)
    - **Validates: Requirements 13.4, 44.8**
  - [x] 6.19 Write property test for unreachable or isolated endpoints
    - **Property 6: Unreachable or isolated endpoints yield NO_VERIFIED_ROUTE_AVAILABLE** (outdoor graph; indoor variant added in task 11.7)
    - **Validates: Requirements 13.6, 14.8, 44.4**
  - [x] 6.20 Write property test for unconfirmed evidence
    - **Property 7: Unconfirmed evidence is a penalty, never a Hard_Restriction**
    - **Validates: Requirements 14.3, 14.5, 16.5, 20.6**
  - [x] 6.21 Write property test for cost decomposition
    - **Property 8: Route_Cost decomposes into non-negative recorded components**
    - **Validates: Requirements 13.2, 13.5**
  - [x] 6.22 Write property test for Unknown data penalties
    - **Property 9: Unknown or low-confidence data is never treated as safe**
    - **Validates: Requirements 12.4, 13.8, 17.4, 44.7**
  - [x] 6.23 Write property test for step-free profile
    - **Property 10: Step-free profile excludes known-inaccessible edges**
    - **Validates: Requirements 13.8**
  - [x] 6.24 Write property test for flood penalties
    - **Property 11: Flood penalties are monotone in elevation and positive for exposed infrastructure**
    - **Validates: Requirements 17.2, 17.3, 17.8**
  - [x] 6.25 Write property test for earthquake proximity penalty
    - **Property 12: Earthquake proximity penalty depends on hazard association, not height class**
    - **Validates: Requirements 18.3, 18.4**
  - [x] 6.26 Write property test for fire smoke penalty
    - **Property 13: Fire smoke penalty applies within 100 m of unconfirmed fire reports**
    - **Validates: Requirements 20.3**
  - [x] 6.27 Write property test for tornado behavior
    - **Property 14: Tornado warning yields shelter-in-place and no outdoor edges**
    - **Validates: Requirements 19.1, 19.5, 19.7**
  - [x] 6.28 Write property test for policy precedence
    - **Property 15: Policy precedence follows the documented order**
    - **Validates: Requirements 11.4, 11.5, 11.6, 11.8, 19.6, 20.4, 44.6**
  - [x] 6.29 Write property test for ineligible records
    - **Property 16: Ineligible records never influence evaluation**
    - **Validates: Requirements 8.9, 9.5, 10.8, 11.9**
  - [x] 6.30 Write property test for the route response contract
    - **Property 17: Route responses satisfy the response contract**
    - **Validates: Requirements 15.1, 15.5, 15.6, 43.2, 43.3**
  - [x] 6.31 Write unit and example tests for routing
    - Known 6-node graph shortest path, weights file validation (missing, negative, non-numeric), one example per Policy_Outcome, tornado with and without an official shelter, 200-request performance smoke on the fixture graph (p95 under 5 s)
    - _Requirements: 13.10, 13.12, 44.1, 19.3, 19.4_

- [ ] 7. Checkpoint - Routing core
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Local-profile backend with adapter ports
  - [x] 8.1 Define adapter ports and local implementations
    - `backend/app/adapters/`: `Repository`, `RealtimePublisher`, `AuthProvider`, `BlobStore` protocols per the design; `MemoryRepository` (default), `SqliteRepository`, `LocalBlobStore` (signed expiring URLs), `InProcessPublisher`, and a factory selecting implementations from `SAFEPATH_PROFILE` and per-port overrides
    - `transact` is all-or-nothing and raises `RepositoryUnavailable`; `update_cas` and `put(if_absent)` raise `Conflict`
    - _Requirements: 29.7, 30.9_
  - [x] 8.2 Implement persistent record models, ids and public/private serializers
    - Models for Users, Incidents, HazardReports, ReportVotes, Messages, CheckIns, OfficialNotices, Overlays, Events, AuditLog, RateLimits; ULID ids with type prefix unique per record type; `PublicView` and `PrivateView` per record; HMAC-based anonymous reporter alias
    - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.8, 22.5, 31.8_
  - [x] 8.3 Implement authentication, roles and audit
    - `DemoTokenAuthProvider` (HMAC-signed tokens, 24 h guest expiry), roles from `roles.yaml` admin config, `require(role_set)` dependency (401 for missing/expired/invalid, 403 for insufficient role), self-escalation rejection with audit entry, privacy preferences defaulting to location sharing off, `POST /api/auth/guest`, audit log writer
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.6, 22.7, 28.7, 31.4, 31.7, 42.3, 42.4_
  - [x] 8.4 Implement API infrastructure
    - Error envelope with field-level errors, body-size cap (1 MB, 413), `nh3` text sanitizer, `RateLimiter` port (in-memory and Repository-backed) enforcing 5 reports/10 min, 20 messages/min, 30 votes/10 min, 10 check-ins/10 min with 429 and `Retry-After`, CORS config, OpenAPI metadata
    - _Requirements: 28.1, 28.2, 28.4, 28.5, 28.9, 31.3, 42.2_
  - [x] 8.5 Implement incidents, notices and the overlay store, and wire routing to the Repository
    - Routers: `GET/POST /api/incidents`, `GET /api/incidents/{id}` (Moderator or Official for POST), `POST /api/notices` (Authorized_Official only, audited, 403 otherwise)
    - Overlay store with `overlay_version` counter incremented in the same transaction as any restriction change; Repository-backed `ContextProvider` for RoutingService with an in-process TTL of at most 2 s; routing fails closed (503) if overlays cannot be read
    - _Requirements: 11.2, 28.8, 30.7, 42.1, 42.3, 42.4, 42.7, 14.7, 16.3_
  - [x] 8.6 Export OpenAPI and generate the typed frontend client
    - Export `docs/openapi.json` from the app and generate `frontend/src/services/api.ts` via `openapi-typescript` with a thin fetch wrapper (bearer token, error envelope parsing); add npm and PowerShell scripts to regenerate
    - _Requirements: 42.2, 43.1_
  - [x] 8.7 Write the Repository contract test suite
    - One suite run against `MemoryRepository` and `SqliteRepository` covering put/get/query, if_absent conflicts, CAS conflicts, transact atomicity and `RepositoryUnavailable`
    - _Requirements: 30.9, 44.11_
  - [x] 8.8 Write property test for serialization round trip
    - **Property 22: Serialization round trip** (Graph, Source_Record, OfficialNotice, HazardReport, Message, CheckIn, Incident, User, Event; unique ids per type)
    - **Validates: Requirements 7.8, 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.8**
  - [x] 8.9 Write property test for request validation
    - **Property 23: Invalid requests are rejected with the specified status and field errors**
    - **Validates: Requirements 13.11, 13.12, 23.7, 26.7, 26.8, 27.7, 28.4, 43.5, 43.6, 43.7**
  - [x] 8.10 Write property test for sanitization
    - **Property 24: Sanitization removes markup and is idempotent**
    - **Validates: Requirements 28.5**
  - [x] 8.11 Write property test for public views
    - **Property 30: Public views never leak private fields**
    - **Validates: Requirements 22.5, 31.6, 31.8**
  - [x] 8.12 Write property test for rate limiting
    - **Property 32: Rate limiting admits at most the limit per window**
    - **Validates: Requirements 28.1, 28.2**

- [ ] 9. Community reports, realtime, messages and check-ins
  - [x] 9.1 Implement the EventBus and local realtime endpoints
    - `communication/events.py`: `Event` envelope with ULID `event_id`, publish-after-commit (Events row written in the same `transact`, publish failures logged and never failing the request), `GET /api/events?incident_id&since=`, FastAPI WebSocket `/ws/incidents/{id}` and SSE `/sse/incidents/{id}` via `InProcessPublisher`
    - _Requirements: 25.1, 25.3, 25.4, 25.6, 30.9_
  - [x] 9.2 Implement ReportService, report creation and listing
    - `POST /api/reports` and `GET /api/reports` with the nine categories, validation (category, position, 1000-char description, extraction-area check), Unverified initial status, edge association within 25 m, duplicate linking (same category within 50 m and 60 min), `client_report_id` idempotency via `uuid5`, 4-hour default expiry, anonymous alias, sanitized text, rate limits
    - Simulated flag propagation from the Incident
    - _Requirements: 23.1, 23.2, 23.3, 23.4, 23.7, 23.8, 28.3, 41.6, 16.1_
  - [x] 9.3 Implement the verification lifecycle and moderation
    - `POST /api/reports/{id}/corroborate`, `/dispute`, `/moderate`: distinct non-reporter votes (409 on repeat, 403 own report), thresholds default 2, Corroborated and Disputed transitions, Officially_Verified only by Authorized_Official or demo Moderator (labeled simulated), Resolved by Moderator/Official, `update_cas` plus history append, audit row, overlay add/remove and event in one transaction, sequential application of concurrent changes
    - _Requirements: 24.1, 24.2, 24.3, 24.4, 24.6, 24.7, 24.9, 24.10, 28.6, 16.3, 16.5_
  - [x] 9.4 Implement the Sweeper
    - `services/sweeper.py` `run_sweep(now)`: transition expired non-Resolved reports to Expired (removing their closure overlays) and scrub check-in location data past retention (default 72 h); idempotent; lazy Expired rendering on reads when the sweeper is late; local scheduler plus Lambda handler entry point
    - _Requirements: 24.5, 27.5_
  - [x] 9.5 Implement MessagingService
    - `GET/POST /api/messages`, `POST /api/messages/{id}/hide`: one channel per Incident, six message types, 1 to 1000 characters, unknown incident 404, Community vs Official source label, Moderator hide removed from public views within 5 s and audited
    - _Requirements: 26.1, 26.2, 26.3, 26.4, 26.6, 26.7, 26.8, 26.9_
  - [x] 9.6 Implement CheckInService
    - `POST/GET /api/checkins`: four statuses, location excluded by default, opt-in coarse rounding to at least 200 m grid, newest check-in as current with history retained, validation 400/404, location removal on opt-out within 5 s
    - _Requirements: 27.1, 27.2, 27.3, 27.4, 27.7, 27.8, 31.6, 31.9_
  - [x] 9.7 Implement photo upload presign
    - `POST /api/photos/presign` using `BlobStore`: JPEG/PNG only (415), max 10 MB (413), private storage, 15-minute expiring URLs, disabled via a feature flag
    - _Requirements: 23.5, 23.9_
  - [x] 9.8 Implement the frontend realtime core
    - `services/eventStore.ts` idempotent reducer with `appliedEventIds` and `lastEventId`; `services/backoff.ts` (1 s doubling to 30 s cap); `RealtimeClient` interface with `AppSyncEventsClient`, `WebSocketClient`, `SseClient` selected by `VITE_REALTIME_MODE`; `useRealtime` hook with catch-up via `/api/events?since=`, stale-data banner within 10 s showing last update time, polling no more often than every 30 s while disconnected
    - _Requirements: 25.2, 25.5, 25.6, 25.7, 25.8, 32.4_
  - [x] 9.9 Implement route recalculation on restriction events
    - `hooks/useRouteRecalc.ts`: coalesce restriction events affecting the active route within a window into one request issued within 5 s; display the updated route with closed edges and cause; mark "Route no longer valid" on failure or non-route outcome
    - _Requirements: 16.4, 16.6, 16.7_
  - [x] 9.10 Build community UI components
    - `ReportForm`, `MessageList` (Official notices in a visually distinct, text-labeled section), `CheckInForm` (shows "SafePath does not dispatch emergency services" with 911 guidance on "I need assistance"), report markers with Verification_Status chips including "Corroborated by community — not officially verified" and "Expired"
    - _Requirements: 23.6, 24.5, 24.8, 26.4, 26.5, 27.6, 16.2_
  - [ ] 9.11 Write property test for the verification lifecycle
    - **Property 25: Verification lifecycle rules**
    - **Validates: Requirements 23.3, 24.1, 24.2, 24.3, 24.4, 24.9, 28.6, 44.10**
  - [ ] 9.12 Write property test for status history under concurrency
    - **Property 26: Status history is append-only and consistent under concurrency**
    - **Validates: Requirements 24.7, 24.10**
  - [x] 9.13 Write property test for expiry
    - **Property 27: Expiry transitions are timely and exclusive**
    - **Validates: Requirements 24.5, 44.10**
  - [x] 9.14 Write property test for report creation and duplicates
    - **Property 28: Report creation, edge association and duplicate linking**
    - **Validates: Requirements 23.4, 28.3, 41.6**
  - [x] 9.15 Write property test for check-in privacy and history
    - **Property 31: Check-in privacy and history**
    - **Validates: Requirements 27.3, 27.4, 27.5, 27.8**
  - [ ] 9.16 Write property test for failed writes
    - **Property 35: Failed writes leave no trace** (fault injection into the Repository)
    - **Validates: Requirements 25.1, 30.9**
  - [x] 9.17 Write property test for event application
    - **Property 36: Event application is idempotent, order-tolerant by id, and catch-up is complete** (fast-check)
    - **Validates: Requirements 25.6, 25.8**
  - [x] 9.18 Write property test for reconnect backoff
    - **Property 37: Reconnect backoff and polling bounds** (fast-check)
    - **Validates: Requirements 25.7, 32.4**
  - [x] 9.19 Write property test for recalculation coalescing
    - **Property 39: Route recalculation requests are coalesced** (fast-check with fake timers)
    - **Validates: Requirements 16.4, 16.7**
  - [x] 9.20 Write realtime integration tests
    - Two WebSocket clients receive the same report event within 5 s, 50 simulated clients delivery, persistence across repository reload, stale-banner component test
    - _Requirements: 25.3, 44.10, 44.11, 46.4_

- [ ] 10. Checkpoint - Backend and community
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Indoor scenario and demo scenarios
  - [x] 11.1 Create the fictional Indoor_Graph
    - `data/demo/indoor_building.json` (3 floors, 2 zones and 2 corridors per floor, stairwells A/B/C, ground-floor exits 1 and 2, anchored near the UIC–downtown corridor) and `routing/indoor.py` builder producing indoor Nodes/Edges with explicit Level_Transition edges (stairwell, exit to outdoor Node), classified Simulated and labeled fictional
    - _Requirements: 21.1, 21.2, 21.6, 9.4, 12.5_
  - [x] 11.2 Implement the Building_Emergency_Module
    - `disasters/building.py`: indoor routing through RoutingService via `origin_indoor_node_id`, reported-blocked stairwell penalty, stairwell closure confirmed only by demo Moderator or Authorized_Official (403 for Guest/User, graph unchanged), removal of stairwell edges from the mask, NO_VERIFIED_ROUTE_AVAILABLE when all stairwells to an exit are removed, no "safe" claims, stairwell report display within 5 s
    - _Requirements: 21.3, 21.4, 21.5, 21.7, 21.8_
  - [x] 11.3 Implement `seed_demo_incidents.py` and scenario data
    - `data/demo/scenarios/{flood_loop,earthquake_debris,blocked_stairwell}.json` and seeding script: all Incidents `simulation_flag=true`, `scenario_id` on every record, flood origin within 200 m of the Riverwalk, designated "Simulation destination — NOT an official shelter", flood highlighting from the lowest 20% elevation percentile with no depth values, simulated debris zones adjacent to real OSM building footprints, three simulated indoor user positions, seeded users (Moderator, Users A/B/C)
    - _Requirements: 37.1, 37.2, 37.3, 37.6, 38.1, 38.2, 39.1, 40.4, 20.5, 7.1_
  - [x] 11.4 Implement the DemoService and admin endpoints
    - `services/demo.py` and routers: `POST /api/demo/{scenario}/start|advance|reset`, `POST/DELETE /api/demo/closures` for simulated closures and hazard polygons (Moderator only, 403 otherwise); reset within 30 s removes only that scenario's records and restores seeds, idempotent when already seeded; debris zones mark edges blocked within 5 s with Simulated zone ids cited in route explanations
    - _Requirements: 40.1, 40.2, 40.3, 40.6, 40.7, 37.4, 37.5, 38.3, 38.4, 39.5_
  - [ ] 11.5 Write property test for scenario reset
    - **Property 40: Scenario reset is complete, isolated and idempotent**
    - **Validates: Requirements 40.3, 40.4, 40.7**
  - [ ] 11.6 Write property test for the authorization matrix
    - **Property 29: Authorization matrix** across incident creation, report moderation, stairwell confirmation, message hiding, notice creation, demo admin actions and role self-assignment
    - **Validates: Requirements 21.8, 22.1, 22.4, 22.7, 24.9, 26.9, 28.8, 31.4, 31.7, 40.6, 42.3, 42.4**
  - [ ] 11.7 Write indoor tests including the indoor variant of Property 6
    - Indoor/outdoor connect only via Level_Transition, all-stairwells-removed gives NO_VERIFIED_ROUTE_AVAILABLE for that floor, individual stairwell closure reroutes
    - _Requirements: 21.2, 21.4, 21.7, 44.9, 44.4_
  - [ ] 11.8 Write backend scenario integration tests
    - Scenario 1 shortest plus disaster-adjusted route and recalculation after a simulated verified closure, Scenario 2 debris exclusion with zone references, Scenario 3 report, corroborate, moderator confirm, indoor reroute, assistance message published
    - _Requirements: 37.4, 37.5, 38.3, 38.4, 39.2, 39.3, 39.4, 39.5, 44.11_

- [x] 12. AWS discovery, CDK and deployment tooling
  - [x] 12.1 Implement AWS sandbox discovery
    - `scripts/aws_discovery.py` (read-only): `sts get-caller-identity`, region, IAM permission probes (`iam simulate-principal-policy` or probe calls), service quotas and availability for DynamoDB, Lambda, API Gateway, S3, CloudFront, Cognito, AppSync Events, Bedrock, Budgets, EventBridge, CloudWatch
    - Write findings to `docs/aws-deployment.md` and emit CDK context flags (`useCognito`, `realtime=appsync|apigw-ws`, `enableBudget`, `enableBedrock`, `enableCloudFront`) to `infrastructure/aws/cdk.context.json`; create a discovery gate file that `deploy` requires; run this before provisioning anything
    - _Requirements: 29.1, 29.5_
  - [x] 12.2 Implement AWS adapters and Lambda entry points
    - `adapters/aws/`: `DynamoRepository` (boto3, supports DynamoDB Local via `AWS_ENDPOINT_URL`), `S3BlobStore` (presigned 15 min), `AppSyncEventsPublisher`, `ApiGwWebSocketPublisher` (fallback with connections table), `CognitoAuthProvider` (JWKS), S3 Graph source; Mangum `handler` and Sweeper Lambda handler; each labeled substitute documented when a service is unavailable
    - _Requirements: 29.3, 29.5, 30.7, 25.4, 31.4, 31.5_
  - [x] 12.3 Scaffold the CDK app and DataStack
    - `infrastructure/aws/` TypeScript CDK app with an Aspect tagging every resource `Project=SafePath`, context-flag handling, `DataStack` (on-demand DynamoDB tables with SSE and TTL on events and rate limits, private versioned graphs bucket, private photos bucket, public access blocked)
    - _Requirements: 29.2, 29.6, 32.1, 32.2, 31.5, 30.7_
  - [x] 12.4 Implement ApiStack
    - Lambda (Python 3.12, Graph loaded from S3 at cold start) with one least-privilege role per function, HTTP API with throttling 50 rps and burst 100, Sweeper Lambda plus 1-minute EventBridge rule, Cognito pool/client/groups (when permitted), AppSync Events API or WebSocket API fallback, optional Bedrock invoke permission, CORS limited to the CloudFront origin
    - _Requirements: 29.3, 31.1, 32.5, 25.4, 31.5_
  - [x] 12.5 Implement WebStack, ObservabilityStack and BudgetStack
    - S3 website bucket with CloudFront (OAC, TLS 1.2+) or documented alternative, PMTiles and GeoJSON static objects, CloudWatch log groups with 7-day retention and basic alarms, optional AWS Budgets monthly 25 USD with alert at 80% when permitted
    - _Requirements: 29.4, 32.3, 32.6, 31.5_
  - [x] 12.6 Write deployment and teardown tooling
    - `scripts/deploy.ps1`: refuse to run without the discovery gate, upload the Graph artifact, PMTiles and frontend bundle (building with only public identifiers), run `cdk deploy` with context flags, print the deployed URL; teardown limited to resources tagged `Project=SafePath`; failed deployments roll back only tagged resources created by that deployment; actual provisioning is run by the user after reviewing discovery results
    - _Requirements: 29.1, 29.6, 29.8, 31.2, 45.3_
  - [ ] 12.7 Write CDK assertion tests
    - All resources tagged, DynamoDB `PAY_PER_REQUEST`, no EC2, log retention 7 days, API throttling 50/100, S3 public access blocked and encrypted, per-function IAM scoping, TLS minimum
    - _Requirements: 29.6, 31.1, 31.5, 32.1, 32.2, 32.5, 32.6_
  - [x] 12.8 Run the Repository contract suite against DynamoDB Local
    - Reuse the task 8.7 contract suite with `DynamoRepository`
    - _Requirements: 30.9, 44.11_

- [x] 13. Frontend screens and polish
  - [x] 13.1 Regenerate the API client and build the left sidebar
    - Regenerate `services/api.ts` from the final OpenAPI; implement `LeftSidebar` with active Incident, disaster type selector (flood, earthquake, tornado, fire, building emergency), start/destination inputs with map click selection, route alternatives list, official info links and uncertainty summary; `hooks/useIncident`, `hooks/useAuth` (guest session)
    - _Requirements: 34.2, 46.3, 22.1_
  - [x] 13.2 Build the route panel and outcome display
    - `RoutePanel`: summary with "algorithm-generated" label, distance, duration, Uncertainty_Level text and confidence limitations, reasons versus alternatives, restrictions applied; SHELTER_IN_PLACE, FOLLOW_OFFICIAL_INSTRUCTIONS, NO_VERIFIED_ROUTE_AVAILABLE and INSUFFICIENT_DATA text displayed in place of navigation (with 911 guidance); render Graph-derived route geometry on the Map_View; export as a component wired in 13.8
    - _Requirements: 1.7, 15.4, 43.4, 21.7, 8.10, 44.11_
  - [x] 13.3 Build map data layers
    - Layers for roads, buildings, transit stations, elevation and hazard overlays (lowest-20% highlight), routes, report markers, incident boundaries, closures (red), verified destinations (green), and SIMULATED labels on every simulated feature within 5 s; CBD-derived layers carry the historical label; toggles take effect within 1 s
    - _Requirements: 1.3, 34.3, 36.1, 37.3, 38.3, 5.4, 8.10_
  - [x] 13.4 Implement map feature inspection
    - `maps/inspect.ts`: road segment popup (street name, Edge attributes, closure status, active reports, Source_Record, last update), report marker popup, route popup, building popup, showing `Unknown` for missing attributes, all within 1 s
    - _Requirements: 35.1, 35.2, 35.3, 35.4, 6.5_
  - [x] 13.5 Build right sidebar, bottom controls and remaining screens
    - `RightSidebar` (messages, reports, Verification_Status, check-ins, assistance requests), `BottomControls` (report hazard, request assistance, mark safe, view official guidance, recalculate route), and pages Disaster Selection, Route Details, Submit Report, Community Communication, Incident Overview, Safety Check-In
    - _Requirements: 34.4, 34.5, 34.6, 23.6_
  - [x] 13.6 Build the indoor building view
    - Floor-by-floor view of the fictional building labeled "fictional" and SIMULATED, stairwell and exit conditions shown as reported conditions with Verification_Status, indoor route display, emergency guidance when no route exists
    - _Requirements: 21.3, 21.5, 21.6, 21.7, 39.4_
  - [x] 13.7 Build the Demo_Admin_Console
    - `pages/DemoAdmin.tsx`: start, advance and reset each scenario, add/remove simulated closures and hazard polygons, Moderator-only with 403 handling
    - _Requirements: 40.1, 40.2, 40.6, 34.6_
  - [x] 13.8 Wire the application shell, responsiveness and accessibility
    - Compose `MainMap` and routing between all pages, Header with logo, "Chicago", demo mode indicator, connectivity status and profile control, responsive layout from 320 to 2560 px without horizontal scrolling for all header, sidebar and bottom controls, keyboard operability with visible focus, accessible names, text/icon plus color for every status
    - _Requirements: 34.1, 34.7, 36.2, 36.4, 46.7_
  - [ ] 13.9 Write frontend component and accessibility tests
    - Disclaimer on every page, SIMULATED indicator, historical label, 911 within one interaction, Corroborated text, stale banner, status chips with text and icon, keyboard operation, `axe-core` checks, layout at 320 and 2560
    - _Requirements: 1.1, 1.2, 8.10, 24.8, 25.5, 34.7, 36.2, 36.3, 36.4_
  - [x] 13.10 Write Playwright end-to-end demo tests
    - Scenario 1 (flood, route change explanation within 5 s) and Scenario 3 (two browser contexts: report, corroborate, moderator confirm, indoor reroute) following the demo script
    - _Requirements: 37.5, 39.2, 39.3, 39.4, 39.6, 44.11, 46.4, 46.5_

- [x] 14. Offline support, optional AI, documentation and demo script
  - [x] 14.1 Implement the Offline_Manager
    - Workbox service worker precaching the app shell, runtime caching of PMTiles range requests, static GeoJSON (zoom 12 to 16) and static official guidance; IndexedDB queue (max 50, creation-order flush, client UUID idempotency key, "Queued — not yet received by server", "Rejected by server" with error and edit/discard); offline indicator with last sync time; "Cached as of [timestamp] — may be outdated" labels; `hooks/useOfflineQueue`
    - _Requirements: 41.1, 41.2, 41.3, 41.4, 41.5, 41.6, 41.7, 41.8_
  - [x] 14.2 Write property test for the offline queue
    - **Property 38: Offline queue ordering, idempotence and capacity** (fast-check)
    - **Validates: Requirements 41.4, 41.5, 41.6, 41.7, 41.8**
  - [x] 14.3 Add the PMTiles extraction script
    - `scripts/build_tiles.ps1` extracting a PMTiles archive for the Chicago bounding box (zoom 12 to 16) for local and S3/CloudFront hosting; if unavailable, document the dev-only OSM raster fallback and no bulk prefetch
    - _Requirements: 41.1, 3.8_
  - [x] 14.4 Implement the optional AI_Assistant
    - `services/ai.py` with Bedrock client (15 s timeout), `POST /api/ai/summarize`, grounding guard that discards output referencing entities absent from the context or citing no supplied Source_Record and returns the unsummarized source data; never mutates Policy_Outcomes, routes, restrictions or Verification_Status; frontend AI-generated, non-authoritative label and "AI summaries unavailable" state
    - _Requirements: 33.1, 33.2, 33.3, 33.4, 33.5, 33.6, 33.7, 13.9_
  - [x] 14.5 Write property test for AI grounding
    - **Property 41: AI output is accepted only if grounded**
    - **Validates: Requirements 33.2, 33.3, 33.6, 33.7**
  - [x] 14.6 Implement optional CTA transit alerts
    - `GET /api/transit/alerts` active only when credentials are configured: 10 s timeout, stale cached alerts with original timestamp, "live transit alerts unavailable" otherwise; frontend message where alerts would appear; station entrance safety status stays Unknown without an Official_Notice or Officially_Verified report
    - _Requirements: 5.2, 5.3, 5.4, 5.5_
  - [x] 14.7 Write architecture and technical documentation
    - `docs/architecture.md` (including the data-flow diagram covering datasets, official plans, Routing_Engine, AWS services, user devices and community reporting), `docs/data-sources.md`, `docs/routing-design.md`, `docs/safety-limitations.md` listing every fallback, unavailable source, substitution and limitation (including that terrain elevation does not represent bridge, tunnel or underground levels)
    - _Requirements: 45.2, 45.4, 45.7, 4.6, 1.5_
  - [x] 14.8 Write README, deployment notes, demo script and pitch
    - Complete `README.md` (setup, local development, deployment, data import, known limitations, deployed URL where permitted), finalize `docs/aws-deployment.md` with the substitution matrix, `docs/demo-script.md` (Scenarios 1 and 3 in 3 minutes) and a pitch script of 3 minutes or less
    - _Requirements: 45.2, 45.3, 45.5, 45.6, 40.5, 29.5_
  - [x] 14.9 Add the test-run recorder
    - `scripts/run_tests.ps1` running pytest (JUnit XML), vitest and Playwright and writing `docs/test-results.md` with test name, pass/fail and execution date
    - _Requirements: 44.13_
  - [x] 14.10 Add secret scanning
    - Add gitleaks (or equivalent) config and a script scanning the repo and built frontend bundle; verify no hardcoded credentials and that the bundle contains only public identifiers
    - _Requirements: 31.2, 31.3_

- [ ] 15. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; property tests validate universal correctness properties, unit and integration tests validate examples and edge cases.
- Each property test is tagged `# Feature: safepath-evacuation-navigation, Property {n}: {title}` and uses at least 100 examples (Hypothesis `ci` profile, fast-check `numRuns` 100).
- The AWS discovery script (task 12.1) must run, and its findings must be recorded in `docs/aws-deployment.md`, before any resource is provisioned. Unavailable services use the labeled substitutes in the design (signed demo tokens instead of Cognito, API Gateway WebSocket instead of AppSync Events, local or S3 hosting instead of CloudFront).
- Tasks 12.x author infrastructure code and deployment scripts only. Running a real deployment is a user decision after reviewing discovery results.
- Real data downloads (tasks 3.8, 3.11, 4.3, 4.4, 14.3, 14.6) can fail. Each must fall back to a previous copy, a missing-source record or a labeled fixture, record the limitation, continue, and be listed in `docs/safety-limitations.md`.
- SafePath is a research and demonstration prototype; all demo scenarios are simulated and no UI text may claim official approval, certified safety, or 911/dispatch integration.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "3.1", "3.4"] },
    { "id": 2, "tasks": ["1.5", "1.6", "3.2", "3.3", "3.5", "3.6", "3.13", "4.1", "6.1", "6.2", "8.1"] },
    { "id": 3, "tasks": ["3.7", "3.8", "3.14", "4.2", "4.3", "4.4", "4.5", "6.3", "6.6", "8.2", "8.4", "12.1", "14.3"] },
    { "id": 4, "tasks": ["3.9", "4.6", "6.4", "6.5", "6.9", "8.3", "8.7", "8.8", "8.10", "8.11", "8.12", "12.3"] },
    { "id": 5, "tasks": ["3.11", "6.7", "6.8", "6.10", "6.11", "6.13", "9.1", "9.8", "11.1"] },
    { "id": 6, "tasks": ["3.10", "3.12", "6.12", "6.15", "6.21", "6.22", "6.24", "6.25", "6.26", "6.28", "6.29", "9.2", "9.5", "9.6", "9.17", "9.18", "12.2"] },
    { "id": 7, "tasks": ["3.15", "6.14", "6.16", "6.17", "6.18", "6.19", "6.20", "6.23", "6.27", "6.30", "6.31", "8.5", "8.9", "9.7", "9.14", "12.4", "12.8"] },
    { "id": 8, "tasks": ["8.6", "9.3", "12.5", "14.4", "14.6"] },
    { "id": 9, "tasks": ["9.4", "9.9", "9.10", "9.11", "9.12", "9.16", "11.2", "11.3", "12.6", "12.7", "14.1", "14.5"] },
    { "id": 10, "tasks": ["9.13", "9.15", "9.19", "9.20", "11.4", "13.2", "13.3", "14.2"] },
    { "id": 11, "tasks": ["11.5", "11.6", "11.7", "11.8", "13.1", "13.4", "13.6"] },
    { "id": 12, "tasks": ["13.5", "13.7"] },
    { "id": 13, "tasks": ["13.8"] },
    { "id": 14, "tasks": ["13.9", "13.10", "14.7", "14.8", "14.9", "14.10"] }
  ]
}
```
