import { describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { memoryStore } from './idb';
import {
  OfflineManager,
  type OfflineDeps,
  QUEUE_FULL_MESSAGE,
  QUEUE_MAX,
  dequeue,
  editItem,
  emptyQueue,
  enqueue,
  flush,
  normalizeLoaded,
  pendingCount,
  statusText,
  type QueueItem,
  type QueueState,
} from './offline';

const body = (n: number) => ({ incident_id: 'i1', category: 'flooding', description: `r${n}` });

function fill(n: number, kind: 'report' | 'message' = 'report'): QueueState {
  let s = emptyQueue();
  for (let i = 0; i < n; i++) {
    const r = enqueue(s, kind, body(i), 1000 + i, () => `k${i}`);
    if (!r.ok) throw new Error('unexpected full');
    s = r.state;
  }
  return s;
}

const fail = (status: number, message = 'nope', fields: { field: string; problem: string }[] = []) =>
  new ApiError(status, 'x', message, fields);

describe('queue reducers', () => {
  it('caps the queue at 50 and refuses more with a queue-full message', () => {
    const s = fill(QUEUE_MAX);
    expect(pendingCount(s)).toBe(50);
    const r = enqueue(s, 'report', body(99), 5, () => 'extra');
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.message).toBe(QUEUE_FULL_MESSAGE);
    expect(r.state).toBe(s);
  });

  it('re-enqueueing the same client id is a no-op (idempotent)', () => {
    const first = enqueue(emptyQueue(), 'report', { ...body(1), client_report_id: 'abc' }, 1);
    if (!first.ok) throw new Error('full');
    const again = enqueue(first.state, 'report', { ...body(1), client_report_id: 'abc' }, 2);
    expect(again.ok && again.duplicate).toBe(true);
    expect(again.state.items).toHaveLength(1);
    expect(first.item.key).toBe('abc');
  });

  it('adds a client key to messages and check-ins without touching other fields', () => {
    const r = enqueue(emptyQueue(), 'message', { incident_id: 'i', message_type: 'update', content: 'hi' }, 1, () => 'm-1');
    if (!r.ok) throw new Error('full');
    expect(r.item.body).toEqual({ incident_id: 'i', message_type: 'update', content: 'hi', client_request_id: 'm-1' });
  });

  it('discarding frees capacity; edit requeues a rejected item with its text changed', () => {
    let s = fill(QUEUE_MAX);
    s = dequeue(s, 'k0');
    expect(enqueue(s, 'report', body(7), 9, () => 'new').ok).toBe(true);
    const rejected: QueueState = { ...s, items: s.items.map((i) => (i.key === 'k1' ? { ...i, status: 'rejected' as const, error: 'bad' } : i)) };
    const edited = editItem(rejected, 'k1', 'fixed').items.find((i) => i.key === 'k1') as QueueItem;
    expect(edited).toMatchObject({ status: 'queued', error: null, body: { description: 'fixed' } });
  });

  it('revives an interrupted "sending" item as queued after a reload', () => {
    const s = fill(1);
    const loaded = normalizeLoaded({ ...s, items: [{ ...s.items[0], status: 'sending' }] });
    expect(loaded.items[0].status).toBe('queued');
  });
});

