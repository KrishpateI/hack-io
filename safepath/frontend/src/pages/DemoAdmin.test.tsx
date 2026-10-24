import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../services/api';
import type { RealtimeClient, RealtimeHandlers } from '../services/realtimeClients';
import { bootstrapDemoUser, parseDemoUser, secondWindowUrl } from '../components/demo/demoSession';
import { actResultText, sortScenarios } from '../components/demo/demoModel';
import DemoAdmin from './DemoAdmin';

vi.mock('../services/api', async (orig) => {
  const real = await orig<typeof import('../services/api')>();
  return {
    ...real,
    demoStatus: vi.fn(),
    demoCreateSession: vi.fn(),
    demoListScenarios: vi.fn(),
    demoStart: vi.fn(),
    demoApplyStep: vi.fn(),
    demoAdvance: vi.fn(),
    demoAct: vi.fn(),
    demoReset: vi.fn(),
    demoResetAll: vi.fn(),
    demoAddClosure: vi.fn(),
    demoRemoveClosure: vi.fn(),
    calculateRoute: vi.fn(),
  };
});

const m = vi.mocked(api);

function fakeClient() {
  let handlers: RealtimeHandlers | null = null;
  const client: RealtimeClient = {
    name: 'fake',
    connect: (_id, h) => {
      handlers = h;
    },
    close: () => {},
  };
  return { client, handlers: () => handlers };
}

const scenario = (id: string, extra: Record<string, unknown> = {}) => ({
  scenario_id: id,
  name: id.replace(/_/g, ' '),
  incident_id: `inc-${id}`,
  seeded: true,
  pristine: true,
  next_step: 's1',
  graph: { used: 'simulated_fixture' },
  steps: [
    { id: 's1', title: 'Activate incident', kind: 'activate_incident', status: 'pending' },
    { id: 's2', title: 'Show closure layer', kind: 'show_layer', status: 'applied' },
  ],
  ...extra,
});

const LIST = {
  scenarios: [
    scenario('tornado_sample', { optional: true }),
    scenario('blocked_stairwell'),
    scenario('earthquake_debris'),
    scenario('flood_loop'),
  ],
};

function renderPage() {
  const fc = fakeClient();
  render(<DemoAdmin makeRealtimeClient={() => fc.client} loadBuilding={() => Promise.reject(new Error('none'))} />);
  return fc;
}

beforeEach(() => {
  vi.resetAllMocks();
  m.demoStatus.mockResolvedValue({ demo_mode: true, graph_used: 'simulated_fixture', moderator_sessions_available: true });
  m.demoListScenarios.mockResolvedValue(LIST);
});

