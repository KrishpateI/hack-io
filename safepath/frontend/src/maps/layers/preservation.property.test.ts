// Feature: map-legend-layers-fix, Property 6 (Preservation): non-hazard layers render as before
// **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.12**
//
// Observation-first: the reference builders below are a frozen copy of what the UNFIXED
// `data.ts` builders produce for routes, reports, incident boundaries, start/destination,
// verified destinations and the artifact layers. They are written out independently (not by
// calling `data.ts`), so after the fix the real `MapLayers.setInputs` output is still compared
// against the pre-fix behavior. Explicit golden objects pin representative inputs.
//
// Deliberately NOT asserted (the fix changes them on purpose): the `elevation` and `graph`
// toggles/sources/legend entries, the flood legend label text, and edge-restriction lines in
// the `closures` source (only report-derived closure points are preserved).
import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import type { Appearance } from '../../styles/tokens';
import { applyBasemapAppearance, type BasemapKind } from '../basemapAppearance';
import { MapLayers, type MapLike } from './MapLayers';
import { LAYER_DEFS, TOGGLES, visibleLegend } from './styles';
import type { DestinationInput, FC, Feat, LngLat, MapLayerInputs, ReportMarkerInput, RouteLineInput } from './types';

// ------------------------------------------------------------------ fake map (as layers.test.tsx)
function fakeMap() {
  const sources = new Map<string, { data: unknown; setData: (d: unknown) => void }>();
  const layers = new Map<string, Record<string, unknown>>();
  const images = new Set<string>();
  const map = {
    addSource: vi.fn((id: string, spec: { data: unknown }) => {
      sources.set(id, { data: spec.data, setData: (d: unknown) => { sources.get(id)!.data = d; } });
    }),
    getSource: (id: string) => sources.get(id),
    addLayer: vi.fn((l: { id: string }) => { layers.set(l.id, l as unknown as Record<string, unknown>); }),
    getLayer: (id: string) => layers.get(id),
    removeLayer: vi.fn(),
    removeSource: vi.fn(),
    setLayoutProperty: vi.fn(),
    setPaintProperty: vi.fn(),
    addImage: vi.fn((id: string) => { images.add(id); }),
    hasImage: (id: string) => images.has(id),
    setGlyphs: vi.fn(),
  };
  return { map: map as unknown as MapLike & typeof map, sources, layers };
}

const data = (f: ReturnType<typeof fakeMap>, id: string) => f.sources.get(id)!.data as FC;

// ------------------------------------------------------------------ frozen reference builders
const SIMULATED = 'SIMULATED';
const SIM_DEST_LABEL = 'SIMULATION DESTINATION — NOT an official evacuation shelter';
const collection = (features: Feat[]): FC => ({ type: 'FeatureCollection', features });
const isPoint = (lng: unknown, lat: unknown) =>
  typeof lng === 'number' && typeof lat === 'number' && Number.isFinite(lng) && Number.isFinite(lat);
const feats = (src: FC | null | undefined): Feat[] => (src && Array.isArray(src.features) ? src.features : []);

function refWithProps(src: FC | null | undefined, layer: string, kind: string, extra: (f: Feat) => Record<string, unknown>): FC {
  return collection(
    feats(src)
      .filter((f) => f && f.geometry)
      .map((f) => ({ ...f, properties: { ...(f.properties ?? {}), layer, feature_kind: kind, ...extra(f) } })),
  );
}

function refRoutes(routes: readonly RouteLineInput[] | undefined): FC {
  return collection(
    (routes ?? [])
      .filter((r) => r.coordinates && r.coordinates.length >= 2)
      .map((r) => ({
        type: 'Feature' as const,
        geometry: { type: 'LineString' as const, coordinates: r.coordinates },
        properties: {
          ...(r.properties ?? {}),
          layer: 'routes',
          feature_kind: 'route',
          route_id: r.id,
          kind: r.kind,
          label: r.label ?? (r.kind === 'primary' ? 'Algorithm-generated route' : 'Alternative route'),
        },
      })),
  );
}

