/**
 * Scenario 3, blocked stairwell with two browser windows (docs/demo-script.md). SIMULATED.
 *
 * Window 1 (presenter, its own browser context): Demo console as the demo moderator; drives users A and C.
 * Window 2 (SIMULATED User B, a second browser context via ?demoUser=B): the main app on the
 * building incident, opened and selected before Start (the order the demo script requires).
 *
 * Checks: B sees A's report within 5 s without reload (Req 39.2, 46.4); B corroborates and C
 * corroborates, the status reads "not officially verified" (39.3); the moderator confirms and B's
 * indoor view shows the confirmed closure and an updated indoor route within 5 s (39.4, 46.5);
 * the presenter's A/B/C route boxes name the new closure (39.4); two windows throughout (39.6).
 *
 * Only one tab is mounted at a time, so window 2 switches tabs: Map for the indoor view, Community for
 * the report card and corroboration, and back to Map before the moderator confirms so the 5 s closure
 * timing is measured against a mounted indoor view.
 */
import { expect, test } from '@playwright/test';
import { LIVE_UPDATE_MS, newWindow, openApp, openConsoleAsModerator, openTab, resetAll } from './helpers';

const INCIDENT = 'sim-blocked-stairwell';
const CORROBORATED = 'Corroborated by community \u2014 not officially verified';

test('Scenario 3: report, corroborate, moderator confirm and indoor reroute across two windows', async ({ browser }) => {
  const presenter = await newWindow(browser);
  const userB = await newWindow(browser);
  try {
    const p = presenter.page;
    const b = userB.page;

    // ---------------------------------------------------------------- window 1: clean state
    await openApp(p);
    await openConsoleAsModerator(p);
    await resetAll(p);

    // ---------------------------------------------------------------- window 2: user B on the building incident
    await openApp(b, '/?demoUser=B');
    await expect(b.getByRole('button', { name: /Profile: SIMULATED User B/ })).toBeVisible();
    await b.getByTestId('disaster-selector').getByRole('button', { name: /Building emergency/ }).click();
    await b.getByTestId('disaster-selector').getByRole('button', { name: new RegExp(INCIDENT) }).click();
    const indoor = b.getByTestId('indoor-view');
    await expect(indoor).toBeVisible();
    await expect(indoor).toContainText('SIMULATED');
    await expect(indoor.getByRole('tab')).toHaveText(['L1', 'L2', 'L3']);
    const stairA = indoor.getByTestId('indoor-status-list').locator('li', { hasText: 'Stairwell A:' });
    await expect(stairA).toContainText('No reports');
    await openTab(b, 'Community');
    const reports = b.getByTestId('right-sidebar').getByTestId('panel-reports');
    await expect(reports).toContainText('No community reports yet.');

    // ---------------------------------------------------------------- window 1: start the scenario
    const card = p.getByTestId('scenario-blocked_stairwell');
    await card.getByRole('button', { name: 'Start' }).click();
    await expect(card.getByTestId('message-blocked_stairwell')).toContainText('Started SIMULATED: Blocked stairwell');
    const actors = p.getByTestId('actor-panel');

    // ---------------------------------------------------------------- A reports; B sees it within 5 s
    const reportCard = reports.getByTestId('report-card').first();
    const t0 = Date.now();
    await actors.getByRole('button', { name: 'A reports Stairwell A blocked' }).click();
    await expect(reportCard).toBeVisible({ timeout: LIVE_UPDATE_MS });
    const seenMs = Date.now() - t0;
    test.info().annotations.push({ type: 'report visible in window 2 (ms)', description: String(seenMs) });
    expect(seenMs).toBeLessThanOrEqual(LIVE_UPDATE_MS);
    await expect(actors.getByTestId('result-a-report')).toContainText('Unverified');
    await expect(reportCard).toContainText('Unverified');
    await expect(reportCard).toContainText('SIMULATED');
    await openTab(b, 'Map');
    await expect(stairA).toContainText('Unverified', { timeout: LIVE_UPDATE_MS });
    await expect(b).toHaveURL(/demoUser=B/); // no reload happened

    // ---------------------------------------------------------------- B and C corroborate
    await openTab(b, 'Community');
    await reportCard.getByRole('button', { name: 'Corroborate', exact: true }).click();
    await expect(reportCard).toContainText('Your response was recorded.');
    await actors.getByRole('button', { name: 'C corroborates the report' }).click();
    await expect(actors.getByTestId('result-c-corroborate')).toContainText(/Corroborated/);
    await expect(actors.getByTestId('result-c-corroborate')).toContainText(/not officially verified/i);
    await expect(reportCard).toContainText(CORROBORATED, { timeout: LIVE_UPDATE_MS });
    await expect(reportCard).not.toContainText('Official/Moderator-confirmed');

    // ---------------------------------------------------------------- moderator confirms; indoor reroute within 5 s
    await openTab(b, 'Map');
    const steps = indoor.getByTestId('indoor-route');
    await expect(steps.locator('li').first()).toBeVisible({ timeout: LIVE_UPDATE_MS });
    const stepsBefore = await steps.locator('ol').innerText();
    const t1 = Date.now();
    await actors.getByRole('button', { name: 'Moderator confirms SIMULATED closure' }).click();
    await expect(stairA).toContainText('Closure confirmed', { timeout: LIVE_UPDATE_MS });
    const closedMs = Date.now() - t1;
    test.info().annotations.push({ type: 'closure visible in window 2 indoor view (ms)', description: String(closedMs) });
    await expect(actors.getByTestId('result-m-confirm')).toContainText('Officially_Verified');
    await expect(steps.locator('ol')).not.toContainText(/Stairwell A\b/, { timeout: LIVE_UPDATE_MS });
    expect(Date.now() - t1).toBeLessThanOrEqual(LIVE_UPDATE_MS + 1_000);
    test.info().annotations.push({ type: 'indoor steps before confirm', description: stepsBefore });

    // ---------------------------------------------------------------- presenter: routes for A, B and C
    for (const who of ['A', 'B', 'C']) {
      await expect(card.getByText(`Route for user ${who} (SIMULATED)`)).toBeVisible({ timeout: LIVE_UPDATE_MS });
    }
    await expect(card.getByTestId('route-change').first()).toContainText(/is now avoided/);

    // ---------------------------------------------------------------- B asks for help (other app users only)
    await actors.getByRole('button', { name: 'B sends assistance request' }).click();
    await expect(actors.getByTestId('result-b-message')).toContainText('not delivered to any real responder');
  } finally {
    await userB.context.close();
    await presenter.context.close();
  }
});
