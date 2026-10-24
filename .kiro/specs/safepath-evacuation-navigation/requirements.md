# Requirements Document

## Introduction

SafePath is an AI-assisted, disaster-aware evacuation navigation and real-time emergency communication web application built as a working end-to-end prototype for HackOHI/O 2026 (24-hour hackathon). The prototype covers downtown Chicago only (Loop, West Loop, Riverwalk, Clark/Lake CTA, Millennium Park, DePaul Loop Campus, UIC, and the UIC–downtown corridor) and is designed with a modular architecture that could scale to additional regions.

SafePath combines real geographic data (street centerlines, OpenStreetMap, USGS 3DEP elevation, CTA GTFS, building footprints), official emergency planning references (including the historical 2007 Chicago Central Business District Evacuation Plan), disaster-specific routing policies (flood, earthquake, tornado, fire/urban, building emergency), community hazard reporting with real-time synchronization, and AWS serverless cloud storage. Every rule, zone, closure, shelter, and destination carries provenance and an explicit classification of verified, unverified, unknown, or simulated.

SafePath is a research and demonstration prototype. SafePath is not an approved life-safety, emergency dispatch, or official evacuation system, and all demo scenarios are simulated. Where an external dependency, data source, or AWS permission is unavailable, SafePath uses a clearly labeled fallback, documents the limitation, and continues.

Implementation priority when time is limited: (1) real Chicago street network, (2) disaster-adjusted routing, (3) cloud-backed reports, (4) real-time updates between users, (5) polished demo. Optional AI and 3D features are lower priority than these core capabilities.

## Glossary

- **SafePath**: The complete prototype system described in this document, including Frontend, Backend_API, Routing_Engine, Data_Pipeline, and cloud infrastructure.
- **Frontend**: The React + TypeScript + Vite + Tailwind + MapLibre GL JS web client.
- **Map_View**: The center map component of the Frontend that renders geographic layers, routes, reports, and boundaries.
- **Backend_API**: The Python FastAPI service (deployed on AWS Lambda behind API Gateway or a documented substitute) exposing the `/api/*` endpoints.
- **Routing_Engine**: The backend component that computes paths over the Graph using A* or Dijkstra, independent of map display.
- **Graph**: The directed routing graph G=(V,E) built from processed Chicago data, stored as a versioned static dataset.
- **Node**: A vertex of the Graph representing an intersection, building entrance, pedestrian junction, or indoor location.
- **Edge**: A directed connection between two Nodes carrying the attributes defined in Requirement 12.
- **Indoor_Graph**: The Graph subset representing the fictional simulated multi-floor building used in the building-emergency module.
- **Level_Transition**: An explicitly defined Edge that connects Nodes at different infrastructure levels (for example, indoor to outdoor, street to lower-level road, platform to street).
- **Data_Pipeline**: The set of scripts that download, validate, process, and package geographic and official-source data.
- **Chicago_Extraction_Area**: The data extraction bounding box N 41.91, S 41.85, W -87.68, E -87.60; this box is a data boundary, not an official evacuation boundary.
- **CBD_Plan**: The 2007 Chicago Central Business District Evacuation Plan PDF published by the City of Chicago.
- **Official_Source**: An identifiable authoritative publisher (for example City of Chicago OEMC, UIC, Ready Illinois, Cook County Emergency Management, USGS, CTA, City of Chicago Data Portal).
- **Source_Record**: A provenance record containing source organization, URL, title, publication or update date, retrieval timestamp, geographic applicability, Data_Classification, verification status, page or section reference, and usage restrictions.
- **Source_Catalog**: The searchable collection of all Source_Records.
- **Data_Classification**: One of Historical, Static, Current, or Simulated, assigned to every rule, zone, closure, shelter, destination, and dataset.
- **Rules_Engine**: The backend component that evaluates structured evacuation decision criteria and Official_Notices to determine the Policy_Outcome for an Incident.
- **Disaster_Module**: A separate routing policy module for one disaster type; the modules are Flood_Module, Earthquake_Module, Tornado_Module, Fire_Module, and Building_Emergency_Module.
- **Hard_Restriction**: A confirmed closure, official prohibited area, or modeled impassable area that removes Edges from the routable Graph.
- **Penalty**: A configurable numeric cost added to an Edge cost for hazard or uncertainty without removing the Edge.
- **Route_Cost**: The Edge cost computed as travel_cost + disaster_hazard_penalty + uncertainty_penalty.
- **Policy_Outcome**: One of ROUTE_OPTIONS_AVAILABLE, SHELTER_IN_PLACE, FOLLOW_OFFICIAL_INSTRUCTIONS, NO_VERIFIED_ROUTE_AVAILABLE, or INSUFFICIENT_DATA.
- **Uncertainty_Level**: A categorical indicator (Low, Medium, High, Unknown) summarizing missing or unverified data along a route.
- **Incident**: A record describing an active real or simulated emergency, including incident type, geographic boundary, status, source, and Simulation_Flag.
- **Simulation_Flag**: A boolean attribute marking an Incident, hazard, closure, destination, or notice as simulated.
- **Official_Notice**: A record of an instruction, closure, perimeter, destination, or order attributed to an Official_Source.
- **Hazard_Report**: A community-submitted observation of a hazard condition.
- **Verification_Status**: One of Unverified, Corroborated, Officially_Verified, Disputed, Resolved, or Expired.
- **Report_Service**: The backend component that stores, updates, and publishes Hazard_Reports.
- **Realtime_Channel**: The publish/subscribe mechanism (AWS AppSync Events, or a documented lightweight WebSocket fallback) that pushes updates to connected clients.
- **Messaging_Service**: The backend component handling incident-based channel messages.
- **CheckIn_Service**: The backend component handling safety check-ins.
- **Auth_Service**: The authentication and authorization component (Amazon Cognito, or a documented substitute).
- **Guest**: A role for unauthenticated demo-session users.
- **User**: A role for registered account holders.
- **Moderator**: A role permitted to moderate reports and messages and, in demo mode, to confirm simulated closures.
- **Authorized_Official**: A reserved role permitted to issue Official_Notices; assignment occurs only through administrative configuration.
- **Demo_Admin_Console**: The Frontend screen used to start, reset, and control simulated scenarios.
- **Offline_Manager**: The Frontend service worker and local queue that provide offline behavior.
- **AI_Assistant**: The optional Amazon Bedrock-backed component that summarizes and explains information.
- **Infrastructure_Stack**: The AWS CDK (or equivalent IaC) definition of all deployed cloud resources.
- **Disclaimer_Banner**: The persistent UI text: "SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies."
- **Building_Height_Class**: One of High-rise, Mid-rise, Low-rise, or Unknown.

## Requirements

### Requirement 1: Safety Disclaimer and Prototype Positioning

**User Story:** As a member of the public, I want SafePath to clearly state that it is an experimental prototype, so that I do not mistake it for official emergency guidance.

#### Acceptance Criteria

1. THE Frontend SHALL display the Disclaimer_Banner text verbatim and untruncated on every screen, including the Map_View, the Demo_Admin_Console, and the Data Sources and Limitations screen, visible without user interaction at viewport widths from 320 to 2560 pixels, and SHALL provide no control that dismisses or hides the Disclaimer_Banner.
2. WHILE at least one Incident with Simulation_Flag set to true is active, THE Frontend SHALL display a "SIMULATED" indicator in the header of every screen.
3. THE Frontend SHALL display a "SIMULATED" label on every map feature whose Simulation_Flag is true, including Incidents, hazards, closures, destinations, and notices, and SHALL display the label within 5 seconds of the feature being rendered.
4. THE Frontend SHALL display emergency contact guidance instructing users to call 911 for immediate emergencies in a section visually separate from messaging, check-in, and Hazard_Report features, reachable within one user interaction from every screen.
5. THE SafePath documentation and UI text SHALL describe SafePath as a research and demonstration prototype and SHALL omit any claim of approval as a life-safety, dispatch, or official evacuation system.
6. THE SafePath UI text SHALL omit any claim that SafePath, the Messaging_Service, or the CheckIn_Service integrates with, forwards to, or notifies 911 or emergency dispatch services.
7. WHEN the Frontend displays a route, THE Frontend SHALL display the label "algorithm-generated" adjacent to that route's summary and SHALL omit any label stating that the route is certified safe, guaranteed safe, or officially approved.

### Requirement 2: Geographic Scope

**User Story:** As a demo presenter, I want the prototype limited to downtown Chicago, so that the data and scenarios stay accurate and manageable within the hackathon.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL clip all geographic datasets to the Chicago_Extraction_Area so that no processed feature geometry extends beyond the Chicago_Extraction_Area bounds.
2. THE Data_Pipeline SHALL produce a Graph containing at least one routable Node within each of the Loop, West Loop, Riverwalk, Clark/Lake CTA station area, Millennium Park, DePaul Loop Campus, UIC, and the UIC–downtown corridor.
3. WHILE the Chicago_Extraction_Area boundary is displayed on the Map_View, THE Frontend SHALL label the boundary as a data extraction boundary that is not an official evacuation boundary and SHALL render it with a line style distinct from every official evacuation boundary layer.
4. IF a route request specifies an origin or destination outside the Chicago_Extraction_Area, THEN THE Backend_API SHALL return an error indicating which location (origin, destination, or both) is outside the supported region and SHALL return no route, treating coordinates exactly on the boundary as inside.
5. IF a route request omits the origin or destination, or specifies a latitude outside -90 to 90 or a longitude outside -180 to 180, or a non-numeric coordinate, THEN THE Backend_API SHALL return an error indicating the invalid coordinate and SHALL return no route.
6. WHEN a client requests region metadata, THE Backend_API SHALL return for Chicago the extraction bounds (N 41.91, S 41.85, W -87.68, E -87.60), the list of supported neighborhoods from criterion 2, the coordinate reference system identifier, and the current Graph version identifier.

