# Routing design

SafePath computes route options at request time over an in-memory directed graph. A route is an algorithm-generated
estimate that depends on the data listed in [data-sources.md](data-sources.md); it is not a claim that any street is
passable or free of hazard. Unknown data adds cost, never reduces it. Experimental prototype; not an official system.

Code: `backend/app/routing/` and `backend/app/disasters/`. Weights: `backend/app/routing/routing_weights.yaml`.

## 1. Graph model

### Artifact

The routing graph is a versioned, immutable artifact written by `scripts/build_graph.py` and read by
`backend/app/routing/graph.py`:

```
data/processed/graphs/v20261024-2e1f05/
  manifest.json            version, built_at, sha256 per file, counts, elevation percentiles, source_record_ids
  nodes.jsonl.gz
  edges.jsonl.gz
  layers/buildings.geojson, cta_stations.geojson, extraction_boundary.geojson, simulated_station.geojson
data/processed/graphs/latest.json   (written last)
```

The loader verifies each file's sha256 against the manifest. A missing or unreadable artifact is reported through
`GET /api/graph/status`. Only when `SAFEPATH_GRAPH_ROOT` is unset and no real artifact loads does the app use the
SIMULATED fixture graph (`tests/fixtures/graph`), marked as a fallback in `/api/health` and `/api/graph/status`.

### Real counts (graph `v20261024-2e1f05`, built 2026-10-24, source `data/processed/summary_report.json`)

| Item | Count |
|---|---|
| Nodes | 61,177 (61,175 intersection, 1 pedestrian junction, 1 indoor) |
| Directed edges | 157,687 (157,683 street, 4 level transition) |
| Edges classified Static / Simulated | 157,681 / 6 |
| Edges by travel mode | walking+wheelchair 103,536; driving 50,927; walking 3,040; driving+walking+wheelchair 184 |
| Input street segments (merged CDP + OSM) | 42,512 (before noding) |
| Segments after noding | 83,873 pieces; 4,865 T-junction cuts; 6,407 endpoints snapped (3 m tolerance); 957 self-loop pieces skipped |
| Nodes with Unknown elevation | 102 (of 61,177) |
| Edges with Unknown elevation confidence | 652 |
| Edges whose bridge flag is Unknown | 153,414 |
| Edges whose tunnel flag is Unknown | 152,520 |
| Edges whose infrastructure level is Unknown | 147,323 |
| Edges whose pedestrian access is Unknown | 46,785 |
| Edges whose mobility accessibility is Unknown | 154,609 |
| Edges whose current closure status is Unknown | 157,687 (all: there is no closure source) |
| Driving edges with Unknown one-way | 29,120 |
| Connected components, all modes | 262; largest has 60,389 nodes (98.7%) |
| Walking / wheelchair / driving largest component | 97.5% / 95.2% / 93.5% of that mode's nodes |
| Artifact size | 21.3 MB on disk (6.8 MB edges, 1.3 MB nodes, 13.3 MB buildings layer) |

Disconnected islands are kept in the graph and reported, not dropped. The 28,694 OSM buildings (14,569 with Unknown
height class) are a display layer. Building entrances are not connected to the graph because the OSM extract has no
entrance nodes.

### Nodes and edges

- One node per distinct segment endpoint (rounded to 6 decimals, about 0.1 m). Segments are cut at shared vertices and at
  T-junctions where an unshared endpoint lies within 3 m of another segment. Segments that cross without a shared vertex
  are not joined. This noding is a heuristic and has not been checked against ground truth.
- Edges are directed (`<segment>:f` is the digitised direction, `:r` the reverse). Distances are in meters and travel
  times come from a single baseline: 1.4 m/s for edges that carry walking or wheelchair, and road-class defaults for
  driving-only edges (for example motorway 24.6 m/s, unknown class 8.9 m/s). These are planning defaults, not measured
  speeds.
- Every attribute that a source does not state is the string `Unknown`. It is never defaulted to false, zero or an empty
  string. `current_closure_status` is always Unknown in the artifact; closures come from overlays at request time.
