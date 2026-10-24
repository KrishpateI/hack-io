/**
 * Pure helpers for the Demo_Admin_Console (Req 40). Everything here is SIMULATED demo data.
 * Backend demo responses are loosely typed (DemoBody), so these helpers narrow them defensively.
 */
import { ApiError } from '../../services/api';
import type { RouteResponse } from '../../services/api';

export const DEMO_ONLY_NOTE = 'Demo only, not real events.';
export const MAIN_SCENARIOS = ['flood_loop', 'earthquake_debris', 'blocked_stairwell'] as const;
export const STAIRWELL_ID = 'blocked_stairwell';
export const OBSERVE_LIMIT_SECONDS = 5;
export const CORROBORATED_FALLBACK = 'Corroborated by community \u2014 not officially verified';
export const VERIFIED_TEXT = 'Officially_Verified (SIMULATED moderator verification)';

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

export interface DemoStatusInfo {
  demoMode: boolean;
  notice: string | null;
  graphUsed: string | null;
  seedState: string | null;
  moderatorSessionsAvailable: boolean;
}

export function parseStatus(body: unknown): DemoStatusInfo {
  const b = isObj(body) ? body : {};
  return {
    demoMode: b.demo_mode === true,
    notice: str(b.notice),
    graphUsed: str(b.graph_used),
    seedState: str(b.seed_state),
    moderatorSessionsAvailable: b.moderator_sessions_available === true,
  };
}

export interface DemoStep {
  id: string;
  kind: string;
  title: string;
  status: 'pending' | 'applied';
  actor: string | null;
}
export interface DemoScenario {
  id: string;
  name: string;
  incidentId: string;
  optional: boolean;
  seeded: boolean;
  pristine: boolean;
  nextStep: string | null;
  graphUsed: string | null;
  steps: DemoStep[];
}

function parseStep(s: unknown): DemoStep | null {
  if (!isObj(s) || typeof s.id !== 'string') return null;
  const kind = str(s.kind) ?? 'step';
  return {
    id: s.id,
    kind,
    title: str(s.title) ?? str(s.description) ?? str(s.label) ?? s.id.replace(/_/g, ' '),
    status: s.status === 'applied' ? 'applied' : 'pending',
    actor: str(s.actor),
  };
}

export function parseScenarios(body: unknown): DemoScenario[] {
  const list = isObj(body) && Array.isArray(body.scenarios) ? body.scenarios : [];
  const out: DemoScenario[] = [];
  for (const raw of list) {
    if (!isObj(raw) || typeof raw.scenario_id !== 'string') continue;
    const graph = isObj(raw.graph) ? raw.graph : null;
    out.push({
      id: raw.scenario_id,
      name: str(raw.name) ?? raw.scenario_id.replace(/_/g, ' '),
      incidentId: str(raw.incident_id) ?? '',
      optional: raw.optional === true,
      seeded: raw.seeded !== false,
      pristine: raw.pristine === true,
      nextStep: str(raw.next_step),
      graphUsed: graph ? str(graph.used) : null,
      steps: (Array.isArray(raw.steps) ? raw.steps : []).map(parseStep).filter((s): s is DemoStep => s !== null),
    });
  }
  return sortScenarios(out);
}

/** The three main scenarios first (in fixed order), then optional samples (tornado, fire...). */
export function sortScenarios<T extends { id: string; optional?: boolean }>(list: T[]): T[] {
  const rank = (s: T) => {
    const i = (MAIN_SCENARIOS as readonly string[]).indexOf(s.id);
    return i >= 0 ? i : s.optional ? 100 : 50;
  };
  return [...list].sort((a, b) => rank(a) - rank(b));
}

export function graphLabel(used: string | null | undefined): string {
  if (used === 'real') return 'Real routing graph data';
  if (used === 'simulated_fixture') return 'SIMULATED fixture graph (not real road data)';
  return 'Routing graph: unknown';
}

// ------------------------------------------------------------------ errors

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return `Forbidden (403): moderator-only action. Start a moderator demo session first. ${e.message}`;
    if (e.status === 401) return `Not signed in (401): start a moderator demo session first. ${e.message}`;
    return `${e.code} (HTTP ${e.status}): ${e.message}`;
  }
  return e instanceof Error ? e.message : String(e);
}

// ------------------------------------------------------------------ reset

