/**
 * Pure model for map feature inspection (task 13.4, Req 35, 10.3, 6.5, 5, 36).
 * Turns raw map hits into inspectable candidates and formats attribute values. Nothing is
 * inferred: every missing value renders as "Unknown" (labelAttribute).
 */
import { labelAttribute } from '../../services/labels';
import type { SourceRecord } from '../../services/sources';
import { CORROBORATED_TEXT, OFFICIAL_TEXT } from '../community/ReportStatusBadge';
import type { EdgeDetail, EdgeReport } from './edgeService';

export const UNKNOWN = 'Unknown';
export const ELEVATION_UNIT = 'm NAVD88';
export const ELEVATION_CAVEAT = 'terrain elevation, not bridge deck/tunnel level';
export const STRUCTURAL_TEXT = 'structural condition: Unknown';
export const ENTRANCE_SAFETY_TEXT = 'entrance safety Unknown';
export const SIMULATED_MODERATOR_SOURCE = 'Simulated moderator verification';
export const COMMUNITY_SOURCE = 'Community report';
export const OFFICIAL_SOURCE = 'Official';

export type InspectKind =
  | 'road'
  | 'report'
  | 'closure'
  | 'route'
  | 'building'
  | 'station'
  | 'hazard_zone'
  | 'destination'
  | 'official';

export interface MapHit {
  layerId: string;
  properties: Record<string, unknown>;
}

export interface MapClick {
  lngLat: [number, number];
  hits: MapHit[];
}

export interface Candidate {
  id: string;
  kind: InspectKind;
  title: string;
  layer: string;
  props: Record<string, unknown>;
  edge?: EdgeDetail;
}

/** Layer id (exact or prefix) -> kind. Lower priority number wins when several layers are hit. */
const LAYER_KINDS: { match: (id: string) => boolean; kind: InspectKind; priority: number; layer: string }[] = [
  { match: (id) => id === 'reports-symbol', kind: 'report', priority: 0, layer: 'reports' },
  { match: (id) => id === 'closures-symbol', kind: 'closure', priority: 0, layer: 'closures' },
  { match: (id) => id === 'verified-symbol', kind: 'official', priority: 1, layer: 'verified' },
  { match: (id) => id === 'markers-symbol', kind: 'destination', priority: 1, layer: 'markers' },
  { match: (id) => id.startsWith('cta-stations-'), kind: 'station', priority: 1, layer: 'cta-stations' },
  { match: (id) => id === 'simulated-station-icon', kind: 'station', priority: 1, layer: 'simulated-station' },
  { match: (id) => id.startsWith('routes-'), kind: 'route', priority: 2, layer: 'routes' },
  { match: (id) => id === 'closures-line' || id.startsWith('restrictions-'), kind: 'closure', priority: 3, layer: 'closures' },
  { match: (id) => /^(flood|debris|fire)-|^hazards-label$/.test(id), kind: 'hazard_zone', priority: 4, layer: 'hazards' },
  { match: (id) => id.startsWith('elevation-'), kind: 'hazard_zone', priority: 4, layer: 'elevation' },
  { match: (id) => id.startsWith('incidents-'), kind: 'official', priority: 5, layer: 'incidents' },
  { match: (id) => id.startsWith('buildings-'), kind: 'building', priority: 6, layer: 'buildings' },
  { match: (id) => id === 'graph-line', kind: 'road', priority: 7, layer: 'graph' },
];

/** Interactive MapLibre layer ids (those that appear in LAYER_KINDS), for queryRenderedFeatures. */
export const INTERACTIVE_LAYER_IDS: string[] = [
  'reports-symbol', 'closures-symbol', 'verified-symbol', 'markers-symbol', 'cta-stations-circle',
  'cta-stations-label', 'simulated-station-icon', 'routes-primary', 'routes-alt', 'routes-casing',
  'closures-line', 'restrictions-line', 'restrictions-hatch', 'flood-fill', 'debris-fill', 'fire-fill',
  'elevation-fill', 'incidents-line', 'buildings-fill', 'graph-line',
];

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

export function kindOfLayer(layerId: string): { kind: InspectKind; priority: number; layer: string } | null {
  const m = LAYER_KINDS.find((l) => l.match(layerId));
  return m ? { kind: m.kind, priority: m.priority, layer: m.layer } : null;
}

