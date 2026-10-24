/**
 * Pure builders that turn API/realtime data into the plain inputs the map layers accept.
 * Nothing here fabricates data: missing coordinates or geometry simply produce no feature.
 */
import type { Incident, RouteResponse } from './api';
import type { RealtimeEvent } from './eventStore';
import { toMapRoutes } from '../components/route/routeGeometry';
import type { Destination, LatLng } from '../components/sidebar/StartDestinationPicker';
import type { DestinationInput, FC, LngLat, ReportMarkerInput, RouteLineInput } from '../maps/layers/types';

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
