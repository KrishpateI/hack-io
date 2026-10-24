/**
 * Offline_Manager (Req 41, design D8): online/offline detection, a bounded pending queue for
 * hazard reports, messages and check-ins, and the flush-on-reconnect logic.
 *
 * Queue logic is pure and injectable (storage, clock, submit) so it can be property tested:
 * `enqueue`, `dequeue`, `editItem`, `markSending/Sent/Rejected/Retry` are reducers over
 * `QueueState`, and `flush` drives them against an injected `submit`.
 *
 * Honesty rules: an item is only "Sent" after a 2xx. A queued item has not reached other users
 * or emergency services, and the wording below says so.
 */
import { ApiError, apiBaseUrl, bootstrapGuestSession, createReport, postCheckin, postMessage, tokenStore, type JsonObject } from './api';
import { backoffDelay } from './backoff';
import { idbStore, type KeyValueStore } from './idb';

export const QUEUE_MAX = 50;
/** Sent items stay visible (so "Sent" can be shown) but never count toward the cap. */
export const SENT_HISTORY_MAX = 10;

export type QueueKind = 'report' | 'message' | 'checkin';
export type ItemStatus = 'queued' | 'sending' | 'sent' | 'rejected';

export interface QueueItem {
  /** Client-generated idempotency key: `client_report_id` for reports, `client_request_id` otherwise. */
  key: string;
  kind: QueueKind;
  body: JsonObject;
  createdAt: number;
  /** Creation order; flush submits in ascending `seq`. */
  seq: number;
  status: ItemStatus;
  error: string | null;
  attempts: number;
  sentAt: number | null;
}

export interface QueueState {
  items: readonly QueueItem[];
  nextSeq: number;
}

export const emptyQueue = (): QueueState => ({ items: [], nextSeq: 1 });
/** Items still on this device (everything except already-sent history). */
export const pendingCount = (s: QueueState): number => s.items.filter((i) => i.status !== 'sent').length;

// ------------------------------------------------------------------ user-facing wording
export const QUEUE_FULL_MESSAGE =
  'The offline queue is full (50 items). Reconnect so queued items can be sent, or discard one before adding more.';
export const QUEUED_LABEL = 'Queued on this device \u2014 not sent';
export const QUEUED_DETAIL = 'Not yet received by the server. Other users and emergency services have not seen it.';

export function statusText(item: Pick<QueueItem, 'kind' | 'status' | 'error'>): string {
  switch (item.status) {
    case 'queued':
      return QUEUED_LABEL;
    case 'sending':
      return 'Sending\u2026';
    case 'sent':
      return item.kind === 'checkin' ? 'Sent \u2014 received by the server' : 'Sent \u2014 visible to other users';
    case 'rejected':
      return `Rejected by server: ${item.error ?? 'no reason given'}`;
  }
}

export const KIND_LABEL: Record<QueueKind, string> = { report: 'Hazard report', message: 'Message', checkin: 'Check-in' };

/** Marker returned to forms in place of a server response when an item was queued instead. */
export interface QueuedResult {
  queued: true;
  key: string;
}
export const isQueuedResult = (v: unknown): v is QueuedResult =>
  typeof v === 'object' && v !== null && (v as { queued?: unknown }).queued === true;

// ------------------------------------------------------------------ pure reducers
export type EnqueueResult =
  | { ok: true; state: QueueState; item: QueueItem; duplicate: boolean }
  | { ok: false; state: QueueState; reason: 'full'; message: string };

const KEY_FIELD: Record<QueueKind, string> = { report: 'client_report_id', message: 'client_request_id', checkin: 'client_request_id' };

function newKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Add an item. Re-enqueueing a body that carries an existing key is a no-op (idempotent).
 * Refused when 50 items are already waiting.
 */