export interface ResetOutcome {
  changed: boolean;
  elapsedMs: number | null;
  withinLimit: boolean | null;
}

export function parseReset(body: unknown): ResetOutcome {
  const b = isObj(body) ? body : {};
  return {
    changed: b.changed === true,
    elapsedMs: typeof b.elapsed_ms === 'number' ? b.elapsed_ms : null,
    withinLimit: typeof b.within_limit === 'boolean' ? b.within_limit : null,
  };
}

export function resetMessage(r: ResetOutcome, scope = 'Scenario'): string {
  if (!r.changed) return `${scope} is already pristine: nothing to reset (no-op).`;
  const t = r.elapsedMs === null ? '' : ` in ${(r.elapsedMs / 1000).toFixed(1)} s`;
  const lim = r.withinLimit === null ? '' : r.withinLimit ? ' (within the 30 s limit)' : ' (took longer than the 30 s limit)';
  return `${scope} restored to its seeded state${t}${lim}.`;
}

// ------------------------------------------------------------------ actor result text

export type ActorAction = 'report' | 'corroborate' | 'dispute' | 'message' | 'confirm' | 'resolve';

/** Status text for an actor action result (what the user would see on the report). */
export function actResultText(action: ActorAction, body: unknown): string {
  const b = isObj(body) ? body : {};
  if (action === 'message') {
    const t = str(b.message_type)?.replace(/_/g, ' ') ?? 'message';
    return `Sent ${t} (SIMULATED message, not delivered to any real responder).`;
  }
  const report = isObj(b.report) ? b.report : null;
  const status = report ? str(report.verification_status) : null;
  const label = report ? str(report.status_label) : null;
  if (status === 'Officially_Verified') return VERIFIED_TEXT;
  if (status === 'Corroborated') return label ?? CORROBORATED_FALLBACK;
  const base = label ?? status ?? 'Status unknown';
  return action === 'resolve' ? `${base} (resolved by SIMULATED moderator)` : base;
}

export function reportIdOf(body: unknown): string | null {
  const r = isObj(body) && isObj(body.report) ? body.report : null;
  return r ? (str(r.report_id) ?? str(r.id)) : null;
}

export type BodiesByActor = Record<string, Record<string, unknown>>;

export function bodiesByActorOf(body: unknown): BodiesByActor | null {
  if (!isObj(body)) return null;
  const direct = body.bodies_by_actor ?? body.route_request;
  if (isObj(direct)) {
    const out: BodiesByActor = {};
    for (const [k, v] of Object.entries(direct)) if (isObj(v)) out[k] = v;
    return Object.keys(out).length ? out : null;
  }
  return null;
}

export interface ClientAction {
  do: string;
  body: Record<string, unknown> | null;
  bodiesByActor: BodiesByActor | null;
}

export function parseClientAction(body: unknown): ClientAction | null {
  const ca = isObj(body) ? body.client_action : null;
  if (!isObj(ca) || typeof ca.do !== 'string') return null;
  return { do: ca.do, body: isObj(ca.body) ? ca.body : null, bodiesByActor: bodiesByActorOf(ca) };
}

// ------------------------------------------------------------------ route summary

export type RouteT = RouteResponse['routes'][number];

export interface RouteSummary {
  outcome: string;
  line: string;
  edgeIds: string[];
  route: RouteT | null;
}

export function summarizeRoute(res: RouteResponse): RouteSummary {
  const route = res.routes.find((r) => r.geometry) ?? res.routes[0] ?? null;
  const edgeIds = (route?.edge_penalties ?? []).map((p) => p.edge_id);
  if (res.policy_outcome !== 'ROUTE_OPTIONS_AVAILABLE' || !route) {
    return { outcome: res.policy_outcome, line: `No route: ${res.policy_outcome.replace(/_/g, ' ').toLowerCase()}. ${res.explanation}`.trim(), edgeIds, route: null };
  }
  const km = (route.distance_meters / 1000).toFixed(2);
  const min = Math.max(1, Math.round(route.estimated_duration_seconds / 60));
  const n = route.restrictions_considered.length;
  return {
    outcome: res.policy_outcome,
    line: `${route.route_type ?? 'route'}: ${km} km, about ${min} min, ${n} restriction${n === 1 ? '' : 's'} considered. ${res.label ?? ''}`.trim(),
    edgeIds,
    route,
  };
}
