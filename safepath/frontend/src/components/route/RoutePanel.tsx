import { useId, useState, type ReactNode } from 'react';
import { ApiError, type RouteResponse } from '../../services/api';
import type { RouteRecalcSnapshot } from '../../services/routeRecalc';
import type { SourceRecord } from '../../services/sources';
import { Card } from '../ios/Card';
import { CheckGlyph, Chevron } from '../ios/icons';
import { SimulatedBadge } from '../SimulatedBadge';
import { SourceRecordPopup } from '../SourceRecordPopup';
import { StatusIcon, type IconKind } from '../StatusIcon';
import { RecalculateButton } from './RecalculateButton';
import { RouteChangeBanner } from './RouteChangeBanner';

type RouteT = RouteResponse['routes'][number];
type Notice = RouteResponse['notices'][number];

export const ALGORITHM_LABEL = 'algorithm-generated';
export const NO_ROUTE_TITLE = 'No verified route available';
export const NO_SHELTER_TEXT = 'No verified shelter available';
export const CALL_911_TEXT = 'If you are in immediate danger, call 911 from your phone now.';

/** Static official tornado guidance (NWS). Shown alongside, never instead of, official notices. */
export const TORNADO_GUIDANCE = {
  text: 'Go to the lowest floor of a sturdy building and move to an interior room away from windows. Stay there until the warning has expired or officials say it is over. Do not try to outrun a tornado on foot or by vehicle.',
  sourceName: 'National Weather Service: Tornado Safety',
  url: 'https://www.weather.gov/safety/tornado',
  retrievedAt: '2025-06-01',
} as const;

const UNCERTAINTY: Record<RouteT['uncertainty_level'], { icon: IconKind; cls: string }> = {
  Low: { icon: 'check', cls: 'border-verified text-verified' },
  Medium: { icon: 'warning', cls: 'border-unverified text-unverified' },
  High: { icon: 'cross', cls: 'border-closure text-closure' },
  Unknown: { icon: 'question', cls: 'border-border-subtle text-text-muted' },
};

function fmt(value: string | null | undefined): string {
  if (!value) return 'Unknown';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
}
export const formatDistance = (m: number): string => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);
export const formatDuration = (s: number): string => `${Math.max(1, Math.round(s / 60))} min`;

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return typeof error === 'string' ? error : 'Route calculation failed.';
}

