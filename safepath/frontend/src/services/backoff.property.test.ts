// Feature: safepath-evacuation-navigation, Property 37: Reconnect backoff and polling bounds
// **Validates: Requirements 25.7, 32.4**
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { BACKOFF_CAP_MS, POLL_MIN_INTERVAL_MS, backoffDelay, baseDelay, pollInterval } from './backoff';
import { RealtimeController } from './realtimeController';
import type { RealtimeClient, RealtimeHandlers } from './realtimeClients';
import type { RealtimeEvent } from './eventStore';

const RUNS = { numRuns: 100 };
/** Expected delay after n consecutive failed attempts (n ≥ 1). */
const expected = (n: number) => Math.min(30_000, 1000 * 2 ** (n - 1));

/** Manual scheduler injected into the controller so every scheduled timer is observable by id. */
class Scheduler {
  private seq = 0;
  pending = new Map<number, { fn: () => void; ms: number }>();
  log: { id: number; ms: number }[] = [];
  set = (fn: () => void, ms: number): unknown => {
    const id = ++this.seq;
    this.pending.set(id, { fn, ms });
    this.log.push({ id, ms });
    return id;
  };
  clear = (id: unknown): void => void this.pending.delete(id as number);
  fire(id: number): void {
    const t = this.pending.get(id);
    if (!t) throw new Error(`timer ${id} not pending`);
    this.pending.delete(id);
    t.fn();
  }
}

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

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

describe('Property 37: Reconnect backoff and polling bounds', () => {
  it('un-jittered delay is min(30 s, 1 s × 2^(n−1)), non-decreasing and capped', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 200 }), (n) => {
        const d = baseDelay(n - 1);
        expect(d).toBe(expected(n));
        expect(d).toBeLessThanOrEqual(BACKOFF_CAP_MS);
        expect(baseDelay(n)).toBeGreaterThanOrEqual(d);
      }),
      RUNS,
    );
  });

  it('jittered delay stays within [base/2, base] and never exceeds 30 s', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 200 }), fc.double({ min: 0, max: 1, noNaN: true }), (n, r) => {
        const base = expected(n);
        const d = backoffDelay(n - 1, () => r);
        expect(d).toBeGreaterThanOrEqual(base / 2);
        expect(d).toBeLessThanOrEqual(base);
        expect(d).toBeLessThanOrEqual(BACKOFF_CAP_MS);
      }),
      RUNS,
    );
  });

  it('poll interval is never below 30 s for any requested value', () => {
    fc.assert(
      fc.property(fc.integer({ min: -1_000_000, max: 1_000_000 }), (req) => {
        const p = pollInterval(req);
        expect(p).toBeGreaterThanOrEqual(POLL_MIN_INTERVAL_MS);
        expect(p).toBe(Math.max(POLL_MIN_INTERVAL_MS, req));
      }),
      RUNS,
    );
  });

  it('controller reconnects on the backoff schedule and polls no faster than every 30 s', async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 12 }),
        fc.integer({ min: 0, max: 120_000 }),
        fc.integer({ min: 1, max: 4 }),
        async (failures, requestedPoll, polls) => {
          const sched = new Scheduler();
          const client = new FakeClient();
          const fetchSince = async () => [] as RealtimeEvent[];
          const c = new RealtimeController({
            client,
            fetchSince,
            random: () => 1, // full delay: equals the base schedule exactly
            isOnline: () => true,
            pollIntervalMs: requestedPoll,
            setTimer: sched.set,
            clearTimer: sched.clear,
          });
          c.start('i');
          const reconnectIds = new Set<number>();

          // n consecutive failures: each schedules exactly one reconnect timer with the expected delay.
          let prev = 0;
          for (let n = 1; n <= failures; n++) {
            const before = sched.log.length;
            client.h.onClose();
            const added = sched.log.slice(before);
            expect(added).toHaveLength(1);
            const { id, ms } = added[0];
            reconnectIds.add(id);
            expect(ms).toBe(expected(n));
            expect(ms).toBeGreaterThanOrEqual(prev);
            expect(ms).toBeLessThanOrEqual(BACKOFF_CAP_MS);
            prev = ms;
            const connects = client.connects;
            sched.fire(id);
            expect(client.connects).toBe(connects + 1);
          }

          // Drive the polling fallback while still disconnected.
          for (let k = 0; k < polls; k++) {
            const pollTimers = [...sched.pending.entries()].filter(([id, t]) => !reconnectIds.has(id) && t.ms >= POLL_MIN_INTERVAL_MS);
            expect(pollTimers).toHaveLength(1);
            sched.fire(pollTimers[0][0]);
            await flush();
          }

          // Every non-reconnect, non-stale timer (i.e. every poll) is ≥ 30 s.
          const staleMs = 5000;
          const polled = sched.log.filter((e) => !reconnectIds.has(e.id) && e.ms !== staleMs);
          expect(polled.length).toBe(polls + 1);
          polled.forEach((e) => expect(e.ms).toBe(Math.max(POLL_MIN_INTERVAL_MS, requestedPoll)));

          c.stop();
        },
      ),
      RUNS,
    );
  });
});
