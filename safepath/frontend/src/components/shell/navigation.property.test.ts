import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { TAB_IDS, formatLocation, parseLocation, type NavLocation } from './navigation';
import { SCREEN_IDS, screenFromHash, screenHash } from './screens';

// Feature: ios-style-ui-redesign, Property 4: Navigation location round trip and totality
// **Validates: Requirements 2.2, 6.5**

const navLocation: fc.Arbitrary<NavLocation> = fc.oneof(
  fc.constantFrom(...TAB_IDS).map((tab): NavLocation => ({ kind: 'tab', tab })),
  fc.constantFrom(...SCREEN_IDS).map((screen): NavLocation => ({ kind: 'screen', screen })),
  fc.constant<NavLocation>({ kind: 'demo' }),
);

const isValid = (loc: NavLocation): boolean =>
  (loc.kind === 'tab' && (TAB_IDS as readonly string[]).includes(loc.tab)) ||
  (loc.kind === 'screen' && (SCREEN_IDS as readonly string[]).includes(loc.screen)) ||
  loc.kind === 'demo';

describe('Property 4: Navigation location round trip and totality', () => {
  it('parseLocation(formatLocation(loc)) equals loc', () => {
    fc.assert(
      fc.property(navLocation, (loc) => {
        expect(parseLocation(formatLocation(loc))).toEqual(loc);
      }),
      { numRuns: 100 },
    );
  });

  it('parseLocation is total for arbitrary strings and returns a valid location', () => {
    fc.assert(
      fc.property(fc.oneof(fc.string(), fc.string({ unit: 'binary' }), fc.string().map((s) => `#/${s}`)), (s) => {
        const loc = parseLocation(s);
        expect(isValid(loc)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });

  it('unknown values resolve to the Map tab', () => {
    const known = new Set<string>([...SCREEN_IDS, 'sources', 'demo']);
    fc.assert(
      fc.property(
        fc.string().filter((s) => !known.has(s.replace(/^#\/?/, '')) && !s.replace(/^#\/?/, '').startsWith('tab/')),
        (s) => {
          expect(parseLocation(`#/${s}`)).toEqual({ kind: 'tab', tab: 'map' });
        },
      ),
      { numRuns: 100 },
    );
  });

  it('legacy hashes (#/sources and the eight screen hashes) resolve to the same screens as before', () => {
    expect(parseLocation('#/sources')).toEqual({ kind: 'screen', screen: screenFromHash('#/sources') });
    expect(screenFromHash('#/sources')).toBe('data-sources');
    expect(SCREEN_IDS).toHaveLength(8);
    fc.assert(
      fc.property(fc.constantFrom(...SCREEN_IDS), (id) => {
        expect(parseLocation(screenHash(id))).toEqual({ kind: 'screen', screen: id });
        expect(screenFromHash(screenHash(id))).toBe(id);
      }),
      { numRuns: 100 },
    );
  });
});
