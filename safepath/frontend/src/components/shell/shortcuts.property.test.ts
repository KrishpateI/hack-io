import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  INITIAL_SHORTCUT_STATE,
  SHORTCUT_WINDOW_MS,
  shortcutReducer,
  type ShortcutEvent,
  type ShortcutState,
} from './useKeyboardShortcuts';
import { TAB_IDS, type TabId } from './navigation';

/** Small oracle: tracks the last accepted "g" time and decides the tab for each event. */
function oracle(events: ShortcutEvent[], enabled: boolean): (TabId | null)[] {
  let armedAt: number | null = null;
  const out: (TabId | null)[] = [];
  for (const e of events) {
    if (!enabled) {
      armedAt = null;
      out.push(null);
      continue;
    }
    if (e.ctrl || e.alt || e.meta || e.inEditable) {
      out.push(null);
      continue;
    }
    if (e.key === 'g') {
      armedAt = e.at;
      out.push(null);
      continue;
    }
    const within = armedAt !== null && e.at >= armedAt && e.at - armedAt <= 1500;
    const digit = /^[1-5]$/.test(e.key) ? Number(e.key) : 0;
    out.push(within && digit ? TAB_IDS[digit - 1] : null);
    armedAt = null;
  }
  return out;
}

const eventArb = fc.record({
  key: fc.oneof(
    fc.constantFrom('g', 'g', '1', '2', '3', '4', '5', '0', '6', 'a', 'G', 'Escape'),
    fc.string({ maxLength: 2 }),
  ),
  delta: fc.oneof(fc.integer({ min: 0, max: 100 }), fc.integer({ min: 1400, max: 1600 }), fc.integer({ min: 0, max: 5000 })),
  ctrl: fc.boolean(),
  alt: fc.boolean(),
  meta: fc.boolean(),
  inEditable: fc.boolean(),
});

// Bias towards no modifiers so that sequences actually activate tabs.
const cleanBias = fc.record({ ctrl: fc.constant(false), alt: fc.constant(false), meta: fc.constant(false), inEditable: fc.constant(false) });

const sequenceArb = fc
  .array(fc.oneof(eventArb, fc.tuple(eventArb, cleanBias).map(([a, b]) => ({ ...a, ...b }))), { maxLength: 30 })
  .map((raw) => {
    let t = 0;
    return raw.map<ShortcutEvent>(({ delta, ...rest }) => {
      t += delta;
      return { ...rest, at: t };
    });
  });

function run(events: ShortcutEvent[], enabled: boolean): (TabId | null)[] {
  let state: ShortcutState = INITIAL_SHORTCUT_STATE;
  return events.map((e) => {
    const r = shortcutReducer(state, e, enabled);
    state = r.state;
    return r.tab;
  });
}

describe('shortcuts property', () => {
  // Feature: ios-style-ui-redesign, Property 10: Keyboard shortcut sequences
  // **Validates: Requirements 16.1, 16.2, 16.3, 16.5**
  it('matches the oracle and never activates when disabled', () => {
    fc.assert(
      fc.property(sequenceArb, fc.boolean(), (events, enabled) => {
        const actual = run(events, enabled);
        expect(actual).toEqual(oracle(events, enabled));
        if (!enabled) expect(actual.every((t) => t === null)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  it('activates on g then digit within the window', () => {
    const mk = (key: string, at: number): ShortcutEvent => ({ key, at, ctrl: false, alt: false, meta: false, inEditable: false });
    expect(run([mk('g', 0), mk('3', SHORTCUT_WINDOW_MS)], true)[1]).toBe(TAB_IDS[2]);
    expect(run([mk('g', 0), mk('3', SHORTCUT_WINDOW_MS + 1)], true)[1]).toBeNull();
  });
});
