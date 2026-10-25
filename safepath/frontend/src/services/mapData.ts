/**
 * Pure builders that turn API/realtime data into the plain inputs the map layers accept.
 * Nothing here fabricates data: missing coordinates or geometry simply produce no feature.
 */
import type { Incident, RouteResponse } from './api';
import type { RealtimeEvent } from './eventStore';
import { toMapRoutes } from '../components/route/routeGeometry';
import type { Destination, LatLng } from '../components/sidebar/StartDestinationPicker';
import type { DestinationInput, FC, LngLat, MapLayerInputs, ReportMarkerInput, RouteLineInput } from '../maps/layers/types';

type Raw = Record<string, unknown>;

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Merge REST rows with live store entities by id; live fields win because they are newer. */
export function mergeById(fetched: readonly Raw[], live: Record<string, Raw>, idKeys: readonly string[]): Raw[] {
  const byId = new Map<string, Raw>();
  const idOf = (e: Raw) => idKeys.map((k) => e[k]).find((v) => typeof v === 'string') as string | undefined;
  for (const e of fetched) {
    const id = idOf(e);
    if (id) byId.set(id, e);
  }
  for (const [id, e] of Object.entries(live)) byId.set(id, { ...byId.get(id), ...e });
  return [...byId.values()];
}

const INACTIVE = new Set(['Resolved', 'Expired']);

/** Hazard reports with a valid position become markers. Resolved and Expired reports are not drawn. */
export function reportMarkers(reports: readonly Raw[]): ReportMarkerInput[] {
  const out: ReportMarkerInput[] = [];
  for (const r of reports) {
    const id = str(r.report_id) ?? str(r.id);
    const lat = num(r.latitude);
    const lng = num(r.longitude);
    const status = str(r.verification_status) ?? 'Unverified';
    if (!id || lat === null || lng === null || Math.abs(lat) > 90 || Math.abs(lng) > 180 || INACTIVE.has(status)) continue;
    out.push({
      report_id: id,
      category: str(r.category) ?? 'other',
      verification_status: status,
      lng,
      lat,
      description: str(r.description),
      source_label: str(r.source_label),
      simulated: r.simulated === true,
      confirmations: num(r.confirmations) ?? 0,
      disputes: num(r.disputes) ?? 0,
      created_at: str(r.created_at),
      confirmed_closure: status === 'Officially_Verified',
    });
  }
  return out;
}

/** Incident area as a FeatureCollection; this is an incident area, never an official evacuation boundary. */
export function incidentBoundaryFC(incident: Incident | null): FC | null {
  const g = incident?.geographic_boundary as { type?: string; coordinates?: unknown } | null | undefined;
  if (!incident || !g || typeof g !== 'object' || typeof g.type !== 'string' || !Array.isArray(g.coordinates)) return null;
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: g as unknown as FC['features'][number]['geometry'],
        properties: {
          incident_id: incident.incident_id,
          label: `Incident area (not an official evacuation boundary)${incident.simulation_flag ? ' - SIMULATED' : ''}`,
          simulated: incident.simulation_flag === true,
        },
      },
    ],
  };
}

/** Route polylines for the map. Non-route outcomes (shelter in place, no route, ...) draw nothing. */
export function routeLines(response: RouteResponse | null, selectedId?: string | null): RouteLineInput[] {
  return toMapRoutes(response, selectedId).map((r) => ({
    id: r.id,
    kind: r.primary ? 'primary' : 'alternative',
    coordinates: r.feature.geometry.coordinates as LngLat[],
    label: r.feature.properties.label,
    properties: { route_id: r.id, route_type: r.routeType, selected: r.selected, label: r.feature.properties.label },
  }));
}

export function startPoint(p: LatLng | null): LngLat | null {
  return p ? [p.lng, p.lat] : null;
}

export function destinationInput(d: Destination | null): DestinationInput | null {
  if (!d) return null;
  return {
    id: 'destination',
    name: d.simulated ? 'SIMULATION DESTINATION (not an official shelter)' : 'Destination',
    lng: d.lng,
    lat: d.lat,
    simulated: d.simulated === true,
  };
}

// ------------------------------------------------------------------ restrictions from raw events
export interface ActiveRestriction {
  restriction_id: string;
  kind: string;
  edge_ids: string[];
  simulated: boolean;
  report_id: string | null;
  source_record_id: string | null;
}

/** Add or remove a restriction according to a raw realtime event (restriction_added / restriction_removed). */
export function applyRestrictionEvent(
  current: Record<string, ActiveRestriction>,
  event: Pick<RealtimeEvent, 'type' | 'payload'>,
): Record<string, ActiveRestriction> {
  const type = event.type.toLowerCase();
  if (!type.startsWith('restriction')) return current;
  const p = event.payload ?? {};
  const id = str(p.restriction_id) ?? str(p.id);
  if (!id) return current;
  if (type === 'restriction_removed') {
    if (!(id in current)) return current;
    const next = { ...current };
    delete next[id];
    return next;
  }
  const edgeIds = Array.isArray(p.edge_ids) ? p.edge_ids.filter((e): e is string => typeof e === 'string') : [];
  return {
    ...current,
    [id]: {
      restriction_id: id,
      kind: str(p.kind) ?? 'closure',
      edge_ids: edgeIds,
      simulated: p.simulated === true,
      report_id: str(p.report_id),
      source_record_id: str(p.source_record_id),
    },
  };
}

