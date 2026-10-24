import { ReportCard, type VoteHandler } from './ReportCard';
import type { CommunityReport } from './types';

export interface ReportListProps {
  reports: readonly CommunityReport[];
  ownReportIds?: ReadonlySet<string>;
  ownAlias?: string | null;
  onVote?: VoteHandler;
}

/** Live list of community reports, newest first. New items are announced politely. */
export function ReportList({ reports, ownReportIds, ownAlias, onVote }: ReportListProps) {
  const sorted = [...reports].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''));
  return (
    <section aria-labelledby="reports-heading" className="space-y-2">
      <h2 id="reports-heading" className="text-base font-semibold text-text-primary">Community reports</h2>
      <p className="text-xs text-text-muted">Reports come from other SafePath users and are not official information.</p>
      <div aria-live="polite" aria-relevant="additions" className="space-y-2">
        {sorted.length === 0 && <p className="text-sm text-text-muted">No community reports yet.</p>}
        {sorted.map((r) => (
          <ReportCard
            key={r.report_id}
            report={r}
            onVote={onVote}
            isOwn={ownReportIds?.has(r.report_id) === true || (!!ownAlias && r.alias === ownAlias)}
          />
        ))}
      </div>
    </section>
  );
}
