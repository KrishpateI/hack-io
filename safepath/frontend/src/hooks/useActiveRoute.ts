import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { calculateRoute, type JsonObject, type RouteResponse } from '../services/api';
import type { RealtimeEvent } from '../services/eventStore';
import { RouteRecalcController, type RouteRecalcSnapshot } from '../services/routeRecalc';

const KEEP_RESPONSES = 8;

export interface UseActiveRouteArgs {
  incidentId: string | null;
  /** POST /api/routes/calculate body; null while start/destination are not both set. */
  body: JsonObject | null;
  /** Realtime `stale` flag: road conditions must not be shown as current. */
  stale: boolean;
}

export interface UseActiveRoute {
  /** Latest full response (initial calculation or the newest automatic recalculation). */
  response: RouteResponse | null;
  loading: boolean;
  error: unknown;
  recalc: RouteRecalcSnapshot;
  /** Manual recalculation (coalesced with automatic ones inside a 1 s window). */
  recalculate: () => void;
  /** Feed raw realtime events; restriction and Officially_Verified report events trigger recalculation. */
  handleEvent: (event: RealtimeEvent) => void;
}

/**
 * Route lifecycle for the dashboard: an initial calculation whenever the request changes, then
 * automatic recalculation on relevant raw realtime events through RouteRecalcController. Raw
 * events are used (not a store diff) so restriction removals are distinguished from additions.
 */
export function useActiveRoute({ incidentId, body, stale }: UseActiveRouteArgs): UseActiveRoute {
  const [responses, setResponses] = useState<Record<string, RouteResponse>>({});
  const [result, setResult] = useState<{ key: string; response?: RouteResponse; error?: unknown } | null>(null);
  const [nonce, setNonce] = useState(0);

  const remember = useCallback((res: RouteResponse) => {
    setResponses((prev) => {
      const next = { ...prev, [res.calculated_at]: res };
      const keys = Object.keys(next);
      if (keys.length > KEEP_RESPONSES) for (const k of keys.sort().slice(0, keys.length - KEEP_RESPONSES)) delete next[k];
      return next;
    });
  }, []);

  const controller = useMemo(
    () =>
      new RouteRecalcController({
        calculate: async (b, signal) => {
          const res = await calculateRoute(b, signal);
          remember(res);
          return res;
        },
      }),
    [remember],
  );
  const recalc = useSyncExternalStore(controller.subscribe, controller.getSnapshot);

  const key = body && incidentId ? `${incidentId}|${JSON.stringify(body)}|${nonce}` : null;

  useEffect(() => {
    if (!body || !incidentId || !key) return;
    const ctl = new AbortController();
    calculateRoute(body, ctl.signal).then(
      (res) => {
        if (ctl.signal.aborted) return;
        remember(res);
        setResult({ key, response: res });
        controller.setActive(incidentId, body, res);
      },
      (error: unknown) => {
        if (ctl.signal.aborted) return;
        setResult({ key, error });
        controller.setActive(incidentId, body, null);
      },
    );
    return () => ctl.abort();
  }, [controller, incidentId, body, key, remember]);

  useEffect(() => () => controller.stop(), [controller]);
  useEffect(() => controller.setRealtimeStale(stale), [controller, stale]);

  const handleEvent = useCallback(
    (e: RealtimeEvent) => controller.handleEvent({ type: e.type, incident_id: e.incident_id, payload: e.payload }),
    [controller],
  );

  const recalculate = useCallback(() => {
    if (result?.response) controller.requestRecalc();
    else setNonce((n) => n + 1);
  }, [controller, result]);

  const current = key !== null && result?.key === key ? result : null;
  const response = current ? (responses[recalc.calculatedAt ?? ''] ?? current.response ?? null) : null;
  return {
    response,
    loading: key !== null && current === null,
    error: current?.error ?? null,
    recalc,
    recalculate,
    handleEvent,
  };
}
