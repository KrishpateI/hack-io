import { describe, expect, it } from 'vitest';
import { detentHeights, nearestDetent, nextDetent, stageLayout, viewportTier } from './layout';

describe('layout helpers', () => {
  it('classifies viewport tiers at the boundaries', () => {
    expect(viewportTier(320)).toBe('compact');
    expect(viewportTier(767)).toBe('compact');
    expect(viewportTier(768)).toBe('wide');
    expect(viewportTier(1279)).toBe('wide');
    expect(viewportTier(1280)).toBe('desktop');
    expect(viewportTier(2560)).toBe('desktop');
  });

  it('keeps the map at least 480 px from 1280 to 2560', () => {
    for (const w of [1280, 1500, 2560]) {
      for (const mode of ['default', 'expanded', 'collapsed'] as const) {
        for (const open of [false, true]) {
          expect(stageLayout(w, mode, open).mapWidth).toBeGreaterThanOrEqual(480);
        }
      }
    }
    expect(stageLayout(2560, 'collapsed', false).panelWidth).toBe(72);
    expect(stageLayout(2560, 'default', true).panelWidth).toBe(400);
  });

  it('accounts for the real panel offsets with the inspector open at 1280', () => {
    // room = 1280 - 12 (left) - 12 (gap) - 12 (gap) - 60 (inspector right) = 1184
    const r = stageLayout(1280, 'default', true);
    expect(r.panelWidth).toBe(360);
    expect(r.inspectorWidth).toBe(344);
    expect(r.mapWidth).toBe(480);
    expect(r.panelWidth + r.inspectorWidth + r.mapWidth).toBe(1184);
  });

  it('computes detent heights', () => {
    expect(detentHeights(600)).toEqual({ collapsed: 88, half: 300, full: 592 });
    expect(detentHeights(300).half).toBe((88 + 292) / 2);
  });

  it('cycles and settles detents', () => {
    expect(nextDetent(nextDetent(nextDetent('half')))).toBe('half');
    const h = detentHeights(600);
    expect(nearestDetent(h, 120, 0)).toBe('collapsed');
    expect(nearestDetent(h, 290, 0)).toBe('half');
  });
});