/** Closure lines for active restrictions whose edge geometry is known. */
export function closureFeatures(
  restrictions: Record<string, ActiveRestriction>,
  edgeCoordinates: Record<string, number[][]>,
): FC {
  const features: FC['features'] = [];
  for (const r of Object.values(restrictions)) {
    for (const edgeId of r.edge_ids) {
      const coords = edgeCoordinates[edgeId];
      if (!coords || coords.length < 2) continue;
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: coords },
        properties: {
          restriction_id: r.restriction_id,
          edge_id: edgeId,
          label: `Closed road segment (${r.kind})`,
          status: 'Closed',
          simulated: r.simulated,
          ...(r.report_id ? { report_id: r.report_id } : {}),
          ...(r.source_record_id ? { source_record_id: r.source_record_id } : {}),
        },
      });
    }
  }
  return { type: 'FeatureCollection', features };
}

// ------------------------------------------------------------------ map-overlays snapshot + live events
export type HazardKind = 'flood' | 'debris' | 'fire';
const HAZARD_KINDS: readonly HazardKind[] = ['flood', 'debris', 'fire'];

type FCLike = { features?: readonly unknown[] | null } | null | undefined;

/**
 * Structural subset of the `MapOverlays` payload (GET /api/incidents/{id}/map-overlays) that the
 * merge needs. The generated `MapOverlays` type is assignable to it.
 */
export interface OverlaySnapshotLike {
  incident_id: string;
  overlay_version: number;
  restrictions?: FCLike;
  hazards?: Partial<Record<HazardKind, FCLike>> | null;
}

/** A realtime event as the merge sees it; `incident_id` / `event_id` are optional. */
export type OverlayEventLike = Pick<RealtimeEvent, 'type' | 'payload'> & { incident_id?: string; event_id?: string };

/** Snapshot features with live restriction events applied on top. */
export interface MergedOverlays {
  incidentId: string | null;
  /** Highest overlay version reflected (snapshot version or a newer applied event). */
  overlayVersion: number | null;
  /** Restricted edge lines with geometry (snapshot features). */
  restrictions: FC;
  hazards: Record<HazardKind, FC>;
  /**
   * Restrictions added after the snapshot whose geometry is not known yet. Edge lines can be drawn
   * through `useEdgeCoordinates` + `closureFeatures` until the debounced refetch delivers them.
   */
  pending: Record<string, ActiveRestriction>;
  /** Edge ids of `pending`, deduplicated, in first-seen order. */
  pendingEdgeIds: string[];
}

const emptyFC = (): FC => ({ type: 'FeatureCollection', features: [] });

export const EMPTY_OVERLAYS: MergedOverlays = Object.freeze({
  incidentId: null,
  overlayVersion: null,
  restrictions: emptyFC(),
  hazards: { flood: emptyFC(), debris: emptyFC(), fire: emptyFC() },
  pending: {},
  pendingEdgeIds: [],
}) as MergedOverlays;

const copyFeatures = (c: FCLike): FC['features'] =>
  (Array.isArray(c?.features) ? c.features : []).filter(
    (f): f is FC['features'][number] => !!f && typeof f === 'object' && !!(f as { geometry?: unknown }).geometry,
  );

const restrictionIdOf = (f: FC['features'][number]): string | null => str(f.properties?.restriction_id);

/**
 * Merge the map-overlays snapshot with live `restriction_added` / `restriction_removed` events.
 *
 * - Only events with `payload.overlay_version > snapshot.overlay_version` are applied (older ones are
 *   already reflected in the snapshot). Without a snapshot every versioned event applies.
 * - Events are applied in `overlay_version` order and deduplicated, so duplicates and out-of-order
 *   delivery give the same result. Events without a numeric `overlay_version` are ignored (the
 *   debounced refetch picks the change up).
 * - Events whose `incident_id` differs from the snapshot's are ignored.
 * - `restriction_removed` drops every feature with that `restriction_id` (edge lines and areas).
 * - `restriction_added` for an id with no feature keeps its `edge_ids` pending.
 */
