// Feature: safepath-evacuation-navigation, Property 36: Event application is idempotent, order-tolerant by id, and catch-up is complete
/**
 * Property 36: for any sequence of events with arbitrary duplication and arbitrary
 * interleaving of live delivery and catch-up batches, the eventStore reaches the same
 * state as applying each distinct event once in event_id order; discarding duplicates
 * never changes state; and after reconnect, catch-up from lastEventId yields every
 * event stored after it.
 *
 * **Validates: Requirements 25.6, 25.8**
 */
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { applyEvent, applyEvents, initialEventState, type EventStoreState, type RealtimeEvent } from './eventStore';
import { RealtimeController } from './realtimeController';
import type { RealtimeClient, RealtimeHandlers } from './realtimeClients';

const TYPES = [
  ['report.created', 'report_id'],
  ['report.updated', 'report_id'],
  ['message.created', 'message_id'],
  ['checkin.updated', 'checkin_id'],
  ['restriction.created', 'restriction_id'],
  ['incident.updated', 'id'], // unknown kind: only tracked as applied
] as const;

/** Event payload "skeletons"; ids and timestamps are assigned once the list is generated. */
const eventSpec = fc.record({
  typeIdx: fc.nat(TYPES.length - 1),
  entity: fc.constantFrom('e1', 'e2', 'e3'),
  fields: fc.dictionary(fc.constantFrom('a', 'b', 'c', 'status'), fc.integer({ min: 0, max: 5 }), { maxKeys: 3 }),
});

/** Distinct events whose fixed-width ids sort chronologically (like ULIDs). */
const eventsArb = fc.array(eventSpec, { minLength: 1, maxLength: 12 }).map((specs) =>
  specs.map((s, i): RealtimeEvent => {
    const [type, idKey] = TYPES[s.typeIdx];
    return {
      event_id: `EV${String(i).padStart(4, '0')}`,
      type,
      incident_id: 'inc',
      payload: { [idKey]: s.entity, ...s.fields },
      created_at: new Date(Date.UTC(2025, 0, 1, 0, 0, i)).toISOString(),
    };
  }),
);

/** Every event at least once, plus duplicates, in arbitrary order. */
const deliveryArb = (events: RealtimeEvent[]) =>
  fc
    .array(fc.nat(events.length - 1), { maxLength: 12 })
    .chain((dups) => {
      const all = [...events, ...dups.map((i) => events[i])];
      return fc.shuffledSubarray(all, { minLength: all.length, maxLength: all.length });
    });

/** Split a delivery into chunks, each applied as live events (one by one) or as a catch-up batch. */
const chunkModesArb = fc.array(fc.record({ size: fc.integer({ min: 1, max: 4 }), batch: fc.boolean() }), {
  minLength: 1,
  maxLength: 30,
});

function deliver(delivery: RealtimeEvent[], modes: { size: number; batch: boolean }[]): EventStoreState {
  let state = initialEventState;
  let pos = 0;
  let m = 0;
  while (pos < delivery.length) {
    const { size, batch } = modes[m++ % modes.length];
    const chunk = delivery.slice(pos, pos + size);
    pos += size;
    state = batch ? applyEvents(state, chunk) : chunk.reduce(applyEvent, state);
  }
  return state;
}

describe('Property 36: event application', () => {
  it('converges to in-order single application under duplication and interleaving', () => {
    fc.assert(
      fc.property(
        eventsArb.chain((events) => fc.tuple(fc.constant(events), deliveryArb(events), chunkModesArb)),
        ([events, delivery, modes]) => {
          const expected = applyEvents(initialEventState, events);
          const actual = deliver(delivery, modes);
          expect(actual).toEqual(expected);
          expect(Object.keys(actual.appliedEventIds)).toHaveLength(events.length);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('discarding a duplicate never changes state', () => {
    fc.assert(
      fc.property(
        eventsArb.chain((events) => fc.tuple(fc.constant(events), deliveryArb(events), fc.nat())),
        ([events, delivery, pick]) => {
          const state = delivery.reduce(applyEvent, initialEventState);
          const dup = events[pick % events.length];
          expect(applyEvent(state, dup)).toBe(state);
          expect(applyEvents(state, [dup, dup])).toEqual(state);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('catch-up after reconnect yields every event stored after lastEventId', async () => {
    class FakeClient implements RealtimeClient {
      name = 'fake';
      h!: RealtimeHandlers;
      connect(_i: string, h: RealtimeHandlers) {
        this.h = h;
      }
      close() {}
    }
    const flush = () => new Promise<void>((r) => setTimeout(r, 0));

    // Per stored event: live delivery while connected (once or duplicated; events stored
    // while disconnected are missed and must come from catch-up), and whether the
    // connection toggles after it.
    const stepArb = fc.record({
      live: fc.constantFrom('once', 'twice'),
      disconnectAfter: fc.boolean(),
      overlap: fc.boolean(), // server re-sends the boundary event in the catch-up response
    });

    await fc.assert(
      fc.asyncProperty(
        eventsArb.chain((events) =>
          fc.tuple(fc.constant(events), fc.array(stepArb, { minLength: events.length, maxLength: events.length })),
        ),
        async ([events, steps]) => {
          const stored: RealtimeEvent[] = [];
          let overlap = false;
          const fetchSince = async (_i: string, since: string | null) => {
            const after = stored.filter((e) => since === null || e.event_id > since);
            const boundary = stored.filter((e) => e.event_id === since);
            return overlap ? [...boundary, ...after].reverse() : after;
          };
          const client = new FakeClient();
          const c = new RealtimeController({
            client,
            fetchSince,
            isOnline: () => true,
            // Timers are driven manually; reconnects are simulated by calling onOpen.
            setTimer: () => null,
            clearTimer: () => {},
          });
          c.start('inc');
          client.h.onOpen();
          await flush();
          let connected = true;

          for (let i = 0; i < events.length; i++) {
            const e = events[i];
            const step = steps[i];
            stored.push(e);
            if (connected) {
              client.h.onEvent(e);
              if (step.live === 'twice') client.h.onEvent(e);
            }
            if (step.disconnectAfter) {
              if (connected) {
                client.h.onClose();
                connected = false;
              } else {
                const before = c.getSnapshot().store.lastEventId;
                overlap = step.overlap;
                client.h.onOpen();
                await flush();
                connected = c.getSnapshot().status === 'connected';
                // Every stored event after the pre-reconnect lastEventId is now applied.
                const applied = c.getSnapshot().store.appliedEventIds;
                for (const s of stored) if (before === null || s.event_id > before) expect(applied[s.event_id]).toBe(true);
              }
            }
          }

          // Final reconnect: the store must equal applying all stored events once, in order.
          if (connected) client.h.onClose();
          overlap = false;
          client.h.onOpen();
          await flush();
          const snap = c.getSnapshot();
          expect(snap.status).toBe('connected');
          expect(snap.store).toEqual(applyEvents(initialEventState, stored));
          c.stop();
        },
      ),
      { numRuns: 100 },
    );
  });
});
