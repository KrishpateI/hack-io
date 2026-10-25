/**
 * Bug condition exploration (map-legend-layers-fix, task 1), Property 1:
 * every applicable legend entry is drawn for the selected incident, and the legend lists no entry
 * without data.
 *
 * These tests encode the EXPECTED behavior and are expected to FAIL on the unfixed code. The data
 * path is built the way AppShell.tsx builds `layerInputs`; when the fix's helpers exist
 * (`mergeRestrictions`, `overlayLayerInputs`, `legendPresence`, `useArtifactStatus`) they are picked
 * up through dynamic imports, so the same tests validate the fix without import errors today.
 *
 * **Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10**
 */
import { renderHook } from '@testing-library/react';
import fc from 'fast-check';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Incident } from '../../services/api';
import type { RealtimeEvent } from '../../services/eventStore';
import * as mapData from '../../services/mapData';
import { MapLayers, type MapLike } from './MapLayers';
import * as layerData from './layerData';
import * as styles from './styles';
import type { FC, MapLayerInputs } from './types';

type AnyFn = (...args: unknown[]) => unknown;
const optional = (mod: unknown, name: string): AnyFn | null => {
  const f = (mod as Record<string, unknown>)[name];
  return typeof f === 'function' ? (f as AnyFn) : null;
};

// ------------------------------------------------------------------ fixtures
function fakeMap() {
  const sources = new Map<string, { data: unknown; setData: (d: unknown) => void }>();
  const layers = new Map<string, unknown>();
  const images = new Set<string>();
  const map: MapLike = {
    addSource: (id, spec) => {
      const entry = { data: (spec as { data: unknown }).data, setData: (d: unknown) => { entry.data = d; } };
      sources.set(id, entry);
    },
    getSource: (id) => sources.get(id),
    addLayer: (l) => { layers.set((l as { id: string }).id, l); },
    getLayer: (id) => layers.get(id),
    setLayoutProperty: () => undefined,
    setPaintProperty: () => undefined,
    addImage: (id) => { images.add(id); },
    hasImage: (id) => images.has(id),
  };
  const features = (id: string): FC['features'] => ((sources.get(id)?.data as FC | undefined)?.features ?? []);
  return { map, features };
}

const defaults = (): Record<string, boolean> => Object.fromEntries(styles.TOGGLES.map((t) => [t.id, t.defaultVisible]));
const allOn = (): Record<string, boolean> => Object.fromEntries(styles.TOGGLES.map((t) => [t.id, true]));

function circle(lat: number, lng: number, radiusM: number, n = 24): number[][] {
  const out: number[][] = [];
  for (let i = 0; i <= n; i++) {
    const a = (2 * Math.PI * (i % n)) / n;
    out.push([lng + (radiusM * Math.cos(a)) / (111_320 * Math.cos((lat * Math.PI) / 180)), lat + (radiusM * Math.sin(a)) / 110_540]);
  }
  return out;
}

const incident = (id: string, type: string, boundary: unknown = null): Incident =>
  ({ incident_id: id, incident_type: type, simulation_flag: true, geographic_boundary: boundary, status: 'active' }) as unknown as Incident;

interface Notice { notice_id: string; notice_type: string; simulation_flag: boolean; text: string; geometry: unknown }

/** The MapOverlays payload the design's endpoint returns (used only when the fix's helpers exist). */
function overlaySnapshot(incidentId: string, parts: { fire?: FC['features']; debris?: FC['features']; restrictions?: FC['features'] }) {
  const coll = (features: FC['features'] = []): FC => ({ type: 'FeatureCollection', features });
  return {
    incident_id: incidentId,
    simulation_flag: true,
    overlay_version: 1,
    disclaimer: 'SIMULATED data for demonstration. Not official.',
    restrictions: coll(parts.restrictions),
    hazards: { flood: coll(), debris: coll(parts.debris), fire: coll(parts.fire) },
    limitations: { missing_edge_geometry: 0, graph_loaded: true },
  };
}

/**
 * `layerInputs` exactly as AppShell builds them: routes, reports, incident boundary, closures from the
 * live restriction reducer (`applyRestrictionEvent` + `closureFeatures`), markers. With the fix in place,
 * the hazards/restrictions from the map-overlays snapshot are merged in the same way AppShell will.
 */
