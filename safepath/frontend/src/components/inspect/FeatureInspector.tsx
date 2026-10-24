import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import type { RouteResponse } from '../../services/api';
import type { SourceRecord } from '../../services/sources';
import { SIMULATION_DESTINATION_LABEL } from '../../maps/layers/styles';
import { HistoricalLabel } from '../HistoricalLabel';
import { SimulatedBadge } from '../SimulatedBadge';
import { SourceRecordPopup } from '../SourceRecordPopup';
import { ReportStatusBadge } from '../community/ReportStatusBadge';
import { RouteDetails } from '../route/RoutePanel';
import type { EdgeDetail, EdgeReport } from './edgeService';
import {
  ELEVATION_CAVEAT,
  buildingSummary,
  edgeSourceIds,
  featureSourceId,
  findSource,
  formatText,
  hazardSummary,
  isHistorical,
  roadRows,
  stationSummary,
  type Candidate,
} from './inspectModel';
import type { UseFeatureInspector } from './useFeatureInspector';

type RouteT = RouteResponse['routes'][number];

export interface FeatureInspectorProps {
  inspector: UseFeatureInspector;
  /** Public reports of the incident (adds expiry, status label). */
  reports?: readonly EdgeReport[];
  /** Routes of the current response (for route clicks). */
  routes?: readonly RouteT[];
  /** Loaded Source_Records (rendered synchronously, no extra request). */
  sources?: readonly SourceRecord[];
  /** Layers derived from CBD data (Req 8.10). */
  historicalLayers?: readonly string[];
}

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-text-muted">{k}</dt>
          <dd className="break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function SourceButton({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  return (
    <button type="button" onClick={() => onOpen(id)} className="break-all rounded text-route underline focus:outline-2 focus:outline-route">
      Source {id}
    </button>
  );
}

