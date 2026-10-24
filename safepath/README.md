# SafePath

Evacuation navigation for Chicago: hard-restriction-aware routing, incident policy, and community reports.

## Layout

```
safepath/
  frontend/src/{components,pages,hooks,services,maps,styles}
  backend/app/{api,models,services,routing,disasters,communication,auth,adapters}
  data/{raw,processed,demo,sources}
  scripts/  infrastructure/aws/  tests/  docs/
```

### Deviations from Requirement 45.1

- `backend/app/adapters/` is added for the four ports (Repository, RealtimePublisher, AuthProvider, BlobStore) and their local/AWS implementations.
- `backend/app/api/routers/` holds one module per router; every module exposing `router` is auto-registered, so `main.py` is not edited by later tasks.
- `tests/` is split into `backend/`, `frontend/` and `integration/`.
- `data/raw` is git-ignored. Files over 50 MB must not be committed; run `python scripts/check_large_files.py`.

## Local development (PowerShell, Python 3.12+)

```powershell
cd safepath\backend
py -3.12 -m venv .venv          # or: py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

$env:SAFEPATH_PROFILE = "local"  # local | aws
uvicorn app.main:app --reload --port 8000
# http://localhost:8000/api/health

pytest                                   # ci profile (100 examples)
pytest --hypothesis-profile=demo-check   # 25 examples
```

## Download Chicago source data

```powershell
cd safepath
backend\.venv\Scripts\python.exe scripts\download_chicago_data.py            # all datasets
backend\.venv\Scripts\python.exe scripts\download_chicago_data.py --only cta_gtfs
```

Downloads the Chicago Data Portal street centerlines (`6imu-meau` is the portal map view; its data is served by parent dataset `pr57-gg9e`), OSM streets, walkways/pedestrian paths and building footprints (Overpass API, same highway filters as OSMnx) and the CTA GTFS feed for the Chicago_Extraction_Area. Raw files go to the git-ignored `data/raw/<dataset>/`. Each fetch uses 3 attempts x 60 s, then falls back to the previous raw copy, else the dataset is recorded as missing. Outputs: `data/processed/summary_report.json`, `source_records.json`, `cta_gtfs/data.json` (CTA stations with routes) and `fallbacks.json` (every fallback and limitation, input for `docs/safety-limitations.md`).

Optional OSMnx/GeoPandas stack for later processing steps: `pip install -e ".[pipeline]"`.

## Profiles

`SAFEPATH_PROFILE=local|aws` selects the adapter implementations. `GET /api/health` reports the active profile.

## AWS sandbox discovery (read-only)

```powershell
cd safepath
backend\.venv\Scripts\python.exe -m pip install -e "backend[aws]"   # optional boto3
backend\.venv\Scripts\python.exe scripts\aws_discover.py [--region us-east-2]
```

Only list/describe calls are made. Results go to the "Sandbox discovery" section of `docs/aws-deployment.md` and to the gate file `infrastructure/aws/discovery.json` (CDK context flags, substitution matrix, `deployable`). Without boto3 or credentials it records `status: unavailable` with the steps to fix it.

## Deployment, data import, known limitations

To be completed in later tasks (see `docs/`).