- Node elevation comes from USGS 3DEP (NAVD 88, meters), never zero-filled. See the elevation caveats below.
- Every Level_Transition edge (`edge_kind = "level_transition"`, with `transition_type` of `stairwell`, `exit`,
  `platform_stair` or `ramp`) is explicit. A floor change without one is a validation error.

## 2. Hard restrictions are separate from penalties

The pipeline has two distinct steps (`service.py`):

1. Hard step, `RestrictionResolver` (`restrictions.py`). Produces a boolean mask over edges. A masked edge is not
   searchable at all; no cost can bring it back, and alternatives never relax it.
2. Soft step, `CostModel` (`cost.py`). Gives every remaining edge a non-negative cost.

What goes into the mask:

| Source | Mask rule |
|---|---|
| Report with status `Officially_Verified` and not expired | its `affected_edge_ids` |
| Active overlay (demo moderator closure, impassable polygon, module output) for this incident | listed edges, plus every edge whose LineString `intersects` the polygon (exact test on STRtree candidates) |
| In-force Official_Notice of type `road_closure` or `temporary_perimeter` | listed edges and polygon intersection |
| In-force `road_direction_restriction` notice | only the listed directed edges; the reverse edge stays usable |
| Step-free profile | edges with `mobility_accessibility == "inaccessible"` |
| Travel mode | edges lacking the mode; walking and wheelchair also skip edges with `pedestrian_access == false` |
| Tornado warning in force | all outdoor edges (street edges and `exit` transitions) |

Never in the mask: `Unverified`, `Corroborated` and `Disputed` reports; notices that are `cbd_derived`, lack a
Source_Record, are not yet effective or expired (60 s grace); Cook County HMP areas used as live boundaries; anything
derived from the 2007 plan.

Origin or destination is "isolated" when it cannot be snapped, or its node has no remaining usable edge once the mask is
applied. Snapping is done twice: once ignoring hard restrictions (to tell isolation from lack of data) and once on the
masked graph so a route never starts or ends on a blocked edge. A request point farther than 500 m from every usable
street edge has no snap candidate and gives `INSUFFICIENT_DATA`. Resolved restrictions are cached by overlay version,
incident id, graph version and an input fingerprint. The overlay version is a counter incremented in the same
transaction as any restriction change, and the context provider reads it with an in-process TTL of at most 2 s.

If the overlays cannot be read, the route request fails closed with HTTP 503 rather than routing without them.

## 3. Cost function

```
Route_Cost(edge) = travel_cost + disaster_hazard_penalty + uncertainty_penalty
scale(edge)      = max(distance_m, d_min) / 100            d_min = 10 m
travel_cost      = estimated_travel_time_s x mode_factor
```

All three terms are non-negative and length-scaled. The per-edge breakdown (`PenaltyComponents`) is returned in
`edge_penalties` of each route, so the displayed numbers are the ones the search used.

Current weights from `routing_weights.yaml` (version 1):

| Group | Key | Value |
|---|---|---|
| Mode factor | walking / wheelchair / driving | 1.0 / 1.4 / 1.0 |
| Max speed for the heuristic (m/s) | walking / wheelchair / driving | 2.0 / 1.5 / 20.0 |
| Minimum length for scaling | `d_min_meters` | 10 |
| Uncertainty attributes checked | | `infrastructure_level`, `pedestrian_access`, `bridge_flag`, `tunnel_flag`, `elevation_confidence` |
| Uncertainty | `w_unknown_attr` (per Unknown attribute) | 8 |
| | `w_elev_low` | 6 |
| | `w_elev_unknown` | 12 |
| | `w_profile_unknown` (step-free profile, Unknown accessibility) | 15 |
| | `w_disputed` | 10 |
| Reports (soft) | `w_unverified` / `w_corroborated` | 20 / 40 |
| Flood | `w_tunnel` / `w_lower` / `w_shore` / `w_elev` | 120 / 80 / 60 / 40 |
| | `E_low` / `E_high` (m) | 176.0 / 184.0 |
| Earthquake | `w_highrise_proximity`, `proximity_meters` | 60, 50 |
| Fire | `w_smoke_report`, `radius_meters` | 90, 100 |
| Building | `w_reported_blocked_stair` | 100 |
| Alternatives | `inflate_factor`, `max_attempts`, `min_distinct_edges` | 1.5, 8, 1 |
| Uncertainty level thresholds | `medium_fraction`, `high_fraction` | 0.05, 0.20 |

