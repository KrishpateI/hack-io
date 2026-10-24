/**
 * Client for the public, read-only graph edge endpoints (task 13.4, Req 35.1, 35.2):
 *   GET /api/graph/edges/{edge_id}
 *   GET /api/graph/edges/near?lat=&lon=&radius=
 * Uses its own fetch (like layerData.ts) because graph edges are not a map layer.
 */
import { apiBaseUrl } from '../../services/api';

export type Maybe<T> = T | 'Unknown';

export interface EdgeCause {
  kind: string;
  id: string;
  source_record_id?: string | null;
  simulated?: boolean;
}

export interface EdgeReport {
  report_id: string;
  category?: string;
  description?: string;
  verification_status: string;
  status_label?: string;
  source_label?: string;
  confirmations?: number;
  disputes?: number;
  created_at?: string | null;
  expires_at?: string | null;
  simulated?: boolean;
}

/** Edge attributes as served by the backend; missing values are the string "Unknown". */
export interface EdgeDetail {
  edge_id: string;
  street_name?: string | null;
  road_type?: Maybe<string>;
  bridge_flag?: Maybe<boolean>;
  tunnel_flag?: Maybe<boolean>;
  infrastructure_level?: string;
  pedestrian_access?: Maybe<boolean>;
  mobility_accessibility?: string;
  elevation_start?: Maybe<number>;
  elevation_end?: Maybe<number>;
  elevation_confidence?: string;
  current_closure_status?: string;
  closure_verification?: string | null;
  source?: string;
  source_ids?: Record<string, string>;
  data_timestamp?: string | null;
  classification?: string;
  distance_m?: number;
  geometry_geojson?: { type: 'LineString'; coordinates: number[][] };
  restrictions?: { incident_id: string | null; available: boolean; causes: EdgeCause[]; error?: string };
  reports?: EdgeReport[];
}

export interface EdgeQuery {
  incidentId?: string | null;
  signal?: AbortSignal;
}

function url(path: string, params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) qs.set(k, String(v));
  const s = qs.toString();
  return `${apiBaseUrl()}${path}${s ? `?${s}` : ''}`;
}

async function getJson<T>(u: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(u, { headers: { Accept: 'application/json' }, signal });
  if (!res.ok) throw new Error(`Edge request failed (${res.status})`);
  return (await res.json()) as T;
}

export const edgeUrl = (edgeId: string, incidentId?: string | null) =>
  url(`/api/graph/edges/${encodeURIComponent(edgeId)}`, { incident_id: incidentId });

export const edgesNearUrl = (lat: number, lon: number, radius: number, limit: number, incidentId?: string | null) =>
  url('/api/graph/edges/near', { lat, lon, radius, limit, incident_id: incidentId });

export const fetchEdge = (edgeId: string, q: EdgeQuery = {}): Promise<EdgeDetail> =>
  getJson<EdgeDetail>(edgeUrl(edgeId, q.incidentId), q.signal);

/** Nearest edges within `radius` meters of the point, nearest first. */
export async function fetchEdgesNear(
  lat: number,
  lon: number,
  opts: EdgeQuery & { radius?: number; limit?: number } = {},
): Promise<EdgeDetail[]> {
  const body = await getJson<{ edges?: EdgeDetail[] }>(
    edgesNearUrl(lat, lon, opts.radius ?? 25, opts.limit ?? 5, opts.incidentId),
    opts.signal,
  );
  return Array.isArray(body.edges) ? body.edges : [];
}
