// Feature: map-legend-layers-fix, task 3.8: AppShell feeds map-overlays hazards and restrictions to
// the map and a presence-aware legend.
// **Validates: Requirements 2.1, 2.3, 2.5, 2.6, 2.9, 2.11, 2.12, 3.1, 3.6, 3.8, 3.11**
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Incident } from './services/api';
import { initialEventState } from './services/eventStore';
import type { RealtimeEvent } from './services/eventStore';
import type { RealtimeSnapshot } from './services/realtimeController';
import type { FC, Feat, MapLayerInputs } from './maps/layers/types';

// ---------------------------------------------------------------- mocks (as App.test.tsx)
const h = vi.hoisted(() => ({
  incidents: [] as unknown[],
  overlays: {} as Record<string, unknown>,
  overlayCalls: [] as string[],
  snapshot: null as unknown,
  onEvent: null as null | ((e: unknown) => void),
  /** Fake MapLibre map behind the stubbed MapView: source id -> current data. */
  sources: new Map<string, { data: unknown; setData: (d: unknown) => void }>(),
  layout: new Map<string, string>(),
}));

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>();
  return {
    ...actual,
    ensureSession: vi.fn(async () => 'token'),
    listIncidents: vi.fn(async () => h.incidents),
    demoStatus: vi.fn(async () => ({ demo_mode: false })),
    getMe: vi.fn(async () => ({ subject: 'guest-1', role: 'guest', is_guest: true, display_name: 'Heron-12', expires_at: null })),
    listReports: vi.fn(async () => ({ reports: [] })),
    listMessages: vi.fn(async () => ({ messages: [] })),
    listCheckins: vi.fn(async () => ({ checkins: [] })),
    getMapOverlays: vi.fn(async (id: string) => {
      h.overlayCalls.push(id);
      const payload = h.overlays[id];
      if (!payload) throw new actual.ApiError(404, 'not_found', 'Incident not found');
      return structuredClone(payload);
    }),
  };
});

vi.mock('./hooks/useRealtime', () => ({
  useRealtime: (_id: string | null, onEvent?: (e: unknown) => void) => {
    h.onEvent = onEvent ?? null;
    return h.snapshot;
  },
}));

