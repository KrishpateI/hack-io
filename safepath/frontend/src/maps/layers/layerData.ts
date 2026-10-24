/**
 * Fetching and caching of artifact layers from GET /api/layers/{name} (public, read-only).
 * Uses its own small fetch so it does not depend on the generated API types.
 * Results are cached in memory (in-flight requests are shared) and mirrored to an optional
 * offline store, which is the hook point for the offline cache (Req 34, 46).
 */
import { useEffect, useRef } from 'react';
import { apiBaseUrl } from '../../services/api';
import type { ArtifactLayerName, MapLayers } from './MapLayers';
import type { FC } from './types';

export interface OfflineLayerStore {
  get(key: string): Promise<FC | null | undefined> | FC | null | undefined;
  put(key: string, data: FC): Promise<void> | void;
}

let offlineStore: OfflineLayerStore | null = null;
/** Install an offline cache (e.g. IndexedDB); used as a fallback when the network fails. */
export function setOfflineLayerStore(store: OfflineLayerStore | null): void {
  offlineStore = store;
}

const memory = new Map<string, Promise<FC>>();
export function clearLayerCache(): void {
  memory.clear();
}

export interface LayerQuery {
  bbox?: [number, number, number, number];
  simplify?: boolean;
}

export function layerUrl(name: string, query: LayerQuery = {}): string {
  const qs = new URLSearchParams();
  if (query.bbox) qs.set('bbox', query.bbox.join(','));
  if (query.simplify) qs.set('simplify', 'true');
  const s = qs.toString();
  return `${apiBaseUrl()}/api/layers/${name}${s ? `?${s}` : ''}`;
}

/** Buildings are large: always request the simplified variant. */
export const DEFAULT_QUERIES: Record<ArtifactLayerName, LayerQuery> = {
  buildings: { simplify: true },
  cta_stations: {},
  simulated_station: {},
};

/**
 * Called for every successful layer response with its headers, so the Offline_Manager can tell a
 * live answer from one the service worker served out of its cache (`x-sp-source: cache`).
 */
export type LayerFetchObserver = (key: string, headers: Headers) => void;
let fetchObserver: LayerFetchObserver | null = null;
export function setLayerFetchObserver(fn: LayerFetchObserver | null): void {
  fetchObserver = fn;
}

async function fetchLayer(name: string, query: LayerQuery, key: string): Promise<FC> {
  try {
    const res = await fetch(layerUrl(name, query), { headers: { Accept: 'application/geo+json, application/json' } });
    if (!res.ok) throw new Error(`layer ${name}: HTTP ${res.status}`);
    fetchObserver?.(key, res.headers);
    const data = (await res.json()) as FC;
    if (data?.type !== 'FeatureCollection') throw new Error(`layer ${name}: not a FeatureCollection`);
    void Promise.resolve(offlineStore?.put(key, data)).catch(() => undefined);
    return data;
  } catch (err) {
    const cached = offlineStore ? await offlineStore.get(key) : null;
    if (cached) return cached;
    throw err;
  }
}

/** Load a layer with in-memory caching. Failures are not cached so a retry can succeed. */
export function loadLayer(name: string, query: LayerQuery = {}): Promise<FC> {
  const key = layerUrl(name, query);
  let p = memory.get(key);
  if (!p) {
    p = fetchLayer(name, query, key);
    memory.set(key, p);
    p.catch(() => memory.delete(key));
  }
  return p;
}

const ARTIFACT_TOGGLE: Record<ArtifactLayerName, string> = {
  buildings: 'buildings',
  cta_stations: 'transit',
  simulated_station: 'simulated_station',
};

/**
 * Fetches each artifact layer the first time its toggle is visible and pushes it into the
 * manager. `visible` is the toggle state map from MapView.
 */
export function useArtifactLayers(manager: MapLayers | null, visible: Record<string, boolean>): void {
  const loaded = useRef(new Set<string>());
  useEffect(() => {
    if (!manager) return;
    let cancelled = false;
    for (const name of Object.keys(ARTIFACT_TOGGLE) as ArtifactLayerName[]) {
      if (visible[ARTIFACT_TOGGLE[name]] === false || loaded.current.has(name)) continue;
      loaded.current.add(name);
      loadLayer(name, DEFAULT_QUERIES[name])
        .then((data) => {
          if (!cancelled) manager.setArtifactLayer(name, data);
          else loaded.current.delete(name);
        })
        .catch(() => loaded.current.delete(name));
    }
    return () => {
      cancelled = true;
    };
  }, [manager, visible]);
}
