/**
 * Hazard areas and restricted edges for the selected incident (map-legend-layers-fix, task 3.5).
 *
 * Loads GET /api/incidents/{id}/map-overlays on selection, merges live restriction events on top
 * (`mergeRestrictions`) and refetches (debounced) when restriction or notice events arrive. Responses
 * and events for any other incident are ignored; the last good snapshot is kept while a refetch
 * fails and is retried with backoff.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import { ApiError, getMapOverlays, type MapOverlays } from '../services/api';
import { backoffDelay } from '../services/backoff';
import {
  EMPTY_OVERLAYS,
  mergeRestrictions,
  overlayLayerInputs,
  type MergedOverlays,
  type OverlayEventLike,
  type OverlaySnapshotLike,
} from '../services/mapData';

export const OVERLAY_REFETCH_DEBOUNCE_MS = 300;
/** Upper bound on buffered live restriction events per incident. */
const MAX_BUFFERED_EVENTS = 500;

// ------------------------------------------------------------------ pure state
export interface OverlayState {
  incidentId: string | null;
  snapshot: OverlaySnapshotLike | null;
  /** Live restriction_* events for `incidentId` that are newer than the snapshot. */
  events: OverlayEventLike[];
  /** `loading` until the first snapshot for the selection; `failed` after a failed (re)fetch. */
  status: OverlayLoadStatus;
}

export type OverlayLoadStatus = 'idle' | 'loading' | 'loaded' | 'failed';

export type OverlayAction =
  | { type: 'select'; incidentId: string | null }
  | { type: 'snapshot'; snapshot: OverlaySnapshotLike }
  | { type: 'failed'; incidentId: string }
  | { type: 'event'; event: OverlayEventLike };

export const INITIAL_OVERLAY_STATE: OverlayState = { incidentId: null, snapshot: null, events: [], status: 'idle' };

const eventVersion = (e: OverlayEventLike): number | null => {
  const v = e.payload?.overlay_version;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
};

const isRestrictionEvent = (type: string) => type.toLowerCase().startsWith('restriction');
const isRefetchEvent = (type: string) => {
  const t = type.toLowerCase();
  return t.startsWith('restriction_') || t.startsWith('notice_');
};

/**
 * Selection, snapshot and event transitions. A snapshot or event for any incident other than the
 * selected one is ignored; selecting another incident (or none) clears everything.
 */
export function overlayReducer(state: OverlayState, action: OverlayAction): OverlayState {
  switch (action.type) {
    case 'select':
      return action.incidentId === state.incidentId
        ? state
        : { incidentId: action.incidentId, snapshot: null, events: [], status: action.incidentId ? 'loading' : 'idle' };
    case 'failed':
      // the last good snapshot stays drawn
      return action.incidentId === state.incidentId && state.status !== 'failed' ? { ...state, status: 'failed' } : state;
    case 'snapshot': {
      const s = action.snapshot;
      if (!state.incidentId || s.incident_id !== state.incidentId) return state;
      // an older response arriving after a newer one does not roll the state back
      if (state.snapshot && s.overlay_version < state.snapshot.overlay_version) return state;
      const events = state.events.filter((e) => {
        const v = eventVersion(e);
        return v !== null && v > s.overlay_version;
      });
      return { ...state, snapshot: s, events, status: 'loaded' };
    }
    case 'event': {
      const e = action.event;
      if (!state.incidentId || !isRestrictionEvent(e.type)) return state;
      if (e.incident_id && e.incident_id !== state.incidentId) return state;
      if (e.event_id && state.events.some((x) => x.event_id === e.event_id)) return state;
      const v = eventVersion(e);
      if (v === null || (state.snapshot && v <= state.snapshot.overlay_version)) return state;
      return { ...state, events: [...state.events, e].slice(-MAX_BUFFERED_EVENTS) };
    }
    default:
      return state;
  }
}

/** Merged overlays for the state; empty unless the state belongs to `selectedId`. */
export function selectMerged(state: OverlayState, selectedId: string | null): MergedOverlays {
  if (!selectedId || state.incidentId !== selectedId) return EMPTY_OVERLAYS;
  const merged = mergeRestrictions(state.snapshot, state.events);
  return state.snapshot ? merged : { ...merged, incidentId: selectedId };
}