describe('flush', () => {
  it('submits in creation order and marks each sent after success', async () => {
    const seen: string[] = [];
    const r = await flush(fill(3), { submit: async (i) => void seen.push(i.key) });
    expect(seen).toEqual(['k0', 'k1', 'k2']);
    expect(r.outcome).toBe('done');
    expect(r.state.items.every((i) => i.status === 'sent')).toBe(true);
  });

  it('keeps a 4xx-rejected item with the server reason and continues with the next', async () => {
    const submit = vi.fn(async (i: QueueItem) => {
      if (i.key === 'k0') throw fail(422, 'Invalid report', [{ field: 'category', problem: 'unsupported' }]);
    });
    const r = await flush(fill(2), { submit });
    const [a, b] = r.state.items;
    expect(a.status).toBe('rejected');
    expect(a.error).toContain('Invalid report');
    expect(a.error).toContain('category: unsupported');
    expect(statusText(a)).toMatch(/^Rejected by server: Invalid report/);
    expect(b.status).toBe('sent');
  });

  it('keeps items and stops on 5xx so order is preserved', async () => {
    const submit = vi.fn(async () => {
      throw fail(503, 'down');
    });
    const r = await flush(fill(3), { submit });
    expect(submit).toHaveBeenCalledTimes(1);
    expect(r.outcome).toBe('retry');
    expect(r.state.items.map((i) => i.status)).toEqual(['queued', 'queued', 'queued']);
  });

  it('keeps items and flags a network failure', async () => {
    const r = await flush(fill(2), { submit: async () => Promise.reject(new TypeError('Failed to fetch')) });
    expect(r.outcome).toBe('retry');
    expect(r.networkFailure).toBe(true);
    expect(pendingCount(r.state)).toBe(2);
  });

  it('re-bootstraps once on 401 and retries the same item; a second 401 stops', async () => {
    let calls = 0;
    const reauth = vi.fn(async () => undefined);
    const ok = await flush(fill(1), {
      reauth,
      submit: async () => {
        if (calls++ === 0) throw fail(401);
      },
    });
    expect(reauth).toHaveBeenCalledTimes(1);
    expect(ok.state.items[0].status).toBe('sent');

    const reauth2 = vi.fn(async () => undefined);
    const stop = await flush(fill(2), { reauth: reauth2, submit: async () => Promise.reject(fail(401)) });
    expect(reauth2).toHaveBeenCalledTimes(1);
    expect(stop.outcome).toBe('auth');
    expect(pendingCount(stop.state)).toBe(2);
  });

  it('shows "Sent" only after a 2xx', async () => {
    let release: () => void = () => undefined;
    const states: string[] = [];
    const p = flush(fill(1), {
      submit: () => new Promise<void>((res) => (release = res)),
      onChange: (s) => states.push(statusText(s.items[0])),
    });
    await Promise.resolve();
    expect(states).toEqual(['Sending\u2026']);
    release();
    await p;
    expect(states.at(-1)).toBe('Sent \u2014 visible to other users');
  });
});

describe('OfflineManager', () => {
  const mk = (over: OfflineDeps = {}) => {
    let online = true;
    const storage = memoryStore();
    const submit = vi.fn<(i: QueueItem) => Promise<void>>(async () => undefined);
    const m = new OfflineManager({ storage, submit, navigatorOnline: () => online, schedule: () => () => undefined, now: () => 5000, ...over });
    return { m, storage, submit, setOnline: (v: boolean) => (online = v) };
  };

  it('queues while offline, then flushes in order on reconnect', async () => {
    const { m, submit, setOnline } = mk();
    await m.ready;
    setOnline(false);
    m.setNavigatorOnline(false);
    expect(m.getSnapshot().offline).toBe(true);
    m.queueItem('report', body(1));
    m.queueItem('message', { incident_id: 'i1', message_type: 'update', content: 'hello' });
    expect(submit).not.toHaveBeenCalled();
    expect(m.getSnapshot().pending).toBe(2);
    m.setNavigatorOnline(true);
    await m.flush();
    expect(submit.mock.calls.map((c) => c[0].kind)).toEqual(['report', 'message']);
    expect(m.getSnapshot().pending).toBe(0);
    expect(m.getSnapshot().lastSyncAt).toBe(5000);
  });

  it('persists the queue and restores it in a new manager', async () => {
    const { m, storage } = mk();
    await m.ready;
    m.setNavigatorOnline(false);
    m.queueItem('report', body(1));
    await new Promise((r) => setTimeout(r, 0));
    const m2 = new OfflineManager({ storage, navigatorOnline: () => false });
    await m2.ready;
    expect(m2.getSnapshot().queue.items).toHaveLength(1);
    expect(m2.getSnapshot().queue.items[0].status).toBe('queued');
  });

  it('treats a failed request as offline until a probe succeeds', async () => {
    let cb: (() => void) | null = null;
    const probe = vi.fn(async () => true);
    const { m } = mk({ probe, schedule: (fn) => ((cb = fn), () => undefined) });
    await m.ready;
    m.noteNetworkFailure();
    expect(m.getSnapshot().offline).toBe(true);
    cb!();
    await new Promise((r) => setTimeout(r, 0));
    expect(probe).toHaveBeenCalled();
    expect(m.getSnapshot().offline).toBe(false);
  });

  it('follows the realtime offline status', async () => {
    const { m } = mk();
    m.setRealtimeStatus('offline');
    expect(m.getSnapshot().offline).toBe(true);
    m.setRealtimeStatus('connected');
    expect(m.getSnapshot().offline).toBe(false);
  });
});
