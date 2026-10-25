/**
 * Appearance and pointer-density checks in a real browser (task 14.4).
 *
 *  - Req 5.4   the stored/system Appearance is on the root element before the first paint;
 *  - Req 12.3  keyboard focus ring is at least 2 px wide and has at least 3:1 contrast in both Appearances;
 *  - Req 9.1   Coarse_Pointer: every interactive control has a Touch_Target of at least 44 x 44 CSS px;
 *  - Req 9.8   Fine_Pointer: every interactive control has a Touch_Target of at least 32 x 32 CSS px;
 *  - Req 5.8, 5.9  offline basemap: with the PMTiles file cached and the browser offline, switching Appearance
 *                  issues no tile request and keeps the OpenStreetMap attribution visible.
 *
 * Offline basemap step (needs a PMTiles fixture AND an app configured to use it). The Playwright config does not
 * set these, so the step skips itself with the reason unless the run is started like this:
 *
 *   VITE_PMTILES_URL=http://127.0.0.1:4599/chicago.pmtiles VITE_ENABLE_SW=true npx playwright test appearance-and-density
 *
 * The Vite dev server inherits both variables. The spec serves the fixture
 * (safepath/data/processed/tiles/chicago.pmtiles) itself, with Range and CORS support, on the URL's loopback port,
 * and counts the requests that really reach it, which is how "no tile network request" is proven.
 */
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import { openApp, openTab } from './helpers';

const here = path.dirname(fileURLToPath(import.meta.url));
const TAB_NAMES = ['Map', 'Route', 'Community', 'Safety', 'More'] as const;
const STORAGE_KEY = 'safepath.appearance';

type Scheme = 'dark' | 'light';
const SCHEMES: readonly Scheme[] = ['dark', 'light'];

