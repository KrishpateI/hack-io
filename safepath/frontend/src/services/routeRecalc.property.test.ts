// Feature: safepath-evacuation-navigation, Property 39: Route recalculation requests are coalesced
//
// For any sequence of restriction events (arbitrary arrival times) affecting the active route,
// events inside one coalescing window produce exactly one recalculation request that reflects all
// of them, issued no later than 5 s after the first event of the window.
// **Validates: Requirements 16.4, 16.7**
import { afterEach, describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import type { JsonObject, RouteResponse } from './api';
import { RECALC_WINDOW_MS, RouteRecalcController, type RouteT } from './routeRecalc';

const REQ_LIMIT_MS = 5000;

const route = {
  calculated_at: '2024-01-01T00:00:00Z', confidence_limitations: [], distance_meters: 1000,
  edge_penalties: [{ edge_id: 'e1' }, { edge_id: 'e2' }], estimated_duration_seconds: 600,
  excluded_edges_count: 0, geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
  hazard_flags: [], label: 'algorithm-generated', policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
  reason_summary: '', restrictions_considered: [], route_id: 'r', route_type: 'disaster-adjusted',
  sources: [], status: 'ok', uncertainty_level: 'low',
} as unknown as RouteT;

const response = (): RouteResponse =>
  ({ calculated_at: 'now', explanation: '', label: 'algorithm-generated', missing_data: [], notices: [],
    policy_outcome: 'ROUTE_OPTIONS_AVAILABLE', route_count: 1, routes: [route], sources: [] }) as unknown as RouteResponse;

// Restriction events that all affect the active route (edges e1/e2), in the shapes the backend emits.
const affectingEvent = fc.oneof(
  fc.constant({ type: 'restriction_added', payload: { edge_ids: ['e1'], restriction_id: 'x' } }),
  fc.constant({ type: 'restriction_added', payload: { edge_ids: ['zz', 'e2'], restriction_id: 'y' } }),
  fc.constant({ type: 'restriction_changed', payload: { edge_ids: [], restriction_id: 'z' } }),
  fc.constant({ type: 'restriction_removed', payload: { edge_ids: ['q'], restriction_id: 'w' } }),
);

/** Reference model: a window opens at the first event outside any open window and closes at open + w. */
function modelWindows(times: number[], w: number): { open: number; count: number }[] {
  const out: { open: number; count: number }[] = [];
  let open: number | null = null;
  times.forEach((t, i) => {
    if (open === null || t >= open + w) {
      open = t;
      out.push({ open: t, count: 0 });
    }
    out[out.length - 1].count = i + 1; // events seen so far, all reflected by this window's request
  });
  return out;
}

describe('Property 39: Route recalculation requests are coalesced', () => {
  afterEach(() => vi.useRealTimers());

  it('one request per window, issued within 5 s of its first event, reflecting every event in it', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.array(fc.tuple(fc.integer({ min: 0, max: 7000 }), affectingEvent), { minLength: 1, maxLength: 25 }),
        fc.oneof(fc.constant(RECALC_WINDOW_MS), fc.constant(REQ_LIMIT_MS), fc.integer({ min: 1, max: REQ_LIMIT_MS })),
        async (steps, windowMs) => {
          vi.useFakeTimers();
          vi.setSystemTime(0);
          try {
            let delivered = 0;
            const calls: { at: number; seen: number; body: JsonObject }[] = [];
            const calc = vi.fn(async (body: JsonObject) => {
              calls.push({ at: Date.now(), seen: delivered, body });
              return response();
            });
            const c = new RouteRecalcController({ calculate: calc, windowMs });
            const body = { origin: 'a', destination: 'b' };
            c.setActive('inc', body, response());

            const times: number[] = [];
            for (const [gap, ev] of steps) {
              await vi.advanceTimersByTimeAsync(gap);
              times.push(Date.now());
              c.handleEvent({ ...ev, incident_id: 'inc' });
              delivered += 1;
            }
            await vi.advanceTimersByTimeAsync(windowMs + 1);

            const windows = modelWindows(times, windowMs);
            // Exactly one request per coalescing window (Req 16.7).
            expect(calls.length).toBe(windows.length);
            windows.forEach((win, k) => {
              const call = calls[k];
              // Issued no later than 5 s after the window's first event (Req 16.4).
              expect(call.at - win.open).toBeLessThanOrEqual(REQ_LIMIT_MS);
              expect(call.at).toBeGreaterThanOrEqual(win.open);
              // Request is sent after every event of its window arrived, so it reflects all of them.
              expect(call.seen).toBe(win.count);
              expect(call.body).toEqual(body);
            });
            c.stop();
          } finally {
            vi.useRealTimers();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('the production window is within the 5 s requirement', () => {
    expect(RECALC_WINDOW_MS).toBeGreaterThan(0);
    expect(RECALC_WINDOW_MS).toBeLessThanOrEqual(REQ_LIMIT_MS);
  });
});
