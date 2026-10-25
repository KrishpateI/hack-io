import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReportStatusBadge, CORROBORATED_TEXT } from './components/community/ReportStatusBadge';
import { ConnectivityStatus, type ConnectivityState } from './components/shell/ConnectivityStatus';
import { SCREEN_IDS, SCREEN_TITLES, screenHash } from './components/shell/screens';
import { StatusBadge, type Status } from './components/StatusBadge';
import type { Incident } from './services/api';
import { initialEventState } from './services/eventStore';
import type { RealtimeSnapshot } from './services/realtimeController';
import { DISCLAIMER_TEXT } from './styles/tokens';

/**
 * Cross-screen accessibility and safety-UI checks for the composed app (task 13.9).
 * Validates: Requirements 1.1, 1.2, 8.10, 24.8, 25.5, 34.7, 36.2, 36.3, 36.4.
 * Narrower component checks live next to their components (labels, classification, community,
 * staleBanner.integration, SourcesLimitations, inspect); this file covers the whole shell.
 */

// ---------------------------------------------------------------- mocks (network, map, realtime)
const h = vi.hoisted(() => ({
  incidents: [] as unknown[],
  snapshot: null as unknown,
}));

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>();
  return {
    ...actual,
    ensureSession: vi.fn(async () => 'token'),
    listIncidents: vi.fn(async () => h.incidents),
    demoStatus: vi.fn(async () => ({ demo_mode: false })),
    getMe: vi.fn(async () => ({ subject: 'guest-1', role: 'guest', is_guest: true, display_name: 'Heron-12', expires_at: null })),
    listReports: vi.fn(async () => ({ reports: [] })),
    listMessages: vi.fn(async () => ({ messages: [] })),
    listCheckins: vi.fn(async () => ({ checkins: [] })),
    calculateRoute: vi.fn(async () => null),
  };
});

vi.mock('./hooks/useRealtime', () => ({ useRealtime: () => h.snapshot }));

// MapLibre needs WebGL; the map itself is covered by MapView/layers tests and Playwright.
vi.mock('./maps/MapView', () => ({ MapView: () => <div data-testid="map-stub" role="img" aria-label="Map" /> }));

import App from './App';

// Each test renders the whole shell and axe walks the full DOM; allow headroom on slow machines.
vi.setConfig({ testTimeout: 20_000 });

const incident = (over: Partial<Incident> = {}): Incident => ({
  incident_id: 'sim-flood-loop',
  incident_type: 'flood',
  simulation_flag: true,
  status: 'active',
  source: 'demo',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

const snapshot = (over: Partial<RealtimeSnapshot> = {}): RealtimeSnapshot => ({
  status: 'connected',
  lastSyncedAt: Date.UTC(2026, 0, 1, 12, 0, 0),
  stale: false,
  store: initialEventState,
  ...over,
});

function setViewport(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
  window.dispatchEvent(new Event('resize'));
}

type AppearanceName = 'light' | 'dark';

/** Applies an Appearance the way `applyAppearance` does: the attribute on the root element. */
function setAppearance(appearance: AppearanceName) {
  document.documentElement.setAttribute('data-appearance', appearance);
}

beforeEach(() => {
  h.incidents = [incident()];
  h.snapshot = snapshot();
  window.location.hash = '';
  setViewport(1024);
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}), text: async () => '' })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
  document.documentElement.removeAttribute('data-appearance');
});

/** Every page the user can land on: the map, each screen, and the demo console. */
const PAGES: { name: string; hash: string; ready: () => Promise<unknown> }[] = [
  { name: 'main map', hash: '', ready: () => screen.findByTestId('active-incident') },
  ...SCREEN_IDS.map((id) => ({
    name: SCREEN_TITLES[id],
    hash: screenHash(id),
    ready: () => screen.findByRole('dialog', { name: SCREEN_TITLES[id] }),
  })),
  { name: 'demo console', hash: '#/demo', ready: () => screen.findByTestId('demo-route') },
];

async function renderPage(hash: string, ready: () => Promise<unknown>) {
  window.location.hash = hash;
  const utils = render(<App />);
  await ready();
  await waitFor(() => expect(screen.queryByText('Loading incidents…')).toBeNull());
  // The header SIMULATED badge appears once the incident list resolves.
  await within(screen.getByRole('banner')).findByTestId('simulated-badge');
  return utils;
}