export function enqueue(state: QueueState, kind: QueueKind, body: JsonObject, now: number, makeKey: () => string = newKey): EnqueueResult {
  const field = KEY_FIELD[kind];
  const given = body[field];
  const key = typeof given === 'string' && given ? given : makeKey();
  const existing = state.items.find((i) => i.key === key);
  if (existing) return { ok: true, state, item: existing, duplicate: true };
  if (pendingCount(state) >= QUEUE_MAX) return { ok: false, state, reason: 'full', message: QUEUE_FULL_MESSAGE };
  const item: QueueItem = {
    key, kind, body: { ...body, [field]: key }, createdAt: now, seq: state.nextSeq,
    status: 'queued', error: null, attempts: 0, sentAt: null,
  };
  return { ok: true, state: { items: [...state.items, item], nextSeq: state.nextSeq + 1 }, item, duplicate: false };
}

const mapItem = (s: QueueState, key: string, fn: (i: QueueItem) => QueueItem): QueueState =>
  s.items.some((i) => i.key === key) ? { ...s, items: s.items.map((i) => (i.key === key ? fn(i) : i)) } : s;

/** Remove an item (discard). */
export const dequeue = (s: QueueState, key: string): QueueState =>
  s.items.some((i) => i.key === key) ? { ...s, items: s.items.filter((i) => i.key !== key) } : s;

export const markSending = (s: QueueState, key: string) => mapItem(s, key, (i) => ({ ...i, status: 'sending', attempts: i.attempts + 1 }));
export const markRetry = (s: QueueState, key: string) => mapItem(s, key, (i) => ({ ...i, status: 'queued' }));
export const markRejected = (s: QueueState, key: string, error: string) =>
  mapItem(s, key, (i) => ({ ...i, status: 'rejected', error }));

export function markSent(s: QueueState, key: string, now: number): QueueState {
  const next = mapItem(s, key, (i) => ({ ...i, status: 'sent', error: null, sentAt: now }));
  const sent = next.items.filter((i) => i.status === 'sent');
  if (sent.length <= SENT_HISTORY_MAX) return next;
  const drop = new Set(sent.slice(0, sent.length - SENT_HISTORY_MAX).map((i) => i.key));
  return { ...next, items: next.items.filter((i) => !drop.has(i.key)) };
}

/** The user-editable text field of an item, if it has one. */
export const textFieldOf = (kind: QueueKind): 'description' | 'content' | null =>
  kind === 'report' ? 'description' : kind === 'message' ? 'content' : null;

/** Edit a rejected (or still queued) item's text and queue it again. Keeps its key and position. */
export function editItem(s: QueueState, key: string, text: string): QueueState {
  return mapItem(s, key, (i) => {
    const f = textFieldOf(i.kind);
    if (!f || (i.status !== 'rejected' && i.status !== 'queued')) return i;
    return { ...i, body: { ...i.body, [f]: text }, status: 'queued', error: null };
  });
}

/** Revive state read from storage: an interrupted "sending" goes back to "queued". */
export function normalizeLoaded(raw: unknown): QueueState {
  const r = raw as Partial<QueueState> | null | undefined;
  if (!r || !Array.isArray(r.items)) return emptyQueue();
  const items = (r.items as QueueItem[])
    .filter((i) => i && typeof i.key === 'string' && typeof i.seq === 'number')
    .map((i) => (i.status === 'sending' ? { ...i, status: 'queued' as const } : i));
  const maxSeq = items.reduce((m, i) => Math.max(m, i.seq), 0);
  return { items, nextSeq: Math.max(typeof r.nextSeq === 'number' ? r.nextSeq : 1, maxSeq + 1) };
}

/** Merge stored state with items enqueued before the load finished (stored items stay first). */
export function mergeQueues(stored: QueueState, current: QueueState): QueueState {
  const known = new Set(stored.items.map((i) => i.key));
  let seq = stored.nextSeq;
  const extra = current.items.filter((i) => !known.has(i.key)).map((i) => ({ ...i, seq: seq++ }));
  return { items: [...stored.items, ...extra], nextSeq: seq };
}

// ------------------------------------------------------------------ flush
export type SubmitFn = (item: QueueItem) => Promise<unknown>;
export type FlushOutcome = 'empty' | 'done' | 'retry' | 'auth';

export interface FlushDeps {
  submit: SubmitFn;
  /** Re-bootstrap the guest session. Called at most once per flush, after a 401. */
  reauth?: () => Promise<unknown>;
  now?: () => number;
  onChange?: (s: QueueState) => void;
}

