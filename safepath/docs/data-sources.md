# Data sources

## Street segments: source precedence (`scripts/process_streets.py`)

Inputs: Chicago Data Portal (CDP) street centerlines, OSM streets, OSM pedestrian paths (all from
`scripts/download_chicago_data.py`). Output: `data/processed/streets/segments.jsonl.gz` (git-ignored)
and a `street_processing` section in `data/processed/summary_report.json`.

Reproduce:

```powershell
backend\.venv\Scripts\python.exe scripts\download_chicago_data.py
backend\.venv\Scripts\python.exe scripts\process_streets.py
```

| Attribute | Retained source |
| --- | --- |
| Geometry, street name | CDP when a CDP segment exists, else OSM |
| Road class | OSM `highway` when matched or OSM-only; CDP-only keeps `cdp_class_<code>` |
| One-way | OSM; CDP `dir_travel` only when OSM states nothing |
| Pedestrian access, bridge, tunnel, layer, wheelchair | OSM only (CDP has none of these) |
| River crossing | CDP river lines (class `RIV`); otherwise `Unknown` |

Matching: a CDP segment is paired with the OSM way that overlaps it most (12 m buffer, same normalized
street name needs 60 % overlap, a different or missing name needs 90 %). Unmatched segments from
either source are kept with that single source. Name or one-way disagreements keep the source above
and record a conflict on the segment (`conflicts`, `attribute_sources`) and a warning in the summary.

Rules: absent attributes are the string `Unknown` and are never defaulted. `infrastructure_level` is
only assigned where a source says so (bridge, tunnel, viaduct as elevated, negative layer as
lower-level, explicit layer 0 or explicit `bridge=no`/`tunnel=no` as surface). Pedestrian paths are
walking segments; `highway=steps` is wheelchair inaccessible, other paths stay `Unknown` unless
tagged. Streets list `walking` only when OSM states foot access.

Known limits: CDP `tiered` and z-level fields are not used to infer levels; CDP names with suffixes
such as `OB`/`IB` can raise name conflicts against OSM; wheelchair data is sparse in OSM.
