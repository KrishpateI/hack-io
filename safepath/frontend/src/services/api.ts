/**
 * Thin typed fetch wrapper over the generated OpenAPI types (Req 42.2, 43.1).
 *
 * - Types come from `api.generated.ts` (`npm run gen:api`, source: docs/openapi.json).
 * - Bearer token comes from a token store (memory + sessionStorage).
 * - Non-2xx responses are parsed from the error envelope into a typed `ApiError`.
 * - A guest session is bootstrapped via POST /api/auth/guest; a 401 re-bootstraps once.
 */
import type { components, paths } from './api.generated';

// ---------------------------------------------------------------- errors

export interface ApiFieldError {
  field: string;
  problem: string;
}

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    fields?: ApiFieldError[] | null;
    retry_after_seconds?: number | null;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: ApiFieldError[];
  readonly retryAfterSeconds: number | null;

  constructor(status: number, code: string, message: string, fields: ApiFieldError[] = [], retryAfterSeconds: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function parseEnvelope(status: number, body: unknown, statusText: string): ApiError {
  const err = (body as Partial<ErrorEnvelope> | null)?.error;
  if (err && typeof err === 'object' && typeof err.code === 'string') {
    return new ApiError(
      status,
      err.code,
      typeof err.message === 'string' ? err.message : statusText,
      Array.isArray(err.fields) ? err.fields : [],
      typeof err.retry_after_seconds === 'number' ? err.retry_after_seconds : null,
    );
  }
  return new ApiError(status, 'http_error', statusText || `HTTP ${status}`);
}

// ---------------------------------------------------------------- token store

const TOKEN_KEY = 'safepath.token';

export interface TokenStore {
  get(): string | null;
  set(token: string | null): void;
}

let memoryToken: string | null = null;

/** Default store: memory first, mirrored to sessionStorage so a reload keeps the session. */
export const tokenStore: TokenStore = {
  get() {
    if (memoryToken) return memoryToken;
    try {
      memoryToken = globalThis.sessionStorage?.getItem(TOKEN_KEY) ?? null;
    } catch {
      memoryToken = null;
    }
    return memoryToken;
  },
  set(token) {
    memoryToken = token;
    try {
      if (token) globalThis.sessionStorage?.setItem(TOKEN_KEY, token);
      else globalThis.sessionStorage?.removeItem(TOKEN_KEY);
    } catch {
      /* sessionStorage unavailable: memory only */
    }
  },
};

export function apiBaseUrl(): string {
  return ((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '').replace(/\/+$/, '');
}

// ---------------------------------------------------------------- guest bootstrap

export interface SessionInfo {
  token: string;
  token_type: string;
  expires_at: string;
  subject: string;
  role: string;
}

let bootstrapping: Promise<string> | null = null;

/** Create a guest session (deduplicated across concurrent callers) and store its token. */
export function bootstrapGuestSession(): Promise<string> {
  if (!bootstrapping) {
    bootstrapping = (async () => {
      const res = await fetch(`${apiBaseUrl()}/api/auth/guest`, {
        method: 'POST',
        headers: { Accept: 'application/json' },
      });
      const body = await readBody(res);
      if (!res.ok) throw parseEnvelope(res.status, body, res.statusText);
      const token = (body as SessionInfo | null)?.token;
      if (!token) throw new ApiError(res.status, 'bad_session', 'Guest session response had no token');
      tokenStore.set(token);
      return token;
    })().finally(() => {
      bootstrapping = null;
    });
  }
  return bootstrapping;
}

export async function ensureSession(): Promise<string> {
  return tokenStore.get() ?? bootstrapGuestSession();
}

// ---------------------------------------------------------------- core request

type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
}

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function buildUrl(path: string, query?: Query): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null) qs.set(k, String(v));
  }
  const s = qs.toString();
  return `${apiBaseUrl()}${path}${s ? `?${s}` : ''}`;
}

async function send(method: string, path: string, opts: RequestOptions, token: string): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', Authorization: `Bearer ${token}` };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  return fetch(buildUrl(path, opts.query), {
    method,
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  });
}