The file is validated at start-up and on reload: a missing, negative or non-numeric value, or `w_unknown_attr <= 0`
(each Unknown value must add a strictly positive cost), makes `/api/routes/calculate` return 503 with the file and
field named, and `/api/health` reports `routing: invalid_configuration`. These weights are hand-chosen prototype values
and have not been calibrated against any observed outcome.

The flood elevation bounds in the file (`E_low` 176.0, `E_high` 184.0) are static. The 5th and 95th elevation
percentiles of the built graph are 178.609 m and 185.277 m (in the manifest); the weights file has not been synchronized
with them.

### How Unknown is treated

- Each Unknown attribute in `uncertainty_attributes` adds `w_unknown_attr x scale`. A longer Unknown edge costs more.
- Low elevation confidence and Unknown elevation confidence add their own terms.
- Under the step-free profile, Unknown `mobility_accessibility` adds `w_profile_unknown x scale`; known-inaccessible
  edges are masked.
- A Disputed report on an edge adds `w_disputed x scale` as uncertainty.
- Unknown is never favorable: no code path turns Unknown into "clear".
- Because the real graph has most bridge, tunnel and level attributes Unknown (see counts), most edges carry a similar
  constant uncertainty overhead, so the term mostly separates edges with known attributes from the rest rather than
  ranking hazards.

## 4. Search

- Algorithm: A* over the masked graph (`search.py`), edges traversed only from `edge_from` to `edge_to`. A reference
  Dijkstra with a zero heuristic is used by tests as the optimality oracle.
- Heuristic: `haversine(node, destination) / v_max`, where `v_max = v_max_mps[mode] / min(1, mode_factor)`. Because
  `Route_Cost >= travel_cost >= distance / speed`, this is a lower bound when the baseline speed of every edge is at most
  `v_max`. Walking (baseline 1.4 against 2.0) and wheelchair (1.5) satisfy this.
- Driving: `v_max_mps.driving` is 24.6 m/s (the motorway speed the graph builder assigns), and `CostModel.v_max` is also raised to the fastest speed implied by the loaded graph (distance / travel time), so the heuristic stays admissible and A* matches Dijkstra. A regression test covers this.
- Deterministic tie-breaking: heap order is (priority, node index), edges are scanned in CSR order, and a predecessor is
  replaced only on a strictly lower cost.
- `cost_fn` must return a finite non-negative value; anything else raises.

### Alternatives

Up to `alternatives` (1 to 3) routes:

1. Find the optimal path P1.
2. Multiply the search cost of every edge on already-accepted paths by `inflate_factor` (1.5) and search again, up to
   8 attempts. Accept a result only if it differs from every accepted path by at least `min_distinct_edges` edges.
3. If fewer than k distinct routes result, run Yen-style spur searches from the accepted paths and take the cheapest
   distinct candidate.

The mask and module filter apply in every pass. Fewer than k routes may be returned; duplicates are never used as padding.
Reported costs are the true uninflated costs, ordered by cost. Alternatives are cheap variants of the same cost
function, not independent evacuation plans, and they may share most of their length.

### Uncertainty level of a route

`f` is the fraction of route length on edges with at least one Unknown or Low-confidence attribute. `Low` if
`f < 0.05`, `Medium` if `f < 0.20`, otherwise `High`; `Unknown` when there is no coverage information at all. A module's
floor can only raise the level (flood with no evidence gives High). With the current Unknown-heavy graph, `High` is the
expected result for most routes on real data.

