# SafePath demo script (3 minutes)

SafePath is an experimental prototype. Everything in this demo is simulated. Say so in the first ten seconds and keep the
SIMULATED badge in the header visible.

This script covers Scenario 1 (flood, about 1:30) and Scenario 3 (blocked stairwell with two windows, about 1:30), plus a
60-second variant for Scenario 2 (earthquake debris). The click paths and the numbers below were checked against the
running stack on 2026-10-24 (Chromium driven by Playwright). Timings are tool timings, not a rehearsed human pace.
Anything not checked is marked **unchecked**.

## Pre-flight checklist (10 minutes before)

Run these in PowerShell from `safepath\`.

1. **Restart the backend for a clean state.** The local store is in memory, so a restart wipes every report, vote and
   closure. Wait for `/api/health` to answer (about 4 seconds), then load the page.
   ```powershell
   cd backend
   $env:SAFEPATH_PROFILE = "local"; $env:SAFEPATH_DEMO_MODE = "true"
   $env:SAFEPATH_ALIAS_SECRET = "dev-alias-secret-not-for-prod"; $env:SAFEPATH_TOKEN_SECRET = "dev-token-secret-not-for-prod"
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
   ```
2. **Start the frontend** in a second terminal: `cd frontend; npm run dev` (http://localhost:5173). For the self-hosted basemap,
   copy `data\processed\tiles\chicago.pmtiles` to `frontend\public\tiles\` and put `VITE_PMTILES_URL=/tiles/chicago.pmtiles`
   and `VITE_PMTILES_KIND=vector` in `frontend\.env.local` first (see README). Reload the page after the backend is up.
   If the page was loaded while the backend was down, the Demo console stays hidden. Reload.
3. **Check the header** shows **DEMO MODE** (orange) and **SIMULATED**. If DEMO MODE is missing, the backend was started
   without `SAFEPATH_DEMO_MODE=true`.
4. **Window 1 (presenter):** `http://localhost:5173/`. Confirm the map shows buildings and station dots. Open **Demo console**
   (orange link in the row under the header), press **Get a moderator demo session** and **Reset all**. Expect the message
   "All scenarios are already pristine" or a reset count.
5. **Window 2 (audience view):** open `http://localhost:5173/?demoUser=B` in a separate browser window (a new profile or
   private window, so its session is its own). The profile button reads **Profile: SIMULATED User B**. Click
   **Building emergency** in the left sidebar, then click the incident **sim-blocked-stairwell**. Leave it on screen.
   Do this **before** you press Start on the stairwell card (see the note under Scenario 3).
6. Zoom the browser to 110 to 125 percent. Close other tabs. Put the two windows side by side.
7. Have `docs/pitch.md` open for the spoken lines and `http://localhost:8000/api/health` in a spare tab.

## Scenario 1: Chicago Loop flooding (0:00 to 1:30)

| Time | Say | Do (exact clicks) | You should see |
|---|---|---|---|
| 0:00 | "SafePath is an experimental prototype. Every incident here is simulated. It is not official guidance; call 911 in an emergency." | Window 1, map screen. Point at the banner and the **SIMULATED** badge | Disclaimer banner, **DEMO MODE** and **SIMULATED** badges, Flood selected, incident `sim-flood-loop` |
| 0:10 | "This is the real downtown Chicago street graph: 61 thousand intersections, 158 thousand directed street segments, built from OpenStreetMap and city data." | Click **Map layers and legend** to show the toggles, then close it | Dark map with roads, building footprints, CTA stations, dashed extraction boundary, OpenStreetMap attribution |
| 0:25 | "Activate the flood incident." | Click **Demo console**, then **Get a moderator demo session**. In the card **SIMULATED: Chicago Loop flooding** click **Start**, then **Next step** | Message "Step applied; client action: open incident. Demo only, not real events." |
| 0:35 | "Highlight the low-lying segments. This is modeled from elevation. There are no water depths." | **Next step** | "client action: show layer". Say the highlight is a model, not a measurement. Whether the highlight layer is visible on the map: **unchecked** |
| 0:45 | "Now route from the Riverwalk to a simulated destination." | **Next step** | "client action: calculate routes". A **Route (SIMULATED)** box appears in the card: about 1.00 km, about 12 min, 0 restrictions considered, labeled algorithm-generated |
| 1:00 | "A verified closure appears on the best route." | **Next step** (adds the SIMULATED verified closure), then **Next step** again (recalculate) | The **After** column: 0.99 km, about 12 min, **1 restriction considered**. The explanation text names the new closure ("New closure ... is now avoided") |
| 1:15 | "The route changed because an official-style closure is a hard restriction. A community report that nobody confirmed would only add cost." | Click **Back to the map** | Dashboard back, still SIMULATED |

