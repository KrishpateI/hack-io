import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest';

const h = vi.hoisted(() => {
  const layers = new Map<string, unknown>();
  const state = {
    options: null as Record<string, unknown> | null,
    map: null as {
      setLayoutProperty: ReturnType<typeof vi.fn>;
      setPaintProperty: ReturnType<typeof vi.fn>;
      setPadding: ReturnType<typeof vi.fn>;
      easeTo: ReturnType<typeof vi.fn>;
    } | null,
    constructed: 0,
    /** When set, the next Map constructor throws this message (no WebGL). */
    failWith: null as string | null,
    handlers: null as Record<string, (e?: unknown) => void> | null,
  };
  return { layers, state };
});

vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));
vi.mock('maplibre-gl', () => {
  class Map {
    handlers: Record<string, () => void> = {};
    constructor(opts: Record<string, unknown>) {
      if (h.state.failWith) throw new Error(h.state.failWith);
      h.state.options = opts;
      h.state.map = this;
      h.state.constructed += 1;
      queueMicrotask(() => this.handlers.load?.());
    }
    on(ev: string, fn: (e?: unknown) => void) { this.handlers[ev] = fn as () => void; h.state.handlers = this.handlers; }
    off(ev: string) { delete this.handlers[ev]; }
    queryRenderedFeatures() { return [{ layer: { id: 'buildings-fill' }, properties: { name: 'B' } }]; }
    addControl() {}
    getSource(id: string) { return h.layers.has('src:' + id) ? { setData() {} } : undefined; }
    addSource(id: string) { h.layers.set('src:' + id, true); }
    hasImage() { return false; }
    addImage() {}
    setGlyphs() {}
    addLayer(l: { id: string }) { h.layers.set(l.id, l); }
    getLayer(id: string) { return h.layers.get(id); }
    setLayoutProperty = vi.fn();
    setPaintProperty = vi.fn();
    setPadding = vi.fn();
    easeTo = vi.fn();
    remove() {}
  }
  class NavigationControl {}
  return { Map, NavigationControl, addProtocol: vi.fn() };
});

import { MapView } from './MapView';
import { buildBasemapStyle, OSM_RASTER_TILES } from './pmtiles';
import { basemapPaint } from './basemapAppearance';
import { EXTRACTION_BOUNDS, EXTRACTION_LABEL, extractionAreaFeature } from './extractionArea';
import { clearLayerCache } from './layers/layerData';
import { LABEL_PAINT } from './layers/MapLayers';
import { DATA_LAYER_GROUPS } from './layers/styles';

beforeEach(() => {
  h.layers.clear();
  h.state.options = null;
  h.state.map = null;
  h.state.constructed = 0;
  h.state.failWith = null;
  clearLayerCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Waits until the map's load handler has created the layers and handed the manager to React. */
async function mapReady() {
  await waitFor(() => expect(h.layers.has('extraction-area-line')).toBe(true));
  await waitFor(() => expect(h.layers.has('routes-primary')).toBe(true));
}

const backgroundColor = (appearance: 'dark' | 'light') =>
  (basemapPaint(appearance, 'none').background as Record<string, string>)['background-color'];

describe('MapView', () => {
  it('centers on Chicago, shows attribution, label and a dashed boundary', async () => {
    render(<MapView />);
    await waitFor(() => expect(h.layers.has('extraction-area-line')).toBe(true));
    const center = h.state.options!.center as [number, number];
    expect(center[1]).toBeCloseTo(41.88, 1);
    expect(screen.getByTestId('osm-attribution')).toHaveTextContent('OpenStreetMap');
    expect(screen.getByText(EXTRACTION_LABEL)).toBeInTheDocument();
    const layer = h.layers.get('extraction-area-line') as { paint: Record<string, unknown> };
    expect(layer.paint['line-dasharray']).toBeDefined();
  });

  it('reports the features under a click through onFeatureClick', async () => {
    const onFeatureClick = vi.fn();
    render(<MapView onFeatureClick={onFeatureClick} />);
    await waitFor(() => expect(h.layers.has('buildings-fill')).toBe(true));
    h.state.handlers!.click({ point: { x: 1, y: 2 }, lngLat: { lng: -87.63, lat: 41.88 } });
    expect(onFeatureClick).toHaveBeenCalledWith({
      lngLat: [-87.63, 41.88],
      hits: [{ layerId: 'buildings-fill', properties: { name: 'B' } }],
    });
  });

  it('no longer renders the layer controls or the details panel itself', async () => {
    render(<MapView />);
    await mapReady();
    expect(screen.queryByTestId('map-panel')).not.toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByTestId('map-legend')).not.toBeInTheDocument();
  });

  it('shows an alert and keeps the attribution when the map cannot start', async () => {
    h.state.failWith = 'WebGL2 is not supported';
    render(<MapView />);
    expect(await screen.findByTestId('map-error')).toHaveTextContent('WebGL2 is not supported');
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByTestId('osm-attribution')).toBeInTheDocument();
  });
});

