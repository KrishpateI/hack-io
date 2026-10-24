import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { ApiError, type JsonObject } from '../services/api';
import { getOfflineManager, type OfflineSnapshot, type QueuedResult, type QueueItem } from '../services/offline';

/** Offline_Manager state (offline flag, last sync time, queue). Does not attach listeners. */
export function useOffline(): OfflineSnapshot {
  const m = getOfflineManager();
  return useSyncExternalStore(m.subscribe, m.getSnapshot, m.getSnapshot);
}

/**
 * Mount once near the app root: attaches online/offline listeners, resumes the queue after a
 * reload, and feeds the realtime connection status and last-sync time into the manager.
 */
export function useOfflineLifecycle(realtime?: { status: string; lastSyncedAt: number | null }): void {
  const m = getOfflineManager();
  useEffect(() => m.start(), [m]);
  const status = realtime?.status;
  const syncedAt = realtime?.lastSyncedAt ?? null;
  useEffect(() => {
    if (status) m.setRealtimeStatus(status);
  }, [m, status]);
  useEffect(() => {
    m.noteSync(syncedAt);
  }, [m, syncedAt]);
}

export interface OfflineQueueView {
  items: readonly QueueItem[];
  pending: number;
  flushing: boolean;
  discard: (key: string) => void;
  edit: (key: string, text: string) => void;
  retryNow: () => void;
}

/** The pending queue and its actions (Req 41.4 to 41.8). */
export function useOfflineQueue(): OfflineQueueView {
  const snap = useOffline();
  const m = getOfflineManager();
  return {
    items: snap.queue.items,
    pending: snap.pending,
    flushing: snap.flushing,
    discard: useCallback((k: string) => m.discard(k), [m]),
    edit: useCallback((k: string, t: string) => m.edit(k, t), [m]),
    retryNow: useCallback(() => void m.retryNow(), [m]),
  };
}

/**
 * Wrap a direct send (message or check-in). When offline, or when the request fails at the
 * network level, the body is queued on this device and a `QueuedResult` is returned instead of a
 * server response. Callers must not present a queued result as sent.
 */
export function useQueuedSender(
  kind: 'message' | 'checkin',
  direct: (body: JsonObject) => Promise<unknown>,
): (body: JsonObject) => Promise<unknown> {
  return useCallback(
    async (body) => {
      const m = getOfflineManager();
      const queue = (): QueuedResult => {
        const r = m.queueItem(kind, body);
        if (!r.ok) throw new ApiError(0, 'offline_queue_full', r.message);
        return { queued: true, key: r.item.key };
      };
      if (m.isOffline()) return queue();
      try {
        const res = await direct(body);
        m.noteSuccess();
        return res;
      } catch (e) {
        if (e instanceof TypeError) {
          m.noteNetworkFailure();
          return queue();
        }
        throw e;
      }
    },
    [kind, direct],
  );
}

/** Handler for ReportForm.onOffline that stores the report in the queue. */
export function useQueuedReportHandler(): (body: JsonObject) => { ok: boolean; message?: string } {
  return useCallback((body) => {
    const r = getOfflineManager().queueItem('report', body);
    return r.ok ? { ok: true } : { ok: false, message: r.message };
  }, []);
}
