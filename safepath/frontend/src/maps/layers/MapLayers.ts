/**
 * Adds and updates the GeoJSON sources/layers on a MapLibre map (task 13.3). The manager only
 * needs a small subset of the map API (`MapLike`) so tests can use a fake map.
 */
import {
  buildClosures,
  buildElevation,
  buildGraphEdges,
  buildHazard,
  buildIncidentBoundaries,
  buildMarkers,
  buildReports,
  buildRestrictions,
  buildRoutes,
  buildSimulatedStation,
  buildStations,
  buildVerified,
} from './data';
import type { Appearance } from '../../styles/tokens';
import { renderIcon } from './icons';
import { ICONS, LAYER_DEFS, TEXT_FONT, TOGGLES } from './styles';
import { EMPTY_FC, SOURCE_IDS, type FC, type MapLayerInputs, type SourceId } from './types';

export interface GeoJsonSourceLike {
  setData(data: unknown): void;
}

export interface MapLike {
  addSource(id: string, spec: unknown): void;
  getSource(id: string): unknown;
  addLayer(layer: unknown): void;
  getLayer(id: string): unknown;
  setLayoutProperty(layerId: string, prop: string, value: unknown): void;
  setPaintProperty?(layerId: string, prop: string, value: unknown): void;
  addImage(id: string, image: unknown, options?: unknown): void;
  hasImage(id: string): boolean;
  setGlyphs?(url: string): void;
}

/** Artifact layers fetched from GET /api/layers/{name}. */
export type ArtifactLayerName = 'cta_stations' | 'simulated_station' | 'buildings';
const ARTIFACT_SOURCE: Record<ArtifactLayerName, SourceId> = {
  cta_stations: 'cta-stations',
  simulated_station: 'simulated-station',
  buildings: 'buildings',
};

/** Default-color label layers whose text/halo follow the Appearance (Req 5.6-5.8). */
export const APPEARANCE_LABEL_LAYERS = ['elevation-label', 'hazards-label', 'fire-label', 'flood-label', 'cta-stations-label'] as const;

export const LABEL_PAINT: Record<Appearance, { text: string; halo: string }> = {
  light: { text: '#1c1c1e', halo: '#ffffff' },
  dark: { text: '#f2f2f7', halo: '#1c1c1e' },
};

export class MapLayers {
  private visible: Record<string, boolean> = {};
  private initialized = false;
  private inputs: MapLayerInputs = {};
  private appearance: Appearance = 'dark';

  constructor(private readonly map: MapLike, initialVisible: Record<string, boolean> = {}, glyphsUrl?: string) {
    for (const t of TOGGLES) this.visible[t.id] = initialVisible[t.id] ?? t.defaultVisible;
    if (glyphsUrl && typeof map.setGlyphs === 'function') map.setGlyphs(glyphsUrl);
  }

  /** Register icons, sources and layers. Idempotent. */
  init(): void {
    if (this.initialized) return;
    this.initialized = true;
    for (const spec of Object.values(ICONS)) {
      if (!this.map.hasImage(spec.id)) this.map.addImage(spec.id, renderIcon(spec), { pixelRatio: 2 });
    }
    for (const id of SOURCE_IDS) {
      if (!this.map.getSource(id)) this.map.addSource(id, { type: 'geojson', data: EMPTY_FC });
    }
    for (const { toggle, layer } of LAYER_DEFS) {
      if (this.map.getLayer(layer.id)) continue;
      this.map.addLayer({
        ...layer,
        layout: { ...(layer.layout ?? {}), visibility: this.visible[toggle] === false ? 'none' : 'visible' },
      });
    }
    this.setInputs(this.inputs);
    this.setAppearance(this.appearance);
  }

  /** Repaint default-color labels for the Appearance; paint-only, no layer is removed or re-added. */
  setAppearance(appearance: Appearance): void {
    this.appearance = appearance;
    if (!this.initialized || !this.map.setPaintProperty) return;
    const { text, halo } = LABEL_PAINT[appearance];
    for (const id of APPEARANCE_LABEL_LAYERS) {
      if (!this.map.getLayer(id)) continue;
      this.map.setPaintProperty(id, 'text-color', text);
      this.map.setPaintProperty(id, 'text-halo-color', halo);
    }
  }

  get fontStack(): string[] {
    return TEXT_FONT;
  }

  isVisible(toggle: string): boolean {
    return this.visible[toggle] !== false;
  }

  visibility(): Record<string, boolean> {
    return { ...this.visible };
  }

  /** Show or hide a toggle group immediately (Req 34.3: within 1 s). */
  setVisible(toggle: string, visible: boolean): void {
    this.visible[toggle] = visible;
    const def = TOGGLES.find((t) => t.id === toggle);
    if (!def || !this.initialized) return;
    for (const id of def.layers) {
      if (this.map.getLayer(id)) this.map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
  }

  /** Replace the data of one source. */
  setSourceData(id: SourceId, data: FC): void {
    const src = this.map.getSource(id) as GeoJsonSourceLike | undefined;
    src?.setData(data);
  }

  /** Feed a fetched artifact layer (stations, simulated station, buildings). */
  setArtifactLayer(name: ArtifactLayerName, data: FC): void {
    if (name === 'cta_stations') this.setSourceData('cta-stations', buildStations(data));
    else if (name === 'simulated_station') this.setSourceData('simulated-station', buildSimulatedStation(data));
    else this.setSourceData(ARTIFACT_SOURCE[name], data);
  }

  /** Recompute every prop-driven source. Synchronous, so SIMULATED labels appear immediately. */
  setInputs(inputs: MapLayerInputs): void {
    this.inputs = inputs;
    if (!this.initialized) return;
    const h = inputs.historicalLayers;
    const rep = buildReports(inputs.reports);
    this.setSourceData('graph', buildGraphEdges(inputs.graphEdges));
    this.setSourceData('elevation', buildElevation(inputs.floodHighlight, h));
    this.setSourceData('debris', buildHazard('debris', inputs.hazards?.debris, h));
    this.setSourceData('fire', buildHazard('fire', inputs.hazards?.fire, h));
    this.setSourceData('flood', buildHazard('flood', inputs.hazards?.flood, h));
    this.setSourceData('incidents', buildIncidentBoundaries(inputs.incidentBoundaries));
    this.setSourceData('restrictions', buildRestrictions(inputs.restrictions));
    this.setSourceData('closures', buildClosures(inputs.closures, rep.closurePoints));
    this.setSourceData('reports', rep.reports);
    this.setSourceData('routes', buildRoutes(inputs.routes));
    this.setSourceData('markers', buildMarkers(inputs.start, inputs.destination));
    this.setSourceData('verified', buildVerified(inputs.verifiedDestinations));
  }
}

export { ARTIFACT_SOURCE };