// ------------------------------------------------------------------ hook
export type OverlayFetcher = (incidentId: string, signal: AbortSignal) => Promise<MapOverlays | OverlaySnapshotLike>;

export interface UseMapOverlaysOptions {
  fetcher?: OverlayFetcher;
  debounceMs?: number;
  /** Random source for the backoff jitter (tests). */
  random?: () => number;
}

export interface UseMapOverlays {
  merged: MergedOverlays;
  /** `hazards` and `restrictions` layer inputs. */
  inputs: ReturnType<typeof overlayLayerInputs>;
  /** Edge ids of restrictions added after the snapshot (draw via `useEdgeCoordinates` meanwhile). */
  pendingEdgeIds: string[];
  /** Raw `MapOverlays` payload of the last good snapshot for the selection (disclaimer, limitations). */
  snapshot: OverlaySnapshotLike | null;
  status: OverlayLoadStatus;
  /** Realtime sink: buffers restriction events and schedules a debounced refetch. */
  handleEvent: (event: OverlayEventLike) => void;
  /** Refetch now. */
  refresh: () => void;
}

/** Not worth retrying: the incident does not exist. */
const permanent = (err: unknown) => err instanceof ApiError && err.status === 404;

export function useMapOverlays(incidentId: string | null, opts: UseMapOverlaysOptions = {}): UseMapOverlays {
  const [state, dispatch] = useReducer(overlayReducer, INITIAL_OVERLAY_STATE);

  const optsRef = useRef(opts);
  useEffect(() => {
    optsRef.current = opts;
  });
  const loadRef = useRef<(id: string) => void>(() => undefined);
  const current = useRef<string | null>(incidentId);
  const ctrl = useRef<AbortController | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attempt = useRef(0);

  const clearTimer = (t: { current: ReturnType<typeof setTimeout> | null }) => {
    if (t.current !== null) clearTimeout(t.current);
    t.current = null;
  };

  const load = useCallback((id: string) => {
    ctrl.current?.abort();
    clearTimer(retryTimer);
    const c = new AbortController();
    ctrl.current = c;
    const fetcher = optsRef.current.fetcher ?? getMapOverlays;
    fetcher(id, c.signal).then(
      (snap) => {
        if (c.signal.aborted || current.current !== id) return;
        // a response for another incident (stale or misrouted) is discarded by the reducer
        attempt.current = 0;
        dispatch(snap.incident_id === id ? { type: 'snapshot', snapshot: snap } : { type: 'failed', incidentId: id });
      },
      (err: unknown) => {
        if (c.signal.aborted || current.current !== id) return;
        dispatch({ type: 'failed', incidentId: id });
        if (permanent(err)) return;
        const delay = backoffDelay(attempt.current++, optsRef.current.random);
        retryTimer.current = setTimeout(() => {
          retryTimer.current = null;
          if (current.current === id) loadRef.current(id);
        }, delay);
      },
    );
  }, []);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    current.current = incidentId;
    attempt.current = 0;
    dispatch({ type: 'select', incidentId });
    if (incidentId) load(incidentId);
    return () => {
      ctrl.current?.abort();
      ctrl.current = null;
      clearTimer(retryTimer);
      clearTimer(debounceTimer);
    };
  }, [incidentId, load]);

  const handleEvent = useCallback(
    (event: OverlayEventLike) => {
      const id = current.current;
      if (!id || (event.incident_id && event.incident_id !== id) || !isRefetchEvent(event.type)) return;
      dispatch({ type: 'event', event });
      clearTimer(debounceTimer);
      debounceTimer.current = setTimeout(() => {
        debounceTimer.current = null;
        if (current.current === id) load(id);
      }, optsRef.current.debounceMs ?? OVERLAY_REFETCH_DEBOUNCE_MS);
    },
    [load],
  );

  const refresh = useCallback(() => {
    if (current.current) load(current.current);
  }, [load]);

  const merged = useMemo(() => selectMerged(state, incidentId), [state, incidentId]);
  const inputs = useMemo(() => overlayLayerInputs(merged), [merged]);
  const own = state.incidentId === incidentId && incidentId !== null;
  return {
    merged,
    inputs,
    pendingEdgeIds: merged.pendingEdgeIds,
    snapshot: own ? state.snapshot : null,
    status: own ? state.status : incidentId ? 'loading' : 'idle',
    handleEvent,
    refresh,
  };
}