### Requirement 3: Street Network Data Acquisition

**User Story:** As a developer, I want to download real Chicago street and pedestrian data, so that routing operates on actual geography.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL retrieve street centerlines from the Chicago Data Portal dataset at https://data.cityofchicago.org/d/6imu-meau.
2. THE Data_Pipeline SHALL retrieve OpenStreetMap street, walkway, and pedestrian path data for the Chicago_Extraction_Area using OSMnx or an equivalent OSM client.
3. THE Data_Pipeline SHALL extract for each segment, where present in the source, the geometry, source identifier, road classification, one-way designation, pedestrian access, bridge indicator, tunnel indicator, and level or layer value.
4. IF a segment attribute listed in criterion 3 is absent from both the Chicago Data Portal and OpenStreetMap sources, THEN THE Data_Pipeline SHALL record the attribute value as Unknown and SHALL not substitute a default value such as false, zero, or empty.
5. IF the Chicago Data Portal and OpenStreetMap provide conflicting values for the same attribute of the same segment, THEN THE Data_Pipeline SHALL retain one value according to a documented source precedence order, record the source of the retained value, and record a warning in the summary report.
6. THE Data_Pipeline SHALL assign each segment an infrastructure_level value of surface, lower-level, elevated, bridge, tunnel, or Unknown, assigning lower-level, elevated, bridge, or tunnel only where the source data identifies that infrastructure, and SHALL flag river crossings as bridge or tunnel according to the source data.
7. THE Data_Pipeline SHALL create a Source_Record for each retrieved dataset, and the OpenStreetMap Source_Record SHALL include the ODbL license and the attribution requirement as usage restrictions.
8. WHILE OSM-derived data is rendered on the Map_View, THE Frontend SHALL display OpenStreetMap attribution on the Map_View, visible without user interaction.

### Requirement 4: Elevation Data Import

**User Story:** As a routing developer, I want approximate elevations on graph nodes and edges, so that flood routing can account for terrain.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL retrieve elevation data from USGS 3DEP covering the entire Chicago_Extraction_Area.
2. THE Data_Pipeline SHALL assign approximate terrain elevation, sampled at the Node coordinate, to each Node, and SHALL assign elevation_start and elevation_end to each Edge from its start and end coordinates.
3. THE Data_Pipeline SHALL store elevation values in meters together with the vertical datum of the source (NAVD 88 for USGS 3DEP).
4. THE Data_Pipeline SHALL assign each Edge an elevation_confidence value of High, Medium, Low, or Unknown, and SHALL set elevation_confidence to Low or Unknown for every Edge flagged as bridge, tunnel, elevated, lower-level, or underground.
5. IF USGS 3DEP data is unavailable for a Node or Edge endpoint, THEN THE Data_Pipeline SHALL record that elevation as Unknown rather than zero or an interpolated default, SHALL set the elevation_confidence of each affected Edge to Unknown, and SHALL report the count of Unknown elevations in the summary report.
6. THE SafePath documentation SHALL state that terrain elevation does not represent bridge deck, tunnel, or underground levels and that higher terrain does not guarantee flood safety.

### Requirement 5: Transit Data Import

**User Story:** As a user evacuating downtown, I want CTA stations shown on the map, so that I can see transit infrastructure relative to hazards.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL retrieve the CTA GTFS static feed from https://www.transitchicago.com/developers/gtfs/ and extract the location, identifier, name, and served routes of every station located within the Chicago_Extraction_Area.
2. WHERE authorized CTA real-time credentials are configured, WHEN a client requests transit alerts, THE Backend_API SHALL retrieve CTA service alerts from https://transitdata.transitchicago.com/ and label each alert with a Current Data_Classification and its retrieval timestamp.
3. WHERE authorized CTA real-time credentials are configured, IF the CTA service alert retrieval fails or receives no response within 10 seconds, THEN THE Backend_API SHALL return the most recently retrieved alerts with their original retrieval timestamp and a stale indicator, or an indication that live transit alerts are unavailable when no prior alerts exist.
4. WHERE CTA real-time credentials are not configured, THE Frontend SHALL display a message stating that live transit alerts are unavailable in the location where transit alerts would appear.
5. THE Routing_Engine SHALL treat station entrance safety status as Unknown unless an active Official_Notice or a Hazard_Report with Officially_Verified Verification_Status provides the status.
6. THE SafePath demo data SHALL include a simplified station model with at least a street level and a platform level connected only by Level_Transition Edges, labeled with a Simulated Data_Classification.

### Requirement 6: Building Data Import

**User Story:** As a routing developer, I want real building footprints with heights where known, so that earthquake and building scenarios reference actual structures.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL retrieve building footprints from OpenStreetMap for the Chicago_Extraction_Area.
2. WHEN a building record includes a height or floor-count attribute with a positive numeric value, THE Data_Pipeline SHALL attach the value and its source to the building, and SHALL treat zero, negative, or non-numeric values as absent.
3. THE Data_Pipeline SHALL assign each building a Building_Height_Class using height when present (Low-rise below 12 meters, Mid-rise from 12 to below 35 meters, High-rise 35 meters or above), otherwise floor count (Low-rise 1 to 3 floors, Mid-rise 4 to 11 floors, High-rise 12 or more floors), and SHALL assign Unknown when neither attribute is present.
4. THE Data_Pipeline SHALL store only building attributes present in the source data and SHALL leave facade, structural condition, and construction properties unset.
5. WHEN a user selects a building on the Map_View, THE Frontend SHALL display Unknown for every displayed building attribute lacking source data.

### Requirement 7: Data Processing Pipeline and Fallbacks

**User Story:** As a developer, I want reproducible data scripts with a summary report, so that the processed Chicago graph can be rebuilt and audited.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL provide the scripts download_chicago_data.py, process_streets.py, build_graph.py, import_elevation.py, import_emergency_plan.py, and seed_demo_incidents.py, each runnable through a command documented in the repository.
2. WHEN the Data_Pipeline runs, THE Data_Pipeline SHALL perform the stages in order: check source availability, save raw data, reproject to a single documented coordinate reference system, clip to the Chicago_Extraction_Area, clean geometry, normalize attributes, build Nodes and Edges, and save processed outputs.
3. WHEN the Data_Pipeline completes, THE Data_Pipeline SHALL write a summary report containing, for each dataset, the feature count, source URL, retrieval timestamp, acquisition status (freshly downloaded, reused previous copy, or missing), and warnings.
4. IF a data source fails to respond within 60 seconds on each of 3 consecutive attempts during a run, THEN THE Data_Pipeline SHALL reuse the most recent previously downloaded copy, record a warning containing that copy's retrieval timestamp in the summary report, and continue processing.
5. IF a data source is unavailable and no previous copy exists, THEN THE Data_Pipeline SHALL record the source as missing in the summary report and SHALL complete processing of the remaining sources.
6. IF a processing stage fails for a dataset after acquisition, THEN THE Data_Pipeline SHALL record the dataset and the failed stage as an error in the summary report, exclude that dataset's partial outputs from the processed data, and continue processing the remaining datasets.
7. IF neither the Chicago Data Portal street centerlines nor the OpenStreetMap street data are available from a fresh download or a previous copy, THEN THE Data_Pipeline SHALL exit with a failure status and SHALL leave the previously saved processed Graph unchanged.
8. THE Data_Pipeline SHALL save each processed Graph as a versioned static dataset with a unique version identifier and build timestamp, without overwriting previously saved Graph versions.
9. THE repository SHALL exclude all raw downloaded data files and any data file larger than 50 MB from version control and SHALL document the commands that reproduce them.

### Requirement 8: Chicago CBD Evacuation Plan Ingestion

**User Story:** As a planner, I want the 2007 Chicago CBD Evacuation Plan analyzed and referenced, so that SafePath reflects official planning concepts without presenting them as current orders.

#### Acceptance Criteria

