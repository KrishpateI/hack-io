import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { Incident, RouteResponse } from './api';
import {
  applyRestrictionEvent,
  closureFeatures,
  indoorExit,
  indoorStarts,
  mergeById,
  pickDefaultIncident,
  reportMarkers,
  routeLines,
  type ActiveRestriction,
} from './mapData';

const inc = (over: Partial<Incident>): Incident =>
  ({ incident_id: 'i', incident_type: 'flood', simulation_flag: false, status: 'active', source: 's', created_at: '', updated_at: '', ...over }) as Incident;

describe('pickDefaultIncident', () => {
  const list = [inc({ incident_id: 'a', incident_type: 'fire' }), inc({ incident_id: 'sim', simulation_flag: true })];
  it('prefers the simulated flood scenario in demo mode', () => {
    expect(pickDefaultIncident(list, true)?.incident_id).toBe('sim');
  });
  it('uses the first active incident otherwise, and none when nothing is active', () => {
    expect(pickDefaultIncident(list, false)?.incident_id).toBe('a');
    expect(pickDefaultIncident([inc({ status: 'closed' })], false)).toBeNull();
  });
});

describe('applyRestrictionEvent', () => {
  const added = { type: 'restriction_added', payload: { restriction_id: 'r1', edge_ids: ['e1'], kind: 'closure' } };
  it('adds then removes a restriction', () => {
    const on = applyRestrictionEvent({}, added);
    expect(on.r1.edge_ids).toEqual(['e1']);
    expect(applyRestrictionEvent(on, { type: 'restriction_removed', payload: { restriction_id: 'r1' } })).toEqual({});
  });
  it('ignores unrelated events', () => {
    const on = applyRestrictionEvent({}, added);
    expect(applyRestrictionEvent(on, { type: 'report_created', payload: { restriction_id: 'r1' } })).toBe(on);
  });
  it('property: add followed by remove always leaves no trace of the restriction', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1 }), fc.array(fc.string({ minLength: 1 }), { maxLength: 4 }), (id, edges) => {
        const on = applyRestrictionEvent({}, { type: 'restriction_added', payload: { restriction_id: id, edge_ids: edges } });
        return Object.keys(applyRestrictionEvent(on, { type: 'restriction_removed', payload: { restriction_id: id } })).length === 0;
      }),
      { numRuns: 50 },
    );
  });
});

describe('closureFeatures', () => {
  it('draws only edges with known geometry', () => {
    const r: Record<string, ActiveRestriction> = {
      r1: { restriction_id: 'r1', kind: 'closure', edge_ids: ['e1', 'e2'], simulated: true, report_id: null, source_record_id: null },
    };
    const fc = closureFeatures(r, { e1: [[0, 0], [1, 1]] });
    expect(fc.features).toHaveLength(1);
    expect(fc.features[0].properties).toMatchObject({ restriction_id: 'r1', edge_id: 'e1', simulated: true });
  });
});

describe('reportMarkers', () => {
  it('keeps positioned active reports and drops resolved, expired and unpositioned ones', () => {
    const rows = [
      { report_id: 'a', latitude: 41.88, longitude: -87.63, verification_status: 'Corroborated', category: 'flooding' },
      { report_id: 'b', latitude: 41.88, longitude: -87.63, verification_status: 'Resolved' },
      { report_id: 'c', verification_status: 'Unverified' },
      { report_id: 'd', latitude: 41.88, longitude: -87.63, verification_status: 'Officially_Verified' },
    ];
    const m = reportMarkers(rows);
    expect(m.map((x) => x.report_id)).toEqual(['a', 'd']);
    expect(m[1].confirmed_closure).toBe(true);
    expect(m[0].confirmed_closure).toBe(false);
  });
});

describe('mergeById', () => {
  it('lets live fields win over fetched ones', () => {
    const out = mergeById([{ report_id: 'a', verification_status: 'Unverified', x: 1 }], { a: { report_id: 'a', verification_status: 'Corroborated' } }, ['report_id']);
    expect(out).toEqual([{ report_id: 'a', verification_status: 'Corroborated', x: 1 }]);
  });
});

describe('routeLines', () => {
  const base = { label: 'algorithm-generated', calculated_at: 'x' };
  it('draws nothing for a shelter-in-place response', () => {
    expect(routeLines({ ...base, policy_outcome: 'SHELTER_IN_PLACE', routes: [{ route_id: 'r', geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] } }] } as unknown as RouteResponse)).toEqual([]);
  });
});

describe('indoor helpers', () => {
  const geo = {
    features: [
      { geometry: { type: 'Point', coordinates: [-87.63, 41.88] }, properties: { role: 'start_position', start_id: 's1', node_id: 'sim-indoor-L2-zone-1' } },
      { geometry: { type: 'Point', coordinates: [-87.631, 41.881] }, properties: { role: 'exit', exit_id: 'exit-1' } },
      { geometry: { type: 'Point', coordinates: [0, 0] }, properties: { role: 'start_position' } },
    ],
  };
  it('extracts start positions with node ids and the first exit', () => {
    expect(indoorStarts(geo)).toEqual([{ startId: 's1', nodeId: 'sim-indoor-L2-zone-1', lat: 41.88, lng: -87.63 }]);
    expect(indoorExit(geo)).toEqual({ lat: 41.881, lng: -87.631 });
    expect(indoorStarts(null)).toEqual([]);
  });
});