describe('MapView layerVisibility (controlled)', () => {
  it('applies the initial visibility to the extraction layer and data layers', async () => {
    render(<MapView layerVisibility={{ extraction: false, routes: false }} />);
    await mapReady();
    const extraction = h.layers.get('extraction-area-line') as { layout: { visibility: string } };
    expect(extraction.layout.visibility).toBe('none');
    const routes = h.layers.get('routes-primary') as { layout: { visibility: string } };
    expect(routes.layout.visibility).toBe('none');
    const closures = h.layers.get(DATA_LAYER_GROUPS.closures[0]) as { layout: { visibility: string } };
    expect(closures.layout.visibility).toBe('visible');
    expect(screen.queryByTestId('extraction-label')).not.toBeInTheDocument();
  });

  it('changing the extraction entry sets the layer visibility and the label right away', async () => {
    const { rerender } = render(<MapView layerVisibility={{ extraction: true }} />);
    await mapReady();
    expect(screen.getByText(EXTRACTION_LABEL)).toBeInTheDocument();
    rerender(<MapView layerVisibility={{ extraction: false }} />);
    await waitFor(() =>
      expect(h.state.map!.setLayoutProperty).toHaveBeenCalledWith('extraction-area-line', 'visibility', 'none'),
    );
    expect(screen.queryByText(EXTRACTION_LABEL)).not.toBeInTheDocument();
    rerender(<MapView layerVisibility={{ extraction: true }} />);
    await waitFor(() =>
      expect(h.state.map!.setLayoutProperty).toHaveBeenCalledWith('extraction-area-line', 'visibility', 'visible'),
    );
    expect(screen.getByText(EXTRACTION_LABEL)).toBeInTheDocument();
  });

  it('changing a data toggle shows or hides every layer of that group', async () => {
    const { rerender } = render(<MapView layerVisibility={{ closures: true }} />);
    await mapReady();
    rerender(<MapView layerVisibility={{ closures: false }} />);
    for (const id of DATA_LAYER_GROUPS.closures) {
      await waitFor(() => expect(h.state.map!.setLayoutProperty).toHaveBeenCalledWith(id, 'visibility', 'none'));
    }
    rerender(<MapView layerVisibility={{ closures: true }} />);
    for (const id of DATA_LAYER_GROUPS.closures) {
      await waitFor(() => expect(h.state.map!.setLayoutProperty).toHaveBeenCalledWith(id, 'visibility', 'visible'));
    }
  });

  it('fetches an artifact layer only once its toggle is visible', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('offline');
    });
    vi.stubGlobal('fetch', fetchMock);
    const hidden = { transit: false, simulated_station: false, buildings: false };
    const { rerender } = render(<MapView layerVisibility={hidden} />);
    await mapReady();
    expect(fetchMock).not.toHaveBeenCalled();
    rerender(<MapView layerVisibility={{ ...hidden, transit: true }} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain('cta_stations');
  });
});

