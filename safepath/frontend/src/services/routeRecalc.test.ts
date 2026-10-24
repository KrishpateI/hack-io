import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JsonObject, RouteResponse } from './api';
import {
  coalesceAccepts, coalesceFire, coalesceTrigger, eventAffectsRoute, explainChange, initialCoalesce,
  RECALC_FAILED_MESSAGE, RouteRecalcController, type RouteT,
} from './routeRecalc';

const mkRoute = (over: Partial<RouteT> = {}): RouteT =>
  ({
    calculated_at: '2024-01-01T00:00:00Z', confidence_limitations: [], distance_meters: 1000,
    edge_penalties: [{ edge_id: 'e1' }, { edge_id: 'e2' }], estimated_duration_seconds: 600,
    excluded_edges_count: 0, geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] },
    hazard_flags: [], label: 'algorithm-generated', policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
    reason_summary: '', restrictions_considered: [], route_id: 'r', route_type: 'disaster-adjusted',
    sources: [], status: 'ok', uncertainty_level: 'low', ...over,
  }) as unknown as RouteT;

const mkRes = (route: RouteT | null, outcome: RouteResponse['policy_outcome'] = 'ROUTE_OPTIONS_AVAILABLE', explanation = ''): RouteResponse =>
  ({ calculated_at: 'now', explanation, label: 'algorithm-generated', missing_data: [], notices: [],
    policy_outcome: outcome, route_count: route ? 1 : 0, routes: route ? [route] : [], sources: [] }) as unknown as RouteResponse;

const restr = (type: string, edge_ids: string[]) => ({ type, incident_id: 'inc', payload: { edge_ids, restriction_id: 'x1' } });

describe('coalescing reducer', () => {
  it('opens one window and issues one id', () => {
    const a = coalesceTrigger(initialCoalesce, 0);
    const b = coalesceTrigger(a.state, 200);
    expect(a.schedule).toBe(1000);
    expect(b.schedule).toBeNull();
    const f = coalesceFire(b.state);
    expect(f.issue).toBe(1);
    expect(coalesceFire(f.state).issue).toBeNull();
  });
  it('only the latest id is accepted', () => {
    const f1 = coalesceFire(coalesceTrigger(initialCoalesce, 0).state);
    const f2 = coalesceFire(coalesceTrigger(f1.state, 5000).state);
    expect(coalesceAccepts(f2.state, 1)).toBe(false);
    expect(coalesceAccepts(f2.state, 2)).toBe(true);
  });
});

describe('eventAffectsRoute', () => {
  const route = mkRoute();
  it('matches intersecting edges, ignores others and other incidents', () => {
    expect(eventAffectsRoute(restr('restriction_added', ['e2', 'z']), 'inc', route)).toBe(true);
    expect(eventAffectsRoute(restr('restriction_added', ['z']), 'inc', route)).toBe(false);
    expect(eventAffectsRoute({ ...restr('restriction_added', ['e1']), incident_id: 'other' }, 'inc', route)).toBe(false);
    expect(eventAffectsRoute(restr('restriction_removed', ['z']), 'inc', route)).toBe(true);
  });
  it('reacts to Officially_Verified status transitions only', () => {
    const ev = (p: Record<string, unknown>) => ({ type: 'report_status_changed', incident_id: 'inc', payload: p });
    expect(eventAffectsRoute(ev({ verification_status: 'Officially_Verified' }), 'inc', route)).toBe(true);
    expect(eventAffectsRoute(ev({ verification_status: 'Expired', previous_status: 'officially_verified' }), 'inc', route)).toBe(true);
    expect(eventAffectsRoute(ev({ verification_status: 'Expired', previous_status: 'Unverified' }), 'inc', route)).toBe(false);
  });
});

describe('explainChange', () => {
  it('names closure, source, simulated flag and deltas', () => {
    const next = mkRoute({
      distance_meters: 1800, estimated_duration_seconds: 900, reason_summary: 'Avoids closed bridge.',
      restrictions_considered: [{ id: 'c1', kind: 'closure', simulated: true, source_record_id: 'src9' }],
    });
    const text = explainChange(mkRoute(), next);
    expect(text).toContain('closure (c1, source src9) [simulated]');
    expect(text).toContain('+800 m');
    expect(text).toContain('+5 min');
    expect(text).toContain('Avoids closed bridge.');
  });
});