type Sym = { group: 'reports' | 'closures' | 'hidden'; icon: string; statusText: string };
function refSymbol(status: string, confirmed: boolean): Sym {
  if (confirmed && status === 'Officially_Verified') return { group: 'closures', icon: 'icon-closure', statusText: 'Officially verified closure' };
  if (status === 'Unverified') return { group: 'reports', icon: 'icon-report-unverified', statusText: 'Unverified' };
  if (status === 'Corroborated') return { group: 'reports', icon: 'icon-report-corroborated', statusText: 'Corroborated' };
  if (status === 'Disputed') return { group: 'reports', icon: 'icon-report-disputed', statusText: 'Disputed' };
  if (status === 'Officially_Verified') return { group: 'reports', icon: 'icon-report-official', statusText: 'Officially verified' };
  return { group: 'hidden', icon: 'icon-report-unverified', statusText: status };
}

function refReports(reports: readonly ReportMarkerInput[] | undefined): { reports: FC; closurePoints: Feat[] } {
  const out: Feat[] = [];
  const closures: Feat[] = [];
  for (const r of reports ?? []) {
    if (!isPoint(r.lng, r.lat)) continue;
    const sym = refSymbol(r.verification_status, r.confirmed_closure === true);
    if (sym.group === 'hidden') continue;
    const f: Feat = {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [r.lng, r.lat] },
      properties: {
        layer: sym.group,
        feature_kind: 'report',
        report_id: r.report_id,
        category: r.category,
        verification_status: r.verification_status,
        status_text: sym.statusText,
        description: r.description ?? 'Unknown',
        source_label: r.source_label ?? 'Community report',
        simulated: r.simulated === true,
        confirmations: r.confirmations ?? 0,
        disputes: r.disputes ?? 0,
        created_at: r.created_at ?? 'Unknown',
        icon: sym.icon,
        label: `${r.simulated ? `${SIMULATED} ` : ''}${sym.statusText}: ${r.category}`,
      },
    };
    (sym.group === 'closures' ? closures : out).push(f);
  }
  return { reports: collection(out), closurePoints: closures };
}

const refBoundaries = (src: FC | null | undefined) =>
  refWithProps(src, 'incidents', 'incident_boundary', (f) => {
    const p = f.properties ?? {};
    const name = typeof p.name === 'string' && p.name ? p.name : 'Incident boundary';
    const sim = p.simulation_flag === true || p.simulated === true;
    return { simulated: sim, label: sim ? `${SIMULATED} ${name}` : name };
  });

const refIsVerified = (d: DestinationInput) => d.simulated !== true && d.source_record?.is_official === true;

const refVerified = (list: readonly DestinationInput[] | undefined) =>
  collection(
    (list ?? []).filter((d) => refIsVerified(d) && isPoint(d.lng, d.lat)).map((d) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [d.lng, d.lat] },
      properties: {
        layer: 'verified',
        feature_kind: 'verified_destination',
        destination_id: d.id,
        name: d.name,
        source_record_id: d.source_record?.source_id ?? 'Unknown',
        label: d.name,
      },
    })),
  );

function refMarkers(start: LngLat | null | undefined, dest: DestinationInput | null | undefined): FC {
  const out: Feat[] = [];
  if (start && isPoint(start[0], start[1])) {
    out.push({ type: 'Feature', geometry: { type: 'Point', coordinates: start },
      properties: { layer: 'markers', feature_kind: 'start', icon: 'icon-start', label: 'Start' } });
  }
  if (dest && isPoint(dest.lng, dest.lat) && !refIsVerified(dest)) {
    const sim = dest.simulated === true;
    out.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [dest.lng, dest.lat] },
      properties: {
        layer: 'markers', feature_kind: 'destination', destination_id: dest.id, name: dest.name, simulated: sim,
        icon: 'icon-destination-simulated',
        label: sim ? SIM_DEST_LABEL : `${dest.name} (not verified as an official shelter)`,
      } });
  }
  return collection(out);
}

