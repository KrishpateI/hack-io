import { calculateRoute, type JsonObject, type RouteResponse } from './api';
import type { RealtimeEvent } from './eventStore';

export type RouteT = RouteResponse['routes'][number];

/** Coalescing window: all qualifying events inside it produce one request (Req 16.4, 16.7). */
export const RECALC_WINDOW_MS = 1000;
export const RECALC_FAILED_MESSAGE = 'Recalculation failed, showing previous route as possibly outdated.';
export const ROUTE_INVALID_MESSAGE = 'Route no longer valid';

// ------------------------------------------------------------------ pure coalescing reducer

export interface CoalesceState {
  /** A trigger arrived and no request has been issued for it yet. */
  pending: boolean;
  /** Time the current window closes, or null when no window is open. */
  dueAt: number | null;
  /** Last request id handed out (monotonic). */
  seq: number;
  /** Id of the only request whose response may be applied. */
  latest: number;
}

export const initialCoalesce: CoalesceState = { pending: false, dueAt: null, seq: 0, latest: 0 };

/** Register a qualifying event. `schedule` is the delay to arm a timer for (only when a window opens). */
export function coalesceTrigger(
  s: CoalesceState,
  now: number,
  windowMs = RECALC_WINDOW_MS,
): { state: CoalesceState; schedule: number | null } {
  if (s.dueAt !== null) return { state: { ...s, pending: true }, schedule: null };
  return { state: { ...s, pending: true, dueAt: now + windowMs }, schedule: windowMs };
}

/** Window closed: issue one request (new id) if anything is pending. */
export function coalesceFire(s: CoalesceState): { state: CoalesceState; issue: number | null } {
  if (!s.pending) return { state: { ...s, dueAt: null }, issue: null };
  const id = s.seq + 1;
  return { state: { pending: false, dueAt: null, seq: id, latest: id }, issue: id };
}

/** An older response must never overwrite a newer one. */
export const coalesceAccepts = (s: CoalesceState, id: number): boolean => id === s.latest;

// ------------------------------------------------------------------ event relevance

const asStrings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
const isOfficial = (v: unknown): boolean => typeof v === 'string' && v.toLowerCase() === 'officially_verified';

/**
 * Does this event warrant a recalculation of the active route?
 * - restriction add/remove/change: when its edge ids intersect the route's, or when the edge set is
 *   unknown or the change removes a restriction (a removal can open a better route / flip the outcome).
 * - report status change to/from Officially_Verified.
 */
export function eventAffectsRoute(
  event: Pick<RealtimeEvent, 'type' | 'incident_id' | 'payload'>,
  incidentId: string,
  route: RouteT | null,
  outcomeIsRoute = true,
): boolean {
  if (event.incident_id && event.incident_id !== incidentId) return false;
  const t = event.type.toLowerCase();
  const p = event.payload ?? {};
  if (t.startsWith('restriction')) {
    if (!route || !outcomeIsRoute) return true; // a removal may make a route possible again
    if (t === 'restriction_removed') return true;
    const edges = asStrings(p.edge_ids);
    if (edges.length === 0) return true;
    const own = new Set(route.edge_penalties.map((e) => e.edge_id));
    return edges.some((e) => own.has(e)) || route.restrictions_considered.some((r) => r.id === p.restriction_id);
  }
  if (t === 'report_status_changed') {
    return isOfficial(p.verification_status) || isOfficial(p.previous_status);
  }
  return false;
}

// ------------------------------------------------------------------ change explanation

function fmtDistance(m: number): string {
  const a = Math.abs(m);
  return a >= 1000 ? `${(a / 1000).toFixed(1)} km` : `${Math.round(a)} m`;
}
function fmtDuration(s: number): string {
  const a = Math.round(Math.abs(s) / 60);
  return a < 1 ? `${Math.round(Math.abs(s))} s` : `${a} min`;
}

