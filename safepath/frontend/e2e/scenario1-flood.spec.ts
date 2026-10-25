/**
 * Scenario 1, Chicago Loop flooding (docs/demo-script.md). SIMULATED.
 *
 * Presenter window: Demo console -> moderator demo session -> Reset all -> Start -> five steps.
 * Viewer window: the main map routed between the scenario start and the SIMULATION destination.
 * After the SIMULATED verified closure, both windows must show the updated route with an
 * explanation within 5 s (Req 37.5, 44.11, 46.5).
 */
import { expect, test } from '@playwright/test';
import { LIVE_UPDATE_MS, newWindow, openApp, openConsoleAsModerator, openTab, resetAll, timeUntilVisible } from './helpers';

const ORIGIN = { lat: '41.8882', lng: '-87.628' }; // SIMULATED start near the Riverwalk (scenarios.json)
const DESTINATION = { lat: '41.8827', lng: '-87.6233' }; // SIMULATION destination, not an official shelter

test('Scenario 1: flood closure changes the route and the explanation within 5 s', async ({ page, browser }) => {
  // ---------------------------------------------------------------- presenter: console setup
  await openApp(page);
  await expect(page.getByTestId('active-incident')).toContainText('Flood');
  await expect(page.getByTestId('disaster-selector')).toContainText('sim-flood-loop');
  await openConsoleAsModerator(page);
  await resetAll(page);

  const card = page.getByTestId('scenario-flood_loop');
  await expect(card).toContainText('SIMULATED: Chicago Loop flooding');
  const message = card.getByTestId('message-flood_loop');
  const next = card.getByRole('button', { name: 'Next step' });

  await card.getByRole('button', { name: 'Start' }).click();
  await expect(message).toContainText('Started SIMULATED: Chicago Loop flooding');
  await expect(card.getByTestId('error-flood_loop')).toHaveCount(0);

  await next.click(); // 1 activate
  await expect(message).toContainText('client action: open incident');
  await next.click(); // 2 highlight
  await expect(message).toContainText('client action: show layer');
  await next.click(); // 3 route
  await expect(message).toContainText('client action: calculate routes');
  const comparison = card.getByTestId('route-comparison');
  await expect(comparison).toContainText('Route (SIMULATED)');
  await expect(comparison).toContainText(/0 restrictions considered/);
  await expect(comparison).toContainText(/algorithm-generated/i);

  // ---------------------------------------------------------------- viewer: main map route
  const viewer = await newWindow(browser);
  try {
    const v = viewer.page;
    await openApp(v);
    await expect(v.getByTestId('disaster-selector')).toContainText('sim-flood-loop');
    await openTab(v, 'Route');
    await v.locator('#start-lat').fill(ORIGIN.lat);
    await v.locator('#start-lng').fill(ORIGIN.lng);
    await v.locator('#start-lng').blur();
    await v.locator('#dest-lat').fill(DESTINATION.lat);
    await v.locator('#dest-lng').fill(DESTINATION.lng);
    await v.locator('#dest-lng').blur();
    const panel = v.getByTestId('route-panel');
    await expect(panel).toContainText(/algorithm-generated/i, { timeout: 15_000 });

    // ---------------------------------------------------------------- closure + reroute
    const viewerChange = v.getByTestId('route-change-banner').getByTestId('route-change');
    const closureMs = await timeUntilVisible(
      () => next.click(), // 4 SIMULATED verified closure
      viewerChange,
      LIVE_UPDATE_MS,
    );
    await expect(viewerChange).toContainText(/is now avoided/);
    test.info().annotations.push({ type: 'viewer route change after closure (ms)', description: String(closureMs) });

    const change = card.getByTestId('route-change');
    const rerouteMs = await timeUntilVisible(() => next.click(), change, LIVE_UPDATE_MS); // 5 recalculate
    await expect(change).toContainText(/New .*sim-flood-closure-1.* is now avoided/);
    await expect(comparison.getByText('After')).toBeVisible();
    await expect(comparison).toContainText(/1 restriction considered/);
    test.info().annotations.push({ type: 'console reroute explanation (ms)', description: String(rerouteMs) });

    await page.getByRole('button', { name: 'Back to More' }).click();
    await expect(page.getByTestId('dashboard')).toBeVisible();
  } finally {
    await viewer.context.close();
  }
});