const refStations = (src: FC | null | undefined) =>
  refWithProps(src, 'cta-stations', 'cta_station', (f) => {
    const p = f.properties ?? {};
    const name = typeof p.name === 'string' && p.name ? p.name : 'Unknown';
    return {
      label: name, name,
      source: typeof p.source === 'string' && p.source ? p.source : 'CTA GTFS static feed',
      wheelchair_boarding: p.wheelchair_boarding ?? 'Unknown',
      entrance_safety: 'Unknown',
    };
  });

const refSimStation = (src: FC | null | undefined) =>
  refWithProps(src, 'simulated-station', 'simulated_station', (f) => {
    const name = typeof f.properties?.name === 'string' ? (f.properties.name as string) : 'station';
    return { simulated: true, label: name.toUpperCase().startsWith(SIMULATED) ? name : `${SIMULATED} ${name}` };
  });

// ------------------------------------------------------------------ generators
const lng = fc.double({ min: -87.7, max: -87.55, noNaN: true });
const lat = fc.double({ min: 41.8, max: 41.95, noNaN: true });
const coord: fc.Arbitrary<LngLat> = fc.tuple(lng, lat);
const word = fc.string({ minLength: 0, maxLength: 12 });

const routeArb: fc.Arbitrary<RouteLineInput> = fc.record(
  {
    id: fc.string({ minLength: 1, maxLength: 8 }),
    kind: fc.constantFrom('primary' as const, 'alternative' as const),
    coordinates: fc.array(coord, { maxLength: 5 }),
    label: word,
    properties: fc.dictionary(fc.constantFrom('distance_meters', 'reason_summary', 'route_type', 'label'), fc.oneof(word, fc.nat())),
  },
  { requiredKeys: ['id', 'kind', 'coordinates'] },
);

const STATUSES = ['Unverified', 'Corroborated', 'Disputed', 'Officially_Verified', 'Expired', 'Resolved'] as const;
const COMMUNITY = new Set<string>(['Unverified', 'Corroborated', 'Disputed']);
const reportArb: fc.Arbitrary<ReportMarkerInput> = fc.record(
  {
    report_id: fc.string({ minLength: 1, maxLength: 8 }),
    category: fc.constantFrom('flooding', 'blocked road', 'debris', 'other'),
    verification_status: fc.constantFrom(...STATUSES),
    lng: fc.oneof({ weight: 9, arbitrary: lng }, { weight: 1, arbitrary: fc.constant(Number.NaN) }),
    lat,
    description: fc.option(word, { nil: null }),
    source_label: fc.option(word, { nil: null }),
    simulated: fc.boolean(),
    confirmations: fc.nat(5),
    disputes: fc.nat(5),
    created_at: fc.option(fc.constant('2025-01-01T00:00:00Z'), { nil: null }),
    confirmed_closure: fc.boolean(),
  },
  { requiredKeys: ['report_id', 'category', 'verification_status', 'lng', 'lat'] },
);

const ring = coord.map(([x, y]): LngLat[] => [[x, y], [x + 0.001, y], [x + 0.001, y + 0.001], [x, y]]);
const polyFeat = (props: fc.Arbitrary<Record<string, unknown>>): fc.Arbitrary<Feat> =>
  fc.record({ r: ring, p: props, nogeom: fc.boolean() }).map(({ r, p, nogeom }) => ({
    type: 'Feature' as const,
    geometry: nogeom ? (null as unknown as Feat['geometry']) : { type: 'Polygon' as const, coordinates: [r] },
    properties: p,
  }));
const pointFeat = (props: fc.Arbitrary<Record<string, unknown>>): fc.Arbitrary<Feat> =>
  fc.record({ c: coord, p: props }).map(({ c, p }) => ({ type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: c }, properties: p }));
const fcArb = (f: fc.Arbitrary<Feat>) => fc.array(f, { maxLength: 4 }).map(collection);

const boundaryArb = fcArb(polyFeat(fc.record({ name: word, simulation_flag: fc.boolean(), simulated: fc.boolean() }, { requiredKeys: [] })));
const stationArb = fcArb(pointFeat(fc.record({ name: word, source: word, wheelchair_boarding: fc.constantFrom(0, 1, 2) }, { requiredKeys: [] })));
const simStationArb = fcArb(pointFeat(fc.record({ name: fc.oneof(word, fc.constant('SIMULATED platform')) }, { requiredKeys: [] })));
const buildingsArb = fcArb(polyFeat(fc.record({
  building_height_class: fc.constantFrom('Low-rise', 'Mid-rise', 'High-rise', 'Unknown', 'Skyscraper'),
  height_m: fc.nat(300),
}, { requiredKeys: [] })));

