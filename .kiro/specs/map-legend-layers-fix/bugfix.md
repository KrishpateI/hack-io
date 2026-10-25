# Bugfix Requirements Document

## Introduction

The map legend lists 21 symbols, but several of them never appear on the map even when the matching incident is selected and the layer toggle is on. Users selecting the seeded SIMULATED Flood, Earthquake or Fire incident see a route but no SIMULATED flood area, no SIMULATED debris zone and no SIMULATED fire perimeter. Low-lying areas, restricted edges and the street graph toggle are also affected, and closures only appear if they arrive as live events while the page is open. This misleads users about which hazards the route was planned around.

The fix covers every legend entry: each one must render when its data applies to the selected incident and its toggle is on, and must not render otherwise. It must work for normal users selecting a seeded incident (demo mode is off in the deployed environment), not only through the demo console. Everything simulated must stay labeled SIMULATED, no area or route may be presented as official, and the required disclaimer must remain.

Legend entries audited (toggle in brackets):

| # | Legend entry | Toggle | Status found |
|---|---|---|---|
| A | Recommended route (algorithm-generated) | routes | Works |
| B | Alternative route (dashed) | routes | Works |
| C | Confirmed closure / restricted edge (red, hatched dash) | closures | Partially broken |
| D | Unverified report (triangle) | reports | Works |
| E | Corroborated report (diamond) | reports | Works |
| F | Disputed report (hollow square) | reports | Works |
| G | Verified destination with official source (star) | verified | Never fed (no data passed) |
| H | SIMULATED flood area | hazards | Broken |
| I | SIMULATED debris zone (dotted outline) | hazards | Broken |
| J | SIMULATED fire perimeter (dash-dot outline) | hazards | Broken |
| K | SIMULATED low-lying area (no depth values) | elevation | Broken |
| L | Incident boundary (dotted yellow) | incidents | Works when the incident has a boundary |
| M | CTA station (GTFS data only) | transit | Works when deployed; fails when the layer is unavailable |
| N | SIMULATED station (hexagon) | simulated_station | Works when deployed; fails when the layer is unavailable |
| O-R | Low-rise / Mid-rise / High-rise / Height Unknown building | buildings | Works |
| S | Start | markers | Works |
| T | Simulation destination | markers | Works |
| U | Street graph (faint) | graph | Broken (toggle on shows nothing) |

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a user selects the SIMULATED Flood incident (sim-flood-loop) with the "SIMULATED hazards" toggle on THEN the system draws no SIMULATED flood area, even though the scenario defines SIMULATED flood-prone segments for that incident

1.2 WHEN a user selects the SIMULATED Flood incident with the "SIMULATED low-lying areas" toggle on THEN the system draws no SIMULATED low-lying area

1.3 WHEN a user selects the SIMULATED Earthquake incident (sim-earthquake-debris) with the "SIMULATED hazards" toggle on THEN the system draws no SIMULATED debris zone (dotted outline)

1.4 WHEN a SIMULATED debris zone is an active area restriction for the selected incident (for example after the debris step is applied) THEN the system still draws nothing for it, because area restrictions without road segment ids are not shown at all

1.5 WHEN a user selects the SIMULATED Fire incident (sim-fire-downtown) with the "SIMULATED hazards" toggle on THEN the system draws no SIMULATED fire perimeter (dash-dot outline), even though the incident has a SIMULATED temporary perimeter notice with an area

1.6 WHEN a user selects an incident that already has active closures or restrictions created before the page was opened THEN the system draws no closure or restricted edge, because closures are only drawn from live events received during the session

1.7 WHEN active restrictions exist for the selected incident THEN the system never draws the "restricted edge (hatched)" style, because no restricted-edge data is ever supplied to the map

1.8 WHEN a user turns on the "Street graph (faint)" toggle THEN the system draws nothing, because no street graph data is ever supplied to the map

1.9 WHEN no data for an incident-dependent layer exists for the current selection (for example no incident selected, or an incident without a fire perimeter) THEN the system still lists that layer's entry in the legend, so the legend advertises symbols that cannot appear on the map

1.10 WHEN the CTA stations or SIMULATED station layer request fails (for example HTTP 503 because the layer is not in the loaded data) THEN the system shows no markers and no indication, and the legend still lists the entry

### Expected Behavior (Correct)

2.1 WHEN a user selects the SIMULATED Flood incident with the "SIMULATED hazards" toggle on THEN the system SHALL draw the scenario's SIMULATED flood area, labeled SIMULATED, with no water depth values and no claim that it is official