// MapView with the real MapLayers manager on a fake map, so the test sees the drawn sources.
vi.mock('./maps/MapView', async () => {
  const React = await import('react');
  const { MapLayers } = await import('./maps/layers/MapLayers');
  const { TOGGLES } = await import('./maps/layers/styles');
  const { DEFAULT_LAYER_VISIBILITY } = await import('./maps/useLayerToggles');
  type Props = { layerInputs?: MapLayerInputs; layerVisibility?: Record<string, boolean> };
  const images = new Set<string>();
  const layers = new Map<string, unknown>();
  const map = {
    addSource: (id: string, spec: unknown) => {
      const entry = { data: (spec as { data: unknown }).data, setData: (d: unknown) => { entry.data = d; } };
      h.sources.set(id, entry);
    },
    getSource: (id: string) => h.sources.get(id),
    addLayer: (l: unknown) => {
      const layer = l as { id: string; layout?: { visibility?: string } };
      layers.set(layer.id, l);
      h.layout.set(layer.id, layer.layout?.visibility ?? 'visible');
    },
    getLayer: (id: string) => layers.get(id),
    setLayoutProperty: (id: string, prop: string, value: unknown) => {
      if (prop === 'visibility') h.layout.set(id, String(value));
    },
    addImage: (id: string) => { images.add(id); },
    hasImage: (id: string) => images.has(id),
  };
  return {
    MapView: ({ layerInputs, layerVisibility }: Props) => {
      const ref = React.useRef<InstanceType<typeof MapLayers> | null>(null);
      React.useEffect(() => {
        h.sources.clear();
        h.layout.clear();
        layers.clear();
        images.clear();
        const m = new MapLayers(map, { ...DEFAULT_LAYER_VISIBILITY, ...layerVisibility });
        m.init();
        ref.current = m;
        return () => { ref.current = null; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
      React.useEffect(() => {
        if (layerInputs) ref.current?.setInputs(layerInputs);
      }, [layerInputs]);
      React.useEffect(() => {
        const v = { ...DEFAULT_LAYER_VISIBILITY, ...layerVisibility };
        for (const t of TOGGLES) ref.current?.setVisible(t.id, v[t.id] !== false);
      }, [layerVisibility]);
      return <div data-testid="map-stub" />;
    },
  };
});

import App from './App';

// ---------------------------------------------------------------- fixtures
const ring = (lng: number, lat: number, d = 0.002) => [[[lng, lat], [lng + d, lat], [lng + d, lat + d], [lng, lat + d], [lng, lat]]];
const poly = (lng: number, lat: number, props: Record<string, unknown>): Feat => ({
  type: 'Feature',
  geometry: { type: 'Polygon', coordinates: ring(lng, lat) },
  properties: props,
});
const coll = (...features: Feat[]): FC => ({ type: 'FeatureCollection', features });

const incident = (id: string, type: string, over: Partial<Incident> = {}): Incident =>
  ({
    incident_id: id,
    incident_type: type,
    simulation_flag: true,
    status: 'active',
    source: 'SIMULATED',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  }) as Incident;

const TORNADO_BOUNDARY = { type: 'Polygon', coordinates: ring(-87.64, 41.87, 0.01) };

const INCIDENTS = [
  incident('sim-flood-loop', 'flood'),
  incident('sim-earthquake-debris', 'earthquake'),
  incident('sim-fire-downtown', 'fire'),
  incident('sim-tornado', 'tornado', { geographic_boundary: TORNADO_BOUNDARY } as Partial<Incident>),
  incident('sim-building-stairwell', 'building'),
];

const payload = (id: string, over: { hazards?: Partial<Record<'flood' | 'debris' | 'fire', FC>>; restrictions?: FC; overlay_version?: number } = {}) => ({
  incident_id: id,
  simulation_flag: true,
  overlay_version: over.overlay_version ?? 3,
  generated_at: '2026-01-01T00:00:00Z',
  disclaimer: 'SIMULATED data for demonstration. Not official. Follow official instructions and call 911 in an emergency.',
  restrictions: over.restrictions ?? coll(),
  hazards: { flood: coll(), debris: coll(), fire: coll(), ...over.hazards },
  limitations: { missing_edge_geometry: 0, graph_loaded: true },
});

const SIM = { simulated: true, classification: 'Simulated' };
const OVERLAYS: Record<string, unknown> = {
  'sim-flood-loop': payload('sim-flood-loop', {
    hazards: {
      flood: coll(poly(-87.63, 41.88, { ...SIM, label: 'SIMULATED flood area (modeled low-lying streets, no depth values)', applied_to_routing: false })),
    },
    // A closure applied before the page was opened (Req 2.6).
    restrictions: coll({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[-87.631, 41.881], [-87.629, 41.882]] },
      properties: { ...SIM, restriction_id: 'sim-closure-1', edge_id: 'e1', kind: 'closure', status: 'active', applied_to_routing: true, label: 'SIMULATED closed road segment (not a real closure)' },
    }),
  }),
  'sim-earthquake-debris': payload('sim-earthquake-debris', {
    hazards: {
      debris: coll(
        poly(-87.632, 41.884, { ...SIM, zone_id: 'z1', label: 'SIMULATED scenario area, not applied to routing', applied_to_routing: false }),
        poly(-87.628, 41.886, { ...SIM, zone_id: 'z2', label: 'SIMULATED scenario area, not applied to routing', applied_to_routing: false }),
      ),
    },
  }),
  'sim-fire-downtown': payload('sim-fire-downtown', {
    hazards: {
      fire: coll(poly(-87.63, 41.885, { ...SIM, notice_id: 'n-fire', label: 'SIMULATED fire perimeter (not an official instruction)', applied_to_routing: true })),
    },
  }),
  'sim-tornado': payload('sim-tornado'),
  'sim-building-stairwell': payload('sim-building-stairwell'),
};

const snapshot = (): RealtimeSnapshot => ({
  status: 'connected',
  lastSyncedAt: Date.UTC(2026, 0, 1, 12, 0, 0),
  stale: false,
  store: initialEventState,
});

const features = (id: string): Feat[] => ((h.sources.get(id)?.data as FC | undefined)?.features ?? []);
const counts = () => ({
  flood: features('flood').length,
  debris: features('debris').length,
  fire: features('fire').length,
  restrictions: features('restrictions').length,
  incidents: features('incidents').length,
});

/** In LEGEND_ENTRIES order. */
const INCIDENT_LEGEND = ['closure', 'verified', 'hazard-flood', 'hazard-debris', 'hazard-fire', 'incident'];
/** Incident-dependent legend entries currently listed. */
function legendIds(): string[] {
  const legend = screen.getByTestId('map-legend');
  return INCIDENT_LEGEND.filter((id) => within(legend).queryByTestId(`legend-${id}`));
}

beforeEach(() => {
  h.incidents = INCIDENTS;
  h.overlays = OVERLAYS;
  h.overlayCalls = [];
  h.snapshot = snapshot();
  h.onEvent = null;
  window.location.hash = '';
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}), text: async () => '' })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