/** Human explanation of what changed between two routes (uses restrictions_considered + reason_summary). */
export function explainChange(prev: RouteT | null, next: RouteT, response?: RouteResponse): string {
  const parts: string[] = [];
  const before = new Map((prev?.restrictions_considered ?? []).map((r) => [r.id, r]));
  const after = new Map(next.restrictions_considered.map((r) => [r.id, r]));
  const added = [...after.values()].filter((r) => !before.has(r.id));
  const removed = [...before.values()].filter((r) => !after.has(r.id));
  for (const r of added) {
    const src = r.source_record_id ? `, source ${r.source_record_id}` : '';
    parts.push(`New ${r.kind} (${r.id}${src})${r.simulated ? ' [simulated]' : ''} is now avoided.`);
  }
  for (const r of removed) parts.push(`${r.kind} (${r.id}) no longer applies.`);
  if (prev) {
    const dd = next.distance_meters - prev.distance_meters;
    const dt = next.estimated_duration_seconds - prev.estimated_duration_seconds;
    if (Math.round(dd) === 0 && Math.round(dt) === 0) {
      parts.push('Distance and time are unchanged.');
    } else {
      parts.push(
        `Distance ${dd >= 0 ? '+' : '-'}${fmtDistance(dd)} (now ${fmtDistance(next.distance_meters)}), ` +
          `time ${dt >= 0 ? '+' : '-'}${fmtDuration(dt)} (now ${fmtDuration(next.estimated_duration_seconds)}).`,
      );
    }
  }
  if (next.reason_summary) parts.push(next.reason_summary);
  if (response?.label ?? next.label) parts.push(`(${response?.label ?? next.label})`);
  return parts.join(' ');
}

// ------------------------------------------------------------------ controller

export type RecalcStatus = 'idle' | 'updating' | 'failed' | 'no_route';

export interface RouteRecalcSnapshot {
  route: RouteT | null;
  previousRoute: RouteT | null;
  change: string | null;
  status: RecalcStatus;
  /** Old route is on screen but being replaced. */
  updating: boolean;
  /** Message to display on failure / non-route outcome. */
  message: string | null;
  policyOutcome: RouteResponse['policy_outcome'] | null;
  /** Realtime channel is stale/offline: road conditions must not be presented as current. */
  stale: boolean;
  calculatedAt: string | null;
}

export interface RouteRecalcDeps {
  calculate?: (body: JsonObject, signal?: AbortSignal) => Promise<RouteResponse>;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
  now?: () => number;
  windowMs?: number;
}

const emptySnap: RouteRecalcSnapshot = {
  route: null, previousRoute: null, change: null, status: 'idle', updating: false,
  message: null, policyOutcome: null, stale: false, calculatedAt: null,
};

function pickRoute(res: RouteResponse, like: RouteT | null): RouteT | null {
  if (res.policy_outcome !== 'ROUTE_OPTIONS_AVAILABLE') return null;
  const usable = res.routes.filter((r) => r.geometry);
  if (usable.length === 0) return null;
  return usable.find((r) => like && r.route_type === like.route_type) ?? usable[0];
}

/** Framework-agnostic recalculation manager. */
export class RouteRecalcController {
  private snap: RouteRecalcSnapshot = emptySnap;
  private listeners = new Set<() => void>();
  private co: CoalesceState = initialCoalesce;
  private timer: unknown = null;
  private abort: AbortController | null = null;
  private incidentId = '';
  private body: JsonObject = {};
  private readonly calc: NonNullable<RouteRecalcDeps['calculate']>;
  private readonly setT: NonNullable<RouteRecalcDeps['setTimer']>;
  private readonly clearT: NonNullable<RouteRecalcDeps['clearTimer']>;
  private readonly now: () => number;
  private readonly windowMs: number;