// ---------------------------------------------------------------------------------------------------------------
// Req 5.4: Appearance is applied before the first paint
// ---------------------------------------------------------------------------------------------------------------
test.describe('Appearance is applied before first paint (Req 5.4)', () => {
  for (const scheme of SCHEMES) {
    test(`System follows prefers-color-scheme: ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await recordAppearanceAtFirstPaint(page);
      await page.goto('/');
      await page.waitForFunction(() => (window as unknown as PaintProbe).__firstPaint !== undefined);
      const atFirstPaint = await page.evaluate(() => (window as unknown as PaintProbe).__firstPaint);
      expect(atFirstPaint).toEqual({ appearance: scheme, colorScheme: scheme });
      await expect(page.getByTestId('dashboard')).toBeVisible();
      await expect(page.locator('html')).toHaveAttribute('data-appearance', scheme);
    });

    test(`a stored ${scheme} choice wins over the opposite system scheme`, async ({ page }) => {
      const opposite: Scheme = scheme === 'dark' ? 'light' : 'dark';
      await page.emulateMedia({ colorScheme: opposite });
      await page.addInitScript(
        ([key, value]) => window.localStorage.setItem(key, value),
        [STORAGE_KEY, scheme] as const,
      );
      await recordAppearanceAtFirstPaint(page);
      await page.goto('/');
      await page.waitForFunction(() => (window as unknown as PaintProbe).__firstPaint !== undefined);
      const atFirstPaint = await page.evaluate(() => (window as unknown as PaintProbe).__firstPaint);
      expect(atFirstPaint).toEqual({ appearance: scheme, colorScheme: scheme });
    });

    test(`the early script alone sets ${scheme} (app bundle blocked)`, async ({ page }) => {
      // Proves the attribute comes from the blocking script in <head>, not from React after mount.
      await page.emulateMedia({ colorScheme: scheme });
      await page.route('**/src/main.tsx*', (route) => route.abort());
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('html')).toHaveAttribute('data-appearance', scheme);
      await expect(page.getByTestId('dashboard')).toHaveCount(0);
    });
  }

  test('the root attribute follows a system change without a reload (Req 5.2)', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await openApp(page);
    await expect(page.locator('html')).toHaveAttribute('data-appearance', 'dark');
    await page.emulateMedia({ colorScheme: 'light' });
    await expect(page.locator('html')).toHaveAttribute('data-appearance', 'light');
  });
});

interface PaintProbe {
  __firstPaint?: { appearance: string | null; colorScheme: string };
}

/** Before any page script runs, record the root attribute at the moment the browser reports its first paint. */
async function recordAppearanceAtFirstPaint(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as PaintProbe;
    const observer = new PerformanceObserver((list) => {
      if (w.__firstPaint !== undefined || list.getEntries().length === 0) return;
      const root = document.documentElement;
      w.__firstPaint = { appearance: root.getAttribute('data-appearance'), colorScheme: root.style.colorScheme };
    });
    observer.observe({ type: 'paint', buffered: true });
  });
}

// ---------------------------------------------------------------------------------------------------------------
// Req 12.3: focus ring width and contrast in both Appearances
// ---------------------------------------------------------------------------------------------------------------
test.describe('Focus ring (Req 12.3)', () => {
  for (const scheme of SCHEMES) {
    test(`is at least 2 px wide with at least 3:1 contrast in ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await openApp(page);
      await expect(page.locator('html')).toHaveAttribute('data-appearance', scheme);

      // Reach controls with the keyboard so :focus-visible applies; include a Tab_List button explicitly.
      const checked: FocusRing[] = [];
      for (let i = 0; i < 6; i += 1) {
        await page.keyboard.press('Tab');
        const ring = await readFocusRing(page);
        if (ring) checked.push(ring);
      }
      const tab = page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: /^Route\b/ });
      await tab.focus();
      const tabRing = await readFocusRing(page);
      expect(tabRing, 'Primary tab shows :focus-visible after keyboard use').not.toBeNull();
      checked.push(tabRing as FocusRing);
      // Map_View overlay control (Req 16.8): MapLibre's own focus glow is replaced by the token ring.
      await page.keyboard.press('Tab'); // make sure the keyboard modality is active
      await page.getByRole('button', { name: 'Zoom in' }).focus();
      const zoomRing = await readFocusRing(page);
      expect(zoomRing, 'Zoom in shows :focus-visible after keyboard use').not.toBeNull();
      checked.push(zoomRing as FocusRing);

      expect(checked.length).toBeGreaterThanOrEqual(2);
      for (const ring of checked) {
        expect(ring.style, `${ring.target} outline style`).not.toBe('none');
        expect(ring.widthPx, `${ring.target} outline width`).toBeGreaterThanOrEqual(2);
        expect(ring.contrast, `${ring.target} ring ${ring.ring} on ${ring.background}`).toBeGreaterThanOrEqual(3);
      }
    });
  }
});

interface FocusRing {
  target: string;
  style: string;
  widthPx: number;
  ring: string;
  background: string;
  contrast: number;
}