describe('DemoAdmin visibility', () => {
  it('hides every control when demo mode is off, but keeps the SIMULATED banner', async () => {
    m.demoStatus.mockResolvedValue({ demo_mode: false });
    renderPage();
    expect(await screen.findByTestId('demo-off')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument();
    expect(m.demoListScenarios).not.toHaveBeenCalled();
    expect(screen.getByTestId('demo-banner')).toHaveTextContent('SIMULATED');
    expect(screen.getByTestId('demo-banner')).toHaveTextContent('Demo only, not real events');
  });

  it('keeps the banner and shows the API error when status fails', async () => {
    m.demoStatus.mockRejectedValue(new api.ApiError(500, 'internal', 'boom'));
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('internal (HTTP 500): boom');
    expect(screen.getByTestId('demo-banner')).toBeInTheDocument();
    expect(screen.getByTestId('simulated-badge')).toBeInTheDocument();
  });
});

describe('DemoAdmin scenarios', () => {
  it('lists the three main scenarios first, then samples, with step status text', async () => {
    renderPage();
    await screen.findByTestId('scenario-flood_loop');
    const order = screen.getAllByRole('article').map((a) => a.getAttribute('data-testid'));
    expect(order).toEqual(['scenario-flood_loop', 'scenario-earthquake_debris', 'scenario-blocked_stairwell', 'scenario-tornado_sample']);
    expect(screen.getByTestId('step-status-flood_loop-s1')).toHaveTextContent('Pending');
    expect(screen.getByTestId('step-status-flood_loop-s2')).toHaveTextContent('Applied');
    expect(screen.getByTestId('graph-used')).toHaveTextContent('SIMULATED fixture graph');
  });

  it('starts, applies a step, advances and refreshes the list', async () => {
    m.demoStart.mockResolvedValue({ incident_id: 'inc-flood_loop' });
    m.demoApplyStep.mockResolvedValue({ step_id: 's1', applied: true, client_action: null });
    m.demoAdvance.mockResolvedValue({ step_id: 's1', applied: true, client_action: null });
    renderPage();
    const card = within(await screen.findByTestId('scenario-flood_loop'));
    await userEvent.click(card.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(m.demoStart).toHaveBeenCalledWith('flood_loop'));
    await userEvent.click(card.getByRole('button', { name: /Apply this step: Activate incident/ }));
    await waitFor(() => expect(m.demoApplyStep).toHaveBeenCalledWith('flood_loop', 's1'));
    await userEvent.click(card.getByRole('button', { name: 'Next step' }));
    await waitFor(() => expect(m.demoAdvance).toHaveBeenCalledWith('flood_loop'));
    expect(m.demoListScenarios.mock.calls.length).toBeGreaterThan(1);
  });

  it('runs calculate_routes client actions and shows a summary', async () => {
    const route = {
      route_type: 'disaster-adjusted',
      distance_meters: 2500,
      estimated_duration_seconds: 1800,
      geometry: { type: 'LineString', coordinates: [] },
      edge_penalties: [],
      restrictions_considered: [],
    };
    m.demoApplyStep.mockResolvedValue({
      step_id: 's1',
      applied: false,
      client_action: { do: 'calculate_routes', body: { incident_id: 'inc-flood_loop' } },
    });
    m.calculateRoute.mockResolvedValue({
      policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
      routes: [route],
      explanation: '',
      label: 'algorithm-generated',
    } as never);
    renderPage();
    const card = within(await screen.findByTestId('scenario-flood_loop'));
    await userEvent.click(card.getByRole('button', { name: /Apply this step: Activate incident/ }));
    await waitFor(() => expect(m.calculateRoute).toHaveBeenCalledWith({ incident_id: 'inc-flood_loop' }));
    expect(await card.findByTestId('route-comparison')).toHaveTextContent('2.50 km');
  });

  it('shows the already-pristine no-op message and the timed reset result', async () => {
    m.demoReset.mockResolvedValueOnce({ changed: false }).mockResolvedValueOnce({ changed: true, elapsed_ms: 1200, within_limit: true });
    renderPage();
    const card = within(await screen.findByTestId('scenario-flood_loop'));
    await userEvent.click(card.getByRole('button', { name: 'Reset scenario' }));
    expect(await card.findByTestId('message-flood_loop')).toHaveTextContent('already pristine');
    await userEvent.click(card.getByRole('button', { name: 'Reset scenario' }));
    await waitFor(() => expect(card.getByTestId('message-flood_loop')).toHaveTextContent('1.2 s (within the 30 s limit)'));
  });

  it('reset all reports the outcome', async () => {
    m.demoResetAll.mockResolvedValue({ changed: true, elapsed_ms: 900, within_limit: true });
    renderPage();
    await screen.findByTestId('scenario-flood_loop');
    await userEvent.click(screen.getByRole('button', { name: 'Reset all' }));
    expect(await screen.findByTestId('reset-all-message')).toHaveTextContent('restored');
  });

  it('shows a 403 envelope as a moderator-only message', async () => {
    m.demoStart.mockRejectedValue(new api.ApiError(403, 'forbidden', 'insufficient role'));
    renderPage();
    const card = within(await screen.findByTestId('scenario-flood_loop'));
    await userEvent.click(card.getByRole('button', { name: 'Start' }));
    expect(await card.findByTestId('error-flood_loop')).toHaveTextContent('moderator-only');
  });
});

describe('DemoAdmin session and actors', () => {
  it('requests a moderator session and shows who you are', async () => {
    m.demoCreateSession.mockResolvedValue({ token: 't', subject: 'sim-mod', role: 'moderator' });
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Get a moderator demo session' }));
    expect(m.demoCreateSession).toHaveBeenCalledWith({ kind: 'moderator', actor: null }, undefined);
    expect(await screen.findByTestId('session-identity')).toHaveTextContent('sim-mod with role moderator');
  });

  it('actor buttons call demoAct with the right arguments and show status text', async () => {
    m.demoAct.mockImplementation(async (_s, body) => {
      if (body.action === 'corroborate') return { report: { verification_status: 'Corroborated', status_label: 'Corroborated by community \u2014 not officially verified' } };
      if (body.action === 'confirm') return { report: { verification_status: 'Officially_Verified' } };
      return { report: { verification_status: 'Unverified', status_label: 'Unverified' } };
    });
    renderPage();
    const panel = within(await screen.findByTestId('actor-panel'));
    await userEvent.click(panel.getByRole('button', { name: 'A reports Stairwell A blocked' }));
    await userEvent.click(panel.getByRole('button', { name: 'C corroborates the report' }));
    await userEvent.click(panel.getByRole('button', { name: 'Moderator confirms SIMULATED closure' }));
    await userEvent.click(panel.getByRole('button', { name: 'B sends assistance request' }));
    expect(m.demoAct.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      ['blocked_stairwell', { actor: 'A', action: 'report' }],
      ['blocked_stairwell', { actor: 'C', action: 'corroborate' }],
      ['blocked_stairwell', { actor: 'moderator', action: 'confirm' }],
      ['blocked_stairwell', { actor: 'B', action: 'message', message_type: 'assistance_request' }],
    ]);
    expect(panel.getByTestId('result-c-corroborate')).toHaveTextContent('Corroborated by community \u2014 not officially verified');
    expect(panel.getByTestId('result-m-confirm')).toHaveTextContent('Officially_Verified (SIMULATED moderator verification)');
    expect(panel.getByTestId('observe-status')).toBeInTheDocument();
  });

  it('logs realtime events with a simulated flag and clears stale routes on scenario_reset', async () => {
    const fc = renderPage();
    await screen.findByTestId('event-log');
    await waitFor(() => expect(fc.handlers()).not.toBeNull());
    const h = fc.handlers()!;
    const { act } = await import('@testing-library/react');
    act(() => {
      h.onEvent({ event_id: 'e1', type: 'report_created', incident_id: 'inc-blocked_stairwell', payload: { report_id: 'r1', simulated: true }, created_at: '' });
    });
    expect(within(screen.getByTestId('event-log')).getByText('report_created')).toBeInTheDocument();
    expect(within(screen.getByTestId('event-log')).getByText('SIMULATED')).toBeInTheDocument();
    act(() => {
      h.onEvent({ event_id: 'e2', type: 'scenario_reset', incident_id: 'inc-blocked_stairwell', payload: { scenario_id: 'blocked_stairwell' }, created_at: '' });
    });
    const log = screen.getByTestId('event-log');
    expect(within(log).queryByText('report_created')).not.toBeInTheDocument();
    expect(within(log).getByText('scenario_reset')).toBeInTheDocument();
  });
});