const destArb: fc.Arbitrary<DestinationInput> = fc.record({
  id: fc.string({ minLength: 1, maxLength: 6 }),
  name: fc.string({ minLength: 1, maxLength: 10 }),
  lng, lat,
  simulated: fc.boolean(),
  source_record: fc.option(fc.record({
    is_official: fc.option(fc.boolean(), { nil: null }),
    source_id: fc.option(fc.string({ minLength: 1, maxLength: 6 }), { nil: null }),
  }), { nil: null }),
});

/** Inputs where the bug condition is false: no hazards, flood highlight, restrictions or graph. */
const inputsArb: fc.Arbitrary<MapLayerInputs> = fc.record(
  {
    routes: fc.array(routeArb, { maxLength: 4 }),
    reports: fc.array(reportArb, { maxLength: 8 }),
    incidentBoundaries: fc.option(boundaryArb, { nil: null }),
    start: fc.option(coord, { nil: null }),
    destination: fc.option(destArb, { nil: null }),
    verifiedDestinations: fc.array(destArb, { maxLength: 4 }),
  },
  { requiredKeys: [] },
);

const DEPTH = /depth/i;
const allFeatures = (f: ReturnType<typeof fakeMap>, ids: string[]) => ids.flatMap((id) => data(f, id).features);
const PRESERVED_SOURCES = ['routes', 'reports', 'incidents', 'markers', 'verified', 'closures'];
const ARTIFACT_SOURCES = ['buildings', 'cta-stations', 'simulated-station'];

function render(inputs: MapLayerInputs) {
  const f = fakeMap();
  const m = new MapLayers(f.map);
  m.init();
  m.setInputs(inputs);
  return { f, m };
}

// ------------------------------------------------------------------ properties
describe('Property 6: non-hazard sources equal the pre-fix builders', () => {
  it('routes, reports, incidents, markers, verified and report-derived closure points match', () => {
    fc.assert(
      fc.property(inputsArb, (inputs) => {
        const { f } = render(inputs);
        const rep = refReports(inputs.reports);
        expect(data(f, 'routes')).toEqual(refRoutes(inputs.routes));
        expect(data(f, 'reports')).toEqual(rep.reports);
        expect(data(f, 'incidents')).toEqual(refBoundaries(inputs.incidentBoundaries));
        expect(data(f, 'markers')).toEqual(refMarkers(inputs.start, inputs.destination));
        expect(data(f, 'verified')).toEqual(refVerified(inputs.verifiedDestinations));
        expect(data(f, 'closures')).toEqual(collection(rep.closurePoints));
      }),
      { numRuns: 200 },
    );
  });

  it('buildings, CTA stations and the SIMULATED station match', () => {
    fc.assert(
      fc.property(buildingsArb, stationArb, simStationArb, (buildings, stations, sim) => {
        const { f, m } = render({});
        m.setArtifactLayer('buildings', buildings);
        m.setArtifactLayer('cta_stations', stations);
        m.setArtifactLayer('simulated_station', sim);
        expect(data(f, 'buildings')).toEqual(buildings);
        expect(data(f, 'cta-stations')).toEqual(refStations(stations));
        expect(data(f, 'simulated-station')).toEqual(refSimStation(sim));
      }),
      { numRuns: 100 },
    );
  });

  it('community reports are never labeled officially verified; no star without an official source', () => {
    fc.assert(
      fc.property(inputsArb, (inputs) => {
        const { f } = render(inputs);
        for (const r of data(f, 'reports').features) {
          const p = r.properties!;
          if (COMMUNITY.has(String(p.verification_status))) {
            expect(`${p.label} ${p.status_text}`.toLowerCase()).not.toContain('officially verified');
            expect(p.layer).toBe('reports');
          }
        }
        const official = new Set(
          (inputs.verifiedDestinations ?? []).filter((d) => d.simulated !== true && d.source_record?.is_official === true).map((d) => d.id),
        );
        for (const v of data(f, 'verified').features) expect(official.has(String(v.properties?.destination_id))).toBe(true);
        if (official.size === 0) expect(data(f, 'verified').features).toEqual([]);
        // a SIMULATED destination is never a verified star and never presented as a shelter
        for (const mk of data(f, 'markers').features) {
          if (mk.properties?.feature_kind === 'destination' && mk.properties.simulated === true) {
            expect(mk.properties.label).toBe(SIM_DEST_LABEL);
          }
        }
      }),
      { numRuns: 200 },
    );
  });

  it('SIMULATED items keep SIMULATED labels and no built feature carries depth keys', () => {
    fc.assert(
      fc.property(inputsArb, stationArb, simStationArb, (inputs, stations, sim) => {
        const { f, m } = render(inputs);
        m.setArtifactLayer('cta_stations', stations);
        m.setArtifactLayer('simulated_station', sim);
        for (const feat of allFeatures(f, [...PRESERVED_SOURCES, ...ARTIFACT_SOURCES])) {
          const p = feat.properties ?? {};
          expect(Object.keys(p).some((k) => DEPTH.test(k))).toBe(false);
          if (p.simulated === true) expect(String(p.label)).toMatch(/^SIMULAT(ED|ION)\b/);
        }
      }),
      { numRuns: 100 },
    );
  });

  it('nothing incident-dependent is drawn when no incident data is given (3.8)', () => {
    const { f } = render({});
    for (const id of ['incidents', 'closures', 'flood', 'debris', 'fire', 'restrictions']) {
      expect(data(f, id).features).toEqual([]);
    }
  });
});