describe('MapView appearance', () => {
  it('builds the initial style for the Appearance and repaints in place on change', async () => {
    h.layers.set('background', {});
    const { rerender } = render(<MapView appearance="dark" />);
    await mapReady();
    const style = h.state.options!.style as { layers: { id: string; paint: Record<string, string> }[] };
    expect(style.layers[0].id).toBe('background');
    expect(style.layers[0].paint['background-color']).toBe(backgroundColor('dark'));
    await waitFor(() =>
      expect(h.state.map!.setPaintProperty).toHaveBeenCalledWith('background', 'background-color', backgroundColor('dark')),
    );
    expect(h.state.map!.setPaintProperty).toHaveBeenCalledWith('fire-label', 'text-color', LABEL_PAINT.dark.text);

    rerender(<MapView appearance="light" />);
    await waitFor(() =>
      expect(h.state.map!.setPaintProperty).toHaveBeenCalledWith('background', 'background-color', backgroundColor('light')),
    );
    expect(h.state.map!.setPaintProperty).toHaveBeenCalledWith('fire-label', 'text-color', LABEL_PAINT.light.text);
    expect(h.state.map!.setPaintProperty).toHaveBeenCalledWith('fire-label', 'text-halo-color', LABEL_PAINT.light.halo);
    // Paint-only: the map and its style are never rebuilt.
    expect(h.state.constructed).toBe(1);
  });

  it('starts with the Light style when the Appearance is light', async () => {
    render(<MapView appearance="light" />);
    await mapReady();
    const style = h.state.options!.style as { layers: { paint: Record<string, string> }[] };
    expect(style.layers[0].paint['background-color']).toBe(backgroundColor('light'));
  });
});

describe('MapView viewPadding', () => {
  const stubReducedMotion = (reduce: boolean) =>
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: reduce && q.includes('prefers-reduced-motion'),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));

  it('applies the first padding instantly and eases later changes', async () => {
    stubReducedMotion(false);
    const { rerender } = render(<MapView viewPadding={{ top: 10, left: 320 }} />);
    await mapReady();
    await waitFor(() =>
      expect(h.state.map!.setPadding).toHaveBeenCalledWith({ top: 10, right: 0, bottom: 0, left: 320 }),
    );
    expect(h.state.map!.easeTo).not.toHaveBeenCalled();
    // The extraction chip stays inside the visible area.
    const left = screen.getByTestId('extraction-label').style.left;
    expect(left).toContain('320px');
    expect(left).toContain('0.5rem');

    rerender(<MapView viewPadding={{ top: 10, left: 320, bottom: 300 }} />);
    await waitFor(() =>
      expect(h.state.map!.easeTo).toHaveBeenCalledWith({
        padding: { top: 10, right: 0, bottom: 300, left: 320 },
        duration: 250,
      }),
    );
  });

  it('changes padding without animation when reduced motion is requested', async () => {
    stubReducedMotion(true);
    const { rerender } = render(<MapView viewPadding={{ left: 100 }} />);
    await mapReady();
    await waitFor(() => expect(h.state.map!.setPadding).toHaveBeenCalled());
    rerender(<MapView viewPadding={{ left: 200 }} />);
    await waitFor(() =>
      expect(h.state.map!.setPadding).toHaveBeenLastCalledWith({ top: 0, right: 0, bottom: 0, left: 200 }),
    );
    expect(h.state.map!.easeTo).not.toHaveBeenCalled();
  });

  it('positions the attribution with --map-attribution-bottom', async () => {
    render(<MapView />);
    await mapReady();
    expect(screen.getByTestId('osm-attribution').style.bottom).toBe('var(--map-attribution-bottom, 0px)');
  });
});

