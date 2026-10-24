# SafePath documentation

SafePath is an experimental prototype. It is not an official emergency service, it has no official approval or
endorsement, and every demo incident is simulated. Routes are algorithm-generated estimates. In an emergency call 911.

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | System context and deployment diagram, components, adapter ports (local and AWS), request flow, the data-flow diagram (datasets, official plans, routing engine, AWS, devices, community reporting), closure-to-recalculation sequence, report state machine |
| [routing-design.md](routing-design.md) | Graph model and real counts, hard restrictions versus penalties, cost function and weights, A*, alternatives, Rules_Engine precedence, disaster modules, Unknown handling, indoor graph, routing limitations |
| [data-sources.md](data-sources.md) | Every dataset with URL, license position, retrieval date, counts, limitations and fallbacks; 2007 CBD plan (Historical); official source review; CTA; 3DEP caveats; OSM attribution; PMTiles |
| [safety-limitations.md](safety-limitations.md) | What is simulated, community versus official, no 911 integration, policy behavior, substitutions, verified known gaps, AI guardrails, privacy, offline rules, demo-mode risk, photo EXIF |
| [api.md](api.md) | Endpoint table with auth and rate limits, routing request and response example, error envelope; contract in [openapi.json](openapi.json) |
| [aws-deployment.md](aws-deployment.md) | Sandbox discovery, adapters and Lambda packaging, CDK stacks, deploy and teardown scripts. Nothing has been deployed |
| [cbd-plan-extraction.md](cbd-plan-extraction.md) | Topic-by-topic extraction summary of the 2007 plan |
| [security-checks.md](security-checks.md) | Secret scanner and credentials policy |
| [test-results.md](test-results.md), [test-results.json](test-results.json) | Recorded test runs |

Also here: [demo-script.md](demo-script.md) (3-minute rehearsal for Scenarios 1 and 3, 60-second Scenario 2 variant,
pre-flight and fallback plan) and [pitch.md](pitch.md) (3-minute HackOHI/O script, elevator pitch, judge Q&A). The top-level
[README](../README.md) covers setup, local development, data import, deployment pointers and the definition-of-done table.

## Where the numbers come from

Counts in these documents come from `data/processed/summary_report.json`, `data/processed/fallbacks.json`,
`data/sources/catalog.json` and the graph manifest under `data/processed/graphs/`. When a pipeline script is re-run
they change; the documents carry the retrieval dates (2026-10-24) of the figures they quote.
