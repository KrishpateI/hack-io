/**
 * Real-browser layout checks (Req 34.7, 36.2): no horizontal scrolling at 320 px and 2560 px on the
 * map dashboard, the indoor building view and the Demo console. Complements the jsdom tests of 13.9,
 * which cannot measure layout.
 */
import { expect, test } from '@playwright/test';
import { hasHorizontalScroll, openApp } from './helpers';

const WIDTHS = [320, 2560] as const;

for (const width of WIDTHS) {
  test.describe(`viewport ${width} px`, () => {
    test.use({ viewport: { width, height: width === 320 ? 640 : 1440 } });

    test('map dashboard has no horizontal scroll', async ({ page }) => {
      await openApp(page);
      await expect(page.getByTestId('disaster-selector')).toContainText('sim-flood-loop');
      await expect(page.getByRole('banner')).toBeVisible();
      await expect(page.getByRole('button', { name: /Report hazard/i }).first()).toBeVisible();
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
      await page.getByRole('link', { name: 'Demo console' }).click();
      await expect(page.getByTestId('scenario-flood_loop')).toBeVisible();
      await expect(page.getByTestId('actor-panel')).toBeVisible();
      expect(await hasHorizontalScroll(page)).toMatchObject({ overflow: false });
    });
  });
}
