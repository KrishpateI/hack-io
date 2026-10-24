import type { ConnectionStatus } from '../../services/realtimeController';
import { StatusIcon, type IconKind } from '../StatusIcon';

export const STALE_HEADER_MESSAGE = 'Road conditions may be out of date';

export type ConnectivityState = ConnectionStatus | 'idle';

const STYLE: Record<ConnectivityState, { icon: IconKind; label: string; cls: string }> = {
  connected: { icon: 'check', label: 'Connected', cls: 'border-verified text-verified' },
  reconnecting: { icon: 'warning', label: 'Reconnecting', cls: 'border-unverified text-unverified' },
  offline: { icon: 'cross', label: 'Offline', cls: 'border-closure text-closure' },
  idle: { icon: 'question', label: 'No live channel', cls: 'border-border-subtle text-text-muted' },
};

export function formatSyncTime(ms: number | null, locale?: string): string {
  if (ms === null) return 'not yet';
  return new Date(ms).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export interface ConnectivityStatusProps {
  status: ConnectivityState;
  lastSyncedAt: number | null;
  stale: boolean;
}

/** Connection state as icon + text + color, last sync time and the stale-data warning (Req 25, 34.1). */
export function ConnectivityStatus({ status, lastSyncedAt, stale }: ConnectivityStatusProps) {
  const s = STYLE[status];
  return (
    <div data-testid="connectivity" className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span role="status" aria-live="polite" data-testid="connectivity-status" className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 ${s.cls}`}>
        <StatusIcon kind={s.icon} />
        <span>{s.label}</span>
      </span>
      <span data-testid="last-synced" className="text-text-muted">
        Last synced: {formatSyncTime(lastSyncedAt)}
      </span>
      {stale && (
        <span
          role="alert"
          data-testid="stale-indicator"
          className="inline-flex items-center gap-1 rounded border border-unverified px-1.5 py-0.5 font-semibold text-unverified"
        >
          <StatusIcon kind="warning" />
          <span>{STALE_HEADER_MESSAGE}</span>
        </span>
      )}
    </div>
  );
}
