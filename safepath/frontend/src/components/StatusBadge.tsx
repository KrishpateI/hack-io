import { StatusIcon, type IconKind } from './StatusIcon';
export type Status = 'verified' | 'unverified' | 'closed' | 'unknown';

const STYLE: Record<Status, { icon: IconKind; text: string; cls: string }> = {
  verified: { icon: 'check', text: 'Verified', cls: 'border-verified text-verified' },
  unverified: { icon: 'warning', text: 'Unverified', cls: 'border-unverified text-unverified' },
  closed: { icon: 'cross', text: 'Closed', cls: 'border-closure text-closure' },
  unknown: { icon: 'question', text: 'Unknown', cls: 'border-border-subtle text-text-muted' },
};

/** Icon + text + color; never color alone (Req 36.2). */
export function StatusBadge({ status }: { status?: Status | null }) {
  const s = STYLE[status ?? 'unknown'] ?? STYLE.unknown;
  return (
    <span data-testid="status-badge" className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${s.cls}`}>
      <StatusIcon kind={s.icon} />
      <span>{s.text}</span>
    </span>
  );
}