1. THE Data_Pipeline SHALL download the CBD_Plan from https://webapps1.chicago.gov/ChicagoAlertWeb/resources/pdf/CBD_Evac_Plan_final_30-5-07.pdf.
2. WHERE the CBD_Plan source terms permit local storage, THE Data_Pipeline SHALL store a local copy of the CBD_Plan and record the storage permission determination in its Source_Record.
3. THE Data_Pipeline SHALL assign the CBD_Plan a Historical Data_Classification with the 2007 publication date stated in the CBD_Plan.
4. THE Data_Pipeline SHALL extract from the CBD_Plan into structured records: Evacuation Levels I, II, and III with their activation conditions, shelter-in-place versus evacuation criteria, activation authority, CBD boundaries, hot and cold zone definitions, transportation coordination, road and expressway closure procedures, CTA and Metra coordination, Assembly and Transportation Centers, temporary shelters, communication procedures, and the roles of OEMC, police, fire, and transportation agencies, and SHALL record any listed topic not found in the CBD_Plan as not found.
5. THE Data_Pipeline SHALL attach to each extracted record the CBD_Plan page number and section heading from which the record was extracted.
6. THE Data_Pipeline SHALL record the CBD boundary as described in the CBD_Plan (approximately Division Street to Roosevelt Road and Halsted Street to Lake Michigan), labeled as approximate, with the CBD_Plan as its Source_Record.
7. THE Source_Catalog SHALL contain, for each appendix referenced by the CBD_Plan and absent from the PDF, and for newer versions of the CBD_Plan, a search record stating the search date and the result (located with URL, or not located).
8. IF a referenced appendix or a newer CBD_Plan version is not located, THEN THE Source_Catalog SHALL record the item as unavailable, and THE Data_Pipeline SHALL leave the corresponding content unpopulated rather than inferring it.
9. THE Rules_Engine SHALL exclude CBD_Plan-derived records from evaluation as active Official_Notices.
10. THE Frontend SHALL display the label "Historical reference (2007), not a current evacuation order" adjacent to every displayed item of CBD_Plan-derived content, including map layers, Source_Records, and route explanations.

### Requirement 9: Additional Official Source Review

**User Story:** As a user, I want SafePath to reference current official preparedness guidance, so that I am directed to authoritative information.

#### Acceptance Criteria

