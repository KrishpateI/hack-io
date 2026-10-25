import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end demo tests (task 13.10). Playwright starts both servers itself:
 *  - the FastAPI backend from backend/.venv on the local profile, demo mode on, scenarios auto-seeded,
 *    routing on the SIMULATED fixture graph (tests/fixtures/graph) so runs are fast and deterministic;
 *  - the Vite dev server, which proxies /api, /ws and /sse to the backend on port 8000.
 * The local store is in memory, so every run starts from a clean seeded state. Existing servers on
 * 8000 / 5173 are reused only when PW_REUSE_SERVERS=1 (their state and graph are then unknown).
 */
const here = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(here, '../backend');
const python = path.join(backendDir, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const reuse = process.env.PW_REUSE_SERVERS === '1';

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  outputDir: './e2e/.results',
  // Scenario state lives in one backend process: run the specs one at a time.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: backendDir,
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: reuse,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: {
        SAFEPATH_PROFILE: 'local',
        SAFEPATH_DEMO_MODE: 'true',
        SAFEPATH_DEMO_AUTOSEED: 'true',
        SAFEPATH_GRAPH_ROOT: path.resolve(here, '../tests/fixtures/graph'),
        // Local development secrets only (same values as the README / demo script).
        SAFEPATH_ALIAS_SECRET: 'dev-alias-secret-not-for-prod',
        SAFEPATH_TOKEN_SECRET: 'dev-token-secret-not-for-prod',
        SAFEPATH_AI_ENABLED: 'false',
      },
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      cwd: here,
      url: 'http://localhost:5173',
      reuseExistingServer: reuse,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
  ],
});