function appShellInputs(ctx: {
  incident: Incident | null;
  events?: Pick<RealtimeEvent, 'type' | 'payload'>[];
  edgeCoords?: Record<string, number[][]>;
  snapshot?: unknown;
}): MapLayerInputs {
  let active: Record<string, mapData.ActiveRestriction> = {};
  for (const e of ctx.events ?? []) active = mapData.applyRestrictionEvent(active, e);
  const inputs: MapLayerInputs = {
    routes: mapData.routeLines(null, null),
    reports: [],
    incidentBoundaries: mapData.incidentBoundaryFC(ctx.incident),
    closures: mapData.closureFeatures(active, ctx.edgeCoords ?? {}),
    start: null,
    destination: null,
    historicalLayers: [],
  };
  const merge = optional(mapData, 'mergeRestrictions');
  const toInputs = optional(mapData, 'overlayLayerInputs');
  if (merge && toInputs && ctx.snapshot) {
    Object.assign(inputs, toInputs(merge(ctx.snapshot, ctx.events ?? [])) as Partial<MapLayerInputs>);
  }
  return inputs;
}

function draw(inputs: MapLayerInputs, visible: Record<string, boolean> = defaults()) {
  const f = fakeMap();
  const m = new MapLayers(f.map, visible);
  m.init();
  m.setInputs(inputs);
  return f;
}

function legendIds(inputs: MapLayerInputs, visible: Record<string, boolean>, artifactStatus: Record<string, unknown> = {}): string[] {
  const presenceFn = optional(styles, 'legendPresence');
  const vl = styles.visibleLegend as unknown as AnyFn;
  const entries = (presenceFn ? vl(visible, presenceFn(inputs, artifactStatus)) : vl(visible)) as styles.LegendEntry[];
  return entries.map((e) => e.id);
}

// ------------------------------------------------------------------ hazards and restrictions
describe('Property 1: hazards for the selected incident reach the map sources', () => {
  it('sim-fire-downtown: the SIMULATED temporary_perimeter notice is drawn in the fire source', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 41.86, max: 41.9, noNaN: true }),
        fc.double({ min: -87.65, max: -87.61, noNaN: true }),
        fc.integer({ min: 50, max: 400 }),
        (lat, lng, r) => {
          const notice: Notice = {
            notice_id: 'sim-fire-perimeter-1', notice_type: 'temporary_perimeter', simulation_flag: true,
            text: 'SIMULATED fire perimeter (not an official instruction)',
            geometry: { type: 'Polygon', coordinates: [circle(lat, lng, r)] },
          };
          const snapshot = overlaySnapshot('sim-fire-downtown', {
            fire: [{ type: 'Feature', geometry: notice.geometry as FC['features'][number]['geometry'],
              properties: { notice_id: notice.notice_id, simulated: true, applied_to_routing: true, label: notice.text } }],
          });
          const f = draw(appShellInputs({ incident: incident('sim-fire-downtown', 'fire'), snapshot }));
          expect(f.features('fire').length).toBeGreaterThan(0);
        },
      ),
      { numRuns: 10 },
    );
  });

  it('restriction_added impassable_polygon (edge_ids []) is drawn as a polygon', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 3 }), fc.integer({ min: 20, max: 120 }), (zones, r) => {
        const ids = Array.from({ length: zones }, (_, i) => `sim-eq-debris-${i + 1}`);
        const events = ids.map((id, i) => ({
          type: 'restriction_added',
          payload: { restriction_id: id, kind: 'impassable_polygon', edge_ids: [], simulated: true, overlay_version: i + 2 },
        }));
        // what the debounced refetch returns after the events (geometry is not in the event payload)
        const snapshot = {
          ...overlaySnapshot('sim-earthquake-debris', {
            debris: ids.map((id, i) => ({ type: 'Feature' as const,
              geometry: { type: 'Polygon' as const, coordinates: [circle(41.88 + i * 0.002, -87.63, r)] },
              properties: { restriction_id: id, zone_id: id, simulated: true, applied_to_routing: true, label: 'SIMULATED debris zone' } })),
          }),
          overlay_version: zones + 1,
        };
        const f = draw(appShellInputs({ incident: incident('sim-earthquake-debris', 'earthquake'), events, snapshot }));
        const polygons = [...f.features('debris'), ...f.features('closures'), ...f.features('restrictions')]
          .filter((x) => x.geometry.type === 'Polygon' || x.geometry.type === 'MultiPolygon');
        expect(polygons.length).toBe(zones);
      }),
      { numRuns: 10 },
    );
  });

  it('a closure that existed before page load is drawn by a fresh reducer (no live events)', () => {
    const edge = { id: 'fx-sim-e031', coords: [[-87.63, 41.88], [-87.629, 41.881]] };
    const snapshot = overlaySnapshot('sim-flood-loop', {
      restrictions: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: edge.coords },
        properties: { restriction_id: 'sim-flood-closure-1', edge_id: edge.id, kind: 'closure', simulated: true,
          status: 'active', applied_to_routing: true, label: 'SIMULATED closed road segment (not a real closure)' } }],
    });
    const f = draw(appShellInputs({ incident: incident('sim-flood-loop', 'flood'), events: [], edgeCoords: { [edge.id]: edge.coords }, snapshot }));
    expect(f.features('restrictions').length).toBeGreaterThan(0);
  });

  it('street graph toggle: either it draws features or the toggle does not exist', () => {
    const hasGraph = styles.TOGGLES.some((t) => t.id === 'graph');
    const f = draw(appShellInputs({ incident: incident('sim-flood-loop', 'flood') }), allOn());
    expect(!hasGraph || f.features('graph').length > 0).toBe(true);
  });
});

