import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Incident, RouteResponse } from './services/api';
import type { RealtimeEvent } from './services/eventStore';
import { initialEventState } from './services/eventStore';
import type { RealtimeSnapshot } from './services/realtimeController';

// ---------------------------------------------------------------- mocks
const h = vi.hoisted(() => ({
  incidents: [] as unknown[],
  demoMode: false,
  calc: [] as unknown[],
  calcCalls: [] as unknown[],
  snapshot: null as unknown,
  onEvent: null as null | ((e: unknown) => void),
}));

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>();
  return {
    ...actual,
    ensureSession: vi.fn(async () => 'token'),
    listIncidents: vi.fn(async () => h.incidents),
    demoStatus: vi.fn(async () => ({ demo_mode: h.demoMode })),
    getMe: vi.fn(async () => ({ subject: 'guest-1', role: 'guest', is_guest: true, display_name: 'Heron-12', expires_at: null })),
    listReports: vi.fn(async () => ({ reports: [] })),
    listMessages: vi.fn(async () => ({ messages: [] })),
    listCheckins: vi.fn(async () => ({ checkins: [] })),
    calculateRoute: vi.fn(async (body: unknown) => {
      h.calcCalls.push(body);
      const next = h.calc.length > 1 ? h.calc.shift() : h.calc[0];
      return next;
    }),
  };
});

vi.mock('./hooks/useRealtime', () => ({
  useRealtime: (_id: string | null, onEvent?: (e: unknown) => void) => {
    h.onEvent = onEvent ?? null;
    return h.snapshot;
  },
}));

vi.mock('./maps/MapView', () => ({
  MapView: ({ layerInputs }: { layerInputs?: { routes?: unknown[] } }) => (
    <div data-testid="map-stub" data-routes={layerInputs?.routes?.length ?? 0} />
  ),
}));

import App from './App';

// ---------------------------------------------------------------- fixtures
const incident = (over: Partial<Incident> = {}): Incident => ({
  incident_id: 'flood-1',
  incident_type: 'flood',
  simulation_flag: false,
  status: 'active',
  source: 'test',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

const route = (id: string, distance: number) => ({
  route_id: id,
  route_type: 'shortest',
  label: 'algorithm-generated',
  distance_meters: distance,
  estimated_duration_seconds: distance / 1.3,
  geometry: { type: 'LineString', coordinates: [[-87.63, 41.88], [-87.62, 41.89]] },
  uncertainty_level: 'Low',
  confidence_limitations: [],
  hazard_flags: [],
  reason_summary: 'Shortest path avoiding closures.',
  restrictions_considered: [],
  excluded_edges_count: 0,
  sources: [],
  edge_penalties: [],
});

const response = (over: Record<string, unknown> = {}, calculatedAt = '2026-01-01T00:00:00.000001Z'): RouteResponse =>
  ({
    policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
    routes: [route('r1', 1000)],
    notices: [],
    explanation: 'Route options are available.',
    label: 'algorithm-generated',
    sources: [],
    missing_data: [],
    data_timestamp: '2026-01-01T00:00:00Z',
    calculated_at: calculatedAt,
    ...over,
  }) as unknown as RouteResponse;

const snapshot = (over: Partial<RealtimeSnapshot> = {}): RealtimeSnapshot => ({
  status: 'connected',
  lastSyncedAt: Date.UTC(2026, 0, 1, 12, 0, 0),
  stale: false,
  store: initialEventState,
  ...over,
});

beforeEach(() => {
  h.incidents = [incident()];
  h.demoMode = false;
  h.calc = [response()];
  h.calcCalls = [];
  h.snapshot = snapshot();
  h.onEvent = null;
  window.location.hash = '';
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}), text: async () => '' })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

async function loaded() {
  render(<App />);
  await screen.findByText(/Active incident/);
  await waitFor(() => expect(screen.queryByText('Loading incidents…')).toBeNull());
}

/** Opens a tab through the Tab_List (wide) or Tab_Bar (compact); jsdom's default width renders the Tab_List. */
async function openTab(name: 'Map' | 'Route' | 'Community' | 'Safety' | 'More') {
  const primary = await screen.findByRole('navigation', { name: 'Primary' });
  await userEvent.click(within(primary).getByRole('button', { name: new RegExp(`^${name}\\b`) }));
}

async function useSamplePoints() {
  await openTab('Route');
  await userEvent.click(await screen.findByRole('button', { name: /use sample points/i }));
}

