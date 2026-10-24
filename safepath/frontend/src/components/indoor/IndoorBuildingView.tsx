import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { SimulatedBadge } from '../SimulatedBadge';
import { StatusIcon, type IconKind } from '../StatusIcon';
import {
  STATUS_TEXT,
  avoidanceNotes,
  buildRoute,
  deriveStatuses,
  edgeIdsOfResponse,
  featuresOf,
  floorWord,
  hasNoRoute,
  parseBuilding,
  statusOf,
  type Building,
  type Entity,
  type FeaturePoint,
  type FeatureStatus,
  type FeatureStatusKind,
  type IndoorGeoJson,
} from './indoorModel';
import { fetchIndoorBuilding, useIndoorBuilding } from './indoorService';

export const SIMULATED_BANNER = 'SIMULATED fictional building, not a real floor plan';

const SCALE = 10; // plan units per metre
const PAD = 50;
const ICON: Record<FeatureStatusKind, IconKind> = {
  no_reports: 'question',
  reported_unverified: 'warning',
  reported_corroborated: 'people',
  closure_confirmed: 'cross',
};
const SHORT: Record<FeatureStatusKind, string> = {
  no_reports: 'No reports',
  reported_unverified: 'Unverified',
  reported_corroborated: 'Corroborated',
  closure_confirmed: 'Closed (SIM)',
};
const GLYPH: Record<FeatureStatusKind, string> = {
  no_reports: '',
  reported_unverified: '!',
  reported_corroborated: '!!',
  closure_confirmed: '\u2715',
};
const BTN =
  'rounded border border-border-subtle px-3 py-1.5 text-sm text-text-primary hover:bg-surface-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-route disabled:cursor-not-allowed disabled:opacity-60';

export function featureLabel(f: Pick<FeaturePoint, 'featureId' | 'kind'>): string {
  return f.kind === 'stairwell' ? `Stairwell ${f.featureId.replace(/^stair-/, '')}` : f.featureId.replace(/^exit-/, 'Exit ');
}

export interface IndoorBuildingViewProps {
  /** Building GeoJSON. When omitted the view fetches GET /api/indoor/building itself. */
  building?: IndoorGeoJson | null;
  loadBuilding?: typeof fetchIndoorBuilding;
  /** Live values from the realtime store (`state.reports`, `state.restrictions`). */
  reports?: readonly Entity[];
  restrictions?: readonly Entity[];
  /** Latest indoor RouteResponse (first route's edge_penalties carry the indoor edge ids). */
  route?: unknown;
  /** Alternative to `route`: ordered indoor edge ids. */
  edgeIds?: readonly string[];
  selectedStartId?: string | null;
  onSelectStart?: (startId: string) => void;
  onReport?: (featureId: string) => void;
  onCorroborate?: (reportId: string) => void | Promise<unknown>;
  onDispute?: (reportId: string) => void | Promise<unknown>;
  initialLevel?: string;
}

export function IndoorBuildingView(props: IndoorBuildingViewProps) {
  const { building: given, loadBuilding = fetchIndoorBuilding } = props;
  const [attempt, setAttempt] = useState(0);
  const loaded = useIndoorBuilding(loadBuilding, given === undefined, attempt);
  const geo = given === undefined ? loaded.data : given;
  const building = useMemo(() => parseBuilding(geo), [geo]);

  if (!building) {
    const loading = given === undefined && loaded.loading;
    return (
      <section aria-label="Indoor building view" data-testid="indoor-view" className="space-y-2 rounded-lg border border-border-subtle bg-surface p-3 text-sm text-text-primary">
        <Banner />
        {loading ? (
          <p role="status">Loading the SIMULATED building&hellip;</p>
        ) : (
          <div role="alert" data-testid="indoor-unavailable" className="space-y-2">
            <p>
              Indoor building data is unavailable{loaded.error ? ` (${loaded.error})` : ''}. If you are inside a building, follow
              instructions from building staff and emergency responders.
            </p>
            {given === undefined && (
              <button type="button" className={BTN} onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            )}
          </div>
        )}
      </section>
    );
  }
  return <Loaded {...props} building={building} />;
}