describe('golden examples (frozen from the unfixed builders)', () => {
  it('route, reports, boundary, markers and verified destination', () => {
    const { f } = render({
      routes: [
        { id: 'r1', kind: 'primary', coordinates: [[-87.63, 41.88], [-87.62, 41.881]] },
        { id: 'r2', kind: 'alternative', coordinates: [[-87.63, 41.88], [-87.625, 41.879]], properties: { distance_meters: 10 } },
        { id: 'r3', kind: 'alternative', coordinates: [[-87.63, 41.88]] },
      ],
      reports: [
        { report_id: 'u', category: 'flooding', verification_status: 'Unverified', lng: -87.6, lat: 41.9 },
        { report_id: 'c', category: 'debris', verification_status: 'Corroborated', lng: -87.61, lat: 41.9, simulated: true, confirmations: 2 },
        { report_id: 'd', category: 'blocked road', verification_status: 'Disputed', lng: -87.62, lat: 41.9, description: 'x' },
        { report_id: 'k', category: 'blocked road', verification_status: 'Officially_Verified', confirmed_closure: true, lng: -87.63, lat: 41.9 },
        { report_id: 'e', category: 'other', verification_status: 'Expired', lng: -87.6, lat: 41.9 },
      ],
      incidentBoundaries: collection([{
        type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
        properties: { name: 'SIMULATED tornado warning area', simulation_flag: true },
      }]),
      start: [-87.628, 41.8882],
      destination: { id: 'dst', name: 'Point A', lng: -87.6233, lat: 41.8827, simulated: true },
      verifiedDestinations: [
        { id: 'v1', name: 'Hall', lng: -87.6, lat: 41.9, simulated: false, source_record: { is_official: true, source_id: 'S1' } },
        { id: 'v2', name: 'Sim', lng: -87.6, lat: 41.9, simulated: true, source_record: { is_official: true } },
        { id: 'v3', name: 'Community', lng: -87.6, lat: 41.9, simulated: false, source_record: { is_official: false } },
      ],
    });
    expect(data(f, 'routes').features.map((x) => x.properties)).toEqual([
      { layer: 'routes', feature_kind: 'route', route_id: 'r1', kind: 'primary', label: 'Algorithm-generated route' },
      { distance_meters: 10, layer: 'routes', feature_kind: 'route', route_id: 'r2', kind: 'alternative', label: 'Alternative route' },
    ]);
    expect(data(f, 'reports').features.map((x) => [x.properties?.icon, x.properties?.label, x.properties?.description])).toEqual([
      ['icon-report-unverified', 'Unverified: flooding', 'Unknown'],
      ['icon-report-corroborated', 'SIMULATED Corroborated: debris', 'Unknown'],
      ['icon-report-disputed', 'Disputed: blocked road', 'x'],
    ]);
    expect(data(f, 'closures').features).toEqual([{
      type: 'Feature', geometry: { type: 'Point', coordinates: [-87.63, 41.9] },
      properties: {
        layer: 'closures', feature_kind: 'report', report_id: 'k', category: 'blocked road',
        verification_status: 'Officially_Verified', status_text: 'Officially verified closure', description: 'Unknown',
        source_label: 'Community report', simulated: false, confirmations: 0, disputes: 0, created_at: 'Unknown',
        icon: 'icon-closure', label: 'Officially verified closure: blocked road',
      },
    }]);
    expect(data(f, 'incidents').features[0].properties).toEqual({
      name: 'SIMULATED tornado warning area', simulation_flag: true, layer: 'incidents',
      feature_kind: 'incident_boundary', simulated: true, label: 'SIMULATED SIMULATED tornado warning area',
    });
    expect(data(f, 'markers').features.map((x) => x.properties)).toEqual([
      { layer: 'markers', feature_kind: 'start', icon: 'icon-start', label: 'Start' },
      { layer: 'markers', feature_kind: 'destination', destination_id: 'dst', name: 'Point A', simulated: true,
        icon: 'icon-destination-simulated', label: SIM_DEST_LABEL },
    ]);
    expect(data(f, 'verified').features).toEqual([{
      type: 'Feature', geometry: { type: 'Point', coordinates: [-87.6, 41.9] },
      properties: { layer: 'verified', feature_kind: 'verified_destination', destination_id: 'v1', name: 'Hall', source_record_id: 'S1', label: 'Hall' },
    }]);
  });

  it('artifact layers', () => {
    const { f, m } = render({});
    const pt = (p: Record<string, unknown>): Feat => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [-87.63, 41.88] }, properties: p });
    m.setArtifactLayer('cta_stations', collection([pt({ name: 'Quincy', wheelchair_boarding: 1 }), pt({})]));
    m.setArtifactLayer('simulated_station', collection([pt({ name: 'Clark/Lake' }), pt({ name: 'SIMULATED platform' })]));
    expect(data(f, 'cta-stations').features.map((x) => x.properties)).toEqual([
      { name: 'Quincy', wheelchair_boarding: 1, layer: 'cta-stations', feature_kind: 'cta_station', label: 'Quincy', source: 'CTA GTFS static feed', entrance_safety: 'Unknown' },
      { layer: 'cta-stations', feature_kind: 'cta_station', label: 'Unknown', name: 'Unknown', source: 'CTA GTFS static feed', wheelchair_boarding: 'Unknown', entrance_safety: 'Unknown' },
    ]);
    expect(data(f, 'simulated-station').features.map((x) => x.properties?.label)).toEqual(['SIMULATED Clark/Lake', 'SIMULATED platform']);
  });
});

