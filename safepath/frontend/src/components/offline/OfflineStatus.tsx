import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { useOffline, useOfflineQueue } from '../../hooks/useOffline';
import { KIND_LABEL, QUEUED_DETAIL, statusText, textFieldOf, type QueueItem } from '../../services/offline';
import { MapCache, pmtilesUrlFromEnv } from '../../services/offlineMap';
import { StatusIcon } from '../StatusIcon';

/** Full local date and time, used in "last synchronized" and "Cached as of" labels. */
export function formatStamp(ms: number | null): string {
  return ms === null ? 'an unknown time' : new Date(ms).toLocaleString();
}

export const OUTDATED_NOTE = 'Conditions shown may be out of date.';

/** Req 41.3: label for cached hazard and closure data. */
export function cachedAsOf(ms: number | null): string {
  return `Cached as of ${formatStamp(ms)} \u2014 may be outdated`;
}

export interface OfflineBannerProps {
  lastSyncAt: number | null;
  pending: number;
}

/** Persistent offline banner. It has no dismiss control: offline state must stay visible (Req 41.2). */
export function OfflineBanner({ lastSyncAt, pending }: OfflineBannerProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="offline-banner"
      className="border-b-2 border-closure bg-surface px-3 py-2 text-sm text-text-primary"
    >
      <p className="flex flex-wrap items-center gap-x-2 font-semibold text-closure">
        <StatusIcon kind="cross" />
        <span>You are offline.</span>
        <span data-testid="offline-last-sync" className="font-normal text-text-primary">
          Last synchronized at {formatStamp(lastSyncAt)}.
        </span>
      </p>
      <p>{OUTDATED_NOTE}</p>
      <p data-testid="offline-cached-label" className="text-xs text-text-muted">
        Hazard and closure data: {cachedAsOf(lastSyncAt)}
      </p>
      {pending > 0 && (
        <p data-testid="offline-pending" className="text-xs text-unverified">
          {pending} item{pending === 1 ? '' : 's'} saved on this device and not sent.
        </p>
      )}
    </div>
  );
}

function summary(item: QueueItem): string {
  const b = item.body;
  if (item.kind === 'report') return `${String(b.category ?? 'report')}${b.description ? `: ${String(b.description)}` : ''}`;
  if (item.kind === 'message') return String(b.content ?? '');
  return String(b.status ?? 'check-in');
}

function QueueRow({ item, onDiscard, onEdit }: { item: QueueItem; onDiscard: () => void; onEdit: (t: string) => void }) {
  const field = textFieldOf(item.kind);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(field ? String(item.body[field] ?? '') : '');
  const canAct = item.status === 'queued' || item.status === 'rejected';
  const tone = item.status === 'rejected' ? 'text-closure' : item.status === 'sent' ? 'text-verified' : 'text-unverified';
  return (
    <li data-testid="queue-item" data-status={item.status} className="space-y-1 rounded border border-border-subtle p-2">
      <p className="break-words">
        <span className="font-medium">{KIND_LABEL[item.kind]}</span>
        <span className="text-text-muted">{' \u00b7 '}{summary(item)}</span>
      </p>
      <p data-testid="queue-item-status" className={`text-xs font-semibold ${tone}`}>{statusText(item)}</p>
      {item.status === 'queued' && <p className="text-xs text-text-muted">{QUEUED_DETAIL}</p>}
      {editing && field && (
        <div className="space-y-1">
          <label className="block text-xs font-medium">
            {field === 'content' ? 'Message text' : 'Description'}
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded border border-border-subtle bg-base px-2 py-1.5 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              onEdit(text);
              setEditing(false);
            }}
            className="min-h-11 rounded bg-route px-3 text-sm font-semibold text-base"
          >
            Save and queue again
          </button>
        </div>
      )}
      {canAct && !editing && (
        <div className="flex flex-wrap gap-2">
          {field && item.status === 'rejected' && (
            <button type="button" onClick={() => setEditing(true)} className="min-h-11 rounded border border-route px-3 text-sm text-route">
              Edit
            </button>
          )}
          <button type="button" onClick={onDiscard} className="min-h-11 rounded border border-border-subtle px-3 text-sm">
            Discard
          </button>
        </div>
      )}
    </li>
  );
}

/** Items kept on this device with their per-item state (Req 41.4, 41.5, 41.7). */
export function OfflineQueuePanel() {
  const q = useOfflineQueue();
  const { offline } = useOffline();
  if (q.items.length === 0) return null;
  const waiting = q.items.some((i) => i.status === 'queued');
  return (
    <section aria-label="Items waiting on this device" data-testid="offline-queue" className="space-y-2 border-b border-border-subtle bg-surface px-3 py-2 text-sm">
      <h2 className="text-sm font-semibold">Saved on this device</h2>
      <ul className="space-y-2">
        {[...q.items]
          .sort((a, b) => a.seq - b.seq)
          .map((i) => (
            <QueueRow key={i.key} item={i} onDiscard={() => q.discard(i.key)} onEdit={(t) => q.edit(i.key, t)} />
          ))}
      </ul>
      {waiting && !offline && (
        <button type="button" onClick={q.retryNow} disabled={q.flushing} className="min-h-11 rounded border border-route px-3 text-sm text-route disabled:opacity-60">
          {q.flushing ? 'Sending\u2026' : 'Send now'}
        </button>
      )}
    </section>
  );
}

/** Opt-in download of the basemap for offline use (one file, a few MB). */
export function OfflineMapDownload({ cache }: { cache?: MapCache }) {
  const c = useMemo(() => cache ?? new MapCache(pmtilesUrlFromEnv()), [cache]);
  useEffect(() => c.start(), [c]);
  const snap = useSyncExternalStore(c.subscribe, c.getSnapshot, c.getSnapshot);
  if (snap.state === 'unsupported') return null;
  return (
    <div data-testid="map-download" className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-surface px-3 py-1 text-xs">
      {snap.state === 'cached' ? (
        <span>Map saved on this device for offline use.</span>
      ) : snap.state === 'downloading' ? (
        <span role="status">{'Saving the map to this device\u2026'}</span>
      ) : (
        <button type="button" onClick={() => c.download()} className="min-h-11 rounded border border-route px-3 text-route">
          Save map for offline use
        </button>
      )}
      {snap.state === 'error' && <span role="alert" className="text-closure">Map download failed: {snap.message}</span>}
    </div>
  );
}

/** Everything offline-related that sits above the header: banner, queue, map download. */
export function OfflineStatus() {
  const { offline, lastSyncAt, pending } = useOffline();
  return (
    <div data-testid="offline-status">
      {offline && <OfflineBanner lastSyncAt={lastSyncAt} pending={pending} />}
      <OfflineQueuePanel />
      <OfflineMapDownload />
    </div>
  );
}
