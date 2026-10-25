import { expect, type Browser, type BrowserContext, type Locator, type Page } from '@playwright/test';

/** Requirement time limits for live updates (Req 37.5, 39.2, 39.4, 46.4). */
export const LIVE_UPDATE_MS = 5_000;

/** Open the app and wait until the shell has a session and knows demo mode is on. */
export async function openApp(page: Page, path = '/'): Promise<void> {
  watch(page);
  await page.goto(path);
  await expect(page.getByTestId('dashboard')).toBeVisible();
  await expect(page.getByText('DEMO MODE', { exact: false }).first()).toBeVisible();
  await expect(page.getByTestId('session-status')).toHaveCount(0);
}

/** Open the Demo console in this page, take a moderator demo session and reset every scenario. */
export async function openConsoleAsModerator(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Demo console' }).click();
  await expect(page.getByTestId('demo-route')).toBeVisible();
  await page.getByRole('button', { name: 'Get a moderator demo session' }).click();
  await expect(page.getByTestId('session-identity')).toContainText(/role moderator/i);
}

export async function resetAll(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Reset all' }).click();
  await expect(page.getByTestId('reset-all-message')).toBeVisible();
}

/** Click a button and resolve once `target` is visible; returns the elapsed milliseconds. */
export async function timeUntilVisible(click: () => Promise<void>, target: Locator, limitMs: number): Promise<number> {
  const t0 = Date.now();
  await click();
  await expect(target).toBeVisible({ timeout: limitMs });
  return Date.now() - t0;
}

/** A fresh browser context = a separate browser window with its own sessionStorage and session. */
export async function newWindow(browser: Browser): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  return { context, page };
}

/** True when the document is wider than the viewport (horizontal scrolling). */
export async function hasHorizontalScroll(page: Page): Promise<{ scrollWidth: number; clientWidth: number; overflow: boolean }> {
  return page.evaluate(() => {
    const el = document.documentElement;
    const scrollWidth = Math.max(el.scrollWidth, document.body.scrollWidth);
    return { scrollWidth, clientWidth: el.clientWidth, overflow: scrollWidth > el.clientWidth + 1 };
  });
}

const watched = new WeakSet<Page>();
/** Print page errors, console errors and failed API responses so a failing run explains itself. */
export function watch(page: Page): void {
  if (watched.has(page)) return;
  watched.add(page);
  const tag = () => `[page ${new URL(page.url() || 'http://x/').search || '/'}]`;
  page.on('pageerror', (e) => console.log(`${tag()} pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') console.log(`${tag()} console.error: ${m.text()}`);
  });
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().includes('/api/')) console.log(`${tag()} HTTP ${r.status()} ${r.request().method()} ${r.url()}`);
  });
}