function Banner() {
  return (
    <div data-testid="indoor-banner" className="flex flex-wrap items-center gap-2 rounded border border-route-alt p-2 text-xs font-semibold text-route-alt">
      <SimulatedBadge />
      <span>{SIMULATED_BANNER}</span>
    </div>
  );
}

type LoadedProps = Omit<IndoorBuildingViewProps, 'building'> & { building: Building };

function Loaded({
  building,
  reports = [],
  restrictions = [],
  route,
  edgeIds,
  selectedStartId,
  onSelectStart,
  onReport,
  onCorroborate,
  onDispute,
  initialLevel,
}: LoadedProps) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
  const levels = building.floors.map((f) => f.level);
  const [level, setLevel] = useState(initialLevel && levels.includes(initialLevel) ? initialLevel : levels[0]);
  const [selected, setSelected] = useState<string | null>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const statuses = useMemo(() => deriveStatuses(reports, restrictions), [reports, restrictions]);
  const features = useMemo(() => featuresOf(building), [building]);

  // aria-live announcement of status changes, derived during render (no effect needed).
  const sig = features.map((f) => `${f.featureId}:${statusOf(statuses, f.featureId).kind}`).join('|');
  const [seen, setSeen] = useState<{ sig: string; kinds: Record<string, FeatureStatusKind> } | null>(null);
  const [announcement, setAnnouncement] = useState('');
  if (!seen || seen.sig !== sig) {
    const kinds = Object.fromEntries(features.map((f) => [f.featureId, statusOf(statuses, f.featureId).kind]));
    if (seen) {
      const changes = features
        .filter((f) => seen.kinds[f.featureId] !== kinds[f.featureId])
        .map((f) => `${featureLabel(f)}: ${STATUS_TEXT[kinds[f.featureId]]}`);
      if (changes.length) setAnnouncement(`Condition update. ${changes.join('. ')}`);
    }
    setSeen({ sig, kinds });
  }

  const ids = edgeIds ?? edgeIdsOfResponse(route);
  const indoorRoute = useMemo(() => buildRoute(building, ids), [building, ids]);
  const noRoute = route != null && hasNoRoute(route) && !indoorRoute;
  const notes = useMemo(
    () => avoidanceNotes(building, statuses, indoorRoute?.usedFeatures ?? []),
    [building, statuses, indoorRoute],
  );

  const onTabKey = (e: KeyboardEvent, i: number) => {
    const next: Record<string, number> = {
      ArrowRight: (i + 1) % levels.length,
      ArrowLeft: (i - 1 + levels.length) % levels.length,
      Home: 0,
      End: levels.length - 1,
    };
    const n = next[e.key];
    if (n === undefined) return;
    e.preventDefault();
    setLevel(levels[n]);
    tabRefs.current[levels[n]]?.focus();
  };

  const floor = building.floors.find((f) => f.level === level) ?? building.floors[0];
  const W = building.width * SCALE;
  const D = building.depth * SCALE;
  const px = (v: number) => v * SCALE;
  const visible = features.filter((f) =>
    f.kind === 'stairwell' ? true : building.exits.some((e) => e.featureId === f.featureId && e.level === level),
  );
  const selectedFeature = features.find((f) => f.featureId === selected) ?? null;
  const legs = indoorRoute?.legs.filter((l) => l.level === level) ?? [];
  const starts = building.starts.filter((s) => s.level === level);
  const allStarts = building.starts;

  return (
    <section aria-label="Indoor building view" data-testid="indoor-view" className="space-y-3 rounded-lg border border-border-subtle bg-surface p-3 text-sm text-text-primary">
      <Banner />
      <h2 className="break-words text-base font-semibold">{building.name}</h2>
      <p className="text-xs text-text-muted">{building.notice}</p>
      <p className="text-xs text-text-muted">
        Stairwell and exit conditions are community reports shown with their Verification_Status. &ldquo;No reports&rdquo; only means
        nothing has been reported; it does not mean a stairwell or exit is clear or usable. Follow instructions from building staff
        and emergency responders.
      </p>

      <div role="tablist" aria-label="Building floors" className="flex flex-wrap gap-1">
        {building.floors.map((f, i) => (
          <button
            key={f.level}
            ref={(el) => {
              tabRefs.current[f.level] = el;
            }}
            type="button"
            role="tab"
            id={`${uid}-tab-${f.level}`}
            aria-selected={f.level === level}
            aria-controls={`${uid}-panel`}
            aria-label={`${floorWord(f.level)} (${f.level})`}
            tabIndex={f.level === level ? 0 : -1}
            onClick={() => setLevel(f.level)}
            onKeyDown={(e) => onTabKey(e, i)}
            className={`${BTN} ${f.level === level ? 'border-route bg-surface-raised font-semibold' : ''}`}
          >
            {f.level}
          </button>
        ))}
      </div>

      <div id={`${uid}-panel`} role="tabpanel" aria-labelledby={`${uid}-tab-${level}`} className="space-y-2">
        <svg
          role="group"
          aria-label={`${floor.name}. Corridors, zones, stairwells and exits.`}
          viewBox={`${-PAD} ${-PAD} ${W + 2 * PAD} ${D + 2 * PAD}`}
          className="h-auto w-full max-w-full rounded border border-border-subtle bg-base"
          data-testid="indoor-svg"
          fontFamily="sans-serif"
        >
          <defs>
            <pattern id={`${uid}-hatch`} width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="12" height="12" fill="#1a2540" />
              <line x1="0" y1="0" x2="0" y2="12" stroke="#fb923c" strokeWidth="5" />
            </pattern>
            <pattern id={`${uid}-cross`} width="12" height="12" patternUnits="userSpaceOnUse">
              <rect width="12" height="12" fill="#1a2540" />
              <path d="M0 6H12M6 0V12" stroke="#fb923c" strokeWidth="3" />
            </pattern>
            <pattern id={`${uid}-solid`} width="12" height="12" patternUnits="userSpaceOnUse">
              <rect width="12" height="12" fill="#7f1d1d" />
              <circle cx="6" cy="6" r="2" fill="#f87171" />
            </pattern>
          </defs>
          <rect x={0} y={0} width={W} height={D} fill="#111a2e" stroke="#2c3a5c" strokeWidth={4} />
          <text x={8} y={-14} fontSize={26} fill="#a9b6cf">{`${floor.name} \u2014 SIMULATED`}</text>
          {building.corridors
            .filter((c) => c.level === level)
            .map((c) => (
              <g key={c.id}>
                <polyline
                  points={c.points.map((p) => `${px(p.x)},${px(p.y)}`).join(' ')}
                  fill="none"
                  stroke="#2c3a5c"
                  strokeWidth={22}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <title>{c.name}</title>
                </polyline>
                <text x={px(c.points[0].x)} y={px(c.points[0].y) + (c.id.endsWith('1') ? 40 : -22)} fontSize={22} fill="#a9b6cf">
                  {c.name.replace('SIMULATED ', '')}
                </text>
              </g>
            ))}
          {building.zones
            .filter((z) => z.level === level)
            .map((z) => (
              <g key={z.id}>
                <rect x={px(z.x) - 45} y={px(z.y) - 30} width={90} height={60} rx={6} fill="#1a2540" stroke="#60a5fa" strokeDasharray="6 4" strokeWidth={3} />
                <text x={px(z.x)} y={px(z.y) + 8} fontSize={22} fill="#e6edf7" textAnchor="middle">
                  {z.name.replace('SIMULATED ', '')}
                </text>
              </g>
            ))}
          {legs.map((l, i) => (
            <polyline
              key={i}
              data-testid="indoor-route-leg"
              points={l.points.map((p) => `${px(p.x)},${px(p.y)}`).join(' ')}
              fill="none"
              stroke="#22d3ee"
              strokeWidth={8}
              strokeLinecap="round"
              strokeDasharray="22 10"
            />
          ))}
          {visible.map((f) => {
            const p = (f.kind === 'stairwell' ? building.stairwells : building.exits).find((q) => q.featureId === f.featureId && q.level === level) ?? f;
            const st = statusOf(statuses, f.featureId);
            const isSel = selected === f.featureId;
            const fill =
              st.kind === 'reported_unverified' ? `url(#${uid}-hatch)` : st.kind === 'reported_corroborated' ? `url(#${uid}-cross)` : st.kind === 'closure_confirmed' ? `url(#${uid}-solid)` : '#111a2e';
            const name = `SIMULATED ${featureLabel(f)}`;
            const activate = () => setSelected(f.featureId);
            const cx = px(p.x);
            const cy = px(p.y);
            return (
              <g
                key={f.featureId}
                role="button"
                tabIndex={0}
                aria-pressed={isSel}
                aria-label={`${name}, ${STATUS_TEXT[st.kind]}, ${st.reportCount} ${st.reportCount === 1 ? 'report' : 'reports'}`}
                data-testid={`indoor-feature-${f.featureId}`}
                className="cursor-pointer focus:outline-none"
                onClick={activate}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    activate();
                  }
                }}
              >
                <rect x={cx - 34} y={cy - 34} width={68} height={68} rx={f.kind === 'exit' ? 4 : 18} fill={fill} stroke={isSel ? '#22d3ee' : '#e6edf7'} strokeWidth={isSel ? 8 : 4} strokeDasharray={st.kind === 'no_reports' ? '8 5' : undefined} />
                <text x={cx} y={cy + 9} fontSize={28} fontWeight="bold" fill="#e6edf7" textAnchor="middle" stroke="#0b1220" strokeWidth={4} paintOrder="stroke">
                  {GLYPH[st.kind] || (f.kind === 'exit' ? '\u21E5' : f.featureId.replace(/^stair-/, ''))}
                </text>
                <text x={cx} y={cy - 42} fontSize={22} fill="#e6edf7" textAnchor="middle">{featureLabel(f)}</text>
                <text x={cx} y={cy + 58} fontSize={19} fill="#e6edf7" textAnchor="middle">{SHORT[st.kind]}</text>
              </g>
            );
          })}
          {starts.map((s) => {
            const idx = allStarts.indexOf(s);
            const letter = String.fromCharCode(65 + idx);
            const isSel = selectedStartId === s.id;
            return (
              <g
                key={s.id}
                role="button"
                tabIndex={0}
                aria-pressed={isSel}
                aria-label={`Position ${letter}: ${s.name}`}
                data-testid={`indoor-start-${s.id}`}
                className="cursor-pointer focus:outline-none"
                onClick={() => onSelectStart?.(s.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelectStart?.(s.id);
                  }
                }}
              >
                <circle cx={px(s.x)} cy={px(s.y)} r={22} fill={isSel ? '#22d3ee' : '#0b1220'} stroke="#22d3ee" strokeWidth={5} />
                <text x={px(s.x)} y={px(s.y) + 8} fontSize={24} fontWeight="bold" textAnchor="middle" fill={isSel ? '#0b1220' : '#22d3ee'}>{letter}</text>
              </g>
            );
          })}
        </svg>

        <p className="text-xs text-text-muted">
          Positions: {allStarts.length ? allStarts.map((s, i) => `${String.fromCharCode(65 + i)} = ${s.name}`).join('; ') : 'none provided'}.
        </p>
        {indoorRoute && !indoorRoute.fullyPlaced && (
          <p className="text-xs text-text-muted">Part of this route could not be drawn on a floor plan; see the steps list.</p>
        )}
      </div>

      <div aria-live="polite" role="status" data-testid="indoor-live" className="sr-only">{announcement}</div>

      <section aria-labelledby={`${uid}-cond`} className="space-y-1">
        <h3 id={`${uid}-cond`} className="font-semibold">Stairwell and exit conditions (reported)</h3>
        <ul className="space-y-1" data-testid="indoor-status-list">
          {features.map((f) => {
            const st = statusOf(statuses, f.featureId);
            return (
              <li key={f.featureId} className="flex flex-wrap items-center gap-1.5 break-words">
                <span className="font-medium">{featureLabel(f)}:</span>
                <StatusChip kind={st.kind} />
                <span className="text-xs text-text-muted">{st.reportCount} {st.reportCount === 1 ? 'report' : 'reports'}</span>
              </li>
            );
          })}
        </ul>
      </section>

      {selectedFeature && (
        <FeaturePanel
          feature={selectedFeature}
          status={statusOf(statuses, selectedFeature.featureId)}
          onReport={onReport}
          onCorroborate={onCorroborate}
          onDispute={onDispute}
          onClose={() => setSelected(null)}
        />
      )}

      <section aria-labelledby={`${uid}-route`} className="space-y-1" data-testid="indoor-route">
        <h3 id={`${uid}-route`} className="font-semibold">Indoor route steps</h3>
        {indoorRoute ? (
          <ol className="list-decimal space-y-0.5 pl-5">
            {indoorRoute.steps.map((s, i) => (
              <li key={i} className="break-words">{s}</li>
            ))}
          </ol>
        ) : noRoute ? (
          <div role="alert" data-testid="indoor-no-route" className="space-y-1 rounded border border-closure p-2">
            <p className="font-semibold">No verified indoor route is available right now.</p>
            <p>
              Follow instructions from building staff and emergency responders. If you are in immediate danger, call 911 yourself.
              SafePath does not call, dispatch, or notify 911.
            </p>
          </div>
        ) : (
          <p className="text-text-muted">No indoor route calculated yet. Pick a position and calculate a route.</p>
        )}
        {notes.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5" data-testid="indoor-route-notes">
            {notes.map((n) => (
              <li key={n} className="break-words">{n}</li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}

function StatusChip({ kind }: { kind: FeatureStatusKind }) {
  const cls = kind === 'closure_confirmed' ? 'border-closure' : kind === 'no_reports' ? 'border-border-subtle text-text-muted' : 'border-unverified';
  return (
    <span className={`inline-flex items-start gap-1 rounded border px-1.5 py-0.5 text-xs ${cls}`} data-testid="indoor-status">
      <span className="mt-0.5"><StatusIcon kind={ICON[kind]} /></span>
      <span className="break-words">{STATUS_TEXT[kind]}</span>
    </span>
  );
}

function FeaturePanel({
  feature,
  status,
  onReport,
  onCorroborate,
  onDispute,
  onClose,
}: {
  feature: FeaturePoint;
  status: FeatureStatus;
  onReport?: (featureId: string) => void;
  onCorroborate?: (reportId: string) => void | Promise<unknown>;
  onDispute?: (reportId: string) => void | Promise<unknown>;
  onClose: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const noun = feature.kind === 'stairwell' ? 'stairwell' : 'exit';
  const latest = status.latestReport;
  const vote = async (fn: ((id: string) => void | Promise<unknown>) | undefined) => {
    if (!fn || !latest) return;
    setError(null);
    try {
      await fn(latest.id);
    } catch {
      setError('Could not record your response. Try again.');
    }
  };
  return (
    <section aria-label={`Selected: SIMULATED ${featureLabel(feature)}`} data-testid="indoor-panel" className="space-y-2 rounded border border-route p-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">SIMULATED {featureLabel(feature)}</h3>
        <button type="button" className={BTN} onClick={onClose}>Close</button>
      </div>
      <p className="flex flex-wrap items-center gap-1.5">
        <StatusChip kind={status.kind} />
      </p>
      <p data-testid="indoor-panel-count">Reports: {status.reportCount}</p>
      {latest && (
        <p className="break-words text-xs text-text-muted">
          Latest report: {latest.category} ({latest.status.replace('_', ' ')})
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} onClick={() => onReport?.(feature.featureId)}>
          Report this {noun} blocked
        </button>
        <button type="button" className={BTN} disabled={!latest || !onCorroborate} onClick={() => void vote(onCorroborate)}>
          Corroborate latest report
        </button>
        <button type="button" className={BTN} disabled={!latest || !onDispute} onClick={() => void vote(onDispute)}>
          Dispute latest report
        </button>
      </div>
      {!latest && <p className="text-xs text-text-muted">There is no active report to corroborate or dispute.</p>}
      {error && <p role="alert" className="text-xs text-closure">{error}</p>}
    </section>
  );
}