// ---------------------------------------------------------------- tests
describe('App shell', () => {
  it('renders the disclaimer, 911 guidance, and the Chicago header', async () => {
    await loaded();
    expect(screen.getByTestId('disclaimer-banner')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /call 911/i })).toBeInTheDocument();
    const banner = screen.getByRole('banner');
    expect(within(banner).getByRole('heading', { level: 1, name: 'SafePath' })).toBeInTheDocument();
    expect(within(banner).getByTestId('header-city')).toHaveTextContent('Chicago');
    expect(within(banner).getByTestId('connectivity-status')).toHaveTextContent('Connected');
    expect(within(banner).queryByTestId('demo-mode-indicator')).toBeNull();
    expect(within(banner).queryByTestId('simulated-badge')).toBeNull();
  });

  it('has a skip link that targets the main landmark', async () => {
    await loaded();
    const skip = screen.getByRole('link', { name: /skip to main content/i });
    expect(skip).toHaveAttribute('href', '#main-content');
    expect(document.getElementById('main-content')?.tagName).toBe('MAIN');
  });

  it('shows offline status, last sync time, and the stale warning in the header', async () => {
    h.snapshot = snapshot({ status: 'offline', stale: true });
    await loaded();
    const banner = screen.getByRole('banner');
    expect(within(banner).getByTestId('connectivity-status')).toHaveTextContent('Offline');
    expect(within(banner).getByTestId('last-synced')).toHaveTextContent(/Last synced: \d/);
    expect(within(banner).getByTestId('stale-indicator')).toHaveTextContent('Road conditions may be out of date');
  });

  it('shows reconnecting with text, not color alone', async () => {
    h.snapshot = snapshot({ status: 'reconnecting', lastSyncedAt: null });
    await loaded();
    expect(screen.getByTestId('connectivity-status')).toHaveTextContent('Reconnecting');
    expect(screen.getByTestId('last-synced')).toHaveTextContent('not yet');
  });

  it('shows SIMULATED in the header for a simulated incident, on the map and on every screen', async () => {
    h.incidents = [incident({ incident_id: 'sim-flood-loop', simulation_flag: true })];
    await loaded();
    expect(within(screen.getByRole('banner')).getByTestId('simulated-badge')).toBeInTheDocument();
    await openTab('Route');
    await userEvent.click(screen.getByRole('button', { name: 'Route details' }));
    expect(await screen.findByRole('dialog', { name: 'Route details' })).toBeInTheDocument();
    expect(within(screen.getByRole('banner')).getByTestId('simulated-badge')).toBeInTheDocument();
    expect(screen.getByTestId('disclaimer-banner')).toBeVisible();
  });

  it('shows the demo-mode indicator and selects the flood scenario by default in demo mode', async () => {
    h.demoMode = true;
    h.incidents = [
      incident({ incident_id: 'real-tornado', incident_type: 'tornado' }),
      incident({ incident_id: 'sim-flood-loop', simulation_flag: true }),
    ];
    render(<App />);
    expect(await screen.findByTestId('demo-mode-indicator')).toHaveTextContent('DEMO MODE');
    const active = await screen.findByTestId('active-incident');
    await waitFor(() => expect(within(active).getByText('Flood')).toBeInTheDocument());
    expect(within(active).getByTestId('simulated-badge')).toBeInTheDocument();
    await openTab('More');
    expect(screen.getByRole('link', { name: 'Demo console' })).toHaveAttribute('href', '#/demo');
  });

  it('opens the demo console from More with a "Back to More" control that returns to More', async () => {
    h.demoMode = true;
    render(<App />);
    await screen.findByTestId('demo-mode-indicator');
    await openTab('More');
    await userEvent.click(await screen.findByRole('link', { name: 'Demo console' }));
    expect(await screen.findByTestId('demo-route')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Back to More' }));
    await waitFor(() => expect(window.location.hash).toBe('#/tab/more'));
    expect(screen.queryByTestId('demo-route')).toBeNull();
  });

  it('selects the first active incident outside demo mode', async () => {
    h.incidents = [
      incident({ incident_id: 'old', status: 'closed' }),
      incident({ incident_id: 'first-active', incident_type: 'fire' }),
    ];
    render(<App />);
    const active = await screen.findByTestId('active-incident');
    await waitFor(() => expect(within(active).getByText('Fire')).toBeInTheDocument());
  });

  it('calculates a route for the chosen points and draws it on the map', async () => {
    await loaded();
    expect(screen.getByTestId('map-stub')).toHaveAttribute('data-routes', '0');
    await useSamplePoints();
    expect(await screen.findByTestId('route-item')).toBeInTheDocument();
    expect(screen.getByTestId('map-stub')).toHaveAttribute('data-routes', '1');
    expect(h.calcCalls[0]).toMatchObject({ incident_id: 'flood-1', travel_mode: 'walking', mobility_profile: 'standard' });
  });

  it('shows the shelter-in-place panel and draws no route', async () => {
    h.calc = [
      response({
        policy_outcome: 'SHELTER_IN_PLACE',
        routes: [],
        explanation: 'Take shelter now.',
      }),
    ];
    await loaded();
    await useSamplePoints();
    expect(await screen.findByTestId('shelter-in-place')).toBeInTheDocument();
    expect(screen.getByTestId('shelter-note')).toHaveTextContent('no route is shown');
    expect(screen.getByTestId('map-stub')).toHaveAttribute('data-routes', '0');
    expect(screen.queryByTestId('route-item')).toBeNull();
  });

  it('recalculates automatically on a restriction event and explains the change', async () => {
    h.calc = [
      response({ routes: [route('r1', 1000)] }, '2026-01-01T00:00:00.000001Z'),
      response({ routes: [route('r1', 1800)], explanation: 'Rerouted.' }, '2026-01-01T00:00:05.000001Z'),
    ];
    await loaded();
    await useSamplePoints();
    await screen.findByTestId('route-item');
    expect(h.calcCalls).toHaveLength(1);

    const event: RealtimeEvent = {
      event_id: 'e-1',
      type: 'restriction_added',
      incident_id: 'flood-1',
      payload: { restriction_id: 'sim-closure-1', kind: 'closure', edge_ids: [], simulated: true },
      created_at: '2026-01-01T00:00:03Z',
    };
    act(() => h.onEvent?.(event));
    await waitFor(() => expect(h.calcCalls).toHaveLength(2), { timeout: 4000 });
    expect(await screen.findByTestId('route-change', undefined, { timeout: 4000 })).toHaveTextContent(/Distance \+800 m/);
  });

  it('opens a screen from the screens navigation and closes it with Escape', async () => {
    await loaded();
    const dashboard = screen.getByTestId('dashboard');
    await openTab('More');
    await userEvent.click(screen.getByRole('link', { name: 'Incident overview' }));
    const dialog = await screen.findByRole('dialog', { name: 'Incident overview' });
    // The screen is pushed inside the frame: the Tab_List and the Disclaimer_Banner stay visible.
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeVisible();
    expect(screen.getByTestId('disclaimer-banner')).toBeVisible();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(dialog).not.toBeInTheDocument();
    expect(dashboard).toBeVisible();
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeVisible();
  });

  it('shows "Back to More" on a screen opened from More and returns to More', async () => {
    await loaded();
    await openTab('More');
    await userEvent.click(screen.getByRole('link', { name: 'Data sources and limitations' }));
    const dialog = await screen.findByRole('dialog', { name: 'Data sources and limitations' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Back to More' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(window.location.hash).toBe('#/tab/more');
  });

  it('opens screens from the bottom controls', async () => {
    await loaded();
    await userEvent.click(screen.getByRole('button', { name: 'Mark safe' }));
    expect(await screen.findByRole('dialog', { name: 'Safety check-in' })).toBeInTheDocument();
  });

  it('opens the data sources screen at #/sources', async () => {
    window.location.hash = '#/sources';
    render(<App />);
    expect(await screen.findByRole('dialog', { name: 'Data sources and limitations' })).toBeInTheDocument();
  });

  it('opens the profile menu with role and alias', async () => {
    await loaded();
    await userEvent.click(await screen.findByRole('button', { name: /Profile/ }));
    const panel = await screen.findByTestId('profile-panel');
    await waitFor(() => expect(panel).toHaveTextContent('Heron-12'));
    expect(panel).toHaveTextContent('Guest');
    expect(panel).toHaveTextContent('guest');
  });
});

describe('App accessibility structure', () => {
  it('has labelled landmarks and one h1, and every control has an accessible name', async () => {
    await loaded();
    expect(screen.getAllByRole('banner')).toHaveLength(1);
    expect(screen.getAllByRole('main')).toHaveLength(1);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    for (const nav of screen.getAllByRole('navigation')) expect(nav).toHaveAccessibleName();
    // The panel and the map area are named regions; the old complementary landmarks are gone.
    expect(screen.getByRole('region', { name: 'SafePath panel' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Map area' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    for (const region of screen.getAllByRole('region')) expect(region).toHaveAccessibleName();
    for (const el of [
      ...screen.getAllByRole('button'),
      ...screen.getAllByRole('link'),
      ...screen.queryAllByRole('textbox'),
      ...screen.queryAllByRole('combobox'),
      ...screen.queryAllByRole('checkbox'),
      ...screen.queryAllByRole('switch'),
    ]) {
      expect(el).toHaveAccessibleName();
    }
  });

  it('never relies on color alone for connectivity status', async () => {
    h.snapshot = snapshot({ status: 'offline' });
    await loaded();
    const status = screen.getByTestId('connectivity-status');
    expect(status.querySelector('svg')).not.toBeNull();
    expect(status).toHaveTextContent('Offline');
  });
});
