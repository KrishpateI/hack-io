// Feature: map-legend-layers-fix, Property 3: legend lists exactly the entries with data and toggle on
// **Validates: Requirements 2.8, 2.9, 2.12**
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { MapLayers, type ArtifactLayerName, type MapLike } from './MapLayers';
import { LAYER_DEFS, LEGEND_ENTRIES, TOGGLES, legendPresence, visibleLegend } from './styles';
import { SOURCE_IDS, type ArtifactStatusMap, type FC, type Feat, type MapLayerInputs } from './types';

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
    addImage: (id) => { images.add(id); },
    hasImage: (id) => images.has(id),
  };
  return { map, features: (id: string): Feat[] => ((sources.get(id)?.data as FC | undefined)?.features ?? []) };
}

const coll = (...features: Feat[]): FC => ({ type: 'FeatureCollection', features });
const poly = (props: Record<string, unknown> = {}): Feat => ({
  type: 'Feature',
  geometry: { type: 'Polygon', coordinates: [[[-87.63, 41.88], [-87.62, 41.88], [-87.62, 41.89], [-87.63, 41.88]]] },
  properties: props,
});
const line = (props: Record<string, unknown> = {}): Feat => ({
  type: 'Feature', geometry: { type: 'LineString', coordinates: [[-87.63, 41.88], [-87.629, 41.881]] }, properties: props,
});
const point = (props: Record<string, unknown> = {}): Feat => ({
  type: 'Feature', geometry: { type: 'Point', coordinates: [-87.63, 41.88] }, properties: props,
});
const report = (status: string, confirmed = false) => ({
  report_id: `r-${status}`, category: 'blocked road', verification_status: status, lng: -87.63, lat: 41.88,
  confirmed_closure: confirmed,
});

const allOn = (): Record<string, boolean> => Object.fromEntries(TOGGLES.map((t) => [t.id, true]));
const ids = (visible: Record<string, boolean>, inputs: MapLayerInputs, status: ArtifactStatusMap = {}) =>
  visibleLegend(visible, legendPresence(inputs, status)).map((e) => e.id);

interface Case {
  entry: string;
  inputs?: MapLayerInputs;
  status?: ArtifactStatusMap;
  artifact?: [ArtifactLayerName, FC];
  source: string;
  /** Picks the feature that represents this entry in the source. */
  pick?: (f: Feat) => boolean;
}

const CASES: Case[] = [
  { entry: 'route-primary', source: 'routes', inputs: { routes: [{ id: 'p', kind: 'primary', coordinates: [[0, 0], [1, 1]] }] }, pick: (f) => f.properties?.kind === 'primary' },
  { entry: 'route-alt', source: 'routes', inputs: { routes: [{ id: 'a', kind: 'alternative', coordinates: [[0, 0], [1, 1]] }] }, pick: (f) => f.properties?.kind === 'alternative' },
  { entry: 'closure', source: 'restrictions', inputs: { restrictions: coll(line({ restriction_id: 'x', label: 'SIMULATED closed road segment (not a real closure)' })) } },
  { entry: 'closure', source: 'closures', inputs: { reports: [report('Officially_Verified', true)] } },
  { entry: 'report-unverified', source: 'reports', inputs: { reports: [report('Unverified')] } },
  { entry: 'report-corroborated', source: 'reports', inputs: { reports: [report('Corroborated')] } },
  { entry: 'report-disputed', source: 'reports', inputs: { reports: [report('Disputed')] } },
  {
    entry: 'verified', source: 'verified',
    inputs: { verifiedDestinations: [{ id: 'v', name: 'Hall', lng: -87.6, lat: 41.9, simulated: false, source_record: { is_official: true, source_id: 'S1' } }] },
  },
  { entry: 'hazard-flood', source: 'flood', inputs: { hazards: { flood: coll(poly()) } } },
  { entry: 'hazard-debris', source: 'debris', inputs: { hazards: { debris: coll(poly({ label: 'SIMULATED scenario area, not applied to routing' })) } } },
  { entry: 'hazard-fire', source: 'fire', inputs: { hazards: { fire: coll(poly()) } } },
  { entry: 'incident', source: 'incidents', inputs: { incidentBoundaries: coll(poly({ name: 'tornado area', simulation_flag: true })) } },
  { entry: 'transit', source: 'cta-stations', status: { cta_stations: { status: 'loaded', count: 1 } }, artifact: ['cta_stations', coll(point({ name: 'Quincy' }))] },
  { entry: 'sim-station', source: 'simulated-station', status: { simulated_station: { status: 'loaded', count: 1 } }, artifact: ['simulated_station', coll(point({ name: 'Clark/Lake' }))] },
  ...([['height-low', 'Low-rise'], ['height-mid', 'Mid-rise'], ['height-high', 'High-rise'], ['height-unknown', 'Unknown']] as const).map(
    ([entry, cls]): Case => ({
      entry, source: 'buildings',
      status: { buildings: { status: 'loaded', count: 1, classCounts: { [cls]: 1 } as Partial<Record<typeof cls, number>> } },
      artifact: ['buildings', coll(poly({ building_height_class: cls }))],
    }),
  ),
  { entry: 'start', source: 'markers', inputs: { start: [-87.63, 41.88] }, pick: (f) => f.properties?.feature_kind === 'start' },
  {
    entry: 'destination', source: 'markers', inputs: { destination: { id: 'd', name: 'Point A', lng: -87.62, lat: 41.88, simulated: true } },
    pick: (f) => f.properties?.feature_kind === 'destination',
  },
];

