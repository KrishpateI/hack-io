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

export class MapLayers {
  private visible: Record<string, boolean> = {};
  private initialized = false;
  private inputs: MapLayerInputs = {};

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
