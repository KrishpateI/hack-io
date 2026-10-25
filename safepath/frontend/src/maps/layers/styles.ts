/**
 * Pure style definitions for the map data layers: icons, data-driven paint, layer specs,
 * toggle groups and legend entries. No MapLibre runtime imports, so it is unit-testable.
 */
import { palette } from '../../styles/tokens';
import type { IconShape, IconSpec } from './icons';
import type { ArtifactStatus, ArtifactStatusMap, DestinationInput, FC, MapLayerInputs } from './types';

export const SIMULATED_LABEL = 'SIMULATED';
export const SIMULATION_DESTINATION_LABEL = 'SIMULATION DESTINATION — NOT an official evacuation shelter';
export const HISTORICAL_LAYER_LABEL = 'Historical reference (2007), not a current evacuation order';
/** Flood area and low-lying area are the same scenario data, so they share one entry (design D3). */
export const FLOOD_AREA_LABEL = 'SIMULATED flood area (modeled low-lying streets, no depth values)';

/** Building_Height_Class colors; Unknown is distinct (dark slate + dashed outline). */
export const HEIGHT_CLASS_COLORS = {
  'Low-rise': '#6b7fa8',
  'Mid-rise': '#8b6fc9',
  'High-rise': '#c084fc',
  Unknown: '#334155',
} as const;
export type HeightClass = keyof typeof HEIGHT_CLASS_COLORS;

export function normalizeHeightClass(value: unknown): HeightClass {
  return typeof value === 'string' && value in HEIGHT_CLASS_COLORS ? (value as HeightClass) : 'Unknown';
}

/** Extraction-area boundary line color (map layer color, so it lives with the other layer colors). */
export const EXTRACTION_COLOR = '#e879f9';

export const HAZARD_COLORS = {
  debris: '#d6a56a',
  fire: '#f43f5e',
  flood: '#3b82f6',
  incident: '#fde047',
  simulatedStation: '#a78bfa',
  station: '#e2e8f0',
} as const;

// ------------------------------------------------------------------ icons
export const ICONS: Record<string, IconSpec> = {
  'icon-report-unverified': { id: 'icon-report-unverified', shape: 'triangle', color: palette.unverified },
  'icon-report-corroborated': { id: 'icon-report-corroborated', shape: 'diamond', color: palette.unverified },
  'icon-report-disputed': { id: 'icon-report-disputed', shape: 'square', color: palette.unverified, hollow: true },
  'icon-report-official': { id: 'icon-report-official', shape: 'circle', color: palette.text },
  'icon-closure': { id: 'icon-closure', shape: 'cross', color: palette.closure },
  'icon-verified-resource': { id: 'icon-verified-resource', shape: 'star', color: palette.verified },
  'icon-simulated-station': { id: 'icon-simulated-station', shape: 'hexagon', color: HAZARD_COLORS.simulatedStation },
  'icon-start': { id: 'icon-start', shape: 'circle', color: palette.route },
  'icon-destination-simulated': { id: 'icon-destination-simulated', shape: 'hexagon', color: palette.routeAlt, hollow: true },
};

export interface ReportSymbol {
  /** Which source the marker belongs to. */
  group: 'reports' | 'closures' | 'hidden';
  icon: string;
  shape: IconShape;
  /** Text shown in the popup/legend so status is never conveyed by color or shape alone. */
  statusText: string;
}

/**
 * Status -> symbol. Orange for Unverified/Corroborated/Disputed with distinct shapes;
 * red only for confirmed closures; green is reserved for explicitly verified resources.
 */
export function reportSymbol(status: string, confirmedClosure = false): ReportSymbol {
  if (confirmedClosure && status === 'Officially_Verified') {
    return { group: 'closures', icon: 'icon-closure', shape: 'cross', statusText: 'Officially verified closure' };
  }
  switch (status) {
    case 'Unverified':
      return { group: 'reports', icon: 'icon-report-unverified', shape: 'triangle', statusText: 'Unverified' };
    case 'Corroborated':
      return { group: 'reports', icon: 'icon-report-corroborated', shape: 'diamond', statusText: 'Corroborated' };
    case 'Disputed':
      return { group: 'reports', icon: 'icon-report-disputed', shape: 'square', statusText: 'Disputed' };
    case 'Officially_Verified':
      return { group: 'reports', icon: 'icon-report-official', shape: 'circle', statusText: 'Officially verified' };
    default:
      return { group: 'hidden', icon: 'icon-report-unverified', shape: 'triangle', statusText: status };
  }
}

