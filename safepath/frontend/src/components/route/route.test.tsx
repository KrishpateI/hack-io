import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiError, type RouteResponse } from '../../services/api';
import type { RouteRecalcSnapshot } from '../../services/routeRecalc';
import { findViolations } from '../../wordingLint.test';
import { RoutePanel } from './RoutePanel';
import { RouteChangeBanner } from './RouteChangeBanner';
import { toMapRoutes } from './routeGeometry';

type R = RouteResponse['routes'][number];

const route = (id: string, over: Partial<R> = {}): R => ({
  calculated_at: '2025-01-01T00:00:00Z',
  confidence_limitations: ['Elevation data is low confidence'],
  distance_meters: 1500,
  edge_penalties: [],
  estimated_duration_seconds: 1080,
  excluded_edges_count: 7,
  geometry: { type: 'LineString', coordinates: [[-87.6, 41.8], [-87.61, 41.81]] },
  hazard_flags: ['Lower modeled exposure to flood layer'],
  label: 'algorithm-generated',
  policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
  reason_summary: `Reason for ${id}: avoids closed bridge`,
  restrictions_considered: [{ id: 'r1', kind: 'road_closure', simulated: true, source_record_id: 'src-1' }],
  route_id: id,
  route_type: 'disaster-adjusted',
  sources: ['src-1'],
  status: 'ok',
  uncertainty_level: 'Medium',
  ...over,
});

const response = (over: Partial<RouteResponse> = {}): RouteResponse => ({
  calculated_at: '2025-01-01T00:00:00Z',
  data_timestamp: '2025-01-01T00:00:00Z',
  explanation: 'Explanation text.',
  label: 'algorithm-generated',
  missing_data: [],
  notices: [],
  policy_outcome: 'ROUTE_OPTIONS_AVAILABLE',
  route_count: 2,
  routes: [route('a'), route('b', { route_type: 'shortest' })],
  sources: [],
  status: 'ok',
  ...over,
});

const notice = (over: Record<string, unknown> = {}) => ({
  cbd_derived: false,
  edge_ids: [],
  effective_at: '2025-01-01T00:00:00Z',
  incident_id: 'i1',
  notice_id: 'n1',
  notice_type: 'shelter_in_place' as const,
  published_at: '2025-01-01T00:00:00Z',
  retrieved_at: '2025-01-01T00:00:00Z',
  simulation_flag: true,
  source_organization: 'Chicago OEMC',
  source_record_id: 'src-n1',
  source_url: 'https://example.org/n1',
  text: 'Take cover in the library basement.',
  verification_status: 'verified',
  ...over,
});

const snap = (over: Partial<RouteRecalcSnapshot> = {}): RouteRecalcSnapshot => ({
  route: null, previousRoute: null, change: null, status: 'idle', updating: false,
  message: null, policyOutcome: null, stale: false, calculatedAt: null, ...over,
});

