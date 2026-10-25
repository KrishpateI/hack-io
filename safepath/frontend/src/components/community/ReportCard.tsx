import { useState } from 'react';
import { corroborateReport, disputeReport } from '../../services/api';
import { toFailure, formatTime, type CommunityReport } from './types';
import { ReportStatusBadge } from './ReportStatusBadge';

export type VoteKind = 'corroborate' | 'dispute';
export type VoteHandler = (reportId: string, kind: VoteKind) => Promise<unknown>;

const defaultVote: VoteHandler = (id, kind) => (kind === 'corroborate' ? corroborateReport(id) : disputeReport(id));

const BTN =
  'min-h-[var(--target-min)] rounded-[var(--radius-control)] border border-border-subtle px-4 py-1.5 text-sm text-text-primary hover:bg-surface-raised focus:outline-none focus-visible:ring-2 focus-visible:ring-route disabled:cursor-not-allowed disabled:opacity-60';

interface Pending {
  confirmations: number;
  disputes: number;
  baseConfirmations: number;
  baseDisputes: number;
}

export interface ReportCardProps {
  report: CommunityReport;
  /** True when the viewer authored this report (buttons are disabled, Req 24.1). */
  isOwn?: boolean;
  onVote?: VoteHandler;
}

export function ReportCard({ report, isOwn = false, onVote = defaultVote }: ReportCardProps) {
  const [busy, setBusy] = useState<VoteKind | null>(null);
  const [voted, setVoted] = useState<VoteKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Optimistic delta, valid only while the server counts still equal what they were at vote time.
  const [pending, setPending] = useState<Pending | null>(null);

  const applies = pending && pending.baseConfirmations === report.confirmations && pending.baseDisputes === report.disputes;
  const confirmations = report.confirmations + (applies ? pending.confirmations : 0);
  const disputes = report.disputes + (applies ? pending.disputes : 0);

  const closed = report.verification_status === 'Resolved' || report.verification_status === 'Expired';
  const disabled = isOwn || closed || busy !== null || voted !== null;
  const reason = isOwn
    ? 'You cannot corroborate or dispute your own report.'
    : closed
      ? 'This report is no longer active.'
      : voted
        ? 'Your response was recorded.'
        : null;

  async function vote(kind: VoteKind) {
    setError(null);
    setBusy(kind);
    setPending({
      confirmations: kind === 'corroborate' ? 1 : 0,
      disputes: kind === 'dispute' ? 1 : 0,
      baseConfirmations: report.confirmations,
      baseDisputes: report.disputes,
    });
    try {
      await onVote(report.report_id, kind);
      setVoted(kind);
    } catch (e) {
      setPending(null);
      setError(toFailure(e).message);
    } finally {
      setBusy(null);
    }
  }

  const headingId = `report-${report.report_id}-title`;
  const reasonId = `report-${report.report_id}-reason`;
  return (
    <article
      aria-labelledby={headingId}
      data-testid="report-card"
      className="space-y-2 rounded-[var(--radius-card)] border border-border-subtle bg-surface p-4 text-sm text-text-primary shadow-[var(--shadow-card)]"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="font-semibold capitalize">{report.category}</h3>
        <ReportStatusBadge status={report.verification_status} simulated={report.simulated} />
      </header>
      {report.description && <p className="break-words">{report.description}</p>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-xs text-text-muted">
        <dt>Source</dt>
        <dd data-testid="report-source">{report.source_label}</dd>
        <dt>Reported</dt>
        <dd><time dateTime={report.created_at ?? undefined}>{formatTime(report.created_at)}</time></dd>
        <dt>By</dt>
        <dd>{report.alias ?? 'Anonymous'}{isOwn ? ' (you)' : ''}</dd>
        <dt>Responses</dt>
        <dd data-testid="report-counts">{confirmations} corroborations, {disputes} disputes</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} disabled={disabled} aria-describedby={reason ? reasonId : undefined} onClick={() => void vote('corroborate')}>
          {busy === 'corroborate' ? 'Corroborating\u2026' : 'Corroborate'}
        </button>
        <button type="button" className={BTN} disabled={disabled} aria-describedby={reason ? reasonId : undefined} onClick={() => void vote('dispute')}>
          {busy === 'dispute' ? 'Disputing\u2026' : 'Dispute'}
        </button>
      </div>
      {reason && <p id={reasonId} className="text-xs text-text-muted">{reason}</p>}
      {error && <p role="alert" className="text-xs text-closure">{error}</p>}
    </article>
  );
}
