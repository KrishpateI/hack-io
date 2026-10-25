/**
 * Real-browser layout checks (Req 34.7, 36.2; iOS redesign 3.1, 13.3, 13.5, 13.7, 14.9): no horizontal
 * scrolling at 320, 768, 1280 and 2560 px on the map dashboard, the indoor building view and the Demo
 * console, plus Side_Panel / Inspector_Panel geometry on the Desktop_Viewport. Complements the jsdom
 * tests of 13.9, which cannot measure layout.
 */
import { expect, test, type Locator } from '@playwright/test';
import { hasHorizontalScroll, openApp, openConsoleAsModerator } from './helpers';

/** 320 = Compact, 768 = first Wide width, 1280 = first Desktop width, 2560 = widest Desktop. */
const WIDTHS = [320, 768, 1280, 2560] as const;
const HEIGHTS: Record<(typeof WIDTHS)[number], number> = { 320: 640, 768: 1024, 1280: 800, 2560: 1440 };

/** Map_View minimum visible width beside the Side_Panel and Inspector_Panel (Req 13.7). */
const MAP_MIN_WIDTH = 480;

for (const width of WIDTHS) {
  test.describe(`viewport ${width} px`, () => {
    test.use({ viewport: { width, height: HEIGHTS[width] } });

    test('map dashboard has no horizontal scroll', async ({ page }) => {
      await openApp(page);
      await expect(page.getByTestId('disaster-selector')).toContainText('sim-flood-loop');
      await expect(page.getByRole('banner')).toBeVisible();
      // The Map tab's quick-action row labels this control "Report a hazard".
      await expect(page.getByRole('button', { name: /Report a hazard/i }).first()).toBeVisible();
      expect(await hasHorizontalScroll(page)).toMatchObject({ overflow: false });
    });

    test('indoor building view has no horizontal scroll', async ({ page }) => {
      await openApp(page);
      const selector = page.getByTestId('disaster-selector');
      await selector.getByRole('button', { name: /Building emergency/ }).click();
      await selector.getByRole('button', { name: /sim-blocked-stairwell/ }).click();
      await expect(page.getByTestId('indoor-view')).toBeVisible();
      expect(await hasHorizontalScroll(page)).toMatchObject({ overflow: false });
    });

    test('demo console has no horizontal scroll', async ({ page }) => {
      await openApp(page);
      // More tab -> Demo console, then a moderator demo session: the scenario list is moderator-only (HTTP 403 otherwise).
      await openConsoleAsModerator(page);
      await expect(page.getByTestId('scenario-flood_loop')).toBeVisible();
      await expect(page.getByTestId('actor-panel')).toBeVisible();
      expect(await hasHorizontalScroll(page)).toMatchObject({ overflow: false });
    });
  });
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function box(locator: Locator): Promise<Box> {
  const b = await locator.boundingBox();
  if (!b) throw new Error('element has no bounding box');
  return b;
}

/** Desktop_Viewport geometry (Req 13.5, 13.7): Side_Panel leading, Inspector_Panel trailing, map keeps 480 px. */
for (const width of [1280, 2560] as const) {
  test.describe(`desktop geometry ${width} px`, () => {
    test.use({ viewport: { width, height: HEIGHTS[width] } });

    test('Side_Panel and Inspector_Panel leave the Map_View at least 480 px wide', async ({ page }) => {
      await openApp(page);
      await expect(page.getByTestId('dashboard')).toHaveAttribute('data-viewport-tier', 'desktop');

      const mapView = page.getByTestId('map-view');
      const sidePanel = page.getByRole('region', { name: 'SafePath panel' });
      await expect(sidePanel).toBeVisible();

      // Map_View fills the whole stage; the panels float over its leading edge.
      const stage = await box(page.getByTestId('dashboard'));
      const map = await box(mapView);
      const panel = await box(sidePanel);
      expect(map.width).toBeGreaterThanOrEqual(stage.width - 1);
      expect(panel.x).toBeLessThan(stage.x + stage.width / 2);
      expect(panel.width).toBeGreaterThanOrEqual(360);
      expect(panel.width).toBeLessThanOrEqual(720);

      // Without the inspector, the map is visible from the Side_Panel's trailing edge to the stage edge.
      const mapRight = map.x + map.width;
      expect(mapRight - (panel.x + panel.width)).toBeGreaterThanOrEqual(MAP_MIN_WIDTH);

      // Click the visible map to select a location, which opens the Inspector_Panel at Desktop.
      await page.mouse.click(panel.x + panel.width + MAP_MIN_WIDTH / 2, map.y + map.height / 2);
      const inspector = page.getByTestId('inspector-panel');
      await expect(inspector).toBeVisible();

      const insp = await box(inspector);
      const panelAfter = await box(sidePanel);
      expect(insp.width).toBeGreaterThanOrEqual(340);
      expect(insp.width).toBeLessThanOrEqual(400);
      // Side_Panel on the leading edge, Inspector_Panel on the trailing edge, both inside the Map_View.
      expect(panelAfter.x).toBeGreaterThanOrEqual(map.x);
      expect(insp.x + insp.width).toBeLessThanOrEqual(mapRight + 1);
      expect(insp.x).toBeGreaterThan(panelAfter.x + panelAfter.width);
      // The map visible between the two panels stays at least 480 px wide.
      expect(insp.x - (panelAfter.x + panelAfter.width)).toBeGreaterThanOrEqual(MAP_MIN_WIDTH);
      expect(await hasHorizontalScroll(page)).toMatchObject({ overflow: false });
    });
  });
}
