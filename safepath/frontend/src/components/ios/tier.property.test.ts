import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { viewportTier, type ViewportTier } from './layout';

const RANK: Record<ViewportTier, number> = { compact: 0, wide: 1, desktop: 2 };
const width = fc.integer({ min: 320, max: 2560 });

// Feature: ios-style-ui-redesign, Property 6: Viewport tier classification is total and monotonic
describe('Property 6: Viewport tier classification is total and monotonic', () => {
  it('classifies every width by the 768 / 1280 thresholds', () => {
    // **Validates: Requirements 6.1, 6.2, 7.2, 7.5, 7.6, 13.5**
    fc.assert(
      fc.property(width, (w) => {
        const expected: ViewportTier = w < 768 ? 'compact' : w < 1280 ? 'wide' : 'desktop';
        expect(viewportTier(w)).toBe(expected);
      }),
      { numRuns: 100 },
    );
  });

  it('never ranks a smaller width above a larger one', () => {
    // **Validates: Requirements 6.1, 6.2, 7.2, 7.5, 7.6, 13.5**
    fc.assert(
      fc.property(width, width, (x, y) => {
        const [a, b] = x <= y ? [x, y] : [y, x];
        expect(RANK[viewportTier(a)]).toBeLessThanOrEqual(RANK[viewportTier(b)]);
      }),
      { numRuns: 100 },
    );
  });
});