2.2 WHEN a user selects the SIMULATED Flood incident with the "SIMULATED low-lying areas" toggle on THEN the system SHALL draw the SIMULATED low-lying area (dashed outline, no depth values), OR, if the flood area and low-lying area are the same data, the legend SHALL describe it with a single entry so that no legend entry is left without a data source

2.3 WHEN a user selects the SIMULATED Earthquake incident with the "SIMULATED hazards" toggle on and the incident's SIMULATED debris zones are in effect for routing THEN the system SHALL draw each debris zone with the dotted outline, labeled SIMULATED, without requiring demo mode or the demo console

2.4 WHEN an area restriction (such as a SIMULATED debris zone) is active for the selected incident THEN the system SHALL draw its area on the map in the matching hazard style, and SHALL remove it when the restriction is removed

2.5 WHEN a user selects the SIMULATED Fire incident with the "SIMULATED hazards" toggle on THEN the system SHALL draw the SIMULATED fire perimeter with the dash-dot outline, labeled SIMULATED and not an official instruction

2.6 WHEN a user selects an incident that already has active closures or restrictions THEN the system SHALL draw them on selection (including after a page reload), and SHALL keep them in sync with later live add and remove events

2.7 WHEN active restrictions exist for the selected incident THEN the system SHALL draw the affected road segments in the "Confirmed closure / restricted edge (red, hatched dash)" style described by the legend

2.8 WHEN a user turns on the "Street graph (faint)" toggle THEN the system SHALL draw the faint street graph, OR the toggle and legend entry SHALL be removed if no street graph data can be provided

2.9 WHEN an incident-dependent layer (closures, hazards, low-lying area, incident boundary, verified destinations) has no data for the current selection THEN the system SHALL omit that entry from the legend, and SHALL show it again as soon as its data is present and its toggle is on

2.10 WHEN the CTA stations or SIMULATED station layer cannot be loaded THEN the system SHALL omit its legend entry and SHALL retry loading later, without blocking the rest of the map

2.11 WHEN the user switches from one incident to another THEN the system SHALL clear the previous incident's hazard areas, low-lying area, closures and boundary and draw only the newly selected incident's data

2.12 WHEN the user turns a layer toggle off THEN the system SHALL hide every map element of that layer and its legend entries

### Unchanged Behavior (Regression Prevention)

3.1 WHEN routes are calculated for a selected incident THEN the system SHALL CONTINUE TO draw the recommended route (solid) and alternative routes (dashed) labeled as algorithm-generated

3.2 WHEN community reports exist for the selected incident THEN the system SHALL CONTINUE TO draw unverified (triangle), corroborated (diamond) and disputed (hollow square) reports, never labeled as officially verified

3.3 WHEN no destination has an official source record THEN the system SHALL CONTINUE TO draw no verified destination star, and SHALL NOT present any SIMULATED destination as an official shelter

3.4 WHEN the selected incident has a geographic boundary (for example the SIMULATED Tornado incident) THEN the system SHALL CONTINUE TO draw it as a dotted yellow incident area labeled as not an official evacuation boundary

3.5 WHEN the buildings, CTA stations and SIMULATED station layers load successfully THEN the system SHALL CONTINUE TO draw buildings by height class (including the dashed Height Unknown outline), CTA station circles and the SIMULATED station hexagon

3.6 WHEN a start point and destination are chosen THEN the system SHALL CONTINUE TO draw the Start marker and the Simulation destination marker labeled not an official shelter

3.7 WHEN the extraction boundary and basemap are shown THEN the system SHALL CONTINUE TO draw them as they do today

3.8 WHEN no incident is selected THEN the system SHALL CONTINUE TO draw no hazard areas, closures or incident boundary

3.9 WHEN any SIMULATED item is drawn or listed in the legend THEN the system SHALL CONTINUE TO label it SIMULATED, show no water depth values, and keep the required disclaimer visible

3.10 WHEN routes are calculated THEN the system SHALL CONTINUE TO return the same routes as before for the same incident state, since this fix changes what is displayed, not how routes are computed

3.11 WHEN demo mode is enabled and the demo console applies steps THEN the system SHALL CONTINUE TO apply them, and the resulting closures and debris zones SHALL appear on the map through the same display path as for normal users

3.12 WHEN the user changes basemap appearance (light, dark, satellite) THEN the system SHALL CONTINUE TO restyle labels and keep all data layers visible