describe('basemap appearance keeps every data layer visible (3.7, 3.12)', () => {
  const kinds: BasemapKind[] = ['raster', 'vector', 'none']; // raster covers imagery/satellite-style basemaps
  it('appearance and basemap paint changes never hide, remove or re-add data layers or change their data', () => {
    fc.assert(
      fc.property(
        inputsArb,
        fc.array(fc.tuple(fc.constantFrom<Appearance>('light', 'dark'), fc.constantFrom(...kinds)), { minLength: 1, maxLength: 6 }),
        (inputs, switches) => {
          const { f, m } = render(inputs);
          const before = Object.fromEntries([...f.sources.entries()].map(([k, v]) => [k, JSON.stringify(v.data)]));
          const added = f.map.addLayer.mock.calls.length;
          f.map.setLayoutProperty.mockClear();
          for (const [appearance, kind] of switches) {
            m.setAppearance(appearance);
            applyBasemapAppearance(f.map, appearance, kind);
          }
          expect(f.map.setLayoutProperty).not.toHaveBeenCalled();
          expect(f.map.removeLayer).not.toHaveBeenCalled();
          expect(f.map.removeSource).not.toHaveBeenCalled();
          expect(f.map.addLayer.mock.calls.length).toBe(added);
          expect(f.layers.size).toBe(LAYER_DEFS.length);
          for (const { toggle, layer } of LAYER_DEFS) {
            const def = TOGGLES.find((t) => t.id === toggle)!;
            const vis = (f.layers.get(layer.id)!.layout as Record<string, string>).visibility;
            expect(vis).toBe(def.defaultVisible ? 'visible' : 'none');
            if (def.defaultVisible) expect(m.isVisible(toggle)).toBe(true);
          }
          for (const [k, v] of f.sources.entries()) expect(JSON.stringify(v.data)).toBe(before[k]);
        },
      ),
      { numRuns: 50 },
    );
  });

  it('every preserved data toggle is on by default', () => {
    for (const id of ['routes', 'markers', 'closures', 'reports', 'hazards', 'incidents', 'verified', 'transit', 'simulated_station', 'buildings']) {
      expect(TOGGLES.find((t) => t.id === id)?.defaultVisible).toBe(true);
    }
  });
});

