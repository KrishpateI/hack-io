import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { stageLayout, type PanelMode } from './layout';

// Feature: ios-style-ui-redesign, Property 7: Stage layout keeps the map usable
// **Validates: Requirements 7.2, 7.3, 7.5, 13.5, 13.7**

const modeArb = fc.constantFrom<PanelMode>('default', 'expanded', 'collapsed');

describe('Property 7: Stage layout keeps the map usable', () => {
  it('keeps map >= 480, panel and inspector widths in range, and canExpand honest', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1280, max: 2560 }),
        modeArb,
        fc.boolean(),
        (width, mode, inspectorOpen) => {
          const r = stageLayout(width, mode, inspectorOpen);
          expect(r.mapWidth).toBeGreaterThanOrEqual(480);
          if (mode === 'collapsed') {
            expect(r.panelWidth).toBe(72);
          } else {
            expect(r.panelWidth).toBeGreaterThanOrEqual(360);
            expect(r.panelWidth).toBeLessThanOrEqual(720);
          }
          if (inspectorOpen) {
            expect(r.inspectorWidth).toBeGreaterThanOrEqual(340);
            expect(r.inspectorWidth).toBeLessThanOrEqual(400);
          } else {
            expect(r.inspectorWidth).toBe(0);
          }
          // canExpand false => the map cannot afford a wider panel than the current one.
          if (!r.canExpand && mode !== 'collapsed') {
            // Growing the panel by any amount would drop the map below 480.
            const slack = r.mapWidth - 480;
            expect(slack === 0 || r.panelWidth >= 720).toBe(true);
          }
          // canExpand true => expanding really keeps the map usable.
          if (r.canExpand) {
            const e = stageLayout(width, 'expanded', inspectorOpen);
            expect(e.mapWidth).toBeGreaterThanOrEqual(480);
            expect(e.panelWidth).toBeGreaterThanOrEqual(r.panelWidth);
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