describe('RouteRecalcController', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const setup = (calculate: (b: JsonObject, s?: AbortSignal) => Promise<RouteResponse>) => {
    const c = new RouteRecalcController({ calculate });
    c.setActive('inc', { a: 1 }, mkRes(mkRoute()));
    return c;
  };

  it('coalesces many events into one request and explains the change', async () => {
    const calc = vi.fn().mockResolvedValue(mkRes(mkRoute({ distance_meters: 1500, restrictions_considered: [{ id: 'c1', kind: 'closure', simulated: false }] })));
    const c = setup(calc);
    c.handleEvent(restr('restriction_added', ['e1']));
    await vi.advanceTimersByTimeAsync(400);
    c.handleEvent(restr('restriction_added', ['e2']));
    c.handleEvent(restr('restriction_added', ['zzz'])); // unrelated, ignored
    expect(calc).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(700);
    expect(calc).toHaveBeenCalledTimes(1);
    const s = c.getSnapshot();
    expect(s.route?.distance_meters).toBe(1500);
    expect(s.previousRoute?.distance_meters).toBe(1000);
    expect(s.change).toContain('closure (c1)');
    expect(s.updating).toBe(false);
  });

  it('keeps old route marked updating while in flight', async () => {
    const calc = vi.fn().mockReturnValue(new Promise(() => {}));
    const c = setup(calc);
    c.handleEvent(restr('restriction_added', ['e1']));
    await vi.advanceTimersByTimeAsync(1000);
    const s = c.getSnapshot();
    expect(s.updating).toBe(true);
    expect(s.route?.distance_meters).toBe(1000);
  });

  it('ignores a stale response and aborts the in-flight request', async () => {
    const resolvers: ((r: RouteResponse) => void)[] = [];
    const signals: AbortSignal[] = [];
    const calc = vi.fn((_b: JsonObject, signal?: AbortSignal) => {
      signals.push(signal as AbortSignal);
      return new Promise<RouteResponse>((res) => resolvers.push(res));
    });
    const c = setup(calc);
    c.handleEvent(restr('restriction_added', ['e1']));
    await vi.advanceTimersByTimeAsync(1000);
    c.handleEvent(restr('restriction_added', ['e1']));
    await vi.advanceTimersByTimeAsync(1000);
    expect(calc).toHaveBeenCalledTimes(2);
    expect(signals[0].aborted).toBe(true);
    resolvers[1](mkRes(mkRoute({ distance_meters: 2000 })));
    await vi.advanceTimersByTimeAsync(0);
    resolvers[0](mkRes(mkRoute({ distance_meters: 9999 })));
    await vi.advanceTimersByTimeAsync(0);
    expect(c.getSnapshot().route?.distance_meters).toBe(2000);
  });

  it('shows failure message and keeps previous route', async () => {
    const c = setup(vi.fn().mockRejectedValue(new Error('boom')));
    c.handleEvent(restr('restriction_added', ['e1']));
    await vi.advanceTimersByTimeAsync(1000);
    const s = c.getSnapshot();
    expect(s.status).toBe('failed');
    expect(s.message).toBe(RECALC_FAILED_MESSAGE);
    expect(s.route?.distance_meters).toBe(1000);
  });

  it('clears route geometry on policy outcome flip', async () => {
    const c = setup(vi.fn().mockResolvedValue(mkRes(null, 'SHELTER_IN_PLACE', 'Shelter in place per official notice.')));
    c.handleEvent(restr('restriction_added', ['e1']));
    await vi.advanceTimersByTimeAsync(1000);
    const s = c.getSnapshot();
    expect(s.route).toBeNull();
    expect(s.status).toBe('no_route');
    expect(s.message).toContain('Route no longer valid');
    expect(s.change).toContain('Shelter in place per official notice.');
  });

  it('exposes the realtime stale flag', () => {
    const c = setup(vi.fn());
    c.setRealtimeStale(true);
    expect(c.getSnapshot().stale).toBe(true);
  });
});