// ------------------------------------------------------------------ formatters
export function formatFlag(v: unknown): string {
  if (v === true) return 'Yes';
  if (v === false) return 'No';
  return UNKNOWN;
}

/** Elevation with unit; a non-number (including "Unknown") is Unknown. */
export function formatElevation(v: unknown): string {
  return typeof v === 'number' && Number.isFinite(v) ? `${v.toFixed(1)} ${ELEVATION_UNIT}` : UNKNOWN;
}

export function formatTimestamp(v: unknown): string {
  const s = str(v);
  if (!s) return UNKNOWN;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
}

export const formatText = (v: unknown): string => labelAttribute(v);

/** Verification_Status text; Corroborated always uses the exact wording (Req 24, 36.2). */
export function verificationText(status: string | null | undefined, simulated: boolean): string {
  switch (status) {
    case 'Corroborated':
      return CORROBORATED_TEXT;
    case 'Officially_Verified':
      return simulated ? 'Officially_Verified (SIMULATED moderator verification)' : OFFICIAL_TEXT;
    case undefined:
    case null:
    case '':
      return UNKNOWN;
    default:
      return status.replace(/_/g, ' ');
  }
}

/** Source category: Community report / Official / Simulated moderator verification. */
export function sourceCategory(status: string | null | undefined, simulated: boolean): string {
  if (status === 'Officially_Verified') return simulated ? SIMULATED_MODERATOR_SOURCE : OFFICIAL_SOURCE;
  return COMMUNITY_SOURCE;
}

// ------------------------------------------------------------------ summaries
export interface HazardSummary {
  id: string;
  category: string;
  description: string;
  createdAt: string;
  statusText: string;
  corroborating: number;
  sourceCategory: string;
  expiry: string;
  simulated: boolean;
}

/** Merge marker properties with the full public report (which carries expires_at). */
export function hazardSummary(props: Record<string, unknown>, report?: EdgeReport | null): HazardSummary {
  const status = str(report?.verification_status) ?? str(props.verification_status);
  const simulated = report?.simulated === true || props.simulated === true;
  const n = report?.confirmations ?? props.confirmations;
  return {
    id: str(report?.report_id) ?? str(props.report_id) ?? UNKNOWN,
    category: str(report?.category) ?? str(props.category) ?? UNKNOWN,
    description: str(report?.description) ?? str(props.description) ?? UNKNOWN,
    createdAt: formatTimestamp(report?.created_at ?? props.created_at),
    statusText: verificationText(status, simulated),
    corroborating: typeof n === 'number' && Number.isFinite(n) ? n : 0,
    sourceCategory: sourceCategory(status, simulated),
    expiry: formatTimestamp(report?.expires_at ?? props.expires_at),
    simulated,
  };
}

export interface BuildingSummary {
  heightClass: string;
  height: string;
  levels: string;
  structural: string;
}

function withSource(value: unknown, source: unknown, unit: string): string {
  if (typeof value !== 'number' && !(typeof value === 'string' && value.trim() && value !== UNKNOWN)) return UNKNOWN;
  return `${value}${unit} (source: ${labelAttribute(source)})`;
}

/** Building_Height_Class plus height/levels with their source; structural condition is never inferred. */
export function buildingSummary(props: Record<string, unknown>): BuildingSummary {
  return {
    heightClass: labelAttribute(props.building_height_class),
    height: withSource(props.height_m, props.height_source, ' m'),
    levels: withSource(props.levels ?? props.building_levels, props.levels_source, ''),
    structural: STRUCTURAL_TEXT,
  };
}

export interface StationSummary {
  name: string;
  routes: string;
  wheelchair: string;
  entranceSafety: string;
  simulated: boolean;
}

export function stationSummary(props: Record<string, unknown>): StationSummary {
  const routes = Array.isArray(props.routes) ? props.routes.filter((r) => typeof r === 'string' && r) : [];
  return {
    name: labelAttribute(props.name),
    routes: routes.length ? routes.join(', ') : UNKNOWN,
    wheelchair: labelAttribute(props.wheelchair_boarding),
    entranceSafety: ENTRANCE_SAFETY_TEXT,
    simulated: props.simulated === true,
  };
}