export interface FlushResult {
  state: QueueState;
  outcome: FlushOutcome;
  /** True when the stop was caused by a network-level failure (no HTTP response). */
  networkFailure: boolean;
}

/** Why the server rejected an item, using the API error envelope (message plus field problems). */
export function rejectionReason(e: ApiError): string {
  const fields = e.fields.map((f) => `${f.field}: ${f.problem}`).join('; ');
  return fields ? `${e.message} (${fields})` : e.message || `HTTP ${e.status}`;
}

type Verdict = { kind: 'reject'; reason: string } | { kind: 'auth' } | { kind: 'retry'; network: boolean };

export function classifyFailure(e: unknown): Verdict {
  if (e instanceof ApiError) {
    if (e.status === 401) return { kind: 'auth' };
    if (e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) return { kind: 'reject', reason: rejectionReason(e) };
    return { kind: 'retry', network: false }; // 5xx, 408, 429
  }
  return { kind: 'retry', network: true };
}

/**
 * Submit queued items in creation order, one at a time.
 *  - 2xx: item becomes "sent".
 *  - 401: re-bootstrap the guest session once and retry the same item; a second 401 stops (keeps item).
 *  - other 4xx (validation etc.): item becomes "rejected" with the server's reason; flush continues.
 *  - 5xx / 408 / 429 / network failure: item stays queued and flush stops, so order is preserved.
 */
export async function flush(initial: QueueState, deps: FlushDeps): Promise<FlushResult> {
  const now = deps.now ?? Date.now;
  let state = initial;
  let reauthed = false;
  let sentAny = false;
  const set = (s: QueueState) => {
    state = s;
    deps.onChange?.(s);
  };
  for (;;) {
    const next = [...state.items].filter((i) => i.status === 'queued').sort((a, b) => a.seq - b.seq)[0];
    if (!next) return { state, outcome: sentAny ? 'done' : 'empty', networkFailure: false };
    set(markSending(state, next.key));
    try {
      await deps.submit(state.items.find((i) => i.key === next.key) ?? next);
      set(markSent(state, next.key, now()));
      sentAny = true;
    } catch (e) {
      const v = classifyFailure(e);
      if (v.kind === 'reject') {
        set(markRejected(state, next.key, v.reason));
      } else if (v.kind === 'auth') {
        set(markRetry(state, next.key));
        if (reauthed || !deps.reauth) return { state, outcome: 'auth', networkFailure: false };
        reauthed = true;
        try {
          await deps.reauth();
        } catch {
          return { state, outcome: 'retry', networkFailure: true };
        }
      } else {
        set(markRetry(state, next.key));
        return { state, outcome: 'retry', networkFailure: v.network };
      }
    }
  }
}

/** Default submit: route each item to its API endpoint. */
export const submitItem: SubmitFn = (item) => {
  switch (item.kind) {
    case 'report':
      return createReport(item.body);
    case 'message':
      return postMessage(item.body);
    case 'checkin':
      return postCheckin(item.body);
  }
};

// ------------------------------------------------------------------ manager
export interface OfflineSnapshot {
  /** True when the device is offline or recent requests failed at the network level. */
  offline: boolean;
  /** Epoch ms of the last successful sync with the server, or null if never. */
  lastSyncAt: number | null;
  queue: QueueState;
  pending: number;
  flushing: boolean;
  /** Oldest sync time among map layers currently shown from a cache, or null when none are. */
  cachedLayersAt: number | null;
  /** Some layer is currently shown from a cache (its timestamp may be unknown). */
  layersCached: boolean;
}

export interface OfflineDeps {
  storage?: KeyValueStore;
  now?: () => number;
  submit?: SubmitFn;
  reauth?: () => Promise<unknown>;
  navigatorOnline?: () => boolean;
  /** Resolves true when the API is reachable. */
  probe?: () => Promise<boolean>;
  /** Schedule `fn` after `ms`; returns a cancel function. */
  schedule?: (fn: () => void, ms: number) => () => void;
  random?: () => number;
}

