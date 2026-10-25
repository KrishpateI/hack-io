/**
 * Basemap paint per Appearance (ios-style-ui-redesign Req 5.6, 5.8, 5.9).
 *
 * Paint-only: switching Appearance never changes the basemap source, URL,
 * attribution, or layer set, so no tile is refetched and the cached PMTiles
 * file keeps serving offline.
 */
import type { Appearance } from '../styles/tokens';

export type BasemapKind = 'raster' | 'vector' | 'none';

export type BasemapPaint = Record<string, Record<string, string | number>>;

const DARK_VECTOR: BasemapPaint = {
  background: { 'background-color': '#0b1220' },
  water: { 'fill-color': '#16324f' },
  roads: { 'line-color': '#3a4a6e' },
};

const LIGHT_VECTOR: BasemapPaint = {
  background: { 'background-color': '#f2f2f7' },
  water: { 'fill-color': '#aad3df' },
  roads: { 'line-color': '#b8bcc8' },
};

const DARK_RASTER = { 'raster-brightness-max': 0.55, 'raster-saturation': -0.4, 'raster-contrast': 0.1 };
const LIGHT_RASTER = { 'raster-brightness-max': 1, 'raster-saturation': 0, 'raster-contrast': 0 };

/** Map from basemap layer id to the paint properties for the Appearance. */
export function basemapPaint(appearance: Appearance, kind: BasemapKind): BasemapPaint {
  const light = appearance === 'light';
  const base = light ? LIGHT_VECTOR : DARK_VECTOR;
  if (kind === 'vector') return { background: { ...base.background }, water: { ...base.water }, roads: { ...base.roads } };
  if (kind === 'raster') return { background: { ...base.background }, basemap: { ...(light ? LIGHT_RASTER : DARK_RASTER) } };
  return { background: { ...base.background } };
}

export interface BasemapAppearanceMap {
  getLayer?: (id: string) => unknown;
  setPaintProperty?: (layer: string, prop: string, value: unknown) => void;
  isStyleLoaded?: () => boolean;
  once?: (event: 'load', cb: () => void) => void;
}

/**
 * Applies the basemap paint for the Appearance, skipping missing layers. When
 * the style is not loaded yet, defers to the map `load` event.
 */
export function applyBasemapAppearance(map: BasemapAppearanceMap, appearance: Appearance, kind: BasemapKind): void {
  if (!map.setPaintProperty) return;
  if (map.isStyleLoaded && !map.isStyleLoaded()) {
    map.once?.('load', () => applyBasemapAppearance(map, appearance, kind));
    return;
  }
  for (const [layer, props] of Object.entries(basemapPaint(appearance, kind))) {
    if (map.getLayer && !map.getLayer(layer)) continue;
    for (const [prop, value] of Object.entries(props)) {
      try {
        map.setPaintProperty(layer, prop, value);
      } catch {
        // Style not ready for this layer; the load handler or next call re-applies.
      }
    }
  }
}