// ---------------------------------------------------------------- layout helpers
const NAMED_PX: Record<string, number> = {
  '3xs': 256, '2xs': 288, xs: 320, sm: 384, md: 448, lg: 512, xl: 576,
  '2xl': 672, '3xl': 768, '4xl': 896, '5xl': 1024, '6xl': 1152, '7xl': 1280,
};
const toPx = (n: number, unit: string) => (unit === 'rem' ? n * 16 : n);

/**
 * Smallest width (px) a class forces on its element when it applies at every viewport,
 * i.e. unprefixed width/min-width/basis/grid-track utilities. Responsive (`lg:` etc.) classes
 * are ignored because they do not apply at 320 px. Returns 0 for non-fixed classes.
 */
function forcedWidthPx(token: string): number {
  if (token.includes(':')) return 0;
  const t = token.replace(/!$/, '');
  let m = /^(?:min-w|w|basis)-\[(\d+(?:\.\d+)?)(px|rem)\]$/.exec(t);
  if (m) return toPx(Number(m[1]), m[2]);
  m = /^(?:min-w|w)-(\d+(?:\.\d+)?)$/.exec(t);
  if (m) return Number(m[1]) * 4;
  m = /^(?:min-w|w)-(3xs|2xs|xs|sm|md|lg|xl|[2-7]xl)$/.exec(t);
  if (m) return NAMED_PX[m[1]];
  m = /^grid-cols-\[(.+)\]$/.exec(t);
  if (m) {
    // Sum only fixed tracks; flexible tracks (fr, minmax(0,...), auto) can shrink.
    return m[1].split('_').reduce((sum, track) => {
      const f = /^(\d+(?:\.\d+)?)(px|rem)$/.exec(track);
      return sum + (f ? toPx(Number(f[1]), f[2]) : 0);
    }, 0);
  }
  return 0;
}

function overflowingElements(root: HTMLElement, viewport: number): string[] {
  const out: string[] = [];
  for (const el of [root, ...root.querySelectorAll<HTMLElement>('*')]) {
    if (el.closest('svg')) continue; // icon sizes are tiny and intrinsic
    const cls = typeof el.className === 'string' ? el.className : '';
    for (const token of cls.split(/\s+/).filter(Boolean)) {
      if (forcedWidthPx(token) > viewport) out.push(`<${el.tagName.toLowerCase()} class="${cls}"> (${token})`);
    }
    for (const prop of ['width', 'minWidth'] as const) {
      const v = /^(\d+(?:\.\d+)?)(px|rem)$/.exec(el.style[prop]);
      if (v && toPx(Number(v[1]), v[2]) > viewport) out.push(`<${el.tagName.toLowerCase()} style="${prop}:${el.style[prop]}">`);
    }
  }
  return out;
}