const QUEUE_KEY = 'queue';
const SYNC_KEY = 'lastSync';

export class OfflineManager {
  private readonly storage: KeyValueStore;
  private readonly now: () => number;
  private readonly submit: SubmitFn;
  private readonly reauth: () => Promise<unknown>;
  private readonly navOnline: () => boolean;
  private readonly probe: () => Promise<boolean>;
  private readonly schedule: (fn: () => void, ms: number) => () => void;
  private readonly random: () => number;

  private queue: QueueState = emptyQueue();
  private lastSyncAt: number | null = null;
  private navigatorOnline: boolean;
  private networkFailed = false;
  private realtimeOffline = false;
  private flushPromise: Promise<FlushOutcome> | null = null;
  private attempt = 0;
  private cancelRetry: (() => void) | null = null;
  private writes: Promise<unknown> = Promise.resolve();
  private readonly cachedLayers = new Map<string, number | null>();
  private listeners = new Set<() => void>();
  private snapshot: OfflineSnapshot;
  readonly ready: Promise<void>;

  constructor(deps: OfflineDeps = {}) {
    this.storage = deps.storage ?? idbStore('safepath-offline', 'kv');
    this.now = deps.now ?? Date.now;
    this.submit = deps.submit ?? submitItem;
    this.reauth =
      deps.reauth ??
      (() => {
        tokenStore.set(null);
        return bootstrapGuestSession();
      });
    this.navOnline = deps.navigatorOnline ?? (() => (typeof navigator === 'undefined' ? true : navigator.onLine !== false));
    this.probe =
      deps.probe ??
      (async () => {
        try {
          const res = await fetch(`${apiBaseUrl()}/api/health`, { cache: 'no-store' });
          return res.ok;
        } catch {
          return false;
        }
      });
    this.schedule =
      deps.schedule ??
      ((fn, ms) => {
        const t = setTimeout(fn, ms);
        return () => clearTimeout(t);
      });
    this.random = deps.random ?? Math.random;
    this.navigatorOnline = this.navOnline();
    this.snapshot = this.build();
    this.ready = this.load();
  }