  constructor(deps: RouteRecalcDeps = {}) {
    this.calc = deps.calculate ?? calculateRoute;
    this.setT = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearT = deps.clearTimer ?? ((id) => clearTimeout(id as ReturnType<typeof setTimeout>));
    this.now = deps.now ?? (() => Date.now());
    this.windowMs = deps.windowMs ?? RECALC_WINDOW_MS;
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = (): RouteRecalcSnapshot => this.snap;
  private set(patch: Partial<RouteRecalcSnapshot>): void {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }

  /** Set the active route (initial calculation result) and the request body to reuse. */
  setActive(incidentId: string, body: JsonObject, response: RouteResponse | null): void {
    this.cancel();
    this.incidentId = incidentId;
    this.body = body;
    const route = response ? pickRoute(response, null) : null;
    this.set({
      route, previousRoute: null, change: null, updating: false,
      status: response && !route ? 'no_route' : 'idle',
      message: response && !route ? `${ROUTE_INVALID_MESSAGE}. ${response.explanation}` : null,
      policyOutcome: response?.policy_outcome ?? null,
      calculatedAt: response?.calculated_at ?? null,
    });
  }

  setRealtimeStale(stale: boolean): void {
    if (stale !== this.snap.stale) this.set({ stale });
  }

  /** Feed a realtime event; qualifying events are coalesced into one request per window. */
  handleEvent(event: Pick<RealtimeEvent, 'type' | 'incident_id' | 'payload'>): void {
    if (!this.incidentId) return;
    if (!eventAffectsRoute(event, this.incidentId, this.snap.route, this.snap.policyOutcome === 'ROUTE_OPTIONS_AVAILABLE' || this.snap.policyOutcome === null)) return;
    this.requestRecalc();
  }

  requestRecalc(): void {
    const { state, schedule } = coalesceTrigger(this.co, this.now(), this.windowMs);
    this.co = state;
    if (schedule !== null) this.timer = this.setT(() => void this.fire(), schedule);
  }

  private async fire(): Promise<void> {
    this.timer = null;
    const { state, issue } = coalesceFire(this.co);
    this.co = state;
    if (issue === null) return;
    this.abort?.abort(); // stale in-flight request
    const ctl = new AbortController();
    this.abort = ctl;
    this.set({ updating: true, status: this.snap.status === 'no_route' ? 'no_route' : 'updating' });
    try {
      const res = await this.calc(this.body, ctl.signal);
      if (!coalesceAccepts(this.co, issue) || ctl.signal.aborted) return;
      this.apply(res);
    } catch {
      if (!coalesceAccepts(this.co, issue) || ctl.signal.aborted) return;
      this.set({ updating: false, status: 'failed', message: RECALC_FAILED_MESSAGE });
    } finally {
      if (this.abort === ctl) this.abort = null;
    }
  }

  private apply(res: RouteResponse): void {
    const old = this.snap.route ?? this.snap.previousRoute;
    const next = pickRoute(res, old);
    if (!next) {
      this.set({
        route: null, previousRoute: this.snap.route ?? this.snap.previousRoute, updating: false, status: 'no_route',
        policyOutcome: res.policy_outcome, calculatedAt: res.calculated_at,
        message: `${ROUTE_INVALID_MESSAGE}. ${res.explanation}`.trim(),
        change: `${ROUTE_INVALID_MESSAGE}: ${res.policy_outcome.replace(/_/g, ' ').toLowerCase()}. ${res.explanation}`.trim(),
      });
      return;
    }
    this.set({
      route: next, previousRoute: this.snap.route, updating: false, status: 'idle', message: null,
      policyOutcome: res.policy_outcome, calculatedAt: res.calculated_at,
      change: explainChange(this.snap.route, next, res),
    });
  }

  private cancel(): void {
    if (this.timer !== null) this.clearT(this.timer);
    this.timer = null;
    this.abort?.abort();
    this.abort = null;
    this.co = { ...initialCoalesce, seq: this.co.seq, latest: this.co.seq };
  }

  stop(): void {
    this.cancel();
  }
}