/** Rows of the road panel, in display order. */
export function roadRows(e: EdgeDetail): [string, string][] {
  const level = labelAttribute(e.infrastructure_level);
  const oneWay = e.source_ids?.oneway ?? e.source_ids?.one_way;
  return [
    ['Road name', labelAttribute(e.street_name)],
    ['Road type', labelAttribute(e.road_type)],
    ['Bridge', formatFlag(e.bridge_flag)],
    ['Tunnel', formatFlag(e.tunnel_flag)],
    ['Infrastructure level', level],
    ['Pedestrian access', formatFlag(e.pedestrian_access)],
    ['Mobility accessibility', labelAttribute(e.mobility_accessibility)],
    ['Elevation at start', formatElevation(e.elevation_start)],
    ['Elevation at end', formatElevation(e.elevation_end)],
    ['Elevation confidence', labelAttribute(e.elevation_confidence)],
    ['One-way', labelAttribute(oneWay)],
    ['Closure status', labelAttribute(e.current_closure_status)],
    ['Data source', labelAttribute(e.source)],
    ['Last update', formatTimestamp(e.data_timestamp)],
  ];
}

/** Source_Record ids named by an edge (source_ids values; segment_id is an internal id, not a record). */
export function edgeSourceIds(e: EdgeDetail): string[] {
  return [
    ...new Set(
      Object.entries(e.source_ids ?? {})
        .filter(([k, v]) => k !== 'segment_id' && typeof v === 'string' && v)
        .map(([, v]) => v),
    ),
  ];
}

export function findSource(records: readonly SourceRecord[] | undefined, id: string | null | undefined): SourceRecord | null {
  if (!id) return null;
  return records?.find((r) => r.source_id === id) ?? null;
}

/** Source_Record id carried by a feature, if any. */
export function featureSourceId(props: Record<string, unknown>): string | null {
  return str(props.source_record_id) ?? str(props.source_id) ?? str(props.sourceRecordId);
}

/** CBD-derived content carries the historical label (Req 8.10). */
export function isHistorical(
  props: Record<string, unknown>,
  layer: string,
  historicalLayers: readonly string[] | undefined,
  record?: SourceRecord | null,
): boolean {
  return (
    historicalLayers?.includes(layer) === true ||
    props.cbd_derived === true ||
    props.classification === 'Historical' ||
    record?.data_classification === 'Historical'
  );
}

// ------------------------------------------------------------------ candidates
const TITLES: Record<InspectKind, string> = {
  road: 'Road segment',
  report: 'Hazard report',
  closure: 'Closure or restriction',
  route: 'Route',
  building: 'Building',
  station: 'Transit station',
  hazard_zone: 'Hazard area',
  destination: 'Destination',
  official: 'Official data',
};

export function titleFor(kind: InspectKind, props: Record<string, unknown>): string {
  const label = str(props.label) ?? str(props.name);
  return label ? `${TITLES[kind]}: ${label}` : TITLES[kind];
}

/** Map hits -> candidates (best first, de-duplicated by kind and identity). Road edges come later. */
export function buildCandidates(click: MapClick): Candidate[] {
  const out: (Candidate & { priority: number })[] = [];
  const seen = new Set<string>();
  for (const hit of click.hits) {
    const k = kindOfLayer(hit.layerId);
    if (!k) continue;
    const p = hit.properties ?? {};
    const identity = str(p.report_id) ?? str(p.route_id) ?? str(p.destination_id) ?? str(p.id) ?? str(p.name) ?? str(p.label) ?? hit.layerId;
    const id = `${k.kind}:${identity}`;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({
      id,
      kind: k.kind,
      priority: k.priority,
      layer: str(p.layer) ?? k.layer,
      title: titleFor(k.kind, p),
      props: p,
    });
  }
  return out
    .sort((a, b) => a.priority - b.priority)
    .map((c) => ({ id: c.id, kind: c.kind, title: c.title, layer: c.layer, props: c.props }));
}

export function edgeCandidate(e: EdgeDetail): Candidate {
  const name = labelAttribute(e.street_name);
  return {
    id: `road:${e.edge_id}`,
    kind: 'road',
    title: `Road segment: ${name}`,
    layer: 'graph',
    props: {},
    edge: e,
  };
}

/** Distance label for nearby-feature lists. */
export function describeCandidate(c: Candidate): string {
  return c.title;
}
