import { backoffDelay, POLL_MIN_INTERVAL_MS, STALE_WARNING_DELAY_MS } from './backoff';
import { applyEvent, applyEvents, initialEventState, type EventStoreState, type RealtimeEvent } from './eventStore';
import type { RealtimeClient } from './realtimeClients';

export type ConnectionStatus = 'connected' | 'reconnecting' | 'offline';

export interface RealtimeSnapshot {
  status: ConnectionStatus;
  /** ms epoch of the last received update (event, catch-up, or successful connect). */
  lastSyncedAt: number | null;
  /** True once disconnected longer than the stale delay: road conditions must not be shown as current. */
  stale: boolean;
  store: EventStoreState;
}

export interface ControllerDeps {
  client: RealtimeClient;
  fetchSince: (incidentId: string, since: string | null) => Promise<RealtimeEvent[]>;
  now?: () => number;
  random?: () => number;
  isOnline?: () => boolean;
  pollIntervalMs?: number;
  staleDelayMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (id: unknown) => void;
}

/** Framework-agnostic realtime manager: reconnect, catch-up, dedupe, polling, stale tracking. */
export class RealtimeController {
  private snap: RealtimeSnapshot = { status: 'reconnecting', lastSyncedAt: null, stale: false, store: initialEventState };
  private listeners = new Set<() => void>();
  private eventListeners = new Set<(e: RealtimeEvent) => void>();
  private attempt = 0;
  private running = false;
  private incidentId = '';
  private reconnectTimer: unknown = null;
  private pollTimer: unknown = null;
  private staleTimer: unknown = null;
  private readonly d: Required<Omit<ControllerDeps, 'client' | 'fetchSince'>> & Pick<ControllerDeps, 'client' | 'fetchSince'>;

  constructor(deps: ControllerDeps) {
    this.d = {
      now: () => Date.now(),
      random: Math.random,
      isOnline: () => (typeof navigator === 'undefined' ? true : navigator.onLine),
      pollIntervalMs: POLL_MIN_INTERVAL_MS,
      staleDelayMs: STALE_WARNING_DELAY_MS,
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
      ...deps,
    };
    // Polling must never be more frequent than every 30 s.
    this.d.pollIntervalMs = Math.max(POLL_MIN_INTERVAL_MS, this.d.pollIntervalMs);
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = (): RealtimeSnapshot => this.snap;

  /** Raw, de-duplicated events as they are first applied (live or catch-up), oldest first within a batch. */
  subscribeEvents = (fn: (e: RealtimeEvent) => void): (() => void) => {
    this.eventListeners.add(fn);
    return () => this.eventListeners.delete(fn);
  };

  private set(patch: Partial<RealtimeSnapshot>): void {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }

  start(incidentId: string): void {
    this.stop();
    this.running = true;
    // A different incident must not inherit the previous incident's entities or sync time.
    if (this.incidentId && this.incidentId !== incidentId) {
      this.snap = { ...this.snap, store: initialEventState, lastSyncedAt: null, stale: false };
    }
    this.incidentId = incidentId;
    this.attempt = 0;
    this.markDisconnected();
    this.open();
  }

  stop(): void {
    this.running = false;
    this.d.client.close();
    this.clearAll();
  }

  /** Browser online/offline hint. */
  setOnline(online: boolean): void {
    if (!this.running) return;
    if (online && this.snap.status !== 'connected') {
      this.clearTimer('reconnect');
      this.open();
    } else if (!online) {
      this.markDisconnected();
    }
  }

  private clearTimer(which: 'reconnect' | 'poll' | 'stale'): void {
    const key = (which + 'Timer') as 'reconnectTimer' | 'pollTimer' | 'staleTimer';
    if (this[key] !== null) this.d.clearTimer(this[key]);
    this[key] = null;
  }
  private clearAll(): void {
    this.clearTimer('reconnect');
    this.clearTimer('poll');
    this.clearTimer('stale');
  }

  private open(): void {
    if (!this.running) return;
    this.d.client.connect(this.incidentId, {
      onOpen: () => void this.handleOpen(),
      onEvent: (e) => this.handleEvent(e),
      onClose: () => this.handleClose(),
    });
  }

  private async handleOpen(): Promise<void> {
    if (!this.running) return;
    this.attempt = 0;
    this.clearTimer('reconnect');
    // Catch up before declaring the channel current (Req 25.6).
    const ok = await this.catchUp();
    if (!this.running) return;
    if (!ok) {
      this.d.client.close();
      this.handleClose();
      return;
    }
    this.clearTimer('poll');
    this.clearTimer('stale');
    this.set({ status: 'connected', stale: false, lastSyncedAt: this.d.now() });
  }

  private handleEvent(e: RealtimeEvent): void {
    if (!this.running) return;
    const fresh = !this.snap.store.appliedEventIds[e.event_id];
    this.set({ store: applyEvent(this.snap.store, e), lastSyncedAt: this.d.now() });
    if (fresh) this.eventListeners.forEach((l) => l(e));
  }

  private handleClose(): void {
    if (!this.running) return;
    this.markDisconnected();
    this.scheduleReconnect();
  }

  private markDisconnected(): void {
    this.set({ status: this.d.isOnline() ? 'reconnecting' : 'offline' });
    if (this.staleTimer === null && !this.snap.stale) {
      this.staleTimer = this.d.setTimer(() => {
        this.staleTimer = null;
        if (this.running && this.snap.status !== 'connected') this.set({ stale: true });
      }, this.d.staleDelayMs);
    }
    if (this.pollTimer === null) this.schedulePoll();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    const delay = backoffDelay(this.attempt++, this.d.random);
    this.reconnectTimer = this.d.setTimer(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  private schedulePoll(): void {
    this.pollTimer = this.d.setTimer(async () => {
      this.pollTimer = null;
      if (!this.running || this.snap.status === 'connected') return;
      await this.catchUp();
      if (this.running && this.getSnapshot().status !== 'connected') this.schedulePoll();
    }, this.d.pollIntervalMs);
  }

  /** Fetch events after the last seen one and apply each once. */
  private async catchUp(): Promise<boolean> {
    try {
      const events = await this.d.fetchSince(this.incidentId, this.snap.store.lastEventId);
      if (!this.running) return false;
      const seen = this.snap.store.appliedEventIds;
      const fresh = events
        .filter((e) => !seen[e.event_id])
        .sort((a, b) => (a.event_id < b.event_id ? -1 : a.event_id > b.event_id ? 1 : 0));
      this.set({ store: applyEvents(this.snap.store, events), lastSyncedAt: this.d.now() });
      fresh.forEach((e) => this.eventListeners.forEach((l) => l(e)));
      return true;
    } catch {
      return false;
    }
  }
}