/** JSON request with bearer auth, envelope error parsing and one guest re-bootstrap on 401. */
export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  let res = await send(method, path, opts, await ensureSession());
  if (res.status === 401) {
    tokenStore.set(null);
    res = await send(method, path, opts, await bootstrapGuestSession());
  }
  const body = await readBody(res);
  if (!res.ok) throw parseEnvelope(res.status, body, res.statusText);
  return body as T;
}

export const getJson = <T>(path: string, query?: Query, signal?: AbortSignal) =>
  request<T>('GET', path, { query, signal });
export const postJson = <T>(path: string, body?: unknown, signal?: AbortSignal) =>
  request<T>('POST', path, { body, signal });

// ---------------------------------------------------------------- generated-type helpers

type Schemas = components['schemas'];
type JsonContent<R> = R extends { content: { 'application/json': infer J } } ? J : never;
/** Success (200/201) JSON body of operation `M` on path `P`. */
export type ResponseOf<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends { responses: infer R }
  ? JsonContent<R extends { 200: infer A } ? A : R extends { 201: infer B } ? B : never>
  : never;
/** Query parameters of operation `M` on path `P`. */
export type QueryOf<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends {
  parameters: { query?: infer Q };
}
  ? NonNullable<Q>
  : never;

export type RouteResponse = Schemas['RouteResponse'];
export type Incident = Schemas['Incident'];
export type SourceRecord = Schemas['SourceRecord'];

// Request bodies for these endpoints are not modelled in the OpenAPI schema yet (backend uses
// untyped bodies), so they are accepted as plain JSON objects.
export type JsonObject = Record<string, unknown>;

// ---------------------------------------------------------------- typed endpoint helpers

export const calculateRoute = (body: JsonObject, signal?: AbortSignal) =>
  postJson<RouteResponse>('/api/routes/calculate', body, signal);

export const listIncidents = (signal?: AbortSignal) => getJson<Incident[]>('/api/incidents', undefined, signal);
export const getIncident = (incidentId: string, signal?: AbortSignal) =>
  getJson<Incident>(`/api/incidents/${encodeURIComponent(incidentId)}`, undefined, signal);

export const listReports = (query: QueryOf<'/api/reports', 'get'>, signal?: AbortSignal) =>
  getJson<ResponseOf<'/api/reports', 'get'>>('/api/reports', query, signal);
