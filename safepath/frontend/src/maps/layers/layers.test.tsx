import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildClosures, buildElevation, buildHazard, buildIncidentBoundaries, buildMarkers, buildReports,
  buildRoutes, buildSimulatedStation, buildStations, buildVerified, isVerifiedDestination,
} from './data';
import { renderIcon } from './icons';
import { Legend } from './Legend';
import { clearLayerCache, layerUrl, loadLayer, setOfflineLayerStore } from './layerData';
import { MapLayers, type MapLike } from './MapLayers';
import {
  DATA_LAYER_GROUPS, ICONS, LAYER_DEFS, LEGEND_ENTRIES, SIMULATION_DESTINATION_LABEL, TOGGLES,
  normalizeHeightClass, reportSymbol, visibleLegend,
} from './styles';
import type { FC } from './types';

const pt = (props: Record<string, unknown>, c: [number, number] = [-87.63, 41.88]) => ({
  type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: c }, properties: props,
});
const poly = (props: Record<string, unknown> = {}) => ({
  type: 'Feature' as const,
  geometry: { type: 'Polygon' as const, coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
  properties: props,
});
const fc = (...features: FC['features']): FC => ({ type: 'FeatureCollection', features });

describe('style definitions', () => {
  it('assigns every layer to exactly one toggle and groups them for MapView', () => {
    const ids = LAYER_DEFS.map((d) => d.layer.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(Object.values(DATA_LAYER_GROUPS).flat().sort()).toEqual([...ids].sort());
    expect(TOGGLES.find((t) => t.id === 'graph')?.defaultVisible).toBe(false);
  });

  it('normalizes unknown or missing height classes to Unknown', () => {
    expect(normalizeHeightClass('Mid-rise')).toBe('Mid-rise');
    expect(normalizeHeightClass(undefined)).toBe('Unknown');
    expect(normalizeHeightClass('Skyscraper')).toBe('Unknown');
  });

  it('uses a distinct shape per report status, never color alone', () => {
    const shapes = ['Unverified', 'Corroborated', 'Disputed'].map((s) => reportSymbol(s).shape);
    expect(new Set(shapes).size).toBe(3);
    expect(reportSymbol('Disputed').statusText).toBe('Disputed');
    expect(ICONS[reportSymbol('Disputed').icon].hollow).toBe(true);
    expect(reportSymbol('Resolved').group).toBe('hidden');
  });

  it('keeps green for verified resources and red for closures only', () => {
    for (const s of ['Unverified', 'Corroborated', 'Disputed']) {
      expect(ICONS[reportSymbol(s).icon].color).toBe('#fb923c');
    }
    expect(ICONS['icon-closure'].color).toBe('#f87171');
    expect(ICONS['icon-verified-resource'].color).toBe('#4ade80');
    expect(reportSymbol('Officially_Verified', true).group).toBe('closures');
    expect(reportSymbol('Unverified', true).group).toBe('reports');
  });

  it('legend lists a text label for every visible group', () => {
    const all = Object.fromEntries(TOGGLES.map((t) => [t.id, true]));
    expect(visibleLegend(all)).toHaveLength(LEGEND_ENTRIES.length);
    expect(visibleLegend({ ...all, buildings: false }).some((e) => e.toggle === 'buildings')).toBe(false);
    expect(LEGEND_ENTRIES.some((e) => e.label.includes('Unknown'))).toBe(true);
  });
});

describe('icon renderer', () => {
  it('produces filled and hollow RGBA images of different shapes', () => {
    const filled = renderIcon(ICONS['icon-report-unverified'], 16);
    const hollow = renderIcon(ICONS['icon-report-disputed'], 16);
    const alpha = (img: { data: Uint8ClampedArray }) => img.data.filter((_, i) => i % 4 === 3 && img.data[i] > 0).length;
    expect(filled.data).toHaveLength(16 * 16 * 4);
    expect(alpha(filled)).toBeGreaterThan(0);
    expect(alpha(hollow)).toBeGreaterThan(0);
    const centre = (8 * 16 + 8) * 4 + 3;
    expect(hollow.data[centre]).toBe(0); // hollow: centre transparent
    expect(renderIcon(ICONS['icon-closure'], 16).data.join()).not.toBe(renderIcon(ICONS['icon-report-corroborated'], 16).data.join());
  });
});

describe('data builders', () => {
  it('stations carry GTFS data only and entrance safety Unknown', () => {
    const out = buildStations(fc(pt({ name: 'Quincy', source: 'CTA GTFS static feed' }), pt({})));
    expect(out.features[0].properties).toMatchObject({ label: 'Quincy', entrance_safety: 'Unknown', layer: 'cta-stations' });
    expect(out.features[1].properties).toMatchObject({ name: 'Unknown', wheelchair_boarding: 'Unknown', entrance_safety: 'Unknown' });
  });

  it('simulated station is labeled SIMULATED', () => {
    const out = buildSimulatedStation(fc(pt({ name: 'Clark/Lake' }), pt({ name: 'SIMULATED station platform' })));
    expect(out.features[0].properties?.label).toBe('SIMULATED Clark/Lake');
    expect(out.features[1].properties?.label).toBe('SIMULATED station platform');
    expect(out.features.every((f) => f.properties?.simulated === true)).toBe(true);
  });

  it('elevation highlight is SIMULATED and drops depth values', () => {
    const out = buildElevation(fc(poly({ depth_m: 2, flood_depth: 1, keep: 'x' })));
    const p = out.features[0].properties!;
    expect(String(p.label)).toContain('SIMULATED');
    expect(p).not.toHaveProperty('depth_m');
    expect(p).not.toHaveProperty('flood_depth');
    expect(p.keep).toBe('x');
    expect(buildElevation(null).features).toEqual([]);
  });

  it('hazards and incident boundaries are labeled; CBD layers get the historical label', () => {
    expect(buildHazard('debris', fc(poly())).features[0].properties?.label).toBe('SIMULATED debris zone');
    const hist = buildHazard('flood', fc(poly()), ['flood']).features[0].properties?.label as string;
    expect(hist).toContain('Historical reference (2007)');
    const inc = buildIncidentBoundaries(fc(poly({ name: 'Loop flood', simulation_flag: true })));
    expect(inc.features[0].properties?.label).toBe('SIMULATED Loop flood');
  });

  it('routes keep primary and alternative kinds and skip degenerate lines', () => {
    const out = buildRoutes([
      { id: 'a', kind: 'primary', coordinates: [[0, 0], [1, 1]] },
      { id: 'b', kind: 'alternative', coordinates: [[0, 0], [1, 2]] },
      { id: 'c', kind: 'alternative', coordinates: [[0, 0]] },
    ]);
    expect(out.features.map((f) => f.properties?.kind)).toEqual(['primary', 'alternative']);
  });

  it('reports use status shapes; confirmed closures move to the closures source', () => {
    const { reports, closurePoints } = buildReports([
      { report_id: '1', category: 'flooding', verification_status: 'Unverified', lng: -87.6, lat: 41.9 },
      { report_id: '2', category: 'blocked road', verification_status: 'Disputed', lng: -87.6, lat: 41.9, simulated: true },
      { report_id: '3', category: 'blocked road', verification_status: 'Officially_Verified', confirmed_closure: true, lng: -87.6, lat: 41.9 },
      { report_id: '4', category: 'other', verification_status: 'Expired', lng: -87.6, lat: 41.9 },
      { report_id: '5', category: 'other', verification_status: 'Unverified', lng: NaN, lat: 41.9 },
    ]);
    expect(reports.features.map((f) => f.properties?.icon)).toEqual(['icon-report-unverified', 'icon-report-disputed']);
    expect(reports.features[1].properties?.label).toBe('SIMULATED Disputed: blocked road');
    expect(reports.features[0].properties?.description).toBe('Unknown');
    expect(closurePoints).toHaveLength(1);
    expect(buildClosures(fc(), closurePoints).features).toHaveLength(1);
  });

  it('only official, non-simulated destinations are verified (green)', () => {
    const base = { id: 'd', name: 'Hall', lng: -87.6, lat: 41.9 };
    const official = { ...base, simulated: false, source_record: { is_official: true, source_id: 'S1' } };
    const community = { ...base, simulated: false, source_record: { is_official: false } };
    const sim = { ...base, simulated: true, source_record: { is_official: true } };
    expect(isVerifiedDestination(official)).toBe(true);
    expect(isVerifiedDestination(community)).toBe(false);
    expect(isVerifiedDestination(sim)).toBe(false);
    expect(buildVerified([official, community, sim]).features).toHaveLength(1);
  });

  it('simulated destination is labeled as not an official shelter', () => {
    const dest = { id: 'd', name: 'Point A', lng: -87.6, lat: 41.9, simulated: true };
    const out = buildMarkers([-87.62, 41.88], dest);
    expect(out.features.map((f) => f.properties?.feature_kind)).toEqual(['start', 'destination']);
    expect(out.features[1].properties?.label).toBe(SIMULATION_DESTINATION_LABEL);
    expect(SIMULATION_DESTINATION_LABEL).toContain('NOT an official evacuation shelter');
    expect(buildMarkers(null, null).features).toEqual([]);
  });
});

function fakeMap() {
  const sources = new Map<string, { data: unknown; setData: ReturnType<typeof vi.fn> }>();
  const layers = new Map<string, Record<string, unknown>>();
  const images = new Set<string>();
  const map = {
    addSource: vi.fn((id: string, spec: { data: unknown }) => {
      sources.set(id, { data: spec.data, setData: vi.fn(function (this: unknown, d: unknown) { sources.get(id)!.data = d; }) });
    }),
    getSource: (id: string) => sources.get(id),
    addLayer: vi.fn((l: { id: string }) => { layers.set(l.id, l as unknown as Record<string, unknown>); }),
    getLayer: (id: string) => layers.get(id),
    setLayoutProperty: vi.fn(),
    addImage: vi.fn((id: string) => { images.add(id); }),
    hasImage: (id: string) => images.has(id),
    setGlyphs: vi.fn(),
  };
  return { map: map as unknown as MapLike & typeof map, sources, layers, images };
}

describe('MapLayers manager', () => {
  it('registers icons, sources and layers once, honoring default visibility', () => {
    const f = fakeMap();
    const m = new MapLayers(f.map, {}, 'https://example.test/{fontstack}/{range}.pbf');
    m.init();
    m.init();
    expect(f.map.setGlyphs).toHaveBeenCalledOnce();
    expect(f.layers.size).toBe(LAYER_DEFS.length);
    expect(f.map.addImage).toHaveBeenCalledTimes(Object.keys(ICONS).length);
    expect((f.layers.get('graph-line')!.layout as Record<string, string>).visibility).toBe('none');
    expect((f.layers.get('routes-primary')!.layout as Record<string, string>).visibility).toBe('visible');
  });

  it('updates sources from inputs and keeps SIMULATED labels synchronous', () => {
    const f = fakeMap();
    const m = new MapLayers(f.map);
    m.init();
    m.setInputs({
      floodHighlight: fc(poly({ depth_m: 3 })),
      routes: [{ id: 'r', kind: 'primary', coordinates: [[0, 0], [1, 1]] }],
      destination: { id: 'd', name: 'x', lng: 0, lat: 0, simulated: true },
    });
    const elev = f.sources.get('elevation')!.data as FC;
    expect(String(elev.features[0].properties?.label)).toContain('SIMULATED');
    expect((f.sources.get('routes')!.data as FC).features).toHaveLength(1);
    expect((f.sources.get('markers')!.data as FC).features[0].properties?.label).toBe(SIMULATION_DESTINATION_LABEL);
  });

  it('applies inputs set before init once initialized, and toggles layers immediately', () => {
    const f = fakeMap();
    const m = new MapLayers(f.map);
    m.setInputs({ routes: [{ id: 'r', kind: 'primary', coordinates: [[0, 0], [1, 1]] }] });
    m.init();
    expect((f.sources.get('routes')!.data as FC).features).toHaveLength(1);
    m.setVisible('closures', false);
    for (const id of DATA_LAYER_GROUPS.closures) {
      expect(f.map.setLayoutProperty).toHaveBeenCalledWith(id, 'visibility', 'none');
    }
    expect(m.isVisible('closures')).toBe(false);
  });

  it('feeds artifact layers through the station builders', () => {
    const f = fakeMap();
    const m = new MapLayers(f.map);
    m.init();
    m.setArtifactLayer('cta_stations', fc(pt({ name: 'Quincy' })));
    m.setArtifactLayer('buildings', fc(poly({ building_height_class: 'Unknown' })));
    expect((f.sources.get('cta-stations')!.data as FC).features[0].properties?.entrance_safety).toBe('Unknown');
    expect((f.sources.get('buildings')!.data as FC).features).toHaveLength(1);
  });
});

describe('layer data loading', () => {
  const data = fc(pt({ name: 'A' }));
  beforeEach(() => clearLayerCache());
  afterEach(() => {
    vi.unstubAllGlobals();
    setOfflineLayerStore(null);
  });

  it('builds query urls and caches responses (one request for repeated loads)', async () => {
    expect(layerUrl('buildings', { simplify: true, bbox: [1, 2, 3, 4] })).toBe('/api/layers/buildings?bbox=1%2C2%2C3%2C4&simplify=true');
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(data), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const [a, b] = await Promise.all([loadLayer('cta_stations'), loadLayer('cta_stations')]);
    await loadLayer('cta_stations');
    expect(a).toEqual(data);
    expect(b).toEqual(data);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('falls back to the offline store when the network fails and does not cache failures', async () => {
    const store = new Map<string, FC>();
    setOfflineLayerStore({ get: (k) => store.get(k), put: (k, d) => void store.set(k, d) });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })));
    await loadLayer('cta_stations');
    await Promise.resolve();
    expect(store.size).toBe(1);
    clearLayerCache();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
    await expect(loadLayer('cta_stations')).resolves.toEqual(data);
    clearLayerCache();
    setOfflineLayerStore(null);
    await expect(loadLayer('cta_stations')).rejects.toThrow();
  });
});

describe('Legend', () => {
  it('renders text for visible layers only and the historical note when needed', () => {
    const all = Object.fromEntries(TOGGLES.map((t) => [t.id, true]));
    const { rerender } = render(<Legend visible={all} />);
    expect(screen.getByText(/Height Unknown/)).toBeInTheDocument();
    expect(screen.getByText(/Disputed report \(hollow square\)/)).toBeInTheDocument();
    expect(screen.getByText(SIMULATION_DESTINATION_LABEL)).toBeInTheDocument();
    expect(screen.queryByTestId('legend-historical')).toBeNull();
    rerender(<Legend visible={{ ...all, buildings: false }} historicalLayers={['flood']} />);
    expect(screen.queryByText(/Height Unknown/)).toBeNull();
    expect(screen.getByTestId('legend-historical')).toBeInTheDocument();
  });
});
