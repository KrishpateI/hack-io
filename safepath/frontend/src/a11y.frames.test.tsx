import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import axe from 'axe-core';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BottomSheet } from './components/ios/BottomSheet';
import { InspectorPanel } from './components/ios/InspectorPanel';
import { stageLayout, type Detent, type PanelMode } from './components/ios/layout';
import { SidePanel } from './components/ios/SidePanel';
import { TabBar } from './components/ios/TabBar';
import { TabList } from './components/ios/TabList';
import { TAB_IDS, TAB_TITLES } from './components/shell/navigation';
import { SCREEN_IDS, SCREEN_TITLES, screenHash } from './components/shell/screens';
import type { Incident } from './services/api';
import { initialEventState } from './services/eventStore';
import type { RealtimeSnapshot } from './services/realtimeController';
import { DISCLAIMER_TEXT } from './styles/tokens';

/**
 * Accessibility coverage for the iOS-style frames (task 12.1): the main layout at each tier,
 * Tab_List, Tab_Bar, Bottom_Sheet at every Detent, Side_Panel in every mode, Inspector_Panel, and
 * every pushed screen, each in both Appearances.
 * Validates: Requirements 3.1, 3.2, 12.4, 12.5, 12.6, 14.7.
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
vi.setConfig({ testTimeout: 30_000 });

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
const APPEARANCES: AppearanceName[] = ['light', 'dark'];

/** Applies an Appearance the way `applyAppearance` does: the attribute on the root element. */
function setAppearance(appearance: AppearanceName) {
  document.documentElement.setAttribute('data-appearance', appearance);
}

beforeEach(() => {
  h.incidents = [incident()];
  h.snapshot = snapshot();
  window.location.hash = '';
  window.localStorage.clear();
  setViewport(1024);
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}), text: async () => '' })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
  document.documentElement.removeAttribute('data-appearance');
});

async function axeViolations(root: Element) {
  // jsdom has no layout or computed Tailwind colors, so contrast is checked separately against
  // the palette tokens (styles/tokens.test.ts); every other rule runs.
  const res = await axe.run(root, { rules: { 'color-contrast': { enabled: false } } });
  return res.violations.map((v) => `${v.id}: ${v.help} -> ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`);
}

async function renderPage(hash: string, ready: () => Promise<unknown>) {
  window.location.hash = hash;
  const utils = render(<App />);
  await ready();
  await waitFor(() => expect(screen.queryByText('Loading incidents…')).toBeNull());
  await within(screen.getByRole('banner')).findByTestId('simulated-badge');
  return utils;
}

const mapReady = () => screen.findByTestId('active-incident');

/** One `main`, the skip link, and a Disclaimer_Banner without any dismiss control (Req 3.1, 3.2, 12.6). */
function expectPageInvariants() {
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.getByRole('link', { name: /skip to main content/i })).toBeInTheDocument();
  const banner = screen.getByTestId('disclaimer-banner');
  expect(banner.textContent).toBe(DISCLAIMER_TEXT);
  expect(banner).toBeVisible();
  expect(within(banner).queryByRole('button')).toBeNull();
  expect(screen.queryByRole('button', { name: /dismiss|hide disclaimer/i })).toBeNull();
}

// ---------------------------------------------------------------- main layout per tier
const TIERS: [string, number][] = [
  ['compact', 320],
  ['wide', 1024],
  ['desktop', 1440],
];

describe.each(APPEARANCES)('main layout, %s appearance', (appearance) => {
  it.each(TIERS)('has no axe-core violations at the %s tier (%i px)', async (tier, width) => {
    setAppearance(appearance);
    setViewport(width);
    const { container } = await renderPage('', mapReady);
    expect(document.documentElement).toHaveAttribute('data-appearance', appearance);
    expect(screen.getByTestId('dashboard')).toHaveAttribute('data-viewport-tier', tier);
    expectPageInvariants();
    expect(await axeViolations(container)).toEqual([]);
  });

  // Bottom_Sheet at every Detent inside the composed app (compact tier). Requirement 3.2, 12.5.
  it('has no axe-core violations with the Bottom_Sheet at Collapsed, Half, and Full', async () => {
    setAppearance(appearance);
    setViewport(320);
    const user = userEvent.setup();
    const { container } = await renderPage('', mapReady);
    const sheet = screen.getByRole('region', { name: 'SafePath panel' });
    const seen: string[] = [];
    for (let i = 0; i < 3; i++) {
      seen.push(sheet.getAttribute('data-detent') ?? '');
      expectPageInvariants();
      expect(await axeViolations(container)).toEqual([]);
      await user.click(screen.getByRole('button', { name: /^Resize panel/ }));
    }
    // Cycling from any start visits all three Detents.
    expect(new Set(seen)).toEqual(new Set(['collapsed', 'half', 'full']));
  });
});

