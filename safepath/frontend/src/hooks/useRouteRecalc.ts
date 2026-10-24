import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import type { JsonObject, RouteResponse } from '../services/api';
import type { EventStoreState } from '../services/eventStore';
import { RouteRecalcController, type RouteRecalcSnapshot } from '../services/routeRecalc';

type Entity = Record<string, unknown>;

/** Entities in the store whose `_event_id` is newer than `since` become pseudo-events for relevance checks. */
function newEntityEvents(store: EventStoreState, since: string | null) {
  const out: { type: string; incident_id: string; payload: Entity }[] = [];
  const scan = (bucket: Record<string, Entity>, type: string) => {
    for (const e of Object.values(bucket)) {
      const id = typeof e._event_id === 'string' ? e._event_id : '';
      if (id && (since === null || id > since)) out.push({ type, incident_id: '', payload: e });
    }
  };
  scan(store.restrictions, 'restriction_changed');
  scan(store.reports, 'report_status_changed');
  return out;
}

export interface UseRouteRecalcArgs {
  incidentId: string;
  /** Request body for POST /api/routes/calculate (origin, mode, ...). */
  requestBody: JsonObject;
  /** Latest calculation response for the active route (null before the first one). */
  initial: RouteResponse | null;
  /** Realtime store from useRealtime(). */
  store: EventStoreState;
  /** Realtime `stale` flag from useRealtime(). */
  realtimeStale: boolean;
}

/** Recalculate the active route when restriction events affect it; exposes route, previousRoute and change. */
export function useRouteRecalc(args: UseRouteRecalcArgs): RouteRecalcSnapshot {
  const controller = useMemo(() => new RouteRecalcController(), []);
  const seen = useRef<string | null>(null);

  useEffect(() => {
    controller.setActive(args.incidentId, args.requestBody, args.initial);
    seen.current = args.store.lastEventId; // only events after this point trigger recalculation
    return () => controller.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controller, args.incidentId, args.requestBody, args.initial]);

  useEffect(() => {
    const last = args.store.lastEventId;
    if (last === seen.current) return;
    const events = newEntityEvents(args.store, seen.current);
    seen.current = last;
    events.forEach((e) => controller.handleEvent({ ...e, incident_id: args.incidentId }));
  }, [controller, args.store, args.incidentId]);

  useEffect(() => controller.setRealtimeStale(args.realtimeStale), [controller, args.realtimeStale]);

  return useSyncExternalStore(controller.subscribe, controller.getSnapshot);
}