async function axeViolations(root: Element) {
  // jsdom has no layout or computed Tailwind colors, so contrast is checked separately against
  // the palette tokens (styles/tokens.test.ts); every other rule runs.
  const res = await axe.run(root, { rules: { 'color-contrast': { enabled: false } } });
  return res.violations.map((v) => `${v.id}: ${v.help} -> ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

// ---------------------------------------------------------------- every page
describe.each(PAGES)('page: $name', ({ hash, ready }) => {
  it.each([320, 768, 1280, 2560])('at %i px shows the verbatim disclaimer, SIMULATED header, no dismiss control, and no forced overflow', async (width) => {
    setViewport(width);
    const { container } = await renderPage(hash, ready);
    const banner = screen.getByTestId('disclaimer-banner');
    expect(banner.textContent).toBe(DISCLAIMER_TEXT);
    expect(banner).toBeVisible();
    expect(screen.getAllByTestId('disclaimer-banner')).toHaveLength(1);
    expect(within(banner).queryByRole('button')).toBeNull();
    expect(screen.queryByRole('button', { name: /dismiss|hide disclaimer/i })).toBeNull();
    expect(within(screen.getByRole('banner')).getByTestId('simulated-badge')).toHaveTextContent('SIMULATED');
    expect(overflowingElements(container as HTMLElement, width)).toEqual([]);
  });

  it('reaches the 911 guidance in one click, separate from community features', async () => {
    await renderPage(hash, ready);
    const panel = screen.getByTestId('emergency-911');
    expect(screen.queryByTestId('emergency-911-body')).toBeNull();
    await userEvent.click(within(panel).getByRole('button', { name: /emergency: call 911/i }));
    expect(within(panel).getByTestId('emergency-911-body')).toHaveTextContent(/call 911/i);
    // Visually separate: not nested inside main content (messaging, check-in, reports).
    expect(screen.getByRole('main')).not.toContainElement(panel);
  });

  // Compact, Wide, and Desktop widths in both Appearances (Requirements 14.7, 12.6).
  describe.each(['light', 'dark'] as const)('%s appearance', (appearance) => {
    it.each([320, 1024, 1440])('has no axe-core violations at %i px', async (width) => {
      setAppearance(appearance);
      setViewport(width);
      const { container } = await renderPage(hash, ready);
      expect(document.documentElement).toHaveAttribute('data-appearance', appearance);
      // The skip link stays, and the page has a single main landmark (Requirement 12.6).
      expect(screen.getByRole('link', { name: /skip to main content/i })).toBeInTheDocument();
      expect(screen.getAllByRole('main')).toHaveLength(1);
      expect(await axeViolations(container)).toEqual([]);
    });
  });
});

// ---------------------------------------------------------------- keyboard operation
describe('keyboard operation', () => {
  it('opens a screen, closes it with Escape, and opens 911 guidance using only the keyboard', async () => {
    const user = userEvent.setup();
    await renderPage('', () => screen.findByTestId('active-incident'));

    // The first Tab stop is the skip link to the main landmark.
    await user.tab();
    const skip = screen.getByRole('link', { name: /skip to main content/i });
    expect(skip).toHaveFocus();

    // Reach the More tab with the keyboard shortcut "g" then "5" (Requirement 16.1).
    await user.keyboard('g5');
    const more = await screen.findByTestId('more-tab');

    // Tab forward until the "Incident overview" row link has focus, then activate with Enter.
    const link = within(more).getByRole('link', { name: 'Incident overview' });
    for (let i = 0; i < 60 && document.activeElement !== link; i++) await user.tab();
    expect(link).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('dialog', { name: 'Incident overview' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // The 911 control lives in the header, outside main. From the document start it is reachable
    // within the first ten Tab stops after the skip link, and it toggles with Enter and Space.
    const call = screen.getByRole('button', { name: /emergency: call 911/i });
    expect(screen.getByRole('main')).not.toContainElement(call);
    expect(screen.getByRole('banner')).toContainElement(call);
    (document.activeElement as HTMLElement | null)?.blur();
    await user.tab();
    expect(skip).toHaveFocus();
    let stops = 0;
    while (stops < 10 && document.activeElement !== call) {
      await user.tab();
      stops++;
    }
    expect(call).toHaveFocus();
    expect(stops).toBeLessThanOrEqual(10);
    await user.keyboard('{Enter}');
    expect(call).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('emergency-911-body')).toBeInTheDocument();
    await user.keyboard(' ');
    expect(call).toHaveAttribute('aria-expanded', 'false');
  }, 30_000);

  it('keeps every focusable control in the tab order with an accessible name', async () => {
    const { container } = await renderPage('', () => screen.findByTestId('active-incident'));
    const focusables = container.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]');
    expect(focusables.length).toBeGreaterThan(10);
    for (const el of focusables) {
      if (el.id === 'main-content') continue; // programmatic skip-link target (tabindex -1)
      if (el.hasAttribute('disabled')) continue;
      expect(el).toHaveAccessibleName();
      // Programmatic focus targets (the Map area region, tab headings) are focused by code and by
      // pointer only, so they are not tab stops; they still need an accessible name (checked above).
      if (el.hasAttribute('data-focus-target')) continue;
      // Roving tabindex: inactive tabs are reached with the arrow keys (WAI-ARIA tabs pattern).
      if (el.getAttribute('role') === 'tab' && el.getAttribute('aria-selected') !== 'true') continue;
      expect(el.tabIndex, el.outerHTML.slice(0, 120)).toBeGreaterThanOrEqual(0);
    }
  });

  // The global `:focus-visible { outline: 3px solid }` rule lives in styles/tailwind.css (vitest does
  // not load CSS, so the rendered ring is checked in a real browser); here no control may opt out of it.
  it('never suppresses the focus outline without a replacement focus style', async () => {
    const { container } = await renderPage('', () => screen.findByTestId('active-incident'));
    const suppressed = [...container.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea')].filter((el) => {
      const cls = el.className.split(/\s+/);
      const removes = cls.some((c) => /^(focus(-visible)?:)?outline-(none|hidden|0)$/.test(c));
      const replaces = cls.some((c) => /^focus(-visible)?:(outline-\d|outline-\[|ring)/.test(c));
      return removes && !replaces;
    });
    expect(suppressed.map((el) => el.outerHTML.slice(0, 120))).toEqual([]);
  });
});

// ---------------------------------------------------------------- stale banner in the composed header
describe('stale-data banner in the shell', () => {
  it('shows the warning with the last update time while the channel is disconnected', async () => {
    h.snapshot = snapshot({ status: 'reconnecting', stale: true });
    await renderPage('', () => screen.findByTestId('active-incident'));
    const header = screen.getByRole('banner');
    const stale = within(header).getByTestId('stale-indicator');
    expect(stale).toHaveAttribute('role', 'alert');
    expect(stale).toHaveTextContent('Road conditions may be out of date');
    expect(stale.querySelector('svg')).not.toBeNull();
    expect(within(header).getByTestId('last-synced')).toHaveTextContent(/Last synced: \d/);
  });
});

// ---------------------------------------------------------------- status chips (text + icon + color)
describe('status chips convey status with text and an icon, not color alone', () => {
  const STATUS: Status[] = ['verified', 'unverified', 'closed', 'unknown'];
  it.each(STATUS)('StatusBadge %s', (status) => {
    render(<StatusBadge status={status} />);
    const chip = screen.getByTestId('status-badge');
    expect(chip.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(chip.textContent?.trim().length).toBeGreaterThan(0);
  });

  const VERIFICATION = ['Unverified', 'Corroborated', 'Officially_Verified', 'Disputed', 'Resolved', 'Expired', 'bogus'];
  it.each(VERIFICATION)('ReportStatusBadge %s', (status) => {
    render(<ReportStatusBadge status={status} />);
    const chip = screen.getByTestId('report-status');
    expect(chip.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(chip.textContent?.trim().length).toBeGreaterThan(0);
  });

  it('every verification status has a distinct label, and Corroborated uses the exact wording', () => {
    const texts = VERIFICATION.slice(0, 6).map((s) => {
      const { unmount } = render(<ReportStatusBadge status={s} />);
      const t = screen.getByTestId('report-status').textContent;
      unmount();
      return t;
    });
    expect(new Set(texts).size).toBe(texts.length);
    expect(texts[1]).toBe(CORROBORATED_TEXT);
    expect(CORROBORATED_TEXT).toBe('Corroborated by community \u2014 not officially verified');
  });

  const CONN: ConnectivityState[] = ['connected', 'reconnecting', 'offline', 'idle'];
  it('connectivity states each have a distinct text label and an icon', () => {
    const labels = CONN.map((status) => {
      const { unmount } = render(<ConnectivityStatus status={status} lastSyncedAt={null} stale={false} />);
      const chip = screen.getByTestId('connectivity-status');
      expect(chip.querySelector('svg')).not.toBeNull();
      const t = chip.textContent;
      unmount();
      return t;
    });
    expect(new Set(labels).size).toBe(CONN.length);
  });

  it('the simulated chip and status chips have no axe-core violations', async () => {
    const { container } = render(
      <div>
        {STATUS.map((s) => <StatusBadge key={s} status={s} />)}
        {VERIFICATION.map((s) => <ReportStatusBadge key={s} status={s} simulated />)}
      </div>,
    );
    expect(await axeViolations(container)).toEqual([]);
  });
});

// ---------------------------------------------------------------- helper self-check
describe('forcedWidthPx', () => {
  it('flags only unprefixed fixed widths', () => {
    expect(forcedWidthPx('w-96')).toBe(384);
    expect(forcedWidthPx('min-w-[320px]')).toBe(320);
    expect(forcedWidthPx('w-[30rem]')).toBe(480);
    expect(forcedWidthPx('max-w-xs')).toBe(0);
    expect(forcedWidthPx('w-sm')).toBe(384);
    expect(forcedWidthPx('lg:w-96')).toBe(0);
    expect(forcedWidthPx('w-full!')).toBe(0);
    expect(forcedWidthPx('grid-cols-[20rem_minmax(0,1fr)_24rem]')).toBe(704);
  });
});