describe('RoutePanel outcomes', () => {
  it('lists routes with distance, ETA, label, uncertainty, flags; selecting reveals details', async () => {
    const onSelect = vi.fn();
    render(<RoutePanel response={response()} onSelectRoute={onSelect} />);
    const items = screen.getAllByTestId('route-item');
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText('1.5 km')).toBeInTheDocument();
    expect(within(items[0]).getByText('18 min')).toBeInTheDocument();
    expect(within(items[0]).getByText('algorithm-generated')).toBeInTheDocument();
    expect(within(items[0]).getByTestId('uncertainty-badge')).toHaveTextContent('Uncertainty: Medium');
    expect(within(items[0]).getByTestId('hazard-flags')).toBeInTheDocument();
    expect(screen.getByText(/Last information update/)).toBeInTheDocument();
    // default selection shows first route details
    expect(screen.getByText(/Reason for a/)).toBeInTheDocument();
    await userEvent.click(within(items[1]).getByRole('button', { name: /Alternative 1/ }));
    expect(onSelect).toHaveBeenCalledWith('b');
    expect(screen.getByText(/Reason for b/)).toBeInTheDocument();
    expect(screen.getByText('Excluded edges: 7')).toBeInTheDocument();
    expect(screen.getByText('Elevation data is low confidence')).toBeInTheDocument();
  });

  it('labels simulated restrictions and opens the source popup', async () => {
    render(<RoutePanel response={response()} />);
    expect(screen.getAllByTestId('simulated-badge').length).toBeGreaterThan(0);
    expect(screen.getByText('Cause: road_closure')).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole('button', { name: 'Source src-1' })[0]);
    expect(screen.getByTestId('source-popup')).toBeInTheDocument();
  });

  it('shelter in place: notice, tornado guidance, 911, no route drawn', () => {
    const res = response({ policy_outcome: 'SHELTER_IN_PLACE', routes: [], route_count: 0, notices: [notice()] });
    render(<RoutePanel response={res} />);
    expect(screen.getByTestId('shelter-in-place')).toBeInTheDocument();
    expect(screen.getByText('Take cover in the library basement.')).toBeInTheDocument();
    expect(screen.getByText(/Tornado guidance/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Tornado Safety/ })).toHaveAttribute('href', expect.stringContaining('weather.gov'));
    expect(screen.getByTestId('call-911')).toBeInTheDocument();
    expect(screen.queryByTestId('route-item')).toBeNull();
    expect(toMapRoutes(res)).toEqual([]);
  });

  it('shelter in place without notice says no verified shelter', () => {
    render(<RoutePanel response={response({ policy_outcome: 'SHELTER_IN_PLACE', routes: [], notices: [] })} />);
    expect(screen.getByText('No verified shelter available')).toBeInTheDocument();
  });

  it('follow official instructions shows every notice with source', () => {
    const res = response({
      policy_outcome: 'FOLLOW_OFFICIAL_INSTRUCTIONS', routes: [],
      notices: [notice({ notice_id: 'n1', notice_type: 'follow_instructions', text: 'Evacuate east.' }), notice({ notice_id: 'n2', text: 'Avoid Main St.' })],
    });
    render(<RoutePanel response={res} />);
    expect(screen.getByText('Evacuate east.')).toBeInTheDocument();
    expect(screen.getByText('Avoid Main St.')).toBeInTheDocument();
    expect(screen.getAllByText(/Chicago OEMC/)).toHaveLength(2);
  });

  it('no verified route explains and never fabricates a route', () => {
    const res = response({ policy_outcome: 'NO_VERIFIED_ROUTE_AVAILABLE', routes: [], explanation: 'Closures removed all paths.', missing_data: ['Sidewalk data'] });
    render(<RoutePanel response={res} />);
    expect(screen.getByRole('heading', { name: 'No verified route available' })).toBeInTheDocument();
    expect(screen.getByText('Closures removed all paths.')).toBeInTheDocument();
    expect(screen.getByText('Sidewalk data')).toBeInTheDocument();
    expect(screen.queryByTestId('route-item')).toBeNull();
    expect(toMapRoutes(res)).toEqual([]);
  });

  it('insufficient data lists missing data', () => {
    render(<RoutePanel response={response({ policy_outcome: 'INSUFFICIENT_DATA', routes: [], missing_data: ['Road graph', 'Elevation'] })} />);
    const list = screen.getByTestId('missing-data');
    expect(within(list).getByText('Road graph')).toBeInTheDocument();
    expect(within(list).getByText('Elevation')).toBeInTheDocument();
  });

  it('shows loading and API error envelope message; recalc button fires', async () => {
    const onRecalc = vi.fn();
    const { rerender } = render(<RoutePanel response={null} loading onRecalculate={onRecalc} />);
    expect(screen.getByTestId('route-loading')).toBeInTheDocument();
    rerender(<RoutePanel response={null} error={new ApiError(503, 'routing_unavailable', 'Routing configuration is invalid')} onRecalculate={onRecalc} />);
    expect(screen.getByTestId('route-error')).toHaveTextContent('Routing configuration is invalid');
    await userEvent.click(screen.getByRole('button', { name: 'Recalculate route' }));
    expect(onRecalc).toHaveBeenCalled();
  });

  it('rendered text has no forbidden wording for any outcome', () => {
    const outcomes: RouteResponse[] = [
      response(),
      response({ policy_outcome: 'SHELTER_IN_PLACE', routes: [], notices: [notice()] }),
      response({ policy_outcome: 'FOLLOW_OFFICIAL_INSTRUCTIONS', routes: [], notices: [notice()] }),
      response({ policy_outcome: 'NO_VERIFIED_ROUTE_AVAILABLE', routes: [] }),
      response({ policy_outcome: 'INSUFFICIENT_DATA', routes: [], missing_data: ['x'] }),
    ];
    for (const o of outcomes) {
      const { container, unmount } = render(<RoutePanel response={o} recalc={snap({ stale: true })} onRecalculate={() => {}} />);
      const text = container.textContent ?? '';
      expect(findViolations(text), text).toEqual([]);
      unmount();
    }
  });
});

describe('RouteChangeBanner', () => {
  it('shows stale warning', () => {
    render(<RouteChangeBanner recalc={snap({ stale: true })} />);
    expect(screen.getByTestId('stale-warning')).toHaveTextContent('Road conditions may be out of date');
  });

  it('shows updating state and marks route possibly outdated in panel', () => {
    const res = response();
    render(<RoutePanel response={res} recalc={snap({ updating: true, status: 'updating', route: res.routes[0] })} />);
    expect(screen.getByTestId('route-updating')).toHaveTextContent(/Updating/);
    expect(screen.getAllByText('Possibly outdated').length).toBeGreaterThan(0);
  });

  it('shows change with previous vs new numbers and failure message', () => {
    const prev = route('p', { distance_meters: 1000, estimated_duration_seconds: 600 });
    const next = route('n', { distance_meters: 2500, estimated_duration_seconds: 1800 });
    const { rerender } = render(<RouteChangeBanner recalc={snap({ route: next, previousRoute: prev, change: 'New road_closure is now avoided.' })} />);
    expect(screen.getByText('New road_closure is now avoided.')).toBeInTheDocument();
    expect(screen.getByText('1.0 km')).toBeInTheDocument();
    expect(screen.getByText('2.5 km')).toBeInTheDocument();
    expect(screen.getByText('10 min')).toBeInTheDocument();
    expect(screen.getByText('30 min')).toBeInTheDocument();
    rerender(<RouteChangeBanner recalc={snap({ status: 'failed', message: 'Recalculation failed, showing previous route as possibly outdated.' })} />);
    expect(screen.getByTestId('route-recalc-failed')).toBeInTheDocument();
  });

  it('renders nothing when idle', () => {
    const { container } = render(<RouteChangeBanner recalc={snap()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('toMapRoutes', () => {
  it('draws primary solid and alternatives dashed; skips routes without geometry', () => {
    const res = response({ routes: [route('a'), route('b'), route('c', { geometry: undefined })] });
    const m = toMapRoutes(res, 'b');
    expect(m.map((x) => x.id)).toEqual(['a', 'b']);
    expect(m[0].style.dasharray).toBeNull();
    expect(m[1].style.dasharray).not.toBeNull();
    expect(m[1].selected).toBe(true);
    expect(m[0].feature.geometry.type).toBe('LineString');
    expect(toMapRoutes(null)).toEqual([]);
  });
});