// ------------------------------------------------------------------ legend presence
describe('Property 1: the legend lists no entry without data', () => {
  const INCIDENT_ONLY = ['hazard-flood', 'hazard-debris', 'hazard-fire', 'closure', 'verified'];

  it('tornado incident: no flood/debris/fire/closure/verified entries for any toggle map', () => {
    const tornado = incident('sim-tornado-shelter', 'tornado', { type: 'Polygon', coordinates: [circle(41.885, -87.632, 600)] });
    const toggleIds = styles.TOGGLES.map((t) => t.id);
    expect(legendIds(appShellInputs({ incident: tornado }), defaults()).filter((id) => INCIDENT_ONLY.includes(id))).toEqual([]);
    fc.assert(
      fc.property(fc.record(Object.fromEntries(toggleIds.map((id) => [id, fc.boolean()]))), (visible) => {
        const listed = legendIds(appShellInputs({ incident: tornado }), visible as Record<string, boolean>);
        expect(listed.filter((id) => INCIDENT_ONLY.includes(id))).toEqual([]);
      }),
      { numRuns: 25 },
    );
  });
});

// ------------------------------------------------------------------ artifact layer failure
describe('Property 1: a failed station layer is omitted from the legend and retried', () => {
  const okFc = { type: 'FeatureCollection', features: [] };
  let calls: string[] = [];

  beforeEach(() => {
    calls = [];
    layerData.clearLayerCache();
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push(String(url));
      if (String(url).includes('/api/layers/simulated_station')) return new Response('unavailable', { status: 503 });
      return new Response(JSON.stringify(okFc), { status: 200 });
    }));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    layerData.clearLayerCache();
  });

  const manager = () => ({ setArtifactLayer: vi.fn() }) as unknown as MapLayers;
  const stationCalls = () => calls.filter((u) => u.includes('/api/layers/simulated_station')).length;

  it('legend omits sim-station after /api/layers/simulated_station returns 503', async () => {
    const visible = defaults();
    renderHook(() => layerData.useArtifactLayers(manager(), visible));
    await vi.advanceTimersByTimeAsync(10);
    expect(stationCalls()).toBe(1);
    const statusHook = optional(layerData, 'useArtifactStatus');
    const status = statusHook ? (renderHook(() => statusHook()).result.current as Record<string, unknown>) : {};
    expect(legendIds(appShellInputs({ incident: null }), visible, status)).not.toContain('sim-station');
  });

  it('a retry of the failed station layer is scheduled', async () => {
    const visible = defaults();
    renderHook(() => layerData.useArtifactLayers(manager(), visible));
    await vi.advanceTimersByTimeAsync(10);
    expect(stationCalls()).toBe(1);
    await vi.advanceTimersByTimeAsync(6 * 60 * 1000);
    expect(stationCalls()).toBeGreaterThan(1);
  });
});