export const createReport = (body: JsonObject, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/reports', 'post'>>('/api/reports', body, signal);

const reportPath = (reportId: string, action: 'corroborate' | 'dispute' | 'moderate') =>
  `/api/reports/${encodeURIComponent(reportId)}/${action}` as const;

export const corroborateReport = (reportId: string, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/reports/{report_id}/corroborate', 'post'>>(reportPath(reportId, 'corroborate'), undefined, signal);
export const disputeReport = (reportId: string, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/reports/{report_id}/dispute', 'post'>>(reportPath(reportId, 'dispute'), undefined, signal);
export const moderateReport = (reportId: string, body: JsonObject, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/reports/{report_id}/moderate', 'post'>>(reportPath(reportId, 'moderate'), body, signal);

export const listMessages = (query: QueryOf<'/api/messages', 'get'>, signal?: AbortSignal) =>
  getJson<ResponseOf<'/api/messages', 'get'>>('/api/messages', query, signal);
export const postMessage = (body: JsonObject, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/messages', 'post'>>('/api/messages', body, signal);

export const listCheckins = (query: QueryOf<'/api/checkins', 'get'>, signal?: AbortSignal) =>
  getJson<ResponseOf<'/api/checkins', 'get'>>('/api/checkins', query, signal);
export const postCheckin = (body: JsonObject, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/checkins', 'post'>>('/api/checkins', body, signal);

export const listSources = (query?: QueryOf<'/api/sources', 'get'>, signal?: AbortSignal) =>
  getJson<SourceRecord[]>('/api/sources', query, signal);
export const getGraphStatus = (signal?: AbortSignal) =>
  getJson<ResponseOf<'/api/graph/status', 'get'>>('/api/graph/status', undefined, signal);
export const getRegionMetadata = (signal?: AbortSignal) =>
  getJson<ResponseOf<'/api/regions/chicago/metadata', 'get'>>('/api/regions/chicago/metadata', undefined, signal);

/** Catch-up fetch of incident events (WebSocket/SSE streams are not part of OpenAPI). */
export const listEvents = (query: QueryOf<'/api/events', 'get'>, signal?: AbortSignal) =>
  getJson<ResponseOf<'/api/events', 'get'>>('/api/events', query, signal);

export type OfficialNotice = Schemas['OfficialNotice'];
export const listNotices = (incidentId: string, signal?: AbortSignal) =>
  getJson<OfficialNotice[]>('/api/notices', { incident_id: incidentId }, signal);

/** Hazard areas and restricted edges for one incident (public, read-only, `Cache-Control: no-store`). */
export type MapOverlays = Schemas['MapOverlays'];
export const getMapOverlays = (incidentId: string, signal?: AbortSignal) =>
  getJson<MapOverlays>(`/api/incidents/${encodeURIComponent(incidentId)}/map-overlays`, undefined, signal);

/** Ask for a presigned upload target for a JPEG/PNG photo (<= 10 MB). */
export const presignPhoto = (body: { content_type: 'image/jpeg' | 'image/png'; size: number }, signal?: AbortSignal) =>
  postJson<ResponseOf<'/api/photos/presign', 'post'>>('/api/photos/presign', body, signal);

// ---------------------------------------------------------------- demo / admin console (Req 37)
export type DemoBody = Record<string, unknown>;
const scen = (id: string) => `/api/demo/scenarios/${encodeURIComponent(id)}`;
export const demoStatus = (signal?: AbortSignal) => getJson<DemoBody>('/api/demo/status', undefined, signal);
export const demoCreateSession = (body: { kind: 'guest' | 'user' | 'moderator'; actor?: 'A' | 'B' | 'C' | null }, signal?: AbortSignal) =>
  postJson<DemoBody>('/api/demo/sessions', body, signal);
export const demoListScenarios = (signal?: AbortSignal) => getJson<DemoBody>('/api/demo/scenarios', undefined, signal);
export const demoStart = (scenarioId: string, signal?: AbortSignal) =>
  postJson<DemoBody>(`${scen(scenarioId)}/start`, undefined, signal);
export const demoApplyStep = (scenarioId: string, stepId: string, signal?: AbortSignal) =>
  postJson<DemoBody>(`${scen(scenarioId)}/steps/${encodeURIComponent(stepId)}/apply`, undefined, signal);
export const demoAdvance = (scenarioId: string, signal?: AbortSignal) =>
  postJson<DemoBody>(`${scen(scenarioId)}/advance`, undefined, signal);
export const demoAct = (
  scenarioId: string,
  body: {
    actor: 'A' | 'B' | 'C' | 'moderator';
    action: 'report' | 'corroborate' | 'dispute' | 'message' | 'confirm' | 'resolve';
    content?: string | null;
    report_id?: string | null;
    message_type?: string;
  },
  signal?: AbortSignal,
) => postJson<DemoBody>(`${scen(scenarioId)}/act`, body, signal);
export const demoReset = (scenarioId: string, signal?: AbortSignal) =>
  postJson<DemoBody>(`${scen(scenarioId)}/reset`, undefined, signal);
export const demoResetAll = (signal?: AbortSignal) => postJson<DemoBody>('/api/demo/reset-all', undefined, signal);
export const demoAddClosure = (
  body: { incident_id: string; edge_ids?: string[]; geometry?: Record<string, unknown> | null; kind?: 'closure' | 'impassable_polygon' },
  signal?: AbortSignal,
) => postJson<DemoBody>('/api/demo/closures', body, signal);
export const demoRemoveClosure = (restrictionId: string, incidentId: string, signal?: AbortSignal) =>
  request<DemoBody>('DELETE', `/api/demo/closures/${encodeURIComponent(restrictionId)}`, { query: { incident_id: incidentId }, signal });

// ---------------------------------------------------------------- session profile (Req 22, 34.1)
export interface MeInfo {
  subject: string;
  role: string;
  is_guest: boolean;
  display_name?: string | null;
  expires_at?: string | null;
}
/** GET /api/auth/me: who the current bearer token belongs to (role, alias, guest/user). */
export const getMe = (signal?: AbortSignal) => getJson<MeInfo>('/api/auth/me', undefined, signal);
