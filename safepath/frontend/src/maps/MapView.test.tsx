import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach } from 'vitest';

const h = vi.hoisted(() => {
  const layers = new Map<string, unknown>();
  const state = {
    options: null as Record<string, unknown> | null,
    setLayoutProperty: null as unknown,
  };
  return { layers, state };
});

vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));
vi.mock('maplibre-gl', () => {
  class Map {
    handlers: Record<string, () => void> = {};
    constructor(opts: Record<string, unknown>) {
      h.state.options = opts;
      h.state.setLayoutProperty = this.setLayoutProperty;
      queueMicrotask(() => this.handlers.load?.());
    }
    on(ev: string, fn: () => void) { this.handlers[ev] = fn; }
    addControl() {}
    getSource() { return h.layers.has('src') ? {} : undefined; }
    addSource() { h.layers.set('src', true); }
    addLayer(l: { id: string }) { h.layers.set(l.id, l); }
    getLayer(id: string) { return h.layers.get(id); }
    setLayoutProperty = vi.fn();
    remove() {}
  }
  class NavigationControl {}
  return { Map, NavigationControl, addProtocol: vi.fn() };
});

import { MapView } from './MapView';
import { buildBasemapStyle, OSM_RASTER_TILES } from './pmtiles';
import { EXTRACTION_BOUNDS, EXTRACTION_LABEL, extractionAreaFeature } from './extractionArea';

beforeEach(() => {
  h.layers.clear();
  h.state.options = null;
});

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

  it('toggling a layer sets visibility immediately', async () => {
    render(<MapView />);
    await waitFor(() => expect(h.layers.has('extraction-area-line')).toBe(true));
    await userEvent.click(screen.getByLabelText('Data extraction boundary'));
    expect(h.state.setLayoutProperty).toHaveBeenCalledWith('extraction-area-line', 'visibility', 'none');
    expect(screen.queryByText(EXTRACTION_LABEL)).not.toBeInTheDocument();
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