describe('each legend entry is listed exactly when its data is present and its toggle is on', () => {
  for (const c of CASES) {
    it(`${c.entry} (${c.source})`, () => {
      const entry = LEGEND_ENTRIES.find((e) => e.id === c.entry)!;
      const inputs = c.inputs ?? {};
      expect(ids(allOn(), inputs, c.status)).toEqual([c.entry]);
      expect(ids({ ...allOn(), [entry.toggle]: false }, inputs, c.status)).toEqual([]);
      // no data -> not listed even with every toggle on
      expect(ids(allOn(), {}, {})).toEqual([]);

      const f = fakeMap();
      const m = new MapLayers(f.map, allOn());
      m.init();
      m.setInputs(inputs);
      if (c.artifact) m.setArtifactLayer(c.artifact[0], c.artifact[1]);
      const drawn = f.features(c.source).filter(c.pick ?? (() => true));
      expect(drawn.length).toBeGreaterThan(0);
      // the source is drawn by a layer of the entry's toggle
      expect(LAYER_DEFS.some((d) => d.layer.source === c.source && d.toggle === entry.toggle)).toBe(true);
      for (const feat of drawn) {
        const p = feat.properties ?? {};
        expect(Object.keys(p).some((k) => /depth/i.test(k))).toBe(false);
        if (p.simulated === true) expect(String(p.label)).toMatch(/^SIMULAT(ED|ION)\b/);
      }
    });
  }

  it('hazard features keep SIMULATED labels and the merged flood label (H + K)', () => {
    const f = fakeMap();
    const m = new MapLayers(f.map);
    m.init();
    m.setInputs({ hazards: { flood: coll(poly({ water_depth_m: 1 })), debris: coll(poly({ label: 'SIMULATED scenario area, not applied to routing' })) } });
    expect(f.features('flood')[0].properties?.label).toBe('SIMULATED flood area (modeled low-lying streets, no depth values)');
    expect(f.features('flood')[0].properties).not.toHaveProperty('water_depth_m');
    expect(f.features('debris')[0].properties?.label).toBe('SIMULATED scenario area, not applied to routing');
    expect(LEGEND_ENTRIES.find((e) => e.id === 'hazard-flood')?.label).toBe('SIMULATED flood area (modeled low-lying streets, no depth values)');
  });

  it('G: no verified star without an official source record (3.3)', () => {
    const unofficial: MapLayerInputs = {
      verifiedDestinations: [
        { id: 's', name: 'Sim', lng: -87.6, lat: 41.9, simulated: true, source_record: { is_official: true } },
        { id: 'c', name: 'Community', lng: -87.6, lat: 41.9, simulated: false, source_record: { is_official: false } },
      ],
      destination: { id: 'v', name: 'Hall', lng: -87.6, lat: 41.9, simulated: false, source_record: { is_official: true } },
    };
    expect(legendPresence(unofficial, {}).verified).toBe(false);
    // an official destination is a star, not the simulation-destination marker
    expect(legendPresence(unofficial, {}).destination).toBe(false);
  });

  it('closure lines from the live reducer draw in the hatched restrictions source, not closures', () => {
    const f = fakeMap();
    const m = new MapLayers(f.map);
    m.init();
    m.setInputs({ closures: coll(line({ restriction_id: 'live' })) });
    expect(f.features('restrictions')).toHaveLength(1);
    expect(f.features('closures')).toEqual([]);
    expect(legendPresence({ closures: coll(line()) }, {}).closure).toBe(true);
  });

  it('buildings loaded with count > 0 and no class counts list all four height classes', () => {
    const listed = ids(allOn(), {}, { buildings: { status: 'loaded', count: 3 } });
    expect(listed).toEqual(['height-low', 'height-mid', 'height-high', 'height-unknown']);
    expect(ids(allOn(), {}, { buildings: { status: 'loaded', count: 0 } })).toEqual([]);
    for (const status of ['idle', 'loading', 'failed'] as const) {
      expect(ids(allOn(), {}, { buildings: { status, count: 3 }, cta_stations: { status }, simulated_station: { status } })).toEqual([]);
    }
  });

  it('K and U: no elevation or graph toggles, layers, sources or legend entries remain', () => {
    const removed = ['elevation', 'graph'];
    expect(TOGGLES.map((t) => t.id).filter((id) => removed.includes(id))).toEqual([]);
    expect(LEGEND_ENTRIES.filter((e) => removed.includes(e.id) || removed.includes(e.toggle))).toEqual([]);
    expect(LAYER_DEFS.filter((d) => /^(elevation|graph)-/.test(d.layer.id) || removed.includes(d.layer.source))).toEqual([]);
    expect((SOURCE_IDS as readonly string[]).filter((s) => removed.includes(s))).toEqual([]);
    expect(LEGEND_ENTRIES.map((e) => e.label).join('|')).not.toMatch(/street graph/i);
  });
});

