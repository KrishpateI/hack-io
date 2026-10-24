import type { Feature, FeatureCollection, Geometry } from 'geojson';

export type LngLat = [number, number];
export type FC = FeatureCollection<Geometry, Record<string, unknown>>;
export type Feat = Feature<Geometry, Record<string, unknown>>;

/** A route polyline from the route panel (GeoJSON-style [lng, lat] coordinates). */
export interface RouteLineInput {
  id: string;
  kind: 'primary' | 'alternative';
  coordinates: LngLat[];
  label?: string;
  properties?: Record<string, unknown>;
}

/** A community report (or confirmed closure report) to place on the map. */
export interface ReportMarkerInput {
  report_id: string;
  category: string;
  verification_status: string;
  lng: number;
  lat: number;
  description?: string | null;
  source_label?: string | null;
  simulated?: boolean;
  confirmations?: number;
  disputes?: number;
  created_at?: string | null;
  /** True when this is an Officially_Verified closure (rendered red, not orange). */
  confirmed_closure?: boolean;
}

export interface DestinationInput {
  id: string;
  name: string;
  lng: number;
  lat: number;
  /** True for an algorithm-chosen simulation destination (never an official shelter). */
  simulated: boolean;
  /** Only an official Source_Record makes a destination a verified (green) resource. */
  source_record?: { is_official?: boolean | null; source_id?: string | null } | null;
}

/** Everything the manager renders besides the artifact layers fetched from the API. */
export interface MapLayerInputs {
  /** Optional faint street/graph overlay (LineString features). */
  graphEdges?: FC | null;
  routes?: RouteLineInput[];
  reports?: ReportMarkerInput[];
  incidentBoundaries?: FC | null;
  hazards?: { debris?: FC | null; fire?: FC | null; flood?: FC | null };
  /** Scenario flood highlight GeoJSON (lowest ~20% elevation), SIMULATED, no depth values. */
  floodHighlight?: FC | null;
  /** Restricted edges (LineString features) from route restrictions/closures. */
  restrictions?: FC | null;
  /** Confirmed closures (lines or points). */
  closures?: FC | null;
  start?: LngLat | null;
  destination?: DestinationInput | null;
  verifiedDestinations?: DestinationInput[];
  /** Layers derived from CBD data carry the historical label (Req 8.10). */
  historicalLayers?: string[];
}

/** Source ids the manager owns. */
export const SOURCE_IDS = [
  'graph', 'buildings', 'cta-stations', 'simulated-station', 'elevation', 'debris', 'fire',
  'flood', 'incidents', 'restrictions', 'closures', 'routes', 'reports', 'markers', 'verified',
] as const;
export type SourceId = (typeof SOURCE_IDS)[number];

export const EMPTY_FC: FC = { type: 'FeatureCollection', features: [] };
