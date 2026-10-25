/**
 * map-legend-layers-fix, task 3.5: overlay snapshot + live restriction events.
 *
 * Property 2: snapshot plus live events equals current server state.
 * Property 4: incident switch isolation.
 */
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { INITIAL_OVERLAY_STATE, overlayReducer, selectMerged, type OverlayAction } from '../hooks/useMapOverlays';
import {
  EMPTY_OVERLAYS,
  mergeRestrictions,
  mergedRestrictionIds,
  overlayLayerInputs,
  type OverlayEventLike,
  type OverlaySnapshotLike,
} from './mapData';

type Kind = 'edge' | 'area';
const IDS = ['r1', 'r2', 'r3', 'r4'] as const;

const fcOf = (features: unknown[]) => ({ type: 'FeatureCollection' as const, features });

function lineFeature(incidentId: string, id: string) {
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: [[-87.63, 41.88], [-87.629, 41.881]] },
    properties: { incident_id: incidentId, restriction_id: id, edge_id: `e-${id}`, kind: 'closure', simulated: true },
  };
}
function areaFeature(incidentId: string, id: string) {
  const ring = [[-87.63, 41.88], [-87.629, 41.88], [-87.629, 41.881], [-87.63, 41.88]];
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [ring] },
    properties: { incident_id: incidentId, restriction_id: id, zone_id: id, simulated: true, applied_to_routing: true },
  };
}

/** Server-side snapshot of the active set (what GET .../map-overlays returns). */
function snapshotOf(incidentId: string, active: ReadonlySet<string>, kinds: Record<string, Kind>, version: number): OverlaySnapshotLike {
  const lines = [...active].filter((id) => kinds[id] === 'edge').map((id) => lineFeature(incidentId, id));
  const areas = [...active].filter((id) => kinds[id] === 'area').map((id) => areaFeature(incidentId, id));
  return {
    incident_id: incidentId,
    overlay_version: version,
    restrictions: fcOf(lines),
    hazards: { flood: fcOf([]), debris: fcOf(areas), fire: fcOf([]) },
  };
}

function eventOf(incidentId: string, type: 'restriction_added' | 'restriction_removed', id: string, kind: Kind, version: number): OverlayEventLike {
  return {
    event_id: `${incidentId}-ev-${version}`,
    incident_id: incidentId,
    type,
    payload: {
      restriction_id: id,
      kind: kind === 'edge' ? 'closure' : 'impassable_polygon',
      edge_ids: kind === 'edge' ? [`e-${id}`] : [],
      simulated: true,
      overlay_version: version,
    },
  };
}

/** A server timeline: initial active set, then toggling ops with increasing overlay_version. */
const timelineArb = fc
  .record({
    kinds: fc.record(Object.fromEntries(IDS.map((id) => [id, fc.constantFrom<Kind>('edge', 'area')])) as Record<string, fc.Arbitrary<Kind>>),
    initial: fc.subarray([...IDS]),
    v0: fc.integer({ min: 0, max: 50 }),
    ops: fc.array(fc.constantFrom(...IDS), { maxLength: 12 }),
  })
  .map(({ kinds, initial, v0, ops }) => {
    const states: Set<string>[] = [new Set(initial)];
    const events: OverlayEventLike[] = [];
    ops.forEach((id, i) => {
      const prev = states[states.length - 1];
      const next = new Set(prev);
      const removed = prev.has(id);
      if (removed) next.delete(id);
      else next.add(id);
      events.push(eventOf('inc-a', removed ? 'restriction_removed' : 'restriction_added', id, kinds[id], v0 + i + 1));
      states.push(next);
    });
    return { kinds, v0, states, events };
  });

