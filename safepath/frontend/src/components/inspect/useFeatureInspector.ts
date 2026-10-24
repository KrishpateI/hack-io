import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchEdgesNear, type EdgeDetail } from './edgeService';
import { buildCandidates, edgeCandidate, type Candidate, type MapClick } from './inspectModel';

export interface InspectorState {
  /** Everything found at the last click or coordinate, best first. */
  candidates: Candidate[];
  selectedId: string | null;
  selected: Candidate | null;
  /** True while nearby roads are being fetched. */
  loading: boolean;
  /** Set when the nearby-roads request failed (the rest of the result is still shown). */
  error: string | null;
  /** Polite live-region text announcing what was found. */
  announcement: string;
  open: boolean;
}

export interface UseFeatureInspector extends InspectorState {
  onMapClick: (click: MapClick) => void;
  /** Keyboard alternative to clicking: inspect a location by coordinates. */
  inspectCoordinate: (lat: number, lon: number) => void;
  select: (id: string) => void;
  close: () => void;
}

export interface InspectorOptions {
  incidentId?: string | null;
  /** Search radius for roads, meters. */
  radius?: number;
  /** Injectable for tests. */
  fetchEdges?: typeof fetchEdgesNear;
}

const EMPTY: InspectorState = {
  candidates: [], selectedId: null, selected: null, loading: false, error: null, announcement: '', open: false,
};

function describe(cs: Candidate[]): string {
  if (!cs.length) return 'No map features found at this location.';
  return `${cs.length} feature${cs.length === 1 ? '' : 's'} found. Showing ${cs[0].title}.`;
}

/**
 * Click handling state: features under the pointer appear immediately (synchronously from the
 * rendered map); nearby road segments are fetched from the graph edge endpoint and appended.
 */
export function useFeatureInspector(opts: InspectorOptions = {}): UseFeatureInspector {
  const { incidentId, radius = 25, fetchEdges = fetchEdgesNear } = opts;
  const [state, setState] = useState<InspectorState>(EMPTY);
  const ticket = useRef(0);
  const abort = useRef<AbortController | null>(null);

  const finish = useCallback((candidates: Candidate[], loading: boolean, error: string | null): InspectorState => {
    const selected = candidates[0] ?? null;
    return {
      candidates, selected, selectedId: selected?.id ?? null, loading, error, open: true,
      announcement: loading ? 'Looking up nearby roads…' : describe(candidates),
    };
  }, []);

  const lookup = useCallback(
    (lat: number, lon: number, base: Candidate[]) => {
      const mine = ++ticket.current;
      abort.current?.abort();
      const ctl = (abort.current = new AbortController());
      setState(finish(base, true, null));
      fetchEdges(lat, lon, { incidentId, radius, limit: 5, signal: ctl.signal })
        .then((edges: EdgeDetail[]) => {
          if (mine !== ticket.current) return;
          const roads = edges.map(edgeCandidate);
          setState((prev) => {
            const all = [...base, ...roads];
            // keep the user's choice if they already picked something
            const keep = all.find((c) => c.id === prev.selectedId);
            const next = finish(all, false, null);
            return keep ? { ...next, selected: keep, selectedId: keep.id } : next;
          });
        })
        .catch((err: unknown) => {
          if (mine !== ticket.current || (err as { name?: string })?.name === 'AbortError') return;
          setState(finish(base, false, 'Nearby road details could not be loaded.'));
        });
    },
    [fetchEdges, finish, incidentId, radius],
  );

  const onMapClick = useCallback(
    (click: MapClick) => lookup(click.lngLat[1], click.lngLat[0], buildCandidates(click)),
    [lookup],
  );
  const inspectCoordinate = useCallback((lat: number, lon: number) => lookup(lat, lon, []), [lookup]);

  const select = useCallback((id: string) => {
    setState((s) => {
      const c = s.candidates.find((x) => x.id === id);
      return c ? { ...s, selected: c, selectedId: id, announcement: `Showing ${c.title}.` } : s;
    });
  }, []);

  const close = useCallback(() => {
    ticket.current++;
    abort.current?.abort();
    setState({ ...EMPTY, announcement: 'Feature details closed.' });
  }, []);

  useEffect(() => () => abort.current?.abort(), []);

  return { ...state, onMapClick, inspectCoordinate, select, close };
}
