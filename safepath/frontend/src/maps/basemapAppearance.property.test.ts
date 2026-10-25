// Feature: ios-style-ui-redesign, Property 11: Appearance never changes the basemap source or layer set
// **Validates: Requirements 5.6, 5.8, 5.9, 3.5**
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { Appearance } from '../styles/tokens';
import {
  applyBasemapAppearance,
  basemapPaint,
  type BasemapAppearanceMap,
  type BasemapKind,
} from './basemapAppearance';
import { buildBasemapStyle, type BasemapConfig } from './pmtiles';

const APPEARANCES: Appearance[] = ['light', 'dark'];

const configArb: fc.Arbitrary<BasemapConfig> = fc.record({
  pmtilesUrl: fc.option(fc.constantFrom('/tiles/chicago.pmtiles', 'https://example.com/a.pmtiles', ''), { nil: undefined }),
  pmtilesKind: fc.option(fc.constantFrom('raster' as const, 'vector' as const), { nil: undefined }),
  isDev: fc.boolean(),
  pmtilesPresent: fc.boolean(),
});

/** Everything about a style except layer paint. */
function structure(cfg: BasemapConfig, appearance: Appearance) {
  const { style, source } = buildBasemapStyle(cfg, appearance);
  return {
    source,
    version: style.version,
    sources: style.sources,
    layers: style.layers.map((l) => ({
      id: l.id,
      type: l.type,
      source: 'source' in l ? l.source : undefined,
      sourceLayer: 'source-layer' in l ? l['source-layer'] : undefined,
    })),
  };
}

/** Fake map recording every mutation call. */
function fakeMap(layerIds: string[], styleLoaded = true) {
  const calls: { method: string; args: unknown[] }[] = [];
  const loadCbs: (() => void)[] = [];
  const map: BasemapAppearanceMap & Record<string, unknown> = {
    getLayer: (id: string) => (layerIds.includes(id) ? { id } : undefined),
    setPaintProperty: (layer: string, prop: string, value: unknown) => {
      calls.push({ method: 'setPaintProperty', args: [layer, prop, value] });
    },
    isStyleLoaded: () => styleLoaded,
    once: (_event: 'load', cb: () => void) => {
      loadCbs.push(cb);
    },
    // Anything that would alter source/layer set must never be called.
    addLayer: () => calls.push({ method: 'addLayer', args: [] }),
    removeLayer: () => calls.push({ method: 'removeLayer', args: [] }),
    addSource: () => calls.push({ method: 'addSource', args: [] }),
    removeSource: () => calls.push({ method: 'removeSource', args: [] }),
    setStyle: () => calls.push({ method: 'setStyle', args: [] }),
  };
  return { map, calls, loadCbs };
}

describe('Property 11: Appearance never changes the basemap source or layer set', () => {
  it('buildBasemapStyle yields identical sources and layer set for both appearances', () => {
    fc.assert(
      fc.property(configArb, (cfg) => {
        expect(structure(cfg, 'light')).toEqual(structure(cfg, 'dark'));
      }),
      { numRuns: 100 },
    );
  });

  it('applyBasemapAppearance only calls setPaintProperty, on existing layers, with basemapPaint values', () => {
    const kindArb = fc.constantFrom<BasemapKind>('raster', 'vector', 'none');
    fc.assert(
      fc.property(
        fc.constantFrom(...APPEARANCES),
        kindArb,
        fc.subarray(['background', 'water', 'roads', 'basemap']),
        (appearance, kind, present) => {
          const { map, calls } = fakeMap(present);
          applyBasemapAppearance(map, appearance, kind);
          expect(calls.every((c) => c.method === 'setPaintProperty')).toBe(true);
          const expected = Object.entries(basemapPaint(appearance, kind))
            .filter(([layer]) => present.includes(layer))
            .flatMap(([layer, props]) => Object.entries(props).map(([p, v]) => [layer, p, v]));
          expect(calls.map((c) => c.args)).toEqual(expected);
        },
      ),
      { numRuns: 100 },
    );
  });
});

describe('basemapPaint examples', () => {
  it('returns Dark vector paint', () => {
    expect(basemapPaint('dark', 'vector')).toEqual({
      background: { 'background-color': '#0b1220' },
      water: { 'fill-color': '#16324f' },
      roads: { 'line-color': '#3a4a6e' },
    });
  });

  it('returns Light vector paint', () => {
    expect(basemapPaint('light', 'vector')).toEqual({
      background: { 'background-color': '#f2f2f7' },
      water: { 'fill-color': '#aad3df' },
      roads: { 'line-color': '#b8bcc8' },
    });
  });

  it('returns raster paint for the basemap layer per appearance', () => {
    expect(basemapPaint('light', 'raster').basemap).toEqual({
      'raster-brightness-max': 1,
      'raster-saturation': 0,
      'raster-contrast': 0,
    });
    expect(basemapPaint('dark', 'raster').basemap).toEqual({
      'raster-brightness-max': 0.55,
      'raster-saturation': -0.4,
      'raster-contrast': 0.1,
    });
  });

  it('returns only background paint for kind none', () => {
    expect(Object.keys(basemapPaint('light', 'none'))).toEqual(['background']);
    expect(Object.keys(basemapPaint('dark', 'none'))).toEqual(['background']);
  });
});

describe('applyBasemapAppearance examples', () => {
  it('sets paint on existing layers only and skips missing ones', () => {
    const { map, calls } = fakeMap(['background', 'water']);
    applyBasemapAppearance(map, 'light', 'vector');
    expect(calls).toEqual([
      { method: 'setPaintProperty', args: ['background', 'background-color', '#f2f2f7'] },
      { method: 'setPaintProperty', args: ['water', 'fill-color', '#aad3df'] },
    ]);
  });

  it('defers to the load event when the style is not loaded', () => {
    const { map, calls, loadCbs } = fakeMap(['background'], false);
    applyBasemapAppearance(map, 'dark', 'none');
    expect(calls).toHaveLength(0);
    expect(loadCbs).toHaveLength(1);
  });

  it('does nothing when the map has no setPaintProperty', () => {
    expect(() => applyBasemapAppearance({}, 'dark', 'vector')).not.toThrow();
  });

  it('swallows errors thrown by setPaintProperty', () => {
    const map: BasemapAppearanceMap = {
      setPaintProperty: () => {
        throw new Error('style not ready');
      },
    };
    expect(() => applyBasemapAppearance(map, 'light', 'raster')).not.toThrow();
  });
});