## 5. Rules_Engine and policy outcomes

`disasters/rules_engine.py` is pure and deterministic. It first filters notices to the eligible set, then applies the
precedence list; the first match wins. Rank 6 depends on the search result, so evaluation is split into
`evaluate_pre_route` (ranks 1 to 5) and `finalize` (rank 6).

Eligibility: a notice must carry a Source_Record (otherwise it is excluded and labeled "Unknown source"), must not be
`cbd_derived`, must not be a Cook County HMP area used as a live boundary, must be effective and not expired (60 s
grace), and must belong to the incident.

| Rank | Condition | Policy outcome |
|---|---|---|
| 1 | Eligible notices covering the origin give opposite directives (shelter vs go to a destination) | `FOLLOW_OFFICIAL_INSTRUCTIONS` |
| 2 | A covering `shelter_in_place` notice, and the incident is not a fire | `SHELTER_IN_PLACE` |
| 3 | A covering notice SafePath cannot model (`follow_instructions`, or any fire-incident transportation or shelter notice) | `FOLLOW_OFFICIAL_INSTRUCTIONS` |
| 4 | The disaster module's `policy_override` (tornado: warning active, origin inside a live boundary) | module outcome (`SHELTER_IN_PLACE`) |
| 5 | Required data missing (no graph, no snap candidate, no indoor graph) | `INSUFFICIENT_DATA` |
| 6 | Origin or destination isolated by hard restrictions, or no path exists | `NO_VERIFIED_ROUTE_AVAILABLE` |
| 7 | Otherwise | `ROUTE_OPTIONS_AVAILABLE` |

Coverage is a point-in-polygon test of the origin against the notice geometry; a notice with no geometry uses the incident
boundary if it is live, else applies to the whole incident. A non-route outcome returns a normal response with an
empty-geometry placeholder route and `route_count` 0; no outdoor route is computed for ranks 2 and 3. When an eligible
notice designates an incident-specific destination, it is listed ahead of non-official destinations.

## 6. Disaster modules

Modules register themselves (`DisasterModule` in `disasters/base.py`) and expose `hard_restrictions`, `edge_penalty`,
`route_filter`, `policy_override`, `uncertainty_floor` and `advisories`. None describes a route or place as safe.

| Module | Hard restrictions | Penalties (soft) | Notes |
|---|---|---|---|
| Flood | Impassable flood polygons and closures from overlays; road-closure or perimeter notices that mention flooding | Tunnel or underpass 120; lower-level 80; lower-level within 50 m of water 60; elevation term up to 40 that decreases with mean edge elevation between `E_high` and `E_low`; elevation Unknown or Low confidence adds uncertainty | Wording is "lower modeled exposure". Shoreline term is skipped when the graph has no `water` layer (see below). No water depth values are modeled. Uncertainty floor High when the incident has no polygon, notice, report or closure |
| Earthquake | Confirmed bridge closures, collapse and debris zones, edges named by active damage notices | High-rise proximity 60 within 50 m, only for buildings associated with a debris zone, notice or report | Building height class is display-only and never an input to a penalty on its own; an official "intact" status removes a building from consideration |
| Tornado | None | None | While the warning is active and the origin is inside a live boundary: `SHELTER_IN_PLACE`, with the official shelter named only if a verified one exists, else "No verified shelter available" plus official guidance. All outdoor edges are filtered out, so no outdoor route is produced. Routing resumes when the incident leaves `active` |
| Fire | Edges intersecting an Officially_Verified perimeter or an official prohibited area; confirmed blocked streets | Unverified/Corroborated/Disputed fire or smoke reports 90 within 100 m; an unverified perimeter is never a hard restriction and gets a penalty and warning | Official fire instructions rank ahead of computed routes (rank 3) |
| Building (indoor) | Stairwell edges (Level_Transitions and corridor landings) whose closure is confirmed by a demo moderator or an official | Reported-blocked but unconfirmed stairwell 100 | If every stairwell to an exit is removed, the result is `NO_VERIFIED_ROUTE_AVAILABLE` |

