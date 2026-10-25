/**
 * Fetching and caching of artifact layers from GET /api/layers/{name} (public, read-only).
 * Uses its own small fetch so it does not depend on the generated API types.
 * Results are cached in memory (in-flight requests are shared) and mirrored to an optional
 * offline store, which is the hook point for the offline cache (Req 34, 46).
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { apiBaseUrl } from '../../services/api';
import { BACKOFF_BASE_MS, backoffDelay } from '../../services/backoff';
import type { ArtifactLayerName, MapLayers } from './MapLayers';
import type { ArtifactStatus, ArtifactStatusMap, FC } from './types';

/** Internal store shape: every artifact layer always has a status. */
type FullStatusMap = Record<ArtifactLayerName, ArtifactStatus>;

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
/** Drops cached layers, resets every artifact status to idle and cancels pending retries. */
export function clearLayerCache(): void {
  memory.clear();
  resetArtifactState();
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

const ARTIFACT_NAMES = Object.keys(ARTIFACT_TOGGLE) as ArtifactLayerName[];

// ------------------------------------------------------------------ artifact status store

/** Retry timing for failed artifact layers (Req 2.10): 5 s doubling to a 5 min cap, with jitter. */
export const ARTIFACT_RETRY_BASE_MS = 5000;
export const ARTIFACT_RETRY_CAP_MS = 5 * 60 * 1000;

/**
 * Delay before retry number `attempt` (zero-based). Uses the equal-jitter factor from backoff.ts
 * (always in [0.5, 1]), so the delay lies in [base/2, base] and never exceeds the 5 min cap.
 */
export function artifactRetryDelay(attempt: number, random: () => number = Math.random): number {
  const n = Math.max(0, Math.floor(attempt));
  const base = Math.min(ARTIFACT_RETRY_CAP_MS, ARTIFACT_RETRY_BASE_MS * 2 ** Math.min(n, 20));
  const jitter = backoffDelay(0, random) / BACKOFF_BASE_MS;
  return Math.round(base * jitter);
}

function idleStatuses(): FullStatusMap {
  return { buildings: { status: 'idle' }, cta_stations: { status: 'idle' }, simulated_station: { status: 'idle' } };
}

let statuses: FullStatusMap = idleStatuses();
const statusListeners = new Set<() => void>();
const retryTimers = new Map<ArtifactLayerName, ReturnType<typeof setTimeout>>();
const retryAttempts = new Map<ArtifactLayerName, number>();
const loadedData = new Map<ArtifactLayerName, FC>();
/** Bumped by clearLayerCache so late results of an earlier generation are ignored. */
let generation = 0;

function setStatus(name: ArtifactLayerName, next: ArtifactStatus): void {
  statuses = { ...statuses, [name]: next };
  for (const fn of statusListeners) fn();
}

function resetArtifactState(): void {
  generation += 1;
  for (const t of retryTimers.values()) clearTimeout(t);
  retryTimers.clear();
  retryAttempts.clear();
  loadedData.clear();
  statuses = idleStatuses();
  for (const fn of statusListeners) fn();
}

/** Current artifact statuses (immutable snapshot, replaced on every change). */
export function getArtifactStatus(): FullStatusMap & ArtifactStatusMap {
  return statuses;
}

function subscribeStatus(fn: () => void): () => void {
  statusListeners.add(fn);
  return () => {
    statusListeners.delete(fn);
  };
}

/** Per-layer load status (`idle | loading | loaded | failed`, feature count when loaded). */
export function useArtifactStatus(): FullStatusMap & ArtifactStatusMap {
  return useSyncExternalStore(subscribeStatus, getArtifactStatus, getArtifactStatus);
}

/** A mounted useArtifactLayers instance: which layers it shows and where to push data. */
interface Consumer {
  wants: Set<ArtifactLayerName>;
  deliver(name: ArtifactLayerName, data: FC): void;
}
const consumers = new Set<Consumer>();

function wanted(name: ArtifactLayerName): boolean {
  for (const c of consumers) if (c.wants.has(name)) return true;
  return false;
}

function scheduleRetry(name: ArtifactLayerName): void {
  const prev = retryTimers.get(name);
  if (prev !== undefined) clearTimeout(prev);
  const attempt = retryAttempts.get(name) ?? 0;
  retryAttempts.set(name, attempt + 1);
  const timer = setTimeout(() => {
    retryTimers.delete(name);
    // Toggle off: stay failed without a timer; turning it on again retries immediately.
    if (wanted(name)) fetchArtifact(name);
  }, artifactRetryDelay(attempt));
  retryTimers.set(name, timer);
}

/** Fetch one artifact layer, tracking its status. Each layer is independent of the others. */
function fetchArtifact(name: ArtifactLayerName): void {
  if (statuses[name].status === 'loading') return;
  const prev = retryTimers.get(name);
  if (prev !== undefined) clearTimeout(prev);
  retryTimers.delete(name);
  const gen = generation;
  setStatus(name, { status: 'loading' });
  loadLayer(name, DEFAULT_QUERIES[name]).then(
    (data) => {
      if (gen !== generation) return;
      retryAttempts.delete(name);
      loadedData.set(name, data);
      setStatus(name, { status: 'loaded', count: data.features.length });
      for (const c of consumers) c.deliver(name, data);
    },
    () => {
      if (gen !== generation) return;
      setStatus(name, { status: 'failed' });
      scheduleRetry(name);
    },
  );
}

/** Retry every failed layer that is still wanted, skipping the backoff wait. */
function retryFailedNow(): void {
  for (const name of ARTIFACT_NAMES) {
    if (statuses[name].status === 'failed' && wanted(name)) fetchArtifact(name);
  }
}

/**
 * Fetches each artifact layer the first time its toggle is visible and pushes it into the
 * manager. `visible` is the toggle state map from MapView. A failed layer is marked `failed` and
 * retried with backoff, on the browser `online` event and when its toggle is turned on again;
 * the other layers load regardless.
 */
export function useArtifactLayers(manager: MapLayers | null, visible: Record<string, boolean>): void {
  const consumerRef = useRef<Consumer | null>(null);
  const delivered = useRef(new Set<ArtifactLayerName>());
  const prevOn = useRef<Partial<Record<ArtifactLayerName, boolean>>>({});

  useEffect(() => {
    if (!manager) return;
    delivered.current = new Set();
    const consumer: Consumer = {
      wants: new Set(),
      deliver: (name, data) => {
        delivered.current.add(name);
        manager.setArtifactLayer(name, data);
      },
    };
    consumerRef.current = consumer;
    consumers.add(consumer);
    const onOnline = () => retryFailedNow();
    window.addEventListener('online', onOnline);
    return () => {
      window.removeEventListener('online', onOnline);
      consumers.delete(consumer);
      if (consumerRef.current === consumer) consumerRef.current = null;
    };
  }, [manager]);

  useEffect(() => {
    const consumer = consumerRef.current;
    if (!manager || !consumer) return;
    for (const name of ARTIFACT_NAMES) {
      const on = visible[ARTIFACT_TOGGLE[name]] !== false;
      const turnedOn = on && prevOn.current[name] === false;
      prevOn.current[name] = on;
      if (on) consumer.wants.add(name);
      else consumer.wants.delete(name);
      if (!on || delivered.current.has(name)) continue;
      const data = loadedData.get(name);
      if (data) {
        consumer.deliver(name, data);
        continue;
      }
      const st = statuses[name].status;
      if (st === 'idle' || (st === 'failed' && (turnedOn || !retryTimers.has(name)))) fetchArtifact(name);
    }
  }, [manager, visible]);
}