1. THE Source_Catalog SHALL include Source_Records for Chicago OEMC (https://www.chicago.gov/city/en/depts/oem.html), UIC building-specific emergency action plans (https://ehso.uic.edu/fire-safety/building-specific-emergency-action-plans/), UIC Ready (https://ready.uic.edu/), Ready Illinois (https://ready.illinois.gov/), and the Cook County Hazard Mitigation Plan (https://www.cookcountyemergencymanagement.org/hmp).
2. THE Source_Catalog SHALL record, for each UIC Source_Record, a reuse determination listing the content approved for public reuse and the content excluded from reuse.
3. THE Data_Pipeline SHALL exclude from SafePath data all UIC-sourced building access codes, security procedures, restricted-area locations, and floor plans lacking a public-reuse determination.
4. THE Building_Emergency_Module SHALL use a fictional indoor floor plan unless the Source_Catalog contains a reuse determination confirming that a real floor plan is publicly published and reusable.
5. THE Source_Catalog SHALL classify Cook County Hazard Mitigation Plan areas as long-term mitigation planning areas, and THE Rules_Engine SHALL exclude those areas from use as live Incident boundaries.
6. THE Frontend SHALL display links to OEMC, Ready Illinois, and UIC Ready guidance in the official information section of the left sidebar, each link targeting the URL listed in criterion 1.
7. IF a source URL listed in criterion 1 fails to respond within 60 seconds during source review, THEN THE Source_Catalog SHALL record the URL as unreachable with the check date and SHALL retain the most recent existing Source_Record for that source.

### Requirement 10: Source Provenance and Catalog

**User Story:** As a user, I want to see where every official-looking rule or zone came from, so that I can judge its authority and currency.

#### Acceptance Criteria

1. THE SafePath system SHALL attach a Source_Record to every rule, zone, closure, shelter, destination, Official_Notice, and dataset.
2. WHEN a client calls GET /api/sources, THE Backend_API SHALL return the matching Source_Records within 2 seconds, supporting case-insensitive substring search by organization and title and exact-match filtering by Data_Classification and verification status.
3. WHEN a user selects a feature in an official-data layer, THE Frontend SHALL display within 1 second the source organization, URL, title, publication or update date, retrieval timestamp, Data_Classification, verification status, and page or section reference from the feature's Source_Record.
4. THE SafePath system SHALL apply the label "Official" only to content whose Source_Record identifies an Official_Source.
5. IF content lacks an identifiable Official_Source, THEN THE Frontend SHALL label the content as Simulated when its Simulation_Flag is true, as Community when the content originates from a Hazard_Report or message, and as Unknown otherwise.
6. THE Frontend SHALL provide a Data Sources and Limitations screen listing all Source_Records and documented limitations.
7. IF a GET /api/sources request specifies a Data_Classification or verification status filter value outside the defined values, THEN THE Backend_API SHALL return HTTP status 400 with an error listing the permitted values.
8. IF a rule, zone, closure, shelter, or destination lacks a Source_Record, THEN THE Rules_Engine SHALL exclude the item from Policy_Outcome evaluation and THE Frontend SHALL label the item "Unknown source".

### Requirement 11: Evacuation Rules Engine

**User Story:** As an emergency planner, I want evacuation decision criteria represented as structured rules, so that routing follows incident-specific official instructions instead of a fixed route.

#### Acceptance Criteria

1. THE Rules_Engine SHALL represent evacuation decision criteria as structured rule records, each containing a rule identifier, version number, Data_Classification, effective timestamp, optional expiration timestamp, and Source_Record.
2. THE Rules_Engine SHALL support Official_Notices of type temporary perimeter, road closure, incident-specific destination, shelter-in-place instruction, road-direction restriction, and incident-specific transportation rule.
3. THE Routing_Engine SHALL compute each route at request time from the Incident state, active Official_Notices, and Hard_Restrictions in effect at the request timestamp, and SHALL use no precomputed permanent evacuation route.
4. WHEN an active Official_Notice instructs shelter-in-place for an area containing the requester's origin, THE Rules_Engine SHALL return a Policy_Outcome of SHELTER_IN_PLACE regardless of route travel time.
5. WHEN an active Official_Notice designates an incident-specific destination, THE Rules_Engine SHALL rank the designated destination ahead of every non-official destination.
6. WHEN an active Official_Notice requires users to follow instructions that SafePath cannot model, THE Rules_Engine SHALL return a Policy_Outcome of FOLLOW_OFFICIAL_INSTRUCTIONS with the notice text and Source_Record.
7. THE Routing_Engine SHALL apply road-direction restrictions from active Official_Notices as directed Edge constraints that exclude traversal in the prohibited direction.
8. IF two or more active Official_Notices give conflicting instructions for an area containing the requester's origin, THEN THE Rules_Engine SHALL return a Policy_Outcome of FOLLOW_OFFICIAL_INSTRUCTIONS with the text and Source_Record of each conflicting notice.
9. WHEN an Official_Notice passes its expiration timestamp, THE Rules_Engine SHALL exclude the notice from evaluation of route requests received more than 60 seconds after the expiration timestamp.

### Requirement 12: Routing Graph Model

**User Story:** As a routing developer, I want a well-defined graph schema, so that routing logic and explanations rely on consistent attributes.

#### Acceptance Criteria

1. THE Graph SHALL contain Nodes representing intersections, building entrances, pedestrian junctions, and indoor locations.
2. THE Graph SHALL store for each Edge: edge_id, from_node, to_node, geometry, distance_meters, estimated_travel_time_seconds, travel_mode, source, road_type, elevation_start, elevation_end, elevation_confidence, bridge_flag, tunnel_flag, infrastructure_level, pedestrian_access, mobility_accessibility, current_closure_status, closure_verification, hazard_flags, and data_timestamp.
3. WHEN an Edge attribute value is missing from all sources, THE Graph SHALL store the value as Unknown and SHALL store no substituted default value.
4. THE Routing_Engine SHALL apply an uncertainty_penalty greater than zero for each Unknown attribute value on an Edge rather than treating the Unknown value as safe.
5. THE Graph SHALL connect Nodes at different infrastructure levels only through an explicit Level_Transition Edge.
6. THE Graph SHALL represent one-way streets as directed Edges that permit vehicle travel_mode only in the designated direction.
7. THE Graph SHALL store a distance_meters value greater than zero and an estimated_travel_time_seconds value greater than zero for every Edge.
8. IF an Edge references a from_node or to_node absent from the Graph, THEN THE Data_Pipeline SHALL exclude the Edge from the Graph and record a warning identifying the edge_id in the summary report.

### Requirement 13: Route Calculation and Cost Model

**User Story:** As an evacuee, I want routes that account for disaster hazards and data uncertainty, so that I can choose a path with understood risks.

#### Acceptance Criteria

1. THE Routing_Engine SHALL compute paths using A* or Dijkstra over the Graph, independent of the Map_View rendering layer.
2. THE Routing_Engine SHALL compute Route_Cost for each Edge as travel_cost + disaster_hazard_penalty + uncertainty_penalty, with non-negative Penalty weights defined in a configuration file.
3. THE Routing_Engine SHALL support the route types shortest, disaster-adjusted, alternate, and no-verified-route.
4. WHEN a route request specifies alternatives as an integer from 2 to 3, THE Routing_Engine SHALL return up to the requested number of valid routes, each differing from every other returned route by at least one Edge, together with the count of routes returned.
5. THE Routing_Engine SHALL record for each Edge on a returned route the Penalty components that contributed to its Route_Cost.
6. WHEN no path exists between origin and destination after applying Hard_Restrictions, THE Routing_Engine SHALL return a Policy_Outcome of NO_VERIFIED_ROUTE_AVAILABLE with an explanation.
7. IF the Graph lacks the data required to evaluate the request, THEN THE Routing_Engine SHALL return a Policy_Outcome of INSUFFICIENT_DATA with a list of the missing data items.
8. WHEN a mobility_profile requiring step-free access is specified, THE Routing_Engine SHALL exclude Edges whose mobility_accessibility is known to be inaccessible and SHALL apply an uncertainty_penalty greater than zero to Edges whose mobility_accessibility is Unknown.
9. THE Routing_Engine SHALL compute routes while the AI_Assistant is disabled or unavailable.
10. WHEN a route request is received for an origin and destination within the Chicago_Extraction_Area, THE Routing_Engine SHALL return the computed result within 5 seconds under demo load.
11. IF a route request specifies alternatives less than 1, greater than 3, or as a non-integer value, THEN THE Backend_API SHALL return HTTP status 400 with an error stating the permitted range of 1 to 3.
12. IF the Penalty configuration file is missing or contains a negative or non-numeric weight, THEN THE Backend_API SHALL return HTTP status 503 for route requests with an error identifying the invalid configuration.

### Requirement 14: Hard Restrictions

**User Story:** As an evacuee, I want confirmed closures and prohibited areas fully excluded, so that the engine never trades safety for time.

#### Acceptance Criteria

1. THE Routing_Engine SHALL remove from the routable Graph every Edge subject to a Hard_Restriction before path search.
2. THE Routing_Engine SHALL apply Hard_Restrictions in a processing step separate from Penalty calculation.
3. THE Routing_Engine SHALL treat Edges with a confirmed closure (Officially_Verified status, or a demo-mode Moderator-confirmed simulated closure) as Hard_Restrictions.
4. THE Routing_Engine SHALL treat Edges whose geometry shares at least one point with an official prohibited area or modeled impassable area as Hard_Restrictions.
5. WHILE a Hazard_Report is Unverified, Corroborated, or Disputed, THE Routing_Engine SHALL apply the report as a Penalty and warning and SHALL exclude the report from Hard_Restriction processing.
6. THE Routing_Engine SHALL report the count of excluded Edges in each route response.
7. WHEN a Hard_Restriction is removed because the source report is Resolved or Expired or a Moderator removes the simulated closure, THE Routing_Engine SHALL restore the affected Edges to the routable Graph for route requests received more than 5 seconds after the removal.
8. IF Hard_Restrictions remove every Edge connected to the origin or destination Node, THEN THE Routing_Engine SHALL return a Policy_Outcome of NO_VERIFIED_ROUTE_AVAILABLE with an explanation identifying the isolated endpoint (origin, destination, or both).

### Requirement 15: Route Response and Explanation

**User Story:** As an evacuee, I want each route to explain its distance, timing, restrictions, and uncertainties, so that I understand why it was chosen.

#### Acceptance Criteria

1. WHEN the Routing_Engine returns a route, THE Backend_API SHALL include route_id, path geometry, distance_meters, estimated_duration_seconds, hazard_flags, excluded_edges_count, uncertainty_level, policy_outcome, sources, calculated_at, and status.
2. WHEN the Routing_Engine returns a route, THE Backend_API SHALL include the list of restrictions considered and the data timestamp of the most recent update applied.
3. WHEN multiple routes are returned, THE Backend_API SHALL include for each route a reason summary stating the differences in distance, duration, and each Route_Cost component relative to the other returned routes.
4. WHEN the Frontend displays a route, THE Frontend SHALL display the route's Uncertainty_Level as a text label and the route's confidence limitations adjacent to the route summary.
5. WHEN the Policy_Outcome is other than ROUTE_OPTIONS_AVAILABLE, THE Backend_API SHALL return an empty path geometry together with the Policy_Outcome explanation.
6. THE Backend_API SHALL include in the sources field of each route response the Source_Record identifiers of every Official_Notice, Hard_Restriction, and Hazard_Report that affected the route.

### Requirement 16: Dynamic Route Updates

**User Story:** As an evacuee, I want my route recalculated when a closure is verified, so that I avoid newly blocked paths.

#### Acceptance Criteria

1. WHEN a Hazard_Report is stored, THE Report_Service SHALL publish the report through the Realtime_Channel to clients subscribed to the report's Incident.
2. WHEN a client receives a Hazard_Report, THE Map_View SHALL display the report marker on the affected location within 5 seconds of publication.
3. WHEN a Hazard_Report reaches Officially_Verified status or a Moderator confirms a simulated closure, THE Routing_Engine SHALL apply the closure as a Hard_Restriction to the affected Edges for route requests received more than 5 seconds after the status change.
4. WHEN a Hard_Restriction is added to an Edge on a client's active route, THE Frontend SHALL request route recalculation within 5 seconds of receiving the event and SHALL display the updated route with an explanation identifying the closed Edges and the cause of the change.
5. WHILE a Hazard_Report is Unverified or Corroborated, THE Report_Service SHALL keep the report visible as a warning and SHALL create no closure from the report.
6. IF a route recalculation request fails or returns a Policy_Outcome other than ROUTE_OPTIONS_AVAILABLE, THEN THE Frontend SHALL mark the previously displayed route "Route no longer valid" and SHALL display the returned Policy_Outcome or error text.
7. WHEN multiple Hard_Restriction events affecting the same active route arrive within 5 seconds, THE Frontend SHALL send one recalculation request that reflects all of the received events.

### Requirement 17: Flood Module

**User Story:** As a downtown resident during flooding, I want routes that avoid flooded and low-lying infrastructure, so that I reduce exposure to water.

#### Acceptance Criteria

1. WHILE a flood Incident is active, THE Flood_Module SHALL treat Edges intersecting confirmed closures or modeled impassable flood polygons as Hard_Restrictions.
2. WHILE a flood Incident is active, THE Flood_Module SHALL apply a disaster_hazard_penalty greater than zero to Edges flagged as tunnel or underpass, to Edges with infrastructure_level lower-level, and to lower-level Edges within 50 meters of the Chicago River or the Lake Michigan shoreline.
3. WHILE a flood Incident is active, THE Flood_Module SHALL apply an elevation-aware Penalty that does not increase as the average of an Edge's elevation_start and elevation_end increases, among Edges with known elevation.
4. WHILE a flood Incident is active, THE Flood_Module SHALL apply an uncertainty_penalty greater than zero to Edges whose elevation_confidence is Low or Unknown.
5. WHEN an active Official_Notice reports a flood warning or a flooded segment, THE Flood_Module SHALL display the warning text with its Source_Record and SHALL treat each Edge on an officially reported flooded segment as a Hard_Restriction.
6. THE Flood_Module SHALL label flood polygons used in demos as Simulated and SHALL omit water-depth values absent from a source.
7. THE Flood_Module SHALL describe elevation-favored routes as lower modeled exposure and SHALL omit any statement that a route is flood-safe.
8. IF a flood Incident has no associated flood polygon, Official_Notice, or confirmed closure, THEN THE Flood_Module SHALL apply only the infrastructure and elevation Penalties and SHALL set the route Uncertainty_Level to High.

### Requirement 18: Earthquake Module

**User Story:** As a person downtown after an earthquake, I want routes that avoid damaged infrastructure and debris, so that I stay clear of known hazards.

#### Acceptance Criteria

1. WHILE an earthquake Incident is active, THE Earthquake_Module SHALL treat Edges intersecting confirmed bridge closures, collapse exclusion zones, or debris zones as Hard_Restrictions.
2. WHEN an active Official_Notice reports earthquake damage to a road segment, bridge, or building, THE Earthquake_Module SHALL treat the Edges identified by the notice as Hard_Restrictions and display the notice with its Source_Record.
3. THE Earthquake_Module SHALL apply a high-rise proximity Penalty only to Edges within 50 meters of a building associated with an active debris zone, Official_Notice, or Hazard_Report.
4. THE Earthquake_Module SHALL apply no Penalty based on Building_Height_Class alone.
5. WHEN confirmed structural information from an Official_Source exists for a building, THE Earthquake_Module SHALL use the confirmed information in place of height-based heuristics.
6. THE Earthquake_Module SHALL label debris polygons used in demos as Simulated.

### Requirement 19: Tornado Module

**User Story:** As a person downtown during a tornado warning, I want shelter guidance instead of an outdoor route, so that I am not directed into exposed streets.

#### Acceptance Criteria

1. WHILE a tornado warning Incident is active, WHEN a route request has an origin inside the Incident boundary, THE Tornado_Module SHALL return a Policy_Outcome of SHELTER_IN_PLACE in place of an outdoor route.
2. WHILE a tornado warning Incident is active, THE Frontend SHALL display official tornado shelter guidance with its Source_Record.
3. WHEN a shelter whose Source_Record identifies an Official_Source exists within the tornado warning Incident boundary, THE Tornado_Module SHALL include that shelter and its Source_Record in the response.
4. IF no shelter with an Official_Source Source_Record exists within the tornado warning Incident boundary, THEN THE Tornado_Module SHALL state that no verified shelter is available and display official shelter-in-place guidance.
5. WHILE a tornado warning Incident is active, THE Tornado_Module SHALL exclude outdoor street Edges from any returned route.
6. WHEN an active Official_Notice provides a tornado shelter instruction, THE Tornado_Module SHALL return the instruction text and Source_Record ahead of any computed route, regardless of Route_Cost.
7. WHEN a tornado warning Incident status changes from active, THE Tornado_Module SHALL resume outdoor route computation for route requests received after the status change.

### Requirement 20: Fire and Urban Hazard Module

**User Story:** As a person near an urban fire, I want routes that avoid fire perimeters and smoke, so that I follow verified instructions and avoid blocked streets.

#### Acceptance Criteria

1. WHILE a fire Incident is active, THE Fire_Module SHALL treat Edges intersecting a verified fire perimeter or official prohibited area as Hard_Restrictions.
2. WHILE a fire Incident is active, THE Fire_Module SHALL treat confirmed blocked streets as Hard_Restrictions.
3. WHILE a fire Incident is active, THE Fire_Module SHALL apply a disaster_hazard_penalty greater than zero to Edges within 100 meters of an Unverified, Corroborated, or Disputed fire/smoke Hazard_Report.
4. WHEN an official fire instruction is active, THE Fire_Module SHALL display the instruction with its Source_Record and THE Rules_Engine SHALL apply the instruction ahead of any computed route.
5. THE SafePath demo data SHALL include a simulated downtown fire scenario with Simulation_Flag set to true and labeled as Simulated.
6. IF a fire perimeter lacks an Official_Source Source_Record and lacks Officially_Verified status, THEN THE Fire_Module SHALL apply the perimeter as a Penalty and warning and SHALL exclude the perimeter from Hard_Restriction processing.

### Requirement 21: Building Emergency Module and Indoor Graph

**User Story:** As a building occupant, I want to see reported conditions of stairwells and exits, so that I can choose an exit path informed by others' reports and official instructions.

#### Acceptance Criteria

1. THE Indoor_Graph SHALL model a fictional simulated building near downtown Chicago with at least three floors, at least two zones and two corridors per floor, three stairwells (A, B, C), and at least two exits.
2. THE Indoor_Graph SHALL connect each building exit to an outdoor Graph Node through an explicit Level_Transition Edge.
3. WHEN a user reports a stairwell as blocked or inaccessible, THE Building_Emergency_Module SHALL display the stairwell with the reported condition and its Verification_Status to all users subscribed to the Incident within 5 seconds of report storage.
4. WHEN a stairwell closure is confirmed by a Moderator in demo mode or by an Authorized_Official, THE Building_Emergency_Module SHALL remove the stairwell Edges from the routable Indoor_Graph for route requests received more than 5 seconds after the confirmation.
5. THE Building_Emergency_Module SHALL present stairwell and exit conditions as reported conditions and SHALL omit any guarantee that a stairwell or exit is safe.
6. THE Frontend SHALL label the building and floor plan as fictional and Simulated.
7. IF every stairwell connecting a floor to an exit is removed from the routable Indoor_Graph, THEN THE Building_Emergency_Module SHALL return a Policy_Outcome of NO_VERIFIED_ROUTE_AVAILABLE for origins on that floor and THE Frontend SHALL display the emergency contact guidance.
8. IF a Guest or User attempts to confirm a stairwell closure, THEN THE Backend_API SHALL reject the request with HTTP status 403 and leave the Indoor_Graph unchanged.

### Requirement 22: User Accounts and Roles

**User Story:** As a user, I want to participate as a guest or registered user with a private display name, so that I can contribute without revealing my identity.

#### Acceptance Criteria

1. THE Auth_Service SHALL support guest demo sessions that expire 24 hours after creation, and registered accounts.
2. THE Auth_Service SHALL assign each account exactly one role from Guest, User, Moderator, or Authorized_Official.
3. THE Auth_Service SHALL assign Moderator and Authorized_Official roles only through administrative configuration.
4. IF a user requests assignment of the Moderator or Authorized_Official role to the user's own account, THEN THE Auth_Service SHALL reject the request with HTTP status 403 and record the attempt in the audit log.
5. THE Report_Service SHALL display for each reporter a public anonymous alias that contains no part of the reporter's display name, email address, or account identifier.
6. THE Auth_Service SHALL store privacy preferences for each account, defaulting to location sharing disabled.
7. IF a request carries a missing, expired, or invalid authentication token for an operation requiring authentication, THEN THE Auth_Service SHALL reject the request with HTTP status 401.

### Requirement 23: Hazard Reporting

**User Story:** As a community member, I want to report hazards on the map, so that others nearby can see conditions I have observed.

#### Acceptance Criteria

1. THE Report_Service SHALL accept Hazard_Reports in the categories blocked road, flooding, fallen debris, fire/smoke, blocked building exit, inaccessible stairwell, transit closure, person needing assistance, and other.
2. THE Report_Service SHALL store for each Hazard_Report: report ID, incident ID, category, description, position or map feature reference, created timestamp, updated timestamp, anonymous public alias, optional photo reference, source category, Verification_Status, expiration timestamp, and status history.
3. WHEN a Hazard_Report is created, THE Report_Service SHALL set the Verification_Status to Unverified.
4. WHEN a Hazard_Report references a map position, THE Report_Service SHALL associate the report with the identifiers of Edges within a configured distance of the position, defaulting to 25 meters.
5. WHERE photo upload is enabled, THE Report_Service SHALL accept JPEG or PNG photos of at most 10 MB, store report photos in S3 with private access, and serve the photos through URLs that expire 15 minutes after issue.
6. THE Frontend SHALL provide a Submit Report screen reachable from the bottom controls in one user interaction.
7. IF a Hazard_Report omits the category or the position, specifies a category outside criterion 1, or contains a description longer than 1000 characters, THEN THE Report_Service SHALL reject the report with HTTP status 400 and an error identifying the invalid field.
8. IF a Hazard_Report position is outside the Chicago_Extraction_Area, THEN THE Report_Service SHALL reject the report with HTTP status 400 stating that the location is outside the supported region.
9. WHERE photo upload is enabled, IF a submitted photo exceeds 10 MB or is not JPEG or PNG, THEN THE Report_Service SHALL reject the photo with HTTP status 413 or 415 respectively and store no photo.

### Requirement 24: Report Verification Lifecycle

**User Story:** As a user, I want report status to show whether a hazard is corroborated by others or verified by officials, so that I can weigh the information correctly.

#### Acceptance Criteria

1. THE Report_Service SHALL assign each Hazard_Report a Verification_Status from Unverified, Corroborated, Officially_Verified, Disputed, Resolved, or Expired.
2. WHEN the number of distinct users other than the reporter confirming a Hazard_Report reaches the configured corroboration threshold, defaulting to 2, THE Report_Service SHALL set the Verification_Status to Corroborated.
3. THE Report_Service SHALL set Officially_Verified status only upon action by an Authorized_Official, or upon a Moderator confirmation in demo mode labeled as a simulated verification, and SHALL set no Officially_Verified status from corroboration count.
4. WHEN the number of distinct users disputing a Hazard_Report reaches the configured dispute threshold, defaulting to 2, THE Report_Service SHALL set the Verification_Status to Disputed.
5. WHEN a Hazard_Report passes its expiration timestamp, defaulting to 4 hours after creation, without being Resolved, THE Report_Service SHALL set the Verification_Status to Expired within 60 seconds and THE Map_View SHALL display the report with the label "Expired".
6. WHEN a Moderator or Authorized_Official marks a Hazard_Report resolved, THE Report_Service SHALL set the Verification_Status to Resolved and remove any closure created from the report.
7. THE Report_Service SHALL record every Verification_Status change with timestamp, acting role, and previous status in the report history.
8. THE Frontend SHALL display Corroborated status with the text "Corroborated by community — not officially verified".
9. IF a Guest or User attempts to set a Hazard_Report to Officially_Verified or Resolved, THEN THE Report_Service SHALL reject the request with HTTP status 403 and leave the Verification_Status unchanged.
10. WHEN two or more Verification_Status changes for the same Hazard_Report are received concurrently, THE Report_Service SHALL apply the changes sequentially in server receipt order and record each change in the report history.

### Requirement 25: Real-Time Synchronization

**User Story:** As a user, I want to see other users' reports and status changes without refreshing, so that I have current community information.

#### Acceptance Criteria

1. WHEN a Hazard_Report, message, check-in, or Verification_Status change is stored, THE Backend_API SHALL publish the event, with a unique event identifier, through the Realtime_Channel for the related Incident.
2. WHEN a subscribed client receives a Realtime_Channel event, THE Frontend SHALL update the Map_View and right sidebar without a page reload.
3. THE Realtime_Channel SHALL deliver events to connected subscribed clients within 5 seconds of storage with up to 50 concurrently connected clients.
4. IF AWS AppSync Events is unavailable in the AWS environment, THEN THE SafePath system SHALL use a lightweight WebSocket backend and document the substitution.
5. WHILE the Realtime_Channel connection is disconnected, THE Frontend SHALL display, within 10 seconds of disconnection, a stale-data warning with the time of the last received update.
6. WHEN the Realtime_Channel connection is restored, THE Frontend SHALL fetch the events stored since the last received event and apply each event once.
7. WHILE the Realtime_Channel connection is disconnected, THE Frontend SHALL attempt reconnection at intervals starting at 1 second and increasing to at most 30 seconds, and SHALL poll the Backend_API for updates at an interval of no less than 30 seconds.
8. WHEN a client receives a Realtime_Channel event whose event identifier the client has already applied, THE Frontend SHALL discard the duplicate event.

### Requirement 26: Incident Communication Channels

**User Story:** As a user involved in an incident, I want an incident-specific channel, so that I can read updates and share observations with others affected.

#### Acceptance Criteria

1. THE Messaging_Service SHALL provide exactly one communication channel per Incident.
2. THE Messaging_Service SHALL support the message types update, observation, assistance request, person safe, accessibility assistance request, and blocked-route update.
3. THE Messaging_Service SHALL store each message with message_id, incident_id, sender_id, message_type, content of 1 to 1000 characters, approximate_location, and created_at.
4. THE Frontend SHALL display each message with its timestamp and a source label of Community or Official.
5. THE Frontend SHALL display Official_Notices in a visually distinct section from community messages, using both a text label and a distinct style.
6. WHEN a Moderator hides a message, THE Messaging_Service SHALL remove the message from public channel views within 5 seconds and record the action in the audit log.
7. IF a message has empty content, content longer than 1000 characters, or a message_type outside criterion 2, THEN THE Messaging_Service SHALL reject the message with HTTP status 400 and an error identifying the invalid field.
8. IF a message references an incident_id that does not exist, THEN THE Messaging_Service SHALL reject the message with HTTP status 404.
9. IF a Guest or User attempts to hide a message, THEN THE Messaging_Service SHALL reject the request with HTTP status 403 and leave the message visible.

### Requirement 27: Safety Check-In

**User Story:** As an evacuee, I want to post a safety status, so that others in my incident channel know whether I am safe or need help.

#### Acceptance Criteria

1. THE CheckIn_Service SHALL accept check-in statuses "I'm safe", "I need assistance", "I cannot reach my planned destination", and "My route is blocked".
2. THE CheckIn_Service SHALL store each check-in with checkin_id, incident_id, user_id, status, optional_location, created_at, and visibility.
3. THE CheckIn_Service SHALL default check-in visibility to exclude location information.
4. WHERE a user opts in to location sharing, THE CheckIn_Service SHALL store and display only a coarse location rounded to a configured precision of no finer than approximately 200 meters.
5. THE CheckIn_Service SHALL delete check-in location data at the end of the configured retention period, defaulting to 72 hours after created_at.
6. WHEN a user submits "I need assistance", THE Frontend SHALL display a statement that SafePath does not dispatch emergency services together with the emergency contact guidance.
7. IF a check-in specifies a status outside criterion 1 or an incident_id that does not exist, THEN THE CheckIn_Service SHALL reject the check-in with HTTP status 400 or 404 respectively.
8. WHEN a user submits a check-in for an Incident in which the user has a previous check-in, THE CheckIn_Service SHALL display the newest check-in as the user's current status and retain the previous check-ins in history.

### Requirement 28: Abuse Prevention and Input Safety

**User Story:** As a moderator, I want protections against spam and misleading reports, so that community information stays useful.

#### Acceptance Criteria

1. THE Backend_API SHALL enforce per-user and per-session rate limits of 5 Hazard_Reports per 10 minutes, 20 messages per minute, 30 corroborations or disputes per 10 minutes, and 10 check-ins per 10 minutes.
2. IF a user exceeds a rate limit, THEN THE Backend_API SHALL reject the request with HTTP status 429 and a Retry-After value in seconds.
3. WHEN a new Hazard_Report matches an active report of the same category within a configured distance, defaulting to 50 meters, created within the previous 60 minutes, THE Report_Service SHALL link the new report to the existing report as a duplicate.
4. IF a request fails validation against its defined schema, THEN THE Backend_API SHALL reject the request with HTTP status 400 and an error identifying each invalid field.
5. THE Backend_API SHALL sanitize all user-provided text to remove HTML and script content before storage.
6. IF a user submits a second confirmation or dispute for the same Hazard_Report, or a confirmation of the user's own report, THEN THE Report_Service SHALL reject the request with HTTP status 409 or 403 respectively.
7. THE Backend_API SHALL record moderation actions, role changes, verification changes, and Official_Notice creation in an audit history with the timestamp, actor identifier, actor role, action, and target record identifier.
8. IF a user without the Authorized_Official role attempts to create an Official_Notice, THEN THE Backend_API SHALL reject the request with HTTP status 403.
9. IF a request body other than a photo upload exceeds 1 MB, THEN THE Backend_API SHALL reject the request with HTTP status 413.

### Requirement 29: AWS Environment Discovery and Infrastructure

**User Story:** As a developer, I want the AWS deployment to fit the sandbox's actual permissions, so that the prototype deploys without assuming admin access.

#### Acceptance Criteria

1. WHEN AWS deployment work begins, THE SafePath team SHALL, prior to provisioning any resource, inspect the AWS sandbox credentials, region, IAM permissions, service quotas, and restrictions and record the findings in docs/aws-deployment.md.
2. THE Infrastructure_Stack SHALL define all cloud resources using AWS CDK or an equivalent IaC tool.
3. THE Infrastructure_Stack SHALL deploy the Backend_API on AWS Lambda behind Amazon API Gateway, store records in Amazon DynamoDB, store Graph datasets in Amazon S3, use AppSync Events for real-time delivery, use Amazon Cognito for authentication, and send logs to Amazon CloudWatch.
4. THE Infrastructure_Stack SHALL host the Frontend on Amazon S3 with Amazon CloudFront or a documented permitted alternative.
5. IF a required AWS service or permission is unavailable, THEN THE SafePath team SHALL implement a labeled substitute and document the substitution in docs/aws-deployment.md.
6. THE Infrastructure_Stack SHALL tag all created resources with a SafePath project tag and SHALL modify or delete only resources carrying that tag.
7. THE SafePath system SHALL provide a local development environment that runs the Frontend and Backend_API without AWS deployment.
8. IF an Infrastructure_Stack deployment fails, THEN THE Infrastructure_Stack SHALL roll back the SafePath-tagged resources created by that deployment and leave resources without the SafePath project tag unchanged.

### Requirement 30: Cloud Data Model

**User Story:** As a backend developer, I want defined persistent record structures, so that reports and incidents persist consistently.

#### Acceptance Criteria

1. THE Backend_API SHALL persist Users records with user_id, display_name, role, created_at, and privacy_preferences.
2. THE Backend_API SHALL persist Incidents records with incident_id, incident_type, geographic_boundary, status, source, created_at, updated_at, and simulation_flag.
3. THE Backend_API SHALL persist HazardReports records with report_id, incident_id, reporter_id, category, coordinates, affected_edge_ids, description, verification_status, confirmations, created_at, expires_at, and updated_at.
4. THE Backend_API SHALL persist Messages records with message_id, incident_id, sender_id, message_type, content, approximate_location, and created_at.
5. THE Backend_API SHALL persist CheckIns records with checkin_id, incident_id, user_id, status, optional_location, created_at, and visibility.
6. THE Backend_API SHALL persist OfficialNotices records with notice_id, source_organization, source_url, incident_id, notice_type, geometry, published_at, retrieved_at, and verification_status.
7. THE SafePath system SHALL store the Graph as a versioned static dataset in S3 and SHALL store closure and hazard overlays separately from the Graph dataset.
8. THE Backend_API SHALL assign each persisted record an identifier unique within its record type at creation time.
9. IF a record write to the data store fails, THEN THE Backend_API SHALL return HTTP status 503, persist no partial record, and publish no Realtime_Channel event for the failed write.

### Requirement 31: Security and Privacy

**User Story:** As a user, I want my data and location protected, so that using SafePath does not expose me.

#### Acceptance Criteria

1. THE Infrastructure_Stack SHALL grant each component IAM permissions limited to the resources and actions that component uses.
2. THE Frontend bundle SHALL contain no secrets or long-lived credentials.
3. THE SafePath source code SHALL load credentials and secrets from secure environment configuration and SHALL contain no hardcoded credentials.
4. THE Backend_API SHALL perform authorization checks server-side for every protected operation.
5. THE SafePath system SHALL serve all client traffic over HTTPS using TLS 1.2 or higher and SHALL encrypt stored data at rest.
6. THE Backend_API SHALL return exact user locations only to the owning user and SHALL publish locations only for users who have opted in to location sharing.
7. IF a Guest or User attempts to modify another user's private record, THEN THE Backend_API SHALL reject the request with HTTP status 403.
8. THE Backend_API SHALL separate public response fields from private fields in all record serializers.
9. WHEN a user disables location sharing, THE Backend_API SHALL stop publishing that user's location and remove the user's location from public views within 5 seconds.

### Requirement 32: Cost Controls

**User Story:** As the sandbox owner, I want SafePath to stay low cost, so that the hackathon does not incur unexpected charges.

#### Acceptance Criteria

1. THE Infrastructure_Stack SHALL use serverless and on-demand services and SHALL include no always-on EC2 instances or provisioned large database instances.
2. THE Infrastructure_Stack SHALL configure DynamoDB tables in on-demand capacity mode.
3. WHERE AWS Budgets is permitted, THE Infrastructure_Stack SHALL configure a monthly budget for the SafePath project with an alert at 80 percent of a configured amount, defaulting to 25 USD.
4. THE Frontend SHALL receive updates through the Realtime_Channel and SHALL limit fallback polling to an interval of no less than 30 seconds.
5. THE Backend_API SHALL enforce API Gateway throttling limits of 50 requests per second steady-state and 100 requests burst, returning HTTP status 429 for throttled requests.
6. THE Infrastructure_Stack SHALL set the CloudWatch log retention period for SafePath log groups to 7 days.

### Requirement 33: Optional AI Assistance

**User Story:** As a user, I want plain-language summaries of verified notices and route flags, so that I understand complex information quickly.

#### Acceptance Criteria

1. WHERE Amazon Bedrock is available, THE AI_Assistant SHALL summarize verified Official_Notices, explain route hazard flags, suggest report categories, and explain the difference between confirmed and unverified information.
2. THE AI_Assistant SHALL generate output only from data supplied in the request context and SHALL cite the Source_Records of that data.
3. THE AI_Assistant SHALL leave Policy_Outcomes, Hard_Restrictions, routes, and Verification_Status values unchanged.
4. THE Frontend SHALL label all AI_Assistant output as AI-generated and non-authoritative.
5. IF Amazon Bedrock is unavailable or returns no response within 15 seconds, THEN THE SafePath system SHALL disable the affected AI feature, display that AI summaries are unavailable, and continue all routing and communication functions.
6. IF the AI_Assistant output references a shelter, closure, order, route, or hazard absent from the supplied context, THEN THE Backend_API SHALL discard the output and return the unsummarized source data.
7. IF the AI_Assistant output cites no Source_Record from the supplied context, THEN THE Backend_API SHALL discard the output and return the unsummarized source data.

### Requirement 34: Frontend Layout

**User Story:** As a user, I want a clear, organized interface, so that I can find routes, reports, and guidance quickly under stress.

#### Acceptance Criteria

1. THE Frontend SHALL display a header containing the SafePath logo, the city name "Chicago", a demo mode indicator, a connectivity status indicator, and a profile control.
2. THE Frontend SHALL display a left sidebar containing the active Incident, a disaster type selector, start and destination inputs, route alternatives, official information links, and an uncertainty summary.
3. THE Map_View SHALL render roads, buildings, transit stations, elevation and hazard overlays, routes, report markers, incident boundaries, and verified destinations as layers that the user can toggle individually, with each toggle taking effect within 1 second.
4. THE Frontend SHALL display a right sidebar containing messages, Hazard_Reports, Verification_Status, check-ins, and assistance requests.
5. THE Frontend SHALL display bottom controls for report hazard, request assistance, mark safe, view official guidance, and recalculate route.
6. THE Frontend SHALL provide the screens main map, disaster selection, route details, submit report, community communication, incident overview, safety check-in, data sources and limitations, and demo admin controls.
7. THE Frontend SHALL render a responsive layout at viewport widths from 320 to 2560 pixels in which every control listed in criteria 1, 2, 4, and 5 is reachable without horizontal scrolling.

### Requirement 35: Map Feature Inspection

**User Story:** As a user, I want to click map features to see details, so that I understand road conditions, reports, and route choices.

#### Acceptance Criteria

1. WHEN a user selects a road segment, THE Map_View SHALL display within 1 second the street name, Edge attributes, closure status, active Hazard_Reports, Source_Record, and last update time.
2. WHEN a user selects a Hazard_Report marker, THE Map_View SHALL display within 1 second the description, creation time, Verification_Status, corroboration count, and source category.
3. WHEN a user selects a route, THE Map_View SHALL display within 1 second the distance, estimated duration, restrictions applied, confidence limitations, and reasons the route was chosen over alternatives.
4. IF a selected feature attribute lacks data, THEN THE Map_View SHALL display Unknown for that attribute.

### Requirement 36: Visual Status Encoding and Accessibility

**User Story:** As a user with color vision deficiency, I want status conveyed with text and icons as well as color, so that I can interpret every status.

#### Acceptance Criteria

1. THE Frontend SHALL use a dark navy and charcoal base theme, cyan and blue for navigation routes, red for confirmed closures, orange for unverified reports, and green for verified resources.
2. THE Frontend SHALL convey every status with a text label or icon in addition to color.
3. THE Frontend SHALL render all text with a contrast ratio of at least 4.5:1 against its background.
4. THE Frontend SHALL make every interactive control reachable and operable by keyboard alone, with a visible focus indicator, and SHALL provide an accessible name for every interactive element.
5. THE Frontend SHALL visually distinguish Official, Community, and Simulated content using distinct text labels on each item.

### Requirement 37: Demo Scenario 1 — Loop Flooding

**User Story:** As a demo presenter, I want an end-to-end simulated Loop flooding scenario, so that I can show disaster-aware rerouting on real Chicago streets.

#### Acceptance Criteria

1. WHEN the presenter starts Scenario 1, THE SafePath system SHALL create a simulated flood Incident with Simulation_Flag set to true and an origin within 200 meters of the Chicago Riverwalk.
2. THE Scenario 1 data SHALL include a manually designated simulation destination labeled "Simulation destination — NOT an official shelter".
3. WHEN the flood Incident activates, THE Map_View SHALL highlight, using real street geometry and elevation data, Edges with infrastructure_level lower-level or tunnel_flag set and Edges whose average elevation is in the lowest 20 percent of Graph Edges with known elevation.
4. WHEN the flood Incident activates, THE Routing_Engine SHALL compute a shortest route and at least one disaster-adjusted alternative route.
5. WHEN the presenter adds a simulated verified closure, THE Routing_Engine SHALL recalculate routes and THE Frontend SHALL display the updated route with an explanation of the route change within 5 seconds.
6. THE Scenario 1 data SHALL omit water-depth values.

### Requirement 38: Demo Scenario 2 — Earthquake and High-Rise Debris

**User Story:** As a demo presenter, I want a simulated earthquake debris scenario, so that I can show exclusion of blocked roads near real buildings.

#### Acceptance Criteria

1. WHEN the presenter starts Scenario 2, THE SafePath system SHALL create a simulated earthquake Incident with Simulation_Flag set to true and an origin in the Loop.
2. THE Scenario 2 data SHALL place simulated debris zones adjacent to selected real OSM building footprints and label each zone as Simulated.
3. WHEN debris zones activate, THE Map_View SHALL mark intersecting road Edges as blocked within 5 seconds and THE Routing_Engine SHALL exclude those Edges.
4. WHEN the Routing_Engine returns an alternate route in Scenario 2, THE Backend_API SHALL include in the route explanation a reference to the simulated debris zone that caused each Edge exclusion.

### Requirement 39: Demo Scenario 3 — Blocked Stairwell

**User Story:** As a demo presenter, I want a multi-user blocked-stairwell scenario, so that I can show real-time reporting, corroboration, moderation, and indoor rerouting.

#### Acceptance Criteria

1. WHEN the presenter starts Scenario 3, THE SafePath system SHALL load the fictional Indoor_Graph and place at least three simulated user positions inside the building.
2. WHEN User A reports Stairwell A blocked, THE Frontend for User B SHALL display the report within 5 seconds without page reload.
3. WHEN User C corroborates the report and the corroboration threshold is reached, THE Report_Service SHALL set the Verification_Status to Corroborated and THE Frontend SHALL show the status as not officially verified.
4. WHEN the demo Moderator confirms the simulated closure, THE Building_Emergency_Module SHALL remove Stairwell A Edges from the Indoor_Graph and THE Frontend SHALL display updated indoor routes to affected users within 5 seconds.
5. WHEN a user submits an assistance request in Scenario 3, THE Messaging_Service SHALL publish the request to the Incident channel within 5 seconds.
6. THE Scenario 3 flow SHALL be demonstrable using two or more browser windows.

### Requirement 40: Demo Administration

**User Story:** As a demo presenter, I want admin controls for scenarios, so that I can run and reset the demo in about three minutes.

#### Acceptance Criteria

1. THE Demo_Admin_Console SHALL allow a Moderator to start, advance, and reset each demo scenario.
2. THE Demo_Admin_Console SHALL allow a Moderator to add and remove simulated closures and hazard polygons.
3. WHEN a scenario is reset, THE SafePath system SHALL, within 30 seconds, remove all records created by that scenario, restore the seeded state, and leave records of other scenarios unchanged.
4. THE seed_demo_incidents.py script SHALL create all demo Incidents with Simulation_Flag set to true.
5. THE SafePath documentation SHALL include a demo script that completes Scenarios 1 and 3 within 3 minutes.
6. IF a Guest or User attempts a Demo_Admin_Console action, THEN THE Backend_API SHALL reject the request with HTTP status 403.
7. WHEN a reset is requested for a scenario already in its seeded state, THE SafePath system SHALL leave the seeded state unchanged and report success.

### Requirement 41: Offline Support

**User Story:** As a user with intermittent connectivity, I want cached map data and queued reports, so that I can keep viewing information and submit reports later.

#### Acceptance Criteria

1. THE Offline_Manager SHALL cache through a service worker the app shell, map tiles and geographic data for the Chicago_Extraction_Area at zoom levels 12 through 16, and static official guidance.
2. WHILE the Frontend is offline, THE Frontend SHALL display an offline indicator in the header and the last successful sync time.
3. WHILE the Frontend is offline, THE Frontend SHALL label cached hazard and closure data as "Cached as of [timestamp] — may be outdated".
4. WHILE the Frontend is offline, THE Offline_Manager SHALL queue submitted reports locally and display each queued report as "Queued — not yet received by server".
5. WHEN connectivity is restored, THE Offline_Manager SHALL submit queued reports in creation order and update each report's display status upon server acknowledgment.
6. THE Offline_Manager SHALL attach a client-generated unique identifier to each queued report, and THE Report_Service SHALL treat a resubmission carrying an already-stored identifier as the same report without creating a duplicate.
7. IF the server rejects a queued report, THEN THE Offline_Manager SHALL display the report as "Rejected by server" with the returned error text and retain the report for the user to edit or discard.
8. IF the local queue holds 50 reports, THEN THE Offline_Manager SHALL reject additional report submissions and display a message stating that the offline queue is full.

### Requirement 42: Backend API Endpoints

**User Story:** As a frontend developer, I want a documented REST API, so that the client can access routing, reports, and communication features.

#### Acceptance Criteria

1. THE Backend_API SHALL provide GET /api/health, GET /api/regions, GET /api/regions/chicago/metadata, GET /api/graph/status, GET /api/incidents, POST /api/incidents, GET /api/incidents/{incidentId}, POST /api/routes/calculate, POST /api/reports, GET /api/reports, POST /api/reports/{reportId}/corroborate, POST /api/reports/{reportId}/dispute, POST /api/reports/{reportId}/moderate, GET /api/messages, POST /api/messages, POST /api/checkins, GET /api/checkins, and GET /api/sources.
2. THE Backend_API SHALL publish an OpenAPI specification describing all endpoints, request schemas, and response schemas.
3. IF an unauthenticated request is made to POST /api/incidents, POST /api/reports/{reportId}/moderate, or any write operation on reports, messages, or check-ins, THEN THE Backend_API SHALL reject the request with HTTP status 401.
4. IF a user without the Moderator or Authorized_Official role calls POST /api/incidents or POST /api/reports/{reportId}/moderate, THEN THE Backend_API SHALL reject the request with HTTP status 403.
5. WHEN GET /api/graph/status is called, THE Backend_API SHALL return the Graph version, build timestamp, Node count, Edge count, and active closure count.
6. WHEN GET /api/health is called, THE Backend_API SHALL return HTTP status 200 within 1 second with the service status and the loaded Graph version.
7. IF a request references an incidentId or reportId that does not exist, THEN THE Backend_API SHALL return HTTP status 404 with an error identifying the missing resource.

### Requirement 43: Route API Contract

**User Story:** As a frontend developer, I want a defined route request and response format, so that the client can request and display routes consistently.

#### Acceptance Criteria

1. THE Backend_API SHALL accept route requests at POST /api/routes/calculate containing incident_id, origin (latitude, longitude), destination (latitude, longitude), travel_mode, mobility_profile, and alternatives (integer from 1 to 3, defaulting to 1).
2. WHEN a route request is processed, THE Backend_API SHALL return a response containing route_id, geometry, distance_meters, estimated_duration_seconds, hazard_flags, excluded_edges_count, uncertainty_level, policy_outcome, sources, calculated_at, and status.
3. THE Backend_API SHALL set policy_outcome to exactly one of ROUTE_OPTIONS_AVAILABLE, SHELTER_IN_PLACE, FOLLOW_OFFICIAL_INSTRUCTIONS, NO_VERIFIED_ROUTE_AVAILABLE, or INSUFFICIENT_DATA.
4. WHEN policy_outcome is SHELTER_IN_PLACE, FOLLOW_OFFICIAL_INSTRUCTIONS, NO_VERIFIED_ROUTE_AVAILABLE, or INSUFFICIENT_DATA, THE Frontend SHALL display the outcome text and explanation in place of route navigation.
5. IF a route request contains invalid coordinates or an origin or destination outside the Chicago_Extraction_Area, THEN THE Backend_API SHALL return HTTP status 400 with a descriptive error.
6. IF a route request contains an incident_id that does not exist, THEN THE Backend_API SHALL return HTTP status 404 with a descriptive error.
7. IF a route request specifies a travel_mode or mobility_profile value that the Routing_Engine does not support, THEN THE Backend_API SHALL return HTTP status 400 with an error listing the supported values.
8. THE Backend_API SHALL use the processed Chicago Graph for all outdoor route calculations.

### Requirement 44: Automated Testing

**User Story:** As a developer, I want automated tests for routing, communication, integration, and security, so that core behavior is verified before demo.

#### Acceptance Criteria

1. THE test suite SHALL verify that the Routing_Engine returns the shortest path on a simple known graph.
2. THE test suite SHALL verify that removing an Edge changes the returned path when that Edge was on the shortest path.
3. THE test suite SHALL verify that directed one-way Edges are traversed only in the permitted direction.
4. THE test suite SHALL verify that a disconnected graph produces NO_VERIFIED_ROUTE_AVAILABLE.
5. THE test suite SHALL verify, for at least 100 randomly generated graphs and closure sets, that no returned route traverses an Edge under a Hard_Restriction.
6. THE test suite SHALL verify that active incident policies (SHELTER_IN_PLACE and official instructions) override lower-cost routes.
7. THE test suite SHALL verify that an Edge with Unknown elevation receives an uncertainty_penalty greater than zero.
8. THE test suite SHALL verify, for at least 100 randomly generated graphs, that returned alternative routes are valid connected paths from origin to destination and differ from each other by at least one Edge.
9. THE test suite SHALL verify that indoor and outdoor Nodes connect only through Level_Transition Edges.
10. THE test suite SHALL verify that Hazard_Reports are saved, that two clients receive the same update, that Unverified reports remain Unverified without action, that corroboration never produces Officially_Verified status, that unauthorized users cannot create Official_Notices, that expired reports transition to Expired, and that a disconnected client displays a stale-data warning.
11. THE test suite SHALL verify that the route API uses the processed Chicago Graph, that the Map_View displays Graph-derived route geometry, that a verified simulated closure triggers recalculation, that the database persists reports, that subscriptions deliver events, and that demo mode functions end-to-end.
12. THE test suite SHALL verify that Guests cannot modify other users' private records, that users cannot escalate their own role, that message input validation rejects invalid content, that protected operations require authorization, and that exact location sharing requires opt-in.
13. THE SafePath deliverables SHALL include recorded test results listing each test name, pass or fail result, and execution date.

### Requirement 45: Repository Structure, Documentation, and Deliverables

**User Story:** As a judge or future contributor, I want an organized repository with documentation, so that I can understand, run, and extend SafePath.

#### Acceptance Criteria

1. THE repository SHALL organize code under safepath/ with frontend/src/{components,pages,hooks,services,maps,styles}, backend/app/{api,models,services,routing,disasters,communication,auth}, data/{raw,processed,demo,sources}, scripts/, infrastructure/aws/, tests/, and docs/, with deviations documented in the README.
2. THE repository SHALL include docs/architecture.md, docs/data-sources.md, docs/routing-design.md, docs/safety-limitations.md, docs/aws-deployment.md, and docs/demo-script.md.
3. THE repository SHALL include a README.md describing setup, local development, deployment, data import, known limitations, and the deployed URL where deployment is permitted.
4. THE documentation SHALL include a data-flow diagram covering datasets, official plans, the Routing_Engine, AWS services, user devices, and community reporting.
5. THE documentation SHALL include a pitch script with a spoken duration of 3 minutes or less.
6. THE repository SHALL include the processed Chicago Graph or the scripts and instructions to regenerate the Graph.
7. THE docs/safety-limitations.md document SHALL list all fallbacks, unavailable sources, substitutions, and known limitations.

### Requirement 46: Definition of Done

**User Story:** As the team, I want explicit completion criteria, so that we know the prototype is demo-ready.

#### Acceptance Criteria

1. WHEN the deployed or local application is opened, THE Map_View SHALL display real Chicago street, building, and transit data.
2. WHEN a user selects an origin and destination, THE Routing_Engine SHALL return a route computed on the real Chicago Graph.
3. WHEN a user switches between flood, earthquake, tornado, fire, and building emergency Incidents, THE SafePath system SHALL apply the corresponding Disaster_Module and display disaster-specific route or shelter guidance.
4. WHEN a Hazard_Report is submitted in one browser, THE SafePath system SHALL persist the report in the cloud backend and display the report in a second browser within 5 seconds.
5. WHEN a Moderator verifies or resolves a simulated report, THE Routing_Engine SHALL update the routable network for route requests received more than 5 seconds after the action.
6. THE Frontend SHALL display the CBD_Plan as a referenced historical source dated 2007.
7. THE Frontend SHALL distinguish authoritative, community, and simulated information on every displayed item.
