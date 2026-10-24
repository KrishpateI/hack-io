import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { fetchEdge } from '../components/inspect/edgeService';
import type { RealtimeEvent } from '../services/eventStore';
import { applyRestrictionEvent, type ActiveRestriction } from '../services/mapData';

interface State {
  incidentId: string | null;
  map: Record<string, ActiveRestriction>;
}
type Action = { incidentId: string | null; event: RealtimeEvent };

const EMPTY: Record<string, ActiveRestriction> = {};

function reducer(state: State, a: Action): State {
  if (a.event.incident_id && a.event.incident_id !== a.incidentId) return state;
  const base = state.incidentId === a.incidentId ? state.map : EMPTY;
  const map = applyRestrictionEvent(base, a.event);
  return map === state.map && state.incidentId === a.incidentId ? state : { incidentId: a.incidentId, map };
}

/** Restrictions currently in force for the incident, built from raw restriction_added/removed events. */
export function useActiveRestrictions(incidentId: string | null) {
  const [state, dispatch] = useReducer(reducer, { incidentId: null, map: EMPTY });
  const onEvent = useCallback((event: RealtimeEvent) => dispatch({ incidentId, event }), [incidentId]);
  return { restrictions: state.incidentId === incidentId ? state.map : EMPTY, onEvent };
}

const MAX_EDGES = 40;

/** Line geometry of road edges (GET /api/graph/edges/{id}), fetched once per edge. */
export function useEdgeCoordinates(edgeIds: readonly string[], incidentId: string | null): Record<string, number[][]> {
  const [coords, setCoords] = useState<Record<string, number[][]>>({});
  const requested = useRef(new Set<string>());
  const key = edgeIds.join(',');
  useEffect(() => {
    for (const id of key.split(',').filter(Boolean).slice(0, MAX_EDGES)) {
      if (requested.current.has(id)) continue;
      requested.current.add(id);
      fetchEdge(id, { incidentId }).then(
        (edge) => {
          const line = edge.geometry_geojson?.coordinates;
          if (Array.isArray(line) && line.length >= 2) setCoords((prev) => ({ ...prev, [id]: line }));
        },
        () => requested.current.delete(id),
      );
    }
  }, [key, incidentId]);
  return coords;
}
