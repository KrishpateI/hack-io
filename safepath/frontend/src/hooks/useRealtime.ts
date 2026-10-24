import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import type { RealtimeEvent } from '../services/eventStore';
import { createClient, fetchEventsSince } from '../services/realtimeClients';
import { RealtimeController, type RealtimeSnapshot } from '../services/realtimeController';

/**
 * Subscribe to an incident's realtime channel; returns store + connectivity state.
 * Pass `null` when no incident is selected (nothing is opened). `onEvent` receives each raw,
 * de-duplicated event once (live or catch-up) and may change identity between renders.
 */
export function useRealtime(incidentId: string | null, onEvent?: (e: RealtimeEvent) => void): RealtimeSnapshot {
  const controller = useMemo(
    () => new RealtimeController({ client: createClient(), fetchSince: (id, since) => fetchEventsSince(id, since) }),
    [],
  );
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  }, [onEvent]);
  useEffect(() => controller.subscribeEvents((e) => handler.current?.(e)), [controller]);
  useEffect(() => {
    if (!incidentId) return;
    controller.start(incidentId);
    const on = () => controller.setOnline(true);
    const off = () => controller.setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
      controller.stop();
    };
  }, [controller, incidentId]);
  return useSyncExternalStore(controller.subscribe, controller.getSnapshot);
}
