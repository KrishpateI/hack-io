/**
 * Basemap style resolution and PMTiles protocol support (design D8, Req 3.8, 46.1).
 *
 * Tile policy: the production basemap is a self-hosted PMTiles extract of the
 * Chicago bounding box. The OSM tile usage policy
 * (https://operations.osmfoundation.org/policies/tiles/) forbids bulk
 * prefetching and heavy use of tile.openstreetmap.org, so the public OSM raster
 * tiles are used ONLY as a light-use fallback in development builds, and ONLY
 * when no PMTiles file is present. Production builds never request them.
 * OpenStreetMap attribution is always rendered (see MapView).
 */
import { Protocol } from 'pmtiles';
import type { StyleSpecification } from 'maplibre-gl';
import type { Appearance } from '../styles/tokens';
import { basemapPaint } from './basemapAppearance';

export const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';

export const OSM_RASTER_TILES = ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'];

export type PmtilesKind = 'raster' | 'vector';

export interface BasemapConfig {
  /** URL of the PMTiles file, or empty/undefined when none is configured. */
  pmtilesUrl?: string;
  pmtilesKind?: PmtilesKind;
  /** True only in development builds (import.meta.env.DEV). */
  isDev: boolean;
  /** Result of probing the PMTiles URL. */
  pmtilesPresent: boolean;
}

export type BasemapSource = 'pmtiles' | 'osm-dev-fallback' | 'none';

export interface BasemapResult {
  style: StyleSpecification;
  source: BasemapSource;
}


let protocol: Protocol | null = null;

/** Registers the `pmtiles://` protocol once on the given maplibre module. */
export function registerPmtilesProtocol(maplibre: {
  addProtocol: (name: string, handler: never) => void;
}): void {
  if (protocol) return;
  protocol = new Protocol();
  maplibre.addProtocol('pmtiles', protocol.tile as never);
}

/** Test helper. */
export function resetPmtilesProtocolForTests(): void {
  protocol = null;
}

/** Returns true when the PMTiles file can be fetched (HEAD/range probe). */
export async function probePmtiles(url: string | undefined, fetchFn: typeof fetch = fetch): Promise<boolean> {
  if (!url) return false;
  try {
    const res = await fetchFn(url, { headers: { Range: 'bytes=0-15' } });
    return res.ok || res.status === 206;
  } catch {
    return false;
  }
}

export function buildBasemapStyle(cfg: BasemapConfig, appearance: Appearance = 'dark'): BasemapResult {
  const light = appearance === 'light';
  const bg = basemapPaint(appearance, 'none').background as { 'background-color': string };
  const BACKGROUND = { id: 'background', type: 'background', paint: bg } as const;
  // Dark reproduces the original raster layer (no paint); Light adds the paint.
  const rasterLayer = (): StyleSpecification['layers'][number] => ({
    id: 'basemap',
    type: 'raster',
    source: 'basemap',
    ...(light ? { paint: basemapPaint(appearance, 'raster').basemap } : {}),
  });
  const vp = basemapPaint(appearance, 'vector');
  if (cfg.pmtilesUrl && cfg.pmtilesPresent) {
    const url = `pmtiles://${cfg.pmtilesUrl}`;
    if (cfg.pmtilesKind === 'vector') {
      return {
        source: 'pmtiles',
        style: {
          version: 8,
          sources: { basemap: { type: 'vector', url, attribution: OSM_ATTRIBUTION } },
          layers: [
            BACKGROUND,
            { id: 'water', type: 'fill', source: 'basemap', 'source-layer': 'water', paint: { ...vp.water, } },
            { id: 'roads', type: 'line', source: 'basemap', 'source-layer': 'roads', paint: { ...vp.roads, 'line-width': 1 } },
          ],
        },
      };
    }
    return {
      source: 'pmtiles',
      style: {
        version: 8,
        sources: { basemap: { type: 'raster', url, tileSize: 256, attribution: OSM_ATTRIBUTION } },
        layers: [BACKGROUND, rasterLayer()],
      },
    };
  }
  if (cfg.isDev) {
    return {
      source: 'osm-dev-fallback',
      style: {
        version: 8,
        sources: {
          basemap: { type: 'raster', tiles: OSM_RASTER_TILES, tileSize: 256, maxzoom: 19, attribution: OSM_ATTRIBUTION },
        },
        layers: [BACKGROUND, rasterLayer()],
      },
    };
  }
  // Production without PMTiles: no third-party tile requests.
  return { source: 'none', style: { version: 8, sources: {}, layers: [BACKGROUND] } };
}