describe('Property 2: snapshot plus live events equals current server state', () => {
  /** **Validates: Requirements 2.6, 2.4** */
  it('any snapshot point, shuffled and duplicated delivery -> final active set', () => {
    fc.assert(
      fc.property(
        timelineArb.chain((t) =>
          fc.record({
            t: fc.constant(t),
            k: fc.integer({ min: 0, max: t.events.length }),
            extra: fc.array(fc.nat(), { maxLength: 20 }),
            keys: fc.array(fc.double({ noNaN: true }), { minLength: 64, maxLength: 64 }),
          }),
        ),
        ({ t, k, extra, keys }) => {
          const snapshot = snapshotOf('inc-a', t.states[k], t.kinds, t.v0 + k);
          // every event after the snapshot arrives at least once; earlier ones and duplicates may arrive too
          const n = t.events.length;
          const delivered = [...t.events.slice(k), ...(n ? extra.map((i) => t.events[i % n]) : [])]
            .map((e, i) => ({ e, key: keys[i % keys.length] + i * 1e-9 }))
            .sort((a, b) => a.key - b.key)
            .map((x) => x.e);

          const merged = mergeRestrictions(snapshot, delivered);
          const finalState = t.states[n];
          expect([...mergedRestrictionIds(merged)].sort()).toEqual([...finalState].sort());

          // ids added after the snapshot without geometry keep their edge ids pending
          for (const [id, r] of Object.entries(merged.pending)) {
            expect(finalState.has(id)).toBe(true);
            expect(r.edge_ids).toEqual(t.kinds[id] === 'edge' ? [`e-${id}`] : []);
          }
          // no restriction is drawn twice
          const drawn = [...merged.restrictions.features, ...merged.hazards.debris.features].map((f) => f.properties?.restriction_id);
          expect(new Set(drawn).size).toBe(drawn.length);
          // idempotent under re-delivery
          expect(mergeRestrictions(snapshot, [...delivered, ...delivered])).toEqual(merged);
        },
      ),
      { numRuns: 200 },
    );
  });
});

describe('Property 4: incident switch isolation', () => {
  const INCIDENTS = ['inc-a', 'inc-b'] as const;

  const actionArb: fc.Arbitrary<OverlayAction> = fc.oneof(
    fc.record({ type: fc.constant('select' as const), incidentId: fc.constantFrom<string | null>('inc-a', 'inc-b', null) }),
    fc
      .record({ inc: fc.constantFrom(...INCIDENTS), active: fc.subarray([...IDS]), version: fc.integer({ min: 0, max: 20 }) })
      .map(({ inc, active, version }) => ({
        type: 'snapshot' as const,
        snapshot: snapshotOf(inc, new Set(active.map((id) => `${inc}-${id}`)), Object.fromEntries(
          active.map((id, i) => [`${inc}-${id}`, (i % 2 ? 'area' : 'edge') as Kind]),
        ), version),
      })),
    fc
      .record({
        inc: fc.constantFrom(...INCIDENTS),
        id: fc.constantFrom(...IDS),
        removed: fc.boolean(),
        kind: fc.constantFrom<Kind>('edge', 'area'),
        version: fc.integer({ min: 0, max: 25 }),
      })
      .map(({ inc, id, removed, kind, version }) => ({
        type: 'event' as const,
        event: eventOf(inc, removed ? 'restriction_removed' : 'restriction_added', `${inc}-${id}`, kind, version),
      })),
  );

  /** **Validates: Requirements 2.11, 3.8** */
  it("after any interleaving only the selected incident's features remain", () => {
    fc.assert(
      fc.property(fc.array(actionArb, { maxLength: 30 }), fc.array(actionArb, { maxLength: 30 }), fc.constantFrom<string | null>('inc-a', 'inc-b', null), (before, after, selected) => {
        // late responses and events for the other incident keep arriving after the switch
        const actions = [...before, { type: 'select' as const, incidentId: selected }, ...after.filter((a) => a.type !== 'select')];
        const state = actions.reduce(overlayReducer, INITIAL_OVERLAY_STATE);
        const merged = selectMerged(state, selected);
        if (selected === null) {
          expect(merged).toBe(EMPTY_OVERLAYS);
          return;
        }
        const inputs = overlayLayerInputs(merged);
        const features = [
          ...(inputs.restrictions?.features ?? []),
          ...(inputs.hazards.flood?.features ?? []),
          ...(inputs.hazards.debris?.features ?? []),
          ...(inputs.hazards.fire?.features ?? []),
        ];
        for (const f of features) expect(f.properties?.incident_id).toBe(selected);
        for (const id of Object.keys(merged.pending)) expect(id.startsWith(`${selected}-`)).toBe(true);
        if (state.snapshot) expect(state.snapshot.incident_id).toBe(selected);
      }),
      { numRuns: 300 },
    );
  });
});