export function mergeRestrictions(
  snapshot: OverlaySnapshotLike | null | undefined,
  events: readonly OverlayEventLike[],
): MergedOverlays {
  const base = snapshot && typeof snapshot.overlay_version === 'number' ? snapshot.overlay_version : -Infinity;
  let restrictions = copyFeatures(snapshot?.restrictions);
  const hazards = Object.fromEntries(HAZARD_KINDS.map((k) => [k, copyFeatures(snapshot?.hazards?.[k])])) as Record<
    HazardKind,
    FC['features']
  >;

  const seen = new Set<string>();
  const applicable: { removed: boolean; id: string; version: number; event: OverlayEventLike }[] = [];
  for (const e of events) {
    const type = e.type.toLowerCase();
    if (type !== 'restriction_added' && type !== 'restriction_removed') continue;
    if (snapshot && e.incident_id && e.incident_id !== snapshot.incident_id) continue;
    const p = e.payload ?? {};
    const id = str(p.restriction_id) ?? str(p.id);
    const version = num(p.overlay_version);
    if (!id || version === null || !(version > base)) continue;
    const key = `${type}|${id}|${version}`;
    if (seen.has(key)) continue;
    seen.add(key);
    applicable.push({ removed: type === 'restriction_removed', id, version, event: e });
  }
  applicable.sort((a, b) => a.version - b.version);

  let pending: Record<string, ActiveRestriction> = {};
  let overlayVersion = Number.isFinite(base) ? base : null;
  for (const a of applicable) {
    overlayVersion = a.version;
    if (a.removed) {
      restrictions = restrictions.filter((f) => restrictionIdOf(f) !== a.id);
      for (const k of HAZARD_KINDS) hazards[k] = hazards[k].filter((f) => restrictionIdOf(f) !== a.id);
      if (a.id in pending) {
        pending = { ...pending };
        delete pending[a.id];
      }
      continue;
    }
    const drawn =
      restrictions.some((f) => restrictionIdOf(f) === a.id) ||
      HAZARD_KINDS.some((k) => hazards[k].some((f) => restrictionIdOf(f) === a.id));
    if (!drawn) pending = applyRestrictionEvent(pending, { type: 'restriction_added', payload: a.event.payload });
  }

  const pendingEdgeIds = [...new Set(Object.values(pending).flatMap((r) => r.edge_ids))];
  return {
    incidentId: snapshot?.incident_id ?? null,
    overlayVersion,
    restrictions: { type: 'FeatureCollection', features: restrictions },
    hazards: {
      flood: { type: 'FeatureCollection', features: hazards.flood },
      debris: { type: 'FeatureCollection', features: hazards.debris },
      fire: { type: 'FeatureCollection', features: hazards.fire },
    },
    pending,
    pendingEdgeIds,
  };
}

/** Restriction ids currently in force in a merged state (drawn features and pending ones). */
export function mergedRestrictionIds(merged: MergedOverlays): Set<string> {
  const ids = new Set<string>(Object.keys(merged.pending));
  const all = [merged.restrictions, merged.hazards.flood, merged.hazards.debris, merged.hazards.fire];
  for (const c of all) for (const f of c.features) {
    const id = restrictionIdOf(f);
    if (id) ids.add(id);
  }
  return ids;
}

/** Layer inputs from a merged state: SIMULATED hazard areas and restricted edge lines. */
export function overlayLayerInputs(merged: MergedOverlays): Required<Pick<MapLayerInputs, 'hazards' | 'restrictions'>> {
  return {
    hazards: { flood: merged.hazards.flood, debris: merged.hazards.debris, fire: merged.hazards.fire },
    restrictions: merged.restrictions,
  };
}

// ------------------------------------------------------------------ indoor start positions
export interface IndoorStart {
  startId: string;
  nodeId: string;
  lat: number;
  lng: number;
}

/** Start positions from the indoor GeoJSON (`role: start_position` features). */
export function indoorStarts(geo: { features?: { geometry?: { type?: string; coordinates?: unknown }; properties?: Raw }[] } | null | undefined): IndoorStart[] {
  const out: IndoorStart[] = [];
  for (const f of geo?.features ?? []) {
    const p = f.properties ?? {};
    if (p.role !== 'start_position' || f.geometry?.type !== 'Point' || !Array.isArray(f.geometry.coordinates)) continue;
    const [lng, lat] = f.geometry.coordinates as number[];
    const startId = str(p.start_id);
    const nodeId = str(p.node_id);
    if (startId && nodeId && Number.isFinite(lat) && Number.isFinite(lng)) out.push({ startId, nodeId, lat, lng });
  }
  return out;
}

/** First exit point of the indoor GeoJSON; routing requires a destination, the exits are the targets. */
export function indoorExit(geo: { features?: { geometry?: { type?: string; coordinates?: unknown }; properties?: Raw }[] } | null | undefined): LatLng | null {
  for (const f of geo?.features ?? []) {
    if (f.properties?.role === 'exit' && f.geometry?.type === 'Point' && Array.isArray(f.geometry.coordinates)) {
      const [lng, lat] = f.geometry.coordinates as number[];
      if (Number.isFinite(lat) && Number.isFinite(lng)) return { lat, lng };
    }
  }
  return null;
}

// ------------------------------------------------------------------ incident choice
/** Default incident: in demo mode the flood scenario, otherwise the first active incident. */
export function pickDefaultIncident(incidents: readonly Incident[], demoMode: boolean): Incident | null {
  const active = incidents.filter((i) => i.status === 'active');
  if (demoMode) {
    const flood = active.find((i) => i.incident_type === 'flood' && i.simulation_flag);
    if (flood) return flood;
  }
  return active[0] ?? null;
}
