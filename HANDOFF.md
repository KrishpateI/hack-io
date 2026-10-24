# SafePath handoff

Status as of the last session. Spec: `.kiro/specs/safepath-evacuation-navigation/` (requirements.md, design.md, tasks.md). Code: `safepath/`.

## Where things stand
- 108 of 141 tasks completed. All core implementation tasks are done. Remaining work is mostly optional test tasks, checkpoints and a final verification.
- Finished by a sub-agent but NOT yet marked completed in tasks.md: 4.6 (no new tests needed, existing ones cover it), 8.7, 8.8, 8.10. Verify the test files exist and pass, then mark them completed.
- 8.9 (request validation property test, design Property 23) was cancelled and needs a re-run.
- Not started: 8.11, 8.12, 9.11-9.20, 11.5-11.8, 12.7, 12.8, 13.9, 13.10, 14.2, 14.5.
- Checkpoint/parent tasks to mark after a full green run: 2, 5, 7, 9, 10, 15.
- Final pass: run `safepath\scripts\run_all_tests.ps1`, fix the lint error in `frontend/src/services/offline.test.ts` (unused `_i`), refresh `docs/test-results.md` and README test numbers, run `scripts\scan_secrets.py` and `scripts\check_large_files.py`.
- If `docs/openapi.json` is stale: run `backend\.venv\Scripts\python.exe scripts\export_openapi.py`, then `cd frontend; npm run gen:api`, so `tests/backend/test_openapi.py` passes.

## Setup on a new machine
- Open the `hack-io` folder (repo root) in Kiro so the spec is found.
- Backend: `cd safepath\backend; python -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt`.
- Env vars: `SAFEPATH_PROFILE=local`, `SAFEPATH_DEMO_MODE=true`, `SAFEPATH_ALIAS_SECRET`, `SAFEPATH_TOKEN_SECRET` (pick local values, never commit them).
- Frontend: `cd safepath\frontend; npm ci` (if certs fail: `$env:NODE_OPTIONS='--use-system-ca'`).
- Tests: from `safepath/backend`, `.venv\Scripts\python.exe -m pytest -q` (testpaths are tests/backend and tests/pipeline). Frontend tests sit next to source in `frontend/src`.
- The real Chicago graph (data/processed/graphs/..., tiles/chicago.pmtiles) may be gitignored; regenerate with the scripts in `safepath/scripts` if missing.

## How to continue with the agent
Tell Kiro: "Continue running all remaining tasks in tasks.md." Task ids in the task tools are the full heading text, e.g. "8.9 Write property test for request validation". Up to 5 tasks can run in parallel; give each its own test file.

## Rules to keep
- Never deploy to AWS without the user's explicit go-ahead. Never ask for or paste credentials in chat.
- Everything simulated is labeled SIMULATED. Never claim routes are safe or official.
- Required disclaimer: "SafePath is an experimental prototype. Simulated scenarios and community reports are not official emergency guidance. Follow local authorities and call 911 for immediate emergencies."
- The user commits; the agent does not.

## Known gaps
- Nothing has been deployed or tested on AWS.
- API Gateway WebSocket fallback expects `?incident_id=`, but the frontend uses `/ws/incidents/<id>`.
- Messages and check-ins lack server-side idempotency.
- Hazard overlays are not served over REST.
- Map basemap style is sparse (roads and water only).
- Demo window-2 timing issues are undiagnosed.
- The graph needs about 1 GB RAM (Lambda 3008 MB planned).
- `docs/test-results.md` is stale.