/** Describe the focus indicator of the focused element, or null when it is not :focus-visible. */
async function readFocusRing(page: Page): Promise<FocusRing | null> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body || !el.matches(':focus-visible')) return null;

    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
    type Rgba = [number, number, number, number];
    const parse = (css: string): Rgba => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000';
      ctx.fillStyle = css;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2], d[3] / 255];
    };
    const over = (top: Rgba, bottom: [number, number, number]): [number, number, number] => [
      top[0] * top[3] + bottom[0] * (1 - top[3]),
      top[1] * top[3] + bottom[1] * (1 - top[3]),
      top[2] * top[3] + bottom[2] * (1 - top[3]),
    ];
    const lum = (c: [number, number, number]) => {
      const f = (v: number) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
    };

    // Background the ring is drawn on: the parent chain composited over the page canvas (offset ring sits outside the element).
    const chain: Rgba[] = [];
    for (let n: HTMLElement | null = el.parentElement; n; n = n.parentElement) {
      chain.push(parse(getComputedStyle(n).backgroundColor));
    }
    let bg: [number, number, number] = [255, 255, 255];
    for (let i = chain.length - 1; i >= 0; i -= 1) bg = over(chain[i], bg);

    const cs = getComputedStyle(el);
    // The indicator is the outline, or, where a control replaces the outline with a Tailwind `ring`, the widest
    // solid (zero blur, zero offset, not inset) box-shadow ring. Controls with neither report style "none".
    let style = cs.outlineStyle;
    let widthPx = style === 'none' ? 0 : Number.parseFloat(cs.outlineWidth);
    let color = cs.outlineColor;
    if (style === 'none' || widthPx === 0) {
      const shadow = /(rgba?\([^)]*\)|#[0-9a-f]{3,8})\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px\s+(-?[\d.]+)px(\s+inset)?/gi;
      for (const m of cs.boxShadow.matchAll(shadow)) {
        const [, c, x, y, blur, spread, inset] = m;
        if (inset || Number(x) !== 0 || Number(y) !== 0 || Number(blur) !== 0) continue;
        if (Number(spread) > widthPx) {
          widthPx = Number(spread);
          color = c;
          style = 'solid';
        }
      }
    }
    const ring = over(parse(color), bg);
    const [l1, l2] = [lum(ring), lum(bg)].sort((a, b) => b - a);
    const rgb = (c: [number, number, number]) => `rgb(${c.map(Math.round).join(', ')})`;
    return {
      target: `${el.tagName.toLowerCase()}${el.getAttribute('aria-label') ? `[${el.getAttribute('aria-label')}]` : ''}${el.textContent ? ` "${el.textContent.trim().slice(0, 30)}"` : ''}`,
      style,
      widthPx,
      ring: rgb(ring),
      background: rgb(bg),
      contrast: (l1 + 0.05) / (l2 + 0.05),
    };
  });
}

// ---------------------------------------------------------------------------------------------------------------
// Req 9.1 / 9.8: Touch_Target sizes under coarse and fine pointer emulation
// ---------------------------------------------------------------------------------------------------------------
const CONTROL_SELECTOR = [
  'button',
  'a[href]',
  'input:not([type="hidden"])',
  'select',
  'textarea',
  'summary',
  '[role="button"]',
  '[role="switch"]',
  '[role="radio"]',
  '[role="tab"]',
  '[role="link"]',
].join(',');

interface TargetReport {
  checked: number;
  tooSmall: string[];
}

/**
 * Measure every visible interactive control in the page. Not measured: the off-screen skip link, visually hidden
 * (clipped to a pixel) native inputs whose visible label is itself an interactive control, and links that sit inline in
 * a line of running text (inline links are exempt from target-size rules because their size is set by the text).
 */
async function measureTargets(page: Page, minPx: number): Promise<TargetReport> {
  return page.evaluate(
    ([selector, min]) => {
      const tooSmall: string[] = [];
      let checked = 0;
      for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
        if (el.classList.contains('skip-link')) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || el.closest('[hidden],[inert]')) continue;
        if ((el as HTMLButtonElement).disabled) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        if (r.width <= 1 && r.height <= 1) continue; // visually hidden native control
        if (el.tagName === 'A' && cs.display === 'inline') continue; // inline link in running text
        checked += 1;
        if (r.width < min - 0.5 || r.height < min - 0.5) {
          const label = el.getAttribute('aria-label') || el.textContent?.trim().slice(0, 30) || el.getAttribute('data-testid') || '';
          tooSmall.push(`${el.tagName.toLowerCase()} "${label}" ${r.width.toFixed(1)}x${r.height.toFixed(1)}`);
        }
      }
      return { checked, tooSmall };
    },
    [CONTROL_SELECTOR, minPx] as const,
  );
}

async function measureAllTabs(page: Page, minPx: number): Promise<{ checked: number; tooSmall: string[] }> {
  let checked = 0;
  const tooSmall: string[] = [];
  for (const name of TAB_NAMES) {
    await openTab(page, name);
    // Let the tab content settle (lists render after the tab's own requests).
    await page.waitForTimeout(300);
    const report = await measureTargets(page, minPx);
    checked += report.checked;
    tooSmall.push(...report.tooSmall.map((s) => `[${name}] ${s}`));
  }
  return { checked, tooSmall: [...new Set(tooSmall)] };
}