async function loaded() {
  render(<App />);
  await screen.findByText(/Active incident/);
  await waitFor(() => expect(screen.queryByText('Loading incidents…')).toBeNull());
}

async function selectIncident(typeLabel: string, id: string) {
  const selector = screen.getByTestId('disaster-selector');
  await userEvent.click(within(selector).getByRole('button', { name: new RegExp(`^(\\u2713 )?${typeLabel}$`) }));
  await userEvent.click(within(selector).getByRole('button', { name: new RegExp(id) }));
  await waitFor(() => expect(h.overlayCalls).toContain(id));
}

// ---------------------------------------------------------------- tests
describe('AppShell map overlays (demo mode off)', () => {
  it('draws each seeded incident’s hazards and lists only the legend entries with data', async () => {
    await loaded();

    // Flood is the default (first active) incident: flood area + pre-existing closure line.
    await waitFor(() => expect(counts()).toMatchObject({ flood: 1, restrictions: 1 }));
    expect(counts()).toMatchObject({ debris: 0, fire: 0, incidents: 0 });
    expect(features('flood')[0].properties?.label).toMatch(/^SIMULATED/);
    expect(features('restrictions')[0].properties?.restriction_id).toBe('sim-closure-1');
    // The closures source carries only report-derived closure points.
    expect(features('closures')).toHaveLength(0);
    await waitFor(() => expect(legendIds()).toEqual(['closure', 'hazard-flood']));

    await selectIncident('Earthquake', 'sim-earthquake-debris');
    await waitFor(() => expect(counts()).toEqual({ flood: 0, debris: 2, fire: 0, restrictions: 0, incidents: 0 }));
    for (const f of features('debris')) expect(f.properties?.label).toMatch(/^SIMULATED/);
    await waitFor(() => expect(legendIds()).toEqual(['hazard-debris']));

    await selectIncident('Fire', 'sim-fire-downtown');
    await waitFor(() => expect(counts()).toEqual({ flood: 0, debris: 0, fire: 1, restrictions: 0, incidents: 0 }));
    expect(features('fire')[0].properties?.label).toMatch(/^SIMULATED fire perimeter/);
    await waitFor(() => expect(legendIds()).toEqual(['hazard-fire']));

    await selectIncident('Tornado', 'sim-tornado');
    await waitFor(() => expect(counts()).toEqual({ flood: 0, debris: 0, fire: 0, restrictions: 0, incidents: 1 }));
    await waitFor(() => expect(legendIds()).toEqual(['incident']));

    await selectIncident('Building emergency', 'sim-building-stairwell');
    await waitFor(() => expect(counts()).toEqual({ flood: 0, debris: 0, fire: 0, restrictions: 0, incidents: 0 }));
    await waitFor(() => expect(legendIds()).toEqual([]));

    // Nothing incident-dependent is ever listed without data; artifact layers that never loaded are absent.
    const legend = screen.getByTestId('map-legend');
    expect(within(legend).queryByTestId('legend-sim-station')).toBeNull();
    expect(within(legend).queryByTestId('legend-transit')).toBeNull();
  });

  it('switching flood -> fire -> another incident clears the previous hazards and redraws', async () => {
    await loaded();
    await waitFor(() => expect(counts()).toMatchObject({ flood: 1, restrictions: 1 }));

    await selectIncident('Fire', 'sim-fire-downtown');
    await waitFor(() => expect(counts()).toMatchObject({ flood: 0, restrictions: 0, fire: 1 }));
    await waitFor(() => expect(legendIds()).toEqual(['hazard-fire']));

    // An incident with no overlays: everything from fire is cleared (Req 2.11).
    await selectIncident('Building emergency', 'sim-building-stairwell');
    await waitFor(() => expect(counts()).toEqual({ flood: 0, debris: 0, fire: 0, restrictions: 0, incidents: 0 }));
    await waitFor(() => expect(legendIds()).toEqual([]));

    // Back to flood: redrawn from a fresh snapshot.
    await selectIncident('Flood', 'sim-flood-loop');
    await waitFor(() => expect(counts()).toMatchObject({ flood: 1, restrictions: 1, fire: 0 }));
    await waitFor(() => expect(legendIds()).toEqual(['closure', 'hazard-flood']));
  });

  it('draws no hazards, closures or boundary and lists none of them when no incident is selected', async () => {
    h.incidents = INCIDENTS.map((i) => ({ ...i, status: 'closed' }));
    await loaded();
    expect(screen.getByTestId('map-stub')).toBeInTheDocument();
    expect(counts()).toEqual({ flood: 0, debris: 0, fire: 0, restrictions: 0, incidents: 0 });
    expect(legendIds()).toEqual([]);
    expect(h.overlayCalls).toEqual([]);
  });

  it('feeds realtime restriction events into the overlays: a removal clears the line at once', async () => {
    await loaded();
    await waitFor(() => expect(counts()).toMatchObject({ restrictions: 1 }));
    const event: RealtimeEvent = {
      event_id: 'e-rm',
      type: 'restriction_removed',
      incident_id: 'sim-flood-loop',
      payload: { restriction_id: 'sim-closure-1', overlay_version: 4 },
      created_at: '2026-01-01T00:01:00Z',
    };
    act(() => h.onEvent?.(event));
    await waitFor(() => expect(counts()).toMatchObject({ restrictions: 0, flood: 1 }));
    await waitFor(() => expect(legendIds()).toEqual(['hazard-flood']));
    // The event also schedules a debounced refetch of the snapshot.
    await waitFor(() => expect(h.overlayCalls.filter((id) => id === 'sim-flood-loop').length).toBeGreaterThanOrEqual(2));
    expect(counts()).toMatchObject({ restrictions: 0 });
  });

  it('turning a toggle off hides its layers and its legend entries (Req 2.12)', async () => {
    await loaded();
    await waitFor(() => expect(legendIds()).toEqual(['closure', 'hazard-flood']));
    await userEvent.click(screen.getByRole('switch', { name: /SIMULATED hazards/ }));
    await waitFor(() => expect(legendIds()).toEqual(['closure']));
    for (const id of ['flood-fill', 'flood-outline', 'debris-fill', 'fire-fill']) expect(h.layout.get(id)).toBe('none');
    expect(features('flood')).toHaveLength(1);
  });
});
