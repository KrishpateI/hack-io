import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { createClient, fetchEventsSince, type RealtimeClient } from '../../services/realtimeClients';
import { RealtimeController, type RealtimeSnapshot } from '../../services/realtimeController';
import type { RealtimeEvent } from '../../services/eventStore';

export interface LogEntry {
  key: string;
  type: string;
  /** ms epoch when this window received the event. */
  receivedAt: number;
  createdAt: string;
  simulated: boolean;
  incidentId: string;
}

const MAX_LOG = 100;

function isSimulated(e: RealtimeEvent): boolean {
  const p = e.payload ?? {};
  return p.simulated === true || p.simulation_flag === true || p.classification === 'Simulated';
}

function toEntry(e: RealtimeEvent): LogEntry {
  return {
    key: e.event_id,
    type: e.type,
    receivedAt: Date.now(),
    createdAt: e.created_at,
    simulated: isSimulated(e),
    incidentId: e.incident_id,
  };
}

/** Wrap a client so every live event is also reported to `tap` (the store stays in the controller). */
function tapClient(inner: RealtimeClient, tap: (e: RealtimeEvent) => void): RealtimeClient {
  return {
    name: inner.name,
    connect: (id, h) => inner.connect(id, { ...h, onEvent: (e) => { tap(e); h.onEvent(e); } }),
    close: () => inner.close(),
  };
}

export interface EventLogState {
  snapshot: RealtimeSnapshot;
  log: LogEntry[];
  clearLog: () => void;
}

/**
 * Realtime subscription for the console: the normal controller/store (reconnect, catch-up) plus
 * a log of received events to prove two-window sync. A `scenario_reset` event drops the stale
 * client state by starting a fresh controller (empty store) and keeps only the reset entry.
 */
export function useEventLog(
  incidentId: string | null,
  makeClient: () => RealtimeClient = createClient,
  onReset?: (scenarioId: string | null) => void,
): EventLogState {
  const [epoch, setEpoch] = useState(0);
  const [log, setLog] = useState<LogEntry[]>([]);

  const controller = useMemo(() => {
    // `epoch` is a dependency on purpose: bumping it replaces the controller and its store.
    void epoch;
    return new RealtimeController({
      client: tapClient(makeClient(), (e) => {
        const entry = toEntry(e);
        if (e.type === 'scenario_reset') {
          setLog([entry]);
          setEpoch((n) => n + 1);
          const sid = e.payload?.scenario_id;
          onReset?.(typeof sid === 'string' ? sid : null);
        } else {
          setLog((l) => (l.some((x) => x.key === entry.key) ? l : [entry, ...l].slice(0, MAX_LOG)));
        }
      }),
      fetchSince: (id, since) => fetchEventsSince(id, since),
    });
  }, [epoch, makeClient, onReset]);

  useEffect(() => {
    if (!incidentId) return;
    controller.start(incidentId);
    return () => controller.stop();
  }, [controller, incidentId]);

  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const clearLog = useCallback(() => setLog([]), []);
  return { snapshot, log, clearLog };
}
