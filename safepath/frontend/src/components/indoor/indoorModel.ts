/**
 * Pure helpers for the indoor building view (Req 21, 39): parse the SIMULATED building GeoJSON,
 * map reports/restrictions to stairwell/exit statuses and turn indoor edge ids into route steps.
 * Wording: statuses are reported conditions only; nothing here ever calls a feature "clear".
 */

export type Entity = Record<string, unknown>;

// ------------------------------------------------------------------ GeoJSON input

export interface IndoorFeature {
  type: 'Feature';
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
}
export interface IndoorGeoJson {
  type: 'FeatureCollection';
  name?: string;
  properties?: { notice?: string; [k: string]: unknown };
  features: IndoorFeature[];
}

// ------------------------------------------------------------------ parsed building

export interface Pt {
  x: number;
  y: number;
}
export interface Floor {
  level: string;
  number: number;
  name: string;
}
export interface Corridor {
  id: string;
  name: string;
  level: string;
  points: Pt[];
  nodeKeys: string[];
}
export interface PlanPoint extends Pt {
  id: string;
  key: string;
  name: string;
  level: string;
}
export interface FeaturePoint extends PlanPoint {
  /** Report/restriction id: 'stair-A' or 'exit-1'. */
  featureId: string;
  kind: 'stairwell' | 'exit';
}
export interface Building {
  name: string;
  notice: string;
  width: number;
  depth: number;
  floors: Floor[];
  corridors: Corridor[];
  zones: PlanPoint[];
  stairwells: FeaturePoint[];
  exits: FeaturePoint[];
  starts: PlanPoint[];
  /** level -> node key -> position (junctions, zones, stairwells, exits). */
  nodes: Record<string, Record<string, PlanPoint>>;
}

const M_PER_DEG = 111_320;
const NODE_PREFIX = /^sim-indoor-(L\d+)-(.+)$/;

export function splitNodeId(nodeId: string): { level: string; key: string } | null {
  const m = NODE_PREFIX.exec(nodeId);
  return m ? { level: m[1], key: m[2] } : null;
}

const s = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d);

