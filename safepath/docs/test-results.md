# SafePath test results

Generated: 2026-10-24T17:30:37-04:00

**Totals:** 1244 passed, 1 failed, 0 skipped (237.7 s)

## Results

| Component | Step | Command | Status | Passed | Failed | Skipped | Duration (s) | Timestamp |
|---|---|---|---|---|---|---|---|---|
| Backend+Pipeline | pytest | `backend\.venv\Scripts\python.exe -m pytest -q --junitxml=<tmp>\pytest.xml` | PASSED | 679 | 0 | 0 | 154.2 | 2026-10-24T17:26:39 |
| Frontend | lint | `npm run lint` | FAILED | 0 | 1 | 0 | 6.7 | 2026-10-24T17:29:13 |
| Frontend | build | `npm run build` | PASSED | 1 | 0 | 0 | 10.7 | 2026-10-24T17:29:20 |
| Frontend | vitest | `npm test -- --reporter=default --reporter=junit --outputFile.junit=<tmp>\vitest.xml` | PASSED | 520 | 0 | 0 | 16.5 | 2026-10-24T17:29:30 |
| Infra | jest | `npm test -- --json --outputFile=<tmp>\jest.json` | PASSED | 43 | 0 | 0 | 20.2 | 2026-10-24T17:29:47 |
| Infra | cdk synth | `npx cdk synth -q` | PASSED | 1 | 0 | 0 | 29.4 | 2026-10-24T17:30:07 |

## Failing tests

### Frontend / lint
- `lint`

```text
> safepath-frontend@0.1.0 lint
> eslint .


C:\Users\kp200\hack-io\safepath\frontend\src\services\offline.test.ts
  154:33  error  '_i' is defined but never used  @typescript-eslint/no-unused-vars

âœ– 1 problem (1 error, 0 warnings)
```


## Property-based test settings

- Hypothesis profile: ci (100 examples per property; tests/backend/conftest.py)
- fast-check numRuns: set per property in frontend tests: 25 to 100 (see frontend/src/**/*.test.ts[x])

## Tool versions

- powershell: 5.1.26100.9711
- os: Microsoft Windows NT 10.0.26200.0
- python: Python 3.14.7
- node: v24.22.1
- npm: 11.19.1
- pytest: 9.1.1
- hypothesis: 6.170.9
- vitest: 5.0.5
- fast-check: 4.10.2
- aws-cdk: 2.1146.1
- jest: 29.7.0