describe('Property 3: visibleLegend(visible, presence)', () => {
  const toggleIds = [...TOGGLES.map((t) => t.id), 'elevation', 'graph'];
  const entryIds = [...LEGEND_ENTRIES.map((e) => e.id), 'elevation', 'graph'];

  it('returns exactly the entries with toggle not off and presence true, in LEGEND_ENTRIES order', () => {
    fc.assert(
      fc.property(
        fc.dictionary(fc.constantFrom(...toggleIds), fc.boolean()),
        fc.dictionary(fc.constantFrom(...entryIds), fc.boolean()),
        (visible, presence) => {
          const got = visibleLegend(visible, presence).map((e) => e.id);
          const expected = LEGEND_ENTRIES.filter((e) => visible[e.toggle] !== false && presence[e.id] === true).map((e) => e.id);
          expect(got).toEqual(expected);
          expect(got.some((id) => id === 'elevation' || id === 'graph')).toBe(false);
        },
      ),
      { numRuns: 300 },
    );
  });

  it('every entry listed from legendPresence has its data present and its toggle on', () => {
    const statusArb = fc.option(
      fc.record({ status: fc.constantFrom('idle' as const, 'loading' as const, 'loaded' as const, 'failed' as const), count: fc.option(fc.nat(3), { nil: undefined }) }),
      { nil: undefined },
    );
    const inputsArb: fc.Arbitrary<MapLayerInputs> = fc.record({
      routes: fc.array(fc.record({ id: fc.string(), kind: fc.constantFrom('primary' as const, 'alternative' as const), coordinates: fc.constant([[0, 0], [1, 1]] as [number, number][]) }), { maxLength: 2 }),
      reports: fc.array(fc.constantFrom('Unverified', 'Corroborated', 'Disputed', 'Expired').map((s) => report(s)), { maxLength: 3 }),
      hazards: fc.record({ flood: fc.option(fc.constant(coll(poly())), { nil: null }), fire: fc.option(fc.constant(coll()), { nil: null }) }),
      start: fc.option(fc.constant([-87.63, 41.88] as [number, number]), { nil: null }),
    }, { requiredKeys: [] });
    fc.assert(
      fc.property(
        inputsArb,
        fc.record({ buildings: statusArb, cta_stations: statusArb, simulated_station: statusArb }, { requiredKeys: [] }),
        fc.dictionary(fc.constantFrom(...TOGGLES.map((t) => t.id)), fc.boolean()),
        (inputs, status, visible) => {
          const presence = legendPresence(inputs, status as ArtifactStatusMap);
          for (const e of LEGEND_ENTRIES) expect(typeof presence[e.id]).toBe('boolean');
          for (const e of visibleLegend(visible, presence)) {
            expect(presence[e.id]).toBe(true);
            expect(visible[e.toggle]).not.toBe(false);
          }
        },
      ),
      { numRuns: 200 },
    );
  });
});