/** Convert the GeoJSON layer to plan coordinates in metres (x east, y south-down for SVG). */
export function parseBuilding(geo: IndoorGeoJson | null | undefined): Building | null {
  if (!geo || geo.type !== 'FeatureCollection' || !Array.isArray(geo.features)) return null;
  const floorFeats = geo.features.filter((f) => f.properties?.role === 'floor');
  const ring = (floorFeats[0]?.geometry.coordinates as number[][][] | undefined)?.[0];
  if (!ring || ring.length < 3) return null;
  const lons = ring.map((c) => c[0]);
  const lats = ring.map((c) => c[1]);
  const minLon = Math.min(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const cosLat = Math.cos(((minLat + maxLat) / 2) * (Math.PI / 180));
  const width = (Math.max(...lons) - minLon) * cosLat * M_PER_DEG;
  const depth = (maxLat - minLat) * M_PER_DEG;
  const toPt = (c: number[]): Pt => ({
    x: Math.round((c[0] - minLon) * cosLat * M_PER_DEG * 100) / 100,
    y: Math.round((maxLat - c[1]) * M_PER_DEG * 100) / 100,
  });

  const floors: Floor[] = floorFeats
    .map((f) => ({ level: s(f.properties.level), number: Number(f.properties.floor) || 0, name: s(f.properties.name) }))
    .sort((a, b) => a.number - b.number);
  const nodes: Building['nodes'] = {};
  const put = (p: PlanPoint) => {
    (nodes[p.level] ??= {})[p.key] = p;
  };
  const point = (f: IndoorFeature): PlanPoint | null => {
    if (f.geometry.type !== 'Point') return null;
    const level = s(f.properties.level);
    const id = s(f.properties.node_id);
    const key = splitNodeId(id)?.key ?? id;
    return { ...toPt(f.geometry.coordinates as number[]), id, key, name: s(f.properties.name, key), level };
  };

  const corridors: Corridor[] = [];
  for (const f of geo.features) {
    if (f.properties?.role !== 'corridor' || f.geometry.type !== 'LineString') continue;
    const level = s(f.properties.level);
    const coords = f.geometry.coordinates as number[][];
    const ids = Array.isArray(f.properties.node_ids) ? (f.properties.node_ids as string[]) : [];
    const points = coords.map(toPt);
    const keys = ids.map((i) => splitNodeId(i)?.key ?? i);
    corridors.push({ id: s(f.properties.corridor_id), name: s(f.properties.name), level, points, nodeKeys: keys });
    keys.forEach((key, i) => {
      if (points[i]) put({ ...points[i], id: ids[i], key, name: junctionLabel(key), level });
    });
  }
  const zones: PlanPoint[] = [];
  const stairwells: FeaturePoint[] = [];
  const exits: FeaturePoint[] = [];
  const starts: PlanPoint[] = [];
  for (const f of geo.features) {
    const p = point(f);
    if (!p) continue;
    const role = f.properties.role;
    if (role === 'zone') {
      zones.push(p);
      put(p);
    } else if (role === 'stairwell') {
      const fp: FeaturePoint = { ...p, featureId: `stair-${s(f.properties.stairwell_id)}`, kind: 'stairwell' };
      stairwells.push(fp);
      put(fp);
    } else if (role === 'exit') {
      const fp: FeaturePoint = { ...p, featureId: s(f.properties.exit_id), kind: 'exit' };
      exits.push(fp);
      put(fp);
    } else if (role === 'start_position') {
      starts.push({ ...p, id: s(f.properties.start_id, p.id) });
    }
  }
  return {
    name: geo.name ?? s(floorFeats[0].properties.name, 'SIMULATED building'),
    notice: geo.properties?.notice ?? 'SIMULATED fictional building. Not a real floor plan.',
    width,
    depth,
    floors,
    corridors,
    zones,
    stairwells,
    exits,
    starts,
    nodes,
  };
}

function junctionLabel(key: string): string {
  const m = /^(cor|nor)-(\w+)$/.exec(key);
  if (!m) return key;
  return `${m[1] === 'nor' ? 'north corridor' : 'corridor'} ${m[2]}`;
}

export const floorWord = (level: string): string => `Floor ${level.replace(/^L/, '')}`;

/** Stairwell/exit unique ids on this building (stairwells appear on every floor, exits on ground). */
export function featuresOf(b: Building): FeaturePoint[] {
  const seen = new Set<string>();
  const out: FeaturePoint[] = [];
  for (const f of [...b.stairwells, ...b.exits]) {
    if (!seen.has(f.featureId)) {
      seen.add(f.featureId);
      out.push(f);
    }
  }
  return out;
}

// ------------------------------------------------------------------ statuses

export type FeatureStatusKind = 'no_reports' | 'reported_unverified' | 'reported_corroborated' | 'closure_confirmed';

export const STATUS_TEXT: Record<FeatureStatusKind, string> = {
  no_reports: 'No reports',
  reported_unverified: 'Reported blocked \u2014 Unverified',
  reported_corroborated: 'Reported blocked \u2014 Corroborated by community, not officially verified',
  closure_confirmed: 'Closure confirmed (SIMULATED moderator verification)',
};

export interface FeatureStatus {
  kind: FeatureStatusKind;
  /** Live (not resolved/expired/disputed-out) reports about this feature. */
  reportCount: number;
  /** Latest live report, for corroborate/dispute shortcuts. */
  latestReport: { id: string; category: string; status: string; createdAt: string } | null;
}

const INACTIVE = new Set(['Resolved', 'Expired', 'Disputed']);
const CLOSURE_KINDS = new Set(['stairwell_closure', 'closure', 'exit_closure']);

const NO_STATUS: FeatureStatus = { kind: 'no_reports', reportCount: 0, latestReport: null };
const RANK: Record<FeatureStatusKind, number> = {
  no_reports: 0,
  reported_unverified: 1,
  reported_corroborated: 2,
  closure_confirmed: 3,
};

/** 'sim-indoor-t-stair-A-L3-L2' / 'sim-indoor-e-L1-stair-A-cor-W' / 'sim-indoor-t-exit-1-out' -> feature id. */
export function featureIdFromEdge(edgeId: string): string | null {
  const stair = /^sim-indoor-t-(stair-[A-Za-z0-9]+)-L\d+-L\d+$/.exec(edgeId);
  if (stair) return stair[1];
  const exit = /^sim-indoor-t-(exit-[A-Za-z0-9]+)-(?:out|in)$/.exec(edgeId);
  if (exit) return exit[1];
  return null;
}

/** Feature ids a report or restriction entity refers to (explicit id, else its indoor edge ids). */
export function featureIdsOf(e: Entity): string[] {
  const out = new Set<string>();
  for (const k of ['indoor_feature_id', 'feature_id']) {
    const v = e[k];
    if (typeof v === 'string' && v) out.add(v);
  }
  for (const k of ['edge_ids', 'affected_edge_ids']) {
    const v = e[k];
    if (Array.isArray(v)) {
      for (const id of v) {
        const f = typeof id === 'string' ? featureIdFromEdge(id) : null;
        if (f) out.add(f);
      }
    }
  }
  return [...out];
}

/**
 * Map reports and restrictions to a status per feature id. Reports: Unverified/Corroborated give a
 * reported condition; Officially_Verified (SIMULATED moderator) gives a confirmed closure; resolved,
 * expired and disputed reports do not count. Active closure restrictions confirm a closure. The
 * highest-ranked condition wins. Features without any entry are absent (use `statusOf`).
 */
export function deriveStatuses(
  reports: readonly Entity[],
  restrictions: readonly Entity[] = [],
): Record<string, FeatureStatus> {
  const out: Record<string, FeatureStatus> = {};
  const bump = (id: string, kind: FeatureStatusKind, report?: FeatureStatus['latestReport']) => {
    const cur = out[id] ?? { ...NO_STATUS };
    const next: FeatureStatus = {
      kind: RANK[kind] > RANK[cur.kind] ? kind : cur.kind,
      reportCount: cur.reportCount + (report ? 1 : 0),
      latestReport:
        report && (!cur.latestReport || report.createdAt >= cur.latestReport.createdAt) ? report : cur.latestReport,
    };
    out[id] = next;
  };
  for (const r of reports) {
    const status = typeof r.verification_status === 'string' ? r.verification_status : 'Unverified';
    if (INACTIVE.has(status)) continue;
    const rid = typeof r.report_id === 'string' ? r.report_id : typeof r.id === 'string' ? r.id : null;
    if (!rid) continue;
    const kind: FeatureStatusKind =
      status === 'Officially_Verified'
        ? 'closure_confirmed'
        : status === 'Corroborated'
          ? 'reported_corroborated'
          : 'reported_unverified';
    const report = {
      id: rid,
      category: typeof r.category === 'string' ? r.category : 'other',
      status,
      createdAt: typeof r.created_at === 'string' ? r.created_at : '',
    };
    for (const f of featureIdsOf(r)) bump(f, kind, report);
  }
  for (const x of restrictions) {
    if (x.active === false || x.status === 'inactive') continue;
    if (typeof x.kind === 'string' && !CLOSURE_KINDS.has(x.kind)) continue;
    for (const f of featureIdsOf(x)) bump(f, 'closure_confirmed');
  }
  return out;
}

export const statusOf = (m: Record<string, FeatureStatus>, featureId: string): FeatureStatus => m[featureId] ?? NO_STATUS;

// ------------------------------------------------------------------ route

export type RouteEdge =
  | { type: 'corridor'; level: string; from: string; to: string; edgeId: string }
  | { type: 'stair'; stair: string; from: string; to: string; edgeId: string }
  | { type: 'exit'; exit: string; direction: 'out' | 'in'; edgeId: string };

/** Parse indoor edge ids; unknown (outdoor) ids are skipped. `keys` resolve hyphenated node keys. */
export function parseEdges(edgeIds: readonly string[], knownKeys: ReadonlySet<string>): RouteEdge[] {
  const out: RouteEdge[] = [];
  for (const id of edgeIds) {
    let m = /^sim-indoor-t-stair-([A-Za-z0-9]+)-(L\d+)-(L\d+)$/.exec(id);
    if (m) {
      out.push({ type: 'stair', stair: m[1], from: m[2], to: m[3], edgeId: id });
      continue;
    }
    m = /^sim-indoor-t-(exit-[A-Za-z0-9]+)-(out|in)$/.exec(id);
    if (m) {
      out.push({ type: 'exit', exit: m[1], direction: m[2] as 'out' | 'in', edgeId: id });
      continue;
    }
    m = /^sim-indoor-e-(L\d+)-(.+)$/.exec(id);
    if (m) {
      const rest = m[2];
      for (let i = rest.indexOf('-'); i !== -1; i = rest.indexOf('-', i + 1)) {
        const a = rest.slice(0, i);
        const b = rest.slice(i + 1);
        if (knownKeys.has(a) && knownKeys.has(b)) {
          out.push({ type: 'corridor', level: m[1], from: a, to: b, edgeId: id });
          break;
        }
      }
    }
  }
  return out;
}

export function knownKeysOf(b: Building): Set<string> {
  const keys = new Set<string>();
  for (const lvl of Object.values(b.nodes)) for (const k of Object.keys(lvl)) keys.add(k);
  return keys;
}

export interface RouteLeg {
  level: string;
  points: Pt[];
}
export interface IndoorRoute {
  /** Per-floor polylines for edges that could be placed. */
  legs: RouteLeg[];
  /** Level-transition markers: stairwell/exit feature ids used, in order. */
  usedFeatures: string[];
  steps: string[];
  /** True when every corridor edge was placed on a floor. */
  fullyPlaced: boolean;
}

function nameOf(b: Building, level: string, key: string): string {
  return b.nodes[level]?.[key]?.name ?? key;
}

/** Ordered text steps + placeable polylines from indoor edge ids. Examples:
 *  'Floor 3 corridor W -> Stairwell B down to Floor 1 -> Exit 1'. */
export function buildRoute(b: Building, edgeIds: readonly string[]): IndoorRoute | null {
  const edges = parseEdges(edgeIds, knownKeysOf(b));
  if (edges.length === 0) return null;
  const steps: string[] = [];
  const legs: RouteLeg[] = [];
  const used: string[] = [];
  let fullyPlaced = true;
  const st: { run: { level: string; first: string; last: string } | null } = { run: null };
  const flush = () => {
    const run = st.run;
    if (run) {
      const a = nameOf(b, run.level, run.first);
      const z = nameOf(b, run.level, run.last);
      steps.push(
        run.first === run.last
          ? `${floorWord(run.level)} ${a}`
          : `${floorWord(run.level)} ${a} to ${z}`,
      );
      st.run = null;
    }
  };
  for (const e of edges) {
    if (e.type === 'corridor') {
      const pa = b.nodes[e.level]?.[e.from];
      const pb = b.nodes[e.level]?.[e.to];
      if (pa && pb) legs.push({ level: e.level, points: [pa, pb] });
      else fullyPlaced = false;
      if (st.run && st.run.level === e.level && st.run.last === e.from) st.run.last = e.to;
      else {
        flush();
        st.run = { level: e.level, first: e.from, last: e.to };
      }
    } else if (e.type === 'stair') {
      flush();
      const down = Number(e.to.slice(1)) < Number(e.from.slice(1));
      steps.push(`Stairwell ${e.stair} ${down ? 'down' : 'up'} to ${floorWord(e.to)}`);
      used.push(`stair-${e.stair}`);
    } else if (e.direction === 'out') {
      flush();
      const label = e.exit.replace(/^exit-/, 'Exit ');
      steps.push(label);
      used.push(e.exit);
    }
  }
  flush();
  return { legs, usedFeatures: used, steps, fullyPlaced };
}

/** Notes naming stairwells/exits removed from or avoided by the route. */
export function avoidanceNotes(
  b: Building,
  statuses: Record<string, FeatureStatus>,
  usedFeatures: readonly string[],
): string[] {
  const notes: string[] = [];
  for (const f of featuresOf(b)) {
    const st = statusOf(statuses, f.featureId);
    const label = f.kind === 'stairwell' ? `Stairwell ${f.featureId.replace(/^stair-/, '')}` : f.featureId.replace(/^exit-/, 'Exit ');
    const used = usedFeatures.includes(f.featureId);
    if (st.kind === 'closure_confirmed') {
      notes.push(`${label} removed from the route: confirmed closure (SIMULATED)`);
    } else if (st.kind !== 'no_reports') {
      notes.push(
        used
          ? `${label} is still on the route but has a reported-blocked condition (${st.kind === 'reported_corroborated' ? 'Corroborated by community, not officially verified' : 'Unverified'}). Follow instructions from building staff.`
          : `${label} avoided: reported blocked (${st.kind === 'reported_corroborated' ? 'Corroborated by community, not officially verified' : 'Unverified'}); routes through it are penalized, not prohibited.`,
      );
    }
  }
  return notes;
}

/** Collect route edge ids from a RouteResponse-like value (first route). */
export function edgeIdsOfResponse(resp: unknown): string[] {
  const routes = (resp as { routes?: { edge_penalties?: { edge_id?: unknown }[] }[] } | null)?.routes;
  const first = Array.isArray(routes) ? routes[0] : undefined;
  return (first?.edge_penalties ?? []).map((p) => p.edge_id).filter((x): x is string => typeof x === 'string');
}

export function hasNoRoute(resp: unknown): boolean {
  const r = resp as { policy_outcome?: unknown; routes?: unknown[] } | null;
  if (!r) return false;
  return r.policy_outcome === 'NO_VERIFIED_ROUTE_AVAILABLE' || !Array.isArray(r.routes) || r.routes.length === 0;
}