describe('closure tools', () => {
  it('adds and removes an ad-hoc closure', async () => {
    m.demoAddClosure.mockResolvedValue({ restriction_id: 'sim-adhoc-1' });
    m.demoRemoveClosure.mockResolvedValue({ removed: true });
    renderPage();
    const tools = within(await screen.findByTestId('closure-tools'));
    await userEvent.type(tools.getByLabelText(/Edge ids/), 'e1, e2');
    await userEvent.click(tools.getByRole('button', { name: /Add SIMULATED closure/ }));
    await waitFor(() =>
      expect(m.demoAddClosure).toHaveBeenCalledWith({ incident_id: 'inc-flood_loop', edge_ids: ['e1', 'e2'], geometry: null, kind: 'closure' }),
    );
    await userEvent.click(await tools.findByRole('button', { name: 'Remove closure sim-adhoc-1' }));
    await waitFor(() => expect(m.demoRemoveClosure).toHaveBeenCalledWith('sim-adhoc-1', 'inc-flood_loop'));
  });
});

describe('demo helpers', () => {
  it('parses ?demoUser and builds second-window URLs', () => {
    expect(parseDemoUser('?demoUser=B')).toBe('B');
    expect(parseDemoUser('?demoUser=Z')).toBeNull();
    expect(secondWindowUrl('C', { origin: 'http://x', pathname: '/app/' })).toBe('http://x/app/?demoUser=C');
  });

  it('bootstraps a demo user token and ignores a missing parameter', async () => {
    expect(await bootstrapDemoUser('')).toBeNull();
    m.demoCreateSession.mockResolvedValue({ token: 'tok', subject: 'sim-user-a', role: 'user' });
    const info = await bootstrapDemoUser('?demoUser=A');
    expect(m.demoCreateSession).toHaveBeenCalledWith({ kind: 'user', actor: 'A' }, undefined);
    expect(info?.subject).toBe('sim-user-a');
    expect(api.tokenStore.get()).toBe('tok');
    api.tokenStore.set(null);
  });

  it('orders scenarios and words actor results', () => {
    expect(sortScenarios([{ id: 'fire', optional: true }, { id: 'blocked_stairwell' }, { id: 'flood_loop' }]).map((s) => s.id)).toEqual([
      'flood_loop',
      'blocked_stairwell',
      'fire',
    ]);
    expect(actResultText('corroborate', { report: { verification_status: 'Corroborated' } })).toContain('not officially verified');
  });
});