test.describe('Touch_Target sizes', () => {
  test.describe('Coarse_Pointer (44 px)', () => {
    test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

    test('every interactive control is at least 44 x 44 CSS px (Req 9.1)', async ({ page }) => {
      await openApp(page);
      expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
      expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--target-min').trim())).toBe('44px');
      const { checked, tooSmall } = await measureAllTabs(page, 44);
      expect(checked).toBeGreaterThan(10);
      expect(tooSmall, `controls under 44 px (of ${checked} measured)`).toEqual([]);
    });
  });

  test.describe('Fine_Pointer (32 px)', () => {
    test.use({ viewport: { width: 1440, height: 900 } });

    test('every interactive control is at least 32 x 32 CSS px (Req 9.8)', async ({ page }) => {
      await openApp(page);
      expect(await page.evaluate(() => matchMedia('(pointer: fine)').matches)).toBe(true);
      expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--target-min').trim())).toBe('32px');
      const { checked, tooSmall } = await measureAllTabs(page, 32);
      expect(checked).toBeGreaterThan(10);
      expect(tooSmall, `controls under 32 px (of ${checked} measured)`).toEqual([]);
    });
  });
});

// ---------------------------------------------------------------------------------------------------------------
// Req 5.8 / 5.9: offline basemap
// ---------------------------------------------------------------------------------------------------------------
const FIXTURE = path.resolve(here, '../../data/processed/tiles/chicago.pmtiles');
const PMTILES_URL = process.env.VITE_PMTILES_URL?.trim() || undefined;

/** Why the offline step cannot run here, or undefined when it can. */
function offlineSkipReason(): string | undefined {
  if (!fs.existsSync(FIXTURE)) return `no PMTiles fixture at ${FIXTURE}`;
  if (!PMTILES_URL) {
    return 'the app is not configured with a PMTiles basemap: start Playwright with VITE_PMTILES_URL=http://127.0.0.1:4599/chicago.pmtiles (and VITE_ENABLE_SW=true)';
  }
  if (process.env.VITE_ENABLE_SW !== 'true') {
    return 'the service worker is off in the dev server (set VITE_ENABLE_SW=true), so the PMTiles file cannot be cached';
  }
  let url: URL;
  try {
    url = new URL(PMTILES_URL);
  } catch {
    return `VITE_PMTILES_URL "${PMTILES_URL}" is not an absolute URL`;
  }
  if (!['127.0.0.1', 'localhost'].includes(url.hostname) || url.protocol !== 'http:' || !url.port) {
    return `VITE_PMTILES_URL "${PMTILES_URL}" is not a loopback http URL with a port, so this spec cannot serve the fixture on it`;
  }
  return undefined;
}

/** Serve the fixture with Range + CORS and count the requests that reach it. */
function serveFixture(port: number): Promise<{ hits: () => number; close: () => Promise<void> }> {
  const size = fs.statSync(FIXTURE).size;
  let hits = 0;
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Range',
    'Access-Control-Expose-Headers': 'Content-Range, Content-Length, Accept-Ranges',
    'Accept-Ranges': 'bytes',
  };
  const server = http.createServer((req, res) => {
    if (req.method === 'OPTIONS') {
      res.writeHead(204, cors).end();
      return;
    }
    hits += 1;
    const m = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range ?? ''));
    let start = 0;
    let end = size - 1;
    let status = 200;
    if (m && (m[1] || m[2])) {
      if (m[1] === '') start = Math.max(0, size - Number(m[2]));
      else {
        start = Number(m[1]);
        if (m[2]) end = Math.min(Number(m[2]), size - 1);
      }
      status = 206;
    }
    const headers: Record<string, string | number> = {
      ...cors,
      'Content-Type': 'application/octet-stream',
      'Content-Length': end - start + 1,
    };
    if (status === 206) headers['Content-Range'] = `bytes ${start}-${end}/${size}`;
    res.writeHead(status, headers);
    if (req.method === 'HEAD') res.end();
    else fs.createReadStream(FIXTURE, { start, end }).pipe(res);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () =>
      resolve({
        hits: () => hits,
        close: () => new Promise<void>((done) => server.close(() => done())),
      }),
    );
  });
}

