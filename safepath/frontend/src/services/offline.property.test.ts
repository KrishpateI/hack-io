// Feature: safepath-evacuation-navigation, Property 38: Offline queue ordering, idempotence and capacity
// **Validates: Requirements 41.4, 41.5, 41.6, 41.7, 41.8**
//
// Drives the pure queue reducers and `flush` with random sequences of enqueue, re-enqueue
// (same client id) and flush operations against a model server that answers each submission
// with success, a 4xx rejection, a 5xx failure, or a "lost acknowledgement" (stored but 503).
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import {
  QUEUE_FULL_MESSAGE,
  QUEUE_MAX,
  emptyQueue,
  enqueue,
  flush,
  pendingCount,
  statusText,
  type QueueItem,
  type QueueKind,
  type QueueState,
} from './offline';

type Reply = 'ok' | 'reject' | 'fail' | 'lostAck';
type Op =
  | { t: 'enqueue'; kind: QueueKind; n: number }
  | { t: 'reenqueue'; pick: number }
  | { t: 'flush'; replies: Reply[] };

const kindArb = fc.constantFrom<QueueKind>('report', 'message', 'checkin');
const replyArb = fc.constantFrom<Reply>('ok', 'ok', 'reject', 'fail', 'lostAck');
const opArb: fc.Arbitrary<Op> = fc.oneof(
  { weight: 6, arbitrary: fc.record({ t: fc.constant('enqueue' as const), kind: kindArb, n: fc.nat(1000) }) },
  { weight: 1, arbitrary: fc.record({ t: fc.constant('reenqueue' as const), pick: fc.nat() }) },
  { weight: 2, arbitrary: fc.record({ t: fc.constant('flush' as const), replies: fc.array(replyArb, { maxLength: 20 }) }) },
);

const bodyFor = (kind: QueueKind, n: number) =>
  kind === 'report'
    ? { incident_id: 'i1', category: 'flooding', description: `r${n}` }
    : kind === 'message'
      ? { incident_id: 'i1', message_type: 'update', content: `m${n}` }
      : { incident_id: 'i1', status: 'safe' };

const KEY_FIELD: Record<QueueKind, string> = { report: 'client_report_id', message: 'client_request_id', checkin: 'client_request_id' };

