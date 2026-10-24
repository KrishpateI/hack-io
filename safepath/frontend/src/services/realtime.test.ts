import { describe, expect, it, vi } from 'vitest';
import { backoffDelay, baseDelay, pollInterval } from './backoff';
import { applyEvent, applyEvents, initialEventState, type RealtimeEvent } from './eventStore';
import { RealtimeController } from './realtimeController';
import type { RealtimeClient, RealtimeHandlers } from './realtimeClients';

const ev = (id: string, type = 'report.created', payload: Record<string, unknown> = { report_id: 'r1' }): RealtimeEvent => ({
  event_id: id,
  type,
  incident_id: 'i',
  payload,
  created_at: '2025-01-01T00:00:00Z',
});

describe('backoff', () => {
  it('doubles from 1 s to a 30 s cap', () => {
    expect([0, 1, 2, 3, 4, 5, 50].map(baseDelay)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  });
  it('jitter stays within [base/2, base]', () => {
    expect(backoffDelay(10, () => 0)).toBe(15000);
    expect(backoffDelay(10, () => 1)).toBe(30000);
  });
  it('polling is never under 30 s', () => {
    expect(pollInterval(1000)).toBe(30000);
  });
});

describe('eventStore', () => {
  it('is idempotent per event_id', () => {
    const a = applyEvent(initialEventState, ev('01'));
    expect(applyEvent(a, ev('01'))).toBe(a);
  });
  it('converges regardless of arrival order', () => {
    const e1 = ev('01', 'report.created', { report_id: 'r1', status: 'unverified', a: 1 });
    const e2 = ev('02', 'report.updated', { report_id: 'r1', status: 'verified' });
    const fwd = applyEvents(initialEventState, [e1, e2]);
    const rev = [e2, e1].reduce(applyEvent, initialEventState);
    expect(rev.reports.r1).toEqual(fwd.reports.r1);
    expect(fwd.reports.r1).toMatchObject({ status: 'verified', a: 1 });
    expect(fwd.lastEventId).toBe('02');
  });
  it('routes messages, check-ins and restrictions', () => {
    const s = applyEvents(initialEventState, [
      ev('1', 'message.created', { message_id: 'm' }),
      ev('2', 'checkin.created', { checkin_id: 'c' }),
      ev('3', 'restriction.created', { restriction_id: 'x' }),
    ]);
    expect(Object.keys(s.messages)).toEqual(['m']);
    expect(Object.keys(s.checkins)).toEqual(['c']);
    expect(Object.keys(s.restrictions)).toEqual(['x']);
  });
});

class FakeClient implements RealtimeClient {
  name = 'fake';
  h!: RealtimeHandlers;
  connects = 0;
  connect(_i: string, h: RealtimeHandlers) {
    this.h = h;
    this.connects++;
  }
  close() {}
}

describe('RealtimeController', () => {
  it('shows stale warning within 10 s, reconnects, and catches up once', async () => {
    vi.useFakeTimers();
    const client = new FakeClient();
    const fetchSince = vi.fn(async (_i: string, since: string | null) =>
      since === '01' ? [ev('02', 'report.updated', { report_id: 'r1', status: 'verified' }), ev('01')] : [],
    );
    const c = new RealtimeController({ client, fetchSince, random: () => 0 });
    c.start('i');
    client.h.onOpen();
    await vi.advanceTimersByTimeAsync(0);
    expect(c.getSnapshot().status).toBe('connected');
    client.h.onEvent(ev('01'));

    client.h.onClose();
    expect(c.getSnapshot().status).toBe('reconnecting');
    expect(c.getSnapshot().stale).toBe(false);
    await vi.advanceTimersByTimeAsync(5000);
    expect(c.getSnapshot().stale).toBe(true);
    expect(client.connects).toBe(2); // reconnected after 0.5 s jittered backoff

    client.h.onOpen();
    await vi.advanceTimersByTimeAsync(0);
    const s = c.getSnapshot();
    expect(s.status).toBe('connected');
    expect(s.stale).toBe(false);
    expect(fetchSince).toHaveBeenLastCalledWith('i', '01');
    expect(s.store.reports.r1).toMatchObject({ status: 'verified' });
    expect(Object.keys(s.store.appliedEventIds)).toHaveLength(2);
    c.stop();
    vi.useRealTimers();
  });

  it('polls no more often than every 30 s while disconnected', async () => {
    vi.useFakeTimers();
    const client = new FakeClient();
    const fetchSince = vi.fn(async () => [] as RealtimeEvent[]);
    const c = new RealtimeController({ client, fetchSince, isOnline: () => false });
    c.start('i');
    expect(c.getSnapshot().status).toBe('offline');
    await vi.advanceTimersByTimeAsync(29000);
    expect(fetchSince).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchSince).toHaveBeenCalledTimes(1);
    c.stop();
    vi.useRealTimers();
  });
});
