import { OfficialNotices } from '../components/community/OfficialNotices';
import { ReportStatusBadge } from '../components/community/ReportStatusBadge';
import type { CommunityReport, OfficialNoticeItem } from '../components/community/types';
import { HistoricalLabel } from '../components/HistoricalLabel';
import { ScreenFrame } from '../components/shell/ScreenFrame';
import { screenTitle } from '../components/shell/screens';
import { boundarySummary } from '../components/sidebar/ActiveIncident';
import { disasterLabel } from '../components/sidebar/disasters';
import { SimulatedBadge } from '../components/SimulatedBadge';
import type { Incident } from '../services/api';

export interface IncidentOverviewScreenProps {
  incident: Incident | null;
  reports: readonly CommunityReport[];
  /** Official notices only (each carries its Source_Record). */
  notices: readonly OfficialNoticeItem[];
  activeRestrictionCount: number;
  /** True when a CBD-derived historical reference is shown for this incident. */
  showCbdReference?: boolean;
  onClose: () => void;
}

const STATUSES = ['Unverified', 'Corroborated', 'Officially_Verified', 'Disputed', 'Resolved', 'Expired'] as const;

export function countByStatus(reports: readonly CommunityReport[]): Record<string, number> {
  const out: Record<string, number> = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const r of reports) out[r.verification_status] = (out[r.verification_status] ?? 0) + 1;
  return out;
}

export function IncidentOverviewScreen({ incident, reports, notices, activeRestrictionCount, showCbdReference, onClose }: IncidentOverviewScreenProps) {
  const counts = countByStatus(reports);
  return (
    <ScreenFrame title={screenTitle('incident-overview')} onClose={onClose} testId="screen-incident-overview">
      {!incident ? (
        <p className="text-sm text-text-muted">No incident selected.</p>
      ) : (
        <section aria-labelledby="ov-incident-h" className="space-y-1 rounded border border-border-subtle bg-surface-raised p-3 text-sm">
          <h2 id="ov-incident-h" className="text-base font-semibold">{disasterLabel(incident.incident_type)} incident</h2>
          {incident.simulation_flag && (
            <p className="flex flex-wrap items-center gap-2"><SimulatedBadge /><span>This is a simulated scenario, not a real event.</span></p>
          )}
          <p>Status: {incident.status}</p>
          <p>Boundary: {boundarySummary(incident)}</p>
          <p className="text-xs text-text-muted">Incident areas are not official evacuation boundaries.</p>
          <p>Source: {incident.source}</p>
          <p data-testid="restriction-count">Active restrictions: {activeRestrictionCount}</p>
        </section>
      )}
      <OfficialNotices notices={notices} />
      <section aria-labelledby="ov-reports-h" data-testid="report-counts" className="space-y-1">
        <h2 id="ov-reports-h" className="text-base font-semibold">Community reports by verification status</h2>
        <ul className="space-y-1">
          {STATUSES.map((s) => (
            <li key={s} className="flex items-center gap-2 text-sm">
              <ReportStatusBadge status={s} />
              <span>{counts[s]}</span>
            </li>
          ))}
        </ul>
      </section>
      {showCbdReference && (
        <section aria-labelledby="ov-cbd-h" className="space-y-1 rounded border border-unverified p-3 text-sm">
          <h2 id="ov-cbd-h" className="text-base font-semibold">Central Business District reference</h2>
          <HistoricalLabel />
        </section>
      )}
    </ScreenFrame>
  );
}
