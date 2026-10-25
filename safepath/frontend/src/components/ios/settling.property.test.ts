// Feature: ios-style-ui-redesign, Property 9: Detent settling and cycling
// **Validates: Requirements 7.11, 7.12**
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { nearestDetent, nextDetent, type Detent, type DetentHeights } from './layout';

const DETENTS: Detent[] = ['collapsed', 'half', 'full'];
const num = (min: number, max: number) => fc.double({ min, max, noNaN: true });

const heightsArb: fc.Arbitrary<DetentHeights> = fc
  .tuple(num(0, 2000), num(0, 2000), num(0, 2000))
  .map(([collapsed, half, full]) => ({ collapsed, half, full }));

describe('Property 9: Detent settling and cycling', () => {
  it('always returns one of the three Detents', () => {
    fc.assert(
      fc.property(heightsArb, num(-3000, 3000), num(-10, 10), (h, y, v) => {
        expect(DETENTS).toContain(nearestDetent(h, y, v));
      }),
      { numRuns: 100 },
    );
  });

  it('returns the closest Detent when velocity is zero', () => {
    fc.assert(
      fc.property(heightsArb, num(-3000, 3000), (h, y) => {
        const result = nearestDetent(h, y, 0);
        const min = Math.min(...DETENTS.map((d) => Math.abs(h[d] - y)));
        expect(Math.abs(h[result] - y)).toBe(min);
      }),
      { numRuns: 100 },
    );
  });

  it('applying nextDetent three times returns the starting Detent', () => {
    fc.assert(
      fc.property(fc.constantFrom(...DETENTS), (d) => {
        expect(nextDetent(nextDetent(nextDetent(d)))).toBe(d);
      }),
      { numRuns: 100 },
    );
  });
});