// ------------------------------------------------------------------ layers
/** A loosely typed MapLibre layer definition (validated by MapLibre at runtime). */
export interface LayerDef {
  id: string;
  type: 'fill' | 'line' | 'circle' | 'symbol';
  source: string;
  filter?: unknown;
  minzoom?: number;
  layout?: Record<string, unknown>;
  paint?: Record<string, unknown>;
}

export const TEXT_FONT = ['Open Sans Regular'];

const labelLayout = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  'text-field': ['get', 'label'],
  'text-font': TEXT_FONT,
  'text-size': 11,
  'text-offset': [0, 1.2],
  'text-anchor': 'top',
  'text-optional': true,
  ...extra,
});
const labelPaint = (color: string = palette.text): Record<string, unknown> => ({
  'text-color': color,
  'text-halo-color': palette.base,
  'text-halo-width': 1.5,
});

const heightClassExpr = ['coalesce', ['get', 'building_height_class'], 'Unknown'];
const knownHeight = ['match', heightClassExpr, ['Low-rise', 'Mid-rise', 'High-rise'], true, false];

export interface ToggleDef {
  id: string;
  label: string;
  defaultVisible: boolean;
  layers: string[];
}

/** Ordered bottom to top. Each layer belongs to exactly one toggle. */
export const LAYER_DEFS: { toggle: string; layer: LayerDef }[] = [
  {
    toggle: 'buildings',
    layer: {
      id: 'buildings-fill', type: 'fill', source: 'buildings', minzoom: 13,
      paint: {
        'fill-color': ['match', heightClassExpr, 'Low-rise', HEIGHT_CLASS_COLORS['Low-rise'], 'Mid-rise', HEIGHT_CLASS_COLORS['Mid-rise'], 'High-rise', HEIGHT_CLASS_COLORS['High-rise'], HEIGHT_CLASS_COLORS.Unknown],
        'fill-opacity': 0.6,
      },
    },
  },
  { toggle: 'buildings', layer: { id: 'buildings-outline', type: 'line', source: 'buildings', minzoom: 13, filter: knownHeight, paint: { 'line-color': palette.border, 'line-width': 0.6 } } },
  { toggle: 'buildings', layer: { id: 'buildings-unknown-outline', type: 'line', source: 'buildings', minzoom: 13, filter: ['!', knownHeight], paint: { 'line-color': palette.textMuted, 'line-width': 0.9, 'line-dasharray': [2, 2] } } },
  { toggle: 'hazards', layer: { id: 'flood-fill', type: 'fill', source: 'flood', paint: { 'fill-color': HAZARD_COLORS.flood, 'fill-opacity': 0.4 } } },
  { toggle: 'hazards', layer: { id: 'flood-outline', type: 'line', source: 'flood', paint: { 'line-color': HAZARD_COLORS.flood, 'line-width': 2 } } },
  { toggle: 'hazards', layer: { id: 'debris-fill', type: 'fill', source: 'debris', paint: { 'fill-color': HAZARD_COLORS.debris, 'fill-opacity': 0.4 } } },
  { toggle: 'hazards', layer: { id: 'debris-outline', type: 'line', source: 'debris', paint: { 'line-color': HAZARD_COLORS.debris, 'line-width': 2, 'line-dasharray': [1, 1] } } },
  { toggle: 'hazards', layer: { id: 'fire-fill', type: 'fill', source: 'fire', paint: { 'fill-color': HAZARD_COLORS.fire, 'fill-opacity': 0.35 } } },
  { toggle: 'hazards', layer: { id: 'fire-outline', type: 'line', source: 'fire', paint: { 'line-color': HAZARD_COLORS.fire, 'line-width': 3, 'line-dasharray': [6, 2, 1, 2] } } },
  { toggle: 'hazards', layer: { id: 'hazards-label', type: 'symbol', source: 'debris', layout: labelLayout({ 'text-offset': [0, 0], 'text-anchor': 'center' }), paint: labelPaint() } },
  { toggle: 'hazards', layer: { id: 'fire-label', type: 'symbol', source: 'fire', layout: labelLayout({ 'text-offset': [0, 0], 'text-anchor': 'center' }), paint: labelPaint() } },
  { toggle: 'hazards', layer: { id: 'flood-label', type: 'symbol', source: 'flood', layout: labelLayout({ 'text-offset': [0, 0], 'text-anchor': 'center' }), paint: labelPaint() } },
  { toggle: 'incidents', layer: { id: 'incidents-line', type: 'line', source: 'incidents', paint: { 'line-color': HAZARD_COLORS.incident, 'line-width': 3, 'line-dasharray': [1, 2.5] } } },
  { toggle: 'incidents', layer: { id: 'incidents-label', type: 'symbol', source: 'incidents', layout: labelLayout({ 'text-offset': [0, 0], 'text-anchor': 'center' }), paint: labelPaint(HAZARD_COLORS.incident) } },
  { toggle: 'closures', layer: { id: 'restrictions-hatch', type: 'line', source: 'restrictions', paint: { 'line-color': palette.closure, 'line-width': 9, 'line-opacity': 0.45, 'line-dasharray': [0.5, 1.2] } } },
  { toggle: 'closures', layer: { id: 'restrictions-line', type: 'line', source: 'restrictions', paint: { 'line-color': palette.closure, 'line-width': 2.5, 'line-dasharray': [4, 2] } } },
  { toggle: 'closures', layer: { id: 'closures-line', type: 'line', source: 'closures', filter: ['==', ['geometry-type'], 'LineString'], paint: { 'line-color': palette.closure, 'line-width': 5, 'line-dasharray': [2, 1.5] } } },
  { toggle: 'routes', layer: { id: 'routes-casing', type: 'line', source: 'routes', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': palette.base, 'line-width': 8 } } },
  { toggle: 'routes', layer: { id: 'routes-alt', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'alternative'], layout: { 'line-cap': 'butt', 'line-join': 'round' }, paint: { 'line-color': palette.routeAlt, 'line-width': 4, 'line-dasharray': [2, 2] } } },
  { toggle: 'routes', layer: { id: 'routes-primary', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'primary'], layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': palette.route, 'line-width': 5 } } },
  { toggle: 'transit', layer: { id: 'cta-stations-circle', type: 'circle', source: 'cta-stations', paint: { 'circle-radius': 6, 'circle-color': HAZARD_COLORS.station, 'circle-stroke-color': palette.base, 'circle-stroke-width': 2 } } },
  { toggle: 'transit', layer: { id: 'cta-stations-label', type: 'symbol', source: 'cta-stations', minzoom: 12, layout: labelLayout(), paint: labelPaint() } },
  { toggle: 'simulated_station', layer: { id: 'simulated-station-icon', type: 'symbol', source: 'simulated-station', layout: { 'icon-image': 'icon-simulated-station', 'icon-size': 0.75, 'icon-allow-overlap': true, ...labelLayout({ 'text-offset': [0, 1.4] }) }, paint: labelPaint(HAZARD_COLORS.simulatedStation) } },
  { toggle: 'reports', layer: { id: 'reports-symbol', type: 'symbol', source: 'reports', layout: { 'icon-image': ['get', 'icon'], 'icon-size': 0.75, 'icon-allow-overlap': true, ...labelLayout({ 'text-size': 10 }) }, paint: labelPaint(palette.unverified) } },
  { toggle: 'closures', layer: { id: 'closures-symbol', type: 'symbol', source: 'closures', filter: ['==', ['geometry-type'], 'Point'], layout: { 'icon-image': 'icon-closure', 'icon-size': 0.8, 'icon-allow-overlap': true, ...labelLayout({ 'text-size': 10 }) }, paint: labelPaint(palette.closure) } },
  { toggle: 'verified', layer: { id: 'verified-symbol', type: 'symbol', source: 'verified', layout: { 'icon-image': 'icon-verified-resource', 'icon-size': 0.85, 'icon-allow-overlap': true, ...labelLayout() }, paint: labelPaint(palette.verified) } },
  { toggle: 'markers', layer: { id: 'markers-symbol', type: 'symbol', source: 'markers', layout: { 'icon-image': ['get', 'icon'], 'icon-size': 0.85, 'icon-allow-overlap': true, ...labelLayout({ 'text-max-width': 14 }) }, paint: labelPaint() } },
];

const TOGGLE_META: { id: string; label: string; defaultVisible: boolean }[] = [
  { id: 'routes', label: 'Routes', defaultVisible: true },
  { id: 'markers', label: 'Start and destination', defaultVisible: true },
  { id: 'closures', label: 'Closures and restrictions', defaultVisible: true },
  { id: 'reports', label: 'Community reports (unverified)', defaultVisible: true },
  { id: 'hazards', label: 'SIMULATED hazards', defaultVisible: true },
  { id: 'incidents', label: 'Incident boundaries', defaultVisible: true },
  { id: 'verified', label: 'Verified destinations', defaultVisible: true },
  { id: 'transit', label: 'CTA stations', defaultVisible: true },
  { id: 'simulated_station', label: 'SIMULATED station', defaultVisible: true },
  { id: 'buildings', label: 'Buildings (height class)', defaultVisible: true },
];

export const TOGGLES: ToggleDef[] = TOGGLE_META.map((t) => ({
  ...t,
  layers: LAYER_DEFS.filter((d) => d.toggle === t.id).map((d) => d.layer.id),
}));

/** Toggle id -> MapLibre layer ids, for merging into MapView's LAYER_GROUPS. */
export const DATA_LAYER_GROUPS: Record<string, string[]> = Object.fromEntries(
  TOGGLES.map((t) => [t.id, t.layers]),
);

// ------------------------------------------------------------------ legend
export interface LegendEntry {
  id: string;
  toggle: string;
  label: string;
  shape: IconShape | 'line' | 'fill';
  color: string;
  dashed?: boolean;
  hollow?: boolean;
  /** Extra text so meaning does not rely on color. */
  note?: string;
}

export const LEGEND_ENTRIES: LegendEntry[] = [
  { id: 'route-primary', toggle: 'routes', label: 'Recommended route (algorithm-generated)', shape: 'line', color: palette.route },
  { id: 'route-alt', toggle: 'routes', label: 'Alternative route (dashed)', shape: 'line', color: palette.routeAlt, dashed: true },
  { id: 'closure', toggle: 'closures', label: 'Confirmed closure / restricted edge (red, hatched dash)', shape: 'cross', color: palette.closure },
  { id: 'report-unverified', toggle: 'reports', label: 'Unverified report (triangle)', shape: 'triangle', color: palette.unverified },
  { id: 'report-corroborated', toggle: 'reports', label: 'Corroborated report (diamond)', shape: 'diamond', color: palette.unverified },
  { id: 'report-disputed', toggle: 'reports', label: 'Disputed report (hollow square)', shape: 'square', color: palette.unverified, hollow: true },
  { id: 'verified', toggle: 'verified', label: 'Verified destination with official source (star)', shape: 'star', color: palette.verified },
  { id: 'hazard-flood', toggle: 'hazards', label: FLOOD_AREA_LABEL, shape: 'fill', color: HAZARD_COLORS.flood },
  { id: 'hazard-debris', toggle: 'hazards', label: 'SIMULATED debris zone (dotted outline)', shape: 'fill', color: HAZARD_COLORS.debris, dashed: true },
  { id: 'hazard-fire', toggle: 'hazards', label: 'SIMULATED fire perimeter (dash-dot outline)', shape: 'fill', color: HAZARD_COLORS.fire, dashed: true },
  { id: 'incident', toggle: 'incidents', label: 'Incident boundary (dotted yellow)', shape: 'line', color: HAZARD_COLORS.incident, dashed: true },
  { id: 'transit', toggle: 'transit', label: 'CTA station (GTFS data only)', shape: 'circle', color: HAZARD_COLORS.station },
  { id: 'sim-station', toggle: 'simulated_station', label: 'SIMULATED station (hexagon)', shape: 'hexagon', color: HAZARD_COLORS.simulatedStation },
  { id: 'height-low', toggle: 'buildings', label: 'Low-rise building', shape: 'fill', color: HEIGHT_CLASS_COLORS['Low-rise'] },
  { id: 'height-mid', toggle: 'buildings', label: 'Mid-rise building', shape: 'fill', color: HEIGHT_CLASS_COLORS['Mid-rise'] },
  { id: 'height-high', toggle: 'buildings', label: 'High-rise building', shape: 'fill', color: HEIGHT_CLASS_COLORS['High-rise'] },
  { id: 'height-unknown', toggle: 'buildings', label: 'Height Unknown (dashed outline)', shape: 'fill', color: HEIGHT_CLASS_COLORS.Unknown, dashed: true },
  { id: 'start', toggle: 'markers', label: 'Start', shape: 'circle', color: palette.route },
  { id: 'destination', toggle: 'markers', label: SIMULATION_DESTINATION_LABEL, shape: 'hexagon', color: palette.routeAlt, hollow: true },
];

/** Legend entry id -> whether its data is currently on the map. */
export type LegendPresence = Record<string, boolean>;

/**
 * Legend entries for toggles that are currently visible. With `presence`, an entry is listed only
 * when its data is also present (Req 2.9); without it, the toggle-only behavior is kept.
 */
export function visibleLegend(visible: Record<string, boolean>, presence?: LegendPresence): LegendEntry[] {
  return LEGEND_ENTRIES.filter((e) => visible[e.toggle] !== false && (!presence || presence[e.id] === true));
}

// ------------------------------------------------------------------ legend presence
// Kept free of `data.ts` imports (data.ts imports this module); the predicates mirror the builders.
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const hasGeometry = (src: FC | null | undefined): boolean =>
  !!src && Array.isArray(src.features) && src.features.some((f) => !!f && !!f.geometry);
const isOfficialDestination = (d: DestinationInput): boolean =>
  d.simulated !== true && d.source_record?.is_official === true;

function artifactPresent(s: ArtifactStatus | undefined): boolean {
  return s?.status === 'loaded' && (s.count === undefined || s.count > 0);
}

const HEIGHT_ENTRY: Record<HeightClass, string> = {
  'Low-rise': 'height-low',
  'Mid-rise': 'height-mid',
  'High-rise': 'height-high',
  Unknown: 'height-unknown',
};

/**
 * Which legend entries have data on the map, from the layer inputs and the artifact-layer load
 * status. Pure; every entry id in LEGEND_ENTRIES gets a boolean.
 */
export function legendPresence(inputs: MapLayerInputs, artifactStatus: ArtifactStatusMap = {}): LegendPresence {
  const routes = (inputs.routes ?? []).filter((r) => Array.isArray(r.coordinates) && r.coordinates.length >= 2);
  const statuses = new Set<string>();
  let closurePoint = false;
  for (const r of inputs.reports ?? []) {
    if (!finite(r.lng) || !finite(r.lat)) continue;
    const sym = reportSymbol(r.verification_status, r.confirmed_closure === true);
    if (sym.group === 'closures') closurePoint = true;
    else if (sym.group === 'reports') statuses.add(r.verification_status);
  }
  const start = inputs.start;
  const dest = inputs.destination;
  const buildings = artifactStatus.buildings;
  const buildingsPresent = artifactPresent(buildings);
  const out: LegendPresence = {
    'route-primary': routes.some((r) => r.kind === 'primary'),
    'route-alt': routes.some((r) => r.kind === 'alternative'),
    closure: hasGeometry(inputs.restrictions) || hasGeometry(inputs.closures) || closurePoint,
    'report-unverified': statuses.has('Unverified'),
    'report-corroborated': statuses.has('Corroborated'),
    'report-disputed': statuses.has('Disputed'),
    verified: (inputs.verifiedDestinations ?? []).some((d) => isOfficialDestination(d) && finite(d.lng) && finite(d.lat)),
    'hazard-flood': hasGeometry(inputs.hazards?.flood),
    'hazard-debris': hasGeometry(inputs.hazards?.debris),
    'hazard-fire': hasGeometry(inputs.hazards?.fire),
    incident: hasGeometry(inputs.incidentBoundaries),
    transit: artifactPresent(artifactStatus.cta_stations),
    'sim-station': artifactPresent(artifactStatus.simulated_station),
    start: !!start && finite(start[0]) && finite(start[1]),
    destination: !!dest && finite(dest.lng) && finite(dest.lat) && !isOfficialDestination(dest),
  };
  for (const [cls, id] of Object.entries(HEIGHT_ENTRY) as [HeightClass, string][]) {
    // Per-class counts when the store provides them; otherwise every class counts as present.
    const counts = buildings?.classCounts;
    out[id] = buildingsPresent && (counts ? (counts[cls] ?? 0) > 0 : true);
  }
  return out;
}
