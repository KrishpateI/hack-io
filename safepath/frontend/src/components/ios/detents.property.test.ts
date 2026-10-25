import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { detentHeights } from './layout';

describe('detentHeights', () => {
  // Feature: ios-style-ui-redesign, Property 8: Detent geometry stays inside the stage
  // **Validates: Requirements 3.2, 3.5, 7.1, 7.10**
  it('keeps 0 < collapsed < half < full <= available for any available >= 200', () => {
    fc.assert(
      fc.property(fc.double({ min: 200, max: 4000, noNaN: true }), (available) => {
        const { collapsed, half, full } = detentHeights(available);
        expect(collapsed).toBeGreaterThan(0);
        expect(collapsed).toBeLessThan(half);
        expect(half).toBeLessThan(full);
        expect(full).toBeLessThanOrEqual(available);
      }),
      { numRuns: 100 },
    );
  });

  it('holds at integer boundaries around the 400 px switch', () => {
    for (const available of [200, 399, 400, 401, 2000]) {
      const { collapsed, half, full } = detentHeights(available);
      expect(0 < collapsed && collapsed < half && half < full && full <= available).toBe(true);
    }
  });
});