describe('visibleLegend(visible) without presence keeps toggle-only behavior', () => {
  // Frozen [entry id, toggle] in legend order, excluding `elevation` and `graph` (removed by the fix).
  const FROZEN: [string, string][] = [
    ['route-primary', 'routes'], ['route-alt', 'routes'], ['closure', 'closures'],
    ['report-unverified', 'reports'], ['report-corroborated', 'reports'], ['report-disputed', 'reports'],
    ['verified', 'verified'], ['hazard-flood', 'hazards'], ['hazard-debris', 'hazards'], ['hazard-fire', 'hazards'],
    ['incident', 'incidents'], ['transit', 'transit'], ['sim-station', 'simulated_station'],
    ['height-low', 'buildings'], ['height-mid', 'buildings'], ['height-high', 'buildings'], ['height-unknown', 'buildings'],
    ['start', 'markers'], ['destination', 'markers'],
  ];
  const REMOVED = new Set(['elevation', 'graph']);
  const toggleIds = [...new Set(FROZEN.map(([, t]) => t)), 'elevation', 'graph'];

  it('lists exactly the frozen entries whose toggle is not false, in order', () => {
    fc.assert(
      fc.property(
        fc.dictionary(fc.constantFrom(...toggleIds), fc.boolean()),
        (visible) => {
          const got = visibleLegend(visible).filter((e) => !REMOVED.has(e.toggle)).map((e) => [e.id, e.toggle]);
          expect(got).toEqual(FROZEN.filter(([, t]) => visible[t] !== false));
        },
      ),
      { numRuns: 200 },
    );
  });

  it('labels of preserved non-flood entries are unchanged', () => {
    const all = Object.fromEntries(toggleIds.map((t) => [t, true]));
    const labels = Object.fromEntries(visibleLegend(all).map((e) => [e.id, e.label]));
    expect(labels).toMatchObject({
      'route-primary': 'Recommended route (algorithm-generated)',
      'route-alt': 'Alternative route (dashed)',
      'report-unverified': 'Unverified report (triangle)',
      'report-corroborated': 'Corroborated report (diamond)',
      'report-disputed': 'Disputed report (hollow square)',
      verified: 'Verified destination with official source (star)',
      'hazard-debris': 'SIMULATED debris zone (dotted outline)',
      'hazard-fire': 'SIMULATED fire perimeter (dash-dot outline)',
      incident: 'Incident boundary (dotted yellow)',
      transit: 'CTA station (GTFS data only)',
      'sim-station': 'SIMULATED station (hexagon)',
      'height-unknown': 'Height Unknown (dashed outline)',
      start: 'Start',
      destination: SIM_DEST_LABEL,
    });
    expect(String(labels['hazard-flood'])).toMatch(/^SIMULATED flood area/);
  });
});