  // ---- store plumbing
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };
  getSnapshot = (): OfflineSnapshot => this.snapshot;

  private build(): OfflineSnapshot {
    const times = [...this.cachedLayers.values()].filter((t): t is number => t !== null);
    return {
      offline: !this.navigatorOnline || this.networkFailed || this.realtimeOffline,
      lastSyncAt: this.lastSyncAt,
      queue: this.queue,
      pending: pendingCount(this.queue),
      flushing: this.flushPromise !== null,
      cachedLayersAt: times.length ? Math.min(...times) : null,
      layersCached: this.cachedLayers.size > 0,
    };
  }
  private emit(): void {
    this.snapshot = this.build();
    for (const l of this.listeners) l();
  }
  private persistQueue(): void {
    const snap = this.queue;
    this.writes = this.writes.then(() => this.storage.set(QUEUE_KEY, snap)).catch(() => undefined);
  }
  private setQueue(q: QueueState): void {
    this.queue = q;
    this.persistQueue();
    this.emit();
  }
  private async load(): Promise<void> {
    try {
      const [q, t] = await Promise.all([this.storage.get<QueueState>(QUEUE_KEY), this.storage.get<number>(SYNC_KEY)]);
      this.queue = mergeQueues(normalizeLoaded(q), this.queue);
      if (typeof t === 'number' && (this.lastSyncAt === null || t > this.lastSyncAt)) this.lastSyncAt = t;
    } catch {
      /* storage unavailable: the in-memory queue still works for this session */
    }
    this.emit();
  }

  isOffline(): boolean {
    return this.snapshot.offline;
  }

  // ---- connectivity
  setNavigatorOnline(online: boolean): void {
    this.navigatorOnline = online;
    if (online) {
      this.networkFailed = false;
      this.attempt = 0;
    }
    this.emit();
    if (online) void this.flush();
  }
  setRealtimeStatus(status: string): void {
    const off = status === 'offline';
    if (off === this.realtimeOffline) return;
    this.realtimeOffline = off;
    this.emit();
  }
  /** A request failed at the network level although the browser reports online. */
  noteNetworkFailure(): void {
    if (this.networkFailed) return;
    this.networkFailed = true;
    this.emit();
    this.scheduleRecovery();
  }
  /** Any successful round trip to the server: we are online and in sync. */
  noteSuccess(): void {
    const wasFailed = this.networkFailed;
    this.networkFailed = false;
    this.attempt = 0;
    this.noteSync(this.now());
    if (wasFailed) this.emit();
  }
  noteSync(ms: number | null): void {
    if (ms === null || (this.lastSyncAt !== null && ms <= this.lastSyncAt)) return;
    this.lastSyncAt = ms;
    this.writes = this.writes.then(() => this.storage.set(SYNC_KEY, ms)).catch(() => undefined);
    this.emit();
  }

  // ---- layers shown from a cache
  noteLayer(key: string, cachedAt: number | null, cached: boolean): void {
    if (cached) {
      this.cachedLayers.set(key, cachedAt);
      this.emit();
      return;
    }
    const was = this.cachedLayers.delete(key);
    this.noteSync(this.now()); // a live layer response is a successful sync
    if (was) this.emit();
  }
  isLayerCached(key: string): boolean {
    return this.cachedLayers.has(key);
  }

  // ---- queue operations
  queueItem(kind: QueueKind, body: JsonObject): EnqueueResult {
    const r = enqueue(this.queue, kind, body, this.now());
    if (r.ok && !r.duplicate) {
      this.setQueue(r.state);
      if (!this.snapshot.offline) void this.flush();
    }
    return r;
  }
  discard(key: string): void {
    this.setQueue(dequeue(this.queue, key));
  }
  edit(key: string, text: string): void {
    this.setQueue(editItem(this.queue, key, text));
    if (!this.snapshot.offline) void this.flush();
  }

  /** Send everything queued, in order. Concurrent calls share one run. */
  flush(): Promise<FlushOutcome> {
    if (this.flushPromise) return this.flushPromise;
    if (this.snapshot.offline && !this.networkFailed) return Promise.resolve('empty');
    if (!this.queue.items.some((i) => i.status === 'queued')) return Promise.resolve('empty');
    const run = flush(this.queue, {
      submit: this.submit,
      reauth: this.reauth,
      now: this.now,
      onChange: (s) => this.setQueue(s),
    }).then((r) => {
      this.queue = r.state;
      this.flushPromise = null;
      if (r.outcome === 'done') this.noteSuccess();
      else if (r.outcome === 'retry') {
        if (r.networkFailure) this.networkFailed = true;
        this.scheduleRecovery();
      }
      this.persistQueue();
      this.emit();
      return r.outcome;
    });
    this.flushPromise = run;
    this.emit();
    return run;
  }

  /** Manual "send now" button. */
  async retryNow(): Promise<FlushOutcome> {
    this.networkFailed = false;
    this.emit();
    return this.flush();
  }

  private scheduleRecovery(): void {
    if (this.cancelRetry) return;
    const wait = backoffDelay(this.attempt++, this.random);
    this.cancelRetry = this.schedule(() => {
      this.cancelRetry = null;
      void this.recover();
    }, wait);
  }
  private async recover(): Promise<void> {
    if (!this.navigatorOnline) return; // the browser's "online" event resumes us
    if (await this.probe()) {
      this.networkFailed = false;
      this.emit();
      await this.flush();
    } else {
      this.scheduleRecovery();
    }
  }

  /** Attach browser listeners. Returns a cleanup function. */
  start(): () => void {
    const on = () => this.setNavigatorOnline(true);
    const off = () => this.setNavigatorOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    this.navigatorOnline = this.navOnline();
    this.emit();
    void this.ready.then(() => this.flush());
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
      this.cancelRetry?.();
      this.cancelRetry = null;
    };
  }
}

let singleton: OfflineManager | null = null;
export function getOfflineManager(): OfflineManager {
  singleton ??= new OfflineManager();
  return singleton;
}
/** Test helper. */
export function setOfflineManagerForTests(m: OfflineManager | null): void {
  singleton = m;
}
