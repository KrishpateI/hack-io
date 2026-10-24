import { describe, expect, it } from 'vitest';
import { demoBuilding, ROUTE_EDGES } from './fixture';
import {
  STATUS_TEXT,
  avoidanceNotes,
  buildRoute,
  deriveStatuses,
  featureIdFromEdge,
  parseBuilding,
  statusOf,
} from './indoorModel';

const rep = (over: Record<string, unknown>) => ({
  report_id: 'r1',
  indoor_feature_id: 'stair-A',
  category: 'inaccessible stairwell',
  verification_status: 'Unverified',
  created_at: '2025-01-01T00:00:00Z',
  ...over,
});

describe('deriveStatuses', () => {
  it('has no entry (No reports) without reports', () => {
    expect(statusOf(deriveStatuses([]), 'stair-A').kind).toBe('no_reports');
    expect(STATUS_TEXT.no_reports).toBe('No reports');
  });
  it('maps verification statuses to the four conditions', () => {
    const s = deriveStatuses([
      rep({}),
      rep({ report_id: 'r2', indoor_feature_id: 'stair-B', verification_status: 'Corroborated' }),
      rep({ report_id: 'r3', indoor_feature_id: 'exit-1', verification_status: 'Officially_Verified' }),
    ]);
    expect(s['stair-A'].kind).toBe('reported_unverified');
    expect(s['stair-B'].kind).toBe('reported_corroborated');
    expect(s['exit-1'].kind).toBe('closure_confirmed');
  });
  it('ignores resolved, expired and disputed reports and counts live ones', () => {
    const s = deriveStatuses([
      rep({ verification_status: 'Resolved' }),
      rep({ report_id: 'r2', verification_status: 'Disputed' }),
      rep({ report_id: 'r3' }),
      rep({ report_id: 'r4', created_at: '2025-01-02T00:00:00Z', verification_status: 'Corroborated' }),
    ]);
    expect(s['stair-A'].reportCount).toBe(2);
    expect(s['stair-A'].kind).toBe('reported_corroborated');
    expect(s['stair-A'].latestReport?.id).toBe('r4');
  });
  it('confirms a closure from a restriction via its indoor edge ids', () => {
    const s = deriveStatuses([], [{ kind: 'stairwell_closure', edge_ids: ['sim-indoor-t-stair-C-L1-L2'] }]);
    expect(s['stair-C'].kind).toBe('closure_confirmed');
  });
});

describe('route mapping', () => {
  const b = parseBuilding(demoBuilding())!;
  it('parses the building', () => {
    expect(b.floors.map((f) => f.level)).toEqual(['L1', 'L2', 'L3']);
    expect(b.stairwells.map((x) => x.featureId).sort()).toEqual(['stair-A', 'stair-A', 'stair-A', 'stair-B', 'stair-B', 'stair-B', 'stair-C', 'stair-C', 'stair-C']);
    expect(b.exits).toHaveLength(2);
    expect(b.width).toBeGreaterThan(70);
  });
  it('extracts feature ids from edge ids', () => {
    expect(featureIdFromEdge('sim-indoor-t-stair-A-L3-L2')).toBe('stair-A');
    expect(featureIdFromEdge('sim-indoor-t-exit-1-out')).toBe('exit-1');
    expect(featureIdFromEdge('osm-123')).toBeNull();
  });
  it('builds ordered steps and floor legs', () => {
    const r = buildRoute(b, ROUTE_EDGES)!;
    expect(r.steps[0]).toMatch(/^Floor 3 corridor W/);
    expect(r.steps).toContain('Stairwell B down to Floor 2');
    expect(r.steps).toContain('Stairwell B down to Floor 1');
    expect(r.steps[r.steps.length - 1]).toBe('Exit 1');
    expect(r.legs.some((l) => l.level === 'L3')).toBe(true);
    expect(r.fullyPlaced).toBe(true);
  });
  it('returns null for outdoor-only edges', () => {
    expect(buildRoute(b, ['osm-1', 'osm-2'])).toBeNull();
  });
  it('names removed and avoided stairwells', () => {
    const s = deriveStatuses(
      [rep({ indoor_feature_id: 'stair-C' })],
      [{ kind: 'stairwell_closure', edge_ids: ['sim-indoor-t-stair-A-L1-L2'] }],
    );
    const notes = avoidanceNotes(b, s, ['stair-B']);
    expect(notes).toContain('Stairwell A removed from the route: confirmed closure (SIMULATED)');
    expect(notes.some((n) => n.startsWith('Stairwell C avoided: reported blocked (Unverified)'))).toBe(true);
  });
});