test.describe('Offline basemap (Req 5.8, 5.9)', () => {
  test('Appearance switch offline issues no tile request and keeps the attribution', async ({ page, context }) => {
    const skipReason = offlineSkipReason();
    test.skip(skipReason !== undefined, `Offline basemap step skipped: ${skipReason}`);

    const url = new URL(PMTILES_URL as string);
    const fixture = await serveFixture(Number(url.port));
    const osmRequests: string[] = [];
    page.on('request', (r) => {
      if (/tile\.openstreetmap\.org/.test(r.url())) osmRequests.push(r.url());
    });
    try {
      await page.emulateMedia({ colorScheme: 'dark' });
      await openApp(page);
      await expect(page.getByTestId('osm-attribution')).toBeVisible();

      // 1. Cache the PMTiles file through the service worker (the opt-in download path).
      const registered = await page.evaluate(async () => {
        if (!('serviceWorker' in navigator)) return false;
        const reg = await Promise.race([
          navigator.serviceWorker.ready,
          new Promise<null>((resolve) => setTimeout(() => resolve(null), 15_000)),
        ]);
        return reg !== null;
      });
      test.skip(!registered, 'Offline basemap step skipped: the service worker did not register (is VITE_ENABLE_SW=true reaching the dev server?)');
      await page.evaluate((u) => {
        void navigator.serviceWorker.ready.then((reg) => reg.active?.postMessage({ type: 'CACHE_PMTILES', url: u }));
      }, PMTILES_URL as string);
      await expect
        .poll(() => page.evaluate(async (u) => Boolean(await caches.match(u)), PMTILES_URL as string), { timeout: 30_000 })
        .toBe(true);

      // 2. Reload so the page is controlled by the worker, then wait for the map to be ready.
      await page.reload();
      await expect(page.getByTestId('dashboard')).toBeVisible();
      await expect(page.getByTestId('osm-attribution')).toBeVisible();
      await page.waitForTimeout(1_500); // initial tile reads settle

      // 3. Go offline, then switch Appearance.
      await context.setOffline(true);
      const hitsBefore = fixture.hits();
      const osmBefore = osmRequests.length;
      await openTab(page, 'More');
      const group = page.getByRole('radiogroup', { name: 'Appearance' });
      await group.getByRole('radio', { name: 'Light' }).click();
      await expect(page.locator('html')).toHaveAttribute('data-appearance', 'light');
      await group.getByRole('radio', { name: 'Dark' }).click();
      await expect(page.locator('html')).toHaveAttribute('data-appearance', 'dark');
      await page.waitForTimeout(1_000);

      // 4. No tile request reached the network in either Appearance; attribution is still visible.
      expect(fixture.hits() - hitsBefore, 'requests that reached the PMTiles server while offline').toBe(0);
      expect(osmRequests.length - osmBefore, 'OSM tile requests while offline').toBe(0);
      await openTab(page, 'Map');
      await expect(page.getByTestId('osm-attribution')).toBeVisible();
      await expect(page.getByTestId('osm-attribution')).toContainText('OpenStreetMap');

      // 5. The cached file still answers Range reads while offline (what the map uses to draw in both Appearances).
      const offlineRead = await page.evaluate(async (u) => {
        const res = await fetch(u, { headers: { Range: 'bytes=0-15' } });
        return { status: res.status, source: res.headers.get('x-sp-source'), bytes: (await res.arrayBuffer()).byteLength };
      }, PMTILES_URL as string);
      expect(offlineRead).toEqual({ status: 206, source: 'cache', bytes: 16 });
      expect(fixture.hits() - hitsBefore).toBe(0);
    } finally {
      await context.setOffline(false).catch(() => undefined);
      await fixture.close();
    }
  });
});