Notes:

- Steps take 0.1 to 0.6 seconds each in my runs. Pause about a second after **Start** before the first **Next step**.
- Honest limit: the change in this example is small (1.00 to 0.99 km). The proof is in "restrictions considered" and in the
  explanation, not in a dramatic detour. Do not call it a major reroute.
- The main map window did **not** visibly reroute in my run: I started its route from other coordinates, so the closure was not
  on that path. Showing the closure on the main-map route line is **unchecked**. If you want to show it, type the scenario
  start (41.8882, -87.6280) and destination (41.8827, -87.6233) in the Start and Destination boxes beforehand and run
  the scenario once to confirm.
- A plain route without the console: press **Use sample points (flood scenario)** in the left sidebar. That uses a start in
  the southwest (41.8689, -87.6486) and returns two options (about 2.7 km, 32 min) with the uncertainty summary, restrictions
  considered and sources. Use this if the console fails.

## Scenario 3: blocked stairwell, two windows (1:30 to 3:00)

Window 2 (User B) is already open on **sim-blocked-stairwell** from the pre-flight.

| Time | Say | Do (exact clicks) | You should see |
|---|---|---|---|
| 1:30 | "Now a fictional three-floor building. Same rules: a report is evidence, not a fact." | Window 1: Demo console, in **SIMULATED: Blocked stairwell** click **Start** | "Started SIMULATED: Blocked stairwell." Window 2 shows the indoor floor plan (L1, L2, L3) with Stairwell A, B, C and Exit 1, 2 |
| 1:40 | "User A reports Stairwell A blocked." | Scroll to **Blocked stairwell: actors (SIMULATED)**, click **A reports Stairwell A blocked** | Result "Unverified". Window 2: within about a second, **Community reports** shows "Inaccessible Stairwell", badge **Unverified** and **SIMULATED**; the floor plan marks Stairwell A as Unverified; the route steps now use Stairwell B and say Stairwell A is "penalized, not prohibited" |
| 1:55 | "User B sees it without reloading. Now B and C each corroborate." | Window 2: click **Corroborate** on the report card. Window 1: click **C corroborates the report** | Needs **two** people besides the reporter. After C: badge **Corroborated by community, not officially verified**. Say: "Corroborated is still not official." |
| 2:15 | "Only a moderator can confirm a closure. In this prototype that is the demo moderator." | Click **Moderator confirms SIMULATED closure** | Result "Officially_Verified (SIMULATED moderator verification)" |
| 2:25 | "Recompute for A, B and C." | Click **Calculate routes for A, B and C** | Three **Route for user A / B / C (SIMULATED)** boxes with Before and After and step lists. Example from my run: A, 0.24 km, about 3 min, restrictions considered 0 then 4, steps via Stairwell B to Exit 2, explanation names the new closures |
| 2:40 | "B asks for help. This goes to other app users only. It does not reach 911." | Click **B sends assistance request** | "Sent assistance request (SIMULATED message, not delivered to any real responder)." Show the **Messages & notices** tab in window 2 for the message (**unchecked** in the UI) |
| 2:50 | "Everything labeled: official, community, simulated. Questions?" | Point at the badges | |

Honest limits to say out loud or have ready:

- In this example the route for A did not change its path after the confirmation, because the earlier Unverified report already
  steered it to Stairwell B. What changes is the restriction count and the explanation. Say "penalized before, excluded now".
- The "B observes" button in the console showed "Waiting for the report event" in my runs although window 2 had the report.
  Use window 2, not that button, as your 5-second proof. Report-to-window latency measured about 50 ms locally over SSE.
- After the moderator confirms, window 2 still showed "Corroborated" 2.5 seconds later in one run (I did not diagnose it).
  The console panel is the source of truth. If window 2 lags, reload it and reselect the incident.
- **Order matters.** In my runs window 2 showed the report live when it was opened and had selected the incident **before**
  **Reset all** and **Start**. When it was opened after Start, it failed twice to show the new report. The cause is not
  diagnosed. If window 2 stays on "No community reports yet", close it, reopen `?demoUser=B`, reselect the incident.
- A single corroboration does not reach Corroborated. The scenario text in `data/demo/scenarios.json` suggests it does;
  the code requires two distinct non-reporters, which is why window 2 clicks **Corroborate** first.

## Scenario 2: earthquake debris, 60-second variant (**unchecked** end to end)

I did not run this scenario by hand. The steps come from the console and `data/demo/scenarios.json`.

| Time | Do | Expect |
|---|---|---|
| 0:00 | Say: "Earthquake. Simulated debris zones next to real building footprints. It says nothing about any real building." Demo console, card **SIMULATED: Earthquake / high-rise debris**, **Start** | Card lists 5 steps |
| 0:10 | **Next step** (activate), **Next step** (route before debris) | A **Route (SIMULATED)** box |
| 0:25 | **Next step** (activate debris zones), **Next step** (mark one road edge as a SIMULATED closure) | Messages "Step applied" |
| 0:40 | **Next step** (recalculate) | After column; the alternate path should cite the SIMULATED debris zone |
| 0:50 | Say: "Debris is a hard restriction here. Unknown building condition is not treated as clear." | |

## Fallback plan

| Problem | Do this |
|---|---|
| Demo console is missing or says "Demo status unavailable" | Backend was down at page load or demo mode is off. Check `/api/health`, restart the backend with `SAFEPATH_DEMO_MODE=true`, reload |
| A step fails or a 409 `conflict` appears | State is stale. Press **Reset scenario**, or restart the backend (the store is in memory; this is the reliable reset). If you restart, reload both windows |
| Map is blank or only a dark background | Reload. Confirm `frontend\public\tiles\chicago.pmtiles` exists and `.env.local` is set, then restart `npm run dev`. If WebGL is blocked, a "The map is unavailable on this device" message shows; routes, reports and guidance still work in the side panels. Continue with the panels |
| Window 2 shows nothing | Reopen `?demoUser=B`, select **Building emergency** and the incident. Narrate from window 1 |
| Realtime shows Offline or Disconnected | The header shows the connection state. Say so, keep going: the REST data still loads. Do not claim live updates |
| No network (venue Wi-Fi) | Everything runs on localhost. Without the PMTiles file the dev map tries public OpenStreetMap tiles and shows only the background. Use the PMTiles file |
| Total failure | Show the screenshots in `frontend/e2e/screenshots/` (`map-basemap-1440.png`, `map-route-1440.png`, `demo-console-flood-after.png`) and the pitch. Say clearly they are screenshots |

## What to say about limitations

- "These incidents are simulated, and so is the building. The routes are estimates from public data with known gaps."
- "Most street attributes are unknown in the data. Unknown costs extra and is never treated as clear."
- "Corroboration needs two other people and still is not official. Only a moderator, or a real official in a real deployment,
  can close an edge, and the moderator here is a demo role."
- "No 911 or dispatch integration. An assistance request reaches other app users only."
- "It is not deployed to AWS. I have no AWS access on this machine; the cloud code is tested with stand-ins."
- "The basemap is sparse: roads and water only, with buildings and stations drawn from our own layers."