export function UncertaintyBadge({ level }: { level: RouteT['uncertainty_level'] }) {
  const s = UNCERTAINTY[level] ?? UNCERTAINTY.Unknown;
  return (
    <span data-testid="uncertainty-badge" className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${s.cls}`}>
      <StatusIcon kind={s.icon} />
      <span>Uncertainty: {level}</span>
    </span>
  );
}

export interface RoutePanelProps {
  response: RouteResponse | null;
  loading?: boolean;
  /** Error from the calculate request (ApiError envelope messages are shown verbatim). */
  error?: unknown;
  /** Output of useRouteRecalc, for the change banner. */
  recalc?: RouteRecalcSnapshot | null;
  /** Controlled selection; defaults to the first route. */
  selectedRouteId?: string | null;
  onSelectRoute?: (routeId: string) => void;
  onRecalculate?: () => void;
  /** Source_Record lookup by id for popups. */
  sourceRecords?: Record<string, SourceRecord> | SourceRecord[];
  /** Render the selected route's details as a disclosure Grouped_List (Route details screen). */
  disclosure?: boolean;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1">
      <h5 className="text-[length:var(--text-caption)] font-semibold uppercase tracking-wide text-text-muted">{title}</h5>
      {children}
    </section>
  );
}

/** One disclosure row of an inset grouped list; open by default so content stays visible. */
function DisclosureSection({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  const bodyId = useId();
  return (
    <li className="m-0 p-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-[var(--target-min)] w-full items-center gap-3 px-4 py-2.5 text-left text-[length:var(--text-body)] font-semibold text-text-primary focus:outline-2 focus:outline-route"
      >
        <span className="flex-1">{title}</span>
        <span aria-hidden="true" className="text-text-muted">
          <Chevron direction={open ? 'down' : 'right'} />
        </span>
      </button>
      <div id={bodyId} hidden={!open} className="space-y-1 px-4 pb-3">
        {children}
      </div>
    </li>
  );
}

function SourceButton({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  return (
    <button type="button" onClick={() => onOpen(id)} className="break-all rounded text-route underline focus:outline-2 focus:outline-route">
      Source {id}
    </button>
  );
}

function DisclosureList({ children }: { children: ReactNode }) {
  return (
    <ul className="m-0 list-none divide-y divide-border-subtle overflow-hidden rounded-[var(--radius-card)] border border-border-subtle bg-surface p-0">
      {children}
    </ul>
  );
}
function Passthrough({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function RouteDetails({
  route, onOpen, outdated, disclosure = false,
}: { route: RouteT; onOpen: (id: string) => void; outdated: boolean; disclosure?: boolean }) {
  const Sec = disclosure ? DisclosureSection : Section;
  const Wrap = disclosure ? DisclosureList : Passthrough;
  return (
    <div data-testid="route-details" className="space-y-3 border-t border-border-subtle pt-2 text-[length:var(--text-body)]">
      {outdated && <p className="text-unverified">This route may be outdated.</p>}
      <p>
        {formatDistance(route.distance_meters)} · {formatDuration(route.estimated_duration_seconds)}
      </p>
      <Wrap>
      <Sec title="Why chosen over alternatives">
        <p>{route.reason_summary || 'No comparison with alternatives was provided.'}</p>
      </Sec>
      <Sec title="Restrictions considered">
        {route.restrictions_considered.length === 0 ? (
          <p>No restrictions were considered for this route.</p>
        ) : (
          <ul className="space-y-1">
            {route.restrictions_considered.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2">
                <span>Cause: {r.kind}</span>
                {r.simulated && <SimulatedBadge />}
                {r.source_record_id ? <SourceButton id={r.source_record_id} onOpen={onOpen} /> : <span>Source: Unknown</span>}
              </li>
            ))}
          </ul>
        )}
        <p>Excluded edges: {route.excluded_edges_count}</p>
      </Sec>
      <Sec title="Confidence limitations">
        {route.confidence_limitations.length === 0 ? (
          <p>No specific limitations were reported. Conditions can still differ from the data.</p>
        ) : (
          <ul className="list-disc pl-5">
            {route.confidence_limitations.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
      </Sec>
      <Sec title="Sources">
        {route.sources.length === 0 ? (
          <p>Source: Unknown</p>
        ) : (
          <ul className="flex flex-wrap gap-3">
            {route.sources.map((s) => (
              <li key={s}>
                <SourceButton id={s} onOpen={onOpen} />
              </li>
            ))}
          </ul>
        )}
      </Sec>
      </Wrap>
    </div>
  );
}

function RouteList({
  response, selectedId, onSelect, onOpen, outdated, disclosure,
}: {
  response: RouteResponse;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  outdated: boolean;
  disclosure: boolean;
}) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-text-muted">
        Flood information is modeled. A route with lower modeled exposure can still be impassable.
      </p>
      <ul className="m-0 list-none space-y-2 p-0" aria-label="Route options">
        {response.routes.map((r, i) => {
          const selected = r.route_id === selectedId;
          return (
            <li key={r.route_id} data-testid="route-item" className="m-0 p-0">
              <Card tone={selected ? 'info' : 'default'} className="p-3">
              <button
                type="button"
                aria-pressed={selected}
                aria-expanded={selected}
                onClick={() => onSelect(r.route_id)}
                className="min-h-[var(--target-min)] w-full rounded text-left focus:outline-2 focus:outline-route"
              >
                <span className="flex flex-wrap items-center gap-2">
                  {selected && (
                    <span aria-hidden="true" className="text-route">
                      <CheckGlyph size={16} />
                    </span>
                  )}
                  <span className="font-semibold">
                    {i === 0 ? 'Route' : `Alternative ${i}`} ({r.route_type})
                  </span>
                  {selected && <span className="text-xs">Selected</span>}
                  {outdated && <span className="text-xs text-unverified">Possibly outdated</span>}
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                  <span>{formatDistance(r.distance_meters)}</span>
                  <span>{formatDuration(r.estimated_duration_seconds)}</span>
                  <span className="rounded border border-border-subtle px-1.5 py-0.5 text-xs">{r.label || ALGORITHM_LABEL}</span>
                  <UncertaintyBadge level={r.uncertainty_level} />
                </span>
                {r.hazard_flags.length > 0 && (
                  <span data-testid="hazard-flags" className="mt-1 flex flex-wrap gap-1 text-xs">
                    {r.hazard_flags.map((h) => (
                      <span key={h} className="inline-flex items-center gap-1 rounded border border-unverified px-1.5 py-0.5 text-unverified">
                        <StatusIcon kind="warning" />
                        <span>{h}</span>
                      </span>
                    ))}
                  </span>
                )}
              </button>
              {selected && <RouteDetails route={r} onOpen={onOpen} outdated={outdated} disclosure={disclosure} />}
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function NoticeCard({ notice, onOpen }: { notice: Notice; onOpen: (id: string) => void }) {
  return (
    <li data-testid="notice" className="m-0 list-none p-0">
      <Card tone="success" className="space-y-1 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 rounded border border-verified px-1.5 py-0.5 text-xs text-verified">
          <StatusIcon kind="check" />
          <span>Official notice</span>
        </span>
        {notice.simulation_flag && <SimulatedBadge />}
      </div>
      <p className="font-semibold">{notice.text}</p>
      <p className="text-xs text-text-muted">
        {notice.source_organization} ·{' '}
        <a href={notice.source_url} target="_blank" rel="noopener noreferrer" className="break-all text-route underline">
          {notice.source_url}
        </a>{' '}
        · Retrieved {fmt(notice.retrieved_at)} · <SourceButton id={notice.source_record_id} onOpen={onOpen} />
      </p>
      </Card>
    </li>
  );
}

function MissingData({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <Section title="Missing data">
      <ul data-testid="missing-data" className="list-disc pl-5 text-sm">
        {items.map((m) => (
          <li key={m}>{m}</li>
        ))}
      </ul>
    </Section>
  );
}

function Call911() {
  return (
    <Card tone="danger" data-testid="call-911" className="p-3 text-sm font-semibold">
      {CALL_911_TEXT}
    </Card>
  );
}

function OutcomeBody({
  response, selectedId, onSelect, onOpen, outdated, disclosure,
}: {
  response: RouteResponse;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  outdated: boolean;
  disclosure: boolean;
}) {
  const notices = response.notices;
  switch (response.policy_outcome) {
    case 'ROUTE_OPTIONS_AVAILABLE':
      return (
        <>
          <p className="text-sm">{response.explanation}</p>
          <RouteList response={response} selectedId={selectedId} onSelect={onSelect} onOpen={onOpen} outdated={outdated} disclosure={disclosure} />
        </>
      );
    case 'SHELTER_IN_PLACE': {
      const shelter = notices.filter((n) => n.notice_type === 'shelter_in_place');
      const shown = shelter.length > 0 ? shelter : notices;
      return (
        <Card tone="danger" data-testid="shelter-in-place" className="space-y-3">
          <h4 className="text-lg font-bold">Shelter in place</h4>
          <p className="text-sm">{response.explanation}</p>
          {shown.length > 0 ? (
            <ul className="space-y-2">
              {shown.map((n) => (
                <NoticeCard key={n.notice_id} notice={n} onOpen={onOpen} />
              ))}
            </ul>
          ) : (
            <p data-testid="no-shelter" className="font-semibold">{NO_SHELTER_TEXT}</p>
          )}
          <Section title="Tornado guidance">
            <p className="text-sm">{TORNADO_GUIDANCE.text}</p>
            <p className="text-xs text-text-muted">
              <a href={TORNADO_GUIDANCE.url} target="_blank" rel="noopener noreferrer" className="text-route underline">
                {TORNADO_GUIDANCE.sourceName}
              </a>{' '}
              · Retrieved {TORNADO_GUIDANCE.retrievedAt}
            </p>
          </Section>
          <Call911 />
        </Card>
      );
    }
    case 'FOLLOW_OFFICIAL_INSTRUCTIONS':
      return (
        <Card tone="warning" data-testid="follow-instructions" className="space-y-3">
          <h4 className="text-lg font-bold">Follow official instructions</h4>
          <p className="text-sm">{response.explanation}</p>
          {notices.length > 0 && (
            <ul className="space-y-2">
              {notices.map((n) => (
                <NoticeCard key={n.notice_id} notice={n} onOpen={onOpen} />
              ))}
            </ul>
          )}
          <Call911 />
        </Card>
      );
    case 'NO_VERIFIED_ROUTE_AVAILABLE':
      return (
        <Card tone="danger" data-testid="no-verified-route" className="space-y-3">
          <h4 className="text-lg font-bold">{NO_ROUTE_TITLE}</h4>
          <p className="text-sm">{response.explanation}</p>
          <MissingData items={response.missing_data} />
          {notices.length > 0 && (
            <ul className="space-y-2">
              {notices.map((n) => (
                <NoticeCard key={n.notice_id} notice={n} onOpen={onOpen} />
              ))}
            </ul>
          )}
          <Call911 />
        </Card>
      );
    case 'INSUFFICIENT_DATA':
      return (
        <Card tone="warning" data-testid="insufficient-data" className="space-y-3">
          <h4 className="text-lg font-bold">Insufficient data</h4>
          <p className="text-sm">{response.explanation}</p>
          {response.missing_data.length > 0 ? (
            <MissingData items={response.missing_data} />
          ) : (
            <p className="text-sm">The missing data was not specified.</p>
          )}
          <Call911 />
        </Card>
      );
  }
}

/** Route results and policy outcome display (Req 15, 19, 34, 35, 36, 1.7, 17.7). */
export function RoutePanel({
  response, loading = false, error = null, recalc = null, selectedRouteId, onSelectRoute, onRecalculate, sourceRecords, disclosure = false,
}: RoutePanelProps) {
  const [localSel, setLocalSel] = useState<string | null>(null);
  const [popupId, setPopupId] = useState<string | null>(null);
  const selectedId = selectedRouteId ?? localSel ?? response?.routes[0]?.route_id ?? null;
  const select = (id: string) => {
    setLocalSel(id);
    onSelectRoute?.(id);
  };
  const records = Array.isArray(sourceRecords)
    ? Object.fromEntries(sourceRecords.map((s) => [s.source_id, s]))
    : (sourceRecords ?? {});
  const outdated = !!recalc && (recalc.updating || recalc.status === 'failed');

  return (
    <section aria-labelledby="route-panel-heading" data-testid="route-panel" className="space-y-3 p-3 text-text-primary">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="route-panel-heading" className="text-base font-semibold">Route</h3>
        {onRecalculate && <RecalculateButton onClick={onRecalculate} busy={loading || !!recalc?.updating} />}
      </div>

      <RouteChangeBanner recalc={recalc} />

      {loading && (
        <p role="status" data-testid="route-loading" className="text-sm">Calculating route…</p>
      )}
      {!!error && !loading && (
        <Card tone="danger" role="alert" data-testid="route-error" className="flex items-start gap-2 p-3 text-sm text-closure">
          <StatusIcon kind="cross" />
          <span>{errorMessage(error)}</span>
        </Card>
      )}

      <div aria-live="polite" data-testid="route-outcome" className="space-y-3">
        {!response && !loading && !error && <p className="text-sm text-text-muted">Choose a start and destination to see route options.</p>}
        {response && (
          <>
            <p className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
              <span className="rounded border border-border-subtle px-1.5 py-0.5">{response.label || ALGORITHM_LABEL}</span>
              <span data-testid="last-update">Last information update: {fmt(response.data_timestamp)}</span>
            </p>
            <OutcomeBody response={response} selectedId={selectedId} onSelect={select} onOpen={setPopupId} outdated={outdated} disclosure={disclosure} />
            {response.sources.length > 0 && response.policy_outcome !== 'ROUTE_OPTIONS_AVAILABLE' && (
              <Section title="Sources">
                <ul className="flex flex-wrap gap-3 text-sm">
                  {response.sources.map((s) => (
                    <li key={s}>
                      <SourceButton id={s} onOpen={setPopupId} />
                    </li>
                  ))}
                </ul>
              </Section>
            )}
          </>
        )}
      </div>

      {popupId && <SourceRecordPopup record={records[popupId]} onClose={() => setPopupId(null)} />}
    </section>
  );
}
