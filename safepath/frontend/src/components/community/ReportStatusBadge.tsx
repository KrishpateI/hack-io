import { SimulatedBadge } from '../SimulatedBadge';
import { StatusIcon, type IconKind } from '../StatusIcon';

export const CORROBORATED_TEXT = 'Corroborated by community \u2014 not officially verified';
export const OFFICIAL_TEXT = 'Official/Moderator-confirmed';

const STYLE: Record<string, { icon: IconKind; text: string; cls: string }> = {
  Unverified: { icon: 'warning', text: 'Unverified', cls: 'border-unverified text-unverified' },
  Corroborated: { icon: 'people', text: CORROBORATED_TEXT, cls: 'border-unverified text-unverified' },
  Officially_Verified: { icon: 'check', text: OFFICIAL_TEXT, cls: 'border-verified text-verified' },
  Disputed: { icon: 'cross', text: 'Disputed', cls: 'border-closure text-closure' },
  Resolved: { icon: 'check', text: 'Resolved', cls: 'border-border-subtle text-text-muted' },
  Expired: { icon: 'question', text: 'Expired', cls: 'border-border-subtle text-text-muted' },
};

/**
 * Verification_Status chip: icon + text, never color alone (Req 36.2). The Official/Moderator
 * label appears only for Officially_Verified (Req 24.5); SIMULATED is shown when simulated.
 */
export function ReportStatusBadge({ status, simulated }: { status: string; simulated?: boolean }) {
  const s = STYLE[status] ?? { icon: 'question' as IconKind, text: 'Unknown status', cls: 'border-border-subtle text-text-muted' };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span data-testid="report-status" className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${s.cls}`}>
        <StatusIcon kind={s.icon} />
        <span>{s.text}</span>
      </span>
      {simulated && <SimulatedBadge />}
    </span>
  );
}
