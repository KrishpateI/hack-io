/**
 * Opt-in offline basemap. The service worker downloads the whole PMTiles file once (the file is
 * a few MB for the Chicago extract, zoom 12 to 15) and then answers Range requests from it.
 * Nothing is prefetched without the user pressing the button (tile policy, design D8).
 */
export type MapCacheState = 'unknown' | 'none' | 'downloading' | 'cached' | 'error' | 'unsupported';

export interface MapCacheSnapshot {
  state: MapCacheState;
  message: string | null;
}

interface SwLike {
  controller: { postMessage: (m: unknown) => void } | null;
  ready: Promise<{ active: { postMessage: (m: unknown) => void } | null }>;
  addEventListener: (t: 'message', fn: (e: MessageEvent) => void) => void;
  removeEventListener: (t: 'message', fn: (e: MessageEvent) => void) => void;
}

export class MapCache {
  private snap: MapCacheSnapshot;
  private listeners = new Set<() => void>();
  constructor(
    readonly url: string | undefined,
    private readonly sw: SwLike | null = typeof navigator !== 'undefined' && 'serviceWorker' in navigator
      ? (navigator.serviceWorker as unknown as SwLike)
      : null,
  ) {
    this.snap = { state: url && sw ? 'unknown' : 'unsupported', message: null };
  }
  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = () => this.snap;
  private set(state: MapCacheState, message: string | null = null) {
    this.snap = { state, message };
    for (const l of this.listeners) l();
  }
  private onMessage = (e: MessageEvent) => {
    const d = e.data as { type?: string; state?: string; message?: string; url?: string } | null;
    if (d?.type !== 'PMTILES_STATUS') return;
    if (d.state === 'cached' || d.state === 'none' || d.state === 'downloading') this.set(d.state);
    else if (d.state === 'error') this.set('error', d.message ?? 'Download failed');
  };
  private async post(msg: unknown) {
    const reg = await this.sw?.ready;
    (this.sw?.controller ?? reg?.active)?.postMessage(msg);
  }
  /** Listen for status and ask the service worker whether the file is already stored. */
  start(): () => void {
    if (!this.url || !this.sw) return () => undefined;
    this.sw.addEventListener('message', this.onMessage);
    void this.post({ type: 'PMTILES_STATUS_QUERY', url: this.url }).catch(() => undefined);
    return () => this.sw?.removeEventListener('message', this.onMessage);
  }
  download(): void {
    if (!this.url || !this.sw) return;
    this.set('downloading');
    void this.post({ type: 'CACHE_PMTILES', url: this.url }).catch((e: unknown) => this.set('error', String(e)));
  }
}

export function pmtilesUrlFromEnv(): string | undefined {
  const v = (import.meta.env as Record<string, string | undefined>).VITE_PMTILES_URL;
  return v ? v : undefined;
}