The flood module reads a `water` layer from the graph. The built artifact has no `water` layer (layers are buildings,
CTA stations, extraction boundary, simulated station), so on the real graph the shoreline term never applies and the
module adds an advisory saying water geometry is unavailable. CDP river lines are used only to flag
`river_crossing` during street processing.

## 7. Indoor graph and Level_Transition

The indoor building is fictional and SIMULATED (`data/demo/indoor_building.json`, "SIMULATED Demo Building"). It is not
a real building or a real UIC floor plan. `routing/indoor.py` merges it into the loaded graph at runtime:

- Three floors (`L1` ground, `L2`, `L3`), two zones and two corridors per floor, stairwells A, B and C, exits 1 and 2.
- Floors connect only through explicit bidirectional Level_Transition edges (`stairwell`). Each ground-floor exit links
  to the nearest outdoor node of the graph being merged into (`exit`), so the same building attaches to either the real
  or the fixture graph.
- A request can start from an indoor node (`origin_indoor_node_id`); the destination is still an outdoor point.
- The station model at Clark/Lake is also SIMULATED: a street level and a platform level joined by `platform_stair` and
  `ramp` Level_Transitions, attached to the real street graph at one node (6.3 m link). It is not the real station
  layout.
- If merging the indoor building fails, the outdoor graph stays usable and a warning is recorded.

## 8. Explanation

Each route carries: restrictions considered (kind, id, source record, simulated flag), the number of excluded edges, per
edge penalty components, a reason summary relative to the other returned routes, the latest data timestamp applied, the
source ids of every notice, restriction and report that affected it, confidence limitations, and the label
`algorithm-generated`. The response also lists `graph_version`.

## 9. Limitations specific to routing

- One-way data: 730 CDP-versus-OSM one-way conflicts were resolved in favor of OSM, and 29,120 driving edges have an
  Unknown one-way flag. Driving edges for those are created in both directions (flagged `oneway_unknown`), which can
  produce routes against traffic. Walking is not affected. The `oneway_unknown` flag is on the edge, but the current
  cost model does not read it, so no penalty is applied for it.
- Elevation: 3DEP is bare-earth terrain. It does not represent bridge decks, tunnels or underground levels. Edges on
  bridges, tunnels, elevated or lower-level infrastructure get Low elevation confidence (3,960 of 42,512 segments;
  High 18, Medium 38,534), and higher terrain does not mean lower flood exposure. Of 51,604 sampled segment endpoints
  none were Unknown, but 102 nodes created by noding stayed Unknown after one failed request.
- Infrastructure level is Unknown on 38,544 of 42,512 segments, so bridge/tunnel awareness is partial. Only 1,828 bridge
  and 1,901 tunnel segments are known.
- Wheelchair and step-free routing: `mobility_accessibility` is Unknown on 154,609 of 157,687 edges. Step-free routing
  masks only known-inaccessible edges (for example steps) and penalizes the Unknown rest; it cannot confirm a route is
  step-free, curb-ramp or surface-quality data are absent.
- Closures: no live closure source is connected (no CDOT, IDOT, 311 or other feed). All closures come from demo
  overlays, moderator or official actions, or notices entered through the API.
- Memory: the real graph is held as Python objects plus NumPy arrays. Local measurements of `Graph.load` of the ~61k-node
  graph were roughly 915 to 975 MB resident. The planned Lambda configuration is 3008 MB. A lighter loader is a follow-up.
- Travel times are baselines (1.4 m/s walking, road-class driving defaults), not live conditions. They do not model crowds,
  signals, stairs or surface.
- Noding is heuristic (see section 1); disconnected islands (262 components overall) can make a destination unreachable
  even though the real streets connect.
- No real-world validation has been done: no comparison with actual evacuation behavior, no calibration of weights.
- Performance on a real deployment has not been measured; test timings in `docs/test-results.md` are local.
