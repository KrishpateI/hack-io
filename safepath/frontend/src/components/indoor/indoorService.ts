import { useEffect, useState } from 'react';
import { apiBaseUrl } from '../../services/api';
import type { IndoorGeoJson } from './indoorModel';

/** GET /api/indoor/building (public, read-only, SIMULATED). Plain fetch: no generated types yet. */
export async function fetchIndoorBuilding(signal?: AbortSignal): Promise<IndoorGeoJson> {
  const res = await fetch(`${apiBaseUrl()}/api/indoor/building`, { headers: { Accept: 'application/json' }, signal });
  if (!res.ok) throw new Error(`Indoor building data unavailable (HTTP ${res.status})`);
  return (await res.json()) as IndoorGeoJson;
}

export interface BuildingLoad {
  data: IndoorGeoJson | null;
  error: string | null;
  loading: boolean;
}

/** Load the building once; `enabled=false` skips the request (caller supplies data). */
export function useIndoorBuilding(load: typeof fetchIndoorBuilding, enabled: boolean, attempt: number): BuildingLoad {
  const [state, setState] = useState<BuildingLoad>({ data: null, error: null, loading: enabled });
  useEffect(() => {
    if (!enabled) return;
    const ctl = new AbortController();
    load(ctl.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((e: unknown) => {
        if (ctl.signal.aborted) return;
        setState({ data: null, error: e instanceof Error ? e.message : 'Indoor building data unavailable', loading: false });
      });
    return () => ctl.abort();
  }, [load, enabled, attempt]);
  return state;
}