function RoadCard({ edge, onOpen }: { edge: EdgeDetail; onOpen: (id: string) => void }) {
  const causes = edge.restrictions?.causes ?? [];
  const reports = edge.reports ?? [];
  const ids = edgeSourceIds(edge);
  return (
    <div className="space-y-3" data-testid="inspect-road">
      <Rows rows={roadRows(edge)} />
      <p className="text-xs text-text-muted" data-testid="elevation-caveat">
        Elevation is {ELEVATION_CAVEAT}.
      </p>
      <section aria-label="Closure causes" className="space-y-1">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">Active restrictions</h4>
        {causes.length === 0 ? (
          <p className="text-sm">
            {edge.restrictions?.available === false && edge.restrictions.incident_id
              ? 'Restriction data is unavailable for this incident.'
              : edge.restrictions?.incident_id
                ? 'No active restriction causes for this road.'
                : 'Select an incident to see active restrictions.'}
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {causes.map((c) => (
              <li key={`${c.kind}:${c.id}`} className="flex flex-wrap items-center gap-2">
                <span>Cause: {c.kind.replace(/_/g, ' ')}</span>
                {c.simulated && <SimulatedBadge />}
                {c.source_record_id ? <SourceButton id={c.source_record_id} onOpen={onOpen} /> : <span>Source: Unknown</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Active reports" className="space-y-1">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">Active hazard reports on this road</h4>
        {reports.length === 0 ? (
          <p className="text-sm">No active hazard reports on this road.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {reports.map((r) => (
              <li key={r.report_id} className="space-y-1">
                <ReportStatusBadge status={r.verification_status} simulated={r.simulated} />
                <p>
                  {formatText(r.category)}: {formatText(r.description)}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
      {ids.length > 0 && (
        <section aria-label="Source records" className="space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">Source records</h4>
          <ul className="flex flex-wrap gap-3 text-sm">
            {ids.map((id) => (
              <li key={id}>
                <SourceButton id={id} onOpen={onOpen} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {edge.classification === 'Simulated' && <SimulatedBadge />}
    </div>
  );
}

function HazardCard({ props, report }: { props: Record<string, unknown>; report?: EdgeReport | null }) {
  const h = hazardSummary(props, report);
  const status = (report?.verification_status as string | undefined) ?? (props.verification_status as string | undefined) ?? '';
  return (
    <div className="space-y-2" data-testid="inspect-hazard">
      <div className="flex flex-wrap items-center gap-2">
        <ReportStatusBadge status={status} simulated={h.simulated} />
      </div>
      <Rows
        rows={[
          ['Category', h.category],
          ['Description', h.description],
          ['Created', h.createdAt],
          ['Corroborating reports', String(h.corroborating)],
          ['Source category', h.sourceCategory],
          ['Expires', h.expiry],
        ]}
      />
    </div>
  );
}

function ZoneCard({ c, historical }: { c: Candidate; historical: boolean }) {
  const simulated = c.props.simulated === true;
  return (
    <div className="space-y-2" data-testid="inspect-zone">
      <div className="flex flex-wrap items-center gap-2">
        {simulated && <SimulatedBadge />}
        {historical && <HistoricalLabel />}
      </div>
      <Rows rows={[['Area', formatText(c.props.label ?? c.props.name)], ['Layer', formatText(c.layer)]]} />
      <p className="text-xs text-text-muted">Scenario area for the demonstration, not an official hazard boundary.</p>
    </div>
  );
}

function BuildingCard({ props }: { props: Record<string, unknown> }) {
  const b = buildingSummary(props);
  return (
    <div className="space-y-2" data-testid="inspect-building">
      <Rows
        rows={[
          ['Name', formatText(props.name)],
          ['Building type', formatText(props.building_type)],
          ['Building_Height_Class', b.heightClass],
          ['Height', b.height],
          ['Levels', b.levels],
        ]}
      />
      <p className="text-sm">{b.structural}</p>
    </div>
  );
}

function StationCard({ props }: { props: Record<string, unknown> }) {
  const s = stationSummary(props);
  return (
    <div className="space-y-2" data-testid="inspect-station">
      {s.simulated && <SimulatedBadge />}
      <Rows
        rows={[
          ['Station (GTFS)', s.name],
          ['Routes', s.routes],
          ['Wheelchair boarding', s.wheelchair],
          ['Source', formatText(props.source)],
        ]}
      />
      <p className="text-sm">{s.entranceSafety}</p>
    </div>
  );
}

export function FeatureInspector({ inspector, reports, routes, sources, historicalLayers }: FeatureInspectorProps) {
  const { open, selected, candidates, loading, error, announcement } = inspector;
  const headingId = useId();
  const panel = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const [opened, setOpened] = useState<{ forId: string | null; id: string } | null>(null);
  const [coords, setCoords] = useState({ lat: '', lon: '' });
  const [coordError, setCoordError] = useState<string | null>(null);

  // Move focus into the panel when it opens and give it back on close.
  useEffect(() => {
    if (open) {
      if (!returnFocus.current && document.activeElement instanceof HTMLElement && document.activeElement !== document.body) {
        returnFocus.current = document.activeElement;
      }
      panel.current?.focus();
    } else if (returnFocus.current) {
      returnFocus.current.focus();
      returnFocus.current = null;
    }
  }, [open]);

  // A Source_Record opened on one feature is not carried over to another.
  const selectedId = selected?.id ?? null;
  const openSourceId = opened && opened.forId === selectedId ? opened.id : null;
  const setOpenSourceId = (id: string | null) => setOpened(id ? { forId: selectedId, id } : null);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      inspector.close();
    }
  };

  const submitCoords = () => {
    const lat = Number(coords.lat);
    const lon = Number(coords.lon);
    if (coords.lat.trim() === '' || coords.lon.trim() === '' || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
      setCoordError('Enter a latitude between -90 and 90 and a longitude between -180 and 180.');
      return;
    }
    setCoordError(null);
    inspector.inspectCoordinate(lat, lon);
  };

  const live = (
    <div aria-live="polite" role="status" className="sr-only" data-testid="inspect-live">
      {announcement}
    </div>
  );
  if (!open) return live;

  const c = selected;
  const record = (id: string | null) => findSource(sources, id);
  const onOpenSource = (id: string) => setOpenSourceId(id);

  let body: ReactNode = <p className="text-sm">No feature selected.</p>;
  let ownSourceId: string | null = null;
  let historical = false;
  let simulated = false;
  if (c) {
    simulated = c.props.simulated === true || c.edge?.classification === 'Simulated';
    ownSourceId = featureSourceId(c.props);
    historical = isHistorical(c.props, c.layer, historicalLayers, record(ownSourceId));
    const reportId = typeof c.props.report_id === 'string' ? c.props.report_id : null;
    switch (c.kind) {
      case 'road':
        body = c.edge ? <RoadCard edge={c.edge} onOpen={onOpenSource} /> : null;
        break;
      case 'report':
        body = <HazardCard props={c.props} report={reports?.find((r) => r.report_id === reportId)} />;
        break;
      case 'closure':
        body = reportId ? (
          <HazardCard props={c.props} report={reports?.find((r) => r.report_id === reportId)} />
        ) : (
          <div className="space-y-2" data-testid="inspect-closure">
            <Rows rows={[['Closure', formatText(c.props.label ?? c.props.name)], ['Status', formatText(c.props.status ?? 'Closed')]]} />
          </div>
        );
        break;
      case 'route': {
        const id = typeof c.props.route_id === 'string' ? c.props.route_id : null;
        const route = routes?.find((r) => r.route_id === id);
        body = route ? (
          <RouteDetails route={route} onOpen={onOpenSource} outdated={false} />
        ) : (
          <p className="text-sm" data-testid="inspect-route-missing">
            {formatText(c.props.label)}. Route details are not available for this route.
          </p>
        );
        break;
      }
      case 'building':
        body = <BuildingCard props={c.props} />;
        break;
      case 'station':
        body = <StationCard props={c.props} />;
        break;
      case 'hazard_zone':
        body = <ZoneCard c={c} historical={historical} />;
        break;
      case 'destination':
        body = (
          <div className="space-y-2" data-testid="inspect-destination">
            <Rows rows={[['Name', formatText(c.props.name ?? c.props.label)]]} />
            {c.props.simulated === true && <p className="text-sm">{SIMULATION_DESTINATION_LABEL}</p>}
          </div>
        );
        break;
      case 'official':
        body = (
          <Rows rows={[['Feature', formatText(c.props.label ?? c.props.name)], ['Layer', formatText(c.layer)]]} />
        );
        break;
    }
  }

  // Official-data features show their Source_Record immediately; other cards on request.
  const autoSource = c && (c.kind === 'official' || c.kind === 'closure') ? ownSourceId : null;
  const shownSourceId = openSourceId ?? autoSource;
  const showSource = shownSourceId !== null || (c?.kind === 'official' && !autoSource);

  return (
    <>
      {live}
      <section
        ref={panel}
        tabIndex={-1}
        role="region"
        aria-labelledby={headingId}
        onKeyDown={onKeyDown}
        data-testid="feature-inspector"
        className="space-y-3 rounded border border-border-subtle bg-surface-raised p-3 text-sm text-text focus:outline-2 focus:outline-route"
      >
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={headingId} className="text-base font-semibold">
            {c ? c.title : 'Map feature details'}
          </h3>
          {simulated && <SimulatedBadge />}
          {historical && c?.kind !== 'hazard_zone' && <HistoricalLabel />}
          <button
            type="button"
            onClick={inspector.close}
            className="ml-auto rounded border border-border-subtle px-2 py-0.5 focus:outline-2 focus:outline-route"
          >
            Close
          </button>
        </div>
        {loading && <p data-testid="inspect-loading">Looking up nearby roads…</p>}
        {error && <p role="alert" className="text-unverified">{error}</p>}
        {!loading && !c && !error && <p>No map features found at this location.</p>}
        {body}
        {showSource && <SourceRecordPopup record={record(shownSourceId)} onClose={openSourceId ? () => setOpenSourceId(null) : undefined} />}
        {candidates.length > 1 && (
          <nav aria-label="Nearby features" className="space-y-1">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">Nearby features</h4>
            <ul className="space-y-1">
              {candidates.map((x) => (
                <li key={x.id}>
                  <button
                    type="button"
                    aria-current={x.id === selectedId ? 'true' : undefined}
                    onClick={() => inspector.select(x.id)}
                    className={`w-full rounded border px-2 py-1 text-left focus:outline-2 focus:outline-route ${
                      x.id === selectedId ? 'border-route font-semibold' : 'border-border-subtle'
                    }`}
                  >
                    {x.id === selectedId ? '● ' : ''}
                    {x.title}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <form
          aria-label="Inspect a location by coordinates"
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submitCoords();
          }}
        >
          <label className="flex flex-col text-xs text-text-muted">
            Latitude
            <input
              inputMode="decimal"
              value={coords.lat}
              onChange={(e) => setCoords((s) => ({ ...s, lat: e.target.value }))}
              className="w-28 rounded border border-border-subtle bg-surface px-2 py-1 text-sm text-text"
            />
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Longitude
            <input
              inputMode="decimal"
              value={coords.lon}
              onChange={(e) => setCoords((s) => ({ ...s, lon: e.target.value }))}
              className="w-28 rounded border border-border-subtle bg-surface px-2 py-1 text-sm text-text"
            />
          </label>
          <button type="submit" className="rounded border border-border-subtle px-2 py-1 focus:outline-2 focus:outline-route">
            Inspect location
          </button>
          {coordError && (
            <p role="alert" className="w-full text-unverified">
              {coordError}
            </p>
          )}
        </form>
      </section>
    </>
  );
}