// ---------------------------------------------------------------- every pushed screen
describe.each(APPEARANCES)('pushed screens, %s appearance', (appearance) => {
  describe.each(SCREEN_IDS)('%s', (id) => {
    it.each([
      ['compact', 320],
      ['desktop', 1440],
    ])('has no axe-core violations at the %s tier', async (_tier, width) => {
      setAppearance(appearance);
      setViewport(width);
      const { container } = await renderPage(screenHash(id), () =>
        screen.findByRole('dialog', { name: SCREEN_TITLES[id] }),
      );
      expectPageInvariants();
      expect(await axeViolations(container)).toEqual([]);
    });
  });
});

// ---------------------------------------------------------------- standalone frame components
/** The pieces render inside a `main`, as they do in the app, so page-level landmark rules apply. */
function inMain(children: ReactNode) {
  return <main>{children}</main>;
}

const NAV_TABS = TAB_IDS.map((id) => ({ id, label: TAB_TITLES[id], icon: id }));
const noop = () => undefined;

describe.each(APPEARANCES)('standalone frame components, %s appearance', (appearance) => {
  beforeEach(() => setAppearance(appearance));

  it('Tab_List has no axe-core violations (full and rail)', async () => {
    const { container } = render(
      inMain(
        <>
          <TabList tabs={NAV_TABS} current="map" onSelect={noop} />
        </>,
      ),
    );
    expect(await axeViolations(container)).toEqual([]);
  });

  it('Tab_List rail mode keeps an accessible name for every tab', async () => {
    const { container } = render(inMain(<TabList tabs={NAV_TABS} current="route" onSelect={noop} rail />));
    for (const id of TAB_IDS) expect(screen.getByRole('button', { name: TAB_TITLES[id] })).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
  });

  it('Tab_Bar has no axe-core violations, including with an "Updated" badge', async () => {
    const tabs = NAV_TABS.map((t) => (t.id === 'route' ? { ...t, badge: 'Updated' } : t));
    const { container } = render(inMain(<TabBar tabs={tabs} current="safety" onSelect={noop} />));
    expect(await axeViolations(container)).toEqual([]);
  });

  it.each<Detent>(['collapsed', 'half', 'full'])('Bottom_Sheet at %s has no axe-core violations', async (detent) => {
    const { container } = render(
      inMain(
        <BottomSheet detent={detent} onDetentChange={noop} available={700}>
          <h2>Panel</h2>
          <button type="button">Inside sheet</button>
        </BottomSheet>,
      ),
    );
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toHaveAttribute('data-detent', detent);
    expect(screen.getByRole('button', { name: `Resize panel, ${detent}` })).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
  });

  it.each<PanelMode>(['default', 'expanded', 'collapsed'])('Side_Panel in %s mode has no axe-core violations', async (mode) => {
    const layout = stageLayout(1600, mode, false);
    const { container } = render(
      inMain(
        <SidePanel
          mode={mode}
          onModeChange={noop}
          layout={layout}
          nav={<TabList tabs={NAV_TABS} current="map" onSelect={noop} rail={mode === 'collapsed'} />}
        >
          <h2>Panel</h2>
          <button type="button">Inside panel</button>
        </SidePanel>,
      ),
    );
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toHaveAttribute('data-mode', mode);
    expect(await axeViolations(container)).toEqual([]);
  });

  it('Side_Panel with Expand disabled (no room) has no axe-core violations', async () => {
    const layout = stageLayout(900, 'default', false);
    expect(layout.canExpand).toBe(false);
    const { container } = render(
      inMain(
        <SidePanel mode="default" onModeChange={noop} layout={layout}>
          <p>Body</p>
        </SidePanel>,
      ),
    );
    expect(screen.getByRole('button', { name: 'Expand panel' })).toBeDisabled();
    expect(await axeViolations(container)).toEqual([]);
  });

  it('Inspector_Panel has no axe-core violations', async () => {
    const { container } = render(
      inMain(
        <>
          <div data-focus-target="map" role="region" aria-label="Map area" />
          <InspectorPanel open title="Lamp post" onClose={noop}>
            <p>Details</p>
          </InspectorPanel>
        </>,
      ),
    );
    expect(screen.getByRole('region', { name: 'Map feature details' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Close map feature details' })).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
  });
});