describe('extraction area', () => {
  it('uses the required bounds', () => {
    const ring = extractionAreaFeature().geometry.coordinates[0];
    expect(EXTRACTION_BOUNDS).toEqual({ north: 41.91, south: 41.85, west: -87.68, east: -87.6 });
    expect(ring[0]).toEqual([-87.68, 41.85]);
    expect(ring[2]).toEqual([-87.6, 41.91]);
  });
});

describe('buildBasemapStyle', () => {
  it('uses PMTiles when present, even in dev', () => {
    const r = buildBasemapStyle({ pmtilesUrl: '/c.pmtiles', isDev: true, pmtilesPresent: true });
    expect(r.source).toBe('pmtiles');
  });
  it('falls back to OSM raster only in dev without PMTiles', () => {
    const dev = buildBasemapStyle({ isDev: true, pmtilesPresent: false });
    expect(dev.source).toBe('osm-dev-fallback');
    expect(JSON.stringify(dev.style)).toContain(OSM_RASTER_TILES[0]);
    const prod = buildBasemapStyle({ isDev: false, pmtilesPresent: false });
    expect(prod.source).toBe('none');
    expect(JSON.stringify(prod.style)).not.toContain('openstreetmap.org/');
  });
});

describe('buildBasemapStyle Appearance', () => {
  const configs = [
    { name: 'raster pmtiles', cfg: { pmtilesUrl: '/c.pmtiles', pmtilesKind: 'raster', isDev: false, pmtilesPresent: true } },
    { name: 'vector pmtiles', cfg: { pmtilesUrl: '/c.pmtiles', pmtilesKind: 'vector', isDev: false, pmtilesPresent: true } },
    { name: 'dev fallback', cfg: { isDev: true, pmtilesPresent: false } },
    { name: 'no basemap', cfg: { isDev: false, pmtilesPresent: false } },
  ] as const;

  it('defaults to dark', () => {
    const cfg = { isDev: false, pmtilesPresent: false };
    expect(buildBasemapStyle(cfg)).toEqual(buildBasemapStyle(cfg, 'dark'));
  });

  it.each(configs)('$name: Light and Dark differ only in paint, never in source, URL, or attribution', ({ cfg }) => {
    const dark = buildBasemapStyle(cfg, 'dark');
    const light = buildBasemapStyle(cfg, 'light');
    expect(light.source).toBe(dark.source);
    expect(light.style.sources).toEqual(dark.style.sources);
    expect(light.style.layers.map((l) => l.id)).toEqual(dark.style.layers.map((l) => l.id));
    expect(light.style.layers[0].id).toBe('background');
    expect((light.style.layers[0] as unknown as { paint: Record<string, string> }).paint['background-color']).toBe(backgroundColor('light'));
    expect((dark.style.layers[0] as unknown as { paint: Record<string, string> }).paint['background-color']).toBe(backgroundColor('dark'));
    expect(JSON.stringify(light.style)).not.toBe(JSON.stringify(dark.style));
  });

  it('keeps OpenStreetMap attribution in both Appearances', () => {
    for (const appearance of ['dark', 'light'] as const) {
      const r = buildBasemapStyle({ pmtilesUrl: '/c.pmtiles', pmtilesKind: 'raster', isDev: false, pmtilesPresent: true }, appearance);
      expect(JSON.stringify(r.style.sources)).toContain('OpenStreetMap');
    }
  });

  it('paints the vector water and roads for the Appearance', () => {
    const cfg = { pmtilesUrl: '/c.pmtiles', pmtilesKind: 'vector', isDev: false, pmtilesPresent: true } as const;
    const water = (a: 'dark' | 'light') =>
      (buildBasemapStyle(cfg, a).style.layers.find((l) => l.id === 'water') as unknown as { paint: Record<string, string> }).paint['fill-color'];
    expect(water('light')).toBe(basemapPaint('light', 'vector').water['fill-color']);
    expect(water('dark')).toBe(basemapPaint('dark', 'vector').water['fill-color']);
    expect(water('light')).not.toBe(water('dark'));
  });
});