describe('Property 38: Offline queue ordering, idempotence and capacity', () => {
  it('flushes in creation order, never duplicates on the server, keeps rejections, caps at 50', async () => {
    await fc.assert(
      fc.asyncProperty(fc.array(opArb, { maxLength: 120 }), async (ops) => {
        let state: QueueState = emptyQueue();
        let counter = 0;
        const makeKey = () => `key-${counter++}`;
        /** Model server: stores each report once per client id (Req 41.6). */
        const server = new Map<string, { kind: QueueKind; times: number }>();
        const seqOf = new Map<string, number>();
        const rejectedErrors = new Map<string, string>();

        for (const op of ops) {
          if (op.t === 'enqueue') {
            const before = state;
            const wasFull = pendingCount(before) >= QUEUE_MAX;
            const r = enqueue(state, op.kind, bodyFor(op.kind, op.n), 1, makeKey);
            if (wasFull) {
              // Req 41.8: refused with the queue-full message, state unchanged.
              expect(r.ok).toBe(false);
              if (!r.ok) expect(r.message).toBe(QUEUE_FULL_MESSAGE);
              expect(r.state).toBe(before);
            } else {
              expect(r.ok).toBe(true);
              if (!r.ok) return;
              expect(r.duplicate).toBe(false);
              // Req 41.6: unique client id attached to the body.
              expect(seqOf.has(r.item.key)).toBe(false);
              expect(r.item.body[KEY_FIELD[op.kind]]).toBe(r.item.key);
              // Req 41.4: queued, and the wording says it has not been sent.
              expect(r.item.status).toBe('queued');
              expect(statusText(r.item)).toMatch(/not sent/);
              seqOf.set(r.item.key, r.item.seq);
              state = r.state;
            }
          } else if (op.t === 'reenqueue') {
            if (state.items.length === 0) continue;
            const target = state.items[op.pick % state.items.length];
            const r = enqueue(state, target.kind, { ...target.body }, 2, makeKey);
            // Re-enqueueing a body carrying an existing id is a no-op.
            expect(r.ok && r.duplicate).toBe(true);
            expect(r.state).toBe(state);
          } else {
            const replies = [...op.replies];
            const submitted: QueueItem[] = [];
            const queuedBefore = state.items.filter((i) => i.status === 'queued').sort((a, b) => a.seq - b.seq);
            const res = await flush(state, {
              now: () => 3,
              submit: async (item) => {
                submitted.push(item);
                const reply = replies.shift() ?? 'ok';
                if (reply === 'reject') throw new ApiError(422, 'validation_error', 'Invalid report', [{ field: 'category', problem: `bad-${item.key}` }]);
                if (reply === 'fail') throw new ApiError(503, 'unavailable', 'down', []);
                const prev = server.get(item.key);
                if (prev) prev.times++; // same id: treated as the same report
                else server.set(item.key, { kind: item.kind, times: 1 });
                if (reply === 'lostAck') throw new ApiError(503, 'unavailable', 'ack lost', []);
              },
            });

            // Req 41.5: submissions follow creation order (a prefix of the queued items by seq).
            const seqs = submitted.map((i) => i.seq);
            expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
            expect(new Set(seqs).size).toBe(seqs.length);
            expect(submitted.map((i) => i.key)).toEqual(queuedBefore.slice(0, submitted.length).map((i) => i.key));
            // A retryable failure stops the flush; otherwise every queued item was attempted.
            if (res.outcome !== 'retry') expect(submitted.length).toBe(queuedBefore.length);

            state = res.state;
            for (const it of state.items) {
              if (it.status === 'rejected') {
                if (!rejectedErrors.has(it.key)) rejectedErrors.set(it.key, it.error ?? '');
              }
            }
          }

          // Invariants after every operation.
          expect(pendingCount(state)).toBeLessThanOrEqual(QUEUE_MAX);
          const keys = state.items.map((i) => i.key);
          expect(new Set(keys).size).toBe(keys.length);
          expect(state.items.some((i) => i.status === 'sending')).toBe(false);
          // Req 41.7: rejected items are retained with the server's error text.
          for (const [key, err] of rejectedErrors) {
            const it = state.items.find((i) => i.key === key);
            expect(it).toBeDefined();
            expect(it!.status).toBe('rejected');
            expect(it!.error).toBe(err);
            expect(err).toContain('Invalid report');
            expect(err).toContain(`category: bad-${key}`);
            expect(statusText(it!)).toBe(`Rejected by server: ${err}`);
          }
        }

        // Req 41.6: the server holds at most one record per client id, and every id it holds
        // came from the queue (resubmissions after a lost ack did not create duplicates).
        for (const key of server.keys()) expect(seqOf.has(key)).toBe(true);
        const sentKeys = state.items.filter((i) => i.status === 'sent').map((i) => i.key);
        for (const key of sentKeys) expect(server.has(key)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  it('a full queue (50 pending) refuses any further submission until one is discarded or sent', async () => {
    await fc.assert(
      fc.asyncProperty(fc.array(kindArb, { minLength: QUEUE_MAX + 1, maxLength: QUEUE_MAX + 20 }), async (kinds) => {
        let state = emptyQueue();
        let accepted = 0;
        let i = 0;
        for (const kind of kinds) {
          const r = enqueue(state, kind, bodyFor(kind, i), i, () => `k${i++}`);
          if (r.ok) {
            accepted++;
            state = r.state;
          } else {
            expect(r.message).toBe(QUEUE_FULL_MESSAGE);
          }
        }
        expect(accepted).toBe(QUEUE_MAX);
        expect(pendingCount(state)).toBe(QUEUE_MAX);
        // After a successful flush the cap is free again.
        const res = await flush(state, { submit: async () => undefined });
        expect(pendingCount(res.state)).toBe(0);
        expect(enqueue(res.state, 'report', bodyFor('report', 0), 0, () => 'fresh').ok).toBe(true);
      }),
      { numRuns: 100 },
    );
  });
});
